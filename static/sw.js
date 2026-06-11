const CACHE = "sport-assistant-static-v17";
const ASSETS = [
  "/static/tablet.css",
  "/static/responsive.css",
  "/static/tablet.js",
  "/static/icon.svg",
  "/static/manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

function isMutableAsset(pathname) {
  return /\.(?:js|css)$/.test(pathname);
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      (async () => {
        if (isMutableAsset(url.pathname)) {
          try {
            const res = await fetch(event.request);
            const copy = res.clone();
            caches.open(CACHE).then((cache) => cache.put(event.request, copy));
            return res;
          } catch (err) {
            const cached = await caches.match(event.request);
            if (cached) return cached;
            throw err;
          }
        }
        const cached = await caches.match(event.request);
        if (cached) return cached;
        const res = await fetch(event.request);
        const copy = res.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        return res;
      })()
    );
  }
});
