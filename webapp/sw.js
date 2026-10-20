/* ErnestOS web app — service worker (served at /app/sw.js, scope /app/).

   Keeps the app's own files so it opens with no network; what was typed
   offline waits in the app's queue and goes when the network is back.
   Never touches /api/: data is always asked of the server, never served
   stale from here. The page itself is network-first, so a new release
   arrives on the next open; __BUILD__ (filled in by the server) names the
   cache, and an old cache is dropped when the new worker takes over. */
"use strict";
var CACHE = "ernestos-web-__BUILD__";
var SHELL = ["/app/", "/app/login", "/app/web.js?v=__BUILD__", "/app/manifest.webmanifest",
             "/app/icons/icon-192.png", "/app/logo.png"];

self.addEventListener("install", function (event) {
  event.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL); })
    .then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (event) {
  event.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k.indexOf("ernestos-web-") === 0 && k !== CACHE; })
      .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

function fromNetwork(request, key) {
  return fetch(request).then(function (res) {
    if (res && res.ok && res.type === "basic") {
      var copy = res.clone();
      caches.open(CACHE).then(function (c) { c.put(key || request, copy); });
    }
    return res;
  });
}

self.addEventListener("fetch", function (event) {
  var req = event.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  if (url.origin !== location.origin || url.pathname.indexOf("/app/") !== 0 || url.pathname === "/app/sw.js") return;
  if (req.mode === "navigate") {
    // The page: the network first, the kept copy when there is none.
    var key = url.pathname === "/app/login" ? "/app/login" : "/app/";
    event.respondWith(fromNetwork(req, key).catch(function () {
      return caches.match(key).then(function (hit) { return hit || Response.error(); });
    }));
    return;
  }
  // The app's files: the kept copy, refreshed in the background.
  event.respondWith(caches.match(req).then(function (hit) {
    var fresh = fromNetwork(req).catch(function () { return hit || Response.error(); });
    return hit || fresh;
  }));
});
