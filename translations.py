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
        #: Referrals. "Tasdiqlangan" (confirmed) rather than "taklif qilingan"
        #: (invited), because the number that moves is the one where somebody
        #: actually started using ErnestOS — and promising otherwise would make
        #: the counter feel broken.
        "ref_title": "🎁 Do'stlarni taklif qiling",
        "ref_body": ("ErnestOS sizga foydali bo'lsa, do'stlaringiz bilan "
                     "ulashing. Do'stingiz ro'yxatdan o'tib, ErnestOS'dan "
                     "haqiqatan foydalana boshlagach hisobga olinadi."),
        "ref_qualified_count": "Tasdiqlangan: <b>{n}</b>",
        "ref_next_level": "Keyingi daraja: {done} / {target}",
        "ref_max_level": "Eng yuqori daraja ochilgan 🏆",
        "ref_your_link": "Sizning havolangiz:",
        "ref_share": "📤 Ulashish",
        "ref_open_app": "🚀 ErnestOS'ni ochish",
        "ref_invite_again": "🎁 Yana taklif qilish",
        "ref_not_configured": "Taklif havolasi hozir mavjud emas.",
        "ref_qualified": ("🎉 <b>Yangi referral tasdiqlandi!</b>\n\n"
                          "Do'stingiz ErnestOS'dan haqiqatan foydalanishni "
                          "boshladi.\n\nTasdiqlangan: <b>{qualified}</b>\n"
                          "{progress}"),
        "ref_share_text": ("Hayotingni shunchaki rejalashtirma — tizimlashtir."
                           "\n\nMen ErnestOS'dan foydalanyapman. Sen ham sinab "
                           "ko'r 👇"),
        "ref_menu": "🎁 Do'st taklif qilish",

        # --- Personal progression ---------------------------------------
        # Level names are nouns the user becomes, not scores they hold:
        # "Siz Architect'siz" has to read naturally in all three languages,
        # so the English words stay untranslated where they already work as
        # identities and are localised where they do not.
        "plevel_starter": "Boshlovchi",
        "plevel_builder": "Builder",
        "plevel_operator": "Operator",
        "plevel_architect": "Architect",
        "plevel_commander": "Commander",
        "plevel_elite": "Elite",
        "plevel_master": "Master",
        "level_up": ("🎖 <b>YANGI DARAJA</b>\n\n"
                     "<b>{name} {numeral}</b>\n{xp} XP\n\n"
                     "<i>Siz shunchaki reja tuzmayapsiz. Tizim quryapsiz.</i>"),
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
        # Evening: the day against yesterday, in words rather than an arrow.
        "r_vs_yesterday_up": "kechagidan <b>+{delta}%</b> yuqori",
        "r_vs_yesterday_down": "kechagidan <b>−{delta}%</b> past",
        "r_vs_yesterday_same": "kechagi bilan bir xil",
        "r_vs_yesterday_none": "kecha o'lchanmagan — taqqoslash yo'q",
        "r_done_title": "✅ Bajarildi",
        "r_missed_title": "⭕️ Bajarilmadi",
        "r_nothing_missed": "Bugun hamma narsa bajarildi.",
        "r_nothing_done": "Bugun hech narsa belgilanmadi.",
        "r_today_plan_title": "📌 Bugungi reja",
        # Teams
        "team_morning_title": "👥 <b>{name}</b> — bugungi umumiy reja",
        "team_evening_title": "👥 <b>{name}</b> — bugungi natija",
        "team_nothing_today": "Bugunga umumiy ish qo'yilmagan.",
        "team_tasks": "⚡ Umumiy vazifalar",
        "team_habits": "✅ Umumiy odatlar",
        "team_all_done": "Ikkovingiz ham hammasini bajardingiz. Zo'r ish! 🎉",
        "team_you": "siz",
        "team_created": "👥 <b>{name}</b> jamoasi yaratildi.\n\nQuyidagi havolani sherigingizga yuboring — u havolani bossa, jamoaga qo'shiladi:",
        "team_joined": "👥 Siz <b>{name}</b> jamoasiga qo'shildingiz.",
        "team_already": "Siz allaqachon <b>{name}</b> jamoasidasiz.",
        "team_full": "Bu jamoa to'lgan yoki siz juda ko'p jamoadasiz.",
        "team_unknown": "Bu taklif havolasi eskirgan yoki noto'g'ri.",
        "team_member_joined": "👥 <b>{who}</b> <b>{name}</b> jamoasiga qo'shildi.",
        "team_none": "Sizda hali jamoa yo'q.\n\nJamoa — bu sherigingiz bilan bitta ro'yxatda ishlash. Vazifa umumiy bo'ladi, lekin har biringiz o'zingiznikini belgilaysiz.",
        "team_list_title": "👥 <b>Jamoalar</b>",
        "team_ask_name": "Jamoa nomini yozing:",
        "team_ask_rename": "Yangi nom yozing:",
        "team_renamed": "Nom o'zgartirildi: <b>{name}</b>",
        "team_create_btn": "➕ Jamoa yaratish",
        "team_invite_btn": "🔗 Taklif havolasi",
        "team_rename_btn": "✏️ Nomini o'zgartirish",
        "team_leave_btn": "🚪 Jamoani tark etish",
        "team_left": "Siz jamoani tark etdingiz.",
        "team_name_empty": "Nom bo'sh bo'lishi mumkin emas.",
        "team_too_many": "Jamoalar soni chegarasiga yetdingiz.",
        # What the other member is told when something in the shared space changes.
        "team_ev_task_add": "👥 <b>{who}</b> yangi umumiy vazifa qo'shdi:\n⚡ {what}",
        "team_ev_task_del": "👥 <b>{who}</b> umumiy vazifani o'chirdi:\n⚡ {what}",
        "team_ev_habit_add": "👥 <b>{who}</b> yangi umumiy odat qo'shdi:\n✅ {what}",
        "team_ev_habit_del": "👥 <b>{who}</b> umumiy odatni o'chirdi:\n✅ {what}",
        "team_ev_done": "👥 <b>{who}</b> bajardi:\n✔️ {what}",
        "team_ev_renamed": "👥 <b>{who}</b> jamoa nomini o'zgartirdi: <b>{what}</b>",
        "team_ev_project_add": "👥 <b>{who}</b> yangi umumiy loyiha ochdi:\n📁 {what}",
        "team_ev_in": "<i>{name}</i>",
        # --- Timers: a habit or task done by the clock ---------------------
        "dur_h": "{h} soat", "dur_m": "{m} daq", "dur_hm": "{h} soat {m} daq",
        "btn_timers": "⏱ Taymer",
        "timer_title": "⏱ <b>{title}</b>",
        "timer_len": "Davomiyligi: <b>{dur}</b>",
        "timer_mode_auto": "<i>(nomidan olindi)</i>",
        "timer_rule_habit": "Odat taymer tugagach o'zi belgilanadi — qo'lda belgilanmaydi.",
        "timer_rule_task": "Vazifa taymer tugagach o'zi bajarilgan bo'ladi.",
        "timer_off_text": "Taymer o'chirilgan — oddiy belgilash bilan bajariladi.",
        "timer_running": "▶️ Ishlayapti — <b>{left}</b> qoldi",
        "timer_ends": "🏁 {time} da tugaydi",
        "timer_paused": "⏸ Pauzada — <b>{left}</b> qoldi",
        "timer_done_today": "✅ Bugun bajarildi.",
        "timer_done_task": "✅ Bajarildi.",
        "btn_timer_start": "▶️ Boshlash · {dur}",
        "btn_timer_pause": "⏸ Pauza",
        "btn_timer_resume": "▶️ Davom etish",
        "btn_timer_stop": "⏹ To'xtatish",
        "btn_timer_refresh": "🔄 Yangilash",
        "btn_timer_change": "⚙️ Vaqtni o'zgartirish",
        "btn_timer_set": "⏱ Taymer qo'yish",
        "btn_timer_off": "🚫 Taymerni o'chirish",
        "btn_timer_auto": "🔤 Nomidagi vaqt · {dur}",
        "btn_timer_custom": "✍️ Boshqa vaqt",
        "btn_tick": "✅ Belgilash",
        "timer_pick": "⏱ <b>{title}</b>\n\nTaymer qancha davom etsin?",
        "timer_ask_custom": "Vaqtni yozing, masalan: <code>1h 30m</code>, <code>45 min</code> yoki <code>2 soat</code>.",
        "timer_bad_custom": "Tushunmadim. Masalan: <code>1h 30m</code>, <code>45 min</code> yoki <code>90</code> (daqiqa).",
        "timer_list_habits": "⏱ <b>Odat taymerlari</b>\n\nQaysi odatga taymer qo'yamiz yoki o'zgartiramiz?",
        "timer_list_tasks": "⏱ <b>Vazifa taymerlari</b>\n\nQaysi vazifaga taymer qo'yamiz yoki o'zgartiramiz?",
        "timer_finished_habit": "⏰ <b>Vaqt tugadi!</b>\n{title} — {dur}\n\n✅ Odat belgilandi. Barakalla!",
        "timer_finished_task": "⏰ <b>Vaqt tugadi!</b>\n{title} — {dur}\n\n✅ Vazifa bajarildi. Barakalla!",
        "timer_required": "⏱ Bu taymer bilan bajariladi — avval taymerni ishga tushiring.",
        "timer_already_done": "Bu allaqachon bajarilgan.",
        "timer_is_paused": "Bu odat pauzada — avval davom ettiring.",
        "timer_none_active": "Hozir ishlayotgan taymer yo'q.",
        "habit_added_timer": "⏱ Nomida vaqt bor — <b>{dur}</b> taymer qo'yildi. Odat taymer tugagach belgilanadi.\nO'zgartirish yoki o'chirish: Odatlar → ⏱ Taymer.",
        "task_added_timer": "⏱ Nomida vaqt bor — <b>{dur}</b> taymer qo'yildi. Vazifa taymer tugagach bajariladi.\nO'zgartirish yoki o'chirish: Vazifalar → ⏱ Taymer.",

        # --- Countdowns: days left until a date ---------------------------
        "btn_countdown": "⏳ Countdown",
        "cd_title": "⏳ <b>Countdown</b>",
        "cd_empty": "Hali countdown yo'q.\n\nMuhim sanani qo'shing — imtihon, safar, loyiha muddati — va har kuni ertalab va kechqurun necha kun qolganini eslatib turaman.",
        "cd_days_left": "<b>{n}</b> kun qoldi",
        "cd_tomorrow": "<b>ertaga!</b>",
        "cd_today": "<b>bugun!</b> 🎉",
        "cd_passed": "<i>o'tdi</i>",
        "btn_cd_add": "➕ Qo'shish",
        "btn_cd_del": "🗑 O'chirish",
        "cd_ask_title": "Nimagacha sanaymiz? Nomini yozing (masalan: <i>IELTS imtihoni</i>):",
        "cd_ask_date": "<b>{title}</b> — qachon?\n\nSanani yozing: <code>15.11.2026</code> · <code>15 noyabr</code> · <code>2026-11-15</code> · <code>30 kun</code> · <code>3 hafta</code>",
        "cd_bad_date": "Sanani tushunmadim. Masalan: <code>15.11.2026</code>, <code>15 noyabr</code> yoki <code>30 kun</code>.",
        "cd_past": "Bu sana o'tib ketgan. Bugungi yoki kelajakdagi sanani yozing.",
        "cd_too_far": "Juda uzoq — 10 yildan oshmasin.",
        "cd_too_many": "Countdownlar chegarasiga yetdingiz (20 ta). Keraksizini o'chiring.",
        "cd_added": "⏳ Qo'shildi: <b>{title}</b> — {left}\n\nHar kuni ertalab va kechqurun eslatib turaman.",
        "cd_deleted": "🗑 O'chirildi: <b>{title}</b>",
        "cd_choose_delete": "Qaysi countdown o'chirilsin?",
        "r_countdowns": "⏳ <b>Countdown</b>",

        # --- Shared (team) work, shown in the bot too ---------------------
        "tasks_team": "👥 Jamoa vazifalari",
        "btn_team_tasks": "👥 Jamoa vazifalari",
        "team_tasks_pick": "👥 <b>Jamoa vazifalari</b>\n\nO'zingiznikini belgilash uchun bosing:",
        "team_mark_hint": "✅ siz bajargansiz · ⬜ hali yo'q",
        "src_personal": "Shaxsiy",
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
        "ref_title": "🎁 Invite your friends",
        "ref_body": ("If ErnestOS is useful to you, share it. A friend counts "
                     "once they have signed up and actually started using it."),
        "ref_qualified_count": "Confirmed: <b>{n}</b>",
        "ref_next_level": "Next level: {done} / {target}",
        "ref_max_level": "Top level unlocked 🏆",
        "ref_your_link": "Your link:",
        "ref_share": "📤 Share",
        "ref_open_app": "🚀 Open ErnestOS",
        "ref_invite_again": "🎁 Invite another",
        "ref_not_configured": "Invite links aren't available right now.",
        "ref_qualified": ("🎉 <b>A referral was confirmed!</b>\n\n"
                          "Your friend has genuinely started using ErnestOS."
                          "\n\nConfirmed: <b>{qualified}</b>\n{progress}"),
        "ref_share_text": ("Don't just plan your life — systemize it.\n\n"
                           "I'm using ErnestOS. Try it with me 👇"),
        "ref_menu": "🎁 Invite a friend",

        # --- Personal progression ---------------------------------------
        "plevel_starter": "Starter",
        "plevel_builder": "Builder",
        "plevel_operator": "Operator",
        "plevel_architect": "Architect",
        "plevel_commander": "Commander",
        "plevel_elite": "Elite",
        "plevel_master": "Master",
        "level_up": ("🎖 <b>LEVEL UP</b>\n\n"
                     "<b>{name} {numeral}</b>\n{xp} XP\n\n"
                     "<i>You are not just planning your life. "
                     "You are building a system.</i>"),
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
        "r_vs_yesterday_up": "<b>+{delta}%</b> on yesterday",
        "r_vs_yesterday_down": "<b>−{delta}%</b> on yesterday",
        "r_vs_yesterday_same": "the same as yesterday",
        "r_vs_yesterday_none": "yesterday had nothing to measure",
        "r_done_title": "✅ Done",
        "r_missed_title": "⭕️ Not done",
        "r_nothing_missed": "Everything on today is done.",
        "r_nothing_done": "Nothing was ticked off today.",
        "r_today_plan_title": "📌 Today's plan",
        "team_morning_title": "👥 <b>{name}</b> — today, together",
        "team_evening_title": "👥 <b>{name}</b> — how today went",
        "team_nothing_today": "Nothing shared is set for today.",
        "team_tasks": "⚡ Shared tasks",
        "team_habits": "✅ Shared habits",
        "team_all_done": "Both of you finished everything. 🎉",
        "team_you": "you",
        "team_created": "👥 Team <b>{name}</b> created.\n\nSend this link to your partner — opening it joins them:",
        "team_joined": "👥 You joined <b>{name}</b>.",
        "team_already": "You are already in <b>{name}</b>.",
        "team_full": "That team is full, or you are in too many.",
        "team_unknown": "That invite link is expired or wrong.",
        "team_member_joined": "👥 <b>{who}</b> joined <b>{name}</b>.",
        "team_none": "You have no team yet.\n\nA team is one shared list. The work is shared; each of you ticks your own.",
        "team_list_title": "👥 <b>Teams</b>",
        "team_ask_name": "Name the team:",
        "team_ask_rename": "New name:",
        "team_renamed": "Renamed to <b>{name}</b>",
        "team_create_btn": "➕ New team",
        "team_invite_btn": "🔗 Invite link",
        "team_rename_btn": "✏️ Rename",
        "team_leave_btn": "🚪 Leave team",
        "team_left": "You left the team.",
        "team_name_empty": "A name cannot be empty.",
        "team_too_many": "You have reached the team limit.",
        "team_ev_task_add": "👥 <b>{who}</b> added a shared task:\n⚡ {what}",
        "team_ev_task_del": "👥 <b>{who}</b> removed a shared task:\n⚡ {what}",
        "team_ev_habit_add": "👥 <b>{who}</b> added a shared habit:\n✅ {what}",
        "team_ev_habit_del": "👥 <b>{who}</b> removed a shared habit:\n✅ {what}",
        "team_ev_done": "👥 <b>{who}</b> finished:\n✔️ {what}",
        "team_ev_renamed": "👥 <b>{who}</b> renamed the team: <b>{what}</b>",
        "team_ev_project_add": "👥 <b>{who}</b> opened a shared project:\n📁 {what}",
        "team_ev_in": "<i>{name}</i>",
        # --- Timers -------------------------------------------------------
        "dur_h": "{h} h", "dur_m": "{m} min", "dur_hm": "{h} h {m} min",
        "btn_timers": "⏱ Timer",
        "timer_title": "⏱ <b>{title}</b>",
        "timer_len": "Length: <b>{dur}</b>",
        "timer_mode_auto": "<i>(read from the name)</i>",
        "timer_rule_habit": "The habit ticks itself when the timer ends — it can't be ticked by hand.",
        "timer_rule_task": "The task completes itself when the timer ends.",
        "timer_off_text": "Timer is off — tick it as usual.",
        "timer_running": "▶️ Running — <b>{left}</b> left",
        "timer_ends": "🏁 Ends at {time}",
        "timer_paused": "⏸ Paused — <b>{left}</b> left",
        "timer_done_today": "✅ Done for today.",
        "timer_done_task": "✅ Done.",
        "btn_timer_start": "▶️ Start · {dur}",
        "btn_timer_pause": "⏸ Pause",
        "btn_timer_resume": "▶️ Resume",
        "btn_timer_stop": "⏹ Stop",
        "btn_timer_refresh": "🔄 Refresh",
        "btn_timer_change": "⚙️ Change length",
        "btn_timer_set": "⏱ Add a timer",
        "btn_timer_off": "🚫 Turn timer off",
        "btn_timer_auto": "🔤 From the name · {dur}",
        "btn_timer_custom": "✍️ Other length",
        "btn_tick": "✅ Tick",
        "timer_pick": "⏱ <b>{title}</b>\n\nHow long should the timer run?",
        "timer_ask_custom": "Type a length, e.g. <code>1h 30m</code>, <code>45 min</code> or <code>2 hours</code>.",
        "timer_bad_custom": "I didn't get that. E.g. <code>1h 30m</code>, <code>45 min</code> or <code>90</code> (minutes).",
        "timer_list_habits": "⏱ <b>Habit timers</b>\n\nWhich habit gets a timer?",
        "timer_list_tasks": "⏱ <b>Task timers</b>\n\nWhich task gets a timer?",
        "timer_finished_habit": "⏰ <b>Time's up!</b>\n{title} — {dur}\n\n✅ Habit ticked. Well done!",
        "timer_finished_task": "⏰ <b>Time's up!</b>\n{title} — {dur}\n\n✅ Task done. Well done!",
        "timer_required": "⏱ This one is done by its timer — start the timer first.",
        "timer_already_done": "That's already done.",
        "timer_is_paused": "This habit is paused — resume it first.",
        "timer_none_active": "No timer is running right now.",
        "habit_added_timer": "⏱ The name has a length — a <b>{dur}</b> timer is on. The habit ticks when it ends.\nChange or turn off: Habits → ⏱ Timer.",
        "task_added_timer": "⏱ The name has a length — a <b>{dur}</b> timer is on. The task completes when it ends.\nChange or turn off: Tasks → ⏱ Timer.",

        # --- Countdowns ---------------------------------------------------
        "btn_countdown": "⏳ Countdowns",
        "cd_title": "⏳ <b>Countdowns</b>",
        "cd_empty": "No countdowns yet.\n\nAdd a date that matters — an exam, a trip, a deadline — and I'll tell you every morning and evening how many days are left.",
        "cd_days_left": "<b>{n}</b> days left",
        "cd_tomorrow": "<b>tomorrow!</b>",
        "cd_today": "<b>today!</b> 🎉",
        "cd_passed": "<i>passed</i>",
        "btn_cd_add": "➕ Add",
        "btn_cd_del": "🗑 Delete",
        "cd_ask_title": "What are we counting down to? Type a name (e.g. <i>IELTS exam</i>):",
        "cd_ask_date": "<b>{title}</b> — when?\n\nType a date: <code>15.11.2026</code> · <code>15 November</code> · <code>2026-11-15</code> · <code>30 days</code> · <code>3 weeks</code>",
        "cd_bad_date": "I couldn't read that date. E.g. <code>15.11.2026</code>, <code>15 November</code> or <code>30 days</code>.",
        "cd_past": "That date has passed. Type today or a future date.",
        "cd_too_far": "Too far away — keep it within 10 years.",
        "cd_too_many": "You've reached the countdown limit (20). Delete one you no longer need.",
        "cd_added": "⏳ Added: <b>{title}</b> — {left}\n\nI'll remind you every morning and evening.",
        "cd_deleted": "🗑 Deleted: <b>{title}</b>",
        "cd_choose_delete": "Which countdown should go?",
        "r_countdowns": "⏳ <b>Countdowns</b>",

        # --- Shared (team) work ------------------------------------------
        "tasks_team": "👥 Team tasks",
        "btn_team_tasks": "👥 Team tasks",
        "team_tasks_pick": "👥 <b>Team tasks</b>\n\nTap to tick your own share:",
        "team_mark_hint": "✅ you did it · ⬜ not yet",
        "src_personal": "Personal",
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
        "ref_title": "🎁 Пригласите друзей",
        "ref_body": ("Если ErnestOS вам полезен — поделитесь. Друг "
                     "засчитывается, когда он зарегистрировался и начал "
                     "по-настоящему пользоваться приложением."),
        "ref_qualified_count": "Подтверждено: <b>{n}</b>",
        "ref_next_level": "Следующий уровень: {done} / {target}",
        "ref_max_level": "Открыт высший уровень 🏆",
        "ref_your_link": "Ваша ссылка:",
        "ref_share": "📤 Поделиться",
        "ref_open_app": "🚀 Открыть ErnestOS",
        "ref_invite_again": "🎁 Пригласить ещё",
        "ref_not_configured": "Ссылка-приглашение сейчас недоступна.",
        "ref_qualified": ("🎉 <b>Реферал подтверждён!</b>\n\n"
                          "Ваш друг действительно начал пользоваться ErnestOS."
                          "\n\nПодтверждено: <b>{qualified}</b>\n{progress}"),
        "ref_share_text": ("Не просто планируй жизнь — систематизируй её.\n\n"
                           "Я пользуюсь ErnestOS. Попробуй и ты 👇"),
        "ref_menu": "🎁 Пригласить друга",

        # --- Personal progression ---------------------------------------
        "plevel_starter": "Начинающий",
        "plevel_builder": "Builder",
        "plevel_operator": "Operator",
        "plevel_architect": "Architect",
        "plevel_commander": "Commander",
        "plevel_elite": "Elite",
        "plevel_master": "Master",
        "level_up": ("🎖 <b>НОВЫЙ УРОВЕНЬ</b>\n\n"
                     "<b>{name} {numeral}</b>\n{xp} XP\n\n"
                     "<i>Вы не просто планируете жизнь. "
                     "Вы строите систему.</i>"),
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
        "r_vs_yesterday_up": "<b>+{delta}%</b> ко вчерашнему",
        "r_vs_yesterday_down": "<b>−{delta}%</b> ко вчерашнему",
        "r_vs_yesterday_same": "как вчера",
        "r_vs_yesterday_none": "вчера нечего было измерять",
        "r_done_title": "✅ Сделано",
        "r_missed_title": "⭕️ Не сделано",
        "r_nothing_missed": "Всё на сегодня сделано.",
        "r_nothing_done": "Сегодня ничего не отмечено.",
        "r_today_plan_title": "📌 План на сегодня",
        "team_morning_title": "👥 <b>{name}</b> — сегодня вместе",
        "team_evening_title": "👥 <b>{name}</b> — итоги дня",
        "team_nothing_today": "На сегодня общих дел нет.",
        "team_tasks": "⚡ Общие задачи",
        "team_habits": "✅ Общие привычки",
        "team_all_done": "Вы оба всё сделали. 🎉",
        "team_you": "вы",
        "team_created": "👥 Команда <b>{name}</b> создана.\n\nОтправьте ссылку партнёру — открыв её, он присоединится:",
        "team_joined": "👥 Вы присоединились к <b>{name}</b>.",
        "team_already": "Вы уже в <b>{name}</b>.",
        "team_full": "Команда заполнена, либо вы уже в слишком многих.",
        "team_unknown": "Ссылка-приглашение устарела или неверна.",
        "team_member_joined": "👥 <b>{who}</b> присоединился к <b>{name}</b>.",
        "team_none": "У вас пока нет команды.\n\nКоманда — это общий список. Дело общее, но каждый отмечает своё.",
        "team_list_title": "👥 <b>Команды</b>",
        "team_ask_name": "Название команды:",
        "team_ask_rename": "Новое название:",
        "team_renamed": "Новое название: <b>{name}</b>",
        "team_create_btn": "➕ Новая команда",
        "team_invite_btn": "🔗 Ссылка-приглашение",
        "team_rename_btn": "✏️ Переименовать",
        "team_leave_btn": "🚪 Покинуть команду",
        "team_left": "Вы покинули команду.",
        "team_name_empty": "Название не может быть пустым.",
        "team_too_many": "Достигнут лимит команд.",
        "team_ev_task_add": "👥 <b>{who}</b> добавил(а) общую задачу:\n⚡ {what}",
        "team_ev_task_del": "👥 <b>{who}</b> удалил(а) общую задачу:\n⚡ {what}",
        "team_ev_habit_add": "👥 <b>{who}</b> добавил(а) общую привычку:\n✅ {what}",
        "team_ev_habit_del": "👥 <b>{who}</b> удалил(а) общую привычку:\n✅ {what}",
        "team_ev_done": "👥 <b>{who}</b> выполнил(а):\n✔️ {what}",
        "team_ev_renamed": "👥 <b>{who}</b> переименовал(а) команду: <b>{what}</b>",
        "team_ev_project_add": "👥 <b>{who}</b> создал(а) общий проект:\n📁 {what}",
        "team_ev_in": "<i>{name}</i>",
        # --- Таймеры -----------------------------------------------------
        "dur_h": "{h} ч", "dur_m": "{m} мин", "dur_hm": "{h} ч {m} мин",
        "btn_timers": "⏱ Таймер",
        "timer_title": "⏱ <b>{title}</b>",
        "timer_len": "Длительность: <b>{dur}</b>",
        "timer_mode_auto": "<i>(взято из названия)</i>",
        "timer_rule_habit": "Привычка отметится сама, когда таймер закончится — вручную не отмечается.",
        "timer_rule_task": "Задача выполнится сама, когда таймер закончится.",
        "timer_off_text": "Таймер выключен — отмечается как обычно.",
        "timer_running": "▶️ Идёт — осталось <b>{left}</b>",
        "timer_ends": "🏁 Закончится в {time}",
        "timer_paused": "⏸ На паузе — осталось <b>{left}</b>",
        "timer_done_today": "✅ Сегодня выполнено.",
        "timer_done_task": "✅ Выполнено.",
        "btn_timer_start": "▶️ Старт · {dur}",
        "btn_timer_pause": "⏸ Пауза",
        "btn_timer_resume": "▶️ Продолжить",
        "btn_timer_stop": "⏹ Стоп",
        "btn_timer_refresh": "🔄 Обновить",
        "btn_timer_change": "⚙️ Изменить время",
        "btn_timer_set": "⏱ Поставить таймер",
        "btn_timer_off": "🚫 Выключить таймер",
        "btn_timer_auto": "🔤 Из названия · {dur}",
        "btn_timer_custom": "✍️ Другое время",
        "btn_tick": "✅ Отметить",
        "timer_pick": "⏱ <b>{title}</b>\n\nСколько должен идти таймер?",
        "timer_ask_custom": "Напишите время, например: <code>1h 30m</code>, <code>45 мин</code> или <code>2 часа</code>.",
        "timer_bad_custom": "Не понял. Например: <code>1h 30m</code>, <code>45 мин</code> или <code>90</code> (минут).",
        "timer_list_habits": "⏱ <b>Таймеры привычек</b>\n\nДля какой привычки поставить таймер?",
        "timer_list_tasks": "⏱ <b>Таймеры задач</b>\n\nДля какой задачи поставить таймер?",
        "timer_finished_habit": "⏰ <b>Время вышло!</b>\n{title} — {dur}\n\n✅ Привычка отмечена. Молодец!",
        "timer_finished_task": "⏰ <b>Время вышло!</b>\n{title} — {dur}\n\n✅ Задача выполнена. Молодец!",
        "timer_required": "⏱ Это выполняется по таймеру — сначала запустите таймер.",
        "timer_already_done": "Это уже выполнено.",
        "timer_is_paused": "Привычка на паузе — сначала возобновите её.",
        "timer_none_active": "Сейчас таймер не идёт.",
        "habit_added_timer": "⏱ В названии есть время — поставлен таймер на <b>{dur}</b>. Привычка отметится, когда он закончится.\nИзменить или выключить: Привычки → ⏱ Таймер.",
        "task_added_timer": "⏱ В названии есть время — поставлен таймер на <b>{dur}</b>. Задача выполнится, когда он закончится.\nИзменить или выключить: Задачи → ⏱ Таймер.",

        # --- Обратный отсчёт ----------------------------------------------
        "btn_countdown": "⏳ Отсчёт",
        "cd_title": "⏳ <b>Обратный отсчёт</b>",
        "cd_empty": "Пока ничего нет.\n\nДобавьте важную дату — экзамен, поездку, дедлайн — и я каждое утро и вечер буду напоминать, сколько дней осталось.",
        "cd_days_left": "осталось дней: <b>{n}</b>",
        "cd_tomorrow": "<b>завтра!</b>",
        "cd_today": "<b>сегодня!</b> 🎉",
        "cd_passed": "<i>прошло</i>",
        "btn_cd_add": "➕ Добавить",
        "btn_cd_del": "🗑 Удалить",
        "cd_ask_title": "До чего считаем? Напишите название (например: <i>экзамен IELTS</i>):",
        "cd_ask_date": "<b>{title}</b> — когда?\n\nНапишите дату: <code>15.11.2026</code> · <code>15 ноября</code> · <code>2026-11-15</code> · <code>30 дней</code> · <code>3 недели</code>",
        "cd_bad_date": "Не понял дату. Например: <code>15.11.2026</code>, <code>15 ноября</code> или <code>30 дней</code>.",
        "cd_past": "Эта дата уже прошла. Напишите сегодняшнюю или будущую.",
        "cd_too_far": "Слишком далеко — не больше 10 лет.",
        "cd_too_many": "Достигнут лимит (20). Удалите ненужный.",
        "cd_added": "⏳ Добавлено: <b>{title}</b> — {left}\n\nБуду напоминать каждое утро и вечер.",
        "cd_deleted": "🗑 Удалено: <b>{title}</b>",
        "cd_choose_delete": "Что удалить?",
        "r_countdowns": "⏳ <b>Обратный отсчёт</b>",

        # --- Общие (командные) дела ---------------------------------------
        "tasks_team": "👥 Задачи команды",
        "btn_team_tasks": "👥 Задачи команды",
        "team_tasks_pick": "👥 <b>Задачи команды</b>\n\nНажмите, чтобы отметить свою часть:",
        "team_mark_hint": "✅ вы сделали · ⬜ ещё нет",
        "src_personal": "Личное",
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

# ---------------------------------------------------------------------------
# v7 — where an item goes, editing from the chat, quick capture, the two
# countdowns, team roles/invites/notifications, opt-in rituals, and the words
# the audit asked to be said plainly (#14, #21, #25).
# ---------------------------------------------------------------------------

T["uz"].update({
    # Home's two buttons, as they were asked for: a date, and a clock.
    "btn_countdown": "📅 Date countdown",
    "btn_timers": "⏱ Time countdown",
    "cd_title": "📅 <b>Date countdown</b>",
    "menu_teams": "👥 Jamoa",
    # Plain words for the three tiers and the levels (#21).
    "cat_non_negotiable": "🔴 Majburiy",
    "cat_target": "🟡 Maqsadli",
    "cat_bonus": "🟢 Qo'shimcha",
    "plevel_builder": "Quruvchi", "plevel_operator": "Boshqaruvchi",
    "plevel_architect": "Me'mor", "plevel_commander": "Sardor",
    "plevel_elite": "Sara", "plevel_master": "Usta",
    "team_all_done": "Hammangiz hammasini bajardingiz. Zo'r ish! 🎉",
    "team_created": ("👥 <b>{name}</b> jamoasi yaratildi.\n\nHavolani sherigingizga "
                     "yuboring — u jamoani ko'radi va «Qo'shilish»ni bossa, qo'shiladi. "
                     "Havola 3 kun ishlaydi:"),
    "ask_habits": (
        "<b>Har kuni takrorlanadigan 1–3 ta odat?</b>\n"
        "Har birini yangi qatordan yozing.\n\n"
        "<i>Masalan:\n"
        "Ertalab 30 daqiqa kitob\n"
        "10 000 qadam\n"
        "Suv — 2 litr</i>"),
    # --- setup: what to keep ---------------------------------------------
    "ask_modules": ("<b>Nimalarni kuzatamiz?</b>\n"
                    "Faqat tanlaganingiz hisoblanadi. Keyin Sozlamalar → "
                    "🧩 Modullar'dan o'zgartirasiz."),
    "mod_wake": "☀️ Erta turish", "mod_prayer": "🕌 Namoz",
    "mod_journal": "📔 Kundalik", "mod_team": "👥 Jamoa bilan ishlash",
    "mod_continue": "Davom etish ➡️",
    "modules_set": "✅ Tanlandi: <b>{list}</b>",
    "modules_none": "hech biri — faqat o'z vazifa va odatlaringiz",
    "modules_settings": ("🧩 <b>Modullar</b>\n\nO'chirilgan modul tarixini saqlaydi; "
                         "qayta yoqsangiz, o'chiq kunlar «bajarilmadi» hisoblanmaydi."),
    "btn_modules": "🧩 Modullar",
    "setup_team_hint": ("👥 Jamoa bilan ishlamoqchi edingiz. Jamoa yarating va "
                        "havolani sherigingizga yuboring:"),
    # --- tasks screen ------------------------------------------------------
    "tasks_today": "⚡ Bugun:",
    "tasks_upcoming": "📅 Keyingi kunlar",
    "no_deadline": "📥 Muddatsiz",
    "days_none": "📥 Muddatsiz",
    # --- where it goes ---------------------------------------------------
    "dest_personal": "👤 Shaxsiy",
    "ask_dest_task": "⚡ <b>{title}</b>\n\nKimniki: shaxsiymi yoki jamoaniki?",
    "ask_dest_habit": "✅ <b>{title}</b>\n\nKimniki: shaxsiymi yoki jamoaniki?",
    "task_added_team": "👥 Jamoa vazifasi qo'shildi: <b>{title}</b> · {team}",
    "habit_added_team": "👥 Jamoa odati qo'shildi: <b>{name}</b> · {team}",
    "flow_expired": "Bu qadam eskirdi — boshidan boshlang.",
    # --- editing -----------------------------------------------------------
    "btn_edit_habit": "✏️ Tahrirlash",
    "edit_name": "✏️ Nomi", "edit_date": "📅 Muddati", "edit_priority": "🎚 Muhimligi",
    "edit_category": "🗂 Toifasi", "edit_move": "🔀 Boshqa joyga",
    "edit_delete": "🗑 O'chirish",
    "ask_new_date": "Yangi muddat:",
    "ask_priority": "Qanchalik muhim?",
    "prio_high": "Muhim", "prio_medium": "O'rtacha", "prio_low": "Past",
    "ask_move": "Qayerga ko'chiramiz?",
    "moved_to": "🔀 Ko'chirildi: {where}",
    "move_between_teams": "Jamoadan jamoaga to'g'ridan-to'g'ri ko'chirib bo'lmaydi — avval shaxsiyga o'tkazing.",
    "confirm_delete": "Rostdan o'chirilsinmi? Bajarilganlar tarixi saqlanadi.",
    "confirm_delete_habit": "Odat o'chirilsinmi? O'tgan kunlardagi natijalar o'zgarmaydi.",
    "completion_all": "Hamma bajaradi", "completion_any": "Bittasi yetarli",
    "completion_assignees": "Faqat tayinlanganlar",
    "team_only_creator": "Buni faqat yaratgan kishi, admin yoki jamoa egasi o'zgartira oladi.",
    "habit_paused": "Pauzada",
    "habit_pause_from": "{day} dan pauza",
    "habit_protected_edit": "Bu odat modul orqali boshqariladi (Sozlamalar → 🧩 Modullar).",
    "habit_resume_btn": "▶️ Davom ettirish",
    "pause_tomorrow": "⏸ Ertadan pauza", "pause_today": "⏸ Bugundan pauza",
    "habit_mirrored": ("🔒 Bu marosim har bir a'zoning o'z odatidan olinadi — "
                       "uni shaxsiy ro'yxatingizda belgilang, bu yerda ikki marta "
                       "sanalmaydi."),
    "team_not_assigned": "Bu vazifa sizga tayinlanmagan.",
    # --- quick capture -----------------------------------------------------
    "capture_ask": "Vazifa sifatida saqlaymi?",
    "capture_save": "👤 Shaxsiy vazifa",
    "capture_skip": "✖️ Kerak emas",
    "capture_saved": "📥 Saqlandi: <b>{title}</b> · {where}",
    "capture_expired": "Bu taklif eskirdi — matnni qayta yuboring.",
    # --- the two countdowns ----------------------------------------------
    "timer_for_habits": "✅ Odatlar", "timer_for_tasks": "⚡ Vazifalar",
    "timer_home_hint": "\nQaysi biriga taymer qo'yamiz yoki ishga tushiramiz?",
    "timer_team_own": "👥 Jamoa ishi: har kim o'z taymerini o'zi yuritadi.",
    "cd_ask_scope": "📅 <b>Yangi Date countdown</b>\n\nNima uchun?",
    "cd_scope_general": "Umumiy", "cd_scope_task": "Vazifa uchun",
    "cd_scope_habit": "Odat uchun",
    "cd_ask_dest": "Kimniki: shaxsiymi yoki jamoaniki?",
    "cd_ask_link_task": "Qaysi vazifaga? (yoki alohida nom bilan)",
    "cd_ask_link_habit": "Qaysi odatga? (yoki alohida nom bilan)",
    "cd_no_link": "✍️ Alohida nom yozaman",
    "cd_use_deadline": "📅 Vazifa muddati — {day}",
    "team_ev_countdown": "👥 <b>{who}</b> yangi countdown qo'shdi:\n📅 {what}",
    # --- statistics wording (#13, #14) -----------------------------------
    "st_week_short": "7 kun", "st_month_short": "30 kun",
    "st_nothing_measured": "Bu davrda o'lchanadigan narsa bo'lmagan.",
    "points": "{n} punkt",
    "team_units": "{items} ta ish · {confirmations} ta tasdiq · {left} tasi qolgan",
    # --- teams: roles, invites, notifications ----------------------------
    "role_owner": "Egasi", "role_admin": "Admin", "role_member": "A'zo",
    "you": "siz",
    "accept": "✅ Qabul qilish", "decline": "✖️ Yo'q",
    "team_open_btn": "👥 Jamoani ochish",
    "team_admin_only": "Buni faqat jamoa egasi yoki admin qila oladi.",
    "team_invite_text": "👥 <b>{name}</b> jamoasiga taklif havolasi:",
    "team_invite_until": "🔗 Havola {when} gacha ishlaydi",
    "team_invite_expired": "🔗 Havola ishlamaydi — yangisini oling",
    "team_invite_revoked": "Eski havola bekor qilindi.",
    "team_revoke_btn": "⛔️ Havolani bekor qilish",
    "team_approval_on": "✋ Qo'shilish uchun tasdiq kerak",
    "team_approval_on_btn": "✋ Tasdiq bilan qo'shish",
    "team_approval_off_btn": "🔓 Tasdiqsiz qo'shish",
    "team_preview": ("👥 <b>{name}</b>\nEgasi: {owner} · a'zolar: {n}/{max}\n\n"
                     "Jamoaga qo'shilasizmi? Umumiy vazifa va odatlar ro'yxatingizda "
                     "👥 belgisi bilan chiqadi, har kim o'zinikini belgilaydi."),
    "team_preview_approval": "✋ Qo'shilishni egasi yoki admin tasdiqlaydi.",
    "team_join_btn": "✅ Qo'shilish",
    "team_link_expired": "Bu havola muddati o'tgan. <b>{name}</b> egasidan yangisini so'rang.",
    "team_unknown": "Bu taklif havolasi eskirgan yoki noto'g'ri.",
    "team_requested": "✋ So'rov yuborildi — <b>{name}</b> admini tasdiqlagach qo'shilasiz.",
    "team_request_new": "✋ <b>{who}</b> <b>{name}</b> jamoasiga qo'shilmoqchi.",
    "team_request_declined": "<b>{name}</b> jamoasiga so'rovingiz qabul qilinmadi.",
    "team_requests": "✋ Kutilayotgan so'rovlar: {n}",
    "team_notify_btn": "🔔 Bildirishnomalar",
    "team_notify_ask": ("🔔 Bu jamoadan qanday xabarlar kelsin?\n\n"
                        "Hammasi — o'zgarishlar, hisobot va eslatmalar\n"
                        "Muhim — hisobot va eslatmalar\n"
                        "Faqat menga — menga tayinlangan vazifa eslatmalari\n"
                        "O'chiq — hech narsa"),
    "team_notify_now": "🔔 Bildirishnoma: {level}",
    "notify_all": "Hammasi", "notify_important": "Muhim",
    "notify_assigned": "Faqat menga", "notify_off": "O'chiq",
    "team_stats_btn": "📊 Statistika",
    "team_members_btn": "👤 A'zolar va rollar",
    "team_members_title": "👤 <b>A'zolar</b>\n\n⭐ admin qilish yoki oddiy a'zo qilish, 🚫 chiqarish.",
    "role_make_admin": "⭐ Admin qilish", "role_make_member": "👤 A'zo qilish",
    "role_give_owner": "👑 Egalikni berish: {name}",
    "team_owner_offer": "👑 Sizga <b>{name}</b> jamoasining egaligi taklif qilindi.",
    "team_owner_offered": "Taklif yuborildi — u qabul qilgach, egalik o'tadi.",
    "team_owner_now": "👑 Endi siz <b>{name}</b> jamoasining egasisiz.",
    "team_owner_declined": "Egalik taklifi rad etildi.",
    "team_owner_must_transfer": "Jamoa egasi chiqishdan oldin egalikni boshqa a'zoga berishi kerak.",
    "team_leave_confirm": "Jamoani tark etasizmi? Siz qo'shgan ishlar jamoada qoladi.",
})

T["en"].update({
    "btn_countdown": "📅 Date countdown",
    "btn_timers": "⏱ Time countdown",
    "cd_title": "📅 <b>Date countdown</b>",
    "menu_teams": "👥 Team",
    "cat_non_negotiable": "🔴 Must-do",
    "cat_target": "🟡 Goal",
    "cat_bonus": "🟢 Extra",
    "team_all_done": "Everyone finished everything. 🎉",
    "team_created": ("👥 Team <b>{name}</b> created.\n\nSend the link to your "
                     "partner — they will see the team and join with one tap. "
                     "The link works for 3 days:"),
    "ask_habits": (
        "<b>1–3 habits you repeat every day?</b>\n"
        "One per line.\n\n"
        "<i>For example:\n"
        "Read 30 minutes in the morning\n"
        "10,000 steps\n"
        "Water — 2 litres</i>"),
    "ask_modules": ("<b>What should we track?</b>\n"
                    "Only what you pick is counted. Change it later in Settings → "
                    "🧩 Modules."),
    "mod_wake": "☀️ Early rising", "mod_prayer": "🕌 Prayer",
    "mod_journal": "📔 Journal", "mod_team": "👥 Working with a team",
    "mod_continue": "Continue ➡️",
    "modules_set": "✅ Chosen: <b>{list}</b>",
    "modules_none": "none — just your own tasks and habits",
    "modules_settings": ("🧩 <b>Modules</b>\n\nA module switched off keeps its "
                         "history; switched back on, the days it was off are not "
                         "counted as missed."),
    "btn_modules": "🧩 Modules",
    "setup_team_hint": "👥 You wanted to work with a team. Create one and send the link:",
    "tasks_today": "⚡ Today:",
    "tasks_upcoming": "📅 Coming days",
    "no_deadline": "📥 No date",
    "days_none": "📥 No date",
    "dest_personal": "👤 Personal",
    "ask_dest_task": "⚡ <b>{title}</b>\n\nWhose is it: yours or a team's?",
    "ask_dest_habit": "✅ <b>{title}</b>\n\nWhose is it: yours or a team's?",
    "task_added_team": "👥 Team task added: <b>{title}</b> · {team}",
    "habit_added_team": "👥 Team habit added: <b>{name}</b> · {team}",
    "flow_expired": "That step has expired — please start again.",
    "btn_edit_habit": "✏️ Edit",
    "edit_name": "✏️ Name", "edit_date": "📅 Due date", "edit_priority": "🎚 Priority",
    "edit_category": "🗂 Tier", "edit_move": "🔀 Move",
    "edit_delete": "🗑 Delete",
    "ask_new_date": "New due date:",
    "ask_priority": "How important?",
    "prio_high": "High", "prio_medium": "Medium", "prio_low": "Low",
    "ask_move": "Move it where?",
    "moved_to": "🔀 Moved: {where}",
    "move_between_teams": "Items can't move straight from one team to another — move it to personal first.",
    "confirm_delete": "Delete it? Completed history is kept.",
    "confirm_delete_habit": "Delete this habit? Past days stay exactly as they were.",
    "completion_all": "Everyone does it", "completion_any": "One person is enough",
    "completion_assignees": "Assignees only",
    "team_only_creator": "Only its creator, an admin or the team owner can change this.",
    "habit_paused": "Paused",
    "habit_pause_from": "Paused from {day}",
    "habit_protected_edit": "This habit is managed by a module (Settings → 🧩 Modules).",
    "habit_resume_btn": "▶️ Resume",
    "pause_tomorrow": "⏸ Pause from tomorrow", "pause_today": "⏸ Pause from today",
    "habit_mirrored": ("🔒 This ritual is read from each member's own habit — tick "
                       "it in your personal list; it is never counted twice."),
    "team_not_assigned": "This task isn't assigned to you.",
    "capture_ask": "Save it as a task?",
    "capture_save": "👤 Personal task",
    "capture_skip": "✖️ No thanks",
    "capture_saved": "📥 Saved: <b>{title}</b> · {where}",
    "capture_expired": "That offer expired — send the text again.",
    "timer_for_habits": "✅ Habits", "timer_for_tasks": "⚡ Tasks",
    "timer_home_hint": "\nWhich one gets a timer, or starts one?",
    "timer_team_own": "👥 Team item: everyone runs their own timer.",
    "cd_ask_scope": "📅 <b>New date countdown</b>\n\nWhat is it for?",
    "cd_scope_general": "General", "cd_scope_task": "For a task",
    "cd_scope_habit": "For a habit",
    "cd_ask_dest": "Whose is it: yours or a team's?",
    "cd_ask_link_task": "Which task? (or give it its own name)",
    "cd_ask_link_habit": "Which habit? (or give it its own name)",
    "cd_no_link": "✍️ I'll type a name",
    "cd_use_deadline": "📅 The task's due date — {day}",
    "team_ev_countdown": "👥 <b>{who}</b> added a countdown:\n📅 {what}",
    "st_week_short": "7 days", "st_month_short": "30 days",
    "st_nothing_measured": "Nothing was measured in this period.",
    "points": "{n} pts",
    "team_units": "{items} items · {confirmations} check-ins · {left} left",
    "role_owner": "Owner", "role_admin": "Admin", "role_member": "Member",
    "you": "you",
    "accept": "✅ Accept", "decline": "✖️ No",
    "team_open_btn": "👥 Open the team",
    "team_admin_only": "Only the team owner or an admin can do this.",
    "team_invite_text": "👥 Invite link for <b>{name}</b>:",
    "team_invite_until": "🔗 The link works until {when}",
    "team_invite_expired": "🔗 The link no longer works — get a new one",
    "team_invite_revoked": "The old link has been revoked.",
    "team_revoke_btn": "⛔️ Revoke link",
    "team_approval_on": "✋ Joining needs approval",
    "team_approval_on_btn": "✋ Require approval",
    "team_approval_off_btn": "🔓 Join without approval",
    "team_preview": ("👥 <b>{name}</b>\nOwner: {owner} · members: {n}/{max}\n\n"
                     "Join this team? Shared tasks and habits appear in your lists "
                     "marked 👥, and everyone ticks their own."),
    "team_preview_approval": "✋ The owner or an admin approves new members.",
    "team_join_btn": "✅ Join",
    "team_link_expired": "This link has expired. Ask the owner of <b>{name}</b> for a new one.",
    "team_requested": "✋ Request sent — you'll join <b>{name}</b> once an admin approves.",
    "team_request_new": "✋ <b>{who}</b> wants to join <b>{name}</b>.",
    "team_request_declined": "Your request to join <b>{name}</b> was declined.",
    "team_requests": "✋ Pending requests: {n}",
    "team_notify_btn": "🔔 Notifications",
    "team_notify_ask": ("🔔 What should this team send you?\n\n"
                        "Everything — changes, reports and reminders\n"
                        "Important — reports and reminders\n"
                        "Only mine — reminders for tasks assigned to me\n"
                        "Off — nothing"),
    "team_notify_now": "🔔 Notifications: {level}",
    "notify_all": "Everything", "notify_important": "Important",
    "notify_assigned": "Only mine", "notify_off": "Off",
    "team_stats_btn": "📊 Statistics",
    "team_members_btn": "👤 Members & roles",
    "team_members_title": "👤 <b>Members</b>\n\n⭐ make admin or member, 🚫 remove.",
    "role_make_admin": "⭐ Make admin", "role_make_member": "👤 Make member",
    "role_give_owner": "👑 Hand ownership to {name}",
    "team_owner_offer": "👑 You've been offered ownership of <b>{name}</b>.",
    "team_owner_offered": "Offer sent — ownership moves once they accept.",
    "team_owner_now": "👑 You now own <b>{name}</b>.",
    "team_owner_declined": "The ownership offer was declined.",
    "team_owner_must_transfer": "The owner has to hand ownership to another member before leaving.",
    "team_leave_confirm": "Leave the team? What you added stays with the team.",
})

T["ru"].update({
    "btn_countdown": "📅 Date countdown",
    "btn_timers": "⏱ Time countdown",
    "cd_title": "📅 <b>Date countdown</b>",
    "menu_teams": "👥 Команда",
    "cat_non_negotiable": "🔴 Обязательно",
    "cat_target": "🟡 Цель",
    "cat_bonus": "🟢 Дополнительно",
    "plevel_builder": "Строитель", "plevel_operator": "Оператор",
    "plevel_architect": "Архитектор", "plevel_commander": "Командир",
    "plevel_elite": "Элита", "plevel_master": "Мастер",
    "team_all_done": "Все всё сделали. 🎉",
    "team_created": ("👥 Команда <b>{name}</b> создана.\n\nОтправьте ссылку "
                     "партнёру — он увидит команду и вступит одним нажатием. "
                     "Ссылка работает 3 дня:"),
    "ask_habits": (
        "<b>1–3 привычки на каждый день?</b>\n"
        "Каждую с новой строки.\n\n"
        "<i>Например:\n"
        "30 минут чтения утром\n"
        "10 000 шагов\n"
        "Вода — 2 литра</i>"),
    "ask_modules": ("<b>Что будем отслеживать?</b>\n"
                    "Считается только выбранное. Изменить можно в Настройки → "
                    "🧩 Модули."),
    "mod_wake": "☀️ Ранний подъём", "mod_prayer": "🕌 Намаз",
    "mod_journal": "📔 Дневник", "mod_team": "👥 Работа в команде",
    "mod_continue": "Дальше ➡️",
    "modules_set": "✅ Выбрано: <b>{list}</b>",
    "modules_none": "ничего — только свои задачи и привычки",
    "modules_settings": ("🧩 <b>Модули</b>\n\nВыключенный модуль хранит историю; "
                         "при включении дни, когда он был выключен, не считаются "
                         "пропущенными."),
    "btn_modules": "🧩 Модули",
    "setup_team_hint": "👥 Вы хотели работать в команде. Создайте её и отправьте ссылку:",
    "tasks_today": "⚡ Сегодня:",
    "tasks_upcoming": "📅 Следующие дни",
    "no_deadline": "📥 Без срока",
    "days_none": "📥 Без срока",
    "dest_personal": "👤 Личное",
    "ask_dest_task": "⚡ <b>{title}</b>\n\nЧьё это: личное или командное?",
    "ask_dest_habit": "✅ <b>{title}</b>\n\nЧьё это: личное или командное?",
    "task_added_team": "👥 Командная задача добавлена: <b>{title}</b> · {team}",
    "habit_added_team": "👥 Командная привычка добавлена: <b>{name}</b> · {team}",
    "flow_expired": "Этот шаг устарел — начните заново.",
    "btn_edit_habit": "✏️ Изменить",
    "edit_name": "✏️ Название", "edit_date": "📅 Срок", "edit_priority": "🎚 Важность",
    "edit_category": "🗂 Уровень", "edit_move": "🔀 Перенести",
    "edit_delete": "🗑 Удалить",
    "ask_new_date": "Новый срок:",
    "ask_priority": "Насколько важно?",
    "prio_high": "Высокая", "prio_medium": "Средняя", "prio_low": "Низкая",
    "ask_move": "Куда перенести?",
    "moved_to": "🔀 Перенесено: {where}",
    "move_between_teams": "Напрямую из команды в команду нельзя — сначала перенесите в личное.",
    "confirm_delete": "Удалить? История выполненного сохранится.",
    "confirm_delete_habit": "Удалить привычку? Прошлые дни не изменятся.",
    "completion_all": "Делают все", "completion_any": "Достаточно одного",
    "completion_assignees": "Только назначенные",
    "team_only_creator": "Изменить может только автор, админ или владелец команды.",
    "habit_paused": "На паузе",
    "habit_pause_from": "Пауза с {day}",
    "habit_protected_edit": "Эта привычка управляется модулем (Настройки → 🧩 Модули).",
    "habit_resume_btn": "▶️ Продолжить",
    "pause_tomorrow": "⏸ Пауза с завтра", "pause_today": "⏸ Пауза с сегодня",
    "habit_mirrored": ("🔒 Этот ритуал берётся из личной привычки каждого участника — "
                       "отмечайте его в личном списке, дважды он не считается."),
    "team_not_assigned": "Эта задача назначена не вам.",
    "capture_ask": "Сохранить как задачу?",
    "capture_save": "👤 Личная задача",
    "capture_skip": "✖️ Не нужно",
    "capture_saved": "📥 Сохранено: <b>{title}</b> · {where}",
    "capture_expired": "Предложение устарело — отправьте текст ещё раз.",
    "timer_for_habits": "✅ Привычки", "timer_for_tasks": "⚡ Задачи",
    "timer_home_hint": "\nНа что поставить или запустить таймер?",
    "timer_team_own": "👥 Командное дело: у каждого свой таймер.",
    "cd_ask_scope": "📅 <b>Новый date countdown</b>\n\nДля чего?",
    "cd_scope_general": "Общий", "cd_scope_task": "Для задачи",
    "cd_scope_habit": "Для привычки",
    "cd_ask_dest": "Чьё это: личное или командное?",
    "cd_ask_link_task": "Какая задача? (или своё название)",
    "cd_ask_link_habit": "Какая привычка? (или своё название)",
    "cd_no_link": "✍️ Напишу название",
    "cd_use_deadline": "📅 Срок задачи — {day}",
    "team_ev_countdown": "👥 <b>{who}</b> добавил отсчёт:\n📅 {what}",
    "st_week_short": "7 дней", "st_month_short": "30 дней",
    "st_nothing_measured": "За этот период нечего было измерять.",
    "points": "{n} п.",
    "team_units": "{items} дел · {confirmations} отметок · осталось {left}",
    "role_owner": "Владелец", "role_admin": "Админ", "role_member": "Участник",
    "you": "вы",
    "accept": "✅ Принять", "decline": "✖️ Нет",
    "team_open_btn": "👥 Открыть команду",
    "team_admin_only": "Это может только владелец команды или админ.",
    "team_invite_text": "👥 Ссылка-приглашение в <b>{name}</b>:",
    "team_invite_until": "🔗 Ссылка работает до {when}",
    "team_invite_expired": "🔗 Ссылка больше не работает — получите новую",
    "team_invite_revoked": "Старая ссылка отозвана.",
    "team_revoke_btn": "⛔️ Отозвать ссылку",
    "team_approval_on": "✋ Для вступления нужно одобрение",
    "team_approval_on_btn": "✋ Вступление с одобрением",
    "team_approval_off_btn": "🔓 Вступление без одобрения",
    "team_preview": ("👥 <b>{name}</b>\nВладелец: {owner} · участники: {n}/{max}\n\n"
                     "Вступить в команду? Общие задачи и привычки появятся в ваших "
                     "списках с отметкой 👥, каждый отмечает своё."),
    "team_preview_approval": "✋ Новых участников одобряет владелец или админ.",
    "team_join_btn": "✅ Вступить",
    "team_link_expired": "Срок ссылки истёк. Попросите у владельца <b>{name}</b> новую.",
    "team_requested": "✋ Заявка отправлена — вы вступите в <b>{name}</b> после одобрения.",
    "team_request_new": "✋ <b>{who}</b> хочет вступить в <b>{name}</b>.",
    "team_request_declined": "Заявка на вступление в <b>{name}</b> отклонена.",
    "team_requests": "✋ Ожидают одобрения: {n}",
    "team_notify_btn": "🔔 Уведомления",
    "team_notify_ask": ("🔔 Что присылать из этой команды?\n\n"
                        "Всё — изменения, отчёты и напоминания\n"
                        "Важное — отчёты и напоминания\n"
                        "Только мне — напоминания о моих задачах\n"
                        "Выкл — ничего"),
    "team_notify_now": "🔔 Уведомления: {level}",
    "notify_all": "Всё", "notify_important": "Важное",
    "notify_assigned": "Только мне", "notify_off": "Выкл",
    "team_stats_btn": "📊 Статистика",
    "team_members_btn": "👤 Участники и роли",
    "team_members_title": "👤 <b>Участники</b>\n\n⭐ сделать админом или участником, 🚫 удалить.",
    "role_make_admin": "⭐ Сделать админом", "role_make_member": "👤 Сделать участником",
    "role_give_owner": "👑 Передать владение: {name}",
    "team_owner_offer": "👑 Вам предложили стать владельцем <b>{name}</b>.",
    "team_owner_offered": "Предложение отправлено — владение перейдёт после согласия.",
    "team_owner_now": "👑 Теперь вы владелец <b>{name}</b>.",
    "team_owner_declined": "Предложение владения отклонено.",
    "team_owner_must_transfer": "Перед выходом владелец должен передать команду другому участнику.",
    "team_leave_confirm": "Выйти из команды? Добавленное вами останется в команде.",
})

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

