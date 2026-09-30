# Ernest agenti: 100 dan 100 000 foydalanuvchigacha — qaror

2026-09-30. Qisqasi: **bepul AI faqat sinov uchun. 100 foydalanuvchidan
boshlab pullik provayder kerak. Kod hozirdanoq provayder almashtirishga va
zaxira provayderga tayyor. Ko‘p serverga o‘tish uchun 4 ta to‘siq bor, ular
5 000 faol foydalanuvchigacha yopilishi kerak.**

## 1. Raqamlar

Bitta buyruq (o‘lchangan): tizim ko‘rsatmasi ~1 060 token, foydalanuvchi
yozuvlari (36 ta) ~1 620, JSON sxema ~410, javob ~300 → **~3 400 token**.
Ovozli bo‘lsa, qo‘shimcha ~15 soniya audio.

| Provayder | Bepul limit (butun bot uchun) | Bot uchun amalda |
|---|---|---|
| Groq `gpt-oss-120b` | 200 000 token/kun, 8 000 token/daqiqa | **~58 buyruq/kun, ~2 buyruq/daqiqa** |
| Groq `whisper-large-v3` | 2 000 so‘rov/kun, 28 800 audio-soniya/kun | ~1 900 ovoz/kun |
| Gemini Flash (bepul) | ~250 so‘rov/kun (limitlar o‘zgarib turadi) | ~250 buyruq/kun |

Groq limiti **API kalitga emas, hisobga** qo‘yiladi: ko‘p kalit ochish yordam
bermaydi. Gemini’ning bepul tarifida yuborilgan ma’lumotdan Google mahsulotlarini
yaxshilashda foydalanilishi mumkin.

Xulosa: bepul tarif birga ~60–300 buyruq/kun beradi. Bu **siz va 10–20 ta
sinovchi** uchun. 100 foydalanuvchi × 5 buyruq = 500/kun — bepul tarifga sig‘maydi.

## 2. Yuklama va xarajat

Taxmin: faol foydalanuvchi kuniga 5 buyruq, kunlik faollik 20%.

| Foydalanuvchi | Buyruq/kun | Eng yuqori yuklama | AI xarajati (taxmin) |
|---|---|---|---|
| 100 | 100 | sezilmaydi | $3–10/oy |
| 5 000 | 5 000 | ~1 buyruq/soniya | $150–450/oy |
| 100 000 | 100 000 | ~10–15 buyruq/soniya | $3 000–9 000/oy |

Bir buyruq narxi taxminan **$0.001–0.003**. Bu provayder va modelga bog‘liq,
aniq narxni provayderning narxlar sahifasida tekshiring. Faol foydalanuvchi
uchun AI xarajati oyiga **$0.15–0.45**.

Biznes qarori: ovozli agent **pullik obunaga** kirsin yoki bepul foydalanuvchiga
kunlik limit qo‘yilsin (masalan, 5–10 buyruq). Obuna narxi ≥ $2–3/oy bo‘lsa,
AI xarajati daromadning 10–20% ichida qoladi.

## 3. Arxitektura qarori

**Provayder — almashtiriladigan qism, platforma emas.** Hech bir provayderga
bog‘lanib qolmaymiz:

- `AGENT_PROVIDER` — asosiy, `AGENT_FALLBACK_PROVIDER` — zaxira. Asosiysi 429
  (limit) yoki xato qaytarsa, so‘rov zaxiraga ketadi. Limitga tushgan provayder
  `retry-after` vaqti davomida (5–300 soniya) chetlab o‘tiladi.
- Gemini ovozni o‘zi eshitadi: bitta so‘rovda ham matn, ham buyruq. Groq ikki
  so‘rov ishlatadi (Whisper, keyin matn modeli).
- `AGENT_MAX_CONCURRENT` bir jarayondan bir vaqtda ketadigan AI so‘rovlarini
  cheklaydi. Keskin yuklamada so‘rovlar navbatda kutadi, provayder va
  ulanishlar to‘lib ketmaydi.
- `AGENT_FREE_ONLY=true` — pullik provayder tasodifan yoqilmaydi. Pullikka
  o‘tish ongli qaror: provayder panelida xarajat chegarasini qo‘ygandan keyin.
- Javob tili foydalanuvchining sozlamasidan olinadi va kod darajasida
  tekshiriladi. Model boshqa tilda savol qaytarsa, u to‘g‘ri tildagi standart
  savol bilan almashtiriladi.

**Qaysi provayder asosiy bo‘lishini sizning 50 ta haqiqiy ovozingiz hal qiladi.**
Ikkala provayder bilan `evaluate_agent.py --audio ...` ishga tushiriladi va
buyruq aniqligi solishtiriladi. Taxminimiz: o‘zbekcha ovozda Gemini Whisper’dan
yaxshi. Bu hali o‘lchanmagan.

## 4. Bosqichlar va o‘tish shartlari

**0-bosqich — hozir (sinov, ≤ 20 kishi).** Bu o‘zgarish bilan tayyor.
`AGENT_PROVIDER=gemini`, `AGENT_FALLBACK_PROVIDER=groq`, ikkala bepul kalit.
50 ta ovoz bilan provayderni tanlash.

**1-bosqich — 100 dan 5 000 foydalanuvchigacha.** Kod o‘zgarmaydi, faqat sozlama:
- Asosiy provayderda pullik tarif va xarajat chegarasi, `AGENT_FREE_ONLY=false`.
- `AGENT_DAILY_REQUESTS` obuna rejasiga mos qilinadi.
- Bitta server nusxasi yetadi (5 000 buyruq/kun ≈ 1 buyruq/soniya dan past).
- `AGENT_MAX_CONCURRENT=16`.

**2-bosqich — 5 000+ faol foydalanuvchi, 2+ server nusxasi.** Quyidagi 4 ta
to‘siq yopilmaguncha ikkinchi nusxani ishga tushirmang:
1. **Suhbat bosqichi faqat xotirada** (`ctx.user_data`, `app.py`da 53 joyda).
   Webhook’da keyingi xabar boshqa nusxaga tushsa, forma yo‘qoladi. Yechim:
   PostgreSQL’da saqlash (python-telegram-bot persistence).
2. **So‘rovlar limiti xotirada** (`ratelimit.py` → `InMemoryRateLimiter`).
   Yechim: Redis limiter (interfeys tayyor, bitta klass).
3. **Polling** faqat bitta nusxada ishlaydi. `WEBHOOK_URL` va `WEBHOOK_SECRET`
   bilan webhook’ga o‘tish (kodda bor).
4. **AI ishi Telegram so‘rovi ichida bajariladi** (5–15 soniya). Yechim:
   PostgreSQL navbati (`FOR UPDATE SKIP LOCKED`) va alohida worker jarayonlari.
   Bot "qabul qilindi" deb darhol javob beradi, natijani worker yuboradi.
   Qoralama, versiya va lease mexanizmi (`agent_core`) buning uchun tayyor.

Allaqachon ko‘p nusxaga chidamli: rejalashtirilgan ishlar (PostgreSQL advisory
lock, `scheduler.py`) va agent tasdiqlari (idempotent, versiya tekshiruvi).

**3-bosqich — 50 000+.** PostgreSQL oldiga PgBouncer, worker’larni AI kvotasiga
qarab kengaytirish, provayder bilan korporativ limit kelishuvi.

## 5. Nima qilinmaydi

- Bepul tarif bilan 100+ foydalanuvchiga xizmat va’da qilinmaydi.
- O‘z GPU serverimizda model yuritilmaydi. 100 000 foydalanuvchida ham API
  arzonroq va ishonchliroq. Qayta ko‘rib chiqish sharti: AI xarajati oyiga
  $10 000 dan oshsa.
- 98% so‘z aniqligi va’da qilinmaydi. Maqsad — **buyruq aniqligi** (to‘g‘ri
  amal, summa, ism, sana), sizning ovozlaringizda o‘lchanadi.

Manbalar: [Groq rate limits](https://console.groq.com/docs/rate-limits),
[Gemini API pricing](https://ai.google.dev/pricing).
