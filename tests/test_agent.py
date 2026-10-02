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


def plan(*items, question=None, language="uz", understood=None):
    return {"language": language, "understood": understood, "question": question, "actions": list(items)}


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
    assert draft["preview"] == "📝 Yangi vazifa\n<b>Hisobot tayyorlash</b>"  # defaults hidden
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
    assert len(calls) == 3  # 4000+ chars of preview, split safely
    assert all(call.kwargs["reply_markup"] is None for call in calls[:-1])
    assert draft["transcript"] not in "".join(call.args[0] for call in calls)
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
def test_create_update_delete(person, monkeypatch, entity):
    _, uid, ws = person
    draft = capture(person, monkeypatch, plan(action(entity, **({"title": "Kitob"} if entity == "task" else {"name": "Kitob"}))))
    result = core.confirm(uid, ws, draft["id"], 1)
    tid = result["result"][0]["id"]
    for op, fields in [("update", {"title" if entity == "task" else "name": "Yangi nom"}), ("delete", {})]:
        draft = capture(person, monkeypatch, plan(action(entity, op, tid, **fields)))
        assert draft["status"] == "ready", draft
        assert core.confirm(uid, ws, draft["id"], 1)["status"] == "executed"
    with db.SessionLocal() as s:
        assert s.get(actions.PERSONAL[entity], tid).archived_at is not None


def test_time_and_project(person, monkeypatch):
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
        bad = capture(person, monkeypatch, plan(action("habit", "delete", protected.id)))
        assert bad["status"] == "failed"


def test_workspace_isolation(person, client, monkeypatch):
    _, uid, ws = person
    other = Caller(client, {"id": next(_next_id), "first_name": "Other"})
    draft = capture(person, monkeypatch)
    with db.SessionLocal() as s:
        ows = svc.workspace_id_for(s, other.user["id"])
    core.consent(ows, True)
    with pytest.raises(actions.AgentError, match="not_found"):
        core.confirm(other.user["id"], ows, draft["id"], 1)
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


def test_model_cannot_choose_identity_or_arbitrary_code():
    bad = plan(action(title="safe"))
    bad["actions"][0]["workspace_id"] = 123
    with pytest.raises(ValueError):
        actions.Plan.model_validate(bad)
    bad = plan(action(title="safe"))
    bad["actions"][0]["entity"] = "sql"
    with pytest.raises(ValueError):
        actions.Plan.model_validate(bad)


@pytest.mark.parametrize("spec", [action("money", amount=-10, kind="expense"), action("money", amount=10, kind="income", category="food"), action("task", deadline="yesterday", title="Bad"), action("habit", name="Bad", schedule="sometimes"), action("task", operation="delete"), action("money", amount=50), action("budget", "create", category="food", limit=100), action("task", "done", 1), action("habit", "reopen", 1)])
def test_reject_invalid_plans(person, monkeypatch, spec):
    draft = capture(person, monkeypatch, plan(spec))
    assert draft["status"] == "failed"


def test_export_and_wipe(person, monkeypatch):
    _, uid, ws = person
    draft = capture(person, monkeypatch)
    core.confirm(uid, ws, draft["id"], 1)
    with db.SessionLocal() as s:
        exported = svc.export_workspace(s, ws, s.get(db.User, uid))
        assert exported["agent_inbox"][0]["transcript"]
        assert exported["agent_history"]
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


def test_groq_key_required(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    with pytest.raises(actions.AgentError, match="provider_not_configured"):
        provider.connection()
    assert not provider.configured()


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


@pytest.mark.parametrize("ui_lang,text,label", [
    ("uz", "Ertaga hisobot", "📝 Yangi vazifa"),
    ("uz", "Эртага ҳисобот", "📝 Yangi vazifa"),
    ("uz", "ertaga abdulbosid bilan soat 10 am da meetingim bor. bunda hamma problemalarni hal qilamiz.", "📝 Yangi vazifa"),
    ("ru", "Завтра отчёт", "📝 Новая задача"),
    ("en", "Tomorrow report", "📝 New task"),
])
def test_profile_language_is_accepted(person, monkeypatch, ui_lang, text, label):
    _, uid, ws = person
    with db.SessionLocal() as s:
        s.get(db.User, uid).language = ui_lang
        s.commit()
    draft = capture(person, monkeypatch, plan(action(title="Report", due_time="10:00"), language=ui_lang), text=text)
    assert draft["status"] == "ready" and label in draft["preview"]
    assert count(db.Task, ws) == 0


@pytest.mark.parametrize("ui_lang,detected,message", [
    ("uz", "ru", "o‘zbek tilida"),
    ("uz", "en", "o‘zbek tilida"),
    ("uz", "other", "o‘zbek tilida"),
    ("ru", "uz", "по-русски"),
    ("en", "ru", "speak English"),
])
def test_other_language_is_refused_in_profile_language(person, monkeypatch, ui_lang, detected, message):
    _, uid, ws = person
    with db.SessionLocal() as s:
        s.get(db.User, uid).language = ui_lang
        s.commit()
    draft = capture(person, monkeypatch, plan(action(title="Unsafe"), language=detected))
    assert draft["status"] == "needs_input" and message in draft["preview"]
    with pytest.raises(actions.AgentError):
        core.confirm(uid, ws, draft["id"], 1)
    assert count(db.Task, ws) == 0


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


# --- Whisper and reply language ---------------------------------------------

CTX = {"items": [{"entity": "task", "scope": "personal", "id": 1, "name": "Abdulvosid bilan uchrashuv"}],
       "teams": [{"id": 9, "name": "Savdo jamoasi"}], "truncated": False,
       "today": "2026-09-30", "timezone": "Asia/Tashkent", "language": "uz"}


@pytest.mark.parametrize("lang", ["uz", "ru", "en"])
def test_whisper_uses_profile_language_and_user_names(monkeypatch, lang):
    sent = []
    async def fake(path, **kwargs):
        sent.append((path, kwargs))
        return {"text": " matn "}
    monkeypatch.setattr(provider, "request", fake)
    monkeypatch.setattr(provider, "audio_wav", lambda data, mime: b"RIFFwav")
    assert asyncio.run(provider.transcribe(b"OggS", "audio/ogg", {**CTX, "language": lang})) == "matn"
    path, kwargs = sent[0]
    assert path == "/audio/transcriptions"
    form = kwargs["data"]
    assert form["model"] == "whisper-large-v3" and form["language"] == lang and form["temperature"] == "0"
    assert "Abdulvosid bilan uchrashuv" in form["prompt"] and "Savdo jamoasi" in form["prompt"]
    assert len(form["prompt"]) <= 600


@pytest.mark.parametrize("ui_lang,question,expected", [
    ("uz", "Какую задачу добавить?", "Tushunmadim"),
    ("uz", "Qaysi loyihaga qo‘shay?", "Qaysi loyihaga qo‘shay?"),
    ("ru", "Qaysi loyihaga qo‘shay?", "Не понял"),
    ("en", "Which project?", "Which project?"),
])
def test_questions_always_follow_profile_language(person, monkeypatch, ui_lang, question, expected):
    _, uid, ws = person
    with db.SessionLocal() as s:
        s.get(db.User, uid).language = ui_lang
        s.commit()
    draft = capture(person, monkeypatch, plan(question=question, language=ui_lang))
    assert draft["status"] == "needs_input"
    assert draft["preview"].startswith(expected)


def test_bot_buttons_are_confirm_edit_cancel_only():
    from agent_bot import AgentBot
    message = SimpleNamespace(reply_text=AsyncMock())
    draft = {"id": "b" * 32, "revision": 1, "status": "ready", "transcript": "Ovqatga 5 ming",
             "preview": "1. Qo‘shish · Pul yozuvi", "error": None}
    asyncio.run(AgentBot(None).show(message, draft, "uz"))
    rows = message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
    assert [b.callback_data.split(":")[1] for row in rows for b in row] == ["c", "e", "x"]
    message.reply_text.reset_mock()
    asyncio.run(AgentBot(None).show(message, {**draft, "status": "executed"}, "uz"))
    assert message.reply_text.await_args.kwargs["reply_markup"] is None


def test_group_refusal_uses_saved_app_language(person):
    from agent_bot import AgentBot
    from agent_text import TEXT
    _, uid, _ = person
    with db.SessionLocal() as s:
        s.get(db.User, uid).language = "ru"
        s.commit()
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_chat=SimpleNamespace(type="group"), effective_message=message,
                             effective_user=SimpleNamespace(id=uid))
    asyncio.run(AgentBot(AsyncMock()).voice(update, SimpleNamespace(user_data={})))
    assert message.reply_text.await_args.args[0] == TEXT["ru"]["private"]


def test_short_preview_hides_defaults_and_uses_today(person, monkeypatch):
    _, uid, ws = person
    with db.SessionLocal() as s:
        today = svc.today_local(svc.user_tz(s.get(db.User, uid)))
    money = capture(person, monkeypatch, plan(action("money", amount=5000, kind="expense", category="food")))
    assert money["preview"] == "💸 Chiqim\n<b>5 000 so‘m</b>\n🍔 Oziq-ovqat"
    task = capture(person, monkeypatch, plan(action(title="Ustun bilan uchrashuv", deadline=today, due_time="08:00"),
                                             understood="Soat 8 da Ustun bilan uchrashuvim bor"))
    assert task["preview"] == ("🎙 <i>Soat 8 da Ustun bilan uchrashuvim bor</i>\n\n📝 Yangi vazifa\n"
                               f"<b>Ustun bilan uchrashuv</b>\n📅 {actions.human_day(today, 'uz', today)}   ⏰ 08:00")
    core.confirm(uid, ws, money["id"], 1)
    with db.SessionLocal() as s:
        assert s.scalar(select(db.MoneyEntry).where(db.MoneyEntry.workspace_id == ws)).day == today


def test_ready_message_is_preview_and_buttons_only():
    from agent_bot import AgentBot
    message = SimpleNamespace(reply_text=AsyncMock())
    draft = {"id": "c" * 32, "revision": 1, "status": "ready", "transcript": "Beş min so‘m ovqat",
             "preview": "➕ Chiqim: 5 000 so‘m · Oziq-ovqat", "error": None}
    asyncio.run(AgentBot(None).show(message, draft, "uz"))
    assert message.reply_text.await_args.args[0] == draft["preview"]


@pytest.mark.parametrize("path,first,second", [
    ("/chat/completions", "openai/gpt-oss-120b", "openai/gpt-oss-20b"),
    ("/audio/transcriptions", "whisper-large-v3", "whisper-large-v3-turbo"),
])
def test_free_limit_moves_to_the_next_groq_model(monkeypatch, path, first, second):
    used = []
    async def fake(p, **kwargs):
        model = kwargs["json"]["model"] if "json" in kwargs else kwargs["data"]["model"]
        used.append(model)
        if model == first:
            raise actions.AgentError("provider_limit", 429)
        if "json" in kwargs:
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(plan(action(title="T")))}}]}
        return {"text": "matn"}
    monkeypatch.setattr(provider, "request", fake)
    monkeypatch.setattr(provider, "audio_wav", lambda data, mime: b"RIFFwav")
    if path == "/chat/completions":
        asyncio.run(provider.plan("T", CTX, []))
    else:
        asyncio.run(provider.transcribe(b"OggS", "audio/ogg", CTX))
    assert used == [first, second]


def test_both_groq_models_limited_reports_limit(monkeypatch):
    async def fake(p, **kwargs):
        raise actions.AgentError("provider_limit", 429)
    monkeypatch.setattr(provider, "request", fake)
    with pytest.raises(actions.AgentError, match="provider_limit"):
        asyncio.run(provider.plan("T", CTX, []))


# --- Launch voice stack: ElevenLabs first, Gemini as paid overflow -------------

def test_elevenlabs_scribe_is_first_with_language_and_user_names(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "fake-11labs")
    sent = []
    async def fake(path, **kwargs):
        sent.append((path, kwargs["service"], kwargs["data"]))
        return {"text": " Ovqatga besh ming so‘m "}
    monkeypatch.setattr(provider, "request", fake)
    monkeypatch.setattr(provider, "audio_wav", lambda data, mime: b"RIFFwav")
    assert asyncio.run(provider.transcribe(b"OggS", "audio/ogg", CTX)) == "Ovqatga besh ming so‘m"
    path, service, form = sent[0]
    assert (path, service) == ("/speech-to-text", "elevenlabs") and len(sent) == 1
    assert form["model_id"] == "scribe_v2" and form["language_code"] == "uz"
    assert form["tag_audio_events"] == "false" and form["no_verbatim"] == "true"
    assert form["keyterms"][:2] == ["Savdo jamoasi", "Abdulvosid bilan uchrashuv"], "own names first"
    assert "turnik" in form["keyterms"] and "tortilish" in form["keyterms"], "everyday words follow"


@pytest.mark.parametrize("code", ["provider_limit", "provider_unavailable"])
def test_elevenlabs_out_of_credit_falls_back_to_groq_whisper(monkeypatch, code):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "fake-11labs")
    used = []
    async def fake(path, **kwargs):
        used.append(kwargs["service"])
        if kwargs["service"] == "elevenlabs":
            raise actions.AgentError(code, 429)
        return {"text": "matn"}
    monkeypatch.setattr(provider, "request", fake)
    monkeypatch.setattr(provider, "audio_wav", lambda data, mime: b"RIFFwav")
    assert asyncio.run(provider.transcribe(b"OggS", "audio/ogg", CTX)) == "matn"
    assert used == ["elevenlabs", "groq"]


def test_keyterms_respect_elevenlabs_limits():
    items = [{"name": f"Mijoz {i}"} for i in range(150)] + [{"name": "x" * 51}, {"name": "bir ikki uch tort besh olti"}]
    terms = provider.keyterms({"items": items, "teams": []})
    assert len(terms) == 100 and all(len(t) <= 50 and len(t.split()) <= 5 for t in terms)


def test_groq_limit_overflows_to_gemini_with_inlined_schema(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-gemini")
    used = []
    async def fake(path, **kwargs):
        used.append(kwargs["service"])
        if kwargs["service"] == "groq":
            raise actions.AgentError("provider_limit", 429)
        assert "$ref" not in json.dumps(kwargs["json"]["generationConfig"]["responseJsonSchema"])
        body = json.dumps(plan(action(title="Hisobot")))
        return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": body}]}}]}
    monkeypatch.setattr(provider, "request", fake)
    assert asyncio.run(provider.plan("Hisobot", CTX, [])).actions[0].entity == "task"
    assert used == ["groq", "gemini"]


def test_a_bad_answer_is_not_retried_on_another_provider(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-gemini")
    used = []
    async def fake(path, **kwargs):
        used.append(kwargs["service"])
        return {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
    monkeypatch.setattr(provider, "request", fake)
    with pytest.raises(actions.AgentError, match="invalid_plan"):
        asyncio.run(provider.plan("Hisobot", CTX, []))
    assert used == ["groq"]


def test_no_keys_means_not_configured(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    with pytest.raises(actions.AgentError, match="provider_not_configured"):
        asyncio.run(provider.plan("Hisobot", CTX, []))


class _Q:
    def __init__(self, data, message):
        self.data, self.message, self.edits = data, message, []

    async def edit_message_text(self, text, reply_markup=None, **kw):
        self.edits.append((text, reply_markup))

    async def edit_message_reply_markup(self, reply_markup=None, **kw):
        self.edits.append((None, reply_markup))


def _tg(uid, *, text=None, data=None):
    message = SimpleNamespace(reply_text=AsyncMock(), text=text, message_id=next(_next_id), voice=None, audio=None)
    chat = SimpleNamespace(id=uid, type="private", send_action=AsyncMock())
    update = SimpleNamespace(effective_chat=chat, effective_message=message, effective_user=SimpleNamespace(id=uid),
                             callback_query=_Q(data, message) if data else None)
    return update, message


def test_first_message_runs_right_after_consent(person, monkeypatch):
    from agent_bot import AgentBot
    _, uid, ws = person
    core.consent(ws, False)
    with db.SessionLocal() as s:
        user = s.get(db.User, uid)
    bot = AgentBot(AsyncMock(return_value=(user, ws)))
    ctx = SimpleNamespace(user_data={}, bot=SimpleNamespace())
    monkeypatch.setattr(provider, "plan", AsyncMock(return_value=plan(action("money", amount=5000, kind="expense", category="food"))))
    update, message = _tg(uid, text="Ovqatga 5 ming")
    asyncio.run(bot.text(update, ctx))
    provider.plan.assert_not_awaited()  # no consent yet: nothing sent to AI
    update, message = _tg(uid, data="ag:consent")
    asyncio.run(bot.callback(update, ctx))
    assert message.reply_text.await_args.args[0] == "💸 Chiqim\n<b>5 000 so‘m</b>\n🍔 Oziq-ovqat"
    assert "agent_pending" not in ctx.user_data


def test_confirm_edits_the_same_message_and_drops_buttons(person, monkeypatch):
    from agent_bot import AgentBot
    _, uid, ws = person
    with db.SessionLocal() as s:
        user = s.get(db.User, uid)
    draft = capture(person, monkeypatch, plan(action(title="Hisobot")))
    bot = AgentBot(AsyncMock(return_value=(user, ws)))
    update, message = _tg(uid, data=f'ag:c:{draft["id"]}:1')
    asyncio.run(bot.callback(update, SimpleNamespace(user_data={})))
    text, markup = update.callback_query.edits[-1]
    assert text.endswith("<b>✅ Tasdiqlandi</b>") and markup is None
    message.reply_text.assert_not_awaited()


def test_log_says_which_provider_answered_never_the_words(monkeypatch, caplog):
    import logging
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "fake-11labs")
    async def fake(path, **kwargs):
        return {"text": "Maxfiy gap"}
    monkeypatch.setattr(provider, "request", fake)
    monkeypatch.setattr(provider, "audio_wav", lambda data, mime: b"RIFFwav")
    with caplog.at_level(logging.INFO, logger="ernestos.agent"):
        asyncio.run(provider.transcribe(b"OggS", "audio/ogg", CTX))
    assert "agent voice: elevenlabs/scribe_v2" in caplog.text
    assert "Maxfiy" not in caplog.text


def test_card_reads_like_a_person_wrote_it():
    today = "2026-10-02"
    card = actions.preview([
        {"entity": "task", "operation": "create", "scope": "personal", "name": "x",
         "fields": {"title": "Yotoqxonaga <kirish>", "deadline": "2026-10-08", "due_time": "09:00",
                    "priority": "high", "timer_minutes": 0, "project_id": None}},
        {"entity": "habit", "operation": "delete", "scope": "personal", "name": "Kitob o‘qish", "fields": {}},
    ], "uz", today, "Juma kuni soat 9 da yotoqxonaga kirishim kerak")
    assert card.startswith("🎙 <i>Juma kuni soat 9 da yotoqxonaga kirishim kerak</i>")
    assert "<b>Yotoqxonaga &lt;kirish&gt;</b>" in card, "names are escaped"
    assert "📅 Payshanba, 8-oktabr" in card and "⏰ 09:00" in card and "🔥 Yuqori" in card
    assert "🗑 Odat o‘chiriladi\n<b>Kitob o‘qish</b>" in card
    assert "2026-10-08" not in card and "Taymer" not in card and "Loyiha" not in card
    assert actions.human_day("2026-10-02", "ru", today) == "Сегодня, 2 октября"
    assert actions.human_day("2027-01-05", "en", today) == "Jan 5 2027"


def test_understood_in_another_language_is_not_shown(person, monkeypatch):
    draft = capture(person, monkeypatch, plan(action(title="Hisobot"), understood="Завтра отчёт"))
    assert "Завтра" not in draft["preview"]


# --- Mini App agent API: speak/type → card → Confirm / Cancel ------------------

def test_mini_app_agent_text_card_confirm(person, client, monkeypatch):
    caller, uid, ws = person
    assert client.post("/api/agent/text", json={"text": "x", "request_key": uuid.uuid4().hex}).status_code == 401
    me = caller.get("/api/me").json()["agent"]
    assert me["available"] and me["consent"]
    monkeypatch.setattr(provider, "plan", AsyncMock(return_value=plan(action(title="Hisobot"), understood="Hisobot tayyorlash")))
    r = caller.post("/api/agent/text", {"text": "hisobot", "request_key": uuid.uuid4().hex})
    assert r.status_code == 200, r.text
    draft = r.json()
    assert draft["status"] == "ready" and "<b>Hisobot</b>" in draft["preview"]
    assert count(db.Task, ws) == 0, "nothing before Confirm"
    done = caller.post(f'/api/agent/drafts/{draft["id"]}/confirm', {"revision": 1}).json()
    assert done["status"] == "executed" and count(db.Task, ws) == 1
    again = caller.post(f'/api/agent/drafts/{draft["id"]}/confirm', {"revision": 1}).json()
    assert again["status"] == "executed" and count(db.Task, ws) == 1, "double tap adds once"


def test_mini_app_agent_cancel_and_consent(person, client, monkeypatch):
    caller, uid, ws = person
    core.consent(ws, False)
    blocked = caller.post("/api/agent/text", {"text": "hisobot", "request_key": uuid.uuid4().hex})
    assert blocked.status_code == 403
    assert caller.post("/api/agent/consent").json()["consent"] is True
    monkeypatch.setattr(provider, "plan", AsyncMock(return_value=plan(action(title="Hisobot"))))
    draft = caller.post("/api/agent/text", {"text": "hisobot", "request_key": uuid.uuid4().hex}).json()
    gone = caller.post(f'/api/agent/drafts/{draft["id"]}/cancel', {"revision": 1}).json()
    assert gone["status"] == "cancelled" and count(db.Task, ws) == 0


def test_mini_app_audio_is_streamed_and_bounded(person, client, monkeypatch):
    caller, _, _ = person
    monkeypatch.setattr(config, "AGENT_AUDIO_BYTES", 5)
    def chunks():
        yield b"OggS"
        yield b"123456789"
    r = client.post("/api/agent/audio", headers={**caller.h, "Content-Type": "audio/webm",
                    "X-Agent-Request-Key": uuid.uuid4().hex}, content=chunks())
    assert r.status_code == 413


def test_mini_app_audio_goes_through_the_same_pipeline(person, client, monkeypatch):
    caller, _, ws = person
    monkeypatch.setattr(provider, "transcribe", AsyncMock(return_value="ovqatga 5 ming"))
    monkeypatch.setattr(provider, "plan", AsyncMock(return_value=plan(action("money", amount=5000, kind="expense", category="food"))))
    r = client.post("/api/agent/audio", headers={**caller.h, "Content-Type": "audio/webm",
                    "X-Agent-Request-Key": uuid.uuid4().hex}, content=b"\x1aE\xdf\xa3voice")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ready" and "5 000 so‘m" in r.json()["preview"]


def journal_fill(**answers):
    return provider.JournalFill(**{k: answers.get(k) for k in svc.JOURNAL_KEYS})


def test_journal_ai_sorts_text_into_answers_and_saves_nothing(person, client, monkeypatch):
    caller, _, ws = person
    seen = {}
    async def fake(text, lang):
        seen.update(text=text, lang=lang)
        return journal_fill(wins="Mijoz bilan shartnoma yopdim.", gratitude="Onamga rahmat aytaman.",
                            problem="Ничего", lesson=None, tomorrow="Ertaga hisobotni tugataman.")
    monkeypatch.setattr(provider, "journal_answers", fake)
    r = caller.post("/api/agent/journal/text", {"text": "bugun mijoz bilan shartnoma yopdim onamga rahmat"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert seen == {"text": "bugun mijoz bilan shartnoma yopdim onamga rahmat", "lang": "uz"}
    # Wrong script is dropped, nothing invented for the empty question.
    assert body["answers"] == {"wins": "Mijoz bilan shartnoma yopdim.", "gratitude": "Onamga rahmat aytaman.",
                               "tomorrow": "Ertaga hisobotni tugataman."}
    assert body["filled"] == 3
    assert count(db.JournalEntry, ws) == 0, "the person reviews and saves"


def test_journal_ai_needs_consent_and_counts_the_budget(person, monkeypatch):
    caller, _, ws = person
    monkeypatch.setattr(provider, "journal_answers", AsyncMock(return_value=journal_fill(wins="Yugurdim.")))
    monkeypatch.setattr(config, "AGENT_DAILY_REQUESTS", 1)
    assert caller.post("/api/agent/journal/text", {"text": "yugurdim"}).status_code == 200
    assert caller.post("/api/agent/journal/text", {"text": "yugurdim"}).status_code == 429
    core.consent(ws, False)
    assert caller.post("/api/agent/journal/text", {"text": "yugurdim"}).status_code == 403


def test_journal_ai_voice_transcribes_in_the_profile_language(person, client, monkeypatch):
    caller, _, _ = person
    heard = {}
    async def transcribe(data, mime, context):
        heard.update(context)
        return "bugun 5 km yugurdim"
    monkeypatch.setattr(provider, "transcribe", transcribe)
    monkeypatch.setattr(provider, "journal_answers", AsyncMock(return_value=journal_fill(wins="Bugun 5 km yugurdim.")))
    r = client.post("/api/agent/journal/audio", headers={**caller.h, "Content-Type": "audio/webm"},
                    content=b"\x1aE\xdf\xa3voice")
    assert r.status_code == 200, r.text
    assert r.json()["answers"] == {"wins": "Bugun 5 km yugurdim."}
    assert heard["language"] == "uz"


def test_journal_ai_wire_is_strict_json_with_the_five_questions(monkeypatch):
    sent = {}
    async def fake_request(path, service="groq", **kwargs):
        sent.update(kwargs["json"])
        content = json.dumps({"wins": "A", "gratitude": None, "problem": None, "lesson": None, "tomorrow": "B"})
        return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
    monkeypatch.setattr(provider, "request", fake_request)
    filled = asyncio.run(provider.journal_answers("salom", "uz"))
    assert filled.wins == "A" and filled.tomorrow == "B" and filled.lesson is None
    schema = sent["response_format"]["json_schema"]
    assert schema["strict"] and set(schema["schema"]["required"]) == set(svc.JOURNAL_KEYS)
    assert "Never invent" in sent["messages"][0]["content"]
    assert json.loads(sent["messages"][1]["content"]) == {"language": "uz", "text": "salom"}


def test_agent_records_a_debt_not_an_expense(person, monkeypatch):
    caller, uid, ws = person
    debt = action("debt", person="Aziz", amount=200000, direction="lent", deadline="2026-10-09")
    draft = capture(person, monkeypatch, plan(debt), text="Azizga 200 ming qarz berdim")
    assert draft["status"] == "ready", draft
    assert "🤝 Qarz berdingiz" in draft["preview"] and "<b>Aziz</b> — 200 000 so‘m" in draft["preview"]
    core.confirm(uid, ws, draft["id"], draft["revision"])
    d = caller.get("/api/debts").json()
    assert d["owed_to_me"] == 200000 and d["open"][0]["due"] == "2026-10-09"
    assert count(db.MoneyEntry, ws) == 0


def test_agent_cannot_edit_or_share_a_debt(person, monkeypatch):
    for bad in (action("debt", operation="update", target_id=1, amount=5),
                action("debt", scope="team", team_id=1, person="A", amount=5, direction="lent"),
                action("debt", amount=5, direction="lent")):
        draft = capture(person, monkeypatch, plan(bad))
        assert draft["status"] == "failed", bad
