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
    """Stop counting the journal as a habit (requirement 07).

    Early workspaces were seeded with a seventh habit, `Summary`, driven by
    journal completion. Journal completion is a status, so leaving it in the
    habit list inflates both the denominator and the streak.

    The habit is archived rather than deleted: its HabitLog rows stay on disk,
    JournalEntry history is untouched, and archiving is exactly what excludes
    it from `habit_progress`, which counts only `archived_at IS NULL`.
    """
    archived = 0
    with SessionLocal() as s:
        rows = s.scalars(select(Habit).where(
            Habit.system_key == "journal",
            Habit.archived_at.is_(None))).all()
        for habit in rows:
            habit.archived_at = utcnow()
            archived += 1
        s.commit()
    return {"migration": "0001_retire_summary_habit", "archived": archived}


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
RETIRED_THEMES = {
    "ocean": "cobalt",
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


MIGRATIONS = {
    "0001": m0001_retire_summary_habit,
    "0002": m0002_retire_goals,
    "0003": m0003_retire_themes,
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
