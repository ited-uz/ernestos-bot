"""
ErnestOS — every environment variable the application reads, in one file.

Before this module the settings were spread across four files, and
`ENVIRONMENT` in particular was read twice — once in `db.py` and once in
`app.py` — which is the shape a bug takes before it happens: two names for one
switch, and no reason to expect anybody to update both.

Two rules this file exists to enforce:

  * **nothing is hardcoded.** Every value comes from the environment, with a
    default that is safe for development and obviously wrong for production.
  * **production fails loudly.** A missing `BOT_TOKEN` or `DATABASE_URL` is not
    something to discover from a user complaint two hours after deploy, so the
    process refuses to start instead.

`db.py` still reads `DATABASE_URL` itself, at import time, because it needs the
engine before anything else exists. This module imports the resolved value from
there rather than the other way round, which is what keeps the two from forming
an import cycle.

`app.py` re-exports every name here, so existing call sites and the tests that
patch `application.WEBAPP_URL` keep working exactly as they did.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

#: development | test | production. Read here and imported everywhere else,
#: with one exception: `db.py` reads it directly, because it must decide
#: between PostgreSQL and the development SQLite file at import time, before
#: this module exists. Both reads hit the same environment variable, so they
#: cannot disagree at runtime — `db.py` is simply earlier, not different.
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development").lower()
IS_PRODUCTION = ENVIRONMENT == "production"
#: True while the suite is running: the rate limiter and the Telegram client
#: both step aside, because neither describes the traffic a test generates.
IS_TEST = ENVIRONMENT == "test"

# --- Telegram --------------------------------------------------------------

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

#: Set to run the bot on a webhook instead of long-polling. Unset — the
#: default — keeps polling, which needs no public URL and is the right choice
#: for one instance. Behind a load balancer polling is not an option at all:
#: several instances cannot each long-poll the same bot without stealing one
#: another's updates.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip().rstrip("/")
#: Telegram echoes this back in a header, which is the only thing separating a
#: real update from anybody who guessed the path.
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()

#: The update types this bot acts on. Anything else is refused at Telegram's
#: end rather than delivered and dropped here.
ALLOWED_UPDATES = ["message", "callback_query", "chat_member", "my_chat_member"]


def _bot_username(raw: str) -> str:
    """`@ernestos_bot`, `ernestos_bot` and a full t.me URL all mean one thing."""
    name = (raw or "").strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name.strip("/ ")


#: The bot's public @name, used to build invite links. Optional: without it the
#: referral API reports sharing as unconfigured and the rest of the product is
#: unaffected, rather than the process refusing to start over a growth feature.
BOT_USERNAME = _bot_username(os.environ.get("BOT_USERNAME", ""))

# --- Mini App --------------------------------------------------------------

WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip().rstrip("/")

#: Telegram initData older than this is rejected, so a captured URL cannot be
#: replayed days later.
INIT_DATA_MAX_AGE = int(os.environ.get("INIT_DATA_MAX_AGE", "86400"))

#: Requests larger than this are refused before parsing (audit 013).
MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", str(256 * 1024)))

# --- Operator channels -----------------------------------------------------
#
# Access policy — the channel a user must join, the free run and the membership
# cache — deliberately does *not* appear here. It lives in `dependencies`, and
# is read from there at every call site: two copies of one setting is how the
# bot and the API end up disagreeing about who is let in, and a test that
# patches the copy silently changes nothing.

ADMIN_LOG_CHANNEL_ID = os.environ.get("ADMIN_LOG_CHANNEL_ID", "").strip()
#: Suggestions and complaints get their own channel, apart from event logs.
FEEDBACK_CHANNEL_ID = (os.environ.get("FEEDBACK_CHANNEL_ID", "").strip()
                       or ADMIN_LOG_CHANNEL_ID)
#: Aggregate platform statistics — counts only, never user content.
STATS_CHANNEL_ID = (os.environ.get("STATS_CHANNEL_ID", "").strip()
                    or ADMIN_LOG_CHANNEL_ID)
#: The hour, on the project clock, that the daily statistics post goes out.
STATS_POST_HOUR = int(os.environ.get("STATS_POST_HOUR", "10"))

# --- Scheduler -------------------------------------------------------------

#: How often the report jobs wake up. Report times are per user, so the job
#: cannot be a single cron entry at 04:00 any more; it ticks and asks each user
#: whether their chosen time has arrived.
#:
#: Two minutes, because the tick interval *is* the worst-case lateness: at ten
#: a report set for 21:30 could arrive at 21:40, which reads as the bot being
#: slow. The cost of the extra ticks is one cheap query per user per tick, and
#: sending exactly once a day is still guaranteed by the outbox claim rather
#: than by the schedule.
REPORT_TICK_MINUTES = int(os.environ.get("REPORT_TICK_MINUTES", "2"))


# --- Startup checks --------------------------------------------------------

def check() -> None:
    """Refuse to start a production process that is missing something critical.

    Called at import by `app.py`. Development and tests fall through: a
    contributor with no bot token should still be able to run the API and the
    suite, which is exactly what `ENVIRONMENT` is for.
    """
    if not IS_PRODUCTION:
        return
    missing = [name for name, value in (("BOT_TOKEN", BOT_TOKEN),) if not value]
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} is required in production. "
            "Set it in the deployment environment and redeploy."
        )
    # `db.py` raises on a missing DATABASE_URL at import, before this runs, so
    # reaching here means the database half is already satisfied.
