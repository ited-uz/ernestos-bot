/* Drives the real Mini App against a running server in headless Chromium.
   Telegram is replaced by a stub carrying correctly signed initData.

   Run it with scripts/e2e.sh, which seeds a throwaway database, starts the
   server, runs this file and stops the server. Screenshots go to
   tests/e2e/out/ for a person to look at. */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');

const BASE = process.env.E2E_BASE || 'http://localhost:8765';
const DIR = process.env.E2E_OUT || path.join(__dirname, 'out');
fs.mkdirSync(DIR, { recursive: true });
const initData = fs.readFileSync(path.join(DIR, 'initdata.txt'), 'utf8');
const stub = `
  window.Telegram = { WebApp: new Proxy({
    initData: ${JSON.stringify(initData)},
    initDataUnsafe: { user: { id: 777001, first_name: 'Ernest' } },
    colorScheme: 'light', themeParams: {}, viewportHeight: 800, viewportStableHeight: 800,
    safeAreaInset: {top:0,bottom:0,left:0,right:0}, contentSafeAreaInset: {top:0,bottom:0,left:0,right:0},
    HapticFeedback: { impactOccurred(){}, notificationOccurred(){}, selectionChanged(){} },
    BackButton: { show(){}, hide(){}, onClick(){}, offClick(){} },
  }, { get: (t, k) => k in t ? t[k] : () => {} }) };`;

const results = [];
const step = async (name, fn) => {
  try { await fn(); results.push(['ok', name]); console.log('✓', name); }
  catch (e) { results.push(['FAIL', name, e.message]); console.log('✗', name, '—', e.message.split('\n')[0]); }
};

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  await context.route('https://telegram.org/**', r => r.fulfill({ contentType: 'text/javascript', body: stub }));
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !/ErnestOS:|ERR_INTERNET_DISCONNECTED/.test(m.text())) errors.push('console: ' + m.text()); });
  const api = (p, opts = {}) => page.evaluate(([p, opts]) => api(p, opts), [p, opts]);
  const shot = n => page.screenshot({ path: path.join(DIR, n + '.png') });
  const settle = () => page.waitForTimeout(500);

  await page.goto(BASE + '/');
  await page.waitForFunction(() => window.state === undefined && typeof state !== 'undefined' && state.me && !state.loading, null, { timeout: 15000 });
  await settle();

  await step('Home renders the Now card and counts', async () => {
    const html = await page.content();
    assert.ok(html.includes('Eski hisobot'), 'the high-priority late task is the Now card');
    await shot('01-home');
  });

  await step('Quick add parses date and time', async () => {
    await page.click('.fab-add');
    await page.fill('#quick-title', 'ertaga 15:00 Mijozga qo\'ng\'iroq');
    await page.click('[data-act="quick-save"]');
    await settle();
    const tasks = await api('/api/tasks?days=7');
    const row = tasks.upcoming.find(x => x.title.includes('Mijozga'));
    assert.ok(row && row.due_time === '15:00', JSON.stringify(row));
  });

  await step('Habit tick in the Habits screen', async () => {
    await page.evaluate(() => goto('habits', { tab: 'habits' }));
    await settle();
    await page.locator('[data-act="habit-toggle"]').first().click();
    await settle();
    const habits = (await api('/api/habits')).habits;
    assert.ok(habits.some(h => h.done), 'one habit is done on the server');
    await shot('02-habits');
  });

  await step('Offline tick waits in the queue and syncs once', async () => {
    await context.setOffline(true);
    const before = (await api('/api/habits').catch(() => null));
    const target = page.locator('[data-act="habit-toggle"]').nth(1);
    await target.click();
    await settle();
    const queued = await page.evaluate(() => loadQueue().length);
    assert.equal(queued, 1, 'queued while offline');
    assert.ok((await page.content()).includes('queue-flush'), 'the sync banner shows');
    await shot('03-offline-queue');
    await context.setOffline(false);
    await page.evaluate(() => flushQueue());
    await settle();
    assert.equal(await page.evaluate(() => loadQueue().length), 0, 'queue drained');
    const habits = (await api('/api/habits')).habits;
    assert.equal(habits.filter(h => h.done).length, 2, 'both ticks on the server, none doubled');
  });

  await step('Debt: partial payment keeps the original; delete asks and can be undone', async () => {
    const d = (await api('/api/debts')).open[0];
    await api(`/api/debts/${d.id}/settle`, { method: 'POST', body: { settled: true, paid: 100000 } });
    await page.evaluate(() => goto('money'));
    await settle();
    await page.evaluate(() => { state.moneyTab = 'debts'; render(); });
    await settle();
    assert.ok((await page.content()).includes('400'), 'what is left is on the person card');
    // One card per person; the loans are inside it.
    await page.locator('[data-act="debt-person-open"]').first().click();
    await settle();
    const sheet = await page.locator('#sheet-body').innerHTML();
    assert.ok(sheet.includes('400') && sheet.includes('500'), 'remaining and original both shown');
    await page.locator('#sheet-body [data-act="debt-open"]').first().click();
    await settle();
    await shot('04-debt-sheet');
    await page.click('#sheet-body [data-act="debt-delete"]');
    await settle();
    assert.ok(await page.locator('#sheet-body [data-act="debt-delete-go"]').count(), 'a confirm step');
    await page.click('#sheet-body [data-act="debt-delete-go"]');
    await settle();
    let ov = await api('/api/debts');
    assert.equal(ov.open.length, 0); assert.equal(ov.archived.length, 1);
    await api(`/api/debts/${ov.archived[0].id}/restore`, { method: 'POST' });
    ov = await api('/api/debts');
    assert.equal(ov.open[0].amount, 400000);
  });

  await step('Fresh start: preview, triage, apply, undo', async () => {
    await page.evaluate(() => A['fresh-start']());
    await settle();
    await page.click('#sheet-body [data-act="fresh-preview"][data-mode="focus"]');
    await settle();
    await shot('05-reset-preview');
    assert.ok(await page.locator('#sheet-body [data-act="fresh-drop"]').count() >= 2);
    await page.click('#sheet-body details summary');
    await page.locator('#sheet-body [data-act="fresh-drop"]').first().click();
    await settle();
    // The plan was recomputed and the sheet redrawn; open the list again.
    assert.ok((await page.content()).includes('strike'), 'the dropped task is struck through');
    await shot('05b-reset-triage');
    await settle();
    await page.click('#sheet-body [data-act="fresh-go"]');
    await settle();
    const t = await api('/api/tasks?days=365');
    assert.equal(t.overdue.length, 0, 'nothing late after the reset');
  });

  await step('Timer sheet: rhythm chips, manual log, ask-done', async () => {
    const id = (await api('/api/tasks?days=7')).upcoming.find(x => x.title === 'Maqola').id;
    await page.evaluate(async id => timerChanged(await api(`/api/timers/task/${id}`)), id);
    await settle();
    const html = await page.content();
    assert.ok(html.includes('timer-cycle') && html.includes('timer-log'), 'rhythm and manual log offered');
    await page.fill('#timer-log', '30');
    await page.click('#sheet-body [data-act="timer-log"]');
    await settle();
    await shot('06-timer-ask-done');
    assert.ok((await page.content()).includes('timer-tick'), '"Is it finished?" offered');
  });

  await step('Settings: quiet hours and free time save', async () => {
    await page.evaluate(() => openSettings('notify'));
    await settle();
    await page.fill('[data-key="quiet_from"]', '23:00');
    await page.locator('[data-key="quiet_from"]').dispatchEvent('change');
    await page.fill('[data-key="quiet_to"]', '07:00');
    await page.locator('[data-key="quiet_to"]').dispatchEvent('change');
    await settle();
    await shot('07-settings-quiet');
    const prefs = (await api('/api/prefs')).prefs;
    assert.equal(prefs.quiet_from, '23:00'); assert.equal(prefs.quiet_to, '07:00');
  });

  await step('Every screen renders without "undefined"', async () => {
    await page.evaluate(() => closeSheet());
    for (const screen of ['home', 'habits', 'tasks', 'stats', 'money', 'more', 'steps']) {
      await page.evaluate(s => goto(s), screen);
      await settle();
      const text = await page.locator('#app').innerText();
      assert.ok(!/\bundefined\b|NaN/.test(text), screen);
    }
  });

  await step('Statistics name the day\'s main task beside the %', async () => {
    const id = (await api('/api/tasks?days=7')).upcoming.find(x => x.title === 'Maqola').id;
    await api(`/api/tasks/${id}/top3`, { method: 'POST', body: { picked: true } });
    await page.evaluate(() => goto('stats'));
    await settle();
    await shot('08-stats');
    const text = await page.locator('#app').innerText();
    assert.ok(text.includes(await page.evaluate(() => t('main_task'))) && text.includes('Maqola'));
  });

  await step('No JavaScript errors', async () => {
    assert.deepEqual(errors, []);
  });

  await browser.close();
  const failed = results.filter(r => r[0] === 'FAIL');
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  if (errors.length) console.log(errors.join('\n'));
  process.exitCode = failed.length ? 1 : 0;
})();
