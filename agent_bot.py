"""Telegram adapter: private-chat voice or text -> proposal -> Confirm / Edit / Cancel."""
import hashlib

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

import agent_core as core
import config
import db
from services import NotFound
from agent_actions import AgentError
from agent_text import tr


class AgentBot:
    def __init__(self, guard):
        self.guard = guard

    async def identity(self, update, ctx, *, write=True):
        chat = update.effective_chat
        if not chat or chat.type != "private":
            if update.effective_message:
                await update.effective_message.reply_text(tr(self.language(update), "private"))
            return None
        return await self.guard(update, ctx, write=write)

    @staticmethod
    def language(update):
        """The user's saved app language; replies never follow the input language."""
        who = getattr(update, "effective_user", None)
        if who is None:
            return "uz"
        with db.SessionLocal() as s:
            user = s.get(db.User, who.id)
            return user.language if user and user.language in {"uz", "ru", "en"} else "uz"

    async def help(self, update, ctx):
        identity = await self.identity(update, ctx, write=False)
        if not identity:
            return
        user, ws = identity
        ctx.user_data.pop("flow", None)
        core.editing(ws, clear=True)
        prefs = core.preferences(ws)
        if not prefs["enabled"]:
            await update.effective_message.reply_text(tr(user.language, "disabled"))
            return
        markup = None
        if not prefs["consent"]:
            markup = InlineKeyboardMarkup([[InlineKeyboardButton(tr(user.language, "consent"), callback_data="ag:consent")]])
        await update.effective_message.reply_text(tr(user.language, "welcome"), reply_markup=markup)

    async def show(self, message, draft, lang):
        status = draft["status"]
        body = f'{tr(lang, "heard")}:\n{draft["transcript"] or "🎙"}'
        if draft["preview"]:
            body += "\n\n" + draft["preview"]
        if draft["error"]:
            body += "\n\n" + tr(lang, draft["error"])
        body += "\n\n" + tr(lang, status if status in {"ready", "executed", "cancelled"} else "draft")
        key = f'{draft["id"]}:{draft["revision"]}'
        buttons = []
        if status == "ready":
            buttons.append([InlineKeyboardButton(tr(lang, "confirm"), callback_data=f"ag:c:{key}")])
        if status in core.EDITABLE:
            buttons.append([InlineKeyboardButton(tr(lang, "edit"), callback_data=f"ag:e:{key}"),
                            InlineKeyboardButton(tr(lang, "cancel"), callback_data=f"ag:x:{key}")])
        # Telegram counts UTF-16 code units. A 1700-codepoint chunk is safe
        # even for emoji. Buttons follow ALL preview chunks, not a truncation.
        chunks = [body[i:i + 1700] for i in range(0, len(body), 1700)]
        for i, chunk in enumerate(chunks):
            markup = InlineKeyboardMarkup(buttons) if buttons and i == len(chunks) - 1 else None
            await message.reply_text(chunk, reply_markup=markup)

    async def text(self, update, ctx):
        identity = await self.identity(update, ctx)
        if not identity:
            return
        user, ws = identity
        lang = user.language
        if not core.preferences(ws)["consent"]:
            return await self.help(update, ctx)
        try:
            correction = core.editing(ws)
            message = update.effective_message
            key = f"tg:{update.effective_chat.id}:{message.message_id}"
            await message.reply_text(tr(lang, "processing"))
            draft = await core.ingest(user.telegram_id, ws, key, text=message.text,
                                      draft_id=correction[0] if correction else None,
                                      revision=correction[1] if correction else None)
            if correction:
                core.editing(ws, clear=True)
            await self.show(message, draft, lang)
        except (AgentError, PermissionError, ValueError, NotFound) as e:
            code = e.code if isinstance(e, AgentError) else ("not_found" if isinstance(e, NotFound) else "forbidden")
            await update.effective_message.reply_text(tr(lang, code))

    async def voice(self, update, ctx):
        identity = await self.identity(update, ctx)
        if not identity:
            return
        user, ws = identity
        lang, message = user.language, update.effective_message
        if not core.preferences(ws)["consent"] or not config.AGENT_ENABLED:
            return await self.help(update, ctx)
        media = message.voice or message.audio
        if not media:
            return
        draft = None
        try:
            seconds = media.duration.total_seconds() if hasattr(media.duration, "total_seconds") else media.duration
            if seconds > config.AGENT_AUDIO_SECONDS:
                raise AgentError("audio_too_long", 422)
            if not media.file_size or media.file_size > config.AGENT_AUDIO_BYTES:
                raise AgentError("invalid_audio", 422)
            correction = core.editing(ws)
            draft, is_new = core.capture(user.telegram_id, ws,
                f"tg:{update.effective_chat.id}:{message.message_id}", source="voice",
                draft_id=correction[0] if correction else None, revision=correction[1] if correction else None,
                digest=hashlib.sha256(media.file_unique_id.encode()).hexdigest())
            if is_new:
                ctx.user_data.pop("flow", None)
                await message.reply_text(tr(lang, "processing"))
                remote = await media.get_file()
                content = await remote.download_as_bytearray()
                if len(content) > config.AGENT_AUDIO_BYTES:
                    raise AgentError("invalid_audio", 422)
                draft = await core.process(user.telegram_id, ws, draft["id"], draft["revision"],
                                            audio=bytes(content), mime=media.mime_type or "audio/ogg")
                if correction:
                    core.editing(ws, clear=True)
            await self.show(message, draft, lang)
        except (AgentError, TelegramError) as e:
            code = e.code if isinstance(e, AgentError) else "processing_interrupted"
            if draft:
                draft = core._failure(ws, draft["id"], draft["revision"], code)
                await self.show(message, draft, lang)
            else:
                await message.reply_text(tr(lang, code))

    async def callback(self, update, ctx):
        parts = update.callback_query.data.split(":")
        identity = await self.identity(update, ctx, write=parts[1] == "c")
        if not identity:
            return
        user, ws = identity
        lang, message = user.language, update.effective_message
        try:
            what = parts[1]
            if what == "consent":
                core.consent(ws, True)
                await message.reply_text(tr(lang, "consented"))
                return
            draft_id, revision = parts[2], int(parts[3])
            if what == "c":
                await self.show(message, core.confirm(user.telegram_id, ws, draft_id, revision), lang)
            elif what == "x":
                core.editing(ws, clear=True)
                await self.show(message, core.cancel(ws, draft_id, revision), lang)
            elif what == "e":
                core.editing(ws, draft_id, revision)
                ctx.user_data.pop("flow", None)
                await message.reply_text(tr(lang, "edit_prompt"))
        except (AgentError, PermissionError, ValueError, IndexError, NotFound) as e:
            code = e.code if isinstance(e, AgentError) else ("not_found" if isinstance(e, NotFound) else ("timer_required" if str(e) == "timer_required" else "forbidden"))
            await message.reply_text(tr(lang, code))
