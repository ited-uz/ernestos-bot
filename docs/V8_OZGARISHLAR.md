# ErnestOS v8 — o'zgarishlar

## O'rnatish

Deploy qiling — yangi jadvallar (`credentials`, `linked_telegrams`) va
`projects.created_by` ustuni ishga tushganda o'zi qo'shiladi. Migratsiya shart emas.

## 1. Login va parol

- Har bir akkauntga **login va parol** beriladi: yangi foydalanuvchiga
  sozlashning 2-qadamida, eski foydalanuvchiga keyingi `/start` da.
- Parol faqat bir marta ko'rsatiladi (spoiler ichida), «✅ Saqladim» bosilsa xabar o'chadi.
  Bazada faqat PBKDF2 hash saqlanadi.
- **Boshqa Telegramdan kirish:** `/login` (yoki sozlashda «🔐 Login bilan kirish») →
  login → parol. Parol yozilgan xabar avtomatik o'chiriladi. Shundan keyin o'sha
  Telegram — bot ham, Mini App ham — xuddi shu akkauntni ko'radi.
- Eslatmalar, hisobotlar va jamoa xabarlari akkauntga ulangan **hamma** Telegramlarga boradi.
- ⚙️ Sozlamalar → 🔐 Akkaunt: loginni o'zgartirish, parolni o'zgartirish,
  yangi parol yaratish, ulangan Telegramlar ro'yxati (❌ bilan chiqarish),
  boshqa akkauntga kirish, `/logout` — chiqish.
- Parol o'zgarsa, boshqa Telegramlar avtomatik chiqariladi (o'zgartirayotgani qoladi).
- 5 ta noto'g'ri paroldan keyin login 15 daqiqaga yopiladi.

## 2–4. Odatlar va vazifalar

- **Har bir odatni** o'chirish mumkin — avtomatik odatlar (Get up, 5x namoz,
  Kundalik) ham: o'chirilsa, tegishli modul o'chadi, tarix saqlanadi.
- **♻️ Qaytarish** — o'chirilgan odat va vazifalar (30 kun ichida) tarixi bilan qaytadi.
- **Tahrirlash endi 💾 Saqlash / ✖️ Bekor qilish bilan.** Nomi, muddati, vaqti,
  eslatmasi, muhimligi, toifasi — avval qoralama, «Saqlash» bosilgandagina yoziladi.
- **Eslatma** — vazifa uchun: vaqtida / 10 / 30 daq / 1 soat / 1 kun oldin / o'chiq.
  Odat uchun: har kuni soat nechada (yoki o'chiq).
- **Taymer** — ✏️ → ⏱ orqali qo'yish, o'zgartirish, o'chirish.
- O'chirish har doim tasdiq bilan.
- «➕ Qo'shish» bosilganda **10 ta odat / 10 ta vazifa** tavsiyasi kichik tugmalarda
  chiqadi — bosish yozish bilan bir xil.

## 3. Yangi foydalanuvchi uchun kam narsa

- Sozlash: til → akkaunt → ism → nimani kuzatish → tayyor (4 bosish, 1 javob).
- Menyu asta-sekin ochiladi:
  - boshida: Home, Odatlar, Vazifalar, Sozlamalar;
  - 5 ta amaldan keyin: + 📊 Statistika;
  - 15 ta amal yoki 7 kundan keyin: + 👥 Jamoa, 💬 Taklif.
  Har safar «🆕 Menyuga qo'shildi: …» deb aytiladi.
- Boshida Home'da faqat «➕ Odat» va «➕ Vazifa»; taymer va countdown keyinroq.
- Matnlar qisqartirildi (uz / en / ru).

## 5. Loyihalar — shaxsiy va jamoa

- Vazifalar → **📁 Loyihalar**: shaxsiy va har bir jamoaning loyihalari bitta ekranda.
- Yaratishda «Kimniki?» — 👤 Shaxsiy yoki 👥 jamoa.
- Loyiha ichida: vazifa qo'shish, ✅ tugatish / ♻️ qayta ochish, nomi, izohi,
  muddati, 🗑 o'chirish (vazifalar qoladi).
- Jamoa loyihasini yaratgan kishi, admin yoki ega o'zgartiradi/o'chiradi;
  vazifani har bir a'zo qo'sha oladi.
- Jamoa ekranida «📁 Loyihalar» va egasi uchun «🗑 Jamoani o'chirish».
- Mini App: jamoa loyihasini qayta nomlash va o'chirish. API:
  `PATCH/DELETE /api/teams/projects/{id}`.

## Buyruqlar

`/login` · `/logout` · `/account` · `/projects` — BotFather'dagi buyruqlar
ro'yxatiga ham qo'shib qo'ying.

## Testlar

811 ta, hammasi o'tadi.
