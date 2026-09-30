/* ==================================================================
   ErnestOS — DESIGN PREVIEW ONLY.

   Loaded by index.html only when the URL carries ?preview. It answers the
   app's /api/* requests in the browser with SAMPLE data so the interface can
   be reviewed without Telegram or the backend. The server serves index.html
   alone (see app.py → index()), so this file never reaches production users,
   and nothing here writes to a real account.

   URL options (all optional):
     ?preview&screen=home|habits|tasks|team|stats|money
            &tab=prayer|journal|open|projects|done|calendar|work|results
            &mode=light|dark   &lang=uz|en|ru   &theme=ocean|midnight|aurora|bento|spatial
            &sheet=task|habit|settings|look|notify|timer
            &scenario=normal|empty|offline|loading
   ================================================================== */
(function(){
  "use strict";
  const Q = new URLSearchParams(location.search);
  const LANG = Q.get("lang") || "en";
  const SCEN = Q.get("scenario") || "normal";
  const EMPTY = SCEN === "empty";

  const pad = n => String(n).padStart(2, "0");
  const iso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const now = new Date();
  const day = off => { const d = new Date(now); d.setDate(d.getDate() + off); return iso(d); };
  const TODAY = day(0);
  const LOCALE = {uz:"uz-UZ", en:"en-GB", ru:"ru-RU"}[LANG] || "en-GB";
  let dateLabel;
  try{ dateLabel = now.toLocaleDateString(LOCALE, {weekday:"long", day:"numeric", month:"long"}); }
  catch(_){ dateLabel = TODAY; }

  /* Sample copy in each language, deliberately including long strings so
     wrapping can be checked in Uzbek and Russian as well as English. */
  const S = {
    en:{now:"Draft the Q4 plan and share it with the team", t2:"Review pull requests",
        t3:"Send the invoice to the design studio", t4:"Call grandma",
        t5:"Book the passport renewal appointment", t6:"Prepare slides for Thursday",
        t7:"Dentist", t8:"Read “Deep Work”", team1:"Buy groceries for the weekend",
        work:"Work", home:"Home renovation", lang:"Learn Spanish", trip:"Summer trip",
        h1:"Wake up at 6:00", h2:"5x prayer", h3:"Read 20 pages", h4:"Deep work 2h",
        h5:"Walk 8,000 steps", h6:"Close the day", h7:"Gym",
        f1:"Ship the onboarding redesign", f2:"Run three user interviews",
        cd1:"IELTS exam", cd2:"Product launch", fam:"Family", walk:"Evening walk together",
        bill:"Pay the internet bill", q:"Small steps, every day.",
        j1:"What went well today?", j2:"What will you do differently tomorrow?",
        j3:"What are you grateful for?", a1:"Finished the onboarding flow."},
    uz:{now:"To'rtinchi chorak rejasini tayyorlab, jamoaga yuborish", t2:"Pull requestlarni ko'rib chiqish",
        t3:"Dizayn studiyasiga hisob-fakturani yuborish", t4:"Buvimga qo'ng'iroq qilish",
        t5:"Pasportni yangilash uchun navbatga yozilish", t6:"Payshanba uchun slaydlar tayyorlash",
        t7:"Tish shifokori", t8:"“Deep Work” kitobini o'qish", team1:"Dam olish kunlari uchun oziq-ovqat olish",
        work:"Ish", home:"Uy ta'miri", lang:"Ispan tilini o'rganish", trip:"Yozgi sayohat",
        h1:"6:00 da turish", h2:"5 mahal namoz", h3:"20 bet kitob o'qish", h4:"Chuqur ish 2 soat",
        h5:"8 000 qadam yurish", h6:"Kun yakuni", h7:"Sport zal",
        f1:"Onboarding dizaynini ishga tushirish", f2:"Uchta foydalanuvchi bilan suhbat",
        cd1:"IELTS imtihoni", cd2:"Mahsulot taqdimoti", fam:"Oila", walk:"Birga kechki sayr",
        bill:"Internet to'lovini qilish", q:"Har kuni kichik qadamlar.",
        j1:"Bugun nima yaxshi o'tdi?", j2:"Ertaga nimani boshqacha qilasiz?",
        j3:"Nimadan minnatdorsiz?", a1:"Onboarding oqimini tugatdim."},
    ru:{now:"Подготовить план на четвёртый квартал и отправить команде", t2:"Проверить пул-реквесты",
        t3:"Отправить счёт дизайн-студии", t4:"Позвонить бабушке",
        t5:"Записаться на продление загранпаспорта", t6:"Подготовить слайды к четвергу",
        t7:"Стоматолог", t8:"Прочитать «Deep Work»", team1:"Купить продукты на выходные",
        work:"Работа", home:"Ремонт квартиры", lang:"Испанский язык", trip:"Летняя поездка",
        h1:"Подъём в 6:00", h2:"5 намазов", h3:"Читать 20 страниц", h4:"Глубокая работа 2 ч",
        h5:"Пройти 8 000 шагов", h6:"Итоги дня", h7:"Спортзал",
        f1:"Запустить новый онбординг", f2:"Провести три интервью с пользователями",
        cd1:"Экзамен IELTS", cd2:"Запуск продукта", fam:"Семья", walk:"Вечерняя прогулка вместе",
        bill:"Оплатить интернет", q:"Маленькие шаги каждый день.",
        j1:"Что сегодня получилось?", j2:"Что завтра сделаете иначе?",
        j3:"За что вы благодарны?", a1:"Закончил сценарий онбординга."},
  }[LANG] || null;
  const s = S || {};

  /* ---------------- sample database ---------------- */
  const task = (id, title, extra) => ({id, title, priority:"medium", deadline:null,
    due_time:null, project:null, recurrence:"", remind_before:null, timer_minutes:null,
    status:"open", top3:false, overdue:false, ...extra});

  const DB = {
    me:{telegram_id:1, name:"Ernest", language:LANG, onboarded:true, gated:false,
        theme:Q.get("theme") || "ocean", gender:"male",
        modules:{wake:true, prayer:true, journal:true}, member_no:128, username:"ernest",
        has_photo:false, avatar_token:"",
        prefs:{morning_report:true, morning_time:"05:30", evening_report:true,
               evening_time:"21:30", task_reminders:true, habit_reminders:true,
               timezone:"Asia/Tashkent"},
        timezones:["Asia/Tashkent","Europe/Moscow","Europe/London","Asia/Dubai",
                   "Europe/Istanbul","Asia/Almaty","Europe/Berlin","America/New_York",
                   "Asia/Seoul","Asia/Tokyo","Asia/Shanghai","UTC","Asia/Samarkand"]},
    tasks:EMPTY ? [] : [
      task(101, s.now, {priority:"high", deadline:TODAY, due_time:"10:00", project:s.work,
                        top3:true, remind_before:30}),
      task(102, s.t2, {deadline:TODAY, due_time:"14:00", project:s.work}),
      task(103, s.t3, {priority:"low", deadline:TODAY, project:s.work, recurrence:"monthly"}),
      task(104, s.t4, {deadline:TODAY, timer_minutes:null}),
      task(105, s.t5, {priority:"high", deadline:day(-2), overdue:true}),
      task(106, s.t6, {deadline:day(1), project:s.work}),
      task(107, s.t7, {deadline:day(3), due_time:"09:30", remind_before:60}),
      task(108, s.t8, {priority:"low"}),
    ],
    habits:EMPTY ? [] : [
      {id:1, name:s.h1, system_key:"wakeup", target_time:"06:00", cat:"non_negotiable", done:true},
      {id:2, name:s.h2, system_key:"prayer", protected:true, cat:"non_negotiable", done:false},
      {id:6, name:s.h6, system_key:"journal", protected:true, cat:"non_negotiable", done:true},
      {id:3, name:s.h3, cat:"target", remind_at:"21:00", done:true},
      {id:4, name:s.h4, cat:"target", timer_minutes:120, done:false},
      {id:5, name:s.h5, cat:"bonus", done:true},
      {id:7, name:s.h7, cat:"bonus", due:false, schedule:"custom", days:[0,2,4], done:false},
    ],
    prayers:{bomdod:"jamaat", peshin:"on_time", asr:"qaza", shom:null, xufton:null},
  };
  DB.habits.forEach(h => Object.assign(h, {due:h.due !== false, source:"personal",
    schedule:h.schedule || "daily", paused:false, protected:!!h.protected}));

  const team = () => EMPTY ? [] : [{
    id:1, name:s.fam,
    members:[{user_id:1, name:"Ernest", role:"owner"}, {user_id:2, name:"Gulyora", role:"member"}],
    permissions:{manage_items:true},
    board:{
      members:[{user_id:1, name:"Ernest"}, {user_id:2, name:"Gulyora"}],
      units:{items:4, confirmations:8, left:3},
      periods:{
        day:[{user_id:1, name:"Ernest", percent:50, delta:10, done:2, total:4},
             {user_id:2, name:"Gulyora", percent:75, delta:-5, done:3, total:4}],
        week:[{user_id:1, name:"Ernest", percent:71, delta:6, done:20, total:28},
              {user_id:2, name:"Gulyora", percent:82, delta:3, done:23, total:28}],
        month:[{user_id:1, name:"Ernest", percent:68, delta:null, done:81, total:120},
               {user_id:2, name:"Gulyora", percent:77, delta:null, done:92, total:120}]},
      open:[{id:201, kind:"task", title:s.team1, missing:[1, 2], done_by:[]},
            {id:401, kind:"habit", title:s.walk, missing:[2], done_by:[1]}],
      done:[{title:s.bill}], open_count:2},
    tasks:[{id:201, title:s.team1, done:false, owed:true, deadline:TODAY, completion:"any",
            done_by:[], created_by:1, priority:"medium", team_id:1, team_name:s.fam}],
    habits:[{id:401, name:s.walk, done:true, due:true, done_by:[1], created_by:1}],
    projects:[{id:9, name:s.trip, tasks_done:3, tasks_total:8, percent:38}],
    countdowns:[],
  }];

  const tasksPayload = () => {
    const open = DB.tasks.filter(x => x.status !== "done");
    return {
      overdue: open.filter(x => x.overdue),
      upcoming: open.filter(x => !x.overdue && x.deadline),
      undated: open.filter(x => !x.deadline), later: [],
      team_tasks: team()[0]?.tasks || [], teams: EMPTY ? [] : [{id:1, name:s.fam}],
      active_timer:null,
    };
  };
  const habitsPayload = () => {
    const grouped = {non_negotiable:[], target:[], bonus:[]};
    DB.habits.forEach(h => grouped[h.cat].push(h));
    const tier = (c, w) => { const due = grouped[c].filter(h => h.due);
      return {due:due.length, done:due.filter(h => h.done).length, weight:w, applied:w}; };
    return {habits:DB.habits, grouped, categories:["non_negotiable","target","bonus"],
            tiers:{non_negotiable:tier("non_negotiable", 50), target:tier("target", 30),
                   bonus:tier("bonus", 20)},
            streak:EMPTY ? 0 : 6, wake:{logged:!EMPTY, done:true, at:"05:52"},
            active_timer:null, teams:[]};
  };
  const home = () => {
    const open = DB.tasks.filter(x => x.status !== "done");
    const today = open.filter(x => x.deadline === TODAY);
    const byProject = {};
    today.forEach(x => (byProject[x.project || ""] ||= []).push(x));
    const due = DB.habits.filter(h => h.due);
    const lead = open.find(x => x.top3) || open.find(x => x.overdue) || today[0];
    return {
      date:TODAY, date_label:dateLabel, name:"Ernest", quote:EMPTY ? "" : s.q, break:null,
      now: lead ? {kind:"task", id:lead.id, title:lead.title,
                   reason:lead.top3 ? "pinned" : lead.overdue ? "overdue" : "due_today",
                   due_time:lead.due_time, priority:lead.priority, project:lead.project}
                : {kind:"clear", reason:"clear"},
      top3: open.filter(x => x.top3),
      tasks_today: Object.entries(byProject).map(([p, tasks]) => ({project:p || null,
                    tasks: tasks.filter(x => !x.top3)})),
      team_today: EMPTY ? [] : team()[0].tasks.map(x => ({...x, source:"team"})),
      countdowns: EMPTY ? [] : [
        {id:1, title:s.cd1, date:day(12), days_left:12, scope:"general"},
        {id:2, title:s.cd2, date:day(33), days_left:33, scope:"general"}],
      overall:{value:EMPTY ? 0 : 62, yesterday:EMPTY ? null : 55, measured:!EMPTY,
               components:{tasks:EMPTY ? null : 57, habits:EMPTY ? null : 67,
                           focus:EMPTY ? null : 50, prayer:EMPTY ? null : 60}},
      habits:{done:due.filter(h => h.done).length, total:due.length},
      counts:{tasks:{done:EMPTY ? 0 : 1, total:EMPTY ? 0 : today.length + 1},
              habits:{done:due.filter(h => h.done).length, total:due.length},
              prayer:{done:EMPTY ? 0 : 3, total:5, excused:false, owed:true}},
      streak:EMPTY ? 0 : 6,
      prayer:{performed:EMPTY ? 0 : 3, required:5, excused:false},
      modules:DB.me.modules, active_timer:null,
    };
  };
  const PRESETS = [["wakeup","non_negotiable",s.h1,true], ["prayer","non_negotiable",s.h2,true],
    ["journal","non_negotiable",s.h6,true], ["plan","target","Plan",false],
    ["deep","target",s.h4,false], ["sport","target",s.h7,false], ["read","target",s.h3,false],
    ["water","bonus","2 L",false], ["language","bonus",s.lang,false], ["sleep","bonus","23:00",false]];
  const money = () => ({month:TODAY.slice(0, 7), year:now.getFullYear(), month_no:now.getMonth() + 1,
    is_current:true, income:EMPTY ? 0 : 5000000, expense:EMPTY ? 0 : 1395000,
    balance:EMPTY ? 0 : 3605000, count:EMPTY ? 0 : 4,
    categories:[["food","🍔",2000000,45000],["transport","🚕",800000,30000],
      ["home","🏠",1500000,0],["health","💊",500000,120000],["fun","🎮",1000000,0],
      ["business","💼",0,1200000],["other","📦",500000,0]].map(([id, icon, limit, spent]) =>
      ({id, icon, limit, spent:EMPTY ? 0 : spent, percent:limit ? Math.round(spent / limit * 100) : null,
        over:false})),
    entries:EMPTY ? [] : [
      {id:1, kind:"expense", amount:1200000, category:"business", note:"Reklama", source:"manual", day:TODAY},
      {id:2, kind:"income", amount:5000000, category:"salary", note:"", source:"voice", day:TODAY},
      {id:3, kind:"expense", amount:120000, category:"health", note:"Dorixona", source:"manual", day:day(-1)},
      {id:4, kind:"expense", amount:45000, category:"food", note:"Tushlik", source:"manual", day:day(-2)}],
    category_ids:["food","transport","home","health","fun","business","other","salary","sales","other_in"],
    kinds:{food:"expense", transport:"expense", home:"expense", health:"expense", fun:"expense",
           business:"expense", other:"expense", salary:"income", sales:"income", other_in:"income"},
    icons:{food:"🍔", transport:"🚕", home:"🏠", health:"💊", fun:"🎮", business:"💼", other:"📦",
           salary:"💰", sales:"📈", other_in:"➕"}});
  const summary = () => {
    const today = DB.tasks.filter(x => x.deadline === TODAY);
    const due = DB.habits.filter(h => h.due);
    return {today:{overall:62, measured:!EMPTY,
      tasks_done:EMPTY ? 0 : 4 + today.filter(x => x.status === "done").length,
      tasks_total:EMPTY ? 0 : 7, habits_done:due.filter(h => h.done).length,
      habits_total:due.length, prayer_performed:3, prayer_required:5},
      windows:EMPTY ? {} : {day:{overall:62, delta:7, measured:true},
                            week:{overall:68, delta:4, measured:true},
                            month:{overall:64, delta:-3, measured:true}}};
  };
  const stats = period => {
    const n = period === "year" ? 12 : period === "month" ? 30 : 7;
    const lbl = i => { const d = new Date(now); d.setDate(d.getDate() - (n - 1 - i));
      return period === "year" ? String(i + 1) : String(d.getDate()); };
    const wave = (i, base, amp) => Math.max(0, Math.min(100, Math.round(base + amp * Math.sin(i * 1.3))));
    return {period,
      today:{overall:62, measured:true, yesterday:55, tasks:57, habits:67, focus:50, prayer:60,
             prayer_performed:3, prayer_required:5, streak:6},
      deltas:{tasks:5, habits:-4, focus:10, prayer:0},
      series:EMPTY ? [] : Array.from({length:n}, (_, i) => ({label:lbl(i),
        overall:wave(i, 64, 14), tasks:wave(i + 1, 58, 20), habits:wave(i + 2, 70, 15),
        focus:wave(i + 3, 52, 18), prayer:wave(i + 4, 66, 12)})),
      averages:{overall:66, tasks:61, habits:72, focus:55, prayer:70},
      best_day:{day:day(-3), overall:88},
      prayer_detail:{full_days:4, days:7, on_time_percent:76, jamaat:9, qaza:3, missed:2,
                     consistency:81}};
  };
  const calendar = () => {
    const y = now.getFullYear(), m = now.getMonth();
    const first = (new Date(y, m, 1).getDay() + 6) % 7;
    const dim = new Date(y, m + 1, 0).getDate();
    const ev = {};
    const add = (off, kind, title) => { const k = day(off);
      if(k.slice(0, 7) === TODAY.slice(0, 7)) (ev[k] ||= []).push({kind, title}); };
    if(!EMPTY){ add(0, "task", s.t2); add(1, "task", s.t6); add(3, "task", s.t7);
      add(3, "birthday", "Gulyora"); add(8, "project", s.work); }
    return {year:y, month:m + 1, first_weekday:first, days_in_month:dim, today:TODAY, events:ev};
  };

  const ROUTES = [
    [/^\/api\/me$/, () => DB.me],
    [/^\/api\/home$/, home],
    [/^\/api\/summary$/, summary],
    [/^\/api\/progress\/me$/, () => EMPTY ? {
      level:{key:"starter", numeral:"I", xp:0, next_threshold:100, remaining:100,
             next_key:"builder", progress:0},
      rank:{eligible:false, days_remaining:7, global:null, top_percent:null, users:0},
      daily:{grade:"—", score:0}, streak:{current:0}} : {
      level:{key:"builder", numeral:"II", xp:1240, next_threshold:2000, remaining:760,
             next_key:"operator", progress:0.62},
      rank:{eligible:true, global:214, top_percent:18, users:1180, days_remaining:0},
      daily:{grade:"B", score:74}, streak:{current:6}}],
    [/^\/api\/teams\/\d+\/activity$/, () => ({activity:[
      {who:"Gulyora", action:"done", subject:s.bill, at:new Date(now - 36e5).toISOString()}]})],
    [/^\/api\/teams$/, () => ({teams:team()})],
    [/^\/api\/habits$/, habitsPayload],
    [/^\/api\/habits\/presets$/, () => ({presets:PRESETS.map(([key, category, name, system], i) =>
      ({key, category, name, system, added:!EMPTY && i < 7, habit_id:null}))})],
    [/^\/api\/money$/, money],
    [/^\/api\/teams\/\d+\/stats$/, () => ({together:EMPTY ? null : 71,
      members:[{user_id:1, name:"Ernest"}, {user_id:2, name:"Gulyora"}],
      series:EMPTY ? [] : Array.from({length:7}, (_, i) => {
        const a = 40 + (i * 9) % 55, b = 55 + (i * 13) % 40;
        return {label:day(i - 6).slice(8), 1:a, 2:b, avg:Math.round((a + b) / 2),
                team:Math.round((a + b) / 2)};
      })})],
    [/^\/api\/prayers$/, () => ({prayers:DB.prayers, statuses:["jamaat","on_time","qaza","missed"],
      performed:Object.values(DB.prayers).filter(v => v && v !== "missed").length, required:5,
      score:7, max:15, complete:false, excused:false})],
    [/^\/api\/journal$/, () => ({entry:{answers:{1:s.a1}, mood:"good"},
      questions:[{id:1, text:s.j1}, {id:2, text:s.j2}, {id:3, text:s.j3}]})],
    [/^\/api\/tasks\/done$/, () => ({groups:{today:[], week:EMPTY ? [] :
      [{id:120, title:s.bill, project:null}], earlier:[]}})],
    [/^\/api\/tasks$/, tasksPayload],
    [/^\/api\/projects$/, () => ({projects:EMPTY ? [] : [
      {id:1, name:s.work, status:"active", archived:false, tasks_total:12, tasks_done:7,
       tasks_open:5, progress:58, deadline:day(14)},
      {id:2, name:s.home, status:"active", archived:false, tasks_total:8, tasks_done:2,
       tasks_open:6, progress:25, deadline:null},
      {id:3, name:s.lang, status:"done", archived:false, tasks_total:5, tasks_done:5,
       tasks_open:0, progress:100, deadline:null}]})],
    [/^\/api\/projects\/\d+\/tasks$/, () => ({project:{id:1, name:s.work, progress:58,
      tasks_done:7, tasks_total:12, status:"active", archived:false, deadline:day(14)},
      tasks:DB.tasks.filter(x => x.project === s.work)})],
    [/^\/api\/focus$/, () => ({week:EMPTY ? {primary:null, supporting:[], slots_free:3} : {
      primary:{id:301, title:s.f1, priority:"high", done:false},
      supporting:[{id:302, title:s.f2, priority:"medium", done:true}], slots_free:1}})],
    [/^\/api\/calendar$/, calendar],
    [/^\/api\/countdowns$/, () => ({countdowns:home().countdowns})],
    [/^\/api\/stats$/, (q) => stats(q.get("period") || "week")],
    [/^\/api\/timers\/candidates\/\w+$/, () => ({items:[{id:4, kind:"habit", title:s.h4,
      timer_minutes:120}]})],
    [/^\/api\/timers\/(\w+)\/(\d+)$/, (q, m) => ({kind:m[1], id:Number(m[2]), title:s.h4,
      timer_minutes:120, timer_mode:"set", parsed_minutes:120, presets:[15,25,45,60,90,120],
      run:null, done:false, protected:false})],
    [/^\/api\/subscription$/, () => ({subscribed:true})],
  ];

  function mutate(method, path, body){
    let m;
    if((m = path.match(/^\/api\/tasks\/(\d+)$/)) && method === "PATCH"){
      const t = DB.tasks.find(x => x.id === Number(m[1]));
      if(t && body?.status) t.status = body.status === "done" ? "done" : "open";
    } else if((m = path.match(/^\/api\/tasks\/(\d+)\/top3$/))){
      const t = DB.tasks.find(x => x.id === Number(m[1])); if(t) t.top3 = !!body?.picked;
    } else if((m = path.match(/^\/api\/habits\/(\d+)\/toggle$/))){
      const h = DB.habits.find(x => x.id === Number(m[1])); if(h) h.done = !h.done;
    } else if(path === "/api/prayers" && body?.prayer){
      DB.prayers[body.prayer] = body.status || null;
    } else if(path === "/api/settings" && body){
      Object.assign(DB.me, body);
    } else if(path === "/api/prefs" && body){
      Object.assign(DB.me.prefs, body);
    }
    return {ok:true, done:true, at:"05:52", answered:1, complete:false, id:999};
  }

  const reply = (data, status = 200) => new Response(
    status === 204 ? null : JSON.stringify(data ?? {}),
    {status, headers:{"Content-Type":"application/json"}});
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const realFetch = window.fetch.bind(window);

  window.fetch = async (input, opts = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if(!url.pathname.startsWith("/api/")) return realFetch(input, opts);
    const method = (opts.method || "GET").toUpperCase();
    if(SCEN === "loading") return new Promise(() => {});
    if(SCEN === "offline") { await wait(300); throw new TypeError("preview: offline"); }
    if(method !== "GET"){
      await wait(650);   // long enough to see the saving state
      let body = null; try{ body = JSON.parse(opts.body || "null"); }catch(_){}
      return reply(mutate(method, url.pathname, body));
    }
    await wait(180);
    for(const [re, fn] of ROUTES){
      const m = url.pathname.match(re);
      if(m) return reply(fn(url.searchParams, m));
    }
    return reply({});
  };

  /* A visible label, in the page flow, so a screenshot can never be mistaken
     for production data. */
  const mark = () => {
    const el = document.createElement("div");
    el.setAttribute("role", "note");
    el.textContent = "Preview · sample data · not connected to an account";
    el.style.cssText = "font:600 12px/1.2 -apple-system,system-ui,sans-serif;" +
      "text-align:center;padding:6px 12px;background:var(--surface-2);" +
      "color:var(--text-2);border-bottom:1px solid var(--border);letter-spacing:.2px";
    document.body.prepend(el);
  };

  /* After the app has booted: pick the screen, mode and sheet from the URL. */
  const apply = () => {
    const screen = Q.get("screen"), tab = Q.get("tab"), mode = Q.get("mode");
    const sheet = Q.get("sheet");
    if(mode === "light" || mode === "dark") state.appearance = mode;
    if(screen && screen !== "home"){
      const patch = {};
      if(screen === "habits" && tab) patch.tab = tab;
      if(screen === "tasks" && tab) patch.taskTab = tab;
      if(screen === "team" && tab) patch.teamTab = tab;
      goto(screen, patch);
    } else render();
    if(sheet) setTimeout(() => {
      if(sheet === "task") A["task-add"]({dataset:{}});
      else if(sheet === "habit") A["habit-add"]({dataset:{}});
      else if(sheet === "timer") openTimer("habit", 4);
      else if(sheet === "quick") A["quick-add"]();
      else openSettings(sheet === "settings" ? "root" : sheet);
    }, 500);
  };
  window.addEventListener("DOMContentLoaded", () => {
    mark();
    if(SCEN === "loading" || SCEN === "offline") return;
    const t0 = Date.now();
    const poll = setInterval(() => {
      if(typeof state !== "undefined" && state.me && !state.loading && state.home){
        clearInterval(poll); apply();
      } else if(Date.now() - t0 > 8000) clearInterval(poll);
    }, 60);
  });
})();
