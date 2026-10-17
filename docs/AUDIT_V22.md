# ErnestOS v22 — 10 rolli to'liq audit

Sana: 2026-10-17. Usul: lokal server (SQLite) + haqiqiy HTTP so'rovlar, Chromium brauzer, kod o'qish.
Har bir topilma qayta takrorlangan (repro) va tuzatilgandan keyin testga aylantirilgan: `tests/test_audit_v22.py` (15 test).
Bu testlar eski kodda yiqiladi, yangisida o'tadi.

## Natija bir qarashda

| # | Rol | Nima tekshirildi | Natija |
|---|---|---|---|
| 1 | Yangi foydalanuvchi | Email bilan ro'yxatdan o'tish, kod, sozlash, chiqish | ✅ To'g'ri. Kod emailda, noto'g'ri/qayta ishlatilgan kod rad etiladi |
| 2 | Kundalik foydalanuvchi | Tez qo'shish, takrorlanish, odat, namoz, kundalik | 🔧 2 ta tuzatildi (F5, F7) |
| 3 | Moliya | Kirim-chiqim, hisoblar, o'tkazma, obuna, byudjet, qarz | 🔧 1 ta tuzatildi (F8) |
| 4 | Guruh rahbari | Taklif, a'zo huquqlari, chiqarish | 🔧 2 ta tuzatildi (F2, F3) |
| 5 | Free tarif | Barcha limitlar va ularni chetlab o'tish | 🔧 1 ta tuzatildi (F11) |
| 6 | Ovoz / AI | Draft izolyatsiyasi, tasdiqlashsiz harakat yo'qligi | ✅ Testlar bilan tasdiqlangan. Haqiqiy AI sifati kalitsiz tekshirilmadi |
| 7 | Android / oflayn | Telefon ilovasi E2E, oflayn navbat, orqaga tugmasi | ✅ 29/29 |
| 8 | Hujumchi | IDOR, auth, XSS, limit, header | 🔧 2 ta tuzatildi (F1 P0, F12) |
| 9 | Backend dasturchi | Fuzzing, akkaunt o'chirish, unumdorlik, kod | 🔧 3 ta tuzatildi (F4, ID>int64, bo'sh izoh) |
| 10 | Frontend dasturchi | XSS (real UI), har tugmani bosish, hujjat | 🔧 1 ta tuzatildi (F13) |

## Topilmalar va tuzatishlar

| ID | Daraja | Muammo | Isbot | Tuzatish |
|---|---|---|---|---|
| F1 | **P0** | Railway proksisi ortida barcha kirmagan foydalanuvchilar bitta IP hisoblanardi. Sign-in kodi limiti (10 ta / 10 daqiqa) hammaga umumiy edi | 12 xil odam kod so'radi → 11- va 12-si **429** | Mijoz IP'si proksi qo'shgan oxirgi `X-Forwarded-For` qiymatidan olinadi (`config.TRUST_PROXY`, Railway'da avtomatik yoqiladi) |
| F13 | **P1** | `ISHGA_TUSHIRISH.md` `WEBAPP_URL=…/webapp` deb o'rgatardi, `/webapp` esa 404 | `curl /webapp` → `{"detail":"Not Found"}` | Hujjat tuzatildi: server manzilining o'zi |
| F7 | P2 | Ertangi namoz va kundalikni bugun belgilash mumkin edi, bu natija va seriyani sun'iy oshiradi | `POST /api/prayers day=ertaga` → 200 | Kelajak kun → 422 `day_out_of_range`; o'tgan kunlar ochiq |
| F11 | P2 | Free: oddiy vazifani tahrirlab "har hafta" qo'yish takrorlanish limitini (3) chetlab o'tardi | 4 → 7 ta takrorlanuvchi | Tahrirlashda ham limit tekshiriladi (402) |
| F8 | P2 | Hisob o'chirilganda boshlang'ich summasi jamidan yo'qolardi | Jami 2 000 → 1 000, "taqsimlanmagan" 999 950 000 | Jami barcha hisoblarning boshlang'ich summasini hisoblaydi; o'chirilgan hisob puli to'liq "taqsimlanmagan"ga o'tadi |
| F2 | P2 | Guruhdan chiqarilgan odam o'sha kod bilan darhol qaytib kirardi | `rejoin` → `joined` | Chiqarilgan odam endi so'rov yuboradi, admin tasdiqlaydi (`team_members.removed_at`) |
| F3 | P2 | `BOT_USERNAME` bo'lmasa, taklif kodi hech qayerda ko'rinmasdi | `invite_link: null` | `invite_code` faqat taklif huquqi borlarga beriladi va UI'da ko'rsatiladi |
| F12 | P2 | Hech qanday xavfsizlik sarlavhasi yo'q edi | `curl -D -` | `nosniff`, `Referrer-Policy`, `frame-ancestors` (faqat Telegram), HTTPS'da HSTS |
| F4 | P2 | Botda shaxsiy vazifa nomi escapesiz HTML xabarga ketardi; `<` bo'lsa tasdiq kelmasdi | `app.py` "task_added" | `esc(title)` |
| F5 | P3 | Tez qo'shishda "har kuni / har hafta / har oy / ish kunlari" takrorlanish sifatida tanilmasdi | "har kuni yugurish" → oddiy vazifa | Tanildi (uz/ru/en). Server, bot va oflayn nusxada bir xil qoida |
| F14 | P3 | Haddan katta ID (`99999999999999999999`) 16 ta endpointda 500 berardi | Fuzzing | 404 `not_found` |
| F15 | P3 | Bo'sh izoh (`"   "`) → 500 | Fuzzing | 422 |
| — | UX | AI o'chiq serverda mikrofon tugmasi faqat "AI yoqilmagan" deyardi | Oflayn nusxa sinovi | AI bo'lmasa mikrofon ko'rsatilmaydi |

## Ataylab o'zgartirilmaganlar (dizayn qarori)

| ID | Holat | Nega |
|---|---|---|
| N1 | Bajarilgan vazifa/qarzni qayta ochish Free limitidan oshishi mumkin | Bu "Bekor qilish" yo'li; uni bloklash foydalanuvchiga qarshi bo'ladi |
| F9 | O'tkazma hisob balansidan oshishi mumkin | Kredit karta salbiy bo'lishi mumkin. Ogohlantirish kerakmi — sizning qaroringiz |
| F10 | Obunani API orqali bir kunda 2 marta to'lash mumkin | UI ikki marta bosishdan himoyalangan (`saveInFlight` + idempotency kalit) |

## Tekshirildi, muammo yo'q

- **IDOR:** 73 ta urinishning hammasi rad etildi (vazifa, odat, pul, qarz, maqsad, loyiha, sana, tug'ilgan kun, fokus, yaqinlar, hisob, obuna, guruh va uning ichidagilar, taymer).
- **Guruh huquqlari:** oddiy a'zo nom/rol/chiqarish/taklif/egalik o'zgartira olmaydi (403). Taklif kodi 72-bit tasodifiy, taxmin qilib bo'lmaydi.
- **Auth:** soxta imzo, boshqa user ID, 10 kunlik va kelajak initData, garbage token → 401. Sozlanmagan akkaunt yoza olmaydi (409).
- **XSS:** barcha yozuvlar `<img src=x onerror=…>` nomli bo'lgan akkaunt bilan 24 ta ekran va barcha oynalar bosib chiqildi: 0 ta bajarilish, 0 ta kiritilgan HTML. Bot xabarlarida 44 ta joy ko'rib chiqildi — escape bor (F4 bundan mustasno).
- **Email:** header injection (`\r\n`) rad etiladi; kod faqat bir marta ishlaydi.
- **Akkaunt o'chirish:** foydalanuvchining hech bir yozuvi bazada qolmadi.
- **Free limitlari:** odat, preset, loyiha, maqsad, qarz, hisob, obuna, guruh, yaqinlar, takrorlanish, faol vazifa, statistika, mavzu — hammasi 402. Promo-kod va soxta mahsulot rad etiladi.
- **Fuzzing:** 3 × 1212 tasodifiy so'rov (null, bo'sh, ulkan son, noto'g'ri tur, buzilgan JSON) — tuzatishdan keyin 0 ta 500.
- **Unumdorlik:** 300 vazifa, 22 odat, 1 000 pul yozuvi, 90 kunlik tarix bilan eng sekin ekran 208 ms (yillik statistika), Bosh sahifa 92 ms.
- **Avatar:** tur baytlar bo'yicha tekshiriladi, hajm cheklangan, server tomonda ochilmaydi.
- **Sessiya:** token SHA-256 xeshi bilan saqlanadi, 90 kun, bekor qilinadi; CORS faqat ilova manzillariga ochiq.

## Tekshirib bo'lmadi (muhit cheklovi)

- Haqiqiy AI javoblari (Groq kaliti yo'q), haqiqiy Telegram bot xabarlari va to'lov (Stars).
- Haqiqiy Android qurilma: mikrofon, push, aniq vaqtli eslatma, Google native kirish.
- Production server (bu muhitdan Railway'ga tarmoq yopiq).

## Test natijalari (yakuniy kod)

| To'plam | Natija |
|---|---|
| Python (pytest) | 1166 / 1166 ✅ (shundan 15 tasi shu audit uchun yangi) |
| Lint (pyflakes) | toza ✅ |
| Frontend tekshiruvlari (3 to'plam) | ✅ |
| Brauzer E2E, real server | 11 / 11 ✅ |
| Telefon ilovasi E2E | 29 / 29 ✅ |
| Oflayn nusxa E2E | ✅ |
| XSS crawler (24 ekran, barcha oynalar) | 0 ta topilma ✅ |
| Fuzzing (3 × 1212 so'rov) | 0 ta 500 ✅ |
| IDOR (73 urinish) | 0 ta oqish ✅ |

## Ishga tushgandan keyin bitta tekshiruv

F1 tuzatishi Railway proksisi `X-Forwarded-For`ni qanday yozishiga bog'liq (bu muhitdan tekshirib bo'lmadi).
Deploydan keyin Railway logida `rate limit hit: key=…` qatorlari turli `key` bilan chiqishi kerak. Hammasi bir xil `key` bo'lsa, `TRUST_PROXY=0` qo'yib menga yozing.
