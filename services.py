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

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db import (
    Birthday, DailyReportLog, Feedback, Goal, Habit, HabitLog, JournalEntry,
    PrayerDay, PrayerLog, Project, Task, User, WeeklyFocus, Workspace, utcnow,
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


# ---------------------------------------------------------------------------
# Users and workspaces
# ---------------------------------------------------------------------------

DEFAULT_HABITS = [
    ("Get up", False),
    ("5x namoz", True),      # protected: derived from prayer logs
    ("Deep flow", False),
    ("Sport", False),
    ("Podcast", False),
    ("Read", False),
]

PROTECTED_HABIT = "5x namoz"


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

    user = User(telegram_id=telegram_id, first_name=first_name or "",
                last_name=last_name or "", username=username or "")
    s.add(user)
    s.flush()

    workspace = Workspace(user_id=telegram_id)
    s.add(workspace)
    s.flush()

    for position, (name, protected) in enumerate(DEFAULT_HABITS, start=1):
        s.add(Habit(workspace_id=workspace.id, name=name,
                    position=position, is_protected=protected))
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

    return [{"id": h.id, "name": h.name, "protected": h.is_protected,
             "done": h.id in done_ids} for h in habits]


def add_habit(s: Session, ws: int, name: str) -> Habit:
    name = name.strip()[:120]
    if not name:
        raise ValueError("empty habit name")
    top = s.scalar(select(func.max(Habit.position)).where(Habit.workspace_id == ws)) or 0
    habit = Habit(workspace_id=ws, name=name, position=top + 1)
    s.add(habit)
    s.commit()
    return habit


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
STATUSES_FEMALE = ["on_time", "missed"]

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
        Habit.workspace_id == ws, Habit.is_protected.is_(True),
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


def add_task(s: Session, ws: int, title: str, *, deadline: date | None = None,
             project_id: int | None = None, priority: str = "medium") -> Task:
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
                project_id=project_id, priority=priority)
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
        "id": task.id, "title": task.title, "status": task.status,
        "priority": task.priority,
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


def tasks_due_today(s: Session, ws: int) -> list[dict]:
    today = today_local()
    tasks = s.scalars(select(Task).where(
        Task.workspace_id == ws, Task.archived_at.is_(None),
        Task.status == "waiting", Task.deadline == today)).all()
    return [_task_dict(s, ws, t, today) for t in tasks]


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

CATEGORIES = ["ultimate", "milestone", "tactical"]


def list_goals(s: Session, ws: int, *, include_completed: bool = True) -> dict:
    stmt = select(Goal).where(Goal.workspace_id == ws, Goal.archived_at.is_(None))
    if not include_completed:
        stmt = stmt.where(Goal.status == "active")
    goals = s.scalars(stmt.order_by(Goal.created_at.desc())).all()

    grouped: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
    for g in goals:
        grouped.setdefault(g.category, []).append({
            "id": g.id, "title": g.title, "description": g.description,
            "category": g.category, "status": g.status, "progress": g.progress,
            "target_date": g.target_date.isoformat() if g.target_date else None,
        })
    return grouped


def add_goal(s: Session, ws: int, title: str, category: str, *,
             description: str = "") -> Goal:
    title = title.strip()[:300]
    if not title:
        raise ValueError("empty goal title")
    if category not in CATEGORIES:
        raise ValueError("unknown category")
    goal = Goal(workspace_id=ws, title=title, category=category,
                description=description.strip()[:2000])
    s.add(goal)
    s.commit()
    return goal


def _owned_goal(s: Session, ws: int, goal_id: int) -> Goal:
    goal = s.get(Goal, goal_id)
    if goal is None or goal.workspace_id != ws:
        raise NotFound("goal")
    return goal


def complete_goal(s: Session, ws: int, goal_id: int) -> Goal:
    goal = _owned_goal(s, ws, goal_id)
    goal.status = "completed"
    goal.progress = 100
    goal.completed_at = utcnow()
    s.commit()
    return goal


def delete_goal(s: Session, ws: int, goal_id: int) -> str:
    goal = _owned_goal(s, ws, goal_id)
    goal.archived_at = utcnow()
    s.commit()
    return goal.title


def update_goal(s: Session, ws: int, goal_id: int, **fields) -> Goal:
    goal = _owned_goal(s, ws, goal_id)
    if fields.get("title"):
        goal.title = str(fields["title"]).strip()[:300]
    if "description" in fields:
        goal.description = str(fields["description"] or "").strip()[:2000]
    if fields.get("category") in CATEGORIES:
        goal.category = fields["category"]
    if "progress" in fields and fields["progress"] is not None:
        goal.progress = max(0, min(100, int(fields["progress"])))
    s.commit()
    return goal


# ---------------------------------------------------------------------------
# Weekly focus — at most three missions per week
# ---------------------------------------------------------------------------

MAX_FOCUS = 3


def list_focus(s: Session, ws: int, when: date | None = None) -> list[dict]:
    start = week_start(when or today_local())
    rows = s.scalars(select(WeeklyFocus).where(
        WeeklyFocus.workspace_id == ws, WeeklyFocus.week_start == start)
        .order_by(WeeklyFocus.slot)).all()
    return [{"id": r.id, "slot": r.slot, "title": r.title, "done": r.done} for r in rows]


def add_focus(s: Session, ws: int, title: str, when: date | None = None) -> WeeklyFocus:
    title = title.strip()[:200]
    if not title:
        raise ValueError("empty focus title")
    start = week_start(when or today_local())
    used = {r.slot for r in s.scalars(select(WeeklyFocus).where(
        WeeklyFocus.workspace_id == ws, WeeklyFocus.week_start == start)).all()}
    free = next((n for n in range(1, MAX_FOCUS + 1) if n not in used), None)
    if free is None:
        raise ValueError("week is full")
    row = WeeklyFocus(workspace_id=ws, week_start=start, slot=free, title=title)
    s.add(row)
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

def get_journal(s: Session, ws: int, day: date | None = None) -> dict | None:
    day = day or today_local()
    row = s.scalar(select(JournalEntry).where(
        JournalEntry.workspace_id == ws, JournalEntry.day == day))
    if row is None:
        return None
    return {"day": row.day.isoformat(), "text": row.text, "mood": row.mood}


def save_journal(s: Session, ws: int, text: str, *, day: date | None = None,
                 mood: str = "") -> JournalEntry:
    day = day or today_local()
    row = s.scalar(select(JournalEntry).where(
        JournalEntry.workspace_id == ws, JournalEntry.day == day))
    if row is None:
        row = JournalEntry(workspace_id=ws, day=day, text=text.strip(),
                           mood=mood[:20])
        s.add(row)
    else:
        row.text = text.strip()
        if mood:
            row.mood = mood[:20]
    s.commit()
    return row


def list_journal(s: Session, ws: int, limit: int = 60) -> list[dict]:
    rows = s.scalars(select(JournalEntry).where(JournalEntry.workspace_id == ws)
                     .order_by(JournalEntry.day.desc()).limit(limit)).all()
    return [{"day": r.day.isoformat(), "text": r.text,
             "preview": (r.text or "")[:80], "mood": r.mood} for r in rows]


def delete_journal(s: Session, ws: int, day: date) -> None:
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
# Home
# ---------------------------------------------------------------------------

def home(s: Session, ws: int, user: User) -> dict:
    today = today_local()
    done, total = habit_progress(s, ws, today)
    prayer = prayer_state(s, ws, today, user.gender)
    tasks = list_tasks(s, ws, horizon_days=0)
    goals = list_goals(s, ws, include_completed=False)

    return {
        "date": today.isoformat(),
        "name": user.first_name or "",
        "quote": user.quote,
        "language": user.language,
        "theme": user.theme,
        "gender": user.gender,
        "habits": {"done": done, "total": total},
        "prayer": {"score": prayer["score"], "threshold": PRAYER_DONE_THRESHOLD,
                   "excused": prayer["excused"]},
        "tasks": {"today": tasks_due_today(s, ws), "overdue": tasks["overdue"]},
        "focus": list_focus(s, ws),
        "projects": list_projects(s, ws)[:5],
        "goals": {c: len(v) for c, v in goals.items()},
        "birthdays": [b for b in list_birthdays(s, ws, within_days=7)],
        "journal_today": bool(get_journal(s, ws, today)),
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
    return s.scalar(select(DailyReportLog.id).where(
        DailyReportLog.workspace_id == ws,
        DailyReportLog.report_type == report_type,
        DailyReportLog.report_date == report_date)) is not None


def mark_sent(s: Session, ws: int, report_type: str, report_date: date) -> None:
    """Record delivery. The unique constraint makes a double send impossible."""
    s.add(DailyReportLog(workspace_id=ws, report_type=report_type,
                         report_date=report_date))
    s.commit()


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
