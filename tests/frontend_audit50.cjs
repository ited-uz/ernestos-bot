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
  Response, TextEncoder, Blob, setTimeout, clearTimeout, setInterval(){}, clearInterval(){},
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

  // #30 / K03 — a draft typed over an older saved version is neither put
  // over the saved text nor thrown away: both are shown and the person picks.
  run(`state.journalDay=todayISO(); state.journal={answers:{win:'from laptop'}, updated_at:'2026-10-05T12:00:00+00:00'}`);
  storage.set(run('draftKey()'), JSON.stringify({win:'stale phone text', _base:'2026-10-05T09:00:00+00:00'}));
  let screen = run('SCREENS.habits()');
  assert.match(screen, /id="jq-win"[^>]*>from laptop</, 'the field shows the saved text');
  assert.ok(screen.includes('data-act="jconf"') && screen.includes('stale phone text'), 'the phone text is kept for review');
  assert.deepEqual(JSON.parse(run('JSON.stringify(journalConflicts().win)')), {mine:'stale phone text', theirs:'from laptop'});
  // Keeping both sends them with the version they were resolved against.
  calls.length = 0;
  reply = (url, m) => url === '/api/journal' && m === 'POST'
    ? {ok:true, updated_at:'2026-10-05T12:05:00+00:00', answered:1, complete:false} : {};
  run(`A.jconf({dataset:{key:'win', pick:'both'}})`);
  await run('journalChain');
  const jsent = posts("/api/journal")[0].body;
  assert.equal(jsent.answers.win, 'from laptop\n\nstale phone text');
  assert.equal(jsent.base, '2026-10-05T12:00:00+00:00');
  assert.equal(run('JSON.stringify(journalConflicts())'), '{}');
  // The server refusing (changed again meanwhile) keeps both versions.
  calls.length = 0;
  reply = (url, m) => url === '/api/journal' && m === 'POST'
    ? new Response(JSON.stringify({detail:'journal_conflict', server:{lesson:'bot text'}, updated_at:'2026-10-05T13:00:00+00:00'}), {status:409}) : {};
  run(`const l=document.getElementById('jq-lesson'); l.dataset.journal='lesson'; l.value='phone lesson'; onJournalInput(l);`);
  await run('flushJournal()');
  assert.deepEqual(JSON.parse(run('JSON.stringify(journalConflicts().lesson)')), {mine:'phone lesson', theirs:'bot text'});
  assert.equal(run('state.journal.updated_at'), '2026-10-05T13:00:00+00:00');
  storage.delete(run('draftKey()'));
  run(`state.journal={answers:{win:'from laptop'}, updated_at:'2026-10-05T12:00:00+00:00'}`);
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

  // #23 — a shared task gets the same date and time the parser read.
  calls.length = 0;
  reply = (url, m, body) => url === '/api/quick/parse'
    ? {ok:true, title:'hisobot', deadline:'2026-10-06', due_time:'15:00'} : {id:1};
  run(`state.dest='team:5'`);
  await run(`quickTask('ertaga 15:00 hisobot', destOf())`);
  await wait(10);
  const team = calls.find(c => c.url === '/api/teams/5/tasks');
  assert.ok(team, 'team task not posted');
  assert.equal(team.body.deadline, '2026-10-06');
  assert.equal(team.body.due_time, '15:00');
  assert.equal(team.body.title, 'hisobot');
  assert.ok(calls.find(c => c.url === '/api/quick/parse'), 'parsed by the read-only endpoint');
  assert.ok(!calls.find(c => c.url === '/api/quick'), 'nothing written to the personal list');

  // #49 — a passed bare time asks, and nothing is posted to the team.
  calls.length = 0;
  reply = url => url === '/api/quick/parse'
    ? {ok:false, ask:'past_time', title:'hisobot', deadline:'2026-10-05', due_time:'10:00',
       options:['2026-10-05','2026-10-06']} : {id:1};
  await run(`quickTask('10:00 hisobot', destOf())`);
  await wait(10);
  assert.equal(calls.filter(c => c.url === '/api/teams/5/tasks').length, 0);
  assert.ok(document.getElementById('sheet-body').innerHTML.includes('2026-10-06'));
  run(`state.dest='personal'`);

  // #28 — a failed voice send keeps the recording; resend sends the same
  // audio with the same request key.
  const sent = [];
  ctx.fetch = async (url, opts={}) => {
    sent.push({url, body: opts.body, key: opts.headers?.['X-Agent-Request-Key']});
    if(sent.length === 1) throw new Error('offline');
    return new Response(JSON.stringify({id: 9, status: 'ready', preview: 'ok', revision: 1}), {status: 200});
  };
  run(`voice.chunks=['abc']; voice.mime='audio/webm'; voice.target='agent';`);
  await run('voiceSend()');
  assert.ok(document.getElementById('sheet-body').innerHTML.includes('voice-resend'));
  await run(`A["voice-resend"]()`);
  assert.equal(sent.length, 2);
  assert.equal(sent[0].body, sent[1].body, 'the same recording is sent again');
  assert.equal(sent[0].key, sent[1].key, 'the same request key, so it is not done twice');
  assert.equal(run('voice.kept'), null, 'dropped once the server answered');

  // #29 — only the changed field is sent, on the draft's revision.
  const edits = [];
  ctx.fetch = async (url, opts={}) => {
    const body = opts.body ? JSON.parse(opts.body) : null;
    edits.push({url, body});
    return new Response(JSON.stringify({id:'d1', status:'ready', revision: body.revision + 1, preview:'ok',
      editable:[{index:0, entity:'task', fields:{title:'Hisobot', due_time:'15:00'}}]}), {status:200});
  };
  run(`voice.draft={id:'d1', status:'ready', revision:3, preview:'x',
       editable:[{index:0, entity:'task', fields:{title:'Hisobot', due_time:'10:00'}}]};
       openSheet(voiceCardSheet(voice.draft), 'voice');`);
  assert.ok(document.getElementById('sheet-body').innerHTML.includes('ve-0-due_time'));
  run(`for(const [f, v] of [['title','Hisobot'], ['due_time','15:00']]){
         const el = document.getElementById('ve-0-' + f); el.value = v; el.dataset.orig = f === 'title' ? 'Hisobot' : '10:00'; }`);
  await run(`A["voice-edit-save"]()`);
  assert.equal(edits.length, 1);
  assert.deepEqual(edits[0].body, {revision:3, index:0, field:'due_time', value:'15:00'});

  // #18 — an offline habit tick stays on screen, waits in the queue and is
  // sent once with its own idempotency key when the network is back.
  storage.clear();
  const wire = [];
  let online = false;
  ctx.fetch = async (url, opts={}) => {
    if(!online) throw new TypeError('Failed to fetch');
    wire.push({url, key: opts.headers?.['X-Idempotency-Key'], method: opts.method});
    return new Response('{"ok":true}', {status: 200});
  };
  run(`state.screen='habits'; state.habits={habits:[{id:5, name:'Kitob', done:false}], grouped:{}};`);
  run(`A["habit-toggle"]({dataset:{id:'5'}})`);
  await wait(20);
  assert.equal(run('state.habits.habits[0].done'), true, 'the tick stays on screen');
  assert.equal(run('loadQueue().length'), 1);
  assert.ok(run('queueBanner()').includes('queue-flush'));
  online = true;
  await run('flushQueue()');
  const toggles = wire.filter(c => c.url === '/api/habits/5/toggle');
  assert.equal(toggles.length, 1, 'sent exactly once');
  assert.ok(toggles[0].key, 'with an idempotency key');
  // A timed-out first attempt and its replay carry the same key, so a request
  // that did reach the server is not applied twice.
  const firstKeys = [];
  ctx.fetch = async (url, opts={}) => {
    firstKeys.push(opts.headers?.['X-Idempotency-Key']);
    const e = new Error('timeout'); e.name = 'TimeoutError'; throw e;
  };
  run(`A["habit-toggle"]({dataset:{id:'5'}})`);
  await wait(20);
  assert.equal(run('loadQueue()[0].key'), firstKeys[0]);
  storage.delete(run('queueKey()'));
  ctx.fetch = async (url, opts={}) => {
    if(!online) throw new TypeError('Failed to fetch');
    wire.push({url, key: opts.headers?.['X-Idempotency-Key'], method: opts.method});
    return new Response('{"ok":true}', {status: 200});
  };
  assert.equal(run('loadQueue().length'), 0);
  // K04: a refusal is never dropped by the app, nor retried forever: it is
  // kept, marked with why, skipped by later flushes, and gone only when the
  // person discards it.
  online = false;
  run(`A["habit-toggle"]({dataset:{id:'5'}})`);
  await wait(20);
  online = true;
  let refusals = 0;
  ctx.fetch = async url => { if(String(url).includes('/toggle')) refusals++; return new Response('{"detail":"not_found"}', {status: 404}); };
  await run('flushQueue()');
  assert.equal(run('loadQueue().length'), 1);
  assert.equal(run('loadQueue()[0].blocked.reason'), 'gone');
  await run('flushQueue()');
  assert.equal(refusals, 1, 'a blocked item is not sent again by itself');
  // K05: the queued tick says which state was wanted and on which day.
  const queued = run('loadQueue()[0]');
  assert.equal(typeof queued.body.done, 'boolean');
  assert.match(queued.body.day, /^\d{4}-\d{2}-\d{2}$/);
  run(`A["queue-drop"]({dataset:{key: loadQueue()[0].key}})`);
  assert.equal(run('loadQueue().length'), 0);
  // 401: stop, keep everything, wait for sign-in.
  ctx.fetch = async () => { throw new TypeError('Failed to fetch'); };
  run(`A["habit-toggle"]({dataset:{id:'5'}})`);
  await wait(20);
  online = true;
  ctx.fetch = async () => new Response('{"detail":"unauthorized"}', {status: 401});
  await run('flushQueue()');
  assert.equal(run('loadQueue().length'), 1);
  assert.equal(run('loadQueue()[0].blocked'), undefined);
  storage.delete(run('queueKey()'));

  // K02 — a wipe the server refuses (or never answers) loses nothing local,
  // and nothing is sent while it is out.
  storage.set(run('queueKey()'), JSON.stringify([{key:'k1', url:'/api/habits/5/toggle', method:'POST', body:{done:true}}]));
  storage.set(run('draftKey()'), JSON.stringify({win:'typed offline'}));
  let sentDuring = 0;
  ctx.fetch = async (url) => {
    if(url.includes('/toggle') || url.includes('/api/journal')) sentDuring++;
    if(url === '/api/account/wipe') return new Response('{"detail":"server_error"}', {status: 500});
    return new Response('{}', {status: 200});
  };
  await run(`A["wipe-data-confirm"]()`);
  assert.equal(sentDuring, 0, 'nothing may be sent while a wipe is out');
  assert.equal(run('loadQueue().length'), 1, 'a refused wipe keeps the queue');
  assert.ok(storage.get(run('draftKey()')), 'a refused wipe keeps the draft');
  assert.equal(run('state.erasing'), false);
  // Accepted: now the local copies go.
  ctx.fetch = async () => new Response('{"ok":true}', {status: 200});
  await run(`A["wipe-data-confirm"]()`);
  assert.equal(run('loadQueue().length'), 0);
  assert.equal(storage.get(run('draftKey()')), undefined);
  // Delete refused for a team owner: everything stays, the reason is named.
  storage.set(run('queueKey()'), JSON.stringify([{key:'k2', url:'/api/habits/5/toggle', method:'POST', body:{done:true}}]));
  ctx.fetch = async () => new Response('{"detail":"owner_must_transfer"}', {status: 409});
  document.getElementById('del-confirm').value = 'DELETE';
  await run(`A["delete-account-go"]()`);
  assert.equal(run('loadQueue().length'), 1, 'a refused delete keeps the queue');
  storage.delete(run('queueKey()'));

  // K08 — no signal at start: the last Home that loaded, marked, not an error.
  run(`state.me={telegram_id:7, language:'uz', avatar_token:'secret', prefs:{timezone:'Asia/Tashkent'}};
       state.home={name:'Ernest', date_label:'x'}; state.offlineSince=null; saveLastGood();`);
  assert.ok(!storage.get('ernestos-lastgood').includes('secret'), 'no token in the cache');
  run(`state.me=null; state.home=null;`);
  ctx.fetch = async () => { throw new TypeError('Failed to fetch'); };
  await run('boot()');
  assert.equal(run('state.error'), null);
  assert.equal(run('state.home.name'), 'Ernest');
  assert.ok(run('state.offlineSince') > 0);
  assert.ok(run('offlineBanner()').includes('offline-retry'));
  // A server error (not offline) is still an error, never stale data.
  ctx.fetch = async () => new Response('{"detail":"server_error"}', {status: 500});
  run(`state.offlineSince=null; state.home=null;`);
  await run('boot()');
  assert.ok(run('state.error'));
  run('forgetLastGood()');

  // K16 — the last project visited does not follow the person to other screens.
  run(`state.project={project:{id:42, name:'P'}, tasks:[]}; state.screen='tasks'; A["task-add"]({dataset:{}})`);
  assert.equal(run('state.form.project_id'), null);
  run(`state.screen='project'; A["task-add"]({dataset:{}})`);
  assert.equal(run('state.form.project_id'), 42);
  run(`closeSheet(); state.screen='home'; state.project=null;`);

  // K17 — a chip on the habit sheet keeps every field typed so far.
  run(`A["habit-add"]({dataset:{name:'Suv'}});
       document.getElementById('habit-name').value='Suv ichish';
       document.getElementById('hq-target').value='8';
       document.getElementById('hq-unit').value='stakan';
       A["habit-form"]({dataset:{field:'category', value:'bonus'}});`);
  const sheetHtml = run(`document.getElementById('sheet-body').innerHTML`);
  assert.ok(sheetHtml.includes('value="8"') && sheetHtml.includes('value="stakan"'), 'amount survives a chip tap');
  assert.ok(sheetHtml.includes('value="Suv ichish"'));
  run('closeSheet()');

  // K21 — tapping an entry edits that entry (PATCH), it does not add another.
  calls.length = 0;
  ctx.fetch = async (url, opts={}) => { calls.push({url, method:(opts.method||'GET').toUpperCase(),
    body: opts.body ? JSON.parse(opts.body) : null}); return new Response('{}', {status:200}); };
  run(`state.money={entries:[{id:9, kind:'expense', amount:45000, category:'food', note:'Tushlik', day:todayISO()}],
       category_ids:['food'], kinds:{food:'expense'}, icons:{}}; A["money-edit"]({dataset:{id:'9'}});
       document.getElementById('money-amount').value='50000'; A["money-save"]();`);
  await wait(30);
  const edit = calls.find(c => c.url === '/api/money/9');
  assert.ok(edit && edit.method === 'PATCH', 'edit goes to PATCH');
  assert.equal(edit.body.amount, 50000);
  assert.ok(!calls.some(c => c.url === '/api/money' && c.method === 'POST'), 'no new entry');
  run('closeSheet(); state.money=null; state.moneyForm=null;');

  // K01 — leaving the voice sheet by any road stops the recorder and the mic.
  run(`globalThis.__rec = {state:'recording', stopped:0, stop(){ this.stopped++; this.state='inactive'; }};
       globalThis.__track = {stopped:0, stop(){ this.stopped++; }};
       voice.rec = __rec; voice.stream = {getTracks:() => [__track]}; voice.session = 3;
       openSheet('<div></div>', 'voice'); closeSheet();`);
  assert.equal(run('__rec.stopped'), 1, 'closing the sheet stops the recorder');
  assert.equal(run('__track.stopped'), 1, 'and releases the microphone');
  assert.equal(run('voice.session'), 4, 'a late answer lands nowhere');
  assert.equal(run('voice.cancelled'), true);

  // The plan table never goes blank: a reply without the limits uses the
  // table built into the app, so Free still reads 3 habits, not unlimited.
  run(`state.planMatrix = {limits: undefined, features: undefined}`);
  const table = run(`planTable({tier:'free', products:[]}, 'month', () => null)`);
  assert.ok(table.includes('<b>3</b>'), 'Free habits limit shown');
  assert.ok(table.includes('pt-yes') && table.includes('pt-no'), 'features shown as yes/no');
  run(`state.planMatrix = null`);

  console.log('Audit-50 frontend checks passed');
})().catch(e => { console.error(e); process.exitCode = 1; });
