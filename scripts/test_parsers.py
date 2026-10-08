#!/usr/bin/env python3
"""Offline tests for the fragile parts of the pipeline. No network, ~1 second.

These exist because both of the bugs they cover were invisible for weeks while
the build reported success:

  * programata.bg writes day headings as "17 септември |четвъртък|: 14:20 | 19:30".
    The numeric-only date regex matched none of them, so every showtime collapsed
    onto one day and every cinema was filed "unreachable".
  * a hand-seeded poster from a ticketing CDN put another film's artwork on the
    Oasis screening, and won every merge because SEED was exempt from checking.

    python3 scripts/test_parsers.py

Exit 0 means the parsers still understand the shapes these sites actually use,
and the poster policy still refuses the shapes it must.
"""
import sys, pathlib, datetime as dt

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bs4 import BeautifulSoup                                  # noqa: E402
import scrape_programs as S                                    # noqa: E402
import posterpolicy as PP                                      # noqa: E402
import fetch_film_info as FI                                   # noqa: E402
import fetch_tmdb as TMDB                                      # noqa: E402

fails = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}: got {got!r}, wanted {want!r}")
        fails.append(name)


def window(start, days):
    d = dt.date.fromisoformat(start)
    return [(d + dt.timedelta(days=i)).isoformat() for i in range(days)]


W = window("2026-09-15", 91)

# ------------------------------------------------------------ date headings
print("dates, in every shape these sites use")
for text, want in [
    ("17 септември |четвъртък|: 14:20 | 19:30", "2026-09-17"),   # programata
    ("1 октомври",                              "2026-10-01"),
    ("15.09",                                   "2026-09-15"),
    ("15.09.2026",                              "2026-09-15"),
    ("2026-09-20",                              "2026-09-20"),
    ("Приложна магия 2",                        None),          # a title is not a date
    ("зала 3",                                  None),
]:
    check(f"{text[:34]!r}", S.find_date(text, W), want)

print("the year comes from the window, not the clock")
W2 = window("2026-12-20", 60)
for text, want in [("28 декември", "2026-12-28"),
                   ("5 януари",    "2027-01-05"),
                   ("3 февруари",  "2027-02-03")]:
    check(f"{text!r} across new year", S.find_date(text, W2), want)

# ------------------------------------------- programata's per-film schedule
print("programata cinema page")
CINEMA = """<div class="films">
  <div class="film">
    <a href="/kino/filmi/prilozhna-magiya-2/">Приложна магия 2</a>
    <div class="sched">15 септември |вторник|: 11:00 | 13:45
                      16 септември |сряда|: 16:30</div>
  </div>
  <div class="film">
    <a href="/kino/filmi/koyota/">Койота срещу Акме</a>
    <div class="sched">17 септември |четвъртък|: 13:50 | 17:00</div>
  </div>
</div>"""


class _Fixed:
    def __init__(self, html): self.html = html
    def soup(self, url, attempts=None): return BeautifulSoup(self.html, "lxml")


rows = S.scrape_cinema("cc-sofia", "https://programata.bg/kino/kino-saloni/x/",
                       _Fixed(CINEMA), W)
got = {(t, d, tuple(x)) for t, _, d, x, _ in rows}
check("first film, first day", ("Приложна магия 2", "2026-09-15", ("11:00", "13:45")) in got, True)
check("same film, second day", ("Приложна магия 2", "2026-09-16", ("16:30",)) in got, True)
check("second film keeps its own dates",
      ("Койота срещу Акме", "2026-09-17", ("13:50", "17:00")) in got, True)
check("dates are not all collapsed onto day one", len({d for _, d, _ in got}), 3)
check("the film's own page is captured as the deep link",
      all(r[4] and "/kino/filmi/" in r[4] for r in rows), True)

# -------------------------------------------------------- theatre venue page
print("theatre venue page")
THEATRE = """<ul>
<li>18 септември, 19:00 — Уроци</li>
<li>20.09 19:30 Медея</li>
<li>Скъперникът — 22 септември 19:00</li>
<li>няма дата 19:00 Нещо</li>
</ul>"""
out = S.scrape_theatre_page("http://fixture", _Fixed(THEATRE), W)
check("bulgarian month row", ("Уроци", "2026-09-18", "19:00") in out, True)
check("numeric row", ("Медея", "2026-09-20", "19:30") in out, True)
check("date written after the title", ("Скъперникът", "2026-09-22", "19:00") in out, True)
check("undated row skipped", len(out), 3)

# ------------------------------------------------------------ poster policy
print("poster policy")
cat = PP.Catalogue.from_html(pathlib.Path(__file__).resolve().parent.parent / "index.html")
BAD = "https://softwareforcinema.com/f/movies/q/7/7ba25f6fad48c2ba6390f9e5800fca33.jpeg"
check("the original bad URL is refused", bool(PP.reject_reason("uroci", BAD, cat)), True)
check("an opaque hash on an unknown host is refused",
      bool(PP.reject_reason("uroci", "https://cdn.example/a/9f2b7c1d4e6a8b0c2d4e6f80.jpg", cat)), True)
check("a readable filename on an unknown host is fine",
      PP.reject_reason("uroci", "https://cdn.example/a/uroci-afish.jpg", cat), None)
check("a venue's own opaque id is fine",
      PP.reject_reason("uroci", "https://nationaltheatre.bg/storage/shows/26866.jpg", cat), None)
check("http is refused", bool(PP.reject_reason("uroci", "http://x.bg/a.jpg", cat)), True)
check("an id nothing in the catalogue owns is refused",
      bool(PP.reject_reason("no-such-show", "https://nationaltheatre.bg/a.jpg", cat)), True)
check("a screening of a catalogued film is refused a harvested poster",
      bool(PP.reject_reason("oasis-screening", "https://theatre.art.bg/img/photos/BIGx.jpg", cat)), True)
check("that screening resolves to the film instead",
      cat.mirrors_film("oasis-screening"), "oasis")
check("a festival keeps its own artwork", cat.mirrors_film("cinelibri"), None)

# -------------------------------------------------------- programata film page
print("programata film page parser")
PROG_FIXTURE = (FIXTURES / "programata_film.html").read_text(encoding="utf-8")
prog_soup = BeautifulSoup(PROG_FIXTURE, "lxml")
prog_rec = FI.parse_programata(prog_soup, "https://programata.bg/kino/filmi/test-film/")
check("programata synopsis extracted",
      len(prog_rec.get("synBg", "")) >= 80, True)
check("programata director extracted",
      bool(prog_rec.get("dir")), True)
check("programata cast extracted",
      bool(prog_rec.get("cast")), True)
check("programata genres extracted",
      isinstance(prog_rec.get("genres"), list) and len(prog_rec.get("genres", [])) > 0, True)
check("programata genres use vocabulary",
      all(g in FI.GENRE_VOCAB or g == "Български" for g in prog_rec.get("genres", [])), True)
check("programata Bulgarian country -> Български genre",
      "Български" in (prog_rec.get("genres") or []), True)

# Bleed guard: cast must be ≤160 chars and must not start with synopsis text.
# This validates the fix for the "cast swallows synopsis" bug (issue #1).
print("programata cast-bleed guard")
# Simulate a page where cast value is followed immediately by synopsis text
# (the bug shape: parser extracts cast+synopsis together from old .film-director span).
_bleed_html = """
<html><body>
<div class="text-summary">
  <div class="text-summary-item movie-0"><span>Режисьор: </span>Дейвид Ейър</div>
  <div class="text-summary-item movie-1"><span>Участват: </span>Брад Пит, Дж. К. Симънс, Ана Ламбе</div>
  <div class="text-summary-item movie-2"><span>Жанр: </span>екшън, драма</div>
  <div class="text-summary-item movie-3"><span>Държава: </span>САЩ</div>
</div>
<div class="text mb-5 pb-5">
  <p>Сърцето на звяра проследява офицера от Специалните сили Джеймс Белмонт и четириногия му боен другар Один, след тежка самолетна катастрофа.</p>
</div>
</body></html>
"""
_bleed_soup = BeautifulSoup(_bleed_html, "lxml")
_bleed_rec = FI.parse_programata(_bleed_soup, "https://programata.bg/kino/filmi/bleed-test/")
check("bleed-test: cast does not contain synopsis text",
      "звяра" not in (_bleed_rec.get("cast") or ""), True)
check("bleed-test: cast length ≤ 160 chars",
      len(_bleed_rec.get("cast") or "") <= 160, True)
check("bleed-test: synopsis not in cast",
      "Сърцето" not in (_bleed_rec.get("cast") or ""), True)
check("bleed-test: synopsis extracted correctly",
      "Джеймс Белмонт" in (_bleed_rec.get("synBg") or ""), True)
check("bleed-test: director exactly 'Дейвид Ейър'",
      _bleed_rec.get("dir") == "Дейвид Ейър", True)
check("bleed-test: genres contain Екшън",
      "Екшън" in (_bleed_rec.get("genres") or []), True)

# ---------------------------------------------------------- vlaikova page
print("vlaikova screening page parser")
VL_FIXTURE = (FIXTURES / "vlaikova_screening.html").read_text(encoding="utf-8")
vl_soup = BeautifulSoup(VL_FIXTURE, "lxml")
vl_rec = FI.parse_vlaikova_page(vl_soup, "https://vlaikovacinema.com/screening/test/")
check("vlaikova synopsis extracted",
      len(vl_rec.get("synBg", "")) >= 80, True)
check("vlaikova director extracted",
      bool(vl_rec.get("dir")), True)
check("vlaikova cast extracted",
      bool(vl_rec.get("cast")), True)
check("vlaikova genres use vocabulary",
      all(g in FI.GENRE_VOCAB or g == "Български" for g in vl_rec.get("genres", [])), True)

# ------------------------------------------------------------ ndk page
print("ndk event page parser")
NDK_FIXTURE = (FIXTURES / "ndk_event.html").read_text(encoding="utf-8")
ndk_soup = BeautifulSoup(NDK_FIXTURE, "lxml")
ndk_rec = FI.parse_ndk_page(ndk_soup, "https://www.ndk.bg/en/events/test/")
check("ndk synopsis extracted as synEn",
      len(ndk_rec.get("synEn", "")) >= 80, True)
check("ndk director extracted",
      bool(ndk_rec.get("dir")), True)
check("ndk cast extracted",
      bool(ndk_rec.get("cast")), True)
check("ndk no synBg (English page)",
      "synBg" not in ndk_rec, True)

# --------------------------------------------------------- urbo/epaygo extraction
print("urbo/epaygo ticket-link extraction")
VLAIKOVA_HTML = str(vl_soup)  # vlaikova fixture has an embed.urboapp.com link
urbo_m = S._URBO_RE.search(VLAIKOVA_HTML)
check("urbo link found in vlaikova fixture",
      bool(urbo_m), True)
check("urbo link is embed.urboapp.com",
      urbo_m and "embed.urboapp.com" in urbo_m.group(0), True)

NDK_HTML = str(ndk_soup)
epaygo_m = S._EPAYGO_RE.search(NDK_HTML)
check("epaygo link found in ndk fixture",
      bool(epaygo_m), True)
check("epaygo link matches https://epaygo.bg/<digits>",
      epaygo_m and "epaygo.bg" in epaygo_m.group(0), True)

# ------------------------------------------------- VLINKS allowlist
print("VLINKS allowlist — programata links rejected")
check("programata.bg not in vlaikova allowlist",
      "programata.bg" not in S.VENUE_LINK_ALLOWLIST.get("vlaikova", []), True)
check("cinemacity.bg in cc-sofia allowlist",
      "cinemacity.bg" in S.VENUE_LINK_ALLOWLIST.get("cc-sofia", []), True)
check("embed.urboapp.com in vlaikova allowlist",
      "embed.urboapp.com" in S.VENUE_LINK_ALLOWLIST.get("vlaikova", []), True)
check("epaygo.bg in lumiere allowlist",
      "epaygo.bg" in S.VENUE_LINK_ALLOWLIST.get("lumiere", []), True)
check("kinoarena.com in arena-mega allowlist",
      "kinoarena.com" in S.VENUE_LINK_ALLOWLIST.get("arena-mega", []), True)
check("programata link rejected: not on vlaikova allowlist",
      not any("programata.bg" in a for a in S.VENUE_LINK_ALLOWLIST.get("vlaikova", [])), True)
check("programata link rejected: not on cc-sofia allowlist",
      not any("programata.bg" in a for a in S.VENUE_LINK_ALLOWLIST.get("cc-sofia", [])), True)

# --------------------------------------------------------- TMDB record building (mocked)
print("TMDB record building (mocked api)")
_canned_details = {
    "id": 12345,
    "overview": "A test English overview for the film.",
    "production_countries": [{"name": "Bulgaria"}],
    "imdb_id": "tt1234567",
    "credits": {
        "crew": [{"job": "Director", "name": "Test Director"},
                 {"job": "Producer", "name": "Test Producer"}],
        "cast": [{"order": 0, "name": "Actor One"},
                 {"order": 1, "name": "Actor Two"},
                 {"order": 2, "name": "Actor Three"},
                 {"order": 3, "name": "Actor Four"},
                 {"order": 4, "name": "Actor Five"},
                 {"order": 5, "name": "Actor Six"}],
    },
}
_canned_bg = {"id": 12345, "overview": "Тест Български преглед на филма."}

_orig_api = TMDB.api
def _mock_api(path, params):
    if "bg" in str(params.get("language", "")):
        return _canned_bg
    return _canned_details
TMDB.api = _mock_api

try:
    ov, country, imdb_id, dir_, cast_ = TMDB.details_en(12345)
    check("TMDB director extracted from credits",
          dir_, "Test Director")
    check("TMDB cast is top 5 (not 6)",
          cast_, "Actor One, Actor Two, Actor Three, Actor Four, Actor Five")
    check("TMDB overview extracted",
          bool(ov and len(ov) > 5), True)
    check("TMDB country extracted",
          country, "Bulgaria")
    check("TMDB imdb_id extracted",
          imdb_id, "tt1234567")

    ovBg = TMDB.details_bg(12345)
    check("TMDB Bulgarian overview extracted",
          bool(ovBg and "Тест" in ovBg), True)
finally:
    TMDB.api = _orig_api

# -------------------------------------------------- TMDB keep-previous
print("TMDB keep-previous")
# When details_bg returns None the previous ovBg must be preserved.
# When credits are missing the previous dir/cast must be preserved.
import json as _json, pathlib as _pl, tempfile as _tmp, os as _os

_tmdb_prev = {
    "test-film-keep": {
        "ovBg": "Запазен български преглед.",
        "dir": "Предишен Режисьор",
        "cast": "Предишен Актьор",
    }
}

# Mock api that returns no credits and no Bulgarian overview
_canned_no_credits = {
    "id": 99,
    "overview": "English overview.",
    "production_countries": [{"name": "Germany"}],
    "imdb_id": "tt9999999",
    "credits": {},            # empty credits — no crew/cast
}
_canned_no_bg = {"id": 99}   # overview field absent → details_bg returns None

def _mock_api_keep_prev(path, params):
    if "bg" in str(params.get("language", "")):
        return _canned_no_bg
    return _canned_no_credits

_orig_api2 = TMDB.api
TMDB.api = _mock_api_keep_prev
try:
    ov2, country2, imdb2, dir2, cast2 = TMDB.details_en(99)
    ovBg2 = TMDB.details_bg(99)
    check("keep-prev: details_en with empty credits returns None dir",
          dir2, None)
    check("keep-prev: details_en with empty credits returns None cast",
          cast2, None)
    check("keep-prev: details_bg with missing overview returns None",
          ovBg2, None)
    # Simulate the fetch_tmdb main() keep-previous merge logic inline
    pv = _tmdb_prev["test-film-keep"]
    rec: dict = {}
    if dir2:
        rec["dir"] = dir2
    elif pv.get("dir"):
        rec["dir"] = pv["dir"]
    if cast2:
        rec["cast"] = cast2
    elif pv.get("cast"):
        rec["cast"] = pv["cast"]
    if ovBg2:
        rec["ovBg"] = ovBg2
    elif pv.get("ovBg"):
        rec["ovBg"] = pv["ovBg"]
    check("keep-prev: previous dir preserved when credits empty",
          rec.get("dir"), "Предишен Режисьор")
    check("keep-prev: previous cast preserved when credits empty",
          rec.get("cast"), "Предишен Актьор")
    check("keep-prev: previous ovBg preserved when details_bg returns None",
          rec.get("ovBg"), "Запазен български преглед.")
finally:
    TMDB.api = _orig_api2

# ---------------------------------------- inject_films genre-patch
print("inject_films genre-patch")
import inject_films as IF

# Build a minimal FILMS array in a temp index.html
_films_new = [
    {"id": "seed-film", "title": "Seed Film", "genres": ["Драма"]},   # seed — never touch
    {"id": "synth-existing-empty",  "title": "Synth A", "genres": []},  # already in FILMS, empty genres
    {"id": "synth-existing-full",   "title": "Synth B", "genres": ["Комедия"]},  # already in FILMS, non-empty
]
_cinema_new = {
    "synth-existing-empty":  {"id": "synth-existing-empty",  "title": "Synth A", "genres": []},
    "synth-existing-full":   {"id": "synth-existing-full",   "title": "Synth B", "genres": ["Комедия"]},
    "synth-new-empty":       {"id": "synth-new-empty",       "title": "Synth C", "genres": []},
    "synth-new-with-genres": {"id": "synth-new-with-genres", "title": "Synth D", "genres": []},
}
_filminfo_new = {
    "synth-existing-empty":  {"genres": ["Трилър"]},          # should be patched
    "synth-existing-full":   {"genres": ["Екшън"]},           # must NOT be touched (already has genres)
    "synth-new-empty":       {},                              # no genres in film_info
    "synth-new-with-genres": {"genres": ["Документален"]},    # should be filled on insert
}

_html_stub = (
    'const FILMS=' + _json.dumps(_films_new, ensure_ascii=False, separators=(",",":")) + ';'
)

with _tmp.TemporaryDirectory() as _td:
    _td = _pl.Path(_td)
    _html_path   = _td / "index.html"
    _cinema_path = _td / "cinema_films.json"
    _fi_path     = _td / "film_info.json"
    _html_path.write_text(_html_stub, encoding="utf-8")
    _cinema_path.write_text(_json.dumps(_cinema_new, ensure_ascii=False), encoding="utf-8")
    _fi_path.write_text(_json.dumps(_filminfo_new, ensure_ascii=False), encoding="utf-8")

    # Point inject_films at our temp files
    _orig_html   = IF.HTML
    _orig_cinema = IF.CINEMA_FILMS
    _orig_fi     = IF.FILM_INFO
    IF.HTML        = _html_path
    IF.CINEMA_FILMS = _cinema_path
    IF.FILM_INFO   = _fi_path
    try:
        IF.main()
    finally:
        IF.HTML        = _orig_html
        IF.CINEMA_FILMS = _orig_cinema
        IF.FILM_INFO   = _orig_fi

    _result_src   = _html_path.read_text(encoding="utf-8")
    _result_films = _json.loads(_result_src[_result_src.index("["): _result_src.rindex("]")+1])
    _by_id = {f["id"]: f for f in _result_films}

check("inject_films: seed film genres untouched",
      _by_id["seed-film"]["genres"], ["Драма"])
check("inject_films: already-present synth with empty genres gets patched",
      _by_id["synth-existing-empty"]["genres"], ["Трилър"])
check("inject_films: already-present synth with non-empty genres NOT overwritten",
      _by_id["synth-existing-full"]["genres"], ["Комедия"])
check("inject_films: newly inserted synth with no film_info genres stays empty",
      _by_id.get("synth-new-empty", {}).get("genres", []), [])
check("inject_films: newly inserted synth with film_info genres gets filled",
      _by_id.get("synth-new-with-genres", {}).get("genres", []), ["Документален"])

# -------------------------------- NDK image extraction (harvest_ndk_img)
print("NDK image extraction (resize1000x1000 vs 182x136 thumbnails)")
_NDK_IMG_HTML = """<html><body>
<h1>Akira Kurosawa: Dreams (1990)</h1>
<div class="event-detail">
  <img src="https://ndk.bg/storage/thumbnails/2026/08/31/38393/group-55-kinocult-festival-20260831-064826_resize1000x1000.jpg?v=1788158928"
       alt="Akira Kurosawa: Dreams (1990)">
  <p>Event description here about the Kurosawa Dreams film.</p>
</div>
<!-- sidebar: other events — 182x136 thumbnails must never be used -->
<div class="sidebar">
  <img src="https://ndk.bg/storage/thumbnails/2026/08/28/38390/frame-9-kinocult-festival-2-20260828-073318_182x136.jpeg?v=1787902500"
       alt="Алехандро Ходоровски: Къртицата (1970)">
  <img src="https://ndk.bg/storage/thumbnails/2026/08/28/38389/group-52-kinocult-festival-20260828-073306_182x136.jpeg?v=1788134441"
       alt="Алехандро Ходоровски: Свещената планина (1973)">
</div>
</body></html>"""
_ndk_img_soup = BeautifulSoup(_NDK_IMG_HTML, "lxml")
_ndk_img = FI.harvest_ndk_img(_ndk_img_soup, "https://www.ndk.bg/en/event/akira-kurosava")
check("ndk harvest: main resize1000x1000 image selected",
      bool(_ndk_img and "_resize1000x1000" in _ndk_img), True)
check("ndk harvest: 182x136 thumbnails not selected",
      bool(_ndk_img and "_182x136" not in _ndk_img), True)
check("ndk harvest: returns a full https URL",
      bool(_ndk_img and _ndk_img.startswith("https://")), True)
# Only-one-candidate case — should return it even without alt-text match
_NDK_IMG_ONE_HTML = """<html><body>
<h1>Some Other Film</h1>
<img src="https://ndk.bg/storage/thumbnails/2026/08/28/38390/some-film_resize1000x1000.jpeg"
     alt="Different Alt Text">
</body></html>"""
_ndk_img_one = FI.harvest_ndk_img(BeautifulSoup(_NDK_IMG_ONE_HTML, "lxml"),
                                   "https://www.ndk.bg/en/event/some-other")
check("ndk harvest: single candidate returned even without alt match",
      bool(_ndk_img_one), True)
# Multiple candidates with NO alt match should return None (ambiguous)
_NDK_IMG_MULTI_HTML = """<html><body>
<h1>Film A</h1>
<img src="https://ndk.bg/storage/thumbnails/2026/08/28/1/img-a_resize1000x1000.jpeg" alt="Film B">
<img src="https://ndk.bg/storage/thumbnails/2026/08/28/2/img-b_resize1000x1000.jpeg" alt="Film C">
</body></html>"""
_ndk_img_multi = FI.harvest_ndk_img(BeautifulSoup(_NDK_IMG_MULTI_HTML, "lxml"),
                                     "https://www.ndk.bg/en/event/film-a")
check("ndk harvest: ambiguous multi-candidate without alt match returns None",
      _ndk_img_multi is None, True)

# -------------------------------- inject_data fallback priority
print("inject_data poster priority (TMDB > film_links > film_info img)")
# These are offline unit checks on the _fi_img_pending / posters_override logic
# built into inject_data.py. We test the reject_reason guard independently.

# A film_info img from ndk.bg (trusted) with a known film id should be accepted
_fi_img_ndk = "https://ndk.bg/storage/thumbnails/2026/08/31/38393/group-55_resize1000x1000.jpg"
_fi_why = PP.reject_reason("akira-kurosava-sanishta-1990", _fi_img_ndk, cat)
check("ndk.bg film_info img accepted by posterpolicy",
      _fi_why is None, True)
# A TMDB-matched film should NOT get its film_info img — checked via reject is None
# (the inject_data loop skips it before policy, but the policy itself is fine either way)
# A programata og:image (already tested above for kucheto-na-zlatyu etc) should pass
_fi_img_prog = "https://programata.bg/wp-content/uploads/2026/02/hqdefault.jpg"
_fi_why_prog = PP.reject_reason("akira-kurosava-sanishta-1990", _fi_img_prog, cat)
check("programata.bg film_info img accepted by posterpolicy",
      _fi_why_prog is None, True)
# Opaque hash on untrusted host should still be rejected
_fi_img_bad = "https://cdn.example.com/9f2b7c1d4e6a8b0c2d4e6f80.jpg"
_fi_why_bad = PP.reject_reason("akira-kurosava-sanishta-1990", _fi_img_bad, cat)
check("opaque hash on untrusted host rejected for film_info img",
      bool(_fi_why_bad), True)

# -------------------------------- declutter() — new cases
print("declutter: Director:Title(Year) and festival-prefix cases")
_dc = TMDB.declutter

# Director-prefix cases
_t, _y = _dc("Акира Куросава: Сънища (1990)")
check("declutter: Cyrillic Director:Title(Year) -> title",
      _t, "Сънища")
check("declutter: Cyrillic Director:Title(Year) -> year",
      _y, 1990)

_t, _y = _dc("Алехандро Ходоровски: Свещената планина (1973)")
check("declutter: Director:Title(Year) -> title",
      _t, "Свещената планина")
check("declutter: Director:Title(Year) -> year",
      _y, 1973)

# Festival prefix cases
_t2, _y2 = _dc("СИНЕЛИБРИ 2026 – Заглавие на филм")
check("declutter: festival prefix stripped",
      "СИНЕЛИБРИ" not in _t2, True)
check("declutter: festival prefix content kept",
      "Заглавие" in _t2, True)

_t3, _y3 = _dc("ЕВРОПЕЙСКИ КИНОКЛАСИКИ: Някакъв филм")
check("declutter: series prefix stripped",
      "ЕВРОПЕЙСКИ" not in _t3, True)
check("declutter: series prefix content kept",
      "Някакъв" in _t3, True)

# Trailing-bar year (title + bars)
_t4, _y4 = _dc("Филм | 1979 |")
check("declutter: trailing bar year stripped from title",
      "|" not in _t4, True)
check("declutter: trailing bar year extracted",
      _y4, 1979)

# "КИНОКЛАСИКИ: КОСА | 1979 |" — festival prefix + bars year + colon subtitle
_t4b, _y4b = _dc("КИНОКЛАСИКИ: КОСА | 1979 |")
check("declutter: КИНОКЛАСИКИ prefix stripped",
      "КИНОКЛАСИКИ" not in _t4b, True)
check("declutter: КОСА title kept",
      "КОСА" in _t4b, True)
check("declutter: bars year extracted from КИНОКЛАСИКИ case",
      _y4b, 1979)

# Passthrough: a normal title without clutter
_t5, _y5 = _dc("The Shining")
check("declutter: plain title unchanged",
      _t5, "The Shining")
check("declutter: plain title yields no year",
      _y5, None)

# ---- Regression: franchise / subtitle colons must NOT be stripped ----
print("declutter: franchise subtitle-colon regression (must pass through unchanged)")
for _franchise in [
    "Venom: The Last Dance",
    "2001: A Space Odyssey",
    "Spider-Man: Brand New Day",
    "Oasis: Don't Look Back In Anger",
    "Mission: Impossible – Dead Reckoning",
]:
    _ft, _fy = _dc(_franchise)
    # The full title must be preserved (colon and all)
    check(f"declutter: {_franchise!r} passes through unchanged",
          _ft, _franchise)
    check(f"declutter: {_franchise!r} yields no year",
          _fy, None)

# Year mismatch: pick() and pick_bg() must reject a result more than 1 year off
# when a year was embedded in the title.
print("declutter: year-mismatch guard (mocked api)")
_orig_api3 = TMDB.api

def _mock_api_wrong_year(path, params):
    # Returns a 2010 film when we search for a 1990 film
    return {"results": [{"id": 9999, "title": "Сънища", "original_title": "Dreams",
                          "release_date": "2010-01-01", "vote_count": 100,
                          "poster_path": "/fake.jpg"}]}
TMDB.api = _mock_api_wrong_year
try:
    _yr_mismatch_result = TMDB.search_en("Сънища", 1990)
    # pick() should still return the result (year scoring only, not hard reject)
    # The hard reject is in main() after declutter; test it inline here:
    _best_yr = _yr_mismatch_result
    _reject = False
    if _best_yr:
        _tmdb_yr = (_best_yr.get("release_date") or "")[:4]
        if _tmdb_yr and abs(int(_tmdb_yr) - 1990) > 1:
            _reject = True
    check("declutter year-mismatch: result from wrong decade is rejected",
          _reject, True)
finally:
    TMDB.api = _orig_api3

# ---- full-title-first ordering (bg path, mocked api) ----
print("declutter: full-title-first ordering on bg path (mocked api)")
_orig_api4 = TMDB.api
_bg_queries_seen = []

def _mock_api_full_first(path, params):
    q = params.get("query", "")
    _bg_queries_seen.append(q)
    # Full title "Акира Куросава: Сънища (1990)" returns no results;
    # decluttered "Сънища" returns a match — verifying fallback order.
    if "Куросава" in q or "1990" in q:
        return {"results": []}
    return {"results": [{"id": 777, "title": "Сънища", "original_title": "Dreams",
                          "release_date": "1990-07-24", "vote_count": 500}]}
TMDB.api = _mock_api_full_first
try:
    _bg_queries_seen.clear()
    # Simulate the bg search path: full title first, then decluttered fallback
    _full_bg = "Акира Куросава: Сънища (1990)"
    _clean_bg, _extr_yr = _dc(_full_bg)
    _res_full = TMDB.api("search/movie", {"query": _full_bg, "language": "bg",
                                           "include_adult": "false"})
    _best_full = TMDB.pick_bg((_res_full or {}).get("results", []), _full_bg, None)
    check("full-title-first: full title searched first",
          _bg_queries_seen[0] == _full_bg, True)
    check("full-title-first: full title returns no match (as expected)",
          _best_full is None, True)
    # Fallback
    _res_clean = TMDB.api("search/movie", {"query": _clean_bg, "language": "bg",
                                            "include_adult": "false"})
    _best_clean = TMDB.pick_bg((_res_clean or {}).get("results", []), _clean_bg,
                                _extr_yr)
    check("full-title-first: decluttered fallback finds a match",
          _best_clean is not None, True)
    check("full-title-first: fallback match has correct year",
          (_best_clean.get("release_date") or "")[:4], "1990")
finally:
    TMDB.api = _orig_api4

# ================================================= translate.py (mocked HTTP)
print("translate.py — offline tests with mocked HTTP")
import json as _j, os as _os, tempfile as _tf, importlib.util as _ilu, types as _ty, hashlib as _hs, unicodedata as _ucd

# Import translate.py without executing __main__
_tr_path = pathlib.Path(__file__).resolve().parent / "translate.py"
_tr_spec = _ilu.spec_from_file_location("translate", _tr_path)
_tr = _ilu.module_from_spec(_tr_spec)
# Temporarily suppress main() execution by patching __name__
_tr_spec.loader.exec_module(_tr)   # runs the module but not __main__ guard

# ---- helper: normalised cache key (mirrors translate.py) ----
def _ck(text):
    t = " ".join(_ucd.normalize("NFC", text).split())
    return _hs.sha1(t.encode("utf-8")).hexdigest()

# ---- 1. batching: ≤50 texts per request ----
print("  batching")
_batches_sent = []

def _mock_translate_batch(texts, kind):
    _batches_sent.append(len(texts))
    return [f"EN:{t}" for t in texts], True

_orig_tb = _tr.translate_batch
_tr.translate_batch = _mock_translate_batch

with _tf.TemporaryDirectory() as _td:
    _td = pathlib.Path(_td)
    _cache_path = _td / "translations.json"
    _orig_cache = _tr.CACHE_FILE
    _tr.CACHE_FILE = _cache_path
    # 55 unique texts — should produce 2 batches (50 + 5)
    _work = [(f"id{i}", f"Текст номер {i}") for i in range(55)]
    ok = _tr.translate_missing(_work, "syn", {})
    check("batching: ≤50 per request (batch 1 size)",
          _batches_sent[0] <= 50, True)
    check("batching: second batch covers remainder",
          len(_batches_sent) == 2 and _batches_sent[1] == 5, True)
    _tr.CACHE_FILE = _orig_cache

_tr.translate_batch = _orig_tb

# ---- 2. cache hit: no request made for already-cached text ----
print("  cache hit")
_requests_made = []

def _mock_tb_count(texts, kind):
    _requests_made.append(texts)
    return [f"EN:{t}" for t in texts], True

_tr.translate_batch = _mock_tb_count

_pre_cache = {_ck("Текст вече в кеша"): {"src": "Текст вече", "en": "Text already cached",
                                           "kind": "syn", "at": "2026-01-01"}}
with _tf.TemporaryDirectory() as _td2:
    _td2 = pathlib.Path(_td2)
    _cache_path2 = _td2 / "translations.json"
    _orig_cache2 = _tr.CACHE_FILE
    _tr.CACHE_FILE = _cache_path2
    # One text already in cache, one new
    _work2 = [("id-cached", "Текст вече в кеша"), ("id-new", "Нов текст")]
    _tr.translate_missing(_work2, "syn", _pre_cache)
    # Only the new text should have been sent
    _sent_texts = [t for batch in _requests_made for t in batch]
    check("cache hit: cached text not re-sent",
          "Текст вече в кеша" not in _sent_texts, True)
    check("cache hit: new text was sent",
          "Нов текст" in _sent_texts, True)
    _tr.CACHE_FILE = _orig_cache2

_tr.translate_batch = _orig_tb

# ---- 3. only-missing-English selection rules ----
print("  selection rules")
# Film WITH synEn → not selected
_films_sel = [
    {"id": "f-has-en",   "synEn": "Already in English", "synBg": "Има английски", "bg": "Заглавие", "en": "Title"},
    {"id": "f-needs-en", "synBg": "Трябва превод", "bg": "Заглавие Ново"},   # no en title → needs translation
    {"id": "f-tmdb-ov",  "synBg": "TMDB има преглед", "bg": "Заглавие Tmdb"},
]
_tmdb_sel = {"f-tmdb-ov": {"ov": "English from TMDB", "en": "Title from TMDB"}}
_fi_sel = {}

_syn_list, _title_list = _tr.films_needing_translation(_films_sel, _tmdb_sel, _fi_sel)
_syn_ids = [fid for fid, _ in _syn_list]
_title_ids = [fid for fid, _ in _title_list]
check("selection: film with synEn not selected for syn",
      "f-has-en" not in _syn_ids, True)
check("selection: film without synEn selected for syn",
      "f-needs-en" in _syn_ids, True)
check("selection: film with TMDB ov not selected for syn",
      "f-tmdb-ov" not in _syn_ids, True)
check("selection: film with en title not selected for title",
      "f-has-en" not in _title_ids, True)
check("selection: film with TMDB en title not selected for title",
      "f-tmdb-ov" not in _title_ids, True)
check("selection: film without en title IS selected",
      "f-needs-en" in _title_ids, True)

# Show selection
_shows_sel = [
    {"id": "s-has-en",   "synEn": "Already EN", "synBg": "Има", "title": "Заглавие", "titleEn": "Title EN"},
    {"id": "s-needs-en", "synBg": "Трябва превод", "title": "Заглавие 2"},
]
_show_syn, _show_title = _tr.shows_needing_translation(_shows_sel)
_show_syn_ids = [sid for sid, _ in _show_syn]
_show_title_ids = [sid for sid, _ in _show_title]
check("selection: show with synEn not selected",
      "s-has-en" not in _show_syn_ids, True)
check("selection: show without synEn selected",
      "s-needs-en" in _show_syn_ids, True)
check("selection: show with titleEn not selected",
      "s-has-en" not in _show_title_ids, True)
check("selection: show without titleEn selected",
      "s-needs-en" in _show_title_ids, True)

# ---- 4. key absent → skip cleanly (exit 0) ----
print("  key absent → skip")
_orig_key = _tr.DEEPL_KEY
_tr.DEEPL_KEY = ""
import io as _io
_buf = _io.StringIO()
import sys as _sys
_old_stdout = _sys.stdout
_sys.stdout = _buf
_rc = _tr.main()
_sys.stdout = _old_stdout
_out = _buf.getvalue()
check("key absent: main() returns 0",
      _rc, 0)
check("key absent: prints skip message",
      "DEEPL_AUTH_KEY not set" in _out, True)
_tr.DEEPL_KEY = _orig_key

# ---- 5. HTTP 456 quota → stop gracefully keeping partial results ----
print("  456 quota → partial stop")
_quota_calls = [0]

def _mock_tb_quota(texts, kind):
    _quota_calls[0] += 1
    if _quota_calls[0] == 1:
        # First batch succeeds — returns one EN result per input text
        return [f"EN:{t}" for t in texts], True
    # Second batch: quota hit
    return [], False

_tr.translate_batch = _mock_tb_quota

with _tf.TemporaryDirectory() as _td3:
    _td3 = pathlib.Path(_td3)
    _cache_quota = _td3 / "translations.json"
    _orig_c3 = _tr.CACHE_FILE
    _tr.CACHE_FILE = _cache_quota
    _partial_cache = {}
    # 51 unique texts — forces two batches (50 + 1); second call triggers quota
    _work3 = [(f"id-q{i}", f"Текст {i} за тест") for i in range(51)]
    _ok3 = _tr.translate_missing(_work3, "syn", _partial_cache)
    check("quota stop: translate_missing returns False on quota",
          not _ok3, True)
    check("quota stop: first batch results kept in cache",
          len(_partial_cache) == 50, True)
    _tr.CACHE_FILE = _orig_c3

_tr.translate_batch = _orig_tb

# ---- 6. inject_data mapping: cached translation reaches SYN_EN / TITLE_EN ----
print("  inject_data mapping")
_films_map = [
    {"id": "f-translated", "synBg": "Преведен текст", "bg": "Преведено заглавие"},
    {"id": "f-with-en",    "synEn": "Has English", "synBg": "Има БГ", "bg": "Заглавие", "en": "Title"},
]
_shows_map = [
    {"id": "s-translated", "synBg": "Преведено шоу", "title": "Шоу заглавие"},
    {"id": "s-has-en",     "synEn": "Has EN", "synBg": "Шоу БГ", "title": "Шоу", "titleEn": "Show EN"},
]
_cache_map = {
    _ck("Преведен текст"):     {"en": "Translated text",  "kind": "syn"},
    _ck("Преведено заглавие"): {"en": "Translated title", "kind": "title"},
    _ck("Преведено шоу"):      {"en": "Translated show",  "kind": "syn"},
    _ck("Шоу заглавие"):       {"en": "Show title EN",    "kind": "title"},
}
_syn_en_out, _title_en_out = _tr.build_mappings(
    _films_map, _shows_map, {}, {}, _cache_map)
check("mapping: translated film syn appears in SYN_EN",
      _syn_en_out.get("f-translated") == "Translated text", True)
check("mapping: translated film title appears in TITLE_EN",
      _title_en_out.get("f-translated") == "Translated title", True)
check("mapping: film WITH English syn not in SYN_EN",
      "f-with-en" not in _syn_en_out, True)
check("mapping: translated show syn in SYN_EN",
      _syn_en_out.get("s-translated") == "Translated show", True)
check("mapping: translated show title in TITLE_EN",
      _title_en_out.get("s-translated") == "Show title EN", True)
check("mapping: show with titleEn not in TITLE_EN",
      "s-has-en" not in _title_en_out, True)

print()
if fails:
    print(f"{len(fails)} test(s) failed: " + ", ".join(fails))
    sys.exit(1)
print("all parser and policy tests passed")
