/* Notes in the real Mini App against a running server (headless Chromium).
   Run with scripts/e2e_notes.sh. Every check goes through the UI the way a
   person would and reads the result back from the server. Screenshots go to
   tests/e2e/out/notes-*.png. */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');

const BASE = process.env.E2E_BASE || 'http://localhost:8766';
const DIR = process.env.E2E_OUT || path.join(__dirname, 'out');
const initData = fs.readFileSync(path.join(DIR, 'initdata.txt'), 'utf8');
const stub = scheme => `
  window.Telegram = { WebApp: new Proxy({
    initData: ${JSON.stringify(initData)},
    initDataUnsafe: { user: { id: 777001, first_name: 'Ernest' } },
    colorScheme: '${scheme}', themeParams: {}, viewportHeight: 800, viewportStableHeight: 800,
    safeAreaInset: {top:0,bottom:0,left:0,right:0}, contentSafeAreaInset: {top:0,bottom:0,left:0,right:0},
    HapticFeedback: { impactOccurred(){}, notificationOccurred(){}, selectionChanged(){} },
    BackButton: { show(){}, hide(){}, onClick(){}, offClick(){} },
    openLink(url){ window.__opened = url; },
  }, { get: (t, k) => k in t ? t[k] : () => {} }) };`;

const results = [];
let current = null;
const step = async (name, fn) => {
  try { await fn(); results.push(['ok', name]); console.log('✓', name); }
  catch (e) {
    results.push(['FAIL', name, e.message]); console.log('✗', name, '—', e.message.split('\n')[0]);
    if (current) await current.screenshot({ path: path.join(DIR, `notes-FAIL-${results.length}.png`) }).catch(() => {});
  }
};

async function open(browser, {width = 360, height = 780, scheme = 'light'} = {}) {
  const context = await browser.newContext({ viewport: { width, height }, colorScheme: scheme, deviceScaleFactor: 2 });
  await context.route('https://telegram.org/**', r => r.fulfill({ contentType: 'text/javascript', body: stub(scheme) }));
  const page = await context.newPage();
  page.errors = [];
  page.on('pageerror', e => page.errors.push('pageerror: ' + e.message));
  page.on('dialog', d => { page.errors.push('dialog: ' + d.message()); d.dismiss().catch(() => {}); });
  await page.goto(BASE + '/');
  await page.waitForFunction(() => typeof state !== 'undefined' && state.me && !state.loading, null, { timeout: 15000 });
  await page.evaluate(() => { state.homeVeil = false; render(); });
  return { context, page };
}
const api = (page, p, opts = {}) => page.evaluate(([p, opts]) => api(p, opts), [p, opts]);
const badge = page => page.evaluate(() => document.getElementById('nt-save')?.className || '');
const waitBadge = (page, cls, ms = 8000) =>
  page.waitForFunction(c => (document.getElementById('nt-save')?.className || '').includes(c), cls, { timeout: ms });
const shot = (page, n) => page.screenshot({ path: path.join(DIR, `notes-${n}.png`), fullPage: false });
const noOverflow = page => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);

(async () => {
  const browser = await chromium.launch();
  const { context, page } = await open(browser);
  current = page;
  let noteId;

  await step('Notes is a place of its own in the bottom bar', async () => {
    const labels = await page.$$eval('nav button', bs => bs.map(b => b.dataset.screen));
    assert.deepEqual(labels, ['home', 'tracker', 'notes', 'money', 'profile']);
    await page.click('nav button[data-screen="notes"]');
    await page.waitForSelector('.nt-search');
    const html = await page.content();
    for (const want of ['Yangi qayd', 'Shablon', 'Kundalik', 'Jami', 'Seriya', 'Daftarlar'])
      assert.ok(html.includes(want), want);
    assert.ok(await noOverflow(page), 'no sideways scroll at 360px');
    await shot(page, '360-home-empty');
  });

  await step('A new note saves itself and says so truthfully', async () => {
    await page.click('.nt-actions [data-act="nt-new"]');
    await page.waitForSelector('#nt-title');
    assert.equal(await badge(page), 'nt-save s-idle', 'nothing claims "saved" before anything was typed');
    await page.fill('#nt-title', 'Marketing lecture 1');
    await page.click('#nt-body');
    await page.keyboard.type('## Key ideas\n- 4P model\n- [ ] Read chapter 3\n\nSee https://example.com/a_b_c');
    await page.waitForFunction(() => /s-(pending|saving)/.test(document.getElementById('nt-save')?.className || ''));
    await waitBadge(page, 's-saved');
    noteId = await page.evaluate(() => state.note.id);
    // The first save redraws the screen (the note now has its panels); the
    // caret stays in the text and typing goes on where it was.
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'nt-body');
    await page.keyboard.type(' (more)');
    assert.ok((await page.inputValue('#nt-body')).endsWith('a_b_c (more)'));
    await waitBadge(page, 's-saved');
    const n = await api(page, `/api/notes/${noteId}`);
    assert.equal(n.title, 'Marketing lecture 1');
    assert.ok(n.body.includes('- [ ] Read chapter 3'));
    const drafts = await page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith('ernestos-note-draft-')));
    assert.deepEqual(drafts, [], 'a saved note leaves no draft behind');
    await shot(page, '360-editor');
  });

  await step('Toolbar formats the selection; preview renders safely', async () => {
    await page.click('#nt-body');
    await page.keyboard.press('End');
    await page.keyboard.type('\nimportant');
    await page.evaluate(() => { const ta = document.getElementById('nt-body'); const i = ta.value.lastIndexOf('important');
      ta.setSelectionRange(i, i + 9); NT.sel = [i, i + 9]; });
    await page.click('[data-act="nt-fmt"][data-fmt="bold"]');
    assert.ok((await page.inputValue('#nt-body')).includes('**important**'));
    await page.click('[data-act="nt-mode"][data-value="preview"]');
    await page.waitForSelector('.nt-preview');
    assert.ok(await page.$('.nt-preview h3.md-h'), 'heading');
    assert.ok(await page.$('.nt-preview strong'), 'bold');
    const href = await page.getAttribute('.nt-preview a[href^="https://example.com"]', 'href');
    assert.equal(href, 'https://example.com/a_b_c', 'underscores in a URL are not italic');
    await page.click('.nt-preview .md-check');
    await page.waitForFunction(() => state.noteEdit.body.includes('- [x] Read chapter 3'));
    await waitBadge(page, 's-saved');
    await shot(page, '360-preview');
    await page.click('[data-act="nt-mode"][data-value="edit"]');
  });

  await step('HTML and javascript: links in a note never run', async () => {
    await page.evaluate(() => { const ta = document.getElementById('nt-body');
      ta.value += '\n<img src=x onerror="window.__xss=1"><script>window.__xss=2</script>\n[click](javascript:window.__xss=3) "quoted" it\'s';
      ta.dispatchEvent(new Event('input', {bubbles: true})); });
    await page.click('[data-act="nt-mode"][data-value="preview"]');
    await page.waitForTimeout(300);
    const bad = await page.evaluate(() => ({
      xss: window.__xss, img: document.querySelectorAll('.nt-preview img[onerror], .nt-preview script').length,
      js: document.querySelectorAll('.nt-preview a[href^="javascript"]').length,
      text: document.querySelector('.nt-preview').textContent.includes('<img src=x')}));
    assert.deepEqual(bad, { xss: undefined, img: 0, js: 0, text: true });
    await page.click('.nt-preview a[href^="https://example.com"]', { modifiers: [] }).catch(() => {});
    assert.equal(await page.evaluate(() => window.__xss), undefined);
    await page.click('[data-act="nt-mode"][data-value="edit"]');
    await waitBadge(page, 's-saved');
  });

  await step('A save on an old revision is a conflict, and both versions survive', async () => {
    const n = await api(page, `/api/notes/${noteId}`);
    // Another phone saves first.
    await api(page, `/api/notes/${noteId}`, { method: 'PATCH', body: { base_revision: n.revision, body: 'Written on the laptop' } });
    await page.click('#nt-body');
    await page.keyboard.press('End');
    await page.keyboard.type(' typed on the phone');
    await waitBadge(page, 's-conflict');
    assert.ok(await page.$('.nt-conflict'), 'the conflict banner is shown');
    const kept = await page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith('ernestos-note-draft-')).length);
    assert.equal(kept, 1, 'the phone text is still on the phone');
    await shot(page, '360-conflict');
    await page.click('[data-act="nt-keep"][data-value="both"]');
    await page.waitForFunction(() => !state.noteConflict);
    const all = await api(page, '/api/notes?view=all');
    const titles = all.notes.map(x => x.title);
    assert.ok(titles.includes('Marketing lecture 1'));
    assert.ok(titles.some(x => /Marketing lecture 1 \(nusxa\)/.test(x)), titles.join(' | '));
    const copy = all.notes.find(x => /nusxa/.test(x.title));
    const full = await api(page, `/api/notes/${copy.id}`);
    assert.ok(full.body.includes('typed on the phone'));
    assert.equal((await api(page, `/api/notes/${noteId}`)).body, 'Written on the laptop');
  });

  await step('Offline: the note stays on the phone and goes when the network is back', async () => {
    await context.setOffline(true);
    await page.click('#nt-body');
    await page.keyboard.press('End');
    await page.keyboard.type('\nwritten offline');
    await waitBadge(page, 's-offline', 10000);
    await shot(page, '360-offline');
    const draft = await page.evaluate(id => localStorage.getItem(`ernestos-note-draft-777001-${id}`), noteId);
    assert.ok(draft && draft.includes('written offline'));
    await context.setOffline(false);
    await page.evaluate(() => window.dispatchEvent(new Event('online')));
    await waitBadge(page, 's-saved', 10000);
    assert.ok((await api(page, `/api/notes/${noteId}`)).body.includes('written offline'));
  });

  await step('Written offline and left: the note reaches the server by itself when the network is back', async () => {
    await page.click('[data-act="nt-back"]');
    await page.waitForSelector('.nt-search');
    await context.setOffline(true);
    try {
      await page.click('.nt-actions [data-act="nt-new"]');
      await page.fill('#nt-title', 'Offline idea');
      await page.fill('#nt-body', 'Typed on the metro');
      await waitBadge(page, 's-offline', 10000);
      await page.click('[data-act="nt-back"]');              // left before it could be saved
      await page.waitForSelector('.nt-search');              // the list, not an error page
      assert.ok(await page.isVisible('.nt-grid .nt-card'), 'the notes last loaded are still shown');
      assert.ok(await page.isVisible('[data-act="nt-resume"]'), 'the unsent note is shown on Notes');
      await shot(page, '360-offline-list');
    } finally { await context.setOffline(false); }
    await page.evaluate(() => window.dispatchEvent(new Event('online')));
    await page.waitForFunction(() => !Object.keys(localStorage).some(k => k.includes('ernestos-note-draft-777001-new-')), null, { timeout: 10000 });
    const got = await api(page, '/api/notes?view=all&q=metro');
    assert.equal(got.notes.length, 1, 'exactly one note, not two');
    await page.waitForFunction(() => !document.querySelector('[data-act="nt-resume"]'));
    await page.evaluate(id => ntOpen(id), noteId);
    await page.waitForSelector('#nt-body');
  });

  await step('Drafts left on a closed app: sent on the next start; a clashing one is kept, not lost', async () => {
    const n = await api(page, `/api/notes/${noteId}`);
    await api(page, `/api/notes/${noteId}`, { method: 'PATCH', body: { base_revision: n.revision, body: n.body + ' (server)' } });
    await page.evaluate(([id, rev]) => {
      localStorage.setItem('ernestos-note-draft-777001-new-closedappkey0001', JSON.stringify(
        { title: 'Left in the phone', body: 'written before the app was closed', base: 0, at: Date.now(),
          id: null, createKey: 'closedappkey0001', meta: { kind: 'quick', tags: [], notebook_ids: [], status: 'inbox' } }));
      localStorage.setItem(`ernestos-note-draft-777001-${id}`, JSON.stringify(
        { title: 'Marketing lecture 1', body: 'typed on an old version', base: rev, at: Date.now(), id }));
    }, [noteId, n.revision]);
    await page.reload();
    await page.waitForFunction(() => typeof state !== 'undefined' && state.me && !state.loading, null, { timeout: 15000 });
    // Drafts go one after another, in whatever order storage lists them.
    await page.waitForFunction(id => !localStorage.getItem('ernestos-note-draft-777001-new-closedappkey0001')
      && JSON.parse(localStorage.getItem(`ernestos-note-draft-777001-${id}`) || '{}').conflict, noteId, { timeout: 10000 })
      .catch(() => {});
    const sent = await api(page, '/api/notes?view=all&q=closed');
    assert.equal(sent.notes.length, 1, 'the new note arrived once');
    assert.equal(sent.notes[0].kind, 'quick');
    const kept = await page.evaluate(id => JSON.parse(localStorage.getItem(`ernestos-note-draft-777001-${id}`) || 'null'), noteId);
    assert.ok(kept && kept.conflict && kept.body === 'typed on an old version', 'the clashing draft stays, marked');
    await page.evaluate(id => { state.homeVeil = false; ntOpen(id); }, noteId);
    await page.waitForSelector('.nt-conflict');
    assert.equal(await page.inputValue('#nt-body'), 'typed on an old version');
    await page.click('[data-act="nt-keep"][data-value="theirs"]');
    await page.waitForFunction(() => !state.noteConflict);
    assert.equal(await page.evaluate(id => localStorage.getItem(`ernestos-note-draft-777001-${id}`), noteId), null);
  });

  await step('An image attachment uploads, is inserted and shows in preview', async () => {
    const png = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(64)]);
    await page.setInputFiles('#nt-file', { name: 'board.png', mimeType: 'image/png', buffer: png });
    await page.waitForFunction(() => /attachment:[0-9a-f]{32}/.test(state.noteEdit.body));
    await waitBadge(page, 's-saved');
    await page.click('[data-act="nt-mode"][data-value="preview"]');
    await page.waitForFunction(() => document.querySelector('.nt-preview img[data-att]')?.src?.startsWith('blob:'));
    await page.click('[data-act="nt-mode"][data-value="edit"]');
  });

  await step('Note → task: preview first, then one task, linked back', async () => {
    const before = (await api(page, '/api/tasks')).total ?? null;
    await page.click('[data-act="nt-menu"]');
    await page.click('.item[data-act="nt-to-task"]');
    await page.waitForSelector('#nt-task-title');
    assert.equal(before === null ? null : (await api(page, '/api/tasks')).total, before, 'the preview wrote nothing');
    await page.fill('#nt-task-title', 'Email the professor');
    await page.click('[data-act="nt-to-task-go"]');
    await page.waitForFunction(() => (state.note.relations || []).some(r => r.rel === 'created_task'));
    await page.waitForSelector('.nt-rel');
    const tasks = JSON.stringify(await api(page, '/api/tasks'));
    assert.ok(tasks.includes('Email the professor'));
    // The task shows the note it came from, and opens it.
    const taskId = await page.evaluate(() => state.note.relations.find(r => r.rel === 'created_task').target_id);
    await page.evaluate(async id => { state.tasks = await api('/api/tasks'); openTaskSheet(id); }, taskId);
    await page.waitForSelector('#sheet .nt-linked .chip', { timeout: 8000 });
    assert.ok((await page.textContent('#sheet .nt-linked')).includes('Marketing lecture 1'));
    await page.click('#sheet .nt-linked .chip');
    await page.waitForFunction(id => state.screen === 'note' && state.note?.id === id && !document.getElementById('sheet').classList.contains('show'), noteId);
  });

  await step('Notebooks: a suggested one is created only when tapped, and filters the list', async () => {
    await page.click('[data-act="nt-back"]');
    await page.waitForSelector('.nt-search');
    await page.click('.nt-views [data-act="nt-view"][data-value="notebooks"]');
    await page.waitForSelector('.nt-gallery');
    assert.equal((await api(page, '/api/notes/notebooks')).notebooks.length, 0, 'suggestions are not created on their own');
    await page.click('[data-act="nt-book-suggest"][data-name="University"]');
    await page.waitForSelector('.nt-gallery [data-act="nt-book"]');
    const book = (await api(page, '/api/notes/notebooks')).notebooks[0];
    await api(page, `/api/notes/${noteId}`, { method: 'PATCH', body: { notebook_ids: [book.id] } });
    await page.click(`.nt-gallery [data-act="nt-book"][data-id="${book.id}"]`);
    await page.waitForSelector('.nt-bookhead');
    await page.waitForFunction(() => document.querySelector('.nt-bookhead')?.textContent.includes('1'));
    await page.waitForFunction(() => document.querySelectorAll('.nt-grid .nt-card').length === 1);
    await shot(page, '360-notebook');
  });

  await step('Search matches % literally', async () => {
    await api(page, '/api/notes', { method: 'POST', body: { title: 'Growth 100% plan' } });
    await page.click('.nt-views [data-act="nt-view"][data-value="all"]');
    await page.fill('#nt-q', '%');
    await page.press('#nt-q', 'Enter');
    await page.waitForFunction(() => state.noteList?.key?.includes('%'));
    const titles = await page.$$eval('.nt-grid .nt-card b', bs => bs.map(b => b.textContent));
    assert.deepEqual(titles, ['Growth 100% plan']);
    await page.click('[data-act="nt-search-clear"]');
  });

  await step('A template starts a ready note; Daily is one per day', async () => {
    await page.click('[data-act="nt-templates"]');
    await page.click('[data-act="nt-tpl"][data-value="idea"]');
    await page.waitForSelector('#nt-body');
    assert.ok((await page.inputValue('#nt-body')).includes('## Kim to‘laydi'));
    await page.click('#nt-body');
    await page.keyboard.type('Notes for founders');
    await waitBadge(page, 's-saved');
    assert.equal(await page.evaluate(() => state.note.kind), 'idea');
    await page.click('[data-act="nt-back"]');
    await page.click('.nt-actions [data-act="nt-daily"]');
    await page.waitForSelector('#nt-title');
    const first = await page.evaluate(() => state.note.id);
    await page.click('[data-act="nt-back"]');
    await page.click('.nt-actions [data-act="nt-daily"]');
    await page.waitForFunction(() => state.note?.id);
    assert.equal(await page.evaluate(() => state.note.id), first);
    await page.click('[data-act="nt-back"]');
  });

  await step('Quick capture keeps a note apart from tasks and money', async () => {
    await page.click('nav button[data-screen="home"]');
    await page.waitForSelector('.fab-add');
    const tasks0 = JSON.stringify(await api(page, '/api/tasks'));
    await page.click('.fab-add');
    await page.click('[data-act="quick-type"][data-value="note"]');
    await page.fill('#quick-note', 'Tushlik 45000 ertaga soat 10 da — just a thought');
    await shot(page, '360-quick-note');
    await page.click('[data-act="quick-save"]');
    await page.waitForFunction(() => !document.getElementById('sheet').classList.contains('show'));
    const inbox = await api(page, '/api/notes?view=inbox&q=just%20a%20thought');
    assert.equal(inbox.notes.length, 1);
    assert.equal(inbox.notes[0].kind, 'quick');
    assert.equal(JSON.stringify(await api(page, '/api/tasks')), tasks0, 'no task was made from it');
    const money = JSON.stringify(await api(page, '/api/money'));
    assert.ok(!money.includes('just a thought'), 'no money entry was made from it');
  });

  await step('Import an Obsidian vault: preview, nothing excluded leaks, links resolve', async () => {
    await page.click('nav button[data-screen="notes"]');
    await page.waitForSelector('.nt-search');
    await page.click('.nt-head [data-act="nt-tools"]');
    await page.click('.item[data-act="nt-import"]');
    await page.setInputFiles('#nt-import-file', path.join(DIR, 'vault.zip'));
    await page.waitForSelector('.nt-implist');
    const sheet = await page.textContent('#sheet');
    assert.ok(sheet.includes('Marketing 101') && sheet.includes('Positioning'));
    assert.ok(!sheet.includes('agent log'));
    assert.ok(sheet.includes('Dataview'), 'the DataviewJS warning is shown');
    await shot(page, '360-import');
    await page.click('[data-act="nt-import-go"]');
    await page.waitForFunction(() => !document.getElementById('sheet').classList.contains('show'));
    const found = await api(page, '/api/notes?view=all&q=Positioning');
    const pos = await api(page, `/api/notes/${found.notes.find(x => x.title === 'Positioning').id}`);
    assert.equal(pos.links[0].title, 'Marketing 101');
    const log = await api(page, '/api/notes?view=all&q=agent%20log');
    assert.equal(log.notes.length, 0, 'AI/log.md was not imported');
  });

  await step('NotebookLM says honestly that the server is not set up', async () => {
    await page.click('.nt-head [data-act="nt-nlm"]');
    await page.waitForFunction(() => document.getElementById('sheet').textContent.includes('sozlanmagan'));
    const text = await page.textContent('#sheet');
    assert.ok(text.includes('CONNECTOR_TOKEN_KEY'));
    assert.ok(text.includes('Enterprise'));
    await shot(page, '360-notebooklm');
    await page.evaluate(() => closeSheet());
  });

  await step('Exports: a five-minute link opens the ZIP without the header', async () => {
    const link = await api(page, '/api/notes/download-link', { method: 'POST', body: { what: 'zip' } });
    const head = await page.evaluate(async url => {
      const r = await fetch(url); const b = new Uint8Array(await r.arrayBuffer());
      return [r.status, String.fromCharCode(b[0], b[1])];
    }, link.url);
    assert.deepEqual(head, [200, 'PK']);
    const forged = await page.evaluate(async url => (await fetch(url.replace(/.$/, c => c === 'a' ? 'b' : 'a'))).status, link.url);
    assert.equal(forged, 404);
  });

  await step('A long title wraps instead of being cut; Enter goes on to the text', async () => {
    await page.setViewportSize({ width: 360, height: 780 });
    await page.evaluate(() => { state.homeVeil = false; A['nt-new'](); });
    await page.waitForSelector('#nt-title');
    await page.fill('#nt-title', 'Idea — an AI sales agent for small shops in Uzbekistan that answers in Telegram');
    const wrapped = await page.evaluate(() => {
      const el = document.getElementById('nt-title');
      return { tall: el.offsetHeight > 60, fits: el.scrollHeight <= el.clientHeight + 2,
               wide: document.documentElement.scrollWidth > innerWidth + 1 };
    });
    assert.ok(wrapped.tall && wrapped.fits, `title shows whole: ${JSON.stringify(wrapped)}`);
    assert.ok(!wrapped.wide, 'no sideways scroll');
    await page.focus('#nt-title');
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'nt-body');
    assert.ok(!(await page.inputValue('#nt-title')).includes('\n'), 'no line break in the title');
    await waitBadge(page, 's-saved');
    await page.evaluate(() => A['nt-back']());
  });

  await step('Nothing threw in the page', async () => {
    const errs = page.errors.filter(e => !/ERR_INTERNET_DISCONNECTED|Failed to fetch/.test(e));
    assert.deepEqual(errs, []);
  });
  await context.close();

  // Every width and both schemes: no sideways scroll, and a picture of each.
  for (const [width, height] of [[360, 780], [768, 1024], [1280, 860]]) {
    for (const scheme of ['light', 'dark']) {
      await step(`Layout at ${width}px, ${scheme}`, async () => {
        const { context: c2, page: p2 } = await open(browser, { width, height, scheme });
        await p2.evaluate(() => { state.noteView = 'all'; goto('notes'); });
        await p2.waitForSelector('.nt-grid .nt-card');
        assert.ok(await noOverflow(p2), 'home fits');
        if (width >= 1000) assert.ok(await p2.isVisible('.nt-side'), 'the side column shows on desktop');
        await shot(p2, `${width}-${scheme}-home`);
        await p2.click('.nt-grid .nt-card');
        await p2.waitForSelector('#nt-title');
        assert.ok(await noOverflow(p2), 'editor fits');
        await shot(p2, `${width}-${scheme}-editor`);
        assert.deepEqual(p2.errors, []);
        await c2.close();
      });
    }
  }

  await browser.close();
  const failed = results.filter(r => r[0] === 'FAIL');
  console.log(`\n${results.length - failed.length}/${results.length} notes checks passed`);
  if (failed.length) process.exit(1);
})();
