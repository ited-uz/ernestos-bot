/* Runs the actual shipped UI functions without a browser or network. */
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
  return {dataset:{}, style:{setProperty(){}}, innerHTML:'', offsetHeight:60,
    classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)},
    addEventListener(){},setAttribute(){},removeAttribute(){},appendChild(){},focus(){}};
}
const document = {documentElement:node(), body:node(), hidden:false,
  getElementById(id){if(!elements.has(id)) elements.set(id,node()); return elements.get(id);},
  querySelectorAll(){return [];}, querySelector(){return null;},
  addEventListener(){}, contains(){return true;}, createElement:node};
const storage = new Map();
const ctx = {console, document, URL, URLSearchParams, Date, Intl, AbortSignal, AbortController,
  Response, TextEncoder, setTimeout,clearTimeout,setInterval(){},clearInterval(){},
  navigator:{}, location:{search:'',href:'http://localhost/'},
  localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  innerHeight:800, addEventListener(){},matchMedia:()=>({matches:false,addEventListener(){}}),
  fetch:async()=>{throw new Error('Network forbidden in frontend unit tests');}};
ctx.window=ctx;
vm.createContext(ctx);
vm.runInContext(source,ctx,{filename:'index.html'});
const run = s => vm.runInContext(s,ctx);
for(const [day,n,want] of [['2026-01-31',1,'2026-02-01'],['2024-02-28',1,'2024-02-29'],['2026-01-01',-1,'2025-12-31']])
  assert.equal(run(`shiftISO('${day}',${n})`),want);
assert.equal(run(`(state.me={prefs:{timezone:'Asia/Tashkent'}}, todayISO().length)`),10);
assert.equal(run('NAV.length'),5);
for(const lang of ['uz','en','ru']) {
  run(`state.lang='${lang}';state.me={telegram_id:1,trial:{required:true,free_actions:20,remaining:3},agent:{available:true}};`);
  const more=run('SCREENS.more()');
  for(const act of ['data-screen="team"','data-screen="money"','data-act="settings"','data-act="agent-open"']) assert.ok(more.includes(act));
  for(const key of run('Object.keys(RELEASE_WORDS.uz)')) assert.notEqual(run(`t('${key}')`),key);
  for(const [,key] of source.matchAll(/\bt\("([a-z_0-9]+)"\s*[,)]/g))
    assert.ok(run(`Object.hasOwn(DICT['${lang}'],'${key}')`), `Missing ${lang} translation: ${key}`);
  assert.ok(run('trialBanner()').includes('20'));
  assert.ok(run('trialBanner()').includes('3'));
  assert.ok(!more.includes('undefined'));
  run('state.tasks={overdue:[],upcoming:[],undated:[],later:[],total:120,next_cursor:"abc"};');
  assert.ok(run('taskPageFooter()').includes('120'));
}
run('state.taskTab="done";state.doneQuery="old & important";');
const page = new URL(run('taskPageURL("abc")'),'http://localhost');
assert.equal(page.searchParams.get('q'),'old & important');
assert.equal(page.searchParams.get('done'),'true');
run('openSheet("test", "sub");closeSheet();');
assert.equal(run('state.sheetView'),null);
assert.equal(run('sheetOpen()'),false);
assert.ok(run('errorMessage({status:401})').length>15);
assert.ok(run('errorMessage({status:429,retryAfter:42})').includes('42'));
// A recognized money entry is only parsed, never sent to the write endpoint.
ctx.fetch = async (url,opts)=>{
  // The category list is read (GET) when Money was never opened; nothing is written.
  if(url==='/api/money'){ assert.ok(!opts?.method || opts.method==='GET'); return new Response(JSON.stringify({category_ids:['food','salary'],kinds:{food:'expense',salary:'income'},icons:{food:'🍔'}})); }
  assert.equal(url,'/api/money/preview');return new Response(JSON.stringify({kind:'expense',amount:45000,category:'food',note:'Lunch'}));};
(async()=>{
  await run('moneyText("Lunch 45000", "voice")');
  assert.equal(run('state.moneyForm.amount'),45000);
  assert.equal(run('state.moneyForm.review'),true);
  assert.ok(elements.get('sheet-body').innerHTML.includes('money-save'));
  assert.ok(elements.get('sheet-body').innerHTML.includes('mcat') || elements.get('sheet-body').innerHTML.includes('🍔'), 'category chips missing');
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../webapp/preview.js'),'utf8'),ctx);
  await run('boot()');
  assert.equal(run('state.error'),null);
  for(const screen of ['home','habits','tasks','team','stats','money','more']) {
    await run(`state.screen='${screen}';loadScreen('${screen}')`);
    assert.equal(run('state.screenError'),null,screen);
    const rendered = run(`SCREENS['${screen}']()`);
    assert.ok(!rendered.includes('undefined') && !rendered.includes('NaN'),screen);
  }
  for(const tab of ['main','open','projects','done','calendar']) {
    await run(`state.screen='tasks';state.taskTab='${tab}';loadScreen('tasks')`);
    assert.equal(run('state.screenError'),null,tab);
    assert.ok(!run('SCREENS.tasks()').includes('undefined'),tab);
  }
  for(const tab of ['habits','prayer','journal']) {
    run(`state.screen='habits';state.tab='${tab}';`);
    assert.ok(!run('SCREENS.habits()').includes('undefined'),tab);
  }
  console.log(`Frontend release checks passed (TZ=${process.env.TZ || 'default'})`);
})().catch(e=>{console.error(e);process.exitCode=1;});
