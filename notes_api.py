"""HTTP door to Notes and the Google Drive / NotebookLM connector.

Identity is the signed header, as everywhere in ErnestOS: no body or query
ever names the workspace. The one route without it is Google's redirect
back (`/connector/google/callback`), which is tied to the workspace by the
one-time state the signed `/connector/google/start` created.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from datetime import date
from html import escape
from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

import config
import notes
import notes_connector as conn
import notes_portable as portable
import services as svc
from db import NoteAttachment, NoteNotebook, SessionLocal

log = logging.getLogger("ernestos.notes")

KEY = r"^[A-Za-z0-9_:-]{8,80}$"
INIT = Header(default=None, alias="X-Telegram-Init-Data")


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="", max_length=notes.TITLE_MAX)
    body: str = Field(default="", max_length=notes.BODY_MAX)
    kind: str = Field(default="", max_length=12)
    template: str = Field(default="blank", max_length=20)
    notebook_ids: list[int] = Field(default_factory=list, max_length=50)
    tags: list[str] = Field(default_factory=list, max_length=40)
    status: str = Field(default="inbox", max_length=10)
    source_url: str = Field(default="", max_length=1000)
    pinned: bool = False


class NotePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, max_length=notes.TITLE_MAX)
    body: str | None = Field(default=None, max_length=notes.BODY_MAX)
    kind: str | None = Field(default=None, max_length=12)
    status: str | None = Field(default=None, max_length=10)
    pinned: bool | None = None
    archived: bool | None = None
    ai_excluded: bool | None = None
    tags: list[str] | None = Field(default=None, max_length=40)
    notebook_ids: list[int] | None = Field(default=None, max_length=50)
    source_url: str | None = Field(default=None, max_length=1000)


class RevisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: int = Field(ge=1)


class BookIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    color: str | None = Field(default=None, max_length=10)
    icon: str | None = Field(default=None, max_length=8)
    position: int | None = Field(default=None, ge=0, le=10_000)
    archived: bool | None = None


class RelationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(max_length=10)
    target_id: int = Field(ge=1)


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(default="", max_length=4000)


class ToTaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    deadline: date | None = None
    project_id: int | None = Field(default=None, ge=1)
    request_key: str = Field(pattern=KEY)


class ImportCommitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exclude: list[str] = Field(default_factory=list, max_length=5000)


class SetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=120)
    notebook_id: int | None = Field(default=None, ge=1)
    note_ids: list[int] = Field(default_factory=list, max_length=2000)


class SetPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=120)
    note_ids: list[int] | None = Field(default=None, max_length=2000)
    nlm_url: str | None = Field(default=None, max_length=300)


class VerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=500)


class DownloadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    what: str = Field(pattern="^(zip|obsidian|ai)$")
    notebook_id: int | None = Field(default=None, ge=1)


#: A download link lives five minutes and opens one export of one workspace.
#: It exists because Telegram's in-app browser cannot save a file fetched
#: with a header; it is not initData and grants nothing else.
DL_TTL = 300


def _sign(payload: str) -> str:
    return hmac.new(config.BOT_TOKEN.encode(), b"notes-dl:" + payload.encode(), hashlib.sha256).hexdigest()[:40]


def download_token(ws: int, what: str, notebook_id: int | None, *, now: float | None = None) -> str:
    payload = f"{ws}.{what}.{notebook_id or 0}.{int((now or time.time()) + DL_TTL)}"
    return f"{payload}.{_sign(payload)}"


def read_download_token(token: str, *, now: float | None = None) -> tuple[int, str, int | None]:
    try:
        ws, what, book, expires, sig = token.split(".")
        payload = f"{ws}.{what}.{book}.{expires}"
        if not hmac.compare_digest(_sign(payload), sig) or int(expires) < (now or time.time()):
            raise ValueError
        return int(ws), what, int(book) or None
    except ValueError:
        raise HTTPException(status_code=404, detail="not_found") from None


def _file(s, ws: int, what: str, notebook_id: int | None) -> Response:
    if what == "ai":
        return Response(notes.ai_export(s, ws), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="ernestos-ai-memory.md"',
                                 "Cache-Control": "no-store"})
    fmt = "obsidian" if what == "obsidian" else "ernestos"
    data = portable.export_zip(s, ws, notebook_id=notebook_id, fmt=fmt, include_trash=fmt == "ernestos")
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="ernestos-notes-{fmt}.zip"',
                             "Cache-Control": "no-store"})


class DropIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trash_docs: bool = False


async def read_body(request: Request, limit: int) -> bytes:
    """Counted while streaming, like the audio upload: a false
    Content-Length cannot make the server hold more than `limit`."""
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > limit:
            raise HTTPException(status_code=413, detail="payload_too_large")
        data.extend(chunk)
    if not data:
        raise HTTPException(status_code=422, detail="empty_file")
    return bytes(data)


def _etag(note) -> dict:
    return {"ETag": f'"r{note.revision}"'}


def _if_match(value: str | None) -> int | None:
    m = re.fullmatch(r'(?:W/)?"r(\d{1,9})"', (value or "").strip())
    return int(m.group(1)) if m else None


_CALLBACK_TEXT = {
    "uz": ("Google Drive ulandi", "ErnestOS’ga qayting va akkauntni tasdiqlang.",
           "Ulanmadi", "ErnestOS’ga qaytib, qaytadan urinib ko‘ring."),
    "en": ("Google Drive connected", "Return to ErnestOS and confirm the account.",
           "Not connected", "Return to ErnestOS and try again."),
    "ru": ("Google Drive подключён", "Вернитесь в ErnestOS и подтвердите аккаунт.",
           "Не подключено", "Вернитесь в ErnestOS и попробуйте снова."),
}


def _callback_page(lang: str, ok: bool, reason: str = "") -> HTMLResponse:
    good, good_body, bad, bad_body = _CALLBACK_TEXT.get(lang, _CALLBACK_TEXT["uz"])
    head, text = (good, good_body) if ok else (bad, bad_body)
    extra = "" if ok else f"<p style='color:#8A8F99;font-size:13px'>{escape(reason)}</p>"
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'><title>ErnestOS</title></head>"
        "<body style=\"font-family:-apple-system,Roboto,Arial,sans-serif;text-align:center;"
        "padding:64px 24px;color:#15171C\">"
        f"<div style='font-size:44px'>{'✅' if ok else '⚠️'}</div><h1 style='font-size:22px'>{head}</h1>"
        f"<p style='color:#4F5561'>{text}</p>{extra}</body></html>",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def install(app, auth):
    router = APIRouter(prefix="/api/notes")

    @app.exception_handler(notes.Invalid)
    async def invalid(request, exc):
        return JSONResponse(status_code=422, content={"detail": exc.code})

    @app.exception_handler(notes.Conflict)
    async def conflict(request, exc):
        return JSONResponse(status_code=409, content={"detail": "conflict", "note": exc.note},
                            headers={"ETag": f'"r{exc.note["revision"]}"'})

    @app.exception_handler(portable.ImportError_)
    async def bad_import(request, exc):
        return JSONResponse(status_code=422, content={"detail": exc.code})

    @app.exception_handler(conn.ConnectorError)
    async def connector_error(request, exc):
        if exc.code == "not_configured":
            return JSONResponse(status_code=503, content={"detail": "connector_not_configured",
                                                          "missing": conn.missing_config()})
        return JSONResponse(status_code=409, content={"detail": exc.code})

    # ---- notes ---------------------------------------------------------------

    @router.get("/home")
    def home(init=INIT):
        user, ws = auth(init)
        with SessionLocal() as s:
            return notes.home(s, ws, user.language or "uz")

    @router.get("")
    def listing(view: str = "all", notebook_id: int | None = None, tag: str = Query("", max_length=40),
                kind: str = Query("", max_length=12), q: str = Query("", max_length=200),
                cursor: str = Query("", max_length=200), limit: int = Query(30, ge=1, le=100), init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            return notes.list_notes(s, ws, view=view, notebook_id=notebook_id, tag=tag, kind=kind,
                                    q=q, cursor=cursor, limit=limit)

    @router.post("", status_code=201)
    def create(body: NoteIn, init=INIT):
        user, ws = auth(init)
        with SessionLocal() as s:
            note = notes.create(s, ws, title=body.title, body=body.body, kind=body.kind,
                                template=body.template, notebook_ids=body.notebook_ids, tags=body.tags,
                                status=body.status, source_url=body.source_url, lang=user.language or "uz")
            if body.pinned:
                notes.update(s, ws, note.id, {"pinned": True}, None)
            return JSONResponse(status_code=201, content=notes.detail(s, ws, note), headers=_etag(note))

    @router.post("/daily")
    def daily(init=INIT):
        user, ws = auth(init)
        with SessionLocal() as s:
            note, made = notes.daily(s, ws, user.language or "uz")
            return JSONResponse(status_code=201 if made else 200,
                                content={**notes.detail(s, ws, note), "created": made}, headers=_etag(note))

    @router.get("/templates")
    def templates(init=INIT):
        user, _ = auth(init)
        import notes_templates
        today = svc.today_local(svc.user_tz(user))
        return {"templates": notes_templates.listing(user.language or "uz", today)}

    @router.get("/tags")
    def tags(init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            return {"tags": notes.tags(s, ws)}

    @router.get("/link-targets")
    def link_targets(q: str = Query("", max_length=100), init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            return notes.link_targets(s, ws, q)

    @router.get("/for/{kind}/{target_id}")
    def notes_for(kind: str, target_id: int, init=INIT):
        _, ws = auth(init)
        if kind not in notes.RELATION_KINDS:
            raise HTTPException(status_code=404, detail="not_found")
        with SessionLocal() as s:
            return {"notes": notes.notes_for(s, ws, kind, target_id)}

    @router.post("/trash/empty")
    def empty_trash(init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            return {"deleted": notes.empty_trash(s, ws)}

    @router.get("/storage")
    def storage(init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            return notes.storage(s, ws)

    # ---- AI memory ---------------------------------------------------------

    @router.get("/ai/context")
    def ai_context(q: str = Query("", max_length=500), limit: int = Query(6, ge=1, le=20), init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            return notes.ai_context(s, ws, q, limit=limit)

    @router.get("/ai/export")
    def ai_export(init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            return _file(s, ws, "ai", None)

    # ---- notebooks ---------------------------------------------------------

    @router.get("/notebooks")
    def books(archived: bool = False, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            return {"notebooks": notes.notebooks(s, ws, archived=archived)}

    @router.post("/notebooks", status_code=201)
    def book_create(body: BookIn, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            book = notes.create_notebook(s, ws, body.model_dump(exclude_none=True))
            return {"id": book.id, "name": book.name, "color": book.color, "icon": book.icon}

    @router.patch("/notebooks/{book_id}")
    def book_update(book_id: int, body: BookIn, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            book = notes.update_notebook(s, ws, book_id, body.model_dump(exclude_none=True))
            return {"id": book.id, "name": book.name, "color": book.color, "icon": book.icon}

    @router.delete("/notebooks/{book_id}")
    def book_delete(book_id: int, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            notes.delete_notebook(s, ws, book_id)
        return {"ok": True}

    # ---- export / import -------------------------------------------------------

    @router.get("/export.zip")
    def export_zip(notebook_id: int | None = None, format: str = Query("ernestos", pattern="^(ernestos|obsidian)$"),
                   init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            return _file(s, ws, "obsidian" if format == "obsidian" else "zip", notebook_id)

    @router.post("/download-link")
    def download_link(body: DownloadIn, init=INIT):
        _, ws = auth(init)
        if body.notebook_id:
            with SessionLocal() as s:
                book = s.get(NoteNotebook, body.notebook_id)
                if book is None or book.workspace_id != ws:
                    raise HTTPException(status_code=404, detail="not_found")
        token = download_token(ws, body.what, body.notebook_id)
        name = {"zip": "ernestos-notes.zip", "obsidian": "ernestos-obsidian.zip", "ai": "ernestos-ai-memory.md"}[body.what]
        return {"url": f"/api/notes/download/{token}", "filename": name, "expires_in": DL_TTL}

    @router.get("/download/{token}")
    def download(token: str):
        ws, what, book = read_download_token(token)
        with SessionLocal() as s:
            return _file(s, ws, what, book)

    @router.post("/import/preview")
    async def import_preview(request: Request, source: str = Query("auto", pattern="^(auto|obsidian|notion|ernestos)$"),
                             init=INIT):
        _, ws = auth(init)
        data = await read_body(request, portable.UPLOAD_MAX)
        with SessionLocal() as s:
            return portable.preview(s, ws, data, source)

    @router.post("/import/{token}/commit")
    def import_commit(token: str, body: ImportCommitIn, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            return portable.commit(s, ws, token, exclude_paths=body.exclude)

    # ---- attachments -----------------------------------------------------------

    @router.get("/attachments/{uid}")
    def attachment_get(uid: str, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            row = notes.attachment(s, ws, uid)
            data = s.scalar(select(NoteAttachment.data).where(NoteAttachment.id == row.id))
            inline = row.mime.startswith("image/")
            return Response(data, media_type=row.mime, headers={
                "Content-Disposition": ("inline" if inline else "attachment")
                + f'; filename="{notes.safe_filename(row.filename).encode("ascii", "replace").decode()}"',
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "Cache-Control": "private, max-age=3600",
            })

    @router.delete("/attachments/{uid}")
    def attachment_delete(uid: str, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            notes.delete_attachment(s, ws, uid)
        return {"ok": True}

    # ---- the Google Drive / NotebookLM connector -----------------------------

    @router.get("/connector")
    def connector(init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            return conn.status(s, ws)

    @router.post("/connector/google/start")
    def connector_start(init=INIT):
        """The URL to open in the phone's real browser. No initData and no
        secret in it: a random state whose hash the server keeps."""
        _, ws = auth(init)
        with SessionLocal() as s:
            return {"url": conn.start(s, ws)}

    @router.get("/connector/google/callback")
    def connector_callback(state: str = "", code: str = "", error: str = ""):
        ok, reason, ws = conn.callback(state, code, error)
        return _callback_page(conn.owner_language(ws), ok, "" if ok else reason)

    @router.post("/connector/confirm")
    def connector_confirm(init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            conn.confirm(s, ws)
            return conn.status(s, ws)

    @router.post("/connector/disconnect")
    def connector_disconnect(body: DropIn, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            conn.disconnect(s, ws, trash_docs=body.trash_docs)
            return conn.status(s, ws)

    @router.post("/connector/sets", status_code=201)
    def set_create(body: SetIn, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            row = conn.create_set(s, ws, name=body.name, notebook_id=body.notebook_id, note_ids=body.note_ids)
            return {"id": row.id, **conn.status(s, ws)}

    @router.patch("/connector/sets/{set_id}")
    def set_update(set_id: int, body: SetPatch, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            conn.update_set(s, ws, set_id, body.model_dump(exclude_none=True))
            return conn.status(s, ws)

    @router.post("/connector/sets/{set_id}/sync")
    def set_sync(set_id: int, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            conn.sync(s, ws, set_id)
            return conn.status(s, ws)

    @router.post("/connector/sets/{set_id}/verify")
    def set_verify(set_id: int, body: VerifyIn, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            conn.verify(s, ws, set_id, body.answer)
            return conn.status(s, ws)

    @router.post("/connector/sets/{set_id}/delete")
    def set_delete(set_id: int, body: DropIn, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            outcome = conn.delete_set(s, ws, set_id, trash_docs=body.trash_docs)
            return {"outcome": outcome, **conn.status(s, ws)}

    # ---- one note ------------------------------------------------------------

    @router.get("/{note_id}")
    def read(note_id: int, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            note = notes.owned(s, ws, note_id)
            return JSONResponse(content=notes.detail(s, ws, note), headers=_etag(note))

    @router.patch("/{note_id}")
    def patch(note_id: int, body: NotePatch, if_match: str | None = Header(default=None), init=INIT):
        _, ws = auth(init)
        fields = body.model_dump(exclude_unset=True)
        base = fields.pop("base_revision", None)
        if base is None:
            base = _if_match(if_match)
        with SessionLocal() as s:
            note = notes.update(s, ws, note_id, fields, base)
            return JSONResponse(content=notes.detail(s, ws, note), headers=_etag(note))

    @router.post("/{note_id}/trash")
    def to_trash(note_id: int, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            note = notes.trash(s, ws, note_id)
            return notes.detail(s, ws, note)

    @router.post("/{note_id}/restore")
    def from_trash(note_id: int, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            note = notes.restore(s, ws, note_id)
            return notes.detail(s, ws, note)

    @router.delete("/{note_id}")
    def delete(note_id: int, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            notes.delete_forever(s, ws, note_id)
        return {"ok": True}

    @router.get("/{note_id}/revisions")
    def revs(note_id: int, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            return {"revisions": notes.revisions(s, ws, note_id)}

    @router.get("/{note_id}/revisions/{rev_id}")
    def rev(note_id: int, rev_id: int, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            r = notes.revision(s, ws, note_id, rev_id)
            return {"id": r.id, "revision": r.revision, "title": r.title, "body": r.body,
                    "reason": r.reason, "created_at": r.created_at.isoformat() + "Z"}

    @router.post("/{note_id}/revisions/{rev_id}/restore")
    def rev_restore(note_id: int, rev_id: int, body: RevisionIn, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            note = notes.restore_revision(s, ws, note_id, rev_id, body.base_revision)
            return JSONResponse(content=notes.detail(s, ws, note), headers=_etag(note))

    @router.post("/{note_id}/relations", status_code=201)
    def relate(note_id: int, body: RelationIn, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            notes.relate(s, ws, note_id, body.kind, body.target_id)
            return {"relations": notes.relations(s, ws, note_id)}

    @router.delete("/{note_id}/relations/{rel_id}")
    def unrelate(note_id: int, rel_id: int, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            notes.unrelate(s, ws, note_id, rel_id)
            return {"relations": notes.relations(s, ws, note_id)}

    @router.post("/{note_id}/task-preview")
    def task_preview(note_id: int, body: PreviewIn, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            out = notes.task_preview(s, ws, note_id, body.text)
            out["projects"] = [{"id": p["id"], "name": p["name"]} for p in svc.list_projects(s, ws)][:50]
            return out

    @router.post("/{note_id}/to-task", status_code=201)
    def to_task(note_id: int, body: ToTaskIn, init=INIT):
        _, ws = auth(init)
        with SessionLocal() as s:
            try:
                task, made = notes.to_task(s, ws, note_id, title=body.title, description=body.description,
                                           deadline=body.deadline, project_id=body.project_id,
                                           request_key=body.request_key)
            except ValueError as e:
                if isinstance(e, notes.Invalid):
                    raise
                raise HTTPException(status_code=422, detail="invalid_task") from None
            return JSONResponse(status_code=201 if made else 200, content={
                "task": {"id": task.id, "title": task.title, "deadline": task.deadline.isoformat() if task.deadline else None,
                         "project_id": task.project_id},
                "created": made, "relations": notes.relations(s, ws, note_id)})

    @router.post("/{note_id}/attachments", status_code=201)
    async def attachment_add(note_id: int, request: Request, x_file_name: str | None = Header(default=None),
                             init=INIT):
        _, ws = auth(init)
        data = await read_body(request, notes.ATTACH_FILE_MAX)
        with SessionLocal() as s:
            row = notes.add_attachment(s, ws, note_id, unquote(x_file_name or "file"),
                                       request.headers.get("content-type", ""), data)
            return notes.attachment_view(row)

    app.include_router(router)
