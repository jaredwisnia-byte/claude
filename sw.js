// Network-first for the app shell so updates land immediately; cache is the offline fallback.
// News requests are never cached here — saved events live in localStorage.
const CACHE = "midnight-watch-v3";
const SHELL = ["./", "index.html", "style.css", "config.js", "app.js", "manifest.webmanifest", "icon.svg", "apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin) return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});

// Payloads come from scripts/notify.mjs: { title, body, tag, url }.
self.addEventListener("push", (e) => {
  let msg = {};
  try {
    msg = e.data ? e.data.json() : {};
  } catch {
    msg = { body: e.data && e.data.text() };
  }
  e.waitUntil(
    self.registration.showNotification(msg.title || "Midnight Watch", {
      body: msg.body || "New event logged.",
      tag: msg.tag,
      icon: "apple-touch-icon.png",
      data: { url: msg.url },
    })
  );
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = (e.notification.data && e.notification.data.url) || self.registration.scope;
  e.waitUntil(self.clients.openWindow(target));
});
