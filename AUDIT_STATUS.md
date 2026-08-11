# Release audit — nima qilindi, nima qilinmadi

`ErnestOS_100_muammo_release_audit.docx` bo'yicha. 100 band: P0 — 22, P1 — 63, P2 — 15.

## Yopilgan P0 lar (kod bilan)

| ID | Muammo | Qanday yopildi |
|---|---|---|
| 001 | Telegram xatosida obuna fail-open | Uch holat aniq: `None` hech qachon kirish bermaydi, `is_subscribed` yozmaydi |
| 002 | Mini App eskirgan bayroqqa qaraydi | Tasdiqlangan javob qisqa TTL bilan keshlanadi, eskirsa qayta tekshiriladi |
| 003 | Onboarding tugamagan akkaunt yozadi | 409 `onboarding_required`; `/api/me` o'qish uchun ochiq |
| 012 | Rate limit yo'q | Foydalanuvchi bo'yicha token bucket, 429 + Retry-After |
| 013 | O'lcham cheklovi yo'q | 256 KB body limiti + har maydonda `max_length` |
| 014 | HTML escape qilinmaydi | Bitta `esc()` helper, barcha user matni undan o'tadi |
| 016 | Telefon admin kanalda | Loglardan butunlay olib tashlandi |
| 032 | Scheduler leader election yo'q | PostgreSQL advisory lock, yuborishni ham qamrab oladi |
| 033 | `drop_pending_updates=True` | `False` — deploy paytidagi bosishlar yo'qolmaydi |
| 034 | Flow race | Har oqimda `id` va `expires`; eski callback e'tiborsiz qoladi |
| 036 | Report idempotency atomik emas | INSERT bilan claim; unique constraint g'olibni belgilaydi |
| 046 | Kundalik o'chirilsa Summary qoladi | Bitta tranzaksiyada ikkalasi ham tozalanadi |
| 076 | Bot HTML parse xatosi | 014 bilan bir xil `esc()` |
| 087 | Health faqat process | `/health/live` va `/health/ready` ajratildi |

## Yopilgan buzuq P1 lar

| ID | Muammo | Yechim |
|---|---|---|
| 037 | Bitta xato butun hisobotni to'xtatadi | Har foydalanuvchi alohida `try`, sabab yoziladi |
| 061 | Avatar 401 qaytaradi | `<img>` header yubora olmaydi — imzolangan blob `?tgdata=` orqali |
| 062 | CSV Telegram brauzerida ochilmaydi | Fayl bot orqali chatga yuboriladi |

## Ataylab qilinmagan bandlar

Bular audit uchun to'g'ri, lekin **hozirgi hajm** (14 foydalanuvchi) uchun
foydadan ko'ra murakkablik qo'shadi. Foydalanuvchi soni oshganda qaytiladi.

| ID | Muammo | Nega hozir emas |
|---|---|---|
| 021 | Alembic migratsiyalar | `init_db()` yetishmayotgan ustunni o'zi qo'shadi va bu **testlangan**. Jonli bazaga Alembic joriy qilish hozir foydadan ko'ra risk |
| 084 | Sync SQLAlchemy event loopni bloklaydi | Bir necha millisekundlik so'rovlar; threadpool yangi xato manbai bo'lardi. Sinab ko'rildi va **orqaga qaytarildi** |
| 088 | To'liq observability stack | Structured log bor; metrics/alert dashboard bu hajmda ortiqcha |
| 011 | PostgreSQL RLS | Ilova qatlamida izolyatsiya 20+ test bilan qoplangan. RLS ni lokal tekshirib bo'lmaydi (Postgres yo'q), tekshirilmagan xavfsizlik qatlami qo'shish noto'g'ri |

## Kod bilan yopib bo'lmaydigan bandlar

Bular **sizning infratuzilmangizni** talab qiladi:

| ID | Nima kerak |
|---|---|
| 030 | Railway'da avtomatik backup yoqish + oyiga bir marta restore sinovi |
| 031 | Web va bot'ni alohida servisga ajratish (hozir bitta instansiya — muammo emas) |
| 089 | GitHub Actions CI sozlash |
| 090 | Staging muhiti, load test, canary rollout |

## Hali qilinmagan P1/P2 lar

63 P1 va 15 P2 dan yuqoridagilar yopildi. Qolganlari asosan mahsulot
yaxshilanishlari (041–060 odat/vazifa mantiqi, 063–070 UX, 091–100 retention).
Ular sizning 10 punktli UX ro'yxatingiz bilan ustma-ust tushadi — keyingi
bosqichda o'shalar bo'yicha boriladi.

## Testlar

```bash
.venv/bin/python -m pytest tests/ -q     # 125 o'tadi
```

Yangi qo'shilganlari: obuna uch holati, onboarding gate, rate limit, o'lcham
cheklovi, escape, PII, report claim, flow expiry, health, avatar, journal sync.
