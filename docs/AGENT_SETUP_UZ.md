# Ernest ovozli agent — ulash yo‘riqnomasi

## Sizga nima kerak?

1. Mavjud Railway loyihangizga kirish huquqi va PostgreSQL zaxira nusxasi.
2. O‘sha mavjud Telegram bot tokeni va Mini App manzili. Yangi bot shart emas.
3. O‘zingizga tegishli **Groq Free** hisobidagi API kalit:
   [Groq Console](https://console.groq.com/keys). Pullik rejaga o‘tmang.
4. Sinov uchun o‘zbekcha, ruscha, inglizcha va aralash tildagi qisqa ovozlar.

Kalit yoki bot tokenini chatga, kodga yoki ZIP ichiga yubormang.
Ularni faqat Railway → kerakli servis → Variables bo‘limiga kiriting.

## Railway’da yoqish

Avval amaldagi ma’lumotlar bazasining tiklash mumkin bo‘lgan backupini oling.
Ushbu papkadagi fayllarni Railway bog‘langan repozitoriyga joylang. Loyiha
ildizida Dockerfile bo‘lishi kerak: u ovozni o‘qish uchun FFmpeg o‘rnatadi.
Start command sozlamasi bo‘lsa, Dockerfile buyrug‘iga zid bo‘lmasin.
Sinov serveri `tests/preview_agent.py` ni Railway’da ishga tushirmang.

Mavjud `BOT_TOKEN`, `DATABASE_URL`, `WEBAPP_URL`, `TZ` va boshqa sozlamalarni
o‘zgartirmang. Quyidagilarni qo‘shing:

| Variable | Qiymat |
|---|---|
| `AGENT_ENABLED` | Dastlab `false`; deploy tekshirilgach `true` |
| `AGENT_PROVIDER` | `groq` |
| `GROQ_API_KEY` | O‘zingizning maxfiy Free-plan API kalitingiz |
| `AGENT_TEXT_MODEL` | `openai/gpt-oss-120b` |
| `AGENT_SPEECH_MODEL` | `whisper-large-v3` |
| `AGENT_DAILY_REQUESTS` | `30` — bitta workspace uchun kunlik tahlil limiti |

Model nomida `openai/` bo‘lishi OpenAI pullik API chaqirilishini anglatmaydi:
ushbu sozlamalarda barcha AI so‘rovlari Groq’ga yuboriladi. Pullik provayderga
avtomatik o‘tish bloklangan. Ammo kod Groq hisobingizning billing rejasini
tekshira olmaydi: Free rejada qolish hisob egasining mas’uliyati.

PostgreSQL’ga yangi agent jadvallari dastur ishga tushganda qo‘shiladi.
Mavjud yozuvlarni o‘chirish talab qilinmaydi. **Bitta servis nusxasi va bitta
worker** bilan ishlating; ayni token bilan ikkita polling bot ishlatmang.
Railway HTTPS manzilidan Mini App ochilishi kerak. Mikrofon ruxsatini qurilmada
foydalanuvchi beradi; Telegram versiyasida yozish ishlamasa, bot chatiga voice yuboring.

## Foydalanish tartibi

Mini App:

1. O‘ngdagi **🎙 Ernest** ni bosing. Birinchi safar AI xizmatiga yuborishga
   rozilik va mikrofon ruxsati so‘raladi. Keyingi bosishda yozish boshlanadi.
2. **Yozishni tugatish** ni bosing. 119 soniyada yozish o‘zi to‘xtaydi.
3. Audio avtomatik tahlil qilinadi. Xabar matni, aniqlangan til, bo‘lim,
   loyiha/jamoa, nom, sana, vaqt va rejalashtirilgan o‘zgarishlar ko‘rsatiladi.
4. **Tasdiqlash** — ko‘rsatilgan amallar bajariladi. **Tuzatish** — matn yoki
   yangi ovoz bilan tuzatasiz, qayta tasdiqlaysiz. **Inbox’da qoldirish** —
   hozir hech narsa bajarilmaydi. Umuman tasdiqlamasangiz ham qoralama saqlanadi.

Yozish oynasini × bilan yopsangiz, yozish to‘xtab qoralamaga yuboriladi.
Lekin ilova butunlay yopilib qolsa, qurilma tarmoqni uzsa yoki OS fon rejimini
to‘xtatsa, yuborilmagan audio uchun kafolat yo‘q. Inbox’da borligini tekshiring.
Server audioni saqlamaydi: tanish muvaffaqiyatsiz tugasa, audio qayta yuboriladi.

**📥 Inbox** alohida tugmasi mikrofonni yoqmaydi. Telegram botda `/agent` —
rozilik/yo‘riqnoma, `/inbox` — saqlangan buyruqlar. Bot ovozli yoki oddiy matnli
buyruqqa to‘liq taklif va tugmalar bilan javob beradi. “Ha” deb yozishning o‘zi
bajarishga ruxsat emas — tasdiqlash tugmasi kerak. Agent faqat shaxsiy chatda.

## “Alohida” qoidasi va tillar

Loyiha yoki jamoa aytilmagan yangi vazifa mavjud **Alohida** bo‘limiga tushadi;
“Alohida” nomli yangi loyiha yaratilmaydi. Nom aniq aytilsa, mavjud mos loyiha
yoki jamoa qidiriladi. Bir xil nomlar bo‘lsa, aniqlashtirish so‘raladi.

Xabar tili interfeys tilidan alohida aniqlanadi. O‘zbek lotin/kirill, rus,
ingliz va aralash buyruqlar uchun parser tayyorlangan. Noaniq gapdan taxminiy
amal bajarilmaydi. **95% aniqlik hozircha o‘lchanmagan va kafolatlanmaydi.**

## Bepullik va 24/7 chegarasi

Groq Free cheksiz emas: uning kvotasi barcha foydalanuvchilarga umumiy.
Limit tugasa pullik xizmat yoqilmaydi; buyruq Inbox’da qoladi, keyin qayta tahlil
qilinadi. Ovoz hali matnga aylanmagan bo‘lsa, qayta yuborish kerak.
100 faol foydalanuvchini uzluksiz bepul xizmat bilan ta’minlash kafolati yo‘q.
Railway hosting xarajati AI xarajatidan alohida. Mac ochiq turishi shart emas.
Joriy hisob limitlari: [Groq rate limits](https://console.groq.com/docs/rate-limits).

## Majburiy jonli sinov

Avval o‘z akkauntingizda quyidagilarni tekshiring:

- O‘zbekcha: “Ertaga Abdulbosid bilan soat 10 am da meetingim bor”.
- Ruscha: “Добавь задачу подготовить отчёт завтра в десять утра”.
- Inglizcha: “Add a task to prepare the report tomorrow at ten AM”.
- O‘zbek kirill va shovqinli/aralash ovoz; ism, sana, vaqtni alohida tekshiring.
- Tasdiqlamasdan yoping → bot `/inbox` va Mini App’da bir xil qoralama bo‘lsin.
- “Ertaga emas, juma 11:00” deb tuzating → eski tasdiqlash ishlamasin.
- Tasdiqlashni ikki marta bosing → faqat bitta vazifa qo‘shilsin.
- Loyiha aytmang → Alohida; mavjud loyihani ayting → aynan o‘sha loyiha.
- Odat, bajarildi/qayta ochish, pul yozuvi va o‘chirishni test yozuvlarda tekshiring.

Katta auditoriyadan oldin kamida 100 xil o‘zbekcha audio yig‘ib, matn xatosi
va buyruq maydonlari to‘g‘riligini alohida o‘lchang. Sana/vaqt/summa noto‘g‘ri
bo‘lsa, yaxshi ko‘ringan matn ham to‘g‘ri buyruq hisoblanmaydi.

`evaluate_agent.py --run-free-api` 32 matnli holatni Groq’da sinaydi;
`--audio sample.ogg --reference "aytilgan matn"` ovozning so‘z xatosini o‘lchaydi.
Bu vosita app yozuvlarini o‘zgartirmaydi, ammo Free kvotasini sarflaydi.

## Maxfiylik va cheklovlar

Audio, matn va mos yozuvlar ro‘yxati Groq’ga yuboriladi. Audio faqat ishlov
vaqtida xotirada turadi; matn/qoralama/amal tarixi PostgreSQL’da saqlanadi.
Groq’dagi ma’lumot saqlash shartlari provayder siyosatiga bog‘liq.
Inbox → **AI tarixini o‘chirish** qoralama va agent tarixini o‘chiradi, lekin
yaratilgan vazifalarni yoki pul yozuvlarini o‘chirmaydi.

Agent server kodi, hisob ruxsatlari, bank o‘tkazmalari yoki boshqa odamning
shaxsiy ma’lumotlarini boshqarmaydi. Jamoada mavjud rollar amal qiladi.
“Hey Ernest” telefon ekranidan tashqarida doimiy tinglash emas: yozish
Telegram yoki Mini App’da boshlanadi. 24 soatdan eski taklif qayta tahlil
qilinadi; eski va keyin o‘zgargan yozuvlar ko‘r-ko‘rona bajarilmaydi.

Muammo bo‘lsa `AGENT_ENABLED=false` qiling: oddiy bot va mavjud ma’lumotlar qoladi.
