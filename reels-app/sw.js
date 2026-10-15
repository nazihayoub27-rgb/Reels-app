/* Service Worker — ريلزي */
const CACHE = "reels-shell-v9";
const SHELL = ["./index.html"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

/* App Shell: كاش أولًا ثم الشبكة */
self.addEventListener("fetch", e => {
  if(e.request.method !== "GET") return;
  const p = new URL(e.request.url).pathname;
  if(p.startsWith("/share") || p === "/lan-ip") return;   // لا نخزّن الفيديوهات المرسلة
  e.respondWith(
    caches.match(e.request, {ignoreSearch: true}).then(cached => {
      const fetched = fetch(e.request).then(res => {
        if(res.ok && new URL(e.request.url).origin === location.origin){
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      }).catch(() => cached);
      return cached || fetched;
    })
  );
});
