# ErnestOS — ishga tushirish yo'riqnomasi (server, email, APK)

Ilova to'liq ishlashi uchun uchta narsa kerak:

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

## 3. Railway → Variables (to'liq ro'yxat)

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
| `ELEVENLABS_API_KEY` | Ixtiyoriy | O'zbekcha ovozni yaxshiroq taniydi |
| `FCM_SERVICE_ACCOUNT` | Push uchun | Telefonga bildirishnoma (Firebase xizmat hisobi JSON) |
| `PLANS_ENABLED` | Ixtiyoriy | Standart holatda yoqilgan (Free/Pro/Max). `0` — hamma narsa ochiq |

## 4. APK'ni olish

1. GitHub → repo → **Actions** → **android** → eng oxirgi yashil (✓) ishga tushirish.
2. Sahifa pastidagi **Artifacts**'dan `ErnestOS-android-NN` ni yuklab oling (zip).
3. Zip ichidagi `app-debug.apk` ni telefonga o'tkazing.

APK `https://ernestos-bot-production.up.railway.app` ga ulanadi. Manzil boshqa bo'lsa, Actions → android → **Run workflow** → `api_url` ga yozing.

Push uchun GitHub → Settings → Secrets'ga `GOOGLE_SERVICES_JSON` qo'shib, APK'ni qayta yig'ing. Usiz ham eslatmalar ilova ichidagi «Xabarlar»ga va telefonning o'z eslatmalariga keladi.

## 5. Telefonga o'rnatish va kirish

1. APK'ni oching. «Noma'lum manbadan o'rnatish»ga ruxsat bering.
2. Ilovani oching va tilni tanlang.
3. **Email bilan:** emailingizni yozing → «Kod yuborish» → xatdagi 6 raqamni kiriting.
   - Email yangi bo'lsa, akkaunt o'zi yaratiladi. Keyin ilova ichida 3 ta savol beriladi: ism, til, nimalarni kuzatasiz (va uyg'onish vaqti).
   - Email avval ulangan bo'lsa, o'sha akkauntga kirasiz.
4. **Telegram'dagi hozirgi akkauntingiz bilan** (ma'lumotlaringiz yo'qolmasligi uchun):
   - ilovaga bir marta «Telegram orqali kirish» bilan kiring (botda `/app`);
   - **Profil → ⚙️ → Profilni tahrirlash → Email → Email ulash**;
   - shundan keyin shu email bilan kirasiz va o'sha akkauntga tushasiz.

Ilova «debug» imzoli — shaxsiy foydalanish va sinov uchun yetarli. Play Market uchun release imzo kaliti kerak bo'ladi.

## 6. Birinchi kun tekshiruvi (10 daqiqa)

- [ ] **Ro'yxatdan o'tish:** yangi email bilan kiring, kod keladi, 3 savoldan keyin «Bugun» ochiladi.
- [ ] **Email ulash:** Telegram akkauntingizga email ulang, chiqib, email bilan qayta kiring — ma'lumotlar o'sha.
- [ ] **Bugun:** «Hozir» kartasi, `+` orqali vazifa qo'shish.
- [ ] **Internetsiz:** internetni o'chiring, «Tushlik 45 ming» yozing → «N ta amal kutmoqda». Yoqing — o'zi yuboriladi.
- [ ] **Reja → Guruhlar:** ro'yxat → guruh. `+` → «Aniq odamga». Telegram'siz a'zo «Kod bilan qo'shilish» orqali kiradi (kod «⋯» ichida).
- [ ] **Moliya → Qarzlar:** bir odamga ikkinchi qarz — bitta kartaga qo'shiladi.
- [ ] **Profil → Yaqinlarim** (Pro/Max): odam, ritm, «Bog'landim».
- [ ] **AI «Hozir»** (Pro/Max, AI kaliti bilan): kartada «AI» belgisi va sabab.
- [ ] **Promokod:** botda `/promo_new SINOV max 30` → ilovada Sozlamalar → Promokod.

## 7. Muammo bo'lsa

| Belgi | Sabab | Yechim |
|---|---|---|
| «Email orqali kirish hali yoqilmagan» | Serverda email kaliti yo'q | 2-bo'lim: `RESEND_API_KEY`/`BREVO_API_KEY` va `EMAIL_FROM` |
| «Xat yuborilmadi» | Kalit noto'g'ri, domen/sender tasdiqlanmagan | Resend/Brevo panelida Logs'ni ko'ring; `EMAIL_FROM` tasdiqlangan manzil bo'lsin |
| Kod kelmaydi | Spam papkada yoki limit tugagan | Spam'ni ko'ring; bir manzilga daqiqada 1 ta, soatiga 5 ta kod |
| «Sessiya tugadi» | Hisobdan chiqilgan yoki 90 kun ishlatilmagan | Qayta email bilan kiring |
| Ilova serverga ulanmaydi | APK boshqa manzilga yig'ilgan yoki server o'chiq | Railway'da servis ishlayaptimi, APK `api_url` to'g'rimi |
| AI qatori yo'q | AI kaliti yo'q yoki tarif Free | `AGENT_ENABLED`, `GROQ_API_KEY`; Pro/Max |
| Push kelmaydi | Firebase sozlanmagan | `GOOGLE_SERVICES_JSON` (APK) va `FCM_SERVICE_ACCOUNT` (server) |
