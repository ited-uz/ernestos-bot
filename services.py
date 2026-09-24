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
import re
import secrets
from datetime import date, datetime, time as dtime, timedelta, timezone as _utc
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db import (
    Birthday, DailyReportLog, DailyScore, Feedback, Habit, HabitLog, JobRun,
    JournalEntry, PrayerDay, PrayerLog, Project, Referral, ReferralCode,
    Task, Team, TeamHabit, TeamHabitLog, TeamMember, TeamTask, TeamTaskDone,
    User, UserAchievement, UserProgress, WeeklyFocus, WeeklyReview,
    Workspace, XPEvent, utcnow,
)

log = logging.getLogger("ernestos")

#: The default zone, used by every workspace that never chose one.
TZ = ZoneInfo("Asia/Tashkent")

#: Offered in Settings. A full IANA list is 600 entries the user has to scroll;
#: these are the zones ErnestOS users actually live in, and any other valid
#: IANA name still works if it is already stored.
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

    The short list was a guess about where users live, and it was wrong for
    anybody outside it: their only options were somebody else's city or a UTC
    offset they would have to work out twice a year. Reports fire on this
    clock, so being unable to name your own zone means being sent a morning
    report in the middle of the night.

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

#: What each tier is worth inside the habit percentage. The three tiers existed
#: as *labels* long before they existed as arithmetic: a workspace could sort
#: its habits into non-negotiable, target and bonus, and then every one of them
#: counted for exactly the same amount, so a missed 5x namoz and a missed
#: podcast cost the day the same number of points. That made the sorting
#: decorative, and worse, it made the number dishonest — the screen said one of
#: these matters more and the score disagreed.
#:
#: Fifty / thirty / twenty. Non-negotiable is worth more than the other two put
#: together, which is what "non-negotiable" has to mean if the word is doing any
#: work; bonus is worth having and never worth much.
HABIT_TIER_WEIGHTS = {"non_negotiable": 50, "target": 30, "bonus": 20}

#: (name, category, system_key). A system_key marks a derived habit the user
#: cannot tick by hand: "wakeup" follows the morning check-in, "prayer" follows
#: the daily prayer score and "journal" follows a fully answered entry.
#:
#: The starting set is exactly these three, and the reason is that a new account
#: should open on habits it cannot argue with. Getting up, praying and writing
#: the day up are the three ErnestOS is actually about; everything else — deep
#: work, sport, reading, podcasts — is a choice about how somebody wants to live,
#: and seeding four of those meant handing every new user a list they never
#: picked and would have to prune before the screen told them anything true.
#:
#: All three are derived, so a new workspace has nothing to tick by hand until
#: the user adds their own. That is deliberate: the first habit in the list is
#: theirs, not ours.
#:
#: This tuple is the only place defaults are defined. `seed_default_habits` is
#: the only reader, and it runs on account creation and on an explicit wipe —
#: never against an existing workspace, so shortening this list can never remove
#: a habit somebody already has.
DEFAULT_HABITS = [
    ("Get up",   "non_negotiable", "wakeup"),
    ("5x namoz", "non_negotiable", "prayer"),
    ("Kundalik", "non_negotiable", "journal"),
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


#: How many times to re-read the maximum member number before giving up. Each
#: retry costs one query and only happens when two accounts are created in the
#: same instant, so a handful is plenty.
MEMBER_NO_ATTEMPTS = 5


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
    #
    # Two people tapping /start in the same second both read the same max and
    # both write it, and the number is not an internal detail — it is shown to
    # the user as "you are member #42" and logged to the admin channel. The
    # column is unique, so the loser of the race now fails loudly on flush and
    # simply reads the new maximum and tries again, inside a SAVEPOINT so the
    # retry cannot damage whatever transaction the caller is running.
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
    s.commit()
    return user, True


def seed_default_habits(s: Session, ws: int) -> None:
    """Put the starting set of habits into an empty workspace.

    Used when an account is created and again when somebody wipes their data:
    a workspace with no habits at all is not a clean slate, it is a dead one.
    The caller commits.
    """
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

    "Done" means the day changed: a task ticked or added, a habit logged, a
    prayer recorded, a journal written. Opening a screen is not an action —
    counting reads would let somebody exhaust their free run by scrolling, and
    the count exists to measure whether the product has been *used*.

    The caller commits. Every increment goes through here so the definition of
    an action lives in one place rather than in each endpoint.
    """
    user = s.get(User, telegram_id)
    if user is None:
        return 0
    user.actions_count = (user.actions_count or 0) + 1
    return user.actions_count


def record_action_and_qualify(s: Session, telegram_id: int) -> tuple[int, int | None]:
    """`record_action`, then the referral check. Returns (total, inviter_to_tell).

    A separate function rather than a change to `record_action`, because that
    one has callers and a return type they rely on, and "count an action" and
    "maybe promote a referral" are not the same sentence.

    This is the *only* place the qualification check hangs off ordinary usage.
    Every write in the product already funnels through the action counter — the
    bot's `count_action` and the API middleware — so hooking it here reaches all
    of them without putting referral logic into forty endpoints, and without a
    scheduler sweeping every user to ask who has grown up yet.
    """
    total = record_action(s, telegram_id)
    s.commit()
    return total, maybe_qualify_referral(s, telegram_id)


def record_action_and_progress(s: Session, telegram_id: int) -> dict:
    """The action counter, the referral check and personal progression, once.

    One funnel, three consequences. `record_action_and_qualify` already had two
    of them; progression is the third, and it hangs off the same single point
    for the same reason — every write in the product passes through the action
    counter, so scoring the day here reaches all of them without putting XP
    logic into forty endpoints and without a nightly job that visits every user
    to ask how they did.

    Progression failing must never fail the user's actual action. Ticking a
    task is the thing they asked for; recomputing their level is bookkeeping
    that happens to be attached to it, and if the bookkeeping raises, the tick
    still stands. So it is caught, logged and dropped.

    Returns the progression result — including whether a level was just
    crossed — for callers that want to say something about it. Callers that do
    not care can ignore it, which is why the referral pair is still its own
    function rather than being folded in here.
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
                       tz: ZoneInfo | None = None,
                       include_team: bool = True) -> dict:
    """Habits grouped into the three tiers, preserving display order.

    Shared habits are in these groups, not in a block of their own. They are
    scored in the same arithmetic and owed on the same day, so putting them
    somewhere else would be the screen disagreeing with the number underneath
    it — and "which of these actually counts?" is the question that makes
    somebody stop trusting both. Each row says where it came from instead.
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
    recreate, which throws away every log it had. So an ordinary habit's name,
    tier, schedule and reminder are all the user's to set.

    The three derived habits are the exception, and on two counts. Their names
    are the contract with the module that drives them, and their *schedule* is
    the contract with the score: Get up, 5x namoz and Kundalik are the daily
    floor every other number on Home is measured against, so a schedule of
    "Mondays only" would not mean "I do this on Mondays", it would mean the
    other six days stop counting and the day's percentage silently rises. The
    Mini App no longer offers the picker for them; this is the half that a
    hand-written request cannot get around.
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
    if ("schedule" in fields and fields["schedule"] is not None
            and not habit.is_protected):
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


def workspace_owner(s: Session, ws: int) -> int | None:
    """Whose workspace this is. The bridge between workspace-scoped scoring
    and team membership, which is keyed on the person rather than the box."""
    return s.scalar(select(Workspace.user_id).where(Workspace.id == ws))


def due_team_habits(s: Session, ws: int, day: date) -> list[tuple]:
    """(habit, done) for every team habit this workspace's owner owes today.

    Shared work counts in the personal day. That is a product decision, and a
    deliberate reversal of an earlier one: the two numbers were kept apart so
    a quiet team evening could not drag down a day you had personally
    finished. In practice that split the thing it was meant to protect — a
    couple doing the programme together were reading two percentages and
    trusting neither. One number, and the shared half of the day is simply
    part of the day.
    """
    owner = workspace_owner(s, ws)
    if owner is None:
        return []
    rows: list[tuple] = []
    for team in teams_for(s, owner):
        habits = [h for h in s.scalars(select(TeamHabit).where(
            TeamHabit.team_id == team.id,
            TeamHabit.archived_at.is_(None))).all()
            if team_habit_is_due(h, day)]
        if not habits:
            continue
        done_ids = set(s.scalars(select(TeamHabitLog.habit_id).where(
            TeamHabitLog.user_id == owner, TeamHabitLog.day == day,
            TeamHabitLog.done.is_(True),
            TeamHabitLog.habit_id.in_([h.id for h in habits]))).all())
        for habit in habits:
            rows.append((habit, habit.id in done_ids))
    return rows


def due_team_tasks(s: Session, ws: int, day: date) -> list[tuple]:
    """(priority, done) for every team task of this owner's due on `day`."""
    owner = workspace_owner(s, ws)
    if owner is None:
        return []
    rows: list[tuple] = []
    for team in teams_for(s, owner):
        tasks = s.scalars(select(TeamTask).where(
            TeamTask.team_id == team.id, TeamTask.archived_at.is_(None),
            TeamTask.deadline == day)).all()
        if not tasks:
            continue
        done_ids = set(s.scalars(select(TeamTaskDone.task_id).where(
            TeamTaskDone.user_id == owner, TeamTaskDone.done.is_(True),
            TeamTaskDone.task_id.in_([t.id for t in tasks]))).all())
        for task in tasks:
            rows.append((task.priority, task.id in done_ids))
    return rows


def habit_progress(s: Session, ws: int, day: date, *,
                   include_team: bool = True) -> tuple[int, int]:
    """(completed, total) habits that were actually expected on that day.

    A habit scheduled for Monday/Wednesday/Friday is not counted on a Tuesday,
    and a paused habit is not counted at all. Counting them would mean the
    user's Tuesday score drops for a gym session they never planned — the exact
    kind of false failure that makes people close the app.
    """
    habits = [h for h in _active_habits(s, ws) if habit_is_due(h, day)]
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

    Only tiers that actually have a habit due that day get a weight. This is
    the part that keeps the number reachable: the default workspace holds three
    non-negotiable habits and nothing else, and a fixed 50/30/20 split would
    cap that user at 50% on a day they did everything they had. Renormalising
    over the tiers in play means "I did all of it" is always 100%, whether "all
    of it" is three habits or eleven.

    `applied` is the weight after that renormalisation — what the tier is
    really worth to *this* user today — and it is what the Mini App shows, so
    the badge on the screen and the arithmetic behind it cannot drift apart.
    """
    habits = [h for h in _active_habits(s, ws) if habit_is_due(h, day)]
    done_ids = set(s.scalars(select(HabitLog.habit_id).where(
        HabitLog.workspace_id == ws, HabitLog.day == day,
        HabitLog.done.is_(True))).all()) if habits else set()
    # Shared habits sit in the same tiers as personal ones, so a day made of
    # both is scored by one rule rather than two stitched together.
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
    """The day's habit score, 0–100, with the three tiers weighted.

    Replaces a flat done/total. Under the old arithmetic a user with three
    non-negotiable habits and seven bonus ones could skip every mandatory one,
    tick the seven optional ones and read 70% — which is not a description of
    that day. Now the mandatory half of the score is missing and it reads 30%.
    """
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
    #
    # Its own window, independent of the grid's. The streak used to walk the
    # same `done_days` the grid was built from, so it could never report more
    # than `days` — a 90-day run of a habit shown on the default 30-day grid
    # read as 30, and the number went *down* when the user narrowed the view.
    streak_start = today - timedelta(days=STREAK_HORIZON)
    streak_done = set(s.scalars(select(HabitLog.day).where(
        HabitLog.workspace_id == ws, HabitLog.habit_id == habit_id,
        HabitLog.done.is_(True), HabitLog.day >= streak_start)).all())

    streak, cursor, guard = 0, today, 0
    if habit_is_due(habit, today) and today not in streak_done:
        cursor = today - timedelta(days=1)
    while guard < STREAK_HORIZON:
        guard += 1
        if habit_is_due(habit, cursor):
            if cursor not in streak_done:
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
    # `team_id IS NULL` is what keeps a shared project out of a private list:
    # the row still carries the creator's workspace, because that column is
    # not nullable, but it belongs to the team.
    stmt = select(Project).where(Project.workspace_id == ws,
                                 Project.team_id.is_(None))
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
REMINDER_OFFSETS = [0, 10, 30, 60, 1440]
#: What a task is given when nobody chose. Half an hour is the offset that is
#: actually useful: on time is a notification about something already late,
#: and an hour is early enough to be read and forgotten. The picker still
#: offers every other value, including none at all.
DEFAULT_REMIND_BEFORE = 30
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


def next_occurrence(recurrence: str | None, after: date, *,
                    anchor_day: int | None = None) -> date | None:
    """The next date a recurring task is due, strictly after `after`.

    `anchor_day` is the day of the month the user originally picked, and it
    only matters for the monthly rule. Without it the clamp below is lossy:
    the 31st becomes the 28th in February, and because the next month is then
    computed from *that*, the task stays on the 28th for ever. Passing the
    anchor lets each month clamp from the original choice instead of from the
    previous clamp, so a 31st task reads 31 / 28 / 31 / 30 as a person expects.
    """
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
    # Remember the day the user picked the first time a monthly task recurs,
    # so later clamps measure from their choice rather than from each other.
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

    existing = s.scalar(select(Task.id).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.title == task.title,
        Task.recurrence == rule, Task.deadline == nxt))
    if existing is not None:
        return None

    clone = Task(workspace_id=ws, title=task.title, description=task.description,
                 project_id=task.project_id, deadline=nxt, due_time=task.due_time,
                 remind_before=task.remind_before, recurrence=rule,
                 anchor_day=anchor, priority=task.priority)
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
    """The habit component of the overall score — tier-weighted since the three
    tiers became arithmetic rather than labels. `habit_progress` still returns
    the raw counts, which is what "4/6 done" on screen means and what the
    streak asks for; this is the *scored* value, and they are different
    questions."""
    return habit_percent(s, ws, day)


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


#: What each part of a day is worth.
#:
#: A flat average said a five-minute habit and the day's real work were the
#: same size, which is not what anybody means by "how did today go". Tasks lead
#: because they are the thing a person actually chose to do today; the week's
#: focus is next-heaviest per unit because it is the one goal that survives the
#: day; habits are the base rhythm; prayer is a fixed personal routine that is
#: either kept or not.
#:
#: The weights only ever apply to the parts a user *has*. A user who never
#: opens the prayer module is not carrying a permanent 15% hole — see
#: `weighted_overall`, which renormalises over whatever is present.
OVERALL_WEIGHTS = {"tasks": 0.40, "habits": 0.25, "focus": 0.20, "prayer": 0.15}

#: What a task is worth inside the tasks component, by its own priority.
#:
#: Three high-priority tasks and one trivial one is not a four-item day where
#: every item is a quarter. Marking something "high" is the user telling the
#: system what today is really about, and the score has to agree with them —
#: otherwise the cheapest way to a good percentage is to do the easy ones.
TASK_PRIORITY_WEIGHTS = {"high": 3, "medium": 2, "low": 1}


def today_task_progress(s: Session, ws: int, day: date | None = None, *,
                        tz: ZoneInfo | None = None) -> tuple[int, int]:
    """(completed, total) tasks that belong to this day.

    Only tasks actually scheduled for the day count. Folding in the whole
    backlog would mean a user with 200 open tasks can never move the number,
    and finishing today's work would not show up at all.

    Plain counts, for the places that print "3 / 5". The score itself uses
    `today_task_score`, which weighs each task by its priority.
    """
    day = day or today_local(tz)
    total = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.deadline == day)) or 0
    done = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.deadline == day, Task.status == "done")) or 0
    shared = due_team_tasks(s, ws, day)
    return done + sum(1 for _, ok in shared if ok), total + len(shared)


def today_task_score(s: Session, ws: int, day: date | None = None, *,
                     tz: ZoneInfo | None = None,
                     include_team: bool = True) -> tuple[int, int]:
    """(earned, available) task points for the day, weighted by priority."""
    day = day or today_local(tz)
    rows = [(priority, status == "done") for priority, status in s.execute(
        select(Task.priority, Task.status).where(
            Task.workspace_id == ws, Task.archived_at.is_(None),
            Task.deadline == day)).all()]
    # Shared tasks are weighed by the same priorities, in the same total.
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
    """(done, total) of this week's missions.

    The week's focus is a weekly commitment read on a daily screen, which is
    the point of it: it is the part of the score that does not reset overnight.
    """
    rows = list_focus(s, ws, day, tz=tz)
    return sum(1 for r in rows if r["done"]), len(rows)


def overall_components(s: Session, ws: int, day: date | None = None, *,
                       tz: ZoneInfo | None = None,
                       include_team: bool = True) -> dict:
    """Each component's percentage, or None when it has no denominator today.

    A category with nothing in it is *absent*, not zero. Counting an empty
    category as 0% would punish a user for a day with no tasks, which is the
    opposite of what the number is for — and it is also what makes the weights
    safe to state as fixed numbers, because an unused category is removed from
    the calculation rather than scored at nought.
    """
    day = day or today_local(tz)

    habits_done, habits_total = habit_progress(s, ws, day,
                                               include_team=include_team)
    tasks_earned, tasks_available = today_task_score(
        s, ws, day, tz=tz, include_team=include_team)
    # Weighted across the three tiers, not a flat count of ticks. The counts
    # above are still what "4/6" on screen means; this is what the score is
    # built from, and they are different questions on purpose.
    habits_scored = habit_percent(s, ws, day, include_team=include_team)
    focus_done, focus_total = focus_progress(s, ws, day, tz=tz)
    prayer_row = s.scalar(select(PrayerDay).where(
        PrayerDay.workspace_id == ws, PrayerDay.day == day))
    prayer_habit = s.scalar(select(Habit.id).where(
        Habit.workspace_id == ws, Habit.system_key == SYSTEM_PRAYER,
        Habit.archived_at.is_(None)))

    return {
        "tasks": (round(tasks_earned / tasks_available * 100)
                  if tasks_available else None),
        "habits": habits_scored if habits_total else None,
        "focus": round(focus_done / focus_total * 100) if focus_total else None,
        # Prayer's denominator is the five daily prayers, which exist for as
        # long as the user keeps the habit — not only on days they logged one.
        "prayer": (round(float(prayer_row.score if prayer_row else 0.0)
                         / PRAYER_MAX_SCORE * 100)
                   if prayer_habit is not None else None),
    }


def weighted_overall(components: dict) -> int:
    """One number from the parts, each carrying its own weight.

    Absent parts are dropped and the remaining weights renormalised, which is
    what redistributes a missing category across the others in proportion
    rather than in equal shares — somebody who does not use the prayer module
    gets that 15% split 40:25:20, not 5 points each. A user with only tasks
    scores exactly their task percentage, which is the only defensible answer.
    """
    present = {k: v for k, v in components.items()
               if v is not None and k in OVERALL_WEIGHTS}
    if not present:
        return EMPTY_OVERALL
    total_weight = sum(OVERALL_WEIGHTS[k] for k in present)
    if total_weight <= 0:
        return EMPTY_OVERALL
    return round(sum(value * OVERALL_WEIGHTS[key]
                     for key, value in present.items()) / total_weight)


def overall_percent(s: Session, ws: int, day: date | None = None) -> int:
    """The weighted score for a day — private and shared, averaged."""
    return day_score(s, ws, day)["value"]


def overall_state(s: Session, ws: int, day: date | None = None) -> dict:
    """Today's overall number plus how it compares with yesterday.

    The two days are only comparable when both had something to measure;
    otherwise the trend is `flat` rather than an invented drop.
    """
    day = day or today_local()
    yesterday = day - timedelta(days=1)

    today_components = overall_components(s, ws, day)
    today_available = [v for v in today_components.values() if v is not None]
    y_components = overall_components(s, ws, yesterday)
    y_available = [v for v in y_components.values() if v is not None]

    value = weighted_overall(today_components)
    previous = weighted_overall(y_components) if y_available else None

    if previous is None or not today_available or value == previous:
        trend = "flat"
    else:
        trend = "up" if value > previous else "down"

    # The split the screens draw: private, shared, and the average of the two.
    split = day_score(s, ws, day, tz=None)
    return {"value": split["value"], "trend": trend, "yesterday": previous,
            "personal": split["personal"], "team": split["team"],
            "band": split["band"],
            "components": today_components}


def _task_percent(s: Session, ws: int, day: date) -> int:
    """The priority-weighted task percentage, or 0 on a day with no tasks.

    The chart needs a number for every day, so an absent component is drawn as
    zero here. The *score* never does that — see `overall_components`, which
    returns None and drops the category from the weighting entirely.
    """
    earned, available = today_task_score(s, ws, day)
    return round(earned / available * 100) if available else 0


def _focus_percent(s: Session, ws: int, day: date) -> int:
    done, total = focus_progress(s, ws, day)
    return round(done / total * 100) if total else 0


def _overall_percent_for(s: Session, ws: int, day: date) -> int:
    return weighted_overall(overall_components(s, ws, day))


#: The four series every chart and average is built from, plus the headline.
SERIES_KEYS = ("habits", "prayer", "tasks", "focus", "overall")


def _day_point(s: Session, ws: int, day: date) -> dict:
    return {
        "habits": _habit_percent(s, ws, day),
        "prayer": _prayer_percent(s, ws, day),
        "tasks": _task_percent(s, ws, day),
        "focus": _focus_percent(s, ws, day),
        "overall": _overall_percent_for(s, ws, day),
    }


def _range_average(s: Session, ws: int, start: date, end: date) -> dict:
    """Average of each series across an inclusive day range."""
    days = (end - start).days + 1
    if days <= 0:
        return {k: 0 for k in SERIES_KEYS}
    totals = {k: 0 for k in SERIES_KEYS}
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
OVERALL_COMPONENTS = ["tasks", "habits", "focus", "prayer"]


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
    focus_done, focus_total = focus_progress(s, ws, day)
    prayer = prayer_state(s, ws, day, user.gender)
    components = overall_components(s, ws, day)
    counted = [k for k in OVERALL_COMPONENTS if components.get(k) is not None]
    # The weight each counted part actually carried today, after the absent
    # ones were dropped and the rest renormalised. Printing the nominal 40/25/
    # 20/15 to somebody who has no prayer module would be a lie about their
    # own number.
    live = sum(OVERALL_WEIGHTS[k] for k in counted) or 1

    return {
        "day": day.isoformat(),
        "value": overall_percent(s, ws, day),
        "counted": counted,
        "parts": [
            {"key": "tasks", "percent": components["tasks"],
             "done": tasks_done, "total": tasks_total},
            {"key": "habits", "percent": components["habits"],
             "done": habits_done, "total": habits_total},
            {"key": "focus", "percent": components["focus"],
             "done": focus_done, "total": focus_total},
            {"key": "prayer", "percent": components["prayer"],
             "done": prayer["score"], "total": PRAYER_MAX_SCORE},
        ],
        "weights": {k: round(OVERALL_WEIGHTS[k] / live * 100)
                    for k in counted},
        "nominal_weights": {k: round(v * 100)
                            for k, v in OVERALL_WEIGHTS.items()},
        "task_priority_weights": dict(TASK_PRIORITY_WEIGHTS),
        # Spelled out so the UI never has to reimplement the rule.
        "rule": "weighted_mean_of_available",
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
    keys = SERIES_KEYS

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
            # Yesterday's own number, so the screen can say how far today has
            # moved rather than only which way. "Up" is a direction; "+12%
            # against yesterday" is the fact the direction was standing in for.
            "yesterday": overall["yesterday"],
            "tasks": components["tasks"],
            "habits": components["habits"],
            "focus": components["focus"],
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
    zone = tz or user_tz(user)
    today = today_local(zone)
    # `last_active_at` is a UTC instant and `today` is a local calendar date,
    # so `.date()` on the raw column compares two different kinds of time. In
    # Tashkent anything after 19:00 local still carries yesterday's UTC date,
    # which made a user who was active last night read as a day away and
    # triggered the "welcome back" prompt on somebody who never left.
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


def _now_task(task: dict, reason: str) -> dict:
    """One open task, in the shape the "now" card reads."""
    return {"kind": "task", "title": task["title"], "id": task["id"],
            "action": "task", "meta": task["due_time"] or "",
            "reason": reason, "priority": task["priority"],
            "due_time": task["due_time"], "deadline": task["deadline"],
            "project": task["project"]}


def now_next(s: Session, ws: int, user: User, *,
             tz: ZoneInfo | None = None) -> dict:
    """The one thing to do next, decided by a fixed ladder.

    Home's job is to answer "what now?" without the user having to read the
    whole screen and choose. The order is deliberate and completely
    deterministic — no model, no scoring, nothing that can produce a different
    answer for the same day:

      1. get up, while it still counts
      2. whatever the user pinned as the day's mission
      3. anything already late — a missed deadline outranks a future one
      4. anything else due today, earliest time first, then by priority
      5. a habit that is due and not done
      6. today's prayers, once the day is past noon
      7. close the day, once the evening has started
      8. otherwise: today's important work is finished

    Every answer carries a `reason`, because a card that decides on the user's
    behalf owes them the sentence explaining why this one and not another. It
    is also why the pinned mission sits at the top of the ladder: the user can
    always overrule the order by pinning something, and then the card says so.

    Nothing here invents work. If the day is empty, it says so.
    """
    tz = tz or user_tz(user)
    today = today_local(tz)
    now = now_local(tz)

    wake = wake_state(s, ws, tz=tz)
    if wake and not wake["logged"] and not wake["late"]:
        return {"kind": "wake", "title": "", "id": wake["habit_id"],
                "action": "wakeup", "meta": wake["target"], "reason": "wake"}

    for task in top3_tasks(s, ws, today, tz=tz):
        if task["status"] != "done":
            return _now_task(task, "pinned")

    # Late work first. `list_tasks` already sorts by deadline, then time of
    # day, then priority, so the oldest genuinely urgent thing surfaces rather
    # than whichever row happened to be created first.
    for task in list_tasks(s, ws, horizon_days=0, tz=tz)["overdue"]:
        if task["status"] != "done":
            return _now_task(task, "overdue")

    for task in tasks_due_today(s, ws, tz=tz):
        if task["status"] != "done":
            return _now_task(task, "due_today")

    for habit in list_habits(s, ws, today, tz=tz):
        if habit["due"] and not habit["done"] and not habit["protected"]:
            return {"kind": "habit", "title": habit["name"], "id": habit["id"],
                    "action": "habit", "meta": habit["target_time"] or "",
                    "reason": "habit"}

    prayer = prayer_state(s, ws, today, user.gender)
    if not prayer["complete"] and prayer["performed"] < PRAYER_REQUIRED \
            and now.hour >= 12:
        return {"kind": "prayer", "title": "", "id": None, "action": "prayer",
                "meta": f"{prayer['performed']}/{PRAYER_REQUIRED}",
                "reason": "prayer"}

    if now.hour >= DAY_CLOSE_HOUR and not journal_done(s, ws, today):
        return {"kind": "journal", "title": "", "id": None, "action": "journal",
                "meta": "", "reason": "evening"}

    return {"kind": "clear", "title": "", "id": None, "action": "", "meta": "",
            "reason": "clear"}


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
# External calendars
# ---------------------------------------------------------------------------

#: Providers the sync layer is being built for. Listed rather than hard-coded
#: at the call site so the UI can ask what exists without guessing.
CALENDAR_PROVIDERS = ("google", "icloud", "caldav")


def sync_calendar(s: Session, ws: int, provider: str,
                  credentials: dict | None = None) -> dict:
    """Two-way sync with an external calendar. Not implemented yet.

    Deliberately a stub with a real signature rather than nothing at all: the
    shape of this call is the decision that matters, and it is worth fixing
    before the first provider is written. What it will do, when it does it:

      * read events in the window ErnestOS already draws (this month forward);
      * map each to a task with a deadline and a due_time, keyed by the
        provider's own event id so a second sync updates rather than duplicates;
      * never write back a task the user did not explicitly share, because a
        personal task list appearing in somebody's work calendar is a privacy
        incident, not a feature.

    Returns the same shape it will return when it works, so a caller written
    against it today keeps working.
    """
    if provider not in CALENDAR_PROVIDERS:
        raise ValueError("unknown provider")
    log.info("calendar sync requested for workspace %s (%s) — not implemented",
             ws, provider)
    return {"provider": provider, "supported": False,
            "imported": 0, "updated": 0, "skipped": 0}


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


def wipe_workspace(s: Session, telegram_id: int) -> bool:
    """Erase everything the user has written, but keep the account.

    The difference from `delete_account` is the whole reason both exist:
    somebody who wants to start over is not somebody who wants to leave. This
    empties the workspace — tasks, habits, logs, prayers, journal, projects,
    missions — and then puts the default habits back, so what they land on is a
    fresh ErnestOS rather than a broken one with no habits in it at all.

    The account, the language, the theme and the notification settings survive,
    because none of those are "data the user wrote", they are how they use the
    product.
    """
    from sqlalchemy import delete as sql_delete

    ws = s.scalar(select(Workspace.id).where(Workspace.user_id == telegram_id))
    if ws is None:
        return False
    for model in WORKSPACE_TABLES:
        s.execute(sql_delete(model).where(model.workspace_id == ws))

    # Level, XP, streaks, daily scores and achievements hang off `user_id`,
    # not `workspace_id`, so the loop above never reaches them. Without this a
    # "wipe everything" left the user at level 9 with a 200-day streak over an
    # empty workspace — every number on the progress screen describing work
    # that no longer exists, and a leaderboard position to match.
    for model in (XPEvent, DailyScore, UserAchievement, UserProgress):
        s.execute(sql_delete(model).where(model.user_id == telegram_id))

    s.commit()
    # A workspace with no habits is not a clean slate, it is a dead one.
    seed_default_habits(s, ws)
    s.commit()
    return True


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

    # Referral rows hang off the *user*, not the workspace, so the loop above
    # does not reach them — and a foreign key pointing at a user being deleted
    # would block the delete outright on PostgreSQL. Leaving means leaving:
    # their own invite code goes, the record of who invited them goes, and so
    # does their side of anybody they invited. Somebody else's *count* drops by
    # one, which is the correct price of an account that no longer exists —
    # keeping the row to protect a statistic would be keeping data about a
    # person who asked to be forgotten.
    s.execute(sql_delete(ReferralCode).where(ReferralCode.user_id == telegram_id))
    s.execute(sql_delete(Referral).where(
        or_(Referral.referred_user_id == telegram_id,
            Referral.inviter_user_id == telegram_id)))

    # Progression hangs off the user for the same reason referrals do, and so
    # needs the same explicit removal. A deleted account must not leave a row on
    # the ranking table: it would keep occupying a position in a leaderboard
    # made of people, and every rank below it would be one worse than the truth.
    for model in (XPEvent, DailyScore, UserAchievement):
        s.execute(sql_delete(model).where(model.user_id == telegram_id))
    s.execute(sql_delete(UserProgress).where(UserProgress.user_id == telegram_id))

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
# Referrals
# ---------------------------------------------------------------------------
#
# One level only: A invites B. If B then invites C, C belongs to B and nobody
# gets credit twice. There is no tree here and there is not meant to be one.
#
# The whole system rests on two rules that are enforced by the schema rather
# than by care:
#
#   * `Referral.referred_user_id` is the primary key, so an account has at most
#     one inviter, for ever. First touch wins and cannot be overwritten.
#   * attribution is only attempted when `get_or_create_user` reports the
#     account was *just created*. An existing user opening a link is not a
#     referral, however the link was constructed.

#: How many real actions the invited person must take before the referral
#: counts. Clicking a link is not usage; neither is pressing /start. Three is
#: roughly "they came back and did something", which is the smallest signal
#: that distinguishes a referral from a click.
REFERRAL_QUALIFY_ACTIONS = 3

#: The prefix that marks a Telegram start parameter as one of ours.
REFERRAL_PREFIX = "ref_"

#: Length of the random part. ~10 characters of urlsafe base64 is about 60 bits
#: — far beyond guessable, and short enough that `ref_<code>` stays well inside
#: Telegram's 64-character start-parameter limit.
REFERRAL_CODE_BYTES = 8

#: Codes are matched exactly against this. `token_urlsafe` emits A-Z a-z 0-9 _ -
#: which is precisely the set Telegram accepts in a deep link, so no encoding
#: is ever needed and a code can be pasted anywhere.
REFERRAL_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{6,32}$")

#: Qualified referrals -> status key. Computed from the count on read; storing
#: a level would be a second copy of a number the referrals already answer.
REFERRAL_LEVELS = [
    (0, "new"),
    (1, "inviter"),
    (3, "builder"),
    (5, "connector"),
    (10, "ambassador"),
]


def get_or_create_referral_code(s: Session, user_id: int) -> str:
    """This account's invite code, generated once and stable for ever.

    Stable matters more than it sounds: a link already sitting in somebody's
    chat history has to keep working, so this never rotates. Repeated calls
    return the same string.

    The retry loop is not for the birthday problem — at 60 bits a collision is
    not going to happen — it is for the case where two requests for the same
    brand-new user race each other. Whichever loses the insert re-reads and
    returns the winner's code, so both callers still get one stable answer.
    """
    for _ in range(5):
        row = s.get(ReferralCode, user_id)
        if row is not None:
            return row.code
        row = ReferralCode(user_id=user_id,
                           code=secrets.token_urlsafe(REFERRAL_CODE_BYTES))
        s.add(row)
        try:
            # A SAVEPOINT, not a bare commit: this is usually called partway
            # through registration, in the caller's transaction, alongside the
            # freshly written user and workspace rows. `s.rollback()` on a
            # clash would throw all of that away and leave the caller looking
            # at objects the database no longer has. Only the insert is undone.
            with s.begin_nested():
                s.flush()
        except IntegrityError:
            # Either this user was given a code by a concurrent request, or the
            # random code collided. Both are answered by looking again.
            s.expunge(row)
            continue
        s.commit()
        return s.get(ReferralCode, user_id).code
    raise RuntimeError("could not allocate a referral code")


def parse_referral_payload(payload: str | None) -> str | None:
    """`ref_<code>` -> `<code>`, or None for anything else.

    Deliberately total: every malformed, hostile or simply unrelated start
    parameter returns None and onboarding carries on. A referral link is not
    something the user typed, so a broken one is never their problem to see.
    """
    if not payload or not isinstance(payload, str):
        return None
    if not payload.startswith(REFERRAL_PREFIX):
        return None
    code = payload[len(REFERRAL_PREFIX):]
    return code if REFERRAL_CODE_RE.match(code) else None


def claim_referral(s: Session, referred_user_id: int, payload: str | None, *,
                   source: str = "bot", newly_created: bool = False) -> bool:
    """Attribute a brand-new account to whoever invited them. Returns success.

    Every rejection here is silent and returns False, because none of them are
    the user's fault and none should interrupt onboarding:

      * the account already existed — attribution is first-touch only, and this
        is the check that stops an existing user being claimed by anybody who
        can get them to open a link;
      * the payload is not one of ours, or names a code nobody owns;
      * the code is the caller's own — a self-referral;
      * this account already has an inviter.

    The caller commits nothing: this function owns its own transaction, and on
    any failure the database is exactly as it was.
    """
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
        # Two /start retries arriving together. The primary key settles it and
        # the first inviter keeps the attribution.
        s.rollback()
        return False
    return True


def maybe_qualify_referral(s: Session, user_id: int) -> int | None:
    """Promote a pending referral once the invited person genuinely arrived.

    Returns the inviter's telegram id when this call is the one that flipped
    it, else None — so the caller knows whether a congratulation is owed, and
    knows it exactly once. Callers may fire a Telegram message on a non-None
    result without any risk of sending it twice.

    The two conditions are deliberately both required. Actions without
    onboarding means somebody poking at the API mid-signup; onboarding without
    actions means somebody who arrived and left. Only the pair is usage.

    Cheap enough to call on every recorded action: it is one indexed primary
    key lookup that returns None immediately for the overwhelming majority of
    users, who were never referred at all.
    """
    referral = s.get(Referral, user_id)
    if referral is None or referral.status != "pending":
        return None

    user = s.get(User, user_id)
    if user is None or not user.onboarded:
        return None
    if (user.actions_count or 0) < REFERRAL_QUALIFY_ACTIONS:
        return None

    referral.status = "qualified"
    referral.qualified_at = utcnow()
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
    """Aggregate counts for one inviter. Never the identity of the invited.

    Counts only, on purpose: knowing *that* four people joined is the whole of
    what the inviter needs, and knowing *who* would hand one user a list of
    other users they can now see the activity of.
    """
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
# Two progression systems live in ErnestOS and they are deliberately kept
# apart. *Referral* progression measures how many genuinely active people
# somebody brought in; *personal* progression measures how well they run their
# own life. Nothing below reads a referral, and nothing in the referral section
# reads any of this. Somebody who invited a hundred people and does not use the
# product has a low personal level, and that is the correct answer.
#
# The scoring formula is not a new one. ErnestOS already computes a weighted
# daily percentage — `overall_components` and `weighted_overall`, tasks 40 /
# habits 25 / focus 20 / prayer 15 — and it is already on the Home screen with
# those weights printed on the tiles. Introducing a second formula with
# different weights would mean two numbers that both claim to be "how today
# went", disagreeing by a few points, on two screens of the same app. That is
# the single most corrosive thing this codebase can do to its own credibility,
# and it is why the daily score *is* the overall percentage rather than a
# parallel calculation.
#
# Two categories a generic design would add are deliberately absent:
#
#   * **Execution quality.** ErnestOS does not reliably track whether a task
#     was done on the day it was planned for, and inventing a proxy would be
#     fake precision dressed as a metric. Its weight is not redistributed by
#     hand — `weighted_overall` already renormalises over whatever is present.
#   * **Reflection as its own component.** The journal is already a
#     non-negotiable habit, so it is counted through `habits`. Scoring it twice
#     would make one screen's worth of writing move the number twice.

#: Score -> grade. Read top down, first threshold wins. The labels are graded
#: rather than judgemental on purpose: the bottom band is "Reset", not "Failed".
#: Nothing in this product tells somebody they were a bad person on a Tuesday.
GRADE_BANDS = [(90, "S"), (80, "A"), (70, "B"), (60, "C"), (40, "D"), (0, "E")]

#: A day at or above this is a Perfect Day.
PERFECT_DAY_SCORE = 90

#: What a day has to reach to count toward the consistency streak. Deliberately
#: not "opened the app": a streak that survives on attendance measures nothing
#: and everybody knows it. 60 is the C band — a day with real work in it.
STREAK_THRESHOLD = 60

#: Missing a day does not have to cost a month. Two protected days per calendar
#: month, spent automatically, and they buy the streak only — no XP is awarded
#: for a day that did not earn it.
RECOVERY_DAYS_PER_MONTH = 2

#: How many days away before returning counts as a comeback, and how long
#: before another one can be earned. The cooldown is what stops the obvious
#: exploit of disappearing on purpose every few days to farm the bonus.
COMEBACK_AFTER_DAYS = 3
COMEBACK_COOLDOWN_DAYS = 14

#: Local days on record before a user is ranked. One brilliant first day must
#: not put a brand-new account at #1 above people with a year of work behind
#: them.
RANK_MIN_DAYS = 7

#: The rolling windows the two ranks are computed over, in calendar days.
#: Calendar, not active — a user who stops using ErnestOS should slide down as
#: the window fills with empty days, without anybody having to punish them.
RANK_WINDOW_DAYS = 30
WEEKLY_WINDOW_DAYS = 7

#: (threshold XP, key, roman numeral). Seven levels, and the level is always
#: computed from `xp_total` rather than stored: a stored level is a second copy
#: of a number XP already answers, and the two drift the first time an award is
#: replayed. Names belong to personal progression only — referral status levels
#: are a separate ladder in the referral section, and the two never mix.
PERSONAL_LEVELS = [
    (0, "starter", "I"),
    (500, "builder", "II"),
    (1500, "operator", "III"),
    (3500, "architect", "IV"),
    (7000, "commander", "V"),
    (15000, "elite", "VI"),
    (30000, "master", "VII"),
]

#: What each kind of event is worth. Awarded once per key, ever — see `award_xp`.
XP_VALUES = {
    "task": 10,          # a task completed
    "ritual_wake": 5,    # got up on time
    "ritual_prayer": 10,  # all five prayers
    "ritual_journal": 5,  # a complete journal entry
    "habit": 5,          # any other habit ticked
    "focus": 10,         # a weekly focus mission finished
    "perfect_day": 25,
    "streak_7": 50,
    "streak_30": 200,
    "comeback": 15,
    "onboarding": 40,
    "achievement": 20,
}

#: The most XP ordinary activity can produce in one local day. Without it, the
#: cheapest way to a high level is to create and complete forty trivial tasks,
#: which is the opposite of what the number is supposed to mean.
XP_DAILY_CAP = 120

#: Event types that are milestones rather than activity, and so are paid
#: outside the cap. A 30-day streak bonus that silently vanished because the
#: user also had a busy day would be a bug the user experiences as a lie.
XP_UNCAPPED = {"perfect_day", "streak", "comeback", "onboarding", "achievement"}


def grade_for(score: int) -> str:
    """The letter a score falls into."""
    for threshold, letter in GRADE_BANDS:
        if score >= threshold:
            return letter
    return "E"


def get_personal_level(xp: int) -> dict:
    """Everything the UI needs about where this XP total sits on the ladder.

    Returns the current level, the next one, and how far through the gap the
    user is — computed, never stored, so replaying an award cannot leave a
    level number that disagrees with the XP behind it.
    """
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
    """This user's summary row, created empty on first sight.

    Created lazily rather than at signup so the feature needs no backfill pass
    over existing accounts: the first time anybody's day is scored, their row
    appears. An account that never comes back never gets one, which is correct.
    """
    row = s.get(UserProgress, user_id)
    if row is None:
        try:
            with s.begin_nested():
                row = UserProgress(user_id=user_id)
                s.add(row)
        except IntegrityError:
            # Two concurrent requests both found nothing and both inserted.
            # The savepoint keeps the loser's other work intact.
            row = s.get(UserProgress, user_id)
            if row is None:
                raise
    return row


def award_xp(s: Session, user_id: int, event_key: str, event_type: str,
             xp: int, day: date) -> int:
    """Write one XP event if it has never been written. Returns XP granted.

    The unique constraint on `event_key` is the whole mechanism, not a
    belt-and-braces check on top of one: the caller does not have to know
    whether this award already happened, and two concurrent requests cannot
    both win. Everything that awards XP goes through here, so "can this be
    claimed twice?" has one answer in one place.

    Returns 0 when the key already existed or the daily cap is reached, so a
    caller can tell whether anything actually happened without a second query.
    """
    if xp <= 0:
        return 0

    if event_type not in XP_UNCAPPED:
        earned = s.scalar(select(func.coalesce(func.sum(XPEvent.xp), 0)).where(
            XPEvent.user_id == user_id, XPEvent.event_date == day,
            XPEvent.event_type.not_in(XP_UNCAPPED))) or 0
        if earned >= XP_DAILY_CAP:
            return 0
        xp = min(xp, XP_DAILY_CAP - earned)

    # A SAVEPOINT, not a plain flush, and this is not defensive decoration.
    # `sync_day_xp` calls this in a loop inside one transaction, and a bare
    # `s.rollback()` on the duplicate would discard *every award already made
    # in that transaction* — so a user whose second habit had already been paid
    # would silently lose the XP for the first. The nested block rolls back
    # only the insert that collided.
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
                          day: date) -> DailyScore:
    """Write (or rewrite) one day's score row from that day's live data.

    Only ever the day it is asked for. Recomputing today as the day goes on is
    the whole point; recomputing *last Tuesday* is not, because the source rows
    move — a task edited next week must not rewrite a score the user has
    already been shown, and a streak must not change retroactively under them.

    Components are stored as -1 for "absent", matching the distinction
    `overall_components` draws between a category with nothing in it and a
    category scored zero. A day with no tasks is not a day that failed its
    tasks.
    """
    components = overall_components(s, ws, day)
    total = weighted_overall(components)

    row = s.scalar(select(DailyScore).where(DailyScore.user_id == user_id,
                                            DailyScore.day == day))
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
    row.focus_score = part("focus")
    row.prayer_score = part("prayer")
    row.total_score = int(total)
    row.grade = grade_for(int(total))

    try:
        s.flush()
    except IntegrityError:
        # Another request inserted this user's day between the read and the
        # write. Theirs is as correct as ours — both read the same source rows.
        s.rollback()
        row = s.scalar(select(DailyScore).where(DailyScore.user_id == user_id,
                                                DailyScore.day == day))
        if row is None:
            raise
    return row


def sync_day_xp(s: Session, user_id: int, ws: int, day: date) -> int:
    """Award every XP event today's state has earned. Returns XP newly granted.

    Driven by *state*, not by intercepting each action, and that is what makes
    it safe to call on every write. Each award names itself after the thing it
    is for — the task's id, the habit and the day — so running this a hundred
    times in a row grants exactly what running it once granted.

    It is also what closes the toggle-farming hole for free. Completing a task,
    undoing it and completing it again produces the key `task:412` all three
    times; the ledger accepts it once. No XP is taken back when something is
    undone, because "award once, when it first happens" needs no refund path
    and a refund path is where double-spend bugs live.
    """
    granted = 0

    # Tasks completed on this local day. `completed_at` is a UTC instant, so
    # the day is converted to a UTC window rather than compared directly —
    # anything finished after 19:00 in Tashkent carries yesterday's UTC date.
    start, end = utc_window(day)
    task_ids = s.scalars(select(Task.id).where(
        Task.workspace_id == ws, Task.status == "done",
        Task.completed_at >= start, Task.completed_at < end)).all()
    for task_id in task_ids:
        granted += award_xp(s, user_id, f"task:{task_id}", "task",
                            XP_VALUES["task"], day)

    # Habits ticked today. The three derived ones are worth naming separately —
    # they are the floor the product is built on — and everything else is a
    # habit the user chose, worth the ordinary amount.
    rows = s.execute(select(Habit.id, Habit.system_key)
                     .join(HabitLog, HabitLog.habit_id == Habit.id)
                     .where(HabitLog.workspace_id == ws, HabitLog.day == day,
                            HabitLog.done.is_(True))).all()
    # One `event_type` per ritual, not one shared "ritual" label. They used to
    # share it, and `early_riser` — which counts days the user woke up on time
    # — counted prayer and journal days too, so anyone praying and writing
    # daily earned a thirty-day award in ten. Migration 0009 relabels the rows
    # already written under the shared name.
    SYSTEM_XP = {SYSTEM_WAKEUP: ("ritual_wake", "wake"),
                 SYSTEM_PRAYER: ("ritual_prayer", "prayer"),
                 SYSTEM_JOURNAL: ("ritual_journal", "journal")}
    for habit_id, system_key in rows:
        value_key, event_type = SYSTEM_XP.get(system_key, ("habit", "task"))
        granted += award_xp(s, user_id, f"habit:{habit_id}:{day}", event_type,
                            XP_VALUES[value_key], day)

    # Weekly focus missions finished. Keyed on the mission, not the day, so
    # finishing one is worth ten once — not ten every day of the week it stays
    # ticked.
    focus_ids = s.scalars(select(WeeklyFocus.id).where(
        WeeklyFocus.workspace_id == ws, WeeklyFocus.done.is_(True),
        WeeklyFocus.week_start == week_start(day))).all()
    for focus_id in focus_ids:
        granted += award_xp(s, user_id, f"focus:{focus_id}", "focus",
                            XP_VALUES["focus"], day)

    return granted


def _month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _apply_day_to_streak(progress: UserProgress, day: date, score: int) -> dict:
    """Move the streak on by one day. Returns what happened, for the caller.

    Called once per local day, in order, from `refresh_progress`. The same day
    arriving twice is a no-op — `last_score_date` is the guard — which matters
    because every single write in the product triggers a refresh.

    The rules, in the order they are checked:

      * same day again  -> nothing moves;
      * a good day right after the last one -> streak + 1;
      * a weak day right after the last one -> spend a Recovery Day if one is
        left this month, otherwise the streak resets to zero;
      * a gap of more than one day -> the streak restarts at 1 if today was
        good, and a long enough gap also earns a Comeback.

    A Recovery Day protects the streak and nothing else: no XP is granted for a
    day that did not earn any. The allowance resets by comparing the stored
    month with today's, so nothing has to sweep every user on the first.
    """
    result = {"streak_changed": False, "recovery_used": False,
              "comeback": False, "gap": 0}

    last = progress.last_score_date
    if last == day:
        return result

    # A new calendar month hands the allowance back.
    if progress.recovery_month != _month_key(day):
        progress.recovery_month = _month_key(day)
        progress.recovery_used = 0

    good = score >= STREAK_THRESHOLD
    gap = (day - last).days if last else None
    result["gap"] = gap or 0

    if last is None or gap is not None and gap > 1:
        # Returning after a break, or arriving for the first time.
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
            result["recovery_used"] = True   # streak survives, untouched
        else:
            progress.current_streak = 0
    else:
        # gap < 0: a day earlier than the last one scored. Backfilling history
        # must never rewrite a streak the user has already been shown.
        return result

    progress.last_score_date = day
    progress.best_streak = max(progress.best_streak or 0,
                               progress.current_streak or 0)
    result["streak_changed"] = True
    return result


def _eligible_window(s: Session, user_id: int, today: date,
                     days: int) -> tuple[date, int]:
    """(first day, how many calendar days) the rolling index is averaged over.

    Calendar days, so that going quiet lowers the index on its own rather than
    needing a punishment rule. But never days from before this account existed:
    a user three days old is measured over three days, not charged for
    twenty-seven days of absence that happened before they arrived.
    """
    first_scored = s.scalar(select(func.min(DailyScore.day))
                            .where(DailyScore.user_id == user_id))
    start = today - timedelta(days=days - 1)
    if first_scored and first_scored > start:
        start = first_scored
    return start, (today - start).days + 1


def performance_index(s: Session, user_id: int, today: date,
                      days: int = RANK_WINDOW_DAYS) -> float:
    """How this user has actually been doing lately, 0-100. The ranking metric.

    Explicitly *not* lifetime XP. Ranking on a lifetime total would mean the
    board is ordered by how long each account has existed, and nobody joining
    this year could ever pass somebody who stopped using the product in March.
    Lifetime effort is what Level is for; this is current form.

        70%  average daily score across the window
        20%  consistency — the share of days that cleared the streak threshold
        10%  weekly focus follow-through

    Missing days count as zero inside the window, which is what makes the
    number decay on its own when somebody stops showing up.
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
    average = sum(totals) / span                       # absent days are zeroes
    consistency = sum(1 for t in totals if t >= STREAK_THRESHOLD) / span * 100
    focus_days = [r[1] for r in rows if r[1] >= 0]
    focus = sum(focus_days) / len(focus_days) if focus_days else 0.0

    return round(0.70 * average + 0.20 * consistency + 0.10 * focus, 2)


def refresh_progress(s: Session, user_id: int, *, day: date | None = None,
                     tz: ZoneInfo | None = None) -> dict:
    """Bring one user's progression up to date. The single entry point.

    Everything hangs off this: scoring the day, paying out whatever the day
    earned, moving the streak, and refreshing the two ranking indexes. It is
    called from exactly one place in ordinary use — the action counter every
    write in the product already funnels through — plus onboarding completion,
    because a day's worth of work can be done before `onboarded` flips.

    Returns what changed, so a caller can decide whether anything is worth
    telling the user about. Nothing here sends a message: this is a database
    service, and putting a Telegram call inside it would make every write in
    the product depend on the network.

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
    granted = sync_day_xp(s, user_id, ws, day)

    # A Perfect Day is paid once per day, ever — the key carries the date, so
    # a day that dips back below 90 and climbs again does not pay twice.
    if score_row.total_score >= PERFECT_DAY_SCORE:
        granted += award_xp(s, user_id, f"perfect_day:{user_id}:{day}",
                            "perfect_day", XP_VALUES["perfect_day"], day)

    moved = _apply_day_to_streak(progress, day, score_row.total_score)

    if moved["comeback"]:
        granted += award_xp(s, user_id, f"comeback:{user_id}:{day}",
                            "comeback", XP_VALUES["comeback"], day)
    for milestone in (7, 30):
        if (progress.current_streak or 0) >= milestone:
            granted += award_xp(
                s, user_id, f"streak_{milestone}:{user_id}:{day}", "streak",
                XP_VALUES[f"streak_{milestone}"], day)

    # Perfect days are counted from the ledger rather than incremented, so a
    # recomputed score cannot inflate the count.
    progress.perfect_days = int(s.scalar(select(func.count()).select_from(XPEvent)
                                         .where(XPEvent.user_id == user_id,
                                                XPEvent.event_type == "perfect_day")) or 0)
    progress.xp_total = xp_total(s, user_id)
    progress.scored_days = int(s.scalar(select(func.count()).select_from(DailyScore)
                                        .where(DailyScore.user_id == user_id)) or 0)
    progress.performance_index_30d = performance_index(s, user_id, day,
                                                       RANK_WINDOW_DAYS)
    progress.performance_index_7d = performance_index(s, user_id, day,
                                                      WEEKLY_WINDOW_DAYS)

    after_level = get_personal_level(progress.xp_total)["number"]
    unlocked = check_achievements(s, user_id, progress, score_row)

    return {
        "score": score_row.total_score,
        "grade": score_row.grade,
        "xp_gained": granted,
        "xp_total": progress.xp_total,
        "level_up": after_level > before_level,
        "level": get_personal_level(progress.xp_total),
        "streak": progress.current_streak or 0,
        "perfect_day": score_row.total_score >= PERFECT_DAY_SCORE,
        "achievements": unlocked,
        **moved,
    }


# ---------------------------------------------------------------------------
# Achievements
# ---------------------------------------------------------------------------
#
# Definitions live here rather than in a table: each one is a key, a rule and
# three translations — code, in other words — and a row per definition would
# mean a migration every time a word changed. `user_achievements` stores only
# the fact that somebody earned one.
#
# Thirteen, and no more for now. A wall of badges is how a progression system
# stops meaning anything: if everything is an achievement, nothing is.

#: key -> (rule, target) where `rule` reads the progress row and the day.
#: `target` is what the UI draws a progress bar toward, or None when the
#: achievement is a single event rather than a count.
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
    """Every number the achievement rules read, in one pass.

    Deliberately one function rather than a query per achievement: thirteen
    rules that each go to the database would be thirteen round trips on every
    single write in the product.
    """
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
    """Unlock whatever this user has now earned. Returns only what is new.

    The unique constraint on (user_id, achievement_key) is what makes this
    idempotent, exactly as `event_key` does for XP — so this can run on every
    write without a "have I already?" check per achievement.
    """
    values = _achievement_values(s, user_id, progress)
    already = set(s.scalars(select(UserAchievement.achievement_key)
                            .where(UserAchievement.user_id == user_id)).all())

    unlocked: list[str] = []
    for key, field, target in ACHIEVEMENTS:
        if key in already or values.get(field, 0) < target:
            continue
        # Savepoint for the same reason `award_xp` uses one: this runs in a
        # loop, and a collision on the fourth achievement must not undo the
        # three already written in this transaction.
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
    """Every achievement, unlocked or not, with progress where it is meaningful.

    Returns all thirteen rather than only the earned ones: a locked achievement
    with "7 / 30" against it is the part that does the motivating, and a screen
    that shows only what somebody already has cannot do that.
    """
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
#
# One indexed scan of one narrow column, and nothing else. The tempting
# implementation — aggregate everybody's daily scores when somebody opens their
# profile — is O(all users x all history) per request, and it is why
# `user_progress` exists at all. Each user's index is written when their own day
# changes; ranking only ever reads it.

def _rank_for(s: Session, column, value: float) -> tuple[int, int]:
    """(rank, eligible users) for a value in one of the index columns.

    Rank is "how many people are strictly ahead, plus one", so equal indexes
    share a rank — #184, #184, #186. Inventing decimal places to break ties
    would be precision the underlying numbers do not have.
    """
    eligible = int(s.scalar(select(func.count()).select_from(UserProgress)
                            .where(UserProgress.scored_days >= RANK_MIN_DAYS)) or 0)
    ahead = int(s.scalar(select(func.count()).select_from(UserProgress)
                         .where(UserProgress.scored_days >= RANK_MIN_DAYS,
                                column > value)) or 0)
    return ahead + 1, eligible


def global_rank(s: Session, user_id: int) -> dict:
    """Where this user stands, and whether they stand anywhere yet.

    Ranking unlocks after `RANK_MIN_DAYS` days on record. Before that the
    payload says how many are left rather than showing a rank built on two
    days of data — a brand-new account with one 100-point day would otherwise
    sit at #1 above people with a year behind them, which discredits the board
    for everybody who can see it.
    """
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
    # Best only ever improves. Overwriting it with a worse rank would make
    # "personal best" mean "most recent", which is not what the words say.
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
        # Rounded to whole percent above 1%, one decimal below it — a top-2%
        # user is not helped by being told 1.4327%.
        "top_percent": (round(rank / users * 100)
                        if users and rank / users * 100 >= 1
                        else round(rank / users * 100, 1) if users else None),
        # Only reported when there is a real previous rank to compare with.
        # Inventing movement on a first view would be a number that means
        # nothing dressed as one that means something.
        "movement": (previous - rank) if previous is not None else None,
    }


def progress_snapshot(s: Session, user_id: int, *,
                      tz: ZoneInfo | None = None) -> dict:
    """Everything the Progress screen shows, for one user, about themselves.

    Never takes a user id from the caller's request — the id comes from the
    verified Telegram identity — so there is no parameter to tamper with and
    no way to read somebody else's day. What ranking exposes about other people
    is a count and a position, never a name.
    """
    user = s.get(User, user_id)
    zone = tz or user_tz(user)
    today = today_local(zone)

    progress = s.get(UserProgress, user_id)
    if progress is None:
        # Same shape as the populated payload, down to the breakdown keys. A
        # client that has to branch on whether a field exists is a client that
        # will get it wrong on the one screen nobody tests: the first one a new
        # user ever opens.
        level = get_personal_level(0)
        return {
            "daily": {"score": 0, "grade": "E", "perfect_day": False,
                      "to_perfect": PERFECT_DAY_SCORE,
                      "breakdown": {"tasks": None, "habits": None,
                                    "focus": None, "prayer": None},
                      "weights": OVERALL_WEIGHTS},
            "xp": {"total": 0, "today": 0, "cap": XP_DAILY_CAP},
            "level": level,
            "streak": {"current": 0, "best": 0,
                       "recovery_remaining": RECOVERY_DAYS_PER_MONTH},
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

    used = (progress.recovery_used or 0) if progress.recovery_month == _month_key(today) else 0

    return {
        "daily": {
            "score": row.total_score if row else 0,
            "grade": row.grade if row else "E",
            "perfect_day": bool(row and row.total_score >= PERFECT_DAY_SCORE),
            "to_perfect": max(PERFECT_DAY_SCORE - (row.total_score if row else 0), 0),
            "breakdown": {
                "tasks": part(row.task_score if row else None),
                "habits": part(row.habit_score if row else None),
                "focus": part(row.focus_score if row else None),
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


def platform_progress_stats(s: Session) -> dict:
    """Aggregate progression numbers for the operator. No personal content."""
    ranked = int(s.scalar(select(func.count()).select_from(UserProgress)
                          .where(UserProgress.scored_days >= RANK_MIN_DAYS)) or 0)
    today = today_local()
    avg = s.scalar(select(func.avg(DailyScore.total_score))
                   .where(DailyScore.day == today))
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
#: the day was claimed another way. Negative so it can never collide with a
#: real row id, and so `s.get(DailyReportLog, id)` simply finds nothing.
FALLBACK_CLAIM = -1


def _fallback_claim_name(ws: int, report_type: str) -> str:
    """The `job_runs` key standing in for one workspace's outbox row."""
    return f"rep:{ws}:{report_type}"


#: Claims refused by the database for a reason that is not a peer worker.
#: Read back by `/health/reports` and `/tekshir`, because the symptom of this
#: is silence and silence is what made it hard to find.
CLAIM_ANOMALIES: dict = {}


#: How many times one report may be attempted in a day before it is given up
#: on. Three, because the errors worth retrying — a rate limit, a 500, a
#: dropped connection — clear within minutes, and the ones that are not worth
#: retrying are marked permanent explicitly rather than by exhausting this.
REPORT_MAX_ATTEMPTS = 3


def claim_report(s: Session, ws: int, report_type: str,
                 report_date: date) -> int | None:
    """Try to own this report. Returns the outbox id, or None if someone else won.

    The INSERT is the lock: the unique constraint means exactly one worker can
    succeed, so two schedulers cannot both send (audit 036).

    A row left in `retry` is taken over rather than refused. Without that, one
    momentary failure — Telegram rate-limiting us, a connection dropped
    mid-send — cost the user their report for the rest of the day, because the
    row that recorded the failure was also the row that blocked every later
    attempt. `sent` and `failed` are still final, so this cannot resend a
    report that went out or hammer an account that has blocked the bot.
    """
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
        # The insert was rejected, yet nothing occupies this slot. That is not
        # a peer winning the race — it means the table is carrying a
        # constraint this code does not know about, most likely a unique index
        # from an older schema on the wrong columns (workspace and type, with
        # no date), under which exactly one report per user is ever allowed to
        # exist and every day after the first is refused. Silently returning
        # None here, as this used to, makes that indistinguishable from a
        # normal skip and hides it for ever.
        CLAIM_ANOMALIES["count"] = CLAIM_ANOMALIES.get("count", 0) + 1
        CLAIM_ANOMALIES["last"] = f"{report_type} {report_date} ws={ws}"
        # Once per report per day, not once per user per tick. Every account
        # hits this within the same second, and at a two-minute tick that was
        # thousands of identical lines a night — which buries the one line
        # that matters and costs real money in log retention.
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

        # The outbox is unusable, but "once a day" does not have to live in
        # that particular table. `job_runs` carries the same kind of unique
        # key, is written by a different feature and is not affected — so the
        # report can still be claimed exactly once and still be delivered.
        # A degraded mode on purpose: it keeps the product working on a
        # database whose schema is wrong, instead of going silent and waiting
        # for somebody to notice.
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
    """Record the failure, and decide whether today is over for this report.

    `permanent=True` is the usual case and the old behaviour: the account
    blocked the bot or no longer exists, neither improves within the day, and
    retrying every tick would mean hammering Telegram with the same rejection
    for hours.

    `permanent=False` is for the failures that say nothing about the account —
    a rate limit, a 500, a timeout. Those used to be filed as permanent too,
    so a single unlucky second cost the user their whole report. The row is
    parked in `retry` instead and the next tick picks it up, up to
    `REPORT_MAX_ATTEMPTS`.
    """
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
    """Drop a claim entirely, so the next run may try again from scratch.

    The workspace and date are only needed for a claim taken through the
    `job_runs` fallback, which has no outbox row to delete.
    """
    if report_id == FALLBACK_CLAIM:
        if ws is not None and report_type and report_date:
            release_job_run(s, _fallback_claim_name(ws, report_type), report_date)
        return
    row = s.get(DailyReportLog, report_id)
    if row is not None:
        s.delete(row)
        s.commit()


#: How long a row may sit in `claimed` before the next tick treats it as
#: abandoned. Generous on purpose: a claim is only ever held for as long as it
#: takes to render one report and hand it to Telegram, so anything still
#: `claimed` half an hour later belongs to a process that is gone.
STALE_CLAIM_MINUTES = 30


def reclaim_stale_claims(s: Session, report_date: date) -> int:
    """Free claims whose worker died, and return how many were freed.

    `claim_report` writes `claimed` and the sender marks it `sent` or `failed`
    afterwards. Between those two writes the process can disappear — a deploy,
    an OOM kill, a platform restart — and the row is then the worst of both
    worlds: it satisfies the unique constraint, so no later tick can claim that
    slot, and nothing ever sends it. That user's report for that day is lost,
    silently, with no way back. Repeat the deploy each morning and the feature
    is simply off for them.

    Deleting the row is safe because the once-a-day guarantee never lived in
    the row's *existence* — it lives in its status. A row that reached `sent`
    or `failed` is a decision and is left alone; only `claimed` is ambiguous,
    and only after it is too old to belong to a live sender.
    """
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
    """Claim today's run of a platform-wide job. True means "you send it".

    The same trick `claim_report` uses, minus the workspace: the INSERT is the
    lock, so of every tick in every process exactly one gets True and the rest
    get False. That is what lets a once-a-day job run on a two-minute tick —
    the schedule stops being the thing that guarantees "once", and a restart at
    any hour can no longer cost a day.
    """
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

    # What today actually asks for, not what yesterday asked for. The habit
    # count in this payload used to be yesterday's total under a "today" key,
    # so a Monday/Wednesday habit was announced on a Tuesday.
    today_habits = [h for h in list_habits(s, ws, today, tz=tz) if h["due"]]

    # Yesterday as one comparable number, from the same function every other
    # surface uses, plus the components behind it.
    y_components = overall_components(s, ws, yesterday)
    y_available = [v for v in y_components.values() if v is not None]
    y_overall = weighted_overall(y_components)

    return {
        # The report opens by greeting somebody, so it needs to know who.
        "name": user.first_name or "",
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
            # Today's habits: the ones still to do, and how many are due at
            # all — a morning report that lists the day's work has to include
            # the part of it that repeats.
            "habits": [h["name"] for h in today_habits if not h["done"]],
            "habits_done": sum(1 for h in today_habits if h["done"]),
            "habits_total": len(today_habits),
            "prayer_required": PRAYER_REQUIRED,
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
    """(telegram_id, workspace_id, language) for every user who should get reports.

    The question this asks is the same one the API and the bot ask before
    letting somebody in: *is this account allowed through right now?* It used
    to ask something subtly different — `is_subscribed IS TRUE` — and that was
    wrong in a way that was invisible from the outside and switched the whole
    feature off.

    `is_subscribed` is not "may use ErnestOS". It is "Telegram confirmed, at
    some point, that this account is in the channel", and it is written in
    exactly two places, both of them behind a *confirmed* membership check.
    A user inside their free run never reaches one — `check_subscription`
    returns "free" and stops — and if no channel is configured, nobody ever
    reaches one at all. So a fully onboarded user whom `trial_state` reports as
    `gated=False` sat at `is_subscribed=False` forever, matched nothing here,
    and silently received no morning report, no evening report and no
    reminders. The suite missed it because its fixtures set the flag by hand.

    The rule below is `dependencies.trial_state` written as SQL, deliberately
    in the same order:

      * onboarded, always — a half-registered account gets nothing;
      * no channel configured → everybody qualifies, because there is nothing
        to gate on;
      * otherwise → in the channel, **or** still inside the free run.

    Kept as one query rather than a Python loop over every user: this runs on
    every scheduler tick.
    """
    import dependencies as deps

    allowed = [User.onboarded.is_(True)]
    if deps.REQUIRED_CHANNEL_ID:
        allowed.append(or_(
            User.is_subscribed.is_(True),
            # `actions_count` is NULL for rows written before the counter
            # existed; coalesce so those users read as "free run untouched"
            # rather than dropping out of the comparison entirely.
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
#
# Every preference column is nullable, because they were added to live tables.
# NULL means "never chosen", and these are what it means instead. Reading them
# through one function is what stops "the default" from being three different
# things in three files.

#: 07:00, and the previous value is worth recording because it was a bug that
#: looked like a setting. This was `dtime(4, 0)`, from when the scheduler ran on
#: the server clock: 04:00 UTC is 09:00 in Tashkent, which is a reasonable hour
#: to be told about your day. When the scheduler moved to the project clock
#: (`svc.TZ`, Asia/Tashkent) that same literal silently became four in the
#: morning. Nothing failed, no error was logged, and the reports went out on
#: time every day — to people who were asleep, who reported the morning report
#: as "not arriving" because they only ever saw it hours later under a stack of
#: other notifications.
#:
#: 05:00 matches `DEFAULT_WAKE_TIME` — the report arrives as the day is meant to
#: start, not an hour before it, and it lands near bomdod for the audience this
#: is built for. The 90-minute window carries it to 06:30 for anybody who is up
#: a little later. This is only what NULL means; a chosen time always wins.
DEFAULT_MORNING_TIME = dtime(5, 0)
#: 21:30 rather than 21:00: the day's last habits and prayers are usually still
#: being entered on the hour, and a summary that arrives mid-entry is wrong.
DEFAULT_EVENING_TIME = dtime(21, 0)

#: How long after its configured time a report may still go out. Past this the
#: day has moved on, and a morning summary at noon is noise rather than a
#: report — a user who onboards at 15:00 must not be sent one immediately.
REPORT_WINDOW = timedelta(minutes=90)

#: How long after its moment a *missed* report may still be delivered.
#:
#: The 90-minute window above assumes the process is alive at the moment the
#: report is owed. On a platform that sleeps an idle service or cycles its
#: containers, the 05:00 report is exactly the one nothing is awake for: the
#: user is asleep, no request comes in, and by the time anything runs again the
#: window has closed and that day's report is gone — permanently, and with no
#: trace. Habit reminders never showed this because people set them for hours
#: they are awake, which is also when the service is being used.
#:
#: So a report that was never sent stays owed. Six hours is long enough to
#: cover a night of downtime and short enough that "this morning's summary"
#: still means this morning. `claim_report` remains the once-a-day guarantee,
#: so a wider window cannot produce a second copy.
REPORT_CATCHUP = timedelta(hours=6)
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
    if now < scheduled:
        return False

    # Never past the end of the user's own day: a report belongs to the day it
    # describes, and tomorrow's tick will be claiming tomorrow's slot.
    end_of_day = datetime.combine(now.date(), dtime(23, 59, 59))
    limit = min(scheduled + REPORT_CATCHUP, end_of_day)
    # Past the limit the day has moved on, and this is also what stops an
    # account registered at 15:00 from being greeted with a summary of a
    # morning it was not there for: 15:00 is long past 05:00 plus the
    # catch-up.
    return now <= limit


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
    """Record that today's nudge for this habit has gone out.

    Writes the day's log row if it does not exist yet, with `done=False`: the
    row means "this habit has a state today", and being reminded is part of
    that state. `toggle_habit` updates the same row rather than adding another,
    because (habit_id, day) is unique.
    """
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
        # Growth, aggregate only — counts and a conversion rate, never who
        # invited whom. Two grouped queries over an indexed column.
        **platform_referral_stats(s),
        # Progression, aggregate only — averages and counts, never a name and
        # never one person's day.
        **platform_progress_stats(s),
    }


# ---------------------------------------------------------------------------
# Scheduler coordination
# ---------------------------------------------------------------------------

#: Stable per-job key for pg_try_advisory_lock. Any two instances computing it
#: from the same job name land on the same number.
def _lock_key(name: str) -> int:
    import zlib
    return zlib.crc32(name.encode()) - 2**31


#: How many consecutive ticks may be refused the lock before that is treated
#: as a stuck lock rather than a busy peer. At a two-minute tick this is about
#: twenty minutes, which no healthy report batch comes close to.
LOCK_REFUSAL_ALARM = 10

#: Per-job count of consecutive refusals, for the warning above and for
#: `/health/ready` to read back.
LOCK_REFUSALS: dict[str, int] = {}


class JobLock:
    """Hold a PostgreSQL advisory lock for the duration of one job run.

    Two instances of the app would otherwise both fire the same job. The loser
    exits quietly instead of sending a second copy (audit 032). On SQLite there
    is nothing to coordinate, so the lock is always granted.

    The lock is **transaction**-scoped, and that is the whole point.
    `pg_try_advisory_lock` — what this used to call — is scoped to the
    *connection*, and a connection is a pooled resource that this class does
    not own. Releasing it was an explicit statement in `__exit__`, so any
    failure on the way out (the unlock itself, or the commit after it) fell
    through to `finally: close()` and handed the connection back to the pool
    **still holding the lock**. Nothing afterwards knew to release it: every
    later tick asked on some other connection, got False, and skipped the batch
    without sending anything — for every user, silently, until the process
    happened to restart. A single failed unlock could switch reports off for
    days.

    `pg_try_advisory_xact_lock` cannot leak that way, because PostgreSQL
    releases it when the transaction ends, however it ends — commit, rollback,
    a dropped connection or a killed process. The job body runs inside that
    open transaction, which is already how this worked.
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
            # One refusal is a peer instance doing the work, which is the
            # feature. Twenty minutes of them is not, and used to be invisible.
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
        # Ending the transaction is what releases the lock, so this needs no
        # unlock statement and cannot fail to run one.
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
#     user and refuses anything they are not a member of. There is no team
#     equivalent of "workspace_id came from the request body".
#   * **The item is shared, the tick is not.** Either member may add, edit or
#     archive a task; neither can tick it for the other. That is what lets a
#     report say what each of them actually did.
#   * **Nothing leaks into private space.** No query in this section touches
#     `workspace_id`, and no workspace query touches a team table, so joining
#     a team reveals nothing about anyone's own lists.

#: A shared space is for people working together, not an audience. Small
#: enough that the reports stay readable and nobody has to scroll a roster.
#: A product decision rather than a deployment knob, so it is a constant here
#: instead of an environment variable — settings live in `config`.
MAX_TEAM_MEMBERS = 8
#: How many teams one account may belong to, so a single user cannot be
#: dragged into an unbounded number of daily summaries.
MAX_TEAMS_PER_USER = 5
#: Bytes of randomness in an invite code.
TEAM_CODE_BYTES = 9
#: The longest a team name may be. Long enough for "Ernest va Gulyora", short
#: enough to sit on one line of a report.
TEAM_NAME_MAX = 60


def clean_team_name(name: str | None) -> str:
    """A usable team name, or the empty string when there is none."""
    return " ".join(str(name or "").split())[:TEAM_NAME_MAX].strip()


def create_team(s: Session, user_id: int, name: str) -> Team:
    """Start a team, with its creator as the first member and its owner."""
    name = clean_team_name(name)
    if not name:
        raise ValueError("empty_name")
    if len(teams_for(s, user_id)) >= MAX_TEAMS_PER_USER:
        raise ValueError("too_many_teams")

    for _ in range(5):
        team = Team(name=name, owner_id=user_id,
                    code=secrets.token_urlsafe(TEAM_CODE_BYTES))
        s.add(team)
        try:
            # A SAVEPOINT, so a collided code cannot roll back whatever the
            # caller was already doing.
            with s.begin_nested():
                s.flush()
            break
        except IntegrityError:
            s.expunge(team)
    else:
        raise RuntimeError("could not allocate a team code")

    s.add(TeamMember(team_id=team.id, user_id=user_id, role="owner"))
    seed_team_rituals(s, team.id, user_id)
    s.commit()
    return team


#: What every team starts with, and it is the personal set on purpose. A shared
#: space that could only hold "tasks we both agreed on" would be a to-do list
#: with two names on it; the point of doing this with somebody is that the
#: whole programme is shared — you both get up, you both pray, you both write
#: the day down — and each of you ticks your own.
DEFAULT_TEAM_HABITS = DEFAULT_HABITS


def seed_team_rituals(s: Session, team_id: int, created_by: int) -> int:
    """Put the ritual habits into a team. Idempotent; returns how many it added.

    Protected, like their personal counterparts: these are the spine of the
    programme, and a team where one member can delete "namoz" for both of them
    is not a shared commitment.
    """
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
    """Every live team this user belongs to, oldest first."""
    return list(s.scalars(
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user_id, Team.archived_at.is_(None))
        .order_by(Team.created_at)
    ).all())


def team_for(s: Session, user_id: int, team_id: int) -> Team | None:
    """The team, only if this user is in it. The single access check."""
    return s.scalar(
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(Team.id == team_id, Team.archived_at.is_(None),
               TeamMember.user_id == user_id)
    )


def _require_team(s: Session, user_id: int, team_id: int) -> Team:
    team = team_for(s, user_id, team_id)
    if team is None:
        raise PermissionError("not_a_member")
    return team


def team_members(s: Session, team_id: int) -> list[dict]:
    """Who is in the team, with the name each of them is shown under."""
    rows = s.execute(
        select(TeamMember, User)
        .join(User, User.telegram_id == TeamMember.user_id)
        .where(TeamMember.team_id == team_id)
        .order_by(TeamMember.joined_at)
    ).all()
    out = []
    for member, user in rows:
        name = (user.first_name or "").strip() or (user.username or "").strip()
        out.append({"user_id": member.user_id, "role": member.role,
                    "name": name or f"#{user.member_no}",
                    "joined_at": member.joined_at})
    return out


def parse_team_payload(payload: str | None) -> str | None:
    """`team_<code>` -> `<code>`, or None for anything else.

    Total, like `parse_referral_payload`: every other start parameter, and
    every malformed one, simply is not a team invite.
    """
    value = (payload or "").strip()
    if not value.startswith("team_"):
        return None
    code = value[len("team_"):].strip()
    return code or None


def join_team(s: Session, user_id: int, code: str) -> tuple[Team | None, str]:
    """Accept an invite. Returns (team, outcome).

    Outcomes: "joined", "already", "full", "unknown". Never raises on a bad
    code — a stale or mistyped link is an ordinary thing for a user to have,
    not an error for the caller to handle.
    """
    code = (code or "").strip()
    if not code:
        return None, "unknown"
    team = s.scalar(select(Team).where(Team.code == code,
                                       Team.archived_at.is_(None)))
    if team is None:
        return None, "unknown"

    existing = s.scalar(select(TeamMember).where(
        TeamMember.team_id == team.id, TeamMember.user_id == user_id))
    if existing is not None:
        return team, "already"

    count = s.scalar(select(func.count()).select_from(TeamMember)
                     .where(TeamMember.team_id == team.id)) or 0
    if count >= MAX_TEAM_MEMBERS:
        return team, "full"
    if len(teams_for(s, user_id)) >= MAX_TEAMS_PER_USER:
        return team, "full"

    s.add(TeamMember(team_id=team.id, user_id=user_id, role="member"))
    try:
        with s.begin_nested():
            s.flush()
    except IntegrityError:
        # Two taps on the same link in the same second.
        s.rollback()
        return team, "already"
    s.commit()
    return team, "joined"


def rename_team(s: Session, user_id: int, team_id: int, name: str) -> Team:
    """Rename a team. Any member may do it.

    Deliberately not owner-only: a shared space belongs to the people in it,
    and for the two-person case this is built around, "ask him to rename it"
    is friction with no safety behind it.
    """
    team = _require_team(s, user_id, team_id)
    cleaned = clean_team_name(name)
    if not cleaned:
        raise ValueError("empty_name")
    team.name = cleaned
    s.commit()
    return team


def leave_team(s: Session, user_id: int, team_id: int) -> bool:
    """Leave a team. The last member out archives it.

    The owner leaving hands ownership to the longest-standing member rather
    than dissolving the team under everybody else — the person who set it up
    is not necessarily the person who keeps using it.
    """
    team = team_for(s, user_id, team_id)
    if team is None:
        return False
    row = s.scalar(select(TeamMember).where(
        TeamMember.team_id == team_id, TeamMember.user_id == user_id))
    if row is None:
        return False
    s.delete(row)
    s.flush()

    remaining = team_members(s, team_id)
    if not remaining:
        team.archived_at = utcnow()
    elif team.owner_id == user_id:
        team.owner_id = remaining[0]["user_id"]
        heir = s.scalar(select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == remaining[0]["user_id"]))
        if heir is not None:
            heir.role = "owner"
    s.commit()
    return True


def team_invite_link(team: Team, bot_username: str) -> str | None:
    """The link to hand to somebody, or None when the bot has no @name."""
    if not bot_username:
        return None
    return f"https://t.me/{bot_username}?start=team_{team.code}"


# --- Team tasks -------------------------------------------------------------

def add_team_task(s: Session, user_id: int, team_id: int, title: str, *,
                  deadline: date | None = None, priority: str = "medium",
                  description: str = "", due_time: dtime | None = None,
                  remind_before: int | None = None,
                  recurrence: str | None = None,
                  project_id: int | None = None) -> dict:
    """Put a task in front of the whole team.

    Takes everything a personal task takes. A shared task that could not
    carry a time or a reminder would be the weaker of the two kinds, which is
    backwards: the reason to put something in a team is that it matters more.
    """
    _require_team(s, user_id, team_id)
    title = title.strip()[:300]
    if not title:
        raise ValueError("empty_title")
    if priority not in PRIORITIES:
        priority = "medium"
    rule = clean_recurrence(recurrence)
    project = _team_project_or_none(s, team_id, project_id)
    task = TeamTask(team_id=team_id, title=title, project_id=project,
                    description=(description or "").strip()[:2000],
                    deadline=deadline, due_time=due_time,
                    remind_before=clean_remind_before(remind_before),
                    recurrence=rule or None,
                    anchor_day=(deadline.day if rule == "monthly" and deadline
                                else None),
                    priority=priority, created_by=user_id)
    s.add(task)
    s.commit()
    return team_task_row(s, task, user_id)


def team_task_row(s: Session, task: TeamTask, viewer_id: int) -> dict:
    """One task, plus who has finished it — including the person looking."""
    done_by = {uid for uid, in s.execute(
        select(TeamTaskDone.user_id).where(
            TeamTaskDone.task_id == task.id,
            TeamTaskDone.done.is_(True))).all()}
    return {
        "id": task.id, "team_id": task.team_id, "title": task.title,
        "description": task.description or "",
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "due_time": task.due_time.strftime("%H:%M") if task.due_time else None,
        "remind_before": task.remind_before,
        "recurrence": task.recurrence,
        "priority": task.priority, "created_by": task.created_by,
        "project_id": task.project_id,
        "project": (s.scalar(select(Project.name)
                             .where(Project.id == task.project_id))
                    if task.project_id else None),
        "done": viewer_id in done_by,
        "done_by": sorted(done_by),
        "done_count": len(done_by),
    }


def list_team_tasks(s: Session, user_id: int, team_id: int, *,
                    day: date | None = None, horizon_days: int = 7,
                    project_id: int | None = None,
                    tz: ZoneInfo | None = None) -> list[dict]:
    """The team's open tasks, each carrying this viewer's own done state."""
    _require_team(s, user_id, team_id)
    today = day or today_local(tz)
    limit = today + timedelta(days=horizon_days)
    stmt = select(TeamTask).where(TeamTask.team_id == team_id,
                                  TeamTask.archived_at.is_(None))
    if project_id is not None:
        # Filing is the question here, not the calendar: a project's tasks are
        # all of them, not only the ones due this week.
        stmt = stmt.where(TeamTask.project_id == project_id)
    else:
        stmt = stmt.where(or_(TeamTask.deadline.is_(None),
                              TeamTask.deadline <= limit))
    tasks = s.scalars(stmt.order_by(
        TeamTask.deadline.is_(None), TeamTask.deadline, TeamTask.id)).all()
    return [team_task_row(s, t, user_id) for t in tasks]


def toggle_team_task(s: Session, user_id: int, task_id: int, *,
                     day: date | None = None,
                     tz: ZoneInfo | None = None) -> bool:
    """Tick or untick a team task **for the person asking**, and only them.

    There is no argument for whose completion to write, and that is the point:
    a member can record their own share and nobody else's.
    """
    task = s.get(TeamTask, task_id)
    if task is None or task.archived_at is not None:
        raise ValueError("unknown_task")
    _require_team(s, user_id, task.team_id)

    row = s.scalar(select(TeamTaskDone).where(
        TeamTaskDone.task_id == task_id, TeamTaskDone.user_id == user_id))
    if row is not None:
        # Flipped, not deleted: the row also remembers whether this member has
        # been reminded, and unticking a task is not a reason to forget that.
        row.done = not row.done
        row.done_at = utcnow()
        row.day = day or today_local(tz)
        s.commit()
        return bool(row.done)

    s.add(TeamTaskDone(task_id=task_id, user_id=user_id, done=True,
                       day=day or today_local(tz), done_at=utcnow()))
    try:
        with s.begin_nested():
            s.flush()
    except IntegrityError:
        s.rollback()
        return True
    s.commit()
    return True


def archive_team_task(s: Session, user_id: int, task_id: int) -> bool:
    """Take a task off the team's list. Either member may."""
    task = s.get(TeamTask, task_id)
    if task is None or task.archived_at is not None:
        return False
    _require_team(s, user_id, task.team_id)
    task.archived_at = utcnow()
    s.commit()
    return True


# --- Team habits ------------------------------------------------------------

def add_team_habit(s: Session, user_id: int, team_id: int, name: str, *,
                   schedule: str | None = None,
                   category: str = "non_negotiable",
                   target_time: dtime | None = None,
                   remind_at: dtime | None = None) -> dict:
    """A habit the team keeps together, with the same settings a private one has."""
    _require_team(s, user_id, team_id)
    name = name.strip()[:120]
    if not name:
        raise ValueError("empty_name")
    if category not in HABIT_CATEGORIES:
        category = "non_negotiable"
    position = (s.scalar(select(func.max(TeamHabit.position))
                         .where(TeamHabit.team_id == team_id)) or 0) + 1
    habit = TeamHabit(team_id=team_id, name=name,
                      schedule=clean_schedule(schedule), category=category,
                      target_time=target_time, remind_at=remind_at,
                      position=position, created_by=user_id)
    s.add(habit)
    s.commit()
    return team_habit_row(habit, user_id, set())


def team_habit_row(habit: TeamHabit, viewer_id: int, done_by: set) -> dict:
    """One team habit, shaped exactly like a personal one on the wire."""
    return {"id": habit.id, "name": habit.name, "category": habit.category,
            "schedule": clean_schedule(habit.schedule),
            "target_time": (habit.target_time.strftime("%H:%M")
                            if habit.target_time else None),
            "remind_at": (habit.remind_at.strftime("%H:%M")
                          if habit.remind_at else None),
            "system_key": habit.system_key or "",
            "protected": bool(habit.is_protected),
            "paused": habit.paused_at is not None,
            # `list_team_habits` only ever returns habits that are owed
            # today, so the flag the screens read is true by construction.
            # Without it every shared row rendered as "not today", greyed out.
            "due": True,
            "done": viewer_id in done_by,
            "done_by": sorted(done_by), "done_count": len(done_by)}


def edit_team_habit(s: Session, user_id: int, habit_id: int, **fields) -> dict:
    """Change a shared habit. Either member may; the rituals keep their name."""
    habit = s.get(TeamHabit, habit_id)
    if habit is None or habit.archived_at is not None:
        raise ValueError("unknown_habit")
    _require_team(s, user_id, habit.team_id)

    if "name" in fields and fields["name"]:
        if habit.is_protected:
            # Renaming "5x namoz" out from under the other person is not a
            # decision one of them makes alone.
            raise ValueError("protected")
        habit.name = str(fields["name"]).strip()[:120]
    if fields.get("category") in HABIT_CATEGORIES:
        habit.category = fields["category"]
    if "schedule" in fields:
        habit.schedule = clean_schedule(fields["schedule"])
    for key in ("target_time", "remind_at"):
        if key in fields:
            setattr(habit, key, fields[key])
    if "paused" in fields:
        habit.paused_at = utcnow() if fields["paused"] else None
    s.commit()
    return team_habit_row(habit, user_id, set())


def list_team_habits(s: Session, user_id: int, team_id: int, *,
                     day: date | None = None,
                     tz: ZoneInfo | None = None) -> list[dict]:
    """Today's team habits, each with this viewer's own tick and everyone's."""
    _require_team(s, user_id, team_id)
    today = day or today_local(tz)
    habits = s.scalars(
        select(TeamHabit)
        .where(TeamHabit.team_id == team_id, TeamHabit.archived_at.is_(None))
        .order_by(TeamHabit.position, TeamHabit.id)
    ).all()

    logs = s.execute(
        select(TeamHabitLog.habit_id, TeamHabitLog.user_id)
        .where(TeamHabitLog.day == today, TeamHabitLog.done.is_(True),
               TeamHabitLog.habit_id.in_([h.id for h in habits] or [0]))
    ).all()
    done_map: dict[int, set[int]] = {}
    for habit_id, member_id in logs:
        done_map.setdefault(habit_id, set()).add(member_id)

    return [team_habit_row(h, user_id, done_map.get(h.id, set()))
            for h in habits if team_habit_is_due(h, today)]


def team_habit_is_due(habit: TeamHabit, day: date) -> bool:
    """Whether the team expects this habit on that day.

    A paused habit is never due — the same rule a personal one follows, and
    the same reason: a Tuesday score must not drop for a session nobody
    planned.
    """
    if habit.paused_at is not None:
        return False
    return day.weekday() in schedule_days(habit.schedule)


def toggle_team_habit(s: Session, user_id: int, habit_id: int, *,
                      day: date | None = None,
                      tz: ZoneInfo | None = None) -> bool:
    """Tick today's team habit for the person asking, and only them."""
    habit = s.get(TeamHabit, habit_id)
    if habit is None or habit.archived_at is not None:
        raise ValueError("unknown_habit")
    _require_team(s, user_id, habit.team_id)
    today = day or today_local(tz)

    row = s.scalar(select(TeamHabitLog).where(
        TeamHabitLog.habit_id == habit_id, TeamHabitLog.user_id == user_id,
        TeamHabitLog.day == today))
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
    if habit.is_protected:
        # One member deleting "namoz" for both of them is not a decision
        # either of them made together.
        raise ValueError("protected")
    habit.archived_at = utcnow()
    s.commit()
    return True


# --- What the team did today ------------------------------------------------

def _existed_on(created_at: datetime | None, day: date,
                tz: ZoneInfo | None = None) -> bool:
    """Whether something had been created by the end of that local day.

    The denominator of any backwards-looking number depends on this. An item
    added today was not owed last Tuesday, and counting it as missed then is
    the difference between a statistic and a discouragement.
    """
    if created_at is None:
        return True
    born = local_date_of(created_at, tz)
    return born is None or born <= day


def team_day_summary(s: Session, team_id: int, day: date | None = None, *,
                     tz: ZoneInfo | None = None) -> dict:
    """One day of a team, per member.

    The shape the reports need: what the team set itself, and how far each
    person got with their own half of it. Deliberately per-member rather than
    one combined number — "we are at 60%" hides which of you is carrying it,
    and the whole reason two people share a list is to see each other's
    progress.
    """
    today = day or today_local(tz)
    team = s.get(Team, team_id)
    if team is None:
        return {}

    members = team_members(s, team_id)
    ids = [m["user_id"] for m in members]

    # Only what existed on the day being counted. Without this the month
    # denominator included every day *before* the team was made — a pair who
    # started yesterday read "1% · 2/154" and reasonably concluded the number
    # was broken. A day you were not there for is not a day you missed.
    tasks = [t for t in s.scalars(
        select(TeamTask)
        .where(TeamTask.team_id == team_id, TeamTask.archived_at.is_(None),
               or_(TeamTask.deadline.is_(None), TeamTask.deadline <= today))
        .order_by(TeamTask.deadline.is_(None), TeamTask.deadline, TeamTask.id)
    ).all() if _existed_on(t.created_at, today, tz)]
    task_ids = [t.id for t in tasks]
    done_rows = s.execute(select(TeamTaskDone.task_id, TeamTaskDone.user_id)
                          .where(TeamTaskDone.done.is_(True),
                                 TeamTaskDone.task_id.in_(task_ids or [0]))).all()
    task_done: dict[int, set[int]] = {}
    for task_id, member_id in done_rows:
        task_done.setdefault(task_id, set()).add(member_id)

    habits = [h for h in s.scalars(
        select(TeamHabit)
        .where(TeamHabit.team_id == team_id, TeamHabit.archived_at.is_(None))
        .order_by(TeamHabit.position, TeamHabit.id)).all()
        if team_habit_is_due(h, today) and _existed_on(h.created_at, today, tz)]
    habit_ids = [h.id for h in habits]
    habit_rows = s.execute(
        select(TeamHabitLog.habit_id, TeamHabitLog.user_id)
        .where(TeamHabitLog.day == today, TeamHabitLog.done.is_(True),
               TeamHabitLog.habit_id.in_(habit_ids or [0]))).all()
    habit_done: dict[int, set[int]] = {}
    for habit_id, member_id in habit_rows:
        habit_done.setdefault(habit_id, set()).add(member_id)

    total = len(tasks) + len(habits)
    people = []
    for member in members:
        uid = member["user_id"]
        tasks_done = sum(1 for t in tasks if uid in task_done.get(t.id, ()))
        habits_done = sum(1 for h in habits if uid in habit_done.get(h.id, ()))
        got = tasks_done + habits_done
        people.append({
            "user_id": uid, "name": member["name"],
            "tasks_done": tasks_done, "tasks_total": len(tasks),
            "habits_done": habits_done, "habits_total": len(habits),
            "done": got, "total": total,
            # None rather than 0 when the team set itself nothing: an empty
            # day is unmeasured, not a failure, exactly as it is personally.
            "percent": round(got / total * 100) if total else None,
        })

    return {
        "team_id": team_id, "name": team.name, "date": today.isoformat(),
        "members": people, "member_ids": ids,
        "tasks": [{"id": t.id, "title": t.title, "priority": t.priority,
                   "deadline": t.deadline.isoformat() if t.deadline else None,
                   "done_by": sorted(task_done.get(t.id, ())),
                   "done_count": len(task_done.get(t.id, ()))}
                  for t in tasks],
        "habits": [{"id": h.id, "name": h.name,
                    "done_by": sorted(habit_done.get(h.id, ())),
                    "done_count": len(habit_done.get(h.id, ()))}
                   for h in habits],
        "total": total,
    }


def team_summaries_for(s: Session, user_id: int, day: date | None = None, *,
                       tz: ZoneInfo | None = None) -> list[dict]:
    """Every team this user is in, summarised for their own day.

    The day is the *reader's* local day: two people in different zones each
    get the summary of the day they are living in, which is the same rule the
    personal reports follow.
    """
    return [team_day_summary(s, team.id, day, tz=tz)
            for team in teams_for(s, user_id)]


def team_items_for_day(s: Session, user_id: int, day: date | None = None, *,
                       tz: ZoneInfo | None = None) -> dict:
    """Every team task and habit this user has today, across all their teams.

    Returned as its own block rather than mixed into the workspace lists, and
    that separation is the whole design. The personal screens *show* shared
    work — a plan that hides half of what you owe today is not a plan — but
    the personal score is built from `workspace_id` alone and never sees these
    rows. So the two numbers stay honest: your own percentage measures what
    you set yourself, and the team's measures what the two of you set
    together. Merging them would mean a quiet evening for the team dragging
    down a day you personally finished, and neither number would mean
    anything afterwards.

    Each row carries the team it came from, so the surface showing it can say
    whose work it is without a second lookup.
    """
    today = day or today_local(tz)
    tasks: list[dict] = []
    habits: list[dict] = []
    for team in teams_for(s, user_id):
        for row in list_team_tasks(s, user_id, team.id, day=today, tz=tz):
            tasks.append({**row, "source": "team",
                          "team_id": team.id, "team_name": team.name})
        for row in list_team_habits(s, user_id, team.id, day=today, tz=tz):
            habits.append({**row, "source": "team",
                           "team_id": team.id, "team_name": team.name})
    return {"tasks": tasks, "habits": habits,
            "teams": [{"id": t.id, "name": t.name} for t in teams_for(s, user_id)]}


# --- Team statistics --------------------------------------------------------

def team_stats(s: Session, user_id: int, team_id: int, *, period: str = "week",
               tz: ZoneInfo | None = None) -> dict:
    """A team's record over a period, per member and side by side.

    The comparison is the feature. A personal chart answers "am I keeping
    this up"; a shared one answers "are we", and the only honest way to show
    that is both lines, not an average that hides one person having carried
    the week. Deliberately not framed as a competition — no winner, no
    ranking — because the people using this are on the same side.
    """
    _require_team(s, user_id, team_id)
    days = {"week": 7, "month": 30, "year": 365}.get(period, 7)
    today = today_local(tz)
    start = today - timedelta(days=days - 1)

    members = team_members(s, team_id)
    ids = [m["user_id"] for m in members]

    tasks = s.scalars(select(TeamTask).where(
        TeamTask.team_id == team_id, TeamTask.archived_at.is_(None),
        TeamTask.deadline.is_not(None),
        TeamTask.deadline >= start, TeamTask.deadline <= today)).all()
    habit_born = {}
    task_done = {}
    for task_id, member_id in s.execute(
            select(TeamTaskDone.task_id, TeamTaskDone.user_id)
            .where(TeamTaskDone.done.is_(True),
                   TeamTaskDone.task_id.in_([t.id for t in tasks] or [0]))).all():
        task_done.setdefault(task_id, set()).add(member_id)

    habits = s.scalars(select(TeamHabit).where(
        TeamHabit.team_id == team_id, TeamHabit.archived_at.is_(None))).all()
    habit_done: dict[tuple, set] = {}
    for habit_id, member_id, day in s.execute(
            select(TeamHabitLog.habit_id, TeamHabitLog.user_id, TeamHabitLog.day)
            .where(TeamHabitLog.day >= start, TeamHabitLog.day <= today,
                   TeamHabitLog.done.is_(True),
                   TeamHabitLog.habit_id.in_([h.id for h in habits] or [0]))).all():
        habit_done.setdefault((habit_id, day), set()).add(member_id)

    series, totals = [], {uid: {"done": 0, "total": 0} for uid in ids}
    for offset in range(days):
        day = start + timedelta(days=offset)
        owed = ([t for t in tasks
                 if t.deadline == day and _existed_on(t.created_at, day, tz)]
                + [h for h in habits if team_habit_is_due(h, day)
                   and _existed_on(h.created_at, day, tz)])
        point = {"date": day.isoformat(), "label": day.strftime("%d.%m")}
        for uid in ids:
            got = 0
            for item in owed:
                if isinstance(item, TeamTask):
                    got += uid in task_done.get(item.id, ())
                else:
                    got += uid in habit_done.get((item.id, day), ())
            point[str(uid)] = round(got / len(owed) * 100) if owed else None
            totals[uid]["done"] += got
            totals[uid]["total"] += len(owed)
        series.append(point)

    people = []
    for member in members:
        uid = member["user_id"]
        done, total = totals[uid]["done"], totals[uid]["total"]
        people.append({
            "user_id": uid, "name": member["name"],
            "done": done, "total": total,
            "percent": round(done / total * 100) if total else None,
            "streak": team_streak(s, team_id, uid, today, tz=tz),
        })

    return {"team_id": team_id, "period": period, "days": days,
            "from": start.isoformat(), "to": today.isoformat(),
            "series": series, "members": people,
            # One number for the pair: what the two of you managed between
            # you, out of everything the two of you owed.
            "together": (round(sum(p["done"] for p in people)
                               / sum(p["total"] for p in people) * 100)
                         if any(p["total"] for p in people) else None)}


def team_streak(s: Session, team_id: int, member_id: int, today: date, *,
                tz: ZoneInfo | None = None, horizon: int = 400) -> int:
    """Consecutive days this member cleared everything the team owed.

    Today does not break it while it is still running, the same rule a
    personal streak follows: a day is only a miss once it is over.
    """
    habits = s.scalars(select(TeamHabit).where(
        TeamHabit.team_id == team_id, TeamHabit.archived_at.is_(None))).all()
    if not habits:
        return 0
    start = today - timedelta(days=horizon)
    done = {}
    for habit_id, day in s.execute(
            select(TeamHabitLog.habit_id, TeamHabitLog.day)
            .where(TeamHabitLog.user_id == member_id, TeamHabitLog.day >= start,
                   TeamHabitLog.done.is_(True))).all():
        done.setdefault(day, set()).add(habit_id)

    streak, cursor = 0, today
    for _ in range(horizon):
        owed = {h.id for h in habits if team_habit_is_due(h, cursor)}
        if owed:
            if not owed <= done.get(cursor, set()):
                if cursor == today:
                    cursor -= timedelta(days=1)
                    continue
                break
            streak += 1
        cursor -= timedelta(days=1)
    return streak


# --- Team reminders ---------------------------------------------------------
#
# Shared work is reminded exactly as private work is, and per member: two
# people in different zones owe the same habit at different moments, and one
# of them being told must never silence the other. That is why the marker
# lives on the per-member row rather than on the item.

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
        tasks = s.scalars(select(TeamTask).where(
            TeamTask.team_id == team.id, TeamTask.archived_at.is_(None),
            TeamTask.deadline == today,
            TeamTask.remind_before.is_not(None))).all()
        if not tasks:
            continue
        state = {row.task_id: row for row in s.scalars(select(TeamTaskDone).where(
            TeamTaskDone.user_id == user_id,
            TeamTaskDone.task_id.in_([t.id for t in tasks]))).all()}
        for task in tasks:
            mine = state.get(task.id)
            if mine is not None and (mine.done or mine.reminder_sent_at):
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
        habits = [h for h in s.scalars(select(TeamHabit).where(
            TeamHabit.team_id == team.id,
            TeamHabit.archived_at.is_(None),
            TeamHabit.remind_at.is_not(None))).all()
            if team_habit_is_due(h, today)]
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
    """Everybody in the team but one — who to tell when something changes."""
    return [m["user_id"] for m in team_members(s, team_id)
            if m["user_id"] != except_user]


def team_habit_history(s: Session, user_id: int, habit_id: int, *,
                       days: int = 30, tz: ZoneInfo | None = None) -> dict:
    """A shared habit's record — the reader's own, and everybody else's.

    Deliberately the same shape a personal habit's history has, key for key,
    so the sheet that draws one can draw the other without a second layout.
    The one addition is `members`: the whole reason to keep a habit with
    somebody is to see how they are getting on with it, and a shared habit
    that only showed your own grid would be a private habit with a label.
    """
    habit = s.get(TeamHabit, habit_id)
    if habit is None or habit.archived_at is not None:
        raise ValueError("unknown_habit")
    team = _require_team(s, user_id, habit.team_id)

    today = today_local(tz)
    start = today - timedelta(days=days - 1)
    horizon_start = today - timedelta(days=STREAK_HORIZON)

    rows = s.execute(select(TeamHabitLog.user_id, TeamHabitLog.day).where(
        TeamHabitLog.habit_id == habit_id, TeamHabitLog.done.is_(True),
        TeamHabitLog.day >= horizon_start)).all()
    by_member: dict[int, set] = {}
    for member_id, day in rows:
        by_member.setdefault(member_id, set()).add(day)

    def record(member_id: int) -> dict:
        done_days = by_member.get(member_id, set())
        grid, due_count, done_count = [], 0, 0
        for offset in range(days):
            day = start + timedelta(days=offset)
            due = team_habit_is_due(habit, day)
            done = day in done_days
            if due:
                due_count += 1
                done_count += int(done)
            grid.append({"day": day.isoformat(), "due": due, "done": done})

        last7 = [g for g in grid[-7:] if g["due"]]

        streak, cursor, guard = 0, today, 0
        if team_habit_is_due(habit, today) and today not in done_days:
            cursor = today - timedelta(days=1)
        while guard < STREAK_HORIZON:
            guard += 1
            if team_habit_is_due(habit, cursor):
                if cursor not in done_days:
                    break
                streak += 1
            cursor -= timedelta(days=1)

        return {
            "streak": streak, "grid": grid,
            "last7_done": sum(1 for g in last7 if g["done"]),
            "last7_due": len(last7),
            "last30_done": done_count, "last30_due": due_count,
            "percent": round(done_count / due_count * 100) if due_count else 0,
            "done_today": today in done_days,
        }

    mine = record(user_id)
    members = [{**m, "is_you": m["user_id"] == user_id,
                **record(m["user_id"])}
               for m in team_members(s, habit.team_id)]

    return {
        "id": habit.id, "name": habit.name, "category": habit.category,
        "schedule": clean_schedule(habit.schedule),
        "days": schedule_days(habit.schedule),
        "paused": habit.paused_at is not None,
        "protected": bool(habit.is_protected),
        "system_key": habit.system_key or "",
        "target_time": (habit.target_time.strftime("%H:%M")
                        if habit.target_time else None),
        "remind_at": (habit.remind_at.strftime("%H:%M")
                      if habit.remind_at else None),
        "source": "team", "team_id": team.id, "team_name": team.name,
        **mine,
        "members": members,
    }


def move_habit(s: Session, user_id: int, *, habit_id: int | None = None,
               team_habit_id: int | None = None,
               to_team: int | None = None) -> dict:
    """Move a habit between a private list and a shared one, keeping its days.

    The logs come with it. A habit moved from "mine" to "ours" that lost its
    streak would be a habit nobody moves, and the streak is usually the reason
    somebody wants it shared in the first place.

    Moving a shared habit into a private list removes it from the other
    member, which is a real decision rather than a tidy-up — the caller is
    expected to announce it.
    """
    owner_ws = workspace_id_for(s, user_id)

    if habit_id is not None:
        if to_team is None:
            raise ValueError("no_destination")
        habit = _owned_habit(s, owner_ws, habit_id)
        if habit.is_protected:
            # The derived rituals are written by the prayer and journal
            # modules; moving one would leave those writing to nothing.
            raise ValueError("protected")
        _require_team(s, user_id, to_team)

        position = (s.scalar(select(func.max(TeamHabit.position))
                             .where(TeamHabit.team_id == to_team)) or 0) + 1
        moved = TeamHabit(team_id=to_team, name=habit.name,
                          category=habit.category, schedule=habit.schedule,
                          target_time=habit.target_time,
                          remind_at=habit.remind_at, position=position,
                          created_by=user_id)
        s.add(moved)
        s.flush()
        for log in s.scalars(select(HabitLog).where(
                HabitLog.habit_id == habit.id)).all():
            s.add(TeamHabitLog(habit_id=moved.id, user_id=user_id,
                               day=log.day, done=log.done,
                               logged_at=log.logged_at))
        habit.archived_at = utcnow()
        s.commit()
        return team_habit_row(moved, user_id, set())

    if team_habit_id is None:
        raise ValueError("nothing_to_move")

    shared = s.get(TeamHabit, team_habit_id)
    if shared is None or shared.archived_at is not None:
        raise ValueError("unknown_habit")
    _require_team(s, user_id, shared.team_id)
    if shared.is_protected:
        raise ValueError("protected")

    top = s.scalar(select(func.max(Habit.position))
                   .where(Habit.workspace_id == owner_ws)) or 0
    habit = Habit(workspace_id=owner_ws, name=shared.name,
                  category=shared.category, schedule=shared.schedule,
                  target_time=shared.target_time, remind_at=shared.remind_at,
                  position=top + 1)
    s.add(habit)
    s.flush()
    for log in s.scalars(select(TeamHabitLog).where(
            TeamHabitLog.habit_id == shared.id,
            TeamHabitLog.user_id == user_id)).all():
        s.add(HabitLog(workspace_id=owner_ws, habit_id=habit.id, day=log.day,
                       done=log.done, logged_at=log.logged_at))
    shared.archived_at = utcnow()
    s.commit()
    return {"id": habit.id, "name": habit.name, "source": "personal"}


# --- Team projects ----------------------------------------------------------

def list_team_projects(s: Session, user_id: int, team_id: int, *,
                       include_archived: bool = False) -> list[dict]:
    """The team's own shelves, with how far each has got.

    The same object a personal project is — one table, one set of rules — so
    a shared task can be filed exactly as a private one is, and the screen
    that draws a project does not need to know which kind it is looking at.
    """
    _require_team(s, user_id, team_id)
    stmt = select(Project).where(Project.team_id == team_id)
    if not include_archived:
        stmt = stmt.where(Project.archived_at.is_(None))

    out = []
    for project in s.scalars(stmt.order_by(Project.status,
                                           Project.created_at)).all():
        total = s.scalar(select(func.count(TeamTask.id)).where(
            TeamTask.project_id == project.id,
            TeamTask.archived_at.is_(None))) or 0
        done = s.scalar(select(func.count(func.distinct(TeamTask.id)))
                        .select_from(TeamTask)
                        .join(TeamTaskDone, TeamTaskDone.task_id == TeamTask.id)
                        .where(TeamTask.project_id == project.id,
                               TeamTask.archived_at.is_(None),
                               TeamTaskDone.user_id == user_id,
                               TeamTaskDone.done.is_(True))) or 0
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
                      deadline=deadline)
    s.add(project)
    s.commit()
    return {"id": project.id, "name": project.name, "team_id": team_id,
            "source": "team", "status": project.status,
            "tasks_total": 0, "tasks_done": 0, "percent": 0}


def _team_project_or_none(s: Session, team_id: int,
                          project_id: int | None) -> int | None:
    """A project id, but only if it is this team's. Otherwise nothing.

    The same rule a personal task follows about its own workspace: filing a
    task onto somebody else's shelf is not a thing the API should make
    possible by passing a number.
    """
    if project_id is None:
        return None
    project = s.get(Project, project_id)
    if project is None or project.team_id != team_id:
        raise ValueError("unknown_project")
    return project.id


def team_scoreboard(s: Session, user_id: int, team_id: int, *,
                    tz: ZoneInfo | None = None) -> dict:
    """Day, week and month at once, plus what is still open and who did what.

    The Team screen answers a different question from the personal one. A
    person opens their own screen to decide what to do next; two people open
    the shared one to find out where they stand — so this is a report, not a
    worklist: three periods side by side, and for today an explicit list of
    what each of you has and has not done.
    """
    _require_team(s, user_id, team_id)
    today = today_local(tz)
    members = team_members(s, team_id)

    def window(first: date, last: date) -> dict:
        """Each member's done/total over an inclusive range of local days."""
        totals = {m["user_id"]: [0, 0] for m in members}
        cursor = first
        while cursor <= last:
            for row in team_day_summary(s, team_id, cursor, tz=tz)["members"]:
                totals[row["user_id"]][0] += row["done"]
                totals[row["user_id"]][1] += row["total"]
            cursor += timedelta(days=1)
        return totals

    periods = {}
    for label, days in (("day", 1), ("week", 7), ("month", 30)):
        start = today - timedelta(days=days - 1)
        totals = window(start, today)
        # The same length again, immediately before, so the number can be read
        # as a direction rather than only a level. A percentage on its own
        # says how today went; the change says whether things are going the
        # way you want, which is the question two people actually have.
        before = window(start - timedelta(days=days), start - timedelta(days=1))

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
                # None when there is nothing to compare with — a team's first
                # week has no "last week", and inventing +100% would be a lie
                # that flatters.
                "delta": (percent - previous
                          if percent is not None and previous is not None
                          else None),
            })
        periods[label] = rows

    # Today, item by item: the bit that turns a percentage into something you
    # can act on before the day is over.
    todays = team_day_summary(s, team_id, today, tz=tz)
    open_items, done_items = [], []
    for kind, rows in (("task", todays["tasks"]), ("habit", todays["habits"])):
        for row in rows:
            entry = {"kind": kind, "id": row["id"],
                     "title": row.get("title") or row.get("name"),
                     "done_by": row["done_by"],
                     "missing": [m["user_id"] for m in members
                                 if m["user_id"] not in row["done_by"]]}
            (done_items if not entry["missing"] else open_items).append(entry)

    return {"team_id": team_id, "date": today.isoformat(),
            "members": members, "periods": periods,
            "open": open_items, "done": done_items,
            "open_count": len(open_items), "done_count": len(done_items)}


def edit_team_task(s: Session, user_id: int, task_id: int, **fields) -> dict:
    """Change a shared task. Either member may, and everything is editable.

    The same field set a private task takes. A shared task that could be
    given a deadline but never moved, or a priority but never a time, would
    be the second-class kind — and the ones that matter most are exactly the
    ones people put in a team.
    """
    task = s.get(TeamTask, task_id)
    if task is None or task.archived_at is not None:
        raise ValueError("unknown_task")
    _require_team(s, user_id, task.team_id)

    if "title" in fields and fields["title"]:
        task.title = str(fields["title"]).strip()[:300]
    if "description" in fields:
        task.description = (fields["description"] or "").strip()[:2000]
    if "deadline" in fields:
        task.deadline = fields["deadline"]
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
                "done_by_names": [m["name"] for m in team_members(s, team.id)
                                  if m["user_id"] in row["done_by"]]})
    return row


def move_task(s: Session, user_id: int, *, task_id: int | None = None,
              team_task_id: int | None = None,
              to_team: int | None = None) -> dict:
    """Move a task between a private list and a shared one.

    Changing your mind about where something belongs is ordinary — "finish
    the maths" starts private and becomes something the two of you are doing,
    or the other way round. Recreating it by hand loses the deadline, the
    reminder, the repeat and the history, so nobody does it and the task just
    sits in the wrong place.

    A shared task carries no project across, and a private one drops its own:
    a project is a shelf inside one container, and the shelf does not move.
    """
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
                         priority=task.priority, created_by=user_id)
        s.add(moved)
        s.flush()
        if task.status == "done":
            s.add(TeamTaskDone(task_id=moved.id, user_id=user_id, done=True,
                               day=local_date_of(task.completed_at)
                               or today_local(), done_at=task.completed_at
                               or utcnow()))
        task.archived_at = utcnow()
        s.commit()
        return team_task_row(s, moved, user_id)

    if team_task_id is None:
        raise ValueError("nothing_to_move")

    shared = s.get(TeamTask, team_task_id)
    if shared is None or shared.archived_at is not None:
        raise ValueError("unknown_task")
    _require_team(s, user_id, shared.team_id)

    mine = s.scalar(select(TeamTaskDone).where(
        TeamTaskDone.task_id == shared.id, TeamTaskDone.user_id == user_id))
    task = Task(workspace_id=ws, title=shared.title,
                description=shared.description or "",
                deadline=shared.deadline, due_time=shared.due_time,
                remind_before=shared.remind_before,
                recurrence=shared.recurrence, anchor_day=shared.anchor_day,
                priority=shared.priority,
                status="done" if (mine and mine.done) else "waiting",
                completed_at=(mine.done_at if mine and mine.done else None))
    s.add(task)
    shared.archived_at = utcnow()
    s.commit()
    return {"id": task.id, "title": task.title, "source": "personal"}


def move_project(s: Session, user_id: int, project_id: int,
                 to_team: int | None) -> dict:
    """Move a project, and the tasks filed on it, between private and shared.

    The shelf and everything on it travel together — a project that arrived
    somewhere empty would be a folder, not a move.
    """
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
                             priority=task.priority, created_by=user_id)
            s.add(moved)
            s.flush()
            if task.status == "done":
                s.add(TeamTaskDone(task_id=moved.id, user_id=user_id,
                                   done=True, day=today_local(),
                                   done_at=task.completed_at or utcnow()))
            task.archived_at = utcnow()
        s.commit()
        return {"id": project.id, "name": project.name, "source": "team",
                "team_id": to_team, "moved_tasks": len(tasks)}

    # Shared -> private. Only for a project that is shared, and it takes it
    # away from the other member, so the caller announces it.
    if project.team_id is None:
        raise ValueError("already_private")
    _require_team(s, user_id, project.team_id)

    shared_tasks = s.scalars(select(TeamTask).where(
        TeamTask.project_id == project.id,
        TeamTask.archived_at.is_(None))).all()
    done_ids = set(s.scalars(select(TeamTaskDone.task_id).where(
        TeamTaskDone.user_id == user_id, TeamTaskDone.done.is_(True),
        TeamTaskDone.task_id.in_([t.id for t in shared_tasks] or [0]))).all())

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
    s.commit()
    return {"id": project.id, "name": project.name, "source": "personal",
            "moved_tasks": len(shared_tasks)}


# --- The day as two halves, and one number ---------------------------------

#: Where a percentage stops being one thing and starts being another. Used for
#: the colour a number is shown in, so the bands are stated once here rather
#: than guessed at in three places in the browser.
SCORE_BANDS = ((85, "great"), (65, "good"), (40, "fair"), (0, "low"))


def score_band(percent: int | None) -> str:
    """"great" / "good" / "fair" / "low", or "none" when unmeasured."""
    if percent is None:
        return "none"
    for floor, name in SCORE_BANDS:
        if percent >= floor:
            return name
    return "low"


def day_score(s: Session, ws: int, day: date | None = None, *,
              tz: ZoneInfo | None = None) -> dict:
    """The day as its two halves and the one number they make.

    Private work and shared work are scored apart and then averaged, rather
    than poured into one pool. The pool version let whichever side happened to
    have more items decide the whole day: a week with twelve private tasks and
    one shared habit read as a private score with a rounding error attached,
    which is not what somebody keeping a programme with another person means
    by "how did we do".

    Averaging two percentages gives each half the same say regardless of how
    many rows it holds. When one half is empty there is nothing to average and
    the other half simply is the day.
    """
    day = day or today_local(tz)
    personal_parts = overall_components(s, ws, day, tz=tz, include_team=False)
    personal = (weighted_overall(personal_parts)
                if any(v is not None for v in personal_parts.values()) else None)

    owner = workspace_owner(s, ws)
    shared_items = (due_team_habits(s, ws, day) + due_team_tasks(s, ws, day)
                    if owner else [])
    if shared_items:
        done = sum(1 for _, ok in shared_items if ok)
        team = round(done / len(shared_items) * 100)
    else:
        team = None

    present = [x for x in (personal, team) if x is not None]
    value = round(sum(present) / len(present)) if present else EMPTY_OVERALL

    return {"value": value, "personal": personal, "team": team,
            "band": score_band(value if present else None),
            "components": personal_parts,
            "team_items": len(shared_items),
            "team_done": sum(1 for _, ok in shared_items if ok)}
