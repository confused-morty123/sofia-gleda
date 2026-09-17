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
TOKEN = os.environ.get("TMDB_TOKEN", "").strip()

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


def main():
    if not TOKEN:
        print("TMDB_TOKEN not set — skipping film posters, keeping previous.", file=sys.stderr)
        return 0
    data = HTML.read_text(encoding="utf-8").split("/* SOFIA-DATA-START */")[1].split("/* SOFIA-DATA-END */")[0]
    films = grab(data, "FILMS")
    out = {}
    for i, f in enumerate(films):
        en, year = f.get("en"), f.get("year")
        if f["id"] in BLOCKLIST:
            print(f"[{i+1}/{len(films)}] {f['id']:24s} BLOCKED (known false match — generated art kept)")
            out[f["id"]] = {"matched": False, "en": en}
            continue
        res = api("search/movie", {"query": en, "year": year, "include_adult": "false"})
        results = (res or {}).get("results", [])
        if not results:
            res = api("search/movie", {"query": en, "include_adult": "false"})
            results = (res or {}).get("results", [])
        best = pick(results, en, year)
        if not best:
            print(f"[{i+1}/{len(films)}] {f['id']:24s} NO MATCH ({en} {year})")
            out[f["id"]] = {"matched": False, "en": en}
            continue
        rec = {
            "matched": True,
            "tmdb_id": best["id"],
            "poster_path": best.get("poster_path"),
            "backdrop_path": best.get("backdrop_path"),
            "en": best.get("title") or en,
            "original_title": best.get("original_title"),
            "orig_lang": best.get("original_language"),
            "tmdb_year": (best.get("release_date") or "")[:4],
            "tmdb_vote": best.get("vote_average"),
        }
        out[f["id"]] = rec
        flag = "" if rec["poster_path"] else "  (no poster!)"
        print(f"[{i+1}/{len(films)}] {f['id']:24s} -> {rec['en']} ({rec['tmdb_year']}) id={rec['tmdb_id']}{flag}")
        time.sleep(0.12)

    posters = sum(1 for v in out.values() if v.get("poster_path"))
    # keep-previous guard: never overwrite a good cache with an empty run
    if posters == 0 and OUT.exists():
        print("0 posters this run — keeping previous tmdb_films.json", file=sys.stderr)
        return 0
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    matched = sum(1 for v in out.values() if v.get("matched"))
    print(f"\nDONE: {matched}/{len(films)} matched, {posters} with posters -> {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
