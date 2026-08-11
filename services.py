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
from datetime import date, datetime, time as dtime, timedelta
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

TZ = ZoneInfo("Asia/Tashkent")


class NotFound(Exception):
    """The row does not exist inside the caller's workspace.

    Deliberately indistinguishable from "never existed": probing ids must not
    reveal whether another workspace owns that row.
    """


def today_local() -> date:
    return datetime.now(TZ).date()


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
    ("Deep flow", "target",         ""),
    ("Sport",     "target",         ""),
    ("Podcast",   "bonus",          ""),
    ("Read",      "bonus",          ""),
]

SYSTEM_PRAYER = "prayer"
SYSTEM_JOURNAL = "journal"      # legacy: archived by migration 0001
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

def list_habits(s: Session, ws: int, day: date | None = None) -> list[dict]:
    """Active habits with today's completion state, in display order."""
    day = day or today_local()
    habits = s.scalars(
        select(Habit)
        .where(Habit.workspace_id == ws, Habit.archived_at.is_(None))
        .order_by(Habit.position, Habit.id)
    ).all()
    if not habits:
        return []

    done_ids = set(s.scalars(
        select(HabitLog.habit_id).where(
            HabitLog.workspace_id == ws,
            HabitLog.day == day,
            HabitLog.done.is_(True),
        )
    ).all())

    return [{"id": h.id, "name": h.name, "category": h.category,
             "protected": h.is_protected, "system_key": h.system_key,
             "target_time": h.target_time.strftime("%H:%M") if h.target_time else None,
             "done": h.id in done_ids} for h in habits]


def habits_by_category(s: Session, ws: int, day: date | None = None) -> dict:
    """Habits grouped into the three tiers, preserving display order."""
    grouped: dict[str, list[dict]] = {c: [] for c in HABIT_CATEGORIES}
    for habit in list_habits(s, ws, day):
        grouped.setdefault(habit["category"], []).append(habit)
    return grouped


def add_habit(s: Session, ws: int, name: str, category: str = "target") -> Habit:
    name = name.strip()[:120]
    if not name:
        raise ValueError("empty habit name")
    if category not in HABIT_CATEGORIES:
        category = "target"
    top = s.scalar(select(func.max(Habit.position)).where(Habit.workspace_id == ws)) or 0
    habit = Habit(workspace_id=ws, name=name, category=category, position=top + 1)
    s.add(habit)
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
                 day: date | None = None) -> bool:
    """Flip today's completion. Returns the new state.

    The protected `5x namoz` habit is derived from prayer logs, so a manual
    toggle is refused rather than silently ignored.
    """
    habit = _owned_habit(s, ws, habit_id)
    if habit.is_protected:
        raise ValueError("protected")

    day = day or today_local()
    row = s.scalar(select(HabitLog).where(
        HabitLog.workspace_id == ws, HabitLog.habit_id == habit_id, HabitLog.day == day))
    if row is None:
        row = HabitLog(workspace_id=ws, habit_id=habit_id, day=day, done=True)
        s.add(row)
    else:
        row.done = not row.done
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


def mark_wakeup(s: Session, ws: int, now: datetime | None = None) -> dict:
    """Record that the user got up, if they said so in time.

    The rule: "turdim" counts until one hour after the target time. Saying it
    later, or not at all, leaves the habit undone for the day — that is the
    whole point of the habit.
    """
    habit = wake_habit(s, ws)
    if habit is None:
        raise NotFound("habit")

    now = now or datetime.now(TZ).replace(tzinfo=None)
    day = now.date()
    target = habit.target_time or DEFAULT_WAKE_TIME
    deadline = datetime.combine(day, target) + WAKE_GRACE
    in_time = now <= deadline

    row = s.scalar(select(HabitLog).where(
        HabitLog.workspace_id == ws, HabitLog.habit_id == habit.id,
        HabitLog.day == day))
    if row is None:
        s.add(HabitLog(workspace_id=ws, habit_id=habit.id, day=day, done=in_time))
    else:
        row.done = row.done or in_time
    s.commit()

    return {"done": in_time, "target": target.strftime("%H:%M"),
            "deadline": deadline.strftime("%H:%M"),
            "now": now.strftime("%H:%M")}


def habit_progress(s: Session, ws: int, day: date) -> tuple[int, int]:
    """(completed, total) active habits for a day."""
    total = s.scalar(select(func.count(Habit.id)).where(
        Habit.workspace_id == ws, Habit.archived_at.is_(None))) or 0
    done = s.scalar(select(func.count(HabitLog.id)).join(Habit).where(
        HabitLog.workspace_id == ws, HabitLog.day == day,
        HabitLog.done.is_(True), Habit.archived_at.is_(None))) or 0
    return done, total


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

PRAYER_DONE_THRESHOLD = 2.5
EXCUSED_SCORE = 2.5


def prayer_statuses_for(gender: str | None) -> list[str]:
    return STATUSES_FEMALE if gender == "female" else STATUSES_MALE


def prayer_score(statuses: dict[str, str], gender: str | None,
                 excused: bool = False) -> float:
    """Daily score from the five prayers.

    Female users have no jamaat/qaza; an excused day is worth exactly 2.5,
    which is the threshold, so the day counts as fulfilled without inventing
    five fake completed prayers.
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


def recalc_prayer_day(s: Session, ws: int, day: date, gender: str | None) -> float:
    """Recompute the day's score and sync the protected `5x namoz` habit."""
    rows = s.scalars(select(PrayerLog).where(
        PrayerLog.workspace_id == ws, PrayerLog.day == day)).all()
    statuses = {r.prayer: r.status for r in rows}

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
        done = score >= PRAYER_DONE_THRESHOLD
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
               gender: str | None, day: date | None = None) -> float:
    if prayer not in PRAYERS:
        raise ValueError("unknown prayer")
    if status not in prayer_statuses_for(gender):
        raise ValueError("status not allowed for this gender")

    day = day or today_local()
    row = s.scalar(select(PrayerLog).where(
        PrayerLog.workspace_id == ws, PrayerLog.day == day, PrayerLog.prayer == prayer))
    if row is None:
        s.add(PrayerLog(workspace_id=ws, day=day, prayer=prayer, status=status))
    else:
        row.status = status
    s.commit()
    return recalc_prayer_day(s, ws, day, gender)


def set_excused(s: Session, ws: int, excused: bool, gender: str | None,
                day: date | None = None) -> float:
    """Female-only day-level exemption."""
    if gender != "female":
        raise ValueError("excused is only available for female users")
    day = day or today_local()
    state = s.scalar(select(PrayerDay).where(
        PrayerDay.workspace_id == ws, PrayerDay.day == day))
    if state is None:
        s.add(PrayerDay(workspace_id=ws, day=day, excused=excused, score=0))
    else:
        state.excused = excused
    s.commit()
    return recalc_prayer_day(s, ws, day, gender)


def prayer_state(s: Session, ws: int, day: date, gender: str | None) -> dict:
    rows = s.scalars(select(PrayerLog).where(
        PrayerLog.workspace_id == ws, PrayerLog.day == day)).all()
    state = s.scalar(select(PrayerDay).where(
        PrayerDay.workspace_id == ws, PrayerDay.day == day))
    return {
        "day": day.isoformat(),
        "prayers": {p: next((r.status for r in rows if r.prayer == p), None)
                    for p in PRAYERS},
        "excused": bool(state and state.excused),
        "score": float(state.score) if state else 0.0,
        "threshold": PRAYER_DONE_THRESHOLD,
        "max": PRAYER_MAX_SCORE,
        "statuses": prayer_statuses_for(gender),
    }


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def list_projects(s: Session, ws: int) -> list[dict]:
    projects = s.scalars(
        select(Project)
        .where(Project.workspace_id == ws, Project.archived_at.is_(None))
        .order_by(Project.created_at.desc())
    ).all()

    out = []
    for p in projects:
        total = s.scalar(select(func.count(Task.id)).where(
            Task.workspace_id == ws, Task.project_id == p.id,
            Task.archived_at.is_(None))) or 0
        done = s.scalar(select(func.count(Task.id)).where(
            Task.workspace_id == ws, Task.project_id == p.id,
            Task.archived_at.is_(None), Task.status == "done")) or 0
        out.append({
            "id": p.id, "name": p.name, "description": p.description,
            "deadline": p.deadline.isoformat() if p.deadline else None,
            "status": p.status, "tasks_total": total, "tasks_done": done,
            "progress": round(done / total * 100) if total else 0,
        })
    return out


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


def _owned_project(s: Session, ws: int, project_id: int) -> Project:
    project = s.get(Project, project_id)
    if project is None or project.workspace_id != ws or project.archived_at:
        raise NotFound("project")
    return project


def update_project(s: Session, ws: int, project_id: int, **fields) -> Project:
    """Rename a project, or adjust its description and deadline.

    A project the user cannot rename is a typo they have to live with, or a
    reason to delete and recreate — which detaches every task inside it.
    """
    project = _owned_project(s, ws, project_id)
    if fields.get("name"):
        name = str(fields["name"]).strip()[:200]
        if not name:
            raise ValueError("empty project name")
        project.name = name
    if "description" in fields:
        project.description = str(fields["description"] or "").strip()[:2000]
    if "deadline" in fields:
        project.deadline = fields["deadline"]
    s.commit()
    return project


def project_tasks(s: Session, ws: int, project_id: int, *,
                  include_done: bool = True) -> list[dict]:
    """Everything inside one project, open work first."""
    _owned_project(s, ws, project_id)
    today = today_local()
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


def add_task(s: Session, ws: int, title: str, *, deadline: date | None = None,
             project_id: int | None = None, priority: str = "medium",
             description: str = "") -> Task:
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
                description=description.strip()[:4000])
    s.add(task)
    s.commit()
    return task


def _owned_task(s: Session, ws: int, task_id: int) -> Task:
    task = s.get(Task, task_id)
    if task is None or task.workspace_id != ws:
        raise NotFound("task")
    return task


def complete_task(s: Session, ws: int, task_id: int) -> Task:
    task = _owned_task(s, ws, task_id)
    task.status = "done"
    task.completed_at = utcnow()
    s.commit()
    return task


def reopen_task(s: Session, ws: int, task_id: int) -> Task:
    task = _owned_task(s, ws, task_id)
    task.status = "waiting"
    task.completed_at = None
    s.commit()
    return task


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
        task.status = fields["status"]
        task.completed_at = utcnow() if fields["status"] == "done" else None
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
        "days_left": days_left,
        "overdue": bool(task.deadline and task.deadline < today and task.status != "done"),
        "project_id": task.project_id, "project": project_name,
    }


def list_tasks(s: Session, ws: int, *, horizon_days: int = 7,
               include_done: bool = False) -> dict:
    """Tasks grouped for display: overdue, the next N days, and undated."""
    today = today_local()
    limit = today + timedelta(days=horizon_days)

    stmt = select(Task).where(Task.workspace_id == ws, Task.archived_at.is_(None))
    if not include_done:
        stmt = stmt.where(Task.status == "waiting")
    tasks = s.scalars(stmt.order_by(Task.deadline.is_(None), Task.deadline,
                                    Task.priority)).all()

    overdue, upcoming, undated = [], [], []
    for task in tasks:
        row = _task_dict(s, ws, task, today)
        if task.deadline is None:
            undated.append(row)
        elif task.deadline < today and task.status != "done":
            overdue.append(row)
        elif task.deadline <= limit:
            upcoming.append(row)
    return {"overdue": overdue, "upcoming": upcoming, "undated": undated}


def completed_tasks(s: Session, ws: int, limit: int = 100) -> list[dict]:
    """The Done archive. Completing a task moves it here instead of deleting it."""
    today = today_local()
    rows = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "done")
        .order_by(Task.completed_at.desc()).limit(limit)).all()
    return [_task_dict(s, ws, t, today) for t in rows]


def tasks_due_today(s: Session, ws: int) -> list[dict]:
    today = today_local()
    tasks = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline == today)).all()
    return [_task_dict(s, ws, t, today) for t in tasks]


def today_tasks_by_project(s: Session, ws: int) -> list[dict]:
    """Today's open tasks, grouped under the project they belong to.

    Home shows today and only today. A week's worth of rows is a backlog, and
    a backlog is what the user opens the app to escape.
    """
    groups: dict[int | None, dict] = {}
    for task in tasks_due_today(s, ws):
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

#: One mission a week. Three "primary" missions is no primary at all, which is
#: the decision this screen exists to make.
MAX_FOCUS = 1

MISSION_PRIORITIES = ["high", "medium", "low"]
DEFAULT_MISSION_PRIORITY = "medium"


def _focus_dict(row: WeeklyFocus) -> dict:
    return {"id": row.id, "slot": row.slot, "title": row.title,
            "priority": row.priority if row.priority in MISSION_PRIORITIES
                        else DEFAULT_MISSION_PRIORITY,
            "done": row.done}


def list_focus(s: Session, ws: int, when: date | None = None) -> list[dict]:
    start = week_start(when or today_local())
    rows = s.scalars(select(WeeklyFocus).where(
        WeeklyFocus.workspace_id == ws, WeeklyFocus.week_start == start)
        .order_by(WeeklyFocus.slot, WeeklyFocus.id)).all()
    return [_focus_dict(r) for r in rows]


def primary_focus(s: Session, ws: int, when: date | None = None) -> dict | None:
    """The one mission Home shows.

    Weeks written before the one-mission rule can still hold three rows. The
    lowest slot wins — deterministically, so every surface names the same
    mission — and the others stay in the database rather than being deleted
    behind the user's back.
    """
    rows = list_focus(s, ws, when)
    return rows[0] if rows else None


def add_focus(s: Session, ws: int, title: str, when: date | None = None, *,
              priority: str = DEFAULT_MISSION_PRIORITY) -> WeeklyFocus:
    title = title.strip()[:200]
    if not title:
        raise ValueError("empty focus title")
    if priority not in MISSION_PRIORITIES:
        priority = DEFAULT_MISSION_PRIORITY
    start = week_start(when or today_local())
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


def journal_is_complete(answers: dict) -> bool:
    return all(str(answers.get(key, "")).strip() for key in JOURNAL_KEYS)


def journal_done(s: Session, ws: int, day: date | None = None) -> bool:
    """Whether the day's journal is fully answered.

    This is a status the UI shows next to the habits, not a habit itself:
    counting it would change the denominator and the streak for something the
    user did not choose to track.
    """
    entry = get_journal(s, ws, day or today_local())
    return bool(entry and entry["complete"])


def get_journal(s: Session, ws: int, day: date | None = None) -> dict | None:
    day = day or today_local()
    row = s.scalar(select(JournalEntry).where(
        JournalEntry.workspace_id == ws, JournalEntry.day == day))
    if row is None:
        return None
    try:
        answers = json.loads(row.answers or "{}")
    except json.JSONDecodeError:
        answers = {}
    return {"day": row.day.isoformat(), "text": row.text, "mood": row.mood,
            "answers": answers, "complete": journal_is_complete(answers)}


def save_journal(s: Session, ws: int, *, answers: dict | None = None,
                 text: str = "", day: date | None = None,
                 mood: str = "") -> JournalEntry:
    """Save a day's answers.

    Completion is derived from the answers on read, so there is nothing to
    keep in sync here.
    """
    day = day or today_local()
    row = s.scalar(select(JournalEntry).where(
        JournalEntry.workspace_id == ws, JournalEntry.day == day))
    if row is None:
        row = JournalEntry(workspace_id=ws, day=day)
        s.add(row)

    if answers is not None:
        cleaned = {k: str(v).strip()[:2000] for k, v in answers.items()
                   if k in JOURNAL_KEYS}
        row.answers = json.dumps(cleaned, ensure_ascii=False)
        # Flat copy so search and exports stay simple.
        row.text = "\n\n".join(
            f"{q['uz']}\n{cleaned.get(q['id'], '')}".strip()
            for q in JOURNAL_QUESTIONS if cleaned.get(q["id"]))
    else:
        row.text = text.strip()

    if mood:
        row.mood = mood[:20]

    s.commit()
    return row


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
                    "complete": journal_is_complete(answers)})
    return out


def delete_journal(s: Session, ws: int, day: date) -> None:
    """Remove a day's entry. Journal completion is derived, so nothing else
    needs updating."""
    row = s.scalar(select(JournalEntry).where(
        JournalEntry.workspace_id == ws, JournalEntry.day == day))
    if row is None:
        raise NotFound("journal")
    s.delete(row)
    s.commit()


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


def list_birthdays(s: Session, ws: int, within_days: int = 30) -> list[dict]:
    today = today_local()
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


def habit_streak(s: Session, ws: int) -> int:
    """Consecutive days with every habit done.

    Today may still be incomplete without breaking the streak — the day is not
    over — so counting starts from yesterday when today is unfinished.
    """
    today = today_local()
    done, total = habit_progress(s, ws, today)
    cursor = today if (total and done == total) else today - timedelta(days=1)

    streak = 0
    for _ in range(365):
        done, total = habit_progress(s, ws, cursor)
        if not total or done < total:
            break
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def prayer_streak(s: Session, ws: int) -> int:
    """Consecutive days whose prayer score reached the completion threshold."""
    today = today_local()
    cursor = today
    if _prayer_score_for(s, ws, today) < PRAYER_DONE_THRESHOLD:
        cursor = today - timedelta(days=1)

    streak = 0
    for _ in range(365):
        if _prayer_score_for(s, ws, cursor) < PRAYER_DONE_THRESHOLD:
            break
        streak += 1
        cursor -= timedelta(days=1)
    return streak


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


def _range_percent(s: Session, ws: int, start: date, end: date) -> tuple[int, int]:
    """Average habit and prayer percentage across an inclusive day range."""
    days = (end - start).days + 1
    if days <= 0:
        return 0, 0
    habit_total = prayer_total = 0
    for offset in range(days):
        day = start + timedelta(days=offset)
        habit_total += _habit_percent(s, ws, day)
        prayer_total += _prayer_percent(s, ws, day)
    return round(habit_total / days), round(prayer_total / days)


def stats(s: Session, ws: int, period: str = "week") -> dict:
    """Series for the line charts, plus streaks and averages.

    `week`  — one point per day for the last 7 days.
    `month` — one point per day for the last 30 days.
    `year`  — one point per month for the last 12 months, because 365 daily
              points are unreadable on a phone.
    """
    today = today_local()

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
            habits, prayer = _range_percent(s, ws, start, end)
            series.append({"day": start.isoformat(),
                           "label": start.strftime("%m.%y"),
                           "habits": habits, "prayer": prayer})
        window_start = date(months[-1][0], months[-1][1], 1)
    else:
        days = 7 if period == "week" else 30
        series = []
        for offset in range(days - 1, -1, -1):
            day = today - timedelta(days=offset)
            series.append({
                "day": day.isoformat(),
                "label": day.strftime("%d.%m"),
                "habits": _habit_percent(s, ws, day),
                "prayer": _prayer_percent(s, ws, day),
            })
        window_start = today - timedelta(days=days - 1)

    prayer_rows = s.scalars(select(PrayerLog).where(
        PrayerLog.workspace_id == ws,
        PrayerLog.day >= window_start)).all()
    breakdown: dict[str, int] = {}
    for row in prayer_rows:
        breakdown[row.status] = breakdown.get(row.status, 0) + 1

    # The headline numbers are today's, from the same function Home uses, so
    # the two screens can never disagree. The series stays a period average.
    overall = overall_state(s, ws, today)
    components = overall["components"]
    prayer_today = prayer_state(s, ws, today, None)

    return {
        "period": period,
        "series": series,
        "habit_avg": round(sum(p["habits"] for p in series) / len(series)),
        "prayer_avg": round(sum(p["prayer"] for p in series) / len(series)),
        "habit_streak": habit_streak(s, ws),
        "prayer_streak": prayer_streak(s, ws),
        "prayer_breakdown": breakdown,
        "today": {
            "overall": overall["value"],
            "trend": overall["trend"],
            "tasks": components["tasks"],
            "habits": components["habits"],
            "prayer": components["prayer"],
            "prayer_score": prayer_today["score"],
            "prayer_max": PRAYER_MAX_SCORE,
            "streak": habit_streak(s, ws),
        },
    }


def stats_csv(s: Session, ws: int, period: str = "month") -> str:
    """The statistics view as CSV, for the download button.

    CSV rather than PDF: it opens in Excel, Numbers and Google Sheets without
    a viewer, and the file stays a few kilobytes.
    """
    data = stats(s, ws, period)
    lines = ["ErnestOS statistics"]
    lines.append(f"period,{period}")
    lines.append(f"generated,{datetime.now(TZ):%Y-%m-%d %H:%M}")
    lines.append("")
    lines.append(f"habit average %,{data['habit_avg']}")
    lines.append(f"prayer average %,{data['prayer_avg']}")
    lines.append(f"habit streak,{data['habit_streak']}")
    lines.append(f"prayer streak,{data['prayer_streak']}")
    lines.append("")
    lines.append("prayer status,count")
    for status, count in sorted(data["prayer_breakdown"].items()):
        lines.append(f"{status},{count}")
    lines.append("")
    lines.append("date,habits %,prayer %")
    for point in data["series"]:
        lines.append(f"{point['day']},{point['habits']},{point['prayer']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Calendar — one month of deadlines
# ---------------------------------------------------------------------------

def calendar_month(s: Session, ws: int, year: int, month: int) -> dict:
    """Every dated item inside one month, keyed by ISO date."""
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
            {"id": task.id, "status": task.status, "priority": task.priority})

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
            add(occurrence, "birthday", row.person_name, {"id": row.id})

    return {"year": year, "month": month,
            "first_weekday": first.weekday(), "days_in_month": last.day,
            "today": today_local().isoformat(), "events": events}


# ---------------------------------------------------------------------------
# Coming back after a break
# ---------------------------------------------------------------------------

#: A gap this long turns the app into a wall of failures on return.
BREAK_DAYS = 3


def break_state(s: Session, ws: int, user: User) -> dict:
    """Whether this user is returning from a gap, and what is waiting.

    Someone who has been away for a week should be met with one decision, not
    a backlog and a broken streak.
    """
    today = today_local()
    last_seen = (user.last_active_at.date() if user.last_active_at else today)
    away = (today - last_seen).days

    overdue = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline < today)) or 0

    return {"days_away": away, "overdue": overdue,
            "suggest_reset": away >= BREAK_DAYS and overdue > 0}


def fresh_start(s: Session, ws: int, *, mode: str = "today") -> int:
    """Clear the backlog in one move. Returns how many tasks were handled.

    `today`   — pull every overdue task to today, keep them all.
    `archive` — move them out of the way; they stay in the Done archive.
    """
    today = today_local()
    overdue = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline < today)).all()

    for task in overdue:
        if mode == "archive":
            task.archived_at = utcnow()
        else:
            task.deadline = today
    s.commit()
    return len(overdue)


# ---------------------------------------------------------------------------
# Weekly review
# ---------------------------------------------------------------------------

def weekly_review(s: Session, ws: int, user: User,
                  when: date | None = None) -> dict:
    """The week's numbers plus the three answers, ready to edit."""
    start = week_start(when or today_local())
    end = start + timedelta(days=6)

    done = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.status == "done",
        func.date(Task.completed_at) >= start,
        func.date(Task.completed_at) <= end)) or 0
    missed = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline < today_local())) or 0

    habit_pct, prayer_pct = _range_percent(s, ws, start, min(end, today_local()))
    focus = list_focus(s, ws, start)

    row = s.scalar(select(WeeklyReview).where(
        WeeklyReview.workspace_id == ws, WeeklyReview.week_start == start))

    return {
        "week_start": start.isoformat(),
        "tasks_done": done, "tasks_overdue": missed,
        "habit_pct": habit_pct, "prayer_pct": prayer_pct,
        "focus": focus,
        "focus_done": sum(1 for f in focus if f["done"]),
        "answers": {
            "went_well": row.went_well if row else "",
            "blocked": row.blocked if row else "",
            "next_focus": row.next_focus if row else "",
        },
        "saved": row is not None,
    }


def save_weekly_review(s: Session, ws: int, *, went_well: str = "",
                       blocked: str = "", next_focus: str = "",
                       when: date | None = None) -> WeeklyReview:
    start = week_start(when or today_local())
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

def home(s: Session, ws: int, user: User) -> dict:
    """Everything Home shows, and nothing else.

    Home answers one question — how is today going, and what is on it — so the
    payload carries exactly the fields on that screen. Project rollups, goal
    counts, birthdays and the full analytics history were all things the user
    had to read past before reaching the answer, and each one cost a query.
    """
    today = today_local()
    done, total = habit_progress(s, ws, today)
    prayer = prayer_state(s, ws, today, user.gender)

    return {
        "date": today.isoformat(),
        "date_label": date_label(today, user.language),
        "name": user.first_name or "",
        "quote": user.quote,
        "language": user.language,
        "theme": user.theme,
        "gender": user.gender,
        "photo_file_id": user.photo_file_id,
        "habits": {"done": done, "total": total},
        "prayer": {"score": prayer["score"], "max": PRAYER_MAX_SCORE,
                   "threshold": PRAYER_DONE_THRESHOLD,
                   "excused": prayer["excused"]},
        "streak": habit_streak(s, ws),
        "overall": overall_state(s, ws, today),
        # One mission, one day's tasks. Both are deliberately singular.
        "mission": primary_focus(s, ws),
        "tasks_today": today_tasks_by_project(s, ws),
        "journal_today": journal_done(s, ws, today),
    }


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
    """Yesterday's summary plus today's plan (the 04:00 report)."""
    today = today_local()
    yesterday = today - timedelta(days=1)

    y_done, y_total = habit_progress(s, ws, yesterday)
    y_prayer = prayer_state(s, ws, yesterday, user.gender)
    y_completed = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.status == "done",
        func.date(Task.completed_at) == yesterday)) or 0
    y_missed = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline == yesterday)) or 0

    tasks = list_tasks(s, ws, horizon_days=0)
    focus = list_focus(s, ws)

    return {
        "yesterday": {
            "date": yesterday.isoformat(),
            "habits_done": y_done, "habits_total": y_total,
            "prayer_score": y_prayer["score"],
            "tasks_completed": y_completed, "tasks_missed": y_missed,
            "journal": bool(get_journal(s, ws, yesterday)),
        },
        "today": {
            "date": today.isoformat(),
            "tasks": tasks_due_today(s, ws),
            "overdue": tasks["overdue"],
            "focus": focus,
            "focus_done": sum(1 for f in focus if f["done"]),
            "birthdays": [b for b in list_birthdays(s, ws, within_days=1)],
            "habits_total": y_total,
        },
    }


def evening_data(s: Session, ws: int, user: User) -> dict:
    """Today's progress so far (the 21:00 report). No next-day plan."""
    today = today_local()
    done, total = habit_progress(s, ws, today)
    prayer = prayer_state(s, ws, today, user.gender)

    completed = s.scalar(select(func.count(Task.id)).where(
        Task.workspace_id == ws, Task.status == "done",
        func.date(Task.completed_at) == today)) or 0

    remaining = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline == today)).all()
    overdue = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline < today)).all()

    habits = list_habits(s, ws, today)
    focus = list_focus(s, ws)

    return {
        "date": today.isoformat(),
        # The same number Home and Statistics show, from the same function.
        "overall": overall_state(s, ws, today),
        "habits_done": done, "habits_total": total,
        "habits_remaining": [h["name"] for h in habits if not h["done"]],
        "prayer_score": prayer["score"],
        "tasks_completed": completed,
        "tasks_remaining": [t.title for t in remaining],
        "tasks_overdue": [t.title for t in overdue],
        "focus": focus,
        "focus_done": sum(1 for f in focus if f["done"]),
        "journal": bool(get_journal(s, ws, today)),
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
    return {
        "total": total,
        "latest_member_no": latest,
        "onboarded": onboarded,
        "subscribed": subscribed,
        "blocked": max(onboarded - subscribed, 0),
        "dau": count(User, func.date(User.last_active_at) == today),
        "wau": count(User, User.last_active_at >= week_ago),
        "mau": count(User, User.last_active_at >= month_ago),
        "new_today": count(User, func.date(User.created_at) == today),
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
