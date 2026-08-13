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
| 🏠 Bosh sahifa | **Hozir** (avtomatik tanlanadi), kun/hafta/oy foizi yonma-yon, vazifa/odat/namoz, bugungi vazifalar |
| ✅ Odatlar | Odatlar (3 kategoriya, jadval, pauza, tarix) + namoz + kundalik |
| ⚡ Vazifalar | **Asosiy / Ochiq / Bajarilgan**, kalendar, qidiruv va filtr, kechikkanlar uchun tez ko'chirish, loyihalar |
| 📊 Statistika | Umumiy % + Vazifalar / Odatlar / Namoz, o'zgarish (↑↓), eng yaxshi kun, namoz tafsiloti |

Pastdagi navigatsiya aynan shu to'rtta. Qolgan hamma narsa — kalendar,
tug'ilgan kunlar, haftalik yakun, sozlamalar, export — kerakli joydan
ochiladi, alohida tab sifatida emas.

Uch til (o'zbek, ingliz, rus) **to'liq** tarjima qilingan, beshta dizayn
uslubi × yorug'/qorong'i rejim.

## Bosh sahifa — bir ochishda ko'rinadigan narsa

Bosh sahifa faqat to'rt blok, shu tartibda:

1. salom, sana, quote, sozlamalar va rasm;
2. **Hozir** — ayni damda qilinadigan bitta ish, avtomatik tanlanadi;
3. **kun / hafta / oy** foizi **yonma-yon**, har birida o'zgarish belgisi
   (↑↓), pastida bitta qatorda vazifa / odat / namoz;
4. loyihalarga bo'lingan **bugungi vazifalar**.

Boshqa hamma narsa — haftaning fokusi, tug'ilgan kunlar, haftalik yakun —
o'ziga tegishli ekranda. Maqsad: ochish bilan kerakli narsani ko'rish.

## Hozir

"Ayni damda nima qilishim kerak?" savolining javobi **bitta**. Foydalanuvchi
ekranni o'qib, o'zi tanlab o'tirmaydi — javob backend'da qat'iy tartib bilan
hisoblanadi (`services.now_next`):

1. turish vaqti, hali hisoblanayotgan bo'lsa;
2. foydalanuvchi o'zi tanlab qo'ygan (★) vazifa;
3. **muddati o'tgan** ish — kechikkan muddat kelasi muddatdan ustun;
4. bugun muddati tugaydigan ish, avval vaqti bo'yicha, keyin muhimligi;
5. bajarilmagan odat;
6. tushdan keyin — namoz;
7. kechqurun — kun yakuni;
8. bo'lmasa: bugungi muhim ishlar tugadi.

Har bir javob **sababi bilan** keladi ("Muddati o'tgan — eng oldin shu"), va
karta uni chiqaradi: foydalanuvchi o'rniga qaror qabul qiladigan interfeys
nega aynan shuni tanlaganini aytishi kerak. Bu taklif, buyruq emas — ✎ tugmasi
tanlovni o'zgartiradi, tanlangan vazifa esa tartibning eng tepasiga chiqadi.

## Odatlar

Yangi hisob **uchta** odat bilan ochiladi, boshqa hech narsa bilan emas:

| Kategoriya | Standart odatlar |
|---|---|
| 🔴 Non-negotiable | **Get up** · **5x namoz** · **Kundalik** |
| 🟡 Target | *(bo'sh — o'zingiz qo'shasiz)* |
| 🟢 Bonus | *(bo'sh — o'zingiz qo'shasiz)* |

Sabab: bu uchtasi — ErnestOS nima haqidaligi. Deep flow, sport, kitob yoki
podcast — bular qanday yashash haqidagi **shaxsiy tanlov**, va ularni oldindan
qo'shib qo'yish yangi foydalanuvchiga o'zi tanlamagan ro'yxatni berish
demakdir. Birinchi «o'z» odatingizni siz qo'shasiz.

> **Eski foydalanuvchilar uchun hech narsa o'zgarmaydi.** Agar hisobingizda
> allaqachon Deep flow, Sport, Podcast yoki Read bo'lsa — ular tarixi bilan
> birga joyida qoladi. Standart ro'yxat faqat **yangi** workspace yaratilganda
> ishlatiladi.

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
| Ertalabki hisobot | ✅ 05:00 |
| Kechqurungi hisobot | ✅ 21:30 |
| Vazifa eslatmalari | ✅ yoqilgan |
| Odat eslatmalari | ❌ o'chirilgan |

Hisobot vaqti har foydalanuvchida o'zining bo'lgani uchun job bitta cron
yozuvi emas: `REPORT_TICK_MINUTES` (default 2) da bir marta uyg'onadi va har
foydalanuvchidan "vaqti keldimi?" deb so'raydi. Kuniga **bir marta**
yuborilishini cron emas, outbox claim kafolatlaydi.

> **Ertalabki hisobot 04:00 dan 05:00 ga ko'chirildi.** Eski qiymat scheduler
> server soatida ishlagan davrdan qolgan edi: 04:00 UTC — Toshkentda 09:00.
> Scheduler loyiha soatiga (`Asia/Tashkent`) o'tganda o'sha raqam jimgina
> tunning to'rtiga aylandi. Hech narsa xato bermadi — hisobotlar har kuni
> o'z vaqtida, uxlab yotgan odamlarga ketdi. Bu faqat **default** qiymat:
> vaqtini o'zi tanlagan foydalanuvchilarga ta'sir qilmaydi.

Jo'natish ishlayaptimi-yo'qmi degan savolga `GET /health/ready` javob beradi:
`stats_last_post`, `stats`, `morning_today` va `evening_today` bugun nima
yuborilganini (`sent · failed · claimed`) ko'rsatadi. Kanal jim bo'lsa,
odatda sabab bitta — bot o'sha kanalda **administrator emas**.

Platforma statistikasi operator kanaliga har kuni **10:00** da boradi
(`STATS_POST_HOUR`, loyiha soati bo'yicha).

## Beshta dizayn uslubi × yorug'/qorong'i

Har biri bir xil UI'ning boshqa rangi emas: o'z radiusi, soyasi, gradient
siyosati, tipografiyasi va animatsiya tezligi bor. Har birida **beshta yorqin
brend rangi** (`--c1`..`--c5`) — ular grafik seriyalari va aksentlar uchun ham
ishlatiladi.

| Tema | Xarakter | Yetakchi rang | Vizual belgilar |
|---|---|---|---|
| **Ocean Glass** *(default)* | Shaffof shisha, chuqur ko'k | systemBlue | blur, yumshoq glow, radius 16 |
| **Midnight Minimal** | Sokin, chalg'itmaydigan | systemIndigo | gradient yo'q, ingichka chiziqlar, qorong'ida to'liq qora |
| **Aurora Glass** | Futuristik, "wow" | systemPurple | shisha + aurora gradientlar, radius 20 |
| **Pure Bento** | Tartibli, eng tez o'qiladi | siyoh (ink) | soya yo'q, aniq bloklar, iliq qog'oz |
| **Spatial Layered** | Qatlamli chuqurlik | systemTeal | suzuvchi kartalar, uzun yumshoq soyalar |

Ranglar tasodifiy tanlanmagan: har bir yorqin rang — **Apple system color**
(`systemBlue`, `systemIndigo`, `systemPurple`, `systemTeal`, `systemMint`…),
kulranglar esa Apple'ning `label` / `secondaryLabel` / `separator` /
`systemGray` shkalasidan. Yorug' fonda matn sifatida ishlatiladigan rang
uchun Apple'ning **accessible** varianti olinadi (`#34C759` o'rniga
`#248A3D`) — test buni tekshiradi. Shrift ham avval **SF Pro**, keyin Inter:
iPhone'da va Telegram Desktop'da ilova atrofidagi tizim bilan bir xil
ko'rinadi.

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

## Shaxsiy progressiya — Daily Score, XP, Daraja, Reyting

ErnestOS'da **ikkita butunlay alohida** progressiya tizimi bor:

| Tizim | Nimani o'lchaydi | Qayerda |
|---|---|---|
| **Shaxsiy** | Hayotingizni qanday boshqarayotganingiz | Bosh sahifadagi Progressiya kartasi |
| **Referral** | Nechta odamni olib kelganingiz | Sozlamalar → 🎁 Do'st taklif qilish |

Ular **hech qachon aralashmaydi**. 100 ta odam taklif qilgan, lekin o'zi
ErnestOS'dan foydalanmaydigan odam yuqori shaxsiy darajaga **chiqmaydi**.

### Daily Score — 0..100

Kunlik ball **yangi formula emas**. Bu Bosh sahifada allaqachon turgan umumiy
foiz: vazifa 40 / odat 25 / fokus 20 / namoz 15. Ikkinchi formula kiritilsa,
bitta ilovaning ikkita ekranida "bugun qanday o'tdi" degan ikkita raqam bir-
biriga to'g'ri kelmay qolardi. Bo'sh kategoriya **nol emas, yo'q** — vazifasi
yo'q kun vazifani bajarmagan kun emas.

| Ball | Baho |
|---|---|
| 90–100 | S · Mukammal kun |
| 80–89 | A · A'lo |
| 70–79 | B · Kuchli |
| 60–69 | C · Harakat |
| 40–59 | D · Tiklanish |
| 0–39 | E · Qaytadan |

Pastki baho ham **ayblamaydi**: "Failed" emas, "Qaytadan".

### XP va darajalar

XP **ledger** orqali beriladi (`xp_events`), har bir mukofot o'z kaliti bilan:
`task:412`, `perfect_day:1001:2026-08-14`. Kalit UNIQUE — shuning uchun
Telegram retry, ikki marta bosish yoki *bajardim → bekor qildim → bajardim*
XP'ni **takrorlamaydi**. Kunlik oddiy faollik chegarasi: **120 XP**. Milestone
(perfect day, streak, comeback, achievement) chegaradan tashqarida.

| Daraja | XP |
|---|---|
| I Boshlovchi | 0 |
| II Builder | 500 |
| III Operator | 1 500 |
| IV Architect | 3 500 |
| V Commander | 7 000 |
| VI Elite | 15 000 |
| VII Master | 30 000 |

Daraja **saqlanmaydi** — har safar `xp_total`dan hisoblanadi.

### Streak va Tiklanish kunlari

Kun **60 balldan** yuqori bo'lsa streak +1. Bir og'ir kun 30 kunlik streakni
buzmasligi kerak, shuning uchun oyiga **2 ta Tiklanish kuni** avtomatik
sarflanadi — streak saqlanadi, lekin o'sha kun uchun XP berilmaydi. Uzoq
tanaffusdan keyin qaytish **Comeback** (+15 XP), 14 kunlik cooldown bilan —
ataylab yo'qolib turib farm qilib bo'lmaydi.

### Reyting

Reyting **umrbod XP bo'yicha emas**. Bo'lsa, taxta faqat akkaunt yoshini
ko'rsatardi. Reyting **oxirgi 30 kalendar kun** bo'yicha Performance Index:

```
70% — o'rtacha kunlik ball
20% — izchillik (60+ ballli kunlar ulushi)
10% — haftalik fokus bajarilishi
```

Kalendar kunlar, faol kunlar emas — ErnestOS'ni tashlab ketgan odam o'z-o'zidan
pastga tushadi, alohida jazo qoidasi kerak emas. Reyting **7 kundan keyin**
ochiladi, aks holda bitta 100 ballik kun yangi akkauntni #1 ga chiqarardi.
Teng indeks — teng o'rin (#184, #184, #186).

**Maxfiylik:** foydalanuvchi faqat **o'z o'rnini** va umumiy sonni ko'radi —
`#184 / 12 842`. Hech kimning ismi, username'i yoki telegram_id'si
ko'rsatilmaydi. Ochiq leaderboard **qilinmadi**.

### Ishlash

Profil ochilganda hech qachon butun tarix skanerlanmaydi. Kun **o'zgarganda**
hisoblanadi (action funnel), reyting esa `user_progress` dagi indeksli bitta
ustunni o'qiydi. 300 foydalanuvchi × 30 kunlik tarixda: reyting **0.7 ms**,
profil **1.2 ms**.

### Eski foydalanuvchilar

Backfill **qilinmadi** — soxta umrbod XP yaratilmaydi. Progressiya feature
yoqilgan kundan boshlanadi, `user_progress` qatori esa har kimga birinchi
marta kuni hisoblanganda o'zi paydo bo'ladi. Migratsiya kerak emas.

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
| `MEMBERSHIP_TTL` | Obuna javobi necha sekund keshlanadi (default 600) | yo'q |
| `FREE_ACTIONS` | Kanal so'ralguncha nechta amal bepul (default 20) | yo'q |
| `WEBHOOK_URL` | Botni webhook rejimida ishlatish uchun to'liq https manzil (`.../webhook`). Bo'sh = polling | yo'q |
| `WEBHOOK_SECRET` | Telegram qaytaradigan maxfiy token — webhook'ni himoyalaydi | `WEBHOOK_URL` bo'lsa tavsiya etiladi |
| `STATS_POST_HOUR` | Kunlik statistika soati, loyiha soati bo'yicha (default 10) | yo'q |
| `MAX_BODY_BYTES` | So'rov tanasining chegarasi (default 256 KB) | yo'q |

Hammasi **bitta joyda** — `config.py` — o'qiladi. Kod ichida tarqoq
`os.environ.get(...)` yo'q, shuning uchun «bu sozlama qayerdan o'qiladi»
degan savolga bitta javob bor.

`ENVIRONMENT=production` bo'lsa:

* `DATABASE_URL` majburiy — SQLite'ga qaytish yo'q (`db.py` import paytida
  xato beradi);
* `BOT_TOKEN` majburiy — `config.check()` ilova ko'tarilishidan oldin xato
  beradi.

Ikkalasi ham **ataylab** shunday: yetishmayotgan sozlamani deploy'dan ikki
soat keyin foydalanuvchi shikoyatidan bilish — eng yomon variant.

## G. Lokal ishga tushirish

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Testlarni ham ishlatmoqchi bo'lsangiz:

```bash
pip install -r requirements-dev.txt
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

> **Muhim:** polling rejimida bitta instansiya ishlatilsin. Ikkita bo'lsa
> Telegram polling to'qnashadi. Bir nechta instansiya kerak bo'lsa —
> webhook rejimiga o'ting (quyida).

## K2. Webhook rejimi (ixtiyoriy)

Polling bitta instansiya uchun yetarli va hech qanday ochiq manzil talab
qilmaydi. Webhook esa ikki holatda kerak bo'ladi: foydalanuvchi ko'payib,
doimiy long-poll protsessning asosiy ishiga aylanganda; va load balancer
ortida bir nechta instansiya ishlaganda — chunki ular bitta botni birga
poll qila olmaydi.

```bash
WEBHOOK_URL=https://sizning-domeningiz.com/webhook
WEBHOOK_SECRET=uzun-tasodifiy-satr
```

`WEBHOOK_URL` qo'yilishi bilan ilova polling'ni ishga tushirmaydi va
o'rniga Telegram'ga webhook o'rnatadi. `/webhook` endpoint faqat shu
o'zgaruvchi bor bo'lgandagina javob beradi — eski webhook qolib ketsa ham
polling ishlayotgan instansiyaga update tushmaydi.

`WEBHOOK_SECRET` — Telegram har so'rovda qaytaradigan sarlavha. Usiz
manzilni topgan har kim bot nomidan update yubora oladi.

## K3. Boshqa platformalar

### Heroku

```bash
heroku create ernestos
heroku addons:create heroku-postgresql:essential-0
heroku config:set BOT_TOKEN=... ENVIRONMENT=production TZ=Asia/Tashkent
git push heroku main
```

`Procfile` allaqachon mavjud. `runtime.txt` qo'shing:

```
python-3.12.3
```

`DATABASE_URL` ni Heroku o'zi qo'shadi, lekin u `postgres://` bilan
boshlanadi — `db.py` buni `postgresql://` ga o'zi o'giradi.

### Fly.io

```bash
flyctl launch --no-deploy
flyctl postgres create --name ernestos-db
flyctl postgres attach ernestos-db
flyctl secrets set BOT_TOKEN=... ENVIRONMENT=production TZ=Asia/Tashkent
flyctl deploy
```

`fly.toml` da portni ochiq qoldiring:

```toml
[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = false   # bot va scheduler doim ishlab turishi kerak
  min_machines_running = 1
```

`auto_stop_machines` ni **albatta** `false` qiling: mashina uxlab qolsa
ertalabki hisobot yuborilmaydi.

### DigitalOcean App Platform

`app.yaml`:

```yaml
name: ernestos
services:
  - name: web
    github:
      repo: sizning-foydalanuvchi/ErnestOS
      branch: main
    run_command: uvicorn app:app --host 0.0.0.0 --port $PORT
    instance_count: 1
    instance_size_slug: basic-xxs
    envs:
      - key: ENVIRONMENT
        value: production
      - key: BOT_TOKEN
        type: SECRET
databases:
  - name: db
    engine: PG
    production: true
```

`instance_count: 1` — polling uchun. Ko'proq kerak bo'lsa webhook rejimiga
o'ting (K2 bo'limi).

## L. Hammasini tekshirish

Deploy'dan keyin quyidagilarni birma-bir bosib chiqing:

```
[ ] /start uch tilda salomlashadi, keyin til so'raydi
[ ] tartib: til -> obuna. Telefon raqam HECH QAYERDA so'ralmaydi
[ ] til tanlash ishlaydi (uz / en / ru)
[ ] til tanlanganda o'sha tilda "Assalomu alaykum, <ism>" chiqadi
[ ] keyin darhol kanal obunasi so'raladi
[ ] obunadan keyin emoji va misollar bilan qo'llanma xabari keladi
[ ] /guide o'sha qo'llanmani qaytadan chiqaradi
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
[ ] beshtasi ham yorug' va qorong'i rejimda ishlaydi
[ ] `Avto` telefon/Telegram sozlamasiga qarab yorug'/qorong'i bo'ladi
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
├── app.py              FastAPI ilova + bot handlerlar + API endpoint'lar
├── config.py           barcha muhit o'zgaruvchilari — yagona joy
├── security.py         initData imzosi, auth, HTML escape
├── ratelimit.py        so'rov limiti (RateLimiter → InMemoryRateLimiter)
├── scheduler.py        job'lar qachon ishlaydi (takrorlanishdan himoya)
├── translations.py     bot matnlari — UZ / EN / RU
├── dependencies.py     kirish siyosati: kanal, bepul urinishlar, obuna
├── services.py         umumiy biznes qatlami (bot va API ikkalasi ishlatadi)
├── db.py               SQLAlchemy modellari + engine
├── migrations.py       qo'lda ishga tushiriladigan ma'lumot migratsiyalari
├── webapp/
│   └── index.html      Mini App (bitta fayl, tashqi kutubxona yo'q)
├── tests/
│   └── test_smoke.py
├── .github/workflows/
│   └── tests.yml       CI: pyflakes + pytest
├── requirements.txt        ishlash uchun
├── requirements-dev.txt    test uchun (pytest, pytest-asyncio, httpx, pyflakes)
├── Procfile
├── .env.example
└── README.md
```

Qatlamlar:

```
app.py  →  services.py  →  db.py
   ↑
bot handlerlar va API endpoint'lar bir xil servis funksiyalarini chaqiradi
```

Telegram bot — alohida biznes qatlami emas, `services.py` ustidagi adapter.
Shuning uchun tugma orqali yaratilgan vazifa va Mini App orqali yaratilgan
vazifa **aynan bir xil funksiyadan** o'tadi.

Mini App ataylab bitta fayl va **hech qanday frontend framework ishlatmaydi**:
Telegram WebView'da yuklanish tezligi eng muhim, va bu hajmda build qadamining
foydasi yo'q. Ikonkalar inline SVG. Fayl ichida ham tartib bor: bitta `state`,
bitta `api()` klient, bitta `DICT` (UZ/EN/RU), `SCREENS` va `A` (action) xarita,
va barcha ranglar CSS o'zgaruvchilarida.

# Testlar

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Aniq test soni bu yerda yozilmaydi — u har commit'da o'zgaradi va
eskirib qoladi. Joriy holatni yuqoridagi buyruq ko'rsatadi.

CI (`.github/workflows/tests.yml`) har push va PR'da shuni ishga tushiradi,
undan oldin `pyflakes` bilan ishlatilmagan import va aniqlanmagan nomlarni
tekshiradi.

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
