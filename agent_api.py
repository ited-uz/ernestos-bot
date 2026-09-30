"""Authenticated Mini App adapter. Request bodies never contain actor IDs."""
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

import agent_core as core
import config
from agent_actions import AgentError
from agent_text import TEXT


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=6000)
    request_key: str = Field(pattern=r"^[A-Za-z0-9_:-]{8,80}$")
    draft_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def revision_pair(self):
        if (self.draft_id is None) != (self.revision is None):
            raise ValueError("draft_id and revision must be supplied together")
        return self


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


class Retry(Revision):
    request_key: str = Field(pattern=r"^[A-Za-z0-9_:-]{8,80}$")


class Consent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accepted: bool


def install(app, auth):
    router = APIRouter(prefix="/api/agent")

    @app.exception_handler(AgentError)
    async def agent_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": exc.code})

    @router.get("/meta")
    def meta(x_telegram_init_data: str | None = Header(None)):
        user, ws = auth(x_telegram_init_data)
        return {**core.preferences(ws), "strings": TEXT.get(user.language, TEXT["uz"])}

    @router.post("/consent")
    def consent(body: Consent, x_telegram_init_data: str | None = Header(None)):
        _, ws = auth(x_telegram_init_data)
        return core.consent(ws, body.accepted)

    @router.get("/inbox")
    def inbox(offset: int = 0, x_telegram_init_data: str | None = Header(None)):
        _, ws = auth(x_telegram_init_data)
        return {"drafts": core.list_drafts(ws, max(0, min(offset, 100000)))}

    @router.get("/drafts/{draft_id}")
    def detail(draft_id: str, x_telegram_init_data: str | None = Header(None)):
        _, ws = auth(x_telegram_init_data)
        return core.get_draft(ws, draft_id)

    @router.post("/text")
    async def text(body: Input, x_telegram_init_data: str | None = Header(None)):
        user, ws = auth(x_telegram_init_data)
        return await core.ingest(user.telegram_id, ws, body.request_key, text=body.text,
                                  draft_id=body.draft_id, revision=body.revision)

    @router.post("/audio")
    async def audio(request: Request, draft_id: str | None = None, revision: int | None = None,
                    x_telegram_init_data: str | None = Header(None), x_agent_request_key: str | None = Header(None)):
        user, ws = auth(x_telegram_init_data)
        if (draft_id is None) != (revision is None):
            raise AgentError("invalid_fields", 422)
        # Consent checked before buffering any voice bytes.
        prefs = core.preferences(ws)
        if not prefs["enabled"]:
            raise AgentError("agent_disabled", 503)
        if not prefs["consent"]:
            raise AgentError("consent_required", 403)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > config.AGENT_AUDIO_BYTES:
                raise AgentError("invalid_audio", 413)
            data.extend(chunk)
        if not data:
            raise AgentError("invalid_audio", 422)
        return await core.ingest(user.telegram_id, ws, x_agent_request_key or "", audio=bytes(data),
                                  mime=request.headers.get("content-type"), draft_id=draft_id, revision=revision)

    @router.post("/drafts/{draft_id}/confirm")
    def confirm(draft_id: str, body: Revision, x_telegram_init_data: str | None = Header(None)):
        user, ws = auth(x_telegram_init_data)
        try:
            return core.confirm(user.telegram_id, ws, draft_id, body.revision)
        except ValueError as e:
            raise AgentError("timer_required" if str(e) == "timer_required" else "invalid_action", 422) from None

    @router.post("/drafts/{draft_id}/cancel")
    def cancel(draft_id: str, body: Revision, x_telegram_init_data: str | None = Header(None)):
        _, ws = auth(x_telegram_init_data)
        return core.cancel(ws, draft_id, body.revision)

    @router.post("/drafts/{draft_id}/retry")
    async def retry(draft_id: str, body: Retry, x_telegram_init_data: str | None = Header(None)):
        user, ws = auth(x_telegram_init_data)
        old = core.get_draft(ws, draft_id)
        if not old["transcript"]:
            raise AgentError("empty_audio", 422)
        return await core.ingest(user.telegram_id, ws, body.request_key, text=old["transcript"],
                                  draft_id=draft_id, revision=body.revision)

    @router.delete("/history")
    def forget(body: Consent, x_telegram_init_data: str | None = Header(None)):
        _, ws = auth(x_telegram_init_data)
        if not body.accepted:
            raise AgentError("confirmation_required", 422)
        core.forget_history(ws)
        return {"ok": True}

    app.include_router(router)
