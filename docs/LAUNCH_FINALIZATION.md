> **HOLAT: implement qilindi (2026-08-12).**
> Ushbu hujjatdagi PRODUCT/UI scope kodga tushirildi — bot menyusi (7 ta),
> compact Home, canonical `overall` formulasi, bitta haftalik missiya,
> faqat bugungi vazifalar, habit reorder, standalone Statistika, fixed
> privacy strip va Goals'ning to'liq olib tashlanishi (migration `0002`).
> Qolgani — real qurilmada manual QA.
>
> Qayta implement qilish uchun emas, **reference** sifatida saqlanmoqda.
> Deploy qadamlari va migration rollback: `README.md`.

---

# ErnestOS — PUBLIC LAUNCH FINALIZATION
## Claude Code uchun yakuniy, token-tejamkor master implementation prompt

> **Bu fayl ErnestOS public launch oldidan eng so‘nggi product qarori.**
> Agar eski `TOP 50`, eski executor, oldingi prompt yoki koddagi eski UX qarori ushbu hujjat bilan zid bo‘lsa, **PRODUCT/UI SCOPE bo‘yicha SHU HUJJAT USTUN**.
>
> P0 kafolatlarni buzma: autentifikatsiya, tenant/workspace isolation, PostgreSQL data integrity, user history, subscription enforcement, scheduler/report idempotency, PII/initData redaction va ishlayotgan barqaror funksiyalar.

---

# 0. ROL

Sen ErnestOS’ning public launch oldidan yakuniy versiyasiga javobgar **senior product engineer + backend engineer + Telegram Bot engineer + Telegram Mini App engineer + UX engineer**san.

Vazifang — mavjud repository ichida quyidagi talablarni **real kodda** implement qilish. Faqat reja, audit, tavsiya yoki pseudo-code bilan to‘xtama.

ErnestOS dashboard devori emas. User bir necha soniyada bugungi holatini, asosiy missiyasini, bugungi vazifalarini, odatlar/namoz holatini va umumiy progressini tushunsin.

---

# 1. QAT’IY TOKEN VA CONTEXT INTIZOMI

Bu bo‘lim majburiy.

## 1.1. Butun repositoryni qayta o‘qima

Ishni:

```bash
git status --short
```

bilan boshlagin.

Keyin targeted search:

```bash
rg "..." .
git grep "..."
```

QAT’IYAN QILMA:

- repositoryni boshidan oxirigacha recursively read qilish;
- `app.py`, `services.py`, `webapp/index.html`, katta test file’larini avtomatik to‘liq o‘qish;
- eski Top 50’ni qayta audit qilish;
- unchanged kodni qayta-qayta o‘qish;
- huge logs yoki full test traceback’larni contextga tashlash;
- full `git diff`ni bir necha marta chiqarish;
- unrelated refactor yoki yangi feature;
- “yana nimalarni yaxshilash mumkin?” deb codebase bo‘ylab yurish.

## 1.2. Katta file qoidasi

Katta file kerak bo‘lsa:

1. exact symbol/string qidir;
2. relevant function/class/sectionni top;
3. faqat surrounding relevant range’ni o‘qi;
4. edit qil;
5. unchanged qismni qayta o‘qima.

## 1.3. Faqat launch scope

Targeted discovery faqat:

- Bot main menu;
- Bot Home;
- Bot Tasks/Projects;
- Goals/Vision;
- Mini App nav;
- Mini App Home;
- Weekly mission/focus;
- Tasks/Projects;
- Habits reorder;
- Prayer metrics;
- Statistics;
- Privacy UI;
- UZ/EN/RU translations;
- relevant tests.

## 1.4. Test intizomi

Development davomida targeted test:

```bash
pytest -q -k "home or task or project or habit or focus or stats or goal" --maxfail=1
```

yoki aniq test file/function.

Har editdan keyin full suite ishlatma. Final implementation tugagach:

```bash
pytest -q
```

faqat bir marta.

## 1.5. Diff

Oxirida:

```bash
git diff --stat
```

va faqat changed filelar uchun targeted diff.

## 1.6. Unrelated muammo

Launch scope’ni bloklamaydigan unrelated bugni hozir tuzatma. Faqat P0 bo‘lsa darhol hal qil:

- data loss;
- auth bypass;
- cross-user leak;
- DB corruption;
- duplicate report;
- current launch flow’ni butunlay bloklaydigan defect.

---

# 2. FINAL PRODUCT TAMOYILLARI

ErnestOS:

- premium;
- minimal;
- calm;
- fast;
- reliable;
- private;
- modern;
- decision-fatigue’ni kamaytiradigan Life OS

bo‘lsin.

Home — ko‘rish, tushunish va action uchun. Edit/delete/admin controls detail yoki management page’larda.

Agar obyekt mavjud bo‘lmasa, unga tegishli Done/Edit/Delete button ham chiqmasin.

Faqat real persisted backend data ishlat. Fake chart, fake analytics, fake progress, fake success yoki client-only mutation yo‘q.

---

# 3. MAXFIYLIK — ENG MUHIM PUBLIC REASSURANCE

Mini App shell’da bitta fixed privacy line:

**UZ:** `Ma’lumotlaringiz va maxfiyligingiz to‘liq himoyalangan 🔒`  
**EN:** `Your data and privacy are fully protected 🔒`  
**RU:** `Ваши данные и конфиденциальность полностью защищены 🔒`

Qoidalar:

- bottom nav’dan biroz yuqorida fixed;
- scroll qilinganda siljimaydi;
- Home/Habits/Tasks/Statistics bir shell orqali ishlatadi;
- content va navni yopmaydi;
- Telegram safe-area hisobga olinadi;
- narrow screen’da max 2 qator;
- page ichidagi repeated privacy cards olib tashlanadi.

Implement qilinmagan undan kuchli claim qo‘shma: “admin ham kira olmaydi”, “100% end-to-end encrypted” kabi.

Bot Home oxirida ham compact privacy line bo‘lsin.

---

# 4. YAKUNIY TELEGRAM BOT MENYUSI

Persistent menu **aynan 7 ta**:

1. Home
2. Odatlar
3. Vazifalar
4. Statistika
5. Sozlamalar
6. Taklif
7. Mini App

Olib tashla:

- Maqsadlar / Goals;
- `Turdim` persistent main-menu item.

`Turdim` kerak bo‘lsa Home/Odatlar ichidagi contextual action yoki safe text command bo‘lishi mumkin.

UZ/EN/RU menu natural bo‘lsin.

---

# 5. TELEGRAM BOT HOME — MAKSIMAL SODDA

Target:

```text
🏠 Ernestning shaxsiy tizimi
📅 11-avgust, Seshanba

🎯 Missiya
ErnestOSni ommaga taqdim qilish

⚡ Bugun
— Vazifa 1
— Vazifa 2

✅ 🕌 🔥 5/6 · 4.5/5 · 0
📊 🔺 80%

🔒 Ma’lumotlaringiz va maxfiyligingiz to‘liq himoyalangan
```

Sample value’larni hardcode qilma.

- Sana user timezone’dan, default `Asia/Tashkent`, active language’da.
- Maksimum **1 ta** current weekly mission.
- **Faqat BUGUNGI tasklar**, haftalik emas.
- Task yo‘q bo‘lsa `— yo‘q`.
- Metrics: habits done/total, prayer score/max, streak, overall/trend.
- O‘sish yashil/up, pasayish qizil/down, teng neutral.
- Ayblovchi copy yo‘q.
- Default Home’dan Goals, Vision, birthdays, project summary, katta overdue wall va secondary clutter olib tashlansin.

---

# 6. BOT TASKS / PROJECTS — CONTEXTUAL BUTTONLAR

## Zero task + zero project

Faqat:

```text
[ + Vazifa ] [ + Loyiha ]
```

Ko‘rsatma:
- Bajarildi;
- Tahrirlash;
- Vazifani o‘chirish;
- Loyihani o‘chirish.

## Data mavjud bo‘lsa

```text
[ + Vazifa ] [ + Loyiha ]
[ ✅ Bajarildi ] [ ✏️ Tahrirlash ]
[ 🗑 Vazifa ] [ 🗑 Loyiha ]
```

Lekin:
- `Bajarildi` faqat open task bo‘lsa;
- `Tahrirlash` faqat editable task/project bo‘lsa;
- task delete faqat task bo‘lsa;
- project delete faqat project bo‘lsa.

Dead button/chooser = 0.

Project management Tasks ichida qolsin. Project detail ichida project info va uning tasklari ko‘rinsin.

**Hozir task yoki project create nosoz bo‘lsa, root cause’ni top, tuzat va real persistence bilan test qil.**

Project edit yo‘q bo‘lsa minimal safe rename/details edit qo‘sh.

---

# 7. GOALS / MAQSADLAR — HAMMAYOQDAN OLIB TASHLA

## FINAL OVERRIDE

Public launch’da Goals / Maqsadlar / Vision kerak emas.

### Telegram Bot’dan olib tashla
- menu;
- Home;
- help;
- reachable command;
- button;
- report;
- visible route/text.

### Mini App’dan olib tashla
- bottom nav;
- Vision/Goals screen;
- Home card;
- calendar goal event;
- public create/edit/complete/delete flow.

### Backend/API’dan
- user-facing route/usage;
- Home dependency;
- report dependency;
- frontend dependency

uzilsin.

## Database bo‘yicha final qaror

Goals active production DB strukturasidan ham olib tashlanadi, lekin **blind destructive DROP qilma**.

Tartib:

1. goal-related existing row/data borligini aniqlagin;
2. mavjud bo‘lsa safe backup/export yoki reversible migration safety yarat;
3. FK/dependencylarni tekshir;
4. repositorydagi mavjud migration/schema-update strategiyasi orqali Goal-related active tables/columns/data’ni olib tashla;
5. rollback/recovery yo‘li bo‘lsin.

Maqsad:
- public Goals UI = 0;
- active Goals runtime dependency = 0;
- active Goals DB feature = 0;
- broken import/FK = 0;
- silent data loss = 0.

---

# 8. MINI APP BOTTOM NAVIGATION

Aynan:

1. Home
2. Odatlar / Habits
3. Vazifalar / Tasks
4. Statistika / Statistics

Goals/Vision yo‘q.

Settings — Home header’dagi gear icon.  
Profile/avatar — Home header’da.

Olib tashlangan featurelarni yashirish uchun yangi `More` page yaratma.

---

# 9. MINI APP HOME — FINAL TARTIB

Aynan:

1. Greeting + Settings + Profile
2. Local date
3. Quote
4. 4 compact metric
5. Bitta primary weekly mission
6. **Faqat bugungi tasks**, project bo‘yicha grouped
7. Calendar
8. Fixed privacy strip
9. Fixed bottom nav

---

# 10. HEADER

```text
Assalomu alaykum, Ernest        ⚙️   [avatar]
11-avgust, Seshanba
```

- real user name;
- gear greeting qatorda;
- avatar eng o‘ngda;
- date pastda;
- user timezone;
- active language;
- Telegram safe-area;
- 320px width’da buzilmasin;
- uzun ism layoutni sindirmasin.

---

# 11. QUOTE

Date’dan keyin user tanlagan motivatsion quote.

- optional;
- bo‘sh bo‘lsa katta empty card yo‘q;
- nozik `Quote qo‘shish` placeholder mumkin;
- edit oson;
- vertical space’ni bekorga yemasligi kerak.

---

# 12. HOME METRICS

Quote’dan keyin 4 metric:

```text
Odatlar       Namoz
5 / 6         4.5 / 5

Ketma-ket     Umumiy
0             80% ↑
```

- no horizontal overflow;
- real backend data;
- overall canonical backend formula;
- trend kecha bilan;
- down = red + arrow;
- up = green + arrow;
- status faqat rangga tayanmasin.

---

# 13. BIRTA ASOSIY HAFTALIK MISSIYA

Eski max 3 Weekly Focus → **max 1 active primary weekly mission**.

Home’da:
- mission nomi;
- priority border;
- edit/delete icon yo‘q.

Mission yo‘q bo‘lsa:

```text
+ Missiya qo‘shish
```

Mission tap → detail/manage sheet mumkin.

Input faqat:
- title;
- importance.

Importance:
- `high`;
- `medium`;
- `low`;
- default `medium`.

Border:
- high = red;
- medium = amber/yellow;
- low = green.

Accessibility uchun label/icon/aria ham bo‘lsin.

Legacy bir haftada bir nechta focus row bo‘lsa:
- Home uchun deterministic bitta primary tanla;
- yangi write’da max 1 active mission enforce qil;
- eski ko‘p cardni UI’da chiqarma.

---

# 14. HOME — FAQAT BUGUNGI TASKLAR

**Haftalik tasklar emas. Faqat bugungi relevant tasklar.**

Project bo‘yicha group:

```text
ErnestOS
┃ Public release polish
  Yuqori
  Home va statistika UI’ni final qilish

Marketing
┃ Launch postini tayyorlash
  O‘rta
  Telegram + Instagram matni

Alohida
┃ Domenni tekshirish
  Past
```

- project task → project nomi ostida;
- project’siz → `Alohida / Standalone`;
- primary = title;
- secondary = priority, time/deadline, qisqa description mavjud bo‘lsa;
- high red, medium amber, low green;
- label rangdan tashqari ham bor.

Home row’da:
- Edit yo‘q;
- Delete yo‘q.

Task tap → detail; edit Tasks/detail’da.

Duplicate Home task card yo‘q.

---

# 15. CALENDAR

Bugungi tasks sectionidan keyin.

- compact;
- responsive;
- Home primary contentni pastga haddan tashqari surmaydi;
- task/project event tap detailga olib borishi mumkin;
- Goals event yo‘q;
- local timezone;
- horizontal overflow yo‘q.

---

# 16. FIXED PRIVACY + NAV SHELL

```text
┌──────────────────────────────────┐
│ page content                     │
│                                  │
├──────────────────────────────────┤
│ Ma’lumotlaringiz va              │
│ maxfiyligingiz to‘liq            │
│ himoyalangan 🔒                  │
├──────────────────────────────────┤
│ Home Odatlar Vazifalar Statistika│
└──────────────────────────────────┘
```

Content bottom padding privacy strip + nav + safe-area balandligini hisobga olsin.

---

# 17. ODATLAR — SECOND PAGE + REORDER

Home’dan keyin Odatlar.

Habits / Prayer / Journal working behavior saqlansin. Stats Habits ichidan olib tashlansin.

## Habit reorder

Existing `Habit.position`dan foydalan.

Mini App:
- touch drag-and-drop;
- accessibility fallback Up/Down yoki reorder mode.

Server:
- persist;
- refresh/reopen’da same order;
- Bot same canonical order.

Suggested API:

```http
PATCH /api/habits/reorder

{
  "habit_ids": [12, 3, 8, 4, 9, 2]
}
```

Validate:
- authenticated workspace ownership;
- reorderable/current habit;
- no duplicates;
- cross-workspace reject;
- atomic transaction.

Canonical reordered list qaytar.

---

# 18. VAZIFALAR SAHIFASI

Bottom nav’da uchinchi. Management shu yerda.

Ishlashi shart:

- task create;
- task edit;
- task complete/reopen;
- safe archive/delete;
- project create;
- project edit;
- project open;
- project ichidagi tasks;
- projectga task qo‘shish.

## Launch blocker

Agar task/project qo‘shib bo‘lmayotgan bo‘lsa:
- root cause top;
- fix qil;
- DB persist test qil;
- refreshdan keyin mavjudligini tekshir.

Projects alohida section/view bo‘lsin. Projectga kirganda shu project tasklari ko‘rinsin.

---

# 19. STATISTIKA — STANDALONE TO‘RTINCHI PAGE

Stats Habits tab emas.

Bottom nav:

```text
Home | Odatlar | Vazifalar | Statistika
```

Statistics page faqat ochilganda kerakli stats data’ni yuklasin. Home full chart history fetch qilmasin.

Majburiy:

1. Overall % — primary;
2. Tasks %;
3. Habits %;
4. Prayer % / score;
5. Streak/consistency — secondary.

Existing clean stats architecture qo‘llasa Week/Month/Year controls qolishi mumkin.

---

# 20. CANONICAL OVERALL FORMULA

Bitta backend calculation. Frontend JS’da duplicate formula yo‘q.

```text
habit_pct =
100 * completed_due_habits / due_habits

prayer_pct =
100 * prayer_score / prayer_max

task_pct =
100 * completed_relevant_today_tasks / relevant_today_tasks

available_components =
faqat denominator'i bugun ma’noli componentlar

overall_pct =
round(arithmetic_mean(available_components))
```

Denominator yo‘q category’ni fake `0%` qilib average’ni buzma — mean’dan chiqar.

Hech component mavjud bo‘lmasa butun system bo‘ylab bitta canonical empty behavior (`0` yoki `—`).

Task denominator:
- due today;
- yoki explicitly selected/scheduled today.

Barcha historical tasklar emas.

Prayer canonical backend score/max.

Bir fixture uchun **aynan bir xil overall**:
- Bot Home;
- Mini App Home;
- Statistics;
- evening/daily report.

---

# 21. TREND

Today Overall vs Yesterday Overall faqat comparable bo‘lsa.

- ↑ growth;
- ↓ decline;
- equal = neutral/no arrow.

Up green, down red, neutral theme color. Ayblovchi copy yo‘q.

---

# 22. MINI APP VISUAL WIREFRAME

```text
┌────────────────────────────────────────┐
│ Assalomu alaykum, Ernest     ⚙️  [E]  │
│ 11-avgust, Seshanba                    │
│                                        │
│ “Intizom erkinlik yaratadi.”           │
│                                        │
│ ┌─────────────┐ ┌─────────────┐        │
│ │ Odatlar 5/6 │ │ Namoz 4.5/5 │        │
│ └─────────────┘ └─────────────┘        │
│ ┌─────────────┐ ┌─────────────┐        │
│ │ Ketma-ket 0 │ │ Umumiy 80%↑ │        │
│ └─────────────┘ └─────────────┘        │
│                                        │
│ ASOSIY MISSIYA                         │
│ ┃ ErnestOSni ommaga taqdim qilish      │
│                                        │
│ BUGUNGI VAZIFALAR                      │
│ ErnestOS                               │
│ ┃ Public release polish                │
│   Yuqori                               │
│   Home va statistika UI’ni final qilish│
│                                        │
│ Marketing                              │
│ ┃ Launch postini tayyorlash            │
│   O‘rta                                │
│   Telegram + Instagram matni           │
│                                        │
│ Alohida                                │
│ ┃ Domenni tekshirish                   │
│   Past                                 │
│                                        │
│ KALENDAR                               │
│ [ responsive calendar ]                │
├────────────────────────────────────────┤
│ Ma’lumotlaringiz va maxfiyligingiz     │
│ to‘liq himoyalangan 🔒                 │
├────────────────────────────────────────┤
│ Home  Odatlar  Vazifalar  Statistika   │
└────────────────────────────────────────┘
```

Pixel-perfect majburiyat emas; **information hierarchy aynan shu**.

---

# 23. HOME BACKEND/API PAYLOAD

Canonical neutral data:

- display name;
- local date;
- quote;
- habit done/total;
- prayer score/max;
- streak;
- overall + trend;
- one weekly mission + priority;
- today tasks grouped by project;
- minimal calendar data.

Remove:
- public Goal count/dependency;
- unnecessary full analytics history.

Frontend source-of-truth bo‘lmasin.

---

# 24. WEEKLY MISSION BACKEND

Existing max 3 → max 1 active current mission.

Priority:

```text
high | medium | low
```

default `medium`.

Create/update/replace/delete’da tenant ownership validate.

Schema change uchun repositoryning existing safe schema-update/migration strategy’sidan foydalan. Faqat bitta field uchun katta yangi framework joriy qilib token yoqma.

---

# 25. STATS API

`/api/stats` yoki existing equivalent:

- overall;
- tasks;
- habits;
- prayer;
- streak;
- required period series.

Obvious N+1 yoki per-day DB loop yaratma. Home full stats historyni yuklamasin.

---

# 26. VISUAL DESIGN

- premium;
- clean;
- calm;
- minimal;
- modern;
- kuchli text/background contrast;
- consistent radius/spacing/typography.

Priority:
- high = red accent;
- medium = amber;
- low = green.

Mavjud theme tokenlardan foydalan.

Accessibility:
- color-only signal yo‘q;
- ~44px settings/avatar touch target;
- semantic controls;
- visible focus;
- zoom/accessibility regressiyasi yo‘q.

Responsive:
- 320px horizontal scroll = 0;
- clipped button = 0;
- fixed strip/nav overlap = 0;
- uzun UZ/RU text va task title test qil.

---

# 27. I18N — UZ / EN / RU

Har yangi visible key uch tilda.

| Concept | UZ | EN | RU |
|---|---|---|---|
| Statistics | Statistika | Statistics | Статистика |
| Overall | Umumiy | Overall | Общий результат |
| Primary mission | Asosiy missiya | Primary mission | Главная миссия |
| Add mission | Missiya qo‘shish | Add mission | Добавить миссию |
| High | Yuqori | High | Высокий |
| Medium | O‘rta | Medium | Средний |
| Low | Past | Low | Низкий |

Privacy:
- UZ: `Ma’lumotlaringiz va maxfiyligingiz to‘liq himoyalangan`
- EN: `Your data and privacy are fully protected`
- RU: `Ваши данные и конфиденциальность полностью защищены`

`Vision`, `Goals`, `Maqsadlar`, `Цели` user-facing feature label sifatida qolmasin.

Date active language’da. Language switch account recreation talab qilmasin.

---

# 28. OLD LAUNCH-CRITICAL KAFOLATLARNI BUZMA

Saqlansin:

- subscription three-state/protected endpoint behavior;
- onboarding mutation gate;
- auth/session validation;
- tenant ownership;
- rate limits;
- request/body validation;
- safe HTML escaping;
- PII/initData redaction;
- scheduler/report duplicate protection;
- health live/ready;
- data integrity/soft-delete semantics.

UI polish uchun security’ni soddalashtirma.

---

# 29. IMPLEMENTATION ORDER

1. `git status --short`
2. Targeted search only.
3. Canonical backend:
   - overall stats;
   - max 1 mission + priority;
   - today-task project grouping;
   - habit reorder;
   - task/project create blocker.
4. Bot:
   - 7-item menu;
   - Goals/Turdim removal;
   - compact Home;
   - Stats;
   - contextual task/project controls.
5. Mini App nav:
   - Vision remove;
   - Statistics add.
6. Mini App Home:
   - header/date → quote → metrics → one mission → today project-grouped tasks → calendar.
7. Repeated privacy cards → one fixed strip.
8. Habits Stats tab remove + reorder.
9. Tasks/Projects create + management fix.
10. Goals DB safe cleanup/removal.
11. UZ/EN/RU.
12. Targeted tests.
13. Full suite once.
14. `git diff --stat` + changed-file targeted diff.
15. STOP.

---

# 30. QABUL MEZONLARI

## Bot
- final 7-item menu;
- Goals yo‘q;
- persistent Turdim yo‘q;
- compact Home;
- max 1 mission;
- only today tasks;
- habits/prayer/streak/overall/trend;
- privacy line;
- contextual empty/data controls.

## Mini App Home
- greeting/settings/profile;
- local date;
- quote;
- 4 metrics;
- exactly 1 mission;
- priority border + non-color label;
- only today tasks grouped by project;
- no Home edit/delete clutter;
- calendar.

## Privacy + nav
- one fixed privacy strip;
- fixed bottom nav;
- `Home / Odatlar / Vazifalar / Statistika`;
- Goals/Vision unreachable.

## Habits
- reorder works;
- persistence works;
- Bot same order;
- Stats tab removed.

## Tasks/Projects
- task create works;
- project create works;
- DB persistence;
- refresh persistence;
- project detail contains its tasks.

## Statistics
- standalone;
- Overall + Tasks + Habits + Prayer;
- streak secondary;
- canonical backend formula;
- denominator-less component average’ni buzmaydi.

## Goals
- public UI = 0;
- runtime user-facing dependency = 0;
- active DB feature = 0;
- safe migration/recovery documented;
- broken reference = 0.

## Responsive/i18n
- 320px overflow = 0;
- fixed controls contentni yopmaydi;
- UZ/EN/RU mixed-language = 0;
- long name/title layoutni buzmaydi.

---

# 31. TEST COMMANDLAR

Environmentga moslashtir:

```bash
python -m py_compile app.py services.py db.py
```

Targeted:

```bash
pytest -q -k "home or task or project or habit or focus or stats or goal" --maxfail=1
```

Final:

```bash
pytest -q
```

faqat bir marta.

QILMA:
- legitimate failing test delete;
- sababsiz skip;
- assertionni green uchun weaken;
- failure bor holda launch-ready claim.

---

# 32. FINAL JAVOB FORMATI

Uzun essay yo‘q. Aynan:

```markdown
## Launch pass
DONE / PARTIAL / BLOCKED

## Implemented
- ...

## Changed files
- file — sabab

## Migrations / DB
- ...

## Tests
- targeted: X passed / Y failed / Z skipped
- full suite: X passed / Y failed / Z skipped

## Final checks
- Bot menu final: PASS/FAIL
- Bot Home compact: PASS/FAIL
- Task create: PASS/FAIL
- Project create: PASS/FAIL
- Goals public UI removed: PASS/FAIL
- Goals active DB feature removed safely: PASS/FAIL
- Stats standalone: PASS/FAIL
- Habit reorder persists: PASS/FAIL
- Fixed privacy strip: PASS/FAIL
- Bot empty-state controls: PASS/FAIL
- Overall formula parity: PASS/FAIL
- UZ/EN/RU parity: PASS/FAIL

## Remaining blocker
- none YOKI aniq blocker
```

---

# 33. STOP

Scoped launch pass implement + verify bo‘lgach STOP.

Boshlama:
- unrelated refactor;
- yangi feature;
- eski Goals;
- Top 50 global audit;
- unnecessary architecture rewrite;
- generic improvement sweep.

Faqat manual/device QA qolsa aynan shuni ayt. Manual test natijasini to‘qima.

---

# 34. BEGIN

Hozir:

1. `git status --short`
2. targeted search
3. minimal relevant reads
4. real implementation
5. targeted tests
6. full suite once
7. targeted diff
8. concise report
9. STOP

**Planning-only mode’da qolma. Kodni real o‘zgartir.**


---

# OWNER ONLY — ERNEST UCHUN, CLAUDE’GA PASTE QILISH SHART EMAS

## Limit va tokenni keskin tejash qoidalari

### 1. Yangi contextdan boshlang
Eski ulkan conversationni davom ettirish o‘rniga launch pass uchun yangi session oching.

### 2. Promptni repositoryga bir marta saqlang
Faylni:

```text
docs/LAUNCH_FINALIZATION.md
```

qilib qo‘ying.

Keyingi sessionda butun promptni qayta paste qilish o‘rniga:

```text
Read docs/LAUNCH_FINALIZATION.md and execute it.
Do not read unrelated files.
```

deyish yetadi.

### 3. `CLAUDE.md`ni kichik saqlang
Unda faqat doimiy qoidalar:
- architecture map;
- common commands;
- token discipline;
- destructive command prohibition.

Bu ulkan launch promptni `CLAUDE.md`ga qo‘ymang, chunki project memory sessionlarda avtomatik contextga tushishi mumkin.

### 4. Bir session = bir maqsad
Launch pass davomida yangi feature qo‘shmang.

### 5. Search → read → edit
“Read the entire repository first” demang.

### 6. Concise tests
`pytest -q --maxfail=1`.

### 7. Full suite faqat finalda
Har editdan keyin emas.

### 8. Diffni qisqa qiling
`git diff --stat`, keyin faqat relevant file diff.

### 9. Session juda uzunlashsa handoff
`docs/LAUNCH_HANDOFF.md`ga faqat:
- completed;
- changed files;
- failing tests;
- next exact step

yozdiring. So‘ng yangi sessionda shu kichik file bilan davom eting.

### 10. Model routing
Oddiy/ko‘p coding uchun Sonnet-class modeldan boshlang. Faqat chinakam murakkab DB/security blocker bo‘lsa Opus-class modelga o‘ting. Butun taskni Opus’da boshidan qayta boshlamang.

### 11. Bir xil narsani qayta audit qildirmang
Claude topgan path/symbolni keyingi turnda yana global qidirmasin.

### 12. Final output qisqa bo‘lsin
Uzun explanation ham usage sarflaydi.

### 13. Web researchni cheklang
Normal implementationda broad web research yo‘q. Aniq security/API uncertainty bo‘lsa faqat official docs.

### 14. Keraksiz MCP/tool serverlarni ulab qo‘ymang
Keraksiz tool definitions/context ham overhead berishi mumkin.

### 15. “Deep audit everything” ishlatmang
Bunday prompt global scanning va ko‘p agent turnlariga olib keladi.

### 16. Scope’ni qat’iy yozing
Masalan:

```text
Only implement docs/LAUNCH_FINALIZATION.md.
Do not fix unrelated issues.
Stop after verification.
```

### 17. Katta loglarni paste qilmang
Claude terminaldan relevant errorni targeted ko‘rsin.

### 18. Screenshot/browser round-trip faqat UI debuggingda
Keraksiz vizual inspection agent turnlarini ko‘paytiradi.

### 19. Tugagach STOP
Automated tests + real manual QA’dan keyin kerak bo‘lsa alohida yangi sessionda keyingi taskni boshlang.
