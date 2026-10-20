# Web ilova — kompyuter va telefon brauzerida

Manzil: **`<server>/app/`** (lokal: <http://localhost:8000/app/>). Server
manzili (`/`) oddiy brauzerda ochilsa, o‘zi `/app/`ga o‘tadi; Telegram ichida
esa Mini App avvalgidek ochiladi.

Bu alohida ilova emas: Telegram Mini App, Android ilova va web — **bitta
interfeys** (`webapp/index.html`), **bitta server**, **bitta akkaunt**.
Bir joyda qilingan o‘zgarish hamma joyda ko‘rinadi.

## Ko‘rinish

| Ekran | Ko‘rinish |
|---|---|
| Telefon (360–999 px) | Pastki panel, oynalar pastdan chiqadi |
| Kompyuter, planshet yonboshlab (≥1000 px) | Kulrang fon ustida oq kartalar; chapda yon panel: yuqorida **«+ Yangi»** (shu ekranda nima qo‘shilsa — vazifa, qayd, pul yozuvi, maqsad), pastda **Xabarlar** va profil kartasi. **Bugun** — ikki ustun: «Hozir» va keyingi ishlar chapda, bugungi progress va odatlar (shu yerda belgilanadi) o‘ngda; xiralik yo‘q. Ro‘yxatlar o‘qishga qulay kenglikda, tugmalar matni kengligida. Oynalar o‘rtada dialog bo‘lib ochiladi, **Esc** yopadi |
| Kirish sahifasi (≥960 px) | Ikki yarim: chapda qorong‘i brend panel va 5 ta afzallik, o‘ngda faqat kirish formasi |
| Qaydlar (≥1000 px) | Yon ro‘yxat + muharrir yonida bog‘lanishlar paneli |

Yorug‘ va qorong‘i rejim tizimga ergashadi (yoki Sozlamalar → Ko‘rinish).
Brauzerning **Orqaga** tugmasi (va Android’dagi orqaga ishorasi) avval ochiq
oynani yopadi, keyin bitta ekran orqaga qaytadi.

Tekshirilgan kengliklar: 360, 390, 768, 1024, 1366, 1440, 1920 px — yon
skroll yo‘q, «undefined» yo‘q, JavaScript xatosi yo‘q
(`scripts/e2e_web.sh`, 18 ta tekshiruv).

## Kirish

- **Email + 6 xonali kod** (parolsiz; yangi email — yangi akkaunt).
- **Google / Apple** — serverda kalitlar sozlansa. Kirish o‘sha tabning
  o‘zida boshlanadi va tugaydi.
- **Telegram bot kodi** — botda boshlaganlar uchun (bot bor serverda).

## O‘rnatish (PWA)

https manzilda (yoki `localhost`da):

- **Kompyuter (Chrome/Edge):** manzil qatoridagi «O‘rnatish» belgisi → ilova
  alohida oynada, dock/menyuda belgisi bilan.
- **Android (Chrome):** ⋮ → «Bosh ekranga qo‘shish / O‘rnatish».
- **iPhone (Safari):** Ulashish → «Bosh ekranga».

Service worker faqat ilova fayllarini saqlaydi: internet yo‘q paytda ham
ochiladi, oxirgi ko‘rilgan holat ko‘rinadi, yozilganlar qurilmada kutib,
internet qaytganda yuboriladi. Ma’lumotlar (`/api`) hech qachon keshdan
berilmaydi.

## Xavfsizlik

- Sessiya — **HttpOnly cookie** (`__Host-eos` https’da): sahifadagi hech
  bir skript uni o‘qiy olmaydi; localStorage’da token yo‘q.
- Cookie `SameSite=Strict`, va server uni faqat sahifa yuborgan maxsus
  sarlavha (`X-Telegram-Init-Data: web`) bilan qabul qiladi — boshqa sayt
  forma yoki havola orqali sizning nomingizdan hech narsa qila olmaydi.
- Google/Apple kirishini faqat uni boshlagan brauzer yakunlay oladi:
  kimdir sizga havola yuborsa, u na sizning akkauntingizga, na siz uning
  akkauntiga kirib qolmaydi.
- Sahifani boshqa sayt ichiga (iframe) joylab bo‘lmaydi.
- Chiqish (Profil → Sozlamalar → Chiqish) sessiyani serverda ham tugatadi.

Testlar: `tests/test_web.py` (17 ta), brauzer: `scripts/e2e_web.sh`.

## Hozircha yo‘q

- **Brauzer bildirishnomasi (Web Push).** Eslatmalar ilova ichidagi
  **Xabarlar**ga va (bor bo‘lsa) Telegram botga keladi. Web Push uchun VAPID
  kalitlari va https domen kerak — hosting bosqichida qo‘shiladi.
- Oddiy `http` orqali (masalan telefondan `http://192.168…`) ovoz va o‘rnatish
  ishlamaydi — brauzer buni faqat https yoki localhost’da beradi.
