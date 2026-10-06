# ErnestOS — Android va iOS ilova

Ilova — Mini App'ning o'zi, Capacitor qobig'ida (`mobile/`). Dizayn, ekranlar
va mantiq bitta: `webapp/index.html` o'zgarsa, keyingi build'da ilova ham
o'zgaradi. Ma'lumotlar bot bilan bir xil server va bir xil akkauntda.

## Kirish

1. Ilova ochiladi → "Telegram'da kod olish".
2. Bot 8 belgili kod yuboradi (`/app` buyrug'i yoki `?start=app` havolasi).
3. Kod ilovaga yoziladi → server 90 kunlik sessiya beradi (`app_auth.py`).

Kod 10 daqiqa va bir marta ishlaydi; yangisi eskisini bekor qiladi. Bazada
faqat SHA-256 izi saqlanadi. Sessiya Sozlamalar → "Ilovadan chiqish" yoki
akkauntni o'chirish bilan tugaydi. Server ilova tokenini Mini App'ning
initData sarlavhasida qabul qiladi, shuning uchun barcha endpointlar,
limitlar va gate o'zgarishsiz ishlaydi.

## APK olish (sinov uchun)

1. GitHub → Settings → Secrets and variables → Actions → **Variables** →
   `ERNEST_API_URL` = serveringiz manzili (masalan `https://….up.railway.app`).
2. Actions → **android** → Run workflow (yoki `mobile/`/`webapp/` ga push).
3. Tayyor bo'lgach, run sahifasidagi **ErnestOS-android-N** faylini yuklab,
   telefonda oching (noma'lum manbalardan o'rnatishga ruxsat bering).

Serverda `APP_ORIGINS` sozlanmasa, standart qiymat ishlaydi
(`https://localhost,capacitor://localhost,http://localhost`).

## Mahalliy ishlar

    cd mobile && npm install
    ERNEST_API_URL=https://… npm run sync     # www/ ni yig'ib, android/ ga ko'chiradi
    python mobile/scripts/make-icons.py        # ikonka va splash (Pillow kerak)
    scripts/e2e_app.sh                         # brauzerda ilova qatlami sinovi

## iOS

Xuddi shu `mobile/` loyihasi: `npx cap add ios` (macOS + Xcode kerak).
`native.js` va `login.html` ikkala platformada bir xil ishlaydi.
