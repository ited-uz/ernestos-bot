"""
ErnestOS — signing in with Google or Apple.

Two ways in, one result:

  * **On the phone, Google:** the app asks Android for the Google account
    already on the device (Credential Manager) and sends the ID token here.
  * **In the phone's browser, Google or Apple:** the app opens the provider's
    page with a random `state`, the provider sends the person back to our
    callback, and the app collects the finished session with that state.
    Used for Apple on Android (there is no Apple account on an Android phone)
    and for Google whenever the native sheet is not available.

Whichever way, the account is found in this order: the same Google/Apple
account before → an account with the same verified email (an email signed up
here, or attached to a Telegram account in Settings) → a new account.

Configuration (all optional; a provider without its ids is simply not shown):

  GOOGLE_CLIENT_ID       the **Web** OAuth client id (also the audience of
                         the phone's token)
  GOOGLE_CLIENT_SECRET   that client's secret — turns on the browser way
  GOOGLE_EXTRA_CLIENT_IDS  other audiences to accept (an iOS client), comma
  APPLE_CLIENT_ID        the Apple Services ID (browser way)
  APPLE_EXTRA_CLIENT_IDS other audiences (the iOS bundle id), comma
  PUBLIC_URL             this server's https origin, for the return address;
                         defaults to the origin of WEBHOOK_URL or WEBAPP_URL
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlparse

import httpx
from sqlalchemy import delete as sql_delete
from sqlalchemy.orm import Session

import email_auth
import oidc
from db import OAuthPending, SocialIdentity, utcnow

PENDING_TTL = timedelta(minutes=10)
PROVIDERS = ("google", "apple")


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _list(name: str) -> list[str]:
    return [x.strip() for x in _env(name).split(",") if x.strip()]


def google_audiences() -> set[str]:
    return {x for x in [_env("GOOGLE_CLIENT_ID"), *_list("GOOGLE_EXTRA_CLIENT_IDS")] if x}


def apple_audiences() -> set[str]:
    return {x for x in [_env("APPLE_CLIENT_ID"), *_list("APPLE_EXTRA_CLIENT_IDS")] if x}


def public_url() -> str:
    explicit = _env("PUBLIC_URL").rstrip("/")
    if explicit:
        return explicit
    for name in ("WEBHOOK_URL", "WEBAPP_URL"):
        raw = _env(name)
        if raw:
            u = urlparse(raw)
            if u.scheme and u.netloc:
                return f"{u.scheme}://{u.netloc}"
    return ""


def browser_ready(provider: str) -> bool:
    if not public_url():
        return False
    if provider == "google":
        return bool(_env("GOOGLE_CLIENT_ID") and _env("GOOGLE_CLIENT_SECRET"))
    if provider == "apple":
        return bool(_env("APPLE_CLIENT_ID"))
    return False


def public_config() -> dict:
    """What the sign-in page needs to draw the buttons. No secrets."""
    google = _env("GOOGLE_CLIENT_ID")
    return {
        "google": ({"client_id": google, "browser": browser_ready("google")} if google else None),
        "apple": ({"browser": True} if browser_ready("apple") else None),
    }


def redirect_uri(provider: str) -> str:
    return f"{public_url()}/api/auth/oauth/{provider}/callback"


# ---------------------------------------------------------------------------
# One account for one person
# ---------------------------------------------------------------------------

def sign_in(s: Session, provider: str, claims: dict, *, language: str = "uz") -> tuple[int, bool]:
    """(account id, whether it is new) for a checked token's claims."""
    subject = str(claims["sub"])
    known = s.get(SocialIdentity, (provider, subject))
    if known is not None:
        return known.account_id, False
    email = oidc.verified_email(claims)
    account = email_auth.account_for(s, email) if email else None
    new = account is None
    if new:
        name = str(claims.get("given_name") or claims.get("name") or "").strip()
        account = email_auth.create_account(s, email, language=language, first_name=name)
    s.add(SocialIdentity(provider=provider, subject=subject, account_id=account,
                         email=email or ""))
    s.commit()
    return account, new


def forget(s: Session, account_ids) -> None:
    """Every Google/Apple link of these accounts. The caller commits."""
    ids = list(account_ids)
    if ids:
        s.execute(sql_delete(SocialIdentity).where(SocialIdentity.account_id.in_(ids)))


# ---------------------------------------------------------------------------
# The browser way
# ---------------------------------------------------------------------------

def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def valid_state(state: str) -> bool:
    return 32 <= len(state or "") <= 128 and all(c in "0123456789abcdefABCDEF-_" for c in state)


def start(s: Session, provider: str, state: str, language: str = "uz") -> str:
    """Remember the attempt and return the provider's page to open."""
    s.execute(sql_delete(OAuthPending).where(OAuthPending.created_at < utcnow() - PENDING_TTL))
    nonce = secrets.token_urlsafe(24)
    s.merge(OAuthPending(state_hash=_digest(state), provider=provider, nonce=nonce,
                         language=language if language in ("uz", "en", "ru") else "uz"))
    s.commit()
    if provider == "google":
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": _env("GOOGLE_CLIENT_ID"), "redirect_uri": redirect_uri("google"),
            "response_type": "code", "scope": "openid email profile", "state": state,
            "nonce": nonce, "prompt": "select_account"})
    return "https://appleid.apple.com/auth/authorize?" + urlencode({
        "client_id": _env("APPLE_CLIENT_ID"), "redirect_uri": redirect_uri("apple"),
        "response_type": "code id_token", "response_mode": "form_post",
        "scope": "name email", "state": state, "nonce": nonce})


def pending(s: Session, state: str) -> OAuthPending | None:
    row = s.get(OAuthPending, _digest(state))
    if row is None or row.created_at < utcnow() - PENDING_TTL:
        return None
    return row


def google_code_to_token(code: str) -> str:
    """Trade the code for the ID token at Google's token endpoint."""
    r = httpx.post("https://oauth2.googleapis.com/token", timeout=15, data={
        "code": code, "client_id": _env("GOOGLE_CLIENT_ID"),
        "client_secret": _env("GOOGLE_CLIENT_SECRET"),
        "redirect_uri": redirect_uri("google"), "grant_type": "authorization_code"})
    if r.status_code != 200:
        raise oidc.TokenError(f"token endpoint {r.status_code}")
    token = r.json().get("id_token")
    if not token:
        raise oidc.TokenError("no id_token")
    return token


def finish(s: Session, row: OAuthPending, *, token: str | None = None,
           new: bool = False, error: str | None = None) -> None:
    row.token, row.new_account, row.error = token, new, error
    s.commit()


def collect(s: Session, state: str) -> dict:
    """What the app gets when it asks: still waiting, the session (once),
    or why it failed."""
    row = pending(s, state)
    if row is None:
        return {"status": "unknown"}
    if row.error:
        out = {"status": "error", "error": row.error}
    elif row.token:
        out = {"status": "done", "token": row.token, "new": row.new_account}
    else:
        return {"status": "pending"}
    s.delete(row)
    s.commit()
    return out
