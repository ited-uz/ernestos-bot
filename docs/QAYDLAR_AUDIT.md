# Qaydlar — 10 foydalanuvchi bilan audit

Sana: 2026-10-17 · Branch: `claude/brave-mayer-78dw7h` · Main’ga merge
qilinmagan, deploy qilinmagan.

Usul: lokal server (`uvicorn`, alohida SQLite fayl), haqiqiy HTTP so‘rovlar,
headless Chromium. 10 ta **sintetik** foydalanuvchi (ID 880001–880010), har
birida 10–15 ta namunaviy qayd. Production bazasi, asoschining shaxsiy
qaydlari yoki haqiqiy Google hisobi ishlatilmagan.

Har bir topilma qayta takrorlangan, tuzatilgan va testga aylantirilgan
(`tests/test_notes.py`, `tests/e2e/notes.cjs`) — bu testlar eski kodda
yiqiladi.

## Natija bir qarashda

| Nima | Natija |
|---|---|
| 10 persona, 115 ta namunaviy qayd (21 tasi Obsidian/Notion arxividan import) | **125 tekshiruv, 0 topilma** (tuzatishlardan keyin, toza bazada) |
| To‘ldirilgan akkauntlar UI’si: 360 px, yorug‘ + qorong‘i | **738 tekshiruv, 0 muammo** |
| Topilgan va tuzatilgan xatolar | **8 ta** (1 ta P1, 4 ta P2, 3 ta P3) |
| Jonli Google Drive / NotebookLM | ⛔ **BLOCKED** — kalitlar yo‘q (P10 faqat fixture) |

## 10 persona va ularning namunaviy qaydlari

| # | Kim | Namunaviy qaydlar | Nima tekshirildi | Natija |
|---|---|---|---|---|
| P1 | **Dilnoza**, talaba (uz) | «Ma’ruza — Marketing asoslari 1», «Ma’ruza — Mikroiqtisodiyot 2: Talab va taklif», «O‘rganish — Excel VLOOKUP», «Lug‘at — Business English», «Imtihon 25-oktabrda» (tezkor), «Nega marketing muhim» (fikr), «Coursera — Marketing» (manba), «Uy vazifasi — 3-hafta», «Talab va taklif — xulosa» (`note:ID` havola), Kundalik, Haftalik tahlil — **11 ta** | Taklif daftarlar o‘zi yaratilmaydi; o‘zbekcha shablonlar; Kundalik sanaga bitta; bir qayd ikki daftarda (University 6, Marketing 2); teg filtri; qidiruv; `%` harfma-harf («20%» ni topadi, hammasini emas); `’` belgili `[[Sarlavha]]`; backlink; statistika (11/11/11, seriya 1); AI iqtiboslar; eksport 11 ta `.md` va papkalar | ✅ 16/16 |
| P2 | **Ernest**, asoschi (en) | «Project context — ErnestOS Notes v1», «Decision — Pro at $9/mo», 5 ta g‘oya («AI sales agent for Uzbek SMBs», «Dropshipping product research bot», «Telegram CRM for bazaar sellers», «Course: AI for students», «Halal investment tracker»), «AI tool — NotebookLM», «Akmal — distributor», «Idea map», «Meeting notes — investor call» — **11 ta**; maqsad «1M high-quality clients», loyiha | Loyiha va maqsadga bog‘lash, loyiha tomonida ko‘rinishi; qayd → vazifa: tanlangan qatordan ko‘rinish («10 calls this week»), ikki marta bosish = bitta vazifa, vazifa loyihaga tushadi; arxiv/savat/tiklash; qadalganlar; «Idea map» → 5 havola va har g‘oyada backlink | ✅ 13/13 |
| P3 | **Olga**, sotuv (ru) | «ООО «Ташкент Строй»», «ИП Каримов», «Магазин «Орзу»», «Холодный звонок — 12 номеров», «Возражение: дорого», «Скрипт первого звонка», «Итоги недели — продажи», «Конкурент: «Мега Опт»», «Идея: программа лояльности», «Встреча с Ириной», sarlavhasiz «Позвонить Ирине завтра в 10» — **11 ta** | Ruscha shablon («## Кто»); sarlavhasiz tezkor qayd; kirill teg filtri (4 ta); kirill qidiruv katta-kichik harfga sezgir emas; teglar buzilmaydi; arxiv | ✅ 7/7 |
| P4 | **Jasur**, treyder (uz) | Bugungi Kundalik + oldingi 4 kunning Kundaliklari, «Savdo qoidalari», «TradingView — BTC tahlil», «Binance Academy — risk», «Shaxsiy kundalik — xatolar» (AI’dan yashirin), «Saboq: kutish ham pozitsiya» — **11 ta** | Kundalik shabloni; `javascript:` manba rad; seriya 5 kun (Toshkent sanasi bo‘yicha); «bugun 7, jami 11»; yashirin qayd AI kontekstiga ham, AI eksportiga ham tushmaydi; «Kundaliklar» faqat haqiqiy kundaliklar | ✅ 7/7 |
| P5 | **Free tarif** (uz) | «Do‘kon g‘oyasi», «Kitob: Atomic Habits», «Xarid ro‘yxati» (☐), «Telefon raqamlar», «Oylik reja», «Retsept: palov», «Film ro‘yxati», «Sayohat: Samarqand», «Ingliz tili so‘zlari» (jadval), «Fikr», «Sport rejasi», «Kitob: Deep Work» — **12 ta** + 15 avtosaqlash + 6 fayl | 12 qayd va 15 avtosaqlash amal/XP sarflamaydi (0→0); vazifa limiti tugaganda qayd → vazifa 402, yarim bog‘lanish qolmaydi; fayllar 25 MB da to‘xtaydi (6-fayl `storage_full`) | ✅ 6/6 |
| P6 | **Obi**, Obsidian’dan ko‘chuvchi (en) | Vault: «Lecture 1» (front matter, alias), «Lecture 2», «Positioning» (DataviewJS + Templater), ikkinchi «Lecture 1» (bir xil nom), «2026-10-01», «Inbox», «Deep note» (3 papka ichida), «Заметка», «emoji 🚀 plan», «Complex» (murakkab front matter) + rasm — **10 ta** + 7 ta tizim fayli; keyin «After the move» | Manba aniqlanadi; `.obsidian`, `.git`, `_system/examples`, `AI/inbox`, `AI/exports`, `AI/log.md` olinmaydi; ogohlantirishlar; papka → daftar; teglar (front matter + `#inline`); `[[alias]]`, `[[#heading]]`; bir xil nomli ikki qaydda havola taxmin qilinmaydi; `![[rasm]]` → biriktirma; Dataview/Templater matn bo‘lib qoladi; **qayta import 0 yangi / 0 yangilanish**; Obsidian eksport; o‘z eksportini qayta import — dublikat yo‘q | ✅ 14/14 (T1 tuzatilgandan keyin) |
| P7 | **Nodir**, Notion’dan ko‘chuvchi (ru) | Ichma-ich Notion ZIP: «Startup OS», «Roadmap», «Hiring», «Personal», «Books», «Gym», «Travel», «Ideas» + baza (CSV) «Leads»: «Acme» (o‘z sahifasi bor), «Beta», «Gamma» — **11 ta** | ZIP ichidagi ZIP; sahifalar + CSV qatorlari, **Acme bir marta**; Notion papkalari → daftarlar; Notion ID saqlanadi; qayta import 0 yangi | ✅ 6/6 (T2 tuzatilgandan keyin) |
| P8 | **Sevara**, telefon + noutbuk (uz) | «Loyiha qaydi 1 … 10» — **10 ta** + «Loyiha qaydi 1 (nusxa)» + Kundalik | Ikkinchi qurilma eski versiyada → 409, javobda server versiyasi; rad etilgan matn ham saqlanadi; «Ikkalasi ham» nusxa; 20 avtosaqlash → 1 versiya; If-Match/ETag; pin/teg versiyasiz; eski versiyani tiklash ham 409; savatdagi qayd tahrirlanmaydi; savatdagi Kundalik qayta ochilganda qaytadi | ✅ 10/10 |
| P9 | **Mallory**, hujumchi (en) | `<img onerror>`, `<script>`, `[x](javascript:…)`, `<svg onload>`, `data:` havola, `{{7*7}}`, `' OR 1=1 --`, `%_%_% ; DROP TABLE`, RTL override, 300 belgili sarlavha — **10 ta** + 3 ta yordamchi; markup’li teg va daftar nomi; zararli ZIP’lar | Boshqalarning qaydi/daftari/importi: 18 ta yo‘l → hammasi 404, jabrlanuvchi qaydi o‘zgarmaydi; begona loyihaga bog‘lab bo‘lmaydi; teg va daftar belgisi tozalanadi; `%_%` SQL emas, harfma-harf; noto‘g‘ri view/cursor/limit → 422; 200k dan uzun matn 422; 900 KB so‘rov 413; ZIP traversal / absolut yo‘l / ZIP emas / ZIP-bomba rad; soxta va boshqa workspace’ga almashtirilgan yuklash havolasi 404; callback sahifasida skript yo‘q; daftarlar soni chegarasi | ✅ 24/24 |
| P10 | **Lola**, NotebookLM foydalanuvchisi (uz) | «Biznes kurs» daftari: «1-dars: Biznes model» … «10-dars: Yakuniy loyiha», «Savollar ro‘yxati», «Shaxsiy: imtihon qo‘rquvi» (AI’dan yashirin) — **12 ta** | Kalitsiz: «sozlanmagan» va nima yetishmasligi, ulanish 503; Google sahifasi: faqat `drive.file`, PKCE, URL’da initData yo‘q; tasdiqlashgacha hech narsa yuborilmaydi; 11 qayd → 1 Google Doc, yashirin qayd yo‘q, 11 qatorli manifest; soxta NotebookLM havolasi rad; «kutilmoqda» → kod bilan «tasdiqlangan»; o‘zgarish → o‘sha fayl joyida yangilanadi, tasdiq saqlanadi; savatdagi qayd hujjatdan chiqadi; Google 503 → navbat, backoff, qayta urinishda yetadi; uzish → Google’da ruxsat bekor; qayta ulanish → o‘sha hujjat; hisobni o‘chirish → ruxsat bekor, qatorlar 0 | ✅ 22/22 **fixture** (Google o‘rniga test-transport); ⛔ jonli — BLOCKED |

Jami: 43 (P1–P4) + 60 (P5–P9) + 22 (P10) = **125 tekshiruv**.

## Topilmalar va tuzatishlar

| ID | Daraja | Muammo | Isbot | Tuzatish | Test |
|---|---|---|---|---|---|
| T1 | **P1** | Rasmli Obsidian vault qayta import qilinsa, «o‘zgarmagan» o‘rniga «yangilanadi» chiqardi va ilovada qilingan tahrirlar arxivdagi eski matn bilan **ustidan yozilardi** | P6: qayta import → `update` > 0 | `![[rasm]]` importda biriktirma havolasiga aylanadi, shuning uchun matn xeshi arxivdagidan farq qiladi. Endi arxivdagi matn xeshi alohida saqlanadi (`Note.import_hash`): o‘sha arxiv qayta kelsa — o‘zgarmagan, sizning tahrirlaringiz saqlanadi | `test_reimporting_a_vault_with_pictures_changes_nothing_and_keeps_edits` |
| T2 | P2 | Notion bazasidagi o‘z sahifasi bor qator («Acme») ikki marta import qilinardi: sahifa sifatida va CSV qatori sifatida | P7: «Acme» ro‘yxatda 2 marta | Ichma-ich yo‘llarda Notion ID’lari to‘liq tozalanmagan edi. `_notion_clean` butun yo‘lni tozalaydi; CSV qatori uchun daftar = yuqori papka, teg = baza nomi | `test_a_notion_database_row_with_its_own_page_is_imported_once` |
| T3 | P2 | Internet yo‘q paytda Qaydlar ro‘yxati xato sahifasini ko‘rsatardi (oldin yuklangan ro‘yxat bo‘lsa ham) | Brauzer, oflayn → «Xato» | Oxirgi ro‘yxat qoladi, ustida «Internet yo‘q» banneri | E2E «Written offline and left…» |
| T4 | P2 | Oflayn yozilib, keyin yopilgan qayd internet qaytganda **o‘zi** yuborilmasdi — faqat o‘sha qaydni qayta ochganda | Brauzer: oflayn yozish → orqaga → online → serverda eski matn | `ntSyncDrafts()`: ilova ochilganda, internet qaytganda va ilova ekranga qaytganda qurilmadagi barcha qoralamalar yuboriladi; ziddiyatlisi o‘chirilmaydi, «Ziddiyat» bo‘lib qoladi | E2E «Written offline and left…», «Drafts left on a closed app…» |
| T5 | P2 | Muharrir saqlashi va fon sinxroni bir xil kalit bilan bir vaqtda ketsa, qoralama noto‘g‘ri «Ziddiyat» bo‘lib qolardi | E2E vaqti-vaqti bilan | Ochiq muharrirdagi qoralama fon sinxronidan o‘tkaziladi; 409 `in_progress` — vaqtinchalik holat, ziddiyat emas | E2E (yuqoridagi 2 qadam, takroriy ishga tushirish) |
| T6 | P3 | Daftar belgisiga `<i>x</i>` kabi markup yozish mumkin edi (UI escape qiladi — XSS emas, lekin belgi buzilardi) | P9 | `<>"'&` olib tashlanadi | `test_a_notebook_icon_carries_no_markup` |
| T7 | P3 | Har bir import ko‘rinishi (preview, 30 MB gacha) bazada qolardi — takror yuklab bazani to‘ldirish mumkin edi | Kod o‘qish + P6 | Bir akkauntda bitta kutilayotgan import: yangisi eskisini almashtiradi | `test_a_new_import_preview_replaces_the_one_waiting` |
| T8 | P3 | Qayd matniga umumiy chegara yo‘q edi (fayllarda bor edi) — bitta akkaunt bazani cheksiz to‘ldira olardi | Kod o‘qish | Jami matn: Free 20 mln, Pro 200 mln, Max 1 mlrd belgi. Saqlash bepulligicha qoladi; to‘liq uzunlikdagi qayd Free’da ham sig‘adi | `test_an_account_cannot_fill_the_database_with_note_text`, `test_a_full_length_note_fits_on_free` |

Qo‘shimcha (audit davomida yetishmagan joy): **bog‘langan qaydlar endi
vazifa oynasida, maqsad oynasida va shaxsiy loyiha sahifasida ham
ko‘rinadi** (oldin faqat qayd tomonida edi). Qaydni bossangiz — oyna yopilib,
qayd ochiladi. E2E «Note → task» qadami buni tekshiradi.

Halollik uchun: birinchi yugurishda 9 ta «topilma» mening skriptimdagi xato
edi (noto‘g‘ri sanoq, sanani UTC bo‘yicha olish, Toshkent emas va h.k.) —
ilova xatosi emas. Skriptlar tuzatildi va hammasi **toza bazada qaytadan**
ishga tushirildi; yuqoridagi 125/125 — o‘sha yakuniy natija.

## To‘ldirilgan UI (360 px)

9 ta to‘ldirilgan akkaunt (P1–P9; P10 o‘z alohida bazasida ishlaydi) ×
yorug‘ va qorong‘i rejim, 360×780, haqiqiy server va Chromium:

- 7 ta ko‘rinish (Inbox, Hammasi, Daftarlar, Qadalgan, Kundaliklar, Arxiv,
  Savat) + birinchi daftar;
- 4 ta oyna: Shablonlar, Asboblar, NotebookLM, Import;
- har akkauntning 15 tagacha qaydi — tahrir va ko‘rish rejimida;
- birinchi qayd uchun: menyu, tur, daftarlar, teglar, tarix, havola, vazifaga
  aylantirish oynalari.

Har qadamda: JavaScript xatosi, 5xx javob, gorizontal skroll, qayddan
«tirik» bo‘lib chiqqan markup (`img onerror`, `svg onload`, `script`,
`javascript:`/`data:` havola), shunchaki ochganda «Saqlanmadi/Ziddiyat»
belgisi. Natija: **738 tekshiruv, 0 muammo** (Mallory’ning 10 ta zararli
qaydi ham).

## Testlar (shu branch, yakuniy kod)

- Python (SQLite): **1224 o‘tdi**, 0 xato. Notes: 57 (`test_notes.py` 39,
  `test_notes_connector.py` 18).
- Python (PostgreSQL 16, lokal, toza baza): **1216 o‘tdi + 8 skip** (faqat
  SQLite sxemasi uchun yozilganlar), 0 xato.
- Brauzer: Notes E2E **24/24**, asosiy E2E 11/11, ilova qobig‘i 29/29,
  oflayn nusxa ✅; frontend unit testlari ✅ (Toshkent va Nyu-York vaqt
  zonasida); pyflakes ✅.

## Nima haqiqiy, nima fixture

| Qism | Qanday tekshirildi |
|---|---|
| P1–P9, UI crawl | Haqiqiy server + HTTP + Chromium, sintetik ma’lumot, alohida SQLite |
| P10 (NotebookLM) | Haqiqiy kod, lekin Google javoblari test-transportdan (**fixture**) |
| Jonli Google Drive / NotebookLM | ⛔ **BLOCKED**: `GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_CLIENT_SECRET`, `CONNECTOR_TOKEN_KEY`, `PUBLIC_URL` yo‘q. `scripts/notebooklm_live_check.py` → `BLOCKED: missing settings` |
| Haqiqiy telefon | Tekshirilmagan — faqat headless Chromium 360 px |
| Haqiqiy AI modeli | Tekshirilmagan (kalit yo‘q) — faqat AI’ga beriladigan kontekst tarkibi |

Audit skriptlari (personalar va crawl) repo’ga kiritilmagan — ular lokal
yordamchi; topilgan har bir xato esa repo’dagi testga aylantirilgan.
