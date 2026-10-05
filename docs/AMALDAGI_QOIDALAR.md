# ErnestOS — amaldagi qoidalar (v13.0)

Bu hujjat kod hozir qanday ishlashini qisqa yozadi. Raqamlar
`tests/test_smoke.py::test_the_rules_document_matches_the_code` testi bilan
koddagi konstantalarga solishtiriladi — kod o‘zgarsa-yu, hujjat o‘zgarmasa,
test yiqiladi. Versiya manbasi: `version.py` (`/api/version` ham shuni beradi).

## Umumiy foiz (Statistika)

| Bo‘lim | Og‘irlik |
|---|---|
| Vazifa | 40% |
| Odat | 25% |
| Jamoa | 20% |
| Namoz | 15% |

- O‘sha kuni bo‘sh bo‘lim hisobdan chiqadi, uning og‘irligi qolganlarga
  taqsimlanadi. Shuning uchun bitta kichik vazifali kun 100% bo‘lishi mumkin.
- Bu foiz — **bugungi rejaning bajarilishi**, kun qanchalik muhim o‘tgani emas.
  Ilova buni foiz tagida yozib qo‘yadi.
- Vazifa ichida muhimlik og‘irligi: yuqori 3, o‘rta 2, past 1.
- Odat darajalari: majburiy 50, maqsad 30, bonus 20.

## Ketma-ketliklar — ikkitasi, nomi ham ikki xil

- **Odatlar seriyasi** (🔥): rejadagi *barcha* odatlar bajarilgan kunlar ketma-ket.
- **Izchil kunlar** (📈): kamida **60** ball to‘plangan kunlar ketma-ket;
  oyiga **2** ta himoya kuni bor. **90**+ ball — mukammal kun.

## Kun xulosasi

Kamida **1** ta mazmunli javob yozilsa, kun xulosasi yozilgan hisoblanadi.
Bo‘sh yoki faqat probeldan iborat javob hisobga kirmaydi. AI to‘ldirgan
javoblar «Qabul qilish» bosilmaguncha saqlanmaydi. Yozuv ochilgan kunga
bog‘lanadi: 23:59 da yozilib, yarim tundan keyin saqlangan matn o‘sha kunda qoladi.

## Qadam

- Har bir tur (vazifa, odat, namoz, xulosa, maqsad) uchun ikki holat:
  **boshlandi** (kamida bittasi) va **to‘liq** (bugungi reja to‘liq).
- Vazifa qadamida bugun tugatilgan *har qanday* vazifa hisoblanadi — kechikkan
  va sanasizi ham. Bugungi va’dalar (`muddati bugun`) alohida ko‘rsatiladi.
- Hafta maqsadi vazifaga bog‘langan bo‘lsa, holati o‘sha vazifadan olinadi —
  barcha ekranlarda bitta qoida.
- Maqsad yo‘q haftada «Maqsad qo‘shish» taklifi chiqadi, bajarilmagan qadam emas.
- Daraja chegaralari: 0 · 26 · 51 · 101 · 201 qadam.

## «Hozir» kartasi

1. Uyg‘onish (vaqti o‘tmagan bo‘lsa) → 2. Bugun uchun tanlangan vazifa →
3. Kechikkanlar (shaxsiy va jamoa — bitta navbat) → 4. Bugun hozir
boshlash mumkin bo‘lgan ishlar (vaqtsiz yoki **60** daqiqa ichida boshlanadigan) →
5. Odat → 6. Bugun keyinroq belgilangan vaqtli ish → 7. Namoz → … → kun yakuni.

Kechki 18:00 uchrashuvi ertalab bajarsa bo‘ladigan ishni siqib chiqarmaydi;
u «Keyingi belgilangan» qatorida ko‘rinadi.

## Taymer

- **Odat**: vaqt — maqsadning o‘zi. To‘liq vaqt tugasa, odat belgilanadi.
- **Vazifa**: taymer ishlangan vaqtni yozadi; vazifa tugaganini foydalanuvchi
  tasdiqlaydi («Vazifa tugadimi?»).
- **Yakunlash** — ishlangan daqiqalar saqlanadi. **To‘xtatish** — sessiya
  bekor qilinadi, vaqt yozilmaydi.
- **Taymersiz bajardim** — daqiqani qo‘lda yozish; o‘lchangan vaqtdan alohida
  ko‘rsatiladi, ishlab turgan taymer ustiga yozilmaydi.
- Taymer davomiyligini o‘zgartirish keyingi sessiyaga tegishli; boshlangan
  sessiya (jamoadoshlarniki ham) o‘zgarmaydi.

## Takrorlanuvchi vazifalar

Kunlik/ish kunlari/haftalik/oylik vazifalar taqvim bo‘yicha chiqadi: kechagi
nusxa belgilanmagan bo‘lsa ham bugungisi paydo bo‘ladi. Seriyada eng oxirgi
o‘tkazib yuborilgan nusxa ochiq qoladi, undan oldingilari arxivga o‘tadi
(o‘chirilmaydi). Bir sanaga ikkita nusxa yaratilmaydi.

## Tanaffusdan qaytish (yengil reset)

- Standart rejim: eng muhim **3** ta kechikkan vazifa bugunga, qolganlari
  keyingi olti kunga taqsimlanadi.
- Har bir rejim oldindan ko‘rsatiladi (qaysi vazifa qaysi kunga), keyin
  bajariladi. Reset **7** kun ichida bir bosishda bekor qilinadi; shu orada
  o‘zingiz o‘zgartirgan vazifa tegilmaydi va ro‘yxatda ko‘rsatiladi.

## Eslatmalar

- Odat eslatmasi sozlamalarda yoqilgan bo‘lsa ishlaydi (standart: o‘chiq).
  Vaqt tanlanganda ilova holatini ko‘rsatadi va yoqishni taklif qiladi.
- Job kechiksa ham eslatma **30** daqiqa ichida bir marta yetkaziladi.

## Bepul amallar va kanal

Dastlabki **20** amal bepul. Keyin yangi narsa qo‘shish va tahrirlash uchun
kanalga a’zolik so‘raladi. **Bajarilganini belgilash ochiq qoladi**: odat,
vazifa, namoz, kun xulosasi, uyg‘onish, jamoa belgisi va taymer.

## Qarzlar

- Asl summa o‘zgarmaydi; har bir qisman qaytarish alohida yozuv (sana, summa).
  Qoldiq hisoblab chiqariladi.
- Qoldiqdan katta summa rad etiladi; teng summa qarzni yopadi.
- O‘chirish tasdiq so‘raydi va arxivlaydi (Bekor qilish bor); butunlay
  o‘chirish faqat arxivdan, yana tasdiq bilan.

## Tez qo‘shish

- Shaxsiy va jamoa vazifasi bitta parser bilan o‘qiladi: «ertaga 15:00 hisobot».
- Bugun o‘tib ketgan vaqt («10:00 hisobot» soat 20:00 da) — ilova
  «Bugunmi yoki ertagami?» deb so‘raydi; bot taxminini ochiq yozadi.
- «7 kundan keyin» tugmasi aynan +7 kunni belgilaydi va sanani ko‘rsatadi.
- Odat matnidagi kunlar («du, chor, juma sport») jadvalga aylanadi va
  saqlashdan oldin ko‘rsatiladi.
- Setup bitta odat bilan boshlanadi, ko‘pi bilan **3** ta tanlanadi.
