// Service worker minimal : met en cache la coquille de l'application (HTML/JS/CSS/polices) pour un
// démarrage instantané. JAMAIS les réponses /api : des données bancaires n'ont rien à faire dans un cache.
const CACHE = "bank-shell-v1";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(["/", "/index.html", "/icon.svg"])));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) {
    return; // réseau uniquement
  }
  if (url.pathname.startsWith("/assets/")) {
    // Fichiers versionnés par hash : cache d'abord.
    event.respondWith(
      caches.match(event.request).then(
        (hit) =>
          hit ||
          fetch(event.request).then((res) => {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(event.request, copy));
            return res;
          }),
      ),
    );
    return;
  }
  // Navigation : réseau d'abord (nouvelle version), cache en secours hors-ligne.
  event.respondWith(fetch(event.request).catch(() => caches.match("/index.html")));
});
