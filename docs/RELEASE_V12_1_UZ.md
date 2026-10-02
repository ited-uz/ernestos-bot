# ErnestOS v12.1 — yakuniy hisobot

Sana: 2026-10-02. Asos: foydalanuvchi bergan `ErnestOS-v12(2).zip`.
Asl arxiv va oldingi papka o‘zgartirilmadi; tuzatishlar alohida nusxada bajarildi.

## Holat

**Mahalliy testlardan o‘tgan reliz nomzodi.** Bu “100% xatosiz” yoki jonli
ishlash tasdiqlangan degani emas. Foydalanuvchi talabi bilan jonli
Telegram/Railway sinovi bajarilmadi. Shu sababli texnik sifatga 9.8/10 yoki
public launch tayyorligiga 10/10 kabi o‘lchanmagan baholar berilmaydi.

## Muhim tuzatishlar

| Muammo / noaniqlik | v12.1 yechimi |
| --- | --- |
| Telefon pastki menyusi tig‘iz | Besh tugma: Asosiy, Odatlar, Vazifalar, Statistika, Ko‘proq. Jamoa, Pul va Sozlamalar yo‘qolmagan; Ko‘proq ichida. |
| Odat soni bilan ekrandagi qatorlar farqi | Majburiy / Maqsadli / Qo‘shimcha sarlavhalari, bugun rejalashtirilgan va rejasiz sonlar, namoz va jamoa alohida hisoblanishi izohi. |
| Vazifa soni va foizi turlicha | Statistika vazifa prioritetining ballarini va amalda ishlatilgan normallashtirilgan vaznlarni ko‘rsatadi; namozning ado etilishi va sifat balli ajratilgan. Formula o‘zgarmagan. |
| Jamoada men bajargan ish ham ochiq | Shaxsiy bajarilgan holat, hali bajarmagan a’zolar va umumiy ochiq ishlar alohida nomlanadi. |
| Balans qaysi davrga tegishli? | Balans barcha davr uchun ekani, kirim/chiqim tanlangan oyga tegishli ekani ochiq yozildi. 100 yozuvdan ko‘p bo‘lsa cheklov va to‘liq eksport yo‘li ko‘rsatiladi. |
| Ovoz noto‘g‘ri summani darhol yozadi | Matn/ovoz avval faqat tahlil qilinadi. Summa, tur va kategoriya tekshirilib, Saqlash bosilgandagina yozuv yaratiladi. Tahlil bepul amal hisobini sarflamaydi. |
| Katta ro‘yxat va eski ishni qidirish | Shaxsiy vazifalar 50 tadan yuklanadi; server chegarasi 100. Davom ettirish kursori, umumiy son va Yana ko‘rsatish bor. Bajarilganlarda qidirish cheklovdan oldin bajariladi. Kirillcha qidirish SQLite’da ham ishlaydi. |
| Sana bir kun siljishi | Sana surish UTC kunlari orqali, bugungi sana profil vaqt mintaqasi orqali hisoblanadi. Toshkent va Nyu-York muhitlarida tekshirildi. |
| Xatoda forma yo‘qolishi / spinner qolishi | Saqlash xatosida forma saqlanadi; sessiya, limit, noto‘g‘ri maydon va o‘chirilgan yozuv xabarlari ajratildi. Qayta yuklash mavjud. Takroriy saqlash bloklandi. Kundalikning qurilmadagi nusxasi izohlanadi. |
| Obuna va agentning mavjudligi noma’lum | 20 amaldan keyingi kanal sharti onboarding yakunida va bosh sahifada oldindan ko‘rsatiladi; qolgan amallar yangilanadi. Agent holati Ko‘proq ichida ko‘rinadi. |

Qo‘shimcha: rejasiz kun “hammasi bajarildi” deb ko‘rsatilmaydi; shaxsiy
ishlar tugab, jamoa ishi qolsa Hozir kartasi jamoaga yo‘naltiradi. Eski
foydalanuvchilar uchun tayyor odatlar taklifi qo‘shildi; uni yashirish mumkin.
Namuna ko‘rinishidagi sana va bugungi ko‘rsatkichlar ham aniqlashtirildi.

Hech bir modul olib tashlanmadi: odatlar, namoz, kundalik, uyg‘onish,
vazifalar, takrorlanish, loyihalar, hafta maqsadi, kalendar, tug‘ilgan kunlar,
taymer/countdown, jamoalar va rollar, pul/byudjet, hisobotlar, daraja/XP,
referral, hisob, eksport, sozlamalar, Groq yordamchisi saqlangan.

## Xavfsizlik va kutubxonalar

Audit eski Starlette 0.48.0 va test muhitidagi pip 26.1.1 uchun ma’lum
zaifliklar qaytardi. FastAPI **0.142.2**, Starlette **1.7.0**, pip **26.2.1**
bilan qayta tekshirildi: `pip-audit` — **No known vulnerabilities found**.
Bu faqat tekshiruv paytidagi ommaviy bazaga tegishli, to‘liq xavfsizlik kafolati emas.

`constraints-tested.txt` tekshirilgan runtime versiyalarini saqlaydi.
Dockerfile yangilangan pip va shu cheklovlar bilan o‘rnatadi.
Agent yoqilgan production muhiti Groq kaliti yoki FFmpeg bo‘lmasa aniq xabar
bilan ishga tushishni to‘xtatadi. Agent odatdagidek sukut bo‘yicha o‘chiq.
Provider limitlari doimiy raqam deb berilmaydi — Groq hisobingizdan tekshiriladi.

## Tekshiruv dalillari

- Python 3.14, macOS, vaqtinchalik SQLite: **946 test**.
- Yangi 11 regression test: sahifalash, nusxa takrorlanmasligi, yangi yozuv
  qo‘shilganda kursor, foydalanuvchilar ajratilishi, `%`/`_` qidiruvi, kirillcha
  qidiruv, eski bajarilgan vazifa, pul preview, obuna metama’lumoti,
  amallar sarlavhasi, vaznlar va agent sozlamalari.
- Python lint va `pip check`: muammo topilmadi.
- Haqiqiy Mini App JavaScript’i Node ichida: barcha asosiy ekranlar,
  vazifa/odat ichki bo‘limlari, uch tildagi statik tarjima kalitlari,
  pulni tasdiqlash va sana chegaralari. Toshkent va Nyu-York TZ.
- 390×844 mobil brauzerda **namuna ma’lumotlari** bilan bosh sahifa,
  menyu, pul preview oynasi, vazifalar, odatlar va statistika ko‘rib chiqildi.
  Bu haqiqiy Telegram akkaunti bilan end-to-end sinov emas.
- CI’ga frontend regression sinovlari ham qo‘shildi.

Testlarda bitta kutubxona ogohlantirishi bor: Starlette test mijozi `httpx`
o‘rniga kelajakda `httpx2` ishlatishni tavsiya qiladi. Testlar o‘tadi;
production funksiyasidagi xato emas.

Qayta tekshirish:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q
TZ=Asia/Tashkent node tests/frontend_release.cjs
TZ=America/New_York node tests/frontend_release.cjs
python -m pip check
```

## O‘rnatish / mavjud foydalanuvchilarni saqlash

1. Mavjud PostgreSQL bazasining backup’ini oling. Eski bot tokeni va aynan
   o‘sha `DATABASE_URL` saqlanadi. Yangi baza yaratish shart emas.
2. Kodni yangilang; `.env.example` — faqat shablon. Undagi `xxxxxxxxxx`,
   `USER`, `PASSWORD`, `HOST` o‘rniga haqiqiy server sozlamalari qo‘yiladi.
   Haqiqiy kalitlarni ZIP yoki chatga kiritmang.
3. `BOT_TOKEN`, `BOT_USERNAME`, `DATABASE_URL`, `WEBAPP_URL` (HTTPS),
   `ENVIRONMENT=production`, `TZ=Asia/Tashkent` ni tekshiring.
4. Talab saqlanishi uchun `REQUIRED_CHANNEL_ID`, `REQUIRED_CHANNEL_URL`,
   `FREE_ACTIONS=20` sozlang. Bot kanal a’zoligini tekshira olishi kerak.
   Kanal ID bo‘sh bo‘lsa avvalgi xatti-harakat saqlanadi: obuna talabi o‘chadi.
5. Agent kerak bo‘lsa `AGENT_ENABLED=true`, `GROQ_API_KEY` va FFmpeg kerak.
   Berilgan Dockerfile FFmpeg o‘rnatadi. Telefon brauzerining pul mikrofoni
   alohida imkoniyat: brauzer qo‘llamasa matn/formadan foydalaniladi.
6. Bitta polling worker ishlating. Eski va yangi bot jarayonini bitta token
   bilan bir vaqtda ishlatmang. Webhook ishlatilsa `WEBHOOK_SECRET` ham kerak.
7. `/health/live` va `/health/ready` ni tekshiring. Keyin Telegram’dan ilovani
   qayta oching — eski ochiq sahifa eski interfeysni ushlab turishi mumkin.

Yangi DB sxemasi qo‘shilmadi. Mavjud migratsiya mexanizmi saqlangan.
Rollback uchun eski kod arxivi va backup saqlansin; avtomatik ma’lumot
o‘chirish yoki bazani qayta yaratish talab qilinmaydi.

## Ochiq tekshiruv chegaralari

Docker/Linux/Python 3.12 build, haqiqiy PostgreSQL migratsiyasi va yuklama,
Telegram a’zolik/real xabarlar/notification, haqiqiy ovoz → FFmpeg → Groq,
Railway HTTPS hamda iOS/Android Telegram webview sinovi bu muhitda bajarilmadi.
Provider javoblari agent testlarida soxtalashtirilgan. ZIP tayyorligi bilan
production muhiti tasdiqlanganini aralashtirmaslik kerak.

## Tekshirilgan rasmiy manbalar

- [Telegram Mini Apps](https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app): serverda imzoni tekshirish, webview integratsiyasi.
- [Groq Speech-to-Text](https://console.groq.com/docs/speech-to-text): transkripsiya endpointi, Whisper modellari, til va audio parametrlari.
- [FastAPI paketi](https://pypi.org/project/fastapi/): yangilangan paket oilasi. Xavfsizlik natijasi alohida `pip-audit` orqali tekshirildi.
