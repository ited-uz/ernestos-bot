"""
ErnestOS — notifications for the phone app.

Everything the bot tells an account on its own — reports, reminders, timers,
team news — also reaches the account's phones:

  * an **inbox** row (`AppNotification`), which the app lists under "Xabarlar"
    whether or not push is set up;
  * a **push** through Firebase Cloud Messaging to every registered phone,
    when the server has `FCM_SERVICE_ACCOUNT`.

Only accounts with a live app session get either, so a Telegram-only account
costs one indexed query per message and nothing else.

A message's buttons travel with it when the app can run them itself: snooze
(15 / 60 / 180 min) and "done" for a task or habit. "Done" only ever ticks,
like the timer message's button — a second tap never unticks.

`deliver` never raises: a notification that could not be stored or pushed is
logged, and the Telegram message it accompanies is unaffected.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import re
import time
from datetime import timedelta

import httpx
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

import accounts
import config
from db import AppNotification, AppSession, PushDevice, SessionLocal, utcnow

log = logging.getLogger("ernestos")

#: Inbox rows older than this are removed by the nightly close.
INBOX_KEEP_DAYS = 60
#: How many rows one inbox request returns.
INBOX_PAGE = 100
#: Buttons the app can run from a notification. Anything else in a bot
#: keyboard (open the Mini App, menus) is Telegram's business.
ACTION_RE = re.compile(
    r"^(?:snz:[htHT]:\d+:\d+|task:done:\d+|ttask:(?:done|toggle):\d+"
    r"|habit:toggle:\d+|thabit:toggle:\d+)$")

_TAG_RE = re.compile(r"<[^>]+>")
_BREAK_RE = re.compile(r"<br\s*/?>", re.I)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def plain_text(text: str | None) -> str:
    """A bot message's HTML as the plain text a phone notification shows."""
    raw = _BREAK_RE.sub("\n", str(text or ""))
    raw = html.unescape(_TAG_RE.sub("", raw))
    lines = [" ".join(line.split()) for line in raw.split("\n")]
    # Keep paragraph breaks, drop runs of empty lines.
    out, blank = [], False
    for line in lines:
        if not line:
            if out and not blank:
                out.append("")
            blank = True
            continue
        out.append(line)
        blank = False
    return "\n".join(out).strip()


def split_title(text: str) -> tuple[str, str]:
    """First line as the title, the rest as the body."""
    first, _, rest = text.partition("\n")
    return first[:160], rest.strip()


def actions_from(markup) -> list[dict]:
    """The buttons of an inline keyboard the app knows how to run."""
    found = []
    for row in getattr(markup, "inline_keyboard", None) or []:
        for button in row:
            data = getattr(button, "callback_data", None)
            if isinstance(data, str) and ACTION_RE.match(data):
                found.append({"label": str(getattr(button, "text", ""))[:40], "cb": data})
    return found[:6]


# ---------------------------------------------------------------------------
# Who has the app
# ---------------------------------------------------------------------------

def account_telegrams(s: Session, account_id: int) -> list[int]:
    return [int(account_id), *accounts.linked_ids(s, int(account_id))]


def has_app(s: Session, account_id: int) -> bool:
    now = utcnow()
    return bool(s.scalar(select(AppSession.id).where(
        AppSession.telegram_id.in_(account_telegrams(s, account_id)),
        AppSession.revoked_at.is_(None),
        AppSession.expires_at > now).limit(1)))


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------

def record(s: Session, account_id: int, text: str, markup=None, *,
           kind: str = "bot") -> AppNotification:
    title, body = split_title(plain_text(text))
    row = AppNotification(account_id=int(account_id), kind=kind[:24], title=title,
                          body=body, actions=json.dumps(actions_from(markup),
                                                        ensure_ascii=False))
    s.add(row)
    s.commit()
    return row


def as_dict(row: AppNotification) -> dict:
    try:
        actions = json.loads(row.actions or "[]")
    except ValueError:
        actions = []
    return {"id": row.id, "kind": row.kind, "title": row.title, "body": row.body,
            "actions": actions, "read": row.read_at is not None,
            "created_at": row.created_at.isoformat() + "Z"}


def inbox(s: Session, account_id: int, limit: int = INBOX_PAGE) -> dict:
    rows = s.scalars(select(AppNotification)
                     .where(AppNotification.account_id == account_id)
                     .order_by(AppNotification.id.desc())
                     .limit(max(1, min(limit, INBOX_PAGE)))).all()
    unread = s.scalar(select(func.count(AppNotification.id)).where(
        AppNotification.account_id == account_id,
        AppNotification.read_at.is_(None))) or 0
    return {"items": [as_dict(r) for r in rows], "unread": int(unread)}


def mark_read(s: Session, account_id: int, ids: list[int] | None = None) -> int:
    """Mark these (or, with no ids, all) of the account's rows read."""
    query = (update(AppNotification)
             .where(AppNotification.account_id == account_id,
                    AppNotification.read_at.is_(None)))
    if ids is not None:
        query = query.where(AppNotification.id.in_([int(i) for i in ids][:200]))
    done = s.execute(query.values(read_at=utcnow())).rowcount or 0
    s.commit()
    return done


def get_owned(s: Session, account_id: int, notification_id: int) -> AppNotification | None:
    row = s.get(AppNotification, notification_id)
    return row if row is not None and row.account_id == account_id else None


def cleanup(s: Session) -> int:
    cutoff = utcnow() - timedelta(days=INBOX_KEEP_DAYS)
    gone = s.execute(sql_delete(AppNotification)
                     .where(AppNotification.created_at < cutoff)).rowcount or 0
    s.commit()
    return gone


def forget(s: Session, ids) -> None:
    """Every notification and device of these accounts. The caller commits."""
    ids = list(ids)
    if not ids:
        return
    s.execute(sql_delete(AppNotification).where(AppNotification.account_id.in_(ids)))
    s.execute(sql_delete(PushDevice).where(PushDevice.account_id.in_(ids)))


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------

def register_device(s: Session, account_id: int, session_id: int | None,
                    token: str, platform: str) -> PushDevice:
    """Store or move a phone's push token. A token belongs to one phone, so a
    phone that signs in to another account takes its token along."""
    token = str(token).strip()[:512]
    now = utcnow()
    row = s.scalar(select(PushDevice).where(PushDevice.token == token))
    if row is None:
        row = PushDevice(token=token)
        s.add(row)
    row.account_id = int(account_id)
    row.session_id = session_id
    row.platform = (platform or "android")[:16]
    row.last_seen_at = now
    row.disabled_at = None
    s.commit()
    return row


def disable_session_devices(s: Session, session_ids) -> None:
    """Sign-out: that phone stops getting pushes. The caller commits."""
    ids = list(session_ids)
    if ids:
        s.execute(update(PushDevice).where(PushDevice.session_id.in_(ids),
                                           PushDevice.disabled_at.is_(None))
                  .values(disabled_at=utcnow()))


def device_tokens(s: Session, account_id: int) -> list[str]:
    return list(s.scalars(select(PushDevice.token).where(
        PushDevice.account_id == account_id, PushDevice.disabled_at.is_(None))).all())


def disable_token(token: str) -> None:
    with SessionLocal() as s:
        s.execute(update(PushDevice).where(PushDevice.token == token)
                  .values(disabled_at=utcnow()))
        s.commit()


# ---------------------------------------------------------------------------
# Firebase Cloud Messaging (HTTP v1)
# ---------------------------------------------------------------------------

class FCM:
    """Sends through FCM's HTTP v1 API with a service-account key.

    The key comes from `FCM_SERVICE_ACCOUNT`: the JSON file Firebase gives
    (Project settings → Service accounts → Generate new private key), pasted
    as is or base64-encoded. Without it push is off and the inbox still works.
    """

    SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

    def __init__(self, raw: str | None):
        self.account = self._parse(raw)
        self._token: str | None = None
        self._token_until = 0.0

    @staticmethod
    def _parse(raw: str | None) -> dict | None:
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            if not raw.startswith("{"):
                raw = base64.b64decode(raw).decode("utf-8")
            data = json.loads(raw)
            if all(data.get(k) for k in ("project_id", "client_email", "private_key")):
                return data
        except Exception:
            pass
        log.error("FCM_SERVICE_ACCOUNT is set but is not a Firebase service-account key")
        return None

    @property
    def configured(self) -> bool:
        return self.account is not None

    def _assertion(self, now: int) -> str:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        def b64(obj) -> str:
            raw = obj if isinstance(obj, bytes) else json.dumps(obj, separators=(",", ":")).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        uri = self.account.get("token_uri") or "https://oauth2.googleapis.com/token"
        unsigned = (b64({"alg": "RS256", "typ": "JWT"}) + "." +
                    b64({"iss": self.account["client_email"], "scope": self.SCOPE,
                         "aud": uri, "iat": now, "exp": now + 3600}))
        key = serialization.load_pem_private_key(
            self.account["private_key"].encode(), password=None)
        signature = key.sign(unsigned.encode(), padding.PKCS1v15(), hashes.SHA256())
        return unsigned + "." + b64(signature)

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        now = time.time()
        if self._token and now < self._token_until:
            return self._token
        uri = self.account.get("token_uri") or "https://oauth2.googleapis.com/token"
        res = await client.post(uri, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": self._assertion(int(now))})
        res.raise_for_status()
        body = res.json()
        self._token = body["access_token"]
        self._token_until = now + int(body.get("expires_in", 3600)) - 120
        return self._token

    def message(self, token: str, title: str, body: str, data: dict, quiet: bool) -> dict:
        return {"message": {
            "token": token,
            "notification": {"title": title or "ErnestOS", "body": body[:900]},
            "data": {k: str(v) for k, v in data.items()},
            "android": {"priority": "high",
                        "notification": {"channel_id": "quiet" if quiet else "reminders"}},
            "apns": {"payload": {"aps": {} if quiet else {"sound": "default"}}},
        }}

    async def send(self, tokens: list[str], title: str, body: str, data: dict,
                   quiet: bool = False) -> int:
        """Push to each token; returns how many were accepted. Tokens FCM says
        are gone are switched off so they are not tried again."""
        if not self.configured or not tokens:
            return 0
        url = (f"https://fcm.googleapis.com/v1/projects/"
               f"{self.account['project_id']}/messages:send")
        sent = 0
        async with httpx.AsyncClient(timeout=10) as client:
            access = await self._access_token(client)
            for token in tokens:
                try:
                    res = await client.post(url, json=self.message(token, title, body, data, quiet),
                                            headers={"Authorization": f"Bearer {access}"})
                except httpx.HTTPError as e:
                    log.info("push to a device failed: %s", e)
                    continue
                if res.status_code == 200:
                    sent += 1
                elif res.status_code == 404 or "UNREGISTERED" in res.text:
                    disable_token(token)
                else:
                    log.info("push refused (%s): %s", res.status_code, res.text[:200])
        return sent


fcm = FCM(getattr(config, "FCM_SERVICE_ACCOUNT", ""))


# ---------------------------------------------------------------------------
# The one call the bot makes
# ---------------------------------------------------------------------------

async def deliver(account_id, text: str, markup=None, *, kind: str = "bot",
                  quiet: bool = False) -> bool:
    """Put a bot message in the account's app inbox and push it to its phones.

    True when the account has the app (the inbox row was written). Never
    raises."""
    try:
        account_id = int(account_id)
        with SessionLocal() as s:
            if not has_app(s, account_id):
                return False
            row = record(s, account_id, text, markup, kind=kind)
            note = as_dict(row)
            tokens = device_tokens(s, account_id)
        if tokens and fcm.configured:
            await fcm.send(tokens, note["title"], note["body"],
                           {"notification_id": note["id"], "kind": kind}, quiet)
        return True
    except Exception:
        log.exception("app notification for %s failed", account_id)
        return False
