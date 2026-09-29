# ErnestOS v9.1 — soddalashtirish, Pul, tezlik

v9 zip ustiga. Backend, bot va Mini App bir xil `services.py` dan o'qiydi;
formula (`OVERALL_WEIGHTS`, tier og'irliklari) **o'zgarmagan**.

## Tuzatilgan xatolar

| Muammo | Sababi | Yechim |
|---|---|---|
| Mini App sekin ochiladi | Bosh sahifa har ochishda `/api/summary` ni kutardi: 60 kunlik ballni qayta hisoblash, yangi akkauntda ham **~3 600 SQL so'rov** (Postgres'da bir necha soniya). Ustiga `me → teams → home` ketma-ket | Home bitta `/api/home` bilan yuklanadi; `me` va `home` parallel; jamoa nomlari `/api/me` ichida. Hisob-kitobda per-tranzaksiya memo va akkaunt ochilishidan oldingi kunlar uchun tez chiqish. Summary: 4 884 → 804 so'rov (to'liq tarix, yopilmagan kunlar), yangi akkauntda ~50 |
| «+» tugmasi orqasidagi narsa | Floating «+» yuklanish paytida bo'sh sahifa ustida chiqardi va oxirgi qatorni yopardi | Floating tugma olib tashlandi; har ekranning «+» si sarlavhasida. Tab bar ma'lumot kelmaguncha chizilmaydi |
| «Ko'chirish: Bugun · Ertaga · Sanasiz» ishlamasdi | `A` obyektida ikkita `"task-move"` kaliti bor edi — ikkinchisi (shaxsiy ↔ jamoa) birinchisini jimgina almashtirardi | Kechikkan vazifa chiplari `task-reschedule`; jamoa vazifasi ham ko'chadi. Test: har action nomi bir marta |
| Taqvimda o'tgan kunga vazifa qo'shish taklifi | — | O'tgan kun faqat qilingan ishlarni ko'rsatadi; qo'shish faqat bugun va keyingi kunlarda. Bo'sh kelajak kuni ham bosiladi |
| Taqvim burchagidagi chiziqli quti | `.cal-day.empty` umumiy `.empty` (chiziqli bo'sh holat) stilini olardi | `cal-day pad` |
| Pyflakes CI da yiqilardi | `show_report_health` da ishlatilmagan `lang` | Olib tashlandi |

## Bot

* **Odatlar:** tepada faqat odatlar, bitta ustunda (daraja sarlavhalari yo'q);
  pastda: ☀️ Turdim (faqat hali belgilash mumkin bo'lsa), ➕ Qo'shish · ✏️ Tahrirlash,
  📋 Tayyor odatlar (10 ta), ♻️ Qaytarish.
* **«Turdim»** doimiy klaviaturadan olib tashlandi — faqat Odatlar ekranida.
  «Turdim» deb yozish ishlaydi.
* **Date/Time countdown** tugmalari Home, Odatlar va Vazifalardan to'liq olib
  tashlandi (Mini App'da qoldi). `/countdown`, `/timer` buyruqlari qoldi.
* **Home:** sana → 👉 Hozir → bir qator sanoq → bugungi ishlar. «Missiya» va
  foiz yo'q; Hozir'dagi vazifa ro'yxatda takrorlanmaydi.
* **💰 Pul** menyuda: balans, kirim/chiqim, kategoriyalar, so'nggi yozuvlar.
  Chatga «Tushlik 45 ming» yozilsa — chiqim sifatida saqlash taklif qilinadi.
* **Setup:** modullardan keyin 7 ta tayyor odat (hammasi belgilangan) — jami 10.
* **Marosimlar:** Get up / 5x namoz / Kundalik — nomi va darajasi
  o'zgartiriladi, o'chiriladi, jamoaga ulanadi (✅/➕ 👥 Jamoa).

## Mini App

* **Navigatsiya:** Asosiy · Odatlar · Vazifalar · Jamoa · Statistika | **Pul**
  (chiziq bilan ajratilgan).
* **Bosh sahifa:** Hozir → `Vazifa 1/3 · Odat 2/6 · Namoz 3/5` (bosilsa
  Statistika) → bugungi vazifalar. Foiz, XP kartasi, countdown olib tashlandi
  (XP → Statistika, countdown → Vazifalar → Taqvim).
* **Odatlar:** avval ro'yxat (daraja — qator chetidagi rang), keyin panel:
  Odat qo'shish · Tayyor odatlar · Tartib · Qaytarish.
* **Tayyor odatlar (10):** ✓/＋ bir bosishda; olib tashlangani tarixi bilan
  qaytadi.
* **Marosim sahifasi:** nom, daraja, eslatma, pauza, o'chirish; «Qayerda
  ko'rinadi: Shaxsiy + jamoalar».
* **Vazifalar:** «Tanlangan» bo'limi yo'q (u Hozir kartasida); hafta maqsadi
  faqat shu ekranda; Taqvim: Date countdown → oy → ⏱ Time countdown.
* **Loyiha yaratish:** Shaxsiy yoki qaysi jamoa — so'raladi.
* **Jamoa → Natija:** butun jamoa uchun **bitta chiziqli grafik** (hafta / oy).
* **Pul:** balans, kirim, chiqim, shu oy tejalgan; bir qatorli kiritish
  («Tushlik 45 ming», «Maosh 5 mln keldi»), qurilmada bo'lsa ovoz bilan;
  xarajat taqsimoti (donut), kategoriya byudjeti (bosib o'zgartiriladi,
  0 — cheklovsiz), yozuvlar (o'chirish + qaytarish), oylar bo'yicha.

## Pul — qoidalar

* Alohida jadval: `money_entries`, `money_budgets` (workspace bo'yicha).
* Hech qanday ball, streak, XP yoki hisobotga **kirmaydi**.
* Export va «ma'lumotlarni o'chirish» Pulni ham qamraydi.
* Kategoriyalar: Ovqat, Transport, Uy, Sog'liq, Ko'ngil ochar, Biznes, Boshqa;
  kirim: Maosh, Savdo, Boshqa kirim. Summa — butun so'm.

## API (yangi)

| Endpoint | Nima |
|---|---|
| `GET /api/money?month=YYYY-MM` | Oy: totals, kategoriyalar, yozuvlar |
| `POST /api/money` | Yozuv (kind, amount, category, note, day ≤ bugun) |
| `POST /api/money/text` | «Tushlik 45 ming» → yozuv |
| `DELETE /api/money/{id}` | O'chirish (javobda yozuv — undo uchun) |
| `PUT /api/money/budgets/{category}` | Oylik byudjet |
| `GET/POST /api/habits/presets` | Tayyor 10 ta; `{key, on}` |
| `GET /api/habits/archived`, `POST /api/habits/{id}/restore` | Qaytarish |
| `POST /api/rituals/share` | `{key, team_id, on}` — marosim jamoada ham |
| `DELETE /api/habits/{id}` | Endi marosimlarni ham o'chiradi (modul o'chadi, tarix qoladi) |

`/api/home` endi `overall`, `focus`, `mission`, `birthdays`, `countdowns`,
`week` ni hisoblamaydi; o'rniga `counts`. `/api/me` ga `teams` (id, nom)
qo'shildi. `/api/teams/{id}/stats` seriyasida `team` (butun jamoa) chizig'i.

## Testlar

`pytest -q` — 852 ta. Mahsulot qarori o'zgargan testlar yangilandi
(Turdim menyuda emas, Home'da foiz yo'q, marosim o'chiriladi va nomi
o'zgaradi, navigatsiyada Pul) va yangilari qo'shildi: pul parseri va API,
izolyatsiya, byudjet, tayyor odatlar, marosimni jamoaga ulash, jamoa grafigi,
so'rovlar soni, memo, ko'chirish, taqvim qoidasi, bot pul oqimi, setup.
