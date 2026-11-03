// Bump this on every deploy that changes the cached shell — a byte-for-byte
// diff in this file is what makes the browser notice an update is available.
const CACHE_NAME = "leprem-shell-v6";

const APP_SHELL_URLS = [
  "/static/manifest.json",
  "/static/icons/apple-touch-icon-v3.png",
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

// Pages: always straight to the network and never cached. Almost every page
// carries a form with a CSRF token, and a cached copy replays a stale token.
// Static assets: cache-first, since they only change on deploy and a fresh
// CACHE_NAME clears stale copies anyway.
self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") return;

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
  // Parse defensively: a payload that is not valid JSON falls back to its raw text as
  // the body. If title and body are both missing the banner reads "LEPREM" / "Tap to
  // read your latest update" rather than the browser's generic "Notification".
  var payload = {};
  if (event.data) {
    try {
      payload = event.data.json() || {};
    } catch (jsonErr) {
      var raw = "";
      try { raw = event.data.text(); } catch (textErr) { raw = ""; }
      payload = { body: raw };
    }
  }
  var title = (payload.title && String(payload.title).trim()) || "LEPREM";
  var body = (payload.body && String(payload.body).trim()) || "Tap to read your latest update";
  var options = {
    body: body,
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
    data: { url: payload.url || "/", notification_id: payload.notification_id || null }
  };

  // The app's home-screen icon badge (the little red counter) is a separate API
  // from the notification itself - showNotification() alone never sets it. We
  // don't track a real unread count, so this just flags "something new" and gets
  // cleared again once the app is opened (see base.html) or the notification is
  // tapped (below).
  //
  // iOS's Badging API support inside a service worker is unreliable even though
  // it works from a page - try it directly here, but also ask any open window to
  // set it from its own page context, which is more likely to actually work.
  var tasks = [self.registration.showNotification(title, options)];
  try {
    if ("setAppBadge" in self.navigator) {
      tasks.push(self.navigator.setAppBadge(1).catch(function () {}));
    }
  } catch (badgeErr) {
    // A synchronous throw here must not stop event.waitUntil() below from running.
  }
  tasks.push(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clientList) {
      clientList.forEach(function (client) { client.postMessage({ type: "SET_APP_BADGE" }); });
    })
  );
  event.waitUntil(Promise.all(tasks));
});

self.addEventListener("notificationclick", function(event) {
  event.notification.close();
  // The page sets the icon badge to the real unread count once it loads, so it is
  // not cleared here.
  var data = event.notification.data || {};
  var url = new URL(data.url || "/", self.location.origin).href;
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
