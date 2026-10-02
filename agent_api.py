"""Mini App door to the same agent the bot uses: speak or type → card → Confirm.

Request bodies never carry an actor id: identity is the Telegram signature.
Nothing is executed until /confirm, exactly as in the bot.
"""
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

import agent_core as core
import config
from agent_actions import AgentError
from agent_text import tr

KEY = r"^[A-Za-z0-9_:-]{8,80}$"


class TextIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=6000)
    request_key: str = Field(pattern=KEY)


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


def card(draft, lang):
    """The draft plus what the app needs to draw it: the HTML card, a message."""
    message = tr(lang, draft["error"]) if draft["error"] else None
    return {**draft, "message": message}


def install(app, auth):
    router = APIRouter(prefix="/api/agent")

    @app.exception_handler(AgentError)
    async def agent_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": exc.code})

    @router.post("/consent")
    def consent(x_telegram_init_data: str | None = Header(None)):
        _, ws = auth(x_telegram_init_data)
        return core.consent(ws, True)

    @router.post("/text")
    async def text(body: TextIn, x_telegram_init_data: str | None = Header(None)):
        user, ws = auth(x_telegram_init_data)
        draft = await core.ingest(user.telegram_id, ws, body.request_key, text=body.text)
        return card(draft, user.language)

    @router.post("/audio")
    async def audio(request: Request, x_telegram_init_data: str | None = Header(None),
                    x_agent_request_key: str | None = Header(None)):
        user, ws = auth(x_telegram_init_data)
        prefs = core.preferences(ws)
        if not prefs["enabled"]:
            raise AgentError("agent_disabled", 503)
        if not prefs["consent"]:
            raise AgentError("consent_required", 403)
        # Counted while streaming: a missing or false Content-Length cannot
        # make the server buffer more than the limit.
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > config.AGENT_AUDIO_BYTES:
                raise AgentError("invalid_audio", 413)
            data.extend(chunk)
        if not data:
            raise AgentError("invalid_audio", 422)
        draft = await core.ingest(user.telegram_id, ws, x_agent_request_key or "", audio=bytes(data),
                                  mime=request.headers.get("content-type"))
        return card(draft, user.language)

    @router.post("/drafts/{draft_id}/confirm")
    def confirm(draft_id: str, body: Revision, x_telegram_init_data: str | None = Header(None)):
        user, ws = auth(x_telegram_init_data)
        try:
            return card(core.confirm(user.telegram_id, ws, draft_id, body.revision), user.language)
        except ValueError as e:
            raise AgentError("timer_required" if str(e) == "timer_required" else "invalid_action", 422) from None

    @router.post("/drafts/{draft_id}/cancel")
    def cancel(draft_id: str, body: Revision, x_telegram_init_data: str | None = Header(None)):
        user, ws = auth(x_telegram_init_data)
        return card(core.cancel(ws, draft_id, body.revision), user.language)

    app.include_router(router)
