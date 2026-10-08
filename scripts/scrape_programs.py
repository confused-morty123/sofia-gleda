#!/usr/bin/env python3
"""Sofia Gleda — weekly programme scraper.

Re-fetches the source pages, rebuilds the showtime/performance tables and edits
them into index.html in place, between the SOFIA-DATA markers. Also writes
changes.json (the diff for this run) and refreshes SNAPSHOT.lastValidated /
changelog.

Guiding rule (the app's hard-won lesson): a dead or stale source must NEVER
empty a venue. Any venue we can't reach keeps its previous rows.

    python3 scripts/scrape_programs.py                 # edits index.html
    python3 scripts/scrape_programs.py --dry-run       # diff only, no write

Requires: requests, beautifulsoup4, lxml
"""
from __future__ import annotations
import argparse, json, os, re, sys, time, datetime as dt, pathlib
from collections import defaultdict
from dataclasses import dataclass, field

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("pip install requests beautifulsoup4 lxml")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from netfetch import Fetcher                    # shared hardened HTTP layer
import film_identity as FI                      # which film does a published title mean
import official_sources as OS                   # each cinema's own programme

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
CHANGES = ROOT / "changes.json"
# Minimal arthouse-film records minted from independent-cinema programmes (titles
# the static FILMS catalogue never carried). Persisted keep-previous and merged
# into FILMS by inject_films.py before the build gate.
CINEMA_FILMS = ROOT / "cinema_films.json"
# Minimal theatre-show records minted from venue programmes (productions the static
# SHOWS catalogue never carried). Same keep-previous discipline as the film sidecar;
# merged into SHOWS by inject_shows.py before the build gate.
THEATRE_SHOWS = ROOT / "theatre_shows.json"
# The previous refresh's report (committed). Its "official" section holds each
# venue's official row count, so a source that suddenly returns a handful of
# rows (markup half-broken) stops the scrape instead of thinning the app.
BUILD_REPORT = ROOT / "build_report.json"

# Headers, timeouts, retries and per-host politeness live in netfetch.py, so every
# scraper behaves the same way against the same fragile sources. The old
# self-identifying bot User-Agent plus a single 25s attempt is what turned one slow
# programata.bg response into "13 sources unreachable" for a fortnight.

# programata.bg is the cinema spine; append ?date= or you get a weeks-old cache.
CINEMA_SOURCES = {
    "cc-sofia":    "https://programata.bg/kino/kino-saloni/sofia/cinema-city-sofia/",
    "cc-paradise": "https://programata.bg/kino/kino-saloni/sofia/cinema-city-paradise/",
    "arena-mega":  "https://programata.bg/kino/kino-saloni/arena-mega-mall/",
    "arena-mall":  "https://programata.bg/kino/kino-saloni/sofia/arena-the-mall/",
    "cg-ring":     "https://programata.bg/kino/kino-saloni/sofia/cine-grand-sofia-ring-mall-2/",
    "cg-park":     "https://programata.bg/kino/kino-saloni/sofia/cine-grand-park-center/",
    "cineland":    "https://programata.bg/kino/kino-saloni/cineland-bulgaria-mall/",
    "odeon":       "https://programata.bg/kino/kino-saloni/sofia/odeon-cinema/",
    "g8":          "https://programata.bg/kino/kino-saloni/sofia/g8-cinema/",
    # Дом на киното's own site stopped exposing a parseable dated grid; programata
    # carries its full programme under the dom-na-kinoto slug (NOT kino-dom, which
    # is an unrelated hall), so we read it from there like the other halls.
    "dom-kino":    "https://programata.bg/kino/kino-saloni/sofia/dom-na-kinoto/",
    # Влайкова publishes a clean structured weekly grid on its own site; programata
    # has no data for it. Parsed by parse_vlaikova().
    "vlaikova":    "https://vlaikovacinema.com/",
    # Кино Люмиер is NDK's arthouse hall; it has no aggregator feed, so we read
    # NDK's own programme and keep only the "Люмиер" hall. Parsed by parse_lumiere().
    # Coverage is partial (NDK highlights a subset of screenings) — a known limit.
    "lumiere":     "https://www.ndk.bg/en/program",
}
# theatre.art.bg aggregates Sofia theatres; city 20 = Sofia. It silently falls
# back to *today* for a date it has no data for, so we trust only rows whose
# echoed date matches what we asked for (checked loosely by presence of times).
THEATRE_DAY_URL = "https://theatre.art.bg/?date={date}&city=20"

# Individual venue programme pages, consulted in addition to the aggregator.
# These are monthly/season listings (not per-date), scanned once for rows that
# carry BOTH a date (within the snapshot window) and a single time. A scraped
# title is only kept if it matches a show ALREADY in the catalogue — the scraper
# never invents a production, author or synopsis, so an unknown title is simply
# ignored. Venues whose sites publish no machine-readable dated programme
# (Derida — repertoire only; venues selling solely via theatre.art.bg) rely on
# the aggregator above and are intentionally omitted here.
THEATRE_VENUE_SOURCES = {
    "satira":      "https://satirata.bg/program",
    "natfiz":      "https://natfiz.bg/udt-mesechna-programa/",
    "iam":         "https://iamstudio.bg/programa/",
    "vazrazhdane": "https://theatrevazrajdane.bg/programa",
    "salzaismyah": "https://www.salzaismyah.bg/site/calendar",
    "new-ndk":     "https://tickets.ndk.bg",
    "atelie313":   "https://www.atelie313.com/programata1",
}
# Artvent is a producer/aggregator listing its own theatre programme.
ARTVENT_URL = "https://artvent.bg/teatar/artvent/programa"

# Venues whose OWN sites publish a machine-readable, dated programme that the
# generic scanner can't parse — each gets a dedicated parser below. Uncatalogued
# titles here are minted (a real, dated performance is never dropped), attributed
# to the venue id these point at.
THEATRE_OWN_SOURCES = {
    "th199":      "https://theatre199.org/bg/schedule",
    "toplo":      "https://toplocentrala.bg/program/performance",
    "zad-kanala": "https://zadkanala.bg/programa",
}
# theatre.art.bg theatre ids for venues that sell ONLY through the aggregator and
# whose own sites carry no scrapable dated programme (Сфумато routes everything
# through art.bg; Сити Марк's "own" domain is an unrelated property company). A
# day-aggregator row at one of these ids is match-or-minted — a real, dated,
# ticketed performance is never dropped. Every OTHER art.bg theatre stays
# match-only, so the SHOWS catalogue is never flooded with venues the app can't
# list. Ids read from each venue's kupi-bilet links (theatre=N).
THEATRE_ART_VENUE = {"14": "sfumato", "173": "citymark"}
# Natfiz publishes its monthly programme only as a single JPG poster, Нов театър
# НДК sells behind a login wall (tickets.ndk.bg), and Сцена Дерида serves a
# JavaScript-only shell — none expose machine-readable dated shows, so they are
# intentionally left to keep-previous rather than scraped (never fabricated).

TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
DATE_RE = re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\b")

# Allowlist of URL hosts that may appear in VLINKS for each venue.
# A link from programata.bg NEVER goes into VLINKS (it is a listing aggregator,
# not the venue's own ticketing site). Only links whose host is on this list for
# a given venue are stored as venue-specific ticket links.
VENUE_LINK_ALLOWLIST = {
    "cc-sofia":     ["cinemacity.bg"],
    "cc-paradise":  ["cinemacity.bg"],
    "arena-mega":   ["kinoarena.com"],
    "arena-mall":   ["kinoarena.com"],
    "cg-ring":      ["cinegrand.bg"],
    "cg-park":      ["cinegrand.bg"],
    "cineland":     ["cineland.bg"],
    "vlaikova":     ["vlaikovacinema.com", "embed.urboapp.com"],
    "lumiere":      ["ndk.bg", "epaygo.bg"],
    "dom-kino":     ["domnakinoto.com"],
    "odeon":        ["bnf.bg"],
    "g8":           ["g8cinema.com"],
}

# Regex to extract ticket-specific deep links from venue pages.
# vlaikova pages embed an urboapp widget; lumiere pages link to epaygo.
_URBO_RE   = re.compile(r'https?://embed\.urboapp\.com/[^\s"\'<>]+')
_EPAYGO_RE = re.compile(r'https://epaygo\.bg/\d+[^\s"\'<>]*')


@dataclass
class Diff:
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    changed: list = field(default_factory=list)
    unreachable: list = field(default_factory=list)   # the page never arrived
    stale: list = field(default_factory=list)         # it arrived, nothing parsed
    emptied: list = field(default_factory=list)        # read, no catalogue film on — stale rows dropped

    def empty(self):
        return not (self.added or self.removed or self.changed)

    def summary(self):
        return (f"{len(self.added)} added, {len(self.removed)} removed/cancelled, "
                f"{len(self.changed)} changed, {len(self.unreachable)} sources unreachable, "
                f"{len(self.stale)} returned nothing usable")


def fetch(url, session, attempts=None):
    """`session` is a netfetch.Fetcher: retries, browser headers, per-host
    politeness and the run's diagnostics all live there."""
    return session.soup(url, attempts=attempts)


def extract_array(src, name):
    m = re.search(rf"const\s+{name}\s*=\s*\[", src)
    if not m:
        raise KeyError(name)
    start = m.end() - 1
    depth, i, in_str, quote, esc = 0, start, False, "", False
    while i < len(src):
        c = src[i]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == quote: in_str = False
        elif c in "\"'": in_str, quote = True, c
        elif c == "[": depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return start, i + 1, src[start:i + 1]
        i += 1
    raise ValueError(f"unterminated array {name}")


def find_prop_array(src, prop):
    """Locate a `prop:[ ... ]` array (e.g. changelog:) by bracket matching,
    ignoring brackets that occur inside string literals."""
    m = re.search(rf'"?{prop}"?\s*:\s*\[', src)
    if not m:
        return None
    start = m.end() - 1
    depth, i, in_str, quote, esc = 0, start, False, "", False
    while i < len(src):
        c = src[i]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == quote: in_str = False
        elif c in "\"'": in_str, quote = True, c
        elif c == "[": depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1
    return None


def js_rows(literal):
    # Strip JS comments, but ONLY outside string literals — otherwise the "//"
    # in a "https://…" URL (e.g. the LINKS array) would be mistaken for a
    # line-comment and truncate the string.
    out, i, n = [], 0, len(literal)
    in_str, quote, esc = False, "", False
    while i < n:
        c = literal[i]
        if in_str:
            out.append(c)
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == quote: in_str = False
            i += 1
            continue
        if c in "\"'":
            in_str, quote = True, c
            out.append(c); i += 1; continue
        if c == "/" and i + 1 < n and literal[i + 1] == "/":
            i += 2
            while i < n and literal[i] != "\n": i += 1
            continue
        if c == "/" and i + 1 < n and literal[i + 1] == "*":
            i += 2
            while i + 1 < n and not (literal[i] == "*" and literal[i + 1] == "/"): i += 1
            i += 2
            continue
        out.append(c); i += 1
    txt = re.sub(r",(\s*[\]\}])", r"\1", "".join(out))
    return json.loads(txt)


def emit_rows(rows):
    """Compact single-line JSON to preserve the app's minified data block."""
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def scrape_cinema(venue_id, url, session, window, stats=None):
    """Return (title, venue_id, date, [times], detail_url) rows.

    programata cinema-hall pages embed the *whole* multi-day schedule in one
    page, listing each film's dates in long-form Bulgarian ("17 септември ...:
    14:20 | 19:30"). We parse those dates directly (fixing the old default-to-
    window[0] bug) and capture the film's own /kino/filmi/<slug>/ page as the
    deep-link. Non-programata pages fall back to numeric inline-date parsing."""
    st = {"fetched": False, "rows": 0, "dates": 0}
    if stats is not None:
        stats[venue_id] = st
    soup = fetch(url, session)
    if soup is None:
        return []
    st["fetched"] = True
    out = []
    if "programata.bg" in url:
        winset, year0 = set(window), int(window[0][:4])
        seen_slugs = set()
        for a in soup.select("a[href*='/kino/filmi/']"):
            href = a.get("href") or ""
            slug = href.split("/kino/filmi/")[-1].strip("/")
            if not slug or "/" in slug or slug in seen_slugs:
                continue
            title = a.get_text(" ", strip=True)
            if not (2 < len(title) < 120):
                continue
            # climb to the tightest ancestor that carries schedule lines and
            # holds only THIS film link (so we never merge two films' dates).
            block, cont = None, a
            for _ in range(6):
                cont = cont.parent
                if cont is None:
                    break
                ct = cont.get_text("\n", strip=True)
                if PROGRAMATA_LINE.search(ct):
                    if len(cont.select("a[href*='/kino/filmi/']")) == 1:
                        block = ct
                    break
            if not block:
                continue
            seen_slugs.add(slug)
            link = ("https://programata.bg" + href) if href.startswith("/") else href
            by_date = {}
            for m in PROGRAMATA_LINE.finditer(block):
                day, mon, tail = int(m.group(1)), MONTHNUM[m.group(2)], m.group(3)
                times = [f"{int(h):02d}:{mm}" for h, mm in TIME_RE.findall(tail)]
                if not times:
                    continue
                dstr = None
                for yr in (year0, year0 + 1):
                    try:
                        cand = dt.date(yr, mon, day).isoformat()
                    except ValueError:
                        continue
                    if cand in winset:
                        dstr = cand
                        break
                if not dstr:
                    continue
                by_date.setdefault(dstr, set()).update(times)
            for dstr, tset in by_date.items():
                out.append((title, venue_id, dstr, sorted(tset), link))
        st["rows"], st["dates"] = len(out), len({r[2] for r in out})
        return out
    if "vlaikovacinema.com" in url:
        return parse_vlaikova(soup, venue_id, window, st)
    if "ndk.bg" in url:
        return parse_lumiere(soup, venue_id, window, st)
    # fallback (generic inline-date headers). No cinema currently uses this path,
    # but it is kept so a future own-site source degrades gracefully.
    current_date = None
    for node in soup.find_all(["h2", "h3", "h4", "li", "tr", "div"]):
        text = node.get_text(" ", strip=True)
        if not text or len(text) > 400:
            continue
        if len(text) < 60:
            got = find_date(text, window)
            if got:
                current_date = got
        times = [f"{int(h):02d}:{m}" for h, m in TIME_RE.findall(text)]
        if times and current_date in window:
            title = TIME_RE.sub("", text).strip(" ·,-–—|")
            title = re.sub(r"\s{2,}", " ", title)
            if 2 < len(title) < 120:
                out.append((title, venue_id, current_date, sorted(set(times)), None))
    st["rows"], st["dates"] = len(out), len({r[2] for r in out})
    return out


def parse_vlaikova(soup, venue_id, window, st):
    """Кино Влайкова publishes a structured weekly grid on its own site:
    an `h3.cinema-day-title` ("07.10 (сряда)") opens each day, and every
    `div.cinema-show` under it carries a `.cinema-time` plus an `a.cinema-title`
    whose href is the film's own detail page (used as the per-title deep-link).
    programata has no data for Влайкова, so this is the only live source."""
    winset = set(window)
    current = None
    agg = {}                                   # (title, date) -> (set(times), link)
    for node in soup.select(".cinema-day-title, .cinema-show"):
        cls = node.get("class") or []
        if "cinema-day-title" in cls:
            current = find_date(node.get_text(" ", strip=True), window)
            continue
        if current not in winset:
            continue
        a = node.select_one("a.cinema-title")
        tnode = node.select_one(".cinema-time")
        if not a or not tnode:
            continue
        title = a.get_text(" ", strip=True)
        times = [f"{int(h):02d}:{m}" for h, m in TIME_RE.findall(tnode.get_text(" ", strip=True))]
        if not title or not times:
            continue
        href = a.get("href") or ""
        link = href if href.startswith("http") else None
        tset, lk = agg.setdefault((title, current), (set(), link))
        tset.update(times)
        if link and not lk:
            agg[(title, current)] = (tset, link)
    out = [(title, venue_id, d, sorted(tset), link) for (title, d), (tset, link) in agg.items()]
    st["rows"], st["dates"] = len(out), len({r[2] for r in out})
    return out


def parse_lumiere(soup, venue_id, window, st):
    """Кино Люмиер is NDK's arthouse hall. NDK's programme page lists events in
    `.single_incoming_event` blocks; we keep only those whose hall (`.ie_place`)
    names "Люмиер", reading the title from `.ie_heading` (its href is the event
    page), the date from `.ie_date` (DD.MM.YYYY) and the time from `.ie_hour`
    ("18:00ч."). Coverage is PARTIAL — NDK highlights a subset of screenings, not
    the full daily grid — so this is never treated as exhaustive."""
    winset = set(window)
    agg = {}
    for ev in soup.select(".single_incoming_event"):
        place = ev.select_one(".ie_place")
        if not place or "люмиер" not in place.get_text(" ", strip=True).lower():
            continue
        hd = ev.select_one(".ie_heading")
        dnode = ev.select_one(".ie_date")
        hnode = ev.select_one(".ie_hour")
        if not hd or not dnode or not hnode:
            continue
        title = hd.get_text(" ", strip=True)
        date = find_date(dnode.get_text(" ", strip=True), window)
        if not title or date not in winset:
            continue
        # NDK renders the time as "18:00ч." — the trailing Cyrillic "ч" is a word
        # character, so it blocks TIME_RE's trailing \b and no time matches. Strip
        # everything but digits and colons before matching.
        hour_txt = re.sub(r"[^\d:]", " ", hnode.get_text(" ", strip=True))
        times = [f"{int(h):02d}:{m}" for h, m in TIME_RE.findall(hour_txt)]
        if not times:
            continue
        href = hd.get("href") or ""
        link = href if href.startswith("http") else None
        tset, lk = agg.setdefault((title, date), (set(), link))
        tset.update(times)
        if link and not lk:
            agg[(title, date)] = (tset, link)
    out = [(title, venue_id, d, sorted(tset), link) for (title, d), (tset, link) in agg.items()]
    st["rows"], st["dates"] = len(out), len({r[2] for r in out})
    return out

BG_MONTHS = ("януари февруари март април май юни юли август септември "
             "октомври ноември декември").split()
MONTHNUM = {m: i + 1 for i, m in enumerate(BG_MONTHS)}
BG_DATE_SUB = re.compile(r"\b\d{1,2}\s+(?:" + "|".join(BG_MONTHS) + r")\b", re.I)
# programata renders each film's schedule as long-form Bulgarian date lines, e.g.
#   "17 септември |четвъртък|: 14:20 | 19:30"
# (the numeric DATE_RE never matched these, so every row used to default to
# window[0] — the root cause of the wrong-date bug).
# The trailing group must be an explicit run of HH:MM values. A looser class of
# "digits, colons, whitespace and pipes" runs past the end of the line and eats
# the day number of the NEXT date ("… 13:45\n  16 септември …"), which silently
# drops every date after the first whenever a venue puts two days in one node.
PROGRAMATA_LINE = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(BG_MONTHS) + r")\b[^:\n]*:\s*"
    r"((?:\d{1,2}:\d{2}(?:[ \t]*\|[ \t]*|[ \t]+)?)+)")


def find_date(text, window, default_year=None):
    """Parse a date in any form these sites use: "18 септември", "18.09",
    "18.09.2026", ISO. The year is rarely printed, so pick the one that lands the
    date inside the snapshot window — that handles December running into January
    without a special case. Returns ISO, or None."""
    day = month = year = None
    m = re.search(r"\b(\d{1,2})\s+(" + "|".join(BG_MONTHS) + r")\b", text, re.I)
    if m:
        day, month = int(m.group(1)), MONTHNUM[m.group(2).lower()]
    else:
        m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
        if m:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            m = DATE_RE.search(text)
            if not m:
                return None
            day, month = int(m.group(1)), int(m.group(2))
            if m.group(3):
                year = int(m.group(3))
                if year < 100:
                    year += 2000
    if year is None:
        for y in sorted({int(d[:4]) for d in (window or [])}) or [int(default_year or dt.date.today().year)]:
            try:
                iso = dt.date(y, month, day).isoformat()
            except ValueError:
                continue
            if not window or iso in window:
                return iso
            year = y
        year = year or int(default_year or dt.date.today().year)
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def page_echoes_date(soup, date):
    """theatre.art.bg silently serves *today* for a date it has no data for.
    Trust a day's rows only if the page actually echoes the requested date, in
    any of the forms the site might render it (ISO, DD.MM[.YYYY], D <bg-month>)."""
    y, m, d = (int(x) for x in date.split("-"))
    forms = [date,
             f"{d:02d}.{m:02d}.{y}", f"{d}.{m}.{y}",
             f"{d:02d}.{m:02d}", f"{d}.{m}",
             f"{d} {BG_MONTHS[m - 1]}"]
    low = soup.get_text(" ", strip=True).lower()
    return any(f.lower() in low for f in forms)


ART_BASE = "https://theatre.art.bg"
_ART_TID = re.compile(r"theatre=(\d+)")
_ART_SLUG_TID = re.compile(r"___(\d+)")


def scrape_theatre_day(date, session):
    """One day of the Sofia aggregator. Each `.afishbox` carries a clean title
    (its own <h3><a>), the time (overlaid on the cover), the venue's theatre id
    (from the kupi-bilet link, so a row can be attributed to a specific venue)
    and that per-performance buy link. Returns (title, date, time, theatre_id,
    buy_link); theatre_id/buy_link are None when a row lacks them. Falls back to
    the old flat scan if the structured markup is ever absent, so a redesign
    degrades to match-only rather than to nothing."""
    soup = fetch(THEATRE_DAY_URL.format(date=date), session)
    if soup is None:
        return []
    if not page_echoes_date(soup, date):
        # the page fell back to another day (or carries no verifiable date):
        # contribute nothing rather than mislabel another day's shows with this
        # date — keep-previous will preserve the real data.
        return []
    out = []
    boxes = soup.select(".afishbox")
    if boxes:
        for box in boxes:
            h = box.select_one("h3 a, h3")
            title = (h.get("title") or h.get_text(" ", strip=True)).strip() if h else ""
            tnode = box.select_one(".afish-img span")
            ttext = tnode.get_text(" ", strip=True) if tnode else box.get_text(" ", strip=True)
            tm = TIME_RE.search(ttext)
            if not title or not tm or not (2 < len(title) < 160):
                continue
            time_ = f"{int(tm.group(1)):02d}:{tm.group(2)}"
            buy = box.select_one("a.kupi_bilet[href], a[href*='kupi-bilet']")
            tid, link = None, None
            if buy and buy.get("href"):
                link = buy["href"]
                if link.startswith("/"):
                    link = ART_BASE + link
                m = _ART_TID.search(buy["href"])
                tid = m.group(1) if m else None
            if tid is None:
                vlink = box.select_one("h5 a[href]")
                if vlink:
                    m = _ART_SLUG_TID.search(vlink.get("href") or "")
                    tid = m.group(1) if m else None
            out.append((title, date, time_, tid, link))
        return out
    # fallback: pre-redesign flat scan (match-only, no venue attribution)
    for row in soup.select("li, tr, article, .event, .performance"):
        text = row.get_text(" ", strip=True)
        if not text or len(text) > 300:
            continue
        times = [f"{int(h):02d}:{m}" for h, m in TIME_RE.findall(text)]
        if len(times) != 1:
            continue
        title = TIME_RE.sub("", text).strip(" ·,-–—|")
        if 2 < len(title) < 160:
            out.append((title, date, times[0], None, None))
    return out


def scrape_theatre_page(url, session, window):
    """Scan a venue's monthly/season programme page once. Yields (title, date,
    time) only for rows carrying BOTH a date inside the window and a single
    time. Best-effort and title-matched downstream, so a page whose markup we
    can't parse simply contributes nothing — it never removes existing data."""
    soup = fetch(url, session)
    if soup is None:
        return []
    year0 = window[0][:4]
    out = []
    for row in soup.select("li, tr, article, .event, .performance, .show, .programa-item, .program-item"):
        text = row.get_text(" ", strip=True)
        if not text or len(text) > 400:
            continue
        times = [f"{int(h):02d}:{m}" for h, m in TIME_RE.findall(text)]
        if len(times) != 1:
            continue
        # Bulgarian venues write "18 септември" at least as often as "18.09".
        date = find_date(text, window, default_year=year0)
        if not date or date not in window:
            continue
        title = TIME_RE.sub("", BG_DATE_SUB.sub("", DATE_RE.sub("", text))).strip(" ·,-–—|")
        title = re.sub(r"\s{2,}", " ", title)
        if 2 < len(title) < 160:
            out.append((title, date, times[0]))
    return out


# --- dedicated venue parsers (own sites, venue known) ----------------------
# Each returns (title, date, time, venue_id, buy_link). The venue is fixed, so an
# uncatalogued title is minted and attributed correctly downstream. Only clean,
# per-item markup is read (title, date and time each from their own element), so
# nothing is ever reconstructed by splitting concatenated text.

def _abs(base, href):
    if not href:
        return None
    if href.startswith("http"):
        return href
    return base.rstrip("/") + "/" + href.lstrip("/")


def scrape_th199(session, window):
    """Театър 199 — theatre199.org/bg/schedule. Each `.js-card` holds a `.date`
    (DD.MM), a `.list-time` (HH:MM), a title in `article h4 a`, and that anchor's
    href as the per-play deep link. The year is inferred from the window."""
    soup = fetch(THEATRE_OWN_SOURCES["th199"], session)
    if soup is None:
        return []
    year0 = int(window[0][:4])
    winset = set(window)
    out = []
    for card in soup.select(".js-card, .news-resum-card"):
        dnode = card.select_one(".datе-label .date, .date")
        tnode = card.select_one(".list-time")
        tnode_a = card.select_one("article.resume-text h4 a, .resume-text h4 a, h4 a")
        if not (dnode and tnode and tnode_a):
            continue
        dm = re.search(r"\b(\d{1,2})[.\-/](\d{1,2})\b", dnode.get_text(" ", strip=True))
        tm = TIME_RE.search(tnode.get_text(" ", strip=True))
        if not dm or not tm:
            continue
        d, mo = int(dm.group(1)), int(dm.group(2))
        # the schedule is forward-looking; pick the year that lands it in window.
        date = None
        for y in (year0, year0 + 1):
            try:
                cand = dt.date(y, mo, d).isoformat()
            except ValueError:
                continue
            if cand in winset:
                date = cand
                break
        if not date:
            continue
        title = tnode_a.get_text(" ", strip=True)
        if not (2 < len(title) < 160):
            continue
        out.append((title, date, f"{int(tm.group(1)):02d}:{tm.group(2)}",
                    "th199", _abs("https://theatre199.org", tnode_a.get("href"))))
    return out


# Топлоцентрала writes times as "19.00часа" and the title with a trailing
# "Режисьор: …" / "ПРЕМИЕРА" tail that must be dropped so the card shows the play,
# not the credits.
_TOPLO_TAIL = re.compile(r"\s*(Режисьор|Хореограф|Автор|ПРЕМИЕРА|I ПРЕМИЕРА)\b.*$", re.I)


def scrape_toplo(session, window):
    """Топлоцентрала — toplocentrala.bg. Each `.program-list-item` has a
    `.program-date` (long-form Bulgarian), `.program-time` ("19.00часа") and a
    `.program-title`; the item's own anchor is the deep link."""
    soup = fetch(THEATRE_OWN_SOURCES["toplo"], session)
    if soup is None:
        return []
    year0 = window[0][:4]
    out = []
    for it in soup.select(".program-list-item"):
        dnode = it.select_one(".program-date")
        tnode = it.select_one(".program-time")
        titlenode = it.select_one(".program-title > div") or it.select_one(".program-title")
        if not (dnode and tnode and titlenode):
            continue
        date = find_date(dnode.get_text(" ", strip=True), window, default_year=year0)
        tm = re.search(r"\b([01]?\d|2[0-3])[.:]([0-5]\d)\b", tnode.get_text(" ", strip=True))
        if not date or date not in window or not tm:
            continue
        title = _TOPLO_TAIL.sub("", titlenode.get_text(" ", strip=True)).strip(" ·,-–—|")
        title = re.sub(r"\s{2,}", " ", title)
        if not (2 < len(title) < 160):
            continue
        link = it.select_one("a[href]")
        out.append((title, date, f"{int(tm.group(1)):02d}:{tm.group(2)}",
                    "toplo", _abs("https://toplocentrala.bg", link.get("href") if link else None)))
    return out


# Зад канала (Drupal) renders dates as "7 Окт. 2026 - 19:00" with abbreviated,
# dotted month names the full-name BG_MONTHS table does not cover.
_ZK_MON = {"ян": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "юни": 6, "юли": 7,
           "авг": 8, "сеп": 9, "окт": 10, "ное": 11, "дек": 12}


def scrape_zadkanala(session, window):
    """Зад канала — zadkanala.bg/programa, a Drupal views table. Each `<tr>` has a
    `.date-display-single` whose `content` attribute is an ISO datetime (date and
    time together) and a `.views-field-title` anchor (title + an own-site detail
    link). The "buy" link points at a WordPress login, so the detail page is used
    as the deep link instead."""
    soup = fetch(THEATRE_OWN_SOURCES["zad-kanala"], session)
    if soup is None:
        return []
    winset = set(window)
    out = []
    for span in soup.select(".date-display-single"):
        row = span.find_parent("tr") or span.parent
        titlenode = (row.select_one(".views-field-title a")
                     or row.select_one(".views-field-title")) if row else None
        if not titlenode:
            continue
        iso = span.get("content") or ""
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", iso)
        if m:
            date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            time_ = f"{m.group(4)}:{m.group(5)}"
        else:  # fall back to the rendered "D Мес. YYYY - HH:MM" text
            dtext = span.get_text(" ", strip=True)
            dm = re.search(r"\b(\d{1,2})\s+([А-Яа-я]{3})[.а-я]*\s+(\d{4})", dtext)
            tm = TIME_RE.search(dtext)
            mon = _ZK_MON.get(dm.group(2).lower()[:3]) if dm else None
            if not dm or not tm or not mon:
                continue
            try:
                date = dt.date(int(dm.group(3)), mon, int(dm.group(1))).isoformat()
            except ValueError:
                continue
            time_ = f"{int(tm.group(1)):02d}:{tm.group(2)}"
        if date not in winset:
            continue
        title = re.sub(r"\s{2,}", " ", titlenode.get_text(" ", strip=True)).strip(" ·,-–—|")
        if not (2 < len(title) < 160):
            continue
        href = titlenode.get("href") if titlenode.name == "a" else None
        out.append((title, date, time_, "zad-kanala",
                    _abs("https://zadkanala.bg", href)))
    return out


ARTVENT_BASE = "https://artvent.bg"
ARTVENT_DATE = re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b")


def scrape_artvent(session, window, artvent_ids):
    """Artvent stages its own productions. The /programa index links to each
    /event/<slug> page, which lists exact date+time pairs in .ticket.item rows
    (e.g. "29-09-2026 19:00"). We match a slug to a catalogued Artvent show id
    (id == slug) and return (show_id, date, time, detail_url). Titles/dates are
    never invented: only slugs already present as SHOWS are harvested."""
    idx = fetch(ARTVENT_URL, session)
    if idx is None:
        return []
    winset, out, seen = set(window), [], set()
    slugs = []
    for a in idx.select("a[href*='/event/']"):
        m = re.search(r"/event/([^/?#]+)", a.get("href") or "")
        if m:
            slugs.append(m.group(1))
    emitted = set()
    for slug in slugs:
        if slug in seen:
            continue
        seen.add(slug)
        if slug not in artvent_ids:
            continue                       # not catalogued — never invent a show
        ev = fetch(f"{ARTVENT_BASE}/event/{slug}", session)
        if ev is None:
            continue
        link = f"{ARTVENT_BASE}/event/{slug}"
        for item in ev.select(".ticket.item, .event-meta"):
            tx = item.get_text(" ", strip=True)
            dm, tm = ARTVENT_DATE.search(tx), TIME_RE.search(tx)
            if not dm or not tm:
                continue
            d, mo, y = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
            try:
                dstr = dt.date(y, mo, d).isoformat()
            except ValueError:
                continue
            if dstr not in winset:
                continue
            time_ = f"{int(tm.group(1)):02d}:{tm.group(2)}"
            k = (slug, dstr, time_)
            if k in emitted:
                continue
            emitted.add(k)
            out.append((slug, dstr, time_, link))
    return out


def norm(s):
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[„“”\"'’«»\.\,\!\?\:\;\-–—\(\)\[\]]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# --- minimal arthouse-film synthesis ---------------------------------------
# Independent cinemas (Одеон, Г8, Дом на киното, Влайкова, Люмиер) overwhelmingly
# screen films the hand-curated FILMS catalogue never listed. The old scraper
# dropped any unmatched title, which is why those halls showed almost nothing.
# Instead we mint a MINIMAL, source-faithful film record (title + a placeholder
# gradient; no invented year/runtime/synopsis) and persist it in cinema_films.json
# (keep-previous), which inject_films.py merges into FILMS before the build gate.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sht", "ъ": "a", "ь": "y",
    "ю": "yu", "я": "ya",
}


def slugify(title):
    """Stable, URL-safe id from a (usually Bulgarian) title. The id is cosmetic —
    never shown to the user — so transliteration need only be deterministic."""
    s = (title or "").lower().replace("ё", "е")
    s = "".join(_TRANSLIT.get(ch, ch) for ch in s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s[:60]


# Dark two-colour gradients; genPoster() draws light shapes over g[0]->g[1]. Chosen
# by hashing the id so each synthesised film gets a stable, distinct placeholder.
_GRADS = [
    ["#241B33", "#45275F"], ["#1B2733", "#27485F"], ["#331B24", "#5F2745"],
    ["#1B331F", "#275F3A"], ["#332A1B", "#5F4527"], ["#2A331B", "#4A5F27"],
    ["#1B3330", "#275F57"], ["#2B1B33", "#50275F"], ["#331B1B", "#5F2727"],
]


def grad_for(fid):
    import hashlib
    return _GRADS[int(hashlib.md5(fid.encode("utf-8")).hexdigest(), 16) % len(_GRADS)]



# --- cinemas: official programmes first, the aggregator only where they are silent
# Owner's rule: listings must be valid and accurate at all times. Each venue's
# OWN programme (official_sources.py) is authoritative for every date it has
# published; programata rows inside that range are discarded (each disagreement
# logged); programata rows after it are kept as PRELIMINARY — PRELIM_FROM marks
# the first such date per venue for the UI. A venue whose official source is
# unreachable keeps last run's rows and PRELIM_FROM; Cineland has no public
# official programme, so all of its (aggregator) rows are preliminary.

# programata pages per venue: the aggregator, used only where the venue is silent.
AGGREGATOR_VENUES = ("cc-sofia", "cc-paradise", "arena-mega", "arena-mall", "cg-ring",
                     "cg-park", "cineland", "odeon", "g8", "dom-kino")
# Order in which official titles are resolved and minted: proper-case sources
# before Cine Grand's ALL-CAPS titles, so a minted card reads "Паяци", not "ПАЯЦИ".
CINEMA_ORDER = ("cc-sofia", "cc-paradise", "arena-mega", "arena-mall", "odeon", "g8",
                "dom-kino", "vlaikova", "lumiere", "cg-ring", "cg-park", "cineland")


def _host(url):
    from urllib.parse import urlparse
    h = (urlparse(url or "").hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def allowed_vlink(venue, url):
    allowed = VENUE_LINK_ALLOWLIST.get(venue, [])
    h = _host(url)
    return bool(url and allowed and "programata.bg" not in h and any(a in h for a in allowed))


def _day_after(iso):
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=1)).isoformat()


def identity_meta(meta):
    """The parts of a source's metadata that identify a film."""
    meta = meta or {}
    out = {}
    for k in ("year", "runtime", "original_title"):
        if meta.get(k):
            out[k] = meta[k]
    if not out.get("year") and meta.get("alt_title"):
        out["year"] = FI.title_year(meta["alt_title"])
    return out


def choose_display(titles):
    """The variant to print on a minted card: one a person typed in normal case
    beats an ALL-CAPS one; then the most frequent; then the first seen."""
    from collections import Counter
    c = Counter(titles)
    best = sorted(c, key=lambda t: (t.upper() == t, -c[t], titles.index(t)))[0]
    return FI.clean_title(best, keep_prefix=True) or best


def resolve_official_rows(index, results, agg_strict, mint):
    """Map every official row to a film id, minting a film for a title no rule
    can place. results: {venue: OfficialResult}; agg_strict: {(venue, date,
    time): {fid}} from the aggregator (corroborates subtitle variants); mint:
    callable(display_title, venue, meta) -> fid. Returns ({venue: [(fid, date,
    time, meta, title)]}, [minted fid])."""
    from collections import defaultdict
    slots, metas = defaultdict(list), {}
    for v, res in results.items():
        for title, d, t, meta in res.rows:
            slots[(title, v)].append((d, t))
            m = metas.setdefault((title, v), dict(meta or {}))
            for k_, val in (meta or {}).items():
                m.setdefault(k_, val)
    order = sorted(slots, key=lambda tv: (CINEMA_ORDER.index(tv[1]) if tv[1] in CINEMA_ORDER else 99, tv[0]))

    def corroborator(tv):
        v, sl = tv[1], slots[tv]
        return lambda fid: any(fid in agg_strict.get((v, d, t), ()) for d, t in sl)

    resolved, pending = {}, []
    for tv in order:
        fid, _rule = index.resolve(tv[0], tv[1], identity_meta(metas[tv]), corroborator(tv))
        if fid:
            resolved[tv] = fid
        else:
            pending.append(tv)
    minted = []
    while pending:
        groups = defaultdict(list)
        for tv in pending:
            groups[FI.key(tv[0])].append(tv)
        k = max(groups, key=lambda g: (sum(len(slots[m]) for m in groups[g]), g))
        members = groups[k]
        meta = {}
        for m in members:
            for k_, val in metas[m].items():
                if val not in (None, "", []):
                    meta.setdefault(k_, val)
        fid = mint(choose_display([m[0] for m in members]), members[0][1], meta)
        minted.append(fid)
        for m in members:
            resolved[m] = fid
        still = []
        for tv in pending:
            if tv in members:
                continue
            fid2, _ = index.resolve(tv[0], tv[1], identity_meta(metas[tv]), corroborator(tv))
            if fid2:
                resolved[tv] = fid2
            else:
                still.append(tv)
        pending = still
    out = defaultdict(list)
    for v, res in results.items():
        for title, d, t, meta in res.rows:
            out[v].append((resolved[(title, v)], d, t, meta or {}, title))
    return dict(out), minted


def merge_cinema_venue(venue, status, official_rows, coverage, agg_rows, agg_fetched,
                       prev_rows, prev_prelim, floor, end, resolve_kept=None):
    """One venue's SHOWTIMES rows and PRELIM_FROM date. Pure — no network.

    status        "official"    coverage=(from, to); official_rows=[(fid, date, time)]
                  "unreachable" the official source failed: keep the previous rows
                                and the previous PRELIM_FROM (today if none)
                  "none"        no official source exists: aggregator rows, all
                                preliminary from today (keep-previous if the
                                aggregator is down too)
    agg_rows      [(fid or None, title, date, [times], link)] — fid resolved
                  strictly; resolve_kept(title, date, times, link) may place
                  (or mint) an unresolved row that survives the merge.
    prev_rows     last run's SHOWTIMES rows for this venue.
    Returns (rows, prelim_from, info). Rows are never dated before `floor`.
    """
    from collections import defaultdict
    info = {"status": status, "discarded": [], "added": 0, "prelim_rows": 0, "unplaced": []}
    cells = defaultdict(set)

    def put(fid, d, times):
        if fid and floor <= d <= end:
            cells[(fid, d)].update(times)

    def finish(prelim):
        rows = [[fid, venue, d, sorted(ts)] for (fid, d), ts in cells.items() if ts]
        rows.sort(key=lambda r: (r[2], r[0]))
        return rows, prelim, info

    def place(fid, title, d, times, link):
        if not fid and resolve_kept:
            fid = resolve_kept(title, d, times, link)
        if not fid:
            info["unplaced"].append([d, title, list(times)])
        return fid

    if status == "unreachable":
        for r in prev_rows:
            put(r[0], r[2], r[3])
        return finish(prev_prelim or floor)

    if status == "none":
        if agg_fetched:
            for fid, title, d, times, link in agg_rows:
                if floor <= d <= end:
                    fid = place(fid, title, d, times, link)
                    put(fid, d, times)
                    info["prelim_rows"] += len(times) if fid else 0
        else:
            for r in prev_rows:
                put(r[0], r[2], r[3])
        return finish(floor)

    cf, ct = max(coverage[0], floor), min(coverage[1], end)
    official = defaultdict(set)                       # date -> {(fid, time)}
    extras = defaultdict(set)                         # (fid, date) -> times, after coverage
    for fid, d, t in official_rows:
        if cf <= d <= ct:
            official[d].add((fid, t))
            put(fid, d, [t])
        elif ct < d <= end:
            extras[(fid, d)].add(t)
    agg_dates = set()
    agg_seen = defaultdict(set)
    for fid, title, d, times, link in agg_rows:
        if not (cf <= d <= ct):
            continue
        agg_dates.add(d)
        for t in times:
            agg_seen[d].add((fid, t))
            if fid is None or (fid, t) not in official[d]:
                info["discarded"].append([d, t, title, fid])
    info["added"] = sum(1 for d in agg_dates for x in official[d] if x not in agg_seen[d])
    if agg_fetched:
        for fid, title, d, times, link in agg_rows:
            if ct < d <= end:
                fid = place(fid, title, d, times, link)
                if fid and (fid, d) not in extras:    # the venue's own row wins that film/day
                    put(fid, d, times)
                    info["prelim_rows"] += len(times)
    else:
        for r in prev_rows:
            if ct < r[2] <= end and (r[0], r[2]) not in extras:
                put(r[0], r[2], r[3])
                info["prelim_rows"] += len(r[3])
    for (fid, d), ts in extras.items():
        put(fid, d, ts)
        info["prelim_rows"] += len(ts)
    if cf > floor and prev_prelim:
        # the venue's published range starts after today: keep only what the
        # previous run had confirmed for those days, never aggregator rows.
        for r in prev_rows:
            if floor <= r[2] < cf and r[2] < prev_prelim:
                put(r[0], r[2], r[3])
    return finish(_day_after(ct))


def write_prelim_from(page, prelim):
    """Replace `const PRELIM_FROM=…;` in the SOFIA-DATA block, or insert it on
    the line after `const VLINKS=…;` when the build does not declare it yet."""
    lit = json.dumps(prelim, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    m = re.search(r"^const PRELIM_FROM\s*=\s*\{[^\n]*?\};[ \t]*$", page, re.M)
    if m:
        return page[:m.start()] + f"const PRELIM_FROM={lit};" + page[m.end():]
    vl = re.search(r"^const VLINKS\s*=[^\n]*;[ \t]*$", page, re.M)
    if not vl:
        raise KeyError("VLINKS")
    return page[:vl.end()] + f"\nconst PRELIM_FROM={lit};" + page[vl.end():]


def read_prelim_from(page):
    m = re.search(r"^const PRELIM_FROM\s*=\s*(\{[^\n]*?\});[ \t]*$", page, re.M)
    if not m:
        return {}
    try:
        v = json.loads(m.group(1))
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def previous_official_stats():
    try:
        rep = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
        return rep.get("official") or {}
    except (OSError, ValueError):
        return {}


def implausible_drop(prev, cur):
    """A source that worked last run and now yields a fraction of its usual
    screenings per covered day has broken half-way (markup drift) — stop the
    scrape rather than publish a thinned programme. Returns a reason or None."""
    if not prev or prev.get("status") != "official" or not cur or cur.get("status") != "official":
        return None
    pr, pd_ = prev.get("screenings") or 0, prev.get("days") or 0
    cr, cd = cur.get("screenings") or 0, cur.get("days") or 0
    if pr < 20 or not pd_ or not cd:
        return None
    before, now = pr / pd_, cr / cd
    if before >= 3 and now < 0.25 * before:
        return (f"{now:.1f} screenings/day now vs {before:.1f} last run "
                f"({cr} over {cd} day(s) vs {pr} over {pd_})")
    return None


def _pos_int(v):
    try:
        n = int(str(v).strip()[:4])
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def minted_genres(meta):
    """Genres the source printed, in the app's vocabulary (unknown words dropped)."""
    out = [g for g in (meta.get("genres") or []) if isinstance(g, str)]
    txt = ", ".join(meta.get("genres_text") or [])
    if txt:
        try:
            from fetch_film_info import map_genres
            out += map_genres(txt)
        except Exception:
            pass
    seen = []
    for g in out:
        if g not in seen:
            seen.append(g)
    return seen


def print_official_table(report):
    if not report:
        return
    print(f"\n  {'venue':12s} {'status':11s} {'covered':23s} {'official':>8s} {'written':>7s} "
          f"{'prelim':>6s} {'discard':>7s} {'minted':>6s}  PRELIM_FROM")
    for vid, r in report.items():
        cov = f"{r['covered'][0]}..{r['covered'][1]}" if r.get("covered") else "—"
        print(f"  {vid:12s} {r['status']:11s} {cov:23s} {r['screenings']:>8d} "
              f"{r['written_screenings']:>7d} {r['prelim_screenings']:>6d} "
              f"{r['discarded_aggregator']:>7d} {len(r['minted']):>6d}  {r.get('prelim_from') or '—'}"
              + (f"   ! {r['error']}" if r.get("error") and r["status"] != "none" else ""))


def print_venue_table(stats):
    if not stats:
        return
    print(f"\n  {'venue':14s} {'fetched':>7s} {'days':>5s} {'rows':>6s} {'matched':>8s}  note")
    for vid, st in stats.items():
        if not st.get("fetched"):
            note = "page never arrived"
        elif not st.get("rows"):
            note = "page parsed, no dated showtimes found — markup changed?"
        elif not st.get("matched", 1):
            note = "showtimes found but no title matched the catalogue"
        else:
            note = ""
        print(f"  {vid:14s} {'yes' if st.get('fetched') else 'no':>7s} "
              f"{st.get('dates', 0):>5d} {st.get('rows', 0):>6d} "
              f"{st.get('matched', 0):>8d}  {note}")


def diagnose(session, window, index, stats, floor, end):
    """Probe every cinema source — each venue's own programme and the
    aggregator — and say exactly where each one breaks down. Writes nothing."""
    print("\n=== diagnose: official cinema programmes ===")
    errors = {}
    for vid in CINEMA_ORDER:
        if vid not in OS.OFFICIAL:
            print(f"  {vid:12s} no official source — {OS.NO_OFFICIAL.get(vid, '')}")
            continue
        res = OS.fetch_official(vid, session, floor, end, errors)
        if res is None:
            print(f"  {vid:12s} BROKEN — {errors.get(vid)}")
            continue
        inside = [r for r in res.rows if res.covers(r[1]) and r[1] >= floor]
        unmatched = sorted({t for t, _d, _t, m in inside
                            if not index.resolve(t, vid, identity_meta(m))[0]})
        print(f"  {vid:12s} {res.covered_from}..{res.covered_to}  {len(inside):4d} screenings  "
              f"{len(unmatched)} new title(s)  [{res.source}]")
        if unmatched:
            print("               new: " + ", ".join(unmatched[:8]))
    print("\n=== diagnose: aggregator (programata.bg) ===")
    for vid in AGGREGATOR_VENUES:
        rows = scrape_cinema(vid, CINEMA_SOURCES[vid], session, window, stats)
        stats[vid]["matched"] = sum(1 for title, *_ in rows if index.resolve(title, vid, fuzzy=False)[0])
    print_venue_table(stats)
    session.print_report()
    print("\nNothing was written. Re-run without --diagnose to apply a refresh.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--html", default=str(DEFAULT_HTML))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--diagnose", action="store_true",
                    help="probe every cinema source and report fetch / days / rows / "
                         "title matches per venue, then stop. Writes nothing.")
    ap.add_argument("--budget", type=int, default=1500,
                    help="seconds of network time before the run gives up gracefully, "
                         "so one dead host cannot stall the weekly job (default 1500)")
    args = ap.parse_args()
    html_path = pathlib.Path(args.html)

    src = html_path.read_text(encoding="utf-8")

    win_m = re.search(r'"?window"?\s*:\s*\{\s*"?from"?\s*:\s*"(\d{4}-\d\d-\d\d)"\s*,\s*"?to"?\s*:\s*"(\d{4}-\d\d-\d\d)"', src)
    if not win_m:
        sys.exit("could not find the snapshot window in " + str(html_path))
    start, end = win_m.group(1), win_m.group(2)
    # Clamp the dating floor to *today*: the static snapshot window still opens at
    # `start` (e.g. 2026-09-15), but we must never scrape, date, or store a showing
    # that has already passed. floor = max(start, today) guarantees past dates can
    # neither leak in from a parsed page nor be resurrected from stale rows.
    today_iso = dt.date.today().isoformat()
    floor = max(start, today_iso)
    d0, d1 = dt.date.fromisoformat(floor), dt.date.fromisoformat(end)
    if d1 < d0:
        sys.exit(f"snapshot window end {end} is before today {today_iso}; refresh the snapshot window")
    window = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    print(f"window {start} .. {end} -> dating floor {floor} ({len(window)} days)")

    st_s, st_e, st_lit = extract_array(src, "SHOWTIMES")
    pf_s, pf_e, pf_lit = extract_array(src, "PERFORMANCES")
    old_showtimes = js_rows(st_lit)
    old_performances = js_rows(pf_lit)

    # Match scraped titles ONLY against the real catalogues (FILMS / SHOWS), never
    # against the taste-quiz seed arrays (TASTE_FILMS / TASTE_SHOWS) elsewhere on
    # the page. Those carry ids like s-hamlet that are not real catalogue entries;
    # scanning the whole source picked them up, so a scraped "Хамлет"/"Чайка" was
    # matched to a show that does not exist and minted an orphan performance.
    def _array_src(name):
        try:
            _, _, lit = extract_array(src, name)
            return lit
        except (KeyError, ValueError):
            return ""
    films_src = _array_src("FILMS")
    title_to_id = {}
    for m in re.finditer(r'\{\s*"?id"?\s*:\s*"([^"]+)"\s*,\s*"?bg"?\s*:\s*"([^"]+)"\s*,\s*"?en"?\s*:\s*"([^"]+)"', films_src):
        fid, bg, en = m.groups()
        title_to_id[norm(bg)] = fid
        title_to_id[norm(en)] = fid
    shows_src = _array_src("SHOWS")
    show_title_to_id = {}
    for m in re.finditer(r'\{\s*"?id"?\s*:\s*"([^"]+)"\s*,\s*"?title"?\s*:\s*"([^"]+)"', shows_src):
        show_title_to_id[norm(m.group(2))] = m.group(1)
    # Artvent shows are keyed by their /event slug (id == slug), so the Artvent
    # parser matches by slug without title-normalisation guesswork.
    artvent_ids = set(re.findall(r'\{"id":"([^"]+)"[^}]*?"theatre":"artvent"', src))

    # Previously minted theatre shows (keep-previous), mirroring cinema_films.
    # Seed the title->id map so a returning production keeps its id (its
    # performances stay on one card) and inject_shows.py never duplicates it.
    theatre_shows = {}
    if THEATRE_SHOWS.exists():
        try:
            theatre_shows = json.loads(THEATRE_SHOWS.read_text(encoding="utf-8"))
        except Exception:
            theatre_shows = {}
    for sid_, rec in theatre_shows.items():
        if rec.get("title"):
            show_title_to_id.setdefault(norm(rec["title"]), sid_)
    existing_show_ids = set(show_title_to_id.values()) | set(theatre_shows.keys())
    try:
        _, _, shows_lit = extract_array(src, "SHOWS")
        existing_show_ids.update(re.findall(r'"id"\s*:\s*"([^"]+)"', shows_lit))
    except (KeyError, ValueError):
        pass

    def mint_show(title, venue):
        import hashlib
        base = slugify(title) or ("show-" + hashlib.md5(norm(title).encode()).hexdigest()[:8])
        cand, i = base, 2
        while cand in existing_show_ids:
            cand, i = f"{base}-{i}", i + 1
        existing_show_ids.add(cand)
        # Minimal, source-faithful: title + venue + a stable placeholder gradient.
        # No invented author, director, cast, synopsis or duration — the UI guards
        # for each of these being absent.
        theatre_shows[cand] = {"id": cand, "title": title, "titleEn": "",
                               "theatre": venue, "author": "", "director": "",
                               "cast": "", "genres": [], "g": grad_for(cand),
                               "duration": None, "synBg": "", "synEn": "",
                               "source": venue}
        return cand

    # Which cinemas are arthouse/independent — only these synthesise films for
    # uncatalogued titles (a multiplex title we don't recognise is a data error,
    # not a new arthouse film). Read straight from the CINEMAS array.
    independent_venues = set()
    cinema_kind = {}
    try:
        _, _, cin_lit = extract_array(src, "CINEMAS")
        for c in js_rows(cin_lit):
            cinema_kind[c.get("id")] = c.get("kind")
        independent_venues = {vid for vid, k in cinema_kind.items() if k == "independent"}
    except (KeyError, ValueError):
        pass

    # Previously minted arthouse films (keep-previous). Seed the title->id map from
    # them so a returning film keeps its id — its showtimes stay on one card across
    # refreshes — and so inject_films.py never duplicates it in FILMS.
    cinema_films = {}
    if CINEMA_FILMS.exists():
        try:
            cinema_films = json.loads(CINEMA_FILMS.read_text(encoding="utf-8"))
        except Exception:
            cinema_films = {}
    for fid, rec in cinema_films.items():
        if rec.get("bg"):
            title_to_id.setdefault(norm(rec["bg"]), fid)
        if rec.get("en"):
            title_to_id.setdefault(norm(rec["en"]), fid)

    # Every id already spoken for (catalogue + minted), so a new id never collides.
    existing_film_ids = set(title_to_id.values()) | set(cinema_films.keys())
    try:
        _, _, films_lit = extract_array(src, "FILMS")
        existing_film_ids.update(re.findall(r'"id"\s*:\s*"([^"]+)"', films_lit))
    except (KeyError, ValueError):
        pass

    # The film catalogue the identity rules resolve against: FILMS as built,
    # plus every film minted by earlier runs (keep-previous).
    try:
        _, _, films_lit2 = extract_array(src, "FILMS")
        catalogue = json.loads(films_lit2)
    except (KeyError, ValueError):
        catalogue = []
    _have = {f.get("id") for f in catalogue}
    index = FI.FilmIndex(catalogue + [r for k, r in cinema_films.items() if k not in _have],
                         minted_ids=set(cinema_films))
    minted_log = []                          # [fid, venue, title, how]

    def mint_film(title, venue, meta=None):
        """A minimal, source-faithful record for a film no rule can place:
        the official title, the venue, and only what the source itself printed
        (runtime, year, original title, genres). Nothing is invented."""
        import hashlib
        meta = meta or {}
        base = slugify(title) or ("film-" + hashlib.md5(norm(title).encode()).hexdigest()[:8])
        cand, i = base, 2
        while cand in existing_film_ids:
            cand, i = f"{base}-{i}", i + 1
        existing_film_ids.add(cand)
        rec = {"id": cand, "bg": title, "en": "", "genres": minted_genres(meta),
               "g": grad_for(cand), "source": venue}
        rt, yr = _pos_int(meta.get("runtime")), _pos_int(meta.get("year"))
        if rt and 20 <= rt <= 400:
            rec["runtime"] = rt
        if yr and 1890 <= yr <= 2100:
            rec["year"] = yr
        ot = (meta.get("original_title") or "").strip()
        if ot and FI.key(ot) != FI.key(title):
            rec["originalTitle"] = ot
        cinema_films[cand] = rec
        index.add(rec, minted=True)
        minted_log.append([cand, venue, title, meta.get("url") or meta.get("booking")])
        return cand

    # Per-title deep-links (film/show id -> detail URL). Seeded from the previous
    # LINKS array so a source we can't reach this week keeps its links.
    links = {}
    try:
        _, _, links_lit = extract_array(src, "LINKS")
        for pair in js_rows(links_lit):
            if isinstance(pair, list) and len(pair) == 2:
                links[pair[0]] = pair[1]
    except (KeyError, ValueError):
        pass

    # Per-(film, venue) ticket links. Seeded from the previous VLINKS array
    # so a venue we cannot reach this week keeps its deep-ticket links.
    # Key: (film_id, venue_id), value: ticket URL.
    vlinks = {}
    try:
        _, _, vl_lit = extract_array(src, "VLINKS")
        for row in js_rows(vl_lit):
            if isinstance(row, list) and len(row) == 3:
                vlinks[(row[0], row[1])] = row[2]
    except (KeyError, ValueError):
        pass
    # Track which (fid, venue) pairs were seen on programata pages so we can
    # prefer a programata link for LINKS (richest film-info page) while still
    # storing venue-specific ticket links in vlinks.
    programata_links = {}   # fid -> programata film-page URL

    session = Fetcher(budget_seconds=args.budget)
    venue_stats = {}

    if args.diagnose:
        return diagnose(session, window, index, venue_stats, floor, end)
    diff = Diff()

    cinema_ids = list(cinema_kind) or list(CINEMA_ORDER)
    old_prelim = read_prelim_from(src)
    prev_stats = previous_official_stats()

    # 1) the aggregator. Read for every venue it carries: its rows feed the
    #    preliminary days after a venue's own programme ends, Cineland (no
    #    official source), and the disagreement log inside official coverage.
    agg = {}
    for vid in AGGREGATOR_VENUES:
        url = CINEMA_SOURCES.get(vid)
        if not url:
            continue
        print(f"aggregator {vid}")
        rows = scrape_cinema(vid, url, session, window, venue_stats)
        agg[vid] = {"fetched": bool(venue_stats.get(vid, {}).get("fetched")), "rows": rows}

    def strict(title, v):
        return index.resolve(title, v, fuzzy=False)[0]


    agg_strict = defaultdict(set)
    for vid, a in agg.items():
        for title, _, d, times, link in a["rows"]:
            fid = strict(title, vid)
            for t in times:
                if fid:
                    agg_strict[(vid, d, t)].add(fid)

    # 2) each venue's own programme
    official, official_errors = {}, {}
    for vid in CINEMA_ORDER:
        if vid not in OS.OFFICIAL or vid not in cinema_ids:
            continue
        print(f"official {vid}")
        res = OS.fetch_official(vid, session, floor, end, official_errors)
        if res is None:
            print(f"  ! {vid}: {official_errors.get(vid)} — previous rows kept")
        else:
            # only rows the app can show: a weekly page also lists days already past
            res.rows = [r for r in res.rows if floor <= r[1] <= end]
            official[vid] = res
    resolved, minted_official = resolve_official_rows(index, official, agg_strict, mint_film)

    # official film pages: the info link (LINKS fallback) and the per-venue link
    fresh_vlinks = {}
    for vid, items in resolved.items():
        for fid, d, t, meta, title in items:
            url = meta.get("url")
            if url and "programata.bg" not in _host(url):
                links.setdefault(fid, url)
            tick = meta.get("booking") if vid == "lumiere" else None
            cand = tick if allowed_vlink(vid, tick) else url
            if allowed_vlink(vid, cand) and (fid, vid) not in fresh_vlinks:
                fresh_vlinks[(fid, vid)] = cand
    vlinks.update(fresh_vlinks)

    # 3) merge, venue by venue
    def resolve_kept(v):
        def place(title, d, times, link):
            fid, _ = index.resolve(title, v)
            if not fid and v in independent_venues and link:
                # an arthouse title only the aggregator lists, after the venue's
                # own programme ends — minted as before (preliminary rows only)
                fid = mint_film(FI.clean_title(title, keep_prefix=True) or title, v)
            return fid
        return place

    new_showtimes, seen_venues, venue_report = [], set(), {}
    prelim = {k: v for k, v in old_prelim.items() if k not in cinema_ids}
    for vid in cinema_ids:
        prev_rows = [r for r in old_showtimes if r[1] == vid]
        a = agg.get(vid, {"fetched": False, "rows": []})
        for title, _, d, times, link in a["rows"]:
            fid = strict(title, vid)
            if fid and link and "programata.bg" in _host(link):
                programata_links[fid] = link       # richest synopsis page for LINKS
        agg_rows = [(strict(t, vid), t, d, times, link) for t, _, d, times, link in a["rows"]]
        if vid in venue_stats:
            venue_stats[vid]["matched"] = sum(1 for r in agg_rows if r[0])
        res = official.get(vid)
        if res is not None:
            status, cov = "official", (res.covered_from, res.covered_to)
            orows = [(fid, d, t) for fid, d, t, _m, _t in resolved.get(vid, [])]
            source = res.source
        elif vid in OS.OFFICIAL:
            status, cov, orows = "unreachable", None, []
            source = "previous run kept — official source failed"
            diff.stale.append(vid)
        elif vid in agg:
            status, cov, orows = "none", None, []
            source = "programata.bg (no public official programme)"
            if not a["fetched"]:
                diff.unreachable.append(vid)
        else:
            new_showtimes += [r for r in prev_rows if floor <= r[2] <= end]
            continue                               # no source at all (casa-libri, ndk1)
        rows, pf, info = merge_cinema_venue(vid, status, orows, cov, agg_rows, a["fetched"],
                                            prev_rows, old_prelim.get(vid), floor, end,
                                            resolve_kept(vid))
        new_showtimes += rows
        prelim[vid] = pf
        if status != "unreachable":
            seen_venues.add(vid)
        cf_ct = [max(cov[0], floor), min(cov[1], end)] if cov else None
        venue_report[vid] = {
            "status": status, "source": source, "covered": cf_ct,
            "days": ((dt.date.fromisoformat(cf_ct[1]) - dt.date.fromisoformat(cf_ct[0])).days + 1) if cf_ct else 0,
            "screenings": sum(1 for _f, d, _t in orows if cf_ct and cf_ct[0] <= d <= cf_ct[1]),
            "rows": len(rows), "written_screenings": sum(len(r[3]) for r in rows),
            "prelim_from": pf, "prelim_screenings": info["prelim_rows"],
            "discarded_aggregator": len(info["discarded"]),
            "discarded_examples": info["discarded"][:12],
            "official_not_in_aggregator": info["added"],
            "unplaced_aggregator": info["unplaced"][:12],
            "minted": sorted({m[0] for m in minted_log if m[1] == vid}),
            "error": official_errors.get(vid), "notes": (res.notes if res else [])}

    # 4) self-check: inside every covered range the written rows must be exactly
    #    the venue's own programme; nothing past-dated; every film id known; and a
    #    source that worked last run must not suddenly yield a fraction of it.
    problems = []
    for vid, res in official.items():
        cf, ct = venue_report[vid]["covered"]
        want = {(fid, d, t) for fid, d, t, _m, _t in resolved.get(vid, []) if cf <= d <= ct}
        got = {(r[0], r[2], t) for r in new_showtimes if r[1] == vid and cf <= r[2] <= ct for t in r[3]}
        if want != got:
            problems.append(f"{vid}: written rows differ from its own programme inside {cf}..{ct} "
                            f"({len(got - want)} extra, {len(want - got)} missing)")
        why = implausible_drop(prev_stats.get(vid), venue_report[vid])
        if why:
            problems.append(f"{vid}: implausibly few rows from {res.source} — {why}")
    if any(r[2] < floor for r in new_showtimes):
        problems.append("a showtime before today survived the merge")
    orphan = sorted({r[0] for r in new_showtimes} - set(index.films))
    if orphan:
        problems.append(f"showtimes for unknown film ids: {', '.join(orphan[:5])}")
    print_official_table(venue_report)
    suspects = index.suspected_duplicates({r[0] for r in new_showtimes} | {m[0] for m in minted_log})
    identity_report = {"decisions": index.log, "minted": minted_log,
                       "suspected_duplicates": suspects}
    if problems:
        print("\nOFFICIAL-SOURCE SELF-CHECK FAILED — index.html left untouched:")
        for p_ in problems:
            print("  ✗", p_)
        CHANGES.write_text(json.dumps({"ran": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                       "aborted": problems, "official": venue_report,
                                       "identity": identity_report, "network": session.summary()},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
        return 3

    # Task 1c: LINKS prefers the programata film page when one was seen this run
    # (it has the richest synopsis/credits), else falls back to the venue page
    # already stored in links{} (setdefault above). This overwrites any venue page
    # that snuck in via the setdefault, making programata always win.
    for fid, purl in programata_links.items():
        links[fid] = purl

    # Task 1b: Ticket upgrade — for vlaikova and lumiere vlink pages, fetch the
    # page once and extract a film-specific ticket deep-link when present:
    #   vlaikova: an embed.urboapp.com link on the page
    #   lumiere:  an https://epaygo.bg/<digits> link on the page
    # Keep-previous: if the fetch fails or no deep-link is found, the page URL
    # already stored in vlinks is preserved.
    upgrade_venues = {"vlaikova", "lumiere"}
    upgraded = 0
    for (fid, vid), page_url in list(vlinks.items()):
        if vid not in upgrade_venues:
            continue
        from urllib.parse import urlparse as _up2
        lhost = _up2(page_url).netloc.lstrip("www.")
        # Only upgrade pages that are themselves on the allowlist (skip if we
        # already stored an embed.urboapp.com or epaygo.bg deep-link last run)
        if vid == "vlaikova" and "embed.urboapp.com" in lhost:
            continue  # already a deep-link ticket URL
        if vid == "lumiere" and "epaygo.bg" in lhost:
            continue  # already a deep-link ticket URL
        soup = fetch(page_url, session)
        if soup is None:
            continue
        page_text = str(soup)
        if vid == "vlaikova":
            m = _URBO_RE.search(page_text)
            if m:
                vlinks[(fid, vid)] = m.group(0)
                upgraded += 1
        elif vid == "lumiere":
            m = _EPAYGO_RE.search(page_text)
            if m:
                vlinks[(fid, vid)] = m.group(0)
                upgraded += 1
    if upgraded:
        print(f"upgraded {upgraded} vlinks to deep-ticket URLs (urbo/epaygo)")

    new_performances = []
    seen_perf, seen_shows = set(), set()

    def add_perf(sid, d, time_):
        seen_shows.add(sid)
        k = (sid, d, time_)
        if k in seen_perf:
            return
        seen_perf.add(k)
        hall = next((p[3] for p in old_performances if p[0] == sid), None)
        price = next((p[4] for p in old_performances if p[0] == sid), None)
        new_performances.append([sid, d, time_, hall, price])

    # 1) the Sofia aggregator (theatre.art.bg), per date. Rows carry the venue's
    # theatre id, so a production at a venue that sells only through art.bg
    # (Сфумато, Сити Марк) is minted and attributed rather than dropped; every
    # other theatre stays match-only.
    for date in window:
        for title, d, time_, tid, link in scrape_theatre_day(date, session):
            k = norm(title)
            sid = show_title_to_id.get(k)
            if not sid and tid in THEATRE_ART_VENUE:
                sid = mint_show(title, THEATRE_ART_VENUE[tid])
                show_title_to_id[k] = sid
            if sid:
                add_perf(sid, d, time_)
                if link:
                    links[sid] = link

    # 1b) dedicated venue parsers (own sites). The venue is known, so an
    # uncatalogued but real, dated production is minted and attributed correctly.
    for vid, parser in (("th199", scrape_th199), ("toplo", scrape_toplo),
                        ("zad-kanala", scrape_zadkanala)):
        print(f"theatre {vid}")
        for title, d, time_, venue, link in parser(session, window):
            k = norm(title)
            sid = show_title_to_id.get(k)
            if not sid:
                sid = mint_show(title, venue)
                show_title_to_id[k] = sid
            add_perf(sid, d, time_)
            if link:
                links[sid] = link

    # 2) individual venue programme pages, each scanned once (match-only)
    for vid, url in THEATRE_VENUE_SOURCES.items():
        print(f"theatre {vid}")
        for title, d, time_ in scrape_theatre_page(url, session, window):
            sid = show_title_to_id.get(norm(title))
            if sid:
                add_perf(sid, d, time_)

    # 3) Artvent — its own productions, harvested from each /event page with a
    # dedicated parser (the generic scanner matched none of them).
    print("theatre artvent")
    for sid, d, time_, link in scrape_artvent(session, window, artvent_ids):
        add_perf(sid, d, time_)
        if link:
            links[sid] = link

    # A show is "refreshed" only if a reachable source produced at least one
    # matched performance for it (seen_shows). Every show we did NOT refresh
    # keeps its previous performances — a dead or stale source never empties
    # the app, and only a refreshed show can have a performance reported
    # removed. If nothing matched at all, the whole theatre set is kept.
    for r in old_performances:
        if r[0] not in seen_shows and r[1] >= floor:
            new_performances.append(r)
    if not seen_shows:
        diff.stale.append("theatre.art.bg")

    def key(r): return (r[0], r[1], r[2])
    old_map = {key(r): r for r in old_showtimes}
    new_map = {key(r): r for r in new_showtimes}
    for k, r in new_map.items():
        if k not in old_map:
            diff.added.append({"type": "screening", "film": r[0], "venue": r[1], "date": r[2], "times": r[3]})
        elif sorted(old_map[k][3]) != sorted(r[3]):
            diff.changed.append({"type": "screening", "film": r[0], "venue": r[1], "date": r[2],
                                 "was": old_map[k][3], "now": r[3]})
    for k, r in old_map.items():
        if k not in new_map and r[1] in seen_venues:
            diff.removed.append({"type": "screening", "film": r[0], "venue": r[1],
                                 "date": r[2], "times": r[3], "reason": "cancelled or pulled"})

    old_pf = {key(r) for r in old_performances}
    new_pf = {key(r) for r in new_performances}
    for r in new_performances:
        if key(r) not in old_pf:
            diff.added.append({"type": "performance", "show": r[0], "date": r[1], "time": r[2]})
    for r in old_performances:
        if key(r) not in new_pf and r[0] in seen_shows:
            diff.removed.append({"type": "performance", "show": r[0], "date": r[1],
                                 "time": r[2], "reason": "cancelled or pulled"})

    print("\n" + diff.summary())
    if diff.unreachable:
        print("unreachable, previous data kept:", ", ".join(diff.unreachable))
    if diff.stale:
        print("reachable but nothing parsed (markup drift?), previous data kept:",
              ", ".join(diff.stale))
    if diff.emptied:
        print("read, but listing no catalogued film — stale rows dropped:",
              ", ".join(diff.emptied))
    print_venue_table(venue_stats)
    session.print_report()

    report = {"ran": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "summary": diff.summary(), "added": diff.added, "removed": diff.removed,
              "changed": diff.changed, "unreachable": diff.unreachable,
              "stale": diff.stale, "emptied": diff.emptied, "venues": venue_stats,
              "official": venue_report, "prelim_from": prelim, "identity": identity_report,
              "network": session.summary()}
    CHANGES.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.dry_run:
        print("dry run — index.html untouched")
        return 0

    new_showtimes.sort(key=lambda r: (r[1], r[2], r[0]))
    new_performances.sort(key=lambda r: (r[1], r[2], r[0]))

    out = src[:st_s] + emit_rows(new_showtimes) + src[st_e:]
    pf_s2, pf_e2, _ = extract_array(out, "PERFORMANCES")
    out = out[:pf_s2] + emit_rows(new_performances) + out[pf_e2:]
    # per-title deep-links → LINKS=[[id,url],…]
    try:
        li_s, li_e, _ = extract_array(out, "LINKS")
        link_rows = sorted([k, v] for k, v in links.items())
        out = out[:li_s] + emit_rows(link_rows) + out[li_e:]
    except (KeyError, ValueError):
        pass
    # per-(film, venue) ticket links → VLINKS=[[fid,venue,url],…]
    # Prune to only (film, venue) pairs that still have a showtime in new_showtimes.
    active_pairs = {(r[0], r[1]) for r in new_showtimes}
    vlinks_pruned = {k: v for k, v in vlinks.items() if k in active_pairs}
    try:
        vl_s, vl_e, _ = extract_array(out, "VLINKS")
        vlink_rows = sorted([fid, vid, url] for (fid, vid), url in vlinks_pruned.items())
        out = out[:vl_s] + emit_rows(vlink_rows) + out[vl_e:]
        print(f"wrote VLINKS: {len(vlink_rows)} entries ({len(vlinks_pruned)} unique film+venue pairs)")
        # Report per-venue counts
        from collections import Counter as _Ctr
        venue_counts = _Ctr(vid for _, vid, _ in vlink_rows)
        for vid, cnt in sorted(venue_counts.items()):
            print(f"  {vid}: {cnt} vlink(s)")
    except (KeyError, ValueError):
        print("VLINKS constant not found in index.html — skipping VLINKS update "
              "(add 'const VLINKS=[];' to src/data.html between the SOFIA-DATA markers)")
    # which listings are preliminary: per venue, every date on/after PRELIM_FROM
    out = write_prelim_from(out, prelim)
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    out = re.sub(r'"?lastValidated"?\s*:\s*"[^"]*"', f'"lastValidated":"{stamp}"', out)
    # Advance the snapshot window's opening to the dating floor so the stored
    # window matches reality (no past days) — keeps the app's range filters honest.
    out = re.sub(r'("?window"?\s*:\s*\{\s*"?from"?\s*:\s*")\d{4}-\d\d-\d\d(")',
                 lambda m: m.group(1) + floor + m.group(2), out, count=1)
    cl = find_prop_array(out, "changelog")
    if cl:
        changelog = diff.added[:40] + diff.removed[:40] + diff.changed[:40]
        out = out[:cl[0]] + json.dumps(changelog, ensure_ascii=False, separators=(",", ":")) + out[cl[1]:]

    html_path.write_text(out, encoding="utf-8")
    # Persist the arthouse-film catalogue (keep-previous union of old + newly minted);
    # inject_films.py merges it into FILMS before the build gate.
    CINEMA_FILMS.write_text(json.dumps(cinema_films, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    # Same for synthesised theatre shows; inject_shows.py merges into SHOWS.
    THEATRE_SHOWS.write_text(json.dumps(theatre_shows, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    print(f"wrote {html_path.name}, {CHANGES.name}, {CINEMA_FILMS.name} "
          f"({len(cinema_films)} arthouse films) and {THEATRE_SHOWS.name} "
          f"({len(theatre_shows)} theatre shows)")
    # The last lines are what refresh_all.py keeps as this step's tail.
    by = defaultdict(list)
    for vid, r in venue_report.items():
        by[r["status"]].append(vid)
    print(f"official programmes: {len(by['official'])} read, {len(by['unreachable'])} kept-previous"
          + (f" ({', '.join(by['unreachable'])})" if by["unreachable"] else "")
          + f", {len(by['none'])} aggregator-only ({', '.join(by['none']) or '—'}); "
          f"{len(minted_log)} film(s) minted; {len(suspects)} suspected duplicate(s)")
    print("PRELIM_FROM " + json.dumps(prelim, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
