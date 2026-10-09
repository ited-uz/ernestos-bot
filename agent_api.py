"""Mini App door to the same agent the bot uses: speak or type → card → Confirm.

Request bodies never carry an actor id: identity is the Telegram signature.
Nothing is executed until /confirm, exactly as in the bot.
"""
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

import agent_chat
import agent_core as core
import config
from agent_actions import AgentError
from agent_text import tr

KEY = r"^[A-Za-z0-9_:-]{8,80}$"


class TextIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=6000)
    request_key: str = Field(pattern=KEY)
    #: An answer or correction to an open proposal, as in the bot.
    draft_id: str | None = Field(default=None, max_length=64)
    revision: int | None = Field(default=None, ge=1)


class FieldEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    index: int = Field(ge=0, le=5)
    field: str = Field(max_length=16)
    value: str | None = Field(default=None, max_length=300)


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)
    request_key: str = Field(pattern=KEY)


class JournalText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=6000)


async def read_audio(request):
    """Counted while streaming: a missing or false Content-Length cannot make
    the server buffer more than the limit."""
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > config.AGENT_AUDIO_BYTES:
            raise AgentError("invalid_audio", 413)
        data.extend(chunk)
    if not data:
        raise AgentError("invalid_audio", 422)
    return bytes(data)


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
        draft = await core.ingest(user.telegram_id, ws, body.request_key, text=body.text,
                                  draft_id=body.draft_id, revision=body.revision)
        return card(draft, user.language)

    @router.post("/drafts/{draft_id}/edit")
    def edit(draft_id: str, body: FieldEdit, x_telegram_init_data: str | None = Header(None)):
        """Fix one field (the hour, the date, the name) without asking the model again."""
        user, ws = auth(x_telegram_init_data)
        return card(core.edit_field(user.telegram_id, ws, draft_id, body.revision, body.index,
                                    body.field, body.value), user.language)

    @router.post("/audio")
    async def audio(request: Request, x_telegram_init_data: str | None = Header(None),
                    x_agent_request_key: str | None = Header(None)):
        user, ws = auth(x_telegram_init_data)
        prefs = core.preferences(ws)
        if not prefs["enabled"]:
            raise AgentError("agent_disabled", 503)
        if not prefs["consent"]:
            raise AgentError("consent_required", 403)
        data = await read_audio(request)
        draft = await core.ingest(user.telegram_id, ws, x_agent_request_key or "", audio=data,
                                  mime=request.headers.get("content-type"))
        return card(draft, user.language)

    @router.get("/now")
    async def now(x_telegram_init_data: str | None = Header(None)):
        """The AI pick for the Hozir card (Pro and Max; the person can switch it off)."""
        import now_ai
        user, ws = auth(x_telegram_init_data)
        return await now_ai.pick(user.telegram_id, ws)

    @router.post("/journal/text")
    async def journal_text(body: JournalText, x_telegram_init_data: str | None = Header(None)):
        user, ws = auth(x_telegram_init_data)
        return await core.journal_fill(user.telegram_id, ws, text=body.text)

    @router.post("/journal/audio")
    async def journal_audio(request: Request, x_telegram_init_data: str | None = Header(None)):
        user, ws = auth(x_telegram_init_data)
        prefs = core.preferences(ws)
        if not prefs["enabled"]:
            raise AgentError("agent_disabled", 503)
        if not prefs["consent"]:
            raise AgentError("consent_required", 403)
        data = await read_audio(request)
        return await core.journal_fill(user.telegram_id, ws, audio=data,
                                       mime=request.headers.get("content-type"))

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

    @router.get("/chat")
    def chat_history(x_telegram_init_data: str | None = Header(None)):
        """The conversation so far, and whether the assistant can answer."""
        _, ws = auth(x_telegram_init_data)
        return {"messages": agent_chat.history(ws), "agent": core.preferences(ws)}

    @router.post("/chat")
    async def chat(body: ChatIn, x_telegram_init_data: str | None = Header(None)):
        """A question or a request. A requested change comes back as a draft
        card; nothing is executed until it is confirmed."""
        user, ws = auth(x_telegram_init_data)
        result = await agent_chat.ask(user.telegram_id, ws, body.request_key, body.text)
        if result.get("draft"):
            result["draft"] = card(result["draft"], user.language)
        return result

    @router.delete("/chat")
    def chat_clear(x_telegram_init_data: str | None = Header(None)):
        _, ws = auth(x_telegram_init_data)
        agent_chat.clear(ws)
        return {"ok": True}

    app.include_router(router)
