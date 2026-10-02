"""Bounded task inventory for the Mini App; legacy exports stay complete."""
import base64
import binascii
import json
from datetime import date, time

from sqlalchemy import case, func, or_, select, tuple_

import services as svc
from db import Task


def page(s, ws, *, today, search="", bucket="", cursor="", limit=50, done=False):
    limit = max(1, min(int(limit), 100))
    if bucket not in {"", "today", "overdue", "undated"}:
        raise ValueError("invalid_filter")
    stamp = [str(today), search, bucket, bool(done)]
    high = s.scalar(select(func.max(Task.id)).where(Task.workspace_id == ws)) or 0
    after = None
    if cursor:
        try:
            raw = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            if len(raw) != 6 or raw[0] != stamp:
                raise ValueError()
            high = int(raw[1])
            after = (date.fromisoformat(raw[2]), time.fromisoformat(raw[3]),
                     int(raw[4]), int(raw[5]))
        except (ValueError, TypeError, IndexError, KeyError, UnicodeError, binascii.Error) as exc:
            raise ValueError("invalid_cursor") from exc
    deadline = func.coalesce(Task.deadline, date(9999, 12, 31))
    clock = func.coalesce(Task.due_time, time(23, 59, 59))
    priority = case((Task.priority == "high", 0), (Task.priority == "low", 2), else_=1)
    order = (deadline, clock, priority, Task.id)
    conditions = [Task.workspace_id == ws, Task.archived_at.is_(None),
                  Task.status == ("done" if done else "waiting"), Task.id <= high]
    if search.strip():
        needle = "%" + search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append(or_(Task.title.ilike(needle, escape="\\"),
                              Task.description.ilike(needle, escape="\\")))
    if bucket == "today":
        conditions.append(Task.deadline <= today)
    elif bucket == "overdue":
        conditions.append(Task.deadline < today)
    elif bucket == "undated":
        conditions.append(Task.deadline.is_(None))
    total = s.scalar(select(func.count(Task.id)).where(*conditions)) or 0
    stmt = select(Task).where(*conditions)
    if after:
        stmt = stmt.where(tuple_(*order) > tuple_(*after))
    rows = s.scalars(stmt.order_by(*order).limit(limit + 1)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    runs = svc.open_timer_runs(s, ws, "task")
    countdowns = svc._task_countdowns(s, ws, [row.id for row in rows], today)
    groups = {key: [] for key in ("overdue", "upcoming", "undated", "later")}
    done_groups = {key: [] for key in ("today", "week", "earlier")}
    for row in rows:
        item = svc._task_dict(s, ws, row, today, runs, countdowns)
        key = "undated" if row.deadline is None else "overdue" if row.deadline < today else "upcoming"
        groups[key].append(item)
        if done:
            when = svc.local_date_of(row.completed_at, svc._habit_tz(s, ws))
            key = "today" if when == today else "week" if when and when >= svc.week_start(today) else "earlier"
            done_groups[key].append(item)
    next_cursor = None
    if has_more:
        row = rows[-1]
        raw = [stamp, high, str(row.deadline or date(9999, 12, 31)),
               str(row.due_time or time(23, 59, 59)),
               {"high": 0, "medium": 1, "low": 2}.get(row.priority, 1), row.id]
        next_cursor = base64.urlsafe_b64encode(json.dumps(raw).encode()).decode()
    return {**groups, "groups": done_groups, "total": total, "next_cursor": next_cursor}
