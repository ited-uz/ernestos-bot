"""
ErnestOS — signing the phone app in.

The Mini App proves who it is with Telegram's signed initData. The Android and
iOS app runs outside Telegram, so it proves it another way:

  1. the person taps "Get a code" in the bot and receives a short code;
  2. they type it into the app, which trades it for a session token;
  3. the app sends `app:<token>` in the same header the Mini App uses for
     initData, and `security.verify_init_payload` resolves it to the Telegram
     that asked for the code.

From step 3 on every endpoint, gate and limit works unchanged: the request is
served exactly as that Telegram's Mini App would be.

Only SHA-256 digests of codes and tokens are stored. A code is single use,
lives ten minutes, and a new one cancels the old. A token lives
`SESSION_DAYS` from its last use and dies on sign-out or account deletion.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import delete as sql_delete
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from db import AppLoginCode, AppSession, utcnow

#: The prefix that marks an app token in the initData header.
TOKEN_PREFIX = "app:"
#: Letters that cannot be misread for one another (no 0/O, 1/I/L).
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
CODE_TTL = timedelta(minutes=10)
SESSION_DAYS = 90
#: A session's `last_used_at` is written at most this often, so a busy app
#: does not turn every read into a write.
TOUCH_EVERY = timedelta(hours=1)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def normalize_code(raw: str | None) -> str:
    """What the person typed, as the code was issued: upper case, no spaces
    or dashes. Lower case and a stray space are typing, not a wrong code."""
    return "".join(ch for ch in str(raw or "").upper() if ch.isalnum())[:CODE_LENGTH * 2]


def format_code(code: str) -> str:
    """`ABCD2345` → `ABCD-2345`, the way the bot shows it."""
    return f"{code[:4]}-{code[4:]}"


def issue_code(s: Session, telegram_id: int) -> str:
    """A fresh code for this Telegram. Earlier unused codes stop working."""
    now = utcnow()
    s.execute(update(AppLoginCode)
              .where(AppLoginCode.telegram_id == telegram_id,
                     AppLoginCode.used_at.is_(None))
              .values(used_at=now))
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    s.add(AppLoginCode(telegram_id=telegram_id, code_hash=_digest(code),
                       expires_at=now + CODE_TTL))
    s.commit()
    return code


def redeem_code(s: Session, raw_code: str, device: str = "") -> str | None:
    """Trade a valid code for a session token, or None.

    The code is spent in the same statement that checks it, so two phones
    racing with one code cannot both get a session.
    """
    code = normalize_code(raw_code)
    if len(code) != CODE_LENGTH:
        return None
    now = utcnow()
    spent = s.execute(update(AppLoginCode)
                      .where(AppLoginCode.code_hash == _digest(code),
                             AppLoginCode.used_at.is_(None),
                             AppLoginCode.expires_at > now)
                      .values(used_at=now)
                      .returning(AppLoginCode.telegram_id)).first()
    if spent is None:
        s.rollback()
        return None
    token = secrets.token_urlsafe(32)
    s.add(AppSession(telegram_id=spent[0], token_hash=_digest(token),
                     device=str(device or "")[:80], last_used_at=now,
                     expires_at=now + timedelta(days=SESSION_DAYS)))
    s.commit()
    return token


def is_app_token(value: str | None) -> bool:
    return bool(value) and str(value).startswith(TOKEN_PREFIX)


def resolve_token(s: Session, header_value: str) -> int | None:
    """The Telegram id an `app:<token>` header stands for, or None."""
    row = session_for(s, header_value)
    return row.telegram_id if row is not None else None


def session_for(s: Session, header_value: str) -> AppSession | None:
    """The live session an `app:<token>` header names, or None."""
    token = str(header_value)[len(TOKEN_PREFIX):]
    if not token:
        return None
    now = utcnow()
    row = s.scalar(select(AppSession).where(AppSession.token_hash == _digest(token)))
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        return None
    if now - row.last_used_at >= TOUCH_EVERY:
        row.last_used_at = now
        row.expires_at = now + timedelta(days=SESSION_DAYS)
        s.commit()
    return row


def revoke_token(s: Session, header_value: str) -> bool:
    """Sign this phone out. True when a live session was ended."""
    token = str(header_value)[len(TOKEN_PREFIX):]
    if not token:
        return False
    ids = list(s.scalars(select(AppSession.id).where(
        AppSession.token_hash == _digest(token), AppSession.revoked_at.is_(None))).all())
    ended = s.execute(update(AppSession).where(AppSession.id.in_(ids))
                      .values(revoked_at=utcnow())).rowcount or 0 if ids else 0
    # That phone stops getting pushes too.
    import app_push
    app_push.disable_session_devices(s, ids)
    s.commit()
    return bool(ended)


def forget(s: Session, telegram_ids) -> None:
    """Every code and session of these Telegrams. The caller commits."""
    ids = list(telegram_ids)
    if not ids:
        return
    import app_push
    app_push.forget(s, ids)
    s.execute(sql_delete(AppSession).where(AppSession.telegram_id.in_(ids)))
    s.execute(sql_delete(AppLoginCode).where(AppLoginCode.telegram_id.in_(ids)))
