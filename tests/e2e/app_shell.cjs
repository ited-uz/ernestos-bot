/* The phone app's web layer (mobile/www) against a real server, in headless
   Chromium at phone size: sign in with a bot code, use the Mini App through
   native.js, and get sent back to sign-in when the session ends. */
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const path = require('path');
const assert = require('assert/strict');

const SHELL = process.env.APP_SHELL_BASE;        // where mobile/www is served
const OUT = process.env.E2E_OUT;
const PY = process.env.PY;
const issue = () => execFileSync(PY, ['-c',
  'import db, app_auth\nwith db.SessionLocal() as s: print(app_auth.issue_code(s, 777001))'],
  { encoding: 'utf8' }).trim();
// What the bot's reminder job does for an account with the app: one inbox row
// with the message's own buttons. Returns the habit's id.
const notify = name => Number(execFileSync(PY, ['-c',
  'import asyncio, db, app_push\nfrom sqlalchemy import select\n'
  + 'from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M\n'
  + 'with db.SessionLocal() as s:\n'
  + ` h = s.scalar(select(db.Habit).where(db.Habit.name == ${JSON.stringify(name)}))\n`
  + 'm = M([[B("15 daqiqa", callback_data=f"snz:h:{h.id}:15"), B("Bajarildi", callback_data=f"habit:toggle:{h.id}")]])\n'
  + 'asyncio.run(app_push.deliver(777001, "🔔 <b>Odat vaqti</b>\\n" + h.name, m, kind="reminder"))\n'
  + 'print(h.id)'], { encoding: 'utf8' }).trim());
const revokeAll = () => execFileSync(PY, ['-c',
  'import db\nfrom sqlalchemy import update\nwith db.SessionLocal() as s:\n'
  + ' s.execute(update(db.AppSession).values(revoked_at=db.utcnow())); s.commit()']);

const results = [];
const step = async (name, fn) => {
  try { await fn(); results.push(['ok', name]); console.log('✓', name); }
  catch (e) { results.push(['FAIL', name]); console.log('✗', name, '—', e.message.split('\n')[0]); }
};

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  // Stand-ins for the phone's Filesystem and Share plugins: record the file
  // the app saves instead of opening Android's share sheet.
  await context.addInitScript(() => {
    window.Capacitor = { getPlatform: () => 'android', Plugins: {
      Filesystem: { writeFile: async o => { (window.__saved = window.__saved || []).push(o);
                                            return { uri: 'file:///cache/' + o.path }; } },
      Share: { share: async o => { window.__shared = o; } },
    } };
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !/401|ErnestOS:/.test(m.text())) errors.push('console: ' + m.text()); });
  const shot = n => page.screenshot({ path: path.join(OUT, 'app-' + n + '.png') });

  await step('No session: the app opens on the sign-in screen', async () => {
    await page.goto(SHELL + '/index.html');
    await page.waitForURL(/login\.html$/);
    await page.waitForSelector('#code');
    await shot('01-login');
  });

  await step('A wrong code is refused in place', async () => {
    await page.fill('#code', 'ZZZZZZZZ');
    await page.click('#submit');
    await page.waitForFunction(() => document.getElementById('msg').textContent.length > 0);
    assert.match(await page.textContent('#msg'), /noto'g'ri|wrong|неверный/);
    await shot('02-bad-code');
  });

  await step('The bot code signs in and the Mini App home renders', async () => {
    await page.fill('#code', issue().toLowerCase());
    assert.match(await page.inputValue('#code'), /^[A-Z0-9]{4}-[A-Z0-9]{4}$/, 'typed code is formatted');
    await page.click('#submit');
    await page.waitForURL(/index\.html$/);
    await page.waitForFunction(() => typeof state !== 'undefined' && state.me && !state.loading, null, { timeout: 15000 });
    await page.waitForTimeout(500);
    assert.ok((await page.content()).includes('Eski hisobot'), 'home shows the seeded late task');
    assert.ok(await page.evaluate(() => Telegram.WebApp.isErnestApp), 'native.js is the WebApp');
    await shot('03-home');
  });

  await step('A habit tick reaches the server through the app token', async () => {
    await page.evaluate(() => goto('habits', { tab: 'habits' }));
    await page.waitForTimeout(500);
    await page.locator('[data-act="habit-toggle"]').first().click();
    await page.waitForTimeout(600);
    const habits = await page.evaluate(() => api('/api/habits'));
    assert.ok(habits.habits.some(h => h.done));
    await shot('04-habits');
  });

  await step('Data export is saved on the phone, not sent to the bot', async () => {
    await page.evaluate(() => A.export());
    await page.waitForFunction(() => (window.__saved || []).length === 1, null, { timeout: 5000 });
    const saved = await page.evaluate(() => window.__saved[0]);
    assert.match(saved.path, /^ernestos-\d{4}-\d{2}-\d{2}\.json$/);
    assert.ok(JSON.parse(saved.data).habits.length >= 2, 'the whole export, habits included');
    assert.deepEqual(await page.evaluate(() => window.__shared.files), ['file:///cache/' + saved.path]);
  });

  await step('Statistics CSV is saved on the phone', async () => {
    await page.evaluate(() => goto('stats'));
    await page.waitForTimeout(400);
    await page.evaluate(() => A['stats-download']());
    await page.waitForFunction(() => window.__saved.length === 2, null, { timeout: 5000 });
    const saved = await page.evaluate(() => window.__saved[1]);
    assert.match(saved.path, /^ernestos-(week|month|year)-.*\.csv$/);
    assert.ok(saved.data.split('\n').length > 2, 'csv has rows');
  });

  await step('A bot message lands in the inbox; its button ticks the habit', async () => {
    await page.evaluate(() => goto('home'));
    const hid = notify('Sport');
    await page.evaluate(() => refreshInbox());
    await page.waitForSelector('.bell-badge');
    assert.equal((await page.textContent('.bell-badge')).trim(), '1');
    await shot('06-bell');
    await page.click('[data-act="inbox-open"]');
    await page.waitForSelector('[data-act="inbox-action"]');
    await shot('07-inbox');
    await page.click(`[data-act="inbox-action"][data-cb="habit:toggle:${hid}"]`);
    await page.waitForTimeout(600);
    const habits = await page.evaluate(() => api('/api/habits'));
    assert.ok(habits.habits.find(h => h.id === hid).done, 'ticked from the notification');
    assert.equal(await page.locator('.bell-badge').count(), 0, 'read once opened');
    await page.evaluate(() => closeSheet());
  });

  await step('Every screen renders without "undefined"', async () => {
    for (const screen of ['home', 'habits', 'tasks', 'stats', 'money', 'more']) {
      await page.evaluate(s => goto(s), screen);
      await page.waitForTimeout(400);
      const text = await page.locator('#app').innerText();
      assert.ok(!/\bundefined\b|NaN/.test(text), screen);
    }
    await shot('05-more');
  });

  await step('Back closes an open sheet first', async () => {
    await page.evaluate(() => goto('home'));
    await page.click('.fab-add');
    await page.waitForTimeout(300);
    assert.ok(await page.evaluate(() => Telegram.WebApp.BackButton.isVisible), 'back shown with a sheet');
  });

  await step('An ended session goes back to sign-in', async () => {
    revokeAll();
    await page.evaluate(() => api('/api/me').catch(() => null));
    await page.waitForURL(/login\.html$/, { timeout: 5000 });
    assert.equal(await page.evaluate(() => localStorage.getItem('ernest.app.token')), null);
  });

  await step('Sign out from settings ends the session on the server too', async () => {
    await page.fill('#code', issue());
    await page.click('#submit');
    await page.waitForURL(/index\.html$/);
    await page.waitForFunction(() => typeof state !== 'undefined' && state.me && !state.loading, null, { timeout: 15000 });
    const token = await page.evaluate(() => localStorage.getItem('ernest.app.token'));
    page.once('dialog', d => d.accept());
    await page.evaluate(() => A['app-sign-out']());
    await page.waitForURL(/login\.html$/);
    await page.waitForTimeout(300);
    const status = await page.evaluate(async t => (await fetch('/api/me', { headers: { 'X-Telegram-Init-Data': t } })).status, token);
    assert.equal(status, 401);
  });

  await step('No JavaScript errors', async () => { assert.deepEqual(errors, []); });

  await browser.close();
  const failed = results.filter(r => r[0] === 'FAIL');
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  if (errors.length) console.log(errors.join('\n'));
  process.exitCode = failed.length ? 1 : 0;
})();
