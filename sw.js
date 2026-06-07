// SunSide Service Worker - App-Shell-Caching.
// Pyodide und Leaflet werden stale-while-revalidate behandelt; API-/Tile-Daten
// bleiben network-first, damit unterwegs keine veralteten Routen erscheinen.
const CACHE = "sunside-shell-v33";
const RUNTIME_CACHE = "sunside-runtime-v33";
const SHELL = [
  "./",
  "./index.html",
  "./app.js",
  "./manifest.webmanifest",
  "./assets/icon.svg",
  "./assets/icon-maskable.svg",
  "./assets/icon-192.png",
  "./assets/icon-512.png",
  "./assets/icon-maskable-192.png",
  "./assets/icon-maskable-512.png",
  "./assets/apple-touch-icon-180.png",
  "./sunside/__init__.py",
  "./sunside/models.py",
  "./sunside/flight.py",
  "./sunside/browser_api.py",
  "./sunside/weather.py",
  "./sunside/sun_analysis/__init__.py",
  "./sunside/sun_analysis/analyzer.py",
  "./sunside/sun_analysis/calculator.py",
  "./sunside/sun_analysis/sampler.py",
];

const SHELL_PATHS = new Set(SHELL.map((path) => new URL(path, self.location).pathname));

function isShellAsset(url) {
  return url.origin === self.location.origin && SHELL_PATHS.has(url.pathname);
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) =>
      // Use individual adds so a single missing optional asset (e.g. a yet-to-be-generated
      // PNG icon) doesn't break the install of the entire shell.
      Promise.all(SHELL.map((url) => c.add(url).catch((err) => {
        console.warn("[sw] shell precache miss:", url, err?.message || err);
      })))
    )
    // No skipWaiting() here: we want the client to opt-in via SKIP_WAITING message
    // so the user can decide when to reload. The client shows an "Update verfuegbar" prompt.
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE && k !== RUNTIME_CACHE).map((k) => caches.delete(k)));
    if (self.registration.navigationPreload) {
      try { await self.registration.navigationPreload.enable(); } catch {}
    }
    await self.clients.claim();
  })());
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  const isPyodide = url.host.includes("cdn.jsdelivr.net") && url.pathname.includes("/pyodide/");
  const isLeaflet = url.host === "unpkg.com" && url.pathname.includes("/leaflet@");

  if (req.mode === "navigate") {
    event.respondWith((async () => {
      try {
        const preload = await event.preloadResponse;
        if (preload) return preload;
        return await fetch(req);
      } catch {
        const cached = await caches.match("./index.html");
        return cached || new Response("Offline", { status: 503, headers: { "Content-Type": "text/plain" } });
      }
    })());
    return;
  }

  if (isShellAsset(url)) {
    event.respondWith(
      caches.match(req, { ignoreSearch: true }).then((cached) => cached || fetch(req).then((resp) => {
        if (resp.ok) {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return resp;
      }).catch(() => cached || new Response("", { status: 504, statusText: "Offline" })))
    );
    return;
  }

  if (isPyodide || isLeaflet) {
    event.respondWith(
      caches.open(RUNTIME_CACHE).then(async (cache) => {
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
