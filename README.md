# ErnestOS

Telegram ichida ishlaydigan shaxsiy tizim: **bot + Mini App + bitta PostgreSQL bazasi**.

Botda qilgan har qanday o'zgarish Mini App'da darrov ko'rinadi va aksincha —
ikkalasi ham bir xil biznes qatlamiga (`services.py`) murojaat qiladi.

| Bo'lim | Mazmuni |
|---|---|
| 🏠 Home | Bugungi vazifalar, odatlar, haftalik 3 maqsad, loyihalar, tug'ilgan kunlar |
| ✅ Odatlar | Odatlar + namoz + kundalik |
| ⚡ Vazifalar | Vazifa va loyihalar, muddat bilan |
| 🎯 Maqsadlar | Ultimate / Milestone / Tactical |

Uch til (o'zbek, ingliz, rus), olti tema. Har kuni **04:00** va **21:00** da hisobot.

---

# ErnestOS Setup From Zero

Noldan ishga tushirish. Har bir qadamni tartib bilan bajaring.

## A. Telegram botni yaratish

1. Telegram'da **[@BotFather](https://t.me/BotFather)** ni oching.
2. `/newbot` yuboring.
3. Bot nomini kiriting (masalan `ErnestOS`).
4. Username kiriting — `_bot` bilan tugashi shart (masalan `ernestos_bot`).
5. BotFather **tokenni** beradi:
   ```
   8123456789:AAF-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
   Buni `BOT_TOKEN` ga yozasiz. **Hech kimga bermang.**

6. Buyruqlarni sozlang — `/setcommands`, botni tanlang, keyin:
   ```
   start - ErnestOS'ni ishga tushirish
   home - Bugungi holat
   ```

## B. Majburiy obuna kanalini yaratish

1. Telegram → **New Channel** → nom bering (masalan `Ernestly`).
2. Kanal turini tanlang (Public yoki Private — ikkalasi ham ishlaydi).
3. Kanal → **Administrators** → **Add Admin** → botingizni qo'shing.
4. Botga kamida shu huquqlarni bering:
   - ✅ **Manage messages** (a'zolikni o'qish uchun kerak)

### `REQUIRED_CHANNEL_ID` ni topish

Taklif havolasi (`https://t.me/+abc...`) **chat_id emas.** Raqamli ID kerak:

**Usul 1 — kanalga xabar yuborib:**
1. Kanalga istalgan xabar yozing.
2. Xabarni **[@userinfobot](https://t.me/userinfobot)** ga forward qiling.
3. U `-100xxxxxxxxxx` ko'rinishidagi ID beradi.

**Usul 2 — API orqali:**
1. Kanalga xabar yozing.
2. Brauzerda oching:
   ```
   https://api.telegram.org/bot<BOT_TOKEN>/getUpdates
   ```
3. Javobdan `"chat":{"id":-100xxxxxxxxxx` ni toping.

`REQUIRED_CHANNEL_URL` — foydalanuvchi bosadigan havola
(`https://t.me/kanal_username` yoki taklif havolasi).

### chat_member yangilanishlarini yoqish

Bot kanaldan chiqishlarni **darhol** bilishi uchun `chat_member` yangilanishi kerak.
ErnestOS buni avtomatik so'raydi (`allowed_updates` ro'yxatida) — qo'shimcha
sozlash shart emas. Faqat bot kanalda **administrator** bo'lishi kerak.

## C. Admin log kanalini yaratish

1. Yangi **private** kanal yarating (masalan `ErnestOS Logs`).
2. Botni administrator qiling — **Post messages** huquqi bilan.
3. `REQUIRED_CHANNEL_ID` bilan bir xil usulda raqamli ID ni oling.
4. `ADMIN_LOG_CHANNEL_ID` ga yozing.

**Sinov:** ilova ishga tushgach botga `/start` yuboring — kanalga
`🆕 NEW ERNESTOS USER` xabari kelishi kerak.

> Bu kanal maxfiy infratuzilma. Foydalanuvchilarga ko'rsatmang va uni
> sozlash imkonini bermang.

## D. PostgreSQL bazasini yaratish

### Railway (tavsiya qilinadi)

1. [railway.app](https://railway.app) → **New Project**.
2. **Provision PostgreSQL** ni bosing.
3. PostgreSQL servisiga kiring → **Variables** → `DATABASE_URL` ni nusxalang.

Railway shunday beradi:
```
postgresql://postgres:PAROL@monorail.proxy.rlwy.net:12345/railway
```

ErnestOS uni avtomatik `postgresql+psycopg://` ga aylantiradi — qo'lda
o'zgartirish shart emas.

### Neon / Supabase

1. Loyiha yarating.
2. Connection string ni oling (Neon: *Connection Details*, Supabase: *Settings → Database*).
3. Xuddi shu formatda `DATABASE_URL` ga yozing.

### Lokal PostgreSQL

```bash
createdb ernestos
```
```
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ernestos
```

### Sxema qanday yaratiladi

Ilova ishga tushganda **yetishmayotgan jadvallarni avtomatik yaratadi**
(`db.init_db()`). Alembic yo'q — bu loyiha uchun ortiqcha.

Qo'lda yaratmoqchi bo'lsangiz:
```bash
python -c "import db; db.init_db()"
```

Bu buyruq mavjud jadval yoki ustunga **tegmaydi** — faqat yo'qlarini qo'shadi.

## E. Eski bazani tozalash

> ⚠️ **DIQQAT: bu barcha ma'lumotni o'chiradi.** Qaytarib bo'lmaydi.

Eski ErnestOS sxemasi bu versiya bilan mos emas (AI, valyuta, `Note`,
`Contact`, `Countdown` jadvallari olib tashlangan, `user_id` o'rniga
`workspace_id` ishlatiladi). Toza boshlash eng xavfsizi.

**Railway'da:** PostgreSQL servisi → **Data** → **Query** oynasida:

```sql
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
```

**psql orqali:**
```bash
psql "$DATABASE_URL" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
```

Keyin ilovani qayta ishga tushiring — jadvallar noldan yaraladi.

Ma'lumotni saqlab qolmoqchi bo'lsangiz, avval zaxira oling:
```bash
pg_dump "$DATABASE_URL" > ernestos-backup.sql
```

## F. Muhit o'zgaruvchilari

`.env.example` dan nusxa oling:

```bash
cp .env.example .env
```

| O'zgaruvchi | Nima | Majburiymi |
|---|---|---|
| `BOT_TOKEN` | BotFather bergan token | ✅ ha |
| `DATABASE_URL` | PostgreSQL ulanish satri | ✅ production'da |
| `REQUIRED_CHANNEL_ID` | Majburiy kanal raqamli ID (`-100...`) | bo'sh = obuna tekshirilmaydi |
| `REQUIRED_CHANNEL_URL` | Foydalanuvchi bosadigan havola | tavsiya etiladi |
| `ADMIN_LOG_CHANNEL_ID` | Log kanali raqamli ID | bo'sh = log yuborilmaydi |
| `WEBAPP_URL` | Mini App'ning https manzili | Mini App uchun |
| `ENVIRONMENT` | `production` yoki `development` | ✅ ha |
| `TZ` | `Asia/Tashkent` | tavsiya etiladi |
| `INIT_DATA_MAX_AGE` | Mini App sessiyasi umri (sekund, default 86400) | yo'q |

`ENVIRONMENT=production` bo'lsa `DATABASE_URL` majburiy — SQLite'ga
qaytish yo'q.

## G. Lokal ishga tushirish

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## H. Bazani tayyorlash

```bash
python -c "import db; db.init_db()"
```

## I. Ilovani ishga tushirish

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

Bitta jarayonda uchtasi birga ishga tushadi:
- FastAPI (Mini App va API)
- Telegram bot (polling)
- Scheduler (04:00 va 21:00)

Tekshirish:
```bash
curl http://localhost:8000/api/health     # {"ok":true}
```

## J. Mini App'ni ulash

1. **[@BotFather](https://t.me/BotFather)** → `/mybots` → botingiz.
2. **Bot Settings** → **Menu Button** → **Configure menu button**.
3. URL kiriting — `WEBAPP_URL` bilan bir xil:
   ```
   https://sizning-app.up.railway.app
   ```
4. Tugma nomini kiriting (masalan `ErnestOS`).

`WEBAPP_URL` **https** bo'lishi shart — Telegram http'ni qabul qilmaydi.
Ilova Mini App'ni `/` manzilida o'zi beradi, alohida hosting kerak emas.

## K. Railway'ga deploy

1. Kodni GitHub'ga yuboring.
2. Railway → **New Project** → **Deploy from GitHub repo**.
3. PostgreSQL qo'shing (D bo'limi).
4. **Variables** bo'limiga F bo'limidagi hammasini kiriting.
   `DATABASE_URL` ni Railway o'zi qo'shadi.
5. **Settings** → **Networking** → **Generate Domain**.
   Chiqqan manzilni `WEBAPP_URL` ga yozing.

Buyruqlar:

| | |
|---|---|
| Build | *(bo'sh — Railway `requirements.txt` ni o'zi o'rnatadi)* |
| Start | `uvicorn app:app --host 0.0.0.0 --port $PORT` |

`Procfile` da ham shu yozilgan, ya'ni Railway avtomatik topadi.

> **Muhim:** bitta instansiya ishlatilsin. Ikkita bo'lsa Telegram polling
> to'qnashadi va hisobotlar ikki marta ketishi mumkin.

## L. Hammasini tekshirish

Deploy'dan keyin quyidagilarni birma-bir bosib chiqing:

```
[ ] /start bosilganda PostgreSQL'da foydalanuvchi yaratiladi
[ ] telefon raqamni ulashish ishlaydi
[ ] boshqa odamning kontaktini yuborsa qabul qilinmaydi
[ ] til tanlash ishlaydi (uz / en / ru)
[ ] jins tanlash ishlaydi
[ ] kanalga obuna bo'lmaganda kirish bloklanadi
[ ] kanaldan chiqilganda darhol bloklanadi va xabar keladi
[ ] kanalga qayta qo'shilganda ochiladi, ma'lumot joyida
[ ] admin log kanaliga "NEW ERNESTOS USER" keladi
[ ] 🏠 Home to'g'ri chiqadi
[ ] ✅ Odatlar — 6 ta standart odat bor
[ ] odat bosilganda belgilanadi/olib tashlanadi
[ ] 5x namoz bosilmaydi (qulf belgisi)
[ ] odat qo'shish va o'chirish ishlaydi
[ ] namoz belgilanganda ball hisoblanadi
[ ] ball 2.5 dan oshganda 5x namoz avtomatik belgilanadi
[ ] ayol foydalanuvchida "Uzrli" tugmasi bor, Jamoat/Qazo yo'q
[ ] ⚡ Vazifalar — qo'shish, muddat, loyiha tanlash ishlaydi
[ ] "Alohida" tanlansa loyihasiz saqlanadi
[ ] loyiha o'chirilganda vazifalari qolib ketadi
[ ] 🎯 Maqsadlar — uch daraja, qo'shish/bajarish/o'chirish
[ ] ⚙️ Sozlamalar — til, jins, tema o'zgaradi
[ ] 💬 Taklif admin kanalga yetib boradi
[ ] Mini App ochiladi va bot bilan bir xil ma'lumotni ko'rsatadi
[ ] Mini App'da qilingan o'zgarish botda ko'rinadi
[ ] 04:00 da ertalabki hisobot keladi
[ ] 21:00 da kechki hisobot keladi
[ ] ilova qayta ishga tushsa hisobot takrorlanmaydi
```

---

# Loyiha tuzilishi

```
ernestos/
├── app.py              FastAPI + bot handlerlar + scheduler + admin log
├── services.py         umumiy biznes qatlami (bot va API ikkalasi ishlatadi)
├── db.py               SQLAlchemy modellari + engine
├── webapp/
│   └── index.html      Mini App
├── tests/
│   └── test_smoke.py   41 ta test
├── requirements.txt
├── Procfile
├── .env.example
└── README.md
```

# Testlar

```bash
python -m pytest tests/ -q
```

Qamrov: foydalanuvchi izolyatsiyasi, Telegram initData tekshiruvi, egalik
nazorati, obuna bloki, namoz balli, hisobot takrorlanmasligi.

# Xavfsizlik

- Mini App `initData` server tomonda HMAC bilan tekshiriladi;
  `telegram_id` **faqat imzolangan ma'lumotdan** olinadi, JSON body'dan emas.
- Har bir so'rov `workspace_id` bo'yicha cheklanadi — begona ma'lumotga
  murojaat 404 qaytaradi va mavjudligini oshkor qilmaydi.
- Telefon raqam faqat `contact.user_id == from_user.id` bo'lganda saqlanadi.
- Ichki xatolik hech qachon foydalanuvchiga qaytmaydi.
- Admin kanalga sir, token yoki stack trace yuborilmaydi.
