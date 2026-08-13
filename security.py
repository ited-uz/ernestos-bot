"""
ErnestOS — who the caller is, and whether their text can be trusted.

Three things live here, and they belong together because they are the whole
answer to "can this request proceed, and can I put its contents in a message":

  * `verify_init_data` — the Telegram WebApp signature. This is the *only*
    place a caller's identity is established. An id in a JSON body is never
    trusted, anywhere, by anything.
  * `auth` — signature, then access policy, then onboarding. What every API
    endpoint calls.
  * `esc` — escaping user text before it enters an HTML Telegram message.

Access policy itself is not here. It is in `dependencies`, shared with the
bot, because the bot and the API answering "is this account allowed through"
differently is exactly the bug worth engineering against.

`app.py` re-exports all three names.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
from datetime import datetime
from urllib.parse import parse_qsl

from fastapi import HTTPException

import config
import dependencies as deps
import services as svc
from db import SessionLocal, User

log = logging.getLogger("ernestos")


def esc(value) -> str:
    """Escape user-controlled text before it enters an HTML message.

    Telegram parses `parse_mode=HTML`, so an unescaped name like
    `<a href=...>` either renders as a link or aborts the whole send with a
    parse error (audit 014, 076). Application markup is written literally in
    the f-string; every value that came from a user goes through here.
    """
    return html.escape(str(value or ""), quote=False)


def verify_init_payload(init_data: str) -> dict:
    """Validate Telegram WebApp initData and return the **whole** signed payload.

    Returns the parsed fields with `user` decoded, so a caller can reach
    `start_param` — the referral code from a `?startapp=` link — knowing it
    came from inside Telegram's signature rather than from the page.

    That distinction is the entire point of this function existing. The client
    also has `Telegram.WebApp.initDataUnsafe.start_param` and can put
    `tgWebAppStartParam` in a URL, and both are attacker-controlled strings:
    trusting either would let anybody mint referrals for themselves by editing
    a query parameter. Only what survives the HMAC below is used.

    Every rejection returns the same 401 with the same body. The reason is
    written to the log, not to the response: telling a caller *why* their
    forgery failed is telling them how to fix it.
    """
    if not init_data:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        received = parsed.pop("hash", "")
        if not received:
            raise ValueError("no hash")

        check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(),
                          hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received):
            raise ValueError("bad signature")

        auth_date = int(parsed.get("auth_date", "0"))
        age = datetime.now().timestamp() - auth_date
        if age > config.INIT_DATA_MAX_AGE or age < -300:
            raise ValueError("stale")

        user = json.loads(parsed.get("user", "{}"))
        if not user.get("id"):
            raise ValueError("no user")
        parsed["user"] = user
        return parsed
    except Exception as e:
        # The reason, never the payload: initData is a bearer credential and
        # must not reach the log.
        log.info("initData rejected: %s", e)
        raise HTTPException(status_code=401, detail="unauthorized")


def verify_init_data(init_data: str) -> dict:
    """Validate initData and return just the embedded user.

    The long-standing contract, unchanged: callers and tests that want "who is
    this" get the user dict and nothing else. `verify_init_payload` does the
    work; this is the narrow view of it.
    """
    return verify_init_payload(init_data)["user"]


def auth(init_data: str | None, *, require_onboarded: bool = True) -> tuple[User, int]:
    """Resolve the caller to (user, workspace_id) and apply access policy.

    Three gates, in order:
      1. a valid Telegram signature (always);
      2. access — the free run, then channel membership. The rule is
         `dependencies.trial_state`, the same one the bot's `guard` applies;
      3. completed onboarding — status endpoints opt out via require_onboarded.

    The membership half is deliberately cache-only here: an HTTP handler must
    not block on a Telegram round-trip. A membership answer that has gone stale
    is simply not acted on by this path — `dependencies.check_subscription`,
    which the bot and `/api/subscription` both call, does the round-trip and
    writes the fresh answer down.

    The workspace id returned here is the one every service call is scoped to.
    That is the whole of ErnestOS's data isolation: a handler that used an id
    from the request body instead of this one would be the bug.
    """
    payload = verify_init_payload(init_data or "")
    tg_user = payload["user"]
    with SessionLocal() as s:
        user, created = svc.get_or_create_user(
            s, int(tg_user["id"]),
            first_name=tg_user.get("first_name", ""),
            last_name=tg_user.get("last_name", ""),
            username=tg_user.get("username", ""))
        svc.touch_activity(s, user.telegram_id)
        s.commit()
        # First touch, from a `?startapp=ref_…` link. `start_param` is read
        # from the *signed* payload, so a hand-edited `tgWebAppStartParam` or a
        # value pulled out of `initDataUnsafe` cannot mint a referral. Same
        # `created` gate as the bot: an existing account is never attributed.
        if created:
            svc.claim_referral(s, user.telegram_id, payload.get("start_param"),
                               source="miniapp", newly_created=True)
        ws = svc.workspace_id_for(s, user.telegram_id)

        trial = deps.trial_state(user)
        if trial.gated:
            raise HTTPException(status_code=403, detail="subscription_required")

        if require_onboarded and not user.onboarded:
            # A half-registered account must not create rows (audit 003).
            raise HTTPException(status_code=409, detail="onboarding_required")

        return user, ws
