/* ErnestOS in a browser — the bridge between the app and the browser.

   The app (webapp/index.html) was written for Telegram: it reads
   `window.Telegram.WebApp` for its credential, back button, haptics, theme
   and safe areas. In a plain browser there is no Telegram, so this file
   provides the same object, the way mobile/src/native.js does on the phone:

     * initData   → the marker "web". The session itself is an HttpOnly
                    cookie no script can read; the server puts it in the
                    marker's place (web_app.py).
     * BackButton → the browser's back (and Android's back gesture): one
                    history entry stands for "there is something to close".
     * openLink   → a new tab.
     * saveFile   → a real download.
     * signing out, or a session that has ended → the sign-in page.

   Served at /app/web.js and loaded in <head>, before the app's own script. */
(function () {
  "use strict";
  var MARK = "web";
  var LOGIN = "/app/login";
  var onLogin = location.pathname === LOGIN;
  var noop = function () {};
  var hinted = /(?:^|;\s*)eos_in=1(?:;|$)/.test(document.cookie);

  function toLogin() {
    if (onLogin) return;
    var back = location.pathname + location.search;
    location.replace(LOGIN + (back && back !== "/app/" ? "?next=" + encodeURIComponent(back) : ""));
  }
  // Never signed in in this browser (or signed out): straight to sign-in,
  // before the app spends a request finding that out.
  if (!onLogin && !hinted) { toLogin(); return; }

  /* ---- fetch: an ended session goes to sign-in. Only the server's own
     "unauthorized" means that: a wrong code is a 401 too, and must not
     sign anybody out. ---- */
  var nativeFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var path = url.replace(location.origin, "");
    return nativeFetch(input, init).then(function (res) {
      if (res.status === 401 && !onLogin && path.indexOf("/api/") === 0) {
        res.clone().json().then(function (b) {
          if (b && b.detail === "unauthorized") toLogin();
        }).catch(noop);
      }
      return res;
    });
  };

  /* ---- the files the app hands over: a real download ---- */
  function download(name, data, mime, base64) {
    var bytes = data;
    if (base64) {
      var raw = atob(String(data));
      bytes = new Uint8Array(raw.length);
      for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    }
    var blob = new Blob([bytes], { type: mime || "application/octet-stream" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = String(name || "ernestos.txt").replace(/[\\/:*?"<>|]+/g, "_");
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 4000);
    return Promise.resolve();
  }

  function openExternal(url) {
    if (!url) return;
    var w = window.open(url, "_blank", "noopener,noreferrer");
    if (!w) location.href = url;
  }

  window.ErnestApp = {
    api: "",
    web: true,
    token: function () { return null; },
    /* The session is already a cookie by the time sign-in calls this. */
    signIn: function () {
      // Back where the session ran out — but only ever inside the app.
      var next = new URLSearchParams(location.search).get("next") || "";
      location.replace(/^\/app\/(\?[^#]*)?$/.test(next) ? next : "/app/");
    },
    signOut: function () {
      nativeFetch("/api/web/logout", { method: "POST", headers: { "X-Telegram-Init-Data": MARK } })
        .catch(noop)
        .then(function () { location.replace(LOGIN); });
    },
    open: openExternal,
    saveFile: download,
    /* Browser notifications are not wired to the server yet; reminders
       come to the in-app inbox and the Telegram bot. Said as it is. */
    pushStatus: function () { return Promise.resolve("web"); },
    requestPush: function () { return Promise.resolve("web"); },
    nativeGoogle: false,
    takePendingInbox: function () { return false; },
  };

  /* ---- safe areas (a phone's notch, rounded corners) ---- */
  function readInsets() {
    var probe = document.createElement("div");
    probe.style.cssText = "position:fixed;visibility:hidden;pointer-events:none;" +
      "padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left)";
    (document.body || document.documentElement).appendChild(probe);
    var cs = getComputedStyle(probe);
    var out = { top: parseFloat(cs.paddingTop) || 0, right: parseFloat(cs.paddingRight) || 0,
                bottom: parseFloat(cs.paddingBottom) || 0, left: parseFloat(cs.paddingLeft) || 0 };
    probe.remove();
    return out;
  }

  /* ---- back: one history entry while there is something to go back to ---- */
  var backHandlers = [];
  var pushed = false;
  var skipPop = false;
  window.addEventListener("popstate", function () {
    if (skipPop) { skipPop = false; return; }
    if (!pushed) return;
    pushed = false;
    backHandlers.forEach(function (fn) { try { fn(); } catch (e) {} });
  });

  var listeners = {};
  var emit = function (name) { (listeners[name] || []).forEach(function (fn) { try { fn(); } catch (e) {} }); };
  var dark = window.matchMedia && matchMedia("(prefers-color-scheme: dark)");
  var fine = window.matchMedia && matchMedia("(pointer: fine)");
  var vibrate = function (p) { if (navigator.vibrate && !(fine && fine.matches)) try { navigator.vibrate(p); } catch (e) {} };
  var WebApp = {
    initData: onLogin ? "" : MARK,
    initDataUnsafe: {},
    platform: "web",
    version: "8.0",
    isErnestApp: true,
    isErnestWeb: true,
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
    close: noop,
    onEvent: function (name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
    offEvent: function (name, fn) {
      listeners[name] = (listeners[name] || []).filter(function (f) { return f !== fn; });
    },
    openLink: openExternal,
    openTelegramLink: openExternal,
    showAlert: function (msg, cb) { alert(msg); if (cb) cb(); },
    showConfirm: function (msg, cb) { var ok = confirm(msg); if (cb) cb(ok); },
    HapticFeedback: {
      impactOccurred: function () { vibrate(8); },
      notificationOccurred: function (type) { vibrate(type === "error" ? [20, 40, 20] : 12); },
      selectionChanged: noop,
    },
    BackButton: {
      isVisible: false,
      show: function () {
        WebApp.BackButton.isVisible = true;
        if (!pushed) { pushed = true; try { history.pushState({ eosBack: 1 }, ""); } catch (e) {} }
      },
      hide: function () {
        WebApp.BackButton.isVisible = false;
        if (pushed) { pushed = false; skipPop = true; try { history.back(); } catch (e) { skipPop = false; } }
      },
      onClick: function (fn) { backHandlers.push(fn); },
      offClick: function (fn) { backHandlers = backHandlers.filter(function (f) { return f !== fn; }); },
    },
    MainButton: { show: noop, hide: noop, setText: noop, onClick: noop, offClick: noop,
                  enable: noop, disable: noop, setParams: noop },
  };
  window.Telegram = { WebApp: WebApp };

  /* The address bar and the window follow the app's own light/dark. */
  function paintTheme() {
    var bg = getComputedStyle(document.body || document.documentElement).backgroundColor;
    if (!bg || bg === "rgba(0, 0, 0, 0)") return;
    Array.prototype.forEach.call(document.querySelectorAll('meta[name="theme-color"]'), function (m) {
      m.setAttribute("content", bg); m.removeAttribute("media");
    });
  }
  if (!onLogin) {
    new MutationObserver(function () { setTimeout(paintTheme, 50); }).observe(document.documentElement,
      { attributes: true, attributeFilter: ["data-mode", "data-theme", "class"] });
  }

  function refreshViewport() {
    WebApp.viewportHeight = WebApp.viewportStableHeight = window.innerHeight;
    WebApp.safeAreaInset = readInsets();
    emit("viewportChanged");
    emit("safeAreaChanged");
  }
  window.addEventListener("resize", refreshViewport);
  document.addEventListener("DOMContentLoaded", function () { refreshViewport(); if (!onLogin) setTimeout(paintTheme, 300); });
  if (dark && dark.addEventListener) {
    dark.addEventListener("change", function () {
      WebApp.colorScheme = dark.matches ? "dark" : "light";
      emit("themeChanged");
    });
  }

  /* Open without a network: the app's own files from the service worker
     (https, or localhost while developing). */
  if ("serviceWorker" in navigator && window.isSecureContext) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/app/sw.js", { scope: "/app/" }).catch(noop);
    });
  }

  /* Sign-in while still signed in (a bookmark, the back button): straight
     on. A session that has ended takes its hint cookie with it. */
  if (onLogin && hinted) {
    nativeFetch("/api/me", { headers: { "X-Telegram-Init-Data": MARK } }).then(function (r) {
      if (r.ok) window.ErnestApp.signIn();
      else if (r.status === 401) document.cookie = "eos_in=; Max-Age=0; Path=/; SameSite=Strict";
    }).catch(noop);
  }
})();
