#!/usr/bin/env python3
"""Best-effort theatre posters from theatre.art.bg (the Sofia theatre aggregator).

Each day page — https://theatre.art.bg/?date=YYYY-MM-DD&city=20 — lists that
day's performances, each with a thumbnail hosted on theatre.peakview.bg. We walk
the app's date window, harvest (title -> image) pairs, match titles back to the
SHOWS in index.html, upgrade the thumbnail to the largest size the CDN serves,
and write theatre_posters.json: {showId: "https://...poster.jpg"}.

Coverage is expected to be partial; every miss keeps the app's generated SVG.

    python3 scripts/fetch_theatre_posters.py            # -> theatre_posters.json

A run that finds nothing keeps any previous theatre_posters.json.
"""
import json, os, re, time, ssl, sys, pathlib, urllib.request
try:
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    CTX = ssl._create_unverified_context()
try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("pip install beautifulsoup4 lxml")

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 SofiaGleda/1.0"}
ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
OUT  = ROOT / "theatre_posters.json"
DAY_URL = "https://theatre.art.bg/?date={date}&city=20"      # city 20 = Sofia
POLITE = 0.4


def grab(data, name):
    i = data.find("const " + name + "="); j = data.find("=", i) + 1
    depth = 0; start = None
    for k in range(j, len(data)):
        c = data[k]
        if c in "[{":
            if depth == 0: start = k
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                return json.loads(data[start:k + 1])


def norm(s):
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"[„“”\"'’«»\.\,\!\?\:\;\-–—\(\)\[\]]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def get(url, binary=False):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
            return r.status if binary else r.read().decode("utf-8", "replace")
    except Exception:
        return None


def full_size(thumb, pattern):
    """Apply the resolved size pattern to a `150_`-prefixed thumbnail URL."""
    if pattern == "strip":
        return re.sub(r"/(\d+)_([^/]+)$", r"/\2", thumb)
    if pattern.isdigit():
        return re.sub(r"/(\d+)_([^/]+)$", rf"/{pattern}_\2", thumb)
    return thumb                                              # "keep"


def resolve_pattern(sample):
    """Probe once for the biggest size the CDN actually serves for this thumb."""
    for pat in ("strip", "800", "500", "300"):
        cand = full_size(sample, pat)
        if cand != sample and get(cand, binary=True) == 200:
            return pat
        time.sleep(0.2)
    return "keep"


def harvest(html):
    """Return [(title, thumb_url)] from a day page."""
    soup = BeautifulSoup(html, "lxml")
    pairs = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if "peakview.bg" not in src:
            continue
        src = ("https:" + src) if src.startswith("//") else src
        # title: prefer alt, else nearest anchor text, else nearest heading
        title = (img.get("alt") or "").strip()
        if not title:
            a = img.find_parent("a") or img.find_next("a")
            if a:
                title = a.get_text(" ", strip=True)
        if 2 < len(title) < 160:
            pairs.append((title, src))
    return pairs


def main():
    data = HTML.read_text(encoding="utf-8").split("/* SOFIA-DATA-START */")[1].split("/* SOFIA-DATA-END */")[0]
    shows = grab(data, "SHOWS")
    src_all = HTML.read_text(encoding="utf-8")
    win = re.search(r'"?window"?\s*:\s*\{\s*"?from"?\s*:\s*"(\d{4}-\d\d-\d\d)"\s*,\s*"?to"?\s*:\s*"(\d{4}-\d\d-\d\d)"', src_all)
    if not win:
        sys.exit("could not find snapshot window")
    import datetime as dt
    d0, d1 = dt.date.fromisoformat(win.group(1)), dt.date.fromisoformat(win.group(2))
    days = [(d0 + dt.timedelta(days=i)) for i in range((d1 - d0).days + 1)]

    title_to_id = {}
    for s in shows:
        title_to_id[norm(s.get("title"))] = s["id"]
        if s.get("titleEn"):
            title_to_id[norm(s["titleEn"])] = s["id"]

    out, pattern = {}, None
    for day in days:
        if len(out) >= len(title_to_id):
            break                                             # got everything
        page = get(DAY_URL.format(date=day.isoformat()))
        if not page:
            continue
        for title, thumb in harvest(page):
            sid = title_to_id.get(norm(title))
            if not sid or sid in out:
                continue
            if pattern is None:
                pattern = resolve_pattern(thumb)
                print(f"  poster size pattern: {pattern}")
            out[sid] = full_size(thumb, pattern)
            print(f"  {sid:22s} -> {out[sid][:72]}")
        time.sleep(POLITE)

    if not out and OUT.exists():
        print("0 posters this run — keeping previous theatre_posters.json", file=sys.stderr)
        return 0
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nDONE: {len(out)}/{len(shows)} theatre posters -> {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
