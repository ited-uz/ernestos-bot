# ErnestOS — global standartlarga muvofiqlik va 5 ta USP

Holat: v21. Har bir qator kod yoki test bilan tekshirilgan. «Sizdan» deb yozilgan qatorlar sozlama yoki hisob talab qiladi; ularni kod bilan bajarib bo'lmaydi.

---

## 1. Muvofiqlik jadvali

### WCAG 2.2 AA (Accessibility — imkoniyati cheklanganlar uchun)

| Talab | Holat | Isbot |
|---|---|---|
| 1.4.3 Matn kontrasti ≥ 4.5:1 | ✅ | axe-core: 14 ekran + kirish sahifasi, yorug' va tungi — **0 xato** (oldin 45+ element). Asosiy ko'k `#007AFF` (4.02) → `#0070E0` (4.78); kulrang matn, yashil va to'q sariq ranglar ham to'g'rilandi |
| 4.1.2 Nom, rol, qiymat | ✅ | Ichma-ich tugma yo'q (Qadam, Yaqinlarim); `aria-checked` faqat checkbox'da; progress-bar va grafik nomlangan |
| 1.1.1 Matnli muqobil | ✅ | Statistika grafigida `<title>` va oxirgi qiymat |
| 1.4.4 Matnni kattalashtirish | ✅ | Telefon shrifti 85–115% qo'llanadi, 360px ekranda so'z kesilmaydi |
| 2.5.8 Bosish maydoni ≥ 24px | ✅ | Belgilash doiralari 46px (ko'rinmas kengaytma bilan), tugmalar ≥ 40px |
| 3.1.1 Sahifa tili | ✅ | `<html lang>` tanlangan tilga qarab (uz/ru/en) |
| Harakatni kamaytirish | ✅ | `prefers-reduced-motion` — animatsiyalar o'chadi |

### Google Play talablari

| Talab | Holat | Isbot |
|---|---|---|
| Target API ≥ 35 | ✅ | `targetSdkVersion = 36` (Android 16) |
| Play uchun AAB | ✅ | Imzo kaliti bo'lsa, CI `bundleRelease` → `ErnestOS-NN.aab` |
| Release imzo, debug emas | ✅ | Kalit bo'lsa `assembleRelease` |
| Ilova ichida akkauntni o'chirish | ✅ | Profil → ⚙️ → Maxfiylik va ma'lumotlar |
| **Veb orqali akkauntni o'chirish** | ✅ yangi | `/account/delete`: email → kod → o'chirish. Test bilan tekshirilgan |
| **Ommaviy maxfiylik siyosati** | ✅ yangi | `/privacy` (uz/ru/en): nima yig'iladi, kimga beriladi, qancha saqlanadi, huquqlar |
| Data safety formasi | 📋 tayyor | Javoblar quyida (3-bo'lim); Play Console'ga siz kiritasiz |
| Exact alarm siyosati | ✅ | Ruxsat foydalanuvchi bosgandagina so'raladi; ruxsatsiz ham ishlaydi |
| Bildirishnoma ruxsati (Android 13+) | ✅ | Birinchi ochilishda bir marta so'raladi |
| Predictive back (Android 14+) | ✅ | `OnBackPressedDispatcher` orqali; Orqaga tugmasi bir daraja orqaga qaytaradi |
| Themed icon (Android 13+) | ✅ yangi | Adaptive icon'ga `monochrome` qatlami qo'shildi |
| Developer akkaunt, listing, skrinshotlar | 🔸 sizdan | Listing matni tayyor: `docs/STORE_LISTING.md` |

### OWASP MASVS (mobil xavfsizlik)

| Talab | Holat | Isbot |
|---|---|---|
| NETWORK: faqat HTTPS | ✅ yangi | `network_security_config`: cleartext taqiqlangan, faqat tizim CA |
| STORAGE: zaxiraga sir chiqmasin | ✅ | `allowBackup=false` + data extraction rules |
| RESILIENCE: debuggable emas | ✅ | Release APK |
| AUTH: kodlar va sessiyalar | ✅ | Bazada faqat xesh; 10 daqiqa; 5 urinish; rate limit |
| AUTH: OAuth | ✅ | OIDC token imzosi (JWKS), nonce, PKCE-uslubidagi verifier |
| PRIVACY: loglarda sir yo'q | ✅ | Xato hisobotidan token/email/initData kesiladi (test bor) |
| STORAGE: sessiya Keystore'da | 🔸 keyingi bosqich | Hozir WebView storage'da (zaxiradan himoyalangan). Keyingi qadam — Android Keystore plagini |

### GDPR / shaxsiy ma'lumotlar

| Huquq | Holat |
|---|---|
| Ma'lumotni olish (Art. 15/20) | ✅ Eksport JSON |
| Tuzatish (Art. 16) | ✅ Har bir yozuv tahrirlanadi |
| O'chirish (Art. 17) | ✅ Ilovada, botda, vebda |
| Rozilik va uni qaytarib olish (Art. 7) | ✅ AI uchun alohida rozilik, Sozlamalarda o'chiriladi |
| Uchinchi tomonlar ro'yxati (Art. 13) | ✅ Maxfiylik siyosatida |
| Ma'lumotlarni minimallashtirish | ✅ Ovoz saqlanmaydi; xato hisobotida shaxsiy ma'lumot yo'q |

---

## 2. Top 5 USP (Unique Selling Points)

USP — raqobatchida yo'q, mijoz uchun pul to'lashga arziydigan farq. Har biri ilovadagi ishlaydigan funksiyaga asoslangan. Kirish sahifasida va Play listing'da ko'rsatiladi.

### 1. 🎙 O'zbekcha gapiring — u bajaradi
**Nima:** «Ertaga 10 da Aziz bilan uchrashuv», «Tushlik 45 ming», «Akmal qarzini berdi», «Meetingga borib keldim». Sheva va rus-o'zbek aralash nutqni tushunadi. Yangi narsa yaratadi va borini yopadi: qarzni qaytdi deb belgilaydi, vazifani bajarildi qiladi.
**Nega noyob:** Todoist, TickTick, Notion o'zbek tilini, ayniqsa shevani tanimaydi. Ularning hech biri «qarzini berdi» degan gapdan qarzni yopa olmaydi.
**Ishonch:** Har bir buyruq avval karta bo'lib chiqadi. Siz «Tasdiqlash»ni bosmaguningizcha hech narsa o'zgarmaydi.

### 2. 🧭 Butun hayot — bitta ilovada
**Nima:** Vazifa, odat, namoz, moliya, qarz, maqsad va yaqinlar — bitta «Bugun» ekrani va bitta statistika.
**Nega noyob:** Odatda buning uchun 5–6 ta ilova kerak (Todoist + Habitica + Money Lover + qarz daftari + namoz ilovasi). Ular bir-birini ko'rmaydi. ErnestOS'da kun natijasi hammasini birga hisoblaydi.
**Qiymat:** Bitta odat, bitta joy. Ilova almashtirishga vaqt va diqqat ketmaydi.

### 3. 🕌 Musulmon kun tartibiga mos
**Nima:** 5 vaqt namoz (jamoat bilan, vaqtida, qazo) kunlik natijaga va qadamlarga kiradi. «Islom va xayriya» maqsadlarda alohida yo'nalish.
**Nega noyob:** Namoz ilovalari faqat namozni, rejalashtiruvchilar faqat ishni ko'radi. ErnestOS ikkalasini bitta kunga birlashtiradi.
**Bozor:** O'zbekiston, Markaziy Osiyo va butun dunyodagi ~1.9 mlrd musulmon. Global planerlarda bu segment uchun mahsulot yo'q.

### 4. 🎯 «Hozir» — bitta ish, ro'yxat emas
**Nima:** Ilovani ochganda 40 ta vazifalik ro'yxat emas, aynan hozir qilinadigan bitta ish turadi. Pro/Max'da uni AI tanlaydi va sababini bir gapda aytadi.
**Nega noyob:** Raqobatchilar ro'yxat beradi, tanlashni sizga qoldiradi. Ro'yxat qancha uzun bo'lsa, boshlash shuncha qiyin. «Hozir» qaror charchog'ini olib tashlaydi.

### 5. 👥 Telegram'da ham, telefonda ham, jamoa bilan
**Nima:** Telegram bot va Mini App, Android ilova — bitta akkaunt. Oila yoki jamoaga topshiriq beriladi, kim nima qilgani ko'rinadi. Telegram'siz a'zo kod bilan qo'shiladi.
**Nega noyob:** O'zbekistonda odamlar allaqachon Telegram'da. Yangi ilova o'rnatmasdan boshlash mumkin, keyin xohlasa ilovaga o'tadi. To'lov Telegram Stars orqali — karta shart emas.

---

## 3. Play Console → Data safety (javoblar)

| Savol | Javob |
|---|---|
| Ma'lumot yig'iladimi? | Ha |
| Uchinchi tomonga **ulashiladimi** (sotish/reklama)? | Yo'q |
| Uzatishda shifrlanadimi? | Ha (HTTPS) |
| Foydalanuvchi o'chirishni so'ray oladimi? | Ha — ilovada va `/account/delete` |
| **Shaxsiy ma'lumot:** ism, email, foydalanuvchi ID | Yig'iladi · ilova ishlashi, akkaunt boshqaruvi · majburiy |
| **Moliya:** foydalanuvchi kiritgan kirim-chiqim va qarzlar | Yig'iladi · ilova ishlashi · ixtiyoriy |
| **Ovoz** | Yig'iladi · ilova ishlashi (matnga aylantirish) · ixtiyoriy · saqlanmaydi |
| **Kontaktlar** (Yaqinlarim — qo'lda kiritilgan) | Yig'iladi · ilova ishlashi · ixtiyoriy |
| **Ilova faoliyati:** foydalanuvchi kontenti (vazifa, odat, namoz, kun xulosasi) | Yig'iladi · ilova ishlashi · majburiy |
| **Ilova ma'lumoti va unumdorligi:** xato hisobotlari | Yig'iladi · analitika (xatolarni tuzatish) · shaxsiy ma'lumotsiz |
| **Qurilma ID:** push token | Yig'iladi · ilova ishlashi (bildirishnoma) |
| Joylashuv, rasm galereyasi, SMS, qo'ng'iroqlar | Yig'ilmaydi |
