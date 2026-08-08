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
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, time as dtime, timedelta
from urllib.parse import parse_qsl

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
    ReplyKeyboardRemove, Update, WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, ChatMemberHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)

import db
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
REQUIRED_CHANNEL_ID = os.environ.get("REQUIRED_CHANNEL_ID", "").strip()
REQUIRED_CHANNEL_URL = os.environ.get("REQUIRED_CHANNEL_URL", "").strip()
ADMIN_LOG_CHANNEL_ID = os.environ.get("ADMIN_LOG_CHANNEL_ID", "").strip()
#: Suggestions and complaints get their own channel, apart from event logs.
FEEDBACK_CHANNEL_ID = os.environ.get("FEEDBACK_CHANNEL_ID", "").strip() or ADMIN_LOG_CHANNEL_ID
#: Aggregate platform statistics — counts only, never user content.
STATS_CHANNEL_ID = os.environ.get("STATS_CHANNEL_ID", "").strip() or ADMIN_LOG_CHANNEL_ID
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip().rstrip("/")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development").lower()

#: Telegram initData older than this is rejected, so a captured URL cannot be
#: replayed days later.
INIT_DATA_MAX_AGE = int(os.environ.get("INIT_DATA_MAX_AGE", "86400"))

if ENVIRONMENT == "production" and not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required in production")

# ---------------------------------------------------------------------------
# Translations — every user-visible string lives here, never inline
# ---------------------------------------------------------------------------

T: dict[str, dict[str, str]] = {
    "uz": {
        "menu_wake": "☀️ Turdim",
        "wake_ok": "Xayrli tong! Uyg'onish belgilandi ✓ ({now})",
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
        "btn_phone_set": "📱 Telefon raqam",
        "btn_photo": "🖼 Profil rasmi",
        "ask_photo": "Profil rasmini foto sifatida yuboring:",
        "photo_saved": "Rasm saqlandi ✓",
        "photo_removed": "Rasm o'chirildi",
        "btn_photo_del": "🗑 Rasmni o'chirish",
        "btn_phone_del": "🗑 Raqamni o'chirish",
        "phone_removed": "Raqam o'chirildi",
        "streak": "🔥 Ketma-ket",
        "welcome": "Assalomu alaykum, {name}!\n\nErnestOS — Telegram ichidagi shaxsiy tizimingiz.",
        "ask_phone": "Telefon raqamingizni ulashing.\n\nBu hisobingizni tiklash va sizni tanib olish uchun kerak.",
        "btn_phone": "📱 Raqamni ulashish",
        "btn_skip": "⏭ O'tkazib yuborish",
        "phone_saved": "Raqam saqlandi ✓",
        "phone_wrong": "Bu sizning raqamingiz emas. O'z kontaktingizni yuboring.",
        "phone_skipped": "Yaxshi, keyinroq qo'shasiz.",
        "ask_lang": "Tilni tanlang:",
        "ask_gender": "Jinsingiz:",
        "male": "👨 Erkak", "female": "👩 Ayol",
        "sub_required": "ErnestOS'dan foydalanish uchun kanalimizga obuna bo'ling.",
        "btn_join": "📢 Kanalga qo'shilish",
        "btn_check": "✅ Tekshirish",
        "sub_missing": "Hali obuna bo'lmadingiz. Kanalga qo'shilib, qayta tekshiring.",
        "sub_lost": "Siz ErnestOS kanalidan chiqdingiz. Davom etish uchun kanalga qayta qo'shiling.",
        "sub_restored": "Xush kelibsiz! ErnestOS yana ochiq.",
        "onboard_done": "Tayyor! ErnestOS ishga tushdi.",
        "menu_home": "🏠 Home", "menu_habits": "✅ Odatlar",
        "menu_tasks": "⚡ Vazifalar", "menu_goals": "🎯 Maqsadlar",
        "menu_settings": "⚙️ Sozlamalar", "menu_feedback": "💬 Taklif",
        "menu_app": "🚀 ErnestOS",
        "home_title": "🏠 ErnestOS",
        "home_habits": "✅ Odatlar", "home_tasks": "⚡ Bugungi vazifalar",
        "home_focus": "🎯 Shu hafta", "home_projects": "📁 Loyihalar",
        "home_goals": "🎯 Maqsadlar", "home_bday": "🎂 Tug'ilgan kunlar",
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
        "goals_title": "🎯 Maqsadlar",
        "cat_ultimate": "👑 ULTIMATE", "cat_milestone": "🏆 MILESTONE",
        "cat_tactical": "⚡ TACTICAL",
        "btn_add_goal": "➕ Maqsad", "btn_done_goal": "✅ Done qilish",
        "btn_del_goal": "🗑 Maqsad o'chirish",
        "ask_goal_title": "Maqsad nomini yozing:",
        "ask_goal_cat": "Qaysi darajaga tegishli?",
        "goal_added": "Maqsad qo'shildi: {title}",
        "goal_done": "Bajarildi 🎉 {title}",
        "goal_deleted": "O'chirildi: {title}",
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
        "evening_title": "🌙 21:00 ErnestOS hisoboti",
        "r_habits": "✅ Odatlar", "r_prayer": "🕌 Namoz", "r_tasks": "⚡ Vazifalar",
        "r_completed": "bajarildi", "r_remaining": "qoldi",
        "r_journal": "📓 Kundalik", "r_yes": "yozilgan", "r_no": "yozilmagan",
        "r_unfinished": "❗ Tugallanmagan:",
        "r_focus": "🎯 Haftalik maqsadlar",
        "days_short": "kun",
    },
    "en": {
        "menu_wake": "☀️ I'm up",
        "wake_ok": "Good morning! Wake-up recorded ✓ ({now})",
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
        "btn_phone_set": "📱 Phone number",
        "btn_photo": "🖼 Profile photo",
        "ask_photo": "Send your profile photo:",
        "photo_saved": "Photo saved ✓",
        "photo_removed": "Photo removed",
        "btn_photo_del": "🗑 Remove photo",
        "btn_phone_del": "🗑 Remove phone",
        "phone_removed": "Phone removed",
        "streak": "🔥 Streak",
        "welcome": "Hello, {name}!\n\nErnestOS — your personal system inside Telegram.",
        "ask_phone": "Please share your phone number.\n\nIt is used to recover your account and recognise you.",
        "btn_phone": "📱 Share phone number",
        "btn_skip": "⏭ Skip",
        "phone_saved": "Phone saved ✓",
        "phone_wrong": "That is not your own contact. Please share your own number.",
        "phone_skipped": "No problem, you can add it later.",
        "ask_lang": "Choose your language:",
        "ask_gender": "Your gender:",
        "male": "👨 Male", "female": "👩 Female",
        "sub_required": "Join our channel to use ErnestOS.",
        "btn_join": "📢 Join channel",
        "btn_check": "✅ Check",
        "sub_missing": "You are not subscribed yet. Join the channel and check again.",
        "sub_lost": "You left the required ErnestOS channel. Join the channel to continue using ErnestOS.",
        "sub_restored": "Welcome back! ErnestOS is open again.",
        "onboard_done": "All set! ErnestOS is ready.",
        "menu_home": "🏠 Home", "menu_habits": "✅ Habits",
        "menu_tasks": "⚡ Tasks", "menu_goals": "🎯 Goals",
        "menu_settings": "⚙️ Settings", "menu_feedback": "💬 Feedback",
        "menu_app": "🚀 ErnestOS",
        "home_title": "🏠 ErnestOS",
        "home_habits": "✅ Habits", "home_tasks": "⚡ Today's tasks",
        "home_focus": "🎯 This week", "home_projects": "📁 Projects",
        "home_goals": "🎯 Goals", "home_bday": "🎂 Birthdays",
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
        "goals_title": "🎯 Goals",
        "cat_ultimate": "👑 ULTIMATE", "cat_milestone": "🏆 MILESTONE",
        "cat_tactical": "⚡ TACTICAL",
        "btn_add_goal": "➕ Goal", "btn_done_goal": "✅ Complete",
        "btn_del_goal": "🗑 Delete goal",
        "ask_goal_title": "Enter goal title:",
        "ask_goal_cat": "Which level?",
        "goal_added": "Goal added: {title}",
        "goal_done": "Completed 🎉 {title}",
        "goal_deleted": "Deleted: {title}",
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
        "evening_title": "🌙 21:00 ErnestOS report",
        "r_habits": "✅ Habits", "r_prayer": "🕌 Prayer", "r_tasks": "⚡ Tasks",
        "r_completed": "completed", "r_remaining": "remaining",
        "r_journal": "📓 Journal", "r_yes": "written", "r_no": "not written",
        "r_unfinished": "❗ Still unfinished:",
        "r_focus": "🎯 Weekly missions",
        "days_short": "days",
    },
    "ru": {
        "menu_wake": "☀️ Я встал",
        "wake_ok": "Доброе утро! Подъём засчитан ✓ ({now})",
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
        "btn_phone_set": "📱 Номер телефона",
        "btn_photo": "🖼 Фото профиля",
        "ask_photo": "Отправьте фото профиля:",
        "photo_saved": "Фото сохранено ✓",
        "photo_removed": "Фото удалено",
        "btn_photo_del": "🗑 Удалить фото",
        "btn_phone_del": "🗑 Удалить номер",
        "phone_removed": "Номер удалён",
        "streak": "🔥 Серия",
        "welcome": "Здравствуйте, {name}!\n\nErnestOS — ваша личная система внутри Telegram.",
        "ask_phone": "Поделитесь номером телефона.\n\nЭто нужно для восстановления аккаунта.",
        "btn_phone": "📱 Отправить номер",
        "btn_skip": "⏭ Пропустить",
        "phone_saved": "Номер сохранён ✓",
        "phone_wrong": "Это не ваш контакт. Отправьте свой номер.",
        "phone_skipped": "Хорошо, добавите позже.",
        "ask_lang": "Выберите язык:",
        "ask_gender": "Ваш пол:",
        "male": "👨 Мужской", "female": "👩 Женский",
        "sub_required": "Подпишитесь на наш канал, чтобы пользоваться ErnestOS.",
        "btn_join": "📢 Подписаться",
        "btn_check": "✅ Проверить",
        "sub_missing": "Вы ещё не подписаны. Подпишитесь и проверьте снова.",
        "sub_lost": "Вы вышли из канала ErnestOS. Подпишитесь снова, чтобы продолжить.",
        "sub_restored": "С возвращением! ErnestOS снова доступен.",
        "onboard_done": "Готово! ErnestOS запущен.",
        "menu_home": "🏠 Главная", "menu_habits": "✅ Привычки",
        "menu_tasks": "⚡ Задачи", "menu_goals": "🎯 Цели",
        "menu_settings": "⚙️ Настройки", "menu_feedback": "💬 Отзыв",
        "menu_app": "🚀 ErnestOS",
        "home_title": "🏠 ErnestOS",
        "home_habits": "✅ Привычки", "home_tasks": "⚡ Задачи на сегодня",
        "home_focus": "🎯 На этой неделе", "home_projects": "📁 Проекты",
        "home_goals": "🎯 Цели", "home_bday": "🎂 Дни рождения",
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
        "goals_title": "🎯 Цели",
        "cat_ultimate": "👑 ULTIMATE", "cat_milestone": "🏆 MILESTONE",
        "cat_tactical": "⚡ TACTICAL",
        "btn_add_goal": "➕ Цель", "btn_done_goal": "✅ Выполнено",
        "btn_del_goal": "🗑 Удалить цель",
        "ask_goal_title": "Введите название цели:",
        "ask_goal_cat": "Какой уровень?",
        "goal_added": "Цель добавлена: {title}",
        "goal_done": "Выполнено 🎉 {title}",
        "goal_deleted": "Удалено: {title}",
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
        "evening_title": "🌙 21:00 отчёт ErnestOS",
        "r_habits": "✅ Привычки", "r_prayer": "🕌 Намаз", "r_tasks": "⚡ Задачи",
        "r_completed": "выполнено", "r_remaining": "осталось",
        "r_journal": "📓 Дневник", "r_yes": "заполнен", "r_no": "не заполнен",
        "r_unfinished": "❗ Не завершено:",
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
    name = " ".join(x for x in (user.first_name, user.last_name) if x) or "—"
    username = f"@{user.username}" if user.username else "—"
    return (f"No: <b>#{user.member_no}</b>\n"
            f"ID: <code>{user.telegram_id}</code>\n"
            f"Name: {name}\nUsername: {username}")


async def log_event(bot, user: User, event: str, detail: str = "") -> None:
    body = f"<b>{event}</b>\n{_who(user)}"
    if detail:
        body += f"\n{detail}"
    await admin_log(bot, body)


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------

MEMBER_STATES = {"member", "administrator", "creator"}


async def is_subscribed(bot, telegram_id: int) -> bool | None:
    """True / False / None when Telegram could not be asked.

    None is treated as "keep the previous state" so a Telegram outage never
    deletes access or data.
    """
    if not REQUIRED_CHANNEL_ID:
        return True
    try:
        member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL_ID,
                                           user_id=telegram_id)
        return member.status in MEMBER_STATES
    except TelegramError as e:
        log.warning("subscription check failed for %s: %s", telegram_id, e)
        return None


def subscribe_keyboard(lang: str) -> InlineKeyboardMarkup:
    rows = []
    if REQUIRED_CHANNEL_URL:
        rows.append([InlineKeyboardButton(t(lang, "btn_join"), url=REQUIRED_CHANNEL_URL)])
    rows.append([InlineKeyboardButton(t(lang, "btn_check"), callback_data="sub:check")])
    return InlineKeyboardMarkup(rows)


async def guard(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> tuple[User, int] | None:
    """Every protected action starts here.

    Returns (user, workspace_id) when the caller may proceed, otherwise sends
    the appropriate prompt and returns None.
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

    state = await is_subscribed(ctx.bot, tg_user.id)
    if state is False:
        with SessionLocal() as s:
            if svc.set_subscription(s, tg_user.id, False):
                s.commit()
        target = update.effective_message
        if target:
            await target.reply_text(t(lang, "sub_lost"),
                                    reply_markup=subscribe_keyboard(lang))
        return None
    if state is True:
        with SessionLocal() as s:
            if svc.set_subscription(s, tg_user.id, True):
                s.commit()

    with SessionLocal() as s:
        user = s.get(User, tg_user.id)
        return user, ws


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def main_menu(lang: str) -> ReplyKeyboardMarkup:
    rows = [
        [t(lang, "menu_wake"), t(lang, "menu_home")],
        [t(lang, "menu_habits"), t(lang, "menu_tasks")],
        [t(lang, "menu_goals"), t(lang, "menu_settings")],
        [t(lang, "menu_feedback")],
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
        await message.reply_text(t(lang, "welcome", name=tg_user.first_name or ""),
                                 reply_markup=main_menu(lang))
        await show_home(update, ctx)
        return

    # A brand-new user has not chosen a language yet, so greet in all three
    # rather than guessing which one to use.
    name = tg_user.first_name or ""
    await message.reply_text(
        "\n\n".join(t(code, "welcome", name=name) for code in ("uz", "en", "ru")))
    await resume_onboarding(update, ctx, step)


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

    if step == "language":
        await message.reply_text(t(lang, "ask_lang"), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🇺🇿 O'zbek", callback_data="lang:uz")],
            [InlineKeyboardButton("🇬🇧 English", callback_data="lang:en")],
            [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru")],
        ]))

    elif step == "phone":
        keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton(t(lang, "btn_phone"), request_contact=True)],
             [KeyboardButton(t(lang, "btn_skip"))]],
            resize_keyboard=True, one_time_keyboard=True)
        await message.reply_text(t(lang, "ask_phone"), reply_markup=keyboard)

    elif step == "gender":
        await message.reply_text(t(lang, "ask_gender"), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(t(lang, "male"), callback_data="gender:male")],
            [InlineKeyboardButton(t(lang, "female"), callback_data="gender:female")],
        ]))

    elif step == "subscribe":
        await message.reply_text(t(lang, "sub_required"),
                                 reply_markup=subscribe_keyboard(lang))

    else:
        await finish_onboarding(update, ctx)


async def on_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Accept a shared contact only when it belongs to the sender."""
    message = update.effective_message
    tg_user = update.effective_user
    if message is None or message.contact is None or tg_user is None:
        return

    contact = message.contact
    with SessionLocal() as s:
        user = s.get(User, tg_user.id)
        if user is None:
            return
        lang = user.language
        if contact.user_id != tg_user.id:
            # Someone forwarded another person's card — never store it.
            await message.reply_text(t(lang, "phone_wrong"))
            return
        user.phone_number = contact.phone_number
        onboarded_already = user.onboarded
        if not onboarded_already:
            user.onboarding_step = "gender"
        s.commit()
        snapshot = user

    await message.reply_text(t(lang, "phone_saved"), reply_markup=ReplyKeyboardRemove())
    await log_event(ctx.bot, snapshot, "📱 PHONE SUBMITTED",
                    f"Phone: {snapshot.phone_number}")
    if onboarded_already:
        # Changed from Settings — go straight back to the menu.
        await message.reply_text(t(lang, "saved"), reply_markup=main_menu(lang))
    else:
        await resume_onboarding(update, ctx, "gender")


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


async def skip_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    message = update.effective_message
    if tg_user is None or message is None:
        return
    with SessionLocal() as s:
        user = s.get(User, tg_user.id)
        if user is None:
            return
        lang = user.language
        user.onboarding_step = "gender"
        s.commit()
    await message.reply_text(t(lang, "phone_skipped"),
                             reply_markup=ReplyKeyboardRemove())
    await resume_onboarding(update, ctx, "gender")


async def finish_onboarding(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    message = update.effective_message
    if tg_user is None or message is None:
        return

    state = await is_subscribed(ctx.bot, tg_user.id)
    with SessionLocal() as s:
        user = s.get(User, tg_user.id)
        if user is None:
            return
        lang = user.language
        if state is False:
            user.onboarding_step = "subscribe"
            s.commit()
            await message.reply_text(t(lang, "sub_missing"),
                                     reply_markup=subscribe_keyboard(lang))
            return
        user.is_subscribed = state is not False
        user.onboarding_step = "done"
        user.onboarded = True
        s.commit()
        snapshot = user

    await message.reply_text(t(lang, "onboard_done"), reply_markup=main_menu(lang))
    await log_event(ctx.bot, snapshot, "✅ ONBOARDING COMPLETE",
                    f"Language: {snapshot.language}\nGender: {snapshot.gender or '—'}\n"
                    f"Phone: {snapshot.phone_number or '—'}")
    await show_home(update, ctx)


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

def render_home(data: dict, lang: str) -> str:
    lines = [f"<b>{t(lang, 'home_title')}</b>"]
    if data["quote"]:
        lines.append(f"<i>{data['quote']}</i>")
    lines.append(f"📅 {data['date']}")

    focus = data["focus"]
    if focus:
        lines.append(f"\n<b>{t(lang, 'home_focus')}</b>")
        for f in focus:
            lines.append(f"{'✅' if f['done'] else '▫️'} {f['title']}")

    tasks = data["tasks"]["today"]
    lines.append(f"\n<b>{t(lang, 'home_tasks')}</b>")
    if tasks:
        marks = {"high": "🔴", "medium": "🟡", "low": "🟢"}
        for task in tasks[:6]:
            project = f" — {task['project']}" if task["project"] else ""
            lines.append(f"{marks.get(task['priority'], '▫️')} {task['title']}{project}")
    else:
        lines.append(t(lang, "none"))

    overdue = data["tasks"]["overdue"]
    if overdue:
        lines.append(f"\n<b>{t(lang, 'home_overdue')}</b>")
        for task in overdue[:4]:
            lines.append(f"• {task['title']} ({task['deadline']})")

    habits = data["habits"]
    lines.append(f"\n<b>{t(lang, 'home_habits')}</b>  "
                 f"{habits['done']} / {habits['total']}")

    prayer = data["prayer"]
    lines.append(f"🕌 {prayer['score']} / {prayer['max']}")

    streaks = data.get("streaks") or {}
    if streaks.get("habits") or streaks.get("prayer"):
        lines.append(f"{t(lang, 'streak')}: "
                     f"{streaks.get('habits', 0)} · 🕌 {streaks.get('prayer', 0)}")

    projects = data["projects"]
    if projects:
        lines.append(f"\n<b>{t(lang, 'home_projects')}</b>")
        for p in projects[:4]:
            lines.append(f"• {p['name']} — {p['progress']}%")

    goals = data["goals"]
    if any(goals.values()):
        lines.append(f"\n<b>{t(lang, 'home_goals')}</b>")
        lines.append(f"👑 {goals.get('ultimate', 0)}  "
                     f"🏆 {goals.get('milestone', 0)}  "
                     f"⚡ {goals.get('tactical', 0)}")

    birthdays = data["birthdays"]
    if birthdays:
        lines.append(f"\n<b>{t(lang, 'home_bday')}</b>")
        for b in birthdays[:3]:
            when = "🎉" if b["days_left"] == 0 else f"{b['days_left']} {t(lang, 'days_short')}"
            lines.append(f"• {b['person_name']} — {when}")

    return "\n".join(lines)


async def handle_wakeup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """The user reports getting up. Only counts before target time + 1 hour."""
    got = await guard(update, ctx)
    if got is None:
        return
    user, ws = got
    with SessionLocal() as s:
        result = svc.mark_wakeup(s, ws)

    message = update.effective_message
    if message is None:
        return
    if result["done"]:
        await message.reply_text(t(user.language, "wake_ok", now=result["now"]))
        await log_event(ctx.bot, user, "☀️ WAKE-UP", f"At: {result['now']}")
    else:
        await message.reply_text(
            t(user.language, "wake_late", deadline=result["deadline"]))


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
            mark = "✅" if h["done"] else "⬜"
            lock = " 🔒" if h["protected"] else ""
            clock = f" ⏰{h['target_time']}" if h.get("target_time") else ""
            rows.append([InlineKeyboardButton(
                f"{mark} {h['name']}{clock}{lock}",
                callback_data=f"habit:toggle:{h['id']}")])
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
    with SessionLocal() as s:
        grouped = svc.habits_by_category(s, ws)
        streak = svc.habit_streak(s, ws)

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

def render_tasks(data: dict, lang: str) -> str:
    marks = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    lines = []

    if data["overdue"]:
        lines.append(f"<b>{t(lang, 'tasks_overdue')}</b>")
        for task in data["overdue"]:
            project = task["project"] or t(lang, "standalone")
            lines.append(f"{marks.get(task['priority'], '▫️')} {task['title']}\n"
                         f"   📁 {project} · 📅 {task['deadline']}")
        lines.append("")

    lines.append(f"<b>{t(lang, 'tasks_title')}</b>")
    upcoming = data["upcoming"]
    if upcoming:
        for task in upcoming:
            project = task["project"] or t(lang, "standalone")
            lines.append(f"{marks.get(task['priority'], '▫️')} {task['title']}\n"
                         f"   📁 {project} · 📅 {task['deadline']}")
    else:
        lines.append(t(lang, "none"))

    if data["undated"]:
        lines.append("")
        for task in data["undated"][:5]:
            lines.append(f"▫️ {task['title']}")

    return "\n".join(lines)


def tasks_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Completing replaces deleting: finished work moves to the Done archive
    instead of disappearing from history."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_done_task"), callback_data="task:donelist"),
         InlineKeyboardButton(t(lang, "btn_edit_task"), callback_data="task:editlist")],
        [InlineKeyboardButton(t(lang, "btn_add_task"), callback_data="task:add")],
        [InlineKeyboardButton(t(lang, "btn_add_project"), callback_data="project:add"),
         InlineKeyboardButton(t(lang, "btn_del_project"), callback_data="project:dellist")],
    ])


async def show_tasks(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                     edit: bool = False) -> None:
    got = await guard(update, ctx)
    if got is None:
        return
    user, ws = got
    with SessionLocal() as s:
        data = svc.list_tasks(s, ws, horizon_days=7)

    text = render_tasks(data, user.language)
    markup = tasks_keyboard(user.language)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup)
    elif update.effective_message:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup)


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

def render_goals(grouped: dict, lang: str) -> str:
    lines = [f"<b>{t(lang, 'goals_title')}</b>"]
    icons = {"ultimate": "cat_ultimate", "milestone": "cat_milestone",
             "tactical": "cat_tactical"}
    empty = True
    for category in svc.CATEGORIES:
        goals = grouped.get(category, [])
        if not goals:
            continue
        empty = False
        lines.append(f"\n<b>{t(lang, icons[category])}</b>")
        for g in goals:
            mark = "✅" if g["status"] == "completed" else "•"
            lines.append(f"{mark} {g['title']}")
    if empty:
        lines.append(f"\n{t(lang, 'empty')}")
    return "\n".join(lines)


def goals_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_add_goal"), callback_data="goal:add")],
        [InlineKeyboardButton(t(lang, "btn_done_goal"), callback_data="goal:donelist"),
         InlineKeyboardButton(t(lang, "btn_del_goal"), callback_data="goal:dellist")],
    ])


async def show_goals(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                     edit: bool = False) -> None:
    got = await guard(update, ctx)
    if got is None:
        return
    user, ws = got
    with SessionLocal() as s:
        grouped = svc.list_goals(s, ws)

    text = render_goals(grouped, user.language)
    markup = goals_keyboard(user.language)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup)
    elif update.effective_message:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

THEMES = ["ocean", "emerald", "obsidian", "rose", "pink", "aurora"]


async def show_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                        edit: bool = False) -> None:
    got = await guard(update, ctx)
    if got is None:
        return
    user, _ = got
    lang = user.language
    text = (f"<b>{t(lang, 'settings_title')}</b>\n\n"
            f"🌐 {lang}\n👤 {user.gender or '—'}\n🎨 {user.theme}\n"
            f"📱 {user.phone_number or '—'}\n"
            f"🖼 {'✓' if user.photo_file_id else '—'}")
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "btn_lang"), callback_data="set:lang")],
        [InlineKeyboardButton(t(lang, "btn_gender"), callback_data="set:gender")],
        [InlineKeyboardButton(t(lang, "btn_theme"), callback_data="set:theme")],
        [InlineKeyboardButton(t(lang, "btn_phone_set"), callback_data="set:phone")],
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

    # Skip button during onboarding
    if not onboarded:
        if text == t(lang, "btn_skip"):
            await skip_phone(update, ctx)
        return

    flow = ctx.user_data.get("flow")
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
        if text == t(code, "menu_goals"):
            return await show_goals(update, ctx)
        if text == t(code, "menu_settings"):
            return await show_settings(update, ctx)
        if text == t(code, "menu_feedback"):
            ctx.user_data["flow"] = {"name": "feedback"}
            return await message.reply_text(t(lang, "ask_feedback"),
                                            reply_markup=cancel_keyboard(lang))
        if text == t(code, "menu_app"):
            markup = webapp_button(lang)
            if markup:
                return await message.reply_text(t(lang, "open_app"), reply_markup=markup)
            return

    await show_home(update, ctx)


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
            ctx.user_data["flow"] = {"name": "habit_cat", "title": text}
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
                task = svc.update_task(s, ws, flow["id"], title=text)
            ctx.user_data.pop("flow", None)
            await message.reply_text(t(lang, "task_updated", title=task.title))
            await show_tasks(update, ctx)

        elif name == "task_title":
            ctx.user_data["flow"] = {"name": "task_days", "title": text}
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
            await log_event(ctx.bot, user, "📁 PROJECT ADDED", f"Project: {project.name}")
            await show_tasks(update, ctx)

        elif name == "goal_title":
            ctx.user_data["flow"] = {"name": "goal_cat", "title": text}
            await message.reply_text(t(lang, "ask_goal_cat"),
                                     reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(t(lang, "cat_ultimate"), callback_data="goalcat:ultimate")],
                [InlineKeyboardButton(t(lang, "cat_milestone"), callback_data="goalcat:milestone")],
                [InlineKeyboardButton(t(lang, "cat_tactical"), callback_data="goalcat:tactical")],
                [InlineKeyboardButton(t(lang, "cancel"), callback_data="flow:cancel")],
            ]))

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
                              f"Phone: {user.phone_number or '—'}\n"
                              f"Date: {datetime.now(svc.TZ):%Y-%m-%d %H:%M}\n\n"
                              f"{text}"),
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

    ctx.user_data["flow"] = {"name": "task_project", "title": title,
                             "deadline": deadline.isoformat()}
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
            if state is False:
                await query.edit_message_text(t(lang, "sub_missing"),
                                              reply_markup=subscribe_keyboard(lang))
                return
            changed = svc.set_subscription(s, tg_user.id, True)
            if not user.onboarded:
                user.onboarding_step = "done"
                user.onboarded = True
            s.commit()
            snapshot = user
        if changed:
            await log_event(ctx.bot, snapshot, "🔓 SUBSCRIPTION RESTORED")
        await query.edit_message_text(t(lang, "sub_restored"))
        await update.effective_message.reply_text(t(lang, "onboard_done"),
                                                  reply_markup=main_menu(lang))
        await show_home(update, ctx)
        return

    if action == "lang":
        tg_user = update.effective_user
        with SessionLocal() as s:
            user = s.get(User, tg_user.id)
            user.language = parts[1]
            if not user.onboarded:
                user.onboarding_step = "phone"
            s.commit()
            lang, onboarded = user.language, user.onboarded
            snapshot = user
        await query.edit_message_text(t(lang, "saved"))
        await log_event(ctx.bot, snapshot, "🌐 LANGUAGE CHANGED", f"Language: {lang}")
        if not onboarded:
            await resume_onboarding(update, ctx, "phone")
        else:
            await update.effective_message.reply_text(t(lang, "saved"),
                                                      reply_markup=main_menu(lang))
        return

    if action == "gender":
        tg_user = update.effective_user
        with SessionLocal() as s:
            user = s.get(User, tg_user.id)
            user.gender = parts[1]
            if not user.onboarded:
                user.onboarding_step = "subscribe"
            s.commit()
            lang, onboarded = user.language, user.onboarded
            snapshot = user
        await query.edit_message_text(t(lang, "saved"))
        await log_event(ctx.bot, snapshot, "👤 GENDER CHANGED", f"Gender: {parts[1]}")
        if not onboarded:
            await finish_onboarding(update, ctx)
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
            ctx.user_data["flow"] = {"name": "habit_name"}
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
            await log_event(ctx.bot, user, "🗑 HABIT DELETED", f"Habit: {name}")
            await show_habits(update, ctx, edit=True)
        elif sub == "back":
            await show_habits(update, ctx, edit=True)
        elif sub == "noop":
            pass

    # --- tasks ---
    elif action == "task":
        sub = parts[1]
        if sub == "add":
            ctx.user_data["flow"] = {"name": "task_title"}
            await message.reply_text(t(lang, "ask_task_name"),
                                     reply_markup=cancel_keyboard(lang))
        elif sub in ("donelist", "editlist"):
            with SessionLocal() as s:
                data = svc.list_tasks(s, ws, horizon_days=365)
            tasks = data["overdue"] + data["upcoming"] + data["undated"]
            if not tasks:
                await query.answer(t(lang, "empty"), show_alert=True)
                return
            verb = "done" if sub == "donelist" else "edit"
            prompt = "choose_done" if sub == "donelist" else "choose_edit"
            rows = [[InlineKeyboardButton(task["title"][:40],
                                          callback_data=f"task:{verb}:{task['id']}")]
                    for task in tasks[:15]]
            rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="task:back")])
            await query.edit_message_text(t(lang, prompt),
                                          reply_markup=InlineKeyboardMarkup(rows))
        elif sub == "done":
            with SessionLocal() as s:
                task = svc.complete_task(s, ws, int(parts[2]))
            await log_event(ctx.bot, user, "✅ TASK COMPLETED", f"Task: {task.title}")
            await query.edit_message_text(t(lang, "task_done", title=task.title))
            await show_tasks(update, ctx)
        elif sub == "edit":
            ctx.user_data["flow"] = {"name": "task_edit", "id": int(parts[2])}
            await query.edit_message_text(t(lang, "ask_new_title"))
        elif sub == "back":
            await show_tasks(update, ctx, edit=True)
        elif sub == "noop":
            pass

    elif action == "taskday":
        flow = ctx.user_data.get("flow") or {}
        title = flow.get("title")
        if not title:
            await query.answer(t(lang, "error"), show_alert=True)
            return
        if parts[1] == "custom":
            ctx.user_data["flow"] = {"name": "task_custom_days", "title": title}
            await query.edit_message_text(t(lang, "ask_custom_days"))
            return
        deadline = svc.today_local() + timedelta(days=int(parts[1]))
        await query.edit_message_text(f"📅 {deadline.isoformat()}")
        await ask_task_project(update, ctx, title, deadline)

    elif action == "taskproj":
        flow = ctx.user_data.get("flow") or {}
        title, deadline = flow.get("title"), flow.get("deadline")
        if not title:
            await query.answer(t(lang, "error"), show_alert=True)
            return
        project_id = int(parts[1]) or None
        with SessionLocal() as s:
            task = svc.add_task(s, ws, title,
                                deadline=date.fromisoformat(deadline) if deadline else None,
                                project_id=project_id)
        ctx.user_data.pop("flow", None)
        await query.edit_message_text(t(lang, "task_added", title=task.title))
        await log_event(ctx.bot, user, "⚡ TASK ADDED",
                        f"Task: {task.title}\nDeadline: {deadline or '—'}")
        await show_tasks(update, ctx)

    # --- projects ---
    elif action == "project":
        sub = parts[1]
        if sub == "add":
            ctx.user_data["flow"] = {"name": "project_add"}
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
        elif sub == "del":
            with SessionLocal() as s:
                name = svc.delete_project(s, ws, int(parts[2]))
            await log_event(ctx.bot, user, "🗑 PROJECT DELETED", f"Project: {name}")
            await show_tasks(update, ctx, edit=True)

    # --- goals ---
    elif action == "goal":
        sub = parts[1]
        if sub == "add":
            ctx.user_data["flow"] = {"name": "goal_title"}
            await message.reply_text(t(lang, "ask_goal_title"),
                                     reply_markup=cancel_keyboard(lang))
        elif sub in ("donelist", "dellist"):
            with SessionLocal() as s:
                grouped = svc.list_goals(s, ws, include_completed=(sub == "dellist"))
            goals = [g for items in grouped.values() for g in items
                     if sub == "dellist" or g["status"] == "active"]
            if not goals:
                await query.answer(t(lang, "empty"), show_alert=True)
                return
            verb = "done" if sub == "donelist" else "del"
            rows = [[InlineKeyboardButton(g["title"][:40],
                                          callback_data=f"goal:{verb}:{g['id']}")]
                    for g in goals[:15]]
            rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="goal:back")])
            await query.edit_message_text(t(lang, "choose_delete"),
                                          reply_markup=InlineKeyboardMarkup(rows))
        elif sub == "done":
            with SessionLocal() as s:
                goal = svc.complete_goal(s, ws, int(parts[2]))
            await log_event(ctx.bot, user, "🎯 GOAL COMPLETED", f"Goal: {goal.title}")
            await show_goals(update, ctx, edit=True)
        elif sub == "del":
            with SessionLocal() as s:
                title = svc.delete_goal(s, ws, int(parts[2]))
            await log_event(ctx.bot, user, "🗑 GOAL DELETED", f"Goal: {title}")
            await show_goals(update, ctx, edit=True)
        elif sub == "back":
            await show_goals(update, ctx, edit=True)

    elif action == "habitcat":
        flow = ctx.user_data.get("flow") or {}
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

    elif action == "goalcat":
        flow = ctx.user_data.get("flow") or {}
        title = flow.get("title")
        if not title:
            await query.answer(t(lang, "error"), show_alert=True)
            return
        with SessionLocal() as s:
            goal = svc.add_goal(s, ws, title, parts[1])
        ctx.user_data.pop("flow", None)
        await query.edit_message_text(t(lang, "goal_added", title=goal.title))
        await log_event(ctx.bot, user, "🎯 GOAL ADDED",
                        f"Goal: {goal.title}\nCategory: {parts[1]}")
        await show_goals(update, ctx)

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
            rows = [[InlineKeyboardButton(name.title(), callback_data=f"theme:{name}")]
                    for name in THEMES]
            rows.append(back)
            await query.edit_message_text(t(lang, "btn_theme"),
                                          reply_markup=InlineKeyboardMarkup(rows))
        elif sub == "phone":
            rows = [[InlineKeyboardButton(t(lang, "btn_phone_del"),
                                          callback_data="set:phonedel")]] \
                if user.phone_number else []
            rows.append(back)
            await query.edit_message_text(t(lang, "ask_phone"),
                                          reply_markup=InlineKeyboardMarkup(rows))
            await message.reply_text(
                t(lang, "btn_phone_set"),
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton(t(lang, "btn_phone"), request_contact=True)]],
                    resize_keyboard=True, one_time_keyboard=True))
        elif sub == "phonedel":
            with SessionLocal() as s:
                row = s.get(User, user.telegram_id)
                row.phone_number = None
                s.commit()
            await query.edit_message_text(t(lang, "phone_removed"))
            await show_settings(update, ctx)
        elif sub == "photo":
            ctx.user_data["flow"] = {"name": "photo_wait"}
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
            ctx.user_data["flow"] = {"name": "wake_time"}
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
            row.theme = parts[1] if parts[1] in THEMES else "ocean"
            s.commit()
            snapshot = row
        await query.edit_message_text(t(lang, "saved"))
        await log_event(ctx.bot, snapshot, "🎨 THEME CHANGED", f"Theme: {parts[1]}")


# ---------------------------------------------------------------------------
# Channel membership events
# ---------------------------------------------------------------------------

async def on_chat_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """React the moment a user joins or leaves the required channel."""
    member = update.chat_member
    if member is None or not REQUIRED_CHANNEL_ID:
        return
    if str(member.chat.id) != str(REQUIRED_CHANNEL_ID):
        return

    telegram_id = member.new_chat_member.user.id
    subscribed = member.new_chat_member.status in MEMBER_STATES

    with SessionLocal() as s:
        user = s.get(User, telegram_id)
        if user is None:
            return
        if not svc.set_subscription(s, telegram_id, subscribed):
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

def render_morning(data: dict, lang: str) -> str:
    y, today = data["yesterday"], data["today"]
    lines = [f"<b>{t(lang, 'yesterday_title', date=y['date'])}</b>",
             f"{t(lang, 'r_habits')}: {y['habits_done']}/{y['habits_total']}",
             f"{t(lang, 'r_prayer')}: {y['prayer_score']}/5",
             f"{t(lang, 'r_tasks')}: {y['tasks_completed']} {t(lang, 'r_completed')}"]
    if y["tasks_missed"]:
        lines.append(f"❌ {y['tasks_missed']} {t(lang, 'r_remaining')}")
    lines.append(f"{t(lang, 'r_journal')}: "
                 f"{t(lang, 'r_yes') if y['journal'] else t(lang, 'r_no')}")

    lines.append(f"\n<b>{t(lang, 'morning_title', date=today['date'])}</b>")

    if today["tasks"]:
        lines.append(f"\n{t(lang, 'r_tasks')}")
        for task in today["tasks"][:8]:
            lines.append(f"• {task['title']}")
    if today["overdue"]:
        lines.append(f"\n{t(lang, 'home_overdue')}")
        for task in today["overdue"][:5]:
            lines.append(f"• {task['title']} ({task['deadline']})")
    if today["focus"]:
        lines.append(f"\n{t(lang, 'r_focus')}")
        for i, f in enumerate(today["focus"], start=1):
            lines.append(f"{i}. {'✅ ' if f['done'] else ''}{f['title']}")
    if today["birthdays"]:
        lines.append(f"\n{t(lang, 'home_bday')}")
        for b in today["birthdays"]:
            when = "🎉" if b["days_left"] == 0 else f"{b['days_left']} {t(lang, 'days_short')}"
            lines.append(f"• {b['person_name']} — {when}")

    return "\n".join(lines)


def render_evening(data: dict, lang: str) -> str:
    lines = [f"<b>{t(lang, 'evening_title')}</b>",
             f"{t(lang, 'r_habits')}: {data['habits_done']}/{data['habits_total']}",
             f"{t(lang, 'r_prayer')}: {data['prayer_score']}/5",
             f"{t(lang, 'r_tasks')}: {data['tasks_completed']} {t(lang, 'r_completed')}, "
             f"{len(data['tasks_remaining'])} {t(lang, 'r_remaining')}"]

    if data["focus"]:
        lines.append(f"{t(lang, 'r_focus')}: {data['focus_done']}/{len(data['focus'])}")
    lines.append(f"{t(lang, 'r_journal')}: "
                 f"{t(lang, 'r_yes') if data['journal'] else t(lang, 'r_no')}")

    unfinished = data["tasks_remaining"] + data["tasks_overdue"] + data["habits_remaining"]
    if unfinished:
        lines.append(f"\n{t(lang, 'r_unfinished')}")
        for item in unfinished[:8]:
            lines.append(f"• {item}")

    return "\n".join(lines)


async def send_platform_stats(bot) -> None:
    """Aggregate usage numbers for the operator. No user content, ever."""
    if not STATS_CHANNEL_ID:
        return
    with SessionLocal() as s:
        st = svc.platform_stats(s)

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
        f"Maqsad bajarildi: {st['goals_done']}\n"
        f"Bugun kundalik: {st['journal_today']}\n"
        f"Taklif: {st['feedback_week']}\n\n"
        f"<b>Til</b>: {languages or '—'}\n<b>Jins</b>: {genders or '—'}"
    ), chat_id=STATS_CHANNEL_ID)


async def send_reports(bot, report_type: str) -> None:
    """Deliver one report to every eligible user, at most once per local day."""
    report_date = svc.today_local()
    with SessionLocal() as s:
        recipients = svc.active_recipients(s)

    sent = failed = 0
    for telegram_id, ws, lang in recipients:
        with SessionLocal() as s:
            if svc.already_sent(s, ws, report_type, report_date):
                continue
            user = s.get(User, telegram_id)
            if user is None:
                continue
            data = (svc.morning_data(s, ws, user) if report_type == "morning"
                    else svc.evening_data(s, ws, user))

        text = (render_morning(data, lang) if report_type == "morning"
                else render_evening(data, lang))
        try:
            await bot.send_message(telegram_id, text, parse_mode=ParseMode.HTML,
                                   reply_markup=webapp_button(lang))
            with SessionLocal() as s:
                svc.mark_sent(s, ws, report_type, report_date)
            sent += 1
        except TelegramError as e:
            # A blocked bot or deleted account must not stop the whole run.
            log.warning("%s report to %s failed: %s", report_type, telegram_id, e)
            failed += 1
        await asyncio.sleep(0.05)  # stay inside Telegram's rate limit

    log.info("%s report: %s sent, %s failed", report_type, sent, failed)


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


def auth(init_data: str | None) -> tuple[User, int]:
    """Resolve the caller to (user, workspace_id), enforcing subscription."""
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
        if REQUIRED_CHANNEL_ID and not user.is_subscribed:
            raise HTTPException(status_code=403, detail="subscription_required")
        return user, ws


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------

telegram_app: Application | None = None
scheduler: AsyncIOScheduler | None = None


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
        await telegram_app.updater.start_polling(
            allowed_updates=["message", "callback_query", "chat_member", "my_chat_member"],
            drop_pending_updates=True)
        log.info("telegram bot polling")

        scheduler = AsyncIOScheduler(timezone=svc.TZ)
        scheduler.add_job(send_reports, "cron", hour=4, minute=0,
                          args=[telegram_app.bot, "morning"], id="morning",
                          misfire_grace_time=3600)
        scheduler.add_job(send_reports, "cron", hour=21, minute=0,
                          args=[telegram_app.bot, "evening"], id="evening",
                          misfire_grace_time=3600)
        scheduler.add_job(send_platform_stats, "cron", hour=23, minute=0,
                          args=[telegram_app.bot], id="stats",
                          misfire_grace_time=3600)
        scheduler.start()
        log.info("scheduler started (Asia/Tashkent 04:00 / 21:00)")
    else:
        log.warning("BOT_TOKEN missing — API only, no bot and no scheduler")

    yield

    if scheduler:
        scheduler.shutdown(wait=False)
    if telegram_app:
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()


app = FastAPI(title="ErnestOS", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """Never leak an exception type or traceback to a client."""
    log.exception("api error: %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "server_error"})


@app.exception_handler(svc.NotFound)
async def not_found(request: Request, exc: svc.NotFound):
    return JSONResponse(status_code=404, content={"detail": "not_found"})


# --- request bodies ---

class SettingsIn(BaseModel):
    language: str | None = None
    gender: str | None = None
    theme: str | None = None
    quote: str | None = None


class HabitIn(BaseModel):
    name: str
    category: str = "target"


class PrayerIn(BaseModel):
    prayer: str
    status: str
    day: str | None = None


class ExcusedIn(BaseModel):
    excused: bool
    day: str | None = None


class TaskIn(BaseModel):
    title: str
    description: str = ""
    deadline: str | None = None
    project_id: int | None = None
    priority: str = "medium"


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    deadline: str | None = None
    project_id: int | None = None
    priority: str | None = None
    status: str | None = None


class ProjectIn(BaseModel):
    name: str
    description: str = ""
    deadline: str | None = None


class GoalIn(BaseModel):
    title: str
    category: str
    description: str = ""


class GoalPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    category: str | None = None
    progress: int | None = None


class FocusIn(BaseModel):
    title: str


class JournalIn(BaseModel):
    answers: dict[str, str] | None = None
    text: str = ""
    day: str | None = None
    mood: str = ""


class BirthdayIn(BaseModel):
    person_name: str
    birth_date: str
    note: str = ""


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="bad_date")


# --- endpoints ---

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/me")
def api_me(init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, _ = auth(init)
    return {"telegram_id": user.telegram_id, "member_no": user.member_no,
            "first_name": user.first_name,
            "language": user.language, "gender": user.gender,
            "theme": user.theme, "quote": user.quote,
            "has_photo": bool(user.photo_file_id),
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


@app.get("/api/home")
def api_home(init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    with SessionLocal() as s:
        return svc.home(s, ws, s.get(User, user.telegram_id))


@app.get("/api/habits")
def api_habits(day: str | None = None, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        target = _date(day)
        return {"habits": svc.list_habits(s, ws, target),
                "grouped": svc.habits_by_category(s, ws, target),
                "categories": svc.HABIT_CATEGORIES,
                "streak": svc.habit_streak(s, ws)}


@app.post("/api/habits")
def api_habit_add(body: HabitIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        habit = svc.add_habit(s, ws, body.name, body.category)
    return {"ok": True, "id": habit.id}


@app.post("/api/habits/{habit_id}/toggle")
def api_habit_toggle(habit_id: int, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        try:
            return {"ok": True, "done": svc.toggle_habit(s, ws, habit_id)}
        except ValueError:
            raise HTTPException(status_code=400, detail="protected_habit")


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
    with SessionLocal() as s:
        try:
            score = svc.set_prayer(s, ws, body.prayer, body.status,
                                   user.gender, _date(body.day))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True, "score": score}


@app.post("/api/prayers/excused")
def api_prayer_excused(body: ExcusedIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    user, ws = auth(init)
    with SessionLocal() as s:
        try:
            score = svc.set_excused(s, ws, body.excused, user.gender, _date(body.day))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True, "score": score}


@app.get("/api/tasks")
def api_tasks(days: int = 7, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        return svc.list_tasks(s, ws, horizon_days=max(0, min(days, 365)))


@app.post("/api/tasks")
def api_task_add(body: TaskIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        task = svc.add_task(s, ws, body.title, deadline=_date(body.deadline),
                            project_id=body.project_id, priority=body.priority,
                            description=body.description)
    return {"ok": True, "id": task.id}


@app.patch("/api/tasks/{task_id}")
def api_task_patch(task_id: int, body: TaskPatch,
                   init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    fields = body.model_dump(exclude_unset=True)
    if "deadline" in fields:
        fields["deadline"] = _date(fields["deadline"])
    with SessionLocal() as s:
        svc.update_task(s, ws, task_id, **fields)
    return {"ok": True}


@app.delete("/api/tasks/{task_id}")
def api_task_delete(task_id: int, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        svc.delete_task(s, ws, task_id)
    return {"ok": True}


@app.get("/api/projects")
def api_projects(init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        return {"projects": svc.list_projects(s, ws)}


@app.post("/api/projects")
def api_project_add(body: ProjectIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        project = svc.add_project(s, ws, body.name, description=body.description,
                                  deadline=_date(body.deadline))
    return {"ok": True, "id": project.id}


@app.delete("/api/projects/{project_id}")
def api_project_delete(project_id: int, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        svc.delete_project(s, ws, project_id)
    return {"ok": True}


@app.get("/api/goals")
def api_goals(init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        return svc.list_goals(s, ws)


@app.post("/api/goals")
def api_goal_add(body: GoalIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        try:
            goal = svc.add_goal(s, ws, body.title, body.category,
                                description=body.description)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True, "id": goal.id}


@app.patch("/api/goals/{goal_id}")
def api_goal_patch(goal_id: int, body: GoalPatch,
                   init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        svc.update_goal(s, ws, goal_id, **body.model_dump(exclude_unset=True))
    return {"ok": True}


@app.post("/api/goals/{goal_id}/complete")
def api_goal_complete(goal_id: int, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        svc.complete_goal(s, ws, goal_id)
    return {"ok": True}


@app.delete("/api/goals/{goal_id}")
def api_goal_delete(goal_id: int, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        svc.delete_goal(s, ws, goal_id)
    return {"ok": True}


@app.get("/api/focus")
def api_focus(init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        return {"focus": svc.list_focus(s, ws)}


@app.post("/api/focus")
def api_focus_add(body: FocusIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        try:
            row = svc.add_focus(s, ws, body.title)
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
    with SessionLocal() as s:
        questions = [{"id": q["id"], "text": q.get(user.language, q["uz"])}
                     for q in svc.JOURNAL_QUESTIONS]
        if day:
            return {"entry": svc.get_journal(s, ws, _date(day)), "questions": questions}
        return {"entries": svc.list_journal(s, ws), "questions": questions}


@app.post("/api/journal")
def api_journal_save(body: JournalIn, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        row = svc.save_journal(s, ws, answers=body.answers, text=body.text,
                               day=_date(body.day), mood=body.mood)
        entry = svc.get_journal(s, ws, row.day)
    return {"ok": True, "day": row.day.isoformat(),
            "complete": bool(entry and entry["complete"])}


@app.delete("/api/journal/{day}")
def api_journal_delete(day: str, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        svc.delete_journal(s, ws, _date(day))
    return {"ok": True}


@app.get("/api/stats")
def api_stats(period: str = "week", init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Daily series for the habit and prayer line charts, plus streaks."""
    _, ws = auth(init)
    if period not in ("week", "month", "year"):
        period = "week"
    with SessionLocal() as s:
        return svc.stats(s, ws, period)


@app.get("/api/calendar")
def api_calendar(year: int | None = None, month: int | None = None,
                 init=Header(default=None, alias="X-Telegram-Init-Data")):
    """One month of deadlines, project dates, goal targets and birthdays."""
    _, ws = auth(init)
    today = svc.today_local()
    year, month = year or today.year, month or today.month
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        raise HTTPException(status_code=422, detail="bad_month")
    with SessionLocal() as s:
        return svc.calendar_month(s, ws, year, month)


@app.get("/api/tasks/done")
def api_tasks_done(init=Header(default=None, alias="X-Telegram-Init-Data")):
    """The Done archive — completed tasks are kept, never deleted."""
    _, ws = auth(init)
    with SessionLocal() as s:
        return {"tasks": svc.completed_tasks(s, ws)}


@app.get("/api/goals/done")
def api_goals_done(init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        return {"goals": svc.completed_goals(s, ws)}


@app.post("/api/goals/{goal_id}/reopen")
def api_goal_reopen(goal_id: int, init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        svc.reopen_goal(s, ws, goal_id)
    return {"ok": True}


@app.patch("/api/focus/{focus_id}")
def api_focus_edit(focus_id: int, body: FocusIn,
                   init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        try:
            svc.edit_focus(s, ws, focus_id, body.title)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True}


@app.get("/api/birthdays")
def api_birthdays(init=Header(default=None, alias="X-Telegram-Init-Data")):
    _, ws = auth(init)
    with SessionLocal() as s:
        return {"birthdays": svc.list_birthdays(s, ws, within_days=366)}


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
async def api_avatar(init=Header(default=None, alias="X-Telegram-Init-Data")):
    """Stream the user's profile photo.

    The bytes live on Telegram's CDN; the database only holds the file_id.
    Fetching server-side keeps the bot token out of the browser.
    """
    user, _ = auth(init)
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
    """Mini App mirror of the bot's "Turdim" button."""
    _, ws = auth(init)
    with SessionLocal() as s:
        return svc.mark_wakeup(s, ws)


class WakeTimeIn(BaseModel):
    time: str


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
