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

## Xabarlar (bildirishnomalar)

Bot o'zi yuboradigan har bir xabar — ertalabki/kechki hisobot, vazifa va
odat eslatmalari, kechiktirilgan eslatma, taymer tugashi, jamoa yangiliklari —
ilovasi bor akkauntga ham boradi:

- **Xabarlar qutisi** — bosh sahifadagi 🔔 (o'qilmaganlar soni bilan).
  Xabardagi tugmalar ham ishlaydi: 15 / 60 / 180 daqiqaga kechiktirish va
  "Bajarildi" (faqat belgilaydi, qayta bosish bekor qilmaydi). Firebase'siz
  ham ishlaydi. 60 kundan eski xabarlar o'chadi.
- **Push** — telefon yopiq bo'lsa ham keladi. Sokin soatlarda ovozsiz kanalga
  tushadi. Firebase kerak (bir martalik, bepul):
  1. https://console.firebase.google.com → Add project → nom: ErnestOS.
  2. Android ilova qo'shing: package name **uz.ernestos.app** →
     `google-services.json` ni yuklab oling.
  3. GitHub → Settings → Secrets and variables → Actions → **Secrets** →
     `GOOGLE_SERVICES_JSON` = fayl matni (to'liq JSON).
  4. Firebase → Project settings → Service accounts → Generate new private key →
     Railway → Variables → `FCM_SERVICE_ACCOUNT` = shu JSON fayl matni.
  5. android workflow'ni qayta ishga tushiring va yangi APK'ni o'rnating.

Telegram bot ishlashda davom etadi: xabar ikkala joyga ham boradi. Botni
bloklagan, lekin ilovadan foydalanayotgan odamga xabar faqat ilovaga keladi.

## Eksport

Sozlamalar → "Ma'lumotlarimni export qilish" (JSON) va Statistika → yuklab
olish (CSV) ilovada faylni telefonning o'zida yaratadi va ulashish oynasini
ochadi: Fayllar, Google Drive, Telegram, e-mail — qayerga saqlashni o'zingiz
tanlaysiz. Bot chatiga hech narsa yuborilmaydi.

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
    bash mobile/scripts/make-brand.sh          # logo: ikonka, splash, kirish sahifasi (ImageMagick kerak)
    scripts/e2e_app.sh                         # brauzerda ilova qatlami sinovi

## Sinov sahifasi (telefonsiz)

    node mobile/scripts/build-demo.mjs ernestos-ilova.html uz

Bitta HTML fayl: ilovaning o'zi (`webapp/index.html`), ilova rejimida, namuna
ma'lumotlar bilan (`webapp/preview.js`). Serverga ulanmaydi, hech narsa
saqlanmaydi — dizayn va oqimni ko'rib chiqish uchun.

## iOS

Xuddi shu `mobile/` loyihasi: `npx cap add ios` (macOS + Xcode kerak).
`native.js` va `login.html` ikkala platformada bir xil ishlaydi.
