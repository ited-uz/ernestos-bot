"""The AI pick for the Hozir card, on Pro and Max.

Everybody gets Hozir from the fixed ladder in `services.now_next`. On Pro and
Max the app also asks the model which of today's open tasks to do now, and
one sentence why. The model only ever chooses among candidates built here
from the person's own open work; its answer is checked against them, cached
for a while, and capped per day, so a screen opened fifty times does not ask
fifty times.

The cache and the daily counter live in this process. With several workers
each keeps its own, so the cap is per worker — a cost guard, not a security
boundary; the plan check and the agent consent are the boundaries.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time

from pydantic import ValidationError

import db
import plans
import services as svc
import agent_core as core
from agent_actions import AgentError

log = logging.getLogger(__name__)

#: How long one answer stands while the list it was made from is unchanged.
CACHE_SECONDS = 45 * 60
#: Model calls per person per day; past it the card keeps the ladder's pick.
DAILY_CAP = 24
#: What the model is shown at most: enough to choose well, little to pay for.
MAX_CANDIDATES = 20

_cache: dict[tuple, tuple[float, dict]] = {}
_calls: dict[tuple, int] = {}


def _row(task: dict, late: bool, pinned: set[int]) -> dict:
    team = task.get("source") == "team"
    return {"key": ("t" if team else "p") + str(task["id"]),
            "title": task["title"], "priority": task["priority"],
            "deadline": task["deadline"], "due_time": task["due_time"],
            "late": late, "pinned": (not team) and task["id"] in pinned,
            "project": task.get("project") or None,
            "group": task.get("team_name") if team else None,
            "_task": task}


def candidates(s, ws: int, user, tz) -> list[dict]:
    """Open work that could be the answer: late, due today, then undated."""
    today = svc.today_local(tz)
    listed = svc.list_tasks(s, ws, horizon_days=0, tz=tz)
    pinned = {t["id"] for t in svc.top3_tasks(s, ws, today, tz=tz) if t.get("status") != "done"}
    team_late, team_due = svc._team_open_for_now(s, user, today, tz)
    out, seen = [], set()

    def add(rows, late):
        for task in rows:
            if task.get("status") == "done" or task.get("done") or svc._is_parked(task):
                continue
            row = _row(task, late, pinned)
            if row["key"] not in seen:
                seen.add(row["key"])
                out.append(row)

    add(listed.get("overdue", []), True)
    add(team_late, True)
    add(svc.tasks_due_today(s, ws, tz=tz), False)
    add(team_due, False)
    add(listed.get("undated", [])[:8], False)
    return out[:MAX_CANDIDATES]


def _prune(now: float) -> None:
    if len(_cache) > 5000:
        for key in [k for k, (at, _) in _cache.items() if now - at > CACHE_SECONDS]:
            _cache.pop(key, None)
    if len(_calls) > 20000:
        _calls.clear()


async def pick(uid: int, ws: int) -> dict:
    """{"source": "ai", "pick": <now card task>, ...} or {"source": "none"}."""
    import agent_provider
    with db.SessionLocal() as s:
        if plans.ENABLED and plans.tier_of(s, uid) not in plans.FEATURES["now_ai"]:
            raise AgentError("plan_limit", 402)
        core.require_consent(s, ws)
        if not agent_provider.configured():
            raise AgentError("provider_not_configured", 503)
        user = s.get(db.User, uid)
        tz = svc.user_tz(user)
        today = svc.today_local(tz)
        rows = candidates(s, ws, user, tz)
        lang = user.language or "uz"
        week = svc.week_focus(s, ws, tz=tz)
        focus = (week.get("primary") or {}).get("title")
        goals = [g["title"] for g in svc.list_goals(s, ws)
                 if g["level"] == "ultimate" and g["status"] != "done"][:5]
        clock = svc.now_local(tz).strftime("%H:%M")
    if not rows:
        return {"source": "none", "pick": None}

    public = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    digest = hashlib.sha256(json.dumps([public, focus, goals], ensure_ascii=False,
                                       sort_keys=True).encode()).hexdigest()[:24]
    key = (uid, today.isoformat(), digest)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_SECONDS:
        return {**hit[1], "cached": True}
    day = (uid, today.isoformat())
    if _calls.get(day, 0) >= DAILY_CAP:
        raise AgentError("provider_limit", 429)
    _calls[day] = _calls.get(day, 0) + 1
    _prune(now)

    payload = {"language": lang, "time_now": clock, "week_focus": focus,
               "life_goals": goals, "tasks": public}
    try:
        async with asyncio.timeout(40):
            answer = await agent_provider.now_pick(payload)
        if not isinstance(answer, agent_provider.NowPick):
            answer = agent_provider.NowPick.model_validate(answer)
    except AgentError:
        raise
    except (TimeoutError, asyncio.CancelledError):
        raise AgentError("processing_interrupted", 503) from None
    except (ValidationError, ValueError, TypeError, KeyError):
        raise AgentError("invalid_plan", 422) from None

    chosen = next((r for r in rows if r["key"] == (answer.key or "").strip()), None)
    if chosen is None:
        # Never show a task the person does not have.
        log.warning("now_ai: the model chose outside the candidates")
        raise AgentError("invalid_plan", 422)
    reason = " ".join((answer.reason or "").split())[:220]
    if not core.in_language(reason, lang):
        reason = ""
    card = svc._now_task(chosen["_task"], "ai")
    card["ai_reason"] = reason
    out = {"source": "ai", "pick": card}
    _cache[key] = (now, out)
    return out
