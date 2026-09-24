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
    BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer,
    String, Text, Time, UniqueConstraint, create_engine,
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
    #: Unique so that two accounts created in the same instant cannot be
    #: handed the same number; `get_or_create_user` retries on the clash.
    member_no: Mapped[int] = mapped_column(
        Integer, default=0, index=True, unique=True)
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

    #: How many real actions this account has taken — a task ticked, a habit
    #: logged, a prayer recorded. The channel is not asked for until this
    #: passes `FREE_ACTIONS`: somebody who has just arrived has no reason to
    #: join a channel about a product they have not used yet, and asking at the
    #: door is where most of them left. Reading and scrolling do not count;
    #: only the things that change the day do.
    actions_count: Mapped[int] = mapped_column(Integer, default=0)

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
    #: When today's reminder for this habit was sent. Task reminders have had
    #: `Task.reminder_sent_at` all along; habits had nothing, so "did we
    #: already nudge them?" was answered by the width of the job window alone,
    #: and any late or doubled tick asked twice.
    reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)

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
    #: For a monthly recurrence, the day of the month the user actually chose.
    #: Needed because the deadline is clamped to the length of each month: a
    #: task set for the 31st becomes the 28th in February, and without this the
    #: 28th is what every later month inherits, so the task silently walks
    #: backwards and never returns to the 31st.
    anchor_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
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


class ReferralCode(Base):
    """One stable, opaque invite code per account.

    Keyed on the user, so the code is generated once and never rotates — a link
    somebody has already shared must keep working. The code is random rather
    than derived from the Telegram id: `ref_123456789` would publish the id of
    everybody who ever sent an invite, to everybody who ever received one.

    Deliberately its own table rather than a column on `users`. The project
    creates missing tables on boot but adds columns in place, and a new table
    is the change with no effect at all on the existing `users` rows.
    """

    __tablename__ = "referral_codes"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"),
        primary_key=True)
    #: Telegram deep-link safe alphabet only: A-Z a-z 0-9 _ -
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Referral(Base):
    """Who brought this account, recorded once and never rewritten.

    `referred_user_id` is the primary key, and that single choice is what makes
    attribution first-touch and immutable: there is physically nowhere to put a
    second inviter for the same person. A later link from somebody else hits
    the primary key and loses, which is the correct outcome rather than an
    error to handle.

    The database is the last line of defence, not the first — `claim_referral`
    checks before inserting — but under concurrent /start retries the check can
    race and the constraint is what actually holds.
    """

    __tablename__ = "referrals"

    referred_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"),
        primary_key=True)
    inviter_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"),
        index=True)
    #: bot | miniapp — which surface the link was opened through.
    source: Mapped[str] = mapped_column(String(10), default="bot")
    #: pending -> qualified. A referral is only ever promoted, never demoted.
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    qualified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Personal progression
# ---------------------------------------------------------------------------
#
# Three tables, and the split between them is the point. `DailyScore` and
# `XPEvent` are the *record* — append-mostly, never derived from anything else,
# and the thing any number shown to a user can be traced back to. `UserProgress`
# is a *cache*: every field on it can be recomputed from the other two, and it
# exists so that opening a profile is one indexed row read rather than a scan of
# a year of history for every user on the platform.
#
# Keeping that distinction honest is what stops the cache from quietly becoming
# the only copy. Nothing writes to `UserProgress` that was not first written to
# `DailyScore` or `XPEvent`.
#
# All three are new tables rather than columns on `users`, for the same reason
# the referral tables were: this project creates missing tables on boot and adds
# columns in place, and a new table is the change with no effect at all on the
# rows that already exist.

class DailyScore(Base):
    """One row per user per local day: how that day actually went.

    The day is the user's own calendar date, not the server's. Storing it as a
    plain `Date` computed in their zone is what makes "my Tuesday" mean the same
    thing to the database as it did to them — comparing a UTC timestamp against
    a local date is the bug this project already had once, and the reason
    `local_date_of` exists.

    The component columns are stored rather than recomputed because the source
    rows move underneath them: a task edited next week must not silently rewrite
    last Tuesday's score. Recomputing today is fine and expected; recomputing
    the past is not, which is why `upsert_daily_score` only ever writes the day
    it was asked for.
    """

    __tablename__ = "daily_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)

    #: The four components, each 0-100, or -1 for "this category had no
    #: denominator that day". -1 rather than NULL so the column is cheap to read
    #: back into the same shape `overall_components` produces, where absent and
    #: zero are deliberately different things.
    task_score: Mapped[int] = mapped_column(Integer, default=-1)
    habit_score: Mapped[int] = mapped_column(Integer, default=-1)
    focus_score: Mapped[int] = mapped_column(Integer, default=-1)
    prayer_score: Mapped[int] = mapped_column(Integer, default=-1)

    total_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    #: S | A | B | C | D | E — the grade the total falls into.
    grade: Mapped[str] = mapped_column(String(1), default="E")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow,
                                                 onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "day", name="uq_daily_score"),
        # Ranking reads "every user's scores in the last 30 days" and nothing
        # else; this is the index that query lives on.
        Index("ix_daily_score_day_user", "day", "user_id"),
    )


class XPEvent(Base):
    """One row per thing that earned XP. The ledger, not a running total.

    `event_key` is the whole design. Every award names itself deterministically
    — `task_complete:412`, `perfect_day:1001:2026-08-14`, `streak_7:1001:...` —
    and the unique constraint means the second attempt to write it does nothing.
    That is what makes XP survive the things that actually happen in production:
    a Telegram retry, a double-tapped button, a user completing a task, undoing
    it and completing it again, and the API being called twice because the phone
    lost signal mid-request.

    A `xp_total` column incremented in place would have none of that. It would
    also have no way to answer "where did these 2,840 points come from?", which
    is the question anybody disputing their score is really asking.
    """

    __tablename__ = "xp_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), index=True)
    #: Globally unique, and deterministic from what happened. The user id is
    #: part of every key so two people completing task 412 do not collide.
    event_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    #: Coarse bucket for reporting: task | ritual | focus | perfect_day |
    #: streak | comeback | onboarding | achievement | level.
    event_type: Mapped[str] = mapped_column(String(20), default="task")
    xp: Mapped[int] = mapped_column(Integer, default=0)
    #: The user's local day this belongs to — what the daily cap is counted
    #: against, and what "XP earned today" on the profile means.
    event_date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_xp_user_date", "user_id", "event_date"),
    )


class UserProgress(Base):
    """The rolled-up summary. Every field here is derivable; none is the source.

    This exists for one reason: ranking. "Where am I among 12,842 users?" is a
    question about every user at once, and answering it from `daily_scores`
    would mean aggregating a month of rows per person on every profile open.
    Instead each user's index is maintained when their own day changes, and the
    rank query is one indexed scan of a single narrow column.

    `best_global_rank` is the one field that is *not* recomputable, and that is
    deliberate rather than an oversight: it is a high-water mark over ranks that
    existed at moments in the past, and those moments are gone. It only ever
    moves toward a better rank.
    """

    __tablename__ = "user_progress"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"),
        primary_key=True)

    xp_total: Mapped[int] = mapped_column(Integer, default=0, index=True)

    current_streak: Mapped[int] = mapped_column(Integer, default=0)
    best_streak: Mapped[int] = mapped_column(Integer, default=0)
    perfect_days: Mapped[int] = mapped_column(Integer, default=0)

    #: Recovery days are a monthly allowance, so the month they belong to is
    #: stored beside the count. Comparing that to the user's current local month
    #: is what resets them, rather than a scheduled job that has to visit every
    #: user on the first of the month.
    recovery_used: Mapped[int] = mapped_column(Integer, default=0)
    recovery_month: Mapped[str] = mapped_column(String(7), default="")

    #: The ranking inputs. Indexed because the rank query orders by them.
    performance_index_30d: Mapped[float] = mapped_column(Float, default=0.0)
    performance_index_7d: Mapped[float] = mapped_column(Float, default=0.0)
    #: Local days with a score on record. Ranking unlocks at 7, so that a new
    #: account cannot take #1 on the strength of one good day.
    scored_days: Mapped[int] = mapped_column(Integer, default=0)

    best_global_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: What the last shown rank was, so movement (↑7) can be reported honestly
    #: rather than invented.
    last_global_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: The most recent local day this user has a score for. Drives the streak,
    #: the comeback check and "is this user still active".
    last_score_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: The last day a comeback was awarded, so returning cannot be farmed by
    #: disappearing on purpose.
    last_comeback_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow,
                                                 onupdate=utcnow)

    __table_args__ = (
        # The two ranking scans, and nothing else reads these columns in bulk.
        Index("ix_progress_30d", "performance_index_30d"),
        Index("ix_progress_7d", "performance_index_7d"),
    )


class UserAchievement(Base):
    """One row the first time a user earns something. Never written twice.

    The definitions live in `services.ACHIEVEMENTS` rather than in a table:
    they are code — a key, a rule and three translations — and a row per
    definition would mean a migration every time the wording changed.
    """

    __tablename__ = "user_achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), index=True)
    achievement_key: Mapped[str] = mapped_column(String(40))
    unlocked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "achievement_key", name="uq_user_achievement"),
    )


class JobRun(Base):
    """One row per job, per day it actually ran.

    `DailyReportLog` does this for reports, but it is keyed on a workspace and
    the daily statistics post belongs to no user — it is one message about the
    whole platform. This is the same idea without the workspace: the unique
    constraint is the lock, so the first tick of the day to INSERT wins and
    every other tick, in this process or another, finds the row taken.

    It exists because a `cron(hour=10)` job on an in-memory jobstore has no
    memory. APScheduler computes the next fire from *now* at boot, so a deploy
    at 11:00 moved the statistics post to 10:00 tomorrow — and a project being
    redeployed most days never reached it at all. A frequent tick that asks
    "has today's run been claimed?" cannot be starved by a restart, and cannot
    double-post either.
    """

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(40), index=True)
    #: The local date, on the project clock, this run belongs to.
    run_date: Mapped[date] = mapped_column(Date, index=True)
    ran_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("job_name", "run_date", name="uq_job_run"),
    )


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------
#
# A team is a second container, standing beside a workspace rather than inside
# one. That shape is forced by what a shared goal actually is: two people
# working on *the same* thing and each doing their own share of it. A task
# copied into both workspaces would be two tasks that drift apart; a task in
# one shared workspace could only be ticked once, by whoever got there first,
# and the other person's effort would be invisible.
#
# So the definition is shared and the doing is not. `TeamTask` and `TeamHabit`
# hold what the item *is* — one row, one title, one deadline, edited by either
# member. `TeamTaskDone` and `TeamHabitLog` hold who has done it, one row per
# member, which is what lets the reports say "you did four of six, she did
# five" instead of collapsing the two of you into one number.
#
# Nothing here touches `workspace_id`, and no team row is ever mixed into a
# workspace query: a person's private lists stay exactly as private as they
# were before they joined anything.

class Team(Base):
    """A shared space two or more people work in."""

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), default="")
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), index=True)
    #: The invite token, carried in `t.me/<bot>?start=team_<code>`. Random
    #: rather than the id, so a link cannot be guessed from a team number.
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TeamMember(Base):
    """One person's membership of one team."""

    __tablename__ = "team_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10), default="member")  # owner|member
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_member"),
    )


class TeamTask(Base):
    """A task the whole team is working on. One row, however many members."""

    __tablename__ = "team_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    priority: Mapped[str] = mapped_column(String(6), default="medium")
    created_by: Mapped[int] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TeamTaskDone(Base):
    """One member's completion of one team task.

    Separate from the task so that "done" is a per-person fact. A couple
    revising the same chapter are not finished when one of them is.
    """

    __tablename__ = "team_task_done"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("team_tasks.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    done_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    #: The local day it was ticked, so the reports can count it against the
    #: day the person actually lived rather than a UTC instant.
    day: Mapped[date] = mapped_column(Date, index=True)

    __table_args__ = (
        UniqueConstraint("task_id", "user_id", name="uq_team_task_done"),
    )


class TeamHabit(Base):
    """A habit the team keeps together, each member ticking their own."""

    __tablename__ = "team_habits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    schedule: Mapped[str | None] = mapped_column(String(24), nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    #: Same three tiers a personal habit has, because a team habit is scored
    #: in the same arithmetic. Defaults to the top tier: something two people
    #: agreed to do is harder to drop than something one person told themselves.
    category: Mapped[str] = mapped_column(String(16), default="non_negotiable")
    #: Set for the rituals every team is seeded with — waking, prayer, the
    #: journal — so they can be recognised, kept, and never deleted by accident.
    system_key: Mapped[str] = mapped_column(String(16), default="")
    is_protected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TeamHabitLog(Base):
    """One member, one team habit, one day."""

    __tablename__ = "team_habit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    habit_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("team_habits.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    logged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("habit_id", "user_id", "day", name="uq_team_habit_day"),
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


#: The outbox's unique key. `claim_report` inserts a row and reads the
#: resulting IntegrityError as "somebody already has this one", so this exact
#: combination is the once-a-day guarantee.
REPORT_OUTBOX_KEY = ("workspace_id", "report_type", "report_date")


def _repair_report_outbox_key() -> str | None:
    """Make `daily_report_logs` carry the right unique key, and only that one.

    `create_all` builds constraints only when it creates the whole table, so a
    database whose outbox predates the current key keeps the old one for ever.
    When the old key is a *subset* of the right one — (workspace, type), with
    no date — the effect is not a subtle inconsistency: the first report a user
    is ever sent fills their only slot, and from the next day on every claim is
    refused, every tick skips them, and nothing is ever delivered again. No
    error reaches the user and the job logs look healthy.

    This runs at boot rather than as a numbered migration because the
    application cannot do its job without it, and the people running it deploy
    by uploading a build and have no shell to run a migration from. It is
    idempotent: with the right key already in place it inspects and returns.
    """
    from sqlalchemy import inspect, text

    table = "daily_report_logs"
    inspector = inspect(engine)
    if not inspector.has_table(table):
        return None

    wanted = sorted(REPORT_OUTBOX_KEY)

    def cols(entry: dict) -> list:
        return sorted(entry.get("column_names") or entry.get("columns") or [])

    unique_entries = [
        *({"name": c["name"], "cols": cols(c)}
          for c in inspector.get_unique_constraints(table)),
        *({"name": i["name"], "cols": cols(i)}
          for i in inspector.get_indexes(table) if i.get("unique")),
    ]
    if any(e["cols"] == wanted for e in unique_entries):
        return None

    # The outbox has exactly one legitimate unique key, so anything else that
    # is unique on this table is wrong and has to go. Matching only on "a
    # subset of the right columns" was too narrow: a key over some *other*
    # combination rejects rows just as effectively, and would have been left
    # in place while the repair reported success.
    stale = [e for e in unique_entries
             if e["name"] and e["cols"] and e["cols"] != wanted
             and e["cols"] != ["id"]]

    dropped, removed = [], 0
    with engine.begin() as conn:
        for entry in stale:
            for statement in (
                    f"ALTER TABLE {table} DROP CONSTRAINT {entry['name']}",
                    f"DROP INDEX {entry['name']}"):
                try:
                    conn.execute(text(statement))
                    dropped.append(entry["name"])
                    break
                except Exception:
                    continue

        removed = _collapse_outbox_duplicates(conn, table)

        # Only claim to have fixed it if the bad key is really gone. SQLite
        # cannot drop a constraint written into CREATE TABLE, so on that
        # backend the loop above does nothing and the table has to be rebuilt.
        if len(dropped) < len(stale):
            _rebuild_report_outbox(conn, table)
            dropped = [e["name"] for e in stale]
            note = " (table rebuilt)"
        else:
            conn.execute(text(
                f"CREATE UNIQUE INDEX uq_daily_report ON {table} "
                f"({', '.join(REPORT_OUTBOX_KEY)})"))
            note = ""

    # Say what is true now, not what was attempted.
    after = inspect(engine)
    still_wrong = [
        e["name"] for e in (
            *({"name": c["name"], "cols": cols(c)}
              for c in after.get_unique_constraints(table)),
            *({"name": i["name"], "cols": cols(i)}
              for i in after.get_indexes(table) if i.get("unique")),
        ) if e["cols"] and e["cols"] != wanted and e["cols"] != ["id"]
    ]
    if still_wrong:
        log.error("the stale unique key(s) %s are still on %s — daily reports "
                  "will keep being skipped until they are dropped by hand",
                  still_wrong, table)
        return None

    return (f"rebuilt the report outbox key{note} — dropped {dropped}, "
            f"removed {removed} duplicate row(s)")


def _collapse_outbox_duplicates(conn, table: str) -> int:
    """Leave one row per slot, because a unique key cannot be built over two.

    Duplicates are what the missing key allowed in the first place. The row
    that was actually sent wins, so collapsing can never turn a delivered
    report back into an undelivered one; otherwise the earliest wins.
    """
    from sqlalchemy import text

    removed = 0
    groups = conn.execute(text(f"""
        SELECT MIN(CASE WHEN status = 'sent' THEN id END), MIN(id),
               workspace_id, report_type, report_date
        FROM {table}
        GROUP BY workspace_id, report_type, report_date
        HAVING COUNT(*) > 1
    """)).all()
    for sent_id, any_id, ws, kind, day in groups:
        result = conn.execute(text(f"""
            DELETE FROM {table}
            WHERE workspace_id = :ws AND report_type = :kind
              AND report_date = :day AND id <> :keep
        """), {"ws": ws, "kind": kind, "day": day, "keep": sent_id or any_id})
        removed += result.rowcount or 0
    return removed


#: The outbox as SQLite wants it spelled. Only SQLite needs this: PostgreSQL
#: drops a constraint in place, so it never reaches the rebuild.
_SQLITE_OUTBOX_DDL = """
CREATE TABLE {name} (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    report_type VARCHAR(10) NOT NULL,
    report_date DATE NOT NULL,
    status VARCHAR(10),
    attempts INTEGER,
    last_error VARCHAR(200),
    claimed_at DATETIME,
    sent_at DATETIME
)
"""


def _rebuild_report_outbox(conn, table: str) -> None:
    """Recreate the outbox with the right key, carrying every row across.

    SQLite has no `DROP CONSTRAINT`, so a unique key written into CREATE TABLE
    can only be removed by rebuilding the table. Copy, swap, keep the history.
    Written as explicit DDL rather than reflected from the model, because
    reflecting it into a fresh MetaData cannot resolve the foreign key to
    `workspaces` and fails.
    """
    from sqlalchemy import text

    if engine.dialect.name != "sqlite":
        raise RuntimeError(
            f"cannot rebuild {table} on {engine.dialect.name} — the stale "
            "unique key must be dropped with ALTER TABLE DROP CONSTRAINT")

    columns = ", ".join(c.name for c in DailyReportLog.__table__.columns)
    staging = f"{table}__rebuild"

    conn.execute(text(f"DROP TABLE IF EXISTS {staging}"))
    conn.execute(text(_SQLITE_OUTBOX_DDL.format(name=staging)))
    conn.execute(text(
        f"INSERT INTO {staging} ({columns}) SELECT {columns} FROM {table}"))
    conn.execute(text(f"DROP TABLE {table}"))
    conn.execute(text(f"ALTER TABLE {staging} RENAME TO {table}"))
    conn.execute(text(
        f"CREATE UNIQUE INDEX uq_daily_report ON {table} "
        f"({', '.join(REPORT_OUTBOX_KEY)})"))
    conn.execute(text(
        f"CREATE INDEX ix_daily_report_logs_workspace_id ON {table} (workspace_id)"))
    conn.execute(text(
        f"CREATE INDEX ix_daily_report_logs_report_date ON {table} (report_date)"))


def init_db() -> None:
    """Create missing tables, then add any missing columns to existing ones.

    Purely additive with one deliberate exception, `_repair_report_outbox_key`,
    which replaces a unique key that silently switches the daily reports off.
    No table is dropped, no column is removed or retyped.
    """
    Base.metadata.create_all(engine)
    added = _add_missing_columns()
    if added:
        log.info("schema updated — added columns: %s", ", ".join(added))
    try:
        repaired = _repair_report_outbox_key()
        if repaired:
            log.warning("schema repair — %s", repaired)
    except Exception:
        log.exception("could not repair the report outbox unique key")


def drop_all() -> None:
    """Destroy every ErnestOS table. Only ever called by the reset command."""
    Base.metadata.drop_all(engine)
