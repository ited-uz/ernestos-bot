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

- Python testlari: **978 o‘tdi** (v12.1: 946). Toza Python 3.12 muhitida ham
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

## Qo‘shimcha: yangi navigatsiya

- **Pastki menyu 4 ta:** Asosiy · Kundalik · Moliya · 👣 Qadam.
  - **Kundalik** tepasida katta «Odatlar | Vazifalar» almashtirgichi bor.
  - **Qadam** tepasida avatar, daraja, ⚙️ Sozlamalar va «Qadam | Statistika | Jamoa» almashtirgichi bor.
- **Pastda o‘ngda katta ➕ va uning ustida 🎙.**
  - ➕ oynasida «Vazifa | Odat» tanlovi bor. Matndan sana va vaqt avtomatik olinadi.
  - «Batafsil» yozilgan matnni saqlab, to‘liq formani ochadi.
  - 🎙: yozish → to‘xtatish → karta → ✅ Tasdiqlash / 🔄 Qayta / ✖ Bekor.
- **Bosishlar soni:** vazifa qo‘shish 2 ta, odat 2–3 ta, odatni belgilash 1–2 ta, sozlamalar 2 ta.

## 9 bandlik so‘rov bo‘yicha o‘zgarishlar

1. **Agent ma’noni tushunadi.** Prompt avval nutqni tiklaydi, keyin mavzuni o‘ylaydi
   («turnik» → «tortilish», «do‘stim bilan ko‘rishish» → vazifa, savolsiz).
   Karta o‘qishga oson: sarlavha, qalin nom, bitta qatorda tafsilotlar.
2. **Asosiy ekran:** birinchi «Hozir» kartasi, keyin bitta qator hisob, 🎯 hafta
   maqsadi va faqat keyingi 3 ta ish.
3. **➕ va 🎙** — yuqoridagidek.
4. **Kundalik sozlamalari:**
   - Odat oynasida har bir sozlama bosilgan zahoti saqlanadi, oyna yopilmaydi.
     «Saqlash» tugmalari yo‘q. Eslatma chiplari 07:00 / 12:00 / 21:00 yoki istalgan vaqt.
   - Yangi odat: muhimlik, kunlar va eslatma chiplar bilan tanlanadi.
   - Vazifa: eslatma va takrorlanish ro‘yxat emas, chip. Yopiq bo‘limda nima
     sozlangani ko‘rinadi («🕐 10:00 · 🔔 30 daq · 🔁 Har kuni»). Bugungi vazifa
     «Bugun» chipida ochiladi.
5. **«Kun xulosasi»** (oldingi «Kundalik» jurnali):
   - «✨ AI bilan to‘ldirish»: 🎙 Gapirish yoki ✍️ Yozish.
   - AI erkin gapni 5 savolga ajratadi, adabiy tilda to‘g‘rilaydi, mos gap bo‘lmasa
     savolni bo‘sh qoldiradi. Hech narsa o‘ylab topmaydi.
   - Javoblar maydonlarga tushadi, siz tekshirib saqlaysiz.
   - Rozilik va kunlik limit agent bilan bir xil.
6. **Hafta maqsadi** Asosiy ekranda ko‘rinadi.
7. **Moliya:**
   - Bitta karta: balans, shu oy kirim/chiqim, ochiq qarzlar.
   - Undan keyin tezkor yozish, so‘ng «Yozuvlar | Kategoriyalar | Qarzlar».
   - **Qarzlar** (yangi):
     - «Men berdim / Men oldim», ism, summa, «qachongacha» chiplari.
     - Bir bosishda yopiladi va bekor qilsa bo‘ladi. Qisman qaytarishni ham yozsa bo‘ladi.
     - Muddati o‘tgani qizil bilan belgilanadi.
     - Qarz balansga qo‘shilmaydi, chunki qarz xarajat emas.
     - Agent «Azizga 200 ming qarz berdim»ni xarajat emas, qarz deb yozadi.
   - Tadqiqot: foydalanuvchilarning eng ko‘p tashlab ketish sababi — ilovadan
     yetarlicha foydalanmaslik (28%). Shu sababli yozish tezligi birinchi o‘rinda.
     33% foydalanuvchi qarzlarni kuzatishni xohlaydi.
8. **Profil → 👣 Qadam:**
   - Bitta daraja tizimi; XP darajasi ekrandan olib tashlandi.
   - Bitta qatorda ketma-ketlik, muzlatish va reyting.
   - Bugungi 5 qadam: bosilsa, o‘sha qadam bajariladigan joy ochiladi.
   - «Keyingi qadam» tugmasi, yo‘l va yutuqlar.
   - Bugungi sonlar endi jonli o‘qiladi; avval birinchi ochilishda 0/0 chiqardi.
9. **KISS:** Statistika va Moliyadagi takror bloklar olib tashlandi.

## Agent qanday ishlaydi (qadam-baqadam)

1. **Kirish.** Siz botga yoki ilovadagi 🎙 ga gapirasiz yoki yozasiz. Hech narsa hali bajarilmaydi.
2. **Ovoz → matn.** ElevenLabs Scribe v2 profilingiz tilida, ismlaringiz va kundalik
   so‘zlar lug‘ati bilan eshitadi. Limit tugasa, Groq Whisper zaxira bo‘ladi.
3. **Matn → ma’no.** Groq gpt-oss-120b (zaxira: Gemini, keyin gpt-oss-20b) xom matnni
   tiklaydi: shevani, noto‘g‘ri harflarni, ruscha so‘zlarni tushunadi.
   Mavzuga qarab so‘zni tanlaydi. Natija qat’iy JSON reja va «tushundim» jumlasi.
4. **Tekshiruv.** Server rejani o‘zi tekshiradi:
   - faqat ruxsat etilgan amallar va maydonlar;
   - sana va summa chegaralari;
   - faqat sizning yozuvlaringiz;
   - til profilingiz bilan bir xil bo‘lishi kerak.
   Model o‘ylab topgan ID rad etiladi.
5. **Karta.** Siz «nima bo‘lishini» ko‘rasiz: «📝 Yangi vazifa · **Do‘st bilan
   uchrashuv** · 📅 Ertaga ⏰ 17:00». Xom transkript ko‘rsatilmaydi.
6. **Tasdiqlash.** ✅ bosilgandagina bitta tranzaksiyada bajariladi. Ikki marta bosilsa,
   bir marta bajariladi. 🔄 qayta yozadi, ✖ bekor qiladi. Natija: «✅ Tasdiqlandi».

**Aniqlik haqida rost gap.** 97% ni kafolatlab bo‘lmaydi. Bu jonli sinovda
o‘lchanadi, chunki o‘zbek nutqini aniqlash shovqin va shevaga bog‘liq. Himoya
shundaki, xato tushunilgan buyruq karta bosqichida ko‘rinadi va siz tasdiqlamasangiz
hech narsa o‘zgarmaydi. Kun xulosasida esa javoblar faqat maydonlarga tushadi.
