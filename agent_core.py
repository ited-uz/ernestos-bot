"""Draft lifecycle for the Telegram agent: capture -> propose -> confirm/edit/cancel.

No network calls inside transactions. Nothing executes without the button.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import timedelta
from html import escape

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import config
import db
import services as svc
from agent_actions import AgentError, AtomicSession, Plan, actor, catalog, dumps, execute, prepare, preview
from agent_text import tr

log = logging.getLogger("ernestos.agent")
EDITABLE = {"inbox", "ready", "needs_input", "failed"}
LEASE = timedelta(minutes=4)
EXPIRY = timedelta(hours=24)


def _audit(s, row, event, detail=None, key=None):
    s.add(db.AgentAudit(workspace_id=row.workspace_id, draft_id=row.id,
                       revision=row.revision, event=event, detail=dumps(detail or {}), request_key=key))


def owned(s, ws, draft_id):
    row = s.get(db.AgentDraft, draft_id)
    if row is None or row.workspace_id != ws:
        raise AgentError("not_found", 404)
    return row


def public(row):
    status, error = row.status, row.error
    if status == "processing" and row.updated_at < db.utcnow() - LEASE:
        status, error = "failed", "processing_interrupted"
    return {"id": row.id, "status": status, "revision": row.revision,
            "transcript": row.transcript, "language": row.detected_language, "preview": row.preview,
            "editable": _editable(row) if status == "ready" else [],
            "error": error, "result": json.loads(row.result),
            "created_at": row.created_at.isoformat() + "Z",
            "updated_at": row.updated_at.isoformat() + "Z"}


#: Fields the app can change by hand on a proposal, without asking the model
#: again — fixing the hour must not re-guess the rest (audit #29).
EDITABLE_FIELDS = ("title", "name", "deadline", "due_time", "remind_at", "priority",
                   "amount", "note", "person", "day")


def _editable(row):
    try:
        actions = json.loads(row.plan or "{}").get("actions") or []
    except ValueError:
        return []
    out = []
    for i, a in enumerate(actions):
        if a.get("operation") == "delete":
            continue
        fields = {k: v for k, v in (a.get("fields") or {}).items() if k in EDITABLE_FIELDS}
        if a.get("operation") == "create" and a.get("entity") == "task":
            fields.setdefault("deadline", None)
            fields.setdefault("due_time", None)
        if fields:
            out.append({"index": i, "entity": a["entity"], "operation": a["operation"],
                        "name": a.get("name"), "fields": fields})
    return out


def edit_field(uid, ws, draft_id, revision, index, field, value):
    """Change one field of a ready proposal by hand.

    The whole plan is validated again exactly as the model's plan was, the
    card is redrawn, and the revision moves on — so an older AI answer (or a
    second tab) can no longer overwrite this edit.
    """
    if field not in EDITABLE_FIELDS:
        raise AgentError("invalid_fields", 422)
    with db.SessionLocal() as s:
        row = owned(s, ws, draft_id)
        if row.revision != revision or row.status != "ready":
            raise AgentError("stale_draft")
        proposed = json.loads(row.plan or "{}")
        actions = proposed.get("actions") or []
        if not 0 <= index < len(actions):
            raise AgentError("invalid_action", 422)
        lang = s.get(db.User, uid).language or "uz"
        specs = []
        for i, a in enumerate(actions):
            fields = dict(a.get("fields") or {})
            if i == index:
                fields[field] = value
            specs.append({"entity": a["entity"], "operation": a["operation"], "scope": a["scope"],
                          "team_id": a.get("team_id"), "target_id": a.get("target_id"),
                          "changes": [{"field": k, "value": None if v is None else str(v)}
                                      for k, v in fields.items()]})
        try:
            plan = Plan.model_validate({"language": lang, "understood": None, "question": None,
                                        "actions": specs})
            prepared = prepare(s, uid, ws, plan)
        except (ValidationError, ValueError, TypeError):
            raise AgentError("invalid_fields", 422) from None
        summary = preview(prepared, lang, proposed.get("planned_day"))
        won = s.execute(update(db.AgentDraft).where(
            db.AgentDraft.id == draft_id, db.AgentDraft.workspace_id == ws,
            db.AgentDraft.revision == revision, db.AgentDraft.status == "ready")
            .values(revision=revision + 1, plan=dumps({**proposed, "actions": prepared}),
                    preview=summary, updated_at=db.utcnow())
            .execution_options(synchronize_session=False)).rowcount
        if not won:
            raise AgentError("stale_draft")
        s.refresh(row)
        _audit(s, row, "edited", {"index": index, "field": field})
        s.commit()
        return public(row)


def get_draft(ws, draft_id):
    with db.SessionLocal() as s:
        return public(owned(s, ws, draft_id))


def preferences(ws):
    from agent_provider import configured
    with db.SessionLocal() as s:
        p = s.get(db.AgentPreference, ws)
        return {"enabled": config.AGENT_ENABLED, "configured": configured(),
                "consent": bool(p and p.consent_at)}


def consent(ws, accepted):
    with db.SessionLocal() as s:
        # UPSERT through a savepoint, safe for two devices accepting together.
        if not s.get(db.AgentPreference, ws):
            try:
                with s.begin_nested():
                    s.add(db.AgentPreference(workspace_id=ws))
                    s.flush()
            except IntegrityError:
                pass
        s.execute(update(db.AgentPreference).where(db.AgentPreference.workspace_id == ws)
                  .values(consent_at=db.utcnow() if accepted else None,
                          edit_draft_id=None, edit_revision=None, edit_until=None))
        s.commit()
    return preferences(ws)


def require_consent(s, ws):
    p = s.get(db.AgentPreference, ws)
    if not config.AGENT_ENABLED:
        raise AgentError("agent_disabled", 503)
    if not p or not p.consent_at:
        raise AgentError("consent_required", 403)
    return p


def _budget(s, uid, ws):
    p = require_consent(s, ws)
    today = svc.today_local(svc.user_tz(s.get(db.User, uid)))
    # Lock/update the preference row BEFORE reading the counter. Works with
    # SQLite's writer lock as well as PostgreSQL's row locks, across workers.
    s.execute(update(db.AgentPreference).where(db.AgentPreference.workspace_id == ws)
              .values(usage_count=db.AgentPreference.usage_count))
    s.refresh(p)
    if p.usage_day != today:
        p.usage_day, p.usage_count = today, 0
    if p.usage_count >= config.AGENT_DAILY_REQUESTS:
        raise AgentError("daily_limit", 429)
    p.usage_count += 1


def editing(ws, draft_id=None, revision=None, *, clear=False):
    with db.SessionLocal() as s:
        p = s.get(db.AgentPreference, ws)
        if p is None:
            return None
        if clear:
            p.edit_draft_id = p.edit_revision = p.edit_until = None
            s.commit()
            return None
        if draft_id:
            row = owned(s, ws, draft_id)
            if row.revision != revision or row.status not in EDITABLE:
                raise AgentError("stale_draft")
            p.edit_draft_id, p.edit_revision, p.edit_until = draft_id, revision, db.utcnow() + timedelta(minutes=15)
            s.commit()
        if p.edit_draft_id and p.edit_until and p.edit_until > db.utcnow():
            return p.edit_draft_id, p.edit_revision
        return None


def capture(uid, ws, key, *, text="", source="text", draft_id=None, revision=None, digest=None):
    """First persist the input, then ask the model. No task/money changes here."""
    if not re.fullmatch(r"[A-Za-z0-9_:-]{8,80}", key):
        raise AgentError("invalid_request_key", 422)
    text = text.strip()
    if len(text) > 6000 or (not text and source not in {"voice", "audio"}):
        raise AgentError("invalid_text", 422)
    digest = digest or hashlib.sha256(text.encode()).hexdigest()
    with db.SessionLocal() as s:
        actor(s, uid, ws)
        require_consent(s, ws)
        receipt = s.scalar(select(db.AgentAudit).where(db.AgentAudit.workspace_id == ws, db.AgentAudit.request_key == key))
        if receipt:
            if json.loads(receipt.detail).get("digest") != digest:
                raise AgentError("request_key_reused", 422)
            return public(owned(s, ws, receipt.draft_id)), False
        if draft_id:
            row = owned(s, ws, draft_id)
            stale_processing = row.status == "processing" and row.updated_at < db.utcnow() - LEASE
            if row.revision != revision or (row.status not in EDITABLE and not stale_processing):
                raise AgentError("stale_draft")
            won = s.execute(update(db.AgentDraft).where(db.AgentDraft.id == row.id, db.AgentDraft.revision == revision,
                                db.AgentDraft.status == row.status)
                            .values(revision=revision + 1, status="processing", updated_at=db.utcnow())
                            .execution_options(synchronize_session=False)).rowcount
            if not won:
                raise AgentError("stale_draft")
            history = json.loads(row.history)
            if len(history) >= 12:
                raise AgentError("too_many_edits", 422)
            history.append({"text": row.transcript, "plan": json.loads(row.plan), "revision": revision})
            s.refresh(row)
            row.history = dumps(history)
            row.transcript, row.plan, row.preview, row.error = text, "{}", "", None
            row.detected_language = None
        else:
            row = db.AgentDraft(id=uuid.uuid4().hex, workspace_id=ws, request_key=key,
                                source=source, status="processing", transcript=text)
            s.add(row)
        try:
            s.flush()
            _audit(s, row, "captured", {"digest": digest}, key=key)
            s.commit()
        except IntegrityError:
            s.rollback()
            receipt = s.scalar(select(db.AgentAudit).where(db.AgentAudit.workspace_id == ws, db.AgentAudit.request_key == key))
            if not receipt or json.loads(receipt.detail).get("digest") != digest:
                raise AgentError("request_key_reused", 422) from None
            return public(owned(s, ws, receipt.draft_id)), False
        return public(row), True


def in_language(text, lang):
    """Replies follow the user's app language, never the input language.

    A model question mostly in the wrong script is dropped for the fixed
    fallback in the right language. Names in another script are a minority of
    letters and pass.
    """
    letters = [c for c in text or "" if c.isalpha()]
    if not letters:
        return False
    cyrillic = sum("\u0400" <= c <= "\u04ff" for c in letters) / len(letters)
    return cyrillic >= 0.5 if lang == "ru" else cyrillic < 0.5


def _failure(ws, draft_id, revision, code):
    with db.SessionLocal() as s:
        s.execute(update(db.AgentDraft).where(db.AgentDraft.id == draft_id, db.AgentDraft.workspace_id == ws,
                   db.AgentDraft.revision == revision, db.AgentDraft.status == "processing")
                  .values(status="failed", error=code, updated_at=db.utcnow()))
        s.commit()
    return get_draft(ws, draft_id)


async def process(uid, ws, draft_id, revision, *, audio=None, mime=None):
    """Every awaited response is version checked, so corrections win over late AI."""
    import agent_provider
    try:
        with db.SessionLocal() as s:
            row = owned(s, ws, draft_id)
            if row.revision != revision or row.status != "processing":
                return public(row)
            _budget(s, uid, ws)
            transcript = row.transcript
            history = json.loads(row.history)
            context = catalog(s, uid, ws)
            s.commit()
        # The whole operation is bounded; provider has its own network timeout.
        async with asyncio.timeout(150):
            if audio is not None:
                transcript = await agent_provider.transcribe(audio, mime, context)
                if not transcript.strip() or len(transcript) > 6000:
                    raise AgentError("empty_audio", 422)
                with db.SessionLocal() as s:
                    won = s.execute(update(db.AgentDraft).where(db.AgentDraft.id == draft_id,
                                db.AgentDraft.revision == revision, db.AgentDraft.status == "processing")
                                .values(transcript=transcript)).rowcount
                    s.commit()
                    if not won:
                        return get_draft(ws, draft_id)
            plan = await agent_provider.plan(transcript, context, history[-3:])
        if not isinstance(plan, Plan):
            plan = Plan.model_validate(plan)
        if plan.question and len(plan.question) > 1000:
            raise AgentError("invalid_plan", 422)
        lang = context["language"]
        with db.SessionLocal() as s:
            require_consent(s, ws)
            if plan.language != lang:
                # Only the language chosen in the profile is accepted.
                actions, question, summary = [], None, escape(tr(lang, "wrong_language"))
            else:
                actions = prepare(s, uid, ws, plan, allowed_catalog=context)
                question = plan.question if in_language(plan.question, lang) else None
                understood = (plan.understood or "")[:300] if in_language(plan.understood, lang) else None
                # The summary is the HTML card the bot sends; plain text is escaped.
                summary = (preview(actions, lang, context["today"], understood) if actions
                           else escape(question or tr(lang, "clarify")))
            proposed = {"actions": actions, "question": question,
                        "planned_day": context["today"], "timezone": context["timezone"]}
            won = s.execute(update(db.AgentDraft).where(db.AgentDraft.id == draft_id, db.AgentDraft.workspace_id == ws,
                               db.AgentDraft.revision == revision, db.AgentDraft.status == "processing")
                            .values(status="ready" if actions else "needs_input", plan=dumps(proposed),
                                    preview=summary, detected_language=plan.language, error=None, updated_at=db.utcnow())).rowcount
            if won:
                _audit(s, owned(s, ws, draft_id), "proposed")
            s.commit()
        return get_draft(ws, draft_id)
    except AgentError as e:
        return _failure(ws, draft_id, revision, e.code)
    except (ValidationError, ValueError, TypeError, KeyError):
        return _failure(ws, draft_id, revision, "invalid_plan")
    except PermissionError:
        return _failure(ws, draft_id, revision, "forbidden")
    except svc.NotFound:
        return _failure(ws, draft_id, revision, "not_found")
    except (TimeoutError, asyncio.CancelledError):
        return _failure(ws, draft_id, revision, "processing_interrupted")
    except Exception as e:
        # Never log input, model output, audio, credentials or provider response.
        log.warning("agent processing failed: %s", type(e).__name__)
        return _failure(ws, draft_id, revision, "provider_unavailable")


async def ingest(uid, ws, key, *, text="", audio=None, mime=None, draft_id=None, revision=None):
    source = "voice" if audio is not None else "text"
    digest = hashlib.sha256(audio).hexdigest() if audio is not None else None
    draft, is_new = capture(uid, ws, key, text=text, source=source,
                            draft_id=draft_id, revision=revision, digest=digest)
    if not is_new:
        return draft
    return await process(uid, ws, draft["id"], draft["revision"], audio=audio, mime=mime)


def confirm(uid, ws, draft_id, revision):
    """One ACID transaction: CAS claim + all actions + audit + executed result.

    There is no committed 'executing' state to strand on a process crash. Either
    the entire transaction exists or the old ready draft still exists.
    """
    with AtomicSession(bind=db.engine, expire_on_commit=False) as s:
        try:
            actor(s, uid, ws)
            require_consent(s, ws)
            row = owned(s, ws, draft_id)
            if row.revision != revision:
                raise AgentError("stale_draft")
            if row.status == "executed":
                return public(row)
            if row.status != "ready":
                raise AgentError("not_ready")
            if row.updated_at < db.utcnow() - EXPIRY:
                raise AgentError("draft_expired")
            won = s.execute(update(db.AgentDraft).where(db.AgentDraft.id == draft_id, db.AgentDraft.revision == revision,
                                db.AgentDraft.status == "ready").values(status="executing")
                            .execution_options(synchronize_session=False)).rowcount
            if not won:
                s.expire_all()
                row = owned(s, ws, draft_id)
                if row.status == "executed" and row.revision == revision:
                    return public(row)
                raise AgentError("stale_draft")
            proposal = json.loads(row.plan)
            actions = proposal.get("actions", [])
            if not 1 <= len(actions) <= 6:
                raise AgentError("invalid_plan", 422)
            if any(a["entity"] == "habit" and a["operation"] == "create" for a in actions):
                if proposal.get("planned_day") != str(svc.today_local(svc.user_tz(s.get(db.User, uid)))):
                    raise AgentError("draft_expired")
            result = [execute(s, uid, ws, a) for a in actions]
            # Exactly once and in the same transaction as the actions.
            svc.record_action_and_progress(s, uid)
            row.status, row.result, row.error = "executed", dumps(result), None
            row.confirmed_at = row.updated_at = db.utcnow()
            _audit(s, row, "executed", {"actions": actions, "results": result})
            Session.commit(s)
            return public(row)
        except Exception:
            Session.rollback(s)
            raise


def cancel(ws, draft_id, revision):
    with db.SessionLocal() as s:
        row = owned(s, ws, draft_id)
        if row.status == "cancelled" and row.revision == revision:
            return public(row)
        won = s.execute(update(db.AgentDraft).where(db.AgentDraft.id == draft_id, db.AgentDraft.revision == revision,
                          db.AgentDraft.status.in_(EDITABLE | {"processing"}))
                        .values(status="cancelled", updated_at=db.utcnow())
                        .execution_options(synchronize_session=False)).rowcount
        if not won:
            raise AgentError("stale_draft")
        s.refresh(row)
        _audit(s, row, "cancelled")
        s.commit()
        return public(row)


async def journal_fill(uid, ws, *, text="", audio=None, mime=None):
    """Voice or free text -> the five day-summary answers, for the user to review.

    Nothing is saved here: the app fills the five fields and the person saves.
    Counts against the same daily budget as the agent.
    """
    import agent_provider
    with db.SessionLocal() as s:
        _budget(s, uid, ws)
        lang = s.get(db.User, uid).language or "uz"
        s.commit()
    try:
        async with asyncio.timeout(150):
            if audio is not None:
                context = {"language": lang, "items": [], "teams": []}
                text = await agent_provider.transcribe(audio, mime, context)
            text = (text or "").strip()
            if not text or len(text) > 6000:
                raise AgentError("empty_audio", 422)
            filled = await agent_provider.journal_answers(text, lang)
    except (TimeoutError, asyncio.CancelledError):
        raise AgentError("processing_interrupted", 503) from None
    except (ValidationError, ValueError, TypeError, KeyError):
        raise AgentError("invalid_plan", 422) from None
    answers = {}
    for key in svc.JOURNAL_KEYS:
        value = (getattr(filled, key, None) or "").strip()[:2000]
        # A reply in the wrong script is dropped rather than shown.
        if value and in_language(value, lang):
            answers[key] = value
    return {"answers": answers, "filled": len(answers)}
