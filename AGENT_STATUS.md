# v11 agent — tekshiruv holati

2026-09-30. Ushbu nusxa `ErnestOS-v10` asosida alohida tayyorlandi; original
papka va ZIP o‘zgartirilmadi. Railway’ga deploy va jonli botga ulanish bajarilmagan.

## Kiritilgan

- Telegram voice/audio/matn va Mini App mikrofon/audio fayl/matn.
- Mikrofon tugmasi → yozish; to‘xtatish → avtomatik tahlil; to‘liq taklif →
  aniq tasdiqlash. × yopish ham yozuvni qoralamaga yuborishga urinadi.
- PostgreSQL’da umumiy Inbox, tahrir versiyalari, amal tarixi, qayta tahlil.
- O‘zbek/rus/ingliz interfeyslari, alohida kiruvchi til maydoni.
- Vazifa/odat/loyiha CRUD va bajarildi/qayta ochish; pul yozuvlari va oylik limit.
- Loyiha/jamoasiz vazifa — Alohida; jamoa rollari va maxsus odat cheklovlari.
- Takroriy tasdiq, eski tugma, parallel so‘rov, egasi begona yozuv va
  keyin o‘zgargan obyektlardan himoya. Bir nechta amal bitta tranzaksiyada.
- Groq Free adapteri, rozilik, kunlik limit, audio chegarasi, pullik fallback yo‘q.
- Dockerfile (FFmpeg), Railway ulash yo‘riqnomasi va 32 ta jonli til sinov namunasi.

## Tekshirilgan

- Python offline testlari: **919 o‘tdi** (oldingi 872 + agentning 47 holati).
- Node offline mikrofon/approval testlari: **5 o‘tdi** — darhol yozish, birinchi
  rozilik, to‘xtatish, yopish/fonga o‘tish, Inbox va faqat tasdiqda bajarish.
- Python lint va JavaScript sintaksisi.
- Brauzerda mahalliy DEMO oynasi: matn, aniqlangan til, to‘liq taklif va tugmalar.

Offline testlarda AI javoblari almashtirilgan: ular haqiqiy o‘zbekcha nutq
aniqligining isboti emas. DEMO hech qanday haqiqiy vazifa yaratmaydi va kalit ishlatmaydi.

## Hali tekshirilmagan

- Haqiqiy Groq Free hisobida endpoint/model/kvota va ovoz aniqligi.
- Haqiqiy Telegram mobil WebView’da mikrofon ruxsati va iOS/Android yozuvi.
- Railway Docker build, deploy, jonli PostgreSQL, Telegram bot va tarmoq uzilishi.
- Haqiqiy FFmpeg bilan mahalliy dekodlash (bu Mac’da FFmpeg o‘rnatilmagan).
- 100 faol foydalanuvchi yuklamasi; 95% o‘zbekcha aniqlik; 24/7 xizmat kafolati.

Jonli ishga tushirishdan oldin [ulash va qabul sinovlari](docs/AGENT_SETUP_UZ.md)
bajarilishi kerak. API kalitlari, tokenlar yoki foydalanuvchi bazasi paketga kiritilmaydi.
