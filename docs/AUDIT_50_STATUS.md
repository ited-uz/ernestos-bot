# 50 bandli audit — holat (v13.0)

Manba: «ErnestOS — 50 ta muammo, tuzatish yo‘llari va launch rejasi»
(2026-10-05). Tekshirilgan kod: `ErnestOS-v15.zip` ichidagi v12.2 —
repodagi kod bilan bir xil ekani solishtirib tasdiqlandi.

**Belgilar:** ✅ tuzatildi va test bilan yopildi · 🟡 qisman (nima qolgani
yozilgan) · ⏭ keyingi bosqich (P2 mahsulot imkoniyati, launchni ushlab
turmaydi — auditning o‘zi shunday tavsiya qiladi).

**Yakun:** 36 ✅ · 6 🟡 · 8 ⏭ (jami 50). P0 — 7/7 ✅. P1 — 30 tadan
27 ✅, 3 🟡. Ochiq qolganlarning barchasi P2.

Testlar: `python -m pytest -q` → **1007 o‘tdi** (bazada 978 edi);
`node tests/frontend_release.cjs` va yangi `node tests/frontend_audit50.cjs`
o‘tdi; `pyflakes` toza.

## 1-bosqich — ma’lumot yaxlitligi (P0)

| # | Holat | Nima o‘zgardi | Dalil (test) |
|---|---|---|---|
| 25 | ✅ | AI to‘ldirgan javoblar belgilangan taklif: autosave ham, mahalliy qoralama ham yozilmaydi. «Qabul qilish» (yoki maydonni tahrirlash / Saqlash) dan keyingina saqlanadi; «Bekor qilish» eski holatga qaytaradi. | `frontend_audit50.cjs` #25 |
| 27 | ✅ | AI uchun yozilgan matn so‘rovdan oldin qurilmada saqlanadi; xato bo‘lsa, muharrir aynan o‘sha matn bilan qayta ochiladi. Muvaffaqiyatdan keyin tozalanadi. | `frontend_audit50.cjs` #27 |
| 31 | ✅ | Kundalik ochilgan kunga bog‘lanadi (`journalDay`); autosave, Saqlash va kayfiyat shu sanani yuboradi. Kun almashsa, «{sana} kuni uchun yozilmoqda» ko‘rinadi. | `test_journal_save_keeps_the_day_it_was_written_for`, `frontend_audit50.cjs` #31 |
| 32 | ✅ | Ma’lumotni o‘chirish va akkauntni o‘chirishdan oldin autosave to‘xtatiladi, uchayotgan so‘rov kutiladi; keyin shu akkauntning barcha kundalik qoralamalari, AI matni va ovoz yozuvi o‘chiriladi. Boshqa akkaunt qoralamasiga tegilmaydi. | `frontend_audit50.cjs` #32 |
| 44 | ✅ | `Debt.amount` — asl summa, o‘zgarmaydi. Qisman to‘lov — `debt_payments` jadvalida alohida (summa, sana). Qoldiq hisoblanadi. Eski yozuvlarda yo‘qolgan tarix taxmin qilib yaratilmaydi. Eksportda ham bor. | `test_partial_debt_payments_keep_the_original_and_the_history` |
| 45 | ✅ | Qoldiqdan katta summa 422 `paid_exceeds_remaining`, hech narsa o‘zgarmaydi; teng summa qarzni yopadi. Ilova ham oldindan tekshiradi. | `test_overpaying_a_debt_is_refused_and_changes_nothing` |
| 46 | ✅ | O‘chirish tasdiq so‘raydi → arxivlaydi → «Bekor qilish». «O‘chirilganlar» ro‘yxatidan qaytarish yoki alohida tasdiq bilan butunlay o‘chirish. | `test_deleting_a_debt_archives_it_and_can_be_undone` |

## 2-bosqich — asosiy hisob va bajarish oqimi

| # | Holat | Nima o‘zgardi | Dalil |
|---|---|---|---|
| 1 | ✅ | «Hozir»: vaqtli ish 60 daqiqa qolguncha bajarsa bo‘ladigan ishni siqib chiqarmaydi; keyinroq kelsa «Keyingi belgilangan» qatorida ko‘rinadi. Boshqa ish bo‘lmasa, o‘zi tavsiya qilinadi. | `test_now_does_not_offer_an_evening_meeting_in_the_morning`, `test_now_offers_a_later_timed_task_when_nothing_else_waits` |
| 3 | ✅ | Vazifa taymeri tugasa vazifa yopilmaydi: vaqt yoziladi, ilova va bot «Vazifa tugadimi?» deb so‘raydi (botda «✅ Ha, tugadi» tugmasi). Odat — avvalgidek vaqt bilan belgilanadi. | `test_a_timed_task_records_time_and_waits_for_the_person` |
| 7 | ✅ | «Bugun bajarilgan ishlar» (kechikkan va sanasiz ham) va «bugungi va’dalar» alohida: Qadam va Bosh sahifadagi «+N bugun bajarildi». | `test_steps_count_late_work_and_separate_started_from_finished` |
| 11 | ✅ | `focus_is_done()` — maqsad holatining yagona qoidasi; Qadam saqlangan bayroq o‘rniga shuni o‘qiydi. Qayta ochish ham mos. | `test_a_week_goal_done_through_its_task_counts_everywhere` |
| 21 | ✅ | Bugun uchun tanlash deadline’ni o‘zgartirmaydi (sanasiz vazifa ham sanasiz qoladi). **Eski xatti-harakat o‘zgardi** — eski test yangi qoidaga moslandi. | `test_picking_a_task_for_today_keeps_its_deadline`, `test_picking_a_task_for_today_does_not_date_it` |
| 22 | ✅ | «Shu hafta» → «7 kundan keyin · {sana}». | `test_the_seven_day_option_says_what_it_does` |
| 23 | ✅ | `/api/quick` `preview` rejimi; jamoa yo‘li shu parser natijasini yuboradi. | `test_quick_preview_parses_without_saving`, `frontend_audit50.cjs` #23 |
| 26 | ✅ | AI so‘rovlari uchun 155 s kutish (server chegarasi 150 s). Ovozda bir xil so‘rov kaliti bilan qayta yuborish takror amal yaratmaydi. Topshiriq-ID bilan holat so‘rash qo‘shilmagan — kerak bo‘lmadi. | `frontend_audit50.cjs` #26 |
| 33 | ✅ | Qadam kundalikni `journal_is_written()` bilan tekshiradi; bo‘sh javoblar hisoblanmaydi. | `test_an_empty_journal_is_not_a_written_step` |
| 34 | ✅ | Odatga vaqt tanlanganda global holat ko‘rsatiladi; o‘chiq bo‘lsa, shu yerning o‘zida yoqish tugmasi. Serverdagi qoida o‘zgarmadi (standart — o‘chiq). | UI; avtomatik test yo‘q |
| 35 | ✅ | Odat eslatmasi oynasi 5 → 30 daqiqa; `reminder_sent_at` bir martalikni ta’minlaydi, 30 daqiqadan eski eslatma yuborilmaydi. | `test_habit_reminders_are_off_by_default_and_fire_in_one_window` (7 daqiqa kechikish holati qo‘shildi) |
| 39 | ✅ | Davomiylik o‘zgarishi keyingi sessiyaga tegishli; ishlab turgan/pauzadagi sessiya (jamoadoshlarniki ham) o‘zgarmaydi. O‘chirish faqat o‘z sessiyangizni to‘xtatadi. | `test_changing_the_timer_length_leaves_the_running_session_alone` |
| 42 | ✅ | Maqsadsiz haftada «Maqsad qo‘shish» qatori, maxrajga kirmaydi. | `test_no_week_goal_is_not_an_unmet_step` |
| 49 | ✅ | Bugun o‘tib ketgan vaqt: ilova «Bugunmi / ertagami?» so‘raydi, javobsiz saqlamaydi. Bot taxminni ochiq yozadi va qanday tuzatishni aytadi (alohida tugma yo‘q). | `test_a_bare_time_that_has_passed_is_asked_about`, `frontend_audit50.cjs` #49 |

## 3-bosqich — kundalik foydalanish

| # | Holat | Nima o‘zgardi | Dalil |
|---|---|---|---|
| 4 | ✅ | «Taymersiz bajardim: N daqiqa» — `manual` belgisi bilan alohida; ishlab turgan taymer ustiga yozilmaydi; bo‘laklar odat maqsadiga qo‘shiladi. | `test_work_done_without_the_timer_can_be_logged_once` |
| 5 | ✅ | Setup bitta odat belgilangan holda boshlanadi, ko‘pi bilan 3 ta. | `test_setup_starts_with_one_habit_and_allows_three`, `test_setup_left_as_it_is_adds_a_single_habit` |
| 8 | ✅ | «Odatlar seriyasi» 🔥 va «Izchil kunlar» 📈 — nomi, belgisi va qoida matni alohida. | UI; avtomatik test yo‘q |
| 10 | ✅ | Har bir qadamda `ok` (boshlandi) va `complete` (to‘liq); 1/10 «boshlandi» deb ko‘rinadi. | `test_steps_count_late_work_and_separate_started_from_finished` |
| 13 | ✅ | Jamoa vazifalari shaxsiylar bilan bitta navbatda (kechikkan va bugungi); karta jamoa nomini ko‘rsatadi, belgi faqat sizniki. | `test_now_puts_shared_work_in_the_same_queue` |
| 14 | 🟡 | Taqvimli takrorlanish: oldingisi belgilanmasa ham yangi kunda paydo bo‘ladi, takror nusxa yo‘q; eng oxirgi o‘tkazib yuborilgani ochiq, oldingilari arxivga. **Qolgani:** «bajargandan N kun keyin» turi qo‘shilmadi. | `test_a_daily_task_appears_even_if_yesterdays_was_not_ticked`, `test_older_misses_of_a_series_are_archived_not_piled_up` |
| 15 | 🟡 | Yangi standart rejim: eng muhim 3 tasi bugun, qolgani 6 kunga; har rejim oldindan ko‘rsatiladi. «Hafta» rejimi endi ertadan boshlanadi. **Qolgani:** har bir vazifa bo‘yicha «Hali kerakmi?» saralash; band kunlar va davomiylikni hisobga olish. | `test_focus_reset_puts_three_on_today_and_shows_it_first` |
| 16 | 🟡 | Hafta maqsadlari ostida «Haftalik tahlil» qatori (jumadan belgilanadi); «keyingi fokus» takrorsiz keyingi hafta maqsadiga aylanadi. **Qolgani:** yukni kamaytirish / to‘siqli vazifani o‘zgartirish takliflari. | `test_the_review_is_reachable_and_feeds_next_weeks_goal_once` |
| 17 | ✅ | Ko‘chirilgan maqsad eski haftada «ko‘chirildi» bo‘lib qoladi (`carried_to/carried_from`); 2+ marta surilsa «kichikroq qadamga bo‘ling». | `test_carrying_a_goal_keeps_its_trace_in_the_old_week` |
| 19 | ✅ | 20 amaldan keyin ham belgilash ochiq (API va bot): odat/vazifa/jamoa belgisi, namoz, kundalik, uyg‘onish, taymer. Yangi qo‘shish va tahrirlash — kanal bilan. | `test_past_the_free_run_ticking_done_work_stays_open` |
| 24 | ✅ | Tez odat matnidagi kunlar jadvalga aylanadi va saqlashdan oldin chiplar bilan ko‘rsatiladi. | `test_a_quick_habit_line_with_days_is_scheduled_on_those_days` |
| 28 | ✅ | Yozuv javob kelguncha xotirada (10 daqiqagacha) saqlanadi; «Shu yozuvni qayta yuborish» o‘sha audio va o‘sha kalit bilan. | `frontend_audit50.cjs` #28 |
| 29 | ✅ | Mini App AI kartasida maydonni qo‘lda tahrirlash (butun reja qayta tekshiriladi, revision oshadi — eski javob ustiga yozolmaydi) va savolga matnli javob. | `test_one_field_of_a_proposal_is_fixed_by_hand_and_the_rest_kept`, `frontend_audit50.cjs` #29 |
| 30 | ✅ | Qoralama qaysi saqlangan versiya ustiga yozilganini eslaydi (`_base`); boshqa qurilmada yangilangan bo‘lsa, eskisi tashlanadi. Saqlangan maydonlar qoralamadan chiqadi. | `frontend_audit50.cjs` #30 |
| 47 | ✅ | Har reset `reset_logs` ga yoziladi; 7 kun ichida bir bosishda qaytariladi. Keyin o‘zingiz o‘zgartirgan vazifa tegilmaydi va ziddiyat sifatida ko‘rsatiladi. | `test_a_reset_can_be_undone_without_overwriting_later_edits` |
| 50 | ✅ | `version.py` — yagona manba (v13.0); `/api/version`, `/api/me`, Sozlamalar pastida versiya va build; arxiv nomi shundan. `docs/AMALDAGI_QOIDALAR.md` — qisqa amaldagi qoidalar, raqamlari test bilan kodga bog‘langan. README sarlavhasi yangilandi, eski bo‘limlar «tarixiy» deb belgilandi. | `test_the_rules_document_matches_the_code` |

## 4-bosqich — keyingi rivojlantirish (P2)

| # | Holat | Izoh |
|---|---|---|
| 2 | ⏭ | Kun sig‘imi / reja davomiyligi ogohlantirishi — yangi maydonlar va UX qarori kerak. |
| 6 | 🟡 | Taymerli odatda qisman vaqt endi ko‘rinadi (#4, #40). Miqdorli odat (12/20 bet) va minimal variant — yo‘q. |
| 9 | 🟡 | Foiz tagida «bu reja bajarilishi, kun qiymati emas» va reja hajmi yozildi. «Asosiy natija» alohida ko‘rsatilmadi. |
| 12 | ⏭ | «Javob kutilyapti / boshqa ishga bog‘liq» holati — model va «Hozir» o‘zgarishi kerak. |
| 18 | ⏭ | Umumiy offline navbat. Hozircha: kundalik qoralamasi, AI matni va ovoz qayta yuborish saqlanadi. |
| 20 | ⏭ | Agent orqali vazifani yopish / odatni belgilash — xavfsizlik (noto‘g‘ri vazifani yopish) sinovi bilan alohida qilinishi kerak. |
| 36 | ⏭ | Eslatmani surish (snooze). |
| 37 | ⏭ | Umumiy sokin soatlar. |
| 38 | ⏭ | Setup’da «Odatda qachon turasiz?» savoli; standart hali ham 05:00. |
| 40 | ✅ | «Sessiyani yakunlash» — ishlangan vaqt saqlanadi; «To‘xtatish» — bekor. `test_finishing_a_session_early_keeps_the_time_stopping_does_not` |
| 41 | ⏭ | Ish–tanaffus (50/10) rejimi. |
| 43 | 🟡 | Qadam o‘tgan kunlarni SQL’da yig‘adi, kundalik matnlarini yuklamaydi. Ko‘p yillik sun’iy tarixda o‘lchov qilinmadi. |
| 48 | ✅ | Loyiha foizi «vazifalar soni bo‘yicha — ish hajmi emas» deb yozildi. |

## Xatti-harakati o‘zgargan joylar (foydalanuvchiga aytish kerak)

1. Vazifa taymeri tugashi vazifani yopmaydi — tasdiq kerak.
2. Bugun uchun tanlangan vazifaning deadline’i o‘zgarmaydi.
3. Kunlik takrorlanuvchi vazifa belgilanmasa ham har kuni chiqadi; eski
   o‘tkazib yuborilganlar (oxirgisidan tashqari) arxivga o‘tadi.
4. Reset «Hafta» rejimi bugundan emas, ertadan boshlanadi; standart rejim — «3 tasi bugun».
5. Setup endi 7 emas, 1 ta odat bilan boshlanadi.
6. 20 amaldan keyin ham bajarilganini belgilash mumkin.
7. Qarzni o‘chirish arxivlaydi; eski yozuvlarda `amount` hozirgi qoldiq
   (yo‘qolgan to‘lov tarixi tiklanmaydi).

## Deploy oldidan

- **Backup oling.** Yangi jadvallar (`debt_payments`, `reset_logs`)
  `create_all` bilan, yangi ustunlar (`debts.archived_at`, `timer_runs.manual`,
  `weekly_focus.carried_to`, `weekly_focus.carried_from`) `init_db()` ichidagi
  avtomatik `ALTER TABLE ADD COLUMN` bilan qo‘shiladi — barchasi NULL-ga ruxsat
  beradi, mavjud qatorlar qayta yozilmaydi. PostgreSQL’da jonli sinab
  ko‘rilmagan (testlar SQLite’da).
- Railway’da `BUILD_ID` yoki `RAILWAY_GIT_COMMIT_SHA` o‘rnatilgan bo‘lsin —
  `GET /api/version` deploy qilingan build’ni ko‘rsatadi.
- Repo’da `.github/workflows/tests.yml` yo‘q (v15 arxivida bor edi). CI
  tiklansa, `node tests/frontend_audit50.cjs` qatorini qo‘shing.

## Tekshirilmagan

Haqiqiy Telegram (iOS/Android), jonli AI provayderlari (Groq/ElevenLabs/Gemini),
PostgreSQL, ko‘p foydalanuvchili yuklama, 23:59 → 00:00 ni haqiqiy qurilmada
o‘tkazish, ikki qurilmada ketma-ket tahrir. Audit hujjatidagi «Haqiqiy
foydalanish sinovlari» ro‘yxati to‘liq ochiq — launchdan oldin qo‘lda o‘tish kerak.
