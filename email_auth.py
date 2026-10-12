"""
ErnestOS — signing up and in with an email address.

The phone app no longer needs Telegram to start. A person types an email,
gets a six-digit code, types it back, and is in:

  * an address nobody has used yet makes a new account, then setup runs in
    the app itself (name, language, what to track);
  * an address already attached to an account signs in to that account —
    including a Telegram account that attached it in Settings.

An account made this way has no Telegram, so its id is a large negative
number. Telegram ids are positive, and the rate limiter's anonymous buckets
live in -1 … -9 999 999, so nothing collides. Everything downstream keys on
that id exactly as it keys on a Telegram id.

Only SHA-256 digests of codes are stored. A code lives ten minutes, allows five
wrong tries, and a new one cancels the last. One address can ask for a code
once a minute and five times an hour.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import timedelta

from sqlalchemy import delete as sql_delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db import AccountEmail, EmailCode, User, utcnow

EMAIL_ID_BASE = 10 ** 12
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
CODE_LENGTH = 6
CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
RESEND_AFTER = timedelta(seconds=60)
HOURLY_MAX = 5


class TooSoon(Exception):
    """A code was asked for too recently; `seconds` until the next one."""

    def __init__(self, seconds: int):
        super().__init__(seconds)
        self.seconds = seconds


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def normalize_email(raw) -> str:
    email = str(raw or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise ValueError("bad_email")
    return email


def is_email_account(account_id: int) -> bool:
    return int(account_id) <= -EMAIL_ID_BASE


def account_for(s: Session, email: str) -> int | None:
    row = s.get(AccountEmail, email)
    return row.account_id if row is not None else None


def email_of(s: Session, account_id: int) -> str | None:
    return s.scalar(select(AccountEmail.email).where(AccountEmail.account_id == account_id))


def issue_code(s: Session, email: str, purpose: str = "signin",
               account_id: int | None = None) -> str:
    """A fresh code for this address. Earlier unused ones stop working."""
    now = utcnow()
    recent = s.scalars(select(EmailCode.created_at).where(
        EmailCode.email == email, EmailCode.created_at > now - timedelta(hours=1))
        .order_by(EmailCode.created_at.desc())).all()
    if recent and now - recent[0] < RESEND_AFTER:
        raise TooSoon(int((RESEND_AFTER - (now - recent[0])).total_seconds()) + 1)
    if len(recent) >= HOURLY_MAX:
        raise TooSoon(int((recent[-1] + timedelta(hours=1) - now).total_seconds()) + 1)
    s.execute(update(EmailCode).where(EmailCode.email == email, EmailCode.used_at.is_(None))
              .values(used_at=now))
    code = "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))
    s.add(EmailCode(email=email, purpose=purpose, account_id=account_id,
                    code_hash=_digest(code), expires_at=now + CODE_TTL))
    s.commit()
    return code


def burn_codes(s: Session, email: str) -> None:
    """The mail did not go out: the code it carried must not work either."""
    s.execute(update(EmailCode).where(EmailCode.email == email, EmailCode.used_at.is_(None))
              .values(used_at=utcnow()))
    s.commit()


def check_code(s: Session, email: str, raw_code, purpose: str = "signin",
               account_id: int | None = None) -> bool:
    """Spend the code when it is right. A wrong one counts against it, and
    the fifth wrong try kills it."""
    code = "".join(ch for ch in str(raw_code or "") if ch.isdigit())
    now = utcnow()
    row = s.scalar(select(EmailCode).where(
        EmailCode.email == email, EmailCode.purpose == purpose,
        EmailCode.used_at.is_(None), EmailCode.expires_at > now)
        .order_by(EmailCode.id.desc()).limit(1))
    if row is None or (purpose == "attach" and row.account_id != account_id):
        return False
    if len(code) == CODE_LENGTH and secrets.compare_digest(row.code_hash, _digest(code)):
        spent = s.execute(update(EmailCode).where(EmailCode.id == row.id,
                                                  EmailCode.used_at.is_(None))
                          .values(used_at=now)).rowcount
        s.commit()
        return bool(spent)
    row.attempts = (row.attempts or 0) + 1
    if row.attempts >= MAX_ATTEMPTS:
        row.used_at = now
    s.commit()
    return False


def create_account(s: Session, email: str, *, language: str = "uz",
                   first_name: str = "") -> int:
    """A new account for this address, with no Telegram behind it."""
    import services as svc
    # Ids count down from -EMAIL_ID_BASE; two sign-ups in the same instant
    # collide on the primary key and the loser simply takes the next one.
    for _ in range(5):
        lowest = s.scalar(select(func.min(User.telegram_id)).where(
            User.telegram_id <= -EMAIL_ID_BASE))
        new_id = (lowest - 1) if lowest is not None else -EMAIL_ID_BASE
        try:
            svc.get_or_create_user(s, new_id, first_name=first_name.strip()[:60])
            break
        except (IntegrityError, RuntimeError):
            s.rollback()
    else:
        raise RuntimeError("could not allocate an account id")
    s.add(AccountEmail(email=email, account_id=new_id))
    user = s.get(User, new_id)
    user.language = language if language in ("uz", "en", "ru") else "uz"
    user.onboarded = False
    user.onboarding_step = "app"
    s.commit()
    return new_id


def attach(s: Session, account_id: int, email: str) -> None:
    """Make this the account's email. One address, one account."""
    owner = account_for(s, email)
    if owner is not None and owner != account_id:
        raise ValueError("email_taken")
    s.execute(sql_delete(AccountEmail).where(AccountEmail.account_id == account_id,
                                             AccountEmail.email != email))
    if owner is None:
        s.add(AccountEmail(email=email, account_id=account_id))
    s.commit()


def forget(s: Session, account_ids) -> None:
    """Every address and code of these accounts. The caller commits."""
    ids = list(account_ids)
    if not ids:
        return
    emails = list(s.scalars(select(AccountEmail.email).where(AccountEmail.account_id.in_(ids))).all())
    s.execute(sql_delete(AccountEmail).where(AccountEmail.account_id.in_(ids)))
    if emails:
        s.execute(sql_delete(EmailCode).where(EmailCode.email.in_(emails)))
    s.execute(sql_delete(EmailCode).where(EmailCode.account_id.in_(ids)))
