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
const py = code => execFileSync(PY, ['-c', code], { encoding: 'utf8' });
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
  // 401 (signed out) and 402 (plan limit) are answers the app handles.
  page.on('console', m => { if (m.type() === 'error' && !/401|402|ErnestOS:/.test(m.text())) errors.push('console: ' + m.text()); });
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

  await step('Statistics file is a Max feature: Pro is offered the plan', async () => {
    await page.evaluate(() => goto('stats'));
    await page.waitForTimeout(400);
    await page.evaluate(() => A['stats-download']());
    await page.waitForSelector('#sheet-body [data-act="plan-buy"]', { timeout: 5000 });
    assert.equal(await page.evaluate(() => (window.__saved || []).length), 1, 'nothing saved');
    await page.evaluate(() => closeSheet());
  });

  await step('Statistics CSV is saved on the phone', async () => {
    py('import db, plans\nwith db.SessionLocal() as s:\n plans.grant(s, 777001, "max", 30, "admin"); s.commit()');
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
    // The server answers, then the app reloads its screen: wait on the server.
    let done = false;
    for (let i = 0; i < 20 && !done; i++) {
      const habits = await page.evaluate(() => api('/api/habits'));
      done = habits.habits.find(h => h.id === hid).done;
      if (!done) await page.waitForTimeout(250);
    }
    assert.ok(done, 'ticked from the notification');
    await page.waitForFunction(() => !document.querySelector('.bell-badge'), null, { timeout: 5000 });
  });

  await step('Opening the inbox again does not redraw an unchanged list', async () => {
    const before = await page.evaluate(() => {
      window.__chip = document.querySelector('[data-act="inbox-action"]');
      return Boolean(window.__chip);
    });
    assert.ok(before, 'the list is open');
    await page.evaluate(() => refreshInbox());
    assert.ok(await page.evaluate(() => window.__chip.isConnected), 'same nodes, a tap is never lost');
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
    await page.evaluate(() => { closeSheet(); goto('home'); });
    await page.click('.fab-add');
    await page.waitForTimeout(300);
    assert.ok(await page.evaluate(() => Telegram.WebApp.BackButton.isVisible), 'back shown with a sheet');
  });

  await step('Settings: profile on top, a chosen photo is saved and served', async () => {
    await page.evaluate(() => A.settings());
    await page.waitForSelector('#sheet-body .set-profile');
    // A real 1x1 PNG, so the phone-side shrink has something to decode.
    const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
    await page.setInputFiles('#sheet-body input[data-act="avatar-pick"]', { name: 'me.png', mimeType: 'image/png', buffer: png });
    await page.waitForFunction(() => !!state.me.avatar_custom, null, { timeout: 8000 });
    const served = await page.evaluate(async () => {
      const r = await fetch(`${API_BASE}/api/avatar?token=${encodeURIComponent(state.me.avatar_token)}`);
      return [r.status, r.headers.get('content-type')];
    });
    assert.deepEqual(served, [200, 'image/jpeg']);
    assert.ok(await page.locator('#sheet-body .set-profile img').count(), 'the new photo is shown');
    assert.ok(await page.locator('#sheet-body [data-act="sign-out"]').count(), 'sign out is on the settings root');
  });

  await step('Plans: a month/year switch and two buy buttons, closed by the cross', async () => {
    await page.click('#sheet-body [data-act="set-plan"]');
    await page.waitForSelector('#sheet-body .pcards');
    assert.equal(await page.locator('#sheet-body [data-act="plan-buy"]').count(), 2);
    await page.click('#sheet-body [data-act="plan-period"][data-p="year"]');
    const keys = await page.locator('#sheet-body [data-act="plan-buy"]').evaluateAll(b => b.map(x => x.dataset.key));
    assert.deepEqual(keys, ['pro_year', 'max_year']);
    await page.click('#sheet-body .sheet-head [data-act="close"]');
    await page.waitForFunction(() => !document.getElementById('sheet').classList.contains('show'));
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
