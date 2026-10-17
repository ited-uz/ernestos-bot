# Qaydlar (Notes) — nima qurildi va qanday tekshirildi

Sana: 2026-10-17 · Branch: `claude/brave-mayer-78dw7h` · Main’ga merge qilinmagan,
deploy qilinmagan.

## Qisqacha

Pastki menyuda yangi bo‘lim: **Bugun · Reja · Qaydlar · Moliya · Profil**.
Bitta qaydlar to‘plami; Inbox, Hammasi, Daftarlar, Qadalgan, Kundaliklar,
Arxiv, Savat — shu to‘plamning filtrlari (nusxa yo‘q).

| Qism | Fayl |
|---|---|
| Modellar (14 ta yangi jadval, faqat qo‘shiladi) | `db.py` → «Notes» bo‘limi |
| Servis (CRUD, 409, versiyalar, qidiruv, bog‘lanishlar, note→task, AI xotira) | `notes.py` |
| Shablonlar (uz/en/ru) | `notes_templates.py` |
| Markdown/ZIP eksport, Obsidian/Notion import | `notes_portable.py` |
| Google Drive / NotebookLM connector | `notes_connector.py`, `notes_crypto.py` |
| HTTP API (`/api/notes/...`) | `notes_api.py` |
| UI | `webapp/index.html` (Qaydlar bo‘limi), demo/offline: `webapp/preview.js` |
| Sozlash | [NOTES_NOTEBOOKLM_SETUP.md](NOTES_NOTEBOOKLM_SETUP.md) |

## Imkoniyatlar

- **Bosh ekran**: qidiruv, «Yangi qayd», Shablon, Kundalik; statistika (jami,
  bugun, shu oy, seriya — mahalliy sana bo‘yicha); Inbox/Hammasi/Daftarlar;
  qadalganlar; daftarlar galereyasi (rang, belgi, qayd soni). Bo‘sh hisobga
  ErnestOS / University / Idea Bank daftarlari faqat **taklif** sifatida
  ko‘rinadi — bosmaguningizcha yaratilmaydi.
- **Qayd turlari**: Qayd, Tezkor, O‘qish, Fikr, G‘oya, Manba.
  **Shablonlar (13)**: Bo‘sh, Ma’ruza, O‘rganish, Fikr, G‘oya, Manba, Loyiha
  konteksti, Kundalik (sanaga bitta), Haftalik tahlil, Qaror, Inson, AI vosita,
  Lug‘at. Shablon matni interfeys tilida; siz yozgan matn hech qachon
  tarjima qilinmaydi.
- **Muharrir**: Markdown — asosiy format; asboblar paneli (sarlavha, qalin,
  kursiv, ro‘yxat, ☐ vazifa, iqtibos, kod, jadval, ajratgich, bog‘lash,
  fayl, vazifaga aylantirish); «Ko‘rish» rejimi (xavfsiz render, ☐ ni bosib
  belgilash mumkin).
- **Avtomatik saqlash va halol holatlar**: O‘zgarish bor → Saqlanmoqda… →
  Saqlandi; Internet yo‘q · qurilmada; Ziddiyat; Saqlanmadi. Har bir
  harf qurilmada (akkaunt bo‘yicha alohida) saqlanadi va server qabul
  qilmaguncha o‘chmaydi.
- **Ziddiyat (409)**: boshqa qurilmada o‘zgargan qaydga eski versiya asosida
  saqlash rad etiladi; serverdagi va sizning versiyangiz ikkalasi saqlanadi;
  siz tanlaysiz: «Meniki», «Serverdagisi», «Ikkalasi ham».
- **Versiyalar tarixi**: 10 daqiqadagi avtosaqlashlar bitta versiya; oxirgi
  30 tasi; istalganini tiklash.
- **Pin, arxiv, savat, Inbox/ko‘rib chiqilgan** — mustaqil belgilar; teglar;
  bir qayd bir nechta daftarda.
- **Bog‘lanishlar**: `[nom](note:ID)` va `[[Sarlavha]]` havolalari, teskari
  havolalar (backlinks) ID bo‘yicha — nom o‘zgarsa ham buzilmaydi. O‘z
  vazifa / loyiha / maqsadlaringizga bog‘lash. Jamoa elementlariga bog‘lab
  bo‘lmaydi, jamoa a’zolari sizning qaydingizni hech qachon ko‘rmaydi.
- **Qayd → vazifa**: avval ko‘rinish (hech narsa yozilmaydi), keyin bitta
  tranzaksiyada vazifa + bog‘lanish; takroriy bosish ikkinchi vazifa
  yaratmaydi; Free tarif vazifa limiti amal qiladi.
- **Fayllar**: rasm (PNG/JPEG/WebP/GIF), PDF, matn, CSV, audio; ≤5 MB; jami
  Free 25 MB / Pro 250 MB / Max 1 GB; turi baytlar bo‘yicha tekshiriladi;
  SVG qabul qilinmaydi; fayl `sandbox` CSP bilan beriladi.
- **Tezkor kiritish (+)**: Vazifa · **Qayd** · **Pul** · Odat — to‘rt xil
  narsa. Qayd hech qachon vazifa yoki pulga aylantirilmaydi, Inbox’ga tushadi;
  internet bo‘lmasa navbatda kutadi.
- **Eksport**: Markdown ZIP (front matter + `_ernestos/manifest.json` +
  aniq matnlar — yo‘qotishsiz), Obsidian uchun (`[[wikilinks]]`), AI xotira
  `.md`. Telegram ichida 5 daqiqalik bir eksportga cheklangan havola orqali
  yuklanadi (initData URL’ga tushmaydi).
- **Import**: Obsidian vault yoki Notion eksporti ZIP → avval ro‘yxat
  (yangi / yangilanadi / o‘zgarmagan / chiqarib tashlangan), keyin tasdiq.
  `.obsidian`, `.git`, `_system/examples`, `AI/inbox`, `AI/exports`,
  `AI/log.md` olinmaydi. DataviewJS/Templater ishga tushirilmaydi — matn
  bo‘lib qoladi. ZIP traversal, symlink, shifrlangan fayl, ZIP-bomba rad
  etiladi. Qayta import dublikat yaratmaydi (ErnestOS ID → manba ID/yo‘l →
  bir xil matn).
- **AI xotira**: `/api/notes/ai/context` — faqat sizning qaydlaringizdan
  iqtiboslar (`[note:ID]`); savatdagi va «AI’dan yashirin» qaydlar kirmaydi;
  pul va jamoa ma’lumotlari qaydlar orqali berilmaydi. AI suhbatga ham shu
  iqtiboslar «ishonchsiz ma’lumot» sifatida beriladi: qayd ichidagi buyruq
  bajarilmaydi va undan amal (command) yasalmaydi.
- **NotebookLM**: [NOTES_NOTEBOOKLM_SETUP.md](NOTES_NOTEBOOKLM_SETUP.md).
- **Kvota/XP**: qayd yozish, saqlash, import — bepul amal, kvota, XP yoki
  referal hisobiga kirmaydi.
- **Hisob**: JSON eksportda qaydlar bor; «ma’lumotni tozalash» va «hisobni
  o‘chirish» barcha 14 jadvalni tozalaydi va Google ruxsatini bekor qiladi;
  qurilmadagi saqlanmagan qaydlar ham o‘chiriladi.
- **Moslashuv**: 360 px telefon, planshet (2 ustun), desktop (yon panel +
  muharrir yonida bog‘lanishlar paneli); yorug‘ va qorong‘i rejim.
- **Demo va oflayn nusxa**: Qaydlar brauzerda ishlaydi va zaxira faylga
  kiradi; fayl, import va NotebookLM u yerda yo‘q (server kerak) — ilova shuni
  aytadi.

## Qabul testlari (1–10)

«Fixture» — haqiqiy kod, haqiqiy server va brauzer, lekin Google o‘rniga
Google hujjatlashtirgan javoblarni beruvchi test-transport. «Jonli» — haqiqiy
Google bilan.

| № | Nima tekshiriladi | Qayerda | Natija |
|---|---|---|---|
| 1 | Yangi qayd o‘zi saqlanadi, holatlar to‘g‘ri; qurilmada qoralama qolmaydi; saqlash amal/XP/kvota sarflamaydi | `test_notes.py` (saving_notes_spends…, content_needs_a_revision…), `e2e/notes.cjs` 2-qadam | ✅ fixture |
| 2 | Eski versiya ustiga saqlash → 409, ikkala versiya saqlanadi, UI’da tanlov | `test_a_save_on_an_old_revision…`, E2E «conflict» | ✅ |
| 3 | Internet yo‘q → qurilmada, qaytganda yuboriladi; tezkor qayd navbatda; 409 tashlab yuborilmaydi | E2E «Offline», `flushQueue` (409 → «kutmoqda») | ✅ |
| 4 | Bitta to‘plam, filtrlar, daftarlar (ko‘p-ko‘p), %/_ harfma-harf qidiruv, kursorli sahifalash, kirill | `test_a_note_is_created…`, `test_search_matches…`, `test_unicode…`, E2E | ✅ |
| 5 | Shablonlar til bo‘yicha, Kundalik sanaga bitta, statistika va seriya, taklif daftarlar o‘zi yaratilmaydi | `test_templates_follow…`, `test_the_streak…`, E2E | ✅ |
| 6 | Havola/backlink ID bo‘yicha; bog‘lanish faqat o‘z elementlariga; qayd→vazifa: ko‘rinish, idempotent, limit, tranzaksiya | `test_links…`, `test_relations…`, `test_note_to_task…` (2 ta), E2E | ✅ |
| 7 | Fayllar (turi, hajmi, kvota, xavfsiz berish, boshqa akkauntga 404); Markdown’da HTML/`javascript:` ishlamaydi | `test_attachments…`, `test_attachment_storage…`, E2E «HTML and javascript…» | ✅ |
| 8 | Eksport→import bayt-ma-bayt, qayta import no-op; Obsidian (istisnolar, Dataview matn, rasm, wikilink), Notion (sahifa+CSV); traversal/bomba/symlink | `test_export_and_import…`, `test_obsidian…`, `test_notion…`, `test_paths…`, `test_a_zip_bomb…`, E2E import | ✅ |
| 9 | Izolyatsiya (8 ta yo‘l bilan begona qayd → 404), AI xotira qoidalari, tozalash/o‘chirish/eksport | `test_another_account…`, `test_ai_context…`, `test_the_assistant…`, `test_wipe_and_delete…`, `test_the_account_export…` | ✅ |
| 10 | NotebookLM connector: drive.file + PKCE, bir martalik state, shifrlangan token, tasdiqlash, joyida yangilash, savat/istisno tozalash, bo‘laklash, retry/backoff, lease, revoke, statuslar | `test_notes_connector.py` (18 ta) | ✅ **fixture**; ⛔ **jonli — BLOCKED** (kalitlar yo‘q) |

Raqamlar (2026-10-17, shu branch):

- Python (SQLite): **1218 o‘tdi**, 0 xato. Shundan Notes: 51 ta
  (`test_notes.py` 33, `test_notes_connector.py` 18).
- Python (PostgreSQL 16, lokal): to‘liq to‘plam **1208 o‘tdi + 8 skip**
  (faqat SQLite sxemasi uchun yozilganlar); keyin qo‘shilgan 2 test bilan
  Notes’ning 51 testi PostgreSQL’da alohida **51/51**.
- Brauzer (headless Chromium, haqiqiy server): Notes E2E **22/22**, asosiy
  E2E 11/11, ilova qobig‘i 29/29, oflayn nusxa ✅ (Notes bilan); frontend
  unit testlari ✅; pyflakes ✅.
- Jonli Google/NotebookLM: **o‘tkazilmagan** —
  `scripts/notebooklm_live_check.py` → `BLOCKED: missing settings`.

## Cheklovlar (halol ro‘yxat)

1. Google Drive va NotebookLM bilan **jonli** tekshiruv yo‘q. Kod Google’ning
   hujjatlashtirilgan endpointlariga yozilgan, test-transport bilan
   tekshirilgan — lekin haqiqiy Google javobi farq qilsa, faqat jonli
   tekshiruv ko‘rsatadi.
2. Consumer NotebookLM’da API yo‘q: manba qo‘shish va «Sync with Google
   Drive» — foydalanuvchi qo‘lda. «Tasdiqlangan» = NotebookLM hujjatdagi kodni
   aytib berdi (foydalanuvchi javobni qo‘ygan).
3. Markdown → Google Docs formatlash: Drive `text/markdown` importini
   qo‘llasa — formatli, aks holda oddiy matn (kod buni Drive’dan so‘rab
   aniqlaydi).
4. Fayllar PostgreSQL’da saqlanadi (alohida obyekt xotira yo‘q). Ko‘p
   foydalanuvchida S3/R2 ga ko‘chirish kerak bo‘ladi.
5. Qidiruv — `LIKE` (to‘liq matnli indeks emas). Bir odamda minglab qayd
   uchun yetarli; o‘sishda PostgreSQL full-text qo‘shiladi.
6. Real vaqtli birgalikda tahrir yo‘q: ziddiyatda siz tanlaysiz.
7. Planshet/desktop ko‘rinishi headless Chromium skrinshotlarida tekshirildi;
   haqiqiy telefon/planshetda emas.
8. AI suhbatga qayd iqtiboslari qo‘shildi, lekin haqiqiy AI modeli bilan
   (Groq kaliti yo‘q) tekshirilmagan — faqat kontekst tarkibi testlangan.
