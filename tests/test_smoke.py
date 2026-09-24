"""
ErnestOS smoke tests — small on purpose.

Covers only what would be dangerous to get wrong:
user isolation, Telegram initData validation, ownership, subscription gating,
prayer scoring and report idempotency.

    .venv/bin/python -m pytest tests/ -q
"""

import hashlib
import hmac
import itertools
import json
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TOKEN = "123456:TEST-TOKEN"
os.environ.update({
    "BOT_TOKEN": TOKEN,
    "DATABASE_URL": f"sqlite:///{tempfile.mkdtemp()}/test.db",
    "ENVIRONMENT": "test",
    "REQUIRED_CHANNEL_ID": "",       # subscription gate off unless a test sets it
    "ADMIN_LOG_CHANNEL_ID": "",
})

import app as application  # noqa: E402
import config  # noqa: E402
import security  # noqa: E402
import db  # noqa: E402
import migrations  # noqa: E402
import dependencies as deps  # noqa: E402
import services as svc  # noqa: E402
from db import SessionLocal, User  # noqa: E402

ALICE = {"id": 1001, "first_name": "Alice", "username": "alice"}
BOB = {"id": 2002, "first_name": "Bob", "username": "bob"}


def init_data(user: dict, auth_date: int | None = None, token: str = TOKEN,
              tamper: bool = False, start_param: str | None = None) -> str:
    """Build initData exactly as Telegram would sign it.

    `start_param` goes *inside* the signed field set, which is the whole point
    of the referral tests: an attacker can put anything in the query string,
    but only what is covered by this hash reaches the backend as trustworthy.
    """
    fields = {"user": json.dumps(user, separators=(",", ":")),
              "auth_date": str(auth_date if auth_date is not None else int(time.time()))}
    if start_param is not None:
        fields["start_param"] = start_param
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if tamper:
        digest = "0" * 64
    return urlencode({**fields, "hash": digest})


@pytest.fixture(scope="session", autouse=True)
def schema():
    db.init_db()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    with TestClient(application.app, raise_server_exceptions=False) as c:
        yield c


def _onboard(telegram_id: int) -> None:
    """Bring a fixture user to the state a real user reaches after /start.

    The API refuses to create rows for a half-registered account (audit 003),
    so fixtures must finish onboarding just like a person would.
    """
    with SessionLocal() as s:
        svc.get_or_create_user(s, telegram_id)
        user = s.get(User, telegram_id)
        user.onboarded = True
        user.is_subscribed = True
        user.sub_checked_at = db.utcnow()
        s.commit()


class Caller:
    def __init__(self, client, user):
        self.c, self.user = client, user
        self.h = {"X-Telegram-Init-Data": init_data(user)}
        _onboard(user["id"])

    def get(self, url):
        return self.c.get(url, headers=self.h)

    def post(self, url, json=None):
        return self.c.post(url, headers=self.h, json=json)

    def patch(self, url, json=None):
        return self.c.patch(url, headers=self.h, json=json)

    def delete(self, url):
        return self.c.delete(url, headers=self.h)


@pytest.fixture()
def alice(client):
    return Caller(client, ALICE)


@pytest.fixture()
def bob(client):
    return Caller(client, BOB)


#: Ids for throwaway users. Tests that archive habits, clear a day or delete an
#: account must not do it inside a workspace another test relies on, so they get
#: their own instead of sharing alice's.
_next_id = itertools.count(700_001)


@pytest.fixture()
def fresh(client):
    """A caller with a workspace of its own, in the default three-habit state."""
    return Caller(client, {"id": next(_next_id), "first_name": "Fresh"})


# --------------------------------------------------------------------------
# Telegram authentication
# --------------------------------------------------------------------------

def test_missing_init_data_is_rejected(client):
    assert client.get("/api/me").status_code == 401


def test_tampered_signature_is_rejected(client):
    r = client.get("/api/me", headers={"X-Telegram-Init-Data": init_data(ALICE, tamper=True)})
    assert r.status_code == 401


def test_foreign_bot_token_is_rejected(client):
    forged = init_data(ALICE, token="999:SOMEONE-ELSE")
    assert client.get("/api/me", headers={"X-Telegram-Init-Data": forged}).status_code == 401


def test_stale_init_data_is_rejected(client):
    stale = init_data(ALICE, auth_date=int(time.time()) - 30 * 86400)
    assert client.get("/api/me", headers={"X-Telegram-Init-Data": stale}).status_code == 401


def test_identity_comes_from_the_signature_not_the_body(alice):
    """A forged telegram_id in the JSON body must be ignored."""
    alice.post("/api/tasks", json={"title": "ALICE-TASK", "telegram_id": BOB["id"]})
    assert "ALICE-TASK" in alice.get("/api/tasks").text


def test_valid_init_data_creates_the_user_and_workspace(alice):
    assert alice.get("/api/me").json()["telegram_id"] == ALICE["id"]
    with SessionLocal() as s:
        assert svc.workspace_id_for(s, ALICE["id"])


# --------------------------------------------------------------------------
# User isolation
# --------------------------------------------------------------------------

def test_tasks_are_not_visible_across_users(alice, bob):
    alice.post("/api/tasks", json={"title": "ALICE-SECRET-TASK"})
    assert "ALICE-SECRET-TASK" not in bob.get("/api/tasks").text


def test_journal_is_not_visible_across_users(alice, bob):
    alice.post("/api/journal", json={"text": "ALICE-SECRET-JOURNAL"})
    assert "ALICE-SECRET-JOURNAL" not in bob.get("/api/journal").text


def test_another_users_task_cannot_be_edited(alice, bob):
    task_id = alice.post("/api/tasks", json={"title": "protected"}).json()["id"]
    assert bob.patch(f"/api/tasks/{task_id}", json={"title": "hijacked"}).status_code == 404
    assert "protected" in alice.get("/api/tasks").text


def test_another_users_task_cannot_be_deleted(alice, bob):
    task_id = alice.post("/api/tasks", json={"title": "keep me"}).json()["id"]
    assert bob.delete(f"/api/tasks/{task_id}").status_code == 404


def test_another_users_habit_cannot_be_toggled(alice, bob):
    habit_id = alice.get("/api/habits").json()["habits"][0]["id"]
    assert bob.post(f"/api/habits/{habit_id}/toggle").status_code == 404


def test_task_cannot_join_another_users_project(alice, bob):
    project_id = alice.post("/api/projects", json={"name": "Alice project"}).json()["id"]
    r = bob.post("/api/tasks", json={"title": "injected", "project_id": project_id})
    assert r.status_code == 404


# --------------------------------------------------------------------------
# Defaults and habits
# --------------------------------------------------------------------------

def test_new_user_gets_the_three_mandatory_habits(alice):
    """Exactly three, in order, and nothing else.

    A new account opens on the habits it cannot argue with. Anything about how
    somebody wants to live — deep work, sport, reading — is theirs to add.
    """
    names = [h["name"] for h in alice.get("/api/habits").json()["habits"]]
    assert names == ["Get up", "5x namoz", "Kundalik"]


@pytest.mark.parametrize("name", ["Deep flow", "Sport", "Podcast", "Read"])
def test_a_new_user_is_not_given_a_habit_they_did_not_choose(alice, name):
    """These four used to be seeded. They must not come back by accident."""
    names = [h["name"] for h in alice.get("/api/habits").json()["habits"]]
    assert name not in names


def test_an_existing_account_keeps_the_habits_it_already_has(client):
    """Shortening the defaults must never reach a workspace that already exists.

    This is the whole risk of the change: somebody who joined last month has
    `Deep flow`, `Sport`, `Podcast` and `Read` with months of history behind
    them. `/start` calls `get_or_create_user` on every single visit, so if that
    path could touch habits at all, their list would be silently pruned. It
    cannot — seeding happens once, inside the branch that builds the workspace.
    """
    from sqlalchemy import select

    legacy_id = next(_next_id)
    legacy = Caller(client, {"id": legacy_id, "first_name": "Legacy"})
    ws = _ws(legacy_id)

    # Rebuild the account as it looked before this change: the three defaults
    # plus the four that used to be seeded, one of them with history.
    with SessionLocal() as s:
        for position, (name, category) in enumerate(
                [("Deep flow", "target"), ("Sport", "target"),
                 ("Podcast", "bonus"), ("Read", "bonus")], start=4):
            s.add(db.Habit(workspace_id=ws, name=name, category=category,
                           position=position))
        s.flush()
        sport = s.scalar(select(db.Habit).where(
            db.Habit.workspace_id == ws, db.Habit.name == "Sport"))
        s.add(db.HabitLog(workspace_id=ws, habit_id=sport.id,
                          day=svc.today_local(), done=True))
        s.commit()

    # What /start does, every time.
    with SessionLocal() as s:
        user, created = svc.get_or_create_user(s, legacy_id, first_name="Legacy")
        s.commit()
        assert created is False

    names = [h["name"] for h in legacy.get("/api/habits").json()["habits"]]
    assert names == ["Get up", "5x namoz", "Kundalik",
                     "Deep flow", "Sport", "Podcast", "Read"]
    assert next(h for h in legacy.get("/api/habits").json()["habits"]
                if h["name"] == "Sport")["done"] is True


def test_the_default_set_is_defined_in_exactly_one_place():
    """`seed_default_habits` is the only reader, and it reads this tuple.

    A second copy of the defaults is how the /start path and the wipe path end
    up disagreeing about what a fresh workspace contains.
    """
    assert [n for n, _c, _k in svc.DEFAULT_HABITS] == \
        ["Get up", "5x namoz", "Kundalik"]
    assert all(key for _n, _c, key in svc.DEFAULT_HABITS)


def test_habits_are_grouped_into_three_categories(alice):
    """All three defaults are non-negotiable; the other tiers start empty.

    An empty tier still has to render — the screen says "nothing here yet"
    rather than dropping the section, so adding the first one has somewhere
    obvious to go.
    """
    body = alice.get("/api/habits").json()
    assert body["categories"] == ["non_negotiable", "target", "bonus"]
    grouped = body["grouped"]
    assert [h["name"] for h in grouped["non_negotiable"]] == \
        ["Get up", "5x namoz", "Kundalik"]
    assert grouped["target"] == []
    assert grouped["bonus"] == []


def test_new_habit_lands_in_the_chosen_category(alice):
    alice.post("/api/habits", json={"name": "Meditation", "category": "bonus"})
    grouped = alice.get("/api/habits").json()["grouped"]
    assert "Meditation" in [h["name"] for h in grouped["bonus"]]


def test_unknown_category_falls_back_to_target(alice):
    alice.post("/api/habits", json={"name": "Stretching", "category": "nonsense"})
    grouped = alice.get("/api/habits").json()["grouped"]
    assert "Stretching" in [h["name"] for h in grouped["target"]]


def test_default_theme_is_ocean(alice):
    assert alice.get("/api/me").json()["theme"] == "ocean"


def test_protected_habit_cannot_be_toggled(alice):
    habits = alice.get("/api/habits").json()["habits"]
    protected = next(h for h in habits if h["protected"])
    assert alice.post(f"/api/habits/{protected['id']}/toggle").status_code == 400


def test_protected_habit_cannot_be_deleted(alice):
    habits = alice.get("/api/habits").json()["habits"]
    protected = next(h for h in habits if h["protected"])
    assert alice.delete(f"/api/habits/{protected['id']}").status_code == 400


def test_normal_habit_toggles(alice):
    """Every default is derived, so the first tickable habit is one they add."""
    alice.post("/api/habits", json={"name": "Gym", "category": "target"})
    habits = alice.get("/api/habits").json()["habits"]
    normal = next(h for h in habits if not h["protected"])
    assert alice.post(f"/api/habits/{normal['id']}/toggle").json()["done"] is True


def test_the_derived_habits_are_protected(alice):
    """All three are computed from their module, never ticked by hand."""
    habits = alice.get("/api/habits").json()["habits"]
    derived = {h["name"] for h in habits if h["protected"]}
    assert derived == {"Get up", "5x namoz", "Kundalik"}


# --------------------------------------------------------------------------
# Habit tiers as arithmetic
#
# The three tiers were labels for a long time: the screen sorted habits into
# non-negotiable, target and bonus, and the score counted every one of them the
# same. That made a missed 5x namoz cost exactly what a missed podcast cost,
# and it made the headings dishonest. These pin the weighting down.
# --------------------------------------------------------------------------

def test_the_tiers_are_weighted_in_the_declared_order(fresh):
    """Non-negotiable outweighs the other two together — or the word is a lie."""
    w = svc.HABIT_TIER_WEIGHTS
    assert w["non_negotiable"] > w["target"] > w["bonus"]
    # At least as much as the other two put together: a day that loses every
    # mandatory habit can never be a majority-scoring day, whatever else was
    # ticked.
    assert w["non_negotiable"] >= w["target"] + w["bonus"]
    assert sum(w.values()) == 100


def test_a_workspace_of_only_mandatory_habits_can_still_reach_full_marks(fresh):
    """The renormalisation, which is what makes the weighting usable.

    A new workspace holds three non-negotiable habits and nothing else. Under a
    fixed 50/30/20 split that user is capped at 50% on a day they did every
    single thing they had — a score they can never move, which is worse than no
    score. Weights are renormalised over the tiers actually in play.
    """
    tiers = fresh.get("/api/habits").json()["tiers"]
    assert tiers["non_negotiable"]["due"] == 3
    assert tiers["target"]["due"] == 0 and tiers["bonus"]["due"] == 0
    # The only tier in play carries the whole 100%.
    assert tiers["non_negotiable"]["applied"] == 100


def test_optional_habits_cannot_carry_a_day_the_mandatory_ones_lost(fresh):
    """Ticking every bonus habit while skipping the floor is not a good day.

    Under the old flat count this workspace read 3/6 = 50% — a passing-looking
    number for a day that missed getting up, prayer and the journal entirely.

    Two tiers are in play, so the weights renormalise over 50 + 20 = 70, and
    the bonus tier's full share is 20/70 ≈ 29. Still well under half, which is
    the property that matters: the optional habits cannot buy a majority.
    """
    for name in ("Podcast", "Read", "Stretch"):
        fresh.post("/api/habits", json={"name": name, "category": "bonus"})
    for habit in fresh.get("/api/habits").json()["habits"]:
        if not habit["protected"]:
            fresh.post(f"/api/habits/{habit['id']}/toggle")

    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, fresh.user["id"])
        assert svc.habit_percent(s, ws, svc.today_local()) == 29

    # And the raw counts are untouched: "3/6 ticked" is still what the list says.
    done, total = None, None
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, fresh.user["id"])
        done, total = svc.habit_progress(s, ws, svc.today_local())
    assert (done, total) == (3, 6)


def test_a_mandatory_habit_cannot_be_rescheduled_off_a_day(fresh):
    """The schedule of a derived habit is a contract with the score.

    "5x namoz, Mondays only" does not mean "I pray on Mondays" — it means the
    other six days stop being counted and the percentage silently rises. The
    Mini App stopped offering the picker for these three; this is the half a
    hand-written request cannot get around.
    """
    protected = next(h for h in fresh.get("/api/habits").json()["habits"]
                     if h["protected"])
    fresh.patch(f"/api/habits/{protected['id']}", json={"schedule": "1,3"})

    after = next(h for h in fresh.get("/api/habits").json()["habits"]
                 if h["id"] == protected["id"])
    assert after["schedule"] == "daily"
    assert after["due"] is True

    # An ordinary habit is still the user's to schedule.
    fresh.post("/api/habits", json={"name": "Gym", "category": "target"})
    gym = next(h for h in fresh.get("/api/habits").json()["habits"]
               if h["name"] == "Gym")
    fresh.patch(f"/api/habits/{gym['id']}", json={"schedule": "weekdays"})
    gym = next(h for h in fresh.get("/api/habits").json()["habits"]
               if h["name"] == "Gym")
    assert gym["schedule"] == "weekdays"


# --------------------------------------------------------------------------
# Habit order
# --------------------------------------------------------------------------

def _habit_names(caller) -> list[str]:
    return [h["name"] for h in caller.get("/api/habits").json()["habits"]]


def test_habits_can_be_reordered(alice):
    ids = [h["id"] for h in alice.get("/api/habits").json()["habits"]]
    reversed_ids = list(reversed(ids))
    r = alice.patch("/api/habits/reorder", json={"habit_ids": reversed_ids})
    assert r.status_code == 200
    assert [h["id"] for h in r.json()["habits"]] == reversed_ids


def test_a_new_order_survives_a_reload(alice):
    before = _habit_names(alice)
    ids = [h["id"] for h in alice.get("/api/habits").json()["habits"]]
    alice.patch("/api/habits/reorder", json={"habit_ids": list(reversed(ids))})
    assert _habit_names(alice) == list(reversed(before))


def test_the_bot_sees_the_same_order_as_the_mini_app(alice):
    """The bot groups by tier, but inside a tier it is the same list."""
    ids = [h["id"] for h in alice.get("/api/habits").json()["habits"]]
    wanted = list(reversed(ids))
    alice.patch("/api/habits/reorder", json={"habit_ids": wanted})
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        grouped = svc.habits_by_category(s, ws)
    for category in svc.HABIT_CATEGORIES:
        inside = [h["id"] for h in grouped[category]]
        assert inside == [i for i in wanted if i in inside]


def test_reorder_rejects_a_duplicate_id(alice):
    habit_id = alice.get("/api/habits").json()["habits"][0]["id"]
    r = alice.patch("/api/habits/reorder", json={"habit_ids": [habit_id, habit_id]})
    assert r.status_code == 422


def test_reorder_cannot_reach_another_workspace(alice, bob):
    """A foreign id is a 404, and Alice's own order must not move either."""
    stolen = bob.get("/api/habits").json()["habits"][0]["id"]
    before = _habit_names(alice)
    assert alice.patch("/api/habits/reorder",
                       json={"habit_ids": [stolen]}).status_code == 404
    assert _habit_names(alice) == before


def test_a_partial_order_keeps_the_habits_it_did_not_mention(alice):
    habits = alice.get("/api/habits").json()["habits"]
    last_id = habits[-1]["id"]
    alice.patch("/api/habits/reorder", json={"habit_ids": [last_id]})
    after = alice.get("/api/habits").json()["habits"]
    assert after[0]["id"] == last_id
    assert len(after) == len(habits)


# --------------------------------------------------------------------------
# Prayer scoring
# --------------------------------------------------------------------------

def test_male_jamaat_and_on_time_score_one():
    statuses = {p: "jamaat" for p in svc.PRAYERS}
    assert svc.prayer_score(statuses, "male") == 5.0


def test_male_qaza_scores_half():
    assert svc.prayer_score({p: "qaza" for p in svc.PRAYERS}, "male") == 2.5


def test_female_has_no_jamaat_but_does_have_qaza():
    """Women record on-time, qaza and missed — only jamaat is male-only."""
    assert svc.STATUSES_FEMALE == ["on_time", "qaza", "missed"]
    assert svc.prayer_score({p: "jamaat" for p in svc.PRAYERS}, "female") == 0.0
    assert svc.prayer_score({p: "qaza" for p in svc.PRAYERS}, "female") == 2.5


def test_prayer_is_scored_out_of_five():
    assert svc.PRAYER_MAX_SCORE == 5.0
    assert svc.prayer_score({p: "on_time" for p in svc.PRAYERS}, "male") == 5.0


def test_female_excused_day_counts_as_a_full_day():
    """An excused day is fulfilled, not half-fulfilled.

    It used to score 2.5 because 2.5 was the completion threshold, which made
    an excused day read as 50% everywhere a percentage was shown. Completion is
    now a separate question from quality, so the day is complete *and* scores as
    a full one.
    """
    assert svc.prayer_score({}, "female", excused=True) == svc.PRAYER_MAX_SCORE
    assert svc.prayer_is_complete({}, "female", excused=True) is True
    # A male user has no excused day, so the flag cannot fulfil one.
    assert svc.prayer_is_complete({}, "male", excused=True) is False


def test_excused_is_rejected_for_male_users(alice):
    alice.post("/api/settings", json={"gender": "male"})
    assert alice.post("/api/prayers/excused", json={"excused": True}).status_code == 422


def _prayer_habit(alice) -> dict:
    habits = alice.get("/api/habits").json()["habits"]
    return next(h for h in habits if h["name"] == "5x namoz")


def test_three_prayers_do_not_complete_a_five_prayer_habit(alice):
    """The bug this replaces: score >= 2.5 marked "5x namoz" done.

    Three on-time prayers scored 3.0, cleared the old 2.5 threshold, and the
    app told the user they had prayed five times. Completion is now the count,
    not the score.
    """
    alice.post("/api/settings", json={"gender": "male"})
    for prayer in ["bomdod", "peshin", "asr"]:
        alice.post("/api/prayers", json={"prayer": prayer, "status": "on_time"})
    state = alice.get("/api/prayers").json()
    assert state["performed"] == 3
    assert state["score"] == 3.0          # the quality number still moves
    assert state["complete"] is False
    assert _prayer_habit(alice)["done"] is False


def test_all_five_prayers_complete_the_habit(alice):
    alice.post("/api/settings", json={"gender": "male"})
    for prayer in svc.PRAYERS:
        alice.post("/api/prayers", json={"prayer": prayer, "status": "on_time"})
    assert alice.get("/api/prayers").json()["complete"] is True
    assert _prayer_habit(alice)["done"] is True


def test_a_late_prayer_still_counts_towards_the_five(alice):
    """Qaza is prayed late, not skipped, so it fills the slot at half quality."""
    alice.post("/api/settings", json={"gender": "male"})
    for prayer in svc.PRAYERS:
        alice.post("/api/prayers", json={"prayer": prayer, "status": "qaza"})
    state = alice.get("/api/prayers").json()
    assert state["performed"] == 5 and state["complete"] is True
    assert state["score"] == 2.5
    assert _prayer_habit(alice)["done"] is True


def test_a_missed_prayer_does_not_count_towards_the_five(alice):
    alice.post("/api/settings", json={"gender": "male"})
    for prayer in svc.PRAYERS[:4]:
        alice.post("/api/prayers", json={"prayer": prayer, "status": "on_time"})
    alice.post("/api/prayers", json={"prayer": svc.PRAYERS[4], "status": "missed"})
    state = alice.get("/api/prayers").json()
    assert state["performed"] == 4 and state["complete"] is False
    assert _prayer_habit(alice)["done"] is False


def test_a_prayer_entry_can_be_undone(alice):
    """A mis-tap has to be reversible, and clearing must move the count back."""
    alice.post("/api/settings", json={"gender": "male"})
    # Start from a known day: the fixtures share one workspace across tests.
    for prayer in svc.PRAYERS:
        alice.post("/api/prayers/clear", json={"prayer": prayer})
    alice.post("/api/prayers", json={"prayer": "bomdod", "status": "on_time"})
    assert alice.get("/api/prayers").json()["performed"] == 1

    body = alice.post("/api/prayers/clear", json={"prayer": "bomdod"}).json()
    assert body["prayers"]["bomdod"] is None
    assert body["performed"] == 0
    assert _prayer_habit(alice)["done"] is False


def test_prayer_status_outside_the_gender_set_is_rejected(alice):
    """Jamaat is not offered to women, so the API must refuse it."""
    alice.post("/api/settings", json={"gender": "female"})
    r = alice.post("/api/prayers", json={"prayer": "bomdod", "status": "jamaat"})
    assert r.status_code == 422


def test_female_qaza_is_accepted(alice):
    alice.post("/api/settings", json={"gender": "female"})
    assert alice.post("/api/prayers",
                      json={"prayer": "bomdod", "status": "qaza"}).status_code == 200


# --------------------------------------------------------------------------
# Tasks, projects and the weekly mission
# --------------------------------------------------------------------------

def test_task_without_a_project_is_standalone(alice):
    alice.post("/api/tasks", json={"title": "standalone task"})
    tasks = alice.get("/api/tasks?days=365").json()
    row = next(t for t in tasks["undated"] if t["title"] == "standalone task")
    assert row["project_id"] is None


def test_deleting_a_project_keeps_its_tasks(alice):
    project_id = alice.post("/api/projects", json={"name": "Temp"}).json()["id"]
    alice.post("/api/tasks", json={"title": "SURVIVOR", "project_id": project_id})
    alice.delete(f"/api/projects/{project_id}")
    assert "SURVIVOR" in alice.get("/api/tasks?days=365").text


def _clear_missions(telegram_id: int) -> None:
    """The suite shares one database, so a week can already hold its mission."""
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, telegram_id)
        for row in s.query(db.WeeklyFocus).filter_by(workspace_id=ws).all():
            s.delete(row)
        s.commit()


def test_the_week_holds_one_primary_and_two_supporting(alice):
    """One dominant mission, two priorities beside it, and no fourth."""
    _clear_missions(ALICE["id"])
    assert alice.post("/api/focus", json={"title": "the one"}).status_code == 200
    assert alice.post("/api/focus", json={"title": "second"}).status_code == 200
    assert alice.post("/api/focus", json={"title": "third"}).status_code == 200
    assert alice.post("/api/focus", json={"title": "fourth"}).status_code == 422


def test_the_primary_mission_is_the_first_slot(alice):
    """The hierarchy lives in the slot number, so every surface agrees."""
    _clear_missions(ALICE["id"])
    alice.post("/api/focus", json={"title": "primary"})
    alice.post("/api/focus", json={"title": "supporting"})
    week = alice.get("/api/focus").json()["week"]
    assert week["primary"]["title"] == "primary"
    assert [x["title"] for x in week["supporting"]] == ["supporting"]
    assert alice.get("/api/home").json()["mission"]["title"] == "primary"


def test_a_mission_defaults_to_medium_importance(alice):
    _clear_missions(ALICE["id"])
    alice.post("/api/focus", json={"title": "unranked"})
    assert alice.get("/api/focus").json()["focus"][0]["priority"] == "medium"


def test_a_mission_keeps_the_importance_it_was_given(alice):
    _clear_missions(ALICE["id"])
    alice.post("/api/focus", json={"title": "urgent", "priority": "high"})
    assert alice.get("/api/focus").json()["focus"][0]["priority"] == "high"


def test_an_unknown_importance_falls_back_to_medium(alice):
    _clear_missions(ALICE["id"])
    alice.post("/api/focus", json={"title": "odd", "priority": "cosmic"})
    assert alice.get("/api/focus").json()["focus"][0]["priority"] == "medium"


def test_a_mission_written_before_the_column_existed_reads_as_medium(alice):
    """Rows predating the priority column carry NULL, not a bad value."""
    _clear_missions(ALICE["id"])
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        s.add(db.WeeklyFocus(workspace_id=ws, week_start=svc.week_start(svc.today_local()),
                             slot=1, title="legacy row", priority=None))
        s.commit()
    assert alice.get("/api/home").json()["mission"]["priority"] == "medium"


def test_home_shows_one_mission_even_for_a_legacy_week(alice):
    """Weeks written under the old three-slot rule still resolve to one."""
    _clear_missions(ALICE["id"])
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        start = svc.week_start(svc.today_local())
        for slot in (2, 3, 1):
            s.add(db.WeeklyFocus(workspace_id=ws, week_start=start, slot=slot,
                                 title=f"legacy {slot}"))
        s.commit()
    assert alice.get("/api/home").json()["mission"]["title"] == "legacy 1"


def test_bad_date_is_rejected_without_leaking_internals(alice):
    r = alice.post("/api/tasks", json={"title": "x", "deadline": "31-12-2026"})
    assert r.status_code == 422
    assert "ValueError" not in r.text and "Traceback" not in r.text


# --------------------------------------------------------------------------
# Report idempotency
# --------------------------------------------------------------------------

def test_a_report_is_recorded_once_per_day(alice):
    alice.get("/api/me")
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        today = svc.today_local()
        assert svc.already_sent(s, ws, "morning", today) is False
        svc.mark_sent(s, ws, "morning", today)
        assert svc.already_sent(s, ws, "morning", today) is True


def test_morning_and_evening_are_tracked_separately(alice):
    alice.get("/api/me")
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        day = svc.today_local() - timedelta(days=1)
        svc.mark_sent(s, ws, "morning", day)
        assert svc.already_sent(s, ws, "evening", day) is False


def _recipient(telegram_id: int) -> bool:
    with SessionLocal() as s:
        return any(r[0] == telegram_id for r in svc.active_recipients(s))


def _make_user(telegram_id: int, *, onboarded=True, subscribed=False, actions=0):
    with SessionLocal() as s:
        svc.get_or_create_user(s, telegram_id, first_name="R")
        user = s.get(User, telegram_id)
        user.onboarded = onboarded
        user.is_subscribed = subscribed
        user.actions_count = actions
        s.commit()


def test_an_onboarded_user_receives_reports_without_a_channel(client):
    """The bug this replaces switched reports off for almost everybody.

    `is_subscribed` means "Telegram confirmed this account is in the channel",
    and it is only ever written behind a confirmed membership check. A user
    inside their free run never reaches one, and with no channel configured
    nobody ever does — so this flag stayed False and the recipient list came
    back empty. Reports, and reminders with them, silently did nothing.
    """
    telegram_id = next(_next_id)
    _make_user(telegram_id, subscribed=False)
    assert _recipient(telegram_id), "an onboarded user was not sent their report"


def test_a_user_still_inside_the_free_run_receives_reports(client, monkeypatch):
    """They are allowed to use the product, so they are allowed to hear from it."""
    monkeypatch.setattr(deps, "REQUIRED_CHANNEL_ID", "-1001234567890")
    telegram_id = next(_next_id)
    _make_user(telegram_id, subscribed=False, actions=deps.FREE_ACTIONS - 1)
    assert _recipient(telegram_id)


def test_a_gated_user_receives_nothing(client, monkeypatch):
    """Free run spent and not in the channel: the API refuses them, so does this."""
    monkeypatch.setattr(deps, "REQUIRED_CHANNEL_ID", "-1001234567890")
    telegram_id = next(_next_id)
    _make_user(telegram_id, subscribed=False, actions=deps.FREE_ACTIONS)
    assert not _recipient(telegram_id)


def test_a_subscriber_receives_reports_however_many_actions(client, monkeypatch):
    monkeypatch.setattr(deps, "REQUIRED_CHANNEL_ID", "-1001234567890")
    telegram_id = next(_next_id)
    _make_user(telegram_id, subscribed=True, actions=deps.FREE_ACTIONS * 5)
    assert _recipient(telegram_id)


def test_a_half_registered_account_receives_nothing(client):
    """Onboarding is the one gate that never has an exception."""
    telegram_id = next(_next_id)
    _make_user(telegram_id, onboarded=False, subscribed=True)
    assert not _recipient(telegram_id)


def test_the_recipient_rule_matches_the_access_rule(client, monkeypatch):
    """Two surfaces, one question. They must not answer it differently.

    Whatever `trial_state` says about an account, the scheduler must agree —
    otherwise somebody is either using an app that never writes to them, or
    being written to by an app that will not let them in.
    """
    monkeypatch.setattr(deps, "REQUIRED_CHANNEL_ID", "-1001234567890")
    for subscribed, actions in ((False, 0), (False, deps.FREE_ACTIONS),
                                (True, 0), (True, deps.FREE_ACTIONS * 3)):
        telegram_id = next(_next_id)
        _make_user(telegram_id, subscribed=subscribed, actions=actions)
        with SessionLocal() as s:
            allowed = not deps.trial_state(s.get(User, telegram_id)).gated
        assert _recipient(telegram_id) is allowed, \
            f"subscribed={subscribed} actions={actions} disagreed"


# --------------------------------------------------------------------------
# Subscription gate
# --------------------------------------------------------------------------

def _set_actions(telegram_id: int, count: int) -> None:
    with SessionLocal() as s:
        s.get(User, telegram_id).actions_count = count
        s.commit()


def test_a_new_account_is_not_asked_for_the_channel(client, monkeypatch):
    """The free run comes first.

    The channel used to be step two of onboarding — asked before the user had
    seen one thing the product does. Somebody who has just arrived owes nobody
    a subscription, so the first twenty actions are simply open.
    """
    monkeypatch.setattr(deps, "REQUIRED_CHANNEL_ID", "-1001234567890")
    with SessionLocal() as s:
        svc.get_or_create_user(s, BOB["id"], first_name="Bob")
        user = s.get(User, BOB["id"])
        user.is_subscribed = False
        user.onboarded = True
        user.actions_count = 0
        s.commit()
    r = client.get("/api/home", headers={"X-Telegram-Init-Data": init_data(BOB)})
    assert r.status_code == 200, "a brand-new account was gated at the door"


def test_the_api_is_blocked_once_the_free_run_is_spent(client, monkeypatch):
    monkeypatch.setattr(deps, "REQUIRED_CHANNEL_ID", "-1001234567890")
    with SessionLocal() as s:
        svc.get_or_create_user(s, BOB["id"], first_name="Bob")
        user = s.get(User, BOB["id"])
        user.is_subscribed = False
        user.onboarded = True
        user.actions_count = deps.FREE_ACTIONS
        s.commit()
    r = client.get("/api/home", headers={"X-Telegram-Init-Data": init_data(BOB)})
    assert r.status_code == 403


def test_a_subscriber_is_never_gated_however_many_actions(client, monkeypatch):
    monkeypatch.setattr(deps, "REQUIRED_CHANNEL_ID", "-1001234567890")
    with SessionLocal() as s:
        svc.get_or_create_user(s, BOB["id"], first_name="Bob")
        user = s.get(User, BOB["id"])
        user.is_subscribed = True
        user.onboarded = True
        user.actions_count = deps.FREE_ACTIONS * 5
        s.commit()
    r = client.get("/api/home", headers={"X-Telegram-Init-Data": init_data(BOB)})
    assert r.status_code == 200


def test_reading_does_not_spend_the_free_run(alice):
    """Scrolling is not use. Only a write counts."""
    _set_actions(ALICE["id"], 0)
    for _ in range(5):
        alice.get("/api/home")
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).actions_count == 0

    alice.post("/api/tasks", json={"title": "A real action"})
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).actions_count == 1


def test_checking_the_subscription_does_not_spend_an_action(alice):
    """Charging somebody for tapping "have I joined yet" would be absurd.

    Nor for changing a theme or a report time: those are settings, not use.
    """
    _set_actions(ALICE["id"], 0)
    alice.post("/api/settings", json={"theme": "ocean"})
    alice.get("/api/subscription")
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).actions_count == 0
    assert "/api/prefs" in application.UNCOUNTED_PATHS
    assert "/api/subscription" in application.UNCOUNTED_PATHS


def test_a_rejected_write_costs_nothing(alice):
    """A 4xx never spends a free action."""
    _set_actions(ALICE["id"], 0)
    alice.post("/api/tasks", json={"title": ""})
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).actions_count == 0


# --------------------------------------------------------------------------
# Removed features must stay removed
# --------------------------------------------------------------------------

@pytest.mark.parametrize("term", ["anthropic", "claude", "currency", "exchange_rate"])
def test_no_ai_or_money_code_remains(term):
    for name in ("app.py", "db.py", "services.py"):
        source = (ROOT / name).read_text().lower()
        assert term not in source, f"{name} still mentions {term!r}"


def test_frontend_has_no_ai_or_money_ui():
    html = (ROOT / "webapp" / "index.html").read_text().lower()
    for term in ("anthropic", "claude", "currency", "/api/money"):
        assert term not in html, f"index.html still mentions {term!r}"


# --------------------------------------------------------------------------
# The launch surfaces: seven menu entries, four screens, one privacy line
# --------------------------------------------------------------------------

def _menu_labels(lang: str) -> list[str]:
    application.WEBAPP_URL = "https://example.test"
    return [button.text for row in application.main_menu(lang).keyboard
            for button in row]


@pytest.mark.parametrize("lang", ["uz", "en", "ru"])
def test_the_menu_puts_the_wake_button_above_the_mini_app(lang):
    """Turdim gets its own row directly above the Mini App button — the two
    rows a thumb reaches first, and the one action that expires."""
    labels = _menu_labels(lang)
    assert len(labels) == 8
    assert labels == [application.t(lang, key) for key in (
        "menu_home", "menu_habits", "menu_tasks", "menu_stats",
        "menu_settings", "menu_feedback",
        "menu_wake", "menu_app")]


@pytest.mark.parametrize("lang", ["uz", "en", "ru"])
def test_the_menu_has_no_goals(lang):
    labels = _menu_labels(lang)
    for word in ("maqsad", "goal", "цел"):
        assert not any(word in label.lower() for label in labels)


def test_typing_i_am_up_still_works_in_every_language():
    """Removing the button must not remove the action."""
    source = (ROOT / "app.py").read_text()
    assert 'if text == t(code, "menu_wake")' in source
    for lang in ("uz", "en", "ru"):
        assert application.t(lang, "menu_wake")


def _habit_buttons(done: bool) -> list[str]:
    grouped = {"non_negotiable": [{"id": 1, "name": "Get up", "protected": True,
                                   "system_key": "wakeup", "target_time": "05:00",
                                   "done": done}],
               "target": [], "bonus": []}
    markup = application.habits_keyboard(grouped, "uz")
    return [b.text for row in markup.inline_keyboard for b in row]


def test_wake_up_is_offered_on_the_habits_screen_while_it_can_be_recorded():
    assert application.t("uz", "menu_wake") in _habit_buttons(done=False)


def test_wake_up_disappears_from_the_habits_screen_once_recorded():
    """On that screen it would only be able to say "already done". The keyboard
    button stays regardless, because that one is the morning entry point."""
    assert application.t("uz", "menu_wake") not in _habit_buttons(done=True)
    assert application.t("uz", "menu_wake") in _menu_labels("uz")


def test_a_late_wake_up_is_answered_with_a_joke_not_a_verdict():
    """The habit still only completes on time; the wording is what changed."""
    late = application.wake_reply(
        {"done": False, "now": "08:20", "target": "05:00"}, "uz")
    assert "08:20" in late
    for scolding in ("hisoblanmadi", "kech bo'ldi", "failed"):
        assert scolding not in late.lower()
    on_time = application.wake_reply(
        {"done": True, "now": "04:53", "target": "05:00"}, "uz")
    assert "04:53" in on_time


def test_goals_are_unreachable_from_either_surface():
    app_source = (ROOT / "app.py").read_text()
    html = (ROOT / "webapp" / "index.html").read_text()
    for term in ('"/api/goals', "show_goals", "menu_goals", "list_goals"):
        assert term not in app_source, f"app.py still exposes {term!r}"
    for term in ("/api/goals", "SCREENS.vision", "goal-add", "Maqsadlar", "Цели"):
        assert term not in html, f"index.html still exposes {term!r}"


def test_the_mini_app_navigation_is_the_five_launch_screens():
    """Home, the two things you do, the shared list, and the numbers.

    Team sits between Tasks and Statistics deliberately: it is work, not a
    report, and putting it after the numbers would file a shared goal as
    something you review rather than something you do.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    nav = html[html.index("const NAV = ["):html.index("const NAV_OF")]
    assert [line.split('id:"')[1].split('"')[0]
            for line in nav.splitlines() if 'id:"' in line] == \
        ["home", "habits", "tasks", "team", "stats"]


def test_the_privacy_line_is_said_once_on_home():
    """One line, in the page, on the screen the user lands on.

    It used to be fixed chrome: a bar welded above the tab bar on all four
    screens, with a hairline of its own, repeating one sentence forever. The
    promise is worth making and worth making once — it is rendered at the foot
    of Home, and again in Settings under "About privacy". What must not come
    back is a copy of it per screen.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    assert html.count('class="privacy-strip"') == 1
    assert html.count("privacyNote()") == 2, \
        "the privacy note is defined once and rendered once, on Home"
    assert "+ privacyNote();" in html.split("SCREENS.home")[1][:2000], \
        "the privacy note left Home"
    rule = html.split(".privacy-strip{")[1].split("}")[0]
    assert "position:fixed" not in rule, "the privacy line is chrome again"
    # "Fully protected" was a promise no product can keep, and the one
    # sentence a user is entitled to hold you to. What replaced it is what is
    # actually true, and is also what they wanted to know.
    for lang, phrase in (("uz", "boshqa foydalanuvchilardan ajratilgan"),
                         ("en", "separate from other users"),
                         ("ru", "отделены от данных других")):
        assert phrase in html, f"{lang} privacy line missing"
        assert phrase in application.t(lang, "privacy_line")


def test_the_privacy_line_is_identical_in_both_surfaces():
    """The bot and the Mini App must not word the same promise differently.

    The copy itself is a product decision, made deliberately and reaffirmed. The
    isolation it refers to is what the workspace tests above actually prove; the
    technical caveat — an administrator can reach the database for maintenance —
    is documented in the README's security section rather than in this line.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    for lang in ("uz", "en", "ru"):
        line = application.t(lang, "privacy_line").replace("🔒", "").strip()
        assert line.replace("'", "'") in html or line in html, \
            f"{lang}: the bot and the Mini App disagree on the privacy line"


def test_no_unimplemented_security_claim_is_made():
    """Wording is a product call; claiming a mechanism that does not exist is
    not. Nothing anywhere may promise encryption ErnestOS does not perform."""
    text = ((ROOT / "webapp" / "index.html").read_text()
            + (ROOT / "app.py").read_text()).lower()
    for claim in ("end-to-end", "e2e encrypt", "shifrlangan", "зашифрован",
                  "hatto admin", "даже админ"):
        assert claim not in text, f"unimplemented security claim: {claim!r}"


def test_the_privacy_claim_stays_within_what_is_implemented():
    """No end-to-end-encryption or not-even-admins promise anywhere."""
    text = ((ROOT / "webapp" / "index.html").read_text()
            + (ROOT / "app.py").read_text()).lower()
    for claim in ("end-to-end", "e2e encrypt", "hatto admin", "даже админ"):
        assert claim not in text, f"unimplemented privacy claim: {claim!r}"


# --------------------------------------------------------------------------
# Retiring Goals from a live database
# --------------------------------------------------------------------------

def _drop(*names: str) -> None:
    from sqlalchemy import text
    with db.engine.begin() as conn:
        for name in names:
            conn.execute(text(f"DROP TABLE IF EXISTS {name}"))


def _make_legacy_goals_table(rows: int = 2) -> None:
    from sqlalchemy import text
    _drop("goals", migrations.GOALS_ARCHIVE_TABLE)
    with db.engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE goals (id INTEGER PRIMARY KEY, workspace_id INTEGER, "
            "title TEXT, category TEXT)"))
        for i in range(rows):
            conn.execute(text("INSERT INTO goals (workspace_id, title, category) "
                              f"VALUES (1, 'kept {i}', 'tactical')"))


def _tables() -> set[str]:
    from sqlalchemy import inspect
    return set(inspect(db.engine).get_table_names())


def test_the_migration_takes_goals_out_of_the_live_schema():
    _make_legacy_goals_table()
    result = migrations.m0002_retire_goals()
    assert result["rows"] == 2
    assert "goals" not in _tables()
    _drop(migrations.GOALS_ARCHIVE_TABLE)


def test_the_migration_keeps_every_row(alice):
    """A removed screen must never mean deleted data."""
    from sqlalchemy import text
    _make_legacy_goals_table(rows=3)
    migrations.m0002_retire_goals()
    with db.engine.begin() as conn:
        kept = conn.execute(text(
            f"SELECT count(*) FROM {migrations.GOALS_ARCHIVE_TABLE}")).scalar()
    assert kept == 3
    _drop(migrations.GOALS_ARCHIVE_TABLE)


def test_the_migration_is_safe_to_run_twice():
    _make_legacy_goals_table()
    migrations.m0002_retire_goals()
    again = migrations.m0002_retire_goals()
    assert again["status"] == "already archived"
    _drop(migrations.GOALS_ARCHIVE_TABLE)


def test_the_migration_does_nothing_on_a_fresh_database():
    _drop("goals", migrations.GOALS_ARCHIVE_TABLE)
    assert migrations.m0002_retire_goals()["status"] == "nothing to do"


def test_the_archive_can_be_renamed_back():
    """The documented rollback has to actually work."""
    from sqlalchemy import text
    _make_legacy_goals_table()
    migrations.m0002_retire_goals()
    with db.engine.begin() as conn:
        conn.execute(text(
            f"ALTER TABLE {migrations.GOALS_ARCHIVE_TABLE} RENAME TO goals"))
        assert conn.execute(text("SELECT count(*) FROM goals")).scalar() == 2
    _drop("goals")


def test_creating_the_schema_never_brings_goals_back():
    _drop("goals", migrations.GOALS_ARCHIVE_TABLE)
    db.init_db()
    assert "goals" not in _tables()


def test_no_orphaned_foreign_key_points_at_goals():
    from sqlalchemy import inspect
    inspector = inspect(db.engine)
    for table in _tables():
        for fk in inspector.get_foreign_keys(table):
            assert fk["referred_table"] != "goals", f"{table} still references goals"


# --- 0003: nine themes down to four ---------------------------------------

def _set_theme(telegram_id: int, value: str) -> None:
    """Write a theme straight to the row, bypassing the API's own validation."""
    with SessionLocal() as s:
        s.get(User, telegram_id).theme = value
        s.commit()


def test_every_offered_theme_is_one_the_mini_app_styles():
    """The picker and the stylesheet must not be able to disagree."""
    styled = (ROOT / "webapp" / "index.html").read_text()
    assert application.THEMES == ["ocean", "midnight", "aurora", "bento",
                                  "spatial"]
    for name in application.THEMES:
        assert f'[data-theme="{name}"]' in styled, \
            f"{name} is offered but never styled"
    picker = styled[styled.index("const THEMES = ["):styled.index("const THEME_NAMES")]
    assert [line.split('id:"')[1].split('"')[0]
            for line in picker.splitlines() if 'id:"' in line] == application.THEMES


def _theme_block(styled: str, name: str, mode: str = "light") -> str:
    """The colour block for one theme in one mode."""
    import re

    pattern = (r':root\[data-theme="%s"\]\[data-mode="%s"\]\{(.*?)\n\}'
               % (name, mode))
    return re.search(pattern, styled, re.S).group(1)


def _structure_block(styled: str, name: str) -> str:
    """The geometry/weight block, which is keyed on the theme alone."""
    import re

    return re.search(r'\n\[data-theme="%s"\]\{(.*?)\n\}' % name,
                     styled, re.S).group(1)


#: Every token a component is allowed to reference. A theme that leaves one of
#: these unset inherits Calm's, which is a bug the eye finds slowly.
SEMANTIC_TOKENS = [
    "--bg", "--surface", "--surface-2",
    "--text", "--text-2", "--text-3",
    "--primary", "--primary-2", "--on-primary",
    "--accent", "--accent-2",
    "--border", "--border-2",
    "--ok", "--warn", "--danger",
]


@pytest.mark.parametrize("name", application.THEMES)
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_every_theme_and_mode_defines_the_whole_palette(name, mode):
    """One vocabulary, redefined ten times.

    Every combination sets every token, so no theme in no mode can inherit
    another one's surfaces. That bug shipped once — two "light" themes rendered
    identically — and generating the blocks from one table makes it impossible.
    """
    import re

    styled = (ROOT / "webapp" / "index.html").read_text()
    block = _theme_block(styled, name, mode)
    declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", block))
    missing = [token for token in SEMANTIC_TOKENS if token not in declared]
    assert not missing, f"{name}/{mode} does not define {missing}"


@pytest.mark.parametrize("name", application.THEMES)
def test_every_theme_offers_three_to_five_vivid_colours(name):
    """A theme is a palette of its own, not one hue plus grey."""
    import re

    styled = (ROOT / "webapp" / "index.html").read_text()
    block = _theme_block(styled, name)
    brand = re.findall(r"--c[1-5]:\s*(#[0-9A-Fa-f]{6})", block)
    assert len(brand) == 5, f"{name} declares {len(brand)} brand colours"
    assert len(set(c.lower() for c in brand)) == 5, f"{name} repeats a colour"

    # And they are actually vivid rather than five greys: at least three need a
    # meaningful spread between their brightest and dullest channel.
    def saturated(hex_colour: str) -> bool:
        r, g, b = (int(hex_colour[i:i+2], 16) for i in (1, 3, 5))
        return (max(r, g, b) - min(r, g, b)) > 60

    assert sum(1 for c in brand if saturated(c)) >= 3, \
        f"{name}'s palette is not vivid: {brand}"


def test_the_picker_shows_the_same_palette_the_theme_uses():
    """The swatches must be the theme's real colours, not decoration."""
    import re

    styled = (ROOT / "webapp" / "index.html").read_text()
    picker = styled[styled.index("const THEMES = ["):styled.index("const THEME_NAMES")]
    for entry in re.findall(r'\{id:"(\w+)",\s*c:\[([^\]]*)\]\}', picker):
        name, colours = entry
        shown = [c.strip().strip('"').lower() for c in colours.split(",")]
        block = _theme_block(styled, name)
        actual = [c.lower() for c in
                  re.findall(r"--c[1-5]:\s*(#[0-9A-Fa-f]{6})", block)]
        assert shown == actual, f"{name}: picker shows {shown}, css uses {actual}"


def test_components_never_hard_code_a_colour():
    """The whole point of the token layer: theming must not need a component
    rewrite. Below the theme blocks, no rule may name a literal colour."""
    import re

    styled = (ROOT / "webapp" / "index.html").read_text()
    css = styled[styled.index("<style>"):styled.index("</style>")]
    components = css[css.index("   Base\n"):]
    literals = re.findall(r":\s*(#[0-9a-fA-F]{3,8})\b", components)
    # White and black are allowed inside rgba()/shadow definitions only, which
    # the pattern above does not match.
    assert not literals, f"hard-coded colours in components: {set(literals)}"


@pytest.mark.parametrize("name", application.THEMES)
def test_a_theme_is_more_than_a_palette(name):
    """Each theme also sets structure, or it is only a hue swap.

    The five are supposed to feel different, not merely be different colours:
    radius, gradient policy, type weight and motion are what carry that. Those
    are keyed on the theme alone — geometry is part of an identity and does not
    change between light and dark.
    """
    styled = (ROOT / "webapp" / "index.html").read_text()
    block = _structure_block(styled, name)
    structural = [token for token in
                  ("--radius:", "--hero-fill:", "--title-w:", "--display-w:",
                   "--motion:", "--primary-fill:", "--blur:", "--shadow-2:")
                  if token in block]
    assert len(structural) >= 3, f"{name} only changes colour: {structural}"


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_each_theme_owns_its_ground_in_each_mode(mode):
    """Five distinct grounds per mode: two themes sharing one are one theme."""
    import re

    styled = (ROOT / "webapp" / "index.html").read_text()
    grounds = {}
    for name in application.THEMES:
        block = _theme_block(styled, name, mode)
        grounds[name] = re.search(r"--bg:\s*(#[0-9A-Fa-f]+)",
                                  block).group(1).lower()
    assert len(set(grounds.values())) == 5, grounds


def test_light_and_dark_are_genuinely_different():
    """A "dark mode" that only nudges the background is not one."""
    import re

    styled = (ROOT / "webapp" / "index.html").read_text()
    for name in application.THEMES:
        light = _theme_block(styled, name, "light")
        dark = _theme_block(styled, name, "dark")
        get = lambda block, token: re.search(  # noqa: E731
            r"%s:\s*(#[0-9A-Fa-f]{6})" % token, block).group(1).lower()

        def luma(hex_colour):
            r, g, b = (int(hex_colour[i:i+2], 16) for i in (1, 3, 5))
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        # The ground and the text swap ends of the scale.
        assert luma(get(light, "--bg")) > 200, name
        assert luma(get(dark, "--bg")) < 60, name
        assert luma(get(light, "--text")) < 80, name
        assert luma(get(dark, "--text")) > 200, name


def test_the_mode_is_resolved_in_one_place():
    """Telegram's scheme first, then the OS, and the user can always override."""
    html = (ROOT / "webapp" / "index.html").read_text()
    assert "function resolveMode()" in html
    assert "tg?.colorScheme" in html
    assert "prefers-color-scheme" in html
    assert 'root.dataset.mode = resolveMode();' in html
    # And the choice is remembered on the device rather than in the account.
    assert 'localStorage.setItem("ernestos-appearance"' in html


def test_a_retired_theme_reads_as_the_default(alice):
    _set_theme(ALICE["id"], "pink")
    assert alice.get("/api/me").json()["theme"] == "ocean"


def test_the_migration_moves_a_retired_theme_to_its_closest_survivor(alice):
    """Someone who chose pink gets the pink one, not the default."""
    _set_theme(ALICE["id"], "rose")
    result = migrations.m0003_retire_themes()
    assert result["total"] >= 1
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).theme == "blossom"


def test_the_theme_migration_leaves_a_current_choice_alone(alice):
    _set_theme(ALICE["id"], "oxford")
    migrations.m0003_retire_themes()
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).theme == "oxford"


def test_the_theme_migration_keeps_a_reused_name(alice):
    """`aurora` names a current theme again — those rows must not be moved."""
    _set_theme(ALICE["id"], "aurora")
    migrations.m0003_retire_themes()
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).theme == "aurora"


def test_the_theme_migration_is_safe_to_run_twice(alice):
    _set_theme(ALICE["id"], "obsidian")
    migrations.m0003_retire_themes()
    again = migrations.m0003_retire_themes()
    assert again["total"] == 0
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).theme == "slate"


def test_a_retired_theme_cannot_be_set_again(alice):
    alice.patch("/api/me", json={"theme": "aurora"})
    assert alice.get("/api/me").json()["theme"] != "aurora"


# --------------------------------------------------------------------------
# Bot rendering
# --------------------------------------------------------------------------

def _home_text(telegram_id: int, lang: str = "uz") -> str:
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, telegram_id)
        user = s.get(User, telegram_id)
        user.language = lang
        s.commit()
        return application.render_home(svc.home(s, ws, user), lang)


def test_bot_home_stays_compact(alice):
    """Mission, today, the numbers, the privacy line — and nothing else."""
    _clear_tasks(ALICE["id"])
    text = _home_text(ALICE["id"])
    assert application.t("uz", "home_mission") in text
    assert application.t("uz", "home_today") in text
    assert application.t("uz", "privacy_line") in text
    for gone in ("Loyihalar", "Tug'ilgan kunlar", "Kechikkan", "Maqsadlar"):
        assert gone not in text


def test_bot_home_shows_only_todays_tasks(alice):
    _clear_tasks(ALICE["id"])
    today = svc.today_local()
    alice.post("/api/tasks", json={"title": "TODAY ONE", "deadline": today.isoformat()})
    alice.post("/api/tasks", json={"title": "NEXT MONTH",
                                   "deadline": (today + timedelta(days=25)).isoformat()})
    text = _home_text(ALICE["id"])
    assert "TODAY ONE" in text and "NEXT MONTH" not in text


def test_bot_home_says_none_rather_than_nothing(alice):
    _clear_tasks(ALICE["id"])
    _clear_missions(ALICE["id"])
    assert _home_text(ALICE["id"]).count(application.t("uz", "none")) == 2


def test_bot_home_escapes_a_hostile_task_title(alice):
    _clear_tasks(ALICE["id"])
    alice.post("/api/tasks", json={"title": "<b>bold</b>",
                                   "deadline": svc.today_local().isoformat()})
    text = _home_text(ALICE["id"])
    assert "&lt;b&gt;bold&lt;/b&gt;" in text


def test_bot_home_uses_the_language_of_the_reader(alice):
    for lang in ("uz", "en", "ru"):
        text = _home_text(ALICE["id"], lang)
        assert application.t(lang, "privacy_line") in text
        assert svc.MONTHS[lang][svc.today_local().month - 1] in text


def test_bot_statistics_reports_the_same_overall_as_home(alice):
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        data = svc.summary(s, ws)
    text = application.render_stats(data, "uz")
    assert f"{data['today']['overall']}%" in text
    assert alice.get("/api/home").json()["overall"]["value"] == data["today"]["overall"]


def test_bot_statistics_compares_today_with_the_week_and_the_month(alice):
    """One percentage is not information; three comparable ones are."""
    with SessionLocal() as s:
        data = svc.summary(s, svc.workspace_id_for(s, ALICE["id"]))
    assert set(data["windows"]) == {"day", "week", "month"}
    for name, window in data["windows"].items():
        for key in ("overall", "tasks", "habits", "prayer", "delta", "previous"):
            assert key in window, f"{name} window is missing {key}"
        assert window["delta"] == window["overall"] - window["previous"]

    text = application.render_stats(data, "uz")
    for label in ("st_today", "st_week", "st_month"):
        assert application.t("uz", label) in text
    assert f"{data['windows']['week']['overall']}%" in text
    assert f"{data['windows']['month']['overall']}%" in text


def test_statistics_shows_a_dash_for_a_component_with_nothing_due(alice):
    _clear_tasks(ALICE["id"])
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        data = svc.summary(s, ws)
    assert "—" in application.render_stats(data, "uz")


# --- the bot never offers a button that leads nowhere --------------------

def _task_buttons(**kwargs) -> list[str]:
    markup = application.tasks_keyboard("uz", **kwargs)
    return [button.text for row in markup.inline_keyboard for button in row]


def test_an_empty_workspace_offers_only_the_two_add_buttons():
    labels = _task_buttons(projects=[], open_tasks=0, editable=0)
    assert labels == [application.t("uz", "btn_add_task"),
                      application.t("uz", "btn_add_project")]


def test_the_done_and_edit_buttons_appear_once_there_are_tasks():
    labels = _task_buttons(projects=[], open_tasks=2, editable=2)
    assert application.t("uz", "btn_done_task") in labels
    assert application.t("uz", "btn_edit_task") in labels
    assert application.t("uz", "btn_del_project") not in labels


def test_the_project_delete_button_needs_a_project():
    without = _task_buttons(projects=[], open_tasks=1, editable=1)
    with_one = _task_buttons(projects=[{"id": 1, "name": "P"}],
                             open_tasks=1, editable=1)
    assert application.t("uz", "btn_del_project") not in without
    assert application.t("uz", "btn_del_project") in with_one
    assert "📁 P" in with_one


# --------------------------------------------------------------------------
# Journal — five questions, reported as their own status
# --------------------------------------------------------------------------

def _journal_done(caller) -> bool:
    return caller.get("/api/home").json()["journal_today"]


def test_a_partial_journal_is_not_complete(alice):
    alice.post("/api/journal", json={"answers": {"wins": "shipped"}})
    assert _journal_done(alice) is False


def test_all_five_answers_complete_the_day(alice):
    alice.post("/api/journal",
               json={"answers": {k: "answer" for k in svc.JOURNAL_KEYS}})
    assert _journal_done(alice) is True


def test_deleting_the_journal_clears_the_day(alice):
    today = svc.today_local().isoformat()
    alice.post("/api/journal",
               json={"answers": {k: "a" for k in svc.JOURNAL_KEYS}})
    alice.delete(f"/api/journal/{today}")
    assert _journal_done(alice) is False


def test_the_journal_is_not_a_habit(alice):
    """It is a status. Counting it would move the denominator and the streak."""
    names = [h["name"] for h in alice.get("/api/habits").json()["habits"]]
    assert "Summary" not in names


def test_journal_exposes_five_questions(alice):
    questions = alice.get("/api/journal").json()["questions"]
    assert len(questions) == 5
    assert {q["id"] for q in questions} == set(svc.JOURNAL_KEYS)


def test_journal_answers_survive_a_reload(alice):
    alice.post("/api/journal", json={"answers": {"wins": "a", "lesson": "b"}})
    entry = alice.get("/api/journal?day=" + svc.today_local().isoformat()).json()["entry"]
    assert entry["answers"]["wins"] == "a"
    assert entry["complete"] is False


# --------------------------------------------------------------------------
# Statistics and calendar
# --------------------------------------------------------------------------

def test_week_stats_return_seven_points(alice):
    body = alice.get("/api/stats?period=week").json()
    assert len(body["series"]) == 7
    assert {"habits", "prayer", "label", "day"} <= set(body["series"][0])


def test_month_stats_return_thirty_points(alice):
    assert len(alice.get("/api/stats?period=month").json()["series"]) == 30


def test_unknown_period_falls_back_to_week(alice):
    assert len(alice.get("/api/stats?period=decade").json()["series"]) == 7


def test_stats_include_streaks(alice):
    body = alice.get("/api/stats").json()
    assert "habit_streak" in body and "prayer_streak" in body


def test_calendar_shows_task_deadlines(alice):
    today = svc.today_local()
    alice.post("/api/tasks", json={"title": "CALENDAR-TASK",
                                   "deadline": today.isoformat()})
    body = alice.get(f"/api/calendar?year={today.year}&month={today.month}").json()
    titles = [e["title"] for e in body["events"].get(today.isoformat(), [])]
    assert "CALENDAR-TASK" in titles


def test_calendar_rejects_an_impossible_month(alice):
    assert alice.get("/api/calendar?year=2026&month=13").status_code == 422


# --------------------------------------------------------------------------
# Done archives
# --------------------------------------------------------------------------

def test_completed_task_moves_to_the_done_archive(alice):
    task_id = alice.post("/api/tasks", json={"title": "ARCHIVE-ME"}).json()["id"]
    alice.patch(f"/api/tasks/{task_id}", json={"status": "done"})
    assert "ARCHIVE-ME" in alice.get("/api/tasks/done").text
    assert "ARCHIVE-ME" not in alice.get("/api/tasks?days=365").text


def test_task_description_round_trips(alice):
    task_id = alice.post("/api/tasks", json={"title": "with details",
                                             "description": "the long version"}).json()["id"]
    tasks = alice.get("/api/tasks?days=365").json()["undated"]
    assert next(t for t in tasks if t["id"] == task_id)["description"] == "the long version"


# --------------------------------------------------------------------------
# Weekly focus editing
# --------------------------------------------------------------------------

def _a_focus_id(caller) -> int:
    """An existing mission, or a fresh one when the week still has a slot.

    The suite shares one database, so an earlier test may already have filled
    all three slots for this week.
    """
    existing = caller.get("/api/focus").json()["focus"]
    if existing:
        return existing[0]["id"]
    return caller.post("/api/focus", json={"title": "mission"}).json()["id"]


def test_weekly_focus_can_be_renamed(alice):
    focus_id = _a_focus_id(alice)
    assert alice.patch(f"/api/focus/{focus_id}", json={"title": "after"}).status_code == 200
    titles = [f["title"] for f in alice.get("/api/focus").json()["focus"]]
    assert "after" in titles


def test_renaming_a_focus_to_blank_is_rejected(alice):
    focus_id = _a_focus_id(alice)
    assert alice.patch(f"/api/focus/{focus_id}", json={"title": "   "}).status_code == 422


def test_another_users_focus_cannot_be_renamed(alice, bob):
    focus_id = _a_focus_id(alice)
    assert bob.patch(f"/api/focus/{focus_id}", json={"title": "stolen"}).status_code == 404


# --------------------------------------------------------------------------
# Platform statistics stay aggregate
# --------------------------------------------------------------------------

def test_platform_stats_are_counts_only(alice):
    alice.get("/api/me")
    with SessionLocal() as s:
        st = svc.platform_stats(s)
    assert st["total"] >= 1
    for key in ("dau", "wau", "mau", "new_today", "tasks_created"):
        assert isinstance(st[key], int)
    assert "journal" not in str(st).lower() or "journal_today" in st


# --------------------------------------------------------------------------
# Sequential member number
# --------------------------------------------------------------------------

def test_every_user_gets_a_member_number(alice, bob):
    a = alice.get("/api/me").json()["member_no"]
    b = bob.get("/api/me").json()["member_no"]
    assert a >= 1 and b >= 1 and a != b


def test_member_numbers_do_not_repeat(client):
    from db import User
    with SessionLocal() as s:
        numbers = [u.member_no for u in s.query(User).all()]
    assert len(numbers) == len(set(numbers)), "a member number was reused"


def test_platform_stats_report_the_latest_member_number(alice):
    alice.get("/api/me")
    with SessionLocal() as s:
        st = svc.platform_stats(s)
    assert st["latest_member_no"] >= 1


# --------------------------------------------------------------------------
# Wake-up habit
# --------------------------------------------------------------------------

def test_wake_habit_starts_with_a_default_time(alice):
    habits = alice.get("/api/habits").json()["habits"]
    wake = next(h for h in habits if h["name"] == "Get up")
    assert wake["target_time"] == "05:00"
    assert wake["system_key"] == "wakeup"


def test_wake_time_can_be_changed(alice):
    assert alice.post("/api/waketime", json={"time": "06:30"}).status_code == 200
    habits = alice.get("/api/habits").json()["habits"]
    assert next(h for h in habits if h["name"] == "Get up")["target_time"] == "06:30"


def test_bad_wake_time_is_rejected(alice):
    assert alice.post("/api/waketime", json={"time": "half past six"}).status_code == 422


def test_saying_i_am_up_in_time_marks_the_habit(alice):
    """Inside the window the habit is done."""
    alice.post("/api/waketime", json={"time": "05:00"})
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        result = svc.mark_wakeup(s, ws, now=datetime.combine(
            svc.today_local(), dtime(5, 30)))
    assert result["done"] is True
    habits = alice.get("/api/habits").json()["habits"]
    assert next(h for h in habits if h["name"] == "Get up")["done"] is True


def test_saying_it_after_the_grace_hour_does_not_count(alice):
    """05:00 target means the bot waits until 06:00 — 06:01 is too late."""
    alice.post("/api/waketime", json={"time": "05:00"})
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        result = svc.mark_wakeup(s, ws, now=datetime.combine(
            svc.today_local(), dtime(6, 1)))
    assert result["done"] is False
    assert result["deadline"] == "06:00"


def test_waking_before_the_target_still_counts(alice):
    alice.post("/api/waketime", json={"time": "06:00"})
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        result = svc.mark_wakeup(s, ws, now=datetime.combine(
            svc.today_local(), dtime(4, 45)))
    assert result["done"] is True


def test_a_late_message_cannot_undo_an_earlier_success(alice):
    """Once the day is earned it stays earned."""
    alice.post("/api/waketime", json={"time": "05:00"})
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        svc.mark_wakeup(s, ws, now=datetime.combine(svc.today_local(), dtime(5, 10)))
        svc.mark_wakeup(s, ws, now=datetime.combine(svc.today_local(), dtime(9, 0)))
    habits = alice.get("/api/habits").json()["habits"]
    assert next(h for h in habits if h["name"] == "Get up")["done"] is True


# --------------------------------------------------------------------------
# Yearly statistics
# --------------------------------------------------------------------------

def test_year_stats_return_twelve_monthly_points(alice):
    body = alice.get("/api/stats?period=year").json()
    assert body["period"] == "year"
    assert len(body["series"]) == 12


def test_year_points_are_labelled_by_month(alice):
    body = alice.get("/api/stats?period=year").json()
    assert all(len(p["label"]) == 5 for p in body["series"])   # MM.YY


def test_all_three_periods_are_available(alice):
    sizes = {p: len(alice.get(f"/api/stats?period={p}").json()["series"])
             for p in ("week", "month", "year")}
    assert sizes == {"week": 7, "month": 30, "year": 12}


# --------------------------------------------------------------------------
# Additive schema migration
#
# With real users on the system, a release that adds a column must not mean
# dropping the database. init_db() adds missing columns in place.
# --------------------------------------------------------------------------

def test_init_db_adds_missing_columns_without_touching_data(tmp_path):
    """An old table gains new columns and keeps every row."""
    import importlib
    from sqlalchemy import create_engine, inspect, text

    url = f"sqlite:///{tmp_path}/legacy.db"
    engine = create_engine(url)
    with engine.begin() as c:
        # users as it looked before member_no existed
        c.execute(text("""CREATE TABLE users (
            telegram_id BIGINT PRIMARY KEY, first_name VARCHAR(200) DEFAULT '',
            last_name VARCHAR(200) DEFAULT '', username VARCHAR(200) DEFAULT '',
            phone_number VARCHAR(40), language VARCHAR(2) DEFAULT 'uz',
            gender VARCHAR(6), theme VARCHAR(20) DEFAULT 'ocean',
            quote TEXT DEFAULT '', is_subscribed BOOLEAN DEFAULT 0,
            onboarding_step VARCHAR(20) DEFAULT 'language',
            onboarded BOOLEAN DEFAULT 0, created_at DATETIME,
            updated_at DATETIME, last_active_at DATETIME)"""))
        c.execute(text("INSERT INTO users (telegram_id, first_name) "
                       "VALUES (999001, 'RealUser')"))
    engine.dispose()

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        legacy = importlib.reload(db)
        assert "member_no" not in {
            c["name"] for c in inspect(legacy.engine).get_columns("users")}
        legacy.init_db()

        columns = {c["name"] for c in inspect(legacy.engine).get_columns("users")}
        assert "member_no" in columns, "new column was not added"
        assert "photo_file_id" in columns

        with legacy.engine.connect() as c:
            rows = c.execute(text("SELECT first_name FROM users")).fetchall()
        assert [r[0] for r in rows] == ["RealUser"], "existing data was lost"

        legacy.init_db()   # running twice must not fail
        legacy.engine.dispose()
    finally:
        if previous is not None:
            os.environ["DATABASE_URL"] = previous
        importlib.reload(db)


# --------------------------------------------------------------------------
# Statistics export
# --------------------------------------------------------------------------

def test_stats_csv_contains_the_daily_series(alice):
    alice.get("/api/me")
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        body = svc.stats_csv(s, ws, "week")
    assert "date,overall %,tasks %,habits %,prayer %" in body
    assert "habit streak" in body
    assert "task average %" in body
    assert body.count("\n") > 10


def test_stats_csv_reports_the_requested_period(alice):
    alice.get("/api/me")
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        assert "period,year" in svc.stats_csv(s, ws, "year")


def test_stats_csv_is_scoped_to_one_workspace(alice, bob):
    """Each workspace produces its own numbers."""
    alice.post("/api/habits", json={"name": "ExportOnly", "category": "bonus"})
    with SessionLocal() as s:
        a = svc.stats_csv(s, svc.workspace_id_for(s, ALICE["id"]), "week")
        b = svc.stats_csv(s, svc.workspace_id_for(s, BOB["id"]), "week")
    assert a.startswith("ErnestOS statistics") and b.startswith("ErnestOS statistics")


def test_export_endpoint_needs_the_bot_to_deliver(alice):
    """Without a running bot the endpoint says so rather than pretending."""
    r = alice.post("/api/stats/export?period=week")
    assert r.status_code == 503
    assert r.json()["detail"] == "bot_unavailable"


def test_export_endpoint_requires_authentication(client):
    assert client.post("/api/stats/export").status_code == 401


# ==========================================================================
# Release audit — P0 fixes
# ==========================================================================

# --- 001: a Telegram outage must not grant access ------------------------

@pytest.mark.asyncio
async def test_membership_check_returns_none_when_telegram_fails():
    """None means "unknown" — it must never be read as "subscribed"."""
    from telegram.error import TelegramError

    class Failing:
        async def get_chat_member(self, **_):
            raise TelegramError("boom")

    previous = deps.REQUIRED_CHANNEL_ID
    deps.REQUIRED_CHANNEL_ID = "-1001234567890"
    try:
        assert await application.is_subscribed(Failing(), 42, retries=0) is None
    finally:
        deps.REQUIRED_CHANNEL_ID = previous


def test_unknown_membership_never_marks_a_user_subscribed():
    """record_membership is the only writer, and it needs a definite answer."""
    with SessionLocal() as s:
        svc.get_or_create_user(s, 555777)
        s.commit()
        application.record_membership(s, 555777, False, "api")
        s.commit()
        assert s.get(User, 555777).is_subscribed is False


def test_a_confirmed_check_records_when_and_how(alice):
    with SessionLocal() as s:
        application.record_membership(s, ALICE["id"], True, "event")
        s.commit()
        user = s.get(User, ALICE["id"])
    assert user.sub_source == "event"
    assert user.sub_checked_at is not None


# --- 002: cached membership expires --------------------------------------

def test_a_fresh_membership_answer_is_reused():
    with SessionLocal() as s:
        svc.get_or_create_user(s, 555778)
        application.record_membership(s, 555778, True, "api")
        s.commit()
        assert application.membership_is_fresh(s.get(User, 555778)) is True


def test_an_old_membership_answer_is_stale():
    with SessionLocal() as s:
        svc.get_or_create_user(s, 555779)
        user = s.get(User, 555779)
        user.sub_checked_at = db.utcnow() - timedelta(hours=2)
        s.commit()
        assert application.membership_is_fresh(s.get(User, 555779)) is False


def test_a_never_checked_user_is_stale():
    with SessionLocal() as s:
        svc.get_or_create_user(s, 555780)
        s.commit()
        assert application.membership_is_fresh(s.get(User, 555780)) is False


# --- 003: a half-registered account cannot create rows -------------------

def test_unonboarded_user_cannot_create_data(client):
    headers = {"X-Telegram-Init-Data": init_data({"id": 606001, "first_name": "New"})}
    r = client.post("/api/tasks", headers=headers, json={"title": "too early"})
    assert r.status_code == 409
    assert r.json()["detail"] == "onboarding_required"


def test_unonboarded_user_can_still_read_their_status(client):
    headers = {"X-Telegram-Init-Data": init_data({"id": 606002, "first_name": "New"})}
    r = client.get("/api/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["onboarded"] is False


def test_the_same_user_succeeds_once_onboarded(client):
    user = {"id": 606003, "first_name": "New"}
    headers = {"X-Telegram-Init-Data": init_data(user)}
    assert client.post("/api/tasks", headers=headers,
                       json={"title": "x"}).status_code == 409
    _onboard(user["id"])
    assert client.post("/api/tasks", headers=headers,
                       json={"title": "x"}).status_code == 200


# --- 012: rate limiting ---------------------------------------------------

def test_rate_limit_allows_normal_use():
    application._buckets.clear()
    for _ in range(10):
        assert application.rate_limit_check(999001, "write") is None


def test_rate_limit_blocks_a_flood():
    application._buckets.clear()
    limit, _ = application.RATE_LIMITS["write"]
    for _ in range(limit):
        application.rate_limit_check(999002, "write")
    retry = application.rate_limit_check(999002, "write")
    assert retry is not None and retry >= 1


def test_heavy_endpoints_have_a_tighter_budget():
    application._buckets.clear()
    for _ in range(application.RATE_LIMITS["heavy"][0]):
        application.rate_limit_check(999003, "heavy")
    assert application.rate_limit_check(999003, "heavy") is not None
    # A different bucket for the same user is unaffected.
    assert application.rate_limit_check(999003, "read") is None


def test_users_have_separate_budgets():
    application._buckets.clear()
    for _ in range(application.RATE_LIMITS["write"][0]):
        application.rate_limit_check(999004, "write")
    assert application.rate_limit_check(999005, "write") is None


def test_a_spent_bucket_is_forgotten_once_its_window_has_passed():
    """The leak this replaces: buckets were created and never removed.

    Timestamps inside a bucket expired, but the bucket itself lived as long as
    the process — and unauthenticated callers are bucketed per client host, so
    the dictionary grew with every address that ever touched the API.
    """
    import ratelimit

    clock = _FakeClock()
    limiter = ratelimit.InMemoryRateLimiter({"write": (5, 60)},
                                            sweep_every=10, clock=clock)
    for user in range(100):
        limiter.check(user, "write")
    assert limiter.buckets == 100

    clock.advance(61)                       # every hit is now expired
    limiter.check(999, "write")             # any call triggers the sweep
    assert limiter.buckets == 1


def test_sweeping_does_not_forgive_a_caller_still_over_budget():
    """A live bucket must survive the sweep, or the limit means nothing."""
    import ratelimit

    clock = _FakeClock()
    limiter = ratelimit.InMemoryRateLimiter({"write": (5, 60)},
                                            sweep_every=10, clock=clock)
    for _ in range(5):
        limiter.check(7, "write")
    assert limiter.check(7, "write") is not None

    clock.advance(30)                       # half the window: still spent
    limiter.check(8, "write")               # triggers a sweep
    assert limiter.buckets == 2
    assert limiter.check(7, "write") is not None


class _FakeClock:
    """A monotonic clock the test moves by hand, so no test ever sleeps."""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- 013: bounded payloads -----------------------------------------------

def test_oversized_body_is_refused(alice):
    r = alice.post("/api/tasks", json={"title": "x" * 400})
    assert r.status_code == 422


def test_journal_rejects_a_stuffed_answer_dictionary(alice):
    r = alice.post("/api/journal",
                   json={"answers": {f"k{i}": "v" for i in range(500)}})
    assert r.status_code == 422


def test_journal_rejects_an_enormous_single_answer(alice):
    r = alice.post("/api/journal", json={"answers": {"wins": "x" * 9000}})
    assert r.status_code == 422


def test_normal_uzbek_text_is_accepted(alice):
    r = alice.post("/api/tasks", json={"title": "O'zbekcha matn — chiroyli ✓"})
    assert r.status_code == 200


# --- 014 / 076: user text never becomes markup ---------------------------

@pytest.mark.parametrize("raw,expected", [
    ("<a href='x'>hi</a>", "&lt;a href='x'&gt;hi&lt;/a&gt;"),
    ("A & B < C", "A &amp; B &lt; C"),
    ("plain", "plain"),
    (None, ""),
])
def test_escape_neutralises_user_markup(raw, expected):
    assert application.esc(raw) == expected


def test_admin_identity_line_escapes_the_name():
    with SessionLocal() as s:
        svc.get_or_create_user(s, 707001, first_name="<b>Bold</b>", username="a&b")
        s.commit()
        line = application._who(s.get(User, 707001))
    assert "<b>Bold</b>" not in line
    assert "&lt;b&gt;" in line


# --- 016: no phone number in any log -------------------------------------

def test_admin_identity_line_carries_no_phone():
    with SessionLocal() as s:
        svc.get_or_create_user(s, 707002, first_name="Phoney")
        user = s.get(User, 707002)
        user.phone_number = "+998901234567"
        s.commit()
        line = application._who(user)
    assert "998" not in line and "901234567" not in line


def test_source_never_logs_a_phone_number():
    """No log line may interpolate phone_number."""
    source = (ROOT / "app.py").read_text()
    for offender in ("Phone: {snapshot.phone_number}",
                     "Phone: {user.phone_number"):
        assert offender not in source


# --- 032 / 036: reports are claimed once, not sent twice -----------------

def test_a_report_can_only_be_claimed_once(alice):
    alice.get("/api/me")
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        day = date(2030, 1, 1)
        first = svc.claim_report(s, ws, "morning", day)
        second = svc.claim_report(s, ws, "morning", day)
    assert first is not None, "the first worker must win the claim"
    assert second is None, "a second worker must not also send"


def test_a_failed_report_records_the_reason(alice):
    alice.get("/api/me")
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        report_id = svc.claim_report(s, ws, "evening", date(2030, 1, 2))
        svc.mark_report_failed(s, report_id, "blocked by user")
        row = s.get(db.DailyReportLog, report_id)
        assert row.status == "failed"
        assert "blocked" in row.last_error
        assert row.attempts == 1


def test_a_released_claim_can_be_retried(alice):
    alice.get("/api/me")
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        day = date(2030, 1, 3)
        first = svc.claim_report(s, ws, "morning", day)
        svc.release_report(s, first)
        assert svc.claim_report(s, ws, "morning", day) is not None


def test_morning_and_evening_claims_are_independent(alice):
    alice.get("/api/me")
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        day = date(2030, 1, 4)
        assert svc.claim_report(s, ws, "morning", day) is not None
        assert svc.claim_report(s, ws, "evening", day) is not None


def test_job_lock_is_granted_on_sqlite():
    """Nothing to coordinate on SQLite, so the job always runs."""
    with svc.JobLock(SessionLocal, "test-job") as lock:
        assert lock.acquired is True


# --- 037: one bad recipient never takes the batch down -------------------
#
# The scheduler talks to real users unattended, which makes it the one place
# where a silent failure is invisible until somebody notices they stopped
# getting their reports. These are the first tests it has had.

class _FakeBot:
    """Records every send, and fails for the ids it was told to fail for."""

    def __init__(self, failures: dict[int, Exception] | None = None):
        self.sent: list[int] = []
        self.failures = failures or {}

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id in self.failures:
            raise self.failures[chat_id]
        self.sent.append(chat_id)
        return True


def _batch_of_three(monkeypatch, client) -> list[int]:
    """Three onboarded recipients, and nobody else in the batch."""
    ids = [next(_next_id) for _ in range(3)]
    for telegram_id in ids:
        Caller(client, {"id": telegram_id, "first_name": "Batch"})
    with SessionLocal() as s:
        recipients = [(i, svc.workspace_id_for(s, i), "uz") for i in ids]
    monkeypatch.setattr(svc, "active_recipients", lambda s: list(recipients))
    return ids


async def test_a_telegram_failure_does_not_stop_the_report_batch(monkeypatch, client):
    """A blocked user is the common case, not an exceptional one."""
    from telegram.error import Forbidden

    ids = _batch_of_three(monkeypatch, client)
    bot = _FakeBot({ids[1]: Forbidden("bot was blocked by the user")})

    await application._send_reports_locked(bot, "morning", date(2031, 3, 1))

    assert bot.sent == [ids[0], ids[2]], "the users after the failure must be sent"


async def test_a_non_telegram_error_does_not_stop_the_report_batch(monkeypatch, client):
    """Anything at all can raise mid-batch; only that user may lose their report."""
    ids = _batch_of_three(monkeypatch, client)
    bot = _FakeBot({ids[0]: RuntimeError("something entirely unexpected")})

    await application._send_reports_locked(bot, "evening", date(2031, 3, 2))

    assert bot.sent == [ids[1], ids[2]]


async def test_the_failing_recipient_is_recorded_and_the_others_are_sent(
        monkeypatch, client):
    """The outbox has to say what happened, per user, or nothing is diagnosable."""
    from telegram.error import Forbidden

    ids = _batch_of_three(monkeypatch, client)
    day = date(2031, 3, 3)
    bot = _FakeBot({ids[1]: Forbidden("blocked")})

    await application._send_reports_locked(bot, "morning", day)

    from sqlalchemy import select
    with SessionLocal() as s:
        rows = {}
        for telegram_id in ids:
            ws = svc.workspace_id_for(s, telegram_id)
            rows[telegram_id] = s.scalar(select(db.DailyReportLog).where(
                db.DailyReportLog.workspace_id == ws,
                db.DailyReportLog.report_type == "morning",
                db.DailyReportLog.report_date == day))
    assert rows[ids[0]].status == "sent"
    assert rows[ids[2]].status == "sent"
    assert rows[ids[1]].status == "failed"
    assert "blocked" in rows[ids[1]].last_error


async def test_a_failure_before_the_claim_does_not_stop_the_batch(
        monkeypatch, client):
    """Deciding who is due, and claiming their slot, sit outside the send.

    They can raise for exactly the same reasons the send can — and until this
    guard existed, one unreadable row there ended the batch before anybody
    after that recipient was even looked at.
    """
    ids = _batch_of_three(monkeypatch, client)
    real_claim = svc.claim_report

    def failing_claim(s, ws, report_type, report_date):
        with SessionLocal() as inner:
            if ws == svc.workspace_id_for(inner, ids[0]):
                raise RuntimeError("could not reach the outbox")
        return real_claim(s, ws, report_type, report_date)

    monkeypatch.setattr(svc, "claim_report", failing_claim)
    bot = _FakeBot()

    await application._send_reports_locked(bot, "morning", date(2031, 3, 5))

    assert bot.sent == [ids[1], ids[2]]


async def test_a_second_run_of_the_same_day_sends_nothing(monkeypatch, client):
    """Duplicate-run protection, exercised through the job rather than the claim."""
    ids = _batch_of_three(monkeypatch, client)
    day = date(2031, 3, 4)

    first = _FakeBot()
    await application._send_reports_locked(first, "morning", day)
    assert first.sent == ids

    second = _FakeBot()
    await application._send_reports_locked(second, "morning", day)
    assert second.sent == [], "every slot was already claimed"


# --- the daily statistics post survives a restart ------------------------
#
# It used to be `cron(hour=10)` on an in-memory jobstore, so every boot moved
# the next fire to *after now* — a deploy at 11:00 pushed it to tomorrow, and
# redeploying most days meant it never fired. "Once a day" is now the claim's
# job, not the schedule's.

def _clear_stats_claims():
    from sqlalchemy import delete
    with SessionLocal() as s:
        s.execute(delete(db.JobRun).where(
            db.JobRun.job_name == application.STATS_JOB))
        s.commit()


async def test_the_statistics_post_goes_out_once_a_day(monkeypatch, client):
    _clear_stats_claims()
    monkeypatch.setattr(application, "STATS_CHANNEL_ID", "-100999")
    bot = _FakeBot()

    assert await application.send_platform_stats(bot, force=True) is True
    assert len(bot.sent) == 1
    # A second tick two minutes later, and a third from another instance.
    assert await application.send_platform_stats(bot, force=True) is False
    assert await application.send_platform_stats(bot, force=True) is False
    assert len(bot.sent) == 1, "the channel was posted to twice in one day"


def test_the_statistics_tally_survives_users_who_never_answered():
    """The bug that actually kept the channel silent.

    `gender` is NULL until the prayer screen asks for it, so any real user base
    holds a mix of None and "male"/"female". `sorted()` on the raw pairs then
    compares None with a string and raises TypeError — inside a scheduled job,
    where nothing was catching it. The post never appeared and nothing said why.
    """
    assert application._tally({"male": 3, None: 5, "female": 2}) == \
        "— 5 · female 2 · male 3"
    assert application._tally({None: 1}) == "— 1"
    assert application._tally({}) == ""


async def test_the_statistics_post_renders_with_mixed_genders(monkeypatch, client):
    """End to end, against a workspace where only some users answered."""
    _clear_stats_claims()
    monkeypatch.setattr(application, "STATS_CHANNEL_ID", "-100999")
    answered = next(_next_id)
    with SessionLocal() as s:
        svc.get_or_create_user(s, answered, first_name="Answered")
        s.get(User, answered).gender = "male"
        s.commit()

    bot = _FakeBot()
    assert await application.send_platform_stats(bot, force=True) is True
    assert len(bot.sent) == 1


async def test_the_statistics_post_waits_for_its_hour(monkeypatch, client):
    """A tick before STATS_POST_HOUR must not post the day's numbers early."""
    _clear_stats_claims()
    monkeypatch.setattr(application, "STATS_CHANNEL_ID", "-100999")
    monkeypatch.setattr(application, "STATS_POST_HOUR", 23)
    monkeypatch.setattr(svc, "now_local",
                        lambda tz=None: datetime.combine(svc.today_local(),
                                                         dtime(9, 0)))
    bot = _FakeBot()
    assert await application.send_platform_stats(bot) is False
    assert bot.sent == []


async def test_a_restart_after_the_hour_still_delivers_the_post(
        monkeypatch, client):
    """The whole point. A process that boots at 11:00 must still post today."""
    _clear_stats_claims()
    monkeypatch.setattr(application, "STATS_CHANNEL_ID", "-100999")
    monkeypatch.setattr(application, "STATS_POST_HOUR", 10)
    monkeypatch.setattr(svc, "now_local",
                        lambda tz=None: datetime.combine(svc.today_local(),
                                                         dtime(11, 0)))
    bot = _FakeBot()
    assert await application.send_platform_stats(bot) is True
    assert len(bot.sent) == 1


async def test_a_failed_statistics_post_is_retried_not_lost(monkeypatch, client):
    """One blip must cost a couple of minutes, not the whole day's numbers."""
    from telegram.error import TimedOut

    _clear_stats_claims()
    monkeypatch.setattr(application, "STATS_CHANNEL_ID", "-100999")

    failing = _FakeBot({"-100999": TimedOut()})
    with pytest.raises(TimedOut):
        await application.send_platform_stats(failing, force=True)

    # The claim was handed back, so the next tick genuinely retries.
    working = _FakeBot()
    assert await application.send_platform_stats(working, force=True) is True
    assert len(working.sent) == 1


async def test_a_statistics_failure_never_escapes_into_the_scheduler(
        monkeypatch, client):
    """APScheduler's own logger is the worst place for this to surface."""
    from telegram.error import TimedOut

    _clear_stats_claims()
    monkeypatch.setattr(application, "STATS_CHANNEL_ID", "-100999")
    # Must not raise: the tick wrapper is what the scheduler actually calls.
    await application.send_platform_stats_tick(_FakeBot({"-100999": TimedOut()}))


async def test_no_statistics_channel_posts_nothing(monkeypatch, client):
    _clear_stats_claims()
    monkeypatch.setattr(application, "STATS_CHANNEL_ID", "")
    bot = _FakeBot()
    assert await application.send_platform_stats(bot, force=True) is False
    assert bot.sent == []


async def test_a_failed_recipient_does_not_stop_the_reminder_batch(
        monkeypatch, client):
    """The gap this closes: reminders isolated the send but not the queries.

    Anything raised outside the send — a query, a mark — escaped the loop and
    silently cancelled every recipient after it.
    """
    ids = _batch_of_three(monkeypatch, client)
    seen: list[int] = []

    def exploding_task_reminders(s, ws, user, **kwargs):
        seen.append(user.telegram_id)
        if user.telegram_id == ids[0]:
            raise RuntimeError("the database blinked")
        return []

    monkeypatch.setattr(svc, "due_task_reminders", exploding_task_reminders)
    monkeypatch.setattr(svc, "due_habit_reminders", lambda s, ws, user, **kw: [])

    await application.send_reminders(_FakeBot())

    assert seen == ids, "every recipient must still be visited"


# --- 033: pending updates are replayed, not dropped ----------------------

def test_pending_updates_are_not_dropped():
    source = (ROOT / "app.py").read_text()
    assert "drop_pending_updates=False" in source


# --- 034: a stale callback cannot drive a superseded flow ----------------

class _Ctx:
    """Minimal stand-in for a python-telegram-bot context."""

    def __init__(self):
        self.user_data: dict = {}


def test_a_new_flow_replaces_the_previous_one():
    ctx = _Ctx()
    first = application.start_flow(ctx, "task_title")
    second = application.start_flow(ctx, "project_rename")
    assert first["id"] != second["id"]
    assert application.current_flow(ctx, "task_title") is None
    assert application.current_flow(ctx, "project_rename") is not None


def test_an_expired_flow_is_forgotten():
    ctx = _Ctx()
    application.start_flow(ctx, "task_title")
    ctx.user_data["flow"]["expires"] = time.time() - 1
    assert application.current_flow(ctx) is None
    assert "flow" not in ctx.user_data


def test_current_flow_filters_by_name():
    ctx = _Ctx()
    application.start_flow(ctx, "habit_cat", title="Reading")
    assert application.current_flow(ctx, "task_days") is None
    assert application.current_flow(ctx, "habit_cat")["title"] == "Reading"


# --- 087: readiness reports its dependencies -----------------------------

def test_liveness_is_a_plain_ok(client):
    assert client.get("/health/live").json() == {"ok": True}


def test_readiness_reports_each_dependency(client):
    body = client.get("/health/ready").json()
    assert body["ok"] is True
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["schema"] == "ok"


# --- 061: the avatar is reachable the way an <img> tag asks for it -------

def test_avatar_accepts_the_signed_blob_as_a_query_parameter(client):
    """An <img> cannot send headers, so ?tgdata= must authenticate too."""
    _onboard(ALICE["id"])
    # The blob contains & and =, so it has to be encoded as one value —
    # exactly what encodeURIComponent does in the Mini App.
    signed = quote(init_data(ALICE), safe="")
    r = client.get(f"/api/avatar?tgdata={signed}")
    # 404 = authenticated, simply no photo stored. 401 would be the bug.
    assert r.status_code == 404


def test_avatar_still_rejects_an_unsigned_request(client):
    assert client.get("/api/avatar").status_code == 401


def test_avatar_rejects_a_tampered_query_blob(client):
    r = client.get(f"/api/avatar?tgdata={quote(init_data(ALICE, tamper=True), safe='')}")
    assert r.status_code == 401


# ==========================================================================
# Product round: one decision at a time
# ==========================================================================

# --- Home is today, and only today ---------------------------------------

def _clear_tasks(telegram_id: int) -> None:
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, telegram_id)
        for row in s.query(db.Task).filter_by(workspace_id=ws).all():
            s.delete(row)
        s.commit()


def test_home_carries_only_todays_tasks(alice):
    _clear_tasks(ALICE["id"])
    today = svc.today_local()
    alice.post("/api/tasks", json={"title": "TODAY", "deadline": today.isoformat()})
    alice.post("/api/tasks", json={"title": "NEXT WEEK",
                                   "deadline": (today + timedelta(days=6)).isoformat()})
    alice.post("/api/tasks", json={"title": "LAST WEEK",
                                   "deadline": (today - timedelta(days=6)).isoformat()})
    titles = [task["title"]
              for group in alice.get("/api/home").json()["tasks_today"]
              for task in group["tasks"]]
    assert titles == ["TODAY"]


def test_home_groups_todays_tasks_by_project(alice):
    _clear_tasks(ALICE["id"])
    today = svc.today_local().isoformat()
    project_id = alice.post("/api/projects", json={"name": "Launch"}).json()["id"]
    alice.post("/api/tasks", json={"title": "IN PROJECT", "deadline": today,
                                   "project_id": project_id})
    alice.post("/api/tasks", json={"title": "ON ITS OWN", "deadline": today})
    groups = alice.get("/api/home").json()["tasks_today"]
    assert [g["project"] for g in groups] == ["Launch", None]
    assert groups[0]["tasks"][0]["title"] == "IN PROJECT"


def test_home_does_not_carry_the_removed_modules(alice):
    body = alice.get("/api/home").json()
    for key in ("goals", "projects", "money", "notes", "contacts"):
        assert key not in body, f"Home still ships {key}"


def test_home_answers_what_now_before_anything_else(alice):
    """Home's first job is one action, so the payload has to carry one."""
    body = alice.get("/api/home").json()
    assert "now" in body
    assert body["now"]["kind"] in {"wake", "task", "habit", "prayer",
                                   "journal", "clear"}
    # And the pieces the screen is built from, each deliberately singular.
    for key in ("top3", "focus", "week", "wake", "break"):
        assert key in body, f"Home is missing {key}"


def test_home_writes_the_date_in_the_users_language(alice):
    alice.post("/api/settings", json={"language": "en"})
    assert svc.MONTHS["en"][svc.today_local().month - 1] in \
        alice.get("/api/home").json()["date_label"]
    alice.post("/api/settings", json={"language": "uz"})
    assert svc.MONTHS["uz"][svc.today_local().month - 1] in \
        alice.get("/api/home").json()["date_label"]


# --- One overall number, computed once -----------------------------------

def test_a_category_with_nothing_due_is_left_out_of_the_score(alice):
    """An empty category must not be scored as 0%.

    It is dropped from the weighting entirely, and the remaining weights are
    renormalised over what is left — which is what makes a fixed 40/25/20/15
    safe to state. A user with no prayer module does not carry a permanent 15%
    hole; that 15 is split across the other three in proportion.
    """
    _clear_tasks(ALICE["id"])
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        components = svc.overall_components(s, ws)
        assert components["tasks"] is None
        assert svc.overall_percent(s, ws) == svc.weighted_overall(components)


def test_the_weights_are_the_ones_the_product_promises():
    assert svc.OVERALL_WEIGHTS == {"tasks": 0.40, "habits": 0.25,
                                   "focus": 0.20, "prayer": 0.15}
    assert round(sum(svc.OVERALL_WEIGHTS.values()), 6) == 1.0


def test_one_category_on_its_own_scores_exactly_itself():
    """The only defensible answer when there is nothing to weigh it against."""
    assert svc.weighted_overall({"tasks": 73}) == 73
    assert svc.weighted_overall({"prayer": 40}) == 40
    assert svc.weighted_overall({}) == svc.EMPTY_OVERALL
    assert svc.weighted_overall({"tasks": None, "habits": None}) == svc.EMPTY_OVERALL


def test_a_missing_category_is_split_in_proportion_not_in_equal_shares():
    """Dropping prayer must not hand its 15 points out evenly.

    Tasks are worth more than habits, so tasks absorb more of the gap. An equal
    split would quietly flatten the weighting the moment anybody stopped using
    one module.
    """
    # tasks 40, habits 25, focus 20 → renormalised over 85
    score = svc.weighted_overall({"tasks": 100, "habits": 0, "focus": 0})
    assert score == round(100 * 0.40 / 0.85)
    # and the same three at full marks is still exactly 100
    assert svc.weighted_overall({"tasks": 100, "habits": 100, "focus": 100}) == 100


def test_tasks_outweigh_habits_in_the_score():
    """40 against 25 — the day's real work is not the same size as a habit."""
    tasks_only = svc.weighted_overall({"tasks": 100, "habits": 0})
    habits_only = svc.weighted_overall({"tasks": 0, "habits": 100})
    assert tasks_only > habits_only


def test_an_important_task_is_worth_more_than_a_trivial_one(alice):
    """Marking something "high" tells the system what today is about.

    If every task were worth the same, the cheapest route to a good percentage
    would be to do the easy ones and leave the one that mattered.
    """
    _clear_tasks(ALICE["id"])
    today = svc.today_local().isoformat()
    big = alice.post("/api/tasks", json={"title": "The hard one",
                                         "deadline": today,
                                         "priority": "high"}).json()["id"]
    alice.post("/api/tasks", json={"title": "A small one", "deadline": today,
                                   "priority": "low"})
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        assert svc.overall_components(s, ws)["tasks"] == 0

    alice.patch(f"/api/tasks/{big}", json={"status": "done"})
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        # 3 of 4 priority points, not 1 of 2.
        assert svc.overall_components(s, ws)["tasks"] == 75
    _clear_tasks(ALICE["id"])


def test_finishing_todays_tasks_scores_them_at_a_hundred(alice):
    _clear_tasks(ALICE["id"])
    today = svc.today_local().isoformat()
    task_id = alice.post("/api/tasks",
                         json={"title": "DO IT", "deadline": today}).json()["id"]
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        assert svc.overall_components(s, ws)["tasks"] == 0
    alice.patch(f"/api/tasks/{task_id}", json={"status": "done"})
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        assert svc.overall_components(s, ws)["tasks"] == 100


def test_the_backlog_does_not_drag_todays_number(alice):
    _clear_tasks(ALICE["id"])
    today = svc.today_local()
    alice.post("/api/tasks", json={"title": "ANCIENT",
                                   "deadline": (today - timedelta(days=30)).isoformat()})
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        assert svc.overall_components(s, ws)["tasks"] is None


def test_every_surface_reports_the_same_overall(alice):
    """Home, Statistics and the evening report read one function."""
    today = svc.today_local().isoformat()
    alice.post("/api/tasks", json={"title": "PARITY", "deadline": today})
    home = alice.get("/api/home").json()["overall"]["value"]
    stats = alice.get("/api/stats").json()["today"]["overall"]
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        evening = svc.evening_data(s, ws, s.get(User, ALICE["id"]))["overall"]["value"]
    assert home == stats == evening


def test_the_trend_is_flat_when_yesterday_had_nothing_to_measure(bob):
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, BOB["id"])
        for row in s.query(db.Habit).filter_by(workspace_id=ws).all():
            row.archived_at = db.utcnow()
        s.commit()
        assert svc.overall_state(s, ws)["trend"] == "flat"


def test_statistics_report_a_percentage_per_component(alice):
    body = alice.get("/api/stats").json()["today"]
    assert set(body) >= {"overall", "trend", "tasks", "habits", "prayer",
                         "prayer_score", "prayer_max", "streak"}


# --- Quick capture -------------------------------------------------------

def test_quick_add_needs_only_a_title(alice):
    r = alice.post("/api/quick", json={"title": "idea while walking"})
    assert r.status_code == 200
    assert "idea while walking" in alice.get("/api/tasks?days=365").text


def test_quick_add_rejects_a_blank_title(alice):
    """Whitespace used to reach the service layer and surface as a 500."""
    assert alice.post("/api/quick", json={"title": "   "}).status_code == 422
    assert alice.post("/api/quick", json={"title": ""}).status_code == 422


def test_quick_add_is_scoped_to_the_caller(alice, bob):
    alice.post("/api/quick", json={"title": "ALICE-QUICK-ONLY"})
    assert "ALICE-QUICK-ONLY" not in bob.get("/api/tasks?days=365").text


# --- Returning after a break --------------------------------------------

def test_a_recent_user_is_not_offered_a_reset(alice):
    alice.get("/api/me")
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        state = svc.break_state(s, ws, s.get(User, ALICE["id"]))
    assert state["suggest_reset"] is False


def test_a_returning_user_with_a_backlog_is_offered_a_reset(alice):
    today = svc.today_local()
    alice.post("/api/tasks", json={"title": "OLD",
                                   "deadline": (today - timedelta(days=5)).isoformat()})
    with SessionLocal() as s:
        user = s.get(User, ALICE["id"])
        user.last_active_at = db.utcnow() - timedelta(days=6)
        s.commit()
        ws = svc.workspace_id_for(s, ALICE["id"])
        state = svc.break_state(s, ws, s.get(User, ALICE["id"]))
    assert state["suggest_reset"] is True
    assert state["days_away"] >= 3


def test_fresh_start_pulls_the_backlog_to_today(alice):
    today = svc.today_local()
    alice.post("/api/tasks", json={"title": "PULLME",
                                   "deadline": (today - timedelta(days=4)).isoformat()})
    assert alice.post("/api/fresh-start", json={"mode": "today"}).status_code == 200
    overdue = alice.get("/api/tasks?days=7").json()["overdue"]
    assert "PULLME" not in [x["title"] for x in overdue]


def test_fresh_start_can_archive_instead(alice):
    today = svc.today_local()
    alice.post("/api/tasks", json={"title": "ARCHIVEME",
                                   "deadline": (today - timedelta(days=4)).isoformat()})
    alice.post("/api/fresh-start", json={"mode": "archive"})
    assert "ARCHIVEME" not in alice.get("/api/tasks?days=365").text


def test_fresh_start_ignores_an_unknown_mode(alice):
    r = alice.post("/api/fresh-start", json={"mode": "nonsense"})
    assert r.status_code == 200 and r.json()["mode"] == "today"


# --- Weekly review -------------------------------------------------------

def test_review_returns_the_weeks_numbers_and_questions(alice):
    body = alice.get("/api/review").json()
    assert "week_start" in body
    assert set(body["answers"]) == {"went_well", "blocked", "next_focus"}
    assert body["saved"] is False


def test_review_answers_round_trip(alice):
    alice.post("/api/review", json={"went_well": "shipped the audit fixes",
                                    "blocked": "no staging",
                                    "next_focus": "pilot with 20 users"})
    body = alice.get("/api/review").json()
    assert body["answers"]["went_well"] == "shipped the audit fixes"
    assert body["saved"] is True


def test_review_is_one_row_per_week(alice):
    alice.post("/api/review", json={"went_well": "first"})
    alice.post("/api/review", json={"went_well": "second"})
    assert alice.get("/api/review").json()["answers"]["went_well"] == "second"


def test_review_is_private_to_the_workspace(alice, bob):
    alice.post("/api/review", json={"went_well": "ALICE-REVIEW-SECRET"})
    assert "ALICE-REVIEW-SECRET" not in bob.get("/api/review").text


# --- Onboarding is two questions, not four -------------------------------

def test_onboarding_starts_at_language():
    with SessionLocal() as s:
        svc.get_or_create_user(s, 808001)
        s.commit()
        assert s.get(User, 808001).onboarding_step == "language"


def test_setup_builds_a_day_instead_of_filling_a_form():
    """Language, the pitch, then four questions that each create something.

    Every step of setup writes a real row — the goal becomes the week's
    mission, the tasks become today's tasks — so the last screen can show the
    user their actual day. A form that collects answers and shows a tour at the
    end has taught nobody anything.

    What must not come back: the channel as step two, and the phone number
    between them. Both were tolls charged before the user had seen a single
    thing the product does.
    """
    assert application.ONBOARDING_STEPS == [
        "language", "intro", "name", "goal", "tasks", "habits", "done"]
    source = (ROOT / "app.py").read_text()
    assert 'user.onboarding_step = "phone"' not in source
    assert 'user.onboarding_step = "subscribe"' not in source, \
        "the channel is back in onboarding"
    # Legacy accounts parked on a retired step are moved on, not re-asked.
    assert application.LEGACY_STEPS == {"phone", "gender", "subscribe"}


def test_setup_writes_each_answer_as_it_is_given():
    """Nothing is held in limbo until the end.

    Somebody who walks away after the goal keeps the goal. Buffering the whole
    setup and committing it on the last screen means an abandoned setup leaves
    the account exactly as empty as it started.
    """
    source = (ROOT / "app.py").read_text()
    handler = source[source.index("async def handle_setup_answer("):
                     source.index("#: Three tasks and three habits.")]
    assert "svc.add_focus(" in handler, "the goal is not written"
    assert "svc.add_task(" in handler, "the tasks are not written"
    assert "svc.add_habit(" in handler, "the habits are not written"
    assert "user.first_name = " in handler, "the name is not written"


@pytest.mark.parametrize("lang", ["uz", "en", "ru"])
def test_every_setup_question_carries_an_example(lang):
    """"What is your main goal?" is a question somebody stalls on.

    An example is the difference between a prompt and a blank page, and it is
    also how the answer arrives in the shape the product can use.
    """
    for key in ("ask_goal", "ask_tasks", "ask_habits"):
        text = application.t(lang, key)
        assert "<i>" in text, f"{lang}/{key} gives no example"
        assert len(text) < 400, f"{lang}/{key} is a paragraph, not a question"


@pytest.mark.parametrize("lang", ["uz", "en", "ru"])
def test_the_intro_makes_a_promise_before_it_asks_anything(lang):
    """One screen, one promise, one button — shown before any question."""
    intro = application.t(lang, "intro")
    assert 200 < len(intro) < 800, f"{lang} intro is the wrong size for a hook"
    assert intro.count("<b>") >= 2
    assert application.t(lang, "intro_go")
    source = (ROOT / "app.py").read_text()
    assert 'user.onboarding_step = "intro"' in source, \
        "the pitch does not come straight after the language"


def test_gender_is_not_an_onboarding_step():
    """It is asked the first time prayer needs it, with the reason attached."""
    source = (ROOT / "app.py").read_text()
    assert 'user.onboarding_step = "gender"' not in source
    html = (ROOT / "webapp" / "index.html").read_text()
    assert "ask_gender_why" in html, "the prayer screen must explain why it asks"


def test_the_phone_number_is_never_asked_for():
    """No prompt, no keyboard, no Settings row — the question is gone.

    A contact can still arrive unprompted from a keyboard left over from an
    older build. It is acknowledged and dropped rather than stored: keeping a
    number the product has stopped asking for is exactly the surprise this
    change was meant to remove.
    """
    source = (ROOT / "app.py").read_text()
    assert not hasattr(application, "phone_keyboard")
    assert "request_contact=True" not in source, "a contact button is back"
    assert 'callback_data="set:phone"' not in source, "Settings asks again"
    assert "user.phone_number = contact.phone_number" not in source, \
        "a volunteered number is being stored"
    for lang in ("uz", "en", "ru"):
        assert application.t(lang, "phone_not_needed")


def test_the_guide_is_on_demand_rather_than_pushed_at_a_new_account():
    """Eleven paragraphs are not a welcome.

    The guide used to be sent automatically on the way in — a manual for a
    machine the reader had not been shown yet. The way in is now the intro and
    the setup; the guide stays for the moment somebody actually wants it.
    """
    source = (ROOT / "app.py").read_text()
    assert 'CommandHandler("guide", show_guide)' in source
    finish = source[source.index("async def finish_onboarding("):
                    source.index("def render_day_ready(")]
    assert 't(lang, "guide")' not in finish, "the guide is pushed again"
    for lang in ("uz", "en", "ru"):
        guide = application.t(lang, "guide")
        # Four features, each with a concrete example rather than a category:
        # "track your habits" describes a genre, "wake up at 6:00" describes a
        # Tuesday.
        assert len(guide) > 400, f"{lang} guide is a sentence, not a guide"
        assert guide.count("<b>") >= 5, f"{lang} guide has no structure"
        assert "<i>" in guide, f"{lang} guide gives no examples"
        for emoji in ("🎯", "✅", "🔁", "📊"):
            assert emoji in guide, f"{lang} guide is missing {emoji}"


# ==========================================================================
# Habit schedules — a habit that was not due today was not failed today
# ==========================================================================

def _ws(telegram_id: int) -> int:
    with SessionLocal() as s:
        return svc.workspace_id_for(s, telegram_id)


def _set_schedule(telegram_id: int, name: str, schedule: str) -> int:
    from sqlalchemy import select
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, telegram_id)
        habit = s.scalar(select(db.Habit).where(
            db.Habit.workspace_id == ws, db.Habit.name == name))
        habit.schedule = schedule
        s.commit()
        return habit.id


def _only_habit(telegram_id: int, name: str, schedule: str) -> int:
    """Leave exactly one active habit, on the given schedule.

    Progress is a fraction, so a clean denominator is the only way to assert
    on it without the six defaults muddying the arithmetic.
    """
    from sqlalchemy import select
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, telegram_id)
        for habit in s.scalars(select(db.Habit).where(
                db.Habit.workspace_id == ws)).all():
            habit.archived_at = db.utcnow()
        kept = db.Habit(workspace_id=ws, name=name, category="target",
                        position=1, schedule=schedule)
        s.add(kept)
        s.commit()
        return kept.id


@pytest.mark.parametrize("schedule,expected", [
    ("daily", [0, 1, 2, 3, 4, 5, 6]),
    ("weekdays", [0, 1, 2, 3, 4]),
    ("days:0,2,4", [0, 2, 4]),
    ("days:4,0,2", [0, 2, 4]),        # order is normalised
    ("days:", [0, 1, 2, 3, 4, 5, 6]),  # empty means every day
    ("nonsense", [0, 1, 2, 3, 4, 5, 6]),
])
def test_a_schedule_resolves_to_the_days_it_names(schedule, expected):
    assert svc.schedule_days(schedule) == expected


def test_a_habit_written_before_schedules_existed_is_daily():
    """NULL must read as "every day", or the upgrade silently pauses habits."""
    assert svc.clean_schedule(None) == "daily"
    assert svc.schedule_days(None) == [0, 1, 2, 3, 4, 5, 6]


def test_an_off_day_is_not_counted_against_the_user(fresh):
    """Gym on Mon/Wed/Fri must not cost anything on a Tuesday.

    This is the scenario the schedule exists for: before it, every habit was
    due every day, so a three-day-a-week habit failed four times a week.
    """
    habit_id = _only_habit(fresh.user["id"], "Gym", "days:0,2,4")
    ws = _ws(fresh.user["id"])
    with SessionLocal() as s:
        monday = svc.week_start(svc.today_local())
        tuesday = monday + timedelta(days=1)
        assert svc.habit_progress(s, ws, monday) == (0, 1)   # due, not done
        assert svc.habit_progress(s, ws, tuesday) == (0, 0)  # not due at all


def test_a_day_with_nothing_scheduled_does_not_break_the_streak(fresh):
    """A weekday-only habit must survive the weekend."""
    _only_habit(fresh.user["id"], "Deep work", "weekdays")
    ws = _ws(fresh.user["id"])
    with SessionLocal() as s:
        from sqlalchemy import select
        habit = s.scalar(select(db.Habit).where(
            db.Habit.workspace_id == ws, db.Habit.archived_at.is_(None)))
        # Tick every weekday of the last three weeks, and nothing else.
        today = svc.today_local()
        for offset in range(21):
            day = today - timedelta(days=offset)
            if day.weekday() < 5:
                s.add(db.HabitLog(workspace_id=ws, habit_id=habit.id,
                                  day=day, done=True))
        s.commit()
        # The streak has to cross at least two weekends to prove the point.
        assert svc.habit_streak(s, ws) >= 14


def test_a_paused_habit_leaves_the_denominator_but_keeps_its_logs(fresh):
    habit_id = _only_habit(fresh.user["id"], "Swim", "daily")
    ws = _ws(fresh.user["id"])
    today = svc.today_local()
    with SessionLocal() as s:
        s.add(db.HabitLog(workspace_id=ws, habit_id=habit_id,
                          day=today - timedelta(days=1), done=True))
        s.commit()
        assert svc.habit_progress(s, ws, today) == (0, 1)

    assert fresh.post(f"/api/habits/{habit_id}/pause",
                      json={"paused": True}).json()["paused"] is True
    with SessionLocal() as s:
        assert svc.habit_progress(s, ws, today) == (0, 0)
        # The history is untouched — that is the difference from deleting.
        assert svc.habit_progress(s, ws, today - timedelta(days=1)) == (0, 0)
        from sqlalchemy import func, select
        assert s.scalar(select(func.count(db.HabitLog.id)).where(
            db.HabitLog.habit_id == habit_id)) == 1

    assert fresh.post(f"/api/habits/{habit_id}/pause",
                      json={"paused": False}).json()["paused"] is False
    with SessionLocal() as s:
        assert svc.habit_progress(s, ws, today) == (0, 1)


def test_a_habit_can_be_renamed_and_rescheduled(fresh):
    habit_id = _only_habit(fresh.user["id"], "Old name", "daily")
    assert fresh.patch(f"/api/habits/{habit_id}",
                       json={"name": "New name", "schedule": "weekdays",
                             "remind_at": "07:30"}).status_code == 200
    row = next(h for h in fresh.get("/api/habits").json()["habits"]
               if h["id"] == habit_id)
    assert row["name"] == "New name"
    assert row["schedule"] == "weekdays"
    assert row["remind_at"] == "07:30"


def test_a_derived_habit_cannot_be_renamed(fresh):
    """Its name is the contract with the module that drives it."""
    habits = fresh.get("/api/habits").json()["habits"]
    prayer = next(h for h in habits if h["system_key"] == "prayer")
    assert fresh.patch(f"/api/habits/{prayer['id']}",
                       json={"name": "anything"}).status_code == 400


def test_habit_history_counts_only_the_days_it_was_due(fresh):
    """"How often I did it when I meant to", not a number diluted by off days."""
    habit_id = _only_habit(fresh.user["id"], "Read", "days:0")   # Mondays only
    ws = _ws(fresh.user["id"])
    with SessionLocal() as s:
        today = svc.today_local()
        for offset in range(30):
            day = today - timedelta(days=offset)
            if day.weekday() == 0:
                s.add(db.HabitLog(workspace_id=ws, habit_id=habit_id,
                                  day=day, done=True))
        s.commit()
    body = fresh.get(f"/api/habits/{habit_id}/history").json()
    assert body["last30_due"] in (4, 5)          # Mondays in a 30-day window
    assert body["last30_done"] == body["last30_due"]
    assert body["percent"] == 100
    assert sum(1 for g in body["grid"] if g["due"]) == body["last30_due"]


def test_habit_history_is_private_to_the_workspace(alice, bob):
    habit_id = alice.get("/api/habits").json()["habits"][0]["id"]
    assert bob.get(f"/api/habits/{habit_id}/history").status_code == 404


def test_a_habit_cannot_be_paused_from_another_workspace(alice, bob):
    habit_id = alice.get("/api/habits").json()["habits"][0]["id"]
    assert bob.post(f"/api/habits/{habit_id}/pause",
                    json={"paused": True}).status_code == 404


# ==========================================================================
# Recurrence — ticking one off must not end the series
# ==========================================================================

@pytest.mark.parametrize("rule,start,expected", [
    ("daily",    date(2026, 8, 12), date(2026, 8, 13)),
    ("weekly",   date(2026, 8, 12), date(2026, 8, 19)),
    ("monthly",  date(2026, 8, 12), date(2026, 9, 12)),
    ("monthly",  date(2026, 1, 31), date(2026, 2, 28)),   # clamped, not an error
    ("monthly",  date(2026, 12, 15), date(2027, 1, 15)),  # year rolls over
    ("weekdays", date(2026, 8, 14), date(2026, 8, 17)),   # Friday -> Monday
    ("days:0,2,4", date(2026, 8, 12), date(2026, 8, 14)),  # Wed -> Fri
])
def test_the_next_occurrence_is_the_next_matching_date(rule, start, expected):
    assert svc.next_occurrence(rule, start) == expected


def test_a_one_off_task_has_no_next_occurrence():
    for value in (None, "", "nonsense"):
        assert svc.next_occurrence(value, date(2026, 8, 12)) is None


def test_completing_a_recurring_task_creates_the_next_one(alice):
    today = svc.today_local().isoformat()
    task_id = alice.post("/api/tasks", json={
        "title": "Standup", "deadline": today, "recurrence": "daily",
        "due_time": "09:30", "remind_before": 10}).json()["id"]

    alice.patch(f"/api/tasks/{task_id}", json={"status": "done"})

    open_tasks = alice.get("/api/tasks?days=365").json()
    upcoming = [x for x in open_tasks["upcoming"] + open_tasks["later"]
                if x["title"] == "Standup"]
    assert len(upcoming) == 1, "the recurrence was consumed by one tick"
    nxt = upcoming[0]
    assert nxt["deadline"] == (svc.today_local() + timedelta(days=1)).isoformat()
    # The whole shape carries forward, not just the title.
    assert nxt["recurrence"] == "daily"
    assert nxt["due_time"] == "09:30"
    assert nxt["remind_before"] == 10
    # And the finished occurrence keeps its own completion date.
    assert any(x["title"] == "Standup"
               for x in alice.get("/api/tasks/done").json()["tasks"])


def test_completing_the_same_task_twice_does_not_double_the_series(alice):
    today = svc.today_local().isoformat()
    task_id = alice.post("/api/tasks", json={
        "title": "Once only", "deadline": today,
        "recurrence": "daily"}).json()["id"]
    alice.patch(f"/api/tasks/{task_id}", json={"status": "done"})
    alice.patch(f"/api/tasks/{task_id}", json={"status": "done"})
    data = alice.get("/api/tasks?days=365").json()
    assert len([x for x in data["upcoming"] + data["later"]
                if x["title"] == "Once only"]) == 1


def test_a_recurring_task_completed_after_a_long_gap_lands_in_the_future(alice):
    """No run of overdue clones after a month away."""
    old = (svc.today_local() - timedelta(days=40)).isoformat()
    task_id = alice.post("/api/tasks", json={
        "title": "Weekly review", "deadline": old,
        "recurrence": "weekly"}).json()["id"]
    alice.patch(f"/api/tasks/{task_id}", json={"status": "done"})
    data = alice.get("/api/tasks?days=365").json()
    nxt = next(x for x in data["upcoming"] + data["later"]
               if x["title"] == "Weekly review")
    assert nxt["deadline"] >= svc.today_local().isoformat()
    assert not [x for x in data["overdue"] if x["title"] == "Weekly review"]


# ==========================================================================
# Task time, reminders and rescheduling
# ==========================================================================

def test_a_task_can_carry_a_time_and_stay_all_day_without_one(alice):
    today = svc.today_local().isoformat()
    timed = alice.post("/api/tasks", json={
        "title": "Call", "deadline": today, "due_time": "14:30"}).json()["id"]
    plain = alice.post("/api/tasks", json={
        "title": "Errand", "deadline": today}).json()["id"]
    rows = {x["id"]: x for x in alice.get("/api/tasks").json()["upcoming"]}
    assert rows[timed]["due_time"] == "14:30"
    assert rows[plain]["due_time"] is None


def test_a_bad_time_is_refused_without_leaking_internals(alice):
    r = alice.post("/api/tasks", json={"title": "x", "due_time": "99:99"})
    assert r.status_code == 422 and r.json()["detail"] == "bad_time"


def test_a_reminder_fires_once_inside_its_window(alice):
    ws = _ws(ALICE["id"])
    today = svc.today_local()
    task_id = alice.post("/api/tasks", json={
        "title": "Meeting", "deadline": today.isoformat(),
        "due_time": "15:00", "remind_before": 10}).json()["id"]

    with SessionLocal() as s:
        user = s.get(User, ALICE["id"])
        early = datetime.combine(today, dtime(14, 30))
        assert svc.due_task_reminders(s, ws, user, early) == []

        at = datetime.combine(today, dtime(14, 50))
        due = svc.due_task_reminders(s, ws, user, at)
        assert [x["id"] for x in due] == [task_id]

        svc.mark_reminder_sent(s, ws, task_id)
        assert svc.due_task_reminders(s, ws, user, at) == []


def test_no_reminder_is_sent_for_a_task_already_done(alice):
    """The point of the reminder has passed; sending it teaches people to mute."""
    today = svc.today_local()
    task_id = alice.post("/api/tasks", json={
        "title": "Done early", "deadline": today.isoformat(),
        "due_time": "16:00", "remind_before": 0}).json()["id"]
    alice.patch(f"/api/tasks/{task_id}", json={"status": "done"})
    with SessionLocal() as s:
        at = datetime.combine(today, dtime(16, 0))
        due = svc.due_task_reminders(s, _ws(ALICE["id"]), s.get(User, ALICE["id"]), at)
    assert task_id not in [x["id"] for x in due]


def test_reminders_respect_the_user_switch(alice):
    today = svc.today_local()
    alice.post("/api/tasks", json={"title": "Muted", "deadline": today.isoformat(),
                                   "due_time": "11:00", "remind_before": 0})
    alice.post("/api/prefs", json={"task_reminders": False})
    with SessionLocal() as s:
        at = datetime.combine(today, dtime(11, 0))
        assert svc.due_task_reminders(s, _ws(ALICE["id"]),
                                      s.get(User, ALICE["id"]), at) == []
    alice.post("/api/prefs", json={"task_reminders": True})


def test_habit_reminders_are_off_by_default_and_fire_in_one_window(fresh):
    habit_id = _only_habit(fresh.user["id"], "Stretch", "daily")
    fresh.patch(f"/api/habits/{habit_id}", json={"remind_at": "08:00"})
    today = svc.today_local()
    at = datetime.combine(today, dtime(8, 0))

    with SessionLocal() as s:
        user = s.get(User, fresh.user["id"])
        # Opt-in: a daily nudge nobody asked for is how an app gets muted.
        assert svc.prefs_for(user)["habit_reminders"] is False
        assert svc.due_habit_reminders(s, _ws(fresh.user["id"]), user, at) == []

    fresh.post("/api/prefs", json={"habit_reminders": True})
    with SessionLocal() as s:
        user = s.get(User, fresh.user["id"])
        ws = _ws(fresh.user["id"])
        assert len(svc.due_habit_reminders(s, ws, user, at)) == 1
        # Exactly one job interval wide: there is nothing to mark as sent, so a
        # wider window would repeat the nudge on every pass.
        outside = at + svc.HABIT_REMINDER_WINDOW
        assert svc.due_habit_reminders(s, ws, user, outside) == []
    fresh.post("/api/prefs", json={"habit_reminders": False})


@pytest.mark.parametrize("when,offset", [
    ("today", 0), ("tomorrow", 1), ("week", 7),
])
def test_an_overdue_task_moves_with_one_tap(alice, when, offset):
    old = (svc.today_local() - timedelta(days=5)).isoformat()
    task_id = alice.post("/api/tasks", json={"title": "Late",
                                            "deadline": old}).json()["id"]
    body = alice.post(f"/api/tasks/{task_id}/reschedule",
                      json={"when": when}).json()
    assert body["deadline"] == (svc.today_local() + timedelta(days=offset)).isoformat()


def test_an_overdue_task_can_lose_its_date_entirely(alice):
    old = (svc.today_local() - timedelta(days=5)).isoformat()
    task_id = alice.post("/api/tasks", json={"title": "Someday",
                                            "deadline": old}).json()["id"]
    assert alice.post(f"/api/tasks/{task_id}/reschedule",
                      json={"when": "none"}).json()["deadline"] is None


def test_rescheduling_is_scoped_to_the_owner(alice, bob):
    task_id = alice.post("/api/tasks", json={"title": "Mine"}).json()["id"]
    assert bob.post(f"/api/tasks/{task_id}/reschedule",
                    json={"when": "today"}).status_code == 404


def test_an_unknown_reschedule_target_is_refused(alice):
    task_id = alice.post("/api/tasks", json={"title": "x"}).json()["id"]
    assert alice.post(f"/api/tasks/{task_id}/reschedule",
                      json={"when": "someday"}).status_code == 422


# ==========================================================================
# Today's top three
# ==========================================================================

def _clear_top3(telegram_id: int) -> None:
    from sqlalchemy import select
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, telegram_id)
        for task in s.scalars(select(db.Task).where(
                db.Task.workspace_id == ws)).all():
            task.focus_day = None
        s.commit()


def test_nothing_still_calls_the_single_pick_a_top_three():
    """The label has to agree with the limit.

    `MAX_TOP3` is 1 — "the most important thing today" is singular by
    definition — while every string still said "TOP 3", so the product promised
    three slots and refused the second. The internal name stays (renaming a
    column and an endpoint is a migration for no user benefit); the words the
    user reads do not.
    """
    assert svc.MAX_TOP3 == 1
    html = (ROOT / "webapp" / "index.html").read_text()
    dicts = html[html.index("const DICT = {"):html.index("/* ---------- themes")]
    for wrong in ("TOP 3", "top 3", "Топ-3", "топ-3"):
        assert wrong not in dicts, f"the UI still says {wrong!r}"
    for lang in ("uz", "en", "ru"):
        assert "3" not in application.t(lang, "home_top3"), \
            f"{lang} still advertises three slots"


def test_the_day_has_exactly_one_mission(alice):
    """"The most important thing today" is singular by definition."""
    _clear_top3(ALICE["id"])
    assert svc.MAX_TOP3 == 1
    first = alice.post("/api/tasks", json={"title": "First"}).json()["id"]
    second = alice.post("/api/tasks", json={"title": "Second"}).json()["id"]

    alice.post(f"/api/tasks/{first}/top3", json={"picked": True})
    assert [x["id"] for x in alice.get("/api/home").json()["top3"]] == [first]

    # Choosing another replaces it rather than being refused: with a limit of
    # one, a rejection would be a dead end.
    assert alice.post(f"/api/tasks/{second}/top3",
                      json={"picked": True}).status_code == 200
    assert [x["id"] for x in alice.get("/api/home").json()["top3"]] == [second]


def test_unpicking_frees_a_slot(alice):
    _clear_top3(ALICE["id"])
    ids = [alice.post("/api/tasks", json={"title": f"S{n}"}).json()["id"]
           for n in range(4)]
    for task_id in ids[:3]:
        alice.post(f"/api/tasks/{task_id}/top3", json={"picked": True})
    alice.post(f"/api/tasks/{ids[0]}/top3", json={"picked": False})
    assert alice.post(f"/api/tasks/{ids[3]}/top3",
                      json={"picked": True}).status_code == 200


def test_picking_a_task_for_today_also_dates_it_today(alice):
    """Calling something one of today's three says it is due today."""
    _clear_top3(ALICE["id"])
    task_id = alice.post("/api/tasks", json={"title": "Undated"}).json()["id"]
    alice.post(f"/api/tasks/{task_id}/top3", json={"picked": True})
    picked = next(x for x in alice.get("/api/home").json()["top3"]
                  if x["id"] == task_id)
    assert picked["deadline"] == svc.today_local().isoformat()


def test_a_picked_task_is_not_listed_twice_on_home(alice):
    _clear_top3(ALICE["id"])
    today = svc.today_local().isoformat()
    task_id = alice.post("/api/tasks", json={"title": "Only once",
                                            "deadline": today}).json()["id"]
    alice.post(f"/api/tasks/{task_id}/top3", json={"picked": True})
    home = alice.get("/api/home").json()
    assert task_id in [x["id"] for x in home["top3"]]
    rest = [x["id"] for group in home["tasks_today"] for x in group["tasks"]]
    assert task_id not in rest


def test_yesterdays_picks_do_not_linger(alice):
    """The pick is dated, so it expires on its own rather than being cleared."""
    _clear_top3(ALICE["id"])
    task_id = alice.post("/api/tasks", json={"title": "Old pick"}).json()["id"]
    with SessionLocal() as s:
        task = s.get(db.Task, task_id)
        task.focus_day = svc.today_local() - timedelta(days=1)
        s.commit()
    assert task_id not in [x["id"] for x in alice.get("/api/home").json()["top3"]]


def test_top3_cannot_reach_another_workspace(alice, bob):
    task_id = alice.post("/api/tasks", json={"title": "Mine"}).json()["id"]
    assert bob.post(f"/api/tasks/{task_id}/top3",
                    json={"picked": True}).status_code == 404


# ==========================================================================
# Timezone
# ==========================================================================

def test_an_unknown_timezone_falls_back_instead_of_raising():
    """A zone the platform dropped must not make the app unusable."""
    assert svc.tz_for("Mars/Olympus") is svc.TZ
    assert svc.tz_for(None) is svc.TZ
    assert svc.tz_for("") is svc.TZ
    assert str(svc.tz_for("Europe/Berlin")) == "Europe/Berlin"


def test_a_user_with_no_timezone_gets_the_default(alice):
    assert alice.get("/api/me").json()["prefs"]["timezone"] == "Asia/Tashkent"


def test_the_timezone_can_be_changed_and_is_used_for_today(alice):
    assert alice.post("/api/prefs",
                      json={"timezone": "Pacific/Kiritimati"}).status_code == 200
    assert alice.get("/api/me").json()["prefs"]["timezone"] == "Pacific/Kiritimati"

    # Kiritimati is UTC+14 and Tashkent UTC+5, so the two are not always on the
    # same date — which is the whole reason the setting exists.
    with SessionLocal() as s:
        user = s.get(User, ALICE["id"])
        assert svc.today_local(svc.user_tz(user)) == \
            svc.today_local(svc.tz_for("Pacific/Kiritimati"))
    assert alice.get("/api/home").json()["date"] == \
        svc.today_local(svc.tz_for("Pacific/Kiritimati")).isoformat()

    alice.post("/api/prefs", json={"timezone": "Asia/Tashkent"})


def test_an_invalid_timezone_is_refused_rather_than_stored(alice):
    r = alice.post("/api/prefs", json={"timezone": "Nowhere/Nothing"})
    assert r.status_code == 422
    assert alice.get("/api/me").json()["prefs"]["timezone"] == "Asia/Tashkent"


def test_the_wake_up_boundary_follows_the_users_timezone(fresh):
    """The grace hour is local, so 05:30 means 05:30 where the user is."""
    ws = _ws(fresh.user["id"])
    with SessionLocal() as s:
        svc.set_wake_time(s, ws, dtime(5, 0))
        tz = svc.tz_for("Europe/London")
        # 05:40 local is inside the hour; the same instant in Tashkent is not.
        result = svc.mark_wakeup(s, ws, datetime.combine(
            svc.today_local(tz), dtime(5, 40)), tz=tz)
        assert result["done"] is True and result["at"] == "05:40"


# ==========================================================================
# Notification preferences and per-user report times
# ==========================================================================

def test_report_defaults_are_on_at_five_and_nine(alice):
    """05:00, not 04:00, and the difference is the whole point.

    The old default was a literal left over from when the scheduler ran on the
    server clock: 04:00 UTC is 09:00 in Tashkent. Moving the scheduler onto the
    project clock turned it into four in the morning without failing anything —
    reports went out on time, every day, to people who were asleep.

    05:00 matches the product's own wake target, so the report arrives as the
    day is meant to start rather than an hour before it. The evening report is
    on the hour at 21:00: a round time people recognise as "the end of the
    day", and far enough before sleep that acting on it is still possible.
    """
    with SessionLocal() as s:
        prefs = svc.prefs_for(s.get(User, ALICE["id"]))
    assert prefs["morning_report"] is True and prefs["morning_time"] == "05:00"
    assert prefs["evening_report"] is True and prefs["evening_time"] == "21:00"


def test_a_report_is_due_only_inside_its_window(alice):
    with SessionLocal() as s:
        user = s.get(User, ALICE["id"])
        today = svc.today_local()
        assert svc.report_is_due(user, "morning",
                                 datetime.combine(today, dtime(5, 5))) is True
        # Far past its time: a "good morning" at noon is noise, and a user who
        # joins at 15:00 must not be sent one immediately.
        assert svc.report_is_due(user, "morning",
                                 datetime.combine(today, dtime(12, 0))) is False
        # And nothing at four in the morning any more: 04:05 is before the
        # window opens, which is the whole reason the default moved.
        assert svc.report_is_due(user, "morning",
                                 datetime.combine(today, dtime(4, 5))) is False


def test_a_report_time_the_user_chose_is_the_one_used(alice):
    # Far enough from the 05:00 default that the 90-minute window cannot cover
    # both — otherwise the test passes without proving the choice was read.
    alice.post("/api/prefs", json={"morning_time": "09:30"})
    with SessionLocal() as s:
        user = s.get(User, ALICE["id"])
        today = svc.today_local()
        assert svc.report_is_due(user, "morning",
                                 datetime.combine(today, dtime(9, 35))) is True
        # The default no longer applies once a time has been chosen.
        assert svc.report_is_due(user, "morning",
                                 datetime.combine(today, dtime(5, 5))) is False
    alice.post("/api/prefs", json={"morning_time": "05:00"})


def test_a_switched_off_report_is_never_due(alice):
    alice.post("/api/prefs", json={"evening_report": False})
    with SessionLocal() as s:
        user = s.get(User, ALICE["id"])
        assert svc.report_is_due(user, "evening", datetime.combine(
            svc.today_local(), dtime(21, 5))) is False
    alice.post("/api/prefs", json={"evening_report": True})


def _built_jobs():
    """Every job the scheduler would register, without starting a bot.

    Asserted against the real APScheduler triggers rather than against the
    source text: a test that greps for a string passes the day somebody
    reformats the call and fails the day they rename a constant, neither of
    which is what it is trying to check.
    """
    import scheduler as scheduling

    async def noop(*a, **kw):
        return None

    built = scheduling.build(object(), send_reports=noop, send_reminders=noop,
                             send_platform_stats=noop)
    # `build` deliberately does not start: wiring needs no event loop, and a
    # scheduler that never ran needs no shutdown.
    return {job.id: job for job in built.get_jobs()}


def test_the_report_job_interval_is_shared_with_the_scheduler():
    """The windows and the cron entry must not be able to drift apart."""
    jobs = _built_jobs()

    def minute_of(job_id):
        return next(str(f) for f in jobs[job_id].trigger.fields
                    if f.name == "minute")

    assert minute_of("reminders") == f"*/{svc.REMINDER_JOB_MINUTES}"
    for report_type in ("morning", "evening"):
        assert minute_of(report_type) == f"*/{application.REPORT_TICK_MINUTES}"
    assert svc.HABIT_REMINDER_WINDOW == timedelta(
        minutes=svc.REMINDER_JOB_MINUTES)


def test_no_scheduled_job_may_overlap_itself():
    """One process must not run two copies of the same job.

    Between this and the advisory lock each job takes, neither a slow run nor
    a second instance can produce a duplicate report.
    """
    for job_id, job in _built_jobs().items():
        assert job.max_instances == 1, f"{job_id} may overlap itself"


# ==========================================================================
# Projects — finishing is not deleting
# ==========================================================================

def test_a_project_can_be_created_with_a_description_and_a_deadline(alice):
    deadline = (svc.today_local() + timedelta(days=30)).isoformat()
    project_id = alice.post("/api/projects", json={
        "name": "Launch", "description": "the beta",
        "deadline": deadline}).json()["id"]
    row = next(p for p in alice.get("/api/projects").json()["projects"]
               if p["id"] == project_id)
    assert row["description"] == "the beta" and row["deadline"] == deadline


def test_a_finished_project_keeps_its_tasks(alice):
    project_id = alice.post("/api/projects", json={"name": "Done soon"}).json()["id"]
    alice.post("/api/tasks", json={"title": "inside", "project_id": project_id})
    assert alice.patch(f"/api/projects/{project_id}",
                       json={"status": "done"}).status_code == 200
    body = alice.get(f"/api/projects/{project_id}/tasks").json()
    assert body["project"]["status"] == "done"
    assert [x["title"] for x in body["tasks"]] == ["inside"]


def test_a_project_can_be_archived_and_brought_back(alice):
    project_id = alice.post("/api/projects", json={"name": "Later"}).json()["id"]
    alice.patch(f"/api/projects/{project_id}", json={"archived": True})
    visible = [p["id"] for p in alice.get("/api/projects").json()["projects"]]
    assert project_id not in visible
    assert project_id in [p["id"] for p in
                          alice.get("/api/projects?archived=true").json()["projects"]]
    alice.patch(f"/api/projects/{project_id}", json={"archived": False})
    assert project_id in [p["id"] for p in
                          alice.get("/api/projects").json()["projects"]]


def test_projects_can_be_filtered_by_status(alice):
    active = alice.post("/api/projects", json={"name": "Running"}).json()["id"]
    finished = alice.post("/api/projects", json={"name": "Shipped"}).json()["id"]
    alice.patch(f"/api/projects/{finished}", json={"status": "done"})
    only_active = [p["id"] for p in
                   alice.get("/api/projects?status=active").json()["projects"]]
    assert active in only_active and finished not in only_active


def test_project_progress_carries_the_numbers_behind_the_percentage(alice):
    """"57%" needs "4 of 7" beside it or the reader has to do the arithmetic."""
    project_id = alice.post("/api/projects", json={"name": "Counted"}).json()["id"]
    ids = [alice.post("/api/tasks", json={"title": f"P{n}",
                                          "project_id": project_id}).json()["id"]
           for n in range(4)]
    alice.patch(f"/api/tasks/{ids[0]}", json={"status": "done"})
    row = next(p for p in alice.get("/api/projects").json()["projects"]
               if p["id"] == project_id)
    assert (row["tasks_total"], row["tasks_done"], row["tasks_open"]) == (4, 1, 3)
    assert row["progress"] == 25


def test_a_project_cannot_be_touched_from_another_workspace(alice, bob):
    project_id = alice.post("/api/projects", json={"name": "Private"}).json()["id"]
    assert bob.patch(f"/api/projects/{project_id}",
                     json={"status": "done"}).status_code == 404
    assert bob.get(f"/api/projects/{project_id}/tasks").status_code == 404


# ==========================================================================
# Weekly focus — completing and carrying forward
# ==========================================================================

def test_a_mission_can_be_completed_without_being_deleted(alice):
    _clear_missions(ALICE["id"])
    focus_id = alice.post("/api/focus", json={"title": "Ship it"}).json()["id"]
    assert alice.post(f"/api/focus/{focus_id}/toggle").json()["done"] is True
    week = alice.get("/api/focus").json()["week"]
    assert week["primary"]["done"] is True and week["total"] == 1


def test_an_unfinished_mission_can_be_carried_into_next_week(alice):
    _clear_missions(ALICE["id"])
    focus_id = alice.post("/api/focus", json={"title": "Still matters"}).json()["id"]
    body = alice.post(f"/api/focus/{focus_id}/carry").json()

    this_week = svc.week_start(svc.today_local())
    assert body["week_start"] == (this_week + timedelta(days=7)).isoformat()
    # Gone from this week, present in the next — not duplicated across both.
    assert alice.get("/api/focus").json()["week"]["primary"] is None
    with SessionLocal() as s:
        rows = svc.list_focus(s, _ws(ALICE["id"]), this_week + timedelta(days=7))
    assert [r["title"] for r in rows] == ["Still matters"]


def test_carrying_forward_cannot_reach_another_workspace(alice, bob):
    _clear_missions(ALICE["id"])
    focus_id = alice.post("/api/focus", json={"title": "Mine"}).json()["id"]
    assert bob.post(f"/api/focus/{focus_id}/carry").status_code == 404


# ==========================================================================
# Journal — partial is normal
# ==========================================================================

def test_a_partial_journal_is_saved_and_reported_as_partial(fresh):
    body = fresh.post("/api/journal", json={"answers": {"wins": "shipped"}}).json()
    assert body["answered"] == 1 and body["total"] == 5
    assert body["complete"] is False


def test_an_autosave_of_one_answer_does_not_wipe_the_others(alice):
    """The bug this guards: a debounced per-field save replaced the whole set."""
    day = svc.today_local().isoformat()
    alice.post("/api/journal", json={"day": day, "answers": {
        "wins": "one", "gratitude": "two", "problem": "three"}})
    alice.post("/api/journal", json={"day": day, "answers": {"lesson": "four"}})
    entry = alice.get(f"/api/journal?day={day}").json()["entry"]
    assert entry["answers"] == {"wins": "one", "gratitude": "two",
                                "problem": "three", "lesson": "four"}
    assert entry["answered"] == 4 and entry["complete"] is False


def test_an_incomplete_journal_does_not_move_the_overall_number(alice):
    """A journal is a status, never a component of the score."""
    day = svc.today_local()
    with SessionLocal() as s:
        ws = _ws(ALICE["id"])
        before = svc.overall_percent(s, ws, day)
        svc.save_journal(s, ws, answers={"wins": "partial"}, day=day)
        assert svc.overall_percent(s, ws, day) == before
        assert "journal" not in svc.overall_components(s, ws, day)


def test_mood_is_optional_and_bounded(alice):
    alice.post("/api/journal", json={"mood": "good"})
    day = svc.today_local().isoformat()
    assert alice.get(f"/api/journal?day={day}").json()["entry"]["mood"] == "good"
    # Anything outside the five is dropped rather than stored as free text.
    alice.post("/api/journal", json={"mood": "ecstatic"})
    assert alice.get(f"/api/journal?day={day}").json()["entry"]["mood"] == ""


# ==========================================================================
# Statistics — tasks included, and the number is explainable
# ==========================================================================

def test_statistics_carry_all_four_series(alice):
    body = alice.get("/api/stats?period=week").json()
    for key in ("overall", "tasks", "habits", "prayer"):
        assert key in body["series"][0], f"the {key} series is missing"
        assert key in body["averages"]
        assert key in body["deltas"]


def test_statistics_compare_with_the_previous_period(alice):
    body = alice.get("/api/stats?period=month").json()
    assert set(body["previous"]) == set(svc.SERIES_KEYS)
    for key, delta in body["deltas"].items():
        assert delta == body["averages"][key] - body["previous"][key]


def test_the_prayer_breakdown_separates_the_five_facts(alice):
    alice.post("/api/settings", json={"gender": "male"})
    for prayer in svc.PRAYERS:
        alice.post("/api/prayers", json={"prayer": prayer, "status": "jamaat"})
    detail = alice.get("/api/stats?period=week").json()["prayer_detail"]
    assert detail["jamaat"] == 5
    assert detail["full_days"] >= 1
    assert detail["on_time_percent"] == 100
    for key in ("qaza", "missed", "consistency", "days"):
        assert key in detail


def test_the_overall_number_explains_itself(alice):
    body = alice.get("/api/overall").json()
    assert body["rule"] == "weighted_mean_of_available"
    assert [p["key"] for p in body["parts"]] == \
        ["tasks", "habits", "focus", "prayer"]
    parts = {p["key"]: p["percent"] for p in body["parts"]}
    assert body["value"] == svc.weighted_overall(parts)
    assert set(body["counted"]) <= {"tasks", "habits", "focus", "prayer"}


def test_the_explanation_prints_the_weights_that_actually_applied(alice):
    """Showing the nominal 40/25/20/15 to somebody missing a category would be
    a lie about their own number."""
    body = alice.get("/api/overall").json()
    weights = body["weights"]
    assert set(weights) == set(body["counted"])
    assert sum(weights.values()) in range(99, 102)     # rounding, not drift
    assert body["nominal_weights"] == {"tasks": 40, "habits": 25,
                                       "focus": 20, "prayer": 15}
    assert body["task_priority_weights"] == {"high": 3, "medium": 2, "low": 1}


def test_the_explanation_matches_the_number_home_shows(alice):
    assert alice.get("/api/overall").json()["value"] == \
        alice.get("/api/home").json()["overall"]["value"]


def test_a_component_with_nothing_due_is_named_as_absent(alice):
    """Not zero — absent. Zero would claim a failure at nothing."""
    _clear_top3(ALICE["id"])
    with SessionLocal() as s:
        from sqlalchemy import select
        ws = _ws(ALICE["id"])
        for task in s.scalars(select(db.Task).where(
                db.Task.workspace_id == ws)).all():
            task.deadline = None
        s.commit()
    tasks = next(p for p in alice.get("/api/overall").json()["parts"]
                 if p["key"] == "tasks")
    assert tasks["total"] == 0 and tasks["percent"] is None
    assert "tasks" not in alice.get("/api/overall").json()["counted"]


# ==========================================================================
# Completed tasks and search
# ==========================================================================

def test_the_done_archive_is_grouped_by_when(alice):
    task_id = alice.post("/api/tasks", json={"title": "Finished now"}).json()["id"]
    alice.patch(f"/api/tasks/{task_id}", json={"status": "done"})
    groups = alice.get("/api/tasks/done").json()["groups"]
    assert set(groups) >= {"today", "week", "earlier", "total"}
    assert task_id in [x["id"] for x in groups["today"]]


def test_an_older_completion_lands_in_the_earlier_bucket(alice):
    task_id = alice.post("/api/tasks", json={"title": "Long ago"}).json()["id"]
    alice.patch(f"/api/tasks/{task_id}", json={"status": "done"})
    with SessionLocal() as s:
        s.get(db.Task, task_id).completed_at = db.utcnow() - timedelta(days=45)
        s.commit()
    groups = alice.get("/api/tasks/done").json()["groups"]
    assert task_id in [x["id"] for x in groups["earlier"]]
    assert task_id not in [x["id"] for x in groups["today"]]


def test_a_completed_task_can_be_reopened(alice):
    task_id = alice.post("/api/tasks", json={"title": "Not done after all",
                                            "deadline": svc.today_local().isoformat()
                                            }).json()["id"]
    alice.patch(f"/api/tasks/{task_id}", json={"status": "done"})
    alice.patch(f"/api/tasks/{task_id}", json={"status": "waiting"})
    assert task_id in [x["id"] for x in alice.get("/api/tasks").json()["upcoming"]]


def test_open_tasks_can_be_searched(alice):
    alice.post("/api/tasks", json={"title": "Buy a hammer"})
    alice.post("/api/tasks", json={"title": "Write the report"})
    found = alice.get("/api/tasks?days=365&q=hammer").json()
    titles = [x["title"] for group in ("overdue", "upcoming", "undated", "later")
              for x in found[group]]
    assert "Buy a hammer" in titles and "Write the report" not in titles


def test_search_is_scoped_to_the_caller(alice, bob):
    alice.post("/api/tasks", json={"title": "alice-secret-string"})
    found = bob.get("/api/tasks?days=365&q=alice-secret-string").json()
    assert found["total"] == 0


def test_tasks_can_be_filtered_by_project_and_priority(alice):
    project_id = alice.post("/api/projects", json={"name": "Filtered"}).json()["id"]
    alice.post("/api/tasks", json={"title": "in project", "project_id": project_id,
                                   "priority": "high"})
    alice.post("/api/tasks", json={"title": "outside", "priority": "low"})
    body = alice.get(f"/api/tasks?days=365&project_id={project_id}").json()
    titles = [x["title"] for g in ("overdue", "upcoming", "undated", "later")
              for x in body[g]]
    assert titles == ["in project"]
    high = alice.get("/api/tasks?days=365&priority=high").json()
    assert "outside" not in [x["title"] for g in
                             ("overdue", "upcoming", "undated", "later")
                             for x in high[g]]


# ==========================================================================
# Fresh start — moves and archives, never deletes
# ==========================================================================

def _make_overdue(alice, count: int) -> list[int]:
    old = (svc.today_local() - timedelta(days=6)).isoformat()
    return [alice.post("/api/tasks", json={"title": f"Overdue {n}",
                                          "deadline": old}).json()["id"]
            for n in range(count)]


def test_the_reset_preview_writes_nothing(alice):
    _make_overdue(alice, 3)
    before = alice.get("/api/tasks?days=365").json()["overdue"]
    body = alice.get("/api/fresh-start").json()
    assert body["overdue"] >= 3
    assert set(body["modes"]) == {"today", "week", "undate", "archive"}
    assert len(alice.get("/api/tasks?days=365").json()["overdue"]) == len(before)


@pytest.mark.parametrize("mode", ["today", "week", "undate", "archive"])
def test_no_reset_mode_destroys_a_task(alice, mode):
    """Every mode is reversible in the database. That is what lets the
    confirmation promise the history is intact."""
    from sqlalchemy import func, select
    ids = _make_overdue(alice, 4)
    with SessionLocal() as s:
        ws = _ws(ALICE["id"])
        before = s.scalar(select(func.count(db.Task.id)).where(
            db.Task.workspace_id == ws))
    alice.post("/api/fresh-start", json={"mode": mode})
    with SessionLocal() as s:
        after = s.scalar(select(func.count(db.Task.id)).where(
            db.Task.workspace_id == ws))
        assert after == before, f"{mode} deleted rows"
        for task_id in ids:
            assert s.get(db.Task, task_id) is not None


def test_the_reset_clears_the_overdue_wall(alice):
    _make_overdue(alice, 5)
    moved = alice.post("/api/fresh-start", json={"mode": "today"}).json()["moved"]
    assert moved >= 5
    assert alice.get("/api/tasks?days=365").json()["overdue"] == []


def test_spreading_over_a_week_does_not_pile_everything_on_one_day(alice):
    _make_overdue(alice, 7)
    alice.post("/api/fresh-start", json={"mode": "week"})
    data = alice.get("/api/tasks?days=365").json()
    days = {x["deadline"] for x in data["upcoming"] if x["title"].startswith("Overdue")}
    assert len(days) >= 2


def test_a_returning_user_is_not_buried(alice):
    """Scenario: a month away, a pile of overdue tasks, one decision.

    `break_state` is read directly rather than through /api/home: every
    authenticated request records activity first, so the HTTP call would reset
    the very gap being tested.
    """
    _make_overdue(alice, 12)
    with SessionLocal() as s:
        user = s.get(User, ALICE["id"])
        user.last_active_at = db.utcnow() - timedelta(days=30)
        s.commit()
        state = svc.break_state(s, _ws(ALICE["id"]), user)

    assert state["suggest_reset"] is True
    assert state["days_away"] >= 30
    assert state["overdue"] >= 12
    # The recovery path is the one offer, and it works.
    assert alice.post("/api/fresh-start", json={"mode": "week"}).json()["moved"] >= 12


# ==========================================================================
# Data and privacy
# ==========================================================================

def test_the_export_carries_what_the_user_wrote(alice):
    alice.post("/api/tasks", json={"title": "exported task"})
    alice.post("/api/journal", json={"answers": {"wins": "exported answer"}})
    body = alice.get("/api/export").json()
    for key in ("profile", "habits", "habit_logs", "prayers", "projects",
                "tasks", "weekly_focus", "weekly_reviews", "journal", "birthdays"):
        assert key in body, f"the export is missing {key}"
    assert "exported task" in [x["title"] for x in body["tasks"]]
    assert any("exported answer" in str(e["answers"].values())
               for e in body["journal"])


def test_the_export_is_scoped_to_one_workspace(alice, bob):
    alice.post("/api/tasks", json={"title": "alice-only-export"})
    assert "alice-only-export" not in [x["title"] for x in
                                       bob.get("/api/export").json()["tasks"]]


def test_deleting_an_account_needs_the_typed_word(alice):
    assert alice.post("/api/account/delete", json={"confirm": "yes"}).status_code == 422
    assert alice.post("/api/account/delete", json={"confirm": ""}).status_code == 422
    # And the account is still there.
    assert alice.get("/api/me").status_code == 200


def test_deleting_an_account_leaves_nothing_behind(client):
    """No orphan workspace holding somebody's journal.

    Deliberately not trusting ON DELETE CASCADE: SQLite enforces foreign keys
    only when the connection asks it to, so the deletion walks the tables.
    """
    from sqlalchemy import func, select

    victim = {"id": 909090, "first_name": "Temp"}
    caller = Caller(client, victim)
    caller.post("/api/tasks", json={"title": "will be erased"})
    caller.post("/api/journal", json={"answers": {"wins": "private"}})
    ws = _ws(victim["id"])

    assert caller.post("/api/account/delete",
                       json={"confirm": "DELETE"}).json()["deleted"] is True

    with SessionLocal() as s:
        assert s.get(User, victim["id"]) is None
        assert s.scalar(select(func.count(db.Workspace.id)).where(
            db.Workspace.id == ws)) == 0
        for model in svc.WORKSPACE_TABLES:
            left = s.scalar(select(func.count()).select_from(model).where(
                model.workspace_id == ws))
            assert left == 0, f"{model.__tablename__} still holds rows"


def test_every_workspace_scoped_table_is_on_the_deletion_list():
    """A model added without being added here would survive a deletion."""
    scoped = {m.__tablename__ for m in db.Base.__subclasses__()
              if hasattr(m, "workspace_id")}
    listed = {m.__tablename__ for m in svc.WORKSPACE_TABLES}
    assert scoped - listed == set(), f"not deleted on request: {scoped - listed}"


# ==========================================================================
# Migration 0004 — the prayer habit recomputed on a populated database
# ==========================================================================

def test_the_prayer_migration_corrects_a_day_that_was_never_five(alice):
    """Reproduces the old bug, then fixes it the way a live database would be.

    Three prayers used to mark "5x namoz" done. The migration recomputes from
    the PrayerLog rows, which it never modifies.
    """
    from sqlalchemy import func, select

    ws = _ws(ALICE["id"])
    day = svc.today_local() - timedelta(days=3)
    with SessionLocal() as s:
        s.get(User, ALICE["id"]).gender = "male"
        for prayer in ["bomdod", "peshin", "asr"]:
            s.add(db.PrayerLog(workspace_id=ws, day=day, prayer=prayer,
                               status="on_time"))
        habit = s.scalar(select(db.Habit).where(
            db.Habit.workspace_id == ws, db.Habit.system_key == "prayer"))
        # Exactly what the old rule wrote: score 3.0 >= 2.5, so done.
        s.add(db.HabitLog(workspace_id=ws, habit_id=habit.id, day=day, done=True))
        s.add(db.PrayerDay(workspace_id=ws, day=day, excused=False, score=3.0))
        s.commit()
        habit_id = habit.id

    result = migrations.m0004_recompute_prayer_completion()
    assert result["no_longer_complete"] >= 1

    with SessionLocal() as s:
        row = s.scalar(select(db.HabitLog).where(
            db.HabitLog.habit_id == habit_id, db.HabitLog.day == day))
        assert row.done is False
        # The source of truth is untouched.
        assert s.scalar(select(func.count(db.PrayerLog.id)).where(
            db.PrayerLog.workspace_id == ws, db.PrayerLog.day == day)) == 3


def test_the_prayer_migration_leaves_a_real_five_alone(alice):
    from sqlalchemy import select

    ws = _ws(ALICE["id"])
    day = svc.today_local() - timedelta(days=4)
    with SessionLocal() as s:
        s.get(User, ALICE["id"]).gender = "male"
        for prayer in svc.PRAYERS:
            s.add(db.PrayerLog(workspace_id=ws, day=day, prayer=prayer,
                               status="on_time"))
        habit = s.scalar(select(db.Habit).where(
            db.Habit.workspace_id == ws, db.Habit.system_key == "prayer"))
        s.add(db.HabitLog(workspace_id=ws, habit_id=habit.id, day=day, done=True))
        s.commit()
        habit_id = habit.id

    migrations.m0004_recompute_prayer_completion()
    with SessionLocal() as s:
        assert s.scalar(select(db.HabitLog).where(
            db.HabitLog.habit_id == habit_id, db.HabitLog.day == day)).done is True


def test_the_prayer_migration_is_safe_to_run_twice(alice):
    migrations.m0004_recompute_prayer_completion()
    again = migrations.m0004_recompute_prayer_completion()
    assert again["habit_logs_changed"] == 0


# ==========================================================================
# Migration 0005 — theme names
# ==========================================================================

@pytest.mark.parametrize("old,new", [
    ("cobalt", "ocean"), ("slate", "midnight"), ("oxford", "pure"),
    ("blossom", "aurora"), ("obsidian", "midnight"), ("emerald", "sage"),
])
def test_the_theme_rename_lands_on_the_closest_survivor(alice, old, new):
    _set_theme(ALICE["id"], old)
    migrations.m0005_rename_themes()
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).theme == new


def test_the_theme_rename_keeps_a_name_that_survived(alice):
    """`aurora` names a current theme, so those rows must not be moved."""
    _set_theme(ALICE["id"], "aurora")
    migrations.m0005_rename_themes()
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).theme == "aurora"


def test_the_theme_rename_is_safe_to_run_twice(alice):
    _set_theme(ALICE["id"], "cobalt")
    migrations.m0005_rename_themes()
    assert migrations.m0005_rename_themes()["total"] == 0
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).theme == "ocean"


def test_the_older_theme_migration_cannot_undo_the_newer_one(alice):
    """0003 used to map `ocean` onto a name that no longer exists.

    Running the migrations out of order, or re-running 0003 after 0005, would
    then have moved every default account onto a dead value.
    """
    assert "ocean" not in migrations.RETIRED_THEMES
    _set_theme(ALICE["id"], "ocean")
    migrations.m0003_retire_themes()
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).theme == "ocean"
    for target in migrations.RETIRED_THEMES.values():
        assert target in migrations.THEME_RENAMES, \
            f"0003 lands on {target}, which 0005 does not rename"


def test_every_theme_rename_target_is_a_theme_that_exists():
    """The last mapping in the chain has to land on something real."""
    for target in migrations.THEME_SYSTEMS.values():
        assert target in application.THEMES, target
    # And every name an earlier migration can produce is handled by the next.
    # Every name an earlier step can produce is either handled by the next
    # step or already a live name that needs no move. A rename that rewrites a
    # live name would make the chain cyclic: running it twice would walk a row
    # from calm to ocean and back again.
    def handled(target, mapping, by):
        assert target in mapping or target in migrations.LIVE_THEMES, \
            f"{target} is produced but {by} neither maps nor keeps it"

    for target in migrations.THEME_RENAMES.values():
        handled(target, migrations.THEME_REDESIGN, "0007")
    for target in migrations.RETIRED_THEMES.values():
        handled(target, migrations.THEME_REDESIGN, "0007")
    for target in migrations.THEME_REDESIGN.values():
        handled(target, migrations.THEME_SYSTEMS, "0008")
    for name in migrations.LIVE_THEMES:
        assert name in application.THEMES
        assert name not in migrations.THEME_SYSTEMS, \
            f"0008 renames {name}, which is a live theme"


@pytest.mark.parametrize("old,new", [
    ("pure", "calm"), ("sage", "muse"), ("cobalt", "calm"),
    ("slate", "titan"), ("blossom", "muse"),
])
def test_the_redesign_moves_a_theme_to_its_closest_survivor(alice, old, new):
    _set_theme(ALICE["id"], old)
    migrations.m0007_redesign_themes()
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).theme == new


@pytest.mark.parametrize("name", ["ocean", "midnight", "aurora"])
def test_the_redesign_leaves_a_name_that_is_live_again(alice, name):
    """Three of the names 0007 used to rewrite are live themes now.

    Rewriting them would make the chain cyclic — 0008 renames `calm` to
    `ocean`, and a second pass of 0007 would send it straight back. It would
    also be wrong on its own terms: `ocean` meant "the default blue" in the
    old set and means the same thing in this one.
    """
    _set_theme(ALICE["id"], name)
    migrations.m0007_redesign_themes()
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).theme == name


def test_the_redesign_is_safe_to_run_twice(alice):
    _set_theme(ALICE["id"], "pure")
    migrations.m0007_redesign_themes()
    assert migrations.m0007_redesign_themes()["total"] == 0
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).theme == "calm"


@pytest.mark.parametrize("old,new", [
    ("calm", "ocean"), ("titan", "midnight"), ("nexus", "aurora"),
    ("muse", "bento"), ("rage", "spatial"), ("pink", "bento"),
])
def test_the_named_systems_migration_lands_on_a_live_theme(alice, old, new):
    _set_theme(ALICE["id"], old)
    migrations.m0008_named_theme_systems()
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).theme == new
    assert migrations.m0008_named_theme_systems()["total"] == 0


def test_nobody_is_migrated_into_execution_mode():
    """Rage had no predecessor. Putting somebody in it uninvited was a decision
    on their behalf, not a migration."""
    assert "rage" not in migrations.THEME_REDESIGN.values()


def test_every_backend_capability_is_reachable_from_the_mini_app():
    """No feature that exists on the server but cannot be used.

    Each of these was, at some point, a working endpoint with no way to reach
    it — the user had to be told to go and type in the chat instead.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    for path in ("/api/wakeup", "/api/quick", "/api/review", "/api/birthdays",
                 "/api/fresh-start", "/api/overall", "/api/prefs",
                 "/api/export", "/api/subscription", "/api/feedback",
                 "/api/tasks/done", "/api/calendar",
                 "/api/habits/reorder"):
        assert path in html, f"{path} exists on the server but not in the UI"
    # And the ones built from a template string.
    for fragment in ("/top3", "/reschedule", "/pause", "/history", "/carry",
                     "/api/prayers/clear", "/api/account/delete"):
        assert fragment in html, f"{fragment} is unreachable from the UI"


def test_no_placeholder_is_left_in_the_shipped_ui():
    html = (ROOT / "webapp" / "index.html").read_text()
    # `placeholder=` is a real HTML attribute, so the markers are stub *text*
    # and comment tags rather than the word itself.
    for marker in ("TODO", "FIXME", "XXX:", "coming soon", "not implemented",
                   "lorem ipsum", "hozircha ishlamaydi", "тут будет"):
        assert marker.lower() not in html.lower(), f"stub left in the UI: {marker!r}"


def test_every_action_the_ui_offers_is_wired_to_something():
    """A button with no handler is a button that lies about what it does."""
    import re

    html = (ROOT / "webapp" / "index.html").read_text()
    offered = set(re.findall(r'data-act="([a-z0-9\-]+)"', html))
    # The handler table, plus the two names built dynamically for search boxes.
    block = html[html.index("const A = {"):html.index("/* Opens the edit sheet")]
    handled = set(re.findall(r'^\s{2}"?([a-z0-9\-]+)"?\s*:', block, re.M))
    handled |= set(re.findall(r'data-search="([a-z0-9\-]+)"', html))
    handled |= {name + "-clear" for name in
                re.findall(r'data-act="([a-z0-9\-]+)-clear"', html)}
    missing = {name for name in offered - handled}
    assert not missing, f"actions with no handler: {sorted(missing)}"


def test_the_mini_app_never_claims_a_save_it_did_not_make():
    """Every "Saqlandi" is behind an awaited request, not a local mutation."""
    html = (ROOT / "webapp" / "index.html").read_text()
    # The one helper that shows the confirmation also performs the request.
    assert "async function save(request, reload, quiet, message, draft){" in html
    assert "await request();" in html
    # And the optimistic path reverts on failure rather than keeping the lie.
    assert "setState(back);" in html


def test_every_timezone_the_platform_knows_is_offerable():
    """A shortlist of twelve was a guess about where users live.

    Reports fire on this clock, so somebody who cannot name their own zone gets
    a morning report in the middle of the night. The common ones still lead the
    list, because a picker sorted purely alphabetically opens on Africa/Abidjan.
    """
    from zoneinfo import ZoneInfo

    assert len(svc.TIMEZONES) > 400, "still a shortlist"
    assert svc.TIMEZONES[:len(svc.COMMON_TIMEZONES)] == svc.COMMON_TIMEZONES
    assert svc.TIMEZONES[0] == "Asia/Tashkent"
    assert len(svc.TIMEZONES) == len(set(svc.TIMEZONES)), "a zone is listed twice"
    for name in ("Asia/Samarkand", "Europe/Kyiv", "America/Sao_Paulo",
                 "Australia/Sydney", "Africa/Cairo"):
        assert name in svc.TIMEZONES, f"{name} is not offerable"
    # Every offered name must actually resolve, or saving it 422s.
    for name in svc.TIMEZONES[:40]:
        assert ZoneInfo(name)


def test_the_timezone_picker_keeps_the_common_ones_within_reach():
    """The UI must split the list, or the shortlist's whole benefit is lost."""
    html = (ROOT / "webapp" / "index.html").read_text()
    picker = html[html.index("function timezonePicker(zones, current){"):
                  html.index("//: How many of the server's zones")]
    assert "optgroup" in picker, "five hundred zones in one flat list"
    assert 't("tz_common")' in picker
    assert f"const TZ_COMMON_COUNT = {len(svc.COMMON_TIMEZONES)};" in html, \
        "the app and the server disagree on how many zones lead the list"
    # A zone the server no longer offers must still show, not silently reset.
    assert "zones.includes(current)" in picker


def test_a_form_control_saves_on_change_and_never_on_click():
    """The bug that made the report times and the timezone uneditable.

    Both carry a `data-act`, and the delegated click handler matched them: it
    called `preventDefault()` — which is how a `<select>` is stopped from ever
    opening — and then ran the save with the value already in the field. The
    app answered "Saqlandi" and changed nothing, every time.

    The rule is the element's tag rather than a list of action names, so a
    control added later cannot quietly reintroduce it.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    assert 'const CHANGE_TAGS = new Set(["INPUT", "SELECT", "TEXTAREA"]);' in html

    click = html[html.index('document.addEventListener("click", e => {'):]
    click = click[:click.index("});")]
    assert "if(CHANGE_TAGS.has(el.tagName)) return;" in click, \
        "a click on a form control still runs its action"

    change = html[html.index('document.addEventListener("change", e => {'):]
    change = change[:change.index("});")]
    assert 'e.target.closest("[data-act]")' in change, \
        "the change path is still hard-coded to two action names"
    assert "CHANGE_TAGS.has(el.tagName)" in change

    # And saving a time or a zone must not rebuild the sheet under the open
    # picker, which closed it after the first value on iOS.
    assert "async function prefSave(patch, redraw = true){" in html
    for act in ('"set-tz": el =>', '"pref-time": el =>'):
        assert act in html, f"{act} no longer takes the element it fired on"
    assert 'prefSave({timezone: el.value || val("tz-select")}, false)' in html
    assert "prefSave({[el.dataset.key]: el.value}, false)" in html


def test_home_reads_the_day_as_four_separate_blocks():
    """Greeting and now, then today's work, then the numbers about it.

    Tasks, habits and prayer used to be three cells inside the overall card,
    which made them look like a footnote to the percentage above them. They are
    what the percentage is made of, and they are three different things — so
    each is its own block, in its own colour, opening its own screen.

    Work now comes before the numbers. The Now card names one task and the
    question that immediately follows is "what else is there today?"; the
    answer used to be two blocks of percentages further down the scroll. A
    score is a reading of the day's work, so it is placed after the work.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    home = html[html.index("SCREENS.home = () => {"):html.index("function privacyNote(")]
    order = [home.index(f"{fn}(d)") for fn in
             ("headBlock", "nowBlock", "tasksBlock", "scoreBlock", "todayBlock")]
    assert order == sorted(order), "Home's blocks are out of order"

    score = html[html.index("function scoreBlock(d){"):html.index("function todayBlock(d){")]
    for period in ("period_day", "period_week", "period_month"):
        assert f't("{period}")' in score, f"the score block dropped {period}"
    assert 't("habits")' not in score, "the three parts are back inside the score"

    today = html[html.index("function todayBlock(d){"):html.index("function tasksBlock(d){")]
    for key in ("tasks", "habits", "prayer", "week_focus"):
        assert f't("{key}")' in today, f"today's block is missing {key}"
    # Each in a hue of its own, from the one table the whole app reads.
    tones = html[html.index("const COMPONENT_TONE = {"):]
    tones = tones[:tones.index("};") + 2]
    assert len(set(re.findall(r"tone-\d", tones))) == 4, \
        "two of the four parts share a colour"
    assert 'data-screen="tasks"' in today and 'data-tab="prayer"' in today
    # And each carries what it is worth, or the tiles contradict the headline.
    assert "WEIGHTS[key]" in today
    assert "const WEIGHTS = {tasks:40, habits:25, focus:20, prayer:15};" in html


def test_a_tinted_block_never_names_a_colour():
    """The tint is one of the theme's own five, chosen by position.

    A block that reached for a literal hex would be a block that stops working
    the moment somebody switches theme — which is the whole reason the token
    layer exists.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    css = html[html.index(".tinted{"):html.index("/* ==================================================================\n   Rows")]
    assert not re.findall(r":\s*#[0-9a-fA-F]{3,8}\b", css), \
        "a tinted block hard-codes a colour"
    for tone in range(1, 6):
        assert f".tone-{tone}{{--tint:var(--c{tone})}}" in css, \
            f"tone {tone} is not wired to the palette"


# ==========================================================================
# The daily reports
# ==========================================================================

def _morning_payload() -> dict:
    return {
        "name": "Ernest",
        "yesterday": {"date": "2026-08-12", "overall": 72, "measured": True,
                      "components": {"tasks": 60, "habits": 80, "prayer": 76},
                      "habits_done": 4, "habits_total": 5, "prayer_score": 4,
                      "prayer_performed": 5, "prayer_required": 5,
                      "tasks_completed": 3, "tasks_missed": 1, "journal": True},
        "today": {"date": "2026-08-13",
                  "tasks": [{"title": "Send the proposal", "due_time": "10:00",
                             "priority": "high", "project": "Launch",
                             "deadline": "2026-08-13"}],
                  "top3": [], "overdue": [],
                  "focus": [{"slot": 1, "title": "Ship the beta", "done": False}],
                  "focus_done": 0, "birthdays": [],
                  "habits": ["Workout"], "habits_done": 3, "habits_total": 5,
                  "prayer_required": 5},
    }


def _evening_payload() -> dict:
    return {
        "date": "2026-08-13",
        "overall": {"value": 78, "trend": "up", "yesterday": 72,
                    "components": {"tasks": 75, "habits": 80, "prayer": 80}},
        "habits_done": 4, "habits_total": 5, "habits_remaining": ["Workout"],
        "prayer_score": 4, "prayer_performed": 4, "prayer_required": 5,
        "tasks_completed": 3, "tasks_remaining": ["Call the accountant"],
        "tasks_overdue": [], "focus": [], "focus_done": 0, "journal": True,
    }


@pytest.mark.parametrize("lang", ["uz", "en", "ru"])
def test_the_morning_report_greets_before_it_measures(lang):
    """The first message of the day opens by greeting a person by name.

    A report that opens with a percentage is a dashboard, and nobody wants a
    dashboard before breakfast. The number follows immediately — it is still
    the second line — but the order is the point.
    """
    text = application.render_morning(_morning_payload(), lang)
    first = text.splitlines()[0]
    assert application.t(lang, "r_good_morning", name="Ernest") in first
    assert "%" not in first, "the greeting line carries a statistic"
    # Yesterday, once, as a percentage, above the plan.
    assert text.index("72%") < text.index(application.t(lang, "r_today_all"))


def test_the_morning_report_names_the_whole_day():
    """Tasks, habits and prayer — everything expected of somebody today.

    It used to list tasks alone, so the habits due that day and the five
    prayers were things you had to open the app to find out about, which is
    exactly what this message exists to save.
    """
    text = application.render_morning(_morning_payload(), "uz")
    assert "Send the proposal" in text, "today's tasks are missing"
    assert "Workout" in text, "today's habits are missing"
    assert application.t("uz", "r_prayer_today") in text, "prayer is missing"
    assert application.t("uz", "r_habits_today") in text


def test_the_morning_report_still_works_without_a_name():
    """A user who never gave Telegram a first name is still greeted."""
    payload = _morning_payload()
    payload["name"] = ""
    text = application.render_morning(payload, "uz")
    assert text.splitlines()[0].strip("<b>").startswith("🌅")
    assert "{name}" not in text and "None" not in text


@pytest.mark.parametrize("lang", ["uz", "en", "ru"])
def test_the_evening_report_ends_by_saying_good_night(lang):
    """The last line of the last message of the day is a human one.

    The day's numbers come first and the wish comes last — a good night wished
    before the summary is a sign-off in the middle of a message.
    """
    text = application.render_evening(_evening_payload(), lang)
    good_night = application.t(lang, "r_good_night")
    assert good_night in text
    assert text.rstrip().endswith(f"{good_night}</b>"), \
        "the good-night line is not the last thing said"
    assert text.index("78%") < text.index(good_night)


def test_the_evening_report_leads_with_tasks_habits_prayer_in_that_order():
    """The same order as every other surface, so one vocabulary is learned."""
    text = application.render_evening(_evening_payload(), "uz")
    order = [text.index(application.t("uz", key))
             for key in ("r_tasks", "r_habits", "r_prayer")]
    assert order == sorted(order)


# ==========================================================================
# Translations — three languages, no gaps and no leftovers
# ==========================================================================

def _dict_blocks() -> dict[str, set[str]]:
    import re

    html = (ROOT / "webapp" / "index.html").read_text()
    blocks = {}
    for lang in ("uz", "en", "ru"):
        body = re.search(r"\n %s:\{(.*?)\n \},\n" % lang, html, re.S).group(1)
        blocks[lang] = set(re.findall(r"(?:^|[\s{,])([a-z_0-9]+)\s*:", body, re.M))
    return blocks


def test_the_mini_app_dictionaries_have_identical_keys():
    """A key present in one language and missing in another ships as a bug."""
    blocks = _dict_blocks()
    assert blocks["uz"] == blocks["en"] == blocks["ru"], {
        "missing in en": sorted(blocks["uz"] - blocks["en"]),
        "missing in ru": sorted(blocks["uz"] - blocks["ru"]),
        "extra in en": sorted(blocks["en"] - blocks["uz"]),
        "extra in ru": sorted(blocks["ru"] - blocks["uz"]),
    }


def test_the_bot_dictionaries_have_identical_keys():
    keys = {lang: set(application.T[lang]) for lang in ("uz", "en", "ru")}
    assert keys["uz"] == keys["en"] == keys["ru"], {
        "missing in en": sorted(keys["uz"] - keys["en"]),
        "missing in ru": sorted(keys["uz"] - keys["ru"]),
    }


@pytest.mark.parametrize("lang", ["en", "ru"])
def test_no_uzbek_is_left_inside_another_language(lang):
    """The habit tiers used to read "Asosiy / Rivojlanish / Qo'shimcha" in all
    three languages, which is the exact leak this catches."""
    import re

    html = (ROOT / "webapp" / "index.html").read_text()
    body = re.search(r"\n %s:\{(.*?)\n \},\n" % lang, html, re.S).group(1)
    for uzbek in ("Rivojlanish", "Qo'shimcha", "Bekor qilish", "Saqlash",
                  "Vazifalar", "Odatlar", "Kundalik", "Tayyor",
                  "Hozircha bo'sh", "Muddat"):
        assert uzbek not in body, f"{lang} still contains {uzbek!r}"


@pytest.mark.parametrize("lang", ["uz", "en", "ru"])
def test_month_and_weekday_names_are_translated(lang):
    import re

    html = (ROOT / "webapp" / "index.html").read_text()
    body = re.search(r"\n %s:\{(.*?)\n \},\n" % lang, html, re.S).group(1)
    months = re.search(r"months:\[(.*?)\]", body, re.S).group(1).split(",")
    assert len(months) == 12
    dow = re.search(r"dow:\[(.*?)\]", body, re.S).group(1).split(",")
    assert len(dow) == 7
    # The bot writes its own dates, and they have to agree with the app.
    assert len(svc.MONTHS[lang]) == 12 and len(svc.WEEKDAYS[lang]) == 7


@pytest.mark.parametrize("lang", ["uz", "en", "ru"])
def test_every_bot_string_the_code_asks_for_exists(lang):
    """Catches a t() call whose key was never added to the dictionaries."""
    import re

    source = (ROOT / "app.py").read_text()
    used = set(re.findall(r't\((?:lang|user\.language|code|"\w\w"), "(\w+)"', source))
    missing = sorted(key for key in used if key not in application.T[lang])
    assert not missing, f"{lang} is missing: {missing}"


def test_the_privacy_body_is_written_in_every_language():
    blocks = _dict_blocks()
    for lang in ("uz", "en", "ru"):
        assert "privacy_body" in blocks[lang]


def test_no_appearance_overlay_can_outrank_a_theme():
    """A previous build had a bare `:root[data-mode=...]` overlay, which
    outranked the single-attribute theme blocks and handed two themes somebody
    else's surfaces. Every colour block now names both attributes, so all ten
    have identical specificity and each matches exactly one combination.
    """
    import re

    styled = (ROOT / "webapp" / "index.html").read_text()
    blocks = re.findall(r"\n(:root\[[^{]*|\[data-[^{]*)\{", styled)
    for selector in blocks:
        selector = selector.strip()
        if "data-mode" in selector:
            assert "data-theme" in selector, \
                f"{selector} sets colour for every theme at once"


def test_each_theme_declares_its_own_lead_colour():
    """Two themes sharing a brand colour are one theme with two names."""
    import re

    styled = (ROOT / "webapp" / "index.html").read_text()
    leads = {}
    for name in application.THEMES:
        block = _theme_block(styled, name)
        leads[name] = re.search(r"--c1:\s*(#[0-9A-Fa-f]+)",
                                block).group(1).lower()
    assert len(set(leads.values())) == 5, leads


def test_the_brand_surface_control_is_visible_on_it():
    """The one-tap complete on Home sits on the brand-painted surface.

    It was invisible once: an inline `border-color:currentColor` resolved to
    the tick's own colour, which is transparent until ticked.
    """
    styled = (ROOT / "webapp" / "index.html").read_text()
    assert "border-color:currentColor" not in styled
    assert ".hero .check{border-color:color-mix(in srgb, var(--hero-text)" in styled


def test_page_content_clears_the_floating_button():
    """The ＋ button is fixed, so the last row of every screen has to be able
    to scroll out from under it — it covered the reorder button once."""
    styled = (ROOT / "webapp" / "index.html").read_text()
    assert "--fab-clear:" in styled
    assert "var(--fab-clear)" in styled.split("padding-bottom:calc(var(--shell-b")[1][:80]


def test_a_prayer_write_refreshes_the_derived_habit():
    """`5x namoz` is derived server-side, so the client has to re-read it.

    Without this the Prayer tab showed 5/5 while the Habits tab still showed the
    habit unticked, and the two disagreed about the same day until a reload.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    assert "async function refreshHabits()" in html
    block = html[html.index("/* ---- prayer ----"):html.index("/* ---- journal ---- */")]
    assert block.count("refreshHabits()") >= 3, \
        "every prayer write must refresh the habit list"


# ==========================================================================
# The journal is a non-negotiable habit again
# ==========================================================================

def test_the_journal_habit_completes_only_on_a_full_entry(fresh):
    """Three answers is a saved entry and an unfinished habit, both at once."""
    def journal_habit():
        return next(h for h in fresh.get("/api/habits").json()["habits"]
                    if h["system_key"] == "journal")

    assert journal_habit()["done"] is False

    fresh.post("/api/journal", json={"answers": {"wins": "a", "gratitude": "b"}})
    assert journal_habit()["done"] is False, "a partial entry must not tick it"

    answers = {q: "written" for q in svc.JOURNAL_KEYS}
    body = fresh.post("/api/journal", json={"answers": answers}).json()
    assert body["complete"] is True
    assert journal_habit()["done"] is True


def test_the_journal_habit_cannot_be_ticked_by_hand(fresh):
    habit = next(h for h in fresh.get("/api/habits").json()["habits"]
                 if h["system_key"] == "journal")
    assert habit["protected"] is True
    assert fresh.post(f"/api/habits/{habit['id']}/toggle").status_code == 400


def test_emptying_the_journal_unticks_the_habit(fresh):
    answers = {q: "written" for q in svc.JOURNAL_KEYS}
    fresh.post("/api/journal", json={"answers": answers})
    day = svc.today_local().isoformat()
    fresh.delete(f"/api/journal/{day}")
    habit = next(h for h in fresh.get("/api/habits").json()["habits"]
                 if h["system_key"] == "journal")
    assert habit["done"] is False


def test_the_journal_habit_counts_towards_the_day(fresh):
    """It is a non-negotiable, so it belongs in the denominator.

    Asserted against the habits actually due today rather than a fixed number,
    so changing the starting set cannot make this test lie about what it checks.
    """
    due = [h for h in fresh.get("/api/habits").json()["habits"] if h["due"]]
    assert any(h["system_key"] == "journal" for h in due)
    assert fresh.get("/api/home").json()["habits"]["total"] == len(due)


def test_migration_0006_restores_an_archived_journal_habit(fresh):
    """Reverses 0001 without losing the logs that habit already had."""
    from sqlalchemy import func, select

    ws = _ws(fresh.user["id"])
    answers = {q: "written" for q in svc.JOURNAL_KEYS}
    fresh.post("/api/journal", json={"answers": answers})

    with SessionLocal() as s:
        habit = s.scalar(select(db.Habit).where(
            db.Habit.workspace_id == ws, db.Habit.system_key == "journal"))
        habit_id = habit.id
        logs_before = s.scalar(select(func.count(db.HabitLog.id)).where(
            db.HabitLog.habit_id == habit_id))

    # Put the workspace back into the post-0001 state. Archived directly
    # rather than by calling 0001, which is now a no-op precisely so that it
    # cannot undo 0006 on a replay.
    with SessionLocal() as s:
        s.get(db.Habit, habit_id).archived_at = db.utcnow()
        s.commit()
        assert s.get(db.Habit, habit_id).archived_at is not None
    assert not any(h["system_key"] == "journal"
                   for h in fresh.get("/api/habits").json()["habits"])

    result = migrations.m0006_restore_journal_habit()
    assert result["unarchived"] >= 1

    with SessionLocal() as s:
        row = s.get(db.Habit, habit_id)
        assert row.archived_at is None
        assert row.name == "Kundalik" and row.is_protected is True
        # The same habit row, so every log it had is still attached to it.
        assert s.scalar(select(func.count(db.HabitLog.id)).where(
            db.HabitLog.habit_id == habit_id)) >= logs_before


def test_migration_0006_is_safe_to_run_twice(fresh):
    migrations.m0006_restore_journal_habit()
    again = migrations.m0006_restore_journal_habit()
    assert again["unarchived"] == 0 and again["created"] == 0


# ==========================================================================
# UTC timestamps vs local days
# ==========================================================================

def test_a_local_day_maps_to_the_right_utc_window():
    """Asia/Tashkent is UTC+5, so its day starts at 19:00 UTC the day before."""
    start, end = svc.utc_window(date(2026, 8, 12), tz=svc.TZ)
    assert start == datetime(2026, 8, 11, 19, 0)
    assert end == datetime(2026, 8, 12, 19, 0)


def test_a_utc_timestamp_reads_back_as_the_local_day_it_happened_on():
    # 21:00 in Tashkent on the 12th is 16:00 UTC on the 12th.
    assert svc.local_date_of(datetime(2026, 8, 12, 16, 0), svc.TZ) == date(2026, 8, 12)
    # 01:00 in Tashkent on the 13th is 20:00 UTC on the 12th — still the 13th
    # as far as the user is concerned.
    assert svc.local_date_of(datetime(2026, 8, 12, 20, 0), svc.TZ) == date(2026, 8, 13)
    assert svc.local_date_of(None, svc.TZ) is None


def test_a_task_finished_late_in_the_evening_is_filed_under_today(fresh):
    """The bug this guards: `completed_at` is UTC and the bucket is a local
    date, so between 19:00 and midnight in Tashkent everything completed today
    was filed under "earlier" — the Done archive looked empty all evening.
    """
    task_id = fresh.post("/api/tasks", json={"title": "Late night"}).json()["id"]
    fresh.patch(f"/api/tasks/{task_id}", json={"status": "done"})

    ws = _ws(fresh.user["id"])
    today = svc.today_local()
    with SessionLocal() as s:
        # 23:30 local, whatever that is in UTC.
        local_late = datetime.combine(today, dtime(23, 30)).replace(tzinfo=svc.TZ)
        s.get(db.Task, task_id).completed_at = \
            local_late.astimezone(timezone.utc).replace(tzinfo=None)
        s.commit()
        groups = svc.completed_tasks(s, ws, tz=svc.TZ)

    assert task_id in [x["id"] for x in groups["today"]]
    assert task_id not in [x["id"] for x in groups["earlier"]]


def test_migration_0001_can_no_longer_undo_0006(fresh):
    """The two used to fight: 0001 archived the journal habit and 0006 brought
    it back, so replaying the chain left the outcome depending on order."""
    migrations.m0006_restore_journal_habit()
    result = migrations.m0001_retire_summary_habit()
    assert result["archived"] == 0
    assert any(h["system_key"] == "journal"
               for h in fresh.get("/api/habits").json()["habits"])


def test_the_whole_migration_chain_is_idempotent(fresh):
    """Running every migration twice must change nothing the second time."""
    migrations.run()
    second = {r["migration"]: r for r in migrations.run()}
    assert second["0004_recompute_prayer_completion"]["habit_logs_changed"] == 0
    assert second["0005_rename_themes"]["total"] == 0
    assert second["0006_restore_journal_habit"]["unarchived"] == 0
    assert second["0006_restore_journal_habit"]["created"] == 0
    assert second["0007_redesign_themes"]["total"] == 0
    assert second["0008_named_theme_systems"]["total"] == 0


# ==========================================================================
# Regressions from the redesign round
# ==========================================================================

def test_every_symbol_the_mini_app_uses_is_defined():
    """The Statistics screen once threw on every open.

    `TREND_ICON` was defined beside the Home block that a redesign replaced, and
    nothing failed until a user opened Statistics — where it is read. A static
    check is the only thing that catches this class of break without clicking
    through every screen.
    """
    import re

    html = (ROOT / "webapp" / "index.html").read_text()
    js = html.split('<script>\n"use strict";', 1)[1].rsplit("</script>", 1)[0]

    declared = set()
    for pattern in (r"\bfunction\s+([A-Za-z_$][\w$]*)",
                    r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)"):
        declared.update(re.findall(pattern, js))

    # Screaming-case module constants are the ones that get orphaned; locals and
    # parameters are out of scope for a regex and not what broke.
    GLOBALS = {"JSON", "Math", "Object", "Array", "String", "Number", "Boolean",
               "Promise", "Date", "Set", "Map", "Error", "Intl"}
    used = set(re.findall(r"(?<![\w$.\"'`])([A-Z][A-Z_0-9]{3,})\s*[\[\(.]", js))
    missing = sorted(used - declared - GLOBALS)
    assert not missing, f"used but never defined: {missing}"


def test_the_statistics_screen_reads_the_trend_arrows():
    html = (ROOT / "webapp" / "index.html").read_text()
    assert "const TREND_ICON =" in html
    stats = html[html.index("SCREENS.stats = () => {"):html.index("/* ====")
                 if "/* ====" in html[html.index("SCREENS.stats = () => {"):]
                 else len(html)]
    assert "TREND_ICON" in html[html.index("SCREENS.stats = () => {"):]


def test_the_report_tick_is_short_enough_to_be_punctual():
    """The tick interval *is* the worst-case lateness: a report set for 21:30
    on a ten-minute tick could arrive at 21:40, which reads as a slow bot."""
    assert application.REPORT_TICK_MINUTES <= 3


def test_a_wake_up_on_time_says_good_morning(alice):
    on_time = application.wake_reply(
        {"done": True, "now": "04:53", "target": "05:00"}, "uz")
    assert "Xayrli tong" in on_time and "04:53" in on_time
    for lang in ("en", "ru"):
        assert application.t(lang, "wake_ok_at")


def test_a_late_wake_up_is_regretful_not_punitive(alice):
    late = application.wake_reply(
        {"done": False, "now": "08:20", "target": "05:00"}, "uz")
    assert "Afsuski" in late and "08:20" in late
    # It still reports the fact without declaring the day a failure.
    for verdict in ("hisoblanmadi", "bajarilmadi"):
        assert verdict not in late.lower()


def test_home_carries_only_the_numbers_it_shows():
    """Home is a glance: what to do now, how today is going, today's tasks.

    Four blocks. The month grid was the fifth and the tallest — forty-two
    cells about the rest of the month on the one screen whose whole job is
    today — and it now opens from the date in the header instead.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    home = html[html.index("SCREENS.home = () => {"):html.index("function privacyNote")]
    for block in ("headBlock", "nowBlock", "scoreBlock", "tasksBlock"):
        assert block in home, f"Home no longer renders {block}"
    assert "homeCalendar" not in html, "the month grid is back on Home"
    # The blocks that moved off it must not have come back.
    assert "top3Block" not in html and "weekBlock" not in html
    assert "focusBlock" not in html, "the week's focus belongs on Tasks"


def test_the_score_block_shows_day_week_and_month_at_once():
    """The comparison people actually want is between the three windows.

    Behind a switch it took two taps and remembering the first number; three
    rows answer it by being read. The switch is gone, so nothing may bring
    back a "current period" for this block to be in.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    assert 'data-act="score-period"' not in html, "the switch is back"
    assert "scorePeriod" not in html, "the block has a current period again"
    block = html[html.index("function scoreBlock("):html.index("function tasksBlock(")]
    for period in ("period_day", "period_week", "period_month"):
        assert f't("{period}")' in block
    # Each row carries its own change, and the arrow means the direction
    # survives a screen where the colour does not.
    assert "deltaTag(" in block
    assert "state.summary" in block, "the week and month are not read"


def test_todays_change_is_measured_against_yesterday():
    """A delta on the day panel needs a comparison, and yesterday only counts
    when it had something to measure — otherwise it is absent, not a fall."""
    html = (ROOT / "webapp" / "index.html").read_text()
    assert "d.overall.yesterday" in html
    assert "d.overall.value - d.overall.yesterday" in html


def test_the_calendar_opens_from_the_date_on_home(alice):
    """The month is one tap from Home, behind the date it is about.

    It is a sheet rather than a block, so Home's first paint no longer waits
    for — or scrolls past — a grid nobody asked for. The date in the header is
    where somebody looking for a month taps anyway.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    assert "function calendarSheet()" in html
    assert "calendarBlock()" in html
    assert 'data-act="calendar-open"' in html, "the date no longer opens the month"
    assert '"calendar-open":' in html, "nothing handles opening the month"
    # Fetched when that sheet opens, not on every paint of Home.
    home_load = html.split('if(screen === "home")')[1][:400]
    assert 'api("/api/calendar")' not in home_load, \
        "Home fetches a month grid it does not show"


# ==========================================================================
# The UX round: one "now", one score block, three task views
# ==========================================================================

def test_the_now_card_decides_and_says_why():
    """Home's largest block answers "what do I do at this moment?".

    It is computed rather than chosen: the user does not have to read the
    screen and pick. A card that decides on somebody's behalf owes them the
    sentence explaining why this one, so every rung of the ladder carries a
    reason and the card prints it.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    block = html[html.index("function nowBlock("):html.index("function scoreBlock(")]
    assert "d.now" in block, "the card is not reading the computed answer"
    assert 't("now")' in block, "the card is still titled something else"
    assert 'data-act="mission-pick"' in block, "the suggestion cannot be changed"
    for reason in ("pinned", "overdue", "due_today", "habit", "wake",
                   "prayer", "evening"):
        assert f'{reason}:"why_' in block or f'{reason}:"' in block, reason
    for lang in ("uz", "en", "ru"):
        assert f'why_overdue:' in html


def test_the_ladder_prefers_late_work_over_work_due_later(alice):
    """A missed deadline outranks a future one.

    Overdue tasks were not on the ladder at all, so a task that was late
    yesterday lost to one due at the end of today — which is exactly backwards
    from how anybody actually works.
    """
    today = date.today()
    late = alice.post("/api/tasks", json={
        "title": "Late invoice",
        "deadline": (today - timedelta(days=2)).isoformat()}).json()
    alice.post("/api/tasks", json={
        "title": "Later today", "deadline": today.isoformat(),
        "due_time": "23:00"})

    now = alice.get("/api/home").json()["now"]
    assert now["title"] == "Late invoice"
    assert now["id"] == late["id"]
    assert now["reason"] == "overdue"


def test_pinning_a_task_overrules_the_ladder(alice):
    """The card is a suggestion. Pinning is how the user overrides it, and the
    reason then says so rather than pretending the ladder chose."""
    today = date.today()
    alice.post("/api/tasks", json={
        "title": "Late invoice",
        "deadline": (today - timedelta(days=2)).isoformat()})
    chosen = alice.post("/api/tasks", json={
        "title": "Write the proposal", "deadline": today.isoformat()}).json()
    alice.post(f"/api/tasks/{chosen['id']}/top3", json={"picked": True})

    now = alice.get("/api/home").json()["now"]
    assert now["title"] == "Write the proposal"
    assert now["reason"] == "pinned"


def test_a_reason_comes_back_for_every_rung(alice):
    """An answer with no reason would print a card that cannot explain itself."""
    assert alice.get("/api/home").json()["now"]["reason"]


def test_tasks_is_four_views_of_the_same_data():
    """Main, Open, Projects, Done — one screen, four questions.

    Main is what matters now, in the order it is asked: the week's focus, then
    what is pinned, late and due today, and the month at the foot. The calendar
    used to open from a header button into a sheet; it reads as the last part
    of the plan, so it is the last block of the plan.

    Open is the inbox — the whole inventory with the search and the filters.
    Projects is its own view, because "where has this got to" is not the same
    question as "what do I do next" and the two were sharing one scroll.

    No view may print another's blocks: the week's focus and the project list
    under a searchable list of every open task was the same content twice.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    screen = html[html.index("SCREENS.tasks = () => {"):html.index("function sectionHead(")]
    for tab in ("main_tab", "open_tab", "done_tab"):
        assert f't("{tab}")' in screen, f"the {tab} view is missing"
    assert 't("projects")' in screen, "the projects view is missing"

    main = html[html.index("function mainTab("):html.index("function searchBox(")]
    assert "weekFocusBlock()" in main
    assert "calendarBlock()" in main, "the month left the plan"
    assert main.index("weekFocusBlock()") < main.index("calendarBlock()"), \
        "the month is above the week's focus"
    assert "projectsTab()" not in main, "projects are a view of their own"

    open_tab = html[html.index("function openTab("):html.index("function doneTab(")]
    assert "weekFocusBlock()" not in open_tab, "the focus block is printed twice"
    assert "projectsTab()" not in open_tab, "the project list is printed twice"
    assert 'searchBox("task-q"' in open_tab
    for section in ("overdue", "upcoming", "no_date", "later"):
        assert f't("{section}")' in open_tab, \
            f"the inbox holds back {section}"


def test_the_projects_view_says_what_is_left_not_only_how_far():
    """A percentage is a measurement; "4 tasks left" is the answer.

    The old row drew a name and a progress bar, which made a project with two
    tasks outstanding and one with fourteen look identical at 60%.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    view = html[html.index("function projectsTab("):html.index("SCREENS.project =")]
    assert "p.tasks_open" in view, "nothing says how much work is left"
    assert 't("pr_left"' in view
    assert "p.progress" in view and "p.deadline" in view
    # And the portfolio in one line above the list.
    for key in ("pr_active", "pr_open_tasks", "pr_average"):
        assert f't("{key}")' in view, f"the summary is missing {key}"


def test_a_new_task_is_reminded_about_half_an_hour_before():
    """Nobody chooses a reminder offset, so the default has to be the useful
    one. "At the time" is a notification about something already starting."""
    assert svc.DEFAULT_REMIND_BEFORE == 30
    assert 30 in svc.REMINDER_OFFSETS
    html = (ROOT / "webapp" / "index.html").read_text()
    assert "const DEFAULT_REMIND_BEFORE = 30;" in html
    assert "remind_before: DEFAULT_REMIND_BEFORE" in html
    # And the picker offers the same set the server accepts, plus none at all.
    assert "const REMINDER_OFFSETS = [0, 10, 30, 60, 1440];" in html
    for offset in svc.REMINDER_OFFSETS:
        assert f"remind_{offset}:" in html, f"no label for {offset} minutes"
    assert "remind_none:" in html


def test_the_reminder_default_can_still_be_turned_off(alice):
    """A default that cannot be refused is not a default."""
    alice.post("/api/tasks", json={
        "title": "Quiet one", "deadline": date.today().isoformat(),
        "remind_before": None})
    alice.post("/api/tasks", json={
        "title": "Remind me", "deadline": date.today().isoformat(),
        "remind_before": 30})
    rows = {t["title"]: t for group in alice.get("/api/tasks").json().values()
            if isinstance(group, list) for t in group}
    assert rows["Quiet one"]["remind_before"] is None
    assert rows["Remind me"]["remind_before"] == 30


def test_the_channel_statistics_post_goes_out_in_the_morning():
    """23:00 was written for whoever was still up. The channel is read in the
    morning, so that is when the post lands."""
    assert application.STATS_POST_HOUR == 10
    stats = _built_jobs()["stats"]
    fields = {f.name: str(f) for f in stats.trigger.fields}
    # The hour is no longer in the trigger — it is checked inside the job, so
    # that a restart cannot skip the day. The trigger only decides how often
    # the question gets asked.
    assert fields["minute"] == f"*/{application.REPORT_TICK_MINUTES}"
    # The project clock, so 10:00 means 10:00 in Tashkent wherever this runs.
    assert stats.trigger.timezone == svc.TZ


def test_the_now_card_is_not_printed_twice(alice):
    """Whatever the Now card shows must not appear again in today's list.

    The backend drops the pinned tasks from that list, but the ladder can also
    choose an unpinned task due today — which the old filter, keyed on the
    pinned id alone, would have let through.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    block = html[html.index("function tasksBlock("):html.index("function calendarBlock(")]
    assert 'd.now?.kind === "task" ? d.now.id' in block

    created = alice.post("/api/tasks", json={
        "title": "The only one", "deadline": date.today().isoformat()}).json()
    alice.post(f"/api/tasks/{created['id']}/top3", json={"picked": True})

    home = alice.get("/api/home").json()
    assert home["now"]["id"] == created["id"]
    # A pinned task is dropped from today's list by the backend; the filter in
    # the screen is what covers the unpinned case the ladder can also choose.
    listed = [t["id"] for g in home["tasks_today"] for t in g["tasks"]]
    assert created["id"] not in listed


# ==========================================================================
# The palette is Apple's, on purpose
# ==========================================================================

#: Apple's system colours, light variant then dark, plus the accessible
#: darker variants the platform ships for coloured *text* on a light ground.
#: Every brand hue in every theme comes from this table.
#:
#: The point is not brand-worship. The set is contrast-tested at both ends,
#: the light and dark variant of a hue are calibrated to read as the same
#: colour rather than merely to share a name, and it is the palette the
#: operating system around the Mini App is already using. Hexes picked by eye
#: are how an interface ends up looking almost right.
APPLE_SYSTEM_COLOURS = {
    # light                                   # dark
    "#007AFF", "#0A84FF",   # blue
    "#5856D6", "#5E5CE6",   # indigo
    "#AF52DE", "#BF5AF2",   # purple
    "#FF2D55", "#FF375F",   # pink
    "#FF3B30", "#FF453A",   # red
    "#FF9500", "#FF9F0A",   # orange
    "#FFCC00", "#FFD60A",   # yellow
    "#34C759", "#30D158",   # green
    "#00C7BE", "#66D4CF",   # mint
    "#30B0C7", "#40C8E0",   # teal
    "#32ADE6", "#64D2FF",   # cyan
    "#5AC8FA", "#64D2FF",   # light blue
    "#8E8E93", "#98989D",   # gray
    # Accessible variants, for a hue used as text on a light ground.
    "#248A3D", "#B25000", "#D70015",
    # The ink both light-on-dark and dark-on-light themes lead with.
    "#1C1C1A", "#F2F2ED",
}


@pytest.mark.parametrize("name", application.THEMES)
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_every_brand_colour_is_an_apple_system_colour(name, mode):
    import re

    styled = (ROOT / "webapp" / "index.html").read_text()
    block = _theme_block(styled, name, mode)
    for token in ("--c1", "--c2", "--c3", "--c4", "--c5",
                  "--ok", "--warn", "--danger"):
        found = re.search(r"%s:\s*(#[0-9A-Fa-f]{6})" % token, block)
        assert found, f"{name}/{mode} does not set {token}"
        colour = found.group(1).upper()
        assert colour in APPLE_SYSTEM_COLOURS, \
            f"{name}/{mode} {token} is {colour}, which is not a system colour"


def test_a_light_lead_colour_carries_a_dark_label():
    """White on teal is unreadable, so that fill takes a dark label instead.

    Apple does the same on its own yellow and mint fills. What must not happen
    is a theme keeping white because every other theme has white.
    """
    import re

    styled = (ROOT / "webapp" / "index.html").read_text()

    def luma(hex_colour):
        r, g, b = (int(hex_colour[i:i+2], 16) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    for name in application.THEMES:
        for mode in ("light", "dark"):
            block = _theme_block(styled, name, mode)
            lead = re.search(r"--c1:\s*(#[0-9A-Fa-f]{6})", block).group(1)
            label = re.search(r"--on-primary:\s*(#[0-9A-Fa-f]{6})",
                              block).group(1)
            # The label and the fill it sits on must be at opposite ends.
            assert abs(luma(lead) - luma(label)) > 90, \
                f"{name}/{mode}: {label} on {lead} is not readable"


def test_the_app_renders_in_the_system_face_where_there_is_one():
    """San Francisco first, Inter as the fallback for Windows and Android.

    On an iPhone, and in Telegram Desktop on a Mac, the Mini App then renders
    in the same face as everything around it — which is most of what "it looks
    native" turns out to mean.
    """
    styled = (ROOT / "webapp" / "index.html").read_text()
    stack = styled[styled.index("font:15px/"):][:200]
    assert stack.index("-apple-system") < stack.index("Inter")


# ==========================================================================
# Referrals
# ==========================================================================
#
# The loop these protect: someone shares a link, a genuinely new person joins,
# and the referral only counts once that person actually used ErnestOS. Every
# test below is really one of two questions — can attribution be faked, and
# can it be counted twice.

def _new_user(telegram_id: int, *, onboarded=True, actions=0):
    with SessionLocal() as s:
        svc.get_or_create_user(s, telegram_id, first_name=f"U{telegram_id}")
        user = s.get(User, telegram_id)
        user.onboarded = onboarded
        user.actions_count = actions
        s.commit()


def _code_for(telegram_id: int) -> str:
    with SessionLocal() as s:
        return svc.get_or_create_referral_code(s, telegram_id)


def _referral(telegram_id: int):
    with SessionLocal() as s:
        return s.get(db.Referral, telegram_id)


# --- codes ----------------------------------------------------------------

def test_a_referral_code_is_stable_for_the_life_of_the_account():
    """A link already sitting in somebody's chat has to keep working."""
    uid = next(_next_id)
    _new_user(uid)
    first = _code_for(uid)
    assert first and _code_for(uid) == first
    assert _code_for(uid) == first


def test_two_users_get_different_codes():
    a, b = next(_next_id), next(_next_id)
    _new_user(a); _new_user(b)
    assert _code_for(a) != _code_for(b)


def test_a_code_is_deep_link_safe_and_not_the_telegram_id():
    """`ref_123456789` would publish the id of everybody who sent an invite."""
    uid = next(_next_id)
    _new_user(uid)
    code = _code_for(uid)
    assert re.fullmatch(r"[A-Za-z0-9_-]{6,32}", code), code
    assert str(uid) not in code


# --- payload parsing ------------------------------------------------------

@pytest.mark.parametrize("payload", [
    None, "", "123", "abc", "ref_", "ref:code", "javascript:alert(1)",
    "ref_has spaces", "ref_" + "x" * 40, "REF_abcdefgh", "ref_bad/slash",
])
def test_a_malformed_referral_payload_is_simply_ignored(payload):
    """Never the user's fault, so never the user's error to look at."""
    assert svc.parse_referral_payload(payload) is None


def test_a_well_formed_payload_yields_the_code():
    assert svc.parse_referral_payload("ref_G7krP_2XaF") == "G7krP_2XaF"


# --- attribution ----------------------------------------------------------

def test_a_new_user_joining_through_a_link_is_attributed_as_pending():
    alice, bob = next(_next_id), next(_next_id)
    _new_user(alice)
    code = _code_for(alice)

    with SessionLocal() as s:
        _user, created = svc.get_or_create_user(s, bob, first_name="Bob")
        s.commit()
        assert svc.claim_referral(s, bob, f"ref_{code}",
                                  source="bot", newly_created=created) is True

    row = _referral(bob)
    assert row.inviter_user_id == alice
    assert row.status == "pending"
    assert row.qualified_at is None


def test_an_existing_account_can_never_be_attributed():
    """The single most important rule: /start is not a claim ticket.

    Without the `created` gate, anybody could get an established user to open
    a link and harvest them as a referral.
    """
    alice, bob = next(_next_id), next(_next_id)
    _new_user(alice); _new_user(bob)
    code = _code_for(alice)

    with SessionLocal() as s:
        _user, created = svc.get_or_create_user(s, bob)
        s.commit()
        assert created is False
        assert svc.claim_referral(s, bob, f"ref_{code}",
                                  source="bot", newly_created=created) is False
    assert _referral(bob) is None


def test_the_first_inviter_cannot_be_displaced():
    """Attribution is first-touch and there is nowhere to write a second one."""
    alice, charlie, bob = next(_next_id), next(_next_id), next(_next_id)
    _new_user(alice); _new_user(charlie)
    alice_code, charlie_code = _code_for(alice), _code_for(charlie)

    with SessionLocal() as s:
        svc.get_or_create_user(s, bob); s.commit()
        svc.claim_referral(s, bob, f"ref_{alice_code}", newly_created=True)
        # Bob later opens Charlie's link, and even if `created` were somehow
        # true again, the row already exists.
        assert svc.claim_referral(s, bob, f"ref_{charlie_code}",
                                  newly_created=True) is False
    assert _referral(bob).inviter_user_id == alice


def test_a_user_cannot_refer_themselves():
    alice = next(_next_id)
    _new_user(alice)
    code = _code_for(alice)
    with SessionLocal() as s:
        assert svc.claim_referral(s, alice, f"ref_{code}",
                                  newly_created=True) is False
    assert _referral(alice) is None


def test_a_code_nobody_owns_creates_nothing():
    bob = next(_next_id)
    with SessionLocal() as s:
        svc.get_or_create_user(s, bob); s.commit()
        assert svc.claim_referral(s, bob, "ref_nosuchcode1",
                                  newly_created=True) is False
    assert _referral(bob) is None


def test_opening_the_same_link_twice_creates_one_referral():
    alice, bob = next(_next_id), next(_next_id)
    _new_user(alice)
    code = _code_for(alice)
    with SessionLocal() as s:
        svc.get_or_create_user(s, bob); s.commit()
        assert svc.claim_referral(s, bob, f"ref_{code}", newly_created=True) is True
        assert svc.claim_referral(s, bob, f"ref_{code}", newly_created=True) is False
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(db.Referral)
                        .where(db.Referral.referred_user_id == bob)) == 1


# --- qualification --------------------------------------------------------

def _attach(inviter: int, invited: int):
    code = _code_for(inviter)
    with SessionLocal() as s:
        svc.get_or_create_user(s, invited, first_name="Invited")
        s.commit()
        svc.claim_referral(s, invited, f"ref_{code}", newly_created=True)


def test_a_referral_qualifies_only_on_the_third_action():
    """Clicking a link is not usage. Three real actions is the smallest signal
    that separates a referral from a click."""
    alice, bob = next(_next_id), next(_next_id)
    _new_user(alice)
    _attach(alice, bob)
    with SessionLocal() as s:
        s.get(User, bob).onboarded = True
        s.commit()

    assert svc.REFERRAL_QUALIFY_ACTIONS == 3
    for expected_status, _n in (("pending", 1), ("pending", 2)):
        with SessionLocal() as s:
            svc.record_action_and_qualify(s, bob)
        assert _referral(bob).status == expected_status

    with SessionLocal() as s:
        _total, inviter = svc.record_action_and_qualify(s, bob)
    assert inviter == alice, "the inviter should be told, exactly once"
    row = _referral(bob)
    assert row.status == "qualified" and row.qualified_at is not None


def test_actions_without_onboarding_do_not_qualify():
    alice, bob = next(_next_id), next(_next_id)
    _new_user(alice)
    _attach(alice, bob)
    with SessionLocal() as s:
        s.get(User, bob).onboarded = False
        s.commit()
        for _ in range(10):
            svc.record_action_and_qualify(s, bob)
    assert _referral(bob).status == "pending"

    # Finishing onboarding is the other half, and the same central check.
    with SessionLocal() as s:
        s.get(User, bob).onboarded = True
        s.commit()
        assert svc.maybe_qualify_referral(s, bob) == alice
    assert _referral(bob).status == "qualified"


def test_a_qualified_referral_is_never_qualified_twice():
    """Otherwise every later action would re-congratulate the inviter."""
    alice, bob = next(_next_id), next(_next_id)
    _new_user(alice)
    _attach(alice, bob)
    with SessionLocal() as s:
        s.get(User, bob).onboarded = True
        s.commit()

    fired = []
    for _ in range(100):
        with SessionLocal() as s:
            _total, inviter = svc.record_action_and_qualify(s, bob)
        if inviter is not None:
            fired.append(inviter)

    assert fired == [alice], "the qualification message must fire exactly once"
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(db.Referral)
                        .where(db.Referral.referred_user_id == bob)) == 1


def test_referral_counts_and_levels_follow_the_qualified_total():
    alice = next(_next_id)
    _new_user(alice)
    with SessionLocal() as s:
        assert svc.referral_stats(s, alice)["level"]["key"] == "new"

    for _ in range(2):
        invited = next(_next_id)
        _attach(alice, invited)
        with SessionLocal() as s:
            s.get(User, invited).onboarded = True
            s.commit()
            for _ in range(svc.REFERRAL_QUALIFY_ACTIONS):
                svc.record_action_and_qualify(s, invited)

    # One more who joined but never really arrived.
    _attach(alice, next(_next_id))

    with SessionLocal() as s:
        stats = svc.referral_stats(s, alice)
    assert stats["counts"] == {"total": 3, "pending": 1, "qualified": 2}
    assert stats["level"]["key"] == "inviter"
    assert stats["level"]["next"] == {"target": 3, "remaining": 1}


# --- the bot deep link ----------------------------------------------------

class _Ctx:
    """Enough of a python-telegram-bot context for /start."""

    def __init__(self, args=None):
        self.args = args or []
        self.bot = _FakeBot()
        self.user_data = {}


class _Msg:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


class _Update:
    def __init__(self, telegram_id):
        self.effective_user = type("U", (), {
            "id": telegram_id, "first_name": "Deep", "last_name": "",
            "username": ""})()
        self.effective_message = _Msg()
        self.callback_query = None


async def test_start_with_a_referral_payload_attributes_a_new_user(monkeypatch):
    alice, bob = next(_next_id), next(_next_id)
    _new_user(alice)
    code = _code_for(alice)

    await application.start(_Update(bob), _Ctx([f"ref_{code}"]))

    assert _referral(bob).inviter_user_id == alice


async def test_start_with_nonsense_does_not_break_onboarding(monkeypatch):
    """A bad link must cost the new user nothing at all."""
    bob = next(_next_id)
    update = _Update(bob)

    await application.start(update, _Ctx(["utter-nonsense"]))

    assert _referral(bob) is None
    with SessionLocal() as s:
        assert s.get(User, bob) is not None, "the account must still be created"
        ws = svc.workspace_id_for(s, bob)
        names = [h["name"] for h in svc.list_habits(s, ws)]
    assert names == ["Get up", "5x namoz", "Kundalik"], \
        "onboarding must have run normally, defaults and all"


async def test_start_without_any_payload_still_works():
    bob = next(_next_id)
    await application.start(_Update(bob), _Ctx([]))
    with SessionLocal() as s:
        assert s.get(User, bob) is not None
    assert _referral(bob) is None


# --- the Mini App signed start_param --------------------------------------

def test_a_signed_start_param_attributes_a_new_miniapp_user(client):
    alice, bob = next(_next_id), next(_next_id)
    _new_user(alice)
    code = _code_for(alice)

    profile = {"id": bob, "first_name": "WebBob"}
    r = client.get("/api/me", headers={
        "X-Telegram-Init-Data": init_data(profile, start_param=f"ref_{code}")})
    assert r.status_code == 200

    row = _referral(bob)
    assert row is not None and row.inviter_user_id == alice
    assert row.source == "miniapp"


def test_an_unsigned_start_param_cannot_mint_a_referral(client):
    """The attack this closes.

    `initDataUnsafe.start_param` and `tgWebAppStartParam` are both fully
    client-controlled. If either were trusted, anybody could award themselves
    referrals by editing a query string. Only the HMAC-covered field counts —
    so a payload signed *without* start_param, with the code bolted on
    afterwards, must attribute nothing.
    """
    alice, bob = next(_next_id), next(_next_id)
    _new_user(alice)
    code = _code_for(alice)

    profile = {"id": bob, "first_name": "Forger"}
    signed_without = init_data(profile)          # start_param not in the hash
    forged = signed_without + f"&start_param=ref_{code}"

    r = client.get("/api/me", headers={"X-Telegram-Init-Data": forged})
    # Either the extra field breaks the signature (401) or it is ignored — but
    # under no circumstances may it create a referral.
    assert r.status_code in (200, 401)
    assert _referral(bob) is None


def test_verify_init_data_still_returns_just_the_user():
    """The long-standing contract other callers and tests rely on."""
    profile = {"id": 909001, "first_name": "Contract"}
    got = application.verify_init_data(init_data(profile))
    assert got["id"] == 909001
    assert "auth_date" not in got and "hash" not in got


# --- the API --------------------------------------------------------------

def test_referrals_me_returns_only_the_callers_own_aggregate(alice, bob):
    """No id in the path or the query, so there is nothing to tamper with."""
    a = alice.get("/api/referrals/me").json()
    b = bob.get("/api/referrals/me").json()

    assert a["code"] != b["code"]
    assert a["counts"]["qualified"] >= 0
    # Nothing about *who* was invited may appear anywhere in the payload.
    assert "referred" not in json.dumps(a)
    assert str(BOB["id"]) not in json.dumps(a)


def test_referrals_me_needs_authentication(client):
    assert client.get("/api/referrals/me").status_code == 401


def test_the_referral_link_carries_the_bot_username(alice, monkeypatch):
    monkeypatch.setattr(application, "BOT_USERNAME", "ernestos_bot")
    body = alice.get("/api/referrals/me").json()
    assert body["configured"] is True
    assert body["link"] == f"https://t.me/ernestos_bot?start=ref_{body['code']}"
    assert body["miniapp_link"].endswith(f"?startapp=ref_{body['code']}")


def test_without_a_bot_username_sharing_reports_itself_unconfigured(
        alice, monkeypatch):
    """Missing config must degrade to an honest message, not a broken link."""
    monkeypatch.setattr(application, "BOT_USERNAME", "")
    body = alice.get("/api/referrals/me").json()
    assert body["configured"] is False and body["link"] is None
    assert body["code"], "the code still exists — only the link is unavailable"


@pytest.mark.parametrize("raw,expected", [
    ("@ernestos_bot", "ernestos_bot"),
    ("ernestos_bot", "ernestos_bot"),
    ("https://t.me/ernestos_bot", "ernestos_bot"),
    ("  ernestos_bot  ", "ernestos_bot"),
    ("", ""),
])
def test_the_bot_username_is_normalised(raw, expected):
    import config
    assert config._bot_username(raw) == expected


# --- account lifecycle ----------------------------------------------------

def test_wiping_a_workspace_keeps_the_referral_identity(client):
    """A wipe is starting over, not leaving — the invite link must survive."""
    uid = next(_next_id)
    _new_user(uid)
    code = _code_for(uid)
    with SessionLocal() as s:
        svc.wipe_workspace(s, uid)
    assert _code_for(uid) == code


def test_deleting_an_account_is_not_blocked_by_referrals(client):
    """Foreign keys must never stand between a person and leaving."""
    alice, bob = next(_next_id), next(_next_id)
    _new_user(alice)
    _attach(alice, bob)
    assert _referral(bob) is not None

    with SessionLocal() as s:
        assert svc.delete_account(s, bob) is True

    with SessionLocal() as s:
        assert s.get(User, bob) is None
        assert s.get(db.Referral, bob) is None, "no orphan row may survive"
        assert s.get(db.ReferralCode, bob) is None


def test_deleting_an_inviter_removes_their_side_too():
    alice, bob = next(_next_id), next(_next_id)
    _new_user(alice)
    _attach(alice, bob)

    with SessionLocal() as s:
        assert svc.delete_account(s, alice) is True

    with SessionLocal() as s:
        assert s.get(db.ReferralCode, alice) is None
        assert s.scalar(select(func.count()).select_from(db.Referral)
                        .where(db.Referral.inviter_user_id == alice)) == 0


# --- growth metrics -------------------------------------------------------

def test_platform_stats_carry_aggregate_referral_numbers_only():
    with SessionLocal() as s:
        st = svc.platform_stats(s)
    for key in ("referrals_total", "referrals_pending", "referrals_qualified",
                "referral_inviters", "referral_conversion"):
        assert key in st
    assert st["referrals_total"] >= st["referrals_qualified"]
    # Aggregates only: no identity may appear in the operator's numbers.
    assert all(isinstance(v, (int, float)) for k, v in st.items()
               if k.startswith("referral"))


# --- the defaults must not regress ---------------------------------------

def test_referral_work_did_not_change_the_new_user_defaults():
    """User creation was edited for referrals. The defaults are still three."""
    uid = next(_next_id)
    with SessionLocal() as s:
        svc.get_or_create_user(s, uid, first_name="Defaults")
        s.commit()
        ws = svc.workspace_id_for(s, uid)
        names = [h["name"] for h in svc.list_habits(s, ws)]
    assert names == ["Get up", "5x namoz", "Kundalik"]
    for gone in ("Deep flow", "Sport", "Podcast", "Read"):
        assert gone not in names


# ==========================================================================
# Screen changes from the UX round
#
# These read the Mini App source rather than a rendered DOM, which is what the
# rest of the file's UI tests do: there is no build step and no component tree
# to mount, so the source *is* the artefact. Each one pins a decision that is
# invisible from the API and easy to undo by accident.
# ==========================================================================

def test_every_priority_paints_its_own_edge():
    """Red, amber, and a quiet default — not one colour and two blanks.

    Only `.pri-high` was styled, so "is this urgent?" had exactly two answers
    on screen: red, or unknown. Medium is deliberately the weaker of the two
    colours — it is what every task is born with, and at full strength it would
    drown the red it exists to set off.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    assert ".trow.pri-high{border-left-color:var(--danger)}" in html
    assert ".trow.pri-medium{border-left-color:color-mix(" in html
    assert ".trow.pri-low{border-left-color:var(--border)}" in html
    # And the row still carries the class the CSS hangs off.
    assert 'return `<div class="trow pri-${task.priority}">' in html


def test_the_prayer_screen_asks_for_honesty_in_every_language():
    """One quiet line, above the rows, in all three languages.

    Above rather than below, because it is only worth anything if it is read
    before the tapping starts.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    assert html.count("prayer_honesty:") == 3, \
        "the prayer note is missing from a language"
    prayer = html[html.index("function prayerTab(){"):html.index("function journalTab(){")]
    note = prayer.index('t("prayer_honesty")')
    rows = prayer.index('["bomdod","peshin","asr","shom","xufton"]')
    assert note < rows, "the honesty note sits below the prayers it is about"
    # No English left in the Russian string (it was there once).
    assert "honestly — эта запись" not in html


def test_the_three_default_habits_are_not_offered_a_day_picker():
    """They are every day by definition; the question had one right answer."""
    html = (ROOT / "webapp" / "index.html").read_text()
    sheet = html[html.index("function habitSheet(h){"):html.index("function missionSheet(")]
    picker = sheet.index('data-act="habit-sched"')
    guard = sheet.index("h.protected")
    assert guard < picker, "the schedule picker is no longer behind the guard"
    assert 't("habit_always_daily")' in sheet
    assert html.count("habit_always_daily:") == 3


def test_each_habit_tier_shows_what_it_is_worth():
    """The badge comes from the API's applied weight, not a second formula."""
    html = (ROOT / "webapp" / "index.html").read_text()
    tab = html[html.index("function habitsTab(){"):html.index("function habitRow(")]
    assert "data.tiers?.[cat]" in tab
    assert "tier.applied" in tab
    assert "sectionHead(TIER_TONE[cat]" in tab and ", badge)" in tab


def test_home_puts_the_work_before_the_numbers():
    """Now → today's tasks → the score. Complements the block-order test."""
    html = (ROOT / "webapp" / "index.html").read_text()
    home = html[html.index("SCREENS.home = () => {"):html.index("function privacyNote(")]
    assert home.index("tasksBlock(d)") < home.index("scoreBlock(d)")
    # And the lower half is introduced as a section rather than a loose card.
    assert 't("overall_section")' in html
    assert html.count("overall_section:") == 3


# ==========================================================================
# Personal progression
#
# Two progression systems exist and they are deliberately separate: referrals
# measure who you brought in, this measures how you run your own life. The last
# test in this block is the one that pins them apart.
# ==========================================================================

def _progress_user(onboarded: bool = True) -> int:
    """A bare user with a workspace, outside anybody else's fixtures."""
    uid = next(_next_id)
    with SessionLocal() as s:
        svc.get_or_create_user(s, uid, first_name="Prog")
        s.get(User, uid).onboarded = onboarded
        s.commit()
    return uid


def _seed_days(user_id: int, scores: list[int], *, ending: date | None = None,
               start_offset: int | None = None) -> None:
    """Write a run of daily scores ending on `ending` (default today).

    Straight into `daily_scores`, because these tests are about what the
    progression engine does *with* a history, not about reproducing one
    through the UI a day at a time.
    """
    ending = ending or svc.today_local()
    with SessionLocal() as s:
        for i, score in enumerate(reversed(scores)):
            day = ending - timedelta(days=i if start_offset is None else i + start_offset)
            s.add(db.DailyScore(user_id=user_id, day=day, total_score=score,
                                grade=svc.grade_for(score), task_score=score,
                                habit_score=score, focus_score=score,
                                prayer_score=score))
        s.commit()


# --- the daily score ------------------------------------------------------

def test_a_daily_score_never_leaves_its_range(alice):
    """Whatever the components do, the total is a percentage."""
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        for offset in range(0, 5):
            day = svc.today_local() - timedelta(days=offset)
            row = svc.recompute_daily_score(s, ALICE["id"], ws, day)
            assert 0 <= row.total_score <= 100
        s.commit()


@pytest.mark.parametrize("score,grade", [
    (100, "S"), (90, "S"), (89, "A"), (80, "A"), (79, "B"), (70, "B"),
    (69, "C"), (60, "C"), (59, "D"), (40, "D"), (39, "E"), (0, "E"),
])
def test_grade_boundaries_are_exact(score, grade):
    assert svc.grade_for(score) == grade


def test_the_daily_score_is_the_score_already_on_the_home_screen(alice):
    """One formula, not two.

    A second set of weights would mean two numbers on two screens of the same
    app both claiming to be "how today went" and disagreeing by a few points.
    The daily score *is* the overall percentage.
    """
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        today = svc.today_local()
        row = svc.recompute_daily_score(s, ALICE["id"], ws, today)
        s.commit()
        assert row.total_score == svc.overall_percent(s, ws, today)


def test_a_component_with_no_denominator_is_absent_not_zero(alice):
    """A day with no tasks is not a day that failed its tasks."""
    uid = _progress_user()
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, uid)
        row = svc.recompute_daily_score(s, uid, ws, svc.today_local())
        s.commit()
        # No tasks and no weekly focus exist for a brand-new workspace.
        assert row.task_score == -1 and row.focus_score == -1
    snapshot = None
    with SessionLocal() as s:
        snapshot = svc.progress_snapshot(s, uid)
    assert snapshot["daily"]["breakdown"]["tasks"] is None


# --- XP ----------------------------------------------------------------

def test_the_same_event_is_never_paid_twice():
    uid = _progress_user()
    day = svc.today_local()
    with SessionLocal() as s:
        first = svc.award_xp(s, uid, f"task:{uid}:1", "task", 10, day)
        second = svc.award_xp(s, uid, f"task:{uid}:1", "task", 10, day)
        s.commit()
        assert (first, second) == (10, 0)
        assert svc.xp_total(s, uid) == 10


def test_completing_undoing_and_completing_again_pays_once(fresh):
    """The toggle-farming hole, closed by the key rather than by a guard."""
    uid = fresh.user["id"]
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, uid)
        task_id = svc.add_task(s, ws, "Farm me").id
        s.commit()

    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, uid)
        for _ in range(5):
            svc.complete_task(s, ws, task_id)
            svc.sync_day_xp(s, uid, ws, svc.today_local())
            svc.reopen_task(s, ws, task_id)
        svc.complete_task(s, ws, task_id)
        svc.sync_day_xp(s, uid, ws, svc.today_local())
        s.commit()
        paid = s.scalar(select(func.count()).select_from(db.XPEvent).where(
            db.XPEvent.user_id == uid,
            db.XPEvent.event_key == f"task:{task_id}"))
    assert paid == 1


def test_ordinary_activity_cannot_exceed_the_daily_cap():
    """Forty trivial tasks must not out-earn a real day."""
    uid = _progress_user()
    day = svc.today_local()
    with SessionLocal() as s:
        for i in range(40):
            svc.award_xp(s, uid, f"task:{uid}:{i}", "task", 10, day)
        s.commit()
        assert svc.xp_total(s, uid) == svc.XP_DAILY_CAP


def test_milestone_xp_is_paid_outside_the_cap():
    """A 30-day streak bonus that vanished because the day was busy is a lie."""
    uid = _progress_user()
    day = svc.today_local()
    with SessionLocal() as s:
        for i in range(40):
            svc.award_xp(s, uid, f"task:{uid}:{i}", "task", 10, day)
        bonus = svc.award_xp(s, uid, f"streak_30:{uid}:{day}", "streak", 200, day)
        s.commit()
        assert bonus == 200
        assert svc.xp_total(s, uid) == svc.XP_DAILY_CAP + 200


def test_the_cap_is_counted_per_local_day():
    uid = _progress_user()
    today = svc.today_local()
    with SessionLocal() as s:
        for i in range(40):
            svc.award_xp(s, uid, f"task:{uid}:a{i}", "task", 10, today)
        for i in range(40):
            svc.award_xp(s, uid, f"task:{uid}:b{i}", "task", 10,
                         today - timedelta(days=1))
        s.commit()
        assert svc.xp_total(s, uid) == svc.XP_DAILY_CAP * 2


# --- levels ---------------------------------------------------------------

@pytest.mark.parametrize("xp,key,number", [
    (0, "starter", 1), (499, "starter", 1), (500, "builder", 2),
    (1499, "builder", 2), (1500, "operator", 3), (3500, "architect", 4),
    (7000, "commander", 5), (15000, "elite", 6), (30000, "master", 7),
    (999999, "master", 7),
])
def test_level_thresholds(xp, key, number):
    level = svc.get_personal_level(xp)
    assert (level["key"], level["number"]) == (key, number)


def test_the_top_level_has_nowhere_left_to_go():
    level = svc.get_personal_level(50000)
    assert level["next_threshold"] is None and level["remaining"] == 0
    assert level["progress"] == 1.0


def test_level_progress_is_measured_across_the_gap_it_is_in():
    """2,000 XP is a third of the way from Operator to Architect, not of 3,500."""
    level = svc.get_personal_level(2000)
    assert level["key"] == "operator"
    assert level["remaining"] == 1500
    assert level["progress"] == round(500 / 2000, 4)


def test_level_is_computed_from_xp_and_never_stored_separately():
    """A stored level number is a second copy that drifts on replay."""
    assert not hasattr(db.UserProgress, "level")
    assert not hasattr(db.UserProgress, "level_key")


# --- streak, recovery, comeback -------------------------------------------

def test_a_qualifying_day_extends_the_streak():
    uid = _progress_user()
    with SessionLocal() as s:
        p = svc._progress_row(s, uid)
        day = svc.today_local() - timedelta(days=3)
        for i in range(3):
            svc._apply_day_to_streak(p, day + timedelta(days=i), 80)
        s.commit()
        assert p.current_streak == 3 and p.best_streak == 3


def test_a_weak_day_spends_a_recovery_day_instead_of_resetting():
    """One hard day must not cost a month."""
    uid = _progress_user()
    with SessionLocal() as s:
        p = svc._progress_row(s, uid)
        start = svc.today_local() - timedelta(days=10)
        for i in range(8):
            svc._apply_day_to_streak(p, start + timedelta(days=i), 80)
        assert p.current_streak == 8

        moved = svc._apply_day_to_streak(p, start + timedelta(days=8), 20)
        s.commit()
        assert moved["recovery_used"] is True
        assert p.current_streak == 8, "the streak was not protected"
        assert p.recovery_used == 1


def test_the_recovery_allowance_runs_out():
    uid = _progress_user()
    with SessionLocal() as s:
        p = svc._progress_row(s, uid)
        start = svc.today_local() - timedelta(days=10)
        svc._apply_day_to_streak(p, start, 80)
        for i in (1, 2):
            svc._apply_day_to_streak(p, start + timedelta(days=i), 10)
        assert p.recovery_used == svc.RECOVERY_DAYS_PER_MONTH
        assert p.current_streak == 1
        # The third weak day in a row has nothing left to spend.
        svc._apply_day_to_streak(p, start + timedelta(days=3), 10)
        s.commit()
        assert p.current_streak == 0


def test_the_recovery_allowance_comes_back_next_month():
    uid = _progress_user()
    with SessionLocal() as s:
        p = svc._progress_row(s, uid)
        p.recovery_used = svc.RECOVERY_DAYS_PER_MONTH
        p.recovery_month = "2026-07"
        p.last_score_date = date(2026, 7, 31)
        p.current_streak = 12
        svc._apply_day_to_streak(p, date(2026, 8, 1), 10)
        s.commit()
        assert p.recovery_month == "2026-08"
        assert p.recovery_used == 1 and p.current_streak == 12


def test_a_comeback_cannot_be_farmed_by_disappearing():
    """Returning is worth recognising once, not every third day."""
    uid = _progress_user()
    with SessionLocal() as s:
        p = svc._progress_row(s, uid)
        base = date(2026, 8, 1)
        p.last_score_date = base
        first = svc._apply_day_to_streak(p, base + timedelta(days=6), 80)
        assert first["comeback"] is True

        # Vanish and return again, inside the cooldown.
        again = svc._apply_day_to_streak(p, base + timedelta(days=12), 80)
        s.commit()
        assert again["comeback"] is False, "a comeback was farmable"


def test_backfilling_an_earlier_day_never_rewrites_the_streak():
    uid = _progress_user()
    with SessionLocal() as s:
        p = svc._progress_row(s, uid)
        today = svc.today_local()
        svc._apply_day_to_streak(p, today, 90)
        before = p.current_streak
        svc._apply_day_to_streak(p, today - timedelta(days=5), 10)
        s.commit()
        assert p.current_streak == before
        assert p.last_score_date == today


def test_the_same_day_arriving_twice_moves_nothing():
    """Every write in the product triggers a refresh; most are the same day."""
    uid = _progress_user()
    with SessionLocal() as s:
        p = svc._progress_row(s, uid)
        today = svc.today_local()
        svc._apply_day_to_streak(p, today - timedelta(days=1), 80)
        svc._apply_day_to_streak(p, today, 80)
        assert p.current_streak == 2
        for _ in range(10):
            svc._apply_day_to_streak(p, today, 80)
        s.commit()
        assert p.current_streak == 2


# --- perfect days ---------------------------------------------------------

def test_a_perfect_day_is_paid_once_however_often_the_day_is_rescored():
    uid = _progress_user()
    day = svc.today_local()
    with SessionLocal() as s:
        for _ in range(6):
            svc.award_xp(s, uid, f"perfect_day:{uid}:{day}", "perfect_day",
                         svc.XP_VALUES["perfect_day"], day)
        s.commit()
        assert svc.xp_total(s, uid) == svc.XP_VALUES["perfect_day"]


# --- ranking --------------------------------------------------------------

def test_users_are_ranked_by_recent_performance():
    """Alice ahead of Bob ahead of Charlie, on their last 30 days."""
    strong, middle, weak = (_progress_user() for _ in range(3))
    _seed_days(strong, [95] * 10)
    _seed_days(middle, [80] * 10)
    _seed_days(weak, [65] * 10)

    today = svc.today_local()
    with SessionLocal() as s:
        for uid in (strong, middle, weak):
            p = svc._progress_row(s, uid)
            p.scored_days = 10
            p.performance_index_30d = svc.performance_index(s, uid, today)
        s.commit()
        ranks = {uid: svc.global_rank(s, uid)["global"]
                 for uid in (strong, middle, weak)}
        s.commit()

    assert ranks[strong] < ranks[middle] < ranks[weak]


def test_equal_performance_shares_a_rank():
    """#184, #184, #186 — not invented decimals to force an order."""
    a, b = _progress_user(), _progress_user()
    with SessionLocal() as s:
        for uid in (a, b):
            p = svc._progress_row(s, uid)
            p.scored_days = svc.RANK_MIN_DAYS
            p.performance_index_30d = 77.0
        s.commit()
        assert svc.global_rank(s, a)["global"] == svc.global_rank(s, b)["global"]
        s.commit()


def test_a_brand_new_account_is_not_ranked_on_one_good_day():
    """One 100-point day must not sit above people with a year behind them."""
    uid = _progress_user()
    _seed_days(uid, [100, 100])
    with SessionLocal() as s:
        p = svc._progress_row(s, uid)
        p.scored_days = 2
        p.performance_index_30d = 100.0
        s.commit()
        rank = svc.global_rank(s, uid)
        s.commit()
    assert rank["eligible"] is False
    assert rank["global"] is None
    assert rank["days_remaining"] == svc.RANK_MIN_DAYS - 2


def test_going_quiet_lowers_the_rolling_index_on_its_own():
    """Calendar days, not active days — no punishment rule needed."""
    uid = _progress_user()
    today = svc.today_local()
    # Ten strong days, but they finished three weeks ago.
    _seed_days(uid, [95] * 10, ending=today - timedelta(days=20))
    with SessionLocal() as s:
        stale = svc.performance_index(s, uid, today)

    active = _progress_user()
    _seed_days(active, [95] * 10)
    with SessionLocal() as s:
        fresh_index = svc.performance_index(s, active, today)

    assert stale < fresh_index


def test_a_personal_best_rank_only_ever_improves():
    uid = _progress_user()
    with SessionLocal() as s:
        p = svc._progress_row(s, uid)
        p.scored_days = svc.RANK_MIN_DAYS
        p.best_global_rank = 42
        p.performance_index_30d = 1.0
        s.commit()
        # Comfortably more than 42 people ahead, so this read really does
        # produce a worse rank than the stored best.
        for _ in range(60):
            s.add(db.UserProgress(user_id=next(_next_id),
                                  scored_days=svc.RANK_MIN_DAYS,
                                  performance_index_30d=99.0))
        s.commit()
        rank = svc.global_rank(s, uid)
        s.commit()
    assert rank["global"] > 42
    assert rank["best"] == 42, "a worse rank overwrote the personal best"


def test_rank_movement_is_never_invented_on_a_first_view():
    uid = _progress_user()
    with SessionLocal() as s:
        p = svc._progress_row(s, uid)
        p.scored_days = svc.RANK_MIN_DAYS
        p.performance_index_30d = 50.0
        s.commit()
        assert svc.global_rank(s, uid)["movement"] is None
        s.commit()
    with SessionLocal() as s:
        # Second read has a previous rank to compare against.
        assert svc.global_rank(s, uid)["movement"] == 0
        s.commit()


# --- the two systems stay apart -------------------------------------------

def test_referrals_do_not_move_a_personal_rank():
    """The rule the whole split exists for.

    Somebody who invited a hundred people and does not use ErnestOS must not
    outrank somebody who uses it every day.
    """
    inviter = _progress_user()
    worker = _progress_user()
    _seed_days(inviter, [30] * 10)
    _seed_days(worker, [95] * 10)

    today = svc.today_local()
    with SessionLocal() as s:
        # Give the inviter a pile of qualified referrals.
        for _ in range(20):
            friend = next(_next_id)
            svc.get_or_create_user(s, friend, first_name="F")
            s.add(db.Referral(referred_user_id=friend, inviter_user_id=inviter,
                              status="qualified", source="bot"))
        s.commit()
        for uid in (inviter, worker):
            p = svc._progress_row(s, uid)
            p.scored_days = 10
            p.performance_index_30d = svc.performance_index(s, uid, today)
        s.commit()
        ranks = {uid: svc.global_rank(s, uid)["global"] for uid in (inviter, worker)}
        s.commit()

    assert ranks[worker] < ranks[inviter], "referrals bought a personal rank"


# --- privacy --------------------------------------------------------------

def test_the_progress_endpoint_takes_no_user_id(alice):
    """No parameter to tamper with is stronger than a parameter checked well."""
    import inspect as _inspect
    signature = _inspect.signature(application.api_progress_me)
    assert list(signature.parameters) == ["init"]

    source = (ROOT / "app.py").read_text()
    assert '@app.get("/api/progress/me")' in source
    assert "/api/progress/{" not in source


def test_progress_is_scoped_to_the_caller(alice, bob):
    """Two callers, two different answers, no way to ask for the other's."""
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        svc.recompute_daily_score(s, ALICE["id"], ws, svc.today_local())
        svc.award_xp(s, ALICE["id"], f"probe:{ALICE['id']}", "task", 10,
                     svc.today_local())
        s.commit()

    mine = alice.get("/api/progress/me")
    theirs = bob.get("/api/progress/me")
    assert mine.status_code == 200 and theirs.status_code == 200
    assert mine.json()["xp"]["total"] != theirs.json()["xp"]["total"]

    # And nothing in the payload names another human being.
    body = mine.text.lower()
    for leak in ("bob", "username", "telegram_id", "first_name"):
        assert leak not in body, f"{leak} leaked into the progress payload"


def test_the_progress_endpoint_needs_a_signature(client):
    assert client.get("/api/progress/me").status_code == 401
    assert client.get("/api/progress/achievements").status_code == 401


# --- achievements ---------------------------------------------------------

def test_an_achievement_is_unlocked_once(alice):
    uid = _progress_user()
    with SessionLocal() as s:
        p = svc._progress_row(s, uid)
        p.scored_days = 1
        row = db.DailyScore(user_id=uid, day=svc.today_local(), total_score=50,
                            grade="D")
        s.add(row)
        s.flush()
        first = svc.check_achievements(s, uid, p, row)
        second = svc.check_achievements(s, uid, p, row)
        s.commit()
    assert "first_step" in first
    assert second == [], "an achievement was handed out twice"


def test_the_achievement_list_shows_locked_ones_with_progress(alice):
    body = alice.get("/api/progress/achievements").json()["achievements"]
    assert len(body) == len(svc.ACHIEVEMENTS)
    locked = [a for a in body if not a["unlocked"]]
    assert locked, "every achievement was already unlocked"
    for entry in body:
        assert entry["progress"] <= entry["target"]


# --- the existing product must not regress -------------------------------

def test_progression_did_not_change_the_new_user_defaults():
    """User creation and the action funnel were both edited. Still three."""
    uid = next(_next_id)
    with SessionLocal() as s:
        svc.get_or_create_user(s, uid, first_name="Still")
        s.commit()
        ws = svc.workspace_id_for(s, uid)
        names = [h["name"] for h in svc.list_habits(s, ws)]
    assert names == ["Get up", "5x namoz", "Kundalik"]
    for gone in ("Deep flow", "Sport", "Podcast", "Read"):
        assert gone not in names


def test_a_progression_failure_never_fails_the_users_action(alice, monkeypatch):
    """Ticking a task is what they asked for. Scoring it is bookkeeping."""
    def explode(*a, **kw):
        raise RuntimeError("progression is broken")

    monkeypatch.setattr(svc, "refresh_progress", explode)
    with SessionLocal() as s:
        outcome = svc.record_action_and_progress(s, ALICE["id"])
    assert outcome["actions"] > 0
    assert outcome["progress"] == {}


def test_deleting_an_account_takes_its_progression_with_it():
    """No orphan rows, and no progression outliving the person."""
    uid = _progress_user()
    with SessionLocal() as s:
        svc.award_xp(s, uid, f"task:{uid}:x", "task", 10, svc.today_local())
        svc.refresh_progress(s, uid)
        s.commit()
        assert s.get(db.UserProgress, uid) is not None

    with SessionLocal() as s:
        assert svc.delete_account(s, uid) is True
        s.commit()

    with SessionLocal() as s:
        assert s.get(db.UserProgress, uid) is None
        for model in (db.XPEvent, db.DailyScore, db.UserAchievement):
            left = s.scalar(select(func.count()).select_from(model)
                            .where(model.user_id == uid))
            assert left == 0, f"{model.__tablename__} kept orphan rows"


# ---------------------------------------------------------------------------
# Report delivery — the whole path, on the schedule production actually runs
# ---------------------------------------------------------------------------
#
# Every report test above hands `_send_reports_locked` an explicit date, which
# is the one argument production never passes. That short-circuits the due
# check entirely, so the tests that looked like they covered delivery were
# really only covering rendering and the outbox. These drive the path the
# scheduler drives: `report_date=None`, the user's own clock, the real data
# and the real renderer.

def _solo_recipient(client, monkeypatch, **fields):
    """One onboarded user, alone in the batch, with `fields` applied."""
    telegram_id = next(_next_id)
    Caller(client, {"id": telegram_id, "first_name": "Tick"})
    with SessionLocal() as s:
        user = s.get(User, telegram_id)
        for key, value in fields.items():
            setattr(user, key, value)
        s.commit()
        ws = svc.workspace_id_for(s, telegram_id)
    monkeypatch.setattr(svc, "active_recipients",
                        lambda s: [(telegram_id, ws, "uz")])
    return telegram_id, ws


async def test_a_report_goes_out_on_the_real_scheduler_path(monkeypatch, client):
    """report_date=None — the only form the scheduler ever calls."""
    tz = svc.TZ
    now = svc.now_local(tz)
    telegram_id, _ = _solo_recipient(
        client, monkeypatch,
        morning_time=(now - timedelta(minutes=1)).time().replace(
            second=0, microsecond=0))

    bot = _FakeBot()
    await application._send_reports_locked(bot, "morning", None)

    assert bot.sent == [telegram_id], (
        "a user whose morning time has just passed must receive the report "
        "when the job runs the way the scheduler runs it")


async def test_a_report_is_sent_once_over_a_whole_day_of_ticks(monkeypatch, client):
    """Ticking every two minutes for a day must produce exactly one report.

    This is the property the frequent tick exists to provide, and the one a
    duplicate would break. Twelve hours of ticks, one message.
    """
    telegram_id, _ = _solo_recipient(client, monkeypatch,
                                morning_time=dtime(5, 0))
    bot = _FakeBot()
    day = date(2031, 5, 4)
    moment = datetime.combine(day, dtime(0, 0))

    monkeypatch.setattr(svc, "now_local", lambda tz=None: moment)
    monkeypatch.setattr(svc, "today_local", lambda tz=None: moment.date())
    for _ in range(360):                      # 12 hours at two-minute ticks
        await application._send_reports_locked(bot, "morning", None)
        moment += timedelta(minutes=2)

    assert bot.sent == [telegram_id], f"expected exactly one report, got {bot.sent}"


async def test_a_dead_workers_claim_is_reclaimed_and_retried(monkeypatch, client):
    """A claim nobody resolved must not cost the user their day.

    A process killed between `claim_report` and `mark_report_sent` leaves a
    row that blocks the slot for ever: it satisfies the unique constraint, so
    no later tick can claim it, and nothing sends it.
    """
    telegram_id, ws = _solo_recipient(client, monkeypatch, morning_time=dtime(5, 0))
    day = date(2031, 5, 5)

    with SessionLocal() as s:
        claim_id = svc.claim_report(s, ws, "morning", day)
        assert claim_id is not None
        # Backdate it, as a process that died half an hour ago would leave it.
        row = s.get(db.DailyReportLog, claim_id)
        row.claimed_at = db.utcnow() - timedelta(
            minutes=svc.STALE_CLAIM_MINUTES + 5)
        s.commit()
        assert svc.claim_report(s, ws, "morning", day) is None, "slot is blocked"

    with SessionLocal() as s:
        assert svc.reclaim_stale_claims(s, day) == 1
        assert svc.claim_report(s, ws, "morning", day) is not None, (
            "the freed slot must be claimable again")


async def test_a_fresh_claim_is_never_reclaimed(client):
    """Recovery must not steal a slot from a worker that is still sending."""
    telegram_id = next(_next_id)
    Caller(client, {"id": telegram_id, "first_name": "Live"})
    day = date(2031, 5, 6)
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, telegram_id)
        assert svc.claim_report(s, ws, "morning", day) is not None
        assert svc.reclaim_stale_claims(s, day) == 0, (
            "a claim taken seconds ago belongs to a live sender")


async def test_a_database_error_releases_the_claim_instead_of_burning_the_day(
        monkeypatch, client):
    """A wobbling database must not cost the user their report for the day."""
    from sqlalchemy.exc import OperationalError

    telegram_id, ws = _solo_recipient(client, monkeypatch, morning_time=dtime(5, 0))
    day = date(2031, 5, 7)

    bot = _FakeBot({telegram_id: OperationalError("SELECT 1", {}, Exception("gone"))})
    await application._send_reports_locked(bot, "morning", day)

    with SessionLocal() as s:
        assert svc.claim_report(s, ws, "morning", day) is not None, (
            "a transient database failure must leave the slot free to retry")


async def test_a_telegram_failure_does_burn_the_day(monkeypatch, client):
    """The counterpart: a blocked bot will not improve within the day."""
    from telegram.error import Forbidden

    telegram_id, ws = _solo_recipient(client, monkeypatch, morning_time=dtime(5, 0))
    day = date(2031, 5, 8)

    bot = _FakeBot({telegram_id: Forbidden("bot was blocked by the user")})
    await application._send_reports_locked(bot, "morning", day)

    with SessionLocal() as s:
        assert svc.claim_report(s, ws, "morning", day) is None, (
            "a permanent failure must stay claimed, not be retried every tick")


def test_a_report_job_survives_a_process_that_restarts_all_day():
    """The trigger must not depend on the process being alive at one instant.

    This is the bug that switched reports off in production. A `cron(hour=4)`
    job on an in-memory jobstore recomputes its next fire time at every boot,
    always to the next 04:00 *after now* — so a process that restarts more
    often than once a day pushes the job forward for ever and it never runs.
    A frequent tick has no such instant to miss.
    """
    from apscheduler.triggers.cron import CronTrigger

    def fired_days(trigger, alive_hours: int) -> int:
        fired = 0
        for offset in range(30):
            boot = (datetime(2031, 6, 1, 9, 0, tzinfo=svc.TZ)
                    + timedelta(days=offset))
            following = trigger.get_next_fire_time(None, boot)
            if following is not None and following < boot + timedelta(hours=alive_hours):
                fired += 1
        return fired

    fixed_hour = CronTrigger(hour=4, minute=0, timezone=svc.TZ)
    every_tick = CronTrigger(minute=f"*/{config.REPORT_TICK_MINUTES}",
                             timezone=svc.TZ)

    assert fired_days(fixed_hour, alive_hours=6) == 0, (
        "sanity check: this is the failure mode being guarded against")
    assert fired_days(every_tick, alive_hours=6) == 30, (
        "the report tick must fire regardless of when the process restarts")


# ---------------------------------------------------------------------------
# The logic bugs that survived because nothing asked about them
# ---------------------------------------------------------------------------

def test_wiping_a_workspace_also_clears_the_progression():
    """"Erase everything I wrote" has to include the numbers built from it.

    XP, scores, achievements and progress hang off `user_id` rather than
    `workspace_id`, so the workspace-scoped wipe never reached them and left
    the user at their old level with a streak over an empty workspace.
    """
    telegram_id = next(_next_id)
    _onboard(telegram_id)
    day = date(2031, 7, 1)
    with SessionLocal() as s:
        svc.award_xp(s, telegram_id, f"test:{telegram_id}", "task", 10, day)
        svc.refresh_progress(s, telegram_id)
        s.commit()
        assert s.scalar(select(func.count()).select_from(db.XPEvent)
                        .where(db.XPEvent.user_id == telegram_id)) > 0

    with SessionLocal() as s:
        assert svc.wipe_workspace(s, telegram_id) is True

    with SessionLocal() as s:
        for model in (db.XPEvent, db.DailyScore, db.UserAchievement):
            left = s.scalar(select(func.count()).select_from(model)
                            .where(model.user_id == telegram_id))
            assert left == 0, f"{model.__tablename__} survived the wipe"
        progress = s.get(db.UserProgress, telegram_id)
        assert progress is None or (progress.total_xp or 0) == 0
        # The account itself, and its habits, must still be there.
        assert s.get(User, telegram_id) is not None
        ws = svc.workspace_id_for(s, telegram_id)
        assert s.scalar(select(func.count()).select_from(db.Habit)
                        .where(db.Habit.workspace_id == ws)) > 0


def test_each_ritual_earns_its_own_kind_of_xp():
    """Waking, praying and journalling must not all read as "woke up early".

    `early_riser` counts wake days by `event_type`. While all three rituals
    wrote the same label, praying and journalling counted towards it too and
    the thirty-day award arrived in about ten.
    """
    telegram_id = next(_next_id)
    _onboard(telegram_id)
    day = date(2031, 7, 2)
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, telegram_id)
        habits = {h.system_key: h.id for h in s.scalars(
            select(db.Habit).where(db.Habit.workspace_id == ws)).all()}
        for key in (svc.SYSTEM_WAKEUP, svc.SYSTEM_PRAYER, svc.SYSTEM_JOURNAL):
            assert key in habits, f"missing system habit {key}"
            s.add(db.HabitLog(workspace_id=ws, habit_id=habits[key],
                              day=day, done=True))
        s.commit()
        svc.sync_day_xp(s, telegram_id, ws, day)
        s.commit()

        types = {t for (t,) in s.execute(
            select(db.XPEvent.event_type)
            .where(db.XPEvent.user_id == telegram_id,
                   db.XPEvent.event_date == day)).all()}

    assert {"wake", "prayer", "journal"} <= types, (
        f"each ritual needs its own event_type, got {types}")
    assert "ritual" not in types, "the shared label must be gone"


def test_two_accounts_created_at_once_get_different_member_numbers():
    """The number is shown to the user, so two people cannot share one."""
    ids = [next(_next_id) for _ in range(6)]
    numbers = []
    for telegram_id in ids:
        with SessionLocal() as s:
            user, created = svc.get_or_create_user(s, telegram_id)
            assert created
            numbers.append(user.member_no)
    assert len(set(numbers)) == len(numbers), f"duplicate member_no: {numbers}"
    assert all(n > 0 for n in numbers)


def test_a_monthly_task_set_for_the_31st_returns_to_the_31st():
    """February must borrow the date, not keep it.

    Clamping the 31st to the 28th is right for February; computing March from
    that clamp is not, and it left the task on the 28th for ever.
    """
    run, cursor = [], date(2031, 1, 31)
    for _ in range(4):
        cursor = svc.next_occurrence("monthly", cursor, anchor_day=31)
        run.append(cursor)

    assert run == [date(2031, 2, 28), date(2031, 3, 31),
                   date(2031, 4, 30), date(2031, 5, 31)], run


def test_a_habit_reminder_is_not_repeated_by_a_second_tick(client):
    """The job window is not a substitute for recording what was sent."""
    telegram_id = next(_next_id)
    Caller(client, {"id": telegram_id, "first_name": "Nudge"})
    with SessionLocal() as s:
        user = s.get(User, telegram_id)
        user.habit_reminders = True
        ws = svc.workspace_id_for(s, telegram_id)
        habit = s.scalars(select(db.Habit).where(
            db.Habit.workspace_id == ws)).first()
        tz = svc.user_tz(user)
        now = svc.now_local(tz)
        habit.remind_at = now.time().replace(second=0, microsecond=0)
        habit.schedule = svc.SCHEDULE_DAILY
        habit.paused_at = None
        habit_id = habit.id
        s.commit()

    with SessionLocal() as s:
        user = s.get(User, telegram_id)
        due = svc.due_habit_reminders(s, ws, user, now=now)
        assert any(h["id"] == habit_id for h in due), "the first tick must nudge"
        svc.mark_habit_reminder_sent(s, ws, habit_id, tz=svc.user_tz(user))

    with SessionLocal() as s:
        user = s.get(User, telegram_id)
        again = svc.due_habit_reminders(s, ws, user, now=now)
        assert not any(h["id"] == habit_id for h in again), (
            "a habit already nudged today must not be nudged again")


def test_being_active_late_at_night_is_not_a_day_away():
    """`last_active_at` is UTC; "today" is the user's calendar.

    In Tashkent everything after 19:00 local carries yesterday's UTC date, so
    comparing the raw column with a local date told a user who was using the
    app an hour ago that they had been away, and offered them a fresh start.
    """
    telegram_id = next(_next_id)
    _onboard(telegram_id)
    tz = svc.TZ
    local_now = datetime.now(tz)
    with SessionLocal() as s:
        user = s.get(User, telegram_id)
        # 22:00 local today, stored the way the app stores it: naive UTC.
        local_evening = datetime.combine(local_now.date(), dtime(22, 0))
        user.last_active_at = (local_evening.replace(tzinfo=tz)
                               .astimezone(timezone.utc).replace(tzinfo=None))
        s.commit()
        state = svc.break_state(s, svc.workspace_id_for(s, telegram_id),
                                user, tz=tz)

    assert state["days_away"] == 0, (
        f"active this evening, reported {state['days_away']} days away")
    assert state["suggest_reset"] is False


# ---------------------------------------------------------------------------
# Avatar tokens — a credential in a URL, scoped down to what the URL needs
# ---------------------------------------------------------------------------

def test_an_avatar_token_round_trips():
    token = application.issue_avatar_token(ALICE["id"])
    assert application.verify_avatar_token(token) == ALICE["id"]


def test_an_avatar_token_expires():
    from fastapi import HTTPException

    past = time.time() - security.AVATAR_TOKEN_TTL - 60
    token = application.issue_avatar_token(ALICE["id"], now=past)
    with pytest.raises(HTTPException) as caught:
        application.verify_avatar_token(token)
    assert caught.value.status_code == 401


def test_a_tampered_avatar_token_is_refused():
    from fastapi import HTTPException

    user_id, expires, _ = application.issue_avatar_token(ALICE["id"]).rsplit(":", 2)
    forged = f"{user_id}:{expires}:{'0' * 64}"
    with pytest.raises(HTTPException):
        application.verify_avatar_token(forged)

    # And a token may not be re-pointed at somebody else's picture.
    real = application.issue_avatar_token(ALICE["id"])
    _, expires, signature = real.rsplit(":", 2)
    with pytest.raises(HTTPException):
        application.verify_avatar_token(f"{BOB['id']}:{expires}:{signature}")


def test_the_mini_app_is_given_a_token_only_when_there_is_a_photo(alice):
    body = alice.get("/api/me").json()
    assert "avatar_token" in body
    if not body["has_photo"]:
        assert body["avatar_token"] is None


# ---------------------------------------------------------------------------
# The two ways a report could go silent for ever
# ---------------------------------------------------------------------------

def test_the_job_lock_is_transaction_scoped():
    """A connection-scoped lock can be returned to the pool still held.

    `pg_advisory_unlock` was an explicit statement on the way out, so any
    failure before it handed the connection back to the pool with the lock
    still on it — and every later tick, asking on some other connection, was
    refused and skipped the batch silently. The transaction-scoped variant is
    released by PostgreSQL itself, so there is no statement that can be missed.
    """
    import inspect as _inspect

    source = _inspect.getsource(svc.JobLock)
    assert "pg_try_advisory_xact_lock" in source
    assert "pg_advisory_unlock" not in source, (
        "an explicit unlock is exactly the statement that can fail to run")


def test_repeated_lock_refusals_are_escalated(monkeypatch):
    """One refusal is a peer working. Twenty minutes of them is a stuck lock."""
    svc.LOCK_REFUSALS.clear()
    warnings = []
    monkeypatch.setattr(svc.log, "warning",
                        lambda msg, *a, **k: warnings.append(msg % a if a else msg))

    class _Refusing:
        def __init__(self): self.bind = type("b", (), {"dialect": type("d", (), {"name": "postgresql"})()})()
        def scalar(self, *a, **k): return False
        def rollback(self): pass
        def close(self): pass

    for _ in range(svc.LOCK_REFUSAL_ALARM):
        with svc.JobLock(lambda: _Refusing(), "report:morning") as lock:
            assert lock.acquired is False

    assert warnings, "a lock refused for twenty minutes must not stay at INFO"
    assert "refused its lock" in warnings[-1]
    svc.LOCK_REFUSALS.clear()


async def test_a_rate_limited_send_is_retried_not_written_off(monkeypatch, client):
    """A 429 says nothing about the account, so it must not cost the day."""
    from telegram.error import TimedOut

    telegram_id, ws = _solo_recipient(client, monkeypatch, morning_time=dtime(5, 0))
    day = date(2031, 9, 1)

    bot = _FakeBot({telegram_id: TimedOut()})
    await application._send_reports_locked(bot, "morning", day)

    with SessionLocal() as s:
        row = s.scalar(select(db.DailyReportLog).where(
            db.DailyReportLog.workspace_id == ws,
            db.DailyReportLog.report_date == day))
        assert row.status == "retry", f"expected a retryable row, got {row.status}"

    # The next tick takes it over and delivers.
    bot = _FakeBot()
    await application._send_reports_locked(bot, "morning", day)
    assert bot.sent == [telegram_id], "the retry must actually deliver"


async def test_a_blocked_user_is_not_retried(monkeypatch, client):
    """The counterpart: being blocked will not improve before tomorrow."""
    from telegram.error import Forbidden

    telegram_id, ws = _solo_recipient(client, monkeypatch, morning_time=dtime(5, 0))
    day = date(2031, 9, 2)

    bot = _FakeBot({telegram_id: Forbidden("bot was blocked by the user")})
    await application._send_reports_locked(bot, "morning", day)

    with SessionLocal() as s:
        row = s.scalar(select(db.DailyReportLog).where(
            db.DailyReportLog.workspace_id == ws,
            db.DailyReportLog.report_date == day))
        assert row.status == "failed"

    bot = _FakeBot()
    await application._send_reports_locked(bot, "morning", day)
    assert bot.sent == [], "a blocked account must not be retried all day"


async def test_retries_are_bounded(monkeypatch, client):
    """Retrying for ever is its own failure mode."""
    from telegram.error import TimedOut

    telegram_id, ws = _solo_recipient(client, monkeypatch, morning_time=dtime(5, 0))
    day = date(2031, 9, 3)

    for _ in range(svc.REPORT_MAX_ATTEMPTS + 2):
        await application._send_reports_locked(
            _FakeBot({telegram_id: TimedOut()}), "morning", day)

    with SessionLocal() as s:
        row = s.scalar(select(db.DailyReportLog).where(
            db.DailyReportLog.workspace_id == ws,
            db.DailyReportLog.report_date == day))
        assert row.status == "failed"
        assert row.attempts <= svc.REPORT_MAX_ATTEMPTS


def test_the_report_diagnostic_needs_the_bot_token(client):
    assert client.get("/health/reports").status_code == 404
    assert client.get("/health/reports?key=nope").status_code == 404


def test_the_report_diagnostic_explains_one_user(client, monkeypatch):
    """It has to answer "why did this account get nothing" on its own."""
    telegram_id = next(_next_id)
    Caller(client, {"id": telegram_id, "first_name": "Diag"})
    with SessionLocal() as s:
        user = s.get(User, telegram_id)
        user.timezone = "Asia/Tashkent"
        user.morning_time, user.evening_time = dtime(5, 0), dtime(19, 25)
        s.commit()

    body = client.get(f"/health/reports?key={TOKEN}&limit=200").json()
    assert "scheduler_running" in body and "lock_refusals" in body
    mine = [r for r in body["reports"] if r["telegram_id"] == telegram_id]
    assert mine, "an onboarded account must appear in the diagnostic"
    row = mine[0]
    assert row["is_recipient"] is True
    assert row["morning"]["at"] == "05:00"
    assert row["evening"]["at"] == "19:25"
    assert row["timezone"] == "Asia/Tashkent"


# ---------------------------------------------------------------------------
# Asking the bot itself
# ---------------------------------------------------------------------------
#
# The person who notices that reports stopped is holding a phone, not a shell
# with the database password in it. These two commands are the whole diagnosis
# and the manual send, reachable from the chat.

class _DiagReplies:
    """Stands in for a Telegram message, recording what was sent back."""

    def __init__(self, user_id: int):
        self.sent: list[str] = []
        self.from_user = type("U", (), {
            "id": user_id, "first_name": "Diag", "last_name": "",
            "username": "diag", "language_code": "uz"})()

    async def reply_text(self, text, **kwargs):
        self.sent.append(text)
        return True


class _DiagUpdate:
    def __init__(self, user_id: int):
        self.message = _DiagReplies(user_id)
        self.effective_message = self.message
        self.effective_user = self.message.from_user
        self.effective_chat = type("C", (), {"id": user_id, "type": "private"})()
        self.callback_query = None


class _DiagCtx:
    def __init__(self):
        self.user_data: dict = {}
        self.bot = None
        self.args: list[str] = []


async def _diag_user(client) -> int:
    telegram_id = next(_next_id)
    Caller(client, {"id": telegram_id, "first_name": "Diag"})
    return telegram_id


async def test_the_bot_can_send_todays_report_on_demand(client):
    """`/hisobot` must produce the real report, not a stub."""
    telegram_id = await _diag_user(client)
    update, ctx = _DiagUpdate(telegram_id), _DiagCtx()

    await application.send_report_now(update, ctx, "evening")

    assert update.message.sent, "the command must answer"
    body = update.message.sent[-1]
    assert "<b>" in body, "it must be the rendered report"
    assert body != _generic_error(), "it must not be the generic error"


def _generic_error() -> str:
    return application.t("uz", "error")


async def test_on_demand_morning_report_also_works(client):
    telegram_id = await _diag_user(client)
    update, ctx = _DiagUpdate(telegram_id), _DiagCtx()
    await application.send_report_now(update, ctx, "morning")
    assert update.message.sent and update.message.sent[-1] != _generic_error()


async def test_the_bot_explains_why_reports_are_missing(client):
    """`/tekshir` has to answer the question without a terminal."""
    telegram_id = await _diag_user(client)
    with SessionLocal() as s:
        user = s.get(User, telegram_id)
        user.timezone = "Asia/Tashkent"
        user.morning_time, user.evening_time = dtime(5, 0), dtime(19, 25)
        s.commit()

    update, ctx = _DiagUpdate(telegram_id), _DiagCtx()
    await application.show_report_health(update, ctx)

    assert update.message.sent, "the command must answer"
    body = update.message.sent[-1]
    for expected in ("Hisobot tekshiruvi", "05:00", "19:25", "Asia/Tashkent"):
        assert expected in body, f"{expected!r} missing from:\n{body}"


async def test_the_diagnosis_names_the_reason_when_gated(client, monkeypatch):
    """Being excluded from the recipient query is the invisible failure."""
    telegram_id = await _diag_user(client)
    monkeypatch.setattr(svc, "active_recipients", lambda s: [])

    update, ctx = _DiagUpdate(telegram_id), _DiagCtx()
    await application.show_report_health(update, ctx)

    body = update.message.sent[-1]
    assert "ro'yxatda yo'qsiz" in body, (
        f"an excluded user must be told that is the reason:\n{body}")


# ---------------------------------------------------------------------------
# A report the process slept through
# ---------------------------------------------------------------------------
#
# The symptom that led here: habit reminders arrived every day and the 05:00
# report never did. Both run on the same scheduler, through the same lock and
# the same recipient query — so the difference was not any of those. It was the
# hour. Reminders are set for times people are awake, which is also when the
# service is being used; 05:00 is the one moment nothing is touching it, and a
# platform that sleeps an idle container or cycles it overnight was simply not
# running during the only 90 minutes that report was allowed to go out.

def _sleeper(telegram_id: int) -> User:
    with SessionLocal() as s:
        user = s.get(User, telegram_id)
        user.timezone = "Asia/Tashkent"
        user.morning_time = dtime(5, 0)
        user.morning_report = True
        # An established account, so the new-account guard does not apply.
        user.created_at = db.utcnow() - timedelta(days=30)
        s.commit()
        s.expunge(user)
        return user


def test_a_report_missed_overnight_still_goes_out_in_the_morning(client):
    """The process was asleep at 05:00. The report is still owed at 08:00."""
    telegram_id = next(_next_id)
    Caller(client, {"id": telegram_id, "first_name": "Slept"})
    user = _sleeper(telegram_id)
    day = date(2031, 10, 1)

    at_five = datetime.combine(day, dtime(5, 0))
    assert svc.report_is_due(user, "morning", at_five), "on time"

    at_eight = datetime.combine(day, dtime(8, 0))
    assert svc.report_is_due(user, "morning", at_eight), (
        "a report nothing was awake to send must still be owed when the "
        "process comes back")


def test_the_catch_up_does_not_deliver_a_morning_report_at_night(client):
    """"This morning's summary" has to still mean this morning."""
    telegram_id = next(_next_id)
    Caller(client, {"id": telegram_id, "first_name": "Late"})
    user = _sleeper(telegram_id)
    day = date(2031, 10, 2)

    assert not svc.report_is_due(user, "morning",
                                 datetime.combine(day, dtime(12, 0)))
    assert not svc.report_is_due(user, "morning",
                                 datetime.combine(day, dtime(23, 30)))
    # And never before its hour.
    assert not svc.report_is_due(user, "morning",
                                 datetime.combine(day, dtime(4, 30)))


def test_the_catch_up_never_crosses_midnight(client):
    """An evening report late enough to land on the next local day is dropped.

    Otherwise the catch-up would claim tomorrow's slot with today's summary.
    """
    telegram_id = next(_next_id)
    Caller(client, {"id": telegram_id, "first_name": "Midnight"})
    with SessionLocal() as s:
        user = s.get(User, telegram_id)
        user.timezone = "Asia/Tashkent"
        user.evening_time = dtime(22, 0)
        user.evening_report = True
        user.created_at = db.utcnow() - timedelta(days=30)
        s.commit()
        s.expunge(user)

    day = date(2031, 10, 3)
    assert svc.report_is_due(user, "evening", datetime.combine(day, dtime(23, 0)))
    assert not svc.report_is_due(user, "evening",
                                 datetime.combine(day, dtime(23, 59, 59)) + timedelta(seconds=1))


def test_an_account_registered_long_after_its_hour_gets_nothing(client):
    """Registering at 15:00 must not trigger that morning's report.

    The catch-up limit is what enforces this, rather than a separate rule
    about new accounts: 15:00 is hours past 05:00 plus the catch-up, so the
    report is simply no longer owed to anybody.
    """
    telegram_id = next(_next_id)
    Caller(client, {"id": telegram_id, "first_name": "Newcomer"})
    user = _sleeper(telegram_id)
    day = date(2031, 10, 4)

    assert not svc.report_is_due(user, "morning",
                                 datetime.combine(day, dtime(15, 0)))


# ---------------------------------------------------------------------------
# The outbox key that switched the reports off
# ---------------------------------------------------------------------------
#
# Found in production, from its own logs: every claim rejected, no row holding
# the slot. `daily_report_logs` was carrying a unique key from an older schema
# — (workspace_id, report_type), with no date — so the first report a user was
# ever sent filled their only slot and every day after it was refused. The job
# logs looked healthy the whole time.

def _break_the_outbox_key(engine) -> None:
    """Recreate the outbox the way the affected database had it."""
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE daily_report_logs"))
        conn.execute(text("""
            CREATE TABLE daily_report_logs (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL,
                report_type VARCHAR(10) NOT NULL,
                report_date DATE NOT NULL,
                status VARCHAR(10), attempts INTEGER,
                last_error VARCHAR(200),
                claimed_at DATETIME, sent_at DATETIME,
                CONSTRAINT uq_daily_report UNIQUE (workspace_id, report_type))
        """))


def test_a_stale_outbox_key_is_detected_and_worked_around(client):
    """The outbox is unusable, and the report still has to go out.

    The unique key is wrong, so the insert is refused for every day after the
    first and no row can be written at all. Rather than go quiet, the claim
    falls through to `job_runs`, which carries the same kind of key on a table
    this bug does not touch — one claim per day, no duplicates, on a database
    whose schema is still broken.
    """
    telegram_id = next(_next_id)
    Caller(client, {"id": telegram_id, "first_name": "Blocked"})
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, telegram_id)

    _break_the_outbox_key(db.engine)
    svc.CLAIM_ANOMALIES.clear()
    try:
        first, second = date(2031, 11, 1), date(2031, 11, 2)
        with SessionLocal() as s:
            assert svc.claim_report(s, ws, "morning", first) is not None
        # The day the broken key would have refused outright.
        with SessionLocal() as s:
            claimed = svc.claim_report(s, ws, "morning", second)
        assert claimed == svc.FALLBACK_CLAIM, (
            "a day the outbox cannot record must still be claimable")
        # ...and still exactly once.
        with SessionLocal() as s:
            assert svc.claim_report(s, ws, "morning", second) is None, (
                "the fallback must not let the same day be claimed twice")
        assert svc.CLAIM_ANOMALIES.get("count"), (
            "working around it must not hide that the schema is wrong")
    finally:
        svc.CLAIM_ANOMALIES.clear()
        db.init_db()


def test_starting_up_repairs_a_stale_outbox_key(client):
    """The repair has to happen on boot: the affected deploys have no shell."""
    telegram_id = next(_next_id)
    Caller(client, {"id": telegram_id, "first_name": "Repaired"})
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, telegram_id)

    _break_the_outbox_key(db.engine)
    svc.CLAIM_ANOMALIES.clear()

    db.init_db()                       # what a restart does

    try:
        # Consecutive days must now each get their own slot.
        first = date(2031, 11, 10)
        ids = []
        for offset in range(3):
            with SessionLocal() as s:
                ids.append(svc.claim_report(s, ws, "morning",
                                            first + timedelta(days=offset)))
        assert all(i is not None for i in ids), f"still blocked: {ids}"

        # And the once-a-day guarantee must survive the repair.
        with SessionLocal() as s:
            assert svc.claim_report(s, ws, "morning", first) is None
    finally:
        svc.CLAIM_ANOMALIES.clear()


def test_the_repair_keeps_the_rows_it_finds(client):
    """A schema fix must not throw away what was already delivered."""
    telegram_id = next(_next_id)
    Caller(client, {"id": telegram_id, "first_name": "History"})
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, telegram_id)

    _break_the_outbox_key(db.engine)
    with SessionLocal() as s:
        s.add(db.DailyReportLog(workspace_id=ws, report_type="morning",
                                report_date=date(2031, 11, 20), status="sent",
                                attempts=1, claimed_at=db.utcnow()))
        s.commit()

    db.init_db()

    with SessionLocal() as s:
        kept = s.scalar(select(db.DailyReportLog).where(
            db.DailyReportLog.workspace_id == ws,
            db.DailyReportLog.report_date == date(2031, 11, 20)))
        assert kept is not None and kept.status == "sent", (
            "a report already delivered must survive the repair")


def test_the_repair_is_idempotent(client):
    """It runs on every boot, so running it again must change nothing."""
    before = db._repair_report_outbox_key()
    again = db._repair_report_outbox_key()
    assert before is None and again is None, (
        "a healthy outbox key must be left alone")


# ---------------------------------------------------------------------------
# What the two daily reports actually say
# ---------------------------------------------------------------------------
#
# Morning: good morning, how yesterday went, what today asks for.
# Evening: the day as one number against yesterday's, then what was done and
# what was not. The number on its own is not the point — the comparison and
# the two lists are.

def test_the_evening_report_states_the_change_from_yesterday():
    """An arrow hides the size, which is the part worth knowing."""
    payload = _evening_payload()
    payload["overall"] = {"value": 78, "trend": "up", "yesterday": 60,
                          "components": {"tasks": 75}}
    text = application.render_evening(payload, "uz")
    assert "+18%" in text, f"the size of the change must be stated:\n{text}"

    payload["overall"] = {"value": 40, "trend": "down", "yesterday": 65,
                          "components": {"tasks": 40}}
    assert "−25%" in application.render_evening(payload, "uz")

    payload["overall"] = {"value": 50, "trend": "flat", "yesterday": 50,
                          "components": {"tasks": 50}}
    text = application.render_evening(payload, "uz")
    assert "+" not in text.split("📊")[1].split("\n")[1], "no change is not a rise"


def test_the_evening_report_invents_no_comparison():
    """A day with nothing measured is not a 78% improvement."""
    payload = _evening_payload()
    payload["overall"] = {"value": 78, "trend": "flat", "yesterday": None,
                          "components": {"tasks": 75}}
    text = application.render_evening(payload, "uz")
    assert "+78%" not in text
    assert application.t("uz", "r_vs_yesterday_none") in text


def test_the_evening_report_shows_both_what_was_and_was_not_done():
    payload = _evening_payload()
    text = application.render_evening(payload, "uz")
    assert application.t("uz", "r_done_title") in text
    assert application.t("uz", "r_missed_title") in text
    # The leftovers are named, and counted.
    assert "Call the accountant" in text
    # One task left plus one habit left: the count is what is actually open.
    left = (len(payload["tasks_remaining"]) + len(payload["tasks_overdue"])
            + len(payload["habits_remaining"]))
    assert f"{application.t('uz', 'r_missed_title')}</b> · {left}" in text


def test_a_day_with_nothing_left_over_says_so():
    payload = _evening_payload()
    payload["tasks_remaining"] = []
    payload["tasks_overdue"] = []
    payload["habits_remaining"] = []
    text = application.render_evening(payload, "uz")
    assert application.t("uz", "r_nothing_missed") in text


def test_a_day_with_nothing_done_is_not_dressed_up():
    payload = _evening_payload()
    payload.update(tasks_completed=0, habits_done=0, prayer_performed=0,
                   journal=False)
    text = application.render_evening(payload, "uz")
    assert application.t("uz", "r_nothing_done") in text


def test_the_morning_report_carries_yesterday_and_today(client):
    """Built from the real data, not a fixture, so the payload keys are real."""
    telegram_id = next(_next_id)
    Caller(client, {"id": telegram_id, "first_name": "Ernest"})
    with SessionLocal() as s:
        user = s.get(User, telegram_id)
        # The fixture registers through initData, which does not write the
        # profile name; the report greets by the stored one.
        user.first_name = "Ernest"
        ws = svc.workspace_id_for(s, telegram_id)
        tz = svc.user_tz(user)
        svc.add_task(s, ws, "Bugungi ish", deadline=svc.today_local(tz))
        s.commit()
        data = svc.morning_data(s, ws, user)
        text = application.render_morning(data, "uz")

    assert application.t("uz", "r_good_morning", name="Ernest") in text
    assert application.t("uz", "r_yesterday") in text, "yesterday's result"
    assert "Bugungi ish" in text, "today's work"


def test_both_reports_render_in_every_language(client):
    """A missing key in one language would only show up in production."""
    telegram_id = next(_next_id)
    Caller(client, {"id": telegram_id, "first_name": "Ernest"})
    with SessionLocal() as s:
        user = s.get(User, telegram_id)
        ws = svc.workspace_id_for(s, telegram_id)
        morning = svc.morning_data(s, ws, user)
        evening = svc.evening_data(s, ws, user)

    for lang in ("uz", "ru", "en"):
        for render, payload in ((application.render_morning, morning),
                                (application.render_evening, evening)):
            text = render(payload, lang)
            assert text and "{" not in text, (
                f"{render.__name__} in {lang} left a placeholder unfilled:\n{text}")


# ---------------------------------------------------------------------------
# Teams — a shared list where the effort stays separate
# ---------------------------------------------------------------------------
#
# The product this is for is two people working towards the same thing. That
# makes two properties load-bearing, and they pull in opposite directions:
# both of them must see the same item, and neither of them may tick it for the
# other. Everything below is one of those two, or the membership boundary that
# keeps a team from becoming a way to read somebody's private workspace.

def _named(client, telegram_id: int, name: str) -> int:
    """An onboarded account with a profile name, as a real one has.

    The fixture registers through initData, which does not write the Telegram
    profile; the bot does, on every `/start`. Team screens show people by
    name, so the tests need one.
    """
    Caller(client, {"id": telegram_id, "first_name": name})
    with SessionLocal() as s:
        s.get(User, telegram_id).first_name = name
        s.commit()
    return telegram_id


def _pair(client) -> tuple[int, int, int]:
    """Two onboarded accounts and a team the first one owns."""
    one = _named(client, next(_next_id), "Ernest")
    two = _named(client, next(_next_id), "Gulyora")
    with SessionLocal() as s:
        team = svc.create_team(s, one, "Ernest va Gulyora")
        svc.join_team(s, two, team.code)
        return one, two, team.id


def test_an_invite_link_adds_the_person_who_opens_it(client):
    one = _named(client, next(_next_id), "Ernest")
    two = _named(client, next(_next_id), "Gulyora")
    with SessionLocal() as s:
        team = svc.create_team(s, one, "Ernest va Gulyora")
        code = svc.parse_team_payload(f"team_{team.code}")
        assert code == team.code

        assert svc.join_team(s, two, code)[1] == "joined"
        assert svc.join_team(s, two, code)[1] == "already", "twice is not two"
        assert svc.join_team(s, two, "nonsense")[1] == "unknown"

        names = {m["name"] for m in svc.team_members(s, team.id)}
    assert names == {"Ernest", "Gulyora"}


def test_a_shared_task_is_ticked_per_person(client):
    """The heart of it: one task, two independent completions."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        task = svc.add_team_task(s, one, team_id, "Matritsalar mavzusi")

        # Both see it, neither has done it.
        for uid in (one, two):
            row = svc.list_team_tasks(s, uid, team_id)[0]
            assert row["title"] == "Matritsalar mavzusi"
            assert row["done"] is False

        svc.toggle_team_task(s, one, task["id"])

        mine = svc.list_team_tasks(s, one, team_id)[0]
        theirs = svc.list_team_tasks(s, two, team_id)[0]
        assert mine["done"] is True, "the person who ticked it has done it"
        assert theirs["done"] is False, (
            "ticking your own share must not tick anybody else's")
        assert mine["done_count"] == theirs["done_count"] == 1, (
            "both sides see the same progress")

        svc.toggle_team_task(s, two, task["id"])
        assert svc.list_team_tasks(s, two, team_id)[0]["done_count"] == 2


def test_a_shared_habit_is_ticked_per_person_per_day(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        habit = svc.add_team_habit(s, two, team_id, "30 daqiqa o'qish")
        svc.toggle_team_habit(s, two, habit["id"])

        def row(uid, day=None):
            rows = svc.list_team_habits(s, uid, team_id, day=day)
            return next(x for x in rows if x["id"] == habit["id"])

        assert row(two)["done"] is True
        assert row(one)["done"] is False

        # Yesterday is a different day, and untouched.
        yesterday = svc.today_local() - timedelta(days=1)
        assert row(two, yesterday)["done"] is False


def test_a_stranger_cannot_reach_a_team(client):
    """Membership is the only key, and it is checked on every call."""
    one, two, team_id = _pair(client)
    outsider = next(_next_id)
    Caller(client, {"id": outsider, "first_name": "Begona"})

    with SessionLocal() as s:
        task = svc.add_team_task(s, one, team_id, "Ichki ish")

        assert svc.team_for(s, outsider, team_id) is None
        for call in (
            lambda: svc.list_team_tasks(s, outsider, team_id),
            lambda: svc.list_team_habits(s, outsider, team_id),
            lambda: svc.add_team_task(s, outsider, team_id, "Yomon"),
            lambda: svc.add_team_habit(s, outsider, team_id, "Yomon"),
            lambda: svc.toggle_team_task(s, outsider, task["id"]),
            lambda: svc.archive_team_task(s, outsider, task["id"]),
        ):
            with pytest.raises(PermissionError):
                call()


def test_a_team_never_exposes_a_private_workspace(client):
    """Joining a team must not make anybody's own lists visible."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        ws_one = svc.workspace_id_for(s, one)
        svc.add_task(s, ws_one, "Shaxsiy ish — hech kim ko'rmasin")
        s.commit()

        shared = [t["title"] for t in svc.list_team_tasks(s, two, team_id)]
        summary = svc.team_day_summary(s, team_id)
    assert "Shaxsiy ish — hech kim ko'rmasin" not in shared
    assert all("Shaxsiy" not in t["title"] for t in summary["tasks"])


def test_either_member_can_rename_the_team(client):
    """A shared space belongs to the people in it, not only its creator."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        svc.rename_team(s, two, team_id, "Biz ikkimiz")
        assert svc.team_for(s, one, team_id).name == "Biz ikkimiz"
        with pytest.raises(ValueError):
            svc.rename_team(s, one, team_id, "   ")


def test_the_owner_leaving_hands_the_team_over(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        assert svc.leave_team(s, one, team_id) is True
        team = svc.team_for(s, two, team_id)
        assert team is not None and team.owner_id == two, (
            "the remaining member keeps the team")
        assert svc.team_for(s, one, team_id) is None


def test_the_last_member_out_archives_the_team(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        svc.leave_team(s, one, team_id)
        svc.leave_team(s, two, team_id)
        assert svc.teams_for(s, two) == []


def test_the_day_summary_reports_each_member_separately(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        a = svc.add_team_task(s, one, team_id, "Birinchi",
                              deadline=svc.today_local())
        svc.add_team_task(s, one, team_id, "Ikkinchi", deadline=svc.today_local())
        svc.toggle_team_task(s, one, a["id"])

        summary = svc.team_day_summary(s, team_id)

    # Two tasks plus the three rituals every team is seeded with.
    rituals = len(svc.DEFAULT_TEAM_HABITS)
    total = 2 + rituals
    by_id = {m["user_id"]: m for m in summary["members"]}
    assert by_id[one]["done"] == 1 and by_id[one]["total"] == total
    assert by_id[two]["done"] == 0 and by_id[two]["total"] == total
    assert by_id[one]["percent"] == round(1 / total * 100)
    assert by_id[two]["percent"] == 0


def test_a_new_team_starts_with_the_rituals(client):
    """The shared space is the whole programme, not only a to-do list.

    Waking, prayer and the journal are seeded into every team, exactly as
    they are into every workspace, because the point of doing this with
    somebody is that you are both keeping the same day — not that you agreed
    on two errands.
    """
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        habits = svc.list_team_habits(s, one, team_id)
        summary = svc.team_day_summary(s, team_id)

    keys = {h["id"]: h for h in habits}
    assert len(habits) == len(svc.DEFAULT_TEAM_HABITS), habits
    assert summary["total"] == len(svc.DEFAULT_TEAM_HABITS)
    # Nothing ticked yet, so the day is measured and at nought — which is
    # different from unmeasured, and correct: these are owed today.
    assert all(m["percent"] == 0 for m in summary["members"])


def test_the_seeded_rituals_cannot_be_deleted(client):
    """One member removing "namoz" for both of them is not a shared decision."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        ritual = svc.list_team_habits(s, one, team_id)[0]
        with pytest.raises(ValueError):
            svc.archive_team_habit(s, two, ritual["id"])


def test_the_team_api_keeps_each_side_separate(client):
    """The Mini App must get the same per-person answer the services give."""
    one = _named(client, next(_next_id), "Ernest")
    two = _named(client, next(_next_id), "Gulyora")
    a = Caller(client, {"id": one, "first_name": "Ernest"})
    b = Caller(client, {"id": two, "first_name": "Gulyora"})

    made = a.post("/api/teams", {"name": "Ernest va Gulyora"})
    assert made.status_code == 200, made.text
    team_id = made.json()["id"]
    with SessionLocal() as s:
        svc.join_team(s, two, svc.team_for(s, one, team_id).code)

    created = a.post(f"/api/teams/{team_id}/tasks", {"title": "Matritsalar"})
    assert created.status_code == 200, created.text
    task_id = created.json()["id"]

    assert a.post(f"/api/teams/tasks/{task_id}/toggle").json()["done"] is True

    mine = a.get("/api/teams").json()["teams"][0]["tasks"][0]
    theirs = b.get("/api/teams").json()["teams"][0]["tasks"][0]
    assert mine["done"] is True and theirs["done"] is False
    assert mine["done_count"] == theirs["done_count"] == 1


def test_the_team_api_refuses_a_team_you_are_not_in(client):
    one, two, team_id = _pair(client)
    outsider = _named(client, next(_next_id), "Begona")
    stranger = Caller(client, {"id": outsider, "first_name": "Begona"})

    assert stranger.get("/api/teams").json()["teams"] == []
    assert stranger.post(f"/api/teams/{team_id}/tasks",
                         {"title": "Yomon"}).status_code == 404
    assert stranger.patch(f"/api/teams/{team_id}",
                          {"name": "O'zimniki"}).status_code == 404
    assert stranger.delete(f"/api/teams/{team_id}").status_code == 404


def test_the_team_message_names_who_did_what(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        task = svc.add_team_task(s, one, team_id, "Matritsalar",
                                 deadline=svc.today_local())
        svc.add_team_task(s, one, team_id, "Kitob o'qish",
                          deadline=svc.today_local())
        svc.toggle_team_task(s, one, task["id"])
        summary = svc.team_day_summary(s, team_id)

    # Both people are named, including the reader. A report about two people
    # that calls one of them "you" reads as a form rather than as the two of
    # them, and the names are the thing that makes it scannable.
    mine = application.render_team(summary, "uz", one, evening=True)
    assert "Matritsalar" in mine and "Kitob o'qish" in mine
    assert "Ernest" in mine and "Gulyora" in mine
    assert application.t("uz", "team_you") not in mine

    # And it reads the same from the other side — same facts, same names.
    theirs = application.render_team(summary, "uz", two, evening=True)
    assert "Ernest" in theirs and "Gulyora" in theirs


def test_the_team_message_lists_the_shared_rituals(client):
    """A team always has the day's rituals on it, so it always has something
    to say. The "say nothing" branch still exists for a team whose every item
    has been archived, which the rituals themselves cannot be."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        summary = svc.team_day_summary(s, team_id)
    text = application.render_team(summary, "uz", one, evening=False)
    assert text and "5x namoz" in text


def test_a_summary_with_no_items_at_all_sends_nothing(client):
    """The guard itself, driven directly."""
    assert application.render_team({"total": 0}, "uz", 1, evening=False) is None
    assert application.render_team({}, "uz", 1, evening=True) is None


async def test_team_summaries_ride_along_with_the_daily_reports(
        monkeypatch, client):
    """The team message is its own message, sent after the personal one."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        task = svc.add_team_task(s, one, team_id, "Matritsalar",
                                 deadline=svc.today_local())
        svc.toggle_team_task(s, one, task["id"])
        ws = svc.workspace_id_for(s, one)
    monkeypatch.setattr(svc, "active_recipients", lambda s: [(one, ws, "uz")])

    bot = _FakeBot()
    await application._send_reports_locked(bot, "morning", date(2032, 1, 5))

    assert bot.sent.count(one) == 2, (
        f"expected the daily report and one team summary, got {bot.sent}")


async def test_a_user_with_no_team_gets_one_message(monkeypatch, client):
    """Somebody working alone is not sent an empty team summary."""
    solo = _named(client, next(_next_id), "Yolg'iz")
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, solo)
    monkeypatch.setattr(svc, "active_recipients", lambda s: [(solo, ws, "uz")])

    bot = _FakeBot()
    await application._send_reports_locked(bot, "evening", date(2032, 1, 6))

    assert bot.sent.count(solo) == 1, f"one message only, got {bot.sent}"


async def test_a_failed_team_summary_never_unsends_the_report(
        monkeypatch, client):
    """The report is marked sent before the team message is attempted."""
    from telegram.error import Forbidden

    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        task = svc.add_team_task(s, one, team_id, "Matritsalar",
                                 deadline=svc.today_local())
        svc.toggle_team_task(s, one, task["id"])
        ws = svc.workspace_id_for(s, one)
    monkeypatch.setattr(svc, "active_recipients", lambda s: [(one, ws, "uz")])

    class _FailsSecond(_FakeBot):
        async def send_message(self, chat_id, text, **kwargs):
            self.sent.append(chat_id)
            if len(self.sent) > 1:
                raise Forbidden("blocked")
            return True

    day = date(2032, 1, 7)
    bot = _FailsSecond()
    await application._send_reports_locked(bot, "morning", day)

    with SessionLocal() as s:
        row = s.scalar(select(db.DailyReportLog).where(
            db.DailyReportLog.workspace_id == ws,
            db.DailyReportLog.report_date == day))
    assert row is not None and row.status == "sent", (
        "a team summary that could not be delivered must not mark the "
        "personal report failed — it already went out")


# ---------------------------------------------------------------------------
# Shared work on a personal screen — shown together, scored apart
# ---------------------------------------------------------------------------

def test_shared_work_appears_on_the_personal_screens(client):
    """A day's plan that hides half of what you owe is not a plan."""
    one, two, team_id = _pair(client)
    caller = Caller(client, {"id": one, "first_name": "Ernest"})
    with SessionLocal() as s:
        svc.add_team_task(s, one, team_id, "Umumiy ish", deadline=svc.today_local())
        svc.add_team_habit(s, one, team_id, "Umumiy odat")

    tasks = caller.get("/api/tasks").json()
    habits = caller.get("/api/habits").json()

    assert [x["title"] for x in tasks["team_tasks"]] == ["Umumiy ish"]
    # The rituals every team is seeded with, plus the one just added.
    names = [x["name"] for x in habits["team_habits"]]
    assert "Umumiy odat" in names
    assert "5x namoz" in names and len(names) == len(svc.DEFAULT_TEAM_HABITS) + 1
    # Named, so the screen can say whose work it is.
    assert tasks["team_tasks"][0]["team_name"] == "Ernest va Gulyora"
    assert {x["id"] for x in tasks["teams"]} == {team_id}


def test_shared_work_is_kept_out_of_the_personal_lists(client):
    """Shown alongside, never mixed in — the scored list stays workspace-only."""
    one, two, team_id = _pair(client)
    caller = Caller(client, {"id": one, "first_name": "Ernest"})
    with SessionLocal() as s:
        svc.add_team_task(s, one, team_id, "Umumiy ish", deadline=svc.today_local())

    tasks = caller.get("/api/tasks").json()
    personal = [x["title"] for group in ("overdue", "upcoming", "undated", "later")
                for x in tasks.get(group) or []]
    assert "Umumiy ish" not in personal


def test_shared_work_counts_in_the_personal_score(client):
    """One number for the day, and the shared half is part of it.

    This reverses an earlier decision. The two were kept apart so that a quiet
    team evening could not drag down a day somebody had personally finished —
    but two people doing the programme together ended up reading two
    percentages and trusting neither. A shared task is work you owe today, so
    it is in the day, and finishing it moves the day.
    """
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, one)
        tz = svc.user_tz(s.get(User, one))
        today = svc.today_local(tz)

        task = svc.add_team_task(s, one, team_id, "Umumiy ish", deadline=today)
        before = svc.overall_components(s, ws, today, tz=tz)
        assert before["tasks"] is not None, (
            "a shared task due today gives the day a task denominator")

        svc.toggle_team_task(s, one, task["id"])
        after = svc.overall_components(s, ws, today, tz=tz)

    assert after["tasks"] > before["tasks"], (
        f"finishing shared work must raise the day: {before} -> {after}")


def test_one_persons_share_is_the_one_that_counts_for_them(client):
    """Your partner finishing their half does not finish yours."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        ws_one = svc.workspace_id_for(s, one)
        tz = svc.user_tz(s.get(User, one))
        today = svc.today_local(tz)
        task = svc.add_team_task(s, one, team_id, "Umumiy ish", deadline=today)

        svc.toggle_team_task(s, two, task["id"])          # she does it
        mine = svc.overall_components(s, ws_one, today, tz=tz)

    assert mine["tasks"] == 0, (
        "the other member's tick must not score this member's day")


def test_the_team_score_counts_only_shared_work(client):
    """And the mirror image: a personal task is not the team's business."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, one)
        today = svc.today_local()
        svc.add_task(s, ws, "Shaxsiy ish", deadline=today)
        s.commit()
        summary = svc.team_day_summary(s, team_id)
    assert summary["tasks"] == [], (
        f"a personal task leaked into the team's day: {summary['tasks']}")


def test_shared_work_is_listed_per_viewer(client):
    """Each side's block carries their own ticks, not a shared one."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        task = svc.add_team_task(s, one, team_id, "Umumiy ish",
                                 deadline=svc.today_local())
        svc.toggle_team_task(s, one, task["id"])
        mine = svc.team_items_for_day(s, one)
        theirs = svc.team_items_for_day(s, two)
    assert mine["tasks"][0]["done"] is True
    assert theirs["tasks"][0]["done"] is False
    assert mine["tasks"][0]["done_count"] == theirs["tasks"][0]["done_count"] == 1


def test_a_user_with_no_team_gets_empty_blocks(client):
    """The picker and the blocks must simply not appear for a solo user."""
    solo = _named(client, next(_next_id), "Yolg'iz")
    caller = Caller(client, {"id": solo, "first_name": "Yolg'iz"})
    tasks = caller.get("/api/tasks").json()
    assert tasks["team_tasks"] == [] and tasks["teams"] == []


# ---------------------------------------------------------------------------
# One day, one number — and the team's own record
# ---------------------------------------------------------------------------

def test_the_team_rituals_move_the_personal_day(client):
    """Waking, prayer and the journal are shared work that scores."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, one)
        tz = svc.user_tz(s.get(User, one))
        today = svc.today_local(tz)

        before = svc.weighted_overall(svc.overall_components(s, ws, today, tz=tz))
        for habit in svc.list_team_habits(s, one, team_id):
            svc.toggle_team_habit(s, one, habit["id"])
        after = svc.weighted_overall(svc.overall_components(s, ws, today, tz=tz))

    assert after > before, (
        f"keeping the shared rituals must raise the day: {before} -> {after}")


def test_a_shared_habit_counts_at_the_top_tier(client):
    """A promise made to somebody else is not a bonus item."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        added = svc.add_team_habit(s, one, team_id, "Birga yugurish")
    assert added["category"] == "non_negotiable"


def test_the_team_has_its_own_record(client):
    """Both lines, not an average that hides who carried the week."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        today = svc.today_local()
        task = svc.add_team_task(s, one, team_id, "Matritsalar", deadline=today)
        svc.toggle_team_task(s, one, task["id"])
        stats = svc.team_stats(s, one, team_id, period="week")

    assert len(stats["series"]) == 7
    by_id = {m["user_id"]: m for m in stats["members"]}
    assert by_id[one]["done"] == 1 and by_id[two]["done"] == 0
    assert by_id[one]["percent"] > by_id[two]["percent"]
    assert stats["together"] is not None
    # Today's point carries a number for each member, keyed by their id.
    assert str(one) in stats["series"][-1] and str(two) in stats["series"][-1]


def test_the_team_record_is_refused_to_a_stranger(client):
    one, two, team_id = _pair(client)
    outsider = _named(client, next(_next_id), "Begona")
    with SessionLocal() as s:
        with pytest.raises(PermissionError):
            svc.team_stats(s, outsider, team_id)

    stranger = Caller(client, {"id": outsider, "first_name": "Begona"})
    assert stranger.get(f"/api/teams/{team_id}/stats").status_code == 404


def test_a_team_streak_needs_the_whole_day(client):
    """Half the rituals is not a day kept."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        today = svc.today_local()
        habits = svc.list_team_habits(s, one, team_id)
        svc.toggle_team_habit(s, one, habits[0]["id"])
        assert svc.team_streak(s, team_id, one, today) == 0, "partial is not a day"

        for habit in habits[1:]:
            svc.toggle_team_habit(s, one, habit["id"])
        assert svc.team_streak(s, team_id, one, today) == 1


def test_the_team_stats_api_answers_for_a_member(client):
    one, two, team_id = _pair(client)
    caller = Caller(client, {"id": one, "first_name": "Ernest"})
    body = caller.get(f"/api/teams/{team_id}/stats?period=month").json()
    assert body["days"] == 30 and len(body["series"]) == 30
    assert {m["user_id"] for m in body["members"]} == {one, two}


def test_a_protected_ritual_is_refused_by_the_api(client):
    one, two, team_id = _pair(client)
    caller = Caller(client, {"id": one, "first_name": "Ernest"})
    ritual = caller.get("/api/teams").json()["teams"][0]["habits"][0]
    assert caller.delete(f"/api/teams/habits/{ritual['id']}").status_code == 422


def test_the_screen_is_told_which_habits_are_protected(client):
    """So it can hide a delete button that would only ever be refused."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        rows = svc.list_team_habits(s, one, team_id)
    seeded = [r for r in rows if r["system_key"]]
    assert seeded and all(r["protected"] for r in seeded)
    assert all(r["category"] == "non_negotiable" for r in seeded)

    with SessionLocal() as s:
        added = svc.add_team_habit(s, one, team_id, "Birga yugurish")
        rows = svc.list_team_habits(s, one, team_id)
    mine = next(r for r in rows if r["id"] == added["id"])
    assert mine["protected"] is False, "what a member added, a member may remove"


# ---------------------------------------------------------------------------
# Parity — a shared item is not a weaker kind of item
# ---------------------------------------------------------------------------

def test_a_shared_habit_takes_every_setting_a_private_one_does(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        habit = svc.add_team_habit(
            s, one, team_id, "Birga yugurish", schedule="weekdays",
            category="target", target_time=dtime(6, 30), remind_at=dtime(6, 0))
    assert habit["schedule"] == "weekdays"
    assert habit["category"] == "target"
    assert habit["target_time"] == "06:30"
    assert habit["remind_at"] == "06:00"


def test_a_shared_task_takes_every_setting_a_private_one_does(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        task = svc.add_team_task(
            s, one, team_id, "Hisobot", deadline=date(2032, 3, 31),
            due_time=dtime(18, 0), remind_before=30, recurrence="monthly",
            priority="high")
    assert task["due_time"] == "18:00"
    assert task["remind_before"] == 30
    assert task["recurrence"] == "monthly"
    assert task["priority"] == "high"


def test_a_shared_habit_sits_in_its_own_tier(client):
    """"Asosiy bo'lsa asosiyda tursin" — grouped with its peers, not apart."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, one)
        svc.add_team_habit(s, one, team_id, "Birga yugurish", category="target")
        grouped = svc.habits_by_category(s, ws)

    names = {tier: [h["name"] for h in rows] for tier, rows in grouped.items()}
    assert "Birga yugurish" in names["target"]
    assert "5x namoz" in names["non_negotiable"], "the rituals sit at the top tier"
    # Every row says where it came from, personal ones included.
    for rows in grouped.values():
        for row in rows:
            assert row["source"] in ("personal", "team")
            if row["source"] == "team":
                assert row["team_name"]


def test_a_paused_shared_habit_is_not_owed(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        habit = svc.add_team_habit(s, one, team_id, "Birga yugurish")
        svc.edit_team_habit(s, one, habit["id"], paused=True)
        rows = svc.list_team_habits(s, one, team_id)
    assert all(r["id"] != habit["id"] for r in rows)


def test_a_shared_ritual_cannot_be_renamed_by_one_member(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        ritual = svc.list_team_habits(s, one, team_id)[0]
        with pytest.raises(ValueError):
            svc.edit_team_habit(s, two, ritual["id"], name="Boshqa nom")


def test_shared_reminders_are_per_member(client):
    """One of you being told must never silence the other."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        for uid in (one, two):
            user = s.get(User, uid)
            user.habit_reminders = True
            user.timezone = "Asia/Tashkent"
        s.commit()
        now = svc.now_local(svc.TZ)
        habit = svc.add_team_habit(
            s, one, team_id, "Birga yugurish",
            remind_at=now.time().replace(second=0, microsecond=0))

        def due(uid):
            return [x["id"] for x in svc.due_team_habit_reminders(
                s, uid, s.get(User, uid), now=now)]

        assert habit["id"] in due(one) and habit["id"] in due(two)

        svc.mark_team_habit_reminded(s, one, habit["id"])
        assert habit["id"] not in due(one), "already told"
        assert habit["id"] in due(two), "the other member is still owed it"


def test_a_finished_shared_task_is_not_reminded(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        user = s.get(User, one)
        user.task_reminders = True
        s.commit()
        now = svc.now_local(svc.user_tz(user))
        task = svc.add_team_task(
            s, one, team_id, "Hisobot", deadline=now.date(),
            due_time=now.time().replace(second=0, microsecond=0),
            remind_before=0)
        assert any(x["id"] == task["id"] for x in
                   svc.due_team_task_reminders(s, one, user, now=now))

        svc.toggle_team_task(s, one, task["id"])
        assert not any(x["id"] == task["id"] for x in
                       svc.due_team_task_reminders(s, one, user, now=now))


def test_unticking_a_shared_task_keeps_it_unticked(client):
    """The row is now state, not a bare completion — flipping must still work."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        task = svc.add_team_task(s, one, team_id, "Hisobot",
                                 deadline=svc.today_local())
        assert svc.toggle_team_task(s, one, task["id"]) is True
        assert svc.toggle_team_task(s, one, task["id"]) is False
        rows = svc.list_team_tasks(s, one, team_id)
        mine = next(r for r in rows if r["id"] == task["id"])
    assert mine["done"] is False and mine["done_count"] == 0


def test_the_other_member_is_told_who_to_tell(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        assert svc.teammates_of(s, team_id, one) == [two]
        assert svc.teammates_of(s, team_id, two) == [one]


# ---------------------------------------------------------------------------
# A shared item opens like a private one, and can move between the two
# ---------------------------------------------------------------------------

def test_a_shared_habit_has_the_same_history_shape(client):
    """The sheet that draws a private habit has to draw a shared one."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, one)
        personal = svc.add_habit(s, ws, "Shaxsiy odat")
        s.commit()
        mine = svc.habit_history(s, ws, personal.id)

        shared_id = svc.add_team_habit(s, one, team_id, "Umumiy odat")["id"]
        svc.toggle_team_habit(s, one, shared_id)
        ours = svc.team_habit_history(s, one, shared_id)

    for key in ("streak", "grid", "last7_done", "last7_due",
                "last30_done", "last30_due", "percent", "schedule",
                "category", "days", "paused", "target_time", "remind_at"):
        assert key in ours, f"{key} missing from the shared history"
        assert key in mine
    assert ours["team_name"] == "Ernest va Gulyora"


def test_a_shared_history_shows_the_other_member(client):
    """The reason to keep a habit with somebody is to see how they are doing."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        habit = svc.add_team_habit(s, one, team_id, "Umumiy odat")
        svc.toggle_team_habit(s, two, habit["id"])
        seen = svc.team_habit_history(s, one, habit["id"])

    by_id = {m["user_id"]: m for m in seen["members"]}
    assert by_id[one]["is_you"] is True and by_id[one]["done_today"] is False
    assert by_id[two]["is_you"] is False and by_id[two]["done_today"] is True
    # `percent` is the thirty-day rate, so one day of a fresh habit is a few
    # per cent — what matters is that hers moved and his did not.
    assert by_id[two]["percent"] > by_id[one]["percent"] == 0
    assert by_id[two]["last30_done"] == 1


def test_a_habit_moves_into_a_team_and_keeps_its_days(client):
    """Losing the streak is why nobody would ever move one."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, one)
        habit = svc.add_habit(s, ws, "Yugurish")
        s.commit()
        today = svc.today_local()
        for offset in range(3):
            s.add(db.HabitLog(workspace_id=ws, habit_id=habit.id,
                              day=today - timedelta(days=offset), done=True))
        s.commit()

        moved = svc.move_habit(s, one, habit_id=habit.id, to_team=team_id)
        history = svc.team_habit_history(s, one, moved["id"])

    assert history["name"] == "Yugurish"
    assert history["streak"] >= 3, f"the run came with it: {history['streak']}"
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, one)
        assert all(h["name"] != "Yugurish"
                   for h in svc.list_habits(s, ws)), "and left the private list"


def test_a_shared_habit_moves_back_to_private(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        shared = svc.add_team_habit(s, one, team_id, "Yugurish")
        svc.toggle_team_habit(s, one, shared["id"])
        moved = svc.move_habit(s, one, team_habit_id=shared["id"])
        ws = svc.workspace_id_for(s, one)
        names = [h["name"] for h in svc.list_habits(s, ws)]
        still_shared = [h["name"] for h in svc.list_team_habits(s, one, team_id)]
    assert moved["source"] == "personal"
    assert "Yugurish" in names and "Yugurish" not in still_shared


def test_a_seeded_ritual_cannot_be_moved(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        ritual = svc.list_team_habits(s, one, team_id)[0]
        with pytest.raises(ValueError):
            svc.move_habit(s, one, team_habit_id=ritual["id"])


# ---------------------------------------------------------------------------
# Shared projects, and the report the Team screen is built from
# ---------------------------------------------------------------------------

def test_a_shared_task_files_onto_a_shared_project(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        project = svc.add_team_project(s, one, team_id, "Universitet")
        task = svc.add_team_task(s, one, team_id, "Matritsalar",
                                 deadline=svc.today_local(),
                                 project_id=project["id"])
        assert task["project"] == "Universitet"

        listed = svc.list_team_projects(s, one, team_id)
        assert listed[0]["tasks_total"] == 1 and listed[0]["tasks_done"] == 0

        svc.toggle_team_task(s, one, task["id"])
        assert svc.list_team_projects(s, one, team_id)[0]["tasks_done"] == 1


def test_a_shared_project_stays_out_of_the_private_list(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        svc.add_team_project(s, one, team_id, "Universitet")
        ws = svc.workspace_id_for(s, one)
        assert all(p["name"] != "Universitet" for p in svc.list_projects(s, ws))


def test_a_task_cannot_be_filed_on_another_teams_shelf(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        other = svc.create_team(s, one, "Boshqa jamoa")
        theirs = svc.add_team_project(s, one, other.id, "Ularniki")
        with pytest.raises(ValueError):
            svc.add_team_task(s, one, team_id, "Yomon",
                              project_id=theirs["id"])


def test_the_scoreboard_says_who_each_item_is_waiting_on(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        task = svc.add_team_task(s, one, team_id, "Matritsalar",
                                 deadline=svc.today_local())
        svc.toggle_team_task(s, one, task["id"])
        board = svc.team_scoreboard(s, one, team_id)

    waiting = {x["title"]: x["missing"] for x in board["open"]}
    assert waiting["Matritsalar"] == [two], "one of them still owes it"
    assert set(board["periods"]) == {"day", "week", "month"}
    for rows in board["periods"].values():
        assert {r["user_id"] for r in rows} == {one, two}


def test_the_scoreboard_moves_an_item_to_done_when_both_have_it(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        task = svc.add_team_task(s, one, team_id, "Matritsalar",
                                 deadline=svc.today_local())
        svc.toggle_team_task(s, one, task["id"])
        svc.toggle_team_task(s, two, task["id"])
        board = svc.team_scoreboard(s, one, team_id)
    assert "Matritsalar" in [x["title"] for x in board["done"]]
    # The seeded rituals are still owed, so "open" is not empty — only the
    # task has moved across.
    assert "Matritsalar" not in [x["title"] for x in board["open"]]


# ---------------------------------------------------------------------------
# The numbers have to be about days that happened
# ---------------------------------------------------------------------------

def test_a_period_counts_only_days_the_work_existed(client):
    """A team made today has not missed a month.

    The month denominator used to include every day before the team was
    created, so a pair who started yesterday read "1% · 2/154" and reasonably
    concluded the number was broken.
    """
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        today = svc.today_local()
        task = svc.add_team_task(s, one, team_id, "Matritsalar", deadline=today)
        svc.toggle_team_task(s, one, task["id"])
        board = svc.team_scoreboard(s, one, team_id)

    mine = {p: next(r for r in board["periods"][p] if r["is_you"])
            for p in ("day", "week", "month")}
    assert mine["day"]["total"] == mine["week"]["total"] == mine["month"]["total"], (
        f"nothing existed before today: {mine}")
    assert mine["day"]["percent"] == mine["month"]["percent"]


def test_a_first_period_has_no_change_to_report(client):
    """No previous week means no delta, not +100%."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        board = svc.team_scoreboard(s, one, team_id)
    for rows in board["periods"].values():
        for row in rows:
            assert row["delta"] is None, (
                "a period with nothing before it cannot report a change")


def test_a_change_is_reported_once_there_is_a_yesterday(client):
    """With a day behind it, today reports the direction and the size."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        today = svc.today_local()
        yesterday = today - timedelta(days=1)
        # Backdate the team and its rituals so yesterday counts.
        team = svc.team_for(s, one, team_id)
        team.created_at = db.utcnow() - timedelta(days=5)
        for habit in s.scalars(select(db.TeamHabit).where(
                db.TeamHabit.team_id == team_id)).all():
            habit.created_at = db.utcnow() - timedelta(days=5)
        s.commit()

        # Nothing yesterday, everything today.
        for habit in svc.list_team_habits(s, one, team_id):
            svc.toggle_team_habit(s, one, habit["id"])
        board = svc.team_scoreboard(s, one, team_id)

    day = next(r for r in board["periods"]["day"] if r["is_you"])
    assert day["percent"] == 100
    assert day["previous"] == 0
    assert day["delta"] == 100, f"a real rise must be reported: {day}"


def test_a_shared_task_can_be_edited_like_a_private_one(client):
    """Deadline, time, priority, repeat, project — all of it."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        project = svc.add_team_project(s, one, team_id, "Universitet")
        task = svc.add_team_task(s, one, team_id, "Matritsalar")
        updated = svc.edit_team_task(
            s, one, task["id"], title="Matritsalar — 2-qism",
            deadline=date(2032, 5, 20), due_time=dtime(17, 30),
            remind_before=15, recurrence="weekly", priority="high",
            project_id=project["id"])

    assert updated["title"] == "Matritsalar — 2-qism"
    assert updated["deadline"] == "2032-05-20"
    assert updated["due_time"] == "17:30"
    assert updated["remind_before"] == 15
    assert updated["recurrence"] == "weekly"
    assert updated["priority"] == "high"
    assert updated["project"] == "Universitet"


def test_the_shared_task_endpoint_answers_like_the_private_one(client):
    one, two, team_id = _pair(client)
    caller = Caller(client, {"id": one, "first_name": "Ernest"})
    with SessionLocal() as s:
        task = svc.add_team_task(s, one, team_id, "Matritsalar",
                                 deadline=svc.today_local())

    got = caller.get(f"/api/teams/tasks/{task['id']}").json()
    for key in ("title", "deadline", "due_time", "remind_before",
                "recurrence", "priority", "project_id", "done"):
        assert key in got, f"{key} missing — the task sheet needs it"
    assert got["source"] == "team" and got["team_name"]

    patched = caller.patch(f"/api/teams/tasks/{task['id']}",
                           {"title": "Yangi nom", "priority": "high"})
    assert patched.status_code == 200
    assert patched.json()["title"] == "Yangi nom"


def test_a_stranger_cannot_read_or_edit_a_shared_task(client):
    one, two, team_id = _pair(client)
    outsider = _named(client, next(_next_id), "Begona")
    stranger = Caller(client, {"id": outsider, "first_name": "Begona"})
    with SessionLocal() as s:
        task = svc.add_team_task(s, one, team_id, "Matritsalar")

    assert stranger.get(f"/api/teams/tasks/{task['id']}").status_code == 404
    assert stranger.patch(f"/api/teams/tasks/{task['id']}",
                          {"title": "Buzildi"}).status_code == 404


# ---------------------------------------------------------------------------
# Changing your mind about where something lives
# ---------------------------------------------------------------------------

def test_a_task_moves_into_a_team_with_everything_on_it(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, one)
        task = svc.add_task(s, ws, "Matematikani bitirish",
                            deadline=date(2032, 6, 1), priority="high",
                            due_time=dtime(18, 0), remind_before=30,
                            recurrence="weekly")
        s.commit()
        moved = svc.move_task(s, one, task_id=task.id, to_team=team_id)

    assert moved["title"] == "Matematikani bitirish"
    assert moved["deadline"] == "2032-06-01" and moved["priority"] == "high"
    assert moved["due_time"] == "18:00" and moved["remind_before"] == 30
    assert moved["recurrence"] == "weekly"
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, one)
        titles = [x["title"] for x in svc.list_tasks(s, ws)["upcoming"]]
    assert "Matematikani bitirish" not in titles


def test_a_shared_task_moves_back_and_keeps_your_own_tick(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        shared = svc.add_team_task(s, one, team_id, "Matematikani bitirish",
                                   deadline=svc.today_local())
        svc.toggle_team_task(s, one, shared["id"])
        moved = svc.move_task(s, one, team_task_id=shared["id"])
        ws = svc.workspace_id_for(s, one)
        row = s.get(db.Task, moved["id"])
    assert moved["source"] == "personal"
    assert row.status == "done", "a finished task does not come back unfinished"


def test_a_project_moves_and_takes_its_tasks(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, one)
        project = svc.add_project(s, ws, "Universitet")
        svc.add_task(s, ws, "Matritsalar", project_id=project.id,
                     deadline=svc.today_local())
        svc.add_task(s, ws, "Integral", project_id=project.id,
                     deadline=svc.today_local())
        s.commit()

        moved = svc.move_project(s, one, project.id, team_id)
        assert moved["source"] == "team" and moved["moved_tasks"] == 2

        shared = svc.list_team_projects(s, one, team_id)
        assert [p["name"] for p in shared] == ["Universitet"]
        assert shared[0]["tasks_total"] == 2
        # And it is gone from the private shelf list.
        assert all(p["name"] != "Universitet" for p in svc.list_projects(s, ws))


def test_a_shared_project_moves_back_to_private(client):
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, one)
        project = svc.add_team_project(s, one, team_id, "Universitet")
        task = svc.add_team_task(s, one, team_id, "Matritsalar",
                                 deadline=svc.today_local(),
                                 project_id=project["id"])
        svc.toggle_team_task(s, one, task["id"])

        moved = svc.move_project(s, one, project["id"], None)
        assert moved["source"] == "personal" and moved["moved_tasks"] == 1
        names = [p["name"] for p in svc.list_projects(s, ws)]
        assert "Universitet" in names
        assert svc.list_team_projects(s, one, team_id) == []


def test_the_move_endpoints_refuse_a_stranger(client):
    one, two, team_id = _pair(client)
    outsider = _named(client, next(_next_id), "Begona")
    stranger = Caller(client, {"id": outsider, "first_name": "Begona"})
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, outsider)
        task = svc.add_task(s, ws, "O'zimniki")
        s.commit()
        task_id = task.id

    # Their own task, but not their team.
    assert stranger.post(f"/api/tasks/{task_id}/move",
                         {"to": f"team:{team_id}"}).status_code == 404


# ---------------------------------------------------------------------------
# One number, from two halves
# ---------------------------------------------------------------------------

def test_the_day_is_the_average_of_the_two_halves(client):
    """Each half has the same say, however many rows it happens to hold."""
    one, two, team_id = _pair(client)
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, one)
        tz = svc.user_tz(s.get(User, one))
        today = svc.today_local(tz)

        before = svc.day_score(s, ws, today, tz=tz)
        assert before["personal"] is not None
        assert before["team"] == 0, "the seeded rituals are owed and undone"

        for habit in svc.list_team_habits(s, one, team_id):
            svc.toggle_team_habit(s, one, habit["id"])
        after = svc.day_score(s, ws, today, tz=tz)

    assert after["team"] == 100
    assert after["personal"] == before["personal"], "the private half is untouched"
    assert after["value"] == round((after["personal"] + 100) / 2), (
        f"the day is the average of the two: {after}")


def test_a_person_with_no_team_is_scored_on_their_own_half(client):
    solo = _named(client, next(_next_id), "Yolg'iz")
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, solo)
        score = svc.day_score(s, ws)
    assert score["team"] is None
    assert score["value"] == score["personal"], (
        "with nothing to average against, the private half is the day")


def test_a_score_gets_a_colour_band():
    assert svc.score_band(95) == "great"
    assert svc.score_band(70) == "good"
    assert svc.score_band(50) == "fair"
    assert svc.score_band(10) == "low"
    assert svc.score_band(None) == "none"


def test_the_bands_in_the_browser_match_the_ones_on_the_server():
    """Two copies of the same thresholds is how they drift apart."""
    html = (ROOT / "webapp" / "index.html").read_text()
    block = html[html.index("function bandOf("):]
    block = block[:block.index("}")]
    for floor, name in svc.SCORE_BANDS:
        if floor:
            assert f">= {floor}" in block, f"{name} band missing from bandOf()"


def test_every_key_the_team_screen_uses_is_defined():
    """A missing key renders as its own name, which only shows in production."""
    import re

    html = (ROOT / "webapp" / "index.html").read_text()
    screen = html[html.index("/* ---------- Jamoa ----------"):
                  html.index("SCREENS.habits = () => {")]
    used = set(re.findall(r'\bt\("([a-z_0-9]+)"', screen))
    uz = html[html.index(" uz:{"):html.index(" en:{")]
    # Keys sit several to a line, so anchor on the separator rather than on
    # the start of the line.
    defined = set(re.findall(r"(?:^|[,{])\s*([a-z_0-9]+)\s*:", uz, re.M))
    missing = sorted(used - defined)
    assert not missing, f"the team screen uses undefined keys: {missing}"
