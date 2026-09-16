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

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
CHANGES = ROOT / "changes.json"

UA = {"User-Agent": "SofiaGleda/1.0 (personal programme aggregator)"}
TIMEOUT = 25
POLITE_DELAY = 1.5

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
    "euro-cinema": "https://programata.bg/kino/kino-saloni/sofia/euro-cinema/",
    "g8":          "https://programata.bg/kino/kino-saloni/sofia/g8-cinema/",
    "dom-kino":    "https://domnakinoto.com/programing/index",
    "vlaikova":    "https://vlaikovacinema.com/",
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
    "satira":    "https://satirata.bg/program",
    "natfiz":    "https://natfiz.bg/udt-mesechna-programa/",
    "iam":       "https://iamstudio.bg/programa/",
    "comedy":    "https://comedyclub.bg/program/",
    "melpomena": "https://melpomenatheatre.com/програма",
    "new-ndk":   "https://tickets.ndk.bg",
    "atelie313": "https://www.atelie313.com/programata1",
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
    unreachable: list = field(default_factory=list)

    def empty(self):
        return not (self.added or self.removed or self.changed)

    def summary(self):
        return (f"{len(self.added)} added, {len(self.removed)} removed/cancelled, "
                f"{len(self.changed)} changed, {len(self.unreachable)} sources unreachable")


def fetch(url, session):
    try:
        r = session.get(url, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        time.sleep(POLITE_DELAY)
        return BeautifulSoup(r.text, "lxml")
    except Exception as e:
        print(f"  ! {url}: {e}", file=sys.stderr)
        return None


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
    txt = re.sub(r"/\*.*?\*/", "", literal, flags=re.S)
    txt = re.sub(r"//[^\n]*", "", txt)
    txt = re.sub(r",(\s*[\]\}])", r"\1", txt)
    return json.loads(txt)


def emit_rows(rows):
    """Compact single-line JSON to preserve the app's minified data block."""
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def scrape_cinema(venue_id, url, session, window):
    # append ?date= for programata pages to defeat the stale cache
    if "programata.bg" in url:
        url = url + ("?date=" + window[0])
    soup = fetch(url, session)
    if soup is None:
        return []
    out, current_date = [], None
    for node in soup.find_all(["h2", "h3", "h4", "li", "tr", "div"]):
        text = node.get_text(" ", strip=True)
        if not text or len(text) > 400:
            continue
        dm = DATE_RE.search(text)
        if dm and len(text) < 60:
            day, month = int(dm.group(1)), int(dm.group(2))
            year = int(dm.group(3) or window[0][:4])
            if year < 100: year += 2000
            try:
                current_date = dt.date(year, month, day).isoformat()
            except ValueError:
                pass
        times = [f"{int(h):02d}:{m}" for h, m in TIME_RE.findall(text)]
        if times and current_date in window:
            title = TIME_RE.sub("", text).strip(" ·,-–—|")
            title = re.sub(r"\s{2,}", " ", title)
            if 2 < len(title) < 120:
                out.append((title, venue_id, current_date, sorted(set(times))))
    return out


BG_MONTHS = ("януари февруари март април май юни юли август септември "
             "октомври ноември декември").split()


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
        dm = DATE_RE.search(text)
        times = [f"{int(h):02d}:{m}" for h, m in TIME_RE.findall(text)]
        if not dm or len(times) != 1:
            continue
        day, month = int(dm.group(1)), int(dm.group(2))
        year = int(dm.group(3) or year0)
        if year < 100:
            year += 2000
        try:
            date = dt.date(year, month, day).isoformat()
        except ValueError:
            continue
        if date not in window:
            continue
        title = TIME_RE.sub("", DATE_RE.sub("", text)).strip(" ·,-–—|")
        title = re.sub(r"\s{2,}", " ", title)
        if 2 < len(title) < 160:
            out.append((title, date, times[0]))
    return out


def norm(s):
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[„“”\"'’«»\.\,\!\?\:\;\-–—\(\)\[\]]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default=str(DEFAULT_HTML))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    html_path = pathlib.Path(args.html)

    src = html_path.read_text(encoding="utf-8")

    win_m = re.search(r'"?window"?\s*:\s*\{\s*"?from"?\s*:\s*"(\d{4}-\d\d-\d\d)"\s*,\s*"?to"?\s*:\s*"(\d{4}-\d\d-\d\d)"', src)
    if not win_m:
        sys.exit("could not find the snapshot window in " + str(html_path))
    start, end = win_m.group(1), win_m.group(2)
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    window = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    print(f"window {start} .. {end} ({len(window)} days)")

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

    session = requests.Session()
    diff = Diff()

    new_showtimes, seen_venues = [], set()
    for vid, url in CINEMA_SOURCES.items():
        print(f"cinema {vid}")
        rows = scrape_cinema(vid, url, session, window)
        matched = 0
        for title, v, date, times in rows:
            fid = title_to_id.get(norm(title))
            if fid:
                new_showtimes.append([fid, v, date, times])
                matched += 1
        # A venue counts as refreshed only if at least one row matched a film in
        # the catalogue. Reachable-but-unmatched (markup drift, renamed titles,
        # an encoding change) is treated as stale: keep last week's rows rather
        # than empty the venue and fabricate cancellations.
        if matched:
            seen_venues.add(vid)
        else:
            diff.unreachable.append(vid)
    # keep venues we could not reach or match — a dead or stale source never
    # empties the app
    for row in old_showtimes:
        if row[1] not in seen_venues:
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

    # 2) individual venue programme pages + Artvent, each scanned once
    for vid, url in {**THEATRE_VENUE_SOURCES, "artvent": ARTVENT_URL}.items():
        print(f"theatre {vid}")
        for title, d, time_ in scrape_theatre_page(url, session, window):
            sid = show_title_to_id.get(norm(title))
            if sid:
                add_perf(sid, d, time_)

    # A show is "refreshed" only if a reachable source produced at least one
    # matched performance for it (seen_shows). Every show we did NOT refresh
    # keeps its previous performances — a dead or stale source never empties
    # the app, and only a refreshed show can have a performance reported
    # removed. If nothing matched at all, the whole theatre set is kept.
    for r in old_performances:
        if r[0] not in seen_shows:
            new_performances.append(r)
    if not seen_shows:
        diff.unreachable.append("theatre.art.bg")

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

    report = {"ran": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "summary": diff.summary(), "added": diff.added, "removed": diff.removed,
              "changed": diff.changed, "unreachable": diff.unreachable}
    CHANGES.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.dry_run:
        print("dry run — index.html untouched")
        return 0

    new_showtimes.sort(key=lambda r: (r[1], r[2], r[0]))
    new_performances.sort(key=lambda r: (r[1], r[2], r[0]))

    out = src[:st_s] + emit_rows(new_showtimes) + src[st_e:]
    pf_s2, pf_e2, _ = extract_array(out, "PERFORMANCES")
    out = out[:pf_s2] + emit_rows(new_performances) + out[pf_e2:]
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    out = re.sub(r'"?lastValidated"?\s*:\s*"[^"]*"', f'"lastValidated":"{stamp}"', out)
    cl = find_prop_array(out, "changelog")
    if cl:
        changelog = diff.added[:40] + diff.removed[:40] + diff.changed[:40]
        out = out[:cl[0]] + json.dumps(changelog, ensure_ascii=False, separators=(",", ":")) + out[cl[1]:]

    html_path.write_text(out, encoding="utf-8")
    print(f"wrote {html_path.name} and {CHANGES.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
