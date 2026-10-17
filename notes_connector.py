"""
ErnestOS Notes → Google Drive → NotebookLM (consumer), version 1.

What is real here, and what is not
-----------------------------------
Consumer NotebookLM has no public API. What this module does for real:

  1. The person connects their own Google Drive with OAuth 2.0 (authorization
     code + PKCE), scope `drive.file` only: ErnestOS can see and change only
     the files it created, nothing else in their Drive.
  2. Notes they pick (a notebook, or a hand-picked list) are written into
     Google Docs "source packs" in a folder ErnestOS creates. Each pack is
     bounded in size, starts with a manifest of the notes in it, and keeps its
     Drive file id: the next export updates the same document in place, so a
     NotebookLM source made from it keeps pointing at it.
  3. The person adds those docs to a NotebookLM notebook themselves (Add
     source → Google Docs), saves the notebook's link here, and proves
     NotebookLM read the docs by asking it for the pack's verification code.

Statuses are kept apart on purpose: *exported* (the docs are in Drive and
current), *waiting* (a notebook link is saved, not yet proven), *verified*
(NotebookLM answered with the code). NotebookLM shows a Google Docs source as
a snapshot; after an update the person taps "Sync with Google Drive" on the
source in NotebookLM. ErnestOS cannot do that step for them.

NotebookLM Enterprise (Google Cloud, Discovery Engine API) has notebook and
source APIs in Preview; they need a Google Cloud project with NotebookLM
Enterprise licences and are not used by this consumer connector.

Never: a shared founder account, service-account impersonation, public links,
scraping NotebookLM, browser cookies or any undocumented endpoint.

Configuration (see docs/NOTES_NOTEBOOKLM_SETUP.md):
  GOOGLE_DRIVE_CLIENT_ID / GOOGLE_DRIVE_CLIENT_SECRET  (fall back to
      GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET, the sign-in client)
  PUBLIC_URL               https origin of this server (redirect URI host)
  CONNECTOR_TOKEN_KEY      key that encrypts refresh tokens (notes_crypto)
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import random
import re
import secrets
import threading
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete as sql_delete, or_, select, update
from sqlalchemy.orm import Session

import notes_crypto
from db import (
    ConnectorAccount, ConnectorJob, ConnectorOAuthState, NlmPack, NlmSourceSet,
    Note, NoteNotebook, NoteNotebookMember, NoteTag, SessionLocal, User, Workspace, utcnow,
)

log = logging.getLogger("ernestos.connector")

SCOPE = "https://www.googleapis.com/auth/drive.file"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
DRIVE = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
DOC_MIME = "application/vnd.google-apps.document"
FOLDER_MIME = "application/vnd.google-apps.folder"
FOLDER_NAME = "ErnestOS → NotebookLM"

STATE_TTL = timedelta(minutes=10)
LEASE = timedelta(minutes=5)
MAX_ATTEMPTS = 6
#: One Google Doc stays well under the Docs size ceiling and NotebookLM's
#: per-source limit; a set bigger than this becomes several packs.
PACK_CHARS = 350_000
MAX_PACKS = 20
NLM_URL = re.compile(r"^https://notebooklm\.google\.com/notebook/([A-Za-z0-9_-]{8,128})/?(?:[?#].*)?$")

#: Tests put an httpx.MockTransport here; production leaves it None.
TRANSPORT: httpx.BaseTransport | None = None


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def client_id() -> str:
    return _env("GOOGLE_DRIVE_CLIENT_ID") or _env("GOOGLE_CLIENT_ID")


def client_secret() -> str:
    return _env("GOOGLE_DRIVE_CLIENT_SECRET") or _env("GOOGLE_CLIENT_SECRET")


def public_url() -> str:
    import social_auth
    return social_auth.public_url()


def redirect_uri() -> str:
    return f"{public_url()}/api/notes/connector/google/callback"


def missing_config() -> list[str]:
    """Names of the settings still missing; empty when the connector can run."""
    out = []
    if not client_id():
        out.append("GOOGLE_DRIVE_CLIENT_ID")
    if not client_secret():
        out.append("GOOGLE_DRIVE_CLIENT_SECRET")
    if not public_url().startswith("https://") and not os.environ.get("CONNECTOR_ALLOW_HTTP"):
        out.append("PUBLIC_URL")
    if not notes_crypto.configured():
        out.append("CONNECTOR_TOKEN_KEY")
    return out


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ConnectorError(Exception):
    def __init__(self, code: str, *, retry: bool = False):
        super().__init__(code)
        self.code, self.retry = code, retry


class Revoked(ConnectorError):
    def __init__(self):
        super().__init__("reauth_required")


class Gone(ConnectorError):
    def __init__(self):
        super().__init__("file_gone")


def _check(r: httpx.Response, what: str) -> httpx.Response:
    if r.status_code < 300:
        return r
    reason = ""
    try:
        body = r.json()
        err = body.get("error")
        reason = (err.get("errors") or [{}])[0].get("reason", "") if isinstance(err, dict) else str(err or "")
    except Exception:
        pass
    if r.status_code == 404:
        raise Gone()
    if r.status_code == 401:
        raise Revoked()
    if r.status_code == 429 or r.status_code >= 500 or reason in (
            "rateLimitExceeded", "userRateLimitExceeded", "backendError"):
        raise ConnectorError(f"{what}_{r.status_code}", retry=True)
    raise ConnectorError(f"{what}_{r.status_code}")


# ---------------------------------------------------------------------------
# Google, over plain HTTPS
# ---------------------------------------------------------------------------

class Google:
    """The few documented Google endpoints this connector uses."""

    def __init__(self):
        self.http = httpx.Client(timeout=30, transport=TRANSPORT)

    def close(self):
        self.http.close()

    def exchange(self, code: str, verifier: str) -> dict:
        r = self.http.post(TOKEN_URL, data={
            "code": code, "client_id": client_id(), "client_secret": client_secret(),
            "redirect_uri": redirect_uri(), "grant_type": "authorization_code",
            "code_verifier": verifier})
        if r.status_code == 400:
            raise ConnectorError("exchange_refused")
        return _check(r, "exchange").json()

    def access(self, refresh_token: str) -> str:
        try:
            r = self.http.post(TOKEN_URL, data={
                "client_id": client_id(), "client_secret": client_secret(),
                "refresh_token": refresh_token, "grant_type": "refresh_token"})
        except httpx.HTTPError:
            raise ConnectorError("network", retry=True) from None
        if r.status_code in (400, 401):
            try:
                if r.json().get("error") in ("invalid_grant", "unauthorized_client"):
                    raise Revoked()
            except ValueError:
                pass
        return _check(r, "refresh").json()["access_token"]

    def revoke(self, token: str) -> bool:
        try:
            r = self.http.post(REVOKE_URL, data={"token": token})
        except httpx.HTTPError:
            return False
        # 400 invalid_token: already revoked — the outcome wanted.
        return r.status_code in (200, 400)

    def _h(self, access: str) -> dict:
        return {"Authorization": f"Bearer {access}"}

    def about(self, access: str) -> dict:
        r = self.http.get(f"{DRIVE}/about", headers=self._h(access),
                          params={"fields": "user(displayName,emailAddress),importFormats"})
        return _check(r, "about").json()

    def file(self, access: str, file_id: str) -> dict | None:
        try:
            r = _check(self.http.get(f"{DRIVE}/files/{file_id}", headers=self._h(access),
                                     params={"fields": "id,trashed,mimeType"}), "file")
        except Gone:
            return None
        meta = r.json()
        return None if meta.get("trashed") else meta

    def folder(self, access: str) -> str:
        r = self.http.post(f"{DRIVE}/files", headers=self._h(access), params={"fields": "id"},
                           json={"name": FOLDER_NAME, "mimeType": FOLDER_MIME})
        return _check(r, "folder").json()["id"]

    def _multipart(self, meta: dict, content: str, mime: str) -> tuple[bytes, str]:
        boundary = "ernestos" + secrets.token_hex(12)
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                f"{json.dumps(meta)}\r\n--{boundary}\r\nContent-Type: {mime}; charset=UTF-8\r\n\r\n"
                ).encode() + content.encode() + f"\r\n--{boundary}--\r\n".encode()
        return body, f"multipart/related; boundary={boundary}"

    def create_doc(self, access: str, folder: str, name: str, content: str, mime: str) -> str:
        body, ctype = self._multipart({"name": name, "mimeType": DOC_MIME, "parents": [folder]},
                                      content, mime)
        r = self.http.post(f"{UPLOAD}/files", params={"uploadType": "multipart", "fields": "id"},
                           headers={**self._h(access), "Content-Type": ctype}, content=body)
        return _check(r, "create").json()["id"]

    def update_doc(self, access: str, file_id: str, name: str, content: str, mime: str) -> None:
        body, ctype = self._multipart({"name": name}, content, mime)
        r = self.http.patch(f"{UPLOAD}/files/{file_id}", params={"uploadType": "multipart", "fields": "id"},
                            headers={**self._h(access), "Content-Type": ctype}, content=body)
        _check(r, "update")

    def export_text(self, access: str, file_id: str) -> str:
        """A Google Doc's text, read back from Drive (the live check uses it)."""
        r = self.http.get(f"{DRIVE}/files/{file_id}/export", headers=self._h(access),
                          params={"mimeType": "text/plain"})
        return _check(r, "export").text

    def trash(self, access: str, file_id: str) -> None:
        try:
            _check(self.http.patch(f"{DRIVE}/files/{file_id}", headers=self._h(access),
                                   params={"fields": "id"}, json={"trashed": True}), "trash")
        except Gone:
            pass


def _upload_mime(about: dict) -> str:
    """Markdown is imported as a formatted Google Doc where Drive supports
    it (it says so in importFormats); otherwise plain text."""
    formats = about.get("importFormats") or {}
    return "text/markdown" if DOC_MIME in (formats.get("text/markdown") or []) else "text/plain"


# ---------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------

def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def start(s: Session, ws: int) -> str:
    """Remember a consent attempt for this workspace and return Google's page.

    Nothing secret goes in the URL: `state` is random and only its hash is
    stored; the PKCE verifier stays on the server, encrypted."""
    if missing_config():
        raise ConnectorError("not_configured")
    s.execute(sql_delete(ConnectorOAuthState).where(ConnectorOAuthState.created_at < utcnow() - STATE_TTL))
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    s.add(ConnectorOAuthState(state_hash=_digest(state), workspace_id=ws,
                              verifier_enc=notes_crypto.encrypt(verifier, purpose="pkce")))
    s.commit()
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id(), "redirect_uri": redirect_uri(), "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})


def callback(state: str, code: str, error: str = "") -> tuple[bool, str, int | None]:
    """Finish a consent: (ok, reason, workspace). One use per state; the code
    exchange and Drive calls happen with no database transaction open."""
    if not state or len(state) > 128:
        return False, "bad_state", None
    with SessionLocal() as s:
        row = s.get(ConnectorOAuthState, _digest(state))
        if row is None or row.used_at is not None or row.created_at < utcnow() - STATE_TTL:
            return False, "bad_state", None
        row.used_at = utcnow()
        ws = row.workspace_id
        sealed = row.verifier_enc
        s.commit()
    if error or not code:
        return False, "denied", ws
    google = Google()
    try:
        verifier = notes_crypto.decrypt(sealed, purpose="pkce")
        tokens = google.exchange(code, verifier)
        granted = set((tokens.get("scope") or "").split())
        if SCOPE not in granted:
            # The person unticked Drive on Google's page.
            if tokens.get("refresh_token") or tokens.get("access_token"):
                google.revoke(tokens.get("refresh_token") or tokens["access_token"])
            return False, "scope_denied", ws
        refresh = tokens.get("refresh_token")
        if not refresh:
            return False, "no_refresh_token", ws
        about = google.about(tokens["access_token"])
    except (ConnectorError, notes_crypto.KeyMissing, KeyError, ValueError) as e:
        log.info("drive connect failed: %s", getattr(e, "code", type(e).__name__))
        return False, "exchange_failed", ws
    finally:
        google.close()
    user = about.get("user") or {}
    old_token = None
    with SessionLocal() as s:
        acct = s.scalar(select(ConnectorAccount).where(ConnectorAccount.workspace_id == ws,
                                                       ConnectorAccount.provider == "google_drive"))
        if acct is None:
            acct = ConnectorAccount(workspace_id=ws, provider="google_drive")
            s.add(acct)
        elif acct.refresh_token_enc:
            old_token = acct.refresh_token_enc
        # Not usable until the person confirms, in the app, that this is the
        # Google account they meant (a link opened by someone else ends here).
        acct.status = "pending_confirm"
        acct.account_email = str(user.get("emailAddress") or "")[:200]
        acct.account_name = str(user.get("displayName") or "")[:200]
        acct.refresh_token_enc = notes_crypto.encrypt(refresh, purpose="refresh")
        acct.scope = SCOPE
        acct.last_error = ""
        acct.updated_at = utcnow()
        s.commit()
    if old_token:
        revoke_in_background([old_token])
    return True, "ok", ws


def account(s: Session, ws: int) -> ConnectorAccount | None:
    return s.scalar(select(ConnectorAccount).where(ConnectorAccount.workspace_id == ws,
                                                   ConnectorAccount.provider == "google_drive"))


def confirm(s: Session, ws: int) -> ConnectorAccount:
    acct = account(s, ws)
    if acct is None or acct.status != "pending_confirm":
        raise ConnectorError("nothing_to_confirm")
    acct.status = "connected"
    acct.connected_at = acct.connected_at or utcnow()
    acct.updated_at = utcnow()
    # Sets that waited for a connection go out now.
    for set_id in s.scalars(select(NlmSourceSet.id).where(NlmSourceSet.workspace_id == ws)):
        enqueue(s, ws, "export_set", set_id)
    s.commit()
    kick()
    return acct


def disconnect(s: Session, ws: int, *, trash_docs: bool) -> None:
    """Revoke Google's grant and forget the token (a job: it needs the
    network). With `trash_docs`, ErnestOS's docs go to the Drive trash first."""
    acct = account(s, ws)
    if acct is None:
        raise ConnectorError("not_connected")
    acct.status = "disconnecting"
    acct.updated_at = utcnow()
    enqueue(s, ws, "disconnect", acct.id, {"trash_docs": bool(trash_docs)})
    s.commit()
    kick()


# ---------------------------------------------------------------------------
# Source sets and packs
# ---------------------------------------------------------------------------

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _new_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _norm_code(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _owned_set(s: Session, ws: int, set_id: int) -> NlmSourceSet:
    row = s.get(NlmSourceSet, set_id)
    if row is None or row.workspace_id != ws:
        import services as svc
        raise svc.NotFound("source_set")
    return row


def eligible(s: Session, ws: int, row: NlmSourceSet) -> list[Note]:
    """The notes a set sends today: never a trashed note, never one kept out
    of AI and connectors."""
    stmt = select(Note).where(Note.workspace_id == ws, Note.trashed_at.is_(None),
                              Note.ai_excluded.is_(False))
    if row.notebook_id:
        stmt = stmt.where(Note.id.in_(select(NoteNotebookMember.note_id).where(
            NoteNotebookMember.notebook_id == row.notebook_id)))
    else:
        ids = [int(i) for i in json.loads(row.note_ids or "[]")][:2000]
        stmt = stmt.where(Note.id.in_(ids))
    return list(s.scalars(stmt.order_by(Note.created_at, Note.id)).all())


def _tags(s: Session, ids: list[int]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for nid, tag in s.execute(select(NoteTag.note_id, NoteTag.tag).where(NoteTag.note_id.in_(ids))):
        out.setdefault(nid, []).append(tag)
    return out


def _note_block(n: Note, tags: list[str]) -> str:
    meta = [f"ID: note:{n.uid}", f"Type: {n.kind}"]
    if tags:
        meta.append("Tags: " + ", ".join(sorted(tags)))
    meta.append(f"Updated: {n.updated_at:%Y-%m-%d %H:%M} UTC")
    if n.source_url:
        meta.append(f"Source: {n.source_url}")
    body = n.body.strip() or "_(empty)_"
    return f"# {n.title or 'Untitled'}\n\n{' · '.join(meta)}\n\n{body}\n"


def build(s: Session, ws: int, row: NlmSourceSet, notes: list[Note]) -> tuple[list[dict], str]:
    """Split the set into packs of at most PACK_CHARS: [{part, content, manifest, hash}]."""
    tag_map = _tags(s, [n.id for n in notes]) if notes else {}
    blocks = [(n, _note_block(n, tag_map.get(n.id, []))) for n in notes]
    groups: list[list[tuple[Note, str]]] = [[]]
    size = 0
    for n, block in blocks:
        if groups[-1] and size + len(block) > PACK_CHARS - 20_000:
            groups.append([])
            size = 0
        groups[-1].append((n, block))
        size += len(block)
    if len(groups) > MAX_PACKS:
        raise ConnectorError("set_too_large")
    total = len(groups)
    packs = []
    for i, group in enumerate(groups, start=1):
        manifest = [{"uid": n.uid, "title": n.title, "hash": n.content_hash} for n, _ in group]
        lines = [f"# ErnestOS source pack — {row.name}" + (f" (part {i} of {total})" if total > 1 else ""),
                 "",
                 f"ErnestOS verification code: {row.verify_code}",
                 f"Notes in this document: {len(group)}. Written by the owner in ErnestOS; "
                 "each note starts with a level-1 heading and its ID.",
                 "", "## Manifest", ""]
        lines += [f"{k}. {n.title or 'Untitled'} — note:{n.uid}" for k, (n, _) in enumerate(group, 1)]
        if not group:
            lines.append("_(no notes in this set right now)_")
        content = "\n".join(lines) + "\n\n---\n\n" + "\n---\n\n".join(b for _, b in group)
        packs.append({"part": i, "content": content, "manifest": manifest,
                      "hash": hashlib.sha256(content.encode()).hexdigest()})
    overall = hashlib.sha256("|".join(p["hash"] for p in packs).encode()).hexdigest()
    return packs, overall


def pack_name(row: NlmSourceSet, part: int, total: int) -> str:
    return f"ErnestOS · {row.name}" + (f" · {part}/{total}" if total > 1 else "")


def create_set(s: Session, ws: int, *, name: str, notebook_id: int | None = None,
               note_ids=None) -> NlmSourceSet:
    import notes as notes_mod
    import services as svc
    name = " ".join(str(name or "").split())[:120]
    if notebook_id:
        book = s.get(NoteNotebook, int(notebook_id))
        if book is None or book.workspace_id != ws:
            raise svc.NotFound("notebook")
        name = name or book.name
        ids: list[int] = []
    else:
        ids = sorted({int(i) for i in note_ids or []})
        if not ids:
            raise notes_mod.Invalid("no_notes")
        if len(ids) > 2000:
            raise notes_mod.Invalid("too_many_notes")
        mine = set(s.scalars(select(Note.id).where(Note.workspace_id == ws, Note.id.in_(ids))).all())
        if mine != set(ids):
            raise svc.NotFound("note")
    if not name:
        raise notes_mod.Invalid("empty_name")
    if (s.scalar(select(NlmSourceSet.id).where(NlmSourceSet.workspace_id == ws).offset(49).limit(1))):
        raise notes_mod.Invalid("too_many_sets")
    row = NlmSourceSet(workspace_id=ws, name=name, notebook_id=int(notebook_id) if notebook_id else None,
                       note_ids=json.dumps(ids), verify_code=_new_code())
    s.add(row)
    s.flush()
    acct = account(s, ws)
    if acct is not None and acct.status == "connected":
        enqueue(s, ws, "export_set", row.id)
    s.commit()
    kick()
    return row


def update_set(s: Session, ws: int, set_id: int, fields: dict) -> NlmSourceSet:
    import notes as notes_mod
    row = _owned_set(s, ws, set_id)
    if "name" in fields:
        name = " ".join(str(fields["name"] or "").split())[:120]
        if not name:
            raise notes_mod.Invalid("empty_name")
        row.name = name
    if "note_ids" in fields and not row.notebook_id:
        ids = sorted({int(i) for i in fields["note_ids"] or []})
        mine = set(s.scalars(select(Note.id).where(Note.workspace_id == ws, Note.id.in_(ids))).all())
        if not ids or mine != set(ids):
            raise notes_mod.Invalid("no_notes")
        row.note_ids = json.dumps(ids)
    if "nlm_url" in fields:
        url = str(fields["nlm_url"] or "").strip()
        if url:
            m = NLM_URL.match(url)
            if not m:
                raise notes_mod.Invalid("bad_notebook_link")
            url = f"https://notebooklm.google.com/notebook/{m.group(1)}"
            if url != row.nlm_url:
                row.nlm_status, row.verified_at = "waiting", None
        else:
            row.nlm_status, row.verified_at = "none", None
        row.nlm_url = url
    row.updated_at = utcnow()
    s.commit()
    return row


def verify(s: Session, ws: int, set_id: int, answer: str) -> NlmSourceSet:
    """The person asked NotebookLM for the pack's verification code and
    pasted its answer. Matching proves NotebookLM read the exported docs."""
    import notes as notes_mod
    row = _owned_set(s, ws, set_id)
    if not row.nlm_url:
        raise notes_mod.Invalid("notebook_link_first")
    if not row.exported_at:
        raise notes_mod.Invalid("export_first")
    if _norm_code(row.verify_code) not in _norm_code(answer):
        raise notes_mod.Invalid("code_mismatch")
    row.nlm_status, row.verified_at = "verified", utcnow()
    s.commit()
    return row


def sync(s: Session, ws: int, set_id: int) -> None:
    row = _owned_set(s, ws, set_id)
    acct = account(s, ws)
    if acct is None or acct.status != "connected":
        raise ConnectorError("not_connected")
    enqueue(s, ws, "export_set", row.id)
    s.commit()
    kick()


def delete_set(s: Session, ws: int, set_id: int, *, trash_docs: bool) -> str:
    row = _owned_set(s, ws, set_id)
    has_docs = s.scalar(select(NlmPack.id).where(NlmPack.set_id == row.id, NlmPack.drive_file_id != "").limit(1))
    acct = account(s, ws)
    if trash_docs and has_docs:
        if acct is None or acct.status != "connected":
            raise ConnectorError("not_connected")
        row.status = "deleting"
        enqueue(s, ws, "trash_set", row.id, {"delete": True})
        s.commit()
        kick()
        return "deleting"
    _drop_set(s, row.id)
    s.commit()
    return "deleted"


def _drop_set(s: Session, set_id: int) -> None:
    s.execute(sql_delete(NlmPack).where(NlmPack.set_id == set_id))
    s.execute(update(ConnectorJob).where(ConnectorJob.ref_id == set_id,
                                         ConnectorJob.kind.in_(("export_set", "trash_set")),
                                         ConnectorJob.status == "queued").values(status="cancelled"))
    s.execute(sql_delete(NlmSourceSet).where(NlmSourceSet.id == set_id))


# ---- hooks from the notes service -----------------------------------------

def _sets_with(s: Session, ws: int, note: Note) -> set[int]:
    ids = set(s.scalars(select(NlmPack.set_id).where(
        NlmPack.workspace_id == ws, NlmPack.manifest.contains(note.uid))).all())
    return ids


def note_withdrawn(s: Session, ws: int, note_id: int) -> None:
    """A note was trashed or kept out of AI: every pack already carrying it is
    rewritten without it. Runs in the caller's transaction."""
    note = s.get(Note, note_id)
    if note is None:
        return
    acct = account(s, ws)
    if acct is None or acct.status != "connected":
        return
    for set_id in _sets_with(s, ws, note):
        enqueue(s, ws, "export_set", set_id)
    kick()


def notebook_gone(s: Session, ws: int, book_id: int) -> None:
    """A notebook a set followed is deleted: the set keeps the notes it had."""
    for row in s.scalars(select(NlmSourceSet).where(NlmSourceSet.workspace_id == ws,
                                                    NlmSourceSet.notebook_id == book_id)):
        members = s.scalars(select(NoteNotebookMember.note_id).where(
            NoteNotebookMember.notebook_id == book_id)).all()
        row.note_ids = json.dumps(sorted(members))
        row.notebook_id = None


def revocable_tokens(s: Session, ws: int) -> list[str]:
    """Sealed refresh tokens of a workspace, read before its rows are deleted
    (account wipe or deletion) so Google can be told afterwards."""
    return [a.refresh_token_enc for a in s.scalars(select(ConnectorAccount).where(
        ConnectorAccount.workspace_id == ws)) if a.refresh_token_enc]


def revoke_in_background(sealed_tokens: list[str]) -> threading.Thread | None:
    """Tell Google to drop grants whose rows are already gone. Best effort,
    outside any transaction; the token is decrypted only in this thread."""
    tokens = []
    for sealed in sealed_tokens:
        try:
            tokens.append(notes_crypto.decrypt(sealed, purpose="refresh"))
        except Exception:
            continue
    if not tokens:
        return None

    def run():
        google = Google()
        try:
            for token in tokens:
                if not google.revoke(token):
                    log.warning("could not revoke a Drive grant; the person can remove it at "
                                "myaccount.google.com/permissions")
        finally:
            google.close()

    thread = threading.Thread(target=run, daemon=True, name="drive-revoke")
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def enqueue(s: Session, ws: int, kind: str, ref_id: int | None, options: dict | None = None) -> ConnectorJob:
    """One waiting job per (kind, item): asking twice does not do the work
    twice. A job already running is left alone and a new one queued, so a
    change made during a run is picked up by the next."""
    found = s.scalar(select(ConnectorJob).where(
        ConnectorJob.workspace_id == ws, ConnectorJob.kind == kind,
        ConnectorJob.ref_id == ref_id, ConnectorJob.status == "queued"))
    if found is not None:
        found.next_run_at = min(found.next_run_at, utcnow())
        if options:
            found.options = json.dumps(options)
        return found
    job = ConnectorJob(workspace_id=ws, kind=kind, ref_id=ref_id, options=json.dumps(options or {}),
                       next_run_at=utcnow())
    s.add(job)
    s.flush()
    return job


def claim(now: datetime | None = None) -> tuple[int, str] | None:
    """Take one due job: queued and due, or running with a lease that ran out
    (its worker died). The UPDATE's WHERE is the lock: of two workers, one
    changes the row."""
    now = now or utcnow()
    with SessionLocal() as s:
        candidates = s.scalars(select(ConnectorJob.id).where(or_(
            (ConnectorJob.status == "queued") & (ConnectorJob.next_run_at <= now),
            (ConnectorJob.status == "running") & (ConnectorJob.lease_until < now)))
            .order_by(ConnectorJob.next_run_at, ConnectorJob.id).limit(5)).all()
        for job_id in candidates:
            token = secrets.token_hex(16)
            got = s.execute(update(ConnectorJob).where(
                ConnectorJob.id == job_id, or_(
                    (ConnectorJob.status == "queued") & (ConnectorJob.next_run_at <= now),
                    (ConnectorJob.status == "running") & (ConnectorJob.lease_until < now)))
                .values(status="running", lease_token=token, lease_until=now + LEASE,
                        attempts=ConnectorJob.attempts + 1, updated_at=now)).rowcount
            s.commit()
            if got == 1:
                return job_id, token
    return None


def _finish(job_id: int, token: str, *, error: ConnectorError | None = None) -> None:
    with SessionLocal() as s:
        job = s.get(ConnectorJob, job_id)
        if job is None or job.lease_token != token:
            return      # lease lost: whoever holds it now decides
        if error is None:
            job.status, job.last_error = "done", ""
        elif error.retry and job.attempts < MAX_ATTEMPTS:
            delay = min(30 * 2 ** (job.attempts - 1), 3600) + random.randint(0, 15)
            job.status, job.next_run_at, job.last_error = "queued", utcnow() + timedelta(seconds=delay), error.code
        else:
            job.status, job.last_error = "failed", error.code
            if job.kind in ("export_set", "trash_set") and job.ref_id:
                row = s.get(NlmSourceSet, job.ref_id)
                if row is not None:
                    row.status, row.last_error = "error", error.code
            if isinstance(error, Revoked):
                acct = account(s, job.workspace_id)
                if acct is not None:
                    acct.status, acct.last_error = "error", error.code
        job.lease_until, job.updated_at = None, utcnow()
        s.commit()


def _still_mine(s: Session, job_id: int, token: str) -> bool:
    job = s.get(ConnectorJob, job_id)
    return job is not None and job.lease_token == token and job.status == "running"


def _token(s: Session, ws: int) -> tuple[ConnectorAccount, str]:
    acct = account(s, ws)
    if acct is None or not acct.refresh_token_enc:
        raise ConnectorError("not_connected")
    try:
        return acct, notes_crypto.decrypt(acct.refresh_token_enc, purpose="refresh")
    except notes_crypto.KeyMissing:
        raise ConnectorError("key_missing") from None


def run(job_id: int, token: str) -> None:
    """Do one claimed job. Reads in one short session, calls Google with no
    session open, writes the outcome in another."""
    with SessionLocal() as s:
        job = s.get(ConnectorJob, job_id)
        if job is None:
            return
        kind, ws, ref, options = job.kind, job.workspace_id, job.ref_id, json.loads(job.options or "{}")
    try:
        if kind == "export_set":
            _export(job_id, token, ws, ref)
        elif kind == "trash_set":
            _trash(job_id, token, ws, ref, delete=bool(options.get("delete")))
        elif kind == "disconnect":
            _disconnect(job_id, token, ws, trash_docs=bool(options.get("trash_docs")))
        else:
            raise ConnectorError("unknown_job")
    except ConnectorError as e:
        log.info("connector job %s (%s) failed: %s", job_id, kind, e.code)
        _finish(job_id, token, error=e)
        return
    except httpx.HTTPError as e:
        log.info("connector job %s (%s) network: %s", job_id, kind, type(e).__name__)
        _finish(job_id, token, error=ConnectorError("network", retry=True))
        return
    _finish(job_id, token)


def _export(job_id: int, token: str, ws: int, set_id: int) -> None:
    with SessionLocal() as s:
        row = s.get(NlmSourceSet, set_id)
        if row is None or row.workspace_id != ws or row.status == "deleting":
            return
        acct, refresh = _token(s, ws)
        if acct.status != "connected":
            raise ConnectorError("not_connected")
        packs, overall = build(s, ws, row, eligible(s, ws, row))
        name = row.name
        folder = acct.folder_id
        known = {p.part: (p.drive_file_id, p.content_hash) for p in s.scalars(
            select(NlmPack).where(NlmPack.set_id == set_id))}
        row.status = "exporting"
        s.commit()
    google = Google()
    results: dict[int, str] = {}
    recreated = False
    try:
        access = google.access(refresh)
        about = google.about(access)
        mime = _upload_mime(about)
        if not folder or google.file(access, folder) is None:
            folder = google.folder(access)
        total = len(packs)
        for p in packs:
            file_id, old_hash = known.get(p["part"], ("", ""))
            title = f"ErnestOS · {name}" + (f" · {p['part']}/{total}" if total > 1 else "")
            if file_id and old_hash == p["hash"] and google.file(access, file_id) is not None:
                results[p["part"]] = file_id
                continue
            if file_id:
                try:
                    google.update_doc(access, file_id, title, p["content"], mime)
                    results[p["part"]] = file_id
                    continue
                except Gone:
                    recreated = True     # the person deleted the doc in Drive
            results[p["part"]] = google.create_doc(access, folder, title, p["content"], mime)
        extra = [fid for part, (fid, _) in known.items() if part > total and fid]
        for fid in extra:
            google.trash(access, fid)
    finally:
        google.close()
    with SessionLocal() as s:
        row = s.get(NlmSourceSet, set_id)
        acct = account(s, ws)
        if acct is not None and folder:
            acct.folder_id = folder
            acct.last_ok_at = utcnow()
        if row is None:
            # Deleted meanwhile: the docs just made would be orphans.
            s.commit()
            return
        mine = _still_mine(s, job_id, token)
        for p in packs:
            pack = s.scalar(select(NlmPack).where(NlmPack.set_id == set_id, NlmPack.part == p["part"]))
            if pack is None:
                pack = NlmPack(workspace_id=ws, set_id=set_id, part=p["part"])
                s.add(pack)
            elif not mine and pack.drive_file_id:
                continue    # a newer run owns this pack now
            pack.drive_file_id = results[p["part"]]
            pack.content_hash = p["hash"]
            pack.manifest = json.dumps(p["manifest"], ensure_ascii=False)
            pack.chars = len(p["content"])
            pack.status, pack.exported_at = "exported", utcnow()
        if mine:
            s.execute(sql_delete(NlmPack).where(NlmPack.set_id == set_id, NlmPack.part > len(packs)))
            row.status, row.content_hash, row.exported_at = "exported", overall, utcnow()
            row.last_error = "doc_recreated" if recreated else ""
            if recreated and row.nlm_status == "verified":
                # The NotebookLM source pointed at the deleted doc.
                row.nlm_status, row.verified_at = "waiting", None
            row.updated_at = utcnow()
        s.commit()


def _trash(job_id: int, token: str, ws: int, set_id: int, *, delete: bool) -> None:
    with SessionLocal() as s:
        acct, refresh = _token(s, ws)
        files = [p.drive_file_id for p in s.scalars(select(NlmPack).where(
            NlmPack.set_id == set_id, NlmPack.workspace_id == ws)) if p.drive_file_id]
    google = Google()
    try:
        access = google.access(refresh)
        for fid in files:
            google.trash(access, fid)
    finally:
        google.close()
    with SessionLocal() as s:
        if delete:
            _drop_set(s, set_id)
        else:
            s.execute(update(NlmPack).where(NlmPack.set_id == set_id).values(drive_file_id="", status="pending"))
        s.commit()


def _disconnect(job_id: int, token: str, ws: int, *, trash_docs: bool) -> None:
    with SessionLocal() as s:
        acct = account(s, ws)
        if acct is None:
            return
        sealed = acct.refresh_token_enc
        files = [p.drive_file_id for p in s.scalars(select(NlmPack).where(
            NlmPack.workspace_id == ws)) if p.drive_file_id] if trash_docs else []
    revoked = True
    if sealed:
        try:
            refresh = notes_crypto.decrypt(sealed, purpose="refresh")
        except Exception:
            refresh = None
        if refresh:
            google = Google()
            try:
                if files:
                    try:
                        access = google.access(refresh)
                        for fid in files:
                            google.trash(access, fid)
                    except Revoked:
                        pass
                revoked = google.revoke(refresh)
            finally:
                google.close()
    if not revoked:
        with SessionLocal() as s:
            job = s.get(ConnectorJob, job_id)
            if job is not None and job.attempts < MAX_ATTEMPTS:
                raise ConnectorError("revoke_failed", retry=True)
    with SessionLocal() as s:
        if trash_docs:
            s.execute(update(NlmPack).where(NlmPack.workspace_id == ws).values(drive_file_id="", status="pending"))
        else:
            s.execute(update(NlmPack).where(NlmPack.workspace_id == ws, NlmPack.drive_file_id != "")
                      .values(status="orphan"))
        s.execute(update(NlmSourceSet).where(NlmSourceSet.workspace_id == ws)
                  .values(status="draft", content_hash=""))
        s.execute(update(ConnectorJob).where(ConnectorJob.workspace_id == ws, ConnectorJob.status == "queued",
                                             ConnectorJob.kind != "disconnect").values(status="cancelled"))
        s.execute(sql_delete(ConnectorAccount).where(ConnectorAccount.workspace_id == ws))
        if not revoked:
            log.warning("Drive grant could not be revoked after %s tries; token deleted locally", MAX_ATTEMPTS)
        s.commit()


def process_due(limit: int = 10) -> int:
    """Run up to `limit` due jobs, one after another. Returns how many ran."""
    done = 0
    while done < limit:
        claimed = claim()
        if claimed is None:
            break
        run(*claimed)
        done += 1
    return done


# ---- the worker ---------------------------------------------------------------

_wake: asyncio.Event | None = None
_loop: asyncio.AbstractEventLoop | None = None


def kick() -> None:
    """Ask the worker to look now instead of at its next tick."""
    if _wake is not None and _loop is not None:
        try:
            _loop.call_soon_threadsafe(_wake.set)
        except RuntimeError:
            pass


async def worker(interval: float = 20.0) -> None:
    """Runs for the life of the process. Each pass happens in a thread, so
    the network never blocks the API."""
    global _wake, _loop
    _wake, _loop = asyncio.Event(), asyncio.get_running_loop()
    while True:
        try:
            await asyncio.to_thread(process_due)
        except Exception:
            log.exception("connector worker pass failed")
        try:
            await asyncio.wait_for(_wake.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
        _wake.clear()


# ---------------------------------------------------------------------------
# What the app shows
# ---------------------------------------------------------------------------

def _doc_url(file_id: str) -> str:
    return f"https://docs.google.com/document/d/{file_id}/edit" if file_id else ""


def status(s: Session, ws: int) -> dict:
    acct = account(s, ws)
    sets = []
    books = {b.id: b.name for b in s.scalars(select(NoteNotebook).where(NoteNotebook.workspace_id == ws))}
    for row in s.scalars(select(NlmSourceSet).where(NlmSourceSet.workspace_id == ws).order_by(NlmSourceSet.id)):
        notes = eligible(s, ws, row)
        try:
            _, current = build(s, ws, row, notes)
        except ConnectorError:
            current = ""
        packs = s.scalars(select(NlmPack).where(NlmPack.set_id == row.id).order_by(NlmPack.part)).all()
        job = s.scalar(select(ConnectorJob).where(ConnectorJob.ref_id == row.id,
                                                  ConnectorJob.kind.in_(("export_set", "trash_set")))
                       .order_by(ConnectorJob.id.desc()).limit(1))
        sets.append({
            "id": row.id, "name": row.name, "notebook_id": row.notebook_id,
            "notebook": books.get(row.notebook_id or 0),
            "note_ids": json.loads(row.note_ids or "[]") if not row.notebook_id else None,
            "notes": len(notes), "status": row.status,
            "changed": bool(row.exported_at) and current != row.content_hash,
            "exported_at": row.exported_at.isoformat() + "Z" if row.exported_at else None,
            "packs": [{"part": p.part, "doc_url": _doc_url(p.drive_file_id), "chars": p.chars,
                       "notes": len(json.loads(p.manifest or "[]")), "status": p.status}
                      for p in packs],
            "nlm_url": row.nlm_url, "nlm_status": row.nlm_status,
            "verified_at": row.verified_at.isoformat() + "Z" if row.verified_at else None,
            "last_error": row.last_error,
            "job": ({"status": job.status, "attempts": job.attempts, "error": job.last_error,
                     "next_run_at": job.next_run_at.isoformat() + "Z"} if job else None),
        })
    return {
        "configured": not missing_config(),
        "missing": missing_config(),
        "scope": SCOPE,
        "account": ({"status": acct.status, "email": acct.account_email, "name": acct.account_name,
                     "connected_at": acct.connected_at.isoformat() + "Z" if acct.connected_at else None,
                     "last_ok_at": acct.last_ok_at.isoformat() + "Z" if acct.last_ok_at else None,
                     "last_error": acct.last_error} if acct else None),
        "sets": sets,
        "edition": "consumer",
    }


def owner_language(ws: int | None) -> str:
    if ws is None:
        return "uz"
    with SessionLocal() as s:
        uid = s.scalar(select(Workspace.user_id).where(Workspace.id == ws))
        user = s.get(User, uid) if uid else None
        return (user.language if user and user.language in ("uz", "en", "ru") else "uz")
