"""The Google Drive / NotebookLM connector, against a recorded-protocol fixture.

These tests drive the real connector code over httpx with a transport that
answers like Google's documented endpoints (token, revoke, Drive v3). They
prove the flow and the safety properties; they are NOT a live verification
with Google — see docs/NOTES_NOTEBOOKLM_SETUP.md for the live check.
"""
import base64
import hashlib
import json
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import func, select

from test_smoke import client, fresh, Caller, _next_id, _ws  # noqa: F401
import db
import notes_connector as conn
import notes_crypto
from db import SessionLocal

KEY = base64.urlsafe_b64encode(b"k" * 32).decode()


class FakeGoogle:
    """Answers the way Google's documented endpoints do, and records calls."""

    def __init__(self):
        self.challenges = {}       # state -> code_challenge, filled by the test from the auth URL
        self.codes = {}            # code -> state
        self.refresh_tokens = {"rt-1": True}
        self.files = {}            # id -> {"name", "content", "mime", "trashed", "parents"}
        self.calls = []
        self.revoked = []
        self.fail = []             # status codes to answer the next Drive calls with
        self.markdown = True
        self.scope = conn.SCOPE
        self.n = 0

    def _id(self):
        self.n += 1
        return f"file{self.n:04d}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.calls.append((request.method, url.host + url.path))
        if url.host == "oauth2.googleapis.com" and url.path == "/token":
            form = parse_qs(request.content.decode())
            grant = form["grant_type"][0]
            if grant == "authorization_code":
                state = self.codes.get(form["code"][0])
                verifier = form.get("code_verifier", [""])[0]
                want = self.challenges.get(state)
                got = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
                if want is None or got != want:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-1",
                                                 "scope": self.scope, "expires_in": 3599})
            token = form["refresh_token"][0]
            if not self.refresh_tokens.get(token):
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "at-2", "expires_in": 3599})
        if url.host == "oauth2.googleapis.com" and url.path == "/revoke":
            token = parse_qs(request.content.decode())["token"][0]
            self.revoked.append(token)
            self.refresh_tokens.pop(token, None)
            return httpx.Response(200)
        assert request.headers.get("authorization", "").startswith("Bearer at-"), "Drive call without a token"
        if self.fail:
            code = self.fail.pop(0)
            return httpx.Response(code, json={"error": {"errors": [{"reason": "backendError"}]}})
        if url.path == "/drive/v3/about":
            fmts = {"text/markdown": ["application/vnd.google-apps.document"]} if self.markdown else {}
            return httpx.Response(200, json={"user": {"emailAddress": "student@example.com",
                                                      "displayName": "Student"}, "importFormats": fmts})
        m = re.fullmatch(r"/drive/v3/files/([\w-]+)", url.path)
        if m and request.method == "GET":
            f = self.files.get(m.group(1))
            if f is None:
                return httpx.Response(404, json={"error": {"errors": [{"reason": "notFound"}]}})
            return httpx.Response(200, json={"id": m.group(1), "trashed": f["trashed"], "mimeType": f["mime"]})
        if m and request.method == "PATCH":
            f = self.files.get(m.group(1))
            if f is None:
                return httpx.Response(404, json={"error": {"errors": [{"reason": "notFound"}]}})
            f["trashed"] = json.loads(request.content).get("trashed", f["trashed"])
            return httpx.Response(200, json={"id": m.group(1)})
        if url.path == "/drive/v3/files" and request.method == "POST":
            meta = json.loads(request.content)
            fid = self._id()
            self.files[fid] = {"name": meta["name"], "content": "", "mime": meta["mimeType"], "trashed": False,
                               "parents": meta.get("parents", [])}
            return httpx.Response(200, json={"id": fid})
        if url.path == "/upload/drive/v3/files" and request.method == "POST":
            meta, media, mime = self._parts(request)
            fid = self._id()
            self.files[fid] = {"name": meta["name"], "content": media, "mime": meta["mimeType"],
                               "trashed": False, "parents": meta.get("parents", []), "source_mime": mime}
            return httpx.Response(200, json={"id": fid})
        m = re.fullmatch(r"/upload/drive/v3/files/([\w-]+)", url.path)
        if m and request.method == "PATCH":
            f = self.files.get(m.group(1))
            if f is None or f["trashed"]:
                return httpx.Response(404, json={"error": {"errors": [{"reason": "notFound"}]}})
            meta, media, mime = self._parts(request)
            f.update({"name": meta["name"], "content": media, "source_mime": mime})
            return httpx.Response(200, json={"id": m.group(1)})
        return httpx.Response(400, json={"error": "unexpected " + url.path})

    @staticmethod
    def _parts(request):
        boundary = request.headers["content-type"].split("boundary=")[1]
        chunks = request.content.decode().split(f"--{boundary}")
        meta = json.loads(chunks[1].split("\r\n\r\n", 1)[1].rstrip("\r\n"))
        head, media = chunks[2].split("\r\n\r\n", 1)
        mime = re.search(r"Content-Type: ([^;\r\n]+)", head).group(1)
        return meta, media[:-2] if media.endswith("\r\n") else media, mime

    def consent(self, auth_url: str, code: str = "code-1") -> tuple[str, str]:
        """What the person's browser does on Google's page: approve, come back."""
        q = parse_qs(urlparse(auth_url).query)
        state = q["state"][0]
        self.challenges[state] = q["code_challenge"][0]
        self.codes[code] = state
        return state, code


@pytest.fixture()
def google(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(conn, "TRANSPORT", httpx.MockTransport(fake.handler))
    monkeypatch.setenv("GOOGLE_DRIVE_CLIENT_ID", "client-123.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_DRIVE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("PUBLIC_URL", "https://ernestos.example")
    monkeypatch.setenv("CONNECTOR_TOKEN_KEY", KEY)
    return fake


def connect(caller, google) -> None:
    url = caller.post("/api/notes/connector/google/start").json()["url"]
    state, code = google.consent(url)
    page = caller.c.get(f"/api/notes/connector/google/callback?state={state}&code={code}")
    assert page.status_code == 200 and "✅" in page.text
    assert caller.post("/api/notes/connector/confirm").json()["account"]["status"] == "connected"


def run_jobs():
    return conn.process_due()


def note(caller, **body):
    r = caller.post("/api/notes", json=body)
    assert r.status_code == 201
    return r.json()


# --- setup and consent -------------------------------------------------------------

def test_without_settings_the_connector_says_what_is_missing(fresh, monkeypatch):
    for name in ("GOOGLE_DRIVE_CLIENT_ID", "GOOGLE_CLIENT_ID", "CONNECTOR_TOKEN_KEY", "PUBLIC_URL",
                 "WEBHOOK_URL", "WEBAPP_URL"):
        monkeypatch.delenv(name, raising=False)
    st = fresh.get("/api/notes/connector").json()
    assert st["configured"] is False and "CONNECTOR_TOKEN_KEY" in st["missing"]
    r = fresh.post("/api/notes/connector/google/start")
    assert r.status_code == 503 and r.json()["detail"] == "connector_not_configured"


def test_consent_url_asks_only_for_drive_file_with_pkce_and_no_credentials(fresh, google):
    url = fresh.post("/api/notes/connector/google/start").json()["url"]
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert q["scope"] == ["https://www.googleapis.com/auth/drive.file"]
    assert q["code_challenge_method"] == ["S256"] and len(q["code_challenge"][0]) == 43
    assert q["access_type"] == ["offline"] and q["redirect_uri"] == ["https://ernestos.example/api/notes/connector/google/callback"]
    assert "hash=" not in url and "user=" not in url and "init" not in url.lower()
    with SessionLocal() as s:
        row = s.get(db.ConnectorOAuthState, hashlib.sha256(q["state"][0].encode()).hexdigest())
        assert row is not None and row.workspace_id == _ws(fresh.user["id"])
        assert row.verifier_enc.startswith("v1:")     # the PKCE verifier is sealed
        assert q["state"][0] not in row.verifier_enc


def test_the_refresh_token_is_sealed_and_never_returned(fresh, google):
    connect(fresh, google)
    with SessionLocal() as s:
        acct = s.scalar(select(db.ConnectorAccount).where(db.ConnectorAccount.workspace_id == _ws(fresh.user["id"])))
        assert acct.refresh_token_enc.startswith("v1:") and "rt-1" not in acct.refresh_token_enc
        assert notes_crypto.decrypt(acct.refresh_token_enc, purpose="refresh") == "rt-1"
    for path in ("/api/notes/connector", "/api/export"):
        r = fresh.get(path)
        assert "rt-1" not in r.text and "at-1" not in r.text and "refresh_token" not in r.text


def test_a_state_works_once_and_not_after_ten_minutes(fresh, google):
    url = fresh.post("/api/notes/connector/google/start").json()["url"]
    state, code = google.consent(url)
    assert "✅" in fresh.c.get(f"/api/notes/connector/google/callback?state={state}&code={code}").text
    again = fresh.c.get(f"/api/notes/connector/google/callback?state={state}&code={code}")
    assert "⚠️" in again.text
    url = fresh.post("/api/notes/connector/google/start").json()["url"]
    state, code = google.consent(url, "code-2")
    with SessionLocal() as s:
        for row in s.scalars(select(db.ConnectorOAuthState)):
            row.created_at -= timedelta(minutes=11)
        s.commit()
    assert "⚠️" in fresh.c.get(f"/api/notes/connector/google/callback?state={state}&code={code}").text


def test_a_wrong_verifier_or_an_unticked_drive_box_does_not_connect(fresh, google):
    url = fresh.post("/api/notes/connector/google/start").json()["url"]
    state, code = google.consent(url)
    google.challenges[state] = "x" * 43               # PKCE mismatch at Google
    assert "⚠️" in fresh.c.get(f"/api/notes/connector/google/callback?state={state}&code={code}").text
    google.scope = "openid"
    url = fresh.post("/api/notes/connector/google/start").json()["url"]
    state, code = google.consent(url, "code-3")
    page = fresh.c.get(f"/api/notes/connector/google/callback?state={state}&code={code}")
    assert "scope_denied" in page.text and google.revoked
    assert fresh.get("/api/notes/connector").json()["account"] is None


def test_the_account_must_be_confirmed_in_the_app_before_anything_is_sent(fresh, google):
    url = fresh.post("/api/notes/connector/google/start").json()["url"]
    state, code = google.consent(url)
    fresh.c.get(f"/api/notes/connector/google/callback?state={state}&code={code}")
    st = fresh.get("/api/notes/connector").json()
    assert st["account"]["status"] == "pending_confirm" and st["account"]["email"] == "student@example.com"
    n = note(fresh, title="x")
    fresh.post("/api/notes/connector/sets", json={"name": "Set", "note_ids": [n["id"]]})
    run_jobs()
    assert not any("upload" in p for _, p in google.calls)


# --- source packs ---------------------------------------------------------------

def test_a_notebook_becomes_a_google_doc_that_is_updated_in_place(fresh, google):
    connect(fresh, google)
    book = fresh.post("/api/notes/notebooks", json={"name": "Marketing"}).json()
    a = note(fresh, title="Lecture 1", body="The 4P model.", notebook_ids=[book["id"]], tags=["uni"])
    note(fresh, title="Lecture 2", body="Positioning.", notebook_ids=[book["id"]])
    fresh.post("/api/notes/connector/sets", json={"notebook_id": book["id"]})
    assert run_jobs() == 1
    st = fresh.get("/api/notes/connector").json()["sets"][0]
    assert st["status"] == "exported" and st["notes"] == 2 and st["changed"] is False
    doc_id = st["packs"][0]["doc_url"].split("/d/")[1].split("/")[0]
    doc = google.files[doc_id]
    assert doc["mime"] == "application/vnd.google-apps.document" and doc["source_mime"] == "text/markdown"
    assert doc["name"] == "ErnestOS · Marketing"
    assert "ErnestOS verification code:" in doc["content"] and f"note:{a['uid']}" in doc["content"]
    assert "## Manifest" in doc["content"] and "The 4P model." in doc["content"]
    folder = doc["parents"][0]
    assert google.files[folder]["name"] == conn.FOLDER_NAME
    # Nothing changed: no upload.
    uploads = sum(1 for _, p in google.calls if "upload" in p)
    fresh.post(f"/api/notes/connector/sets/{st['id']}/sync")
    run_jobs()
    assert sum(1 for _, p in google.calls if "upload" in p) == uploads
    # A change: the same document, rewritten.
    full = fresh.get(f"/api/notes/{a['id']}").json()
    fresh.patch(f"/api/notes/{a['id']}", json={"base_revision": full["revision"], "body": "The 7P model."})
    assert fresh.get("/api/notes/connector").json()["sets"][0]["changed"] is True
    fresh.post(f"/api/notes/connector/sets/{st['id']}/sync")
    run_jobs()
    st2 = fresh.get("/api/notes/connector").json()["sets"][0]
    assert st2["packs"][0]["doc_url"] == st["packs"][0]["doc_url"]
    assert "The 7P model." in google.files[doc_id]["content"] and "4P" not in google.files[doc_id]["content"]


def test_plain_text_is_sent_where_drive_does_not_import_markdown(fresh, google):
    google.markdown = False
    connect(fresh, google)
    n = note(fresh, title="x", body="y")
    fresh.post("/api/notes/connector/sets", json={"name": "S", "note_ids": [n["id"]]})
    run_jobs()
    doc = [f for f in google.files.values() if f["mime"].endswith("document")][0]
    assert doc["source_mime"] == "text/plain"


def test_trashing_or_excluding_a_note_rewrites_the_docs_without_it(fresh, google):
    connect(fresh, google)
    keep = note(fresh, title="Keep", body="public part")
    drop = note(fresh, title="Drop", body="SECRET-1")
    hide = note(fresh, title="Hide", body="SECRET-2")
    fresh.post("/api/notes/connector/sets", json={"name": "S", "note_ids": [keep["id"], drop["id"], hide["id"]]})
    run_jobs()
    doc = next(f for f in google.files.values() if f["mime"].endswith("document"))
    assert "SECRET-1" in doc["content"] and "SECRET-2" in doc["content"]
    fresh.post(f"/api/notes/{drop['id']}/trash")
    fresh.patch(f"/api/notes/{hide['id']}", json={"ai_excluded": True})
    run_jobs()
    assert "SECRET-1" not in doc["content"] and "SECRET-2" not in doc["content"]
    assert "public part" in doc["content"]


def test_a_big_set_is_split_into_bounded_packs_and_extra_docs_are_trashed(fresh, google, monkeypatch):
    monkeypatch.setattr(conn, "PACK_CHARS", 21_000)
    connect(fresh, google)
    ids = [note(fresh, title=f"N{i}", body="w " * 1500)["id"] for i in range(6)]
    fresh.post("/api/notes/connector/sets", json={"name": "Big", "note_ids": ids})
    run_jobs()
    st = fresh.get("/api/notes/connector").json()["sets"][0]
    assert len(st["packs"]) >= 3 and all(p["chars"] <= 21_000 for p in st["packs"])
    for nid in ids[1:]:
        fresh.post(f"/api/notes/{nid}/trash")
    run_jobs()
    st = fresh.get("/api/notes/connector").json()["sets"][0]
    assert len(st["packs"]) == 1
    assert sum(1 for f in google.files.values() if f["mime"].endswith("document") and f["trashed"]) >= 2


def test_a_doc_deleted_in_drive_is_made_again_and_the_notebook_must_re_add_it(fresh, google):
    connect(fresh, google)
    n = note(fresh, title="x", body="one")
    sid = fresh.post("/api/notes/connector/sets", json={"name": "S", "note_ids": [n["id"]]}).json()["id"]
    run_jobs()
    st = fresh.get("/api/notes/connector").json()["sets"][0]
    fresh.patch(f"/api/notes/connector/sets/{sid}", json={"nlm_url": "https://notebooklm.google.com/notebook/abcDEF123456"})
    code = re.search(r"verification code: (\S+)", next(f for f in google.files.values()
                                                       if f["mime"].endswith("document"))["content"]).group(1)
    fresh.post(f"/api/notes/connector/sets/{sid}/verify", json={"answer": f"The code is {code}."})
    old = st["packs"][0]["doc_url"].split("/d/")[1].split("/")[0]
    google.files.pop(old)
    full = fresh.get(f"/api/notes/{n['id']}").json()
    fresh.patch(f"/api/notes/{n['id']}", json={"base_revision": full["revision"], "body": "two"})
    fresh.post(f"/api/notes/connector/sets/{sid}/sync")
    run_jobs()
    st = fresh.get("/api/notes/connector").json()["sets"][0]
    assert old not in st["packs"][0]["doc_url"] and st["last_error"] == "doc_recreated"
    assert st["nlm_status"] == "waiting"


# --- NotebookLM statuses ----------------------------------------------------------------

def test_exported_waiting_and_verified_are_distinct(fresh, google):
    connect(fresh, google)
    n = note(fresh, title="x", body="y")
    sid = fresh.post("/api/notes/connector/sets", json={"name": "S", "note_ids": [n["id"]]}).json()["id"]
    run_jobs()
    st = fresh.get("/api/notes/connector").json()["sets"][0]
    assert (st["status"], st["nlm_status"]) == ("exported", "none")
    for bad in ("https://notebooklm.google.com.evil.io/notebook/abcdefgh", "javascript:alert(1)",
                "http://notebooklm.google.com/notebook/abcdefgh", "https://notebooklm.google.com/notebook/../x"):
        assert fresh.patch(f"/api/notes/connector/sets/{sid}", json={"nlm_url": bad}).json()["detail"] == "bad_notebook_link"
    assert fresh.post(f"/api/notes/connector/sets/{sid}/verify", json={"answer": "x"}).json()["detail"] == "notebook_link_first"
    st = fresh.patch(f"/api/notes/connector/sets/{sid}",
                     json={"nlm_url": "https://notebooklm.google.com/notebook/0a1b2c3d-4e5f?authuser=0"}).json()["sets"][0]
    assert st["nlm_status"] == "waiting" and st["nlm_url"] == "https://notebooklm.google.com/notebook/0a1b2c3d-4e5f"
    assert "verify_code" not in json.dumps(st)            # the app never shows the code
    assert fresh.post(f"/api/notes/connector/sets/{sid}/verify", json={"answer": "I think it is ABCD-EFGH"}).json()["detail"] == "code_mismatch"
    code = re.search(r"verification code: (\S+)", next(f for f in google.files.values()
                                                       if f["mime"].endswith("document"))["content"]).group(1)
    st = fresh.post(f"/api/notes/connector/sets/{sid}/verify", json={"answer": code.lower().replace("-", " ")}).json()["sets"][0]
    assert st["nlm_status"] == "verified" and st["verified_at"]


# --- jobs ---------------------------------------------------------------------------------

def test_a_failing_drive_call_is_retried_with_backoff_then_gives_up(fresh, google):
    connect(fresh, google)
    n = note(fresh, title="x")
    fresh.post("/api/notes/connector/sets", json={"name": "S", "note_ids": [n["id"]]})
    google.fail = [503]
    run_jobs()
    with SessionLocal() as s:
        job = s.scalar(select(db.ConnectorJob).where(db.ConnectorJob.kind == "export_set").order_by(db.ConnectorJob.id.desc()))
        assert job.status == "queued" and job.attempts == 1 and job.next_run_at > db.utcnow()
        job.next_run_at = db.utcnow()
        s.commit()
    run_jobs()
    st = fresh.get("/api/notes/connector").json()["sets"][0]
    assert st["status"] == "exported"
    google.fail = [503] * 20
    fresh.post(f"/api/notes/connector/sets/{st['id']}/delete", json={"trash_docs": False})
    n2 = note(fresh, title="y")
    fresh.post("/api/notes/connector/sets", json={"name": "T", "note_ids": [n2["id"]]})
    for _ in range(conn.MAX_ATTEMPTS):
        with SessionLocal() as s:
            for j in s.scalars(select(db.ConnectorJob).where(db.ConnectorJob.status == "queued")):
                j.next_run_at = db.utcnow()
            s.commit()
        run_jobs()
    st = fresh.get("/api/notes/connector").json()["sets"][0]
    assert st["status"] == "error" and st["job"]["status"] == "failed"


def test_a_job_whose_worker_died_is_taken_over_once_its_lease_runs_out(fresh, google):
    connect(fresh, google)
    run_jobs()                       # nothing else due: the claim below is this set's
    n = note(fresh, title="x")
    fresh.post("/api/notes/connector/sets", json={"name": "S", "note_ids": [n["id"]]})
    claimed = conn.claim()
    assert claimed is not None and conn.claim() is None          # leased: nobody else takes it
    with SessionLocal() as s:
        job = s.get(db.ConnectorJob, claimed[0])
        job.lease_until = db.utcnow() - timedelta(seconds=1)      # the worker died
        s.commit()
    taken = conn.claim()
    assert taken[0] == claimed[0] and taken[1] != claimed[1]
    conn.run(*claimed)               # the dead worker wakes up late: its result is not recorded as the job's
    with SessionLocal() as s:
        assert s.get(db.ConnectorJob, claimed[0]).status == "running"
    conn.run(*taken)
    with SessionLocal() as s:
        job = s.get(db.ConnectorJob, claimed[0])
        assert job.status == "done"
        packs = s.scalars(select(db.NlmPack).where(db.NlmPack.set_id == job.ref_id)).all()
    assert len(packs) == 1 and packs[0].drive_file_id      # one doc, not two
    assert sum(1 for f in google.files.values() if f["mime"].endswith("document")) == 1


def test_revoked_access_asks_to_reconnect(fresh, google):
    connect(fresh, google)
    n = note(fresh, title="x")
    google.refresh_tokens.clear()
    fresh.post("/api/notes/connector/sets", json={"name": "S", "note_ids": [n["id"]]})
    run_jobs()
    st = fresh.get("/api/notes/connector").json()
    assert st["account"]["status"] == "error" and st["account"]["last_error"] == "reauth_required"


# --- leaving ----------------------------------------------------------------------------------

def test_disconnect_revokes_the_grant_and_can_trash_the_docs(fresh, google):
    connect(fresh, google)
    n = note(fresh, title="x")
    fresh.post("/api/notes/connector/sets", json={"name": "S", "note_ids": [n["id"]]})
    run_jobs()
    fresh.post("/api/notes/connector/disconnect", json={"trash_docs": True})
    assert fresh.get("/api/notes/connector").json()["account"]["status"] == "disconnecting"
    run_jobs()
    assert google.revoked == ["rt-1"]
    assert all(f["trashed"] for f in google.files.values() if f["mime"].endswith("document"))
    st = fresh.get("/api/notes/connector").json()
    assert st["account"] is None and st["sets"][0]["status"] == "draft"


def test_deleting_the_account_revokes_the_grant_and_leaves_no_connector_rows(client, google):
    c = Caller(client, {"id": next(_next_id), "first_name": "D"})
    connect(c, google)
    n = note(c, title="x")
    c.post("/api/notes/connector/sets", json={"name": "S", "note_ids": [n["id"]]})
    run_jobs()
    ws = _ws(c.user["id"])
    started = []
    real = conn.revoke_in_background
    conn.revoke_in_background = lambda tokens: started.append(real(tokens))
    try:
        c.post("/api/account/delete", json={"confirm": "DELETE"})
    finally:
        conn.revoke_in_background = real
    for thread in started:
        if thread:
            thread.join(5)
    assert google.revoked == ["rt-1"]
    with SessionLocal() as s:
        for model in (db.ConnectorAccount, db.ConnectorJob, db.NlmPack, db.NlmSourceSet, db.ConnectorOAuthState):
            assert s.scalar(select(func.count()).select_from(model).where(model.workspace_id == ws)) == 0


def test_another_account_cannot_touch_a_source_set(client, google):
    a = Caller(client, {"id": next(_next_id), "first_name": "A"})
    b = Caller(client, {"id": next(_next_id), "first_name": "B"})
    n = note(a, title="x")
    sid = a.post("/api/notes/connector/sets", json={"name": "S", "note_ids": [n["id"]]}).json()["id"]
    assert b.post("/api/notes/connector/sets", json={"name": "S", "note_ids": [n["id"]]}).status_code == 404
    for r in (b.patch(f"/api/notes/connector/sets/{sid}", json={"name": "x"}),
              b.post(f"/api/notes/connector/sets/{sid}/verify", json={"answer": "x"}),
              b.post(f"/api/notes/connector/sets/{sid}/delete", json={})):
        assert r.status_code == 404
    assert b.get("/api/notes/connector").json()["sets"] == []
