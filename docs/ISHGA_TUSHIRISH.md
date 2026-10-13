# ErnestOS — ishga tushirish yo'riqnomasi (server, email, APK)

Ilova to'liq ishlashi uchun uchta narsa kerak (Google/Apple bilan kirish va doimiy imzo kaliti — 3 va 4-bo'limlar):

1. **Server** — Railway'dagi bot va API, shu branchdagi yangi kodda.
2. **Email xizmati** — ro'yxatdan o'tish kodlarini yuboradi (Resend yoki Brevo).
3. **APK** — GitHub Actions yig'gan ilova, serverga ulanadi.

Server eski kodda qolsa, yangi bo'limlar ishlamaydi:
- email bilan kirish;
- AI «Hozir»;
- Yaqinlarim;
- guruhlar ro'yxati.

---

## 1. Kodni serverga chiqarish

1. Branchni `main` ga birlashtiring. Ikki yo'l bor:
   - GitHub'da `claude/brave-mayer-78dw7h` → `main` Pull Request'ini oching va «Merge» bosing;
   - yoki menga «merge qil» deng.
2. Railway `main` ga push bo'lganda o'zi deploy qiladi. Railway → Deployments'da «Success» chiqishini kuting.

Qo'lda buyruq kerak emas. Server ishga tushganda o'zi:
- yangi jadval va ustunlarni qo'shadi: `account_emails`, `email_codes`, `close_people`, `applied_migrations` va boshqalar;
- `0014` va `0015` migratsiyalarini **bir marta** bajaradi va buni bazaga yozib qo'yadi:
  - `0014` — eski mavzu nomlari;
  - `0015` — maqsadlarda «Xayriya» «Islom va xayriya»ga, `career` «Biznes»ga o'tadi.

## 2. Email xizmatini ulash (ro'yxatdan o'tish uchun shart)

Ilova emailga 6 xonali kod yuboradi. Server buning uchun email xizmatining kalitini bilishi kerak. Kalitsiz ilova «Email orqali kirish hali yoqilmagan» deb aytadi va Telegram orqali kirishni taklif qiladi.

### A variant — Resend (tavsiya; o'z domeningiz bo'lsa)

1. https://resend.com da ro'yxatdan o'ting.
2. **Domains → Add domain**: domeningizni qo'shing (masalan `ernestos.uz`).
3. Resend ko'rsatgan DNS yozuvlarini (SPF, DKIM) domen sotib olingan joyga kiriting. «Verified» bo'lishini kuting.
4. **API Keys → Create** — kalitni nusxalang.
5. Railway → Variables'ga qo'shing:
   - `RESEND_API_KEY` = kalit
   - `EMAIL_FROM` = `ErnestOS <no-reply@ernestos.uz>`

Bepul: oyiga 3 000 ta xat (kuniga 100). Domen bilan xatlar Spam'ga tushmaydi.

### B variant — Brevo (domensiz, tez boshlash uchun)

1. https://brevo.com da ro'yxatdan o'ting.
2. **Senders, Domains & Dedicated IPs → Senders → Add a sender**: xat qaysi manzildan ketishini yozing va kelgan xat orqali tasdiqlang.
3. **SMTP & API → API Keys → Generate** — kalitni nusxalang.
4. Railway → Variables:
   - `BREVO_API_KEY` = kalit
   - `EMAIL_FROM` = `ErnestOS <tasdiqlangan@manzil>`

Bepul: kuniga 300 ta xat.

Diqqat: jo'natuvchi `@gmail.com` bo'lsa, Gmail ba'zi xatlarni Spam'ga tushirishi mumkin. Real foydalanuvchilar uchun o'z domeningiz (A variant yoki Brevo'da domen) yaxshiroq.

### C variant — oddiy SMTP

Kerakli o'zgaruvchilar: `SMTP_HOST`, `SMTP_PORT` (587 yoki 465), `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`.

Railway'ning ba'zi tariflarida chiquvchi SMTP portlari yopiq bo'lishi mumkin. Shuning uchun A yoki B variant (HTTP API) ishonchliroq.

### Tekshirish

Deploy'dan keyin brauzerda `https://<server>/api/app/config` ni oching. Javobda `"email": true` bo'lishi kerak.

## 3. Google va Apple bilan kirish (ixtiyoriy)

Kirish sahifasida, email ostida, «Google orqali kirish» va «Apple orqali kirish» tugmalari bor. Ular serverda sozlangandagina ko'rinadi. Sozlanmagan bo'lsa yashirin turadi — email bilan kirish baribir ishlaydi.

### Google (bepul, ~15 daqiqa)

Avval **4-bo'limdagi imzo kalitini** yarating — Android uchun uning SHA-1 kerak.

1. https://console.cloud.google.com → yangi loyiha (masalan «ErnestOS»).
2. **APIs & Services → OAuth consent screen**:
   - User type: **External**;
   - ilova nomi, email;
   - **Publish app** (aks holda faqat test foydalanuvchilar kira oladi).
3. **Credentials → Create credentials → OAuth client ID → Web application**:
   - Authorized redirect URI: `https://<server>/api/auth/oauth/google/callback`;
   - **Client ID** va **Client secret** ni nusxalang.
4. Yana **Create credentials → OAuth client ID → Android**:
   - Package name: `uz.ernestos.app`;
   - SHA-1: imzo kalitingiznikini yozing (`make_android_key.py` chiqargan yoki GitHub Actions → android → Summary'dagi).
   - Bu clientning ID'si hech qayerga yozilmaydi. Google telefondagi ilovani shu orqali taniydi.
5. Railway → Variables:
   - `GOOGLE_CLIENT_ID` = Web client ID (`…apps.googleusercontent.com`)
   - `GOOGLE_CLIENT_SECRET` = Web client secret
   - `PUBLIC_URL` = `https://<server>` (oxirida `/` yo'q)

Natija:
- **Telefonda** «Google orqali kirish» bosilganda Android'ning o'z oynasi chiqadi, telefondagi Google akkauntlar ro'yxati bilan. Bitta bosish — kirdingiz.
- Agar bu oyna ishlamasa (SHA-1 noto'g'ri yoki eski Play xizmatlari), ilova Google sahifasini brauzerda ochadi. Kirgach, o'zi ilovaga qaytadi.

### Apple (Apple Developer akkaunti kerak — yiliga $99)

Android'da Apple bilan kirish faqat brauzer orqali bo'ladi (Apple Android uchun boshqa yo'l bermaydi).

1. https://developer.apple.com/account → **Certificates, Identifiers & Profiles**.
2. **Identifiers → App ID** yarating va unda **Sign in with Apple** ni yoqing.
3. **Identifiers → Services ID** yarating (masalan `uz.ernestos.signin`) → **Sign in with Apple → Configure**:
   - Domain: serveringiz domeni (`ernestos-bot-production.up.railway.app`);
   - Return URL: `https://<server>/api/auth/oauth/apple/callback`.
4. Railway → Variables:
   - `APPLE_CLIENT_ID` = Services ID (`uz.ernestos.signin`)
   - `PUBLIC_URL` = `https://<server>`

Apple kaliti (.p8) kerak emas: server Apple qaytargan `id_token` imzosini Apple'ning ochiq kalitlari bilan tekshiradi.

### Bir odam — bitta akkaunt

- Google yoki Apple bergan email allaqachon ErnestOS akkauntiga ulangan bo'lsa (email kodi orqali), o'sha akkauntga kirasiz. Yangi akkaunt ochilmaydi.
- Apple «emailni yashirish»ni tanlasangiz, `…@privaterelay.appleid.com` manzil keladi. Bu alohida akkaunt bo'ladi.

### Tekshirish

`https://<server>/api/app/config` javobida `"google": {"client_id": …, "browser": true}` va `"apple": {"browser": true}` chiqishi kerak.

## 4. APK imzo kaliti (bir marta, shart)

Hozircha har bir APK tasodifiy kalit bilan imzolanadi. Buning ikki zarari bor:
- yangi versiya eskisining ustiga o'rnatilmaydi — avval eskisini o'chirish kerak (ma'lumotlar serverda, ular yo'qolmaydi);
- Google telefondagi ilovani taniy olmaydi.

Bir marta doimiy kalit yarating:

1. O'z kompyuteringizda (yoki Railway → servis → **Shell**):
   ```
   python scripts/make_android_key.py
   ```
2. U uchta narsa chiqaradi:
   - `ANDROID_KEYSTORE_B64` — uzun qator;
   - `ANDROID_KEYSTORE_PASSWORD`;
   - `SHA-1`.
3. GitHub → repo → **Settings → Secrets and variables → Actions → New repository secret**:
   - `ANDROID_KEYSTORE_B64` = uzun qator;
   - `ANDROID_KEYSTORE_PASSWORD` = parol.
4. SHA-1 ni Google Cloud'dagi Android client'ga yozing (3-bo'lim).
5. Terminalni yoping. Kalitni hech qayerga (chat, skrinshot, repo) qo'ymang.

Shundan keyingi har bir APK shu kalit bilan imzolanadi. Actions → android → run sahifasidagi **Summary**'da SHA-1 ko'rinadi.

**Bir marta:** kalit almashgani uchun telefondagi eski ErnestOS'ni o'chirib, yangisini o'rnating. Keyingi yangilanishlar ustiga o'rnatiladi.

## 5. Railway → Variables (to'liq ro'yxat)

| O'zgaruvchi | Shartmi | Nima uchun |
|---|---|---|
| `BOT_TOKEN` | **Ha** | Telegram bot tokeni. Eslatma va hisobotlar jadvali ham shunga bog'liq |
| `DATABASE_URL` | **Ha** | PostgreSQL manzili (Railway Postgres) |
| `WEBAPP_URL` | **Ha** | Mini App manzili (`https://…railway.app/webapp`) |
| `RESEND_API_KEY` yoki `BREVO_API_KEY` + `EMAIL_FROM` | **Email uchun ha** | Ro'yxatdan o'tish kodlari (2-bo'lim) |
| `WEBHOOK_URL`, `WEBHOOK_SECRET` | Tavsiya | Bot webhook orqali ishlaydi |
| `BOT_USERNAME` | Tavsiya | Taklif havolalari uchun |
| `ADMIN_IDS` | Promokod uchun | Telegram ID'ingiz — `/promo_new` faqat shu ID'larga ishlaydi |
| `AGENT_ENABLED=true` va `GROQ_API_KEY` (yoki `GEMINI_API_KEY`) | AI uchun | AI «Hozir», AI suhbat, ovozli buyruq, AI kun xulosasi |
| `SUPPORT_EMAIL` | Play uchun tavsiya | `/privacy` va `/account/delete` sahifalaridagi aloqa manzili |
| `PUBLIC_URL` | Google/Apple uchun | Serverning tashqi manzili, `https://…` (callback manzillari shundan yasaladi) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google bilan kirish uchun | 3-bo'lim |
| `APPLE_CLIENT_ID` | Apple bilan kirish uchun | 3-bo'lim (Services ID) |
| `ELEVENLABS_API_KEY` | Ixtiyoriy | O'zbekcha ovozni yaxshiroq taniydi |
| `FCM_SERVICE_ACCOUNT` | Push uchun | Telefonga bildirishnoma (Firebase xizmat hisobi JSON) |
| `PLANS_ENABLED` | Ixtiyoriy | Standart holatda yoqilgan (Free/Pro/Max). `0` — hamma narsa ochiq |

## 6. APK'ni olish

1. GitHub → repo → **Actions** → **android** → eng oxirgi yashil (✓) ishga tushirish.
2. Sahifa pastidagi **Artifacts**'dan `ErnestOS-android-NN` ni yuklab oling (zip).
3. Zip ichidagi `ErnestOS-NN.apk` ni telefonga o'tkazing.
   - Imzo kaliti (4-bo'lim) sozlangan bo'lsa, bu **release** APK: telefonda debug rejimi yo'q, ilova ma'lumotlariga USB orqali kirib bo'lmaydi. Run sahifasidagi Summary'da «APK: release build» deb yoziladi.
   - Kalit bo'lmasa — **debug** APK (faqat sinov uchun).

APK `https://ernestos-bot-production.up.railway.app` ga ulanadi. Manzil boshqa bo'lsa, Actions → android → **Run workflow** → `api_url` ga yozing.

### Push (ilova yopiq bo'lganda ham xabar kelishi uchun)

Pushsiz ham ilova o'zi rejalashtirgan eslatmalar (vazifa, odat, hisobot vaqti) telefonda chiqadi. Lekin **serverdan keladigan xabarlar** — guruhda sizga topshiriq berilgani, yangi hisobot, to'lov — ilova ochilgandagina «Xabarlar»da ko'rinadi. Ular darhol kelishi uchun Firebase kerak (bepul):

1. https://console.firebase.google.com → **Add project** (masalan «ErnestOS»).
2. **Project settings → General → Add app → Android**. Package name: `uz.ernestos.app`. `google-services.json` faylini yuklab oling.
3. GitHub → Settings → Secrets → **New repository secret**: `GOOGLE_SERVICES_JSON` = faylning to'liq matni.
4. **Project settings → Service accounts → Generate new private key** → JSON fayl.
5. Railway → Variables: `FCM_SERVICE_ACCOUNT` = shu JSON'ning to'liq matni.
6. APK'ni qayta yig'ing (Actions → android → Run workflow) va o'rnating.

Ikkala JSON ham maxfiy — repo, chat yoki skrinshotga qo'ymang.

### Aniq vaqtda eslatish (Android 12+)

Android 14 dan boshlab «Signal va eslatmalar» ruxsati standart holatda o'chiq. Ilova endi bu oynani o'zi ochmaydi. Ruxsat bo'lmasa, eslatma bir necha daqiqa kechikishi mumkin. Yoqish: **Profil → ⚙️ → Eslatmalar va hisobotlar → Aniq vaqtda eslatish → Yoqish**.

### Google Play'ga chiqarish

1. Imzo kaliti (4-bo'lim) sozlangan bo'lsin — CI `ErnestOS-NN.aab` ham yig'adi (Artifacts ichida).
2. https://play.google.com/console → Developer akkaunt (bir martalik $25) → **Create app**.
3. **Store listing:** matnlar tayyor — `docs/STORE_LISTING.md` (uz/ru/en, belgilar limiti tekshirilgan).
4. **App content:**
   - Privacy policy: `https://<server>/privacy`
   - Data safety: javoblar `docs/GLOBAL_STANDARTLAR.md` 3-bo'limida
   - Account deletion URL: `https://<server>/account/delete`
   - Ads: yo'q · Content rating: Everyone · Target audience: 13+
5. **Production → Create release** → `.aab` faylni yuklang.

Imzo kalitini Play App Signing'ga topshirish so'ralsa, «Use Google-generated key» tanlang. Sizning kalitingiz upload key bo'lib qoladi.

## 7. Telefonga o'rnatish va kirish

1. APK'ni oching. «Noma'lum manbadan o'rnatish»ga ruxsat bering.
2. Ilovani oching va tilni tanlang.
3. **Email bilan:** emailingizni yozing → «Kod yuborish» → xatdagi 6 raqamni kiriting.
   - Email yangi bo'lsa, akkaunt o'zi yaratiladi. Keyin ilova ichida 3 ta savol beriladi: ism, til, nimalarni kuzatasiz (va uyg'onish vaqti).
   - Email avval ulangan bo'lsa, o'sha akkauntga kirasiz.
4. **Telegram'dagi hozirgi akkauntingiz bilan** (ma'lumotlaringiz yo'qolmasligi uchun):
   - ilovaga bir marta «Telegram orqali kirish» bilan kiring (botda `/app`);
   - **Profil → ⚙️ → Profilni tahrirlash → Email → Email ulash**;
   - shundan keyin shu email bilan kirasiz va o'sha akkauntga tushasiz.

5. **Google yoki Apple bilan** (3-bo'lim sozlangan bo'lsa): email ostidagi tugmani bosing.

Ilova «debug» turida yig'iladi, lekin 4-bo'limdagi doimiy kalit bilan imzolanadi. Shaxsiy foydalanish va sinov uchun bu yetarli. Play Market uchun `assembleRelease` va AAB kerak bo'ladi — bu alohida qadam.

## 8. Birinchi kun tekshiruvi (10 daqiqa)

- [ ] **Ro'yxatdan o'tish:** yangi email bilan kiring, kod keladi, 3 savoldan keyin «Bugun» ochiladi.
- [ ] **Email ulash:** Telegram akkauntingizga email ulang, chiqib, email bilan qayta kiring — ma'lumotlar o'sha.
- [ ] **Bugun:** «Hozir» kartasi, `+` orqali vazifa qo'shish.
- [ ] **Internetsiz:** internetni o'chiring, «Tushlik 45 ming» yozing → «N ta amal kutmoqda». Yoqing — o'zi yuboriladi.
- [ ] **Reja → Guruhlar:** ro'yxat → guruh. `+` → «Aniq odamga». Telegram'siz a'zo «Kod bilan qo'shilish» orqali kiradi (kod «⋯» ichida).
- [ ] **Moliya → Qarzlar:** bir odamga ikkinchi qarz — bitta kartaga qo'shiladi.
- [ ] **Profil → Yaqinlarim** (Pro/Max): odam, ritm, «Bog'landim».
- [ ] **Google bilan kirish:** chiqib, «Google orqali kirish» → telefondagi akkauntni tanlang → «Bugun» ochiladi.
- [ ] **Ovozli buyruq:** «Bugun»dagi `+` ustidagi 🎤 → «Ertaga soat 10 da Aziz bilan uchrashuv» → kartada «Tasdiqlash» → vazifa paydo bo'ladi.
- [ ] **AI yordamchida ovoz:** 🔔 → AI yordamchi → 🎤 → savol yoki buyruq ayting.
- [ ] **AI «Hozir»** (Pro/Max, AI kaliti bilan): kartada «AI» belgisi va sabab.
- [ ] **Promokod:** botda `/promo_new SINOV max 30` → ilovada Sozlamalar → Promokod.

## 9. Muammo bo'lsa

| Belgi | Sabab | Yechim |
|---|---|---|
| «Email orqali kirish hali yoqilmagan» | Serverda email kaliti yo'q | 2-bo'lim: `RESEND_API_KEY`/`BREVO_API_KEY` va `EMAIL_FROM` |
| «Xat yuborilmadi» | Kalit noto'g'ri, domen/sender tasdiqlanmagan | Resend/Brevo panelida Logs'ni ko'ring; `EMAIL_FROM` tasdiqlangan manzil bo'lsin |
| Kod kelmaydi | Spam papkada yoki limit tugagan | Spam'ni ko'ring; bir manzilga daqiqada 1 ta, soatiga 5 ta kod |
| «Sessiya tugadi» | Hisobdan chiqilgan yoki 90 kun ishlatilmagan | Qayta email bilan kiring |
| Ilova serverga ulanmaydi | APK boshqa manzilga yig'ilgan yoki server o'chiq | Railway'da servis ishlayaptimi, APK `api_url` to'g'rimi |
| AI qatori yo'q | AI kaliti yo'q yoki tarif Free | `AGENT_ENABLED`, `GROQ_API_KEY`; Pro/Max |
| Google/Apple tugmasi yo'q | Serverda `GOOGLE_CLIENT_ID` / `APPLE_CLIENT_ID` yo'q | 3-bo'lim |
| Google oynasi chiqmay, brauzer ochiladi | Android client SHA-1 noto'g'ri yoki kalit hali sozlanmagan | 4-bo'lim; SHA-1 ni Summary'dagisi bilan solishtiring |
| «Kirib bo'lmadi» (brauzerda) | Redirect URI / Return URL mos emas yoki `PUBLIC_URL` yo'q | Google/Apple panelida manzil `PUBLIC_URL` bilan bir xil bo'lsin |
| APK o'rnatilmaydi («ilova o'rnatilmadi») | Eski versiya boshqa kalit bilan | Eskisini o'chirib o'rnating (bir marta, 4-bo'lim) |
| 🎤 bosilsa «Hozir yoqilmagan» | AI kaliti yo'q | `AGENT_ENABLED=true` va `GROQ_API_KEY` |
| 🎤 ruxsat so'ramaydi / «mikrofonga ruxsat yo'q» | Telefonda ruxsat o'chirilgan | Sozlamalar → Ilovalar → ErnestOS → Ruxsatlar → Mikrofon |
| Eslatma bir necha daqiqa kech keladi | Android 14+: «Aniq vaqt» ruxsati o'chiq | Sozlamalar → Eslatmalar va hisobotlar → Aniq vaqtda eslatish → Yoqish |
| Telefon soati/batareya ko'rinmaydi (yuqorida) | Eski APK | Yangi APK'da status bar ilova rejimiga (yorug'/tungi) moslashadi |
| Push kelmaydi | Firebase sozlanmagan | `GOOGLE_SERVICES_JSON` (APK) va `FCM_SERVICE_ACCOUNT` (server) |
