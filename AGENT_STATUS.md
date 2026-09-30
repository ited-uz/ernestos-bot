# v12 agent — holat

2026-09-30. v11 agentidan soddalashtirildi (KISS).

## Bor

- Telegram bot: ovoz yoki matn → Groq Whisper-large-v3 (profil tilida) →
  Groq `openai/gpt-oss-120b` → taklif → Tasdiqlash / Tahrirlash / Bekor qilish.
- Vazifa, odat, loyiha, pul: yaratish, o‘zgartirish, o‘chirish. Shaxsiy va jamoa.
- Faqat profil tili (uz/ru/en) qabul qilinadi; javoblar ham shu tilda.
- Himoyalar: takroriy tasdiq bitta yozuv, eski tugma ishlamaydi, keyin o‘zgargan
  yozuv ko‘r-ko‘rona o‘zgartirilmaydi, jamoa rollari, bitta tranzaksiya.
- Groq Free uchun tejamkor: bir buyruq ~2 300 token (~87 buyruq/kun butun bot).

## Olib tashlandi (v11 dan)

Mini App mikrofoni va agent API, Inbox (`/inbox`), bajarildi/qayta ochish,
oylik byudjet limiti, Gemini va boshqa provayderlar, «Inbox’da qoldirish»,
«Qayta tahlil».

## Tekshirilgan

- Python offline testlari: **930 o‘tdi**. AI javoblari testda almashtirilgan —
  bu o‘zbekcha nutq aniqligining isboti emas.

## Hali tekshirilmagan

- Haqiqiy Groq kaliti bilan sizning ovozingizda aniqlik.
- Railway deploy, jonli Telegram, FFmpeg bilan haqiqiy dekodlash.
