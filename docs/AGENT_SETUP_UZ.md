# Ernest ovozli agenti (v12.2) — ulash yo‘riqnomasi

## Qanday ishlaydi

1. Telegram botga (shaxsiy chatda) ovozli xabar yoki matn yuborasiz.
2. **ElevenLabs Scribe v2** ovozni profil tilingizda matnga aylantiradi
   (kalit bo‘lmasa yoki kredit tugasa — Groq Whisper-large-v3, keyin turbo).
3. Groq AI (`gpt-oss-120b`, bepul) matndan taklif tuzadi; Groq limiti tugasa
   Gemini (pullik, kalit bo‘lsa), keyin Groq `gpt-oss-20b`: vazifa, odat, loyiha yoki pul yozuvini
   **yaratish, o‘zgartirish yoki o‘chirish** (shaxsiy yoki jamoa).
4. Bot bitta qisqa qator ko‘rsatadi, masalan `➕ Chiqim: 5 000 so‘m · Oziq-ovqat`,
   va tugmalar: **✅ Tasdiqlash · ✏️ Tahrirlash · ❌ Bekor qilish**.
   Tasdiqlamaguningizcha hech narsa o‘zgarmaydi. Taklif 24 soatdan keyin eskiradi.

Aytilmagan narsa so‘ralmaydi, o‘zi to‘ldiriladi: pul — bugun, chiqim;
vaqti aytilgan vazifa — bugun; odat — har kuni. «Soat 8» = 08:00,
«soat 3» = 15:00, «soat 8 kechqurun» = 20:00. Savol faqat summa yoki nom
umuman aytilmaganda beriladi.

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
`WEBAPP_URL` va boshqalarni o‘zgartirmang. Qo‘shing:

| Variable | Qiymat | Shartmi |
|---|---|---|
| `AGENT_ENABLED` | `true` | ha |
| `ELEVENLABS_API_KEY` | elevenlabs.io → API keys | tavsiya (o‘zbek ovozi uchun eng yaxshisi) |
| `GROQ_API_KEY` | console.groq.com/keys | ha (bepul matn + zaxira ovoz) |
| `GEMINI_API_KEY` | aistudio.google.com, billing yoqilgan | 1000 foydalanuvchi uchun ha |
| `AGENT_DAILY_REQUESTS` | `30` (sukut) | yo‘q |

Kalit bo‘lmagan provayder shunchaki o‘tkazib yuboriladi. Dockerfile FFmpeg
o‘rnatadi. Bitta servis nusxasi, bitta worker. Eski foydalanuvchilarning
odat nomlarini bir marta mahalliylashtirish: `python migrations.py 0013`.

## Limit va xarajat

Bir buyruq: ~10 soniya ovoz + ~2 300 token.

| Qism | Narx | 1 buyruq |
|---|---|---|
| ElevenLabs Scribe v2 (+ism lug‘ati) | $0.22 + $0.05 / soat audio | ≈ $0.00075 |
| Groq `gpt-oss-120b` Free | bepul, butun bot uchun ~87 buyruq/kun | $0 |
| Gemini (Groq tugagach) | `gemini-flash-latest`: ~$0.50–0.75 kirish / ~$3–3.75 chiqish (1M token, versiyaga qarab) | ≈ $0.003–0.007 |

1000 foydalanuvchi, kunlik 30% faol, har biri 3 buyruq ≈ 900 buyruq/kun →
taxminan **oyiga $80–180** (asosan Gemini; uning "fikrlash" tokenlari narxni
o‘zgartiradi). Ochiq manbalarga ko‘ra yangi Flash versiyalari narxi
2027-yil yanvaridan oshadi — narxni AI Studio’da tekshiring. Arzonroq variant:
`GEMINI_MODEL=gemini-flash-lite-latest` (sifati biroz past). ElevenLabs balansini va Google
AI Studio’da byudjet ogohlantirishini qo‘ying. Groq Developer tarifi ochilsa,
matn qismi ~$0.0007/buyruq bo‘ladi — kod o‘zgarmaydi.

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
