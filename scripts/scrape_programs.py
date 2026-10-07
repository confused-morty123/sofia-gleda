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
from dataclasses import dataclass, field

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("pip install requests beautifulsoup4 lxml")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from netfetch import Fetcher                    # shared hardened HTTP layer

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
CHANGES = ROOT / "changes.json"
# Minimal arthouse-film records minted from independent-cinema programmes (titles
# the static FILMS catalogue never carried). Persisted keep-previous and merged
# into FILMS by inject_films.py before the build gate.
CINEMA_FILMS = ROOT / "cinema_films.json"

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

TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
DATE_RE = re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\b")


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


def scrape_theatre_day(date, session):
    soup = fetch(THEATRE_DAY_URL.format(date=date), session)
    if soup is None:
        return []
    if not page_echoes_date(soup, date):
        # the page fell back to another day (or carries no verifiable date):
        # contribute nothing rather than mislabel another day's shows with this
        # date — keep-previous will preserve the real data.
        return []
    out = []
    for row in soup.select("li, tr, article, .event, .performance"):
        text = row.get_text(" ", strip=True)
        if not text or len(text) > 300:
            continue
        times = [f"{int(h):02d}:{m}" for h, m in TIME_RE.findall(text)]
        if len(times) != 1:
            continue
        title = TIME_RE.sub("", text).strip(" ·,-–—|")
        if 2 < len(title) < 160:
            out.append((title, date, times[0]))
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


def diagnose(session, window, title_to_id, stats):
    """Probe every cinema source and say exactly where each one breaks down.
    Writes nothing — this is for telling a dead source apart from one whose
    markup moved, which a bare 'unreachable' count cannot do."""
    print("\n=== diagnose: cinema sources ===")
    for vid, url in CINEMA_SOURCES.items():
        rows = scrape_cinema(vid, url, session, window, stats)
        matched = sum(1 for title, *_ in rows if title_to_id.get(norm(title)))
        stats[vid]["matched"] = matched
        if rows and not matched:
            sample = ", ".join(sorted({t for t, *_ in rows})[:4])
            print(f"  {vid}: parsed titles that matched nothing — {sample}")
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

    title_to_id = {}
    for m in re.finditer(r'\{\s*"?id"?\s*:\s*"([^"]+)"\s*,\s*"?bg"?\s*:\s*"([^"]+)"\s*,\s*"?en"?\s*:\s*"([^"]+)"', src):
        fid, bg, en = m.groups()
        title_to_id[norm(bg)] = fid
        title_to_id[norm(en)] = fid
    show_title_to_id = {}
    for m in re.finditer(r'\{\s*"?id"?\s*:\s*"([^"]+)"\s*,\s*"?title"?\s*:\s*"([^"]+)"', src):
        show_title_to_id[norm(m.group(2))] = m.group(1)
    # Artvent shows are keyed by their /event slug (id == slug), so the Artvent
    # parser matches by slug without title-normalisation guesswork.
    artvent_ids = set(re.findall(r'\{"id":"([^"]+)"[^}]*?"theatre":"artvent"', src))

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

    def mint_film(title, venue):
        import hashlib
        base = slugify(title) or ("film-" + hashlib.md5(norm(title).encode()).hexdigest()[:8])
        cand, i = base, 2
        while cand in existing_film_ids:
            cand, i = f"{base}-{i}", i + 1
        existing_film_ids.add(cand)
        cinema_films[cand] = {"id": cand, "bg": title, "en": "", "genres": [],
                              "g": grad_for(cand), "source": venue}
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

    session = Fetcher(budget_seconds=args.budget)
    venue_stats = {}

    if args.diagnose:
        return diagnose(session, window, title_to_id, venue_stats)
    diff = Diff()

    new_showtimes, seen_venues = [], set()
    for vid, url in CINEMA_SOURCES.items():
        print(f"cinema {vid}")
        rows = scrape_cinema(vid, url, session, window, venue_stats)
        matched = 0
        for title, v, date, times, link in rows:
            key = norm(title)
            fid = title_to_id.get(key)
            if not fid and v in independent_venues and link:
                # An arthouse film the static catalogue doesn't carry. Mint a
                # minimal, source-faithful record rather than drop the screening.
                # Only titles that arrived WITH a real film-detail link are minted
                # (never event/accent links), and no metadata is invented.
                fid = mint_film(title, v)
                title_to_id[key] = fid     # collapse repeats of this title this run
            if fid:
                new_showtimes.append([fid, v, date, times])
                matched += 1
                if link:
                    links[fid] = link      # programata/venue film page — canonical
        # A venue is authoritative — its old rows may be dropped — once we have
        # actually read its programme this week: either a row matched a film in
        # the catalogue, or the page parsed into dated rows at all (st["rows"]).
        # A venue that lists only films outside our catalogue (e.g. Вайкова, all
        # SINELIBRI festival titles) has genuinely stopped showing any catalogue
        # film, so a phantom screening left over from a previous run — such as a
        # row that defaulted to window[0] — must be removed, not preserved. Only
        # a venue we could not read into any dated row (fetch failed, or markup
        # drifted so nothing parsed) is treated as stale and keeps last week's
        # rows, so a dead source never empties the app or fabricates a listing.
        st = venue_stats.get(vid, {})
        st["matched"] = matched
        if matched or st.get("rows", 0) > 0:
            seen_venues.add(vid)
            if not matched:
                diff.emptied.append(vid)   # read, but nothing we catalogue is on
        elif not st.get("fetched"):
            diff.unreachable.append(vid)          # the page never arrived
        else:
            diff.stale.append(vid)                # it arrived; nothing parsed out
    # keep venues we could not read — a dead or unparseable source never empties
    # the app; a venue we read is authoritative and its stale rows are dropped.
    # Even a kept row must never be a past date: drop any carried-over showing that
    # falls before the dating floor, so stale window[0] rows cannot resurrect.
    for row in old_showtimes:
        if row[1] not in seen_venues and row[2] >= floor:
            new_showtimes.append(row)

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

    # 1) the Sofia aggregator (theatre.art.bg), per date
    for date in window:
        for title, d, time_ in scrape_theatre_day(date, session):
            sid = show_title_to_id.get(norm(title))
            if sid:
                add_perf(sid, d, time_)

    # 2) individual venue programme pages, each scanned once
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
    print(f"wrote {html_path.name}, {CHANGES.name} and {CINEMA_FILMS.name} "
          f"({len(cinema_films)} arthouse films)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
