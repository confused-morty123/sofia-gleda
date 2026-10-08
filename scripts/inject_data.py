#!/usr/bin/env python3
"""Inline the poster maps into index.html between the SOFIA-POSTERS markers.

  TMDBART: id -> {p:poster_path, b:backdrop_path, en:"English title"}  (from tmdb_films.json)
  SHOWART: theatre show id -> full poster URL                          (from theatre_posters.json)
  POSTERS: id -> full image URL (film_links_posters.json + film_info img fallback)

Priority (highest first):
  1. TMDB poster (TMDBART[id].p)
  2. film_links_posters.json entry (og:image harvested by fetch_tmdb.py)
  3. film_info.json `img` — lowest priority; only for films with no TMDB poster
     and no film_links_posters entry; must pass posterpolicy.

Idempotent: regenerates the whole block each run. Missing ids keep the app's
generated SVG artwork.

    python3 scripts/inject_data.py            # edits index.html in place
"""
import json, os, re, sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
try:
    from posterpolicy import Catalogue, reject_reason
except ImportError:
    Catalogue = None
    def reject_reason(pid, url, catalogue=None): return None  # type: ignore

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
TMDB_JSON = ROOT / "tmdb_films.json"
SHOW_JSON = ROOT / "theatre_posters.json"
LINKS_JSON = ROOT / "film_links_posters.json"
FILM_INFO_JSON = ROOT / "film_info.json"

tmdb = json.load(open(TMDB_JSON, encoding="utf-8")) if TMDB_JSON.exists() else {}
shows = json.load(open(SHOW_JSON, encoding="utf-8")) if SHOW_JSON.exists() else {}
film_links = json.load(open(LINKS_JSON, encoding="utf-8")) if LINKS_JSON.exists() else {}

art = {}
for fid, v in tmdb.items():
    if not v.get("matched"):
        continue
    rec = {}
    if v.get("poster_path"):   rec["p"] = v["poster_path"]
    if v.get("backdrop_path"): rec["b"] = v["backdrop_path"]
    if v.get("en"):            rec["en"] = v["en"]
    if v.get("ov"):            rec["ov"] = v["ov"]        # TMDB English synopsis
    if v.get("country"):       rec["country"] = v["country"]  # TMDB English country
    if v.get("imdb") is not None: rec["imdb"] = v["imdb"]  # genuine IMDb rating (OMDb)
    if v.get("imdbVotes"):     rec["imdbVotes"] = v["imdbVotes"]  # IMDb vote count (OMDb)
    if v.get("dir"):           rec["dir"] = v["dir"]      # director(s) from TMDB credits
    if v.get("cast"):          rec["cast"] = v["cast"]    # top-5 cast from TMDB credits
    if v.get("ovBg"):          rec["ovBg"] = v["ovBg"]    # Bulgarian overview from TMDB
    if rec:
        art[fid] = rec

showart = {k: v for k, v in shows.items() if v}

# Film details harvested from each film's own programme page (synopsis, director,
# cast) for films the seed data and TMDB leave empty. Written by fetch_film_info.py;
# always emitted (possibly empty) because the app declares nothing else for it.
filminfo = {}
if FILM_INFO_JSON.exists():
    try:
        filminfo = {k: v for k, v in json.load(open(FILM_INFO_JSON, encoding="utf-8")).items() if v}
    except Exception as e:
        print(f"  (could not read {FILM_INFO_JSON.name}: {e})")

# Films TMDB cannot match keep their own programme-page image (og:image), harvested
# into film_links_posters.json. These are full URLs and go into POSTERS, which
# realPoster() checks first — but fetch_tmdb only writes an entry here for a film
# WITHOUT a TMDB poster, so this stays a pure fallback and never shadows TMDB art.
posters_override = {k: v for k, v in film_links.items() if v}

# Lowest-priority fallback: film_info.json `img` field (harvested by fetch_film_info.py
# from the NDK/programata/vlaikova event page). Only used when:
#   - the film has no TMDB poster (TMDBART[id].p absent)
#   - the film has no film_links_posters entry
# posterpolicy is applied to each candidate to refuse opaque/untrusted images.
_tmdb_with_poster = {fid for fid, v in tmdb.items() if v.get("poster_path")}
_fi_img_added = 0
_fi_img_log = []
# Defer catalogue construction (it reads index.html which hasn't been written yet
# for this very run — but we only need to CHECK `knows`, which is stable).
# We build the catalogue once below, after alias computation.
_fi_img_pending = {}  # fid -> img_url, to be policy-checked after catalogue is ready
for _fid, _fi in filminfo.items():
    _img = _fi.get("img")
    if not _img:
        continue
    if _fid in _tmdb_with_poster:
        continue   # TMDB poster exists — no need for fallback
    if _fid in posters_override:
        continue   # film_links_posters already covers this film
    _fi_img_pending[_fid] = _img

# A cinema-scope event that is simply a screening of a film we already list must
# borrow that film's verified TMDB poster - harvesting a second image for the same
# title is what put the wrong artwork on the Oasis screening. Computed here rather
# than hand-maintained, so a new limited screening is covered the day it appears.
alias = {}
_cat = None
if Catalogue is not None:
    try:
        _cat = Catalogue.from_html(HTML)
        for _eid in _cat.event_by_id:
            _film = _cat.mirrors_film(_eid)
            if _film:
                alias[_eid] = _film
    except Exception as e:
        print(f"  (could not compute SHOWALIAS: {e})")

# Apply film_info img fallback: check posterpolicy and merge into POSTERS.
# Now that _cat is available we can do the full policy check (including knows()).
for _fid, _img in _fi_img_pending.items():
    _why = reject_reason(_fid, _img, _cat)
    if _why:
        _fi_img_log.append(f"  fi-img dropped {_fid!r}: {_why}")
    else:
        posters_override[_fid] = _img
        _fi_img_added += 1
if _fi_img_log:
    for _l in _fi_img_log:
        print(_l)
if _fi_img_added:
    print(f"  film_info img: added {_fi_img_added} fallback poster(s) to POSTERS")

header = ('/* Sofia Gleda — real poster artwork.\n'
    '   POSTERS: id -> data: URI override (base64, any web format). Highest priority.\n'
    '   TMDBART: id -> {p:poster_path, b:backdrop_path, en:"English title"} from TMDB;\n'
    '            paths resolve to https://image.tmdb.org/t/p/<size><path> at render time,\n'
    '            so posters load on any hosted/live web-app or mobile webview.\n'
    '   SHOWART: theatre show id -> full poster URL (best-effort, theatre.art.bg).\n'
    '   SHOWALIAS: event id -> film id, for cinema events that are screenings of a\n'
    '            catalogued film. realPoster() should resolve through this FIRST, so\n'
    '            such an event shows the film\'s verified TMDB poster and can never\n'
    '            pick up a separately harvested, unverifiable image.\n'
    '   FILMINFO: film id -> {syn, dir, cast, src} from the film\'s own programme page,\n'
    '            for films the seed data and TMDB leave without details.\n'
    '   Missing ids keep the generated SVG artwork. */')

block = ("<script>/* SOFIA-POSTERS-START */\n"
    + header + "\n"
    + "const POSTERS = " + json.dumps(posters_override, ensure_ascii=False, separators=(",", ":")) + ";\n"
    + "const TMDBART = " + json.dumps(art, ensure_ascii=False, separators=(",", ":")) + ";\n"
    + "const SHOWART = " + json.dumps(showart, ensure_ascii=False, separators=(",", ":")) + ";\n"
    + "const SHOWALIAS = " + json.dumps(alias, ensure_ascii=False, separators=(",", ":")) + ";\n"
    + "const FILMINFO = " + json.dumps(filminfo, ensure_ascii=False, separators=(",", ":")) + ";\n"
    + "/* SOFIA-POSTERS-END */</script>")

data = HTML.read_text(encoding="utf-8")

MARKERS = r"<script>/\* SOFIA-POSTERS-START \*/.*?/\* SOFIA-POSTERS-END \*/</script>"
found = re.search(MARKERS, data, flags=re.S)
assert found, "marker block not found in " + str(HTML)

# This block is REGENERATED WHOLESALE, so anything hand-written inside it is
# destroyed on every run. That is exactly how `const PRICES` was lost in the
# 2026-09-16 refresh and every film became unclickable. Refuse to run rather
# than silently delete app code again.
GENERATED = {"POSTERS", "TMDBART", "SHOWART", "SHOWALIAS", "FILMINFO"}
declared = set(re.findall(r"^const\s+([A-Za-z_$][\w$]*)\s*=", found.group(0), flags=re.M))
stray = declared - GENERATED
if stray:
    raise SystemExit(
        "refusing to overwrite the poster block: it also declares "
        + ", ".join(sorted(stray))
        + ".\nThose are not generated here and would be erased. Move them into "
          "their own <script> outside the SOFIA-POSTERS markers, then re-run.")

new = re.sub(MARKERS, lambda m: block, data, count=1, flags=re.S)
HTML.write_text(new, encoding="utf-8")
_n_fi_img_in_posters = sum(1 for fid in _fi_img_pending if fid in posters_override)
print(f"injected TMDBART: {len(art)} films ({sum(1 for r in art.values() if 'p' in r)} with posters), "
      f"POSTERS: {len(posters_override)} entries "
      f"({len(posters_override) - _n_fi_img_in_posters} og:image + {_n_fi_img_in_posters} film_info-img fallbacks), "
      f"SHOWART: {len(showart)} shows, SHOWALIAS: {len(alias)} mirrored events"
      + (" (" + ", ".join(f"{k}->{v}" for k, v in alias.items()) + ")" if alias else ""))
