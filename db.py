"""
ErnestOS — database layer.

One PostgreSQL database. Every Telegram user gets one Workspace, and every
domain row carries `workspace_id`, so one user can never reach another's data.

    User ── Workspace ─┬─ Habit ── HabitLog
                       ├─ PrayerLog / PrayerDay
                       ├─ Task ── Project
                       ├─ WeeklyFocus
                       ├─ JournalEntry
                       ├─ Birthday
                       ├─ Feedback
                       └─ DailyReportLog

Goals were removed before the public launch. The model is gone from this
file; the live table is renamed out of the way by migration 0002 rather than
dropped, so the rows survive and the change can be undone.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, time, timezone

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text,
    Time, UniqueConstraint, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development").lower()
IS_PRODUCTION = ENVIRONMENT == "production"

if DATABASE_URL.startswith("postgres://"):
    # Railway/Heroku hand out the legacy scheme; SQLAlchemy 2 wants the driver.
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

if not DATABASE_URL:
    if IS_PRODUCTION:
        raise RuntimeError(
            "DATABASE_URL is required in production. ErnestOS does not fall "
            "back to SQLite — attach a PostgreSQL service and set DATABASE_URL."
        )
    # Development and tests only.
    DATABASE_URL = "sqlite:///ernestos-dev.db"

_kwargs: dict = {"pool_pre_ping": True} if DATABASE_URL.startswith("postgresql") else {
    "connect_args": {"check_same_thread": False}
}
engine = create_engine(DATABASE_URL, **_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


log = logging.getLogger("ernestos")


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    #: Sequential join number — "you are ErnestOS user #42". Assigned once at
    #: registration and never reused, so it stays stable if someone is deleted.
    member_no: Mapped[int] = mapped_column(Integer, default=0, index=True)
    first_name: Mapped[str] = mapped_column(String(200), default="")
    last_name: Mapped[str] = mapped_column(String(200), default="")
    username: Mapped[str] = mapped_column(String(200), default="")
    #: Only ever set from a shared contact whose user_id matches this user.
    phone_number: Mapped[str | None] = mapped_column(String(40), nullable=True)

    language: Mapped[str] = mapped_column(String(2), default="uz")     # uz|en|ru
    gender: Mapped[str | None] = mapped_column(String(6), nullable=True)  # male|female
    theme: Mapped[str] = mapped_column(String(20), default="calm")
    quote: Mapped[str] = mapped_column(Text, default="")
    #: Telegram file_id of the uploaded avatar, or empty for initials.
    photo_file_id: Mapped[str] = mapped_column(String(200), default="")

    #: IANA name, e.g. "Asia/Tashkent". Nullable because the column is added in
    #: place on live tables, where existing rows have no value; every reader
    #: goes through `services.tz_for`, which defaults it.
    timezone: Mapped[str | None] = mapped_column(String(40), nullable=True)

    #: Report and reminder preferences. Nullable for the same reason: NULL means
    #: "never chosen" and reads as the default (both reports on, 04:00 / 21:00,
    #: task reminders on, habit reminders off).
    morning_report: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    morning_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    evening_report: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    evening_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    task_reminders: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    habit_reminders: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    is_subscribed: Mapped[bool] = mapped_column(Boolean, default=False)
    #: When membership was last confirmed with Telegram, and how. The Mini App
    #: re-checks once this goes stale instead of trusting the flag forever
    #: (audit 002).
    sub_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sub_source: Mapped[str] = mapped_column(String(12), default="")  # api|event
    #: Resumable onboarding: survives a bot restart mid-flow.
    onboarding_step: Mapped[str] = mapped_column(String(20), default="language")
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    last_active_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Workspace(Base):
    """One private container per user. Everything below hangs off this id."""

    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"),
        unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------------------
# Habits
# ---------------------------------------------------------------------------

class Habit(Base):
    __tablename__ = "habits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    #: non_negotiable | target | bonus — drives grouping everywhere.
    category: Mapped[str] = mapped_column(String(16), default="target")
    position: Mapped[int] = mapped_column(Integer, default=0)
    #: Derived habits cannot be ticked by hand:
    #:   "prayer"  follows the daily prayer score
    #:   "journal" follows a fully answered journal entry
    #:   "wakeup"  follows a "turdim" message sent before target_time + 1h
    is_protected: Mapped[bool] = mapped_column(Boolean, default=False)
    system_key: Mapped[str] = mapped_column(String(16), default="")
    #: Only meaningful for the wake-up habit: the hour the user intends to rise.
    target_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    #: Which days this habit is expected on:
    #:   "daily"      every day
    #:   "weekdays"   Monday to Friday
    #:   "days:0,2,4" the listed weekdays, 0 = Monday
    #: Nullable — NULL reads as "daily", so habits written before the column
    #: existed keep behaving exactly as they did.
    schedule: Mapped[str | None] = mapped_column(String(24), nullable=True)
    #: Paused habits leave today's denominator but keep every past log, so a
    #: holiday or an injury does not have to mean deleting the habit.
    paused_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: Optional daily nudge for this habit.
    remind_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    #: Soft delete — historical reports must not change retroactively.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class HabitLog(Base):
    __tablename__ = "habit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    habit_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("habits.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Local wall-clock time the habit was ticked. The wake-up habit shows it
    #: back as "✓ 04:53" — "recorded" tells the user nothing they did not
    #: already know. Nullable: rows written before the column have no time.
    logged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("habit_id", "day", name="uq_habit_day"),)


# ---------------------------------------------------------------------------
# Prayer
# ---------------------------------------------------------------------------

class PrayerLog(Base):
    """One row per prayer per day. Status is canonical, never a display label."""

    __tablename__ = "prayer_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    prayer: Mapped[str] = mapped_column(String(10))   # bomdod|peshin|asr|shom|xufton
    status: Mapped[str] = mapped_column(String(10))   # on_time|jamaat|qaza|missed

    __table_args__ = (
        UniqueConstraint("workspace_id", "day", "prayer", name="uq_prayer_day"),
    )


class PrayerDay(Base):
    """Day-level prayer state: the derived score and the female excused flag."""

    __tablename__ = "prayer_days"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    excused: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float] = mapped_column(default=0.0)

    __table_args__ = (
        UniqueConstraint("workspace_id", "day", name="uq_prayer_day_state"),
    )


# ---------------------------------------------------------------------------
# Work
# ---------------------------------------------------------------------------

class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: active | done. A finished project does not have to be deleted, and an
    #: archived one is `archived_at IS NOT NULL` rather than a third status, so
    #: "hidden" and "finished" stay independent.
    status: Mapped[str] = mapped_column(String(10), default="active")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    #: NULL means a standalone task — no fake "Alohida" project row is created.
    project_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    #: Optional clock time on the deadline day. NULL is an all-day task.
    due_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    #: Minutes before the due moment to send a reminder; 0 means exactly then.
    #: NULL means no reminder was asked for.
    remind_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Set once the reminder went out, so it is never sent twice.
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: NULL or "" is a one-off task. Otherwise:
    #:   daily | weekdays | weekly | monthly | days:0,2,4  (0 = Monday)
    #: Completing a recurring task creates the next occurrence; the recurrence
    #: itself is never consumed by ticking it once.
    recurrence: Mapped[str | None] = mapped_column(String(24), nullable=True)
    #: The day on which the user picked this task as one of their top three.
    #: A date rather than a flag, so yesterday's choice does not linger.
    focus_day: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    priority: Mapped[str] = mapped_column(String(6), default="medium")  # high|medium|low
    status: Mapped[str] = mapped_column(String(10), default="waiting")  # waiting|done
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class WeeklyFocus(Base):
    """The week's mission and its supporting priorities.

    One row per slot. Slot 1 is *the* mission — the week's single answer to
    "what matters most" — and slots 2 and 3 are supporting priorities shown
    at a visibly lower weight. Three equally sized missions is no mission at
    all, so the hierarchy is in the slot number rather than in the user's
    memory.
    """

    __tablename__ = "weekly_focus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    week_start: Mapped[date] = mapped_column(Date, index=True)  # Monday
    slot: Mapped[int] = mapped_column(Integer)                  # 1 for new rows
    title: Mapped[str] = mapped_column(String(200))
    #: high | medium | low. Nullable because the column is added in place to
    #: live tables, where existing rows have no value; readers default it.
    priority: Mapped[str | None] = mapped_column(String(6), nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("workspace_id", "week_start", "slot", name="uq_focus_slot"),
    )


# ---------------------------------------------------------------------------
# Journal, birthdays, feedback, scheduling
# ---------------------------------------------------------------------------

class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    #: JSON object keyed by question id. The day counts as journalled only
    #: once every question has an answer.
    answers: Mapped[str] = mapped_column(Text, default="{}")
    mood: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("workspace_id", "day", name="uq_journal_day"),)


class WeeklyReview(Base):
    """One review per ISO week: what worked, what blocked, next week's focus.

    Statistics alone do not close the loop — the point of the week is to make
    three decisions, and those need somewhere to live.
    """

    __tablename__ = "weekly_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    week_start: Mapped[date] = mapped_column(Date, index=True)   # Monday
    went_well: Mapped[str] = mapped_column(Text, default="")
    blocked: Mapped[str] = mapped_column(Text, default="")
    next_focus: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("workspace_id", "week_start", name="uq_weekly_review"),
    )


class Birthday(Base):
    __tablename__ = "birthdays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    person_name: Mapped[str] = mapped_column(String(200))
    birth_date: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message: Mapped[str] = mapped_column(Text)
    #: False until Telegram confirms the admin-channel message was delivered.
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DailyReportLog(Base):
    """Outbox row for one report.

    The unique constraint is the lock: a worker INSERTs `claimed` and only the
    winner sends. Check-then-send-then-mark could send twice when two workers
    interleave, or lose a report when the process dies between send and mark
    (audit 036).
    """

    __tablename__ = "daily_report_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    report_type: Mapped[str] = mapped_column(String(10))  # morning|evening
    report_date: Mapped[date] = mapped_column(Date, index=True)
    #: claimed -> sent | failed
    status: Mapped[str] = mapped_column(String(10), default="claimed")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(200), default="")
    claimed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("workspace_id", "report_type", "report_date",
                         name="uq_daily_report"),
    )


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

#: SQLAlchemy type -> the DDL used when a column has to be added in place.
#: Only additive DDL appears here; nothing in this file ever drops or narrows.
def _column_ddl(column) -> str | None:
    try:
        type_sql = column.type.compile(engine.dialect)
    except Exception:
        return None
    parts = [f'"{column.name}" {type_sql}']
    default = column.server_default
    if default is not None and getattr(default, "arg", None) is not None:
        parts.append(f"DEFAULT {default.arg}")
    return " ".join(parts)


def _add_missing_columns() -> list[str]:
    """Bring existing tables up to the model, without touching their data.

    A release that adds a column used to mean dropping the whole database,
    because create_all() only creates missing *tables*. With real users on the
    system that is not an acceptable upgrade path, so missing columns are added
    in place instead. Adding is always safe: existing rows get NULL (or the
    server default) and nothing is rewritten.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    added: list[str] = []

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing or column.primary_key:
                    continue
                ddl = _column_ddl(column)
                if ddl is None:
                    log.warning("cannot auto-add %s.%s — add it by hand",
                                table.name, column.name)
                    continue
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
                added.append(f"{table.name}.{column.name}")

    return added


def init_db() -> None:
    """Create missing tables, then add any missing columns to existing ones.

    Purely additive, so it is safe to run on every boot against a live
    database: no table is dropped, no column is removed or retyped.
    """
    Base.metadata.create_all(engine)
    added = _add_missing_columns()
    if added:
        log.info("schema updated — added columns: %s", ", ".join(added))


def drop_all() -> None:
    """Destroy every ErnestOS table. Only ever called by the reset command."""
    Base.metadata.drop_all(engine)
