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
    burst that earns us a rate limit — and a host's robots.txt Crawl-delay
    (CRAWL_DELAY: cinelibri.com asks for 10 s) is respected, retries included.
  * a named exemption list for hosts with genuinely broken TLS. tickets.ndk.bg
    serves a certificate that does not cover its own name; these are public
    programme pages with no credentials and nothing to steal, so we read them
    with verification off rather than losing the venue. Nothing else is exempt.
  * a diagnostic record of every attempt, so one workflow run tells you which
    source failed and how, instead of "unreachable".
  * REAL time budgets (2026-10-09 audit, CODE-03: the old budget was never
    checked, and a dead host cost 36 s per URL — 40 minutes for theatre.art.bg's
    67 day pages, past the 30-minute step timeout). Each URL gets at most
    URL_BUDGET seconds, retries and back-off included; each host HOST_BUDGET
    for the run; a host that fails HOST_FAIL_LIMIT URLs in a row is closed for
    the run; `with net.deadline(s):` bounds one step (one venue); and the
    whole-run budget_seconds is enforced. A refused request returns None at
    once, so the caller's keep-previous rule takes over.
  * blocking is recognised: 401/403/429 or a WAF challenge page (Cloudflare,
    Sucuri, Imunify360, DDoS-Guard) is recorded as `blocked` in the log, never
    retried, and never parsed as a programme.

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
# Time budgets (CODE-03). A healthy run spends at most ~160 s on one host
# (cinemacity.bg: 134 day payloads), so HOST_BUDGET leaves room for a slow day
# while a dead host costs at most HOST_FAIL_LIMIT x URL_BUDGET.
URL_BUDGET = 75            # one URL: every attempt, timeout and back-off included
HOST_BUDGET = 420          # one host, the whole run
HOST_FAIL_LIMIT = 2        # failed URLs in a row before a host is closed for the run
MIN_TRY_SECONDS = 3        # an attempt with less time left than this is not made
RETRY_STATUS = (408, 425, 429, 500, 502, 503, 504)   # worth another attempt
BLOCK_STATUS = (401, 403, 429)                       # "not from this network / client"
RETRY_AFTER_MAX = 30       # a Retry-After longer than this is not waited for

# A page that is a WAF's challenge or block page, not the programme asked for.
# Matched in the first few KB, lower-cased. NOT "/cdn-cgi/challenge-platform/":
# Cloudflare injects that script into ordinary pages too (mlt.bg, 2026-10-09).
CHALLENGE_SIGNS = (
    ("<title>just a moment...</title>", "Cloudflare challenge"),
    ("cf_chl_opt", "Cloudflare challenge"),
    ("attention required! | cloudflare", "Cloudflare block page"),
    ("sucuri website firewall", "Sucuri firewall"),
    ("imunify360 bot-protection", "Imunify360 bot protection"),
    ("<title>ddos-guard</title>", "DDoS-Guard"),
)


def challenge_signature(resp):
    """The WAF a response is a challenge/block page of, or None."""
    try:
        if (resp.headers.get("cf-mitigated") or "").lower() == "challenge":
            return "Cloudflare challenge"
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if ctype and "html" not in ctype:
            return None
        head = (resp.content or b"")[:8192].decode("utf-8", errors="ignore").lower()
    except Exception:
        return None
    for sign, name in CHALLENGE_SIGNS:
        if sign in head:
            return name
    return None

# Hosts whose robots.txt asks for a Crawl-delay (seconds between requests).
# For these the gap is counted from the END of the previous request, retries
# included, and the transport layer does not retry on its own (its 1-2 s
# backoff would break the delay). cinelibri.com: "Crawl-delay: 10" (2026-10-09).
CRAWL_DELAY = {
    "www.cinelibri.com": 10.0,
    "cinelibri.com": 10.0,
}


class Fetcher:
    def __init__(self, attempts=DEFAULT_ATTEMPTS, host_delay=DEFAULT_HOST_DELAY,
                 connect_timeout=DEFAULT_CONNECT_TIMEOUT,
                 read_timeout=DEFAULT_READ_TIMEOUT, verbose=True,
                 budget_seconds=None, url_budget=URL_BUDGET, host_budget=HOST_BUDGET,
                 host_fail_limit=HOST_FAIL_LIMIT):
        self.attempts = attempts
        self.host_delay = host_delay
        self.timeout = (connect_timeout, read_timeout)
        self.verbose = verbose
        self._last_hit: dict[str, float] = {}
        self.log: list[dict] = []
        # A whole-run wall-clock budget. Retries are what make a dead host
        # expensive; without a ceiling, 118 lookups against a host that is down
        # turn a five-minute job into an hour-long one. Enforced in every request.
        self.budget_seconds = budget_seconds
        self.url_budget = url_budget
        self.host_budget = host_budget
        self.host_fail_limit = host_fail_limit
        self._t0 = time.monotonic()
        self._host_spent = defaultdict(float)      # host -> seconds spent on it
        self._host_streak = defaultdict(int)       # host -> failed URLs in a row
        self.closed: dict[str, str] = {}           # host -> why it is closed for the run
        self._step_end = None                      # monotonic end of the current deadline()

        self.session = requests.Session()
        self.session.headers.update(BROWSER_HEADERS)
        # No transport-level retries: urllib3 retried read timeouts and waited
        # out any Retry-After on its own, which no budget could bound (3 x 60 s
        # per attempt). Every retry happens in get(), inside the budgets.
        adapter = HTTPAdapter(max_retries=0, pool_connections=8, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        for h in CRAWL_DELAY:                      # longest prefix wins in requests
            for scheme in ("https://", "http://"):
                self.session.mount(f"{scheme}{h}/", HTTPAdapter(max_retries=0))

    def budget_spent(self):
        return (self.budget_seconds is not None
                and time.monotonic() - self._t0 > self.budget_seconds)

    # --------------------------------------------------------------- budgets
    class _Deadline:
        def __init__(self, fetcher, seconds):
            self.f, self.seconds, self.saved = fetcher, seconds, None

        def __enter__(self):
            self.saved = self.f._step_end
            end = time.monotonic() + self.seconds
            self.f._step_end = end if self.saved is None else min(self.saved, end)
            return self.f

        def __exit__(self, *exc):
            self.f._step_end = self.saved
            return False

    def deadline(self, seconds):
        """`with net.deadline(300):` — no request inside the block may run past
        300 s from now (nested blocks keep the earlier end). One venue's fetch is
        one block, so one slow venue cannot eat the others' time."""
        return Fetcher._Deadline(self, seconds)

    def _time_left(self, host, url_end=None):
        """Seconds any request to `host` may still take (None = unbounded)."""
        now = time.monotonic()
        lefts = [self.host_budget - self._host_spent[host]] if self.host_budget is not None else []
        if self.budget_seconds is not None:
            lefts.append(self.budget_seconds - (now - self._t0))
        if self._step_end is not None:
            lefts.append(self._step_end - now)
        if url_end is not None:
            lefts.append(url_end - now)
        return min(lefts) if lefts else None

    def _refusal(self, host):
        """Why no request to `host` may be made now, or None."""
        if host in self.closed:
            return f"skipped: {host} closed for this run ({self.closed[host]})"
        if self.budget_spent():
            return "skipped: the run's network budget is spent"
        if self._step_end is not None and time.monotonic() >= self._step_end:
            return "skipped: this step's time budget is spent"
        if self.host_budget is not None and self._host_spent[host] >= self.host_budget:
            self.closed[host] = f"{self.host_budget:.0f} s host budget spent"
            return f"skipped: {host} closed for this run ({self.closed[host]})"
        return None

    def _settle(self, host, ok, answered=False, err=None):
        """Bookkeeping after one URL: a success or an answer (404) resets the
        host's failure streak; HOST_FAIL_LIMIT failures in a row close it."""
        if ok or answered:
            self._host_streak[host] = 0
            return
        self._host_streak[host] += 1
        if self.host_fail_limit and self._host_streak[host] >= self.host_fail_limit:
            self.closed.setdefault(host, f"{self._host_streak[host]} failed URLs in a row, last: {err}")

    def blocked_since(self, start=0):
        """[(url, host, reason)] of requests from log index `start` on that the
        host refused as blocked (401/403/429 or a challenge page)."""
        return [(e["url"], e["host"], e["blocked"]) for e in self.log[start:] if e.get("blocked")]

    # ------------------------------------------------------------------ core
    def _polite(self, host):
        """Sleep until `host` may be asked again; returns the seconds slept."""
        last, waited = self._last_hit.get(host), 0.0
        if last is not None:
            wait = max(self.host_delay, CRAWL_DELAY.get(host, 0.0)) - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
                waited = wait
        self._last_hit[host] = time.monotonic()
        return waited

    def _done(self, host):
        """A crawl-delay host's gap runs from the end of the previous request."""
        if host in CRAWL_DELAY:
            self._last_hit[host] = time.monotonic()

    def get(self, url, referer=None, accept=None, attempts=None, headers=None):
        """Return a Response (2xx) or None. Never raises. `headers` adds or
        overrides request headers (e.g. X-Requested-With for an AJAX fragment)."""
        host = urllib.parse.urlsplit(url).hostname or ""
        verify = host not in TLS_EXEMPT
        extra, headers = headers, {}
        if referer:
            headers["Referer"] = referer
        elif host:
            headers["Referer"] = f"https://{host}/"
        if accept:
            headers["Accept"] = accept
        if extra:
            headers.update(extra)

        tries = attempts or self.attempts
        refused = self._refusal(host)
        if refused:
            self._record(url, host, "skipped", None, 0.0, 0, refused)
            return None
        last_err, status, blocked, answered, n = None, None, None, False, 0
        started = time.monotonic()
        url_end = started + self.url_budget if self.url_budget else None
        crawl_wait = 0.0                               # robots.txt Crawl-delay waits, not charged
        try:
            for n in range(1, tries + 1):
                left = self._time_left(host, url_end)
                if left is not None and left < MIN_TRY_SECONDS:
                    last_err = (last_err + "; " if last_err else "") + "time budget spent"
                    n -= 1
                    break
                waited = self._polite(host)
                if host in CRAWL_DELAY:
                    crawl_wait += waited
                left = self._time_left(host, url_end)
                timeout = self.timeout if left is None else (
                    max(1.0, min(self.timeout[0], left)), max(1.0, min(self.timeout[1], left)))
                r = None
                try:
                    r = self.session.get(url, headers=headers, timeout=timeout,
                                         verify=verify, allow_redirects=True)
                except requests.exceptions.SSLError as e:
                    last_err = f"TLS: {self._short(e)}"
                    break                              # retrying will not help
                except requests.exceptions.RequestException as e:
                    last_err = self._short(e)
                finally:
                    self._done(host)
                if r is not None:
                    status = r.status_code
                    sign = challenge_signature(r)
                    if status < 400 and not sign:
                        self._settle(host, True)
                        self._record(url, host, "ok", status, time.monotonic() - started, n, None)
                        return r
                    if sign:
                        blocked = f"challenge page ({sign}, HTTP {status})"
                        last_err = blocked
                        break                          # a challenge is never solved here
                    last_err = f"HTTP {status}"
                    if status in BLOCK_STATUS:
                        blocked = f"HTTP {status}"
                    # 404 and 410 are answers, not failures; retrying is just rude.
                    if status in (404, 410):
                        answered = True
                        break
                    # 401, 403 and other 4xx are answers too: this client is refused.
                    if status not in RETRY_STATUS:
                        break
                if n < tries:
                    pause = min(2.0 * n * n, 20)       # 2s, 8s, 18s
                    ra = (r.headers.get("Retry-After") or "").strip() if r is not None else ""
                    if ra.isdigit():
                        if int(ra) > RETRY_AFTER_MAX:
                            last_err += f" (Retry-After {ra} s — not waited for)"
                            break
                        pause = max(pause, float(ra))
                    left = self._time_left(host, url_end)
                    if left is not None and pause + MIN_TRY_SECONDS > left:
                        last_err = (last_err or "failed") + "; time budget spent"
                        break
                    time.sleep(pause)
        finally:
            # A host's budget measures the host: the pacing its robots.txt asks for
            # (cinelibri.com: 10 s between requests) is not charged to it, or a
            # crawl-delay host would close after ~40 requests. The run / step
            # budgets still bound the wall time those waits take.
            self._host_spent[host] += time.monotonic() - started - crawl_wait
        if n == 0:                                     # no attempt made: a budget, not the host
            self._record(url, host, "skipped", None, time.monotonic() - started, 0,
                         "skipped: " + (last_err or "time budget spent"))
            return None
        self._settle(host, False, answered, last_err)
        self._record(url, host, "fail", status, time.monotonic() - started,
                     n, last_err, blocked)
        if self.verbose:
            print(f"  ! {url}: {last_err}", file=sys.stderr, flush=True)
        return None

    def post(self, url, data, referer=None, headers=None):
        """One polite form POST (no retries: a POST is not assumed idempotent).
        Used only for Cine Grand's cinema selection, which merely sets a session
        cookie. Returns a Response (status < 400) or None. Never raises."""
        host = urllib.parse.urlsplit(url).hostname or ""
        hdrs = {"Referer": referer or f"https://{host}/"}
        if headers:
            hdrs.update(headers)
        refused = self._refusal(host)
        if refused:
            self._record(url, host, "skipped", None, 0.0, 0, refused)
            return None
        started = time.monotonic()
        self._polite(host)
        left = self._time_left(host, started + self.url_budget if self.url_budget else None)
        timeout = self.timeout if left is None else (
            max(1.0, min(self.timeout[0], left)), max(1.0, min(self.timeout[1], left)))
        try:
            r = self.session.post(url, data=data, headers=hdrs, timeout=timeout,
                                  verify=host not in TLS_EXEMPT, allow_redirects=True)
        except requests.exceptions.RequestException as e:
            self._host_spent[host] += time.monotonic() - started
            self._settle(host, False, err=self._short(e))
            self._record(url, host, "fail", None, time.monotonic() - started, 1, self._short(e))
            return None
        self._host_spent[host] += time.monotonic() - started
        ok = r.status_code < 400
        self._settle(host, ok, answered=r.status_code in (404, 410), err=f"HTTP {r.status_code}")
        self._record(url, host, "ok" if ok else "fail", r.status_code,
                     time.monotonic() - started, 1, None if ok else f"HTTP {r.status_code}",
                     f"HTTP {r.status_code}" if r.status_code in BLOCK_STATUS else None)
        return r if ok else None

    @staticmethod
    def _short(e):
        s = str(e).replace("\n", " ")
        return (s[:160] + "…") if len(s) > 160 else s

    def _record(self, url, host, outcome, status, seconds, attempts, error, blocked=None):
        self.log.append({"url": url, "host": host, "outcome": outcome,
                         "status": status, "seconds": round(seconds, 1),
                         "attempts": attempts, "error": error, "blocked": blocked})

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
        if self._refusal(host):
            return False
        self._polite(host)
        started = time.monotonic()
        left = self._time_left(host, started + self.url_budget if self.url_budget else None)
        timeout = self.timeout if left is None else (
            max(1.0, min(self.timeout[0], left)), max(1.0, min(self.timeout[1], left)))
        try:
            r = self.session.head(url, timeout=timeout,
                                  verify=host not in TLS_EXEMPT,
                                  allow_redirects=True)
            if r.status_code == 405:
                self._done(host)
                self._polite(host)
                r = self.session.get(url, timeout=timeout, stream=True,
                                     verify=host not in TLS_EXEMPT)
                r.close()
            return r.status_code < 400
        except requests.exceptions.RequestException:
            return False
        finally:
            self._done(host)
            self._host_spent[host] += time.monotonic() - started

    # ----------------------------------------------------------- diagnostics
    def summary(self):
        by_host = defaultdict(lambda: {"ok": 0, "fail": 0, "skipped": 0, "seconds": 0.0,
                                       "errors": [], "blocked": None, "closed": None})
        for e in self.log:
            h = by_host[e["host"]]
            h[e["outcome"]] = h.get(e["outcome"], 0) + 1
            h["seconds"] += e["seconds"]
            if e["outcome"] == "fail" and e["error"] and len(h["errors"]) < 3:
                h["errors"].append(e["error"])
            if e.get("blocked") and not h["blocked"]:
                h["blocked"] = e["blocked"]
        for host, why in self.closed.items():
            if host in by_host:
                by_host[host]["closed"] = why
        return {h: {**v, "seconds": round(v["seconds"], 1)}
                for h, v in sorted(by_host.items())}

    def print_report(self, stream=sys.stdout):
        print("\n=== network report ===", file=stream)
        print(f"  {'host':34s} {'ok':>4s} {'fail':>5s} {'skip':>5s} {'total s':>8s}  notes",
              file=stream)
        for host, v in self.summary().items():
            note = v["errors"][0] if v["errors"] else ""
            if v.get("closed"):
                note = f"CLOSED ({v['closed']}) " + note
            print(f"  {host:34s} {v['ok']:>4d} {v['fail']:>5d} {v.get('skipped', 0):>5d} "
                  f"{v['seconds']:>8.1f}  {note[:90]}", file=stream)
