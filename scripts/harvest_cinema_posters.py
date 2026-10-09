#!/usr/bin/env python3
"""Sofia Gleda — harvest poster images from Cinema City and Кино Арена.

For each film in FILMS that has no poster from TMDB or film_links_posters,
tries to locate a poster URL from:
  1. Cinema City quickbook API (posterLink per film, every date in the window)
  2. Кино Арена film detail pages (og:image per /bg/movie/<slug> URL)

Stores found URLs as the film's "img" fallback in film_info.json so
inject_data.py can include them in POSTERS (after posterpolicy checks).

Keep-previous: a failed fetch never removes an existing img entry.
Never replaces an existing film_info img (only fills missing ones).
TMDB and film_links_posters entries always take priority — unchanged.

Usage:
    python3 scripts/harvest_cinema_posters.py [--budget N]
"""
from __future__ import annotations
import argparse
import datetime
import json
import pathlib
import re
import sys
import urllib.parse

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("pip install beautifulsoup4 lxml")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from netfetch import Fetcher

ROOT = pathlib.Path(__file__).resolve().parent.parent
_html_env = None
try:
    import os as _os
    _html_env = _os.environ.get("SOFIA_HTML")
except Exception:
    pass
HTML = pathlib.Path(_html_env) if _html_env else ROOT / "index.html"
OUT = ROOT / "film_info.json"
TMDB_JSON = ROOT / "tmdb_films.json"
LINKS_JSON = ROOT / "film_links_posters.json"

# ---------------------------------------- CC API constants
CC_API = ("https://www.cinemacity.bg/bg/data-api-service/v1/quickbook/10106/film-events/"
          "in-cinema/{cinema}/at-date/{date}?attr=&lang=bg_BG")
CC_CINEMAS = {"cc-sofia": "1261", "cc-paradise": "1266"}

# ---------------------------------------- KA constants
ARENA_URL = "https://www.kinoarena.com/bg/program/view/{slug}/{dmy}"
ARENA_BASE = "https://www.kinoarena.com"
ARENA_SLUGS = {"arena-mega": "arena-mega-mol", "arena-mall": "kino-arena-the-mall"}


# ---------------------------------------- helpers

def _extract_array(src, name):
    m = re.search(r"const\s+" + name + r"\s*=\s*\[", src)
    if not m:
        raise KeyError(name)
    start = m.end() - 1
    depth, i, in_str, quote, esc = 0, start, False, "", False
    while i < len(src):
        c = src[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                in_str = False
        elif c in "\"'":
            in_str, quote = True, c
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise ValueError(f"unterminated array {name}")


def _is_bad_img(url):
    """Reject site-chrome / placeholder images."""
    bad = ("logo", "placeholder", "default-", "/default", "fallback",
           "avatar", "sprite", "share-", "/share.", "og-image.png")
    return any(b in url.lower() for b in bad)


# ---------------------------------------- Film index (title -> id)

def _build_title_index(films):
    """Return a dict mapping normalised BG title -> film id."""
    import unicodedata

    def norm(s):
        s = (s or "").lower()
        s = unicodedata.normalize("NFC", s)
        s = re.sub(r'[„"""\'‘’«»`.,!?:;—–\-_\(\)\[\]/\\]', " ", s)
        return re.sub(r"\s+", " ", s).strip()

    # Also use the film_identity module if available
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import film_identity as FI
        index = FI.FilmIndex(films)
        return index, norm
    except Exception:
        return None, norm


# ---------------------------------------- CC harvesting

def harvest_cc_posters(session, today, last_day):
    """Fetch CC API for all dates; return {normalised_title: poster_url}."""
    result = {}
    d = datetime.date.fromisoformat(today)
    end = datetime.date.fromisoformat(last_day)
    while d <= end:
        date_str = d.isoformat()
        for venue, cinema in CC_CINEMAS.items():
            url = CC_API.format(cinema=cinema, date=date_str)
            payload = session.json(url)
            if payload is None:
                continue   # this cinema's day failed; the other cinema and later days still count
            for f in (payload.get("body") or {}).get("films", []):
                name = (f.get("name") or "").strip()
                poster = (f.get("posterLink") or "").strip()
                if name and poster and poster.startswith("https://") and not _is_bad_img(poster):
                    if name not in result:
                        result[name] = poster
        d += datetime.timedelta(days=1)
    return result


# ---------------------------------------- KA harvesting

def _ka_film_urls(session, today, last_day):
    """Walk KA schedule pages; return {title: film_detail_url}."""
    film_urls = {}
    d = datetime.date.fromisoformat(today)
    end = datetime.date.fromisoformat(last_day)
    while d <= end:
        dmy = d.strftime("%d-%m-%Y")
        for venue, slug in ARENA_SLUGS.items():
            url = ARENA_URL.format(slug=slug, dmy=dmy)
            r = session.get(url)
            if r is None:
                d += datetime.timedelta(days=1)
                continue
            soup = BeautifulSoup(r.text, "lxml")
            # Verify this page is for the right cinema
            opt = soup.select_one("#cinema_choice option[selected]")
            if not opt or not (opt.get("value") or "").rstrip("/").endswith("/" + slug):
                continue
            # Check selected date tab
            selected = None
            for a in soup.select(".projectionDays a.tabItem"):
                if "selected" in (a.get("class") or []):
                    m = re.search(r"/(\d{2})-(\d{2})-(\d{4})/?$", a.get("href") or "")
                    if m:
                        selected = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            if selected and selected != d.isoformat():
                # KA is serving a different date — we've reached coverage end for this venue
                continue
            for row in soup.select("div.scheduleRow"):
                a = row.select_one("header.rowHeader h5.title a") or row.select_one("h5.title")
                title = a.get_text(" ", strip=True) if a else ""
                href = a.get("href") if a and a.name == "a" else None
                full_url = urllib.parse.urljoin(ARENA_BASE, href) if href else None
                if title and full_url and title not in film_urls:
                    film_urls[title] = full_url
        d += datetime.timedelta(days=1)
    return film_urls


def harvest_ka_posters(session, today, last_day):
    """Collect KA film detail URLs then fetch og:image; return {title: poster_url}."""
    film_urls = _ka_film_urls(session, today, last_day)
    print(f"  KA: found {len(film_urls)} unique film page URLs")
    result = {}
    for title, url in sorted(film_urls.items()):
        r = session.get(url)
        if r is None:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        og = soup.find("meta", attrs={"property": "og:image"})
        if og:
            poster = (og.get("content") or "").strip()
            if poster.startswith("https://") and not _is_bad_img(poster):
                result[title] = poster
    return result


# ---------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=600,
                    help="Network budget in seconds (default 600)")
    args = ap.parse_args()

    src = HTML.read_text(encoding="utf-8")
    films = json.loads(_extract_array(src, "FILMS"))
    print(f"FILMS: {len(films)} entries")

    # Poster coverage already present
    tmdb = json.loads(TMDB_JSON.read_text()) if TMDB_JSON.exists() else {}
    tmdb_poster = {fid for fid, v in tmdb.items() if v.get("poster_path")}

    flp = json.loads(LINKS_JSON.read_text()) if LINKS_JSON.exists() else {}
    flp_set = {fid for fid, v in flp.items() if v}

    # Load existing film_info (keep-previous)
    prev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            prev = {}

    fi_img = {fid for fid, v in prev.items() if v.get("img")}

    # Films that still need a poster
    need_poster = [f for f in films if
                   f["id"] not in tmdb_poster and
                   f["id"] not in flp_set and
                   f["id"] not in fi_img]
    print(f"Films needing poster: {len(need_poster)} (of {len(films)} total)")

    if not need_poster:
        print("  Nothing to harvest — all films have poster coverage.")
        return 0

    # Build title index using FilmIndex
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import film_identity as FI
        index = FI.FilmIndex(films)

        def resolve(title):
            fid, _rule = index.resolve(title, "cc-sofia")
            if not fid:
                fid, _rule = index.resolve(title, "arena-mega")
            return fid
    except Exception as e:
        print(f"  WARNING: film_identity unavailable ({e}); falling back to exact match")
        film_by_bg = {(f.get("bg") or "").strip(): f["id"] for f in films}
        def resolve(title):
            return film_by_bg.get(title.strip())

    # Determine date range
    today = datetime.date.today().isoformat()
    last_day = "2026-12-14"

    session = Fetcher(budget_seconds=args.budget)

    # ---- Cinema City ----
    print("Harvesting Cinema City posters …")
    cc_posters = harvest_cc_posters(session, today, last_day)
    print(f"  CC: {len(cc_posters)} unique film→poster pairs collected")

    # ---- Кино Арена ----
    print("Harvesting Кино Арена posters …")
    ka_posters = harvest_ka_posters(session, today, last_day)
    print(f"  KA: {len(ka_posters)} poster URLs collected")

    # ---- Map to film IDs and merge into film_info ----
    out = dict(prev)
    n_added_cc = n_added_ka = n_skipped = 0
    added_pairs = []

    all_source_posters = []
    for title, url in cc_posters.items():
        all_source_posters.append((title, url, "CC"))
    for title, url in ka_posters.items():
        all_source_posters.append((title, url, "KA"))

    for title, url, source in all_source_posters:
        fid = resolve(title)
        if not fid:
            continue
        # Skip if already has any poster (keep-previous + priority)
        if fid in tmdb_poster or fid in flp_set:
            continue
        if out.get(fid, {}).get("img"):
            continue
        entry = dict(out.get(fid, {}))
        entry["img"] = url
        out[fid] = entry
        added_pairs.append((fid, title, url, source))
        if source == "CC":
            n_added_cc += 1
        else:
            n_added_ka += 1

    print(f"\nAdded: {n_added_cc} from Cinema City, {n_added_ka} from Кино Арена")
    for fid, title, url, source in sorted(added_pairs):
        print(f"  [{source}] {fid}: {url[:80]}")

    # Write film_info.json
    out_clean = {k: v for k, v in out.items() if v}
    OUT.write_text(json.dumps(out_clean, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{OUT.name}: {len(out_clean)} entries written")

    # Re-count films still without poster
    fi_img_new = {fid for fid, v in out_clean.items() if v.get("img")}
    still_need = [f for f in films if
                  f["id"] not in tmdb_poster and
                  f["id"] not in flp_set and
                  f["id"] not in fi_img_new]
    print(f"\nFilms still without poster: {len(still_need)}")
    for f in sorted(still_need, key=lambda x: x["id"])[:20]:
        print(f"  {f['id']} | {f.get('bg', '')[:60]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
