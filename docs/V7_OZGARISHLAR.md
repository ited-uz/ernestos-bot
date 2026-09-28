# ErnestOS v7 — o'zgarishlar

## O'rnatish (muhim)

1. Zipni **yangi papkaga** oching. Eski papka ustiga ochmang: eski fayllar
   (va `.git`) aralashib ketadi.
2. `.env` ni eski papkadan ko'chiring. Webhook ishlatsangiz, `WEBHOOK_SECRET`
   endi **majburiy**: kamida 32 belgi. Usiz production ishga tushmaydi.
   ```
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
3. Deploy qiling. Yangi ustunlar va jadvallar ishga tushganda o'zi qo'shiladi.
4. Bir marta migratsiyani yurgizing (o'tgan kunlarni yopib, tarixni qotiradi):
   ```
   python migrations.py 0012
   ```

## Bot

- **Home** siz bergan formatda: sana, Missiya, Bugun, ✅ odatlar, 🕌 namoz,
  🔥 streak, 📊 umumiy. "…ning shaxsiy tizimi" sarlavhasi olib tashlandi.
  Pastida ikki tugma bor: **📅 Date countdown** va **⏱ Time countdown**.
- **Vazifa yoki odat qo'shish**: jamoa bo'lsa, nomdan keyin
  "👤 Shaxsiy / 👥 Miro*" deb so'raydi.
- **Tahrirlash** (shaxsiy va jamoa uchun): nomi, muddati, muhimligi, joyini
  ko'chirish, pauza (bugundan yoki ertadan), taymer, o'chirish. Jamoa ishini
  uni yaratgan kishi, admin yoki egasi o'zgartiradi.
- **Vazifalar ro'yxati**: ❗ Kechikkan → ⚡ Bugun → 📅 Keyingi kunlar →
  📥 Muddatsiz → 👥 Jamoa vazifalari (jamoa nomi bilan).
- **Tez yozib qo'yish**: istalgan matn (masalan, "ertaga 15:00 doktorga
  qo'ng'iroq") bitta tugma bilan vazifa bo'ladi. Sana va vaqt matndan
  olinadi. Salomlashish vazifa bo'lmaydi.
- **Menyu tugmasi** oqim o'rtasida bosilsa, vazifa nomi bo'lib qolmaydi.
- **Date countdown**: umumiy, vazifa uchun yoki odat uchun; shaxsiy yoki
  jamoaniki. Vazifaga bog'lansa, uning muddati bir tugma bilan tanlanadi.
- **Time countdown**: shaxsiy va jamoa odat/vazifalari. Jamoa ishida har kim
  o'z taymerini yuritadi.
- **Jamoa**: taklif havolasi avval jamoani ko'rsatadi, keyin "Qo'shilish".
  Havola 3 kun ishlaydi, uni yangilash yoki bekor qilish mumkin. So'rov
  bilan qo'shish (tasdiq) ham bor. Rollar: 👑 egasi, ⭐ admin, 👤 a'zo.
  Egalik faqat qabul qilinganda o'tadi. Har kim o'zi uchun bildirishnoma
  darajasini tanlaydi (Hammasi / Muhim / Faqat menga / O'chiq). Xabarlar
  har a'zoga o'z tilida boradi.
- **Statistika** jamoa natijalarini ham ko'rsatadi.
- **Sozlash**: nimani kuzatish (erta turish, namoz, kundalik, jamoa) o'zingiz
  tanlaysiz. Keyin Sozlamalar → 🧩 Modullar'dan o'zgartirasiz.

## Mini App

- Jamoa ekrani **Bugun / Ishlar / Natija** bo'limlariga bo'lingan. ⋯
  menyusida havola, so'rovlar, bildirishnoma, a'zolar va rollar, qayta
  nomlash, chiqish bor. Faollik tarixidan o'chirilganni qaytarish mumkin.
- Vazifalar ekrani **Bugun / Reja / Taqvim**. Reja ichida Ochiq · Loyihalar ·
  Bajarilgan chiplari bor.
- Date va Time countdown alohida oynalarda; vazifa, odat va jamoa uchun.
- Jamoa vazifasida "Kim bajaradi" tanlanadi: hamma / bittasi yetarli /
  tayinlanganlar.
- Home'da bugungi jamoa vazifalari ham chiqadi.
- Modullar sozlamasi; o'chirilgan modulning tabi yashiriladi.
- Uyg'onish vaqtini keyin qo'lda kiritish mumkin (⏰).
- Pauza: "bugundan" yoki "ertadan".
- Tuzatilgan xatolar:
  - jurnal avtosaqlashi (bir necha maydon, tartib, ikki foydalanuvchi bitta
    telefonda);
  - jurnal hisoblagichi yozish bilan birga yangilanadi;
  - ikki marta bosish yoki qayta yuborish ikkinchi yozuv yaratmaydi;
  - kalendar eskirib qolmaydi;
  - sozlash tugamagan akkauntda spinner osilib qolmaydi;
  - jamoa odatini tahrirlash endi to'g'ri joyga yoziladi.
- Kanalga obuna tugagan akkaunt ma'lumotlarini ko'ra oladi va eksport qila
  oladi; faqat yangi yozishlar to'xtaydi.
- Klaviatura va ekran o'qituvchi uchun katakchalar (role, aria, Enter/Space).
- "+" tugmasi (FAB) ekranga qarab ishlaydi.

## O'lchov (audit bandlari)

- Kun natijasi bitta formula bilan hisoblanadi. Jamoa ishlari o'sha formula
  ichida o'z og'irligi bilan sanaladi (50/50 yo'q).
- Namoz va jurnal ikki marta sanalmaydi. Jamoa "marosimlari" har a'zoning o'z
  odatidan o'qiladi.
- Jurnalda bitta javob = yozilgan kun; beshtasi = to'liq.
- O'lchanmagan kun "0%" emas, "—" bo'lib chiqadi. O'zgarishlar punktda
  ko'rsatiladi.
- Har o'tgan kun yopilib saqlanadi: jadval yoki pauzani o'zgartirish tarixni
  qayta yozmaydi. Jamoadan chiqish yoki arxivlash ham jamoa tarixini
  o'zgartirmaydi.
- Jamoa: "5 ta ish · 10 ta tasdiq · 9 tasi qolgan". Birov bajarmagan odatga
  yashil belgi qo'yilmaydi.

## API va xavfsizlik

- Webhook maxfiy kaliti majburiy, doimiy vaqtli taqqoslash va hajm chegarasi
  bilan tekshiriladi.
- `X-Idempotency-Key` bilan qayta yuborilgan yaratish so'rovi birinchi javobni
  qaytaradi.
- Ruxsat: a'zo bo'lmaganga 404, ruxsati yo'qqa 403.
- Har 10 daqiqada kunlarni yopadigan job ishlaydi (eski kalitlarni ham
  tozalaydi).
- Testlar: **779 ta, hammasi o'tadi**.
