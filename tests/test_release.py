"""Regression checks for the 12.1 release, using isolated fixture accounts."""
from datetime import date, timedelta
from urllib.parse import urlencode

import pytest
from test_smoke import schema, client, fresh, bob, alice, svc, db, deps
from db import SessionLocal, Task, User


def seed(caller, count=137, done=False):
    with SessionLocal() as s:
        ws = svc.workspace_id_for(s, caller.user["id"])
        today = svc.today_local()
        rows = [Task(workspace_id=ws, title=f"Release task {i}",
                     deadline=None if i % 3 == 0 else today + timedelta(days=i % 4 - 1),
                     priority=("high", "medium", "low")[i % 3],
                     status="done" if done else "waiting",
                     completed_at=db.utcnow() if done else None) for i in range(count)]
        s.add_all(rows)
        s.commit()
        return ws, [r.id for r in rows]


def flatten(data):
    return [r for key in ("overdue", "upcoming", "undated", "later") for r in data[key]]


@pytest.mark.parametrize("done", [False, True])
def test_task_pages_cover_all_without_duplicates(fresh, done):
    _, ids = seed(fresh, done=done)
    cursor, seen = "", []
    for _ in range(10):
        r = fresh.get("/api/tasks/page?" + urlencode(dict(limit=23, cursor=cursor, done=str(done).lower())))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["total"] == len(ids)
        batch = flatten(d)
        assert len(batch) <= 23
        seen.extend(x["id"] for x in batch)
        cursor = d["next_cursor"]
        if not cursor:
            break
    assert not cursor
    assert len(seen) == len(set(seen)) == len(ids)
    assert set(seen) == set(ids)


def test_paging_search_filters_and_invalid_cursor(fresh, bob):
    ws, _ = seed(fresh, count=120)
    with SessionLocal() as s:
        s.add(Task(workspace_id=ws, title="literal 100%_match", status="waiting"))
        s.commit()
    d = fresh.get("/api/tasks/page?" + urlencode({"q":"100%_"})).json()
    assert [x["title"] for x in flatten(d)] == ["literal 100%_match"]
    assert bob.get("/api/tasks/page?" + urlencode({"q":"100%_"})).json()["total"] == 0
    d = fresh.get("/api/tasks/page?bucket=undated&limit=100").json()
    assert all(x["deadline"] is None for x in flatten(d))
    assert fresh.get("/api/tasks/page?cursor=garbage").status_code == 422
    assert fresh.get("/api/tasks/page?bucket=unknown").status_code == 422
    first = fresh.get("/api/tasks/page?limit=1").json()
    assert fresh.get("/api/tasks/page?" + urlencode({"cursor":first["next_cursor"], "q":"changed"})).status_code == 422


def test_completed_search_happens_before_limit(fresh):
    ws, ids = seed(fresh, 215, done=True)
    with SessionLocal() as s:
        old = s.get(Task, ids[0])
        old.title = "uniquely old completed"
        old.completed_at = db.utcnow() - timedelta(days=90)
        s.commit()
    d = fresh.get("/api/tasks/done?q=uniquely").json()
    assert [r["id"] for k in ("today", "week", "earlier") for r in d["groups"][k]] == [ids[0]]


def test_money_preview_does_not_write_or_spend_trial(fresh):
    before = fresh.get("/api/money").json()
    with SessionLocal() as s:
        count = s.get(User, fresh.user["id"]).actions_count
    response = fresh.post("/api/money/preview", json={"text":"Tushlikka 45 ming sarfladim", "source":"voice"})
    assert response.status_code == 200, response.text
    d = response.json()
    assert d["amount"] == 45000 and d["kind"] == "expense"
    assert fresh.get("/api/money").json()["balance"] == before["balance"]
    with SessionLocal() as s:
        assert s.get(User, fresh.user["id"]).actions_count == count


def test_normalized_weights_expose_real_calculation():
    assert svc.applied_weights({"tasks":50}) == {"tasks":100.0}
    assert svc.applied_weights({"tasks":None, "habits":None}) == {}
    weights = svc.applied_weights({"tasks":0, "habits":80})
    assert abs(sum(weights.values()) - 100) <= .1


def test_profile_explains_trial_and_assistant(fresh):
    data = fresh.get("/api/me").json()
    assert data["trial"]["free_actions"] == deps.FREE_ACTIONS
    assert isinstance(data["agent"]["available"], bool)
    date.fromisoformat(data["today"])


def test_successful_write_returns_remaining_actions(fresh):
    r = fresh.post("/api/money", json={"kind":"expense", "amount":1000, "category":"food"})
    assert r.status_code == 200, r.text
    assert int(r.headers["X-Trial-Remaining"]) >= 0


def test_cursor_excludes_new_insertions_until_refresh(fresh):
    ws, ids = seed(fresh, 12)
    first = fresh.get("/api/tasks/page?limit=6").json()
    with SessionLocal() as s:
        s.add(Task(workspace_id=ws, title="arrived after page one"))
        s.commit()
    second = fresh.get("/api/tasks/page?" + urlencode({"limit":100, "cursor":first["next_cursor"]})).json()
    assert second["total"] == 12
    assert set(r["id"] for r in flatten(first) + flatten(second)) == set(ids)
    assert fresh.get("/api/tasks/page").json()["total"] == 13


def test_agent_startup_reports_missing_requirements(monkeypatch):
    import config
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    monkeypatch.setattr(config, "BOT_TOKEN", "test")
    monkeypatch.setattr(config, "AGENT_ENABLED", True)
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        config.check()
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr(config.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="FFmpeg"):
        config.check()


def test_cyrillic_search_is_case_insensitive(fresh):
    ws, _ = seed(fresh, 0)
    with SessionLocal() as s:
        s.add(Task(workspace_id=ws, title="ВСТРЕЧА с командой"))
        s.commit()
    r = fresh.get("/api/tasks/page?" + urlencode({"q":"встреча"}))
    assert r.status_code == 200
    assert r.json()["total"] == 1
