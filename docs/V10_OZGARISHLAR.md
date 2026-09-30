# ErnestOS v10 — bitta tizim, bitta sanoq

v9.2 ustiga. Sening 5 ta aniq talabing + tashqi auditdan (30 band) **to'g'ri
deb topilganlari**. Har bir o'zgarish test bilan qotirilgan (`pytest -q` — 872).

## 1. Sening talablaring

| Talab | Nima qilindi |
|---|---|
| Bot klaviaturasi | `Home · Pul` / `Odatlar · Vazifalar` / `Jamoa · Statistika` / `Sozlamalar`. **Taklif** — Sozlamalar ichida. Home'dagi takroriy «Statistika» tugmasi olib tashlandi |
| Odatlar — bitta ustun | Har odat bitta qatorda (katak bekor). Pastdagi panel yengil: sanoq + «Tayyor odatlar» · «Tartib». «Odat qo'shish» — faqat sarlavhadagi «+» (ikkita qo'shish tugmasi yo'q) |
| Muddatda yil tanlanmaydi (2030) | Telegram'ning ichki sana oynasi yilni yashirardi. Endi **Kun · Oy · Yil** — uchta oddiy tanlov, hamma telefonda bir xil; yil: bu yildan +20 yil. Vazifa, loyiha, countdown, tug'ilgan kun (−100 yil) |
| Botda sana | «12.05.2030» o'qilmasdi («12.05» soat deb olinardi), «2030-05-12» **5-dekabr** deb o'qilardi, «12 may 2030» yilni tashlab yuborardi — tuzatildi |
| Jamoa loyihasi | Shaxsiy loyiha bilan **bir xil ekran va bir xil karta**: progress, muddat, holat, tahrir, o'chirish. Loyiha ichidagi «+» → vazifa **avtomatik jamoa vazifasi** bo'ladi (qayerga degan savol ham berilmaydi) |
| Pul limiti | Har kategoriyada ✎; «2 mln», «500 ming», «1 500 000» deb yoziladi yoki tayyor tugma (cheklovsiz · 500k · 1m · 2m · 5m). Botda: 💰 Pul → 🎯 Limitlar → kategoriya → summa |

## 2. Audit — qabul qilinganlari

| # | Muammo | Yechim |
|---|---|---|
| 1 | Pastki tab yozuvlari kesiladi | 360px da sig'adi (tekshirildi). 359px dan kichik ekranda shrift kichrayadi, kesilmaydi |
| 3 | Bir ish uchun ikki «qo'shish» | Odatlarda bittasi qoldi; loyiha ichida ham bitta «+» |
| 5 | Odatlar paneli og'ir | 2 ta tugma, qaytarish yo'q |
| 6 | Namoz holatlari sig'maydi, ✕ kesiladi | Bitta qatorda 4 ta teng katak (44px). Tanlanganni qayta bosish — tozalaydi, ✕ yo'q |
| 7 | Namozda emoji | Olib tashlandi |
| 8 | Vazifalar: kechikkan ish pastga suriladi | Tartib: Kechikkan → Bugun → Hafta maqsadi |
| 9 | «Ko'chirish» chiplari o'raladi | 3 ta teng katak bitta qatorda, yorliq ustida |
| 10 | Taqvimda nuqtalar toshadi | Maksimum 3 nuqta, markazda |
| 11 | Jamoada «kim qoldi» yorlig'i yo'q | «Qilmagan: Ernest · Gulyora» |
| 12 | Jamoada bo'sh loyiha kartasi | Loyiha yo'q bo'lsa — bitta kulrang qator, bo'sh karta emas |
| 14 | Sozlamalar yashirin | Avatarda ⚙ belgisi |
| 15 | «Quote qo'shish» havolasi Home'da | Bo'sh bo'lsa Home'da yo'q; Sozlamalarda «Quote» qatori |
| 16 | Zoom taqiqlangan | `user-scalable=no`, `maximum-scale=1` olib tashlandi |
| 17 | Nishonlar 36–38px | Chip 40px, segment 44px, namoz 44px |
| 21 | Hozir: pin kechikkanni yashiradi | Pin qoladi, lekin ostida qizil qator: «Kechikkan: Pasport… +1» (Mini App + bot) |
| 22 | Ertalab namoz taklif qilinmaydi | «Soat 12 dan keyin» sharti olib tashlandi |
| 23 | Kech uyg'onish Hozir'dan yozilmaydi | Boshqa ish qolmasa — «Turdim» (kech bo'lsa ham vaqti yoziladi) |
| 24 | Home sanog'i ≠ Statistika sanog'i | Endi bitta: Vazifa va Odat — shaxsiy, **Jamoa** — alohida. Home `Vazifa 1/3` = Statistika «Vazifalar» ostidagi `1/3`. Bot ham xuddi shunday |
| 13 | Statistika: «Jamoa —» tushuntirishsiz | «Bugun jamoa ishi yo'q — 20% boshqalarga»; «O'rtacha» 5 ustuni 440px gacha 3+2 bo'lib ko'rsatiladi (kesilmaydi) |

## 3. Audit — rad etilganlari (sababi bilan)

| # | Taklif | Nega yo'q |
|---|---|---|
| 2 | Pulni tabdan olib, Sozlamalarga yashirish | Sen Pulni alohida bo'lim va botda birinchi qatorda so'ragansan. Tab 360px da sig'adi — muammo yo'q |
| 4 | Odat qatorlari 3 xil vidjet | Farq ma'noli: oddiy — belgi, namoz/kundalik — o'z bo'limidan (qulf), taymerli — soat. Bir xil qilsak, namoz ikki marta belgilanadi |
| 18 | Pulda emoji-kategoriya | Pul ataylab alohida ko'rinadi; emoji tez taniladi. Past ta'sir |
| 19 | Preview banner nav ustida | Faqat `?preview` namunasi; haqiqiy ilovada banner yo'q |
| 25 | Kundalik ballda yo'q | Kundalik — «Kun yakuni» odati sifatida Odatlar ulushida (25%) hisoblanadi |
| 26 | Hafta maqsadi 20% | Eskirgan: v9.2 da bu 20% jamoaga berilgan |
| 27 | Jamoa foizi adolatsiz | Grafikda har a'zo alohida chiziq + o'rtacha; kartada har kishi o'z foizi bilan |
| 28 | Pul: «4.5 ming», «1.2 mln» | Tekshirildi — ishlaydi. Valyuta ($) — hozircha so'm, qasddan (KISS) |
| 29 | «Tejalgan» chalkash | Eskirgan: v9.2 da olib tashlangan; «Umumiy balans» yorlig'i bor |
| 30 | Takrorlanuvchi vazifa belgisi | Qatorda allaqachon ⇄ belgisi bor |

## API

| O'zgarish | |
|---|---|
| `GET /api/teams/projects/{id}/tasks` | Loyiha endi shaxsiy loyiha shaklida: `progress`, `tasks_done/total`, `status`, `deadline`, `description`, `team_name`, `archived`; vazifalarda `source: "team"` |
| `GET /api/home` → `counts.team` | `{done, total}` — bugungi jamoa ishi. `counts.tasks/habits` endi faqat shaxsiy |
| `summary.today` | `tasks_*`/`habits_*` — shaxsiy; yangi `team_done`, `team_total` |
| `now` | `overdue: {count, title, id}` (pin bo'lsa); yangi `reason: "wake_late"` |
