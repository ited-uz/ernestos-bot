"""
ErnestOS in a plain browser — on a laptop or a phone.

The interface is one page, webapp/index.html. Telegram opens it as a Mini
App; the Android app wraps it with mobile/src/native.js. Here the same page
runs in any browser:

  /app/                       the app: index.html with webapp/web.js in
                              place of Telegram's script
  /app/login                  sign-in (mobile/src/login.html): email code,
                              Google / Apple, or the bot's code
  /app/web.js                 the Telegram.WebApp stand-in for browsers
  /app/sw.js                  service worker: the app's own files, so it
                              opens without a network; never /api
  /app/manifest.webmanifest   install to the home screen or the dock
  /app/icons/*, /app/logo.png

The session is an HttpOnly cookie: no script on the page can read it. The
page sends `X-Telegram-Init-Data: web`, and the middleware below puts the
cookie's `app:<token>` in its place, so every endpoint, gate and limit works
as it does for the phone app. Without that marker header the cookie is
ignored: a form, link or image on another site cannot act as the person
(a custom header needs CORS, which never allows credentials here, and the
cookie is SameSite=Strict besides).
"""
from __future__ import annotations

import glob
import hashlib
import html as _html
import json
import os
import re
import secrets
from datetime import datetime

from fastapi import Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

import app_auth
import config
import mailer
import social_auth
from db import SessionLocal

ROOT = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(ROOT, "webapp", "index.html")
WEB_JS = os.path.join(ROOT, "webapp", "web.js")
SW_JS = os.path.join(ROOT, "webapp", "sw.js")
ICONS = os.path.join(ROOT, "webapp", "icons")
LOGIN = os.path.join(ROOT, "mobile", "src", "login.html")
LOGO = os.path.join(ROOT, "mobile", "src", "logo.png")

#: The marker the page sends instead of a credential.
MARKER = "web"
#: `__Host-` makes the browser refuse the cookie unless it is Secure, for
#: the whole site and from this host only. Plain http (a local run) cannot
#: have that, so it gets the plain name.
COOKIE_SECURE = "__Host-eos"
COOKIE_PLAIN = "eos_session"
#: Not a secret: only tells the page, before it loads anything, that a
#: session is probably there (so a signed-out visit goes to sign-in at once).
COOKIE_HINT = "eos_in"
#: The browser that started a Google/Apple sign-in, so only that browser
#: can finish it.
COOKIE_OAUTH = "eos_oauth"
OAUTH_PATH = "/api/auth/oauth"
SESSION_AGE = app_auth.SESSION_DAYS * 86400
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,100}$")

TELEGRAM_SCRIPT = '<script src="https://telegram.org/js/telegram-web-app.js"></script>'
PHONE_SCRIPTS = '<script src="config.js"></script>\n<script src="native.js"></script>'
_PREVIEW_LOADER = re.compile(r"<script>\s*/\* Design preview only\.[\s\S]*?</script>\s*")
PAGE_HEADERS = {
    "Cache-Control": "no-cache",
    # Never inside someone else's frame: the web app is a page of its own.
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}


def is_https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def set_session(response: Response, request: Request, raw_token: str) -> None:
    """Hand the browser its session. The token never reaches page scripts."""
    secure = is_https(request)
    response.set_cookie(COOKIE_SECURE if secure else COOKIE_PLAIN, raw_token, max_age=SESSION_AGE,
                        path="/", secure=secure, httponly=True, samesite="strict")
    response.set_cookie(COOKIE_HINT, "1", max_age=SESSION_AGE, path="/", secure=secure,
                        httponly=False, samesite="strict")


def clear_session(response: Response, request: Request) -> None:
    secure = is_https(request)
    for name, httponly in ((COOKIE_SECURE, True), (COOKIE_PLAIN, True), (COOKIE_HINT, False)):
        response.delete_cookie(name, path="/", secure=secure or name == COOKIE_SECURE,
                               httponly=httponly, samesite="strict")


def _parse_cookies(raw: str) -> dict:
    out = {}
    for part in raw.split(";"):
        name, _, value = part.strip().partition("=")
        if name and name not in out:
            out[name] = value
    return out


def session_token(cookies: dict) -> str | None:
    value = cookies.get(COOKIE_SECURE) or cookies.get(COOKIE_PLAIN) or ""
    return value if _TOKEN.match(value) else None


class WebSessionMiddleware:
    """`X-Telegram-Init-Data: web` → the session cookie's `app:<token>`.

    Plain ASGI and the outermost layer, so the limits, the gate and every
    handler see the same header the phone app sends. The marker without a
    cookie is dropped, and the request is simply not signed in."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith("/api/"):
            headers = scope.get("headers") or []
            marker = any(k == b"x-telegram-init-data" and v == MARKER.encode() for k, v in headers)
            if marker:
                raw = "; ".join(v.decode("latin-1") for k, v in headers if k == b"cookie")
                token = session_token(_parse_cookies(raw))
                kept = [(k, v) for k, v in headers if k != b"x-telegram-init-data"]
                if token:
                    kept.append((b"x-telegram-init-data", (app_auth.TOKEN_PREFIX + token).encode()))
                scope = dict(scope, headers=kept)
        await self.app(scope, receive, send)


# ---- Google / Apple from the web: the same browser starts and finishes ------

def oauth_start(request: Request) -> tuple[str, str]:
    """A secret for this browser (kept in a cookie) and the state that
    stands for it. Returns (state, binder)."""
    binder = secrets.token_hex(32)
    return social_auth.state_for(binder), binder


def bind_oauth(response: Response, request: Request, binder: str) -> None:
    secure = is_https(request)
    # Apple answers with a cross-site POST: only SameSite=None comes along
    # with it, and that needs Secure. A plain-http local run uses Lax (Google
    # returns with a top-level GET, which Lax allows).
    response.set_cookie(COOKIE_OAUTH, binder, max_age=600, path=OAUTH_PATH, secure=secure,
                        httponly=True, samesite="none" if secure else "lax")


def oauth_binder(request: Request, state: str) -> str | None:
    """The binder, when this browser is the one that started `state`."""
    binder = request.cookies.get(COOKIE_OAUTH, "")
    if binder and re.fullmatch(r"[0-9a-f]{64}", binder) and social_auth.state_for(binder) == state:
        return binder
    return None


def oauth_done(request: Request, raw_token: str | None) -> RedirectResponse:
    """Back to the web app signed in, or to sign-in with the reason."""
    target = "/app/" if raw_token else "/app/login?oauth=failed"
    response = RedirectResponse(target, status_code=303, headers={"Cache-Control": "no-store"})
    response.delete_cookie(COOKIE_OAUTH, path=OAUTH_PATH, secure=is_https(request), httponly=True,
                           samesite="none" if is_https(request) else "lax")
    if raw_token:
        set_session(response, request, raw_token)
    return response


# ---- the pages and their files ------------------------------------------------

_cache: dict = {}


def _read(path: str) -> str:
    stamp = os.path.getmtime(path)
    hit = _cache.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    _cache[path] = (stamp, text)
    return text


def build() -> str:
    """A short fingerprint of the web app's files: a new release is a new
    service worker and a new cache, and nobody keeps an old copy."""
    h = hashlib.sha256()
    for path in (INDEX, WEB_JS, SW_JS, LOGIN):
        h.update(_read(path).encode())
    return h.hexdigest()[:12]


def _head(ver: str) -> str:
    return (
        '<link rel="manifest" href="/app/manifest.webmanifest" />\n'
        '<meta name="theme-color" content="#FFFFFF" media="(prefers-color-scheme: light)" />\n'
        '<meta name="theme-color" content="#1C1C1E" media="(prefers-color-scheme: dark)" />\n'
        '<link rel="icon" type="image/png" sizes="192x192" href="/app/icons/icon-192.png" />\n'
        '<link rel="apple-touch-icon" href="/app/icons/apple-touch-icon.png" />\n'
        '<meta name="mobile-web-app-capable" content="yes" />\n'
        '<meta name="apple-mobile-web-app-capable" content="yes" />\n'
        '<meta name="apple-mobile-web-app-title" content="ErnestOS" />\n'
        f'<script src="/app/web.js?v={ver}"></script>')


def app_page() -> str:
    html = _read(INDEX)
    if TELEGRAM_SCRIPT not in html:
        raise RuntimeError("index.html no longer loads telegram-web-app.js where web_app.py expects it")
    html = html.replace(TELEGRAM_SCRIPT, _head(build()), 1)
    # The design-preview loader (?preview) is for the repository only.
    return _PREVIEW_LOADER.sub("", html, count=1)


def login_page() -> str:
    html = _read(LOGIN)
    if PHONE_SCRIPTS not in html:
        raise RuntimeError("login.html no longer loads config.js and native.js where web_app.py expects it")
    return html.replace(PHONE_SCRIPTS, _head(build()), 1)


def manifest() -> dict:
    return {
        "name": "ErnestOS", "short_name": "ErnestOS",
        "description": "Vazifa, odat, namoz, moliya, qaydlar va maqsadlar — bitta ilovada.",
        "id": "/app/", "start_url": "/app/", "scope": "/app/",
        "display": "standalone", "display_override": ["window-controls-overlay", "standalone"],
        "orientation": "any", "background_color": "#111215", "theme_color": "#111215",
        "lang": "uz", "dir": "ltr", "categories": ["productivity", "lifestyle", "finance"],
        "icons": [
            {"src": "/app/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/app/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/app/icons/maskable-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Bugun", "url": "/app/?s=home"},
            {"name": "Reja", "url": "/app/?s=tracker"},
            {"name": "Qaydlar", "url": "/app/?s=notes"},
            {"name": "Moliya", "url": "/app/?s=money"},
        ],
    }


# ---- a local run: the mailbox on this computer ------------------------------

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def dev_mailbox_on(request: Request | None = None) -> bool:
    """Sign-in codes readable in the browser — only on a local run.

    A local run (`ENVIRONMENT=local`, run_local.py) has no mail service: the
    codes are written to files (EMAIL_PROVIDER=file) and this page shows
    them, like a mailbox on this computer. Three locks: the environment says
    local, mail goes to files, and the request comes from this machine
    itself — straight, not through a proxy, and addressed to localhost. Any
    one missing, and the page does not exist."""
    if config.ENVIRONMENT != "local" or mailer.provider() != "file" or not mailer.configured():
        return False
    if request is None:
        return True
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
    peer = request.client.host if request.client else ""
    forwarded = any(h in request.headers for h in ("x-forwarded-for", "forwarded", "x-real-ip"))
    return peer in _LOOPBACK and host in _LOOPBACK and not forwarded


def _mailbox_page() -> str:
    folder = os.environ.get("EMAIL_OUTBOX_DIR", "")
    rows = []
    for path in sorted(glob.glob(os.path.join(folder, "*.json")), reverse=True)[:12]:
        try:
            with open(path, encoding="utf-8") as f:
                msg = json.load(f)
            when = datetime.fromtimestamp(int(os.path.basename(path)[:-5]) / 1e9).strftime("%H:%M:%S")
        except (OSError, ValueError):
            continue
        code = re.search(r"\b(\d{6})\b", msg.get("subject", ""))
        rows.append(f"<tr><td>{when}</td><td>{_html.escape(msg.get('to', ''))}</td>"
                    f"<td class='code'>{code.group(1) if code else '—'}</td>"
                    f"<td>{_html.escape(msg.get('subject', ''))}</td></tr>")
    body = "".join(rows) or "<tr><td colspan='4' class='empty'>Hali xat yo'q. Kirish sahifasida «Kod yuborish»ni bosing.</td></tr>"
    return ("<!doctype html><html lang='uz'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta http-equiv='refresh' content='3'><title>Lokal pochta — ErnestOS</title>"
            "<style>:root{color-scheme:light dark}body{font:15px/1.5 system-ui,sans-serif;margin:0;padding:24px 16px;"
            "max-width:860px;margin:auto}h1{font-size:20px;margin:0 0 4px}p{color:GrayText;margin:0 0 16px}"
            "table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:10px 8px;border-bottom:1px solid "
            "color-mix(in srgb,currentColor 15%,transparent)}.code{font:700 22px ui-monospace,monospace;letter-spacing:.15em}"
            ".empty{color:GrayText;text-align:center;padding:32px}</style></head><body>"
            "<h1>📬 Lokal pochta qutisi</h1><p>Faqat shu kompyuterda, lokal rejimda. Kodlar 10 daqiqa amal qiladi. "
            "Sahifa o'zi yangilanadi.</p><table><tr><th>Vaqt</th><th>Kimga</th><th>Kod</th><th>Mavzu</th></tr>"
            f"{body}</table></body></html>")


ICON_FILES = {"icon-192.png", "icon-512.png", "maskable-512.png", "apple-touch-icon.png", "favicon-32.png"}


def install(app) -> None:
    @app.get("/app", include_in_schema=False)
    def web_root(request: Request):
        query = request.url.query
        return RedirectResponse("/app/" + (f"?{query}" if query else ""), status_code=308)

    @app.get("/app/", include_in_schema=False)
    def web_app_page():
        return HTMLResponse(app_page(), headers=PAGE_HEADERS)

    @app.get("/app/login", include_in_schema=False)
    def web_login_page():
        return HTMLResponse(login_page(), headers={**PAGE_HEADERS, "Cache-Control": "no-store"})

    @app.get("/app/web.js", include_in_schema=False)
    def web_js():
        return Response(_read(WEB_JS), media_type="text/javascript",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/app/sw.js", include_in_schema=False)
    def web_sw():
        body = _read(SW_JS).replace("__BUILD__", build())
        return Response(body, media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/app/"})

    @app.get("/app/manifest.webmanifest", include_in_schema=False)
    def web_manifest():
        return Response(json.dumps(manifest(), ensure_ascii=False), media_type="application/manifest+json",
                        headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/app/icons/{name}", include_in_schema=False)
    def web_icon(name: str):
        if name not in ICON_FILES:
            raise HTTPException(status_code=404, detail="not_found")
        return FileResponse(os.path.join(ICONS, name), media_type="image/png",
                            headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/app/logo.png", include_in_schema=False)
    def web_logo():
        return FileResponse(LOGO, media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/app/dev/mailbox", include_in_schema=False)
    def web_dev_mailbox(request: Request):
        if not dev_mailbox_on(request):
            raise HTTPException(status_code=404, detail="Not Found")
        return HTMLResponse(_mailbox_page(), headers={**PAGE_HEADERS, "Cache-Control": "no-store"})

    @app.post("/api/web/logout")
    def web_logout(request: Request, init=Header(default=None, alias="X-Telegram-Init-Data")):
        """End this browser's session and take its cookies away."""
        ended = False
        if app_auth.is_app_token(init):
            with SessionLocal() as s:
                ended = app_auth.revoke_token(s, init)
        response = Response(json.dumps({"ok": True, "ended": ended}), media_type="application/json")
        clear_session(response, request)
        return response

    # Outermost: the limits, the gate and the handlers all see the header.
    app.add_middleware(WebSessionMiddleware)
