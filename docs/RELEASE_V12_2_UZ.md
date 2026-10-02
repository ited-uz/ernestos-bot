# ErnestOS v12.2 — launch versiyasi (1000 foydalanuvchi sinovi)

Sana: 2026-10-02. Asos: egasi bergan `ErnestOS-v12.1.zip`. Konsepsiya:
**bir bosish va oson foydalanish** — eng oson yo‘l ovoz yoki matn yuborish.

## Audit qanday qilindi

- **Bot:** haqiqiy handlerlar yangi foydalanuvchi sifatida ishga tushirildi:
  `/start` → onboarding → menyu → matn/ovozli agent → tasdiqlash / tahrirlash /
  bekor qilish. AI javoblari almashtirilgan, foydalanuvchi oqimi esa haqiqiy.
- **Mini App:** haqiqiy server + Chromium 390×844. Telegram imzosi bilan kirildi,
  har bir asosiy ekran va "+" oqimi bosib ko‘rildi.
- Topilgan muammolar foydalanuvchiga ta’siri bo‘yicha saralandi.

## Eng katta 15 muammo va yechim

| # | Muammo (foydalanuvchi ko‘zi bilan) | Yechim |
|---|---|---|
| 1 | Ovoz noto‘g‘ri yoziladi — Whisper o‘zbekchani yomon eshitadi | **ElevenLabs Scribe v2** (profil tili + sizning ismlar/loyihalar lug‘ati). Kalit yo‘q yoki kredit tugasa — Groq Whisper avtomatik |
| 2 | Groq bepul limiti butun bot uchun ~87 buyruq/kun — 1000 kishida tushdan oldin tugaydi | Matn: Groq (bepul) → **Gemini** (pullik zaxira) → Groq 20b. Faqat limit yoki uzilishda o‘tadi |
| 3 | Rozilik tugmasidan keyin birinchi xabar yo‘qoladi, qayta yuborish kerak | Xabar saqlanadi va «Roziman»dan keyin o‘zi bajariladi |
| 4 | Tasdiqlash / Bekor qilish yangi xabar chiqaradi, eski tugmalar qolib ketadi | O‘sha xabar o‘zgaradi: «✅ Bajarildi», tugmalar yo‘qoladi |
| 5 | Onboarding «Akkauntingiz bormi?» deb so‘raydi va chatga parol chiqaradi | Til → ism → modullar → odatlar (4 bosish). Login kerak bo‘lsa — Sozlamalar → Akkaunt. «Login bilan kirish» tugmasi ism ekranida qoldi |
| 6 | Eski akkauntlarda `/start` bosilganda parol xabari chiqadi | Olib tashlandi |
| 7 | Asosiy imkoniyat — ovoz bilan qo‘shish — hech qayerda aytilmaydi | Onboarding yakunida misollar bilan bitta qator; Mini App «+» ichida «🎙 Ovoz bilan aytish» tugmasi |
| 8 | Onboarding oxirida ketma-ket 3–4 xabar | Bitta yakuniy xabar: kuningiz + ovoz bilan qo‘shish + kanal sharti |
| 9 | O‘zbekcha ekranda inglizcha so‘zlar: «🏠 Home», «Get up», «Deep work», «Target» | «🏠 Asosiy», «Erta turish», «Chuqur ish», «Maqsad». Til tanlanganda nomlar o‘sha tilga o‘tadi; eski akkauntlar uchun `migrations.py 0013` |
| 10 | Mini App «+»: «Ertaga soat 10 da hisobot» sanasiz, vaqtsiz saqlanadi | Sana va vaqt ajratiladi: «✓ Vazifa: hisobot · Ertaga 10:00» |
| 11 | «soat 10 da», «3 yarimda», «в 8 вечера», «at 5 pm» tushunilmaydi | Tushuniladi. Odatdagi nutq qoidasi: 7–11 ertalab, 1–6 tushdan keyin, «kechqurun» +12. Sanasiz vaqt — bugun |
| 12 | Mini App «+» orqali «Tushlik 45 ming» vazifa bo‘lib qoladi | Pulni tekshirish oynasiga o‘tadi (summa, tur, kategoriya → Saqlash) |
| 13 | Pul oynasi bosh sahifadan ochilganda kategoriya tanlovi bo‘sh | Kategoriyalar yuklanadi, eng mos biri oldindan tanlangan |
| 14 | Statistikada texnik jargon: «og‘irlik 62.5%», «Prioritet balli 0/0», formula matnlari | Kartalarda faqat natija. Formula va og‘irliklar (i) tugmasi ichida |
| 15 | Ortiqcha shovqin: bo‘sh ro‘yxatda «0 / 0 ta ish ko‘rsatilgan»; odatlari bor odamga doim «Tayyor odatlar» kartasi; «Vazifalar» va «Ko‘proq» bir xil belgida; «Odatlar tayyor: 8» va «Odatlar · 7» | Faqat kerak bo‘lganda ko‘rinadi; «Ko‘proq» uchun alohida «•••» belgisi; son olib tashlandi |

## Launch uchun qadamlar

1. PostgreSQL backupini oling.
2. Railway Variables — [ulash yo‘riqnomasi](AGENT_SETUP_UZ.md):
   - `AGENT_ENABLED=true`
   - `ELEVENLABS_API_KEY` (elevenlabs.io) — balans to‘ldirilgan
   - `GROQ_API_KEY` (console.groq.com)
   - `GEMINI_API_KEY` (aistudio.google.com) — billing yoqilgan va byudjet ogohlantirishi qo‘yilgan
3. Deploy qiling (Dockerfile; FFmpeg o‘rnatiladi).
4. Bir marta ishga tushiring: `python migrations.py 0013` — eski foydalanuvchilar
   odat nomlarini mahalliylashtiradi.
5. O‘zingiz sinang: `/start` → onboarding → ovoz «Ovqatga 5 ming» → Tasdiqlash.
   Keyin 10–20 kishi, so‘ng 1000 kishi.

## Xarajat (1000 foydalanuvchi)

Taxmin: kunlik 30% faol, har biri 3 buyruq → ~900 buyruq/kun.
**Oyiga taxminan $80–180**, asosan Gemini. Ovoz uchun ElevenLabs ~$12/oy.
Har bir foydalanuvchiga kuniga 30 buyruq limiti xarajatning yuqori chegarasi.
Batafsil: [AGENT_SETUP_UZ.md → Limit va xarajat](AGENT_SETUP_UZ.md).

## Tekshirilgani

- Python testlari: **960 o‘tdi** (v12.1: 946). Toza Python 3.12 muhitida ham
  `requirements.txt` + `constraints-tested.txt` o‘rnatildi va `pip check` toza.
- Frontend regression testlari: Toshkent va Nyu-York vaqt mintaqalarida o‘tdi.
- Yangi testlar: ElevenLabs chaqiruvi (model, til, lug‘at, cheklovlar),
  ElevenLabs → Whisper va Groq → Gemini zaxiralari, yomon javobning boshqa
  provayderda qayta so‘ralmasligi, rozilikdan keyin birinchi xabar, xabarning
  o‘zgarishi, vaqt iboralari, «+»dagi sana va pul, til tanlanganda odat
  nomlari, migratsiya 0013.
- Bot oqimi va Mini App ekranlari brauzerda qo‘lda ko‘rib chiqildi.

## Tekshirilmagani

- Haqiqiy ElevenLabs / Groq / Gemini kalitlari bilan jonli chaqiruv —
  bu muhitda kalitlar yo‘q. Birinchi jonli ovozingiz shu tekshiruv bo‘ladi.
- Docker image yig‘ilishi (bu muhitda Docker daemon yo‘q) va Railway deploy.
- Haqiqiy Telegram ilovasida (iOS/Android) end-to-end sinov.
- 1000 foydalanuvchi yuklamasi. Bitta nusxa bunga yetishi kerak:
  900 buyruq/kun ≈ daqiqasiga 1 ta.

## Qo‘shimcha: yangi navigatsiya va «Qadam»

- **Pastki menyu 4 ta:** Asosiy · Kundalik · Moliya · Profil.
  - **Kundalik** tepasida katta «Odatlar | Vazifalar» almashtirgichi bor.
  - **Profil** tepasida avatar, ⚙️ Sozlamalar va «Statistika | Jamoa» almashtirgichi bor.
  - Pastki menyu doim joyida turadi, chiqish bir bosish.
- **Pastda o‘ngda katta ➕ va uning ustida 🎙.**
  - ➕ oynasida «Vazifa | Odat» tanlovi bor.
  - Vazifa matnidan sana va vaqt avtomatik olinadi. Pulga oid matn pul tekshiruviga o‘tadi.
  - Moliya ekranida ➕ to‘g‘ridan-to‘g‘ri pul qo‘shadi.
  - «Batafsil» tugmasi to‘liq formani ochadi.
  - Har ekranda bitta ➕ bor, ikkitasi emas.
- **«Qadam» kartasi** Statistika tepasida:
  - jami qadamlar, daraja va 5 bosqichli yo‘l (Boshlovchi → O‘z ustida ishlash);
  - bugungi qadamlar: Vazifa, Odat, Namoz, Kundalik, Maqsad;
  - ❄️ ketma-ketlikni saqlab qoluvchi kunlar.
- Jamoa nomi namunasi: «Masalan: Savdo jamoasi», shaxsiy ismlar olib tashlandi.
- **Bosishlar soni:** vazifa qo‘shish 2 ta, odat 2–3 ta, odatni belgilash 1–2 ta, sozlamalar 2 ta.
