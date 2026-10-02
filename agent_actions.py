"""An allowlisted, identity-free command language. Models propose; users authorize.

Neither prompts nor model output can run SQL, arbitrary Python, send messages,
change accounts, or choose a workspace. Both transports use this same executor.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, time
from html import escape
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import db
import services as svc


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["title", "name", "description", "deadline", "due_time",
                   "priority", "project_id", "recurrence", "remind_before",
                   "timer_minutes", "category", "schedule", "remind_at",
                   "start", "day", "kind", "amount", "note", "person", "direction"]
    value: str | None


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: Literal["task", "habit", "project", "money", "debt"]
    operation: Literal["create", "update", "delete"]
    scope: Literal["personal", "team"]
    team_id: int | None
    target_id: int | None
    changes: list[Change] = Field(max_length=14)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # The input's MAIN language. Uzbek with a few Russian/English words is uz.
    language: Literal["uz", "ru", "en", "other"]
    # What the person meant, rewritten as one clean sentence in their app
    # language — the speech transcript itself is often phonetic or dialectal.
    understood: str | None
    question: str | None
    actions: list[Action] = Field(max_length=6)


class AgentError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(code)


class AtomicSession(Session):
    """Adapt legacy commit-owning services to ONE outer unit of work.

    A service commit only flushes. An attempted full rollback aborts the entire
    command rather than letting a legacy retry continue after losing our claim.
    Only the controller may call Session.commit/rollback on this instance.
    """
    def commit(self):
        self.flush()

    def rollback(self):
        raise AgentError("concurrent_change")


FIELDS = {
    "task": {"title", "description", "deadline", "due_time", "priority",
             "project_id", "recurrence", "remind_before", "timer_minutes"},
    "habit": {"name", "category", "schedule", "remind_at", "timer_minutes", "start"},
    "project": {"name", "description", "deadline"},
    "money": {"kind", "amount", "category", "note", "day"},
    # Create only: closing or changing a debt is one tap in the app.
    "debt": {"person", "amount", "direction", "note", "deadline"},
}
PERSONAL = {"task": db.Task, "habit": db.Habit, "project": db.Project, "money": db.MoneyEntry}
TEAM = {"task": db.TeamTask, "habit": db.TeamHabit, "project": db.Project}
NULLABLE = {"deadline", "due_time", "project_id", "remind_at", "recurrence", "remind_before", "timer_minutes"}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      default=lambda v: v.strftime("%H:%M") if isinstance(v, time) else str(v))


def snapshot(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def fingerprint(row):
    return hashlib.sha256(dumps(snapshot(row)).encode()).hexdigest()


def actor(s, uid, ws):
    workspace = s.get(db.Workspace, ws)
    user = s.get(db.User, uid)
    if not user or not workspace or workspace.user_id != uid:
        raise AgentError("not_found", 404)
    return user


def target(s, uid, ws, a, *, lock=False):
    if a["scope"] == "team":
        if a["entity"] not in TEAM or not a["team_id"]:
            raise AgentError("invalid_action", 422)
        svc._require_team(s, uid, a["team_id"])
        model = TEAM[a["entity"]]
    else:
        if a["team_id"] is not None:
            raise AgentError("invalid_action", 422)
        model = PERSONAL.get(a["entity"])
    if a["target_id"] is None:
        return None
    if model is None:
        raise AgentError("invalid_action", 422)
    stmt = select(model).where(model.id == a["target_id"])
    if a["scope"] == "team":
        stmt = stmt.where(model.team_id == a["team_id"])
    else:
        stmt = stmt.where(model.workspace_id == ws)
        if model is db.Project:
            stmt = stmt.where(model.team_id.is_(None))
    row = s.scalar(stmt.with_for_update() if lock else stmt)
    if row is None or getattr(row, "archived_at", None):
        raise AgentError("not_found", 404)
    if a["scope"] == "team" and a["operation"] in {"update", "delete"}:
        svc._require_manage(s, uid, row.team_id, row.created_by)
    if a["entity"] == "habit" and getattr(row, "system_key", None):
        # Derived prayer/journal/wakeup habits must use their own screens.
        raise AgentError("protected_item", 422)
    return row


def _value(key, raw):
    if raw is None:
        if key not in NULLABLE:
            raise AgentError("invalid_fields", 422)
        return None
    raw = raw.strip()
    if key in {"amount", "project_id", "remind_before", "timer_minutes"}:
        if not re.fullmatch(r"\d{1,13}", raw):
            raise AgentError("invalid_fields", 422)
        value = int(raw)
        high = {"amount": 10**12, "timer_minutes": 1440,
                "remind_before": 10080, "project_id": 2**31 - 1}[key]
        if value > high or (key in {"amount", "project_id"} and value < 1):
            raise AgentError("invalid_fields", 422)
        return value
    if key in {"deadline", "day"}:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            raise AgentError("invalid_fields", 422)
        return date.fromisoformat(raw)
    if key in {"due_time", "remind_at"}:
        if not re.fullmatch(r"\d{2}:\d{2}", raw):
            raise AgentError("invalid_fields", 422)
        return time.fromisoformat(raw)
    bounds = {"title": 300, "name": 120, "description": 2000, "note": 200, "person": 80}
    if len(raw) > bounds.get(key, 32) or (key in {"title", "name", "person"} and not raw):
        raise AgentError("invalid_fields", 422)
    choices = {"priority": {"low", "medium", "high"}, "kind": {"income", "expense"},
               "start": {"today", "tomorrow"}, "recurrence": {"", "daily", "weekly", "monthly"},
               "direction": {"lent", "borrowed"}}
    if key in choices and raw not in choices[key]:
        raise AgentError("invalid_fields", 422)
    if key == "schedule" and raw not in {"daily", "weekdays"} and not re.fullmatch(r"days:[0-6](?:,[0-6])*", raw):
        raise AgentError("invalid_fields", 422)
    return raw


def prepare(s, uid, ws, plan: Plan, allowed_catalog=None):
    """Normalize defaults BEFORE preview; revalidate permissions again on confirm."""
    user = actor(s, uid, ws)
    today = svc.today_local(svc.user_tz(user))
    if plan.question or not plan.actions:
        return []
    out, seen = [], set()
    for spec in plan.actions:
        a = spec.model_dump()
        entity, op = a["entity"], a["operation"]
        if (op == "create") != (a["target_id"] is None):
            raise AgentError("invalid_action", 422)
        if entity == "debt" and (op != "create" or a["scope"] != "personal"):
            raise AgentError("invalid_action", 422)
        row = target(s, uid, ws, a)
        if allowed_catalog is not None:
            # A model cannot invent an identifier, even in the caller's account.
            refs = {(r["entity"], r["scope"], r["id"]) for r in allowed_catalog["items"]}
            if row is not None and (entity, a["scope"], row.id) not in refs:
                raise AgentError("unknown_reference", 422)
            if a["scope"] == "team" and a["team_id"] not in {t["id"] for t in allowed_catalog["teams"]}:
                raise AgentError("unknown_reference", 422)
        fields = {}
        allowed = FIELDS[entity] if op in {"create", "update"} else set()
        for change in spec.changes:
            if change.field in fields or change.field not in allowed:
                raise AgentError("invalid_fields", 422)
            fields[change.field] = _value(change.field, change.value)
        if op == "update" and not fields:
            raise AgentError("invalid_fields", 422)
        if entity == "habit" and op == "update" and "start" in fields:
            raise AgentError("invalid_fields", 422)
        if op == "create":
            required = {"task": "title", "habit": "name", "project": "name", "money": "amount",
                        "debt": "amount"}[entity]
            if not fields.get(required):
                raise AgentError("missing_fields", 422)
            if entity == "debt" and not (fields.get("person") and fields.get("direction")):
                raise AgentError("missing_fields", 422)
            if entity == "habit":
                fields.setdefault("category", "target")
                fields.setdefault("schedule", "daily")
                fields.setdefault("start", "today")
                fields.setdefault("timer_minutes", 0)
            if entity == "task":
                fields.setdefault("priority", "medium")
                fields.setdefault("timer_minutes", 0)
                fields.setdefault("project_id", None)
        if entity == "habit" and "category" in fields and fields["category"] not in svc.HABIT_CATEGORIES:
            raise AgentError("invalid_fields", 422)
        if entity == "money":
            if op == "create":
                if "kind" not in fields:
                    raise AgentError("missing_fields", 422)
                fields.setdefault("category", "other" if fields["kind"] == "expense" else "other_in")
                fields.setdefault("day", today)
            kind = fields.get("kind", getattr(row, "kind", None))
            category = fields.get("category", getattr(row, "category", None))
            if op != "delete" and svc._MONEY_KIND_OF.get(category) != kind:
                raise AgentError("invalid_fields", 422)
        if "day" in fields and (fields["day"] > today or (today - fields["day"]).days > 366):
            raise AgentError("invalid_day", 422)
        if fields.get("project_id"):
            pa = {**a, "entity": "project", "target_id": fields["project_id"], "operation": "reference"}
            project = target(s, uid, ws, pa)
            if allowed_catalog is not None and ("project", a["scope"], project.id) not in refs:
                raise AgentError("unknown_reference", 422)
            a["project_name"] = project.name
        if row is not None:
            key = (entity, a["scope"], row.id)
            if key in seen:
                raise AgentError("duplicate_target", 422)
            seen.add(key)
        a.pop("changes")
        a["fields"] = fields
        a["name"] = (getattr(row, "title", None) or getattr(row, "name", None)
                     or (f'{row.kind}: {row.amount:,} UZS · {row.category} · {row.day} · {row.note}' if entity == "money" and row else None)
                     or fields.get("title") or fields.get("name") or fields.get("person") or entity)
        a["before"] = snapshot(row) if row is not None else None
        a["fingerprint"] = fingerprint(row) if row is not None else None
        if a["scope"] == "team":
            a["team_name"] = s.get(db.Team, a["team_id"]).name
        out.append(json.loads(dumps(a)))
    return out


def catalog(s, uid, ws):
    user = actor(s, uid, ws)
    teams = svc.teams_for(s, uid)
    tids = [t.id for t in teams]
    items, truncated = [], False
    for scope, models in (("personal", PERSONAL), ("team", TEAM)):
        for entity, model in models.items():
            q = select(model)
            if scope == "personal":
                q = q.where(model.workspace_id == ws)
                if model is db.Project:
                    q = q.where(model.team_id.is_(None))
            else:
                q = q.where(model.team_id.in_(tids))
            if hasattr(model, "archived_at"):
                q = q.where(model.archived_at.is_(None))
            rows = s.scalars(q.order_by(model.id.desc()).limit(81)).all()
            truncated |= len(rows) > 80
            for row in rows[:80]:
                item = {"entity": entity, "scope": scope, "id": row.id,
                        "name": getattr(row, "title", None) or getattr(row, "name", ""),
                        "team_id": getattr(row, "team_id", None)}
                for field in ("status", "deadline", "due_time", "project_id", "category", "schedule", "system_key", "day", "amount", "kind", "note"):
                    if hasattr(row, field):
                        item[field] = getattr(row, field)
                items.append(item)
    return json.loads(dumps({"today": svc.today_local(svc.user_tz(user)),
                            "timezone": str(svc.user_tz(user)), "language": user.language,
                            "teams": [{"id": t.id, "name": t.name} for t in teams],
                            "items": items, "truncated": truncated}))


def execute(s, uid, ws, a):
    user = actor(s, uid, ws)
    tz = svc.user_tz(user)
    row = target(s, uid, ws, a, lock=True)
    if row is not None and fingerprint(row) != a["fingerprint"]:
        raise AgentError("stale_target")
    fields = {k: _value(k, str(v) if v is not None else None) for k, v in a["fields"].items()}
    if fields.get("project_id"):
        target(s, uid, ws, {**a, "entity": "project", "target_id": fields["project_id"], "operation": "reference"}, lock=True)
    entity, op, tid = a["entity"], a["operation"], a["target_id"]
    team = a["scope"] == "team"
    out = None
    if entity == "task":
        if op == "create":
            out = svc.add_team_task(s, uid, a["team_id"], **fields) if team else svc.add_task(s, ws, **fields)
        elif op == "update":
            out = svc.edit_team_task(s, uid, tid, **fields) if team else svc.update_task(s, ws, tid, **fields)
        else:
            out = svc.archive_team_task(s, uid, tid) if team else svc.delete_task(s, ws, tid)
    elif entity == "habit":
        if op == "create":
            out = svc.add_team_habit(s, uid, a["team_id"], tz=tz, **fields) if team else svc.add_habit(s, ws, tz=tz, **fields)
        elif op == "update":
            out = svc.edit_team_habit(s, uid, tid, **fields) if team else svc.update_habit(s, ws, tid, **fields)
        else:
            out = svc.archive_team_habit(s, uid, tid) if team else svc.delete_habit(s, ws, tid)
    elif entity == "project":
        if op == "create":
            out = svc.add_team_project(s, uid, a["team_id"], **fields) if team else svc.add_project(s, ws, **fields)
        elif op == "delete":
            out = svc.delete_team_project(s, uid, tid) if team else svc.delete_project(s, ws, tid)
        else:
            out = svc.update_team_project(s, uid, tid, **fields) if team else svc.update_project(s, ws, tid, **fields)
    elif entity == "money":
        if op == "create":
            out = svc.add_money(s, ws, source="voice", tz=tz, **fields)
        elif op == "delete":
            out = svc.delete_money(s, ws, tid)
        else:
            # Money v10 has no edit service. Validate through the same rules,
            # then update the existing row, retaining its identity and source.
            for key, value in fields.items():
                setattr(row, key, value)
            s.flush()
            out = row
    elif entity == "debt":
        out = svc.add_debt(s, ws, fields["person"], fields["amount"], fields["direction"],
                           note=fields.get("note") or "", due=fields.get("deadline"))
    result_id = out.get("id") if isinstance(out, dict) else getattr(out, "id", tid)
    return {"entity": entity, "operation": op, "id": result_id, "name": a["name"]}


VALUES = {"uz": {"daily": "Har kuni", "weekdays": "Dushanba–Juma", "today": "Bugundan", "tomorrow": "Ertadan", "target": "Maqsadli", "non_negotiable": "Majburiy", "bonus": "Bonus", "low": "Past", "medium": "O‘rta", "high": "Yuqori", "expense": "Chiqim", "income": "Kirim", "food": "Oziq-ovqat", "transport": "Transport", "home": "Uy", "health": "Salomatlik", "fun": "Dam olish", "business": "Biznes", "other": "Boshqa", "salary": "Maosh", "sales": "Savdo", "other_in": "Boshqa kirim"}}
VALUES["uz"].update({"weekly": "Har hafta", "monthly": "Har oy"})
VALUES["ru"] = {"daily": "Каждый день", "weekdays": "Понедельник–Пятница", "weekly": "Каждую неделю", "monthly": "Каждый месяц", "today": "С сегодня", "tomorrow": "С завтра", "target": "Целевая", "non_negotiable": "Обязательная", "bonus": "Бонус", "low": "Низкий", "medium": "Средний", "high": "Высокий", "expense": "Расход", "income": "Доход", "food": "Питание", "transport": "Транспорт", "home": "Дом", "health": "Здоровье", "fun": "Отдых", "business": "Бизнес", "other": "Прочее", "salary": "Зарплата", "sales": "Продажи", "other_in": "Прочий доход"}
VALUES["en"] = {"daily": "Every day", "weekdays": "Monday–Friday", "weekly": "Every week", "monthly": "Every month", "today": "Today", "tomorrow": "Tomorrow", "target": "Target", "non_negotiable": "Non-negotiable", "bonus": "Bonus", "low": "Low", "medium": "Medium", "high": "High", "expense": "Expense", "income": "Income", "food": "Food", "transport": "Transport", "home": "Home", "health": "Health", "fun": "Leisure", "business": "Business", "other": "Other", "salary": "Salary", "sales": "Sales", "other_in": "Other income"}


# --- The proposal card ---------------------------------------------------------
# One block per action: what happens (heading), to what (bold name), and only
# the details the user actually gave, on one line with icons. HTML for Telegram;
# every user-supplied string is escaped.

HEADINGS = {
    "uz": {("task", "create"): "📝 Yangi vazifa", ("task", "update"): "✏️ Vazifa o‘zgaradi", ("task", "delete"): "🗑 Vazifa o‘chiriladi",
           ("habit", "create"): "🔁 Yangi odat", ("habit", "update"): "✏️ Odat o‘zgaradi", ("habit", "delete"): "🗑 Odat o‘chiriladi",
           ("project", "create"): "📁 Yangi loyiha", ("project", "update"): "✏️ Loyiha o‘zgaradi", ("project", "delete"): "🗑 Loyiha o‘chiriladi",
           ("money", "expense"): "💸 Chiqim", ("money", "income"): "💰 Kirim",
           ("money", "update"): "✏️ Pul yozuvi o‘zgaradi", ("money", "delete"): "🗑 Pul yozuvi o‘chiriladi",
           ("debt", "lent"): "🤝 Qarz berdingiz", ("debt", "borrowed"): "🤝 Qarz oldingiz"},
    "ru": {("task", "create"): "📝 Новая задача", ("task", "update"): "✏️ Изменить задачу", ("task", "delete"): "🗑 Удалить задачу",
           ("habit", "create"): "🔁 Новая привычка", ("habit", "update"): "✏️ Изменить привычку", ("habit", "delete"): "🗑 Удалить привычку",
           ("project", "create"): "📁 Новый проект", ("project", "update"): "✏️ Изменить проект", ("project", "delete"): "🗑 Удалить проект",
           ("money", "expense"): "💸 Расход", ("money", "income"): "💰 Доход",
           ("money", "update"): "✏️ Изменить запись", ("money", "delete"): "🗑 Удалить запись",
           ("debt", "lent"): "🤝 Вы дали в долг", ("debt", "borrowed"): "🤝 Вы взяли в долг"},
    "en": {("task", "create"): "📝 New task", ("task", "update"): "✏️ Edit task", ("task", "delete"): "🗑 Delete task",
           ("habit", "create"): "🔁 New habit", ("habit", "update"): "✏️ Edit habit", ("habit", "delete"): "🗑 Delete habit",
           ("project", "create"): "📁 New project", ("project", "update"): "✏️ Edit project", ("project", "delete"): "🗑 Delete project",
           ("money", "expense"): "💸 Expense", ("money", "income"): "💰 Income",
           ("money", "update"): "✏️ Edit money entry", ("money", "delete"): "🗑 Delete money entry",
           ("debt", "lent"): "🤝 You lent", ("debt", "borrowed"): "🤝 You borrowed"},
}
MONTHS = {"uz": ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"],
          "ru": ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"],
          "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]}
DAYS_FULL = {"uz": ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"],
             "ru": ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"],
             "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
RELATIVE = {"uz": ("Bugun", "Ertaga"), "ru": ("Сегодня", "Завтра"), "en": ("Today", "Tomorrow")}
SOM = {"uz": "so‘m", "ru": "сум", "en": "UZS"}
CATEGORY_ICONS = {"food": "🍔", "transport": "🚕", "home": "🏠", "health": "💊", "fun": "🎮",
                  "business": "💼", "other": "📦", "salary": "💼", "sales": "🛒", "other_in": "📦"}
DETAIL = {
    "uz": {"no_project": "Loyihasiz", "before": "{n} daqiqa oldin", "timer": "{n} daqiqa", "start_tomorrow": "Ertadan boshlab"},
    "ru": {"no_project": "Без проекта", "before": "за {n} мин", "timer": "{n} мин", "start_tomorrow": "С завтрашнего дня"},
    "en": {"no_project": "No project", "before": "{n} min before", "timer": "{n} min", "start_tomorrow": "Starting tomorrow"},
}
# Values the app fills in by itself. Showing them only adds noise.
HIDDEN = {("priority", "medium"), ("timer_minutes", 0), ("project_id", None), ("category", "target"),
          ("schedule", "daily"), ("start", "today"), ("recurrence", None), ("remind_before", None)}


def human_day(value, lang="uz", today=None):
    """'Bugun, 2-oktabr' / 'Juma, 9-oktabr' / '9-oktabr 2027' — never 2026-10-09."""
    d = date.fromisoformat(str(value))
    months = MONTHS.get(lang, MONTHS["uz"])
    plain = {"uz": f"{d.day}-{months[d.month - 1]}", "ru": f"{d.day} {months[d.month - 1]}",
             "en": f"{months[d.month - 1]} {d.day}"}.get(lang, f"{d.day}-{months[d.month - 1]}")
    t = date.fromisoformat(str(today)) if today else None
    if t is None:
        return plain
    if d.year != t.year:
        plain += f" {d.year}"
    ahead = (d - t).days
    if ahead in (0, 1):
        return f"{RELATIVE.get(lang, RELATIVE['uz'])[ahead]}, {plain}"
    if 2 <= ahead <= 6:
        return f"{DAYS_FULL.get(lang, DAYS_FULL['uz'])[d.weekday()]}, {plain}"
    return plain


def _amount(value, lang):
    return f'{int(value):,} {SOM.get(lang, "so‘m")}'.replace(",", " ")


def _block(a, lang, today):
    f, op, entity = a["fields"], a["operation"], a["entity"]
    words, before = VALUES.get(lang, VALUES["uz"]), a.get("before") or {}
    detail = DETAIL.get(lang, DETAIL["uz"])
    if entity == "debt":
        heading = HEADINGS.get(lang, HEADINGS["uz"])[("debt", f.get("direction", "lent"))]
        lines = [heading, f'<b>{escape(str(f.get("person", "")))}</b> — {_amount(f.get("amount", 0), lang)}']
        extra = []
        if f.get("deadline"):
            extra.append(f'📅 {human_day(f["deadline"], lang, today)}')
        if f.get("note"):
            extra.append(f'💬 {escape(str(f["note"]))}')
        if extra:
            lines.append("   ".join(extra))
        return "\n".join(lines)
    if entity == "money" and op == "create":
        heading = HEADINGS.get(lang, HEADINGS["uz"])[("money", f.get("kind", "expense"))]
    else:
        heading = HEADINGS.get(lang, HEADINGS["uz"])[(entity, op)]
    if entity == "money":
        name = _amount(f.get("amount", before.get("amount", 0)), lang)
    else:
        name = f.get("title") or f.get("name") or a["name"]
    lines = [heading, f"<b>{escape(str(name))}</b>"]
    if op == "delete":
        if entity == "project":
            lines.append({"uz": "Vazifalari saqlanadi.", "ru": "Задачи сохранятся.", "en": "Its tasks are kept."}.get(lang, ""))
        return "\n".join(lines)
    parts = []
    if entity == "money":
        category = f.get("category") or (before.get("category") if op == "update" else None)
        if category and ("category" in f or op == "create"):
            parts.append(f'{CATEGORY_ICONS.get(category, "")} {words.get(category, category)}'.strip())
        if f.get("day") and str(f["day"]) != str(today):
            parts.append(f'📅 {human_day(f["day"], lang, today)}')
        if f.get("note"):
            parts.append(f'💬 {escape(str(f["note"]))}')
        if "kind" in f and op == "update":
            parts.append(words.get(f["kind"], f["kind"]))
    else:
        for key, value in f.items():
            if key in {"title", "name"} or (op == "create" and (key, value) in HIDDEN):
                continue
            if key in {"deadline"}:
                parts.append(f'📅 {human_day(value, lang, today)}' if value else "📅 —")
            elif key == "due_time":
                parts.append(f"⏰ {value}" if value else "⏰ —")
            elif key == "remind_at":
                parts.append(f"🔔 {value}" if value else "🔔 —")
            elif key == "remind_before":
                parts.append(f'🔔 {detail["before"].format(n=value)}' if value else "🔔 —")
            elif key == "timer_minutes":
                parts.append(f'⏱ {detail["timer"].format(n=value)}' if value else "⏱ —")
            elif key == "project_id":
                parts.append(f'📁 {escape(a.get("project_name") or detail["no_project"])}')
            elif key == "priority":
                parts.append({"high": "🔥 ", "low": "⬇️ "}.get(value, "") + words.get(value, value))
            elif key == "recurrence":
                parts.append(f'🔁 {words.get(value, value)}' if value else "🔁 —")
            elif key == "schedule":
                if str(value).startswith("days:"):
                    value = ", ".join(DAYS_FULL.get(lang, DAYS_FULL["uz"])[int(n)] for n in str(value)[5:].split(","))
                parts.append(f"🗓 {words.get(value, value)}")
            elif key == "category":
                parts.append(words.get(value, value))
            elif key == "start":
                parts.append(detail["start_tomorrow"] if value == "tomorrow" else words.get(value, value))
            elif key == "description":
                parts.append(f"💬 {escape(str(value))}" if value else "💬 —")
    if a["scope"] == "team":
        parts.append(f'👥 {escape(str(a.get("team_name") or ""))}')
    if parts:
        lines.append("   ".join(parts))
    return "\n".join(lines)


def preview(actions, lang="uz", today=None, understood=None):
    """The card the user approves: what was understood, then one block per action."""
    blocks = [_block(a, lang, today) for a in actions]
    head = [f"🎙 <i>{escape(understood.strip())}</i>"] if understood and understood.strip() else []
    return "\n\n".join(head + blocks)
