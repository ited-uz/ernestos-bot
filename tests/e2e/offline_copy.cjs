/* The offline copy (mobile/scripts/build-demo.mjs --offline) in headless
   Chromium: first-run setup, a day of entries, a reload, and the next day.
   No server — everything lives in the page's own storage. */
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const OUT = process.env.E2E_OUT || path.join(__dirname, 'out');
fs.mkdirSync(OUT, { recursive: true });
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ernest-offline-'));
const body = path.join(dir, 'body.html');
execFileSync('node', [path.join(ROOT, 'mobile/scripts/build-demo.mjs'), body, 'uz', '--offline'], { stdio: 'inherit' });
// The publish skeleton wraps the body in a document; so does this.
const page = '<!doctype html><html lang="uz"><head><meta charset="utf-8">'
  + '<meta name="viewport" content="width=device-width,initial-scale=1"></head><body>'
  + fs.readFileSync(body, 'utf8') + '</body></html>';
const server = http.createServer((req, res) => {
  if (req.url !== '/') { res.writeHead(404); return res.end(); }
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); res.end(page);
});

const errors = [];
const D1 = '2026-10-14', D2 = '2026-10-15';
const api = (p, url, opts) => p.evaluate(([u, o]) => api(u, o), [url, opts || {}]);

(async () => {
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const URL = `http://127.0.0.1:${server.address().port}/`;
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const open = async time => {
    const p = await ctx.newPage();
    await p.clock.install({ time: new Date(time) });
    p.on('pageerror', e => errors.push(e.message));
    await p.goto(URL);
    await p.clock.runFor(2500);
    return p;
  };
  const step = (p, ms = 1200) => p.clock.runFor(ms);

  // 1. A first visit is the app's own setup, with nothing filled in.
  let p = await open(`${D1}T09:00:00`);
  assert.equal(await p.locator('#su-name').count(), 1, 'no first-run setup');
  await p.fill('#su-name', 'Test');
  for (const m of ['wake', 'prayer', 'journal']) {
    const sw = p.locator(`[data-act="su-mod"][data-value="${m}"]`);
    if (await sw.count() && !/\bon\b/.test(await sw.getAttribute('class'))) await sw.click();
  }
  await p.click('[data-act="su-finish"]'); await step(p, 2500);
  assert.equal(await p.locator('#fab .fab-mic').count(), 0, 'a mic without AI');
  const empty = await api(p, '/api/home');
  assert.equal(empty.tasks_today.length, 0, 'sample tasks in a new copy');

  // 2. A day: a task through quick add, one by hand, habits, prayers, journal, money, a debt.
  await p.click('#fab [data-act="quick-add"]'); await step(p, 500);
  await p.fill('#quick-title', 'Mijozga taklif bugun 15:00');
  await p.click('[data-act="quick-save"]'); await step(p, 1500);
  await api(p, '/api/tasks', { method: 'POST', body: { title: 'Hisobot', deadline: D1, priority: 'high' } });
  await api(p, '/api/habits', { method: 'POST', body: { name: 'Kitob', category: 'target' } });
  await p.evaluate(() => loadScreen('home')); await step(p);
  await p.locator('[data-act="task-done"]').first().click(); await step(p, 1500);
  const book = (await api(p, '/api/habits')).habits.find(h => h.name === 'Kitob');
  await api(p, `/api/habits/${book.id}/toggle`, { method: 'POST' });
  for (const pr of ['bomdod', 'peshin', 'asr'])
    await api(p, '/api/prayers', { method: 'POST', body: { prayer: pr, status: 'on_time' } });
  await api(p, '/api/journal', { method: 'POST', body: { answers: { wins: 'Taklif' }, day: D1 } });
  await api(p, '/api/money', { method: 'POST', body: { kind: 'expense', amount: 45000, category: 'food' } });
  await api(p, '/api/debts', { method: 'POST', body: { person: 'Akmal', amount: '500000', direction: 'lent' } });
  const debt = (await api(p, '/api/debts')).open[0];
  await api(p, `/api/debts/${debt.id}/settle`, { method: 'POST', body: { settled: true, paid: 200000 } });
  await assert.rejects(api(p, `/api/debts/${debt.id}/settle`, { method: 'POST', body: { settled: true, paid: 400000 } }),
                       /http_422/, 'paying more than is owed was accepted');
  assert.equal((await api(p, '/api/debts')).open[0].amount, 300000);

  // The day's result by the server's rule: tasks 2/5 → 40, habits
  // (1 of 2 must-do × 50 + 1 of 1 target × 30) / 80 → 69, prayer 3 × 1 / 5 → 60.
  const home1 = await api(p, '/api/home');
  assert.deepEqual(home1.overall.components, { tasks: 40, habits: 69, team: null, prayer: 60 });
  assert.equal(home1.overall.value, 53);
  const steps = (await api(p, '/api/progress/me')).steps;
  assert.equal(steps.total, 4, 'Qadam: task, habit, prayer, journal');
  await p.screenshot({ path: path.join(OUT, 'offline-day1.png') });

  // Notes work in the copy too: typed, saved by itself, kept in the browser.
  await p.evaluate(() => goto('notes')); await step(p, 600);
  await p.click('.nt-actions [data-act="nt-new"]'); await step(p, 300);
  await p.fill('#nt-title', 'Lecture notes');
  await p.fill('#nt-body', '## Key ideas\n- [ ] Read chapter 3');
  await step(p, 2500);
  assert.match(await p.getAttribute('#nt-save', 'class'), /s-saved/, 'the note did not save');
  await p.evaluate(() => A['nt-back']()); await step(p, 600);
  assert.equal(await p.locator('.nt-grid .nt-card').count(), 1);
  const nlm = await api(p, '/api/notes/connector');
  assert.equal(nlm.configured, false, 'the copy claims a Google connection');
  await p.screenshot({ path: path.join(OUT, 'offline-notes.png') });
  await p.close();

  // 3. A reload keeps everything.
  p = await open(`${D1}T21:00:00`);
  assert.equal((await api(p, '/api/money')).entries.length, 1);
  assert.equal((await api(p, '/api/home')).overall.value, 53);
  assert.deepEqual((await api(p, '/api/notes?view=all')).notes.map(n => n.title), ['Lecture notes']);
  await p.close();

  // 4. The next day starts clean; yesterday stays on record.
  p = await open(`${D2}T09:00:00`);
  assert.ok((await api(p, '/api/habits')).habits.every(h => !h.done), 'habits carried into a new day');
  assert.equal((await api(p, '/api/prayers')).performed, 0);
  assert.equal((await api(p, '/api/home')).overall.yesterday, 53);
  const week = await api(p, '/api/stats?period=week');
  assert.deepEqual(week.series.map(s => s.day), [D1, D2]);
  assert.deepEqual((await api(p, '/api/tasks')).overdue.map(x => x.title), ['Hisobot']);
  await p.evaluate(() => goto('stats')); await step(p);
  await p.screenshot({ path: path.join(OUT, 'offline-day2-stats.png') });

  // 5. Backup as text (a viewer may block downloads), then restore into an emptied copy.
  await p.evaluate(() => A.backup()); await step(p, 800);
  const backup = await p.inputValue('#eb-out');
  assert.equal(JSON.parse(backup).kind, 'offline-copy');
  await p.click('#eb-close');
  await p.evaluate(() => localStorage.clear());
  await p.close();
  p = await open(`${D2}T10:00:00`);
  assert.equal(await p.locator('#su-name').count(), 1, 'cleared copy did not start over');
  await p.evaluate(() => ErnestOffline.showBackup('x.json', '{}'));
  await p.fill('#eb-in', '{"not":"a backup"}'); await p.click('#eb-go');
  assert.match(await p.textContent('#eb-msg'), /emas/);
  await p.fill('#eb-in', backup);
  await p.click('#eb-go');                       // arms
  await Promise.all([p.waitForEvent('load'), p.click('#eb-go')]);
  await p.clock.runFor(2500);
  assert.equal((await api(p, '/api/home')).overall.yesterday, 53, 'restore lost the record');
  assert.equal((await api(p, '/api/money')).entries.length, 1);
  assert.equal((await api(p, '/api/notes?view=all')).notes.length, 1, 'restore lost the notes');

  assert.deepEqual(errors, [], 'page errors');
  await browser.close();
  server.close();
  console.log('Offline copy checks passed');
})().catch(e => { console.error(e); console.error('page errors:', errors); process.exit(1); });
