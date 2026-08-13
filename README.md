# ErnestOS

Telegram ichida ishlaydigan shaxsiy tizim: **bot + Mini App + bitta PostgreSQL bazasi**.

Botda qilgan har qanday o'zgarish Mini App'da darrov ko'rinadi va aksincha —
ikkalasi ham bir xil biznes qatlamiga (`services.py`) murojaat qiladi. Formula
JavaScript'da takrorlanmaydi: botda 80%, ilovada 74% ko'rgan foydalanuvchi
ikkalasiga ham ishonmay qo'yadi.

Mahsulotning asosiy sikli:
**Rejalashtir → Bajar → Kuzat → Tahlil qil → Takrorla.**

| Bo'lim | Mazmuni |
|---|---|
| 🏠 Bosh sahifa | **Bugungi missiya** (bitta vazifa), kun/hafta/oy foizi va o'zgarishi, vazifa/odat/namoz, bugungi vazifalar, kalendar |
| ✅ Odatlar | Odatlar (3 kategoriya, jadval, pauza, tarix) + namoz + kundalik |
| ⚡ Vazifalar | Qidiruv va filtr, kechikkanlar uchun tez ko'chirish, loyihalar, **Bajarilgan** arxivi |
| 📊 Statistika | Umumiy % + Vazifalar / Odatlar / Namoz, o'zgarish (↑↓), eng yaxshi kun, namoz tafsiloti |

Pastdagi navigatsiya aynan shu to'rtta. Qolgan hamma narsa — kalendar,
tug'ilgan kunlar, haftalik yakun, sozlamalar, export — kerakli joydan
ochiladi, alohida tab sifatida emas.

Uch til (o'zbek, ingliz, rus) **to'liq** tarjima qilingan, beshta dizayn
uslubi × yorug'/qorong'i rejim.

## Bosh sahifa — bir ochishda ko'rinadigan narsa

Bosh sahifa faqat to'rt blok, shu tartibda:

1. salom, sana, quote, sozlamalar va rasm;
2. **Bugungi missiya** — bitta vazifa, mavjud vazifalardan tanlanadi;
3. **kun / hafta / oy** foizi, o'sish yoki pasayish belgisi bilan, va bitta
   qatorda vazifa / odat / namoz;
4. loyihalarga bo'lingan **bugungi vazifalar**, pastida **kalendar**.

Boshqa hamma narsa — haftaning fokusi, tug'ilgan kunlar, haftalik yakun —
o'ziga tegishli ekranda. Maqsad: ochish bilan kerakli narsani ko'rish.

## Bugungi missiya

"Bugun eng muhim ish nima?" savolining javobi **bitta** yoki yo'q. Uchta
"eng muhim" ish — bu ro'yxat.

Missiya mavjud vazifalardan tanlanadi, shuning uchun parallel ro'yxat
saqlanmaydi. Boshqasini tanlash avvalgisini **almashtiradi** — chegara bitta
bo'lganda rad javobi berish ko'chaga olib boradi. Tanlov **sanaga bog'langan**,
shuning uchun kechagisi ertaga o'zi yo'qoladi.

## Odatlar

| Kategoriya | Standart odatlar |
|---|---|
| 🔴 Non-negotiable | **Get up** · **5x namoz** · **Kundalik** |
| 🟡 Target | Deep flow · Sport |
| 🟢 Bonus | Podcast · Read |

Uchtasi **avtomatik hisoblanadi** — qo'lda bosib bo'lmaydi:

| Odat | Nimadan hisoblanadi |
|---|---|
| **Get up** | Mini App'dagi «Turdim» tugmasi yoki botga «Turdim» deb yozilganda |
| **5x namoz** | Beshta namozning **hammasi** o'qilganda (yoki uzrli kun) |
| **Kundalik** | Beshta savolning **hammasiga** javob berilganda |

Har bir odatni bosib ochish mumkin: nomi, kategoriyasi, **qaysi kunlar**,
eslatma vaqti, streak, oxirgi 7/30 kun va tarix gridi.

### Jadval — bajarilmagan kun bilan rejada yo'q kun bir narsa emas

Har bir odat uchun: **har kuni**, **ish kunlari** yoki **tanlangan kunlar**.

Gym faqat Du/Chor/Jum bo'lsa, seshanba statistikasi Gym uchun minus bermaydi:
o'sha kun maxrajga umuman kirmaydi. Hech narsa rejada bo'lmagan kun streakni
ham buzmaydi.

### Pauza — o'chirish emas

Ta'til, jarohat yoki imtihon davri uchun odat **to'xtatib turiladi**. Bugungi
maxrajdan chiqadi, lekin **hamma eski log joyida qoladi**. O'chirish esa butun
tarixni yo'q qiladi — shuning uchun pauza birinchi taklif qilinadi.

Odatni o'chirish odatning o'z sahifasida, tasdiqlash bilan.

### Uyg'onish (Get up)

Odat sahifasida **uyg'onish vaqti** belgilanadi (default 05:00).

«Turdim» tugmasi bot klaviaturasida, **ErnestOS tugmasi ustida** turadi —
ertalab barmoq birinchi tegadigan joy.

O'sha vaqtdan **bir soat** kutiladi. Shu oraliqda bosilsa:
`☀️ Xayrli tong! 04:53 da turdingiz.` Kechiksa:
`😴 Afsuski, kech qoldingiz — 08:20. Target 05:00 edi. Ertaga o'zib ketamiz!`
Vaqt ikkalasida ham ko'rsatiladi; odat esa faqat vaqtida bajarilgan bo'ladi.

Erta turish ham hisoblanadi: 04:45 da bosilsa ham bajarilgan bo'ladi.

## Kundalik

Har kuni beshta savol. **Qancha yozilsa — shuncha saqlanadi**: uchta javob
uchta javob sifatida qoladi va `3 / 5 javob berildi` deb ko'rsatiladi.

Kundalik — **non-negotiable odat**, lekin u **faqat beshta javob to'liq
bo'lganda** bajarilgan bo'ladi. Ikkisi bir vaqtda to'g'ri: yozuv saqlangan va
odat hali tugamagan. To'liq emasligi umumiy foizni tushirmaydi — kundalik
umumiy hisobga kirmaydi.

Yozilayotgan matn har bosishda **brauzerda** saqlanadi va backend'ga
debounce bilan yuboriladi. Telegram yopilib qolsa, qayta kirganda matn
tiklanadi.

Ixtiyoriy **kayfiyat** check-in'i (😞 😕 😐 🙂 😄) — majburiy emas.

## Namoz — sifat va 5/5 alohida

Beshta namoz: Bomdod, Peshin, Asr, Shom, Xufton.

| | O'g'il bola | Qiz bola | 5/5 ga kiradimi |
|---|---|---|---|
| Jamoat | 1 ball | — | ✅ ha |
| O'z vaqtida | 1 ball | 1 ball | ✅ ha |
| Qazo | 0.5 ball | 0.5 ball | ✅ ha — kechikkan, o'tkazib yuborilmagan |
| O'qilmadi | 0 ball | 0 ball | ❌ yo'q |
| **Uzrli kun** | — | kunlik bayroq | ✅ to'liq kun |

Ikkita **alohida** savol:

* **Sifat balli** — 5 balldan (jamoat/vaqtida 1, qazo 0.5);
* **5 mahal bajarilishi** — beshtasining hammasi o'qilganmi.

`5x namoz` odati **faqat 5/5** bo'lganda bajarilgan bo'ladi. Ilgari ball ≥ 2.5
bo'lsa yetardi, ya'ni **uchta** namoz "5x namoz bajarildi" deb ko'rsatilardi —
bu tuzatildi (`migrations.py 0004` eski kunlarni qayta hisoblaydi).

## Vazifalar

* ixtiyoriy **vaqt**: `15 Avgust · 14:30`, vaqt yo'q bo'lsa — kun bo'yi;
* **eslatma**: aynan vaqtida / 10 minut / 1 soat / 1 kun oldin. Bajarilgan
  vazifa uchun eslatma yuborilmaydi;
* **takrorlanish**: har kuni / ish kunlari / har hafta / har oy. Bir marta
  bajarish takrorlanishni yo'q qilmaydi — keyingi nusxa o'zi paydo bo'ladi,
  bajarilgani esa arxivda o'z sanasi bilan qoladi;
* **qidiruv va filtr**: Hammasi / Shu hafta / Shu oy / Kechikkan /
  Muddatsiz / Yuqori / Past;
* **Bajarilgan** arxivi Bugun / Shu hafta / Oldin bo'yicha guruhlangan.

Yangi vazifa yaratish: bitta maydon va to'rtta chip —
**Bugun · Ertaga · Shu hafta · Sanasiz**. Qolgani (vaqt, eslatma,
takrorlanish, muhimlik, loyiha, izoh) `Qo'shimcha sozlamalar` ichida.

### Kechikkan vazifalar

Qizil devor yo'q. Har bir kechikkan qator ostida to'rtta tugma:
**Bugun · Ertaga · Shu hafta · Sanasiz**. Bir bosish bilan hal bo'ladi.

Uzoq tanaffusdan keyin qaytilganda bosh sahifada bitta taklif chiqadi —
**Yengil reset**: hammasini bugunga, bir haftaga taqsimlash, muddatlarni olib
tashlash yoki arxivga o'tkazish. **Hech qanday rejim vazifani o'chirmaydi.**

## Loyihalar

Nom majburiy; izoh va muddat ixtiyoriy. Holati **Faol / Tugallangan** va
alohida **arxiv** — tugagan loyihani o'chirish shart emas.

Loyiha sahifasida foiz **va** uning ortidagi son: `57% · 7 ta vazifadan 4 tasi
bajarilgan`. Loyiha ichidan vazifa qo'shilganda o'sha loyiha avtomatik
tanlanadi.

## Haftaning fokusi

**Vazifalar** bo'limining tepasida — bu rejalashtirish qarori, va shu yer
rejalashtirish ekrani. Bosh sahifa bugun haqida qoladi.

Bitta **asosiy** maqsad va maksimal ikkita **qo'shimcha** prioritet. Asosiysi
kattaroq va birinchi turadi — uchta teng maqsad "asosiy" degan tushunchani
yo'q qiladi.

Maqsad `✓ Bajarildi` deb belgilanadi yoki bir bosish bilan **keyingi haftaga
ko'chiriladi**.

## Statistika

Vazifalar, Odatlar, Namoz va Umumiy — hammasi **haftalik** (7 kun),
**oylik** (30 kun) va **yillik** (12 oy) kesimda. Har bir raqam yonida
oldingi davrga nisbatan o'zgarish (↑ ↓), eng yaxshi kun va streak.

Namoz tafsiloti bitta noma'lum ballga aylantirilmaydi: **5/5 bo'lgan kunlar**,
**vaqtida %**, **jamoat**, **qazo**, **o'qilmadi** va **barqarorlik** alohida.

### Umumiy foiz qanday hisoblanadi

Umumiy — **bugun o'lchanadigan** bo'limlarning oddiy o'rtachasi:
Vazifalar, Odatlar, Namoz.

Bugun bo'sh bo'lgan bo'lim **nol emas, yo'q** — hisobga olinmaydi. Bugun
vazifa belgilanmagan bo'lsa, u 0% deb yozilmaydi, aks holda ilova
foydalanuvchini so'ralmagan ish uchun jazolagan bo'lardi.

Raqam yonidagi ⓘ tugmasi shu hisobni **bo'lim bo'yicha** ochib beradi —
raqamni yaratgan aynan o'sha funksiyalardan.

## Vaqt mintaqasi, hisobot va eslatmalar

Default `Asia/Tashkent`, lekin Sozlamalardan o'zgartiriladi. Foydalanuvchi
mintaqasi bo'yicha ishlaydi: uyg'onish, kun chegarasi, hisobotlar,
eslatmalar va statistika kunlari.

Sozlamalarda boshqariladi:

| Sozlama | Default |
|---|---|
| Ertalabki hisobot | ✅ 04:00 |
| Kechqurungi hisobot | ✅ 21:30 |
| Vazifa eslatmalari | ✅ yoqilgan |
| Odat eslatmalari | ❌ o'chirilgan |

Hisobot vaqti har foydalanuvchida o'zining bo'lgani uchun job bitta cron
yozuvi emas: `REPORT_TICK_MINUTES` (default 2) da bir marta uyg'onadi va har
foydalanuvchidan "vaqti keldimi?" deb so'raydi. Kuniga **bir marta**
yuborilishini cron emas, outbox claim kafolatlaydi.

Platforma statistikasi operator kanaliga **23:00** da boradi.

## Beshta dizayn uslubi × yorug'/qorong'i

Har biri bir xil UI'ning boshqa rangi emas: o'z radiusi, soyasi, gradient
siyosati, tipografiyasi va animatsiya tezligi bor. Har birida **beshta yorqin
brend rangi** (`--c1`..`--c5`) — ular grafik seriyalari va aksentlar uchun ham
ishlatiladi.

| Tema | Xarakter | Yetakchi ranglar |
|---|---|---|
| **Calm** *(default)* | Muvozanatli va sokin | ko'k · siyan · binafsha · amber · emerald |
| **Titan** | Kuchli va premium | electric blue · siyan · steel · amber |
| **Muse** | Nafis va iliq | rose · lavender · fuchsia · amber · teal |
| **Rage** | Fokus va tezlik | qizil · orange · sariq · lime |
| **Nexus** | Futuristik va aqlli | indigo · binafsha · siyan · fuchsia · lime |

Beshtasi ham **yorug' va qorong'i** rejimda ishlaydi. Rejim: Sozlamalar →
Ko'rinish → `Avto / Yorug' / Qorong'i`. `Avto` Telegram'ning o'z rejimiga
qaraydi, u bo'lmasa operatsion tizimga. Tanlov qurilmada saqlanadi (bir xil
akkaunt yorqin telefonda va qorong'i desktopda o'qilishi mumkin), tema esa
PostgreSQL'da.

Texnik jihatdan: bitta semantik token qatlami (`--bg`, `--surface`, `--text`,
`--primary`, `--accent`, `--border`, `--ok/--warn/--danger`) va **o'n** ta
kombinatsiya bloki. Hech bir komponent rang nomini yozmaydi — test buni
tekshiradi, shuning uchun temani o'zgartirish komponentni qayta yozishni
talab qilmaydi.

## Ma'lumotlar va maxfiylik

Sozlamalar → **Ma'lumotlar va maxfiylik**:

* **Ma'lumotlarimni export qilish** — hamma yozgan narsa JSON fayl sifatida
  bot chatiga yuboriladi;
* **Maxfiylik haqida** — nima himoyalangan va nima yo'qligi ochiq yozilgan;
* **Akkaunt va ma'lumotlarni o'chirish** — `DELETE` deb yozib tasdiqlash
  talab qiladi, keyin hammasi butunlay o'chadi.

Vada faqat bajarilgani aytiladi: *"Ma'lumotlaringiz boshqa foydalanuvchilardan
alohida saqlanadi"*. "To'liq himoyalangan" degan absolut vada yo'q — bazaga
texnik xizmat uchun administrator kira oladi va buni yashirmaslik kerak.

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
| `REPORT_TICK_MINUTES` | Hisobot job'i necha minutda bir uyg'onadi (default 2). Bu qiymat — hisobotning eng ko'p kechikishi | yo'q |
| `MEMBERSHIP_TTL` | Obuna javobi necha sekund keshlanadi (default 180) | yo'q |

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
| `0003` | Eski mavzu nomlarini o'sha davrdagi to'plamga ko'chiradi | `UPDATE users SET theme=<eski nom>` |
| `0004` | `5x namoz` odatini **haqiqiy 5/5** bo'yicha qayta hisoblaydi | qayta hisoblash — `PrayerLog` tegilmagan, manba joyida |
| `0005` | Mavzu nomlarini yangi beshtaga ko'chiradi (`cobalt→ocean` va h.k.) | `UPDATE users SET theme=<eski nom>` |

`0004` **tarixiy raqamlarni o'zgartiradi**: uchta namoz o'qilgan kun endi
bajarilgan odat sifatida hisoblanmaydi. Bu ataylab — eski raqam noto'g'ri
edi, va uni joyida qoldirish streak va statistikaning foydalanuvchi
qilmagan narsani ko'rsatishda davom etishini bildirardi. `prayer_logs`
jadvaliga tegilmaydi, shuning uchun hisob har doim qaytadan chiqariladi.

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
- Scheduler (hisobotlar, eslatmalar, kunlik platforma statistikasi)

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
[ ] /start → faqat til tanlash chiqadi (uzun uch tilli matn yo'q)
[ ] til tanlanganda o'sha tilda "Assalomu alaykum, <ism>" chiqadi
[ ] keyin telefon so'raladi va nima uchun kerakligi tushuntiriladi
[ ] "O'tkazib yuborish" bosilsa onboarding to'xtamaydi
[ ] kanalga obuna bo'lmaganda kirish bloklanadi
[ ] Mini App'da blok ekrani chiqadi: Kanalga kirish + Obunani tekshirish
[ ] obuna bo'lgach "Tekshirish" bosilsa ilova qayta ishga tushmasdan davom etadi
[ ] kanaldan chiqilganda bloklanadi, lekin ma'lumot o'chmaydi
[ ] admin log kanaliga "NEW ERNESTOS USER" keladi

--- Bosh sahifa ---
[ ] birinchi ko'rinishda HOZIR bloki bor va bitta harakatni ko'rsatadi
[ ] ertalab HOZIR = ☀️ Turdim; bosilganda "✓ 05:07 da turdingiz" chiqadi
[ ] kech bosilsa "06:18 — bugungi targetdan kechroq" deydi, jazolamaydi
[ ] ＋ tugmasi har doim ko'rinadi va 5 sekundda vazifa saqlaydi
[ ] bugungi vazifani Home'dan bitta bosishda bajarilgan qilish mumkin
[ ] bajarilgandan keyin "Qaytarish" taklif qiladi va u ishlaydi
[ ] TOP 3 ga uchta vazifa tanlanadi, to'rtinchisi qabul qilinmaydi
[ ] TOP 3 dagi vazifa pastda ikkinchi marta ko'rinmaydi
[ ] haftaning fokusi: asosiy kattaroq, qo'shimchalar kichikroq
[ ] maqsad ✓ bajarildi va keyingi haftaga ko'chirildi
[ ] hafta stripi ko'rinadi, bosilganda to'liq kalendar ochiladi
[ ] kalendar ichidagi har bir qator bosilganda haqiqiy narsani ochadi
[ ] tug'ilgan kun Mini App'dan qo'shiladi va yaqinlashsa Home'da chiqadi

--- Odatlar ---
[ ] odat bosilganda darhol belgilanadi, sahifa sakramaydi
[ ] odat ustiga bosilsa: nom, kategoriya, kunlar, eslatma, tarix ochiladi
[ ] Gym faqat Du/Chor/Jum bo'lsa, seshanba minus bermaydi
[ ] hech narsa rejada yo'q kun streakni buzmaydi
[ ] Pauza bosilsa maxrajdan chiqadi, eski loglar joyida qoladi
[ ] tarix gridi, streak va % ko'rsatiladi
[ ] 5x namoz qo'lda bosilmaydi (qulf)
[ ] odatlar tartibi o'zgartiriladi va botda ham shu tartibda ko'rinadi

--- Namoz ---
[ ] birinchi ochilganda jins so'raladi va nega kerakligi yozilgan
[ ] uch namoz kiritilsa "5x namoz bajarildi" DEB KO'RSATILMAYDI
[ ] beshtasi kiritilganda 5x namoz bajarilgan bo'ladi
[ ] qazo 5/5 ga kiradi, lekin ball 0.5
[ ] xato bosilgan holat ✕ bilan olib tashlanadi
[ ] ayolda Uzrli + vaqtida/qazo/o'qilmadi bor, Jamoat yo'q
[ ] uzrli kun to'liq kun sifatida hisoblanadi

--- Kundalik ---
[ ] uch javob yozilsa "3 / 5 javob berildi" deydi, xato deb ko'rsatmaydi
[ ] to'liq emasligi umumiy foizni tushirmaydi
[ ] yozayotganda Telegram yopilsa, qayta kirganda matn tiklanadi
[ ] kayfiyat check-in'i ixtiyoriy

--- Vazifalar ---
[ ] yangi vazifa: bitta maydon + Bugun/Ertaga/Shu hafta/Sanasiz
[ ] Qo'shimcha sozlamalar ichida vaqt, eslatma, takrorlanish, loyiha
[ ] vaqt belgilanmasa kun bo'yi vazifa bo'ladi
[ ] eslatma belgilangan vaqtda keladi va ikki marta kelmaydi
[ ] bajarilgan vazifa uchun eslatma kelmaydi
[ ] takrorlanuvchi vazifani bir marta bajarish seriyani tugatmaydi
[ ] kechikkan qator ostida Bugun/Ertaga/Shu hafta/Sanasiz tugmalari bor
[ ] qidiruv va filtr ishlaydi
[ ] Bajarilgan arxivi Bugun / Shu hafta / Oldin bo'yicha guruhlangan
[ ] bajarilgan vazifa qayta ochiladi

--- Loyihalar ---
[ ] loyiha yaratishda izoh va muddat kiritish mumkin
[ ] loyiha ichidan vazifa qo'shilganda o'sha loyiha avtomatik tanlanadi
[ ] "57% · 7 ta vazifadan 4 tasi bajarilgan" ko'rinadi
[ ] tugagan loyiha o'chirilmaydi — Tugallangan yoki Arxiv
[ ] loyiha o'chirilganda vazifalari qolib ketadi

--- Statistika ---
[ ] Vazifalar + Odatlar + Namoz + Umumiy, hafta/oy/yil
[ ] har raqam yonida oldingi davrga nisbatan ↑ ↓
[ ] eng yaxshi kun ko'rsatiladi
[ ] namoz tafsiloti: 5/5 kunlar, vaqtida %, jamoat, qazo, o'qilmadi
[ ] ⓘ bosilganda umumiy foiz qanday chiqqani bo'lim bo'yicha ochiladi
[ ] botdagi va ilovadagi umumiy foiz bir xil
[ ] CSV bot chatiga keladi

--- Sozlamalar ---
[ ] Saqlash tugmasi YO'Q — har o'zgarish darhol saqlanadi va toast chiqadi
[ ] beshta tema haqiqatan boshqacha his beradi, faqat rang emas
[ ] Pure/Sage yorug', Midnight/Aurora qorong'i — tanlov ekranida yozilgan
[ ] Ocean telefon sozlamasiga qarab yorug'/qorong'i bo'ladi
[ ] vaqt mintaqasi o'zgartirilganda "bugun" o'zgaradi
[ ] hisobot vaqtlari va eslatma kalitlari ishlaydi
[ ] avatar Telegram rasmidan o'zi keladi — botdan yuklash talab qilinmaydi
[ ] Ma'lumotlarimni export qilish → JSON fayl bot chatiga keladi
[ ] Akkauntni o'chirish DELETE deb yozishni talab qiladi
[ ] Taklif Mini App'dan yuboriladi va yetkazilmasa "sent" deb ko'rsatmaydi

--- Til va qurilma ---
[ ] UZ → EN → RU almashtirilganda aralash til qolmaydi
[ ] oy va hafta kunlari nomlari ham tarjima bo'ladi
[ ] iPhone / Android / Telegram Desktop da gorizontal scroll yo'q
[ ] kichik ekran (320px) va katta shriftda hamma narsa o'qiladi
[ ] Telegram Back tugmasi sheet'ni yopadi, ilovani yopmaydi
[ ] maxfiylik qatori nav ustida qotib turadi va kontentni yopmaydi

--- Uzoq tanaffus ---
[ ] 1 oy kirmasdan qaytilganda 300 ta kechikkan vazifa bosib ketmaydi
[ ] bitta taklif chiqadi: Yengil reset
[ ] hech qanday reset rejimi vazifani o'chirmaydi

--- Bot ---
[ ] bot menyusi aynan 7 ta: Home/Odatlar/Vazifalar/Statistika/Sozlamalar/Taklif/Mini App
[ ] Mini App'da qilingan o'zgarish botda ko'rinadi va aksincha
[ ] belgilangan vaqtda ertalabki va kechqurungi hisobot keladi
[ ] ilova qayta ishga tushsa hisobot takrorlanmaydi
```

---

# Loyiha tuzilishi

```
ernestos/
├── app.py              FastAPI + bot handlerlar + scheduler + admin log
├── services.py         umumiy biznes qatlami (bot va API ikkalasi ishlatadi)
├── db.py               SQLAlchemy modellari + engine
├── migrations.py       qo'lda ishga tushiriladigan ma'lumot migratsiyalari
├── webapp/
│   └── index.html      Mini App (bitta fayl, tashqi kutubxona yo'q)
├── tests/
│   └── test_smoke.py   323 ta test
├── requirements.txt
├── Procfile
├── .env.example
└── README.md
```

Mini App ataylab bitta fayl va **hech qanday frontend framework ishlatmaydi**:
Telegram WebView'da yuklanish tezligi eng muhim, va bu hajmda build qadamining
foydasi yo'q. Ikonkalar inline SVG.

# Testlar

```bash
python -m pytest tests/ -q
```

**323 test o'tadi.**

Qamrov:

* foydalanuvchi/workspace izolyatsiyasi va har bir endpoint uchun egalik
  nazorati;
* Telegram initData tekshiruvi (imzo, muddat, boshqa bot tokeni);
* obunaning uch holati va onboarding gate;
* namoz **sifat balli** va **5/5 bajarilishi** — alohida-alohida;
* odat jadvali (ish kunlari / tanlangan kunlar), pauza, streak;
* takrorlanuvchi vazifa: bir marta bajarish seriyani tugatmasligi;
* eslatma oynasi va ikki marta yuborilmasligi;
* vaqt mintaqasi — uyg'onish chegarasi va "bugun" tushunchasi;
* umumiy foizning har bir surat ustida bir xil chiqishi;
* UZ/EN/RU kalitlarining to'liq mosligi va tarjima qolib ketmasligi;
* migratsiyalar **to'ldirilgan** baza ustida, ikki marta ishga tushirilganda;
* har bir UI action'ining real handler'ga bog'langani va route'lar
  bir-birini to'sib qo'ymasligi.

# Xavfsizlik

- Mini App `initData` server tomonda HMAC bilan tekshiriladi;
  `telegram_id` **faqat imzolangan ma'lumotdan** olinadi, JSON body'dan emas.
- Har bir so'rov `workspace_id` bo'yicha cheklanadi — begona ma'lumotga
  murojaat 404 qaytaradi va mavjudligini oshkor qilmaydi.
- Telefon raqam faqat `contact.user_id == from_user.id` bo'lganda saqlanadi va
  admin kanalga **hech qachon** yuborilmaydi.
- Ichki xatolik hech qachon foydalanuvchiga qaytmaydi.
- Admin kanalga sir, token yoki stack trace yuborilmaydi.
- Har foydalanuvchi bo'yicha rate limit (o'qish/yozish/og'ir) va 256 KB body
  cheklovi.
- Akkauntni o'chirish `ON DELETE CASCADE` ga tayanmaydi: har bir jadval
  ataylab qo'lda tozalanadi, chunki SQLite foreign key'ni faqat so'ralganda
  tekshiradi va "o'chirdim" deb aytib kimningdir kundaligini qoldirib ketish
  eng yomon xato bo'lardi.

## Nima himoyalangan va nima yo'q

Bu ochiq yozilishi kerak, chunki foydalanuvchiga ko'rsatiladigan matn ham
shunga mos bo'lishi shart:

| | Holat |
|---|---|
| Boshqa foydalanuvchi ma'lumotingizni ko'rishi | ❌ mumkin emas — 20+ test bilan qoplangan |
| Ma'lumot tashqi xizmatga yuborilishi | ❌ yuborilmaydi (AI yo'q, analytics yo'q) |
| Administrator bazaga texnik kirishi | ✅ mumkin — buni yashirish to'g'ri emas |
| End-to-end shifrlash | ❌ yo'q, va da'vo ham qilinmaydi |

Shuning uchun ilovadagi matn *"Ma'lumotlaringiz boshqa foydalanuvchilardan
alohida saqlanadi"* deydi — *"to'liq himoyalangan"* demaydi.
