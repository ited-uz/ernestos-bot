"""The assistant chat: summaries, advice and plans from the person's own data.

A question gets an answer written from a compact snapshot of the person's
tasks, habits, goals and money. A request to change something becomes the
same draft the voice agent makes, shown as a card to confirm — the chat
never executes anything itself. Every turn counts against the same AI budget
as voice, and the latest messages are kept so the conversation continues.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta

from pydantic import ValidationError
from sqlalchemy import delete, select

import agent_core as core
import db
import services as svc
from agent_actions import AgentError, actor
from agent_text import tr

log = logging.getLogger("ernestos.agent")

#: Messages kept per person, and how many recent ones the model sees.
KEEP = 40
HISTORY = 8
MAX_TEXT = 2000
KEY = re.compile(r"[A-Za-z0-9_:-]{8,80}")


def _slim(text, n=80):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def context(s, uid, ws) -> dict:
    """What the assistant knows: today's work, the week, goals and money.
    Bounded so one turn stays cheap; names are trimmed."""
    user = s.get(db.User, uid)
    tz = svc.user_tz(user)
    today = svc.today_local(tz)
    tasks = s.scalars(select(db.Task).where(
        db.Task.workspace_id == ws, db.Task.archived_at.is_(None),
        db.Task.status == "waiting")).all()
    week_ago = db.utcnow() - timedelta(days=7)
    done_week = s.scalars(select(db.Task.title).where(
        db.Task.workspace_id == ws, db.Task.status == "done",
        db.Task.completed_at >= week_ago)).all()
    habits = [h for h in svc.list_habits(s, ws, today, tz=tz) if h["due"]]
    focus = svc.week_focus(s, ws, tz=tz)
    week = [x for x in [focus.get("primary"), *(focus.get("supporting") or [])] if x]
    money = svc.money_overview(s, ws, tz=tz, limit=5)
    wallet = money.get("wallet") or {}
    debts = money.get("debts") or {}
    return {
        "today": today.isoformat(), "weekday": today.strftime("%A"),
        "language": user.language or "uz", "name": user.first_name or "",
        "tasks": {
            "open": len(tasks),
            "overdue": [_slim(t.title) for t in tasks if t.deadline and t.deadline < today][:8],
            "today": [_slim(t.title) + (f" {t.due_time:%H:%M}" if t.due_time else "")
                      for t in tasks if t.deadline == today][:10],
            "next_days": [f"{t.deadline.isoformat()} {_slim(t.title)}" for t in sorted(
                (t for t in tasks if t.deadline and today < t.deadline <= today + timedelta(days=7)),
                key=lambda t: t.deadline)][:8],
            "done_last_7_days": len(done_week),
        },
        "habits_today": {"done": sum(1 for h in habits if h["done"]), "total": len(habits),
                         "left": [_slim(h["name"], 50) for h in habits if not h["done"]][:10]},
        "week_focus": [{"title": _slim(x["title"]), "done": bool(x.get("done"))} for x in week],
        "goals": [{"level": g["level"], "title": _slim(g["title"]), "area": g["category"],
                   "status": g["status"], "progress": g["progress"], "by": g["horizon"] or None}
                  for g in svc.list_goals(s, ws)][:15],
        "money_this_month": {
            "unit": "UZS", "income": money["income"], "expense": money["expense"],
            "total_balance": money["balance"],
            "top_spending": [{"category": c["id"], "spent": c["spent"], "limit": c["limit"] or None}
                             for c in sorted(money["categories"], key=lambda c: -c["spent"])
                             if c["spent"]][:5],
            "accounts": [{"name": _slim(a["name"], 40), "balance": a["balance"]}
                         for a in wallet.get("accounts", [])][:8],
            "payments_due_7_days": [{"name": _slim(x["name"], 40), "amount": x["amount"],
                                     "in_days": x["days_left"]}
                                    for x in wallet.get("subscriptions", []) if x["days_left"] <= 7][:6],
            "debts": {"owed_to_me": debts.get("owed_to_me", 0), "i_owe": debts.get("i_owe", 0)},
        },
    }


def _message(row, drafts) -> dict:
    draft = drafts.get(row.draft_id)
    return {"id": row.id, "role": row.role, "text": row.text,
            "at": row.created_at.isoformat() + "Z" if row.created_at else None,
            "draft": _draft_view(draft) if draft else None}


def _draft_view(row) -> dict:
    """What the chat draws for a proposal: the card and whether it can still
    be confirmed."""
    view = core.public(row)
    return {"id": view["id"], "revision": view["revision"], "status": view["status"],
            "preview": view["preview"], "error": view["error"]}


def history(ws) -> list[dict]:
    with db.SessionLocal() as s:
        rows = list(reversed(s.scalars(select(db.AgentChatMessage).where(
            db.AgentChatMessage.workspace_id == ws)
            .order_by(db.AgentChatMessage.id.desc()).limit(KEEP)).all()))
        ids = [r.draft_id for r in rows if r.draft_id]
        drafts = {d.id: d for d in s.scalars(select(db.AgentDraft).where(
            db.AgentDraft.id.in_(ids), db.AgentDraft.workspace_id == ws)).all()} if ids else {}
        return [_message(r, drafts) for r in rows]


def clear(ws) -> None:
    with db.SessionLocal() as s:
        s.execute(delete(db.AgentChatMessage).where(db.AgentChatMessage.workspace_id == ws))
        s.commit()


def _prune(s, ws) -> None:
    keep = s.scalars(select(db.AgentChatMessage.id).where(db.AgentChatMessage.workspace_id == ws)
                     .order_by(db.AgentChatMessage.id.desc()).limit(KEEP)).all()
    if len(keep) == KEEP:
        s.execute(delete(db.AgentChatMessage).where(
            db.AgentChatMessage.workspace_id == ws, db.AgentChatMessage.id < min(keep)))


async def ask(uid, ws, key, text) -> dict:
    """One turn: store the question, answer it, and turn a requested change
    into a draft to confirm. A retry with the same key returns what is stored."""
    import agent_provider
    text = (text or "").strip()
    if not KEY.fullmatch(key or ""):
        raise AgentError("invalid_request_key", 422)
    if not text or len(text) > MAX_TEXT:
        raise AgentError("invalid_text", 422)
    with db.SessionLocal() as s:
        actor(s, uid, ws)
        core.require_consent(s, ws)
        if s.scalar(select(db.AgentChatMessage.id).where(
                db.AgentChatMessage.workspace_id == ws, db.AgentChatMessage.request_key == key)):
            return {"messages": history(ws), "draft": None, "repeat": True}
        core._budget(s, uid, ws)
        snapshot = context(s, uid, ws)
        past = [{"role": r.role, "text": r.text} for r in reversed(s.scalars(
            select(db.AgentChatMessage).where(db.AgentChatMessage.workspace_id == ws)
            .order_by(db.AgentChatMessage.id.desc()).limit(HISTORY)).all())]
        question = db.AgentChatMessage(workspace_id=ws, role="user", text=text, request_key=key)
        s.add(question)
        s.commit()
        question_id = question.id
    lang = snapshot["language"]
    try:
        async with asyncio.timeout(120):
            answer = await agent_provider.chat(snapshot, past, text)
        if not isinstance(answer, agent_provider.ChatReply):
            answer = agent_provider.ChatReply.model_validate(answer)
    except Exception as e:
        # The question goes too, so sending it again asks again.
        with db.SessionLocal() as s:
            s.execute(delete(db.AgentChatMessage).where(db.AgentChatMessage.id == question_id))
            s.commit()
        if isinstance(e, AgentError):
            raise
        if isinstance(e, (ValidationError, ValueError, TypeError, KeyError)):
            raise AgentError("invalid_plan", 422) from None
        if isinstance(e, (TimeoutError, asyncio.CancelledError)):
            raise AgentError("processing_interrupted", 503) from None
        log.warning("agent chat failed: %s", type(e).__name__)
        raise AgentError("provider_unavailable", 503) from None

    reply = (answer.reply or "").strip()[:MAX_TEXT] or tr(lang, "clarify")
    draft = None
    command = (answer.command or "").strip()[:600]
    if command:
        try:
            draft = await core.ingest(uid, ws, (key[:70] + ":cmd"), text=command)
        except AgentError as e:
            reply += "\n\n" + tr(lang, e.code)
    with db.SessionLocal() as s:
        s.add(db.AgentChatMessage(workspace_id=ws, role="assistant", text=reply,
                                  draft_id=draft["id"] if draft else None))
        _prune(s, ws)
        s.commit()
    return {"messages": history(ws), "draft": draft, "repeat": False}
