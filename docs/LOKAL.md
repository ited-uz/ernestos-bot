# ErnestOS'ni o‘z kompyuteringizda ishga tushirish

Domen ham, hosting ham kerak emas. Server sizning kompyuteringizda ishlaydi,
ilova brauzerda ochiladi (kompyuterda ham, bir xil Wi-Fi'dagi telefonda ham).

## 1. O‘rnatish — `Documents\PROJECTS\ernestOS web`

1. **`ernestOS-web.zip`** ni oching (o‘ng tugma → «Barchasini chiqarish»).
   ZIP’ni o‘zingiz yig‘ish: `python scripts/package_local.py`
   (faqat Mac uchun, Windows fayllarisiz: `--mac` → `ernestOS-web-mac.zip`).
2. **Windows:** `ornatish-windows.bat` ni ikki marta bosing. U:
   - dasturni **`Documents\PROJECTS\ernestOS web`** ga ko‘chiradi
     (OneDrive’dagi Documents ham hisobga olinadi);
   - Python 3.11+ ni topadi, yo‘q bo‘lsa `winget` bilan o‘rnatadi
     (winget bo‘lmasa python.org ochiladi — «Add python.exe to PATH»);
   - ish stoliga **«ErnestOS»** yorlig‘ini qo‘yadi;
   - serverni ishga tushiradi va brauzerni ochadi.
3. **macOS:** `ornatish-mac.command` → o‘ng tugma → **Open** (birinchi marta
   Gatekeeper so‘raydi). Mac ochishga ruxsat bermasa: **Terminal**ni oching,
   `bash ` deb yozing (oxirida bo‘sh joy), faylni Terminal oynasiga sudrab
   tashlang → **Enter**. `~/Documents/PROJECTS/ernestOS web` ga o‘rnatadi.
   Python 3.11+ yo‘q bo‘lsa (Mac’da odatda 3.9 turadi), o‘zi o‘rnatadi:
   Homebrew bo‘lsa `brew` bilan, bo‘lmasa python.org o‘rnatuvchisi ochiladi
   (Continue → Install → Close), keyin o‘rnatish o‘zi davom etadi.

Yangi versiyani o‘rnatish ham xuddi shunday: yangi ZIP → o‘rnatuvchi.
Ma’lumotlar (`data/`), sozlamalar (`.env.local`) va `.venv` **ustidan
yozilmaydi**.

## 2. Keyingi safar

| Tizim | Nima qilinadi |
|---|---|
| **Windows** | Ish stolidagi **ErnestOS** yorlig‘i (yoki papkadagi `start-local.bat`) |
| **macOS** | Ish stolidagi **ErnestOS** (`ErnestOS.command`) — ikki marta bosing |
| **Linux** | Papkada `./start-local.sh` (yoki `python3 run_local.py`) |

Birinchi marta 2–5 daqiqa ketadi (kutubxonalar o‘rnatiladi). Keyin har safar
bir necha soniyada ochiladi. Brauzer o‘zi ochiladi:
**<http://localhost:8000/app/>**

Oynani yopmang — server shu oynada ishlaydi. To‘xtatish: **Ctrl+C**.

## 3. Kirish

1. Email manzilingizni yozing → **Kod yuborish**.
2. Lokal rejimda xat yuborilmaydi — kod shu kompyuterdagi **pochta qutisiga**
   tushadi. Kirish sahifasidagi «📬 Lokal rejim: kodni … ko‘ring» havolasini
   bosing yoki oching: <http://localhost:8000/app/dev/mailbox>
3. 6 xonali kodni yozing → ism, tanlovlar → tayyor.

Pochta qutisi sahifasi **faqat shu kompyuterdan** ochiladi: boshqa
qurilmadan, proksi orqali yoki `ENVIRONMENT=local` bo‘lmasa — u yo‘q (404).

## 4. Telefondan ochish (bir xil Wi-Fi)

```
python run_local.py --lan
```

Oynada `Telefondan: http://192.168.x.x:8000/app/` chiqadi — shu manzilni
telefon brauzerida oching. Kodni kompyuterdagi pochta qutisidan oling.
Cheklov: oddiy `http` orqali telefonda **ovoz (mikrofon)** va **o‘rnatish
(PWA)** ishlamaydi — buni brauzer faqat `https`da beradi. Qolgan hamma narsa
ishlaydi. Domen va hosting ulangach (https) bu cheklov yo‘qoladi.

## 5. Ma’lumotlar qayerda

| Nima | Qayerda |
|---|---|
| Barcha ma’lumotlar (SQLite) | `data/ernestos.db` |
| Kirish kodlari | `data/mail/` |
| Sozlamalar | `.env.local` |

**Zaxira nusxa:** serverni to‘xtatib, `data/ernestos.db` faylini nusxalang.
Ilova ichidan ham: Profil → Sozlamalar → **Ma'lumotlarimni export qilish** (JSON fayl yuklanadi).
`data/` va `.env.local` Git’ga tushmaydi.

## 6. Lokal rejimda nima ishlaydi

| Imkoniyat | Lokal | Izoh |
|---|---|---|
| Vazifa, odat, namoz, kundalik, maqsad, guruh, moliya, qarz, qaydlar, statistika, Qadam | ✅ | To‘liq |
| Email bilan kirish / ro‘yxatdan o‘tish | ✅ | Kod — lokal pochta qutisida |
| Eslatmalar, ertalabki/kechki hisobot, taymer | ✅ | Ilova ichidagi **Xabarlar**ga (🔔) keladi |
| Kompyuter (yon panel) va telefon (pastki panel) ko‘rinishi, yorug‘/qorong‘i | ✅ | |
| Internet yo‘q paytda ochilish va keyin yuborish | ✅ | `localhost`da |
| Eksport (JSON, CSV, Markdown ZIP), Obsidian/Notion import | ✅ | |
| Telegram bot va bot kodi bilan kirish | ⚙️ | `.env.local`ga **test** bot `BOT_TOKEN` qo‘ying |
| Ovoz va AI yordamchi | ⚙️ | `GROQ_API_KEY`, `AGENT_ENABLED=true` va FFmpeg |
| Google bilan kirish | ⚙️ | Google OAuth «Web application», redirect: `http://localhost:8000/api/auth/oauth/google/callback` |
| Haqiqiy email (Gmail SMTP) | ⚙️ | `.env.local`dagi SMTP qatorlari |
| Telegram Stars to‘lovi, Apple bilan kirish, NotebookLM, telefon push | ⛔ | Bot, https domen yoki Google kalitlari kerak — hosting bosqichida |

⚙️ — `.env.local` faylida kalit qo‘yilsa ishlaydi (izohlar faylning o‘zida).
Kalit qo‘shgach serverni qayta ishga tushiring.

**Muhim:** lokal `BOT_TOKEN` sifatida serverdagi (production) botning
tokenini bermang — bitta token bir vaqtda ikki joyda ishlay olmaydi.
@BotFather’dan alohida test bot oching.

## 7. Muammo bo‘lsa

| Belgi | Yechim |
|---|---|
| `localhost refused to connect` (ERR_CONNECTION_REFUSED) | Server shu kompyuterda ishlamayapti: o‘rnatuvchini (yoki yorliqni) ishga tushiring, qora oynada `✅ ErnestOS ishlayapti` chiqishini kuting, oynani yopmang. Manzil — oynada yozilgani (odatda `http://localhost:8000/app/`) |
| `Python 3.11 yoki yangisi kerak` | Python’ni yangilang, oynani qayta oching |
| `'python' is not recognized` (Windows) | Python’ni «Add to PATH» bilan qayta o‘rnating |
| Port band | O‘zi keyingi bo‘sh portni oladi (8001, 8002…) — oynadagi manzilni oching |
| Kutubxona o‘rnatilmadi | Internetni tekshiring, `start-local.bat`ni qayta ishga tushiring |
| Hammasini boshidan | Serverni to‘xtating, `data/` va `.env.local`ni o‘chiring (ma’lumot ham o‘chadi!) |

## 8. Keyin: domen va hosting

Lokal versiya bilan serverdagi versiya bir xil kod. Hostingga o‘tganda:
`ENVIRONMENT=production`, PostgreSQL (`DATABASE_URL`), haqiqiy email
(SMTP/Resend), `PUBLIC_URL=https://sizning-domen`, bot tokeni —
[ISHGA_TUSHIRISH.md](ISHGA_TUSHIRISH.md). `ENVIRONMENT=local` serverda ishga
tushmaydi (atayin: pochta qutisi sahifasi faqat shaxsiy kompyuter uchun).
