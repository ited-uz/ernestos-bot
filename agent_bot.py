"""Telegram adapter: private-chat voice or text -> proposal -> Confirm / Edit / Cancel."""
import hashlib
import re
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, TelegramError

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
        if prefs["consent"]:
            # Already set up: one line, then they just speak.
            await update.effective_message.reply_text(tr(user.language, "consented"))
            return
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(tr(user.language, "consent"), callback_data="ag:consent")]])
        await update.effective_message.reply_text(tr(user.language, "welcome"), reply_markup=markup)

    @staticmethod
    def render(draft, lang):
        """Short: only what will happen, then the buttons. No transcript, no filler."""
        status = draft["status"]
        if draft["error"]:
            body = escape(tr(lang, draft["error"]))
        elif status == "executed":
            body = f'{draft["preview"]}\n\n<b>{escape(tr(lang, "executed"))}</b>'
        elif status == "cancelled":
            body = (f'<s>{draft["preview"]}</s>\n\n' if draft["preview"] else "") + escape(tr(lang, "cancelled"))
        else:
            body = draft["preview"] or escape(tr(lang, "processing"))
        key = f'{draft["id"]}:{draft["revision"]}'
        buttons = []
        if status == "ready":
            buttons.append([InlineKeyboardButton(tr(lang, "confirm"), callback_data=f"ag:c:{key}")])
        if status in core.EDITABLE:
            buttons.append([InlineKeyboardButton(tr(lang, "edit"), callback_data=f"ag:e:{key}"),
                            InlineKeyboardButton(tr(lang, "cancel"), callback_data=f"ag:x:{key}")])
        return body, buttons

    async def show(self, message, draft, lang):
        body, buttons = self.render(draft, lang)
        markup = InlineKeyboardMarkup(buttons) if buttons else None
        if len(body) <= 4000:
            try:
                return await message.reply_text(body, parse_mode=ParseMode.HTML, reply_markup=markup)
            except BadRequest:
                pass  # a draft saved before cards were HTML: send it as it is
        # Telegram counts UTF-16 code units. A 1700-codepoint chunk is safe
        # even for emoji. Buttons follow ALL chunks, not a truncation.
        plain = re.sub(r"<[^>]+>", "", body)
        chunks = [plain[i:i + 1700] for i in range(0, len(plain), 1700)]
        for i, chunk in enumerate(chunks):
            await message.reply_text(chunk, reply_markup=markup if i == len(chunks) - 1 else None)

    async def replace(self, query, draft, lang):
        """After a button: the same message changes, so old buttons never linger."""
        body, buttons = self.render(draft, lang)
        markup = InlineKeyboardMarkup(buttons) if buttons else None
        if len(body) > 4000:
            await query.edit_message_reply_markup(reply_markup=None)
            return await self.show(query.message, draft, lang)
        try:
            await query.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=markup)
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return  # a repeated tap
            try:
                await query.edit_message_text(re.sub(r"<[^>]+>", "", body), reply_markup=markup)
            except TelegramError:
                pass
        except TelegramError:
            pass

    @staticmethod
    async def typing(update):
        """Telegram's "typing…" indicator instead of an extra message."""
        try:
            await update.effective_chat.send_action(ChatAction.TYPING)
        except (TelegramError, AttributeError):
            pass

    async def text(self, update, ctx):
        identity = await self.identity(update, ctx)
        if not identity:
            return
        user, ws = identity
        message = update.effective_message
        if not core.preferences(ws)["consent"]:
            # Kept, not lost: it runs right after the consent tap.
            ctx.user_data["agent_pending"] = {"kind": "text", "text": message.text,
                                             "key": f"tg:{update.effective_chat.id}:{message.message_id}"}
            return await self.help(update, ctx)
        await self.run_text(update, ctx, user, ws, message, message.text,
                            f"tg:{update.effective_chat.id}:{message.message_id}")

    async def run_text(self, update, ctx, user, ws, message, text, key):
        lang = user.language
        try:
            correction = core.editing(ws)
            await self.typing(update)
            draft = await core.ingest(user.telegram_id, ws, key, text=text,
                                      draft_id=correction[0] if correction else None,
                                      revision=correction[1] if correction else None)
            if correction:
                core.editing(ws, clear=True)
            await self.show(message, draft, lang)
        except (AgentError, PermissionError, ValueError, NotFound) as e:
            code = e.code if isinstance(e, AgentError) else ("not_found" if isinstance(e, NotFound) else "forbidden")
            await message.reply_text(tr(lang, code))

    async def voice(self, update, ctx):
        identity = await self.identity(update, ctx)
        if not identity:
            return
        user, ws = identity
        message = update.effective_message
        media = message.voice or message.audio
        if not media:
            return
        seconds = media.duration.total_seconds() if hasattr(media.duration, "total_seconds") else media.duration
        audio = {"kind": "voice", "file_id": media.file_id, "unique_id": media.file_unique_id,
                 "mime": media.mime_type or "audio/ogg", "seconds": seconds or 0, "size": media.file_size or 0,
                 "key": f"tg:{update.effective_chat.id}:{message.message_id}"}
        if not config.AGENT_ENABLED or not core.preferences(ws)["consent"]:
            if config.AGENT_ENABLED:
                ctx.user_data["agent_pending"] = audio
            return await self.help(update, ctx)
        await self.run_voice(update, ctx, user, ws, message, audio)

    async def run_voice(self, update, ctx, user, ws, message, audio):
        lang, draft = user.language, None
        try:
            if audio["seconds"] > config.AGENT_AUDIO_SECONDS:
                raise AgentError("audio_too_long", 422)
            if not audio["size"] or audio["size"] > config.AGENT_AUDIO_BYTES:
                raise AgentError("invalid_audio", 422)
            correction = core.editing(ws)
            draft, is_new = core.capture(user.telegram_id, ws, audio["key"], source="voice",
                draft_id=correction[0] if correction else None, revision=correction[1] if correction else None,
                digest=hashlib.sha256(audio["unique_id"].encode()).hexdigest())
            if is_new:
                ctx.user_data.pop("flow", None)
                await self.typing(update)
                remote = await ctx.bot.get_file(audio["file_id"])
                content = await remote.download_as_bytearray()
                if len(content) > config.AGENT_AUDIO_BYTES:
                    raise AgentError("invalid_audio", 422)
                draft = await core.process(user.telegram_id, ws, draft["id"], draft["revision"],
                                            audio=bytes(content), mime=audio["mime"])
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
        query = update.callback_query
        parts = query.data.split(":")
        identity = await self.identity(update, ctx, write=parts[1] == "c")
        if not identity:
            return
        user, ws = identity
        lang, message = user.language, update.effective_message
        try:
            what = parts[1]
            if what == "consent":
                core.consent(ws, True)
                pending = ctx.user_data.pop("agent_pending", None)
                try:
                    await query.edit_message_text(tr(lang, "consented"))
                except TelegramError:
                    pass
                # The message that asked for consent runs now; nobody re-sends it.
                if pending and pending["kind"] == "text":
                    await self.run_text(update, ctx, user, ws, message, pending["text"], pending["key"])
                elif pending:
                    await self.run_voice(update, ctx, user, ws, message, pending)
                return
            draft_id, revision = parts[2], int(parts[3])
            if what == "c":
                await self.replace(query, core.confirm(user.telegram_id, ws, draft_id, revision), lang)
            elif what == "x":
                core.editing(ws, clear=True)
                await self.replace(query, core.cancel(ws, draft_id, revision), lang)
            elif what == "e":
                core.editing(ws, draft_id, revision)
                ctx.user_data.pop("flow", None)
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except TelegramError:
                    pass
                await message.reply_text(tr(lang, "edit_prompt"))
        except (AgentError, PermissionError, ValueError, IndexError, NotFound) as e:
            code = e.code if isinstance(e, AgentError) else ("not_found" if isinstance(e, NotFound) else ("timer_required" if str(e) == "timer_required" else "forbidden"))
            await message.reply_text(tr(lang, code))
