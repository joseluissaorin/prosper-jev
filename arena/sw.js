/* La arena sin red: la interfaz y la demostración se sirven desde la caché; el directo y la llamada (WebSocket) van
 * siempre por la red. Primero la caché y, por detrás, se refresca (stale-while-revalidate). */
const VERSION = "arena-v3";
const SHELL = ["./", "index.html", "style.css", "app.js", "llamar.js", "config.js", "demo.json", "manifest.webmanifest",
               "icons/icono-192.png", "icons/icono-512.png", "icons/apple-touch-icon.png", "icons/favicon-32.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  const propio = url.origin === location.origin;
  const fuentes = url.hostname.endsWith("fonts.googleapis.com") || url.hostname.endsWith("fonts.gstatic.com");
  if (!propio && !fuentes) return;
  e.respondWith(caches.open(VERSION).then(async (c) => {
    const clave = propio && url.pathname.endsWith("/") ? "./" : req;
    const hit = await c.match(clave, { ignoreSearch: propio });
    const red = fetch(req).then((r) => { if (r.ok || r.type === "opaque") c.put(clave, r.clone()); return r; }).catch(() => hit);
    return hit || red;
  }));
});
