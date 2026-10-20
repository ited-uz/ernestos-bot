"""The web app (/app/): the pages and their files, the session cookie that
page scripts cannot read, the marker header that keeps other sites out, and
Google/Apple finishing only in the browser that started them."""
import json
import re
import uuid
from urllib.parse import parse_qs, urlparse

# test_smoke sets up the environment (bot token, database) before the app is imported.
from test_smoke import client, _mailbox, _oidc_keys  # noqa: F401
import social_auth
import web_app
from db import AppSession, OAuthPending, SessionLocal
from sqlalchemy import select

WEB = {"X-Telegram-Init-Data": "web"}


def _cookie_lines(response):
    return [v for k, v in response.headers.multi_items() if k.lower() == "set-cookie"]


def _email_session(client, monkeypatch, tmp_path, email=None):
    # A fresh address each time: one address may ask for a code once a minute.
    email = email or f"web-{uuid.uuid4().hex[:10]}@example.com"
    last_code = _mailbox(monkeypatch, tmp_path)
    assert client.post("/api/auth/email/start", json={"email": email}).status_code == 200
    r = client.post("/api/auth/email/verify", json={"email": email, "code": last_code(email),
                                                    "device": "web · test", "web": True})
    assert r.status_code == 200, r.text
    return r


# --- the pages ------------------------------------------------------------------

def test_the_web_app_and_its_files_are_served(client):
    r = client.get("/app", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/app/"
    assert client.get("/app?s=notes", follow_redirects=False).headers["location"] == "/app/?s=notes"

    page = client.get("/app/")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    html = page.text
    assert "telegram-web-app.js" not in html, "no Telegram script in the browser"
    assert re.search(r'<script src="/app/web\.js\?v=[0-9a-f]{12}"></script>', html)
    assert '<link rel="manifest" href="/app/manifest.webmanifest"' in html
    assert "Design preview only" not in html and "preview.js" not in html
    assert page.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert page.headers["cache-control"] == "no-cache"

    login = client.get("/app/login")
    assert login.status_code == 200
    assert "native.js" not in login.text and "config.js" not in login.text
    assert "/app/web.js?v=" in login.text and login.headers["cache-control"] == "no-store"

    js = client.get("/app/web.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert "isErnestWeb" in js.text

    sw = client.get("/app/sw.js")
    assert sw.status_code == 200 and "__BUILD__" not in sw.text
    assert web_app.build() in sw.text and sw.headers["service-worker-allowed"] == "/app/"
    assert "/api/" not in re.sub(r"/\*[\s\S]*?\*/", "", sw.text).replace('"/api/"', ""), \
        "the service worker never caches the API"

    m = client.get("/app/manifest.webmanifest")
    assert m.headers["content-type"].startswith("application/manifest+json")
    manifest = m.json()
    assert manifest["start_url"] == "/app/" and manifest["scope"] == "/app/"
    assert manifest["display"] == "standalone"
    sizes = {i["sizes"] for i in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes and any(i["purpose"] == "maskable" for i in manifest["icons"])
    for icon in manifest["icons"]:
        got = client.get(icon["src"])
        assert got.status_code == 200 and got.content[:8] == b"\x89PNG\r\n\x1a\n", icon["src"]
    assert client.get("/app/icons/../web_app.py").status_code == 404
    assert client.get("/app/icons/nope.png").status_code == 404
    assert client.get("/app/logo.png").content[:4] == b"\x89PNG"


def test_the_mini_app_address_still_serves_the_mini_app(client):
    """Telegram keeps its page; only a plain browser is sent on to /app/
    (by the page itself, which sees there is no Telegram around it)."""
    r = client.get("/")
    assert r.status_code == 200 and "telegram-web-app.js" in r.text
    assert 'location.replace("/app/"' in r.text


# --- the session ------------------------------------------------------------------

def test_email_sign_in_from_the_web_gives_a_cookie_and_no_token(client, monkeypatch, tmp_path):
    r = _email_session(client, monkeypatch, tmp_path)
    body = r.json()
    assert body == {"ok": True, "new": True}, "the token never reaches the page"
    cookies = _cookie_lines(r)
    session = next(c for c in cookies if c.startswith(web_app.COOKIE_PLAIN + "="))
    assert "HttpOnly" in session and "SameSite=strict" in session and "Path=/" in session
    hint = next(c for c in cookies if c.startswith(web_app.COOKIE_HINT + "=1"))
    assert "HttpOnly" not in hint
    me = client.get("/api/me", headers=WEB)
    assert me.status_code == 200 and me.json()["email"].startswith("web-")
    # The same sign-in from the phone still answers with a token.
    with SessionLocal() as s:
        assert s.scalar(select(AppSession.device).order_by(AppSession.id.desc())) == "web · test"


def test_the_cookie_alone_signs_in_nothing(client, monkeypatch, tmp_path):
    """Another site can make the browser send the cookie (well, not with
    SameSite=Strict — but even so): without the marker header nothing is
    signed in, and a write is refused."""
    _email_session(client, monkeypatch, tmp_path)
    r = client.post("/api/onboarding", json={"first_name": "Mallory", "language": "uz"})
    assert r.status_code == 401
    assert client.get("/api/me").status_code == 401
    # With the marker the same request works.
    ok = client.post("/api/onboarding", headers=WEB, json={"first_name": "Webby", "language": "uz"})
    assert ok.status_code == 200, ok.text
    assert client.get("/api/me", headers=WEB).json()["first_name"] == "Webby"


def test_the_marker_without_a_cookie_is_simply_signed_out(client):
    client.cookies.clear()
    r = client.get("/api/me", headers=WEB)
    assert r.status_code == 401 and r.json()["detail"] == "unauthorized"
    client.cookies.set(web_app.COOKIE_PLAIN, "not a token; x=1")
    assert client.get("/api/me", headers=WEB).status_code == 401
    client.cookies.clear()


def test_over_https_the_cookie_is_host_only_and_secure(client, monkeypatch, tmp_path):
    last_code = _mailbox(monkeypatch, tmp_path)
    client.post("/api/auth/email/start", json={"email": "secure@example.com"})
    r = client.post("/api/auth/email/verify", headers={"X-Forwarded-Proto": "https"},
                    json={"email": "secure@example.com", "code": last_code("secure@example.com"), "web": True})
    session = next(c for c in _cookie_lines(r) if c.startswith("__Host-eos="))
    assert "Secure" in session and "HttpOnly" in session and "Domain" not in session
    token = session.split(";")[0].split("=", 1)[1]
    # The middleware reads the __Host- name too.
    assert client.get("/api/me", headers={**WEB, "Cookie": f"__Host-eos={token}"}).status_code == 200


def test_signing_out_ends_the_session_for_good(client, monkeypatch, tmp_path):
    _email_session(client, monkeypatch, tmp_path)
    token = client.cookies.get(web_app.COOKIE_PLAIN)
    out = client.post("/api/web/logout", headers=WEB)
    assert out.status_code == 200 and out.json()["ended"] is True
    cleared = _cookie_lines(out)
    assert any(c.startswith(web_app.COOKIE_PLAIN + "=") and "Max-Age=0" in c for c in cleared)
    assert client.get("/api/me", headers=WEB).status_code == 401
    # The old value, sent again by hand, is dead too.
    assert client.get("/api/me", headers={**WEB, "Cookie": f"{web_app.COOKIE_PLAIN}={token}"}).status_code == 401


def test_the_bot_code_signs_the_web_in_too(client):
    import app_auth
    with SessionLocal() as s:
        code = app_auth.issue_code(s, 7701)
    r = client.post("/api/app/login", json={"code": code, "device": "web", "web": True})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert client.get("/api/me", headers=WEB).json()["telegram_id"] == 7701
    client.cookies.clear()


def test_a_wrong_code_is_not_a_signed_out_session(client, monkeypatch, tmp_path):
    """web.js (and native.js) sign out only on the server's own
    "unauthorized"; a wrong code is a 401 with another reason."""
    _email_session(client, monkeypatch, tmp_path)
    r = client.post("/api/account/email/verify", headers=WEB, json={"email": "other@example.com", "code": "000000"})
    assert r.status_code == 401 and r.json()["detail"] == "bad_code"
    assert client.get("/api/me", headers=WEB).status_code == 200
    js = client.get("/app/web.js").text
    assert 'b.detail === "unauthorized"' in js
    client.cookies.clear()


# --- Google / Apple from the web ------------------------------------------------

def _google(monkeypatch):
    sign = _oidc_keys(monkeypatch)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "web.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "s")
    monkeypatch.setenv("PUBLIC_URL", "https://ernestos.example")
    return sign


def _start(client):
    r = client.get("/api/auth/oauth/google/start?web=1&lang=en", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://accounts.google.com/")
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    bind = next(c for c in _cookie_lines(r) if c.startswith(web_app.COOKIE_OAUTH + "="))
    assert "HttpOnly" in bind and "Path=/api/auth/oauth" in bind
    with SessionLocal() as s:
        row = s.get(OAuthPending, social_auth._digest(state))
        assert row.web is True
        return state, row.nonce


def test_google_from_the_web_lands_in_the_same_browser_with_a_cookie(client, monkeypatch):
    sign = _google(monkeypatch)
    client.cookies.clear()
    state, nonce = _start(client)
    monkeypatch.setattr(social_auth, "google_code_to_token", lambda code: sign({
        "iss": "https://accounts.google.com", "aud": "web.apps.googleusercontent.com", "sub": "g-web-1",
        "email": "gweb@example.com", "email_verified": True, "nonce": nonce}))
    r = client.get(f"/api/auth/oauth/google/callback?state={state}&code=c", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/app/"
    assert any(c.startswith(web_app.COOKIE_PLAIN + "=") for c in _cookie_lines(r))
    assert client.get("/api/me", headers=WEB).json()["email"] == "gweb@example.com"
    # Nothing was left behind for anyone to collect.
    verifier_guess = client.post("/api/auth/oauth/poll", json={"verifier": state}).json()
    assert verifier_guess["status"] in ("unknown", "error") and "token" not in verifier_guess
    client.cookies.clear()


def test_a_web_sign_in_link_finished_in_another_browser_gives_nobody_a_session(client, monkeypatch):
    """Someone starts a web sign-in and sends the Google page to another
    person. That person signs in — and the session goes nowhere: not to
    their browser (it did not start it), not to the sender's poll."""
    sign = _google(monkeypatch)
    client.cookies.clear()
    state, nonce = _start(client)
    binder = client.cookies.get(web_app.COOKIE_OAUTH)
    client.cookies.clear()                       # the other person's browser
    with SessionLocal() as s:
        before = len(s.scalars(select(AppSession.id)).all())
    monkeypatch.setattr(social_auth, "google_code_to_token", lambda code: sign({
        "iss": "https://accounts.google.com", "aud": "web.apps.googleusercontent.com", "sub": "g-victim",
        "email": "victim@example.com", "email_verified": True, "nonce": nonce}))
    r = client.get(f"/api/auth/oauth/google/callback?state={state}&code=c", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/app/login?oauth=failed"
    assert not any(c.startswith(web_app.COOKIE_PLAIN + "=") for c in _cookie_lines(r))
    assert client.get("/api/me", headers=WEB).status_code == 401
    # The sender holds the secret, and still gets nothing.
    got = client.post("/api/auth/oauth/poll", json={"verifier": binder}).json()
    assert "token" not in got and got["status"] in ("error", "unknown")
    with SessionLocal() as s:
        assert len(s.scalars(select(AppSession.id)).all()) == before, "no session was made at all"


def test_a_cancelled_web_sign_in_goes_back_to_sign_in(client, monkeypatch):
    _google(monkeypatch)
    client.cookies.clear()
    state, _ = _start(client)
    r = client.get(f"/api/auth/oauth/google/callback?state={state}&error=access_denied", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/app/login?oauth=failed"
    assert any(c.startswith(web_app.COOKIE_OAUTH + "=") and "Max-Age=0" in c for c in _cookie_lines(r))
    client.cookies.clear()


def test_the_phone_app_sign_in_is_unchanged(client, monkeypatch, tmp_path):
    """Without the web flag the answer carries the token, as the app expects."""
    last_code = _mailbox(monkeypatch, tmp_path)
    client.cookies.clear()
    client.post("/api/auth/email/start", json={"email": "phone@example.com"})
    r = client.post("/api/auth/email/verify", json={"email": "phone@example.com",
                                                    "code": last_code("phone@example.com")})
    assert r.json()["token"].startswith("app:") and not _cookie_lines(r)
    assert json.loads(client.get("/app/manifest.webmanifest").text)["name"] == "ErnestOS"


# --- a local run (run_local.py) ------------------------------------------------

class _Req:
    """Just enough of a request for dev_mailbox_on."""
    def __init__(self, peer="127.0.0.1", host="localhost:8000", headers=None):
        self.client = type("C", (), {"host": peer})()
        self.headers = {"host": host, **(headers or {})}


def test_the_local_mailbox_exists_only_on_this_computer(client, monkeypatch, tmp_path):
    import config
    _mailbox(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "ENVIRONMENT", "local")
    assert web_app.dev_mailbox_on(_Req())
    assert web_app.dev_mailbox_on(_Req(peer="::1", host="[::1]:8000"))
    # From another machine, through a proxy, or under another name: no.
    assert not web_app.dev_mailbox_on(_Req(peer="192.168.1.20", host="192.168.1.5:8000"))
    assert not web_app.dev_mailbox_on(_Req(headers={"x-forwarded-for": "1.2.3.4"}))
    assert not web_app.dev_mailbox_on(_Req(host="ernestos.example"))
    # The test client is not this computer's loopback: the page is not there.
    assert client.get("/app/dev/mailbox").status_code == 404
    assert "dev_mailbox" not in client.get("/api/app/config").json()
    # Not a local run: never, whoever asks.
    monkeypatch.setattr(config, "ENVIRONMENT", "development")
    assert not web_app.dev_mailbox_on(_Req())
    monkeypatch.setattr(config, "ENVIRONMENT", "local")
    monkeypatch.setenv("EMAIL_PROVIDER", "smtp")
    assert not web_app.dev_mailbox_on(_Req())


def test_the_local_mailbox_page_shows_the_newest_codes(monkeypatch, tmp_path):
    (tmp_path / "1700000000000000000.json").write_text(json.dumps(
        {"to": "a@b.c", "subject": "123456 — ErnestOS kodi", "text": "", "html": ""}))
    (tmp_path / "1700000001000000000.json").write_text(json.dumps(
        {"to": "<x>@b.c", "subject": "654321 — ErnestOS kodi", "text": "", "html": ""}))
    monkeypatch.setenv("EMAIL_OUTBOX_DIR", str(tmp_path))
    page = web_app._mailbox_page()
    assert page.index("654321") < page.index("123456"), "newest first"
    assert "&lt;x&gt;@b.c" in page and "<x>" not in page


def test_local_mode_refuses_to_start_on_a_server(monkeypatch):
    import config
    import pytest
    monkeypatch.setattr(config, "ENVIRONMENT", "local")
    monkeypatch.setenv("PUBLIC_URL", "http://localhost:8000")
    config.check()                                   # this computer: fine
    monkeypatch.setenv("PUBLIC_URL", "https://ernestos.example")
    with pytest.raises(RuntimeError, match="ENVIRONMENT=local"):
        config.check()
    monkeypatch.setenv("PUBLIC_URL", "http://localhost:8000")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="ENVIRONMENT=local"):
        config.check()


def test_without_a_bot_reminders_reach_the_app_inbox(client):
    """No BOT_TOKEN (a local run): the jobs run with OfflineBot, and what
    they say goes to the inbox of whoever uses the app — never lost, never
    retried in a loop."""
    import asyncio
    import app as application
    import app_auth
    import pytest
    from db import AppNotification
    from telegram.error import Forbidden
    with SessionLocal() as s:
        code = app_auth.issue_code(s, 7702)
    client.post("/api/app/login", json={"code": code, "device": "web", "web": True})
    fan = application.AccountFanOut(application.OfflineBot(), "reminder")
    assert asyncio.run(fan.send_message(7702, "🔔 Vazifa vaqti: <b>Hisobot</b>")) is None
    with SessionLocal() as s:
        row = s.scalar(select(AppNotification).where(AppNotification.account_id == 7702)
                       .order_by(AppNotification.id.desc()))
        assert row is not None and "Hisobot" in (row.body or row.title or "")
    # Somebody without the app: refused the way Telegram refuses, so the
    # caller files it as undeliverable instead of retrying every tick.
    with pytest.raises(Forbidden):
        asyncio.run(fan.send_message(7703, "x"))
    client.cookies.clear()
