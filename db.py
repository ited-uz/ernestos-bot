"""
ErnestOS — database layer.

One PostgreSQL database. Every Telegram user gets one Workspace, and every
domain row carries `workspace_id`, so one user can never reach another's data.

    User ── Workspace ─┬─ Habit ── HabitLog
                       ├─ PrayerLog / PrayerDay
                       ├─ Task ── Project
                       ├─ Goal
                       ├─ WeeklyFocus
                       ├─ JournalEntry
                       ├─ Birthday
                       ├─ Feedback
                       └─ DailyReportLog
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint, create_engine,
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
    first_name: Mapped[str] = mapped_column(String(200), default="")
    last_name: Mapped[str] = mapped_column(String(200), default="")
    username: Mapped[str] = mapped_column(String(200), default="")
    #: Only ever set from a shared contact whose user_id matches this user.
    phone_number: Mapped[str | None] = mapped_column(String(40), nullable=True)

    language: Mapped[str] = mapped_column(String(2), default="uz")     # uz|en|ru
    gender: Mapped[str | None] = mapped_column(String(6), nullable=True)  # male|female
    theme: Mapped[str] = mapped_column(String(20), default="ocean")
    quote: Mapped[str] = mapped_column(Text, default="")
    #: Telegram file_id of the uploaded avatar, or empty for initials.
    photo_file_id: Mapped[str] = mapped_column(String(200), default="")

    is_subscribed: Mapped[bool] = mapped_column(Boolean, default=False)
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
    #: Derived habits cannot be ticked by hand: "prayer" follows the daily
    #: prayer score, "journal" follows a fully answered journal entry.
    is_protected: Mapped[bool] = mapped_column(Boolean, default=False)
    system_key: Mapped[str] = mapped_column(String(16), default="")
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
    status: Mapped[str] = mapped_column(String(10), default="active")  # active|done
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
    priority: Mapped[str] = mapped_column(String(6), default="medium")  # high|medium|low
    status: Mapped[str] = mapped_column(String(10), default="waiting")  # waiting|done
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    #: Flat category, not a parent/child tree — three levels is all this needs.
    category: Mapped[str] = mapped_column(String(10), default="tactical")
    status: Mapped[str] = mapped_column(String(10), default="active")  # active|completed
    progress: Mapped[int] = mapped_column(Integer, default=0)          # 0..100
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WeeklyFocus(Base):
    """At most three missions per ISO week, enforced by the unique slot."""

    __tablename__ = "weekly_focus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    week_start: Mapped[date] = mapped_column(Date, index=True)  # Monday
    slot: Mapped[int] = mapped_column(Integer)                  # 1..3
    title: Mapped[str] = mapped_column(String(200))
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
    #: JSON object keyed by question id. The Summary habit only counts as done
    #: once every question has an answer.
    answers: Mapped[str] = mapped_column(Text, default="{}")
    mood: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("workspace_id", "day", name="uq_journal_day"),)


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
    """Idempotency for the scheduler: a restart must not re-send a report."""

    __tablename__ = "daily_report_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    report_type: Mapped[str] = mapped_column(String(10))  # morning|evening
    report_date: Mapped[date] = mapped_column(Date, index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("workspace_id", "report_type", "report_date",
                         name="uq_daily_report"),
    )


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create any missing tables.

    Explicit and additive: it never drops or alters an existing column, so it is
    safe to call on startup. Schema changes are made by editing the models and
    running `python -c "import db; db.init_db()"` against the target database.
    """
    Base.metadata.create_all(engine)


def drop_all() -> None:
    """Destroy every ErnestOS table. Only ever called by the reset command."""
    Base.metadata.drop_all(engine)
