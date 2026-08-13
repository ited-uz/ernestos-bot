"""
ErnestOS — Telegram bot + Mini App API in one process.

Both surfaces call services.py, so the bot and the Mini App can never drift
apart: creating a task from a Telegram button and creating one from the web UI
run the exact same function.

Run with:  uvicorn app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, time as dtime, timedelta
from urllib.parse import parse_qsl

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, InputFile,
    ReplyKeyboardMarkup, Update, WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, ChatMemberHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)

import db
import dependencies as deps
import services as svc
from db import SessionLocal, User

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("ernestos")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
#: Access policy — the channel, the free run and the membership cache — lives
#: in `dependencies` and is read from there at every call site. Deliberately
#: not copied into a module-level name here: two copies of one setting is how
#: the bot and the API end up disagreeing about who is let in, and a test that
#: patches the copy silently changes nothing.
ADMIN_LOG_CHANNEL_ID = os.environ.get("ADMIN_LOG_CHANNEL_ID", "").strip()
#: Suggestions and complaints get their own channel, apart from event logs.
FEEDBACK_CHANNEL_ID = os.environ.get("FEEDBACK_CHANNEL_ID", "").strip() or ADMIN_LOG_CHANNEL_ID
#: Aggregate platform statistics — counts only, never user content.
STATS_CHANNEL_ID = os.environ.get("STATS_CHANNEL_ID", "").strip() or ADMIN_LOG_CHANNEL_ID
#: The hour, on the project clock, that the daily statistics post goes out.
STATS_POST_HOUR = int(os.environ.get("STATS_POST_HOUR", "10"))
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip().rstrip("/")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development").lower()

#: Set to run the bot on a webhook instead of long-polling. Unset — the
#: default — keeps polling, which needs no public URL and is the right choice
#: for one instance. Behind a load balancer polling is not an option at all:
#: several instances cannot each long-poll the same bot without stealing one
#: another's updates.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip().rstrip("/")
#: Telegram echoes this back in a header, which is the only thing separating a
#: real update from anybody who guessed the path.
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()

#: The update types this bot acts on. Anything else is refused at Telegram's
#: end rather than delivered and dropped here.
ALLOWED_UPDATES = ["message", "callback_query", "chat_member", "my_chat_member"]

#: Telegram initData older than this is rejected, so a captured URL cannot be
#: replayed days later.
INIT_DATA_MAX_AGE = int(os.environ.get("INIT_DATA_MAX_AGE", "86400"))

#: How often the report jobs wake up. Report times are per user, so the job
#: cannot be a single cron entry at 04:00 any more; it ticks and asks each user
#: whether their chosen time has arrived.
#:
#: Two minutes, because the tick interval *is* the worst-case lateness: at ten
#: a report set for 21:30 could arrive at 21:40, which reads as the bot being
#: slow. The cost of the extra ticks is one cheap query per user per tick, and
#: sending exactly once a day is still guaranteed by the outbox claim rather
#: than by the schedule.
REPORT_TICK_MINUTES = int(os.environ.get("REPORT_TICK_MINUTES", "2"))

if ENVIRONMENT == "production" and not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required in production")

# ---------------------------------------------------------------------------
# Translations — every user-visible string lives here, never inline
# ---------------------------------------------------------------------------

T: dict[str, dict[str, str]] = {
    "uz": {
        "pick_lang_multi": ("ErnestOS\n\n🇺🇿 Tilni tanlang\n"
                            "🇬🇧 Choose your language\n🇷🇺 Выберите язык"),
        "hello_named": "Assalomu alaykum, {name}!",
        #: The one screen a new account is given before it is left alone with
        #: an empty app. Written as four concrete things they will actually do
        #: today, with a real example under each, because "track your habits"
        #: describes a category and "Ertalab 6:00 da turish" describes a
        #: Tuesday. HTML, so it can be sent with the rest of the welcome.
        "guide": (
            "<b>ErnestOS nima qiladi?</b>\n"
            "Kuningizni bir joyda ushlab turadi va ayni damda nima qilish "
            "kerakligini aytadi. Boshqa hech narsa.\n\n"

            "🎯 <b>Hozir</b> — bosh sahifadagi eng katta karta.\n"
            "Sizda 12 ta ish bo'lsa ham, u bittasini ko'rsatadi: "
            "<i>«Diplom ishining 2-bobini yozish»</i>. Tugatdingiz — keyingisi "
            "chiqadi.\n\n"

            "✅ <b>Vazifalar</b> — muddat, vaqt va eslatma bilan.\n"
            "<i>«Soliq to'lovi — 25-avgust, 14:00»</i> qo'ysangiz, "
            "13:30 da eslatma keladi. Kalendarda ham ko'rinadi.\n\n"

            "🔁 <b>Odatlar</b> — har kuni takrorlanadigan ishlar.\n"
            "<i>«Ertalab 6:00 da turish»</i>, <i>«30 daqiqa kitob»</i>. "
            "Ketma-ket necha kun bajarganingiz 🔥 bilan ko'rsatiladi.\n\n"

            "📊 <b>Statistika</b> — kun, hafta va oy bitta ekranda.\n"
            "Foizingiz kecha 64% edi, bugun 71% — o'sish ham, tushish ham "
            "ko'rinib turadi.\n\n"

            "<b>Bugun nima qilish kerak</b>\n"
            "1️⃣ Bitta vazifa qo'shing — hozir bajaradigan ishingizni.\n"
            "2️⃣ Bitta odat qo'shing — har kuni takrorlanadiganini.\n"
            "3️⃣ Kechqurun 📔 Kundalikni to'ldiring.\n\n"

            "Hammasi shu. Ertaga ochganingizda ErnestOS sizga nimadan "
            "boshlashni aytib turadi."
        ),
        "phone_not_needed": ("Rahmat, lekin telefon raqam kerak emas — "
                             "ErnestOS uni so'ramaydi va saqlamaydi."),
        "welcome_in": "Xush kelibsiz, {name}! ErnestOS ishga tushdi.",
        "remind_task": "⏰ {title}",
        "remind_task_at": "⏰ {title} — {time}",
        "remind_habit": "⏰ {name}",
        "report_off": "Hisobotlar o'chirilgan. Sozlamalardan yoqishingiz mumkin.",
        "sub_unknown": "Hozir obunani tekshirib bo'lmadi. Bir ozdan keyin qayta urinib ko'ring.",
        "menu_wake": "☀️ Turdim",
        "wake_ok": "Xayrli tong! Turdingiz ✓ ({now})",
        "wake_ok_at": "☀️ Xayrli tong! {now} da turdingiz.",
        "wake_late_soft": "😴 Afsuski, kech qoldingiz — {now}. Target {target} edi. Ertaga o'zib ketamiz!",
        "wake_late": "Kech bo'ldi — {deadline} gacha yozish kerak edi. Bugun hisoblanmadi.",
        "wake_time_btn": "⏰ Uyg'onish vaqti",
        "ask_wake_time": "Soatni yozing (masalan 05:00):",
        "wake_time_set": "Uyg'onish vaqti: {time}. Bir soat ichida «Turdim» deb yozing.",
        "bad_time": "Format noto'g'ri. Masalan: 05:30",
        "btn_done_task": "✅ Bajarildi",
        "btn_edit_task": "✏️ Tahrirlash",
        "choose_done": "Qaysi vazifa bajarildi?",
        "choose_edit": "Qaysi birini tahrirlaysiz?",
        "ask_new_title": "Yangi nomni yozing:",
        "task_updated": "Yangilandi: {title}",
        "cat_non_negotiable": "🔴 Non-negotiable",
        "cat_target": "🟡 Target",
        "cat_bonus": "🟢 Bonus",
        "ask_habit_cat": "Qaysi kategoriyaga?",
        "btn_photo": "🖼 Profil rasmi",
        "ask_photo": "Profil rasmini foto sifatida yuboring:",
        "photo_saved": "Rasm saqlandi ✓",
        "photo_removed": "Rasm o'chirildi",
        "btn_photo_del": "🗑 Rasmni o'chirish",
        "streak": "🔥 Ketma-ket",
        "welcome": "Assalomu alaykum, {name}!\n\nErnestOS — Telegram ichidagi shaxsiy tizimingiz.",
        "btn_skip": "⏭ O'tkazib yuborish",
        "phone_skipped": "Yaxshi, keyinroq qo'shasiz.",
        "ask_lang": "Tilni tanlang:",
        "ask_gender": "Jinsingiz:",
        "male": "👨 Erkak", "female": "👩 Ayol",
        "sub_required": "ErnestOS'dan foydalanish uchun kanalimizga obuna bo'ling.",
        "btn_join": "📢 Kanalga qo'shilish",
        "btn_check": "✅ Tekshirish",
        # --- the free run, and the ask at the end of it ---
        "trial_soon": ("Yana <b>{n}</b> ta amaldan keyin kanalga qo'shilish "
                       "so'raladi. Bir marta — va hammasi ochiq qoladi."),
        "trial_over": ("<b>20 ta amalni bajardingiz.</b>\n\n"
                       "Endi ErnestOS'ni ochiq saqlash uchun kanalimizga "
                       "qo'shiling — bir marta bosasiz, xolos. "
                       "Barcha ma'lumotlaringiz joyida turibdi."),
        # --- the 60-second setup ---
        "intro": (
            "<b>ErnestOS — kuningizni boshqaradigan tizim.</b>\n\n"
            "Ertalab turasiz — nima qilishni bilmaysiz. Kechqurun yotasiz — "
            "nima qilganingizni eslay olmaysiz. Shu ikkisi orasidagi bo'shliqni "
            "ErnestOS to'ldiradi.\n\n"
            "🎯 U sizga <b>bitta</b> ish aytadi. O'ntasini emas — bittasini.\n"
            "📊 Har kuni nechchi foiz yashaganingizni ko'rsatadi.\n"
            "🔥 Va ketma-ketligingizni uzmaydi.\n\n"
            "<i>60 soniya — va bugungi kuningiz tayyor bo'ladi.</i>"),
        "intro_go": "🚀 Boshlash",
        "ask_name": "Sizni qanday chaqiray?",
        "name_set": "Xush kelibsiz, {name}.",
        "skip_step": "⏭ Keyinroq",
        "habits_keep": "✅ Yetarli",
        "ask_goal": (
            "<b>Shu haftaning asosiy maqsadi nima?</b>\n"
            "Bitta. Eng muhimi.\n\n"
            "<i>Masalan: «Diplomni yozib tugatish» · «Sport zalga 5 marta "
            "borish» · «Mijozga taqdimotni topshirish»</i>"),
        "goal_set": "🎯 Haftaning maqsadi: <b>{goal}</b>",
        "ask_tasks": (
            "<b>Bugun qaysi 3 ta ishni qilasiz?</b>\n"
            "Har birini yangi qatordan yozing.\n\n"
            "<i>Masalan:\n"
            "Buxgalterga qo'ng'iroq qilish\n"
            "2-bobni o'qib chiqish\n"
            "Kechki mashg'ulot</i>"),
        "tasks_set": "⚡ {n} ta vazifa bugunga qo'shildi",
        "ask_habits": (
            "<b>Har kuni takrorlanadigan 1–3 ta odat?</b>\n"
            "Har birini yangi qatordan yozing.\n\n"
            "<i>Masalan:\n"
            "Ertalab 30 daqiqa kitob\n"
            "10 000 qadam\n"
            "Suv — 2 litr</i>\n\n"
            "Uyg'onish, namoz va kundalik allaqachon qo'shilgan."),
        "habits_set": "✅ {n} ta odat qo'shildi",
        "day_ready": "🌅 {name}, bugungi kuningiz tayyor",
        "day_ready_plain": "🌅 Bugungi kuningiz tayyor",
        "day_ready_first": "Birinchi ish:",
        "day_ready_open": "Kunni ErnestOS'da boshlang.",
        "day_ready_app": "Hammasi shu yerda 👇",
        "sub_missing": "Hali obuna bo'lmadingiz. Kanalga qo'shilib, qayta tekshiring.",
        "sub_lost": "Siz ErnestOS kanalidan chiqdingiz. Davom etish uchun kanalga qayta qo'shiling.",
        "sub_restored": "Xush kelibsiz! ErnestOS yana ochiq.",
        "onboard_done": "Tayyor! ErnestOS ishga tushdi.",
        "lang_changed": "Til o'zgartirildi ✓",
        "theme_changed": "Tema o'zgartirildi — {name}",
        "gender_changed": "Jins saqlandi — {value}",
        "menu_home": "🏠 Home", "menu_habits": "✅ Odatlar",
        "menu_tasks": "⚡ Vazifalar", "menu_stats": "📊 Statistika",
        "menu_settings": "⚙️ Sozlamalar", "menu_feedback": "💬 Taklif",
        "menu_app": "🚀 ErnestOS",
        "home_title": "🏠 {name}ning shaxsiy tizimi",
        "home_title_plain": "🏠 ErnestOS",
        "home_mission": "🎯 Missiya", "home_today": "⚡ Bugun",
        "home_now": "⚡ HOZIR",
        "home_top3": "🎯 Kun tanlovi",
        "now_wake": "Turdim — belgilang",
        "now_prayer": "Namozni kiriting",
        "now_journal": "Kun yakuni",
        "now_clear": "Bugungi muhim ishlar tugadi",
        "privacy_line": ("🔒 Ma'lumotlaringiz boshqa foydalanuvchilardan "
                         "ajratilgan va xavfsiz saqlanadi."),
        "stats_title": "📊 Statistika",
        "st_today": "📅 Bugun",
        "st_week": "📆 Oxirgi 7 kun",
        "st_month": "🗓 Oxirgi 30 kun",
        "tasks_undated": "📥 Muddatsiz",
        "st_overall": "Umumiy", "st_tasks": "Vazifalar", "st_habits": "Odatlar",
        "st_prayer": "Namoz", "st_streak": "Ketma-ket",
        "home_habits": "✅ Odatlar", "home_tasks": "⚡ Bugungi vazifalar",
        "home_focus": "🎯 Shu hafta", "home_projects": "📁 Loyihalar",
        "home_bday": "🎂 Tug'ilgan kunlar",
        "home_overdue": "❗ Kechikkan",
        "none": "— yo'q",
        "habits_title": "✅ Odatlar",
        "btn_add_habit": "➕ Odat qo'shish", "btn_del_habit": "🗑 Odat o'chirish",
        "ask_habit_name": "Odat nomini yozing:",
        "habit_added": "Odat qo'shildi: {name}",
        "habit_deleted": "O'chirildi: {name}",
        "habit_protected": "5x namoz avtomatik hisoblanadi — Namoz bo'limidan belgilanadi.",
        "choose_delete": "Qaysi birini o'chirasiz?",
        "tasks_title": "⚡ Yaqin 7 kun",
        "tasks_overdue": "❗ Kechikkan",
        "btn_add_task": "➕ Vazifa", "btn_del_task": "🗑 Vazifa o'chirish",
        "btn_add_project": "➕ Loyiha", "btn_del_project": "🗑 Loyiha o'chirish",
        "ask_task_name": "Vazifa nomini yozing:",
        "ask_task_days": "Qachongacha bajarilishi kerak?",
        "days_today": "Bugun", "days_1": "1 kun", "days_2": "2 kun",
        "days_3": "3 kun", "days_7": "7 kun", "days_custom": "Boshqa",
        "ask_custom_days": "Necha kun? Raqam yozing (masalan 14):",
        "ask_task_project": "Qaysi loyihaga tegishli?",
        "standalone": "📌 Alohida",
        "task_added": "Vazifa qo'shildi: {title}",
        "task_deleted": "O'chirildi: {title}",
        "task_done": "Bajarildi ✓ {title}",
        "ask_project_name": "Loyiha nomini yozing:",
        "project_added": "Loyiha qo'shildi: {name}",
        "project_deleted": "Loyiha arxivlandi: {name}",
        "btn_rename_project": "✏️ Nomini o'zgartirish",
        "ask_project_rename": "Loyihaning yangi nomini yozing:",
        "project_updated": "Loyiha yangilandi: {name}",
        "project_tasks": "Vazifalar",
        "settings_title": "⚙️ Sozlamalar",
        "btn_lang": "🌐 Til", "btn_gender": "👤 Jins", "btn_theme": "🎨 Tema",
        "saved": "Saqlandi ✓",
        "ask_feedback": "Taklif yoki shikoyatingizni yozing:",
        "feedback_sent": "Rahmat! Taklifingiz qabul qilindi.",
        "feedback_saved": "Taklifingiz saqlandi, tez orada yetkaziladi.",
        "cancel": "❌ Bekor qilish",
        "cancelled": "Bekor qilindi.",
        "back": "⬅️ Orqaga",
        "error": "Xatolik yuz berdi. Qayta urinib ko'ring.",
        "not_found": "Topilmadi.",
        "empty": "Hozircha bo'sh.",
        "open_app": "Mini App'da to'liq ko'rish:",
        "morning_title": "🌅 Bugun — {date}",
        "yesterday_title": "🌙 Kechagi kun — {date}",
        "evening_title": "🌙 Kun yakuni",
        "r_habits": "✅ Odatlar", "r_prayer": "🕌 Namoz", "r_tasks": "⚡ Vazifalar",
        "r_completed": "bajarildi", "r_remaining": "qoldi",
        "r_journal": "📓 Kundalik", "r_yes": "yozilgan", "r_no": "yozilmagan",
        "r_unfinished": "❗ Tugallanmagan:",
        "r_yesterday": "Kecha",
        "r_good_morning": "🌅 Xayrli tong, {name}!",
        "r_good_morning_plain": "🌅 Xayrli tong!",
        "r_good_night": "🌙 Xayrli tun! Yaxshi dam oling.",
        "r_today_all": "📌 BUGUN QILINADI",
        "r_habits_today": "Odatlar",
        "r_prayer_today": "Namoz — 5 mahal",
        "r_nothing_planned": "Bugunga hech narsa qo'shilmagan. Bitta ish yozib qo'ying — kun shundan boshlanadi.",
        "r_mission": "Haftaning asosiy maqsadi",
        "r_today_plan": "Vazifalar",
        "r_overdue_hint": "Ularni bugunga ko'chirish 10 sekund oladi — Vazifalar bo'limida.",
        "r_start_now": "Eng kichigidan boshlang. Birinchi ishni tanlang va 25 daqiqa bering.",
        "coach_high": "Kecha {pct}% — bu tasodif emas, tizim ishlayapti. Bugun ham shu darajani ushlab turing.",
        "coach_mid": "Kecha {pct}%. Yarmi bajarildi, ya'ni qiyin qismi allaqachon ortda. Bugun bittasini ko'proq yopsangiz — yetadi.",
        "coach_low": "Kecha {pct}%. Bu kun haqida, siz haqingizda emas. Bugun faqat bitta ishni tanlang va uni tugatib qo'ying — qolgani o'zi ketadi.",
        "coach_blank": "Kecha hech narsa belgilanmagan. Muhim emas — bugun bitta ish va bitta odat yetarli. Boshlash uchun ko'p narsa kerak emas.",
        "r_evening_close": "Ertaga bulardan bittasini birinchi qilib yoping.",
        "r_evening_clear": "✅ Hammasi yopildi. Shu holatni ushlab turing — dam olishga haqlisiz.",
        "r_focus": "🎯 Haftalik maqsadlar",
        "days_short": "kun",
    },
    "en": {
        "pick_lang_multi": ("ErnestOS\n\n🇺🇿 Tilni tanlang\n"
                            "🇬🇧 Choose your language\n🇷🇺 Выберите язык"),
        "hello_named": "Hello, {name}!",
        "guide": (
            "<b>What ErnestOS does</b>\n"
            "It holds your day in one place and tells you what to do right "
            "now. Nothing else.\n\n"

            "🎯 <b>Now</b> — the largest card on the home screen.\n"
            "Even with 12 things open, it shows one: "
            "<i>&quot;Write chapter 2 of the thesis&quot;</i>. Finish it and "
            "the next one appears.\n\n"

            "✅ <b>Tasks</b> — with a date, a time and a reminder.\n"
            "Add <i>&quot;Pay the tax bill — 25 August, 14:00&quot;</i> and a "
            "reminder arrives at 13:30. It shows up on the calendar too.\n\n"

            "🔁 <b>Habits</b> — the things you repeat every day.\n"
            "<i>&quot;Wake up at 6:00&quot;</i>, <i>&quot;30 minutes of "
            "reading&quot;</i>. Your run of days shows as 🔥.\n\n"

            "📊 <b>Statistics</b> — day, week and month on one screen.\n"
            "You were on 64% yesterday and 71% today; both the rise and the "
            "fall are visible.\n\n"

            "<b>What to do today</b>\n"
            "1️⃣ Add one task — whatever you are doing next.\n"
            "2️⃣ Add one habit — something you repeat daily.\n"
            "3️⃣ Fill in the 📔 journal this evening.\n\n"

            "That is all of it. Tomorrow, ErnestOS tells you where to start."
        ),
        "phone_not_needed": ("Thanks, but no phone number is needed — "
                             "ErnestOS does not ask for one or store one."),
        "welcome_in": "Welcome, {name}! ErnestOS is running.",
        "remind_task": "⏰ {title}",
        "remind_task_at": "⏰ {title} — {time}",
        "remind_habit": "⏰ {name}",
        "report_off": "Reports are off. You can turn them on in Settings.",
        "sub_unknown": "Could not verify your subscription right now. Please try again shortly.",
        "menu_wake": "☀️ I'm up",
        "wake_ok": "Good morning! You are up ✓ ({now})",
        "wake_ok_at": "☀️ Good morning! You were up at {now}.",
        "wake_late_soft": "😴 Afraid you were late — {now}. The target was {target}. Tomorrow we beat it!",
        "wake_late": "Too late — the cut-off was {deadline}. It does not count today.",
        "wake_time_btn": "⏰ Wake-up time",
        "ask_wake_time": "Enter the time (e.g. 05:00):",
        "wake_time_set": "Wake-up time: {time}. Say «I'm up» within an hour of it.",
        "bad_time": "Wrong format. Example: 05:30",
        "btn_done_task": "✅ Mark done",
        "btn_edit_task": "✏️ Edit",
        "choose_done": "Which task is done?",
        "choose_edit": "Which one do you want to edit?",
        "ask_new_title": "Enter the new title:",
        "task_updated": "Updated: {title}",
        "cat_non_negotiable": "🔴 Non-negotiable",
        "cat_target": "🟡 Target",
        "cat_bonus": "🟢 Bonus",
        "ask_habit_cat": "Which category?",
        "btn_photo": "🖼 Profile photo",
        "ask_photo": "Send your profile photo:",
        "photo_saved": "Photo saved ✓",
        "photo_removed": "Photo removed",
        "btn_photo_del": "🗑 Remove photo",
        "streak": "🔥 Streak",
        "welcome": "Hello, {name}!\n\nErnestOS — your personal system inside Telegram.",
        "btn_skip": "⏭ Skip",
        "phone_skipped": "No problem, you can add it later.",
        "ask_lang": "Choose your language:",
        "ask_gender": "Your gender:",
        "male": "👨 Male", "female": "👩 Female",
        "sub_required": "Join our channel to use ErnestOS.",
        "btn_join": "📢 Join channel",
        "btn_check": "✅ Check",
        # --- the free run, and the ask at the end of it ---
        "trial_soon": ("<b>{n}</b> more actions and you will be asked to join "
                       "the channel. One tap, once, and everything stays open."),
        "trial_over": ("<b>You have done 20 things here.</b>\n\n"
                       "Join our channel to keep ErnestOS open — one tap, once. "
                       "Everything you entered is exactly where you left it."),
        # --- the 60-second setup ---
        "intro": (
            "<b>ErnestOS runs your day.</b>\n\n"
            "You wake up not knowing what to do. You go to bed unable to "
            "remember what you did. ErnestOS fills the gap between those two.\n\n"
            "🎯 It tells you <b>one</b> thing to do. Not ten — one.\n"
            "📊 It shows what percentage of the day you actually lived.\n"
            "🔥 And it does not let your streak break.\n\n"
            "<i>Sixty seconds — and your day is ready.</i>"),
        "intro_go": "🚀 Start",
        "ask_name": "What should I call you?",
        "name_set": "Welcome, {name}.",
        "skip_step": "⏭ Later",
        "habits_keep": "✅ That's enough",
        "ask_goal": (
            "<b>What is this week's main goal?</b>\n"
            "One. The one that matters.\n\n"
            "<i>For example: &quot;Finish writing the thesis&quot; · &quot;Gym "
            "five times&quot; · &quot;Ship the client presentation&quot;</i>"),
        "goal_set": "🎯 This week: <b>{goal}</b>",
        "ask_tasks": (
            "<b>Which three things are you doing today?</b>\n"
            "One per line.\n\n"
            "<i>For example:\n"
            "Call the accountant\n"
            "Read chapter two\n"
            "Evening workout</i>"),
        "tasks_set": "⚡ {n} tasks added to today",
        "ask_habits": (
            "<b>One to three habits you repeat daily?</b>\n"
            "One per line.\n\n"
            "<i>For example:\n"
            "30 minutes of reading\n"
            "10,000 steps\n"
            "Two litres of water</i>\n\n"
            "Waking up, prayer and the journal are already in."),
        "habits_set": "✅ {n} habits added",
        "day_ready": "🌅 {name}, your day is ready",
        "day_ready_plain": "🌅 Your day is ready",
        "day_ready_first": "First up:",
        "day_ready_open": "Start the day in ErnestOS.",
        "day_ready_app": "It is all in here 👇",
        "sub_missing": "You are not subscribed yet. Join the channel and check again.",
        "sub_lost": "You left the required ErnestOS channel. Join the channel to continue using ErnestOS.",
        "sub_restored": "Welcome back! ErnestOS is open again.",
        "onboard_done": "All set! ErnestOS is ready.",
        "lang_changed": "Language changed ✓",
        "theme_changed": "Theme changed to {name}",
        "gender_changed": "Gender saved as {value}",
        "menu_home": "🏠 Home", "menu_habits": "✅ Habits",
        "menu_tasks": "⚡ Tasks", "menu_stats": "📊 Statistics",
        "menu_settings": "⚙️ Settings", "menu_feedback": "💬 Feedback",
        "menu_app": "🚀 ErnestOS",
        "home_title": "🏠 {name}'s personal system",
        "home_title_plain": "🏠 ErnestOS",
        "home_mission": "🎯 Mission", "home_today": "⚡ Today",
        "home_now": "⚡ NOW",
        "home_top3": "🎯 The day's pick",
        "now_wake": "Mark that you are up",
        "now_prayer": "Log your prayers",
        "now_journal": "Close the day",
        "now_clear": "Today's important work is done",
        "privacy_line": ("🔒 Your data is kept separate from other users' "
                         "and stored securely."),
        "stats_title": "📊 Statistics",
        "st_today": "📅 Today",
        "st_week": "📆 Last 7 days",
        "st_month": "🗓 Last 30 days",
        "tasks_undated": "📥 No deadline",
        "st_overall": "Overall", "st_tasks": "Tasks", "st_habits": "Habits",
        "st_prayer": "Prayer", "st_streak": "Streak",
        "home_habits": "✅ Habits", "home_tasks": "⚡ Today's tasks",
        "home_focus": "🎯 This week", "home_projects": "📁 Projects",
        "home_bday": "🎂 Birthdays",
        "home_overdue": "❗ Overdue",
        "none": "— none",
        "habits_title": "✅ Habits",
        "btn_add_habit": "➕ Add habit", "btn_del_habit": "🗑 Delete habit",
        "ask_habit_name": "Enter habit name:",
        "habit_added": "Habit added: {name}",
        "habit_deleted": "Deleted: {name}",
        "habit_protected": "5x namoz is calculated automatically from your prayers.",
        "choose_delete": "Which one do you want to delete?",
        "tasks_title": "⚡ Next 7 days",
        "tasks_overdue": "❗ Overdue",
        "btn_add_task": "➕ Task", "btn_del_task": "🗑 Delete task",
        "btn_add_project": "➕ Project", "btn_del_project": "🗑 Delete project",
        "ask_task_name": "Enter task name:",
        "ask_task_days": "When is it due?",
        "days_today": "Today", "days_1": "1 day", "days_2": "2 days",
        "days_3": "3 days", "days_7": "7 days", "days_custom": "Custom",
        "ask_custom_days": "How many days? Enter a number (e.g. 14):",
        "ask_task_project": "Which project does it belong to?",
        "standalone": "📌 Standalone",
        "task_added": "Task added: {title}",
        "task_deleted": "Deleted: {title}",
        "task_done": "Done ✓ {title}",
        "ask_project_name": "Enter project name:",
        "project_added": "Project added: {name}",
        "project_deleted": "Project archived: {name}",
        "btn_rename_project": "✏️ Rename",
        "ask_project_rename": "Enter the new project name:",
        "project_updated": "Project updated: {name}",
        "project_tasks": "Tasks",
        "settings_title": "⚙️ Settings",
        "btn_lang": "🌐 Language", "btn_gender": "👤 Gender", "btn_theme": "🎨 Theme",
        "saved": "Saved ✓",
        "ask_feedback": "Write your suggestion or complaint:",
        "feedback_sent": "Thank you! Your feedback was received.",
        "feedback_saved": "Your feedback is saved and will be delivered shortly.",
        "cancel": "❌ Cancel",
        "cancelled": "Cancelled.",
        "back": "⬅️ Back",
        "error": "Something went wrong. Please try again.",
        "not_found": "Not found.",
        "empty": "Nothing yet.",
        "open_app": "See everything in the Mini App:",
        "morning_title": "🌅 Today — {date}",
        "yesterday_title": "🌙 Yesterday — {date}",
        "evening_title": "🌙 End of day",
        "r_habits": "✅ Habits", "r_prayer": "🕌 Prayer", "r_tasks": "⚡ Tasks",
        "r_completed": "completed", "r_remaining": "remaining",
        "r_journal": "📓 Journal", "r_yes": "written", "r_no": "not written",
        "r_unfinished": "❗ Still unfinished:",
        "r_yesterday": "Yesterday",
        "r_good_morning": "🌅 Good morning, {name}!",
        "r_good_morning_plain": "🌅 Good morning!",
        "r_good_night": "🌙 Good night — rest well.",
        "r_today_all": "📌 TODAY'S PLAN",
        "r_habits_today": "Habits",
        "r_prayer_today": "Prayer — five times",
        "r_nothing_planned": "Nothing is on today yet. Write one thing down — that is where the day starts.",
        "r_mission": "This week's mission",
        "r_today_plan": "Tasks",
        "r_overdue_hint": "Moving them to today takes ten seconds — see Tasks.",
        "r_start_now": "Start with the smallest one. Pick the first task and give it 25 minutes.",
        "coach_high": "Yesterday {pct}% — that is not luck, that is the system working. Hold the same line today.",
        "coach_mid": "Yesterday {pct}%. Half of it is done, which means the hard part is already behind you. One more today is enough.",
        "coach_low": "Yesterday {pct}%. That is a fact about the day, not about you. Today pick one task and finish it — the rest follows.",
        "coach_blank": "Nothing was logged yesterday. That is fine — one task and one habit today is enough. Starting does not require much.",
        "r_evening_close": "Tomorrow, close one of these first.",
        "r_evening_clear": "✅ Everything is closed. Hold this — you have earned the rest.",
        "r_focus": "🎯 Weekly missions",
        "days_short": "days",
    },
    "ru": {
        "pick_lang_multi": ("ErnestOS\n\n🇺🇿 Tilni tanlang\n"
                            "🇬🇧 Choose your language\n🇷🇺 Выберите язык"),
        "hello_named": "Здравствуйте, {name}!",
        "guide": (
            "<b>Что делает ErnestOS</b>\n"
            "Держит ваш день в одном месте и говорит, что делать прямо "
            "сейчас. Больше ничего.\n\n"

            "🎯 <b>Сейчас</b> — самая большая карточка на главной.\n"
            "Даже если открыто 12 дел, она показывает одно: "
            "<i>«Написать вторую главу диплома»</i>. Закончили — появится "
            "следующее.\n\n"

            "✅ <b>Задачи</b> — с датой, временем и напоминанием.\n"
            "Добавьте <i>«Оплатить налог — 25 августа, 14:00»</i>, и в 13:30 "
            "придёт напоминание. Задача видна и в календаре.\n\n"

            "🔁 <b>Привычки</b> — то, что повторяется каждый день.\n"
            "<i>«Вставать в 6:00»</i>, <i>«30 минут чтения»</i>. Серия дней "
            "подряд показывается значком 🔥.\n\n"

            "📊 <b>Статистика</b> — день, неделя и месяц на одном экране.\n"
            "Вчера было 64%, сегодня 71% — видно и рост, и падение.\n\n"

            "<b>Что сделать сегодня</b>\n"
            "1️⃣ Добавьте одну задачу — то, чем займётесь сейчас.\n"
            "2️⃣ Добавьте одну привычку — то, что повторяете каждый день.\n"
            "3️⃣ Вечером заполните 📔 дневник.\n\n"

            "Это всё. Завтра ErnestOS сам подскажет, с чего начать."
        ),
        "phone_not_needed": ("Спасибо, но номер телефона не нужен — "
                             "ErnestOS его не запрашивает и не хранит."),
        "welcome_in": "Добро пожаловать, {name}! ErnestOS запущен.",
        "remind_task": "⏰ {title}",
        "remind_task_at": "⏰ {title} — {time}",
        "remind_habit": "⏰ {name}",
        "report_off": "Отчёты отключены. Их можно включить в настройках.",
        "sub_unknown": "Сейчас не удалось проверить подписку. Попробуйте чуть позже.",
        "menu_wake": "☀️ Я встал",
        "wake_ok": "Доброе утро! Вы встали ✓ ({now})",
        "wake_ok_at": "☀️ Доброе утро! Вы встали в {now}.",
        "wake_late_soft": "😴 К сожалению, вы проспали — {now}. Цель была {target}. Завтра обгоним!",
        "wake_late": "Поздно — крайний срок был {deadline}. Сегодня не засчитано.",
        "wake_time_btn": "⏰ Время подъёма",
        "ask_wake_time": "Введите время (например 05:00):",
        "wake_time_set": "Время подъёма: {time}. Напишите «Я встал» в течение часа.",
        "bad_time": "Неверный формат. Пример: 05:30",
        "btn_done_task": "✅ Выполнено",
        "btn_edit_task": "✏️ Изменить",
        "choose_done": "Какая задача выполнена?",
        "choose_edit": "Что изменить?",
        "ask_new_title": "Введите новое название:",
        "task_updated": "Обновлено: {title}",
        "cat_non_negotiable": "🔴 Non-negotiable",
        "cat_target": "🟡 Target",
        "cat_bonus": "🟢 Bonus",
        "ask_habit_cat": "Какая категория?",
        "btn_photo": "🖼 Фото профиля",
        "ask_photo": "Отправьте фото профиля:",
        "photo_saved": "Фото сохранено ✓",
        "photo_removed": "Фото удалено",
        "btn_photo_del": "🗑 Удалить фото",
        "streak": "🔥 Серия",
        "welcome": "Здравствуйте, {name}!\n\nErnestOS — ваша личная система внутри Telegram.",
        "btn_skip": "⏭ Пропустить",
        "phone_skipped": "Хорошо, добавите позже.",
        "ask_lang": "Выберите язык:",
        "ask_gender": "Ваш пол:",
        "male": "👨 Мужской", "female": "👩 Женский",
        "sub_required": "Подпишитесь на наш канал, чтобы пользоваться ErnestOS.",
        "btn_join": "📢 Подписаться",
        "btn_check": "✅ Проверить",
        # --- the free run, and the ask at the end of it ---
        "trial_soon": ("Ещё <b>{n}</b> действия — и попросим подписаться на "
                       "канал. Одно нажатие, один раз, и всё останется открытым."),
        "trial_over": ("<b>Вы сделали здесь 20 действий.</b>\n\n"
                       "Подпишитесь на канал, чтобы ErnestOS остался открытым — "
                       "одно нажатие, один раз. Все ваши данные на месте."),
        # --- the 60-second setup ---
        "intro": (
            "<b>ErnestOS ведёт ваш день.</b>\n\n"
            "Утром встаёте и не знаете, что делать. Вечером ложитесь и не "
            "можете вспомнить, что сделали. ErnestOS закрывает разрыв между "
            "этими двумя моментами.\n\n"
            "🎯 Он называет <b>одно</b> дело. Не десять — одно.\n"
            "📊 Показывает, на сколько процентов вы прожили день.\n"
            "🔥 И не даёт прервать серию.\n\n"
            "<i>Шестьдесят секунд — и день готов.</i>"),
        "intro_go": "🚀 Начать",
        "ask_name": "Как к вам обращаться?",
        "name_set": "Добро пожаловать, {name}.",
        "skip_step": "⏭ Позже",
        "habits_keep": "✅ Достаточно",
        "ask_goal": (
            "<b>Главная цель этой недели?</b>\n"
            "Одна. Самая важная.\n\n"
            "<i>Например: «Дописать диплом» · «Пять раз в зал» · "
            "«Сдать презентацию клиенту»</i>"),
        "goal_set": "🎯 Цель недели: <b>{goal}</b>",
        "ask_tasks": (
            "<b>Какие три дела вы сделаете сегодня?</b>\n"
            "Каждое с новой строки.\n\n"
            "<i>Например:\n"
            "Позвонить бухгалтеру\n"
            "Прочитать вторую главу\n"
            "Вечерняя тренировка</i>"),
        "tasks_set": "⚡ Добавлено задач на сегодня: {n}",
        "ask_habits": (
            "<b>1–3 привычки, которые повторяются каждый день?</b>\n"
            "Каждую с новой строки.\n\n"
            "<i>Например:\n"
            "30 минут чтения\n"
            "10 000 шагов\n"
            "Два литра воды</i>\n\n"
            "Подъём, намаз и дневник уже добавлены."),
        "habits_set": "✅ Добавлено привычек: {n}",
        "day_ready": "🌅 {name}, ваш день готов",
        "day_ready_plain": "🌅 Ваш день готов",
        "day_ready_first": "Первое дело:",
        "day_ready_open": "Начните день в ErnestOS.",
        "day_ready_app": "Всё здесь 👇",
        "sub_missing": "Вы ещё не подписаны. Подпишитесь и проверьте снова.",
        "sub_lost": "Вы вышли из канала ErnestOS. Подпишитесь снова, чтобы продолжить.",
        "sub_restored": "С возвращением! ErnestOS снова доступен.",
        "onboard_done": "Готово! ErnestOS запущен.",
        "lang_changed": "Язык изменён ✓",
        "theme_changed": "Тема изменена — {name}",
        "gender_changed": "Пол сохранён — {value}",
        "menu_home": "🏠 Главная", "menu_habits": "✅ Привычки",
        "menu_tasks": "⚡ Задачи", "menu_stats": "📊 Статистика",
        "menu_settings": "⚙️ Настройки", "menu_feedback": "💬 Отзыв",
        "menu_app": "🚀 ErnestOS",
        "home_title": "🏠 Личная система — {name}",
        "home_title_plain": "🏠 ErnestOS",
        "home_mission": "🎯 Миссия", "home_today": "⚡ Сегодня",
        "home_now": "⚡ СЕЙЧАС",
        "home_top3": "🎯 Выбор дня",
        "now_wake": "Отметьте подъём",
        "now_prayer": "Отметьте намазы",
        "now_journal": "Итоги дня",
        "now_clear": "Важные дела на сегодня закрыты",
        "privacy_line": ("🔒 Ваши данные отделены от данных других "
                         "пользователей и хранятся безопасно."),
        "stats_title": "📊 Статистика",
        "st_today": "📅 Сегодня",
        "st_week": "📆 Последние 7 дней",
        "st_month": "🗓 Последние 30 дней",
        "tasks_undated": "📥 Без срока",
        "st_overall": "Общий результат", "st_tasks": "Задачи",
        "st_habits": "Привычки", "st_prayer": "Намаз", "st_streak": "Серия",
        "home_habits": "✅ Привычки", "home_tasks": "⚡ Задачи на сегодня",
        "home_focus": "🎯 На этой неделе", "home_projects": "📁 Проекты",
        "home_bday": "🎂 Дни рождения",
        "home_overdue": "❗ Просрочено",
        "none": "— нет",
        "habits_title": "✅ Привычки",
        "btn_add_habit": "➕ Добавить", "btn_del_habit": "🗑 Удалить",
        "ask_habit_name": "Введите название привычки:",
        "habit_added": "Привычка добавлена: {name}",
        "habit_deleted": "Удалено: {name}",
        "habit_protected": "5x namoz считается автоматически по намазам.",
        "choose_delete": "Что удалить?",
        "tasks_title": "⚡ Ближайшие 7 дней",
        "tasks_overdue": "❗ Просрочено",
        "btn_add_task": "➕ Задача", "btn_del_task": "🗑 Удалить задачу",
        "btn_add_project": "➕ Проект", "btn_del_project": "🗑 Удалить проект",
        "ask_task_name": "Введите название задачи:",
        "ask_task_days": "Когда нужно выполнить?",
        "days_today": "Сегодня", "days_1": "1 день", "days_2": "2 дня",
        "days_3": "3 дня", "days_7": "7 дней", "days_custom": "Другое",
        "ask_custom_days": "Сколько дней? Введите число (например 14):",
        "ask_task_project": "К какому проекту относится?",
        "standalone": "📌 Отдельно",
        "task_added": "Задача добавлена: {title}",
        "task_deleted": "Удалено: {title}",
        "task_done": "Выполнено ✓ {title}",
        "ask_project_name": "Введите название проекта:",
        "project_added": "Проект добавлен: {name}",
        "project_deleted": "Проект архивирован: {name}",
        "btn_rename_project": "✏️ Переименовать",
        "ask_project_rename": "Введите новое название проекта:",
        "project_updated": "Проект обновлён: {name}",
        "project_tasks": "Задачи",
        "settings_title": "⚙️ Настройки",
        "btn_lang": "🌐 Язык", "btn_gender": "👤 Пол", "btn_theme": "🎨 Тема",
        "saved": "Сохранено ✓",
        "ask_feedback": "Напишите ваше предложение или жалобу:",
        "feedback_sent": "Спасибо! Ваш отзыв получен.",
        "feedback_saved": "Отзыв сохранён и скоро будет доставлен.",
        "cancel": "❌ Отмена",
        "cancelled": "Отменено.",
        "back": "⬅️ Назад",
        "error": "Произошла ошибка. Попробуйте снова.",
        "not_found": "Не найдено.",
        "empty": "Пока пусто.",
        "open_app": "Полностью — в Mini App:",
        "morning_title": "🌅 Сегодня — {date}",
        "yesterday_title": "🌙 Вчера — {date}",
        "evening_title": "🌙 Итоги дня",
        "r_habits": "✅ Привычки", "r_prayer": "🕌 Намаз", "r_tasks": "⚡ Задачи",
        "r_completed": "выполнено", "r_remaining": "осталось",
        "r_journal": "📓 Дневник", "r_yes": "заполнен", "r_no": "не заполнен",
        "r_unfinished": "❗ Не завершено:",
        "r_yesterday": "Вчера",
        "r_good_morning": "🌅 Доброе утро, {name}!",
        "r_good_morning_plain": "🌅 Доброе утро!",
        "r_good_night": "🌙 Спокойной ночи — хорошего отдыха.",
        "r_today_all": "📌 ПЛАН НА СЕГОДНЯ",
        "r_habits_today": "Привычки",
        "r_prayer_today": "Намаз — пять раз",
        "r_nothing_planned": "На сегодня пока ничего нет. Запишите одно дело — с этого и начинается день.",
        "r_mission": "Главная цель недели",
        "r_today_plan": "Задачи",
        "r_overdue_hint": "Перенести их на сегодня — десять секунд, в разделе Задачи.",
        "r_start_now": "Начните с самого маленького. Выберите первую задачу и дайте ей 25 минут.",
        "coach_high": "Вчера {pct}% — это не случайность, это работает система. Удержите тот же уровень сегодня.",
        "coach_mid": "Вчера {pct}%. Половина сделана, значит трудная часть уже позади. Сегодня достаточно закрыть на одну больше.",
        "coach_low": "Вчера {pct}%. Это факт о дне, а не о вас. Сегодня выберите одну задачу и доведите её до конца — остальное подтянется.",
        "coach_blank": "Вчера ничего не отмечено. Это нормально — сегодня хватит одной задачи и одной привычки. Чтобы начать, много не нужно.",
        "r_evening_close": "Завтра закройте одну из них первой.",
        "r_evening_clear": "✅ Всё закрыто. Удержите это — отдых заслужен.",
        "r_focus": "🎯 Цели недели",
        "days_short": "дн.",
    },
}

PRAYER_LABELS = {
    "uz": {"bomdod": "Bomdod", "peshin": "Peshin", "asr": "Asr",
           "shom": "Shom", "xufton": "Xufton",
           "jamaat": "Jamoat", "on_time": "O'z vaqtida", "qaza": "Qazo",
           "missed": "O'qilmagan", "excused": "Uzrli"},
    "en": {"bomdod": "Fajr", "peshin": "Dhuhr", "asr": "Asr",
           "shom": "Maghrib", "xufton": "Isha",
           "jamaat": "Jamaat", "on_time": "On time", "qaza": "Qaza",
           "missed": "Missed", "excused": "Excused"},
    "ru": {"bomdod": "Фаджр", "peshin": "Зухр", "asr": "Аср",
           "shom": "Магриб", "xufton": "Иша",
           "jamaat": "Джамаат", "on_time": "Вовремя", "qaza": "Каза",
           "missed": "Пропущен", "excused": "Уважит."},
}


def t(lang: str, key: str, **kwargs) -> str:
    value = T.get(lang, T["uz"]).get(key) or T["uz"].get(key, key)
    return value.format(**kwargs) if kwargs else value


# ---------------------------------------------------------------------------
# Admin log channel
# ---------------------------------------------------------------------------

def esc(value) -> str:
    """Escape user-controlled text before it enters an HTML message.

    Telegram parses `parse_mode=HTML`, so an unescaped name like
    `<a href=...>` either renders as a link or aborts the whole send with a
    parse error (audit 014, 076). Application markup is written literally in
    the f-string; every value that came from a user goes through here.
    """
    return html.escape(str(value or ""), quote=False)


async def admin_log(bot, text: str, chat_id: str | None = None) -> None:
    """Send a business event to a private admin channel.

    Never carries secrets or stack traces — technical failures go to the
    application log instead.
    """
    target = chat_id or ADMIN_LOG_CHANNEL_ID
    if not target:
        return
    try:
        await bot.send_message(chat_id=target, text=text,
                               parse_mode=ParseMode.HTML,
                               disable_web_page_preview=True)
    except TelegramError as e:
        log.warning("admin log failed: %s", e)


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


async def guard(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> tuple[User, int] | None:
    """Every protected action starts here.

    Returns (user, workspace_id) when the caller may proceed, otherwise sends
    the appropriate prompt and returns None. The access decision itself is
    `dependencies.check_subscription`, which the API calls too — the two
    surfaces must never disagree about who is allowed in.
    """
    tg_user = update.effective_user
    if tg_user is None:
        return None

    with SessionLocal() as s:
        user, _ = svc.get_or_create_user(
            s, tg_user.id, first_name=tg_user.first_name or "",
            last_name=tg_user.last_name or "", username=tg_user.username or "")
        svc.touch_activity(s, tg_user.id)
        s.commit()
        lang = user.language
        onboarded = user.onboarded
        ws = svc.workspace_id_for(s, tg_user.id)

    if not onboarded:
        await start(update, ctx)
        return None

    verdict = await deps.check_subscription(tg_user.id, ctx.bot)
    target = update.effective_message
    if verdict not in deps.ALLOWED:
        if target:
            # "missing" is the free run being spent, which is a different
            # message from having left a channel already joined.
            await target.reply_text(
                t(lang, "sub_unknown" if verdict == "unknown" else "trial_over"),
                parse_mode=ParseMode.HTML,
                reply_markup=subscribe_keyboard(lang))
        return None

    with SessionLocal() as s:
        user = s.get(User, tg_user.id)
        return user, ws


async def count_action(telegram_id: int, ctx: ContextTypes.DEFAULT_TYPE | None = None,
                       message=None, lang: str = "uz") -> None:
    """Record one thing done, and say so at the two moments it matters.

    Silence until the run is nearly spent, then one warning, then the ask. A
    counter shown after every tick would turn using the app into watching a
    meter run down, which is the opposite of the point.
    """
    with SessionLocal() as s:
        svc.record_action(s, telegram_id)
        s.commit()
        user = s.get(User, telegram_id)
        trial = deps.trial_state(user)

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

def main_menu(lang: str) -> ReplyKeyboardMarkup:
    """The persistent menu, in a fixed order.

    Home, Habits, Tasks and Statistics are the four screens the Mini App also
    has, so a feature found in one surface is findable in the other.

    "Turdim" gets its own full-width row directly above the Mini App button —
    the two rows a thumb reaches first, at the bottom of the keyboard. It is the
    one action that expires, so telling someone to type it, or to go two screens
    in to find it, is how a wake-up habit stops being recorded.
    """
    rows = [
        [t(lang, "menu_home"), t(lang, "menu_habits")],
        [t(lang, "menu_tasks"), t(lang, "menu_stats")],
        [t(lang, "menu_settings"), t(lang, "menu_feedback")],
        [t(lang, "menu_wake")],
    ]
    if WEBAPP_URL:
        rows.append([t(lang, "menu_app")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def webapp_button(lang: str) -> InlineKeyboardMarkup | None:
    if not WEBAPP_URL:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        t(lang, "menu_app"), web_app=WebAppInfo(url=WEBAPP_URL))]])


def cancel_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t(lang, "cancel"), callback_data="flow:cancel")]])


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    message = update.effective_message
    if tg_user is None or message is None:
        return

    with SessionLocal() as s:
        user, created = svc.get_or_create_user(
            s, tg_user.id, first_name=tg_user.first_name or "",
            last_name=tg_user.last_name or "", username=tg_user.username or "")
        s.commit()
        lang, step, onboarded = user.language, user.onboarding_step, user.onboarded
        snapshot = user

    if created:
        await log_event(ctx.bot, snapshot, f"🆕 NEW ERNESTOS USER #{snapshot.member_no}",
                        f"Language: {lang}\nRegistered: "
                        f"{datetime.now(svc.TZ):%Y-%m-%d %H:%M}")

    if onboarded:
        await message.reply_text(t(lang, "hello_named", name=tg_user.first_name or ""),
                                 reply_markup=main_menu(lang))
        await show_home(update, ctx)
        return

    await resume_onboarding(update, ctx, step)


#: The order onboarding walks, and the only place it is written down.
#:
#:   language → intro → name → goal → tasks → habits → done
#:
#: Six taps and four short answers, and every one of them builds something the
#: user then sees. That is the whole design: onboarding is not a form standing
#: between somebody and the product, it *is* the product's first use. They
#: finish it holding a real week's goal, three real tasks for today and their
#: habits — so the last screen can be their actual day rather than a tour of
#: an empty one.
#:
#: What is deliberately not here:
#:   * the channel. It used to be step two, asked before the user had seen a
#:     single thing the product does, which is a toll booth at the door. It is
#:     now asked after `FREE_ACTIONS` real actions, when the answer is
#:     "obviously, this is useful" instead of a gamble.
#:   * the phone number, which was the most personal thing the app ever asked
#:     for and bought the user nothing they could feel. Not asked anywhere.
#:   * gender, which only the prayer module needs, so it is asked the first
#:     time prayer is opened and explained when it is asked.
ONBOARDING_STEPS = ["language", "intro", "name", "goal", "tasks", "habits", "done"]

#: Steps from older builds. Anybody parked on one is moved into the new flow
#: rather than shown a question that no longer exists.
LEGACY_STEPS = {"phone", "gender", "subscribe"}


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

    with SessionLocal() as s:
        user = s.get(User, tg_user.id)
        lang = user.language if user else "uz"
        name = (user.first_name if user else "") or tg_user.first_name or ""

    if step in LEGACY_STEPS:
        step = "name"
        with SessionLocal() as s:
            user = s.get(User, tg_user.id)
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

    elif step == "intro":
        await send_intro(message, lang)

    elif step == "name":
        rows = []
        if name:
            rows.append([InlineKeyboardButton(f"👤 {name}",
                                              callback_data="setup:name")])
        await message.reply_text(t(lang, "ask_name"), parse_mode=ParseMode.HTML,
                                 reply_markup=InlineKeyboardMarkup(rows) if rows
                                 else None)

    elif step == "goal":
        await message.reply_text(t(lang, "ask_goal"), parse_mode=ParseMode.HTML,
                                 reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(t(lang, "skip_step"), callback_data="setup:skip")]]))

    elif step == "tasks":
        await message.reply_text(t(lang, "ask_tasks"), parse_mode=ParseMode.HTML,
                                 reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(t(lang, "skip_step"), callback_data="setup:skip")]]))

    elif step == "habits":
        await message.reply_text(t(lang, "ask_habits"), parse_mode=ParseMode.HTML,
                                 reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(t(lang, "habits_keep"), callback_data="setup:skip")]]))

    else:
        await finish_onboarding(update, ctx)


async def send_intro(message, lang: str) -> None:
    """The hook: what this is, in the fewest words that can carry it.

    One screen, one promise, one button. The old guide was eleven paragraphs
    sent to somebody who had not yet done anything — a manual for a machine
    they had not been shown. It still exists behind /guide, for the moment
    somebody actually wants it.
    """
    await message.reply_text(t(lang, "intro"), parse_mode=ParseMode.HTML,
                             disable_web_page_preview=True,
                             reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "intro_go"), callback_data="setup:go")]]))


async def advance_setup(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                        step: str) -> None:
    """Write the next step down, then ask it."""
    tg_user = update.effective_user
    with SessionLocal() as s:
        user = s.get(User, tg_user.id)
        if user is not None:
            user.onboarding_step = step
            s.commit()
    if step == "done":
        await finish_onboarding(update, ctx)
    else:
        await resume_onboarding(update, ctx, step)


async def handle_setup_answer(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                              step: str, text: str) -> None:
    """A typed answer during setup. Each step writes something real.

    Nothing is stored in limbo: the goal becomes the week's mission the moment
    it is typed, the tasks become tasks. If somebody walks away at step five,
    what they entered in steps three and four is already theirs.
    """
    tg_user = update.effective_user
    message = update.effective_message
    with SessionLocal() as s:
        user = s.get(User, tg_user.id)
        if user is None:
            return
        lang = user.language
        ws = svc.workspace_id_for(s, tg_user.id)
        tz = svc.user_tz(user)

    if step == "name":
        with SessionLocal() as s:
            user = s.get(User, tg_user.id)
            user.first_name = text.strip()[:200]
            s.commit()
        return await advance_setup(update, ctx, "goal")

    if step == "goal":
        with SessionLocal() as s:
            try:
                svc.add_focus(s, ws, text, tz=tz, priority="high")
            except ValueError:
                pass                     # empty or a full week — move on either way
        await message.reply_text(t(lang, "goal_set", goal=esc(text.strip()[:200])),
                                 parse_mode=ParseMode.HTML)
        return await advance_setup(update, ctx, "tasks")

    if step == "tasks":
        # One per line, so three tasks is one message rather than three rounds
        # of question and answer. That is most of the sixty seconds.
        titles = [line.strip(" -•\t") for line in text.splitlines()]
        titles = [x for x in titles if x][:SETUP_MAX_TASKS]
        today = svc.today_local(tz)
        with SessionLocal() as s:
            for title in titles:
                try:
                    svc.add_task(s, ws, title, deadline=today)
                except ValueError:
                    continue
        if titles:
            await message.reply_text(
                t(lang, "tasks_set", n=len(titles)), parse_mode=ParseMode.HTML)
        return await advance_setup(update, ctx, "habits")

    if step == "habits":
        names = [line.strip(" -•\t") for line in text.splitlines()]
        names = [x for x in names if x][:SETUP_MAX_HABITS]
        with SessionLocal() as s:
            for name in names:
                try:
                    svc.add_habit(s, ws, name, "target")
                except ValueError:
                    continue
        if names:
            await message.reply_text(t(lang, "habits_set", n=len(names)),
                                     parse_mode=ParseMode.HTML)
        return await advance_setup(update, ctx, "done")

    # Any other step takes no typed answer; re-ask rather than swallow it.
    await resume_onboarding(update, ctx, step)


#: Three tasks and three habits. The caps are the product's opinion: a first
#: day with nine things on it is a first day that does not get finished, and
#: the number somebody can actually hold is three.
SETUP_MAX_TASKS = 3
SETUP_MAX_HABITS = 3

#: Callback actions that change the day, and therefore spend a free action.
#: `set` and `theme` are settings, not use — the same reasoning as
#: `UNCOUNTED_PATHS` on the API side.
COUNTED_CALLBACKS = {"habit", "task", "taskday", "taskproj", "project", "habitcat"}


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
    tg_user = update.effective_user
    if message is None or message.contact is None or tg_user is None:
        return

    with SessionLocal() as s:
        user = s.get(User, tg_user.id)
        lang = user.language if user else "uz"

    await message.reply_text(t(lang, "phone_not_needed"),
                             reply_markup=main_menu(lang))


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Store the Telegram file_id of an uploaded avatar.

    Only the id is kept — Telegram already hosts the bytes, so the database
    never holds binary blobs.
    """
    message = update.effective_message
    tg_user = update.effective_user
    if message is None or not message.photo or tg_user is None:
        return
    if (ctx.user_data.get("flow") or {}).get("name") != "photo_wait":
        return

    file_id = message.photo[-1].file_id          # highest resolution
    with SessionLocal() as s:
        user = s.get(User, tg_user.id)
        if user is None:
            return
        user.photo_file_id = file_id
        s.commit()
        lang, snapshot = user.language, user

    ctx.user_data.pop("flow", None)
    await message.reply_text(t(lang, "photo_saved"), reply_markup=main_menu(lang))
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
    tg_user = update.effective_user
    message = update.effective_message
    if tg_user is None or message is None:
        return

    with SessionLocal() as s:
        user = s.get(User, tg_user.id)
        if user is None:
            return
        user.onboarding_step = "done"
        user.onboarded = True
        s.commit()
        lang, snapshot = user.language, user
        ws = svc.workspace_id_for(s, tg_user.id)
        data = svc.home(s, ws, user)

    ctx.user_data.pop("setup", None)
    await message.reply_text(render_day_ready(data, lang), parse_mode=ParseMode.HTML,
                             reply_markup=main_menu(lang))
    markup = webapp_button(lang)
    if markup:
        await message.reply_text(t(lang, "day_ready_app"), reply_markup=markup)
    await log_event(ctx.bot, snapshot, "✅ ONBOARDING COMPLETE",
                    f"Language: {snapshot.language}")


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

    focus = data.get("focus") or {}
    primary = focus.get("primary")
    if primary:
        lines.append(f"🎯 <b>{t(lang, 'r_mission')}</b>")
        lines.append(esc(primary["title"]))
        lines.append("")

    groups = data.get("tasks_today") or []
    # Whatever was pinned leads, then the rest of today — the same order the
    # app itself shows them in.
    tasks = list(data.get("top3") or [])
    tasks += [x for group in groups for x in group["tasks"]]
    if tasks:
        lines.append(f"⚡ <b>{t(lang, 'r_today_plan')}</b>")
        for task in tasks[:SETUP_MAX_TASKS]:
            lines.append(f"• {esc(task['title'])}")
        lines.append("")

    habits = data.get("habits") or {}
    if habits.get("total"):
        lines.append(f"✅ <b>{t(lang, 'r_habits_today')}</b> · {habits['total']}")
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
        return f"{icon} {esc(now.get('title') or '')}{meta}"
    return f"{icon} {t(lang, 'now_' + kind)}"


def render_home(data: dict, lang: str) -> str:
    """Home in one screenful: the mission, today's work, today's numbers.

    Deliberately short. Everything that used to sit here — goals, projects,
    birthdays, the overdue wall — answered a question the user was not asking at
    6am, and each line of it pushed the answer further down the message.
    """
    name = (data.get("name") or "").strip()
    title = t(lang, "home_title", name=esc(name)) if name \
        else t(lang, "home_title_plain")
    lines = [f"<b>{title}</b>", f"📅 {data['date_label']}"]

    mission = data.get("mission")
    lines.append(f"\n<b>{t(lang, 'home_mission')}</b>")
    lines.append(esc(mission["title"]) if mission else t(lang, "none"))

    lines.append(f"\n<b>{t(lang, 'home_today')}</b>")
    rows = [task for group in data["tasks_today"] for task in group["tasks"]]
    if rows:
        for task in rows[:8]:
            when = f" · {task['due_time']}" if task.get("due_time") else ""
            lines.append(f"— {esc(task['title'])}{when}")
    else:
        lines.append(t(lang, "none"))

    # One status line: habits, prayers, streak. Then the one number.
    habits, prayer, overall = data["habits"], data["prayer"], data["overall"]
    lines.append(f"\n✅  {habits['done']}/{habits['total']} ·"
                 f"🕌 {prayer['performed']}/{prayer['required']} · 🔥{data['streak']}")
    lines.append(f"📊 {TREND_MARK.get(overall['trend'], '▪️')} {overall['value']}%")

    lines.append(f"\n{t(lang, 'privacy_line')}")
    return "\n".join(lines)


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
    lines.append(f"<b>{t(lang, 'st_today')}</b>")
    lines.append(f"{_bar(today['overall'])}  <b>{today['overall']}%</b> "
                 f"{TREND_MARK.get(today['trend'], '▪️')}")
    lines.append(f"{t(lang, 'st_tasks')}: {pct(today['tasks'])} · "
                 f"{t(lang, 'st_habits')}: {pct(today['habits'])} · "
                 f"{t(lang, 'st_prayer')}: {today['prayer_performed']}/"
                 f"{today['prayer_required']}")

    # Then the two longer windows, each with its own overall.
    for key, label in (("week", "st_week"), ("month", "st_month")):
        window = data["windows"][key]
        lines.append("")
        lines.append(f"<b>{t(lang, label)}</b>")
        lines.append(f"{_bar(window['overall'])}  <b>{window['overall']}%</b> "
                     f"{_delta(window['delta'])}")
        lines.append(f"{t(lang, 'st_tasks')}: {window['tasks']}% · "
                     f"{t(lang, 'st_habits')}: {window['habits']}% · "
                     f"{t(lang, 'st_prayer')}: {window['prayer']}%")

    lines.append("")
    lines.append(f"🔥 {t(lang, 'st_streak')}: {today['streak']}")
    lines.append("")
    lines.append(t(lang, "privacy_line"))
    return "\n".join(lines)


def _delta(value: int) -> str:
    """A signed change against the previous window of the same length."""
    if not value:
        return "▪️"
    return f"{'🔺' if value > 0 else '🔻'}{abs(value)}%"


async def show_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    got = await guard(update, ctx)
    if got is None:
        return
    user, ws = got
    with SessionLocal() as s:
        data = svc.summary(s, ws, gender=user.gender, tz=svc.user_tz(user))
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
    got = await guard(update, ctx)
    if got is None:
        return
    user, ws = got
    with SessionLocal() as s:
        data = svc.home(s, ws, s.get(User, user.telegram_id))
    message = update.effective_message
    if message:
        await message.reply_text(render_home(data, user.language),
                                 parse_mode=ParseMode.HTML,
                                 reply_markup=webapp_button(user.language))


# ---------------------------------------------------------------------------
# Habits
# ---------------------------------------------------------------------------

CATEGORY_KEYS = {"non_negotiable": "cat_non_negotiable",
                 "target": "cat_target", "bonus": "cat_bonus"}


def habits_keyboard(grouped: dict, lang: str) -> InlineKeyboardMarkup:
    """One section per tier, so the three categories stay visible at a glance."""
    rows = []
    for category in svc.HABIT_CATEGORIES:
        habits = grouped.get(category, [])
        if not habits:
            continue
        rows.append([InlineKeyboardButton(t(lang, CATEGORY_KEYS[category]),
                                          callback_data="habit:noop")])
        for h in habits:
            if h.get("paused"):
                # A paused habit is shown, greyed by its label, with resume as
                # the only thing it can do. Hiding it would mean it can never
                # come back.
                rows.append([InlineKeyboardButton(
                    f"⏸ {h['name']}", callback_data=f"habit:resume:{h['id']}")])
                continue
            if not h.get("due", True):
                # Not scheduled today: listed without a checkbox, so an off-day
                # never looks like something the user skipped.
                rows.append([InlineKeyboardButton(
                    f"·  {h['name']}", callback_data="habit:noop")])
                continue
            mark = "✅" if h["done"] else "⬜"
            lock = " 🔒" if h["protected"] else ""
            clock = f" ⏰{h['target_time']}" if h.get("target_time") else ""
            rows.append([InlineKeyboardButton(
                f"{mark} {h['name']}{clock}{lock}",
                callback_data=f"habit:toggle:{h['id']}")])
    # "Turdim" left the persistent menu, so it lives here, where the habit it
    # belongs to is — and only while it can still be recorded today.
    wake = next((h for group in grouped.values() for h in group
                 if h.get("system_key") == svc.SYSTEM_WAKEUP), None)
    if wake and not wake["done"]:
        rows.append([InlineKeyboardButton(t(lang, "menu_wake"),
                                          callback_data="habit:wake")])

    rows.append([
        InlineKeyboardButton(t(lang, "btn_add_habit"), callback_data="habit:add"),
        InlineKeyboardButton(t(lang, "btn_del_habit"), callback_data="habit:dellist"),
    ])
    return InlineKeyboardMarkup(rows)


async def show_habits(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                      edit: bool = False) -> None:
    got = await guard(update, ctx)
    if got is None:
        return
    user, ws = got
    tz = svc.user_tz(user)
    with SessionLocal() as s:
        grouped = svc.habits_by_category(s, ws, tz=tz)
        streak = svc.habit_streak(s, ws, tz=tz)

    text = f"<b>{t(user.language, 'habits_title')}</b>"
    if streak:
        text += f"   {t(user.language, 'streak')}: {streak}"
    markup = habits_keyboard(grouped, user.language)
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


def _task_lines(tasks: list[dict], lang: str, start: int = 1) -> list[str]:
    """Numbered rows: colour, number, title, then the details underneath."""
    lines = []
    for number, task in enumerate(tasks, start=start):
        mark = PRIORITY_MARK.get(task["priority"], "▫️")
        lines.append(f"{mark} <b>{number}.</b> {esc(task['title'])}")
        meta = []
        if task.get("deadline"):
            meta.append(short_date(task["deadline"], lang))
        if task.get("due_time"):
            meta.append(task["due_time"])
        if task.get("recurrence"):
            meta.append("🔁")
        if meta:
            lines.append(f"     └ {' · '.join(meta)}")
    return lines


def render_tasks(data: dict, lang: str) -> str:
    """The next seven days, grouped by project.

    The old version printed every task as two dense lines with a folder icon and
    an ISO date, and thirty of those is a wall nobody reads. Grouping by project
    removes the repeated folder name, numbering gives the eye somewhere to land,
    and the colour comes first so the list is sorted before it is read.
    """
    lines = []

    if data["overdue"]:
        lines.append(f"<b>{t(lang, 'tasks_overdue')}</b>")
        lines += _task_lines(data["overdue"], lang)
        lines.append("")

    lines.append(f"<b>{t(lang, 'tasks_title')}</b>")

    # Group the coming week under the project each task belongs to.
    groups: dict[str | None, list[dict]] = {}
    for task in data["upcoming"]:
        groups.setdefault(task["project"], []).append(task)

    if groups:
        named = sorted((k for k in groups if k), key=lambda x: x.lower())
        for project in named:
            lines.append("")
            lines.append(f"📁 <b>{esc(project)}</b>")
            lines += _task_lines(groups[project], lang)
        if None in groups:
            lines.append("")
            # The label already carries its own icon.
            lines.append(f"<b>{t(lang, 'standalone')}</b>")
            lines += _task_lines(groups[None], lang)
    else:
        lines.append(t(lang, "none"))

    if data["undated"]:
        lines.append("")
        lines.append(f"<b>{t(lang, 'tasks_undated')}</b>")
        lines += _task_lines(data["undated"][:6], lang)

    return "\n".join(lines)


def tasks_keyboard(lang: str, *, projects: list[dict],
                   open_tasks: int, editable: int) -> InlineKeyboardMarkup:
    """Only buttons that lead somewhere.

    A "Bajarildi" button on an empty task list opens a chooser with nothing in
    it — the user taps, gets an alert, and learns the app is lying about what
    it can do. So each control appears only when it has something to act on:
    with no data at all, the screen is two Add buttons and nothing else.
    Completing still replaces deleting, so finished work keeps its history.
    """
    rows = []
    for project in projects[:6]:
        rows.append([InlineKeyboardButton(
            f"📁 {project['name'][:32]}", callback_data=f"project:open:{project['id']}")])

    rows.append([
        InlineKeyboardButton(t(lang, "btn_add_task"), callback_data="task:add"),
        InlineKeyboardButton(t(lang, "btn_add_project"), callback_data="project:add"),
    ])

    action_row = []
    if open_tasks:
        action_row.append(InlineKeyboardButton(t(lang, "btn_done_task"),
                                               callback_data="task:donelist"))
    if editable:
        action_row.append(InlineKeyboardButton(t(lang, "btn_edit_task"),
                                               callback_data="task:editlist"))
    if action_row:
        rows.append(action_row)

    delete_row = []
    if editable:
        delete_row.append(InlineKeyboardButton(t(lang, "btn_del_task"),
                                               callback_data="task:dellist"))
    if projects:
        delete_row.append(InlineKeyboardButton(t(lang, "btn_del_project"),
                                               callback_data="project:dellist"))
    if delete_row:
        rows.append(delete_row)

    return InlineKeyboardMarkup(rows)


def _all_open_tasks(s, ws: int) -> list[dict]:
    data = svc.list_tasks(s, ws, horizon_days=365)
    return data["overdue"] + data["upcoming"] + data["undated"]


async def show_tasks(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                     edit: bool = False) -> None:
    got = await guard(update, ctx)
    if got is None:
        return
    user, ws = got
    with SessionLocal() as s:
        data = svc.list_tasks(s, ws, horizon_days=7)
        projects = svc.list_projects(s, ws)
        open_tasks = len(_all_open_tasks(s, ws))

    text = render_tasks(data, user.language)
    markup = tasks_keyboard(user.language, projects=projects,
                            open_tasks=open_tasks, editable=open_tasks)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup)
    elif update.effective_message:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup)


def render_project(project: dict, tasks: list[dict], lang: str) -> str:
    """One project and the work inside it."""
    marks = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    lines = [f"<b>📁 {esc(project['name'])}</b>"]
    if project.get("description"):
        lines.append(esc(project["description"]))
    if project.get("deadline"):
        lines.append(f"📅 {project['deadline']}")
    lines.append(f"{project['tasks_done']} / {project['tasks_total']} · "
                 f"{project['progress']}%")

    lines.append(f"\n<b>{t(lang, 'project_tasks')}</b>")
    if tasks:
        for task in tasks:
            mark = "✅" if task["status"] == "done" else marks.get(task["priority"], "▫️")
            when = f" · 📅 {task['deadline']}" if task["deadline"] else ""
            lines.append(f"{mark} {esc(task['title'])}{when}")
    else:
        lines.append(t(lang, "none"))
    return "\n".join(lines)


async def show_project(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                       project_id: int) -> None:
    got = await guard(update, ctx)
    if got is None:
        return
    user, ws = got
    lang = user.language
    with SessionLocal() as s:
        project = next((p for p in svc.list_projects(s, ws)
                        if p["id"] == project_id), None)
        if project is None:
            raise svc.NotFound("project")
        tasks = svc.project_tasks(s, ws, project_id)

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_rename_project"),
                              callback_data=f"project:rename:{project_id}")],
        [InlineKeyboardButton(t(lang, "btn_del_project"),
                              callback_data=f"project:del:{project_id}")],
        [InlineKeyboardButton(t(lang, "back"), callback_data="task:back")],
    ])
    if update.callback_query:
        await update.callback_query.edit_message_text(
            render_project(project, tasks, lang),
            parse_mode=ParseMode.HTML, reply_markup=markup)


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
        [InlineKeyboardButton(t(lang, "btn_lang"), callback_data="set:lang")],
        [InlineKeyboardButton(t(lang, "btn_gender"), callback_data="set:gender")],
        [InlineKeyboardButton(t(lang, "btn_theme"), callback_data="set:theme")],
        [InlineKeyboardButton(t(lang, "btn_photo"), callback_data="set:photo")],
        [InlineKeyboardButton(t(lang, "wake_time_btn"), callback_data="set:waketime")],
    ])
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup)
    elif update.effective_message:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup)


# ---------------------------------------------------------------------------
# Multi-step flows
#
# `ctx.user_data["flow"]` holds only the in-progress step. Losing it on restart
# costs the user one retyped message; nothing durable depends on it.
# ---------------------------------------------------------------------------

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    tg_user = update.effective_user
    if message is None or tg_user is None or not message.text:
        return
    text = message.text.strip()

    with SessionLocal() as s:
        user = s.get(User, tg_user.id)
        if user is None:
            await start(update, ctx)
            return
        lang = user.language
        onboarded = user.onboarded
        step = user.onboarding_step

    if not onboarded:
        # Setup is mostly typed now — a name, a goal, three tasks, some habits
        # — so a typed message during it is an answer, not noise. The steps
        # that take no typing re-prompt instead of swallowing the text, so
        # somebody who types "salom" at the language screen is never left
        # staring at nothing.
        await handle_setup_answer(update, ctx, step, text)
        return

    flow = current_flow(ctx)
    if flow:
        await handle_flow(update, ctx, flow, text)
        return

    # Main menu routing — match against every language so a language change
    # mid-session never strands the user with a dead keyboard.
    for code in ("uz", "en", "ru"):
        if text == t(code, "menu_wake"):
            return await handle_wakeup(update, ctx)
        if text == t(code, "menu_home"):
            return await show_home(update, ctx)
        if text == t(code, "menu_habits"):
            return await show_habits(update, ctx)
        if text == t(code, "menu_tasks"):
            return await show_tasks(update, ctx)
        if text == t(code, "menu_stats"):
            return await show_stats(update, ctx)
        if text == t(code, "menu_settings"):
            return await show_settings(update, ctx)
        if text == t(code, "menu_feedback"):
            start_flow(ctx, "feedback")
            return await message.reply_text(t(lang, "ask_feedback"),
                                            reply_markup=cancel_keyboard(lang))
        if text == t(code, "menu_app"):
            markup = webapp_button(lang)
            if markup:
                return await message.reply_text(t(lang, "open_app"), reply_markup=markup)
            return

    await show_home(update, ctx)


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
            start_flow(ctx, "habit_cat", title=text)
            await message.reply_text(t(lang, "ask_habit_cat"),
                                     reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(t(lang, "cat_non_negotiable"),
                                      callback_data="habitcat:non_negotiable")],
                [InlineKeyboardButton(t(lang, "cat_target"),
                                      callback_data="habitcat:target")],
                [InlineKeyboardButton(t(lang, "cat_bonus"),
                                      callback_data="habitcat:bonus")],
                [InlineKeyboardButton(t(lang, "cancel"), callback_data="flow:cancel")],
            ]))

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
                reply_markup=main_menu(lang))

        elif name == "task_edit":
            with SessionLocal() as s:
                task = svc.update_task(s, ws, flow["target_id"], title=text)
            ctx.user_data.pop("flow", None)
            await message.reply_text(t(lang, "task_updated", title=task.title))
            await show_tasks(update, ctx)

        elif name == "task_title":
            start_flow(ctx, "task_days", title=text)
            await message.reply_text(t(lang, "ask_task_days"),
                                     reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(t(lang, "days_today"), callback_data="taskday:0"),
                 InlineKeyboardButton(t(lang, "days_1"), callback_data="taskday:1")],
                [InlineKeyboardButton(t(lang, "days_2"), callback_data="taskday:2"),
                 InlineKeyboardButton(t(lang, "days_3"), callback_data="taskday:3")],
                [InlineKeyboardButton(t(lang, "days_7"), callback_data="taskday:7"),
                 InlineKeyboardButton(t(lang, "days_custom"), callback_data="taskday:custom")],
                [InlineKeyboardButton(t(lang, "cancel"), callback_data="flow:cancel")],
            ]))

        elif name == "task_custom_days":
            try:
                days = int(text)
                if not 0 <= days <= 3650:
                    raise ValueError
            except ValueError:
                await message.reply_text(t(lang, "ask_custom_days"))
                return
            deadline = svc.today_local() + timedelta(days=days)
            await ask_task_project(update, ctx, flow["title"], deadline)

        elif name == "project_add":
            with SessionLocal() as s:
                project = svc.add_project(s, ws, text)
            ctx.user_data.pop("flow", None)
            await message.reply_text(t(lang, "project_added", name=project.name))
            await log_event(ctx.bot, user, "📁 PROJECT ADDED", f"Project: {esc(project.name)}")
            await show_tasks(update, ctx)

        elif name == "project_rename":
            with SessionLocal() as s:
                project = svc.update_project(s, ws, flow["target_id"], name=text)
                name_after = project.name
            ctx.user_data.pop("flow", None)
            await message.reply_text(t(lang, "project_updated", name=name_after))
            await log_event(ctx.bot, user, "✏️ PROJECT RENAMED",
                            f"Project: {esc(name_after)}")
            await show_tasks(update, ctx)

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

    except ValueError:
        ctx.user_data.pop("flow", None)
        await message.reply_text(t(lang, "error"))
    except svc.NotFound:
        ctx.user_data.pop("flow", None)
        await message.reply_text(t(lang, "not_found"))


async def ask_task_project(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                           title: str, deadline: date) -> None:
    got = await guard(update, ctx)
    if got is None:
        return
    user, ws = got
    lang = user.language
    with SessionLocal() as s:
        projects = svc.list_projects(s, ws)

    start_flow(ctx, "task_project", title=title, deadline=deadline.isoformat())
    rows = [[InlineKeyboardButton(t(lang, "standalone"), callback_data="taskproj:0")]]
    for p in projects[:10]:
        rows.append([InlineKeyboardButton(f"📁 {p['name']}",
                                          callback_data=f"taskproj:{p['id']}")])
    rows.append([InlineKeyboardButton(t(lang, "cancel"), callback_data="flow:cancel")])

    message = update.effective_message
    if message:
        await message.reply_text(t(lang, "ask_task_project"),
                                 reply_markup=InlineKeyboardMarkup(rows))


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
        tg_user = update.effective_user
        state = await is_subscribed(ctx.bot, tg_user.id)
        with SessionLocal() as s:
            user = s.get(User, tg_user.id)
            lang = user.language if user else "uz"
            if state is not True:
                await query.edit_message_text(
                    t(lang, "sub_missing" if state is False else "sub_unknown"),
                    reply_markup=subscribe_keyboard(lang))
                return
            changed = record_membership(s, tg_user.id, True, "api")
            first_time = not user.onboarded
            if first_time:
                user.onboarding_step = "done"
                user.onboarded = True
            s.commit()
            snapshot, name = user, user.first_name or ""
        if changed:
            await log_event(ctx.bot, snapshot, "🔓 SUBSCRIPTION RESTORED")
        # Joining and checking lands the user inside — never back at /start.
        await query.edit_message_text(t(lang, "sub_restored"))
        await update.effective_message.reply_text(
            t(lang, "welcome_in", name=esc(name)) if first_time else t(lang, "saved"),
            parse_mode=ParseMode.HTML, reply_markup=main_menu(lang))
        await show_home(update, ctx)
        return

    if action == "lang":
        tg_user = update.effective_user
        with SessionLocal() as s:
            user = s.get(User, tg_user.id)
            user.language = parts[1]
            if not user.onboarded:
                user.onboarding_step = "intro"
            s.commit()
            lang, onboarded = user.language, user.onboarded
            snapshot = user
        await log_event(ctx.bot, snapshot, "🌐 LANGUAGE CHANGED", f"Language: {lang}")

        if not onboarded:
            # First words in the language they just picked, by name — then the
            # pitch, before a single question is asked.
            await query.edit_message_text(
                t(lang, "hello_named", name=esc(tg_user.first_name or "")),
                parse_mode=ParseMode.HTML)
            await resume_onboarding(update, ctx, "intro")
        else:
            # A settings change says what it changed, not just "saved".
            await query.edit_message_text(t(lang, "lang_changed"))
            await update.effective_message.reply_text(t(lang, "lang_changed"),
                                                      reply_markup=main_menu(lang))
        return

    # Setup runs before onboarding completes, so it sits above the guard.
    if action == "setup":
        tg_user = update.effective_user
        with SessionLocal() as s:
            user = s.get(User, tg_user.id)
            if user is None:
                return
            lang, step = user.language, user.onboarding_step
            telegram_name = user.first_name or tg_user.first_name or ""

        if parts[1] == "go":
            await query.edit_message_reply_markup(reply_markup=None)
            return await advance_setup(update, ctx, "name")

        if parts[1] == "name":
            # Keeping the Telegram name is one tap, which is what it should be.
            await query.edit_message_text(t(lang, "name_set", name=esc(telegram_name)),
                                          parse_mode=ParseMode.HTML)
            return await advance_setup(update, ctx, "goal")

        if parts[1] == "skip":
            await query.edit_message_reply_markup(reply_markup=None)
            order = ONBOARDING_STEPS
            nxt = order[order.index(step) + 1] if step in order else "done"
            return await advance_setup(update, ctx, nxt)
        return

    if action == "gender":
        tg_user = update.effective_user
        with SessionLocal() as s:
            user = s.get(User, tg_user.id)
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
            user = s.get(User, update.effective_user.id)
            lang = user.language if user else "uz"
        await query.edit_message_text(t(lang, "cancelled"))
        return

    # Everything below requires a completed, subscribed account.
    got = await guard(update, ctx)
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
        await query.answer(t(lang, "not_found"), show_alert=True)
    except ValueError as e:
        if str(e) == "protected":
            await query.answer(t(lang, "habit_protected"), show_alert=True)
        else:
            await query.answer(t(lang, "error"), show_alert=True)


async def route_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                         action: str, parts: list[str], user: User,
                         ws: int, lang: str) -> None:
    query = update.callback_query
    message = update.effective_message

    # --- habits ---
    if action == "habit":
        sub = parts[1]
        if sub == "toggle":
            with SessionLocal() as s:
                svc.toggle_habit(s, ws, int(parts[2]))
            await show_habits(update, ctx, edit=True)
        elif sub == "add":
            start_flow(ctx, "habit_name")
            await message.reply_text(t(lang, "ask_habit_name"),
                                     reply_markup=cancel_keyboard(lang))
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
        elif sub == "back":
            await show_habits(update, ctx, edit=True)
        elif sub == "noop":
            pass

    # --- tasks ---
    elif action == "task":
        sub = parts[1]
        if sub == "add":
            start_flow(ctx, "task_title")
            await message.reply_text(t(lang, "ask_task_name"),
                                     reply_markup=cancel_keyboard(lang))
        elif sub in ("donelist", "editlist", "dellist"):
            with SessionLocal() as s:
                tasks = _all_open_tasks(s, ws)
            if not tasks:
                # The button is not drawn in this state, so reaching here means
                # a stale keyboard. Say so instead of opening an empty chooser.
                await query.answer(t(lang, "empty"), show_alert=True)
                return
            verb = {"donelist": "done", "editlist": "edit", "dellist": "del"}[sub]
            prompt = {"donelist": "choose_done", "editlist": "choose_edit",
                      "dellist": "choose_delete"}[sub]
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
            with SessionLocal() as s:
                task = svc.complete_task(s, ws, int(parts[2]))
            await log_event(ctx.bot, user, "✅ TASK COMPLETED", f"Task: {esc(task.title)}")
            await query.edit_message_text(t(lang, "task_done", title=task.title))
            await show_tasks(update, ctx)
        elif sub == "edit":
            start_flow(ctx, "task_edit", target_id=int(parts[2]))
            await query.edit_message_text(t(lang, "ask_new_title"))
        elif sub == "back":
            await show_tasks(update, ctx, edit=True)
        elif sub == "noop":
            pass

    elif action == "taskday":
        flow = current_flow(ctx, "task_days") or {}
        title = flow.get("title")
        if not title:
            await query.answer(t(lang, "error"), show_alert=True)
            return
        if parts[1] == "custom":
            start_flow(ctx, "task_custom_days", title=title)
            await query.edit_message_text(t(lang, "ask_custom_days"))
            return
        deadline = svc.today_local() + timedelta(days=int(parts[1]))
        await query.edit_message_text(f"📅 {deadline.isoformat()}")
        await ask_task_project(update, ctx, title, deadline)

    elif action == "taskproj":
        flow = current_flow(ctx, "task_project") or {}
        title, deadline = flow.get("title"), flow.get("deadline")
        if not title:
            await query.answer(t(lang, "error"), show_alert=True)
            return
        project_id = int(parts[1]) or None
        with SessionLocal() as s:
            # The chat flow asks for a title, a date and a project and stops
            # there, so a task made here would never get a reminder at all.
            # It gets the standard one instead; the Mini App's task sheet is
            # where it can be changed or turned off.
            task = svc.add_task(s, ws, title,
                                deadline=date.fromisoformat(deadline) if deadline else None,
                                project_id=project_id,
                                remind_before=svc.DEFAULT_REMIND_BEFORE
                                if deadline else None)
        ctx.user_data.pop("flow", None)
        await query.edit_message_text(t(lang, "task_added", title=task.title))
        await log_event(ctx.bot, user, "⚡ TASK ADDED",
                        f"Task: {task.title}\nDeadline: {deadline or '—'}")
        await show_tasks(update, ctx)

    # --- projects ---
    elif action == "project":
        sub = parts[1]
        if sub == "add":
            start_flow(ctx, "project_add")
            await message.reply_text(t(lang, "ask_project_name"),
                                     reply_markup=cancel_keyboard(lang))
        elif sub == "dellist":
            with SessionLocal() as s:
                projects = svc.list_projects(s, ws)
            if not projects:
                await query.answer(t(lang, "empty"), show_alert=True)
                return
            rows = [[InlineKeyboardButton(p["name"][:40],
                                          callback_data=f"project:del:{p['id']}")]
                    for p in projects[:15]]
            rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="task:back")])
            await query.edit_message_text(t(lang, "choose_delete"),
                                          reply_markup=InlineKeyboardMarkup(rows))
        elif sub == "open":
            await show_project(update, ctx, int(parts[2]))
        elif sub == "rename":
            start_flow(ctx, "project_rename", target_id=int(parts[2]))
            await query.edit_message_text(t(lang, "ask_project_rename"),
                                          reply_markup=cancel_keyboard(lang))
        elif sub == "del":
            with SessionLocal() as s:
                name = svc.delete_project(s, ws, int(parts[2]))
            await log_event(ctx.bot, user, "🗑 PROJECT DELETED", f"Project: {esc(name)}")
            await show_tasks(update, ctx, edit=True)

    elif action == "habitcat":
        flow = current_flow(ctx, "habit_cat") or {}
        title = flow.get("title")
        if not title:
            await query.answer(t(lang, "error"), show_alert=True)
            return
        with SessionLocal() as s:
            habit = svc.add_habit(s, ws, title, parts[1])
        ctx.user_data.pop("flow", None)
        await query.edit_message_text(t(lang, "habit_added", name=habit.name))
        await log_event(ctx.bot, user, "➕ HABIT ADDED",
                        f"Habit: {habit.name}\nCategory: {parts[1]}")
        await show_habits(update, ctx)

    # --- settings ---
    elif action == "set":
        sub = parts[1]
        # Every sub-screen offers Back, so changing your mind never strands you.
        back = [InlineKeyboardButton(t(lang, "back"), callback_data="set:back")]

        if sub == "back":
            await show_settings(update, ctx, edit=True)
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
        elif sub == "photodel":
            ctx.user_data.pop("flow", None)
            with SessionLocal() as s:
                row = s.get(User, user.telegram_id)
                row.photo_file_id = ""
                s.commit()
            await query.edit_message_text(t(lang, "photo_removed"))
            await show_settings(update, ctx)

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
                                       reply_markup=main_menu(lang))
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
    lines.append("")

    def row(label: str, done, total, percent) -> str:
        return (f"{label}  {_bar(percent if percent is not None else 0, 6)}  "
                f"{done}/{total}")

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

    unfinished = (data["tasks_remaining"] + data["tasks_overdue"]
                  + data["habits_remaining"])
    if unfinished:
        lines.append("")
        lines.append(f"{t(lang, 'r_unfinished')}")
        for item in unfinished[:8]:
            lines.append(f"• {esc(item)}")
        if len(unfinished) > 8:
            lines.append(f"<i>+{len(unfinished) - 8}</i>")
        lines.append("")
        lines.append(f"<i>{t(lang, 'r_evening_close')}</i>")
    else:
        lines.append("")
        lines.append(t(lang, "r_evening_clear"))

    # The last line of the day.
    lines.append("")
    lines.append(f"<b>{t(lang, 'r_good_night')}</b>")

    return "\n".join(lines)


async def send_platform_stats(bot) -> None:
    """Aggregate usage numbers for the operator. No user content, ever."""
    if not STATS_CHANNEL_ID:
        return
    # The lock must span the send, not just the query — otherwise a second
    # instance takes it the moment the query ends and posts a duplicate.
    with svc.JobLock(SessionLocal, "stats") as lock:
        if not lock.acquired:
            return
        with SessionLocal() as s:
            st = svc.platform_stats(s)
        await _post_platform_stats(bot, st)


async def _post_platform_stats(bot, st: dict) -> None:
    languages = " · ".join(f"{k or '—'} {v}" for k, v in sorted(st["languages"].items()))
    genders = " · ".join(f"{k or '—'} {v}" for k, v in sorted(st["genders"].items()))

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
        f"<b>Til</b>: {languages or '—'}\n<b>Jins</b>: {genders or '—'}"
    ), chat_id=STATS_CHANNEL_ID)


async def send_reports(bot, report_type: str) -> None:
    """Deliver one report to every user whose chosen time has just arrived.

    The job runs often and decides per user, because report times are now a
    setting rather than one hour for everybody. Sending exactly once per local
    day is still guaranteed by the outbox claim, not by the schedule.
    """
    with svc.JobLock(SessionLocal, f"report:{report_type}") as lock:
        if not lock.acquired:
            return
        await _send_reports_locked(bot, report_type, None)


async def _send_reports_locked(bot, report_type: str, report_date) -> None:
    with SessionLocal() as s:
        recipients = svc.active_recipients(s)

    sent = failed = skipped = 0
    for telegram_id, ws, lang in recipients:
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
            skipped += 1
            continue

        # Claim before building anything: the insert is the lock, so a second
        # worker finds the row taken and moves on (audit 036).
        with SessionLocal() as s:
            report_id = svc.claim_report(s, ws, report_type, when)
        if report_id is None:
            skipped += 1
            continue

        try:
            with SessionLocal() as s:
                user = s.get(User, telegram_id)
                if user is None:
                    svc.release_report(s, report_id)
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

        except TelegramError as e:
            # Blocked bot or deleted account: record and continue.
            log.warning("%s report to %s failed: %s", report_type, telegram_id, e)
            with SessionLocal() as s:
                svc.mark_report_failed(s, report_id, str(e))
            failed += 1

        except Exception as e:
            # Any other error must not abort the remaining recipients
            # (audit 037). Release the claim so a later run can retry.
            log.exception("%s report to %s errored", report_type, telegram_id)
            with SessionLocal() as s:
                svc.mark_report_failed(s, report_id, repr(e))
            failed += 1

        await asyncio.sleep(0.05)  # stay inside Telegram's rate limit

    log.info("%s report: %s sent, %s failed, %s not due or already claimed",
             report_type, sent, failed, skipped)


async def send_reminders(bot) -> None:
    """Task and habit reminders whose moment has just arrived.

    Each reminder is marked sent the instant it goes out, so a job that runs
    every few minutes cannot repeat one. Reminders are opt-out for tasks and
    opt-in for habits: a notification a day is how an app gets muted.
    """
    with svc.JobLock(SessionLocal, "reminders") as lock:
        if not lock.acquired:
            return

        with SessionLocal() as s:
            recipients = svc.active_recipients(s)

        sent = 0
        for telegram_id, ws, lang in recipients:
            with SessionLocal() as s:
                user = s.get(User, telegram_id)
                if user is None:
                    continue
                tasks = svc.due_task_reminders(s, ws, user)
                habits = svc.due_habit_reminders(s, ws, user)

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
                    log.warning("task reminder to %s failed: %s", telegram_id, e)

            for habit in habits:
                try:
                    await bot.send_message(
                        telegram_id, t(lang, "remind_habit", name=esc(habit["name"])),
                        parse_mode=ParseMode.HTML)
                    sent += 1
                except TelegramError as e:
                    log.warning("habit reminder to %s failed: %s", telegram_id, e)

            await asyncio.sleep(0.05)   # stay inside Telegram's rate limit

        if sent:
            log.info("reminders: %s sent", sent)


# ---------------------------------------------------------------------------
# Mini App authentication
# ---------------------------------------------------------------------------

def verify_init_data(init_data: str) -> dict:
    """Validate Telegram WebApp initData and return the embedded user.

    The Telegram id is taken from the signed payload only — a client-supplied
    id in the JSON body is never trusted.
    """
    if not init_data:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        received = parsed.pop("hash", "")
        if not received:
            raise ValueError("no hash")

        check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received):
            raise ValueError("bad signature")

        auth_date = int(parsed.get("auth_date", "0"))
        age = datetime.now().timestamp() - auth_date
        if age > INIT_DATA_MAX_AGE or age < -300:
            raise ValueError("stale")

        user = json.loads(parsed.get("user", "{}"))
        if not user.get("id"):
            raise ValueError("no user")
        return user
    except Exception as e:
        log.info("initData rejected: %s", e)
        raise HTTPException(status_code=401, detail="unauthorized")


#: Users whose cached membership answer expired; the bot re-verifies on next
#: contact rather than the API trusting a stale flag forever (audit 002).
_stale_membership: set[int] = set()


def auth(init_data: str | None, *, require_onboarded: bool = True) -> tuple[User, int]:
    """Resolve the caller to (user, workspace_id) and apply access policy.

    Three gates, in order:
      1. a valid Telegram signature (always);
      2. access — the free run, then channel membership. The rule is
         `dependencies.trial_state`, the same one the bot's `guard` applies;
      3. completed onboarding — status endpoints opt out via require_onboarded.

    The membership half is deliberately synchronous and cache-only here: an
    HTTP handler must not block on a Telegram round-trip, so a stale answer is
    flagged for the background re-check instead.
    """
    tg_user = verify_init_data(init_data or "")
    with SessionLocal() as s:
        user, _ = svc.get_or_create_user(
            s, int(tg_user["id"]),
            first_name=tg_user.get("first_name", ""),
            last_name=tg_user.get("last_name", ""),
            username=tg_user.get("username", ""))
        svc.touch_activity(s, user.telegram_id)
        s.commit()
        ws = svc.workspace_id_for(s, user.telegram_id)

        trial = deps.trial_state(user)
        if trial.gated:
            raise HTTPException(status_code=403, detail="subscription_required")
        if deps.REQUIRED_CHANNEL_ID and not trial.free \
                and not membership_is_fresh(user):
            _stale_membership.add(user.telegram_id)

        if require_onboarded and not user.onboarded:
            # A half-registered account must not create rows (audit 003).
            raise HTTPException(status_code=409, detail="onboarding_required")

        return user, ws


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------

telegram_app: Application | None = None
scheduler: AsyncIOScheduler | None = None

#: The screens reachable by command as well as by keyboard button. `/start`,
#: `/home` and `/guide` are registered separately because they are also the
#: entry points, and must work before onboarding finishes.
BOT_COMMANDS = [
    ("tasks", lambda u, c: show_tasks(u, c)),
    ("habits", lambda u, c: show_habits(u, c)),
    ("stats", lambda u, c: show_stats(u, c)),
    ("settings", lambda u, c: show_settings(u, c)),
]



@asynccontextmanager
async def lifespan(_: FastAPI):
    """Start the database, the Telegram bot and the scheduler together."""
    global telegram_app, scheduler

    db.init_db()
    log.info("database ready: %s", db.engine.url.render_as_string(hide_password=True))
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
        if WEBHOOK_URL:
            # One HTTP call per update instead of a permanent long-poll. Worth
            # it once there are enough users that polling is the process's main
            # activity, and required behind a load balancer, where several
            # instances cannot all poll the same bot.
            await telegram_app.bot.set_webhook(
                WEBHOOK_URL, allowed_updates=ALLOWED_UPDATES,
                secret_token=WEBHOOK_SECRET or None,
                drop_pending_updates=False)
            log.info("telegram bot on webhook: %s", WEBHOOK_URL)
        else:
            await telegram_app.updater.start_polling(
                allowed_updates=ALLOWED_UPDATES,
                # Dropping these loses whatever users tapped during a deploy
                # (audit 033). Handlers are idempotent, so replaying is safer.
                drop_pending_updates=False)
            log.info("telegram bot polling")

        scheduler = AsyncIOScheduler(timezone=svc.TZ)
        # Report times are a per-user setting now, so the jobs run on a short
        # cycle and each one decides, per user, whether their moment has come.
        # Delivering exactly once a day is still the outbox claim's job, which
        # means a frequent tick cannot produce a duplicate.
        scheduler.add_job(send_reports, "cron", minute=f"*/{REPORT_TICK_MINUTES}",
                          args=[telegram_app.bot, "morning"], id="morning",
                          max_instances=1, misfire_grace_time=600)
        scheduler.add_job(send_reports, "cron", minute=f"*/{REPORT_TICK_MINUTES}",
                          args=[telegram_app.bot, "evening"], id="evening",
                          max_instances=1, misfire_grace_time=600)
        scheduler.add_job(send_reminders, "cron",
                          minute=f"*/{svc.REMINDER_JOB_MINUTES}",
                          args=[telegram_app.bot], id="reminders",
                          max_instances=1, misfire_grace_time=120)
        # The channel post goes out in the morning, when the channel is read,
        # rather than at 23:00 when it was written for whoever was still up.
        # `svc.TZ` is the project clock, so 10:00 means 10:00 in Tashkent
        # regardless of where the process happens to run.
        scheduler.add_job(send_platform_stats, "cron",
                          hour=STATS_POST_HOUR, minute=0,
                          args=[telegram_app.bot], id="stats",
                          misfire_grace_time=3600)
        scheduler.start()
        log.info("scheduler started — reports every %s min, reminders every %s min",
                 REPORT_TICK_MINUTES, svc.REMINDER_JOB_MINUTES)
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

#: Requests larger than this are refused before parsing (audit 013).
MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", str(256 * 1024)))

#: Per-user token buckets. Reads are cheap, writes cost more, and exports hit
#: Telegram, so each class gets its own budget (audit 012).
RATE_LIMITS = {"read": (60, 60), "write": (30, 60), "heavy": (5, 60)}
#: The suite drives hundreds of writes as one user in a few seconds, which is
#: not the traffic this limit describes. Tests exercise it explicitly instead.
RATE_LIMIT_ENABLED = ENVIRONMENT != "test"
_buckets: dict[tuple[int, str], list[float]] = {}


def _rate_class(request: Request) -> str:
    path = request.url.path
    if path.startswith(("/api/stats/export", "/api/avatar")):
        return "heavy"
    return "read" if request.method == "GET" else "write"


def rate_limit_check(key: int, bucket: str) -> int | None:
    """Return seconds to wait when over budget, else None."""
    limit, window = RATE_LIMITS[bucket]
    now = time.monotonic()
    hits = _buckets.setdefault((key, bucket), [])
    cutoff = now - window
    hits[:] = [h for h in hits if h > cutoff]
    if len(hits) >= limit:
        return max(1, int(hits[0] + window - now))
    hits.append(now)
    return None


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
    if not key:
        host = request.client.host if request.client else "unknown"
        key = -(abs(hash(host)) % 10_000_000)

    bucket = _rate_class(request)
    retry_after = rate_limit_check(key, bucket) if RATE_LIMIT_ENABLED else None
    if retry_after is not None:
        log.info("rate limit hit: key=%s bucket=%s", key, bucket)
        return JSONResponse(status_code=429, content={"detail": "rate_limited"},
                            headers={"Retry-After": str(retry_after)})

    response = await call_next(request)

    # One place decides what spends a free action: a write that succeeded.
    # Counting in the middleware rather than in forty handlers is what stops
    # the definition from drifting endpoint by endpoint — and counting only on
    # a 2xx means a rejected body or a 404 never costs anybody anything.
    if (key > 0 and request.method in MUTATING_METHODS
            and 200 <= response.status_code < 300
            and request.url.path not in UNCOUNTED_PATHS):
        try:
            with SessionLocal() as s:
                svc.record_action(s, key)
                s.commit()
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


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    deadline: str | None = Field(default=None, max_length=10)


class FocusIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    priority: str | None = Field(default=None, max_length=6)


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


@app.get("/health/ready")
def health_ready():
    """Readiness: can this instance actually serve?

    Checks the database, the schema and the bot worker, because a process that
    answers 200 while the database is unreachable is worse than one that
    admits it (audit 087).
    """
    from sqlalchemy import inspect, text as sql_text

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
            "has_phone": bool(user.phone_number),
            "prefs": svc.prefs_for(user),
            "timezones": svc.TIMEZONES,
            "onboarded": user.onboarded, "is_subscribed": user.is_subscribed}


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


@app.post("/webhook", include_in_schema=False)
async def telegram_webhook(request: Request):
    """Telegram's delivery endpoint, live only when WEBHOOK_URL is set.

    Refused outright when the bot is polling, so a stale webhook left over from
    an earlier deploy cannot inject updates into an instance that is also
    long-polling — that combination delivers everything twice.
    """
    if not WEBHOOK_URL or telegram_app is None:
        raise HTTPException(status_code=404, detail="not_found")
    if WEBHOOK_SECRET and request.headers.get(
            "x-telegram-bot-api-secret-token") != WEBHOOK_SECRET:
        # Wrong secret is somebody who found the path, not Telegram.
        raise HTTPException(status_code=403, detail="forbidden")
    try:
        update = Update.de_json(await request.json(), telegram_app.bot)
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
    telegram_id = int(tg_user["id"])

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
    with SessionLocal() as s:
        return svc.home(s, ws, s.get(User, user.telegram_id))


@app.get("/api/habits")
def api_habits(day: str | None = None, init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    tz = svc.user_tz(user)
    with SessionLocal() as s:
        target = _date(day)
        return {"habits": svc.list_habits(s, ws, target, tz=tz),
                "grouped": svc.habits_by_category(s, ws, target, tz=tz),
                "categories": svc.HABIT_CATEGORIES,
                "wake": svc.wake_state(s, ws, tz=tz),
                "streak": svc.habit_streak(s, ws, tz=tz)}


@app.post("/api/habits")
def api_habit_add(body: HabitIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        habit = svc.add_habit(s, ws, body.name, body.category,
                              schedule=body.schedule,
                              remind_at=_time(body.remind_at))
    return {"ok": True, "id": habit.id}


class HabitPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=16)
    schedule: str | None = Field(default=None, max_length=24)
    remind_at: str | None = Field(default=None, max_length=5)
    target_time: str | None = Field(default=None, max_length=5)


class HabitPauseIn(BaseModel):
    paused: bool


@app.post("/api/habits/{habit_id}/pause")
def api_habit_pause(habit_id: int, body: HabitPauseIn,
                    init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Pause or resume a habit. Every past log survives either way."""
    _, ws = auth(init)
    with SessionLocal() as s:
        habit = svc.set_habit_paused(s, ws, habit_id, body.paused)
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
        except ValueError:
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
    _, ws = auth(init)
    with SessionLocal() as s:
        try:
            svc.delete_habit(s, ws, habit_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="protected_habit")
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
    with SessionLocal() as s:
        return svc.list_tasks(s, ws, horizon_days=max(0, min(days, 365)),
                              search=q[:100], project_id=project_id,
                              priority=priority, tz=svc.user_tz(user))


@app.post("/api/tasks")
def api_task_add(body: TaskIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        task = svc.add_task(s, ws, body.title, deadline=_date(body.deadline),
                            project_id=body.project_id, priority=body.priority,
                            description=body.description,
                            due_time=_time(body.due_time),
                            remind_before=body.remind_before,
                            recurrence=body.recurrence)
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
        svc.update_task(s, ws, task_id, **fields)
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
                                priority=body.priority or svc.DEFAULT_MISSION_PRIORITY)
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


@app.get("/api/avatar")
async def api_avatar(tgdata: str | None = None,
                     init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Stream the user's profile photo.

    A browser `<img src=...>` cannot attach a header, so the same signed
    initData may arrive as `?tgdata=` instead (audit 061). It is the identical
    credential — signature and freshness are checked the same way.
    """
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


@app.post("/api/wakeup")
def api_wakeup(init=Header(default=None, alias="X-Telegram-Init-Data")):
    """The Mini App's own "Turdim" button.

    Identical to the bot's, through the same service function, so the button on
    Home is the real thing rather than an instruction to go and use the chat.
    """
    user, ws = auth(init)
    tz = svc.user_tz(user)
    with SessionLocal() as s:
        result = svc.mark_wakeup(s, ws, tz=tz)
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
