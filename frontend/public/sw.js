// Service worker minimal : met en cache la coquille de l'application (HTML/JS/CSS/polices) pour un
// démarrage instantané. JAMAIS les réponses /api : des données bancaires n'ont rien à faire dans un cache.
const CACHE = "bank-shell-v2";

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

// Notifications (Web Push) : Bank chiffre chaque message pour cet appareil ; ici on l'affiche et on ouvre l'écran.
self.addEventListener("push", (event) => {
  let data = { title: "Bank", body: "Du nouveau dans Bank.", url: "/", tag: "bank" };
  try {
    if (event.data) data = { ...data, ...event.data.json() };
  } catch {
    /* corps illisible : la notification générique suffit */
  }
  const body = String(data.body || "").slice(0, 600);
  event.waitUntil(
    self.registration.showNotification(String(data.title || "Bank").slice(0, 60), {
      body,
      tag: String(data.tag || "bank").slice(0, 40),
      icon: "/icon.svg",
      badge: "/icon.svg",
      lang: "fr",
      data: { url: String(data.url || "/") },
      renotify: false,
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = String((event.notification.data && event.notification.data.url) || "/");
  // Jamais ailleurs que sur Bank : une adresse étrangère retombe sur l'accueil.
  const target = url.startsWith(self.location.origin) || url.startsWith("/") ? url : "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      const open = wins.find((w) => w.url.startsWith(self.location.origin));
      if (open) {
        open.navigate(target);
        return open.focus();
      }
      return self.clients.openWindow(target);
    }),
  );
});
