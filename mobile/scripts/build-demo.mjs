// One self-contained page of the phone app with sample data, for trying the
// interface without a phone or a server: the same webapp/index.html, the
// phone-app bridge reduced to what a browser can do, and webapp/preview.js
// answering /api/* in the page. Nothing is sent anywhere.
//   node mobile/scripts/build-demo.mjs <out.html> [lang] [--offline]
//
// --offline builds the personal offline copy instead: no sample data, the
// user's own entries kept in the browser (webapp/offline-store.js), and the
// backup file really downloaded.
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webapp = join(here, "..", "..", "webapp");
const args = process.argv.slice(2).filter(a => !a.startsWith("--"));
const offline = process.argv.includes("--offline");
const out = args[0];
const lang = args[1] || "uz";
if (!out) { console.error("usage: build-demo.mjs <out.html> [lang] [--offline]"); process.exit(1); }

let html = readFileSync(join(webapp, "index.html"), "utf8");
const preview = readFileSync(join(webapp, "preview.js"), "utf8");
const store = offline ? readFileSync(join(webapp, "offline-store.js"), "utf8") : "";
// On the phone these hand the file to Android's share sheet. The offline copy
// downloads it and also shows it as text to copy, with a way to restore it
// (offline-store.js): the backup is the only copy of the data off this browser.
const saveFile = offline
  ? `function(name, text){ window.ErnestOffline.showBackup(name, text); return Promise.resolve(); }`
  : `function(){ return Promise.resolve(); }`;

const shim = `<script>
window.ERNEST_PREVIEW_QS = ${JSON.stringify(`lang=${lang}&app=1`)};
(function(){
  var noop = function(){};
  var dark = window.matchMedia && matchMedia("(prefers-color-scheme: dark)");
  var back = [];
  var WebApp = {
    initData: "preview", initDataUnsafe: {}, platform: "android", version: "8.0", isErnestApp: true,
    colorScheme: dark && dark.matches ? "dark" : "light", themeParams: {},
    viewportHeight: innerHeight, viewportStableHeight: innerHeight,
    safeAreaInset: {top:0,right:0,bottom:0,left:0}, contentSafeAreaInset: {top:0,right:0,bottom:0,left:0},
    isVersionAtLeast: function(){ return true; }, ready: noop, expand: noop,
    disableVerticalSwipes: noop, setHeaderColor: noop, setBackgroundColor: noop, close: noop,
    onEvent: noop, offEvent: noop,
    openLink: function(u){ window.open(u, "_blank"); }, openTelegramLink: function(u){ window.open(u, "_blank"); },
    HapticFeedback: {impactOccurred: noop, notificationOccurred: noop, selectionChanged: noop},
    BackButton: {isVisible:false, show:function(){ this.isVisible = true; }, hide:function(){ this.isVisible = false; },
                 onClick:function(f){ back.push(f); }, offClick: noop},
    MainButton: {show:noop, hide:noop, setText:noop, onClick:noop, offClick:noop, setParams:noop}
  };
  window.Telegram = {WebApp: WebApp};
  window.ErnestApp = {
    saveFile: ${saveFile},
    signOut: noop, open: function(u){ window.open(u, "_blank"); },
    takePendingInbox: function(){ return false; }
  };
})();
</script>
${offline ? `<script>\n${store}\n</script>\n` : ""}<script>
${preview}
</script>`;

const telegram = '<script src="https://telegram.org/js/telegram-web-app.js"></script>';
if (!html.includes(telegram)) { console.error("telegram script tag not found"); process.exit(1); }
html = html.replace(telegram, shim);
// The ?preview loader is replaced by the inline copy above.
html = html.replace(/<script>\s*\/\* Design preview only\.[\s\S]*?<\/script>/, "");
// The publish skeleton supplies the document; keep the page's own head contents.
html = html.replace(/<!DOCTYPE html>\s*/i, "").replace(/<html[^>]*>\s*/i, "").replace(/<\/html>\s*$/i, "")
           .replace(/<head>\s*/i, "").replace(/<\/head>\s*/i, "").replace(/<body([^>]*)>/i, "").replace(/<\/body>\s*/i, "");
html = html.replace("<title>ErnestOS</title>", offline ? "<title>ErnestOS oflayn</title>" : "<title>ErnestOS ilova sinovi</title>");
// The app's confirm() dialogs do not show inside an artifact viewer; say yes.
html = html.replace("<script>\nwindow.ERNEST_PREVIEW_QS", "<script>\nwindow.confirm = function(){ return true; };\nwindow.ERNEST_PREVIEW_QS");
writeFileSync(out, html);
console.log(`${offline ? "offline copy" : "demo"} written: ${out} (${(html.length / 1024).toFixed(0)} KB, ${lang})`);
