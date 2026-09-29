# ErnestOS v9 — Soft Minimalism redesign

Drop-in: copy `webapp/index.html`, `webapp/preview.js`, `tests/test_smoke.py` over the v8 tree. Backend, APIs, formulas, auth and storage are untouched.

## What changed (webapp/index.html)
- **Stylesheet replaced** (same class names, same token names). Opaque surfaces, no glass/gradients/backdrop; soft grey ground, white surfaces, charcoal dark mode; restrained accent split into `--primary` (text, AA) and `--primary-fill` (buttons, AA with its label).
- **Five themes kept** (`ocean` default) as accent variants of one structure: Calm Blue, Indigo, Lilac, Ink, Teal. Theme ids and the API are unchanged.
- **Type**: system font, 16px body, sentence-case section headings, all text tokens ≥4.5:1.
- **Lists**: consecutive rows join into one grouped surface with hairlines.
- **Home**: compact greeting + date button (opens month) + avatar (opens settings) → Now card with one explicit action button and a hairline of today's score → today's tasks → day/week/month with counts ("4 of 7 tasks done · 4 of 6 habits done") → today's parts → countdowns → progress.
- **Tasks**: priority High is written as a tag, not only an edge colour; overdue rows read "Move: Today · Tomorrow · No date"; star is a line icon with `aria-pressed`.
- **Statistics**: task/habit tiles show counts under the percentage (`/api/summary` now also fetched with stats; a failure only hides the counts).
- **Icons**: emoji used as UI glyphs (👥 📅 ⏱ ⚡ ✅ ⚖ 🔥 🔒 …) replaced with the existing line-icon set. Mood faces and the birthday cake stay as content.
- **States**: buttons show a busy ring while an async action or `save()` is in flight; loading ring fades in late; empty states are dashed panels with the next action; error/blocked/setup screens use an icon tile instead of emoji.
- **Keyboard**: the tab bar and + button hide while a page field is focused. Inputs are 16px (no iOS zoom). Targets ≥44px.
- **Reduced motion** collapses all animation.
- New i18n key `of_habits` in uz/en/ru; theme descriptions rewritten in all three.

## Preview
`webapp/index.html?preview` loads `preview.js`, which answers `/api/*` in the browser with labelled sample data. The server only serves `index.html`, so production never loads it. Options: `screen`, `tab`, `mode`, `lang`, `theme`, `sheet`, `scenario=empty|offline|loading`.

## Tests
`test_the_app_renders_in_the_system_face_where_there_is_one` now looks for `font:16px/`. All other CSS/HTML assertions were re-checked with a JS port; pytest itself was not run.
