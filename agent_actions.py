"""An allowlisted, identity-free command language. Models propose; users authorize.

Neither prompts nor model output can run SQL, arbitrary Python, send messages,
change accounts, or choose a workspace. Both transports use this same executor.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, time
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
                   "start", "day", "kind", "amount", "note"]
    value: str | None


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: Literal["task", "habit", "project", "money"]
    operation: Literal["create", "update", "delete"]
    scope: Literal["personal", "team"]
    team_id: int | None
    target_id: int | None
    changes: list[Change] = Field(max_length=14)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # The input's MAIN language. Uzbek with a few Russian/English words is uz.
    language: Literal["uz", "ru", "en", "other"]
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
    bounds = {"title": 300, "name": 120, "description": 2000, "note": 200}
    if len(raw) > bounds.get(key, 32) or (key in {"title", "name"} and not raw):
        raise AgentError("invalid_fields", 422)
    choices = {"priority": {"low", "medium", "high"}, "kind": {"income", "expense"},
               "start": {"today", "tomorrow"}, "recurrence": {"", "daily", "weekly", "monthly"}}
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
            required = {"task": "title", "habit": "name", "project": "name", "money": "amount"}[entity]
            if not fields.get(required):
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
                     or fields.get("title") or fields.get("name") or entity)
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
    result_id = out.get("id") if isinstance(out, dict) else getattr(out, "id", tid)
    return {"entity": entity, "operation": op, "id": result_id, "name": a["name"]}


LABELS = {
    "uz": {"task": "Vazifa", "habit": "Odat", "project": "Loyiha", "money": "Pul yozuvi",
           "create": "Qo‘shish", "update": "Tahrirlash", "delete": "O‘chirish", "personal": "Shaxsiy", "team": "Jamoa",
           "title": "Nomi", "name": "Nomi", "description": "Izoh", "deadline": "Sana", "due_time": "Vaqt", "priority": "Muhimlik", "project_id": "Loyiha", "recurrence": "Takror", "remind_before": "Eslatma (daqiqa oldin)", "timer_minutes": "Taymer (daqiqa)", "category": "Guruh", "schedule": "Jadval", "remind_at": "Eslatma vaqti", "start": "Boshlanish", "day": "Sana", "kind": "Turi", "amount": "Summa (UZS)", "note": "Izoh"},
    "ru": {"task": "Задача", "habit": "Привычка", "project": "Проект", "money": "Финансы", "create": "Добавить", "update": "Изменить", "delete": "Удалить", "personal": "Личное", "team": "Команда", "title": "Название", "name": "Название", "description": "Описание", "deadline": "Дата", "due_time": "Время", "priority": "Приоритет", "project_id": "Проект", "recurrence": "Повтор", "remind_before": "Напомнить за (мин)", "timer_minutes": "Таймер (мин)", "category": "Категория", "schedule": "Расписание", "remind_at": "Напоминание", "start": "Начало", "day": "Дата", "kind": "Тип", "amount": "Сумма (UZS)", "note": "Заметка"},
    "en": {"task": "Task", "habit": "Habit", "project": "Project", "money": "Money entry", "create": "Create", "update": "Edit", "delete": "Delete", "personal": "Personal", "team": "Team"},
}
VALUES = {"uz": {"daily": "Har kuni", "weekdays": "Dushanba–Juma", "today": "Bugundan", "tomorrow": "Ertadan", "target": "Maqsadli", "non_negotiable": "Majburiy", "bonus": "Bonus", "low": "Past", "medium": "O‘rta", "high": "Yuqori", "expense": "Chiqim", "income": "Kirim", "food": "Oziq-ovqat", "transport": "Transport", "home": "Uy", "health": "Salomatlik", "fun": "Dam olish", "business": "Biznes", "other": "Boshqa", "salary": "Maosh", "sales": "Savdo", "other_in": "Boshqa kirim"}}
VALUES["uz"].update({"weekly": "Har hafta", "monthly": "Har oy"})
VALUES["ru"] = {"daily": "Каждый день", "weekdays": "Понедельник–Пятница", "weekly": "Каждую неделю", "monthly": "Каждый месяц", "today": "С сегодня", "tomorrow": "С завтра", "target": "Целевая", "non_negotiable": "Обязательная", "bonus": "Бонус", "low": "Низкий", "medium": "Средний", "high": "Высокий", "expense": "Расход", "income": "Доход", "food": "Питание", "transport": "Транспорт", "home": "Дом", "health": "Здоровье", "fun": "Отдых", "business": "Бизнес", "other": "Прочее", "salary": "Зарплата", "sales": "Продажи", "other_in": "Прочий доход"}
VALUES["en"] = {"daily": "Every day", "weekdays": "Monday–Friday", "weekly": "Every week", "monthly": "Every month", "today": "Today", "tomorrow": "Tomorrow", "target": "Target", "non_negotiable": "Non-negotiable", "bonus": "Bonus", "low": "Low", "medium": "Medium", "high": "High", "expense": "Expense", "income": "Income", "food": "Food", "transport": "Transport", "home": "Home", "health": "Health", "fun": "Leisure", "business": "Business", "other": "Other", "salary": "Salary", "sales": "Sales", "other_in": "Other income"}
WEEKDAYS = {"uz": ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"], "ru": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"], "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}


def preview(actions, lang="uz"):
    labels = LABELS.get(lang, LABELS["uz"])
    chunks = []
    for i, a in enumerate(actions, 1):
        where = labels[a["scope"]] + (f' · {a["team_name"]}' if a["scope"] == "team" else "")
        lines = [f'{i}. {labels[a["operation"]]} · {labels[a["entity"]]} · {where}', str(a["name"])]
        for key, value in a["fields"].items():
            value = a.get("project_name", value) if key == "project_id" else value
            if key == "project_id" and value is None:
                value = {"uz": "Alohida (yakka vazifa)", "ru": "Отдельные (без проекта)", "en": "Standalone (no project)"}.get(lang, "Alohida")
            value = VALUES.get(lang, {}).get(str(value), value)
            if key == "schedule" and str(value).startswith("days:"):
                value = ", ".join(WEEKDAYS.get(lang, WEEKDAYS["uz"])[int(n)] for n in str(value)[5:].split(","))
            if key == "amount":
                value = f'{value:,}'.replace(",", " ")
            lines.append(f'{labels.get(key, key.replace("_", " ").capitalize())}: {value if value is not None else "—"}')
        if a["entity"] == "project" and a["operation"] == "delete":
            lines.append({"uz": "Loyiha arxivlanadi, vazifalari saqlanadi va loyihadan ajratiladi.", "ru": "Проект архивируется, задачи сохраняются без проекта.", "en": "Project is archived; its tasks are kept, detached from the project."}.get(lang, ""))
        if a["entity"] == "habit" and "schedule" in a["fields"] and a["operation"] == "update":
            lines.append({"uz": "Yangi jadval ertadan kuchga kiradi.", "ru": "Новое расписание действует с завтрашнего дня.", "en": "The new schedule takes effect tomorrow."}.get(lang, ""))
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)
