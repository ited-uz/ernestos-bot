// Builds mobile/www from the Mini App: the same index.html Telegram serves,
// with the Telegram script swapped for native.js and a config.js naming the
// server. Run with ERNEST_API_URL=https://your-server (no trailing slash).
import { copyFileSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const mobile = join(here, "..");
const webapp = join(mobile, "..", "webapp");
const www = join(mobile, "www");

const api = (process.env.ERNEST_API_URL || "").trim().replace(/\/+$/, "");
// Plain http only for the local browser checks (scripts/e2e_app.sh).
const scheme = process.env.ERNEST_ALLOW_HTTP === "1" ? /^https?:\/\/[^/]+/ : /^https:\/\/[^/]+/;
if (!scheme.test(api)) {
  console.error("ERNEST_API_URL must be the server's https:// address, e.g. https://ernestos.up.railway.app");
  process.exit(1);
}

rmSync(www, { recursive: true, force: true });
mkdirSync(www, { recursive: true });

const telegramScript = '<script src="https://telegram.org/js/telegram-web-app.js"></script>';
let html = readFileSync(join(webapp, "index.html"), "utf8");
if (!html.includes(telegramScript)) {
  console.error("index.html no longer loads telegram-web-app.js where expected; update build-www.mjs");
  process.exit(1);
}
html = html.replace(telegramScript,
  '<script src="config.js"></script>\n<script src="native.js"></script>');
writeFileSync(join(www, "index.html"), html);

// Push is on only in builds that carry Firebase's google-services.json;
// registering without it would fail natively.
const push = existsSync(join(mobile, "android", "app", "google-services.json"));
writeFileSync(join(www, "config.js"),
  `window.ERNEST_CONFIG = ${JSON.stringify({ api, push, built: new Date().toISOString() })};\n`);
copyFileSync(join(mobile, "src", "native.js"), join(www, "native.js"));
copyFileSync(join(mobile, "src", "login.html"), join(www, "login.html"));
copyFileSync(join(mobile, "src", "logo.png"), join(www, "logo.png"));
console.log(`www built for ${api} (push ${push ? "on" : "off: no google-services.json"})`);
