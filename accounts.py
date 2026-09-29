"""
ErnestOS — logins and passwords.

An account has always been one Telegram id. That still holds: every row in
every table hangs off the Telegram id that created the account. What this
module adds is a second way in. Each account gets a login and a password, and
any *other* Telegram account that signs in with them is linked to it — from
then on the bot and the Mini App serve that Telegram as the account's owner.

    Telegram A ──────────────┐
                             ├──> account #A  (habits, tasks, teams …)
    Telegram B ── login ─────┘

So `resolve()` is the whole trick: every entry point turns "who is talking"
into "whose account is this" with one lookup, and everything downstream is
unchanged.

Passwords are stored as PBKDF2 hashes and never shown again after the moment
they are issued. Guessing is capped on the account itself — five wrong
passwords close it to password sign-in for fifteen minutes — so a guesser
cannot reset the count by switching Telegram accounts.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import timedelta

from sqlalchemy import delete as sql_delete, select
from sqlalchemy.orm import Session

from db import Credential, LinkedTelegram, SessionLocal, User, utcnow

#: PBKDF2 work factor. Lower in the test suite only, where hundreds of
#: accounts are made and nobody is attacking them.
ITERATIONS = int(os.environ.get(
    "PASSWORD_ITERATIONS",
    "1000" if os.environ.get("ENVIRONMENT", "").lower() == "test" else "240000"))

LOGIN_RE = re.compile(r"^[a-z0-9_.]{4,32}$")
PASSWORD_MIN, PASSWORD_MAX = 6, 64

#: Letters a person can read back without confusing 0/o or 1/l.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
GENERATED_PASSWORD_LEN = 8

MAX_FAILED = 5
LOCK_FOR = timedelta(minutes=15)


# ---------------------------------------------------------------------------
# Who is this Telegram, really
# ---------------------------------------------------------------------------

def resolve(s: Session, telegram_id: int) -> int:
    """The account a Telegram id acts as: its linked account, or itself."""
    link = s.get(LinkedTelegram, telegram_id)
    return link.account_id if link is not None else telegram_id


def resolve_id(telegram_id: int) -> int:
    """`resolve`, with its own session — for callers that have none open."""
    with SessionLocal() as s:
        return resolve(s, telegram_id)


def is_linked(s: Session, telegram_id: int) -> bool:
    return s.get(LinkedTelegram, telegram_id) is not None


def linked_telegrams(s: Session, account_id: int) -> list[LinkedTelegram]:
    """Every other Telegram signed in to this account, oldest first."""
    return list(s.scalars(select(LinkedTelegram)
                          .where(LinkedTelegram.account_id == account_id)
                          .order_by(LinkedTelegram.created_at)).all())


def linked_ids(s: Session, account_id: int) -> list[int]:
    return [row.telegram_id for row in linked_telegrams(s, account_id)]


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def hash_password(password: str, *, iterations: int | None = None) -> str:
    rounds = iterations or ITERATIONS
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), rounds)
    return f"pbkdf2_sha256${rounds}${salt}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt, expected = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(),
                                     int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), expected)


def generate_password() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(GENERATED_PASSWORD_LEN))


def clean_password(value: str | None) -> str:
    """The password as typed, or ValueError naming what is wrong with it."""
    value = (value or "").strip()
    if len(value) < PASSWORD_MIN:
        raise ValueError("password_short")
    if len(value) > PASSWORD_MAX or any(ch.isspace() for ch in value):
        raise ValueError("password_bad")
    return value


# ---------------------------------------------------------------------------
# Logins
# ---------------------------------------------------------------------------

def clean_login(value: str | None) -> str:
    """Lower case, 4–32 of `a-z 0-9 _ .`, or ValueError("login_bad")."""
    value = (value or "").strip().lstrip("@").lower()
    if not LOGIN_RE.match(value):
        raise ValueError("login_bad")
    return value


def login_taken(s: Session, login: str, *, except_user: int | None = None) -> bool:
    row = s.scalar(select(Credential).where(Credential.login == login))
    return row is not None and row.user_id != except_user


def _suggest_login(s: Session, user: User) -> str:
    """The Telegram username when it is free and valid, else `eos<number>`."""
    candidates = []
    username = re.sub(r"[^a-z0-9_.]", "", (user.username or "").lower())
    if len(username) >= 4:
        candidates.append(username[:28])
    candidates.append(f"eos{user.member_no or user.telegram_id}")
    for base in candidates:
        if not login_taken(s, base):
            return base
    base = candidates[-1][:26]
    while True:
        guess = f"{base}{secrets.randbelow(9000) + 1000}"
        if not login_taken(s, guess):
            return guess


# ---------------------------------------------------------------------------
# An account's credentials
# ---------------------------------------------------------------------------

def credential_for(s: Session, account_id: int) -> Credential | None:
    return s.get(Credential, account_id)


def ensure_credentials(s: Session, account_id: int) -> tuple[str, str | None]:
    """(login, password) — the password only when it was issued just now.

    Called for every account the first time it is started. An account that
    already has a login is left alone and gets `None` back for the password:
    it was shown once, when it was issued, and is not recoverable.
    """
    existing = s.get(Credential, account_id)
    if existing is not None:
        return existing.login, None
    user = s.get(User, account_id)
    if user is None:
        raise LookupError("account")
    password = generate_password()
    row = Credential(user_id=account_id, login=_suggest_login(s, user),
                     password_hash=hash_password(password))
    s.add(row)
    s.commit()
    return row.login, password


def set_login(s: Session, account_id: int, value: str) -> str:
    login = clean_login(value)
    if login_taken(s, login, except_user=account_id):
        raise ValueError("login_taken")
    row = s.get(Credential, account_id)
    if row is None:
        ensure_credentials(s, account_id)
        row = s.get(Credential, account_id)
    row.login = login
    s.commit()
    return login


def set_password(s: Session, account_id: int, value: str | None = None, *,
                 keep: int | None = None) -> tuple[str, int]:
    """Replace the password; returns (password, how many Telegrams signed out).

    `value=None` issues a new random one. Every other Telegram signed in to
    the account is signed out — a changed password is what somebody does when
    they think it leaked — except `keep`, the Telegram making the change.
    """
    password = clean_password(value) if value is not None else generate_password()
    row = s.get(Credential, account_id)
    if row is None:
        ensure_credentials(s, account_id)
        row = s.get(Credential, account_id)
    row.password_hash = hash_password(password)
    row.failed_attempts = 0
    row.locked_until = None
    stmt = sql_delete(LinkedTelegram).where(LinkedTelegram.account_id == account_id)
    if keep is not None:
        stmt = stmt.where(LinkedTelegram.telegram_id != keep)
    removed = s.execute(stmt).rowcount or 0
    s.commit()
    return password, removed


# ---------------------------------------------------------------------------
# Signing a Telegram in and out
# ---------------------------------------------------------------------------

def sign_in(s: Session, telegram_id: int, login: str, password: str, *,
            first_name: str = "", username: str = "") -> tuple[str, int | None]:
    """("ok", account) · ("bad", None) · ("locked", minutes) · ("self", None).

    "self" is a Telegram signing in to the account it already is — nothing to
    link, and saying so beats a silent success.
    """
    try:
        login = clean_login(login)
    except ValueError:
        return "bad", None
    row = s.scalar(select(Credential).where(Credential.login == login))
    if row is None:
        return "bad", None
    now = utcnow()
    if row.locked_until is not None and row.locked_until > now:
        left = row.locked_until - now
        return "locked", max(1, int(left.total_seconds() // 60) + 1)
    if not check_password((password or "").strip(), row.password_hash):
        row.failed_attempts = (row.failed_attempts or 0) + 1
        if row.failed_attempts >= MAX_FAILED:
            row.failed_attempts = 0
            row.locked_until = now + LOCK_FOR
        s.commit()
        return "bad", None
    row.failed_attempts = 0
    row.locked_until = None
    account_id = row.user_id
    if account_id == telegram_id:
        # Signing back in to your own account is the same as signing out of
        # whichever one you were linked to.
        s.execute(sql_delete(LinkedTelegram).where(
            LinkedTelegram.telegram_id == telegram_id))
        s.commit()
        return "self", account_id
    link = s.get(LinkedTelegram, telegram_id)
    if link is None:
        link = LinkedTelegram(telegram_id=telegram_id, account_id=account_id)
        s.add(link)
    link.account_id = account_id
    link.first_name = (first_name or "")[:200]
    link.username = (username or "")[:200]
    link.created_at = now
    s.commit()
    return "ok", account_id


def sign_out(s: Session, telegram_id: int) -> bool:
    """Unlink this Telegram; it is its own account again. True if it was linked."""
    removed = s.execute(sql_delete(LinkedTelegram).where(
        LinkedTelegram.telegram_id == telegram_id)).rowcount or 0
    s.commit()
    return bool(removed)


def remove_link(s: Session, account_id: int, telegram_id: int) -> bool:
    """The owner signing one of the account's other Telegrams out."""
    removed = s.execute(sql_delete(LinkedTelegram).where(
        LinkedTelegram.account_id == account_id,
        LinkedTelegram.telegram_id == telegram_id)).rowcount or 0
    s.commit()
    return bool(removed)


def forget_account(s: Session, account_id: int) -> None:
    """Everything this module holds about an account. The caller commits."""
    s.execute(sql_delete(LinkedTelegram).where(
        (LinkedTelegram.account_id == account_id)
        | (LinkedTelegram.telegram_id == account_id)))
    s.execute(sql_delete(Credential).where(Credential.user_id == account_id))
