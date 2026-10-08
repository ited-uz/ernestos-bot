"""
ErnestOS — Free, Pro and Max.

What an account may do is decided here and nowhere else. The rest of the code
asks two questions:

  * `tier_of(s, account_id)` — which plan is this account on right now;
  * `require(s, account_id, key, used)` — may it add one more of `key`
    (raises `PlanLimit` when not).

How an account gets Pro or Max
------------------------------

Every stretch of a paid tier is a `PlanGrant` row with a start and an end:

  * **trial** — a new account starts with `TRIAL_DAYS` of Pro;
  * **gift** — accounts that existed before plans were switched on get
    `LAUNCH_GIFT_DAYS` of Pro once, so nobody loses features overnight;
  * **channel** — joining the channel adds `CHANNEL_BONUS_DAYS` of Pro,
    once per account;
  * **referral** — the friend who arrives gets `REFERRAL_BONUS_DAYS` of Pro
    (at most `REFERRAL_MONTHLY_CAP` days a month); the inviter is paid in
    steps, `REFERRAL_REWARDS`: 5 friends a month of Pro, 10 two more months,
    20 a month of Max — each step once;
  * **stars** — a Telegram Stars payment adds 30 or 365 days.

Grants of one tier queue end to end: paying during the trial adds after it.
Max outranks Pro while both are running.

Nothing is ever deleted when an account falls back to Free. Over-the-limit
items stay, are shown and can be ticked; only adding new ones is refused.

`PLANS_ENABLED=0` turns all of this off: every account behaves as Max and
the old free-actions/channel gate applies as before.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db import PlanGrant, User, utcnow


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


ENABLED = os.environ.get("PLANS_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")

TRIAL_DAYS = _int("PLAN_TRIAL_DAYS", 3)
LAUNCH_GIFT_DAYS = _int("PLAN_LAUNCH_GIFT_DAYS", 14)
CHANNEL_BONUS_DAYS = _int("PLAN_CHANNEL_BONUS_DAYS", 7)
REFERRAL_BONUS_DAYS = _int("PLAN_REFERRAL_BONUS_DAYS", 3)
REFERRAL_MONTHLY_CAP = _int("PLAN_REFERRAL_MONTHLY_CAP", 30)
#: (qualified friends, tier, days) — what the inviter gets at each step.
REFERRAL_REWARDS: tuple[tuple[int, str, int], ...] = ((5, "pro", 30), (10, "pro", 60), (20, "max", 30))

TIERS = ("free", "pro", "max")
RANK = {"free": 0, "pro": 1, "max": 2}

#: None means no limit. Keys are what `require` is asked about.
LIMITS: dict[str, dict[str, int | None]] = {
    "habits":          {"free": 3,  "pro": 25,   "max": None},
    "active_tasks":    {"free": 30, "pro": None, "max": None},
    "recurring_tasks": {"free": 3,  "pro": 25,   "max": None},
    "projects":        {"free": 1,  "pro": 15,   "max": None},
    #: Milestone and ultimate goals still being worked on.
    "life_goals":      {"free": 3,  "pro": 25,   "max": None},
    "open_debts":      {"free": 3,  "pro": None, "max": None},
    #: Money accounts (cash, card, bank…) and repeating payments.
    "money_accounts":  {"free": 1,  "pro": 3,    "max": None},
    "money_subs":      {"free": 0,  "pro": 3,    "max": None},
    #: Teams an account owns. Joining somebody else's team is always free.
    "teams_owned":     {"free": 0,  "pro": 2,    "max": 10},
    #: Members of a team, set by its owner's plan.
    "team_members":    {"free": 8,  "pro": 10,   "max": 50},
    #: Voice and AI requests: per day on Pro and Max, per week on Free.
    "voice_day":       {"free": None, "pro": 30, "max": 100},
    "voice_week":      {"free": 5,  "pro": None, "max": None},
}

#: Features that are simply on or off.
FEATURES: dict[str, tuple[str, ...]] = {
    "journal_ai":    ("pro", "max"),   # AI fills the evening journal
    "timer_rhythm":  ("pro", "max"),   # 25/5, 50/10, 90/15
    "stats_history": ("pro", "max"),   # month and year views
    "stats_csv":     ("max",),         # statistics as a spreadsheet file
    "money_transfers": ("pro", "max"), # moving money between own accounts
    "money_year":    ("max",),         # the year of money, month by month
    "themes":        ("pro", "max"),   # Blossom, Obsidian, Emerald and dark mode
}


@dataclass(frozen=True)
class Product:
    key: str
    tier: str
    days: int
    stars: int
    uzs: int


def _products() -> dict[str, Product]:
    rows = [
        Product("pro_month", "pro", 30, _int("PLAN_STARS_PRO_MONTH", 125), 29_000),
        Product("pro_year", "pro", 365, _int("PLAN_STARS_PRO_YEAR", 1100), 249_000),
        Product("max_month", "max", 30, _int("PLAN_STARS_MAX_MONTH", 350), 79_000),
        Product("max_year", "max", 365, _int("PLAN_STARS_MAX_YEAR", 3000), 690_000),
    ]
    return {p.key: p for p in rows}


PRODUCTS = _products()


class PlanLimit(Exception):
    """Adding this would go past the account's plan. Carries what to say."""

    def __init__(self, key: str, limit: int | None, tier: str, needs: str):
        super().__init__(key)
        self.key, self.limit, self.tier, self.needs = key, limit, tier, needs

    def as_dict(self) -> dict:
        return {"detail": "plan_limit", "key": self.key, "limit": self.limit,
                "tier": self.tier, "needs": self.needs}


# ---------------------------------------------------------------------------
# Which plan
# ---------------------------------------------------------------------------

def _active(s: Session, account_id: int, now: datetime | None = None) -> list[PlanGrant]:
    now = now or utcnow()
    return list(s.scalars(select(PlanGrant).where(
        PlanGrant.account_id == account_id,
        PlanGrant.starts_at <= now, PlanGrant.ends_at > now)).all())


def tier_of(s: Session, account_id: int | None) -> str:
    if not ENABLED:
        return "max"
    if account_id is None:
        return "free"
    best = "free"
    for grant in _active(s, int(account_id)):
        if RANK.get(grant.tier, 0) > RANK[best]:
            best = grant.tier
    return best


def tier_for_workspace(s: Session, ws: int) -> str:
    if not ENABLED:
        return "max"
    from db import Workspace
    owner = s.scalar(select(Workspace.user_id).where(Workspace.id == ws))
    return tier_of(s, owner)


def _until(s: Session, account_id: int, tier: str) -> datetime | None:
    """When the account's current run of `tier` (or better) ends."""
    rows = s.scalars(select(PlanGrant).where(
        PlanGrant.account_id == account_id,
        PlanGrant.ends_at > utcnow()).order_by(PlanGrant.starts_at)).all()
    end = None
    for row in rows:
        if RANK.get(row.tier, 0) < RANK[tier]:
            continue
        if end is None:
            if row.starts_at > utcnow():
                break
            end = row.ends_at
        elif row.starts_at <= end:
            end = max(end, row.ends_at)
    return end


def summary(s: Session, account_id: int) -> dict:
    """What the app shows: the plan, until when, and what it allows."""
    tier = tier_of(s, account_id)
    until = _until(s, account_id, tier) if tier != "free" and ENABLED else None
    used_sources = set(s.scalars(select(PlanGrant.source).where(
        PlanGrant.account_id == account_id)).all())
    return {
        "enabled": ENABLED,
        "tier": tier,
        "until": until.isoformat() + "Z" if until else None,
        "days_left": math.ceil((until - utcnow()).total_seconds() / 86400) if until else None,
        "channel_bonus_used": "channel" in used_sources,
        "channel_bonus_days": CHANNEL_BONUS_DAYS,
        "limits": {key: row[tier] for key, row in LIMITS.items()},
        "features": [name for name, tiers in FEATURES.items() if tier in tiers],
        "products": [{"key": p.key, "tier": p.tier, "days": p.days, "stars": p.stars, "uzs": p.uzs}
                     for p in PRODUCTS.values()],
    }


# ---------------------------------------------------------------------------
# Getting a plan
# ---------------------------------------------------------------------------

def grant(s: Session, account_id: int, tier: str, days: int, source: str, *,
          ref: str | None = None, amount: int | None = None) -> PlanGrant | None:
    """Add `days` of `tier`, queued after any time of the same tier the
    account already has. Returns None when `ref` was already applied."""
    if tier not in ("pro", "max") or days <= 0:
        raise ValueError("bad_grant")
    if ref and s.scalar(select(PlanGrant.id).where(PlanGrant.ref == ref)):
        return None
    now = utcnow()
    last_end = s.scalar(select(func.max(PlanGrant.ends_at)).where(
        PlanGrant.account_id == account_id, PlanGrant.tier == tier,
        PlanGrant.ends_at > now))
    start = max(now, last_end) if last_end else now
    row = PlanGrant(account_id=int(account_id), tier=tier, source=source, starts_at=start,
                    ends_at=start + timedelta(days=days), ref=ref, amount=amount)
    s.add(row)
    s.flush()
    return row


def ensure_started(s: Session, user: User, *, created: bool) -> PlanGrant | None:
    """First touch under plans: a new account gets the trial, an account that
    was already here gets the launch gift. Once per account. The caller commits."""
    if not ENABLED or user is None or user.plan_started_at is not None:
        return None
    user.plan_started_at = utcnow()
    if created:
        return grant(s, user.telegram_id, "pro", TRIAL_DAYS, "trial")
    return grant(s, user.telegram_id, "pro", LAUNCH_GIFT_DAYS, "gift")


def channel_bonus(s: Session, account_id: int) -> PlanGrant | None:
    """Joining the channel: Pro for a week, once per account. The caller commits."""
    if not ENABLED or CHANNEL_BONUS_DAYS <= 0:
        return None
    if s.scalar(select(PlanGrant.id).where(PlanGrant.account_id == account_id,
                                           PlanGrant.source == "channel")):
        return None
    return grant(s, account_id, "pro", CHANNEL_BONUS_DAYS, "channel")


def referral_bonus(s: Session, account_id: int) -> PlanGrant | None:
    """A qualified referral, for either side. Capped per rolling 30 days."""
    if not ENABLED or REFERRAL_BONUS_DAYS <= 0:
        return None
    since = utcnow() - timedelta(days=30)
    given = s.scalar(select(func.count(PlanGrant.id)).where(
        PlanGrant.account_id == account_id, PlanGrant.source == "referral",
        PlanGrant.created_at >= since)) or 0
    if (given + 1) * REFERRAL_BONUS_DAYS > REFERRAL_MONTHLY_CAP:
        return None
    return grant(s, account_id, "pro", REFERRAL_BONUS_DAYS, "referral")


def _reward_ref(account_id: int, target: int) -> str:
    return f"refstep:{int(account_id)}:{target}"


def referral_rewards(s: Session, account_id: int, qualified: int) -> list[PlanGrant]:
    """Every step the inviter has reached and not yet been given. Idempotent:
    each step carries its own ref, so a recount never pays twice."""
    if not ENABLED:
        return []
    given = []
    for target, tier, days in REFERRAL_REWARDS:
        if qualified >= target:
            row = grant(s, account_id, tier, days, "referral", ref=_reward_ref(account_id, target))
            if row is not None:
                given.append(row)
    return given


def referral_steps(s: Session, account_id: int, qualified: int) -> list[dict]:
    """What the invite screen draws: each step, how far along, and whether it
    was already paid."""
    paid = set(s.scalars(select(PlanGrant.ref).where(
        PlanGrant.account_id == account_id,
        PlanGrant.ref.like(f"refstep:{int(account_id)}:%"))).all())
    return [{"target": target, "tier": tier, "days": days,
             "have": min(qualified, target), "reached": qualified >= target,
             "granted": _reward_ref(account_id, target) in paid}
            for target, tier, days in REFERRAL_REWARDS]


def apply_payment(s: Session, account_id: int, product_key: str, charge_id: str,
                  stars: int) -> PlanGrant | None:
    """A Stars payment Telegram confirmed. Idempotent on the charge id."""
    product = PRODUCTS.get(product_key)
    if product is None:
        raise ValueError("unknown_product")
    return grant(s, account_id, product.tier, product.days, "stars", ref=charge_id, amount=stars)


#: Telegram Stars: priced in XTR, with no outside payment provider.
STARS_INVOICE = {"currency": "XTR", "provider_token": ""}


def checkout_matches(query) -> tuple[str, int] | None:
    """A pre-checkout query for one of our invoices, at its listed price, in
    Stars: (product, account). Anything else is refused."""
    parsed = parse_payload(getattr(query, "invoice_payload", ""))
    if parsed is None:
        return None
    if getattr(query, "currency", None) != STARS_INVOICE["currency"]:
        return None
    if getattr(query, "total_amount", None) != PRODUCTS[parsed[0]].stars:
        return None
    return parsed


def invoice_payload(account_id: int, product_key: str) -> str:
    return f"plan:{product_key}:{int(account_id)}"


def parse_payload(payload: str) -> tuple[str, int] | None:
    try:
        kind, product, account = str(payload).split(":")
        if kind != "plan" or product not in PRODUCTS:
            return None
        return product, int(account)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

def needed_tier(key: str, wanted: int | None = None) -> str:
    """The cheapest tier that allows `wanted` (or, for a feature, has it)."""
    if key in FEATURES:
        return min(FEATURES[key], key=RANK.get)
    for tier in TIERS:
        limit = LIMITS[key][tier]
        if limit is None or (wanted is not None and wanted <= limit):
            return tier
    return "max"


def require(s: Session, account_id: int | None, key: str, used: int) -> None:
    """Refuse one more `key` when `used` already reaches the plan's limit."""
    if not ENABLED:
        return
    tier = tier_of(s, account_id)
    limit = LIMITS[key][tier]
    if limit is not None and used >= limit:
        raise PlanLimit(key, limit, tier, needed_tier(key, used + 1))


def require_feature(s: Session, account_id: int | None, feature: str) -> None:
    if not ENABLED:
        return
    tier = tier_of(s, account_id)
    if tier not in FEATURES[feature]:
        raise PlanLimit(feature, None, tier, needed_tier(feature))


def require_in_workspace(s: Session, ws: int, key: str, used: int) -> None:
    if not ENABLED:
        return
    from db import Workspace
    require(s, s.scalar(select(Workspace.user_id).where(Workspace.id == ws)), key, used)


# ---------------------------------------------------------------------------
# Reminders about the plan
# ---------------------------------------------------------------------------

def ending_soon(s: Session, within: timedelta = timedelta(hours=24)) -> list[tuple[int, datetime, str]]:
    """Accounts whose Pro or Max ends within `within` and that have not been
    told about this particular end yet: (account, end, tier)."""
    if not ENABLED:
        return []
    now = utcnow()
    candidates = s.scalars(select(PlanGrant.account_id).where(
        PlanGrant.ends_at > now, PlanGrant.ends_at <= now + within).distinct()).all()
    out = []
    for account in candidates:
        tier = tier_of(s, account)
        if tier == "free":
            continue
        end = _until(s, account, tier)
        if end is None or end > now + within:
            continue
        user = s.get(User, account)
        if user is None or user.plan_notice_for == end:
            continue
        out.append((account, end, tier))
    return out


def mark_notified(s: Session, account_id: int, end: datetime) -> None:
    user = s.get(User, account_id)
    if user is not None:
        user.plan_notice_for = end
        s.commit()


def forget(s: Session, ids) -> None:
    """Every grant of these accounts. The caller commits."""
    from sqlalchemy import delete as sql_delete
    ids = list(ids)
    if ids:
        s.execute(sql_delete(PlanGrant).where(PlanGrant.account_id.in_(ids)))
