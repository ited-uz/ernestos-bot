/* ErnestOS phone app — the bridge between the Mini App and the phone.

   The Mini App was written for Telegram: it reads `window.Telegram.WebApp`
   for its credential, back button, haptics, theme and safe areas. In the
   Android/iOS app there is no Telegram, so this file provides the same object
   backed by Capacitor, and the Mini App runs unchanged:

     * initData   → `app:<token>` from the sign-in code (see login.html);
     * fetch      → `/api/...` is sent to the server in config.js;
     * BackButton → the Android hardware back button;
     * openLink / openTelegramLink → the phone's browser or Telegram app;
     * a 401 from the server → back to the sign-in screen.

   Loaded in <head>, before the Mini App's own script. */
(function () {
  "use strict";
  var CFG = window.ERNEST_CONFIG || {};
  var API = String(CFG.api || "").replace(/\/+$/, "");
  var TOKEN_KEY = "ernest.app.token";
  var cap = window.Capacitor;
  var plugin = function (name) {
    if (!cap) return null;
    if (cap.Plugins && cap.Plugins[name]) return cap.Plugins[name];
    try { return cap.registerPlugin ? cap.registerPlugin(name) : null; } catch (e) { return null; }
  };
  var store = {
    get: function () { try { return localStorage.getItem(TOKEN_KEY); } catch (e) { return null; } },
    set: function (v) { try { localStorage.setItem(TOKEN_KEY, v); } catch (e) {} },
    clear: function () { try { localStorage.removeItem(TOKEN_KEY); } catch (e) {} },
  };
  var onLoginPage = /login\.html$/.test(location.pathname);
  var pendingInbox = false;
  var token = store.get();

  window.ErnestApp = {
    api: API,
    token: function () { return store.get(); },
    signIn: function (value) { store.set(value); location.replace("index.html"); },
    signOut: function () {
      var current = store.get();
      store.clear();
      if (current) {
        nativeFetch(API + "/api/app/logout", { method: "POST",
          headers: { "X-Telegram-Init-Data": current } }).catch(function () {});
      }
      location.replace("login.html");
    },
    open: function (url) { openExternal(url); },
    pushStatus: function () { return pushStatus(); },
    requestPush: function () { return requestPush(); },
    /* A tapped push asked for the inbox; the Mini App takes the request once. */
    takePendingInbox: function () {
      var wanted = pendingInbox;
      pendingInbox = false;
      return wanted;
    },
    /* Data export: write the file into the app's cache, then hand it to the
       phone's share sheet, where the person picks Files, Drive, Telegram… */
    saveFile: function (name, text, mime) {
      var fs = plugin("Filesystem"), share = plugin("Share");
      if (!fs || !share) return Promise.reject(new Error("no_filesystem"));
      var safe = String(name || "ernestos.txt").replace(/[^A-Za-z0-9._-]/g, "_");
      return fs.writeFile({ path: safe, data: String(text), directory: "CACHE", encoding: "utf8" })
        .then(function (res) {
          return share.share({ title: safe, files: [res.uri], dialogTitle: safe })
            .catch(function (e) {
              // Closing the share sheet is not an error worth showing.
              if (!/cancel/i.test(String(e && e.message))) throw e;
            });
        });
    },
  };

  if (!token && !onLoginPage) { location.replace("login.html"); return; }

  /* ---- fetch: the server lives elsewhere; an expired session signs out ---- */
  var nativeFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : null;
    if (url && url.charAt(0) === "/" && url.charAt(1) !== "/") input = url = API + url;
    return nativeFetch(input, init).then(function (res) {
      if (res.status === 401 && token && url && url.indexOf(API + "/api/") === 0
          && url.indexOf("/api/app/") === -1) {
        store.clear();
        location.replace("login.html");
      }
      return res;
    });
  };

  /* ---- links: Capacitor hands any non-app URL to the phone (browser,
     Telegram, mail), so a plain navigation is the whole implementation. ---- */
  function openExternal(url) {
    if (!url) return;
    // Telegram's "share" link becomes the phone's own share sheet, so an
    // invite can go to WhatsApp, SMS or anywhere else, Telegram included.
    var share = plugin("Share");
    var m = /^https:\/\/t\.me\/share\/url\?(.*)$/.exec(url);
    if (share && m) {
      var q = {};
      m[1].split("&").forEach(function (kv) {
        var i = kv.indexOf("=");
        if (i > 0) q[kv.slice(0, i)] = decodeURIComponent(kv.slice(i + 1).replace(/\+/g, " "));
      });
      share.share({ text: [q.text, q.url].filter(Boolean).join("\n"), url: q.url })
        .catch(function () {});
      return;
    }
    var browser = plugin("Browser");
    if (browser && /^https?:/.test(url) && !/^https?:\/\/t\.me\//.test(url)) {
      browser.open({ url: url }).catch(function () { location.href = url; });
      return;
    }
    location.href = url;
  }

  /* ---- safe areas: Android 15+ draws edge to edge; read the insets the
     WebView reports through CSS env(). ---- */
  function readInsets() {
    var probe = document.createElement("div");
    probe.style.cssText = "position:fixed;visibility:hidden;pointer-events:none;" +
      "padding:var(--safe-area-inset-top,env(safe-area-inset-top)) " +
      "var(--safe-area-inset-right,env(safe-area-inset-right)) " +
      "var(--safe-area-inset-bottom,env(safe-area-inset-bottom)) " +
      "var(--safe-area-inset-left,env(safe-area-inset-left))";
    (document.body || document.documentElement).appendChild(probe);
    var cs = getComputedStyle(probe);
    var out = { top: parseFloat(cs.paddingTop) || 0, right: parseFloat(cs.paddingRight) || 0,
                bottom: parseFloat(cs.paddingBottom) || 0, left: parseFloat(cs.paddingLeft) || 0 };
    probe.remove();
    return out;
  }

  /* ---- the Telegram.WebApp stand-in ---- */
  var listeners = {};
  var emit = function (name) { (listeners[name] || []).forEach(function (fn) { try { fn(); } catch (e) {} }); };
  var dark = window.matchMedia && matchMedia("(prefers-color-scheme: dark)");
  var backHandlers = [];
  var noop = function () {};
  var haptics = plugin("Haptics");
  var WebApp = {
    initData: token || "",
    initDataUnsafe: {},
    platform: (cap && cap.getPlatform && cap.getPlatform()) || "web",
    version: "8.0",
    isErnestApp: true,
    colorScheme: dark && dark.matches ? "dark" : "light",
    themeParams: {},
    viewportHeight: window.innerHeight,
    viewportStableHeight: window.innerHeight,
    safeAreaInset: { top: 0, right: 0, bottom: 0, left: 0 },
    contentSafeAreaInset: { top: 0, right: 0, bottom: 0, left: 0 },
    isVersionAtLeast: function () { return true; },
    ready: noop, expand: noop, disableVerticalSwipes: noop, enableVerticalSwipes: noop,
    enableClosingConfirmation: noop, disableClosingConfirmation: noop,
    setHeaderColor: noop, setBackgroundColor: noop, setBottomBarColor: noop,
    close: function () {
      var app = plugin("App");
      if (app && app.minimizeApp) app.minimizeApp(); else if (app) app.exitApp();
    },
    onEvent: function (name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
    offEvent: function (name, fn) {
      listeners[name] = (listeners[name] || []).filter(function (f) { return f !== fn; });
    },
    openLink: function (url) { openExternal(url); },
    openTelegramLink: function (url) { openExternal(url); },
    showAlert: function (msg, cb) { alert(msg); if (cb) cb(); },
    showConfirm: function (msg, cb) { var ok = confirm(msg); if (cb) cb(ok); },
    HapticFeedback: {
      impactOccurred: function (style) {
        if (haptics) haptics.impact({ style: String(style || "light").toUpperCase() === "HEAVY" ? "HEAVY"
          : String(style || "light").toUpperCase() === "MEDIUM" ? "MEDIUM" : "LIGHT" }).catch(noop);
        else if (navigator.vibrate) navigator.vibrate(8);
      },
      notificationOccurred: function (type) {
        var kind = { success: "SUCCESS", warning: "WARNING", error: "ERROR" }[type] || "SUCCESS";
        if (haptics) haptics.notification({ type: kind }).catch(noop);
        else if (navigator.vibrate) navigator.vibrate(type === "error" ? [20, 40, 20] : 12);
      },
      selectionChanged: function () { if (haptics) haptics.selectionChanged().catch(noop); },
    },
    BackButton: {
      isVisible: false,
      show: function () { WebApp.BackButton.isVisible = true; },
      hide: function () { WebApp.BackButton.isVisible = false; },
      onClick: function (fn) { backHandlers.push(fn); },
      offClick: function (fn) { backHandlers = backHandlers.filter(function (f) { return f !== fn; }); },
    },
    MainButton: { show: noop, hide: noop, setText: noop, onClick: noop, offClick: noop,
                  enable: noop, disable: noop, setParams: noop },
  };
  window.Telegram = { WebApp: WebApp };

  /* Android back: whatever the Mini App calls "back" while its button is
     showing (a sheet, a sub-screen), else leave the app to the background. */
  var app = plugin("App");
  if (app && app.addListener) {
    app.addListener("backButton", function () {
      if (WebApp.BackButton.isVisible && backHandlers.length) {
        backHandlers.forEach(function (fn) { try { fn(); } catch (e) {} });
      } else {
        WebApp.close();
      }
    });
  }

  /* ---- push: register this phone with Firebase and the server ----
     Only in builds made with a google-services.json (config.js says so):
     without Firebase, registering would fail natively. The inbox works
     either way. */
  function setUpPush() {
    var push = plugin("PushNotifications");
    if (!push || !CFG.push || !token) return;
    var channels = [
      { id: "reminders", name: "Eslatmalar", description: "Eslatmalar, hisobotlar, taymerlar",
        importance: 4, visibility: 1, vibration: true },
      { id: "quiet", name: "Sokin soatlar", description: "Sokin soatlarda ovozsiz",
        importance: 2, visibility: 1, vibration: false },
    ];
    channels.forEach(function (c) { if (push.createChannel) push.createChannel(c).catch(noop); });
    push.addListener("registration", function (reg) {
      nativeFetch(API + "/api/app/push-token", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Telegram-Init-Data": token },
        body: JSON.stringify({ token: reg.value, platform: WebApp.platform }),
      }).catch(noop);
    });
    push.addListener("registrationError", function (e) {
      console.warn("push registration failed", e && e.error);
    });
    push.addListener("pushNotificationReceived", function () {
      window.dispatchEvent(new Event("ernest:push"));
    });
    push.addListener("pushNotificationActionPerformed", function () {
      pendingInbox = true;
      window.dispatchEvent(new Event("ernest:open-inbox"));
    });
    /* Allowed already: register quietly. Not asked yet: ask once when the
       app opens (the owner's request), then not again for a few days —
       Android stops showing the dialog after two refusals anyway. */
    push.checkPermissions().then(function (p) {
      if (p.receive === "granted") return push.register();
      if (p.receive !== "denied" && mayAskNow()) {
        return push.requestPermissions().then(function (r) {
          if (r.receive === "granted") push.register();
        });
      }
    }).catch(noop);
  }

  /* ---- reminders shown by the phone itself ----
     A build without Firebase cannot receive pushes, so the reminders of the
     next two days (/api/app/schedule) are scheduled on the device and
     replaced on every open and every return to the app. What changes while
     the app stays closed is corrected the next time it opens. */
  var ASKED_KEY = "ernest.notify.asked";
  var LOCAL_BASE = 700000;
  function mayAskNow() {
    try {
      var last = Number(localStorage.getItem(ASKED_KEY) || 0);
      if (Date.now() - last < 3 * 864e5) return false;
      localStorage.setItem(ASKED_KEY, String(Date.now()));
      return true;
    } catch (e) { return true; }
  }
  /* A whole number per reminder key, so the same reminder keeps its id. */
  function localId(key) {
    var h = 0;
    for (var i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
    return LOCAL_BASE + (Math.abs(h) % 1000000000);
  }
  var syncing = null;
  function syncLocal() {
    var local = plugin("LocalNotifications");
    if (!local || CFG.push || !token) return Promise.resolve("off");
    if (syncing) return syncing;
    syncing = local.checkPermissions().then(function (p) {
      if (p.display === "granted") return p.display;
      if (p.display === "denied" || !mayAskNow()) return p.display;
      return local.requestPermissions().then(function (r) { return r.display; });
    }).then(function (state) {
      if (state !== "granted") return state;
      return nativeFetch(API + "/api/app/schedule", { headers: { "X-Telegram-Init-Data": token } })
        .then(function (r) { return r.ok ? r.json() : { items: null }; })
        .then(function (data) {
          if (!data || !data.items) return "on";
          return local.getPending().then(function (pending) {
            var ours = (pending.notifications || []).filter(function (n) {
              return n.extra && n.extra.ernest; });
            return ours.length ? local.cancel({ notifications: ours.map(function (n) {
              return { id: n.id }; }) }) : null;
          }).then(function () {
            var list = data.items.map(function (x) {
              return { id: localId(x.key), title: x.title, body: x.body,
                       schedule: { at: new Date(x.at), allowWhileIdle: true },
                       channelId: x.silent ? "quiet" : "reminders",
                       extra: { ernest: 1, open: x.open || "inbox" } };
            });
            return list.length ? local.schedule({ notifications: list }) : null;
          }).then(function () { return "on"; });
        });
    }).catch(function () { return "off"; }).then(function (v) { syncing = null; return v; });
    return syncing;
  }
  function setUpLocal() {
    var local = plugin("LocalNotifications");
    if (!local || CFG.push || !token) return;
    [{ id: "reminders", name: "Eslatmalar", description: "Eslatmalar, hisobotlar, to'lovlar",
       importance: 4, visibility: 1, vibration: true },
     { id: "quiet", name: "Sokin soatlar", description: "Sokin soatlarda ovozsiz",
       importance: 2, visibility: 1, vibration: false }].forEach(function (c) {
      if (local.createChannel) local.createChannel(c).catch(noop);
    });
    local.addListener("localNotificationActionPerformed", function (ev) {
      var open = ev && ev.notification && ev.notification.extra && ev.notification.extra.open;
      pendingInbox = true;
      window.dispatchEvent(new CustomEvent("ernest:open-inbox", { detail: { open: open } }));
    });
    syncLocal();
    // Back in the app: what was done meanwhile drops out of the schedule.
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) syncLocal();
    });
    // A change in the Mini App (a tick, a new task) reschedules shortly after.
    var later = null;
    window.addEventListener("ernest:changed", function () {
      clearTimeout(later);
      later = setTimeout(syncLocal, 4000);
    });
  }
  /* What Settings shows: is this build able to push at all, and what has
     the phone allowed. Nothing here claims a message was delivered. */
  function pushStatus() {
    var push = plugin("PushNotifications");
    var local = plugin("LocalNotifications");
    if ((!push || !CFG.push) && local) {
      return local.checkPermissions().then(function (p) {
        return p.display === "granted" ? "on" : p.display === "denied" ? "denied" : "ask";
      }).catch(function () { return "no_build"; });
    }
    if (!push || !CFG.push) return Promise.resolve("no_build");
    return push.checkPermissions().then(function (p) {
      return p.receive === "granted" ? "on" : p.receive === "denied" ? "denied" : "ask";
    }).catch(function () { return "no_build"; });
  }
  function requestPush() {
    var push = plugin("PushNotifications");
    var local = plugin("LocalNotifications");
    if ((!push || !CFG.push) && local) {
      return local.requestPermissions().then(function (p) {
        if (p.display === "granted") { syncLocal(); return "on"; }
        return p.display === "denied" ? "denied" : "ask";
      }).catch(function () { return "denied"; });
    }
    if (!push || !CFG.push) return Promise.resolve("no_build");
    return push.requestPermissions().then(function (p) {
      if (p.receive === "granted") { push.register(); return "on"; }
      return p.receive === "denied" ? "denied" : "ask";
    }).catch(function () { return "denied"; });
  }
  if (!onLoginPage) {
    document.addEventListener("DOMContentLoaded", setUpPush);
    document.addEventListener("DOMContentLoaded", setUpLocal);
  }

  function refreshViewport() {
    WebApp.viewportHeight = WebApp.viewportStableHeight = window.innerHeight;
    var inset = readInsets();
    WebApp.safeAreaInset = inset;
    emit("viewportChanged");
    emit("safeAreaChanged");
  }
  window.addEventListener("resize", refreshViewport);
  document.addEventListener("DOMContentLoaded", refreshViewport);
  if (dark && dark.addEventListener) {
    dark.addEventListener("change", function () {
      WebApp.colorScheme = dark.matches ? "dark" : "light";
      emit("themeChanged");
    });
  }
})();
