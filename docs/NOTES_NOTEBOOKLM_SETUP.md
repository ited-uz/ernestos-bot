# Qaydlar → Google Drive → NotebookLM: sozlash

Bu hujjat NotebookLM ulanishini **haqiqiy** ishga tushirish uchun. Kalitlarsiz
ilova buni halol aytadi («Server sozlanmagan — yetishmayapti: …») va hech
narsani soxtalashtirmaydi.

## Nima qilinadi va nima qilinmaydi

| | Holat |
|---|---|
| Google Drive’ga ulanish (OAuth 2.0 + PKCE, faqat `drive.file`) | Kodda bor, test-transport bilan tekshirilgan. **Jonli Google bilan tekshirilmagan** — kalitlar yo‘q |
| Tanlangan qaydlar → Google Docs «source pack» (manifest bilan, ≤350 000 belgi, keyin bir nechta qism) | Bor |
| Hujjat **o‘sha fayl ID’sida** yangilanadi (NotebookLM manbasi o‘sha hujjatga qaraydi) | Bor |
| Savatga tashlangan yoki «AI’dan yashirin» qayd hujjatdan avtomatik olib tashlanadi | Bor |
| NotebookLM’ga manba qo‘shish | **Qo‘lda** (consumer NotebookLM’da ochiq API yo‘q). Ilova qadam-baqadam ko‘rsatadi |
| «Tasdiqlangan» holati | Siz NotebookLM’dan hujjatdagi tekshiruv kodini so‘raysiz va javobni ilovaga qo‘yasiz; kod mos kelsa — tasdiqlandi |
| NotebookLM Enterprise | Google Cloud’da notebook/source API’lari **Preview** holatida, NotebookLM Enterprise litsenziyasi va GCP loyihasi kerak. Bu ulanishda **ishlatilmaydi** |

Hech qachon: hamma uchun bitta asoschi akkaunti, service-account orqali
foydalanuvchi nomidan ishlash, ommaviy (public) havola, NotebookLM’ni
skreyping qilish, brauzer cookie’lari, hujjatlashtirilmagan endpointlar.

## 1. Google Cloud (bir marta, ~20 daqiqa)

1. <https://console.cloud.google.com> → loyiha (Google bilan kirish uchun
   ochilgan loyihaning o‘zi bo‘lishi mumkin).
2. **APIs & Services → Library → Google Drive API → Enable.**
3. **OAuth consent screen**: User type — External; app nomi «ErnestOS»;
   Scopes → **faqat** `https://www.googleapis.com/auth/drive.file`
   (bu «non-sensitive» scope — Google’ning maxsus tekshiruvisiz ishlaydi).
   Testing holatida bo‘lsa, test foydalanuvchilarni **Test users** ga qo‘shing.
4. **Credentials → Create credentials → OAuth client ID → Web application.**
   *Authorized redirect URIs* ga aynan shuni yozing:

   ```
   https://<SIZNING-SERVER>/api/notes/connector/google/callback
   ```

   (`<SIZNING-SERVER>` — `PUBLIC_URL` bilan bir xil.)
5. Client ID va Client secret’ni nusxalang.

## 2. Railway → Variables

| O‘zgaruvchi | Qiymat (namuna, haqiqiysini qo‘ying) |
|---|---|
| `GOOGLE_DRIVE_CLIENT_ID` | `1234567890-abc.apps.googleusercontent.com` |
| `GOOGLE_DRIVE_CLIENT_SECRET` | `GOCSPX-…` |
| `PUBLIC_URL` | `https://ernestos.up.railway.app` |
| `CONNECTOR_TOKEN_KEY` | 32 bayt tasodifiy kalit (pastdagi buyruq) |
| `CONNECTOR_TOKEN_KEY_OLD` | faqat kalitni almashtirayotganda — eski kalit |

`GOOGLE_DRIVE_CLIENT_ID/SECRET` bo‘lmasa, Google bilan kirish uchun ochilgan
`GOOGLE_CLIENT_ID/SECRET` ishlatiladi (o‘sha klientga yuqoridagi redirect
URI’ni qo‘shish kifoya).

Kalit yaratish (o‘z kompyuteringizda):

```
python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Kalitni hech qayerga (chat, repo, log) yozmang — faqat Railway Variables’ga.
Kalit yo‘qolsa, saqlangan tokenlarni ochib bo‘lmaydi: foydalanuvchilar Drive’ni
qayta ulaydi, boshqa hech narsa yo‘qolmaydi.

## 3. Xavfsizlik — qanday qurilgan

- **Scope**: faqat `drive.file` — ErnestOS foydalanuvchi Drive’idagi faqat
  o‘zi yaratgan fayllarni ko‘radi.
- **PKCE (S256)** + bir martalik `state` (serverda faqat SHA-256 xeshi, 10
  daqiqa yashaydi). PKCE verifier serverda shifrlangan holda turadi.
- **Telegram initData hech qachon URL’ga tushmaydi**: Mini App signed header
  bilan `/start` so‘raydi, server Google sahifasi havolasini qaytaradi (unda
  faqat tasodifiy `state` bor), havola telefonning haqiqiy brauzerida ochiladi.
- **Tasdiqlash**: Google’dan qaytgach ulanish `pending_confirm` holatida
  turadi. Ilovada «shu Google akkaunt sizniki» deb tasdiqlamaguningizcha hech
  narsa yuborilmaydi (begona havola orqali boshqa akkaunt ulanib qolmasin).
- **Refresh token** AES-256-GCM bilan shifrlangan (`CONNECTOR_TOKEN_KEY`),
  hech qachon API javobida, URL’da, log’da, eksportda ko‘rinmaydi.
- **Fon ishlari** (`connector_jobs`): lease bilan olinadi, worker o‘lsa lease
  tugaydi va ish boshqasiga o‘tadi; xatoda 30 s → 1 soatgacha backoff, 6
  urinish. Google’ga so‘rov vaqtida DB tranzaksiyasi ochiq turmaydi.
- **Uzish**: Google’da ruxsat bekor qilinadi (`/revoke`), token o‘chiriladi;
  hujjatlarni Drive’da qoldirish yoki Drive savatiga tashlashni siz tanlaysiz.
  Revoke 6 marta o‘xshamasa — token baribir o‘chiriladi va
  myaccount.google.com/permissions orqali o‘chirish kerakligi log’ga yoziladi.
- **Hisobni o‘chirish / ma’lumotni tozalash**: barcha Notes va connector
  qatorlari o‘chadi, so‘ng (tranzaksiyadan tashqarida) Google’dagi ruxsat
  bekor qilinadi. Drive’dagi hujjatlar foydalanuvchining o‘z fayllari bo‘lib
  qoladi.

## 4. Foydalanuvchi uchun qadamlar (ilovada)

1. Qaydlar → NotebookLM → **Google Drive’ni ulash** → brauzerda Google
   ruxsati → ilovaga qayting → «Ha, ulash».
2. **Daftarni yuborish** (yoki qayd menyusidan «NotebookLM’ga yuborish»).
   Bir necha soniyada «eksport qilingan» bo‘ladi va 📄 hujjat havolasi chiqadi.
3. NotebookLM’da daftar oching → *Add source* → *Google Docs* →
   «ErnestOS · …» → NotebookLM daftari havolasini ilovaga qo‘ying
   (holat: **kutilmoqda**).
4. NotebookLM’dan so‘rang: «What is the ErnestOS verification code?» →
   javobni ilovaga qo‘ying → holat: **tasdiqlangan**.
5. Qaydlar o‘zgarsa — «Yangilash». NotebookLM’da manbani ochib
   **Sync with Google Drive** ni bosing (NotebookLM Google Docs manbalarini
   o‘zi avtomatik yangilamaydi).

## 5. Jonli tekshiruv (kalitlar qo‘yilgandan keyin)

Faqat **test akkaunt** va alohida test bazasi bilan:

```
python scripts/notebooklm_live_check.py --workspace <test workspace id> --test-account
```

U sintetik qayd yaratadi, Google Doc’ga eksport qiladi, Drive’dan o‘qib
tekshiradi, tahrirni o‘sha fayl ID’sida tekshiradi, savatga tashlangan qayd
hujjatdan chiqqanini tekshiradi, oxirida hujjatni Drive savatiga tashlaydi.
NotebookLM qismini (manba qo‘shish, kodni so‘rash) qo‘lda bajarasiz — skript
kodni ko‘rsatadi.

Hozirgi holat (2026-10-17): skript `BLOCKED: missing settings` qaytaradi —
jonli tekshiruv **o‘tkazilmagan**.
