#!/usr/bin/env python3
"""Sofia Gleda — festival programmes as official sources (wave F3).

The owner's decisions (2026-10-09):
  * A festival's own website is a source of film details AND of screenings.
  * At a venue that publishes its own programme (every app cinema), a festival
    screening the venue does not list yet is shown PRELIMINARY until the venue
    lists it — also on dates the venue's programme otherwise covers.
  * At a venue with no programme of its own (НДК Зала 1 = ndk1, Френски институт)
    the festival IS the official source: its screenings there are confirmed.
  * Never invent data. A festival row that the venue's own programme contradicts
    (same film, same day, different time) is not shown; it is logged.
The merge itself lives in scrape_programs.py (merge_festival_rows); this module
only READS, and never writes a file.

One reader per festival. Each returns None — the site is unreachable, or its
markup no longer has the shape the parser was written for (keep the previous
run's festival rows) — or a dict:

    {"festival": id, "name": str, "source": url,
     "films": [{"title_bg", "title_en", "original", "year", "country", "runtime",
                "director", "cast", "syn_bg", "syn_en", "poster", "url",
                "genres_text", "kind"}],
     "screenings": [(title_bg, "YYYY-MM-DD", "HH:MM", venue_name, ticket_url)],
     "covered_from": "YYYY-MM-DD" | None, "covered_to": "YYYY-MM-DD" | None,
     "unknown_venues": {venue_name: screenings}, "outside_sofia": {venue_name: n},
     "notes": [str]}

Contract:
  * `screenings` are Sofia screenings dated today..last_day, at any Sofia venue.
    venue_id() maps a festival's venue name to an app venue id through an explicit
    table and never guesses: a Sofia venue it does not know is reported
    ("unknown_venues") and its screenings are not shown. Screenings outside Sofia
    are dropped here and only counted.
  * covered_from..covered_to are festival days whose programme was read in full;
    both are None while the festival has published no screenings yet (Киномания
    before its programme is out) — that is "nothing yet", not a failure.
  * `films` holds one record per film in `screenings`, carrying exactly what the
    festival's own page prints; a field the page does not print is None.
    kind "programme" marks a page that is not one film (a shorts programme):
    only its title and description are taken from it.

The sites (verified live 2026-10-09):
  Синелибри / CineLibri, www.cinelibri.com — WordPress. robots.txt asks for
    Crawl-delay: 10, which netfetch.CRAWL_DELAY enforces, so a full read (the
    programme page, one page per festival day, one REST call for every film page
    and one for their images, ~28 requests) takes about five minutes. The day
    pages (/15-oktomvri/ … /8-noemvri/, listed on /programa/) are the programme:
    a "Прожекции в София" grid of screenings (film, venue, "8 ноември 2026 г.,
    13:00 часа", ticket button) and a "Прожекции в страната" grid (outside Sofia).
    The grids are rendered into the HTML; the REST API only has the raw shortcodes.
    Film pages come from the REST API (<h1> title, <h4> original title, a
    labelled credits block, a description block, featured_media = the poster).
  Киномания / Kinomania, www.kinomania.bg — static HTML. The programme
    (/bg_BG/programa-tablica.html) lists screenings venue by venue; the full
    programme is announced for ~23 October, before which only a few gala
    screenings are listed. Film pages carry the credits block, the synopsis
    ("Анотация:") and a per-film gallery (its og:image, or a gallery file
    named "…poster…").

    python3 scripts/official_festivals.py                    # every festival, summary
    python3 scripts/official_festivals.py --festival kinomania -v
    python3 scripts/official_festivals.py --json /tmp/festivals.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as _html
import json
import pathlib
import re
import sys
import urllib.parse
from collections import Counter

try:
    from bs4 import BeautifulSoup
except ImportError:                                    # pragma: no cover
    sys.exit("pip install beautifulsoup4 lxml")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


class Unavailable(Exception):
    """The festival's site could not be read this run: keep the previous rows."""


class MarkupError(Unavailable):
    """The page arrived but no longer has the shape the parser was written for."""


FESTIVALS = ("cinelibri", "kinomania")
NAMES = {"cinelibri": "Синелибри / CineLibri", "kinomania": "Киномания / Kinomania"}

# Ticket shops the festivals sell through (verified 2026-10-09), plus the
# festivals' own film pages. A festival row's ticket link may point here.
TICKET_HOSTS = ("cinelibri.com", "kinomania.bg", "epaygo.bg", "urboapp.com")

# ------------------------------------------------------------------- venues
# Festival venue name -> app venue id. Explicit on purpose: a name not listed is
# reported, never guessed ("Арена" could be either Кино Арена). Every name here
# was seen on the festival's own programme on 2026-10-09. Евросинема / Euro
# Cinema is G8 under its old name (the owner: "Euro Cinema does not exist; it is
# G8"). Топлоцентрала and Сцена „Централни хали“ show films only in festivals:
# cinema venues of their own (toplo-film — `toplo` is the theatre —, tsentralni-hali).
VENUES = {
    "cinelibri": {
        "Зала 1 на НДК": "ndk1",
        "НДК, Зала 1": "ndk1",
        "Кино Люмиер": "lumiere",
        "Дом на киното": "dom-kino",
        "Одеон": "odeon",
        "Кино Одеон": "odeon",
        "Културен център G8": "g8",
        "Влайкова": "vlaikova",
        "Френски институт": "frenski-institut",
        "Cinema City Mall of Sofia": "cc-sofia",
        "Cine Grand Park center": "cg-park",
        "Кино Арена The Mall": "arena-mall",
        "Cineland Bulgaria Mall": "cineland",
        "Casa Libri": "casa-libri",
        "Евросинема": "g8",
        "Euro Cinema": "g8",
        "Топлоцентрала": "toplo-film",
        "Сцена Централни Хали": "tsentralni-hali",
    },
    "kinomania": {
        "НДК - Зала 1": "ndk1",
        "Люмиер": "lumiere",
        "Кино Люмиер": "lumiere",
        "Одеон": "odeon",
        "Дом на киното": "dom-kino",
        "Културен център G8": "g8",
        "Френски институт": "frenski-institut",
        "Влайкова": "vlaikova",
        "Сине Гранд Парк Център": "cg-park",
        "Синема Сити Парадайс Център": "cc-paradise",
        "Euro Cinema": "g8",
    },
}
# Venue names the festivals use for screenings in other towns (dropped, counted).
OUTSIDE_SOFIA = {
    "cinelibri": {"ФКЦ, Варна"},
    "kinomania": {"Lucky Пловдив", "ФКЦ Варна", "Пловдив - Lucky Дом на киното",
                  "Варна - ФКЦ", "Бургас", "Стара Загора - Регионална библиотека"},
}
# A venue named after another town is outside Sofia whatever the festival calls it.
_OTHER_TOWN = re.compile(
    r"(?<!\w)(?:варна|пловдив|бургас|стара загора|русе|велико търново|благоевград|"
    r"плевен|шумен|габрово|хасково|сливен|ямбол|казанлък|добрич|кърджали|смолян|"
    r"пазарджик|перник|кюстендил|видин|враца|монтана|ловеч|търговище|разград|"
    r"силистра)(?!\w)")


def fold_venue(name):
    """A venue name with case, quotes, dashes and spacing folded."""
    t = (name or "").lower().replace("ё", "е")
    t = re.sub(r"[„“”\"«»‘’'`]", "", t)
    t = re.sub(r"[‐‑‒–—―]", "-", t)
    t = re.sub(r"\s*-\s*", " - ", t)
    t = re.sub(r"\s*,\s*", ", ", t)
    return re.sub(r"\s+", " ", t).strip(" ,.-")


_FOLDED = {f: {fold_venue(k): v for k, v in table.items()} for f, table in VENUES.items()}
_FOLDED_OUT = {f: {fold_venue(k) for k in names} for f, names in OUTSIDE_SOFIA.items()}


def venue_id(festival, name):
    """("app", venue id) | ("outside", None) | ("unknown", None)."""
    k = fold_venue(name)
    vid = _FOLDED.get(festival, {}).get(k)
    if vid:
        return "app", vid
    if k in _FOLDED_OUT.get(festival, set()) or _OTHER_TOWN.search(k):
        return "outside", None
    return "unknown", None


# -------------------------------------------------------------- text helpers
BG_MONTHS = ("януари февруари март април май юни юли август септември "
             "октомври ноември декември").split()
MONTHNUM = {m: i + 1 for i, m in enumerate(BG_MONTHS)}
_DATE_RE = re.compile(r"(\d{1,2})\s+([а-я]+)\s+(\d{4})", re.I)
_TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)")
_SHORTCODE = re.compile(r"\[/?(?:vc_|rev_slider|laborator)[^\]]*\]")


def bg_date(text):
    """'неделя, 8 ноември 2026 г.' -> '2026-11-08' (None if there is no full date)."""
    m = _DATE_RE.search(text or "")
    if not m or m.group(2).lower() not in MONTHNUM:
        return None
    try:
        return dt.date(int(m.group(3)), MONTHNUM[m.group(2).lower()], int(m.group(1))).isoformat()
    except ValueError:
        return None


def hhmm(text):
    m = _TIME_RE.search(text or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def _ws(text):
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _text(fragment):
    """Visible text of an HTML fragment, line breaks kept at block ends."""
    frag = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", fragment or "")
    return BeautifulSoup(frag, "lxml").get_text("")


def _infer_year(day, month, today):
    """Day + month without a year: the candidate nearest to today, never more than
    two months in the past (a programme looks forward)."""
    t = dt.date.fromisoformat(today)
    best = None
    for y in (t.year - 1, t.year, t.year + 1):
        try:
            c = dt.date(y, month, day)
        except ValueError:
            continue
        if (c - t).days < -62:
            continue
        if best is None or abs((c - t).days) < abs((best - t).days):
            best = c
    return best.isoformat() if best else None


def runtime_min(text):
    m = re.search(r"(\d{2,3})\s*мин", text or "")
    return int(m.group(1)) if m else None


def country_year(text):
    """'Чехия, Франция и Испания (2025)' / 'Австрия, Германия, 2026' / 'Франция,
    Люксембург, 2022, 82 мин.' -> ('Чехия/Франция/Испания', 2025). The country
    list is written the app's way (FILMS uses 'САЩ/Канада'); no name is changed."""
    t = _ws(text)
    t = re.sub(r",?\s*\d{2,3}\s*мин\.?.*$", "", t)            # a trailing runtime
    year = None
    m = re.search(r"\(\s*((?:18|19|20)\d{2})\s*\)\s*$", t) or re.search(r",\s*((?:18|19|20)\d{2})\s*(?:г\.?)?\s*$", t)
    if m:
        year = int(m.group(1))
        t = t[:m.start()]
    parts = [p.strip(" .,;") for p in re.split(r",|\s+и\s+", t)]
    parts = [p for p in parts if p and not re.fullmatch(r"\d+", p)]
    return ("/".join(parts) or None), year


def _person_list(text):
    """Credits as printed, minus the trailing 'и др.' ('and others')."""
    t = _ws(text).strip(" .,;:")
    t = re.sub(r",?\s+и\s+др\.?$", "", t)
    return t.strip(" ,;:") or None


def _blank_film(url):
    return {"title_bg": None, "title_en": None, "original": None, "year": None,
            "country": None, "runtime": None, "director": None, "cast": None,
            "syn_bg": None, "syn_en": None, "poster": None, "url": url,
            "genres_text": None, "kind": "film"}


def _https(url):
    url = (url or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    return url


def _host(url):
    h = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


# =============================================================== CineLibri
CL_BASE = "https://www.cinelibri.com"
CL_API = CL_BASE + "/wp-json/wp/v2"
CL_PROGRAMME = CL_API + "/pages?slug=programa&_fields=id,slug,link,content"
CL_PAGES = CL_API + "/pages?per_page=100&_fields=id,slug,title,link,featured_media,content&slug={slugs}"
CL_MEDIA = CL_API + "/media?per_page=100&_fields=id,source_url&include={ids}"
_CL_BANNER = re.compile(r"\[laborator_banner2\b([^\]]*)\]")
_QUOTED = "[\"“”″']([^\"“”″']*)[\"“”″']"
_CL_TITLE = re.compile(r"\btitle\s*=\s*" + _QUOTED)
_CL_HREF = re.compile(r"\bhref\s*=\s*" + _QUOTED)
_CL_ORIG_NOTE = re.compile(r"\(\s*оригинално\s+заглавие\s*:?\s*(.+?)\)\s*$", re.I)
_CL_BRANDING = re.compile(r"(?i)cinelibri[-_ ]?kv|key[-_ ]?visual|logo|banner")


def cinelibri_days(content, today):
    """[(iso date, url)] of the festival's day pages, read from the programme
    page's day banners ('15 октомври' -> https://www.cinelibri.com/15-oktomvri/)."""
    raw = _html.unescape(content or "")
    days = {}
    for m in _CL_BANNER.finditer(raw):
        attrs = m.group(1)
        t, h = _CL_TITLE.search(attrs), _CL_HREF.search(attrs)
        if not t or not h:
            continue
        dm = re.fullmatch(r"\s*(\d{1,2})\s+([а-я]+)\s*", t.group(1).lower())
        href = h.group(1)
        if not dm or dm.group(2) not in MONTHNUM or not href.startswith("url:"):
            continue
        url = _https(urllib.parse.unquote(href[4:].split("|")[0]))
        if _host(url) != "cinelibri.com":
            continue
        iso = _infer_year(int(dm.group(1)), MONTHNUM[dm.group(2)], today)
        if iso:
            days.setdefault(iso, url)
    return sorted(days.items())


def _cl_item(it):
    """One grid card -> {title, venue, date, time, film_url, ticket_url, extra}."""
    head = it.select_one(".vc_gitem-post-data-source-post_title")
    link = it.select_one("a.vc-zone-link[href]") or it.select_one("a.vc_gitem-link[href]")
    title = _ws(head.get_text(" ")) if head else ""
    if not title and link is not None:
        title = _ws(link.get("title"))
    venue = when = extra = None
    for d in it.select(".vc_gitem-acf"):
        txt = _ws(d.get_text(" "))
        if "film_add_info" in (d.get("class") or []):
            extra = txt or None
            continue
        iso, tm = bg_date(txt), hhmm(txt)
        if iso and tm and when is None:
            when = (iso, tm)
        elif txt and venue is None and not iso:
            venue = txt
    ticket = None
    for a in it.select("a[href]"):
        label = (a.get_text(" ") + " " + (a.get("title") or "")).lower()
        if "купи" in label and "билет" in label:
            ticket = _https(a["href"])
    if not (title and venue and when):
        return None
    return {"title": title, "venue": venue, "date": when[0], "time": when[1],
            "film_url": _https(link["href"]) if link is not None else None,
            "ticket_url": ticket, "extra": extra}


def parse_cinelibri_day(html, iso):
    """A day page -> (Sofia items, items outside Sofia, malformed cards).

    A day with screenings in other towns has two grids, headed "Прожекции в
    София" and "Прожекции в страната"; a day without has one unheaded grid (15
    and 16 October 2026). Items of an unheaded grid are returned as Sofia items:
    the venue table then drops any other town's venue (venue_id) and reports any
    venue it does not know. Raises MarkupError when the page has no grid."""
    soup = BeautifulSoup(html or "", "lxml")
    sofia, other, bad = [], [], 0
    grids = soup.select(".vc_grid-container")
    if not grids:
        raise MarkupError(f"{iso}: no programme grid on the day page")
    headed = any("прожекции в" in _ws(h.get_text(" ")).lower() for h in soup.find_all(["h2", "h3"]))
    for g in grids:
        head = g.find_previous(["h2", "h3"])
        label = _ws(head.get_text(" ")).lower() if head else ""
        if "страната" in label:
            bucket = other
        elif "софия" in label or not headed:
            bucket = sofia
        else:
            continue
        for card in g.select(".vc_grid-item"):
            item = _cl_item(card)
            if item is None:
                bad += 1
            else:
                bucket.append(item)
    return sofia, other, bad


def _cl_block(content, marker):
    """The HTML inside the [vc_column_text] whose el_class is `marker`."""
    i = (content or "").find(marker)
    if i < 0:
        return None
    i = content.find("]", i)
    j = content.find("[/vc_column_text]", i)
    if i < 0 or j < 0:
        return None
    return content[i + 1:j]


def _cl_labels(block):
    """'<strong>Режисьор и сценарист:</strong> Кристоф Оноре' pairs -> ({label:
    value}, first unlabelled line). Some pages start with an unlabelled
    'Франция, Япония (2026), 196 мин.' line instead of Държава/Времетраене."""
    labels, free = {}, None
    for para in re.split(r"(?i)</p>|<br\s*/?>", block or ""):
        m = re.search(r"(?is)<strong>(.*?)</strong>(.*)$", para)
        if m:
            lab = _ws(_text(m.group(1))).rstrip(":").strip().lower()
            val = _ws(_text(m.group(2))).lstrip(":").strip()
            if lab and val and lab not in labels:
                labels[lab] = val
            continue
        t = _ws(_text(para))
        if t and free is None and not labels:
            free = t
    return labels, free


def _label(labels, *starts):
    for lab, val in labels.items():
        if any(lab.startswith(s) for s in starts):
            return val
    return None


def _cl_synopsis(content):
    block = _cl_block(content, "film_description")
    if block is None:
        return None
    lines = []
    for line in _text(_SHORTCODE.sub("\n", block)).splitlines():
        s = _ws(line)
        if not s:
            continue
        if re.match(r"(?i)(фестивали и отличия|награди|отличия)\s*:?", s):
            break
        lines.append(s)
    return " ".join(lines).strip() or None


def parse_cinelibri_film(page):
    """A REST page record (content.rendered holds the raw shortcodes) -> film."""
    content = ((page or {}).get("content") or {}).get("rendered") or ""
    cut = content.find("<!-- START Banners")
    if cut >= 0:
        content = content[:cut]
    film = _blank_film(_https((page or {}).get("link")))
    film["media_id"] = (page or {}).get("featured_media") or None
    film["syn_bg"] = _cl_synopsis(content)
    soup = BeautifulSoup(_SHORTCODE.sub("\n", content), "lxml")
    h1 = soup.find("h1")
    if h1 is None:
        # a programme of shorts / an event page: its credits block describes the
        # first short only, so nothing but the title and description is taken
        film["kind"] = "programme"
        film["title_bg"] = _ws(_html.unescape(((page or {}).get("title") or {}).get("rendered") or "")) or None
        return film
    film["title_bg"] = _ws(h1.get_text(" ")) or None
    h4 = h1.find_next("h4")
    if h4 is not None:
        orig = _ws(h4.get_text(" "))
        note = _CL_ORIG_NOTE.search(orig)
        if note:
            orig = note.group(1).strip()
        film["original"] = orig or None
    labels, free = _cl_labels(_cl_block(content, "film_shortinfo"))
    ctext = _label(labels, "държава") or free
    if ctext:
        film["country"], film["year"] = country_year(ctext)
    film["runtime"] = runtime_min(_label(labels, "времетраене") or free or "")
    film["genres_text"] = _label(labels, "жанр")
    film["director"] = _person_list(_label(labels, "режисьор") or "")
    film["cast"] = _person_list(_label(labels, "в ролите", "участват", "с участието") or "")
    return film


def _chunks(seq, n):
    seq = list(seq)
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def read_cinelibri(session, today, last_day, notes):
    prog = session.json(CL_PROGRAMME)
    if not isinstance(prog, list) or not prog or not isinstance(prog[0], dict):
        raise Unavailable("the programme page (/programa/) did not load")
    days = cinelibri_days((prog[0].get("content") or {}).get("rendered"), today)
    if not days:
        raise MarkupError("the programme page lists no festival days")
    want = [(d, u) for d, u in days if today <= d <= last_day]
    items, covered, outside, bad = [], [], Counter(), 0
    for d, url in want:
        if session.budget_spent():
            notes.append(f"{d}: network budget spent — coverage ends before it")
            break
        page = session.text(url)
        if page is None:
            notes.append(f"{d}: day page did not load — coverage ends before it")
            break
        try:
            sofia, other, nbad = parse_cinelibri_day(page, d)
        except MarkupError as e:
            notes.append(f"{e} — coverage ends before it")
            break
        stray = [x for x in sofia if x["date"] != d]
        if stray:
            notes.append(f"{d}: {len(stray)} card(s) dated another day — skipped")
        items += [x for x in sofia if x["date"] == d]
        for x in other:
            if x["date"] == d:
                outside[x["venue"]] += 1
        bad += nbad
        covered.append(d)
    if want and not covered:
        raise Unavailable(notes[-1] if notes else "no day page could be read")
    if covered and not items:
        raise MarkupError("every day page came back without Sofia screenings "
                          "(the grids are no longer rendered into the HTML?)")
    if bad:
        notes.append(f"{bad} programme card(s) without a title, venue or date — skipped")

    # film pages: one REST call per 40 slugs, one more for their images
    def slug_of(url):
        if not url or _host(url) != "cinelibri.com":
            return None
        s = urllib.parse.urlsplit(url).path.strip("/")
        return s if s and "/" not in s else None

    slugs = []
    for x in items:
        s = slug_of(x["film_url"])
        if s and s not in slugs:
            slugs.append(s)
    pages = {}
    for chunk in _chunks(slugs, 40):
        if session.budget_spent():
            notes.append("network budget spent — some film pages not read")
            break
        got = session.json(CL_PAGES.format(slugs=",".join(chunk)))
        if not isinstance(got, list):
            notes.append(f"{len(chunk)} film page(s) did not load")
            continue
        for p in got:
            if isinstance(p, dict) and p.get("slug"):
                pages[p["slug"]] = p
    by_slug = {}
    for slug, p in pages.items():
        try:
            by_slug[slug] = parse_cinelibri_film(p)
        except Exception as e:                       # one odd page never sinks the rest
            notes.append(f"film page {slug}: {e.__class__.__name__}: {e}")
    media_ids = sorted({f["media_id"] for f in by_slug.values() if f.get("media_id")})
    media = {}
    for chunk in _chunks(media_ids, 100):
        if session.budget_spent():
            break
        got = session.json(CL_MEDIA.format(ids=",".join(str(i) for i in chunk)))
        for m in got if isinstance(got, list) else []:
            if isinstance(m, dict) and m.get("id") and m.get("source_url"):
                media[m["id"]] = _https(m["source_url"])
    for f in by_slug.values():
        img = media.get(f.pop("media_id", None))
        # a shorts programme's page carries the festival's key visual
        # ("Cinelibri-KV-18.06.jpg"): festival branding, not the title's poster
        if (img and f["kind"] == "film" and img.startswith("https://")
                and _host(img) == "cinelibri.com" and not _CL_BRANDING.search(img.rsplit("/", 1)[-1])):
            f["poster"] = img

    films, screenings, unknown = {}, [], Counter()
    for x in items:
        s = slug_of(x["film_url"])
        f = by_slug.get(s) if s else None
        if f is None:
            f = _blank_film(x["film_url"])
            if s:
                by_slug[s] = f
        if not f["title_bg"]:
            f["title_bg"] = x["title"]
        title = f["title_bg"]
        films.setdefault(s or title, f)
        kind, _vid = venue_id("cinelibri", x["venue"])
        if kind == "outside":
            outside[x["venue"]] += 1
            continue
        if kind == "unknown":
            unknown[x["venue"]] += 1
        screenings.append((title, x["date"], x["time"], x["venue"], x["ticket_url"]))
    used = {s[0] for s in screenings}
    return _result("cinelibri", CL_BASE + "/programa/",
                   [f for f in films.values() if f["title_bg"] in used],
                   screenings, (covered[0], covered[-1]) if covered else None,
                   unknown, outside, notes)


# =============================================================== Kinomania
KM_BASE = "https://www.kinomania.bg"
KM_PROGRAMME = KM_BASE + "/bg_BG/programa-tablica.html"


def parse_kinomania_programme(html):
    """The programme, venue by venue -> [(venue, date, time, title, film_url,
    ticket_url)]. The list view's class names do not say what they hold (the
    venue is .date_day, the date .hour_list, the time .name_cinema), so each value
    is also checked by shape. Raises MarkupError without the programme block."""
    soup = BeautifulSoup(html or "", "lxml")
    wrap = soup.select_one(".shedule_new .wrapper_list") or soup.select_one(".wrapper_list")
    if wrap is None:
        raise MarkupError("no programme block (.shedule_new .wrapper_list)")
    rows, venue, bad = [], None, 0
    for el in wrap.find_all(recursive=False):
        cls = el.get("class") or []
        if "date_day" in cls:
            venue = _ws(el.get_text(" ")) or None
            continue
        if "inner_list" not in cls:
            continue
        date = tm = None
        for sub in el.find_all(recursive=False):
            scls = sub.get("class") or []
            text = _ws(sub.get_text(" "))
            if "hour_list" in scls:
                date, tm = bg_date(text), None
            elif "name_cinema" in scls:
                tm = hhmm(text)
            elif "cinema_block" in scls:
                a = sub.select_one(".name_film a[href]")
                t = sub.select_one("a.btn_epay[href]") or next(
                    (x for x in sub.select("a[href]") if "билет" in x.get_text(" ").lower()), None)
                title = _ws(a.get_text(" ")) if a is not None else ""
                if venue and date and tm and title:
                    rows.append((venue, date, tm, title, _https(a["href"]),
                                 _https(t["href"]) if t is not None else None))
                else:
                    bad += 1
    return rows, bad


def _km_poster(soup):
    """A gallery file named '…poster…' (a real poster), else the og:image when it
    is one of the film's own gallery files. Site chrome is never taken."""
    def ok(u):
        return u.startswith("https://") and _host(u) == "kinomania.bg" and "/public/uploads/gallery/" in u
    for a in soup.select("a[href]"):
        u = _https(a["href"])
        if ok(u) and "poster" in u.rsplit("/", 1)[-1].lower():
            return u
    og = soup.find("meta", attrs={"property": "og:image"})
    u = _https(og.get("content")) if og is not None else ""
    return u if ok(u) else None


def parse_kinomania_film(html, url):
    """A film page. Its .movieDetails block holds the <h1> title, the gallery, a
    'Title (Original)' line with 'Label: value' lines, then 'Анотация:' and the
    synopsis — wrapped in <h2><p> in ways that differ from film to film (and that
    an HTML parser rearranges), so the block is read as lines of text."""
    html = html or ""
    soup = BeautifulSoup(html, "lxml")
    film = _blank_film(url)
    h1 = soup.select_one(".movieDetails h1") or soup.find("h1")
    title = _ws(h1.get_text(" ")) if h1 is not None else ""
    if not title:
        og = soup.find("meta", attrs={"property": "og:title"})
        title = _ws(og.get("content")) if og is not None else ""
    # some headings carry the original title too: "Фиорд (Fjord)"
    m = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", title)
    if m:
        title, film["original"] = m.group(1).strip(), m.group(2).strip()
    i = html.find('class="movieDetails"')
    j = html.find('class="programaSpot"', max(i, 0))
    region = html[i:j] if 0 <= i < j else ""
    region = re.sub(r"(?is)<ul\b.*?</ul>", "\n", region)          # the gallery
    lines = [_ws(x) for x in _text(region).splitlines() if _ws(x)]
    labels, syn, in_syn = {}, [], False
    for line in lines:
        if in_syn:
            syn.append(line)
            continue
        m = re.match(r"Анотация\s*:?\s*(.*)$", line)
        if m:
            in_syn = True
            if m.group(1):
                syn.append(m.group(1))
            continue
        m = re.match(r"([^:]{2,30}):\s*(.+)$", line)
        if m:
            labels.setdefault(m.group(1).strip().lower(), m.group(2).strip())
        elif title and line.startswith(title) and film["original"] is None:
            m = re.fullmatch(r"\((.+)\)", line[len(title):].strip())
            if m:
                film["original"] = m.group(1).strip() or None
    film["title_bg"] = title or None
    if labels.get("държава"):
        film["country"], _y = country_year(labels["държава"])
    ym = re.search(r"(?:18|19|20)\d{2}", labels.get("година", ""))
    film["year"] = int(ym.group(0)) if ym else None
    film["runtime"] = runtime_min(labels.get("продължителност"))
    film["director"] = _person_list(_label(labels, "режисьор") or "")
    film["cast"] = _person_list(_label(labels, "с участието", "в ролите", "участват") or "")
    film["genres_text"] = _label(labels, "жанр")
    film["syn_bg"] = _ws(" ".join(syn)) or None
    film["poster"] = _km_poster(soup)
    return film


def read_kinomania(session, today, last_day, notes):
    page = session.text(KM_PROGRAMME)
    if page is None:
        raise Unavailable("the programme page did not load")
    rows, bad = parse_kinomania_programme(page)
    if bad:
        notes.append(f"{bad} programme entr(ies) without a venue, date, time or title — skipped")
    if not rows:
        notes.append("no screenings published yet (the full programme is announced for ~23 October)")
        return _result("kinomania", KM_PROGRAMME, [], [], None, Counter(), Counter(), notes)
    dates = sorted(r[1] for r in rows)
    outside, unknown, screenings, urls = Counter(), Counter(), [], {}
    for venue, d, tm, title, film_url, ticket in rows:
        if not (today <= d <= last_day):
            continue
        kind, _vid = venue_id("kinomania", venue)
        if kind == "outside":
            outside[venue] += 1
            continue
        if kind == "unknown":
            unknown[venue] += 1
        screenings.append([title, d, tm, venue, ticket, film_url])
        urls.setdefault(film_url, title)
    films = {}
    for film_url, title in urls.items():
        f = None
        if film_url and _host(film_url) == "kinomania.bg" and not session.budget_spent():
            body = session.text(film_url)
            if body is not None:
                try:
                    f = parse_kinomania_film(body, film_url)
                except Exception as e:
                    notes.append(f"film page {film_url}: {e.__class__.__name__}: {e}")
        if f is None:
            f = _blank_film(film_url)
        f["title_bg"] = f["title_bg"] or title
        films[film_url] = f
    out = []
    for title, d, tm, venue, ticket, film_url in screenings:
        f = films.get(film_url)
        out.append(((f or {}).get("title_bg") or title, d, tm, venue, ticket))
    return _result("kinomania", KM_PROGRAMME, list(films.values()), out,
                   (dates[0], dates[-1]), unknown, outside, notes)


# ===================================================================== API
def _result(fest, source, films, screenings, covered, unknown, outside, notes):
    return {"festival": fest, "name": NAMES[fest], "source": source,
            "films": films, "screenings": sorted(screenings, key=lambda s: (s[1], s[2], s[3], s[0])),
            "covered_from": covered[0] if covered else None,
            "covered_to": covered[1] if covered else None,
            "unknown_venues": dict(unknown), "outside_sofia": dict(outside),
            "notes": notes}


READERS = {"cinelibri": read_cinelibri, "kinomania": read_kinomania}


def read_festival(festival, session, today, last_day, errors=None):
    """The festival's programme for today..last_day, or None (keep previous)."""
    notes = []
    try:
        return READERS[festival](session, today, last_day, notes)
    except Unavailable as e:
        why = str(e)
    except Exception as e:                       # a parser bug must not sink the scrape
        why = f"{e.__class__.__name__}: {e}"
    if errors is not None:
        errors[festival] = why
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--festival", choices=FESTIVALS)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--days", type=int, default=70)
    args = ap.parse_args()
    from netfetch import Fetcher
    today = dt.date.today().isoformat()
    last = (dt.date.today() + dt.timedelta(days=args.days)).isoformat()
    net, errors, out = Fetcher(budget_seconds=900), {}, {}
    for fest in ([args.festival] if args.festival else FESTIVALS):
        res = read_festival(fest, net, today, last, errors)
        out[fest] = res
        if res is None:
            print(f"{fest:10s} UNAVAILABLE — {errors.get(fest)}")
            continue
        apps = Counter(venue_id(fest, s[3])[1] for s in res["screenings"])
        print(f"{fest:10s} {res['covered_from']}..{res['covered_to']}  {len(res['films'])} films, "
              f"{len(res['screenings'])} Sofia screenings  "
              + ", ".join(f"{k or '?'}:{v}" for k, v in sorted(apps.items(), key=lambda kv: str(kv[0]))))
        if res["unknown_venues"]:
            print("           unknown venues: " + ", ".join(f"{k} ({v})" for k, v in res["unknown_venues"].items()))
        if res["outside_sofia"]:
            print("           outside Sofia: " + ", ".join(f"{k} ({v})" for k, v in res["outside_sofia"].items()))
        for n in res["notes"]:
            print("           note: " + n)
        if args.verbose:
            for s in res["screenings"]:
                print("   ", s)
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    net.print_report()


if __name__ == "__main__":
    main()
