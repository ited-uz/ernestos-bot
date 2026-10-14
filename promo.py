"""
ErnestOS — promo codes: days of Pro or Max for whoever types the code.

An admin creates a code in the bot (`/promo_new KOD max 30 100 2026-12-31`);
anyone redeems it once, in the app or with `/promo KOD`. Redeeming is a plan
grant like any other (`plans.grant(source="promo")`), so it queues after
time of the same tier the account already has instead of overlapping it.

Every refusal has its own code so the app can say exactly why:
not_found · inactive · expired · exhausted · already_used · plans_off.
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import plans
from db import PromoCode, PromoCodeRedemption, utcnow

CODE_RE = re.compile(r"^[A-Z0-9_-]{3,32}$")
DURATIONS = (1, 3, 7, 14, 30, 60, 90, 180, 365)


class PromoError(Exception):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


def clean(code: str) -> str:
    return re.sub(r"\s+", "", str(code or "")).upper()


def create(s: Session, code: str, tier: str, days: int, *, max_uses: int | None = None,
           expires_at: datetime | None = None, created_by: int | None = None) -> PromoCode:
    code = clean(code)
    if not CODE_RE.match(code):
        raise PromoError("bad_code", 422)
    if tier not in ("pro", "max"):
        raise PromoError("bad_tier", 422)
    if not 1 <= int(days) <= 730:
        raise PromoError("bad_days", 422)
    if max_uses is not None and int(max_uses) < 1:
        raise PromoError("bad_uses", 422)
    if s.scalar(select(PromoCode.id).where(PromoCode.code == code)):
        raise PromoError("code_taken")
    row = PromoCode(code=code, tier=tier, duration_days=int(days),
                    max_uses=int(max_uses) if max_uses else None,
                    expires_at=expires_at, created_by=created_by)
    s.add(row)
    s.flush()
    return row


def deactivate(s: Session, code: str) -> bool:
    row = s.scalar(select(PromoCode).where(PromoCode.code == clean(code)))
    if row is None:
        return False
    row.is_active = False
    return True


def redeem(s: Session, user_id: int, code: str) -> dict:
    """Give the code's days to this account. The caller commits."""
    if not plans.ENABLED:
        raise PromoError("plans_off")
    code = clean(code)
    if not CODE_RE.match(code):
        raise PromoError("not_found", 404)
    row = s.scalar(select(PromoCode).where(PromoCode.code == code))
    if row is None:
        raise PromoError("not_found", 404)
    if not row.is_active:
        raise PromoError("inactive")
    if row.expires_at is not None and row.expires_at <= utcnow():
        raise PromoError("expired")
    if s.scalar(select(PromoCodeRedemption.id).where(
            PromoCodeRedemption.promo_id == row.id, PromoCodeRedemption.user_id == user_id)):
        raise PromoError("already_used")
    try:
        with s.begin_nested():
            # Checked and counted in one statement: the last use goes to one person.
            won = s.execute(update(PromoCode).where(
                PromoCode.id == row.id,
                (PromoCode.max_uses.is_(None)) | (PromoCode.used_count < PromoCode.max_uses))
                .values(used_count=PromoCode.used_count + 1)
                .execution_options(synchronize_session=False)).rowcount
            if not won:
                raise PromoError("exhausted")
            s.add(PromoCodeRedemption(promo_id=row.id, user_id=user_id))
            s.flush()
    except IntegrityError:
        raise PromoError("already_used") from None
    plans.grant(s, user_id, row.tier, row.duration_days, "promo", ref=f"promo:{row.id}:{user_id}")
    return {"code": row.code, "tier": row.tier, "days": row.duration_days}


def listing(s: Session, limit: int = 20) -> list[dict]:
    return [{"code": r.code, "tier": r.tier, "days": r.duration_days, "max_uses": r.max_uses,
             "used": r.used_count, "active": r.is_active,
             "expires_at": r.expires_at.date().isoformat() if r.expires_at else None}
            for r in s.scalars(select(PromoCode).order_by(PromoCode.id.desc()).limit(limit)).all()]


def redeemed_by(s: Session, user_id: int) -> list[dict]:
    rows = s.execute(select(PromoCode.code, PromoCode.tier, PromoCode.duration_days,
                            PromoCodeRedemption.redeemed_at)
                     .join(PromoCode, PromoCode.id == PromoCodeRedemption.promo_id)
                     .where(PromoCodeRedemption.user_id == user_id)
                     .order_by(PromoCodeRedemption.id)).all()
    return [{"code": c, "tier": t, "days": d, "at": at.isoformat() if at else None}
            for c, t, d, at in rows]


def forget(s: Session, ids) -> None:
    ids = list(ids)
    if ids:
        s.execute(delete(PromoCodeRedemption).where(PromoCodeRedemption.user_id.in_(ids)))


def count_redeemed(s: Session, code: str) -> int:
    return int(s.scalar(select(func.count(PromoCodeRedemption.id))
                        .join(PromoCode, PromoCode.id == PromoCodeRedemption.promo_id)
                        .where(PromoCode.code == clean(code))) or 0)
