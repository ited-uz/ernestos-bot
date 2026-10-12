"""
ErnestOS — checking a Google or Apple ID token.

Both providers hand back an OpenID Connect ID token: a JWT signed RS256 with a
key from their public key set. Checking it is five things — the signature
against the published key, who issued it, who it was issued for (our client
ids), that it has not expired, and the nonce when one was sent. Nothing else
in the token is trusted until all five hold.

Public keys are fetched over HTTPS and kept an hour; a token signed with a
key we have not seen makes one fresh fetch (keys rotate).
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

log = logging.getLogger("ernestos")

JWKS_URLS = {
    "google": "https://www.googleapis.com/oauth2/v3/certs",
    "apple": "https://appleid.apple.com/auth/keys",
}
ISSUERS = {
    "google": {"accounts.google.com", "https://accounts.google.com"},
    "apple": {"https://appleid.apple.com"},
}
CACHE_SECONDS = 3600
LEEWAY = 120

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


class TokenError(Exception):
    """The token is not one we accept. The reason is for the log only."""


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _fetch_keys(provider: str) -> dict:
    r = httpx.get(JWKS_URLS[provider], timeout=10)
    r.raise_for_status()
    return {k["kid"]: k for k in r.json().get("keys", []) if k.get("kty") == "RSA"}


def _keys(provider: str, kid: str) -> dict | None:
    """The provider's key with this id, from cache, refreshed when stale or
    when the id is new."""
    with _lock:
        hit = _cache.get(provider)
        fresh = hit and time.monotonic() - hit[0] < CACHE_SECONDS
        if not fresh or kid not in hit[1]:
            try:
                _cache[provider] = (time.monotonic(), _fetch_keys(provider))
            except Exception as e:
                log.warning("could not fetch %s keys: %s", provider, type(e).__name__)
                if not hit:
                    return None
        return _cache[provider][1].get(kid) if provider in _cache else None


def _public_key(jwk: dict) -> rsa.RSAPublicKey:
    n = int.from_bytes(_b64(jwk["n"]), "big")
    e = int.from_bytes(_b64(jwk["e"]), "big")
    return rsa.RSAPublicNumbers(e, n).public_key()


def verify(provider: str, token: str, audiences, *, nonce: str | None = None) -> dict:
    """The token's claims, once every check has passed. Raises TokenError."""
    if provider not in JWKS_URLS:
        raise TokenError("unknown provider")
    audiences = {a for a in (audiences or ()) if a}
    if not audiences:
        raise TokenError("no audience configured")
    try:
        head_b64, body_b64, sig_b64 = str(token).split(".")
        header = json.loads(_b64(head_b64))
        claims = json.loads(_b64(body_b64))
        signature = _b64(sig_b64)
    except Exception:
        raise TokenError("malformed") from None
    if header.get("alg") != "RS256":
        raise TokenError("algorithm")
    jwk = _keys(provider, header.get("kid", ""))
    if jwk is None:
        raise TokenError("unknown key")
    try:
        _public_key(jwk).verify(signature, f"{head_b64}.{body_b64}".encode(),
                                padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise TokenError("signature") from None
    if claims.get("iss") not in ISSUERS[provider]:
        raise TokenError("issuer")
    aud = claims.get("aud")
    if not (set(aud) if isinstance(aud, list) else {aud}) & audiences:
        raise TokenError("audience")
    now = time.time()
    if float(claims.get("exp", 0)) < now - LEEWAY:
        raise TokenError("expired")
    if float(claims.get("iat", now)) > now + LEEWAY:
        raise TokenError("issued in the future")
    if nonce is not None and claims.get("nonce") != nonce:
        raise TokenError("nonce")
    if not claims.get("sub"):
        raise TokenError("no subject")
    return claims


def verified_email(claims: dict) -> str | None:
    """The address, when the provider vouches for it."""
    email = str(claims.get("email") or "").strip().lower()
    ok = claims.get("email_verified")
    if email and (ok is True or str(ok).lower() == "true"):
        return email
    return None
