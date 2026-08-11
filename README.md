# ErnestOS

Telegram ichida ishlaydigan shaxsiy tizim: **bot + Mini App + bitta PostgreSQL bazasi**.

Botda qilgan har qanday o'zgarish Mini App'da darrov ko'rinadi va aksincha —
ikkalasi ham bir xil biznes qatlamiga (`services.py`) murojaat qiladi.

| Bo'lim | Mazmuni |
|---|---|
| 🏠 Home | Sana, quote, 4 ta ko'rsatkich, **bitta** haftalik missiya, **faqat bugungi** vazifalar (loyiha bo'yicha), **1 oylik kalendar** |
| ✅ Odatlar | Odatlar (3 kategoriya, tartibi o'zgartiriladi) + namoz + kundalik |
| ⚡ Vazifalar | Vazifa va loyihalar, loyiha sahifasi, **Bajarilgan** arxivi |
| 📊 Statistika | Umumiy % (asosiy) + Vazifalar / Odatlar / Namoz + streak |

Home — ko'rish va tushunish uchun; tahrirlash va o'chirish o'z sahifasida.

Uch til (o'zbek, ingliz, rus), olti tema × yorug'/qorong'i.
Har kuni **04:00** va **21:00** da hisobot, **23:00** da statistika.

## Odatlar uch kategoriyada

| Kategoriya | Standart odatlar |
|---|---|
| 🔴 Non-negotiable | **Get up** · **5x namoz** |
| 🟡 Target | Deep flow · Sport |
| 🟢 Bonus | Podcast · Read |

Ikkitasi **avtomatik hisoblanadi** — qo'lda bosib bo'lmaydi:

| Odat | Nimadan hisoblanadi |
|---|---|
| **Get up** | Botga «Turdim» deb yozilganda |
| **5x namoz** | Kunlik namoz balli ≥ 2.5 bo'lganda |

Kundalik odat emas: u alohida holat sifatida ko'rsatiladi, aks holda
foydalanuvchi tanlamagan narsa maxraj va streakni o'zgartirib yuborardi.

Odatlar tartibi Mini App'dagi **↕ Tartibni o'zgartirish** rejimida
o'zgartiriladi — surib yoki ↑ ↓ tugmalari bilan. Tartib serverda saqlanadi va
bot ham aynan shu tartibda ko'rsatadi.

Odatni o'chirish uchun ro'yxat ostidagi **🗑 Odat o'chirish** tugmasi bosiladi va
o'chiriladigani tanlanadi — har bir qator yonida kichik xoch yo'q.

## Uyg'onish (Get up)

Sozlamalarda **⏰ Uyg'onish vaqti** belgilanadi (default 05:00).

Bot o'sha vaqtdan **bir soat** kutadi. Shu oraliqda botga «Turdim» deb yozilsa —
odat bajarilgan. 06:00 dan keyin yozilsa yoki umuman yozilmasa — bugun
bajarilmagan hisoblanadi.

Erta turish ham hisoblanadi: 04:45 da yozilsa ham bajarilgan bo'ladi.

## Kundalik

Har kuni beshta savol. **Hammasi to'ldirilgandagina** kun to'liq
hisoblanadi. Tarixni sana bo'yicha ko'rish mumkin.

---

## Namoz ball tizimi

Beshta namoz: Bomdod, Peshin, Asr, Shom, Xufton.

| | O'g'il bola | Qiz bola |
|---|---|---|
| Jamoat | 1 ball | — |
| O'z vaqtida | 1 ball | 1 ball |
| Qazo | 0.5 ball | 0.5 ball |
| O'qilmagan | 0 ball | 0 ball |
| **Uzrli** | — | kunlik bayroq, ball aniq **2.5** |

Kunlik ball **5 balldan** ko'rsatiladi. **≥ 2.5** bo'lsa `5x namoz` odati
avtomatik bajarilgan bo'ladi — qo'lda belgilab bo'lmaydi.

## Statistika

Odat va namoz uchun line chart — **haftalik** (7 kun), **oylik** (30 kun) va
**yillik** (12 oy). O'rtacha foiz, namoz taqsimoti va **streak** (ketma-ket
kunlar). Odatlar bo'limining Statistika tabida.


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

## C. Uchta admin kanalini yaratish

Uchta alohida **private** kanal yarating. Har birida botni administrator
qiling — **Post messages** huquqi bilan. Raqamli ID ni B bo'limidagi usul
bilan oling.

| Kanal | O'zgaruvchi | Nima keladi |
|---|---|---|
| ErnestOS Logs | `ADMIN_LOG_CHANNEL_ID` | Ro'yxatdan o'tish, odat/vazifa/loyiha o'zgarishlari, obuna |
| ErnestOS Feedback | `FEEDBACK_CHANNEL_ID` | 💬 Taklif orqali kelgan takliflar va shikoyatlar |
| ErnestOS Stats | `STATS_CHANNEL_ID` | Har kuni 23:00 da statistika: jami foydalanuvchi, oxirgi raqam (#42), DAU/WAU/MAU, til va jins taqsimoti |

Oxirgi ikkitasi majburiy emas — bo'sh qoldirsangiz hammasi
`ADMIN_LOG_CHANNEL_ID` ga boradi.

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

### Sxema qanday yaratiladi va yangilanadi

Ilova ishga tushganda:

1. yetishmayotgan **jadvallarni** yaratadi;
2. mavjud jadvallarga yetishmayotgan **ustunlarni** qo'shadi.

Ikkalasi ham faqat **qo'shadi** — hech qachon jadval yoki ustun o'chirmaydi,
tur o'zgartirmaydi. Shuning uchun uni real foydalanuvchilar ustida ishlatish
xavfsiz.

**Ya'ni yangi versiyaga o'tishda bazani o'chirish shart emas.** Deploy qilasiz,
yangi ustunlar o'zi qo'shiladi, ma'lumot joyida qoladi.

Qo'lda ishga tushirish:
```bash
python -c "import db; db.init_db()"
```

## E. Eski bazani tozalash

> **Odatda kerak emas.** Ilova yetishmayotgan ustunlarni o'zi qo'shadi, ya'ni
> yangi versiyaga o'tishda ma'lumotni yo'qotmaysiz. Quyidagi amal faqat
> butunlay noldan boshlamoqchi bo'lsangiz kerak.

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
| `ADMIN_LOG_CHANNEL_ID` | Hodisalar kanali raqamli ID | bo'sh = log yuborilmaydi |
| `FEEDBACK_CHANNEL_ID` | Taklif/shikoyatlar kanali | bo'sh = ADMIN_LOG ga boradi |
| `STATS_CHANNEL_ID` | Kunlik statistika kanali | bo'sh = ADMIN_LOG ga boradi |
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

`init_db()` faqat **qo'shadi** — yo'q jadval va yo'q ustunni yaratadi, hech
narsani o'chirmaydi. Shuning uchun uni har safar ishga tushirishda chaqirish
xavfsiz.

### Ma'lumot migratsiyalari

Ma'lumotni o'zgartiradigan qadamlar alohida va **qo'lda** ishga tushiriladi —
hech qachon import paytida yoki boot'da emas:

```bash
python migrations.py
```

Har biri raqamlangan, ikki marta ishga tushirilsa ham hech narsa
o'zgartirmaydi va foydalanuvchi tarixini o'chirmaydi:

| № | Nima qiladi | Orqaga qaytarish |
|---|---|---|
| `0001` | Kundalikni odat sifatida hisoblashni to'xtatadi (`Summary` arxivlanadi) | `archived_at` ni `NULL` qilish |
| `0002` | `goals` jadvalini `goals_archived_v1` ga ko'chiradi | `ALTER TABLE goals_archived_v1 RENAME TO goals` |

`0002` jadvalni **o'chirmaydi** — nomini o'zgartiradi. Public launch'da Goals
UI, API va modeli olib tashlandi, lekin foydalanuvchi yozgan qatorlar joyida
qoladi.

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
[ ] /start uch tilda salomlashadi, keyin til so'raydi
[ ] tartib: til -> telefon -> jins -> obuna
[ ] telefon raqamni ulashish ishlaydi
[ ] boshqa odamning kontaktini yuborsa qabul qilinmaydi
[ ] til tanlash ishlaydi (uz / en / ru)
[ ] jins tanlash ishlaydi
[ ] kanalga obuna bo'lmaganda kirish bloklanadi
[ ] kanaldan chiqilganda darhol bloklanadi va xabar keladi
[ ] kanalga qayta qo'shilganda ochiladi, ma'lumot joyida
[ ] admin log kanaliga "NEW ERNESTOS USER" keladi
[ ] 🏠 Home to'g'ri chiqadi
[ ] ✅ Odatlar — 3 kategoriyada ko'rsatiladi
[ ] kundalik to'liq to'ldirilganda kun to'liq hisoblanadi
[ ] odatlar tartibi o'zgartiriladi va qayta ochilganda saqlanib qoladi
[ ] Sozlamalarda ⬅️ Orqaga tugmasi bor
[ ] Sozlamalarda telefon va profil rasmi boshqariladi
[ ] odat bosilganda belgilanadi/olib tashlanadi
[ ] 5x namoz bosilmaydi (qulf belgisi)
[ ] odat qo'shish va o'chirish ishlaydi
[ ] namoz belgilanganda ball hisoblanadi
[ ] ball 5 dan ko'rsatiladi, 2.5 dan oshganda 5x namoz belgilanadi
[ ] ayolda Uzrli + o'z vaqtida/qazo/o'qilmagan bor, Jamoat yo'q
[ ] ⚡ Vazifalar — o'chirish emas, ✅ Bajarildi va ✏️ Tahrirlash
[ ] bajarilgan vazifa Done arxiviga tushadi
[ ] Home pastida 1 oylik kalendar deadline'lar bilan
[ ] Statistika alohida sahifa: Umumiy + Vazifalar + Odatlar + Namoz
[ ] ⏰ Uyg'onish vaqti sozlanadi
[ ] «Turdim» vaqtida yozilsa Get up belgilanadi
[ ] bir soatdan keyin yozilsa hisoblanmaydi
[ ] profil rasmi Mini App'da avatar sifatida chiqadi
[ ] statistika o'z kanaliga boradi, log kanaliga emas
[ ] Sozlamalarda «Saqlash» tugmasi bor
[ ] Namoz holatlari rangli va emojili
[ ] Statistikani CSV qilib yuklab olish ishlaydi
[ ] maxfiylik qatori nav ustida qotib turadi va kontentni yopmaydi
[ ] bot menyusi aynan 7 ta: Home/Odatlar/Vazifalar/Statistika/Sozlamalar/Taklif/Mini App
[ ] "Alohida" tanlansa loyihasiz saqlanadi
[ ] loyiha o'chirilganda vazifalari qolib ketadi
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
│   └── test_smoke.py   84 ta test
├── requirements.txt
├── Procfile
├── .env.example
└── README.md
```

# Testlar

```bash
python -m pytest tests/ -q
```

**84 test o'tadi.**

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
