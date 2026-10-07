# ErnestOS — amaldagi qoidalar (v13.1)

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

- **Ish va tanaffus**: 50+ daqiqali sessiyada 25/5, 50/10 yoki 90/15 ritmi.
  Blok tugashi bilan taymer o‘zi pauza qiladi; tanaffus ish vaqtiga kirmaydi.

## Odatlar

- O‘lchanadigan odat: maqsad (masalan 20 bet), birlik va ixtiyoriy minimal
  variant. Maqsadga yetganda bajarilgan; minimal — alohida belgi, bajarilgan emas.
- Ovozli agent mavjud vazifani yopishi yoki odatni belgilashi mumkin — har doim
  tasdiq tugmasi orqali.

## Takrorlanuvchi vazifalar

Kunlik/ish kunlari/haftalik/oylik vazifalar taqvim bo‘yicha chiqadi: kechagi
nusxa belgilanmagan bo‘lsa ham bugungisi paydo bo‘ladi. Seriyada eng oxirgi
o‘tkazib yuborilgan nusxa ochiq qoladi, undan oldingilari arxivga o‘tadi
(o‘chirilmaydi). Bir sanaga ikkita nusxa yaratilmaydi.
«Bajargandan N kun keyin» turi bajarilgan kundan sanaladi va taqvim bo‘yicha
yaratilmaydi.

## Kutishdagi vazifa

«Javob kutilyapti» yoki «Boshqa ishga bog‘liq» deb belgilangan vazifa
tekshirish sanasigacha «Hozir» va resetdan chiqadi, sana kelganda
«Javobni tekshiring» bo‘lib qaytadi.

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
- Har eslatmani **15 / 60 / 180** daqiqaga surish mumkin; muddat o‘zgarmaydi.
- Sokin soatlarda eslatmalar ovozsiz keladi.

## Kun sig‘imi

Bo‘sh vaqt kiritilsa, bugungi taymerli ishlar yig‘indisi bilan solishtiriladi.
Oshsa — ogohlantirish va ko‘chirish taklifi; o‘zi hech narsa ko‘chmaydi.

## Offline

Belgilash (odat, vazifa, namoz, jamoa, miqdor) internet bo‘lmasa qurilmada
navbatga tushadi va ulanganda bir marta yuboriladi.

## Tariflar: Free, Pro, Max (`plans.py`)

| | Free | Pro | Max |
|---|---|---|---|
| Odatlar (marosimlardan tashqari) | **3** | **25** | cheksiz |
| Faol vazifalar | **30** | cheksiz | cheksiz |
| Takrorlanuvchi vazifalar | **3** | **25** | cheksiz |
| Loyihalar | **1** | **15** | cheksiz |
| Ochiq qarzlar | **3** | cheksiz | cheksiz |
| O‘zi yaratgan jamoalar | **0** | **2** | **10** |
| Jamoa a’zolari (egasining tarifi) | **8** | **10** | **50** |
| Ovozli buyruq | **5** / hafta | **30** / kun | **100** / kun |
| AI kun xulosasi, taymer ritmi, oylik/yillik statistika | — | bor | bor |
| Statistikani faylga yuklash | — | — | bor |

- Yangi akkaunt **3** kunlik Pro bilan boshlanadi. Tariflar yoqilgunga qadar
  bor bo‘lgan akkauntlarga bir marta **14** kun Pro sovg‘a.
- Kanalga qo‘shilish: **+7** kun Pro, bir akkauntga bir marta.
  Taklif qilingan odam faol bo‘lsa: ikkalasiga **+3** kun, oyiga ko‘pi bilan **30** kun.
- Bir tarifning muddatlari ketma-ket qo‘shiladi: sinov paytida to‘lansa, to‘lov
  sinovdan keyin boshlanadi. Max Pro’dan ustun.
- To‘lov: Telegram Stars — Pro 125⭐/oy, 1100⭐/yil; Max 350⭐/oy, 3000⭐/yil.
- Muddat tugashidan bir kun oldin bot ogohlantiradi. Tugagach Free:
  **hech narsa o‘chmaydi**, ortiqcha narsalar ko‘rinadi va belgilanadi,
  faqat yangisini qo‘shib bo‘lmaydi.
- Tariflar yoqilganda kanal **devor emas**, bonus. `PLANS_ENABLED=0` bo‘lsa eski
  qoida qaytadi: Dastlabki **20** amal bepul, keyin yangi narsa qo‘shish uchun
  kanalga a’zolik so‘raladi; bajarilganini belgilash har doim ochiq.

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
- Setup bitta odat bilan boshlanadi, ko‘pi bilan **3** ta tanlanadi; turish
  vaqti so‘raladi va ertalabki hisobot shunga moslanadi.
