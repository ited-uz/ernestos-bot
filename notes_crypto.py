"""Encryption of connector secrets at rest (Google refresh tokens, PKCE verifiers).

AES-256-GCM with a key from the environment, never from the database:

  CONNECTOR_TOKEN_KEY       32 random bytes, urlsafe base64 (required to connect)
  CONNECTOR_TOKEN_KEY_OLD   the previous key while rotating (optional, decrypt only)

A stored value is `v1:<key id>:<nonce+ciphertext, base64>`. The key id is a
short digest of the key, so a value encrypted under a key that is no longer
configured is recognised and refused instead of decrypting to garbage.

Without a key there is no fallback to plaintext: the connector reports itself
as not configured.

Generate a key:  python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class KeyMissing(RuntimeError):
    pass


def _load(name: str) -> bytes | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (binascii.Error, ValueError):
        return None
    return key if len(key) == 32 else None


def _kid(key: bytes) -> str:
    return hashlib.sha256(b"ernestos-connector:" + key).hexdigest()[:8]


def configured() -> bool:
    return _load("CONNECTOR_TOKEN_KEY") is not None


def encrypt(plain: str, *, purpose: str) -> str:
    key = _load("CONNECTOR_TOKEN_KEY")
    if key is None:
        raise KeyMissing("CONNECTOR_TOKEN_KEY")
    nonce = os.urandom(12)
    sealed = AESGCM(key).encrypt(nonce, plain.encode(), purpose.encode())
    return f"v1:{_kid(key)}:{base64.urlsafe_b64encode(nonce + sealed).decode()}"


def decrypt(stored: str, *, purpose: str) -> str:
    try:
        version, kid, blob = str(stored).split(":", 2)
    except ValueError:
        raise ValueError("bad sealed value") from None
    if version != "v1":
        raise ValueError("unknown version")
    for name in ("CONNECTOR_TOKEN_KEY", "CONNECTOR_TOKEN_KEY_OLD"):
        key = _load(name)
        if key is not None and _kid(key) == kid:
            raw = base64.urlsafe_b64decode(blob)
            return AESGCM(key).decrypt(raw[:12], raw[12:], purpose.encode()).decode()
    raise KeyMissing("no configured key matches")


def needs_rotation(stored: str) -> bool:
    key = _load("CONNECTOR_TOKEN_KEY")
    return key is not None and not str(stored).startswith(f"v1:{_kid(key)}:")
