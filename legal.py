"""Public pages the stores and the law ask for: the privacy policy and a web
page to delete an account without the app (Google Play's account-deletion
rule). Plain HTML in Uzbek, Russian and English, readable on a phone, in
light and dark, with nothing to load from elsewhere.

The deletion page works by email: the address gets a code, the code deletes
the account it belongs to — the same proof of ownership as signing in.
Telegram-only accounts are pointed to the app and the bot, which know them.
"""
from __future__ import annotations

import html
import os

LANGS = ("uz", "ru", "en")

#: Updated by hand whenever the policy text below changes.
POLICY_DATE = "2026-10-13"


def contact() -> str:
    email = os.environ.get("SUPPORT_EMAIL", "").strip()
    bot = os.environ.get("BOT_USERNAME", "").strip().lstrip("@")
    parts = []
    if email:
        parts.append(f'<a href="mailto:{html.escape(email)}">{html.escape(email)}</a>')
    if bot:
        parts.append(f'<a href="https://t.me/{html.escape(bot)}">@{html.escape(bot)}</a>')
    return " · ".join(parts)


def lang_of(raw: str | None) -> str:
    return raw if raw in LANGS else "uz"


_CSS = """
:root{--bg:#fff;--text:#111827;--text-2:#4B5563;--line:#E5E7EB;--card:#F8F9FA;--accent:#0062CC;
      --on-accent:#fff;--danger:#B00012;--ok:#1F7A36}
@media (prefers-color-scheme:dark){:root{--bg:#1C1C1E;--text:#EBEBF0;--text-2:#B8B8BE;--line:#38383A;
      --card:#242426;--accent:#4DA3FF;--on-accent:#0B1220;--danger:#FF6B61;--ok:#4ADE80}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
     font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans",Arial,sans-serif}
main{max-width:720px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:26px;line-height:1.25;margin:8px 0 4px} h2{font-size:19px;margin:28px 0 6px}
p,li{color:var(--text-2)} b,strong{color:var(--text)} a{color:var(--accent)}
.meta{font-size:14px;color:var(--text-2)}
.langs{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 8px}
.langs a{padding:6px 12px;border:1px solid var(--line);border-radius:16px;text-decoration:none;font-size:14px}
.langs a[aria-current="page"]{background:var(--accent);color:var(--on-accent);border-color:transparent}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;margin:14px 0}
label{display:block;font-weight:600;margin:12px 0 4px}
input{width:100%;min-height:48px;border:1px solid var(--line);border-radius:12px;padding:0 14px;
      font:inherit;background:var(--bg);color:var(--text)}
input:focus{outline:3px solid color-mix(in srgb,var(--accent) 40%,transparent);border-color:var(--accent)}
button{min-height:48px;border:0;border-radius:12px;padding:0 18px;font:inherit;font-weight:650;
       background:var(--accent);color:var(--on-accent);cursor:pointer;margin-top:12px;width:100%}
button.danger{background:var(--danger);color:#fff}
button[disabled]{opacity:.55}
.msg{min-height:24px;margin:10px 0 0;font-weight:600}
.msg.err{color:var(--danger)} .msg.ok{color:var(--ok)}
table{border-collapse:collapse;width:100%;font-size:15px} td,th{border-bottom:1px solid var(--line);padding:8px 6px;
      text-align:left;vertical-align:top} th{color:var(--text)}
"""


def _page(lang: str, title: str, body: str, path: str) -> str:
    names = {"uz": "O'zbekcha", "ru": "Русский", "en": "English"}
    current = ' aria-current="page"'
    switch = "".join(
        f'<a href="{path}?lang={code}"{current if code == lang else ""}>{name}</a>'
        for code, name in names.items())
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="color-scheme" content="light dark">'
            f'<title>{html.escape(title)} — ErnestOS</title><style>{_CSS}</style></head>'
            f'<body><main><nav class="langs" aria-label="Language">{switch}</nav>{body}</main></body></html>')


# ---------------------------------------------------------------------------
# Privacy policy
# ---------------------------------------------------------------------------

_PRIVACY = {
    "uz": {
        "title": "Maxfiylik siyosati",
        "updated": "Yangilangan",
        "intro": "ErnestOS — vazifa, odat, namoz, maqsad va moliya uchun shaxsiy reja ilovasi (Telegram bot, Mini App va Android ilova). Bu sahifa qanday ma'lumot yig'ilishi, nima uchun ishlatilishi va uni qanday boshqarishingizni tushuntiradi.",
        "sections": [
            ("Qanday ma'lumot yig'iladi", [
                "<b>Akkaunt:</b> Telegram ID, ism va @username (Telegram orqali kirsangiz); email manzil (email bilan kirsangiz); Google yoki Apple identifikatori va email (shu usul bilan kirsangiz).",
                "<b>Siz kiritgan narsalar:</b> vazifalar, odatlar, namoz belgilari, kun xulosasi, maqsadlar, pul yozuvlari, hisoblar, qarzlar, «Yaqinlarim» ro'yxati (ism, telefon, tug'ilgan kun — o'zingiz yozganlari), guruhlar.",
                "<b>Qurilma:</b> ilovaga kirish sessiyasi, push-bildirishnoma tokeni (Firebase), ilova xatolari haqida qisqa yozuv (xato matni, ekran, versiya — shaxsiy ma'lumotsiz).",
                "<b>Ovoz:</b> ovozli buyruq yozuvi faqat matnga aylantirish uchun yuboriladi va saqlanmaydi.",
            ]),
            ("Nima uchun ishlatiladi", [
                "Ilovaning o'zi ishlashi uchun: rejalaringizni saqlash, eslatma va hisobot yuborish, guruhda topshiriq berish.",
                "AI funksiyalari uchun (faqat siz rozi bo'lsangiz yoki o'zingiz bossangiz): ovoz/matnni tushunish, «Hozir» tavsiyasi, kun xulosasini to'ldirish, AI yordamchi javoblari.",
                "Reklama yo'q. Ma'lumotlaringiz sotilmaydi va reklama uchun ishlatilmaydi.",
            ]),
            ("Kimlarga uzatiladi (xizmat ko'rsatuvchilar)", [
                "<b>Railway</b> — server va ma'lumotlar bazasi (hosting).",
                "<b>Groq, Google (Gemini), ElevenLabs</b> — AI: matn va ovozni tushunish. Faqat AI funksiyasidan foydalansangiz.",
                "<b>Resend yoki Brevo</b> — kirish kodi xatlarini yuborish.",
                "<b>Google Firebase</b> — telefonga push-bildirishnoma.",
                "<b>Telegram</b> — bot va Mini App orqali foydalansangiz.",
                "Boshqa hech kimga berilmaydi. Qonun talab qilgan holatlar bundan mustasno.",
            ]),
            ("Saqlash muddati", [
                "Ma'lumotlar akkauntingiz bor ekan saqlanadi. Akkauntni o'chirsangiz, barcha ma'lumotlar darhol o'chiriladi.",
                "Server jurnallari (loglar) xizmat ko'rsatuvchining muddati bo'yicha (odatda 30 kungacha) saqlanadi va shaxsni aniqlovchi ma'lumot yozilmaydi.",
            ]),
            ("Sizning huquqlaringiz", [
                "<b>Ko'rish va eksport:</b> Profil → ⚙️ → Maxfiylik va ma'lumotlar → Eksport (JSON).",
                "<b>Tuzatish:</b> har qanday yozuvni ilovaning o'zida o'zgartirish mumkin.",
                "<b>O'chirish:</b> ilovada (Maxfiylik va ma'lumotlar → Akkauntni o'chirish), botda yoki <a href=\"/account/delete?lang=uz\">shu sahifada</a>.",
                "<b>Rozilikni qaytarib olish:</b> AI funksiyalarini Sozlamalarda o'chirish mumkin.",
            ]),
            ("Xavfsizlik", [
                "Barcha aloqa HTTPS orqali shifrlanadi. Kirish kodlari va sessiyalar bazada faqat xesh ko'rinishida saqlanadi.",
                "Har bir foydalanuvchining ma'lumoti alohida ish maydonida, boshqalarga ko'rinmaydi (guruhga o'zingiz qo'shgan narsalardan tashqari).",
            ]),
            ("Bolalar", ["Ilova 13 yoshdan kichiklar uchun mo'ljallanmagan."]),
        ],
        "contact": "Aloqa",
    },
    "ru": {
        "title": "Политика конфиденциальности",
        "updated": "Обновлено",
        "intro": "ErnestOS — личное приложение для задач, привычек, намаза, целей и финансов (Telegram-бот, Mini App и Android-приложение). Здесь описано, какие данные собираются, зачем и как вы ими управляете.",
        "sections": [
            ("Какие данные собираются", [
                "<b>Аккаунт:</b> Telegram ID, имя и @username (при входе через Telegram); email (при входе по email); идентификатор и email Google или Apple (при входе этим способом).",
                "<b>Что вы вводите:</b> задачи, привычки, отметки намаза, итоги дня, цели, денежные записи, счета, долги, список «Близкие» (имя, телефон, день рождения — что вы сами записали), группы.",
                "<b>Устройство:</b> сессия входа, токен push-уведомлений (Firebase), краткие записи об ошибках приложения (текст ошибки, экран, версия — без личных данных).",
                "<b>Голос:</b> запись голосовой команды отправляется только для распознавания и не хранится.",
            ]),
            ("Зачем используются", [
                "Для работы приложения: хранить планы, присылать напоминания и отчёты, давать задачи в группе.",
                "Для AI-функций (только с вашего согласия или по вашему нажатию): понимать голос и текст, совет «Сейчас», заполнение итогов дня, ответы AI-помощника.",
                "Рекламы нет. Данные не продаются и не используются для рекламы.",
            ]),
            ("Кому передаются (подрядчики)", [
                "<b>Railway</b> — сервер и база данных (хостинг).",
                "<b>Groq, Google (Gemini), ElevenLabs</b> — AI: распознавание текста и голоса. Только если вы пользуетесь AI.",
                "<b>Resend или Brevo</b> — отправка писем с кодом входа.",
                "<b>Google Firebase</b> — push-уведомления на телефон.",
                "<b>Telegram</b> — если вы пользуетесь ботом и Mini App.",
                "Больше никому, кроме случаев, предусмотренных законом.",
            ]),
            ("Срок хранения", [
                "Данные хранятся, пока существует аккаунт. При удалении аккаунта все данные удаляются сразу.",
                "Журналы сервера хранятся по сроку подрядчика (обычно до 30 дней) и не содержат данных, позволяющих установить личность.",
            ]),
            ("Ваши права", [
                "<b>Просмотр и экспорт:</b> Профиль → ⚙️ → Конфиденциальность и данные → Экспорт (JSON).",
                "<b>Исправление:</b> любую запись можно изменить в самом приложении.",
                "<b>Удаление:</b> в приложении (Конфиденциальность и данные → Удалить аккаунт), в боте или <a href=\"/account/delete?lang=ru\">на этой странице</a>.",
                "<b>Отзыв согласия:</b> AI-функции выключаются в Настройках.",
            ]),
            ("Безопасность", [
                "Вся связь шифруется (HTTPS). Коды входа и сессии хранятся в базе только в виде хеша.",
                "Данные каждого пользователя лежат в отдельном рабочем пространстве и не видны другим (кроме того, что вы сами добавили в группу).",
            ]),
            ("Дети", ["Приложение не предназначено для детей младше 13 лет."]),
        ],
        "contact": "Контакты",
    },
    "en": {
        "title": "Privacy policy",
        "updated": "Updated",
        "intro": "ErnestOS is a personal planning app for tasks, habits, prayer, goals and money (Telegram bot, Mini App and Android app). This page explains what data is collected, why, and how you control it.",
        "sections": [
            ("What we collect", [
                "<b>Account:</b> Telegram ID, name and @username (if you sign in with Telegram); email address (if you sign in with email); Google or Apple identifier and email (if you sign in that way).",
                "<b>What you enter:</b> tasks, habits, prayer ticks, day summaries, goals, money entries, accounts, debts, your «Close people» list (name, phone, birthday — what you typed), groups.",
                "<b>Device:</b> sign-in session, push token (Firebase), short app error reports (error text, screen, version — no personal data).",
                "<b>Voice:</b> a voice command recording is sent only to be transcribed and is not stored.",
            ]),
            ("Why we use it", [
                "To run the app: keep your plans, send reminders and reports, assign tasks in a group.",
                "For AI features (only with your consent or when you tap them): understanding voice and text, the «Now» suggestion, filling the day summary, AI assistant answers.",
                "No ads. Your data is not sold and not used for advertising.",
            ]),
            ("Who processes it (service providers)", [
                "<b>Railway</b> — server and database hosting.",
                "<b>Groq, Google (Gemini), ElevenLabs</b> — AI: understanding text and voice. Only if you use AI features.",
                "<b>Resend or Brevo</b> — sending sign-in code emails.",
                "<b>Google Firebase</b> — push notifications to the phone.",
                "<b>Telegram</b> — if you use the bot and Mini App.",
                "No one else, except where the law requires it.",
            ]),
            ("How long we keep it", [
                "Data is kept while your account exists. When you delete the account, all of its data is deleted immediately.",
                "Server logs are kept for the provider's period (usually up to 30 days) and hold no data that identifies you.",
            ]),
            ("Your rights", [
                "<b>Access and export:</b> Profile → ⚙️ → Privacy and data → Export (JSON).",
                "<b>Correction:</b> any entry can be edited in the app.",
                "<b>Deletion:</b> in the app (Privacy and data → Delete account), in the bot, or <a href=\"/account/delete?lang=en\">on this page</a>.",
                "<b>Withdrawing consent:</b> AI features can be switched off in Settings.",
            ]),
            ("Security", [
                "All traffic is encrypted (HTTPS). Sign-in codes and sessions are stored only as hashes.",
                "Each user's data lives in its own workspace and is not visible to others (except what you add to a group yourself).",
            ]),
            ("Children", ["The app is not meant for children under 13."]),
        ],
        "contact": "Contact",
    },
}


def privacy_page(lang: str) -> str:
    lang = lang_of(lang)
    p = _PRIVACY[lang]
    body = [f'<h1>{p["title"]}</h1>',
            f'<p class="meta">{p["updated"]}: {POLICY_DATE}</p>',
            f'<p>{p["intro"]}</p>']
    for head, items in p["sections"]:
        body.append(f'<h2>{head}</h2><ul>' + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
    if contact():
        body.append(f'<h2>{p["contact"]}</h2><p>{contact()}</p>')
    return _page(lang, p["title"], "".join(body), "/privacy")


# ---------------------------------------------------------------------------
# Account deletion without the app
# ---------------------------------------------------------------------------

_DELETE = {
    "uz": dict(
        title="Akkauntni o'chirish",
        intro="Akkaunt o'chirilganda <b>barcha ma'lumotlar darhol va butunlay o'chiriladi</b>: vazifalar, odatlar, namoz, maqsadlar, moliya, qarzlar, Yaqinlarim, sessiyalar va bildirishnoma tokenlari. Buni qaytarib bo'lmaydi.",
        ways="O'chirish yo'llari",
        in_app="<b>Ilovada:</b> Profil → ⚙️ → Maxfiylik va ma'lumotlar → Akkauntni o'chirish.",
        in_bot="<b>Telegram'da:</b> botni oching → Mini App → Profil → ⚙️ → Maxfiylik va ma'lumotlar → Akkauntni o'chirish.",
        by_email="<b>Shu sahifada (email bilan kirgan akkauntlar uchun):</b>",
        email="Email manzilingiz", send="Kod yuborish", code="Xatdagi 6 xonali kod",
        confirm="Akkauntni butunlay o'chirish",
        sent="Agar bu email akkauntga ulangan bo'lsa, unga kod yuborildi. Spam papkasini ham ko'ring.",
        done="Akkaunt va undagi barcha ma'lumotlar o'chirildi.",
        bad_code="Kod noto'g'ri yoki eskirgan.", no_account="Bu email hech qaysi akkauntga ulanmagan.",
        owner="Avval o'zingiz egasi bo'lgan guruhni boshqa a'zoga topshiring (ilovada: Guruh → ⋯), so'ng qayta urining.",
        too_soon="Biroz kuting va qayta urining.", fail="Xatolik. Birozdan keyin qayta urining.",
        no_mail="Email xizmati hozir ishlamayapti. Ilova yoki bot orqali o'chiring yoki bizga yozing.",
        contact="Yordam kerakmi?"),
    "ru": dict(
        title="Удаление аккаунта",
        intro="При удалении аккаунта <b>все данные удаляются сразу и навсегда</b>: задачи, привычки, намаз, цели, финансы, долги, «Близкие», сессии и токены уведомлений. Отменить это нельзя.",
        ways="Как удалить",
        in_app="<b>В приложении:</b> Профиль → ⚙️ → Конфиденциальность и данные → Удалить аккаунт.",
        in_bot="<b>В Telegram:</b> откройте бота → Mini App → Профиль → ⚙️ → Конфиденциальность и данные → Удалить аккаунт.",
        by_email="<b>На этой странице (для аккаунтов с входом по email):</b>",
        email="Ваш email", send="Отправить код", code="6-значный код из письма",
        confirm="Удалить аккаунт навсегда",
        sent="Если этот email привязан к аккаунту, на него отправлен код. Проверьте и папку «Спам».",
        done="Аккаунт и все его данные удалены.",
        bad_code="Код неверный или устарел.", no_account="Этот email не привязан ни к одному аккаунту.",
        owner="Сначала передайте свою группу другому участнику (в приложении: Группа → ⋯), затем повторите.",
        too_soon="Подождите немного и повторите.", fail="Ошибка. Повторите чуть позже.",
        no_mail="Почта сейчас недоступна. Удалите аккаунт в приложении или в боте, либо напишите нам.",
        contact="Нужна помощь?"),
    "en": dict(
        title="Delete your account",
        intro="Deleting the account <b>removes all of its data immediately and for good</b>: tasks, habits, prayer, goals, money, debts, Close people, sessions and notification tokens. This cannot be undone.",
        ways="Ways to delete",
        in_app="<b>In the app:</b> Profile → ⚙️ → Privacy and data → Delete account.",
        in_bot="<b>In Telegram:</b> open the bot → Mini App → Profile → ⚙️ → Privacy and data → Delete account.",
        by_email="<b>On this page (for accounts that sign in with email):</b>",
        email="Your email", send="Send code", code="The 6-digit code from the email",
        confirm="Delete my account for good",
        sent="If this email belongs to an account, a code was sent to it. Check Spam too.",
        done="The account and all of its data were deleted.",
        bad_code="The code is wrong or expired.", no_account="This email is not linked to any account.",
        owner="First hand your group to another member (in the app: Group → ⋯), then try again.",
        too_soon="Wait a moment and try again.", fail="Something went wrong. Try again shortly.",
        no_mail="Email is not available right now. Delete the account in the app or the bot, or write to us.",
        contact="Need help?"),
}


def delete_page(lang: str) -> str:
    lang = lang_of(lang)
    d = _DELETE[lang]
    msgs = {k: d[k] for k in ("sent", "done", "bad_code", "no_account", "owner", "too_soon", "fail", "no_mail")}
    import json
    body = f"""
<h1>{d["title"]}</h1>
<p>{d["intro"]}</p>
<h2>{d["ways"]}</h2>
<ul><li>{d["in_app"]}</li><li>{d["in_bot"]}</li></ul>
<p>{d["by_email"]}</p>
<div class="card">
  <form id="f1" novalidate>
    <label for="em">{d["email"]}</label>
    <input id="em" type="email" autocomplete="email" inputmode="email" required maxlength="254">
    <button id="b1" type="submit">{d["send"]}</button>
  </form>
  <form id="f2" hidden novalidate>
    <label for="cd">{d["code"]}</label>
    <input id="cd" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]*" maxlength="6" required>
    <button id="b2" class="danger" type="submit">{d["confirm"]}</button>
  </form>
  <p id="msg" class="msg" role="status" aria-live="polite"></p>
</div>
{f'<h2>{d["contact"]}</h2><p>{contact()}</p>' if contact() else ""}
<script>
(function(){{
  var M = {json.dumps(msgs, ensure_ascii=False)}, lang = {json.dumps(lang)};
  var $ = function(i){{ return document.getElementById(i); }};
  function say(t, ok){{ var m = $("msg"); m.textContent = t; m.className = "msg " + (ok ? "ok" : "err"); }}
  function post(path, body){{
    return fetch(path, {{method:"POST", headers:{{"Content-Type":"application/json"}}, body: JSON.stringify(body)}});
  }}
  $("f1").addEventListener("submit", function(e){{
    e.preventDefault();
    var email = $("em").value.trim(); if(!email) return $("em").focus();
    $("b1").disabled = true;
    post("/api/account/delete/start", {{email: email, language: lang}}).then(function(r){{
      if(r.ok){{ $("f2").hidden = false; $("cd").focus(); return say(M.sent, true); }}
      say(r.status === 503 ? M.no_mail : r.status === 429 ? M.too_soon : M.fail);
    }}).catch(function(){{ say(M.fail); }}).then(function(){{ $("b1").disabled = false; }});
  }});
  $("f2").addEventListener("submit", function(e){{
    e.preventDefault();
    $("b2").disabled = true;
    post("/api/account/delete/confirm", {{email: $("em").value.trim(), code: $("cd").value}}).then(function(r){{
      if(r.ok){{ $("f1").hidden = true; $("f2").hidden = true; return say(M.done, true); }}
      return r.json().catch(function(){{ return {{}}; }}).then(function(b){{
        var d = b && b.detail;
        say(d === "bad_code" ? M.bad_code : d === "no_account" ? M.no_account
          : d === "owner_must_transfer" ? M.owner : r.status === 429 ? M.too_soon : M.fail);
      }});
    }}).catch(function(){{ say(M.fail); }}).then(function(){{ $("b2").disabled = false; }});
  }});
}})();
</script>"""
    return _page(lang, d["title"], body, "/account/delete")
