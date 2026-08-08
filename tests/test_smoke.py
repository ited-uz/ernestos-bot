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
from datetime import date, datetime, time as dtime, timedelta
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

def test_new_user_gets_the_default_habits(alice):
    names = [h["name"] for h in alice.get("/api/habits").json()["habits"]]
    assert names == ["Get up", "5x namoz", "Summary",
                     "Deep flow", "Sport", "Podcast", "Read"]


def test_habits_are_grouped_into_three_categories(alice):
    body = alice.get("/api/habits").json()
    assert body["categories"] == ["non_negotiable", "target", "bonus"]
    grouped = body["grouped"]
    assert [h["name"] for h in grouped["non_negotiable"]] == \
        ["Get up", "5x namoz", "Summary"]
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


def test_the_three_derived_habits_are_protected(alice):
    """Get up, 5x namoz and Summary are all computed, never ticked by hand."""
    habits = alice.get("/api/habits").json()["habits"]
    derived = {h["name"] for h in habits if h["protected"]}
    assert derived == {"Get up", "5x namoz", "Summary"}


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
    assert next(h for h in habits if h["name"] == "5x namoz")["done"] is True


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


# --------------------------------------------------------------------------
# Journal — five questions drive the derived Summary habit
# --------------------------------------------------------------------------

def _summary_done(caller) -> bool:
    habits = caller.get("/api/habits").json()["habits"]
    return next(h for h in habits if h["name"] == "Summary")["done"]


def test_journal_exposes_five_questions(alice):
    questions = alice.get("/api/journal").json()["questions"]
    assert len(questions) == 5
    assert {q["id"] for q in questions} == set(svc.JOURNAL_KEYS)


def test_partial_journal_does_not_tick_the_summary_habit(alice):
    alice.post("/api/journal", json={"answers": {"wins": "shipped"}})
    assert _summary_done(alice) is False


def test_complete_journal_ticks_the_summary_habit(alice):
    """Exactly like 5x namoz: the habit is derived, never ticked by hand."""
    alice.post("/api/journal",
               json={"answers": {k: "answer" for k in svc.JOURNAL_KEYS}})
    assert _summary_done(alice) is True


def test_summary_habit_cannot_be_toggled_by_hand(alice):
    habits = alice.get("/api/habits").json()["habits"]
    summary = next(h for h in habits if h["name"] == "Summary")
    assert summary["protected"] is True
    assert alice.post(f"/api/habits/{summary['id']}/toggle").status_code == 400


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


def test_completed_goal_moves_to_the_achieved_list(alice):
    goal_id = alice.post("/api/goals",
                         json={"title": "ACHIEVE-ME", "category": "tactical"}).json()["id"]
    alice.post(f"/api/goals/{goal_id}/complete")
    assert "ACHIEVE-ME" in alice.get("/api/goals/done").text


def test_a_completed_goal_can_be_reopened(alice):
    goal_id = alice.post("/api/goals",
                         json={"title": "REOPEN-ME", "category": "tactical"}).json()["id"]
    alice.post(f"/api/goals/{goal_id}/complete")
    alice.post(f"/api/goals/{goal_id}/reopen")
    goals = alice.get("/api/goals").json()["tactical"]
    assert next(g for g in goals if g["id"] == goal_id)["status"] == "active"


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

def test_stats_export_returns_a_csv_attachment(alice):
    r = alice.get("/api/stats/export?period=week")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert ".csv" in r.headers["content-disposition"]


def test_stats_export_contains_the_daily_series(alice):
    body = alice.get("/api/stats/export?period=week").text
    assert "date,habits %,prayer %" in body
    assert body.count("\n") > 10


def test_stats_export_is_scoped_to_the_caller(alice, bob):
    """Two users must not receive each other's numbers."""
    a = alice.get("/api/stats/export?period=week").text
    b = bob.get("/api/stats/export?period=week").text
    assert "ErnestOS statistics" in a and "ErnestOS statistics" in b


def test_stats_export_rejects_an_unknown_period(alice):
    """An unknown period falls back rather than erroring."""
    r = alice.get("/api/stats/export?period=decade")
    assert r.status_code == 200
    assert "period,month" in r.text
