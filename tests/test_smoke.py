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
    assert names == ["Get up", "5x namoz",
                     "Deep flow", "Sport", "Podcast", "Read"]


def test_habits_are_grouped_into_three_categories(alice):
    body = alice.get("/api/habits").json()
    assert body["categories"] == ["non_negotiable", "target", "bonus"]
    grouped = body["grouped"]
    assert [h["name"] for h in grouped["non_negotiable"]] == \
        ["Get up", "5x namoz"]
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


def test_default_theme_is_graphite(alice):
    assert alice.get("/api/me").json()["theme"] == "graphite"


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
    """Get up and 5x namoz are computed, never ticked by hand."""
    habits = alice.get("/api/habits").json()["habits"]
    derived = {h["name"] for h in habits if h["protected"]}
    assert derived == {"Get up", "5x namoz"}


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


def test_the_week_holds_exactly_one_mission(alice):
    _clear_missions(ALICE["id"])
    assert alice.post("/api/focus", json={"title": "the one"}).status_code == 200
    assert alice.post("/api/focus", json={"title": "second"}).status_code == 422


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
def test_the_menu_is_exactly_seven_entries(lang):
    labels = _menu_labels(lang)
    assert len(labels) == 7
    assert labels == [application.t(lang, key) for key in (
        "menu_home", "menu_habits", "menu_tasks", "menu_stats",
        "menu_settings", "menu_feedback", "menu_app")]


@pytest.mark.parametrize("lang", ["uz", "en", "ru"])
def test_the_menu_has_no_goals_and_no_standing_wake_button(lang):
    labels = _menu_labels(lang)
    assert application.t(lang, "menu_wake") not in labels
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


def test_wake_up_disappears_once_it_is_recorded():
    """A button that can only tell you 'already done' is not worth a tap."""
    assert application.t("uz", "menu_wake") not in _habit_buttons(done=True)


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


def test_the_privacy_line_is_one_fixed_strip():
    html = (ROOT / "webapp" / "index.html").read_text()
    assert html.count('class="privacy-strip"') == 1
    assert "privacyNote" not in html, "per-page privacy cards are back"
    for lang, phrase in (("uz", "maxfiyligingiz to'liq himoyalangan"),
                         ("en", "Your data and privacy are fully protected"),
                         ("ru", "конфиденциальность полностью защищены")):
        assert phrase in html, f"{lang} privacy line missing"
        assert phrase in application.t(lang, "privacy_line") or \
            phrase.replace("'", "'") in application.t(lang, "privacy_line")


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
        data = svc.stats(s, ws, "week")
    text = application.render_stats(data, "uz")
    assert f"{data['today']['overall']}%" in text
    assert alice.get("/api/home").json()["overall"]["value"] == data["today"]["overall"]


def test_statistics_shows_a_dash_for_a_component_with_nothing_due(alice):
    _clear_tasks(ALICE["id"])
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, ALICE["id"])
        data = svc.stats(s, ws, "week")
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
    assert "date,habits %,prayer %" in body
    assert "habit streak" in body
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


def test_home_no_longer_carries_the_removed_sections(alice):
    body = alice.get("/api/home").json()
    for key in ("goals", "projects", "birthdays", "now", "top3", "focus"):
        assert key not in body, f"Home still ships {key}"


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


def test_phone_and_gender_are_not_onboarding_steps():
    """They live in Settings and in the prayer screen respectively."""
    source = (ROOT / "app.py").read_text()
    assert 'user.onboarding_step = "phone"' not in source
    assert 'user.onboarding_step = "gender"' not in source
