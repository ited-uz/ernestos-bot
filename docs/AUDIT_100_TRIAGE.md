# «100 ta chuqur muammo» — kodga solishtirilgan tahlil

Ro‘yxat AI tomonidan umumiy shablon asosida yozilgan. Har bir band ErnestOS
kodining o‘zida tekshirildi. Natija:

- **🔧 Hozir qilindi** — to‘g‘ri va foydali, shu o‘zgarishda kiritildi.
- **✅ Allaqachon bor** — kodda bajarilgan; dalil ko‘rsatilgan.
- **❌ Kerak emas / noto‘g‘ri** — bu loyihaga mos kelmaydi yoki zararli.
- **⏸ Keyinroq** — to‘g‘ri, lekin hozir xavfi yoki narxi foydasidan katta.

Umumiy xato: ro‘yxat Claude API va uzun chat-bot nazarda tutadi. ErnestOS'da
agent **qisqa buyruq tahlilchisi** (ovoz → bitta tasdiqlanadigan amal), modellar
Groq → Gemini → gpt-oss, Claude ishlatilmaydi.

## I. LLM va tokenlar

| # | Band | Holat | Izoh |
|---|---|---|---|
| 1 | Vector DB / RAG | ❌ | Uzun suhbat yo‘q: har buyruq alohida, tarix qisqa va bazada. |
| 2 | Matnni tozalash | ❌ | Transkript qisqa; tejash sezilmaydi. |
| 3 | Dinamik prompt | ❌ | Bitta vazifa (buyruqni JSON'ga), modellar bepul tarifda. |
| 4 | Semantik kesh | ❌ | Har buyruq foydalanuvchining shaxsiy holatiga bog‘liq — keshlab bo‘lmaydi. |
| 5 | Temperature, «bilmasang ayt» | ✅ | `temperature: 0`; noaniq bo‘lsa `clarify` qaytadi (`agent_provider.py`). |
| 6 | Prompt inyeksiyasi | ✅ | Prompt foydalanuvchi matnini «ishonchsiz DATA» deb belgilaydi; model faqat taklif qiladi, bajarish tugma bilan. |
| 7 | Strukturali javob | ✅ | JSON schema + Pydantic (`_structured`). |
| 8 | Katta fayllar | ❌ | Agent fayl o‘qimaydi. |
| 9 | Streaming | ❌ | Javob qisqa JSON — oqim kerak emas. |
| 10 | Zaxira model | ✅ | `first_available`: Groq → Gemini → gpt-oss-20b. |
| 11 | Tarix RAMda | ✅ | Qoralama va tarix `agent_drafts` jadvalida. |
| 12 | Multi-agent | ❌ | Bitta oddiy vazifa uchun ortiqcha murakkablik. |
| 13 | Vaqt va zona | ✅ | Kontekstda bugungi sana va foydalanuvchi zonasi bor. |
| 14 | UI tarjimalari | ✅ | Hammasi `translations.py` va `index.html` lug‘atida, LLM'siz. |
| 15 | Model tanlash | ✅ | Arzon/bepul modellar; endi limit tarifga bog‘langan (Free 5/hafta, Pro 30/kun, Max 100/kun). |

## II. Backend va arxitektura

| # | Band | Holat | Izoh |
|---|---|---|---|
| 1 | `app.py`ni bo‘lish | ⏸ | To‘g‘ri (10 000+ qator), lekin katta refaktor; launchdan keyin bosqichma-bosqich. |
| 2 | Sinxron bloklanish | ✅ | Tashqi chaqiruvlar `httpx.AsyncClient`; sinxron handlerlar FastAPI threadpool'ida. |
| 3 | Og‘ir CPU ishlari | ✅ | FFmpeg alohida jarayonda, vaqt chegarasi bilan. |
| 4 | Ochiq kalitlar | ✅ | Hammasi muhit o‘zgaruvchilarida (`config.py`). |
| 5 | Global xato | ✅ | `exception_handler(Exception)` → `server_error`, ichki tafsilot chiqmaydi. |
| 6 | Scheduler xotirasi | ✅ | Doimiy, sanoqli joblar; `max_instances=1`. |
| 7 | Holatni DB'da saqlash | ✅ | Agent qoralamalari DB'da. Bot oqimlari xotirada — restartda faqat yozilayotgan qadam yo‘qoladi. |
| 8 | Graceful shutdown | ✅ | `lifespan` scheduler va botni to‘xtatadi. |
| 9 | CORS | ✅ | Faqat ilova manbalari (`APP_ORIGINS`). |
| 10 | `/api/v1` | ❌ | Hammasini qayta nomlash foyda bermaydi; o‘zgarishlar orqaga mos qilinadi, `/api/version` bor. |
| 11 | Circular import | ✅ | Muammo yo‘q; kerakli joyda kechiktirilgan import. |
| 12 | Asinxron log | ❌ | Log hajmi kichik, stdout'ga yoziladi. |
| 13 | Servis izolyatsiyasi | ✅ | Har bir job foydalanuvchi bo‘yicha `try/except` bilan o‘ralgan. |
| 14 | BackgroundTasks | ✅ | Telegram yuborishlari asinxron; email yo‘q. |
| 15 | Swagger'ni yoqish | 🔧 teskarisi | Swagger ochiq edi — production'da **o‘chirildi** (`/docs`, `/redoc`, `/openapi.json`): bu hujumchiga API xaritasi. |

## III. Ma’lumotlar bazasi

| # | Band | Holat | Izoh |
|---|---|---|---|
| 1 | N+1 | ✅ | Og‘ir joylar SQL agregatsiya bilan (audit-50, #48). |
| 2 | Indekslar | ✅ | 100+ indeks (`index=True`). |
| 3 | Connection pool | ✅ | SQLAlchemy QueuePool + `pool_pre_ping`. |
| 4 | Optimistic locking | ⏸ | Har ish joyi bitta egada; jamoada har kim o‘z belgisini qo‘yadi — to‘qnashuv xavfi past. |
| 5 | Parol xeshi | 🔧 | PBKDF2-SHA256 + tuz + `compare_digest` bor edi; takrorlar soni OWASP tavsiyasiga ko‘ra 240 000 → **600 000**. Eski xeshlar ishlashda davom etadi. |
| 6 | Alembic | ⏸ | Hozirgi migratsiya faqat ustun qo‘shadi va PostgreSQL'da tekshirilgan. |
| 7 | UTC | ✅ | `utcnow()`; ko‘rsatish foydalanuvchi zonasida. |
| 8 | Soft delete | ✅ | Vazifa, qarz, odat arxivlanadi. Akkauntni o‘chirish ataylab to‘liq (maxfiylik). |
| 9 | S3 | ❌ | Fayl saqlanmaydi; audio saqlanmaydi. |
| 10 | SQL inyeksiyasi | ✅ | Faqat ORM / parametrli so‘rovlar. |
| 11 | In-memory SQLite | ✅ | Production PostgreSQL. |
| 12 | Eski loglarni arxivlash | 🔧 | AI audit jurnali cheksiz o‘sardi — endi **180 kun**dan eskisi tunda o‘chadi. Ilova xabarlari 60 kun, idempotentlik kalitlari 1 kun. |
| 13 | JSONB/GIN | ❌ | JSON maydonlar qidirilmaydi. |
| 14 | Sessiya context manager | ✅ | Hamma joyda `with SessionLocal()`. |
| 15 | Aniq tiplar | ✅ | Sana, vaqt, BigInteger to‘g‘ri tiplar bilan. |

## IV. Xavfsizlik

| # | Band | Holat | Izoh |
|---|---|---|---|
| 1 | IP bo‘yicha limit | ✅ | Limit foydalanuvchi ID bo‘yicha; IP faqat tanib bo‘lmagan so‘rovlar uchun. |
| 2 | Slowloris | ✅ | Railway edge proxy va uvicorn vaqt chegaralari. |
| 3 | XSS | ✅ | Barcha foydalanuvchi matni `esc()` orqali; botda HTML escape. |
| 4 | 15 daqiqalik JWT | ❌ | JWT yo‘q. Telegram initData 24 soat; ilova tokeni 90 kun, bazada faqat xeshi, chiqish/o‘chirishda bekor qilinadi. |
| 5 | So‘rov hajmi | ✅ | `MAX_BODY_BYTES`, audio uchun alohida chegara. |
| 6 | RBAC | ✅ | Ish joyi izolyatsiyasi + jamoada egasi/admin rollari. |
| 7 | Brute-force | ✅ | Parol: 15 daqiqa qulf; ilova kodi: 10 daqiqada 10 urinish. |
| 8 | Server versiyasi | 🔧 | `--no-server-header` (Dockerfile, Procfile). |
| 9 | Timing attack | ✅ | `hmac.compare_digest` hamma joyda. |
| 10 | JWE / kalit aylantirish | ❌ | JWT yo‘q; imzo bot tokeni bilan (Telegram standarti). |
| 11 | Path traversal | ✅ | Foydalanuvchi fayl yo‘lini bera olmaydi. |
| 12 | WebSocket | ✅ | Yo‘q. |
| 13 | Zaif kutubxonalar | 🔧 | `.github/dependabot.yml`: pip, npm (mobile) va GitHub Actions. |
| 14 | CSRF | ❌ | Cookie ishlatilmaydi — ma’lumot header'da; CSRF bu yerda imkonsiz. |
| 15 | Loglarda sir | ✅ | initData, token, parol logga yozilmaydi — faqat sabab. |

## V. Frontend

| # | Band | Holat | Izoh |
|---|---|---|---|
| 1 | Bitta asosiy tugma | ✅ | «Hozir» kartasi — bitta asosiy harakat. |
| 2 | Offline | ✅ | Offline navbat: belgilashlar qurilmada kutadi, bir marta yuboriladi. |
| 3 | Og‘ir JS | 🔧 | Sahifa ~550 KB — endi **gzip** bilan beriladi (~5 barobar kichik). |
| 4 | Qorong‘i rejim | ✅ | Tizim mavzusi + qo‘lda tanlash. |
| 5 | Teskari aloqa | ✅ | Haptic, toast, saqlash holati. |
| 6 | Optimistic UI | ✅ | Belgilash darhol ko‘rinadi, xatoda qaytadi. |
| 7 | CLS | ✅ | Muhim muammo topilmadi. |
| 8 | Responsive | ✅ | Telefon uchun yozilgan, 360 px'da tekshirilgan. |
| 9 | Qoralamani saqlash | ✅ | Qoralamalar `localStorage`da. |
| 10 | Klaviatura yorliqlari | ❌ | Telefon ilovasi. |
| 11 | a11y | ✅ | `aria-label`, `role`, fokus holati. |
| 12 | CSS modullar | ❌ | Tokenlar bitta tizimda; konflikt yo‘q. |
| 13 | Virtual scroll | ❌ | Ro‘yxatlar kichik (sahifalangan). |
| 14 | Markdown | ❌ | AI matn chiqarmaydi, faqat tasdiq kartasi. |
| 15 | Rasm alt | ✅ | Avatar `alt` bilan. |

## VI. Telegram bot

| # | Band | Holat | Izoh |
|---|---|---|---|
| 1 | Webhook | ✅ | `WEBHOOK_URL` berilsa webhook. |
| 2 | aiogram | ❌ | python-telegram-bot ham asinxron, `concurrent_updates(True)`. |
| 3 | Escape | ✅ | `esc()` HTML rejimida. |
| 4 | FSM'ni DB'da | ⏸ | Faqat yozilayotgan qadam yo‘qoladi; foydalanuvchi qayta boshlaydi. |
| 5 | Spam cheklovi | ✅ | API limiter + agent kunlik/haftalik limit. |
| 6 | `drop_pending_updates=True` | ❌ | Ataylab `False`: deploy paytida yozilgan xabarlar yo‘qolmasligi kerak. |
| 7 | «Yozmoqda…» | ✅ | Agent `ChatAction.TYPING` yuboradi. |
| 8 | Fayl turi | ✅ | Faqat ovoz/audio; hajm va davomiylik tekshiriladi. |
| 9 | AI ishlamasa | ✅ | Zaxira modellar; xatoda «qayta yuborish» tugmasi. |
| 10 | Buyruqlar menyusi | 🔧 | «/» menyusi 3 tilda (`set_my_commands`). |

## VII. Infratuzilma

| # | Band | Holat | Izoh |
|---|---|---|---|
| 1 | Alpine | ❌ | `python:3.12-slim` allaqachon; alpine'da FFmpeg va wheel'lar muammoli. |
| 2 | Root | ✅ | `USER ernest`. |
| 3 | Qatlam keshi | ✅ | `requirements.txt` koddan oldin. |
| 4 | Statik fayllar | ✅ | Bitta fayl; endi gzip. |
| 5 | Sirlar | ✅ | `.env` `.dockerignore`da. |
| 6 | Mikroservislar | ❌ | Bitta kichik jamoa uchun ortiqcha; Railway'da DB alohida servis. |
| 7 | Gunicorn -w 4 | ❌ **zararli** | Bot polling, scheduler va xotiradagi limiter **bitta jarayon** talab qiladi; 4 worker = har xabar 4 marta. |
| 8 | Healthcheck | ✅ | `/health/live`, `/health/ready`, `/health/reports`. |
| 9 | Versiyalar | ✅ | `constraints-tested.txt`. |
| 10 | Loglar | ⏸ | Railway loglari; tashqi log servisi trafik oshganda. |

## VIII. Test va monitoring

| # | Band | Holat | Izoh |
|---|---|---|---|
| 1 | AI mock | ✅ | Testlar tashqi API'ga chiqmaydi. |
| 2 | Linter | ✅ | CI'da pyflakes. |
| 3 | Coverage | ⏸ | 1 000+ test bor; foiz talabini keyin qo‘shish mumkin. |
| 4 | Tarmoq xatolari | ✅ | Brauzer sinovi offline navbatni tekshiradi. |
| 5 | Pre-commit | ⏸ | Ixtiyoriy. |

## Xulosa

100 bandning: **8 tasi hozir qilindi**, **~60 tasi allaqachon bor edi**,
**~22 tasi bu loyihaga mos emas** (ulardan ikkitasi — `gunicorn -w 4` va
`drop_pending_updates=True` — botni buzgan bo‘lardi), **~10 tasi keyinga**.
