# Soddalashtirish rejasi

Foydalanuvchi feedbacki: *"dizayn juda murakkab, asabga tegadi"*.

Kodni o'qib chiqqandagi xulosa: shikoyat **vizual dizayn** haqida emas.
Ranglar, shrift, bo'shliq, kontrast — hammasi izchil. Shikoyat ikki narsa
haqida:

1. **Qiymat ko'rinmasdan turib so'raladigan narsalar** (telefon, obuna).
2. **Birinchi kunning yuki va tushunchalar soni** — foydalanuvchi hech
   narsa tanlamasdan turib 17 ta majburiyat va ~20 ta tushuncha oladi.

Quyida beshta band. Har biri mustaqil — birini qilib, qolganini
qoldirish mumkin.

---

## A. Kirish to'siqlarini yumshatish

### Muammo

`ONBOARDING_STEPS = ["language", "phone", "subscribe", "done"]`
— [app.py:801](../app.py)

Foydalanuvchi mahsulotning birorta ekranini ko'rmasdan turib:

- **telefon raqamini beradi** — Skip yo'q, [app.py:804-815](../app.py)
  dagi docstring buni ataylab shunday qilganini yozadi
  («There is no Skip: the number is what account recovery is keyed on»);
- **kanalga obuna bo'ladi** — [app.py:661](../app.py),
  `finish_onboarding` obunani tasdiqlamaguncha
  `onboarding_step = "subscribe"` da qoladi ([app.py:915-940](../app.py)).

Ikkalasi ham mahsulot uchun mantiqiy. Lekin ikkalasi ham **birinchi 30
soniyada**, hech qanday qiymat berilmasdan so'raladi. "Asabga tegadi"
hissiyotining eng katta ulushi shu.

### O'zgarish

**A1. Telefonni ixtiyoriy qilish.**

- [app.py:801](../app.py) — `ONBOARDING_STEPS` dan `"phone"` ni olib
  tashlash: `["language", "subscribe", "done"]`.
- [app.py:804](../app.py) `phone_keyboard` — ikkinchi qator sifatida
  `t(lang, "btn_phone_later")` tugmasi qo'shish (yoki inline
  `callback_data="phone:skip"`).
- [app.py:817](../app.py) `resume_onboarding` — `elif step == "phone"`
  bloki qoladi, chunki uni Sozlamalardan chaqirish mumkin.
- Yangi handler: `phone:skip` → `user.onboarding_step = "subscribe"` va
  `resume_onboarding(..., "subscribe")`.
- [app.py:871-873](../app.py) `on_contact` da `onboarding_step` ni
  `"subscribe"` ga o'tkazish mantig'i o'zgarmaydi.
- Telefon o'rniga: 3-kunda yoki `export` bosilganda bir marta so'rash
  ("hisobingizni tiklash uchun kerak"), rad javobini eslab qolish.

**A2. Obunani birinchi ochilishdan keyinga surish.**

Ikki variant:

- *Yumshoq:* onboarding `subscribe` siz tugaydi, `guard`
  ([app.py:682](../app.py)) esa obunani faqat **2-kirishdan** boshlab
  talab qiladi. `user.created_at` bor ([db.py](../db.py) `User`), shuning
  uchun yangi ustun kerak emas.
- *Qattiq:* obuna qoladi, lekin undan **oldin** bitta ekran ko'rsatiladi —
  "ErnestOS nima qiladi" va Mini App tugmasi demo rejimida.

Tavsiya: yumshoq variant. Kanal obunasi mahsulot yoqqandan keyin
so'ralsa, konversiya ham yuqori bo'ladi.

**Xavf:** past. `guard` allaqachon uchta natijani (a'zo / a'zo emas /
tekshirib bo'lmadi) to'g'ri ishlaydi, faqat qachon chaqirilishi
o'zgaradi.

**Mehnat:** ~80 qator `app.py` ichida, uchta tilga 2-3 ta yangi satr.

---

## B. Nol holatdan boshlash

### Muammo

Ro'yxatdan o'tgan zahoti `get_or_create_user`
([services.py:229](../services.py)) **7 ta odat** yaratadi:

```
Get up      non_negotiable  (wakeup)   ← qizil
5x namoz    non_negotiable  (prayer)   ← qizil
Kundalik    non_negotiable  (journal)  ← qizil
Deep flow   target
Sport       target
Podcast     bonus
Read        bonus
```

— [services.py:179](../services.py)

Ustiga:

- **5 ta namoz** har kuni;
- **Kundalik 5 ta savolning hammasi** — `sync_journal_habit`
  ([services.py:1644](../services.py)) 5 tadan 4 tasiga javob berilgan
  kunni ham **bajarilmagan** deb belgilaydi.

Jami **17 ta belgi**, va foydalanuvchi bularning birortasini tanlamagan.
Birinchi kuni `overall_percent` ([services.py:1969](../services.py))
0–15% chiqadi. Mahsulot birinchi daqiqadanoq hukm qiladi.

Odat nomlari inglizcha (`Get up`, `Deep flow`, `Read`) — o'zbek
foydalanuvchi uchun qo'shimcha yot qatlam.

### O'zgarish

**B1. Default odatlarni 7 tadan 1 taga tushirish.**

- [services.py:179](../services.py) `DEFAULT_HABITS` — faqat
  `("Get up", "non_negotiable", "wakeup")` qoldirish.
- Namoz va Kundalik odatlari **birinchi marta o'sha modul ochilganda**
  yaratilsin — gender uchun allaqachon shunday qilingan
  ([app.py:799](../app.py) izohi: «Gender ... is asked the first time
  prayer is opened»). Xuddi shu naqsh.
- Kod bunga tayyor: `overall_components`
  ([services.py:1941](../services.py)) `habits_total` 0 bo'lsa `None`
  qaytaradi, `prayer_habit is None` bo'lsa namozni hisobga qo'shmaydi,
  `sync_journal_habit` va `wake_state` odat yo'qligini normal holat deb
  qabul qiladi. Ya'ni **nol odat sindirmaydi**.
- Mavjud foydalanuvchilarga tegmaydi — migratsiya kerak emas.

**B2. Onboardingdan keyin bitta savol: "Bugundan boshlab nimani
kuzatasiz?"**

3-5 ta tayyor variant (o'zbekcha: *Erta turish · Sport · Kitob o'qish ·
Deep flow · Namoz*) + "Keyinroq". Tanlangani `add_habit` orqali
qo'shiladi. Bu odatni **foydalanuvchining o'z tanloviga** aylantiradi —
psixologik farq katta.

**B3. Kundalik: 5 ta emas, 1 ta savol.**

- [services.py:1543](../services.py) `JOURNAL_QUESTIONS` — beshtasi
  qolsin, lekin `journal_is_complete` "hammasi" emas **"kamida bittasi"**
  bo'lsin. Qolgan to'rttasi ixtiyoriy, "yana yozish" ostida.
- Bu `sync_journal_habit` ning bitta shartini o'zgartiradi
  ([services.py:1644](../services.py)).
- Mini App tomonida `journalTab` ([webapp/index.html:1869](../webapp/index.html))
  birinchi savolni ochiq, qolganini `<details>` ichida ko'rsatadi.

**B4. Birinchi hafta foizsiz.**

`overall` foizi `user.created_at + 7 kun` dan oldin **raqam emas, matn**
ko'rsatsin: *"1-kun. Kuzatuv boshlandi."* Yoki oddiy "3/5 bajarildi".

- Server: [services.py:2541](../services.py) `home()` payloadiga
  `"onboarding_day": (today - user.created_at.date()).days` qo'shish.
- Mini App: `scoreBlock` ([webapp/index.html:1560](../webapp/index.html))
  `d.onboarding_day < 7` bo'lsa katta foiz o'rniga sodda hisobni
  chizsin.

**Xavf:** o'rta. `DEFAULT_HABITS` testlarda kutilayotgan bo'lishi mumkin
— `tests/` ni tekshirish kerak. Statistika hisoblari nol maxrajga
tayyor.

**Mehnat:** `services.py` da ~60 qator, `app.py` da onboarding qadami,
`webapp/index.html` da ikki blok, uch tilga ~10 ta yangi kalit.

---

## C. Bitta narsa uchun uchta tizim

### Muammo

Hozir "eng muhim ish" **uch xil** ko'rinishda mavjud:

| Nomi | Saqlanishi | Chegara | Qayerda |
|---|---|---|---|
| Bugungi missiya | `Task.focus_day` | `MAX_TOP3 = 1` ([services.py:1161](../services.py)) | Bosh sahifa, `missionBlock` |
| Hafta fokusi — primary | `WeeklyFocus` jadvali, slot 1 | `PRIMARY_SLOT` ([services.py:1404](../services.py)) | Vazifalar ekrani, `weekFocusBlock` |
| Hafta fokusi — supporting | `WeeklyFocus`, slot 2-3 | `MAX_FOCUS = 3` ([services.py:1403](../services.py)) | Xuddi shu yerda |

Ikkinchisi va uchinchisi Mini App'da **ham "missiya" deb ataladi** —
`t("mission_main")`, `t("add_mission")`
([webapp/index.html:2012, 2025](../webapp/index.html)).

Ya'ni foydalanuvchi ikkita boshqa-boshqa joyda, ikkita boshqa-boshqa
ma'lumotlar bazasi jadvalida, bir xil nom ostida ikki xil narsa
yaratadi. Buni tushunish mumkin emas.

`home()` payloadi uchalasini ham yuboradi — `top3`, `mission`, `focus`
([services.py:2565-2585](../services.py)) — lekin `SCREENS.home`
([webapp/index.html:1444](../webapp/index.html)) faqat `top3[0]` ni
chizadi. `mission`, `focus`, `now`, `week`, `birthdays` maydonlari
**har bir Home so'rovida hisoblanadi va tashlab yuboriladi**.

### O'zgarish

**C1. `WeeklyFocus` ni butunlay olib tashlash.** Kunlik missiya
(`Task.focus_day`) yetadi va u haqiqiy vazifaga bog'langan — parallel
ro'yxat emas.

- O'chadi: `list_focus`, `week_focus`, `primary_focus`, `add_focus` va
  qolgan `WeeklyFocus` funksiyalari ([services.py:1403-1500](../services.py)),
  `/api/focus` endpointi, `weekFocusBlock`
  ([webapp/index.html:2001](../webapp/index.html)), `focus-*` action'lari
  ([webapp/index.html:3407-3440](../webapp/index.html)).
- Jadval o'chirilmasin — faqat o'qilmasin (ma'lumot yo'qolmasligi uchun).

**C2. `home()` payloadini tozalash.** `mission`, `focus`, `now`, `week`,
`birthdays` — ekranda chizilmaydi, hisoblanmasin
([services.py:2565-2586](../services.py)). Bosh sahifa tezlashadi.

**Xavf:** past, lekin diffi katta. Bot tomonida `focus` ishlatilishini
`grep` bilan tekshirish shart.

**Mehnat:** ~250 qator o'chirish, ~20 qator o'zgartirish.

---

## D. Ekran zichligini kamaytirish

### D1. Statistika — bir ekranda ~20 ta raqam

[webapp/index.html:2273-2370](../webapp/index.html):

hero (1) + metrikalar (3) + davr segmenti (3) + 4 seriyali grafik +
o'rtachalar (4) + eng yaxshi kun (1) + **namoz tafsiloti (6)** + yuklab
olish.

O'zgarish:

- Namoz tafsilotini ([index.html:2350-2365](../webapp/index.html))
  alohida sahifaga/sheet'ga ko'chirish — "Namoz tafsiloti →".
- Grafikda default bitta chiziq (`overall`), qolgani legend orqali —
  hozir `state.statsSeries` bo'yicha to'rttasi ham yonishi mumkin.
- "O'rtacha" bloki ([index.html:2329](../webapp/index.html)) hozirgi
  metrikalar bilan bir xil to'rt raqamni takrorlaydi. Bittasini olib
  tashlash.

### D2. Vazifalar — 7 ta filtr chip

[webapp/index.html:2059-2062](../webapp/index.html):
`Hammasi · Bu hafta · Bu oy · Kechikkan · Sanasiz · Muhim · Past`

Uchtaga tushirish: **Hammasi · Kechikkan · Sanasiz**. "Bu hafta / bu oy"
allaqachon kalendarda bor, prioritet esa qatorda nuqta bilan ko'rinadi.

### D3. Sozlamalar sheet'i

[webapp/index.html:2771-2869](../webapp/index.html) — bitta uzun
skroll ichida: til (3) + rejim (3) + tema (5) + timezone + 4 ta
bildirishnoma + 2 ta vaqt tanlagich + profil + 5 ta ma'lumot/xavf bandi.

O'zgarish: uchta bo'limga bo'lish — **Ko'rinish** · **Bildirishnomalar**
· **Hisob** — har biri o'z sheet'ida, asosiy sozlamalar ro'yxatdan
ochiladi. Temalarni 5 tadan 2-3 taga tushirish ham ko'rib chiqilsin: 5 ta
tema × yorug'/qorong'i = 10 ta variant, va bu tanlov mahsulot qiymatiga
hech narsa qo'shmaydi.

### D4. Bot menyusi Mini App'ni takrorlaydi

[app.py:731-750](../app.py) — 7 ta tugma (`Bosh sahifa · Odatlar ·
Vazifalar · Statistika · Sozlamalar · Fikr · Turdim`) + Mini App.

Bu ikkita to'liq mahsulot degani. Botni **uchta** narsaga qisqartirish:
`Turdim` · `Tez qo'shish` · `Ilovani ochish`. Qolgan hammasi ilovada.
Bot — bildirishnoma va tezkor kirish kanali, ikkinchi interfeys emas.

**Xavf:** D4 eng katta mahsulot qarori — botni afzal ko'radigan
foydalanuvchilar bo'lishi mumkin. Avval o'lchash kerak.

---

## E. "Ilg'or" narsalarni yashirish

Vazifa formasi ([webapp/index.html:2386](../webapp/index.html)) aslida
yaxshi tuzilgan: bitta maydon + 4 ta chip, qolgani `<details>` ichida.
Bu naqsh **butun ilovaga** yoyilsin:

- Odat jadvali (har kuni / ish kunlari / tanlangan kunlar)
  ([webapp/index.html:2517](../webapp/index.html)) — default "har kuni",
  qolgani "Ko'proq" ostida.
- Odat kategoriyalari (non-negotiable / target / bonus) — yangi
  foydalanuvchida umuman ko'rsatilmasin, hamma odat bir xil bo'lsin;
  kategoriya 10+ odat bo'lganda paydo bo'lsin.
- `recurrence`, `remind_before`, `priority`, `project` — hozirgidek
  `<details>` da qoladi, lekin `f.open` holati saqlanmasin (hozir
  [index.html:2481](../webapp/index.html) da saqlanadi, ya'ni bir marta
  ochgan odam har safar to'liq formani ko'radi).

---

## Tartib

| # | Band | Ta'sir | Mehnat | Xavf |
|---|---|---|---|---|
| 1 | A — kirish to'siqlari | ★★★ | past | past |
| 2 | B — nol holat | ★★★ | o'rta | o'rta |
| 3 | C — uchta missiya tizimi | ★★ | o'rta | past |
| 4 | E — ilg'or rejim | ★★ | past | past |
| 5 | D — ekran zichligi | ★ | o'rta | D4 da yuqori |

A va B birgalikda feedbackning katta qismini yopadi va bir-biriga
bog'liq emas — parallel qilinishi mumkin.

## O'lchash

Bu o'zgarishlarning ishlaganini bilish uchun kerak bo'ladigan uchta
raqam (hozir hech biri yig'ilmaydi):

1. `/start` bosganlarning nechtasi `onboarded = True` gacha yetadi;
2. 1-kunda kamida bitta narsa belgilaganlar ulushi;
3. 7-kunda qaytganlar ulushi.

`log_event` ([app.py:943](../app.py)) allaqachon onboarding tugashini
yozadi — 1-raqam deyarli tayyor.
