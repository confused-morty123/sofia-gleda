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
const CACHE_VERSION = "sofia-gleda-v7";

// Domain move (2026-10): on the old github.io address this worker retires itself.
// Once sofiagleda.com is the custom domain, GitHub redirects every request here,
// including the browser's update check for this file, and a service worker never
// follows a redirect for its own script: an old worker would stay installed for
// good, serving a frozen copy offline. So before the switch the worker on
// github.io deletes its own caches and unregisters, and the page there no longer
// registers it (scripts/build_index.py). On any other host it works as before.
const RETIRED = /(^|\.)github\.io$/.test(self.location.hostname);
const SHELL_CACHE = CACHE_VERSION + "-shell";
const ASSET_CACHE = CACHE_VERSION + "-assets";

// Precache the app shell so a first offline load still works.
const SHELL = [
  "./",
  "index.html",
  "manifest.webmanifest",
  "fonts/fonts.css",
  "logo.svg",
  "icons/icon.svg",
  "icons/icon-192.png",
  "icons/icon-512.png",
  "icons/icon-maskable-512.png",
];

// Local theatre posters (webapp/posters/<id>.<ext>) and the font files are
// deliberately NOT precached here: they are matched at fetch time instead —
// posters by request destination (image), the self-hosted fonts (fonts/*.woff2)
// as same-origin static files — so adding a venue, a poster or a font subset needs
// no edit to this list. Bumping CACHE_VERSION above is enough to refresh them.
// Theatre artwork comes from whichever venue published it (nationaltheatre.bg,
// theatre199.org, toplocentrala.bg, mlt.bg and a dozen more), so images are matched
// by destination, never by a hand-kept host list. No other host is contacted: the
// webfonts used to come from fonts.googleapis.com / fonts.gstatic.com (until v7).

self.addEventListener("install", (event) => {
  if (RETIRED) { self.skipWaiting(); return; }
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL))
      .catch(() => {})            // a single 404 must not abort the whole install
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  // Only this site's caches: github.io is shared by every project on the account.
  const ours = (k) => k.startsWith("sofia-gleda");
  if (RETIRED) {
    event.waitUntil(
      caches.keys()
        .then((keys) => Promise.all(keys.filter(ours).map((k) => caches.delete(k))))
        .then(() => self.registration.unregister())
    );
    return;
  }
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => ours(k) && !k.startsWith(CACHE_VERSION)).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// The page is cached under its path alone: ?film= / ?show= / ?event= links all load
// the same document, and keying by the full URL kept one ~1.5 MB copy of the page
// per shared link, never cleared (until v7).
function pageKey(request) {
  const u = new URL(request.url);
  u.search = "";
  u.hash = "";
  return u.href;
}

// Network-first: fresh when online, cached when not.
async function networkFirst(request) {
  const key = pageKey(request);
  try {
    const fresh = await fetch(request);
    if (fresh && fresh.ok) {
      const cache = await caches.open(SHELL_CACHE);
      cache.put(key, fresh.clone());
    }
    return fresh;
  } catch (err) {
    const cached = await caches.match(key);
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
      if (resp && (resp.ok || resp.type === "opaque")) cache.put(request, resp.clone()).catch(() => {});
      return resp;
    })
    .catch(() => null);
  return cached || (await network) || Response.error();
}

self.addEventListener("fetch", (event) => {
  if (RETIRED) return;      // straight to the network
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Navigations and this site's HTML documents: network-first. (Same origin only: a
  // poster URL on another host that ends in "/" is an image, not our page.)
  if (req.mode === "navigate" ||
      (url.origin === self.location.origin &&
       ((req.destination === "" && url.pathname.endsWith(".html")) || url.pathname.endsWith("/")))) {
    event.respondWith(networkFirst(req));
    return;
  }

  // Every image, wherever it is published: serve from cache instantly and refresh
  // in the background.
  if (req.destination === "image") {
    event.respondWith(staleWhileRevalidate(req));
    return;
  }

  // Everything same-origin static (icons, fonts, etc.): cache-first with network fill.
  if (url.origin === self.location.origin) {
    event.respondWith(
      caches.match(req).then((cached) => cached || staleWhileRevalidate(req))
    );
  }
});
