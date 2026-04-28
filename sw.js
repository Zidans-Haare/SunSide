// SunSide Service Worker — App-Shell-Caching
// Pyodide-Runtime und Tile-Server bleiben Network-First (zu groß / dynamisch).
const CACHE = "sunside-shell-v4";
const SHELL = [
  "./",
  "./index.html",
  "./app.js",
  "./manifest.webmanifest",
  "./assets/icon.svg",
  "./assets/icon-maskable.svg",
  "./sunside/__init__.py",
  "./sunside/models.py",
  "./sunside/browser_api.py",
  "./sunside/weather.py",
  "./sunside/sun_analysis/__init__.py",
  "./sunside/sun_analysis/analyzer.py",
  "./sunside/sun_analysis/calculator.py",
  "./sunside/sun_analysis/sampler.py",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  const isShell = url.origin === self.location.origin;
  const isPyodide = url.host.includes("cdn.jsdelivr.net") && url.pathname.includes("/pyodide/");
  const isLeaflet = url.host === "unpkg.com" && url.pathname.includes("/leaflet@");

  if (isShell) {
    // cache-first for shell
    event.respondWith(
      caches.match(req).then((cached) => cached || fetch(req).then((resp) => {
        if (resp.ok) {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return resp;
      }).catch(() => cached))
    );
    return;
  }

  if (isPyodide || isLeaflet) {
    // stale-while-revalidate for runtime libs
    event.respondWith(
      caches.open(CACHE).then(async (cache) => {
        const cached = await cache.match(req);
        const networkPromise = fetch(req).then((resp) => {
          if (resp.ok) cache.put(req, resp.clone());
          return resp;
        }).catch(() => null);
        return cached || (await networkPromise) || new Response("", { status: 504 });
      })
    );
  }
  // everything else (Nominatim, Overpass, Open-Meteo, tiles): default network
});
