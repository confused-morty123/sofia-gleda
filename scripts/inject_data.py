#!/usr/bin/env python3
"""Inline the poster maps into index.html between the SOFIA-POSTERS markers.

  TMDBART: id -> {p:poster_path, b:backdrop_path, en:"English title", …, v:{k,l,t} trailer}
           (from tmdb_films.json via fetch_tmdb.art_record)
  SHOWART: theatre show id -> full poster URL                          (from theatre_posters.json)
  POSTERS: id -> full image URL (film_links_posters.json + film_info img fallback)
  SYN_EN:  id -> English synopsis (from translations.json cache, for BG-only items)
  TITLE_EN: id -> English title   (from translations.json cache, for BG-only items)
  EVENTS_AUTO: festivals / limited runs programata.bg announces    (from events.json)
  EVENT_MEMBERS: event id -> [film/show ids] the event's page lists (from events.json)
  EVENTART: event id -> "posters/ev-<id>.jpg", the event's re-hosted key art (from event_posters.json)

Priority (highest first):
  1. TMDB poster (TMDBART[id].p)
  2. of the film's og:image (film_links_posters.json), own-page image
     (poster_sources.json) and film_info.json `img`, in that order, the first
     the gate counts as the film's own image (see posterpolicy.true_poster);
     else the first that passes posterpolicy.

Idempotent: regenerates the whole block each run. Missing ids keep the app's
generated SVG artwork.

    python3 scripts/inject_data.py            # edits index.html in place
"""
import json, os, re, sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from csp import json_for_script, stamp as stamp_csp  # noqa: E402
try:
    from posterpolicy import Catalogue, reject_reason, film_image_is_own
except ImportError:
    Catalogue = None
    def reject_reason(pid, url, catalogue=None): return None  # type: ignore
    def film_image_is_own(pid, url, maps): return True  # type: ignore

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

# TMDBART: one entry per matched film (fetch_tmdb.art_record, so the pipeline tests
# exercise the same code). FILMS' own year feeds the trailer's year guard: a trailer
# goes in only when TMDB's year agrees with the film's within 1.
from fetch_tmdb import art_record  # noqa: E402
_films_year = {}
try:
    _fm = re.search(r"^const FILMS=(\[.*?\]);\s*$", HTML.read_text(encoding="utf-8"), flags=re.M | re.S)
    _films_year = {f.get("id"): f.get("year") for f in (json.loads(_fm.group(1)) if _fm else [])
                   if isinstance(f, dict)}
except (OSError, ValueError) as e:
    print(f"  (could not read FILMS years: {e}; no trailers this build)")
art = {}
_tr_dropped = []
for fid, v in tmdb.items():
    rec = art_record(v, _films_year.get(fid))
    if v.get("trailer") and "v" not in rec and v.get("matched"):
        _tr_dropped.append(fid)
    if rec:
        art[fid] = rec
if _tr_dropped:
    print(f"  TMDBART: trailer left out for {len(_tr_dropped)} film(s) whose TMDB year disagrees "
          f"with theirs: {', '.join(sorted(_tr_dropped)[:8])}")

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

# Films without a TMDB poster take one image from up to three candidates, in this
# order: their programme-page og:image (film_links_posters.json, fetch_tmdb), the
# image own_page_posters.py read from the page the listing links
# (poster_sources.json, re-hosted under posters/, used only while the file is
# unchanged), and the film_info.json `img` (fetch_film_info / harvest_cinema_posters:
# the cinema's own poster). Every candidate must pass posterpolicy.reject_reason.
# Of those that pass, the first the publish gate counts as the film's OWN image
# (posterpolicy.true_poster: a re-hosted own-page file, or any host but a listings
# aggregator the film does not link) wins; only when none does, the first that
# passes. Until 2026-11-27 the og:image always won: a programata.bg still shadowed
# Кино Арена's and Cinema City's posters and the own-page posters of eight films,
# and film coverage fell to 89.4 %, under the gate's 90 % floor.
# fetch_tmdb only writes film_links_posters.json for a film WITHOUT a TMDB poster,
# so none of this shadows TMDB art.
_tmdb_with_poster = {fid for fid, v in tmdb.items() if v.get("poster_path")}
posters_override = {}
_fi_img_added = 0
_fi_img_log = []
_fi_img_pending = {}  # fid -> film_info img, for the summary line below

# Images read from each title's OWN page by own_page_posters.py, with their
# provenance in poster_sources.json. A play's own-page image replaces whatever
# theatre_posters.json holds for it (an aggregator match, a hand-added file). Only
# files that still exist, unchanged.
try:
    import hashlib as _hl
    _own = json.load(open(ROOT / "poster_sources.json", encoding="utf-8"))
except (OSError, ValueError):
    _own = {}
_own_n = {"shows": 0, "films": 0}
for _sid, _rec in (_own.get("shows") or {}).items():
    _p = (_rec or {}).get("path") or ""
    if _p and (ROOT / _p).is_file() and _hl.md5((ROOT / _p).read_bytes()).hexdigest() == _rec.get("md5"):
        if showart.get(_sid) != _p:
            showart[_sid] = _p
            _own_n["shows"] += 1
_own_film = {}
for _fid, _rec in (_own.get("films") or {}).items():
    _p = (_rec or {}).get("path") or ""
    if (_p and _fid not in _tmdb_with_poster and (ROOT / _p).is_file()
            and _hl.md5((ROOT / _p).read_bytes()).hexdigest() == _rec.get("md5")):
        _own_film[_fid] = _p

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

# The film+venue links scrape_programs.py wrote (VLINKS), for film_image_is_own:
# an aggregator's image is the film's own only when the film links that site.
try:
    _vm = re.search(r"^const VLINKS=(\[.*?\]);\s*$", HTML.read_text(encoding="utf-8"), flags=re.M | re.S)
    _own_maps = {"VLINKS": json.loads(_vm.group(1)) if _vm else []}
except (OSError, ValueError):
    _own_maps = {"VLINKS": []}

for _fid in sorted(set(film_links) | set(_own_film) | set(filminfo)):
    if _fid in _tmdb_with_poster:
        continue   # TMDB poster exists — no need for fallback
    _cands = [(k, u) for k, u in (("og:image", film_links.get(_fid)), ("own page", _own_film.get(_fid)),
                                  ("fi-img", (filminfo.get(_fid) or {}).get("img"))) if u]
    if not _cands:
        continue
    _ok = []
    for _kind, _url in _cands:
        _why = reject_reason(_fid, _url, _cat)
        if _why:
            _fi_img_log.append(f"  {_kind} dropped {_fid!r}: {_why}")
        else:
            _ok.append((_kind, _url))
    if not _ok:
        continue
    _pick = next(((k, u) for k, u in _ok
                  if k == "own page" or film_image_is_own(_fid, u, _own_maps)), _ok[0])
    if _pick != _ok[0]:
        _fi_img_log.append(f"  {_fid!r}: {_pick[0]} ({_pick[1]}) ahead of the aggregator's {_ok[0][0]} "
                           f"({_ok[0][1]})")
    posters_override[_fid] = _pick[1]
    if _pick[0] == "own page":
        _own_n["films"] += 1
    elif _pick[0] == "fi-img":
        _fi_img_pending[_fid] = _pick[1]
        _fi_img_added += 1
if any(_own_n.values()):
    print(f"  own-page posters (poster_sources.json): {_own_n['shows']} play(s) replaced, "
          f"{_own_n['films']} film(s) added to POSTERS")
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

# Renamed show ids (scripts/show_aliases.json "renamed": old -> new). The app maps
# an old id from a link (?show=, #show=) or from saved preferences through this
# before it looks the show up, so a URL shared before the rename still opens the
# play. Always emitted (possibly empty).
try:
    from show_identity import renamed_ids as _renamed_ids
    _id_redirect = _renamed_ids()
except Exception as e:
    print(f"  (could not read the renamed ids: {e}; ID_REDIRECT is empty)")
    _id_redirect = {}
# Film ids merged into the film they duplicated (film_merges.json, written by
# scrape_programs.py): the app opens the kept film for an old ?film= / #film= link
# or a saved pick. Always emitted (possibly empty).
try:
    from film_identity import load_merges as _load_merges, film_redirects as _film_redirects
    _film_redirect = _film_redirects(_load_merges())
except Exception as e:
    print(f"  (could not read the merged film ids: {e}; FILM_REDIRECT is empty)")
    _film_redirect = {}

header = ('/* Sofia Gleda — real poster artwork.\n'
    '   POSTERS: id -> data: URI override (base64, any web format). Highest priority.\n'
    '   TMDBART: id -> {p:poster_path, b:backdrop_path, en:"English title", tr:weekly trending rank} from TMDB;\n'
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
    '   ID_REDIRECT: old show id -> new show id, for ids that were renamed\n'
    '            (show_aliases.json "renamed"). Always emitted.\n'
    '   FILM_REDIRECT: merged-away film id -> the film it was merged into\n'
    '            (film_merges.json). Always emitted.\n'
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
    + "const ID_REDIRECT = " + json_for_script(_id_redirect) + ";\n"
    + "const FILM_REDIRECT = " + json_for_script(_film_redirect) + ";\n"
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
             "EVENTS_AUTO", "EVENT_MEMBERS", "EVENTART", "ID_REDIRECT", "FILM_REDIRECT"}
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
