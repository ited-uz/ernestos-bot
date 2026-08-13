"""
ErnestOS — shared business layer.

Every rule lives here exactly once. The Telegram bot and the Mini App API both
call these functions, so a task created in the bot and a task created in the
Mini App go through identical validation and produce identical rows.

Two invariants hold throughout:

  1. Every function takes `workspace_id` and scopes its query to it, so a
     caller can never read or modify another workspace's data.
  2. Local dates use Asia/Tashkent. Grouping a day's habits by UTC would put
     everything after 19:00 local time into the wrong day.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time as dtime, timedelta, timezone as _utc
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db import (
    Birthday, DailyReportLog, Feedback, Habit, HabitLog, JournalEntry,
    PrayerDay, PrayerLog, Project, Task, User, WeeklyFocus, WeeklyReview,
    Workspace, utcnow,
)

log = logging.getLogger("ernestos")

#: The default zone, used by every workspace that never chose one.
TZ = ZoneInfo("Asia/Tashkent")

#: Offered in Settings. A full IANA list is 600 entries the user has to scroll;
#: these are the zones ErnestOS users actually live in, and any other valid
#: IANA name still works if it is already stored.
TIMEZONES = [
    "Asia/Tashkent", "Asia/Almaty", "Asia/Dubai", "Asia/Istanbul",
    "Asia/Seoul", "Asia/Tokyo", "Europe/Moscow", "Europe/Berlin",
    "Europe/London", "America/New_York", "America/Los_Angeles", "UTC",
]


class NotFound(Exception):
    """The row does not exist inside the caller's workspace.

    Deliberately indistinguishable from "never existed": probing ids must not
    reveal whether another workspace owns that row.
    """


def tz_for(name: str | None) -> ZoneInfo:
    """Resolve a stored zone name, falling back rather than raising.

    A row holding a zone the platform no longer knows must not make the app
    unusable, so an unknown name reads as the default.
    """
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
    """Today, in the caller's zone.

    Every function that groups by day takes a `tz` so that "today" means the
    same thing to the user as it does to their phone. Omitting it keeps the
    historical default, which is what the bot's own scheduling still uses.
    """
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
# Comparing `completed_at.date()` with a local date is wrong for every user
# whose offset crosses midnight: in Asia/Tashkent (UTC+5) everything finished
# after 19:00 local carries yesterday's UTC date, so a task completed at 22:00
# was filed under "earlier" instead of "today". These two helpers are the only
# sanctioned way across the boundary.

def local_date_of(moment: datetime | None, tz: ZoneInfo | None = None) -> date | None:
    """The local calendar date a stored UTC instant fell on."""
    if moment is None:
        return None
    return moment.replace(tzinfo=_utc.utc).astimezone(tz or TZ).date()


def utc_window(first: date, last: date | None = None,
               tz: ZoneInfo | None = None) -> tuple[datetime, datetime]:
    """The half-open UTC range [start, end) covering local days first..last.

    Used for counting rows by the day the user experienced, while still letting
    the database do the filtering.
    """
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

#: (name, category, system_key). A system_key marks a derived habit the user
#: cannot tick by hand: "prayer" follows the daily prayer score and "journal"
#: follows a fully answered journal entry.
#: Exactly six. Journal completion is reported as its own status rather than
#: a seventh habit, so it never moves the habit denominator or the streak.
DEFAULT_HABITS = [
    ("Get up",    "non_negotiable", "wakeup"),
    ("5x namoz",  "non_negotiable", "prayer"),
    ("Kundalik",  "non_negotiable", "journal"),
    ("Deep flow", "target",         ""),
    ("Sport",     "target",         ""),
    ("Podcast",   "bonus",          ""),
    ("Read",      "bonus",          ""),
]

SYSTEM_PRAYER = "prayer"
#: The journal is a non-negotiable habit again, and it completes only when all
#: five questions are answered — a partial entry is saved and kept, but it does
#: not tick the habit. Migration 0001 archived this habit; 0006 brings it back.
SYSTEM_JOURNAL = "journal"
SYSTEM_WAKEUP = "wakeup"

#: Default rise time, used until the user picks their own.
DEFAULT_WAKE_TIME = dtime(5, 0)
#: How long after the target time a "turdim" message still counts.
WAKE_GRACE = timedelta(hours=1)


def get_or_create_user(s: Session, telegram_id: int, *, first_name: str = "",
                       last_name: str = "", username: str = "") -> tuple[User, bool]:
    """Return (user, created). Creating a user also builds their workspace."""
    user = s.get(User, telegram_id)
    if user is not None:
        # Keep Telegram profile fields fresh, but never overwrite with blanks.
        if first_name:
            user.first_name = first_name
        if last_name:
            user.last_name = last_name
        if username:
            user.username = username
        return user, False

    # Sequential join number: max()+1 rather than a count, so deleting a user
    # never hands their number to somebody else.
    next_no = (s.scalar(select(func.max(User.member_no))) or 0) + 1
    user = User(telegram_id=telegram_id, member_no=next_no,
                first_name=first_name or "", last_name=last_name or "",
                username=username or "")
    s.add(user)
    s.flush()

    workspace = Workspace(user_id=telegram_id)
    s.add(workspace)
    s.flush()

    for position, (name, category, system_key) in enumerate(DEFAULT_HABITS, start=1):
        s.add(Habit(workspace_id=workspace.id, name=name, category=category,
                    position=position, is_protected=bool(system_key),
                    system_key=system_key,
                    target_time=DEFAULT_WAKE_TIME if system_key == SYSTEM_WAKEUP else None))
    s.commit()
    return user, True


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


def set_subscription(s: Session, telegram_id: int, subscribed: bool) -> bool:
    """Update membership state. Returns True when the value actually changed."""
    user = s.get(User, telegram_id)
    if user is None or user.is_subscribed == subscribed:
        return False
    user.is_subscribed = subscribed
    return True


# ---------------------------------------------------------------------------
# Habits
# ---------------------------------------------------------------------------

#: A habit that is not expected today is not a habit the user failed. The
#: schedule decides whether it counts towards the day at all.
SCHEDULE_DAILY = "daily"
SCHEDULE_WEEKDAYS = "weekdays"
SCHEDULE_PREFIX_DAYS = "days:"


def clean_schedule(value: str | None) -> str:
    """Normalise a schedule, or fall back to daily.

    Anything unrecognised becomes "daily" rather than an error: a habit whose
    schedule cannot be parsed must still be tickable.
    """
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


def habit_is_due(habit: Habit, day: date) -> bool:
    """Whether this habit is expected on that day.

    A paused habit is never due — that is the entire point of pausing — and its
    logs stay on disk, so past reports do not change.
    """
    if habit.paused_at is not None:
        return False
    return day.weekday() in schedule_days(habit.schedule)


def _habit_dict(habit: Habit, day: date, done: bool) -> dict:
    return {
        "id": habit.id, "name": habit.name, "category": habit.category,
        "protected": habit.is_protected, "system_key": habit.system_key,
        "target_time": habit.target_time.strftime("%H:%M") if habit.target_time else None,
        "remind_at": habit.remind_at.strftime("%H:%M") if habit.remind_at else None,
        "schedule": clean_schedule(habit.schedule),
        "days": schedule_days(habit.schedule),
        "paused": habit.paused_at is not None,
        "due": habit_is_due(habit, day),
        "done": done,
    }


def _active_habits(s: Session, ws: int) -> list[Habit]:
    return list(s.scalars(
        select(Habit)
        .where(Habit.workspace_id == ws, Habit.archived_at.is_(None))
        .order_by(Habit.position, Habit.id)).all())


def list_habits(s: Session, ws: int, day: date | None = None, *,
                tz: ZoneInfo | None = None) -> list[dict]:
    """Habits with that day's completion state, in display order.

    Paused habits stay in the list — hidden away, a paused habit is one the
    user cannot resume — but carry `paused: true` and `due: false`.
    """
    day = day or today_local(tz)
    habits = _active_habits(s, ws)
    if not habits:
        return []

    done_ids = set(s.scalars(
        select(HabitLog.habit_id).where(
            HabitLog.workspace_id == ws,
            HabitLog.day == day,
            HabitLog.done.is_(True),
        )
    ).all())

    return [_habit_dict(h, day, h.id in done_ids) for h in habits]


def habits_by_category(s: Session, ws: int, day: date | None = None, *,
                       tz: ZoneInfo | None = None) -> dict:
    """Habits grouped into the three tiers, preserving display order."""
    grouped: dict[str, list[dict]] = {c: [] for c in HABIT_CATEGORIES}
    for habit in list_habits(s, ws, day, tz=tz):
        grouped.setdefault(habit["category"], []).append(habit)
    return grouped


def add_habit(s: Session, ws: int, name: str, category: str = "target", *,
              schedule: str | None = None, remind_at: dtime | None = None) -> Habit:
    name = name.strip()[:120]
    if not name:
        raise ValueError("empty habit name")
    if category not in HABIT_CATEGORIES:
        category = "target"
    top = s.scalar(select(func.max(Habit.position)).where(Habit.workspace_id == ws)) or 0
    habit = Habit(workspace_id=ws, name=name, category=category, position=top + 1,
                  schedule=clean_schedule(schedule), remind_at=remind_at)
    s.add(habit)
    s.commit()
    return habit


def update_habit(s: Session, ws: int, habit_id: int, **fields) -> Habit:
    """Edit a habit in place.

    A habit the user cannot rename or reschedule is one they delete and
    recreate, which throws away every log it had. The name of a derived habit
    is fixed — it is the contract with the module that drives it — but its
    schedule and reminder are the user's to set.
    """
    habit = _owned_habit(s, ws, habit_id)

    if "name" in fields and fields["name"] is not None:
        if habit.is_protected:
            raise ValueError("protected")
        name = str(fields["name"]).strip()[:120]
        if not name:
            raise ValueError("empty habit name")
        habit.name = name
    if fields.get("category") in HABIT_CATEGORIES:
        habit.category = fields["category"]
    if "schedule" in fields and fields["schedule"] is not None:
        habit.schedule = clean_schedule(fields["schedule"])
    if "remind_at" in fields:
        habit.remind_at = fields["remind_at"]
    if "target_time" in fields and fields["target_time"] is not None:
        habit.target_time = fields["target_time"]
    s.commit()
    return habit


def set_habit_paused(s: Session, ws: int, habit_id: int, paused: bool) -> Habit:
    """Pause or resume a habit without touching a single log row."""
    habit = _owned_habit(s, ws, habit_id)
    habit.paused_at = utcnow() if paused else None
    s.commit()
    return habit


def reorder_habits(s: Session, ws: int, habit_ids: list[int]) -> list[dict]:
    """Persist a new display order and return the canonical list.

    Every id must be an active habit of this workspace and each may appear
    once: a partial or padded list would silently reshuffle habits the caller
    never saw. Ids the caller did not send keep their relative order after the
    ones it did, so a stale client cannot lose a habit that was added
    meanwhile. The whole move is one transaction — a half-applied order is
    worse than none.
    """
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
            # Includes another workspace's habit: indistinguishable from a
            # habit that never existed.
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

    The protected `5x namoz` habit is derived from prayer logs, so a manual
    toggle is refused rather than silently ignored.
    """
    habit = _owned_habit(s, ws, habit_id)
    if habit.is_protected:
        raise ValueError("protected")

    day = day or today_local(tz)
    row = s.scalar(select(HabitLog).where(
        HabitLog.workspace_id == ws, HabitLog.habit_id == habit_id, HabitLog.day == day))
    if row is None:
        row = HabitLog(workspace_id=ws, habit_id=habit_id, day=day, done=True,
                       logged_at=now_local(tz))
        s.add(row)
    else:
        row.done = not row.done
        row.logged_at = now_local(tz) if row.done else None
    s.commit()
    return row.done


def delete_habit(s: Session, ws: int, habit_id: int) -> str:
    """Archive a habit, keeping its logs so past reports stay truthful."""
    habit = _owned_habit(s, ws, habit_id)
    if habit.is_protected:
        raise ValueError("protected")
    habit.archived_at = utcnow()
    s.commit()
    return habit.name


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
    if habit is None or habit.paused_at is not None:
        return None

    now = now_local(tz)
    day = now.date()
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
        "logged": row is not None,
        "done": bool(row and row.done),
        "late": now > deadline,
        # The time the user actually got up, so the button can answer with
        # "✓ 04:53" instead of "recorded", which says nothing.
        "at": row.logged_at.strftime("%H:%M") if (row and row.logged_at) else None,
    }


def mark_wakeup(s: Session, ws: int, now: datetime | None = None, *,
                tz: ZoneInfo | None = None) -> dict:
    """Record that the user got up, if they said so in time.

    The rule: "turdim" counts until one hour after the target time. Saying it
    later still records the moment — the user did get up, and hiding that is
    what makes the screen feel like an accusation — but the habit stays undone
    for the day, which is the whole point of the habit.
    """
    habit = wake_habit(s, ws)
    if habit is None:
        raise NotFound("habit")

    now = now or now_local(tz)
    day = now.date()
    target = habit.target_time or DEFAULT_WAKE_TIME
    deadline = datetime.combine(day, target) + WAKE_GRACE
    in_time = now <= deadline

    row = s.scalar(select(HabitLog).where(
        HabitLog.workspace_id == ws, HabitLog.habit_id == habit.id,
        HabitLog.day == day))
    if row is None:
        s.add(HabitLog(workspace_id=ws, habit_id=habit.id, day=day, done=in_time,
                       logged_at=now))
    else:
        row.done = row.done or in_time
        # Keep the first time reported: a second "turdim" is the same morning.
        row.logged_at = row.logged_at or now
    s.commit()

    return {"done": in_time, "late": not in_time, "at": now.strftime("%H:%M"),
            "target": target.strftime("%H:%M"),
            "deadline": deadline.strftime("%H:%M"),
            "now": now.strftime("%H:%M")}


def habit_progress(s: Session, ws: int, day: date) -> tuple[int, int]:
    """(completed, total) habits that were actually expected on that day.

    A habit scheduled for Monday/Wednesday/Friday is not counted on a Tuesday,
    and a paused habit is not counted at all. Counting them would mean the
    user's Tuesday score drops for a gym session they never planned — the exact
    kind of false failure that makes people close the app.
    """
    habits = [h for h in _active_habits(s, ws) if habit_is_due(h, day)]
    if not habits:
        return 0, 0

    due_ids = {h.id for h in habits}
    done_ids = set(s.scalars(select(HabitLog.habit_id).where(
        HabitLog.workspace_id == ws, HabitLog.day == day,
        HabitLog.done.is_(True))).all())
    return len(due_ids & done_ids), len(due_ids)


def habit_history(s: Session, ws: int, habit_id: int, *, days: int = 30,
                  tz: ZoneInfo | None = None) -> dict:
    """One habit's own record: its streak, its grid and its completion rate.

    Only days the habit was scheduled for appear in the grid, so the rate is
    "how often I did it when I meant to" rather than a number diluted by every
    day it was never due.
    """
    habit = _owned_habit(s, ws, habit_id)
    today = today_local(tz)
    start = today - timedelta(days=days - 1)

    done_days = set(s.scalars(select(HabitLog.day).where(
        HabitLog.workspace_id == ws, HabitLog.habit_id == habit_id,
        HabitLog.done.is_(True), HabitLog.day >= start)).all())

    grid, due_count, done_count = [], 0, 0
    for offset in range(days):
        day = start + timedelta(days=offset)
        due = habit_is_due(habit, day)
        done = day in done_days
        if due:
            due_count += 1
            done_count += int(done)
        grid.append({"day": day.isoformat(), "due": due, "done": done})

    last7 = [g for g in grid[-7:] if g["due"]]

    # The streak counts backwards over scheduled days only, and today does not
    # break it while the day is still going.
    streak, cursor, guard = 0, today, 0
    if habit_is_due(habit, today) and today not in done_days:
        cursor = today - timedelta(days=1)
    while guard < 400:
        guard += 1
        if habit_is_due(habit, cursor):
            if cursor not in done_days and cursor >= start:
                break
            if cursor < start:
                # Beyond the window we no longer have the logs loaded; stop
                # rather than guess.
                break
            streak += 1
        cursor -= timedelta(days=1)

    return {
        "id": habit.id, "name": habit.name, "category": habit.category,
        "schedule": clean_schedule(habit.schedule),
        "days": schedule_days(habit.schedule),
        "paused": habit.paused_at is not None,
        "protected": habit.is_protected, "system_key": habit.system_key,
        "target_time": habit.target_time.strftime("%H:%M") if habit.target_time else None,
        "remind_at": habit.remind_at.strftime("%H:%M") if habit.remind_at else None,
        "streak": streak,
        "grid": grid,
        "last7_done": sum(1 for g in last7 if g["done"]), "last7_due": len(last7),
        "last30_done": done_count, "last30_due": due_count,
        "percent": round(done_count / due_count * 100) if due_count else 0,
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
#: of one. The PrayerLog rows stay untouched — this is the derived day score,
#: and the underlying record still says nothing was logged.
EXCUSED_SCORE = PRAYER_MAX_SCORE


def prayer_statuses_for(gender: str | None) -> list[str]:
    return STATUSES_FEMALE if gender == "female" else STATUSES_MALE


def prayer_score(statuses: dict[str, str], gender: str | None,
                 excused: bool = False) -> float:
    """Daily *quality* score from the five prayers, 0 to 5.

    This is how well the day was prayed — jamaat and on-time are worth a full
    point, a qaza half — and it is a separate question from whether all five
    were prayed at all. `prayer_is_complete` answers that one. Conflating them
    is what let three prayers count as "5x namoz bajarildi".
    """
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
    """Whether the `5x namoz` habit is done for the day.

    All five prayed, or an excused day. Nothing in between: four out of five is
    four out of five, and saying otherwise is the app lying to the user about
    their own religious practice.
    """
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
    """Projects, open ones first.

    `status` filters to `active` or `done`; `include_archived` brings back the
    ones put away. A finished project keeps its tasks and its history — the only
    way to lose either is to delete it, which the UI asks about first.
    """
    stmt = select(Project).where(Project.workspace_id == ws)
    if not include_archived:
        stmt = stmt.where(Project.archived_at.is_(None))
    if status in PROJECT_STATUSES:
        stmt = stmt.where(Project.status == status)

    projects = s.scalars(stmt.order_by(Project.created_at.desc())).all()
    rows = [_project_dict(s, ws, p) for p in projects]
    # Active before finished, so the work in progress is never below the
    # archive of things already closed.
    rows.sort(key=lambda p: (p["status"] == "done", p["archived"]))
    return rows


def add_project(s: Session, ws: int, name: str, *, description: str = "",
                deadline: date | None = None) -> Project:
    name = name.strip()[:200]
    if not name:
        raise ValueError("empty project name")
    project = Project(workspace_id=ws, name=name,
                      description=description.strip()[:2000], deadline=deadline)
    s.add(project)
    s.commit()
    return project


def _owned_project(s: Session, ws: int, project_id: int, *,
                   allow_archived: bool = False) -> Project:
    project = s.get(Project, project_id)
    if project is None or project.workspace_id != ws:
        raise NotFound("project")
    if project.archived_at and not allow_archived:
        raise NotFound("project")
    return project


def update_project(s: Session, ws: int, project_id: int, **fields) -> Project:
    """Rename a project, or adjust its description and deadline.

    A project the user cannot rename is a typo they have to live with, or a
    reason to delete and recreate — which detaches every task inside it.
    """
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
    return [_task_dict(s, ws, task, today) for task in rows]


def delete_project(s: Session, ws: int, project_id: int) -> str:
    """Archive a project and detach its tasks. Tasks are never deleted with it."""
    project = s.get(Project, project_id)
    if project is None or project.workspace_id != ws:
        raise NotFound("project")
    for task in s.scalars(select(Task).where(
            Task.workspace_id == ws, Task.project_id == project_id)).all():
        task.project_id = None
    project.archived_at = utcnow()
    s.commit()
    return project.name


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

PRIORITIES = ["high", "medium", "low"]

#: Sort order wherever tasks are listed: the order a person would use when
#: asked which to start with.
_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}

#: Fixed recurrences, plus "days:0,2,4" for a hand-picked set. Everything else
#: is a one-off, because a recurrence nobody can describe is a bug generator.
RECURRENCES = ["daily", "weekdays", "weekly", "monthly"]

#: Reminder offsets in minutes before the due moment, as offered in the UI.
#: 0 means exactly on time.
REMINDER_OFFSETS = [0, 10, 60, 1440]
#: A reminder cannot be asked for further ahead than this.
MAX_REMIND_BEFORE = 60 * 24 * 7


def clean_recurrence(value: str | None) -> str:
    """Normalise a recurrence, or return "" for a one-off task."""
    value = (value or "").strip().lower()
    if value in RECURRENCES:
        return value
    if value.startswith(SCHEDULE_PREFIX_DAYS):
        days = sorted({int(x) for x in value[len(SCHEDULE_PREFIX_DAYS):].split(",")
                       if x.strip().isdigit() and 0 <= int(x) <= 6})
        if not days:
            return ""
        if len(days) == 7:
            return "daily"
        return SCHEDULE_PREFIX_DAYS + ",".join(str(d) for d in days)
    return ""


def next_occurrence(recurrence: str | None, after: date) -> date | None:
    """The next date a recurring task is due, strictly after `after`."""
    rule = clean_recurrence(recurrence)
    if not rule:
        return None
    if rule == "daily":
        return after + timedelta(days=1)
    if rule == "weekly":
        return after + timedelta(days=7)
    if rule == "monthly":
        year, month = after.year + (after.month == 12), (after.month % 12) + 1
        # Clamp so the 31st of a 30-day month lands on the last day instead of
        # raising, and a monthly task never silently stops recurring.
        last = (date(year + (month == 12), (month % 12) + 1, 1)
                - timedelta(days=1)).day
        return date(year, month, min(after.day, last))

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
             recurrence: str | None = None) -> Task:
    title = title.strip()[:300]
    if not title:
        raise ValueError("empty task title")
    if priority not in PRIORITIES:
        priority = "medium"
    if project_id is not None:
        # A task may only join a project inside the same workspace.
        project = s.get(Project, project_id)
        if project is None or project.workspace_id != ws:
            raise NotFound("project")

    task = Task(workspace_id=ws, title=title, deadline=deadline,
                project_id=project_id, priority=priority,
                description=description.strip()[:4000],
                due_time=due_time,
                remind_before=clean_remind_before(remind_before),
                recurrence=clean_recurrence(recurrence))
    s.add(task)
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

    Ticking a recurring task off must not end the recurrence — the whole point
    of "every weekday" is that tomorrow's copy appears. The finished occurrence
    stays in the archive with its own completion date, so the history of a
    recurring habit-like task is real rather than a single row overwritten
    forever.
    """
    rule = clean_recurrence(task.recurrence)
    if not rule:
        return None

    base = task.deadline or today_local(tz)
    nxt = next_occurrence(rule, base)
    if nxt is None:
        return None
    # Never fall behind: after a long gap, the next copy is the next date from
    # today rather than a run of overdue clones.
    today = today_local(tz)
    while nxt < today:
        following = next_occurrence(rule, nxt)
        if following is None or following == nxt:
            break
        nxt = following

    existing = s.scalar(select(Task.id).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.title == task.title,
        Task.recurrence == rule, Task.deadline == nxt))
    if existing is not None:
        return None

    clone = Task(workspace_id=ws, title=task.title, description=task.description,
                 project_id=task.project_id, deadline=nxt, due_time=task.due_time,
                 remind_before=task.remind_before, recurrence=rule,
                 priority=task.priority)
    s.add(clone)
    s.flush()
    return clone


def complete_task(s: Session, ws: int, task_id: int, *,
                  tz: ZoneInfo | None = None) -> Task:
    task = _owned_task(s, ws, task_id)
    already_done = task.status == "done"
    task.status = "done"
    task.completed_at = utcnow()
    if not already_done:
        _spawn_next_occurrence(s, ws, task, tz)
    s.commit()
    return task


def reopen_task(s: Session, ws: int, task_id: int) -> Task:
    task = _owned_task(s, ws, task_id)
    task.status = "waiting"
    task.completed_at = None
    s.commit()
    return task


def reschedule_task(s: Session, ws: int, task_id: int, when: str, *,
                    tz: ZoneInfo | None = None) -> Task:
    """Move a task's deadline with one tap.

    `today` / `tomorrow` / `week` / `none` — the four answers to "not today",
    which is the only useful thing to offer someone looking at an overdue list.
    """
    task = _owned_task(s, ws, task_id)
    today = today_local(tz)
    if when == "today":
        task.deadline = today
    elif when == "tomorrow":
        task.deadline = today + timedelta(days=1)
    elif when == "week":
        task.deadline = today + timedelta(days=7)
    elif when == "none":
        task.deadline = None
    else:
        raise ValueError("unknown target")
    # A rescheduled task deserves a fresh reminder.
    task.reminder_sent_at = None
    s.commit()
    return task


#: Exactly one. "The most important thing today" is singular by definition, and
#: a list of three of them is a list. The column stays `focus_day`, so the pick
#: expires on its own overnight rather than needing to be cleared.
MAX_TOP3 = 1


def set_top3(s: Session, ws: int, task_id: int, picked: bool,
             day: date | None = None, *, tz: ZoneInfo | None = None) -> dict:
    """Pick or unpick one of today's three most important tasks."""
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
        # With a limit of one, refusing would be a dead end: the user asked for
        # *this* task to be the day's, so the previous one steps aside.
        if MAX_TOP3 == 1:
            for previous in current:
                previous.focus_day = None
        else:
            raise ValueError("top3 full")

    task.focus_day = day
    # Picking a task for today is also a statement that it is due today.
    if task.deadline is None or task.deadline > day:
        task.deadline = day
    s.commit()
    return {"picked": True, "count": len(current) + 1}


def top3_tasks(s: Session, ws: int, day: date | None = None, *,
               tz: ZoneInfo | None = None) -> list[dict]:
    """The three the user chose for today, open and finished alike.

    Finished ones stay in place, ticked: crossing something off and watching it
    vanish removes the only reward the list offers.
    """
    day = day or today_local(tz)
    rows = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.focus_day == day).order_by(Task.id)).all()
    return [_task_dict(s, ws, task, day) for task in rows]


def delete_task(s: Session, ws: int, task_id: int) -> str:
    task = _owned_task(s, ws, task_id)
    task.archived_at = utcnow()
    s.commit()
    return task.title


def update_task(s: Session, ws: int, task_id: int, **fields) -> Task:
    task = _owned_task(s, ws, task_id)
    if "title" in fields and fields["title"]:
        task.title = str(fields["title"]).strip()[:300]
    if "description" in fields:
        task.description = str(fields["description"] or "").strip()[:4000]
    if "priority" in fields and fields["priority"] in PRIORITIES:
        task.priority = fields["priority"]
    if "deadline" in fields:
        task.deadline = fields["deadline"]
        task.reminder_sent_at = None
    if "due_time" in fields:
        task.due_time = fields["due_time"]
        task.reminder_sent_at = None
    if "remind_before" in fields:
        task.remind_before = clean_remind_before(fields["remind_before"])
        task.reminder_sent_at = None
    if "recurrence" in fields:
        task.recurrence = clean_recurrence(fields["recurrence"])
    if "project_id" in fields:
        pid = fields["project_id"]
        if pid:
            project = s.get(Project, pid)
            if project is None or project.workspace_id != ws:
                raise NotFound("project")
            task.project_id = project.id
        else:
            task.project_id = None
    if "status" in fields and fields["status"] in ("waiting", "done"):
        was_done = task.status == "done"
        task.status = fields["status"]
        task.completed_at = utcnow() if fields["status"] == "done" else None
        if fields["status"] == "done" and not was_done:
            _spawn_next_occurrence(s, ws, task)
    s.commit()
    return task


def _task_dict(s: Session, ws: int, task: Task, today: date) -> dict:
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
    }


def _sort_open(rows: list[dict]) -> list[dict]:
    """Deadline, then time of day, then priority — the order to work in."""
    return sorted(rows, key=lambda t: (
        t["deadline"] or "9999-12-31", t["due_time"] or "99:99",
        _PRIORITY_RANK.get(t["priority"], 1), t["id"]))


def list_tasks(s: Session, ws: int, *, horizon_days: int = 7,
               include_done: bool = False, search: str = "",
               project_id: int | None = None, priority: str = "",
               tz: ZoneInfo | None = None) -> dict:
    """Tasks grouped for display: overdue, the next N days, and undated.

    `search`, `project_id` and `priority` narrow the result. Once a list passes
    thirty rows, scrolling stops being a way to find anything, and the filters
    are cheaper than making the user remember where they put it.
    """
    today = today_local(tz)
    limit = today + timedelta(days=horizon_days)
    needle = search.strip().lower()[:100]

    stmt = select(Task).where(Task.workspace_id == ws, Task.archived_at.is_(None))
    if not include_done:
        stmt = stmt.where(Task.status == "waiting")
    if project_id is not None:
        stmt = stmt.where(Task.project_id == project_id)
    if priority in PRIORITIES:
        stmt = stmt.where(Task.priority == priority)
    tasks = s.scalars(stmt.order_by(Task.deadline.is_(None), Task.deadline,
                                    Task.priority)).all()

    overdue, upcoming, undated, later = [], [], [], []
    for task in tasks:
        if needle and needle not in (task.title or "").lower() \
                and needle not in (task.description or "").lower():
            continue
        row = _task_dict(s, ws, task, today)
        if task.deadline is None:
            undated.append(row)
        elif task.deadline < today and task.status != "done":
            overdue.append(row)
        elif task.deadline <= limit:
            upcoming.append(row)
        else:
            later.append(row)
    return {"overdue": _sort_open(overdue), "upcoming": _sort_open(upcoming),
            "undated": _sort_open(undated), "later": _sort_open(later),
            "total": len(overdue) + len(upcoming) + len(undated) + len(later)}


def completed_tasks(s: Session, ws: int, limit: int = 200, *, search: str = "",
                    tz: ZoneInfo | None = None) -> dict:
    """The Done archive, in three buckets rather than one endless list.

    Today / this week / earlier: finishing something an hour ago and finishing
    it in March are not the same fact, and one flat list treats them as if they
    were.
    """
    today = today_local(tz)
    monday = week_start(today)
    needle = search.strip().lower()[:100]

    rows = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "done")
        .order_by(Task.completed_at.desc()).limit(limit)).all()

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
    today = today_local(tz)
    tasks = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline == today)).all()
    return _sort_open([_task_dict(s, ws, t, today) for t in tasks])


def today_tasks_by_project(s: Session, ws: int, *, tz: ZoneInfo | None = None,
                           skip_ids: set[int] | None = None) -> list[dict]:
    """Today's open tasks, grouped under the project they belong to.

    Home shows today and only today. A week's worth of rows is a backlog, and
    a backlog is what the user opens the app to escape. `skip_ids` leaves out
    the ones already shown in the top three, so nothing appears twice.
    """
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

    # Named projects first, in a stable order; standalone tasks last, because
    # "Alohida" is a leftover bucket rather than a project.
    named = sorted((g for g in groups.values() if g["project_id"] is not None),
                   key=lambda g: (g["project"] or "").lower())
    standalone = [g for g in groups.values() if g["project_id"] is None]
    return named + standalone


# ---------------------------------------------------------------------------
# Weekly mission — exactly one per week
# ---------------------------------------------------------------------------

#: Slot 1 is the week's mission; slots 2 and 3 are supporting priorities, drawn
#: smaller. One dominant goal with two things beside it is a week a person can
#: hold in their head; three equal goals is a list.
MAX_FOCUS = 3
PRIMARY_SLOT = 1

MISSION_PRIORITIES = ["high", "medium", "low"]
DEFAULT_MISSION_PRIORITY = "medium"


def _focus_dict(row: WeeklyFocus) -> dict:
    return {"id": row.id, "slot": row.slot, "title": row.title,
            "priority": row.priority if row.priority in MISSION_PRIORITIES
                        else DEFAULT_MISSION_PRIORITY,
            "primary": row.slot == PRIMARY_SLOT,
            "done": row.done}


def list_focus(s: Session, ws: int, when: date | None = None, *,
               tz: ZoneInfo | None = None) -> list[dict]:
    start = week_start(when or today_local(tz))
    rows = s.scalars(select(WeeklyFocus).where(
        WeeklyFocus.workspace_id == ws, WeeklyFocus.week_start == start)
        .order_by(WeeklyFocus.slot, WeeklyFocus.id)).all()
    return [_focus_dict(r) for r in rows]


def week_focus(s: Session, ws: int, when: date | None = None, *,
               tz: ZoneInfo | None = None) -> dict:
    """The week split into its one mission and its supporting priorities.

    The split is by slot, so every surface names the same primary — a screen
    that picks "the first one it happens to read" would disagree with itself
    across reloads.
    """
    rows = list_focus(s, ws, when, tz=tz)
    primary = next((r for r in rows if r["slot"] == PRIMARY_SLOT), None)
    supporting = [r for r in rows if r["slot"] != PRIMARY_SLOT]
    return {
        "primary": primary, "supporting": supporting,
        "slots_free": max(MAX_FOCUS - len(rows), 0),
        "done": sum(1 for r in rows if r["done"]), "total": len(rows),
    }


def primary_focus(s: Session, ws: int, when: date | None = None, *,
                  tz: ZoneInfo | None = None) -> dict | None:
    """The one mission Home leads with, or the first supporting row if the
    primary slot was never filled (older weeks can start at slot 2)."""
    rows = list_focus(s, ws, when, tz=tz)
    if not rows:
        return None
    return next((r for r in rows if r["slot"] == PRIMARY_SLOT), rows[0])


def add_focus(s: Session, ws: int, title: str, when: date | None = None, *,
              priority: str = DEFAULT_MISSION_PRIORITY,
              tz: ZoneInfo | None = None) -> WeeklyFocus:
    title = title.strip()[:200]
    if not title:
        raise ValueError("empty focus title")
    if priority not in MISSION_PRIORITIES:
        priority = DEFAULT_MISSION_PRIORITY
    start = week_start(when or today_local(tz))
    used = {r.slot for r in s.scalars(select(WeeklyFocus).where(
        WeeklyFocus.workspace_id == ws, WeeklyFocus.week_start == start)).all()}
    free = next((n for n in range(1, MAX_FOCUS + 1) if n not in used), None)
    if free is None:
        raise ValueError("week is full")
    row = WeeklyFocus(workspace_id=ws, week_start=start, slot=free, title=title,
                      priority=priority)
    s.add(row)
    s.commit()
    return row


def carry_focus_forward(s: Session, ws: int, focus_id: int, *,
                        tz: ZoneInfo | None = None) -> WeeklyFocus:
    """Move an unfinished mission into next week.

    A week that ends with the mission untouched has two honest answers — it
    still matters, or it does not. This is the first one, and it takes one tap
    instead of retyping the title.
    """
    row = s.get(WeeklyFocus, focus_id)
    if row is None or row.workspace_id != ws:
        raise NotFound("focus")

    target = row.week_start + timedelta(days=7)
    used = {r.slot for r in s.scalars(select(WeeklyFocus).where(
        WeeklyFocus.workspace_id == ws, WeeklyFocus.week_start == target)).all()}
    free = next((n for n in range(1, MAX_FOCUS + 1) if n not in used), None)
    if free is None:
        raise ValueError("week is full")

    moved = WeeklyFocus(workspace_id=ws, week_start=target, slot=free,
                        title=row.title, priority=row.priority)
    s.add(moved)
    s.delete(row)
    s.commit()
    return moved


def edit_focus(s: Session, ws: int, focus_id: int, title: str, *,
               priority: str | None = None) -> WeeklyFocus:
    row = s.get(WeeklyFocus, focus_id)
    if row is None or row.workspace_id != ws:
        raise NotFound("focus")
    title = title.strip()[:200]
    if not title:
        raise ValueError("empty focus title")
    row.title = title
    if priority in MISSION_PRIORITIES:
        row.priority = priority
    s.commit()
    return row


def toggle_focus(s: Session, ws: int, focus_id: int) -> bool:
    row = s.get(WeeklyFocus, focus_id)
    if row is None or row.workspace_id != ws:
        raise NotFound("focus")
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

#: The daily journal is five fixed questions. All five answered marks the day
#: complete, which Home and the reports show as a status of its own.
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


#: The five moods the optional check-in offers, saddest first. Stored as one of
#: these keys; the UI supplies the face and the wording.
MOODS = ["awful", "low", "ok", "good", "great"]


def journal_answered(answers: dict) -> int:
    """How many of the five have something in them."""
    return sum(1 for key in JOURNAL_KEYS if str(answers.get(key, "")).strip())


def journal_is_complete(answers: dict) -> bool:
    return journal_answered(answers) == len(JOURNAL_KEYS)


def journal_done(s: Session, ws: int, day: date | None = None, *,
                 tz: ZoneInfo | None = None) -> bool:
    """Whether the day's journal is fully answered.

    This is a status the UI shows next to the habits, not a habit itself, and it
    is deliberately kept out of the overall percentage: three answers out of
    five is a journal entry, not a failed day.
    """
    entry = get_journal(s, ws, day or today_local(tz))
    return bool(entry and entry["complete"])


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
            "answers": answers, "answered": journal_answered(answers),
            "total": len(JOURNAL_KEYS),
            "complete": journal_is_complete(answers)}


def save_journal(s: Session, ws: int, *, answers: dict | None = None,
                 text: str = "", day: date | None = None,
                 mood: str = "", tz: ZoneInfo | None = None) -> JournalEntry:
    """Save whatever is written so far.

    A partial save is a normal save. Three answers are kept as three answers,
    and completion is derived on read, so there is no state to keep in sync and
    no reason to refuse an entry for being unfinished.
    """
    day = day or today_local(tz)
    row = s.scalar(select(JournalEntry).where(
        JournalEntry.workspace_id == ws, JournalEntry.day == day))
    if row is None:
        row = JournalEntry(workspace_id=ws, day=day)
        s.add(row)

    if answers is not None:
        # Merge rather than replace: an autosave that carries one field must not
        # wipe the four the user filled in earlier.
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
    """Tick the protected `Kundalik` habit only on a fully answered day.

    Derived exactly like the prayer habit, and for the same reason: the habit is
    a mirror of the module, never a separate thing the user can tick by hand.
    Three answers out of five is a saved journal entry and an unfinished habit —
    both statements are true at once, and neither one overrides the other.
    """
    habit = s.scalar(select(Habit).where(
        Habit.workspace_id == ws, Habit.system_key == SYSTEM_JOURNAL,
        Habit.archived_at.is_(None)))
    if habit is None:
        return False

    entry = get_journal(s, ws, day)
    done = bool(entry and entry["complete"])

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
    """This year's birthday, or next year's if it already passed.

    29 February falls back to 28 February in common years.
    """
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
# Statistics — streaks and chart series
# ---------------------------------------------------------------------------

def _habit_percent(s: Session, ws: int, day: date) -> int:
    done, total = habit_progress(s, ws, day)
    return round(done / total * 100) if total else 0


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
    """Consecutive days on which every habit that was due got done.

    Today may still be incomplete without breaking the streak — the day is not
    over — so counting starts from yesterday when today is unfinished. Days on
    which nothing was scheduled are skipped rather than counted as failures:
    a habit set to weekdays should not lose its streak every Saturday.
    """
    today = today_local(tz)
    habits = _active_habits(s, ws)
    if not habits:
        return 0

    earliest = today - timedelta(days=400)
    done_by_day: dict[date, set[int]] = {}
    for habit_id, day in s.execute(select(HabitLog.habit_id, HabitLog.day).where(
            HabitLog.workspace_id == ws, HabitLog.done.is_(True),
            HabitLog.day >= earliest)).all():
        done_by_day.setdefault(day, set()).add(habit_id)

    def complete(day: date) -> bool | None:
        """True/False, or None when the day had nothing due."""
        due = {h.id for h in habits if habit_is_due(h, day)}
        if not due:
            return None
        return due <= done_by_day.get(day, set())

    cursor = today
    if complete(today) is not True:
        cursor = today - timedelta(days=1)

    streak = 0
    for _ in range(400):
        state = complete(cursor)
        if state is False:
            break
        if state is True:
            streak += 1
        cursor -= timedelta(days=1)
    return streak


def prayer_streak(s: Session, ws: int, gender: str | None = None, *,
                  tz: ZoneInfo | None = None) -> int:
    """Consecutive days on which all five prayers were prayed.

    The same rule as the habit: a full day, not a score above some threshold.
    Today does not break the streak while it is still going.
    """
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
    """What the prayer numbers actually consist of, over a range.

    One opaque score tells the user nothing they can act on. Full days, on-time
    share, jamaat count, qaza count and misses are five separate facts, and each
    one suggests a different change.
    """
    rows = s.scalars(select(PrayerLog).where(
        PrayerLog.workspace_id == ws,
        PrayerLog.day >= start, PrayerLog.day <= end)).all()

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1

    logged = sum(counts.values())
    performed = sum(counts.get(k, 0) for k in PRAYER_PERFORMED)

    days = (end - start).days + 1
    full_days = 0
    for offset in range(max(days, 0)):
        if _prayer_day_complete(s, ws, start + timedelta(days=offset), gender):
            full_days += 1

    return {
        "counts": counts,
        "full_days": full_days,
        "days": max(days, 0),
        "jamaat": counts.get("jamaat", 0),
        "on_time": counts.get("on_time", 0),
        "qaza": counts.get("qaza", 0),
        "missed": counts.get("missed", 0),
        # Of the prayers that were prayed, how many were on time or in jamaat.
        "on_time_percent": (round((counts.get("on_time", 0) + counts.get("jamaat", 0))
                                  / performed * 100) if performed else 0),
        # Of the five expected each day, how many were logged at all.
        "logged_percent": round(logged / (days * 5) * 100) if days > 0 else 0,
        "consistency": round(full_days / days * 100) if days > 0 else 0,
    }


# ---------------------------------------------------------------------------
# The one overall number
# ---------------------------------------------------------------------------
#
# Every surface — the bot's Home, the Mini App's Home, the Statistics page and
# the evening report — reads this function. There is no second copy of the
# formula in JavaScript: two implementations drift, and a user who sees 80% in
# the bot and 74% in the app stops believing either.

#: Shown when the day has nothing measurable in it yet.
EMPTY_OVERALL = 0


def today_task_progress(s: Session, ws: int, day: date | None = None) -> tuple[int, int]:
    """(completed, total) tasks that belong to this day.

    Only tasks actually scheduled for the day count. Folding in the whole
    backlog would mean a user with 200 open tasks can never move the number,
    and finishing today's work would not show up at all.
    """
    day = day or today_local()
    total = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.deadline == day)) or 0
    done = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.deadline == day, Task.status == "done")) or 0
    return done, total


def overall_components(s: Session, ws: int, day: date | None = None) -> dict:
    """Each component's percentage, or None when it has no denominator today.

    A category with nothing due is *absent*, not zero. Counting an empty
    category as 0% would punish a user for a day with no tasks, which is the
    opposite of what the number is for.
    """
    day = day or today_local()

    habits_done, habits_total = habit_progress(s, ws, day)
    tasks_done, tasks_total = today_task_progress(s, ws, day)
    prayer_row = s.scalar(select(PrayerDay).where(
        PrayerDay.workspace_id == ws, PrayerDay.day == day))
    prayer_habit = s.scalar(select(Habit.id).where(
        Habit.workspace_id == ws, Habit.system_key == SYSTEM_PRAYER,
        Habit.archived_at.is_(None)))

    return {
        "habits": round(habits_done / habits_total * 100) if habits_total else None,
        "tasks": round(tasks_done / tasks_total * 100) if tasks_total else None,
        # Prayer's denominator is the five daily prayers, which exist for as
        # long as the user keeps the habit — not only on days they logged one.
        "prayer": (round(float(prayer_row.score if prayer_row else 0.0)
                         / PRAYER_MAX_SCORE * 100)
                   if prayer_habit is not None else None),
    }


def overall_percent(s: Session, ws: int, day: date | None = None) -> int:
    """The arithmetic mean of whichever components exist today."""
    available = [v for v in overall_components(s, ws, day).values() if v is not None]
    if not available:
        return EMPTY_OVERALL
    return round(sum(available) / len(available))


def overall_state(s: Session, ws: int, day: date | None = None) -> dict:
    """Today's overall number plus how it compares with yesterday.

    The two days are only comparable when both had something to measure;
    otherwise the trend is `flat` rather than an invented drop.
    """
    day = day or today_local()
    yesterday = day - timedelta(days=1)

    today_components = overall_components(s, ws, day)
    today_available = [v for v in today_components.values() if v is not None]
    y_available = [v for v in overall_components(s, ws, yesterday).values()
                   if v is not None]

    value = round(sum(today_available) / len(today_available)) if today_available \
        else EMPTY_OVERALL
    previous = round(sum(y_available) / len(y_available)) if y_available else None

    if previous is None or not today_available or value == previous:
        trend = "flat"
    else:
        trend = "up" if value > previous else "down"

    return {"value": value, "trend": trend, "yesterday": previous,
            "components": today_components}


def _task_percent(s: Session, ws: int, day: date) -> int:
    done, total = today_task_progress(s, ws, day)
    return round(done / total * 100) if total else 0


def _overall_percent_for(s: Session, ws: int, day: date) -> int:
    values = [v for v in overall_components(s, ws, day).values() if v is not None]
    return round(sum(values) / len(values)) if values else 0


def _day_point(s: Session, ws: int, day: date) -> dict:
    return {
        "habits": _habit_percent(s, ws, day),
        "prayer": _prayer_percent(s, ws, day),
        "tasks": _task_percent(s, ws, day),
        "overall": _overall_percent_for(s, ws, day),
    }


def _range_average(s: Session, ws: int, start: date, end: date) -> dict:
    """Average of each series across an inclusive day range."""
    days = (end - start).days + 1
    if days <= 0:
        return {k: 0 for k in ("habits", "prayer", "tasks", "overall")}
    totals = {k: 0 for k in ("habits", "prayer", "tasks", "overall")}
    for offset in range(days):
        point = _day_point(s, ws, start + timedelta(days=offset))
        for key in totals:
            totals[key] += point[key]
    return {key: round(value / days) for key, value in totals.items()}


def _range_percent(s: Session, ws: int, start: date, end: date) -> tuple[int, int]:
    """(habit %, prayer %) across a range. Kept for the reports and the review."""
    avg = _range_average(s, ws, start, end)
    return avg["habits"], avg["prayer"]


#: What each component of the overall number means and where it comes from, so
#: the info panel is generated from the same place the number is.
OVERALL_COMPONENTS = ["tasks", "habits", "prayer"]


def overall_explain(s: Session, ws: int, user: User,
                    day: date | None = None) -> dict:
    """The arithmetic behind today's percentage, component by component.

    A number the user cannot check is a number they stop trusting. This returns
    each part with its own numerator and denominator, which parts were counted,
    and the mean that produced the headline — the same values, from the same
    functions, that produced it.
    """
    tz = user_tz(user)
    day = day or today_local(tz)

    tasks_done, tasks_total = today_task_progress(s, ws, day)
    habits_done, habits_total = habit_progress(s, ws, day)
    prayer = prayer_state(s, ws, day, user.gender)
    components = overall_components(s, ws, day)
    counted = [k for k in OVERALL_COMPONENTS if components.get(k) is not None]

    return {
        "day": day.isoformat(),
        "value": overall_percent(s, ws, day),
        "counted": counted,
        "parts": [
            {"key": "tasks", "percent": components["tasks"],
             "done": tasks_done, "total": tasks_total},
            {"key": "habits", "percent": components["habits"],
             "done": habits_done, "total": habits_total},
            {"key": "prayer", "percent": components["prayer"],
             "done": prayer["score"], "total": PRAYER_MAX_SCORE},
        ],
        # Spelled out so the UI never has to reimplement the rule.
        "rule": "mean_of_available",
    }


def stats(s: Session, ws: int, period: str = "week", *,
          gender: str | None = None, tz: ZoneInfo | None = None) -> dict:
    """Series for the charts, plus streaks, averages and what changed.

    `week`  — one point per day for the last 7 days.
    `month` — one point per day for the last 30 days.
    `year`  — one point per month for the last 12 months, because 365 daily
              points are unreadable on a phone.

    Every period carries the previous one's averages as `deltas`, because "74%"
    means nothing on its own and "74%, down from 81%" is something to act on.
    """
    today = today_local(tz)
    keys = ("habits", "prayer", "tasks", "overall")

    if period == "year":
        series = []
        year, month = today.year, today.month
        months = []
        for _ in range(12):
            months.append((year, month))
            month -= 1
            if month == 0:
                month, year = 12, year - 1
        for y, m in reversed(months):
            start = date(y, m, 1)
            end = min(today, date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1))
            avg = _range_average(s, ws, start, end)
            series.append({"day": start.isoformat(),
                           "label": start.strftime("%m.%y"), **avg})
        window_start = date(months[-1][0], months[-1][1], 1)
        previous_start = window_start - timedelta(days=365)
        previous_end = window_start - timedelta(days=1)
    else:
        days = 7 if period == "week" else 30
        series = []
        for offset in range(days - 1, -1, -1):
            day = today - timedelta(days=offset)
            series.append({"day": day.isoformat(),
                           "label": day.strftime("%d.%m"),
                           **_day_point(s, ws, day)})
        window_start = today - timedelta(days=days - 1)
        previous_start = window_start - timedelta(days=days)
        previous_end = window_start - timedelta(days=1)

    averages = {key: round(sum(p[key] for p in series) / len(series))
                for key in keys}
    previous = _range_average(s, ws, previous_start, previous_end)
    deltas = {key: averages[key] - previous[key] for key in keys}

    # The strongest day in the window, by the overall number. Only meaningful
    # once something has actually been measured.
    best = max(series, key=lambda p: p["overall"]) if series else None
    if best and best["overall"] <= 0:
        best = None

    breakdown = prayer_breakdown(s, ws, window_start, today, gender)

    # The headline numbers are today's, from the same function Home uses, so
    # the two screens can never disagree. The series stays a period average.
    overall = overall_state(s, ws, today)
    components = overall["components"]
    prayer_today = prayer_state(s, ws, today, gender)
    streak = habit_streak(s, ws, tz=tz)

    return {
        "period": period,
        "series": series,
        "averages": averages,
        "previous": previous,
        "deltas": deltas,
        "best_day": best and {"day": best["day"], "label": best["label"],
                              "overall": best["overall"]},
        # Kept under their original names: the bot renderer and the CSV read them.
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
            "trend": overall["trend"],
            "tasks": components["tasks"],
            "habits": components["habits"],
            "prayer": components["prayer"],
            "prayer_score": prayer_today["score"],
            "prayer_max": PRAYER_MAX_SCORE,
            "prayer_performed": prayer_today["performed"],
            "prayer_required": PRAYER_REQUIRED,
            "streak": streak,
        },
    }


#: The windows the summary compares: today, the last 7 days, the last 30.
SUMMARY_WINDOWS = {"day": 1, "week": 7, "month": 30}


def summary(s: Session, ws: int, *, gender: str | None = None,
            tz: ZoneInfo | None = None) -> dict:
    """Today, this week and this month as directly comparable numbers.

    One percentage on its own is not information. Three windows in the same
    units are: today against the week says whether today is going well, and the
    week against the month says whether the direction is holding. Each window
    also carries its change against the previous window of the same length, so
    "74%" is never the whole story.
    """
    today = today_local(tz)
    out = {"today": overall_state(s, ws, today), "windows": {}}

    for name, days in SUMMARY_WINDOWS.items():
        end = today
        start = today - timedelta(days=days - 1)
        current = _range_average(s, ws, start, end)
        previous = _range_average(s, ws, start - timedelta(days=days),
                                  start - timedelta(days=1))
        out["windows"][name] = {
            **current,
            "days": days,
            "delta": current["overall"] - previous["overall"],
            "previous": previous["overall"],
        }

    prayer = prayer_state(s, ws, today, gender)
    habits_done, habits_total = habit_progress(s, ws, today)
    tasks_done, tasks_total = today_task_progress(s, ws, today)
    components = out["today"]["components"]

    out["today"] = {
        "overall": out["today"]["value"],
        "trend": out["today"]["trend"],
        "tasks": components["tasks"], "habits": components["habits"],
        "prayer": components["prayer"],
        "tasks_done": tasks_done, "tasks_total": tasks_total,
        "habits_done": habits_done, "habits_total": habits_total,
        "prayer_performed": prayer["performed"],
        "prayer_required": PRAYER_REQUIRED,
        "prayer_score": prayer["score"], "prayer_max": PRAYER_MAX_SCORE,
        "streak": habit_streak(s, ws, tz=tz),
    }
    return out


def stats_csv(s: Session, ws: int, period: str = "month", *,
              gender: str | None = None, tz: ZoneInfo | None = None) -> str:
    """The statistics view as CSV, for the download button.

    CSV rather than PDF: it opens in Excel, Numbers and Google Sheets without
    a viewer, and the file stays a few kilobytes.
    """
    data = stats(s, ws, period, gender=gender, tz=tz)
    detail = data["prayer_detail"]
    lines = ["ErnestOS statistics"]
    lines.append(f"period,{period}")
    lines.append(f"generated,{datetime.now(tz or TZ):%Y-%m-%d %H:%M}")
    lines.append("")
    lines.append(f"overall average %,{data['overall_avg']}")
    lines.append(f"task average %,{data['task_avg']}")
    lines.append(f"habit average %,{data['habit_avg']}")
    lines.append(f"prayer average %,{data['prayer_avg']}")
    lines.append(f"habit streak,{data['habit_streak']}")
    lines.append(f"prayer streak,{data['prayer_streak']}")
    lines.append("")
    lines.append(f"prayer full days,{detail['full_days']} of {detail['days']}")
    lines.append(f"prayer on-time %,{detail['on_time_percent']}")
    lines.append("prayer status,count")
    for status, count in sorted(detail["counts"].items()):
        lines.append(f"{status},{count}")
    lines.append("")
    lines.append("date,overall %,tasks %,habits %,prayer %")
    for point in data["series"]:
        lines.append(f"{point['day']},{point['overall']},{point['tasks']},"
                     f"{point['habits']},{point['prayer']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Calendar — one month of deadlines
# ---------------------------------------------------------------------------

def calendar_month(s: Session, ws: int, year: int, month: int, *,
                   tz: ZoneInfo | None = None) -> dict:
    """Every dated item inside one month, keyed by ISO date.

    Each event carries its kind and its id, so tapping one opens the actual
    task, project or birthday rather than a dead row of text.
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
            Project.workspace_id == ws, Project.archived_at.is_(None),
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
    """Whether this user is returning from a gap, and what is waiting.

    Someone who has been away for a week should be met with one decision, not
    a backlog and a broken streak.
    """
    today = today_local(tz or user_tz(user))
    last_seen = (user.last_active_at.date() if user.last_active_at else today)
    away = (today - last_seen).days

    overdue = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline < today)) or 0

    return {"days_away": away, "overdue": overdue,
            "suggest_reset": away >= BREAK_DAYS and overdue > 0}


#: What each fresh-start mode does, so the confirmation text and the code cannot
#: disagree about it. Nothing here deletes a row.
FRESH_START_MODES = {
    "today": "move every overdue task to today",
    "week": "spread overdue tasks across the coming week",
    "undate": "drop the deadlines, keep the tasks",
    "archive": "put overdue tasks in the archive",
}


def fresh_start(s: Session, ws: int, *, mode: str = "today",
                tz: ZoneInfo | None = None) -> int:
    """Clear the backlog in one move. Returns how many tasks were handled.

    `today`   — pull every overdue task to today, keep them all.
    `week`    — spread them over the next seven days, a few per day.
    `undate`  — keep the tasks, drop the dates, so nothing is "late" any more.
    `archive` — move them out of the way; archived, never deleted.

    No mode destroys a task. Archiving sets `archived_at`, which is reversible
    in the database, and is why the confirmation can promise nothing is lost.
    """
    today = today_local(tz)
    overdue = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline < today)
        .order_by(Task.deadline, Task.id)).all()

    for index, task in enumerate(overdue):
        if mode == "archive":
            task.archived_at = utcnow()
        elif mode == "undate":
            task.deadline = None
        elif mode == "week":
            task.deadline = today + timedelta(days=index % 7)
        else:
            task.deadline = today
        task.reminder_sent_at = None
    s.commit()
    return len(overdue)


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
        # The review is offered from Friday onwards, and only once a week has
        # something in it to review.
        "is_week_end": today.weekday() >= 4,
        "answers": {
            "went_well": row.went_well if row else "",
            "blocked": row.blocked if row else "",
            "next_focus": row.next_focus if row else "",
        },
        "saved": row is not None,
    }


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


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

#: From this hour on, closing the day is the thing worth suggesting.
DAY_CLOSE_HOUR = 20


def now_next(s: Session, ws: int, user: User, *,
             tz: ZoneInfo | None = None) -> dict:
    """The one thing to do next, decided by a fixed ladder.

    Home's job is to answer "what now?" without the user having to read the
    whole screen and choose. The order is deliberate and completely
    deterministic — no model, no scoring, nothing that can produce a different
    answer for the same day:

      1. get up, while it still counts
      2. the top-three picks, in the order they were picked
      3. anything else due today
      4. a habit that is due and not done
      5. close the day, once the evening has started
      6. otherwise: today's important work is finished

    Nothing here invents work. If the day is empty, it says so.
    """
    tz = tz or user_tz(user)
    today = today_local(tz)
    now = now_local(tz)

    wake = wake_state(s, ws, tz=tz)
    if wake and not wake["logged"] and not wake["late"]:
        return {"kind": "wake", "title": "", "id": wake["habit_id"],
                "action": "wakeup", "meta": wake["target"]}

    for task in top3_tasks(s, ws, today, tz=tz):
        if task["status"] != "done":
            return {"kind": "task", "title": task["title"], "id": task["id"],
                    "action": "task", "meta": task["due_time"] or ""}

    for task in tasks_due_today(s, ws, tz=tz):
        if task["status"] != "done":
            return {"kind": "task", "title": task["title"], "id": task["id"],
                    "action": "task", "meta": task["due_time"] or ""}

    for habit in list_habits(s, ws, today, tz=tz):
        if habit["due"] and not habit["done"] and not habit["protected"]:
            return {"kind": "habit", "title": habit["name"], "id": habit["id"],
                    "action": "habit", "meta": habit["target_time"] or ""}

    prayer = prayer_state(s, ws, today, user.gender)
    if not prayer["complete"] and prayer["performed"] < PRAYER_REQUIRED \
            and now.hour >= 12:
        return {"kind": "prayer", "title": "", "id": None, "action": "prayer",
                "meta": f"{prayer['performed']}/{PRAYER_REQUIRED}"}

    if now.hour >= DAY_CLOSE_HOUR and not journal_done(s, ws, today):
        return {"kind": "journal", "title": "", "id": None, "action": "journal",
                "meta": ""}

    return {"kind": "clear", "title": "", "id": None, "action": "", "meta": ""}


def week_strip(s: Session, ws: int, *, tz: ZoneInfo | None = None) -> dict:
    """The current week as seven cells, with a marker where something lands.

    A full month grid on Home costs a third of the first screen to answer a
    question the user is not asking yet. The week is the useful horizon; the
    month is one tap away.
    """
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


def home(s: Session, ws: int, user: User) -> dict:
    """Everything Home shows, and nothing else.

    Home answers two questions — what should I do right now, and how is today
    going — so the payload carries exactly the fields on that screen. The month
    grid, the project rollups and the analytics history all live one tap away,
    because each one was something to read past before reaching the answer.
    """
    tz = user_tz(user)
    today = today_local(tz)
    done, total = habit_progress(s, ws, today)
    prayer = prayer_state(s, ws, today, user.gender)
    top3 = top3_tasks(s, ws, today, tz=tz)
    journal = get_journal(s, ws, today)

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
        "habits": {"done": done, "total": total},
        "prayer": {"score": prayer["score"], "max": PRAYER_MAX_SCORE,
                   "performed": prayer["performed"], "required": PRAYER_REQUIRED,
                   "complete": prayer["complete"], "excused": prayer["excused"]},
        "streak": habit_streak(s, ws, tz=tz),
        "overall": overall_state(s, ws, today),
        # The single dominant answer to "what now?".
        "now": now_next(s, ws, user, tz=tz),
        "wake": wake_state(s, ws, tz=tz),
        # One mission with its supporting priorities, and today's work.
        "focus": week_focus(s, ws, tz=tz),
        "mission": primary_focus(s, ws, tz=tz),
        "top3": top3,
        "top3_max": MAX_TOP3,
        "tasks_today": today_tasks_by_project(
            s, ws, tz=tz, skip_ids={t["id"] for t in top3}),
        "journal_today": bool(journal and journal["complete"]),
        "journal_answered": journal["answered"] if journal else 0,
        "journal_total": len(JOURNAL_KEYS),
        "birthdays": list_birthdays(s, ws, within_days=7, tz=tz),
        "week": week_strip(s, ws, tz=tz),
        "break": break_state(s, ws, user, tz=tz),
    }


# ---------------------------------------------------------------------------
# Data and privacy
# ---------------------------------------------------------------------------

def export_workspace(s: Session, ws: int, user: User) -> dict:
    """Everything this workspace contains, as plain JSON-ready data.

    The user wrote it, so they can have it back. No aggregate, no summary —
    the actual rows, in the form they were stored.
    """
    def habits():
        for h in s.scalars(select(Habit).where(Habit.workspace_id == ws)).all():
            yield {"name": h.name, "category": h.category,
                   "schedule": clean_schedule(h.schedule),
                   "target_time": h.target_time.strftime("%H:%M") if h.target_time else None,
                   "paused": h.paused_at is not None,
                   "archived": h.archived_at is not None,
                   "created": h.created_at.isoformat() if h.created_at else None}

    return {
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
            {"habit_id": r.habit_id, "day": r.day.isoformat(), "done": r.done}
            for r in s.scalars(select(HabitLog)
                               .where(HabitLog.workspace_id == ws)
                               .order_by(HabitLog.day)).all()],
        "prayers": [
            {"day": r.day.isoformat(), "prayer": r.prayer, "status": r.status}
            for r in s.scalars(select(PrayerLog)
                               .where(PrayerLog.workspace_id == ws)
                               .order_by(PrayerLog.day)).all()],
        "projects": [
            {"name": p.name, "description": p.description, "status": p.status,
             "deadline": p.deadline.isoformat() if p.deadline else None,
             "archived": p.archived_at is not None}
            for p in s.scalars(select(Project).where(Project.workspace_id == ws)).all()],
        "tasks": [
            {"title": t.title, "description": t.description, "status": t.status,
             "priority": t.priority, "project_id": t.project_id,
             "deadline": t.deadline.isoformat() if t.deadline else None,
             "due_time": t.due_time.strftime("%H:%M") if t.due_time else None,
             "recurrence": clean_recurrence(t.recurrence),
             "completed_at": t.completed_at.isoformat() if t.completed_at else None,
             "archived": t.archived_at is not None}
            for t in s.scalars(select(Task).where(Task.workspace_id == ws)).all()],
        "weekly_focus": [
            {"week_start": f.week_start.isoformat(), "slot": f.slot,
             "title": f.title, "priority": f.priority, "done": f.done}
            for f in s.scalars(select(WeeklyFocus)
                               .where(WeeklyFocus.workspace_id == ws)
                               .order_by(WeeklyFocus.week_start)).all()],
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
    }


#: Every table that holds workspace-scoped data. Deleting an account walks this
#: list, so adding a model without adding it here is the one way a deletion
#: could leave someone's rows behind — which is why the list is explicit rather
#: than left to the database.
WORKSPACE_TABLES = [HabitLog, Habit, PrayerLog, PrayerDay, Task, Project,
                    WeeklyFocus, WeeklyReview, JournalEntry, Birthday,
                    Feedback, DailyReportLog]


def delete_account(s: Session, telegram_id: int) -> bool:
    """Erase a user and everything in their workspace, for real.

    Only ever called from an explicitly confirmed action. The rows are removed
    table by table rather than trusting `ON DELETE CASCADE`: SQLite enforces
    foreign keys only when the connection asks it to, and a delete that silently
    leaves a workspace full of journal entries behind would be the worst
    possible thing to be wrong about.
    """
    from sqlalchemy import delete as sql_delete

    user = s.get(User, telegram_id)
    if user is None:
        return False

    ws = s.scalar(select(Workspace.id).where(Workspace.user_id == telegram_id))
    if ws is not None:
        for model in WORKSPACE_TABLES:
            s.execute(sql_delete(model).where(model.workspace_id == ws))
        s.execute(sql_delete(Workspace).where(Workspace.id == ws))
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
# Daily reports
# ---------------------------------------------------------------------------

def already_sent(s: Session, ws: int, report_type: str, report_date: date) -> bool:
    """True when this report was already claimed by someone."""
    return s.scalar(select(DailyReportLog.id).where(
        DailyReportLog.workspace_id == ws,
        DailyReportLog.report_type == report_type,
        DailyReportLog.report_date == report_date)) is not None


def claim_report(s: Session, ws: int, report_type: str,
                 report_date: date) -> int | None:
    """Try to own this report. Returns the outbox id, or None if someone else won.

    The INSERT is the lock: the unique constraint means exactly one worker can
    succeed, so two schedulers cannot both send (audit 036).
    """
    row = DailyReportLog(workspace_id=ws, report_type=report_type,
                         report_date=report_date, status="claimed")
    s.add(row)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        return None
    return row.id


def mark_report_sent(s: Session, report_id: int) -> None:
    row = s.get(DailyReportLog, report_id)
    if row is not None:
        row.status = "sent"
        row.sent_at = utcnow()
        row.attempts += 1
        s.commit()


def mark_report_failed(s: Session, report_id: int, error: str) -> None:
    """Record the failure and release the claim so a retry can pick it up."""
    row = s.get(DailyReportLog, report_id)
    if row is not None:
        row.status = "failed"
        row.attempts += 1
        row.last_error = str(error)[:200]
        s.commit()


def release_report(s: Session, report_id: int) -> None:
    """Drop a claim entirely, so the next run may try again from scratch."""
    row = s.get(DailyReportLog, report_id)
    if row is not None:
        s.delete(row)
        s.commit()


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

    # Yesterday as one comparable number, from the same function every other
    # surface uses, plus the components behind it.
    y_components = overall_components(s, ws, yesterday)
    y_available = [v for v in y_components.values() if v is not None]
    y_overall = round(sum(y_available) / len(y_available)) if y_available else 0

    return {
        "yesterday": {
            "date": yesterday.isoformat(),
            "overall": y_overall,
            "measured": bool(y_available),
            "components": y_components,
            "habits_done": y_done, "habits_total": y_total,
            "prayer_score": y_prayer["score"],
            "prayer_performed": y_prayer["performed"],
            "prayer_required": PRAYER_REQUIRED,
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
            "habits_total": y_total,
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
        # The same number Home and Statistics show, from the same function.
        "overall": overall_state(s, ws, today),
        "habits_done": done, "habits_total": total,
        # Only what was actually due today: a Monday/Wednesday habit is not
        # "still unfinished" on a Tuesday evening.
        "habits_remaining": [h["name"] for h in habits
                             if h["due"] and not h["done"]],
        "prayer_score": prayer["score"],
        "prayer_performed": prayer["performed"],
        "prayer_required": PRAYER_REQUIRED,
        "tasks_completed": completed,
        "tasks_remaining": [t.title for t in remaining],
        "tasks_overdue": [t.title for t in overdue],
        "focus": focus,
        "focus_done": sum(1 for f in focus if f["done"]),
        # Complete, not merely started: the `Kundalik` habit uses the same
        # rule, and a report that says "written" beside an unticked habit is
        # the app disagreeing with itself.
        "journal": journal_done(s, ws, today, tz=tz),
    }


def active_recipients(s: Session) -> list[tuple[int, int, str]]:
    """(telegram_id, workspace_id, language) for every user who should get reports."""
    rows = s.execute(
        select(User.telegram_id, Workspace.id, User.language)
        .join(Workspace, Workspace.user_id == User.telegram_id)
        .where(User.onboarded.is_(True), User.is_subscribed.is_(True))
    ).all()
    return [(r[0], r[1], r[2]) for r in rows]


# ---------------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------------
#
# Every preference column is nullable, because they were added to live tables.
# NULL means "never chosen", and these are what it means instead. Reading them
# through one function is what stops "the default" from being three different
# things in three files.

DEFAULT_MORNING_TIME = dtime(4, 0)
#: 21:30 rather than 21:00: the day's last habits and prayers are usually still
#: being entered on the hour, and a summary that arrives mid-entry is wrong.
DEFAULT_EVENING_TIME = dtime(21, 30)

#: How long after its configured time a report may still go out. Past this the
#: day has moved on, and a morning summary at noon is noise rather than a
#: report — a user who onboards at 15:00 must not be sent one immediately.
REPORT_WINDOW = timedelta(minutes=90)
#: The same idea for task reminders: a phone that was off does not get an alert
#: about a meeting that started two hours ago. A task reminder is marked sent
#: the moment it goes out, so a generous window cannot produce a duplicate.
REMINDER_WINDOW = timedelta(minutes=30)

#: How often the reminder job runs. The scheduler reads this, so the interval
#: and the windows below cannot drift apart.
REMINDER_JOB_MINUTES = 5

#: Habit reminders have nothing to mark — a habit has one row per day and it
#: means "done", not "reminded" — so their window is exactly one job interval.
#: Any wider and every pass inside the window would send the nudge again.
HABIT_REMINDER_WINDOW = timedelta(minutes=REMINDER_JOB_MINUTES)


def prefs_for(user: User) -> dict:
    """The user's notification settings, with every NULL resolved."""
    return {
        "timezone": user.timezone or str(TZ),
        "morning_report": True if user.morning_report is None else bool(user.morning_report),
        "morning_time": (user.morning_time or DEFAULT_MORNING_TIME).strftime("%H:%M"),
        "evening_report": True if user.evening_report is None else bool(user.evening_report),
        "evening_time": (user.evening_time or DEFAULT_EVENING_TIME).strftime("%H:%M"),
        "task_reminders": True if user.task_reminders is None else bool(user.task_reminders),
        # Off by default: a habit reminder every day is the fastest way to teach
        # someone to ignore the app's notifications.
        "habit_reminders": False if user.habit_reminders is None else bool(user.habit_reminders),
    }


def save_prefs(s: Session, user: User, **fields) -> dict:
    """Write notification settings. Unknown or malformed values are ignored."""
    if "timezone" in fields and fields["timezone"]:
        name = str(fields["timezone"])[:40]
        # Only store a zone the platform can actually resolve.
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
    s.commit()
    return prefs_for(user)


def report_is_due(user: User, report_type: str, now: datetime) -> bool:
    """Whether this user's report should go out at this local moment.

    A window rather than an exact match, so a scheduler that runs every few
    minutes — or recovers from a restart — still delivers exactly once. The
    once-per-day guarantee itself comes from `claim_report`, not from here.
    """
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
    return scheduled <= now <= scheduled + REPORT_WINDOW


def due_task_reminders(s: Session, ws: int, user: User,
                       now: datetime | None = None) -> list[dict]:
    """Tasks whose reminder is due now and has not been sent.

    A reminder for a task that is already done is never returned: the point of
    the reminder has passed, and sending it anyway is what teaches people to
    mute the bot.
    """
    if not prefs_for(user)["task_reminders"]:
        return []

    tz = user_tz(user)
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

    out = []
    for habit in list_habits(s, ws, today, tz=tz):
        if not habit["due"] or habit["done"] or not habit["remind_at"]:
            continue
        hour, minute = (int(x) for x in habit["remind_at"].split(":"))
        fire = datetime.combine(today, dtime(hour, minute))
        if fire <= now < fire + HABIT_REMINDER_WINDOW:
            out.append(habit)
    return out


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
        # "Today" is the operator's day, in the platform's own zone, and the
        # timestamps are UTC — so the window is converted rather than compared.
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
        "languages": languages,
        "genders": genders,
    }


# ---------------------------------------------------------------------------
# Scheduler coordination
# ---------------------------------------------------------------------------

#: Stable per-job key for pg_try_advisory_lock. Any two instances computing it
#: from the same job name land on the same number.
def _lock_key(name: str) -> int:
    import zlib
    return zlib.crc32(name.encode()) - 2**31


class JobLock:
    """Hold a PostgreSQL advisory lock for the duration of one job run.

    Two instances of the app would otherwise both fire the 04:00 job. The
    loser exits quietly instead of sending a second copy (audit 032).
    On SQLite there is nothing to coordinate, so the lock is always granted.
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
            return self
        self.acquired = bool(self._session.scalar(
            sql_text("SELECT pg_try_advisory_lock(:k)"), {"k": _lock_key(self._name)}))
        if not self.acquired:
            log.info("job %s already running elsewhere — skipping", self._name)
        return self

    def __exit__(self, *exc) -> None:
        if self._session is None:
            return
        try:
            if self.acquired and self._session.bind.dialect.name == "postgresql":
                self._session.execute(
                    sql_text("SELECT pg_advisory_unlock(:k)"),
                    {"k": _lock_key(self._name)})
                self._session.commit()
        finally:
            self._session.close()
