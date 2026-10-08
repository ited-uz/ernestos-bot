# UI/UX 100 ta taklif — nima qilindi, nima qilinmadi va nega

Bu ro'yxatni AI yozgan. Har bir band hozirgi kod va ekran bilan solishtirildi.
**Qilindi** — kodda bor va testdan o'tgan. **Allaqachon bor edi** — kodda oldin ham shunday edi.
**Qilinmadi** — sababi yozilgan.

## Qilindi (shu commit)

| Band | Nima o'zgardi |
|---|---|
| 3 | Avatar ustidagi ⚙️ olib tashlandi. Sozlamalarga avatarning o'zi olib boradi |
| 4 | Iqtibos endi faqat bo'sh kunda ko'rinadi. Band kunda u birinchi vazifani pastga surardi |
| 7 | Bosh sahifadagi "Hafta maqsadini qo'shish" bloki olib tashlandi. U endi + oynasida va Vazifalar ekranida |
| 13 | "Siz o'zingiz tanlagansiz" yozuvi olib tashlandi. Boshqa sabablar ("muddati o'tgan" va hokazo) qoldi, ular foydali |
| 14 | ✏️ belgisi o'rnida endi "Boshqasi" yozuvi turadi. Vazifa nomini bossangiz, vazifa ochiladi |
| 18 | Kechikkan vazifadagi uchta katta tugma ("Bugun / Ertaga / Sanasiz") bitta "Ko'chirish" tugmasiga birlashdi. Tanlovlar alohida oynada chiqadi |
| 17 | "Shaxsiy" yozuvi jamoasi yo'q foydalanuvchida ko'rinmaydi. Jamoasi borlarda qoladi, chunki u yerda farqni ko'rsatadi |
| 33–34, 38–40, 43 | (avvalroq qilingan) Oylik/Yillik tanlagich, Max asosiy tugma, ✕ yopish, do'st taklifi kartasi va 5/10/20 progress |
| 36, 39 | ⭐ belgisi narx va tugma ichida turibdi. ✅ emojilar SVG belgiga almashtirildi |
| 38 | Belgilar katta harfli kichik "pill" ko'rinishida |
| 43 | Yillik tarifda tejalgan miqdor aniq yoziladi: "Yiliga 400 ⭐ tejaysiz" |
| 44, 45 | Izoh matni kichraytirildi (11,5 px). Narx 18–22 px, qalin 800 |
| 47, 54 | Namoz: 20 ta chegarali tugma o'rniga har namozga bitta segmentli qator. Ekran balandligi taxminan yarmiga tushdi. Belgilash baribir **bir bosishda** |
| 54 | "O'qilmadi" endi och qizil rangda, to'q qizil blok emas |
| 48 | Kun xulosasi maydonlari yozganingiz sari kattalashadi |
| 49 | "1/3 javob berildi" yozuvi ingichka progress chizig'iga almashdi. Ekran o'quvchi (screen reader) uchun matn yashirin holda qoldi |
| 50 | "Qancha yozsangiz…" yozuvi olib tashlandi |
| 59 | Kalendar ostiga nuqtalar izohi qo'shildi: Vazifalar / Loyihalar / Tug'ilgan kunlar. "Yopish" tugmasi o'rniga ✕ |
| 91 | Pastki oyna orqa foni quyuqroq va xiralashtirilgan (0.55 + blur) |
| 80 | Faol bo'lmagan tugma: `cursor:not-allowed` |
| — | Namoz va Kun xulosasi sahifalarida + tugmasi chiqmaydi. U Saqlash/Tarix tugmalarini yopib qo'yardi |
| — | + oynasidagi 📝/🔁 emojilari SVG belgiga almashtirildi |

## Allaqachon bor edi

- **6:** FAB pastki menyu ustida turadi.
- **12:** konteyner kengligi cheklangan.
- **23:** bajarilgan narsa ustidan chiziladi.
- **35:** maxfiylik matni qisqa va kulrang.
- **63:** chiqish tugmasi bor.
- **65:** budjet chizig'i bor.
- **69:** summalar `tabular-nums`.
- **76:** fon sof qora emas (`#111215`).
- **79:** matn sof oq emas (`#F1F2F4`).
- **84:** `-webkit-tap-highlight-color: transparent`.
- **90:** tugma ichida yuklanish halqasi bor.
- **95:** oflayn banner bor.
- **100:** `100dvh`.

## Qilinmadi — sababi bilan

| Band | Nega yo'q |
|---|---|
| **35 — "bank darajasidagi AES-256, hatto biz ham o'qiy olmaymiz"** | **Bu yolg'on.** Server ma'lumotni o'qiydi: bot eslatma yuboradi, hisobot tuzadi, ovozni tushunadi. Bunday da'vo foydalanuvchini aldaydi va huquqiy xavf tug'diradi. Hozirgi haqqoniy matn qoldi |
| 1, 31 — tablarni filtr oynasiga yashirish | Bugun/Reja/Taqvim har kuni ishlatiladi. Ularni yashirsak, har safar bitta ortiqcha bosish kerak bo'ladi |
| 16 — ☆ yulduzlarni olib tashlash | ☆ bu bezak emas — "bugungi asosiy" vazifani tanlash tugmasi. Olib tashlasak, funksiya yo'qoladi |
| 18 (svayp) | Svayp yashirin harakat: ko'rinmaydi va ekran o'quvchida ishlamaydi. Uning o'rniga bitta "Ko'chirish" tugmasi qilindi |
| 47 (pastki oyna orqali) | Namoz belgilash 1 bosishdan 2 bosishga oshardi: kuniga 5 namoz × har kuni. Segmentli qator bilan joy tejaldi, bosishlar soni o'zgarmadi |
| 52–53 — "Chuqur" rejim, 7 savol, slayd | Yangi funksiya. K-topshiriq qoidasi: "yangi funksiya qo'shma" |
| 55, 21 — kayfiyat va odat emojilari → SVG | Kayfiyat emojisi hamma tushunadigan til. Yuz SVG'lari qo'shimcha ish, foydasi kam |
| 62 — sozlamalar o'ngdan chiqsin | Barcha oynalar pastdan chiqadi. Bitta oyna o'ngdan chiqsa, tartib buziladi |
| 64 — o'chirishda parol | Ilovada parol yo'q, kirish Telegram orqali. Buning o'rniga "DELETE" so'zini yozish talab qilinadi |
| 67, 71, 72, 73, 74 — YNAB nol-budjet, chat pufaklari, Google/Apple kirish, tanga do'koni, gamifikatsiya tabi | Yangi funksiyalar, K-topshiriqda "hozir qo'shilmaydi" deb belgilangan. "Qadam" darajalari allaqachon bor |
| 96 — o'z select ro'yxati | Telefonning o'z ro'yxati ekran o'quvchida ishlaydi va telefonga mos ochiladi. O'zimizniki yomonroq bo'ladi |
| 98 — Inter shrifti | Tashqi shrift sahifani sekinlashtiradi va oflaynda yuklanmaydi. Tizim shrifti qoldi |
| "Emoji umuman yo'q" | Qisman bajarildi: tugma va ro'yxatlardagi emojilar SVG bo'ldi. Bildirishnoma matni va kayfiyatdagilar qoldi |
| 2 — qidiruv tepada sticky | Qidiruv faqat Vazifalar ro'yxatida bor. Sticky header kichik ekranda joy yeydi. Keyingi bosqichda qaraladi |
| 8–11, 19, 24–30, 32, 41, 46, 56–58, 60–61, 66, 68, 70, 75, 77–78, 81–83, 85–89, 92–94, 97, 99 | Mayda uslub o'zgarishlari yoki allaqachon bor narsalar. Ko'pchiligi yuqoridagi umumiy o'zgarishlarga kirdi. Qolganlari alohida ko'rib chiqishni talab qiladi |

## Testlar

- pytest **1071 passed**.
- Frontend VM testlari o'tdi.
- Playwright: Mini App **11/11**, telefon qatlami **17/17** (4 marta ketma-ket).

Yo'l-yo'lakay bitta haqiqiy xato topildi va tuzatildi. Do'st taklifi ma'lumoti kech kelganda butun Sozlamalar oynasi qayta chizilardi. Rasm tanlash shu paytga to'g'ri kelsa, tanlangan rasm yo'qolardi. Endi faqat taklif kartasi yangilanadi.
