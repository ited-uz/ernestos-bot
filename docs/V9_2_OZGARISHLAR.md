# ErnestOS v9.2 — menyu 2×3, jamoa formulada, Pul soddalashdi

v9.1 ustiga. Hamma hisob-kitob hamon bitta `services.py` da.

## Formula (SCORE_FORMULA = 3)

| Qism | Og'irlik | Nima |
|---|---|---|
| Vazifalar | 40% | Faqat shaxsiy vazifalar |
| Odatlar | 25% | Faqat shaxsiy odatlar |
| **Jamoa** | **20%** | Bugun sizdan kutilgan jamoa ishlari (vazifa + odat): bajarilgan / jami |
| Namoz | 15% | O'zgarmagan |

* Oldingi 20% — hafta maqsadi (fokus) — endi **jamoa natijasi**ga berildi.
* Jamoa ishi bo'lmagan kun (yoki jamoa yo'q) — jamoa `None`, uning 20% i
  qolgan qismlarga **proporsional taqsimlanadi**.
* Hafta maqsadi Vazifalar ekranida qoladi; kunlik ballga kirmaydi.
* `daily_scores.team_score` ustuni qo'shildi (migratsiya avtomatik).

## Bot

* **Menyu — 2 ustun, 3 qator:** 🏠 Home | 💰 Pul · ✅ Odatlar | ⚡ Vazifalar ·
  👥 Jamoa | ⚙️ Sozlamalar. Pastdagi «ErnestOS» (Mini App) tugmasi olib
  tashlandi — ilova chat maydoni yonidagi menyu tugmasidan (ErnestOS) ochiladi.
  Menyu endi foydalanishga qarab «o'smaydi»: birinchi kundan 6 ta tugma.
* **Taklif kiritish** — Sozlamalar ichida.
* **Statistika** — Home ichidagi 📊 tugmasi orqali.
* **Odatlar:** 2 ustunli tugmalar; ♻️ Qaytarish olib tashlandi.
* **Pul:** 🐷 «Tejaldi» qatori yo'q. Chatga «Tushlik 45 ming» yozilsa —
  summa ko'rsatiladi va ikkita tugma: ➖ Chiqim · ➕ Kirim. Qaysi bosilsa,
  o'sha saqlanadi (taxmin qaror qilmaydi).

## Mini App

* **Odatlar:** 2 ustunli katak (keng ekranda 3). Qaytarish tugmasi, oynasi va
  `GET /api/habits/archived`, `POST /api/habits/{id}/restore` olib tashlandi.
  Tayyor 10 ta odat o'chirilsa — «Tayyor odatlar»dan tarixi bilan qaytadi.
* **Pul:** «Shu oy tejaldi» kartasi yo'q. Bir qatorli kiritish ostida
  **− Chiqim / + Kirim** tugmalari; Enter — so'zlardan o'zi aniqlaydi.
* **Jamoa → Natija:** har a'zo o'z rangidagi chiziq, **ko'k qalin chiziq —
  o'rtacha**; ostida bitta qatorda ikki ustun: Hafta | Oy.
* **Statistika:** «Fokus» o'rniga «Jamoa».

## Pul parseri

* `1 mln 200 ming` → 1 200 000 (kamayib boruvchi birliklar qo'shiladi).
* `Tushlik 45 ming, taksi 20 ming` → 45 000 (birinchisi).
* `Oylik ijara 3 mln` → chiqim (Uy). `Oylik keldi 5 mln` → kirim (Maosh).
* `POST /api/money/text` endi `kind` qabul qiladi (tugma tanlovi ustun).
* `money_overview` javobidan `saved` olib tashlandi.

## Testlar

`pytest -q` — 860 ta, hammasi o'tadi.
