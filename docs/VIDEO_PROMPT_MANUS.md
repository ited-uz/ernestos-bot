# ROLE

You are a senior motion designer and creative director (Apple-keynote / Linear / Notion launch-film level). Produce a **premium motion-design product video** for **ErnestOS**, a personal life-planner app. The audience is Uzbek-speaking adults (18–45) in Uzbekistan and Central Asia: entrepreneurs, students and families who already live in Telegram.

The video must show **only features that exist today**. Every UI frame on screen must come from the real screenshots attached (folder `screens/`). Never invent screens, numbers, features, reviews or user counts.

---

# 1. DELIVERABLES

| # | Format | Length | Use |
|---|---|---|---|
| A | **16:9 · 1920×1080** | **60 s** | Master: YouTube, website, presentations |
| B | **9:16 · 1080×1920** | **30 s** | Instagram Reels, TikTok, YouTube Shorts, Telegram stories |
| C | **9:16 · 1080×1920** | **15 s** | Ads / pre-roll: voice feature only |
| D | Same as A and B | — | **Music-free versions**: voice-over + SFX only (many in the audience prefer no music) |

- 30 fps (60 fps if your pipeline allows), H.264 MP4, high bitrate (≥ 16 Mbps for 1080p).
- Audio: −14 LUFS integrated, true peak ≤ −1 dBTP, voice clearly above music.
- Subtitles: a separate `.srt` (Uzbek). In the 9:16 versions, also burn subtitles into the picture (most people watch muted).
- Also deliver: 3 still thumbnails (1280×720) and a 2–3 frame **styleframe board**, before or with the final.

---

# 2. PRODUCT TRUTH: what ErnestOS really does

Use these and only these. The screen file names are in brackets.

1. **Voice commands in Uzbek** — the hero feature.
   - The user taps 🎤 and speaks: «Ertaga soat 10 da Aziz bilan uchrashuv», «Tushlik 45 ming», «Akmal qarzini berdi», «Meetingga borib keldim».
   - The AI understands dialect and Uzbek mixed with Russian.
   - It **creates** new items and **closes** existing ones: marks a debt as paid back, marks a meeting as done.
   - Every command first appears as a **card**. Nothing changes until the user taps **«Tasdiqlash»**.
   - Screens: `02-ovoz-yozilmoqda`, `03`–`07` (cards), `24-tungi-ovoz-karta`, `18-ai-yordamchi` (voice inside the AI assistant chat).
2. **«HOZIR» (Now)** — the home screen shows the one thing to do right now, not a list. A blur focus mode hides the rest until the user taps «Kunning qolgan qismini ko'rish». On Pro/Max an AI picks this task and gives the reason. [`01-bugun-hozir`, `01b-bugun-toliq`, `21-tungi-bugun`]
3. **Everything in one app.** Tabs: **Bugun · Reja · Moliya · Profil**.
   - Tasks: today / overdue / calendar, priority, reminders, ⭐ main task. [`08-vazifalar`]
   - Habits in three tiers (Majburiy / Maqsadli / Qo'shimcha), streaks, timers. [`09-odatlar`]
   - **Namoz** — the 5 daily prayers with statuses Jamoat / Vaqtida / Qazo / O'qilmadi; prayer counts in the day's result. [`10-namoz`]
   - Goals at three levels: Haftalik → Bosqichlar → Hayotiy. Categories include Biznes, Kapital, Salomatlik, **Islom va xayriya**. [`11-maqsadlar`]
   - **Groups**: family or team; assign a task to a person; see who did what. Members can join without Telegram, by code. [`12-guruhlar`]
   - **Money**: balance, income and expenses, accounts, transfers. [`13-moliya`, `22-tungi-moliya`]
   - **Debts**: one card per person, partial repayments, "due this month". [`14-qarzlar`]
   - **Qadam**: XP levels (Boshlovchi → Quruvchi → Intizom → Barqarorlik → O'z ustida ishlash), streak, rank. [`15-qadam`, `23-tungi-qadam`]
   - Statistics. [`16-statistika`]
   - **Yaqinlarim** (close people): who to stay in touch with, birthdays. [`17-yaqinlarim`]
   - Quick add with text or voice. [`19-tezkor-qoshish`]
4. **Design**: 4 themes (Clean White, Blossom, Obsidian Slate, Emerald Royal), light and dark modes. [`20-mavzular`]
5. **Sign-in**: email code (no password), Google, Apple, Telegram. The sign-in page lists "Nega ErnestOS?". [`00-kirish`, `00b-kirish-tungi`, `00c-nega-ernestos`]
6. **Platforms**: Telegram bot + Telegram Mini App + Android app, all on one account.
7. **Trust**: no ads, data is never sold, export and full account deletion are inside the app. Entries made offline are sent automatically when the connection returns.

### Never claim or show

- An iPhone app, an App Store badge, or an iOS device. The app is Android + Telegram. Apple *sign-in* exists, but there is no iOS app.
- A Google Play badge, **unless** a store link is provided at the end of this prompt.
- User counts, ratings, stars, reviews, testimonials, "No. 1", awards, prices, discounts.
- Bank or card integration, automatic bank import, Google Calendar sync, a web app, a smartwatch app, widgets.
- Real people's faces or hands (no stock "happy user" footage). Abstract hands or silhouettes are fine only as minimal 2D line art.
- Third-party logos, other than the Google and Apple sign-in buttons exactly as they appear on the real screens. Write "Telegram" as plain text; do not use the Telegram logo.
- Edited UI text. Never translate, retype or "improve" text inside the screenshots.

---

# 3. ASSETS (attached)

- `screens/*.png`: 28 real app screens, **1080×2340** (Android 19.5:9).
  - Captured from the real app with sample data.
  - Use them **inside a phone mockup**: a clean generic Android frame, thin bezel, no brand logo, no notch gimmicks. The frame color is graphite (#1C1C1E) on dark scenes and silver (#E5E7EB) on light scenes.
  - You may **crop, mask and lift out** single UI elements (the voice card, the «Tasdiqlash» button, the HOZIR card, a habit row, the balance card) and animate them in 2.5D space.
  - You may rebuild a simple element as vector **only** if it is pixel-identical (same text, colors, radius).
- `brand/logo-1024.jpg`: app logo. A black rounded tile with a white "E" whose lower stroke is a green staircase.
- `brand/splash-1280x1920.png`: the launch screen (logo + "ErnestOS" wordmark on black).

---

# 4. DESIGN SYSTEM

**Colors**

| Token | Hex | Use |
|---|---|---|
| Ink black | `#000000` | logo tile, opening and closing frames |
| Brand green | `#01E07B` | logo accent, "done" moments, progress lines (sparingly: max ~5% of the frame) |
| App blue | `#0070E0` | the app's primary buttons. Use for UI highlights and the cursor/tap ripple |
| Deep blue | `#0062CC` | text links |
| Paper | `#F8F9FA` / `#FFFFFF` | light scenes |
| Night | `#1C1C1E` / `#242426` | dark scenes |
| Text | `#111827` (light) / `#EBEBF0` (dark) | type |
| Success green (UI) | `#1F7A36` | income, "ok" (in the UI only) |
| Danger red (UI) | `#D70015` | expense / overdue (in the UI only) |

**Typography**

- Headlines: **Inter Display** (or SF Pro Display) — Semibold/Bold, tight tracking (−1% to −2%).
- Body and captions: **Inter** Regular/Medium.
- Uzbek Latin must use proper letters: **o‘ g‘** (U+2018) and the apostrophe ʼ. Never use `o'` with a straight quote in titles.
- Minimum on-screen text: 48 px on 1080-wide 9:16, 40 px on 16:9. Contrast ≥ 4.5:1.

**Layout**

- 12-column grid. 9:16 safe area: 150 px top, 340 px bottom (UI overlays), 60 px sides.
- One idea per shot. Maximum 6 words of headline on screen at once.

**Motion language**

- Easing:
  - entrances: `cubic-bezier(0.22, 1, 0.36, 1)` (expo-out), 400–600 ms;
  - exits: `cubic-bezier(0.64, 0, 0.78, 0)`, 250–350 ms;
  - UI cards: spring (stiffness ~220, damping ~26), with a slight overshoot.
- The phone floats in 2.5D (±8° Y-rotation, soft shadow 0 40px 80px rgba(0,0,0,.25)) with slow parallax drift.
- **Match cuts on shapes**: the round 🎤 button becomes the waveform; the voice card becomes the list row it creates; the green check becomes the logo's green staircase.
- Taps: a 24 px blue ripple (`#0070E0`, 30% opacity) plus a subtle 2 px press-down scale (0.97).
- Text: per-word kinetic reveal (y +16 px → 0, opacity 0 → 1, 40 ms stagger). No bouncing letters, no glitch, no neon, no lens flares.
- Transitions: 8–12 frames. Prefer masks, push and scale-through to cross-dissolves.
- The overall feel is calm, confident and premium. Think "quiet power", not hype.

**Sound**

- Music (music versions only): minimal modern electronic / soft piano-pulse, 96–110 BPM, warm, no vocals, fully licensed or royalty-free (state the source). Duck under the voice-over by −12 dB.
- SFX (soft, short, never cartoonish):
  - mic-on "tick";
  - waveform shimmer;
  - card slide "whoosh";
  - confirm "chime" (a single bell-like tone) on every «Tasdiqlash»;
  - page flicks on tab changes.
- The music-free version uses the same SFX plus a subtle room tone.

**Voice-over**

- A warm, confident Uzbek voice (male or female), standard literary Uzbek with Tashkent pronunciation, conversational, not "TV announcer".
- About 140 words per minute.
- If you synthesize the voice: natural prosody, no robotic stress. Proper names: Aziz, Akmal, ErnestOS ("Ernest-O-Es").

---

# 5. STORYBOARD — 60 s master (16:9)

| Time | Visual & motion | On-screen text (uz) | Voice-over (uz) | SFX |
|---|---|---|---|---|
| **0:00–0:05** | **Hook.** Black frame. Five floating white "fragments" drift in chaotic directions: a sticky note, a paper notebook page with numbers, a checklist, a calendar square, a prayer-time list (abstract line art, no real logos). They collide slightly and slow down. | «5 ta ilova. 5 ta daftar.» | «Vazifalar — bir ilovada. Odatlar — boshqasida. Pul — daftarda. Qarzlar — xotirada.» | soft paper rustle, low pulse |
| **0:05–0:08** | All fragments are pulled into the centre and collapse into the **ErnestOS logo tile** (scale-through). The green staircase draws itself (stroke reveal). | «Bitta joyda.» | «Endi hammasi — bitta joyda.» | deep "whoom" + chime |
| **0:08–0:12** | Logo tile morphs into the phone. Screen `01b-bugun-toliq` → zoom to the **🎤 button** (bottom right). Tap ripple. Cut to `02-ovoz-yozilmoqda`: the red mic pulses, the timer runs 0:01 → 0:02. A clean waveform appears beside the phone. | «Faqat ayting.» | «Faqat ayting.» | mic tick, waveform shimmer |
| **0:12–0:20** | Above the waveform a speech bubble types: **«Akmal qarzini berdi»**. The bubble flies into the phone and becomes card `03-ovoz-karta-qarz-yopildi` («✅ Qarz to‘liq qaytdi · Akmal — 500 000 so‘m»). Hold 1 s. The finger-tap ripple lands on **«Tasdiqlash»**, then a green check burst. | «Gapiring → Tasdiqlang → Bajarildi» | «ErnestOS o‘zbekcha tushunadi. Yangi ish qo‘shadi, bo‘lganini yopadi. Siz tasdiqlaysiz — tamom.» | card whoosh, **confirm chime** |
| **0:20–0:26** | **Voice montage**, 3 fast cards, 2 s each. Phone left, the spoken sentence right as kinetic type, then the card slides in: `05` «Ertaga soat 10 da Aziz bilan uchrashuv» → `06` «Tushlik 45 ming» → `07` «Meetingga borib keldim». Each ends on a quick chime. | (the three sentences, one at a time) | «Uchrashuv, xarajat, bajarilgan ish — bitta gap bilan.» | 3× light chime |
| **0:26–0:32** | **HOZIR.** `01-bugun-hozir`: the blurred home with the bright HOZIR card. Lift the HOZIR card out in 3D and float it forward; the blurred list stays behind. Tap «Kunning qolgan qismini ko‘rish» → unblur to `01b`. | «Ro‘yxat emas — bitta ish.» | «Ochganingizda — uzun ro‘yxat emas. Aynan hozir nima qilish kerakligi.» | soft focus pull |
| **0:32–0:38** | **Namoz + odatlar.** Split screen: `10-namoz` (left, highlight a «Jamoat» chip being selected) and `09-odatlar` (right, a habit row ticks green). The Bugungi progress values count up (lift the strip from `01b`). | «Namoz. Odatlar. Kun natijasi.» | «Namoz, odatlar va kun xulosasi — kuningiz natijasiga kiradi.» | 2 ticks |
| **0:38–0:44** | **Moliya + qarzlar.** `13-moliya`: the balance card lifts out (46 185 000 so‘m) → push to `14-qarzlar` with the person-cards fanning out. | «Pul va qarz — nazoratda.» | «Kirim-chiqim, hisoblar va qarzlar. Har bir odam — bitta kartada.» | page flick |
| **0:44–0:50** | **Maqsadlar + guruhlar.** `11-maqsadlar` goal cards (including «Islom va xayriya») slide up like a deck → match-cut to `12-guruhlar`; highlight the «Sizga 1 ta» badge. | «Maqsaddan — jamoagacha.» | «Hayotiy maqsaddan haftalik qadamgacha. Oila va jamoaga topshiriq bering — natijani ko‘ring.» | whoosh |
| **0:50–0:54** | **O‘sish.** `15-qadam`: the level ring fills (animate the arc), «Quruvchi» level badge pops. Then a quick flash of `16-statistika`. | «Har kun — bir qadam.» | «Qadam va statistika — o‘sishingiz ko‘rinib turadi.» | rising tone |
| **0:54–0:57** | **Platforms + themes.** Three phones fan out: light `01b`, dark `21-tungi-bugun`, `20-mavzular`. Plain text chips: «Telegram» · «Android». | «Telegram’da va Android’da» | «Telegram’da ham, telefoningizda ham — bitta akkaunt.» | — |
| **0:57–1:00** | **End card.** Black. Logo tile centre, green staircase glint. Wordmark «ErnestOS». Tagline. CTA line(s) from the end of this prompt. | «Gapiring — ErnestOS bajaradi.» + CTA | «ErnestOS. Gapiring — u bajaradi.» | final chime, music tail |

---

# 6. CUT-DOWNS (9:16)

**B · 30 s**

| Time | Content |
|---|---|
| 0:00–0:03 | Hook: fragments collapse into the logo — «Bitta joyda.» |
| 0:03–0:13 | Voice hero: `02` → «Akmal qarzini berdi» → `03` → Tasdiqlash ✓ |
| 0:13–0:17 | 2 quick cards: `05`, `06` |
| 0:17–0:21 | HOZIR: `01` → `01b` |
| 0:21–0:26 | Montage, 1.2 s each: `10`, `13`, `12`, `15` |
| 0:26–0:30 | End card |

Voice-over: «Faqat ayting. ErnestOS o‘zbekcha tushunadi — qo‘shadi, yopadi, siz tasdiqlaysiz. Ochganingizda — bitta ish. Namoz, odat, pul, jamoa — hammasi bitta joyda. ErnestOS. Gapiring — u bajaradi.»

**C · 15 s** (voice only)

| Time | Content |
|---|---|
| 0:00–0:02 | Text on black: «Qarzni eslab yurmang.» |
| 0:02–0:10 | `02` → «Akmal qarzini berdi» → `03` → Tasdiqlash ✓ |
| 0:10–0:15 | End card |

Voice-over: «Akmal qarzini berdi — dedingiz, ErnestOS yozib qo‘ydi. Gapiring — u bajaradi.»

In 9:16: the phone fills about 70% of the height, centred; the headline sits above, the burned-in subtitles below.

---

# 7. WORKFLOW

1. Check every attached screen and map it to the storyboard. If a screen is missing, **do not invent it**. Use a listed alternative and note what you changed.
2. Deliver **3 styleframes** first: the 0:12 voice card, the 0:26 HOZIR lift, and the end card. Note your font, mockup and colour choices.
3. Build a rough animatic with the voice-over to lock timing, then the final animation, sound design and mix.
4. Export all deliverables (section 1). Name the files `ErnestOS_Master_16x9_60s.mp4`, `ErnestOS_Reels_9x16_30s.mp4`, `ErnestOS_Ad_9x16_15s.mp4`, plus `_NoMusic` variants and `ErnestOS_uz.srt`.

---

# 8. QUALITY CHECKLIST (verify before delivery)

- [ ] Every UI frame comes from `screens/`. No invented UI, numbers or text.
- [ ] No iPhone, App Store badge, ratings, user counts, prices or real faces.
- [ ] Uzbek text is spelled correctly, using o‘ / g‘.
- [ ] The logo is never stretched, recoloured or placed on a busy background. Clear space ≥ ½ tile width.
- [ ] Voice-over timing matches the on-screen action. No word appears before it is spoken.
- [ ] The confirm chime plays only on «Tasdiqlash».
- [ ] Loudness −14 LUFS, true peak ≤ −1 dBTP. Music-free versions are delivered.
- [ ] 9:16 versions have burned-in subtitles inside the safe area.
- [ ] No frame shows a sample-data label, browser chrome or a debug overlay.

---

# 9. CTA FOR THE END CARD (fill in before sending)

- Telegram bot: **[[@BOT_USERNAME]]**
- Android: **[[Google Play link or "APK — ernestos.uz"]]**
- Website (optional): **[[domain]]**

If a field above still shows `[[...]]`, leave that line off the end card. Do not invent a handle or link.
