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

print()
if fails:
    print(f"{len(fails)} test(s) failed: " + ", ".join(fails))
    sys.exit(1)
print("all parser and policy tests passed")
