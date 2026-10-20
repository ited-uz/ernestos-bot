/* The web app (/app/) against a real server, in headless Chromium: a laptop
   and a phone. The way in from a plain browser, email sign-up and setup,
   the bot code, every screen at both sizes (no sideways scroll, nothing
   undefined, the laptop's side bar), back, a download, the install files,
   opening offline, signing out. */
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');

const BASE = process.env.WEB_BASE;
const OUT = process.env.E2E_OUT;
const PY = process.env.PY;
const SHOTS = path.join(OUT, 'web');
fs.mkdirSync(SHOTS, { recursive: true });
const issue = () => execFileSync(PY, ['-c',
  'import db, app_auth\nwith db.SessionLocal() as s: print(app_auth.issue_code(s, 777001))'],
  { encoding: 'utf8' }).trim();
const readCode = to => {
  const dir = path.join(OUT, 'mail-web');
  const files = fs.readdirSync(dir).filter(f => f.endsWith('.json')).sort().reverse();
  for (const f of files) {
    const m = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
    if (m.to === to) return m.subject.match(/\b(\d{6})\b/)[1];
  }
  throw new Error('no mail to ' + to);
};

const results = [];
let failPage = null;
const step = async (name, fn) => {
  try { await fn(); results.push(['ok', name]); console.log('✓', name); }
  catch (e) {
    results.push(['FAIL', name]); console.log('✗', name, '—', e.message.split('\n')[0]);
    try { await failPage?.screenshot({ path: path.join(SHOTS, 'fail-' + results.length + '.png') }); } catch (_) {}
  }
};
const ready = page => page.waitForFunction(() => typeof state !== 'undefined' && state.me && !state.loading,
  null, { timeout: 15000 });
const wide = page => page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
const SCREENS = ['home', 'habits', 'tasks', 'goals', 'team', 'notes', 'money', 'steps', 'stats', 'people'];

(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const watch = (page, who) => {
    page.on('pageerror', e => errors.push(`${who} pageerror: ${e.message}`));
    page.on('console', m => { if (m.type() === 'error' && !/401|402|409|ErnestOS:|ERR_INTERNET_DISCONNECTED|Failed to load resource|ErnestOS boot failed: TypeError: Failed to fetch/.test(m.text()))
      errors.push(`${who} console: ${m.text()}`); });
  };

  /* ---------------- a laptop ---------------- */
  const laptop = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  // Telegram's script, as it behaves in a plain browser: a WebApp with no
  // launch data (the sandbox has no route to telegram.org).
  const outsideTelegram = r => r.fulfill({ contentType: 'text/javascript',
    body: 'window.Telegram={WebApp:{initData:"",ready(){},expand(){}}};' });
  await laptop.route('https://telegram.org/**', outsideTelegram);
  const lp = await laptop.newPage();
  failPage = lp;
  watch(lp, 'laptop');

  await step('A plain browser at the Mini App address goes to the web app, then to sign-in', async () => {
    await lp.goto(BASE + '/');
    await lp.waitForURL(/\/app\/login$/);
    await lp.waitForSelector('#email');
    assert.equal(await lp.evaluate(() => window.ErnestApp.web), true);
  });

  await step('Sign-in on a laptop: the way in on the left, why ErnestOS on the right', async () => {
    const layout = await lp.evaluate(() => {
      const main = document.querySelector('main'), usp = document.querySelector('.usp');
      return { grid: getComputedStyle(main).display, uspLeft: usp.getBoundingClientRect().left,
               cardLeft: document.getElementById('email-card').getBoundingClientRect().left };
    });
    assert.equal(layout.grid, 'grid');
    assert.ok(layout.uspLeft > layout.cardLeft + 300, JSON.stringify(layout));
    assert.ok(!(await wide(lp)));
    await lp.screenshot({ path: path.join(SHOTS, 'laptop-01-login.png') });
  });

  await step('The bot code signs the laptop in; no token anywhere the page can read', async () => {
    await lp.evaluate(() => { document.getElementById('tg-box').open = true; });
    await lp.fill('#code', issue());
    await lp.waitForURL(/\/app\/$/);
    await ready(lp);
    const seen = await lp.evaluate(() => ({ init: Telegram.WebApp.initData, cookie: document.cookie,
      stored: Object.keys(localStorage).filter(k => /token/i.test(k)), web: Telegram.WebApp.isErnestWeb }));
    assert.equal(seen.init, 'web');
    assert.equal(seen.cookie, 'eos_in=1', 'only the hint cookie is visible to scripts');
    assert.deepEqual(seen.stored, []);
    assert.equal(seen.web, true);
    assert.ok((await lp.content()).includes('Eski hisobot'), 'home shows the seeded task');
  });

  await step('Sample data for the screens (tasks, goals, money, notes) through the web session', async () => {
    const made = await lp.evaluate(async () => {
      const out = [];
      const post = (p, b) => api(p, { method: 'POST', body: b }).then(() => out.push(p)).catch(e => out.push(p + ' ' + e.status));
      await post('/api/tasks', { title: 'Investor bilan uchrashuv', deadline: todayISO(), due_time: '15:00' });
      await post('/api/tasks', { title: 'Landing sahifani tugatish', deadline: todayISO(), priority: 'high' });
      await post('/api/goals', { title: '1 mln mijoz', level: 'ultimate', category: 'business', horizon: '2035' });
      await post('/api/money', { kind: 'income', amount: 12000000, category: 'salary', note: 'Oylik' });
      await post('/api/money', { kind: 'expense', amount: 450000, category: 'food', note: 'Bozor' });
      await post('/api/notes', { title: 'G‘oya — AI savdo agenti', body: '## G‘oya\nDo‘konlar uchun Telegram agent.\n\n- [ ] 10 ta qo‘ng‘iroq', kind: 'idea' });
      return out;
    });
    assert.ok(made.every(x => !/ \d+$/.test(x)), JSON.stringify(made));
  });

  await step('Laptop: a side bar instead of the bottom bar, and room for the screen', async () => {
    await lp.evaluate(() => goto('home'));
    await lp.waitForTimeout(500);
    const box = await lp.evaluate(() => {
      const nav = document.getElementById('nav').getBoundingClientRect();
      const app = document.getElementById('app').getBoundingClientRect();
      return { navW: nav.width, navH: nav.height, navLeft: nav.left, appLeft: app.left, appW: app.width };
    });
    assert.ok(box.navW >= 200 && box.navW <= 280 && box.navH >= 800 && box.navLeft === 0, 'side bar: ' + JSON.stringify(box));
    assert.ok(box.appLeft >= box.navW, 'content beside the bar: ' + JSON.stringify(box));
    assert.ok(box.appW >= 720, 'content wider than a phone: ' + JSON.stringify(box));
  });

  await step('Laptop: every screen fits, nothing undefined', async () => {
    for (const s of SCREENS) {
      await lp.evaluate(s => goto(s), s);
      await lp.waitForTimeout(600);
      const text = await lp.locator('#app').innerText();
      assert.ok(!/\bundefined\b|NaN/.test(text), s + ' shows undefined/NaN');
      assert.ok(!(await wide(lp)), s + ' scrolls sideways');
      await lp.screenshot({ path: path.join(SHOTS, `laptop-${s}.png`) });
    }
  });

  await step('Laptop in dark mode: the side bar and the screens follow the system', async () => {
    await lp.emulateMedia({ colorScheme: 'dark' });
    for (const s of ['home', 'notes', 'money']) {
      await lp.evaluate(s => goto(s), s);
      await lp.waitForTimeout(500);
      await lp.screenshot({ path: path.join(SHOTS, `laptop-dark-${s}.png`) });
    }
    const look = await lp.evaluate(() => ({ mode: document.documentElement.dataset.mode,
      nav: getComputedStyle(document.getElementById('nav')).backgroundColor,
      body: getComputedStyle(document.body).backgroundColor }));
    assert.equal(look.mode, 'dark');
    // rgb(28, 28, 30) or color(srgb 0.14 0.14 0.15 / 0.84)
    const dark = c => { const n = c.match(/[\d.]+/g).map(Number).slice(0, 3);
      const k = /^color\(/.test(c) ? 255 : 1; return n.reduce((a, v) => a + v * k, 0) < 200; };
    assert.ok(dark(look.nav) && dark(look.body), JSON.stringify(look));
    await lp.emulateMedia({ colorScheme: 'light' });
  });

  await step('Laptop: a sheet opens as a dialog in the middle; back or Escape closes it', async () => {
    await lp.evaluate(() => goto('home'));
    await lp.waitForTimeout(300);
    await lp.click('.fab-add');
    await lp.waitForSelector('#sheet.show .sheet-inner');
    const r = await lp.evaluate(() => {
      const b = document.querySelector('#sheet .sheet-inner').getBoundingClientRect();
      return { top: b.top, bottom: innerHeight - b.bottom, w: b.width };
    });
    assert.ok(r.top > 20 && r.bottom > 20 && r.w <= 680, 'centred dialog: ' + JSON.stringify(r));
    await lp.screenshot({ path: path.join(SHOTS, 'laptop-sheet.png') });
    await lp.goBack();
    await lp.waitForFunction(() => !document.querySelector('#sheet.show'));
    assert.match(lp.url(), /\/app\/$/, 'back closed the sheet and stayed in the app');
    // Escape does the same on a keyboard.
    await lp.click('.fab-add');
    await lp.waitForSelector('#sheet.show .sheet-inner');
    await lp.keyboard.press('Escape');
    await lp.waitForFunction(() => !document.querySelector('#sheet.show'));
  });

  await step('Browser back walks back one screen inside the app', async () => {
    await lp.evaluate(() => goto('money'));
    await lp.waitForTimeout(300);
    await lp.goBack();
    await lp.waitForFunction(() => state.screen === 'home');
    assert.match(lp.url(), /\/app\/$/);
  });

  await step('Data export is a real download', async () => {
    await lp.evaluate(() => goto('home'));
    const [dl] = await Promise.all([
      lp.waitForEvent('download', { timeout: 10000 }),
      lp.evaluate(() => A.export()),
    ]);
    assert.match(dl.suggestedFilename(), /^ernestos-.*\.json$/);
    const file = path.join(SHOTS, dl.suggestedFilename());
    await dl.saveAs(file);
    const data = JSON.parse(fs.readFileSync(file, 'utf8'));
    assert.ok(JSON.stringify(data).includes('Eski hisobot'));
  });

  await step('Install files: manifest, icons, service worker in control', async () => {
    const m = await lp.evaluate(async () => (await fetch('/app/manifest.webmanifest')).json());
    assert.equal(m.display, 'standalone');
    await lp.waitForFunction(() => navigator.serviceWorker && navigator.serviceWorker.controller, null, { timeout: 10000 })
      .catch(async () => { await lp.reload(); await ready(lp); await lp.waitForFunction(() => navigator.serviceWorker.controller, null, { timeout: 10000 }); });
  });

  await step('No network: the app still opens from the kept copy', async () => {
    await laptop.setOffline(true);
    await lp.reload();
    await lp.waitForFunction(() => typeof state !== 'undefined' && !state.loading, null, { timeout: 15000 });
    const text = await lp.locator('body').innerText();
    assert.ok(text.length > 50, 'something is shown offline');
    await lp.screenshot({ path: path.join(SHOTS, 'laptop-offline.png') });
    await laptop.setOffline(false);
    await lp.reload();
    await ready(lp);
  });

  await step('Signing out ends the session; the app sends the browser to sign-in', async () => {
    lp.once('dialog', d => d.accept());
    await lp.evaluate(() => A['sign-out']());
    await lp.waitForURL(/\/app\/login$/);
    const me = await lp.evaluate(async () => (await fetch('/api/me', { headers: { 'X-Telegram-Init-Data': 'web' } })).status);
    assert.equal(me, 401);
    await lp.goto(BASE + '/app/');
    await lp.waitForURL(/\/app\/login/);
  });

  /* ---------------- a phone ---------------- */
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2,
                                           isMobile: true, hasTouch: true });
  const pp = await phone.newPage();
  failPage = pp;
  watch(pp, 'phone');

  await step('Phone: a new email signs up, setup runs, the day opens', async () => {
    await pp.goto(BASE + '/app/');
    await pp.waitForURL(/\/app\/login$/);
    await pp.screenshot({ path: path.join(SHOTS, 'phone-01-login.png') });
    await pp.fill('#email', 'telefon@example.com');
    await pp.click('#send');
    await pp.waitForSelector('#otp-form:not([hidden])');
    await pp.fill('#otp', readCode('telefon@example.com'));
    await pp.waitForURL(/\/app\/$/);
    await pp.waitForSelector('#su-name', { timeout: 15000 });
    await pp.screenshot({ path: path.join(SHOTS, 'phone-02-setup.png') });
    await pp.fill('#su-name', 'Malika');
    await pp.click('[data-act="su-finish"]');
    await pp.waitForFunction(() => typeof state !== 'undefined' && state.me && state.me.onboarded && !state.loading
      && !state.needsSetup, null, { timeout: 15000 });
    assert.equal(await pp.evaluate(() => state.me.email), 'telefon@example.com');
  });

  await step('Phone: the bottom bar, every screen fits', async () => {
    const nav = await pp.evaluate(() => { const r = document.getElementById('nav').getBoundingClientRect();
      return { top: r.top, h: r.height, w: r.width }; });
    assert.ok(nav.w >= 380 && nav.top > 700, 'bottom bar: ' + JSON.stringify(nav));
    for (const s of SCREENS) {
      await pp.evaluate(s => goto(s), s);
      await pp.waitForTimeout(500);
      const text = await pp.locator('#app').innerText();
      assert.ok(!/\bundefined\b|NaN/.test(text), s + ' shows undefined/NaN');
      assert.ok(!(await wide(pp)), s + ' scrolls sideways');
      await pp.screenshot({ path: path.join(SHOTS, `phone-${s}.png`) });
    }
  });

  await step('Phone: a sheet comes up from the bottom; back closes it', async () => {
    await pp.evaluate(() => goto('home'));
    await pp.waitForTimeout(300);
    await pp.tap('.fab-add');
    await pp.waitForSelector('#sheet.show .sheet-inner');
    const bottom = await pp.evaluate(() => innerHeight - document.querySelector('#sheet .sheet-inner').getBoundingClientRect().bottom);
    assert.ok(bottom < 2, 'sheet sits on the bottom edge');
    await pp.goBack();
    await pp.waitForFunction(() => !document.querySelector('#sheet.show'));
  });

  /* ---------------- in between: a tablet ---------------- */
  await step('Tablet (768px) and a small laptop (1024px): every screen fits', async () => {
    for (const w of [768, 1024, 1920]) {
      await pp.setViewportSize({ width: w, height: w === 768 ? 1024 : 900 });
      for (const s of SCREENS) {
        await pp.evaluate(s => goto(s), s);
        await pp.waitForTimeout(250);
        assert.ok(!(await wide(pp)), `${w}px ${s} scrolls sideways`);
      }
      await pp.evaluate(() => goto('home'));
      await pp.waitForTimeout(300);
      await pp.screenshot({ path: path.join(SHOTS, `w${w}-home.png`) });
    }
  });

  await step('No JavaScript errors', async () => {
    if (errors.length) console.log(JSON.stringify(errors, null, 1));
    assert.deepEqual(errors, []);
  });

  await browser.close();
  const failed = results.filter(r => r[0] !== 'ok');
  console.log(`\n${results.length - failed.length}/${results.length} web checks passed`);
  process.exit(failed.length ? 1 : 0);
})();
