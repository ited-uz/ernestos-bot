"""
ErnestOS — access policy, in one place.

The bot's `guard` and the API's `auth` were asking the same question in two
different ways: is this account allowed through right now? Two copies of an
access rule is one copy too many — the day they disagree, one surface lets
somebody in that the other locks out, and neither is obviously wrong.

Everything about *whether* a caller may proceed lives here:

  * how long a confirmed membership answer stays fresh;
  * how many actions an account gets before the channel is asked for;
  * the single Telegram round-trip that answers "is this user a member";
  * the decision function both surfaces call.

Nothing here knows about FastAPI or about python-telegram-bot's Update — it
takes a user row and (where it must) a bot handle, and returns a verdict. That
is what lets the same function serve a chat message and an HTTP request.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from dataclasses import dataclass
from datetime import timedelta

import db
from db import SessionLocal, User

log = logging.getLogger("ernestos.access")

#: The channel a user must eventually join, and where to send them.
REQUIRED_CHANNEL_ID = os.environ.get("REQUIRED_CHANNEL_ID", "").strip()
REQUIRED_CHANNEL_URL = os.environ.get("REQUIRED_CHANNEL_URL", "").strip()

#: Statuses Telegram reports for somebody who is actually in the channel.
MEMBER_STATES = {"member", "administrator", "creator", "owner"}

#: How long a confirmed answer is trusted before Telegram is asked again.
#:
#: Three minutes meant a round-trip to Telegram roughly every third action, for
#: a fact that changes a handful of times in an account's life. Ten minutes is
#: still far tighter than it needs to be for correctness, because leaving the
#: channel does not wait for the TTL: `on_chat_member` fires the moment it
#: happens and writes the new state straight away. The TTL only bounds how long
#: a *missed* event can go unnoticed.
MEMBERSHIP_TTL = timedelta(seconds=int(os.environ.get("MEMBERSHIP_TTL", "600")))

#: How many real actions an account gets before the channel is required.
#:
#: The channel used to be the second screen of onboarding — asked before the
#: user had seen a single thing the product does. That is a toll booth at the
#: door: it converts curiosity into a decision about somebody else's marketing
#: channel. Twenty actions is roughly a first proper day — a few tasks, the
#: habits, the prayers — which is long enough for the ask to be answerable
#: ("this thing is useful, fine") instead of a gamble.
FREE_ACTIONS = int(os.environ.get("FREE_ACTIONS", "20"))


@dataclass(frozen=True)
class Trial:
    """Where an account stands in its free run."""

    used: int
    remaining: int
    #: True once the free run is spent and the channel is genuinely required.
    gated: bool
    #: True while the run is still going — nothing to ask for yet.
    free: bool


def trial_state(user: User | None) -> Trial:
    """How much of the free run is left for this account.

    With no channel configured there is nothing to gate on, so every account is
    permanently free — a self-hosted instance must not lock its own users out
    over a channel its operator never set up.
    """
    used = (user.actions_count or 0) if user is not None else 0
    remaining = max(FREE_ACTIONS - used, 0)
    if not REQUIRED_CHANNEL_ID:
        return Trial(used=used, remaining=remaining, gated=False, free=True)
    if user is not None and user.is_subscribed:
        # Already a member: the trial is irrelevant, they are simply in.
        return Trial(used=used, remaining=remaining, gated=False, free=False)
    return Trial(used=used, remaining=remaining,
                 gated=remaining == 0, free=remaining > 0)


def membership_is_fresh(user: User) -> bool:
    return (user.sub_checked_at is not None
            and db.utcnow() - user.sub_checked_at <= MEMBERSHIP_TTL)


def record_membership(s, telegram_id: int, subscribed: bool, source: str) -> bool:
    """Persist a *confirmed* answer. Returns True when the value changed."""
    user = s.get(User, telegram_id)
    if user is None:
        return False
    changed = user.is_subscribed != subscribed
    user.is_subscribed = subscribed
    user.sub_checked_at = db.utcnow()
    user.sub_source = source
    return changed


async def ask_telegram(bot, telegram_id: int, *, retries: int = 2) -> bool | None:
    """True / False / None — None means Telegram could not be asked.

    None is never treated as a pass: callers must show "try again" rather than
    letting an outage silently grant access (audit 001).
    """
    if not REQUIRED_CHANNEL_ID:
        return True
    if bot is None:
        return None
    # Imported here so this module stays importable without the bot library
    # present — the API test suite runs without it.
    from telegram.error import TelegramError

    for attempt in range(retries + 1):
        try:
            member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL_ID,
                                               user_id=telegram_id)
            return member.status in MEMBER_STATES
        except TelegramError as e:
            if attempt == retries:
                log.warning("membership check failed for %s: %s", telegram_id, e)
                return None
            # Jittered backoff so a blip does not lock everyone out at once.
            await asyncio.sleep(0.4 * (attempt + 1) + random.random() * 0.3)
    return None


#: What `check_subscription` can answer.
#:
#:   "ok"       — let them through
#:   "free"     — let them through; the free run is still going
#:   "missing"  — Telegram says they are not a member
#:   "unknown"  — Telegram could not be reached; do not guess
ALLOWED = {"ok", "free"}


async def check_subscription(telegram_id: int, bot=None) -> str:
    """The one access decision, shared by the bot and the API.

    In order:
      1. no channel configured, or the free run still has actions left → in;
      2. a confirmed membership that is still fresh → in, with no round-trip;
      3. otherwise ask Telegram, and write down whatever it says.

    Returns one of the four verdicts above. A caller that gets something
    outside `ALLOWED` must show the join prompt rather than continuing — an
    unreachable Telegram is not a pass.
    """
    with SessionLocal() as s:
        user = s.get(User, telegram_id)
        trial = trial_state(user)
        if trial.free:
            return "free"
        fresh = user is not None and membership_is_fresh(user)
        if fresh and user.is_subscribed:
            return "ok"

    state = await ask_telegram(bot, telegram_id)
    if state is None:
        return "unknown"

    with SessionLocal() as s:
        record_membership(s, telegram_id, state, "api")
        s.commit()
    return "ok" if state else "missing"
