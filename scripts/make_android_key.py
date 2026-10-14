"""Make the Android app's signing key — once, then keep it forever.

    python scripts/make_android_key.py

Prints three things to paste:

  * ANDROID_KEYSTORE_B64 and ANDROID_KEYSTORE_PASSWORD → GitHub → repo →
    Settings → Secrets and variables → Actions → New repository secret;
  * the SHA-1 → Google Cloud → Credentials → Android OAuth client.

The key is printed to this terminal only: nothing is written to disk or
sent anywhere. Run it on your own computer (or Railway → Shell), copy the
two values into GitHub, and close the terminal. Lose them and phones must
uninstall the app before the next build installs; leak them and someone
can publish an "update" of your app — never put them in the repo, a chat or
a screenshot.

Needs Python 3.9+ and `cryptography` (already in requirements.txt).
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import secrets
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

ALIAS = "ernestos"


def make(password: str) -> tuple[bytes, str]:
    """A PKCS#12 keystore (RSA 2048, 30-year self-signed certificate) and
    the certificate's SHA-1, as Google Cloud wants it (AA:BB:…)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ErnestOS"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ErnestOS"),
                      x509.NameAttribute(NameOID.COUNTRY_NAME, "UZ")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=365 * 30))
            .sign(key, hashes.SHA256()))
    p12 = pkcs12.serialize_key_and_certificates(
        ALIAS.encode(), key, cert, None,
        serialization.BestAvailableEncryption(password.encode()))
    der = cert.public_bytes(serialization.Encoding.DER)
    sha1 = hashlib.sha1(der).hexdigest().upper()
    return p12, ":".join(sha1[i:i + 2] for i in range(0, len(sha1), 2))


def main() -> int:
    password = secrets.token_urlsafe(24)
    p12, sha1 = make(password)
    out = sys.stdout
    out.write("\n=== ErnestOS Android signing key — copy into GitHub, then close this terminal ===\n\n")
    out.write("ANDROID_KEYSTORE_B64 (one long line):\n")
    out.write(base64.b64encode(p12).decode() + "\n\n")
    out.write("ANDROID_KEYSTORE_PASSWORD:\n" + password + "\n\n")
    out.write("SHA-1 (public — for Google Cloud → Android OAuth client):\n" + sha1 + "\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
