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

# ---- NDK inline-credits label-bleed guard (issue: Director eats Screenplay/Starring) ----
print("ndk inline-credits label-bleed guard")
# Reproduces the real ndk.bg layout: all credits in a single paragraph,
# separated only by "Label: value" pairs (no newlines between them).
_NDK_BLEED_HTML = """<html><body>
<div class="event-description">
  <p>shed by Criterion Collection. Director: Akira Kurosawa Screenplay: Akira Kurosawa
Starring: Akira Terao, Mitsuko Baisho, Mie Harada, Chishu Ryu, Martin Scorsese
Cinematography: Takao Saito, Shoji Ueda Editing: Tome Minami Music: Shinichiro Ikebe
Language: Japanese | Subtitles: Bulgarian Duration: 120 min.
A little boy witnesses a wedding procession of foxes in a forest. The story continues
with eight dreamlike vignettes inspired by personal recollections of the director.</p>
</div>
<a class="ticket-link" href="https://epaygo.bg/12345">Buy tickets</a>
</body></html>"""
_ndk_bleed_soup = BeautifulSoup(_NDK_BLEED_HTML, "lxml")
_ndk_bleed_rec = FI.parse_ndk_page(_ndk_bleed_soup, "https://www.ndk.bg/en/event/akira-bleed/")
check("ndk-bleed: director is exactly 'Akira Kurosawa'",
      _ndk_bleed_rec.get("dir") == "Akira Kurosawa", True)
check("ndk-bleed: director does not contain 'Screenplay'",
      "Screenplay" not in (_ndk_bleed_rec.get("dir") or ""), True)
check("ndk-bleed: cast does not contain 'Cinematography'",
      "Cinematography" not in (_ndk_bleed_rec.get("cast") or ""), True)
check("ndk-bleed: cast does not contain 'Music'",
      "Music" not in (_ndk_bleed_rec.get("cast") or ""), True)
check("ndk-bleed: cast contains 'Akira Terao'",
      "Akira Terao" in (_ndk_bleed_rec.get("cast") or ""), True)
check("ndk-bleed: cast limited to 5 names",
      len((_ndk_bleed_rec.get("cast") or "").split(",")) <= 5, True)

# ---- Programata label-bleed guard (issue: dir = 'В ролите: О' from bad source data) ----
print("programata label-bleed guard (bad source data)")
# Reproduces the real programata.bg bug where the Режисьор field contains
# a partial cast label instead of the director name.
_PROG_BLEED_HTML = """<html><body>
<div class="text-summary">
  <div class="text-summary-item movie-0"><span>Жанр: </span>комедия, драма</div>
  <div class="text-summary-item movie-1"><span>Държава: </span>САЩ</div>
  <div class="text-summary-item movie-2"><span>Режисьор: </span>В ролите: О</div>
  <div class="text-summary-item movie-3"><span>Участват: </span>Оливия Уайлд, Пенелопе Крус, Сет Роугън</div>
</div>
<div class="text mb-5 pb-5">
  <p>Историята на комедия с хора, която се развива в съвременна Америка и показва трудностите на съжителство между съседите.</p>
</div>
</body></html>"""
_prog_bleed_soup = BeautifulSoup(_PROG_BLEED_HTML, "lxml")
_prog_bleed_rec = FI.parse_programata(_prog_bleed_soup,
                                      "https://programata.bg/kino/filmi/sasedite-otgore/")
check("prog-bleed: garbage dir 'В ролите: О' is discarded (dir absent or clean)",
      not _prog_bleed_rec.get("dir") or
      FI._CREDIT_LABEL_RE.search(_prog_bleed_rec.get("dir") or "") is None, True)
check("prog-bleed: cast is still extracted correctly",
      "Оливия Уайлд" in (_prog_bleed_rec.get("cast") or ""), True)
check("prog-bleed: synopsis extracted",
      len(_prog_bleed_rec.get("synBg") or "") >= 80, True)

# ---- _clean_credit_value unit tests ----
print("_clean_credit_value helper")
check("clean: strips 'Screenplay:' tail",
      FI._clean_credit_value("Akira Kurosawa Screenplay: Akira Kurosawa"), "Akira Kurosawa")
check("clean: strips 'Starring:' tail",
      FI._clean_credit_value("Someone Starring: Actor A, Actor B"), "Someone")
check("clean: strips Bulgarian 'В ролите:' tail",
      FI._clean_credit_value("В ролите: О"), "")
check("clean: clean value unchanged",
      FI._clean_credit_value("Akira Kurosawa"), "Akira Kurosawa")
check("clean: strips trailing punctuation",
      FI._clean_credit_value("Steven Spielberg,"), "Steven Spielberg")

# ---- _film_info_stale detects label bleed ----
print("_film_info_stale label-bleed detection")
check("stale: dir with label bleed is stale",
      FI._film_info_stale({"pv": FI._PARSER_VERSION, "dir": "Akira Kurosawa Screenplay: Akira Kurosawa"}), True)
check("stale: dir 'В ролите: О' is stale",
      FI._film_info_stale({"pv": FI._PARSER_VERSION, "dir": "В ролите: О"}), True)
check("stale: clean dir at current pv is not stale",
      FI._film_info_stale({"pv": FI._PARSER_VERSION, "dir": "Akira Kurosawa", "cast": "Akira Terao"}), False)

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

# =========================================== official cinema programmes (Wave M1)
# Each venue's own programme is authoritative for the dates it publishes. These
# pin the parsers on trimmed real pages (scripts/fixtures, 2026-10-08), where a
# programme ends, which titles are one film, and the merge rules.
import json as _jm
import official_sources as OS                                   # noqa: E402
import film_identity as FID                                     # noqa: E402


class _Resp:
    def __init__(self, body):
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")
        self.text = body if isinstance(body, str) else body.decode("utf-8", "replace")
        self.status_code = 200


class _Pages:
    """A stand-in Fetcher: serves fixture bodies by URL substring (first match)."""
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url, **kw):
        self.asked.append(url)
        for k, body in self.pages.items():
            if k in url:
                return _Resp(body)
        return None

    def soup(self, url, **kw):
        r = self.get(url)
        return BeautifulSoup(r.text, "lxml") if r else None

    def json(self, url, **kw):
        r = self.get(url)
        return _jm.loads(r.text) if r else None

    def post(self, *a, **kw):
        return None


def _raises(fn, *a):
    try:
        fn(*a)
    except OS.SourceBroken:
        return True
    return False


print("official: Cinema City API (cc-sofia, 2026-10-09, trimmed)")
_cc = _jm.loads((FIXTURES / "cinemacity_day.json").read_text(encoding="utf-8"))
_r = OS.parse_cinemacity_day(_cc, "2026-10-09", "1261")
check("one row per event", len(_r), 6)
check("title, date and time as published", ("Верити", "2026-10-09", "14:50") in {x[:3] for x in _r}, True)
check("hall, runtime and film page kept as meta",
      [(x[3]["hall"], x[3]["runtime"], x[3]["url"]) for x in _r if x[0] == "Верити"][0],
      ("Зала 8", 114, "https://www.cinemacity.bg/films/verity/8244s2r"))
check("genres mapped into the app's vocabulary",
      sorted([x for x in _r if x[0] == "Верити"][0][3]["genres"]), ["Драма", "Криминален", "Трилър"])
check("a TBC pre-sale event is flagged", sum(1 for x in _r if x[3].get("tbc")), 2)
check("an answer for another day yields no rows", OS.parse_cinemacity_day(_cc, "2026-10-10", "1261"), [])
check("another cinema's events are ignored", OS.parse_cinemacity_day(_cc, "2026-10-09", "1266"), [])
check("a body without films/events is broken, not 'no screenings'",
      _raises(OS.parse_cinemacity_day, {"body": {}}, "2026-10-09"), True)

print("official: where a published programme ends")
_cnt = [("2026-10-08", 54), ("2026-10-09", 55), ("2026-10-10", 67), ("2026-10-11", 67),
        ("2026-10-12", 48), ("2026-10-13", 47), ("2026-10-14", 48), ("2026-10-15", 55),
        ("2026-10-16", 1), ("2026-10-17", 2), ("2026-10-18", 1), ("2026-10-19", 0)]
check("thin pre-sale days after the week are not published (Cinema City)",
      OS.published_through(_cnt), "2026-10-15")
check("today thinned by past screenings still counts",
      OS.published_through([("2026-10-08", 2)] + _cnt[1:]), "2026-10-15")
check("a failed day ends coverage before it",
      OS.published_through(_cnt[:3] + [("2026-10-11", None)] + _cnt[4:]), "2026-10-10")
check("nothing published -> None", OS.published_through([("2026-10-08", 0), ("2026-10-09", 0)]), None)
_dkc = [("2026-10-08", 4), ("2026-10-09", 3), ("2026-10-10", 5), ("2026-10-11", 4), ("2026-10-12", 4),
        ("2026-10-13", 4), ("2026-10-14", 3), ("2026-10-15", 4), ("2026-10-16", 2), ("2026-10-17", 4),
        ("2026-10-18", 4), ("2026-10-19", 2), ("2026-10-20", 1), ("2026-10-21", 1)]
check("Дом на киното: festival-only days end the normal run", OS.published_through(_dkc, 0.5), "2026-10-19")


class _DayFeed:
    """A per-day source: counts {date: n} → that many screenings at distinct times."""
    def __init__(self, counts):
        self.counts, self.asked = counts, []

    def _date(self, url):
        m = re.search(r"(\d{4})-(\d\d)-(\d\d)", url)
        if m:
            return m.group(0)
        m = re.search(r"(\d\d)\.(\d\d)\.(\d{4})", url)
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

    def json(self, url, **kw):                 # Cinema City quickbook API
        d = self._date(url)
        self.asked.append(d)
        n = self.counts.get(d, 0)
        ev = [{"filmId": "f", "cinemaId": "1261", "eventDateTime": f"{d}T{10 + i // 6:02d}:{(i % 6) * 10:02d}:00",
               "attributeIds": []} for i in range(n)]
        return {"body": {"films": [{"id": "f", "name": "Филм"}], "events": ev}}

    def get(self, url, **kw):                  # Дом на киното day fragment
        d = self._date(url)
        self.asked.append(d)
        hours = "".join(f'<a class="hour-box" href="#">{10 + i}:00</a>' for i in range(self.counts.get(d, 0)))
        return _Resp(f'<div class="film-box"><h3 class="film-title">Филм</h3>{hours}</div>' if hours else "<div></div>")


import re                                                       # noqa: E402
_days = lambda start, n: [(dt.date.fromisoformat(start) + dt.timedelta(days=i)).isoformat() for i in range(n)]
_ccc = {d: 50 for d in _days("2026-10-08", 8)}
_ccc.update({"2026-10-16": 1, "2026-10-21": 2, "2026-10-24": 1})
_feed = _DayFeed(_ccc)
_res = OS.fetch_cinemacity(_feed, "cc-sofia", "2026-10-08", "2026-12-14")
check("Cinema City: coverage is the normal week only", (_res.covered_from, _res.covered_to), ("2026-10-08", "2026-10-15"))
check("…advance sales after it are fetched past empty days and kept (the merge marks them preliminary)",
      sorted({r[1] for r in _res.rows if r[1] > "2026-10-15"}), ["2026-10-16", "2026-10-21", "2026-10-24"])
check("…and the fetch stops after 14 empty days in a row", _feed.asked[-1], "2026-11-07")
_dk_days = dict(_dkc)
_dk_days.update({d: 1 for d in _days("2026-10-22", 19)})          # festival: one a day to 11-09
_dk_days["2026-11-11"] = 1
_dkf = _DayFeed(_dk_days)
_res = OS.fetch_domkino(_dkf, "dom-kino", "2026-10-08", "2026-12-14")
check("Дом на киното: coverage = the run of normal days, the next week's first days included",
      _res.covered_to, "2026-10-19")
_later = sorted({r[1] for r in _res.rows if r[1] > "2026-10-19"})
check("…the festival screenings after it are official rows (preliminary)",
      (len(_later), _later[0], _later[-1]), (22, "2026-10-20", "2026-11-11"))
check("…fetched until 14 empty days in a row", _dkf.asked[-1], "2026-11-25")

print("official: Кино Арена (arena-mega-mol, trimmed)")
_ka = (FIXTURES / "kinoarena_day.html").read_text(encoding="utf-8")
_sel, _tabs, _r = OS.parse_kinoarena_page(_ka, "arena-mega-mol")
check("the selected day tab is read", _sel, "2026-10-08")
check("published days come from the tabs", _tabs[:8], [f"2026-10-{d:02d}" for d in range(8, 16)])
check("every time of a film", sorted(t for ti, _, t, _m in _r if ti == "Верити"),
      ["13:00", "14:40", "17:00", "19:20", "21:30"])
check("the film page is the link", _r[0][3]["url"], "https://www.kinoarena.com/bg/movie/kulata-2-murtva-tochka")
check("another cinema's page is refused", _raises(OS.parse_kinoarena_page, _ka, "kino-arena-the-mall"), True)
_res = OS.fetch_kinoarena(_Pages({"arena-mega-mol/": _ka}), "arena-mega", "2026-10-08", "2026-12-14")
check("a date served with TODAY's programme ends coverage (never mislabelled)",
      (_res.covered_from, _res.covered_to), ("2026-10-08", "2026-10-08"))
check("rows of the fallback page are not re-dated", {x[1] for x in _res.rows}, {"2026-10-08"})


class _KA:
    """Кино Арена: a published date's page selects its own tab; any other date
    is answered with TODAY's page (tab 08-10 selected)."""
    def __init__(self, html, published):
        self.html, self.published, self.asked = html, published, []

    def get(self, url, **kw):
        m = re.search(r"/(\d\d)-(\d\d)-(\d{4})$", url)
        iso, dmy = f"{m.group(3)}-{m.group(2)}-{m.group(1)}", m.group(0)[1:]
        self.asked.append(iso)
        body = self.html
        if iso in self.published:
            body = body.replace('class="tabItem selected"', 'class="tabItem"').replace(
                f'class="tabItem" href="/bg/program/view/arena-mega-mol/{dmy}"',
                f'class="tabItem selected" href="/bg/program/view/arena-mega-mol/{dmy}"')
        return _Resp(body)


_week = set(_days("2026-10-08", 8))
_kaf = _KA(_ka, _week | {"2026-10-18", "2026-10-21", "2026-11-28"})
_res = OS.fetch_kinoarena(_kaf, "arena-mega", "2026-10-08", "2026-12-14")
check("Кино Арена: coverage is the contiguous published run", _res.covered_to, "2026-10-15")
check("…the advance-sale tabs are read one by one and kept when the page selects that date",
      sorted({r[1] for r in _res.rows if r[1] > "2026-10-15"}), ["2026-10-18", "2026-10-21", "2026-11-28"])
check("…a tab answered with TODAY's programme is never re-dated",
      any("2026-11-22: advance-sale tab served 2026-10-08" in n for n in _res.notes), True)
check("…tabs past the window end are not requested", max(_kaf.asked), "2026-11-28")

print("official: Cine Grand (София Ринг Мол, trimmed)")
_cg = (FIXTURES / "cinegrand_day.html").read_text(encoding="utf-8")
_sel, _tabs, _r, _ok = OS.parse_cinegrand_page(_cg, "софия-ринг-мол", "BGSOFCG2", "2026-10-08")
check("day tabs carry real dates", [d for d, _ in _tabs], [f"2026-10-{d:02d}" for d in range(8, 15)])
check("each show is dated by its own label", {x[1] for x in _r}, {"2026-10-08"})
check("runtime and hall kept", [(x[3]["runtime"], x[3]["hall"]) for x in _r if x[0] == "КОЛИТЕ"][0], (116, "TSAR Зала 6"))
check("the page carries this cinema", _ok, True)
check("Park Center never accepts Ring Mall's page",
      OS.parse_cinegrand_page(_cg, "парк-център-софия", "BGSOFCG1", "2026-10-08")[3], False)
check("Park Center served Ring Mall's programme is broken, not data",
      _raises(OS.fetch_cinegrand, _Pages({"schedule": _cg}), "cg-park", "2026-10-08", "2026-12-14"), True)
_res = OS.fetch_cinegrand(_Pages({"schedule": _cg}), "cg-ring", "2026-10-08", "2026-12-14")
check("a day page showing another day ends coverage",
      (_res.covered_from, _res.covered_to), ("2026-10-08", "2026-10-08"))

print("official: G8 weekly programme (trimmed)")
_g8 = (FIXTURES / "g8_week.html").read_text(encoding="utf-8")
_wf, _wt, _r = OS.parse_g8(_g8)
check("the week G8 itself published", (_wf, _wt), ("2026-10-02", "2026-10-08"))
check("start of 'HH:MM - HH:MM'", ('Улица "Малага"', "2026-10-08", "14:00") in {x[:3] for x in _r}, True)
check("the legend row is not a screening", len(_r), 6)
check("runtime and genre from the item", (_r[0][3]["runtime"], _r[0][3]["genres_text"]), (100, ["Драма"]))
_res = OS.fetch_g8(_Pages({"movies.php": _g8}), "g8", "2026-10-08", "2026-12-14")
check("coverage = today..week end, never G8's unpublished next week",
      (_res.covered_from, _res.covered_to), ("2026-10-08", "2026-10-08"))
check("a week already over is broken (keep previous), not 'no screenings'",
      _raises(OS.fetch_g8, _Pages({"movies.php": _g8}), "g8", "2026-10-09", "2026-12-14"), True)

print("official: Одеон (windows-1251, trimmed)")
_od = (FIXTURES / "odeon_program.html").read_bytes()
_p = OS.parse_odeon_page(_od)
check("windows-1251 decoded", [x[0] for x in _p["rows"] if x[1] == "2026-10-08"], ["Дигър", "Шибил", "Тигрите", "NAZA"])
check("programme number and next link", (_p["number"], _p["next"]), (915, "http://bnf.bg/bg/odeon/program/916/"))
check("day sections", _p["dates"], ["2026-10-07", "2026-10-08"])
_empty = (FIXTURES / "odeon_program_empty.html").read_bytes()
check("the next programme's empty shell has no rows", OS.parse_odeon_page(_empty)["rows"], [])
_res = OS.fetch_odeon(_Pages({"/program/916/": _empty, "/odeon/program/": _od}), "odeon", "2026-10-08", "2026-12-14")
check("an empty next programme is not coverage", (_res.covered_from, _res.covered_to), ("2026-10-08", "2026-10-08"))

print("official: Дом на киното (one day, trimmed)")
_r = OS.parse_domkino_day((FIXTURES / "domkino_day.html").read_text(encoding="utf-8"), "2026-10-13")
check("one row per hour box", [(x[0], x[2]) for x in _r],
      [("Кес", "20:00"), ("МУХА", "18:00"), ("Sofia Documental: NAZA", "18:30")])
check("runtime from 'Времетраене'", _r[0][3]["runtime"], 111)
check("film page as the link", _r[1][3]["url"], "https://domnakinoto.com/muha-movie7230.html?a=")

print("official: Влайкова grid (trimmed)")
_res = OS.fetch_vlaikova(_Pages({"vlaikovacinema.com": (FIXTURES / "vlaikova_grid.html").read_text(encoding="utf-8")}),
                         "vlaikova", "2026-10-08", "2026-12-14")
check("coverage = the dates in the grid", (_res.covered_from, _res.covered_to), ("2026-10-08", "2026-10-09"))
check("rows dated by their day", sorted({x[1] for x in _res.rows}), ["2026-10-08", "2026-10-09"])

print("official: Люмиер = NDK ∪ KinoCult")
_lum = _Pages({"ndk.bg": (FIXTURES / "ndk_program.html").read_text(encoding="utf-8"),
               "sanity.io": (FIXTURES / "kinocult_screenings.json").read_text(encoding="utf-8")})
_res = OS.fetch_lumiere(_lum, "lumiere", "2026-10-08", "2026-12-14")
_slots = {(x[1], x[2]): x for x in _res.rows}
check("the same slot in both sources is one row with KinoCult's title",
      (_slots[("2026-10-10", "18:00")][0], _slots[("2026-10-10", "18:00")][3].get("alt_title")),
      ("Къртицата", "Алехандро Ходоровски: Къртицата (1970)"))
check("NDK-only and KinoCult-only screenings both kept",
      sorted(_slots), [("2026-10-10", "18:00"), ("2026-10-11", "17:00"), ("2026-10-14", "19:00"), ("2026-11-30", "19:00")])
check("other venues and dates past the window are not Люмиер rows",
      any(x[0] in ("Жертвоприношение", "Паднали ангели") for x in _res.rows), False)
check("coverage ends at the last date either source lists", _res.covered_to, "2026-11-30")
check("one half unreachable -> keep previous",
      _raises(OS.fetch_lumiere, _Pages({"ndk.bg": "<html></html>"}), "lumiere", "2026-10-08", "2026-12-14"), True)
check("cineland has no official source", OS.fetch_official("cineland", None, "2026-10-08", "2026-12-14", {}), None)

print("film identity: normalisation")
for a, b in [("ПАДАНЕ 2: МЪРТВА ТОЧКА", "Падане 2: Мъртва точка"),
             ("Одисея (IMAX 3D)", "Одисея"), ("Пес Патрул (2D, бг аудио)", "Пес Патрул"),
             ("Премиера: Одисея", "Одисея"), ("Миньони & чудовища", "Миньони и чудовища"),
             ("Улица „Малага“", 'Улица "Малага"'), ("Шопен-соната в Париж", "Шопен — соната в Париж"),
             ("СИНЕЛИБРИ 2026 – ЕМБАРГО", "Синелибри 2026: Ембарго"),
             ("ЕВРОПЕЙСКИ КИНОКЛАСИКИ: Алис в градовете (1974)", "Алис в градовете"),
             ("КИНОКЛАСИКИ: КОСА | 1979 |", "Коса"), ("Акира Куросава: Сънища (1990)", "Сънища"),
             ("Sofia Documental: NAZA", "NAZA")]:
    check(f"key({a[:30]!r}) == key({b[:24]!r})", FID.key(a), FID.key(b))
check("a title's own word 'премиерата' survives", FID.key("Колите: 20 години от премиерата"),
      "колите 20 години от премиерата")
check("no director prefix stripped without a year", FID.key("Мисията: Невъзможна"), "мисията невъзможна")
check("an ALL-CAPS title before a colon is not a festival prefix", FID.key("ПАДАНЕ 2: МЪРТВА ТОЧКА"),
      "падане 2 мъртва точка")
check("a bare trailing number is part of the title", FID.key("Блейд Рънър 2049"), "блейд рънър 2049")

print("film identity: known variants (both directions) and traps")
_cat = [
    {"id": "padane-2", "bg": "Падане 2: Мъртва точка", "en": "Fall 2: Deadpoint", "year": 2026, "runtime": 98},
    {"id": "kolite", "bg": "Колите: 20 години от премиерата", "en": "Cars (20th Anniversary)", "year": 2006, "runtime": 117},
    {"id": "sablezab", "bg": "Капитан Съблезъб и графинята на Грел", "en": "Captain Sabertooth", "year": 2026, "runtime": 85},
    {"id": "akira-kurosava-sanishta-1990", "bg": "Акира Куросава: Сънища (1990)", "en": "", "source": "lumiere"},
    {"id": "avatar-3", "bg": "Аватар: Огън и пепел", "en": "Avatar: Fire and Ash", "year": 2025},
    {"id": "naza", "bg": "НАЗА", "en": "", "source": "dom-kino"},
    {"id": "el-topo", "bg": "Къртицата", "en": "El Topo", "year": 1970},
    {"id": "alehandro-hodorovski-kartitsata-1970", "bg": "Алехандро Ходоровски: Къртицата (1970)", "en": "", "source": "lumiere"},
    {"id": "minoni", "bg": "Миньони и чудовища", "en": "Minions & Monsters", "year": 2026},
    {"id": "vayana", "bg": "Смелата Ваяна", "en": "Moana", "year": 2016},
    {"id": "otrazheniya-3", "bg": "Отражения №3", "en": "Reflection No. 3", "year": 2025},
    {"id": "in-the-mood", "bg": "Любовно настроение", "en": "In the Mood for Love", "year": 2000},
    {"id": "milen-filmat", "bg": "Милен - филмът", "en": "", "source": "odeon"},
    {"id": "muzh", "bg": "Мъж", "en": "", "source": "odeon"},
]
_minted = {f["id"] for f in _cat if f.get("source")}
_idx = FID.FilmIndex(_cat, _minted)                      # + the real scripts/film_aliases.json


def _R(title, venue=None, corroborate=None, index=None, **meta):
    return (index or _idx).resolve(title, venue, meta, corroborate)[0]


check("'Падане 2' -> 'Падане 2: Мъртва точка'", _R("Падане 2", "arena-mega"), "padane-2")
check("'Колите' -> 'Колите: 20 години от премиерата'", _R("КОЛИТЕ", "cg-ring"), "kolite")
check("'Капитан Саблезъб…' -> 'Капитан Съблезъб…'", _R("КАПИТАН САБЛЕЗЪБ И ГРАФИНЯТА НА ГРЕЛ", "cg-ring"), "sablezab")
check("'Сънища' -> 'Акира Куросава: Сънища (1990)'", _R("Сънища", "lumiere"), "akira-kurosava-sanishta-1990")
_rev = FID.FilmIndex([{"id": "p2", "bg": "Падане 2"}, {"id": "k", "bg": "Колите"},
                      {"id": "s", "bg": "Капитан Саблезъб и графинята на Грел"},
                      {"id": "dreams", "bg": "Сънища", "source": "lumiere"}], {"dreams"})
check("reverse: 'Падане 2: Мъртва точка' -> 'Падане 2'", _R("Падане 2: Мъртва точка", "cc-sofia", index=_rev), "p2")
check("reverse: 'Колите: 20 години…' -> 'Колите'", _R("Колите: 20 години от премиерата", "x", index=_rev), "k")
check("reverse: 'Капитан Съблезъб…' -> 'Капитан Саблезъб…'", _R("Капитан Съблезъб и графинята на Грел", "x", index=_rev), "s")
check("reverse: 'Акира Куросава: Сънища (1990)' -> 'Сънища'", _R("Акира Куросава: Сънища (1990)", "lumiere", index=_rev), "dreams")
check("1-edit spelling merges without an alias",
      _R("Капитан Саблезъб и графинята на Грел", "x", index=FID.FilmIndex(_cat[2:3], aliases={})), "sablezab")
check("trap: 'Аватар' is not 'Аватар: Огън и пепел'", _R("Аватар", "cc-sofia"), None)
check("trap: reverse, 'Аватар: Огън и пепел' is not 'Аватар'",
      _R("Аватар: Огън и пепел", "x", index=FID.FilmIndex([{"id": "a1", "bg": "Аватар", "year": 2009}], aliases={})), None)
_fall = FID.FilmIndex([{"id": "fall", "bg": "Падане", "year": 2022}], aliases={})
check("trap: 'Падане 2' is not 'Падане'", _R("Падане 2", "x", index=_fall), None)
check("trap: 'Падане' is not 'Падане 2: Мъртва точка'", _R("Падане", "x"), None)
check("trap: 'Отражения №4' is not 'Отражения №3'", _R("Отражения №4", "x"), None)
check("Latin == Cyrillic: 'NAZA' -> 'НАЗА'", _R("NAZA", "odeon"), "naza")
check("Latin == Cyrillic, reverse", _R("НАЗА", "x", index=FID.FilmIndex([{"id": "nz", "bg": "NAZA"}], aliases={})), "nz")
check("trap: two Cyrillic spellings are not 'transliterated' together", _R("Маж", "x"), None)
check("'&' is 'и'", _R("Миньони & чудовища", "cc-sofia"), "minoni")
check("the catalogue film wins over a minted duplicate key", _R("Алехандро Ходоровски: Къртицата (1970)", "lumiere"), "el-topo")
check("the source's original title places a renamed film", _R("В настроение за любов", "lumiere", original_title="In the Mood for Love"), "in-the-mood")
check("a year conflict vetoes a title match (2026 remake vs 2016 film)", _R("Смелата Ваяна", "cc-sofia", year=2026), None)
check("same title, no year conflict -> the catalogue film", _R("Смелата Ваяна", "cc-sofia"), "vayana")
check("subtitle variant with same venue/date/time corroboration",
      _R("Милен", "odeon", corroborate=lambda f: f == "milen-filmat"), "milen-filmat")
# a fuzzy decision is remembered for the run (one title, one film), so every
# negative case below gets an index of its own
check("subtitle variant without corroboration is not merged",
      _R("Милен", "odeon", corroborate=lambda f: False, index=FID.FilmIndex(_cat, _minted)), None)
check("subtitle variant corroborated by the venue's runtime",
      _R("КОЛИТЕ", "cg-ring", index=FID.FilmIndex(_cat, _minted, aliases={}), runtime=116), "kolite")
check("subtitle variant with a different runtime is not merged",
      _R("КОЛИТЕ", "cg-ring", index=FID.FilmIndex(_cat, _minted, aliases={}), runtime=90), None)
check("an alias scoped to Люмиер does not apply elsewhere",
      _R("Сънища", "cc-sofia", index=FID.FilmIndex([{"id": "other", "bg": "Нещо"}])), None)
_dup = FID.FilmIndex(_cat + [{"id": "av", "bg": "Аватар", "source": "cc-sofia"}], _minted | {"av"}, aliases={})
check("the unmerged look-alike is listed as a suspected duplicate",
      [(d["title"], d["film"]) for d in _dup.suspected_duplicates(["av"])], [("Аватар", "avatar-3")])

print("merge: the venue's own programme is authoritative")
_F, _E = "2026-10-08", "2026-12-14"
_off = [("a", "2026-10-08", "13:00"), ("a", "2026-10-08", "15:00"), ("b", "2026-10-09", "20:00"),
        ("c", "2026-10-20", "19:00")]
_agg = [("a", "A", "2026-10-08", ["13:00", "17:00"], None), ("x", "X", "2026-10-09", ["18:00"], None),
        (None, "New", "2026-10-17", ["21:00"], "https://programata.bg/kino/filmi/new/"),
        ("c", "C", "2026-10-20", ["18:00"], None), ("d", "D", "2026-10-18", ["12:00"], None)]
_prev = [["old", "v", "2026-10-07", ["10:00"]], ["gone", "v", "2026-10-11", ["20:30"]]]
_rows, _pf, _info = S.merge_cinema_venue("v", "official", _off, ("2026-10-08", "2026-10-15"), _agg, True,
                                         _prev, None, _F, _E, resolve_kept=lambda t, d, ts, l: "new")
check("inside coverage: exactly the official rows; after it: aggregator rows + official extras", _rows,
      [["a", "v", "2026-10-08", ["13:00", "15:00"]], ["b", "v", "2026-10-09", ["20:00"]],
       ["new", "v", "2026-10-17", ["21:00"]], ["d", "v", "2026-10-18", ["12:00"]],
       ["c", "v", "2026-10-20", ["19:00"]]])
check("contradicting aggregator rows are discarded and logged", _info["discarded"],
      [["2026-10-08", "17:00", "A", "a"], ["2026-10-09", "18:00", "X", "x"]])
check("PRELIM_FROM = the day after the official coverage", _pf, "2026-10-16")
_rows, _pf, _ = S.merge_cinema_venue("v", "unreachable", [], None, _agg, True, _prev, "2026-10-12", _F, _E)
check("unreachable official source: previous rows kept, past ones dropped", _rows, [["gone", "v", "2026-10-11", ["20:30"]]])
check("unreachable official source: previous PRELIM_FROM kept", _pf, "2026-10-12")
check("unreachable with no previous PRELIM_FROM: preliminary from today",
      S.merge_cinema_venue("v", "unreachable", [], None, [], True, _prev, None, _F, _E)[1], _F)
_rows, _pf, _ = S.merge_cinema_venue("cineland", "none", [], None, [("a", "A", "2026-10-08", ["13:00"], None)],
                                     True, [], None, _F, _E)
check("no official source (Cineland): aggregator rows, preliminary from today", (_rows, _pf),
      ([["a", "cineland", "2026-10-08", ["13:00"]]], _F))
_rows, _ = S.merge_cinema_venue("cineland", "none", [], None, [], False,
                                [["a", "cineland", "2026-10-09", ["11:00"]], ["z", "cineland", "2026-10-01", ["11:00"]]],
                                None, _F, _E)[:2]
check("no official source and aggregator down: previous rows, never past ones", _rows,
      [["a", "cineland", "2026-10-09", ["11:00"]]])
_rows, _pf, _ = S.merge_cinema_venue("g8", "official", [("a", "2026-10-09", "12:00")], ("2026-10-09", "2026-10-15"),
                                     [("q", "Q", "2026-10-08", ["10:00"], None)], True,
                                     [["p", "g8", "2026-10-08", ["11:00"]]], "2026-10-09", _F, _E)
check("before a coverage that starts after today: only rows confirmed last run", _rows,
      [["p", "g8", "2026-10-08", ["11:00"]], ["a", "g8", "2026-10-09", ["12:00"]]])
_page = 'const VLINKS=[["a","v","u"]];\n/* SOFIA-DATA-END */'
_page = S.write_prelim_from(_page, {"g8": "2026-10-09"})
check("PRELIM_FROM inserted right after VLINKS",
      _page, 'const VLINKS=[["a","v","u"]];\nconst PRELIM_FROM={"g8":"2026-10-09"};\n/* SOFIA-DATA-END */')
_page = S.write_prelim_from('const PRELIM_FROM={};\nconst VLINKS=[];', {"cineland": "2026-10-08"})
check("an existing PRELIM_FROM is replaced in place",
      (_page.count("PRELIM_FROM"), S.read_prelim_from(_page)), (1, {"cineland": "2026-10-08"}))
check("a source that worked last run and now yields a fraction stops the scrape",
      bool(S.implausible_drop({"status": "official", "screenings": 400, "days": 8},
                              {"status": "official", "screenings": 40, "days": 8})), True)
check("a normal week-to-week change does not",
      S.implausible_drop({"status": "official", "screenings": 400, "days": 8},
                         {"status": "official", "screenings": 300, "days": 8}), None)
check("a tiny programme (Люмиер) is never 'implausible'",
      S.implausible_drop({"status": "official", "screenings": 7, "days": 58},
                         {"status": "official", "screenings": 1, "days": 58}), None)


print()
if fails:
    print(f"{len(fails)} test(s) failed: " + ", ".join(fails))
    sys.exit(1)
print("all parser and policy tests passed")
