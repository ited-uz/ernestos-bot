# 165 bandlik audit (Codex spec) — kod bo'yicha triaj

Sana: 2026-10-08 · Branch: `claude/brave-mayer-78dw7h`
Commitlar: `1f52482` (M0), `4c3647d` (M1), `262a8b3` (M2), `290a464` (M3), `4021291` (M4).

Belgilar:
- ✅ Bajarildi: bu ishda kod o'zgardi.
- 🟢 Oldin bor edi: kodda tekshirildi.
- 🟡 Qisman.
- ❌ Rad etildi: sababi yozilgan.
- ⏳ Keyinga: katta ish, alohida qaror kerak.

Har bir bandga qo'yilgan "tekshirildi" belgisi quyidagi testlarga tayanadi:
- pytest: 1112 ta test.
- `tests/frontend_release.cjs` va `tests/frontend_audit50.cjs`.
- Brauzer E2E testlari haqiqiy server va test bazasiga qarshi ishga tushirildi: `scripts/e2e_app.sh` 23/23, `scripts/e2e.sh` 11/11.

Skrinshotlar faqat demo (mock) sahifadan olingan.

## Mock va haqiqiy tekshiruv — alohida

| Nima | Qanday tekshirildi |
|---|---|
| Backend (promo, focus↔bosqich, obuna to'lovi, CSV) | pytest, SQLite test bazasi. Production'ga deploy qilinmagan. |
| Mini App (brauzer) | E2E: haqiqiy `uvicorn` server va test bazasi, Playwright Chromium. |
| Skrinshotlar | `webapp/preview.js` mock. Ma'lumotlar namunaviy, server yo'q. |
| Android APK | CI'da yig'iladi. Telefonda qo'lda sinalmagan. |

## M0 — Obuna, promokod, mavzular

| Band | Holat | Izoh |
|---|---|---|
| 0.1 Obuna holati va muddati | ✅ | Badge `Max · Faol` va `Amal qilish muddati — sana gacha`. `User.subscription_*` ustunlari **qo'shilmadi** ❌: tarif allaqachon `plans.summary` (grant jadvali) da bor. Ikkinchi nusxa ikki xil javob beradigan holat yaratadi. |
| 0.2 Free → faqat Clean White | ✅ | Render vaqtida qulf qo'yiladi, saqlangan mavzu o'chirilmaydi. Server ham Free uchun boshqa mavzuni 402 bilan rad etadi. ⚠️ Risk: Free'da qorong'i rejim ham yopiq. Kechasi ishlatadiganlar uchun bu noqulay; konversiyani kuzating. |
| 0.3 Uchta mavzu (Blossom, Obsidian, Emerald) | ✅ | Eski 5 mavzu migratsiya `0014` orqali yangilariga o'tkaziladi. Migratsiya qayta ishga tushirilsa ham xavfsiz. |
| 0.4 Promokod | ✅ | Jadvallar, `POST /api/promocode/redeem`, rate limit va atomik `used_count` bor. Bot komandalari: `/promo` va admin uchun `/promo_admin`. |

## M1 — Bosh sahifa va Sozlamalar

| Band | Holat | Izoh |
|---|---|---|
| D01 | ✅ | Iqtibos xiralashtirildi. "Bugungi progress" bitta kartaga yig'ildi. Keyingi ishlardan 3 tasi ko'rinadi, qolgani "Barchasini ko'rish (N ta vazifa)" orqali ochiladi. |
| D02 | ✅ | Hozir kartasi ixcham: sarlavha 2 qator, meta bitta qatorda, tugma 42px. |
| D03 | ✅ | Bitta `+` tugma qoldi. Ovozli kiritish shu tugma ochadigan oynaning ichiga ko'chdi. |
| D04 | ✅ | Muhimlik: 🔥 Yuqori. Ranglar ham shu qoidaga keltirildi (S07 bandiga qarang). |
| D05 | ✅ | Raqamlar qalin. Har bir qismning o'z kichik progress chizig'i bor. |
| ST01 | ❌ | Spec matni "1 oy Max" va'da qiladi, lekin 5 ta do'st uchun Max mukofoti tizimda yo'q. Haqiqiy mukofot yozildi: "5 do'st taklif qiling — 1 oy Pro oling". |
| ST02–ST10 | ✅ | Hisob va Maxfiylik alohida bo'limlarda. "Chiqish" neytral rangda, o'chirish faqat Maxfiylik ichida. Do'stlar progressi so'z bilan yozildi. Til va Ko'rinish alohida qatorlar. Har qatorda joriy qiymat ko'rinadi. Profil yonida "Profilni tahrirlash" havolasi, rasmda kamera belgisi. |

## M2 — Interfeys (S01–S36)

| Band | Holat | Izoh |
|---|---|---|
| S01 | 🟢 | Sahifa pastdan 84px bo'sh joy bilan to'ldirilgan, sheet ochilganda tugma yashirinadi. |
| S02 | 🟢 | "Saqlash" / "Tarix" pastki menyu ostida qolishi faqat to'liq sahifali skrinshotda ko'rinadi. Haqiqiy ekranda menyu ularni yopmaydi. |
| S03 | ✅ | Ikkinchi darajali tablar chiziqli ko'rinishda. "8 ta ochiq vazifa" sarlavha yonida badge sifatida. |
| S04 | ✅ | Tablar: "Bugungi / Barchasi / Taqvim". Spec'dagi "Bugungi vazifalar / Barcha vazifalar" 360px ekranga sig'maydi. |
| S05 | ✅ | `+` tugma joyiga qarab yoziladi: "Odat qo'shish", "Vazifa qo'shish" yoki "Maqsad qo'shish". `aria-label` ham shunga mos. |
| S06 | ✅ | "Inbox" → "Barcha ochiq vazifalar". "Date countdown" → "Sanagacha qolgan vaqt". "Time countdown" → "Taymer". |
| S07 | ✅ | Qizil rang faqat kechikkan ish va xato uchun. "Yuqori" muhimlik endi to'q sariq. Odat darajalari va pul kategoriyalarida qizil yo'q. |
| S08 | ✅ | Odatning 4 holati: bo'sh halqa, boshlangan (to'q sariq), bajarilgan (yashil), bugun emas (kulrang, punktir). |
| S09 | ✅ | Namoz: "Bugun qayd etilgan · 3/5". Kun yakuni: "Saqlangan / Kutilmoqda". |
| S10 | ✅ | Taymerli odat: "Maqsad 2 soat". Pauzada: "Bajarildi 45 daq / 2 soat". |
| S11 | ✅ | "Bugun rejalashtirilmagan · Du Ch Ju" kulrang badge bilan. Bugungi hisobga kirmaydi; bu oldin ham shunday edi. |
| S12 | 🟡 | "Bugun · sana" qatori tablar ostida bor. Sticky emas: ichki sticky sarlavha telefon ekranida joy yeydi. |
| S13 | 🟢 | Yulduzcha = kunning asosiy ishi (top-1). Muhimlik — alohida badge. |
| S14 | ✅ | "2 kun kechikdi · 6 Oktabr". |
| S15 | ✅ | "Sanani o'zgartirish" tugmasi tayyor variantlarni beradi: Bugun, Ertaga, 7 kundan keyin, Sanasiz yoki istalgan sana. |
| S16, S17 | ✅ | "Eslatma · 09:30" va "Har oy" so'z bilan yoziladi. Alohida "pill"larga ajratilmadi: qator uzunlashib ketadi. |
| S18 | ✅ | "Oila · Bir kishi bajarsa yetarli". |
| S19 | ✅ | Holat (Ochiq / Bajarilgan) va Loyihalar alohida boshqaruvlar. |
| S20 | 🟡 | "5 ta ochiq vazifa" qo'shildi. "Bugun va keyingi 7 kun" sarlavhasi qo'shilmadi: mavjud "Yaqin kunlar" bo'limi aynan shu. |
| S21–S23 | ✅ | Bo'limlar: "Haftalik / Bosqichlar / Hayotiy". Hisoblagichlar so'z bilan: "1/2 bajarildi", "4 faol", "6 ta". Karta: "Haftaning asosiy maqsadi". |
| S24 | ✅ | Haftalik maqsadni bosqichga bog'lash mumkin. Backend'ga `weekly_focus.goal_id` qo'shildi (nullable). Faqat egasining tirik bosqichi qabul qilinadi; test bor. |
| S25 | 🟡 | Summa "MAQSAD $10M" deb yoziladi. "Joriy / maqsad" ko'rsatilmadi: joriy summa saqlanmaydi, foiz bosqichlardan hisoblanadi. Uni qo'shish qo'lda kiritiladigan yangi maydon degani. |
| S26 | 🟢 | Takrorlanmadi. Kartada `overflow:hidden` bor. |
| S27–S30 | ✅ | "1 / 3 savol to'ldirildi" yozuvi qo'shildi. AI tugmalari: "Ovozdan / Matndan tayyorlash". Tanlangan kayfiyat nomi bilan ko'rinadi. "Chala ham saqlanadi" izohi qo'shildi. "Qoralamani saqlash" tugmasi qo'shilmadi: qoralama har bosishda avtomatik saqlanadi (bu oldin ham bor edi). |
| S31–S34 | ✅ | Taqvim ostida tanlangan kun ro'yxati. "Bugunga" tugmasi. Countdown sanalari ★ bilan belgilanadi, sanani bossangiz o'sha kun ochiladi. Turlar rang bilan birga shakl bilan ham ajratiladi. Har katakning yozuvi elementlar sonini aytadi. |
| S35 | ✅ | "Demo rejim · o'zgarishlar faqat shu sessiyada saqlanadi". |
| S36 | ✅ | Amal tugmalaridagi emojilar chiziqli ikonkalarga almashtirildi. |

## M3 — Moliya (F01–F30)

| Band | Holat | Izoh |
|---|---|---|
| F01 | 🟢 / ✅ | Production'da sarlavha va kategoriyalar bir manbadan (oy yozuvlari) olinadi, xato yo'q edi. Farq va yo'qolgan "Transport 30 000" faqat demo mock'da edi. Mock tuzatildi. |
| F02 | ✅ | Eng katta qoldiq usuli: foizlar yig'indisi doim 100. Test bor. |
| F03 | ✅ | 5 ta eng katta kategoriya nomi bilan, qolganlari "Qolgan kategoriyalar" bo'lib bitta qatorda. |
| F04, F17–F20 | ✅ | Doimiy to'lovlar: "Oylik reja / To'langan / Qolgan". Hisob nomi va "Muddat · sana · N kun" ko'rinadi. To'langanda "To'langan · sana" chiqadi. Tugmalar: "Doimiy to'lov qo'shish" va "To'lovni qayd etish". Backend'ga `money_entries.subscription_id` qo'shildi (nullable); test bor. |
| F05 | 🟢 | S01 bilan bir xil: sahifa pastdan bo'sh joy bilan to'ldirilgan. |
| F06, F07 | ✅ | "Joriy balans · Barcha hisoblar". O'tgan oyda ko'k yozuv o'rniga oy nomi chiqadi. Qarzlar: "Barcha ochiq qarzlar" yoki "Shu oy qaytarilishi kerak". |
| F08 | ❌ | Kripto kurs (USDT ≈ so'm) qo'shilmadi. Ilova valyuta konvertatsiyasi qilmaydi, jonli kurs manbasi ham yo'q. Hisobda "summa so'mda yuritiladi" deb yozildi. |
| F09, F10 | ✅ | Hisoblar ichidagi ikkinchi jami olib tashlandi. Tez kiritish qatori faqat Yozuvlar tabida turadi. |
| F11 | 🟢 | Matn avval tahlil qilinadi, keyin "Yozuvni tekshiring" oynasi chiqadi. Tasdiqlamaguningizcha hech narsa saqlanmaydi. |
| F12 | ✅ | Bitta oynada Chiqim / Kirim / O'tkazma. |
| F13 | ✅ | × o'rniga "⋯" menyu: Tahrirlash yoki O'chirish. O'chirish tasdiq so'rash o'rniga **Undo** beradi; bu kamroq bosish talab qiladi va xatoni tuzatish ham oson. |
| F14, F15 | ✅ | Qidiruv va kategoriya chiplari. Yozuvlar kunlar bo'yicha guruhlanadi (Bugun / Kecha / sana), har kunning sof summasi yonida. |
| F16 | 🟢 | `moneyFmt` / `moneyNum` — yagona formatlovchi. |
| F21–F26 | ✅ | Diagramma sarlavhasi: oy va jami. Legendada summa va %. Limit ustuni "Sarflandi / Limit", ostida "Qoldi". Limitsiz kategoriya: "Limit belgilanmagan · Limit qo'yish". Progress rangi foydalanishga qarab: <75% yashil, 75–90% sariq, >90% qizil. "✎ bosing" yozuvi olib tashlandi. |
| F27–F29 | ✅ | Oylar 3 harfli. Y o'qida qiymatlar. Ustunni bossangiz izoh chiqadi. Kelasi oylar punktir. |
| F30 | ✅ | "Men qarz berdim / Men qarz oldim". |

## M4 — Statistika, Qadam, Jamoa (V01–V32)

| Band | Holat | Izoh |
|---|---|---|
| V01, V02 | ✅ | Katta raqam endi sanoqqa teng: 1/5 = 20%. Og'irlikli ball ostida nomi bilan yoziladi ("Muhimlik bo'yicha 27%"). Formula o'zgarmadi. |
| V03 | 🟢 | 3/5 namoz chizig'i 60%. |
| V04, V05 | 🟢 / ✅ | Server bugungi nuqta va eng yaxshi kunni bir xil seriyadan oladi. Farq faqat mock'da edi; mock endi ham bitta seriyadan hisoblaydi. |
| V07, V28 | ✅ | Davrning sana oralig'i va "kunlar o'rtachasi" yozuvi qo'shildi. |
| V09 | 🟢 | Umumiy % kartasida (i) belgisi formulani ochadi. |
| V10, V11 | ✅ | Segmentlar soni maxrajga teng (5/5). "Boshlandi" belgisi oldin ham bor edi. |
| V12, V13 | ✅ | "Bu darajada 12/25 (48%)" yozuvi. Qadam = faollik ochkosi (XP) degan izoh. |
| V14, V15 | ✅ | "Foydalanuvchilarning 82% idan oldindasiz". Yorliqlar: "Shu haftalik o'rin", "Shaxsiy rekord", "Pog'ona o'sishi". |
| V16, V17 | ✅ | "Bugun olindi +7 · Kunlik limit 12". Seriya va himoya kunlarini tushuntiruvchi oyna qo'shildi; qoidalar `services.py` dan olindi, chegara testi bor. |
| V18 | 🟢 | Ustma-ust tushish faqat to'liq sahifali skrinshotda ko'rinadi. |
| V20, V21 | ✅ | Jamoa ekranidagi `+` jamoaga vazifa qo'shadi va yonida nomi yozilgan. Toj o'rniga "Egasi" yoki "Admin" badge. |
| V22–V24 | ✅ | Bajarilganlar "Bajarilgan ishlar" bo'limiga yig'iladi. "Siz bajardingiz · Guruhda 1/2". ANY uchun "Bir kishi bajarsa yetarli". |
| V29, V30 | ✅ | Legendada endi o'chirib-yoqiladigan chiplar. "p." o'rniga "foiz punkt". |
| V06, V08, V19, V25–V27, V31, V32 | ⏳ | Jamoa hisobotini qayta bo'lish, mas'ullar ro'yxati, jamoani pastki menyuga chiqarish. Bular navigatsiya darajasidagi qarorlar va sizning tasdig'ingizni talab qiladi. |

## M4 — Ko'p guruhli arxitektura (T33–T56)

| Band | Holat | Izoh |
|---|---|---|
| T35 yangi model (Group, WorkItem, WorkOccurrence…) | ❌ | Mavjud Team modeli (`TeamTask`, `TeamHabit`, `TeamTaskDone`, `TeamHabitLog`, `TeamActivity`) shu vazifani bajaradi va testlar bilan himoyalangan. Parallel ikkinchi model qo'shish ikki xil haqiqat manbasini yaratadi; mavjud model kengaytiriladi. |
| T33, T34, T36 | 🟢 | Bir nechta jamoa va almashtirish tablari bor. Yangi ish joriy jamoaga yoziladi. |
| T37–T40 | 🟢 | Mas'ul tanlash bor (assignees). Bajarish qoidasi all / any. Belgilar har bir a'zo uchun alohida qator, `uq_team_task_done` cheklovi bilan. |
| T43, T50, T54, T55 | 🟢 | Jamoa loyihalari va ularning progressi bor. Faoliyat jurnali — `TeamActivity`. Foiz yonida sanoq ko'rinadi. Shaxsiy bo'limlar (Moliya, Namoz, Kun xulosasi) jamoaga ko'rinmaydi; "begona" testlari buni tekshiradi. |
| T45–T47 | 🟡 | Rollar (egasi, admin, a'zo), chiqish, a'zoni chiqarish, egalikni topshirish va o'chirish bor. Taklif holatlari (expired, revoked) yo'q. |
| T41, T42, T44, T48, T49, T51–T53, T56 | ⏳ | Ko'p guruhga nusxalash, 10+ guruh uchun qidiruv, avatar to'plami, guruhni ovozsiz qilish, guruh vaqt zonasi, "ANY hissasi" metrikasi, guruh eksporti. Har biri alohida reja va sizning tasdig'ingiz bilan qilinadi. |

## M4 — Xavfsizlik (S57–S80)

| Band | Holat | Izoh |
|---|---|---|
| S57, S58 IDOR | 🟢 | Har bir jamoa so'rovida `_require_team` a'zolikni tekshiradi. 5 ta "stranger" testi bor. |
| S59 | 🟢 | `toggle_team_task` faqat so'rov yuborgan odamning belgisini o'zgartiradi. |
| S60, S61 | 🟢 | Jamoa uchun alohida token yo'q: a'zolik har so'rovda tekshiriladi. Chiqarilgan a'zo kirish huquqini darhol yo'qotadi. |
| S62 | 🟢 | `test_a_task_cannot_be_filed_on_another_teams_shelf`. |
| S64, S66, S67 | 🟢 | Har bir a'zo uchun alohida qator, unique cheklov bilan. ANY qoidasi "kamida bitta belgi"ni o'qiydi, shuning uchun mukofot ikki marta berilmaydi. |
| S68 | 🟢 | Optimistik UI yozuv o'xshamasa holatni orqaga qaytaradi (K04 ishidan beri). |
| S69, S71, S77 | 🟢 | XP `event_key` unique: tez-tez bosish yoki retry qo'shimcha ochko bermaydi. |
| S72 | 🟢 | Rejasiz kun seriyani uzmaydi. Himoya kuni kuniga ko'pi bilan bitta ishlatiladi. |
| S73 | ✅ | Maxraj 0 bo'lsa "Reja yo'q" yoziladi, NaN% ko'rsatilmaydi. |
| S74 | 🟢 | Jamoa natijasi foizlarning o'rtachasi emas, jami bajarilgan / jami majburiyat. |
| S76 | ✅ | Demo statistika bitta seriyadan hisoblanadi. |
| S78 | ✅ | `csv_cell()` qo'shildi, test bor. Hozirgi CSV'da foydalanuvchi matni yo'q edi, ya'ni zaiflik bo'lmagan; himoya kelajakdagi matnli ustunlar uchun. |
| S79 | ✅ | 360, 390 va 768px'da 11 ta ekranda sahifa gorizontal siljimaydi (Playwright). Chiplar o'z ichida aylanadigan qatorda — bu ataylab. |
| S80 | 🟢 | Vazifalar cursor bilan sahifalanadi. Moliya oyiga 100 ta yozuv ko'rsatadi, to'liq tarix eksport orqali olinadi. |
| S63, S65, S70, S75 | ⏳ | Tekshirilmadi. Yangi kod ham yozilmadi. |

## Nima qilinmadi, nima uchun

- Production'ga deploy qilinmadi.
- Haqiqiy foydalanuvchiga xabar yuborilmadi.
- Tashqi hisoblar ulanmadi.
- Migratsiyalar faqat test bazasida ishga tushirildi.
- `main` branchga merge sizning tasdig'ingizni kutadi.

---

# v16 — yakuniy tahrir (launch oldidan)

Commitlar: `3e96300` (tariflar), `956eacf` (guruhlar Reja'da), `9274dbf` (AI Hozir),
`ce5531e` (internetsiz ishlash), `e9f2ce2` (dark rejim), `29c46ce` (guruhlar hisobi).

## Egasining 4 ta talabi

| Talab | Holat | Nima qilindi | Qanday tekshirildi |
|---|---|---|---|
| Tarifda yo'q yoki kam funksiya — qizil | ✅ | Tarif jadvalida yo'q funksiya qizil ✕ bilan, Max'dagidan kichik limit qizil raqam bilan ko'rsatiladi. Ilovadagi barcha qulflar ham qizil: mavzular, oy/yil statistikasi, AI Hozir, yangi guruh, moliya. | Skrinshot (mock). Free jadval qiymatini tekshiruvchi frontend testi. |
| Dark rejim — kuchli, har mavzu o'zicha | ✅ | Clean — OLED qora. Obsidian — neon noir (cyan/violet). Emerald — qora-oltin, oltin shimmer bilan (kamaytirilgan harakat sozlamasida o'chadi). Blossom — tungi atirgul shishasi. Hammasi tokenlar bilan, komponentlarda qattiq kodlangan rang yo'q. | Palitra, kontrast va "rang yozilmagan" testlari (78 ta). 4 ta mavzu dark skrinshoti (mock). |
| Hozir — AI tavsiyasi asosida | ✅ | Faqat Pro/Max. AI faqat sizning ochiq vazifalaringiz orasidan tanlaydi va bir jumla sabab yozadi. Tanlov 45 daqiqa keshlanadi, kuniga limit bor. Pin qilingan vazifa, uyg'onish va namoz kartasini almashtirmaydi. AI o'chiq bo'lsa, oddiy qoida ishlaydi. Free'da qizil qulf qatori ko'rinadi. | pytest: AI soxta javob bilan. Nomzoddan tashqari tanlov rad etiladi, keshdan javob, tarif/rozilik/limit tekshiriladi. **Haqiqiy AI kaliti bilan sinalmagan** — bu test muhitida kalit yo'q. |
| Internetsiz hamma narsa qabul qilinadi | ✅ | Barcha yozuvlar telefonda navbatga turadi va internet kelganda o'sha idempotentlik kaliti bilan yuboriladi: tezkor qo'shish, vazifa, odat, namoz, pul, xulosa, qarz, maqsad, guruh. Ovozli yozuv IndexedDB'da saqlanadi va internet kelganda tasdiqlash kartasi chiqadi. Kirish, to'lov, tarif va AI navbatga kirmaydi. | E2E (haqiqiy server, brauzer offline): vazifa va pul yozuvi navbatga tushdi va bazaga **bir martadan** yetdi. Pytest: offline tezkor qo'shish pulni yozadi, "ertaga" yozilgan kundan hisoblanadi. Ovoz navbati brauzerda qo'lda sinalmagan, faqat kod va statik testlar bor. |

## Sizning javoblaringiz bo'yicha

- **Tillar:** uz/ru/en qoldi.
- **Free'ga dark ochildi.** Rangli mavzular Pro'da.
- **Free qattiqlashdi:**
  - faol vazifalar 30 → 20;
  - maqsadlar 3 → 2;
  - ovoz/AI haftasiga 5 → 3;
  - oy/yil statistikasi qizil qulf bilan.

  Limitdan oshgan mavjud yozuvlar o'chmaydi, faqat yangisini qo'shib bo'lmaydi.
- **Guruhlar Reja'da:** Odatlar · Vazifalar · Maqsadlar · Guruhlar.
  - Guruh nomi katta sarlavhada.
  - Har guruh nomi bilan chip, oxirgi chip "Yangi guruh". Free'da u qizil qulf bilan: jamoaga ega bo'lish Pro, qo'shilish bepul.

## 36 ta interfeys bandi — yakuniy holat

1. **01, 18 (suzuvchi tugma):** ✅ `+` pastga aylantirganda yashirinadi, yuqoriga aylantirganda, sahifa boshida va oxirida qaytadi.
2. **02 (klaviatura):** 🟢 Avvaldan to'g'ri — tekshirildi. Maydon fokusda bo'lsa, pastki menyu va `+` yashiriladi.
3. **12 (sticky sana):** 🟡 Sana qatori bor, lekin sticky emas.
4. **25 (joriy summa):** 🟡 Joriy summa maydoni yo'q; foiz bosqichlardan hisoblanadi.

Qolgan bandlar v15 jadvalidagidek.

## 30 ta moliya bandi — yakuniy holat

- **08 (USDT kursi):** ❌ Yo'q (sabab yuqorida). Kripto hisob ostida "summa so'mda yuritiladi" deb yozilgan.
- Qolgan bandlar v15 jadvalidagidek.

## 80 ta guruh/statistika bandi — v16 o'zgarishlari

| Band | Holat | Izoh |
|---|---|---|
| V08 | ✅ | Statistika kartasi nomi: "Guruhdagi ishlarim". |
| V19, V32 | ✅ | Guruh nomi sarlavhada, guruhlar Reja'da. |
| V20 | ✅ | `+` jamoaga vazifa qo'shadi va nomi yozilgan. |
| V25 | ✅ | "Mas'ullar · …", "✓ kim bajardi", "Hali bajarmagan · …". |
| V26 | ✅ | O'chirish oldin "barcha a'zolar uchun" deb so'raydi. |
| V27, T53 | ✅ | "Guruh ishlari 1/4 yopildi · Shaxsiy belgilar 5/8". ANY vazifa bitta ish deb sanaladi — test bor. |
| T46, T51 | 🟢 | Avvaldan to'g'ri — tekshirildi. Taklif havolasini yangilash/bekor qilish va guruh bo'yicha bildirishnoma darajalari mavjud. |
| T35 | ❌ | Rad etildi (sabab yuqorida). |
| T41, T42, T44, T48, T49, T52, T56; S63, S65, S70, S75 | ⏳ | Keyingi bosqich. |
| V06, V31 | ⏳ | Keyingi bosqich. |

## Tekshiruv natijalari (v16)

| Tekshiruv | Natija |
|---|---|
| pytest | 1118 passed |
| `tests/frontend_release.cjs`, `tests/frontend_audit50.cjs`, `tests/test_agent_ui.cjs` | o'tdi |
| E2E: ilova qobig'i, haqiqiy server, offline qadami bilan | 24/24 |
| E2E: Mini App | 11/11 |
| Kengliklar: 360, 390, 768, 1280 px | 11 ta ekranda sahifa gorizontal siljimaydi |

## Mock va haqiqiy

- **Skrinshotlar:** `webapp/preview.js` mock.
- **AI Hozir sababi:** demoda yozib qo'yilgan matn, modeldan kelmagan.
- **Haqiqiy server testlari:** pytest va E2E. Ular SQLite test bazasida ishladi. Production'ga deploy qilinmagan.
- **APK:** CI'da yig'iladi, telefonda qo'lda sinalmagan.

## v17 — xatolar ovi va sokin dizayn

**Usul.** Brauzer har bir ekranni ochadi va undagi har bir tugmani bosadi: 22 ta ekran, Free va Max, yorug' va tungi rejimda. Har bir bosishdan keyin quyidagilar qidiriladi:
- JS xatosi;
- matnda `undefined`, `NaN` yoki `[object Object]`;
- tarjima qilinmagan kalit;
- gorizontal siljish.

Ikki xil muhitda ishga tushirildi:
- demoda (mock);
- haqiqiy serverda (SQLite test bazasi). Har bir serverga ~2 000 so'rov ketdi, 35 ta yozuv amali bajarildi.

| Topildi | Tuzatildi |
|---|---|
| Odat tarixi, Qadam va Haftalik xulosada sonlar o'rniga `undefined`. Faqat demoda, chunki mock'da yo'llar yo'q edi. | Mock'ga yo'llar qo'shildi. Ekranda yetishmagan son endi "—" bo'lib chiqadi (`n0`). |
| Sozlamalar → Eslatmalar: "Telefon bildirishnomalari" ostida "…" qotib qolardi (ko'prik javob bermasa). | Javob bo'lmasa yoki xato bo'lsa — "Bu versiyada telefon bildirishnomasi yo'q" deb yoziladi. |
| Statistika: "↓-5 foiz punkt" — ikki marta manfiy. | Strelka belgining o'zi: "↓ 5 foiz punkt". |
| Tungi rejim ko'zni charchatardi: yaltiroq nur, oltin jilo, neon. | Har mavzuda sokin to'q kulrang fon, yumshoq aksent rangi. Nur va jilo o'chirildi. Clean — iOS tungi kulrang. |
| Test yarim tundan uyg'onish vaqtigacha yiqilardi: "Hozir" kartasida avval "Turdim" chiqadi. | Testlar va E2E seed soatga bog'liq emas. |

| Tekshiruv | Natija |
|---|---|
| Crawler, demo: Free (yorug') va Max (yorug') | topilma yo'q |
| Crawler, haqiqiy server: Max (yorug') va Free (tungi) | topilma yo'q. Barcha javoblar 200, faqat Stars to'lovi 503 qaytardi — lokal testda bot yo'q, bu kutilgan |
| pytest | 1121 passed |
| E2E: ilova qobig'i va Mini App | 24/24 va 11/11 |
