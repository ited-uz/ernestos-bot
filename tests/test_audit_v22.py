"""v22 audit: one test per hole found by the ten-role audit (docs/AUDIT_V22.md).

Each test reproduces what the audit did by hand and fails on the old code.
"""
from datetime import timedelta

from starlette.requests import Request

# test_smoke sets up the test environment (bot token, database) before the
# app is imported; it has to come first.
from test_smoke import client, fresh, Caller, _next_id  # noqa: F401
from test_smoke import plans_on, _fresh_account, _expire_grants  # noqa: F401
import app as application
import config
import plans
import services as svc


def _request(headers: dict, peer: str = "10.0.0.7") -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/auth/email/start",
                    "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
                    "client": (peer, 443), "query_string": b""})


# --- F1: signed-out callers behind the hosting proxy --------------------------

def test_behind_the_proxy_each_visitor_has_its_own_address(monkeypatch):
    """Every request reaches the app from the proxy's address. Read naively,
    eleven people asking for sign-in codes in ten minutes locked out all
    signed-out users at once."""
    monkeypatch.setattr(config, "TRUST_PROXY", True)
    one = application.client_host(_request({"X-Forwarded-For": "81.95.1.1"}))
    two = application.client_host(_request({"X-Forwarded-For": "81.95.1.2"}))
    assert (one, two) == ("81.95.1.1", "81.95.1.2")
    # The proxy appends the address it saw; whatever the client wrote before
    # it is the client's own word and is not used.
    assert application.client_host(_request({"X-Forwarded-For": "1.1.1.1, 8.8.4.4"})) == "8.8.4.4"
    # Internal hops of the hosting network, after the caller, are skipped.
    assert application.client_host(_request({"X-Forwarded-For": "9.9.9.9, 8.8.4.4, 100.64.0.3, 10.1.2.3"})) == "8.8.4.4"


def test_without_a_proxy_the_header_is_not_believed(monkeypatch):
    monkeypatch.setattr(config, "TRUST_PROXY", False)
    assert application.client_host(_request({"X-Forwarded-For": "81.95.1.1"})) == "10.0.0.7"


def test_sign_in_codes_are_limited_per_visitor_not_for_everyone(client, monkeypatch):
    monkeypatch.setattr(config, "TRUST_PROXY", True)
    monkeypatch.setattr(application, "RATE_LIMIT_ENABLED", True)
    application._buckets.clear()
    try:
        codes = [client.post("/api/auth/email/start", json={"email": f"v{i}@example.com"},
                             headers={"X-Forwarded-For": f"81.95.2.{i + 1}"}).status_code
                 for i in range(12)]
        assert 429 not in codes, codes
        # One visitor is still limited.
        same = [client.post("/api/auth/email/start", json={"email": f"w{i}@example.com"},
                            headers={"X-Forwarded-For": "81.95.3.1"}).status_code
                for i in range(application.RATE_LIMITS["auth"][0] + 1)]
        assert same[-1] == 429
    finally:
        application._buckets.clear()


# --- F12: security headers ------------------------------------------------------

def test_every_answer_carries_the_security_headers(client):
    for path in ("/", "/privacy", "/api/version"):
        r = client.get(path)
        assert r.headers.get("x-content-type-options") == "nosniff", path
        assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin", path
        assert "frame-ancestors" in r.headers.get("content-security-policy", ""), path
        assert "https://web.telegram.org" in r.headers["content-security-policy"], "Telegram Web frames the Mini App"
    assert "strict-transport-security" not in client.get("/api/version").headers
    assert client.get("/api/version", headers={"X-Forwarded-Proto": "https"}).headers.get(
        "strict-transport-security", "").startswith("max-age=")


# --- F7: no marking tomorrow --------------------------------------------------------

def test_prayers_and_journal_refuse_tomorrow_but_keep_the_past(fresh):
    today = svc.today_local()
    tomorrow = (today + timedelta(days=1)).isoformat()
    yesterday = (today - timedelta(days=1)).isoformat()
    assert fresh.post("/api/prayers", json={"prayer": "bomdod", "status": "jamaat", "day": tomorrow}).status_code == 422
    assert fresh.post("/api/prayers/excused", json={"excused": True, "day": tomorrow}).status_code == 422
    assert fresh.post("/api/prayers/clear", json={"prayer": "bomdod", "day": tomorrow}).status_code == 422
    assert fresh.post("/api/journal", json={"answers": {"wins": "x"}, "day": tomorrow}).status_code == 422
    assert fresh.post("/api/prayers", json={"prayer": "bomdod", "status": "jamaat", "day": yesterday}).status_code == 200
    assert fresh.post("/api/journal", json={"answers": {"wins": "x"}, "day": yesterday}).status_code == 200
    assert fresh.post("/api/prayers", json={"prayer": "bomdod", "status": "jamaat"}).status_code == 200


# --- F11: Free recurring limit on edit -------------------------------------------------

def test_free_cannot_turn_one_off_tasks_into_repeating_ones_past_the_limit(client, plans_on):
    uid = 7601
    _fresh_account(uid)
    _expire_grants(uid)
    caller = Caller(client, {"id": uid, "first_name": "P"})
    limit = plans.LIMITS["recurring_tasks"]["free"]
    for i in range(limit):
        assert caller.post("/api/tasks", json={"title": f"R{i}", "recurrence": "daily"}).status_code == 200
    one_off = caller.post("/api/tasks", json={"title": "Bir martalik"}).json()["id"]
    r = caller.patch(f"/api/tasks/{one_off}", json={"title": "Bir martalik", "recurrence": "weekly"})
    assert r.status_code == 402 and r.json()["key"] == "recurring_tasks"
    # Editing a task that already repeats is not a new repeating task.
    rows = caller.get("/api/tasks?days=365").json()
    rec = next(x for g in ("overdue", "upcoming", "undated", "later") for x in rows.get(g, []) if x["title"] == "R0")
    assert caller.patch(f"/api/tasks/{rec['id']}", json={"title": "R0", "recurrence": "weekly"}).status_code == 200


# --- F8: removing an account that still holds money ----------------------------------

def test_removing_an_account_moves_all_its_money_to_unassigned(fresh):
    """Its opening amount used to fall out of the total, so removing a card
    holding 1 000 made a third of the money vanish."""
    fresh.post("/api/money/accounts", json={"name": "Karta", "kind": "card", "opening": 1000})
    fresh.post("/api/money/accounts", json={"name": "Naqd", "kind": "cash", "opening": 500})
    w = fresh.get("/api/money/wallet").json()
    card = next(a for a in w["accounts"] if a["name"] == "Karta")["id"]
    cash = next(a for a in w["accounts"] if a["name"] == "Naqd")["id"]
    assert fresh.post("/api/money/transfers", json={"from_account": card, "to_account": cash, "amount": 200}).status_code == 200
    before = fresh.get("/api/money/wallet").json()
    assert before["total"] == 1500
    assert fresh.patch(f"/api/money/accounts/{card}", json={"archived": True}).status_code == 200
    after = fresh.get("/api/money/wallet").json()
    assert after["total"] == 1500, "removing an account does not remove money"
    assert [a["balance"] for a in after["accounts"]] == [700]
    assert after["unassigned"] == 800
    assert fresh.get("/api/money").json()["balance"] == 1500


# --- F2 / F3: team invites ---------------------------------------------------------------

def test_a_removed_member_asks_again_instead_of_walking_back_in(client, plans_on):
    owner_id, member_id = 7611, 7612
    _fresh_account(owner_id)
    owner = Caller(client, {"id": owner_id, "first_name": "Owner"})
    member = Caller(client, {"id": member_id, "first_name": "Member"})
    team = owner.post("/api/teams", json={"name": "Jamoa"}).json()
    code = next(t for t in owner.get("/api/teams").json()["teams"] if t["id"] == team["id"])["invite_code"]
    assert code, "the code is there even when the bot has no @name"
    assert member.post("/api/teams/join", json={"code": code}).json()["outcome"] == "joined"
    assert client.delete(f"/api/teams/{team['id']}/members/{member_id}", headers=owner.h).status_code == 200
    again = member.post("/api/teams/join", json={"code": code}).json()
    assert again["outcome"] == "requested"
    assert all(t["id"] != team["id"] for t in member.get("/api/teams").json()["teams"])
    # Somebody who left on their own comes back freely.
    other = Caller(client, {"id": 7613, "first_name": "Other"})
    assert other.post("/api/teams/join", json={"code": code}).json()["outcome"] == "joined"
    assert client.delete(f"/api/teams/{team['id']}", headers=other.h).status_code == 200
    assert other.post("/api/teams/join", json={"code": code}).json()["outcome"] == "joined"


def test_only_those_who_may_invite_see_the_code(client, plans_on):
    _fresh_account(7621)
    owner = Caller(client, {"id": 7621, "first_name": "Owner"})
    member = Caller(client, {"id": 7622, "first_name": "Member"})
    team = owner.post("/api/teams", json={"name": "Jamoa"}).json()
    code = next(t for t in owner.get("/api/teams").json()["teams"] if t["id"] == team["id"])["invite_code"]
    member.post("/api/teams/join", json={"code": code})
    seen = next(t for t in member.get("/api/teams").json()["teams"] if t["id"] == team["id"])
    assert seen["invite_code"] is None


# --- Fuzzing: an id no row can have ---------------------------------------------------

def test_an_impossible_id_is_not_found_not_a_server_error(fresh):
    huge = "9" * 20
    for method, path in (("PATCH", f"/api/money/{huge}"), ("DELETE", f"/api/people/{huge}"),
                         ("DELETE", f"/api/projects/{huge}"), ("GET", f"/api/teams/tasks/{huge}"),
                         ("DELETE", f"/api/debts/{huge}/purge")):
        r = fresh.c.request(method, path, headers=fresh.h, json={} if method == "PATCH" else None)
        assert r.status_code == 404, (method, path, r.status_code)


# --- F4: bot confirmation escapes the title ----------------------------------------

def test_bot_task_confirmation_escapes_a_personal_title():
    src = open(application.__file__, encoding="utf-8").read()
    assert 't(lang, "task_added", title=esc(title))' in src


# --- F13: the setup guide names the address that is served ---------------------------

def test_setup_guide_points_webapp_url_at_the_served_address(client):
    guide = open("docs/ISHGA_TUSHIRISH.md", encoding="utf-8").read()
    assert "railway.app/webapp`)" not in guide
    assert client.get("/webapp").status_code == 404 and client.get("/").status_code == 200


# --- F5: "har kuni" in quick add is a repeating task ------------------------------------

def test_quick_add_reads_how_often_from_the_words():
    from datetime import date, time
    today = date(2026, 10, 17)
    cases = {"har kuni 07:00 yugurish": ("yugurish", "daily"),
             "Har hafta dushanba hisobot": ("hisobot", "weekly"),
             "har oy ijara to'lash": ("ijara to'lash", "monthly"),
             "ish kunlari 9:00 standup": ("standup", "weekdays"),
             "каждый день читать": ("читать", "daily"),
             "every week call mom": ("call mom", "weekly"),
             # "Kundalik" is also the diary; it is not read as "daily".
             "kundalik yozish": ("kundalik yozish", ""),
             "Aziz bilan uchrashuv": ("Aziz bilan uchrashuv", "")}
    for text, (title, rule) in cases.items():
        got = svc.parse_quick_capture(text, today, time(6, 0))
        assert (got["title"], got["recurrence"]) == (title, rule), text


def test_quick_add_saves_a_repeating_task_starting_today(fresh):
    r = fresh.post("/api/quick", json={"title": "har oy ijara to'lash"}).json()
    assert r["ok"] and r["recurrence"] == "monthly" and r["title"] == "ijara to'lash"
    rows = fresh.get("/api/tasks?days=365").json()
    task = next(x for g in ("overdue", "upcoming", "undated", "later") for x in rows.get(g, []) if x["id"] == r["id"])
    assert task["recurrence"] == "monthly" and task["deadline"] == svc.today_local().isoformat()


def test_blank_feedback_is_refused_not_a_server_error(fresh):
    assert fresh.post("/api/feedback", json={"message": "   "}).status_code == 422


# --- Voice/quick lines found while filling ten accounts ----------------------------------

def test_day_of_month_and_part_of_day_are_read_not_kept_in_the_title():
    from datetime import date, time
    today, noon = date(2026, 10, 17), time(12, 0)
    got = svc.parse_quick_capture("har oy 5-sanada ijara to'lash", today, noon)
    assert (got["title"], got["recurrence"], got["deadline"]) == ("ijara to'lash", "monthly", date(2026, 11, 5))
    assert svc.parse_quick_capture("har oy 20-sanada maosh", today, noon)["deadline"] == date(2026, 10, 20)
    assert svc.parse_quick_capture("5-числа оплата", today, noon)["deadline"] == date(2026, 11, 5)
    evening = svc.parse_quick_capture("bugun kechqurun 20:00 kitob o'qish", today, noon)
    assert (evening["title"], evening["due_time"]) == ("kitob o'qish", time(20, 0))
    assert svc.parse_quick_capture("kechqurun 8:00 sport", today, noon)["due_time"] == time(20, 0)
    # A repeat with no day named starts today, and says so.
    assert svc.parse_quick_capture("har hafta hisobot", today, noon)["deadline"] == today
