#!/usr/bin/env python3
"""Sofia Gleda — every cinema's OWN official programme.

The owner's rule: listings must be valid and accurate at all times. An
aggregator (programata.bg) lists some venues' next week before the venue does
(G8's "Без конкуренция" on a Friday G8 had not published), files screenings
under the wrong hall (Финик 2 at Paradise) and drops a third of the multiplex
screenings. So each venue's own programme is authoritative for every date it
has published, and this module reads it.

Each fetcher returns an OfficialResult — rows as published plus the date range
the source authoritatively covers (a covered date with no rows means "no
screenings that day") — or None when the source is unreachable or its markup no
longer parses (zero rows where rows are expected, HTTP errors, a page that is
not the one we asked for). None is never "no screenings": the merge keeps the
previous run's rows for that venue instead.

Traps found while building this (2026-10-08), each guarded below:
  * Кино Арена silently serves TODAY's programme for a date it has not
    published; the selected day tab is checked against the requested date.
  * Cine Grand moved to /<cinema>/schedule-<weekday>-<n> pages; every show's
    own "чт, 8 окт, 11:50" label and the cinema code on each page are checked.
  * Cinema City and Дом на киното publish thin pre-sale/festival days far
    ahead; those are not a published programme (see published_through()).
  * Одеон's next programme number exists as an empty shell before it is filled.

    from official_sources import fetch_official
    res = fetch_official("cc-sofia", session, "2026-10-08", "2026-12-14", errors)
"""
from __future__ import annotations

import datetime as dt
import re
import statistics
import urllib.parse
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

TIME_RE = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b")


class SourceBroken(Exception):
    """The official source could not be read into a trustworthy programme."""


@dataclass
class OfficialResult:
    venue: str
    source: str
    rows: list                  # [(title_as_published, "YYYY-MM-DD", "HH:MM", meta)]
    covered_from: str
    covered_to: str
    notes: list = field(default_factory=list)
    days: dict = field(default_factory=dict)    # date -> screenings parsed (diagnostics)

    def covers(self, d):
        return self.covered_from <= d <= self.covered_to


# ---------------------------------------------------------------- helpers
def _d(iso):
    return dt.date.fromisoformat(iso)


def _iso(d):
    return d.isoformat()


def _next(iso, n=1):
    return _iso(_d(iso) + dt.timedelta(days=n))


def _hhmm(text):
    m = TIME_RE.search(text or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def _int(text):
    m = re.search(r"\d+", str(text or ""))
    return int(m.group(0)) if m else None


def _infer_year(day, month, today):
    """Day+month without a year: the candidate nearest to today, never more
    than two months in the past (programmes look forward)."""
    t = _d(today)
    best = None
    for y in (t.year - 1, t.year, t.year + 1):
        try:
            c = dt.date(y, month, day)
        except ValueError:
            continue
        delta = (c - t).days
        if delta < -62:
            continue
        if best is None or abs(delta) < abs((best - t).days):
            best = c
    return _iso(best) if best else None


BG_MONTHS = ("януари февруари март април май юни юли август септември "
             "октомври ноември декември").split()
_BG_MON3 = {m[:3]: i + 1 for i, m in enumerate(BG_MONTHS)}


def _bg_day_month(text, today):
    """'четвъртък,  8 октомври' / 'чт,  8 окт, 11:50' -> ISO date."""
    m = re.search(r"(\d{1,2})\s+([а-я]{3})[а-я]*\.?", (text or "").lower())
    if not m or m.group(2) not in _BG_MON3:
        return None
    return _infer_year(int(m.group(1)), _BG_MON3[m.group(2)], today)


def published_through(counts, ratio=0.2, ref_days=7):
    """End of the published programme for a per-day source.

    `counts` is [(date, screenings or None)] for consecutive days from today
    (None = that day's request failed). The reference level is the median of
    the non-empty days among the first week; the programme runs while a day
    reaches `ratio` of it. A thin trailing day (one pre-sale event, a festival
    screening booked months ahead) is "not published yet", never "the whole
    programme". Today itself may be thin late in the evening (past screenings
    drop off), so it counts as covered when tomorrow is a normal day. A failed
    day ends the coverage before it. Returns the last covered date or None."""
    sample = [n for _, n in counts[:ref_days] if n]
    if not sample:
        return None
    floor = max(1.0, ratio * statistics.median(sample))
    end = None
    for i, (d, n) in enumerate(counts):
        if n is None:
            break
        if n < floor:
            if i == 0 and len(counts) > 1 and counts[1][1] is not None and counts[1][1] >= floor:
                end = d
                continue
            break
        end = d
    return end


def week_end_at_or_before(iso, today, weekday=3):
    """The programme week runs Friday–Thursday (weekday 3 = Thursday). Trim a
    coverage end back to the last week end, but never before today."""
    d = _d(iso)
    while d.weekday() != weekday:
        d -= dt.timedelta(days=1)
    return _iso(d) if _iso(d) >= today else iso


def _soup(html):
    return BeautifulSoup(html, "lxml")


# ------------------------------------------------------- Cinema City (JSON)
CC_API = ("https://www.cinemacity.bg/bg/data-api-service/v1/quickbook/10106/film-events/"
          "in-cinema/{cinema}/at-date/{date}?attr=&lang=bg_BG")
CC_CINEMAS = {"cc-sofia": "1261", "cc-paradise": "1266"}
CC_GENRES = {"action": "Екшън", "adventure": "Приключенски", "animation": "Анимация",
             "biography": "Биографичен", "comedy": "Комедия", "crime": "Криминален",
             "documentary": "Документален", "drama": "Драма", "family": "Семеен",
             "fantasy": "Фентъзи", "horror": "Хорър", "music": "Музикален",
             "mystery": "Мистерия", "romance": "Романтика", "sci-fi": "Фантастика",
             "thriller": "Трилър", "history": "Исторически", "sport": "Спорт"}
CC_FORMATS = ("2d", "3d", "imax", "4dx", "screenx", "d-box", "infinity-vision")


def parse_cinemacity_day(payload, date, cinema=None):
    """One day of the quickbook API: body.films[{id,name,length,link,...}] and
    body.events[{filmId,cinemaId,eventDateTime,auditorium,attributeIds,...}].
    Only events whose own eventDateTime falls on `date` are kept."""
    body = (payload or {}).get("body") if isinstance(payload, dict) else None
    if not isinstance(body, dict) or not isinstance(body.get("events"), list) \
            or not isinstance(body.get("films"), list):
        raise SourceBroken("Cinema City API answered without body.films/body.events")
    films = {f.get("id"): f for f in body["films"] if isinstance(f, dict)}
    rows = []
    for e in body["events"]:
        m = re.match(r"(\d{4}-\d\d-\d\d)T(\d\d):(\d\d)", str(e.get("eventDateTime") or ""))
        if not m or m.group(1) != date:
            continue
        if cinema and str(e.get("cinemaId")) != str(cinema):
            continue
        f = films.get(e.get("filmId")) or {}
        name = (f.get("name") or "").strip()
        if not name:
            continue
        attrs = e.get("attributeIds") or []
        meta = {"url": f.get("link"), "runtime": f.get("length"),
                "year": f.get("releaseYear"),
                "genres": [CC_GENRES[a] for a in (f.get("attributeIds") or []) if a in CC_GENRES],
                "hall": e.get("auditorium"), "booking": e.get("bookingLink"),
                "format": [a for a in attrs if a in CC_FORMATS]}
        if "tbc" in attrs:
            meta["tbc"] = True
        rows.append((name, date, f"{m.group(2)}:{m.group(3)}", meta))
    return rows


def fetch_cinemacity(session, venue, today, last_day, max_days=21):
    cinema = CC_CINEMAS[venue]
    counts, rows, zeros = [], [], 0
    for i in range(max_days):
        d = _next(today, i)
        if d > last_day:
            break
        payload = session.json(CC_API.format(cinema=cinema, date=d))
        if payload is None:
            if i == 0:
                raise SourceBroken("Cinema City API unreachable")
            counts.append((d, None))
            break
        day = parse_cinemacity_day(payload, d, cinema)
        counts.append((d, len(day)))
        rows += day
        zeros = zeros + 1 if not day else 0
        if zeros >= 2 and i >= 7:
            break
    end = published_through(counts, 0.2)
    if end is None:
        raise SourceBroken("Cinema City API returned no programme")
    return OfficialResult(venue, "cinemacity.bg quickbook API", rows, today, end,
                          days=dict(counts))


# ------------------------------------------------------- Кино Арена (HTML)
ARENA_URL = "https://www.kinoarena.com/bg/program/view/{slug}/{dmy}"
ARENA_BASE = "https://www.kinoarena.com"
ARENA_SLUGS = {"arena-mega": "arena-mega-mol", "arena-mall": "kino-arena-the-mall"}


def parse_kinoarena_page(html, slug):
    """Returns (selected_date, tab_dates, rows). The page must be this cinema
    (#cinema_choice) — rows carry no date: they belong to selected_date, which
    the caller must compare with the date it asked for."""
    s = _soup(html)
    opt = s.select_one("#cinema_choice option[selected]")
    if not opt or not (opt.get("value") or "").rstrip("/").endswith("/" + slug):
        raise SourceBroken(f"Кино Арена page is not the {slug} programme")
    tabs, selected = [], None
    for a in s.select(".projectionDays a.tabItem"):
        m = re.search(r"/(\d{2})-(\d{2})-(\d{4})/?$", a.get("href") or "")
        if not m:
            continue
        iso = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        tabs.append(iso)
        if "selected" in (a.get("class") or []):
            selected = iso
    rows = []
    for row in s.select("div.scheduleRow"):
        a = row.select_one("header.rowHeader h5.title a") or row.select_one("h5.title")
        title = a.get_text(" ", strip=True) if a else ""
        if not title:
            continue
        href = a.get("href") if a.name == "a" else None
        url = urllib.parse.urljoin(ARENA_BASE, href) if href else None
        for sp in row.select(".timeTable span.time"):
            t = _hhmm(sp.get_text(" ", strip=True))
            if not t:
                continue
            line = sp.find_parent("div", class_="row")
            fmt = [x.get("data-title") for x in (line.select(".attr .item") if line else [])
                   if x.get("data-title")]
            bk = sp.find_parent("a")
            rows.append((title, None, t, {"url": url, "format": fmt,
                                           "booking": urllib.parse.urljoin(ARENA_BASE, bk.get("href"))
                                           if bk and bk.get("href") else None}))
    return selected, tabs, rows


def fetch_kinoarena(session, venue, today, last_day):
    slug = ARENA_SLUGS[venue]

    def page(iso):
        d = _d(iso)
        r = session.get(ARENA_URL.format(slug=slug, dmy=d.strftime("%d-%m-%Y")))
        return None if r is None else parse_kinoarena_page(r.text, slug)

    first = page(today)
    if first is None:
        raise SourceBroken("Кино Арена unreachable")
    selected, tabs, rows0 = first
    tabset = sorted(set(tabs))
    if not tabset:
        raise SourceBroken("Кино Арена page has no day tabs")
    notes, counts, rows = [], [], []
    if selected == today:
        counts.append((today, len(rows0)))
        rows += [(t, today, tm, m) for t, _, tm, m in rows0]
    elif today not in tabset and selected == tabset[0] == _next(today):
        counts.append((today, 0))            # nothing left today; tomorrow is the first tab
        counts.append((selected, len(rows0)))
        rows += [(t, selected, tm, m) for t, _, tm, m in rows0]
    else:
        raise SourceBroken(f"asked for {today}, Кино Арена served {selected}")
    d = counts[-1][0]
    while True:
        d = _next(d)
        if d > last_day or d not in tabset:
            break                           # the contiguous published run ended
        got = page(d)
        if got is None:
            counts.append((d, None))
            break
        sel, _, day = got
        if sel != d:
            notes.append(f"{d}: page fell back to {sel} — not counted")
            counts.append((d, None))
            break
        counts.append((d, len(day)))
        rows += [(t, d, tm, m) for t, _, tm, m in day]
    end = published_through(counts, 0.2)
    if end is None:
        raise SourceBroken("Кино Арена: no normal programme day")
    return OfficialResult(venue, "kinoarena.com programme pages", rows, today, end,
                          notes=notes, days=dict(counts))


# ------------------------------------------------------- Cine Grand (HTML)
CG_BASE = "https://www.cinegrand.bg"
CG_CINEMAS = {"cg-ring": ("софия-ринг-мол", "BGSOFCG2"),
              "cg-park": ("парк-център-софия", "BGSOFCG1")}
CG_SELECT = CG_BASE + "/site/default/select-cinema"


def parse_cinegrand_page(html, slug, code, today):
    """Returns (selected_date, [(date, url)] day tabs, rows, cinema_ok). Each
    show carries its own "чт,  8 окт, 11:50, Зала 2" label, so every row is
    dated individually; cinema_ok is False when the page shows another
    cinema's programme (ticket links or poster paths of a different code)."""
    s = _soup(html)
    tabs, selected = [], None
    for li in s.select("ul.calendar-schedule li.schedule-day"):
        a = li.find("a")
        cls = li.get("class") or []
        if not a or "more" in cls:
            continue
        iso = _bg_day_month(a.get("title") or "", today)
        if not iso:
            continue
        tabs.append((iso, urllib.parse.urljoin(CG_BASE, a.get("href") or "")))
        if "today" in cls:                  # the site marks the SELECTED day "today"
            selected = iso
    rows, foreign = [], 0
    for li in s.select("ul.movie-list li.movie"):
        h = li.select_one("h2.movie-title")
        title = h.get_text(" ", strip=True) if h else ""
        if not title:
            continue
        link = li.select_one("a.movie-poster[href]") or li.select_one(".movie-info a[href]")
        url = urllib.parse.urljoin(CG_BASE, link.get("href")) if link else None
        info = " ".join(p.get_text(" ", strip=True) for p in li.select("div.movie-info p"))
        rt = re.search(r"(\d{2,3})\s*мин", info)
        gm = re.search(r"Жанр:\s*(.+)$", info)
        genres = [g.strip() for g in gm.group(1).split(",") if g.strip()] if gm else []
        for st in li.select("div.movie-schedule a.show-time"):
            t = _hhmm((st.select_one("span.time") or st).get_text(" ", strip=True))
            d = _bg_day_month(st.get("title") or "", today)
            if not t or not d:
                continue
            buy = urllib.parse.unquote(st.get("data-buy") or "")
            if buy and f"cinema_slug={slug}" not in buy:
                foreign += 1
            hall = st.select_one("span.hall")
            fmt = st.select_one("span.info")
            rows.append((title, d, t, {"url": url, "runtime": int(rt.group(1)) if rt else None,
                                       "genres_text": genres,
                                       "hall": hall.get_text(" ", strip=True) if hall else None,
                                       "format": [fmt.get_text(" ", strip=True)] if fmt else []}))
    codes = set(re.findall(r"/files/movie-image/(BG[A-Z0-9]+)/", html))
    cinema_ok = foreign == 0 and (not codes or codes == {code})
    return selected, tabs, rows, cinema_ok


def fetch_cinegrand(session, venue, today, last_day):
    slug, code = CG_CINEMAS[venue]
    sched = f"{CG_BASE}/{slug}/schedule"
    notes = []

    def get(url):
        r = session.get(url)
        return None if r is None else (r.text, parse_cinegrand_page(r.text, slug, code, today))

    first = get(sched)
    if first is None:
        raise SourceBroken("Cine Grand unreachable")
    html, (selected, tabs, rows0, ok) = first
    if not ok:
        # Older site versions chose the cinema by session cookie; select it the
        # way the site's own form does, then read the page again.
        meta = _soup(html).select_one("meta[name=csrf-token]")
        if meta and meta.get("content"):
            session.post(CG_SELECT, {"cinema_code": code, "_csrf": meta["content"]}, referer=sched)
            first = get(sched)
            if first:
                html, (selected, tabs, rows0, ok) = first
        if not ok:
            raise SourceBroken(f"Cine Grand pages do not carry cinema {code}")
    if not tabs:
        raise SourceBroken("Cine Grand page has no day tabs")
    counts, rows = [], []
    expect = today
    for iso, url in tabs:
        if iso < today:
            continue
        if iso != expect or iso > last_day:
            break                            # tabs must be consecutive days from today
        expect = _next(iso)
        if iso == today and selected == today:
            day_rows, sel, ok2 = rows0, selected, ok      # /schedule opens on today
        else:
            got = get(url)
            if got is None:
                counts.append((iso, None))
                break
            _, (sel, _, day_rows, ok2) = got
        if not ok2 or sel != iso:
            notes.append(f"{iso}: page showed {sel} / cinema check {'ok' if ok2 else 'FAILED'}")
            counts.append((iso, None))
            break
        good = [r for r in day_rows if r[1] == iso]
        if len(good) != len(day_rows):
            notes.append(f"{iso}: {len(day_rows) - len(good)} show(s) labelled with another date dropped")
        counts.append((iso, len(good)))
        rows += good
    end = published_through(counts, 0.2)
    if end is None:
        raise SourceBroken("Cine Grand: no normal programme day")
    return OfficialResult(venue, "cinegrand.bg schedule pages", rows, today, end,
                          notes=notes, days=dict(counts))


# ------------------------------------------------------------- G8 (HTML)
G8_URL = "https://g8cinema.com/bg/movies.php"
G8_BASE = "https://g8cinema.com/bg/"


def parse_g8(html):
    """G8's weekly fragment: "Седмична програма DD-MM-YYYY--DD-MM-YYYY" and one
    div.schedule-item per screening (date and "HH:MM - HH:MM" in <time>, the
    title in <h4>, genre and country in <h6>). Returns (from, to, rows)."""
    s = _soup(html)
    text = re.sub(r"\s+", " ", s.get_text(" ", strip=True))
    m = re.search(r"Седмична програма\s*(\d{2})-(\d{2})-(\d{4})\s*-+\s*(\d{2})-(\d{2})-(\d{4})", text)
    if not m:
        raise SourceBroken("G8: no 'Седмична програма' week range")
    wf = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    wt = f"{m.group(6)}-{m.group(5)}-{m.group(4)}"
    rows = []
    for it in s.select("div.schedule-item"):
        if "leg_m" in (it.get("class") or []):
            continue
        tnode = it.select_one("time")
        h4 = it.select_one("h4")
        if not tnode or not h4:
            continue
        dm = re.search(r"(\d{2})-(\d{2})-(\d{4})", tnode.get_text(" ", strip=True))
        strong = tnode.select_one("strong")
        t = _hhmm(strong.get_text(" ", strip=True) if strong else "")
        title = h4.get_text(" ", strip=True)
        if not dm or not t or not title:
            continue
        d = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"
        if not (wf <= d <= wt):
            continue
        a = it.select_one("a[href]")
        full = it.get_text(" ", strip=True)
        rt = re.search(r"(\d{2,3})\s*мин", full)
        h6 = [x.get_text(" ", strip=True) for x in it.select("h6")]
        rows.append((title, d, t, {"url": urllib.parse.urljoin(G8_BASE, a.get("href")) if a else None,
                                   "runtime": int(rt.group(1)) if rt else None,
                                   "genres_text": h6[:1], "country": h6[1] if len(h6) > 1 else None}))
    return wf, wt, rows


def fetch_g8(session, venue, today, last_day):
    r = session.get(G8_URL, referer=G8_BASE, headers={"X-Requested-With": "XMLHttpRequest"})
    if r is None:
        raise SourceBroken("G8 programme unreachable")
    wf, wt, rows = parse_g8(r.text)
    if wt < today:
        raise SourceBroken(f"G8 still shows the past week {wf}..{wt}")
    cf, ct = max(wf, today), min(wt, last_day)
    if not any(cf <= r_[1] <= ct for r_ in rows):
        raise SourceBroken(f"G8 week {wf}..{wt}: no screenings parsed")
    return OfficialResult(venue, "g8cinema.com weekly programme", rows, cf, ct,
                          notes=[f"week {wf}..{wt}"])


# --------------------------------------------------------- Одеон (HTML)
ODEON_INDEX = "http://bnf.bg/bg/odeon/program/"


def parse_odeon_page(raw):
    """A BNF programme page (windows-1251). Returns dict(number, next, rows,
    dates). Rows: <td class="date">DD.MM.YYYY</td> opens a day, then
    <td>HH:MM</td><td><a href=film>Title</a></td><td>accent</td>."""
    if isinstance(raw, bytes):
        head = raw[:2000].decode("ascii", errors="ignore").lower()
        enc = "utf-8" if "charset=utf-8" in head else "cp1251"
        txt = raw.decode(enc, errors="replace")
    else:
        txt = raw
    s = _soup(txt)
    num = None
    m = re.search(r"/odeon/program/(\d+)/(?:date|title)/", txt)
    if m:
        num = int(m.group(1))
    nxt = None
    for a in s.select("a[href]"):
        if "следващи" in a.get_text(" ", strip=True).lower() and "/odeon/program/" in a["href"]:
            nxt = urllib.parse.urljoin(ODEON_INDEX, a["href"])
    rows, dates, cur = [], [], None
    tb = s.select_one("table.tbl-2 tbody") or s.select_one("table.tbl-2")
    for tr in (tb.select("tr") if tb else []):
        td = tr.select_one("td.date")
        if td is not None:
            dm = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", td.get_text(" ", strip=True))
            cur = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}" if dm else None
            if cur:
                dates.append(cur)
            continue
        tds = tr.find_all("td", recursive=False)
        if cur is None or len(tds) < 2:
            continue
        t = _hhmm(tds[0].get_text(" ", strip=True))
        a = tds[1].select_one("a")
        title = (a or tds[1]).get_text(" ", strip=True)
        if not t or not title:
            continue
        rows.append((title, cur, t, {"url": urllib.parse.urljoin(ODEON_INDEX, a["href"]) if a and a.get("href") else None,
                                     "series": tds[2].get_text(" ", strip=True) if len(tds) > 2 else None}))
    return {"number": num, "next": nxt, "rows": rows, "dates": dates}


def fetch_odeon(session, venue, today, last_day):
    r = session.get(ODEON_INDEX)
    if r is None:
        raise SourceBroken("BNF/Одеон programme unreachable")
    pages = [parse_odeon_page(r.content)]
    notes = []
    while pages[-1]["next"] and len(pages) < 3:
        r = session.get(pages[-1]["next"])
        if r is None:
            notes.append(f"next programme {pages[-1]['next']} unreachable — coverage stops before it")
            break
        p = parse_odeon_page(r.content)
        if not p["rows"]:
            notes.append(f"programme {p['number']} is still an empty shell")
            break
        pages.append(p)
    pages = [p for p in pages if p["rows"]]
    if not pages:
        raise SourceBroken("Одеон: no programme rows parsed")
    # each programme covers its own first..last date; consecutive programmes must
    # touch, otherwise coverage stops at the gap.
    cf, ct = min(pages[0]["dates"]), max(pages[0]["dates"])
    rows = list(pages[0]["rows"])
    for p in pages[1:]:
        if min(p["dates"]) > _next(ct):
            notes.append(f"gap before programme {p['number']} — not covered")
            break
        ct = max(ct, max(p["dates"]))
        rows += p["rows"]
    if ct < today:
        raise SourceBroken(f"Одеон programme ends {ct}, before today")
    nums = ", ".join(str(p["number"]) for p in pages)
    return OfficialResult(venue, f"bnf.bg Одеон programme ({nums})", rows,
                          max(cf, today), min(ct, last_day), notes=notes)


# ------------------------------------------------- Дом на киното (HTML/day)
DK_URL = "https://domnakinoto.com/home/getDayProgrammings/{dmy}"


def parse_domkino_day(html, date):
    s = _soup(html or "")
    rows = []
    for box in s.select("div.film-box"):
        h = box.select_one("h3.film-title")
        title = h.get_text(" ", strip=True) if h else ""
        if not title:
            continue
        a = box.select_one("a.see-info[href]") or box.select_one("a.image-box[href]") \
            or box.select_one(".text-wrap a[href]")
        info = {}
        for f in box.select(".info-film"):
            sp = [x.get_text(" ", strip=True) for x in f.select("span")]
            if len(sp) >= 2:
                info[sp[0].rstrip(":").lower()] = sp[1]
        fmt = box.select_one("div.info")
        for hb in box.select("a.hour-box"):
            t = _hhmm(hb.get_text(" ", strip=True))
            if not t:
                continue
            rows.append((title, date, t, {"url": a.get("href") if a else None,
                                          "runtime": _int(info.get("времетраене")),
                                          "genres_text": [info["жанр"]] if info.get("жанр") else [],
                                          "format": [fmt.get_text(" ", strip=True)] if fmt else [],
                                          "booking": hb.get("href")}))
    return rows


def fetch_domkino(session, venue, today, last_day, horizon=21):
    counts, rows = [], []
    for i in range(horizon):
        d = _next(today, i)
        if d > last_day:
            break
        r = session.get(DK_URL.format(dmy=_d(d).strftime("%d.%m.%Y")))
        if r is None:
            if i == 0:
                raise SourceBroken("Дом на киното unreachable")
            counts.append((d, None))
            break
        day = parse_domkino_day(r.text, d)
        counts.append((d, len(day)))
        rows += day
    # A small hall: ~4 screenings a day. Festival and special screenings are
    # booked weeks ahead, so a thin day is common; the regular programme is
    # published a Friday–Thursday week at a time — coverage ends at the last
    # week end inside the run of normal days.
    run = published_through(counts, 0.5)
    if run is None:
        raise SourceBroken("Дом на киното: no programme")
    end = week_end_at_or_before(run, today)
    return OfficialResult(venue, "domnakinoto.com day programme", rows, today, end,
                          notes=[f"normal days run to {run}; programme week ends {end}"],
                          days=dict(counts))


# ----------------------------------------------------- Влайкова (own grid)
VLAIKOVA_URL = "https://vlaikovacinema.com/"


def fetch_vlaikova(session, venue, today, last_day):
    import scrape_programs as SP           # its parse_vlaikova is verified 29/29
    soup = session.soup(VLAIKOVA_URL)
    if soup is None:
        raise SourceBroken("Влайкова unreachable")
    window = [_next(today, i) for i in range((_d(last_day) - _d(today)).days + 1)]
    grid = sorted({d for d in (SP.find_date(n.get_text(" ", strip=True), window)
                               for n in soup.select(".cinema-day-title")) if d})
    rows = [(title, d, t, {"url": link})
            for title, _, d, times, link in SP.parse_vlaikova(soup, venue, window, {})
            for t in times]
    grid = [d for d in grid if d >= today]
    if not grid or not rows:
        raise SourceBroken("Влайкова: no dated grid parsed")
    return OfficialResult(venue, "vlaikovacinema.com weekly grid", rows, today,
                          min(grid[-1], last_day), notes=[f"grid days: {', '.join(grid)}"])


# ------------------------------------------------ Люмиер (NDK + KinoCult)
NDK_PROGRAM = "https://www.ndk.bg/en/program"
SANITY_API = "https://i3edy4c2.api.sanity.io/v2024-01-01/data/query/production?query="
SANITY_GROQ = ('*[_type=="screening" && date >= "{today}"]|order(date asc)'
               '{{date,time,venueBg,venue,ticketUrl,soldOut,"titleBg":movie->titleBg,'
               '"title":movie->title,"year":movie->year,"duration":movie->duration}}')


def parse_sanity(payload, today, last_day):
    res = (payload or {}).get("result") if isinstance(payload, dict) else None
    if not isinstance(res, list):
        raise SourceBroken("KinoCult API answered without a result list")
    rows = []
    for x in res:
        if "люмиер" not in (x.get("venueBg") or "").lower():
            continue
        d, t = x.get("date") or "", _hhmm(x.get("time") or "")
        title = (x.get("titleBg") or x.get("title") or "").strip()
        if not re.match(r"\d{4}-\d\d-\d\d$", d) or not t or not title or not (today <= d <= last_day):
            continue
        rows.append((title, d, t, {"booking": x.get("ticketUrl"), "original_title": x.get("title"),
                                   "year": x.get("year"), "runtime": x.get("duration")}))
    return rows


def fetch_lumiere(session, venue, today, last_day):
    import scrape_programs as SP
    window = [_next(today, i) for i in range((_d(last_day) - _d(today)).days + 1)]
    soup = session.soup(NDK_PROGRAM)
    payload = session.json(SANITY_API + urllib.parse.quote(SANITY_GROQ.format(today=today)))
    if soup is None or payload is None:
        raise SourceBroken("NDK programme or KinoCult API unreachable — Люмиер kept as before")
    ndk = [(title, d, t, {"url": link})
           for title, _, d, times, link in SP.parse_lumiere(soup, venue, window, {})
           for t in times]
    kc = parse_sanity(payload, today, last_day)
    # One hall: the same date and time in both sources is the same screening.
    # KinoCult's bare title wins (no "Director: … (YEAR)" decoration); NDK's
    # event page stays as the info link and its title as corroboration.
    by_slot = {}
    for title, d, t, meta in kc:
        by_slot[(d, t)] = [title, d, t, dict(meta)]
    for title, d, t, meta in ndk:
        if (d, t) in by_slot:
            by_slot[(d, t)][3]["url"] = meta["url"]
            by_slot[(d, t)][3]["alt_title"] = title
        else:
            by_slot[(d, t)] = [title, d, t, dict(meta)]
    rows = [tuple(v) for _, v in sorted(by_slot.items())]
    if not rows:
        raise SourceBroken("neither NDK nor KinoCult lists a Люмиер screening")
    end = max(r[1] for r in rows)
    return OfficialResult(venue, "ndk.bg programme + KinoCult (Sanity) API", rows, today,
                          min(end, last_day),
                          notes=[f"NDK {len(ndk)} + KinoCult {len(kc)} rows, {len(rows)} distinct"])


# ------------------------------------------------------------- Cineland
def fetch_cineland(session, venue, today, last_day):
    """cineland.bg is an Angular app over a private API; there is no public
    official programme to read. Its listings stay aggregator-only and are
    marked preliminary from today."""
    return None


OFFICIAL = {
    "cc-sofia": fetch_cinemacity, "cc-paradise": fetch_cinemacity,
    "arena-mega": fetch_kinoarena, "arena-mall": fetch_kinoarena,
    "cg-ring": fetch_cinegrand, "cg-park": fetch_cinegrand,
    "g8": fetch_g8, "odeon": fetch_odeon, "dom-kino": fetch_domkino,
    "vlaikova": fetch_vlaikova, "lumiere": fetch_lumiere,
}
NO_OFFICIAL = {"cineland": "cineland.bg is an Angular app on a private API — no public programme"}


def fetch_official(venue, session, today, last_day, errors=None):
    """OfficialResult, or None when the source is unreachable/broken (the
    reason goes into errors[venue]) or the venue has no official source."""
    fn = OFFICIAL.get(venue)
    if fn is None:
        if errors is not None and venue in NO_OFFICIAL:
            errors[venue] = NO_OFFICIAL[venue]
        return None
    try:
        res = fn(session, venue, today, last_day)
    except SourceBroken as e:
        if errors is not None:
            errors[venue] = str(e)
        return None
    except Exception as e:                       # a parser bug must not kill the run
        if errors is not None:
            errors[venue] = f"parser crashed: {e.__class__.__name__}: {e}"
        return None
    if res is None:
        return None
    if res.covered_to < res.covered_from:
        if errors is not None:
            errors[venue] = f"empty coverage {res.covered_from}..{res.covered_to}"
        return None
    return res
