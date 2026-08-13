"""
ErnestOS — explicit data migrations.

Schema columns are added automatically by `db.init_db()`. This module is for
*data* changes that must be applied deliberately, once, against a live
database — never on import and never on boot.

Each migration is:
  * numbered, so the order is fixed;
  * idempotent, so running it twice changes nothing;
  * non-destructive, so user history survives.

Run them all:

    python migrations.py

Run one:

    python migrations.py 0001
"""

from __future__ import annotations

import logging
import sys

from sqlalchemy import select

import db
from db import Habit, SessionLocal, utcnow

log = logging.getLogger("ernestos.migrations")


def m0001_retire_summary_habit() -> dict:
    """Superseded by `0006`. Kept as a no-op so the numbering stays honest.

    This migration used to archive the journal habit, on the reasoning that a
    journal is a status rather than a habit. That product decision was reversed:
    writing the day up is one of the non-negotiables again, and `0006` restores
    the habit and backfills it from the journal entries.

    It must not run any more. Left as a live step it would re-archive the habit
    every time the chain was replayed, immediately undoing `0006` — the two
    would fight, and which one won would depend on the order they happened to
    be invoked in.
    """
    return {"migration": "0001_retire_summary_habit",
            "status": "superseded by 0006", "archived": 0}


#: Where migration 0002 parks the goals table. Keeping the name in one place
#: means the rollback instruction and the code cannot disagree.
GOALS_ARCHIVE_TABLE = "goals_archived_v1"


def m0002_retire_goals() -> dict:
    """Take Goals out of the live schema without destroying the rows.

    Goals are gone from the product: no bot menu, no Mini App screen, no API,
    no model. What is left is the `goals` table on databases that already have
    one, still holding whatever users wrote there.

    Dropping it would be the tidy move and the wrong one — a deleted table is
    not recoverable from a running system, and "we removed a screen" must
    never mean "we deleted your data". So the table is *renamed* instead:

      * the rows survive, with their workspace_id intact;
      * nothing in the application can reach them, which is the actual goal;
      * `Base.metadata.create_all()` will not recreate `goals`, because the
        model no longer exists;
      * the change is undone with one statement.

    Rollback:

        ALTER TABLE goals_archived_v1 RENAME TO goals;

    then restore the Goal model and its routes from version control.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())

    if "goals" not in tables:
        # Either a fresh database or a second run.
        return {"migration": "0002_retire_goals",
                "status": "already archived" if GOALS_ARCHIVE_TABLE in tables
                          else "nothing to do",
                "rows": 0}

    if GOALS_ARCHIVE_TABLE in tables:
        # An archive already exists from an earlier run and a `goals` table
        # came back. Refuse rather than overwrite the older archive.
        raise SystemExit(
            f"both goals and {GOALS_ARCHIVE_TABLE} exist — resolve by hand "
            f"before re-running 0002")

    with db.engine.begin() as conn:
        rows = conn.execute(text("SELECT count(*) FROM goals")).scalar() or 0
        conn.execute(text(f"ALTER TABLE goals RENAME TO {GOALS_ARCHIVE_TABLE}"))

    return {"migration": "0002_retire_goals", "status": "archived",
            "rows": rows, "archive": GOALS_ARCHIVE_TABLE}


#: What each retired theme is replaced by. The mapping is by intent, not by
#: hue: someone who chose `rose` wanted pink and someone who chose `obsidian`
#: wanted something restrained, so each lands on the closest survivor rather
#: than all of them being flattened into the default.
#:
#: `aurora` is deliberately absent. The name was reused for the new violet
#: theme, so those rows are already valid and must be left untouched.
#:
#: `ocean` was removed from this mapping when the palette was rebuilt: it used
#: to be a retired name pointing at `cobalt`, and it is now the *default* theme.
#: Leaving it here would mean running 0003 after 0005 quietly moved every
#: default account onto a name that no longer exists. Ancient rows still holding
#: `ocean` are simply valid again, which is the outcome that needed no code.
RETIRED_THEMES = {
    "obsidian": "slate",
    "emerald": "slate",
    "rose": "blossom",
    "pink": "blossom",
}


def m0003_retire_themes() -> dict:
    """Move accounts off the themes the Mini App no longer styles.

    The palette shrank from nine themes to four. A row still holding a retired
    name is not broken — the app reads any unknown value as the default — but
    the stored value and the Settings screen would disagree until the user
    picked something, so the value is rewritten once here.

    Only the known retired names are touched. A row already on one of the four
    is left alone, which is what makes a second run a no-op.
    """
    from sqlalchemy import update

    from db import User

    moved: dict[str, int] = {}
    with SessionLocal() as s:
        for old, new in RETIRED_THEMES.items():
            count = s.execute(
                update(User).where(User.theme == old).values(theme=new)).rowcount
            if count:
                moved[f"{old}→{new}"] = count
        s.commit()
    return {"migration": "0003_retire_themes", "moved": moved,
            "total": sum(moved.values())}


def m0004_recompute_prayer_completion() -> dict:
    """Re-derive the `5x namoz` habit from what was actually prayed.

    The habit used to be marked done whenever the day's *quality* score
    reached 2.5, which meant three prayers could complete a habit called "5x
    namoz". Completion is now all five prayed (or an excused day), and those
    are two separate questions — see `services.prayer_is_complete`.

    Every existing HabitLog row for that habit was written under the old rule,
    so this recomputes them from the PrayerLog rows, which are the source of
    truth and are not touched. Days where the answer does not change are left
    alone, which is what makes a second run a no-op.

    This does move historical numbers: a day with three prayers logged stops
    counting as a completed habit. That is the point — the old number was
    wrong, and leaving it in place would mean the streak and the statistics
    keep reporting something the user did not do.
    """
    from sqlalchemy import select as sql_select

    from db import Habit, HabitLog, PrayerDay, PrayerLog, User, Workspace
    import services as svc

    changed = corrected_true = corrected_false = 0

    with SessionLocal() as s:
        habits = s.scalars(sql_select(Habit).where(
            Habit.system_key == svc.SYSTEM_PRAYER)).all()

        for habit in habits:
            workspace = s.get(Workspace, habit.workspace_id)
            gender = None
            if workspace is not None:
                owner = s.get(User, workspace.user_id)
                gender = owner.gender if owner else None

            # Every day this workspace has either a prayer record or a habit
            # log for: both sides have to be reconciled, because a log may
            # exist for a day whose prayers were later cleared.
            days = set(s.scalars(sql_select(PrayerLog.day).where(
                PrayerLog.workspace_id == habit.workspace_id)).all())
            days |= set(s.scalars(sql_select(HabitLog.day).where(
                HabitLog.workspace_id == habit.workspace_id,
                HabitLog.habit_id == habit.id)).all())

            for day in days:
                statuses = {r.prayer: r.status for r in s.scalars(
                    sql_select(PrayerLog).where(
                        PrayerLog.workspace_id == habit.workspace_id,
                        PrayerLog.day == day)).all()}
                state = s.scalar(sql_select(PrayerDay).where(
                    PrayerDay.workspace_id == habit.workspace_id,
                    PrayerDay.day == day))
                excused = bool(state and state.excused)
                should = svc.prayer_is_complete(statuses, gender, excused)

                row = s.scalar(sql_select(HabitLog).where(
                    HabitLog.workspace_id == habit.workspace_id,
                    HabitLog.habit_id == habit.id, HabitLog.day == day))
                if row is None:
                    if not should:
                        continue
                    s.add(HabitLog(workspace_id=habit.workspace_id,
                                   habit_id=habit.id, day=day, done=True))
                    changed += 1
                    corrected_true += 1
                elif row.done != should:
                    row.done = should
                    changed += 1
                    corrected_true += int(should)
                    corrected_false += int(not should)

                # The stored day score is the quality number; recompute it too
                # so an excused day reads as a full day rather than as half.
                score = svc.prayer_score(statuses, gender, excused)
                if state is None:
                    if score:
                        s.add(PrayerDay(workspace_id=habit.workspace_id, day=day,
                                        excused=excused, score=score))
                elif float(state.score) != score:
                    state.score = score
        s.commit()

    return {"migration": "0004_recompute_prayer_completion",
            "habit_logs_changed": changed,
            "now_complete": corrected_true, "no_longer_complete": corrected_false}


#: Where each previous theme name lands. The five themes were rebuilt as five
#: distinct visual systems rather than five palettes, so the names changed with
#: them. The mapping is by intent, not by hue: someone who picked the restrained
#: dark one still gets a restrained dark one.
THEME_RENAMES = {
    "cobalt": "ocean",       # the old default blue -> the new default blue
    "slate": "midnight",     # restrained and dark -> restrained and dark
    "oxford": "pure",        # formal on paper -> paper
    "blossom": "aurora",     # pink -> the violet-to-rose one
    # Older names that migration 0003 mapped into the set above; anyone who
    # never ran it lands in the right place directly.
    "obsidian": "midnight",
    "emerald": "sage",
    "rose": "aurora",
    "pink": "aurora",
}


def m0005_rename_themes() -> dict:
    """Move accounts onto the rebuilt theme set.

    A row holding an old name is not broken — the app reads any unknown value
    as the default — but the stored value and the Settings screen would
    disagree until the user picked something, so it is rewritten once here.

    `aurora` is deliberately absent from the keys: the name survives into the
    new set, so those rows are already valid and must be left untouched.
    """
    from sqlalchemy import update

    from db import User

    moved: dict[str, int] = {}
    with SessionLocal() as s:
        for old, new in THEME_RENAMES.items():
            count = s.execute(
                update(User).where(User.theme == old).values(theme=new)).rowcount
            if count:
                moved[f"{old}→{new}"] = count
        s.commit()
    return {"migration": "0005_rename_themes", "moved": moved,
            "total": sum(moved.values())}


def m0006_restore_journal_habit() -> dict:
    """Bring the journal back as a non-negotiable habit, and backfill it.

    This deliberately reverses migration `0001`. The reasoning there was that a
    journal is a status rather than a habit, and that counting it inflated the
    denominator. The product decision has changed: writing the day up *is* one
    of the non-negotiables, and it completes only when all five questions are
    answered — so it behaves exactly like the prayer habit, mirroring its module
    instead of being tickable by hand.

    For each workspace this un-archives the old habit if one exists (keeping
    every log it already had), creates it if not, and then recomputes its logs
    from the JournalEntry rows, which are never modified.
    """
    from sqlalchemy import func, select as sql_select

    from db import Habit, JournalEntry, Workspace
    import services as svc

    restored = created = backfilled = 0

    with SessionLocal() as s:
        for ws_id in s.scalars(sql_select(Workspace.id)).all():
            habit = s.scalar(sql_select(Habit).where(
                Habit.workspace_id == ws_id,
                Habit.system_key == svc.SYSTEM_JOURNAL))

            if habit is None:
                # Slot it in after the other non-negotiables rather than at the
                # end, so the three derived habits stay together.
                top = s.scalar(sql_select(func.max(Habit.position)).where(
                    Habit.workspace_id == ws_id)) or 0
                habit = Habit(workspace_id=ws_id, name="Kundalik",
                              category="non_negotiable", position=top + 1,
                              is_protected=True,
                              system_key=svc.SYSTEM_JOURNAL)
                s.add(habit)
                created += 1
            elif habit.archived_at is not None:
                habit.archived_at = None
                # An older build called it "Summary"; the product calls it
                # Kundalik now, and the name of a derived habit is the contract.
                habit.name = "Kundalik"
                habit.category = "non_negotiable"
                habit.is_protected = True
                restored += 1
            s.commit()

            # Backfill from what the user actually wrote.
            for day in s.scalars(sql_select(JournalEntry.day).where(
                    JournalEntry.workspace_id == ws_id)).all():
                if svc.sync_journal_habit(s, ws_id, day):
                    backfilled += 1

    return {"migration": "0006_restore_journal_habit",
            "unarchived": restored, "created": created,
            "days_marked_done": backfilled}


#: The redesign replaced the five themes with five complete visual systems, so
#: the names changed with them. Mapped by intent, not by hue: whoever chose the
#: restrained dark one still lands on a restrained dark one.
THEME_REDESIGN = {
    # The set 0005 produced.
    "ocean": "calm",        # the default blue -> the new default blue
    "pure": "calm",         # paper and charcoal -> the light, minimal one
    "midnight": "titan",    # graphite and steel -> obsidian and steel
    "sage": "muse",         # warm, soft, low contrast -> warm and elegant
    "aurora": "nexus",      # violet gradient -> indigo to cyan
    # Anything that never ran 0005 lands correctly in one step.
    "cobalt": "calm",
    "slate": "titan",
    "oxford": "calm",
    "blossom": "muse",
    "obsidian": "titan",
    "emerald": "muse",
    "rose": "muse",
    "pink": "muse",
}


def m0007_redesign_themes() -> dict:
    """Move every account onto the redesigned theme set.

    A row holding an old name is not broken — the app reads anything unknown as
    the default — but the stored value and the Settings screen would disagree
    until the user picked something, so it is rewritten once here.

    Nobody is moved onto `rage`: it has no predecessor, and assigning a theme
    called "execution mode" to someone who never asked for it is not a
    migration, it is a decision on their behalf.
    """
    from sqlalchemy import update

    from db import User

    moved: dict[str, int] = {}
    with SessionLocal() as s:
        for old, new in THEME_REDESIGN.items():
            count = s.execute(
                update(User).where(User.theme == old).values(theme=new)).rowcount
            if count:
                moved[f"{old}→{new}"] = count
        s.commit()
    return {"migration": "0007_redesign_themes", "moved": moved,
            "total": sum(moved.values())}


MIGRATIONS = {
    "0001": m0001_retire_summary_habit,
    "0002": m0002_retire_goals,
    "0003": m0003_retire_themes,
    "0004": m0004_recompute_prayer_completion,
    "0005": m0005_rename_themes,
    "0006": m0006_restore_journal_habit,
    "0007": m0007_redesign_themes,
}


def run(*names: str) -> list[dict]:
    """Apply the named migrations, or all of them in order."""
    db.init_db()
    chosen = names or tuple(sorted(MIGRATIONS))
    results = []
    for name in chosen:
        fn = MIGRATIONS.get(name)
        if fn is None:
            raise SystemExit(f"unknown migration: {name}")
        result = fn()
        log.info("%s", result)
        results.append(result)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for outcome in run(*sys.argv[1:]):
        print(outcome)
