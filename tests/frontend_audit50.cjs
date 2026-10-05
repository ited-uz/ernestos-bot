/* Regression checks for the 50-item audit (docs/AUDIT_50_STATUS.md).
   Runs the shipped UI code in a VM with a fake fetch and localStorage. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../webapp/index.html'), 'utf8');
const source = html.slice(html.indexOf('<script>\n"use strict";') + 9, html.lastIndexOf('</script>'))
  .replace(/\nboot\(\);\s*$/, '\n');
const elements = new Map();
function node() {
  const classes = new Set();
  return {dataset:{}, style:{setProperty(){}}, innerHTML:'', value:'', offsetHeight:60,
    classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)},
    addEventListener(){},setAttribute(){},removeAttribute(){},appendChild(){},focus(){},
    scrollIntoView(){}, closest(){return null;}};
}
const document = {documentElement:node(), body:node(), hidden:false,
  getElementById(id){if(!elements.has(id)) elements.set(id,node()); return elements.get(id);},
  querySelectorAll(){return [];}, querySelector(){return null;},
  addEventListener(){}, contains(){return true;}, createElement:node};
const storage = new Map();
const localStorage = {getItem:k=>storage.has(k)?storage.get(k):null, setItem:(k,v)=>storage.set(k,String(v)),
  removeItem:k=>storage.delete(k), key:i=>[...storage.keys()][i] ?? null,
  get length(){return storage.size;}};
const calls = [];
let reply = () => ({});
const ctx = {console, document, URL, URLSearchParams, Date, Intl, AbortSignal, AbortController,
  Response, TextEncoder, setTimeout, clearTimeout, setInterval(){}, clearInterval(){},
  navigator:{}, location:{search:'',href:'http://localhost/'}, localStorage,
  innerHeight:800, addEventListener(){}, matchMedia:()=>({matches:false,addEventListener(){}}),
  fetch: async (url, opts={}) => {
    const body = opts.body ? JSON.parse(opts.body) : null;
    calls.push({url, method:(opts.method||'GET').toUpperCase(), body});
    const r = reply(url, opts.method || 'GET', body);
    if(r instanceof Response) return r;
    return new Response(JSON.stringify(r), {status:200});
  }};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(source, ctx, {filename:'index.html'});
const run = s => vm.runInContext(s, ctx);
const wait = ms => new Promise(r => setTimeout(r, ms));
const posts = url => calls.filter(c => c.method === 'POST' && c.url === url);

(async () => {
  run(`state.lang='uz'; state.me={telegram_id:7, prefs:{timezone:'Asia/Tashkent'}, agent:{available:true, consent:true}};
       state.screen='habits'; state.tab='journal';
       state.journalQuestions=[{id:'win',text:'A'},{id:'lesson',text:'B'}];
       state.journal={answers:{win:'old'}, updated_at:'2026-10-04T10:00:00+00:00'};
       state.journalDay=todayISO();`);

  // #25 — the AI's answers are a proposal: no request, no draft, until Accept.
  calls.length = 0;
  run(`journalFilled({answers:{win:'AI win', lesson:'AI lesson'}})`);
  await wait(1100);
  assert.equal(posts('/api/journal').length, 0, 'AI fill must not autosave');
  assert.equal(storage.get(run('draftKey()')), undefined, 'AI fill must not write the draft');
  assert.ok(run('SCREENS.habits()').includes('jai-accept'));
  run(`jaiReject = A["jai-reject"]; jaiReject();`);
  assert.equal(run('hasJaiPending()'), false);
  // Accept sends what the fields hold.
  run(`journalFilled({answers:{lesson:'AI lesson'}});
       document.getElementById('jq-lesson').dataset.journal='lesson';
       document.getElementById('jq-lesson').value=state.jaiPending.lesson;`);
  reply = (url, m) => url === '/api/journal' && m === 'POST'
    ? {ok:true, day:run('journalDay()'), updated_at:'2026-10-05T10:00:00+00:00', answered:2, complete:true} : {};
  run(`A["jai-accept"]()`);
  await run('flushJournal()');
  assert.equal(posts('/api/journal').length, 1);
  assert.equal(posts('/api/journal')[0].body.answers.lesson, 'AI lesson');
  // #30 — the sent field leaves the local draft once the server has it.
  assert.equal(storage.get(run('draftKey()')), undefined, 'saved fields should leave the draft');

  // #31 — the entry is pinned to its day; a save after midnight keeps it.
  calls.length = 0;
  run(`state.journalDay='2026-10-04'; const el=document.getElementById('jq-win');
       el.dataset.journal='win'; el.value='late night'; onJournalInput(el);`);
  await run('flushJournal()');
  assert.equal(posts('/api/journal')[0].body.day, '2026-10-04');
  assert.ok(storage.size === 0 || [...storage.keys()].every(k => !k.endsWith(run('todayISO()')) || run('todayISO()') === '2026-10-04'));

  // #30 — a draft typed over an older saved version is dropped, not shown.
  run(`state.journalDay=todayISO(); state.journal={answers:{win:'from laptop'}, updated_at:'2026-10-05T12:00:00+00:00'}`);
  storage.set(run('draftKey()'), JSON.stringify({win:'stale phone text', _base:'2026-10-05T09:00:00+00:00'}));
  assert.ok(run('SCREENS.habits()').includes('from laptop'));
  assert.ok(!run('SCREENS.habits()').includes('stale phone text'));
  // ...while one typed over the current version is kept.
  storage.set(run('draftKey()'), JSON.stringify({win:'unsent', _base:'2026-10-05T12:00:00+00:00'}));
  assert.ok(run('SCREENS.habits()').includes('unsent'));

  // #27 — text typed for the AI comes back after a failure.
  reply = url => url === '/api/agent/journal/text' ? new Response('{"detail":"provider_unavailable"}', {status:503}) : {};
  run(`openSheet(jaiTextSheet()); document.getElementById('jai-text').value='a long evening note';`);
  await run(`A["jai-send"]()`);
  assert.equal(storage.get(run('jaiKey()')), 'a long evening note');
  assert.ok(document.getElementById('sheet-body').innerHTML.includes('a long evening note'));
  // #26 — the AI call waits longer than the plain 15 s.
  assert.ok(run('AGENT_TIMEOUT_MS') >= 150000);
  assert.ok(source.includes('timeout: AGENT_TIMEOUT_MS'));

  // #32 — wiping data forgets this account's drafts, not another's.
  storage.set('ernestos-journal-7-2026-10-01', '{"win":"x"}');
  storage.set('ernestos-journal-99-2026-10-01', '{"win":"other"}');
  reply = () => ({ok:true});
  await run(`A["wipe-data-confirm"]()`);
  assert.equal([...storage.keys()].filter(k => k.includes('-7-') || k === 'ernestos-jai-7').length, 0);
  assert.ok(storage.has('ernestos-journal-99-2026-10-01'));

  console.log('Audit-50 frontend checks passed');
})().catch(e => { console.error(e); process.exitCode = 1; });
