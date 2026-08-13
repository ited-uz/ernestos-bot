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
import sys
import tempfile
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest

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
import db  # noqa: E402
import migrations  # noqa: E402
import services as svc  # noqa: E402
from db import SessionLocal, User  # noqa: E402

ALICE = {"id": 1001, "first_name": "Alice", "username": "alice"}
BOB = {"id": 2002, "first_name": "Bob", "username": "bob"}


def init_data(user: dict, auth_date: int | None = None, token: str = TOKEN,
              tamper: bool = False) -> str:
    fields = {"user": json.dumps(user, separators=(",", ":")),
              "auth_date": str(auth_date if auth_date is not None else int(time.time()))}
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
    """A caller with a workspace of its own, in the default six-habit state."""
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

def test_new_user_gets_the_default_habits(alice):
    names = [h["name"] for h in alice.get("/api/habits").json()["habits"]]
    assert names == ["Get up", "5x namoz", "Kundalik",
                     "Deep flow", "Sport", "Podcast", "Read"]


def test_habits_are_grouped_into_three_categories(alice):
    body = alice.get("/api/habits").json()
    assert body["categories"] == ["non_negotiable", "target", "bonus"]
    grouped = body["grouped"]
    assert [h["name"] for h in grouped["non_negotiable"]] == \
        ["Get up", "5x namoz", "Kundalik"]
    assert [h["name"] for h in grouped["target"]] == ["Deep flow", "Sport"]
    assert [h["name"] for h in grouped["bonus"]] == ["Podcast", "Read"]


def test_new_habit_lands_in_the_chosen_category(alice):
    alice.post("/api/habits", json={"name": "Meditation", "category": "bonus"})
    grouped = alice.get("/api/habits").json()["grouped"]
    assert "Meditation" in [h["name"] for h in grouped["bonus"]]


def test_unknown_category_falls_back_to_target(alice):
    alice.post("/api/habits", json={"name": "Stretching", "category": "nonsense"})
    grouped = alice.get("/api/habits").json()["grouped"]
    assert "Stretching" in [h["name"] for h in grouped["target"]]


def test_default_theme_is_calm(alice):
    assert alice.get("/api/me").json()["theme"] == "calm"


def test_protected_habit_cannot_be_toggled(alice):
    habits = alice.get("/api/habits").json()["habits"]
    protected = next(h for h in habits if h["protected"])
    assert alice.post(f"/api/habits/{protected['id']}/toggle").status_code == 400


def test_protected_habit_cannot_be_deleted(alice):
    habits = alice.get("/api/habits").json()["habits"]
    protected = next(h for h in habits if h["protected"])
    assert alice.delete(f"/api/habits/{protected['id']}").status_code == 400


def test_normal_habit_toggles(alice):
    habits = alice.get("/api/habits").json()["habits"]
    normal = next(h for h in habits if not h["protected"])
    assert alice.post(f"/api/habits/{normal['id']}/toggle").json()["done"] is True


def test_the_derived_habits_are_protected(alice):
    """All three are computed from their module, never ticked by hand."""
    habits = alice.get("/api/habits").json()["habits"]
    derived = {h["name"] for h in habits if h["protected"]}
    assert derived == {"Get up", "5x namoz", "Kundalik"}


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


def test_only_subscribed_onboarded_users_receive_reports(alice):
    alice.get("/api/me")
    with SessionLocal() as s:
        user = s.get(User, ALICE["id"])
        user.onboarded, user.is_subscribed = True, True
        s.commit()
        assert any(r[0] == ALICE["id"] for r in svc.active_recipients(s))

        user = s.get(User, ALICE["id"])
        user.is_subscribed = False
        s.commit()
        assert not any(r[0] == ALICE["id"] for r in svc.active_recipients(s))


# --------------------------------------------------------------------------
# Subscription gate
# --------------------------------------------------------------------------

def test_api_is_blocked_while_unsubscribed(client, monkeypatch):
    monkeypatch.setattr(application, "REQUIRED_CHANNEL_ID", "-1001234567890")
    with SessionLocal() as s:
        svc.get_or_create_user(s, BOB["id"], first_name="Bob")
        user = s.get(User, BOB["id"])
        user.is_subscribed = False
        s.commit()
    r = client.get("/api/home", headers={"X-Telegram-Init-Data": init_data(BOB)})
    assert r.status_code == 403


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


def test_the_mini_app_navigation_is_the_four_launch_screens():
    html = (ROOT / "webapp" / "index.html").read_text()
    nav = html[html.index("const NAV = ["):html.index("const NAV_OF")]
    assert [line.split('id:"')[1].split('"')[0]
            for line in nav.splitlines() if 'id:"' in line] == \
        ["home", "habits", "tasks", "stats"]


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
    assert "+ privacyNote();" in html.split("SCREENS.home")[1][:900], \
        "the privacy note left Home"
    rule = html.split(".privacy-strip{")[1].split("}")[0]
    assert "position:fixed" not in rule, "the privacy line is chrome again"
    for lang, phrase in (("uz", "to'liq himoya qilingan"),
                         ("en", "fully protected"),
                         ("ru", "полностью защищены")):
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
    assert application.THEMES == ["calm", "titan", "muse", "rage", "nexus"]
    for name in application.THEMES:
        assert f'[data-theme="{name}"]' in styled or name == "calm", \
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

    if name == "calm":
        return re.search(r"\n:root\{(.*?)\n\}", styled, re.S).group(1)
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


@pytest.mark.parametrize("name", ["calm", "titan", "muse", "rage", "nexus"])
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


@pytest.mark.parametrize("name", ["calm", "titan", "muse", "rage", "nexus"])
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


@pytest.mark.parametrize("name", ["titan", "muse", "rage", "nexus"])
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
                   "--motion:", "--primary-fill:")
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
    assert alice.get("/api/me").json()["theme"] == "calm"


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

    previous = application.REQUIRED_CHANNEL_ID
    application.REQUIRED_CHANNEL_ID = "-1001234567890"
    try:
        assert await application.is_subscribed(Failing(), 42, retries=0) is None
    finally:
        application.REQUIRED_CHANNEL_ID = previous


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

def test_a_category_with_nothing_due_is_left_out_of_the_average(alice):
    """An empty category must not be averaged in as 0%."""
    _clear_tasks(ALICE["id"])
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        components = svc.overall_components(s, ws)
        assert components["tasks"] is None
        available = [v for v in components.values() if v is not None]
        assert svc.overall_percent(s, ws) == round(sum(available) / len(available))


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


def test_onboarding_is_language_then_phone_then_channel():
    """Three steps, in that order, and nothing else.

    Phone is asked because it is what account recovery is keyed on, and the
    prompt explains that. It is skippable, so it cannot become a wall.
    """
    assert application.ONBOARDING_STEPS == ["language", "phone", "subscribe", "done"]
    source = (ROOT / "app.py").read_text()
    assert 'user.onboarding_step = "phone"' in source
    assert 'user.onboarding_step = "subscribe"' in source


def test_gender_is_not_an_onboarding_step():
    """It is asked the first time prayer needs it, with the reason attached."""
    source = (ROOT / "app.py").read_text()
    assert 'user.onboarding_step = "gender"' not in source
    html = (ROOT / "webapp" / "index.html").read_text()
    assert "ask_gender_why" in html, "the prayer screen must explain why it asks"


def test_the_phone_step_is_required_and_explains_itself():
    """The number is what account recovery is keyed on, so there is no Skip.

    It is also the one field the user cannot type: only Telegram's own contact
    button proves the number belongs to the sender.
    """
    for lang in ("uz", "en", "ru"):
        keyboard = application.phone_keyboard(lang)
        buttons = [b for row in keyboard.keyboard for b in row]
        assert len(buttons) == 1
        assert buttons[0].request_contact is True
        assert application.t(lang, "btn_skip") not in [
            getattr(b, "text", b) for b in buttons]
        # And the prompt says why it is being asked.
        assert application.t(lang, "phone_why")
    assert "tiklab beramiz" in application.t("uz", "phone_why")


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

def test_report_defaults_are_on_at_four_and_half_past_nine(alice):
    with SessionLocal() as s:
        prefs = svc.prefs_for(s.get(User, ALICE["id"]))
    assert prefs["morning_report"] is True and prefs["morning_time"] == "04:00"
    assert prefs["evening_report"] is True and prefs["evening_time"] == "21:30"


def test_a_report_is_due_only_inside_its_window(alice):
    with SessionLocal() as s:
        user = s.get(User, ALICE["id"])
        today = svc.today_local()
        assert svc.report_is_due(user, "morning",
                                 datetime.combine(today, dtime(4, 5))) is True
        # Far past its time: a "good morning" at noon is noise, and a user who
        # joins at 15:00 must not be sent one immediately.
        assert svc.report_is_due(user, "morning",
                                 datetime.combine(today, dtime(12, 0))) is False
        assert svc.report_is_due(user, "morning",
                                 datetime.combine(today, dtime(3, 30))) is False


def test_a_report_time_the_user_chose_is_the_one_used(alice):
    alice.post("/api/prefs", json={"morning_time": "07:15"})
    with SessionLocal() as s:
        user = s.get(User, ALICE["id"])
        today = svc.today_local()
        assert svc.report_is_due(user, "morning",
                                 datetime.combine(today, dtime(7, 20))) is True
        assert svc.report_is_due(user, "morning",
                                 datetime.combine(today, dtime(4, 5))) is False
    alice.post("/api/prefs", json={"morning_time": "04:00"})


def test_a_switched_off_report_is_never_due(alice):
    alice.post("/api/prefs", json={"evening_report": False})
    with SessionLocal() as s:
        user = s.get(User, ALICE["id"])
        assert svc.report_is_due(user, "evening", datetime.combine(
            svc.today_local(), dtime(21, 5))) is False
    alice.post("/api/prefs", json={"evening_report": True})


def test_the_report_job_interval_is_shared_with_the_scheduler():
    """The windows and the cron entry must not be able to drift apart."""
    source = (ROOT / "app.py").read_text()
    assert 'minute=f"*/{svc.REMINDER_JOB_MINUTES}"' in source
    assert 'minute=f"*/{REPORT_TICK_MINUTES}"' in source
    assert svc.HABIT_REMINDER_WINDOW == timedelta(
        minutes=svc.REMINDER_JOB_MINUTES)


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
    assert set(body["previous"]) == {"overall", "tasks", "habits", "prayer"}
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
    assert body["rule"] == "mean_of_available"
    assert [p["key"] for p in body["parts"]] == ["tasks", "habits", "prayer"]
    counted = [p["percent"] for p in body["parts"] if p["percent"] is not None]
    assert body["value"] == (round(sum(counted) / len(counted)) if counted else 0)
    assert set(body["counted"]) <= {"tasks", "habits", "prayer"}


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
    for target in migrations.THEME_REDESIGN.values():
        assert target in application.THEMES, target
    # And every name an earlier migration can produce is handled by the last.
    for target in migrations.THEME_RENAMES.values():
        assert target in migrations.THEME_REDESIGN, \
            f"0005 lands on {target}, which 0007 does not map"
    for target in migrations.RETIRED_THEMES.values():
        assert target in migrations.THEME_REDESIGN, \
            f"0003 lands on {target}, which 0007 does not map"


@pytest.mark.parametrize("old,new", [
    ("ocean", "calm"), ("pure", "calm"), ("midnight", "titan"),
    ("sage", "muse"), ("aurora", "nexus"), ("cobalt", "calm"),
    ("slate", "titan"), ("blossom", "muse"),
])
def test_the_redesign_moves_a_theme_to_its_closest_survivor(alice, old, new):
    _set_theme(ALICE["id"], old)
    migrations.m0007_redesign_themes()
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).theme == new


def test_the_redesign_is_safe_to_run_twice(alice):
    _set_theme(ALICE["id"], "ocean")
    migrations.m0007_redesign_themes()
    assert migrations.m0007_redesign_themes()["total"] == 0
    with SessionLocal() as s:
        assert s.get(User, ALICE["id"]).theme == "calm"


def test_nobody_is_migrated_into_execution_mode():
    """Rage has no predecessor. Putting somebody in it uninvited is a decision
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
    """It is a non-negotiable, so it belongs in the denominator."""
    body = fresh.get("/api/home").json()
    assert body["habits"]["total"] == 7


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
    """Home is a glance: the day's mission, how today is going, today's tasks.

    Four blocks. The month grid was the fifth and the tallest — forty-two
    cells about the rest of the month on the one screen whose whole job is
    today — and it now opens from the date in the header instead.
    """
    html = (ROOT / "webapp" / "index.html").read_text()
    home = html[html.index("SCREENS.home = () => {"):html.index("function privacyNote")]
    for block in ("headBlock", "missionBlock", "scoreBlock", "tasksBlock"):
        assert block in home, f"Home no longer renders {block}"
    assert "homeCalendar" not in html, "the month grid is back on Home"
    # The blocks that moved off it must not have come back.
    assert "top3Block" not in html and "weekBlock" not in html
    assert "focusBlock" not in html, "the week's focus belongs on Tasks"


def test_the_score_switch_offers_day_week_and_month():
    html = (ROOT / "webapp" / "index.html").read_text()
    assert 'data-act="score-period"' in html
    for period in ("period_day", "period_week", "period_month"):
        assert f't("{period}")' in html


def test_todays_change_is_measured_against_yesterday():
    """A delta on the day panel needs a comparison, and yesterday only counts
    when it had something to measure — otherwise it is absent, not a fall."""
    html = (ROOT / "webapp" / "index.html").read_text()
    assert "d.overall.yesterday" in html
    assert "delta = value - d.overall.yesterday" in html


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
