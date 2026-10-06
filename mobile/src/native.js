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
