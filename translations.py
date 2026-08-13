"""
ErnestOS — every user-visible string the *bot* sends.

Kept apart from `app.py` for one reason: it is 700 lines of data sitting in
the middle of 3,600 lines of behaviour, and anybody opening the file to change
a handler had to scroll past all of it. Nothing here decides anything.

The Mini App has its own dictionary, in `webapp/index.html`. That is not a
duplicate — it is a different surface with different strings, loaded into the
browser rather than into this process — and a test asserts that each of the
three languages carries the same set of keys as the others, so a translation
cannot go missing on either side.

`app.py` re-exports `T` and `t`, so every existing call site and test keeps
working unchanged.
"""

from __future__ import annotations

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

