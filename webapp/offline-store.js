/* ==================================================================
   ErnestOS — OFFLINE COPY ONLY.

   Built into one page by mobile/scripts/build-offline.mjs, never served by
   the server. preview.js answers the app's /api/* requests in the browser;
   this file turns that into a working personal copy:

   - it starts empty, with the app's own first-run setup;
   - everything the person enters is kept in this browser (localStorage)
     and is there again after a reload;
   - a new day starts clean (habits, prayers), and the past days stay on
     record, so the day's result, streaks, Qadam and the statistics are
     counted from what was really done, by the same rules the server uses.

   What it cannot do without the server, and does not pretend to: AI and
   voice, sign-in and sync between devices, push reminders, Telegram, paid
   plans, inviting other people to a group, the global rating.
   ================================================================== */
(function(){
  "use strict";
  const KEY = "ernestos.offline.v1";

  window.ERNEST_LOCAL_STORE = function(P){
    const {DB, W, TEAMS, goalRows, PEOPLE, DEBTS, ROUTES, PRESETS, task, TODAY, day, LANG} = P;
    const iso = P.iso;

    /* ---------------- what is kept ---------------- */
    const X = {v:1, since:TODAY, seq:5000, habitLog:{}, habitQty:{}, prayers:{}, excused:{}, journal:{},
               wake:{}, countdowns:[], projects:[], focus:[], birthdays:[], budgets:{}, reviews:{}};
    const ME_KEYS = ["first_name", "last_name", "name", "username", "gender", "theme", "language",
                     "modules", "prefs", "onboarded", "has_photo", "avatar_custom"];
    let stored = null;
    try{ stored = JSON.parse(localStorage.getItem(KEY) || "null"); }catch(_){ stored = null; }
    const fill = (arr, rows) => { arr.length = 0; (rows || []).forEach(r => arr.push(r)); };
    if(stored && stored.v === 1){
      DB.tasks = stored.tasks || [];
      DB.habits = stored.habits || [];
      DB.money = stored.money || [];
      W.accounts = stored.W?.accounts || []; W.subs = stored.W?.subs || []; W.transfers = stored.W?.transfers || [];
      fill(TEAMS, stored.teams); fill(goalRows, stored.goals); fill(PEOPLE, stored.people); fill(DEBTS, stored.debts);
      ME_KEYS.forEach(k => { if(stored.me && stored.me[k] !== undefined) DB.me[k] = stored.me[k]; });
      Object.assign(X, stored.x || {});
      P.seq.set(stored.seq || {});
      if(X.notes) DB.notesStore = X.notes;
    } else {
      // A first visit: the app's own setup asks for a name and what to track.
      DB.me.onboarded = false;
      DB.me.first_name = ""; DB.me.name = ""; DB.me.username = "";
      DB.me.modules = {wake:false, prayer:false, journal:false};
    }
    // Nothing here is paid for or reaches a server: every feature is open,
    // and the AI is off rather than answering with made-up text.
    DB.me.plan = {...DB.me.plan, enabled:false, tier:"max", until:null, days_left:null};
    DB.me.agent = {enabled:false, available:false, consent:false};
    DB.me.trial = {required:false, remaining:null, free_actions:null, subscribed:false, channel:""};
    DB.me.has_telegram = false; DB.me.email = null; DB.me.member_no = null; DB.me.gated = false;
    DB.me.prefs = {...DB.me.prefs, now_ai:false};
    DB.me.offline_copy = true;
    DB.inbox = [];
    // Notes live in what is saved, so they are in the backup file too.
    X.notes = DB.notesStore = X.notes || DB.notesStore || {notes:[], books:[], seq:1};
    // Today's prayers are the same object preview.js scores.
    X.prayers[TODAY] = X.prayers[TODAY] || {};
    DB.prayers = X.prayers[TODAY];

    /* What is saved is also the backup file: one format, so a backup can
       always be put back. */
    const current = () => ({v:1, app:"ErnestOS", kind:"offline-copy", saved_at:new Date().toISOString(),
      tasks:DB.tasks, habits:DB.habits, money:DB.money, W:{accounts:W.accounts, subs:W.subs, transfers:W.transfers},
      teams:TEAMS, goals:goalRows, people:PEOPLE, debts:DEBTS,
      me:Object.fromEntries(ME_KEYS.map(k => [k, DB.me[k]])), x:X, seq:P.seq.get()});
    const save = () => {
      try{ localStorage.setItem(KEY, JSON.stringify(current())); return true; }
      catch(_){ return false; }
    };
    const newId = () => {
      const s = P.seq.get();
      const id = Math.max(s.nextId || 0, X.seq || 0) + 1;
      X.seq = id; P.seq.set({nextId:id});
      return id;
    };

    /* ---------------- days ---------------- */
    const addDays = (d, n) => { const x = new Date(d + "T12:00:00"); x.setDate(x.getDate() + n); return iso(x); };
    const weekday = d => (new Date(d + "T12:00:00").getDay() + 6) % 7;      // Monday = 0
    const weekStart = d => addDays(d, -weekday(d));
    const daysBetween = (a, b) => Math.round((new Date(b + "T12:00:00") - new Date(a + "T12:00:00")) / 864e5);
    const nowHM = () => { const n = new Date(); return `${String(n.getHours()).padStart(2, "0")}:${String(n.getMinutes()).padStart(2, "0")}`; };

    /* ---------------- habits ---------------- */
    const schedDays = h => {
      const sc = String(h.schedule || "daily");
      if(sc === "daily") return [0, 1, 2, 3, 4, 5, 6];
      if(sc === "weekdays") return [0, 1, 2, 3, 4];
      if(sc.startsWith("days:")) return sc.slice(5).split(",").filter(Boolean).map(Number);
      return Array.isArray(h.days) ? h.days : [0, 1, 2, 3, 4, 5, 6];
    };
    const pausedOn = (h, d) => Boolean(h.paused_from && h.paused_from <= d);
    const startedBy = (h, d) => !h.created || h.created <= d;
    const owedOn = (h, d) => startedBy(h, d) && !pausedOn(h, d) && schedDays(h).includes(weekday(d));
    const doneOn = (h, d) => Boolean(X.habitLog[d]?.[h.id]);
    const setDone = (h, d, on) => {
      X.habitLog[d] = X.habitLog[d] || {};
      if(on) X.habitLog[d][h.id] = true; else delete X.habitLog[d][h.id];
    };
    const habitView = (h, d) => {
      h.category = h.cat = h.cat || h.category || "target";
      return Object.assign(h, {due:owedOn(h, d), done:doneOn(h, d), paused:pausedOn(h, d),
        pause_from:h.paused_from && h.paused_from > d ? h.paused_from : null,
        days:schedDays(h), scored:h.system_key !== "prayer", source:"personal",
        protected:Boolean(h.system_key), qty:X.habitQty[d]?.[h.id] || 0,
        minimal:Boolean(h.target_qty && !doneOn(h, d) && h.min_qty && (X.habitQty[d]?.[h.id] || 0) >= h.min_qty)});
    };
    const written = d => Object.values(X.journal[d]?.answers || {}).some(v => String(v || "").trim());
    const prayerOf = d => X.prayers[d] || {};
    const prayed = d => Object.values(prayerOf(d)).filter(v => v && v !== "missed").length;
    const prayerPoints = d => Object.values(prayerOf(d)).reduce((n, v) => n + ({jamaat:1, on_time:1, qaza:.5}[v] || 0), 0);

    /* The rituals tick themselves, as on the server: the journal when an
       answer is written, prayer when all five are in, wake-up when said in time. */
    const syncRituals = d => {
      DB.habits.forEach(h => {
        if(h.system_key === "journal") setDone(h, d, written(d));
        if(h.system_key === "prayer") setDone(h, d, prayed(d) >= 5 || Boolean(X.excused[d]));
        if(h.system_key === "wakeup") setDone(h, d, Boolean(X.wake[d]?.done));
      });
    };

    /* ---------------- one day's result ----------------
       The server's rule: a weighted mean of the parts that exist that day
       (tasks 40, habits 25, prayer 15 — groups have no part here). */
    const PRIO = {high:3, medium:2, low:1};
    function score(d){
      const planned = DB.tasks.filter(x => x.deadline === d);
      const total = planned.reduce((n, x) => n + (PRIO[x.priority] || 2), 0);
      const earned = planned.filter(x => x.status === "done").reduce((n, x) => n + (PRIO[x.priority] || 2), 0);
      const worked = DB.tasks.filter(x => x.status === "done" && x.done_on === d).length;
      const owed = DB.habits.filter(h => h.system_key !== "prayer" && owedOn(h, d));
      const hDone = owed.filter(h => doneOn(h, d)).length;
      let habitPct = null;
      if(owed.length){
        let sum = 0, weight = 0;
        [["non_negotiable", 50], ["target", 30], ["bonus", 20]].forEach(([c, w]) => {
          const rows = owed.filter(h => (h.cat || h.category) === c);
          if(rows.length){ sum += w * rows.filter(h => doneOn(h, d)).length / rows.length; weight += w; }
        });
        habitPct = Math.round(sum / weight * 100);
      }
      const prayerOn = DB.me.modules?.prayer && !X.excused[d];
      const prayerPct = prayerOn && (d === TODAY || Object.keys(prayerOf(d)).length || d >= X.since)
        ? Math.round(prayerPoints(d) / 5 * 100) : null;
      const taskPct = total ? Math.round(earned / total * 100) : null;
      const parts = {tasks:taskPct, habits:habitPct, prayer:prayerOn ? prayerPct : null};
      const weights = {tasks:40, habits:25, prayer:15};
      let num = 0, den = 0;
      Object.entries(parts).forEach(([k, v]) => { if(v !== null){ num += v * weights[k]; den += weights[k]; } });
      return {day:d, overall:den ? Math.round(num / den) : null, measured:den > 0, ...parts,
              task_points:{earned, total}, tasks_done:planned.filter(x => x.status === "done").length,
              tasks_total:planned.length, worked, habits_done:hDone, habits_total:owed.length,
              prayer_performed:prayed(d), prayer_score:prayerPoints(d), excused:Boolean(X.excused[d]),
              weights:Object.fromEntries(Object.entries(weights).filter(([k]) => parts[k] !== null))};
    }
    const recorded = () => { const out = []; for(let d = X.since; d <= TODAY; d = addDays(d, 1)) out.push(d); return out; };

    /* Consecutive days at 60 or more, today included once it gets there. */
    function streaks(){
      const days = recorded().map(score);
      let best = 0, run = 0;
      days.forEach(s => { if(!s.measured) return; run = s.overall >= 60 ? run + 1 : 0; best = Math.max(best, run); });
      let current = 0;
      for(let i = days.length - 1; i >= 0; i--){
        const s = days[i];
        if(!s.measured) continue;
        if(s.overall >= 60) current++;
        else if(s.day === TODAY) continue;   // today is not over
        else break;
      }
      return {current, best, perfect:days.filter(s => s.measured && s.overall >= 90).length,
              scored:days.filter(s => s.measured).length};
    }
    function habitStreak(){
      let n = 0;
      for(let d = TODAY; d >= X.since; d = addDays(d, -1)){
        const owed = DB.habits.filter(h => h.system_key !== "prayer" && owedOn(h, d));
        if(!owed.length) continue;
        const all = owed.every(h => doneOn(h, d));
        if(all) n++; else if(d === TODAY) continue; else break;
      }
      return n;
    }
    /* XP by the server's price list, the daily cap included. */
    function xpOn(d){
      const s = score(d);
      const base = Math.min(120, 10 * s.worked + 5 * s.habits_done + (s.prayer_performed >= 5 ? 10 : 0)
                   + (written(d) ? 5 : 0) + 10 * X.focus.filter(f => f.done_on === d).length);
      return base + (s.measured && s.overall >= 90 ? 25 : 0);
    }
    const LEVELS = [[0, "starter", "I"], [500, "builder", "II"], [1500, "operator", "III"], [3500, "architect", "IV"],
                    [7000, "commander", "V"], [15000, "elite", "VI"], [30000, "master", "VII"]];
    function level(xp){
      let i = 0; LEVELS.forEach(([t], k) => { if(xp >= t) i = k; });
      const [t, key, numeral] = LEVELS[i], nxt = LEVELS[i + 1];
      return {key, number:i + 1, numeral, xp, current_threshold:t, next_threshold:nxt ? nxt[0] : null,
              next_key:nxt ? nxt[1] : null, remaining:nxt ? nxt[0] - xp : 0,
              progress:nxt ? (xp - t) / (nxt[0] - t) : 1};
    }
    const grade = v => v >= 90 ? "S" : v >= 80 ? "A" : v >= 70 ? "B" : v >= 60 ? "C" : v >= 40 ? "D" : "E";

    /* Qadam: one step per kind of good thing done on a day (the server's rule). */
    const STEP_LEVELS = [["starter", 0], ["builder", 26], ["discipline", 51], ["steady", 101], ["master", 201]];
    function steps(){
      let total = 0;
      recorded().forEach(d => {
        const s = score(d);
        total += (s.worked > 0) + (s.habits_done > 0) + (s.prayer_performed > 0) + (written(d) ? 1 : 0);
      });
      total += X.focus.filter(f => f.done && !f.carried).length;
      const s = score(TODAY), wk = X.focus.filter(f => f.week === weekStart(TODAY) && !f.carried);
      const checks = [
        {key:"tasks", done:s.worked, total:s.tasks_total, planned_done:s.tasks_done,
         complete:s.tasks_total > 0 && s.tasks_done >= s.tasks_total},
        {key:"habits", done:s.habits_done, total:s.habits_total,
         complete:s.habits_total > 0 && s.habits_done >= s.habits_total}];
      if(DB.me.modules?.prayer) checks.push({key:"prayer", done:s.prayer_performed, total:5, complete:s.prayer_performed >= 5});
      if(DB.me.modules?.journal) checks.push({key:"journal", done:written(TODAY) ? 1 : 0, total:1, complete:written(TODAY)});
      checks.push({key:"goal", done:wk.filter(f => f.done).length, total:wk.length, empty:!wk.length,
                   complete:wk.length > 0 && wk.every(f => f.done)});
      checks.forEach(c => { c.ok = c.done > 0; c.empty = Boolean(c.empty); });
      let idx = 0; STEP_LEVELS.forEach(([, low], i) => { if(total >= low) idx = i; });
      return {total, level:idx + 1, level_key:STEP_LEVELS[idx][0], next:STEP_LEVELS[idx + 1]?.[1] ?? null,
              levels:STEP_LEVELS.map(([k, low], i) => ({key:k, min:low, max:STEP_LEVELS[i + 1] ? STEP_LEVELS[i + 1][1] - 1 : null})),
              today:checks, today_done:checks.filter(c => !c.empty && c.ok).length,
              today_counted:checks.filter(c => !c.empty).length, freeze:0};
    }

    /* ---------------- keeping things current ---------------- */
    function refresh(){
      DB.tasks.forEach(x => { x.overdue = x.status !== "done" && Boolean(x.deadline) && x.deadline < TODAY; });
      DEBTS.forEach(d => { d.overdue = !d.settled && Boolean(d.due) && d.due < TODAY; });
      syncRituals(TODAY);
      DB.habits.forEach(h => habitView(h, TODAY));
    }
    refresh();
    save();

    /* ---------------- reads ---------------- */
    const original = path => (ROUTES.find(([re]) => re.test(path)) || [null, () => ({})])[1];
    const base = {
      home:original("/api/home"), habits:original("/api/habits"), journal:original("/api/journal"),
      money:original("/api/money"), tasks:original("/api/tasks"), stats:original("/api/stats"),
      people:original("/api/people"), prayers:original("/api/prayers"),
    };
    const projectsOut = () => X.projects.map(p => {
      const rows = DB.tasks.filter(x => x.project_id === p.id);
      const done = rows.filter(x => x.status === "done").length;
      return {...p, tasks_total:rows.length, tasks_done:done, tasks_open:rows.length - done,
              progress:rows.length ? Math.round(done / rows.length * 100) : 0};
    });
    const countdownsOut = () => X.countdowns.filter(c => c.date >= TODAY)
      .map(c => ({...c, days_left:daysBetween(TODAY, c.date)})).sort((a, b) => a.days_left - b.days_left);
    const birthdaysOut = () => {
      const own = X.birthdays.map(b => ({...b}));
      return own.map(b => {
        let next = `${TODAY.slice(0, 4)}-${b.birth_date.slice(5)}`;
        if(next < TODAY) next = `${Number(TODAY.slice(0, 4)) + 1}-${b.birth_date.slice(5)}`;
        return {...b, next, days_left:daysBetween(TODAY, next), turning:Number(next.slice(0, 4)) - Number(b.birth_date.slice(0, 4))};
      }).sort((a, b) => a.days_left - b.days_left);
    };
    const focusOut = () => {
      const wk = weekStart(TODAY);
      const goals = Object.fromEntries(goalRows.map(g => [g.id, g]));
      const rows = X.focus.filter(f => f.week === wk).map(f => ({id:f.id, slot:f.slot, title:f.title,
        carried:Boolean(f.carried), carries:f.carries || 0, priority:f.priority || "medium",
        primary:f.slot === 1, done:Boolean(f.done), task_id:null, task_title:null,
        goal_id:goals[f.goal_id] ? f.goal_id : null, goal_title:goals[f.goal_id]?.title || null}));
      const live = rows.filter(r => !r.carried);
      return {focus:live, max:3, week:{primary:live.find(r => r.slot === 1) || null,
        supporting:live.filter(r => r.slot !== 1), carried:rows.filter(r => r.carried),
        slots_free:Math.max(3 - live.length, 0), done:live.filter(r => r.done).length, total:live.length}};
    };
    function periodScores(n){
      const out = [];
      for(let i = n - 1; i >= 0; i--){ const d = addDays(TODAY, -i); if(d >= X.since) out.push(score(d)); }
      return out;
    }
    const avgOf = (rows, k) => { const v = rows.map(r => r[k]).filter(x => x !== null && x !== undefined);
      return v.length ? Math.round(v.reduce((a, b) => a + b, 0) / v.length) : null; };
    function statsOut(period){
      const s = score(TODAY), st = streaks();
      let series;
      if(period === "year"){
        const y = TODAY.slice(0, 4);
        series = Array.from({length:12}, (_, i) => {
          const m = `${y}-${String(i + 1).padStart(2, "0")}`;
          const rows = recorded().filter(d => d.startsWith(m)).map(score).filter(r => r.measured);
          return rows.length ? {label:String(i + 1), day:`${m}-01`, overall:avgOf(rows, "overall"),
            tasks:avgOf(rows, "tasks") ?? 0, habits:avgOf(rows, "habits") ?? 0, team:0, prayer:avgOf(rows, "prayer") ?? 0} : null;
        }).filter(Boolean);
      } else {
        const n = period === "month" ? 30 : 7;
        series = periodScores(n).map(r => ({label:String(Number(r.day.slice(8))), day:r.day, overall:r.overall ?? 0,
          tasks:r.tasks ?? 0, habits:r.habits ?? 0, team:0, prayer:r.prayer ?? 0}));
      }
      const avg = k => series.length ? Math.round(series.reduce((a, p) => a + p[k], 0) / series.length) : null;
      const top = series.reduce((b, p) => !b || p.overall > b.overall ? p : b, null);
      const n = period === "year" ? 365 : period === "month" ? 30 : 7;
      const window = periodScores(n), prev = window.slice(0, -1);
      const delta = k => s[k] !== null && avgOf(prev, k) !== null ? s[k] - avgOf(prev, k) : null;
      const pdays = window.filter(r => r.prayer !== null);
      const marks = pdays.flatMap(r => Object.values(prayerOf(r.day)));
      const performed = marks.filter(v => v && v !== "missed").length;
      const yesterday = score(addDays(TODAY, -1));
      return {period,
        today:{overall:s.overall ?? 0, measured:s.measured, yesterday:addDays(TODAY, -1) >= X.since && yesterday.measured ? yesterday.overall : null,
               tasks:s.tasks, habits:s.habits, team:null, prayer:s.prayer, weights:s.weights,
               task_points:s.task_points, prayer_score:s.prayer_score, prayer_max:5,
               prayer_performed:s.prayer_performed, prayer_required:5, streak:st.current},
        deltas:{tasks:delta("tasks"), habits:delta("habits"), team:null, prayer:delta("prayer")},
        series, averages:{overall:avg("overall"), tasks:avg("tasks"), habits:avg("habits"), team:null, prayer:avg("prayer")},
        best_day:top && top.overall > 0 ? {day:top.day, overall:top.overall} : null,
        prayer_detail:{full_days:pdays.filter(r => r.prayer_performed >= 5).length, days:pdays.length,
          on_time_percent:performed ? Math.round(marks.filter(v => v === "jamaat" || v === "on_time").length / performed * 100) : 0,
          jamaat:marks.filter(v => v === "jamaat").length, qaza:marks.filter(v => v === "qaza").length,
          missed:pdays.length * 5 - performed,
          consistency:pdays.length ? Math.round(pdays.filter(r => r.prayer_performed >= 5).length / pdays.length * 100) : 0}};
    }
    function windowOf(n){
      const now = periodScores(n).filter(r => r.measured), before = [];
      for(let i = 2 * n - 1; i >= n; i--){ const d = addDays(TODAY, -i); if(d >= X.since){ const r = score(d); if(r.measured) before.push(r); } }
      const a = avgOf(now, "overall"), b = avgOf(before, "overall");
      return {overall:a ?? 0, delta:a !== null && b !== null ? a - b : null, measured:a !== null};
    }

    const OVR = [
      [/^\/api\/me$/, () => DB.me],
      [/^\/api\/home$/, q => {
        const h = base.home(q), s = score(TODAY), st = streaks();
        const y = score(addDays(TODAY, -1));
        return {...h, name:DB.me.first_name || "", team_today:[], countdowns:countdownsOut(),
          overall:{value:s.overall ?? 0, yesterday:addDays(TODAY, -1) >= X.since && y.measured ? y.overall : null,
                   measured:s.measured, components:{tasks:s.tasks, habits:s.habits, team:null, prayer:s.prayer}},
          counts:{...h.counts, team:{done:0, total:0},
                  prayer:{done:s.prayer_performed, total:5, excused:s.excused, owed:Boolean(DB.me.modules?.prayer)}},
          streak:st.current, prayer:{performed:s.prayer_performed, required:5, excused:s.excused}};
      }],
      [/^\/api\/summary$/, () => {
        const s = score(TODAY);
        return {today:{overall:s.overall ?? 0, measured:s.measured, tasks_done:s.tasks_done, tasks_total:s.tasks_total,
          habits_done:s.habits_done, habits_total:s.habits_total, team_done:0, team_total:0,
          prayer_performed:s.prayer_performed, prayer_required:5},
          windows:{day:windowOf(1), week:windowOf(7), month:windowOf(30)}};
      }],
      [/^\/api\/overall$/, () => {
        const s = score(TODAY);
        const parts = [{key:"tasks", percent:s.tasks, done:s.tasks_done, total:s.tasks_total},
                       {key:"habits", percent:s.habits, done:s.habits_done, total:s.habits_total},
                       {key:"prayer", percent:s.prayer, done:s.prayer_performed, total:5}].filter(p => p.percent !== null);
        return {day:TODAY, value:s.overall ?? 0, measured:s.measured, counted:parts.map(p => p.key), parts,
          weights:s.weights, nominal_weights:{tasks:40, habits:25, team:20, prayer:15},
          task_priority_weights:PRIO, rule:"weighted_mean_of_available", formula:3};
      }],
      [/^\/api\/stats$/, q => statsOut(q.get("period") || "week")],
      [/^\/api\/progress\/me$/, () => {
        const s = score(TODAY), st = streaks();
        const xp = 40 + recorded().reduce((n, d) => n + xpOn(d), 0);
        return {level:level(xp),
          rank:{eligible:false, days_remaining:null, global:null, top_percent:null, users:0, offline:true},
          daily:{grade:s.measured ? grade(s.overall) : null, score:s.measured ? s.overall : null, measured:s.measured,
                 perfect_day:s.measured && s.overall >= 90, to_perfect:Math.max(90 - (s.overall || 0), 0),
                 breakdown:{tasks:s.tasks, habits:s.habits, team:null, prayer:s.prayer},
                 weights:{tasks:0.4, habits:0.25, team:0.2, prayer:0.15}},
          xp:{total:xp, today:xpOn(TODAY), cap:120},
          streak:{current:st.current, best:st.best, recovery_remaining:0, threshold:60},
          perfect_days:st.perfect, scored_days:st.scored, steps:steps()};
      }],
      [/^\/api\/coins$/, () => ({balance:0, earned:0, spent:0, today:0, daily_cap:0, xp_per_coin:10, history:[], items:[]})],
      [/^\/api\/referrals\/me$/, () => ({configured:false})],
      [/^\/api\/birthdays$/, () => ({birthdays:birthdaysOut()})],
      [/^\/api\/countdowns$/, () => ({countdowns:countdownsOut()})],
      [/^\/api\/focus$/, focusOut],
      [/^\/api\/projects$/, () => ({projects:projectsOut()})],
      [/^\/api\/projects\/(\d+)\/tasks$/, (q, m) => {
        const p = projectsOut().find(x => x.id === Number(m[1]));
        return p ? {project:p, tasks:DB.tasks.filter(x => x.project_id === p.id)} : {__status:404, detail:"not_found"};
      }],
      [/^\/api\/tasks\/done$/, () => {
        const done = DB.tasks.filter(x => x.status === "done").sort((a, b) => (b.done_on || "").localeCompare(a.done_on || ""));
        const wk = addDays(TODAY, -6);
        return {groups:{today:done.filter(x => x.done_on === TODAY),
          week:done.filter(x => x.done_on && x.done_on < TODAY && x.done_on >= wk),
          earlier:done.filter(x => !x.done_on || x.done_on < wk)}};
      }],
      [/^\/api\/habits\/(\d+)\/history$/, (q, m) => {
        const h = DB.habits.find(x => x.id === Number(m[1]));
        if(!h) return {__status:404, detail:"not_found"};
        const grid = Array.from({length:30}, (_, i) => { const d = addDays(TODAY, i - 29);
          return {day:d, due:d >= (h.created || X.since) && owedOn(h, d), done:doneOn(h, d)}; });
        const count = list => [list.filter(g => g.done).length, list.filter(g => g.due).length];
        const [d30, u30] = count(grid), [d7, u7] = count(grid.slice(-7));
        let streak = 0;
        for(let i = grid.length - 1; i >= 0; i--){ if(!grid[i].due) continue; if(grid[i].done) streak++; else if(i < 29) break; }
        return {...habitView(h, TODAY), grid, streak, percent:u30 ? Math.round(d30 / u30 * 100) : 0,
                last7_done:d7, last7_due:u7, last30_done:d30, last30_due:u30};
      }],
      [/^\/api\/habits\/presets$/, () => ({presets:PRESETS.map(([key, category, name, system]) => {
        const h = DB.habits.find(x => x.preset_key === key || (system && x.system_key === key));
        return {key, category, name, system, added:Boolean(h), habit_id:h ? h.id : null};
      })})],
      [/^\/api\/habits$/, q => {
        const d = q.get("day") || TODAY;
        syncRituals(d);
        DB.habits.forEach(h => habitView(h, d));
        const out = base.habits(q);
        const target = X.wakeTarget || "05:00";
        const wake = DB.habits.find(h => h.system_key === "wakeup");
        return {...out, streak:habitStreak(), team_habits:[], teams:[],
          wake:wake ? {habit_id:wake.id, target:wake.target_time || target, deadline:addHour(wake.target_time || target),
            now:nowHM(), logged:Boolean(X.wake[TODAY]), done:Boolean(X.wake[TODAY]?.done),
            late:nowHM() > addHour(wake.target_time || target), at:X.wake[TODAY]?.at || null} : null};
      }],
      [/^\/api\/prayers$/, q => prayersOut(q.get("day") || TODAY)],
      [/^\/api\/journal$/, q => {
        const d = q.get("day");
        const Q = {uz:["Bugun nimalarga erishdim?", "Nima uchun shukr qilaman?", "Qaysi muammoga duch keldim?",
                       "Bugun nima o'rgandim?", "Ertaga eng muhim ish nima?"],
                   ru:["Чего я достиг сегодня?", "За что я благодарен?", "С какой проблемой столкнулся?",
                       "Чему я научился сегодня?", "Главная задача на завтра?"],
                   en:["What did I accomplish today?", "What am I grateful for?", "What problem did I face?",
                       "What did I learn today?", "What is tomorrow's most important task?"]}[DB.me.language || LANG]
                  || ["What did I accomplish today?", "What am I grateful for?", "What problem did I face?",
                      "What did I learn today?", "What is tomorrow's most important task?"];
        const payload = {questions:["wins", "gratitude", "problem", "lesson", "tomorrow"].map((id, i) => ({id, text:Q[i]})),
                         moods:["awful", "low", "ok", "good", "great"], total:5};
        if(d) return {...payload, entry:journalOut(d)};
        return {...payload, entries:Object.keys(X.journal).sort().reverse().map(journalOut).filter(Boolean)};
      }],
      [/^\/api\/review$/, () => {
        const wk = weekStart(TODAY), rows = [];
        for(let d = wk; d <= TODAY; d = addDays(d, 1)) if(d >= X.since) rows.push(score(d));
        const f = X.focus.filter(x => x.week === wk && !x.carried);
        const saved = X.reviews[wk];
        return {week_start:wk, tasks_done:DB.tasks.filter(x => x.status === "done" && x.done_on >= wk).length,
          tasks_overdue:DB.tasks.filter(x => x.overdue).length,
          habit_pct:avgOf(rows, "habits") ?? 0, prayer_pct:avgOf(rows, "prayer") ?? 0,
          task_pct:avgOf(rows, "tasks") ?? 0, overall_pct:avgOf(rows.filter(r => r.measured), "overall") ?? 0,
          focus:f.map(x => ({id:x.id, title:x.title, done:Boolean(x.done)})), focus_done:f.filter(x => x.done).length,
          is_week_end:weekday(TODAY) >= 4, answers:saved || {went_well:"", blocked:"", next_focus:""},
          saved:Boolean(saved), suggestions:[]};
      }],
      [/^\/api\/progress\/achievements$/, () => {
        const st = streaks(), done = DB.tasks.filter(x => x.status === "done").length;
        const early = Object.values(X.wake).filter(w => w.done).length;
        const rows = [["first_step", done > 0 ? 1 : 0, 1], ["perfect_day", st.perfect ? 1 : 0, 1],
          ["consistent", st.best, 7], ["disciplined", st.best, 30], ["century", done, 100],
          ["early_riser", early, 30], ["focused", X.focus.filter(x => x.done).length, 10]];
        return {achievements:rows.map(([key, progress, target]) => ({key, progress:Math.min(progress, target), target,
          unlocked:progress >= target, unlocked_at:null}))};
      }],
      [/^\/api\/money$/, q => {
        const m = base.money(q);
        return {...m, categories:m.categories.map(c => {
          const limit = Number(X.budgets[c.id]) || 0;
          return {...c, limit, percent:limit ? Math.min(100, Math.round(c.spent / limit * 100)) : null,
                  over:Boolean(limit) && c.spent > limit};
        })};
      }],
      [/^\/api\/money\/year$/, q => {
        const y = Number(q.get("year")) || Number(TODAY.slice(0, 4));
        const rows = DB.money.filter(e => String(e.day).startsWith(String(y)));
        const months = Array.from({length:12}, (_, i) => {
          const m = `${y}-${String(i + 1).padStart(2, "0")}`;
          const sum = k => rows.filter(e => e.kind === k && String(e.day).startsWith(m)).reduce((n, e) => n + e.amount, 0);
          return {month:i + 1, income:sum("income"), expense:sum("expense")};
        });
        const income = months.reduce((n, x) => n + x.income, 0), expense = months.reduce((n, x) => n + x.expense, 0);
        const cats = {};
        rows.filter(e => e.kind === "expense").forEach(e => { cats[e.category] = (cats[e.category] || 0) + e.amount; });
        return {year:y, months, income, expense, net:income - expense,
                categories:Object.entries(cats).map(([id, spent]) => ({id, spent})).sort((a, b) => b.spent - a.spent)};
      }],
      [/^\/api\/calendar$/, q => {
        const y = Number(q.get("year")) || Number(TODAY.slice(0, 4)), mo = Number(q.get("month")) || Number(TODAY.slice(5, 7));
        const key = `${y}-${String(mo).padStart(2, "0")}`;
        const first = (new Date(y, mo - 1, 1).getDay() + 6) % 7, dim = new Date(y, mo, 0).getDate();
        const ev = {};
        const add = (d, kind, title) => { if(d && d.startsWith(key)) (ev[d] ||= []).push({kind, title}); };
        DB.tasks.filter(x => x.status !== "done").forEach(x => add(x.deadline, "task", x.title));
        X.projects.filter(p => !p.archived).forEach(p => add(p.deadline, "project", p.name));
        X.countdowns.forEach(c => add(c.date, "countdown", c.title));
        X.birthdays.forEach(b => add(`${y}-${b.birth_date.slice(5)}`, "birthday", b.person_name));
        PEOPLE.filter(p => p.bday && !p.archived).forEach(p => add(`${y}-${p.bday.slice(5)}`, "birthday", p.first_name));
        return {year:y, month:mo, first_weekday:first, days_in_month:dim, today:TODAY, events:ev};
      }],
      [/^\/api\/fresh-start$/, () => {
        const late = DB.tasks.filter(x => x.status !== "done" && x.deadline && x.deadline < TODAY);
        return {mode:"focus", today:late.slice(0, 3).map(x => ({id:x.id, title:x.title, to:TODAY})),
                later:late.slice(3).map((x, i) => ({id:x.id, title:x.title, to:addDays(TODAY, i + 1)})),
                dropped:[], total:late.length};
      }],
      [/^\/api\/export$/, () => snapshot()],
      [/^\/api\/stats\/(csv|export)$/, q => {
        const n = q.get("period") === "year" ? 365 : q.get("period") === "week" ? 7 : 30;
        return "day,overall,tasks,habits,prayer\n" + periodScores(n)
          .map(r => [r.day, r.overall ?? "", r.tasks ?? "", r.habits ?? "", r.prayer ?? ""].join(",")).join("\n") + "\n";
      }],
      [/^\/api\/agent\/now$/, () => ({source:"none", pick:null})],
      [/^\/api\/app\/notifications$/, () => ({items:[], unread:0})],
    ];
    function addHour(hm){ const [h, m] = String(hm).split(":").map(Number); return `${String(Math.min(23, h + 1)).padStart(2, "0")}:${String(m || 0).padStart(2, "0")}`; }
    function journalOut(d){
      const j = X.journal[d];
      if(!j) return null;
      const answered = Object.values(j.answers || {}).filter(v => String(v || "").trim()).length;
      return {day:d, text:"", mood:j.mood || null, updated_at:j.updated_at || null, answers:j.answers || {},
              answered, total:5, written:answered > 0, complete:answered >= 5};
    }
    function prayersOut(d){
      const p = prayerOf(d);
      return {prayers:p, statuses:["jamaat", "on_time", "qaza", "missed"], performed:prayed(d), required:5,
              score:prayerPoints(d), max:5, complete:prayed(d) >= 5, excused:Boolean(X.excused[d]), day:d};
    }
    const snapshot = () => current();
    ROUTES.unshift(...OVR);

    /* ---------------- writes ---------------- */
    const TASK_FIELDS = ["title", "description", "deadline", "due_time", "remind_before", "recurrence", "priority",
                         "timer_minutes", "project_id"];
    const projectName = id => X.projects.find(p => p.id === Number(id))?.name || null;
    const nextDue = (d, rule) => {
      const r = String(rule || "");
      if(r === "daily") return addDays(d, 1);
      if(r === "weekdays"){ let n = addDays(d, 1); while(weekday(n) > 4) n = addDays(n, 1); return n; }
      if(r === "weekly") return addDays(d, 7);
      if(r === "monthly"){ const x = new Date(d + "T12:00:00"); x.setMonth(x.getMonth() + 1); return iso(x); }
      if(r === "yearly"){ const x = new Date(d + "T12:00:00"); x.setFullYear(x.getFullYear() + 1); return iso(x); }
      return null;
    };
    const bad = (status, detail) => ({__status:status, detail});
    const REPEAT = [[/\bhar\s+kun(?:i|ga)?\b|каждый\s+день|ежедневно|\bevery\s+day\b|\bdaily\b/i, "daily"],
                    [/\bish\s+kunlari\b|по\s+будням|\bweekdays\b/i, "weekdays"],
                    [/\bhar\s+hafta(?:da)?\b|каждую\s+неделю|еженедельно|\bevery\s+week\b|\bweekly\b/i, "weekly"],
                    [/\bhar\s+oy(?:da)?\b|каждый\s+месяц|ежемесячно|\bevery\s+month\b|\bmonthly\b/i, "monthly"]];
    const MODULE_HABIT = {wake:["wakeup", "non_negotiable"], prayer:["prayer", "non_negotiable"], journal:["journal", "non_negotiable"]};
    const RITUAL_NAME = {wakeup:{uz:"Erta turish", en:"Wake up early", ru:"Ранний подъём"},
                         prayer:{uz:"5 vaqt namoz", en:"5 daily prayers", ru:"5 намазов"},
                         journal:{uz:"Kun xulosasi", en:"Day summary", ru:"Итоги дня"}};
    function setModules(list){
      const on = new Set(list || []);
      DB.me.modules = {wake:on.has("wake"), prayer:on.has("prayer"), journal:on.has("journal")};
      Object.entries(MODULE_HABIT).forEach(([mod, [key, cat]]) => {
        const h = DB.habits.find(x => x.system_key === key);
        if(on.has(mod) && !h) DB.habits.unshift({id:newId(), name:RITUAL_NAME[key][DB.me.language || LANG] || RITUAL_NAME[key].uz,
          system_key:key, cat, category:cat, schedule:"daily", created:TODAY,
          target_time:key === "wakeup" ? (X.wakeTarget || "05:00") : null});
        if(!on.has(mod) && h) DB.habits.splice(DB.habits.indexOf(h), 1);
      });
    }

    function write(method, path, body, query){
      let m;
      body = body || {};
      /* --- account --- */
      if(path === "/api/onboarding"){
        const name = String(body.first_name || "").trim().replace(/\s+/g, " ").slice(0, 60);
        if(!name) return bad(422, "empty_name");
        DB.me.first_name = name; DB.me.name = name;
        if(["uz", "ru", "en"].includes(body.language)) DB.me.language = body.language;
        if(body.wake_time) X.wakeTarget = body.wake_time;
        setModules(body.modules);
        const wake = DB.habits.find(h => h.system_key === "wakeup");
        if(wake && body.wake_time){ wake.target_time = body.wake_time; DB.me.prefs.morning_time = body.wake_time; }
        DB.me.onboarded = true; X.since = TODAY;
        return {ok:true};
      }
      if(path === "/api/modules"){ setModules(body.modules); return {ok:true, modules:DB.me.modules}; }
      if(path === "/api/settings" && body.language && ["uz", "ru", "en"].includes(body.language)){
        DB.me.language = body.language;
        DB.habits.forEach(h => { if(h.system_key && RITUAL_NAME[h.system_key]) h.name = RITUAL_NAME[h.system_key][body.language]; });
      }
      if(path === "/api/account/wipe"){
        // Everything entered goes; the name and settings stay, as on the server.
        DB.tasks = []; DB.money = []; W.accounts = []; W.subs = []; W.transfers = [];
        [TEAMS, goalRows, PEOPLE, DEBTS].forEach(a => { a.length = 0; });
        DB.habits = DB.habits.filter(h => h.system_key);
        Object.assign(X, {since:TODAY, habitLog:{}, habitQty:{}, prayers:{[TODAY]:{}}, excused:{}, journal:{}, wake:{},
                          countdowns:[], projects:[], focus:[], birthdays:[], budgets:{}, reviews:{}});
        DB.prayers = X.prayers[TODAY];
        return {ok:true};
      }
      if(path === "/api/account/delete"){
        try{ localStorage.removeItem(KEY); }catch(_){}
        setTimeout(() => location.reload(), 1500);
        return {ok:true, deleted:true};
      }
      if(path === "/api/promocode/redeem") return bad(404, "not_found");
      if(path === "/api/plans/invoice") return bad(409, "offline");
      if(path === "/api/coins/buy") return bad(409, "not_enough_coins");
      if(path === "/api/feedback") return {ok:true};
      if(path === "/api/export/send") return {ok:true};

      /* --- tasks --- */
      if(path === "/api/quick" || (path === "/api/tasks" && method === "POST")){
        // "Har kuni …", "har oy …": how often, said in words (the server's rule).
        let line = String(body.title || ""), rule = body.recurrence || "";
        if(path === "/api/quick") for(const [re, r] of REPEAT){
          const m = line.match(re);
          if(m){ rule = r; line = (line.slice(0, m.index) + line.slice(m.index + m[0].length)).replace(/\s+/g, " ").trim(); break; }
        }
        const p = path === "/api/quick" ? P.parseLine(line) : {title:body.title, deadline:body.deadline ?? null,
                                                               due_time:body.due_time || null};
        if(path === "/api/quick" && body.deadline) p.deadline = body.deadline;
        const title = String(p.title || "").trim();
        if(!title) return bad(422, "empty_title");
        const row = task(newId(), title, {deadline:path === "/api/quick" ? (p.deadline || TODAY) : p.deadline,
          due_time:p.due_time, priority:body.priority || "medium", description:body.description || "",
          remind_before:body.remind_before ?? null, recurrence:rule,
          timer_minutes:body.timer_minutes ?? null, project_id:body.project_id || null,
          project:projectName(body.project_id), created:TODAY});
        DB.tasks.push(row);
        return {ok:true, ...row};
      }
      if((m = path.match(/^\/api\/tasks\/(\d+)$/))){
        const t = DB.tasks.find(x => x.id === Number(m[1]));
        if(!t) return bad(404, "not_found");
        if(method === "DELETE"){ DB.tasks.splice(DB.tasks.indexOf(t), 1); return {ok:true}; }
        if(body.status){
          const done = body.status === "done";
          if(done && t.status !== "done"){
            t.status = "done"; t.done_on = TODAY;
            const next = t.recurrence && nextDue(t.deadline || TODAY, t.recurrence);
            if(next && !t.spawned){
              t.spawned = true;
              const copy = {...t, id:newId(), status:"open", deadline:next, done_on:null, spawned:false, top3:false};
              DB.tasks.push(copy);
            }
          } else if(!done){ t.status = "open"; t.done_on = null; }
          return {ok:true, status:t.status};
        }
        TASK_FIELDS.forEach(k => { if(body[k] !== undefined) t[k] = body[k]; });
        if(body.project_id !== undefined) t.project = projectName(body.project_id);
        return {ok:true, ...t};
      }
      if((m = path.match(/^\/api\/tasks\/(\d+)\/reschedule$/))){
        const t = DB.tasks.find(x => x.id === Number(m[1]));
        if(!t) return bad(404, "not_found");
        t.deadline = {today:TODAY, tomorrow:addDays(TODAY, 1), week:addDays(weekStart(TODAY), 7), none:null}[body.when] ?? t.deadline;
        return {ok:true, deadline:t.deadline};
      }
      if((m = path.match(/^\/api\/tasks\/(\d+)\/top3$/))){
        const t = DB.tasks.find(x => x.id === Number(m[1]));
        if(t) t.top3 = Boolean(body.picked);
        return {ok:true};
      }
      if((m = path.match(/^\/api\/tasks\/(\d+)\/block$/))){
        const t = DB.tasks.find(x => x.id === Number(m[1]));
        if(t){ if(method === "DELETE"){ t.blocked = null; t.blocked_reason = null; }
               else { t.blocked = true; t.blocked_reason = body.reason || ""; t.blocked_until = body.until || null; } }
        return {ok:true};
      }
      if(path.startsWith("/api/fresh-start") && method === "POST"){
        const late = DB.tasks.filter(x => x.status !== "done" && x.deadline && x.deadline < TODAY);
        const drop = new Set(String(query?.get("drop") || "").split(",").filter(Boolean).map(Number));
        late.forEach((x, i) => {
          if(drop.has(x.id)) DB.tasks.splice(DB.tasks.indexOf(x), 1);
          else x.deadline = i < 3 ? TODAY : addDays(TODAY, i - 2);
        });
        return {ok:true, reset_id:null, moved:late.length - drop.size, dropped:drop.size};
      }

      /* --- projects --- */
      if(path === "/api/projects" && method === "POST"){
        const name = String(body.name || "").trim();
        if(!name) return bad(422, "empty_name");
        const p = {id:newId(), name, description:body.description || "", deadline:body.deadline || null,
                   status:"active", archived:false};
        X.projects.push(p);
        return {ok:true, id:p.id};
      }
      if((m = path.match(/^\/api\/projects\/(\d+)$/))){
        const p = X.projects.find(x => x.id === Number(m[1]));
        if(!p) return bad(404, "not_found");
        if(method === "DELETE"){
          X.projects.splice(X.projects.indexOf(p), 1);
          DB.tasks.forEach(x => { if(x.project_id === p.id){ x.project_id = null; x.project = null; } });
          return {ok:true};
        }
        ["name", "description", "deadline", "status", "archived"].forEach(k => { if(body[k] !== undefined) p[k] = body[k]; });
        DB.tasks.forEach(x => { if(x.project_id === p.id) x.project = p.name; });
        return {ok:true};
      }

      /* --- habits --- */
      if(path === "/api/habits" && method === "POST"){
        const name = String(body.name || "").trim();
        if(!name) return bad(422, "empty_name");
        const h = {id:newId(), name, cat:body.category || "target", category:body.category || "target",
                   schedule:body.schedule || "daily", remind_at:body.remind_at || null,
                   timer_minutes:body.timer_minutes ?? null, target_qty:body.target_qty || null,
                   min_qty:body.min_qty || null, unit:body.unit || "", created:TODAY, preset_key:body.preset_key || null};
        DB.habits.push(h);
        return {ok:true, id:h.id};
      }
      if(path === "/api/habits/presets" && method === "POST"){
        const pre = PRESETS.find(([key]) => key === body.key);
        if(!pre) return bad(404, "not_found");
        const [key, category, name, system] = pre;
        if(system){
          const mods = Object.entries(DB.me.modules || {}).filter(([, v]) => v).map(([k]) => k);
          const mod = {wakeup:"wake", prayer:"prayer", journal:"journal"}[key];
          setModules(body.on ? [...new Set([...mods, mod])] : mods.filter(x => x !== mod));
        } else if(body.on){
          if(!DB.habits.some(h => h.preset_key === key))
            DB.habits.push({id:newId(), name, cat:category, category, schedule:"daily", created:TODAY, preset_key:key});
        } else {
          const h = DB.habits.find(x => x.preset_key === key);
          if(h) DB.habits.splice(DB.habits.indexOf(h), 1);
        }
        refresh();
        return OVR.find(([re]) => re.test("/api/habits/presets"))[1]();
      }
      if(path === "/api/habits/reorder"){
        const order = (body.habit_ids || []).map(Number);
        DB.habits.sort((a, b) => (order.indexOf(a.id) + 1 || 1e6) - (order.indexOf(b.id) + 1 || 1e6));
        return {habits:DB.habits.map(h => habitView(h, TODAY))};
      }
      if((m = path.match(/^\/api\/habits\/(\d+)\/toggle$/))){
        const h = DB.habits.find(x => x.id === Number(m[1]));
        if(!h) return bad(404, "not_found");
        const d = body.day || TODAY;
        const on = body.done === undefined ? !doneOn(h, d) : Boolean(body.done);
        setDone(h, d, on);
        if(h.system_key === "wakeup"){ if(on) X.wake[d] = {at:X.wake[d]?.at || nowHM(), done:true}; else delete X.wake[d]; }
        return {ok:true, done:on};
      }
      if((m = path.match(/^\/api\/habits\/(\d+)\/qty$/))){
        const h = DB.habits.find(x => x.id === Number(m[1]));
        if(!h) return bad(404, "not_found");
        const qty = Math.max(0, Math.round(Number(body.qty) || 0));
        (X.habitQty[TODAY] ||= {})[h.id] = qty;
        setDone(h, TODAY, Boolean(h.target_qty) && qty >= h.target_qty);
        return {ok:true, qty, done:doneOn(h, TODAY)};
      }
      if((m = path.match(/^\/api\/habits\/(\d+)\/pause$/))){
        const h = DB.habits.find(x => x.id === Number(m[1]));
        if(!h) return bad(404, "not_found");
        h.paused_from = body.paused ? (body.from || TODAY) : null;
        return {ok:true, paused:Boolean(body.paused)};
      }
      if((m = path.match(/^\/api\/habits\/(\d+)$/))){
        const h = DB.habits.find(x => x.id === Number(m[1]));
        if(!h) return bad(404, "not_found");
        if(method === "DELETE"){
          if(h.system_key){
            const mod = {wakeup:"wake", prayer:"prayer", journal:"journal"}[h.system_key];
            setModules(Object.entries(DB.me.modules || {}).filter(([k, v]) => v && k !== mod).map(([k]) => k));
          } else DB.habits.splice(DB.habits.indexOf(h), 1);
          return {ok:true};
        }
        if(body.name !== undefined && String(body.name).trim()) h.name = String(body.name).trim();
        if(body.category) h.cat = h.category = body.category;
        if(body.schedule) h.schedule = body.schedule;
        ["remind_at", "timer_minutes", "target_qty", "min_qty", "unit", "target_time"].forEach(k => { if(body[k] !== undefined) h[k] = body[k]; });
        if(body.paused !== undefined) h.paused_from = body.paused ? (body.from || TODAY) : null;
        return {ok:true};
      }
      if(path === "/api/wakeup"){
        const at = body.at || nowHM();
        const h = DB.habits.find(x => x.system_key === "wakeup");
        const deadline = addHour(h?.target_time || X.wakeTarget || "05:00");
        X.wake[TODAY] = {at, done:at <= deadline};
        return {ok:true, at, done:X.wake[TODAY].done, late:!X.wake[TODAY].done};
      }

      /* --- prayer --- */
      if(path === "/api/prayers" && body.prayer){
        const d = body.day || TODAY;
        (X.prayers[d] ||= {})[body.prayer] = body.status || null;
        if(!body.status) delete X.prayers[d][body.prayer];
        syncRituals(d);
        return prayersOut(d);
      }
      if(path === "/api/prayers/clear"){
        const d = body.day || TODAY;
        if(body.prayer) delete (X.prayers[d] ||= {})[body.prayer]; else X.prayers[d] = {};
        if(d === TODAY) DB.prayers = X.prayers[TODAY];
        syncRituals(d);
        return prayersOut(d);
      }
      if(path === "/api/prayers/excused"){
        const d = body.day || TODAY;
        if(body.excused) X.excused[d] = true; else delete X.excused[d];
        syncRituals(d);
        return prayersOut(d);
      }

      /* --- journal --- */
      if(path === "/api/journal" && method === "POST"){
        const d = body.day || TODAY;
        const j = X.journal[d] ||= {answers:{}, mood:null};
        if(body.answers) Object.assign(j.answers, body.answers);
        if(body.mood !== undefined) j.mood = body.mood || null;
        j.updated_at = new Date().toISOString();
        syncRituals(d);
        const out = journalOut(d);
        return {ok:true, day:d, updated_at:j.updated_at, answered:out.answered, total:5, complete:out.complete};
      }
      if((m = path.match(/^\/api\/journal\/(\d{4}-\d{2}-\d{2})$/)) && method === "DELETE"){
        delete X.journal[m[1]]; syncRituals(m[1]); return {ok:true};
      }

      /* --- week goals --- */
      if(path === "/api/focus" && method === "POST"){
        const wk = weekStart(TODAY), live = X.focus.filter(f => f.week === wk && !f.carried);
        if(live.length >= 3) return bad(422, "focus_full");
        const used = new Set(live.map(f => f.slot));
        const slot = [1, 2, 3].find(s => !used.has(s));
        const title = String(body.title || "").trim();
        if(!title) return bad(422, "empty_title");
        const f = {id:newId(), week:wk, slot, title, priority:body.priority || "medium", goal_id:body.goal_id || null, done:false};
        X.focus.push(f);
        return {ok:true, id:f.id};
      }
      if((m = path.match(/^\/api\/focus\/(\d+)(\/(toggle|carry))?$/))){
        const f = X.focus.find(x => x.id === Number(m[1]));
        if(!f) return bad(404, "not_found");
        if(m[3] === "toggle"){ f.done = !f.done; f.done_on = f.done ? TODAY : null; return {ok:true, done:f.done}; }
        if(m[3] === "carry"){
          const wk = addDays(f.week, 7);
          if(X.focus.filter(x => x.week === wk && !x.carried).length >= 3) return bad(422, "focus_full");
          f.carried = true;
          const used = new Set(X.focus.filter(x => x.week === wk && !x.carried).map(x => x.slot));
          const g = {...f, id:newId(), week:wk, carried:false, carries:(f.carries || 0) + 1, done:false,
                     slot:[1, 2, 3].find(s => !used.has(s))};
          X.focus.push(g);
          return {ok:true, id:g.id, week_start:wk};
        }
        if(method === "DELETE"){ X.focus.splice(X.focus.indexOf(f), 1); return {ok:true}; }
        ["title", "priority", "goal_id"].forEach(k => { if(body[k] !== undefined) f[k] = body[k]; });
        if(body.primary){ const cur = X.focus.find(x => x.week === f.week && !x.carried && x.slot === 1 && x !== f);
                          if(cur) cur.slot = f.slot; f.slot = 1; }
        return {ok:true};
      }
      if(path === "/api/review" && method === "POST"){
        const wk = weekStart(TODAY);
        X.reviews[wk] = {went_well:body.went_well || "", blocked:body.blocked || "", next_focus:body.next_focus || ""};
        if(body.make_goal && String(body.next_focus || "").trim()){
          const nw = addDays(wk, 7), used = new Set(X.focus.filter(x => x.week === nw && !x.carried).map(x => x.slot));
          const slot = [1, 2, 3].find(s => !used.has(s));
          if(slot) X.focus.push({id:newId(), week:nw, slot, title:String(body.next_focus).trim(), priority:"high", done:false});
        }
        return {ok:true};
      }

      /* --- dates --- */
      if(path === "/api/countdowns" && method === "POST"){
        const title = String(body.title || "").trim();
        if(!title || !body.date) return bad(422, "countdown_need");
        X.countdowns.push({id:newId(), title, date:body.date, scope:body.scope || "general", item_id:body.item_id || null});
        return {ok:true};
      }
      if((m = path.match(/^\/api\/countdowns\/(\d+)$/))){
        X.countdowns = X.countdowns.filter(c => c.id !== Number(m[1])); return {ok:true};
      }
      if(path === "/api/birthdays" && method === "POST"){
        const name = String(body.person_name || "").trim();
        if(!name || !body.birth_date) return bad(422, "empty_name");
        X.birthdays.push({id:newId(), person_name:name, birth_date:body.birth_date, note:body.note || ""});
        return {ok:true};
      }
      if((m = path.match(/^\/api\/birthdays\/(\d+)$/))){
        X.birthdays = X.birthdays.filter(b => b.id !== Number(m[1])); return {ok:true};
      }

      /* --- money --- */
      if((m = path.match(/^\/api\/money\/budgets\/(\w+)$/))){
        X.budgets[m[1]] = Math.max(0, Number(body.limit) || 0); return {ok:true};
      }
      if((m = path.match(/^\/api\/money\/(\d+)$/)) && method === "PATCH"){
        const e = DB.money.find(x => x.id === Number(m[1]));
        if(!e) return bad(404, "not_found");
        ["kind", "category", "note", "account_id", "day"].forEach(k => { if(body[k] !== undefined) e[k] = body[k]; });
        if(body.amount !== undefined) e.amount = Number(body.amount) || e.amount;
        return {ok:true};
      }
      if(path === "/api/money" && method === "POST" && body.amount){
        DB.money.push({id:newId(), kind:body.kind || "expense", amount:Number(body.amount),
          category:body.category || "other", note:body.note || "", source:"manual",
          day:body.day || TODAY, account_id:body.account_id || null});
        return {ok:true};
      }

      /* --- debts: the server's rules for paying back and reopening --- */
      if((m = path.match(/^\/api\/debts\/(\d+)\/settle$/))){
        const d = DEBTS.find(x => x.id === Number(m[1]));
        if(!d) return bad(404, "not_found");
        const remaining = d.original - d.payments.reduce((n, p) => n + p.amount, 0);
        if(body.settled !== false && body.paid !== undefined && body.paid !== null){
          if(d.settled) return bad(422, "debt_closed");
          const paid = Math.round(Number(String(body.paid).replace(/[^0-9]/g, "")) || 0);
          if(paid <= 0) return bad(422, "bad_amount");
          if(paid > remaining) return bad(422, "paid_exceeds_remaining");
          d.payments.push({id:newId(), amount:paid, day:TODAY});
          if(paid === remaining) d.settled = true;
        } else if(body.settled !== false){
          d.settled = true;
        } else {
          d.settled = false;
          if(d.payments.length && remaining <= 0) d.payments.pop();
        }
        d.paid = d.payments.reduce((n, p) => n + p.amount, 0);
        d.amount = d.settled ? 0 : d.original - d.paid;
        return {ok:true, ...d};
      }
      if((m = path.match(/^\/api\/debts\/(\d+)$/)) && method === "PATCH"){
        const d = DEBTS.find(x => x.id === Number(m[1]));
        if(!d) return bad(404, "not_found");
        ["person", "direction", "note", "due"].forEach(k => { if(body[k] !== undefined) d[k] = body[k]; });
        if(body.amount !== undefined){
          const a = Math.round(Number(String(body.amount).replace(/[^0-9]/g, "")) || 0);
          if(a > 0){ d.original = a; d.amount = d.settled ? 0 : Math.max(0, a - d.paid); }
        }
        return {ok:true};
      }

      /* --- groups: one device, so nobody can be invited --- */
      if(/^\/api\/teams\/(join|\d+\/invite)/.test(path)) return bad(409, "offline");

      /* --- AI and voice need the server --- */
      if(path.startsWith("/api/agent/")) return bad(503, "agent_off");
      return null;
    }

    function mutate(method, path, body, query){
      const mine = write(method, path, body, query);
      const out = mine === null ? P.mutate(method, path, body) : mine;
      // A task added through preview.js's own routes is stamped too.
      DB.tasks.forEach(x => { if(!x.created) x.created = TODAY; });
      refresh();
      if(!save()) console.warn("ErnestOS offline: could not save to this browser's storage");
      return out;
    }

    /* A new day while the page stayed open: start it from a fresh load. */
    document.addEventListener("visibilitychange", () => {
      if(document.visibilityState === "visible" && iso(new Date()) !== TODAY) location.reload();
    });
    window.ErnestOffline = {snapshot, key:KEY, showBackup:(name, text) => showBackup(name, text, LANG)};

    return {mutate, label:{uz:"Oflayn nusxa · ma'lumotlar shu brauzerda saqlanadi · AI, ovoz va eslatmalar serverli ilovada",
                           ru:"Офлайн-копия · данные хранятся в этом браузере · AI, голос и напоминания — в приложении с сервером"}[LANG]
                         || "Offline copy · data is kept in this browser · AI, voice and reminders need the server app"};
  };

  /* Backup and restore. A page inside a viewer may not be allowed to
     download files, so the backup is also shown as text to copy, and a copied
     backup pasted back here restores everything. */
  const COPY = {
    uz:{title:"Zaxira nusxa", hint:"Fayl yuklanmagan bo'lsa, matnni nusxalab xavfsiz joyda saqlang (Telegram «Saqlangan xabarlar», eslatmalar).",
        copy:"Nusxa olish", copied:"Nusxalandi", file:"Faylga saqlash", saved:"Saqlandi", nofile:"Fayl saqlanmadi — matnni nusxalang", restore:"Zaxiradan tiklash",
        rhint:"Avval saqlagan matningizni shu yerga qo'ying. Hozirgi ma'lumotlar uning o'rniga yoziladi.",
        go:"Tiklash", sure:"Ha, almashtirish", bad:"Bu ErnestOS oflayn zaxirasi emas", close:"Yopish"},
    ru:{title:"Резервная копия", hint:"Если файл не скачался, скопируйте текст и сохраните его (Избранное в Telegram, заметки).",
        copy:"Скопировать", copied:"Скопировано", file:"Сохранить файл", saved:"Сохранено", nofile:"Файл не сохранён — скопируйте текст", restore:"Восстановить из копии",
        rhint:"Вставьте сохранённый текст. Текущие данные будут заменены.",
        go:"Восстановить", sure:"Да, заменить", bad:"Это не офлайн-копия ErnestOS", close:"Закрыть"},
    en:{title:"Backup", hint:"If no file was downloaded, copy the text and keep it somewhere safe (Telegram Saved Messages, notes).",
        copy:"Copy", copied:"Copied", file:"Save as file", saved:"Saved", nofile:"Not saved — copy the text instead", restore:"Restore from a backup",
        rhint:"Paste a backup you saved. It replaces what is here now.",
        go:"Restore", sure:"Yes, replace", bad:"This is not an ErnestOS offline backup", close:"Close"}};
  /* Inside the claude.ai viewer a file is offered through its `downloads`
     capability (the viewer confirms); anywhere else, an ordinary download. */
  const viewer = () => Boolean(window.claude && typeof window.claude.use === "function");
  let saver = null;
  const getSaver = () => saver || (saver = viewer()
    ? Promise.resolve(window.claude.use("downloads")).catch(() => null) : Promise.resolve("anchor"));
  function saveFile(name, text){
    return getSaver().then(d => {
      if(d === "anchor"){
        const url = URL.createObjectURL(new Blob([text], {type:"application/json"}));
        const a = document.createElement("a"); a.href = url; a.download = name;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 4000);
        return "saved";
      }
      if(!d) throw {code:"unavailable"};
      return d.save({filename:name, data:text}).then(r => r.status);
    });
  }
  function showBackup(name, text, lang){
    const c = COPY[lang] || COPY.en;
    name = name || "ernestos.json";
    document.getElementById("ernest-backup")?.remove();
    const box = document.createElement("div");
    box.id = "ernest-backup";
    box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true"); box.setAttribute("aria-label", c.title);
    box.style.cssText = "position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.45);display:flex;align-items:flex-end;justify-content:center";
    const field = "width:100%;box-sizing:border-box;height:110px;font:12px/1.4 ui-monospace,monospace;padding:10px;" +
      "border:1px solid var(--border);border-radius:12px;background:var(--surface-2);color:var(--text);resize:vertical";
    const btn = "flex:1;min-height:44px;border-radius:12px;font:600 15px system-ui,sans-serif;cursor:pointer;";
    box.innerHTML = `<div style="width:100%;max-width:520px;max-height:92%;overflow:auto;box-sizing:border-box;
        background:var(--surface);color:var(--text);border-radius:20px 20px 0 0;
        padding:20px max(16px,env(safe-area-inset-right,0px)) calc(20px + env(safe-area-inset-bottom,0px)) max(16px,env(safe-area-inset-left,0px));
        font:15px/1.45 system-ui,sans-serif;display:grid;gap:12px">
      <h3 style="margin:0;font-size:18px">${c.title}</h3>
      <div style="color:var(--text-2);font-size:14px">${c.hint}</div>
      <textarea id="eb-out" readonly style="${field}"></textarea>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button id="eb-file" hidden style="${btn}border:0;background:var(--primary-fill);color:var(--on-primary)">${c.file}</button>
        <button id="eb-copy" style="${btn}border:1px solid var(--border);background:var(--surface-2);color:var(--text)">${c.copy}</button></div>
      <h3 style="margin:8px 0 0;font-size:16px">${c.restore}</h3>
      <div style="color:var(--text-2);font-size:14px">${c.rhint}</div>
      <textarea id="eb-in" style="${field}"></textarea>
      <div id="eb-msg" role="status" style="color:var(--danger,#c0392b);font-size:14px;min-height:1em"></div>
      <div style="display:flex;gap:8px">
        <button id="eb-go" style="${btn}border:1px solid var(--border);background:var(--surface-2);color:var(--text)">${c.go}</button>
        <button id="eb-close" style="${btn}border:1px solid var(--border);background:var(--surface);color:var(--text)">${c.close}</button>
      </div></div>`;
    document.body.appendChild(box);
    const out = box.querySelector("#eb-out"), msg = box.querySelector("#eb-msg");
    out.value = text;
    box.querySelector("#eb-copy").onclick = ev => {
      const done = () => { ev.target.textContent = c.copied; };
      const pick = () => { out.focus(); out.select(); try{ document.execCommand("copy"); done(); }catch(_){} };
      try{ navigator.clipboard.writeText(text).then(done, pick); }catch(_){ pick(); }
    };
    const file = box.querySelector("#eb-file");
    getSaver().then(d => { if(d) file.hidden = false; });
    file.onclick = () => {
      saveFile(name, text).then(() => { file.textContent = c.saved; msg.textContent = ""; },
        e => { if(e?.code !== "declined") msg.textContent = c.nofile; });
    };
    box.querySelector("#eb-close").onclick = () => box.remove();
    box.addEventListener("click", ev => { if(ev.target === box) box.remove(); });
    let armed = false;
    box.querySelector("#eb-go").onclick = ev => {
      let data = null;
      try{ data = JSON.parse(box.querySelector("#eb-in").value); }catch(_){}
      if(!data || data.v !== 1 || data.kind !== "offline-copy" || !Array.isArray(data.tasks)){ msg.textContent = c.bad; armed = false; return; }
      if(!armed){ armed = true; msg.textContent = ""; ev.target.textContent = c.sure; return; }
      try{ localStorage.setItem(KEY, JSON.stringify(data)); }catch(_){ msg.textContent = c.bad; return; }
      location.reload();
    };
  }
})();
