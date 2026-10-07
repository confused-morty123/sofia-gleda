#!/usr/bin/env python3
"""Match every film in the app to TMDB for real posters + canonical English
titles + ratings. Writes tmdb_films.json next to the app.

Reads the film list from the inline dataset of index.html (between the
SOFIA-DATA markers). The TMDB read token comes from the TMDB_TOKEN environment
variable (a GitHub Actions secret in the hosted setup) — never hard-coded.

    TMDB_TOKEN=... python3 scripts/fetch_tmdb.py            # -> tmdb_films.json

If a run matches nothing (e.g. TMDB unreachable) and a previous tmdb_films.json
exists, the previous file is kept — a bad run never empties the posters.
"""
import json, os, re, time, urllib.parse, urllib.request, ssl, sys, pathlib

try:
    import certifi
    SSLCTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSLCTX = ssl._create_unverified_context()

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
OUT  = ROOT / "tmdb_films.json"
LINKS_OUT = ROOT / "film_links_posters.json"
TOKEN = os.environ.get("TMDB_TOKEN", "").strip()
# Genuine IMDb rating + vote count come from OMDb (TMDB only exposes its own
# vote_average, a different metric we deliberately never show as "IMDb"). Keyed by
# the imdb_id TMDB gives us. A GitHub Actions secret in the hosted setup — never
# hard-coded. Absent token => ratings are inherited from the previous run.
OMDB_TOKEN = os.environ.get("OMDB_TOKEN", "").strip()

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15")

# Re-release / restoration decoration that is NOT part of a film's canonical title
# and makes TMDB return nothing (e.g. "Cars (20th Anniversary)", "The Shining
# (Restored)"). Stripped only as a FALLBACK, after the decorated title misses, so a
# film with an IMDb rating and a synopsis stops falling through to the grey gradient.
_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")
_DECOR_RE = re.compile(
    r"\s*[-–—]\s*(?:\d+\s*(?:th|st|nd|rd)?\s*)?"
    r"(?:anniversary|restored|restoration|remastered|re-?release|director'?s cut|"
    r"final cut|extended cut|uncut|special edition|redux|4k|imax|70\s*mm)\b.*$",
    re.I)

# og:image fallback: a film TMDB cannot match (Bulgarian/festival titles) still has
# a real still/poster on its own programme page, whose URL is already in the app's
# LINKS array. These are the film's OWN images — honest, never a fabricated match.
_OG_RE  = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I)
_OG_RE2 = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.I)
# Site-wide chrome that is NOT the film's image — never adopt these as a poster.
_BAD_IMG = ("logo", "placeholder", "default-", "/default", "fallback",
            "avatar", "sprite", "share-", "/share.", "og-image.png")


# Films TMDB matches WRONGLY (mostly Bulgarian/festival titles whose English
# name collides with a famous foreign film — e.g. "adat-v-neya" matched "The
# Good, the Bad and the Ugly"). TMDB has no correct entry, so any match here is
# a false poster/title. Keep these on the generated SVG artwork instead. Verified
# 2026-09-17; revisit if TMDB later adds a correct entry for one of them.
BLOCKLIST = {
    "adat-v-neya", "cherno-more", "divo-sarce", "fevruari", "kokoshka",
    "pomilvane", "sentimentalno", "vreme-za-zhivot", "zalog",
}


def grab(data, name):
    i = data.find("const " + name + "=")
    j = data.find("=", i) + 1
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


def api(path, params):
    q = urllib.parse.urlencode(params)
    url = f"https://api.themoviedb.org/3/{path}?{q}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}",
                                               "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=25, context=SSLCTX) as r:
                return json.load(r)
        except Exception as e:
            if attempt == 2:
                print("  ! api error", e, file=sys.stderr); return None
            time.sleep(1.5)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def cnorm(s):
    """Like norm but keeps Cyrillic, so Bulgarian titles can be compared."""
    return re.sub(r"[^a-z0-9а-я]", "", (s or "").lower())


def pick(results, en, year):
    if not results: return None
    ten = norm(en)
    def score(r):
        s = 0
        rt = norm(r.get("title")); ro = norm(r.get("original_title"))
        if rt == ten or ro == ten: s += 100
        elif ten in rt or rt in ten: s += 40
        ry = (r.get("release_date") or "")[:4]
        if ry and year:
            if ry == str(year): s += 50
            elif abs(int(ry) - int(year)) <= 1: s += 20
            else: s -= 10 * min(abs(int(ry) - int(year)), 5)
        s += min(r.get("vote_count", 0), 5000) / 5000.0
        return s
    return max(results, key=score)


def pick_bg(results, bg, year):
    """Conservative match for a Bulgarian-only title (synthesised arthouse films
    have no English title). TMDB is queried with language=bg, so each result's
    `title` is its Bulgarian title; accept ONLY a result whose Bulgarian title
    actually matches ours (exact, or strong containment for a title long enough
    to be unambiguous). A loose Cyrillic match is rejected so an arthouse film
    can never pick up a wrong, fabricated poster — it keeps its placeholder."""
    nb = cnorm(bg)
    if not nb or not results:
        return None
    best, bestscore = None, 0.0
    for r in results:
        rt = cnorm(r.get("title")); ro = cnorm(r.get("original_title"))
        s = 0.0
        if nb == rt or nb == ro:
            s = 100.0
        elif len(nb) >= 6 and (nb in rt or (rt and rt in nb)):
            s = 60.0
        if not s:
            continue
        ry = (r.get("release_date") or "")[:4]
        if ry and year and ry == str(year):
            s += 20.0
        s += min(r.get("vote_count", 0), 5000) / 5000.0
        if s > bestscore:
            best, bestscore = r, s
    return best if bestscore >= 60.0 else None


def declutter(title):
    """Strip re-release/restoration decoration so the canonical title can match."""
    t = (title or "").strip()
    prev = None
    while t and t != prev:
        prev = t
        t = _PAREN_RE.sub("", t).strip()
    t = _DECOR_RE.sub("", t).strip()
    return t


def search_en(en, year):
    """Query TMDB by English title (with, then without, the year) and pick best."""
    res = api("search/movie", {"query": en, "year": year, "include_adult": "false"})
    results = (res or {}).get("results", [])
    if not results:
        res = api("search/movie", {"query": en, "include_adult": "false"})
        results = (res or {}).get("results", [])
    return pick(results, en, year)


def details_en(tmdb_id):
    """English overview + production country + imdb_id from the film's detail record.
    Owner's choice is 'TMDB English where it exists, else Bulgarian' — no machine
    translation, so this authoritative English fills the detail card when the dataset
    has none. The imdb_id is used to look up the genuine IMDb rating via OMDb."""
    d = api(f"movie/{tmdb_id}", {"language": "en-US"})
    if not d:
        return None, None, None
    ov = (d.get("overview") or "").strip() or None
    pcs = d.get("production_countries") or []
    country = (pcs[0].get("name") or "").strip() if pcs else None
    imdb_id = (d.get("imdb_id") or "").strip() or None
    return ov, (country or None), imdb_id


def _votes_compact(n):
    """491700 -> '491.7K', 2_000_000 -> '2.0M' — matches the dataset's vote style."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def omdb_rating(imdb_id):
    """Genuine IMDb rating + vote count via OMDb, keyed by the film's imdb_id. Returns
    (rating_float, votes_compact) or (None, None). Never fabricates: a missing token,
    an 'N/A' rating, or any error yields None so the score tile honestly stays '—'.
    TMDB's own vote_average is NOT used here — it is a different metric, not IMDb."""
    if not OMDB_TOKEN or not imdb_id:
        return None, None
    try:
        q = urllib.parse.urlencode({"i": imdb_id, "apikey": OMDB_TOKEN})
        req = urllib.request.Request(f"https://www.omdbapi.com/?{q}",
                                     headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20, context=SSLCTX) as r:
            d = json.load(r)
    except Exception as e:
        print("    ! omdb error", e, file=sys.stderr)
        return None, None
    if d.get("Response") == "False":
        return None, None
    rating = d.get("imdbRating")
    try:
        rating = float(rating) if rating and rating != "N/A" else None
    except (ValueError, TypeError):
        rating = None
    votes_raw = (d.get("imdbVotes") or "").replace(",", "")
    votes = _votes_compact(int(votes_raw)) if votes_raw.isdigit() else None
    return rating, votes


def og_image(url):
    """Best-effort og:image from a film's own programme page. Returns a usable image
    URL or None — site chrome (logos, share images) is rejected, never fabricated."""
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=20, context=SSLCTX) as r:
            html = r.read(300000).decode("utf-8", "replace")
    except Exception as e:
        print("    ! og fetch error", e, file=sys.stderr)
        return None
    for rx in (_OG_RE, _OG_RE2):
        m = rx.search(html)
        if m:
            u = m.group(1).strip()
            if u.startswith("//"):
                u = "https:" + u
            if not u.lower().startswith("http"):
                continue
            if any(b in u.lower() for b in _BAD_IMG):
                return None
            return u
    return None


def main():
    if not TOKEN:
        print("TMDB_TOKEN not set — skipping film posters, keeping previous.", file=sys.stderr)
        return 0
    data = HTML.read_text(encoding="utf-8").split("/* SOFIA-DATA-START */")[1].split("/* SOFIA-DATA-END */")[0]
    films = grab(data, "FILMS")
    # id -> detail-page URL (for the og:image fallback). LINKS is a list of [id, url].
    links = {}
    try:
        for row in (grab(data, "LINKS") or []):
            if isinstance(row, (list, tuple)) and len(row) >= 2 and row[0]:
                links.setdefault(row[0], row[1])
    except Exception as e:
        print("  (could not read LINKS for og:image fallback:", e, ")", file=sys.stderr)
    # Carry forward previously-harvested og:image posters so a transient fetch
    # failure never blanks a film that had a real image last run (keep-previous).
    film_links = {}
    if LINKS_OUT.exists():
        try:
            film_links = json.load(open(LINKS_OUT, encoding="utf-8")) or {}
        except Exception:
            film_links = {}
    # Previous run's records, so a missing OMDB_TOKEN or a transient OMDb failure
    # inherits last run's IMDb rating/votes rather than blanking the score tile.
    prev = {}
    if OUT.exists():
        try:
            prev = json.load(open(OUT, encoding="utf-8")) or {}
        except Exception:
            prev = {}

    out = {}
    for i, f in enumerate(films):
        fid = f["id"]
        en, bg, year = f.get("en"), f.get("bg"), f.get("year")
        if fid in BLOCKLIST:
            print(f"[{i+1}/{len(films)}] {fid:24s} BLOCKED (known false match — generated art kept)")
            out[fid] = {"matched": False, "en": en}
            # TMDB has no correct entry, but the film's own programme page does — a
            # real image for this exact title beats the grey gradient.
            img = og_image(links.get(fid))
            if img:
                film_links[fid] = img
                print(f"      og:image -> {img}")
            continue
        # Synthesised arthouse films carry only a Bulgarian title; fall back to it
        # (confirmed via language=bg in pick_bg) so posters/ratings fill where TMDB
        # has them, without ever accepting a loose, fabricated match.
        via_bg = False
        if en:
            best = search_en(en, year)
            if not best:
                # Re-release decoration (e.g. "Cars (20th Anniversary)") blocks the
                # match; retry with the canonical title before giving up.
                clean = declutter(en)
                if clean and clean.lower() != en.lower():
                    best = search_en(clean, year)
                    if best:
                        print(f"      (matched after declutter: {en!r} -> {clean!r})")
        elif bg:
            via_bg = True
            res = api("search/movie", {"query": bg, "language": "bg", "include_adult": "false"})
            results = (res or {}).get("results", [])
            best = pick_bg(results, bg, year)
        else:
            best = None
        if not best:
            print(f"[{i+1}/{len(films)}] {fid:24s} NO MATCH ({en or bg} {year})")
            out[fid] = {"matched": False, "en": en}
            img = og_image(links.get(fid))
            if img:
                film_links[fid] = img
                print(f"      og:image -> {img}")
            continue
        # When matched via the Bulgarian title the result's `title` is Bulgarian,
        # so only trust `original_title` as an English name when the film is
        # actually English-language; otherwise leave en empty and let the UI show
        # the Bulgarian title rather than invent an English one.
        if via_bg:
            en_title = best.get("original_title") if best.get("original_language") == "en" else (en or "")
        else:
            en_title = best.get("title") or en
        rec = {
            "matched": True,
            "tmdb_id": best["id"],
            "poster_path": best.get("poster_path"),
            "backdrop_path": best.get("backdrop_path"),
            "en": en_title,
            "original_title": best.get("original_title"),
            "orig_lang": best.get("original_language"),
            "tmdb_year": (best.get("release_date") or "")[:4],
            "tmdb_vote": best.get("vote_average"),
        }
        # Owner's choice: fill English synopsis/country from TMDB where it exists,
        # Bulgarian otherwise. Theatre shows aren't in TMDB, so this is films only.
        ov, country, imdb_id = details_en(best["id"])
        if ov:
            rec["ov"] = ov
        if country:
            rec["country"] = country
        # Genuine IMDb rating via OMDb (keyed by TMDB's imdb_id). The score tile is
        # labelled "IMDb", so only a real IMDb number may fill it — never TMDB's own
        # vote_average. Keep-previous: no token / failed call inherits last run's value.
        if imdb_id:
            rec["imdb_id"] = imdb_id
        rating, votes = omdb_rating(imdb_id)
        pv = prev.get(fid, {})
        if rating is None and pv.get("imdb") is not None:
            rating = pv["imdb"]
        if not votes and pv.get("imdbVotes"):
            votes = pv["imdbVotes"]
        if rating is not None:
            rec["imdb"] = rating
        if votes:
            rec["imdbVotes"] = votes
        out[fid] = rec
        # A matched film with no TMDB poster still has its own programme-page image.
        if not rec["poster_path"]:
            img = og_image(links.get(fid))
            if img:
                film_links[fid] = img
                print(f"      og:image -> {img}")
        else:
            # Now has a verified TMDB poster: drop any stale og fallback so the
            # higher-priority POSTERS override can never shadow the better image.
            film_links.pop(fid, None)
        flag = "" if rec["poster_path"] else "  (no poster!)"
        print(f"[{i+1}/{len(films)}] {fid:24s} -> {rec['en']} ({rec['tmdb_year']}) id={rec['tmdb_id']}{flag}")
        time.sleep(0.12)

    posters = sum(1 for v in out.values() if v.get("poster_path"))
    # keep-previous guard: never overwrite a good cache with an empty run
    if posters == 0 and OUT.exists():
        print("0 posters this run — keeping previous tmdb_films.json", file=sys.stderr)
        return 0
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # Only films without a TMDB poster appear here, so this override map stays a
    # pure fallback and never shadows a verified TMDB poster.
    film_links = {k: v for k, v in film_links.items() if v}
    json.dump(film_links, open(LINKS_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    matched = sum(1 for v in out.values() if v.get("matched"))
    rated = sum(1 for v in out.values() if v.get("imdb") is not None)
    print(f"\nDONE: {matched}/{len(films)} matched, {posters} with posters, "
          f"{len(film_links)} og:image fallbacks, {rated} with IMDb ratings "
          f"(OMDb {'on' if OMDB_TOKEN else 'OFF — ratings inherited'}) -> {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
