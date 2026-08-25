const CACHE_NAME = "mpb-suivi-v2";
const SHELL = ["./manifest.json", "./icon-192.png", "./icon-512.png"];

self.addEventListener("install", function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) { return cache.addAll(SHELL); })
  );
  self.skipWaiting();
});

self.addEventListener("activate", function(event) {
  event.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(keys.filter(function(k) { return k !== CACHE_NAME; }).map(function(k) { return caches.delete(k); }));
    })
  );
  self.clients.claim();
});

self.addEventListener("fetch", function(event) {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  // La page HTML elle-même et les appels à Supabase ne sont JAMAIS mis en
  // cache — toujours du réseau frais. Seuls les vrais fichiers statiques
  // (icônes, manifest) sont mis en cache, pour un secours hors-ligne minimal.
  if (url.hostname.indexOf("supabase.co") !== -1 || event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).catch(function() { return caches.match(event.request); }));
    return;
  }
  event.respondWith(
    fetch(event.request)
      .then(function(response) {
        const copy = response.clone();
        caches.open(CACHE_NAME).then(function(cache) { cache.put(event.request, copy); });
        return response;
      })
      .catch(function() { return caches.match(event.request); })
  );
});
