# K01–K26: tuzatish va soddalashtirish — holat

Manba: shu repo (`claude/brave-mayer-78dw7h`). ZIP, APK va `ERNESTOS_TOLIQ_AUDIT_UZ.md`
bu sessiyaga yuklanmagan. Har bir da'vo shu koddagi test bilan tekshirilgan.

Holatlar:
- **✅ sinovdan o'tdi**: kod va avtomatik test (pytest yoki brauzer/VM test) bor.
- **🟡 real muhitda tekshirilmagan**: kod bor, lekin haqiqiy Android telefon yoki prod server kerak.
- **⏭ keyingi bosqich**: hozircha qilinmagan.

| # | Nima | Holat | Qayerda / qanday tekshirildi |
|---|------|-------|------------------------------|
| K01 | Mikrofon: oyna yopilsa, ilovadan chiqilsa yoki bekor qilinsa yozuv to'xtaydi; kech kelgan javob tashlanadi | ✅ (VM test) · 🟡 Android | `voiceAbandon`, frontend_audit50 |
| K02 | Tozalash/o'chirish: server "ha" demaguncha lokal qoralama va navbat o'chmaydi; jamoa egasi o'chirilsa jamoa boshqalardan tortib olinmaydi | ✅ | `freezeLocalDrafts`/`thawLocalDrafts`; `delete_account` → 409 `owner_must_transfer`; FK yoqilgan SQLite testi |
| K03 | Kun xulosasi: boshqa joyda o'zgargan bo'lsa ustidan yozilmaydi (409); ikkala matn ko'rsatiladi: meniki / saqlangan / ikkalasi | ✅ | `JournalIn.base`, `journalConflicts`, pytest + VM test |
| K04 | Offline navbat: 4xx endi elementni o'chirmaydi; sababi ko'rsatiladi, "qayta yuborish / olib tashlash" bor | ✅ | `flushQueue`, `queueSheet` |
| K05 | Belgi "kerakli holat + kun" bilan ketadi: qayta yuborilsa teskari bo'lmaydi; 23:59 dagi belgi o'z kuniga tushadi (7 kungacha orqaga); namoz ekrani foydalanuvchi soat mintaqasida | ✅ | `TickIn`, `_tick_day`, 3 ta pytest |
| K06 | Xato bo'lsa faqat o'sha element qaytariladi; bir elementdagi belgilar navbat bilan ketadi | ✅ | `optimistic(..., undo, chain)` |
| K07 | Chiqish: mikrofon to'xtaydi, yuborib bo'ladigani yuboriladi, qolgani soni bilan aytiladi; token serverda bekor qilinadi | ✅ (brauzer, real server) · 🟡 Android | E2E "Sign out … ends the session on the server" |
| K08 | Internet yo'q paytda oxirgi yuklangan Bosh sahifa vaqti bilan ko'rsatiladi; token keshga yozilmaydi | ✅ | `saveLastGood`/`loadLastGood`, VM test |
| K09 | Kirish: til tanlash, bot nomi eslab qolinadi, har xatoga alohida xabar, "Joylash" tugmasi, to'liq kod o'zi yuboriladi, 15 soniya timeout | ✅ (brauzer, real server) · 🟡 Android clipboard | E2E 17/17 |
| K10 | Ilovada "Botga qaytish" ilovani yopmaydi, botni ochadi; qaytganda o'zi tekshiradi | ✅ kod · 🟡 Android | Minimal sozlashni ilovaning o'zida tugatish ⏭ |
| K11 | Push ruxsati har ishga tushganda so'ralmaydi; Sozlamalarda holat ko'rinadi: yoqilgan / ruxsat yo'q + tugma / o'chirilgan / bu versiyada yo'q | 🟡 | Firebase sozlanmagan, real qurilmada push yetkazilishi tekshirilmagan. Eslatmani tahrirlash va bekor qilish ⏭ |
| K12 | Sessiyalar va release imzosi | ⏭ | Keystore storage, signed release, debuggable=false — real build kerak |
| K13 | Pastki bo'limlar: Bugun · Reja · Moliya · Profil | ✅ | pytest nav testi |
| K14 | Bugun ekrani: avval amal | qisman (Hozir → Keyingi allaqachon bor) | Uydan odatni belgilash, faol taymer ⏭ |
| K15 | Vazifalar soddaligi | ⏭ | |
| K16 | Oxirgi ochilgan loyiha boshqa ekrandagi yangi vazifani o'ziga olmaydi | ✅ | VM test |
| K17 | Odat formasi: chip bosilganda miqdor maydonlari saqlanadi | ✅ | VM test. Ilova qayta ochilganda qoralama ⏭ |
| K18 | Namoz yoki xulosa yuklanmasa, odatlar ekrani baribir ishlaydi | ✅ | `part()` ajratish |
| K19 | Ish/tanaffus ritmi 25 daqiqadan uzun har bir taymerda (25/5) | ✅ | Ritm Pro funksiya, shuning uchun standart "bir martada" qoldi |
| K20 | Maqsadlar | ⏭ | |
| K21 | Pul yozuvini joyida tahrirlash: summa, turi, kategoriya, izoh, kun | ✅ | `PATCH /api/money/{id}`, pytest + VM test. Qidiruv/sahifalash ⏭ |
| K22 | AI xulosa: savollar soni haqiqiy; to'qnashuv holati ko'rsatiladi (K03) | qisman ✅ | |
| K23 | Statistika | ⏭ | |
| K24 | Jamoa: egalik/ruxsat himoyasi (K02, K05) | qisman ✅ | Navbatdagi rad etilgan element "ko'rib chiqish"da (K04) |
| K25 | Eksport v2: barcha ID, odat miqdori, jadval tarixi, pauzalar, taymerlar, `schema_version`; ilovada bitta eksport tugmasi; "zaxira" so'zi olib tashlandi | ✅ eksport | **Tiklash (restore) yo'q** — eksport to'liq backup emas |
| K26 | Accessibility/copy | qisman | Yangi tugmalar `aria-*`, 44 px; to'liq TalkBack auditi ⏭ |

## Qo'shimcha (foydalanuvchi so'rovi bilan)

- **Sozlamalar**: tepada profil (rasm yuklash ✏️, ism, @username), keyin joriy tarif va "Tarifni yaxshilash". Undan keyin do'st taklifi progressi, ilova sozlamalari, qizil "Chiqish" va kulrang "Akkauntni o'chirish". Yopish tugmasi o'ng yuqoridagi ✕.
- **Tariflar**: Oylik/Yillik tanlagich bor. Chegirma narxlardan hisoblanadi (−26%), "−20%" deb yozilmagan. Har tarif o'z kartasida: joriy tarif belgisi, "Tavsiya etiladi" Max va ✅ ro'yxat. Ikkita ⭐ tugma, ostida "Tasdiqlamaguningizcha hech narsa yechilmaydi. O'zi yangilanmaydi" degan izoh.
- **Do'st taklifi**: 5 do'st = +1 oy Pro, 10 = yana +2 oy Pro, 20 = +1 oy Max. Har bosqich bir marta beriladi, `ref` takrorlanmaydi. Kelgan do'st 3 kun Pro oladi.
- **Profil rasmi**: `POST/DELETE /api/avatar`. Faqat JPEG/PNG qabul qilinadi, turi baytlardan tekshiriladi, hajmi ≤ 96 KB. Rasm akkaunt bilan birga o'chadi.

## Migratsiya va orqaga qaytarish

- Yangi jadval `user_avatars` paydo bo'ladi (`create_all` uni o'zi yaratadi). Mavjud jadvallarga ustun qo'shilmagan.
- `plan_grants` jadvaliga `ref = "refstep:<id>:<5|10|20>"` yozuvlari tushadi.
- Orqaga qaytarish: oldingi commit'ga qaytish kifoya. Yangi jadval eski kodga xalaqit bermaydi.
- Do'st taklifida 3 kunlik bonus endi faqat kelgan do'stga beriladi. Taklif qilgan odam bosqich sovg'alarini oladi.
- Mijoz `base` maydonini yuboradi. Eski mijozlar va bot uni yubormaydi, ular uchun xatti-harakat o'zgarmagan.

## Testlar (shu commit'da)

- pytest: **1071 passed**
- Frontend VM: frontend_release (2 ta TZ), frontend_audit50, test_agent_ui 5/5. Hammasi o'tdi.
- Playwright, real lokal server bilan: Mini App **11/11**, telefon ilova qatlami **17/17**.

## Real Android'da hali tekshirish kerak

1. Mikrofon: ilova fonga o'tganda yozuv to'xtaydimi (K01).
2. Rasm tanlash: `<input type=file>` Capacitor WebView'da galereyani ochadimi.
3. "Joylash" tugmasi: clipboard ruxsati.
4. Push: Firebase `google-services.json` bilan build, ruxsat so'rash va bildirishnoma kelishi (K11).
5. "Botga qaytish": Telegram ilovasi ochiladimi va qaytganda o'zi tekshiradimi (K10).
6. Telegram Stars to'lov oynasi ilovadan ochiladimi. Play Market uchun Google Play Billing talab qilinadi — bu hali yo'q.
