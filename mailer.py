"""
ErnestOS — sending email.

One job: put a short message with a sign-in code in somebody's inbox. Which
service carries it is configuration, read from the environment:

  EMAIL_PROVIDER   resend | brevo | smtp | file   (optional: guessed from
                   whichever key below is set)
  EMAIL_FROM       the sender, e.g. "ErnestOS <no-reply@ernestos.uz>"
  RESEND_API_KEY   https://resend.com — needs a verified domain
  BREVO_API_KEY    https://brevo.com — a verified sender address is enough
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD   any SMTP server

`file` writes each message as JSON into EMAIL_OUTBOX_DIR instead of sending
it — for local runs and the browser checks only; it is never guessed, it has
to be asked for by name.

Nothing here logs a message body: it carries a sign-in code.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import parseaddr

import httpx

log = logging.getLogger("ernestos")

TIMEOUT = 15


class MailError(Exception):
    """The message could not be handed to the provider."""


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def provider() -> str:
    chosen = _env("EMAIL_PROVIDER").lower()
    if chosen:
        return chosen
    if _env("RESEND_API_KEY"):
        return "resend"
    if _env("BREVO_API_KEY"):
        return "brevo"
    if _env("SMTP_HOST"):
        return "smtp"
    return ""


def sender() -> tuple[str, str]:
    """(name, address) of EMAIL_FROM; the name defaults to ErnestOS."""
    name, address = parseaddr(_env("EMAIL_FROM") or _env("SMTP_USER"))
    return (name or "ErnestOS"), address


def configured() -> bool:
    """Whether a message can be sent at all — the sign-up screen says so
    plainly instead of promising a code that will never come."""
    kind = provider()
    if kind == "file":
        return bool(_env("EMAIL_OUTBOX_DIR"))
    if not sender()[1]:
        return False
    if kind == "resend":
        return bool(_env("RESEND_API_KEY"))
    if kind == "brevo":
        return bool(_env("BREVO_API_KEY"))
    if kind == "smtp":
        return bool(_env("SMTP_HOST"))
    return False


async def send(to: str, subject: str, text: str, html: str) -> None:
    """Send one message. Raises MailError when the provider refuses it."""
    kind = provider()
    if not configured():
        raise MailError("not_configured")
    try:
        if kind == "resend":
            await _resend(to, subject, text, html)
        elif kind == "brevo":
            await _brevo(to, subject, text, html)
        elif kind == "smtp":
            await asyncio.to_thread(_smtp, to, subject, text, html)
        elif kind == "file":
            _file(to, subject, text, html)
        else:
            raise MailError("unknown_provider")
    except MailError:
        raise
    except Exception as e:
        # The kind of failure, never the message: it holds a code.
        log.warning("email via %s failed: %s", kind, type(e).__name__)
        raise MailError("send_failed") from None


async def _post(url: str, headers: dict, payload: dict) -> None:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(url, headers=headers, json=payload)
    if r.status_code >= 300:
        log.warning("email provider answered %s", r.status_code)
        raise MailError("provider_refused")


async def _resend(to, subject, text, html):
    name, address = sender()
    await _post("https://api.resend.com/emails",
                {"Authorization": f"Bearer {_env('RESEND_API_KEY')}"},
                {"from": f"{name} <{address}>", "to": [to], "subject": subject,
                 "text": text, "html": html})


async def _brevo(to, subject, text, html):
    name, address = sender()
    await _post("https://api.brevo.com/v3/smtp/email",
                {"api-key": _env("BREVO_API_KEY"), "accept": "application/json"},
                {"sender": {"name": name, "email": address}, "to": [{"email": to}],
                 "subject": subject, "textContent": text, "htmlContent": html})


def _smtp(to, subject, text, html):
    name, address = sender()
    msg = EmailMessage()
    msg["From"] = f"{name} <{address}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    host, port = _env("SMTP_HOST"), int(_env("SMTP_PORT") or "587")
    user, password = _env("SMTP_USER"), _env("SMTP_PASSWORD")
    context = ssl.create_default_context()
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=TIMEOUT)
        server.starttls(context=context)
    try:
        if user:
            server.login(user, password)
        server.send_message(msg)
    finally:
        server.quit()


def _file(to, subject, text, html):
    folder = _env("EMAIL_OUTBOX_DIR")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{time.time_ns()}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"to": to, "subject": subject, "text": text, "html": html}, f,
                  ensure_ascii=False)


# ---------------------------------------------------------------------------
# The one message we send
# ---------------------------------------------------------------------------

_TEXT = {
    "uz": ("{code} — ErnestOS kodi",
           "Kirish kodingiz: {code}\n\nKod 10 daqiqa amal qiladi. Uni hech kimga bermang.\n"
           "Agar bu so'rovni siz yubormagan bo'lsangiz, xatni e'tiborsiz qoldiring."),
    "en": ("{code} — your ErnestOS code",
           "Your sign-in code is {code}\n\nIt works for 10 minutes. Do not share it.\n"
           "If you did not ask for it, ignore this email."),
    "ru": ("{code} — код ErnestOS",
           "Ваш код для входа: {code}\n\nКод действует 10 минут. Никому его не сообщайте.\n"
           "Если вы не запрашивали код, просто проигнорируйте письмо."),
}


def code_message(lang: str, code: str) -> tuple[str, str, str]:
    """(subject, text, html) for a sign-in code, in the person's language."""
    subject, body = _TEXT.get(lang, _TEXT["uz"])
    subject, text = subject.format(code=code), body.format(code=code)
    lines = text.split("\n")
    html = (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;'
        'max-width:420px;margin:0 auto;padding:24px;color:#111">'
        '<div style="font-weight:800;font-size:18px">ErnestOS</div>'
        f'<p style="font-size:15px;margin:18px 0 8px">{lines[0].replace(code, "").strip()}</p>'
        f'<div style="font-size:34px;font-weight:800;letter-spacing:8px;margin:6px 0 18px">{code}</div>'
        + "".join(f'<p style="font-size:13px;color:#555;margin:4px 0">{line}</p>'
                  for line in lines[1:] if line)
        + "</div>")
    return subject, text, html
