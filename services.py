"""
ErnestOS — shared business layer.

Every rule lives here exactly once. The Telegram bot and the Mini App API both
call these functions, so a task created in the bot and a task created in the
Mini App go through identical validation and produce identical rows.

Three invariants hold throughout:

  1. Every function takes `workspace_id` and scopes its query to it, so a
     caller can never read or modify another workspace's data.
  2. Local dates use the user's own zone (Asia/Tashkent by default). Grouping
     a day's habits by UTC would put everything after 19:00 local time into
     the wrong day.
  3. **The measurement contract.** A day is scored by one formula, from the
     things that were owed *on that day*: a habit counts from the day it
     started until the day it was archived, only on the days its schedule of
     the time covered and it was not paused; a closed day is read back from
     its snapshot rather than recomputed. Changing a setting today can change
     tomorrow, never last week.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from collections import defaultdict
from datetime import date, datetime, time as dtime, timedelta, timezone as _utc
from zoneinfo import ZoneInfo

from sqlalchemy import case, event, func, or_, select, text as sql_text, update as sql_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import plans

from db import (
    AgentAudit, AgentChatMessage, AgentDraft, AgentPreference,
    Birthday, Countdown, DailyReportLog, DailyScore, Feedback, Habit, HabitLog,
    HabitPauseInterval, HabitScheduleVersion, IdempotencyKey, JobRun,
    Debt, DebtPayment, JournalEntry, LifeGoal, MoneyAccount, MoneyBudget, MoneySubscription, MoneyTransfer, ResetLog, Snooze, MoneyEntry, PrayerDay, PrayerLog, Project,
    Referral, ReferralCode, Task,
    Team, TeamActivity, TeamDayScore, TeamHabit, TeamHabitLog, TeamJoinRequest,
    TeamMember, TeamTask, TeamTaskDone, TimerRun, User, UserAchievement,
    UserProgress, WeeklyFocus, WeeklyReview, Workspace, XPEvent, utcnow,
)

log = logging.getLogger("ernestos")


# ---------------------------------------------------------------------------
# Read memo — one transaction's worth of repeated lookups
# ---------------------------------------------------------------------------
#
# Scoring a day asks the same handful of questions many times over: whose
# workspace is this, which zone is it in, which habits could have been owed,
# which teams was the owner in. Scoring a month asked them thousands of times,
# one database round trip each — /api/summary sent ~3,600 queries for a
# thirty-day account, and on a networked PostgreSQL that is the several
# seconds Home used to wait before it drew anything.
#
# The answers are kept on the session for as long as nothing has been written:
# any flush, commit or rollback throws the memo away, and so does a session
# holding a new or deleted object that has not been flushed yet. A write can
# therefore never be answered from before itself.

_MEMO_KEY = "_ernestos_memo"


def _memo(s: Session) -> dict | None:
    """This session's memo, or None while it holds unflushed adds/deletes."""
    if s.new or s.deleted:
        s.info.pop(_MEMO_KEY, None)
        return None
    return s.info.setdefault(_MEMO_KEY, {})


def _forget(session, *_args) -> None:
    session.info.pop(_MEMO_KEY, None)


for _name in ("after_flush", "after_commit", "after_rollback",
              "after_soft_rollback"):
    event.listen(Session, _name, _forget)


def _memoized(s: Session, key: tuple, compute):
    memo = _memo(s)
    if memo is None:
        return compute()
    if key not in memo:
        memo[key] = compute()
    return memo[key]

#: The default zone, used by every workspace that never chose one.
TZ = ZoneInfo("Asia/Tashkent")

#: The zones offered first. Not a whitelist — the full IANA database follows
#: them in `TIMEZONES` — but the twelve somebody is most likely to be in,
#: sitting at the top of a list of six hundred so the common case stays one
#: tap. A picker sorted purely alphabetically opens on Africa/Abidjan, which
#: is nobody's timezone here.
COMMON_TIMEZONES = [
    "Asia/Tashkent", "Asia/Almaty", "Asia/Dubai", "Asia/Istanbul",
    "Asia/Seoul", "Asia/Tokyo", "Europe/Moscow", "Europe/Berlin",
    "Europe/London", "America/New_York", "America/Los_Angeles", "UTC",
]


def _all_timezones() -> list[str]:
    """Every zone this platform knows, common ones first.

    Deprecated aliases and the `posix/`/`right/` trees are dropped — they are
    the same zones under older names, and six hundred entries is already a long
    list without three copies of each.
    """
    try:
        from zoneinfo import available_timezones
        names = {name for name in available_timezones()
                 if "/" in name and not name.startswith(("posix/", "right/",
                                                         "Etc/", "SystemV/"))}
    except Exception:                    # no tzdata on this platform
        log.warning("no timezone database available — offering the short list")
        return list(COMMON_TIMEZONES)
    # UTC is not in the set above (no slash) and is worth keeping offerable.
    rest = sorted(names - set(COMMON_TIMEZONES))
    return list(COMMON_TIMEZONES) + rest


TIMEZONES = _all_timezones()


class NotFound(Exception):
    """The row does not exist inside the caller's workspace.

    Deliberately indistinguishable from "never existed": probing ids must not
    reveal whether another workspace owns that row.
    """


def tz_for(name: str | None) -> ZoneInfo:
    """Resolve a stored zone name, falling back rather than raising."""
    if not name:
        return TZ
    try:
        return ZoneInfo(name)
    except Exception:
        log.info("unknown timezone %r — using the default", name)
        return TZ


def user_tz(user: User | None) -> ZoneInfo:
    return tz_for(getattr(user, "timezone", None))


def today_local(tz: ZoneInfo | None = None) -> date:
    """Today, in the caller's zone."""
    return datetime.now(tz or TZ).date()


def now_local(tz: ZoneInfo | None = None) -> datetime:
    """Wall-clock local time, without a tzinfo — the form the database holds."""
    return datetime.now(tz or TZ).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# UTC timestamps vs local days
# ---------------------------------------------------------------------------
#
# Two kinds of time live in this database and they must never be compared
# directly:
#
#   * `day` columns are local calendar dates — the day the user was living in;
#   * `created_at` / `completed_at` are naive UTC instants.
#
# These two helpers are the only sanctioned way across the boundary.

def local_date_of(moment: datetime | None, tz: ZoneInfo | None = None) -> date | None:
    """The local calendar date a stored UTC instant fell on."""
    if moment is None:
        return None
    return moment.replace(tzinfo=_utc.utc).astimezone(tz or TZ).date()


def utc_window(first: date, last: date | None = None,
               tz: ZoneInfo | None = None) -> tuple[datetime, datetime]:
    """The half-open UTC range [start, end) covering local days first..last."""
    zone = tz or TZ
    last = last or first
    start = datetime.combine(first, dtime(0, 0)).replace(tzinfo=zone)
    end = (datetime.combine(last, dtime(0, 0)).replace(tzinfo=zone)
           + timedelta(days=1))
    return (start.astimezone(_utc.utc).replace(tzinfo=None),
            end.astimezone(_utc.utc).replace(tzinfo=None))


def week_start(d: date) -> date:
    """Monday of the given date's week."""
    return d - timedelta(days=d.weekday())


#: Month and weekday names for the one date line Home shows. Formatting with
#: the C locale would print "August" to an Uzbek user, and pulling in a locale
#: package for twelve words each is not worth the dependency.
MONTHS = {
    "uz": ["yanvar", "fevral", "mart", "aprel", "may", "iyun",
           "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"],
    "en": ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"],
    "ru": ["января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря"],
}
WEEKDAYS = {
    "uz": ["Dushanba", "Seshanba", "Chorshanba", "Payshanba",
           "Juma", "Shanba", "Yakshanba"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday",
           "Friday", "Saturday", "Sunday"],
    "ru": ["Понедельник", "Вторник", "Среда", "Четверг",
           "Пятница", "Суббота", "Воскресенье"],
}


def date_label(day: date, lang: str = "uz") -> str:
    """The local date, written the way that language writes it."""
    lang = lang if lang in MONTHS else "uz"
    month = MONTHS[lang][day.month - 1]
    weekday = WEEKDAYS[lang][day.weekday()]
    if lang == "uz":
        return f"{day.day}-{month}, {weekday}"
    if lang == "ru":
        return f"{day.day} {month}, {weekday}"
    return f"{month} {day.day}, {weekday}"


# ---------------------------------------------------------------------------
# Users and workspaces
# ---------------------------------------------------------------------------

#: Habits are grouped into three tiers everywhere they are shown.
HABIT_CATEGORIES = ["non_negotiable", "target", "bonus"]

#: What each tier is worth inside the habit percentage. Fifty / thirty /
#: twenty: non-negotiable is worth more than the other two put together, which
#: is what "non-negotiable" has to mean if the word is doing any work.
HABIT_TIER_WEIGHTS = {"non_negotiable": 50, "target": 30, "bonus": 20}

#: (name, category, system_key). A system_key marks a derived habit the user
#: cannot tick by hand: "wakeup" follows the morning check-in, "prayer" follows
#: the prayer record and "journal" follows a written journal entry.
#:
#: This tuple is the only place defaults are defined. `seed_default_habits` is
#: the only reader, and it runs on account creation and on an explicit wipe —
#: never against an existing workspace. Which of the three a new account keeps
#: is the user's own choice at setup (`set_modules`).
DEFAULT_HABITS = [
    ("Get up",   "non_negotiable", "wakeup"),
    ("5x namoz", "non_negotiable", "prayer"),
    ("Kundalik", "non_negotiable", "journal"),
]

#: What the three rituals are called in each language. Rows are created with
#: the DEFAULT_HABITS names and renamed to these when the person picks (or
#: changes) a language, so an Uzbek screen never says "Get up".
SYSTEM_HABIT_NAMES = {
    "wakeup": {"uz": "Erta turish", "en": "Wake up early", "ru": "Ранний подъём"},
    "prayer": {"uz": "5 vaqt namoz", "en": "5 daily prayers", "ru": "5 намазов"},
    "journal": {"uz": "Kun xulosasi", "en": "Day summary", "ru": "Итоги дня"},
}

SYSTEM_PRAYER = "prayer"
#: The journal is a non-negotiable habit, and one meaningful answer is enough
#: to tick it: a short evening is still a written day. All five answers is a
#: full reflection, which the screens name separately.
SYSTEM_JOURNAL = "journal"
SYSTEM_WAKEUP = "wakeup"
SYSTEM_KEYS = (SYSTEM_WAKEUP, SYSTEM_PRAYER, SYSTEM_JOURNAL)

#: The modules a person chooses at setup, and the habit each one drives.
MODULES = {"wake": SYSTEM_WAKEUP, "prayer": SYSTEM_PRAYER, "journal": SYSTEM_JOURNAL}

#: Default rise time, used until the user picks their own.
DEFAULT_WAKE_TIME = dtime(5, 0)
#: How long after the target time a "turdim" message still counts.
WAKE_GRACE = timedelta(hours=1)


#: How many times to re-read the maximum member number before giving up.
MEMBER_NO_ATTEMPTS = 5


def get_or_create_user(s: Session, telegram_id: int, *, first_name: str = "",
                       last_name: str = "", username: str = "") -> tuple[User, bool]:
    """Return (user, created). Creating a user also builds their workspace."""
    user = s.get(User, telegram_id)
    if user is not None:
        if user.plan_started_at is None and plans.ENABLED:
            plans.ensure_started(s, user, created=False)   # launch gift, once
        # Keep Telegram profile fields fresh, but never overwrite with blanks.
        if first_name and not user.onboarded:
            user.first_name = first_name
        elif first_name and not user.first_name:
            user.first_name = first_name
        if last_name:
            user.last_name = last_name
        if username:
            user.username = username
        return user, False

    # Sequential join number: max()+1 rather than a count, so deleting a user
    # never hands their number to somebody else. The column is unique, so the
    # loser of a same-second race fails on flush and simply tries again, inside
    # a SAVEPOINT so the retry cannot damage the caller's transaction.
    for _ in range(MEMBER_NO_ATTEMPTS):
        next_no = (s.scalar(select(func.max(User.member_no))) or 0) + 1
        user = User(telegram_id=telegram_id, member_no=next_no,
                    first_name=first_name or "", last_name=last_name or "",
                    username=username or "")
        s.add(user)
        try:
            with s.begin_nested():
                s.flush()
            break
        except IntegrityError:
            s.expunge(user)
    else:
        raise RuntimeError(
            f"could not allocate a member number after {MEMBER_NO_ATTEMPTS} tries")

    workspace = Workspace(user_id=telegram_id)
    s.add(workspace)
    s.flush()

    seed_default_habits(s, workspace.id)
    plans.ensure_started(s, user, created=True)   # the Pro trial
    s.commit()
    return user, True


def seed_default_habits(s: Session, ws: int) -> None:
    """Put the starting set of habits into an empty workspace. The caller commits."""
    for position, (name, category, system_key) in enumerate(DEFAULT_HABITS, start=1):
        s.add(Habit(workspace_id=ws, name=name, category=category,
                    position=position, is_protected=bool(system_key),
                    system_key=system_key,
                    target_time=DEFAULT_WAKE_TIME if system_key == SYSTEM_WAKEUP else None))


def workspace_id_for(s: Session, telegram_id: int) -> int:
    ws = s.scalar(select(Workspace).where(Workspace.user_id == telegram_id))
    if ws is None:
        raise NotFound("workspace")
    return ws.id


def touch_activity(s: Session, telegram_id: int) -> None:
    """Record a real interaction. Scheduler jobs must not call this."""
    user = s.get(User, telegram_id)
    if user is not None:
        user.last_active_at = utcnow()


def record_action(s: Session, telegram_id: int) -> int:
    """Count one thing actually done, and return the new total.

    One UPDATE that adds one, rather than "read, add, write": two writes
    landing in the same instant — a double tap, a bot tap and an API call —
    used to read the same number and both write it plus one, losing a count.
    The database does the addition, so twenty concurrent actions add twenty.
    The caller commits.
    """
    result = s.execute(
        sql_update(User).where(User.telegram_id == telegram_id)
        .values(actions_count=func.coalesce(User.actions_count, 0) + 1)
        .execution_options(synchronize_session=False))
    if not result.rowcount:
        return 0
    value = s.scalar(select(User.actions_count)
                     .where(User.telegram_id == telegram_id)) or 0
    cached = s.get(User, telegram_id)
    if cached is not None:
        # Keep the identity map honest without reloading the whole row.
        s.expire(cached, ["actions_count"])
    return int(value)


def record_action_and_qualify(s: Session, telegram_id: int) -> tuple[int, int | None]:
    """`record_action`, then the referral check. Returns (total, inviter_to_tell)."""
    total = record_action(s, telegram_id)
    s.commit()
    return total, maybe_qualify_referral(s, telegram_id)


def record_action_and_progress(s: Session, telegram_id: int) -> dict:
    """The action counter, the referral check and personal progression, once.

    Progression failing must never fail the user's actual action. Ticking a
    task is the thing they asked for; recomputing their level is bookkeeping
    that happens to be attached to it, and if the bookkeeping raises, the tick
    still stands.
    """
    total, inviter = record_action_and_qualify(s, telegram_id)
    result: dict = {}
    try:
        result = refresh_progress(s, telegram_id)
        s.commit()
    except Exception:
        s.rollback()
        log.exception("progression refresh failed for %s", telegram_id)
    return {"actions": total, "inviter_to_tell": inviter, "progress": result}


def set_subscription(s: Session, telegram_id: int, subscribed: bool) -> bool:
    """Update membership state. Returns True when the value actually changed."""
    user = s.get(User, telegram_id)
    if user is None or user.is_subscribed == subscribed:
        return False
    user.is_subscribed = subscribed
    return True


# ---------------------------------------------------------------------------
# Modules — which of the three rituals somebody actually keeps
# ---------------------------------------------------------------------------
#
# A new account used to be handed getting up, prayer and the journal whether
# it wanted them or not, and some of them could not be removed. A shift worker,
# somebody who travels, or somebody who only wanted a task list was in debt
# from the first morning. The rituals are now modules: chosen at setup,
# switched on and off in Settings, and a switched-off module keeps every log it
# ever had — switching it back on brings the same habit back.

def modules_for(s: Session, ws: int) -> dict[str, bool]:
    """{"wake": True, "prayer": False, …} — which rituals are live."""
    live = set(s.scalars(select(Habit.system_key).where(
        Habit.workspace_id == ws, Habit.archived_at.is_(None),
        Habit.system_key.in_(SYSTEM_KEYS))).all())
    return {name: key in live for name, key in MODULES.items()}


def set_modules(s: Session, ws: int, chosen, *, user: User | None = None) -> dict[str, bool]:
    """Switch the three rituals on or off, keeping their history either way.

    Off archives the habit; on brings back the most recent one, or creates it
    if there never was one. Archiving follows the ordinary rule — a habit is
    owed up to the day it was archived — except on the day it was created,
    when switching it off means it was never owed at all: that is the setup
    screen, where nothing has been missed yet.
    """
    chosen = {name for name in (chosen or ()) if name in MODULES}
    tz = _habit_tz(s, ws)
    today = today_local(tz)
    for name, key in MODULES.items():
        live = s.scalar(select(Habit).where(
            Habit.workspace_id == ws, Habit.system_key == key,
            Habit.archived_at.is_(None)))
        if name in chosen and live is None:
            old = s.scalar(select(Habit).where(
                Habit.workspace_id == ws, Habit.system_key == key)
                .order_by(Habit.id.desc()).limit(1))
            if old is not None:
                _reactivate(s, ws, old, today, tz)
            else:
                spec = next(d for d in DEFAULT_HABITS if d[2] == key)
                top = s.scalar(select(func.max(Habit.position))
                               .where(Habit.workspace_id == ws)) or 0
                s.add(Habit(workspace_id=ws, name=spec[0], category=spec[1],
                            position=top + 1, is_protected=True, system_key=key,
                            target_time=DEFAULT_WAKE_TIME if key == SYSTEM_WAKEUP
                            else None))
        elif name not in chosen and live is not None:
            live.archived_at = utcnow()
    if user is not None:
        user.modules = ",".join(sorted(chosen))
        localize_system_habits(s, ws, user.language)
    s.commit()
    return modules_for(s, ws)


#: Stock names from before v12.2, still renamed when someone never changed them.
LEGACY_RITUAL_NAMES = {"kundalik", "journal", "дневник"}


def localize_system_habits(s: Session, ws: int, lang: str) -> None:
    """Rename the rituals to `lang`, unless the person gave one its own name.

    Only a name that is still one of the stock names (any language, or the
    old English/Uzbek defaults) is touched. The caller commits.
    """
    stock = {name.casefold() for names in SYSTEM_HABIT_NAMES.values() for name in names.values()}
    stock |= {name.casefold() for name, _c, _k in DEFAULT_HABITS}
    stock |= LEGACY_RITUAL_NAMES
    for habit in s.scalars(select(Habit).where(Habit.workspace_id == ws,
                                               Habit.system_key.in_(list(SYSTEM_HABIT_NAMES)))):
        if habit.name.strip().casefold() in stock:
            habit.name = SYSTEM_HABIT_NAMES[habit.system_key].get(lang, habit.name)


def _reactivate(s: Session, ws: int, habit: Habit, today: date, tz: ZoneInfo) -> None:
    """Bring an archived habit back without making its time away a debt.

    Clearing `archived_at` alone would make the habit look live all along, and
    every day it was switched off would turn into a missed day. The days away
    become a closed pause instead — so the record before them stays exactly
    as it was, and nothing between is owed.
    """
    archived = local_date_of(habit.archived_at, tz)
    habit.archived_at = None
    if archived is None or archived >= today:
        return
    start = habit.active_from or local_date_of(habit.created_at, tz)
    if start is not None and start >= archived:
        # Switched off on its first day, so never owed: it starts now.
        habit.active_from = today
        return
    first_off, last_off = archived + timedelta(days=1), today - timedelta(days=1)
    if first_off <= last_off:
        s.add(HabitPauseInterval(kind=HABIT_KIND, item_id=habit.id,
                                 workspace_id=ws, start_day=first_off,
                                 end_day=last_off))


# ---------------------------------------------------------------------------
# Habits
# ---------------------------------------------------------------------------

#: A habit that is not expected today is not a habit the user failed. The
#: schedule decides whether it counts towards the day at all.
SCHEDULE_DAILY = "daily"
SCHEDULE_WEEKDAYS = "weekdays"
SCHEDULE_PREFIX_DAYS = "days:"

#: The two kinds the schedule-version and pause tables serve.
HABIT_KIND = "habit"
TEAM_HABIT_KIND = "team_habit"

#: The oldest day a first schedule version reaches back to: "since always".
_BEGINNING = date(2000, 1, 1)


def clean_schedule(value: str | None) -> str:
    """Normalise a schedule, or fall back to daily."""
    value = (value or "").strip().lower()
    if value == SCHEDULE_WEEKDAYS:
        return SCHEDULE_WEEKDAYS
    if value.startswith(SCHEDULE_PREFIX_DAYS):
        days = sorted({int(x) for x in value[len(SCHEDULE_PREFIX_DAYS):].split(",")
                       if x.strip().isdigit() and 0 <= int(x) <= 6})
        # An empty or all-day list is just "daily" written the long way.
        if not days or len(days) == 7:
            return SCHEDULE_DAILY
        return SCHEDULE_PREFIX_DAYS + ",".join(str(d) for d in days)
    return SCHEDULE_DAILY


def schedule_days(schedule: str | None) -> list[int]:
    """The weekdays a schedule covers, 0 = Monday."""
    schedule = clean_schedule(schedule)
    if schedule == SCHEDULE_WEEKDAYS:
        return [0, 1, 2, 3, 4]
    if schedule.startswith(SCHEDULE_PREFIX_DAYS):
        return [int(x) for x in schedule[len(SCHEDULE_PREFIX_DAYS):].split(",")]
    return [0, 1, 2, 3, 4, 5, 6]


class DueCalendar:
    """Which habits were owed on which local days — the measurement contract.

    One object answers the question for every backwards-looking number, so the
    streak, the history grid, the day's percentage and the chart cannot give
    four different answers about the same Tuesday. A habit is owed on a day
    when all of these hold:

      * the day is on or after the day it started (`active_from`, else the day
        it was created) — a habit added today was not missed last week;
      * it had not been archived before that day — archiving today keeps every
        past day, and today, exactly as they were;
      * no pause interval covers the day;
      * the schedule *in force on that day* includes its weekday.

    Built once per question with every version and pause loaded in two queries
    (`load`), then asked as often as needed without touching the database.
    """

    def __init__(self, tz: ZoneInfo | None = None):
        self.tz = tz or TZ
        self.versions: dict[tuple[str, int], list[tuple[date, str]]] = defaultdict(list)
        self.pauses: dict[tuple[str, int], list[tuple[date, date | None]]] = defaultdict(list)

    @classmethod
    def load(cls, s: Session, items: dict[str, list[int]],
             tz: ZoneInfo | None = None) -> "DueCalendar":
        """A calendar for {kind: [ids]} — two queries, whatever the size."""
        cal = cls(tz)
        wanted = [(kind, ids) for kind, ids in items.items() if ids]
        if not wanted:
            return cal
        cond_v = or_(*[(HabitScheduleVersion.kind == kind)
                       & HabitScheduleVersion.item_id.in_(ids) for kind, ids in wanted])
        for kind, item_id, valid_from, schedule in s.execute(
                select(HabitScheduleVersion.kind, HabitScheduleVersion.item_id,
                       HabitScheduleVersion.valid_from, HabitScheduleVersion.schedule)
                .where(cond_v).order_by(HabitScheduleVersion.valid_from,
                                        HabitScheduleVersion.id)).all():
            cal.versions[(kind, item_id)].append((valid_from, schedule))
        cond_p = or_(*[(HabitPauseInterval.kind == kind)
                       & HabitPauseInterval.item_id.in_(ids) for kind, ids in wanted])
        for kind, item_id, start, end in s.execute(
                select(HabitPauseInterval.kind, HabitPauseInterval.item_id,
                       HabitPauseInterval.start_day, HabitPauseInterval.end_day)
                .where(cond_p)).all():
            cal.pauses[(kind, item_id)].append((start, end))
        return cal

    @staticmethod
    def kind_of(habit) -> str:
        return TEAM_HABIT_KIND if isinstance(habit, TeamHabit) else HABIT_KIND

    def start_of(self, habit) -> date | None:
        return getattr(habit, "active_from", None) or local_date_of(habit.created_at, self.tz)

    def end_of(self, habit) -> date | None:
        """The last day the habit is owed, or None while it is live."""
        if habit.archived_at is None:
            return None
        return local_date_of(habit.archived_at, self.tz)

    def exists_on(self, habit, day: date) -> bool:
        start = self.start_of(habit)
        end = self.end_of(habit)
        if start is not None and day < start:
            return False
        if end is not None:
            # Archived on the very day it started: it was never owed at all —
            # a habit set up by mistake and removed is not a missed habit.
            if start is not None and start >= end:
                return False
            if day > end:
                return False
        return True

    def paused_on(self, habit, day: date) -> bool:
        intervals = self.pauses.get((self.kind_of(habit), habit.id), [])
        for start, end in intervals:
            if start <= day and (end is None or day <= end):
                return True
        # A pause from before intervals existed: it runs from the day it was
        # made. Reading it that way keeps today exactly as it was and gives
        # every earlier day back the misses the old rule erased.
        if habit.paused_at is not None and not any(e is None for _, e in intervals):
            legacy = local_date_of(habit.paused_at, self.tz)
            if legacy is not None and day >= legacy:
                return True
        return False

    def pause_pending(self, habit, day: date) -> date | None:
        """The day a pause will start, when one is booked but not running yet."""
        for start, end in self.pauses.get((self.kind_of(habit), habit.id), []):
            if end is None and start > day:
                return start
        return None

    def schedule_on(self, habit, day: date) -> str:
        chosen = None
        for valid_from, schedule in self.versions.get((self.kind_of(habit), habit.id), []):
            if valid_from <= day:
                chosen = schedule
        return clean_schedule(chosen if chosen is not None else habit.schedule)

    def due(self, habit, day: date) -> bool:
        if not self.exists_on(habit, day):
            return False
        if self.paused_on(habit, day):
            return False
        return day.weekday() in schedule_days(self.schedule_on(habit, day))


def calendar_for(s: Session, habits, tz: ZoneInfo | None = None) -> DueCalendar:
    """A calendar for any mix of personal and team habits."""
    items: dict[str, list[int]] = {HABIT_KIND: [], TEAM_HABIT_KIND: []}
    for habit in habits:
        items[DueCalendar.kind_of(habit)].append(habit.id)
    key = ("calendar", str(tz or TZ), tuple(sorted(items[HABIT_KIND])),
           tuple(sorted(items[TEAM_HABIT_KIND])))
    return _memoized(s, key, lambda: DueCalendar.load(s, items, tz))


def habit_is_due(habit, day: date, cal: DueCalendar | None = None) -> bool:
    """Whether this habit is expected on that day.

    With a calendar, the full contract. Without one — a caller holding a
    single row and no session — the current schedule and pause, which is
    right for today and only for today.
    """
    if cal is not None:
        return cal.due(habit, day)
    if habit.paused_at is not None:
        return False
    return day.weekday() in schedule_days(habit.schedule)


def _habit_tz(s: Session, ws: int) -> ZoneInfo:
    def compute():
        owner = workspace_owner(s, ws)
        return user_tz(s.get(User, owner)) if owner else TZ
    return _memoized(s, ("tz", ws), compute)


def _record_schedule_change(s: Session, kind: str, habit, new_schedule: str, *,
                            today: date, workspace_id: int | None = None,
                            team_id: int | None = None) -> date:
    """Write the new schedule as a version, effective from the next day.

    A habit that started today has no past to protect, so the change applies
    at once. Returns the day the new schedule starts.
    """
    new_schedule = clean_schedule(new_schedule)
    existing = s.scalars(select(HabitScheduleVersion).where(
        HabitScheduleVersion.kind == kind,
        HabitScheduleVersion.item_id == habit.id)).all()
    start = (getattr(habit, "active_from", None)
             or local_date_of(habit.created_at) or today)
    effective = today if start >= today else today + timedelta(days=1)
    if not existing:
        s.add(HabitScheduleVersion(kind=kind, item_id=habit.id,
                                   workspace_id=workspace_id, team_id=team_id,
                                   valid_from=_BEGINNING,
                                   schedule=clean_schedule(habit.schedule)))
    same_day = next((v for v in existing if v.valid_from == effective), None)
    if same_day is not None:
        same_day.schedule = new_schedule
    else:
        s.add(HabitScheduleVersion(kind=kind, item_id=habit.id,
                                   workspace_id=workspace_id, team_id=team_id,
                                   valid_from=effective, schedule=new_schedule))
    habit.schedule = new_schedule
    return effective


def _apply_pause(s: Session, kind: str, habit, paused: bool, *, today: date,
                 from_today: bool, workspace_id: int | None = None,
                 team_id: int | None = None, tz: ZoneInfo | None = None) -> date | None:
    """Start or end a pause as an interval of days. Returns the start day.

    A pause starts tomorrow unless the caller asked for today — the screens
    offer both and say which one today's number will reflect. Ending one
    closes it at yesterday, so today is owed again; a pause that had not
    started yet is simply removed.
    """
    intervals = s.scalars(select(HabitPauseInterval).where(
        HabitPauseInterval.kind == kind,
        HabitPauseInterval.item_id == habit.id)).all()
    open_ones = [i for i in intervals if i.end_day is None]

    if paused:
        if open_ones:
            return open_ones[0].start_day
        if habit.paused_at is not None:
            # Already paused under the old single-column rule.
            return local_date_of(habit.paused_at, tz)
        start = today if from_today else today + timedelta(days=1)
        s.add(HabitPauseInterval(kind=kind, item_id=habit.id,
                                 workspace_id=workspace_id, team_id=team_id,
                                 start_day=start, end_day=None))
        habit.paused_at = utcnow()
        return start

    yesterday = today - timedelta(days=1)
    if not open_ones and habit.paused_at is not None:
        legacy = local_date_of(habit.paused_at, tz)
        if legacy is not None and legacy <= yesterday:
            s.add(HabitPauseInterval(kind=kind, item_id=habit.id,
                                     workspace_id=workspace_id, team_id=team_id,
                                     start_day=legacy, end_day=yesterday))
    for interval in open_ones:
        if interval.start_day > yesterday:
            s.delete(interval)
        else:
            interval.end_day = yesterday
    habit.paused_at = None
    return None


def _habit_dict(habit: Habit, day: date, done: bool,
                run: dict | None = None, cal: DueCalendar | None = None,
                qty: int | None = None) -> dict:
    paused_now = cal.paused_on(habit, day) if cal else habit.paused_at is not None
    pending = cal.pause_pending(habit, day) if cal else None
    return {
        "id": habit.id, "name": habit.name, "category": habit.category,
        "protected": habit.is_protected, "system_key": habit.system_key,
        "target_time": habit.target_time.strftime("%H:%M") if habit.target_time else None,
        "remind_at": habit.remind_at.strftime("%H:%M") if habit.remind_at else None,
        "schedule": clean_schedule(habit.schedule),
        "days": schedule_days(habit.schedule),
        # The schedule in force *today*, which differs from `schedule` on the
        # day a change was made: the new one starts tomorrow.
        "schedule_today": cal.schedule_on(habit, day) if cal else clean_schedule(habit.schedule),
        "paused": paused_now,
        "pause_from": pending.isoformat() if pending else None,
        "active_from": (habit.active_from.isoformat() if habit.active_from else None),
        "due": habit_is_due(habit, day, cal),
        "done": done,
        # Prayer has a section and a score of its own; its habit row is a
        # shortcut to it and is deliberately left out of the habit count.
        "scored": habit.system_key != SYSTEM_PRAYER,
        **_timer_fields(habit.timer_minutes, habit.name,
                        protected=habit.is_protected),
        "timer": run,
        "target_qty": habit.target_qty, "min_qty": habit.min_qty, "unit": habit.unit or "",
        "qty": qty or 0,
        # Something real was done, short of the goal: the minimal version.
        "minimal": bool(habit.target_qty and not done and habit.min_qty
                        and (qty or 0) >= habit.min_qty),
    }


def clean_quantity(target, minimum, unit) -> tuple[int | None, int | None, str | None]:
    """Validate a measured habit's goal, minimal version and unit."""
    if not target:
        return None, None, None
    target = int(target)
    if not 1 <= target <= 100_000:
        raise ValueError("bad_target")
    minimum = int(minimum) if minimum else None
    if minimum is not None and not 1 <= minimum < target:
        raise ValueError("bad_minimum")
    unit = " ".join(str(unit or "").split())[:16] or None
    return target, minimum, unit


def log_habit_qty(s: Session, ws: int, habit_id: int, qty: int,
                  day: date | None = None, *, tz: ZoneInfo | None = None) -> dict:
    """Write how much of a measured habit was done today.

    Reaching the goal ticks it; anything less is kept as the amount done —
    12 of 20 pages is neither 20 nor zero (audit #6).
    """
    habit = _owned_habit(s, ws, habit_id)
    if habit.archived_at is not None:
        raise NotFound("habit")
    if habit.is_protected:
        raise ValueError("protected")
    if not habit.target_qty:
        raise ValueError("not_measured")
    qty = int(qty)
    if not 0 <= qty <= 1_000_000:
        raise ValueError("bad_qty")
    tz = tz or _habit_tz(s, ws)
    day = day or today_local(tz)
    # The same gates as the tick: a paused habit is not owed, and a habit
    # done by its timer is not reached by typing a number.
    if calendar_for(s, [habit], tz).paused_on(habit, day):
        raise ValueError("paused")
    if qty >= habit.target_qty and timer_blocks(s, ws, "habit", habit, day):
        raise ValueError("timer_required")
    row = s.scalar(select(HabitLog).where(
        HabitLog.workspace_id == ws, HabitLog.habit_id == habit_id, HabitLog.day == day))
    if row is None:
        row = HabitLog(workspace_id=ws, habit_id=habit_id, day=day, done=False)
        s.add(row)
        try:
            with s.begin_nested():
                s.flush()
        except IntegrityError:
            # A second save raced the first; write onto the row it made.
            row = s.scalar(select(HabitLog).where(
                HabitLog.workspace_id == ws, HabitLog.habit_id == habit_id,
                HabitLog.day == day))
    row.qty = qty
    row.done = qty >= habit.target_qty
    row.logged_at = now_local(tz) if qty else None
    s.commit()
    return {"qty": qty, "done": row.done, "target_qty": habit.target_qty,
            "minimal": bool(not row.done and habit.min_qty and qty >= habit.min_qty)}


def _active_habits(s: Session, ws: int) -> list[Habit]:
    return list(s.scalars(
        select(Habit)
        .where(Habit.workspace_id == ws, Habit.archived_at.is_(None))
        .order_by(Habit.position, Habit.id)).all())


def _habits_owed_candidates(s: Session, ws: int, first: date) -> list[Habit]:
    """Every habit that could have been owed on or after `first`.

    Live ones, plus archived ones whose archive came after the window opened —
    an archived habit still counts on the days before it was archived.
    """
    start, _ = utc_window(first - timedelta(days=1), first - timedelta(days=1))
    return [h for h in _workspace_habits(s, ws)
            if h.archived_at is None or h.archived_at >= start]


def _workspace_habits(s: Session, ws: int) -> list[Habit]:
    """Every habit the workspace ever had, archived ones included, in order."""
    return _memoized(s, ("habits", ws), lambda: list(s.scalars(
        select(Habit).where(Habit.workspace_id == ws)
        .order_by(Habit.position, Habit.id)).all()))


def list_habits(s: Session, ws: int, day: date | None = None, *,
                tz: ZoneInfo | None = None) -> list[dict]:
    """Habits with that day's completion state, in display order.

    Paused habits stay in the list — hidden away, a paused habit is one the
    user cannot resume — but carry `paused: true` and `due: false`.
    """
    tz = tz or _habit_tz(s, ws)
    day = day or today_local(tz)
    habits = _active_habits(s, ws)
    if not habits:
        return []

    # A timer whose time ran out while nobody was looking has already done
    # its habit; the list must say so rather than wait for the next job tick.
    settle_timers(s, ws)
    done_ids = set(s.scalars(
        select(HabitLog.habit_id).where(
            HabitLog.workspace_id == ws,
            HabitLog.day == day,
            HabitLog.done.is_(True),
        )
    ).all())
    runs = open_timer_runs(s, ws, "habit", day=day)
    cal = calendar_for(s, habits, tz)
    qtys = dict(s.execute(select(HabitLog.habit_id, HabitLog.qty).where(
        HabitLog.workspace_id == ws, HabitLog.day == day,
        HabitLog.qty.is_not(None))).all())
    return [_habit_dict(h, day, h.id in done_ids, runs.get(h.id), cal, qtys.get(h.id))
            for h in habits]


def habits_by_category(s: Session, ws: int, day: date | None = None, *,
                       tz: ZoneInfo | None = None,
                       include_team: bool = True) -> dict:
    """Habits grouped into the three tiers, preserving display order.

    Shared habits are in these groups, not in a block of their own. Each row
    says where it came from instead.
    """
    grouped: dict[str, list[dict]] = {c: [] for c in HABIT_CATEGORIES}
    for habit in list_habits(s, ws, day, tz=tz):
        grouped.setdefault(habit["category"], []).append(
            {**habit, "source": "personal", "team_id": None, "team_name": None})

    if include_team:
        owner = workspace_owner(s, ws)
        today = day or today_local(tz)
        for team in (teams_for(s, owner) if owner else []):
            for habit in list_team_habits(s, owner, team.id, day=today, tz=tz):
                grouped.setdefault(habit["category"], []).append(
                    {**habit, "source": "team",
                     "team_id": team.id, "team_name": team.name})
    return grouped


def add_habit(s: Session, ws: int, name: str, category: str = "target", *,
              schedule: str | None = None, remind_at: dtime | None = None,
              timer_minutes: int | None = None,
              start: str | None = None, tz: ZoneInfo | None = None,
              target_qty: int | None = None, min_qty: int | None = None,
              unit: str | None = None) -> Habit:
    """A new habit, owed from today or — when `start == "tomorrow"` — from tomorrow.

    Adding a habit at 22:00 that is owed today lowers a day that is nearly
    over, which reads as a punishment for planning. The screens offer the
    choice; the default stays today, which is what somebody adding a habit in
    the morning means.
    """
    name = name.strip()[:120]
    if not name:
        raise ValueError("empty habit name")
    if category not in HABIT_CATEGORIES:
        category = "target"
    tz = tz or _habit_tz(s, ws)
    today = today_local(tz)
    # The three rituals do not count against the plan; everything else does.
    plans.require_in_workspace(s, ws, "habits", s.scalar(
        select(func.count(Habit.id)).where(Habit.workspace_id == ws,
                                           Habit.archived_at.is_(None),
                                           Habit.system_key == "")) or 0)
    top = s.scalar(select(func.max(Habit.position)).where(Habit.workspace_id == ws)) or 0
    target_qty, min_qty, unit = clean_quantity(target_qty, min_qty, unit)
    habit = Habit(workspace_id=ws, name=name, category=category, position=top + 1,
                  schedule=clean_schedule(schedule), remind_at=remind_at,
                  target_qty=target_qty, min_qty=min_qty, unit=unit,
                  timer_minutes=clean_timer_minutes(timer_minutes),
                  active_from=today + timedelta(days=1) if start == "tomorrow" else today)
    s.add(habit)
    s.commit()
    return habit


def update_habit(s: Session, ws: int, habit_id: int, **fields) -> Habit:
    """Edit a habit in place.

    A habit the user cannot rename or reschedule is one they delete and
    recreate, which throws away every log it had. So an ordinary habit's name,
    tier, schedule and reminder are all the user's to set.

    The three rituals can be renamed, moved to another tier and given a
    reminder like any other habit — the module finds its habit by
    `system_key`, never by name. They keep their daily schedule and never take
    a timer: "5x namoz on Mondays" would not mean "I pray on Mondays", it
    would mean the other six days stop counting.

    A schedule change is a new version, effective tomorrow: the days that
    already happened keep the schedule they were lived under.
    """
    habit = _owned_habit(s, ws, habit_id)
    tz = _habit_tz(s, ws)

    if "name" in fields and fields["name"] is not None:
        name = str(fields["name"]).strip()[:120]
        if not name:
            raise ValueError("empty habit name")
        habit.name = name
    if fields.get("category") in HABIT_CATEGORIES:
        habit.category = fields["category"]
    if ("schedule" in fields and fields["schedule"] is not None
            and not habit.is_protected
            and clean_schedule(fields["schedule"]) != clean_schedule(habit.schedule)):
        _record_schedule_change(s, HABIT_KIND, habit, fields["schedule"],
                                today=today_local(tz), workspace_id=ws)
    if "remind_at" in fields:
        habit.remind_at = fields["remind_at"]
    if "target_time" in fields and fields["target_time"] is not None:
        habit.target_time = fields["target_time"]
    if "timer_minutes" in fields and not habit.is_protected:
        _apply_timer_setting(s, ws, "habit", habit, fields["timer_minutes"])
    if "target_qty" in fields and not habit.is_protected:
        habit.target_qty, habit.min_qty, habit.unit = clean_quantity(
            fields["target_qty"], fields.get("min_qty", habit.min_qty),
            fields.get("unit", habit.unit))
    elif "min_qty" in fields and habit.target_qty:
        habit.target_qty, habit.min_qty, habit.unit = clean_quantity(
            habit.target_qty, fields["min_qty"], fields.get("unit", habit.unit))
    s.commit()
    return habit


def set_habit_paused(s: Session, ws: int, habit_id: int, paused: bool, *,
                     from_day: str = "today") -> Habit:
    """Pause or resume a habit without touching a single log row.

    `from_day` is "today" or "tomorrow". Today is what a user who is ill this
    morning means; tomorrow is what somebody planning a holiday means — and it
    leaves today's number exactly where it is.
    """
    habit = _owned_habit(s, ws, habit_id)
    tz = _habit_tz(s, ws)
    _apply_pause(s, HABIT_KIND, habit, paused, today=today_local(tz),
                 from_today=from_day != "tomorrow", workspace_id=ws, tz=tz)
    s.commit()
    return habit


def reorder_habits(s: Session, ws: int, habit_ids: list[int]) -> list[dict]:
    """Persist a new display order and return the canonical list."""
    if not habit_ids:
        raise ValueError("empty order")
    if len(set(habit_ids)) != len(habit_ids):
        raise ValueError("duplicate habit")

    current = s.scalars(
        select(Habit)
        .where(Habit.workspace_id == ws, Habit.archived_at.is_(None))
        .order_by(Habit.position, Habit.id)
    ).all()
    by_id = {h.id: h for h in current}

    for habit_id in habit_ids:
        if habit_id not in by_id:
            raise NotFound("habit")

    ordered = [by_id[habit_id] for habit_id in habit_ids]
    ordered += [h for h in current if h.id not in set(habit_ids)]

    for position, habit in enumerate(ordered, start=1):
        habit.position = position
    s.commit()
    return list_habits(s, ws)


def _owned_habit(s: Session, ws: int, habit_id: int) -> Habit:
    habit = s.get(Habit, habit_id)
    if habit is None or habit.workspace_id != ws:
        raise NotFound("habit")
    return habit


def toggle_habit(s: Session, ws: int, habit_id: int,
                 day: date | None = None, *, tz: ZoneInfo | None = None) -> bool:
    """Flip today's completion. Returns the new state.

    The derived habits are refused rather than silently ignored.
    """
    habit = _owned_habit(s, ws, habit_id)
    if habit.is_protected:
        raise ValueError("protected")

    day = day or today_local(tz)
    row = s.scalar(select(HabitLog).where(
        HabitLog.workspace_id == ws, HabitLog.habit_id == habit_id, HabitLog.day == day))
    # A habit with a timer is done by the timer, not by the box. Unticking
    # stays free.
    if not (row and row.done) and timer_blocks(s, ws, "habit", habit, day):
        raise ValueError("timer_required")
    if row is None:
        row = HabitLog(workspace_id=ws, habit_id=habit_id, day=day, done=True,
                       logged_at=now_local(tz))
        s.add(row)
        try:
            with s.begin_nested():
                s.flush()
        except IntegrityError:
            # A second tap raced the first; the first one's tick stands.
            row = s.scalar(select(HabitLog).where(
                HabitLog.workspace_id == ws, HabitLog.habit_id == habit_id,
                HabitLog.day == day))
            s.commit()
            return bool(row and row.done)
    else:
        row.done = not row.done
        row.logged_at = now_local(tz) if row.done else None
    s.commit()
    return row.done


def delete_habit(s: Session, ws: int, habit_id: int) -> str:
    """Archive a habit, keeping its logs so past reports stay truthful.

    It stays owed through today — removing it cannot improve a day that is
    already running — unless it was only added today, in which case it was
    never owed at all.
    """
    habit = _owned_habit(s, ws, habit_id)
    if habit.is_protected:
        raise ValueError("protected")
    habit.archived_at = utcnow()
    for countdown in s.scalars(select(Countdown).where(
            Countdown.workspace_id == ws, Countdown.team_id.is_(None),
            Countdown.scope == "habit", Countdown.item_id == habit.id,
            Countdown.archived_at.is_(None))).all():
        countdown.archived_at = utcnow()
    s.commit()
    return habit.name


def _module_of(system_key: str) -> str | None:
    return next((name for name, key in MODULES.items() if key == system_key), None)


def remove_habit(s: Session, ws: int, habit_id: int) -> str:
    """Remove any habit the user has — the derived ones included.

    `delete_habit` refuses the three rituals, because each one is driven by a
    module. Removing one here switches its module off instead, which archives
    the same row and keeps every log it had: the person asked for the habit to
    go, and it goes, with nothing lost if they change their mind.
    """
    habit = _owned_habit(s, ws, habit_id)
    if habit.archived_at is not None:
        return habit.name
    module = _module_of(habit.system_key) if habit.system_key else None
    if module is None:
        if habit.is_protected:
            habit.is_protected = False
        return delete_habit(s, ws, habit_id)
    live = {name for name, on in modules_for(s, ws).items() if on}
    owner = workspace_owner(s, ws)
    set_modules(s, ws, live - {module},
                user=s.get(User, owner) if owner is not None else None)
    return habit.name


def archived_habits(s: Session, ws: int, *, limit: int = 15) -> list[dict]:
    """Removed habits that can be brought back, newest first, one per name.

    A habit whose name is live again — re-added by hand — is left out, so
    restoring can never produce two of the same thing.
    """
    live = _active_habits(s, ws)
    live_names = {h.name.strip().lower() for h in live}
    live_keys = {h.system_key for h in live if h.system_key}
    rows = s.scalars(select(Habit).where(
        Habit.workspace_id == ws, Habit.archived_at.is_not(None))
        .order_by(Habit.archived_at.desc(), Habit.id.desc())).all()
    logged = set(s.scalars(select(HabitLog.habit_id).where(
        HabitLog.workspace_id == ws,
        HabitLog.habit_id.in_([h.id for h in rows if h.system_key] or [0]))).all())
    seen: set[str] = set()
    out: list[dict] = []
    for habit in rows:
        key = habit.system_key or habit.name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        if habit.system_key and habit.system_key in live_keys:
            continue
        if (habit.system_key and habit.id not in logged
                and habit.archived_at - (habit.created_at or habit.archived_at)
                < timedelta(days=1)):
            # A ritual switched off at setup, never used: not something the
            # person removed, just something they did not pick. Settings →
            # Modules is where it is switched on.
            continue
        if not habit.system_key and habit.name.strip().lower() in live_names:
            continue
        out.append({"id": habit.id, "name": habit.name,
                    "category": habit.category, "system_key": habit.system_key})
        if len(out) >= limit:
            break
    return out


def restore_habit(s: Session, ws: int, habit_id: int) -> Habit:
    """Bring a removed habit back, history and all.

    The days it was away are not owed — `_reactivate` files them as a closed
    pause — so bringing a habit back never turns into a string of misses.
    """
    habit = _owned_habit(s, ws, habit_id)
    if habit.archived_at is None:
        return habit
    module = _module_of(habit.system_key) if habit.system_key else None
    if module is not None:
        live = {name for name, on in modules_for(s, ws).items() if on}
        owner = workspace_owner(s, ws)
        set_modules(s, ws, live | {module},
                    user=s.get(User, owner) if owner is not None else None)
        return s.scalar(select(Habit).where(
            Habit.workspace_id == ws, Habit.system_key == habit.system_key,
            Habit.archived_at.is_(None))) or habit
    tz = _habit_tz(s, ws)
    _reactivate(s, ws, habit, today_local(tz), tz)
    top = s.scalar(select(func.max(Habit.position))
                   .where(Habit.workspace_id == ws,
                          Habit.archived_at.is_(None),
                          Habit.id != habit.id)) or 0
    habit.position = top + 1
    s.commit()
    return habit


# ---------------------------------------------------------------------------
# The ready-made ten
# ---------------------------------------------------------------------------
#
# Ten habits most people starting a system like this actually keep: the three
# rituals the app can record on its own, and seven ordinary ones. They are a
# list to pick from, not a regime — every one can be removed and put back, and
# putting one back brings its history with it rather than starting over.

#: (key, category, {lang: name}). A system key names a ritual; the others are
#: matched to a live habit by name, in any of the three languages, so a preset
#: added in Uzbek still reads as "added" after switching to English.
HABIT_PRESETS = [
    (SYSTEM_WAKEUP, "non_negotiable", {}),
    (SYSTEM_PRAYER, "non_negotiable", {}),
    (SYSTEM_JOURNAL, "non_negotiable", {}),
    ("plan", "target", {"uz": "Kunni rejalashtirish", "en": "Plan the day",
                        "ru": "Планировать день"}),
    ("deep", "target", {"uz": "Chuqur ish", "en": "Deep work",
                        "ru": "Глубокая работа"}),
    ("sport", "target", {"uz": "Sport", "en": "Exercise", "ru": "Спорт"}),
    ("read", "target", {"uz": "Kitob o'qish", "en": "Read a book",
                        "ru": "Чтение книги"}),
    ("water", "bonus", {"uz": "2 litr suv ichish", "en": "Drink 2 litres of water",
                        "ru": "Выпить 2 литра воды"}),
    ("language", "bonus", {"uz": "Til o'rganish", "en": "Learn a language",
                           "ru": "Изучение языка"}),
    ("sleep", "bonus", {"uz": "23:00 gacha uxlash", "en": "In bed by 23:00",
                        "ru": "Спать до 23:00"}),
]
PRESET_KEYS = [key for key, _c, _n in HABIT_PRESETS]
#: The seven ordinary presets, which setup offers after the three rituals.
ORDINARY_PRESET_KEYS = [key for key in PRESET_KEYS if key not in SYSTEM_KEYS]


def _preset(key: str) -> tuple[str, str, dict]:
    for row in HABIT_PRESETS:
        if row[0] == key:
            return row
    raise ValueError("unknown_preset")


def preset_name(key: str, lang: str = "uz") -> str:
    """The preset's name as it is created in `lang`."""
    key, _category, names = _preset(key)
    if key in SYSTEM_KEYS:
        return SYSTEM_HABIT_NAMES[key].get(lang) or SYSTEM_HABIT_NAMES[key]["uz"]
    return names.get(lang) or names["uz"]


def _preset_names(key: str) -> set[str]:
    _key, _category, names = _preset(key)
    return {n.strip().casefold() for n in names.values()}


def _preset_habit(s: Session, ws: int, key: str, *, live: bool = True) -> Habit | None:
    """The habit that is this preset — the live one, or the newest archived one."""
    rows = [h for h in _active_habits(s, ws)] if live else list(s.scalars(
        select(Habit).where(Habit.workspace_id == ws,
                            Habit.archived_at.is_not(None))
        .order_by(Habit.archived_at.desc(), Habit.id.desc())).all())
    if key in SYSTEM_KEYS:
        return next((h for h in rows if h.system_key == key), None)
    names = _preset_names(key)
    return next((h for h in rows if not h.system_key
                 and h.name.strip().casefold() in names), None)


def habit_presets(s: Session, ws: int, lang: str = "uz") -> list[dict]:
    """The ten, each with whether it is on this list right now."""
    out = []
    for key, category, _names in HABIT_PRESETS:
        habit = _preset_habit(s, ws, key)
        out.append({"key": key, "name": habit.name if habit else preset_name(key, lang),
                    "category": category, "system": key in SYSTEM_KEYS,
                    "added": habit is not None,
                    "habit_id": habit.id if habit else None})
    return out


def add_preset(s: Session, ws: int, key: str, lang: str = "uz", *,
               tz: ZoneInfo | None = None) -> Habit:
    """Put one of the ten on the list. Bringing one back keeps its history."""
    key, category, _names = _preset(key)
    habit = _preset_habit(s, ws, key)
    if habit is not None:
        return habit
    old = _preset_habit(s, ws, key, live=False)
    if old is not None:
        return restore_habit(s, ws, old.id)
    if key in SYSTEM_KEYS:
        module = _module_of(key)
        live = {name for name, on in modules_for(s, ws).items() if on}
        owner = workspace_owner(s, ws)
        set_modules(s, ws, live | {module},
                    user=s.get(User, owner) if owner is not None else None)
        return _preset_habit(s, ws, key)
    return add_habit(s, ws, preset_name(key, lang), category, tz=tz)


def remove_preset(s: Session, ws: int, key: str) -> str | None:
    """Take one of the ten off the list — archived, never erased."""
    _preset(key)
    habit = _preset_habit(s, ws, key)
    if habit is None:
        return None
    return remove_habit(s, ws, habit.id)


def wake_habit(s: Session, ws: int) -> Habit | None:
    return s.scalar(select(Habit).where(
        Habit.workspace_id == ws, Habit.system_key == SYSTEM_WAKEUP,
        Habit.archived_at.is_(None)))


def set_wake_time(s: Session, ws: int, value: dtime) -> Habit:
    habit = wake_habit(s, ws)
    if habit is None:
        raise NotFound("habit")
    habit.target_time = value
    s.commit()
    return habit


def wake_state(s: Session, ws: int, *, tz: ZoneInfo | None = None) -> dict | None:
    """Everything the wake-up control needs to draw itself.

    Returns None when the habit is gone or paused, so the caller can leave the
    button out rather than showing one that cannot do anything.
    """
    habit = wake_habit(s, ws)
    if habit is None:
        return None
    tz = tz or _habit_tz(s, ws)
    now = now_local(tz)
    day = now.date()
    cal = calendar_for(s, [habit], tz)
    if cal.paused_on(habit, day):
        return None

    target = habit.target_time or DEFAULT_WAKE_TIME
    deadline = datetime.combine(day, target) + WAKE_GRACE

    row = s.scalar(select(HabitLog).where(
        HabitLog.workspace_id == ws, HabitLog.habit_id == habit.id,
        HabitLog.day == day))
    return {
        "habit_id": habit.id,
        "target": target.strftime("%H:%M"),
        "deadline": deadline.strftime("%H:%M"),
        "now": now.strftime("%H:%M"),
        "logged": row is not None and row.logged_at is not None,
        "done": bool(row and row.done),
        "late": now > deadline,
        "at": row.logged_at.strftime("%H:%M") if (row and row.logged_at) else None,
    }


def mark_wakeup(s: Session, ws: int, now: datetime | None = None, *,
                tz: ZoneInfo | None = None, at: dtime | None = None) -> dict:
    """Record that the user got up, if they said so in time.

    "Turdim" counts until one hour after the target time. Saying it later still
    records the moment, but the habit stays undone for the day.

    `at` is the time somebody actually got up, entered afterwards: the button
    measures when it was pressed, and getting up at 04:50 and remembering to
    say so at 06:30 is still getting up at 04:50. Only today, never a time
    still in the future, and the screen asks for honesty rather than proof.
    """
    habit = wake_habit(s, ws)
    if habit is None:
        raise NotFound("habit")

    now = now or now_local(tz)
    day = now.date()
    moment = now
    if at is not None:
        moment = datetime.combine(day, at)
        if moment > now:
            raise ValueError("future_time")
    target = habit.target_time or DEFAULT_WAKE_TIME
    deadline = datetime.combine(day, target) + WAKE_GRACE
    in_time = moment <= deadline

    row = s.scalar(select(HabitLog).where(
        HabitLog.workspace_id == ws, HabitLog.habit_id == habit.id,
        HabitLog.day == day))
    if row is None:
        s.add(HabitLog(workspace_id=ws, habit_id=habit.id, day=day, done=in_time,
                       logged_at=moment))
    else:
        row.done = row.done or in_time
        # Keep the first time reported, unless a real time is being entered.
        if at is not None or row.logged_at is None:
            row.logged_at = moment
    s.commit()

    return {"done": in_time, "late": not in_time, "at": moment.strftime("%H:%M"),
            "target": target.strftime("%H:%M"),
            "deadline": deadline.strftime("%H:%M"),
            "now": now.strftime("%H:%M")}


def workspace_owner(s: Session, ws: int) -> int | None:
    """Whose workspace this is. The bridge between workspace-scoped scoring
    and team membership, which is keyed on the person rather than the box."""
    return _memoized(s, ("owner", ws), lambda: s.scalar(
        select(Workspace.user_id).where(Workspace.id == ws)))


# --- Shared work inside the personal day ------------------------------------
#
# Shared work counts in the personal day, once, with the same weights a
# private item of the same kind carries: a shared high-priority task weighs
# what a private high-priority task weighs, a shared bonus habit what a private
# bonus habit weighs. It does not get a half of the day of its own — one shared
# checkbox is not worth as much as everything else put together — and the
# rituals a team mirrors from each member's own habits are not counted again.

def due_team_habits(s: Session, ws: int, day: date) -> list[tuple]:
    """(habit, done) for every shared habit this workspace's owner owes that day."""
    owner = workspace_owner(s, ws)
    if owner is None:
        return []
    tz = _habit_tz(s, ws)
    rows: list[tuple] = []
    for team in teams_for_on(s, owner, day, tz=tz):
        habits = [h for h in _memoized(s, ("team_habits", team.id),
                                       lambda team_id=team.id: list(s.scalars(
                                           select(TeamHabit).where(
                                               TeamHabit.team_id == team_id)).all()))
                  if not (h.system_key or "")]
        if not habits:
            continue
        cal = calendar_for(s, habits, tz)
        owed = [h for h in habits if cal.due(h, day)]
        if not owed:
            continue
        done_ids = set(s.scalars(select(TeamHabitLog.habit_id).where(
            TeamHabitLog.user_id == owner, TeamHabitLog.day == day,
            TeamHabitLog.done.is_(True),
            TeamHabitLog.habit_id.in_([h.id for h in owed]))).all())
        for habit in owed:
            rows.append((habit, habit.id in done_ids))
    return rows


def due_team_tasks(s: Session, ws: int, day: date) -> list[tuple]:
    """(priority, done) for every shared task this owner owes on `day`."""
    owner = workspace_owner(s, ws)
    if owner is None:
        return []
    tz = _habit_tz(s, ws)
    rows: list[tuple] = []
    for team in teams_for_on(s, owner, day, tz=tz):
        tasks = [t for t in s.scalars(select(TeamTask).where(
            TeamTask.team_id == team.id, TeamTask.deadline == day)).all()
            if _team_item_live_on(t, day, tz)]
        if not tasks:
            continue
        done_by = _team_done_map(s, [t.id for t in tasks])
        for task in tasks:
            if not team_task_owed_by(task, owner):
                continue
            rows.append((task.priority,
                         team_task_done_for(task, owner, done_by.get(task.id, set()))))
    return rows


def _scored_personal(habits: list[Habit]) -> list[Habit]:
    """The habits the habit component is built from: all but prayer's shortcut."""
    return [h for h in habits if h.system_key != SYSTEM_PRAYER]


def habit_progress(s: Session, ws: int, day: date, *,
                   include_team: bool = True) -> tuple[int, int]:
    """(completed, total) habits that were actually owed on that day.

    Prayer is not in this count: it has its own section, its own "4/5" and its
    own place in the score, and counting its habit here as well was the same
    five prayers moving the number twice.
    """
    tz = _habit_tz(s, ws)
    candidates = _scored_personal(_habits_owed_candidates(s, ws, day))
    cal = calendar_for(s, candidates, tz)
    habits = [h for h in candidates if cal.due(h, day)]
    shared = due_team_habits(s, ws, day) if include_team else []
    if not habits and not shared:
        return 0, 0

    due_ids = {h.id for h in habits}
    done_ids = set(s.scalars(select(HabitLog.habit_id).where(
        HabitLog.workspace_id == ws, HabitLog.day == day,
        HabitLog.done.is_(True))).all()) if habits else set()
    return (len(due_ids & done_ids) + sum(1 for _, done in shared if done),
            len(due_ids) + len(shared))


def habit_tier_progress(s: Session, ws: int, day: date, *,
                        include_team: bool = True) -> dict[str, dict]:
    """Per-tier completion for one day: done, due, percent and applied weight.

    Only tiers that actually have a habit due that day get a weight, and the
    weights are renormalised over them — so "I did all of it" is always 100%,
    whether "all of it" is two habits or eleven.
    """
    tz = _habit_tz(s, ws)
    candidates = _scored_personal(_habits_owed_candidates(s, ws, day))
    cal = calendar_for(s, candidates, tz)
    habits = [h for h in candidates if cal.due(h, day)]
    done_ids = set(s.scalars(select(HabitLog.habit_id).where(
        HabitLog.workspace_id == ws, HabitLog.day == day,
        HabitLog.done.is_(True))).all()) if habits else set()
    shared = due_team_habits(s, ws, day) if include_team else []

    tiers: dict[str, dict] = {}
    for name in HABIT_CATEGORIES:
        due = [h for h in habits if h.category == name]
        done = sum(1 for h in due if h.id in done_ids)
        shared_due = [(h, ok) for h, ok in shared if h.category == name]
        due = due + [h for h, _ in shared_due]
        done += sum(1 for _, ok in shared_due if ok)
        tiers[name] = {
            "done": done,
            "due": len(due),
            "percent": round(done / len(due) * 100) if due else 0,
            "weight": HABIT_TIER_WEIGHTS[name],
            "applied": 0,
        }

    live = sum(HABIT_TIER_WEIGHTS[n] for n in HABIT_CATEGORIES if tiers[n]["due"])
    if live:
        for name in HABIT_CATEGORIES:
            if tiers[name]["due"]:
                tiers[name]["applied"] = round(
                    HABIT_TIER_WEIGHTS[name] / live * 100)
    return tiers


def habit_percent(s: Session, ws: int, day: date, *,
                  include_team: bool = True) -> int:
    """The day's habit score, 0–100, with the three tiers weighted."""
    tiers = habit_tier_progress(s, ws, day, include_team=include_team)
    live = sum(t["weight"] for t in tiers.values() if t["due"])
    if not live:
        return 0
    return round(sum(t["weight"] * t["done"] / t["due"]
                     for t in tiers.values() if t["due"]) / live * 100)


#: How far back a streak is counted. Longer than any grid the UI offers, so
#: the streak is a property of the habit rather than of the view you happen to
#: be looking at, and bounded so the walk always terminates.
STREAK_HORIZON = 400


def habit_history(s: Session, ws: int, habit_id: int, *, days: int = 30,
                  tz: ZoneInfo | None = None) -> dict:
    """One habit's own record: its streak, its grid and its completion rate.

    Only days the habit was owed appear as due in the grid — under the
    schedule of *that* day — so the rate is "how often I did it when I meant
    to" and changing the schedule today does not rewrite the grid.
    """
    habit = _owned_habit(s, ws, habit_id)
    tz = tz or _habit_tz(s, ws)
    today = today_local(tz)
    start = today - timedelta(days=days - 1)
    cal = calendar_for(s, [habit], tz)

    streak_start = today - timedelta(days=STREAK_HORIZON)
    done_days = set(s.scalars(select(HabitLog.day).where(
        HabitLog.workspace_id == ws, HabitLog.habit_id == habit_id,
        HabitLog.done.is_(True), HabitLog.day >= streak_start)).all())

    grid, due_count, done_count = [], 0, 0
    for offset in range(days):
        day = start + timedelta(days=offset)
        due = cal.due(habit, day)
        done = day in done_days
        if due:
            due_count += 1
            done_count += int(done)
        grid.append({"day": day.isoformat(), "due": due, "done": done})

    last7 = [g for g in grid[-7:] if g["due"]]

    # The streak counts backwards over owed days only, and today does not
    # break it while the day is still going.
    streak, cursor, guard = 0, today, 0
    if cal.due(habit, today) and today not in done_days:
        cursor = today - timedelta(days=1)
    while guard < STREAK_HORIZON:
        guard += 1
        if not cal.exists_on(habit, cursor) and cursor < (cal.start_of(habit) or cursor):
            break
        if cal.due(habit, cursor):
            if cursor not in done_days:
                break
            streak += 1
        cursor -= timedelta(days=1)

    pending = cal.pause_pending(habit, today)
    return {
        "id": habit.id, "name": habit.name, "category": habit.category,
        "schedule": clean_schedule(habit.schedule),
        "schedule_today": cal.schedule_on(habit, today),
        "days": schedule_days(habit.schedule),
        "paused": cal.paused_on(habit, today),
        "pause_from": pending.isoformat() if pending else None,
        "protected": habit.is_protected, "system_key": habit.system_key,
        "target_time": habit.target_time.strftime("%H:%M") if habit.target_time else None,
        "remind_at": habit.remind_at.strftime("%H:%M") if habit.remind_at else None,
        "active_from": habit.active_from.isoformat() if habit.active_from else None,
        **_timer_fields(habit.timer_minutes, habit.name,
                        protected=habit.is_protected),
        "timer": open_timer_runs(s, ws, "habit", day=today).get(habit.id),
        "streak": streak,
        "grid": grid,
        "last7_done": sum(1 for g in last7 if g["done"]), "last7_due": len(last7),
        "last30_done": done_count, "last30_due": due_count,
        "percent": round(done_count / due_count * 100) if due_count else 0,
        "source": "personal",
    }


# ---------------------------------------------------------------------------
# Prayer
# ---------------------------------------------------------------------------

PRAYERS = ["bomdod", "peshin", "asr", "shom", "xufton"]

#: Canonical statuses. The UI translates them; the database never stores labels.
PRAYER_POINTS = {"jamaat": 1.0, "on_time": 1.0, "qaza": 0.5, "missed": 0.0}
STATUSES_MALE = ["jamaat", "on_time", "qaza", "missed"]
#: Women have no jamaat, but they do record on-time, qaza and missed.
STATUSES_FEMALE = ["on_time", "qaza", "missed"]

#: A full day is five prayers — the denominator the UI shows.
PRAYER_MAX_SCORE = 5.0
#: How many of the five have to be prayed for the day to be a complete "5x".
PRAYER_REQUIRED = 5

#: The statuses that mean the prayer was actually prayed. `qaza` is late, not
#: skipped, so it counts towards the five; `missed` does not.
PRAYER_PERFORMED = {"jamaat", "on_time", "qaza"}

#: An excused day is fulfilled, so it scores as a full day rather than as half
#: of one. The PrayerLog rows stay untouched — this is the derived day score.
EXCUSED_SCORE = PRAYER_MAX_SCORE


def prayer_statuses_for(gender: str | None) -> list[str]:
    return STATUSES_FEMALE if gender == "female" else STATUSES_MALE


def prayer_score(statuses: dict[str, str], gender: str | None,
                 excused: bool = False) -> float:
    """Daily *quality* score from the five prayers, 0 to 5."""
    if gender == "female" and excused:
        return EXCUSED_SCORE
    allowed = set(prayer_statuses_for(gender))
    total = 0.0
    for prayer in PRAYERS:
        status = statuses.get(prayer)
        if status in allowed:
            total += PRAYER_POINTS.get(status, 0.0)
    return round(total, 2)


def prayers_performed(statuses: dict[str, str], gender: str | None) -> int:
    """How many of the five were prayed — the numerator of "5 / 5"."""
    allowed = set(prayer_statuses_for(gender)) & PRAYER_PERFORMED
    return sum(1 for p in PRAYERS if statuses.get(p) in allowed)


def prayer_is_complete(statuses: dict[str, str], gender: str | None,
                       excused: bool = False) -> bool:
    """Whether the `5x namoz` habit is done for the day: all five, or excused."""
    if gender == "female" and excused:
        return True
    return prayers_performed(statuses, gender) >= PRAYER_REQUIRED


def _day_statuses(s: Session, ws: int, day: date) -> dict[str, str]:
    rows = s.scalars(select(PrayerLog).where(
        PrayerLog.workspace_id == ws, PrayerLog.day == day)).all()
    return {r.prayer: r.status for r in rows}


def recalc_prayer_day(s: Session, ws: int, day: date, gender: str | None) -> float:
    """Recompute the day's score and sync the protected `5x namoz` habit."""
    statuses = _day_statuses(s, ws, day)

    state = s.scalar(select(PrayerDay).where(
        PrayerDay.workspace_id == ws, PrayerDay.day == day))
    excused = bool(state and state.excused)

    score = prayer_score(statuses, gender, excused)
    if state is None:
        state = PrayerDay(workspace_id=ws, day=day, excused=excused, score=score)
        s.add(state)
    else:
        state.score = score

    habit = s.scalar(select(Habit).where(
        Habit.workspace_id == ws, Habit.system_key == SYSTEM_PRAYER,
        Habit.archived_at.is_(None)))
    if habit is not None:
        done = prayer_is_complete(statuses, gender, excused)
        row = s.scalar(select(HabitLog).where(
            HabitLog.workspace_id == ws, HabitLog.habit_id == habit.id,
            HabitLog.day == day))
        if row is None:
            s.add(HabitLog(workspace_id=ws, habit_id=habit.id, day=day, done=done))
        else:
            row.done = done

    s.commit()
    return score


def set_prayer(s: Session, ws: int, prayer: str, status: str,
               gender: str | None, day: date | None = None, *,
               tz: ZoneInfo | None = None) -> float:
    if prayer not in PRAYERS:
        raise ValueError("unknown prayer")
    if status not in prayer_statuses_for(gender):
        raise ValueError("status not allowed for this gender")

    day = day or today_local(tz)
    row = s.scalar(select(PrayerLog).where(
        PrayerLog.workspace_id == ws, PrayerLog.day == day, PrayerLog.prayer == prayer))
    if row is None:
        s.add(PrayerLog(workspace_id=ws, day=day, prayer=prayer, status=status))
    else:
        row.status = status
    s.commit()
    return recalc_prayer_day(s, ws, day, gender)


def clear_prayer(s: Session, ws: int, prayer: str, gender: str | None,
                 day: date | None = None, *, tz: ZoneInfo | None = None) -> float:
    """Undo a prayer entry — a mis-tap has to be reversible."""
    if prayer not in PRAYERS:
        raise ValueError("unknown prayer")
    day = day or today_local(tz)
    row = s.scalar(select(PrayerLog).where(
        PrayerLog.workspace_id == ws, PrayerLog.day == day, PrayerLog.prayer == prayer))
    if row is not None:
        s.delete(row)
        s.commit()
    return recalc_prayer_day(s, ws, day, gender)


def set_excused(s: Session, ws: int, excused: bool, gender: str | None,
                day: date | None = None, *, tz: ZoneInfo | None = None) -> float:
    """Female-only day-level exemption."""
    if gender != "female":
        raise ValueError("excused is only available for female users")
    day = day or today_local(tz)
    state = s.scalar(select(PrayerDay).where(
        PrayerDay.workspace_id == ws, PrayerDay.day == day))
    if state is None:
        s.add(PrayerDay(workspace_id=ws, day=day, excused=excused, score=0))
    else:
        state.excused = excused
    s.commit()
    return recalc_prayer_day(s, ws, day, gender)


def prayer_state(s: Session, ws: int, day: date, gender: str | None) -> dict:
    statuses = _day_statuses(s, ws, day)
    state = s.scalar(select(PrayerDay).where(
        PrayerDay.workspace_id == ws, PrayerDay.day == day))
    excused = bool(state and state.excused)
    return {
        "day": day.isoformat(),
        "prayers": {p: statuses.get(p) for p in PRAYERS},
        "excused": excused,
        # Two numbers, not one: how many were prayed, and how well.
        "performed": prayers_performed(statuses, gender),
        "required": PRAYER_REQUIRED,
        "complete": prayer_is_complete(statuses, gender, excused),
        "score": float(state.score) if state else 0.0,
        "max": PRAYER_MAX_SCORE,
        "statuses": prayer_statuses_for(gender),
    }


def prayer_owed(s: Session, ws: int, day: date) -> bool:
    """Whether the prayer module was on for that day."""
    tz = _habit_tz(s, ws)
    candidates = [h for h in _habits_owed_candidates(s, ws, day)
                  if h.system_key == SYSTEM_PRAYER]
    if not candidates:
        return False
    cal = calendar_for(s, candidates, tz)
    return any(cal.due(h, day) for h in candidates)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

#: active | done, plus "archived" as a view rather than a stored status.
PROJECT_STATUSES = ["active", "done"]


def _project_dict(s: Session, ws: int, p: Project) -> dict:
    total = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.project_id == p.id,
        Task.archived_at.is_(None))) or 0
    done = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.project_id == p.id,
        Task.archived_at.is_(None), Task.status == "done")) or 0
    return {
        "id": p.id, "name": p.name, "description": p.description,
        "deadline": p.deadline.isoformat() if p.deadline else None,
        "status": p.status if p.status in PROJECT_STATUSES else "active",
        "archived": p.archived_at is not None,
        "tasks_total": total, "tasks_done": done,
        "tasks_open": max(total - done, 0),
        "progress": round(done / total * 100) if total else 0,
    }


def list_projects(s: Session, ws: int, *, status: str = "",
                  include_archived: bool = False) -> list[dict]:
    """Projects, open ones first. Shared projects never appear here."""
    stmt = select(Project).where(Project.workspace_id == ws,
                                 Project.team_id.is_(None))
    if not include_archived:
        stmt = stmt.where(Project.archived_at.is_(None))
    if status in PROJECT_STATUSES:
        stmt = stmt.where(Project.status == status)

    projects = s.scalars(stmt.order_by(Project.created_at.desc())).all()
    rows = [_project_dict(s, ws, p) for p in projects]
    rows.sort(key=lambda p: (p["status"] == "done", p["archived"]))
    return rows


def add_project(s: Session, ws: int, name: str, *, description: str = "",
                deadline: date | None = None) -> Project:
    name = name.strip()[:200]
    if not name:
        raise ValueError("empty project name")
    plans.require_in_workspace(s, ws, "projects", s.scalar(
        select(func.count(Project.id)).where(Project.workspace_id == ws,
                                             Project.team_id.is_(None),
                                             Project.archived_at.is_(None))) or 0)
    project = Project(workspace_id=ws, name=name,
                      description=description.strip()[:2000], deadline=deadline,
                      created_by=workspace_owner(s, ws))
    s.add(project)
    s.commit()
    return project


def _owned_project(s: Session, ws: int, project_id: int, *,
                   allow_archived: bool = False) -> Project:
    project = s.get(Project, project_id)
    if project is None or project.workspace_id != ws or project.team_id is not None:
        raise NotFound("project")
    if project.archived_at and not allow_archived:
        raise NotFound("project")
    return project


def update_project(s: Session, ws: int, project_id: int, **fields) -> Project:
    """Rename a project, or adjust its description and deadline."""
    project = _owned_project(s, ws, project_id, allow_archived=True)
    if fields.get("name"):
        name = str(fields["name"]).strip()[:200]
        if not name:
            raise ValueError("empty project name")
        project.name = name
    if "description" in fields:
        project.description = str(fields["description"] or "").strip()[:2000]
    if "deadline" in fields:
        project.deadline = fields["deadline"]
    if fields.get("status") in PROJECT_STATUSES:
        project.status = fields["status"]
    if "archived" in fields:
        project.archived_at = utcnow() if fields["archived"] else None
    s.commit()
    return project


def project_tasks(s: Session, ws: int, project_id: int, *,
                  include_done: bool = True,
                  tz: ZoneInfo | None = None) -> list[dict]:
    """Everything inside one project, open work first."""
    _owned_project(s, ws, project_id)
    today = today_local(tz)
    stmt = select(Task).where(
        Task.workspace_id == ws, Task.project_id == project_id,
        Task.archived_at.is_(None))
    if not include_done:
        stmt = stmt.where(Task.status == "waiting")
    rows = s.scalars(stmt).all()
    rows.sort(key=lambda x: (x.status == "done",
                             _PRIORITY_RANK.get(x.priority, 1),
                             x.deadline or date.max, x.id))
    runs = open_timer_runs(s, ws, "task")
    return [_task_dict(s, ws, task, today, runs) for task in rows]


def delete_project(s: Session, ws: int, project_id: int) -> str:
    """Archive a project and detach its tasks. Tasks are never deleted with it."""
    project = s.get(Project, project_id)
    if project is None or project.workspace_id != ws or project.team_id is not None:
        raise NotFound("project")
    for task in s.scalars(select(Task).where(
            Task.workspace_id == ws, Task.project_id == project_id)).all():
        task.project_id = None
    project.archived_at = utcnow()
    s.commit()
    return project.name


# ---------------------------------------------------------------------------
# Goals above the week
# ---------------------------------------------------------------------------

#: Tactical goals are the week's focus; these are the two levels above it.
GOAL_LEVELS = ["milestone", "ultimate"]
GOAL_STATUSES = ["dream", "active", "done"]
GOAL_CATEGORIES = ["capital", "health", "islam", "family", "career",
                   "learning", "charity", "other"]
GOAL_UNITS = ["USD", "UZS", "EUR", "RUB"]
_GOAL_AMOUNT_MAX = 10 ** 13


def _goal_horizon(value) -> str:
    """"2030", "lifetime" or "". Anything else is a mistake, not a horizon."""
    value = str(value or "").strip().lower()
    if value in ("", "lifetime"):
        return value
    if value.isdigit() and 2000 <= int(value) <= 2200:
        return value
    raise ValueError("bad_horizon")


def _goal_fields(s: Session, ws: int, fields: dict, goal: LifeGoal | None) -> dict:
    """Validated column values for a new goal or a change to one."""
    out: dict = {}
    if "title" in fields:
        title = str(fields["title"] or "").strip()[:200]
        if not title:
            raise ValueError("empty_goal_title")
        out["title"] = title
    if "level" in fields:
        if fields["level"] not in GOAL_LEVELS:
            raise ValueError("bad_goal_level")
        out["level"] = fields["level"]
    if "category" in fields:
        out["category"] = fields["category"] if fields["category"] in GOAL_CATEGORIES else "other"
    if "status" in fields:
        if fields["status"] not in GOAL_STATUSES:
            raise ValueError("bad_goal_status")
        out["status"] = fields["status"]
    if "horizon" in fields:
        out["horizon"] = _goal_horizon(fields["horizon"])
    if "amount" in fields:
        amount = fields["amount"]
        if amount in (None, ""):
            out["amount"] = None
        else:
            amount = int(amount)
            if not 0 <= amount <= _GOAL_AMOUNT_MAX:
                raise ValueError("bad_goal_amount")
            out["amount"] = amount or None
    if "unit" in fields:
        unit = str(fields["unit"] or "USD").upper()
        out["unit"] = unit if unit in GOAL_UNITS else "USD"
    if "progress" in fields:
        out["progress"] = max(0, min(100, int(fields["progress"] or 0)))
    if "cover" in fields:
        out["cover"] = str(fields["cover"] or "").strip()[:16]
    if "note" in fields:
        out["note"] = str(fields["note"] or "").strip()[:2000]
    level = out.get("level") or (goal.level if goal else "milestone")
    if "parent_id" in fields or "level" in fields:
        parent_id = fields.get("parent_id", goal.parent_id if goal else None)
        if level != "milestone" or not parent_id:
            out["parent_id"] = None
        else:
            parent = s.get(LifeGoal, int(parent_id))
            if (parent is None or parent.workspace_id != ws or parent.archived_at
                    or parent.level != "ultimate"
                    or (goal is not None and parent.id == goal.id)):
                raise ValueError("bad_goal_parent")
            out["parent_id"] = parent.id
    return out


def _goal_dict(g: LifeGoal, children: list[LifeGoal]) -> dict:
    """One goal as the app draws it. An ultimate goal with milestones shows
    how many of them are done instead of a number typed by hand."""
    done_children = sum(1 for c in children if c.status == "done")
    if g.status == "done":
        progress = 100
    elif g.level == "ultimate" and children:
        progress = round(sum(100 if c.status == "done" else (c.progress or 0)
                             for c in children) / len(children))
    else:
        progress = g.progress or 0
    return {
        "id": g.id, "level": g.level, "title": g.title, "category": g.category,
        "horizon": g.horizon or "", "status": g.status,
        "amount": int(g.amount) if g.amount is not None else None,
        "unit": g.unit or "USD",
        "progress": progress, "own_progress": g.progress or 0,
        "auto_progress": g.level == "ultimate" and bool(children) and g.status != "done",
        "parent_id": g.parent_id, "cover": g.cover or "", "note": g.note or "",
        "position": g.position or 0,
        "milestones": len(children), "milestones_done": done_children,
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "done_at": g.done_at.isoformat() if g.done_at else None,
    }


def list_goals(s: Session, ws: int) -> list[dict]:
    """Every goal that is not archived: in progress, then someday, then
    achieved — each by position."""
    rows = s.scalars(select(LifeGoal).where(LifeGoal.workspace_id == ws,
                                            LifeGoal.archived_at.is_(None))).all()
    kids: dict[int, list[LifeGoal]] = {}
    for g in rows:
        if g.level == "milestone" and g.parent_id:
            kids.setdefault(g.parent_id, []).append(g)
    order = {"active": 0, "dream": 1, "done": 2}
    rows = sorted(rows, key=lambda g: (order.get(g.status, 1), g.position or 0, g.id))
    return [_goal_dict(g, kids.get(g.id, []) if g.level == "ultimate" else []) for g in rows]


def _open_goal_count(s: Session, ws: int) -> int:
    """What the plan limit counts: goals still being worked on. A finished
    goal stays on the board and does not take a place."""
    return int(s.scalar(select(func.count(LifeGoal.id)).where(
        LifeGoal.workspace_id == ws, LifeGoal.archived_at.is_(None),
        LifeGoal.status != "done")) or 0)


def add_goal(s: Session, ws: int, **fields) -> LifeGoal:
    fields.setdefault("level", "milestone")
    values = _goal_fields(s, ws, fields, None)
    if "title" not in values:
        raise ValueError("empty_goal_title")
    if values.get("status", "active") != "done":
        plans.require_in_workspace(s, ws, "life_goals", _open_goal_count(s, ws))
    last = s.scalar(select(func.max(LifeGoal.position)).where(
        LifeGoal.workspace_id == ws, LifeGoal.level == values["level"])) or 0
    goal = LifeGoal(workspace_id=ws, position=last + 1, **values)
    if goal.status == "done":
        goal.done_at = utcnow()
    s.add(goal)
    s.commit()
    return goal


def _owned_goal(s: Session, ws: int, goal_id: int, *, allow_archived: bool = False) -> LifeGoal:
    goal = s.get(LifeGoal, goal_id)
    if goal is None or goal.workspace_id != ws or (goal.archived_at and not allow_archived):
        raise NotFound("goal")
    return goal


def update_goal(s: Session, ws: int, goal_id: int, **fields) -> LifeGoal:
    goal = _owned_goal(s, ws, goal_id, allow_archived=True)
    archived = fields.pop("archived", None)
    if archived is False and goal.archived_at and goal.status != "done":
        # Bringing a goal back takes a place again, like adding it.
        plans.require_in_workspace(s, ws, "life_goals", _open_goal_count(s, ws))
    values = _goal_fields(s, ws, fields, goal)
    if (values.get("status") in ("dream", "active") and goal.status == "done"
            and not goal.archived_at):
        plans.require_in_workspace(s, ws, "life_goals", _open_goal_count(s, ws))
    if values.get("level") == "milestone" and goal.level == "ultimate":
        # An ultimate goal turned into a milestone lets its milestones go.
        for child in s.scalars(select(LifeGoal).where(LifeGoal.parent_id == goal.id)).all():
            child.parent_id = None
    for key, value in values.items():
        setattr(goal, key, value)
    if "status" in values:
        goal.done_at = (goal.done_at or utcnow()) if goal.status == "done" else None
    if archived is not None:
        goal.archived_at = utcnow() if archived else None
    s.commit()
    return goal


def delete_goal(s: Session, ws: int, goal_id: int) -> str:
    """Archive a goal. Its milestones stay, no longer under it."""
    goal = _owned_goal(s, ws, goal_id)
    for child in s.scalars(select(LifeGoal).where(LifeGoal.parent_id == goal.id)).all():
        child.parent_id = None
    goal.archived_at = utcnow()
    s.commit()
    return goal.title


def _export_goals(s: Session, ws: int) -> list[dict]:
    return [{"id": g.id, "level": g.level, "title": g.title, "category": g.category,
             "horizon": g.horizon, "status": g.status,
             "amount": int(g.amount) if g.amount is not None else None,
             "unit": g.unit, "progress": g.progress, "parent_id": g.parent_id,
             "cover": g.cover, "note": g.note,
             "done_at": g.done_at.isoformat() if g.done_at else None,
             "archived": g.archived_at is not None}
            for g in s.scalars(select(LifeGoal).where(LifeGoal.workspace_id == ws)
                               .order_by(LifeGoal.id)).all()]


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

PRIORITIES = ["high", "medium", "low"]

#: Sort order wherever tasks are listed: the order a person would use when
#: asked which to start with.
_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}

#: Fixed recurrences, plus "days:0,2,4" for a hand-picked set.
RECURRENCES = ["daily", "weekdays", "weekly", "monthly"]

#: Reminder offsets in minutes before the due moment, as offered in the UI.
#: 0 means exactly on time.
REMINDER_OFFSETS = [0, 10, 30, 60, 1440]
#: What a task is given when nobody chose. Half an hour is the offset that is
#: actually useful.
DEFAULT_REMIND_BEFORE = 30
#: A reminder cannot be asked for further ahead than this.
MAX_REMIND_BEFORE = 60 * 24 * 7


#: "after:7" — the next copy comes 7 days after the previous one is *done*,
#: not on a calendar date: "change the filter a week after I last did" (audit #14).
RECURRENCE_AFTER = "after:"


def recurrence_after_days(value: str | None) -> int | None:
    value = clean_recurrence(value)
    return int(value[len(RECURRENCE_AFTER):]) if value.startswith(RECURRENCE_AFTER) else None


def clean_recurrence(value: str | None) -> str:
    """Normalise a recurrence, or return "" for a one-off task."""
    value = (value or "").strip().lower()
    if value in RECURRENCES:
        return value
    if value.startswith(RECURRENCE_AFTER):
        days = value[len(RECURRENCE_AFTER):]
        return value if days.isdigit() and 1 <= int(days) <= 365 else ""
    if value.startswith(SCHEDULE_PREFIX_DAYS):
        days = sorted({int(x) for x in value[len(SCHEDULE_PREFIX_DAYS):].split(",")
                       if x.strip().isdigit() and 0 <= int(x) <= 6})
        if not days:
            return ""
        if len(days) == 7:
            return "daily"
        return SCHEDULE_PREFIX_DAYS + ",".join(str(d) for d in days)
    return ""


def next_occurrence(recurrence: str | None, after: date, *,
                    anchor_day: int | None = None) -> date | None:
    """The next date a recurring task is due, strictly after `after`."""
    rule = clean_recurrence(recurrence)
    if not rule:
        return None
    if rule.startswith(RECURRENCE_AFTER):
        return after + timedelta(days=int(rule[len(RECURRENCE_AFTER):]))
    if rule == "daily":
        return after + timedelta(days=1)
    if rule == "weekly":
        return after + timedelta(days=7)
    if rule == "monthly":
        year, month = after.year + (after.month == 12), (after.month % 12) + 1
        last = (date(year + (month == 12), (month % 12) + 1, 1)
                - timedelta(days=1)).day
        return date(year, month, min(anchor_day or after.day, last))

    wanted = [0, 1, 2, 3, 4] if rule == "weekdays" else \
        [int(x) for x in rule[len(SCHEDULE_PREFIX_DAYS):].split(",")]
    for step in range(1, 8):
        candidate = after + timedelta(days=step)
        if candidate.weekday() in wanted:
            return candidate
    return None


def clean_remind_before(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    if minutes < 0 or minutes > MAX_REMIND_BEFORE:
        return None
    return minutes


def add_task(s: Session, ws: int, title: str, *, deadline: date | None = None,
             project_id: int | None = None, priority: str = "medium",
             description: str = "", due_time: dtime | None = None,
             remind_before: int | None = None,
             recurrence: str | None = None,
             timer_minutes: int | None = None) -> Task:
    title = title.strip()[:300]
    if not title:
        raise ValueError("empty task title")
    if priority not in PRIORITIES:
        priority = "medium"
    if project_id is not None:
        # A task may only join a project inside the same workspace.
        project = s.get(Project, project_id)
        if project is None or project.workspace_id != ws or project.team_id is not None:
            raise NotFound("project")

    if plans.ENABLED:
        open_tasks = select(func.count(Task.id)).where(
            Task.workspace_id == ws, Task.status != "done", Task.archived_at.is_(None))
        plans.require_in_workspace(s, ws, "active_tasks", s.scalar(open_tasks) or 0)
        if clean_recurrence(recurrence):
            plans.require_in_workspace(s, ws, "recurring_tasks", s.scalar(
                open_tasks.where(Task.recurrence.is_not(None), Task.recurrence != "")) or 0)
    task = Task(workspace_id=ws, title=title, deadline=deadline,
                project_id=project_id, priority=priority,
                description=description.strip()[:4000],
                due_time=due_time,
                remind_before=clean_remind_before(remind_before),
                recurrence=clean_recurrence(recurrence),
                timer_minutes=clean_timer_minutes(timer_minutes))
    s.add(task)
    if task.recurrence:
        _ROLLED.pop(ws, None)  # a new series may already owe today's copy
    s.commit()
    return task


def _owned_task(s: Session, ws: int, task_id: int) -> Task:
    task = s.get(Task, task_id)
    if task is None or task.workspace_id != ws:
        raise NotFound("task")
    return task


def _spawn_next_occurrence(s: Session, ws: int, task: Task,
                           tz: ZoneInfo | None = None) -> Task | None:
    """Create the next instance of a recurring task, if there isn't one yet.

    The series id and the unique index on (series, date) are what make this
    safe to race: two completions arriving together both try to insert the
    same next occurrence, the database accepts one, and the other is a no-op.
    """
    rule = clean_recurrence(task.recurrence)
    if not rule:
        return None

    after_done = rule.startswith(RECURRENCE_AFTER)
    # Counted from the day it was finished, not from the date it carried.
    base = today_local(tz) if after_done else (task.deadline or today_local(tz))
    anchor = task.anchor_day or (base.day if rule == "monthly" else None)
    nxt = next_occurrence(rule, base, anchor_day=anchor)
    if nxt is None:
        return None
    # Never fall behind: after a long gap, the next copy is the next date from
    # today rather than a run of overdue clones.
    today = today_local(tz)
    while nxt < today:
        following = next_occurrence(rule, nxt, anchor_day=anchor)
        if following is None or following == nxt:
            break
        nxt = following

    series = task.series_id or task.id
    if task.series_id is None:
        task.series_id = series

    existing = s.scalar(select(Task.id).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        or_(Task.series_id == series,
            (Task.title == task.title) & (Task.recurrence == rule)),
        Task.status == "waiting", Task.deadline == nxt))
    if existing is not None:
        return None

    clone = Task(workspace_id=ws, title=task.title, description=task.description,
                 project_id=task.project_id, deadline=nxt, due_time=task.due_time,
                 remind_before=task.remind_before, recurrence=rule,
                 anchor_day=anchor, priority=task.priority,
                 timer_minutes=task.timer_minutes, series_id=series)
    s.add(clone)
    try:
        with s.begin_nested():
            s.flush()
    except IntegrityError:
        # Somebody else's completion already made it.
        return None
    return clone


def roll_recurring(s: Session, ws: int, tz: ZoneInfo | None = None) -> int:
    """Keep calendar repeats on the calendar (audit #14).

    A daily task used to appear for Tuesday only once Monday's copy was
    ticked, so one missed day stopped the series. Now, when a series has no
    open copy for today or later, the next date from today is created — the
    missed copy stays as it was. Only the most recent missed copy stays open:
    older misses of the same series are archived (never deleted), so a week
    away leaves one late reminder, not seven. Returns how many were created.
    """
    tz = tz or _habit_tz(s, ws)
    today = today_local(tz)
    rows = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.recurrence.is_not(None))).all()
    series: dict[int, list[Task]] = defaultdict(list)
    for task in rows:
        rule = clean_recurrence(task.recurrence)
        # "N days after done" repeats only on completion, never by the calendar.
        if rule and not rule.startswith(RECURRENCE_AFTER):
            series[task.series_id or task.id].append(task)
    made = 0
    for copies in series.values():
        if any(c.deadline is None or c.deadline >= today for c in copies):
            continue
        copies.sort(key=lambda c: (c.deadline, c.id))
        latest = copies[-1]
        if _spawn_next_occurrence(s, ws, latest, tz) is not None:
            made += 1
        for older in copies[:-1]:
            older.archived_at = utcnow()
    if made or any(len(c) > 1 for c in series.values()):
        s.commit()
    return made


#: workspace id -> the local day its series were last rolled. Rolling is
#: needed once a day (a copy can only be missed when a day ends), so the hot
#: read paths — Home, the task list, the reminder job — check this first
#: instead of querying every recurring task on every request.
_ROLLED: dict[int, date] = {}


def roll_recurring_daily(s: Session, ws: int, tz: ZoneInfo | None = None) -> int:
    tz = tz or _habit_tz(s, ws)
    today = today_local(tz)
    if _ROLLED.get(ws) == today:
        return 0
    made = roll_recurring(s, ws, tz)
    _ROLLED[ws] = today
    return made


def _finish_task(s: Session, ws: int, task: Task, tz: ZoneInfo | None,
                 moment: datetime | None = None) -> bool:
    """Mark a task done if it is still open. True when *this* call did it.

    A conditional UPDATE rather than read-then-write, so two completions of
    the same task — a double tap, the bot and the Mini App at once — finish it
    once and spawn its next occurrence once.
    """
    moment = moment or utcnow()
    won = s.execute(
        sql_update(Task).where(Task.id == task.id, Task.status != "done")
        .values(status="done", completed_at=moment)
        .execution_options(synchronize_session=False)).rowcount
    s.refresh(task)
    if not won:
        return False
    _spawn_next_occurrence(s, ws, task, tz)
    for countdown in s.scalars(select(Countdown).where(
            Countdown.workspace_id == ws, Countdown.team_id.is_(None),
            Countdown.scope == "task", Countdown.item_id == task.id,
            Countdown.archived_at.is_(None))).all():
        countdown.archived_at = utcnow()
    return True


def complete_task(s: Session, ws: int, task_id: int, *,
                  tz: ZoneInfo | None = None) -> Task:
    task = _owned_task(s, ws, task_id)
    if task.status != "done" and timer_blocks(s, ws, "task", task):
        raise ValueError("timer_required")
    _finish_task(s, ws, task, tz)
    s.commit()
    return task


def reopen_task(s: Session, ws: int, task_id: int) -> Task:
    task = _owned_task(s, ws, task_id)
    task.status = "waiting"
    task.completed_at = None
    s.commit()
    return task


def _move_deadline(s: Session, ws: int, task: Task, new: date | None) -> None:
    """Change a deadline, keeping the series index and linked countdowns true.

    Two open occurrences of one series cannot share a date, so a task moved
    onto a date its series already occupies leaves the series rather than
    failing the move. A countdown filed on the task follows it.
    """
    if new is not None and task.series_id is not None:
        clash = s.scalar(select(Task.id).where(
            Task.series_id == task.series_id, Task.deadline == new,
            Task.id != task.id))
        if clash is not None:
            task.series_id = None
    task.deadline = new
    task.reminder_sent_at = None
    if new is not None:
        for countdown in s.scalars(select(Countdown).where(
                Countdown.workspace_id == ws, Countdown.team_id.is_(None),
                Countdown.scope == "task", Countdown.item_id == task.id,
                Countdown.archived_at.is_(None))).all():
            countdown.target_date = new


def reschedule_task(s: Session, ws: int, task_id: int, when: str, *,
                    tz: ZoneInfo | None = None) -> Task:
    """Move a task's deadline with one tap: today, tomorrow, in 7 days or none."""
    task = _owned_task(s, ws, task_id)
    today = today_local(tz)
    targets = {"today": today, "tomorrow": today + timedelta(days=1),
               "week": today + timedelta(days=7), "none": None}
    if when not in targets:
        raise ValueError("unknown target")
    _move_deadline(s, ws, task, targets[when])
    s.commit()
    return task


#: Exactly one. "The most important thing today" is singular by definition.
#: The column stays `focus_day`, so the pick expires on its own overnight.
MAX_TOP3 = 1


def set_top3(s: Session, ws: int, task_id: int, picked: bool,
             day: date | None = None, *, tz: ZoneInfo | None = None) -> dict:
    """Pick or unpick today's main task."""
    task = _owned_task(s, ws, task_id)
    day = day or today_local(tz)

    if not picked:
        task.focus_day = None
        s.commit()
        return {"picked": False, "count": len(top3_tasks(s, ws, day))}

    current = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.focus_day == day, Task.id != task_id)).all()
    if len(current) >= MAX_TOP3:
        if MAX_TOP3 == 1:
            for previous in current:
                previous.focus_day = None
        else:
            raise ValueError("top3 full")

    # The day it is worked on, not the day it is due: picking Friday's article
    # for Monday leaves Friday as its deadline (audit #21).
    task.focus_day = day
    s.commit()
    return {"picked": True, "count": len(current) + 1}


def top3_tasks(s: Session, ws: int, day: date | None = None, *,
               tz: ZoneInfo | None = None) -> list[dict]:
    """The one the user chose for today, open and finished alike."""
    day = day or today_local(tz)
    rows = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.focus_day == day).order_by(Task.id)).all()
    runs = open_timer_runs(s, ws, "task")
    return [_task_dict(s, ws, task, day, runs) for task in rows]


def delete_task(s: Session, ws: int, task_id: int) -> str:
    task = _owned_task(s, ws, task_id)
    task.archived_at = utcnow()
    for countdown in s.scalars(select(Countdown).where(
            Countdown.workspace_id == ws, Countdown.team_id.is_(None),
            Countdown.scope == "task", Countdown.item_id == task.id,
            Countdown.archived_at.is_(None))).all():
        countdown.archived_at = utcnow()
    s.commit()
    return task.title


#: How far back a deleted task can still be brought back from the bot.
RESTORE_WINDOW = timedelta(days=30)


def archived_tasks(s: Session, ws: int, *, limit: int = 10) -> list[dict]:
    """Tasks deleted in the last month, newest first, that can come back.

    A task moved into a team is archived here too — the team holds the live
    copy — so a title that is live in one of the owner's teams, or live again
    in this workspace, is left out rather than offered twice.
    """
    since = utcnow() - RESTORE_WINDOW
    rows = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_not(None),
        Task.archived_at >= since)
        .order_by(Task.archived_at.desc(), Task.id.desc()).limit(limit * 3)).all()
    live = {x.strip().lower() for x in s.scalars(select(Task.title).where(
        Task.workspace_id == ws, Task.archived_at.is_(None))).all()}
    owner = workspace_owner(s, ws)
    team_ids = [team.id for team in teams_for(s, owner)] if owner else []
    if team_ids:
        live |= {x.strip().lower() for x in s.scalars(select(TeamTask.title).where(
            TeamTask.team_id.in_(team_ids), TeamTask.archived_at.is_(None))).all()}
    out, seen = [], set()
    for task in rows:
        key = task.title.strip().lower()
        if key in live or key in seen:
            continue
        seen.add(key)
        out.append({"id": task.id, "title": task.title,
                    "deadline": task.deadline.isoformat() if task.deadline else None})
        if len(out) >= limit:
            break
    return out


def restore_task(s: Session, ws: int, task_id: int) -> Task:
    """Bring a deleted task back, into its project if that is still there."""
    task = _owned_task(s, ws, task_id)
    if task.archived_at is None:
        return task
    task.archived_at = None
    if task.project_id:
        project = s.get(Project, task.project_id)
        if project is None or project.archived_at is not None or project.team_id is not None:
            task.project_id = None
    task.reminder_sent_at = None
    s.commit()
    return task


def update_task(s: Session, ws: int, task_id: int, **fields) -> Task:
    task = _owned_task(s, ws, task_id)
    tz = _habit_tz(s, ws)
    today = today_local(tz)
    if "title" in fields and fields["title"]:
        task.title = str(fields["title"]).strip()[:300]
    if "description" in fields:
        task.description = str(fields["description"] or "").strip()[:4000]
    if "priority" in fields and fields["priority"] in PRIORITIES \
            and fields["priority"] != task.priority:
        # The day it is due, the day keeps the priority it started with: a
        # lower one would buy a better score for the same unfinished work.
        if task.deadline == today and task.day_priority_date != today \
                and task.status != "done":
            task.day_priority = task.priority
            task.day_priority_date = today
        task.priority = fields["priority"]
    if "deadline" in fields:
        _move_deadline(s, ws, task, fields["deadline"])
    if "due_time" in fields:
        task.due_time = fields["due_time"]
        task.reminder_sent_at = None
    if "remind_before" in fields:
        task.remind_before = clean_remind_before(fields["remind_before"])
        task.reminder_sent_at = None
    if "recurrence" in fields:
        task.recurrence = clean_recurrence(fields["recurrence"])
        _ROLLED.pop(ws, None)
    if "project_id" in fields:
        pid = fields["project_id"]
        if pid:
            project = s.get(Project, pid)
            if project is None or project.workspace_id != ws or project.team_id is not None:
                raise NotFound("project")
            task.project_id = project.id
        else:
            task.project_id = None
    if "timer_minutes" in fields:
        _apply_timer_setting(s, ws, "task", task, fields["timer_minutes"])
    if "status" in fields and fields["status"] in ("waiting", "done"):
        if fields["status"] == "done":
            if task.status != "done" and timer_blocks(s, ws, "task", task):
                raise ValueError("timer_required")
            _finish_task(s, ws, task, tz)
        else:
            task.status = "waiting"
            task.completed_at = None
    s.commit()
    return task


def _task_weight_priority(task: Task, day: date) -> str:
    """The priority a task is scored with on `day`."""
    if task.day_priority_date == day and task.day_priority in PRIORITIES:
        return task.day_priority
    return task.priority


def _task_countdowns(s: Session, ws: int, task_ids: list[int], today: date) -> dict[int, dict]:
    if not task_ids:
        return {}
    out = {}
    for row in s.scalars(select(Countdown).where(
            Countdown.workspace_id == ws, Countdown.team_id.is_(None),
            Countdown.scope == "task", Countdown.item_id.in_(task_ids),
            Countdown.archived_at.is_(None))).all():
        out[row.item_id] = {"id": row.id,
                            "days_left": (row.target_date - today).days}
    return out


def _task_dict(s: Session, ws: int, task: Task, today: date,
               runs: dict | None = None, countdowns: dict | None = None) -> dict:
    project_name = None
    if task.project_id:
        project = s.get(Project, task.project_id)
        project_name = project.name if project else None
    days_left = (task.deadline - today).days if task.deadline else None
    return {
        "id": task.id, "title": task.title, "description": task.description,
        "status": task.status, "priority": task.priority,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "due_time": task.due_time.strftime("%H:%M") if task.due_time else None,
        "remind_before": task.remind_before,
        "recurrence": clean_recurrence(task.recurrence),
        "top3": task.focus_day == today,
        "days_left": days_left,
        "overdue": bool(task.deadline and task.deadline < today and task.status != "done"),
        "project_id": task.project_id, "project": project_name,
        "countdown": (countdowns or {}).get(task.id),
        **_timer_fields(task.timer_minutes, task.title),
        "timer": (runs or {}).get(task.id),
        "blocked": task.blocked_reason if task.status != "done" else None,
        "blocked_until": task.blocked_until.isoformat() if task.blocked_until else None,
        # The day to look again has come: "has the reply arrived?"
        "recheck": bool(task.blocked_reason and task.status != "done"
                        and (task.blocked_until is None or task.blocked_until <= today)),
        "source": "personal",
    }


BLOCK_REASONS = ("reply", "depends")


def set_task_blocked(s: Session, ws: int, task_id: int, reason: str | None,
                     until: date | None = None) -> Task:
    """Mark a task as waiting on someone or something else, or clear that.

    It stays open and keeps its deadline; it just stops standing in "Now" or
    in a reset until `until`, when it is offered again as a check (audit #12).
    """
    task = _owned_task(s, ws, task_id)
    if reason is None:
        task.blocked_reason = task.blocked_until = None
    else:
        if reason not in BLOCK_REASONS:
            raise ValueError("bad_reason")
        task.blocked_reason, task.blocked_until = reason, until
    s.commit()
    return task


def _is_parked(task: dict) -> bool:
    """Blocked and not yet due for a re-check: out of the "Now" queue."""
    return bool(task.get("blocked")) and not task.get("recheck")


def _sort_open(rows: list[dict]) -> list[dict]:
    """Deadline, then time of day, then priority — the order to work in."""
    return sorted(rows, key=lambda t: (
        t["deadline"] or "9999-12-31", t["due_time"] or "99:99",
        _PRIORITY_RANK.get(t["priority"], 1), t["id"]))


def list_tasks(s: Session, ws: int, *, horizon_days: int = 7,
               include_done: bool = False, search: str = "",
               project_id: int | None = None, priority: str = "",
               tz: ZoneInfo | None = None) -> dict:
    """Tasks grouped for display: overdue, today, the next N days, undated."""
    today = today_local(tz)
    limit = today + timedelta(days=horizon_days)
    needle = search.strip().lower()[:100]

    settle_timers(s, ws)
    roll_recurring_daily(s, ws, tz)
    runs = open_timer_runs(s, ws, "task")
    stmt = select(Task).where(Task.workspace_id == ws, Task.archived_at.is_(None))
    if not include_done:
        stmt = stmt.where(Task.status == "waiting")
    if project_id is not None:
        stmt = stmt.where(Task.project_id == project_id)
    if priority in PRIORITIES:
        stmt = stmt.where(Task.priority == priority)
    tasks = s.scalars(stmt.order_by(Task.deadline.is_(None), Task.deadline,
                                    Task.priority)).all()
    countdowns = _task_countdowns(s, ws, [t.id for t in tasks], today)

    overdue, today_rows, upcoming, undated, later = [], [], [], [], []
    for task in tasks:
        if needle and needle not in (task.title or "").lower() \
                and needle not in (task.description or "").lower():
            continue
        row = _task_dict(s, ws, task, today, runs, countdowns)
        if task.deadline is None:
            undated.append(row)
        elif task.deadline < today and task.status != "done":
            overdue.append(row)
        elif task.deadline <= limit:
            upcoming.append(row)
            if task.deadline == today:
                today_rows.append(row)
        else:
            later.append(row)
    return {"overdue": _sort_open(overdue), "upcoming": _sort_open(upcoming),
            "today": _sort_open(today_rows),
            "undated": _sort_open(undated), "later": _sort_open(later),
            "total": len(overdue) + len(upcoming) + len(undated) + len(later)}


def completed_tasks(s: Session, ws: int, limit: int = 200, *, search: str = "",
                    tz: ZoneInfo | None = None) -> dict:
    """The Done archive, in three buckets rather than one endless list."""
    today = today_local(tz)
    monday = week_start(today)
    needle = search.strip().lower()[:100]

    stmt = select(Task).where(Task.workspace_id == ws, Task.archived_at.is_(None),
                              Task.status == "done")
    if needle:
        pattern = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(Task.title.ilike(pattern, escape="\\"))
    rows = s.scalars(stmt.order_by(Task.completed_at.desc(), Task.id.desc()).limit(limit)).all()

    today_group, week_group, earlier = [], [], []
    for task in rows:
        if needle and needle not in (task.title or "").lower():
            continue
        row = _task_dict(s, ws, task, today)
        when = local_date_of(task.completed_at, tz)
        if when == today:
            today_group.append(row)
        elif when is not None and when >= monday:
            week_group.append(row)
        else:
            earlier.append(row)
    return {"today": today_group, "week": week_group, "earlier": earlier,
            "total": len(today_group) + len(week_group) + len(earlier)}


def tasks_due_today(s: Session, ws: int, *, tz: ZoneInfo | None = None) -> list[dict]:
    roll_recurring_daily(s, ws, tz)
    today = today_local(tz)
    tasks = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline == today)).all()
    runs = open_timer_runs(s, ws, "task")
    countdowns = _task_countdowns(s, ws, [t.id for t in tasks], today)
    return _sort_open([_task_dict(s, ws, t, today, runs, countdowns) for t in tasks])


def today_tasks_by_project(s: Session, ws: int, *, tz: ZoneInfo | None = None,
                           skip_ids: set[int] | None = None) -> list[dict]:
    """Today's open tasks, grouped under the project they belong to."""
    skip_ids = skip_ids or set()
    groups: dict[int | None, dict] = {}
    for task in tasks_due_today(s, ws, tz=tz):
        if task["id"] in skip_ids:
            continue
        key = task["project_id"]
        group = groups.setdefault(key, {
            "project_id": key, "project": task["project"], "tasks": []})
        group["tasks"].append(task)

    for group in groups.values():
        group["tasks"].sort(key=lambda t: (_PRIORITY_RANK.get(t["priority"], 1),
                                           t["id"]))

    named = sorted((g for g in groups.values() if g["project_id"] is not None),
                   key=lambda g: (g["project"] or "").lower())
    standalone = [g for g in groups.values() if g["project_id"] is None]
    return named + standalone


# ---------------------------------------------------------------------------
# The week's goal — one primary, two supporting
# ---------------------------------------------------------------------------

#: Slot 1 is the week's goal; slots 2 and 3 are supporting priorities.
MAX_FOCUS = 3
PRIMARY_SLOT = 1

MISSION_PRIORITIES = ["high", "medium", "low"]
DEFAULT_MISSION_PRIORITY = "medium"


def focus_is_done(row: WeeklyFocus, linked: dict[int, Task] | None = None) -> bool:
    """The one rule for whether a week goal is done, for every screen: a goal
    delivered by a task is done when that task is (audit #11)."""
    task = (linked or {}).get(row.task_id) if row.task_id else None
    return bool((task.status == "done") if task is not None else row.done)


def _focus_dict(row: WeeklyFocus, linked: dict[int, Task] | None = None,
                carries: int = 0, goals: dict[int, "LifeGoal"] | None = None) -> dict:
    task = (linked or {}).get(row.task_id) if row.task_id else None
    goal = (goals or {}).get(row.goal_id) if row.goal_id else None
    done = focus_is_done(row, linked)
    return {"id": row.id, "slot": row.slot, "title": row.title,
            # Moved on to a later week: kept here as history, not counted.
            "carried": row.carried_to is not None,
            # How many weeks running it has been pushed — a hint to shrink it.
            "carries": carries,
            "priority": row.priority if row.priority in MISSION_PRIORITIES
                        else DEFAULT_MISSION_PRIORITY,
            "primary": row.slot == PRIMARY_SLOT,
            "done": bool(done),
            # A goal delivered by a task is done when the task is, and is
            # scored through the task — once.
            "task_id": row.task_id if task is not None else None,
            "task_title": task.title if task is not None else None,
            # The milestone this week's goal moves forward (audit S24).
            "goal_id": goal.id if goal is not None else None,
            "goal_title": goal.title if goal is not None else None}


def _linked_goals(s: Session, ws: int, rows: list[WeeklyFocus]) -> dict[int, "LifeGoal"]:
    ids = [r.goal_id for r in rows if r.goal_id]
    if not ids:
        return {}
    return {g.id: g for g in s.scalars(select(LifeGoal).where(
        LifeGoal.workspace_id == ws, LifeGoal.id.in_(ids),
        LifeGoal.archived_at.is_(None))).all()}


def _focus_goal_or_none(s: Session, ws: int, goal_id: int | None) -> int | None:
    """A week's goal can point at one of the owner's live milestones."""
    if not goal_id:
        return None
    goal = s.get(LifeGoal, goal_id)
    if (goal is None or goal.workspace_id != ws or goal.archived_at is not None
            or goal.level != "milestone"):
        raise NotFound("goal")
    return goal.id


def _linked_tasks(s: Session, ws: int, rows: list[WeeklyFocus]) -> dict[int, Task]:
    ids = [r.task_id for r in rows if r.task_id]
    if not ids:
        return {}
    return {t.id: t for t in s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.id.in_(ids),
        Task.archived_at.is_(None))).all()}


def list_focus(s: Session, ws: int, when: date | None = None, *,
               tz: ZoneInfo | None = None,
               include_carried: bool = False) -> list[dict]:
    """The week's goals. A goal moved on to a later week stays in its old
    week as history; it is listed only when asked for, and never counted."""
    start = week_start(when or today_local(tz))
    stmt = select(WeeklyFocus).where(
        WeeklyFocus.workspace_id == ws, WeeklyFocus.week_start == start)
    if not include_carried:
        stmt = stmt.where(WeeklyFocus.carried_to.is_(None))
    rows = s.scalars(stmt.order_by(WeeklyFocus.slot, WeeklyFocus.id)).all()
    linked = _linked_tasks(s, ws, rows)
    goals = _linked_goals(s, ws, rows)
    return [_focus_dict(r, linked, _carry_count(s, r), goals) for r in rows]


def _carry_count(s: Session, row: WeeklyFocus) -> int:
    n, seen = 0, set()
    while row is not None and row.carried_from and row.carried_from not in seen and n < 52:
        seen.add(row.carried_from)
        row = s.get(WeeklyFocus, row.carried_from)
        n += 1
    return n


def week_focus(s: Session, ws: int, when: date | None = None, *,
               tz: ZoneInfo | None = None) -> dict:
    """The week split into its one goal and its supporting priorities."""
    everything = list_focus(s, ws, when, tz=tz, include_carried=True)
    rows = [r for r in everything if not r["carried"]]
    primary = next((r for r in rows if r["slot"] == PRIMARY_SLOT), None)
    supporting = [r for r in rows if r["slot"] != PRIMARY_SLOT]
    return {
        "primary": primary, "supporting": supporting,
        "carried": [r for r in everything if r["carried"]],
        "slots_free": max(MAX_FOCUS - len(rows), 0),
        "done": sum(1 for r in rows if r["done"]), "total": len(rows),
    }


def primary_focus(s: Session, ws: int, when: date | None = None, *,
                  tz: ZoneInfo | None = None) -> dict | None:
    """The one goal Home leads with."""
    rows = list_focus(s, ws, when, tz=tz)
    if not rows:
        return None
    return next((r for r in rows if r["slot"] == PRIMARY_SLOT), rows[0])


def _focus_task_or_none(s: Session, ws: int, task_id: int | None) -> int | None:
    if not task_id:
        return None
    task = s.get(Task, task_id)
    if task is None or task.workspace_id != ws or task.archived_at is not None:
        raise NotFound("task")
    return task.id


def add_focus(s: Session, ws: int, title: str, when: date | None = None, *,
              priority: str = DEFAULT_MISSION_PRIORITY,
              task_id: int | None = None, goal_id: int | None = None,
              tz: ZoneInfo | None = None) -> WeeklyFocus:
    title = title.strip()[:200]
    if not title:
        raise ValueError("empty focus title")
    if priority not in MISSION_PRIORITIES:
        priority = DEFAULT_MISSION_PRIORITY
    start = week_start(when or today_local(tz))
    used = {r.slot for r in s.scalars(select(WeeklyFocus).where(
        WeeklyFocus.workspace_id == ws, WeeklyFocus.week_start == start,
        WeeklyFocus.carried_to.is_(None))).all()}
    free = next((n for n in range(1, MAX_FOCUS + 1) if n not in used), None)
    if free is None:
        raise ValueError("week is full")
    row = WeeklyFocus(workspace_id=ws, week_start=start, slot=free, title=title,
                      priority=priority, task_id=_focus_task_or_none(s, ws, task_id),
                      goal_id=_focus_goal_or_none(s, ws, goal_id))
    s.add(row)
    s.commit()
    return row


def carry_focus_forward(s: Session, ws: int, focus_id: int, *,
                        tz: ZoneInfo | None = None) -> WeeklyFocus:
    """Move an unfinished goal into next week, instead of retyping it."""
    row = s.get(WeeklyFocus, focus_id)
    if row is None or row.workspace_id != ws:
        raise NotFound("focus")

    target = row.week_start + timedelta(days=7)
    used = {r.slot for r in s.scalars(select(WeeklyFocus).where(
        WeeklyFocus.workspace_id == ws, WeeklyFocus.week_start == target,
        WeeklyFocus.carried_to.is_(None))).all()}
    free = next((n for n in range(1, MAX_FOCUS + 1) if n not in used), None)
    if free is None:
        raise ValueError("week is full")

    if row.carried_to is not None:
        raise ValueError("already_carried")
    moved = WeeklyFocus(workspace_id=ws, week_start=target, slot=free,
                        title=row.title, priority=row.priority, task_id=row.task_id,
                        goal_id=row.goal_id, carried_from=row.id)
    s.add(moved)
    s.flush()
    # The old week keeps its row, marked as moved on (audit #17). Its slot is
    # given up — a negative, unique number — so the week can take a new goal.
    row.carried_to = moved.id
    row.slot = -row.id
    s.commit()
    return moved


def edit_focus(s: Session, ws: int, focus_id: int, title: str, *,
               priority: str | None = None, task_id=..., goal_id=...) -> WeeklyFocus:
    row = s.get(WeeklyFocus, focus_id)
    if row is None or row.workspace_id != ws:
        raise NotFound("focus")
    title = title.strip()[:200]
    if not title:
        raise ValueError("empty focus title")
    row.title = title
    if priority in MISSION_PRIORITIES:
        row.priority = priority
    if task_id is not ...:
        row.task_id = _focus_task_or_none(s, ws, task_id)
    if goal_id is not ...:
        row.goal_id = _focus_goal_or_none(s, ws, goal_id)
    s.commit()
    return row


def toggle_focus(s: Session, ws: int, focus_id: int) -> bool:
    """Tick a goal. A goal delivered by a task is ticked by ticking the task."""
    row = s.get(WeeklyFocus, focus_id)
    if row is None or row.workspace_id != ws:
        raise NotFound("focus")
    if row.task_id:
        task = s.get(Task, row.task_id)
        if task is not None and task.workspace_id == ws and task.archived_at is None:
            if task.status == "done":
                reopen_task(s, ws, task.id)
                return False
            complete_task(s, ws, task.id, tz=_habit_tz(s, ws))
            return True
    row.done = not row.done
    s.commit()
    return row.done


def delete_focus(s: Session, ws: int, focus_id: int) -> str:
    row = s.get(WeeklyFocus, focus_id)
    if row is None or row.workspace_id != ws:
        raise NotFound("focus")
    title = row.title
    s.delete(row)
    s.commit()
    return title


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

#: The daily journal is five fixed questions.
JOURNAL_QUESTIONS = [
    {"id": "wins",      "uz": "Bugun nimalarga erishdim?",
     "en": "What did I accomplish today?", "ru": "Чего я достиг сегодня?"},
    {"id": "gratitude", "uz": "Nima uchun shukr qilaman?",
     "en": "What am I grateful for?", "ru": "За что я благодарен?"},
    {"id": "problem",   "uz": "Qaysi muammoga duch keldim?",
     "en": "What problem did I face?", "ru": "С какой проблемой столкнулся?"},
    {"id": "lesson",    "uz": "Bugun nima o'rgandim?",
     "en": "What did I learn today?", "ru": "Чему я научился сегодня?"},
    {"id": "tomorrow",  "uz": "Ertaga eng muhim ish nima?",
     "en": "What is tomorrow's most important task?",
     "ru": "Главная задача на завтра?"},
]
JOURNAL_KEYS = [q["id"] for q in JOURNAL_QUESTIONS]

#: How many meaningful answers make a written day — the Kundalik habit's rule.
#: One. Three honest lines on a hard evening are a journal entry, and scoring
#: them the same as nothing is how somebody stops writing on hard evenings.
JOURNAL_DONE_MIN = 1

#: The five moods the optional check-in offers, saddest first.
MOODS = ["awful", "low", "ok", "good", "great"]


def journal_answered(answers: dict) -> int:
    """How many of the five have something in them."""
    return sum(1 for key in JOURNAL_KEYS if str(answers.get(key, "")).strip())


def journal_is_complete(answers: dict) -> bool:
    """All five answered — a full reflection."""
    return journal_answered(answers) == len(JOURNAL_KEYS)


def journal_is_written(answers: dict) -> bool:
    """Enough written to count the day — what ticks the Kundalik habit."""
    return journal_answered(answers) >= JOURNAL_DONE_MIN


def journal_done(s: Session, ws: int, day: date | None = None, *,
                 tz: ZoneInfo | None = None) -> bool:
    """Whether the day's journal is written (at least one answer)."""
    entry = get_journal(s, ws, day or today_local(tz))
    return bool(entry and entry["written"])


def get_journal(s: Session, ws: int, day: date | None = None, *,
                tz: ZoneInfo | None = None) -> dict | None:
    day = day or today_local(tz)
    row = s.scalar(select(JournalEntry).where(
        JournalEntry.workspace_id == ws, JournalEntry.day == day))
    if row is None:
        return None
    try:
        answers = json.loads(row.answers or "{}")
    except json.JSONDecodeError:
        answers = {}
    return {"day": row.day.isoformat(), "text": row.text, "mood": row.mood,
            "updated_at": _iso_utc(row.updated_at),
            "answers": answers, "answered": journal_answered(answers),
            "total": len(JOURNAL_KEYS),
            "written": journal_is_written(answers),
            "complete": journal_is_complete(answers)}


def save_journal(s: Session, ws: int, *, answers: dict | None = None,
                 text: str = "", day: date | None = None,
                 mood: str = "", tz: ZoneInfo | None = None) -> JournalEntry:
    """Save whatever is written so far. Answers are merged, never replaced."""
    day = day or today_local(tz)
    row = s.scalar(select(JournalEntry).where(
        JournalEntry.workspace_id == ws, JournalEntry.day == day))
    if row is None:
        row = JournalEntry(workspace_id=ws, day=day)
        s.add(row)
        try:
            with s.begin_nested():
                s.flush()
        except IntegrityError:
            # Two autosaves for the same new day arrived together.
            row = s.scalar(select(JournalEntry).where(
                JournalEntry.workspace_id == ws, JournalEntry.day == day))

    if answers is not None:
        try:
            current = json.loads(row.answers or "{}")
        except json.JSONDecodeError:
            current = {}
        current.update({k: str(v).strip()[:2000] for k, v in answers.items()
                        if k in JOURNAL_KEYS})
        cleaned = {k: v for k, v in current.items() if k in JOURNAL_KEYS}
        row.answers = json.dumps(cleaned, ensure_ascii=False)
        # Flat copy so search and exports stay simple.
        row.text = "\n\n".join(
            f"{q['uz']}\n{cleaned.get(q['id'], '')}".strip()
            for q in JOURNAL_QUESTIONS if cleaned.get(q["id"]))
    elif text:
        row.text = text.strip()

    if mood:
        row.mood = mood[:20] if mood in MOODS else ""

    s.commit()
    sync_journal_habit(s, ws, day)
    return row


def sync_journal_habit(s: Session, ws: int, day: date) -> bool:
    """Tick the `Kundalik` habit on any written day."""
    habit = s.scalar(select(Habit).where(
        Habit.workspace_id == ws, Habit.system_key == SYSTEM_JOURNAL,
        Habit.archived_at.is_(None)))
    if habit is None:
        return False

    entry = get_journal(s, ws, day)
    done = bool(entry and entry["written"])

    row = s.scalar(select(HabitLog).where(
        HabitLog.workspace_id == ws, HabitLog.habit_id == habit.id,
        HabitLog.day == day))
    if row is None:
        s.add(HabitLog(workspace_id=ws, habit_id=habit.id, day=day, done=done))
    else:
        row.done = done
    s.commit()
    return done


def list_journal(s: Session, ws: int, limit: int = 60) -> list[dict]:
    rows = s.scalars(select(JournalEntry).where(JournalEntry.workspace_id == ws)
                     .order_by(JournalEntry.day.desc()).limit(limit)).all()
    out = []
    for r in rows:
        try:
            answers = json.loads(r.answers or "{}")
        except json.JSONDecodeError:
            answers = {}
        out.append({"day": r.day.isoformat(), "text": r.text,
                    "preview": (r.text or "").replace("\n", " ")[:80],
                    "mood": r.mood, "answers": answers,
                    "answered": journal_answered(answers),
                    "total": len(JOURNAL_KEYS),
                    "written": journal_is_written(answers),
                    "complete": journal_is_complete(answers)})
    return out


def delete_journal(s: Session, ws: int, day: date) -> None:
    """Remove a day's entry, and untick the habit it was driving."""
    row = s.scalar(select(JournalEntry).where(
        JournalEntry.workspace_id == ws, JournalEntry.day == day))
    if row is None:
        raise NotFound("journal")
    s.delete(row)
    s.commit()
    sync_journal_habit(s, ws, day)


# ---------------------------------------------------------------------------
# Birthdays
# ---------------------------------------------------------------------------

def _next_occurrence(birth: date, today: date) -> date:
    """This year's birthday, or next year's if it already passed."""
    try:
        this_year = birth.replace(year=today.year)
    except ValueError:
        this_year = date(today.year, 2, 28)
    if this_year < today:
        try:
            return birth.replace(year=today.year + 1)
        except ValueError:
            return date(today.year + 1, 2, 28)
    return this_year


def list_birthdays(s: Session, ws: int, within_days: int = 30, *,
                   tz: ZoneInfo | None = None) -> list[dict]:
    today = today_local(tz)
    rows = s.scalars(select(Birthday).where(Birthday.workspace_id == ws)).all()
    out = []
    for r in rows:
        nxt = _next_occurrence(r.birth_date, today)
        days = (nxt - today).days
        if days <= within_days:
            out.append({
                "id": r.id, "person_name": r.person_name,
                "birth_date": r.birth_date.isoformat(),
                "next": nxt.isoformat(), "days_left": days,
                "turning": nxt.year - r.birth_date.year,
                "note": r.note,
            })
    return sorted(out, key=lambda x: x["days_left"])


def add_birthday(s: Session, ws: int, person_name: str, birth_date: date,
                 note: str = "") -> Birthday:
    person_name = person_name.strip()[:200]
    if not person_name:
        raise ValueError("empty name")
    row = Birthday(workspace_id=ws, person_name=person_name,
                   birth_date=birth_date, note=note.strip()[:300])
    s.add(row)
    s.commit()
    return row


def update_birthday(s: Session, ws: int, birthday_id: int, **fields) -> Birthday:
    row = s.get(Birthday, birthday_id)
    if row is None or row.workspace_id != ws:
        raise NotFound("birthday")
    if fields.get("person_name"):
        name = str(fields["person_name"]).strip()[:200]
        if not name:
            raise ValueError("empty name")
        row.person_name = name
    if fields.get("birth_date"):
        row.birth_date = fields["birth_date"]
    if "note" in fields:
        row.note = str(fields["note"] or "").strip()[:300]
    s.commit()
    return row


def delete_birthday(s: Session, ws: int, birthday_id: int) -> str:
    row = s.get(Birthday, birthday_id)
    if row is None or row.workspace_id != ws:
        raise NotFound("birthday")
    name = row.person_name
    s.delete(row)
    s.commit()
    return name


# ---------------------------------------------------------------------------
# Money — kept apart from everything productive
# ---------------------------------------------------------------------------
#
# What came in and what went out, by category, against a monthly limit. None
# of it feeds a score, a streak, XP or a report: spending is not a measure of
# how well a day went, and mixing the two would make both harder to read.
# Amounts are whole so'm.

MONEY_KINDS = ("expense", "income")

#: (id, icon, colour, default monthly limit, kind). The limit is a starting
#: point the user overrides per category; 0 means "no limit".
MONEY_CATEGORIES = [
    ("food", "🍔", "#EF4444", 2_000_000, "expense"),
    ("transport", "🚕", "#F59E0B", 800_000, "expense"),
    ("home", "🏠", "#3B82F6", 1_500_000, "expense"),
    ("health", "💊", "#10B981", 500_000, "expense"),
    ("fun", "🎮", "#8B5CF6", 1_000_000, "expense"),
    ("business", "💼", "#0EA5E9", 0, "expense"),
    ("other", "📦", "#6B7280", 500_000, "expense"),
    ("salary", "💰", "#22C55E", 0, "income"),
    ("sales", "📈", "#14B8A6", 0, "income"),
    ("other_in", "➕", "#84CC16", 0, "income"),
]
MONEY_CATEGORY_IDS = [c[0] for c in MONEY_CATEGORIES]
_MONEY_KIND_OF = {c[0]: c[4] for c in MONEY_CATEGORIES}
MONEY_MAX_AMOUNT = 10 ** 12

#: Words that name a category, in Uzbek, Russian and English. The first match
#: wins, so the more specific groups come first.
_MONEY_WORDS = [
    ("food", r"tushlik|ovqat|nonushta|kechki ovqat|oziq|kafe|restoran|non\b|go'sht|"
             r"обед|еда|завтрак|ужин|продукт|кафе|ресторан|lunch|food|dinner|breakfast|"
             r"grocer|cafe|restaurant"),
    ("transport", r"taxi|taksi|transport|avtobus|metro|benzin|yoqilg'i|"
                  r"такси|автобус|метро|бензин|bus|fuel|uber|yandex"),
    ("home", r"\buy\b|ijara|kommunal|\bgaz\b|svet|elektr|\bsuv\b|internet|"
             r"аренд|коммунал|свет|интернет|rent|utilit|electric"),
    ("health", r"dori|shifokor|kasalxona|dorixona|klinika|stomatolog|"
               r"лекар|аптек|врач|клиник|pharmacy|doctor|clinic|medicine"),
    ("fun", r"kino|o'yin|oyin|ko'ngil|kongil|sayohat|dam olish|"
            r"кино|игр|развлеч|путешеств|movie|game|travel|trip"),
    ("business", r"reklama|tovar|biznes|sklad|yetkazib|dropship|target|"
                 r"реклам|товар|бизнес|склад|доставк|ads|stock|business|shipping"),
]
#: "oylik" is left out on purpose: "oylik ijara" is monthly rent. "Oylik
#: keldi" is still income through "keldi", and still files under salary.
_INCOME_WORDS = (r"keldi|tushdi|maosh|daromad|kirim|bonus|sotdim|foyda|"
                 r"зарплат|доход|пришл|получил|продал|прибыл|"
                 r"salary|income|earned|received|sold|profit")
_EXPENSE_WORDS = (r"sarfladim|sarflad|to'ladim|toladim|to'lov|ketdi|xarajat|"
                  r"sotib oldim|ishlatdim|berdim|"
                  r"потратил|заплатил|купил|расход|spent|paid|bought")
_AMOUNT_RE = re.compile(
    r"(\d+(?:[  ]\d{3})*(?:[.,]\d+)?)\s*"
    r"(mlrd|milliard|млрд|billion|bn|million|millon|mln|млн|миллион\w*|"
    r"ming|минг|тыс\w*|thousand|k\b|к\b)?", re.IGNORECASE)
_MULTIPLIERS = {"mlrd": 10 ** 9, "milliard": 10 ** 9, "млрд": 10 ** 9,
                "billion": 10 ** 9, "bn": 10 ** 9,
                "million": 10 ** 6, "millon": 10 ** 6, "mln": 10 ** 6,
                "млн": 10 ** 6, "миллион": 10 ** 6,
                "ming": 1000, "минг": 1000, "тыс": 1000, "thousand": 1000,
                "k": 1000, "к": 1000}
_THOUSANDS_SEP = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")


def money_category_kind(category: str) -> str | None:
    return _MONEY_KIND_OF.get(category)


def clean_money_amount(value) -> int:
    """A positive whole amount, or ValueError."""
    try:
        amount = int(round(float(value)))
    except (TypeError, ValueError):
        raise ValueError("bad_amount")
    if amount <= 0 or amount > MONEY_MAX_AMOUNT:
        raise ValueError("bad_amount")
    return amount


def _unit_factor(unit: str) -> int:
    unit = (unit or "").lower()
    if not unit:
        return 1
    if unit.startswith("тыс"):
        return 1000
    if unit.startswith("миллион"):
        return 10 ** 6
    return _MULTIPLIERS.get(unit, 1)


def parse_money_amount(text: str) -> int | None:
    """`45 ming` → 45000, `1,5 mln` → 1500000, `45 000` → 45000, else None.

    A number with a unit beats a bare one, so "2 ta non 8 ming" is 8 000 and
    not 2. Without a unit, `45,000` and `45.000` are thousands separators.
    """
    found: list[tuple[bool, int, int]] = []
    for number, unit in _AMOUNT_RE.findall(text or ""):
        raw = number.replace(" ", "").replace(" ", "")
        factor = _unit_factor(unit)
        if factor == 1 and _THOUSANDS_SEP.match(raw):
            raw = raw.replace(",", "").replace(".", "")
        try:
            value = float(raw.replace(",", "."))
        except ValueError:
            continue
        amount = int(round(value * factor))
        if 0 < amount <= MONEY_MAX_AMOUNT:
            found.append((factor != 1, amount, factor))
    if not found:
        return None
    with_unit = [(amount, factor) for has_unit, amount, factor in found if has_unit]
    if not with_unit:
        return found[0][1]
    # "1 mln 200 ming" is one amount: units that step down add up. The same
    # unit twice ("tushlik 45 ming, taksi 20 ming") is two amounts — the first.
    total, last = with_unit[0]
    for amount, factor in with_unit[1:]:
        if factor >= last:
            break
        total, last = total + amount, factor
    return min(total, MONEY_MAX_AMOUNT)


def detect_money_category(text: str, kind: str = "expense") -> str:
    lowered = (text or "").lower()
    if kind == "income":
        if re.search(r"sotdim|savdo|sotuv|продал|продаж|sold|sales", lowered):
            return "sales"
        if re.search(r"maosh|oylik|зарплат|salary", lowered):
            return "salary"
        return "other_in"
    for category, pattern in _MONEY_WORDS:
        if re.search(pattern, lowered):
            return category
    return "other"


def parse_money_text(text: str, kind: str | None = None) -> dict | None:
    """"Tushlikka 45 ming sarfladim" → an expense of 45 000 on food.

    Spending words win over income words ("sotib oldim" is spending even
    though it contains "oldim"). With no word either way, an amount is taken
    as spending — that is what people type far more often. A `kind` the
    person chose (the Chiqim / Kirim button) beats every guess.
    """
    amount = parse_money_amount(text)
    if amount is None:
        return None
    lowered = (text or "").lower()
    if kind in MONEY_KINDS:
        pass
    elif re.search(_EXPENSE_WORDS, lowered):
        kind = "expense"
    elif re.search(_INCOME_WORDS, lowered):
        kind = "income"
    else:
        kind = "expense"
    return {"kind": kind, "amount": amount,
            "category": detect_money_category(text, kind),
            "note": (text or "").strip()[:200]}


_MONEY_HINT = re.compile(r"so'?m\b|сум|\bsum\b|ming\b|mln\b|million|млн|тыс|"
                         r"\d\s*k\b|" + _EXPENSE_WORDS + "|" + _INCOME_WORDS,
                         re.IGNORECASE)


def looks_like_money(text: str) -> bool:
    """Whether a free message is about money rather than a task with a number.

    "Tushlik 45 ming" is money; "call mum at 10" is not — a number alone is
    never enough. It needs a money word, a unit or a spending/earning verb, or
    a spending category ("kommunal", "dorixona") with an amount of at least a
    thousand: nobody pays 3 so'm for anything, but plenty of tasks say "3".
    """
    amount = parse_money_amount(text)
    if amount is None:
        return False
    if _MONEY_HINT.search(text or ""):
        return True
    lowered = (text or "").lower()
    return amount >= 1000 and any(re.search(pattern, lowered)
                                  for _cat, pattern in _MONEY_WORDS)


def _money_dict(row: MoneyEntry) -> dict:
    return {"id": row.id, "kind": row.kind, "amount": int(row.amount),
            "category": row.category, "note": row.note or "",
            "source": row.source or "manual", "day": row.day.isoformat(),
            "account_id": row.account_id,
            "created_at": (row.created_at.replace(tzinfo=_utc.utc).isoformat()
                           if row.created_at else None)}


def add_money(s: Session, ws: int, kind: str, amount, category: str = "", *,
              note: str = "", source: str = "manual", day: date | None = None,
              tz: ZoneInfo | None = None, account_id: int | None = None) -> dict:
    if kind not in MONEY_KINDS:
        raise ValueError("bad_kind")
    amount = clean_money_amount(amount)
    if money_category_kind(category) != kind:
        category = detect_money_category(note, kind)
    if account_id is not None:
        _own_account(s, ws, account_id)
    tz = tz or _habit_tz(s, ws)
    row = MoneyEntry(workspace_id=ws, kind=kind, amount=amount, category=category,
                     note=str(note or "").strip()[:200],
                     source=source if source in ("manual", "voice", "bot") else "manual",
                     day=day or today_local(tz), account_id=account_id)
    s.add(row)
    s.commit()
    return _money_dict(row)


def delete_money(s: Session, ws: int, entry_id: int) -> dict:
    row = s.get(MoneyEntry, entry_id)
    if row is None or row.workspace_id != ws:
        raise NotFound("money")
    out = _money_dict(row)
    s.delete(row)
    s.commit()
    return out


def update_money(s: Session, ws: int, entry_id: int, *, kind: str | None = None,
                 amount=None, category: str | None = None, note: str | None = None,
                 day: date | None = None, account_id=...) -> dict:
    """Correct an entry in place (K21): amount, direction, category, note or
    day. Deleting and typing it again lost its place and its history."""
    row = s.get(MoneyEntry, entry_id)
    if row is None or row.workspace_id != ws:
        raise NotFound("money")
    if kind is not None:
        if kind not in MONEY_KINDS:
            raise ValueError("bad_kind")
        row.kind = kind
    if amount is not None:
        row.amount = clean_money_amount(amount)
    if note is not None:
        row.note = str(note).strip()[:200]
    if category is not None or kind is not None:
        wanted = category if category is not None else row.category
        row.category = wanted if money_category_kind(wanted) == row.kind \
            else detect_money_category(row.note, row.kind)
    if day is not None:
        row.day = day
    if account_id is not ...:
        if account_id is not None:
            _own_account(s, ws, account_id)
        row.account_id = account_id
    s.commit()
    return _money_dict(row)


def restore_money(s: Session, ws: int, data: dict) -> dict:
    """Undo a delete: the same entry, back on the day it was."""
    account_id = data.get("account_id")
    if account_id is not None:
        account = s.get(MoneyAccount, account_id)
        if account is None or account.workspace_id != ws or account.archived_at:
            account_id = None
    return add_money(s, ws, data["kind"], data["amount"], data["category"],
                     note=data.get("note", ""), source=data.get("source", "manual"),
                     day=date.fromisoformat(data["day"]), account_id=account_id)


def money_budgets(s: Session, ws: int) -> dict[str, int]:
    limits = {c[0]: c[3] for c in MONEY_CATEGORIES if c[4] == "expense"}
    for row in s.scalars(select(MoneyBudget).where(MoneyBudget.workspace_id == ws)).all():
        if row.category in limits:
            limits[row.category] = int(row.monthly_limit)
    return limits


def set_money_budget(s: Session, ws: int, category: str, limit) -> int:
    if money_category_kind(category) != "expense":
        raise ValueError("bad_category")
    try:
        limit = max(0, min(int(limit), MONEY_MAX_AMOUNT))
    except (TypeError, ValueError):
        raise ValueError("bad_amount")
    row = s.scalar(select(MoneyBudget).where(MoneyBudget.workspace_id == ws,
                                             MoneyBudget.category == category))
    if row is None:
        s.add(MoneyBudget(workspace_id=ws, category=category, monthly_limit=limit))
    else:
        row.monthly_limit = limit
    s.commit()
    return limit


def _month_bounds(month: date) -> tuple[date, date]:
    first = month.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    return first, nxt - timedelta(days=1)


def money_overview(s: Session, ws: int, *, month: date | None = None,
                   tz: ZoneInfo | None = None, limit: int = 100) -> dict:
    """One month of money: the totals, each category against its limit, and
    the entries themselves, newest first.

    `balance` is everything ever recorded, in minus out; `income` and
    `expense` are the month's own.
    """
    tz = tz or _habit_tz(s, ws)
    today = today_local(tz)
    first, last = _month_bounds(month or today)
    rows = s.execute(select(MoneyEntry.kind, MoneyEntry.category,
                            func.sum(MoneyEntry.amount)).where(
        MoneyEntry.workspace_id == ws, MoneyEntry.day >= first,
        MoneyEntry.day <= last).group_by(MoneyEntry.kind, MoneyEntry.category)).all()
    spent: dict[str, int] = defaultdict(int)
    income = expense = 0
    for kind, category, total in rows:
        total = int(total or 0)
        if kind == "income":
            income += total
        else:
            expense += total
            spent[category] += total
    balance_rows = dict(s.execute(select(MoneyEntry.kind, func.sum(MoneyEntry.amount))
                                  .where(MoneyEntry.workspace_id == ws)
                                  .group_by(MoneyEntry.kind)).all())
    # With accounts, what they held when added is part of what one has.
    opening = int(s.scalar(select(func.coalesce(func.sum(MoneyAccount.opening), 0)).where(
        MoneyAccount.workspace_id == ws, MoneyAccount.archived_at.is_(None))) or 0)
    balance = int(balance_rows.get("income") or 0) - int(balance_rows.get("expense") or 0) + opening
    limits = money_budgets(s, ws)
    categories = []
    for cid, icon, colour, _default, kind in MONEY_CATEGORIES:
        if kind != "expense":
            continue
        cap = limits.get(cid, 0)
        used = spent.get(cid, 0)
        categories.append({"id": cid, "icon": icon, "color": colour,
                           "spent": used, "limit": cap,
                           "percent": min(100, round(used / cap * 100)) if cap else None,
                           "over": bool(cap) and used > cap})
    entries = s.scalars(select(MoneyEntry).where(
        MoneyEntry.workspace_id == ws, MoneyEntry.day >= first,
        MoneyEntry.day <= last)
        .order_by(MoneyEntry.day.desc(), MoneyEntry.id.desc()).limit(limit)).all()
    count = s.scalar(select(func.count(MoneyEntry.id)).where(
        MoneyEntry.workspace_id == ws, MoneyEntry.day >= first,
        MoneyEntry.day <= last)) or 0
    return {"month": first.strftime("%Y-%m"), "year": first.year,
            "month_no": first.month, "is_current": first <= today <= last,
            "income": income, "expense": expense, "balance": balance, "categories": categories,
            "entries": [_money_dict(r) for r in entries], "count": int(count),
            "category_ids": MONEY_CATEGORY_IDS,
            "kinds": {c[0]: c[4] for c in MONEY_CATEGORIES},
            "icons": {c[0]: c[1] for c in MONEY_CATEGORIES},
            "colors": {c[0]: c[2] for c in MONEY_CATEGORIES},
            "debts": debts_overview(s, ws, tz=tz),
            "wallet": wallet_overview(s, ws, today=today, balance=balance)}


# ---------------------------------------------------------------------------
# Accounts, transfers and repeating payments
#
# Accounts say where the money is; transfers move it between them without
# being income or spending; a repeating payment becomes an ordinary expense
# the moment it is marked paid. Free has one account, Pro a few with
# transfers and three repeating payments, Max all of it and the year view.
# ---------------------------------------------------------------------------

MONEY_ACCOUNT_KINDS = ["cash", "card", "bank", "crypto", "invest", "business", "other"]
SUB_PERIODS = ["weekly", "monthly", "yearly"]
#: How many of a period's payments fall in a month, for the "per month" sum.
_PER_MONTH = {"weekly": 52 / 12, "monthly": 1, "yearly": 1 / 12}


def _own_account(s: Session, ws: int, account_id: int, *, archived: bool = False) -> MoneyAccount:
    row = s.get(MoneyAccount, int(account_id))
    if row is None or row.workspace_id != ws or (row.archived_at is not None and not archived):
        raise ValueError("bad_account")
    return row


def _clean_opening(value) -> int:
    try:
        value = int(round(float(value or 0)))
    except (TypeError, ValueError):
        raise ValueError("bad_amount")
    if abs(value) > MONEY_MAX_AMOUNT:
        raise ValueError("bad_amount")
    return value


def _next_due(day: date, period: str) -> date:
    if period == "weekly":
        return day + timedelta(days=7)
    if period == "yearly":
        try:
            return day.replace(year=day.year + 1)
        except ValueError:                      # 29 February
            return day.replace(year=day.year + 1, day=28)
    month = day.month % 12 + 1
    year = day.year + (day.month == 12)
    last = _month_bounds(date(year, month, 1))[1].day
    return date(year, month, min(day.day, last))


def _prev_due(day: date, period: str) -> date:
    """One period before `day` — the start of the period it closes."""
    if period == "weekly":
        return day - timedelta(days=7)
    if period == "yearly":
        try:
            return day.replace(year=day.year - 1)
        except ValueError:                      # 29 February
            return day.replace(year=day.year - 1, day=28)
    month = (day.month - 2) % 12 + 1
    year = day.year - (day.month == 1)
    last = _month_bounds(date(year, month, 1))[1].day
    return date(year, month, min(day.day, last))


def money_access(s: Session, ws: int) -> dict:
    """What this workspace's plan opens in Money, for the screen to draw
    locks rather than discover them by being refused."""
    owner = workspace_owner(s, ws)
    tier = plans.tier_of(s, owner)
    return {"tier": tier,
            "accounts": plans.LIMITS["money_accounts"][tier] if plans.ENABLED else None,
            "subs": plans.LIMITS["money_subs"][tier] if plans.ENABLED else None,
            "transfers": not plans.ENABLED or tier in plans.FEATURES["money_transfers"],
            "year": not plans.ENABLED or tier in plans.FEATURES["money_year"]}


def wallet_overview(s: Session, ws: int, *, today: date | None = None,
                    balance: int | None = None) -> dict:
    """Every open account with its balance, the repeating payments, and the
    latest transfers. `unassigned` is what entries without an account (or
    on a removed one) add up to, so the accounts and it sum to the total."""
    today = today or today_local(_habit_tz(s, ws))
    accounts = s.scalars(select(MoneyAccount).where(
        MoneyAccount.workspace_id == ws, MoneyAccount.archived_at.is_(None))
        .order_by(MoneyAccount.position, MoneyAccount.id)).all()
    ids = [a.id for a in accounts]
    flow: dict[int, int] = defaultdict(int)
    if ids:
        for account_id, kind, total in s.execute(select(
                MoneyEntry.account_id, MoneyEntry.kind, func.sum(MoneyEntry.amount)).where(
                MoneyEntry.workspace_id == ws, MoneyEntry.account_id.in_(ids))
                .group_by(MoneyEntry.account_id, MoneyEntry.kind)).all():
            flow[account_id] += int(total or 0) * (1 if kind == "income" else -1)
        for account_id, total in s.execute(select(
                MoneyTransfer.to_account_id, func.sum(MoneyTransfer.amount)).where(
                MoneyTransfer.workspace_id == ws).group_by(MoneyTransfer.to_account_id)).all():
            flow[account_id] += int(total or 0)
        for account_id, total in s.execute(select(
                MoneyTransfer.from_account_id, func.sum(MoneyTransfer.amount)).where(
                MoneyTransfer.workspace_id == ws).group_by(MoneyTransfer.from_account_id)).all():
            flow[account_id] -= int(total or 0)
    rows = [{"id": a.id, "name": a.name, "kind": a.kind, "opening": int(a.opening or 0),
             "balance": int(a.opening or 0) + flow.get(a.id, 0)} for a in accounts]
    in_accounts = sum(r["balance"] for r in rows)
    if balance is None:
        totals = dict(s.execute(select(MoneyEntry.kind, func.sum(MoneyEntry.amount))
                                .where(MoneyEntry.workspace_id == ws)
                                .group_by(MoneyEntry.kind)).all())
        balance = (int(totals.get("income") or 0) - int(totals.get("expense") or 0)
                   + sum(r["opening"] for r in rows))
    names = {a.id: a.name for a in s.scalars(select(MoneyAccount).where(
        MoneyAccount.workspace_id == ws)).all()}
    subs = s.scalars(select(MoneySubscription).where(
        MoneySubscription.workspace_id == ws, MoneySubscription.archived_at.is_(None))
        .order_by(MoneySubscription.next_due, MoneySubscription.id)).all()
    transfers = s.scalars(select(MoneyTransfer).where(MoneyTransfer.workspace_id == ws)
                          .order_by(MoneyTransfer.day.desc(), MoneyTransfer.id.desc()).limit(10)).all()
    # What each repeating payment last paid, and how much of this month's
    # plan is already paid (audit F04, F17) — read off the entries it made.
    last_paid: dict[int, date] = {}
    paid_month = 0
    sub_ids = [x.id for x in subs]
    if sub_ids:
        first, last = _month_bounds(today)
        for sid, day, amount in s.execute(select(
                MoneyEntry.subscription_id, MoneyEntry.day, MoneyEntry.amount).where(
                MoneyEntry.workspace_id == ws, MoneyEntry.subscription_id.in_(sub_ids))).all():
            if sid not in last_paid or day > last_paid[sid]:
                last_paid[sid] = day
            if first <= day <= last:
                paid_month += int(amount)
    plan_month = round(sum(int(x.amount) * _PER_MONTH.get(x.period, 1) for x in subs))
    return {
        "accounts": rows, "total": balance, "unassigned": balance - in_accounts,
        "subscriptions": [{"id": x.id, "name": x.name, "amount": int(x.amount),
                           "category": x.category, "account_id": x.account_id,
                           "account_name": names.get(x.account_id) if x.account_id else None,
                           "period": x.period, "next_due": x.next_due.isoformat(),
                           "days_left": (x.next_due - today).days,
                           "last_paid": last_paid[x.id].isoformat() if x.id in last_paid else None,
                           # Paid for the due date just covered: the next one is
                           # still ahead, and the last payment falls after the
                           # due date before the covered one (an early payment
                           # counts; one from two periods ago does not).
                           "paid_now": x.id in last_paid and x.next_due > today
                                       and last_paid[x.id] > _prev_due(
                                           _prev_due(x.next_due, x.period), x.period)}
                          for x in subs],
        "subs_monthly": plan_month,
        "subs_paid_month": paid_month,
        "subs_left_month": max(0, plan_month - paid_month),
        "transfers": [{"id": x.id, "from": x.from_account_id, "to": x.to_account_id,
                       "from_name": names.get(x.from_account_id, "?"),
                       "to_name": names.get(x.to_account_id, "?"),
                       "amount": int(x.amount), "note": x.note or "", "day": x.day.isoformat()}
                      for x in transfers],
        "kinds": MONEY_ACCOUNT_KINDS, "periods": SUB_PERIODS,
        "access": money_access(s, ws),
    }


def add_account(s: Session, ws: int, name: str, kind: str = "cash", opening=0) -> MoneyAccount:
    name = " ".join(str(name or "").split())[:60]
    if not name:
        raise ValueError("bad_name")
    plans.require_in_workspace(s, ws, "money_accounts", s.scalar(
        select(func.count(MoneyAccount.id)).where(MoneyAccount.workspace_id == ws,
                                                  MoneyAccount.archived_at.is_(None))) or 0)
    last = s.scalar(select(func.max(MoneyAccount.position)).where(
        MoneyAccount.workspace_id == ws)) or 0
    row = MoneyAccount(workspace_id=ws, name=name,
                       kind=kind if kind in MONEY_ACCOUNT_KINDS else "other",
                       opening=_clean_opening(opening), position=last + 1)
    s.add(row)
    s.commit()
    return row


def update_account(s: Session, ws: int, account_id: int, *, name=None, kind=None,
                   opening=None, archived: bool | None = None) -> MoneyAccount:
    row = _own_account(s, ws, account_id, archived=True)
    if name is not None:
        name = " ".join(str(name).split())[:60]
        if not name:
            raise ValueError("bad_name")
        row.name = name
    if kind is not None:
        row.kind = kind if kind in MONEY_ACCOUNT_KINDS else "other"
    if opening is not None:
        row.opening = _clean_opening(opening)
    if archived is not None:
        if not archived and row.archived_at is not None:
            plans.require_in_workspace(s, ws, "money_accounts", s.scalar(
                select(func.count(MoneyAccount.id)).where(
                    MoneyAccount.workspace_id == ws, MoneyAccount.archived_at.is_(None))) or 0)
        row.archived_at = utcnow() if archived else None
    s.commit()
    return row


def add_transfer(s: Session, ws: int, from_id: int, to_id: int, amount, *,
                 note: str = "", day: date | None = None,
                 tz: ZoneInfo | None = None) -> MoneyTransfer:
    plans.require_feature(s, workspace_owner(s, ws), "money_transfers")
    if int(from_id) == int(to_id):
        raise ValueError("same_account")
    source, target = _own_account(s, ws, from_id), _own_account(s, ws, to_id)
    row = MoneyTransfer(workspace_id=ws, from_account_id=source.id, to_account_id=target.id,
                        amount=clean_money_amount(amount), note=str(note or "").strip()[:200],
                        day=day or today_local(tz or _habit_tz(s, ws)))
    s.add(row)
    s.commit()
    return row


def delete_transfer(s: Session, ws: int, transfer_id: int) -> None:
    row = s.get(MoneyTransfer, transfer_id)
    if row is None or row.workspace_id != ws:
        raise NotFound("transfer")
    s.delete(row)
    s.commit()


def _sub_fields(s: Session, ws: int, fields: dict) -> dict:
    out: dict = {}
    if "name" in fields:
        name = " ".join(str(fields["name"] or "").split())[:80]
        if not name:
            raise ValueError("bad_name")
        out["name"] = name
    if "amount" in fields:
        out["amount"] = clean_money_amount(fields["amount"])
    if "category" in fields:
        out["category"] = (fields["category"] if money_category_kind(fields["category"]) == "expense"
                           else "other")
    if "period" in fields:
        if fields["period"] not in SUB_PERIODS:
            raise ValueError("bad_period")
        out["period"] = fields["period"]
    if "next_due" in fields:
        if not isinstance(fields["next_due"], date):
            raise ValueError("bad_date")
        out["next_due"] = fields["next_due"]
    if "account_id" in fields:
        out["account_id"] = (_own_account(s, ws, fields["account_id"]).id
                             if fields["account_id"] is not None else None)
    return out


def add_subscription(s: Session, ws: int, **fields) -> MoneySubscription:
    plans.require_in_workspace(s, ws, "money_subs", s.scalar(
        select(func.count(MoneySubscription.id)).where(
            MoneySubscription.workspace_id == ws,
            MoneySubscription.archived_at.is_(None))) or 0)
    values = _sub_fields(s, ws, fields)
    if "name" not in values or "amount" not in values:
        raise ValueError("bad_name" if "name" not in values else "bad_amount")
    values.setdefault("next_due", today_local(_habit_tz(s, ws)))
    row = MoneySubscription(workspace_id=ws, **values)
    s.add(row)
    s.commit()
    return row


def _own_sub(s: Session, ws: int, sub_id: int) -> MoneySubscription:
    row = s.get(MoneySubscription, sub_id)
    if row is None or row.workspace_id != ws or row.archived_at is not None:
        raise NotFound("subscription")
    return row


def update_subscription(s: Session, ws: int, sub_id: int, **fields) -> MoneySubscription:
    row = _own_sub(s, ws, sub_id)
    archived = fields.pop("archived", None)
    for key, value in _sub_fields(s, ws, fields).items():
        setattr(row, key, value)
    if archived:
        row.archived_at = utcnow()
    s.commit()
    return row


def pay_subscription(s: Session, ws: int, sub_id: int, *, tz: ZoneInfo | None = None) -> dict:
    """Record this period's payment as an expense and move to the next."""
    row = _own_sub(s, ws, sub_id)
    account_id = row.account_id
    if account_id is not None:
        account = s.get(MoneyAccount, account_id)
        if account is None or account.archived_at is not None:
            account_id = None
    entry = add_money(s, ws, "expense", int(row.amount), row.category, note=row.name,
                      tz=tz, account_id=account_id)
    paid = s.get(MoneyEntry, entry["id"])
    if paid is not None:
        paid.subscription_id = row.id
    row.next_due = _next_due(row.next_due, row.period)
    s.commit()
    return {"entry": entry, "next_due": row.next_due.isoformat()}


def money_year(s: Session, ws: int, year: int) -> dict:
    """A year of money, month by month, and where the spending went."""
    plans.require_feature(s, workspace_owner(s, ws), "money_year")
    first, last = date(year, 1, 1), date(year, 12, 31)
    months = [{"month": m, "income": 0, "expense": 0} for m in range(1, 13)]
    by_cat: dict[str, int] = defaultdict(int)
    for day, kind, category, amount in s.execute(select(
            MoneyEntry.day, MoneyEntry.kind, MoneyEntry.category, MoneyEntry.amount).where(
            MoneyEntry.workspace_id == ws, MoneyEntry.day >= first,
            MoneyEntry.day <= last)).all():
        months[day.month - 1][kind] += int(amount)
        if kind == "expense":
            by_cat[category] += int(amount)
    income = sum(m["income"] for m in months)
    expense = sum(m["expense"] for m in months)
    return {"year": year, "months": months, "income": income, "expense": expense,
            "net": income - expense,
            "categories": sorted(({"id": k, "spent": v} for k, v in by_cat.items()),
                                 key=lambda c: -c["spent"])}


def _export_wallet(s: Session, ws: int) -> dict:
    return {
        "accounts": [{"id": a.id, "name": a.name, "kind": a.kind, "opening": int(a.opening or 0),
                      "archived": a.archived_at is not None}
                     for a in s.scalars(select(MoneyAccount).where(
                         MoneyAccount.workspace_id == ws).order_by(MoneyAccount.id)).all()],
        "transfers": [{"from": x.from_account_id, "to": x.to_account_id, "amount": int(x.amount),
                       "note": x.note, "day": x.day.isoformat()}
                      for x in s.scalars(select(MoneyTransfer).where(
                          MoneyTransfer.workspace_id == ws).order_by(MoneyTransfer.id)).all()],
        "subscriptions": [{"name": x.name, "amount": int(x.amount), "category": x.category,
                           "account_id": x.account_id, "period": x.period,
                           "next_due": x.next_due.isoformat(),
                           "archived": x.archived_at is not None}
                          for x in s.scalars(select(MoneySubscription).where(
                              MoneySubscription.workspace_id == ws)
                              .order_by(MoneySubscription.id)).all()],
    }


# ---------------------------------------------------------------------------
# Debts — who owes whom. Separate from the balance; settled ones stay as history.
# ---------------------------------------------------------------------------

DEBT_DIRECTIONS = ("lent", "borrowed")


def _debt_payments(s: Session, debt_ids: list[int]) -> dict[int, list[DebtPayment]]:
    out: dict[int, list[DebtPayment]] = defaultdict(list)
    if debt_ids:
        for p in s.scalars(select(DebtPayment).where(DebtPayment.debt_id.in_(debt_ids))
                           .order_by(DebtPayment.paid_at, DebtPayment.id)).all():
            out[p.debt_id].append(p)
    return out


def _iso_utc(moment: datetime | None) -> str | None:
    return moment.replace(tzinfo=_utc.utc).isoformat() if moment else None


def _debt_dict(row: Debt, today: date | None = None,
               payments: list[DebtPayment] | None = None) -> dict:
    """`amount` is what is still owed — the figure every screen shows.
    `original` is the sum as it was lent; `payments` is the partial history."""
    payments = payments or []
    paid = sum(int(p.amount) for p in payments)
    return {"id": row.id, "person": row.person,
            "amount": max(0, int(row.amount) - paid),
            "original": int(row.amount), "paid": paid,
            "payments": [{"id": p.id, "amount": int(p.amount), "paid_at": _iso_utc(p.paid_at)}
                         for p in payments],
            "direction": row.direction, "note": row.note or "",
            "due": row.due.isoformat() if row.due else None,
            "overdue": bool(today and row.due and not row.settled_at and row.due < today),
            "settled": row.settled_at is not None,
            "archived": row.archived_at is not None,
            "created_at": _iso_utc(row.created_at)}


def _debt_out(s: Session, row: Debt, today: date | None = None) -> dict:
    return _debt_dict(row, today, _debt_payments(s, [row.id])[row.id])


def add_debt(s: Session, ws: int, person: str, amount, direction: str, *,
             note: str = "", due: date | None = None) -> dict:
    person = " ".join(str(person or "").split())[:80]
    if not person:
        raise ValueError("bad_person")
    if direction not in DEBT_DIRECTIONS:
        raise ValueError("bad_direction")
    plans.require_in_workspace(s, ws, "open_debts", s.scalar(
        select(func.count(Debt.id)).where(Debt.workspace_id == ws, Debt.settled_at.is_(None),
                                          Debt.archived_at.is_(None))) or 0)
    row = Debt(workspace_id=ws, person=person, amount=clean_money_amount(amount),
               direction=direction, note=str(note or "").strip()[:200], due=due)
    s.add(row)
    s.commit()
    return _debt_dict(row)


def _own_debt(s: Session, ws: int, debt_id: int, *, archived: bool = False) -> Debt:
    row = s.get(Debt, debt_id)
    if row is None or row.workspace_id != ws or (row.archived_at is not None) != archived:
        raise NotFound("debt")
    return row


def settle_debt(s: Session, ws: int, debt_id: int, settled: bool = True,
                paid: int | None = None) -> dict:
    """Mark returned, reopen, or record a partial return.

    A partial return is its own row: the original sum never changes. Paying
    more than is still owed is refused rather than silently closing the debt —
    a typo of 400 000 for 40 000 must not look like a settled loan. Paying
    exactly the rest records that payment and closes the debt.
    """
    row = _own_debt(s, ws, debt_id)
    payments = _debt_payments(s, [row.id])[row.id]
    remaining = int(row.amount) - sum(int(p.amount) for p in payments)
    if settled and paid is not None:
        if row.settled_at is not None:
            raise ValueError("debt_closed")
        paid = clean_money_amount(paid)
        if paid > remaining:
            raise ValueError("paid_exceeds_remaining")
        s.add(DebtPayment(workspace_id=ws, debt_id=row.id, amount=paid))
        if paid == remaining:
            row.settled_at = utcnow()
    elif settled:
        row.settled_at = row.settled_at or utcnow()
    else:
        row.settled_at = None
        # Reopening a debt closed by its last payment undoes that payment —
        # otherwise it would come back open with nothing left to return.
        if payments and remaining <= 0:
            s.delete(payments[-1])
    s.commit()
    return _debt_out(s, row)


def delete_debt(s: Session, ws: int, debt_id: int) -> dict:
    """Archive, not erase: the row and its payments stay until restored or purged."""
    row = _own_debt(s, ws, debt_id)
    row.archived_at = utcnow()
    s.commit()
    return _debt_out(s, row)


def restore_debt(s: Session, ws: int, debt_id: int) -> dict:
    row = _own_debt(s, ws, debt_id, archived=True)
    row.archived_at = None
    s.commit()
    return _debt_out(s, row)


def purge_debt(s: Session, ws: int, debt_id: int) -> None:
    """Permanent removal — only for a debt already archived."""
    row = _own_debt(s, ws, debt_id, archived=True)
    s.execute(DebtPayment.__table__.delete().where(DebtPayment.debt_id == row.id))
    s.delete(row)
    s.commit()


def debts_overview(s: Session, ws: int, *, tz: ZoneInfo | None = None) -> dict:
    """Open debts, biggest first per side, the two totals, and recent history."""
    today = today_local(tz or _habit_tz(s, ws))
    live = (Debt.workspace_id == ws, Debt.archived_at.is_(None))
    rows = s.scalars(select(Debt).where(*live, Debt.settled_at.is_(None))
                     .order_by(Debt.due.is_(None), Debt.due, Debt.id.desc())).all()
    settled = s.scalars(select(Debt).where(*live, Debt.settled_at.is_not(None))
                        .order_by(Debt.settled_at.desc()).limit(20)).all()
    archived = s.scalars(select(Debt).where(Debt.workspace_id == ws, Debt.archived_at.is_not(None))
                         .order_by(Debt.archived_at.desc()).limit(20)).all()
    pays = _debt_payments(s, [r.id for r in (*rows, *settled, *archived)])
    open_ = [_debt_dict(r, today, pays[r.id]) for r in rows]
    return {"open": open_,
            "settled": [_debt_dict(r, None, pays[r.id]) for r in settled],
            "archived": [_debt_dict(r, None, pays[r.id]) for r in archived],
            "owed_to_me": sum(d["amount"] for d in open_ if d["direction"] == "lent"),
            "i_owe": sum(d["amount"] for d in open_ if d["direction"] == "borrowed"),
            "overdue": sum(1 for r in rows if r.due and r.due < today)}


# ---------------------------------------------------------------------------
# Statistics — streaks
# ---------------------------------------------------------------------------

def _prayer_score_for(s: Session, ws: int, day: date) -> float:
    row = s.scalar(select(PrayerDay).where(
        PrayerDay.workspace_id == ws, PrayerDay.day == day))
    return float(row.score) if row else 0.0


def _prayer_percent(s: Session, ws: int, day: date) -> int:
    return round(_prayer_score_for(s, ws, day) / PRAYER_MAX_SCORE * 100)


def _prayer_day_complete(s: Session, ws: int, day: date, gender: str | None) -> bool:
    state = s.scalar(select(PrayerDay).where(
        PrayerDay.workspace_id == ws, PrayerDay.day == day))
    return prayer_is_complete(_day_statuses(s, ws, day), gender,
                              bool(state and state.excused))


def habit_streak(s: Session, ws: int, *, tz: ZoneInfo | None = None) -> int:
    """Consecutive days on which every habit that was owed got done.

    Today may still be incomplete without breaking the streak — the day is not
    over — so counting starts from yesterday when today is unfinished. Days on
    which nothing was owed are skipped rather than counted as failures. Prayer
    has its own streak and is not part of this one.
    """
    tz = tz or _habit_tz(s, ws)
    today = today_local(tz)
    earliest = today - timedelta(days=400)
    habits = _scored_personal(_habits_owed_candidates(s, ws, earliest))
    if not habits:
        return 0
    cal = calendar_for(s, habits, tz)

    done_by_day: dict[date, set[int]] = {}
    for habit_id, day in s.execute(select(HabitLog.habit_id, HabitLog.day).where(
            HabitLog.workspace_id == ws, HabitLog.done.is_(True),
            HabitLog.day >= earliest)).all():
        done_by_day.setdefault(day, set()).add(habit_id)

    first = min((cal.start_of(h) or today) for h in habits)

    def complete(day: date) -> bool | None:
        """True/False, or None when the day had nothing owed."""
        due = {h.id for h in habits if cal.due(h, day)}
        if not due:
            return None
        return due <= done_by_day.get(day, set())

    cursor = today
    if complete(today) is not True:
        cursor = today - timedelta(days=1)

    streak = 0
    for _ in range(400):
        if cursor < first:
            break
        state = complete(cursor)
        if state is False:
            break
        if state is True:
            streak += 1
        cursor -= timedelta(days=1)
    return streak


def prayer_streak(s: Session, ws: int, gender: str | None = None, *,
                  tz: ZoneInfo | None = None) -> int:
    """Consecutive days on which all five prayers were prayed."""
    today = today_local(tz)
    cursor = today
    if not _prayer_day_complete(s, ws, today, gender):
        cursor = today - timedelta(days=1)

    streak = 0
    for _ in range(400):
        if not _prayer_day_complete(s, ws, cursor, gender):
            break
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def prayer_breakdown(s: Session, ws: int, start: date, end: date,
                     gender: str | None = None) -> dict:
    """What the prayer numbers actually consist of, over a range."""
    rows = s.scalars(select(PrayerLog).where(
        PrayerLog.workspace_id == ws,
        PrayerLog.day >= start, PrayerLog.day <= end)).all()

    counts: dict[str, int] = {}
    by_day: dict[date, dict[str, str]] = defaultdict(dict)
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
        by_day[row.day][row.prayer] = row.status
    excused_days = set(s.scalars(select(PrayerDay.day).where(
        PrayerDay.workspace_id == ws, PrayerDay.day >= start,
        PrayerDay.day <= end, PrayerDay.excused.is_(True))).all())

    logged = sum(counts.values())
    performed = sum(counts.get(k, 0) for k in PRAYER_PERFORMED)

    days = (end - start).days + 1
    full_days = sum(
        1 for offset in range(max(days, 0))
        if prayer_is_complete(by_day.get(start + timedelta(days=offset), {}),
                              gender, (start + timedelta(days=offset)) in excused_days))

    return {
        "counts": counts,
        "full_days": full_days,
        "days": max(days, 0),
        "jamaat": counts.get("jamaat", 0),
        "on_time": counts.get("on_time", 0),
        "qaza": counts.get("qaza", 0),
        "missed": counts.get("missed", 0),
        "on_time_percent": (round((counts.get("on_time", 0) + counts.get("jamaat", 0))
                                  / performed * 100) if performed else 0),
        "logged_percent": round(logged / (days * 5) * 100) if days > 0 else 0,
        "consistency": round(full_days / days * 100) if days > 0 else 0,
    }


# ---------------------------------------------------------------------------
# The one overall number
# ---------------------------------------------------------------------------
#
# Every surface — the bot's Home, the Mini App's Home, the Statistics page,
# the explanation sheet, the chart, the CSV, the reports and the stored daily
# score — reads `day_score`. There is one formula, and it is this:
#
#   components  tasks (priority-weighted), habits (tier-weighted), the week's
#               goals, prayer — each a percentage, or absent when nothing of
#               that kind was owed that day;
#   total       the weighted mean of the components that are present, with the
#               weights renormalised over them.
#
# Shared work is a component of its own (v9.1, formula 3): the 20% the week
# goal used to carry is the team result — the share of today's shared tasks
# and habits this person finished. Tasks and habits are the personal ones
# only, so nothing is counted twice. No shared work today: the component is
# absent and its 20% is spread over the rest, like any empty part. Rituals a
# team mirrors from each member's personal habits are counted once, in the
# personal habit. The week goal is still set on Tasks; it is no longer scored.

#: Shown when the day has nothing measurable in it yet — as a number. The
#: screens show "—" instead, because `measured` says there was nothing to show.
EMPTY_OVERALL = 0

#: The version of the rules below. Stored on every daily snapshot, so a future
#: change can tell which rows were scored how.
SCORE_FORMULA = 3

#: What each part of a day is worth.
OVERALL_WEIGHTS = {"tasks": 0.40, "habits": 0.25, "team": 0.20, "prayer": 0.15}

#: What a task is worth inside the tasks component, by its own priority.
TASK_PRIORITY_WEIGHTS = {"high": 3, "medium": 2, "low": 1}


def today_task_progress(s: Session, ws: int, day: date | None = None, *,
                        tz: ZoneInfo | None = None,
                        include_team: bool = True) -> tuple[int, int]:
    """(completed, total) tasks that belong to this day — plain counts."""
    day = day or today_local(tz)
    total = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.deadline == day)) or 0
    done = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.deadline == day, Task.status == "done")) or 0
    shared = due_team_tasks(s, ws, day) if include_team else []
    return done + sum(1 for _, ok in shared if ok), total + len(shared)


def tasks_completed_on(s: Session, ws: int, day: date | None = None, *,
                       tz: ZoneInfo | None = None) -> int:
    """Own tasks finished on this local day, whatever their deadline — late,
    undated or due today. "What I got done", kept apart from "what I
    promised for today" (`today_task_progress`)."""
    tz = tz or _habit_tz(s, ws)
    day = day or today_local(tz)
    start, end = utc_window(day, day, tz)
    return int(s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.archived_at.is_(None), Task.status == "done",
        Task.completed_at >= start, Task.completed_at < end)) or 0)


def today_task_score(s: Session, ws: int, day: date | None = None, *,
                     tz: ZoneInfo | None = None,
                     include_team: bool = True) -> tuple[int, int]:
    """(earned, available) task points for the day, weighted by priority.

    Each task is weighed by the priority it had when the day began, when it
    was changed on the day itself.
    """
    day = day or today_local(tz)
    rows = [(_task_weight_priority(task, day), task.status == "done")
            for task in s.scalars(select(Task).where(
                Task.workspace_id == ws, Task.archived_at.is_(None),
                Task.deadline == day)).all()]
    if include_team:
        rows += due_team_tasks(s, ws, day)
    earned = available = 0
    for priority, done in rows:
        weight = TASK_PRIORITY_WEIGHTS.get(priority, 2)
        available += weight
        if done:
            earned += weight
    return earned, available


def focus_progress(s: Session, ws: int, day: date | None = None, *,
                   tz: ZoneInfo | None = None) -> tuple[int, int]:
    """(done, total) of this week's goals that are scored as goals.

    A goal delivered by a task is scored through that task, so it is left out
    here — one piece of work, one place in the number.
    """
    rows = [r for r in list_focus(s, ws, day, tz=tz) if not r["task_id"]]
    return sum(1 for r in rows if r["done"]), len(rows)


def overall_components(s: Session, ws: int, day: date | None = None, *,
                       tz: ZoneInfo | None = None,
                       include_team: bool = True) -> dict:
    """Each component's percentage, or None when it had no denominator.

    A category with nothing in it is *absent*, not zero. Counting an empty
    category as 0% would punish a user for a day with no tasks.
    """
    day = day or today_local(tz)

    habits_done, habits_total = habit_progress(s, ws, day, include_team=False)
    tasks_earned, tasks_available = today_task_score(
        s, ws, day, tz=tz, include_team=False)
    habits_scored = habit_percent(s, ws, day, include_team=False)
    shared = (due_team_habits(s, ws, day) + due_team_tasks(s, ws, day)
              if include_team else [])
    prayer_row = s.scalar(select(PrayerDay).where(
        PrayerDay.workspace_id == ws, PrayerDay.day == day))

    return {
        "tasks": (round(tasks_earned / tasks_available * 100)
                  if tasks_available else None),
        "habits": habits_scored if habits_total else None,
        "team": (round(sum(1 for _, ok in shared if ok) / len(shared) * 100)
                 if shared else None),
        # Prayer's denominator is the five daily prayers, on every day the
        # prayer module was on — not only on days something was logged.
        "prayer": (round(float(prayer_row.score if prayer_row else 0.0)
                         / PRAYER_MAX_SCORE * 100)
                   if prayer_owed(s, ws, day) else None),
    }


def weighted_overall(components: dict) -> int:
    """One number from the parts, each carrying its own weight."""
    present = {k: v for k, v in components.items()
               if v is not None and k in OVERALL_WEIGHTS}
    if not present:
        return EMPTY_OVERALL
    total_weight = sum(OVERALL_WEIGHTS[k] for k in present)
    if total_weight <= 0:
        return EMPTY_OVERALL
    return round(sum(value * OVERALL_WEIGHTS[key]
                     for key, value in present.items()) / total_weight)


def applied_weights(components: dict) -> dict:
    present = {k: v for k, v in OVERALL_WEIGHTS.items() if components.get(k) is not None}
    total = sum(present.values())
    return {k: round(v / total * 100, 1) for k, v in present.items()} if total else {}


def _is_measured(components: dict) -> bool:
    return any(v is not None for k, v in components.items() if k in OVERALL_WEIGHTS)


def _closed_score(s: Session, ws: int, day: date) -> DailyScore | None:
    """The day's closed snapshot, if the day is over and was closed."""
    owner = workspace_owner(s, ws)
    if owner is None:
        return None
    return s.scalar(select(DailyScore).where(
        DailyScore.user_id == owner, DailyScore.day == day,
        DailyScore.closed.is_(True)))


def _components_of(row: DailyScore) -> dict:
    def part(value):
        return None if value is None or value < 0 else int(value)
    return {"tasks": part(row.task_score), "habits": part(row.habit_score),
            "team": part(row.team_score), "prayer": part(row.prayer_score)}


def day_score(s: Session, ws: int, day: date | None = None, *,
              tz: ZoneInfo | None = None, live: bool = False) -> dict:
    """The canonical score of one day.

    A closed day comes from its snapshot; today, and any day that was never
    closed, is computed from the rows. `personal` and `team` are the same
    formula over each half alone, for the screens that explain where the
    number came from — the headline is `value`, over both.
    """
    tz = tz or _habit_tz(s, ws)
    day = day or today_local(tz)
    today = today_local(tz)

    snap = None if (live or day >= today) else _closed_score(s, ws, day)
    if snap is not None:
        components = _components_of(snap)
        measured = snap.measured if snap.measured is not None else True
        value = int(snap.total_score or 0)
        return {"value": value, "personal": None, "team": None,
                "band": score_band(value if measured else None),
                "components": components, "measured": bool(measured),
                "team_items": 0, "team_done": 0, "closed": True}

    components = overall_components(s, ws, day, tz=tz, include_team=True)
    personal_parts = overall_components(s, ws, day, tz=tz, include_team=False)
    measured = _is_measured(components)
    value = weighted_overall(components)

    shared = (due_team_habits(s, ws, day) + due_team_tasks(s, ws, day))
    team = (round(sum(1 for _, ok in shared if ok) / len(shared) * 100)
            if shared else None)
    return {"value": value,
            "personal": (weighted_overall(personal_parts)
                         if _is_measured(personal_parts) else None),
            "team": team,
            "band": score_band(value if measured else None),
            "components": components,
            "measured": measured,
            "team_items": len(shared),
            "team_done": sum(1 for _, ok in shared if ok),
            "closed": False}


def overall_percent(s: Session, ws: int, day: date | None = None) -> int:
    """The score for a day — the one number."""
    return day_score(s, ws, day)["value"]


def overall_state(s: Session, ws: int, day: date | None = None) -> dict:
    """Today's number, and how it compares with yesterday.

    The two days are only comparable when both had something to measure;
    otherwise the trend is `flat` and there is no "yesterday" to show.
    """
    tz = _habit_tz(s, ws)
    day = day or today_local(tz)
    now = day_score(s, ws, day, tz=tz)
    before = day_score(s, ws, day - timedelta(days=1), tz=tz)

    previous = before["value"] if before["measured"] else None
    if previous is None or not now["measured"] or now["value"] == previous:
        trend = "flat"
    else:
        trend = "up" if now["value"] > previous else "down"

    return {"value": now["value"], "trend": trend, "yesterday": previous,
            "measured": now["measured"],
            "personal": now["personal"], "team": now["team"],
            "band": now["band"],
            "components": now["components"],
            "team_items": now["team_items"], "team_done": now["team_done"]}


def _task_percent(s: Session, ws: int, day: date) -> int:
    earned, available = today_task_score(s, ws, day)
    return round(earned / available * 100) if available else 0


def _focus_percent(s: Session, ws: int, day: date) -> int:
    done, total = focus_progress(s, ws, day)
    return round(done / total * 100) if total else 0


def _overall_percent_for(s: Session, ws: int, day: date) -> int:
    return day_score(s, ws, day)["value"]


#: The four series every chart and average is built from, plus the headline.
SERIES_KEYS = ("habits", "prayer", "tasks", "team", "overall")


def _closed_scores(s: Session, ws: int, first: date, last: date) -> dict[date, DailyScore]:
    """Every closed snapshot in a range, by day — one query."""
    owner = workspace_owner(s, ws)
    if owner is None:
        return {}
    return {row.day: row for row in s.scalars(select(DailyScore).where(
        DailyScore.user_id == owner, DailyScore.day >= first,
        DailyScore.day <= last, DailyScore.closed.is_(True))).all()}


def _first_owed_day(s: Session, ws: int) -> date | None:
    """The earliest day anything in this workspace could have been owed.

    Every part of the day's score has a start: a habit its first day, a task
    its deadline, a week's goal its Monday, a shared item the day its owner
    joined the team. Before the earliest of them every component is absent,
    so those days are unmeasured by definition. None when no bound is known.
    """
    def compute():
        tz = _habit_tz(s, ws)
        starts: list[date] = []
        cal = DueCalendar(tz)
        for habit in _workspace_habits(s, ws):
            start = cal.start_of(habit)
            if start is None:
                return None
            starts.append(start)
        deadline = s.scalar(select(func.min(Task.deadline)).where(
            Task.workspace_id == ws, Task.archived_at.is_(None)))
        if deadline is not None:
            starts.append(deadline)
        focus = s.scalar(select(func.min(WeeklyFocus.week_start)).where(
            WeeklyFocus.workspace_id == ws))
        if focus is not None:
            starts.append(focus)
        owner = workspace_owner(s, ws)
        if owner is not None:
            for joined in s.scalars(select(TeamMember.joined_at).where(
                    TeamMember.user_id == owner)).all():
                joined_day = local_date_of(joined, tz)
                if joined_day is None:
                    return None
                starts.append(joined_day)
        return min(starts) if starts else date.max
    return _memoized(s, ("first_owed", ws), compute)


def _day_point(s: Session, ws: int, day: date,
               snapshots: dict[date, DailyScore] | None = None,
               today: date | None = None) -> dict:
    """One day as the chart draws it: each series, and whether it was measured.

    A closed day is read from its snapshot, so the chart of last month cannot
    move because of something edited this week.
    """
    today = today or today_local(_habit_tz(s, ws))
    snap = (snapshots or {}).get(day) if day < today else None
    if snap is None and day < today and snapshots is None:
        snap = _closed_score(s, ws, day)
    if snap is None and day < today:
        first = _first_owed_day(s, ws)
        if first is not None and day < first:
            # Before anything in this workspace existed nothing was owed, so
            # the formula would answer "unmeasured" after forty queries.
            return {"habits": 0, "prayer": 0, "tasks": 0, "team": 0,
                    "overall": EMPTY_OVERALL, "measured": False,
                    "present": {k: False for k in OVERALL_COMPONENTS}}
    if snap is not None:
        parts = _components_of(snap)
        measured = snap.measured if snap.measured is not None else True
        return {"habits": parts["habits"] or 0, "prayer": parts["prayer"] or 0,
                "tasks": parts["tasks"] or 0, "team": parts["team"] or 0,
                "overall": int(snap.total_score or 0), "measured": bool(measured),
                "present": {k: v is not None for k, v in parts.items()}}
    score = day_score(s, ws, day, live=True)
    parts = score["components"]
    return {"habits": parts["habits"] or 0, "prayer": parts["prayer"] or 0,
            "tasks": parts["tasks"] or 0, "team": parts["team"] or 0,
            "overall": score["value"], "measured": score["measured"],
            "present": {k: v is not None for k, v in parts.items()}}


def _average_points(points: list[dict]) -> dict:
    """The average of each series over the days it was actually measured.

    A day with nothing owed is not a day at 0%: counting it would drag every
    average towards zero for days the user was never asked to do anything.
    """
    out = {}
    for key in SERIES_KEYS:
        if key == "overall":
            values = [p[key] for p in points if p.get("measured", True)]
        else:
            values = [p[key] for p in points
                      if p.get("present", {}).get(key, p.get("measured", True))]
        out[key] = round(sum(values) / len(values)) if values else 0
    out["measured_days"] = sum(1 for p in points if p.get("measured", True))
    return out


def _range_average(s: Session, ws: int, start: date, end: date) -> dict:
    """Average of each series across an inclusive day range."""
    days = (end - start).days + 1
    if days <= 0:
        return {**{k: 0 for k in SERIES_KEYS}, "measured_days": 0}
    snapshots = _closed_scores(s, ws, start, end)
    today = today_local(_habit_tz(s, ws))
    points = [_day_point(s, ws, start + timedelta(days=offset), snapshots, today)
              for offset in range(days)]
    return _average_points(points)


def _range_percent(s: Session, ws: int, start: date, end: date) -> tuple[int, int]:
    """(habit %, prayer %) across a range. Kept for the reports and the review."""
    avg = _range_average(s, ws, start, end)
    return avg["habits"], avg["prayer"]


#: What each component of the overall number means and where it comes from, so
#: the info panel is generated from the same place the number is.
OVERALL_COMPONENTS = ["tasks", "habits", "team", "prayer"]


def overall_explain(s: Session, ws: int, user: User,
                    day: date | None = None) -> dict:
    """The arithmetic behind the day's percentage, component by component."""
    tz = user_tz(user)
    day = day or today_local(tz)

    score = day_score(s, ws, day, tz=tz)
    tasks_done, tasks_total = today_task_progress(s, ws, day, include_team=False)
    habits_done, habits_total = habit_progress(s, ws, day, include_team=False)
    prayer = prayer_state(s, ws, day, user.gender)
    components = score["components"]
    counted = [k for k in OVERALL_COMPONENTS if components.get(k) is not None]
    live = sum(OVERALL_WEIGHTS[k] for k in counted) or 1

    team_tasks = due_team_tasks(s, ws, day)
    team_habits = due_team_habits(s, ws, day)

    return {
        "day": day.isoformat(),
        "value": score["value"],
        "measured": score["measured"],
        "counted": counted,
        "parts": [
            {"key": "tasks", "percent": components["tasks"],
             "done": tasks_done, "total": tasks_total},
            {"key": "habits", "percent": components["habits"],
             "done": habits_done, "total": habits_total},
            {"key": "team", "percent": components["team"],
             "done": sum(1 for _, ok in team_tasks + team_habits if ok),
             "total": len(team_tasks) + len(team_habits)},
            {"key": "prayer", "percent": components["prayer"],
             "done": prayer["score"], "total": PRAYER_MAX_SCORE},
        ],
        "weights": {k: round(OVERALL_WEIGHTS[k] / live * 100)
                    for k in counted},
        "nominal_weights": {k: round(v * 100)
                            for k, v in OVERALL_WEIGHTS.items()},
        "task_priority_weights": dict(TASK_PRIORITY_WEIGHTS),
        "rule": "weighted_mean_of_available",
        "formula": SCORE_FORMULA,
    }


def stats(s: Session, ws: int, period: str = "week", *,
          gender: str | None = None, tz: ZoneInfo | None = None) -> dict:
    """Series for the charts, plus streaks, averages and what changed.

    `week`  — one point per day for the last 7 days.
    `month` — one point per day for the last 30 days.
    `year`  — one point per month for the last 12 months.

    Averages are over measured days only, and a change against the previous
    period is only given when that period measured something.
    """
    tz = tz or _habit_tz(s, ws)
    today = today_local(tz)
    keys = SERIES_KEYS

    if period == "year":
        months = []
        year, month = today.year, today.month
        for _ in range(12):
            months.append((year, month))
            month -= 1
            if month == 0:
                month, year = 12, year - 1
        window_start = date(months[-1][0], months[-1][1], 1)
        snapshots = _closed_scores(s, ws, window_start - timedelta(days=366), today)
        series = []
        for y, m in reversed(months):
            start = date(y, m, 1)
            end = min(today, date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1))
            points = [_day_point(s, ws, start + timedelta(days=o), snapshots, today)
                      for o in range((end - start).days + 1)]
            avg = _average_points(points)
            series.append({"day": start.isoformat(), "label": start.strftime("%m.%y"),
                           **{k: avg[k] for k in keys},
                           "measured": avg["measured_days"] > 0})
        previous_start = window_start - timedelta(days=365)
        previous_end = window_start - timedelta(days=1)
        averages = _average_points([
            {**p, "present": {k: True for k in keys}} for p in series
            if p["measured"]]) if any(p["measured"] for p in series) else \
            {**{k: 0 for k in keys}, "measured_days": 0}
    else:
        days = 7 if period == "week" else 30
        window_start = today - timedelta(days=days - 1)
        previous_start = window_start - timedelta(days=days)
        previous_end = window_start - timedelta(days=1)
        snapshots = _closed_scores(s, ws, previous_start, today)
        series = []
        for offset in range(days - 1, -1, -1):
            day = today - timedelta(days=offset)
            series.append({"day": day.isoformat(), "label": day.strftime("%d.%m"),
                           **_day_point(s, ws, day, snapshots, today)})
        averages = _average_points(series)

    prev_snaps = _closed_scores(s, ws, previous_start, previous_end)
    prev_points = [_day_point(s, ws, previous_start + timedelta(days=o), prev_snaps, today)
                   for o in range((previous_end - previous_start).days + 1)]
    previous = _average_points(prev_points)
    has_previous = previous["measured_days"] > 0
    deltas = {key: (averages[key] - previous[key]) if has_previous else None
              for key in keys}

    measured = [p for p in series if p.get("measured", True)]
    best = max(measured, key=lambda p: p["overall"]) if measured else None
    if best and best["overall"] <= 0:
        best = None

    breakdown = prayer_breakdown(s, ws, window_start, today, gender)

    overall = overall_state(s, ws, today)
    components = overall["components"]
    prayer_today = prayer_state(s, ws, today, gender)
    streak = habit_streak(s, ws, tz=tz)

    return {
        "period": period,
        "series": series,
        "averages": {k: averages[k] for k in keys},
        "measured_days": averages.get("measured_days", 0),
        "previous": {k: previous[k] for k in keys},
        "deltas": deltas,
        "best_day": best and {"day": best["day"], "label": best["label"],
                              "overall": best["overall"]},
        "habit_avg": averages["habits"],
        "prayer_avg": averages["prayer"],
        "task_avg": averages["tasks"],
        "overall_avg": averages["overall"],
        "habit_streak": streak,
        "prayer_streak": prayer_streak(s, ws, gender, tz=tz),
        "prayer_breakdown": breakdown["counts"],
        "prayer_detail": breakdown,
        "today": {
            "overall": overall["value"],
            "weights": applied_weights(components),
            "task_points": dict(zip(("earned", "total"), today_task_score(s, ws, today, tz=tz, include_team=False))),
            "habit_tiers": habit_tier_progress(s, ws, today, include_team=False),
            "measured": overall["measured"],
            "trend": overall["trend"],
            "yesterday": overall["yesterday"],
            "tasks": components["tasks"],
            "habits": components["habits"],
            "team": components["team"],
            "prayer": components["prayer"],
            "prayer_score": prayer_today["score"],
            "prayer_max": PRAYER_MAX_SCORE,
            "prayer_performed": prayer_today["performed"],
            "prayer_required": PRAYER_REQUIRED,
            "streak": streak,
            # The day's main result beside the percentage: did the one thing
            # that mattered get done? A full score can hide that it did not (audit #9).
            "main": _main_result(s, ws, today, tz),
        },
    }


#: The windows the summary compares: today, the last 7 days, the last 30.
SUMMARY_WINDOWS = {"day": 1, "week": 7, "month": 30}


def summary(s: Session, ws: int, *, gender: str | None = None,
            tz: ZoneInfo | None = None) -> dict:
    """Today, this week and this month as directly comparable numbers."""
    tz = tz or _habit_tz(s, ws)
    today = today_local(tz)
    state = overall_state(s, ws, today)
    out = {"today": state, "windows": {}}

    for name, days in SUMMARY_WINDOWS.items():
        end = today
        start = today - timedelta(days=days - 1)
        current = _range_average(s, ws, start, end)
        previous = _range_average(s, ws, start - timedelta(days=days),
                                  start - timedelta(days=1))
        known = current["measured_days"] > 0
        before = previous["measured_days"] > 0
        out["windows"][name] = {
            **{k: current[k] for k in SERIES_KEYS},
            "days": days,
            "measured": known,
            # A change needs something on both sides of it.
            "delta": (current["overall"] - previous["overall"]) if known and before else None,
            "previous": previous["overall"] if before else None,
        }
    # Today's cell is the live number, not an average of one day.
    out["windows"]["day"]["overall"] = state["value"]
    out["windows"]["day"]["measured"] = state["measured"]
    out["windows"]["day"]["delta"] = (state["value"] - state["yesterday"]
                                      if state["measured"] and state["yesterday"] is not None
                                      else None)

    prayer = prayer_state(s, ws, today, gender)
    # Counts split exactly as the score is: own work, then the team's.
    habits_done, habits_total = habit_progress(s, ws, today, include_team=False)
    tasks_done, tasks_total = today_task_progress(s, ws, today, include_team=False)
    shared = due_team_habits(s, ws, today) + due_team_tasks(s, ws, today)
    components = state["components"]

    out["today"] = {
        "overall": state["value"],
        "measured": state["measured"],
        "trend": state["trend"],
        "tasks": components["tasks"], "habits": components["habits"],
        "prayer": components["prayer"], "team": components["team"],
        "tasks_done": tasks_done, "tasks_total": tasks_total,
        "habits_done": habits_done, "habits_total": habits_total,
        "team_done": sum(1 for _, ok in shared if ok), "team_total": len(shared),
        "prayer_performed": prayer["performed"],
        "prayer_required": PRAYER_REQUIRED,
        "prayer_score": prayer["score"], "prayer_max": PRAYER_MAX_SCORE,
        "prayer_owed": components["prayer"] is not None,
        "streak": habit_streak(s, ws, tz=tz),
    }
    return out


def csv_cell(value) -> str:
    """One CSV cell that a spreadsheet will not run (audit S78).

    Text starting with = + - @ (or a tab / carriage return) is read as a
    formula by Excel and Sheets; a leading apostrophe makes it plain text.
    Numbers pass through untouched, so "-5" stays a number. Quotes and
    commas are escaped the CSV way.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        text = "'" + text
    if any(c in text for c in (",", '"', "\n", "\r")):
        text = '"' + text.replace('"', '""') + '"'
    return text


def stats_csv(s: Session, ws: int, period: str = "month", *,
              gender: str | None = None, tz: ZoneInfo | None = None) -> str:
    """The statistics view as CSV, for the download button."""
    data = stats(s, ws, period, gender=gender, tz=tz)
    detail = data["prayer_detail"]
    lines = ["ErnestOS statistics"]
    lines.append(f"period,{period}")
    lines.append(f"generated,{datetime.now(tz or TZ):%Y-%m-%d %H:%M}")
    lines.append(f"formula,{SCORE_FORMULA}")
    lines.append("")
    lines.append(f"overall average %,{data['overall_avg']}")
    lines.append(f"task average %,{data['task_avg']}")
    lines.append(f"habit average %,{data['habit_avg']}")
    lines.append(f"prayer average %,{data['prayer_avg']}")
    lines.append(f"measured days,{data['measured_days']}")
    lines.append(f"habit streak,{data['habit_streak']}")
    lines.append(f"prayer streak,{data['prayer_streak']}")
    lines.append("")
    lines.append(f"prayer full days,{detail['full_days']} of {detail['days']}")
    lines.append(f"prayer on-time %,{detail['on_time_percent']}")
    lines.append("prayer status,count")
    for status, count in sorted(detail["counts"].items()):
        lines.append(f"{csv_cell(status)},{csv_cell(count)}")
    lines.append("")
    lines.append("date,overall %,tasks %,habits %,prayer %,measured")
    for point in data["series"]:
        lines.append(f"{point['day']},{point['overall']},{point['tasks']},"
                     f"{point['habits']},{point['prayer']},"
                     f"{'yes' if point.get('measured', True) else 'no'}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Calendar — one month of deadlines
# ---------------------------------------------------------------------------

def calendar_month(s: Session, ws: int, year: int, month: int, *,
                   tz: ZoneInfo | None = None) -> dict:
    """Every dated item inside one month, keyed by ISO date.

    Shared tasks and countdowns are on it too: a month that leaves out what
    you owe your team, or the exam you are counting down to, is not the month.
    """
    first = date(year, month, 1)
    last = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)

    events: dict[str, list[dict]] = {}

    def add(day: date, kind: str, title: str, extra: dict | None = None):
        events.setdefault(day.isoformat(), []).append(
            {"kind": kind, "title": title, **(extra or {})})

    for task in s.scalars(select(Task).where(
            Task.workspace_id == ws, Task.archived_at.is_(None),
            Task.deadline.isnot(None),
            Task.deadline.between(first, last))).all():
        add(task.deadline, "task", task.title,
            {"id": task.id, "status": task.status, "priority": task.priority,
             "due_time": task.due_time.strftime("%H:%M") if task.due_time else None})

    for project in s.scalars(select(Project).where(
            Project.workspace_id == ws, Project.team_id.is_(None),
            Project.archived_at.is_(None),
            Project.deadline.isnot(None),
            Project.deadline.between(first, last))).all():
        add(project.deadline, "project", project.name, {"id": project.id})

    for row in s.scalars(select(Birthday).where(Birthday.workspace_id == ws)).all():
        try:
            occurrence = row.birth_date.replace(year=year)
        except ValueError:
            occurrence = date(year, 2, 28)   # 29 Feb in a common year
        if first <= occurrence <= last:
            add(occurrence, "birthday", row.person_name,
                {"id": row.id, "turning": year - row.birth_date.year})

    owner = workspace_owner(s, ws)
    teams = teams_for(s, owner) if owner else []
    for team in teams:
        tasks = s.scalars(select(TeamTask).where(
            TeamTask.team_id == team.id, TeamTask.archived_at.is_(None),
            TeamTask.deadline.isnot(None),
            TeamTask.deadline.between(first, last))).all()
        done_by = _team_done_map(s, [t.id for t in tasks])
        for task in tasks:
            if not team_task_owed_by(task, owner):
                continue
            add(task.deadline, "team_task", task.title,
                {"id": task.id, "team_id": team.id, "team_name": team.name,
                 "status": "done" if team_task_done_for(
                     task, owner, done_by.get(task.id, set())) else "waiting",
                 "priority": task.priority})

    team_ids = [t.id for t in teams]
    cd_filter = Countdown.team_id.in_(team_ids) if team_ids else None
    stmt = select(Countdown).where(
        Countdown.archived_at.is_(None),
        Countdown.target_date.between(first, last),
        or_(Countdown.workspace_id == ws, cd_filter) if cd_filter is not None
        else Countdown.workspace_id == ws)
    names = {t.id: t.name for t in teams}
    for row in s.scalars(stmt).all():
        if row.team_id is not None and row.team_id not in names:
            continue
        add(row.target_date, "countdown", row.title,
            {"id": row.id, "team_id": row.team_id,
             "team_name": names.get(row.team_id)})

    return {"year": year, "month": month,
            "first_weekday": first.weekday(), "days_in_month": last.day,
            "today": today_local(tz).isoformat(), "events": events}


# ---------------------------------------------------------------------------
# Coming back after a break
# ---------------------------------------------------------------------------

#: A gap this long turns the app into a wall of failures on return.
BREAK_DAYS = 3


def break_state(s: Session, ws: int, user: User, *,
                tz: ZoneInfo | None = None) -> dict:
    """Whether this user is returning from a gap, and what is waiting."""
    zone = tz or user_tz(user)
    today = today_local(zone)
    last_seen = (local_date_of(user.last_active_at, zone)
                 if user.last_active_at else today)
    away = (today - last_seen).days

    overdue = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline < today)) or 0

    return {"days_away": away, "overdue": overdue,
            "suggest_reset": away >= BREAK_DAYS and overdue > 0}


#: What each fresh-start mode does, so the confirmation text and the code cannot
#: disagree about it. Nothing here deletes a row.
FRESH_START_MODES = {
    "focus": "the three most important today, the rest over the next six days",
    "today": "move every overdue task to today",
    "week": "spread overdue tasks across the coming week",
    "undate": "drop the deadlines, keep the tasks",
    "archive": "put overdue tasks in the archive",
}


#: How many returning tasks land on today in "focus" mode, and at most on
#: any one day when the rest are spread (audit #15).
FRESH_TODAY = 3


def _fresh_overdue(s: Session, ws: int, today: date) -> list[Task]:
    rows = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline < today,
        # Waiting on somebody else is not backlog to reshuffle.
        Task.blocked_reason.is_(None))).all()
    # Most important first, then the oldest: that order decides who gets today.
    return sorted(rows, key=lambda t: (_PRIORITY_RANK.get(t.priority, 1), t.deadline, t.id))


def fresh_start_plan(s: Session, ws: int, *, mode: str = "focus",
                     tz: ZoneInfo | None = None,
                     drop: set[int] | None = None) -> list[dict]:
    """What a reset would do, task by task, without doing it.

    `drop` are the tasks the person answered "no longer needed" for: they go
    to the archive and take no day from the ones that are still wanted.
    """
    today = today_local(tz)
    drop = drop or set()
    overdue = _fresh_overdue(s, ws, today)
    plan = [{"id": t.id, "title": t.title, "priority": t.priority,
             "from": t.deadline.isoformat() if t.deadline else None,
             "to": t.deadline.isoformat() if t.deadline else None,
             "archive": True, "dropped": True}
            for t in overdue if t.id in drop]
    overdue = [t for t in overdue if t.id not in drop]
    rest = 0
    for index, task in enumerate(overdue):
        archive, target = False, today
        if mode == "archive":
            archive, target = True, task.deadline
        elif mode == "undate":
            target = None
        elif mode == "week":
            # From tomorrow, round the next seven days, most important first.
            target = today + timedelta(days=1 + index % 7)
        elif mode == "focus":
            if index >= FRESH_TODAY:
                target = today + timedelta(days=1 + rest % 6)
                rest += 1
        plan.append({"id": task.id, "title": task.title, "priority": task.priority,
                     "from": task.deadline.isoformat() if task.deadline else None,
                     "to": target.isoformat() if target else None,
                     "archive": archive})
    return plan


def fresh_start(s: Session, ws: int, *, mode: str = "today",
                tz: ZoneInfo | None = None, drop: set[int] | None = None,
                log: list | None = None) -> int:
    """Clear the backlog in one move. Returns how many tasks were handled.

    Every change is written to a ResetLog first, so `undo_fresh_start` can
    put the old dates back.
    """
    plan = fresh_start_plan(s, ws, mode=mode, tz=tz, drop=drop)
    snapshot = []
    for step in plan:
        task = s.get(Task, step["id"])
        before = {"id": task.id,
                  "deadline": task.deadline.isoformat() if task.deadline else None,
                  "archived": False}
        if step["archive"]:
            task.archived_at = utcnow()
        else:
            _move_deadline(s, ws, task, date.fromisoformat(step["to"]) if step["to"] else None)
        task.reminder_sent_at = None
        snapshot.append({**before, "after_deadline": step["to"] if not step["archive"]
                         else before["deadline"], "after_archived": step["archive"]})
    row = None
    if snapshot:
        row = ResetLog(workspace_id=ws, mode=mode,
                       snapshot=json.dumps(snapshot, ensure_ascii=False))
        s.add(row)
    s.commit()
    if log is not None and row is not None:
        log.append(row.id)
    return len(plan)


#: An undo is offered for this long after a reset.
RESET_UNDO_WINDOW = timedelta(days=7)


def undo_fresh_start(s: Session, ws: int, reset_id: int | None = None) -> dict:
    """Put one reset back. A task changed since stays as the person left it.

    With `reset_id` it is exactly that reset, and only once — a second tap or
    a retried request cannot reach past it to an older one. Without it, only
    the newest reset qualifies.
    """
    stmt = select(ResetLog).where(
        ResetLog.workspace_id == ws, ResetLog.created_at >= utcnow() - RESET_UNDO_WINDOW)
    if reset_id is not None:
        log_row = s.scalar(stmt.where(ResetLog.id == reset_id))
    else:
        log_row = s.scalar(stmt.order_by(ResetLog.id.desc()).limit(1))
    if log_row is None or log_row.undone_at is not None:
        raise NotFound("reset")
    restored, conflicts = 0, []
    for item in json.loads(log_row.snapshot or "[]"):
        task = s.get(Task, item["id"])
        if task is None or task.workspace_id != ws:
            continue
        now_deadline = task.deadline.isoformat() if task.deadline else None
        untouched = (now_deadline == item["after_deadline"]
                     and (task.archived_at is not None) == item["after_archived"]
                     and task.status == "waiting")
        if not untouched:
            conflicts.append({"id": task.id, "title": task.title})
            continue
        if item["after_archived"]:
            task.archived_at = None
        _move_deadline(s, ws, task,
                       date.fromisoformat(item["deadline"]) if item["deadline"] else None)
        restored += 1
    log_row.undone_at = utcnow()
    s.commit()
    return {"restored": restored, "conflicts": conflicts}


# ---------------------------------------------------------------------------
# Weekly review
# ---------------------------------------------------------------------------

def weekly_review(s: Session, ws: int, user: User,
                  when: date | None = None) -> dict:
    """The week's numbers plus the three answers, ready to edit."""
    tz = user_tz(user)
    today = today_local(tz)
    start = week_start(when or today)
    end = start + timedelta(days=6)

    week_from, week_to = utc_window(start, end, tz)
    done = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.status == "done",
        Task.completed_at >= week_from, Task.completed_at < week_to)) or 0
    missed = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline < today)) or 0

    averages = _range_average(s, ws, start, min(end, today))
    focus = list_focus(s, ws, start, tz=tz)

    row = s.scalar(select(WeeklyReview).where(
        WeeklyReview.workspace_id == ws, WeeklyReview.week_start == start))

    return {
        "week_start": start.isoformat(),
        "tasks_done": done, "tasks_overdue": missed,
        "habit_pct": averages["habits"], "prayer_pct": averages["prayer"],
        "task_pct": averages["tasks"], "overall_pct": averages["overall"],
        "focus": focus,
        "focus_done": sum(1 for f in focus if f["done"]),
        "is_week_end": today.weekday() >= 4,
        "answers": {
            "went_well": row.went_well if row else "",
            "blocked": row.blocked if row else "",
            "next_focus": row.next_focus if row else "",
        },
        "saved": row is not None,
        "suggestions": _review_suggestions(s, ws, focus, missed, averages, today),
    }


def _main_result(s: Session, ws: int, today: date, tz: ZoneInfo | None) -> dict | None:
    """Today's pinned task, else the week's main goal — and whether it is done."""
    pinned = top3_tasks(s, ws, today, tz=tz)
    if pinned:
        return {"kind": "task", "title": pinned[0]["title"], "done": pinned[0]["status"] == "done"}
    goal = primary_focus(s, ws, today, tz=tz)
    if goal:
        return {"kind": "goal", "title": goal["title"], "done": bool(goal["done"])}
    return None


def _review_suggestions(s: Session, ws: int, focus: list[dict], overdue: int,
                        averages: dict, today: date) -> list[dict]:
    """What the week's numbers suggest changing — offered, never applied.

    Each item names an action the app already has; the person taps it or not
    (audit #16).
    """
    out = []
    if overdue >= 3:
        out.append({"key": "reset", "n": overdue})
    for goal in focus:
        if not goal["done"] and goal.get("carries", 0) >= 1:
            out.append({"key": "shrink", "id": goal["id"], "title": goal["title"],
                        "n": goal["carries"] + 1})
    habits = [h for h in list_habits(s, ws, today) if h["due"] and h["scored"]]
    if len(habits) > 3 and (averages.get("habits") or 0) < 50:
        out.append({"key": "fewer_habits", "n": len(habits)})
    blocked = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.archived_at.is_(None), Task.status == "waiting",
        Task.blocked_reason.is_not(None))) or 0
    if blocked:
        out.append({"key": "blocked", "n": int(blocked)})
    return out[:4]


def save_weekly_review(s: Session, ws: int, *, went_well: str = "",
                       blocked: str = "", next_focus: str = "",
                       when: date | None = None,
                       tz: ZoneInfo | None = None) -> WeeklyReview:
    start = week_start(when or today_local(tz))
    row = s.scalar(select(WeeklyReview).where(
        WeeklyReview.workspace_id == ws, WeeklyReview.week_start == start))
    if row is None:
        row = WeeklyReview(workspace_id=ws, week_start=start)
        s.add(row)
    row.went_well = went_well.strip()[:2000]
    row.blocked = blocked.strip()[:2000]
    row.next_focus = next_focus.strip()[:2000]
    s.commit()
    return row


def review_to_goal(s: Session, ws: int, title: str, *,
                   tz: ZoneInfo | None = None) -> WeeklyFocus | None:
    """Turn the review's "next week's focus" into next week's goal, once.

    Saving the review twice, or a goal of that name already being there, does
    not make a second one (audit #16).
    """
    title = " ".join((title or "").split())[:200]
    if not title:
        return None
    nxt = week_start(today_local(tz)) + timedelta(days=7)
    rows = s.scalars(select(WeeklyFocus).where(
        WeeklyFocus.workspace_id == ws, WeeklyFocus.week_start == nxt,
        WeeklyFocus.carried_to.is_(None))).all()
    if any(r.title.strip().lower() == title.lower() for r in rows):
        return None
    if len(rows) >= MAX_FOCUS:
        raise ValueError("week is full")
    return add_focus(s, ws, title, nxt)


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

#: From this hour on, closing the day is the thing worth suggesting.
DAY_CLOSE_HOUR = 20


#: A task with a time of day is offered this long before it; earlier than
#: that, work that can be done now comes first (audit #1).
NOW_LEAD_MINUTES = 60


def _now_task(task: dict, reason: str) -> dict:
    """One open task, in the shape the "now" card reads."""
    team = task.get("source") == "team"
    return {"kind": "task", "title": task["title"], "id": task["id"],
            "action": "team_task" if team else "task",
            "source": "team" if team else "personal",
            "team_id": task.get("team_id"), "team_name": task.get("team_name"),
            "meta": task["due_time"] or "",
            "reason": reason, "priority": task["priority"],
            "due_time": task["due_time"], "deadline": task["deadline"],
            "project": task.get("project"),
            "timer_minutes": task.get("timer_minutes"),
            "timer": task.get("timer")}


def _not_yet(task: dict, now: datetime) -> bool:
    """Due later today at a set time, and not within the lead window yet."""
    if not task.get("due_time"):
        return False
    hh, mm = (int(x) for x in task["due_time"].split(":")[:2])
    starts = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return starts - now > timedelta(minutes=NOW_LEAD_MINUTES)


def _team_open_for_now(s: Session, user: User, today: date,
                       tz: ZoneInfo | None) -> tuple[list[dict], list[dict]]:
    """Shared tasks this person still owes: (late, due today)."""
    late, due = [], []
    try:
        items = team_items_for_day(s, user.telegram_id, tz=tz)["tasks"]
    except Exception:  # noqa: BLE001 — a team problem must not blank the card
        log.exception("now_next: team tasks unavailable")
        return late, due
    iso = today.isoformat()
    for x in items:
        if not x.get("owed", True) or x.get("done") or not x.get("deadline"):
            continue
        if x["deadline"] < iso:
            late.append(x)
        elif x["deadline"] == iso:
            due.append(x)
    return late, due


def now_next(s: Session, ws: int, user: User, *,
             tz: ZoneInfo | None = None) -> dict:
    """The one thing to do next, decided by a fixed ladder.

      1. get up, while it still counts
      2. whatever the user pinned as the day's main task
      3. anything already late — own or shared, one queue
      4. anything due today that can be done now: no set time, or a time
         within the next hour; earliest time first, then by priority
      5. a habit that is due and not done
      6. something due later today at a set time (a 18:00 meeting at 09:00
         waits here instead of pushing doable work aside)
      7. today's prayers, while any is still unrecorded
      8. a late wake-up that was never written down — late still counts
      9. close the day, once the evening has started
     10. otherwise: today's important work is finished

    Shared tasks the person owes stand in the same queue as their own, marked
    with `source: "team"`. Every answer carries a `reason`, because a card
    that decides on the user's behalf owes them the sentence explaining why
    this one and not another. A pinned task beats a late one, but never hides
    it: the answer then carries `overdue`. When something other than a timed
    task is offered, the next timed one rides along as `upcoming`.
    """
    tz = tz or user_tz(user)
    today = today_local(tz)
    now = now_local(tz)

    wake = wake_state(s, ws, tz=tz)
    if wake and not wake["logged"] and not wake["late"]:
        return {"kind": "wake", "title": "", "id": wake["habit_id"],
                "action": "wakeup", "meta": wake["target"], "reason": "wake"}

    team_late, team_due = _team_open_for_now(s, user, today, tz)
    listed = list_tasks(s, ws, horizon_days=0, tz=tz)
    late = [t for t in listed["overdue"] if t["status"] != "done" and not t.get("blocked")]
    late = _sort_open(late + team_late)
    rechecks = [t for group in ("overdue", "upcoming", "undated", "later")
                for t in listed.get(group, []) if t.get("recheck")]
    for task in top3_tasks(s, ws, today, tz=tz):
        if task["status"] != "done":
            out = _now_task(task, "pinned")
            others = [t for t in late if not (t["id"] == task["id"]
                                              and t.get("source") != "team")]
            if others:
                out["overdue"] = {"count": len(others), "title": others[0]["title"],
                                  "id": others[0]["id"]}
            return out

    for task in late:
        return _now_task(task, "overdue")

    # Waiting on a reply, and the day to check has come.
    for task in rechecks:
        return _now_task(task, "recheck")

    due = _sort_open([t for t in tasks_due_today(s, ws, tz=tz)
                      if t["status"] != "done" and not _is_parked(t)] + team_due)
    ready = [t for t in due if not _not_yet(t, now)]
    later = [t for t in due if _not_yet(t, now)]
    upcoming = ({"title": later[0]["title"], "due_time": later[0]["due_time"],
                 "id": later[0]["id"], "source": later[0].get("source") or "personal"}
                if later else None)

    def with_upcoming(out: dict) -> dict:
        if upcoming:
            out["upcoming"] = upcoming
        return out

    for task in ready:
        return with_upcoming(_now_task(task, "due_today"))

    for habit in list_habits(s, ws, today, tz=tz):
        if habit["due"] and not habit["done"] and not habit["protected"]:
            return with_upcoming({
                "kind": "habit", "title": habit["name"], "id": habit["id"],
                "action": "habit", "meta": habit["target_time"] or "",
                "reason": "habit",
                "timer_minutes": habit.get("timer_minutes"),
                "timer": habit.get("timer")})

    for task in later:
        return _now_task(task, "due_later")

    if prayer_owed(s, ws, today):
        prayer = prayer_state(s, ws, today, user.gender)
        # From the morning on: bomdod is the first thing owed in the day, so
        # waiting for noon left the card saying "all done" at dawn.
        if not prayer["complete"] and prayer["performed"] < PRAYER_REQUIRED:
            return {"kind": "prayer", "title": "", "id": None, "action": "prayer",
                    "meta": f"{prayer['performed']}/{PRAYER_REQUIRED}",
                    "reason": "prayer"}

    if wake and not wake["logged"]:
        return {"kind": "wake", "title": "", "id": wake["habit_id"],
                "action": "wakeup", "meta": wake["target"], "reason": "wake_late"}

    journal_on = s.scalar(select(Habit.id).where(
        Habit.workspace_id == ws, Habit.system_key == SYSTEM_JOURNAL,
        Habit.archived_at.is_(None))) is not None
    if journal_on and now.hour >= DAY_CLOSE_HOUR and not journal_done(s, ws, today):
        return {"kind": "journal", "title": "", "id": None, "action": "journal",
                "meta": "", "reason": "evening"}

    counts = home_counts(s, ws, user, today)
    any_done = any(counts[k]["done"] for k in ("tasks", "habits", "team", "prayer"))
    return {"kind": "clear", "title": "", "id": None, "action": "", "meta": "",
            "reason": "clear", "empty": not any_done,
            "team_remaining": counts["team"]["total"] - counts["team"]["done"]}


def week_strip(s: Session, ws: int, *, tz: ZoneInfo | None = None) -> dict:
    """The current week as seven cells, with a marker where something lands."""
    today = today_local(tz)
    start = week_start(today)
    end = start + timedelta(days=6)

    counts: dict[str, int] = {}

    def bump(day: date):
        counts[day.isoformat()] = counts.get(day.isoformat(), 0) + 1

    for deadline in s.scalars(select(Task.deadline).where(
            Task.workspace_id == ws, Task.archived_at.is_(None),
            Task.status == "waiting", Task.deadline.between(start, end))).all():
        if deadline:
            bump(deadline)
    for deadline in s.scalars(select(Project.deadline).where(
            Project.workspace_id == ws, Project.archived_at.is_(None),
            Project.deadline.between(start, end))).all():
        if deadline:
            bump(deadline)

    days = []
    for offset in range(7):
        day = start + timedelta(days=offset)
        days.append({"day": day.isoformat(), "date": day.day,
                     "weekday": day.weekday(),
                     "today": day == today,
                     "count": counts.get(day.isoformat(), 0)})
    return {"start": start.isoformat(), "days": days}


def home_counts(s: Session, ws: int, user: User, day: date | None = None, *,
                habits: tuple[int, int] | None = None,
                prayer: dict | None = None) -> dict:
    """Today as plain counts — "3 of 5" — and never as a percentage.

    The one line Home shows about how the day is going. A count is something a
    person can check against their own list; a percentage first thing in the
    morning is a grade for a day that has not happened yet. The formula behind
    the percentage is untouched and lives on the Statistics screen.

    The same split as the score and as Statistics: Vazifa and Odat are your
    own, Jamoa is the shared work owed today — so "Vazifa 1/3" here is the
    very count under the Tasks tile there. `habits` is ignored (kept for
    older callers): it used to arrive with the team's habits mixed in.
    """
    tz = user_tz(user)
    day = day or today_local(tz)
    tasks_done, tasks_total = today_task_progress(s, ws, day, tz=tz,
                                                  include_team=False)
    habits_done, habits_total = habit_progress(s, ws, day, include_team=False)
    shared = due_team_habits(s, ws, day) + due_team_tasks(s, ws, day)
    prayer = prayer or prayer_state(s, ws, day, user.gender)
    return {
        # done/total is today's promises; `finished` is everything completed
        # today, late and undated work included (audit #7).
        "tasks": {"done": tasks_done, "total": tasks_total,
                  "finished": tasks_completed_on(s, ws, day, tz=tz)},
        "habits": {"done": habits_done, "total": habits_total},
        "team": {"done": sum(1 for _, ok in shared if ok), "total": len(shared)},
        "prayer": {"done": prayer["performed"], "total": PRAYER_REQUIRED,
                   "excused": prayer["excused"],
                   "owed": prayer_owed(s, ws, day)},
    }


def home(s: Session, ws: int, user: User) -> dict:
    """Everything Home shows, and nothing else.

    Home answers "what do I do right now?" — the Now card — then lists
    today's work under one line of counts. The week goal lives on Tasks, the
    percentages on Statistics and the countdowns on Tasks → Calendar, so none
    of them is computed here: every field below is drawn by Home in the Mini
    App or by the bot's Home.
    """
    tz = user_tz(user)
    today = today_local(tz)
    done, total = habit_progress(s, ws, today)
    prayer = prayer_state(s, ws, today, user.gender)
    top3 = top3_tasks(s, ws, today, tz=tz)
    journal = get_journal(s, ws, today)
    mods = modules_for(s, ws)

    return {
        "date": today.isoformat(),
        "date_label": date_label(today, user.language),
        "name": user.first_name or "",
        "quote": user.quote,
        "language": user.language,
        "theme": user.theme,
        "gender": user.gender,
        "timezone": user.timezone or str(TZ),
        "photo_file_id": user.photo_file_id,
        "modules": mods,
        "habits": {"done": done, "total": total},
        "prayer": {"score": prayer["score"], "max": PRAYER_MAX_SCORE,
                   "performed": prayer["performed"], "required": PRAYER_REQUIRED,
                   "complete": prayer["complete"], "excused": prayer["excused"],
                   "owed": prayer_owed(s, ws, today)},
        "streak": habit_streak(s, ws, tz=tz),
        "counts": home_counts(s, ws, user, today, habits=(done, total),
                              prayer=prayer),
        "now": now_next(s, ws, user, tz=tz),
        # The week's one goal, on Home so it is seen every day (v12.2).
        "focus": primary_focus(s, ws, today, tz=tz),
        "wake": wake_state(s, ws, tz=tz),
        "top3": top3,
        "top3_max": MAX_TOP3,
        "tasks_today": today_tasks_by_project(
            s, ws, tz=tz, skip_ids={t["id"] for t in top3}),
        "tasks_today_total": len(tasks_due_today(s, ws, tz=tz)),
        # Written (one answer is enough to tick the habit) and complete (all
        # five, a full reflection) are two different facts and both are shown.
        "journal_today": bool(journal and journal["written"]),
        "journal_full": bool(journal and journal["complete"]),
        "journal_answered": journal["answered"] if journal else 0,
        "journal_total": len(JOURNAL_KEYS),
        "active_timer": active_timer(s, ws),
        "break": break_state(s, ws, user, tz=tz),
        "load": day_load(s, ws, user, tz=tz),
    }


def day_load(s: Session, ws: int, user: User, *, tz: ZoneInfo | None = None) -> dict:
    """Today's plan in minutes against the time the person said they have.

    A task's length is its timer (set, or read from "2h" in the title); a
    timed habit counts too. Nothing is changed here: when the plan is bigger
    than the day, the least important tasks are *offered* for tomorrow, and
    only the person moves them (audit #2).
    """
    tz = tz or user_tz(user)
    today = today_local(tz)
    tasks = [t for t in tasks_due_today(s, ws, tz=tz) if t["status"] != "done"]
    picked = {t["id"] for t in tasks}
    tasks += [t for t in top3_tasks(s, ws, today, tz=tz)
              if t["status"] != "done" and t["id"] not in picked]
    planned, unestimated = 0, 0
    sized = []
    for t in tasks:
        minutes = t.get("timer_minutes") or 0
        if minutes:
            planned += minutes
            sized.append(t)
        else:
            unestimated += 1
    for h in list_habits(s, ws, today, tz=tz):
        if h["due"] and not h["done"] and h.get("timer_minutes"):
            planned += h["timer_minutes"]
    capacity = user.day_capacity or 0
    over = bool(capacity) and planned > capacity
    suggest = []
    if over:
        excess = planned - capacity
        # Lowest priority first, then the longest: fewest moves to fit.
        for t in sorted(sized, key=lambda t: (-_PRIORITY_RANK.get(t["priority"], 1),
                                              -(t["timer_minutes"] or 0))):
            if excess <= 0 or t.get("top3"):
                continue
            suggest.append({"id": t["id"], "title": t["title"],
                            "minutes": t["timer_minutes"], "priority": t["priority"]})
            excess -= t["timer_minutes"]
    return {"planned_min": planned, "capacity_min": capacity, "over": over,
            "unestimated": unestimated, "suggest": suggest}


# ---------------------------------------------------------------------------
# External calendars
# ---------------------------------------------------------------------------

#: Providers the sync layer is being built for.
CALENDAR_PROVIDERS = ("google", "icloud", "caldav")


def sync_calendar(s: Session, ws: int, provider: str,
                  credentials: dict | None = None) -> dict:
    """Two-way sync with an external calendar. Not implemented yet."""
    if provider not in CALENDAR_PROVIDERS:
        raise ValueError("unknown provider")
    log.info("calendar sync requested for workspace %s (%s) — not implemented",
             ws, provider)
    return {"provider": provider, "supported": False,
            "imported": 0, "updated": 0, "skipped": 0}


# ---------------------------------------------------------------------------
# Data and privacy
# ---------------------------------------------------------------------------

def _export_debts(s: Session, ws: int) -> list[dict]:
    rows = s.scalars(select(Debt).where(Debt.workspace_id == ws).order_by(Debt.id)).all()
    pays = _debt_payments(s, [r.id for r in rows])
    return [_debt_dict(r, None, pays[r.id]) for r in rows]


EXPORT_SCHEMA_VERSION = 2


def export_workspace(s: Session, ws: int, user: User) -> dict:
    """Everything this workspace contains, as plain JSON-ready data."""
    from coins import export as coins_export
    from promo import redeemed_by as promo_export
    def habits():
        for h in s.scalars(select(Habit).where(Habit.workspace_id == ws)).all():
            # `id` is what habit_logs, schedule versions, pauses and timers
            # point at; without it an export could not be put back together.
            yield {"id": h.id, "name": h.name, "category": h.category,
                   "system_key": h.system_key or None, "position": h.position,
                   "schedule": clean_schedule(h.schedule),
                   "target_time": h.target_time.strftime("%H:%M") if h.target_time else None,
                   "remind_at": h.remind_at.strftime("%H:%M") if h.remind_at else None,
                   "timer_minutes": h.timer_minutes,
                   "target_qty": h.target_qty, "min_qty": h.min_qty, "unit": h.unit,
                   "active_from": h.active_from.isoformat() if h.active_from else None,
                   "paused": h.paused_at is not None,
                   "archived": h.archived_at is not None,
                   "created": h.created_at.isoformat() if h.created_at else None}

    return {
        #: Bumped whenever a section or a field's meaning changes, so a
        #: reader can tell which shape it holds (K25). 2: stable ids, habit
        #: amounts, schedule history, pauses and timer sessions.
        "schema_version": EXPORT_SCHEMA_VERSION,
        "exported_at": datetime.now(user_tz(user)).isoformat(),
        "profile": {
            "member_no": user.member_no,
            "first_name": user.first_name, "last_name": user.last_name,
            "username": user.username, "language": user.language,
            "gender": user.gender, "theme": user.theme,
            "timezone": user.timezone or str(TZ), "quote": user.quote,
            "joined": user.created_at.isoformat() if user.created_at else None,
        },
        "habits": list(habits()),
        "habit_logs": [
            {"habit_id": r.habit_id, "day": r.day.isoformat(), "done": r.done, "qty": r.qty}
            for r in s.scalars(select(HabitLog)
                               .where(HabitLog.workspace_id == ws)
                               .order_by(HabitLog.day)).all()],
        "habit_schedules": [
            {"habit_id": r.item_id, "valid_from": r.valid_from.isoformat(), "schedule": r.schedule}
            for r in s.scalars(select(HabitScheduleVersion)
                               .where(HabitScheduleVersion.workspace_id == ws,
                                      HabitScheduleVersion.kind == "habit")
                               .order_by(HabitScheduleVersion.item_id,
                                         HabitScheduleVersion.valid_from)).all()],
        "habit_pauses": [
            {"habit_id": r.item_id, "start_day": r.start_day.isoformat(),
             "end_day": r.end_day.isoformat() if r.end_day else None}
            for r in s.scalars(select(HabitPauseInterval)
                               .where(HabitPauseInterval.workspace_id == ws,
                                      HabitPauseInterval.kind == "habit")
                               .order_by(HabitPauseInterval.item_id,
                                         HabitPauseInterval.start_day)).all()],
        "timers": [
            {"kind": r.kind, "item_id": r.item_id, "day": r.day.isoformat(), "title": r.title,
             "planned_sec": r.duration_sec, "worked_sec": r.elapsed_sec, "status": r.status,
             "manual": bool(r.manual),
             "started_at": r.started_at.isoformat() if r.started_at else None,
             "finished_at": r.finished_at.isoformat() if r.finished_at else None}
            for r in s.scalars(select(TimerRun)
                               .where(TimerRun.workspace_id == ws)
                               .order_by(TimerRun.day, TimerRun.id)).all()],
        "prayers": [
            {"day": r.day.isoformat(), "prayer": r.prayer, "status": r.status}
            for r in s.scalars(select(PrayerLog)
                               .where(PrayerLog.workspace_id == ws)
                               .order_by(PrayerLog.day)).all()],
        "projects": [
            {"id": p.id, "name": p.name, "description": p.description, "status": p.status,
             "deadline": p.deadline.isoformat() if p.deadline else None,
             "archived": p.archived_at is not None}
            for p in s.scalars(select(Project).where(
                Project.workspace_id == ws, Project.team_id.is_(None))).all()],
        "tasks": [
            {"id": t.id, "title": t.title, "description": t.description, "status": t.status,
             "priority": t.priority, "project_id": t.project_id,
             "deadline": t.deadline.isoformat() if t.deadline else None,
             "due_time": t.due_time.strftime("%H:%M") if t.due_time else None,
             "recurrence": clean_recurrence(t.recurrence),
             "timer_minutes": t.timer_minutes,
             "completed_at": t.completed_at.isoformat() if t.completed_at else None,
             "archived": t.archived_at is not None}
            for t in s.scalars(select(Task).where(Task.workspace_id == ws)).all()],
        "countdowns": [
            {"title": c.title, "date": c.target_date.isoformat(),
             "scope": c.scope or "general",
             "archived": c.archived_at is not None}
            for c in s.scalars(select(Countdown)
                               .where(Countdown.workspace_id == ws,
                                      Countdown.team_id.is_(None))
                               .order_by(Countdown.target_date)).all()],
        "weekly_focus": [
            {"week_start": f.week_start.isoformat(), "slot": f.slot,
             "title": f.title, "priority": f.priority, "done": f.done}
            for f in s.scalars(select(WeeklyFocus)
                               .where(WeeklyFocus.workspace_id == ws)
                               .order_by(WeeklyFocus.week_start)).all()],
        "goals": _export_goals(s, ws),
        "weekly_reviews": [
            {"week_start": r.week_start.isoformat(), "went_well": r.went_well,
             "blocked": r.blocked, "next_focus": r.next_focus}
            for r in s.scalars(select(WeeklyReview)
                               .where(WeeklyReview.workspace_id == ws)
                               .order_by(WeeklyReview.week_start)).all()],
        "journal": [
            {"day": r["day"], "answers": r["answers"], "mood": r["mood"]}
            for r in list_journal(s, ws, limit=10_000)],
        "birthdays": [
            {"person_name": r.person_name, "birth_date": r.birth_date.isoformat(),
             "note": r.note}
            for r in s.scalars(select(Birthday)
                               .where(Birthday.workspace_id == ws)).all()],
        "money": [
            {"id": r.id, "day": r.day.isoformat(), "kind": r.kind, "amount": int(r.amount),
             "category": r.category, "note": r.note, "source": r.source,
             "account_id": r.account_id}
            for r in s.scalars(select(MoneyEntry)
                               .where(MoneyEntry.workspace_id == ws)
                               .order_by(MoneyEntry.day, MoneyEntry.id)).all()],
        "money_budgets": money_budgets(s, ws),
        "money_wallet": _export_wallet(s, ws),
        "debts": _export_debts(s, ws),
        "agent_inbox": [
            {"id": r.id, "status": r.status, "revision": r.revision,
             "transcript": r.transcript, "language": r.detected_language, "history": json.loads(r.history),
             "preview": r.preview, "plan": json.loads(r.plan), "result": json.loads(r.result),
             "created_at": r.created_at.isoformat()}
            for r in s.scalars(select(AgentDraft).where(AgentDraft.workspace_id == ws)).all()],
        "agent_history": [
            {"draft_id": r.draft_id, "revision": r.revision, "event": r.event,
             "detail": json.loads(r.detail), "created_at": r.created_at.isoformat()}
            for r in s.scalars(select(AgentAudit).where(AgentAudit.workspace_id == ws)).all()],
        "coin_spends": coins_export(s, user.telegram_id),
        "promo_codes_used": promo_export(s, user.telegram_id),
        "agent_chat": [{"role": r.role, "text": r.text,
                        "at": r.created_at.isoformat() if r.created_at else None}
                       for r in s.scalars(select(AgentChatMessage).where(
                           AgentChatMessage.workspace_id == ws).order_by(AgentChatMessage.id)).all()],
        "daily_scores": [
            {"day": r.day.isoformat(), "score": r.total_score, "grade": r.grade,
             "closed": bool(r.closed)}
            for r in s.scalars(select(DailyScore)
                               .where(DailyScore.user_id == user.telegram_id)
                               .order_by(DailyScore.day)).all()],
    }


#: Every table that holds workspace-scoped data. Deleting an account walks this
#: list, so adding a model without adding it here is the one way a deletion
#: could leave someone's rows behind — which is why the list is explicit.
WORKSPACE_TABLES = [AgentAudit, AgentDraft, AgentPreference,
                    TimerRun, HabitLog, HabitScheduleVersion, HabitPauseInterval,
                    Habit, PrayerLog, PrayerDay, ResetLog, Task,
                    Project, WeeklyFocus, WeeklyReview, JournalEntry, Birthday,
                    Countdown, Feedback, DailyReportLog, MoneyEntry, MoneyBudget,
                    DebtPayment, Debt, Snooze, LifeGoal,
                    MoneyTransfer, MoneySubscription, MoneyAccount, AgentChatMessage]


def wipe_workspace(s: Session, telegram_id: int) -> bool:
    """Erase everything the user has written, but keep the account.

    Shared projects created from this workspace belong to their team, so they
    stay where they are; everything private goes.
    """
    from sqlalchemy import delete as sql_delete

    ws = s.scalar(select(Workspace.id).where(Workspace.user_id == telegram_id))
    if ws is None:
        return False
    for model in WORKSPACE_TABLES:
        stmt = sql_delete(model).where(model.workspace_id == ws)
        if model in (Project, Countdown):
            stmt = stmt.where(model.team_id.is_(None))
        s.execute(stmt)

    for model in (XPEvent, DailyScore, UserAchievement, UserProgress):
        s.execute(sql_delete(model).where(model.user_id == telegram_id))

    s.commit()
    seed_default_habits(s, ws)
    s.commit()
    return True


def delete_account(s: Session, telegram_id: int) -> bool:
    """Erase a user and everything in their workspace, for real."""
    from sqlalchemy import delete as sql_delete

    user = s.get(User, telegram_id)
    if user is None:
        return False

    # Teams this account owns (K02). `teams.owner_id` cascades on delete, so
    # erasing the owner would erase the team — and every other member's
    # tasks, ticks and history in it. A live team with other people in it
    # needs a new owner first, exactly as leaving does; an archived one is
    # handed to its earliest other member so their past stays; one nobody
    # else was ever in goes with the account.
    owned = s.scalars(select(Team).where(Team.owner_id == telegram_id)).all()
    for team in owned:
        if team.archived_at is None and any(
                m["user_id"] != telegram_id for m in team_members(s, team.id)):
            raise ValueError("owner_must_transfer")
    for team in owned:
        heir = s.scalar(select(TeamMember.user_id).where(
            TeamMember.team_id == team.id, TeamMember.user_id != telegram_id)
            .order_by(TeamMember.joined_at).limit(1))
        if heir is None:
            s.delete(team)
        else:
            team.owner_id = heir
            team.pending_owner_id = None
            team.archived_at = team.archived_at or utcnow()
    s.flush()

    ws = s.scalar(select(Workspace.id).where(Workspace.user_id == telegram_id))
    if ws is not None:
        for model in WORKSPACE_TABLES:
            s.execute(sql_delete(model).where(model.workspace_id == ws))
        s.execute(sql_delete(Workspace).where(Workspace.id == ws))

    s.execute(sql_delete(ReferralCode).where(ReferralCode.user_id == telegram_id))
    s.execute(sql_delete(Referral).where(
        or_(Referral.referred_user_id == telegram_id,
            Referral.inviter_user_id == telegram_id)))

    for model in (XPEvent, DailyScore, UserAchievement):
        s.execute(sql_delete(model).where(model.user_id == telegram_id))
    s.execute(sql_delete(UserProgress).where(UserProgress.user_id == telegram_id))
    s.execute(sql_delete(IdempotencyKey).where(IdempotencyKey.user_id == telegram_id))
    # Their share of every team: memberships, ticks and snapshots. The teams
    # themselves stay for the people still in them.
    s.execute(sql_delete(TeamMember).where(TeamMember.user_id == telegram_id))
    s.execute(sql_delete(TeamTaskDone).where(TeamTaskDone.user_id == telegram_id))
    s.execute(sql_delete(TeamHabitLog).where(TeamHabitLog.user_id == telegram_id))
    s.execute(sql_delete(TeamDayScore).where(TeamDayScore.user_id == telegram_id))
    s.execute(sql_delete(TeamJoinRequest).where(TeamJoinRequest.user_id == telegram_id))
    from db import UserAvatar
    import coins
    s.execute(sql_delete(UserAvatar).where(UserAvatar.account_id == telegram_id))
    coins.forget(s, [telegram_id])
    import promo
    promo.forget(s, [telegram_id])
    # The login, the password, and every Telegram signed in to the account.
    import accounts
    accounts.forget_account(s, telegram_id)

    s.delete(user)
    s.commit()
    return True


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def save_feedback(s: Session, ws: int, telegram_id: int, message: str) -> Feedback:
    message = message.strip()[:4000]
    if not message:
        raise ValueError("empty feedback")
    row = Feedback(workspace_id=ws, user_id=telegram_id, message=message)
    s.add(row)
    s.commit()
    return row


def mark_feedback_delivered(s: Session, feedback_id: int) -> None:
    row = s.get(Feedback, feedback_id)
    if row is not None:
        row.delivered = True
        s.commit()


# ---------------------------------------------------------------------------
# Idempotent writes
# ---------------------------------------------------------------------------
#
# The Mini App sends `X-Idempotency-Key` with every create. The first request
# with a key claims it (status 0), does its work and stores its response; a
# repeat — a double tap, or a retry after the phone lost the answer — gets the
# stored response instead of writing a second row.

#: How long an answer is kept for a retry. A day is far past any real retry.
IDEMPOTENCY_TTL = timedelta(hours=24)
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def idempotency_begin(s: Session, user_id: int, key: str, path: str) -> tuple:
    """("new", id) to go ahead, ("done", status, body) to answer, ("busy",)."""
    row = IdempotencyKey(user_id=user_id, key=key, path=path[:160])
    s.add(row)
    try:
        s.commit()
        return ("new", row.id)
    except IntegrityError:
        s.rollback()
    existing = s.scalar(select(IdempotencyKey).where(
        IdempotencyKey.user_id == user_id, IdempotencyKey.key == key))
    if existing is None:
        return ("busy",)
    if existing.path != path[:160]:
        # The same key on a different endpoint is a client bug, not a retry.
        return ("mismatch",)
    if existing.status_code:
        return ("done", existing.status_code, existing.body)
    return ("busy",)


def idempotency_finish(s: Session, row_id: int, status: int, body: str) -> None:
    row = s.get(IdempotencyKey, row_id)
    if row is None:
        return
    if 200 <= status < 300:
        row.status_code = status
        row.body = body[:20000]
    else:
        # A failed attempt must not block a corrected retry with the same key.
        s.delete(row)
    s.commit()


def idempotency_answer(s: Session, user_id: int, key: str) -> tuple | None:
    row = s.scalar(select(IdempotencyKey).where(
        IdempotencyKey.user_id == user_id, IdempotencyKey.key == key))
    if row is None or not row.status_code:
        return None
    return row.status_code, row.body


def idempotency_cleanup(s: Session) -> int:
    from sqlalchemy import delete as sql_delete
    result = s.execute(sql_delete(IdempotencyKey).where(
        IdempotencyKey.created_at < utcnow() - IDEMPOTENCY_TTL))
    s.commit()
    return result.rowcount or 0


#: How long the voice agent's audit trail is kept. Long enough to answer
#: "what did it do last month?", short enough that a busy account does not
#: grow the table without end.
AGENT_AUDIT_KEEP = timedelta(days=180)


def agent_audit_cleanup(s: Session) -> int:
    from sqlalchemy import delete as sql_delete
    from db import AgentAudit
    result = s.execute(sql_delete(AgentAudit).where(
        AgentAudit.created_at < utcnow() - AGENT_AUDIT_KEEP))
    s.commit()
    return result.rowcount or 0


# ---------------------------------------------------------------------------
# Referrals
# ---------------------------------------------------------------------------
#
# One level only: A invites B. If B then invites C, C belongs to B and nobody
# gets credit twice.

#: How many real actions the invited person must take before the referral
#: counts.
REFERRAL_QUALIFY_ACTIONS = 3

#: The prefix that marks a Telegram start parameter as one of ours.
REFERRAL_PREFIX = "ref_"

#: Length of the random part — about 60 bits.
REFERRAL_CODE_BYTES = 8

#: Codes are matched exactly against this.
REFERRAL_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{6,32}$")

#: Qualified referrals -> status key.
REFERRAL_LEVELS = [
    (0, "new"),
    (1, "inviter"),
    (3, "builder"),
    (5, "connector"),
    (10, "ambassador"),
]


def get_or_create_referral_code(s: Session, user_id: int) -> str:
    """This account's invite code, generated once and stable for ever."""
    for _ in range(5):
        row = s.get(ReferralCode, user_id)
        if row is not None:
            return row.code
        row = ReferralCode(user_id=user_id,
                           code=secrets.token_urlsafe(REFERRAL_CODE_BYTES))
        s.add(row)
        try:
            with s.begin_nested():
                s.flush()
        except IntegrityError:
            s.expunge(row)
            continue
        s.commit()
        return s.get(ReferralCode, user_id).code
    raise RuntimeError("could not allocate a referral code")


def parse_referral_payload(payload: str | None) -> str | None:
    """`ref_<code>` -> `<code>`, or None for anything else."""
    if not payload or not isinstance(payload, str):
        return None
    if not payload.startswith(REFERRAL_PREFIX):
        return None
    code = payload[len(REFERRAL_PREFIX):]
    return code if REFERRAL_CODE_RE.match(code) else None


def claim_referral(s: Session, referred_user_id: int, payload: str | None, *,
                   source: str = "bot", newly_created: bool = False) -> bool:
    """Attribute a brand-new account to whoever invited them. Returns success."""
    if not newly_created:
        return False

    code = parse_referral_payload(payload)
    if code is None:
        return False

    inviter_id = s.scalar(select(ReferralCode.user_id)
                          .where(ReferralCode.code == code))
    if inviter_id is None or inviter_id == referred_user_id:
        return False

    if s.get(Referral, referred_user_id) is not None:
        return False

    s.add(Referral(referred_user_id=referred_user_id, inviter_user_id=inviter_id,
                   source=source if source in ("bot", "miniapp") else "bot",
                   status="pending"))
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        return False
    return True


def maybe_qualify_referral(s: Session, user_id: int) -> int | None:
    """Promote a pending referral once the invited person genuinely arrived."""
    referral = s.get(Referral, user_id)
    if referral is None or referral.status != "pending":
        return None

    user = s.get(User, user_id)
    if user is None or not user.onboarded:
        return None
    if (user.actions_count or 0) < REFERRAL_QUALIFY_ACTIONS:
        return None

    won = s.execute(
        sql_update(Referral).where(Referral.referred_user_id == user_id,
                                   Referral.status == "pending")
        .values(status="qualified", qualified_at=utcnow())
        .execution_options(synchronize_session=False)).rowcount
    s.commit()
    if not won:
        return None
    s.expire(referral)
    # The friend gets a few days of Pro on arrival; the inviter is paid in
    # steps (5 / 10 / 20 friends), each once.
    plans.referral_bonus(s, user_id)
    inviter = referral.inviter_user_id
    plans.referral_rewards(s, inviter, referral_stats(s, inviter)["counts"]["qualified"])
    s.commit()
    return referral.inviter_user_id


def referral_level(qualified: int) -> dict:
    """Status key and the next milestone, from the qualified count alone."""
    key, minimum = REFERRAL_LEVELS[0][1], REFERRAL_LEVELS[0][0]
    nxt = None
    for threshold, name in REFERRAL_LEVELS:
        if qualified >= threshold:
            key, minimum = name, threshold
        else:
            nxt = threshold
            break
    return {
        "key": key,
        "minimum": minimum,
        "next": None if nxt is None else {"target": nxt,
                                          "remaining": nxt - qualified},
    }


def referral_stats(s: Session, user_id: int) -> dict:
    """Aggregate counts for one inviter. Never the identity of the invited."""
    rows = s.execute(
        select(Referral.status, func.count())
        .where(Referral.inviter_user_id == user_id)
        .group_by(Referral.status)).all()
    counts = {status: n for status, n in rows}
    qualified = counts.get("qualified", 0)
    pending = counts.get("pending", 0)
    return {
        "counts": {"total": qualified + pending,
                   "pending": pending, "qualified": qualified},
        "level": referral_level(qualified),
    }


def platform_referral_stats(s: Session) -> dict:
    """Aggregate-only growth numbers for the operator channel."""
    rows = s.execute(select(Referral.status, func.count())
                     .group_by(Referral.status)).all()
    counts = {status: n for status, n in rows}
    qualified = counts.get("qualified", 0)
    total = qualified + counts.get("pending", 0)
    inviters = s.scalar(select(func.count(func.distinct(Referral.inviter_user_id)))) or 0
    return {
        "referrals_total": total,
        "referrals_pending": counts.get("pending", 0),
        "referrals_qualified": qualified,
        "referral_inviters": inviters,
        "referral_conversion": round(qualified / total * 100, 1) if total else 0.0,
    }


# ---------------------------------------------------------------------------
# Personal progression — score, XP, levels
# ---------------------------------------------------------------------------
#
# The daily score *is* the overall percentage — `day_score` — rather than a
# parallel calculation: two numbers that both claim to be "how today went" and
# disagree by a few points on two screens is the most corrosive thing this
# codebase could do to its own credibility.
#
# Progression is the secondary signal. Home leads with the day's plan and its
# one number; level, XP, rank and achievements live behind the avatar, where
# somebody can look at them when they want to.

#: Score -> grade. The bottom band is "Reset", not "Failed".
GRADE_BANDS = [(90, "S"), (80, "A"), (70, "B"), (60, "C"), (40, "D"), (0, "E")]

#: The grade stored for a day that had nothing in it to measure.
UNMEASURED_GRADE = "-"

#: A day at or above this is a Perfect Day.
PERFECT_DAY_SCORE = 90

#: What a day has to reach to count toward the consistency streak.
STREAK_THRESHOLD = 60

#: Missing a day does not have to cost a month. Two protected days per month.
RECOVERY_DAYS_PER_MONTH = 2

#: How many days away before returning counts as a comeback, and how long
#: before another one can be earned.
COMEBACK_AFTER_DAYS = 3
COMEBACK_COOLDOWN_DAYS = 14

#: Local days on record before a user is ranked.
RANK_MIN_DAYS = 7

#: The rolling windows the two ranks are computed over, in calendar days.
RANK_WINDOW_DAYS = 30
WEEKLY_WINDOW_DAYS = 7

#: (threshold XP, key, roman numeral). The level is always computed from
#: `xp_total` rather than stored.
PERSONAL_LEVELS = [
    (0, "starter", "I"),
    (500, "builder", "II"),
    (1500, "operator", "III"),
    (3500, "architect", "IV"),
    (7000, "commander", "V"),
    (15000, "elite", "VI"),
    (30000, "master", "VII"),
]

#: What each kind of event is worth. Awarded once per key, ever.
XP_VALUES = {
    "task": 10,
    "ritual_wake": 5,
    "ritual_prayer": 10,
    "ritual_journal": 5,
    "habit": 5,
    "focus": 10,
    "perfect_day": 25,
    "streak_7": 50,
    "streak_30": 200,
    "comeback": 15,
    "onboarding": 40,
    "achievement": 20,
}

#: The most XP ordinary activity can produce in one local day.
XP_DAILY_CAP = 120

#: Milestones, paid outside the cap.
XP_UNCAPPED = {"perfect_day", "streak", "comeback", "onboarding", "achievement"}


def grade_for(score: int) -> str:
    """The letter a score falls into."""
    for threshold, letter in GRADE_BANDS:
        if score >= threshold:
            return letter
    return "E"


def get_personal_level(xp: int) -> dict:
    """Everything the UI needs about where this XP total sits on the ladder."""
    xp = max(int(xp or 0), 0)
    index = 0
    for i, (threshold, _, _) in enumerate(PERSONAL_LEVELS):
        if xp >= threshold:
            index = i
    threshold, key, numeral = PERSONAL_LEVELS[index]
    nxt = PERSONAL_LEVELS[index + 1] if index + 1 < len(PERSONAL_LEVELS) else None

    if nxt is None:
        return {"key": key, "number": index + 1, "numeral": numeral,
                "current_threshold": threshold, "next_threshold": None,
                "next_key": None, "remaining": 0, "progress": 1.0, "xp": xp}

    span = nxt[0] - threshold
    return {
        "key": key, "number": index + 1, "numeral": numeral,
        "current_threshold": threshold, "next_threshold": nxt[0],
        "next_key": nxt[1], "remaining": nxt[0] - xp,
        "progress": round((xp - threshold) / span, 4) if span else 1.0,
        "xp": xp,
    }


def _progress_row(s: Session, user_id: int) -> UserProgress:
    """This user's summary row, created empty on first sight."""
    row = s.get(UserProgress, user_id)
    if row is None:
        try:
            with s.begin_nested():
                row = UserProgress(user_id=user_id)
                s.add(row)
        except IntegrityError:
            row = s.get(UserProgress, user_id)
            if row is None:
                raise
    return row


def award_xp(s: Session, user_id: int, event_key: str, event_type: str,
             xp: int, day: date) -> int:
    """Write one XP event if it has never been written. Returns XP granted."""
    if xp <= 0:
        return 0

    if event_type not in XP_UNCAPPED:
        earned = s.scalar(select(func.coalesce(func.sum(XPEvent.xp), 0)).where(
            XPEvent.user_id == user_id, XPEvent.event_date == day,
            XPEvent.event_type.not_in(XP_UNCAPPED))) or 0
        if earned >= XP_DAILY_CAP:
            return 0
        xp = min(xp, XP_DAILY_CAP - earned)

    try:
        with s.begin_nested():
            s.add(XPEvent(user_id=user_id, event_key=event_key,
                          event_type=event_type, xp=xp, event_date=day))
    except IntegrityError:
        return 0
    return xp


def xp_total(s: Session, user_id: int) -> int:
    """Sum of the ledger. The only definition of somebody's XP there is."""
    return int(s.scalar(select(func.coalesce(func.sum(XPEvent.xp), 0))
                        .where(XPEvent.user_id == user_id)) or 0)


def recompute_daily_score(s: Session, user_id: int, ws: int,
                          day: date, *, close: bool = False) -> DailyScore:
    """Write (or rewrite) one day's score row from that day's live data.

    A closed row is final and is returned untouched: the day it describes is
    over, and nothing done afterwards may rewrite what it was. `close=True`
    is the day-close job finishing a day.
    """
    row = s.scalar(select(DailyScore).where(DailyScore.user_id == user_id,
                                            DailyScore.day == day))
    if row is not None and row.closed:
        return row

    score = day_score(s, ws, day, live=True)
    components = score["components"]
    total = score["value"]
    measured = score["measured"]
    tasks_done, tasks_total = today_task_progress(s, ws, day)
    habits_done, habits_total = habit_progress(s, ws, day)
    prayer = prayer_state(s, ws, day, getattr(s.get(User, user_id), "gender", None))

    if row is None:
        try:
            with s.begin_nested():
                row = DailyScore(user_id=user_id, day=day)
                s.add(row)
        except IntegrityError:
            row = s.scalar(select(DailyScore).where(
                DailyScore.user_id == user_id, DailyScore.day == day))
            if row is None:
                raise

    def part(name: str) -> int:
        value = components.get(name)
        return -1 if value is None else int(value)

    row.task_score = part("tasks")
    row.habit_score = part("habits")
    row.team_score = part("team")
    # The week goal left the score, but the ranking still reads how the week's
    # goals went (`performance_index`), so its percentage is kept here.
    focus_done, focus_total = focus_progress(s, ws, day)
    row.focus_score = round(focus_done / focus_total * 100) if focus_total else -1
    row.prayer_score = part("prayer")
    row.total_score = int(total)
    row.measured = measured
    row.grade = grade_for(int(total)) if measured else UNMEASURED_GRADE
    row.tasks_done, row.tasks_total = tasks_done, tasks_total
    row.habits_done, row.habits_total = habits_done, habits_total
    row.prayer_performed = prayer["performed"]
    row.formula = SCORE_FORMULA
    if close:
        row.closed = True

    try:
        s.flush()
    except IntegrityError:
        s.rollback()
        row = s.scalar(select(DailyScore).where(DailyScore.user_id == user_id,
                                                DailyScore.day == day))
        if row is None:
            raise
    return row


def close_day(s: Session, user_id: int, day: date) -> DailyScore | None:
    """Finish one past day for one user: the personal snapshot and the teams'.

    Idempotent. The day must be over in the user's own zone; a day still
    running is never closed.
    """
    user = s.get(User, user_id)
    if user is None:
        return None
    ws = s.scalar(select(Workspace.id).where(Workspace.user_id == user_id))
    if ws is None:
        return None
    tz = user_tz(user)
    if day >= today_local(tz):
        return None
    row = recompute_daily_score(s, user_id, ws, day, close=True)
    for team in teams_for_on(s, user_id, day, tz=tz):
        close_team_member_day(s, team.id, user_id, day, tz=tz)
    s.commit()
    return row


#: How many users the day-close job finishes per tick. A tick is every few
#: minutes, so everybody is reached well within the first hour of their day.
CLOSE_BATCH = 300


def close_due_days(s: Session, *, limit: int = CLOSE_BATCH) -> int:
    """Close yesterday for everybody whose day has turned. Returns how many.

    Also closes any older row left open by the action funnel — a user who
    wrote on Monday and never came back still gets Monday finished.
    """
    closed = 0
    users = s.execute(select(User.telegram_id, User.timezone)
                      .where(User.onboarded.is_(True))).all()
    for telegram_id, zone in users:
        if closed >= limit:
            break
        yesterday = today_local(tz_for(zone)) - timedelta(days=1)
        pending = [d for d in s.scalars(select(DailyScore.day).where(
            DailyScore.user_id == telegram_id, DailyScore.day <= yesterday,
            or_(DailyScore.closed.is_(None), DailyScore.closed.is_(False)))).all()]
        has_yesterday = s.scalar(select(DailyScore.id).where(
            DailyScore.user_id == telegram_id, DailyScore.day == yesterday,
            DailyScore.closed.is_(True))) is not None
        if not has_yesterday and yesterday not in pending:
            pending.append(yesterday)
        for day in sorted(pending)[-7:]:
            try:
                close_day(s, telegram_id, day)
                closed += 1
            except Exception:
                s.rollback()
                log.exception("could not close %s for %s", day, telegram_id)
    return closed


def sync_day_xp(s: Session, user_id: int, ws: int, day: date) -> int:
    """Award every XP event today's state has earned. Returns XP newly granted."""
    granted = 0

    start, end = utc_window(day)
    task_ids = s.scalars(select(Task.id).where(
        Task.workspace_id == ws, Task.status == "done",
        Task.completed_at >= start, Task.completed_at < end)).all()
    for task_id in task_ids:
        granted += award_xp(s, user_id, f"task:{task_id}", "task",
                            XP_VALUES["task"], day)

    rows = s.execute(select(Habit.id, Habit.system_key)
                     .join(HabitLog, HabitLog.habit_id == Habit.id)
                     .where(HabitLog.workspace_id == ws, HabitLog.day == day,
                            HabitLog.done.is_(True))).all()
    SYSTEM_XP = {SYSTEM_WAKEUP: ("ritual_wake", "wake"),
                 SYSTEM_PRAYER: ("ritual_prayer", "prayer"),
                 SYSTEM_JOURNAL: ("ritual_journal", "journal")}
    for habit_id, system_key in rows:
        value_key, event_type = SYSTEM_XP.get(system_key, ("habit", "task"))
        granted += award_xp(s, user_id, f"habit:{habit_id}:{day}", event_type,
                            XP_VALUES[value_key], day)

    # Goals scored as goals — a goal delivered by a task was paid as the task.
    focus_ids = s.scalars(select(WeeklyFocus.id).where(
        WeeklyFocus.workspace_id == ws, WeeklyFocus.done.is_(True),
        WeeklyFocus.task_id.is_(None),
        WeeklyFocus.week_start == week_start(day))).all()
    for focus_id in focus_ids:
        granted += award_xp(s, user_id, f"focus:{focus_id}", "focus",
                            XP_VALUES["focus"], day)

    return granted


def _month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _apply_day_to_streak(progress: UserProgress, day: date, score: int,
                         measured: bool = True) -> dict:
    """Move the streak on by one day. Returns what happened, for the caller.

    A day with nothing in it to measure moves nothing: it is neither a good
    day nor a missed one, and a new account's empty first morning must not
    read as a failure.
    """
    result = {"streak_changed": False, "recovery_used": False,
              "comeback": False, "gap": 0}

    if not measured:
        return result

    last = progress.last_score_date
    if last == day:
        return result

    if progress.recovery_month != _month_key(day):
        progress.recovery_month = _month_key(day)
        progress.recovery_used = 0

    good = score >= STREAK_THRESHOLD
    gap = (day - last).days if last else None
    result["gap"] = gap or 0

    if last is None or gap is not None and gap > 1:
        if gap is not None and gap - 1 >= COMEBACK_AFTER_DAYS and good:
            cooldown = progress.last_comeback_date
            if (cooldown is None
                    or (day - cooldown).days >= COMEBACK_COOLDOWN_DAYS):
                result["comeback"] = True
                progress.last_comeback_date = day
        progress.current_streak = 1 if good else 0
    elif gap == 1:
        if good:
            progress.current_streak = (progress.current_streak or 0) + 1
        elif progress.recovery_used < RECOVERY_DAYS_PER_MONTH:
            progress.recovery_used += 1
            result["recovery_used"] = True
        else:
            progress.current_streak = 0
    else:
        return result

    progress.last_score_date = day
    progress.best_streak = max(progress.best_streak or 0,
                               progress.current_streak or 0)
    result["streak_changed"] = True
    return result


def _eligible_window(s: Session, user_id: int, today: date,
                     days: int) -> tuple[date, int]:
    """(first day, how many calendar days) the rolling index is averaged over."""
    first_scored = s.scalar(select(func.min(DailyScore.day))
                            .where(DailyScore.user_id == user_id))
    start = today - timedelta(days=days - 1)
    if first_scored and first_scored > start:
        start = first_scored
    return start, (today - start).days + 1


def performance_index(s: Session, user_id: int, today: date,
                      days: int = RANK_WINDOW_DAYS) -> float:
    """How this user has actually been doing lately, 0-100. The ranking metric.

        70%  average daily score across the window
        20%  consistency — the share of days that cleared the streak threshold
        10%  weekly goal follow-through
    """
    start, span = _eligible_window(s, user_id, today, days)
    if span <= 0:
        return 0.0

    rows = s.execute(select(DailyScore.total_score, DailyScore.focus_score)
                     .where(DailyScore.user_id == user_id,
                            DailyScore.day >= start,
                            DailyScore.day <= today)).all()
    if not rows:
        return 0.0

    totals = [r[0] for r in rows]
    average = sum(totals) / span
    consistency = sum(1 for t in totals if t >= STREAK_THRESHOLD) / span * 100
    focus_days = [r[1] for r in rows if r[1] >= 0]
    focus = sum(focus_days) / len(focus_days) if focus_days else 0.0

    return round(0.70 * average + 0.20 * consistency + 0.10 * focus, 2)


def refresh_progress(s: Session, user_id: int, *, day: date | None = None,
                     tz: ZoneInfo | None = None) -> dict:
    """Bring one user's progression up to date. The single entry point.

    Callers commit.
    """
    user = s.get(User, user_id)
    if user is None:
        return {}
    ws = s.scalar(select(Workspace.id).where(Workspace.user_id == user_id))
    if ws is None:
        return {}

    zone = tz or user_tz(user)
    day = day or today_local(zone)

    progress = _progress_row(s, user_id)
    before_xp = progress.xp_total or 0
    before_level = get_personal_level(before_xp)["number"]

    score_row = recompute_daily_score(s, user_id, ws, day)
    measured = score_row.measured if score_row.measured is not None else True
    granted = sync_day_xp(s, user_id, ws, day)

    if measured and score_row.total_score >= PERFECT_DAY_SCORE:
        granted += award_xp(s, user_id, f"perfect_day:{user_id}:{day}",
                            "perfect_day", XP_VALUES["perfect_day"], day)

    moved = _apply_day_to_streak(progress, day, score_row.total_score, measured)

    if moved["comeback"]:
        granted += award_xp(s, user_id, f"comeback:{user_id}:{day}",
                            "comeback", XP_VALUES["comeback"], day)
    for milestone in (7, 30):
        if (progress.current_streak or 0) >= milestone:
            granted += award_xp(
                s, user_id, f"streak_{milestone}:{user_id}:{day}", "streak",
                XP_VALUES[f"streak_{milestone}"], day)

    progress.perfect_days = int(s.scalar(select(func.count()).select_from(XPEvent)
                                         .where(XPEvent.user_id == user_id,
                                                XPEvent.event_type == "perfect_day")) or 0)
    progress.xp_total = xp_total(s, user_id)
    progress.scored_days = int(s.scalar(select(func.count()).select_from(DailyScore)
                                        .where(DailyScore.user_id == user_id,
                                               or_(DailyScore.measured.is_(None),
                                                   DailyScore.measured.is_(True))))
                               or 0)
    progress.performance_index_30d = performance_index(s, user_id, day,
                                                       RANK_WINDOW_DAYS)
    progress.performance_index_7d = performance_index(s, user_id, day,
                                                      WEEKLY_WINDOW_DAYS)

    after_level = get_personal_level(progress.xp_total)["number"]
    unlocked = check_achievements(s, user_id, progress, score_row)

    return {
        "score": score_row.total_score,
        "measured": measured,
        "grade": score_row.grade,
        "xp_gained": granted,
        "xp_total": progress.xp_total,
        "level_up": after_level > before_level,
        "level": get_personal_level(progress.xp_total),
        "streak": progress.current_streak or 0,
        "perfect_day": measured and score_row.total_score >= PERFECT_DAY_SCORE,
        "achievements": unlocked,
        **moved,
    }


# ---------------------------------------------------------------------------
# Achievements
# ---------------------------------------------------------------------------

ACHIEVEMENTS = [
    ("first_step",      "scored_days",   1),
    ("perfect_day",     "perfect_days",  1),
    ("consistent",      "best_streak",   7),
    ("disciplined",     "best_streak",  30),
    ("century",         "tasks_done",  100),
    ("early_riser",     "wake_days",    30),
    ("focused",         "focus_done",   10),
    ("never_miss_twice", "recoveries",   1),
    ("comeback",        "comebacks",     1),
    ("architect",       "level",         4),
    ("commander",       "level",         5),
    ("elite",           "level",         6),
    ("master",          "level",         7),
]


def _achievement_values(s: Session, user_id: int,
                        progress: UserProgress) -> dict[str, int]:
    """Every number the achievement rules read, in one pass."""
    counts = dict(s.execute(
        select(XPEvent.event_type, func.count())
        .where(XPEvent.user_id == user_id)
        .group_by(XPEvent.event_type)).all())
    task_xp = int(s.scalar(select(func.count()).select_from(XPEvent).where(
        XPEvent.user_id == user_id,
        XPEvent.event_key.like("task:%"))) or 0)
    wake_xp = int(s.scalar(select(func.count()).select_from(XPEvent).where(
        XPEvent.user_id == user_id,
        XPEvent.event_key.like("habit:%"),
        XPEvent.event_type == "wake")) or 0)
    return {
        "scored_days": progress.scored_days or 0,
        "perfect_days": progress.perfect_days or 0,
        "best_streak": progress.best_streak or 0,
        "tasks_done": task_xp,
        "wake_days": wake_xp,
        "focus_done": counts.get("focus", 0),
        "recoveries": progress.recovery_used or 0,
        "comebacks": counts.get("comeback", 0),
        "level": get_personal_level(progress.xp_total or 0)["number"],
    }


def check_achievements(s: Session, user_id: int, progress: UserProgress,
                       score_row: DailyScore) -> list[str]:
    """Unlock whatever this user has now earned. Returns only what is new."""
    values = _achievement_values(s, user_id, progress)
    already = set(s.scalars(select(UserAchievement.achievement_key)
                            .where(UserAchievement.user_id == user_id)).all())

    unlocked: list[str] = []
    for key, field, target in ACHIEVEMENTS:
        if key in already or values.get(field, 0) < target:
            continue
        try:
            with s.begin_nested():
                s.add(UserAchievement(user_id=user_id, achievement_key=key))
        except IntegrityError:
            continue
        unlocked.append(key)
        award_xp(s, user_id, f"achievement:{user_id}:{key}", "achievement",
                 XP_VALUES["achievement"], score_row.day)

    if unlocked:
        progress.xp_total = xp_total(s, user_id)
    return unlocked


def achievement_state(s: Session, user_id: int) -> list[dict]:
    """Every achievement, unlocked or not, with progress where it is meaningful."""
    progress = s.get(UserProgress, user_id)
    if progress is None:
        values = {}
    else:
        values = _achievement_values(s, user_id, progress)

    rows = dict(s.execute(
        select(UserAchievement.achievement_key, UserAchievement.unlocked_at)
        .where(UserAchievement.user_id == user_id)).all())

    out = []
    for key, field, target in ACHIEVEMENTS:
        have = values.get(field, 0)
        out.append({
            "key": key,
            "unlocked": key in rows,
            "unlocked_at": rows[key].isoformat() if key in rows else None,
            "progress": min(have, target),
            "target": target,
        })
    return out


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def _rank_for(s: Session, column, value: float) -> tuple[int, int]:
    """(rank, eligible users) for a value in one of the index columns."""
    eligible = int(s.scalar(select(func.count()).select_from(UserProgress)
                            .where(UserProgress.scored_days >= RANK_MIN_DAYS)) or 0)
    ahead = int(s.scalar(select(func.count()).select_from(UserProgress)
                         .where(UserProgress.scored_days >= RANK_MIN_DAYS,
                                column > value)) or 0)
    return ahead + 1, eligible


def global_rank(s: Session, user_id: int) -> dict:
    """Where this user stands, and whether they stand anywhere yet."""
    progress = s.get(UserProgress, user_id)
    if progress is None or (progress.scored_days or 0) < RANK_MIN_DAYS:
        remaining = RANK_MIN_DAYS - ((progress.scored_days or 0) if progress else 0)
        return {"eligible": False, "days_remaining": max(remaining, 0),
                "global": None, "weekly": None, "best": None,
                "users": 0, "top_percent": None, "movement": None}

    rank, users = _rank_for(s, UserProgress.performance_index_30d,
                            progress.performance_index_30d or 0.0)
    weekly, _ = _rank_for(s, UserProgress.performance_index_7d,
                          progress.performance_index_7d or 0.0)

    previous = progress.last_global_rank
    if progress.best_global_rank is None or rank < progress.best_global_rank:
        progress.best_global_rank = rank
    progress.last_global_rank = rank

    return {
        "eligible": True,
        "days_remaining": 0,
        "global": rank,
        "weekly": weekly,
        "best": progress.best_global_rank,
        "users": users,
        "top_percent": (round(rank / users * 100)
                        if users and rank / users * 100 >= 1
                        else round(rank / users * 100, 1) if users else None),
        "movement": (previous - rank) if previous is not None else None,
    }


def progress_snapshot(s: Session, user_id: int, *,
                      tz: ZoneInfo | None = None) -> dict:
    """Everything the Progress sheet shows, for one user, about themselves.

    A day with nothing measured says so — score and grade are None — instead
    of handing a new account the bottom grade before it has done anything.
    """
    user = s.get(User, user_id)
    zone = tz or user_tz(user)
    today = today_local(zone)

    progress = s.get(UserProgress, user_id)
    if progress is None:
        level = get_personal_level(0)
        return {
            "daily": {"score": None, "grade": None, "measured": False,
                      "perfect_day": False,
                      "to_perfect": PERFECT_DAY_SCORE,
                      "breakdown": {"tasks": None, "habits": None,
                                    "team": None, "prayer": None},
                      "weights": OVERALL_WEIGHTS},
            "xp": {"total": 0, "today": 0, "cap": XP_DAILY_CAP},
            "level": level,
            "streak": {"current": 0, "best": 0,
                       "recovery_remaining": RECOVERY_DAYS_PER_MONTH,
                       "threshold": STREAK_THRESHOLD},
            "rank": global_rank(s, user_id),
            "perfect_days": 0,
            "scored_days": 0,
        }

    row = s.scalar(select(DailyScore).where(DailyScore.user_id == user_id,
                                            DailyScore.day == today))
    today_xp = int(s.scalar(select(func.coalesce(func.sum(XPEvent.xp), 0)).where(
        XPEvent.user_id == user_id, XPEvent.event_date == today)) or 0)

    def part(value: int | None) -> int | None:
        return None if value is None or value < 0 else value

    measured = bool(row) and (row.measured if row.measured is not None else True)
    used = (progress.recovery_used or 0) if progress.recovery_month == _month_key(today) else 0

    return {
        "daily": {
            "score": row.total_score if measured else None,
            "grade": row.grade if measured else None,
            "measured": measured,
            "perfect_day": bool(measured and row.total_score >= PERFECT_DAY_SCORE),
            "to_perfect": max(PERFECT_DAY_SCORE - (row.total_score if measured else 0), 0),
            "breakdown": {
                "tasks": part(row.task_score if row else None),
                "habits": part(row.habit_score if row else None),
                "team": part(row.team_score if row else None),
                "prayer": part(row.prayer_score if row else None),
            },
            "weights": OVERALL_WEIGHTS,
        },
        "xp": {"total": progress.xp_total or 0, "today": today_xp,
               "cap": XP_DAILY_CAP},
        "level": get_personal_level(progress.xp_total or 0),
        "streak": {
            "current": progress.current_streak or 0,
            "best": progress.best_streak or 0,
            "recovery_remaining": max(RECOVERY_DAYS_PER_MONTH - used, 0),
            "threshold": STREAK_THRESHOLD,
        },
        "rank": global_rank(s, user_id),
        "perfect_days": progress.perfect_days or 0,
        "scored_days": progress.scored_days or 0,
    }


#: "Qadam": every good thing done is a step. Levels by total steps.
STEP_LEVELS = [("starter", 0), ("builder", 26), ("discipline", 51), ("steady", 101), ("master", 201)]


def steps_snapshot(s: Session, user_id: int, ws: int, *, tz: ZoneInfo | None = None) -> dict:
    """Steps: today's checkpoints, the all-time total and the level.

    A step is one kind of good thing done on a day: at least one task, at
    least one habit, at least one prayer, the journal written. A week goal
    marked done is one more. Everything is read from rows that already exist
    (the day snapshots, the journal, the week goals); nothing new is stored.
    """
    zone = tz or user_tz(s.get(User, user_id))
    today = today_local(zone)
    modules = modules_for(s, ws)
    # Past days are counted in the database, not by loading every row of
    # history into the request (audit #43).
    past_steps = int(s.scalar(select(func.coalesce(func.sum(
        case((DailyScore.tasks_done > 0, 1), else_=0)
        + case((DailyScore.habits_done > 0, 1), else_=0)
        + case((DailyScore.prayer_performed > 0, 1), else_=0)), 0))
        .where(DailyScore.user_id == user_id, DailyScore.day != today)) or 0)

    def wrote(answers_json) -> bool:
        # The same rule as the journal habit: an answer with content (audit #33).
        try:
            return journal_is_written(json.loads(answers_json or "{}"))
        except (ValueError, TypeError):
            return False

    # Only the two columns needed — never the entry text (audit #43).
    journal_days = {day for day, answers in s.execute(
        select(JournalEntry.day, JournalEntry.answers)
        .where(JournalEntry.workspace_id == ws)).all() if wrote(answers)}
    goals = s.scalars(select(WeeklyFocus).where(WeeklyFocus.workspace_id == ws,
                                                WeeklyFocus.carried_to.is_(None))).all()
    linked = _linked_tasks(s, ws, list(goals))
    week_goals = [g for g in goals if g.week_start == week_start(today)]

    # Today is read live: its stored snapshot is only written when a score is
    # computed, and the first open of the day would otherwise show 0/0.
    promised_done, tasks_total = today_task_progress(s, ws, today, tz=zone, include_team=False)
    # Work finished today counts whatever date it carried — yesterday's
    # report finished today is today's work (audit #7).
    worked = tasks_completed_on(s, ws, today, tz=zone)
    habits_done, habits_total = habit_progress(s, ws, today, include_team=False)
    prayed = prayer_state(s, ws, today, getattr(s.get(User, user_id), "gender", None))["performed"]
    total = past_steps
    total += (worked > 0) + (habits_done > 0) + (prayed > 0)
    total += len(journal_days) + sum(1 for g in goals if focus_is_done(g, linked))

    # `ok` is "started" — one done is a step. `complete` is the whole plan
    # for the day; the two are never shown as the same thing (audit #10).
    checks = [
        {"key": "tasks", "done": worked, "total": tasks_total,
         "planned_done": promised_done,
         "complete": tasks_total > 0 and promised_done >= tasks_total},
        {"key": "habits", "done": habits_done, "total": habits_total,
         "complete": habits_total > 0 and habits_done >= habits_total},
    ]
    if modules.get("prayer"):
        checks.append({"key": "prayer", "done": prayed, "total": 5, "complete": prayed >= 5})
    if modules.get("journal"):
        wrote_today = int(today in journal_days)
        checks.append({"key": "journal", "done": wrote_today, "total": 1,
                       "complete": bool(wrote_today)})
    goals_done = sum(1 for g in week_goals if focus_is_done(g, linked))
    # No goal this week is an offer to add one, not an unmet step (audit #42).
    checks.append({"key": "goal", "done": goals_done, "total": len(week_goals),
                   "empty": not week_goals,
                   "complete": bool(week_goals) and goals_done >= len(week_goals)})
    for c in checks:
        c["ok"] = c["done"] > 0
        c.setdefault("empty", False)

    index = max(i for i, (_k, low) in enumerate(STEP_LEVELS) if total >= low)
    nxt = STEP_LEVELS[index + 1][1] if index + 1 < len(STEP_LEVELS) else None
    progress = s.get(UserProgress, user_id)
    used = (progress.recovery_used or 0) if progress and progress.recovery_month == _month_key(today) else 0
    return {
        "total": total,
        "level": index + 1,
        "level_key": STEP_LEVELS[index][0],
        "next": nxt,
        "levels": [{"key": k, "min": low,
                    "max": (STEP_LEVELS[i + 1][1] - 1) if i + 1 < len(STEP_LEVELS) else None}
                   for i, (k, low) in enumerate(STEP_LEVELS)],
        "today": checks,
        "today_done": sum(c["ok"] for c in checks if not c["empty"]),
        "today_counted": sum(1 for c in checks if not c["empty"]),
        "freeze": max(RECOVERY_DAYS_PER_MONTH - used, 0),
    }


def platform_progress_stats(s: Session) -> dict:
    """Aggregate progression numbers for the operator. No personal content."""
    ranked = int(s.scalar(select(func.count()).select_from(UserProgress)
                          .where(UserProgress.scored_days >= RANK_MIN_DAYS)) or 0)
    today = today_local()
    avg = s.scalar(select(func.avg(DailyScore.total_score))
                   .where(DailyScore.day == today,
                          or_(DailyScore.measured.is_(None),
                              DailyScore.measured.is_(True))))
    perfect = int(s.scalar(select(func.count()).select_from(DailyScore).where(
        DailyScore.day == today,
        DailyScore.total_score >= PERFECT_DAY_SCORE)) or 0)
    streaks = int(s.scalar(select(func.count()).select_from(UserProgress)
                           .where(UserProgress.current_streak > 0)) or 0)
    xp_today = int(s.scalar(select(func.coalesce(func.sum(XPEvent.xp), 0))
                            .where(XPEvent.event_date == today)) or 0)

    levels: dict[str, int] = {}
    for (xp,) in s.execute(select(UserProgress.xp_total)).all():
        key = get_personal_level(xp or 0)["key"]
        levels[key] = levels.get(key, 0) + 1

    return {
        "avg_daily_score": round(float(avg), 1) if avg is not None else 0.0,
        "perfect_days_today": perfect,
        "active_streaks": streaks,
        "rank_eligible_users": ranked,
        "xp_today": xp_today,
        "users_by_level": levels,
    }


# ---------------------------------------------------------------------------
# Daily reports
# ---------------------------------------------------------------------------

def already_sent(s: Session, ws: int, report_type: str, report_date: date) -> bool:
    """True when this report was already claimed by someone."""
    return s.scalar(select(DailyReportLog.id).where(
        DailyReportLog.workspace_id == ws,
        DailyReportLog.report_type == report_type,
        DailyReportLog.report_date == report_date)) is not None


#: Returned by `claim_report` when the outbox itself could not be written but
#: the day was claimed another way.
FALLBACK_CLAIM = -1


def _fallback_claim_name(ws: int, report_type: str) -> str:
    """The `job_runs` key standing in for one workspace's outbox row."""
    return f"rep:{ws}:{report_type}"


#: Claims refused by the database for a reason that is not a peer worker.
CLAIM_ANOMALIES: dict = {}


#: How many times one report may be attempted in a day before it is given up on.
REPORT_MAX_ATTEMPTS = 3


def claim_report(s: Session, ws: int, report_type: str,
                 report_date: date) -> int | None:
    """Try to own this report. Returns the outbox id, or None if someone else won."""
    row = DailyReportLog(workspace_id=ws, report_type=report_type,
                         report_date=report_date, status="claimed")
    s.add(row)
    try:
        s.commit()
        return row.id
    except IntegrityError:
        s.rollback()

    existing = s.scalar(select(DailyReportLog).where(
        DailyReportLog.workspace_id == ws,
        DailyReportLog.report_type == report_type,
        DailyReportLog.report_date == report_date))
    if existing is None:
        CLAIM_ANOMALIES["count"] = CLAIM_ANOMALIES.get("count", 0) + 1
        CLAIM_ANOMALIES["last"] = f"{report_type} {report_date} ws={ws}"
        seen = f"{report_type}:{report_date}"
        if CLAIM_ANOMALIES.get("reported") != seen:
            CLAIM_ANOMALIES["reported"] = seen
            log.error(
                "claim_report was rejected for %s %s although no row holds "
                "the slot — daily_report_logs has a unique constraint this "
                "code does not expect. Falling back to job_runs for the "
                "once-a-day guarantee so reports still go out; restart the "
                "app to repair the schema properly. (first seen on "
                "workspace=%s; further occurrences today are counted, not "
                "logged)",
                report_type, report_date, ws)

        if claim_job_run(s, _fallback_claim_name(ws, report_type), report_date):
            return FALLBACK_CLAIM
        return None
    if existing.status == "retry" and (existing.attempts or 0) < REPORT_MAX_ATTEMPTS:
        existing.status = "claimed"
        existing.claimed_at = utcnow()
        s.commit()
        return existing.id
    return None


def mark_report_sent(s: Session, report_id: int) -> None:
    row = s.get(DailyReportLog, report_id)
    if row is not None:
        row.status = "sent"
        row.sent_at = utcnow()
        row.attempts += 1
        s.commit()


def mark_report_failed(s: Session, report_id: int, error: str, *,
                       permanent: bool = True) -> None:
    """Record the failure, and decide whether today is over for this report."""
    row = s.get(DailyReportLog, report_id)
    if row is None:
        return
    row.attempts = (row.attempts or 0) + 1
    row.last_error = str(error)[:200]
    exhausted = row.attempts >= REPORT_MAX_ATTEMPTS
    row.status = "failed" if (permanent or exhausted) else "retry"
    s.commit()


def release_report(s: Session, report_id: int, *, ws: int | None = None,
                   report_type: str | None = None,
                   report_date: date | None = None) -> None:
    """Drop a claim entirely, so the next run may try again from scratch."""
    if report_id == FALLBACK_CLAIM:
        if ws is not None and report_type and report_date:
            release_job_run(s, _fallback_claim_name(ws, report_type), report_date)
        return
    row = s.get(DailyReportLog, report_id)
    if row is not None:
        s.delete(row)
        s.commit()


#: How long a row may sit in `claimed` before the next tick treats it as
#: abandoned.
STALE_CLAIM_MINUTES = 30


def reclaim_stale_claims(s: Session, report_date: date) -> int:
    """Free claims whose worker died, and return how many were freed."""
    cutoff = utcnow() - timedelta(minutes=STALE_CLAIM_MINUTES)
    rows = s.scalars(select(DailyReportLog).where(
        DailyReportLog.report_date == report_date,
        DailyReportLog.status.in_(("claimed", "retry")),
        DailyReportLog.claimed_at < cutoff)).all()
    for row in rows:
        s.delete(row)
    if rows:
        s.commit()
    return len(rows)


def claim_job_run(s: Session, job_name: str, run_date: date) -> bool:
    """Claim today's run of a platform-wide job. True means "you send it"."""
    s.add(JobRun(job_name=job_name, run_date=run_date))
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        return False
    return True


def release_job_run(s: Session, job_name: str, run_date: date) -> None:
    """Hand the claim back so the next tick may try again."""
    row = s.scalar(select(JobRun).where(JobRun.job_name == job_name,
                                        JobRun.run_date == run_date))
    if row is not None:
        s.delete(row)
        s.commit()


def job_last_run(s: Session, job_name: str) -> date | None:
    """The most recent date this job claimed. For the operator's own check."""
    return s.scalar(select(func.max(JobRun.run_date))
                    .where(JobRun.job_name == job_name))


def mark_sent(s: Session, ws: int, report_type: str, report_date: date) -> None:
    """Compatibility shim: claim and immediately mark as sent."""
    report_id = claim_report(s, ws, report_type, report_date)
    if report_id is not None:
        mark_report_sent(s, report_id)


def morning_data(s: Session, ws: int, user: User) -> dict:
    """Yesterday's summary plus today's plan (the morning report)."""
    tz = user_tz(user)
    today = today_local(tz)
    yesterday = today - timedelta(days=1)

    y_done, y_total = habit_progress(s, ws, yesterday)
    y_prayer = prayer_state(s, ws, yesterday, user.gender)
    y_from, y_to = utc_window(yesterday, tz=tz)
    y_completed = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.status == "done",
        Task.completed_at >= y_from, Task.completed_at < y_to)) or 0
    y_missed = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline == yesterday)) or 0

    tasks = list_tasks(s, ws, horizon_days=0, tz=tz)
    focus = list_focus(s, ws, tz=tz)
    top3 = top3_tasks(s, ws, today, tz=tz)

    today_habits = [h for h in list_habits(s, ws, today, tz=tz)
                    if h["due"] and h["scored"]]

    y_score = day_score(s, ws, yesterday, tz=tz)

    return {
        "name": user.first_name or "",
        "yesterday": {
            "date": yesterday.isoformat(),
            "overall": y_score["value"],
            "measured": y_score["measured"],
            "components": y_score["components"],
            "habits_done": y_done, "habits_total": y_total,
            "prayer_score": y_prayer["score"],
            "prayer_performed": y_prayer["performed"],
            "prayer_required": PRAYER_REQUIRED,
            "prayer_owed": prayer_owed(s, ws, yesterday),
            "tasks_completed": y_completed, "tasks_missed": y_missed,
            "journal": journal_done(s, ws, yesterday, tz=tz),
        },
        "today": {
            "date": today.isoformat(),
            "tasks": tasks_due_today(s, ws, tz=tz),
            "top3": top3,
            "overdue": tasks["overdue"],
            "focus": focus,
            "focus_done": sum(1 for f in focus if f["done"]),
            "birthdays": [b for b in list_birthdays(s, ws, within_days=1, tz=tz)],
            "countdowns": list_countdowns(s, ws, tz=tz, include_past=False),
            "habits": [h["name"] for h in today_habits if not h["done"]],
            "habits_done": sum(1 for h in today_habits if h["done"]),
            "habits_total": len(today_habits),
            "prayer_required": PRAYER_REQUIRED,
            "prayer_owed": prayer_owed(s, ws, today),
        },
    }


def evening_data(s: Session, ws: int, user: User) -> dict:
    """Today's progress so far (the evening report). No next-day plan."""
    tz = user_tz(user)
    today = today_local(tz)
    done, total = habit_progress(s, ws, today)
    prayer = prayer_state(s, ws, today, user.gender)

    day_from, day_to = utc_window(today, tz=tz)
    completed = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.status == "done",
        Task.completed_at >= day_from, Task.completed_at < day_to)) or 0

    remaining = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline == today)).all()
    overdue = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline < today)).all()

    habits = list_habits(s, ws, today, tz=tz)
    focus = list_focus(s, ws, tz=tz)

    return {
        "date": today.isoformat(),
        "overall": overall_state(s, ws, today),
        "habits_done": done, "habits_total": total,
        "habits_remaining": [h["name"] for h in habits
                             if h["due"] and not h["done"] and h["scored"]],
        "prayer_score": prayer["score"],
        "prayer_performed": prayer["performed"],
        "prayer_required": PRAYER_REQUIRED,
        "prayer_owed": prayer_owed(s, ws, today),
        "tasks_completed": completed,
        "tasks_remaining": [t.title for t in remaining],
        "tasks_overdue": [t.title for t in overdue],
        "focus": focus,
        "focus_done": sum(1 for f in focus if f["done"]),
        "journal": journal_done(s, ws, today, tz=tz),
        "journal_on": s.scalar(select(Habit.id).where(
            Habit.workspace_id == ws, Habit.system_key == SYSTEM_JOURNAL,
            Habit.archived_at.is_(None))) is not None,
        "countdowns": list_countdowns(s, ws, tz=tz, include_past=False),
    }


def active_recipients(s: Session) -> list[tuple[int, int, str]]:
    """(telegram_id, workspace_id, language) for every user who should get reports.

    The rule is `dependencies.trial_state` written as SQL: onboarded, and
    either no channel configured, in the channel, or still inside the free run.
    """
    import dependencies as deps

    allowed = [User.onboarded.is_(True)]
    if deps.REQUIRED_CHANNEL_ID:
        allowed.append(or_(
            User.is_subscribed.is_(True),
            func.coalesce(User.actions_count, 0) < deps.FREE_ACTIONS,
        ))

    rows = s.execute(
        select(User.telegram_id, Workspace.id, User.language)
        .join(Workspace, Workspace.user_id == User.telegram_id)
        .where(*allowed)
    ).all()
    return [(r[0], r[1], r[2]) for r in rows]


# ---------------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------------

#: 05:00 matches `DEFAULT_WAKE_TIME`. This is only what NULL means; a chosen
#: time always wins.
DEFAULT_MORNING_TIME = dtime(5, 0)
DEFAULT_EVENING_TIME = dtime(21, 0)

#: How long after its configured time a report may still go out.
REPORT_WINDOW = timedelta(minutes=90)

#: How long after its moment a *missed* report may still be delivered.
REPORT_CATCHUP = timedelta(hours=6)
#: The same idea for task reminders.
REMINDER_WINDOW = timedelta(minutes=30)

#: How often the reminder job runs.
REMINDER_JOB_MINUTES = 5

#: How late a habit reminder may still go out. Wider than one job interval
#: on purpose: a job delayed by a deploy or a slow tick must not skip the
#: nudge (audit #35). `reminder_sent_at` keeps it to one per habit per day,
#: and a reminder older than this is dropped rather than sent stale.
HABIT_REMINDER_WINDOW = timedelta(minutes=30)


def prefs_for(user: User) -> dict:
    """The user's notification settings, with every NULL resolved."""
    return {
        "timezone": user.timezone or str(TZ),
        "morning_report": True if user.morning_report is None else bool(user.morning_report),
        "morning_time": (user.morning_time or DEFAULT_MORNING_TIME).strftime("%H:%M"),
        "evening_report": True if user.evening_report is None else bool(user.evening_report),
        "evening_time": (user.evening_time or DEFAULT_EVENING_TIME).strftime("%H:%M"),
        "task_reminders": True if user.task_reminders is None else bool(user.task_reminders),
        "habit_reminders": False if user.habit_reminders is None else bool(user.habit_reminders),
        "quiet_from": user.quiet_from.strftime("%H:%M") if user.quiet_from else "",
        "quiet_to": user.quiet_to.strftime("%H:%M") if user.quiet_to else "",
        "day_capacity": user.day_capacity or 0,
    }


def in_quiet_hours(user: User, now: datetime | None = None) -> bool:
    """Whether this local moment falls in the person's quiet hours.

    23:00–07:00 crosses midnight and is handled as two halves. Reminders in
    the window still arrive — silently — so nothing is lost (audit #37).
    """
    start, end = user.quiet_from, user.quiet_to
    if start is None or end is None or start == end:
        return False
    moment = (now or now_local(user_tz(user))).time()
    if start < end:
        return start <= moment < end
    return moment >= start or moment < end


#: The words of a reminder the phone shows by itself (see
#: `upcoming_notifications`). Kept short: a lock screen shows one line.
_LOCAL_TEXT = {
    "uz": {"task": "⏰ Vazifa vaqti", "habit": "🔁 Odat vaqti", "morning": "☀️ Kun rejasi",
           "morning_body": "Bugun nima muhim? Rejani oching.", "evening": "🌙 Kun yakuni",
           "evening_body": "Kunni yoping: kundalik va ertangi reja.", "bill": "💳 To'lov kuni"},
    "en": {"task": "⏰ Task time", "habit": "🔁 Habit time", "morning": "☀️ Today's plan",
           "morning_body": "What matters today? Open the plan.", "evening": "🌙 Day's end",
           "evening_body": "Close the day: journal and tomorrow's plan.", "bill": "💳 Payment due"},
    "ru": {"task": "⏰ Время задачи", "habit": "🔁 Время привычки", "morning": "☀️ План дня",
           "morning_body": "Что важно сегодня? Откройте план.", "evening": "🌙 Итог дня",
           "evening_body": "Закройте день: дневник и план на завтра.", "bill": "💳 День платежа"},
}


def upcoming_notifications(s: Session, ws: int, user: User, *, now: datetime | None = None,
                           hours: int = 48) -> list[dict]:
    """What the phone should remind about in the next `hours`, for a build
    that shows reminders by itself (no Firebase): task and habit reminders,
    the morning and evening reports, and repeating payments due.

    The same settings as the bot's own reminders apply. Each item has a
    stable `key`, so scheduling them again replaces rather than duplicates.
    Done tasks and habits already ticked today are left out; what changes
    later is corrected the next time the app opens.
    """
    tz = user_tz(user)
    now = now or now_local(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    horizon = now + timedelta(hours=hours)
    today = now.date()
    days = [today + timedelta(days=i) for i in range(hours // 24 + 1)]
    prefs = prefs_for(user)
    words = _LOCAL_TEXT.get(user.language or "uz", _LOCAL_TEXT["uz"])
    out: list[dict] = []

    def add(key: str, moment: datetime, title: str, body: str, opens: str) -> None:
        moment = moment.replace(tzinfo=tz) if moment.tzinfo is None else moment
        if now < moment <= horizon:
            out.append({"key": key, "at": moment.isoformat(), "title": title, "body": body[:180],
                        "silent": in_quiet_hours(user, moment.astimezone(tz).replace(tzinfo=None)),
                        "open": opens})

    if prefs["task_reminders"]:
        for task in s.scalars(select(Task).where(
                Task.workspace_id == ws, Task.archived_at.is_(None), Task.status == "waiting",
                Task.remind_before.isnot(None), Task.deadline.isnot(None),
                Task.deadline >= today, Task.deadline <= days[-1])).all():
            moment = datetime.combine(task.deadline, task.due_time or dtime(9, 0))
            add(f"task:{task.id}:{task.deadline.isoformat()}",
                moment - timedelta(minutes=task.remind_before or 0), words["task"], task.title, "tasks")

    if prefs["habit_reminders"]:
        for day in days:
            for habit in list_habits(s, ws, day, tz=tz):
                if not habit["due"] or not habit["remind_at"] or (day == today and habit["done"]):
                    continue
                hour, minute = (int(x) for x in habit["remind_at"].split(":"))
                add(f"habit:{habit['id']}:{day.isoformat()}", datetime.combine(day, dtime(hour, minute)),
                    words["habit"], habit["name"], "habits")

    for day in days:
        if prefs["morning_report"]:
            add(f"morning:{day.isoformat()}", datetime.combine(day, user.morning_time or DEFAULT_MORNING_TIME),
                words["morning"], words["morning_body"], "home")
        if prefs["evening_report"]:
            add(f"evening:{day.isoformat()}", datetime.combine(day, user.evening_time or DEFAULT_EVENING_TIME),
                words["evening"], words["evening_body"], "home")

    for sub in s.scalars(select(MoneySubscription).where(
            MoneySubscription.workspace_id == ws, MoneySubscription.archived_at.is_(None),
            MoneySubscription.next_due >= today, MoneySubscription.next_due <= days[-1])).all():
        amount = f"{int(sub.amount):,}".replace(",", " ")
        add(f"bill:{sub.id}:{sub.next_due.isoformat()}", datetime.combine(sub.next_due, dtime(10, 0)),
            words["bill"], f"{sub.name} — {amount}", "money")

    out.sort(key=lambda x: x["at"])
    return out[:60]


def save_prefs(s: Session, user: User, **fields) -> dict:
    """Write notification settings. Unknown or malformed values are ignored."""
    if "timezone" in fields and fields["timezone"]:
        name = str(fields["timezone"])[:40]
        try:
            ZoneInfo(name)
        except Exception:
            raise ValueError("unknown timezone")
        user.timezone = name
    for key in ("morning_report", "evening_report", "task_reminders",
                "habit_reminders"):
        if key in fields and fields[key] is not None:
            setattr(user, key, bool(fields[key]))
    for key in ("morning_time", "evening_time"):
        if key in fields and fields[key] is not None:
            setattr(user, key, fields[key])
    # An empty value switches quiet hours off.
    for key in ("quiet_from", "quiet_to"):
        if key in fields:
            setattr(user, key, fields[key])
    if fields.get("day_capacity") is not None:
        minutes = int(fields["day_capacity"])
        if not 0 <= minutes <= 18 * 60:
            raise ValueError("bad_capacity")
        user.day_capacity = minutes or None
    s.commit()
    return prefs_for(user)


SNOOZE_MINUTES = (15, 60, 180)
SNOOZE_KINDS = {"task": Task, "habit": Habit, "ttask": TeamTask, "thabit": TeamHabit}


def snooze_reminder(s: Session, ws: int, kind: str, item_id: int, minutes: int,
                    *, user_id: int | None = None) -> Snooze:
    """Ask for the same reminder again later. Changes nothing on the item."""
    if kind not in SNOOZE_KINDS or minutes not in SNOOZE_MINUTES:
        raise ValueError("bad_snooze")
    item = s.get(SNOOZE_KINDS[kind], item_id)
    if item is None or getattr(item, "archived_at", None) is not None:
        raise NotFound(kind)
    if kind in ("task", "habit") and item.workspace_id != ws:
        raise NotFound(kind)
    if kind in ("ttask", "thabit") and (user_id is None or team_for(s, user_id, item.team_id) is None):
        raise NotFound(kind)
    # One pending snooze per item: a second tap moves it, never doubles it.
    row = s.scalar(select(Snooze).where(Snooze.workspace_id == ws, Snooze.kind == kind,
                                        Snooze.item_id == item_id, Snooze.sent_at.is_(None)))
    if row is None:
        row = Snooze(workspace_id=ws, kind=kind, item_id=item_id, fire_at=utcnow())
        s.add(row)
    row.fire_at = utcnow() + timedelta(minutes=minutes)
    s.commit()
    return row


def due_snoozes(s: Session, ws: int, now: datetime | None = None) -> list[dict]:
    """Snoozed reminders whose time has come and whose item is still open.

    One already done (or gone) is closed quietly instead of being sent.
    """
    now = now or utcnow()
    tz = _workspace_tz(s, ws)
    today = today_local(tz)
    out, closed = [], False
    for row in s.scalars(select(Snooze).where(
            Snooze.workspace_id == ws, Snooze.sent_at.is_(None),
            Snooze.fire_at <= now)).all():
        item = s.get(SNOOZE_KINDS.get(row.kind, Task), row.item_id)
        if item is None or getattr(item, "archived_at", None) is not None \
                or _item_done_for_timer(s, ws, row.kind, item, today):
            row.sent_at, closed = now, True
            continue
        out.append({"id": row.id, "kind": row.kind, "item_id": row.item_id,
                    "title": getattr(item, "title", None) or getattr(item, "name", "")})
    if closed:
        s.commit()
    return out


def mark_snooze_sent(s: Session, snooze_id: int) -> None:
    row = s.get(Snooze, snooze_id)
    if row is not None and row.sent_at is None:
        row.sent_at = utcnow()
        s.commit()


def report_is_due(user: User, report_type: str, now: datetime) -> bool:
    """Whether this user's report should go out at this local moment."""
    prefs = prefs_for(user)
    if report_type == "morning":
        if not prefs["morning_report"]:
            return False
        target = user.morning_time or DEFAULT_MORNING_TIME
    else:
        if not prefs["evening_report"]:
            return False
        target = user.evening_time or DEFAULT_EVENING_TIME

    scheduled = datetime.combine(now.date(), target)
    if now < scheduled:
        return False

    end_of_day = datetime.combine(now.date(), dtime(23, 59, 59))
    limit = min(scheduled + REPORT_CATCHUP, end_of_day)
    return now <= limit


def due_task_reminders(s: Session, ws: int, user: User,
                       now: datetime | None = None) -> list[dict]:
    """Tasks whose reminder is due now and has not been sent."""
    if not prefs_for(user)["task_reminders"]:
        return []

    tz = user_tz(user)
    roll_recurring_daily(s, ws, tz)
    now = now or now_local(tz)
    today = now.date()

    rows = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.reminder_sent_at.is_(None),
        Task.remind_before.isnot(None),
        Task.deadline.isnot(None),
        Task.deadline <= today + timedelta(days=1))).all()

    due = []
    for task in rows:
        moment = datetime.combine(task.deadline, task.due_time or dtime(9, 0))
        fire = moment - timedelta(minutes=task.remind_before or 0)
        if fire <= now <= fire + REMINDER_WINDOW:
            due.append(_task_dict(s, ws, task, today))
    return due


def mark_reminder_sent(s: Session, ws: int, task_id: int,
                       tz: ZoneInfo | None = None) -> None:
    task = s.get(Task, task_id)
    if task is not None and task.workspace_id == ws:
        task.reminder_sent_at = utcnow()
        s.commit()


def due_habit_reminders(s: Session, ws: int, user: User,
                        now: datetime | None = None) -> list[dict]:
    """Habits with a reminder time that has just arrived and are still undone."""
    if not prefs_for(user)["habit_reminders"]:
        return []

    tz = user_tz(user)
    now = now or now_local(tz)
    today = now.date()

    already = set(s.scalars(select(HabitLog.habit_id).where(
        HabitLog.workspace_id == ws, HabitLog.day == today,
        HabitLog.reminder_sent_at.is_not(None))).all())

    out = []
    for habit in list_habits(s, ws, today, tz=tz):
        if not habit["due"] or habit["done"] or not habit["remind_at"]:
            continue
        if habit["id"] in already:
            continue
        hour, minute = (int(x) for x in habit["remind_at"].split(":"))
        fire = datetime.combine(today, dtime(hour, minute))
        if fire <= now < fire + HABIT_REMINDER_WINDOW:
            out.append(habit)
    return out


def mark_habit_reminder_sent(s: Session, ws: int, habit_id: int,
                             day: date | None = None,
                             tz: ZoneInfo | None = None) -> None:
    """Record that today's nudge for this habit has gone out."""
    day = day or today_local(tz)
    row = s.scalar(select(HabitLog).where(
        HabitLog.workspace_id == ws, HabitLog.habit_id == habit_id,
        HabitLog.day == day))
    if row is None:
        row = HabitLog(workspace_id=ws, habit_id=habit_id, day=day, done=False)
        s.add(row)
    row.reminder_sent_at = utcnow()
    s.commit()


# ---------------------------------------------------------------------------
# Platform statistics (operator channel)
# ---------------------------------------------------------------------------

def platform_stats(s: Session) -> dict:
    """Aggregate counts only — never journal text or any personal content."""
    today = today_local()
    week_ago = datetime.now() - timedelta(days=7)
    month_ago = datetime.now() - timedelta(days=30)

    def count(model, *where):
        return s.scalar(select(func.count()).select_from(model).where(*where)) or 0

    total = s.scalar(select(func.count()).select_from(User)) or 0
    onboarded = count(User, User.onboarded.is_(True))
    subscribed = count(User, User.is_subscribed.is_(True))

    languages = dict(s.execute(
        select(User.language, func.count(User.telegram_id)).group_by(User.language)).all())
    genders = dict(s.execute(
        select(User.gender, func.count(User.telegram_id)).group_by(User.gender)).all())

    latest = s.scalar(select(func.max(User.member_no))) or 0
    day_from, day_to = utc_window(today)
    return {
        "total": total,
        "latest_member_no": latest,
        "onboarded": onboarded,
        "subscribed": subscribed,
        "blocked": max(onboarded - subscribed, 0),
        "dau": count(User, User.last_active_at >= day_from,
                     User.last_active_at < day_to),
        "wau": count(User, User.last_active_at >= week_ago),
        "mau": count(User, User.last_active_at >= month_ago),
        "new_today": count(User, User.created_at >= day_from,
                           User.created_at < day_to),
        "new_week": count(User, User.created_at >= week_ago),
        "tasks_created": count(Task, Task.created_at >= week_ago),
        "tasks_done": count(Task, Task.status == "done",
                            Task.completed_at >= week_ago),
        "journal_today": count(JournalEntry, JournalEntry.day == today),
        "feedback_week": count(Feedback, Feedback.created_at >= week_ago),
        "teams": count(Team, Team.archived_at.is_(None)),
        "languages": languages,
        "genders": genders,
        **platform_referral_stats(s),
        **platform_progress_stats(s),
    }


# ---------------------------------------------------------------------------
# Scheduler coordination
# ---------------------------------------------------------------------------

def _lock_key(name: str) -> int:
    import zlib
    return zlib.crc32(name.encode()) - 2**31


#: How many consecutive ticks may be refused the lock before that is treated
#: as a stuck lock rather than a busy peer.
LOCK_REFUSAL_ALARM = 10

#: Per-job count of consecutive refusals.
LOCK_REFUSALS: dict[str, int] = {}


class JobLock:
    """Hold a PostgreSQL advisory lock for the duration of one job run.

    Transaction-scoped (`pg_try_advisory_xact_lock`), so it cannot leak: it is
    released when the transaction ends, however it ends.
    """

    def __init__(self, session_factory, name: str):
        self._factory = session_factory
        self._name = name
        self._session = None
        self.acquired = False

    def __enter__(self) -> "JobLock":
        self._session = self._factory()
        if self._session.bind.dialect.name != "postgresql":
            self.acquired = True
            LOCK_REFUSALS[self._name] = 0
            return self
        self.acquired = bool(self._session.scalar(
            sql_text("SELECT pg_try_advisory_xact_lock(:k)"),
            {"k": _lock_key(self._name)}))
        if self.acquired:
            LOCK_REFUSALS[self._name] = 0
        else:
            refusals = LOCK_REFUSALS.get(self._name, 0) + 1
            LOCK_REFUSALS[self._name] = refusals
            if refusals >= LOCK_REFUSAL_ALARM:
                log.warning(
                    "job %s has been refused its lock %s times in a row — "
                    "either a second instance is stuck mid-run, or a lock was "
                    "leaked by a process that died", self._name, refusals)
            else:
                log.info("job %s already running elsewhere — skipping", self._name)
        return self

    def __exit__(self, *exc) -> None:
        if self._session is None:
            return
        try:
            self._session.rollback()
        except Exception:
            log.exception("job %s could not close its lock transaction cleanly",
                          self._name)
        finally:
            self._session.close()


# ---------------------------------------------------------------------------
# Teams — shared goals, separate effort
# ---------------------------------------------------------------------------
#
# Everything above this line is scoped to one workspace and belongs to one
# person. A team is the one place that is not, and the rules it follows are
# deliberately narrow:
#
#   * **Membership is the only key.** Every function here takes the acting
#     user and refuses anything they are not a member of.
#   * **The item is shared, the tick is not.** Each member ticks their own
#     share — and a task says, when it is made, whether one person's tick
#     closes it for everybody, or whether only named people owe it.
#   * **Roles.** The owner can do everything and is the only one who hands
#     roles out or hands the team over; an admin manages the team and its
#     items; a member adds work and edits what they themselves added.
#   * **History is fixed.** A member is owed only the days they were in the
#     team, an archived item keeps the days before it was archived, and a
#     closed day is read from its snapshot.
#   * **Nothing leaks into private space.** No query in this section touches
#     another person's workspace except to read the three rituals a team
#     mirrors, and those only as done / not done.

#: A shared space is for people working together, not an audience.
MAX_TEAM_MEMBERS = 8


def team_member_cap(s: Session, team) -> int:
    """How many people this team may hold: set by its owner's plan. With
    plans off it is the old fixed size."""
    if not plans.ENABLED:
        return MAX_TEAM_MEMBERS
    tier = plans.tier_of(s, team.owner_id)
    return plans.LIMITS["team_members"][tier] or MAX_TEAM_MEMBERS
#: How many teams one account may belong to.
MAX_TEAMS_PER_USER = 5
#: Bytes of randomness in an invite code.
TEAM_CODE_BYTES = 9
#: The longest a team name may be.
TEAM_NAME_MAX = 60
#: How long an invite link works. Long enough to be opened the next evening,
#: short enough that a link left in an old chat stops being a way in.
TEAM_INVITE_TTL = timedelta(hours=72)

TEAM_ROLES = ("owner", "admin", "member")
_ROLE_RANK = {"member": 0, "admin": 1, "owner": 2}

#: How much a team may message a member:
#:   all        changes, reports and reminders
#:   important  reports and reminders, not every added or removed item
#:   assigned   only reminders for tasks that name them
#:   off        nothing from this team
NOTIFY_LEVELS = ("all", "important", "assigned", "off")

#: Who has to do a shared task, and when it is done.
COMPLETION_POLICIES = ("all", "any", "assignees")

#: How far back a member's team streak is walked.
TEAM_STREAK_HORIZON = 120


def clean_team_name(name: str | None) -> str:
    """A usable team name, or the empty string when there is none."""
    return " ".join(str(name or "").split())[:TEAM_NAME_MAX].strip()


def _log_team(s: Session, team_id: int, actor_id: int, action: str,
              subject: str = "", kind: str = "", item_id: int | None = None) -> None:
    """One line in the team's activity. The caller commits."""
    s.add(TeamActivity(team_id=team_id, actor_id=actor_id, action=action[:24],
                       subject=(subject or "")[:300], item_kind=kind[:10],
                       item_id=item_id))


def create_team(s: Session, user_id: int, name: str) -> Team:
    """Start a team, with its creator as the first member and its owner.

    A new team starts empty. It used to be seeded with the personal rituals —
    getting up, prayer, the journal — as separate shared habits, which meant
    one prayer was ticked once privately and again in every team.
    """
    name = clean_team_name(name)
    if not name:
        raise ValueError("empty_name")
    if len(teams_for(s, user_id)) >= MAX_TEAMS_PER_USER:
        raise ValueError("too_many_teams")
    plans.require(s, user_id, "teams_owned", s.scalar(
        select(func.count(Team.id)).where(Team.owner_id == user_id,
                                          Team.archived_at.is_(None))) or 0)

    for _ in range(5):
        team = Team(name=name, owner_id=user_id,
                    code=secrets.token_urlsafe(TEAM_CODE_BYTES),
                    code_expires_at=utcnow() + TEAM_INVITE_TTL)
        s.add(team)
        try:
            with s.begin_nested():
                s.flush()
            break
        except IntegrityError:
            s.expunge(team)
    else:
        raise RuntimeError("could not allocate a team code")

    s.add(TeamMember(team_id=team.id, user_id=user_id, role="owner"))
    _log_team(s, team.id, user_id, "create", name)
    s.commit()
    return team


#: The rituals a team may mirror. Kept for migrations and older teams: a team
#: that already has them reads each member's own habit rather than a tick.
DEFAULT_TEAM_HABITS = DEFAULT_HABITS


def seed_team_rituals(s: Session, team_id: int, created_by: int) -> int:
    """Put the mirrored rituals into a team. Idempotent; returns how many it added."""
    existing = {h.system_key for h in s.scalars(select(TeamHabit).where(
        TeamHabit.team_id == team_id,
        TeamHabit.system_key != "")).all()}
    added = 0
    for position, (name, category, key) in enumerate(DEFAULT_TEAM_HABITS, start=1):
        if key in existing:
            continue
        s.add(TeamHabit(team_id=team_id, name=name, category=category,
                        system_key=key, is_protected=True, position=position,
                        schedule=SCHEDULE_DAILY, created_by=created_by))
        added += 1
    if added:
        s.flush()
    return added


def teams_for(s: Session, user_id: int) -> list[Team]:
    """Every live team this user is currently in, oldest first."""
    if user_id is None:
        return []
    return list(s.scalars(
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user_id, TeamMember.left_at.is_(None),
               Team.archived_at.is_(None))
        .order_by(Team.created_at)
    ).all())


def teams_for_on(s: Session, user_id: int, day: date, *,
                 tz: ZoneInfo | None = None) -> list[Team]:
    """Every team this user was a member of on `day` — including ones since left."""
    if user_id is None:
        return []
    rows = _memoized(s, ("memberships", user_id), lambda: s.execute(
        select(Team, TeamMember)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user_id)
        .order_by(Team.created_at)).all())
    out = []
    for team, member in rows:
        joined = local_date_of(member.joined_at, tz)
        left = local_date_of(member.left_at, tz)
        archived = local_date_of(team.archived_at, tz)
        if joined is not None and day < joined:
            continue
        if left is not None and day >= left:
            continue
        if archived is not None and day >= archived:
            continue
        out.append(team)
    return out


def team_for(s: Session, user_id: int, team_id: int) -> Team | None:
    """The team, only if this user is in it. The single access check."""
    return s.scalar(
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(Team.id == team_id, Team.archived_at.is_(None),
               TeamMember.user_id == user_id, TeamMember.left_at.is_(None))
    )


def _require_team(s: Session, user_id: int, team_id: int) -> Team:
    team = team_for(s, user_id, team_id)
    if team is None:
        raise PermissionError("not_a_member")
    return team


def _membership(s: Session, team_id: int, user_id: int) -> TeamMember | None:
    return s.scalar(select(TeamMember).where(
        TeamMember.team_id == team_id, TeamMember.user_id == user_id,
        TeamMember.left_at.is_(None)))


def role_of(s: Session, team_id: int, user_id: int) -> str | None:
    row = _membership(s, team_id, user_id)
    if row is None:
        return None
    return row.role if row.role in TEAM_ROLES else "member"


def _require_role(s: Session, user_id: int, team_id: int, minimum: str) -> Team:
    team = _require_team(s, user_id, team_id)
    role = role_of(s, team_id, user_id) or "member"
    if _ROLE_RANK[role] < _ROLE_RANK[minimum]:
        raise PermissionError("forbidden")
    return team


def _may_manage(s: Session, user_id: int, team_id: int, created_by: int | None) -> bool:
    """The creator of an item, or an admin or the owner of its team."""
    if created_by == user_id:
        return True
    role = role_of(s, team_id, user_id) or "member"
    return _ROLE_RANK[role] >= _ROLE_RANK["admin"]


def _require_manage(s: Session, user_id: int, team_id: int,
                    created_by: int | None) -> None:
    if not _may_manage(s, user_id, team_id, created_by):
        raise PermissionError("forbidden")


def team_permissions(s: Session, team_id: int, user_id: int) -> dict:
    """What this member may do, for the screen to show only live controls."""
    role = role_of(s, team_id, user_id) or "member"
    rank = _ROLE_RANK[role]
    return {"role": role,
            "rename": rank >= 1, "invite": rank >= 1, "approve": rank >= 1,
            "manage_items": rank >= 1, "roles": rank >= 2, "transfer": rank >= 2,
            "remove_members": rank >= 1}


def _display_name(user: User | None, fallback: str = "?") -> str:
    if user is None:
        return fallback
    name = (user.first_name or "").strip() or (user.username or "").strip()
    return name or f"#{user.member_no}"


def team_members(s: Session, team_id: int) -> list[dict]:
    """Who is in the team now, with the name each of them is shown under."""
    rows = s.execute(
        select(TeamMember, User)
        .join(User, User.telegram_id == TeamMember.user_id)
        .where(TeamMember.team_id == team_id, TeamMember.left_at.is_(None))
        .order_by(TeamMember.joined_at)
    ).all()
    return [{"user_id": member.user_id,
             "role": member.role if member.role in TEAM_ROLES else "member",
             "name": _display_name(user),
             "joined_at": member.joined_at,
             "notify": member.notify if member.notify in NOTIFY_LEVELS else "all",
             "language": user.language or "uz"}
            for member, user in rows]


def parse_team_payload(payload: str | None) -> str | None:
    """`team_<code>` -> `<code>`, or None for anything else."""
    value = (payload or "").strip()
    if not value.startswith("team_"):
        return None
    code = value[len("team_"):].strip()
    return code or None


def _ensure_invite_window(team: Team) -> None:
    """A team from before invites expired gets one fresh window, once."""
    if team.code_expires_at is None:
        team.code_expires_at = utcnow() + TEAM_INVITE_TTL


def invite_info(team: Team) -> dict:
    expires = team.code_expires_at
    return {"expires_at": expires.isoformat() if expires else None,
            "expired": bool(expires and expires <= utcnow()),
            "approval": bool(team.approval_required)}


def team_invite_link(team: Team, bot_username: str) -> str | None:
    """The link to hand to somebody, or None when the bot has no @name."""
    if not bot_username:
        return None
    return f"https://t.me/{bot_username}?start=team_{team.code}"


def preview_invite(s: Session, code: str) -> dict | None:
    """What joining would mean — the screen shown before anybody joins."""
    code = (code or "").strip()
    if not code:
        return None
    team = s.scalar(select(Team).where(Team.code == code,
                                       Team.archived_at.is_(None)))
    if team is None:
        return None
    _ensure_invite_window(team)
    s.commit()
    owner = s.get(User, team.owner_id)
    members = team_members(s, team.id)
    return {"team_id": team.id, "name": team.name, "code": team.code,
            "owner": _display_name(owner), "members": len(members),
            "max_members": team_member_cap(s, team), **invite_info(team)}


def join_team(s: Session, user_id: int, code: str) -> tuple[Team | None, str]:
    """Accept an invite. Returns (team, outcome).

    Outcomes: "joined", "already", "full", "unknown", "expired", "requested".
    Never raises on a bad code — a stale or mistyped link is an ordinary thing
    for a user to have. The caller shows the preview and asks first; this is
    the step after "Qo'shilish".
    """
    code = (code or "").strip()
    if not code:
        return None, "unknown"
    team = s.scalar(select(Team).where(Team.code == code,
                                       Team.archived_at.is_(None)))
    if team is None:
        return None, "unknown"
    _ensure_invite_window(team)
    if team.code_expires_at <= utcnow():
        s.commit()
        return team, "expired"

    existing = s.scalar(select(TeamMember).where(
        TeamMember.team_id == team.id, TeamMember.user_id == user_id))
    if existing is not None and existing.left_at is None:
        return team, "already"

    count = s.scalar(select(func.count()).select_from(TeamMember)
                     .where(TeamMember.team_id == team.id,
                            TeamMember.left_at.is_(None))) or 0
    if count >= team_member_cap(s, team):
        return team, "full"
    if len(teams_for(s, user_id)) >= MAX_TEAMS_PER_USER:
        return team, "full"

    if team.approval_required:
        pending = s.scalar(select(TeamJoinRequest).where(
            TeamJoinRequest.team_id == team.id, TeamJoinRequest.user_id == user_id,
            TeamJoinRequest.status == "pending"))
        if pending is None:
            s.add(TeamJoinRequest(team_id=team.id, user_id=user_id))
            s.commit()
        return team, "requested"

    return team, _admit(s, team, user_id, existing)


def _admit(s: Session, team: Team, user_id: int,
           existing: TeamMember | None = None) -> str:
    if existing is not None:
        # Coming back: a new stretch of membership, from now.
        existing.left_at = None
        existing.joined_at = utcnow()
        existing.role = "member"
    else:
        s.add(TeamMember(team_id=team.id, user_id=user_id, role="member"))
        try:
            with s.begin_nested():
                s.flush()
        except IntegrityError:
            s.rollback()
            return "already"
    _log_team(s, team.id, user_id, "join", _display_name(s.get(User, user_id)))
    s.commit()
    return "joined"


def list_join_requests(s: Session, user_id: int, team_id: int) -> list[dict]:
    _require_role(s, user_id, team_id, "admin")
    rows = s.execute(select(TeamJoinRequest, User)
                     .join(User, User.telegram_id == TeamJoinRequest.user_id)
                     .where(TeamJoinRequest.team_id == team_id,
                            TeamJoinRequest.status == "pending")
                     .order_by(TeamJoinRequest.created_at)).all()
    return [{"id": r.id, "user_id": r.user_id, "name": _display_name(u),
             "created_at": r.created_at.isoformat()} for r, u in rows]


def decide_join_request(s: Session, user_id: int, request_id: int,
                        approve: bool) -> tuple[TeamJoinRequest, Team, str]:
    """Approve or decline one request. Returns (request, team, outcome)."""
    request = s.get(TeamJoinRequest, request_id)
    if request is None or request.status != "pending":
        raise ValueError("unknown_request")
    team = _require_role(s, user_id, request.team_id, "admin")
    request.decided_at = utcnow()
    request.decided_by = user_id
    if not approve:
        request.status = "declined"
        s.commit()
        return request, team, "declined"
    count = s.scalar(select(func.count()).select_from(TeamMember)
                     .where(TeamMember.team_id == team.id,
                            TeamMember.left_at.is_(None))) or 0
    if count >= team_member_cap(s, team):
        request.status = "declined"
        s.commit()
        return request, team, "full"
    request.status = "approved"
    existing = s.scalar(select(TeamMember).where(
        TeamMember.team_id == team.id, TeamMember.user_id == request.user_id))
    outcome = _admit(s, team, request.user_id, existing)
    return request, team, "approved" if outcome == "joined" else outcome


def renew_invite(s: Session, user_id: int, team_id: int) -> Team:
    """A new link, working for the next three days. The old one stops at once."""
    team = _require_role(s, user_id, team_id, "admin")
    for _ in range(5):
        team.code = secrets.token_urlsafe(TEAM_CODE_BYTES)
        team.code_expires_at = utcnow() + TEAM_INVITE_TTL
        try:
            with s.begin_nested():
                s.flush()
            break
        except IntegrityError:
            continue
    _log_team(s, team.id, user_id, "invite_renew")
    s.commit()
    return team


def revoke_invite(s: Session, user_id: int, team_id: int) -> Team:
    """No link works any more until a new one is made."""
    team = _require_role(s, user_id, team_id, "admin")
    team.code = secrets.token_urlsafe(TEAM_CODE_BYTES)
    team.code_expires_at = utcnow()
    _log_team(s, team.id, user_id, "invite_revoke")
    s.commit()
    return team


def set_invite_approval(s: Session, user_id: int, team_id: int, on: bool) -> Team:
    team = _require_role(s, user_id, team_id, "admin")
    team.approval_required = bool(on)
    _log_team(s, team.id, user_id, "approval_on" if on else "approval_off")
    s.commit()
    return team


def rename_team(s: Session, user_id: int, team_id: int, name: str) -> Team:
    """Rename a team. The owner or an admin may; the change is logged."""
    team = _require_role(s, user_id, team_id, "admin")
    cleaned = clean_team_name(name)
    if not cleaned:
        raise ValueError("empty_name")
    team.name = cleaned
    _log_team(s, team.id, user_id, "rename", cleaned)
    s.commit()
    return team


def leave_team(s: Session, user_id: int, team_id: int) -> bool:
    """Leave a team. The last member out archives it.

    The owner cannot simply walk out on everybody else: ownership is handed to
    a member who accepts it first, so nobody finds themselves in charge of a
    team without having said yes. Raises ValueError("owner_must_transfer").
    """
    team = team_for(s, user_id, team_id)
    if team is None:
        return False
    row = _membership(s, team_id, user_id)
    if row is None:
        return False
    others = [m for m in team_members(s, team_id) if m["user_id"] != user_id]
    if team.owner_id == user_id and others:
        raise ValueError("owner_must_transfer")
    row.left_at = utcnow()
    _log_team(s, team.id, user_id, "leave", _display_name(s.get(User, user_id)))
    if not others:
        team.archived_at = utcnow()
    s.commit()
    return True


def delete_team(s: Session, owner_id: int, team_id: int) -> str:
    """The owner closing a team for everybody.

    Archived, not erased: every member's history in it — the days they owed
    and the days they did — stays as it was, because a team that disappears
    must not rewrite anybody's past statistics.
    """
    team = _require_role(s, owner_id, team_id, "owner")
    team.archived_at = utcnow()
    _log_team(s, team.id, owner_id, "delete", team.name)
    s.commit()
    return team.name


def set_member_role(s: Session, actor_id: int, team_id: int, member_id: int,
                    role: str) -> dict:
    """The owner makes a member an admin, or an admin a member again."""
    team = _require_role(s, actor_id, team_id, "owner")
    if role not in ("admin", "member"):
        raise ValueError("bad_role")
    row = _membership(s, team_id, member_id)
    if row is None or member_id == team.owner_id:
        raise ValueError("unknown_member")
    row.role = role
    _log_team(s, team.id, actor_id, "role",
              f"{_display_name(s.get(User, member_id))}: {role}")
    s.commit()
    return {"user_id": member_id, "role": role}


def remove_member(s: Session, actor_id: int, team_id: int, member_id: int) -> bool:
    """Take somebody out of a team. Admins remove members; the owner, anybody."""
    team = _require_role(s, actor_id, team_id, "admin")
    if member_id == team.owner_id or member_id == actor_id:
        raise ValueError("cannot_remove")
    row = _membership(s, team_id, member_id)
    if row is None:
        return False
    actor_role = role_of(s, team_id, actor_id)
    if row.role == "admin" and actor_role != "owner":
        raise PermissionError("forbidden")
    row.left_at = utcnow()
    _log_team(s, team.id, actor_id, "remove", _display_name(s.get(User, member_id)))
    s.commit()
    return True


def offer_ownership(s: Session, owner_id: int, team_id: int, member_id: int) -> Team:
    """Offer the team to a member. Nothing changes until they accept."""
    team = _require_role(s, owner_id, team_id, "owner")
    if member_id == owner_id or _membership(s, team_id, member_id) is None:
        raise ValueError("unknown_member")
    team.pending_owner_id = member_id
    _log_team(s, team.id, owner_id, "transfer_offer",
              _display_name(s.get(User, member_id)))
    s.commit()
    return team


def answer_ownership(s: Session, user_id: int, team_id: int, accept: bool) -> Team:
    """Accept or decline an ownership offer made to this user."""
    team = _require_team(s, user_id, team_id)
    if team.pending_owner_id != user_id:
        raise ValueError("no_offer")
    team.pending_owner_id = None
    if accept:
        old = _membership(s, team_id, team.owner_id)
        new = _membership(s, team_id, user_id)
        if old is not None:
            old.role = "admin"
        if new is not None:
            new.role = "owner"
        team.owner_id = user_id
        _log_team(s, team.id, user_id, "transfer_accept",
                  _display_name(s.get(User, user_id)))
    else:
        _log_team(s, team.id, user_id, "transfer_decline")
    s.commit()
    return team


def set_team_notify(s: Session, user_id: int, team_id: int, level: str) -> str:
    """How much this team may message this member. Only their own setting."""
    _require_team(s, user_id, team_id)
    if level not in NOTIFY_LEVELS:
        raise ValueError("bad_level")
    row = _membership(s, team_id, user_id)
    row.notify = level
    s.commit()
    return level


#: Which notify levels hear which kind of message.
_NOTIFY_HEARS = {
    "change": {"all"},
    "join": {"all"},
    "report": {"all", "important"},
    "reminder": {"all", "important"},
}


def team_recipients(s: Session, team_id: int, except_user: int | None,
                    event: str) -> list[tuple[int, str]]:
    """(user_id, language) of every member who wants this kind of message."""
    hears = _NOTIFY_HEARS.get(event, {"all"})
    return [(m["user_id"], m["language"]) for m in team_members(s, team_id)
            if m["user_id"] != except_user and m["notify"] in hears]


def member_notify(s: Session, team_id: int, user_id: int) -> str:
    row = _membership(s, team_id, user_id)
    return row.notify if row is not None and row.notify in NOTIFY_LEVELS else "all"


def list_team_activity(s: Session, user_id: int, team_id: int,
                       limit: int = 30) -> list[dict]:
    """The team's recent changes, newest first, each with who made it."""
    _require_team(s, user_id, team_id)
    rows = s.execute(select(TeamActivity, User)
                     .outerjoin(User, User.telegram_id == TeamActivity.actor_id)
                     .where(TeamActivity.team_id == team_id)
                     .order_by(TeamActivity.id.desc()).limit(limit)).all()
    out = []
    archived_now: dict[tuple[str, int], bool] = {}
    for act, user in rows:
        restorable = False
        if act.action in ("task_archive", "habit_archive") and act.item_id:
            key = (act.item_kind, act.item_id)
            if key not in archived_now:
                model = TeamTask if act.item_kind == "task" else TeamHabit
                item = s.get(model, act.item_id)
                archived_now[key] = bool(item and item.archived_at is not None)
            restorable = archived_now[key]
        out.append({"id": act.id, "action": act.action, "subject": act.subject,
                    "kind": act.item_kind, "item_id": act.item_id,
                    "who": _display_name(user), "actor_id": act.actor_id,
                    "at": act.created_at.isoformat(),
                    "restorable": restorable})
    return out


# --- Shared task rules --------------------------------------------------------

def clean_completion(value: str | None) -> str:
    return value if value in COMPLETION_POLICIES else "all"


def _parse_assignees(text: str | None) -> set[int]:
    out = set()
    for part in (text or "").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


def _clean_assignees(s: Session, team_id: int, ids) -> str | None:
    """Only current members can be named; an empty list means nobody named."""
    members = {m["user_id"] for m in team_members(s, team_id)}
    chosen = sorted({int(x) for x in (ids or []) if int(x) in members})
    return ",".join(str(x) for x in chosen) or None


def team_task_owed_by(task: TeamTask, user_id: int) -> bool:
    """Whether this member owes this task at all."""
    if clean_completion(task.completion) == "assignees":
        named = _parse_assignees(task.assignees)
        return not named or user_id in named
    return True


def team_task_done_for(task: TeamTask, user_id: int, done_by: set[int]) -> bool:
    """Whether the task is done *for this member*, under its completion rule."""
    if clean_completion(task.completion) == "any":
        return bool(done_by)
    return user_id in done_by


def _team_done_map(s: Session, task_ids: list[int]) -> dict[int, set[int]]:
    """{task_id: members who have ticked it} — one query."""
    out: dict[int, set[int]] = defaultdict(set)
    if not task_ids:
        return out
    for task_id, member_id in s.execute(
            select(TeamTaskDone.task_id, TeamTaskDone.user_id)
            .where(TeamTaskDone.task_id.in_(task_ids),
                   TeamTaskDone.done.is_(True))).all():
        out[task_id].add(member_id)
    return out


def _team_item_live_on(item, day: date, tz: ZoneInfo | None = None) -> bool:
    """A shared task existed on `day`, and had not been archived yet."""
    born = local_date_of(item.created_at, tz)
    if born is not None and born > day:
        return False
    if item.archived_at is not None:
        gone = local_date_of(item.archived_at, tz)
        if gone is not None and gone <= day:
            return False
    return True


def _existed_on(created_at: datetime | None, day: date,
                tz: ZoneInfo | None = None) -> bool:
    """Whether something had been created by the end of that local day."""
    if created_at is None:
        return True
    born = local_date_of(created_at, tz)
    return born is None or born <= day


# --- Team tasks -------------------------------------------------------------

def add_team_task(s: Session, user_id: int, team_id: int, title: str, *,
                  deadline: date | None = None, priority: str = "medium",
                  description: str = "", due_time: dtime | None = None,
                  remind_before: int | None = None,
                  recurrence: str | None = None,
                  project_id: int | None = None,
                  completion: str | None = None,
                  assignees=None,
                  timer_minutes: int | None = None) -> dict:
    """Put a task in front of the team, saying who has to do it."""
    _require_team(s, user_id, team_id)
    title = title.strip()[:300]
    if not title:
        raise ValueError("empty_title")
    if priority not in PRIORITIES:
        priority = "medium"
    rule = clean_recurrence(recurrence)
    project = _team_project_or_none(s, team_id, project_id)
    policy = clean_completion(completion)
    task = TeamTask(team_id=team_id, title=title, project_id=project,
                    description=(description or "").strip()[:2000],
                    deadline=deadline, due_time=due_time,
                    remind_before=clean_remind_before(remind_before),
                    recurrence=rule or None,
                    anchor_day=(deadline.day if rule == "monthly" and deadline
                                else None),
                    priority=priority, created_by=user_id,
                    completion=policy,
                    assignees=(_clean_assignees(s, team_id, assignees)
                               if policy == "assignees" else None),
                    timer_minutes=clean_timer_minutes(timer_minutes))
    s.add(task)
    s.flush()
    _log_team(s, team_id, user_id, "task_add", title, "task", task.id)
    s.commit()
    return team_task_row(s, task, user_id)


def _team_task_dicts(s: Session, tasks: list[TeamTask], viewer_id: int,
                     tz: ZoneInfo | None = None) -> list[dict]:
    """Rows for many shared tasks in a handful of queries."""
    if not tasks:
        return []
    done_map = _team_done_map(s, [t.id for t in tasks])
    project_ids = {t.project_id for t in tasks if t.project_id}
    projects = dict(s.execute(select(Project.id, Project.name)
                              .where(Project.id.in_(project_ids))).all()) \
        if project_ids else {}
    team_ids = {t.team_id for t in tasks}
    names = {}
    member_rows = s.execute(select(TeamMember.team_id, User)
                            .join(User, User.telegram_id == TeamMember.user_id)
                            .where(TeamMember.team_id.in_(team_ids))).all()
    for _team, user in member_rows:
        names[user.telegram_id] = _display_name(user)
    viewer_ws = s.scalar(select(Workspace.id).where(Workspace.user_id == viewer_id))
    runs = open_timer_runs(s, viewer_ws, "ttask") if viewer_ws else {}
    today = today_local(tz)
    countdowns = _team_item_countdowns(s, "task", [t.id for t in tasks], today)
    out = []
    for task in tasks:
        done_by = done_map.get(task.id, set())
        policy = clean_completion(task.completion)
        assignees = sorted(_parse_assignees(task.assignees))
        out.append({
            "id": task.id, "team_id": task.team_id, "title": task.title,
            "description": task.description or "",
            "deadline": task.deadline.isoformat() if task.deadline else None,
            "due_time": task.due_time.strftime("%H:%M") if task.due_time else None,
            "remind_before": task.remind_before,
            "recurrence": task.recurrence,
            "priority": task.priority, "created_by": task.created_by,
            "project_id": task.project_id,
            "project": projects.get(task.project_id),
            "completion": policy,
            "assignees": assignees,
            "assignee_names": [names.get(a, "?") for a in assignees],
            "owed": team_task_owed_by(task, viewer_id),
            "mine": viewer_id in done_by,
            "done": team_task_done_for(task, viewer_id, done_by),
            "done_by": sorted(done_by),
            "done_by_names": [names.get(u, "?") for u in sorted(done_by)],
            "done_count": len(done_by),
            "overdue": bool(task.deadline and task.deadline < today
                            and not team_task_done_for(task, viewer_id, done_by)),
            "archived": task.archived_at is not None,
            "countdown": countdowns.get(task.id),
            **_timer_fields(task.timer_minutes, task.title),
            "timer": runs.get(task.id),
        })
    return out


def team_task_row(s: Session, task: TeamTask, viewer_id: int) -> dict:
    """One task, plus who has finished it — including the person looking."""
    return _team_task_dicts(s, [task], viewer_id)[0]


def list_team_tasks(s: Session, user_id: int, team_id: int, *,
                    day: date | None = None, horizon_days: int = 7,
                    project_id: int | None = None,
                    tz: ZoneInfo | None = None) -> list[dict]:
    """The team's open tasks, each carrying this viewer's own state."""
    _require_team(s, user_id, team_id)
    today = day or today_local(tz)
    limit = today + timedelta(days=horizon_days)
    stmt = select(TeamTask).where(TeamTask.team_id == team_id,
                                  TeamTask.archived_at.is_(None))
    if project_id is not None:
        stmt = stmt.where(TeamTask.project_id == project_id)
    else:
        stmt = stmt.where(or_(TeamTask.deadline.is_(None),
                              TeamTask.deadline <= limit))
    tasks = s.scalars(stmt.order_by(
        TeamTask.deadline.is_(None), TeamTask.deadline, TeamTask.id)).all()
    return _team_task_dicts(s, list(tasks), user_id, tz)


def toggle_team_task(s: Session, user_id: int, task_id: int, *,
                     day: date | None = None,
                     tz: ZoneInfo | None = None) -> bool:
    """Tick or untick a team task **for the person asking**, and only them.

    Returns whether the task now counts as done for them — under "any", one
    person's tick is everybody's.
    """
    task = s.get(TeamTask, task_id)
    if task is None or task.archived_at is not None:
        raise ValueError("unknown_task")
    _require_team(s, user_id, task.team_id)
    if not team_task_owed_by(task, user_id):
        raise ValueError("not_assigned")

    row = s.scalar(select(TeamTaskDone).where(
        TeamTaskDone.task_id == task_id, TeamTaskDone.user_id == user_id))
    ticking = row is None or not row.done
    if ticking and team_timer_blocks(s, user_id, "ttask", task):
        raise ValueError("timer_required")
    if row is not None:
        row.done = not row.done
        row.done_at = utcnow()
        row.day = day or today_local(tz)
    else:
        s.add(TeamTaskDone(task_id=task_id, user_id=user_id, done=True,
                           day=day or today_local(tz), done_at=utcnow()))
        try:
            with s.begin_nested():
                s.flush()
        except IntegrityError:
            s.rollback()
    s.commit()
    done_by = _team_done_map(s, [task_id]).get(task_id, set())
    return team_task_done_for(task, user_id, done_by)


def archive_team_task(s: Session, user_id: int, task_id: int) -> bool:
    """Take a task off the team's list. Its creator, an admin or the owner may."""
    task = s.get(TeamTask, task_id)
    if task is None or task.archived_at is not None:
        return False
    _require_team(s, user_id, task.team_id)
    _require_manage(s, user_id, task.team_id, task.created_by)
    task.archived_at = utcnow()
    _log_team(s, task.team_id, user_id, "task_archive", task.title, "task", task.id)
    s.commit()
    return True


def restore_team_task(s: Session, user_id: int, task_id: int) -> dict:
    """Undo an archive."""
    task = s.get(TeamTask, task_id)
    if task is None or task.archived_at is None:
        raise ValueError("unknown_task")
    _require_team(s, user_id, task.team_id)
    _require_manage(s, user_id, task.team_id, task.created_by)
    task.archived_at = None
    _log_team(s, task.team_id, user_id, "task_restore", task.title, "task", task.id)
    s.commit()
    return team_task_row(s, task, user_id)


# --- Team habits ------------------------------------------------------------

def add_team_habit(s: Session, user_id: int, team_id: int, name: str, *,
                   schedule: str | None = None,
                   category: str = "non_negotiable",
                   target_time: dtime | None = None,
                   remind_at: dtime | None = None,
                   timer_minutes: int | None = None,
                   start: str | None = None,
                   tz: ZoneInfo | None = None) -> dict:
    """A habit the team keeps together, with the same settings a private one has."""
    _require_team(s, user_id, team_id)
    name = name.strip()[:120]
    if not name:
        raise ValueError("empty_name")
    if category not in HABIT_CATEGORIES:
        category = "non_negotiable"
    today = today_local(tz)
    position = (s.scalar(select(func.max(TeamHabit.position))
                         .where(TeamHabit.team_id == team_id)) or 0) + 1
    habit = TeamHabit(team_id=team_id, name=name,
                      schedule=clean_schedule(schedule), category=category,
                      target_time=target_time, remind_at=remind_at,
                      position=position, created_by=user_id,
                      timer_minutes=clean_timer_minutes(timer_minutes),
                      active_from=today + timedelta(days=1) if start == "tomorrow" else today)
    s.add(habit)
    s.flush()
    _log_team(s, team_id, user_id, "habit_add", name, "habit", habit.id)
    s.commit()
    return team_habit_row(habit, user_id, set())


def team_habit_row(habit: TeamHabit, viewer_id: int, done_by: set, *,
                   due: bool = True, mine: bool | None = None,
                   run: dict | None = None, paused: bool | None = None) -> dict:
    """One team habit, shaped exactly like a personal one on the wire."""
    ritual = bool(habit.system_key)
    return {"id": habit.id, "team_id": habit.team_id, "name": habit.name,
            "category": habit.category,
            "schedule": clean_schedule(habit.schedule),
            "days": schedule_days(habit.schedule),
            "target_time": (habit.target_time.strftime("%H:%M")
                            if habit.target_time else None),
            "remind_at": (habit.remind_at.strftime("%H:%M")
                          if habit.remind_at else None),
            "system_key": habit.system_key or "",
            # A ritual is read from each member's own habit, never ticked here.
            "mirrored": ritual,
            "protected": bool(habit.is_protected),
            "paused": (habit.paused_at is not None) if paused is None else paused,
            "due": due,
            "done": (viewer_id in done_by) if mine is None else mine,
            "done_by": sorted(done_by), "done_count": len(done_by),
            "created_by": habit.created_by,
            "scored": not ritual,
            **_timer_fields(habit.timer_minutes, habit.name, protected=ritual),
            "timer": run}


class TeamLedger:
    """Everything a team owed and did over a range of days, from ~11 queries.

    The team screens used to rebuild each day from scratch — members, tasks,
    ticks, habits, logs, day after day — which came to hundreds of statements
    for one scoreboard. This reads the whole range once and answers every
    per-member, per-day question from memory:

      * a member owes only the days they were in the team;
      * a task counts on its deadline if it existed then and had not been
        archived before it;
      * a habit counts under the calendar (start, archive, pauses, schedule
        of the day);
      * a ritual counts for a member when their own personal ritual was owed,
        and is done when they did it there;
      * a closed day is read from its snapshot.
    """

    def __init__(self, s: Session, team_id: int, first: date, last: date,
                 tz: ZoneInfo | None = None):
        self.team_id, self.first, self.last = team_id, first, last
        self.tz = tz or TZ
        rows = s.execute(select(TeamMember, User)
                         .join(User, User.telegram_id == TeamMember.user_id)
                         .where(TeamMember.team_id == team_id)
                         .order_by(TeamMember.joined_at)).all()
        self.members = [{"user_id": m.user_id, "name": _display_name(u),
                         "role": m.role if m.role in TEAM_ROLES else "member",
                         "joined": local_date_of(m.joined_at, self.tz),
                         "left": local_date_of(m.left_at, self.tz),
                         "active": m.left_at is None}
                        for m, u in rows]
        self.tasks = list(s.scalars(select(TeamTask).where(
            TeamTask.team_id == team_id,
            TeamTask.deadline.between(first, last))).all())
        self.done = _team_done_map(s, [t.id for t in self.tasks])
        self.habits = list(s.scalars(select(TeamHabit).where(
            TeamHabit.team_id == team_id)
            .order_by(TeamHabit.position, TeamHabit.id)).all())

        keys = {h.system_key for h in self.habits if h.system_key}
        uids = [m["user_id"] for m in self.members]
        self.sys_by_member: dict[int, dict[str, list[Habit]]] = defaultdict(lambda: defaultdict(list))
        sys_habits: list[Habit] = []
        if keys and uids:
            ws_of = dict(s.execute(select(Workspace.id, Workspace.user_id)
                                   .where(Workspace.user_id.in_(uids))).all())
            if ws_of:
                sys_habits = list(s.scalars(select(Habit).where(
                    Habit.workspace_id.in_(list(ws_of)),
                    Habit.system_key.in_(keys))).all())
                for habit in sys_habits:
                    self.sys_by_member[ws_of[habit.workspace_id]][habit.system_key].append(habit)
        self.cal = calendar_for(s, self.habits + sys_habits, self.tz)

        plain = [h.id for h in self.habits if not h.system_key]
        self.logs: dict[tuple[int, date], set[int]] = defaultdict(set)
        if plain:
            for habit_id, member_id, day in s.execute(
                    select(TeamHabitLog.habit_id, TeamHabitLog.user_id, TeamHabitLog.day)
                    .where(TeamHabitLog.habit_id.in_(plain),
                           TeamHabitLog.day.between(first, last),
                           TeamHabitLog.done.is_(True))).all():
                self.logs[(habit_id, day)].add(member_id)
        self.sys_done: set[tuple[int, date]] = set()
        if sys_habits:
            for habit_id, day in s.execute(
                    select(HabitLog.habit_id, HabitLog.day)
                    .where(HabitLog.habit_id.in_([h.id for h in sys_habits]),
                           HabitLog.day.between(first, last),
                           HabitLog.done.is_(True))).all():
                self.sys_done.add((habit_id, day))
        self.snapshots = {(row.user_id, row.day): (row.done, row.total)
                          for row in s.scalars(select(TeamDayScore).where(
                              TeamDayScore.team_id == team_id,
                              TeamDayScore.day.between(first, last))).all()}

    # -- who and what --------------------------------------------------------

    def member(self, user_id: int) -> dict | None:
        return next((m for m in self.members if m["user_id"] == user_id), None)

    def member_on(self, member: dict, day: date) -> bool:
        if member["joined"] is not None and day < member["joined"]:
            return False
        if member["left"] is not None and day >= member["left"]:
            return False
        return True

    def tasks_on(self, day: date) -> list[TeamTask]:
        return [t for t in self.tasks
                if t.deadline == day and _team_item_live_on(t, day, self.tz)]

    def habits_on(self, day: date) -> list[TeamHabit]:
        return [h for h in self.habits if h.system_key or self.cal.due(h, day)]

    def habit_owed(self, habit: TeamHabit, user_id: int, day: date) -> bool:
        if habit.system_key:
            if not self.cal.exists_on(habit, day):
                return False
            own = self.sys_by_member.get(user_id, {}).get(habit.system_key, [])
            return any(self.cal.due(h, day) for h in own)
        return self.cal.due(habit, day)

    def habit_done(self, habit: TeamHabit, user_id: int, day: date) -> bool:
        if habit.system_key:
            own = self.sys_by_member.get(user_id, {}).get(habit.system_key, [])
            return any((h.id, day) in self.sys_done for h in own)
        return user_id in self.logs.get((habit.id, day), set())

    def habit_done_by(self, habit: TeamHabit, day: date) -> set[int]:
        return {m["user_id"] for m in self.members
                if self.member_on(m, day) and self.habit_done(habit, m["user_id"], day)}

    # -- per member, per day -------------------------------------------------

    def live_member_day(self, user_id: int, day: date) -> tuple[int, int]:
        member = self.member(user_id)
        if member is None or not self.member_on(member, day):
            return 0, 0
        done = total = 0
        for task in self.tasks_on(day):
            if not team_task_owed_by(task, user_id):
                continue
            total += 1
            done += team_task_done_for(task, user_id, self.done.get(task.id, set()))
        for habit in self.habits_on(day):
            if not self.habit_owed(habit, user_id, day):
                continue
            total += 1
            done += self.habit_done(habit, user_id, day)
        return done, total

    def member_day(self, user_id: int, day: date, today: date) -> tuple[int, int]:
        if day < today and (user_id, day) in self.snapshots:
            return self.snapshots[(user_id, day)]
        return self.live_member_day(user_id, day)

    def window(self, first: date, last: date, today: date) -> dict[int, list[int]]:
        totals = {m["user_id"]: [0, 0] for m in self.members}
        cursor = first
        while cursor <= last:
            for m in self.members:
                done, total = self.member_day(m["user_id"], cursor, today)
                totals[m["user_id"]][0] += done
                totals[m["user_id"]][1] += total
            cursor += timedelta(days=1)
        return totals

    # -- one day, item by item -----------------------------------------------

    def day_detail(self, day: date) -> dict:
        present = [m for m in self.members if self.member_on(m, day)]
        tasks, habits = [], []
        for task in self.tasks_on(day):
            done_by = self.done.get(task.id, set())
            owing = [m["user_id"] for m in present if team_task_owed_by(task, m["user_id"])]
            finished = [u for u in owing if team_task_done_for(task, u, done_by)]
            tasks.append({"id": task.id, "title": task.title, "priority": task.priority,
                          "deadline": task.deadline.isoformat() if task.deadline else None,
                          "completion": clean_completion(task.completion),
                          "owed_by": owing,
                          "done_by": sorted(u for u in done_by if u in owing) or
                                     (sorted(done_by) if clean_completion(task.completion) == "any" else []),
                          "finished_for": finished,
                          "done_count": len(finished),
                          "missing": [u for u in owing if u not in finished]})
        for habit in self.habits_on(day):
            owing = [m["user_id"] for m in present
                     if self.habit_owed(habit, m["user_id"], day)]
            if not owing:
                continue
            finished = [u for u in owing if self.habit_done(habit, u, day)]
            habits.append({"id": habit.id, "name": habit.name,
                           "mirrored": bool(habit.system_key),
                           "owed_by": owing, "done_by": finished,
                           "finished_for": finished,
                           "done_count": len(finished),
                           "missing": [u for u in owing if u not in finished]})
        return {"tasks": tasks, "habits": habits, "present": present}


def list_team_habits(s: Session, user_id: int, team_id: int, *,
                     day: date | None = None,
                     tz: ZoneInfo | None = None) -> list[dict]:
    """Today's team habits this viewer owes, each with their own state and everyone's."""
    _require_team(s, user_id, team_id)
    today = day or today_local(tz)
    ledger = TeamLedger(s, team_id, today, today, tz)
    viewer_ws = s.scalar(select(Workspace.id).where(Workspace.user_id == user_id))
    runs = open_timer_runs(s, viewer_ws, "thabit", day=today) if viewer_ws else {}
    out = []
    for habit in ledger.habits:
        if habit.archived_at is not None:
            continue
        paused = ledger.cal.paused_on(habit, today)
        owed = ledger.habit_owed(habit, user_id, today)
        if not owed and not paused:
            continue
        out.append(team_habit_row(habit, user_id, ledger.habit_done_by(habit, today),
                                  due=owed, mine=ledger.habit_done(habit, user_id, today),
                                  run=runs.get(habit.id), paused=paused))
    return out


def edit_team_habit(s: Session, user_id: int, habit_id: int, **fields) -> dict:
    """Change a shared habit. Its creator, an admin or the owner may."""
    habit = s.get(TeamHabit, habit_id)
    if habit is None or habit.archived_at is not None:
        raise ValueError("unknown_habit")
    _require_team(s, user_id, habit.team_id)
    _require_manage(s, user_id, habit.team_id, habit.created_by)
    tz = user_tz(s.get(User, user_id))
    today = today_local(tz)

    if "name" in fields and fields["name"]:
        if habit.system_key:
            raise ValueError("protected")
        habit.name = str(fields["name"]).strip()[:120]
    if fields.get("category") in HABIT_CATEGORIES:
        habit.category = fields["category"]
    if "schedule" in fields and fields["schedule"] is not None and not habit.system_key \
            and clean_schedule(fields["schedule"]) != clean_schedule(habit.schedule):
        _record_schedule_change(s, TEAM_HABIT_KIND, habit, fields["schedule"],
                                today=today, team_id=habit.team_id)
    for key in ("target_time", "remind_at"):
        if key in fields:
            setattr(habit, key, fields[key])
    if "timer_minutes" in fields and not habit.system_key:
        habit.timer_minutes = clean_timer_minutes(fields["timer_minutes"])
    if "paused" in fields:
        _apply_pause(s, TEAM_HABIT_KIND, habit, bool(fields["paused"]), today=today,
                     from_today=fields.get("from_day") != "tomorrow",
                     team_id=habit.team_id, tz=tz)
    _log_team(s, habit.team_id, user_id, "habit_edit", habit.name, "habit", habit.id)
    s.commit()
    return team_habit_row(habit, user_id, set())


def team_habit_is_due(habit: TeamHabit, day: date) -> bool:
    """Today's rule for one shared habit, without its history."""
    return habit_is_due(habit, day)


def toggle_team_habit(s: Session, user_id: int, habit_id: int, *,
                      day: date | None = None,
                      tz: ZoneInfo | None = None) -> bool:
    """Tick today's team habit for the person asking, and only them."""
    habit = s.get(TeamHabit, habit_id)
    if habit is None or habit.archived_at is not None:
        raise ValueError("unknown_habit")
    _require_team(s, user_id, habit.team_id)
    if habit.system_key:
        # Mirrored from each member's own habit: ticking it here would be the
        # same prayer recorded twice.
        raise ValueError("mirrored")
    today = day or today_local(tz)

    row = s.scalar(select(TeamHabitLog).where(
        TeamHabitLog.habit_id == habit_id, TeamHabitLog.user_id == user_id,
        TeamHabitLog.day == today))
    ticking = row is None or not row.done
    if ticking and team_timer_blocks(s, user_id, "thabit", habit, today):
        raise ValueError("timer_required")
    if row is None:
        row = TeamHabitLog(habit_id=habit_id, user_id=user_id, day=today,
                           done=True, logged_at=utcnow())
        s.add(row)
        try:
            with s.begin_nested():
                s.flush()
        except IntegrityError:
            s.rollback()
            return True
    else:
        row.done = not row.done
        row.logged_at = utcnow() if row.done else None
    s.commit()
    return bool(row.done)


def archive_team_habit(s: Session, user_id: int, habit_id: int) -> bool:
    habit = s.get(TeamHabit, habit_id)
    if habit is None or habit.archived_at is not None:
        return False
    _require_team(s, user_id, habit.team_id)
    if habit.is_protected and role_of(s, habit.team_id, user_id) != "owner":
        # A ritual the whole team keeps is not one member's to remove.
        raise ValueError("protected")
    _require_manage(s, user_id, habit.team_id, habit.created_by)
    habit.archived_at = utcnow()
    _log_team(s, habit.team_id, user_id, "habit_archive", habit.name, "habit", habit.id)
    s.commit()
    return True


def restore_team_habit(s: Session, user_id: int, habit_id: int) -> dict:
    habit = s.get(TeamHabit, habit_id)
    if habit is None or habit.archived_at is None:
        raise ValueError("unknown_habit")
    _require_team(s, user_id, habit.team_id)
    _require_manage(s, user_id, habit.team_id, habit.created_by)
    habit.archived_at = None
    _log_team(s, habit.team_id, user_id, "habit_restore", habit.name, "habit", habit.id)
    s.commit()
    return team_habit_row(habit, user_id, set())


# --- What the team did ------------------------------------------------------

def team_day_summary(s: Session, team_id: int, day: date | None = None, *,
                     tz: ZoneInfo | None = None) -> dict:
    """One day of a team, per member — what the reports and the bot show.

    Only what was owed that day: tasks due on it, habits the calendar owed,
    and each member counted only on a day they were in the team.
    """
    today = day or today_local(tz)
    team = s.get(Team, team_id)
    if team is None:
        return {}
    ledger = TeamLedger(s, team_id, today, today, tz)
    detail = ledger.day_detail(today)
    present = detail["present"]

    people = []
    for member in present:
        done, total = ledger.live_member_day(member["user_id"], today)
        tasks_total = sum(1 for t in detail["tasks"] if member["user_id"] in t["owed_by"])
        tasks_done = sum(1 for t in detail["tasks"] if member["user_id"] in t["finished_for"])
        people.append({
            "user_id": member["user_id"], "name": member["name"],
            "tasks_done": tasks_done, "tasks_total": tasks_total,
            "habits_done": done - tasks_done, "habits_total": total - tasks_total,
            "done": done, "total": total,
            "percent": round(done / total * 100) if total else None,
        })

    confirmations = sum(len(x["owed_by"]) for x in detail["tasks"] + detail["habits"])
    confirmed = sum(len(x["finished_for"]) for x in detail["tasks"] + detail["habits"])
    return {
        "team_id": team_id, "name": team.name, "date": today.isoformat(),
        "members": people, "member_ids": [m["user_id"] for m in present],
        "tasks": [{**t, "done_by": t["finished_for"]} for t in detail["tasks"]],
        "habits": [{**h, "done_by": h["finished_for"]} for h in detail["habits"]],
        "total": len(detail["tasks"]) + len(detail["habits"]),
        # The three numbers the screen needs to stay countable by hand.
        "units": {"items": len(detail["tasks"]) + len(detail["habits"]),
                  # Items closed for the whole group (audit V27): every member
                  # who owed it is done — under "any", one is enough.
                  "closed": sum(1 for x in detail["tasks"] + detail["habits"] if not x["missing"]),
                  "confirmations": confirmations, "confirmed": confirmed,
                  "left": confirmations - confirmed},
    }


def team_summaries_for(s: Session, user_id: int, day: date | None = None, *,
                       tz: ZoneInfo | None = None) -> list[dict]:
    """Every team this user is in, summarised for their own day."""
    return [team_day_summary(s, team.id, day, tz=tz)
            for team in teams_for(s, user_id)]


def team_items_for_day(s: Session, user_id: int, day: date | None = None, *,
                       tz: ZoneInfo | None = None) -> dict:
    """Every team task and habit this user has today, across all their teams."""
    today = day or today_local(tz)
    tasks: list[dict] = []
    habits: list[dict] = []
    teams = teams_for(s, user_id)
    for team in teams:
        for row in list_team_tasks(s, user_id, team.id, day=today, tz=tz):
            tasks.append({**row, "source": "team",
                          "team_id": team.id, "team_name": team.name})
        for row in list_team_habits(s, user_id, team.id, day=today, tz=tz):
            habits.append({**row, "source": "team",
                           "team_id": team.id, "team_name": team.name})
    return {"tasks": tasks, "habits": habits,
            "teams": [{"id": t.id, "name": t.name} for t in teams]}


def close_team_member_day(s: Session, team_id: int, user_id: int, day: date, *,
                          tz: ZoneInfo | None = None) -> None:
    """Snapshot one member's closed day in one team. Idempotent; caller commits."""
    existing = s.scalar(select(TeamDayScore).where(
        TeamDayScore.team_id == team_id, TeamDayScore.user_id == user_id,
        TeamDayScore.day == day))
    if existing is not None:
        return
    done, total = TeamLedger(s, team_id, day, day, tz).live_member_day(user_id, day)
    s.add(TeamDayScore(team_id=team_id, user_id=user_id, day=day,
                       done=done, total=total))
    try:
        with s.begin_nested():
            s.flush()
    except IntegrityError:
        pass


def team_stats(s: Session, user_id: int, team_id: int, *, period: str = "week",
               tz: ZoneInfo | None = None) -> dict:
    """A team's record over a period, per member and side by side."""
    _require_team(s, user_id, team_id)
    days = {"week": 7, "month": 30, "year": 365}.get(period, 7)
    today = today_local(tz)
    start = today - timedelta(days=days - 1)
    horizon = min(start, today - timedelta(days=TEAM_STREAK_HORIZON))
    ledger = TeamLedger(s, team_id, horizon, today, tz)
    active = [m for m in ledger.members if m["active"]]

    series, totals = [], {m["user_id"]: [0, 0] for m in active}
    for offset in range(days):
        day = start + timedelta(days=offset)
        point = {"date": day.isoformat(), "label": day.strftime("%d.%m")}
        day_done = day_total = 0
        for member in active:
            uid = member["user_id"]
            done, total = ledger.member_day(uid, day, today)
            point[str(uid)] = round(done / total * 100) if total else None
            totals[uid][0] += done
            totals[uid][1] += total
            day_done += done
            day_total += total
        # The whole team as one line: every confirmation owed that day, and
        # how many of them came in. None on a day nothing was owed.
        point["team"] = round(day_done / day_total * 100) if day_total else None
        point["team_done"], point["team_total"] = day_done, day_total
        # The average member that day — the graph's main line, drawn over
        # each member's own. Only people who owed something are averaged.
        owed = [point[str(m["user_id"])] for m in active
                if point[str(m["user_id"])] is not None]
        point["avg"] = round(sum(owed) / len(owed)) if owed else None
        series.append(point)

    people = []
    for member in active:
        uid = member["user_id"]
        done, total = totals[uid]
        people.append({
            "user_id": uid, "name": member["name"],
            "done": done, "total": total,
            "percent": round(done / total * 100) if total else None,
            "streak": _ledger_streak(ledger, uid, today),
        })

    return {"team_id": team_id, "period": period, "days": days,
            "from": start.isoformat(), "to": today.isoformat(),
            "series": series, "members": people,
            "together": (round(sum(p["done"] for p in people)
                               / sum(p["total"] for p in people) * 100)
                         if any(p["total"] for p in people) else None)}


def _ledger_streak(ledger: TeamLedger, member_id: int, today: date) -> int:
    """Consecutive days this member cleared everything the team owed them."""
    streak, cursor = 0, today
    while cursor >= ledger.first:
        done, total = ledger.member_day(member_id, cursor, today)
        if total:
            if done < total:
                if cursor == today:
                    cursor -= timedelta(days=1)
                    continue
                break
            streak += 1
        cursor -= timedelta(days=1)
    return streak


def team_streak(s: Session, team_id: int, member_id: int, today: date, *,
                tz: ZoneInfo | None = None, horizon: int = TEAM_STREAK_HORIZON) -> int:
    """Consecutive days this member cleared everything the team owed."""
    ledger = TeamLedger(s, team_id, today - timedelta(days=horizon), today, tz)
    return _ledger_streak(ledger, member_id, today)


def team_scoreboard(s: Session, user_id: int, team_id: int, *,
                    tz: ZoneInfo | None = None) -> dict:
    """Day, week and month at once, plus what is still open and who did what.

    One ledger over sixty days answers all of it — the three periods, the
    three periods before them, and today item by item.
    """
    _require_team(s, user_id, team_id)
    today = today_local(tz)
    ledger = TeamLedger(s, team_id, today - timedelta(days=59), today, tz)
    members = [m for m in ledger.members if m["active"]]

    periods = {}
    for label, days in (("day", 1), ("week", 7), ("month", 30)):
        start = today - timedelta(days=days - 1)
        totals = ledger.window(start, today, today)
        before = ledger.window(start - timedelta(days=days),
                               start - timedelta(days=1), today)
        rows = []
        for m in members:
            uid = m["user_id"]
            done, total = totals[uid]
            percent = round(done / total * 100) if total else None
            was_done, was_total = before[uid]
            previous = round(was_done / was_total * 100) if was_total else None
            rows.append({
                "user_id": uid, "name": m["name"],
                "is_you": uid == user_id,
                "done": done, "total": total, "percent": percent,
                "previous": previous,
                "delta": (percent - previous
                          if percent is not None and previous is not None
                          else None),
            })
        periods[label] = rows

    detail = ledger.day_detail(today)
    open_items, done_items = [], []
    for kind, rows in (("task", detail["tasks"]), ("habit", detail["habits"])):
        for row in rows:
            entry = {"kind": kind, "id": row["id"],
                     "title": row.get("title") or row.get("name"),
                     "mirrored": row.get("mirrored", False),
                     "done_by": row["finished_for"],
                     "owed": len(row["owed_by"]),
                     "done_count": len(row["finished_for"]),
                     # all | any: whether one member's tick closes it for everyone.
                     "completion": row.get("completion", "all"),
                     "missing": row["missing"]}
            (done_items if not entry["missing"] else open_items).append(entry)

    confirmations = sum(e["owed"] for e in open_items + done_items)
    confirmed = sum(e["done_count"] for e in open_items + done_items)
    return {"team_id": team_id, "date": today.isoformat(),
            "members": [{"user_id": m["user_id"], "name": m["name"],
                         "role": m["role"]} for m in members],
            "periods": periods,
            "open": open_items, "done": done_items,
            "open_count": len(open_items), "done_count": len(done_items),
            "units": {"items": len(open_items) + len(done_items),
                      "closed": len(done_items),
                      "confirmations": confirmations, "confirmed": confirmed,
                      "left": confirmations - confirmed}}


# --- Team reminders ---------------------------------------------------------

def due_team_task_reminders(s: Session, user_id: int, user: User,
                            now: datetime | None = None) -> list[dict]:
    """Team tasks whose reminder is due for this member and not yet sent."""
    if not prefs_for(user)["task_reminders"]:
        return []
    tz = user_tz(user)
    now = now or now_local(tz)
    today = now.date()

    out = []
    for team in teams_for(s, user_id):
        level = member_notify(s, team.id, user_id)
        if level == "off":
            continue
        tasks = s.scalars(select(TeamTask).where(
            TeamTask.team_id == team.id, TeamTask.archived_at.is_(None),
            TeamTask.deadline == today,
            TeamTask.remind_before.is_not(None))).all()
        if not tasks:
            continue
        done_map = _team_done_map(s, [t.id for t in tasks])
        state = {row.task_id: row for row in s.scalars(select(TeamTaskDone).where(
            TeamTaskDone.user_id == user_id,
            TeamTaskDone.task_id.in_([t.id for t in tasks]))).all()}
        for task in tasks:
            if not team_task_owed_by(task, user_id):
                continue
            if level == "assigned" and user_id not in _parse_assignees(task.assignees):
                continue
            if team_task_done_for(task, user_id, done_map.get(task.id, set())):
                continue
            mine = state.get(task.id)
            if mine is not None and mine.reminder_sent_at:
                continue
            target = datetime.combine(today, task.due_time or dtime(9, 0))
            fire = target - timedelta(minutes=task.remind_before or 0)
            if fire <= now < fire + REMINDER_WINDOW:
                out.append({"id": task.id, "title": task.title,
                            "team_name": team.name,
                            "due_time": (task.due_time.strftime("%H:%M")
                                         if task.due_time else None)})
    return out


def mark_team_task_reminded(s: Session, user_id: int, task_id: int) -> None:
    """Record that this member has been told, so the next tick stays quiet."""
    row = s.scalar(select(TeamTaskDone).where(
        TeamTaskDone.task_id == task_id, TeamTaskDone.user_id == user_id))
    if row is None:
        row = TeamTaskDone(task_id=task_id, user_id=user_id, done=False,
                           day=today_local(), done_at=utcnow())
        s.add(row)
    row.reminder_sent_at = utcnow()
    s.commit()


def due_team_habit_reminders(s: Session, user_id: int, user: User,
                             now: datetime | None = None) -> list[dict]:
    """Team habits this member should be nudged about right now."""
    if not prefs_for(user)["habit_reminders"]:
        return []
    tz = user_tz(user)
    now = now or now_local(tz)
    today = now.date()

    out = []
    for team in teams_for(s, user_id):
        if member_notify(s, team.id, user_id) not in ("all", "important"):
            continue
        habits = [h for h in s.scalars(select(TeamHabit).where(
            TeamHabit.team_id == team.id,
            TeamHabit.archived_at.is_(None),
            TeamHabit.remind_at.is_not(None))).all()
            if not h.system_key]
        if not habits:
            continue
        cal = calendar_for(s, habits, tz)
        habits = [h for h in habits if cal.due(h, today)]
        if not habits:
            continue
        logs = {row.habit_id: row for row in s.scalars(select(TeamHabitLog).where(
            TeamHabitLog.user_id == user_id, TeamHabitLog.day == today,
            TeamHabitLog.habit_id.in_([h.id for h in habits]))).all()}
        for habit in habits:
            mine = logs.get(habit.id)
            if mine is not None and (mine.done or mine.reminder_sent_at):
                continue
            fire = datetime.combine(today, habit.remind_at)
            if fire <= now < fire + HABIT_REMINDER_WINDOW:
                out.append({"id": habit.id, "name": habit.name,
                            "team_name": team.name})
    return out


def mark_team_habit_reminded(s: Session, user_id: int, habit_id: int,
                             day: date | None = None,
                             tz: ZoneInfo | None = None) -> None:
    day = day or today_local(tz)
    row = s.scalar(select(TeamHabitLog).where(
        TeamHabitLog.habit_id == habit_id, TeamHabitLog.user_id == user_id,
        TeamHabitLog.day == day))
    if row is None:
        row = TeamHabitLog(habit_id=habit_id, user_id=user_id, day=day,
                           done=False)
        s.add(row)
    row.reminder_sent_at = utcnow()
    s.commit()


def teammates_of(s: Session, team_id: int, except_user: int) -> list[int]:
    """Everybody in the team but one."""
    return [m["user_id"] for m in team_members(s, team_id)
            if m["user_id"] != except_user]


def team_habit_history(s: Session, user_id: int, habit_id: int, *,
                       days: int = 30, tz: ZoneInfo | None = None) -> dict:
    """A shared habit's record — the reader's own, and everybody else's."""
    habit = s.get(TeamHabit, habit_id)
    if habit is None or habit.archived_at is not None:
        raise ValueError("unknown_habit")
    team = _require_team(s, user_id, habit.team_id)

    today = today_local(tz)
    start = today - timedelta(days=days - 1)
    horizon_start = today - timedelta(days=TEAM_STREAK_HORIZON)
    ledger = TeamLedger(s, habit.team_id, horizon_start, today, tz)

    first_log: dict[int, date] = {}
    for (logged_habit, day), uids in ledger.logs.items():
        if logged_habit != habit.id:
            continue
        for uid in uids:
            if uid not in first_log or day < first_log[uid]:
                first_log[uid] = day

    def on(member: dict, day: date) -> bool:
        """In the team that day — or already keeping this habit before joining.

        A habit brought into the team keeps its owner's run: their days with
        it before they joined are their own record, not days they owed.
        """
        if ledger.member_on(member, day):
            return True
        started = first_log.get(member["user_id"])
        return (started is not None and day >= started
                and (member["left"] is None or day < member["left"]))

    def record(member: dict) -> dict:
        uid = member["user_id"]
        grid, due_count, done_count = [], 0, 0
        for offset in range(days):
            day = start + timedelta(days=offset)
            due = on(member, day) and ledger.habit_owed(habit, uid, day)
            done = ledger.habit_done(habit, uid, day)
            if due:
                due_count += 1
                done_count += int(done)
            grid.append({"day": day.isoformat(), "due": due, "done": done})

        last7 = [g for g in grid[-7:] if g["due"]]

        streak, cursor = 0, today
        if ledger.habit_owed(habit, uid, today) and not ledger.habit_done(habit, uid, today):
            cursor = today - timedelta(days=1)
        while cursor >= horizon_start:
            if not on(member, cursor):
                break
            if ledger.habit_owed(habit, uid, cursor):
                if not ledger.habit_done(habit, uid, cursor):
                    break
                streak += 1
            cursor -= timedelta(days=1)

        return {
            "streak": streak, "grid": grid,
            "last7_done": sum(1 for g in last7 if g["done"]),
            "last7_due": len(last7),
            "last30_done": done_count, "last30_due": due_count,
            "percent": round(done_count / due_count * 100) if due_count else 0,
            "done_today": ledger.habit_done(habit, uid, today),
        }

    active = [m for m in ledger.members if m["active"]]
    me = next((m for m in active if m["user_id"] == user_id), None)
    mine = record(me) if me else {}
    members = [{**m, "is_you": m["user_id"] == user_id, **record(m)}
               for m in active]
    viewer_ws = s.scalar(select(Workspace.id).where(Workspace.user_id == user_id))
    run = (open_timer_runs(s, viewer_ws, "thabit", day=today).get(habit.id)
           if viewer_ws else None)

    return {
        "id": habit.id, "name": habit.name, "category": habit.category,
        "schedule": clean_schedule(habit.schedule),
        "schedule_today": ledger.cal.schedule_on(habit, today),
        "days": schedule_days(habit.schedule),
        "paused": ledger.cal.paused_on(habit, today),
        "protected": bool(habit.is_protected),
        "mirrored": bool(habit.system_key),
        "system_key": habit.system_key or "",
        "target_time": (habit.target_time.strftime("%H:%M")
                        if habit.target_time else None),
        "remind_at": (habit.remind_at.strftime("%H:%M")
                      if habit.remind_at else None),
        "created_by": habit.created_by,
        "can_manage": _may_manage(s, user_id, habit.team_id, habit.created_by),
        "source": "team", "team_id": team.id, "team_name": team.name,
        **_timer_fields(habit.timer_minutes, habit.name,
                        protected=bool(habit.system_key)),
        "timer": run,
        **mine,
        "members": [{k: v for k, v in m.items() if k not in ("joined", "left")}
                    for m in members],
    }


def move_habit(s: Session, user_id: int, *, habit_id: int | None = None,
               team_habit_id: int | None = None,
               to_team: int | None = None) -> dict:
    """Move a habit between a private list and a shared one, keeping its days."""
    owner_ws = workspace_id_for(s, user_id)

    if habit_id is not None:
        if to_team is None:
            raise ValueError("no_destination")
        habit = _owned_habit(s, owner_ws, habit_id)
        if habit.is_protected:
            raise ValueError("protected")
        _require_team(s, user_id, to_team)

        position = (s.scalar(select(func.max(TeamHabit.position))
                             .where(TeamHabit.team_id == to_team)) or 0) + 1
        moved = TeamHabit(team_id=to_team, name=habit.name,
                          category=habit.category, schedule=habit.schedule,
                          target_time=habit.target_time,
                          remind_at=habit.remind_at, position=position,
                          timer_minutes=habit.timer_minutes,
                          active_from=habit.active_from
                          or local_date_of(habit.created_at),
                          created_by=user_id)
        s.add(moved)
        s.flush()
        for row in s.scalars(select(HabitLog).where(
                HabitLog.habit_id == habit.id)).all():
            s.add(TeamHabitLog(habit_id=moved.id, user_id=user_id,
                               day=row.day, done=row.done,
                               logged_at=row.logged_at))
        habit.archived_at = utcnow()
        _log_team(s, to_team, user_id, "habit_add", moved.name, "habit", moved.id)
        s.commit()
        return team_habit_row(moved, user_id, set())

    if team_habit_id is None:
        raise ValueError("nothing_to_move")

    shared = s.get(TeamHabit, team_habit_id)
    if shared is None or shared.archived_at is not None:
        raise ValueError("unknown_habit")
    _require_team(s, user_id, shared.team_id)
    if shared.is_protected or shared.system_key:
        raise ValueError("protected")
    _require_manage(s, user_id, shared.team_id, shared.created_by)

    top = s.scalar(select(func.max(Habit.position))
                   .where(Habit.workspace_id == owner_ws)) or 0
    habit = Habit(workspace_id=owner_ws, name=shared.name,
                  category=shared.category, schedule=shared.schedule,
                  target_time=shared.target_time, remind_at=shared.remind_at,
                  timer_minutes=shared.timer_minutes,
                  active_from=shared.active_from or local_date_of(shared.created_at),
                  position=top + 1)
    s.add(habit)
    s.flush()
    for row in s.scalars(select(TeamHabitLog).where(
            TeamHabitLog.habit_id == shared.id,
            TeamHabitLog.user_id == user_id)).all():
        s.add(HabitLog(workspace_id=owner_ws, habit_id=habit.id, day=row.day,
                       done=row.done, logged_at=row.logged_at))
    shared.archived_at = utcnow()
    _log_team(s, shared.team_id, user_id, "habit_archive", shared.name,
              "habit", shared.id)
    s.commit()
    return {"id": habit.id, "name": habit.name, "source": "personal"}


def share_ritual(s: Session, user_id: int, team_id: int, system_key: str) -> dict:
    """Show one of the three rituals in a team as well as on your own list.

    A ritual is not moved the way an ordinary habit is: getting up, praying
    and writing the journal are each person's own, recorded once in their own
    workspace. Sharing one puts a mirrored row into the team — each member's
    tick is read from their own ritual — so the team sees who has done it
    without anybody ticking anything twice. It is never scored twice either:
    mirrored rows stay out of the personal day's number.
    """
    if system_key not in SYSTEM_KEYS:
        raise ValueError("not_a_ritual")
    _require_team(s, user_id, team_id)
    live = s.scalar(select(TeamHabit).where(
        TeamHabit.team_id == team_id, TeamHabit.system_key == system_key,
        TeamHabit.archived_at.is_(None)))
    if live is not None:
        return team_habit_row(live, user_id, set())
    name, category, _key = next(d for d in DEFAULT_TEAM_HABITS if d[2] == system_key)
    position = (s.scalar(select(func.max(TeamHabit.position))
                         .where(TeamHabit.team_id == team_id)) or 0) + 1
    # A fresh row from today rather than an archived one brought back: the
    # days it was not shared must not turn into days the team missed it.
    habit = TeamHabit(team_id=team_id, name=name, category=category,
                      system_key=system_key, is_protected=True,
                      position=position, schedule=SCHEDULE_DAILY,
                      created_by=user_id,
                      active_from=today_local(user_tz(s.get(User, user_id))))
    s.add(habit)
    s.flush()
    _log_team(s, team_id, user_id, "habit_add", name, "habit", habit.id)
    s.commit()
    return team_habit_row(habit, user_id, set())


def unshare_ritual(s: Session, user_id: int, team_habit_id: int) -> bool:
    """Take a shared ritual out of the team. Each member's own ritual stays.

    Whoever shared it, or an admin or the owner, may take it out — the same
    rule as any shared item. The personal ritual underneath is untouched.
    """
    habit = s.get(TeamHabit, team_habit_id)
    if habit is None or habit.archived_at is not None or not habit.system_key:
        raise ValueError("unknown_habit")
    _require_team(s, user_id, habit.team_id)
    _require_manage(s, user_id, habit.team_id, habit.created_by)
    habit.archived_at = utcnow()
    _log_team(s, habit.team_id, user_id, "habit_archive", habit.name,
              "habit", habit.id)
    s.commit()
    return True


def unshare_ritual_in(s: Session, user_id: int, team_id: int,
                      system_key: str) -> str | None:
    """`unshare_ritual` by team and ritual. Returns its name, or None if not shared."""
    _require_team(s, user_id, team_id)
    live = s.scalar(select(TeamHabit).where(
        TeamHabit.team_id == team_id, TeamHabit.system_key == system_key,
        TeamHabit.archived_at.is_(None)))
    if live is None:
        return None
    name = live.name
    unshare_ritual(s, user_id, live.id)
    return name


def shared_rituals(s: Session, user_id: int) -> dict[str, list[int]]:
    """{system_key: [team ids]} — where each of this person's rituals is shared."""
    out: dict[str, list[int]] = {key: [] for key in SYSTEM_KEYS}
    teams = [team.id for team in teams_for(s, user_id)]
    if not teams:
        return out
    for team_id, key in s.execute(select(TeamHabit.team_id, TeamHabit.system_key).where(
            TeamHabit.team_id.in_(teams), TeamHabit.archived_at.is_(None),
            TeamHabit.system_key.in_(SYSTEM_KEYS))).all():
        out.setdefault(key, []).append(team_id)
    return out


# --- Team projects ----------------------------------------------------------

def list_team_projects(s: Session, user_id: int, team_id: int, *,
                       include_archived: bool = False) -> list[dict]:
    """The team's own shelves, with how far each has got."""
    _require_team(s, user_id, team_id)
    stmt = select(Project).where(Project.team_id == team_id)
    if not include_archived:
        stmt = stmt.where(Project.archived_at.is_(None))
    projects = s.scalars(stmt.order_by(Project.status, Project.created_at)).all()
    if not projects:
        return []
    tasks = s.scalars(select(TeamTask).where(
        TeamTask.project_id.in_([p.id for p in projects]),
        TeamTask.archived_at.is_(None))).all()
    done_map = _team_done_map(s, [t.id for t in tasks])
    out = []
    for project in projects:
        mine = [t for t in tasks if t.project_id == project.id]
        total = len(mine)
        done = sum(1 for t in mine
                   if team_task_done_for(t, user_id, done_map.get(t.id, set())))
        out.append({
            "id": project.id, "name": project.name,
            "description": project.description or "",
            "deadline": project.deadline.isoformat() if project.deadline else None,
            "status": project.status, "team_id": team_id,
            "source": "team",
            "tasks_total": total, "tasks_done": done,
            "percent": round(done / total * 100) if total else 0,
        })
    return out


def add_team_project(s: Session, user_id: int, team_id: int, name: str, *,
                     description: str = "",
                     deadline: date | None = None) -> dict:
    """Open a shelf the whole team files onto."""
    _require_team(s, user_id, team_id)
    name = name.strip()[:200]
    if not name:
        raise ValueError("empty_name")
    project = Project(workspace_id=workspace_id_for(s, user_id),
                      team_id=team_id, name=name,
                      description=(description or "").strip()[:2000],
                      deadline=deadline, created_by=user_id)
    s.add(project)
    s.flush()
    _log_team(s, team_id, user_id, "project_add", name, "project", project.id)
    s.commit()
    return {"id": project.id, "name": project.name, "team_id": team_id,
            "source": "team", "status": project.status,
            "tasks_total": 0, "tasks_done": 0, "percent": 0}


def _team_project(s: Session, user_id: int, project_id: int) -> Project:
    """A live shared project this person is a member of, or NotFound.

    NotFound rather than PermissionError for somebody outside the team: a
    stranger must not learn that a project with this number exists.
    """
    project = s.get(Project, project_id)
    if (project is None or project.team_id is None
            or project.archived_at is not None
            or team_for(s, user_id, project.team_id) is None):
        raise NotFound("project")
    return project


def team_project_for(s: Session, user_id: int, project_id: int) -> dict:
    """One shared project, its progress, and whether this member may manage it."""
    project = _team_project(s, user_id, project_id)
    team = team_for(s, user_id, project.team_id)
    row = next((p for p in list_team_projects(s, user_id, project.team_id)
                if p["id"] == project.id), None) or {}
    return {**row, "id": project.id, "name": project.name,
            "description": project.description or "",
            "deadline": project.deadline.isoformat() if project.deadline else None,
            "status": project.status if project.status in PROJECT_STATUSES else "active",
            "team_id": project.team_id, "team_name": team.name if team else "",
            "source": "team",
            "can_manage": _may_manage(s, user_id, project.team_id,
                                      project.created_by)}


def update_team_project(s: Session, user_id: int, project_id: int,
                        **fields) -> dict:
    """Rename a shared project or change its note, deadline or status.

    Its creator, an admin or the owner may — the same rule as a shared task.
    """
    project = _team_project(s, user_id, project_id)
    _require_manage(s, user_id, project.team_id, project.created_by)
    if fields.get("name") is not None:
        name = str(fields["name"]).strip()[:200]
        if not name:
            raise ValueError("empty_name")
        project.name = name
    if "description" in fields:
        project.description = str(fields["description"] or "").strip()[:2000]
    if "deadline" in fields:
        project.deadline = fields["deadline"]
    if fields.get("status") in PROJECT_STATUSES:
        project.status = fields["status"]
    _log_team(s, project.team_id, user_id, "project_edit", project.name,
              "project", project.id)
    s.commit()
    return team_project_for(s, user_id, project.id)


def delete_team_project(s: Session, user_id: int, project_id: int) -> str:
    """Archive a shared project. Its tasks stay in the team, just unfiled."""
    project = _team_project(s, user_id, project_id)
    _require_manage(s, user_id, project.team_id, project.created_by)
    for task in s.scalars(select(TeamTask).where(
            TeamTask.project_id == project.id)).all():
        task.project_id = None
    project.archived_at = utcnow()
    _log_team(s, project.team_id, user_id, "project_archive", project.name,
              "project", project.id)
    s.commit()
    return project.name


def _team_project_or_none(s: Session, team_id: int,
                          project_id: int | None) -> int | None:
    """A project id, but only if it is this team's. Otherwise nothing."""
    if project_id is None:
        return None
    project = s.get(Project, project_id)
    if project is None or project.team_id != team_id:
        raise ValueError("unknown_project")
    return project.id


def edit_team_task(s: Session, user_id: int, task_id: int, **fields) -> dict:
    """Change a shared task. Its creator, an admin or the owner may."""
    task = s.get(TeamTask, task_id)
    if task is None or task.archived_at is not None:
        raise ValueError("unknown_task")
    _require_team(s, user_id, task.team_id)
    _require_manage(s, user_id, task.team_id, task.created_by)

    if "title" in fields and fields["title"]:
        task.title = str(fields["title"]).strip()[:300]
    if "description" in fields:
        task.description = (fields["description"] or "").strip()[:2000]
    if "deadline" in fields:
        task.deadline = fields["deadline"]
        if fields["deadline"] is not None:
            for countdown in s.scalars(select(Countdown).where(
                    Countdown.team_id == task.team_id, Countdown.scope == "task",
                    Countdown.item_id == task.id,
                    Countdown.archived_at.is_(None))).all():
                countdown.target_date = fields["deadline"]
    if "due_time" in fields:
        task.due_time = fields["due_time"]
    if "remind_before" in fields:
        task.remind_before = clean_remind_before(fields["remind_before"])
    if "recurrence" in fields:
        rule = clean_recurrence(fields["recurrence"])
        task.recurrence = rule or None
        if rule == "monthly" and task.deadline and not task.anchor_day:
            task.anchor_day = task.deadline.day
    if fields.get("priority") in PRIORITIES:
        task.priority = fields["priority"]
    if "project_id" in fields:
        task.project_id = _team_project_or_none(s, task.team_id,
                                                fields["project_id"])
    if "completion" in fields and fields["completion"] is not None:
        task.completion = clean_completion(fields["completion"])
        if task.completion != "assignees":
            task.assignees = None
    if "assignees" in fields and fields["assignees"] is not None:
        task.assignees = (_clean_assignees(s, task.team_id, fields["assignees"])
                          if clean_completion(task.completion) == "assignees" else None)
    if "timer_minutes" in fields:
        task.timer_minutes = clean_timer_minutes(fields["timer_minutes"])
    _log_team(s, task.team_id, user_id, "task_edit", task.title, "task", task.id)
    s.commit()
    return team_task_row(s, task, user_id)


def team_task_for(s: Session, user_id: int, task_id: int) -> dict | None:
    """One shared task, if this person is in the team that owns it."""
    task = s.get(TeamTask, task_id)
    if task is None or task.archived_at is not None:
        return None
    team = team_for(s, user_id, task.team_id)
    if team is None:
        return None
    row = team_task_row(s, task, user_id)
    row.update({"source": "team", "team_id": team.id, "team_name": team.name,
                "can_manage": _may_manage(s, user_id, team.id, task.created_by),
                "members": [{"user_id": m["user_id"], "name": m["name"]}
                            for m in team_members(s, team.id)]})
    return row


def move_task(s: Session, user_id: int, *, task_id: int | None = None,
              team_task_id: int | None = None,
              to_team: int | None = None) -> dict:
    """Move a task between a private list and a shared one."""
    ws = workspace_id_for(s, user_id)

    if task_id is not None:
        if to_team is None:
            raise ValueError("no_destination")
        task = s.get(Task, task_id)
        if task is None or task.workspace_id != ws or task.archived_at is not None:
            raise ValueError("unknown_task")
        _require_team(s, user_id, to_team)

        moved = TeamTask(team_id=to_team, title=task.title,
                         description=task.description or "",
                         deadline=task.deadline, due_time=task.due_time,
                         remind_before=task.remind_before,
                         recurrence=task.recurrence, anchor_day=task.anchor_day,
                         priority=task.priority, created_by=user_id,
                         completion="all", timer_minutes=task.timer_minutes)
        s.add(moved)
        s.flush()
        if task.status == "done":
            s.add(TeamTaskDone(task_id=moved.id, user_id=user_id, done=True,
                               day=local_date_of(task.completed_at)
                               or today_local(), done_at=task.completed_at
                               or utcnow()))
        task.archived_at = utcnow()
        _log_team(s, to_team, user_id, "task_add", moved.title, "task", moved.id)
        s.commit()
        return team_task_row(s, moved, user_id)

    if team_task_id is None:
        raise ValueError("nothing_to_move")

    shared = s.get(TeamTask, team_task_id)
    if shared is None or shared.archived_at is not None:
        raise ValueError("unknown_task")
    _require_team(s, user_id, shared.team_id)
    _require_manage(s, user_id, shared.team_id, shared.created_by)

    mine = s.scalar(select(TeamTaskDone).where(
        TeamTaskDone.task_id == shared.id, TeamTaskDone.user_id == user_id))
    task = Task(workspace_id=ws, title=shared.title,
                description=shared.description or "",
                deadline=shared.deadline, due_time=shared.due_time,
                remind_before=shared.remind_before,
                recurrence=shared.recurrence, anchor_day=shared.anchor_day,
                priority=shared.priority, timer_minutes=shared.timer_minutes,
                status="done" if (mine and mine.done) else "waiting",
                completed_at=(mine.done_at if mine and mine.done else None))
    s.add(task)
    shared.archived_at = utcnow()
    _log_team(s, shared.team_id, user_id, "task_archive", shared.title,
              "task", shared.id)
    s.commit()
    return {"id": task.id, "title": task.title, "source": "personal"}


def move_project(s: Session, user_id: int, project_id: int,
                 to_team: int | None) -> dict:
    """Move a project, and the tasks filed on it, between private and shared."""
    ws = workspace_id_for(s, user_id)
    project = s.get(Project, project_id)
    if project is None or project.archived_at is not None:
        raise ValueError("unknown_project")

    if to_team is not None:
        if project.team_id is not None:
            raise ValueError("already_shared")
        if project.workspace_id != ws:
            raise ValueError("unknown_project")
        _require_team(s, user_id, to_team)

        tasks = s.scalars(select(Task).where(
            Task.project_id == project.id, Task.archived_at.is_(None))).all()
        project.team_id = to_team
        s.flush()
        for task in tasks:
            moved = TeamTask(team_id=to_team, title=task.title,
                             description=task.description or "",
                             project_id=project.id, deadline=task.deadline,
                             due_time=task.due_time,
                             remind_before=task.remind_before,
                             recurrence=task.recurrence,
                             anchor_day=task.anchor_day,
                             priority=task.priority, created_by=user_id,
                             completion="all")
            s.add(moved)
            s.flush()
            if task.status == "done":
                s.add(TeamTaskDone(task_id=moved.id, user_id=user_id,
                                   done=True, day=today_local(),
                                   done_at=task.completed_at or utcnow()))
            task.archived_at = utcnow()
        _log_team(s, to_team, user_id, "project_add", project.name,
                  "project", project.id)
        s.commit()
        return {"id": project.id, "name": project.name, "source": "team",
                "team_id": to_team, "moved_tasks": len(tasks)}

    if project.team_id is None:
        raise ValueError("already_private")
    _require_role(s, user_id, project.team_id, "admin")

    shared_tasks = s.scalars(select(TeamTask).where(
        TeamTask.project_id == project.id,
        TeamTask.archived_at.is_(None))).all()
    done_ids = set(s.scalars(select(TeamTaskDone.task_id).where(
        TeamTaskDone.user_id == user_id, TeamTaskDone.done.is_(True),
        TeamTaskDone.task_id.in_([t.id for t in shared_tasks] or [0]))).all())

    team_id = project.team_id
    project.team_id = None
    project.workspace_id = ws
    s.flush()
    for shared in shared_tasks:
        s.add(Task(workspace_id=ws, title=shared.title,
                   description=shared.description or "",
                   project_id=project.id, deadline=shared.deadline,
                   due_time=shared.due_time,
                   remind_before=shared.remind_before,
                   recurrence=shared.recurrence, anchor_day=shared.anchor_day,
                   priority=shared.priority,
                   status="done" if shared.id in done_ids else "waiting",
                   completed_at=utcnow() if shared.id in done_ids else None))
        shared.archived_at = utcnow()
    _log_team(s, team_id, user_id, "project_remove", project.name,
              "project", project.id)
    s.commit()
    return {"id": project.id, "name": project.name, "source": "personal",
            "moved_tasks": len(shared_tasks)}


# --- The day's colour bands ---------------------------------------------------

#: Where a percentage stops being one thing and starts being another. Used for
#: the colour a number is shown in.
SCORE_BANDS = ((85, "great"), (65, "good"), (40, "fair"), (0, "low"))


def score_band(percent: int | None) -> str:
    """"great" / "good" / "fair" / "low", or "none" when unmeasured."""
    if percent is None:
        return "none"
    for floor, name in SCORE_BANDS:
        if percent >= floor:
            return name
    return "low"


# ---------------------------------------------------------------------------
# Timers — a habit or a task that is done by the clock, not by a tap
# ---------------------------------------------------------------------------
#
# "5h deep flow" is a promise about five hours, and a checkbox cannot tell five
# hours from five minutes. So an item can carry a timer: while it does, the
# only way to finish it is to let the timer run out, and the moment it does the
# item is ticked for you — in the bot, in the Mini App, and in the score.
#
# Shared items carry timers too. The length is the team's setting; each run
# belongs to the member running it, and finishing it ticks their share only.

#: Longest timer accepted. A day is already more than any one sitting.
TIMER_MAX_MINUTES = 24 * 60
#: The lengths both surfaces offer as one tap, in minutes.
TIMER_PRESETS = [15, 25, 30, 45, 60, 90, 120, 180, 240, 300]
#: habit and task are private; thabit and ttask are a team habit and task.
TIMER_KINDS = ("habit", "task", "thabit", "ttask")
TEAM_TIMER_KINDS = ("thabit", "ttask")
#: A run still counting, as opposed to one that has ended either way.
TIMER_OPEN = ("running", "paused")

_HOUR_UNITS = ("h", "hr", "hrs", "hour", "hours", "soat", "soatlik",
               "ч", "час", "часа", "часов")
_MINUTE_UNITS = ("m", "min", "mins", "minut", "minute", "minutes", "minutlik",
                 "daq", "daqiqa", "daqiqalik",
                 "мин", "минут", "минута", "минуты")
_DURATION_RE = re.compile(
    r"(?<![\w.,])(\d{1,4}(?:[.,]\d{1,2})?)\s*("
    + "|".join(sorted(_HOUR_UNITS + _MINUTE_UNITS, key=len, reverse=True))
    + r")(?!\w)", re.IGNORECASE)
_LETTER_THEN_DIGIT = re.compile(r"(?<=[^\W\d_])(?=\d)")


def parse_duration_minutes(text: str | None) -> int | None:
    """The length of time a name talks about, in minutes, or None.

    Only an explicit unit counts: "5h", "45 min", "1,5 soat", "2 soatlik",
    "1h30m", "30mins", "30 минут". A bare number never does.
    """
    if not text:
        return None
    spaced = _LETTER_THEN_DIGIT.sub(" ", str(text))
    total = 0.0
    for number, unit in _DURATION_RE.findall(spaced):
        value = float(number.replace(",", "."))
        if unit.lower() == "m" and value >= 100:
            continue
        total += value * 60 if unit.lower() in _HOUR_UNITS else value
    minutes = int(round(total))
    if minutes < 1:
        return None
    return min(minutes, TIMER_MAX_MINUTES)


def clean_timer_minutes(value) -> int | None:
    """A timer setting as stored: None (read the name), 0 (off), or minutes."""
    if value is None:
        return None
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    if minutes <= 0:
        return 0
    return min(minutes, TIMER_MAX_MINUTES)


def timer_minutes_for(configured: int | None, name: str | None, *,
                      protected: bool = False) -> int | None:
    """How long the timer on an item actually runs, or None when it has none."""
    if protected:
        return None
    if configured is None:
        return parse_duration_minutes(name)
    if configured <= 0:
        return None
    return min(configured, TIMER_MAX_MINUTES)


def _timer_fields(configured: int | None, name: str | None, *,
                  protected: bool = False) -> dict:
    """The timer half of a habit or task row, identical on every kind."""
    return {
        "timer_minutes": timer_minutes_for(configured, name, protected=protected),
        "timer_mode": ("auto" if configured is None
                       else "off" if configured <= 0 else "on"),
    }


def _workspace_tz(s: Session, ws: int) -> ZoneInfo:
    owner = workspace_owner(s, ws)
    return user_tz(s.get(User, owner)) if owner else TZ


def _timer_item(s: Session, ws: int, kind: str, item_id: int):
    """(row, minutes, title) for the item a timer belongs to."""
    if kind == "habit":
        habit = _owned_habit(s, ws, item_id)
        if habit.archived_at is not None:
            raise NotFound("habit")
        return habit, timer_minutes_for(habit.timer_minutes, habit.name,
                                        protected=habit.is_protected), habit.name
    if kind == "task":
        task = _owned_task(s, ws, item_id)
        if task.archived_at is not None:
            raise NotFound("task")
        return task, timer_minutes_for(task.timer_minutes, task.title), task.title
    if kind in TEAM_TIMER_KINDS:
        owner = workspace_owner(s, ws)
        model = TeamHabit if kind == "thabit" else TeamTask
        item = s.get(model, item_id)
        if item is None or item.archived_at is not None or owner is None \
                or team_for(s, owner, item.team_id) is None:
            raise NotFound("item")
        name = item.name if kind == "thabit" else item.title
        protected = bool(getattr(item, "system_key", ""))
        return item, timer_minutes_for(item.timer_minutes, name,
                                       protected=protected), name
    raise ValueError("unknown_kind")


def _run_remaining(run: TimerRun, now: datetime | None = None) -> int:
    """Seconds left on a run. Paused runs keep what they had."""
    spent = run.elapsed_sec or 0
    if run.status == "running" and run.started_at is not None:
        spent += max(0, int(((now or utcnow()) - run.started_at).total_seconds()))
    return max(0, (run.duration_sec or 0) - spent)


def timer_run_dict(run: TimerRun, now: datetime | None = None,
                   tz: ZoneInfo | None = None) -> dict:
    """One run as both surfaces draw it."""
    now = now or utcnow()
    remaining = _run_remaining(run, now) if run.status in TIMER_OPEN else 0
    ends = None
    if run.status == "running":
        ends = (now + timedelta(seconds=remaining)).replace(
            tzinfo=_utc.utc).astimezone(tz or TZ).strftime("%H:%M")
    return {"id": run.id, "kind": run.kind, "item_id": run.item_id,
            "title": run.title, "status": run.status,
            "duration_sec": run.duration_sec, "remaining_sec": remaining,
            "elapsed_sec": max(0, run.duration_sec - remaining),
            "ends_at": ends, "day": run.day.isoformat(),
            "cycle": f"{run.cycle_work}/{run.cycle_break}" if run.cycle_work else None,
            "on_break": run.status == "paused" and run.break_until is not None,
            "break_until": (run.break_until.replace(tzinfo=_utc.utc).astimezone(tz or TZ)
                            .strftime("%H:%M") if run.break_until else None)}


def _day_bound(kind: str) -> bool:
    """Whether a run belongs to one day — habits do, tasks do not."""
    return kind in ("habit", "thabit")


def _open_run(s: Session, ws: int, kind: str, item_id: int,
              day: date | None = None) -> TimerRun | None:
    """The run still counting for this item, if there is one."""
    run = s.scalar(select(TimerRun).where(
        TimerRun.workspace_id == ws, TimerRun.kind == kind,
        TimerRun.item_id == item_id, TimerRun.status.in_(TIMER_OPEN))
        .order_by(TimerRun.id.desc()).limit(1))
    if run is not None and _day_bound(kind) and day is not None and run.day != day:
        run.status = "cancelled"
        run.started_at = None
        s.flush()
        return None
    return run


def open_timer_runs(s: Session, ws: int, kind: str, *,
                    day: date | None = None) -> dict[int, dict]:
    """{item_id: run} for every run of this kind still counting."""
    if ws is None:
        return {}
    stmt = select(TimerRun).where(
        TimerRun.workspace_id == ws, TimerRun.kind == kind,
        TimerRun.status.in_(TIMER_OPEN))
    if day is not None:
        stmt = stmt.where(TimerRun.day == day)
    now = utcnow()
    tz = _workspace_tz(s, ws)
    out: dict[int, dict] = {}
    for run in s.scalars(stmt.order_by(TimerRun.id)).all():
        out[run.item_id] = timer_run_dict(run, now, tz)
    return out


#: Kinds whose timer *is* the goal ("read 30 min"): running out ticks them.
#: A task's goal is its result, so its timer only records the time worked and
#: the person says whether it is finished (audit #3).
TIMER_COMPLETES = ("habit", "thabit")


def _complete_by_timer(s: Session, run: TimerRun, moment: datetime) -> None:
    """Tick a habit whose time target was met. Tasks are never closed by time."""
    if run.kind not in TIMER_COMPLETES:
        return
    ws = run.workspace_id
    tz = _workspace_tz(s, ws)
    at = moment.replace(tzinfo=_utc.utc).astimezone(tz).replace(tzinfo=None)
    if run.kind == "habit":
        habit = s.get(Habit, run.item_id)
        if habit is None or habit.workspace_id != ws or habit.archived_at is not None:
            return
        row = s.scalar(select(HabitLog).where(
            HabitLog.workspace_id == ws, HabitLog.habit_id == habit.id,
            HabitLog.day == run.day))
        if row is None:
            s.add(HabitLog(workspace_id=ws, habit_id=habit.id, day=run.day,
                           done=True, logged_at=at))
        elif not row.done:
            row.done = True
            row.logged_at = at
    elif run.kind == "task":
        task = s.get(Task, run.item_id)
        if (task is None or task.workspace_id != ws
                or task.archived_at is not None or task.status == "done"):
            return
        _finish_task(s, ws, task, tz, moment)
    elif run.kind in TEAM_TIMER_KINDS:
        owner = workspace_owner(s, ws)
        if owner is None:
            return
        if run.kind == "thabit":
            habit = s.get(TeamHabit, run.item_id)
            if habit is None or habit.archived_at is not None:
                return
            row = s.scalar(select(TeamHabitLog).where(
                TeamHabitLog.habit_id == habit.id, TeamHabitLog.user_id == owner,
                TeamHabitLog.day == run.day))
            if row is None:
                s.add(TeamHabitLog(habit_id=habit.id, user_id=owner, day=run.day,
                                   done=True, logged_at=moment))
            elif not row.done:
                row.done = True
                row.logged_at = moment
        else:
            task = s.get(TeamTask, run.item_id)
            if task is None or task.archived_at is not None:
                return
            row = s.scalar(select(TeamTaskDone).where(
                TeamTaskDone.task_id == task.id, TeamTaskDone.user_id == owner))
            if row is None:
                s.add(TeamTaskDone(task_id=task.id, user_id=owner, done=True,
                                   day=run.day, done_at=moment))
            elif not row.done:
                row.done = True
                row.done_at = moment
                row.day = run.day


#: The rhythms offered for a long session (work minutes, break minutes).
TIMER_CYCLES = {"25/5": (25, 5), "50/10": (50, 10), "90/15": (90, 15)}


def _take_break(run: TimerRun, now: datetime) -> bool:
    """Pause a cycled run at the end of its work block. True if it did.

    The run stops exactly at the block boundary, so the break — and anything
    after it until the person resumes — is never counted as work.
    """
    if not run.cycle_work or run.status != "running" or run.started_at is None:
        return False
    block = run.cycle_work * 60
    done_before = run.elapsed_sec or 0
    boundary = (done_before // block + 1) * block
    if boundary >= run.duration_sec:
        return False
    ran = done_before + int((now - run.started_at).total_seconds())
    if ran < boundary:
        return False
    at = run.started_at + timedelta(seconds=boundary - done_before)
    run.elapsed_sec, run.started_at, run.status = boundary, None, "paused"
    run.break_until = at + timedelta(minutes=run.cycle_break or 0)
    run.break_notice_at = None
    return True


def settle_timers(s: Session, ws: int | None = None,
                  now: datetime | None = None) -> list[int]:
    """Finish every running timer whose time is up. Returns their ids.

    A run with a work/break rhythm is paused for its break instead.
    """
    now = now or utcnow()
    stmt = select(TimerRun).where(TimerRun.status == "running")
    if ws is not None:
        stmt = stmt.where(TimerRun.workspace_id == ws)
    finished: list[int] = []
    cancelled = False
    for run in s.scalars(stmt).all():
        if _take_break(run, now):
            cancelled = True  # something changed: commit below
            continue
        if _run_remaining(run, now) > 0:
            continue
        if _run_is_stale(s, run, date.min):
            run.status, run.started_at = "cancelled", None
            cancelled = True
            continue
        moment = (run.started_at or now) + timedelta(
            seconds=max(0, run.duration_sec - (run.elapsed_sec or 0)))
        moment = min(moment, now)
        won = s.execute(sql_update(TimerRun).where(
            TimerRun.id == run.id, TimerRun.status == "running").values(
            status="finished", finished_at=moment, started_at=None,
            elapsed_sec=run.duration_sec)
            .execution_options(synchronize_session=False)).rowcount
        if not won:
            continue
        s.refresh(run)
        _complete_by_timer(s, run, moment)
        finished.append(run.id)
    if finished or cancelled:
        s.commit()
    return finished


def timer_blocks(s: Session, ws: int, kind: str, item, day: date | None = None) -> bool:
    """Whether a timer stands between this item and being ticked by hand."""
    if kind == "habit":
        minutes = timer_minutes_for(item.timer_minutes, item.name,
                                    protected=item.is_protected)
    elif kind == "thabit":
        minutes = timer_minutes_for(item.timer_minutes, item.name,
                                    protected=bool(item.system_key))
    else:
        minutes = timer_minutes_for(item.timer_minutes, item.title)
    if not minutes:
        return False
    stmt = select(TimerRun.id).where(
        TimerRun.workspace_id == ws, TimerRun.kind == kind,
        TimerRun.item_id == item.id, TimerRun.status == "finished")
    # A habit needs its whole time; for a task any recorded work lets the
    # person say it is finished — the result, not the clock, closes it.
    if kind in TIMER_COMPLETES:
        stmt = stmt.where(TimerRun.elapsed_sec >= TimerRun.duration_sec)
    if _day_bound(kind) and day is not None:
        stmt = stmt.where(TimerRun.day == day)
    return s.scalar(stmt.limit(1)) is None


def team_timer_blocks(s: Session, user_id: int, kind: str, item,
                      day: date | None = None) -> bool:
    """`timer_blocks` for a shared item, from the member's own workspace."""
    ws = s.scalar(select(Workspace.id).where(Workspace.user_id == user_id))
    if ws is None:
        return False
    return timer_blocks(s, ws, kind, item, day or today_local(_workspace_tz(s, ws)))


def _pause_run(run: TimerRun, now: datetime) -> None:
    if run.status == "running" and run.started_at is not None:
        run.elapsed_sec = min(run.duration_sec, (run.elapsed_sec or 0) + max(
            0, int((now - run.started_at).total_seconds())))
    run.started_at = None
    run.status = "paused"


def _item_done_for_timer(s: Session, ws: int, kind: str, item, today: date) -> bool:
    if kind == "habit":
        return bool(s.scalar(select(HabitLog.id).where(
            HabitLog.workspace_id == ws, HabitLog.habit_id == item.id,
            HabitLog.day == today, HabitLog.done.is_(True))))
    if kind == "task":
        return item.status == "done"
    owner = workspace_owner(s, ws)
    if kind == "thabit":
        return bool(s.scalar(select(TeamHabitLog.id).where(
            TeamHabitLog.habit_id == item.id, TeamHabitLog.user_id == owner,
            TeamHabitLog.day == today, TeamHabitLog.done.is_(True))))
    done_by = _team_done_map(s, [item.id]).get(item.id, set())
    return team_task_done_for(item, owner, done_by)


def own_tick(s: Session, user_id: int, kind: str, item, today: date) -> bool:
    """Whether *this person's* own tick is on the item (not someone else's)."""
    if kind == "task":
        return item.status == "done"
    if kind == "habit":
        return bool(s.scalar(select(HabitLog.id).where(
            HabitLog.habit_id == item.id, HabitLog.day == today, HabitLog.done.is_(True))))
    if kind == "thabit":
        return bool(s.scalar(select(TeamHabitLog.id).where(
            TeamHabitLog.habit_id == item.id, TeamHabitLog.user_id == user_id,
            TeamHabitLog.day == today, TeamHabitLog.done.is_(True))))
    return bool(s.scalar(select(TeamTaskDone.id).where(
        TeamTaskDone.task_id == item.id, TeamTaskDone.user_id == user_id,
        TeamTaskDone.done.is_(True))))


def _item_paused(s: Session, kind: str, item, today: date, tz) -> bool:
    if kind in ("habit", "thabit"):
        return calendar_for(s, [item], tz).paused_on(item, today)
    return False


def start_timer(s: Session, ws: int, kind: str, item_id: int, *,
                tz: ZoneInfo | None = None,
                now: datetime | None = None, cycle: str | None = None) -> dict:
    """Start (or resume) the timer on one item.

    One clock at a time: whatever else was running is paused, not lost.
    """
    now = now or utcnow()
    tz = tz or _workspace_tz(s, ws)
    settle_timers(s, ws, now)
    item, minutes, title = _timer_item(s, ws, kind, item_id)
    if not minutes:
        raise ValueError("timer_off")
    today = today_local(tz)
    if _item_paused(s, kind, item, today, tz):
        raise ValueError("paused")
    if _item_done_for_timer(s, ws, kind, item, today):
        raise ValueError("already_done")

    for other in s.scalars(select(TimerRun).where(
            TimerRun.workspace_id == ws, TimerRun.status == "running")).all():
        if not (other.kind == kind and other.item_id == item.id):
            _pause_run(other, now)

    if cycle is not None and cycle not in TIMER_CYCLES:
        raise ValueError("bad_cycle")
    run = _open_run(s, ws, kind, item.id, today if _day_bound(kind) else None)
    if run is None:
        work, rest = TIMER_CYCLES.get(cycle, (None, None))
        # A rhythm longer than the session is just one session.
        if work and work >= minutes:
            work = rest = None
        run = TimerRun(workspace_id=ws, kind=kind, item_id=item.id,
                       day=today, title=(title or "")[:300],
                       duration_sec=minutes * 60, elapsed_sec=0,
                       started_at=now, status="running",
                       cycle_work=work, cycle_break=rest)
        s.add(run)
    elif run.status == "paused":
        run.started_at = now
        run.status = "running"
        run.break_until = run.break_notice_at = None
    s.commit()
    return timer_run_dict(run, now, tz)


def _owned_run(s: Session, ws: int, run_id: int) -> TimerRun:
    run = s.get(TimerRun, run_id)
    if run is None or run.workspace_id != ws:
        raise NotFound("timer")
    return run


def pause_timer(s: Session, ws: int, run_id: int, *,
                now: datetime | None = None) -> dict:
    now = now or utcnow()
    settle_timers(s, ws, now)
    run = _owned_run(s, ws, run_id)
    if run.status == "running":
        _pause_run(run, now)
        s.commit()
    return timer_run_dict(run, now, _workspace_tz(s, ws))


def resume_timer(s: Session, ws: int, run_id: int, *,
                 now: datetime | None = None) -> dict:
    run = _owned_run(s, ws, run_id)
    if run.status != "paused":
        return timer_run_dict(run, now, _workspace_tz(s, ws))
    return start_timer(s, ws, run.kind, run.item_id, now=now)


def stop_timer(s: Session, ws: int, run_id: int, *,
               now: datetime | None = None) -> dict:
    """Give up on a run. Nothing is ticked; the item waits for another go."""
    now = now or utcnow()
    settle_timers(s, ws, now)
    run = _owned_run(s, ws, run_id)
    if run.status in TIMER_OPEN:
        _pause_run(run, now)
        run.status = "cancelled"
        s.commit()
    return timer_run_dict(run, now, _workspace_tz(s, ws))


def finish_timer(s: Session, ws: int, run_id: int, *,
                 now: datetime | None = None) -> dict:
    """End a session early and keep the time worked (audit #40).

    "Stop" gives up and records nothing; "finish" closes the session with the
    minutes actually run, so 35 of 60 minutes count as 35 minutes of work. A
    habit's target is still only met by its full time; a task then asks
    whether it is done.
    """
    now = now or utcnow()
    settle_timers(s, ws, now)
    run = _owned_run(s, ws, run_id)
    if run.status in TIMER_OPEN:
        _pause_run(run, now)
        run.status, run.finished_at, run.notified_at = "finished", now, now
        if run.elapsed_sec >= run.duration_sec:
            _complete_by_timer(s, run, now)
        s.commit()
    return timer_run_dict(run, now, _workspace_tz(s, ws))


def timer_for(s: Session, ws: int, kind: str, item_id: int, *,
              tz: ZoneInfo | None = None) -> dict:
    """Everything the timer screen needs about one item."""
    tz = tz or _workspace_tz(s, ws)
    settle_timers(s, ws)
    item, minutes, title = _timer_item(s, ws, kind, item_id)
    today = today_local(tz)
    run = _open_run(s, ws, kind, item.id, today if _day_bound(kind) else None)
    done = _item_done_for_timer(s, ws, kind, item, today)
    configured = item.timer_minutes
    if kind == "habit":
        protected = item.is_protected
    elif kind == "thabit":
        protected = bool(item.system_key)
    else:
        protected = False
    can_set = True
    if kind in TEAM_TIMER_KINDS:
        owner = workspace_owner(s, ws)
        can_set = _may_manage(s, owner, item.team_id, item.created_by)
    worked = _worked_seconds(s, ws, kind, item.id, today if _day_bound(kind) else None)
    s.commit()
    return {"kind": kind, "id": item.id, "title": title, "done": done,
            "worked_sec": worked["total"], "manual_sec": worked["manual"],
            # Time ran out on a task: it is the person who says it is finished.
            "ask_done": (kind not in TIMER_COMPLETES and not done and run is None
                         and worked["total"] > 0),
            "protected": protected, "can_set": can_set and not protected,
            "team_id": getattr(item, "team_id", None),
            **_timer_fields(configured, title, protected=protected),
            "parsed_minutes": parse_duration_minutes(title),
            "run": timer_run_dict(run, tz=tz) if run else None,
            "presets": TIMER_PRESETS}


def _worked_seconds(s: Session, ws: int, kind: str, item_id: int,
                    day: date | None) -> dict:
    """Time recorded on one item: measured by the clock plus logged by hand."""
    stmt = select(TimerRun.elapsed_sec, TimerRun.manual).where(
        TimerRun.workspace_id == ws, TimerRun.kind == kind,
        TimerRun.item_id == item_id, TimerRun.status == "finished")
    if day is not None:
        stmt = stmt.where(TimerRun.day == day)
    total = manual = 0
    for sec, by_hand in s.execute(stmt).all():
        total += int(sec or 0)
        if by_hand:
            manual += int(sec or 0)
    return {"total": total, "manual": manual}


def log_manual_time(s: Session, ws: int, kind: str, item_id: int, minutes: int, *,
                    tz: ZoneInfo | None = None) -> dict:
    """Record work done without the clock ("I read for an hour, phone away").

    Kept apart from measured time (`manual`), never on top of a run that is
    still counting — that would count the same minutes twice. A habit is
    ticked when the logged time reaches its target; a task only records the
    time, and the person says when it is finished.
    """
    tz = tz or _workspace_tz(s, ws)
    settle_timers(s, ws)
    item, length, title = _timer_item(s, ws, kind, item_id)
    minutes = int(minutes or 0)
    if not 1 <= minutes <= 24 * 60:
        raise ValueError("bad_minutes")
    today = today_local(tz)
    if _item_done_for_timer(s, ws, kind, item, today):
        raise ValueError("already_done")
    if _open_run(s, ws, kind, item.id, today if _day_bound(kind) else None):
        raise ValueError("timer_running")
    now = utcnow()
    run = TimerRun(workspace_id=ws, kind=kind, item_id=item.id, day=today,
                   title=(title or "")[:300], manual=True,
                   duration_sec=max(minutes, length or minutes) * 60,
                   elapsed_sec=minutes * 60, started_at=None, status="finished",
                   finished_at=now, notified_at=now)
    s.add(run)
    s.flush()
    # Logged pieces add up: 20 min and then 40 min meet a 60-min target.
    worked = _worked_seconds(s, ws, kind, item.id, today if _day_bound(kind) else None)
    if length and worked["total"] >= length * 60:
        run.duration_sec = min(run.duration_sec, run.elapsed_sec)
        _complete_by_timer(s, run, now)
    s.commit()
    return timer_for(s, ws, kind, item.id, tz=tz)


def _apply_timer_setting(s: Session, ws: int, kind: str, item, value) -> None:
    """Change how long an item's timer runs, or switch it off.

    The new length is for the next session: a run already counting keeps the
    length it started with, so changing the default at minute 40 of 60 cannot
    finish it on the spot (audit #39) — and on a shared item one member's
    change never touches another member's clock. Switching the timer off
    stops only the asking person's own open run.
    """
    item.timer_minutes = clean_timer_minutes(value)
    name = item.name if kind in ("habit", "thabit") else item.title
    protected = (getattr(item, "is_protected", False) if kind == "habit"
                 else bool(getattr(item, "system_key", "")) if kind == "thabit"
                 else False)
    minutes = timer_minutes_for(item.timer_minutes, name, protected=protected)
    if minutes:
        return
    for run in s.scalars(select(TimerRun).where(
            TimerRun.kind == kind, TimerRun.item_id == item.id,
            TimerRun.workspace_id == ws, TimerRun.status.in_(TIMER_OPEN))).all():
        _pause_run(run, utcnow())
        run.status = "cancelled"


def set_item_timer(s: Session, ws: int, kind: str, item_id: int,
                   value) -> dict:
    """Set a timer from either surface. `None` goes back to reading the name."""
    item, _, _ = _timer_item(s, ws, kind, item_id)
    if kind == "habit" and item.is_protected:
        raise ValueError("protected")
    if kind == "thabit" and item.system_key:
        raise ValueError("protected")
    if kind in TEAM_TIMER_KINDS:
        owner = workspace_owner(s, ws)
        if not _may_manage(s, owner, item.team_id, item.created_by):
            raise ValueError("forbidden")
    _apply_timer_setting(s, ws, kind, item, value)
    s.commit()
    return timer_for(s, ws, kind, item_id)


def _run_is_stale(s: Session, run: TimerRun, today: date) -> bool:
    """A run that can no longer finish anything."""
    if run.kind == "habit":
        item = s.get(Habit, run.item_id)
        if item is None or item.archived_at is not None \
                or item.workspace_id != run.workspace_id:
            return True
        return run.status == "paused" and run.day < today
    if run.kind == "task":
        item = s.get(Task, run.item_id)
        return (item is None or item.archived_at is not None
                or item.workspace_id != run.workspace_id or item.status == "done")
    owner = workspace_owner(s, run.workspace_id)
    model = TeamHabit if run.kind == "thabit" else TeamTask
    item = s.get(model, run.item_id)
    if item is None or item.archived_at is not None or owner is None \
            or team_for(s, owner, item.team_id) is None:
        return True
    if run.kind == "thabit":
        return run.status == "paused" and run.day < today
    done_by = _team_done_map(s, [item.id]).get(item.id, set())
    return team_task_done_for(item, owner, done_by)


def active_timer(s: Session, ws: int) -> dict | None:
    """The timer to show on every screen: the running one, else a paused one."""
    settle_timers(s, ws)
    tz = _workspace_tz(s, ws)
    today = today_local(tz)
    runs = []
    for run in s.scalars(select(TimerRun).where(
            TimerRun.workspace_id == ws, TimerRun.status.in_(TIMER_OPEN))
            .order_by(TimerRun.id.desc())).all():
        if _run_is_stale(s, run, today):
            run.status, run.started_at = "cancelled", None
        else:
            runs.append(run)
    s.commit()
    if not runs:
        return None
    run = next((r for r in runs if r.status == "running"), runs[0])
    return timer_run_dict(run, tz=tz)


def claim_timer_notice(s: Session, run_id: int) -> bool:
    """Take the right to announce that this run finished. True exactly once."""
    won = s.execute(sql_update(TimerRun).where(
        TimerRun.id == run_id, TimerRun.status == "finished",
        TimerRun.notified_at.is_(None)).values(notified_at=utcnow())
        .execution_options(synchronize_session=False)).rowcount
    s.commit()
    return bool(won)


def release_timer_notice(s: Session, run_id: int) -> None:
    """Give a claimed announcement back, after a send that may succeed later."""
    run = s.get(TimerRun, run_id)
    if run is not None:
        run.notified_at = None
        s.commit()


def unannounced_timers(s: Session, limit: int = 200) -> list[TimerRun]:
    """Finished runs nobody has been told about yet."""
    return list(s.scalars(select(TimerRun).where(
        TimerRun.status == "finished", TimerRun.notified_at.is_(None))
        .order_by(TimerRun.id).limit(limit)).all())


def unannounced_breaks(s: Session, limit: int = 200) -> list[TimerRun]:
    """Runs that just paused for a break nobody has been told about."""
    return list(s.scalars(select(TimerRun).where(
        TimerRun.status == "paused", TimerRun.break_until.is_not(None),
        TimerRun.break_notice_at.is_(None)).order_by(TimerRun.id).limit(limit)).all())


def claim_break_notice(s: Session, run_id: int) -> bool:
    won = s.execute(sql_update(TimerRun).where(
        TimerRun.id == run_id, TimerRun.break_notice_at.is_(None))
        .values(break_notice_at=utcnow())
        .execution_options(synchronize_session=False)).rowcount
    s.commit()
    return bool(won)


def live_timer_messages(s: Session) -> list[TimerRun]:
    """Running timers that have a bot message to keep counting down."""
    return list(s.scalars(select(TimerRun).where(
        TimerRun.status == "running", TimerRun.message_id.is_not(None))).all())


def attach_timer_message(s: Session, ws: int, run_id: int, chat_id: int,
                         message_id: int) -> None:
    """Remember which bot message shows this run, so it can be kept current."""
    run = _owned_run(s, ws, run_id)
    run.chat_id, run.message_id = chat_id, message_id
    s.commit()


def timer_candidates(s: Session, ws: int, user_id: int, kind: str, *,
                     tz: ZoneInfo | None = None) -> list[dict]:
    """Everything a timer could be put on, private and shared, for the bot's list.

    `kind` is "habit" or "task"; shared ones come back with their own kind
    ("thabit" / "ttask") and team name, so one list covers both.
    """
    tz = tz or _workspace_tz(s, ws)
    today = today_local(tz)
    out: list[dict] = []
    if kind == "habit":
        for h in list_habits(s, ws, today, tz=tz):
            if not h["protected"] and not h["paused"]:
                out.append({**h, "kind": "habit", "title": h["name"]})
        for team in teams_for(s, user_id):
            for h in list_team_habits(s, user_id, team.id, day=today, tz=tz):
                if not h["mirrored"] and not h["paused"]:
                    out.append({**h, "kind": "thabit", "title": h["name"],
                                "team_name": team.name})
    else:
        data = list_tasks(s, ws, horizon_days=365, tz=tz)
        for t in data["overdue"] + data["upcoming"] + data["undated"] + data["later"]:
            out.append({**t, "kind": "task"})
        for team in teams_for(s, user_id):
            for t in list_team_tasks(s, user_id, team.id, day=today, tz=tz):
                if t["owed"] and not t["done"]:
                    out.append({**t, "kind": "ttask", "team_name": team.name})
    return out


# ---------------------------------------------------------------------------
# Countdowns — how many days until a date that matters
# ---------------------------------------------------------------------------
#
# A countdown is filed under what it is about — a task, a habit, or neither —
# and may be linked to the particular task or habit. A shared one belongs to a
# team: every member sees it, and it is read out in the team's own report.

#: Enough for every exam, trip and launch somebody is actually watching.
MAX_COUNTDOWNS = 20
#: How far ahead a countdown may point.
COUNTDOWN_MAX_DAYS = 3650
COUNTDOWN_SCOPES = ("general", "task", "habit")

_MONTH_PREFIXES = {
    1: ("yan", "jan", "янв"), 2: ("fev", "feb", "фев"), 3: ("mar", "мар"),
    4: ("apr", "апр"), 5: ("may", "май", "мая"), 6: ("iyun", "jun", "июн"),
    7: ("iyul", "jul", "июл"), 8: ("avg", "aug", "авг"),
    9: ("sen", "sep", "сен"), 10: ("okt", "oct", "окт"),
    11: ("noy", "nov", "ноя"), 12: ("dek", "dec", "дек"),
}
_RELATIVE_WORDS = {
    "bugun": 0, "today": 0, "сегодня": 0,
    "ertaga": 1, "tomorrow": 1, "завтра": 1,
    "indinga": 2, "послезавтра": 2,
}
_DAY_UNITS = ("kundan", "kun", "days", "day", "дней", "дня", "день", "дн")
_WEEK_UNITS = ("haftadan", "hafta", "weeks", "week", "недели", "недель",
               "неделя", "неделю", "нед")
_MONTH_UNITS = ("oydan", "oy", "months", "month", "месяца", "месяцев",
                "месяц", "мес")


def _month_of(word: str) -> int | None:
    word = word.lower().strip(".,'’`")
    for month, prefixes in _MONTH_PREFIXES.items():
        if any(word.startswith(p) for p in prefixes):
            return month
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    import calendar
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def parse_countdown_date(text: str | None, today: date) -> date | None:
    """A date typed the way people type dates, or None."""
    raw = (text or "").strip().lower()
    if not raw:
        return None
    if raw in _RELATIVE_WORDS:
        return today + timedelta(days=_RELATIVE_WORDS[raw])

    m = re.fullmatch(r"(\d{1,4})\s*([^\d\s]+)(?:\s+(?:dan\s+)?(?:keyin|later|спустя))?", raw)
    if m:
        n, unit = int(m.group(1)), m.group(2).strip(".")
        if unit.startswith(_DAY_UNITS) and n <= 3650:
            return today + timedelta(days=n)
        if unit.startswith(_WEEK_UNITS) and n <= 520:
            return today + timedelta(weeks=n)
        if unit.startswith(_MONTH_UNITS) and n <= 120:
            return _add_months(today, n)
    m = re.fullmatch(r"(?:через|in)\s+(\d{1,4})\s*([^\d\s]+)", raw)
    if m:
        return parse_countdown_date(f"{m.group(1)} {m.group(2)}", today)

    m = re.fullmatch(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", raw)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{1,2})[-./](\d{1,2})[-./](\d{2}|\d{4})", raw)
    if m:
        year = int(m.group(3))
        year += 2000 if year < 100 else 0
        return _safe_date(year, int(m.group(2)), int(m.group(1)))

    def upcoming(month: int, day: int, year: str | None) -> date | None:
        if year:
            return _safe_date(int(year), month, day)
        found = _safe_date(today.year, month, day)
        if found is not None and found < today:
            found = _safe_date(today.year + 1, month, day)
        return found

    m = re.fullmatch(r"(\d{1,2})[-./](\d{1,2})", raw)
    if m:
        return upcoming(int(m.group(2)), int(m.group(1)), None)
    m = re.fullmatch(r"(\d{1,2})[-\s]*([^\d\s,]+),?\s*(\d{4})?(?:\s*(?:yil|г\.?|year))?", raw)
    if m and _month_of(m.group(2)):
        return upcoming(_month_of(m.group(2)), int(m.group(1)), m.group(3))
    m = re.fullmatch(r"([^\d\s,]+)\s+(\d{1,2}),?\s*(\d{4})?", raw)
    if m and _month_of(m.group(1)):
        return upcoming(_month_of(m.group(1)), int(m.group(2)), m.group(3))
    return None


#: Words a quick capture recognises as "when". The rest of the text is the title.
_WEEKDAY_WORDS = {
    "dushanba": 0, "seshanba": 1, "chorshanba": 2, "payshanba": 3,
    "juma": 4, "shanba": 5, "yakshanba": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "понедельник": 0, "вторник": 1, "среда": 2, "среду": 2, "четверг": 3,
    "пятница": 4, "пятницу": 4, "суббота": 5, "субботу": 5,
    "воскресенье": 6,
}
_TIME_RE = re.compile(r"(?:\bsoat\s*)?\b([01]?\d|2[0-3])[:.]([0-5]\d)\b(?:\s*da\b)?")

#: Short and plural forms a habit line uses: "du, chor, juma sport",
#: "по понедельникам", "on Mondays".
_HABIT_DAY_WORDS = {
    **_WEEKDAY_WORDS,
    "du": 0, "se": 1, "chor": 2, "pay": 3, "sha": 5, "yak": 6,
    "dushanbalari": 0, "seshanbalari": 1, "chorshanbalari": 2, "payshanbalari": 3,
    "jumalari": 4, "shanbalari": 5, "yakshanbalari": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "mondays": 0, "tuesdays": 1, "wednesdays": 2, "thursdays": 3,
    "fridays": 4, "saturdays": 5, "sundays": 6,
    "пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6,
    "понедельникам": 0, "вторникам": 1, "средам": 2, "четвергам": 3,
    "пятницам": 4, "субботам": 5, "воскресеньям": 6,
}
_HABIT_FILLER = {"va", "and", "и", "по", "on", "har", "every", "kuni", "kunlari", ","}


def parse_habit_text(text: str) -> dict:
    """"Dushanba, chorshanba, juma sport" → name "Sport", those three days.

    Day words found anywhere become the schedule; everything else is the
    name. No day words → daily. The result is shown before saving, so a
    wrong guess is one tap to fix, never a silent rule (audit #24).
    """
    words = " ".join((text or "").replace(",", " , ").split()).split(" ")
    days, kept = set(), []
    for word in words:
        key = word.lower().strip(".!?")
        if key in _HABIT_DAY_WORDS:
            days.add(_HABIT_DAY_WORDS[key])
        elif key in _HABIT_FILLER and (days or not kept):
            continue
        else:
            kept.append(word)
    kept = [w for w in kept if w != ","]
    while kept and kept[-1].lower() in _HABIT_FILLER:
        kept.pop()
    while kept and kept[0].lower() in _HABIT_FILLER:
        kept.pop(0)
    if not days:
        name = " ".join((text or "").split())
    else:
        name = " ".join(kept).strip(" ,.-") or " ".join((text or "").split())
        name = name[:1].upper() + name[1:]
    ordered = sorted(days)
    if not ordered or len(ordered) == 7:
        schedule = SCHEDULE_DAILY
    elif ordered == [0, 1, 2, 3, 4]:
        schedule = SCHEDULE_WEEKDAYS
    else:
        schedule = SCHEDULE_PREFIX_DAYS + ",".join(str(d) for d in ordered)
    return {"name": name[:120], "schedule": schedule, "days": ordered}


_EVENING = r"kechqurun|kechki|kechasi|tushdan\s+keyin|вечера|вечером|дня|pm|p\.m\."
_MORNING = r"ertalab|ertalabki|tongda|утра|утром|am|a\.m\."
#: "soat 10 da", "10 da", "soat 3 yarimda", "в 8 вечера", "at 5 pm", "5pm".
_SPOKEN_TIME_RE = re.compile(
    rf"(?:\b(?P<pre>{_MORNING}|{_EVENING})\s+)?"
    r"(?:\bsoat\s+(?P<h1>\d{1,2})|\bв\s+(?P<h2>\d{1,2})|\bat\s+(?P<h3>\d{1,2})"
    r"|\b(?P<h4>\d{1,2})(?=\s*(?:yarim|da\b|-?da\b|larda\b|am\b|pm\b|a\.m|p\.m|час)))"
    r"(?P<half>\s*yarim)?(?:\s*-?(?:da|ga|larda)\b)?(?:\s*(?:часов|часа|час)\b)?"
    rf"(?:\s*(?P<post>{_MORNING}|{_EVENING})\b)?",
    re.IGNORECASE)


def _spoken_time(raw: str) -> tuple[dtime | None, str]:
    """A clock time said the way people say it, and the text without it.

    The hour follows everyday speech: 7–11 is morning, 1–6 is afternoon,
    unless a morning or evening word says otherwise.
    """
    m = _SPOKEN_TIME_RE.search(raw)
    if not m:
        return None, raw
    hour = int(next(g for g in (m.group("h1"), m.group("h2"), m.group("h3"), m.group("h4")) if g))
    if hour > 23:
        return None, raw
    word = (m.group("pre") or m.group("post") or "").lower()
    if hour <= 12:
        if re.fullmatch(_EVENING, word):
            hour = hour % 12 + 12
        elif re.fullmatch(_MORNING, word):
            hour = hour % 12
        elif 1 <= hour <= 6:
            hour += 12
    rest = (raw[:m.start()] + " " + raw[m.end():]).strip()
    return dtime(hour, 30 if m.group("half") else 0), rest


def parse_quick_capture(text: str, today: date, now: dtime | None = None) -> dict:
    """Split "ertaga 15:00 doktorga qo'ng'iroq" into title, date and time.

    Deliberately small: the day words, a weekday, a written date and a clock
    time. Whatever is not recognised stays in the title, so nothing typed is
    ever lost — at worst the date is not picked up and the task is undated.
    """
    raw = " ".join((text or "").split())
    deadline, due = None, None

    # A date with its year is read first: otherwise "12.05.2030" loses
    # "12.05" to the clock ("12:05") and "2030-05-12" is read as 5 December.
    for pattern in (r"\b(\d{4}[-./]\d{1,2}[-./]\d{1,2})\b",
                    r"\b(\d{1,2}[-./]\d{1,2}[-./]\d{4})\b"):
        m = re.search(pattern, raw)
        if m:
            found = parse_countdown_date(m.group(1), today)
            if found is not None:
                deadline = found
                raw = (raw[:m.start()] + raw[m.end():]).strip()
                break

    m = _TIME_RE.search(raw)
    if m:
        due = dtime(int(m.group(1)), int(m.group(2)))
        raw = (raw[:m.start()] + raw[m.end():]).strip()
    else:
        due, raw = _spoken_time(raw)

    words = raw.split(" ")
    kept, previous = [], ""
    for word in words:
        key = word.lower().strip(".,!?")
        if deadline is None and key in _RELATIVE_WORDS:
            deadline = today + timedelta(days=_RELATIVE_WORDS[key])
        elif deadline is None and key in _WEEKDAY_WORDS:
            ahead = (_WEEKDAY_WORDS[key] - today.weekday()) % 7
            deadline = today + timedelta(days=ahead)
        elif not (key in {"kuni", "kuniga", "kunga"} and previous in _WEEKDAY_WORDS):
            kept.append(word)  # "juma kuni" is one date, not a title word
        previous = key
    raw = " ".join(kept)

    if deadline is None:
        for pattern in (r"\b(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?)\b",
                        r"\b(\d{1,2}[-\s](?:yan|fev|mar|apr|may|iyun|iyul|avg|sen|okt|noy|dek|"
                        r"jan|feb|jun|jul|aug|sep|oct|nov|dec|янв|фев|мар|апр|мая|июн|июл|авг|"
                        r"сен|окт|ноя|дек)[^\s,]*(?:,?\s+\d{4}\b)?)"):
            m = re.search(pattern, raw, re.IGNORECASE)
            if m:
                found = parse_countdown_date(m.group(1), today)
                if found is not None:
                    deadline = found
                    raw = (raw[:m.start()] + raw[m.end():]).strip()
                    break

    title = " ".join(raw.split()).strip(" ,.-")
    time_only = due is not None and deadline is None
    if time_only:
        deadline = today  # "soat 10 da hisobot": a time with no day means today
    return {"title": title or " ".join((text or "").split()),
            "deadline": deadline, "due_time": due,
            # "10:00 hisobot" typed at 20:00 may mean tomorrow: today is only a
            # guess, and the caller asks instead of hiding it (audit #49).
            "past_time": bool(time_only and now is not None and due < now)}


def _countdown_dict(row: Countdown, today: date, *, team_name: str | None = None,
                    item_title: str | None = None) -> dict:
    return {"id": row.id, "title": row.title,
            "date": row.target_date.isoformat(),
            "days_left": (row.target_date - today).days,
            "scope": row.scope if row.scope in COUNTDOWN_SCOPES else "general",
            "item_id": row.item_id,
            "item_title": item_title,
            "team_id": row.team_id, "team_name": team_name,
            "created_by": row.created_by}


def _item_titles(s: Session, rows: list[Countdown]) -> dict[tuple, str]:
    """The linked item's name for each countdown that has one."""
    out = {}
    want = defaultdict(set)
    for row in rows:
        if row.item_id and row.scope in ("task", "habit"):
            want[(row.scope, row.team_id is not None)].add(row.item_id)
    for (scope, shared), ids in want.items():
        if scope == "task":
            model, field = (TeamTask, TeamTask.title) if shared else (Task, Task.title)
        else:
            model, field = (TeamHabit, TeamHabit.name) if shared else (Habit, Habit.name)
        for item_id, title in s.execute(select(model.id, field)
                                        .where(model.id.in_(ids))).all():
            out[(scope, shared, item_id)] = title
    return out


def _team_item_countdowns(s: Session, scope: str, item_ids: list[int],
                          today: date) -> dict[int, dict]:
    if not item_ids:
        return {}
    return {row.item_id: {"id": row.id, "days_left": (row.target_date - today).days}
            for row in s.scalars(select(Countdown).where(
                Countdown.team_id.isnot(None), Countdown.scope == scope,
                Countdown.item_id.in_(item_ids),
                Countdown.archived_at.is_(None))).all()}


def list_countdowns(s: Session, ws: int, *, tz: ZoneInfo | None = None,
                    include_past: bool = True, scope: str | None = None) -> list[dict]:
    """Every private countdown, soonest first, each with the days left to it."""
    today = today_local(tz)
    stmt = select(Countdown).where(
        Countdown.workspace_id == ws, Countdown.team_id.is_(None),
        Countdown.archived_at.is_(None))
    rows = s.scalars(stmt.order_by(Countdown.target_date, Countdown.id)).all()
    if scope is not None:
        rows = [r for r in rows if (r.scope or "general") == scope]
    titles = _item_titles(s, list(rows))
    out = [_countdown_dict(r, today, item_title=titles.get(
        (r.scope, False, r.item_id))) for r in rows]
    if not include_past:
        out = [c for c in out if c["days_left"] >= 0]
    return out


def list_team_countdowns(s: Session, user_id: int, team_id: int, *,
                         tz: ZoneInfo | None = None,
                         include_past: bool = True) -> list[dict]:
    team = _require_team(s, user_id, team_id)
    today = today_local(tz)
    rows = s.scalars(select(Countdown).where(
        Countdown.team_id == team_id, Countdown.archived_at.is_(None))
        .order_by(Countdown.target_date, Countdown.id)).all()
    titles = _item_titles(s, list(rows))
    out = [_countdown_dict(r, today, team_name=team.name,
                           item_title=titles.get((r.scope, True, r.item_id)))
           for r in rows]
    if not include_past:
        out = [c for c in out if c["days_left"] >= 0]
    return out


def countdowns_for_user(s: Session, ws: int, user_id: int, *,
                        tz: ZoneInfo | None = None,
                        include_past: bool = True) -> list[dict]:
    """Private and shared countdowns together, soonest first — what Home shows."""
    rows = list_countdowns(s, ws, tz=tz, include_past=include_past)
    for team in teams_for(s, user_id):
        rows += list_team_countdowns(s, user_id, team.id, tz=tz,
                                     include_past=include_past)
    return sorted(rows, key=lambda c: (c["days_left"], c["id"]))


def _validate_countdown_link(s: Session, ws: int, scope: str, item_id: int | None,
                             team_id: int | None) -> int | None:
    if scope not in ("task", "habit") or not item_id:
        return None
    if team_id is None:
        if scope == "task":
            _owned_task(s, ws, item_id)
        else:
            _owned_habit(s, ws, item_id)
        return item_id
    model = TeamTask if scope == "task" else TeamHabit
    item = s.get(model, item_id)
    if item is None or item.team_id != team_id or item.archived_at is not None:
        raise NotFound("item")
    return item_id


def add_countdown(s: Session, ws: int, title: str, target: date, *,
                  tz: ZoneInfo | None = None, scope: str = "general",
                  item_id: int | None = None, team_id: int | None = None,
                  user_id: int | None = None) -> dict:
    title = (title or "").strip()[:200]
    if not title:
        raise ValueError("empty_title")
    scope = scope if scope in COUNTDOWN_SCOPES else "general"
    today = today_local(tz)
    if target < today:
        raise ValueError("past_date")
    if (target - today).days > COUNTDOWN_MAX_DAYS:
        raise ValueError("too_far")
    team_name = None
    if team_id is not None:
        owner = user_id or workspace_owner(s, ws)
        team_name = _require_team(s, owner, team_id).name
        count = s.scalar(select(func.count(Countdown.id)).where(
            Countdown.team_id == team_id, Countdown.archived_at.is_(None))) or 0
    else:
        count = s.scalar(select(func.count(Countdown.id)).where(
            Countdown.workspace_id == ws, Countdown.team_id.is_(None),
            Countdown.archived_at.is_(None))) or 0
    if count >= MAX_COUNTDOWNS:
        raise ValueError("too_many")
    linked = _validate_countdown_link(s, ws, scope, item_id, team_id)
    row = Countdown(workspace_id=ws, title=title, target_date=target,
                    scope=scope, item_id=linked, team_id=team_id,
                    created_by=user_id or workspace_owner(s, ws))
    s.add(row)
    if team_id is not None:
        _log_team(s, team_id, row.created_by, "countdown_add", title)
    s.commit()
    return _countdown_dict(row, today, team_name=team_name)


def _countdown_for_edit(s: Session, ws: int, countdown_id: int) -> Countdown:
    row = s.get(Countdown, countdown_id)
    if row is None or row.archived_at is not None:
        raise NotFound("countdown")
    if row.team_id is None:
        if row.workspace_id != ws:
            raise NotFound("countdown")
        return row
    owner = workspace_owner(s, ws)
    if team_for(s, owner, row.team_id) is None:
        raise NotFound("countdown")
    if not _may_manage(s, owner, row.team_id, row.created_by):
        raise PermissionError("forbidden")
    return row


def update_countdown(s: Session, ws: int, countdown_id: int, *,
                     title: str | None = None, target: date | None = None,
                     tz: ZoneInfo | None = None) -> dict:
    row = _countdown_for_edit(s, ws, countdown_id)
    today = today_local(tz)
    if title is not None:
        title = title.strip()[:200]
        if not title:
            raise ValueError("empty_title")
        row.title = title
    if target is not None:
        if target < today:
            raise ValueError("past_date")
        row.target_date = target
    s.commit()
    return _countdown_dict(row, today)


def delete_countdown(s: Session, ws: int, countdown_id: int) -> str:
    row = _countdown_for_edit(s, ws, countdown_id)
    row.archived_at = utcnow()
    if row.team_id is not None:
        _log_team(s, row.team_id, workspace_owner(s, ws), "countdown_del", row.title)
    s.commit()
    return row.title
