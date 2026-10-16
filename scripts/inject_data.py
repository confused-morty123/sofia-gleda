#!/usr/bin/env python3
"""Inline the poster maps into index.html between the SOFIA-POSTERS markers.

  TMDBART: id -> {p:poster_path, b:backdrop_path, en:"English title"}  (from tmdb_films.json)
  SHOWART: theatre show id -> full poster URL                          (from theatre_posters.json)
  POSTERS: id -> full image URL (film_links_posters.json + film_info img fallback)
  SYN_EN:  id -> English synopsis (from translations.json cache, for BG-only items)
  TITLE_EN: id -> English title   (from translations.json cache, for BG-only items)
  EVENTS_AUTO: festivals / limited runs programata.bg announces    (from events.json)
  EVENT_MEMBERS: event id -> [film/show ids] the event's page lists (from events.json)
  EVENTART: event id -> "posters/ev-<id>.jpg", the event's re-hosted key art (from event_posters.json)

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
from csp import json_for_script, stamp as stamp_csp  # noqa: E402
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
CACHE_FILE = ROOT / "translations.json"

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
    if v.get("rt") is not None:  rec["rt"] = v["rt"]    # Rotten Tomatoes critics % (OMDb)
    if v.get("mc") is not None:  rec["mc"] = v["mc"]    # Metacritic score /100 (OMDb)
    if v.get("tmdb_vote") is not None: rec["tv"] = v["tmdb_vote"]  # TMDB vote_average
    if v.get("vote_count") is not None: rec["tc"] = v["vote_count"]  # TMDB vote count
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

# Auto-translated English synopses and titles (translate.py, DeepL). Built here
# from the translation cache (translations.json) with translate.build_mappings,
# the same function translate.py uses, so no intermediate file can go stale and
# empty them (a keyless local run once left empty sidecars that overrode the
# cache). Always emitted (possibly empty): the UI declares nothing else for them.
_syn_en: dict = {}
_title_en: dict = {}
if CACHE_FILE.exists():
    try:
        from translate import build_mappings as _build_mappings
        _cache = json.load(open(CACHE_FILE, encoding="utf-8"))
        _src = HTML.read_text(encoding="utf-8")
        _films_m = re.search(r"const FILMS=(\[.*?\]);", _src, flags=re.S)
        _shows_m = re.search(r"const SHOWS=(\[.*?\]);", _src, flags=re.S)
        _films = json.loads(_films_m.group(1)) if _films_m else []
        _shows = json.loads(_shows_m.group(1)) if _shows_m else []
        _syn_en, _title_en = _build_mappings(_films, _shows, tmdb, filminfo, _cache)
    except Exception as e:
        print(f"  (could not build translation maps from {CACHE_FILE.name}: {e})")

# DeepL can leave a name or a quoted Bulgarian title in Cyrillic inside its
# English; the publish gate rejects any Cyrillic in SYN_EN/TITLE_EN, so every
# value is served romanised (translate.latinise), whichever branch built the maps.
try:
    from translate import latinise as _latinise
    _syn_en = {k: _latinise(v) for k, v in _syn_en.items() if v}
    _title_en = {k: _latinise(v) for k, v in _title_en.items() if v}
except ImportError:
    pass

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

# og:images harvested from the films' own pages (film_links_posters.json) pass the
# same poster policy the publish gate applies. A ticketing backend's image
# (softwareforcinema.com) or an opaque CDN file is dropped here and logged, and the
# film may still take its film_info img below. Before, one such image failed the
# gate and blocked the whole refresh (2026-10-09: 27 Синелибри og:images on GitHub).
for _fid, _url in list(posters_override.items()):
    _why = reject_reason(_fid, _url, _cat)
    if _why:
        del posters_override[_fid]
        _fi_img_log.append(f"  og:image dropped {_fid!r}: {_why}")
        _img = (filminfo.get(_fid) or {}).get("img")
        if _img and _fid not in _tmdb_with_poster:
            _fi_img_pending[_fid] = _img

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

# A poster keyed by an id the catalogue no longer has — a duplicate show merged
# into another record, a superseded minted film (scrape_programs.py retires
# both) — points at nothing, and the build gate refuses it. The sidecar files
# keep their entries; only the inlined maps drop them, loudly.
if _cat is not None:
    for _name, _map in (("SHOWART", showart), ("POSTERS", posters_override),
                        ("TMDBART", art), ("FILMINFO", filminfo)):
        for _pid in sorted(k for k in _map if not _cat.knows(k)):
            print(f"  {_name}: dropped {_pid!r} — no film, show or event has this id")
            del _map[_pid]

# Festivals and limited runs that programata.bg announces (fetch_events.py ->
# events.json). Shaped for THIS page here, not in the fetch step: ended events
# go, the hand-curated seed wins any duplicate, and the membership map is
# computed against the listings this build actually has. Always emitted
# (possibly empty), like SYN_EN: the UI declares nothing else for them.
EVENTS_JSON = ROOT / "events.json"
_events_auto: list = []
_event_members: dict = {}
if EVENTS_JSON.exists():
    try:
        import fetch_events as _FE
        _ev_doc = json.load(open(EVENTS_JSON, encoding="utf-8"))
        _ev_cat = _FE.catalogue(HTML.read_text(encoding="utf-8"))
        _events_auto, _event_members, _ev_rep = _FE.for_page(_ev_doc, _ev_cat, _FE.sofia_today())
        for _a, _s in _ev_rep["seed"]:
            print(f"  EVENTS_AUTO: {_a} left out, the seed's {_s} lists it")
        _known = {f.get("id") for f in _ev_cat["FILMS"]} | {s.get("id") for s in _ev_cat["SHOWS"]}
        _event_members = {k: [i for i in v if i in _known] for k, v in _event_members.items()}
        _event_members = {k: v for k, v in _event_members.items() if v}
    except Exception as e:
        print(f"  (could not read {EVENTS_JSON.name}: {e}; no auto events this build)")
        _events_auto, _event_members = [], {}

# Key art for the events this page shows (fetch_event_posters.py ->
# event_posters.json, files under posters/ev-*). Only records that pass the same
# rule the gate applies (fetch_event_posters.record_problem: the file exists, the
# image came from programata.bg, the seed festival's own site, or the organiser
# site the event's programata post links). Always emitted (possibly empty).
EVENT_POSTERS_JSON = ROOT / "event_posters.json"
_eventart: dict = {}
if EVENT_POSTERS_JSON.exists():
    try:
        import fetch_events as _FE2
        import fetch_event_posters as _EP
        _ep_doc = json.load(open(EVENT_POSTERS_JSON, encoding="utf-8"))
        _ep_ids = ({e.get("id") for e in _FE2.catalogue(HTML.read_text(encoding="utf-8"))["EVENTS"]}
                   | {e.get("id") for e in _events_auto})
        _eventart = _EP.eventart(_ep_doc, _ep_ids, ROOT)
        for _eid, _rec in sorted((_ep_doc.get("events") or {}).items()):
            _why = _EP.record_problem(_eid, _rec, ROOT)
            if _eid in _ep_ids and _why:
                print(f"  EVENTART: {_eid!r} left out: {_why}")
    except Exception as e:
        print(f"  (could not read {EVENT_POSTERS_JSON.name}: {e}; no event art this build)")
        _eventart = {}

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
    '   SYN_EN:  id -> English synopsis auto-translated from Bulgarian by translate.py\n'
    '            (Wave L2). Only present for ids with no English synopsis from any\n'
    '            other source. Always emitted (possibly empty).\n'
    '   TITLE_EN: id -> English title auto-translated from Bulgarian. Only present for\n'
    '            ids with no English title from any other source. Always emitted.\n'
    '   EVENTS_AUTO: festivals and limited runs programata.bg announces, in the EVENTS\n'
    '            shape plus url (the source page) and auto:true; the UI adds them to\n'
    '            EVENTS (the seed wins an id). Written by fetch_events.py. Always emitted.\n'
    '   EVENT_MEMBERS: event id -> [film/show ids] whose exact titles the event\'s page\n'
    '            lists with their venue and dates. Always emitted.\n'
    '   EVENTART: event id -> "posters/ev-<id>.jpg", key art fetch_event_posters.py\n'
    '            re-hosted; realPoster() reads it before SHOWART. Always emitted.\n'
    '   Missing ids keep the generated SVG artwork. */')

block = ("<script>/* SOFIA-POSTERS-START */\n"
    + header + "\n"
    # Every value goes through csp.json_for_script: these strings come from other
    # sites (TMDB's community-edited overviews, the venues' pages, programata.bg's
    # headlines, DeepL), and a "</script>" in one of them must not end this script.
    + "const POSTERS = " + json_for_script(posters_override) + ";\n"
    + "const TMDBART = " + json_for_script(art) + ";\n"
    + "const SHOWART = " + json_for_script(showart) + ";\n"
    + "const SHOWALIAS = " + json_for_script(alias) + ";\n"
    + "const FILMINFO = " + json_for_script(filminfo) + ";\n"
    + "const SYN_EN = " + json_for_script(_syn_en) + ";\n"
    + "const TITLE_EN = " + json_for_script(_title_en) + ";\n"
    + "const EVENTS_AUTO = " + json_for_script(_events_auto) + ";\n"
    + "const EVENT_MEMBERS = " + json_for_script(_event_members) + ";\n"
    + "const EVENTART = " + json_for_script(_eventart) + ";\n"
    + "/* SOFIA-POSTERS-END */</script>")

data = HTML.read_text(encoding="utf-8")

MARKERS = r"<script>/\* SOFIA-POSTERS-START \*/.*?/\* SOFIA-POSTERS-END \*/</script>"
found = re.search(MARKERS, data, flags=re.S)
assert found, "marker block not found in " + str(HTML)

# This block is REGENERATED WHOLESALE, so anything hand-written inside it is
# destroyed on every run. That is exactly how `const PRICES` was lost in the
# 2026-09-16 refresh and every film became unclickable. Refuse to run rather
# than silently delete app code again.
GENERATED = {"POSTERS", "TMDBART", "SHOWART", "SHOWALIAS", "FILMINFO", "SYN_EN", "TITLE_EN",
             "EVENTS_AUTO", "EVENT_MEMBERS", "EVENTART"}
declared = set(re.findall(r"^const\s+([A-Za-z_$][\w$]*)\s*=", found.group(0), flags=re.M))
stray = declared - GENERATED
if stray:
    raise SystemExit(
        "refusing to overwrite the poster block: it also declares "
        + ", ".join(sorted(stray))
        + ".\nThose are not generated here and would be erased. Move them into "
          "their own <script> outside the SOFIA-POSTERS markers, then re-run.")

new = re.sub(MARKERS, lambda m: block, data, count=1, flags=re.S)
# The data script changed, so its hash did: re-stamp the page's CSP meta here too
# (refresh_all.py stamps once more after the last step). csp.stamp refuses a page
# whose scripts are not the known set, e.g. a data value that broke out of its script.
new = stamp_csp(new)
HTML.write_text(new, encoding="utf-8")
_n_fi_img_in_posters = sum(1 for fid in _fi_img_pending if fid in posters_override)
print(f"injected TMDBART: {len(art)} films ({sum(1 for r in art.values() if 'p' in r)} with posters), "
      f"POSTERS: {len(posters_override)} entries "
      f"({len(posters_override) - _n_fi_img_in_posters} og:image + {_n_fi_img_in_posters} film_info-img fallbacks), "
      f"SHOWART: {len(showart)} shows, SHOWALIAS: {len(alias)} mirrored events"
      + (" (" + ", ".join(f"{k}->{v}" for k, v in alias.items()) + ")" if alias else ""))
print(f"  SYN_EN: {len(_syn_en)} ids, TITLE_EN: {len(_title_en)} ids (auto-translated)")
print(f"  EVENTS_AUTO: {len(_events_auto)} event(s), EVENT_MEMBERS: "
      f"{sum(len(v) for v in _event_members.values())} title(s) in {len(_event_members)} event(s), "
      f"EVENTART: {len(_eventart)} event(s) with key art"
      + (" (" + ", ".join(sorted(_eventart)) + ")" if _eventart else ""))
