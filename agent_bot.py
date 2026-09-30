"""Telegram adapter: private-chat voice, durable corrections, explicit buttons."""
import hashlib
import uuid

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
        buttons = [[InlineKeyboardButton(tr(user.language, "inbox"), callback_data="ag:in:0")]]
        if not prefs["consent"]:
            buttons.insert(0, [InlineKeyboardButton(tr(user.language, "consent"), callback_data="ag:consent")])
        await update.effective_message.reply_text(tr(user.language, "welcome"), reply_markup=InlineKeyboardMarkup(buttons))

    async def inbox(self, update, ctx, offset=0):
        identity = await self.identity(update, ctx, write=False)
        if not identity:
            return
        user, ws = identity
        core.editing(ws, clear=True)
        drafts = core.list_drafts(ws, offset)
        buttons = []
        for draft in drafts[:8]:
            icon = {"executed": "✅", "cancelled": "❌", "processing": "⏳"}.get(draft["status"], "📥")
            title = draft["transcript"][:42] or "🎙 Audio"
            buttons.append([InlineKeyboardButton(f'{icon} {title}', callback_data=f'ag:v:{draft["id"]}')])
        if len(drafts) > 8:
            buttons.append([InlineKeyboardButton(tr(user.language, "more"), callback_data=f'ag:in:{offset + 8}')])
        await update.effective_message.reply_text(tr(user.language, "inbox" if drafts else "empty"),
                                                   reply_markup=InlineKeyboardMarkup(buttons))

    async def show(self, message, draft, lang):
        header = tr(lang, draft["status"] if draft["status"] in {"ready", "executed", "cancelled", "processing"} else "inbox_status")
        body = f'{header}\n\n{tr(lang, "heard")}:\n{draft["transcript"] or "🎙 Audio"}'
        if draft.get("language"):
            body += f'\n{tr(lang, "input_language")}: {tr(lang, draft["language"])}'
        if draft["preview"]:
            body += "\n\n" + draft["preview"]
        if draft["error"]:
            body += "\n\n" + tr(lang, draft["error"])
        key = f'{draft["id"]}:{draft["revision"]}'
        buttons = []
        if draft["status"] == "ready":
            buttons.append([InlineKeyboardButton(tr(lang, "confirm"), callback_data=f"ag:c:{key}")])
        if draft["status"] not in {"executed", "cancelled", "processing"}:
            buttons.append([InlineKeyboardButton(tr(lang, "edit"), callback_data=f"ag:e:{key}"),
                            InlineKeyboardButton(tr(lang, "cancel"), callback_data=f"ag:x:{key}")])
            if draft["transcript"]:
                buttons.append([InlineKeyboardButton(tr(lang, "retry"), callback_data=f"ag:r:{key}")])
            buttons.append([InlineKeyboardButton(tr(lang, "keep"), callback_data=f"ag:k:{key}")])
        buttons.append([InlineKeyboardButton(tr(lang, "inbox"), callback_data="ag:in:0")])
        # Telegram counts UTF-16 code units. A 1700-codepoint chunk is safe
        # even for emoji. Confirmation follows ALL preview chunks, not a truncation.
        chunks = [body[i:i + 1700] for i in range(0, len(body), 1700)]
        for i, chunk in enumerate(chunks):
            await message.reply_text(chunk, reply_markup=InlineKeyboardMarkup(buttons) if i == len(chunks) - 1 else None)

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
        identity = await self.identity(update, ctx, write=parts[1] in {"c", "r"})
        if not identity:
            return
        user, ws = identity
        lang, message = user.language, update.effective_message
        try:
            what = parts[1]
            if what == "consent":
                core.consent(ws, True)
                await message.reply_text(tr(lang, "consented"))
            elif what == "in":
                await self.inbox(update, ctx, max(0, min(int(parts[2]), 100000)))
            elif what == "v":
                await self.show(message, core.get_draft(ws, parts[2]), lang)
            else:
                draft_id, revision = parts[2], int(parts[3])
                if what == "c":
                    draft = core.confirm(user.telegram_id, ws, draft_id, revision)
                    await self.show(message, draft, lang)
                elif what == "x":
                    await self.show(message, core.cancel(ws, draft_id, revision), lang)
                    core.editing(ws, clear=True)
                elif what == "e":
                    core.editing(ws, draft_id, revision)
                    ctx.user_data.pop("flow", None)
                    await message.reply_text(tr(lang, "edit_prompt"))
                elif what == "k":
                    core.get_draft(ws, draft_id)  # ownership check even on a no-op
                    core.editing(ws, clear=True)
                    await message.reply_text(tr(lang, "kept"))
                elif what == "r":
                    old = core.get_draft(ws, draft_id)
                    if not old["transcript"]:
                        raise AgentError("empty_audio", 422)
                    draft = await core.ingest(user.telegram_id, ws, "retry:" + uuid.uuid4().hex,
                        text=old["transcript"], draft_id=draft_id, revision=revision)
                    await self.show(message, draft, lang)
        except (AgentError, PermissionError, ValueError, IndexError, NotFound) as e:
            code = e.code if isinstance(e, AgentError) else ("not_found" if isinstance(e, NotFound) else ("timer_required" if str(e) == "timer_required" else "forbidden"))
            await message.reply_text(tr(lang, code))
