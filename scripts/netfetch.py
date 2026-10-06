#!/usr/bin/env python3
"""Sofia Gleda — one hardened HTTP layer for every scraper.

Why this exists: the 2026-09-16 refresh reported 13 of 13 sources unreachable
and silently kept week-old data. The build log showed a single 25-second read
timeout against programata.bg and one TLS hostname mismatch — one slow response
and one broken certificate were enough to freeze the whole dataset, because the
old fetch() tried each URL exactly once with a bot-shaped User-Agent.

So every request now goes through here:

  * a browser-shaped header set. `SofiaGleda/1.0 (personal programme aggregator)`
    is an honest UA and a red rag to the CDNs in front of these sites, which
    tarpit rather than refuse — which is exactly what a read timeout looks like.
  * retries with backoff, on connection errors, timeouts, 429 and 5xx alike,
    because "slow once" must not mean "gone".
  * separate connect and read timeouts: a slow page is worth waiting for, an
    unroutable host is not.
  * per-host politeness, so twelve cinema pages on one host do not arrive as a
    burst that earns us a rate limit.
  * a named exemption list for hosts with genuinely broken TLS. tickets.ndk.bg
    serves a certificate that does not cover its own name; these are public
    programme pages with no credentials and nothing to steal, so we read them
    with verification off rather than losing the venue. Nothing else is exempt.
  * a diagnostic record of every attempt, so one workflow run tells you which
    source failed and how, instead of "unreachable".

Use it as a singleton:

    from netfetch import Fetcher
    net = Fetcher()
    soup = net.soup(url)            # BeautifulSoup or None
    data = net.json(url)            # parsed JSON or None
    net.print_report()              # per-host, per-URL outcome table
"""
from __future__ import annotations
import json as _json
import sys, time, urllib.parse
from collections import defaultdict

import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except ImportError:                                   # very old urllib3
    from requests.packages.urllib3.util.retry import Retry  # type: ignore

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# A real Chrome on a real Mac. Every one of these sites serves this fine.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/129.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "bg-BG,bg;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}

# Hosts whose certificate does not validate through no fault of ours.
# Keep this list as short as the evidence requires, and say why for each.
TLS_EXEMPT = {
    "tickets.ndk.bg": "certificate does not cover its own hostname (2026-09)",
    "www.tba.art.bg": "self-signed certificate; posters live only on this host "
                      "(Театър Българска армия, 2026-10)",
    "tba.art.bg": "self-signed certificate (Театър Българска армия, 2026-10)",
}

DEFAULT_CONNECT_TIMEOUT = 15
DEFAULT_READ_TIMEOUT = 45
DEFAULT_ATTEMPTS = 4
DEFAULT_HOST_DELAY = 1.2


class Fetcher:
    def __init__(self, attempts=DEFAULT_ATTEMPTS, host_delay=DEFAULT_HOST_DELAY,
                 connect_timeout=DEFAULT_CONNECT_TIMEOUT,
                 read_timeout=DEFAULT_READ_TIMEOUT, verbose=True,
                 budget_seconds=None):
        self.attempts = attempts
        self.host_delay = host_delay
        self.timeout = (connect_timeout, read_timeout)
        self.verbose = verbose
        self._last_hit: dict[str, float] = {}
        self.log: list[dict] = []
        # A whole-run wall-clock budget. Retries are what make a dead host
        # expensive; without a ceiling, 118 lookups against a host that is down
        # turn a five-minute job into an hour-long one.
        self.budget_seconds = budget_seconds
        self._t0 = time.monotonic()

        self.session = requests.Session()
        self.session.headers.update(BROWSER_HEADERS)
        # Transport-level retries handle the reconnect; the loop in get()
        # handles the slow-response case, which Retry does not see as failure.
        retry = Retry(total=2, connect=2, read=2, backoff_factor=1.0,
                      status_forcelist=(408, 425, 429, 500, 502, 503, 504),
                      allowed_methods=frozenset(["GET", "HEAD"]),
                      raise_on_status=False, respect_retry_after_header=True)
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def budget_spent(self):
        return (self.budget_seconds is not None
                and time.monotonic() - self._t0 > self.budget_seconds)

    # ------------------------------------------------------------------ core
    def _polite(self, host):
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.host_delay - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def get(self, url, referer=None, accept=None, attempts=None):
        """Return a Response (2xx) or None. Never raises."""
        host = urllib.parse.urlsplit(url).hostname or ""
        verify = host not in TLS_EXEMPT
        headers = {}
        if referer:
            headers["Referer"] = referer
        elif host:
            headers["Referer"] = f"https://{host}/"
        if accept:
            headers["Accept"] = accept

        tries = attempts or self.attempts
        last_err = None
        started = time.monotonic()
        for n in range(1, tries + 1):
            self._polite(host)
            try:
                r = self.session.get(url, headers=headers, timeout=self.timeout,
                                     verify=verify, allow_redirects=True)
                if r.status_code < 400:
                    self._record(url, host, "ok", r.status_code,
                                 time.monotonic() - started, n, None)
                    return r
                last_err = f"HTTP {r.status_code}"
                # 404 and 410 are answers, not failures; retrying is just rude.
                if r.status_code in (404, 410):
                    break
            except requests.exceptions.SSLError as e:
                last_err = f"TLS: {self._short(e)}"
                break                                  # retrying will not help
            except requests.exceptions.RequestException as e:
                last_err = self._short(e)
            if n < tries:
                time.sleep(min(2.0 * n * n, 20))       # 2s, 8s, 18s
        self._record(url, host, "fail", None, time.monotonic() - started,
                     tries, last_err)
        if self.verbose:
            print(f"  ! {url}: {last_err}", file=sys.stderr, flush=True)
        return None

    @staticmethod
    def _short(e):
        s = str(e).replace("\n", " ")
        return (s[:160] + "…") if len(s) > 160 else s

    def _record(self, url, host, outcome, status, seconds, attempts, error):
        self.log.append({"url": url, "host": host, "outcome": outcome,
                         "status": status, "seconds": round(seconds, 1),
                         "attempts": attempts, "error": error})

    # ------------------------------------------------------------- shortcuts
    def text(self, url, **kw):
        r = self.get(url, **kw)
        return r.text if r is not None else None

    def download(self, url, dest, **kw):
        """Fetch an image to `dest` (pathlib.Path). Returns the Content-Type on
        success, or None. Refuses to write anything that is not actually an
        image, so a host that answers a 404 with an HTML page cannot leave a
        broken file behind."""
        kw.setdefault("accept", "image/avif,image/webp,image/*,*/*;q=0.8")
        r = self.get(url, **kw)
        if r is None:
            return None
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        body = r.content
        if not body:
            return None
        if not ctype.startswith("image/"):
            # Trust the magic bytes over a mislabelled header, but refuse HTML.
            head = body[:16]
            looks_image = (head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n"
                           or head[:4] == b"RIFF" or head[:6] in (b"GIF87a", b"GIF89a"))
            if not looks_image:
                if self.verbose:
                    print(f"  ! {url}: not an image ({ctype or 'no content-type'})",
                          file=sys.stderr, flush=True)
                return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return ctype or "image/*"

    def soup(self, url, parser="lxml", **kw):
        r = self.get(url, **kw)
        if r is None:
            return None
        from bs4 import BeautifulSoup
        return BeautifulSoup(r.text, parser)

    def json(self, url, **kw):
        kw.setdefault("accept", "application/json,text/plain,*/*")
        r = self.get(url, **kw)
        if r is None:
            return None
        try:
            return r.json()
        except ValueError:
            try:
                return _json.loads(r.text)
            except Exception:
                self.log[-1]["outcome"] = "fail"
                self.log[-1]["error"] = "response was not JSON"
                return None

    def head_ok(self, url, **kw):
        """Cheap existence probe that tolerates servers refusing HEAD."""
        host = urllib.parse.urlsplit(url).hostname or ""
        self._polite(host)
        try:
            r = self.session.head(url, timeout=self.timeout,
                                  verify=host not in TLS_EXEMPT,
                                  allow_redirects=True)
            if r.status_code == 405:
                r = self.session.get(url, timeout=self.timeout, stream=True,
                                     verify=host not in TLS_EXEMPT)
                r.close()
            return r.status_code < 400
        except requests.exceptions.RequestException:
            return False

    # ----------------------------------------------------------- diagnostics
    def summary(self):
        by_host = defaultdict(lambda: {"ok": 0, "fail": 0, "seconds": 0.0,
                                       "errors": []})
        for e in self.log:
            h = by_host[e["host"]]
            h[e["outcome"]] += 1
            h["seconds"] += e["seconds"]
            if e["outcome"] == "fail" and e["error"] and len(h["errors"]) < 3:
                h["errors"].append(e["error"])
        return {h: {**v, "seconds": round(v["seconds"], 1)}
                for h, v in sorted(by_host.items())}

    def print_report(self, stream=sys.stdout):
        print("\n=== network report ===", file=stream)
        print(f"  {'host':34s} {'ok':>4s} {'fail':>5s} {'total s':>8s}  notes",
              file=stream)
        for host, v in self.summary().items():
            note = v["errors"][0] if v["errors"] else ""
            print(f"  {host:34s} {v['ok']:>4d} {v['fail']:>5d} "
                  f"{v['seconds']:>8.1f}  {note[:70]}", file=stream)
