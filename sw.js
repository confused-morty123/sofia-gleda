/* Sofia Gleda — service worker.
 *
 * Two jobs:
 *   1. Make repeat visits instant and the app usable offline.
 *   2. Never trap the user on stale data — the weekly refresh must reach them.
 *
 * Strategy:
 *   - The page itself (index.html / navigations): NETWORK-FIRST. When online we
 *     always try the freshest copy first (so Sunday's new programme shows up),
 *     and fall back to the cached copy only if the network fails. This is what
 *     keeps the "updated weekly" promise honest.
 *   - Posters, fonts, other static assets: STALE-WHILE-REVALIDATE. Serve the
 *     cached copy immediately (fast), and quietly fetch a fresh one in the
 *     background for next time.
 *
 * Bump CACHE_VERSION whenever this file changes so old caches are cleared.
 */
const CACHE_VERSION = "sofia-gleda-v1";
const SHELL_CACHE = CACHE_VERSION + "-shell";
const ASSET_CACHE = CACHE_VERSION + "-assets";

// Precache the app shell so a first offline load still works.
const SHELL = [
  "./",
  "index.html",
  "manifest.webmanifest",
  "icons/icon.svg",
  "icons/icon-192.png",
  "icons/icon-512.png",
];

// Hosts whose responses we cache at runtime (posters + webfonts).
const ASSET_HOSTS = [
  "image.tmdb.org",
  "theatre.peakview.bg",
  "fonts.googleapis.com",
  "fonts.gstatic.com",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL))
      .catch(() => {})            // a single 404 must not abort the whole install
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => !k.startsWith(CACHE_VERSION)).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// Network-first: fresh when online, cached when not.
async function networkFirst(request) {
  try {
    const fresh = await fetch(request);
    if (fresh && fresh.ok) {
      const cache = await caches.open(SHELL_CACHE);
      cache.put(request, fresh.clone());
    }
    return fresh;
  } catch (err) {
    const cached = await caches.match(request);
    if (cached) return cached;
    // Last resort for a navigation with no cached match: the shell.
    const shell = await caches.match("index.html");
    if (shell) return shell;
    throw err;
  }
}

// Stale-while-revalidate: instant from cache, refresh in the background.
async function staleWhileRevalidate(request) {
  const cache = await caches.open(ASSET_CACHE);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then((resp) => {
      // Cache opaque (cross-origin) and ok responses alike.
      if (resp && (resp.ok || resp.type === "opaque")) cache.put(request, resp.clone());
      return resp;
    })
    .catch(() => null);
  return cached || network || fetch(request);
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Navigations and the HTML document: network-first.
  if (req.mode === "navigate" ||
      (req.destination === "" && url.pathname.endsWith(".html")) ||
      url.pathname === "/" || url.pathname.endsWith("/")) {
    event.respondWith(networkFirst(req));
    return;
  }

  // Posters and webfonts: stale-while-revalidate.
  if (ASSET_HOSTS.includes(url.hostname)) {
    event.respondWith(staleWhileRevalidate(req));
    return;
  }

  // Everything same-origin static (icons, etc.): cache-first with network fill.
  if (url.origin === self.location.origin) {
    event.respondWith(
      caches.match(req).then((cached) => cached || staleWhileRevalidate(req))
    );
  }
});
