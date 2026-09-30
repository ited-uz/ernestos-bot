/* Ernest Inbox. Private content always uses textContent, never HTML interpolation.
   No keys/audio in localStorage. Bot and Mini App share server-side drafts. */
(() => {
  'use strict';
  const el = (tag, text, cls) => { const n = document.createElement(tag); if(text != null) n.textContent=text; if(cls)n.className=cls; return n; };
  const widget = el('section'); widget.id='ernest-agent'; widget.hidden=true;
  widget.setAttribute('role','dialog'); widget.setAttribute('aria-modal','true'); widget.setAttribute('aria-label','Ernest');
  const launch = el('button','🎙 Ernest'); launch.id='ernest-launch'; launch.hidden=true;
  launch.setAttribute('aria-label','Ernest · Record');
  const draftsLaunch=el('button','📥 Inbox');draftsLaunch.id='ernest-inbox-launch';draftsLaunch.hidden=true;
  draftsLaunch.setAttribute('aria-label','Ernest · Inbox');
  const head=el('header',null,'ernest-head'), title=el('h2','Ernest'), close=el('button','×');
  close.setAttribute('aria-label','Close'); head.append(title,close);
  const body=el('div',null,'ernest-body'); widget.append(head,body); document.body.append(launch,draftsLaunch,widget);
  let meta=null, selected=null, editing=null, busy=false, recording=null, stream=null, audioBlob=null, audioURL=null;
  let lastRequest=null, stopTimer=null, pollTimer=null, offset=0, activeView='', previousFocus=null, startAfterConsent=false;
  const w = key => meta?.strings?.[key] || key;
  const lang = () => { try{return state.me?.language || 'uz';}catch(_){return 'uz';} };
  const ui = (uz,ru,en) => ({uz,ru,en}[lang()] || uz);
  const key = () => crypto.randomUUID().replace(/-/g,'');
  function button(label,fn,primary=false){const n=el('button',label,primary?'ernest-primary':'');n.type='button';n.onclick=fn;return n;}
  async function call(path, options={}){
    const res=await fetch('/api/agent'+path,{
      ...options, signal:options.signal || AbortSignal.timeout(170000),
      headers:{'X-Telegram-Init-Data':window.Telegram?.WebApp?.initData || '',
               ...(options.raw?{}:{'Content-Type':'application/json'}),...(options.headers||{})},
      body:options.body == null?undefined:(options.raw?options.body:JSON.stringify(options.body))
    });
    let data;try{data=await res.json();}catch(_){data={};}
    if(!res.ok){const error=new Error(typeof data.detail==='string'?data.detail:'failure');error.status=res.status;throw error;}
    return data;
  }
  function error(e){
    const old=body.querySelector('[role="alert"]');if(old)old.remove();
    const text=meta?.strings?.[e.message] || (e.status?w('failure'):ui('Aloqa uzildi. Inbox’ni tekshiring yoki shu xabarni qayta yuboring.','Связь прервана. Проверьте Входящие или повторите отправку.','Connection interrupted. Check Inbox or retry the same message.'));
    const n=el('p',text,'ernest-error');n.setAttribute('role','alert');body.prepend(n);
  }
  async function act(fn){if(busy)return;busy=true;body.querySelectorAll('button').forEach(n=>n.disabled=true);try{await fn();}catch(e){error(e);}finally{busy=false;body.querySelectorAll('button').forEach(n=>n.disabled=false);}}
  function stop(discard=false){
    clearTimeout(stopTimer);
    if(recording && recording.state!=='inactive'){if(discard)recording.onstop=()=>{};recording.stop();}
    stream?.getTracks().forEach(track=>track.stop());stream=null;recording=null;
  }
  function clearAudio(){stop(true);audioBlob=null;if(audioURL)URL.revokeObjectURL(audioURL);audioURL=null;}
  // Closing this sheet stops and submits the recording as a draft, not an action.
  function hide(){stop();clearTimeout(pollTimer);widget.hidden=true;document.body.style.overflow='';previousFocus?.focus();}
  close.onclick=hide;
  widget.addEventListener('keydown',event=>{
    if(event.key==='Escape'){hide();return;}
    if(event.key==='Tab'){
      const nodes=[...widget.querySelectorAll('button:not(:disabled),textarea,audio,input')].filter(n=>!n.hidden);
      const first=nodes[0],last=nodes[nodes.length-1];
      if(event.shiftKey && document.activeElement===first){event.preventDefault();last?.focus();}
      if(!event.shiftKey && document.activeElement===last){event.preventDefault();first?.focus();}
    }
  });
  window.addEventListener('pagehide',()=>{stop(true);clearTimeout(pollTimer);});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  function nav(){const n=el('div',null,'ernest-controls');n.append(button(w('new'),()=>compose()),button(w('inbox'),()=>act(()=>inbox(0))));return n;}
  function frame(){clearTimeout(pollTimer);body.replaceChildren();body.append(nav());}
  async function welcome(){
    frame();activeView='welcome';const card=el('div',null,'ernest-card');
    card.append(el('p',w('welcome')),el('p','AI: '+meta.provider,'ernest-muted'));
    if(!meta.enabled){card.append(el('p',w('disabled')));}
    else card.append(button(w('consent'),()=>act(async()=>{await call('/consent',{method:'POST',body:{accepted:true}});meta.consent=true;const recordNow=startAfterConsent;startAfterConsent=false;compose(null,recordNow);}),true));
    body.append(card);
  }
  function compose(draft=null,recordNow=false){
    if(!meta?.consent || !meta.enabled)return welcome();
    clearAudio();lastRequest=null;editing=draft;selected=null;activeView='compose';frame();
    const card=el('div',null,'ernest-card');
    card.append(el('p',draft?w('edit_prompt'):ui('Ayting — Ernest tayyorlaydi. Tasdiqlamaguningizcha hech narsa bajarilmaydi.','Скажите — Ernest подготовит. Без подтверждения ничего не выполнится.','Say it — Ernest prepares it. Nothing runs before confirmation.')));
    if(draft){card.append(el('p',draft.transcript,'ernest-muted'));}
    const text=el('textarea');text.maxLength=6000;
    text.placeholder=draft?ui('Tuzatish…','Исправление…','Correction…'):ui('Masalan: Ertaga soat 10 da hisobot tayyorlashni qo‘sh.','Например: добавь задачу подготовить отчёт завтра в 10.','E.g. add a task to prepare the report tomorrow at 10.');
    text.setAttribute('aria-label',ui('Buyruq yoki tuzatish','Команда или исправление','Command or correction'));
    const audioArea=el('div'),controls=el('div',null,'ernest-controls');
    function showAudio(blob){
      if(!blob.size || blob.size>meta.audio_bytes){clearAudio();error(new Error('invalid_audio'));return;}
      audioBlob=blob;lastRequest=null;audioArea.replaceChildren();if(audioURL)URL.revokeObjectURL(audioURL);
      audioURL=URL.createObjectURL(blob);const player=el('audio');player.controls=true;player.src=audioURL;
      audioArea.append(player,button(ui('Audioni olib tashlash','Убрать аудио','Remove audio'),()=>{clearAudio();audioArea.replaceChildren();}));
    }
    let startingRecord=false;
    const record=button(ui('🎙 Ovoz yozish','🎙 Записать','🎙 Record'),async()=>{
      if(startingRecord)return;
      if(recording){stop();record.textContent=ui('🎙 Ovoz yozish','🎙 Записать','🎙 Record');record.classList.remove('ernest-recording');return;}
      if(text.value.trim()){error(new Error('invalid_text'));return;}
      try{
        startingRecord=true;
        clearAudio();audioArea.replaceChildren();
        if(!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder)throw new Error('mic');
        stream=await navigator.mediaDevices.getUserMedia({audio:true});
        if(widget.hidden || activeView!=='compose'){stop(true);return;}
        const type=['audio/webm;codecs=opus','audio/mp4','audio/ogg;codecs=opus'].find(t=>MediaRecorder.isTypeSupported(t));
        const rec=new MediaRecorder(stream,type?{mimeType:type}:{}), chunks=[];
        recording=rec;
        rec.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
        rec.onstop=()=>{
          showAudio(new Blob(chunks,{type:rec.mimeType || 'audio/webm'}));
          record.textContent=ui('🎙 Ovoz yozish','🎙 Записать','🎙 Record');record.classList.remove('ernest-recording');
          text.disabled=false;body.querySelectorAll('button').forEach(n=>n.disabled=false);
          // End of recording automatically creates a durable draft, NEVER executes.
          if(audioBlob && activeView==='compose')send.click();
        };
        rec.onerror=()=>{stop(true);text.disabled=false;body.querySelectorAll('button').forEach(n=>n.disabled=false);record.classList.remove('ernest-recording');record.textContent=ui('🎙 Ovoz yozish','🎙 Записать','🎙 Record');error(new Error('invalid_audio'));};
        rec.start();record.textContent=ui('⏹ Yozishni tugatish','⏹ Остановить','⏹ Stop recording');record.classList.add('ernest-recording');
        text.disabled=true;body.querySelectorAll('button').forEach(n=>{n.disabled=n!==record;});
        stopTimer=setTimeout(()=>stop(),(meta.audio_seconds-1)*1000);
      }catch(_){stop(true);error(new Error('invalid_audio'));audioArea.append(el('p',ui('Mikrofonga ruxsat bering yoki Telegram botiga ovoz yuboring.','Разрешите микрофон или отправьте голос боту.','Allow microphone access or send a voice message to the bot.'),'ernest-muted'));}
      finally{startingRecord=false;}
    });
    const file=el('input');file.type='file';file.accept='audio/*,.ogg,.webm,.m4a';file.hidden=true;
    file.onchange=()=>{if(file.files[0])showAudio(file.files[0]);};
    const send=button(ui('Tahlil qilish →','Разобрать →','Analyze →'),()=>act(async()=>{
      if(recording){stop();return;}
      const value=text.value.trim();if(!value && !audioBlob)return;
      const signature=JSON.stringify([value,editing?.id,editing?.revision]);
      if(!lastRequest || lastRequest.signature!==signature || lastRequest.audio!==audioBlob)lastRequest={key:key(),signature,audio:audioBlob};
      const progress=el('p',w('processing'),'ernest-muted');progress.setAttribute('role','status');card.append(progress);
      try{
        let draft;
        if(audioBlob){
          if(value)throw new Error('invalid_text'); // never silently discard typed correction
          const query=editing?'?draft_id='+encodeURIComponent(editing.id)+'&revision='+editing.revision:'';
          draft=await call('/audio'+query,{method:'POST',raw:true,body:audioBlob,headers:{'Content-Type':audioBlob.type || 'application/octet-stream','X-Agent-Request-Key':lastRequest.key}});
        }else draft=await call('/text',{method:'POST',body:{text:value,request_key:lastRequest.key,draft_id:editing?.id || null,revision:editing?.revision || null}});
        lastRequest=null;clearAudio();show(draft);
      }finally{progress.remove();}
    }),true);
    controls.append(record,button(ui('Audio fayl','Аудиофайл','Audio file'),()=>file.click()),send);
    card.append(text,file,audioArea,controls,el('p',ui('2 daqiqagacha · 10 MB · Ovoz yoki matndan bittasini yuboring.','До 2 минут · 10 МБ · Отправьте либо аудио, либо текст.','Up to 2 min · 10 MB · Send audio or text, not both.'),'ernest-muted'));
    body.append(card);
    if(recordNow)record.onclick();else text.focus();
  }
  function show(draft){
    selected=draft;editing=null;activeView='draft';frame();
    const card=el('article',null,'ernest-card');
    card.append(el('div',w(['ready','executed','cancelled','processing'].includes(draft.status)?draft.status:'inbox_status'),'ernest-badge'));
    card.append(el('p',draft.transcript || '🎙 Audio','ernest-copy'));
    if(draft.language)card.append(el('p',w('input_language')+': '+w(draft.language),'ernest-muted'));
    if(draft.preview)card.append(el('p',draft.preview,'ernest-copy'));
    if(draft.error)card.append(el('p',w(draft.error),'ernest-error'));
    const controls=el('div',null,'ernest-controls');
    const post=action=>act(async()=>{
      const result=await call('/drafts/'+draft.id+'/'+action,{method:'POST',body:{revision:draft.revision,...(action==='retry'?{request_key:key()}: {})}});
      show(result);
      if(action==='confirm'){try{await loadScreen(state.screen);}catch(_){/* Inbox result remains authoritative. */}}
    });
    if(draft.status==='ready')controls.append(button(w('confirm'),()=>post('confirm'),true));
    if(!['executed','cancelled','processing'].includes(draft.status)){
      controls.append(button(w('edit'),()=>compose(draft)),button(w('cancel'),()=>post('cancel')));
      if(draft.transcript)controls.append(button(w('retry'),()=>post('retry')));
      controls.append(button(w('keep'),()=>act(()=>inbox(0))));
    }
    card.append(controls);body.append(card);
    if(draft.status==='processing' && !widget.hidden){
      pollTimer=setTimeout(()=>{if(!widget.hidden && selected?.id===draft.id)call('/drafts/'+draft.id).then(show).catch(error);},3000);
    }
  }
  async function inbox(page=0){
    stop(true);editing=null;selected=null;activeView='inbox';offset=page;frame();
    const result=await call('/inbox?offset='+page);
    if(activeView!=='inbox' || offset!==page)return;
    const card=el('div',null,'ernest-card');card.append(el('h3',w('inbox')));
    const list=el('div',null,'ernest-list');
    for(const draft of result.drafts){
      const icon={executed:'✅',cancelled:'❌',processing:'⏳'}[draft.status] || '📥';
      const item=button(icon+' '+(draft.transcript.slice(0,110) || '🎙 Audio'),()=>show(draft));item.className='ernest-item';list.append(item);
    }
    if(!result.drafts.length)list.append(el('p',w('empty')));
    card.append(list);body.append(card);
    if(page>0)body.append(button('←',()=>act(()=>inbox(Math.max(0,page-30)))));
    if(result.drafts.length===30)body.append(button(w('more'),()=>act(()=>inbox(page+30))));
    body.append(button(ui('AI tarixini o‘chirish','Удалить историю AI','Delete AI history'),()=>act(async()=>{
      if(!window.confirm(ui('Inbox va AI tarixi o‘chadi. Qo‘shilgan vazifalar va pul yozuvlari qoladi. Davom etasizmi?','Удалятся Входящие и история AI. Созданные задачи и финансы останутся. Продолжить?','Delete Inbox and AI history? Created tasks and money entries will remain. Continue?')))return;
      await call('/history',{method:'DELETE',body:{accepted:true}});meta.consent=false;welcome();
    })));
  }
  async function open(recordNow=false){
    previousFocus=document.activeElement;widget.hidden=false;document.body.style.overflow='hidden';close.focus();
    try{meta=await call('/meta');close.setAttribute('aria-label',w('close'));startAfterConsent=recordNow;meta.consent&&meta.enabled?compose(null,recordNow):welcome();}catch(e){error(e);}
  }
  launch.onclick=()=>open(true);
  draftsLaunch.onclick=async()=>{await open(false);if(meta?.consent)await act(()=>inbox(0));};
  // Show only after authenticated onboarding has completed; retry on user tap
  // via the global helper when loading the profile was slower than this script.
  window.ErnestAgent={open};
  let attempts=0;
  async function init(){
    try{meta=await call('/meta',{signal:AbortSignal.timeout(10000)});launch.hidden=draftsLaunch.hidden=!meta.enabled;}
    catch(_){if(++attempts<6)setTimeout(init,5000);}
  }
  init();
})();
