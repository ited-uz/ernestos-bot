"""Offline contract tests. AI/Telegram network calls are always mocked here.

These verify safety and integration, NOT Uzbek speech recognition quality.
"""
import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from test_smoke import client, fresh, schema, Caller, _next_id  # noqa: F401
import agent_actions as actions
import agent_core as core
import agent_provider as provider
import config
import db
import services as svc


@pytest.fixture(autouse=True)
def agent_config(monkeypatch):
    monkeypatch.setattr(config, "AGENT_ENABLED", True)
    monkeypatch.setattr(config, "AGENT_PROVIDER", "groq")
    monkeypatch.setattr(config, "GROQ_API_KEY", "fake-key-not-used")
    monkeypatch.setattr(config, "AGENT_DAILY_REQUESTS", 100)
    # Catch accidental real provider calls in every test, even if another mock
    # gets removed by a future refactor. Specific wire tests replace this.
    async def no_network(*args, **kwargs):
        raise AssertionError("live provider call forbidden in tests")
    monkeypatch.setattr(provider, "request", no_network)


@pytest.fixture
def person(fresh):
    uid = fresh.user["id"]
    with db.SessionLocal() as s:
        ws = svc.workspace_id_for(s, uid)
    core.consent(ws, True)
    return fresh, uid, ws


def action(entity="task", operation="create", target_id=None, scope="personal", team_id=None, **fields):
    return {"entity": entity, "operation": operation, "scope": scope,
            "team_id": team_id, "target_id": target_id,
            "changes": [{"field": k, "value": str(v) if v is not None else None} for k, v in fields.items()]}


def plan(*items, question=None, language="uz"):
    return {"language": language, "question": question, "actions": list(items)}


def capture(person, monkeypatch, proposed=None, text="Ertaga hisobot tayyorla", **kwargs):
    _, uid, ws = person
    async def fake(*args):
        return proposed or plan(action(title="Hisobot tayyorlash"))
    monkeypatch.setattr(provider, "plan", fake)
    return asyncio.run(core.ingest(uid, ws, kwargs.pop("key", uuid.uuid4().hex), text=text, **kwargs))


def count(model, ws):
    with db.SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(model).where(model.workspace_id == ws))


def test_text_is_durable_without_side_effect_then_confirms_once(person, monkeypatch):
    _, uid, ws = person
    n = count(db.Task, ws)
    draft = capture(person, monkeypatch)
    assert draft["status"] == "ready"
    assert count(db.Task, ws) == n
    assert core.list_drafts(ws)[0]["id"] == draft["id"]
    result = core.confirm(uid, ws, draft["id"], draft["revision"])
    assert result["status"] == "executed"
    assert count(db.Task, ws) == n + 1
    assert core.confirm(uid, ws, draft["id"], 1) == result
    assert count(db.Task, ws) == n + 1
    with db.SessionLocal() as s:
        assert s.get(db.User, uid).actions_count == 1
        assert s.scalar(select(func.count()).select_from(db.AgentAudit).where(db.AgentAudit.draft_id == draft["id"], db.AgentAudit.event == "executed")) == 1


def test_unnamed_project_is_standalone_not_a_new_project(person, monkeypatch):
    _, uid, ws = person
    before = count(db.Project, ws)
    draft = capture(person, monkeypatch)
    assert "Alohida (yakka vazifa)" in draft["preview"]
    assert count(db.Task, ws) == 0
    result = core.confirm(uid, ws, draft["id"], 1)
    with db.SessionLocal() as s:
        assert s.get(db.Task, result["result"][0]["id"]).project_id is None
    assert count(db.Project, ws) == before


def test_bot_sends_full_proposal_before_confirmation_buttons():
    from agent_bot import AgentBot
    draft = {"id": "a" * 32, "revision": 2, "status": "ready", "transcript": "🎙" * 1900,
             "language": "mixed", "preview": "O‘zgarishlar: " + "x" * 4000, "error": None}
    message = SimpleNamespace(reply_text=AsyncMock())
    asyncio.run(AgentBot(None).show(message, draft, "uz"))
    calls = message.reply_text.await_args_list
    assert len(calls) > 3
    assert all(call.kwargs["reply_markup"] is None for call in calls[:-1])
    assert draft["transcript"] in "".join(call.args[0] for call in calls)
    assert draft["preview"] in "".join(call.args[0] for call in calls)
    assert all(len(call.args[0].encode("utf-16-le")) // 2 < 4096 for call in calls)
    assert calls[-1].kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "ag:c:" + "a" * 32 + ":2"


def test_bot_rejects_voice_in_group_without_downloading():
    from agent_bot import AgentBot
    guard = AsyncMock()
    message = SimpleNamespace(reply_text=AsyncMock(), voice=SimpleNamespace(get_file=AsyncMock()))
    update = SimpleNamespace(effective_chat=SimpleNamespace(type="group"), effective_message=message)
    asyncio.run(AgentBot(guard).voice(update, SimpleNamespace(user_data={})))
    guard.assert_not_awaited()
    message.voice.get_file.assert_not_awaited()
    message.reply_text.assert_awaited_once()


def test_parallel_confirm_adds_only_once(person, monkeypatch):
    _, uid, ws = person
    draft = capture(person, monkeypatch)
    n = count(db.Task, ws)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: core.confirm(uid, ws, draft["id"], 1), range(2)))
    assert all(r["status"] == "executed" for r in results)
    assert count(db.Task, ws) == n + 1


def test_old_confirmation_cannot_execute_correction(person, monkeypatch):
    _, uid, ws = person
    first = capture(person, monkeypatch)
    second = capture(person, monkeypatch, plan(action(title="Juma kuni hisobot")), text="Jumaga o‘zgartir", draft_id=first["id"], revision=1)
    assert second["revision"] == 2
    with pytest.raises(actions.AgentError, match="stale_draft"):
        core.confirm(uid, ws, first["id"], 1)
    core.confirm(uid, ws, first["id"], 2)
    with db.SessionLocal() as s:
        assert s.scalar(select(db.Task.title).where(db.Task.workspace_id == ws)) == "Juma kuni hisobot"
        assert len(json.loads(s.get(db.AgentDraft, first["id"]).history)) == 1


def test_idempotent_input_and_key_reuse(person, monkeypatch):
    key = uuid.uuid4().hex
    first = capture(person, monkeypatch, key=key)
    assert capture(person, monkeypatch, key=key)["id"] == first["id"]
    with pytest.raises(actions.AgentError, match="request_key_reused"):
        capture(person, monkeypatch, key=key, text="Boshqa buyruq")


def test_cancel_does_not_create_and_old_button_fails(person, monkeypatch):
    _, uid, ws = person
    draft = capture(person, monkeypatch)
    assert core.cancel(ws, draft["id"], 1)["status"] == "cancelled"
    with pytest.raises(actions.AgentError, match="not_ready"):
        core.confirm(uid, ws, draft["id"], 1)
    assert count(db.Task, ws) == 0


def test_model_failure_keeps_inbox(person, monkeypatch):
    async def broken(*args):
        raise actions.AgentError("provider_limit", 429)
    monkeypatch.setattr(provider, "plan", broken)
    _, uid, ws = person
    draft = asyncio.run(core.ingest(uid, ws, uuid.uuid4().hex, text="Ellik ming taksiga ketdi"))
    assert draft["status"] == "failed" and draft["error"] == "provider_limit"
    assert draft["transcript"] == "Ellik ming taksiga ketdi"
    assert count(db.MoneyEntry, ws) == 0


def test_uncertainty_never_executes_even_with_actions(person, monkeypatch):
    _, uid, ws = person
    draft = capture(person, monkeypatch, plan(action(title="Taxmin"), question="Qaysi loyiha?"))
    assert draft["status"] == "needs_input"
    with pytest.raises(actions.AgentError):
        core.confirm(uid, ws, draft["id"], 1)
    assert count(db.Task, ws) == 0


def test_atomic_rollback_when_second_action_fails(person, monkeypatch):
    _, uid, ws = person
    draft = capture(person, monkeypatch, plan(action(title="Birinchi"), action("money", amount=50000, kind="expense", category="food")))
    assert draft["status"] == "ready"
    def fail(*args, **kwargs):
        raise ValueError("simulated failure")
    monkeypatch.setattr(svc, "add_money", fail)
    with pytest.raises(ValueError):
        core.confirm(uid, ws, draft["id"], 1)
    assert count(db.Task, ws) == count(db.MoneyEntry, ws) == 0
    assert core.get_draft(ws, draft["id"])["status"] == "ready"
    with db.SessionLocal() as s:
        assert s.get(db.User, uid).actions_count == 0


def test_money_create_edit_delete_and_snapshot(person, monkeypatch):
    _, uid, ws = person
    draft = capture(person, monkeypatch, plan(action("money", amount=50000, kind="expense", category="transport", note="Taksi")))
    assert "50 000" in draft["preview"]
    result = core.confirm(uid, ws, draft["id"], 1)
    mid = result["result"][0]["id"]
    edited = capture(person, monkeypatch, plan(action("money", "update", mid, amount=45000)))
    core.confirm(uid, ws, edited["id"], 1)
    with db.SessionLocal() as s:
        assert s.get(db.MoneyEntry, mid).amount == 45000
    deleted = capture(person, monkeypatch, plan(action("money", "delete", mid)))
    core.confirm(uid, ws, deleted["id"], 1)
    assert count(db.MoneyEntry, ws) == 0
    with db.SessionLocal() as s:
        saved = json.loads(s.get(db.AgentDraft, deleted["id"]).plan)
        assert saved["actions"][0]["before"]["amount"] == 45000


@pytest.mark.parametrize("entity", ["task", "habit", "project"])
def test_crud_and_done_reopen(person, monkeypatch, entity):
    _, uid, ws = person
    draft = capture(person, monkeypatch, plan(action(entity, **({"title": "Kitob"} if entity == "task" else {"name": "Kitob"}))))
    result = core.confirm(uid, ws, draft["id"], 1)
    tid = result["result"][0]["id"]
    for op, fields in [("update", {"title" if entity == "task" else "name": "Yangi nom"}), ("done", {}), ("reopen", {}), ("delete", {})]:
        draft = capture(person, monkeypatch, plan(action(entity, op, tid, **fields)))
        assert draft["status"] == "ready", draft
        assert core.confirm(uid, ws, draft["id"], 1)["status"] == "executed"
    with db.SessionLocal() as s:
        assert s.get(actions.PERSONAL[entity], tid).archived_at is not None


def test_time_project_and_budget(person, monkeypatch):
    _, uid, ws = person
    with db.SessionLocal() as s:
        p = svc.add_project(s, ws, "Sayt")
        today = svc.today_local(svc.user_tz(s.get(db.User, uid)))
    draft = capture(person, monkeypatch, plan(action(title="Dizayn", deadline=today, due_time="10:30", project_id=p.id)))
    assert "Sayt" in draft["preview"]
    core.confirm(uid, ws, draft["id"], 1)
    with db.SessionLocal() as s:
        task = s.scalar(select(db.Task).where(db.Task.workspace_id == ws))
        assert task.due_time.hour == 10 and task.project_id == p.id
    budget = capture(person, monkeypatch, plan(action("budget", "update", category="food", limit=750000)))
    core.confirm(uid, ws, budget["id"], 1)
    with db.SessionLocal() as s:
        assert svc.money_budgets(s, ws)["food"] == 750000


def test_stale_target_and_protected_habits(person, monkeypatch):
    _, uid, ws = person
    with db.SessionLocal() as s:
        task = svc.add_task(s, ws, "Old")
    draft = capture(person, monkeypatch, plan(action("task", "delete", task.id)))
    with db.SessionLocal() as s:
        svc.update_task(s, ws, task.id, title="New")
    with pytest.raises(actions.AgentError, match="stale_target"):
        core.confirm(uid, ws, draft["id"], 1)
    with db.SessionLocal() as s:
        protected = s.scalar(select(db.Habit).where(db.Habit.workspace_id == ws, db.Habit.system_key.is_not(None)))
    if protected:
        bad = capture(person, monkeypatch, plan(action("habit", "done", protected.id)))
        assert bad["status"] == "failed"


def test_timer_cannot_be_bypassed(person, monkeypatch):
    _, uid, ws = person
    with db.SessionLocal() as s:
        t = svc.add_task(s, ws, "Fokus", timer_minutes=25)
    draft = capture(person, monkeypatch, plan(action("task", "done", t.id)))
    with pytest.raises(ValueError, match="timer_required"):
        core.confirm(uid, ws, draft["id"], 1)


def test_workspace_isolation_api_and_model(person, client, monkeypatch):
    caller, uid, ws = person
    other = Caller(client, {"id": next(_next_id), "first_name": "Other"})
    draft = capture(person, monkeypatch)
    for path in ("/drafts/" + draft["id"],):
        assert other.get("/api/agent" + path).status_code == 404
    assert other.post("/api/agent/drafts/" + draft["id"] + "/confirm", {"revision": 1}).status_code in {403, 404}
    with db.SessionLocal() as s:
        ows = svc.workspace_id_for(s, other.user["id"])
        foreign = svc.add_task(s, ows, "Private")
        c = actions.catalog(s, uid, ws)
        assert all(r.get("name") != "Private" for r in c["items"])
    bad = capture(person, monkeypatch, plan(action("task", "delete", foreign.id)))
    assert bad["status"] == "failed" and bad["error"] == "not_found"


def test_team_permissions_rechecked_on_confirm(person, monkeypatch, client):
    _, uid, ws = person
    other = Caller(client, {"id": next(_next_id), "first_name": "Owner"})
    oid = other.user["id"]
    with db.SessionLocal() as s:
        team = svc.create_team(s, oid, "Team")
        s.add(db.TeamMember(team_id=team.id, user_id=uid, role="member"))
        s.commit()
        tid = svc.add_team_task(s, oid, team.id, "Owner task")["id"]
    bad = capture(person, monkeypatch, plan(action("task", "delete", tid, scope="team", team_id=team.id)))
    assert bad["status"] == "failed" and bad["error"] == "forbidden"
    draft = capture(person, monkeypatch, plan(action(scope="team", team_id=team.id, title="Shared")))
    assert draft["status"] == "ready"
    with db.SessionLocal() as s:
        membership = s.scalar(select(db.TeamMember).where(db.TeamMember.team_id == team.id, db.TeamMember.user_id == uid))
        s.delete(membership)
        s.commit()
    with pytest.raises(PermissionError):
        core.confirm(uid, ws, draft["id"], 1)


def test_consent_and_limits(person, monkeypatch):
    _, uid, ws = person
    core.consent(ws, False)
    with pytest.raises(actions.AgentError, match="consent_required"):
        capture(person, monkeypatch)
    core.consent(ws, True)
    monkeypatch.setattr(config, "AGENT_DAILY_REQUESTS", 1)
    assert capture(person, monkeypatch)["status"] == "ready"
    limited = capture(person, monkeypatch)
    assert limited["status"] == "failed" and limited["error"] == "daily_limit"
    assert len(core.list_drafts(ws)) == 2


def test_audio_transcript_then_confirmation(person, monkeypatch):
    _, uid, ws = person
    monkeypatch.setattr(provider, "transcribe", AsyncMock(return_value="Har kuni kitob o‘qish odati"))
    monkeypatch.setattr(provider, "plan", AsyncMock(return_value=plan(action("habit", name="Kitob o‘qish"))))
    old_count = count(db.Habit, ws)
    draft = asyncio.run(core.ingest(uid, ws, uuid.uuid4().hex, audio=b"fake-audio", mime="audio/ogg"))
    assert draft["transcript"] == "Har kuni kitob o‘qish odati"
    assert count(db.Habit, ws) == old_count
    core.confirm(uid, ws, draft["id"], 1)
    assert count(db.Habit, ws) == old_count + 1


def test_audio_correction_invalidates_old_confirmation(person, monkeypatch):
    _, uid, ws = person
    old = capture(person, monkeypatch)
    monkeypatch.setattr(provider, "transcribe", AsyncMock(return_value="Nomi Ruscha bo‘lsin"))
    monkeypatch.setattr(provider, "plan", AsyncMock(return_value=plan(action(title="Отчёт"))))
    draft = asyncio.run(core.ingest(uid, ws, uuid.uuid4().hex, audio=b"fake", mime="audio/ogg", draft_id=old["id"], revision=1))
    with pytest.raises(actions.AgentError, match="stale_draft"):
        core.confirm(uid, ws, old["id"], 1)
    core.confirm(uid, ws, draft["id"], 2)


def test_restart_lease_and_expired_confirmation(person, monkeypatch):
    _, uid, ws = person
    draft = capture(person, monkeypatch)
    with db.SessionLocal() as s:
        r = s.get(db.AgentDraft, draft["id"])
        r.updated_at = db.utcnow() - timedelta(days=2)
        s.commit()
    with pytest.raises(actions.AgentError, match="draft_expired"):
        core.confirm(uid, ws, draft["id"], 1)
    with db.SessionLocal() as s:
        r = s.get(db.AgentDraft, draft["id"])
        r.status = "processing"
        s.commit()
    assert core.get_draft(ws, draft["id"])["error"] == "processing_interrupted"
    retried = capture(person, monkeypatch, draft_id=draft["id"], revision=1)
    assert retried["status"] == "ready" and retried["revision"] == 2


def test_api_routes_auth_body_and_confirmation(person, client, monkeypatch):
    caller, _, ws = person
    monkeypatch.setattr(provider, "plan", AsyncMock(return_value=plan(action(title="API task"))))
    assert client.get("/api/agent/inbox").status_code == 401
    r = caller.post("/api/agent/text", {"request_key": uuid.uuid4().hex, "text": "Task"})
    assert r.status_code == 200, r.text
    draft = r.json()
    assert count(db.Task, ws) == 0
    assert caller.post(f'/api/agent/drafts/{draft["id"]}/confirm', {"revision": 1}).json()["status"] == "executed"
    assert caller.post("/api/agent/text", {"request_key": uuid.uuid4().hex, "text": "Task", "workspace_id": ws}).status_code == 422
    assert caller.post("/api/agent/text", {"request_key": uuid.uuid4().hex, "text": "Task", "draft_id": draft["id"]}).status_code == 422


def test_model_cannot_choose_identity_or_arbitrary_code():
    bad = plan(action(title="safe"))
    bad["actions"][0]["workspace_id"] = 123
    with pytest.raises(ValueError):
        actions.Plan.model_validate(bad)
    bad = plan(action(title="safe"))
    bad["actions"][0]["entity"] = "sql"
    with pytest.raises(ValueError):
        actions.Plan.model_validate(bad)


@pytest.mark.parametrize("spec", [action("money", amount=-10, kind="expense"), action("money", amount=10, kind="income", category="food"), action("task", deadline="yesterday", title="Bad"), action("habit", name="Bad", schedule="sometimes"), action("task", operation="delete"), action("money", amount=50), action("budget", "create", category="food", limit=100)])
def test_reject_invalid_plans(person, monkeypatch, spec):
    draft = capture(person, monkeypatch, plan(spec))
    assert draft["status"] == "failed"


def test_export_wipe_and_private_history_deletion(person, monkeypatch):
    _, uid, ws = person
    draft = capture(person, monkeypatch)
    core.confirm(uid, ws, draft["id"], 1)
    with db.SessionLocal() as s:
        exported = svc.export_workspace(s, ws, s.get(db.User, uid))
        assert exported["agent_inbox"][0]["transcript"]
        assert exported["agent_history"]
    core.forget_history(ws)
    assert count(db.AgentDraft, ws) == count(db.AgentAudit, ws) == 0
    assert count(db.Task, ws) == 1
    assert not core.preferences(ws)["consent"]
    with db.SessionLocal() as s:
        svc.wipe_workspace(s, uid)
        assert s.get(db.AgentPreference, ws) is None


def test_provider_wire_uses_strict_schema_and_no_tools(monkeypatch):
    called = []
    async def fake(path, **kwargs):
        called.append((path, kwargs))
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(plan(action(title="Task")))}}]}
    monkeypatch.setattr(provider, "request", fake)
    context = {"items": [], "teams": [], "truncated": False, "today": "2026-09-30", "timezone": "Asia/Tashkent", "language": "uz"}
    p = asyncio.run(provider.plan("Task", context, []))
    assert p.actions[0].entity == "task"
    payload = called[0][1]["json"]
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in payload
    assert "fake-key" not in json.dumps(payload)


def test_no_paid_fallback(monkeypatch):
    monkeypatch.setattr(config, "AGENT_PROVIDER", "openai")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "also-fake")
    with pytest.raises(actions.AgentError):
        provider.connection()


def test_audio_validation_bounds_and_decoder(monkeypatch):
    with pytest.raises(actions.AgentError, match="invalid_audio"):
        provider.audio_wav(b"https://example.test/audio", "audio/ogg")
    with pytest.raises(actions.AgentError, match="invalid_audio"):
        provider.audio_wav(b"OggS", "text/plain")
    monkeypatch.setattr(provider.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=b"\0" * 32000))
    assert provider.audio_wav(b"OggSfake", "audio/ogg").startswith(b"RIFF")
    monkeypatch.setattr(provider.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=b"\0" * (32000 * 121)))
    with pytest.raises(actions.AgentError, match="audio_too_long"):
        provider.audio_wav(b"OggSfake", "audio/ogg")


def test_chunked_audio_limit_not_just_content_length(person, client, monkeypatch):
    caller, _, _ = person
    monkeypatch.setattr(config, "AGENT_AUDIO_BYTES", 5)
    def chunks():
        yield b"OggS"
        yield b"123456789"
    r = client.post("/api/agent/audio", headers={**caller.h, "Content-Type": "audio/ogg", "X-Agent-Request-Key": uuid.uuid4().hex}, content=chunks())
    assert r.status_code == 413


@pytest.mark.parametrize("ui_lang,detected,text,label", [
    ("uz", "uz", "Ertaga hisobot", "Qo‘shish"),
    ("ru", "ru", "Завтра отчёт", "Добавить"),
    ("en", "en", "Tomorrow report", "Create"),
    ("uz", "ru", "Завтра отчёт", "Qo‘shish"),
    ("en", "uz", "Эртага ҳисобот", "Create"),
    ("uz", "mixed", "ertaga abdulbosid bilan soat 10 am da meetingim bor. bunda hamma problemalarni hal qilamiz.", "Qo‘shish"),
])
def test_language_is_checked_before_actions_independent_of_ui(person, monkeypatch, ui_lang, detected, text, label):
    _, uid, ws = person
    with db.SessionLocal() as s:
        s.get(db.User, uid).language = ui_lang
        s.commit()
    draft = capture(person, monkeypatch, plan(action(title="Report", due_time="10:00"), language=detected), text=text)
    assert draft["language"] == detected
    assert label in draft["preview"]
    assert count(db.Task, ws) == 0


def test_unknown_language_cannot_execute_even_if_model_proposes_action(person, monkeypatch):
    _, uid, ws = person
    draft = capture(person, monkeypatch, plan(action(title="Unsafe guess"), language="unknown"))
    assert draft["status"] == "needs_input"
    with pytest.raises(actions.AgentError):
        core.confirm(uid, ws, draft["id"], 1)


def test_all_agent_interface_strings_cover_three_languages():
    from agent_text import TEXT
    assert set(TEXT) == {"uz", "ru", "en"}
    assert set(TEXT["uz"]) == set(TEXT["ru"]) == set(TEXT["en"])
    assert all(isinstance(value, str) and value.strip() for rows in TEXT.values() for value in rows.values())


def test_model_response_arriving_after_cancel_cannot_restore_ready(person, monkeypatch):
    _, uid, ws = person
    draft, _ = core.capture(uid, ws, uuid.uuid4().hex, text="Task")
    async def late(*args):
        core.cancel(ws, draft["id"], 1)
        return plan(action(title="Too late"))
    monkeypatch.setattr(provider, "plan", late)
    result = asyncio.run(core.process(uid, ws, draft["id"], 1))
    assert result["status"] == "cancelled"
    assert count(db.Task, ws) == 0
