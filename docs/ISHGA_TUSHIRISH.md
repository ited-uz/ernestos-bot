# ErnestOS — ishga tushirish yo'riqnomasi (APK va server)

APK telefonda ishlashi uchun ikkita narsa tayyor bo'lishi kerak:

1. **Server**, ya'ni Railway'dagi bot va API. U shu branchdagi yangi kodda ishlashi kerak.
2. **APK**, ya'ni GitHub Actions yig'gan ilova. U serverga ulanadi.

Server eski kodda qolsa, yangi bo'limlar ishlamaydi: AI «Hozir», internetsiz tezkor qo'shish, doimiy to'lovlar holati.

---

## 1. Kodni serverga chiqarish

1. Branchni `main` ga birlashtiring:
   - GitHub'da `claude/brave-mayer-78dw7h` → `main` Pull Request'ini oching va "Merge" bosing;
   - yoki menga "merge qil" deng.
2. Railway odatda `main` ga push bo'lganda o'zi deploy qiladi. Railway → Deployments bo'limida yangi deploy "Success" bo'lganini tekshiring.
3. Deploy'dan keyin **bir marta** Railway → servis → Shell oynasida ishga tushiring:

   ```
   python migrations.py 0014 0015
   ```

   - `0014` eski mavzu nomlarini yangilariga o'tkazadi.
   - `0015` maqsadlardagi «Xayriya»ni «Islom va xayriya»ga, eski `career`ni «Biznes»ga o'tkazadi.

   Ikkinchi marta ishga tushirsangiz ham zarar qilmaydi.

Server ishga tushganda quyidagilar o'zi qo'shiladi, ular uchun alohida buyruq kerak emas:
- yangi ustunlar: `weekly_focus.goal_id`, `money_entries.subscription_id`, `users.now_ai_off`, `users.profile_edited_at`;
- yangi jadval: `close_people` (Yaqinlarim).

## 2. Railway → Variables

| O'zgaruvchi | Shartmi | Nima uchun |
|---|---|---|
| `BOT_TOKEN` | **Ha** | Telegram bot tokeni (@BotFather) |
| `DATABASE_URL` | **Ha** | PostgreSQL manzili (Railway Postgres) |
| `WEBAPP_URL` | **Ha** | Mini App manzili (`https://…railway.app/webapp`) |
| `WEBHOOK_URL`, `WEBHOOK_SECRET` | Tavsiya | Bot webhook orqali ishlaydi |
| `BOT_USERNAME` | Tavsiya | Taklif havolalari uchun |
| `ADMIN_IDS` | Promokod uchun | Telegram ID'ingiz, masalan `123456789` — `/promo_new` buyrug'i faqat shu ID'larga ishlaydi |
| `AGENT_ENABLED=true` va `GROQ_API_KEY` (yoki `GEMINI_API_KEY`) | AI uchun | AI «Hozir», AI suhbat, ovozli buyruq, AI kun xulosasi |
| `ELEVENLABS_API_KEY` | Ixtiyoriy | O'zbekcha ovozni yaxshiroq taniydi |
| `FCM_SERVICE_ACCOUNT` | Push uchun | Telefonga bildirishnoma (Firebase xizmat hisobi JSON) |
| `PLANS_ENABLED` | Ixtiyoriy | Standart holatda yoqilgan (Free/Pro/Max). `0` qilsangiz, hamma narsa ochiladi |

AI kaliti bo'lmasa, AI qatorlari ko'rinmaydi va ilova oddiy qoidalar bilan ishlayveradi. Lekin AI ishlayotgandek ko'rsatilmaydi.

## 3. APK'ni olish

1. GitHub → repo → **Actions** → **android** → eng oxirgi yashil (✓) ishga tushirish.
2. Sahifa pastidagi **Artifacts** bo'limidan `ErnestOS-android-NN` ni yuklab oling (zip).
3. Zip ichidagi `app-debug.apk` ni telefonga o'tkazing.

APK `https://ernestos-bot-production.up.railway.app` manziliga ulanib yig'ilgan. Server manzili boshqa bo'lsa:
- Actions → android → **Run workflow** → `api_url` maydoniga to'g'ri manzilni yozing;
- yoki GitHub → Settings → Variables'da `ERNEST_API_URL` ni bir marta qo'yib qo'ying.

Push bildirishnomalar uchun GitHub → Settings → Secrets'ga `GOOGLE_SERVICES_JSON` (Firebase `google-services.json` matni) qo'shing va APK'ni qayta yig'ing. Usiz ham ilova ishlaydi; eslatmalar ilova ichidagi «Xabarlar» bo'limiga keladi.

## 4. Telefonga o'rnatish

1. APK faylini oching. Android "Noma'lum manbadan o'rnatish"ga ruxsat so'raydi — ruxsat bering.
2. Ilova "debug" imzoli. Shaxsiy foydalanish va sinov uchun yetarli. Play Market uchun alohida release imzo kaliti kerak bo'ladi — buni keyin sozlaymiz.
3. Ilovani oching. Til tanlanadi, so'ng kirish kodi so'raladi.

## 5. Ilovaga kirish

1. Telegram'da botingizga `/app` yozing. Bot 10 daqiqa amal qiladigan kod yuboradi.
2. Kodni ilovaga kiriting. Bot va ilova bitta hisobda ishlaydi.

## 6. Birinchi kun tekshiruvi (5 daqiqa)

- [ ] **Bugun:** "Hozir" kartasi chiqadi, `+` orqali vazifa qo'shiladi.
- [ ] **Internetsiz ishlash:** Wi-Fi va mobil internetni o'chiring. Vazifa yoki «Tushlik 45 ming» yozing, «N ta amal kutmoqda» chiqadi. Internetni yoqing — yozuvlar o'zi yuboriladi.
- [ ] **Reja → Guruhlar:** "Yangi guruh" (Pro/Max'da). Taklif havolasi "⋯" ichida.
- [ ] **Profil → Sozlamalar:** Ko'rinish → Tungi rejim yoqiladi (Free'da ham).
- [ ] **AI «Hozir»** (Pro/Max va AI kaliti bilan): rozilik so'ralmaydi, kartada o'zi «AI» belgisi va sabab chiqadi. O'chirish: Sozlamalar → Kuzatiladigan bo'limlar.
- [ ] **Profil → ⚙️ → Profilni tahrirlash:** ism, familiya va username o'zgaradi.
- [ ] **Reja → Guruhlar:** avval guruhlar ro'yxati chiqadi, guruhni ochasiz. `+` → «Aniq odamga» → a'zoni tanlaysiz. Unga «📌 sizga topshiriq berdi» xabari boradi.
- [ ] **Moliya → Qarzlar:** bir odamga ikkinchi qarzni yozing — ikkalasi bitta kartaga qo'shiladi.
- [ ] **Profil → Yaqinlarim** (Pro/Max): odam qo'shing, «har 14 kunda» ritmini tanlang, «Bog'landim»ni bosing.
- [ ] **Promokod** (o'zingizni sinash uchun): botda `/promo_new SINOV max 30` → ilovada Sozlamalar → Promokod kiritish.

## 7. Muammo bo'lsa

| Belgi | Sabab | Yechim |
|---|---|---|
| "Sessiya tugadi" | Kod eskirgan yoki hisobdan chiqilgan | Botda `/app`, yangi kod bilan kiring |
| Ilova serverga ulanmaydi | APK boshqa manzilga yig'ilgan yoki server o'chiq | Railway'da servis ishlayotganini va APK yig'ilgan `api_url` ni tekshiring |
| AI qatori yo'q | AI kaliti yo'q yoki tarif Free | `AGENT_ENABLED`, `GROQ_API_KEY` ni tekshiring; Pro/Max'ga o'ting |
| Push kelmaydi | Firebase sozlanmagan | `GOOGLE_SERVICES_JSON` (APK) va `FCM_SERVICE_ACCOUNT` (server) |
