# ErnestOS — Claude Code uchun yakuniy Top 50 implementation prompt

Claude Code, ushbu repositorydagi ErnestOS loyihasini to‘liq audit qil va quyidagi talablarni real kodda amalga oshir. Faqat tavsiya, reja yoki pseudo-code bilan to‘xtama. Avval mavjud holatni tekshir, keyin ishlaydigan kod, migratsiya va testlar bilan yakunla.

ErnestOS — Telegram Bot + Telegram Mini App + PostgreSQL asosidagi, foydalanuvchiga bugungi kunini boshqarishga yordam beradigan shaxsiy operatsion tizim. Mahsulotning vazifasi odamga ko‘proq ma’lumot ko‘rsatish emas, qaror charchog‘ini kamaytirib, ayni paytdagi eng muhim keyingi harakatni ko‘rsatishdir.

## Qat’iy product scope

- AI Assistant, Money, Notes, Contacts, Countdown, monetizatsiya, accountability partner, leaderboard va ijtimoiy tarmoq funksiyalarini qo‘shma yoki qaytarma.
- Jarima, 10 000 so‘mlik blokdan ochish, qo‘shimcha majburiy kanallar yoki har soat spam xabar yuborish kabi jazolovchi mexanizmlarni mutlaqo implement qilma.
- Mavjud working funksiyani sababsiz qayta yozma. Eng sodda, ishonchli va maintainable yechimni tanla.
- PostgreSQL productiondagi yagona ma’lumotlar bazasi bo‘lsin. Production uchun SQLite fallback, fake analytics, demo chart, fake currency yoki vaqtinchalik yolg‘on data bo‘lmasin.
- Bot va Mini App bir xil backend service/business logic, validation, permissions va hisoblash formulalaridan foydalansin.
- UZ, EN va RU tillari bir xil darajada qo‘llab-quvvatlansin. Ocean yangi user uchun default bo‘lsin; jami oltita tema: Ocean, Green, Modern Black, Rose, Pink va Rainbow.
- Default timezone `Asia/Tashkent`; default report vaqtlari 04:00 va 21:00.
- Yangi user uchun aynan oltita default habit: `Get up`, `5x namoz`, `Deep flow`, `Sport`, `Podcast`, `Read`. `Summary` habit emas.
- Bot asosiy menyusida aynan yettita band: `Home`, `Odatlar`, `Vazifalar`, `Maqsadlar`, `Sozlamalar`, `Taklif`, `Mini App`. `Turdim` asosiy menyu bandi bo‘lmasin.

## Ishlash tartibi

1. Repositoryni to‘liq o‘qi: `app.py`, `services.py`, `db.py`, `webapp`, testlar, README, deployment/config fayllari va mavjud migratsiyalar.
2. `git status`ni tekshir; userning mavjud unrelated o‘zgarishlarini o‘chirma yoki ustidan yozma.
3. Har talab uchun holatni `done / partial / missing / conflicting` deb ichki checklistda belgilab ol.
4. Avval data loss, auth, tenant isolation, scheduler duplicate va noto‘g‘ri hisob kabi P0 blokatorlarni yop; keyin core UX va P1larni bajar.
5. Har mavjud to‘g‘ri xulq uchun regression test, har tuzatilgan bug uchun bugni oldin reproduksiya qiladigan test yoz.
6. Stub, `TODO`, faqat UI ko‘rinishi, fake success yoki faqat client-side mutation qoldirma.
7. Katta talabni xavfsiz yakunlay olmasang, uni bajarilgandek ko‘rsatma; qolgan aniq riskni yakuniy hisobotda ochiq yoz.

---

## Top 50 majburiy talab

### 01. Mahsulotning bitta aniq vazifasini saqla

ErnestOSning core loopi quyidagicha bo‘lsin: `Home → Hozir → bajarish/belgilash → 21:00 yakun → 04:00 yangi kun rejasi → haftalik Reset`. Yangi modul qo‘shib navigatsiyani kengaytirma. Har asosiy ekranda bitta dominant action bo‘lsin; advanced imkoniyatlar `Batafsil` ichida ochilsin. User eng ko‘p ishlatadigan modulga ikki tapdan ortiq yurmasin.

**Qabul mezoni:** yangi user Home’ni ko‘rib, izohsiz “hozir nima qilishim kerak?” savoliga javob topadi; birinchi viewport dashboard devori emas.

### 02. Avval mavjud kodni dalil bilan bahola

Implementatsiyadan oldin amaldagi route, model, service, scheduler, bot handler, translation key va testlarni xaritalab ol. Oldingi auditdagi line number yoki o‘lchovlarni ko‘r-ko‘rona haqiqat deb qabul qilma; joriy kodda qayta tekshir. Mavjud working feature’ni takroran yaratma, qisman feature’ni yakunla.

**Qabul mezoni:** yakuniy hisobotda har 01–50 band `done / partial / blocked` holati, tegishli fayllar va testlar bilan ko‘rsatiladi.

### 03. Bot va Mini App uchun yagona domain logic yarat

Task, project, goal, habit, prayer, journal, daily plan, report, subscription va settings hisoblari bitta service/domain qatlamida bo‘lsin. Bot handler va HTTP endpointlar biznes formulasini takrorlamasin. Bir klientdagi amal ikkinchisida refreshdan keyin darhol bir xil natija ko‘rsatsin. `create/read/update/archive/restore/complete/reopen` semantikasi ikki muhitda bir xil bo‘lsin.

**Qabul mezoni:** parity contract testlari bir xil input uchun Bot va Mini App natijasi hamda DB holati bir xil ekanini isbotlaydi.

### 04. Onboardingni qisqa va progressiv qil

`/start` bosilganda uch tildagi uzun matnni birdan yuborma. Qisqa ErnestOS sarlavhasi va uchta til tugmasi chiqsin. Til tanlangach barcha keyingi matn faqat o‘sha tilda bo‘lsin. Asosiy onboarding: `til → majburiy kanal → tayyor Home`. Telefon majburiy emas; real recovery funksiyasi bo‘lmaguncha onboardingda so‘rama. Jins faqat Namoz birinchi marta ochilganda so‘ralsin va “Namoz holatlarini to‘g‘ri ko‘rsatish uchun” deb izohlansin. Onboarding DBda restart-safe saqlansin.

**Qabul mezoni:** yangi user 60 soniya ichida Homega kiradi; yarim yo‘lda chiqib qaytsa qolgan qadamdan davom etadi; til almashtirish onboardingni qayta boshlamaydi.

### 05. Kanal obunasini xavfsiz va insoniy boshqar

Majburiy kanal Bot, Mini App va himoyalangan backend endpointlarda bir xil enforcementga ega bo‘lsin. Membership uch holatli: `subscribed`, `not_subscribed`, `unknown/error`. Telegram timeout/xatosida fail-open ham, yolg‘on “obuna emassiz” ham bo‘lmasin; “Telegram javob bermadi — qayta urining” chiqsin. Kanalga qo‘shilib `Tekshirish` bosilgach `/start`ni qayta yozmasdan Home ochilsin. `chat_member` event statusni yangilasin; 2–5 daqiqalik TTL qayta tekshiruvi stale DB bayrog‘ini oldini olsin. Kanaldan chiqsa data o‘chmasin, lekin access yopilsin.

**Qabul mezoni:** timeout, join, leave, rejoin va stale-cache testlari Bot va Mini Appda bir xil ishlaydi; `unknown` hech qachon `is_subscribed=true` yozmaydi.

### 06. Birinchi sessiyada darhol foyda ko‘rsat

Onboarding tugagach majburiy uzun tutorial bermagin. Maksimum uch qisqa karta: `Hozir`, `oltita default habit`, `04:00/21:00 report`. Skip va Help orqali qayta ochish bo‘lsin. So‘ng userga bitta task qo‘shish yoki birinchi habitni belgilash kabi bitta aniq first win ber.

**Qabul mezoni:** onboardingdan keyingi birinchi foydali amal bir daqiqadan kam vaqt oladi; tur ikkinchi kirishda majburan takrorlanmaydi.

### 07. Default oltita habitni to‘g‘ri migratsiya qil

Yangi workspace’da faqat `Get up`, `5x namoz`, `Deep flow`, `Sport`, `Podcast`, `Read` yaratiladi. `Summary` Journal completion holati bo‘lsin, habit denominator/streak ichiga kirmasin. Eski userlarda system `journal/Summary` habit bo‘lsa, tarixiy journalni o‘chirmasdan xavfsiz arxiv/migratsiya qil. Migratsiya idempotent bo‘lsin.

**Qabul mezoni:** yangi userda aynan 6 default; migratsiyadan keyin eski journal tarixi saqlanadi va progress denominator noto‘g‘ri o‘zgarmaydi.

### 08. Home information architecture’ni soddalashtir

Home tartibi: `🔒 [Ism]ning shaxsiy tizimi` va mahalliy sana; ixtiyoriy kichik quote; bitta `Hozir` kartasi; Bugungi Top 3; bugungi habit/progress; eng yaqin bitta hodisa yoki deadline. To‘liq calendar, projectlar, goals, streaklar va statistikani asosiy viewportga tiqma; `Batafsil`da och. Bo‘sh quote kartasini ko‘rsatma. Qayta kirganda user ochgan `Batafsil` holati sessiya davomida saqlansin.

**Qabul mezoni:** kichik telefon viewportida Hozir va primary action scrollsiz ko‘rinadi; bo‘sh bo‘limlar foydasiz card/actionlarni chiqarmaydi.

### 09. `Hozir` kartasi bitta haqiqiy keyingi action bersin

Tanlash qoidasi deterministik bo‘lsin: user pin qilgan Top 1 → bugungi deadline/priority task → due core habit. Kartada nima uchun tanlangani (`Siz tanlagansiz`, `Bugun`, `Kechikkan`, `Yuqori muhimlik`) ko‘rinsin. `Boshqasini tanlash` imkoniyati bo‘lsin. Kartaning o‘ziga tap detailni ochsin; faqat alohida checkbox/button done qilsin. Bir vaqtda 0 yoki 1 Now action bo‘lsin.

**Qabul mezoni:** done bo‘lgach keyingi action chiqadi; user override’i algoritmdan ustun; kartaga tasodifiy tap taskni complete qilmaydi.

### 10. Daily Top 3 va Weekly Focus 3’ni ajrat

Daily Top 3 bugunning 0–3 eng muhim taski; Weekly Focus esa haftaning 0–3 missiyasi. User Daily Top 3ni o‘zi pin va reorder qila olsin; 4-chi task tanlansa mavjud slotni almashtirish taklif qilinsin. 04:00 report, Bot Home va Mini App Home bir xil daily Top 3ni ko‘rsatsin. Weekly Focus keyingi haftaga jimgina yo‘qolmasin, reviewda yopilsin yoki ko‘chirilsin.

**Qabul mezoni:** daily va weekly data modellari alohida; maksimal 3 constraint DB/service darajasida; reorder ikki klientda bir xil.

### 11. Global Quick Inbox’ni 5 soniyalik qil

Bot va Mini Appda global `Tez qo‘shish` bo‘lsin. Faqat title majburiy; default holat `Inbox/Tartiblanmagan`. Save’dan keyin “Vazifalar Inbox’iga saqlandi” va 10 soniyalik Undo ko‘rsat. Project, deadline, priority va description keyin qo‘shiladi. Noma’lum oddiy bot matnini avtomatik taskga aylantirma.

**Qabul mezoni:** ≤2 tap va ≤5 soniyada item saqlanadi; offline/error paytida matn yo‘qolmaydi; retry duplicate task yaratmaydi.

### 12. Kunlik progressni rost va tushunarli hisobla

Home’da bugungi foiz va kechagi taqqoslash (`🔺/🔻`) bo‘lsin, lekin formula shaffof va adolatli bo‘lsin. `Non-negotiable/Core`, `Target/Growth` va `Bonus`ni bir xil og‘irlikda aralashtirma: core completion alohida, growth alohida, bonus count alohida ko‘rinsin; umumiy foiz ishlatilsa hujjatlangan formula bo‘lsin. Due bo‘lmagan, paused yoki excused item denominatorga kirmasin. Trend faqat solishtiriladigan kunlar uchun chiqsin va ayblovchi qizil matn ishlatma.

**Qabul mezoni:** bir xil fixture Bot, Mini App, 04:00, 21:00 va statsda aynan bir foiz beradi; bonus core failure’ni yashirmaydi.

### 13. Task create/edit’ni progressive qil

Task yaratishda faqat title majburiy. `Batafsil` ichida description, sana, aniq vaqt, priority, project, goal, estimate va reminder bo‘lsin. Edit detail oynasidan barcha mavjud maydonlar o‘zgarsin. Invalid enum/sana jim ignore qilinmasin; tushunarli validation chiqsin. Uzun title kamida ikki qatorda ko‘rinsin.

**Qabul mezoni:** title-only create ishlaydi; to‘liq edit Bot/Mini Appda bir xil; server canonical obyektni qaytaradi; invalid input 422 va userga aniq matn beradi.

### 14. Task listlarini topish va boshqarish mumkin qil

Task ekranida `Bugun`, `7 kun`, `Keyin`, `Muddatsiz`, `Inbox`, `Kechikkan`, `Bajarilgan`, `Arxiv` bo‘limlari bo‘lsin. Search title bo‘yicha; filter project, goal, deadline va priority bo‘yicha; sort priority + deadline + stable ID bilan. Botda Prev/Next va Search; Mini Appda cursor pagination/incremental loading. Calendar item tap detail/editni ochsin.

**Qabul mezoni:** 1000 task fixtureda 900-chi task searchdan topiladi; pagination duplicate/skip qilmaydi; completed/archive restore mavjud.

### 15. Task vaqt, reminder va recurrence’ni soddalashtir

Optional exact time va estimate variantlari `15/30/60/120/240 min` bo‘lsin. Daily capacity oshsa bloklamasdan “Bugungi reja X soat — realmi?” deb warning ber. Reminder: vaqtida, 1 soat oldin, 1 kun oldin yoki off. Simple recurrence: daily, selected weekdays, weekly, monthly; to‘liq murakkab RRULE qurma. Next occurrence atomik va idempotent yaralsin.

**Qabul mezoni:** duplicate tap ikki recurrence instance yaratmaydi; timezone bo‘yicha keyingi sana to‘g‘ri; reminder user bajargach qayta kelmaydi.

### 16. Complete, archive va delete semantikasini bir xil qil

Complete — qayta ochiladigan status; Archive — ro‘yxatdan yashiradi, tarix va linkni saqlaydi; Permanent delete — faqat data management ichida. Complete uchun optimistic UI + 10 soniya Undo. Archive/delete uchun entity nomi bilan confirmation + Undo. Bosh element cardiga tasodifiy tap destructive action bo‘lmasin.

**Qabul mezoni:** task/project/goal/habit amallari ikki muhitda bir xil; Undo idempotent; tarixiy report/link yo‘qolmaydi.

### 17. Overdue va uzoq tanaffusni ayblovsiz tikla

Overdue task uchun `Bugun`, `Ertaga`, `Sana tanlash`, `Someday`, `Arxiv` actionlari bo‘lsin. Bulk select ishlasin. `Toza boshlash` hammasini bugunga tashlamasin; user Top 3ni tanlaydi, qolganini ko‘chiradi yoki arxivlaydi. 7+ kun inaktiv userga `Bugundan qayta boshlaymizmi?` Restart card chiqsin; tarix saqlansin, katta qizil ro‘yxat bilan qo‘rqitma.

**Qabul mezoni:** 10 kunlik fixture uch qadamda toza bugungi plan yaratadi; hech qanday pul jarimasi, blok yoki spam yo‘q.

### 18. Projectlar haqiqiy CRUD va lifecycle’ga ega bo‘lsin

Projectda name majburiy; description va deadline ixtiyoriy; status `active/done/paused/archived`. Detail ichida barcha linked tasklar va keyingi task ko‘rinsin. Edit, complete, reopen, pause, archive, restore ishlasin. Project arxivlanganda task.project_id ni NULL qilib tarixni uzma; yangi pickerda yashir, tarixda `Archived` label ko‘rsat.

**Qabul mezoni:** Bot va Mini App create/edit/complete/reopen/archive/restore bir xil; linked tasklar saqlanadi.

### 19. Goalsni ma’noli va tahrirlanadigan qil

Goalda title, optional target date, optional bir qator `Nima uchun bu muhim?`, status va progress mode bo‘lsin. Create/edit/complete/reopen/archive/restore ishlasin. Quick progress 0/25/50/75/100 faqat manual mode uchun. Kategoriyalar bo‘lsa hierarchy `Ultimate → Milestone → Tactical` saqlansin; flat yoki bir-biriga zid goal daraxti bo‘lmasin.

**Qabul mezoni:** target date calendar/reportga tushadi; why edit/export/delete qilinadi; archived goal restore qilinadi.

### 20. Goal → Project → Task bog‘lanishini ishlat

Project optional goalga, standalone Task optional goalga bog‘lana olsin. Cross-workspace link DB va service darajasida rad etilsin. Goal progress mode: `manual`, `task_count`, `milestone_weighted`; auto mode linked workdan hisoblasin, reopen natijani qaytarsin. Breadcrumb va goal detailda linked ishlar ko‘rinsin.

**Qabul mezoni:** 4 linked taskdan 1 done = 25% task_count; reopen = 0%; boshqa user goal IDsi bilan link yaratib bo‘lmaydi.

### 21. Calendar va optional Home bloklarini yengil saqla

Home’da to‘liq oylik calendar bo‘lmasin; faqat yaqin uch hodisa/deadline ichidan eng yaqin bittasi primary viewportda ko‘rinsin. To‘liq calendar alohida ekran/tugma orqali ochilsin. Quote optional; bo‘sh bo‘lsa card yo‘q, Settingsdan yashirish mumkin. Contacts yoki Countdown modulini qaytarma; existing birthday/important-date data bo‘lsa faqat buzmasdan optional event sifatida ko‘rsat, yangi CRM qurma.

**Qabul mezoni:** Home asosiy actionni pastga surmaydi; calendar item detailga olib boradi; out-of-scope modul paydo bo‘lmaydi.

### 22. Habit schedule va kategoriya mantiqini adolatli qil

Habit schedule: `daily`, `selected weekdays`, `weekly N`. Pause range va bugungi excused/rest state bo‘lsin. Due bo‘lmagan kun missed emas. Core/Growth/Bonus kategoriyasi UI va formula bilan mos bo‘lsin. Weekly N habitida N bajarilsa 100%; qolgan kunlar penalty emas.

**Qabul mezoni:** Sport weekly_target=3 uch sessiyada 100%; boshqa to‘rt kun streak/progressni buzmaydi; Bot/Mini App/report bir xil.

### 23. Habit tarixi o‘zgarmas bo‘lsin

Yangi habit qo‘shish, schedule o‘zgartirish, pause yoki archive qilish oldingi kun/oy foizini qayta yozmasin. `active_from/active_until`, effective-dated schedule yoki daily snapshotdan foydalan. Formula version/effective dates aniq bo‘lsin. Archive soft bo‘lsin va restore ishlasin.

**Qabul mezoni:** tarixiy chart qiymati keyingi konfiguratsiya o‘zgarishidan keyin byte-for-byte bir xil qoladi.

### 24. Habitni ma’noli, tahrirlanadigan va tartiblanadigan qil

Custom habit name, category, schedule, reminder, optional bir qator `Nima uchun?`, optional cue `Qaysi voqeadan keyin?` va minimum versionni edit qilish mumkin bo‘lsin. Drag-and-drop yoki accessible yuqoriga/pastga bilan reorder. System habit tap qilinsa “avtomatik” degan noaniq xabar emas, uni aynan nima complete qilishi tushuntirilsin.

**Qabul mezoni:** why/cue bo‘sh bo‘lsa flow uzaymaydi; reorder saqlanadi va ikki klientda bir xil; protected habit qoidasi tushunarli.

### 25. Streakni jazolovchi markaziy ko‘rsatkich qilma

Asosiy ko‘rsatkich rolling 7/30 kun consistency bo‘lsin; per-habit streak ikkinchi darajali. Bitta habit miss bo‘lsa qolgan streaklar saqlansin. Excused/rest day streakni buzmasin, lekin done sifatida qo‘shilmasin. Occasional recovery/freeze ishlatilsa aniq `protected` label va limit bilan bo‘lsin. 7/30/100 kabi milestone’da kichik sokin celebration mumkin, lekin shovqinli gamification yo‘q.

**Qabul mezoni:** bir missed day butun tarixni “nol”dek ko‘rsatmaydi; recovery statistikani soxtalashtirmaydi.

### 26. `Get up / Turdim` vaqt mantiqini to‘g‘ri qil

Target wake time user sozlamasida bo‘lsin. Qabul oynasi default `target − 1 soat` dan `target + 1 soat`gacha; mahsulotda boshqa window mavjud bo‘lsa bitta canonical configdan foydalan. 05:00 target uchun 00:30 qabul qilinmasin. Botda inline `Turdim` va xavfsiz natural sinonimlar UZ/EN/RUda ishlasin; bir kunda duplicate log bo‘lmasin. `Turdim` main menu emas, Home yoki Odatlar ichida.

**Qabul mezoni:** boundary/timezone/duplicate testlari; oldingi sleep-dayga noto‘g‘ri yozilmaydi.

### 27. `5x namoz` semantikasini rost qil

Bomdod, Peshin, Asr, Shom, Xufton alohida qayd qilinsin. `5x namoz` faqat beshta valid holat qayd etilib product qoidasi bajarilganda done. Prayer score va `5/5 qayd` alohida ko‘rsatilsin; 2.5 ball `5x done` emas. Gender faqat birinchi Prayer open’da izoh bilan so‘ralsin. `Uzrli` neutral status: denominator/streakni buzmaydi, done ham emas. Future date rad etilsin; cheklangan backdate `late entry` bo‘lsin. Gender o‘zgarishida effective-from yoki aniq recalc siyosati ishlasin. Diniy ranking, ayblov yoki raqobat bo‘lmasin.

**Qabul mezoni:** 3/5 yoki 2.5/5 holat done emas; excused neutral; kelajak sana 422; bot va app statistikasi bir xil.

### 28. Journalni yengil, xavfsiz va moslashuvchan qil

Summary habit bo‘lmasin. Default 3 asosiy savol, qolganlari optional; user savolni yoqib/o‘chirishi yoki cheklangan custom savol qo‘shishi mumkin. Mood va energy `past/o‘rta/yuqori` alohida 2 soniyalik check-in bo‘lsin. Form autosave draft; app yopilsa matn qoladi. History search/pagination, edit va delete ishlasin. Journal delete completion/statistikani atomik qayta sync qilsin.

**Qabul mezoni:** faqat mood saqlash mumkin; active savollar to‘liq javoblanganda Journal complete; delete’dan keyin ghost Summary/log yo‘q.

### 29. `Minimum kun` rejimini insoniy qil

User o‘zi ongli ravishda Minimum kunni yoqa olsin: bitta asosiy task, namoz holati va user tanlagan minimum habitlar. Avtomatik yoqma. Normal target yashirilmasin; report va statsda `minimum day` labeli hamda optional sabab saqlansin. Bu cheat yoki perfect day hisoblanmasin, lekin userning tizimga qaytishini yengillashtirsin.

**Qabul mezoni:** normal va minimum kunlar statistikada ajraladi; mode ayblamaydi va core tarixni soxtalashtirmaydi.

### 30. Weekly Review bilan siklni yop

Haftalik Reset uch daqiqadan kam bo‘lsin: yutuq; habit/progress trend; Inbox/overdue triage; nima ishlamadi va nega; keyingi haftaning 0–3 Focus’i. O‘tgan Weekly Focus jimgina yo‘qolmasin: done, carry, archive yoki drop qarori bo‘lsin. Skip mumkin va ketma-ket spam reminder bo‘lmasin.

**Qabul mezoni:** review natijasi keyingi hafta Homega chiqadi; unresolved task/focus tarixda saqlanadi.

### 31. Bajarilmagan ish sababini yengil yig‘ va AI’siz reaksiya ber

21:00da faqat qolgan Core habit yoki Daily Top task uchun bir tap sabab: `vaqt yetmadi`, `energiya`, `unutdim`, `sog‘liq`, `reja o‘zgardi`, `boshqa`. Free text optional. AI Assistant qo‘shma. Faqat uch deterministic coaching turi: ketma-ket miss; shaxsiy rekord; 14 kun goalga bog‘liq activity yo‘qligi. Haftasiga maksimum 2 prompt, Settingsdan off.

**Qabul mezoni:** fixture trigger/no-trigger deterministic; causal da’vo qilinmaydi; sensitive sabab admin logga chiqmaydi.

### 32. 04:00 va 21:00 reportlarni qisqa va action-oriented qil

04:00 default report: kechagi qisqa yakun + bugungi Hozir/Top 3 + bugungi due habitlar + mavjud bo‘lsa yaqin muhim hodisa. 21:00: bugungi natija, qolgan 1–3 muhim action va bitta optional reflection. Bo‘sh section yuborilmasin; bir report bir marta; Bot Home bilan bir xil formula. Xabar birinchi ekranda primary actionni ko‘rsatsin, qolgan detail Mini App deep-linkida.

**Qabul mezoni:** duplicate 0; empty section 0; report Telegram limitiga yaqinlashmaydi; stats Home bilan mos.

### 33. Notification va report nazoratini userga ber

Default `Asia/Tashkent`, morning 04:00 va evening 21:00 saqlansin. Settingsda timezone, morning/evening on/off, vaqt va quiet hours bo‘lsin. Habit/task reminder faqat user tanlaganlar uchun; bajarilgan itemga reminder kelmasin. Bir hodisa Bot va Mini App orqali duplicate notification bermasin.

**Qabul mezoni:** default user 04:00/21:00 oladi; o‘chirilgan report kelmaydi; quiet hours buzilmaydi; timezone testlari to‘g‘ri.

### 34. Bot asosiy menyu va tayanch komandalarni final qil

Persistent bot menu aynan 7 canonical band: Home, Odatlar, Vazifalar, Maqsadlar, Sozlamalar, Taklif, Mini App. `Turdim` sakkizinchi band emas. `/help`, `/cancel`, `/privacy`, `/export`, `/delete_account`, `/settings`, `/home` ishlasin va BotFather command list bilan mos bo‘lsin. `/cancel` istalgan flowdan data yozmasdan chiqarsin.

**Qabul mezoni:** UZ/EN/RU menu snapshot aynan 7; barcha command uch tilda natural javob beradi.

### 35. Bot Home, unknown input va parity’ni userga tushunarli qil

Bot Home ham Mini App kabi Hozir va Daily Top 3ni birinchi ko‘rsatsin; uzun detail uchun Mini App tugmasi bersin. Noma’lum matn jim Homega qaytmasin va avtomatik Inbox task bo‘lmasin: “Buni tushunmadim. Menyudan tanlang yoki /help” desin. Botda mavjud bo‘lmagan murakkab ekran uchun aniq Mini App deep-link bo‘lsin, lekin underlying amal/service parity saqlansin. Uzun listlarda search va Prev/Next ishlasin.

**Qabul mezoni:** random input data mutatsiya qilmaydi; Botdan Appga deep-link to‘g‘ri screenni ochadi; 31-item listning oxiriga yetiladi.

### 36. Mini App navigatsiyasi va Telegram-native xulqni yaxshila

Mini App bottom nav 5 destinationdan oshmasin: `Home`, `Odatlar`, `Vazifalar`, `Maqsadlar`, `Ko‘proq`; `Sozlamalar` va `Taklif` Ko‘proqda. Bu botdagi 7 menu scope’ni buzmaydi. Telegram safe-area/content inset, viewport change, themeChanged, BackButton va formda MainButton/fallback ishlasin. `ready()` essential UI yuklangach chaqirilsin. Unsaved form bo‘lsa closing confirmation, aks holda keraksiz confirmation yo‘q. InitData URL/query/logga chiqmasin.

**Qabul mezoni:** iOS, Android, Desktopda header/bottom bar kesilmaydi; Back stack to‘g‘ri; unsaved draft tasodifan yo‘qolmaydi.

### 37. UZ/EN/RU va oltita temani to‘liq professional qil

Har visible UI, bot matni, validation, empty/error, reminder, date/plural va report translation key orqali chiqsin. Uzbek ekranda English/Russian, English ekranda Uzbek, Russian ekranda Uzbek/English aralashmasin. Canonical glossary yarating. Til yoki tema o‘zgarganda reloadsiz darhol yangilansin. Tema nomi texnik ID emas, lokal nom + rang preview bilan; Ocean default; olti tema light/dark kontrastni saqlasin.

**Qabul mezoni:** missing translation CI’ni yiqitadi; barcha screen snapshotlarida mixed-language topilmadi; tanlov qayta kirishda saqlanadi.

### 38. Accessibility va responsive UI’ni release talabi qil

`user-scalable=no` va `maximum-scale=1`ni olib tashla. Interaktiv `div`larni semantic button/inputga aylantir; label, aria-label/pressed/selected, disabled/loading va visible focus ring. Asosiy touch target 44×44pxga yaqin, hech biri WCAG minimumdan kichik bo‘lmasin. Normal text contrast kamida 4.5:1; status faqat rangga tayanmasin. 200% zoom, uzun title, keyboard, screen reader va safe-area’da content/action yo‘qolmasin.

**Qabul mezoni:** accessibility auditda unlabeled interactive control 0; 200%da gorizontal scroll va yo‘qolgan Save/Delete yo‘q.

### 39. Loading, error, offline va draft recovery’ni izchil qil

Generic “Xatolik”ning o‘zi yetmaydi. Nima bo‘ldi, amal saqlandimi, nima qilish kerakligini ayt; Retry va Botga qaytish ber. Mutatsiyada faqat bosilgan button loading/disabled bo‘lsin, double submit bloklansin. Form error/offline’da yopilmasin; draft local/persistent saqlansin. Optimistic UI faqat xavfsiz reversible amallarda; server rad etsa rollback + izoh. Full-app rerender scroll/focusni yo‘qotmasin.

**Qabul mezoni:** POST timeoutda matn qoladi; Retry bitta obyekt yaratadi; 50-item list toggle scrollni saqlaydi; boot error’dan appni yopmasdan tiklanadi.

### 40. Privacy, feedback va support matnini rost qil

“To‘liq himoyalangan”, “hech kim, hatto admin ham kira olmaydi” kabi isbotlanmagan mutlaq iboralarni olib tashla. Qisqa matn: `🔒 Ma’lumotlaringiz boshqa foydalanuvchilardan ajratilgan va ErnestOS ishlashi uchun zarur texnik jarayonlarda qayta ishlanadi.` Privacy sahifada yig‘iladigan data, maqsad, saqlash, export va delete tushuntirilsin. Taklif flowi `Muammo / Taklif / Tushunmadim`, optional izoh; screen, app version va til PII’siz avtomatik biriktirilsin; qabul raqami qaytsin.

**Qabul mezoni:** UZ/EN/RU privacy ma’nosi bir xil; feedback yuborilgani tasdiqlanadi; phone/journal text avtomatik admin logga bormaydi.

### 41. Telegram initData va sessiya autentifikatsiyasini mustahkamla

Telegram initData signature, `auth_date`, hash/signature va bot IDni serverda rasmiy algoritm bilan validate qil. Clientdan kelgan telegram_id/workspace_idga ishonma. InitData faqat qisqa sessiya ochish uchun: 5–15 daqiqalik max age; server session/token rotate/revoke; export uchun bir martalik nonce. Onboarding tugamagan user `/api/me/onboarding status`dan boshqa mutatsiyada 409 olsin. Deleted/blocked session darhol bekor bo‘lsin.

**Qabul mezoni:** forged, expired, replayed va boshqa bot initData rad; 20 daqiqalik stale initData yangi sessiya ochmaydi; token log/URLda yo‘q.

### 42. Tenant isolationni PostgreSQL darajasida kafolatla

Har user/workspace data querysi tenant-scoped. Tenant jadvallarida PostgreSQL RLS va `WITH CHECK`; app role BYPASSRLSsiz. Transaction boshida workspace context o‘rnatilsin. Child relationlarda composite FK/ownership invariant: HabitLog-Habit, Task-Project/Goal va boshqalar cross-workspace bo‘la olmasin. Hardcoded Ernest Telegram ID/data yo‘q.

**Qabul mezoni:** Workspace A B ning known IDsi bilan SELECT/UPDATE/DELETE/link qila olmaydi; DBning o‘zi rad etadi; fuzz ownership testlari bor.

### 43. Input, output va browser security’ni yop

HTTP body limit, field max length, list/dict item cap, strict enum va date policy bo‘lsin. Per-user/per-IP rate limit: oddiy read, mutation va og‘ir export uchun alohida budget; 429 + Retry-After. Barcha bot/admin HTML user kontenti `html.escape`/safe renderer orqali; Unicode/HTML fuzz test. CSP nonce/hash yoki external static files; `nosniff`, `Referrer-Policy`, Telegram WebViewga mos `frame-ancestors`. PII, secret va initData loglanmasin.

**Qabul mezoni:** oversized body 413; invalid enum 422; abuse 429; `A & B < C` Telegram parse xatosiz literal; security header testi pass.

### 44. Database migration, constraint, transaction va vaqt invariantlarini tuzat

Boot-time ad-hoc DDLni Alembic versioned migrationga almashtir. Upgrade/downgrade, backfill, index, FK, UNIQUE va CHECKlar bo‘lsin: enumlar, progress 0..100, Top slot 1..3, member_no unique/identity. TIMESTAMPTZ ishlat; completed/due/report day user timezone bo‘yicha. `MAX()+1` member number yo‘q. Journal delete/sync va bog‘liq hisoblar bitta transactionda. Existing dirty data migratsiya oldidan aniqlanib deterministic tuzatilsin.

**Qabul mezoni:** clean va old schema bir xil final schema hash; 100 parallel registration unique; Tashkent 00:30 task to‘g‘ri local reportga tushadi; rollback rehearsal pass.

### 45. Data ownership, export, delete, soft-delete va backup’ni yakunla

Settingsdan user barcha datasini schema-versioned JSON + CSV ZIP sifatida export qila olsin; qisqa muddatli, bir martalik signed URL yoki Telegram file; ownership va expiry tekshirilsin. Account delete ikki bosqichli, export taklifi va 7 kunlik undo/grace; report va session darhol to‘xtasin; keyin documented hard delete/anonymization. Entitylar soft-delete/Trash/restore. Encrypted automatic backup, retention va real restore drill bo‘lsin.

**Qabul mezoni:** export faqat o‘z data; link 10 daqiqada eskiradi; delete recipientdan darhol chiqaradi; staging restore’da random user count/hash mos.

### 46. Bot worker, scheduler va delivery’ni duplicate/race’dan himoya qil

API, bot va scheduler bir processda qolsa WEB_CONCURRENCY=1 qat’iy enforce va leader lock; afzal arxitektura alohida API, bitta bot consumer va bitta scheduler worker. PostgreSQL advisory lock/leader election. `drop_pending_updates=False`; update/callback idempotent. Stateful flow DB/Redis yoki signed state bilan restart-safe, per-user serial. Report outbox atomik claim, unique key `(workspace, report_type, local_date)`, attempts, lease, delivered_at, next_attempt. 429 Retry-After, 5xx backoff+jitter, blocked user cleanup, outage catch-up policy.

**Qabul mezoni:** 2 scheduler instance bir reportni bir marta yuboradi; 2 daqiqalik bot outage update’larni yo‘qotmaydi; bitta user xatosi qolgan recipientlarni to‘xtatmaydi.

### 47. DB va UI performance’ni bounded qil

Async handler ichida blocking DB I/O qoldirma: SQLAlchemy async/asyncpg yoki bounded threadpool. Pool size/overflow/timeout/recycle hosting limitiga mos. Project counts, stats va streak N+1/day-loop querylarini set-based aggregatega o‘tkaz. Home uchun bounded read model. Collection endpointlarida stable cursor, `limit<=100`, search/filter; export streaming/job sifatida paginationdan mustasno. Indexlarni query plan bilan tekshir.

**Qabul mezoni:** 1 va 100 project deyarli bir xil bounded query count; 365 kun stats query count periodga bog‘liq emas; 1000 task first paint va search target ichida; DB slow test event loopni muzlatmaydi.

### 48. Health, logging, metrics va incident ko‘rinishini yarata ol

`/health/live` process; `/health/ready` DB, migration head, bot worker/scheduler heartbeat va zarur config; authlangan `/health/detail`. JSON structured logs va request_id/update_id/job_run_id. RED metrics, DB pool, Telegram membership latency/cache, report sent/failed/duplicate, scheduler heartbeat, backup failure. PII redaction va error tracking. Alertlar 5xx spike, auth reject spike, scheduler/report/backup failure uchun.

**Qabul mezoni:** DB uzilganda live=200, ready=503; synthetic critical failure 5 daqiqada alert; phone/journal/initData loglarda yo‘q.

### 49. Reproduktiv test, staging va release gate yarat

Pinned production/dev dependencies va bitta buyruqli test setup. CI: compile, lint/format, type check imkon qadar, PostgreSQL integration, Alembic up/down, pytest, coverage, translation-key, HTML fuzz, ownership/RLS, scheduler race/outbox, timezone/history, browser E2E, accessibility. Test soni vanity metric emas, ammo mavjud suite qisqarmasin va har Top 50 band uchun meaningful regression bo‘lsin; amaliy jihatdan 120+ testga chiqsa, faqat mazmunli bo‘lsa qabul. Productionga o‘xshash staging, deploy+migration+rollback rehearsal va kill switch.

**Qabul mezoni:** clean runnerda hammasi pass; P0 regression merge/deployni bloklaydi; failed testni yashirmaysan yoki delete/skiplama.

### 50. Public launchdan oldingi real pilot va yakuniy handoffni bajar

Kamida 5–10 real, jamoaga aloqasi bo‘lmagan user bilan iOS, Android va Telegram Desktopda hintsiz test: onboarding; join/check; task Quick Inbox; Daily Top 3; habit; prayer; overdue reschedule; weekly review; language/theme; offline/retry; Bot↔Mini App parity; export/delete. 72 soat nazoratli pilot gate: data loss/cross-user leak=0; duplicate 04:00/21:00=0; kritik P0=0; kanalga qo‘shilgach muvaffaqiyatli kirish >95%; backup restore va rollback PASS. Bu gate bajarilmasa public deb da’vo qilma, controlled beta deb belgilagin.

**Yakuniy handoff majburiy:**

- 01–50 checklist: `done / partial / blocked`;
- har band bo‘yicha o‘zgargan fayllar va qisqa izoh;
- barcha migrationlar va rollback yo‘li;
- bajarilgan commandlar va test natijasi: passed/failed/skipped sonlari;
- manual QA flow va uch platforma natijasi;
- performance/security o‘lchovlari;
- qolgan real risklar va aynan nima sababli qolgan;
- public launch uchun yakuniy `GO / CONTROLLED BETA / NO-GO` qarori.

Hech qachon test o‘tmagan bo‘lsa “buglarsiz”, audit qilinmagan bo‘lsa “to‘liq xavfsiz”, qisman bajarilgan bo‘lsa “done” deb yozma. Eng muhim tamoyil: ErnestOS userni jazolamasin, qo‘rqitmasin yoki ko‘proq boshqaruv yukini bermasin; unga bugungi eng muhim keyingi qadamni ko‘rsatib, xatodan keyin oson qaytishiga yordam bersin.

## Rasmiy standartlar

- Telegram Mini Apps: https://core.telegram.org/bots/webapps
- Telegram Bot API: https://core.telegram.org/bots/api
- PostgreSQL Row Security: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
- PostgreSQL Advisory Locks: https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS
- Alembic migrations: https://alembic.sqlalchemy.org/
- OWASP API Security: https://owasp.org/API-Security/
- OWASP Logging: https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
- WCAG 2.2: https://www.w3.org/WAI/WCAG22/quickref/
