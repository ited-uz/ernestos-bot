/* Offline unit harness, not browser automation. No microphone, network or account.
 * Run: node --test tests/test_agent_ui.cjs
 */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(require('node:path').join(__dirname, '../webapp/agent.js'), 'utf8');
const settle = async () => {for(let n=0;n<12;n++)await new Promise(r=>setImmediate(r));};

function harness(consented=true){
  class Element {
    constructor(tag){this.tag=tag;this.children=[];this.attrs={};this.events={};this.value='';this.style={};this.hidden=false;this.disabled=false;this.classList={add(){},remove(){}};}
    append(...nodes){for(const node of nodes){this.children.push(node);node.parent=this;}}
    replaceChildren(...nodes){this.children=[];this.append(...nodes);}
    setAttribute(k,v){this.attrs[k]=v;}
    addEventListener(k,fn){this.events[k]=fn;}
    focus(){document.activeElement=this;}
    remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);}
    all(){return this.children.flatMap(n=>[n,...n.all()]);}
    querySelectorAll(selector){return this.all().filter(n=>selector==='button'?n.tag==='button':false);}
    querySelector(selector){return this.all().find(n=>selector==='[role="alert"]'&&n.attrs.role==='alert')||null;}
    click(){if(!this.disabled)return this.onclick?.();}
  }
  const document={body:new Element('body'),hidden:false,events:{},createElement:t=>new Element(t),addEventListener(k,fn){this.events[k]=fn;}};
  const calls=[],recorders=[],tracks=[];
  let micCalls=0,consent=consented;
  const strings={confirm:'CONFIRM',keep:'KEEP',new:'NEW',inbox:'INBOX',close:'CLOSE',consent:'CONSENT',ready:'READY',processing:'PROCESSING',input_language:'LANGUAGE',mixed:'MIXED'};
  const draft={id:'test-draft',revision:1,status:'ready',transcript:'ertaga meeting',language:'mixed',preview:'Task: meeting\nProject: Alohida\nTime: 10:00'};
  class Recorder {
    static isTypeSupported(){return true;}
    constructor(){this.state='inactive';this.mimeType='audio/webm';recorders.push(this);}
    start(){this.state='recording';}
    stop(){this.state='inactive';queueMicrotask(()=>{this.ondataavailable?.({data:new Blob(['sample'],{type:this.mimeType})});this.onstop?.();});}
  }
  const context={document,window:{Telegram:{WebApp:{initData:'test'}},MediaRecorder:Recorder,addEventListener(){}},MediaRecorder:Recorder,
    state:{me:{language:'uz'}},crypto:{randomUUID:()=>`key${calls.length}`},Blob,AbortSignal,
    URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},setTimeout:()=>1,clearTimeout(){},
    navigator:{mediaDevices:{async getUserMedia(){micCalls++;const track={stopped:false,stop(){this.stopped=true;}};tracks.push(track);return {getTracks:()=>[track]};}}},
    async fetch(path,options){calls.push({path,options});let data;
      if(path.endsWith('/meta'))data={consent,enabled:true,provider:'MOCK',audio_bytes:10485760,audio_seconds:120,strings};
      else if(path.endsWith('/consent')){consent=true;data={consent};}
      else if(path.includes('/inbox'))data={drafts:calls.some(c=>c.path.includes('/audio'))?[draft]:[]};
      else if(path.endsWith('/confirm'))data={...draft,status:'executed'};
      else data=draft;
      return {ok:true,json:async()=>data};
    }
  };
  vm.runInNewContext(source,context);
  const find=(predicate)=>document.body.all().find(predicate);
  const button=(label)=>find(n=>n.tag==='button'&&n.textContent===label);
  return {document,calls,recorders,tracks,context,button,find,mics:()=>micCalls};
}

test('right microphone starts immediately; stop previews; only Confirm executes',async()=>{
  const h=harness();await settle();
  await h.find(n=>n.id==='ernest-launch').click();await settle();
  assert.equal(h.mics(),1);assert.equal(h.recorders[0].state,'recording');
  await h.button('⏹ Yozishni tugatish').click();await settle();
  assert.equal(h.calls.filter(c=>c.path==='/api/agent/audio').length,1);
  assert.ok(h.find(n=>n.textContent==='ertaga meeting'));
  assert.ok(h.find(n=>n.textContent?.includes('Project: Alohida')));
  assert.ok(h.find(n=>n.textContent==='LANGUAGE: MIXED'));
  assert.equal(h.calls.filter(c=>c.path.endsWith('/confirm')).length,0);
  assert.ok(h.tracks.every(t=>t.stopped));
  await h.button('CONFIRM').click();await settle();
  assert.equal(h.calls.filter(c=>c.path.endsWith('/confirm')).length,1);
});

test('first use waits for consent before recording and submitting',async()=>{
  const h=harness(false);await settle();
  await h.find(n=>n.id==='ernest-launch').click();await settle();
  assert.equal(h.mics(),0);
  await h.button('CONSENT').click();await settle();
  assert.equal(h.mics(),1);
  assert.equal(h.calls.filter(c=>c.path==='/api/agent/audio').length,0);
  await h.button('⏹ Yozishni tugatish').click();await settle();
  assert.equal(h.calls.filter(c=>c.path==='/api/agent/audio').length,1);
});

test('closing the recording sheet submits a draft, never confirmation',async()=>{
  const h=harness();await settle();
  await h.find(n=>n.id==='ernest-launch').click();await settle();
  await h.find(n=>n.attrs['aria-label']==='CLOSE').click();await settle();
  assert.equal(h.find(n=>n.id==='ernest-agent').hidden,true);
  assert.equal(h.calls.filter(c=>c.path==='/api/agent/audio').length,1);
  assert.equal(h.calls.filter(c=>c.path.endsWith('/confirm')).length,0);
  assert.ok(h.tracks.every(t=>t.stopped));
});

test('backgrounding stops microphone and submits a draft',async()=>{
  const h=harness();await settle();
  await h.find(n=>n.id==='ernest-launch').click();await settle();
  h.document.hidden=true;h.document.events.visibilitychange();await settle();
  assert.equal(h.calls.filter(c=>c.path==='/api/agent/audio').length,1);
  assert.equal(h.calls.filter(c=>c.path.endsWith('/confirm')).length,0);
  assert.ok(h.tracks.every(t=>t.stopped));
});

test('Inbox opens without microphone; keep does not confirm',async()=>{
  const h=harness();await settle();
  await h.find(n=>n.id==='ernest-inbox-launch').click();await settle();
  assert.equal(h.mics(),0);
  assert.ok(h.calls.some(c=>c.path.startsWith('/api/agent/inbox')));
  await h.find(n=>n.id==='ernest-launch').click();await settle();
  await h.button('⏹ Yozishni tugatish').click();await settle();
  await h.button('KEEP').click();await settle();
  assert.equal(h.calls.filter(c=>c.path.endsWith('/confirm')).length,0);
  assert.ok(h.find(n=>n.textContent==='📥 ertaga meeting'));
});
