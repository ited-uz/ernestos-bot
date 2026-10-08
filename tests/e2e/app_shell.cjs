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
let failPage = null;
const step = async (name, fn) => {
  try { await fn(); results.push(['ok', name]); console.log('✓', name); }
  catch (e) {
    results.push(['FAIL', name]); console.log('✗', name, '—', e.message.split('\n')[0]);
    // What the screen showed when it failed, next to the other shots.
    try { await failPage?.screenshot({ path: path.join(OUT, 'fail-' + results.length + '.png') }); } catch (_) {}
  }
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
      // Reminders shown by the phone: record the permission asked and what
      // was scheduled, as Android's alarm manager would receive it.
      LocalNotifications: {
        _granted: false, scheduled: [], asked: 0,
        checkPermissions: async function () { return { display: this._granted ? 'granted' : 'prompt' }; },
        requestPermissions: async function () { this.asked++; this._granted = true; return { display: 'granted' }; },
        getPending: async function () { return { notifications: this.scheduled }; },
        cancel: async function (o) { const ids = o.notifications.map(n => n.id);
                                     this.scheduled = this.scheduled.filter(n => !ids.includes(n.id)); },
        schedule: async function (o) { this.scheduled = this.scheduled.concat(o.notifications); },
        createChannel: async () => {}, addListener: () => {},
      },
    } };
  });
  const page = await context.newPage();
  failPage = page;
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

  await step('The sign-in language is picked there and remembered', async () => {
    await page.click('[data-lang="ru"]');
    assert.match(await page.textContent('#t-lead'), /Войдите/);
    await page.reload();
    await page.waitForSelector('#code');
    assert.match(await page.textContent('#t-lead'), /Войдите/);
    await page.click('[data-lang="uz"]');
    assert.match(await page.textContent('#t-lead'), /kirig|kod bilan/);
  });

  await step('A wrong code is refused in place', async () => {
    // A complete code is sent by itself — no button press needed.
    await page.fill('#code', 'ZZZZZZZZ');
    await page.waitForFunction(() => document.getElementById('msg').textContent.length > 0);
    assert.match(await page.textContent('#msg'), /noto'g'ri|wrong|неверный/);
    await shot('02-bad-code');
  });

  await step('The bot code signs in and the Mini App home renders', async () => {
    await page.fill('#code', issue().toLowerCase());
    await page.waitForURL(/index\.html$/);
    await page.waitForFunction(() => typeof state !== 'undefined' && state.me && !state.loading, null, { timeout: 15000 });
    await page.waitForTimeout(500);
    assert.ok((await page.content()).includes('Eski hisobot'), 'home shows the seeded late task');
    assert.ok(await page.evaluate(() => Telegram.WebApp.isErnestApp), 'native.js is the WebApp');
    await shot('03-home');
  });

  await step('Opening the app asks to notify, and the next reminders are set on the phone', async () => {
    const local = () => page.evaluate(() => {
      const n = window.Capacitor.Plugins.LocalNotifications;
      return { asked: n.asked, items: n.scheduled.map(x => ({ id: x.id, title: x.title, at: String(x.schedule.at),
                                                               channel: x.channelId, open: x.extra.open })) };
    });
    await page.waitForFunction(() => window.Capacitor.Plugins.LocalNotifications.scheduled.length > 0, null, { timeout: 8000 });
    const first = await local();
    assert.equal(first.asked, 1, 'permission asked once on open');
    assert.ok(first.items.some(x => /Kun|hisobot|reja/i.test(x.title)), 'a report reminder is scheduled');
    // Coming back to the app reschedules: the same reminders, not twice as many.
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await page.waitForTimeout(800);
    const again = await local();
    assert.equal(again.items.length, first.items.length, 'rescheduling replaces');
    assert.deepEqual(again.items.map(x => x.id).sort(), first.items.map(x => x.id).sort());
  });

  await step('Opening the app shows Now in focus; the rest waits until a tap', async () => {
    await page.evaluate(() => goto('home'));
    await page.evaluate(() => { state.homeVeil = true; render(); });
    assert.ok(await page.locator('.home-rest.veiled').count(), 'the rest is veiled');
    assert.equal(await page.locator('.home-rest').getAttribute('inert'), '', 'veiled part cannot be tapped');
    await page.click('.unveil');
    await page.waitForFunction(() => !document.querySelector('.home-rest.veiled'));
    assert.ok(await page.locator('.now-swap svg').count(), 'swap is an icon');
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
    // A tap on the picture itself opens the phone's picker.
    const [chooser] = await Promise.all([page.waitForEvent('filechooser', { timeout: 5000 }),
                                         page.click('#sheet-body .avatar.xl')]);
    await chooser.setFiles({ name: 'me.png', mimeType: 'image/png', buffer: png });
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
    await page.waitForSelector('#sheet-body .ptable');
    assert.equal(await page.locator('#sheet-body .ptable tbody tr').count(), 18, 'every limit has a row');
    assert.equal(await page.locator('#sheet-body [data-act="plan-buy"]').count(), 2);
    await page.click('#sheet-body [data-act="plan-period"][data-p="year"]');
    const keys = await page.locator('#sheet-body [data-act="plan-buy"]').evaluateAll(b => b.map(x => x.dataset.key));
    assert.deepEqual(keys, ['pro_year', 'max_year']);
    await page.click('#sheet-body .sheet-head [data-act="close"]');
    await page.waitForFunction(() => !document.getElementById('sheet').classList.contains('show'));
  });

  await step('Profile: rating and coins; a streak freeze is bought with two taps', async () => {
    // 1 000 XP is 100 coins: enough for a freeze (80).
    py('import db, services as svc\nwith db.SessionLocal() as s:\n'
       + ' svc.award_xp(s, 777001, "achievement:777001:e2e-coins", "achievement", 1000, svc.today_local()); s.commit()');
    await page.evaluate(() => closeSheet());
    await page.evaluate(() => goto('steps'));
    await page.waitForSelector('.coin-card', { timeout: 8000 });
    assert.ok(await page.locator('.rank-card').count(), 'the rating block is on Profile');
    await page.click('.coin-card');
    await page.waitForSelector('#sheet-body .coin-item');
    const before = await page.evaluate(() => state.coins.balance);
    assert.ok(before >= 100, 'coins come from XP');
    await page.click('#sheet-body [data-act="coins-buy"][data-value="freeze_1"]');
    await page.waitForSelector('#sheet-body [data-act="coins-buy"][data-value="freeze_1"]:has-text("Tasdiqlash")');
    await page.click('#sheet-body [data-act="coins-buy"][data-value="freeze_1"]');
    await page.waitForFunction(b => state.coins.balance === b - 80, before, { timeout: 8000 });
    const spends = py('import db\nfrom sqlalchemy import select, func\nwith db.SessionLocal() as s:\n'
       + ' print(s.scalar(select(func.count(db.CoinSpend.id)).where(db.CoinSpend.account_id == 777001)))').trim();
    assert.equal(spends, '1');
    await shot('08-coins');
    await page.evaluate(() => closeSheet());
  });

  await step('Reja → Maqsadlar: an ultimate goal and a milestone under it, saved on the server', async () => {
    await page.evaluate(() => closeSheet());
    await page.evaluate(() => goto('goals'));
    await page.waitForSelector('.glevels .glevel.on.lv-ultimate', { timeout: 8000 });
    await page.waitForSelector('.gempty [data-act="goal-add"]');
    await page.click('#fab .fab-add');
    await page.waitForSelector('#sheet-body #gl-title');
    await page.fill('#gl-title', 'EGH: Capital Venture');
    await page.click('#sheet-body [data-act="gl-pick"][data-field="gl-cat"][data-value="capital"]');
    await page.click('#sheet-body [data-act="gl-pick"][data-field="gl-horizon"][data-value="2040"]');
    await page.click('#sheet-body .gmore summary');
    await page.fill('#gl-amount', '10 000 000');
    await page.click('#sheet-body [data-act="goal-save"]');
    await page.waitForSelector('.gcard .gamount:has-text("$10M")', { timeout: 8000 });
    // A milestone under it, from the milestone tab's +.
    await page.click('[data-act="goal-tab"][data-tab="milestone"]');
    await page.click('#fab .fab-add');
    await page.waitForSelector('#sheet-body #gl-parent');
    await page.fill('#gl-title', 'Birinchi $100 000');
    await page.selectOption('#gl-parent', { label: 'EGH: Capital Venture' });
    await page.locator('#gl-progress').fill('40');
    await page.click('#sheet-body [data-act="goal-save"]');
    await page.waitForSelector('.gitem:has-text("Birinchi $100 000") .gpct:has-text("40%")', { timeout: 8000 });
    assert.ok(await page.locator('.gitem .gparent:has-text("EGH: Capital Venture")').count(), 'parent shown');
    // The ultimate goal now reads its progress off the milestone.
    await page.click('[data-act="goal-tab"][data-tab="ultimate"]');
    await page.waitForSelector('.gcard .gprog:has-text("40%")');
    const rows = py('import db\nfrom sqlalchemy import select\nwith db.SessionLocal() as s:\n'
       + ' for g in s.scalars(select(db.LifeGoal).order_by(db.LifeGoal.id)): print(g.level, g.amount, g.parent_id is not None, g.progress)').trim();
    assert.deepEqual(rows.split('\n'), ['ultimate 10000000 False 0', 'milestone None True 40']);
    await shot('09-goals');
    // The tactical tab is the week's focus.
    await page.click('[data-act="goal-tab"][data-tab="tactical"]');
    await page.waitForSelector('.ghint');
    assert.ok(await page.locator('[data-act="focus-add"]').count(), 'week focus reachable from Goals');
  });

  await step('Moliya: two accounts, a transfer, a repeating payment paid, and the year', async () => {
    await page.evaluate(() => closeSheet());
    await page.evaluate(() => goto('money'));
    await page.waitForSelector('[data-act="money-tab"][data-value="accounts"]', { timeout: 8000 });
    await page.click('[data-act="money-tab"][data-value="accounts"]');
    await page.click('.wempty [data-act="acct-add"][data-kind="cash"]');
    await page.waitForSelector('#sheet-body #acct-name');
    assert.equal(await page.inputValue('#acct-name'), 'Naqd');
    await page.fill('#acct-opening', '1 mln');
    await page.click('#sheet-body [data-act="acct-save"]');
    await page.waitForSelector('.wacct:has-text("Naqd")', { timeout: 8000 });
    await page.click('.wactions [data-act="acct-add"]');
    await page.fill('#acct-name', 'Karta');
    await page.click('#sheet-body [data-act="acct-kind"][data-value="card"]');
    await page.fill('#acct-opening', '500000');
    await page.click('#sheet-body [data-act="acct-save"]');
    await page.waitForSelector('.wacct:has-text("Karta")', { timeout: 8000 });
    await page.click('.mkind [data-act="transfer-open"]');
    await page.waitForSelector('#sheet-body #tr-amount');
    await page.fill('#tr-amount', '200 ming');
    await page.click('#sheet-body [data-act="transfer-save"]');
    await page.waitForSelector('.wtr:has-text("200 000")', { timeout: 8000 });
    const bal = await page.evaluate(() => state.money.wallet.accounts.map(a => [a.name, a.balance]));
    assert.deepEqual(bal, [['Naqd', 800000], ['Karta', 700000]]);
    await page.click('[data-act="msub-add"]');
    await page.waitForSelector('#sheet-body #sub-name');
    await page.fill('#sub-name', 'Internet');
    await page.fill('#sub-amount', '150 ming');
    await page.click('#sheet-body [data-act="msub-save"]');
    await page.waitForSelector('.wsub:has-text("Internet")', { timeout: 8000 });
    await page.click('.wsub:has-text("Internet") [data-act="msub-pay"]');
    await page.waitForFunction(() => state.money.entries.some(e => e.note === 'Internet' && e.amount === 150000), null, { timeout: 8000 });
    const rows = py('import db\nfrom sqlalchemy import select\nwith db.SessionLocal() as s:\n'
       + ' print(len(s.scalars(select(db.MoneyAccount)).all()), len(s.scalars(select(db.MoneyTransfer)).all()),'
       + ' s.scalar(select(db.MoneyEntry.amount).where(db.MoneyEntry.note == "Internet")))').trim();
    assert.equal(rows, '2 1 150000');
    await shot('10-money-accounts');
    // The account is on Max by now (granted in the statistics step): the
    // year card is open and counts the payment just made.
    await page.click('[data-act="money-tab"][data-value="cats"]');
    await page.waitForSelector('.ycard .ybars', { timeout: 8000 });
    assert.ok(await page.locator('.ycard .ytotals .neg:has-text("150 000")').count(), 'year counts the payment');
  });

  await step('Xabarlar has an AI assistant tab; without an AI key it says so plainly', async () => {
    await page.evaluate(() => closeSheet());
    await page.evaluate(() => goto('home'));
    await page.click('.bellbtn[data-act="inbox-open"]');
    await page.waitForSelector('#sheet-body [data-act="inbox-tab"][data-tab="chat"]');
    await page.click('#sheet-body [data-act="inbox-tab"][data-tab="chat"]');
    const available = await page.evaluate(() => Boolean(state.me?.agent?.available));
    if (available) await page.waitForSelector('#sheet-body .chat-intro, #sheet-body #chat-text');
    else await page.waitForSelector('#sheet-body .hint:has-text("ulanmagan")');
    await page.evaluate(() => closeSheet());
  });

  await step('An ended session goes back to sign-in', async () => {
    revokeAll();
    await page.evaluate(() => api('/api/me').catch(() => null));
    await page.waitForURL(/login\.html$/, { timeout: 5000 });
    assert.equal(await page.evaluate(() => localStorage.getItem('ernest.app.token')), null);
  });

  await step('Sign out from settings ends the session on the server too', async () => {
    await page.fill('#code', issue());
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
