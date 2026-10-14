"""
ErnestOS — coins: earned by doing, spent in a small shop.

Coins are not a second currency to keep in step with anything. They are read
off the XP ledger, which already pays every task, habit, prayer, journal and
week goal exactly once per key and caps ordinary activity at
`XP_DAILY_CAP` a day:

    balance = floor(total XP / XP_PER_COIN) - everything spent

so ticking a habit on and off, a Telegram retry or a double tap cannot mint
coins that the XP rules would not pay for. Spending is the only thing stored
here: one `CoinSpend` row per purchase, with the client's idempotency key, so a
purchase that times out and is sent again is bought once.

What the shop sells is deliberately small and priced well above what Telegram
Stars charge for the same days, so coins reward consistency without replacing
the paid plans:

  * Pro 3 days, Pro 7 days, Max 3 days — `plans.grant(source="coins")`;
  * one streak freeze for the current month (at most two a month).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import plans
from db import CoinSpend, utcnow

#: Ten XP make one coin. With the daily XP cap of 120 that is at most twelve
#: coins a day from ordinary activity (milestones are paid on top).
XP_PER_COIN = 10

#: Streak freezes bought with coins, per calendar month.
FREEZE_PER_MONTH = 2


@dataclass(frozen=True)
class Item:
    key: str
    coins: int
    kind: str            # "plan" | "freeze"
    tier: str | None = None
    days: int = 0


ITEMS: dict[str, Item] = {item.key: item for item in (
    Item("pro_3", 150, "plan", "pro", 3),
    Item("pro_7", 300, "plan", "pro", 7),
    Item("max_3", 400, "plan", "max", 3),
    Item("freeze_1", 80, "freeze"),
)}


class CoinError(Exception):
    """A purchase that cannot happen; `code` is what the API answers with."""

    def __init__(self, code: str, status: int = 409, **extra):
        super().__init__(code)
        self.code, self.status, self.extra = code, status, extra


def _xp_total(s: Session, account_id: int) -> int:
    from services import xp_total
    return xp_total(s, account_id)


def spent(s: Session, account_id: int) -> int:
    return int(s.scalar(select(func.coalesce(func.sum(CoinSpend.coins), 0))
                        .where(CoinSpend.account_id == account_id)) or 0)


def earned(s: Session, account_id: int) -> int:
    return _xp_total(s, account_id) // XP_PER_COIN


def balance(s: Session, account_id: int) -> int:
    return max(earned(s, account_id) - spent(s, account_id), 0)


def _freezes_this_month(s: Session, account_id: int, today: date) -> int:
    start = today.replace(day=1)
    return int(s.scalar(select(func.count(CoinSpend.id)).where(
        CoinSpend.account_id == account_id, CoinSpend.item == "freeze_1",
        CoinSpend.month == start.strftime("%Y-%m"))) or 0)


def _availability(s: Session, account_id: int, item: Item, today: date) -> str | None:
    """None when it can be bought now, else the reason it cannot."""
    if item.kind == "plan" and not plans.ENABLED:
        return "plans_off"
    if item.kind == "freeze" and _freezes_this_month(s, account_id, today) >= FREEZE_PER_MONTH:
        return "freeze_limit"
    return None


def today_coins(s: Session, account_id: int, today: date) -> int:
    """Coins from today's ordinary activity — what the daily cap counts.
    Milestones (streaks, achievements) are paid on top and not shown here."""
    from db import XPEvent
    from services import XP_UNCAPPED
    xp = int(s.scalar(select(func.coalesce(func.sum(XPEvent.xp), 0)).where(
        XPEvent.user_id == account_id, XPEvent.event_date == today,
        XPEvent.event_type.not_in(XP_UNCAPPED))) or 0)
    return xp // XP_PER_COIN


def summary(s: Session, account_id: int, today: date) -> dict:
    """Everything the shop screen draws."""
    from services import XP_DAILY_CAP
    have = balance(s, account_id)
    rows = s.scalars(select(CoinSpend).where(CoinSpend.account_id == account_id)
                     .order_by(CoinSpend.id.desc()).limit(10)).all()
    return {
        "balance": have,
        "earned": earned(s, account_id),
        "spent": spent(s, account_id),
        "today": today_coins(s, account_id, today),
        "daily_cap": XP_DAILY_CAP // XP_PER_COIN,
        "xp_per_coin": XP_PER_COIN,
        "items": [{"key": i.key, "coins": i.coins, "kind": i.kind, "tier": i.tier,
                   "days": i.days, "blocked": _availability(s, account_id, i, today),
                   "short": max(i.coins - have, 0)}
                  for i in ITEMS.values()],
        "history": [{"item": r.item, "coins": r.coins,
                     "at": r.created_at.isoformat() + "Z" if r.created_at else None}
                    for r in rows],
    }


def buy(s: Session, account_id: int, key: str, *, ref: str | None, today: date) -> dict:
    """Spend coins on one item. Idempotent on `ref`. The caller commits."""
    item = ITEMS.get(key)
    if item is None:
        raise CoinError("unknown_item", 422)
    if ref:
        done = s.scalar(select(CoinSpend).where(CoinSpend.ref == ref,
                                                CoinSpend.account_id == account_id))
        if done is not None:
            return {"item": done.item, "repeat": True}
    blocked = _availability(s, account_id, item, today)
    if blocked:
        raise CoinError(blocked)
    if balance(s, account_id) < item.coins:
        raise CoinError("not_enough_coins", short=item.coins - balance(s, account_id))

    row = CoinSpend(account_id=account_id, item=item.key, coins=item.coins,
                    ref=ref, month=today.strftime("%Y-%m"), created_at=utcnow())
    try:
        with s.begin_nested():
            s.add(row)
            s.flush()
    except IntegrityError:
        return {"item": item.key, "repeat": True}
    # Two purchases racing each other: the one that left the balance negative
    # is undone, so coins can never be spent twice.
    if earned(s, account_id) - spent(s, account_id) < 0:
        s.delete(row)
        s.flush()
        raise CoinError("not_enough_coins", short=item.coins)

    if item.kind == "plan":
        plans.grant(s, account_id, item.tier, item.days, "coins", ref=f"coins:{row.id}")
    elif item.kind == "freeze":
        _add_freeze(s, account_id, today)
    return {"item": item.key, "repeat": False}


def _add_freeze(s: Session, account_id: int, today: date) -> None:
    """One more recovery day this month: the streak survives one more weak
    day. Stored as allowance already used going below zero, which the monthly
    reset in the streak code clears with everything else."""
    from services import _progress_row
    row = _progress_row(s, account_id)
    month = today.strftime("%Y-%m")
    if row.recovery_month != month:
        row.recovery_month = month
        row.recovery_used = 0
    row.recovery_used = (row.recovery_used or 0) - 1


def forget(s: Session, ids) -> None:
    from sqlalchemy import delete as sql_delete
    ids = list(ids)
    if ids:
        s.execute(sql_delete(CoinSpend).where(CoinSpend.account_id.in_(ids)))


def export(s: Session, account_id: int) -> list[dict]:
    return [{"item": r.item, "coins": r.coins, "month": r.month,
             "at": r.created_at.isoformat() if r.created_at else None}
            for r in s.scalars(select(CoinSpend).where(CoinSpend.account_id == account_id)
                               .order_by(CoinSpend.id)).all()]
