"""
ErnestOS smoke tests — small on purpose.

Covers only what would be dangerous to get wrong:
user isolation, Telegram initData validation, ownership, subscription gating,
prayer scoring and report idempotency.

    .venv/bin/python -m pytest tests/ -q
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

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


class Caller:
    def __init__(self, client, user):
        self.c, self.user = client, user
        self.h = {"X-Telegram-Init-Data": init_data(user)}

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


def test_goals_are_not_visible_across_users(alice, bob):
    alice.post("/api/goals", json={"title": "ALICE-SECRET-GOAL", "category": "tactical"})
    assert "ALICE-SECRET-GOAL" not in bob.get("/api/goals").text


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


def test_another_users_goal_cannot_be_deleted(alice, bob):
    goal_id = alice.post("/api/goals",
                         json={"title": "keep", "category": "tactical"}).json()["id"]
    assert bob.delete(f"/api/goals/{goal_id}").status_code == 404


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

def test_new_user_gets_exactly_the_six_default_habits(alice):
    names = [h["name"] for h in alice.get("/api/habits").json()["habits"]]
    assert names == ["Get up", "5x namoz", "Deep flow", "Sport", "Podcast", "Read"]


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
    habits = alice.get("/api/habits").json()["habits"]
    normal = next(h for h in habits if not h["protected"])
    assert alice.post(f"/api/habits/{normal['id']}/toggle").json()["done"] is True


# --------------------------------------------------------------------------
# Prayer scoring
# --------------------------------------------------------------------------

def test_male_jamaat_and_on_time_score_one():
    statuses = {p: "jamaat" for p in svc.PRAYERS}
    assert svc.prayer_score(statuses, "male") == 5.0


def test_male_qaza_scores_half():
    assert svc.prayer_score({p: "qaza" for p in svc.PRAYERS}, "male") == 2.5


def test_female_has_no_jamaat_or_qaza():
    assert svc.prayer_score({p: "jamaat" for p in svc.PRAYERS}, "female") == 0.0
    assert svc.prayer_score({p: "qaza" for p in svc.PRAYERS}, "female") == 0.0


def test_female_excused_day_scores_exactly_the_threshold():
    assert svc.prayer_score({}, "female", excused=True) == svc.PRAYER_DONE_THRESHOLD


def test_excused_is_rejected_for_male_users(alice):
    alice.post("/api/settings", json={"gender": "male"})
    assert alice.post("/api/prayers/excused", json={"excused": True}).status_code == 422


def test_setting_prayers_marks_the_protected_habit_done(alice):
    alice.post("/api/settings", json={"gender": "male"})
    for prayer in ["bomdod", "peshin", "asr"]:
        alice.post("/api/prayers", json={"prayer": prayer, "status": "on_time"})
    habits = alice.get("/api/habits").json()["habits"]
    assert next(h for h in habits if h["protected"])["done"] is True


def test_prayer_status_outside_the_gender_set_is_rejected(alice):
    alice.post("/api/settings", json={"gender": "female"})
    r = alice.post("/api/prayers", json={"prayer": "bomdod", "status": "jamaat"})
    assert r.status_code == 422


# --------------------------------------------------------------------------
# Tasks, projects, goals, focus
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


def test_weekly_focus_is_capped_at_three(alice):
    for i in range(3):
        assert alice.post("/api/focus", json={"title": f"mission {i}"}).status_code == 200
    assert alice.post("/api/focus", json={"title": "fourth"}).status_code == 422


def test_goal_category_must_be_known(alice):
    assert alice.post("/api/goals",
                      json={"title": "bad", "category": "galactic"}).status_code == 422


def test_completing_a_goal_sets_progress_to_100(alice):
    goal_id = alice.post("/api/goals",
                         json={"title": "finish", "category": "tactical"}).json()["id"]
    alice.post(f"/api/goals/{goal_id}/complete")
    goals = alice.get("/api/goals").json()
    row = next(g for g in goals["tactical"] if g["id"] == goal_id)
    assert row["status"] == "completed" and row["progress"] == 100


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
