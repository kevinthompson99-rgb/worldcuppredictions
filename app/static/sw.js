// Bump this on every deploy that changes the cached shell — a byte-for-byte
// diff in this file is what makes the browser notice an update is available.
const CACHE_NAME = "leprem-shell-v1";

const APP_SHELL_URLS = [
  "/",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL_URLS))
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
});

// Pages: network-first (so logged-in players always see live scores/results
// when online) falling back to the cached shell when offline. Static assets:
// cache-first, since they only change on deploy and a fresh CACHE_NAME clears
// stale copies anyway.
self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match(request).then((cached) => cached || caches.match("/")))
    );
    return;
  }

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(request).then(
        (cached) =>
          cached ||
          fetch(request).then((response) => {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
            return response;
          })
      )
    );
  }
});

// The Players page's refresh button sends this when it finds a waiting worker
// — without it, a waiting worker sits idle until every open tab is closed.
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

self.addEventListener("push", function(event) {
  // event.data.json() can throw (decryption hiccup, or a payload that isn't valid
  // JSON) - if it does without a try/catch, the handler throws before
  // event.waitUntil() is ever called, and the browser falls back to its own blank
  // "generic" notification (a push event MUST result in *some* notification per
  // spec) - which is exactly a silent, empty banner with no useful click target.
  // Falling back to the raw payload text at least makes a decryption/format
  // problem visible instead of invisible.
  var payload = {};
  if (event.data) {
    try {
      payload = event.data.json();
    } catch (jsonErr) {
      var raw;
      try { raw = event.data.text(); } catch (textErr) { raw = "(unreadable push payload)"; }
      payload = { title: "LEPREM", body: raw };
    }
  }
  var title = payload.title || "LEPREM";
  var options = {
    body: payload.body || "",
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
    data: { url: payload.url || "/" }
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", function(event) {
  event.notification.close();
  var url = event.notification.data.url;
  // clients.openWindow() alone often just re-focuses an already-open PWA window on
  // iOS instead of navigating it - explicitly find an existing window and navigate
  // it first, falling back to opening a new one only if none is open.
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clientList) {
      for (var i = 0; i < clientList.length; i++) {
        var client = clientList[i];
        if ("focus" in client) {
          var focused = client.focus();
          if ("navigate" in client) {
            return client.navigate(url).then(function () { return focused; }).catch(function () { return focused; });
          }
          return focused;
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(url);
      }
    })
  );
});
