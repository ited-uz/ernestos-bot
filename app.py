"""
ErnestOS — Telegram bot + Mini App API in one process.

Both surfaces call services.py, so the bot and the Mini App can never drift
apart: creating a task from a Telegram button and creating one from the web UI
run the exact same function.

Run with:  uvicorn app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, time as dtime, timedelta
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text as sql_text
from sqlalchemy.exc import DBAPIError, OperationalError
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, InputFile, MenuButtonWebApp,
    ReplyKeyboardMarkup, Update, WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, ChatMemberHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)

import accounts
import config
import db
import dependencies as deps
import ratelimit
import scheduler as scheduling
import security
import services as svc
import translations
from db import SessionLocal, User

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("ernestos")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
#
# Every setting is resolved in `config`, and re-exported here under the name it
# has always had. The re-export is not ceremony: the suite patches
# `application.WEBAPP_URL` directly, and handlers read these as module globals.

config.check()

BOT_TOKEN = config.BOT_TOKEN
#: The bot's public @name, used to build every invite link. Configured by the
#: operator, and — because a missing one silently produces a screen with no
#: link on it and no explanation — filled in from Telegram itself at startup
#: when it was left unset. `get_me()` knows the answer; requiring somebody to
#: type it into an environment variable is a step that can only be got wrong.
BOT_USERNAME = config.BOT_USERNAME
ENVIRONMENT = config.ENVIRONMENT
WEBAPP_URL = config.WEBAPP_URL
WEBHOOK_URL = config.WEBHOOK_URL
WEBHOOK_SECRET = config.WEBHOOK_SECRET
ALLOWED_UPDATES = config.ALLOWED_UPDATES
INIT_DATA_MAX_AGE = config.INIT_DATA_MAX_AGE
MAX_BODY_BYTES = config.MAX_BODY_BYTES
ADMIN_LOG_CHANNEL_ID = config.ADMIN_LOG_CHANNEL_ID
FEEDBACK_CHANNEL_ID = config.FEEDBACK_CHANNEL_ID
STATS_CHANNEL_ID = config.STATS_CHANNEL_ID
STATS_POST_HOUR = config.STATS_POST_HOUR
REPORT_TICK_MINUTES = config.REPORT_TICK_MINUTES


# ---------------------------------------------------------------------------
# Translations
# ---------------------------------------------------------------------------

#: Every user-visible bot string lives in `translations`, never inline. Both
#: names are re-exported here because forty call sites and the test suite
#: reach for `T` and `t` on this module.
T = translations.T
t = translations.t


# ---------------------------------------------------------------------------
# Admin log channel
# ---------------------------------------------------------------------------

#: Escaping user text before it enters an HTML Telegram message. Defined in
#: `security` and re-exported here, because every renderer in this file
#: reaches for it by this name.
esc = security.esc


async def admin_log(bot, text: str, chat_id: str | None = None, *,
                    reraise: bool = False) -> None:
    """Send a business event to a private admin channel.

    Never carries secrets or stack traces — technical failures go to the
    application log instead.

    Failures are swallowed by default, because a per-event log line must never
    be able to break the user action that produced it. `reraise=True` is for
    the callers where silence is the bug rather than the safety: a statistics
    post that cannot reach its channel should be visible and retried, not
    quietly dropped. Either way the reason is logged at `exception` level — it
    was `warning`, which is how a channel the bot had never been made an admin
    of stayed a mystery.
    """
    target = chat_id or ADMIN_LOG_CHANNEL_ID
    if not target:
        return
    try:
        await bot.send_message(chat_id=target, text=text,
                               parse_mode=ParseMode.HTML,
                               disable_web_page_preview=True)
    except TelegramError:
        log.exception("could not post to channel %s — is the bot an "
                      "administrator there?", target)
        if reraise:
            raise


def _who(user: User) -> str:
    """Identity line for the admin channel.

    Deliberately carries no phone number: the channel is read by people who do
    not need it, and a leaked export would expose it (audit 016). Whether a
    number exists is enough for support.
    """
    name = " ".join(x for x in (user.first_name, user.last_name) if x) or "—"
    username = f"@{esc(user.username)}" if user.username else "—"
    return (f"No: <b>#{user.member_no}</b>\n"
            f"ID: <code>{user.telegram_id}</code>\n"
            f"Name: {esc(name)}\nUsername: {username}")


async def log_event(bot, user: User, event: str, detail: str = "") -> None:
    body = f"<b>{event}</b>\n{_who(user)}"
    if detail:
        body += f"\n{detail}"
    await admin_log(bot, body)


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------

MEMBER_STATES = {"member", "administrator", "creator"}

#: How long a confirmed membership answer is trusted before Telegram is asked
#: again. Short enough that leaving the channel locks the Mini App quickly
#: (audit 002), long enough that normal use does not call Telegram per request
#: (audit 004).
MEMBERSHIP_TTL = deps.MEMBERSHIP_TTL
FREE_ACTIONS = deps.FREE_ACTIONS

#: Re-exported so the rest of this module and the suite keep one vocabulary.
is_subscribed = deps.ask_telegram
record_membership = deps.record_membership
membership_is_fresh = deps.membership_is_fresh
trial_state = deps.trial_state


def subscribe_keyboard(lang: str) -> InlineKeyboardMarkup:
    rows = []
    if deps.REQUIRED_CHANNEL_URL:
        rows.append([InlineKeyboardButton(t(lang, "btn_join"), url=deps.REQUIRED_CHANNEL_URL)])
    rows.append([InlineKeyboardButton(t(lang, "btn_check"), callback_data="sub:check")])
    return InlineKeyboardMarkup(rows)


async def guard(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *,
                write: bool = True) -> tuple[User, int] | None:
    """Every protected action starts here.

    Returns (user, workspace_id) when the caller may proceed, otherwise sends
    the appropriate prompt and returns None. The access decision itself is
    `dependencies.check_subscription`, which the API calls too — the two
    surfaces must never disagree about who is allowed in.

    `write=False` is for screens that only show something. Once the free run
    is spent, somebody who has not joined the channel can still read their own
    day — their data never becomes a hostage — and only changing it asks for
    the channel first.
    """
    uid = account_of(update)
    if uid is None:
        return None

    with SessionLocal() as s:
        user, _ = svc.get_or_create_user(s, uid, **_profile(update, uid))
        svc.touch_activity(s, uid)
        s.commit()
        lang = user.language
        onboarded = user.onboarded
        ws = svc.workspace_id_for(s, uid)

    if not onboarded:
        await start(update, ctx)
        return None

    verdict = await deps.check_subscription(uid, ctx.bot)
    target = update.effective_message
    if verdict not in deps.ALLOWED and write:
        if target:
            # "missing" is the free run being spent, which is a different
            # message from having left a channel already joined.
            await target.reply_text(
                t(lang, "sub_unknown" if verdict == "unknown" else "trial_over"),
                parse_mode=ParseMode.HTML,
                reply_markup=subscribe_keyboard(lang))
        return None

    with SessionLocal() as s:
        user = s.get(User, uid)
        return user, ws


async def count_action(telegram_id: int, ctx: ContextTypes.DEFAULT_TYPE | None = None,
                       message=None, lang: str = "uz") -> None:
    """Record one thing done, and say so at the two moments it matters.

    Silence until the run is nearly spent, then one warning, then the ask. A
    counter shown after every tick would turn using the app into watching a
    meter run down, which is the opposite of the point.
    """
    with SessionLocal() as s:
        outcome = svc.record_action_and_progress(s, telegram_id)
        inviter = outcome["inviter_to_tell"]
        progress = outcome["progress"]
        user = s.get(User, telegram_id)
        trial = deps.trial_state(user)

    if inviter is not None and ctx is not None:
        await notify_referral_qualified(ctx.bot, inviter)

    # A level is the one progression event worth interrupting somebody for, and
    # only on the tick that crosses it. Streaks, XP and rank live on the screen
    # they belong to — a bot message for every one of them is how an app gets
    # muted, and the product is explicit that it must not spam.
    if progress.get("level_up") and ctx is not None:
        await notify_level_up(ctx.bot, telegram_id, progress["level"], lang)

    if message is None or not deps.REQUIRED_CHANNEL_ID:
        return
    if trial.remaining == 3 and trial.free:
        await message.reply_text(t(lang, "trial_soon", n=trial.remaining),
                                 parse_mode=ParseMode.HTML)
    elif trial.gated:
        await message.reply_text(t(lang, "trial_over"), parse_mode=ParseMode.HTML,
                                 reply_markup=subscribe_keyboard(lang))


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

#: How much of the product a new account is shown, by actions taken.
#:
#:   1 — Home, Habits, Tasks, Settings: the four things a first day needs.
#:   2 — + Statistics, once there is something to count (5 actions).
#:   3 — + Team and Feedback: the whole menu (15 actions, or a week in).
#:
#: Nothing is locked: every screen still opens by its command, and typing a
#: hidden menu label still routes. The menu only stops shouting about things
#: a newcomer has no use for yet.
STAGE_ACTIONS = (5, 15)
STAGE_FULL_AFTER = timedelta(days=7)


def stage_for(actions: int, created_at: datetime | None) -> int:
    if actions >= STAGE_ACTIONS[1]:
        return 3
    if created_at is not None and db.utcnow() - created_at >= STAGE_FULL_AFTER:
        return 3
    return 2 if actions >= STAGE_ACTIONS[0] else 1


def ui_stage(user: User | None) -> int:
    if user is None:
        return 3
    return stage_for(user.actions_count or 0, user.created_at)


def main_menu(lang: str, stage: int = 3, *,
              team: bool = False) -> ReplyKeyboardMarkup:
    """The persistent menu: seven buttons, the same for everybody.

        🏠 Home       💰 Pul
        ✅ Odatlar    ⚡ Vazifalar
        👥 Jamoa      📊 Statistika
        ⚙️ Sozlamalar

    Suggestions ("Taklif") open from Settings, and the Mini App from
    Telegram's own menu button beside the text field. "Turdim" lives on the
    Habits screen; typing it still works. `stage` and `team` are accepted for
    older callers and no longer change the layout.
    """
    rows = [
        [t(lang, "menu_home"), t(lang, "menu_money")],
        [t(lang, "menu_habits"), t(lang, "menu_tasks")],
        [t(lang, "menu_teams"), t(lang, "menu_stats")],
        [t(lang, "menu_settings")],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def menu_for(uid: int | None, lang: str | None = None) -> ReplyKeyboardMarkup:
    """`main_menu` as this account should see it today."""
    try:
        with SessionLocal() as s:
            user = s.get(User, uid) if uid is not None else None
            if user is None:
                return main_menu(lang or "uz")
            team = bool(svc.teams_for(s, uid))
            return main_menu(lang or user.language, ui_stage(user), team=team)
    except Exception:
        log.exception("could not build the menu for %s", uid)
        return main_menu(lang or "uz")


def webapp_button(lang: str) -> InlineKeyboardMarkup | None:
    if not WEBAPP_URL:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        t(lang, "menu_app"), web_app=WebAppInfo(url=WEBAPP_URL))]])


def cancel_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t(lang, "cancel"), callback_data="flow:cancel")]])


def account_of(update: Update) -> int | None:
    """The ErnestOS account this update speaks for.

    Usually the sender's own Telegram id. A Telegram that signed in with a
    login and password (`accounts.sign_in`) is served as the account it
    signed in to — every handler reads the account from here, never from
    `effective_user.id` directly.
    """
    tg_user = update.effective_user
    if tg_user is None:
        return None
    return accounts.resolve_id(tg_user.id)


def _profile(update: Update, uid: int) -> dict:
    """Telegram profile fields to refresh — only on the sender's own account.

    A linked Telegram must not overwrite the owner's name and username with
    its own every time it sends a message.
    """
    tg_user = update.effective_user
    if tg_user is None or tg_user.id != uid:
        return {}
    return {"first_name": tg_user.first_name or "",
            "last_name": tg_user.last_name or "",
            "username": tg_user.username or ""}


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------

#: Where an invite points. The bot, not the Mini App, because ErnestOS
#: onboarding starts in the chat — a `startapp` link would drop somebody into
#: an app that immediately tells them to go and finish signing up.
def referral_link(code: str) -> str | None:
    if not BOT_USERNAME:
        return None
    return f"https://t.me/{BOT_USERNAME}?start={svc.REFERRAL_PREFIX}{code}"


def referral_miniapp_link(code: str) -> str | None:
    """The `startapp` form. Returned by the API for later use; not the CTA."""
    if not BOT_USERNAME:
        return None
    return f"https://t.me/{BOT_USERNAME}?startapp={svc.REFERRAL_PREFIX}{code}"


async def notify_referral_qualified(bot, inviter_id: int) -> None:
    """Tell an inviter their friend actually started using ErnestOS.

    Called only when `maybe_qualify_referral` reports it was *this* call that
    promoted the referral, so it fires exactly once per friend and cannot
    become a recurring nudge.

    Deliberately application-layer: `services` never opens a socket, so the
    core stays testable without a bot and a Telegram outage can never roll back
    a database transaction. Failures here are logged and dropped — a missed
    congratulation must not undo a qualification that genuinely happened.
    """
    try:
        with SessionLocal() as s:
            inviter = s.get(User, inviter_id)
            if inviter is None:
                return
            lang = inviter.language
            stats = svc.referral_stats(s, inviter_id)

        nxt = stats["level"]["next"]
        progress = (t(lang, "ref_next_level",
                      done=stats["counts"]["qualified"], target=nxt["target"])
                    if nxt else t(lang, "ref_max_level"))
        await bot.send_message(
            inviter_id,
            t(lang, "ref_qualified", qualified=stats["counts"]["qualified"],
              progress=progress),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t(lang, "ref_invite_again"),
                                       callback_data="ref:show")]]))
    except Exception:
        log.warning("could not tell %s their referral qualified", inviter_id,
                    exc_info=True)


async def notify_level_up(bot, telegram_id: int, level: dict, lang: str) -> None:
    """One message, on the tick a level is actually crossed. Never again.

    The only progression event that interrupts somebody. Streaks, XP and rank
    changes live on the Progress screen, where they can be looked at when the
    user chooses to — a notification for each of them is exactly the
    gamification spam the product is not allowed to become.

    Application layer for the same reason `notify_referral_qualified` is: the
    service layer never opens a socket, so a Telegram outage cannot roll back
    the transaction that awarded the level in the first place.
    """
    try:
        await bot.send_message(
            telegram_id,
            t(lang, "level_up", numeral=level["numeral"],
              name=t(lang, f"plevel_{level['key']}"),
              xp=f"{level['current_threshold']:,}".replace(",", " ")),
            parse_mode=ParseMode.HTML,
            reply_markup=webapp_button(lang))
    except Exception:
        log.warning("could not tell %s about their level", telegram_id,
                    exc_info=True)


async def finish_onboarding_progress(telegram_id: int) -> None:
    """Score the day and pay the welcome XP the moment onboarding completes.

    Two reasons this cannot wait for the user's next action. Onboarding itself
    creates habits and a first task, so there is already a day's worth of work
    banked behind a flag that was only just set — and a progression system that
    shows 0 XP to somebody who has just spent five minutes setting up reads as
    broken.

    The welcome award is real progress for real work, not a fake head start:
    it is paid for *completing onboarding*, once, keyed on the user id.
    """
    try:
        with SessionLocal() as s:
            svc.award_xp(s, telegram_id, f"onboarding:{telegram_id}",
                         "onboarding", svc.XP_VALUES["onboarding"],
                         svc.today_local())
            svc.refresh_progress(s, telegram_id)
            s.commit()
    except Exception:
        log.exception("could not start progression for %s", telegram_id)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    message = update.effective_message
    if tg_user is None or message is None:
        return

    # `/start ref_xxxx` — Telegram hands the payload over as the first argument.
    # Only the first one is read; a start parameter is a single opaque token.
    payload = ctx.args[0] if getattr(ctx, "args", None) else None
    uid = account_of(update)

    with SessionLocal() as s:
        user, created = svc.get_or_create_user(s, uid, **_profile(update, uid))
        s.commit()
        # Attribution is attempted only for an account that did not exist a
        # moment ago. That single condition is what stops an existing user from
        # being claimed by anybody who can persuade them to open a link.
        if created:
            svc.claim_referral(s, uid, payload,
                               source="bot", newly_created=True)
    team_code = svc.parse_team_payload(payload)

    with SessionLocal() as s:
        user = s.get(User, uid)
        lang, step, onboarded = user.language, user.onboarding_step, user.onboarded
        name = user.first_name or tg_user.first_name or ""
        snapshot = user

    if created:
        await log_event(ctx.bot, snapshot, f"🆕 NEW ERNESTOS USER #{snapshot.member_no}",
                        f"Language: {lang}\nRegistered: "
                        f"{datetime.now(svc.TZ):%Y-%m-%d %H:%M}")

    # A team invite is shown once the account exists, for a new user and an
    # existing one alike — as a preview with a Join button. Opening a link
    # never adds anybody to anything by itself: they see the team, who runs
    # it and how full it is, and decide.
    if team_code:
        await preview_team_invite(update, ctx, team_code)
        if onboarded:
            return

    if onboarded:
        await message.reply_text(t(lang, "hello_named", name=esc(name)),
                                 parse_mode=ParseMode.HTML,
                                 reply_markup=menu_for(uid))
        # Accounts from before logins existed get theirs on the next /start.
        await issue_credentials(message, uid, lang)
        await show_home(update, ctx)
        return

    await resume_onboarding(update, ctx, step)


#: The order onboarding walks, and the only place it is written down.
#:
#:   language → account → name → modules → done
#:
#: Four taps and one short answer. `account` is where somebody who already
#: has ErnestOS on another Telegram signs in instead of starting again; a new
#: account is handed its login and password on that same screen.
#:
#: Everything else the old setup asked for — a weekly goal, three tasks,
#: three habits — is one tap away on the screens themselves, with ten
#: suggestions under each Add button. A new user sees the smallest possible
#: product first and more of it as they use it (`ui_stage`).
#:
#: What is deliberately not here:
#:   * the channel, asked after `FREE_ACTIONS` real actions instead;
#:   * the phone number, not asked anywhere;
#:   * gender, asked the first time prayer is opened.
ONBOARDING_STEPS = ["language", "account", "name", "modules", "presets", "done"]

#: The choices on the modules step, in the order they are shown. Three are the
#: rituals `services.MODULES` drives; "team" is a promise to offer a team at
#: the end rather than a habit.
SETUP_MODULES = ["wake", "prayer", "journal", "team"]
MODULE_LABELS = {"wake": "mod_wake", "prayer": "mod_prayer",
                 "journal": "mod_journal", "team": "mod_team"}


def modules_keyboard(lang: str, chosen: set[str], *, prefix: str = "setup:mod",
                     done: str = "setup:mod_done") -> InlineKeyboardMarkup:
    """One toggle per module, ✓ when chosen, and a button to carry on."""
    rows = [[InlineKeyboardButton(
        f"{'✅' if name in chosen else '⬜'} {t(lang, MODULE_LABELS[name])}",
        callback_data=f"{prefix}:{name}")]
        for name in (SETUP_MODULES if prefix.startswith("setup") else list(svc.MODULES))]
    rows.append([InlineKeyboardButton(t(lang, "mod_continue"), callback_data=done)])
    return InlineKeyboardMarkup(rows)


def setup_presets_keyboard(lang: str, chosen: set[str]) -> InlineKeyboardMarkup:
    """The seven ordinary ready-made habits, all ticked to start with.

    The three rituals were the step before; together they make the ten. An
    account starts with the list a person would most likely build anyway and
    takes away what does not fit, rather than facing an empty screen.
    """
    rows = [[InlineKeyboardButton(
        f"{'✅' if key in chosen else '⬜'} {svc.preset_name(key, lang)}",
        callback_data=f"setup:pre:{key}")] for key in svc.ORDINARY_PRESET_KEYS]
    rows.append([InlineKeyboardButton(t(lang, "mod_continue"),
                                      callback_data="setup:pre_done")])
    return InlineKeyboardMarkup(rows)


def _setup_presets(ctx: ContextTypes.DEFAULT_TYPE) -> set[str]:
    data = setup_data(ctx)
    if "presets" not in data:
        data["presets"] = list(svc.ORDINARY_PRESET_KEYS)
    return set(data["presets"])

#: Steps from older builds, and where somebody parked on one continues. The
#: long setup's questions are gone; anybody half-way through it is finished.
LEGACY_STEPS = {"phone": "name", "gender": "name", "subscribe": "name",
                "intro": "account", "goal": "done", "tasks": "done",
                "habits": "done"}


def setup_data(ctx: ContextTypes.DEFAULT_TYPE) -> dict:
    """The half-finished setup, kept per user for the length of the flow."""
    return ctx.user_data.setdefault("setup", {})


async def resume_onboarding(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                            step: str) -> None:
    """Onboarding is driven by `users.onboarding_step`, so a restart resumes."""
    message = update.effective_message
    tg_user = update.effective_user
    if message is None or tg_user is None:
        return
    uid = account_of(update)

    with SessionLocal() as s:
        user = s.get(User, uid)
        lang = user.language if user else "uz"
        name = (user.first_name if user else "") or tg_user.first_name or ""

    if step in LEGACY_STEPS:
        step = LEGACY_STEPS[step]
        with SessionLocal() as s:
            user = s.get(User, uid)
            if user is not None:
                user.onboarding_step = step
                s.commit()

    if step == "language":
        # No language is chosen yet, so the prompt is the one screen written in
        # all three. Everything after this point is in the chosen language only.
        await message.reply_text(
            t(lang, "pick_lang_multi"), reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🇺🇿 O'zbek", callback_data="lang:uz")],
                [InlineKeyboardButton("🇬🇧 English", callback_data="lang:en")],
                [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru")],
            ]))

    elif step == "account":
        await message.reply_text(t(lang, "acc_ask"), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(t(lang, "acc_new"), callback_data="acc:new")],
            [InlineKeyboardButton(t(lang, "acc_have"), callback_data="acc:have")]]))

    elif step == "name":
        rows = []
        if name:
            rows.append([InlineKeyboardButton(f"👤 {name}",
                                              callback_data="setup:name")])
        await message.reply_text(t(lang, "ask_name"), parse_mode=ParseMode.HTML,
                                 reply_markup=InlineKeyboardMarkup(rows) if rows
                                 else None)

    elif step == "modules":
        chosen = set(setup_data(ctx).setdefault("modules", []))
        await message.reply_text(t(lang, "ask_modules"), parse_mode=ParseMode.HTML,
                                 reply_markup=modules_keyboard(lang, chosen))

    elif step == "presets":
        await message.reply_text(t(lang, "setup_presets"), parse_mode=ParseMode.HTML,
                                 reply_markup=setup_presets_keyboard(
                                     lang, _setup_presets(ctx)))

    else:
        await finish_onboarding(update, ctx)


async def advance_setup(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                        step: str) -> None:
    """Write the next step down, then ask it."""
    uid = account_of(update)
    with SessionLocal() as s:
        user = s.get(User, uid)
        if user is not None:
            user.onboarding_step = step
            s.commit()
    if step == "done":
        await finish_onboarding(update, ctx)
    else:
        await resume_onboarding(update, ctx, step)


async def handle_setup_answer(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                              step: str, text: str) -> None:
    """A typed answer during setup. Only the name is typed; it is written at once."""
    uid = account_of(update)
    with SessionLocal() as s:
        user = s.get(User, uid)
        if user is None:
            return

    if step == "name":
        with SessionLocal() as s:
            user = s.get(User, uid)
            user.first_name = text.strip()[:200]
            s.commit()
        return await advance_setup(update, ctx, "modules")

    # Any other step takes no typed answer; re-ask rather than swallow it.
    await resume_onboarding(update, ctx, step)


async def issue_credentials(message, uid: int, lang: str) -> bool:
    """Give an account its login and password, once. True if issued now.

    The password is shown in a spoiler with a button that deletes the
    message: it is the only time it is ever readable, and a chat history is
    not where it should stay.
    """
    try:
        with SessionLocal() as s:
            login, password = accounts.ensure_credentials(s, uid)
    except Exception:
        log.exception("could not issue credentials for %s", uid)
        return False
    if password is None or message is None:
        return False
    await message.reply_text(
        t(lang, "acc_issued", login=esc(login), password=esc(password)),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
            t(lang, "acc_saved_btn"), callback_data="acc:hide")]]))
    return True


#: Callback actions that change the day, and therefore spend a free action.
#: `set` and `theme` are settings, not use — the same reasoning as
#: `UNCOUNTED_PATHS` on the API side.
COUNTED_CALLBACKS = {"habit", "task", "taskday", "taskproj", "project", "habitcat",
                     "thabit", "ttask", "cap", "ted", "tep", "tem", "tex",
                     "hec", "hem", "hex", "cdq", "pjd", "pjdest"}

#: Buttons that only open a screen. They work for an account the channel gate
#: has stopped, because reading your own data is never what the gate is for.
READ_CALLBACKS = {("habit", "back"), ("habit", "noop"),
                  ("habit", "mirrored"), ("task", "back"), ("task", "noop"),
                  ("tmr", "home"), ("tmr", "list"), ("tmr", "back"),
                  ("cd", "list"), ("cd", "back"), ("team", "list"),
                  ("team", "open"), ("team", "stats"),
                  ("pj", "list"), ("pj", "open"),
                  ("task", "restorelist"), ("habit", "presets"),
                  ("money", "show"), ("money", "limits")}


def is_read_callback(action: str, parts: list[str]) -> bool:
    sub = parts[1] if len(parts) > 1 else ""
    return action == "home" or (action, sub) in READ_CALLBACKS


async def on_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """A shared contact is acknowledged and dropped.

    ErnestOS no longer asks for a phone number, and nothing in the bot offers
    a contact button, so the only way one arrives is a user sending it
    unprompted — usually from a keyboard left over from an older build.
    Storing a number the product has stopped asking for would be exactly the
    surprise the change was meant to remove, so it is not stored. The reply
    clears that stale keyboard and says why.
    """
    message = update.effective_message
    uid = account_of(update)
    if message is None or message.contact is None or uid is None:
        return

    with SessionLocal() as s:
        user = s.get(User, uid)
        lang = user.language if user else "uz"

    await message.reply_text(t(lang, "phone_not_needed"),
                             reply_markup=menu_for(uid))


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Store the Telegram file_id of an uploaded avatar.

    Only the id is kept — Telegram already hosts the bytes, so the database
    never holds binary blobs.
    """
    message = update.effective_message
    uid = account_of(update)
    if message is None or not message.photo or uid is None:
        return
    # `current_flow` rather than a raw dictionary read, because only it honours
    # the TTL. Without it a "send me a photo" prompt opened yesterday was still
    # armed today, and the next picture the user sent for any reason at all
    # silently became their avatar.
    if current_flow(ctx, "photo_wait") is None:
        return

    file_id = message.photo[-1].file_id          # highest resolution
    with SessionLocal() as s:
        user = s.get(User, uid)
        if user is None:
            return
        user.photo_file_id = file_id
        s.commit()
        lang, snapshot = user.language, user

    ctx.user_data.pop("flow", None)
    await message.reply_text(t(lang, "photo_saved"), reply_markup=menu_for(uid))
    await log_event(ctx.bot, snapshot, "🖼 PHOTO UPDATED")




async def show_guide(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """The new-user guide, on demand.

    It is sent once automatically, and once is not always the moment somebody
    reads it, so /guide brings it back rather than making them scroll a month
    of chat.
    """
    got = await guard(update, ctx)
    if got is None:
        return
    user, _ = got
    if update.effective_message:
        await update.effective_message.reply_text(
            t(user.language, "guide"), parse_mode=ParseMode.HTML,
            disable_web_page_preview=True)


async def finish_onboarding(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """The last screen of setup: the day they just built, not a tour.

    Nothing is checked here any more. The channel used to be the gate at this
    exact point — somebody could answer every question and still be turned away
    at the end, which is the worst possible place to put a wall. It is now
    asked after `FREE_ACTIONS` real actions instead.
    """
    uid = account_of(update)
    message = update.effective_message
    if uid is None or message is None:
        return

    with SessionLocal() as s:
        user = s.get(User, uid)
        if user is None:
            return
        user.onboarding_step = "done"
        user.onboarded = True
        s.commit()
        # Qualification needs `onboarded` *and* three actions, and onboarding
        # itself creates tasks and habits — so somebody can arrive here with
        # the actions already banked and only this flag missing. Same central
        # check as the action path; it is a no-op for everybody else.
        qualified_inviter = svc.maybe_qualify_referral(s, uid)
        lang, snapshot = user.language, user
        ws = svc.workspace_id_for(s, uid)
        data = svc.home(s, ws, user)

    # Same reasoning as the referral check above, for the other progression
    # system: onboarding has already created habits and a task, so the day has
    # a score before the user's next tap.
    await finish_onboarding_progress(uid)

    wants_team = bool((ctx.user_data.get("setup") or {}).get("team"))
    ctx.user_data.pop("setup", None)
    # Anybody who reached the end without the account screen — an older
    # build's setup, or the channel check finishing it — gets theirs here.
    await issue_credentials(message, uid, lang)
    await message.reply_text(render_day_ready(data, lang), parse_mode=ParseMode.HTML,
                             reply_markup=menu_for(uid))
    markup = webapp_button(lang)
    if markup:
        await message.reply_text(t(lang, "day_ready_app"), reply_markup=markup)
    if wants_team:
        await message.reply_text(
            t(lang, "setup_team_hint"), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                t(lang, "team_create_btn"), callback_data="team:new")]]))
    await log_event(ctx.bot, snapshot, "✅ ONBOARDING COMPLETE",
                    f"Language: {snapshot.language}")
    if qualified_inviter is not None:
        await notify_referral_qualified(ctx.bot, qualified_inviter)


def render_day_ready(data: dict, lang: str) -> str:
    """"Your day is ready" — the setup's closing screen.

    It prints what the user just created, in the order they created it, and
    ends on the one thing to do first. Not a summary of features: a summary of
    *their* day, which is the only proof that any of the questions were worth
    answering.
    """
    name = (data.get("name") or "").strip()
    lines = [f"<b>{t(lang, 'day_ready', name=esc(name)) if name else t(lang, 'day_ready_plain')}</b>",
             ""]

    groups = data.get("tasks_today") or []
    # Whatever was pinned leads, then the rest of today — the same order the
    # app itself shows them in.
    tasks = list(data.get("top3") or [])
    tasks += [x for group in groups for x in group["tasks"]]
    if tasks:
        lines.append(f"⚡ <b>{t(lang, 'r_today_plan')}</b>")
        for task in tasks[:3]:
            lines.append(f"• {esc(task['title'])}")
        lines.append("")

    habits = data.get("habits") or {}
    if habits.get("total"):
        lines.append(f"✅ <b>{t(lang, 'r_habits_today')}</b> · {habits['total']}")
    if (data.get("prayer") or {}).get("owed", True):
        lines.append(f"🕌 <b>{t(lang, 'r_prayer_today')}</b>")
    lines.append("")

    now = data.get("now") or {}
    if now.get("title"):
        lines.append(f"👉 <b>{t(lang, 'day_ready_first')}</b>")
        lines.append(esc(now["title"]))
    else:
        lines.append(t(lang, "day_ready_open"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

#: The trend arrow. Direction is carried by the shape as well as the colour,
#: so it still reads on a monochrome screen or to a colour-blind user.
TREND_MARK = {"up": "🔺", "down": "🔻", "flat": "▪️"}


#: How each kind of "what now?" answer is written in the bot. The Mini App
#: renders the same `now` payload its own way; both read the same field, so the
#: two surfaces can never suggest different next actions.
NOW_ICON = {"wake": "☀️", "task": "⚡", "habit": "✅", "prayer": "🕌",
            "journal": "🌙", "clear": "✓"}


def render_now(now: dict, lang: str) -> str:
    """The one line that answers "what should I do right now?"."""
    kind = now.get("kind") or "clear"
    icon = NOW_ICON.get(kind, "▫️")
    if kind == "task" or kind == "habit":
        meta = f" — {now['meta']}" if now.get("meta") else ""
        line = f"{icon} {esc(now.get('title') or '')}{meta}"
        late = now.get("overdue")
        if late:
            # A pinned task never hides a late one.
            more = f" (+{late['count'] - 1})" if late["count"] > 1 else ""
            line += f"\n⏰ {t(lang, 'now_also_late')}: {esc(late['title'])}{more}"
        return line
    return f"{icon} {t(lang, 'now_' + kind)}"


def home_counts_line(counts: dict, lang: str, streak: int = 0) -> str:
    """`Vazifa 1/3 · Odat 2/6 · Jamoa 1/2 · Namoz 3/5 · 🔥 4` — counts, never a
    percentage.

    The same line the Mini App draws under its Now card, from the same
    `counts` payload, so the two Homes cannot disagree about the day.
    """
    parts = []
    tasks, habits, prayer = counts["tasks"], counts["habits"], counts["prayer"]
    if tasks["total"]:
        parts.append(f"{t(lang, 'cnt_tasks')} {tasks['done']}/{tasks['total']}")
    if habits["total"]:
        parts.append(f"{t(lang, 'cnt_habits')} {habits['done']}/{habits['total']}")
    team = counts.get("team") or {}
    if team.get("total"):
        parts.append(f"{t(lang, 'cnt_team')} {team['done']}/{team['total']}")
    if prayer.get("owed", True):
        parts.append(f"{t(lang, 'cnt_prayer')} "
                     + ("✓" if prayer.get("excused")
                        else f"{prayer['done']}/{prayer['total']}"))
    if streak:
        parts.append(f"🔥 {streak}")
    return " · ".join(parts)


def render_home(data: dict, lang: str) -> str:
    """Home in one screenful: the date, what to do now, the counts, today.

        🗓️ 28-sentabr, Dushanba

        👉 Hozir
        ⚡ Q4 rejasini tayyorlash

        Vazifa 1/3 · Odat 2/6 · Namoz 3/5 · 🔥 4

        ⚡ Bugun
        — 👥 Speaking | 30mins (Miro*)

    The same order as the Mini App's Home: one action first, one line of
    counts, then the rest of today. No percentages and no week goal here —
    the goal lives on Tasks and the numbers on Statistics.
    """
    lines = [f"🗓️ {data['date_label']}"]

    lines.append(f"\n<b>{t(lang, 'home_now')}</b>")
    lines.append(render_now(data.get("now") or {}, lang))

    counts = data.get("counts")
    if counts:
        line = home_counts_line(counts, lang, data.get("streak") or 0)
        if line:
            lines.append(f"\n{line}")

    # The pinned task first — it is the day's main one — then the rest. The
    # task already named under "Hozir" is not printed a second time, exactly
    # as the Mini App's Home leaves it out of its list.
    now = data.get("now") or {}
    shown = now.get("id") if now.get("kind") == "task" else None
    pinned = [x for x in (data.get("top3") or []) if x.get("status") != "done"]
    rows = [x for x in pinned + [task for group in data["tasks_today"]
                                 for task in group["tasks"]]
            if x.get("id") != shown]
    # Shared work due today is today's work too; marked 👥 so it is clear
    # whose list it came from, and ticked per person.
    shared = [x for x in (data.get("team_today") or []) if x.get("owed", True)]
    if not rows and not shared and shown is not None:
        # Today's one task is the one above; "Today: none" under it would be
        # the screen contradicting itself.
        return "\n".join(lines)
    lines.append(f"\n<b>{t(lang, 'home_today')}</b>")
    if rows or shared:
        for task in rows[:8]:
            when = f" · {task['due_time']}" if task.get("due_time") else ""
            badge = _timer_badge(task, lang)
            lines.append(f"— {esc(task['title'])}{when}"
                         + (f" · {badge}" if badge else ""))
        for task in shared[:6]:
            mark = "✅" if task.get("done") else "—"
            lines.append(f"{mark} 👥 {esc(task['title'])}"
                         f" ({esc(task.get('team_name') or '')})")
    else:
        lines.append(t(lang, "none"))
    return "\n".join(lines)


def countdown_left(item: dict, lang: str) -> str:
    """`49 kun qoldi`, `ertaga!`, `bugun! 🎉` — never "0 days" or "1 days"."""
    days = item["days_left"]
    if days < 0:
        return t(lang, "cd_passed")
    if days == 0:
        return t(lang, "cd_today")
    if days == 1:
        return t(lang, "cd_tomorrow")
    return t(lang, "cd_days_left", n=days)


def countdown_line(item: dict, lang: str) -> str:
    return (f"⏳ {esc(item['title'])} — {countdown_left(item, lang)}"
            f" <i>· {short_date(item['date'], lang)}</i>")


def home_keyboard(lang: str, stage: int = 3) -> InlineKeyboardMarkup:
    """Home's ways onward: add a habit or a task. Statistics has its own
    keyboard button again, so it is not repeated here (an old message's
    `home:stats` still opens it)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "home_add_habit"), callback_data="habit:add"),
         InlineKeyboardButton(t(lang, "home_add_task"), callback_data="task:add")]])


def _bar(percent: int, width: int = 10) -> str:
    """A ten-cell text bar. Two numbers side by side are hard to compare at a
    glance in a chat message; two bars are not."""
    filled = max(0, min(width, round((percent or 0) / 100 * width)))
    return "▰" * filled + "▱" * (width - filled)


def render_stats(data: dict, lang: str) -> str:
    """Today, this week and this month, side by side and comparable.

    A single percentage says nothing on its own. The point of this screen is the
    comparison: whether today is better than the week, and the week better than
    the month. Each row therefore carries its own overall number, and the three
    are printed in the same units so the eye can do the arithmetic.
    """
    today = data["today"]
    dash = "—"

    def pct(value) -> str:
        # A component with nothing due today has no percentage. Printing 0%
        # would claim the user failed at something they were never asked to do.
        return f"{value}%" if value is not None else dash

    lines = [f"<b>{t(lang, 'stats_title')}</b>", ""]

    # Today, in full: the overall number and what it is made of.
    measured = today.get("measured", True)
    lines.append(f"<b>{t(lang, 'st_today')}</b>")
    lines.append(f"{_bar(today['overall'] if measured else 0)}  "
                 f"<b>{today['overall'] if measured else dash}"
                 f"{'%' if measured else ''}</b> "
                 f"{TREND_MARK.get(today['trend'], '▪️')}")
    parts = [f"{t(lang, 'st_tasks')}: {pct(today['tasks'])}",
             f"{t(lang, 'st_habits')}: {pct(today['habits'])}"]
    if today.get("prayer_owed", True):
        parts.append(f"{t(lang, 'st_prayer')}: {today['prayer_performed']}/"
                     f"{today['prayer_required']}")
    if today.get("team") is not None:
        parts.append(f"{t(lang, 'cnt_team')}: {pct(today['team'])}")
    lines.append(" · ".join(parts))

    # Then the two longer windows, each with its own overall.
    for key, label in (("week", "st_week"), ("month", "st_month")):
        window = data["windows"][key]
        lines.append("")
        lines.append(f"<b>{t(lang, label)}</b>")
        if not window.get("measured", True):
            lines.append(f"<i>{t(lang, 'st_nothing_measured')}</i>")
            continue
        lines.append(f"{_bar(window['overall'])}  <b>{window['overall']}%</b> "
                     f"{_delta(window['delta'], lang)}")
        lines.append(f"{t(lang, 'st_tasks')}: {window['tasks']}% · "
                     f"{t(lang, 'st_habits')}: {window['habits']}% · "
                     f"{t(lang, 'st_prayer')}: {window['prayer']}%")

    lines.append("")
    lines.append(f"🔥 {t(lang, 'st_streak')}: {today['streak']}")

    # Every team this person is in, each member's share side by side: today,
    # the last seven days and the last thirty.
    for team in data.get("teams") or []:
        lines += [""] + render_team_stats(team, lang)
    lines.append("")
    lines.append(t(lang, "privacy_line"))
    return "\n".join(lines)


def render_team_stats(team: dict, lang: str) -> list[str]:
    """One team on the statistics screen — names, bars, and the two windows."""
    board = team.get("board") or {}
    periods = board.get("periods") or {}
    lines = [f"👥 <b>{esc(team['name'])}</b>"]
    today = periods.get("day") or []
    if not today:
        lines.append(f"<i>{t(lang, 'empty')}</i>")
        return lines
    lines.append(f"<b>{t(lang, 'st_today')}</b>")
    for row in today:
        percent = row["percent"]
        lines.append(f"{_bar(percent or 0, 6)}  {esc(row['name'])} · "
                     f"{row['done']}/{row['total']}"
                     + (f" · {percent}%" if percent is not None else ""))
    for key, label in (("week", "st_week_short"), ("month", "st_month_short")):
        rows = periods.get(key) or []
        cells = [f"{esc(r['name'])} {r['percent']}%" if r["percent"] is not None
                 else f"{esc(r['name'])} —" for r in rows]
        lines.append(f"{t(lang, label)}: " + " · ".join(cells))
    units = board.get("units") or {}
    if units.get("items"):
        lines.append(f"<i>{t(lang, 'team_units', items=units['items'], confirmed=units['confirmed'], confirmations=units['confirmations'], left=units['left'])}</i>")
    return lines


def _delta(value: int | None, lang: str = "uz") -> str:
    """A signed change against the previous window of the same length, in
    points — "+12 punkt", never "+12%", which would read as a relative rise."""
    if value is None:
        return ""
    if not value:
        return "▪️"
    return f"{'🔺' if value > 0 else '🔻'}{t(lang, 'points', n=abs(value))}"


async def show_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    got = await guard(update, ctx, write=False)
    if got is None:
        return
    user, ws = got
    with SessionLocal() as s:
        tz = svc.user_tz(user)
        data = svc.summary(s, ws, gender=user.gender, tz=tz)
        data["teams"] = [{"name": team.name,
                          "board": svc.team_scoreboard(s, user.telegram_id,
                                                       team.id, tz=tz)}
                         for team in svc.teams_for(s, user.telegram_id)]
    message = update.effective_message
    if message:
        await message.reply_text(render_stats(data, user.language),
                                 parse_mode=ParseMode.HTML,
                                 reply_markup=webapp_button(user.language))


def wake_reply(result: dict, lang: str) -> str:
    """What to say back after a "turdim".

    A late morning is reported as a fact and not as a failure: the time is shown
    either way, and the late version says how it compares with the target rather
    than announcing that the day does not count. The habit still only completes
    on time — the wording changes, the rule does not.
    """
    if result["done"]:
        return t(lang, "wake_ok_at", now=result["now"])
    return t(lang, "wake_late_soft", now=result["now"], target=result["target"])


async def handle_wakeup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """The user reports getting up. Only counts before target time + 1 hour."""
    got = await guard(update, ctx)
    if got is None:
        return
    user, ws = got
    with SessionLocal() as s:
        result = svc.mark_wakeup(s, ws, tz=svc.user_tz(user))

    message = update.effective_message
    if message is None:
        return
    await message.reply_text(wake_reply(result, user.language))
    if result["done"]:
        await log_event(ctx.bot, user, "☀️ WAKE-UP", f"At: {result['now']}")


async def show_home(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    got = await guard(update, ctx, write=False)
    if got is None:
        return
    user, ws = got
    with SessionLocal() as s:
        data = svc.home(s, ws, s.get(User, user.telegram_id))
        today = data["date"]
        data["team_today"] = [
            x for x in svc.team_items_for_day(
                s, user.telegram_id, tz=svc.user_tz(user))["tasks"]
            if x.get("deadline") == today]
    message = update.effective_message
    if message:
        await message.reply_text(render_home(data, user.language),
                                 parse_mode=ParseMode.HTML,
                                 reply_markup=home_keyboard(user.language,
                                                            ui_stage(user)))


# ---------------------------------------------------------------------------
# Money — its own screen, outside every productivity number
# ---------------------------------------------------------------------------

def fmt_money(amount: int, lang: str) -> str:
    """`1 250 000 so'm` — grouped by thousands with a thin space."""
    return f"{int(amount):,}".replace(",", " ") + " " + t(lang, "money_unit")


def money_capture_text(money: dict, lang: str) -> str:
    """The amount as read and the words it came from — no sign: which way the
    money went is the person's one tap, not the parser's guess."""
    return (f"💰 <b>{fmt_money(money['amount'], lang)}</b>\n"
            f"<i>{esc(money['note'])}</i>\n\n{t(lang, 'money_capture_ask')}")


def money_keyboard(lang: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(t(lang, "money_btn_expense"),
                                  callback_data="money:add:expense"),
             InlineKeyboardButton(t(lang, "money_btn_income"),
                                  callback_data="money:add:income")],
            [InlineKeyboardButton(t(lang, "money_btn_limits"),
                                  callback_data="money:limits"),
             InlineKeyboardButton(t(lang, "money_btn_refresh"),
                                  callback_data="money:show")]]
    if WEBAPP_URL:
        rows.append([InlineKeyboardButton(
            t(lang, "menu_app"), web_app=WebAppInfo(url=WEBAPP_URL + "?s=money"))])
    return InlineKeyboardMarkup(rows)


def render_money(data: dict, lang: str) -> str:
    """This month's money: the balance, in and out, the categories that were
    used against their limits, and the latest entries."""
    month = f"{svc.MONTHS[lang][data['month_no'] - 1]} {data['year']}" \
        if lang in svc.MONTHS else data["month"]
    lines = [f"💰 <b>{t(lang, 'money_title')}</b> · {month}", "",
             f"💼 {t(lang, 'money_balance')}: <b>{fmt_money(data['balance'], lang)}</b>",
             f"📈 {t(lang, 'money_income')}: {fmt_money(data['income'], lang)}",
             f"📉 {t(lang, 'money_expense')}: {fmt_money(data['expense'], lang)}"]
    used = [c for c in data["categories"] if c["spent"]]
    if used:
        lines += ["", f"<b>{t(lang, 'money_by_category')}</b>"]
        for c in sorted(used, key=lambda x: -x["spent"]):
            cap = f" / {fmt_money(c['limit'], lang)}" if c["limit"] else ""
            warn = " ⚠️" if c["over"] else ""
            lines.append(f"{c['icon']} {t(lang, 'mcat_' + c['id'])} — "
                         f"{fmt_money(c['spent'], lang)}{cap}{warn}")
    if data["entries"]:
        lines += ["", f"<b>{t(lang, 'money_latest')}</b>"]
        for e in data["entries"][:8]:
            sign = "−" if e["kind"] == "expense" else "+"
            icon = data["icons"].get(e["category"], "•")
            note = f" · {esc(e['note'][:40])}" if e["note"] else ""
            lines.append(f"{sign}{fmt_money(e['amount'], lang)} {icon}{note}"
                         f" <i>· {short_date(e['day'], lang)}</i>")
    else:
        lines += ["", t(lang, "money_empty")]
    lines += ["", f"<i>{t(lang, 'money_hint')}</i>"]
    return "\n".join(lines)


async def show_money(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                     edit: bool = False) -> None:
    got = await guard(update, ctx, write=False)
    if got is None:
        return
    user, ws = got
    with SessionLocal() as s:
        data = svc.money_overview(s, ws, tz=svc.user_tz(user), limit=8)
    await _show(update, render_money(data, user.language),
                money_keyboard(user.language), edit=edit)


# ---------------------------------------------------------------------------
# Habits
# ---------------------------------------------------------------------------

CATEGORY_KEYS = {"non_negotiable": "cat_non_negotiable",
                 "target": "cat_target", "bonus": "cat_bonus"}


def fmt_minutes(minutes: int | None, lang: str) -> str:
    """`5 soat`, `45 daq`, `1 soat 30 daq` — a length the way it is said."""
    minutes = int(minutes or 0)
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return t(lang, "dur_hm", h=hours, m=rest)
    if hours:
        return t(lang, "dur_h", h=hours)
    return t(lang, "dur_m", m=rest)


def fmt_left(seconds: int, lang: str) -> str:
    """What is left on a timer, rounded up to the minute.

    Minutes rather than seconds, because the bot's message is refreshed on a
    tick and a seconds figure would sit frozen between refreshes, looking
    broken. The Mini App, which can redraw every second, shows the seconds.
    """
    return fmt_minutes(max(1, -(-int(seconds or 0) // 60)), lang)


def _timer_badge(item: dict, lang: str) -> str:
    """The short timer note on a row: `⏱ 5 soat`, or the live state."""
    run = item.get("timer")
    if run and run.get("status") == "running":
        return f"▶️ {fmt_left(run['remaining_sec'], lang)}"
    if run and run.get("status") == "paused":
        return f"⏸ {fmt_left(run['remaining_sec'], lang)}"
    if item.get("timer_minutes"):
        return f"⏱ {fmt_minutes(item['timer_minutes'], lang)}"
    return ""


def habits_keyboard(grouped: dict, lang: str, *,
                    stage: int = 3) -> InlineKeyboardMarkup:
    """The habits first, two to a row, and the controls under them.

    Top of the message: nothing but the habits, in tier order (non-negotiable,
    then target, then bonus), two columns so ten habits fit one screen — the
    thing opened this screen to tick. Bottom: "Turdim" while it can still be
    recorded, then add, edit and the ready-made list. There is no restore:
    the ready-made list brings a removed one back with its history.

    Shared habits sit beside private ones, marked 👥, and tick through the
    team's own toggle — each member ticks only their own share. A habit with a
    timer opens its timer instead of ticking: it is done by the clock running
    out, not by the box.
    """
    buttons = []
    for category in svc.HABIT_CATEGORIES:
        habits = grouped.get(category, [])
        for h in habits:
            if h.get("source") == "team":
                mark = "✅" if h.get("done") else "⬜"
                if h.get("mirrored"):
                    # Read from the member's own ritual; nothing to tick here.
                    buttons.append(InlineKeyboardButton(
                        f"{mark} 👥 {h['name']} 🔒",
                        callback_data="habit:mirrored"))
                elif not h.get("due", True):
                    buttons.append(InlineKeyboardButton(
                        f"⏸ 👥 {h['name']}",
                        callback_data="habit:noop"))
                elif h.get("timer_minutes") and not h.get("done"):
                    buttons.append(InlineKeyboardButton(
                        f"👥 {h['name']} · {_timer_badge(h, lang)}",
                        callback_data=f"tmr:open:H:{h['id']}"))
                else:
                    buttons.append(InlineKeyboardButton(
                        f"{mark} 👥 {h['name']}",
                        callback_data=f"thabit:toggle:{h['id']}"))
                continue
            if h.get("paused"):
                # A paused habit is shown, greyed by its label, with resume as
                # the only thing it can do. Hiding it would mean it can never
                # come back.
                buttons.append(InlineKeyboardButton(
                    f"⏸ {h['name']}",
                    callback_data=f"habit:resume:{h['id']}"))
                continue
            if not h.get("due", True):
                # Not scheduled today: listed without a checkbox, so an off-day
                # never looks like something the user skipped.
                buttons.append(InlineKeyboardButton(
                    f"·  {h['name']}",
                    callback_data="habit:noop"))
                continue
            if h.get("timer_minutes") and not h["done"]:
                buttons.append(InlineKeyboardButton(
                    f"{h['name']} · {_timer_badge(h, lang)}",
                    callback_data=f"tmr:open:h:{h['id']}"))
                continue
            mark = "✅" if h["done"] else "⬜"
            lock = " 🔒" if h["protected"] else ""
            buttons.append(InlineKeyboardButton(
                f"{mark} {h['name']}{lock}",
                callback_data=f"habit:toggle:{h['id']}"))
    # Two columns: a long name is cut short by Telegram, never wrapped into
    # a third line, so the list still reads at a glance.
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    # "Turdim" left the persistent menu, so it lives here, where the habit it
    # belongs to is — and only while it can still be recorded today.
    wake = next((h for group in grouped.values() for h in group
                 if h.get("system_key") == svc.SYSTEM_WAKEUP), None)
    if wake and not wake["done"]:
        rows.append([InlineKeyboardButton(t(lang, "menu_wake"),
                                          callback_data="habit:wake")])

    rows.append([
        InlineKeyboardButton(t(lang, "btn_add_habit"), callback_data="habit:add"),
        InlineKeyboardButton(t(lang, "btn_edit_habit"), callback_data="habit:editlist"),
    ])
    rows.append([InlineKeyboardButton(t(lang, "btn_presets"),
                                      callback_data="habit:presets")])
    return InlineKeyboardMarkup(rows)


def presets_keyboard(presets: list[dict], lang: str) -> InlineKeyboardMarkup:
    """The ready-made ten: ✅ on the list, ➕ not yet. A tap flips it."""
    rows = [[InlineKeyboardButton(
        f"{'✅' if p['added'] else '➕'} {p['name'][:40]}",
        callback_data=f"habit:preset:{p['key']}")] for p in presets]
    rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="habit:back")])
    return InlineKeyboardMarkup(rows)


async def show_presets(update: Update, ws: int, lang: str) -> None:
    with SessionLocal() as s:
        presets = svc.habit_presets(s, ws, lang)
    text = f"<b>{t(lang, 'presets_title')}</b>\n{t(lang, 'presets_hint')}"
    markup = presets_keyboard(presets, lang)
    query = update.callback_query
    if query is not None:
        try:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                          reply_markup=markup)
            return
        except BadRequest:
            pass
    if update.effective_message:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML,
                                                  reply_markup=markup)


async def show_habits(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                      edit: bool = False) -> None:
    got = await guard(update, ctx, write=False)
    if got is None:
        return
    user, ws = got
    tz = svc.user_tz(user)
    with SessionLocal() as s:
        grouped = svc.habits_by_category(s, ws, tz=tz)
        streak = svc.habit_streak(s, ws, tz=tz)

    text = f"<b>{t(user.language, 'habits_title')}</b>"
    due = [h for group in grouped.values() for h in group
           if h.get("due", True) and not h.get("paused")
           and h.get("scored", True) and h.get("owed", True)]
    if due:
        text += f"   ✅ {sum(1 for h in due if h.get('done'))}/{len(due)}"
    if streak:
        text += f"   🔥 {streak}"
    markup = habits_keyboard(grouped, user.language, stage=ui_stage(user))
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup)
    elif update.effective_message:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

#: Priority as a colour, first thing on the line, so the eye sorts the list
#: before it starts reading it.
PRIORITY_MARK = {"high": "🔴", "medium": "🟡", "low": "🟢"}


def short_date(iso: str | None, lang: str) -> str:
    """`12-avgust` — the way a person says a date, not `2026-08-12`.

    An ISO string in a chat message is four numbers the reader has to parse. The
    year is dropped: everything on this screen is inside the next week.
    """
    if not iso:
        return ""
    try:
        day = date.fromisoformat(iso)
    except ValueError:
        return iso
    month = svc.MONTHS.get(lang, svc.MONTHS["uz"])[day.month - 1]
    if lang == "en":
        return f"{month} {day.day}"
    return f"{day.day}-{month}"


def _task_lines(tasks: list[dict], lang: str, start: int = 1, *,
                hide_date: bool = False) -> list[str]:
    """Numbered rows: colour, number, title, then the details underneath.

        🟡 1. hisobotni tekshirish
             └ 25-sentabr · 20:05
    """
    lines = []
    for number, task in enumerate(tasks, start=start):
        mark = PRIORITY_MARK.get(task["priority"], "▫️")
        lines.append(f"{mark} {number}. {esc(task['title'])}")
        meta = []
        if task.get("deadline") and not hide_date:
            meta.append(short_date(task["deadline"], lang))
        if task.get("due_time"):
            meta.append(task["due_time"])
        if task.get("project"):
            meta.append(f"📁 {esc(task['project'])}")
        if task.get("recurrence"):
            meta.append("🔁")
        countdown = task.get("countdown")
        if countdown and countdown.get("days_left", -1) >= 0:
            meta.append(f"⏳ {countdown_left({'days_left': countdown['days_left']}, lang)}")
        badge = _timer_badge(task, lang)
        if badge:
            meta.append(badge)
        if meta:
            lines.append(f"     └ {' · '.join(meta)}")
    return lines


def render_tasks(data: dict, lang: str) -> str:
    """Late, today, the coming days, undated, then the team's — in that order.

        ❗ Kechikkan
        🟡 1. hisobotni tekshirish
             └ 25-sentabr · 20:05

        ⚡ Bugun:
        — yo'q

        📥 Muddatsiz
        🟡 1. namoz vaqtlarini ErnestOS ga qo'shish

        👥 Jamoa vazifalari
        Miro*
        ⬜ Speaking | 30mins · 28-sentabr

    Numbering restarts in each section, so "2" always means the second thing
    under the heading the eye is on. Projects are named on the task's own line
    rather than as headings of their own.
    """
    lines = []

    if data["overdue"]:
        lines.append(f"<b>{t(lang, 'tasks_overdue')}</b>")
        lines += _task_lines(data["overdue"], lang)
        lines.append("")

    today = data.get("today")
    if today is None:
        today = [x for x in data["upcoming"] if x.get("days_left") == 0]
    today_ids = {x["id"] for x in today}
    lines.append(f"<b>{t(lang, 'tasks_today')}</b>")
    lines += _task_lines(today, lang, hide_date=True) or [t(lang, "none")]

    upcoming = [x for x in data["upcoming"] if x["id"] not in today_ids]
    if upcoming:
        lines.append("")
        lines.append(f"<b>{t(lang, 'tasks_upcoming')}</b>")
        lines += _task_lines(upcoming, lang)

    if data["undated"]:
        lines.append("")
        lines.append(f"<b>{t(lang, 'tasks_undated')}</b>")
        lines += _task_lines(data["undated"][:8], lang)
        if len(data["undated"]) > 8:
            lines.append(f"<i>+{len(data['undated']) - 8}</i>")

    # Shared work, per team. The tick is the reader's own share: a task the
    # partner finished and you have not is still open for you — unless the
    # task was set up so that one person's tick closes it.
    team_tasks = [x for x in (data.get("team_tasks") or []) if x.get("owed", True)]
    if team_tasks:
        lines.append("")
        lines.append(f"<b>{t(lang, 'tasks_team')}</b>")
        by_team: dict[str, list[dict]] = {}
        for task in team_tasks:
            by_team.setdefault(task.get("team_name") or "", []).append(task)
        for name, rows in by_team.items():
            lines.append(esc(name))
            for task in rows[:8]:
                mark = "✅" if task.get("done") else "⬜"
                when = (f" · {short_date(task['deadline'], lang)}"
                        if task.get("deadline") else "")
                badge = _timer_badge(task, lang)
                lines.append(f"{mark} {esc(task['title'])}{when}"
                             + (f" · {badge}" if badge else ""))

    return "\n".join(lines)


def tasks_keyboard(lang: str, *, projects: list[dict],
                   open_tasks: int, editable: int,
                   team_tasks: int = 0, stage: int = 3,
                   restorable: int = 0) -> InlineKeyboardMarkup:
    """Only buttons that lead somewhere.

    A "Bajarildi" button on an empty task list opens a chooser with nothing in
    it — the user taps, gets an alert, and learns the app is lying about what
    it can do. So each control appears only when it has something to act on:
    with no data at all, the screen is Add and Projects and nothing else.
    Deleting lives inside ✏️, one confirmation away, and ♻️ brings it back.
    """
    projects_label = t(lang, "btn_projects") + (f" ({len(projects)})" if projects else "")
    rows = [[InlineKeyboardButton(t(lang, "btn_add_task"), callback_data="task:add"),
             InlineKeyboardButton(projects_label, callback_data="pj:list")]]

    action_row = []
    if open_tasks:
        action_row.append(InlineKeyboardButton(t(lang, "btn_done_task"),
                                               callback_data="task:donelist"))
    if editable:
        action_row.append(InlineKeyboardButton(t(lang, "btn_edit_task"),
                                               callback_data="task:editlist"))
    if action_row:
        rows.append(action_row)
    if restorable:
        rows.append([InlineKeyboardButton(f"{t(lang, 'btn_restore')} ({restorable})",
                                          callback_data="task:restorelist")])

    if team_tasks:
        rows.append([InlineKeyboardButton(t(lang, "btn_team_tasks"),
                                          callback_data="ttask:list")])
    # No timer or countdown buttons: both live in the Mini App now. A task
    # with a timer still opens its clock from its own row.
    return InlineKeyboardMarkup(rows)


def _all_open_tasks(s, ws: int) -> list[dict]:
    data = svc.list_tasks(s, ws, horizon_days=365)
    return data["overdue"] + data["upcoming"] + data["undated"]


async def show_tasks(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                     edit: bool = False) -> None:
    got = await guard(update, ctx, write=False)
    if got is None:
        return
    user, ws = got
    with SessionLocal() as s:
        data = svc.list_tasks(s, ws, horizon_days=7, tz=svc.user_tz(user))
        projects = svc.list_projects(s, ws)
        open_tasks = len(_all_open_tasks(s, ws))
        data["team_tasks"] = svc.team_items_for_day(
            s, user.telegram_id, tz=svc.user_tz(user))["tasks"]
        restorable = len(svc.archived_tasks(s, ws))

    text = render_tasks(data, user.language)
    markup = tasks_keyboard(user.language, projects=projects,
                            open_tasks=open_tasks,
                            editable=open_tasks + len(data["team_tasks"]),
                            team_tasks=len(data["team_tasks"]),
                            stage=ui_stage(user), restorable=restorable)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup)
    elif update.effective_message:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup)


# ---------------------------------------------------------------------------
# Timers — habits and tasks done by the clock
# ---------------------------------------------------------------------------
#
# Callback data is `tmr:<verb>:<h|t>:<id>` for an item and `tmr:<verb>:<run>`
# for a run. The kind is a single letter because Telegram allows 64 bytes of
# callback data and a habit id plus a minute count has to fit behind it.

#: Upper case is the team's copy of the same thing: `H` a shared habit, `T` a
#: shared task. The run still lives in the member's own workspace — each
#: member runs their own clock on the team's item.
KIND_OF_CODE = {"h": "habit", "t": "task", "H": "thabit", "T": "ttask"}
CODE_OF_KIND = {v: k for k, v in KIND_OF_CODE.items()}
HABIT_KINDS = ("habit", "thabit")

#: Where each kind's own tick lives, for the "tick it by hand" button.
TICK_CALLBACK = {"h": "habit:toggle:{}", "t": "task:done:{}",
                 "H": "thabit:toggle:{}", "T": "ttask:toggle:{}"}


def _timer_percent(run: dict) -> int:
    if not run.get("duration_sec"):
        return 0
    return max(0, min(100, round(run["elapsed_sec"] / run["duration_sec"] * 100)))


def render_timer(info: dict, lang: str) -> str:
    """One item's timer: how long it is, and where the clock is now."""
    lines = [t(lang, "timer_title", title=esc(info["title"]))]
    if info.get("team_name"):
        lines.append(f"👥 {esc(info['team_name'])}")
    run = info.get("run")
    counting = bool(run and run["status"] in ("running", "paused"))
    minutes = info.get("timer_minutes")
    is_habit = info["kind"] in HABIT_KINDS

    if info.get("done") and not counting:
        lines += ["", t(lang, "timer_done_today" if is_habit
                        else "timer_done_task")]
        if minutes:
            lines.append(t(lang, "timer_len", dur=fmt_minutes(minutes, lang)))
        return "\n".join(lines)
    if not minutes:
        lines += ["", t(lang, "timer_off_text")]
        return "\n".join(lines)

    length = t(lang, "timer_len", dur=fmt_minutes(minutes, lang))
    if info.get("timer_mode") == "auto":
        length += " " + t(lang, "timer_mode_auto")
    lines += ["", length]

    if counting:
        percent = _timer_percent(run)
        key = "timer_running" if run["status"] == "running" else "timer_paused"
        lines += ["", t(lang, key, left=fmt_left(run["remaining_sec"], lang)),
                  f"{_bar(percent)}  {percent}%"]
        if run["status"] == "running" and run.get("ends_at"):
            lines.append(t(lang, "timer_ends", time=run["ends_at"]))
    else:
        lines += ["", t(lang, "timer_rule_habit" if is_habit
                        else "timer_rule_task")]
    if info["kind"] in svc.TEAM_TIMER_KINDS:
        lines.append(f"<i>{t(lang, 'timer_team_own')}</i>")
    return "\n".join(lines)


def timer_keyboard(info: dict, lang: str) -> InlineKeyboardMarkup:
    """Only the buttons that make sense in the state the clock is in."""
    code, item = CODE_OF_KIND[info["kind"]], info["id"]
    run = info.get("run")
    minutes = info.get("timer_minutes")
    rows = []
    if run and run["status"] == "running":
        rows.append([InlineKeyboardButton(t(lang, "btn_timer_pause"),
                                          callback_data=f"tmr:pause:{run['id']}"),
                     InlineKeyboardButton(t(lang, "btn_timer_stop"),
                                          callback_data=f"tmr:stop:{run['id']}")])
        rows.append([InlineKeyboardButton(t(lang, "btn_timer_refresh"),
                                          callback_data=f"tmr:open:{code}:{item}")])
    elif run and run["status"] == "paused":
        rows.append([InlineKeyboardButton(t(lang, "btn_timer_resume"),
                                          callback_data=f"tmr:resume:{run['id']}"),
                     InlineKeyboardButton(t(lang, "btn_timer_stop"),
                                          callback_data=f"tmr:stop:{run['id']}")])
    else:
        # On a shared item the length is the team's setting: its creator, an
        # admin or the owner changes it. Everybody may run their own clock.
        can_set = info.get("can_set", not info.get("protected"))
        if minutes and not info.get("done"):
            rows.append([InlineKeyboardButton(
                t(lang, "btn_timer_start", dur=fmt_minutes(minutes, lang)),
                callback_data=f"tmr:go:{code}:{item}")])
        if can_set:
            rows.append([InlineKeyboardButton(
                t(lang, "btn_timer_change" if minutes else "btn_timer_set"),
                callback_data=f"tmr:dur:{code}:{item}")])
        if minutes and can_set:
            rows.append([InlineKeyboardButton(
                t(lang, "btn_timer_off"), callback_data=f"tmr:set:{code}:{item}:0")])
        if not minutes and not info.get("done") and not info.get("protected"):
            # With the timer off it is an ordinary item again, and the
            # ordinary way to finish it is one tap away.
            rows.append([InlineKeyboardButton(
                t(lang, "btn_tick"), callback_data=TICK_CALLBACK[code].format(item))])
    rows.append([InlineKeyboardButton(t(lang, "back"),
                                      callback_data=f"tmr:back:{code}")])
    return InlineKeyboardMarkup(rows)


def timer_pick_keyboard(info: dict, lang: str) -> InlineKeyboardMarkup:
    """The lengths on offer, three to a row, plus typed, from-the-name and off."""
    code, item = CODE_OF_KIND[info["kind"]], info["id"]
    presets = [InlineKeyboardButton(
        fmt_minutes(m, lang), callback_data=f"tmr:set:{code}:{item}:{m}")
        for m in info.get("presets") or svc.TIMER_PRESETS]
    rows = [presets[i:i + 3] for i in range(0, len(presets), 3)]
    rows.append([InlineKeyboardButton(t(lang, "btn_timer_custom"),
                                      callback_data=f"tmr:custom:{code}:{item}")])
    if info.get("parsed_minutes") and info.get("timer_mode") != "auto":
        rows.append([InlineKeyboardButton(
            t(lang, "btn_timer_auto", dur=fmt_minutes(info["parsed_minutes"], lang)),
            callback_data=f"tmr:set:{code}:{item}:a")])
    if info.get("timer_minutes"):
        rows.append([InlineKeyboardButton(
            t(lang, "btn_timer_off"), callback_data=f"tmr:set:{code}:{item}:0")])
    rows.append([InlineKeyboardButton(t(lang, "back"),
                                      callback_data=f"tmr:open:{code}:{item}")])
    return InlineKeyboardMarkup(rows)


async def _show(update: Update, text: str, markup, *, edit: bool):
    """Edit the message the button was on, or send a new one. Returns it."""
    query = update.callback_query
    if edit and query is not None:
        try:
            return await query.edit_message_text(
                text, parse_mode=ParseMode.HTML, reply_markup=markup)
        except BadRequest as e:
            # Refreshing a timer whose minute has not changed yet.
            if "not modified" not in str(e).lower():
                raise
            return query.message
    message = update.effective_message
    if message is None:
        return None
    return await message.reply_text(text, parse_mode=ParseMode.HTML,
                                    reply_markup=markup)


async def show_timer(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                     user: User, ws: int, kind: str, item_id: int, *,
                     edit: bool = True) -> None:
    """One item's timer screen. A running timer's message keeps counting."""
    lang = user.language
    with SessionLocal() as s:
        info = svc.timer_for(s, ws, kind, item_id, tz=svc.user_tz(user))
        if info.get("team_id"):
            team = svc.team_for(s, user.telegram_id, info["team_id"])
            info["team_name"] = team.name if team else ""
    sent = await _show(update, render_timer(info, lang),
                       timer_keyboard(info, lang), edit=edit)
    run = info.get("run")
    chat_id = getattr(getattr(sent, "chat", None), "id", None) \
        or getattr(sent, "chat_id", None)
    message_id = getattr(sent, "message_id", None)
    if run and run["status"] == "running" and chat_id and message_id:
        with SessionLocal() as s:
            svc.attach_timer_message(s, ws, run["id"], chat_id, message_id)


async def show_timer_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                          user: User, ws: int, kind: str, *,
                          edit: bool = True) -> None:
    """Every habit (or open task) and its timer, private and shared, to pick one.

    Shared items are marked 👥 with their team, and open the member's own clock
    on the team's item.
    """
    lang = user.language
    with SessionLocal() as s:
        items = svc.timer_candidates(s, ws, user.telegram_id, kind,
                                     tz=svc.user_tz(user))[:24]
    rows = []
    for row in items:
        mark = "👥 " if row["kind"] in svc.TEAM_TIMER_KINDS else ""
        rows.append([InlineKeyboardButton(
            f"{mark}{row['title'][:34]} · {_timer_badge(row, lang) or '—'}",
            callback_data=f"tmr:open:{CODE_OF_KIND[row['kind']]}:{row['id']}")])
    rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="tmr:home")])
    text = t(lang, "timer_list_habits" if kind == "habit" else "timer_list_tasks")
    if not items:
        text += "\n\n" + t(lang, "empty")
    await _show(update, text, InlineKeyboardMarkup(rows), edit=edit)


async def show_timer_home(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                          user: User, ws: int, *, edit: bool = True) -> None:
    """⏱ Time countdown: the clock that is running, and where to start one."""
    lang = user.language
    with SessionLocal() as s:
        run = svc.active_timer(s, ws)
    lines = [f"<b>{t(lang, 'btn_timers')}</b>", ""]
    rows = []
    if run:
        key = "timer_running" if run["status"] == "running" else "timer_paused"
        lines += [f"<b>{esc(run['title'])}</b>",
                  t(lang, key, left=fmt_left(run["remaining_sec"], lang)),
                  f"{_bar(_timer_percent(run))}  {_timer_percent(run)}%", ""]
        rows.append([InlineKeyboardButton(
            f"▶️ {run['title'][:30]}",
            callback_data=f"tmr:open:{CODE_OF_KIND[run['kind']]}:{run['item_id']}")])
    else:
        lines.append(t(lang, "timer_none_active"))
    lines.append(t(lang, "timer_home_hint"))
    rows.append([InlineKeyboardButton(t(lang, "timer_for_habits"), callback_data="tmr:list:h"),
                 InlineKeyboardButton(t(lang, "timer_for_tasks"), callback_data="tmr:list:t")])
    rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="home:show")])
    await _show(update, "\n".join(lines), InlineKeyboardMarkup(rows), edit=edit)


async def show_active_timer(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """`/timer`: the clock that is running now, or the list to start one."""
    got = await guard(update, ctx, write=False)
    if got is None:
        return
    user, ws = got
    with SessionLocal() as s:
        run = svc.active_timer(s, ws)
    if run:
        await show_timer(update, ctx, user, ws, run["kind"], run["item_id"],
                         edit=False)
        return
    await show_timer_home(update, ctx, user, ws, edit=False)


async def _toast(update: Update, text: str) -> None:
    """A small confirmation, if Telegram still takes one for this tap.

    Unlike `_notice` it never falls back to a message: the screen redrawn
    right after it already shows what changed.
    """
    query = update.callback_query
    if query is None:
        return
    try:
        await query.answer(text)
    except TelegramError:
        pass


async def _notice(update: Update, text: str) -> None:
    """Tell the user why a button did nothing.

    An alert when Telegram still accepts one for this tap; the router has
    usually answered the query already, and a second answer is refused, so
    the fallback is a plain message rather than silence.
    """
    query = update.callback_query
    try:
        await query.answer(text, show_alert=True)
    except TelegramError:
        if update.effective_message:
            await update.effective_message.reply_text(text)


TIMER_REFUSALS = {"already_done": "timer_already_done",
                  "paused": "timer_is_paused",
                  "timer_required": "timer_required",
                  "timer_off": "timer_off_text",
                  "forbidden": "team_only_creator",
                  "protected": "habit_protected"}


async def route_timer(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                      parts: list[str], user: User, ws: int, lang: str) -> None:
    query = update.callback_query
    sub = parts[1] if len(parts) > 1 else ""
    tz = svc.user_tz(user)

    if sub == "home":
        await show_timer_home(update, ctx, user, ws)
        return
    if sub == "list":
        await show_timer_list(update, ctx, user, ws,
                              "task" if parts[2].lower() == "t" else "habit")
        return
    if sub == "back":
        if parts[2].lower() == "t":
            await show_tasks(update, ctx, edit=True)
        else:
            await show_habits(update, ctx, edit=True)
        return

    if sub in ("pause", "resume", "stop"):
        action = {"pause": svc.pause_timer, "resume": svc.resume_timer,
                  "stop": svc.stop_timer}[sub]
        try:
            with SessionLocal() as s:
                run = action(s, ws, int(parts[2]))
        except ValueError as e:
            await _notice(update, t(lang, TIMER_REFUSALS.get(str(e), "error")))
            return
        await show_timer(update, ctx, user, ws, run["kind"], run["item_id"])
        return

    kind = KIND_OF_CODE.get(parts[2] if len(parts) > 2 else "")
    if kind is None:
        return
    item_id = int(parts[3])

    if sub == "open":
        await show_timer(update, ctx, user, ws, kind, item_id)
    elif sub == "go":
        try:
            with SessionLocal() as s:
                svc.start_timer(s, ws, kind, item_id, tz=tz)
        except ValueError as e:
            await _notice(update, t(lang, TIMER_REFUSALS.get(str(e), "error")))
        await show_timer(update, ctx, user, ws, kind, item_id)
    elif sub == "dur":
        with SessionLocal() as s:
            info = svc.timer_for(s, ws, kind, item_id, tz=tz)
        if not info.get("can_set", True):
            await _notice(update, t(lang, "team_only_creator"))
            return
        await _show(update, t(lang, "timer_pick", title=esc(info["title"])),
                    timer_pick_keyboard(info, lang), edit=True)
    elif sub == "set":
        value = None if parts[4] == "a" else int(parts[4])
        try:
            with SessionLocal() as s:
                svc.set_item_timer(s, ws, kind, item_id, value)
        except ValueError as e:
            await _notice(update, t(lang, TIMER_REFUSALS.get(str(e), "error")))
            return
        await show_timer(update, ctx, user, ws, kind, item_id)
    elif sub == "custom":
        start_flow(ctx, "timer_custom", kind=kind, item=item_id)
        await query.edit_message_text(t(lang, "timer_ask_custom"),
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=cancel_keyboard(lang))


def _typed_minutes(text: str) -> int | None:
    """A length typed by hand: `1h 30m`, `45 min`, or a bare number of minutes."""
    minutes = svc.parse_duration_minutes(text)
    if minutes is None and text.strip().isdigit():
        minutes = int(text.strip())
    if not minutes or minutes > svc.TIMER_MAX_MINUTES:
        return None
    return minutes


# ---------------------------------------------------------------------------
# Countdowns
# ---------------------------------------------------------------------------

#: The three kinds of countdown, in the order they are offered and listed.
CD_SCOPES = (("general", "g", "cd_scope_general"),
             ("task", "t", "cd_scope_task"),
             ("habit", "h", "cd_scope_habit"))
CD_SCOPE_OF = {code: scope for scope, code, _ in CD_SCOPES}
CD_ICON = {"general": "📅", "task": "⚡", "habit": "✅"}


def render_countdowns(items: list[dict], lang: str) -> str:
    """Yours by kind — general, for a task, for a habit — then each team's.

        📅 Date countdown

        📅 Umumiy
        ⏳ IELTS — 49 kun qoldi · 16-noyabr

        ⚡ Vazifa uchun
        ⏳ Hisobot → Oylik hisobot — ertaga! · 29-sentabr

        👥 Miro*
        ⏳ Demo day — 12 kun qoldi · 10-oktabr
    """
    lines = [t(lang, "cd_title"), ""]
    if not items:
        lines.append(t(lang, "cd_empty"))
        return "\n".join(lines)
    personal = [x for x in items if not x.get("team_id")]
    for scope, _code, key in CD_SCOPES:
        rows = [x for x in personal if (x.get("scope") or "general") == scope]
        if not rows:
            continue
        lines.append(f"<b>{CD_ICON[scope]} {t(lang, key)}</b>")
        lines += [countdown_line(x, lang) for x in rows]
        lines.append("")
    teams: dict[str, list[dict]] = {}
    for x in items:
        if x.get("team_id"):
            teams.setdefault(x.get("team_name") or "", []).append(x)
    for name, rows in teams.items():
        lines.append(f"<b>👥 {esc(name)}</b>")
        lines += [countdown_line(x, lang) for x in rows]
        lines.append("")
    return "\n".join(lines).rstrip()


def countdowns_keyboard(items: list[dict], lang: str) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(t(lang, "btn_cd_add"), callback_data="cd:add")]
    if items:
        row.append(InlineKeyboardButton(t(lang, "btn_cd_del"),
                                        callback_data="cd:dellist"))
    return InlineKeyboardMarkup([row, [InlineKeyboardButton(
        t(lang, "btn_timers"), callback_data="tmr:home")]])


async def show_countdowns(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *,
                          edit: bool = False) -> None:
    got = await guard(update, ctx, write=False)
    if got is None:
        return
    user, ws = got
    with SessionLocal() as s:
        items = svc.countdowns_for_user(s, ws, user.telegram_id,
                                        tz=svc.user_tz(user))
    await _show(update, render_countdowns(items, user.language),
                countdowns_keyboard(items, user.language), edit=edit)


def _cd_link_choices(s, user: User, ws: int, scope: str, dest: str) -> list[tuple[int, str, str | None]]:
    """(id, title, deadline) of what a new countdown can be tied to."""
    tz = svc.user_tz(user)
    if scope == "task":
        if dest == "p":
            data = svc.list_tasks(s, ws, horizon_days=365, tz=tz)
            rows = data["overdue"] + data["upcoming"] + data["later"] + data["undated"]
        else:
            rows = svc.list_team_tasks(s, user.telegram_id, int(dest),
                                       horizon_days=365, tz=tz)
        return [(x["id"], x["title"], x.get("deadline")) for x in rows][:12]
    if dest == "p":
        rows = [h for h in svc.list_habits(s, ws, tz=tz) if not h["protected"]]
    else:
        rows = [h for h in svc.list_team_habits(s, user.telegram_id, int(dest), tz=tz)
                if not h.get("mirrored")]
    return [(x["id"], x["name"], None) for x in rows][:12]


async def ask_countdown_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                             lang: str, flow: dict, deadline: str | None = None) -> None:
    """The last question: which date. A linked task's own deadline is one tap."""
    start_flow(ctx, "cd_date", **{k: flow.get(k) for k in ("title", "scope", "dest", "item")})
    rows = []
    if deadline:
        rows.append([InlineKeyboardButton(
            t(lang, "cd_use_deadline", day=short_date(deadline, lang)),
            callback_data=f"cdq:{deadline}")])
    rows.append([InlineKeyboardButton(t(lang, "cancel"), callback_data="flow:cancel")])
    await _show(update, t(lang, "cd_ask_date", title=esc(flow.get("title") or "")),
                InlineKeyboardMarkup(rows), edit=bool(update.callback_query))


async def save_countdown(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                         user: User, ws: int, flow: dict, target: date) -> bool:
    """Write the countdown the flow describes. False keeps the flow open."""
    lang = user.language
    tz = svc.user_tz(user)
    dest = flow.get("dest") or "p"
    message = update.effective_message
    try:
        with SessionLocal() as s:
            item = svc.add_countdown(
                s, ws, flow["title"], target, tz=tz,
                scope=flow.get("scope") or "general",
                item_id=int(flow["item"]) if flow.get("item") else None,
                team_id=None if dest == "p" else int(dest),
                user_id=user.telegram_id)
    except ValueError as e:
        reason = str(e)
        if reason in ("past_date", "too_far"):
            # A typo in the year, most likely — keep the flow open.
            await message.reply_text(t(lang, "cd_past" if reason == "past_date"
                                       else "cd_too_far"))
            return False
        ctx.user_data.pop("flow", None)
        await message.reply_text(t(lang, "cd_too_many" if reason == "too_many"
                                   else "error"))
        return True
    except (PermissionError, svc.NotFound):
        ctx.user_data.pop("flow", None)
        await message.reply_text(t(lang, "not_found"))
        return True
    ctx.user_data.pop("flow", None)
    await message.reply_text(
        t(lang, "cd_added", title=esc(item["title"]),
          left=countdown_left(item, lang)),
        parse_mode=ParseMode.HTML)
    if dest != "p":
        await notify_teammates(int(dest), user.telegram_id, "team_ev_countdown",
                               item["title"], item.get("team_name") or "")
    await show_countdowns(update, ctx)
    return True


async def route_countdown(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                          action: str, parts: list[str], user: User, ws: int,
                          lang: str) -> None:
    """cd:* the screen · cds scope · cdd where · cdl linked item · cdq the date."""
    query = update.callback_query
    sub = parts[1] if len(parts) > 1 else ""

    if action == "cds":
        # Kind chosen: next, whose — unless there is no team to choose.
        scope = CD_SCOPE_OF.get(sub, "general")
        with SessionLocal() as s:
            teams = svc.teams_for(s, user.telegram_id)
        flow = start_flow(ctx, "cd_setup", scope=scope)
        if teams:
            await _show(update, t(lang, "cd_ask_dest"),
                        dest_keyboard(lang, teams, "cdd"), edit=True)
            return
        await _cd_after_dest(update, ctx, user, ws, lang, flow, "p")
        return
    if action == "cdd":
        flow = current_flow(ctx, "cd_setup")
        if flow is None:
            await _notice(update, t(lang, "flow_expired"))
            return
        await _cd_after_dest(update, ctx, user, ws, lang, flow, sub)
        return
    if action == "cdl":
        flow = current_flow(ctx, "cd_setup")
        if flow is None:
            await _notice(update, t(lang, "flow_expired"))
            return
        item_id = int(sub)
        if not item_id:
            start_flow(ctx, "cd_title", scope=flow["scope"], dest=flow["dest"])
            await _show(update, t(lang, "cd_ask_title"), cancel_keyboard(lang), edit=True)
            return
        with SessionLocal() as s:
            choices = {c[0]: c for c in _cd_link_choices(s, user, ws, flow["scope"],
                                                          flow["dest"])}
        if item_id not in choices:
            await _notice(update, t(lang, "not_found"))
            return
        _, title, deadline = choices[item_id]
        flow.update(item=item_id, title=title[:200])
        await ask_countdown_date(update, ctx, lang, flow, deadline)
        return
    if action == "cdq":
        flow = current_flow(ctx, "cd_date")
        if flow is None:
            await _notice(update, t(lang, "flow_expired"))
            return
        await save_countdown(update, ctx, user, ws, flow, date.fromisoformat(sub))
        return

    if sub == "list":
        await show_countdowns(update, ctx)
    elif sub == "back":
        await show_countdowns(update, ctx, edit=True)
    elif sub == "add":
        rows = [[InlineKeyboardButton(f"{CD_ICON[scope]} {t(lang, key)}",
                                      callback_data=f"cds:{code}")]
                for scope, code, key in CD_SCOPES]
        rows.append([InlineKeyboardButton(t(lang, "cancel"), callback_data="cd:back")])
        await _show(update, t(lang, "cd_ask_scope"), InlineKeyboardMarkup(rows),
                    edit=True)
    elif sub == "dellist":
        with SessionLocal() as s:
            items = svc.countdowns_for_user(s, ws, user.telegram_id,
                                            tz=svc.user_tz(user))
        if not items:
            await _notice(update, t(lang, "empty"))
            return
        rows = [[InlineKeyboardButton(
            f"🗑 {'👥 ' if item.get('team_id') else ''}{item['title'][:38]}",
            callback_data=f"cd:del:{item['id']}")] for item in items]
        rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="cd:back")])
        await query.edit_message_text(t(lang, "cd_choose_delete"),
                                      reply_markup=InlineKeyboardMarkup(rows))
    elif sub == "del":
        try:
            with SessionLocal() as s:
                svc.delete_countdown(s, ws, int(parts[2]))
        except PermissionError:
            await _notice(update, t(lang, "team_only_creator"))
            return
        await show_countdowns(update, ctx, edit=True)


async def _cd_after_dest(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                         user: User, ws: int, lang: str, flow: dict,
                         dest: str) -> None:
    """Whose countdown is settled: a general one asks its name, a task or habit
    one offers the items to tie it to."""
    flow["dest"] = dest
    if flow["scope"] == "general":
        start_flow(ctx, "cd_title", scope="general", dest=dest)
        await _show(update, t(lang, "cd_ask_title"), cancel_keyboard(lang), edit=True)
        return
    with SessionLocal() as s:
        choices = _cd_link_choices(s, user, ws, flow["scope"], dest)
    rows = [[InlineKeyboardButton(title[:40], callback_data=f"cdl:{item_id}")]
            for item_id, title, _deadline in choices]
    rows.append([InlineKeyboardButton(t(lang, "cd_no_link"), callback_data="cdl:0")])
    rows.append([InlineKeyboardButton(t(lang, "cancel"), callback_data="flow:cancel")])
    await _show(update, t(lang, "cd_ask_link_task" if flow["scope"] == "task"
                          else "cd_ask_link_habit"),
                InlineKeyboardMarkup(rows), edit=True)


# ---------------------------------------------------------------------------
# Shared (team) tasks in the chat
# ---------------------------------------------------------------------------

async def show_team_tasks(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                          user: User, *, edit: bool = True) -> None:
    """Every shared task, with the reader's own tick. Tapping flips it.

    Only your share is ever written — the same rule the Mini App follows — so
    a task your partner has finished still reads ⬜ here until you finish it.
    """
    lang = user.language
    with SessionLocal() as s:
        tasks = svc.team_items_for_day(s, user.telegram_id,
                                       tz=svc.user_tz(user))["tasks"]
    rows = [[InlineKeyboardButton(
        f"{'✅' if x.get('done') else '⬜'} {x['title'][:34]}"
        f" · {(x.get('team_name') or '')[:14]}",
        callback_data=f"ttask:toggle:{x['id']}")] for x in tasks[:20]]
    rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="task:back")])
    text = t(lang, "team_tasks_pick") + "\n<i>" + t(lang, "team_mark_hint") + "</i>"
    if not tasks:
        text += "\n\n" + t(lang, "empty")
    await _show(update, text, InlineKeyboardMarkup(rows), edit=edit)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

#: Five themes, in the Mini App picker's order. Each is a complete visual
#: system — its own colour, radius, shadow depth, gradient policy, type weight
#: and motion timing — not the same screen in a different hue:
#:
#:   ocean     Ocean Glass. Frosted panels over deep blue. The default.
#:   midnight  Midnight Minimal. Calm dark, no gradient, low stimulus.
#:   aurora    Aurora Glass. Glass over indigo/violet/cyan light.
#:   bento     Pure Bento. Light, bordered blocks; fastest to read.
#:   spatial   Spatial Layered. Floating planes and long soft shadows.
#:
#: Every earlier name is mapped forward by migrations 0007 and 0008; an
#: unknown value reads as the default rather than being rejected, so no
#: account can end up with no theme at all.
THEMES = ["ocean", "midnight", "aurora", "bento", "spatial"]
DEFAULT_THEME = "ocean"

#: Product names, so the chat picker and the Mini App picker say the same
#: thing. The id is what is stored; this is only ever displayed.
THEME_NAMES = {
    "ocean": "Ocean Glass", "midnight": "Midnight Minimal",
    "aurora": "Aurora Glass", "bento": "Pure Bento",
    "spatial": "Spatial Layered",
}


def theme_of(name: str | None) -> str:
    """Read a stored theme, falling back for names that no longer exist."""
    return name if name in THEMES else DEFAULT_THEME


async def show_invite(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """`/invite`, and the 🎁 button in Settings. One screen, one link.

    Nothing here sends anything to anybody: it hands the user their link and a
    Share button that opens Telegram's own picker. Who receives an invite is
    always a person's choice, made in Telegram's UI, not something the bot does
    on their behalf.
    """
    got = await guard(update, ctx)
    if got is None:
        return
    user, _ = got
    lang = user.language
    message = update.effective_message
    if message is None:
        return

    with SessionLocal() as s:
        stats = svc.referral_stats(s, user.telegram_id)
        code = svc.get_or_create_referral_code(s, user.telegram_id)

    link = referral_link(code)
    if link is None:
        await message.reply_text(t(lang, "ref_not_configured"))
        return

    nxt = stats["level"]["next"]
    progress = (t(lang, "ref_next_level", done=stats["counts"]["qualified"],
                  target=nxt["target"]) if nxt else t(lang, "ref_max_level"))
    body = (f"<b>{t(lang, 'ref_title')}</b>\n\n{t(lang, 'ref_body')}\n\n"
            f"{t(lang, 'ref_qualified_count', n=stats['counts']['qualified'])}\n"
            f"{progress}\n\n{t(lang, 'ref_your_link')}\n{esc(link)}")

    share = ("https://t.me/share/url?url=" + quote(link, safe="")
             + "&text=" + quote(t(lang, "ref_share_text"), safe=""))
    rows = [[InlineKeyboardButton(t(lang, "ref_share"), url=share)]]
    if WEBAPP_URL:
        rows.append([InlineKeyboardButton(t(lang, "ref_open_app"),
                                          web_app=WebAppInfo(url=WEBAPP_URL))])
    await message.reply_text(body, parse_mode=ParseMode.HTML,
                             disable_web_page_preview=True,
                             reply_markup=InlineKeyboardMarkup(rows))


async def show_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                        edit: bool = False) -> None:
    got = await guard(update, ctx)
    if got is None:
        return
    user, _ = got
    lang = user.language
    text = (f"<b>{t(lang, 'settings_title')}</b>\n\n"
            f"🌐 {lang}\n👤 {user.gender or '—'}\n"
            f"🎨 {THEME_NAMES.get(theme_of(user.theme), theme_of(user.theme))}\n"
            f"🖼 {'✓' if user.photo_file_id else '—'}")
    markup = InlineKeyboardMarkup([
        # The account first: it is the one setting that is about getting in.
        [InlineKeyboardButton(t(lang, "acc_btn"), callback_data="acc:show")],
        [InlineKeyboardButton(t(lang, "btn_lang"), callback_data="set:lang"),
         InlineKeyboardButton(t(lang, "btn_modules"), callback_data="set:modules")],
        [InlineKeyboardButton(t(lang, "btn_theme"), callback_data="set:theme"),
         InlineKeyboardButton(t(lang, "btn_photo"), callback_data="set:photo")],
        [InlineKeyboardButton(t(lang, "btn_gender"), callback_data="set:gender"),
         InlineKeyboardButton(t(lang, "wake_time_btn"), callback_data="set:waketime")],
        # One row, at the bottom, where it is findable without competing with
        # the settings somebody actually opened this screen to change.
        [InlineKeyboardButton(t(lang, "ref_menu"), callback_data="ref:show")],
        # Suggestions live here now rather than on the main keyboard.
        [InlineKeyboardButton(t(lang, "menu_feedback"), callback_data="set:feedback")],
    ])
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup)
    elif update.effective_message:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup)


# ---------------------------------------------------------------------------
# Account — login, password, and the other Telegrams signed in to it
# ---------------------------------------------------------------------------

#: What `accounts` refuses a login or a password for, and how that is said.
ACCOUNT_ERRORS = {"login_bad": "acc_login_bad", "login_taken": "acc_login_taken",
                  "password_short": "acc_password_short",
                  "password_bad": "acc_password_bad"}


async def show_account(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *,
                       edit: bool = False) -> None:
    got = await guard(update, ctx, write=False)
    if got is None:
        return
    user, _ = got
    lang = user.language
    here = update.effective_user.id
    with SessionLocal() as s:
        login, issued = accounts.ensure_credentials(s, user.telegram_id)
        others = [row for row in accounts.linked_telegrams(s, user.telegram_id)
                  if row.telegram_id != here]
        linked_here = accounts.is_linked(s, here)

    lines = [t(lang, "acc_title"), "", t(lang, "acc_login_line", login=esc(login))]
    if issued:
        lines.append(t(lang, "acc_new_password", password=esc(issued)))
    if linked_here:
        lines.append(t(lang, "acc_linked_here"))
    if others:
        lines.append(t(lang, "acc_devices_line", n=len(others)))
    rows = [[InlineKeyboardButton(t(lang, "acc_btn_login"), callback_data="acc:login"),
             InlineKeyboardButton(t(lang, "acc_btn_pass"), callback_data="acc:pass")],
            [InlineKeyboardButton(t(lang, "acc_btn_newpass"), callback_data="acc:newpass")]]
    if others:
        rows.append([InlineKeyboardButton(f"{t(lang, 'acc_btn_devices')} ({len(others)})",
                                          callback_data="acc:devices")])
    rows.append([InlineKeyboardButton(t(lang, "acc_btn_signin"), callback_data="acc:signin")])
    if linked_here:
        rows.append([InlineKeyboardButton(t(lang, "acc_btn_logout"),
                                          callback_data="acc:logout")])
    rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="set:back")])
    await _show(update, "\n".join(lines), InlineKeyboardMarkup(rows), edit=edit)


async def ask_login(update: Update, ctx: ContextTypes.DEFAULT_TYPE, lang: str, *,
                    edit: bool = False) -> None:
    """Open the two-message sign-in: login, then password."""
    start_flow(ctx, "login_user")
    await _show(update, t(lang, "acc_ask_login"), cancel_keyboard(lang), edit=edit)


async def cmd_login(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = account_of(update)
    if uid is None or update.effective_message is None:
        return
    with SessionLocal() as s:
        user = s.get(User, uid)
        lang = user.language if user else "uz"
    await ask_login(update, ctx, lang)


async def cmd_logout(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user, message = update.effective_user, update.effective_message
    if tg_user is None or message is None:
        return
    with SessionLocal() as s:
        was_linked = accounts.sign_out(s, tg_user.id)
        user = s.get(User, tg_user.id)
        lang = user.language if user else "uz"
    await message.reply_text(t(lang, "acc_logout_ok" if was_linked else "acc_logout_none"))
    if was_linked:
        await start(update, ctx)


async def handle_login_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                            flow: dict, text: str, lang: str) -> None:
    """The two typed answers of signing in."""
    message = update.effective_message
    tg_user = update.effective_user
    if flow["name"] == "login_user":
        start_flow(ctx, "login_pass", login=text.strip()[:64])
        await message.reply_text(t(lang, "acc_ask_password"),
                                 reply_markup=cancel_keyboard(lang))
        return

    # The password should not sit in the chat. Deleting somebody's own
    # message in a private chat is allowed; if it fails, nothing is lost.
    try:
        await message.delete()
    except Exception:
        pass
    ctx.user_data.pop("flow", None)
    with SessionLocal() as s:
        outcome, account_id = accounts.sign_in(
            s, tg_user.id, flow.get("login", ""), text,
            first_name=tg_user.first_name or "", username=tg_user.username or "")
        account = (s.get(User, account_id)
                   if outcome in ("ok", "self") and account_id else None)
        onboarded = bool(account and account.onboarded)
        lang = account.language if account else lang
        name = (account.first_name if account else "") or ""
        snapshot = account

    if outcome == "bad":
        await message.reply_text(t(lang, "acc_signin_bad"), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(t(lang, "acc_have"), callback_data="acc:have")],
            [InlineKeyboardButton(t(lang, "cancel"), callback_data="flow:cancel")]]))
        return
    if outcome == "locked":
        await message.reply_text(t(lang, "acc_signin_locked", n=account_id))
        return

    ctx.user_data.pop("setup", None)
    key = "acc_signin_self" if outcome == "self" else "acc_signin_ok"
    await message.reply_text(t(lang, key, name=esc(name)), parse_mode=ParseMode.HTML,
                             reply_markup=menu_for(account_id) if onboarded else None)
    if outcome == "ok" and snapshot is not None:
        await log_event(ctx.bot, snapshot, "🔐 SIGNED IN FROM ANOTHER TELEGRAM",
                        f"Telegram: <code>{tg_user.id}</code>")
    if onboarded:
        await show_home(update, ctx)
    else:
        await start(update, ctx)


async def route_account(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                        parts: list[str]) -> None:
    """acc:* — everything on the account screen, and signing in and out."""
    query = update.callback_query
    what = parts[1] if len(parts) > 1 else ""
    uid = account_of(update)
    here = update.effective_user.id
    with SessionLocal() as s:
        user = s.get(User, uid)
        lang = user.language if user else "uz"
        onboarded = bool(user and user.onboarded)

    if what == "hide":
        # "I saved it" — the message holding the password goes.
        try:
            await query.message.delete()
        except Exception:
            await query.edit_message_text(t(lang, "saved"))
        return

    if what == "new":
        # Setup: a fresh account, handed its login right here. A stale button
        # tapped on an account that is already set up only opens it.
        await query.edit_message_reply_markup(reply_markup=None)
        if onboarded:
            await show_home(update, ctx)
            return
        await issue_credentials(update.effective_message, uid, lang)
        await advance_setup(update, ctx, "name")
        return

    if what in ("have", "signin"):
        await ask_login(update, ctx, lang, edit=True)
        return

    if what == "logout":
        with SessionLocal() as s:
            was_linked = accounts.sign_out(s, here)
            own = s.get(User, here)
            own_lang = own.language if own else lang
        await query.edit_message_text(
            t(own_lang, "acc_logout_ok" if was_linked else "acc_logout_none"))
        if was_linked:
            await start(update, ctx)
        return

    if not onboarded:
        await start(update, ctx)
        return

    if what == "show":
        await show_account(update, ctx, edit=True)
    elif what == "login":
        start_flow(ctx, "acc_login")
        await _show(update, t(lang, "acc_ask_new_login"), cancel_keyboard(lang), edit=True)
    elif what == "pass":
        start_flow(ctx, "acc_pass")
        await _show(update, t(lang, "acc_ask_new_password"), cancel_keyboard(lang), edit=True)
    elif what == "newpass":
        with SessionLocal() as s:
            password, removed = accounts.set_password(
                s, uid, None, keep=here if here != uid else None)
        body = t(lang, "acc_new_password", password=esc(password))
        if removed:
            body += "\n" + t(lang, "acc_signed_out_others", n=removed)
        await _show(update, body, InlineKeyboardMarkup([[InlineKeyboardButton(
            t(lang, "acc_saved_btn"), callback_data="acc:hide")]]), edit=True)
    elif what == "devices":
        with SessionLocal() as s:
            others = [(row.telegram_id, row.first_name, row.username)
                      for row in accounts.linked_telegrams(s, uid) if row.telegram_id != here]
        if not others:
            await _show(update, t(lang, "acc_devices_none"), InlineKeyboardMarkup([[
                InlineKeyboardButton(t(lang, "back"), callback_data="acc:show")]]), edit=True)
            return
        rows = [[InlineKeyboardButton(
            f"❌ {(first or '').strip() or tid}" + (f" @{username}" if username else ""),
            callback_data=f"acc:unlink:{tid}")] for tid, first, username in others[:20]]
        rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="acc:show")])
        await _show(update, t(lang, "acc_devices_title"), InlineKeyboardMarkup(rows), edit=True)
    elif what == "unlink" and len(parts) > 2:
        with SessionLocal() as s:
            accounts.remove_link(s, uid, int(parts[2]))
        await _toast(update, t(lang, "acc_device_removed"))
        await show_account(update, ctx, edit=True)


# ---------------------------------------------------------------------------
# Multi-step flows
#
# `ctx.user_data["flow"]` holds only the in-progress step. Losing it on restart
# costs the user one retyped message; nothing durable depends on it.
# ---------------------------------------------------------------------------

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    uid = account_of(update)
    if message is None or uid is None or not message.text:
        return
    text = message.text.strip()

    with SessionLocal() as s:
        user = s.get(User, uid)
        lang = user.language if user else "uz"
        onboarded = bool(user and user.onboarded)
        step = user.onboarding_step if user else "language"

    # Signing in is typed, and can happen before this Telegram has an
    # account of its own — so it is answered ahead of setup. A menu button
    # tapped half-way is leaving it, never a login or a password.
    login_flow = current_flow(ctx, "login_user", "login_pass")
    if login_flow is not None:
        if menu_route(text) is None:
            await handle_login_text(update, ctx, login_flow, text, lang)
            return
        ctx.user_data.pop("flow", None)

    if user is None:
        await start(update, ctx)
        return

    if not onboarded:
        # Setup is mostly typed now — a name, a goal, three tasks, some habits
        # — so a typed message during it is an answer, not noise. The steps
        # that take no typing re-prompt instead of swallowing the text, so
        # somebody who types "salom" at the language screen is never left
        # staring at nothing.
        await handle_setup_answer(update, ctx, step, text)
        return

    # Main menu first — match against every language so a language change
    # mid-session never strands the user with a dead keyboard. A menu tap in
    # the middle of a flow is the user leaving it: "📋 Vazifalar" typed while
    # the bot waits for a task title must open Tasks, not become a task
    # called "📋 Vazifalar".
    route = menu_route(text)
    if route is not None:
        ctx.user_data.pop("flow", None)
        await route(update, ctx, lang)
        return

    flow = current_flow(ctx)
    if flow:
        await handle_flow(update, ctx, flow, text)
        return

    # Anything else is something to remember: offered as a task, one tap away.
    await offer_capture(update, ctx, text)


async def _menu_feedback(update: Update, ctx: ContextTypes.DEFAULT_TYPE, lang: str) -> None:
    start_flow(ctx, "feedback")
    await update.effective_message.reply_text(t(lang, "ask_feedback"),
                                              reply_markup=cancel_keyboard(lang))


async def _menu_app(update: Update, ctx: ContextTypes.DEFAULT_TYPE, lang: str) -> None:
    markup = webapp_button(lang)
    if markup:
        await update.effective_message.reply_text(t(lang, "open_app"), reply_markup=markup)


MENU_ROUTES = {
    "menu_wake": lambda u, c, _l: handle_wakeup(u, c),
    "menu_home": lambda u, c, _l: show_home(u, c),
    "menu_habits": lambda u, c, _l: show_habits(u, c),
    "menu_tasks": lambda u, c, _l: show_tasks(u, c),
    "menu_stats": lambda u, c, _l: show_stats(u, c),
    "menu_settings": lambda u, c, _l: show_settings(u, c),
    "menu_teams": lambda u, c, _l: show_teams(u, c),
    "menu_money": lambda u, c, _l: show_money(u, c),
    "menu_feedback": _menu_feedback,
    "menu_app": _menu_app,
}


def menu_route(text: str):
    """The screen a main-menu button opens, in any language, or None."""
    for key, route in MENU_ROUTES.items():
        if any(text == t(code, key) for code in ("uz", "en", "ru")):
            return route
    return None


def start_flow(ctx: ContextTypes.DEFAULT_TYPE, name: str, **data) -> dict:
    """Open a multi-step flow, replacing any half-finished one.

    Each flow carries an id and an expiry so a callback from an abandoned or
    superseded flow can be recognised and ignored (audit 034).
    """
    flow = {"name": name, "id": uuid.uuid4().hex[:8],
            "expires": time.time() + FLOW_TTL, **data}
    ctx.user_data["flow"] = flow
    return flow


def current_flow(ctx: ContextTypes.DEFAULT_TYPE, *names: str) -> dict | None:
    """The open flow, if it is one of `names` and has not expired."""
    flow = ctx.user_data.get("flow")
    if not flow:
        return None
    if flow.get("expires", 0) < time.time():
        ctx.user_data.pop("flow", None)
        return None
    if names and flow.get("name") not in names:
        return None
    return flow


#: A half-finished flow is forgotten after this long.
FLOW_TTL = int(os.environ.get("FLOW_TTL_SECONDS", "900"))


async def handle_flow(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                      flow: dict, text: str) -> None:
    got = await guard(update, ctx)
    if got is None:
        return
    user, ws = got
    lang = user.language
    message = update.effective_message
    name = flow["name"]

    try:
        if name == "habit_name":
            # Yours, or one of your teams'? Asked only when there is a team.
            with SessionLocal() as s:
                teams = svc.teams_for(s, user.telegram_id)
            if teams:
                start_flow(ctx, "habit_dest", title=text[:120])
                await message.reply_text(t(lang, "ask_dest_habit", title=esc(text[:120])),
                                         parse_mode=ParseMode.HTML,
                                         reply_markup=dest_keyboard(lang, teams, "hdest"))
                return
            start_flow(ctx, "habit_cat", title=text[:120], dest="p")
            await message.reply_text(t(lang, "ask_habit_cat"),
                                     reply_markup=category_keyboard(lang))

        elif name == "habit_rename":
            # Held in the draft until 💾 — the edit screen comes back with it.
            kind = flow.get("kind") or "p"
            draft_set(ctx, _draft_key("h", kind, int(flow["target_id"])),
                      name=text.strip()[:120])
            ctx.user_data.pop("flow", None)
            await show_habit_edit(update, ctx, user, ws, kind,
                                  int(flow["target_id"]), edit=False)

        elif name == "habit_remind":
            value = _parse_hhmm(text)
            if value is None:
                await message.reply_text(t(lang, "bad_time"))
                return
            kind = flow.get("kind") or "p"
            draft_set(ctx, _draft_key("h", kind, int(flow["target_id"])),
                      remind_at=value.strftime("%H:%M"))
            ctx.user_data.pop("flow", None)
            await show_habit_edit(update, ctx, user, ws, kind,
                                  int(flow["target_id"]), edit=False)

        elif name == "money_entry":
            kind = flow.get("kind") if flow.get("kind") in svc.MONEY_KINDS else "expense"
            amount = svc.parse_money_amount(text)
            if amount is None:
                await message.reply_text(t(lang, "money_no_amount"),
                                         reply_markup=cancel_keyboard(lang))
                return
            ctx.user_data.pop("flow", None)
            with SessionLocal() as s:
                svc.add_money(s, ws, kind, amount,
                              svc.detect_money_category(text, kind),
                              note=text.strip()[:200], source="bot",
                              tz=svc.user_tz(user))
            await message.reply_text(t(lang, "money_saved",
                                       amount=fmt_money(amount, lang)))
            await show_money(update, ctx)

        elif name == "money_limit":
            category = flow.get("category") or ""
            amount = 0 if text.strip() in ("0", "-", "—") else svc.parse_money_amount(text)
            if amount is None or svc.money_category_kind(category) != "expense":
                await message.reply_text(t(lang, "money_no_amount"),
                                         reply_markup=cancel_keyboard(lang))
                return
            ctx.user_data.pop("flow", None)
            with SessionLocal() as s:
                svc.set_money_budget(s, ws, category, amount)
            await message.reply_text(t(lang, "saved"))
            await show_money(update, ctx)

        elif name == "task_edit_time":
            value = _parse_hhmm(text)
            if value is None:
                await message.reply_text(t(lang, "bad_time"))
                return
            kind = flow.get("kind") or "p"
            draft_set(ctx, _draft_key("t", kind, int(flow["target_id"])),
                      due_time=value.strftime("%H:%M"))
            ctx.user_data.pop("flow", None)
            await show_task_edit(update, ctx, user, ws, kind,
                                 int(flow["target_id"]), edit=False)

        elif name == "acc_login":
            try:
                with SessionLocal() as s:
                    login = accounts.set_login(s, user.telegram_id, text)
            except ValueError as e:
                await message.reply_text(t(lang, ACCOUNT_ERRORS.get(str(e), "error")),
                                         reply_markup=cancel_keyboard(lang))
                return
            ctx.user_data.pop("flow", None)
            await message.reply_text(t(lang, "acc_login_set", login=esc(login)),
                                     parse_mode=ParseMode.HTML)
            await show_account(update, ctx)

        elif name == "acc_pass":
            try:
                await message.delete()
            except Exception:
                pass
            here = update.effective_user.id
            try:
                with SessionLocal() as s:
                    _, removed = accounts.set_password(
                        s, user.telegram_id, text,
                        keep=here if here != user.telegram_id else None)
            except ValueError as e:
                await message.reply_text(t(lang, ACCOUNT_ERRORS.get(str(e), "error")),
                                         reply_markup=cancel_keyboard(lang))
                return
            ctx.user_data.pop("flow", None)
            body = t(lang, "acc_password_set")
            if removed:
                body += "\n" + t(lang, "acc_signed_out_others", n=removed)
            await message.reply_text(body)
            await show_account(update, ctx)

        elif name == "task_edit_date":
            target = svc.parse_countdown_date(text, svc.today_local(svc.user_tz(user)))
            if target is None:
                await message.reply_text(t(lang, "cd_bad_date"), parse_mode=ParseMode.HTML)
                return
            kind = flow.get("kind") or "p"
            draft_set(ctx, _draft_key("t", kind, int(flow["target_id"])),
                      deadline=target.isoformat())
            ctx.user_data.pop("flow", None)
            await show_task_edit(update, ctx, user, ws, kind,
                                 int(flow["target_id"]), edit=False)

        elif name == "wake_time":
            try:
                hour, minute = (int(x) for x in text.replace(".", ":").split(":"))
                value = dtime(hour, minute)
            except (ValueError, TypeError):
                await message.reply_text(t(lang, "bad_time"))
                return
            with SessionLocal() as s:
                svc.set_wake_time(s, ws, value)
            ctx.user_data.pop("flow", None)
            await message.reply_text(
                t(lang, "wake_time_set", time=value.strftime("%H:%M")),
                reply_markup=menu_for(user.telegram_id))

        elif name == "task_edit":
            kind = flow.get("kind") or "p"
            draft_set(ctx, _draft_key("t", kind, int(flow["target_id"])),
                      title=text.strip()[:300])
            ctx.user_data.pop("flow", None)
            await show_task_edit(update, ctx, user, ws, kind,
                                 int(flow["target_id"]), edit=False)

        elif name == "task_title":
            if flow.get("project_id"):
                # Added from inside a project: where it goes is already known.
                start_flow(ctx, "task_days", title=text[:300], dest=flow.get("dest") or "p",
                           project_id=flow["project_id"])
                await message.reply_text(t(lang, "ask_task_days"),
                                         reply_markup=days_keyboard(lang))
                return
            # Yours, or one of your teams'? Asked only when there is a team.
            with SessionLocal() as s:
                teams = svc.teams_for(s, user.telegram_id)
            if teams:
                start_flow(ctx, "task_dest", title=text[:300])
                await message.reply_text(t(lang, "ask_dest_task", title=esc(text[:300])),
                                         parse_mode=ParseMode.HTML,
                                         reply_markup=dest_keyboard(lang, teams, "tdest"))
                return
            start_flow(ctx, "task_days", title=text[:300], dest="p")
            await message.reply_text(t(lang, "ask_task_days"),
                                     reply_markup=days_keyboard(lang))

        elif name == "team_name":
            try:
                with SessionLocal() as s:
                    team = svc.create_team(s, user.telegram_id, text)
                    link = svc.team_invite_link(team, BOT_USERNAME)
                    team_name = team.name
            except ValueError as e:
                key = ("team_name_empty" if str(e) == "empty_name"
                       else "team_too_many")
                await message.reply_text(t(lang, key))
                return
            ctx.user_data.pop("flow", None)
            body = t(lang, "team_created", name=esc(team_name))
            if link:
                body += f"\n\n{link}"
            await message.reply_text(body, parse_mode=ParseMode.HTML,
                                     disable_web_page_preview=True)

        elif name == "team_rename":
            try:
                with SessionLocal() as s:
                    team = svc.rename_team(s, user.telegram_id,
                                           int(flow["team_id"]), text)
                    new_name = team.name
            except ValueError:
                await message.reply_text(t(lang, "team_name_empty"))
                return
            except PermissionError:
                await message.reply_text(t(lang, "error"))
                ctx.user_data.pop("flow", None)
                return
            ctx.user_data.pop("flow", None)
            await message.reply_text(t(lang, "team_renamed", name=esc(new_name)),
                                     parse_mode=ParseMode.HTML)

        elif name == "task_custom_days":
            try:
                days = int(text)
                if not 0 <= days <= 3650:
                    raise ValueError
            except ValueError:
                await message.reply_text(t(lang, "ask_custom_days"))
                return
            # The user's own calendar, not the project default: "in 3 days"
            # typed at 23:00 in London meant three days from Tashkent's
            # tomorrow, so the task landed a day early.
            deadline = svc.today_local(svc.user_tz(user)) + timedelta(days=days)
            await ask_task_project(update, ctx, flow["title"], deadline,
                                   flow.get("dest") or "p",
                                   project_id=flow.get("project_id"))

        elif name == "project_add":
            title = text.strip()[:200]
            # Yours, or one of your teams'? Asked only when there is a team.
            with SessionLocal() as s:
                teams = svc.teams_for(s, user.telegram_id)
            if teams:
                start_flow(ctx, "project_dest", title=title)
                await message.reply_text(t(lang, "ask_dest_project", title=esc(title)),
                                         parse_mode=ParseMode.HTML,
                                         reply_markup=dest_keyboard(lang, teams, "pjdest"))
                return
            await create_project(update, ctx, user, ws, title, "p")

        elif name in ("project_rename", "project_desc", "project_deadline"):
            kind = flow.get("kind") or "p"
            project_id = int(flow["target_id"])
            if name == "project_rename":
                fields = {"name": text}
            elif name == "project_desc":
                fields = {"description": "" if text.strip() == "-" else text}
            else:
                target = svc.parse_countdown_date(text, svc.today_local(svc.user_tz(user)))
                if target is None:
                    await message.reply_text(t(lang, "cd_bad_date"), parse_mode=ParseMode.HTML)
                    return
                fields = {"deadline": target}
            with SessionLocal() as s:
                name_after = _update_project(s, user, ws, kind, project_id, **fields)
            ctx.user_data.pop("flow", None)
            await message.reply_text(t(lang, "project_updated", name=esc(name_after)),
                                     parse_mode=ParseMode.HTML)
            await show_project(update, ctx, project_id, kind, edit=False)

        elif name == "timer_custom":
            minutes = _typed_minutes(text)
            if minutes is None:
                await message.reply_text(t(lang, "timer_bad_custom"),
                                         parse_mode=ParseMode.HTML)
                return
            with SessionLocal() as s:
                svc.set_item_timer(s, ws, flow["kind"], int(flow["item"]), minutes)
            ctx.user_data.pop("flow", None)
            await show_timer(update, ctx, user, ws, flow["kind"],
                             int(flow["item"]), edit=False)

        elif name == "cd_title":
            await ask_countdown_date(update, ctx, lang,
                                     {**flow, "title": text[:200]})

        elif name == "cd_date":
            tz = svc.user_tz(user)
            target = svc.parse_countdown_date(text, svc.today_local(tz))
            if target is None:
                await message.reply_text(t(lang, "cd_bad_date"),
                                         parse_mode=ParseMode.HTML)
                return
            await save_countdown(update, ctx, user, ws, flow, target)

        elif name == "feedback":
            with SessionLocal() as s:
                row = svc.save_feedback(s, ws, user.telegram_id, text)
                feedback_id = row.id
            ctx.user_data.pop("flow", None)

            delivered = False
            if FEEDBACK_CHANNEL_ID:
                try:
                    await ctx.bot.send_message(
                        chat_id=FEEDBACK_CHANNEL_ID,
                        text=(f"<b>💬 ERNESTOS FEEDBACK</b>\n{_who(user)}\n"
                              f"Date: {datetime.now(svc.TZ):%Y-%m-%d %H:%M}\n\n"
                              f"{esc(text)}"),
                        parse_mode=ParseMode.HTML)
                    delivered = True
                except TelegramError as e:
                    log.warning("feedback delivery failed: %s", e)

            if delivered:
                with SessionLocal() as s:
                    svc.mark_feedback_delivered(s, feedback_id)
                await message.reply_text(t(lang, "feedback_sent"))
            else:
                # Never claim delivery that did not happen.
                await message.reply_text(t(lang, "feedback_saved"))

    except PermissionError:
        ctx.user_data.pop("flow", None)
        await message.reply_text(t(lang, "team_only_creator"))
    except ValueError as e:
        ctx.user_data.pop("flow", None)
        await message.reply_text(t(lang, "habit_protected" if str(e) == "protected"
                                   else "error"))
    except svc.NotFound:
        ctx.user_data.pop("flow", None)
        await message.reply_text(t(lang, "not_found"))


async def ask_task_project(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                           title: str, deadline: date | None,
                           dest: str = "p", *, project_id: int | None = None) -> None:
    """Which project, if any — the team's own shelves for a shared task.

    With no project to choose from there is no question: the task is made.
    Neither is there when it is being added from inside a project.
    """
    got = await guard(update, ctx)
    if got is None:
        return
    user, ws = got
    lang = user.language
    if project_id:
        start_flow(ctx, "task_project", title=title,
                   deadline=deadline.isoformat() if deadline else "", dest=dest)
        await create_task_from_flow(update, ctx, user, ws, int(project_id))
        return
    with SessionLocal() as s:
        if dest == "p":
            projects = svc.list_projects(s, ws)
        else:
            projects = svc.list_team_projects(s, user.telegram_id, int(dest))

    start_flow(ctx, "task_project", title=title,
               deadline=deadline.isoformat() if deadline else "", dest=dest)
    if not projects:
        await create_task_from_flow(update, ctx, user, ws, 0)
        return
    rows = [[InlineKeyboardButton(t(lang, "standalone"), callback_data="taskproj:0")]]
    for p in projects[:10]:
        rows.append([InlineKeyboardButton(f"📁 {p['name']}",
                                          callback_data=f"taskproj:{p['id']}")])
    rows.append([InlineKeyboardButton(t(lang, "cancel"), callback_data="flow:cancel")])

    message = update.effective_message
    if message:
        await message.reply_text(t(lang, "ask_task_project"),
                                 reply_markup=InlineKeyboardMarkup(rows))


async def create_task_from_flow(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                                user: User, ws: int, project_id: int) -> None:
    """The last step of adding a task in the chat: write it where it was meant to go."""
    flow = current_flow(ctx, "task_project") or {}
    title, deadline = flow.get("title"), flow.get("deadline") or None
    dest = flow.get("dest") or "p"
    lang = user.language
    message = update.effective_message
    if not title:
        if update.callback_query:
            await _notice(update, t(lang, "error"))
        return
    due = date.fromisoformat(deadline) if deadline else None
    ctx.user_data.pop("flow", None)
    with SessionLocal() as s:
        if dest == "p":
            # The chat flow asks for a title, a date and a project and stops
            # there, so a task made here gets the standard reminder; the Mini
            # App's task sheet is where it can be changed or turned off.
            task = svc.add_task(s, ws, title, deadline=due,
                                project_id=project_id or None,
                                remind_before=svc.DEFAULT_REMIND_BEFORE if due else None)
            timer = svc.timer_minutes_for(task.timer_minutes, task.title)
            team_name = None
        else:
            row = svc.add_team_task(s, user.telegram_id, int(dest), title,
                                    deadline=due, project_id=project_id or None,
                                    remind_before=svc.DEFAULT_REMIND_BEFORE if due else None)
            timer = row.get("timer_minutes")
            team = svc.team_for(s, user.telegram_id, int(dest))
            team_name = team.name if team else ""
    text = (t(lang, "task_added", title=title) if team_name is None
            else t(lang, "task_added_team", title=esc(title), team=esc(team_name)))
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML)
        except BadRequest:
            await message.reply_text(text, parse_mode=ParseMode.HTML)
    elif message:
        await message.reply_text(text, parse_mode=ParseMode.HTML)
    if timer and message:
        await message.reply_text(t(lang, "task_added_timer",
                                   dur=fmt_minutes(timer, lang)),
                                 parse_mode=ParseMode.HTML)
    if team_name is not None:
        await notify_teammates(int(dest), user.telegram_id, "team_ev_task_add",
                               title, team_name)
    await log_event(ctx.bot, user, "⚡ TASK ADDED",
                    f"Task: {esc(title)}\nDeadline: {deadline or '—'}")
    await count_action(user.telegram_id, ctx, message, lang)
    await show_tasks(update, ctx)


# ---------------------------------------------------------------------------
# Where a new item goes — your own list or one of your teams
# ---------------------------------------------------------------------------

def dest_keyboard(lang: str, teams: list, prefix: str) -> InlineKeyboardMarkup:
    """👤 Shaxsiy, then one button per team, then cancel."""
    rows = [[InlineKeyboardButton(t(lang, "dest_personal"),
                                  callback_data=f"{prefix}:p")]]
    for team in teams[:svc.MAX_TEAMS_PER_USER]:
        rows.append([InlineKeyboardButton(f"👥 {team.name[:30]}",
                                          callback_data=f"{prefix}:{team.id}")])
    rows.append([InlineKeyboardButton(t(lang, "cancel"), callback_data="flow:cancel")])
    return InlineKeyboardMarkup(rows)


def days_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "days_today"), callback_data="taskday:0"),
         InlineKeyboardButton(t(lang, "days_1"), callback_data="taskday:1")],
        [InlineKeyboardButton(t(lang, "days_2"), callback_data="taskday:2"),
         InlineKeyboardButton(t(lang, "days_3"), callback_data="taskday:3")],
        [InlineKeyboardButton(t(lang, "days_7"), callback_data="taskday:7"),
         InlineKeyboardButton(t(lang, "days_none"), callback_data="taskday:none")],
        [InlineKeyboardButton(t(lang, "days_custom"), callback_data="taskday:custom")],
        [InlineKeyboardButton(t(lang, "cancel"), callback_data="flow:cancel")],
    ])


def category_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "cat_non_negotiable"),
                              callback_data="habitcat:non_negotiable")],
        [InlineKeyboardButton(t(lang, "cat_target"), callback_data="habitcat:target")],
        [InlineKeyboardButton(t(lang, "cat_bonus"), callback_data="habitcat:bonus")],
        [InlineKeyboardButton(t(lang, "cancel"), callback_data="flow:cancel")],
    ])


def _dest_label(lang: str, dest: str, teams: dict[int, str]) -> str:
    if dest == "p":
        return t(lang, "dest_personal")
    return f"👥 {teams.get(int(dest), '')}"


# ---------------------------------------------------------------------------
# Editing a task or a habit from the chat — private and shared alike
# ---------------------------------------------------------------------------
#
# Callback data carries the kind in one letter — `p` a private item, `t` a
# team item — and the id, so every edit button fits in Telegram's 64 bytes.

# --- The draft: edits wait for 💾 Save or ✖️ Cancel ---------------------------
#
# A change made on the edit screen is held here, per Telegram, until the
# person saves it or throws it away. One draft at a time: opening another
# item's editor starts a new one, and an untouched draft is simply forgotten.
# Deleting, pausing, moving and the timer are actions rather than fields, so
# they happen at once (deleting after a confirmation).

def _draft_key(what: str, kind: str, item_id: int) -> str:
    return f"{what}{kind}:{item_id}"


def draft_changes(ctx: ContextTypes.DEFAULT_TYPE, key: str) -> dict:
    draft = ctx.user_data.get("draft")
    if not draft or draft.get("key") != key:
        return {}
    return draft.get("changes") or {}


def draft_set(ctx: ContextTypes.DEFAULT_TYPE, key: str, **changes) -> None:
    draft = ctx.user_data.get("draft")
    if not draft or draft.get("key") != key:
        draft = {"key": key, "changes": {}}
        ctx.user_data["draft"] = draft
    draft["changes"].update(changes)


def draft_drop(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data.pop("draft", None)


def _remind_label(minutes: int | None, lang: str) -> str:
    if minutes is None:
        return t(lang, "remind_off")
    if minutes == 0:
        return t(lang, "remind_at_time")
    if minutes == 60:
        return t(lang, "remind_hour")
    if minutes == 1440:
        return t(lang, "remind_day")
    return t(lang, "remind_min", n=minutes)


#: The reminder choices a task offers, in minutes before it is due.
TASK_REMIND_CHOICES = (0, 10, 30, 60, 1440)
#: The hours a habit reminder is offered at before somebody types their own.
HABIT_REMIND_CHOICES = ("06:00", "08:00", "12:00", "18:00", "21:00")


def _save_cancel_rows(lang: str, prefix: str, code: str) -> list:
    return [[InlineKeyboardButton(t(lang, "edit_save"), callback_data=f"{prefix}:{code}:sv"),
             InlineKeyboardButton(t(lang, "edit_cancel"), callback_data=f"{prefix}:{code}:cx")]]


def _task_edit_view(s, user: User, ws: int, kind: str, item_id: int,
                    draft: dict | None = None) -> tuple[str, InlineKeyboardMarkup] | None:
    """The edit menu for one task: what it is now — with any unsaved change
    shown in place — and what can be changed."""
    lang = user.language
    tz = svc.user_tz(user)
    teams = svc.teams_for(s, user.telegram_id)
    draft = draft or {}
    if kind == "p":
        task = s.get(db.Task, item_id)
        if task is None or task.workspace_id != ws or task.archived_at is not None:
            return None
        row = svc._task_dict(s, ws, task, svc.today_local(tz))
        where = t(lang, "dest_personal")
        can_manage = True
    else:
        row = svc.team_task_for(s, user.telegram_id, item_id)
        if row is None:
            return None
        where = f"👥 {esc(row['team_name'])}"
        can_manage = row.get("can_manage", False)
    view = {**row, **draft}
    deadline = view.get("deadline")
    lines = [f"✏️ <b>{esc(view['title'])}</b>",
             f"📅 {short_date(deadline, lang) if deadline else t(lang, 'no_deadline')}"
             f" · 🕐 {view.get('due_time') or '—'}",
             f"{PRIORITY_MARK.get(view['priority'], '▫️')} {t(lang, 'prio_' + view['priority'])}"
             f" · 🔔 {_remind_label(view.get('remind_before'), lang)}",
             f"📍 {where}"]
    if row.get("project"):
        lines.append(f"📁 {esc(row['project'])}")
    if kind == "t" and row.get("completion") != "all":
        lines.append(f"👤 {t(lang, 'completion_' + row['completion'])}")
    if not can_manage:
        lines.append(f"<i>{t(lang, 'team_only_creator')}</i>")
    if draft:
        lines += ["", t(lang, "edit_unsaved")]
    code = f"{kind}:{item_id}"
    timer_code = f"tmr:open:{'t' if kind == 'p' else 'T'}:{item_id}"
    rows = []
    if can_manage:
        rows.append([InlineKeyboardButton(t(lang, "edit_name"), callback_data=f"te:{code}:n"),
                     InlineKeyboardButton(t(lang, "edit_date"), callback_data=f"te:{code}:d")])
        rows.append([InlineKeyboardButton(t(lang, "edit_time"), callback_data=f"te:{code}:t"),
                     InlineKeyboardButton(t(lang, "edit_remind"), callback_data=f"te:{code}:r")])
        rows.append([InlineKeyboardButton(t(lang, "edit_priority"), callback_data=f"te:{code}:p"),
                     InlineKeyboardButton(t(lang, "btn_timers"), callback_data=timer_code)])
        if teams and not draft:
            rows.append([InlineKeyboardButton(t(lang, "edit_move"), callback_data=f"te:{code}:m")])
    else:
        rows.append([InlineKeyboardButton(t(lang, "btn_timers"), callback_data=timer_code)])
    if not draft:
        last = []
        if kind == "p" and row.get("status") != "done":
            last.append(InlineKeyboardButton(t(lang, "edit_done"),
                                             callback_data=f"task:done:{item_id}"))
        if can_manage:
            last.append(InlineKeyboardButton(t(lang, "edit_delete"),
                                             callback_data=f"te:{code}:x"))
        if last:
            rows.append(last)
        rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="task:back")])
    else:
        rows += _save_cancel_rows(lang, "te", code)
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _habit_edit_view(s, user: User, ws: int, kind: str, item_id: int,
                     draft: dict | None = None) -> tuple[str, InlineKeyboardMarkup] | None:
    lang = user.language
    teams = svc.teams_for(s, user.telegram_id)
    draft = draft or {}
    if kind == "p":
        habit = s.get(db.Habit, item_id)
        if habit is None or habit.workspace_id != ws or habit.archived_at is not None:
            return None
        info = svc.habit_history(s, ws, item_id, days=7, tz=svc.user_tz(user))
        where = t(lang, "dest_personal")
        can_manage, protected = True, info["protected"]
        ritual = habit.system_key if habit.system_key in svc.SYSTEM_KEYS else None
        shared_in = (svc.shared_rituals(s, user.telegram_id).get(ritual, [])
                     if ritual else [])
    else:
        try:
            info = svc.team_habit_history(s, user.telegram_id, item_id, days=7,
                                          tz=svc.user_tz(user))
        except (ValueError, PermissionError):
            return None
        where = f"👥 {esc(info['team_name'])}"
        can_manage, protected = info["can_manage"], info["mirrored"]
        ritual, shared_in = None, []
    view = {**info, **draft}
    remind = view.get("remind_at")
    lines = [f"✏️ <b>{esc(view['name'])}</b>",
             f"🗂 {t(lang, CATEGORY_KEYS[view['category']])} · 📍 {where}",
             f"🔔 {remind or t(lang, 'remind_off')}",
             f"🔥 {info.get('streak', 0)} · {info.get('last7_done', 0)}/{info.get('last7_due', 0)}"]
    if info.get("paused"):
        lines.append(f"⏸ {t(lang, 'habit_paused')}")
    elif info.get("pause_from"):
        lines.append(f"⏸ {t(lang, 'habit_pause_from', day=short_date(info['pause_from'], lang))}")
    if protected:
        lines.append(f"<i>{t(lang, 'habit_protected_edit')}</i>")
    if shared_in:
        names = [x.name for x in teams if x.id in shared_in]
        lines.append(f"👥 {esc(', '.join(names))}")
    if draft:
        lines += ["", t(lang, "edit_unsaved")]
    code = f"{kind}:{item_id}"
    rows = []
    # A personal ritual is renamed and re-tiered like any habit; only a
    # team's mirrored copy of one stays as it is.
    if can_manage and (not protected or kind == "p"):
        rows.append([InlineKeyboardButton(t(lang, "edit_name"), callback_data=f"he:{code}:n"),
                     InlineKeyboardButton(t(lang, "edit_category"), callback_data=f"he:{code}:c")])
    if ritual and teams and not draft:
        # Personal ↔ team for a ritual: it stays yours and is also shown in
        # the team, each member's tick read from their own record.
        rows += [[InlineKeyboardButton(
            f"{'✅' if x.id in shared_in else '➕'} 👥 {x.name[:30]}",
            callback_data=f"he:{code}:sh:{x.id}")] for x in teams[:5]]
    if can_manage:
        remind_row = [InlineKeyboardButton(t(lang, "edit_remind"), callback_data=f"he:{code}:rm")]
        if not protected:
            remind_row.append(InlineKeyboardButton(
                t(lang, "btn_timers"),
                callback_data=f"tmr:open:{'h' if kind == 'p' else 'H'}:{item_id}"))
        rows.append(remind_row)
    if not draft:
        if can_manage and not protected:
            if teams:
                rows.append([InlineKeyboardButton(t(lang, "edit_move"), callback_data=f"he:{code}:m")])
            if info.get("paused") or info.get("pause_from"):
                rows.append([InlineKeyboardButton(t(lang, "habit_resume_btn"),
                                                  callback_data=f"he:{code}:r")])
            else:
                rows.append([InlineKeyboardButton(t(lang, "pause_tomorrow"), callback_data=f"he:{code}:pt"),
                             InlineKeyboardButton(t(lang, "pause_today"), callback_data=f"he:{code}:pd")])
        # Every habit can go — a ritual by switching its module off — and
        # come back from ♻️ with its history.
        if can_manage and (kind == "p" or not protected):
            rows.append([InlineKeyboardButton(t(lang, "edit_delete"), callback_data=f"he:{code}:x")])
        rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="habit:back")])
    else:
        rows += _save_cancel_rows(lang, "he", code)
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def show_task_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                         user: User, ws: int, kind: str, item_id: int, *,
                         edit: bool = True) -> None:
    draft = draft_changes(ctx, _draft_key("t", kind, item_id))
    with SessionLocal() as s:
        view = _task_edit_view(s, user, ws, kind, item_id, draft)
    if view is None:
        await _notice(update, t(user.language, "not_found"))
        return
    await _show(update, view[0], view[1], edit=edit)


async def show_habit_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                          user: User, ws: int, kind: str, item_id: int, *,
                          edit: bool = True) -> None:
    draft = draft_changes(ctx, _draft_key("h", kind, item_id))
    with SessionLocal() as s:
        view = _habit_edit_view(s, user, ws, kind, item_id, draft)
    if view is None:
        await _notice(update, t(user.language, "not_found"))
        return
    await _show(update, view[0], view[1], edit=edit)


def _save_task_draft(s, user: User, ws: int, kind: str, item_id: int,
                     changes: dict) -> None:
    fields: dict = {}
    if changes.get("title"):
        fields["title"] = changes["title"]
    if "deadline" in changes:
        fields["deadline"] = (date.fromisoformat(changes["deadline"])
                              if changes["deadline"] else None)
    if "due_time" in changes:
        fields["due_time"] = (_parse_hhmm(changes["due_time"])
                              if changes["due_time"] else None)
    if "remind_before" in changes:
        fields["remind_before"] = changes["remind_before"]
    if changes.get("priority") in svc.PRIORITIES:
        fields["priority"] = changes["priority"]
    if not fields:
        return
    if kind == "p":
        svc.update_task(s, ws, item_id, **fields)
    else:
        svc.edit_team_task(s, user.telegram_id, item_id, **fields)


def _save_habit_draft(s, user: User, ws: int, kind: str, item_id: int,
                      changes: dict) -> None:
    fields: dict = {}
    if changes.get("name"):
        fields["name"] = changes["name"]
    if changes.get("category") in svc.HABIT_CATEGORIES:
        fields["category"] = changes["category"]
    if "remind_at" in changes:
        fields["remind_at"] = (_parse_hhmm(changes["remind_at"])
                               if changes["remind_at"] else None)
    if not fields:
        return
    if kind == "p":
        svc.update_habit(s, ws, item_id, **fields)
    else:
        svc.edit_team_habit(s, user.telegram_id, item_id, **fields)


async def route_task_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                          action: str, parts: list[str], user: User, ws: int) -> None:
    """te / ted / tet / ter / tep / tem / tex — every change to a task, from the chat.

    Fields (name, date, time, reminder, priority) go into the draft and are
    written on 💾; moving and deleting happen at once.
    """
    lang = user.language
    kind, item_id = parts[1], int(parts[2])
    tz = svc.user_tz(user)
    code = f"{kind}:{item_id}"
    key = _draft_key("t", kind, item_id)
    back = [InlineKeyboardButton(t(lang, "back"), callback_data=f"tedit:{code}")]

    if action == "te":
        op = parts[3]
        if op == "n":
            start_flow(ctx, "task_edit", target_id=item_id, kind=kind)
            await _show(update, t(lang, "ask_new_title"), cancel_keyboard(lang), edit=True)
        elif op == "d":
            await _show(update, t(lang, "ask_new_date"), InlineKeyboardMarkup([
                [InlineKeyboardButton(t(lang, "days_today"), callback_data=f"ted:{code}:0"),
                 InlineKeyboardButton(t(lang, "days_1"), callback_data=f"ted:{code}:1")],
                [InlineKeyboardButton(t(lang, "days_7"), callback_data=f"ted:{code}:7"),
                 InlineKeyboardButton(t(lang, "days_none"), callback_data=f"ted:{code}:none")],
                [InlineKeyboardButton(t(lang, "days_custom"), callback_data=f"ted:{code}:custom")],
                back]), edit=True)
        elif op == "t":
            start_flow(ctx, "task_edit_time", target_id=item_id, kind=kind)
            await _show(update, t(lang, "ask_task_time"), InlineKeyboardMarkup([
                [InlineKeyboardButton(v, callback_data=f"tet:{code}:{v.replace(':', '')}")
                 for v in ("09:00", "12:00", "15:00", "18:00")],
                [InlineKeyboardButton(t(lang, "time_none"), callback_data=f"tet:{code}:none")],
                back]), edit=True)
        elif op == "r":
            choices = [InlineKeyboardButton(_remind_label(m, lang),
                                            callback_data=f"ter:{code}:{m}")
                       for m in TASK_REMIND_CHOICES]
            await _show(update, t(lang, "ask_remind"), InlineKeyboardMarkup(
                [choices[:3], choices[3:],
                 [InlineKeyboardButton(t(lang, "remind_off"), callback_data=f"ter:{code}:off")],
                 back]), edit=True)
        elif op == "p":
            await _show(update, t(lang, "ask_priority"), InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{PRIORITY_MARK[p]} {t(lang, 'prio_' + p)}",
                                      callback_data=f"tep:{code}:{p[0]}")]
                for p in ("high", "medium", "low")] + [back]), edit=True)
        elif op == "m":
            with SessionLocal() as s:
                teams = svc.teams_for(s, user.telegram_id)
            await _show(update, t(lang, "ask_move"), dest_keyboard(lang, teams, f"tem:{code}"),
                        edit=True)
        elif op == "x":
            await _show(update, t(lang, "confirm_delete"), InlineKeyboardMarkup([
                [InlineKeyboardButton(t(lang, "edit_delete"), callback_data=f"tex:{code}")],
                back]), edit=True)
        elif op == "sv":
            changes = draft_changes(ctx, key)
            try:
                with SessionLocal() as s:
                    _save_task_draft(s, user, ws, kind, item_id, changes)
            finally:
                draft_drop(ctx)
            await _toast(update, t(lang, "edit_saved"))
            await show_task_edit(update, ctx, user, ws, kind, item_id)
        elif op == "cx":
            draft_drop(ctx)
            ctx.user_data.pop("flow", None)
            await _toast(update, t(lang, "edit_discarded"))
            await show_task_edit(update, ctx, user, ws, kind, item_id)
        return

    try:
        if action == "ted":
            value = parts[3]
            if value == "custom":
                start_flow(ctx, "task_edit_date", target_id=item_id, kind=kind)
                await _show(update, t(lang, "cd_ask_date", title=""),
                            cancel_keyboard(lang), edit=True)
                return
            today = svc.today_local(tz)
            new = None if value == "none" else today + timedelta(days=int(value))
            draft_set(ctx, key, deadline=new.isoformat() if new else None)
        elif action == "tet":
            value = parts[3]
            ctx.user_data.pop("flow", None)
            draft_set(ctx, key, due_time=None if value == "none"
                      else f"{value[:2]}:{value[2:]}")
        elif action == "ter":
            value = parts[3]
            draft_set(ctx, key, remind_before=None if value == "off" else int(value))
        elif action == "tep":
            draft_set(ctx, key, priority={"h": "high", "m": "medium", "l": "low"}[parts[3]])
        elif action == "tem":
            target = parts[3]
            with SessionLocal() as s:
                if kind == "p" and target != "p":
                    moved = svc.move_task(s, user.telegram_id, task_id=item_id,
                                          to_team=int(target))
                    team = svc.team_for(s, user.telegram_id, int(target))
                    await notify_teammates(int(target), user.telegram_id,
                                           "team_ev_task_add", moved["title"],
                                           team.name if team else "")
                    await _show(update, t(lang, "moved_to", where=f"👥 {esc(team.name if team else '')}"),
                                None, edit=True)
                    return
                if kind == "t" and target == "p":
                    svc.move_task(s, user.telegram_id, team_task_id=item_id)
                    await _show(update, t(lang, "moved_to", where=t(lang, "dest_personal")),
                                None, edit=True)
                    return
                if kind == "t" and target != "p":
                    await _notice(update, t(lang, "move_between_teams"))
                    return
        elif action == "tex":
            draft_drop(ctx)
            with SessionLocal() as s:
                if kind == "p":
                    title = svc.delete_task(s, ws, item_id)
                else:
                    row = s.get(db.TeamTask, item_id)
                    title = row.title if row else ""
                    team_id = row.team_id if row else None
                    svc.archive_team_task(s, user.telegram_id, item_id)
                    if team_id is not None:
                        team = svc.team_for(s, user.telegram_id, team_id)
                        await notify_teammates(team_id, user.telegram_id,
                                               "team_ev_task_del", title,
                                               team.name if team else "")
            await _show(update, t(lang, "task_deleted", title=esc(title)), None, edit=True)
            await show_tasks(update, ctx)
            return
    except PermissionError:
        await _notice(update, t(lang, "team_only_creator"))
        return
    except (ValueError, svc.NotFound):
        await _notice(update, t(lang, "error"))
        return
    await show_task_edit(update, ctx, user, ws, kind, item_id)


async def route_habit_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                           action: str, parts: list[str], user: User, ws: int) -> None:
    """he / hec / her / hem / hex — every change to a habit, from the chat."""
    lang = user.language
    kind, item_id = parts[1], int(parts[2])
    code = f"{kind}:{item_id}"
    key = _draft_key("h", kind, item_id)
    back = [InlineKeyboardButton(t(lang, "back"), callback_data=f"hedit:{code}")]
    try:
        with SessionLocal() as s:
            if action == "he":
                op = parts[3]
                if op == "n":
                    start_flow(ctx, "habit_rename", target_id=item_id, kind=kind)
                    await _show(update, t(lang, "ask_new_title"), cancel_keyboard(lang),
                                edit=True)
                    return
                if op == "c":
                    await _show(update, t(lang, "ask_habit_cat"), InlineKeyboardMarkup(
                        [[InlineKeyboardButton(t(lang, CATEGORY_KEYS[c]),
                                               callback_data=f"hec:{code}:{c[0]}")]
                         for c in svc.HABIT_CATEGORIES] + [back]), edit=True)
                    return
                if op == "rm":
                    start_flow(ctx, "habit_remind", target_id=item_id, kind=kind)
                    presets = [InlineKeyboardButton(v, callback_data=f"her:{code}:{v.replace(':', '')}")
                               for v in HABIT_REMIND_CHOICES]
                    await _show(update, t(lang, "ask_habit_remind"), InlineKeyboardMarkup(
                        [presets[:3], presets[3:],
                         [InlineKeyboardButton(t(lang, "remind_off"),
                                               callback_data=f"her:{code}:off")],
                         back]), edit=True)
                    return
                if op == "m":
                    teams = svc.teams_for(s, user.telegram_id)
                    await _show(update, t(lang, "ask_move"),
                                dest_keyboard(lang, teams, f"hem:{code}"), edit=True)
                    return
                if op == "x":
                    await _show(update, t(lang, "confirm_delete_habit"), InlineKeyboardMarkup([
                        [InlineKeyboardButton(t(lang, "edit_delete"), callback_data=f"hex:{code}")],
                        back]), edit=True)
                    return
                if op == "sv":
                    changes = draft_changes(ctx, key)
                    draft_drop(ctx)
                    _save_habit_draft(s, user, ws, kind, item_id, changes)
                    await _toast(update, t(lang, "edit_saved"))
                elif op == "cx":
                    draft_drop(ctx)
                    ctx.user_data.pop("flow", None)
                    await _toast(update, t(lang, "edit_discarded"))
                elif op in ("pt", "pd", "r"):
                    paused = op != "r"
                    start = "tomorrow" if op == "pt" else "today"
                    if kind == "p":
                        svc.set_habit_paused(s, ws, item_id, paused, from_day=start)
                    else:
                        svc.edit_team_habit(s, user.telegram_id, item_id,
                                            paused=paused, from_day=start)
                elif op == "sh" and kind == "p" and len(parts) > 4:
                    # A ritual shown in a team, or taken back out of it.
                    habit = s.get(db.Habit, item_id)
                    if habit is None or habit.workspace_id != ws \
                            or habit.system_key not in svc.SYSTEM_KEYS:
                        raise svc.NotFound("habit")
                    team_id = int(parts[4])
                    shared = team_id in svc.shared_rituals(
                        s, user.telegram_id).get(habit.system_key, [])
                    if shared:
                        svc.unshare_ritual_in(s, user.telegram_id, team_id,
                                              habit.system_key)
                    else:
                        svc.share_ritual(s, user.telegram_id, team_id,
                                         habit.system_key)
                    await _toast(update, t(lang, "saved"))
            elif action == "hec":
                draft_set(ctx, key, category={"n": "non_negotiable", "t": "target",
                                              "b": "bonus"}[parts[3]])
            elif action == "her":
                value = parts[3]
                ctx.user_data.pop("flow", None)
                draft_set(ctx, key, remind_at=None if value == "off"
                          else f"{value[:2]}:{value[2:]}")
            elif action == "hem":
                target = parts[3]
                if kind == "p" and target != "p":
                    moved = svc.move_habit(s, user.telegram_id, habit_id=item_id,
                                           to_team=int(target))
                    team = svc.team_for(s, user.telegram_id, int(target))
                    await notify_teammates(int(target), user.telegram_id,
                                           "team_ev_habit_add", moved["name"],
                                           team.name if team else "")
                    await _show(update, t(lang, "moved_to", where=f"👥 {esc(team.name if team else '')}"),
                                None, edit=True)
                    return
                if kind == "t" and target == "p":
                    svc.move_habit(s, user.telegram_id, team_habit_id=item_id)
                    await _show(update, t(lang, "moved_to", where=t(lang, "dest_personal")),
                                None, edit=True)
                    return
                if kind == "t" and target != "p":
                    await _notice(update, t(lang, "move_between_teams"))
                    return
            elif action == "hex":
                draft_drop(ctx)
                if kind == "p":
                    name = svc.remove_habit(s, ws, item_id)
                else:
                    row = s.get(db.TeamHabit, item_id)
                    name, team_id = (row.name, row.team_id) if row else ("", None)
                    svc.archive_team_habit(s, user.telegram_id, item_id)
                    if team_id is not None:
                        team = svc.team_for(s, user.telegram_id, team_id)
                        await notify_teammates(team_id, user.telegram_id,
                                               "team_ev_habit_del", name,
                                               team.name if team else "")
                await log_event(ctx.bot, user, "🗑 HABIT DELETED", f"Habit: {esc(name)}")
                await _toast(update, t(lang, "habit_removed", name=name))
                await show_habits(update, ctx, edit=True)
                return
    except PermissionError:
        await _notice(update, t(lang, "team_only_creator"))
        return
    except ValueError as e:
        await _notice(update, t(lang, "habit_protected" if str(e) == "protected" else "error"))
        return
    except svc.NotFound:
        await _notice(update, t(lang, "not_found"))
        return
    await show_habit_edit(update, ctx, user, ws, kind, item_id)


# ---------------------------------------------------------------------------
# Projects — your own and your teams', side by side
# ---------------------------------------------------------------------------
#
# Callback data is `pj:<verb>:<p|t>:<id>` — `p` a personal project, `t` a
# team's. A team project is managed by whoever opened it, an admin or the
# owner; anybody in the team may file tasks on it.

def _project_row_line(project: dict) -> str:
    done = project.get("status") == "done"
    total, finished = project.get("tasks_total", 0), project.get("tasks_done", 0)
    return (f"{'✅' if done else '•'} {esc(project['name'])}"
            + (f" — {finished}/{total}" if total else ""))


async def show_projects(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *,
                        edit: bool = False) -> None:
    got = await guard(update, ctx, write=False)
    if got is None:
        return
    user, ws = got
    lang = user.language
    with SessionLocal() as s:
        personal = svc.list_projects(s, ws)
        shared = [(team.id, team.name, svc.list_team_projects(s, user.telegram_id, team.id))
                  for team in svc.teams_for(s, user.telegram_id)]

    lines = [t(lang, "projects_title")]
    buttons = []
    if personal:
        lines += ["", f"<b>{t(lang, 'dest_personal')}</b>"]
        lines += [_project_row_line(p) for p in personal]
        buttons += [(f"📁 {p['name'][:32]}", f"pj:open:p:{p['id']}") for p in personal]
    for _team_id, team_name, rows in shared:
        if not rows:
            continue
        lines += ["", f"<b>👥 {esc(team_name)}</b>"]
        lines += [_project_row_line(p) for p in rows]
        buttons += [(f"👥 {p['name'][:30]}", f"pj:open:t:{p['id']}") for p in rows]
    if not buttons:
        lines += ["", t(lang, "projects_none")]

    rows = [[InlineKeyboardButton(label, callback_data=data)] for label, data in buttons[:14]]
    rows.append([InlineKeyboardButton(t(lang, "btn_add_project"), callback_data="pj:new")])
    rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="task:back")])
    await _show(update, "\n".join(lines), InlineKeyboardMarkup(rows), edit=edit)


def _project_view(s, user: User, ws: int, kind: str,
                  project_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    lang = user.language
    tz = svc.user_tz(user)
    if kind == "p":
        project = next((p for p in svc.list_projects(s, ws) if p["id"] == project_id), None)
        if project is None:
            return None
        tasks = svc.project_tasks(s, ws, project_id, tz=tz)
        for task in tasks:
            task["done"] = task["status"] == "done"
        where, can_manage = t(lang, "dest_personal"), True
        percent = project.get("progress", 0)
    else:
        try:
            project = svc.team_project_for(s, user.telegram_id, project_id)
        except svc.NotFound:
            return None
        tasks = svc.list_team_tasks(s, user.telegram_id, project["team_id"],
                                    project_id=project_id, tz=tz)
        where, can_manage = f"👥 {esc(project['team_name'])}", project["can_manage"]
        percent = project.get("percent", 0)

    done = project.get("status") == "done"
    lines = [f"📁 <b>{esc(project['name'])}</b>", f"📍 {where}"]
    if project.get("description"):
        lines.append(f"<i>{esc(project['description'])}</i>")
    lines.append(
        f"📅 {short_date(project['deadline'], lang) if project.get('deadline') else '—'}"
        f" · {t(lang, 'project_status_done' if done else 'project_status_active')}")
    total = project.get("tasks_total", 0)
    lines.append(f"{_bar(percent)} {project.get('tasks_done', 0)}/{total} · {percent}%")
    lines += ["", f"<b>{t(lang, 'project_tasks')}</b>"]
    if tasks:
        for task in tasks[:12]:
            mark = "✅" if task.get("done") else PRIORITY_MARK.get(task["priority"], "▫️")
            when = f" · {short_date(task['deadline'], lang)}" if task.get("deadline") else ""
            lines.append(f"{mark} {esc(task['title'])}{when}")
    else:
        lines.append(t(lang, "none"))
    if not can_manage:
        lines += ["", f"<i>{t(lang, 'proj_only_manager')}</i>"]

    code = f"{kind}:{project_id}"
    rows = [[InlineKeyboardButton(
        f"{'✅' if task.get('done') else '⬜'} {task['title'][:36]}",
        callback_data=f"tedit:{kind}:{task['id']}")]
        for task in tasks[:8] if not task.get("done")]
    rows.append([InlineKeyboardButton(t(lang, "proj_btn_add_task"), callback_data=f"pj:task:{code}")])
    if can_manage:
        rows.append([
            InlineKeyboardButton(t(lang, "proj_btn_reopen" if done else "proj_btn_done"),
                                 callback_data=f"pj:st:{code}:{'active' if done else 'done'}"),
            InlineKeyboardButton(t(lang, "proj_btn_rename"), callback_data=f"pj:ren:{code}")])
        rows.append([
            InlineKeyboardButton(t(lang, "proj_btn_desc"), callback_data=f"pj:desc:{code}"),
            InlineKeyboardButton(t(lang, "proj_btn_deadline"), callback_data=f"pj:dl:{code}")])
        rows.append([InlineKeyboardButton(t(lang, "proj_btn_delete"), callback_data=f"pj:x:{code}")])
    rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="pj:list")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def show_project(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                       project_id: int, kind: str = "p", *, edit: bool = True) -> None:
    got = await guard(update, ctx, write=False)
    if got is None:
        return
    user, ws = got
    with SessionLocal() as s:
        view = _project_view(s, user, ws, kind, project_id)
    if view is None:
        await _notice(update, t(user.language, "not_found"))
        return
    await _show(update, view[0], view[1], edit=edit)


def _update_project(s, user: User, ws: int, kind: str, project_id: int,
                    **fields) -> str:
    if kind == "p":
        return svc.update_project(s, ws, project_id, **fields).name
    return svc.update_team_project(s, user.telegram_id, project_id, **fields)["name"]


async def create_project(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                         user: User, ws: int, title: str, dest: str) -> None:
    ctx.user_data.pop("flow", None)
    with SessionLocal() as s:
        if dest == "p":
            project_id, kind = svc.add_project(s, ws, title).id, "p"
            team_name = None
        else:
            made = svc.add_team_project(s, user.telegram_id, int(dest), title)
            project_id, kind = made["id"], "t"
            team = svc.team_for(s, user.telegram_id, int(dest))
            team_name = team.name if team else ""
    await log_event(ctx.bot, user, "📁 PROJECT ADDED", f"Project: {esc(title)}")
    if team_name is not None:
        await notify_teammates(int(dest), user.telegram_id, "team_ev_project_add",
                               title, team_name)
    await show_project(update, ctx, project_id, kind,
                       edit=update.callback_query is not None)


async def route_project(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                        action: str, parts: list[str], user: User, ws: int) -> None:
    """pj:* and pjd / pjdest — projects, personal and shared."""
    lang = user.language
    verb = parts[1] if len(parts) > 1 else "list"

    if action == "pjdest":
        flow = current_flow(ctx, "project_dest") or {}
        if not flow.get("title"):
            await _notice(update, t(lang, "flow_expired"))
            return
        await create_project(update, ctx, user, ws, flow["title"], parts[1])
        return

    if action == "pjd":
        kind, project_id, value = parts[1], int(parts[2]), parts[3]
        if value == "custom":
            start_flow(ctx, "project_deadline", kind=kind, target_id=project_id)
            await _show(update, t(lang, "ask_project_deadline"), cancel_keyboard(lang), edit=True)
            return
        today = svc.today_local(svc.user_tz(user))
        new = None if value == "none" else today + timedelta(days=int(value))
        with SessionLocal() as s:
            _update_project(s, user, ws, kind, project_id, deadline=new)
        await show_project(update, ctx, project_id, kind)
        return

    if verb == "list":
        await show_projects(update, ctx, edit=True)
        return
    if verb == "new":
        start_flow(ctx, "project_add")
        await _show(update, t(lang, "ask_project_name"), cancel_keyboard(lang), edit=False)
        return

    kind, project_id = parts[2], int(parts[3])
    code = f"{kind}:{project_id}"
    back = [InlineKeyboardButton(t(lang, "back"), callback_data=f"pj:open:{code}")]
    if verb == "open":
        await show_project(update, ctx, project_id, kind)
    elif verb == "task":
        dest = "p"
        if kind == "t":
            with SessionLocal() as s:
                dest = str(svc.team_project_for(s, user.telegram_id, project_id)["team_id"])
        start_flow(ctx, "task_title", dest=dest, project_id=project_id)
        await update.effective_message.reply_text(
            t(lang, "ask_task_name"), reply_markup=suggest_keyboard(lang, "t"))
    elif verb == "st":
        with SessionLocal() as s:
            _update_project(s, user, ws, kind, project_id, status=parts[4])
        await show_project(update, ctx, project_id, kind)
    elif verb == "ren":
        start_flow(ctx, "project_rename", kind=kind, target_id=project_id)
        await _show(update, t(lang, "ask_project_rename"), cancel_keyboard(lang), edit=True)
    elif verb == "desc":
        start_flow(ctx, "project_desc", kind=kind, target_id=project_id)
        await _show(update, t(lang, "ask_project_desc"), cancel_keyboard(lang), edit=True)
    elif verb == "dl":
        await _show(update, t(lang, "ask_project_deadline"), InlineKeyboardMarkup([
            [InlineKeyboardButton(t(lang, "days_7"), callback_data=f"pjd:{code}:7"),
             InlineKeyboardButton("30", callback_data=f"pjd:{code}:30"),
             InlineKeyboardButton("90", callback_data=f"pjd:{code}:90")],
            [InlineKeyboardButton(t(lang, "days_none"), callback_data=f"pjd:{code}:none"),
             InlineKeyboardButton(t(lang, "days_custom"), callback_data=f"pjd:{code}:custom")],
            back]), edit=True)
    elif verb == "x":
        await _show(update, t(lang, "proj_confirm_delete"), InlineKeyboardMarkup([
            [InlineKeyboardButton(t(lang, "proj_btn_delete"), callback_data=f"pj:xx:{code}")],
            back]), edit=True)
    elif verb == "xx":
        with SessionLocal() as s:
            if kind == "p":
                name = svc.delete_project(s, ws, project_id)
            else:
                name = svc.delete_team_project(s, user.telegram_id, project_id)
        await log_event(ctx.bot, user, "🗑 PROJECT DELETED", f"Project: {esc(name)}")
        await _toast(update, t(lang, "project_deleted", name=name))
        await show_projects(update, ctx, edit=True)


# ---------------------------------------------------------------------------
# Ten suggestions under every "type a name" prompt
# ---------------------------------------------------------------------------

def suggestions(lang: str, what: str) -> list[str]:
    """The ten habits (`h`) or tasks (`t`) offered for this language."""
    return [x for x in t(lang, "sug_habits" if what == "h" else "sug_tasks").split("|") if x]


def suggest_keyboard(lang: str, what: str) -> InlineKeyboardMarkup:
    """Small, two to a row, with Cancel under them — a hint, not a pitch."""
    items = suggestions(lang, what)
    buttons = [InlineKeyboardButton(label, callback_data=f"sug:{what}:{i}")
               for i, label in enumerate(items)]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(t(lang, "cancel"), callback_data="flow:cancel")])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Quick capture — anything typed that is not a command becomes a task, on a tap
# ---------------------------------------------------------------------------

#: A greeting is not a task. These, typed alone, just open Home.
GREETINGS = {"salom", "assalomu alaykum", "assalom", "hi", "hello", "hey",
             "привет", "здравствуйте", "rahmat", "thanks", "спасибо", "ok", "ок"}

#: How long an offered capture can still be saved.
CAPTURE_TTL = 1800


async def offer_capture(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                        text: str) -> None:
    """"ertaga 15:00 doktorga qo'ng'iroq" → a task, one tap away.

    Nothing is written until the tap: a stray message must not become a task
    nobody meant. But the tap is the only step — the date and the time are
    already read out of the text, and where it goes is one button per list.
    """
    got = await guard(update, ctx, write=False)
    if got is None:
        return
    user, ws = got
    lang = user.language
    message = update.effective_message
    if text.lower().strip(" !.") in GREETINGS or len(text.strip()) < 3:
        await show_home(update, ctx)
        return
    money = svc.parse_money_text(text[:300]) if svc.looks_like_money(text[:300]) else None
    if money is not None:
        # "Tushlik 45 ming" is spending, not a task: offered as money first,
        # with "as a task" still one tap away.
        capture_id = uuid.uuid4().hex[:6]
        ctx.user_data["capture"] = {"id": capture_id, "title": text.strip()[:300],
                                    "deadline": "", "due_time": "", "money": money,
                                    "expires": time.time() + CAPTURE_TTL}
        await message.reply_text(
            money_capture_text(money, lang), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                # Both directions, one tap each: the guess is only the label
                # at the top, never a decision the person has to undo.
                [InlineKeyboardButton(t(lang, "money_btn_expense"),
                                      callback_data=f"cap:{capture_id}:me"),
                 InlineKeyboardButton(t(lang, "money_btn_income"),
                                      callback_data=f"cap:{capture_id}:mi")],
                [InlineKeyboardButton(t(lang, "money_as_task"),
                                      callback_data=f"cap:{capture_id}:p"),
                 InlineKeyboardButton(t(lang, "capture_skip"),
                                      callback_data=f"cap:{capture_id}:x")]]))
        return
    with SessionLocal() as s:
        parsed = svc.parse_quick_capture(text[:300], svc.today_local(svc.user_tz(user)))
        teams = svc.teams_for(s, user.telegram_id)
    capture_id = uuid.uuid4().hex[:6]
    ctx.user_data["capture"] = {
        "id": capture_id, "title": parsed["title"][:300],
        "deadline": parsed["deadline"].isoformat() if parsed["deadline"] else "",
        "due_time": parsed["due_time"].strftime("%H:%M") if parsed["due_time"] else "",
        "expires": time.time() + CAPTURE_TTL}
    when = []
    if parsed["deadline"]:
        when.append(f"📅 {short_date(parsed['deadline'].isoformat(), lang)}")
    if parsed["due_time"]:
        when.append(f"⏰ {parsed['due_time'].strftime('%H:%M')}")
    body = f"📥 <b>{esc(parsed['title'])}</b>"
    if when:
        body += "\n" + " · ".join(when)
    body += f"\n\n{t(lang, 'capture_ask')}"
    rows = [[InlineKeyboardButton(t(lang, "capture_save"),
                                  callback_data=f"cap:{capture_id}:p")]]
    for team in teams[:svc.MAX_TEAMS_PER_USER]:
        rows.append([InlineKeyboardButton(f"👥 {team.name[:30]}",
                                          callback_data=f"cap:{capture_id}:{team.id}")])
    rows.append([InlineKeyboardButton(t(lang, "capture_skip"),
                                      callback_data=f"cap:{capture_id}:x")])
    await message.reply_text(body, parse_mode=ParseMode.HTML,
                             reply_markup=InlineKeyboardMarkup(rows))


async def save_capture(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                       parts: list[str], user: User, ws: int) -> None:
    lang = user.language
    capture = ctx.user_data.get("capture") or {}
    if (len(parts) < 3 or capture.get("id") != parts[1]
            or capture.get("expires", 0) < time.time()):
        await _notice(update, t(lang, "capture_expired"))
        return
    ctx.user_data.pop("capture", None)
    target = parts[2]
    if target == "x":
        await update.callback_query.edit_message_text(t(lang, "cancelled"))
        return
    if target in ("m", "me", "mi") and capture.get("money"):
        money = capture["money"]
        kind = {"me": "expense", "mi": "income"}.get(target, money["kind"])
        if kind != money["kind"]:
            money = svc.parse_money_text(money["note"], kind) or money
        with SessionLocal() as s:
            svc.add_money(s, ws, money["kind"], money["amount"], money["category"],
                          note=money["note"], source="bot", tz=svc.user_tz(user))
        await update.callback_query.edit_message_text(
            t(lang, "money_saved", amount=fmt_money(money["amount"], lang)),
            reply_markup=money_keyboard(lang))
        return
    deadline = date.fromisoformat(capture["deadline"]) if capture["deadline"] else None
    due = _parse_hhmm(capture["due_time"]) if capture["due_time"] else None
    with SessionLocal() as s:
        if target == "p":
            svc.add_task(s, ws, capture["title"], deadline=deadline, due_time=due,
                         remind_before=svc.DEFAULT_REMIND_BEFORE if deadline else None)
            where = t(lang, "dest_personal")
        else:
            svc.add_team_task(s, user.telegram_id, int(target), capture["title"],
                              deadline=deadline, due_time=due,
                              remind_before=svc.DEFAULT_REMIND_BEFORE if deadline else None)
            team = svc.team_for(s, user.telegram_id, int(target))
            where = f"👥 {team.name if team else ''}"
    await update.callback_query.edit_message_text(
        t(lang, "capture_saved", title=esc(capture["title"]), where=esc(where)),
        parse_mode=ParseMode.HTML)
    if target != "p":
        with SessionLocal() as s:
            team = svc.team_for(s, user.telegram_id, int(target))
        await notify_teammates(int(target), user.telegram_id, "team_ev_task_add",
                               capture["title"], team.name if team else "")


def _parse_hhmm(value: str) -> dtime | None:
    try:
        hour, minute = (int(x) for x in value.replace(".", ":").split(":"))
        return dtime(hour, minute)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Callback router
# ---------------------------------------------------------------------------

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    await query.answer()
    parts = query.data.split(":")
    action = parts[0]

    # Subscription check is available before onboarding completes.
    if action == "sub" and parts[1] == "check":
        uid = account_of(update)
        state = await is_subscribed(ctx.bot, uid)
        with SessionLocal() as s:
            user = s.get(User, uid)
            lang = user.language if user else "uz"
            if state is not True:
                await query.edit_message_text(
                    t(lang, "sub_missing" if state is False else "sub_unknown"),
                    reply_markup=subscribe_keyboard(lang))
                return
            changed = record_membership(s, uid, True, "api")
            first_time = not user.onboarded
            if first_time:
                user.onboarding_step = "done"
                user.onboarded = True
            s.commit()
            # Onboarding can also finish here, on the far side of the channel
            # check — so the same referral question has to be asked.
            qualified_inviter = svc.maybe_qualify_referral(s, uid) \
                if first_time else None
            snapshot, name = user, user.first_name or ""
        if changed:
            await log_event(ctx.bot, snapshot, "🔓 SUBSCRIPTION RESTORED")
        if qualified_inviter is not None:
            await notify_referral_qualified(ctx.bot, qualified_inviter)
        if first_time:
            await finish_onboarding_progress(uid)
        # Joining and checking lands the user inside — never back at /start.
        await query.edit_message_text(t(lang, "sub_restored"))
        await update.effective_message.reply_text(
            t(lang, "welcome_in", name=esc(name)) if first_time else t(lang, "saved"),
            parse_mode=ParseMode.HTML, reply_markup=menu_for(uid))
        await show_home(update, ctx)
        return

    if action == "lang":
        uid = account_of(update)
        if parts[1] not in ("uz", "en", "ru"):
            return
        with SessionLocal() as s:
            user = s.get(User, uid)
            if user is None:
                return
            user.language = parts[1]
            if not user.onboarded:
                user.onboarding_step = "account"
            s.commit()
            lang, onboarded = user.language, user.onboarded
            snapshot = user
        await log_event(ctx.bot, snapshot, "🌐 LANGUAGE CHANGED", f"Language: {lang}")

        if not onboarded:
            # First words in the language they just picked, by name — then the
            # one question that matters: new here, or signing in?
            await query.edit_message_text(
                t(lang, "hello_named", name=esc(update.effective_user.first_name or "")),
                parse_mode=ParseMode.HTML)
            await resume_onboarding(update, ctx, "account")
        else:
            # A settings change says what it changed, not just "saved".
            await query.edit_message_text(t(lang, "lang_changed"))
            await update.effective_message.reply_text(t(lang, "lang_changed"),
                                                      reply_markup=menu_for(uid))
        return

    # The account screen, signing in and out. Above the guard: somebody who
    # has not set up an account of their own can still sign in to one.
    if action == "acc":
        await route_account(update, ctx, parts)
        return

    # Setup runs before onboarding completes, so it sits above the guard.
    if action == "setup":
        uid = account_of(update)
        with SessionLocal() as s:
            user = s.get(User, uid)
            if user is None:
                return
            lang, step = user.language, user.onboarding_step
            telegram_name = user.first_name or update.effective_user.first_name or ""

        if parts[1] == "go":
            await query.edit_message_reply_markup(reply_markup=None)
            return await advance_setup(update, ctx, "name")

        if parts[1] == "name":
            # Keeping the Telegram name is one tap, which is what it should be.
            await query.edit_message_text(t(lang, "name_set", name=esc(telegram_name)),
                                          parse_mode=ParseMode.HTML)
            return await advance_setup(update, ctx, "modules")

        if parts[1] == "mod" and len(parts) > 2:
            chosen = set(setup_data(ctx).setdefault("modules", []))
            name = parts[2]
            if name in SETUP_MODULES:
                chosen ^= {name}
            setup_data(ctx)["modules"] = sorted(chosen)
            try:
                await query.edit_message_reply_markup(
                    reply_markup=modules_keyboard(lang, chosen))
            except BadRequest:
                pass
            return

        if parts[1] == "mod_done":
            chosen = set(setup_data(ctx).get("modules") or [])
            with SessionLocal() as s:
                user = s.get(User, uid)
                ws = svc.workspace_id_for(s, uid)
                svc.set_modules(s, ws, chosen, user=user)
            setup_data(ctx)["team"] = "team" in chosen
            labels = [t(lang, MODULE_LABELS[n]) for n in SETUP_MODULES if n in chosen]
            await query.edit_message_text(
                t(lang, "modules_set", list=", ".join(labels) if labels
                  else t(lang, "modules_none")), parse_mode=ParseMode.HTML)
            return await advance_setup(update, ctx, "presets")

        if parts[1] == "pre" and len(parts) > 2:
            chosen = _setup_presets(ctx)
            if parts[2] in svc.ORDINARY_PRESET_KEYS:
                chosen ^= {parts[2]}
            setup_data(ctx)["presets"] = sorted(chosen)
            try:
                await query.edit_message_reply_markup(
                    reply_markup=setup_presets_keyboard(lang, chosen))
            except BadRequest:
                pass
            return

        if parts[1] == "pre_done":
            chosen = _setup_presets(ctx)
            with SessionLocal() as s:
                user = s.get(User, uid)
                ws = svc.workspace_id_for(s, uid)
                for key in svc.ORDINARY_PRESET_KEYS:
                    if key in chosen:
                        svc.add_preset(s, ws, key, lang, tz=svc.user_tz(user))
                total = len(svc.list_habits(s, ws, tz=svc.user_tz(user)))
            await query.edit_message_text(t(lang, "presets_set", n=total),
                                          parse_mode=ParseMode.HTML)
            return await advance_setup(update, ctx, "done")

        if parts[1] == "skip":
            await query.edit_message_reply_markup(reply_markup=None)
            order = ONBOARDING_STEPS
            nxt = order[order.index(step) + 1] if step in order else "done"
            return await advance_setup(update, ctx, nxt)
        return

    # A team invite is answered before anything else: the person opening it
    # may not have finished setting up, and joining is their decision to make
    # on a screen that says what they are joining.
    if action == "tjoin":
        await answer_team_invite(update, ctx, parts[1] if len(parts) > 1 else "x")
        return

    if action == "gender":
        with SessionLocal() as s:
            user = s.get(User, account_of(update))
            user.gender = parts[1]
            s.commit()
            lang = user.language
            snapshot = user
        await query.edit_message_text(
            t(lang, "gender_changed", value=t(lang, parts[1])))
        await log_event(ctx.bot, snapshot, "👤 GENDER CHANGED", f"Gender: {parts[1]}")
        # Gender is asked when prayer needs it, so land back on that screen.
        await show_habits(update, ctx)
        return

    if action == "flow" and parts[1] == "cancel":
        ctx.user_data.pop("flow", None)
        with SessionLocal() as s:
            user = s.get(User, account_of(update))
            lang = user.language if user else "uz"
            onboarded = bool(user and user.onboarded)
            step = user.onboarding_step if user else "language"
        await query.edit_message_text(t(lang, "cancelled"))
        if not onboarded:
            # Backing out of signing in during setup lands on the setup step
            # it came from, not on a dead end.
            await resume_onboarding(update, ctx, step)
        return

    # The invite screen, from Settings and from the "invite again" button on a
    # qualification message. `show_invite` runs its own guard.
    if action == "ref" and parts[1] == "show":
        await show_invite(update, ctx)
        return

    # Everything below requires a completed account. A button that only opens
    # a screen stays usable for somebody who left the channel — their own data
    # is never locked away from them; what the gate stops is new writes.
    got = await guard(update, ctx, write=not is_read_callback(action, parts))
    if got is None:
        return
    user, ws = got
    lang = user.language

    try:
        await route_callback(update, ctx, action, parts, user, ws, lang)
        # The same rule the API middleware applies, on the other surface: a
        # button that changed something counts, a button that only navigated
        # or opened a settings panel does not.
        if action in COUNTED_CALLBACKS:
            await count_action(user.telegram_id, ctx, update.effective_message, lang)
    except svc.NotFound:
        await _notice(update, t(lang, "not_found"))
    except PermissionError:
        await _notice(update, t(lang, "team_only_creator"))
    except ValueError as e:
        await _notice(update, t(lang, "habit_protected" if str(e) == "protected"
                                else "error"))


async def route_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                         action: str, parts: list[str], user: User,
                         ws: int, lang: str) -> None:
    query = update.callback_query
    message = update.effective_message

    # --- habits ---
    if action == "habit":
        sub = parts[1]
        if sub == "toggle":
            try:
                with SessionLocal() as s:
                    svc.toggle_habit(s, ws, int(parts[2]), tz=svc.user_tz(user))
            except ValueError as e:
                if str(e) != "timer_required":
                    raise
                # A timed habit is finished by its timer: open it instead.
                await show_timer(update, ctx, user, ws, "habit", int(parts[2]))
                return
            await show_habits(update, ctx, edit=True)
        elif sub == "add":
            start_flow(ctx, "habit_name")
            await message.reply_text(t(lang, "ask_habit_name"),
                                     reply_markup=suggest_keyboard(lang, "h"))
        elif sub == "dellist":
            with SessionLocal() as s:
                habits = [h for h in svc.list_habits(s, ws) if not h["protected"]]
            if not habits:
                await query.answer(t(lang, "empty"), show_alert=True)
                return
            rows = [[InlineKeyboardButton(h["name"],
                                          callback_data=f"habit:del:{h['id']}")]
                    for h in habits]
            rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="habit:back")])
            await query.edit_message_text(t(lang, "choose_delete"),
                                          reply_markup=InlineKeyboardMarkup(rows))
        elif sub == "del":
            with SessionLocal() as s:
                name = svc.delete_habit(s, ws, int(parts[2]))
            await log_event(ctx.bot, user, "🗑 HABIT DELETED", f"Habit: {esc(name)}")
            await show_habits(update, ctx, edit=True)
        elif sub == "wake":
            with SessionLocal() as s:
                result = svc.mark_wakeup(s, ws, tz=svc.user_tz(user))
            await query.answer(wake_reply(result, lang), show_alert=True)
            if result["done"]:
                await log_event(ctx.bot, user, "☀️ WAKE-UP", f"At: {result['now']}")
            await show_habits(update, ctx, edit=True)
        elif sub == "resume":
            with SessionLocal() as s:
                svc.set_habit_paused(s, ws, int(parts[2]), False)
            await show_habits(update, ctx, edit=True)
        elif sub == "editlist":
            # Private and shared habits together; a shared one is edited by
            # its creator, an admin or the owner, and says so if you are not.
            with SessionLocal() as s:
                # Every habit, the automatic ones too: each can be deleted
                # and given a reminder, even where it cannot be renamed.
                mine = svc.list_habits(s, ws, tz=svc.user_tz(user))
                shared = svc.team_items_for_day(s, user.telegram_id,
                                                tz=svc.user_tz(user))["habits"]
                shared = [h for h in shared if not h.get("mirrored")]
            if not mine and not shared:
                await _notice(update, t(lang, "empty"))
                return
            rows = [[InlineKeyboardButton(h["name"][:40], callback_data=f"hedit:p:{h['id']}")]
                    for h in mine[:15]]
            rows += [[InlineKeyboardButton(f"👥 {h['name'][:30]} · {h['team_name'][:12]}",
                                           callback_data=f"hedit:t:{h['id']}")]
                     for h in shared[:10]]
            rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="habit:back")])
            await query.edit_message_text(t(lang, "choose_edit"),
                                          reply_markup=InlineKeyboardMarkup(rows))
        elif sub == "presets":
            await show_presets(update, ws, lang)
        elif sub == "preset" and len(parts) > 2 and parts[2] in svc.PRESET_KEYS:
            # One tap puts it on the list or takes it off; taking it off only
            # archives it, so a mis-tap costs nothing — the next tap brings it
            # back with its history.
            with SessionLocal() as s:
                current = next(p for p in svc.habit_presets(s, ws, lang)
                               if p["key"] == parts[2])
                if current["added"]:
                    svc.remove_preset(s, ws, parts[2])
                else:
                    svc.add_preset(s, ws, parts[2], lang, tz=svc.user_tz(user))
            await show_presets(update, ws, lang)
        elif sub == "mirrored":
            await _notice(update, t(lang, "habit_mirrored"))
        elif sub == "back":
            await show_habits(update, ctx, edit=True)
        elif sub == "noop":
            pass

    # --- money ---
    elif action == "money":
        sub = parts[1] if len(parts) > 1 else "show"
        if sub == "add" and len(parts) > 2 and parts[2] in svc.MONEY_KINDS:
            start_flow(ctx, "money_entry", kind=parts[2])
            ask = "money_ask_expense" if parts[2] == "expense" else "money_ask_income"
            await message.reply_text(t(lang, ask),
                                     parse_mode=ParseMode.HTML,
                                     reply_markup=cancel_keyboard(lang))
        elif sub == "limits":
            # Each spending category with its limit; one tap to change it.
            with SessionLocal() as s:
                limits = svc.money_budgets(s, ws)
            rows = [[InlineKeyboardButton(
                f"{icon} {t(lang, 'mcat_' + cid)} — "
                f"{fmt_money(limits.get(cid, 0), lang) if limits.get(cid) else t(lang, 'money_no_limit')}",
                callback_data=f"money:limit:{cid}")]
                for cid, icon, _c, _d, kind in svc.MONEY_CATEGORIES if kind == "expense"]
            rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="money:show")])
            await query.edit_message_text(t(lang, "money_limits_title"),
                                          parse_mode=ParseMode.HTML,
                                          reply_markup=InlineKeyboardMarkup(rows))
        elif (sub == "limit" and len(parts) > 2
              and svc.money_category_kind(parts[2]) == "expense"):
            start_flow(ctx, "money_limit", category=parts[2])
            label_key = "mcat_" + parts[2]
            await message.reply_text(
                t(lang, "money_limit_ask", cat=t(lang, label_key)),
                parse_mode=ParseMode.HTML, reply_markup=cancel_keyboard(lang))
        else:
            await show_money(update, ctx, edit=True)

    # --- tasks ---
    elif action == "task":
        sub = parts[1]
        if sub == "add":
            start_flow(ctx, "task_title")
            await message.reply_text(t(lang, "ask_task_name"),
                                     reply_markup=suggest_keyboard(lang, "t"))
        elif sub == "restorelist":
            with SessionLocal() as s:
                gone = svc.archived_tasks(s, ws)
            if not gone:
                await _notice(update, t(lang, "empty"))
                return
            rows = [[InlineKeyboardButton(f"♻️ {x['title'][:40]}",
                                          callback_data=f"task:restore:{x['id']}")]
                    for x in gone]
            rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="task:back")])
            await query.edit_message_text(t(lang, "restore_title"),
                                          reply_markup=InlineKeyboardMarkup(rows))
        elif sub == "restore":
            with SessionLocal() as s:
                title = svc.restore_task(s, ws, int(parts[2])).title
            await _toast(update, t(lang, "task_restored", title=title))
            await show_tasks(update, ctx, edit=True)
        elif sub in ("donelist", "editlist", "dellist"):
            with SessionLocal() as s:
                tasks = _all_open_tasks(s, ws)
                shared = (svc.team_items_for_day(s, user.telegram_id,
                                                 tz=svc.user_tz(user))["tasks"]
                          if sub == "editlist" else [])
            if not tasks and not shared:
                # The button is not drawn in this state, so reaching here means
                # a stale keyboard. Say so instead of opening an empty chooser.
                await query.answer(t(lang, "empty"), show_alert=True)
                return
            prompt = {"donelist": "choose_done", "editlist": "choose_edit",
                      "dellist": "choose_delete"}[sub]
            if sub == "editlist":
                rows = [[InlineKeyboardButton(task["title"][:40],
                                              callback_data=f"tedit:p:{task['id']}")]
                        for task in tasks[:15]]
                rows += [[InlineKeyboardButton(
                    f"👥 {task['title'][:30]} · {(task.get('team_name') or '')[:12]}",
                    callback_data=f"tedit:t:{task['id']}")] for task in shared[:10]]
            else:
                verb = {"donelist": "done", "dellist": "del"}[sub]
                rows = [[InlineKeyboardButton(task["title"][:40],
                                              callback_data=f"task:{verb}:{task['id']}")]
                        for task in tasks[:15]]
            rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="task:back")])
            await query.edit_message_text(t(lang, prompt),
                                          reply_markup=InlineKeyboardMarkup(rows))
        elif sub == "del":
            with SessionLocal() as s:
                title = svc.delete_task(s, ws, int(parts[2]))
            await log_event(ctx.bot, user, "🗑 TASK DELETED", f"Task: {esc(title)}")
            await query.edit_message_text(t(lang, "task_deleted", title=title))
            await show_tasks(update, ctx)
        elif sub == "done":
            try:
                with SessionLocal() as s:
                    task = svc.complete_task(s, ws, int(parts[2]),
                                             tz=svc.user_tz(user))
            except ValueError as e:
                if str(e) != "timer_required":
                    raise
                await show_timer(update, ctx, user, ws, "task", int(parts[2]))
                return
            await log_event(ctx.bot, user, "✅ TASK COMPLETED", f"Task: {esc(task.title)}")
            await query.edit_message_text(t(lang, "task_done", title=task.title))
            await show_tasks(update, ctx)
        elif sub == "edit":
            # An old keyboard's edit button: the full edit menu now.
            await show_task_edit(update, ctx, user, ws, "p", int(parts[2]))
        elif sub == "back":
            await show_tasks(update, ctx, edit=True)
        elif sub == "noop":
            pass

    elif action == "tedit":
        await show_task_edit(update, ctx, user, ws, parts[1], int(parts[2]))

    elif action in ("te", "ted", "tet", "ter", "tep", "tem", "tex"):
        await route_task_edit(update, ctx, action, parts, user, ws)

    elif action == "hedit":
        await show_habit_edit(update, ctx, user, ws, parts[1], int(parts[2]))

    elif action in ("he", "hec", "her", "hem", "hex"):
        await route_habit_edit(update, ctx, action, parts, user, ws)

    elif action == "cap":
        await save_capture(update, ctx, parts, user, ws)

    elif action == "home":
        if len(parts) > 1 and parts[1] == "stats":
            await show_stats(update, ctx)
        else:
            await show_home(update, ctx)

    elif action == "tmr":
        await route_timer(update, ctx, parts, user, ws, lang)

    elif action in ("cd", "cds", "cdd", "cdl", "cdq"):
        await route_countdown(update, ctx, action, parts, user, ws, lang)

    # --- shared (team) work: each member ticks only their own share ---
    elif action == "thabit" and len(parts) > 2 and parts[1] == "toggle":
        try:
            with SessionLocal() as s:
                svc.toggle_team_habit(s, user.telegram_id, int(parts[2]),
                                      tz=svc.user_tz(user))
        except PermissionError:
            await _notice(update, t(lang, "not_found"))
            return
        except ValueError as e:
            reason = str(e)
            if reason == "timer_required":
                await show_timer(update, ctx, user, ws, "thabit", int(parts[2]))
            else:
                await _notice(update, t(lang, "habit_mirrored" if reason == "mirrored"
                                        else "not_found"))
            return
        await show_habits(update, ctx, edit=True)

    elif action == "ttask":
        if len(parts) > 2 and parts[1] == "toggle":
            try:
                with SessionLocal() as s:
                    svc.toggle_team_task(s, user.telegram_id, int(parts[2]),
                                         tz=svc.user_tz(user))
            except PermissionError:
                await _notice(update, t(lang, "not_found"))
                return
            except ValueError as e:
                reason = str(e)
                if reason == "timer_required":
                    await show_timer(update, ctx, user, ws, "ttask", int(parts[2]))
                else:
                    await _notice(update, t(lang, "team_not_assigned"
                                            if reason == "not_assigned" else "not_found"))
                return
        await show_team_tasks(update, ctx, user)

    elif action in ("team", "treq", "tnot", "town", "trole"):
        await route_team(update, ctx, action, parts, user, lang)

    elif action in ("tdest", "hdest"):
        # Where the new task or habit goes: yours, or a team's.
        kind = "task" if action == "tdest" else "habit"
        flow = current_flow(ctx, f"{kind}_dest") or {}
        title = flow.get("title")
        if not title:
            await _notice(update, t(lang, "flow_expired"))
            return
        dest = parts[1]
        if dest != "p":
            with SessionLocal() as s:
                if svc.team_for(s, user.telegram_id, int(dest)) is None:
                    await _notice(update, t(lang, "not_found"))
                    return
        if kind == "task":
            start_flow(ctx, "task_days", title=title, dest=dest)
            await query.edit_message_text(t(lang, "ask_task_days"),
                                          reply_markup=days_keyboard(lang))
        else:
            start_flow(ctx, "habit_cat", title=title, dest=dest)
            await query.edit_message_text(t(lang, "ask_habit_cat"),
                                          reply_markup=category_keyboard(lang))

    elif action == "taskday":
        flow = current_flow(ctx, "task_days") or {}
        title = flow.get("title")
        if not title:
            await _notice(update, t(lang, "flow_expired"))
            return
        dest = flow.get("dest") or "p"
        project_id = flow.get("project_id")
        if parts[1] == "custom":
            start_flow(ctx, "task_custom_days", title=title, dest=dest,
                       project_id=project_id)
            await query.edit_message_text(t(lang, "ask_custom_days"))
            return
        if parts[1] == "none":
            await query.edit_message_text(t(lang, "no_deadline"))
            await ask_task_project(update, ctx, title, None, dest, project_id=project_id)
            return
        deadline = svc.today_local(svc.user_tz(user)) + timedelta(days=int(parts[1]))
        await query.edit_message_text(f"📅 {short_date(deadline.isoformat(), lang)}")
        await ask_task_project(update, ctx, title, deadline, dest, project_id=project_id)

    elif action == "taskproj":
        if current_flow(ctx, "task_project") is None:
            await _notice(update, t(lang, "flow_expired"))
            return
        await create_task_from_flow(update, ctx, user, ws, int(parts[1]))

    # --- projects ---
    elif action in ("pj", "pjd", "pjdest"):
        await route_project(update, ctx, action, parts, user, ws)

    elif action == "project":
        # Buttons from keyboards drawn before projects had a screen of their
        # own: each lands on the matching place in it.
        sub = parts[1]
        if sub == "add":
            await route_project(update, ctx, "pj", ["pj", "new"], user, ws)
        elif sub in ("open", "rename", "del") and len(parts) > 2:
            verb = {"open": "open", "rename": "ren", "del": "x"}[sub]
            await route_project(update, ctx, "pj", ["pj", verb, "p", parts[2]], user, ws)
        else:
            await show_projects(update, ctx, edit=True)

    elif action == "sug":
        # A suggestion tapped under "type a name": exactly as if it were typed.
        what = parts[1] if len(parts) > 1 else ""
        flow = current_flow(ctx, "habit_name" if what == "h" else "task_title")
        items = suggestions(lang, what)
        index = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else -1
        if flow is None or not 0 <= index < len(items):
            await _notice(update, t(lang, "flow_expired"))
            return
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except BadRequest:
            pass
        await handle_flow(update, ctx, flow, items[index])

    elif action == "habitcat":
        flow = current_flow(ctx, "habit_cat") or {}
        title = flow.get("title")
        if not title:
            await _notice(update, t(lang, "flow_expired"))
            return
        dest = flow.get("dest") or "p"
        category = parts[1] if parts[1] in svc.HABIT_CATEGORIES else "target"
        tz = svc.user_tz(user)
        with SessionLocal() as s:
            if dest == "p":
                habit = svc.add_habit(s, ws, title, category, tz=tz)
                name, timer = habit.name, svc.timer_minutes_for(habit.timer_minutes,
                                                                habit.name)
                team_name = None
            else:
                row = svc.add_team_habit(s, user.telegram_id, int(dest), title,
                                         category=category, tz=tz)
                name, timer = row["name"], row.get("timer_minutes")
                team = svc.team_for(s, user.telegram_id, int(dest))
                team_name = team.name if team else ""
        ctx.user_data.pop("flow", None)
        if team_name is None:
            await query.edit_message_text(t(lang, "habit_added", name=name))
        else:
            await query.edit_message_text(
                t(lang, "habit_added_team", name=esc(name), team=esc(team_name)),
                parse_mode=ParseMode.HTML)
            await notify_teammates(int(dest), user.telegram_id, "team_ev_habit_add",
                                   name, team_name)
        if timer:
            await message.reply_text(t(lang, "habit_added_timer",
                                       dur=fmt_minutes(timer, lang)),
                                     parse_mode=ParseMode.HTML)
        await log_event(ctx.bot, user, "➕ HABIT ADDED",
                        f"Habit: {esc(name)}\nCategory: {category}")
        await show_habits(update, ctx)

    # --- settings ---
    elif action == "set":
        sub = parts[1]
        # Every sub-screen offers Back, so changing your mind never strands you.
        back = [InlineKeyboardButton(t(lang, "back"), callback_data="set:back")]

        if sub == "back":
            await show_settings(update, ctx, edit=True)
        elif sub == "feedback":
            await _menu_feedback(update, ctx, lang)
        elif sub == "lang":
            await query.edit_message_text(t(lang, "ask_lang"),
                                          reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🇺🇿 O'zbek", callback_data="lang:uz")],
                [InlineKeyboardButton("🇬🇧 English", callback_data="lang:en")],
                [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru")],
                back,
            ]))
        elif sub == "gender":
            await query.edit_message_text(t(lang, "ask_gender"),
                                          reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(t(lang, "male"), callback_data="gender:male")],
                [InlineKeyboardButton(t(lang, "female"), callback_data="gender:female")],
                back,
            ]))
        elif sub == "theme":
            rows = [[InlineKeyboardButton(THEME_NAMES.get(name, name.title()),
                                          callback_data=f"theme:{name}")]
                    for name in THEMES]
            rows.append(back)
            await query.edit_message_text(t(lang, "btn_theme"),
                                          reply_markup=InlineKeyboardMarkup(rows))
        elif sub == "photo":
            start_flow(ctx, "photo_wait")
            rows = []
            if user.photo_file_id:
                rows.append([InlineKeyboardButton(t(lang, "btn_photo_del"),
                                                  callback_data="set:photodel")])
            rows.append([InlineKeyboardButton(t(lang, "cancel"),
                                              callback_data="flow:cancel")])
            rows.append(back)
            await query.edit_message_text(t(lang, "ask_photo"),
                                          reply_markup=InlineKeyboardMarkup(rows))
        elif sub == "waketime":
            start_flow(ctx, "wake_time")
            await query.edit_message_text(t(lang, "ask_wake_time"),
                                          reply_markup=InlineKeyboardMarkup([back]))
        elif sub == "modules":
            with SessionLocal() as s:
                live = svc.modules_for(s, ws)
            await query.edit_message_text(
                t(lang, "modules_settings"), parse_mode=ParseMode.HTML,
                reply_markup=modules_keyboard(lang, {k for k, v in live.items() if v},
                                              prefix="setm", done="set:back"))
        elif sub == "photodel":
            ctx.user_data.pop("flow", None)
            with SessionLocal() as s:
                row = s.get(User, user.telegram_id)
                row.photo_file_id = ""
                s.commit()
            await query.edit_message_text(t(lang, "photo_removed"))
            await show_settings(update, ctx)

    elif action == "setm":
        # A ritual switched off keeps every log it had; switched back on, it
        # picks up where it was, and the days away are not counted as missed.
        with SessionLocal() as s:
            row = s.get(User, user.telegram_id)
            live = {k for k, v in svc.modules_for(s, ws).items() if v}
            live ^= {parts[1]} if parts[1] in svc.MODULES else set()
            live = {k for k, v in svc.set_modules(s, ws, live, user=row).items() if v}
        try:
            await query.edit_message_reply_markup(
                reply_markup=modules_keyboard(lang, live, prefix="setm", done="set:back"))
        except BadRequest:
            pass

    elif action == "theme":
        with SessionLocal() as s:
            row = s.get(User, user.telegram_id)
            row.theme = parts[1] if parts[1] in THEMES else DEFAULT_THEME
            s.commit()
            snapshot = row
        # A confirmation that does not name the change is a confirmation the
        # user has to verify by going and looking.
        await query.edit_message_text(
            t(lang, "theme_changed",
              name=THEME_NAMES.get(row.theme, row.theme.title())))
        await log_event(ctx.bot, snapshot, "🎨 THEME CHANGED", f"Theme: {parts[1]}")


# ---------------------------------------------------------------------------
# Channel membership events
# ---------------------------------------------------------------------------

async def on_chat_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """React the moment a user joins or leaves the required channel."""
    member = update.chat_member
    if member is None or not deps.REQUIRED_CHANNEL_ID:
        return
    if str(member.chat.id) != str(deps.REQUIRED_CHANNEL_ID):
        return

    telegram_id = member.new_chat_member.user.id
    subscribed = member.new_chat_member.status in MEMBER_STATES

    with SessionLocal() as s:
        user = s.get(User, telegram_id)
        if user is None:
            return
        if not record_membership(s, telegram_id, subscribed, "event"):
            s.commit()      # still refresh the timestamp
            return
        s.commit()
        lang, snapshot = user.language, user

    try:
        if subscribed:
            await ctx.bot.send_message(telegram_id, t(lang, "sub_restored"),
                                       reply_markup=menu_for(telegram_id))
            await log_event(ctx.bot, snapshot, "🔓 SUBSCRIPTION RESTORED")
        else:
            await ctx.bot.send_message(telegram_id, t(lang, "sub_lost"),
                                       reply_markup=subscribe_keyboard(lang))
            await log_event(ctx.bot, snapshot, "🔒 SUBSCRIPTION LOST")
    except TelegramError as e:
        log.warning("could not notify %s: %s", telegram_id, e)


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Technical failures go to the log, never to the user or admin channel."""
    log.exception("handler error", exc_info=ctx.error)


# ---------------------------------------------------------------------------
# Scheduled reports
# ---------------------------------------------------------------------------

#: Yesterday's score decides which of four things is worth saying this morning.
#: Bands, not randomness: the same day always gets the same message, so the bot
#: never sounds like it is generating encouragement at you. Each one names a
#: fact and then asks for one concrete thing — that is the part that moves
#: someone out of bed, not an exclamation mark.
def morning_note(lang: str, overall: int, measured: bool) -> str:
    if not measured:
        return t(lang, "coach_blank")
    if overall >= 80:
        return t(lang, "coach_high", pct=overall)
    if overall >= 50:
        return t(lang, "coach_mid", pct=overall)
    return t(lang, "coach_low", pct=overall)


def render_morning(data: dict, lang: str) -> str:
    """The first thing read that day: a greeting, yesterday, then the whole day.

    Order matters, and it is the order a person actually wants at four in the
    morning. It opens by greeting them by name — a report that opens with a
    percentage is a dashboard, and nobody wants to be handed a dashboard before
    breakfast. Then yesterday, in one percentage and one line of parts, because
    the only useful thing about yesterday now is whether to hold the line or
    change something.

    Then the day itself, complete: the week's mission, the tasks, the habits
    that repeat and the five prayers. "Complete" is the point — this is the one
    message that has to be readable instead of opening the app, so nothing that
    is expected of somebody today is left out of it.
    """
    y, today = data["yesterday"], data["today"]
    name = (data.get("name") or "").strip()

    greeting = (t(lang, "r_good_morning", name=esc(name)) if name
                else t(lang, "r_good_morning_plain"))
    lines = [f"<b>{greeting}</b>",
             f"<i>{short_date(today['date'], lang)}</i>",
             ""]

    # Yesterday: one line, one percentage, no autopsy.
    lines.append(f"<b>{t(lang, 'r_yesterday')}: {y['overall']}%</b>  "
                 f"{_bar(y['overall'], 8)}")
    lines.append(f"<i>{t(lang, 'r_habits')} {y['habits_done']}/{y['habits_total']} · "
                 f"{t(lang, 'r_prayer')} {y['prayer_performed']}/{y['prayer_required']} · "
                 f"{t(lang, 'r_tasks')} {y['tasks_completed']}</i>")
    lines.append("")
    lines.append(morning_note(lang, y["overall"], y["measured"]))

    # Today: the mission first, because it is the one decision already made.
    focus = today["focus"]
    primary = next((f for f in focus if f["slot"] == 1), focus[0] if focus else None)
    if primary:
        lines.append("")
        lines.append(f"🎯 <b>{t(lang, 'r_mission')}</b>")
        lines.append(esc(primary["title"]))

    # The dates being counted down to. Near the top, because "IELTS in 12
    # days" is what decides how the rest of the morning is spent.
    lines += _countdown_section(today.get("countdowns"), lang)

    # Everything today asks for, under one heading, so the plan reads as one
    # thing rather than as three unrelated lists.
    lines.append("")
    lines.append(f"<b>{t(lang, 'r_today_all')}</b>")

    planned = False
    if today["tasks"]:
        planned = True
        lines.append("")
        lines.append(f"⚡ <b>{t(lang, 'r_today_plan')}</b> · {len(today['tasks'])}")
        lines += _task_lines(today["tasks"][:6], lang)
        if len(today["tasks"]) > 6:
            lines.append(f"<i>+{len(today['tasks']) - 6}</i>")

    if today.get("habits_total"):
        planned = True
        lines.append("")
        lines.append(f"✅ <b>{t(lang, 'r_habits_today')}</b> · "
                     f"{today['habits_done']}/{today['habits_total']}")
        # Only the ones still open: a list that repeats what is already ticked
        # is a list somebody stops reading.
        for habit in today["habits"][:6]:
            lines.append(f"• {esc(habit)}")
        if len(today["habits"]) > 6:
            lines.append(f"<i>+{len(today['habits']) - 6}</i>")

    # Prayer is always part of the day, so it is always in the plan — but it
    # is not what decides whether the day has anything in it.
    lines.append("")
    lines.append(f"🕌 <b>{t(lang, 'r_prayer_today')}</b>")

    if today["overdue"]:
        lines.append("")
        lines.append(f"<b>{t(lang, 'home_overdue')}</b> · {len(today['overdue'])}")
        lines.append(f"<i>{t(lang, 'r_overdue_hint')}</i>")
    if today["birthdays"]:
        lines.append("")
        for b in today["birthdays"]:
            when = "🎉" if b["days_left"] == 0 \
                else f"{b['days_left']} {t(lang, 'days_short')}"
            lines.append(f"🎂 {esc(b['person_name'])} — {when}")

    lines.append("")
    lines.append(t(lang, "r_start_now") if planned else t(lang, "r_nothing_planned"))
    return "\n".join(lines)


def team_item_mark(item: dict) -> str:
    """✅ only when everybody who owed it has it — never on a partial tick.

    `owed_by` is who the item was for that day (everybody, one "any" task,
    or its assignees); `finished_for` is who it counts as done for. A green
    tick on a habit half the team has not done yet reads as "done", and the
    other half then believes it.
    """
    owed = set(item.get("owed_by") or [])
    finished = set(item.get("finished_for") or item.get("done_by") or [])
    if owed and owed <= finished:
        return "✅"
    return "🔸" if finished else "◻️"


def render_team(summary: dict, lang: str, viewer_id: int, *,
                evening: bool) -> str | None:
    """One team's day, as its own message.

    A separate message rather than a section appended to the personal report,
    because the two answer different questions and are read in different
    moods. The personal report is about you; this is about the two of you, and
    burying it under a list of your own unfinished tasks is how a shared goal
    quietly stops being looked at.

    Returns None when the team has set itself nothing for today — an empty
    message every morning is the fastest way to teach somebody to ignore one.
    """
    if not summary or not summary.get("total"):
        return None

    title = t(lang, "team_evening_title" if evening else "team_morning_title",
              name=esc(summary["name"]))
    lines = [title, ""]

    def who(ids: list[int]) -> str:
        """Names of the people who ticked it. Names, never "you".

        A message about two people that calls one of them "you" reads as a
        form; naming both reads as the two of them, which is what a shared
        report is for.
        """
        return ", ".join(esc(m["name"]) for m in summary["members"]
                         if m["user_id"] in ids)

    lines.insert(1, f"<i>{render_team_units(summary, lang)}</i>")

    if summary["tasks"]:
        lines.append(f"<b>{t(lang, 'team_tasks')}</b>")
        for task in summary["tasks"][:8]:
            row = f"{team_item_mark(task)} {esc(task['title'])}"
            if task["done_by"]:
                row += f"  <i>— {who(task['done_by'])}</i>"
            lines.append(row)
        if len(summary["tasks"]) > 8:
            lines.append(f"<i>+{len(summary['tasks']) - 8}</i>")
        lines.append("")

    if summary["habits"]:
        lines.append(f"<b>{t(lang, 'team_habits')}</b>")
        for habit in summary["habits"][:8]:
            row = f"{team_item_mark(habit)} {esc(habit['name'])}"
            if habit["done_by"]:
                row += f"  <i>— {who(habit['done_by'])}</i>"
            lines.append(row)
        lines.append("")

    # Each person's own share, side by side. This is the part that makes a
    # shared list worth having over two private ones.
    for member in summary["members"]:
        name = esc(member["name"])
        percent = member["percent"]
        bar = _bar(percent if percent is not None else 0, 6)
        lines.append(f"{bar}  <b>{name}</b> · {member['done']}/{member['total']}"
                     + (f" · {percent}%" if percent is not None else ""))

    if evening and all(m["done"] == m["total"] and m["total"]
                       for m in summary["members"]):
        lines.append("")
        lines.append(f"<b>{t(lang, 'team_all_done')}</b>")

    return "\n".join(lines)


def _versus_yesterday(overall: dict, lang: str) -> str:
    """Today against yesterday, as a sentence.

    An arrow tells you the direction and hides the size, which is the part
    worth knowing: "up" covers both a point and twenty. When yesterday had
    nothing to measure there is no comparison to make, and inventing one —
    "+63%" against a day the user never used — would be flattering nonsense.
    """
    previous = overall.get("yesterday")
    if previous is None:
        return t(lang, "r_vs_yesterday_none")
    delta = overall["value"] - previous
    if delta > 0:
        return t(lang, "r_vs_yesterday_up", delta=delta)
    if delta < 0:
        return t(lang, "r_vs_yesterday_down", delta=abs(delta))
    return t(lang, "r_vs_yesterday_same")


def render_evening(data: dict, lang: str) -> str:
    """The last thing read that day: how it went, then good night.

    The whole day as one number with its parts drawn as bars, then what is still
    open, and it closes by wishing the reader a good night. The order is the
    point: at half past nine the useful framing is "here is where the day
    landed", not a fresh to-do list — and the last line of the last message of
    the day should be a human one, not a statistic.
    """
    overall = data["overall"]
    lines = [f"<b>{t(lang, 'evening_title')}</b>",
             f"<i>{short_date(data['date'], lang)}</i>", ""]

    lines.append(f"📊 <b>{overall['value']}%</b> "
                 f"{TREND_MARK.get(overall['trend'], '▪️')}  {_bar(overall['value'])}")
    # The number on its own says nothing about whether the day went well. What
    # makes it readable is the one it is being compared with, stated rather
    # than implied by an arrow.
    lines.append(f"<i>{_versus_yesterday(overall, lang)}</i>")
    lines.append("")

    def row(label: str, done, total, percent) -> str:
        # `overall_components` returns None for a category with nothing in it,
        # and drops it from the weighting rather than scoring it nought. An
        # empty bar here said the opposite — that the user scored 0% — about a
        # day that simply had no tasks on it.
        if percent is None:
            return f"{label}  —  {done}/{total}"
        return f"{label}  {_bar(percent, 6)}  {done}/{total}"

    components = overall.get("components", {})
    lines.append(row(t(lang, "r_tasks"), data["tasks_completed"],
                     data["tasks_completed"] + len(data["tasks_remaining"]),
                     components.get("tasks")))
    lines.append(row(t(lang, "r_habits"), data["habits_done"],
                     data["habits_total"], components.get("habits")))
    lines.append(row(t(lang, "r_prayer"), data["prayer_performed"],
                     data["prayer_required"], components.get("prayer")))
    lines.append(f"{t(lang, 'r_journal')}: "
                 f"{t(lang, 'r_yes') if data['journal'] else t(lang, 'r_no')}")

    if data["focus"]:
        lines.append(f"{t(lang, 'r_focus')}: "
                     f"{data['focus_done']}/{len(data['focus'])}")

    # What actually happened today, both halves of it. The report used to list
    # only what was left over, which reads as a reprimand at nine in the
    # evening and leaves out the part worth reading: the things that got done.
    done_count = (data["tasks_completed"] + data["habits_done"]
                  + data["prayer_performed"] + (1 if data["journal"] else 0))
    lines.append("")
    lines.append(f"<b>{t(lang, 'r_done_title')}</b> · {done_count}")
    if done_count:
        done_bits = []
        if data["tasks_completed"]:
            done_bits.append(f"{t(lang, 'r_tasks')} {data['tasks_completed']}")
        if data["habits_done"]:
            done_bits.append(f"{t(lang, 'r_habits')} {data['habits_done']}"
                             f"/{data['habits_total']}")
        if data["prayer_performed"]:
            done_bits.append(f"{t(lang, 'r_prayer')} {data['prayer_performed']}"
                             f"/{data['prayer_required']}")
        if data["journal"]:
            done_bits.append(t(lang, "r_journal"))
        lines.append("<i>" + " · ".join(done_bits) + "</i>")
    else:
        lines.append(f"<i>{t(lang, 'r_nothing_done')}</i>")

    unfinished = (data["tasks_remaining"] + data["tasks_overdue"]
                  + data["habits_remaining"])
    lines.append("")
    lines.append(f"<b>{t(lang, 'r_missed_title')}</b> · {len(unfinished)}")
    if unfinished:
        for item in unfinished[:8]:
            lines.append(f"• {esc(item)}")
        if len(unfinished) > 8:
            lines.append(f"<i>+{len(unfinished) - 8}</i>")
        lines.append("")
        lines.append(f"<i>{t(lang, 'r_evening_close')}</i>")
    else:
        lines.append(f"<i>{t(lang, 'r_nothing_missed')}</i>")
        lines.append("")
        lines.append(t(lang, "r_evening_clear"))

    # Once more before sleep: each of them is one day closer tomorrow.
    lines += _countdown_section(data.get("countdowns"), lang)

    # The last line of the day.
    lines.append("")
    lines.append(f"<b>{t(lang, 'r_good_night')}</b>")

    return "\n".join(lines)


def _countdown_section(items: list[dict] | None, lang: str) -> list[str]:
    """The countdown block both reports carry, or nothing when there is none."""
    if not items:
        return []
    lines = ["", t(lang, "r_countdowns")]
    lines += [countdown_line(item, lang) for item in items[:8]]
    if len(items) > 8:
        lines.append(f"<i>+{len(items) - 8}</i>")
    return lines


#: The name today's statistics run is claimed under.
STATS_JOB = "platform_stats"


async def send_platform_stats(bot, *, force: bool = False) -> bool:
    """Aggregate usage numbers for the operator. No user content, ever.

    Runs on the same frequent tick the reports use, and decides for itself
    whether today's post is owed. It used to be a `cron(hour=10)` entry, which
    looked right and quietly did not work: APScheduler's default jobstore is
    in memory, so at every boot cron computes the next fire *after now*. A
    deploy at 11:00 pushed the post to 10:00 tomorrow — and a project being
    redeployed most days never reached it. `misfire_grace_time` could not help,
    because with nothing persisted there was no record a run had been missed.

    Now the schedule is not what guarantees "once a day"; `claim_job_run` is.
    A restart at any hour still delivers today's post, and any number of
    instances still deliver exactly one.

    Returns True when a post actually went out — `force` skips the clock check
    for `/admin stats`, but never the claim, so a manual trigger cannot produce
    a second copy of a post that already went.
    """
    if not STATS_CHANNEL_ID:
        log.warning("STATS_CHANNEL_ID and ADMIN_LOG_CHANNEL_ID are both unset "
                    "— the statistics post has nowhere to go")
        return False

    today = svc.today_local()
    if not force and svc.now_local().hour < STATS_POST_HOUR:
        return False                      # the hour has not arrived yet today

    with SessionLocal() as s:
        if not svc.claim_job_run(s, STATS_JOB, today):
            return False                  # already posted today, by someone

    try:
        with SessionLocal() as s:
            st = svc.platform_stats(s)
        await _post_platform_stats(bot, st)
    except Exception:
        # Give the claim back. Unlike a user report — where a failed send
        # usually means a blocked account and retrying all day is pointless —
        # this is one message to one channel the operator controls, so a blip
        # should cost a couple of minutes rather than the whole day.
        with SessionLocal() as s:
            svc.release_job_run(s, STATS_JOB, today)
        raise

    log.info("platform statistics posted for %s", today)
    return True


async def send_platform_stats_tick(bot) -> None:
    """The scheduled entry point: never let a failure escape into APScheduler.

    An exception here used to vanish into the scheduler's own logger, which is
    the worst place for it — the channel stays silent and nothing says why.
    """
    try:
        await send_platform_stats(bot)
    except Exception:
        log.exception("platform statistics post failed")


def _tally(counts: dict) -> str:
    """`uz 31 · en 4 · — 1`, sorted, with the unset bucket named.

    Sorted by `str(k or "")` rather than by the key itself, and that is not
    defensive padding — it is the bug that kept this channel silent. `gender`
    is NULL until the prayer screen asks for it, and `sorted()` on the raw
    pairs compares `None` with `"male"` the moment one user has answered and
    another has not, which is every real deployment. The `TypeError` went off
    inside the scheduled job, where nothing was catching it, so the post
    simply never appeared and no error was ever attributed to it.
    """
    return " · ".join(f"{k or '—'} {v}"
                      for k, v in sorted(counts.items(), key=lambda kv: str(kv[0] or "")))


async def _post_platform_stats(bot, st: dict) -> None:
    languages = _tally(st["languages"])
    genders = _tally(st["genders"])

    await admin_log(bot, (
        f"<b>📊 ERNESTOS STATISTIKA</b>\n{datetime.now(svc.TZ):%Y-%m-%d %H:%M}\n\n"
        f"<b>Foydalanuvchilar</b>\n"
        f"Jami: {st['total']} · oxirgi raqam: #{st['latest_member_no']}\n"
        f"Onboarding tugagan: {st['onboarded']}\n"
        f"Obuna: {st['subscribed']} · bloklangan: {st['blocked']}\n"
        f"Yangi — bugun: {st['new_today']} · hafta: {st['new_week']}\n\n"
        f"<b>Faollik</b>\nDAU {st['dau']} · WAU {st['wau']} · MAU {st['mau']}\n\n"
        f"<b>Hafta ichida</b>\n"
        f"Vazifa: +{st['tasks_created']} · bajarildi {st['tasks_done']}\n"
        f"Bugun kundalik: {st['journal_today']}\n"
        f"Taklif: {st['feedback_week']}\n\n"
        f"<b>Til</b>: {languages or '—'}\n<b>Jins</b>: {genders or '—'}\n\n"
        f"<b>Referral</b>\n"
        f"Jami: {st['referrals_total']} · qualified: {st['referrals_qualified']}"
        f" · pending: {st['referrals_pending']}\n"
        f"Conversion: {st['referral_conversion']}% · inviters: "
        f"{st['referral_inviters']}\n\n"
        f"<b>Progression</b>\n"
        f"O'rtacha ball: {st['avg_daily_score']} · perfect: "
        f"{st['perfect_days_today']}\n"
        f"Faol streak: {st['active_streaks']} · reytingda: "
        f"{st['rank_eligible_users']}\n"
        f"Bugun XP: {st['xp_today']}\n"
        f"Darajalar: {_tally(st['users_by_level']) or '—'}"
    ), chat_id=STATS_CHANNEL_ID, reraise=True)


async def _send_team_summaries(bot, telegram_id: int, lang: str,
                               report_type: str) -> int:
    """Send this user one message per team they are in. Returns how many.

    Quiet by design: a team with nothing set for today produces no message at
    all, so somebody who joined a team months ago and stopped using it is not
    sent an empty summary twice a day for ever.
    """
    evening = report_type == "evening"
    sent = 0
    try:
        with SessionLocal() as s:
            user = s.get(User, telegram_id)
            if user is None:
                return 0
            # Only teams this member still wants reports from.
            summaries = [svc.team_day_summary(s, team.id, tz=svc.user_tz(user))
                         for team in svc.teams_for(s, telegram_id)
                         if svc.member_notify(s, team.id, telegram_id)
                         in ("all", "important")]
    except Exception:
        log.exception("could not build team summaries for %s", telegram_id)
        return 0

    for summary in summaries:
        text = render_team(summary, lang, telegram_id, evening=evening)
        if not text:
            continue
        try:
            await bot.send_message(telegram_id, text,
                                   parse_mode=ParseMode.HTML,
                                   reply_markup=webapp_button(lang))
            sent += 1
            await asyncio.sleep(0.05)
        except TelegramError as e:
            log.warning("team summary to %s failed: %s", telegram_id, e)
        except Exception:
            log.exception("team summary to %s errored", telegram_id)
    return sent


class AccountFanOut:
    """A bot whose `send_message` also reaches every Telegram signed in to
    the account it is addressed to.

    Reports, reminders and team news are addressed to the account — its
    owner's Telegram id. Somebody who signed in from a second Telegram with
    the login and password is using the same account there, and should hear
    what it hears. The first send behaves exactly as before, errors and all;
    the copies are best-effort and never fail the job.
    """

    def __init__(self, bot):
        self._bot = bot

    def __getattr__(self, name):
        return getattr(self._bot, name)

    async def send_message(self, *args, **kwargs):
        result = await self._bot.send_message(*args, **kwargs)
        chat_id = kwargs.get("chat_id", args[0] if args else None)
        try:
            with SessionLocal() as s:
                others = accounts.linked_ids(s, int(chat_id))
        except (TypeError, ValueError):
            return result
        except Exception:
            log.exception("could not look up the Telegrams signed in to %s", chat_id)
            return result
        for other in others:
            try:
                if "chat_id" in kwargs:
                    await self._bot.send_message(*args, **{**kwargs, "chat_id": other})
                else:
                    await self._bot.send_message(other, *args[1:], **kwargs)
            except Exception as e:
                log.info("copy to signed-in Telegram %s failed: %s", other, e)
        return result


async def send_reports(bot, report_type: str) -> None:
    """Deliver one report to every user whose chosen time has just arrived.

    The job runs often and decides per user, because report times are now a
    setting rather than one hour for everybody. Sending exactly once per local
    day is still guaranteed by the outbox claim, not by the schedule.

    Nothing escapes into APScheduler. The per-recipient loop already survives
    one bad user, but everything *around* it — taking the advisory lock, and
    the query that lists recipients — ran unguarded, and a failure there is the
    worst possible kind: it takes out the whole batch, for every user, and the
    traceback lands in APScheduler's own logger where nobody is looking. The
    statistics job has had this guard since it was written; the two report jobs
    were the ones without it.
    """
    try:
        with svc.JobLock(SessionLocal, f"report:{report_type}") as lock:
            if not lock.acquired:
                return
            await _send_reports_locked(AccountFanOut(bot), report_type, None)
    except Exception:
        log.exception("%s report job failed before any recipient", report_type)


async def _send_reports_locked(bot, report_type: str, report_date) -> None:
    from db import DailyReportLog

    # Before anything else, take back the slots a dead process is sitting on.
    # A claim that was never resolved blocks that user's report for the whole
    # day, and nothing else in the system would ever clear it.
    try:
        with SessionLocal() as s:
            recovered = svc.reclaim_stale_claims(
                s, report_date or svc.today_local())
        if recovered:
            log.warning("recovered %s stale report claim(s) before the %s batch",
                        recovered, report_type)
    except Exception:
        # Recovery is an optimisation, not a precondition: if it fails the
        # batch should still run for everybody whose slot is free.
        log.exception("could not reclaim stale %s report claims", report_type)

    with SessionLocal() as s:
        recipients = svc.active_recipients(s)

    # Counted apart, because "not due or already claimed" was one number
    # covering two completely different situations: everybody's hour simply
    # has not come round yet, which is the normal state for most of the day,
    # and the outbox refusing every claim, which means nobody will ever be
    # sent anything. From the log those looked identical.
    sent = failed = not_due = taken = 0
    taken_states: dict[str, int] = {}
    for telegram_id, ws, lang in recipients:
        # Deciding whether this user is due, and claiming their slot, is as
        # capable of raising as the send is — an unreadable row or a dropped
        # connection here used to end the whole batch before anybody after
        # this recipient was even looked at.
        try:
            # Each user's day and each user's chosen time, in their own zone.
            with SessionLocal() as s:
                user = s.get(User, telegram_id)
                if user is None:
                    continue
                tz = svc.user_tz(user)
                when = report_date or svc.today_local(tz)
                due = (report_date is not None
                       or svc.report_is_due(user, report_type, svc.now_local(tz)))
            if not due:
                not_due += 1
                continue

            # Claim before building anything: the insert is the lock, so a
            # second worker finds the row taken and moves on (audit 036).
            with SessionLocal() as s:
                report_id = svc.claim_report(s, ws, report_type, when)
        except Exception:
            log.exception("%s report for %s could not be claimed",
                          report_type, telegram_id)
            failed += 1
            continue

        if report_id is None:
            # Due, but the slot would not open. Record what is actually in it,
            # because "sent" here means the user already has it and anything
            # else means they are being skipped for a reason worth seeing.
            taken += 1
            with SessionLocal() as s:
                held = s.scalar(select(DailyReportLog.status).where(
                    DailyReportLog.workspace_id == ws,
                    DailyReportLog.report_type == report_type,
                    DailyReportLog.report_date == when))
            state = held or "no row (constraint?)"
            taken_states[state] = taken_states.get(state, 0) + 1
            continue

        try:
            with SessionLocal() as s:
                user = s.get(User, telegram_id)
                if user is None:
                    svc.release_report(s, report_id, ws=ws,
                                       report_type=report_type, report_date=when)
                    continue
                data = (svc.morning_data(s, ws, user) if report_type == "morning"
                        else svc.evening_data(s, ws, user))

            text = (render_morning(data, lang) if report_type == "morning"
                    else render_evening(data, lang))
            await bot.send_message(telegram_id, text, parse_mode=ParseMode.HTML,
                                   reply_markup=webapp_button(lang))
            with SessionLocal() as s:
                svc.mark_report_sent(s, report_id)
            sent += 1

            # Each shared space gets its own message, after the personal one.
            # Failing to send a team summary must not undo a report that has
            # already gone out and been marked sent, so this is guarded
            # separately and never touches the outbox.
            await _send_team_summaries(bot, telegram_id, lang, report_type)

        except TelegramError as e:
            # Two very different things arrive as TelegramError, and filing
            # them the same way is what turned a single rate-limited second
            # into a lost report. `Forbidden` means the account blocked the
            # bot or is gone — nothing to retry. A timeout, a 429 or a 5xx
            # says nothing about the account and clears in seconds.
            permanent = isinstance(e, (Forbidden, BadRequest))
            log.warning("%s report to %s failed (%s): %s", report_type,
                        telegram_id, "permanent" if permanent else "will retry", e)
            with SessionLocal() as s:
                svc.mark_report_failed(s, report_id, str(e), permanent=permanent)
            failed += 1

        except (OperationalError, DBAPIError):
            # The database wobbled — a dropped connection, a failover, a
            # restart. That says nothing about this user, so marking the day
            # failed would throw away a report over a problem that is already
            # gone. Release the claim and let the next tick have it.
            log.exception("%s report for %s hit a database error — releasing "
                          "the claim for the next tick", report_type, telegram_id)
            try:
                with SessionLocal() as s:
                    svc.release_report(s, report_id, ws=ws,
                                       report_type=report_type, report_date=when)
            except Exception:
                log.exception("could not release claim %s", report_id)
            failed += 1

        except Exception as e:
            # Any other error must not abort the remaining recipients
            # (audit 037). The claim row stays, marked failed with the reason,
            # so today's report is not attempted again every two minutes for a
            # user whose send is going to keep failing.
            log.exception("%s report to %s errored", report_type, telegram_id)
            with SessionLocal() as s:
                svc.mark_report_failed(s, report_id, repr(e))
            failed += 1

        await asyncio.sleep(0.05)  # stay inside Telegram's rate limit

    detail = (" · ".join(f"{state}={n}" for state, n in sorted(taken_states.items()))
              or "none")
    log.info("%s report: %s sent, %s failed, %s not due yet, %s already "
             "in the outbox (%s)",
             report_type, sent, failed, not_due, taken, detail)
    # Every recipient due right now was refused the outbox and none of them
    # has it: that is not a busy day, it is the feature being off.
    if taken and not sent and not taken_states.get("sent"):
        log.warning("%s report: %s user(s) were due and none could be claimed "
                    "— outbox states: %s. Check /health/reports, and if this "
                    "says 'no row' run: python migrations.py 0010",
                    report_type, taken, detail)


async def send_reminders(bot) -> None:
    """Task and habit reminders whose moment has just arrived.

    Each reminder is marked sent the instant it goes out, so a job that runs
    every few minutes cannot repeat one. Reminders are opt-out for tasks and
    opt-in for habits: a notification a day is how an app gets muted.

    One recipient can never take the batch down with them. The reports job has
    worked this way since audit 037; this one did not, and the gap was real:
    only `TelegramError` was caught, and only around the send. Anything raised
    by the queries or by `mark_reminder_sent` — a closed connection, a row that
    vanished mid-pass — escaped the loop, and every user after the failure got
    nothing, silently, until the next tick.
    """
    try:
        with svc.JobLock(SessionLocal, "reminders") as lock:
            if not lock.acquired:
                return

            with SessionLocal() as s:
                recipients = svc.active_recipients(s)

            sent = failed = 0
            bot = AccountFanOut(bot)
            for telegram_id, ws, lang in recipients:
                try:
                    sent += await _send_user_reminders(bot, telegram_id, ws, lang)
                except Exception:
                    # Whatever went wrong here belongs to this user alone.
                    log.exception("reminders for %s errored", telegram_id)
                    failed += 1

                await asyncio.sleep(0.05)   # stay inside Telegram's rate limit

            if sent or failed:
                log.info("reminders: %s sent, %s recipients errored", sent, failed)
    except Exception:
        # Same gap the report jobs had: the per-user loop was guarded, the lock
        # and the recipient query around it were not.
        log.exception("reminder job failed before any recipient")


async def _send_user_reminders(bot, telegram_id: int, ws: int, lang: str) -> int:
    """Everything due for one recipient. Returns how many messages went out."""
    with SessionLocal() as s:
        user = s.get(User, telegram_id)
        if user is None:
            return 0
        # Read the zone while the row is still attached: everything below runs
        # after this session has closed.
        user_zone = svc.user_tz(user)
        tasks = svc.due_task_reminders(s, ws, user)
        habits = svc.due_habit_reminders(s, ws, user)

    sent = 0
    for task in tasks:
        text = (t(lang, "remind_task_at", title=esc(task["title"]),
                  time=task["due_time"]) if task["due_time"]
                else t(lang, "remind_task", title=esc(task["title"])))
        try:
            await bot.send_message(telegram_id, text,
                                   parse_mode=ParseMode.HTML,
                                   reply_markup=webapp_button(lang))
            # Marked only after Telegram accepted it, so a failure is
            # retried on the next pass instead of being lost.
            with SessionLocal() as s:
                svc.mark_reminder_sent(s, ws, task["id"])
            sent += 1
        except TelegramError as e:
            # Blocked, deleted, or simply unreachable: their next reminder is
            # not this one's problem, and neither is anybody else's.
            log.warning("task reminder to %s failed: %s", telegram_id, e)

    # Shared work is reminded on the same pass and marked per member, so the
    # two of you are each told once, in your own zone.
    with SessionLocal() as s:
        user = s.get(User, telegram_id)
        team_tasks = svc.due_team_task_reminders(s, telegram_id, user) if user else []
        team_habits = svc.due_team_habit_reminders(s, telegram_id, user) if user else []

    for item in team_tasks:
        try:
            body = (t(lang, "remind_task_at", title=esc(item["title"]),
                      time=item["due_time"]) if item["due_time"]
                    else t(lang, "remind_task", title=esc(item["title"])))
            await bot.send_message(
                telegram_id, f"{body}\n<i>👥 {esc(item['team_name'])}</i>",
                parse_mode=ParseMode.HTML, reply_markup=webapp_button(lang))
            with SessionLocal() as s:
                svc.mark_team_task_reminded(s, telegram_id, item["id"])
            sent += 1
        except TelegramError as e:
            log.warning("team task reminder to %s failed: %s", telegram_id, e)

    for item in team_habits:
        try:
            await bot.send_message(
                telegram_id,
                t(lang, "remind_habit", name=esc(item["name"]))
                + f"\n<i>👥 {esc(item['team_name'])}</i>",
                parse_mode=ParseMode.HTML)
            with SessionLocal() as s:
                svc.mark_team_habit_reminded(s, telegram_id, item["id"],
                                             tz=user_zone)
            sent += 1
        except TelegramError as e:
            log.warning("team habit reminder to %s failed: %s", telegram_id, e)

    for habit in habits:
        try:
            await bot.send_message(
                telegram_id, t(lang, "remind_habit", name=esc(habit["name"])),
                parse_mode=ParseMode.HTML)
            # Marked only once Telegram accepted it, exactly as task reminders
            # are, so a failure is retried rather than silently swallowed.
            with SessionLocal() as s:
                svc.mark_habit_reminder_sent(s, ws, habit["id"], tz=user_zone)
            sent += 1
        except TelegramError as e:
            log.warning("habit reminder to %s failed: %s", telegram_id, e)

    return sent


#: A timer that finished this long ago without being announced — the process
#: was down, most likely — is marked as told rather than announced hours late.
TIMER_ANNOUNCE_WINDOW = timedelta(hours=6)


async def tick_timers(bot) -> None:
    """Finish timers whose time is up, announce them, keep the rest counting.

    Runs every half minute. Finishing happens in the database first, so the
    habit or task is ticked even if the message cannot be delivered; the
    announcement is claimed per run, so a second instance cannot send it
    twice. Never raises into the scheduler.
    """
    try:
        with svc.JobLock(SessionLocal, "timers") as lock:
            if not lock.acquired:
                return
            with SessionLocal() as s:
                svc.settle_timers(s)
                finished = [r.id for r in svc.unannounced_timers(s)]
                live = [r.id for r in svc.live_timer_messages(s)]
            for run_id in finished:
                try:
                    await _announce_timer(bot, run_id)
                except Exception:
                    log.exception("announcing timer %s failed", run_id)
            for run_id in live:
                try:
                    await _refresh_timer_message(bot, run_id)
                except Exception:
                    log.exception("refreshing timer %s failed", run_id)
    except Exception:
        log.exception("timer job failed")


def _timer_owner(s, run) -> User | None:
    owner = svc.workspace_owner(s, run.workspace_id)
    return s.get(User, owner) if owner else None


async def _announce_timer(bot, run_id: int) -> bool:
    """Tell the owner their timer ran out and the item is done. True if sent."""
    from db import TimerRun

    with SessionLocal() as s:
        if not svc.claim_timer_notice(s, run_id):
            return False
        run = s.get(TimerRun, run_id)
        user = _timer_owner(s, run) if run else None
        if run is None or user is None:
            return False
        if run.finished_at and run.finished_at < db.utcnow() - TIMER_ANNOUNCE_WINDOW:
            return False
        lang = user.language or "uz"
        chat_id, message_id = run.chat_id, run.message_id
        kind, title, minutes = run.kind, run.title, run.duration_sec // 60
        telegram_id = user.telegram_id

    text = t(lang, "timer_finished_habit" if kind == "habit"
             else "timer_finished_task",
             title=esc(title), dur=fmt_minutes(minutes, lang))
    # The message that was counting down stops, and says why.
    if chat_id and message_id:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                        text=text, parse_mode=ParseMode.HTML)
        except TelegramError:
            pass
    try:
        # A new message as well, because an edit does not make the phone ring.
        await bot.send_message(telegram_id, text, parse_mode=ParseMode.HTML)
        return True
    except (Forbidden, BadRequest) as e:
        log.info("timer %s announcement refused: %s", run_id, e)
    except TelegramError as e:
        # Transient: give the claim back so the next tick tries again.
        log.warning("timer %s announcement failed, will retry: %s", run_id, e)
        with SessionLocal() as s:
            svc.release_timer_notice(s, run_id)
    return False


async def _refresh_timer_message(bot, run_id: int) -> None:
    """Redraw a running timer's bot message with the time now left."""
    from db import TimerRun

    with SessionLocal() as s:
        run = s.get(TimerRun, run_id)
        if run is None or run.status != "running" or not run.message_id:
            return
        user = _timer_owner(s, run)
        if user is None:
            return
        try:
            info = svc.timer_for(s, run.workspace_id, run.kind, run.item_id,
                                 tz=svc.user_tz(user))
        except svc.NotFound:
            return
        lang = user.language or "uz"
        chat_id, message_id = run.chat_id, run.message_id
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                    text=render_timer(info, lang),
                                    parse_mode=ParseMode.HTML,
                                    reply_markup=timer_keyboard(info, lang))
    except BadRequest as e:
        reason = str(e).lower()
        if "not modified" in reason:
            return
        # Deleted, or too old to edit: stop trying to redraw it.
        with SessionLocal() as s:
            row = s.get(TimerRun, run_id)
            if row is not None:
                row.message_id = None
                s.commit()


# ---------------------------------------------------------------------------
# Mini App authentication
# ---------------------------------------------------------------------------
#
# The signature check and the access gates live in `security`, beside the
# escaping they sit next to conceptually. Re-exported here: every endpoint
# calls `auth(init)`, and the middleware calls `verify_init_data` to bucket the
# rate limiter by a Telegram id it can actually trust.

verify_init_data = security.verify_init_data
auth = security.auth
issue_avatar_token = security.issue_avatar_token
verify_avatar_token = security.verify_avatar_token


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------

telegram_app: Application | None = None
#: The running APScheduler, or None when there is no bot to schedule for.
scheduler = None

#: The screens reachable by command as well as by keyboard button. `/start`,
#: `/home` and `/guide` are registered separately because they are also the
#: entry points, and must work before onboarding finishes.
ROLE_ICON = {"owner": "👑", "admin": "⭐", "member": "👤"}


def _invite_status(info: dict, lang: str, tz) -> str:
    """`🔗 Havola 2-oktabr 14:00 gacha ishlaydi` — or that it no longer does."""
    if info.get("expired") or not info.get("expires_at"):
        return t(lang, "team_invite_expired")
    until = datetime.fromisoformat(info["expires_at"]).replace(
        tzinfo=svc._utc.utc).astimezone(tz)
    return t(lang, "team_invite_until",
             when=f"{short_date(until.date().isoformat(), lang)} {until:%H:%M}")


def render_team_units(summary: dict, lang: str) -> str:
    """`5 ta ish · 10 ta tasdiq · 9 tasi qolgan` — three countable numbers."""
    units = summary.get("units") or {}
    if not units.get("items"):
        return t(lang, "team_nothing_today")
    return t(lang, "team_units", items=units["items"],
             confirmations=units["confirmations"], left=units["left"])


async def show_teams(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *,
                     edit: bool = False) -> None:
    """The team screen: every team, today's count, and a way into each."""
    got = await guard(update, ctx, write=False)
    if not got:
        return
    user, _ = got
    lang = user.language
    tz = svc.user_tz(user)

    with SessionLocal() as s:
        teams = svc.teams_for(s, user.telegram_id)
        summaries = [(team, svc.team_day_summary(s, team.id, tz=tz),
                      svc.team_members(s, team.id),
                      svc.role_of(s, team.id, user.telegram_id)) for team in teams]
        offers = [team for team in teams if team.pending_owner_id == user.telegram_id]

    if not teams:
        await _show(update, t(lang, "team_none"), InlineKeyboardMarkup([[
            InlineKeyboardButton(t(lang, "team_create_btn"), callback_data="team:new")]]),
            edit=edit)
        return

    lines = [t(lang, "team_list_title"), ""]
    rows = []
    for team, summary, members, role in summaries:
        lines.append(f"<b>{esc(team.name)}</b> · {ROLE_ICON.get(role, '👤')} "
                     f"{t(lang, 'role_' + (role or 'member'))}")
        lines.append(f"<i>{', '.join(esc(m['name']) for m in members)}</i>")
        lines.append(render_team_units(summary, lang))
        for member in summary.get("members", []):
            percent = member["percent"]
            lines.append(f"   {member['done']}/{member['total']} · "
                         f"{esc(member['name'])}"
                         + (f" · {percent}%" if percent is not None else ""))
        lines.append("")
        rows.append([InlineKeyboardButton(f"👥 {team.name[:30]}",
                                          callback_data=f"team:open:{team.id}")])
    for team in offers:
        lines.append(t(lang, "team_owner_offer", name=esc(team.name)))
        rows.append([InlineKeyboardButton(t(lang, "accept"), callback_data=f"town:a:{team.id}"),
                     InlineKeyboardButton(t(lang, "decline"), callback_data=f"town:d:{team.id}")])
    if len(teams) < svc.MAX_TEAMS_PER_USER:
        rows.append([InlineKeyboardButton(t(lang, "team_create_btn"),
                                          callback_data="team:new")])
    await _show(update, "\n".join(lines).rstrip(), InlineKeyboardMarkup(rows), edit=edit)


async def show_team(update: Update, ctx: ContextTypes.DEFAULT_TYPE, user: User,
                    team_id: int, *, edit: bool = True) -> None:
    """One team: who is in it and as what, the invite, and your own settings."""
    lang = user.language
    tz = svc.user_tz(user)
    with SessionLocal() as s:
        team = svc.team_for(s, user.telegram_id, team_id)
        if team is None:
            await _notice(update, t(lang, "not_found"))
            return
        members = svc.team_members(s, team_id)
        perms = svc.team_permissions(s, team_id, user.telegram_id)
        summary = svc.team_day_summary(s, team_id, tz=tz)
        invite = svc.invite_info(team)
        notify = svc.member_notify(s, team_id, user.telegram_id)
        requests = (svc.list_join_requests(s, user.telegram_id, team_id)
                    if perms["approve"] else [])
        name = team.name

    lines = [f"<b>👥 {esc(name)}</b>", render_team_units(summary, lang), ""]
    for m in members:
        you = f" ({t(lang, 'you')})" if m["user_id"] == user.telegram_id else ""
        lines.append(f"{ROLE_ICON[m['role']]} {esc(m['name'])}{you} · "
                     f"{t(lang, 'role_' + m['role'])}")
    lines.append("")
    if perms["invite"]:
        lines.append(_invite_status(invite, lang, tz))
        if invite["approval"]:
            lines.append(t(lang, "team_approval_on"))
    lines.append(t(lang, "team_notify_now", level=t(lang, f"notify_{notify}")))
    if requests:
        lines.append("")
        lines.append(t(lang, "team_requests", n=len(requests)))

    rows = []
    if perms["invite"]:
        rows.append([InlineKeyboardButton(t(lang, "team_invite_btn"),
                                          callback_data=f"team:invite:{team_id}"),
                     InlineKeyboardButton(t(lang, "team_revoke_btn"),
                                          callback_data=f"team:revoke:{team_id}")])
        rows.append([InlineKeyboardButton(
            t(lang, "team_approval_off_btn" if invite["approval"] else "team_approval_on_btn"),
            callback_data=f"team:approval:{team_id}:{0 if invite['approval'] else 1}")])
    for request in requests[:5]:
        rows.append([InlineKeyboardButton(f"✅ {request['name'][:24]}",
                                          callback_data=f"treq:a:{request['id']}"),
                     InlineKeyboardButton("✖️", callback_data=f"treq:d:{request['id']}")])
    rows.append([InlineKeyboardButton(t(lang, "btn_projects"), callback_data="pj:list"),
                 InlineKeyboardButton(t(lang, "team_stats_btn"),
                                      callback_data=f"team:stats:{team_id}")])
    rows.append([InlineKeyboardButton(t(lang, "team_notify_btn"),
                                      callback_data=f"team:notify:{team_id}")])
    if perms["roles"] or perms["remove_members"]:
        rows.append([InlineKeyboardButton(t(lang, "team_members_btn"),
                                          callback_data=f"team:members:{team_id}")])
    if perms["rename"]:
        rows.append([InlineKeyboardButton(t(lang, "team_rename_btn"),
                                          callback_data=f"team:rename:{team_id}")])
    rows.append([InlineKeyboardButton(t(lang, "team_leave_btn"),
                                      callback_data=f"team:leave:{team_id}")])
    if perms["role"] == "owner":
        rows.append([InlineKeyboardButton(t(lang, "team_delete_btn"),
                                          callback_data=f"team:del:{team_id}")])
    rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="team:list")])
    await _show(update, "\n".join(lines), InlineKeyboardMarkup(rows), edit=edit)


async def send_team_invite(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                           team_id: int) -> None:
    """Hand the user the link that adds somebody to this team.

    An expired link is renewed first — asking for the link is asking for one
    that works. Owners and admins only.
    """
    got = await guard(update, ctx)
    if not got:
        return
    user, _ = got
    lang = user.language
    message = update.effective_message

    try:
        with SessionLocal() as s:
            team = svc.team_for(s, user.telegram_id, team_id)
            if team is None:
                await message.reply_text(t(lang, "error"))
                return
            if not svc.team_permissions(s, team_id, user.telegram_id)["invite"]:
                raise PermissionError("forbidden")
            if svc.invite_info(team)["expired"] or team.code_expires_at is None:
                team = svc.renew_invite(s, user.telegram_id, team_id)
            link = svc.team_invite_link(team, BOT_USERNAME)
            name, info = team.name, svc.invite_info(team)
    except PermissionError:
        await _notice(update, t(lang, "team_admin_only"))
        return

    if not link:
        # Without a configured @name there is no link to give, and inventing
        # one would produce something that silently does not work.
        await message.reply_text(t(lang, "ref_not_configured"))
        return
    await message.reply_text(
        t(lang, "team_invite_text", name=esc(name)) + f"\n\n{link}\n\n"
        + _invite_status(info, lang, svc.user_tz(user)),
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def preview_team_invite(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                              code: str) -> None:
    """What opening an invite shows: the team, who runs it, how full it is —
    and a button to join. Nobody is added to anything by opening a link."""
    uid = account_of(update)
    message = update.effective_message
    with SessionLocal() as s:
        user = s.get(User, uid)
        lang = user.language if user else "uz"
        info = svc.preview_invite(s, code)
        already = bool(info and svc.team_for(s, uid, info["team_id"]))
    if info is None:
        await message.reply_text(t(lang, "team_unknown", name=""), parse_mode=ParseMode.HTML)
        return
    if already:
        await message.reply_text(t(lang, "team_already", name=esc(info["name"])),
                                 parse_mode=ParseMode.HTML)
        return
    if info["expired"]:
        await message.reply_text(t(lang, "team_link_expired", name=esc(info["name"])),
                                 parse_mode=ParseMode.HTML)
        return
    ctx.user_data["team_invite"] = code
    text = t(lang, "team_preview", name=esc(info["name"]), owner=esc(info["owner"]),
             n=info["members"], max=info["max_members"])
    if info["approval"]:
        text += "\n" + t(lang, "team_preview_approval")
    await message.reply_text(text, parse_mode=ParseMode.HTML,
                             reply_markup=InlineKeyboardMarkup([[
                                 InlineKeyboardButton(t(lang, "team_join_btn"),
                                                      callback_data="tjoin:y"),
                                 InlineKeyboardButton(t(lang, "decline"),
                                                      callback_data="tjoin:x")]]))


async def answer_team_invite(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                             answer: str) -> None:
    """The tap on the invite preview: join (or ask to), or not."""
    uid = account_of(update)
    query = update.callback_query
    code = ctx.user_data.pop("team_invite", None)
    with SessionLocal() as s:
        user = s.get(User, uid)
        lang = user.language if user else "uz"
        onboarded = bool(user and user.onboarded)
        joiner = ((user.first_name if user else "") or "").strip() or str(uid)
    if answer != "y" or not code:
        await query.edit_message_text(t(lang, "cancelled") if answer != "y"
                                      else t(lang, "flow_expired"))
        return

    with SessionLocal() as s:
        team, outcome = svc.join_team(s, uid, code)
        name = team.name if team else ""
        team_id = team.id if team else None
        recipients = (svc.team_recipients(s, team_id, uid, "join")
                      if team and outcome == "joined" else [])
        approvers = ([(m["user_id"], m["language"]) for m in svc.team_members(s, team_id)
                      if m["role"] in ("owner", "admin")]
                     if team and outcome == "requested" else [])

    key = {"joined": "team_joined", "already": "team_already",
           "full": "team_full", "unknown": "team_unknown",
           "expired": "team_link_expired", "requested": "team_requested"}[outcome]
    await query.edit_message_text(t(lang, key, name=esc(name)),
                                  parse_mode=ParseMode.HTML)

    # Each teammate hears it in their own language, and only if they asked to.
    for member_id, member_lang in recipients:
        try:
            await ctx.bot.send_message(
                member_id, t(member_lang, "team_member_joined", who=esc(joiner),
                             name=esc(name)), parse_mode=ParseMode.HTML)
        except TelegramError as e:
            log.info("could not tell %s about a new member: %s", member_id, e)
    for member_id, member_lang in approvers:
        try:
            await ctx.bot.send_message(
                member_id, t(member_lang, "team_request_new", who=esc(joiner),
                             name=esc(name)), parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                    t(member_lang, "team_open_btn"), callback_data=f"team:open:{team_id}")]]))
        except TelegramError as e:
            log.info("could not tell %s about a join request: %s", member_id, e)
    if outcome == "joined" and onboarded:
        await show_teams(update, ctx)


async def route_team(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                     action: str, parts: list[str], user: User, lang: str) -> None:
    """team:* · treq (join requests) · tnot (notify level) · town (ownership) · trole."""
    query = update.callback_query
    uid = user.telegram_id
    what = parts[1] if len(parts) > 1 else ""

    try:
        if action == "treq":
            with SessionLocal() as s:
                request, team, outcome = svc.decide_join_request(
                    s, uid, int(parts[2]), parts[1] == "a")
                joiner = s.get(User, request.user_id)
                joiner_lang = joiner.language if joiner else "uz"
                team_id, name = team.id, team.name
            key = {"approved": "team_joined", "declined": "team_request_declined",
                   "full": "team_full"}.get(outcome, "team_already")
            try:
                await ctx.bot.send_message(request.user_id,
                                           t(joiner_lang, key, name=esc(name)),
                                           parse_mode=ParseMode.HTML)
            except TelegramError as e:
                log.info("could not answer join request %s: %s", request.id, e)
            await show_team(update, ctx, user, team_id)
            return

        if action == "tnot":
            team_id, level = int(parts[1]), parts[2]
            with SessionLocal() as s:
                svc.set_team_notify(s, uid, team_id, level)
            await show_team(update, ctx, user, team_id)
            return

        if action == "town":
            with SessionLocal() as s:
                team = svc.answer_ownership(s, uid, int(parts[2]), parts[1] == "a")
                name = team.name
            await _show(update, t(lang, "team_owner_now" if parts[1] == "a"
                                  else "team_owner_declined", name=esc(name)),
                        None, edit=True)
            return

        if action == "trole":
            # trole:<team>:<member>:<a|m|x|o>
            team_id, member_id, op = int(parts[1]), int(parts[2]), parts[3]
            with SessionLocal() as s:
                if op == "x":
                    svc.remove_member(s, uid, team_id, member_id)
                elif op == "o":
                    svc.offer_ownership(s, uid, team_id, member_id)
                    target = s.get(User, member_id)
                    target_lang = target.language if target else "uz"
                    team_name = svc.team_for(s, uid, team_id).name
                else:
                    svc.set_member_role(s, uid, team_id, member_id,
                                        "admin" if op == "a" else "member")
            if op == "o":
                try:
                    await ctx.bot.send_message(
                        member_id, t(target_lang, "team_owner_offer", name=esc(team_name)),
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton(t(target_lang, "accept"),
                                                 callback_data=f"town:a:{team_id}"),
                            InlineKeyboardButton(t(target_lang, "decline"),
                                                 callback_data=f"town:d:{team_id}")]]))
                except TelegramError as e:
                    log.info("could not offer ownership to %s: %s", member_id, e)
                await _notice(update, t(lang, "team_owner_offered"))
            await show_team_members(update, ctx, user, team_id)
            return

        if what == "new":
            start_flow(ctx, "team_name")
            await query.edit_message_text(t(lang, "team_ask_name"))
        elif what == "list":
            await show_teams(update, ctx, edit=True)
        elif what == "open":
            await show_team(update, ctx, user, int(parts[2]))
        elif what == "invite":
            await send_team_invite(update, ctx, int(parts[2]))
        elif what == "revoke":
            with SessionLocal() as s:
                svc.revoke_invite(s, uid, int(parts[2]))
            await _notice(update, t(lang, "team_invite_revoked"))
            await show_team(update, ctx, user, int(parts[2]))
        elif what == "approval":
            with SessionLocal() as s:
                svc.set_invite_approval(s, uid, int(parts[2]), parts[3] == "1")
            await show_team(update, ctx, user, int(parts[2]))
        elif what == "rename":
            start_flow(ctx, "team_rename", team_id=int(parts[2]))
            await query.edit_message_text(t(lang, "team_ask_rename"))
        elif what == "notify":
            team_id = int(parts[2])
            rows = [[InlineKeyboardButton(t(lang, f"notify_{level}"),
                                          callback_data=f"tnot:{team_id}:{level}")]
                    for level in svc.NOTIFY_LEVELS]
            rows.append([InlineKeyboardButton(t(lang, "back"),
                                              callback_data=f"team:open:{team_id}")])
            await _show(update, t(lang, "team_notify_ask"), InlineKeyboardMarkup(rows),
                        edit=True)
        elif what == "stats":
            team_id = int(parts[2])
            with SessionLocal() as s:
                stats = {"name": svc.team_for(s, uid, team_id).name,
                         "board": svc.team_scoreboard(s, uid, team_id,
                                                      tz=svc.user_tz(user))}
            await _show(update, "\n".join(render_team_stats(stats, lang)),
                        InlineKeyboardMarkup([[InlineKeyboardButton(
                            t(lang, "back"), callback_data=f"team:open:{team_id}")]]),
                        edit=True)
        elif what == "members":
            await show_team_members(update, ctx, user, int(parts[2]))
        elif what == "leave":
            await _show(update, t(lang, "team_leave_confirm"), InlineKeyboardMarkup([
                [InlineKeyboardButton(t(lang, "team_leave_btn"),
                                      callback_data=f"team:leavey:{parts[2]}")],
                [InlineKeyboardButton(t(lang, "back"),
                                      callback_data=f"team:open:{parts[2]}")]]), edit=True)
        elif what == "leavey":
            with SessionLocal() as s:
                svc.leave_team(s, uid, int(parts[2]))
            await _show(update, t(lang, "team_left"), None, edit=True)
        elif what == "del":
            await _show(update, t(lang, "team_delete_confirm"), InlineKeyboardMarkup([
                [InlineKeyboardButton(t(lang, "team_delete_btn"),
                                      callback_data=f"team:dely:{parts[2]}")],
                [InlineKeyboardButton(t(lang, "back"),
                                      callback_data=f"team:open:{parts[2]}")]]), edit=True)
        elif what == "dely":
            team_id = int(parts[2])
            with SessionLocal() as s:
                # Everybody hears this one, whatever their level: the team
                # itself is going.
                recipients = [(m["user_id"], m["language"])
                              for m in svc.team_members(s, team_id) if m["user_id"] != uid]
                name = svc.delete_team(s, uid, team_id)
            await _show(update, t(lang, "team_deleted", name=esc(name)), None, edit=True)
            for member_id, member_lang in recipients:
                try:
                    await ctx.bot.send_message(
                        member_id, t(member_lang or "uz", "team_deleted", name=esc(name)),
                        parse_mode=ParseMode.HTML)
                except TelegramError as e:
                    log.info("could not tell %s the team was deleted: %s", member_id, e)
    except PermissionError:
        await _notice(update, t(lang, "team_admin_only"))
    except ValueError as e:
        reason = str(e)
        await _notice(update, t(lang, {"owner_must_transfer": "team_owner_must_transfer",
                                       "cannot_remove": "error",
                                       "no_offer": "flow_expired"}.get(reason, "error")))


async def show_team_members(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                            user: User, team_id: int) -> None:
    """Roles, for the owner; removal, for owner and admins."""
    lang = user.language
    with SessionLocal() as s:
        members = svc.team_members(s, team_id)
        perms = svc.team_permissions(s, team_id, user.telegram_id)
    rows = []
    for m in members:
        if m["user_id"] == user.telegram_id or m["role"] == "owner":
            continue
        row = [InlineKeyboardButton(f"{ROLE_ICON[m['role']]} {m['name'][:18]}",
                                    callback_data="habit:noop")]
        if perms["roles"]:
            op = "m" if m["role"] == "admin" else "a"
            row.append(InlineKeyboardButton(
                t(lang, "role_make_member" if op == "m" else "role_make_admin"),
                callback_data=f"trole:{team_id}:{m['user_id']}:{op}"))
        if perms["remove_members"] and (perms["roles"] or m["role"] == "member"):
            row.append(InlineKeyboardButton("🚫", callback_data=f"trole:{team_id}:{m['user_id']}:x"))
        rows.append(row)
        if perms["transfer"]:
            rows.append([InlineKeyboardButton(t(lang, "role_give_owner", name=m["name"][:18]),
                                              callback_data=f"trole:{team_id}:{m['user_id']}:o")])
    rows.append([InlineKeyboardButton(t(lang, "back"), callback_data=f"team:open:{team_id}")])
    text = t(lang, "team_members_title")
    if len(rows) == 1:
        text += "\n\n" + t(lang, "empty")
    await _show(update, text, InlineKeyboardMarkup(rows), edit=True)


async def send_report_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                          report_type: str) -> None:
    """Build and send one report to whoever asked, right now.

    The scheduled path decides *whether* to send; this decides nothing. It is
    the same data and the same renderer, reached without the clock, the
    recipient query, the job lock or the outbox — so when the daily report is
    not arriving, this separates "the report cannot be built or delivered"
    from "the schedule never reached this account". It is also simply worth
    having: wanting today's summary before bedtime is a reasonable thing.
    """
    got = await guard(update, ctx)
    if not got:
        return
    user, ws = got
    lang = user.language
    message = update.effective_message
    try:
        with SessionLocal() as s:
            row = s.get(User, user.telegram_id)
            data = (svc.morning_data(s, ws, row) if report_type == "morning"
                    else svc.evening_data(s, ws, row))
        text = (render_morning(data, lang) if report_type == "morning"
                else render_evening(data, lang))
        await message.reply_text(text, parse_mode=ParseMode.HTML,
                                 reply_markup=webapp_button(lang))
    except Exception:
        # The reason goes to the log, never to the user — but they still need
        # to be told it failed rather than watching nothing happen.
        log.exception("manual %s report for %s failed",
                      report_type, user.telegram_id)
        await message.reply_text(t(lang, "error"))


async def show_report_health(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Why this account is or is not getting its daily reports.

    The same facts `/health/reports` exposes, for one account, delivered in the
    chat — because the person who notices that reports stopped is the person
    holding a phone, not a terminal with the database password in it. Nothing
    here is a secret from the account it describes.
    """
    got = await guard(update, ctx)
    if not got:
        return
    user, ws = got
    message = update.effective_message

    with SessionLocal() as s:
        row = s.get(User, user.telegram_id)
        recipients = {r[0] for r in svc.active_recipients(s)}
        tz = svc.user_tz(row)
        now = svc.now_local(tz)
        prefs = svc.prefs_for(row)
        today = svc.today_local(tz)
        from db import DailyReportLog
        logs = s.scalars(select(DailyReportLog).where(
            DailyReportLog.workspace_id == ws,
            DailyReportLog.report_date == today)).all()

    def mark(value: bool) -> str:
        return "✅" if value else "❌"

    is_recipient = user.telegram_id in recipients
    lines = [
        "<b>🩺 Hisobot tekshiruvi</b>", "",
        f"{mark(bool(scheduler and scheduler.running))} Scheduler ishlayapti",
        f"{mark(is_recipient)} Siz qabul qiluvchilar ro'yxatidasiz",
        f"{mark(bool(row.onboarded))} Onboarding tugagan",
        "",
        f"🕐 Sizning vaqtingiz: <b>{now:%H:%M}</b> ({esc(prefs['timezone'])})",
        f"🌅 Ertalabki: {mark(prefs['morning_report'])} {prefs['morning_time']}"
        f" — hozir navbatda: {mark(svc.report_is_due(row, 'morning', now))}",
        f"🌙 Kechqurungi: {mark(prefs['evening_report'])} {prefs['evening_time']}"
        f" — hozir navbatda: {mark(svc.report_is_due(row, 'evening', now))}",
    ]

    if not is_recipient:
        lines += ["", "<b>⚠️ Siz ro'yxatda yo'qsiz — sabab shu.</b>",
                  f"Obuna: {mark(bool(row.is_subscribed))} · "
                  f"harakatlar: {row.actions_count or 0}"]

    refusals = sum(svc.LOCK_REFUSALS.values())
    if refusals:
        lines += ["", f"⚠️ Job lock {refusals} marta rad etilgan"]

    # A claim the database refused while leaving the slot empty means the
    # table's unique constraint is not the one this code expects, and every
    # report after the first is being dropped. Worth shouting about.
    if svc.CLAIM_ANOMALIES.get("count"):
        lines += ["", "<b>🚨 Bazada eski constraint bor.</b>",
                  f"Rad etilgan claim: {svc.CLAIM_ANOMALIES['count']}",
                  "Serverda ishga tushiring: <code>python migrations.py 0010</code>"]

    lines += ["", f"<b>Bugungi yozuvlar</b> ({today})"]
    if not logs:
        lines.append("<i>Hali hech narsa yozilmagan — hali navbat kelmagan "
                     "yoki job umuman ishlamayapti.</i>")
    for entry in logs:
        line = f"• {entry.report_type}: <b>{entry.status}</b> ({entry.attempts})"
        if entry.last_error:
            line += f"\n   <i>{esc(entry.last_error[:120])}</i>"
        lines.append(line)

    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


BOT_COMMANDS = [
    # Signing in with a login from another Telegram, signing out of it, and
    # the account screen itself.
    ("login", lambda u, c: cmd_login(u, c)),
    ("logout", lambda u, c: cmd_logout(u, c)),
    ("account", lambda u, c: show_account(u, c)),
    ("projects", lambda u, c: show_projects(u, c)),
    ("tasks", lambda u, c: show_tasks(u, c)),
    ("habits", lambda u, c: show_habits(u, c)),
    ("stats", lambda u, c: show_stats(u, c)),
    ("settings", lambda u, c: show_settings(u, c)),
    ("invite", lambda u, c: show_invite(u, c)),
    # Ask for today's report without waiting for its hour, and find out why
    # the scheduled one did not arrive. Both exist because "the reports
    # stopped" was previously answerable only from the server.
    ("hisobot", lambda u, c: send_report_now(u, c, "evening")),
    ("ertalabki", lambda u, c: send_report_now(u, c, "morning")),
    ("tekshir", lambda u, c: show_report_health(u, c)),
    ("jamoa", lambda u, c: show_teams(u, c)),
    ("pul", lambda u, c: show_money(u, c)),
    ("money", lambda u, c: show_money(u, c)),
    # The running timer, or the list to start one; and the dates being
    # counted down to. Typed only: no keyboard offers them any more — both
    # countdowns live in the Mini App.
    ("timer", lambda u, c: show_active_timer(u, c)),
    ("countdown", lambda u, c: show_countdowns(u, c)),
]



@asynccontextmanager
async def lifespan(_: FastAPI):
    """Start the database, the Telegram bot and the scheduler together."""
    global telegram_app, scheduler, BOT_USERNAME

    db.init_db()
    log.info("database ready: %s", db.engine.url.render_as_string(hide_password=True))

    # Advisory locks are held by a *connection*, and a process that is killed
    # mid-job leaves its lock behind until the database notices the connection
    # is gone — which, behind a pooler, can be a long time. Until then every
    # report tick finds the job "already running elsewhere" and returns without
    # sending anything. This releases only what this brand-new connection
    # holds, which is nothing, plus whatever the pooler handed back to us; it
    # cannot disturb another live instance's locks.
    if db.engine.dialect.name == "postgresql":
        try:
            with SessionLocal() as s:
                s.execute(sql_text("SELECT pg_advisory_unlock_all()"))
                s.commit()
            log.info("released any advisory locks left by a previous process")
        except Exception:
            log.exception("could not release stale advisory locks")
    # Print where each stream goes, so "stats landed in the log channel" is
    # diagnosable from the deploy log instead of guesswork.
    log.info("channels — events:%s feedback:%s stats:%s",
             ADMIN_LOG_CHANNEL_ID or "off",
             FEEDBACK_CHANNEL_ID or "off",
             STATS_CHANNEL_ID or "off")
    if STATS_CHANNEL_ID and STATS_CHANNEL_ID == ADMIN_LOG_CHANNEL_ID:
        log.warning("STATS_CHANNEL_ID is unset — statistics fall back to the "
                    "event log channel. Set it to use a dedicated channel.")

    # ENVIRONMENT=test runs the API alone, so the suite never dials Telegram.
    if BOT_TOKEN and ENVIRONMENT != "test":
        telegram_app = (Application.builder().token(BOT_TOKEN)
                        .concurrent_updates(True).build())

        telegram_app.add_handler(CommandHandler("start", start))
        telegram_app.add_handler(CommandHandler("home", show_home))
        telegram_app.add_handler(CommandHandler("guide", show_guide))
        # Every screen the keyboard offers also has a command. Somebody who
        # cleared the reply keyboard, or who simply types faster than they tap,
        # was previously stuck with three commands and no way to reach the rest.
        for command, handler in BOT_COMMANDS:
            telegram_app.add_handler(CommandHandler(command, handler))
        telegram_app.add_handler(MessageHandler(filters.CONTACT, on_contact))
        telegram_app.add_handler(MessageHandler(filters.PHOTO, on_photo))
        telegram_app.add_handler(CallbackQueryHandler(on_callback))
        telegram_app.add_handler(ChatMemberHandler(
            on_chat_member, ChatMemberHandler.CHAT_MEMBER))
        telegram_app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, on_text))
        telegram_app.add_error_handler(on_error)

        await telegram_app.initialize()
        await telegram_app.start()
        if WEBAPP_URL:
            # The keyboard has no Mini App button any more: the chat's own
            # menu button (next to the text field) opens it instead.
            try:
                await telegram_app.bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp("ErnestOS", WebAppInfo(url=WEBAPP_URL)))
            except TelegramError:
                log.warning("could not set the Mini App menu button")
        if WEBHOOK_URL:
            # One HTTP call per update instead of a permanent long-poll. Worth
            # it once there are enough users that polling is the process's main
            # activity, and required behind a load balancer, where several
            # instances cannot all poll the same bot.
            await telegram_app.bot.set_webhook(
                WEBHOOK_URL, allowed_updates=ALLOWED_UPDATES,
                secret_token=WEBHOOK_SECRET,
                drop_pending_updates=False)
            log.info("telegram bot on webhook: %s", WEBHOOK_URL)
        else:
            await telegram_app.updater.start_polling(
                allowed_updates=ALLOWED_UPDATES,
                # Dropping these loses whatever users tapped during a deploy
                # (audit 033). Handlers are idempotent, so replaying is safer.
                drop_pending_updates=False)
            log.info("telegram bot polling")

        if not BOT_USERNAME:
            try:
                me = await telegram_app.bot.get_me()
                if me.username:
                    BOT_USERNAME = me.username
                    log.info("bot username resolved from Telegram: @%s",
                             BOT_USERNAME)
            except Exception:
                log.exception("could not ask Telegram for the bot username — "
                              "invite links will be unavailable until "
                              "BOT_USERNAME is set")

        # When the jobs run, and the duplicate-run guarantees, live in
        # `scheduler`. What each job *says* stays here, next to the renderers.
        scheduler = scheduling.start(
            telegram_app.bot,
            send_reports=send_reports,
            send_reminders=send_reminders,
            send_platform_stats=send_platform_stats_tick,
            tick_timers=tick_timers)
    else:
        log.warning("BOT_TOKEN missing — API only, no bot and no scheduler")

    yield

    if scheduler:
        scheduler.shutdown(wait=False)
    if telegram_app:
        if not WEBHOOK_URL:
            await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()


app = FastAPI(title="ErnestOS", lifespan=lifespan)

#: Per-user token buckets. Reads are cheap, writes cost more, and exports hit
#: Telegram, so each class gets its own budget (audit 012).
RATE_LIMITS = {"read": (60, 60), "write": (30, 60), "heavy": (5, 60)}
#: The suite drives hundreds of writes as one user in a few seconds, which is
#: not the traffic this limit describes. Tests exercise it explicitly instead.
RATE_LIMIT_ENABLED = ENVIRONMENT != "test"

#: The limiter itself lives in `ratelimit`, behind an interface, so replacing
#: this process-local dictionary with Redis later is one class and one line
#: rather than a hunt through the middleware.
limiter = ratelimit.InMemoryRateLimiter(RATE_LIMITS)
#: The bucket store, exposed under its historical name. Same object, so
#: `_buckets.clear()` still empties the live limiter.
_buckets = limiter._hits


def _rate_class(request: Request) -> str:
    path = request.url.path
    if path.startswith(("/api/stats/export", "/api/avatar")):
        return "heavy"
    return "read" if request.method == "GET" else "write"


def rate_limit_check(key: int, bucket: str) -> int | None:
    """Return seconds to wait when over budget, else None."""
    return limiter.check(key, bucket)


@app.middleware("http")
async def guard_requests(request: Request, call_next):
    """Body-size and rate limits, applied before any handler runs."""
    if not request.url.path.startswith("/api/"):
        return await call_next(request)

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "payload_too_large"})

    # Bucket by Telegram id when the signature is valid, else by client host —
    # an unauthenticated flood should not be free either.
    key, init = 0, request.headers.get("x-telegram-init-data")
    if init:
        try:
            key = int(verify_init_data(init)["id"])
        except HTTPException:
            key = 0
    if key:
        # A Telegram signed in with a login counts, spends and is limited as
        # the account it signed in to — the same account `auth` will serve.
        key = accounts.resolve_id(key)
    if not key:
        host = request.client.host if request.client else "unknown"
        # Offset by one: `hash(host) % 10_000_000 == 0` would produce key 0,
        # which is the sentinel for "no identified user" a few lines above, so
        # one unlucky address would share a bucket with that branch.
        key = -(1 + abs(hash(host)) % 9_999_999)

    bucket = _rate_class(request)
    retry_after = rate_limit_check(key, bucket) if RATE_LIMIT_ENABLED else None
    if retry_after is not None:
        log.info("rate limit hit: key=%s bucket=%s", key, bucket)
        return JSONResponse(status_code=429, content={"detail": "rate_limited"},
                            headers={"Retry-After": str(retry_after)})

    security.REQUEST.set((request.method, request.url.path))

    # A create sent twice — a double tap, or a retry after the phone lost the
    # answer — is answered from the first attempt instead of writing twice.
    idem_key = request.headers.get("x-idempotency-key", "")
    idem_row = None
    if (key > 0 and idem_key and request.method == "POST"
            and svc.IDEMPOTENCY_KEY_RE.match(idem_key)):
        with SessionLocal() as s:
            claim = svc.idempotency_begin(s, key, idem_key, request.url.path)
        if claim[0] == "done":
            return Response(content=claim[2] or "{}", status_code=claim[1],
                            media_type="application/json",
                            headers={"X-Idempotent-Replay": "1"})
        if claim[0] == "busy":
            return JSONResponse(status_code=409, content={"detail": "in_progress"})
        if claim[0] == "mismatch":
            return JSONResponse(status_code=422, content={"detail": "idempotency_key_reused"})
        idem_row = claim[1]

    response = await call_next(request)

    if idem_row is not None:
        body = b"".join([chunk async for chunk in response.body_iterator])
        try:
            with SessionLocal() as s:
                svc.idempotency_finish(s, idem_row, response.status_code,
                                       body.decode("utf-8", "replace"))
        except Exception:
            log.exception("could not store an idempotent answer")
        response = Response(content=body, status_code=response.status_code,
                            headers={k: v for k, v in response.headers.items()
                                     if k.lower() != "content-length"},
                            media_type=response.media_type)

    # One place decides what spends a free action: a write that succeeded.
    # Counting in the middleware rather than in forty handlers is what stops
    # the definition from drifting endpoint by endpoint — and counting only on
    # a 2xx means a rejected body or a 404 never costs anybody anything.
    if (key > 0 and request.method in MUTATING_METHODS
            and 200 <= response.status_code < 300
            and request.url.path not in UNCOUNTED_PATHS):
        try:
            with SessionLocal() as s:
                outcome = svc.record_action_and_progress(s, key)
            # The Mini App is the other half of the same loop: a friend who
            # only ever uses the web UI must still qualify, and their inviter
            # must still hear about it.
            inviter = outcome["inviter_to_tell"]
            if inviter is not None and telegram_app is not None:
                await notify_referral_qualified(telegram_app.bot, inviter)
            # Progression is scored on this path too, so a user who only ever
            # touches the Mini App still has a level and a rank. One service,
            # both surfaces — not two systems that drift.
            if outcome["progress"].get("level_up") and telegram_app is not None:
                with SessionLocal() as s:
                    user = s.get(User, key)
                    lang = user.language if user else "uz"
                await notify_level_up(telegram_app.bot, key,
                                      outcome["progress"]["level"], lang)
        except Exception:               # never fail a request over a counter
            log.exception("could not record an action for %s", key)

    return response


#: Requests that change something. A GET never spends a free action.
MUTATING_METHODS = {"POST", "PATCH", "PUT", "DELETE"}

#: Writes that are not *use* of the product: checking whether the channel was
#: joined, changing a theme, sending feedback, or leaving. Charging somebody a
#: free action for tapping "check my subscription" would be absurd.
UNCOUNTED_PATHS = {
    "/api/subscription", "/api/settings", "/api/prefs", "/api/feedback",
    "/api/export/send", "/api/account/delete", "/api/stats/export",
}


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """Never leak an exception type or traceback to a client."""
    log.exception("api error: %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "server_error"})


@app.exception_handler(svc.NotFound)
async def not_found(request: Request, exc: svc.NotFound):
    return JSONResponse(status_code=404, content={"detail": "not_found"})


def _perm(e: PermissionError) -> HTTPException:
    """Not in the team: 404, the same answer as a team that does not exist.
    In it, but not allowed: 403 — the screen then says who can do it."""
    if str(e) == "forbidden":
        return HTTPException(status_code=403, detail="forbidden")
    return HTTPException(status_code=404, detail="not_found")


@app.exception_handler(PermissionError)
async def permission_denied(request: Request, exc: PermissionError):
    error = _perm(exc)
    return JSONResponse(status_code=error.status_code, content={"detail": error.detail})


# --- request bodies ---

# Every string is bounded at the schema edge, so an oversized field is
# rejected before it reaches the database (audit 013).

class SettingsIn(BaseModel):
    language: str | None = Field(default=None, max_length=2)
    gender: str | None = Field(default=None, max_length=6)
    theme: str | None = Field(default=None, max_length=20)
    quote: str | None = Field(default=None, max_length=300)


class HabitIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="target", max_length=16)
    #: daily | weekdays | days:0,2,4 — anything else is read as daily.
    schedule: str | None = Field(default=None, max_length=24)
    remind_at: str | None = Field(default=None, max_length=5)
    #: Timer length in minutes: 0 switches it off, null reads it from the name.
    timer_minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    #: "today" (default) or "tomorrow" — whether today already owes it.
    start: str | None = Field(default=None, max_length=10)


class PrayerIn(BaseModel):
    prayer: str = Field(max_length=10)
    status: str = Field(max_length=10)
    day: str | None = Field(default=None, max_length=10)


class ExcusedIn(BaseModel):
    excused: bool
    day: str | None = None


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    deadline: str | None = Field(default=None, max_length=10)
    #: Optional clock time on the deadline day; omitted means an all-day task.
    due_time: str | None = Field(default=None, max_length=5)
    #: Minutes before the due moment, 0 for exactly then.
    remind_before: int | None = Field(default=None, ge=0, le=60 * 24 * 7)
    recurrence: str | None = Field(default=None, max_length=24)
    project_id: int | None = None
    priority: str = Field(default="medium", max_length=6)
    #: Timer length in minutes: 0 switches it off, null reads it from the name.
    timer_minutes: int | None = Field(default=None, ge=0, le=24 * 60)


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    deadline: str | None = None
    due_time: str | None = Field(default=None, max_length=5)
    remind_before: int | None = Field(default=None, ge=0, le=60 * 24 * 7)
    recurrence: str | None = Field(default=None, max_length=24)
    project_id: int | None = None
    priority: str | None = None
    status: str | None = None
    #: Timer length in minutes: 0 switches it off, null reads it from the name.
    timer_minutes: int | None = Field(default=None, ge=0, le=24 * 60)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    deadline: str | None = Field(default=None, max_length=10)


class FocusIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    priority: str | None = Field(default=None, max_length=6)
    #: The task this mission is carried out by. Finishing the task finishes
    #: the mission — the day scores it once, as the task.
    task_id: int | None = None


class HabitOrderIn(BaseModel):
    #: Bounded so a caller cannot post a list long enough to be a denial of
    #: service in itself; nobody tracks two hundred habits.
    habit_ids: list[int] = Field(min_length=1, max_length=200)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    deadline: str | None = Field(default=None, max_length=10)
    #: active | done. Finishing a project must not mean deleting it.
    status: str | None = Field(default=None, max_length=10)
    #: Archived is stored as a timestamp, but the API takes a plain switch.
    archived: bool | None = None


class JournalIn(BaseModel):
    answers: dict[str, str] | None = None
    text: str = Field(default="", max_length=10000)
    day: str | None = Field(default=None, max_length=10)
    #: One of services.MOODS, or empty. Optional by design.
    mood: str = Field(default="", max_length=20)

    @field_validator("answers")
    @classmethod
    def _bounded_answers(cls, value):
        """Refuse a dictionary stuffed with thousands of keys (audit 013)."""
        if value is None:
            return value
        if len(value) > 20:
            raise ValueError("too many answers")
        for key, text in value.items():
            if len(key) > 32 or len(text) > 4000:
                raise ValueError("answer too long")
        return value


class BirthdayIn(BaseModel):
    person_name: str = Field(min_length=1, max_length=200)
    birth_date: str = Field(max_length=10)
    note: str = Field(default="", max_length=300)


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="bad_date")


# --- endpoints ---

@app.get("/api/health")
@app.get("/health/live")
def health_live():
    """Liveness: the process is up. Says nothing about dependencies."""
    return {"ok": True}


@app.get("/health/reports")
def health_reports(key: str = "", limit: int = 20):
    """Why a given user did or did not get their report, in one request.

    Every previous answer to "the reports are not arriving" needed the
    database, the deploy log and somebody who knew which three things to
    correlate. This does the correlating: for each onboarded account it says
    whether the scheduler would pick them up, whether their moment has passed
    in *their* zone, and what today's outbox row says. A blank `reports` list
    with a non-zero `onboarded` count is the recipient query excluding
    everybody; a `due` of false all day is a clock or a preference; a row
    stuck in `claimed` is a worker that died.

    Guarded by the bot token because it names accounts. Not a general admin
    surface — it answers exactly one question.
    """
    import hmac as _hmac

    if not BOT_TOKEN or not _hmac.compare_digest(key, BOT_TOKEN):
        raise HTTPException(status_code=404, detail="not_found")

    from sqlalchemy import func

    from db import DailyReportLog

    out: dict = {
        "scheduler_running": bool(scheduler and scheduler.running),
        "jobs": sorted(j.id for j in scheduler.get_jobs()) if scheduler else [],
        # A job repeatedly refused its lock is the one failure that is
        # otherwise completely silent.
        "lock_refusals": dict(svc.LOCK_REFUSALS),
        # Non-zero means daily_report_logs carries a unique constraint this
        # code does not expect; migration 0010 repairs it.
        "claim_anomalies": dict(svc.CLAIM_ANOMALIES),
        "report_tick_minutes": config.REPORT_TICK_MINUTES,
        "server_time_utc": db.utcnow().isoformat(timespec="seconds"),
        "required_channel": bool(deps.REQUIRED_CHANNEL_ID),
        "free_actions": deps.FREE_ACTIONS,
    }

    with SessionLocal() as s:
        recipients = {r[0] for r in svc.active_recipients(s)}
        users = s.scalars(select(User).where(User.onboarded.is_(True))
                          .order_by(User.telegram_id).limit(limit)).all()
        out["onboarded"] = s.scalar(select(func.count()).select_from(User)
                                    .where(User.onboarded.is_(True))) or 0
        out["recipients"] = len(recipients)

        rows = []
        for user in users:
            tz = svc.user_tz(user)
            now = svc.now_local(tz)
            prefs = svc.prefs_for(user)
            ws = svc.workspace_id_for(s, user.telegram_id)
            today = svc.today_local(tz)
            logs = s.scalars(select(DailyReportLog).where(
                DailyReportLog.workspace_id == ws,
                DailyReportLog.report_date == today)).all() if ws else []
            rows.append({
                "telegram_id": user.telegram_id,
                "is_recipient": user.telegram_id in recipients,
                # When they are not, this is almost always why.
                "is_subscribed": bool(user.is_subscribed),
                "actions_count": user.actions_count or 0,
                "timezone": prefs["timezone"],
                "their_local_time": now.strftime("%Y-%m-%d %H:%M"),
                "morning": {
                    "enabled": prefs["morning_report"],
                    "at": prefs["morning_time"],
                    "due_now": svc.report_is_due(user, "morning", now),
                },
                "evening": {
                    "enabled": prefs["evening_report"],
                    "at": prefs["evening_time"],
                    "due_now": svc.report_is_due(user, "evening", now),
                },
                "today": [{"type": r.report_type, "status": r.status,
                           "attempts": r.attempts,
                           "error": (r.last_error or "")[:120]} for r in logs],
            })
        out["reports"] = rows

    return out


@app.get("/health/ready")
def health_ready():
    """Readiness: can this instance actually serve?

    Checks the database, the schema and the bot worker, because a process that
    answers 200 while the database is unreachable is worse than one that
    admits it (audit 087).
    """
    from sqlalchemy import inspect

    checks: dict[str, str] = {}
    ok = True

    try:
        with db.engine.connect() as conn:
            conn.execute(sql_text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {type(e).__name__}"
        ok = False

    try:
        missing = [t for t in ("users", "workspaces", "habits", "tasks")
                   if not inspect(db.engine).has_table(t)]
        checks["schema"] = "ok" if not missing else f"missing: {missing}"
        ok = ok and not missing
    except Exception as e:
        checks["schema"] = f"error: {type(e).__name__}"
        ok = False

    if BOT_TOKEN and ENVIRONMENT != "test":
        running = telegram_app is not None and telegram_app.updater is not None
        checks["bot"] = "ok" if running else "not running"
        ok = ok and running
    else:
        checks["bot"] = "disabled"

    checks["scheduler"] = "ok" if (scheduler and scheduler.running) else "disabled"

    # Where the unattended messages are configured to go, and whether they
    # actually went. "The stats channel is not working" was, until this existed,
    # a question with no answer short of reading the deploy log: the job claims
    # its run, the send fails because the bot was never made an administrator of
    # the channel, the reason is logged once, and the channel stays quiet
    # forever. `job_runs` and the report outbox already record what happened —
    # this only reads them back out.
    checks["stats_channel"] = STATS_CHANNEL_ID or "unset"
    try:
        from sqlalchemy import func, select

        from db import DailyReportLog

        with SessionLocal() as s:
            last = svc.job_last_run(s, STATS_JOB)
            today = svc.today_local()
            checks["stats_last_post"] = str(last) if last else "never"
            if not STATS_CHANNEL_ID:
                checks["stats"] = "no channel configured"
            elif last == today:
                checks["stats"] = "ok — posted today"
            elif svc.now_local().hour < STATS_POST_HOUR:
                checks["stats"] = f"waiting for {STATS_POST_HOUR:02d}:00"
            else:
                # Claimed-but-not-today, or never: the hour has passed and
                # nothing went out. Almost always the bot not being an admin of
                # the channel; the application log carries the Telegram error.
                checks["stats"] = "overdue — check the bot is an admin there"
            # How many people should have had a report today, against how many
            # actually did. "sent 0" on its own is ambiguous — nobody was due
            # yet, or delivery is broken — and the denominator is what tells
            # the two apart without opening the database.
            expected = s.scalar(select(func.count()).select_from(User)
                                .where(User.onboarded.is_(True))) or 0
            for kind in ("morning", "evening"):
                rows = s.execute(select(DailyReportLog.status, func.count())
                                 .where(DailyReportLog.report_date == today,
                                        DailyReportLog.report_type == kind)
                                 .group_by(DailyReportLog.status)).all()
                counts = {status: n for status, n in rows}
                resolved = sum(counts.values())
                checks[f"{kind}_today"] = (
                    f"sent {counts.get('sent', 0)} · failed {counts.get('failed', 0)}"
                    f" · claimed {counts.get('claimed', 0)}"
                    f" · of {expected} onboarded")
                # A row still `claimed` is a worker that never finished. The
                # next tick reclaims it once it is older than the cutoff; this
                # is only here so the state is visible while it lasts.
                if counts.get("claimed"):
                    checks[f"{kind}_stuck"] = (
                        f"{counts['claimed']} claim(s) unresolved — "
                        f"reclaimed after {svc.STALE_CLAIM_MINUTES} min")
                if expected and resolved == 0:
                    checks[f"{kind}_note"] = "nobody due yet today"
    except Exception as e:
        checks["stats"] = f"error: {type(e).__name__}"

    return JSONResponse(status_code=200 if ok else 503,
                        content={"ok": ok, "checks": checks})


@app.get("/api/me")
def api_me(init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init, require_onboarded=False)
    return {"telegram_id": user.telegram_id, "member_no": user.member_no,
            "first_name": user.first_name, "last_name": user.last_name,
            "username": user.username,
            "language": user.language, "gender": user.gender,
            "theme": theme_of(user.theme), "quote": user.quote,
            "has_photo": bool(user.photo_file_id),
            # Minted per request: the Mini App puts this in the avatar's
            # `src` instead of its initData, so nothing long-lived reaches a
            # URL. Only useful to the user it names, and only for minutes.
            "avatar_token": (issue_avatar_token(user.telegram_id)
                             if user.photo_file_id else None),
            "has_phone": bool(user.phone_number),
            "prefs": svc.prefs_for(user),
            "timezones": svc.TIMEZONES,
            "onboarded": user.onboarded, "is_subscribed": user.is_subscribed,
            # Read-only mode: the channel gate stops writes, never reading.
            "gated": deps.trial_state(user).gated,
            "onboarding_step": user.onboarding_step,
            "modules": _modules_of(user),
            # The ErnestOS login, for signing in from another Telegram.
            "login": _login_of(user.telegram_id),
            # Names only, for the "personal or which team?" pickers on every
            # add sheet. The full team payload is fetched by the Team screen;
            # asking for it at start-up made the first screen wait on it.
            "teams": _team_names_of(user.telegram_id)}


def _team_names_of(uid: int) -> list[dict]:
    with SessionLocal() as s:
        return [{"id": team.id, "name": team.name}
                for team in svc.teams_for(s, uid)]


def _login_of(uid: int) -> str | None:
    with SessionLocal() as s:
        row = accounts.credential_for(s, uid)
        return row.login if row else None


def _modules_of(user: User) -> dict:
    with SessionLocal() as s:
        return svc.modules_for(s, svc.workspace_id_for(s, user.telegram_id))


class ModulesIn(BaseModel):
    modules: list[str] = Field(default_factory=list, max_length=8)


@app.get("/api/modules")
def api_modules(init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        return {"modules": svc.modules_for(s, ws), "available": list(svc.MODULES)}


@app.post("/api/modules")
def api_modules_save(body: ModulesIn,
                     init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Switch the rituals on or off. History is kept either way."""
    user, ws = auth(init)
    with SessionLocal() as s:
        row = s.get(User, user.telegram_id)
        return {"modules": svc.set_modules(s, ws, body.modules, user=row)}


@app.post("/api/settings")
def api_settings(body: SettingsIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        row = s.get(User, user.telegram_id)
        if body.language in ("uz", "en", "ru"):
            row.language = body.language
        if body.gender in ("male", "female"):
            row.gender = body.gender
        if body.theme in THEMES:
            row.theme = body.theme
        if body.quote is not None:
            row.quote = body.quote.strip()[:300]
        s.commit()
    return {"ok": True}


class PrefsIn(BaseModel):
    """Notification and timezone settings. Every field is optional, so the UI
    can save one switch without resending the rest."""
    timezone: str | None = Field(default=None, max_length=40)
    morning_report: bool | None = None
    morning_time: str | None = Field(default=None, max_length=5)
    evening_report: bool | None = None
    evening_time: str | None = Field(default=None, max_length=5)
    task_reminders: bool | None = None
    habit_reminders: bool | None = None


def _time(value: str | None) -> dtime | None:
    if not value:
        return None
    try:
        hour, minute = (int(x) for x in value.replace(".", ":").split(":"))
        return dtime(hour, minute)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="bad_time")


@app.get("/api/prefs")
def api_prefs(init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    return {"prefs": svc.prefs_for(user), "timezones": svc.TIMEZONES}


@app.post("/api/prefs")
def api_prefs_save(body: PrefsIn,
                   init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Save reports, reminders and the timezone.

    Each switch is written the moment it is flipped, which is why there is no
    Save button on that screen.
    """
    user, _ = auth(init)
    fields = body.model_dump(exclude_unset=True)
    for key in ("morning_time", "evening_time"):
        if key in fields:
            fields[key] = _time(fields[key])
    with SessionLocal() as s:
        row = s.get(User, user.telegram_id)
        try:
            prefs = svc.save_prefs(s, row, **fields)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True, "prefs": prefs}


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------
#
# Every endpoint takes the acting user from the signed initData and passes it
# to the service, which refuses anything they are not a member of. No team id
# from a request body is ever trusted on its own.

class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=svc.TEAM_NAME_MAX)


class TeamTaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    deadline: date | None = None
    priority: str = "medium"
    description: str = Field(default="", max_length=2000)
    due_time: str | None = Field(default=None, max_length=5)
    remind_before: int | None = None
    recurrence: str | None = Field(default=None, max_length=24)
    project_id: int | None = None
    #: all — everybody does it · any — one person is enough · assignees — the
    #: named people only (#24).
    completion: str | None = Field(default=None, max_length=10)
    assignees: list[int] | None = Field(default=None, max_length=svc.MAX_TEAM_MEMBERS)
    timer_minutes: int | None = Field(default=None, ge=0, le=24 * 60)


class TeamTaskPatch(BaseModel):
    """Only what is sent changes — a missing field is left as it was."""
    title: str | None = Field(default=None, min_length=1, max_length=300)
    deadline: date | None = None
    priority: str | None = None
    description: str | None = Field(default=None, max_length=2000)
    due_time: str | None = Field(default=None, max_length=5)
    remind_before: int | None = None
    recurrence: str | None = Field(default=None, max_length=24)
    project_id: int | None = None
    completion: str | None = Field(default=None, max_length=10)
    assignees: list[int] | None = Field(default=None, max_length=svc.MAX_TEAM_MEMBERS)
    timer_minutes: int | None = Field(default=None, ge=0, le=24 * 60)


class TeamHabitIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    schedule: str | None = None
    category: str = "non_negotiable"
    target_time: str | None = Field(default=None, max_length=5)
    remind_at: str | None = Field(default=None, max_length=5)
    timer_minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    start: str | None = Field(default=None, max_length=10)


class TeamHabitPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    schedule: str | None = Field(default=None, max_length=24)
    category: str | None = Field(default=None, max_length=16)
    target_time: str | None = Field(default=None, max_length=5)
    remind_at: str | None = Field(default=None, max_length=5)
    timer_minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    paused: bool | None = None
    #: With `paused`: "today" or "tomorrow".
    from_day: str | None = Field(default=None, alias="from", max_length=10)

    model_config = {"populate_by_name": True}


async def notify_teammates(team_id: int, actor_id: int, key: str,
                           what: str, team_name: str) -> int:
    """Tell the rest of the team what just changed. Returns how many heard.

    A shared list that changes silently is a shared list people stop
    trusting: the other person opens the app and something is different, with
    no idea who did it or when. Every structural change — added, removed,
    renamed — is announced to everybody except whoever made it. Ticking your
    own share is *not* announced: that would be two notifications a day each,
    which is how a couple mutes the bot.

    Never raises. This runs after the change is already committed, and a
    notification that could not be delivered is not a reason to fail the
    request that caused it.
    """
    if telegram_app is None:
        return 0
    told = 0
    try:
        with SessionLocal() as s:
            # Each member's own language, and only members whose notification
            # level for this team still hears changes.
            recipients = svc.team_recipients(s, team_id, actor_id, "change")
            actor = s.get(User, actor_id)
            actor_name = (actor.first_name or "").strip() if actor else ""
    except Exception:
        log.exception("could not work out who to tell about team %s", team_id)
        return 0

    bot = AccountFanOut(telegram_app.bot)
    for uid, lang in recipients:
        lang = lang or "uz"
        try:
            await bot.send_message(
                uid,
                t(lang, key, who=esc(actor_name or "?"), what=esc(what))
                + "\n" + t(lang, "team_ev_in", name=esc(team_name)),
                parse_mode=ParseMode.HTML)
            told += 1
        except TelegramError as e:
            log.info("could not tell %s about a team change: %s", uid, e)
        except Exception:
            log.exception("telling %s about a team change errored", uid)
    return told


@app.get("/api/teams")
def api_teams(init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Every team this account is in, each with today's state for both sides."""
    user, _ = auth(init)
    tz = svc.user_tz(user)
    uid = user.telegram_id
    with SessionLocal() as s:
        out = []
        for team in svc.teams_for(s, uid):
            perms = svc.team_permissions(s, team.id, uid)
            out.append({
                "id": team.id, "name": team.name,
                "is_owner": team.owner_id == uid,
                "role": perms["role"], "permissions": perms,
                # The link is handed only to those who may invite; the rest
                # see that there is one and who to ask.
                "invite_link": (svc.team_invite_link(team, BOT_USERNAME)
                                if perms["invite"] else None),
                "invite": svc.invite_info(team),
                "ownership_offer": team.pending_owner_id == uid,
                "pending_owner_id": team.pending_owner_id,
                "notify": svc.member_notify(s, team.id, uid),
                "requests": (len(svc.list_join_requests(s, uid, team.id))
                             if perms["approve"] else 0),
                "members": [
                    {**m, "is_you": m["user_id"] == uid,
                     "joined_at": m["joined_at"].isoformat()}
                    for m in svc.team_members(s, team.id)],
                "summary": svc.team_day_summary(s, team.id, tz=tz),
                "tasks": svc.list_team_tasks(s, uid, team.id, tz=tz),
                "habits": svc.list_team_habits(s, uid, team.id, tz=tz),
                "stats": svc.team_stats(s, uid, team.id, period="week", tz=tz),
                "projects": svc.list_team_projects(s, uid, team.id),
                "board": svc.team_scoreboard(s, uid, team.id, tz=tz),
                "countdowns": svc.list_team_countdowns(s, uid, team.id, tz=tz,
                                                       include_past=False),
            })
    return {"teams": out, "max_members": svc.MAX_TEAM_MEMBERS,
            "max_teams": svc.MAX_TEAMS_PER_USER,
            "completion_policies": list(svc.COMPLETION_POLICIES),
            "notify_levels": list(svc.NOTIFY_LEVELS)}


class RoleIn(BaseModel):
    role: str = Field(max_length=10)


class TransferIn(BaseModel):
    user_id: int


class AnswerIn(BaseModel):
    accept: bool


class ApprovalIn(BaseModel):
    on: bool


class NotifyIn(BaseModel):
    level: str = Field(max_length=10)


class JoinIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)


@app.get("/api/teams/invite/{code}")
def api_team_invite_preview(code: str,
                            init=Header(default=None, alias="X-Telegram-Init-Data")):
    """What an invite is for, before anybody joins anything (#29)."""
    user, _ = auth(init, require_onboarded=False)
    with SessionLocal() as s:
        info = svc.preview_invite(s, code)
        if info is None:
            raise HTTPException(status_code=404, detail="not_found")
        info["already"] = svc.team_for(s, user.telegram_id, info["team_id"]) is not None
        info.pop("code", None)
        return info


@app.post("/api/teams/join")
async def api_team_join(body: JoinIn,
                        init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        team, outcome = svc.join_team(s, user.telegram_id, body.code)
        name = team.name if team else ""
        team_id = team.id if team else None
        recipients = (svc.team_recipients(s, team_id, user.telegram_id, "join")
                      if outcome == "joined" else [])
    joiner = esc(user.first_name or str(user.telegram_id))
    if telegram_app is not None:
        for member_id, member_lang in recipients:
            try:
                await telegram_app.bot.send_message(
                    member_id, t(member_lang, "team_member_joined", who=joiner,
                                 name=esc(name)), parse_mode=ParseMode.HTML)
            except TelegramError as e:
                log.info("could not tell %s about a new member: %s", member_id, e)
    return {"outcome": outcome, "team_id": team_id, "name": name}


@app.get("/api/teams/{team_id}/activity")
def api_team_activity(team_id: int,
                      init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Who changed what, newest first — and what can still be undone (#26)."""
    user, _ = auth(init)
    with SessionLocal() as s:
        return {"activity": svc.list_team_activity(s, user.telegram_id, team_id)}


@app.put("/api/teams/{team_id}/members/{member_id}/role")
def api_team_role(team_id: int, member_id: int, body: RoleIn,
                  init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            return svc.set_member_role(s, user.telegram_id, team_id, member_id,
                                       body.role)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))


@app.delete("/api/teams/{team_id}/members/{member_id}")
def api_team_remove_member(team_id: int, member_id: int,
                           init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            if not svc.remove_member(s, user.telegram_id, team_id, member_id):
                raise HTTPException(status_code=404, detail="not_found")
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}


@app.post("/api/teams/{team_id}/transfer")
async def api_team_transfer(team_id: int, body: TransferIn,
                            init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Offer the team to a member; it moves only when they accept."""
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            team = svc.offer_ownership(s, user.telegram_id, team_id, body.user_id)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        target = s.get(User, body.user_id)
        target_lang = target.language if target else "uz"
        name = team.name
    if telegram_app is not None:
        try:
            await telegram_app.bot.send_message(
                body.user_id, t(target_lang, "team_owner_offer", name=esc(name)),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(t(target_lang, "accept"),
                                         callback_data=f"town:a:{team_id}"),
                    InlineKeyboardButton(t(target_lang, "decline"),
                                         callback_data=f"town:d:{team_id}")]]))
        except TelegramError as e:
            log.info("could not offer ownership to %s: %s", body.user_id, e)
    return {"ok": True, "pending_owner_id": body.user_id}


@app.post("/api/teams/{team_id}/transfer/answer")
def api_team_transfer_answer(team_id: int, body: AnswerIn,
                             init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            team = svc.answer_ownership(s, user.telegram_id, team_id, body.accept)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return {"ok": True, "owner_id": team.owner_id}


@app.post("/api/teams/{team_id}/invite/renew")
def api_team_invite_renew(team_id: int,
                          init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        team = svc.renew_invite(s, user.telegram_id, team_id)
        return {"invite_link": svc.team_invite_link(team, BOT_USERNAME),
                "invite": svc.invite_info(team)}


@app.post("/api/teams/{team_id}/invite/revoke")
def api_team_invite_revoke(team_id: int,
                           init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        team = svc.revoke_invite(s, user.telegram_id, team_id)
        return {"invite": svc.invite_info(team)}


@app.put("/api/teams/{team_id}/invite/approval")
def api_team_invite_approval(team_id: int, body: ApprovalIn,
                             init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        team = svc.set_invite_approval(s, user.telegram_id, team_id, body.on)
        return {"invite": svc.invite_info(team)}


@app.get("/api/teams/{team_id}/requests")
def api_team_requests(team_id: int,
                      init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        return {"requests": svc.list_join_requests(s, user.telegram_id, team_id)}


@app.post("/api/teams/requests/{request_id}")
async def api_team_request_answer(request_id: int, body: AnswerIn,
                                  init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            request, team, outcome = svc.decide_join_request(
                s, user.telegram_id, request_id, body.accept)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        joiner = s.get(User, request.user_id)
        joiner_lang = joiner.language if joiner else "uz"
        joiner_id, name = request.user_id, team.name
    if telegram_app is not None:
        key = {"approved": "team_joined", "declined": "team_request_declined",
               "full": "team_full"}.get(outcome, "team_already")
        try:
            await telegram_app.bot.send_message(
                joiner_id, t(joiner_lang, key, name=esc(name)), parse_mode=ParseMode.HTML)
        except TelegramError as e:
            log.info("could not answer join request %s: %s", request_id, e)
    return {"outcome": outcome}


@app.put("/api/teams/{team_id}/notify")
def api_team_notify(team_id: int, body: NotifyIn,
                    init=Header(default=None, alias="X-Telegram-Init-Data")):
    """How much this team may message me. My setting only (#30)."""
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            return {"notify": svc.set_team_notify(s, user.telegram_id, team_id,
                                                  body.level)}
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/teams")
def api_team_create(body: TeamIn,
                    init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            team = svc.create_team(s, user.telegram_id, body.name)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return {"id": team.id, "name": team.name,
                "invite_link": svc.team_invite_link(team, BOT_USERNAME)}


@app.patch("/api/teams/{team_id}")
async def api_team_rename(team_id: int, body: TeamIn,
                          init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            team = svc.rename_team(s, user.telegram_id, team_id, body.name)
        except PermissionError as e:
            raise _perm(e)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        out = {"id": team.id, "name": team.name}
    await notify_teammates(team_id, user.telegram_id, "team_ev_renamed",
                           out["name"], out["name"])
    return out


@app.delete("/api/teams/{team_id}")
def api_team_leave(team_id: int,
                   init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            if not svc.leave_team(s, user.telegram_id, team_id):
                raise HTTPException(status_code=404, detail="not_found")
        except ValueError as e:
            # The owner hands the team on before walking out of it.
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}


class MoveHabitIn(BaseModel):
    #: "personal", or "team:<id>". The same vocabulary the add sheet uses.
    to: str = Field(min_length=1, max_length=24)


@app.post("/api/tasks/{task_id}/move")
async def api_task_move(task_id: int, body: MoveHabitIn,
                        init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Move a private task into a team."""
    user, _ = auth(init)
    if not body.to.startswith("team:"):
        raise HTTPException(status_code=422, detail="bad_destination")
    team_id = int(body.to.split(":", 1)[1] or 0)
    with SessionLocal() as s:
        try:
            moved = svc.move_task(s, user.telegram_id, task_id=task_id,
                                  to_team=team_id)
        except PermissionError as e:
            raise _perm(e)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        team = svc.team_for(s, user.telegram_id, team_id)
        name = team.name if team else ""
    await notify_teammates(team_id, user.telegram_id, "team_ev_task_add",
                           moved["title"], name)
    return moved


@app.post("/api/teams/tasks/{task_id}/move")
async def api_team_task_move(task_id: int, body: MoveHabitIn,
                             init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Take a shared task back into a private list."""
    user, _ = auth(init)
    if body.to != "personal":
        raise HTTPException(status_code=422, detail="bad_destination")
    with SessionLocal() as s:
        from db import TeamTask
        row = s.get(TeamTask, task_id)
        team_id, label = (row.team_id, row.title) if row else (None, "")
        try:
            moved = svc.move_task(s, user.telegram_id, team_task_id=task_id)
        except PermissionError as e:
            raise _perm(e)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        team = (svc.team_for(s, user.telegram_id, team_id)
                if team_id is not None else None)
        name = team.name if team else ""
    if team_id is not None:
        await notify_teammates(team_id, user.telegram_id, "team_ev_task_del",
                               label, name)
    return moved


@app.post("/api/projects/{project_id}/move")
async def api_project_move(project_id: int, body: MoveHabitIn,
                           init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Move a project, and everything filed on it, between private and shared."""
    user, _ = auth(init)
    to_team = (int(body.to.split(":", 1)[1] or 0)
               if body.to.startswith("team:") else None)
    if to_team is None and body.to != "personal":
        raise HTTPException(status_code=422, detail="bad_destination")
    with SessionLocal() as s:
        try:
            moved = svc.move_project(s, user.telegram_id, project_id, to_team)
        except PermissionError as e:
            raise _perm(e)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        team_id = to_team if to_team is not None else None
        team = (svc.team_for(s, user.telegram_id, team_id)
                if team_id is not None else None)
        name = team.name if team else ""
    if team_id is not None:
        await notify_teammates(team_id, user.telegram_id,
                               "team_ev_project_add", moved["name"], name)
    return moved


@app.get("/api/teams/habits/{habit_id}/history")
def api_team_habit_history(habit_id: int,
                           init=Header(default=None, alias="X-Telegram-Init-Data")):
    """A shared habit's record, shaped exactly like a private one's."""
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            return svc.team_habit_history(s, user.telegram_id, habit_id,
                                          tz=svc.user_tz(user))
        except PermissionError as e:
            raise _perm(e)
        except ValueError:
            raise HTTPException(status_code=404, detail="not_found")


@app.post("/api/habits/{habit_id}/move")
async def api_habit_move(habit_id: int, body: MoveHabitIn,
                         init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Move a private habit into a team."""
    user, _ = auth(init)
    if not body.to.startswith("team:"):
        raise HTTPException(status_code=422, detail="bad_destination")
    team_id = int(body.to.split(":", 1)[1] or 0)
    with SessionLocal() as s:
        try:
            moved = svc.move_habit(s, user.telegram_id, habit_id=habit_id,
                                   to_team=team_id)
        except PermissionError as e:
            raise _perm(e)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        team = svc.team_for(s, user.telegram_id, team_id)
        name = team.name if team else ""
    await notify_teammates(team_id, user.telegram_id, "team_ev_habit_add",
                           moved["name"], name)
    return moved


@app.post("/api/teams/habits/{habit_id}/move")
async def api_team_habit_move(habit_id: int, body: MoveHabitIn,
                              init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Take a shared habit back into a private list.

    This removes it from the other member too, so they are told.
    """
    user, _ = auth(init)
    if body.to != "personal":
        raise HTTPException(status_code=422, detail="bad_destination")
    with SessionLocal() as s:
        from db import TeamHabit
        row = s.get(TeamHabit, habit_id)
        team_id = row.team_id if row else None
        label = row.name if row else ""
        try:
            moved = svc.move_habit(s, user.telegram_id,
                                   team_habit_id=habit_id, to_team=None)
        except PermissionError as e:
            raise _perm(e)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        team = (svc.team_for(s, user.telegram_id, team_id)
                if team_id is not None else None)
        name = team.name if team else ""
    if team_id is not None:
        await notify_teammates(team_id, user.telegram_id, "team_ev_habit_del",
                               label, name)
    return moved


class TeamProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    deadline: date | None = None


@app.get("/api/teams/{team_id}/projects")
def api_team_projects(team_id: int,
                      init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            return {"projects": svc.list_team_projects(s, user.telegram_id,
                                                       team_id)}
        except PermissionError as e:
            raise _perm(e)


@app.post("/api/teams/{team_id}/projects")
async def api_team_project_add(team_id: int, body: TeamProjectIn,
                               init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            made = svc.add_team_project(s, user.telegram_id, team_id, body.name,
                                        description=body.description,
                                        deadline=body.deadline)
        except PermissionError as e:
            raise _perm(e)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        team = svc.team_for(s, user.telegram_id, team_id)
        name = team.name if team else ""
    await notify_teammates(team_id, user.telegram_id, "team_ev_project_add",
                           made["name"], name)
    return made


@app.get("/api/teams/projects/{project_id}/tasks")
def api_team_project_tasks(project_id: int,
                           init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Everything filed on one shared shelf."""
    user, _ = auth(init)
    with SessionLocal() as s:
        from db import Project
        project = s.get(Project, project_id)
        if project is None or project.team_id is None:
            raise HTTPException(status_code=404, detail="not_found")
        try:
            rows = svc.list_team_tasks(s, user.telegram_id, project.team_id,
                                       project_id=project_id,
                                       tz=svc.user_tz(user))
            listed = svc.list_team_projects(s, user.telegram_id, project.team_id,
                                            include_archived=True)
        except PermissionError as e:
            raise _perm(e)
        team = svc.team_for(s, user.telegram_id, project.team_id)
        # The same shape as a personal project, so one screen draws both.
        info = next((p for p in listed if p["id"] == project_id), {})
        return {"project": {**info, "id": project.id, "name": project.name,
                            "team_id": project.team_id, "source": "team",
                            "team_name": team.name if team else "",
                            "progress": info.get("percent", 0),
                            "archived": project.archived_at is not None},
                "tasks": [{**r, "source": "team",
                           "team_name": team.name if team else ""} for r in rows]}


class TeamProjectPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    deadline: date | None = None
    clear_deadline: bool = False
    status: str | None = Field(default=None, max_length=10)


@app.patch("/api/teams/projects/{project_id}")
def api_team_project_edit(project_id: int, body: TeamProjectPatch,
                          init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Rename a shared project, or change its note, deadline or status."""
    user, _ = auth(init)
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items()
              if k != "clear_deadline"}
    if body.clear_deadline:
        fields["deadline"] = None
    with SessionLocal() as s:
        try:
            return svc.update_team_project(s, user.telegram_id, project_id, **fields)
        except PermissionError as e:
            raise _perm(e)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))


@app.delete("/api/teams/projects/{project_id}")
def api_team_project_delete(project_id: int,
                            init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Archive a shared project; its tasks stay in the team, unfiled."""
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            name = svc.delete_team_project(s, user.telegram_id, project_id)
        except PermissionError as e:
            raise _perm(e)
    return {"ok": True, "name": name}


@app.get("/api/teams/{team_id}/scoreboard")
def api_team_scoreboard(team_id: int,
                        init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Day, week and month, and today's open and finished items."""
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            return svc.team_scoreboard(s, user.telegram_id, team_id,
                                       tz=svc.user_tz(user))
        except PermissionError as e:
            raise _perm(e)


@app.get("/api/teams/{team_id}/stats")
def api_team_stats(team_id: int, period: str = "week",
                   init=Header(default=None, alias="X-Telegram-Init-Data")):
    """How the team has done over a period, one line per member."""
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            return svc.team_stats(s, user.telegram_id, team_id,
                                  period=period if period in
                                  ("week", "month", "year") else "week",
                                  tz=svc.user_tz(user))
        except PermissionError as e:
            raise _perm(e)


@app.post("/api/teams/{team_id}/tasks")
async def api_team_task_add(team_id: int, body: TeamTaskIn,
                            init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            created = svc.add_team_task(
                s, user.telegram_id, team_id, body.title,
                deadline=body.deadline, priority=body.priority,
                description=body.description, due_time=_time(body.due_time),
                remind_before=body.remind_before, recurrence=body.recurrence,
                project_id=body.project_id, completion=body.completion,
                assignees=body.assignees, timer_minutes=body.timer_minutes)
        except PermissionError as e:
            raise _perm(e)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        team = svc.team_for(s, user.telegram_id, team_id)
        name = team.name if team else ""
    await notify_teammates(team_id, user.telegram_id, "team_ev_task_add",
                           created["title"], name)
    return created


@app.get("/api/teams/tasks/{task_id}")
def api_team_task_get(task_id: int,
                      init=Header(default=None, alias="X-Telegram-Init-Data")):
    """One shared task, shaped like a private one so the same sheet opens it."""
    user, _ = auth(init)
    with SessionLocal() as s:
        row = svc.team_task_for(s, user.telegram_id, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")
    return row


@app.patch("/api/teams/tasks/{task_id}")
async def api_team_task_edit(task_id: int, body: TeamTaskPatch,
                             init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    fields = body.model_dump(exclude_unset=True)
    if "due_time" in fields:
        fields["due_time"] = _time(fields["due_time"])
    with SessionLocal() as s:
        try:
            updated = svc.edit_team_task(s, user.telegram_id, task_id, **fields)
        except PermissionError as e:
            raise _perm(e)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return updated


@app.post("/api/teams/tasks/{task_id}/toggle")
def api_team_task_toggle(task_id: int,
                         init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Tick a shared task for whoever is asking, and only them."""
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            done = svc.toggle_team_task(s, user.telegram_id, task_id,
                                        tz=svc.user_tz(user))
        except PermissionError as e:
            raise _perm(e)
        except ValueError as e:
            # A timed task opens its timer; a task named for others is not yours.
            if str(e) in ("timer_required", "not_assigned"):
                raise HTTPException(status_code=409, detail=str(e))
            raise HTTPException(status_code=404, detail="not_found")
    return {"done": done}


@app.post("/api/teams/tasks/{task_id}/restore")
def api_team_task_restore(task_id: int,
                          init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Undo taking a task off the team's list."""
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            return svc.restore_team_task(s, user.telegram_id, task_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="not_found")


@app.post("/api/teams/habits/{habit_id}/restore")
def api_team_habit_restore(habit_id: int,
                           init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            return svc.restore_team_habit(s, user.telegram_id, habit_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="not_found")


@app.patch("/api/teams/habits/{habit_id}")
def api_team_habit_edit(habit_id: int, body: TeamHabitPatch,
                        init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Rename, re-tier, reschedule, pause or time a shared habit."""
    user, _ = auth(init)
    fields = body.model_dump(exclude_unset=True, by_alias=False)
    for key in ("remind_at", "target_time"):
        if key in fields:
            fields[key] = _time(fields[key])
    with SessionLocal() as s:
        try:
            return svc.edit_team_habit(s, user.telegram_id, habit_id, **fields)
        except ValueError as e:
            if str(e) == "unknown_habit":
                raise HTTPException(status_code=404, detail="not_found")
            raise HTTPException(status_code=422, detail=str(e))


@app.delete("/api/teams/tasks/{task_id}")
async def api_team_task_archive(task_id: int,
                                init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        from db import TeamTask
        row = s.get(TeamTask, task_id)
        title = row.title if row else ""
        team_id = row.team_id if row else None
        try:
            ok = svc.archive_team_task(s, user.telegram_id, task_id)
        except PermissionError as e:
            raise _perm(e)
        team = (svc.team_for(s, user.telegram_id, team_id)
                if team_id is not None else None)
        name = team.name if team else ""
    if not ok:
        raise HTTPException(status_code=404, detail="not_found")
    await notify_teammates(team_id, user.telegram_id, "team_ev_task_del",
                           title, name)
    return {"ok": True}


@app.post("/api/teams/{team_id}/habits")
async def api_team_habit_add(team_id: int, body: TeamHabitIn,
                             init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            created = svc.add_team_habit(
                s, user.telegram_id, team_id, body.name,
                schedule=body.schedule, category=body.category,
                target_time=_time(body.target_time),
                remind_at=_time(body.remind_at),
                timer_minutes=body.timer_minutes, start=body.start,
                tz=svc.user_tz(user))
        except PermissionError as e:
            raise _perm(e)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        team = svc.team_for(s, user.telegram_id, team_id)
        name = team.name if team else ""
    await notify_teammates(team_id, user.telegram_id, "team_ev_habit_add",
                           created["name"], name)
    return created


@app.post("/api/teams/habits/{habit_id}/toggle")
def api_team_habit_toggle(habit_id: int,
                          init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        try:
            done = svc.toggle_team_habit(s, user.telegram_id, habit_id,
                                         tz=svc.user_tz(user))
        except PermissionError as e:
            raise _perm(e)
        except ValueError as e:
            # Mirrored rituals are ticked in the member's own list; a timed
            # habit is finished by its timer.
            if str(e) in ("mirrored", "timer_required"):
                raise HTTPException(status_code=409, detail=str(e))
            raise HTTPException(status_code=404, detail="not_found")
    return {"done": done}


@app.delete("/api/teams/habits/{habit_id}")
async def api_team_habit_archive(habit_id: int,
                                 init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    with SessionLocal() as s:
        from db import TeamHabit
        row = s.get(TeamHabit, habit_id)
        label = row.name if row else ""
        team_id = row.team_id if row else None
        try:
            ok = svc.archive_team_habit(s, user.telegram_id, habit_id)
        except PermissionError as e:
            raise _perm(e)
        except ValueError:
            raise HTTPException(status_code=422, detail="protected")
        team = (svc.team_for(s, user.telegram_id, team_id)
                if team_id is not None else None)
        name = team.name if team else ""
    if not ok:
        raise HTTPException(status_code=404, detail="not_found")
    await notify_teammates(team_id, user.telegram_id, "team_ev_habit_del",
                           label, name)
    return {"ok": True}


@app.post("/webhook", include_in_schema=False)
async def telegram_webhook(request: Request):
    """Telegram's delivery endpoint, live only when WEBHOOK_URL is set.

    Refused outright when the bot is polling, so a stale webhook left over from
    an earlier deploy cannot inject updates into an instance that is also
    long-polling — that combination delivers everything twice.
    """
    if not WEBHOOK_URL or telegram_app is None:
        raise HTTPException(status_code=404, detail="not_found")
    # No secret configured means no way to tell Telegram from anybody else,
    # so nothing is accepted — the process refuses to start like this in
    # production, and a development instance refuses the update instead.
    given = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not WEBHOOK_SECRET or not hmac.compare_digest(given.encode(),
                                                     WEBHOOK_SECRET.encode()):
        # Wrong secret is somebody who found the path, not Telegram.
        raise HTTPException(status_code=403, detail="forbidden")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="payload_too_large")
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="payload_too_large")
    try:
        update = Update.de_json(json.loads(raw), telegram_app.bot)
    except Exception:
        log.warning("undecodable webhook payload")
        raise HTTPException(status_code=400, detail="bad_update")
    await telegram_app.process_update(update)
    return {"ok": True}


@app.get("/api/subscription")
async def api_subscription(init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Re-check channel membership from inside the Mini App.

    The blocked screen calls this behind its "check again" button, so joining the
    channel and coming back continues the session instead of restarting the app.
    Deliberately outside the membership gate — a blocked user is exactly who
    needs to call it — but still behind a valid signature.
    """
    tg_user = verify_init_data(init or "")
    telegram_id = accounts.resolve_id(int(tg_user["id"]))

    if not deps.REQUIRED_CHANNEL_ID:
        return {"subscribed": True, "state": "subscribed"}
    if telegram_app is None:
        # Nothing to ask Telegram with. Report the stored answer and say it is
        # unverified rather than inventing a pass or a block.
        with SessionLocal() as s:
            row = s.get(User, telegram_id)
            return {"subscribed": bool(row and row.is_subscribed),
                    "state": "unknown"}

    state = await is_subscribed(telegram_app.bot, telegram_id)
    if state is None:
        # Telegram could not be reached: neither grant nor revoke.
        return {"subscribed": False, "state": "unknown",
                "channel": deps.REQUIRED_CHANNEL_URL}
    with SessionLocal() as s:
        record_membership(s, telegram_id, state, "api")
        s.commit()
    return {"subscribed": state,
            "state": "subscribed" if state else "not_subscribed",
            "channel": deps.REQUIRED_CHANNEL_URL}


@app.get("/api/home")
def api_home(init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    tz = svc.user_tz(user)
    with SessionLocal() as s:
        data = svc.home(s, ws, s.get(User, user.telegram_id))
        # Today's shared tasks, beside today's own. The day's number already
        # counts them; a list that left them out would disagree with it (#16).
        today = svc.today_local(tz).isoformat()
        data["team_today"] = [
            x for x in svc.team_items_for_day(s, user.telegram_id, tz=tz)["tasks"]
            if x.get("owed", True) and x.get("deadline")
            and (x["deadline"] == today or (x["deadline"] < today and not x["done"]))]
        return data


@app.get("/api/habits")
def api_habits(day: str | None = None, init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    tz = svc.user_tz(user)
    with SessionLocal() as s:
        target = _date(day)
        shared = svc.team_items_for_day(s, user.telegram_id, target, tz=tz)
        return {"habits": svc.list_habits(s, ws, target, tz=tz),
                "grouped": svc.habits_by_category(s, ws, target, tz=tz),
                "categories": svc.HABIT_CATEGORIES,
                # Shared work, shown on this screen and scored on its own.
                "team_habits": shared["habits"],
                "teams": shared["teams"],
                # Per-tier completion and the weight each tier actually carries
                # today. Sent from here rather than recomputed in the browser:
                # the weighting is the score's own arithmetic, and a second
                # copy of it in JavaScript is a second copy that can disagree.
                "tiers": svc.habit_tier_progress(s, ws, target or svc.today_local(tz)),
                "wake": svc.wake_state(s, ws, tz=tz),
                "active_timer": svc.active_timer(s, ws),
                "streak": svc.habit_streak(s, ws, tz=tz),
                # Which teams each ritual is also shown in, for the habit's
                # own sheet to offer personal ↔ team.
                "shared_rituals": svc.shared_rituals(s, user.telegram_id)}


@app.post("/api/habits")
def api_habit_add(body: HabitIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    with SessionLocal() as s:
        habit = svc.add_habit(s, ws, body.name, body.category,
                              schedule=body.schedule,
                              remind_at=_time(body.remind_at),
                              timer_minutes=body.timer_minutes,
                              start=body.start, tz=svc.user_tz(user))
    return {"ok": True, "id": habit.id}


class HabitPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=16)
    schedule: str | None = Field(default=None, max_length=24)
    remind_at: str | None = Field(default=None, max_length=5)
    target_time: str | None = Field(default=None, max_length=5)
    #: Timer length in minutes: 0 switches it off, null reads it from the name.
    timer_minutes: int | None = Field(default=None, ge=0, le=24 * 60)


class HabitPauseIn(BaseModel):
    paused: bool
    #: "today" (default) takes today out as well; "tomorrow" keeps today
    #: owed. The screen offers both and says which one today's number shows.
    from_day: str = Field(default="today", alias="from", max_length=10)

    model_config = {"populate_by_name": True}


@app.post("/api/habits/{habit_id}/pause")
def api_habit_pause(habit_id: int, body: HabitPauseIn,
                    init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Pause or resume a habit. Every past log survives either way."""
    _, ws = auth(init)
    with SessionLocal() as s:
        habit = svc.set_habit_paused(s, ws, habit_id, body.paused,
                                     from_day=body.from_day)
    return {"ok": True, "paused": habit.paused_at is not None}


@app.get("/api/habits/{habit_id}/history")
def api_habit_history(habit_id: int, days: int = 30,
                      init=Header(default=None, alias="X-Telegram-Init-Data")):
    """One habit's streak, grid and completion rate."""
    user, ws = auth(init)
    with SessionLocal() as s:
        return svc.habit_history(s, ws, habit_id,
                                 days=max(7, min(days, 365)),
                                 tz=svc.user_tz(user))


@app.patch("/api/habits/reorder")
def api_habit_reorder(body: HabitOrderIn,
                      init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Persist the order the user dragged the habits into.

    Declared before `/api/habits/{habit_id}` so "reorder" is never parsed as a
    habit id. Ownership is checked inside the service: an id from another
    workspace is a 404, the same answer as an id that never existed.
    """
    _, ws = auth(init)
    with SessionLocal() as s:
        try:
            habits = svc.reorder_habits(s, ws, body.habit_ids)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True, "habits": habits}


#: Declared after `/api/habits/reorder` for the same reason that route carries
#: its own note: FastAPI matches in declaration order, so a `{habit_id}` PATCH
#: placed above it would swallow "reorder" and answer 422.
@app.patch("/api/habits/{habit_id}")
def api_habit_patch(habit_id: int, body: HabitPatch,
                    init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Rename a habit, or change its category, schedule, reminder or target."""
    _, ws = auth(init)
    fields = body.model_dump(exclude_unset=True)
    for key in ("remind_at", "target_time"):
        if key in fields:
            fields[key] = _time(fields[key])
    with SessionLocal() as s:
        try:
            svc.update_habit(s, ws, habit_id, **fields)
        except ValueError as e:
            if str(e) == "protected":
                raise HTTPException(status_code=400, detail="protected_habit")
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}


@app.post("/api/habits/{habit_id}/toggle")
def api_habit_toggle(habit_id: int, init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    with SessionLocal() as s:
        try:
            done = svc.toggle_habit(s, ws, habit_id, tz=svc.user_tz(user))
        except ValueError as e:
            if str(e) == "timer_required":
                # Not an error on the client's part: the habit is done by its
                # timer, and the screen should open the timer instead.
                raise HTTPException(status_code=409, detail="timer_required")
            raise HTTPException(status_code=400, detail="protected_habit")
        # The new counts come back with the toggle, so the row and the header
        # both settle in one round trip instead of two.
        habits_done, habits_total = svc.habit_progress(
            s, ws, svc.today_local(svc.user_tz(user)))
        return {"ok": True, "done": done,
                "habits": {"done": habits_done, "total": habits_total},
                "streak": svc.habit_streak(s, ws, tz=svc.user_tz(user))}


@app.delete("/api/habits/{habit_id}")
def api_habit_delete(habit_id: int, init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Remove any habit, the three rituals included.

    Nothing is erased: the habit is archived with every log it had, and
    "Odatlar ro'yxati" (the ready-made ten) or Restore brings it back with
    its history. Removing a ritual switches its module off.
    """
    _, ws = auth(init)
    with SessionLocal() as s:
        svc.remove_habit(s, ws, habit_id)
    return {"ok": True}


@app.get("/api/habits/presets")
def api_habit_presets(init=Header(default=None, alias="X-Telegram-Init-Data")):
    """The ready-made ten, each marked with whether it is on the list."""
    user, ws = auth(init)
    with SessionLocal() as s:
        return {"presets": svc.habit_presets(s, ws, user.language)}


class RitualShareIn(BaseModel):
    key: str = Field(min_length=1, max_length=16)
    team_id: int
    #: True shows the ritual in the team, False takes it out again.
    on: bool = True


@app.post("/api/rituals/share")
async def api_ritual_share(body: RitualShareIn,
                           init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Get up, prayer or the journal: personal, and also shown in a team."""
    user, _ = auth(init)
    if body.key not in svc.SYSTEM_KEYS:
        raise HTTPException(status_code=422, detail="not_a_ritual")
    with SessionLocal() as s:
        try:
            if body.on:
                label = svc.share_ritual(s, user.telegram_id, body.team_id,
                                         body.key)["name"]
            else:
                label = svc.unshare_ritual_in(s, user.telegram_id, body.team_id,
                                              body.key)
                if label is None:
                    return {"ok": True,
                            "shared": svc.shared_rituals(s, user.telegram_id)}
        except PermissionError as e:
            raise _perm(e)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        team = svc.team_for(s, user.telegram_id, body.team_id)
        name = team.name if team else ""
        shared = svc.shared_rituals(s, user.telegram_id)
    await notify_teammates(body.team_id, user.telegram_id,
                           "team_ev_habit_add" if body.on else "team_ev_habit_del",
                           label, name)
    return {"ok": True, "shared": shared}


class PresetIn(BaseModel):
    key: str = Field(min_length=1, max_length=24)
    #: True puts it on the list, False takes it off.
    on: bool = True


@app.post("/api/habits/presets")
def api_habit_preset_set(body: PresetIn,
                         init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    if body.key not in svc.PRESET_KEYS:
        raise HTTPException(status_code=422, detail="unknown_preset")
    with SessionLocal() as s:
        if body.on:
            svc.add_preset(s, ws, body.key, user.language, tz=svc.user_tz(user))
        else:
            svc.remove_preset(s, ws, body.key)
        return {"ok": True, "presets": svc.habit_presets(s, ws, user.language)}


# --- timers ------------------------------------------------------------------

class TimerSetIn(BaseModel):
    #: Minutes; 0 switches the timer off, null goes back to reading the name.
    minutes: int | None = Field(default=None, ge=0, le=24 * 60)


def _timer_kind(kind: str) -> str:
    if kind not in svc.TIMER_KINDS:
        raise HTTPException(status_code=404, detail="not_found")
    return kind


def _timer_refused(e: ValueError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(e) or "timer_refused")


@app.get("/api/timers/active")
def api_timer_active(init=Header(default=None, alias="X-Telegram-Init-Data")):
    """The timer counting right now, if any — every screen shows it."""
    _, ws = auth(init)
    with SessionLocal() as s:
        return {"timer": svc.active_timer(s, ws)}


@app.get("/api/timers/candidates/{kind}")
def api_timer_candidates(kind: str,
                         init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Everything a timer can go on — private and shared — for the Time
    countdown screen. `kind` is habit or task."""
    user, ws = auth(init)
    if kind not in ("habit", "task"):
        raise HTTPException(status_code=404, detail="not_found")
    with SessionLocal() as s:
        return {"items": svc.timer_candidates(s, ws, user.telegram_id, kind,
                                              tz=svc.user_tz(user)),
                "active": svc.active_timer(s, ws)}


@app.get("/api/timers/{kind}/{item_id}")
def api_timer_get(kind: str, item_id: int,
                  init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    with SessionLocal() as s:
        return svc.timer_for(s, ws, _timer_kind(kind), item_id,
                             tz=svc.user_tz(user))


@app.put("/api/timers/{kind}/{item_id}")
def api_timer_set(kind: str, item_id: int, body: TimerSetIn,
                  init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Set how long an item's timer runs, switch it off, or go back to auto."""
    _, ws = auth(init)
    with SessionLocal() as s:
        try:
            return svc.set_item_timer(s, ws, _timer_kind(kind), item_id,
                                      body.minutes)
        except ValueError as e:
            raise _timer_refused(e)


@app.post("/api/timers/{kind}/{item_id}/start")
def api_timer_start(kind: str, item_id: int,
                    init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    tz = svc.user_tz(user)
    with SessionLocal() as s:
        try:
            svc.start_timer(s, ws, _timer_kind(kind), item_id, tz=tz)
        except ValueError as e:
            raise _timer_refused(e)
        return svc.timer_for(s, ws, kind, item_id, tz=tz)


@app.post("/api/timers/runs/{run_id}/{verb}")
def api_timer_run(run_id: int, verb: str,
                  init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Pause, resume or stop a run. Answers with the item's timer screen."""
    user, ws = auth(init)
    action = {"pause": svc.pause_timer, "resume": svc.resume_timer,
              "stop": svc.stop_timer}.get(verb)
    if action is None:
        raise HTTPException(status_code=404, detail="not_found")
    with SessionLocal() as s:
        try:
            run = action(s, ws, run_id)
        except ValueError as e:
            raise _timer_refused(e)
        return svc.timer_for(s, ws, run["kind"], run["item_id"],
                             tz=svc.user_tz(user))


# --- countdowns ---------------------------------------------------------------

class CountdownIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    #: ISO date from the date picker; typed forms ("15 noyabr", "30 kun") are
    #: accepted too, the same ones the bot reads.
    date: str = Field(min_length=1, max_length=40)
    #: general | task | habit — what it is counting down to.
    scope: str = Field(default="general", max_length=10)
    #: The task or habit it belongs to, when it belongs to one.
    item_id: int | None = None
    #: A team's countdown: every member sees it.
    team_id: int | None = None


class CountdownPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    date: str | None = Field(default=None, max_length=40)


def _countdown_date(value: str, tz) -> date:
    found = svc.parse_countdown_date(value, svc.today_local(tz))
    if found is None:
        raise HTTPException(status_code=422, detail="bad_date")
    return found


@app.get("/api/countdowns")
def api_countdowns(init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    with SessionLocal() as s:
        # Private and shared together, soonest first; each says whose it is.
        return {"countdowns": svc.countdowns_for_user(s, ws, user.telegram_id,
                                                      tz=svc.user_tz(user)),
                "scopes": list(svc.COUNTDOWN_SCOPES)}


@app.post("/api/countdowns")
def api_countdown_add(body: CountdownIn,
                      init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    tz = svc.user_tz(user)
    with SessionLocal() as s:
        try:
            made = svc.add_countdown(s, ws, body.title,
                                     _countdown_date(body.date, tz), tz=tz,
                                     scope=body.scope, item_id=body.item_id,
                                     team_id=body.team_id, user_id=user.telegram_id)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return made


@app.patch("/api/countdowns/{countdown_id}")
def api_countdown_edit(countdown_id: int, body: CountdownPatch,
                       init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    tz = svc.user_tz(user)
    with SessionLocal() as s:
        try:
            return svc.update_countdown(
                s, ws, countdown_id, title=body.title,
                target=_countdown_date(body.date, tz) if body.date else None,
                tz=tz)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))


@app.delete("/api/countdowns/{countdown_id}")
def api_countdown_delete(countdown_id: int,
                         init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        svc.delete_countdown(s, ws, countdown_id)
    return {"ok": True}



@app.get("/api/prayers")
def api_prayers(day: str | None = None, init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    with SessionLocal() as s:
        return svc.prayer_state(s, ws, _date(day) or svc.today_local(), user.gender)


@app.post("/api/prayers")
def api_prayer_set(body: PrayerIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    tz = svc.user_tz(user)
    with SessionLocal() as s:
        try:
            svc.set_prayer(s, ws, body.prayer, body.status, user.gender,
                           _date(body.day), tz=tz)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        # Return the whole day, so one tap updates the score, the 5/5 count and
        # the habit tick together rather than in three separate requests.
        return {"ok": True,
                **svc.prayer_state(s, ws, _date(body.day) or svc.today_local(tz),
                                   user.gender)}


class PrayerClearIn(BaseModel):
    prayer: str = Field(max_length=10)
    day: str | None = Field(default=None, max_length=10)


@app.post("/api/prayers/clear")
def api_prayer_clear(body: PrayerClearIn,
                     init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Undo one prayer entry. A mis-tap has to be reversible."""
    user, ws = auth(init)
    tz = svc.user_tz(user)
    with SessionLocal() as s:
        try:
            svc.clear_prayer(s, ws, body.prayer, user.gender, _date(body.day), tz=tz)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return {"ok": True,
                **svc.prayer_state(s, ws, _date(body.day) or svc.today_local(tz),
                                   user.gender)}


@app.post("/api/prayers/excused")
def api_prayer_excused(body: ExcusedIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    tz = svc.user_tz(user)
    with SessionLocal() as s:
        try:
            svc.set_excused(s, ws, body.excused, user.gender, _date(body.day), tz=tz)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return {"ok": True,
                **svc.prayer_state(s, ws, _date(body.day) or svc.today_local(tz),
                                   user.gender)}


@app.get("/api/tasks")
def api_tasks(days: int = 7, q: str = "", project_id: int | None = None,
              priority: str = "",
              init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Open tasks, optionally narrowed by text, project or priority."""
    user, ws = auth(init)
    tz = svc.user_tz(user)
    with SessionLocal() as s:
        out = svc.list_tasks(s, ws, horizon_days=max(0, min(days, 365)),
                             search=q[:100], project_id=project_id,
                             priority=priority, tz=tz)
        # Shared work belongs on this screen too — a day's plan that leaves
        # out half of what you owe is not a plan — but under its own key, so
        # nothing that scores the personal day can pick it up by accident.
        shared = svc.team_items_for_day(s, user.telegram_id, tz=tz)
        out["team_tasks"] = shared["tasks"]
        out["teams"] = shared["teams"]
        out["active_timer"] = svc.active_timer(s, ws)
        return out


@app.post("/api/tasks")
def api_task_add(body: TaskIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        task = svc.add_task(s, ws, body.title, deadline=_date(body.deadline),
                            project_id=body.project_id, priority=body.priority,
                            description=body.description,
                            due_time=_time(body.due_time),
                            remind_before=body.remind_before,
                            recurrence=body.recurrence,
                            timer_minutes=body.timer_minutes)
    return {"ok": True, "id": task.id}


@app.patch("/api/tasks/{task_id}")
def api_task_patch(task_id: int, body: TaskPatch,
                   init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    fields = body.model_dump(exclude_unset=True)
    if "deadline" in fields:
        fields["deadline"] = _date(fields["deadline"])
    if "due_time" in fields:
        fields["due_time"] = _time(fields["due_time"])
    with SessionLocal() as s:
        try:
            svc.update_task(s, ws, task_id, **fields)
        except ValueError as e:
            if str(e) == "timer_required":
                raise HTTPException(status_code=409, detail="timer_required")
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}


class RescheduleIn(BaseModel):
    #: today | tomorrow | week | none
    when: str = Field(max_length=10)


@app.post("/api/tasks/{task_id}/reschedule")
def api_task_reschedule(task_id: int, body: RescheduleIn,
                        init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Move one task's deadline with a single tap.

    This is what the overdue rows offer instead of a red wall: today, tomorrow,
    next week, or no date at all.
    """
    user, ws = auth(init)
    with SessionLocal() as s:
        try:
            task = svc.reschedule_task(s, ws, task_id, body.when,
                                       tz=svc.user_tz(user))
        except ValueError:
            raise HTTPException(status_code=422, detail="bad_target")
    return {"ok": True, "deadline": task.deadline.isoformat() if task.deadline else None}


class Top3In(BaseModel):
    picked: bool


@app.post("/api/tasks/{task_id}/top3")
def api_task_top3(task_id: int, body: Top3In,
                  init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Pick or unpick one of today's three most important tasks."""
    user, ws = auth(init)
    with SessionLocal() as s:
        try:
            result = svc.set_top3(s, ws, task_id, body.picked,
                                  tz=svc.user_tz(user))
        except ValueError:
            raise HTTPException(status_code=409, detail="top3_full")
    return {"ok": True, **result}


@app.delete("/api/tasks/{task_id}")
def api_task_delete(task_id: int, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        svc.delete_task(s, ws, task_id)
    return {"ok": True}


@app.get("/api/projects")
def api_projects(status: str = "", archived: bool = False,
                 init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        return {"projects": svc.list_projects(s, ws, status=status,
                                              include_archived=archived),
                "statuses": svc.PROJECT_STATUSES}


@app.post("/api/projects")
def api_project_add(body: ProjectIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        project = svc.add_project(s, ws, body.name, description=body.description,
                                  deadline=_date(body.deadline))
    return {"ok": True, "id": project.id}


@app.patch("/api/projects/{project_id}")
def api_project_patch(project_id: int, body: ProjectPatch,
                      init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    fields = body.model_dump(exclude_unset=True)
    if "deadline" in fields:
        fields["deadline"] = _date(fields["deadline"])
    with SessionLocal() as s:
        try:
            svc.update_project(s, ws, project_id, **fields)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}


@app.get("/api/projects/{project_id}/tasks")
def api_project_tasks(project_id: int,
                      init=Header(default=None, alias="X-Telegram-Init-Data")):
    """A project and the work inside it — what the project detail view shows."""
    _, ws = auth(init)
    with SessionLocal() as s:
        project = next((p for p in svc.list_projects(s, ws)
                        if p["id"] == project_id), None)
        if project is None:
            raise svc.NotFound("project")
        return {"project": project, "tasks": svc.project_tasks(s, ws, project_id)}


@app.delete("/api/projects/{project_id}")
def api_project_delete(project_id: int, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        svc.delete_project(s, ws, project_id)
    return {"ok": True}


@app.get("/api/focus")
def api_focus(init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    tz = svc.user_tz(user)
    with SessionLocal() as s:
        return {"focus": svc.list_focus(s, ws, tz=tz),
                "week": svc.week_focus(s, ws, tz=tz),
                "max": svc.MAX_FOCUS}


@app.post("/api/focus/{focus_id}/carry")
def api_focus_carry(focus_id: int,
                    init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Move an unfinished mission into next week instead of retyping it."""
    user, ws = auth(init)
    with SessionLocal() as s:
        try:
            row = svc.carry_focus_forward(s, ws, focus_id, tz=svc.user_tz(user))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True, "id": row.id, "week_start": row.week_start.isoformat()}


@app.post("/api/focus")
def api_focus_add(body: FocusIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        try:
            row = svc.add_focus(s, ws, body.title,
                                priority=body.priority or svc.DEFAULT_MISSION_PRIORITY,
                                task_id=body.task_id)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True, "id": row.id}


@app.post("/api/focus/{focus_id}/toggle")
def api_focus_toggle(focus_id: int, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        return {"ok": True, "done": svc.toggle_focus(s, ws, focus_id)}


@app.delete("/api/focus/{focus_id}")
def api_focus_delete(focus_id: int, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        svc.delete_focus(s, ws, focus_id)
    return {"ok": True}


@app.get("/api/journal")
def api_journal(day: str | None = None, init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    tz = svc.user_tz(user)
    with SessionLocal() as s:
        questions = [{"id": q["id"], "text": q.get(user.language, q["uz"])}
                     for q in svc.JOURNAL_QUESTIONS]
        payload = {"questions": questions, "moods": svc.MOODS,
                   "total": len(svc.JOURNAL_KEYS)}
        if day:
            return {**payload, "entry": svc.get_journal(s, ws, _date(day), tz=tz)}
        return {**payload, "entries": svc.list_journal(s, ws)}


@app.post("/api/journal")
def api_journal_save(body: JournalIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Save whatever has been written so far.

    Answers are merged, not replaced, so an autosave carrying one field cannot
    wipe the others. A partial entry is saved as a partial entry — three of five
    is not a failed day, and the response says so plainly.
    """
    user, ws = auth(init)
    tz = svc.user_tz(user)
    with SessionLocal() as s:
        row = svc.save_journal(s, ws, answers=body.answers, text=body.text,
                               day=_date(body.day), mood=body.mood, tz=tz)
        entry = svc.get_journal(s, ws, row.day, tz=tz)
    return {"ok": True, "day": row.day.isoformat(),
            "answered": entry["answered"] if entry else 0,
            "total": len(svc.JOURNAL_KEYS),
            "complete": bool(entry and entry["complete"])}


@app.delete("/api/journal/{day}")
def api_journal_delete(day: str, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        svc.delete_journal(s, ws, _date(day))
    return {"ok": True}


class QuickAddIn(BaseModel):
    """The whole of quick capture: a line of text, nothing else."""
    title: str = Field(min_length=1, max_length=300)


@app.post("/api/quick")
def api_quick_add(body: QuickAddIn,
                  init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Capture a thought without asking for a deadline, project or priority.

    Making someone answer three questions before a note is saved is how notes
    stop getting saved. Sorting happens later, in Tasks.
    """
    _, ws = auth(init)
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="empty_title")
    with SessionLocal() as s:
        task = svc.add_task(s, ws, body.title)
    return {"ok": True, "id": task.id}


class FreshStartIn(BaseModel):
    mode: str = Field(default="today", max_length=8)


@app.get("/api/fresh-start")
def api_fresh_start_preview(init=Header(default=None,
                                        alias="X-Telegram-Init-Data")):
    """What a reset would touch, before anything is touched.

    The confirmation names a real number of real tasks, and each mode says what
    happens to them. Nothing here writes.
    """
    user, ws = auth(init)
    with SessionLocal() as s:
        row = s.get(User, user.telegram_id)
        state = svc.break_state(s, ws, row)
    return {"overdue": state["overdue"], "days_away": state["days_away"],
            "modes": list(svc.FRESH_START_MODES)}


@app.post("/api/fresh-start")
def api_fresh_start(body: FreshStartIn,
                    init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Clear a backlog built up during a break, in one decision.

    No mode deletes anything: tasks are moved, un-dated or archived. That is
    what lets the confirmation promise the history is intact.
    """
    user, ws = auth(init)
    mode = body.mode if body.mode in svc.FRESH_START_MODES else "today"
    with SessionLocal() as s:
        moved = svc.fresh_start(s, ws, mode=mode, tz=svc.user_tz(user))
    return {"ok": True, "moved": moved, "mode": mode}


class ReviewIn(BaseModel):
    went_well: str = Field(default="", max_length=2000)
    blocked: str = Field(default="", max_length=2000)
    next_focus: str = Field(default="", max_length=2000)


@app.get("/api/review")
def api_review(init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    with SessionLocal() as s:
        return svc.weekly_review(s, ws, s.get(User, user.telegram_id))


@app.post("/api/review")
def api_review_save(body: ReviewIn,
                    init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        svc.save_weekly_review(s, ws, went_well=body.went_well,
                               blocked=body.blocked, next_focus=body.next_focus)
    return {"ok": True}


@app.get("/api/stats")
def api_stats(period: str = "week", init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Series for the task, habit, prayer and overall charts, plus streaks."""
    user, ws = auth(init)
    if period not in ("week", "month", "year"):
        period = "week"
    with SessionLocal() as s:
        return svc.stats(s, ws, period, gender=user.gender,
                         tz=svc.user_tz(user))


@app.get("/api/progress/me")
def api_progress_me(init=Header(default=None, alias="X-Telegram-Init-Data")):
    """This caller's own score, XP, level, streak and rank. Never anybody else's.

    Same shape of protection as `/api/referrals/me`, and for the same reason:
    no user id appears in the path or the query, so the identity comes from the
    Telegram signature and there is no parameter anybody could change to read a
    stranger's productivity. Removing the question is stronger than answering
    it correctly.

    What ranking discloses about other people is a *count* and a *position* —
    "#184 of 12,842" — and nothing else. No names, no usernames, no Telegram
    ids, no other person's scores.

    This reads. It never recomputes: the day is scored when the day changes,
    on the action funnel, so opening a profile is a handful of indexed row
    reads rather than an aggregation over history.
    """
    user, _ws = auth(init)
    with SessionLocal() as s:
        snapshot = svc.progress_snapshot(s, user.telegram_id,
                                         tz=svc.user_tz(user))
        # `global_rank` refreshes the stored best and last rank as it reads,
        # which is the only write on this path and is what makes "↑7" and
        # "personal best" honest rather than recomputed guesses.
        s.commit()
    return snapshot


@app.get("/api/progress/achievements")
def api_progress_achievements(init=Header(default=None,
                                          alias="X-Telegram-Init-Data")):
    """The thirteen achievements, locked and unlocked, with progress.

    All of them, not just the earned ones: a locked row reading "7 / 30" is the
    part that motivates, and a screen showing only what somebody already has
    cannot do that. Thirteen rows, so there is nothing to paginate.
    """
    user, _ws = auth(init)
    with SessionLocal() as s:
        return {"achievements": svc.achievement_state(s, user.telegram_id)}


@app.get("/api/referrals/me")
def api_referrals_me(init=Header(default=None, alias="X-Telegram-Init-Data")):
    """This caller's own invite link and counts. Never anybody else's.

    There is deliberately no user id in the path or the query. The identity
    comes from the Telegram signature and nowhere else, which means there is no
    parameter to change and therefore no way to ask for a stranger's referral
    data — the strongest form of the ownership check, because it removes the
    question rather than answering it.

    The response carries counts, a level and a link. It does not carry who was
    invited: an inviter needs to know *that* four people joined, and telling
    them *who* would hand one user a roster of others.
    """
    user, _ws = auth(init)
    with SessionLocal() as s:
        stats = svc.referral_stats(s, user.telegram_id)
        code = svc.get_or_create_referral_code(s, user.telegram_id)

    link = referral_link(code)
    if link is None:
        # Sharing is off, not broken. The screen says so plainly instead of
        # offering a button that would copy a malformed URL.
        log.warning("BOT_USERNAME is not set — referral links cannot be built")
    return {
        "configured": link is not None,
        "code": code,
        "link": link,
        "miniapp_link": referral_miniapp_link(code),
        "counts": stats["counts"],
        "level": {"key": stats["level"]["key"],
                  "minimum": stats["level"]["minimum"]},
        "next_milestone": stats["level"]["next"],
        "qualify_actions": svc.REFERRAL_QUALIFY_ACTIONS,
    }


@app.get("/api/summary")
def api_summary(init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Today, the last 7 days and the last 30, in the same units.

    What Home's percentage switch reads, and what the bot's Statistics message
    is built from — one function, so the two can never disagree.
    """
    user, ws = auth(init)
    with SessionLocal() as s:
        return svc.summary(s, ws, gender=user.gender, tz=svc.user_tz(user))


@app.get("/api/overall")
def api_overall(day: str | None = None,
                init=Header(default=None, alias="X-Telegram-Init-Data")):
    """How today's percentage was arrived at, component by component.

    This is what the info button on the number opens. It comes from the same
    functions that produce the number, so the explanation cannot drift from the
    thing it explains.
    """
    user, ws = auth(init)
    with SessionLocal() as s:
        return svc.overall_explain(s, ws, s.get(User, user.telegram_id),
                                   _date(day))


@app.post("/api/stats/export")
async def api_stats_export(period: str = "month",
                           init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Send the statistics CSV to the user as a Telegram file.

    Telegram's in-app browser blocks ordinary downloads, and opening the URL
    externally would leak the credential (audit 062). Delivering the file
    through the bot avoids both and lands it where the user can keep it.
    """
    user, ws = auth(init)
    if period not in ("week", "month", "year"):
        period = "month"
    if telegram_app is None:
        raise HTTPException(status_code=503, detail="bot_unavailable")

    with SessionLocal() as s:
        body = svc.stats_csv(s, ws, period, gender=user.gender,
                             tz=svc.user_tz(user))

    stamp = datetime.now(svc.user_tz(user)).strftime("%Y-%m-%d")
    document = InputFile(body.encode("utf-8"),
                         filename=f"ernestos-{period}-{stamp}.csv")
    try:
        await telegram_app.bot.send_document(
            chat_id=user.telegram_id, document=document,
            caption=f"ErnestOS — {period}")
    except TelegramError as e:
        log.warning("stats export to %s failed: %s", user.telegram_id, e)
        raise HTTPException(status_code=502, detail="delivery_failed")
    return {"ok": True, "delivered": "telegram"}


@app.get("/api/calendar")
def api_calendar(year: int | None = None, month: int | None = None,
                 init=Header(default=None, alias="X-Telegram-Init-Data")):
    """One month of task deadlines, project dates and birthdays."""
    user, ws = auth(init)
    tz = svc.user_tz(user)
    today = svc.today_local(tz)
    year, month = year or today.year, month or today.month
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        raise HTTPException(status_code=422, detail="bad_month")
    with SessionLocal() as s:
        return svc.calendar_month(s, ws, year, month, tz=tz)


@app.get("/api/tasks/done")
def api_tasks_done(q: str = "", init=Header(default=None, alias="X-Telegram-Init-Data")):
    """The Done archive — completed tasks are kept, never deleted.

    Grouped into today / this week / earlier, and searchable, because a flat
    list of four hundred finished things is a place nothing can be found.
    """
    user, ws = auth(init)
    with SessionLocal() as s:
        groups = svc.completed_tasks(s, ws, search=q[:100],
                                     tz=svc.user_tz(user))
    # `tasks` stays for anything still reading the old flat shape.
    return {"groups": groups, "total": groups["total"],
            "tasks": groups["today"] + groups["week"] + groups["earlier"]}


@app.patch("/api/focus/{focus_id}")
def api_focus_edit(focus_id: int, body: FocusIn,
                   init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        try:
            if "task_id" in body.model_fields_set:
                svc.edit_focus(s, ws, focus_id, body.title, priority=body.priority,
                               task_id=body.task_id)
            else:
                svc.edit_focus(s, ws, focus_id, body.title, priority=body.priority)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}


@app.get("/api/birthdays")
def api_birthdays(init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    with SessionLocal() as s:
        return {"birthdays": svc.list_birthdays(s, ws, within_days=366,
                                                tz=svc.user_tz(user))}


class BirthdayPatch(BaseModel):
    person_name: str | None = Field(default=None, max_length=200)
    birth_date: str | None = Field(default=None, max_length=10)
    note: str | None = Field(default=None, max_length=300)


@app.patch("/api/birthdays/{birthday_id}")
def api_birthday_patch(birthday_id: int, body: BirthdayPatch,
                       init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    fields = body.model_dump(exclude_unset=True)
    if "birth_date" in fields:
        fields["birth_date"] = _date(fields["birth_date"])
    with SessionLocal() as s:
        try:
            svc.update_birthday(s, ws, birthday_id, **fields)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}


@app.post("/api/birthdays")
def api_birthday_add(body: BirthdayIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    parsed = _date(body.birth_date)
    if parsed is None:
        raise HTTPException(status_code=422, detail="bad_date")
    with SessionLocal() as s:
        row = svc.add_birthday(s, ws, body.person_name, parsed, body.note)
    return {"ok": True, "id": row.id}


@app.delete("/api/birthdays/{birthday_id}")
def api_birthday_delete(birthday_id: int, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        svc.delete_birthday(s, ws, birthday_id)
    return {"ok": True}


# --- money: its own place, outside every productivity number ------------------

class MoneyIn(BaseModel):
    kind: str = Field(pattern="^(expense|income)$")
    amount: int = Field(gt=0, le=svc.MONEY_MAX_AMOUNT)
    category: str = Field(default="", max_length=24)
    note: str = Field(default="", max_length=200)
    source: str = Field(default="manual", max_length=8)
    #: A past day may be named (an undo puts an entry back where it was);
    #: a future one may not.
    day: str | None = Field(default=None, max_length=10)


class MoneyTextIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    source: str = Field(default="manual", max_length=8)
    #: The Chiqim / Kirim button that was pressed; empty lets the words decide.
    kind: str = Field(default="", max_length=8)


class MoneyBudgetIn(BaseModel):
    limit: int = Field(ge=0, le=svc.MONEY_MAX_AMOUNT)


def _money_month(value: str | None) -> date | None:
    if not value:
        return None
    try:
        year, month = (int(x) for x in value.split("-")[:2])
        return date(year, month, 1)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="bad_month")


@app.get("/api/money")
def api_money(month: str | None = None,
              init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    with SessionLocal() as s:
        return svc.money_overview(s, ws, month=_money_month(month),
                                  tz=svc.user_tz(user))


@app.post("/api/money")
def api_money_add(body: MoneyIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    tz = svc.user_tz(user)
    day = _date(body.day)
    if day is not None and day > svc.today_local(tz):
        raise HTTPException(status_code=422, detail="future_day")
    with SessionLocal() as s:
        try:
            return svc.add_money(s, ws, body.kind, body.amount, body.category,
                                 note=body.note, source=body.source, day=day, tz=tz)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/money/text")
def api_money_text(body: MoneyTextIn,
                   init=Header(default=None, alias="X-Telegram-Init-Data")):
    """"Tushlikka 45 ming sarfladim" — typed or spoken — straight to an entry."""
    user, ws = auth(init)
    parsed = svc.parse_money_text(body.text, body.kind or None)
    if parsed is None:
        raise HTTPException(status_code=422, detail="no_amount")
    with SessionLocal() as s:
        return svc.add_money(s, ws, parsed["kind"], parsed["amount"],
                             parsed["category"], note=parsed["note"],
                             source=body.source, tz=svc.user_tz(user))


@app.delete("/api/money/{entry_id}")
def api_money_delete(entry_id: int,
                     init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        return {"ok": True, "entry": svc.delete_money(s, ws, entry_id)}


@app.put("/api/money/budgets/{category}")
def api_money_budget(category: str, body: MoneyBudgetIn,
                     init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        try:
            return {"ok": True, "limit": svc.set_money_budget(s, ws, category, body.limit)}
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))


@app.get("/api/avatar")
async def api_avatar(token: str | None = None, tgdata: str | None = None,
                     init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Stream the user's profile photo.

    A browser `<img src=...>` cannot attach a header, so the credential has to
    travel in the URL — and a URL is logged, kept in history and leaked in a
    Referer. `?token=` is what the Mini App now sends: signed the same way, but
    naming one user and expiring in minutes rather than a day (`/api/me`
    issues it). `?tgdata=` stays accepted so that a page still open from
    before this change keeps working; it can be dropped once they have all
    reloaded.
    """
    if token:
        with SessionLocal() as s:
            user = s.get(User, verify_avatar_token(token))
        if user is None:
            raise HTTPException(status_code=401, detail="unauthorized")
    else:
        user, _ = auth(init or tgdata)
    if not user.photo_file_id or telegram_app is None:
        raise HTTPException(status_code=404, detail="no_photo")
    try:
        tg_file = await telegram_app.bot.get_file(user.photo_file_id)
        data = await tg_file.download_as_bytearray()
    except TelegramError:
        raise HTTPException(status_code=404, detail="no_photo")
    return Response(content=bytes(data), media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=300"})


class WakeupIn(BaseModel):
    #: When somebody actually got up, entered afterwards ("HH:MM", today).
    at: str | None = Field(default=None, max_length=5)


@app.post("/api/wakeup")
def api_wakeup(body: WakeupIn | None = None,
               init=Header(default=None, alias="X-Telegram-Init-Data")):
    """The Mini App's own "Turdim" button.

    Identical to the bot's, through the same service function, so the button on
    Home is the real thing rather than an instruction to go and use the chat.
    With `at`, the time is the one somebody got up rather than the one they
    remembered to press the button.
    """
    user, ws = auth(init)
    tz = svc.user_tz(user)
    at = _time(body.at) if body and body.at else None
    with SessionLocal() as s:
        try:
            result = svc.mark_wakeup(s, ws, tz=tz, at=at)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        # The habit counters move with it, so Home can settle in one request.
        habits_done, habits_total = svc.habit_progress(s, ws, svc.today_local(tz))
        return {**result,
                "habits": {"done": habits_done, "total": habits_total},
                "streak": svc.habit_streak(s, ws, tz=tz)}


class FeedbackIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@app.post("/api/feedback")
async def api_feedback(body: FeedbackIn,
                       init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Send feedback from inside the Mini App.

    Stored first, delivered second, and the response says which of those
    actually happened — claiming "sent" for a message that never left would be
    the one thing worse than no feedback button at all.
    """
    user, ws = auth(init)
    with SessionLocal() as s:
        row = svc.save_feedback(s, ws, user.telegram_id, body.message)
        feedback_id = row.id

    delivered = False
    if FEEDBACK_CHANNEL_ID and telegram_app is not None:
        try:
            await telegram_app.bot.send_message(
                chat_id=FEEDBACK_CHANNEL_ID,
                text=(f"<b>💬 ERNESTOS FEEDBACK</b>\n{_who(user)}\n"
                      f"Date: {datetime.now(svc.user_tz(user)):%Y-%m-%d %H:%M}\n\n"
                      f"{esc(body.message)}"),
                parse_mode=ParseMode.HTML)
            delivered = True
        except TelegramError as e:
            log.warning("mini app feedback delivery failed: %s", e)

    if delivered:
        with SessionLocal() as s:
            svc.mark_feedback_delivered(s, feedback_id)
    return {"ok": True, "delivered": delivered}


@app.get("/api/export")
def api_export(init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Everything the user has written, as JSON.

    Their data, on request, in full. Nothing is summarised away.
    """
    user, ws = auth(init)
    with SessionLocal() as s:
        return svc.export_workspace(s, ws, s.get(User, user.telegram_id))


@app.post("/api/export/send")
async def api_export_send(init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Deliver the export as a file in the bot chat.

    The same route the CSV takes, and for the same reason: Telegram's in-app
    browser blocks ordinary downloads, and a download URL carrying the
    credential would leak it.
    """
    user, ws = auth(init)
    if telegram_app is None:
        raise HTTPException(status_code=503, detail="bot_unavailable")
    with SessionLocal() as s:
        payload = svc.export_workspace(s, ws, s.get(User, user.telegram_id))

    stamp = datetime.now(svc.user_tz(user)).strftime("%Y-%m-%d")
    document = InputFile(
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        filename=f"ernestos-data-{stamp}.json")
    try:
        await telegram_app.bot.send_document(
            chat_id=user.telegram_id, document=document, caption="ErnestOS")
    except TelegramError as e:
        log.warning("export to %s failed: %s", user.telegram_id, e)
        raise HTTPException(status_code=502, detail="delivery_failed")
    return {"ok": True, "delivered": "telegram"}


class DeleteAccountIn(BaseModel):
    """The typed confirmation. A destructive action needs an explicit word, not
    a second tap in the same place the first one was."""
    confirm: str = Field(max_length=20)


class WipeDataIn(BaseModel):
    """A second, explicit confirmation. One tap is not a confirmation."""
    confirm: str = Field(max_length=20)


@app.post("/api/account/wipe", summary="Erase everything in the workspace",
          tags=["Privacy"])
def api_account_wipe(body: WipeDataIn,
                     init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Empty the workspace without closing the account.

    Tasks, habits, logs, prayers, journal, projects and missions all go; the
    account, the language, the theme and the notification settings stay. The
    default habits are put back, because landing on an ErnestOS with no habits
    in it is landing on a broken one.
    """
    user, _ = auth(init)
    if body.confirm.strip().upper() != "WIPE":
        raise HTTPException(status_code=422, detail="confirmation_required")
    with SessionLocal() as s:
        svc.wipe_workspace(s, user.telegram_id)
    return {"ok": True}


@app.post("/api/account/delete")
def api_account_delete(body: DeleteAccountIn,
                       init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Erase this account and everything in it. Irreversible, and it says so."""
    user, _ = auth(init)
    if body.confirm.strip().upper() != "DELETE":
        raise HTTPException(status_code=422, detail="confirmation_required")
    with SessionLocal() as s:
        svc.delete_account(s, user.telegram_id)
    log.info("account deleted on request: %s", user.telegram_id)
    return {"ok": True, "deleted": True}


class WakeTimeIn(BaseModel):
    time: str = Field(max_length=5)


@app.post("/api/waketime")
def api_wake_time(body: WakeTimeIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    try:
        hour, minute = (int(x) for x in body.time.split(":"))
        value = dtime(hour, minute)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="bad_time")
    with SessionLocal() as s:
        svc.set_wake_time(s, ws, value)
    return {"ok": True, "time": value.strftime("%H:%M")}


# --- Mini App static file ---

WEBAPP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "webapp", "index.html")


@app.get("/")
def index():
    return FileResponse(WEBAPP_FILE)
