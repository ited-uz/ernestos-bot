# Ernest ovozli agenti (v12) — ulash yo‘riqnomasi

## Qanday ishlaydi

1. Telegram botga (shaxsiy chatda) ovozli xabar yoki matn yuborasiz.
2. Groq **Whisper-large-v3** ovozni profil tilingizda matnga aylantiradi.
3. Groq AI matndan taklif tuzadi: vazifa, odat, loyiha yoki pul yozuvini
   **yaratish, o‘zgartirish yoki o‘chirish** (shaxsiy yoki jamoa).
4. Bot taklifni ko‘rsatadi: **✅ Tasdiqlash · ✏️ Tahrirlash · ❌ Bekor qilish**.
   Tasdiqlamaguningizcha hech narsa o‘zgarmaydi. Taklif 24 soatdan keyin eskiradi.

Misollar: «Ovqatga 5 ming so‘m», «Ertaga soat 10 da Abdulvosid bilan
uchrashuv», «Hisobot vazifasini jumaga ko‘chir», «Kitob odatini o‘chir».

## Til qoidasi

- Profil tili o‘zbekcha → faqat o‘zbekcha (lotin yoki kirill) qabul qilinadi.
  Ichida «meeting», «problema» kabi so‘zlar bo‘lishi mumkin.
- Rus / ingliz profili → faqat ruscha / inglizcha.
- Boshqa tildagi xabarga bot profil tilida «Iltimos, o‘zbek tilida gapiring»
  deb javob beradi va hech narsa qilmaydi. Tilni bot sozlamalarida o‘zgartiring.

## Railway sozlamalari

Avval PostgreSQL backupini oling. Mavjud `BOT_TOKEN`, `DATABASE_URL`,
`WEBAPP_URL` va boshqalarni o‘zgartirmang. Faqat qo‘shing:

| Variable | Qiymat |
|---|---|
| `GROQ_API_KEY` | [console.groq.com/keys](https://console.groq.com/keys) dagi kalit |
| `AGENT_ENABLED` | `true` (muammo bo‘lsa `false` — oddiy bot ishlashda davom etadi) |
| `AGENT_DAILY_REQUESTS` | Ixtiyoriy, sukut `30` — bitta foydalanuvchiga kunlik buyruq |

Kalitni chatga, kodga yoki ZIP ichiga yozmang. Dockerfile FFmpeg o‘rnatadi —
u ovozni o‘qish uchun kerak. Bitta servis nusxasi, bitta worker.

## Groq Free limiti

Free limit **butun bot uchun umumiy** (kalitga emas, hisobga):

| Model | Limit | Amalda |
|---|---|---|
| `openai/gpt-oss-120b` | 200 000 token/kun, 8 000 token/daqiqa | **~87 buyruq/kun, ~3/daqiqa** |
| `whisper-large-v3` | 2 000 so‘rov/kun | ~2 000 ovoz/kun |

Limit tugasa bot «1 daqiqadan keyin qayta yuboring» (yoki «ertaga») deydi,
hech narsa yo‘qolmaydi. Developer tarifi ochilganda: console.groq.com →
Settings → Billing → Developer, xarajat chegarasi qo‘ying. Kod o‘zgarmaydi.
Narx ≈ $0.0012 (~15 so‘m) bir buyruq uchun.

## Birinchi sinov

- «Ovqatga 5 ming so‘m» → Pul yozuvi, Chiqim, 5 000 → Tasdiqlash.
- «Ertaga soat 10 da hisobot tayyorlash» → Vazifa, Alohida, sana/vaqt to‘g‘ri.
- Tahrirlash → «Ertaga emas, juma kuni» → yangi taklif; eski tugma ishlamasin.
- Tasdiqlashni ikki marta bosing → faqat bitta yozuv.
- Ruscha gapiring (o‘zbek profilida) → rad etilsin.
- «Hisobot vazifasini o‘chir» → o‘chirish taklifi → Tasdiqlash.

Noto‘g‘ri tushungan ovozni saqlab qo‘ying: `python evaluate_agent.py
--run-free-api --audio voice.ogg --reference "aslida aytilgan gap"` matn
xatosini o‘lchaydi (app ma’lumotlarini o‘zgartirmaydi).

## Maxfiylik

Ovoz va matn Groq’ga (AQSh) yuboriladi; foydalanuvchi bir marta rozilik
beradi. Audio serverda saqlanmaydi; matn va amal tarixi PostgreSQL’da.
Agent hisoblar, ruxsatlar, bank o‘tkazmalari yoki boshqa odamning shaxsiy
ma’lumotlarini o‘zgartirmaydi. Jamoada mavjud rollar tekshiriladi.
