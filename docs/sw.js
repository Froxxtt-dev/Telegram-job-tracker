const CACHE = "job-radar-v2"; // bumped so this update forces a clean cache
const CORE_ASSETS = ["./index.html", "./style.css", "./app.js", "./manifest.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(CORE_ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)))
    ).then(() => clients.claim())
  );
});

// Network-first: always try to get the latest version when online,
// only fall back to the cached copy if there's no connection.
self.addEventListener("fetch", (event) => {
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});

self.addEventListener("push", (event) => {
  const payload = event.data ? event.data.json() : { title: "New job posted", body: "" };
  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      icon: "icons/icon-192.png",
      badge: "icons/icon-192.png",
      tag: "job-radar-" + Date.now(),
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(clients.openWindow("./index.html"));
});
