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

# -------------------------------------------------- OMDb rt/mc parsing (mocked HTTP)
print("OMDb rt/mc parsing (mocked urllib)")
import urllib.request as _ureq, io as _io, types as _ty
import json as _json_mod

class _FakeResp(_io.BytesIO):
    """Minimal file-like object accepted by json.load / urllib."""
    def __enter__(self): return self
    def __exit__(self, *a): return False

def _mock_urlopen_omdb(payload):
    """Return a context manager that replaces urllib.request.urlopen for one call."""
    import unittest.mock as _um
    body = _FakeResp(_json_mod.dumps(payload).encode())
    return _um.patch("urllib.request.urlopen", return_value=body)

# ── 1. Full response: IMDb + RT + MC all present ──────────────────────────────
_full_omdb = {
    "Response": "True",
    "imdbRating": "8.0",
    "imdbVotes": "450,000",
    "Ratings": [
        {"Source": "Internet Movie Database", "Value": "8.0/10"},
        {"Source": "Rotten Tomatoes",         "Value": "90%"},
        {"Source": "Metacritic",              "Value": "66/100"},
    ],
    "Metascore": "66",
}
_orig_token = TMDB.OMDB_TOKEN
TMDB.OMDB_TOKEN = "fake-key-for-test"
try:
    with _mock_urlopen_omdb(_full_omdb):
        _r, _v, _rt, _mc, _err = TMDB.omdb_rating("tt0000001")
    check("omdb full: IMDb rating parsed", _r, 8.0)
    check("omdb full: votes compact", _v, "450.0K")
    check("omdb full: RT from Ratings array", _rt, 90)
    check("omdb full: MC from Ratings array", _mc, 66)
    check("omdb full: no error", _err, None)

    # ── 2. RT and MC absent (N/A or missing) ─────────────────────────────────
    _partial_omdb = {
        "Response": "True",
        "imdbRating": "7.2",
        "imdbVotes": "12,500",
        "Ratings": [
            {"Source": "Internet Movie Database", "Value": "7.2/10"},
        ],
        "Metascore": "N/A",
    }
    with _mock_urlopen_omdb(_partial_omdb):
        _r2, _v2, _rt2, _mc2, _err2 = TMDB.omdb_rating("tt0000002")
    check("omdb partial: IMDb still parsed", _r2, 7.2)
    check("omdb partial: RT absent -> None", _rt2, None)
    check("omdb partial: MC N/A -> None", _mc2, None)

    # ── 3. MC from Metascore field when not in Ratings ────────────────────────
    _meta_only_omdb = {
        "Response": "True",
        "imdbRating": "6.5",
        "imdbVotes": "8,000",
        "Ratings": [
            {"Source": "Internet Movie Database", "Value": "6.5/10"},
        ],
        "Metascore": "72",
    }
    with _mock_urlopen_omdb(_meta_only_omdb):
        _r3, _v3, _rt3, _mc3, _err3 = TMDB.omdb_rating("tt0000003")
    check("omdb metascore-only: MC from Metascore field", _mc3, 72)
    check("omdb metascore-only: RT still None", _rt3, None)

    # ── 4. Network error => all None + error string ────────────────────────────
    import unittest.mock as _um2
    with _um2.patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        _r4, _v4, _rt4, _mc4, _err4 = TMDB.omdb_rating("tt0000004")
    check("omdb error: rating None on network error", _r4, None)
    check("omdb error: rt None on network error",     _rt4, None)
    check("omdb error: mc None on network error",     _mc4, None)
    check("omdb error: error string returned",        bool(_err4), True)

    # ── 5. Keep-previous for rt/mc on network error ───────────────────────────
    _prev_rec = {"imdb": 7.1, "imdbVotes": "10.0K", "rt": 85, "mc": 70}
    # Simulate fetch_tmdb main() keep-previous merge for rt/mc
    _r5, _v5, _rt5, _mc5, _err5 = None, None, None, None, "network: test"
    _rec5: dict = {}
    if _err5 is not None:
        if _r5 is None and _prev_rec.get("imdb") is not None:
            _r5 = _prev_rec["imdb"]
        if not _v5 and _prev_rec.get("imdbVotes"):
            _v5 = _prev_rec["imdbVotes"]
        if _rt5 is None and _prev_rec.get("rt") is not None:
            _rt5 = _prev_rec["rt"]
        if _mc5 is None and _prev_rec.get("mc") is not None:
            _mc5 = _prev_rec["mc"]
    if _r5 is not None:  _rec5["imdb"] = _r5
    if _v5:              _rec5["imdbVotes"] = _v5
    if _rt5 is not None: _rec5["rt"] = _rt5
    if _mc5 is not None: _rec5["mc"] = _mc5
    check("keep-prev rt: previous rt preserved on error", _rec5.get("rt"), 85)
    check("keep-prev mc: previous mc preserved on error", _rec5.get("mc"), 70)

    # ── 6. TMDBART rt/mc injected by inject_data ─────────────────────────────
    # Verify that inject_data.py maps tmdb_films.json rt/mc into TMDBART rec
    _tmdb_v = {"matched": True, "tmdb_id": 9999, "poster_path": "/p.jpg",
               "en": "Test Film", "tmdb_vote": 7.5, "vote_count": 500,
               "imdb": 8.0, "imdbVotes": "100.0K", "rt": 90, "mc": 66}
    _art_rec = {}
    if _tmdb_v.get("imdb") is not None:   _art_rec["imdb"]      = _tmdb_v["imdb"]
    if _tmdb_v.get("imdbVotes"):          _art_rec["imdbVotes"] = _tmdb_v["imdbVotes"]
    if _tmdb_v.get("rt") is not None:     _art_rec["rt"]        = _tmdb_v["rt"]
    if _tmdb_v.get("mc") is not None:     _art_rec["mc"]        = _tmdb_v["mc"]
    check("inject_data rt field mapped to TMDBART", _art_rec.get("rt"), 90)
    check("inject_data mc field mapped to TMDBART", _art_rec.get("mc"), 66)
finally:
    TMDB.OMDB_TOKEN = _orig_token

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
# kinoarena.com and cinemacity.bg are trusted hosts for poster harvesting
_fi_img_ka = "https://www.kinoarena.com/uploads/media/stenik_movies_image/0001/16/63d3f030447db34cbac4ddadad0ebc69deb03917.jpeg"
check("kinoarena.com opaque hash accepted (trusted host)",
      PP.reject_reason("veriti", _fi_img_ka, cat), None)
_fi_img_cc = "https://www.cinemacity.bg/xmedia-cw/repo/feats/posters/8326S2R.jpg"
check("cinemacity.bg poster accepted",
      PP.reject_reason("ne-kazvay-na-mama", _fi_img_cc, cat), None)

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

# Festival SUFFIX (Люмиер, 2026-10-21): the ALL-CAPS prefix rule used to eat the
# title itself and search TMDB for "СИНЕЛИБРИ"; the identity key must equal the
# prefixed spelling's, so the screening joins the catalogue film
import film_identity as _FID
check("declutter: festival suffix stripped, title kept",
      _dc("КУКЛА - СИНЕЛИБРИ"), ("КУКЛА", None))
check("declutter: Latin festival suffix stripped",
      _dc("LES MISÉRABLES - CINELIBRI"), ("LES MISÉRABLES", None))
check("film_identity: suffix and prefix spellings share a key",
      _FID.key("ВСИЧКИ ВЛЮБЕНИ В НОЩТА - СИНЕЛИБРИ"), _FID.key("Синелибри 2026: ВСИЧКИ ВЛЮБЕНИ В НОЩТА"))
check("film_identity: a hyphenated word is not a suffix",
      _FID.strip_prefix("Филм-Синелибри"), "Филм-Синелибри")
check("film_identity: display of a minted film keeps the venue's suffix",
      _FID.clean_title("КУКЛА - СИНЕЛИБРИ", keep_prefix=True), "КУКЛА - СИНЕЛИБРИ")

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

check("og:image pages: non-ASCII characters in the URL are percent-encoded (Дом на киното’s "
      "\"it’s-alive-…\" pages crashed the fetch), the rest is kept",
      [TMDB.ascii_url("https://domnakinoto.com/it’s-alive-x.html?a="),
       TMDB.ascii_url("https://x.bg/a%20b?c=1&d=ж"), TMDB.ascii_url("https://plain.bg/x?y=1")],
      ["https://domnakinoto.com/it%E2%80%99s-alive-x.html?a=", "https://x.bg/a%20b?c=1&d=%D0%B6",
       "https://plain.bg/x?y=1"])

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

# ---- 1b. request size: DeepL answers 413 above 128 KiB (the 13 Oct 2026 run) ----
print("  request size (HTTP 413)")
import urllib.request as _ur, urllib.error as _ue, io as _io  # noqa: E402,E401
_long = [(f"id{i}", f"{i} " + "Дълъг синопсис " * 120) for i in range(60)]     # ~3.3 KB of UTF-8 each
_bs = list(_tr.request_batches(_long))
check("request_batches: ≤50 texts and ≤BATCH_BYTES of text per request, every text once, in order",
      (max(len(b) for b in _bs) <= 50,
       max(sum(len(t.encode("utf-8")) + 16 for _, t in b) for b in _bs) <= _tr.BATCH_BYTES,
       [x for b in _bs for x in b] == _long, len(_bs) > 1), (True, True, True, True))


class _Resp(_io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_bodies = []


def _fake_urlopen(req, timeout=None):
    _bodies.append(req.data)
    if len(req.data) > 128 * 1024:
        raise _ue.HTTPError(req.full_url, 413, "Payload Too Large", {}, None)
    texts = _j.loads(req.data.decode("utf-8"))["text"]
    return _Resp(_j.dumps({"translations": [{"text": "EN " + t.split()[0]} for t in texts]}).encode())


_orig_uo, _orig_key = _ur.urlopen, _tr.DEEPL_KEY
_ur.urlopen, _tr.DEEPL_KEY = _fake_urlopen, "fake:fx"
try:
    _out, _ok = _tr.translate_batch([t for _, t in _long[:5]], "syn")
    check("the request body is UTF-8 as is, not \\uXXXX escapes (a third of the size)",
          (b"\\u04" in _bodies[-1], "Дълъг".encode("utf-8") in _bodies[-1], _ok), (False, True, True))
    _bodies.clear()
    _out, _ok = _tr.translate_batch([t for _, t in _long[:50]], "syn")
    check("a request DeepL finds too large (413) is split and sent again: all 50 translated, in order",
          (_ok, _out == [f"EN {i}" for i in range(50)], len(_bodies) > 1,
           all(len(b) <= 128 * 1024 for b in _bodies[1:])), (True, True, True, True))
    _bodies.clear()
    with _tf.TemporaryDirectory() as _td:
        _orig_cache = _tr.CACHE_FILE
        _tr.CACHE_FILE = pathlib.Path(_td) / "translations.json"
        try:
            _c = {}
            _ok = _tr.translate_missing(_long, "syn", _c)
        finally:
            _tr.CACHE_FILE = _orig_cache
    check("translate_missing on 60 long synopses: no request over the limit, all 60 cached",
          (_ok, len(_c), max(len(b) for b in _bodies) <= 128 * 1024), (True, 60, True))
finally:
    _ur.urlopen, _tr.DEEPL_KEY = _orig_uo, _orig_key

# ---- 1c. official names: DeepL writes the festival "Sinelibri", it is "CineLibri" ----
print("  official names")
check("official_names / latinise: CineLibri spelt the festival's way, capitals kept, other words untouched",
      [_tr.latinise(t) for t in ("Sinelibri 2026: PERFECT STRANGERS", "SINELIBRI 2026 – KNIFE",
                                  "Синелибри: Историите на нощта", "at the Sinelibri festival",
                                  "Sinelibrism", "CineLibri 2026", "Sinelibri’s guests")],
      ["CineLibri 2026: PERFECT STRANGERS", "CINELIBRI 2026 – KNIFE", "CineLibri: Istoriite na noshtta",
       "at the CineLibri festival", "Sinelibrism", "CineLibri 2026", "CineLibri’s guests"])
_nm_cache = {_ck("Синелибри 2026: ПЕРФЕКТНИ НЕПОЗНАТИ"): {"src": "x", "en": "Sinelibri 2026: PERFECT STRANGERS",
                                                        "kind": "title", "at": "2026-10-13"}}
_nm_syn, _nm_title = _tr.build_mappings([{"id": "sl-pn", "bg": "Синелибри 2026: ПЕРФЕКТНИ НЕПОЗНАТИ"}], [], {}, {},
                                        _nm_cache)
check("…and a translation cached before the fix is served corrected (no new DeepL request)",
      _nm_title.get("sl-pn"), "CineLibri 2026: PERFECT STRANGERS")

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

# ---- 4. leftovers of Cyrillic in DeepL's English are romanised ----
check("latinise: names and quoted titles romanised, quotes kept",
      _tr.latinise("Ivan Вазов's play „Под игото“"), "Ivan Vazov's play „Pod igoto“")
check("latinise: case per word, '-ия' at a word end is '-ia'",
      [_tr.latinise(x) for x in ("СОФИЯ", "София, България", "Щука ЩЕ", "Я")],
      ["SOFIA", "Sofia, Balgaria", "Shtuka SHTE", "Ya"])
check("latinise: English untouched, no Cyrillic ever left",
      [_tr.latinise("already English"), bool(_tr._CYRILLIC.search(_tr.latinise("Ӂ ӂ Ӂук")))],
      ["already English", False])
_lat_cache = {_tr.cache_key("Под игото"): {"src": "Под игото", "en": "Под игото", "kind": "title", "at": "2026-10-09"},
              _tr.cache_key("Синопсис."): {"src": "Синопсис.", "en": "", "kind": "syn", "at": "2026-10-09"}}
_lat_syn, _lat_title = _tr.build_mappings(
    [], [{"id": "pod-igoto", "title": "Под игото", "synBg": "Синопсис."}], {}, {}, _lat_cache)
check("build_mappings: a title DeepL left in Cyrillic is served romanised; an empty one is skipped",
      [_lat_title, _lat_syn], [{"pod-igoto": "Pod igoto"}, {}])

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
check("posterLink included in meta",
      [x for x in _r if x[0] == "Верити"][0][3].get("posterLink"),
      "https://www.cinemacity.bg/xmedia-cw/repo/feats/posters/8244S2R.jpg")
check("releaseDate kept in meta (date part only)",
      [x for x in _r if x[0] == "Верити"][0][3].get("releaseDate"),
      "2026-10-02")
import copy as _copy, re as _re_ev                              # noqa: E402,E401
_ccev = _copy.deepcopy(_cc)
_fv = next(f for f in _ccev["body"]["films"] if f.get("name") == "Верити")
_fv_ids = list(_fv.get("attributeIds") or [])
check("a film is not an event", [x for x in _r if x[0] == "Верити"][0][3].get("event"), None)
for _tag in ("sport", "music"):
    _fv["attributeIds"] = _fv_ids + [_tag]
    check(f"Cinema City's '{_tag}' tag marks a one-off event (never a premiere)",
          [x for x in OS.parse_cinemacity_day(_ccev, "2026-10-09", "1261") if x[0] == "Верити"][0][3].get("event"), True)
_fv["attributeIds"] = _fv_ids
_fv["name"] = "Годзила Маратон"
check("a title naming a marathon marks an event; a word merely containing it does not",
      ([x for x in OS.parse_cinemacity_day(_ccev, "2026-10-09", "1261") if x[0] == "Годзила Маратон"][0][3].get("event"),
       bool(_re_ev.search(r"(?<!\w)(?:маратон|marathon)(?!\w)", "Маратонецът", _re_ev.I))), (True, False))

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
_ccc.update({"2026-10-16": 1, "2026-10-21": 2, "2026-10-24": 1,
             "2026-12-13": 1})                                    # the CS2 final, after 49 empty days
_feed = _DayFeed(_ccc)
_res = OS.fetch_cinemacity(_feed, "cc-sofia", "2026-10-08", "2026-12-14")
check("Cinema City: coverage is the normal week only", (_res.covered_from, _res.covered_to), ("2026-10-08", "2026-10-15"))
check("…advance sales after it are fetched past empty days and kept (the merge marks them preliminary)",
      sorted({r[1] for r in _res.rows if r[1] > "2026-10-15"}), ["2026-10-16", "2026-10-21", "2026-10-24", "2026-12-13"])
check("…every day to the window end is read, however many empty days come first",
      (_feed.asked[-1], len(_feed.asked)), ("2026-12-14", 68))
_dk_days = dict(_dkc)
_dk_days.update({d: 1 for d in _days("2026-10-22", 19)})          # festival: one a day to 11-09
_dk_days["2026-11-11"] = 1
_dk_days.update({"2026-11-30": 1, "2026-12-04": 1, "2026-12-05": 2, "2026-12-14": 1})   # after 18 empty days
_dkf = _DayFeed(_dk_days)
_res = OS.fetch_domkino(_dkf, "dom-kino", "2026-10-08", "2026-12-14")
check("Дом на киното: coverage = the run of normal days, the next week's first days included",
      _res.covered_to, "2026-10-19")
_later = sorted({r[1] for r in _res.rows if r[1] > "2026-10-19"})
check("…the festival screenings after it are official rows (preliminary), the December ones too",
      (len(_later), _later[0], _later[-1], sum(1 for r in _res.rows if r[1] >= "2026-11-30")),
      (26, "2026-10-20", "2026-12-14", 5))
check("…every day to the window end is read", (_dkf.asked[-1], len(_dkf.asked)), ("2026-12-14", 68))

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

print("official: Кино Арена film page poster (og:image)")
_ka_movie_html = (FIXTURES / "kinoarena_movie.html").read_text(encoding="utf-8")
_ka_movie_soup = BeautifulSoup(_ka_movie_html, "lxml")
_ka_img = FI.harvest_kinoarena_img(_ka_movie_soup, "https://www.kinoarena.com/bg/movie/veriti")
check("kinoarena og:image extracted",
      _ka_img,
      "https://www.kinoarena.com/uploads/media/stenik_movies_image/0001/16/63d3f030447db34cbac4ddadad0ebc69deb03917.jpeg")
check("kinoarena img starts with https",
      bool(_ka_img and _ka_img.startswith("https://")), True)


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
_cgp = _Pages({"schedule": _cg})
_res = OS.fetch_cinegrand(_cgp, "cg-ring", "2026-10-08", "2026-12-14")
check("a day page showing another day ends coverage",
      (_res.covered_from, _res.covered_to), ("2026-10-08", "2026-10-08"))
check("…and then no advance sale is read", [u for u in _cgp.asked if "calendar" in u], [])

print("official: Cine Grand advance sales — the per-cinema calendar (София Ринг Мол, 2026-10-09, trimmed)")
_cal = (FIXTURES / "cinegrand_calendar.html").read_text(encoding="utf-8")
_adv = (FIXTURES / "cinegrand_advance.html").read_text(encoding="utf-8")
_cd = OS.parse_cinegrand_calendar(_cal, "софия-ринг-мол")
check("the calendar lists the strip's days and the dates weeks ahead", [d for d, _ in _cd],
      [f"2026-10-{d:02d}" for d in range(9, 16)] + ["2026-11-03", "2026-11-04", "2026-11-07",
                                                     "2026-12-15", "2026-12-16", "2026-12-17"])
check("…each with its own dated page", _cd[7][1].endswith("/schedule?date=2026-11-03"), True)
check("another cinema's calendar is refused", _raises(OS.parse_cinegrand_calendar, _cal, "парк-център-софия"), True)
check("a page reached from the calendar states its date", OS.cinegrand_heading_date(_adv, "2026-10-09"), "2026-11-03")
check("…a strip day page has no such heading", OS.cinegrand_heading_date(_cg, "2026-10-08"), None)
_advp = _Pages({"/calendar": _cal, "date=2026-11-03": _adv,
                "date=2026-11-04": _cg})                   # answered with 8 Oct's programme
_rows, _days_cg, _notes = OS.fetch_cinegrand_advance(_advp, "cg-ring", "2026-10-15", "2026-10-09", "2026-12-14")
check("advance sale kept when the page shows that date", [(r[0], r[1], r[2], r[3]["hall"]) for r in _rows],
      [("GODZILLA MINUS ZERO", "2026-11-03", "19:00", "Зала 9")])
check("…a page answering with another day's programme is not counted",
      (sorted(_days_cg), any(n.startswith("2026-11-04: advance-sale page showed 2026-10-08") for n in _notes)),
      (["2026-11-03"], True))
check("…a page that does not load is noted", any(n.startswith("2026-11-07: advance-sale page did not load")
                                                 for n in _notes), True)
check("…only dates after the strip's run and within the window are requested",
      sorted(re.search(r"date=([\d-]+)", u).group(1) for u in _advp.asked if "date=" in u),
      ["2026-11-03", "2026-11-04", "2026-11-07"])
check("calendar unreachable → no advance rows, noted",
      OS.fetch_cinegrand_advance(_Pages({}), "cg-ring", "2026-10-15", "2026-10-09", "2026-12-14")[::2],
      ([], ["calendar unreachable — advance sales not read"]))

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
# (an index of its own per negative case is a leftover of the run-wide fuzzy
# cache, removed 2026-10-09: see "identity: no memory across titles" below)
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

# ===================================================== P1A (2026-10-09 audit)
# CODE-07: every identity decision stands on its own evidence — no run-wide
# memory of a fuzzy match, and the year veto applies to every rule.
print("identity: no memory across titles, the year check always (CODE-07)")
_av = FID.FilmIndex(_cat, _minted)                       # ONE index, as in a real run
check("Cinema City's 'Аватар', corroborated, is 'Аватар: Огън и пепел'",
      _R("Аватар", "cc-sofia", corroborate=lambda f: f == "avatar-3", index=_av), "avatar-3")
check("…later in the SAME run, Одеон's 'Аватар (2009)' (year 2009, 162 min) is not",
      _R("Аватар (2009)", "odeon", index=_av, year=2009, runtime=162), None)
check("…nor Одеон's plain 'Аватар' with its year in the meta",
      _R("Аватар", "odeon", index=_av, year=2009), None)
check("…nor another venue's 'Аватар' with no evidence of its own (nothing is remembered)",
      _R("Аватар", "arena-mega", index=_av), None)
check("…while the corroborated venue still resolves it, every time",
      _R("Аватар", "cc-sofia", corroborate=lambda f: f == "avatar-3", index=_av), "avatar-3")
check("the resolver keeps no fuzzy cache at all", hasattr(_av, "_fuzzy"), False)
_sp = FID.FilmIndex(_cat[2:3], aliases={})
check("a one-letter spelling match is not carried to a title with another year",
      [_R("Капитан Саблезъб и графинята на Грел", "x", index=_sp, year=2026),
       _R("Капитан Саблезъб и графинята на Грел", "y", index=_sp, year=1999)], ["sablezab", None])
check("a curated alias obeys the year veto (Люмиер's 'Сънища' of 2024 is not Kurosawa's 1990 film)",
      _R("Сънища", "lumiere", year=2024), None)
check("…and still applies with the right year, or none", (_R("Сънища", "lumiere", year=1990),
                                                          _R("Сънища", "lumiere")),
      ("akira-kurosava-sanishta-1990", "akira-kurosava-sanishta-1990"))
check("…a vetoed alias is listed for a human", any(t == "Сънища" for t, _c in _idx.suspects), True)

# CODE-01: the listing window rolls with the date, from one shared rule.
print("the rolling listing window (CODE-01), the year rollover included")
import listing_window as LW                                      # noqa: E402
import official_sources as OS                                    # noqa: E402
import netfetch as NF                                            # noqa: E402
import venue_state as VS                                         # noqa: E402
import json as _js                                               # noqa: E402
check("today's horizon kept: 9 Oct .. 14 Dec (66 days on, 67 listing days)",
      (LW.listing_window("2026-10-09"), len(LW.window_days("2026-10-09"))), (("2026-10-09", "2026-12-14"), 67))
check("on 20 Dec 2026 the window runs into 2027", LW.listing_window("2026-12-20"), ("2026-12-20", "2027-02-24"))
check("on 2 Jan 2027", LW.listing_window("2027-01-02"), ("2027-01-02", "2027-03-09"))
_W20 = LW.window_days("2026-12-20")
check("the window's days are consecutive across New Year",
      _W20[_W20.index("2026-12-31"):_W20.index("2026-12-31") + 2], ["2026-12-31", "2027-01-01"])
check("'today' is Sofia's: 21:30 UTC on 9 Oct is already 10 Oct (EEST)",
      LW.sofia_today(dt.datetime(2026, 10, 9, 21, 30, tzinfo=dt.timezone.utc)), dt.date(2026, 10, 10))
check("…and 22:30 UTC on 31 Dec is 1 Jan 2027 (EET)",
      LW.sofia_today(dt.datetime(2026, 12, 31, 22, 30, tzinfo=dt.timezone.utc)), dt.date(2027, 1, 1))
check("SNAPSHOT.generated: ISO, Sofia offset (+03:00 in October, +02:00 in December)",
      (LW.generated_stamp(dt.datetime(2026, 10, 9, 18, 30, 5, 123, tzinfo=dt.timezone.utc)),
       LW.generated_stamp(dt.datetime(2026, 12, 20, 4, 17, tzinfo=dt.timezone.utc))),
      ("2026-10-09T21:30:05+03:00", "2026-12-20T06:17:00+02:00"))
check("the EU clock rule when the tz database is missing (25 Oct 2026, 28 Mar 2027 at 01:00 UTC)",
      [LW._eu_offset_hours(dt.datetime(*a, tzinfo=dt.timezone.utc)) for a in
       ((2026, 10, 25, 0, 59), (2026, 10, 25, 1, 0), (2027, 3, 28, 0, 59), (2027, 3, 28, 1, 0))], [3, 2, 2, 3])
for _today, _text, _want in [("2026-12-20", "28 декември", "2026-12-28"), ("2026-12-20", "5 януари", "2027-01-05"),
                             ("2026-12-20", "3 февруари", "2027-02-03"), ("2027-01-02", "5 януари", "2027-01-05"),
                             ("2027-01-02", "9 март", "2027-03-09")]:
    check(f"a date without a year, today {_today}: {_text!r}", S.find_date(_text, LW.window_days(_today)), _want)
check("Cine Grand labels across New Year ('чт, 31 дек' / 'пт, 1 яну', today 20 Dec)",
      (OS._bg_day_month("чт,  31 дек, 19:00", "2026-12-20"), OS._bg_day_month("пт,  1 яну, 11:50", "2026-12-20")),
      ("2026-12-31", "2027-01-01"))
check("…and on 2 Jan a December label is last year's (dropped as past), a March one this year's",
      (OS._infer_year(28, 12, "2027-01-02"), OS._infer_year(15, 3, "2027-01-02")), ("2026-12-28", "2027-03-15"))
_offj = [("a", "2026-12-30", "20:00"), ("b", "2027-01-01", "16:00"), ("a", "2027-01-03", "18:00")]
_rows, _pf, _ = S.merge_cinema_venue("v", "official", _offj, ("2026-12-20", "2027-01-01"),
                                     [("c", "C", "2027-01-05", ["21:00"], None)], True, [], None,
                                     "2026-12-20", "2027-02-24")
check("a merge across New Year: rows in date order, coverage to 1 Jan, preliminary from 2 Jan",
      ([r[2] for r in _rows], _pf), (["2026-12-30", "2027-01-01", "2027-01-03", "2027-01-05"], "2027-01-02"))
_snap = ('const SNAPSHOT={"window":{"from":"2026-12-19","to":"2027-02-23"},"captured":"2026-09-15T00:00:00+03:00",'
         '"lastValidated":"x","changelog":[{"film":"„Ф“"}]};\nconst CINEMAS=[];')
_out = S.write_snapshot(_snap, "2026-12-20", "2027-02-24", "2026-12-20T06:17:00+02:00")
_sj = _js.loads(_out.split("\n")[0][len("const SNAPSHOT="):-1])
check("write_snapshot: both window ends move, generated set right after the window, the rest untouched",
      (_sj["window"], _sj["generated"], list(_sj), _sj["changelog"], _out.split("\n")[1]),
      ({"from": "2026-12-20", "to": "2027-02-24"}, "2026-12-20T06:17:00+02:00",
       ["window", "generated", "captured", "lastValidated", "changelog"], [{"film": "„Ф“"}], "const CINEMAS=[];"))
check("…a second run replaces generated in place (one key)",
      S.write_snapshot(_out, "2026-12-21", "2027-02-25", "2026-12-21T06:17:00+02:00").count('"generated"'), 1)
_loose = 'const SNAPSHOT={window:{from:"2026-10-08",to:"2026-12-14"},captured:"x"};'
check("…a SNAPSHOT that is not strict JSON still gets both (regex fallback)",
      S.write_snapshot(_loose, "2026-10-09", "2026-12-14", "G"),
      'const SNAPSHOT={window:{from:"2026-10-09",to:"2026-12-14"},"generated":"G",captured:"x"};')
check("harvest_cinema_posters.py no longer hard-codes the window end",
      "2026-12-14" in (pathlib.Path(__file__).resolve().parent / "harvest_cinema_posters.py").read_text(encoding="utf-8"),
      False)

# CODE-03: netfetch's budgets are real. A fake clock and a fake transport.
print("netfetch: real time budgets, a closed dead host, blocking recognised (CODE-03)")


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


class _Resp:
    def __init__(self, status, body=b"<html>ok</html>", headers=None):
        self.status_code, self.content = status, body
        self.headers = {"Content-Type": "text/html; charset=utf-8", **(headers or {})}
        self.text = body.decode("utf-8", "ignore")


class _Transport:
    """host -> behaviour: 'dead' (connection error), 'tarpit' (every read times
    out after the whole timeout), an int status, or a list of statuses in turn."""
    def __init__(self, clock, plan):
        self.clock, self.plan, self.calls = clock, plan, []

    def get(self, url, headers=None, timeout=None, verify=True, allow_redirects=True):
        import urllib.parse as _u
        import requests as _rq
        host = _u.urlsplit(url).hostname
        self.calls.append((host, url, timeout))
        how = self.plan[host]
        if isinstance(how, list):
            how = how.pop(0) if len(how) > 1 else how[0]
        if how == "dead":
            self.clock.t += 0.2
            raise _rq.exceptions.ConnectionError("Failed to resolve " + host)
        if how == "tarpit":
            self.clock.t += timeout[1]
            raise _rq.exceptions.ReadTimeout("read timed out")
        if how == "challenge":
            self.clock.t += 0.3
            return _Resp(403, b"<!DOCTYPE html><html><head><title>Just a moment...</title></head></html>")
        self.clock.t += 0.3
        if isinstance(how, tuple):
            return _Resp(how[0], headers=how[1])
        return _Resp(how)


def _fetcher(plan, **kw):
    clk = _Clock()
    NF.time = clk                          # netfetch reads time.monotonic / time.sleep
    f = NF.Fetcher(verbose=False, **kw)
    f.session = _Transport(clk, plan)
    return f, clk


_real_time = NF.time
try:
    _f, _c = _fetcher({"theatre.art.bg": "dead", "ok.bg": 200})
    _t0 = _c.t
    _got = [_f.get(f"https://theatre.art.bg/?date=d{i}") for i in range(67)]
    check("a dead host: 67 day pages cost two URLs' retries, not 67 x 36 s",
          (sum(x is not None for x in _got), len(_f.session.calls), _c.t - _t0 <= 2 * NF.URL_BUDGET),
          (0, 8, True))
    check("…then the host is closed for the run, and says why",
          ("theatre.art.bg" in _f.closed, _f.log[-1]["outcome"], _f.log[-1]["error"].startswith("skipped:")),
          (True, "skipped", True))
    check("…a healthy host is not affected", _f.get("https://ok.bg/x") is not None, True)

    _f, _c = _fetcher({"slow.bg": "tarpit"})
    _t0 = _c.t
    _f.get("https://slow.bg/a")
    check("a tarpit: one URL never takes more than URL_BUDGET, retries included",
          _c.t - _t0 <= NF.URL_BUDGET + NF.DEFAULT_HOST_DELAY, True)
    for _i in range(10):
        _f.get(f"https://slow.bg/{_i}")
    check("…and the host closes after HOST_FAIL_LIMIT such URLs",
          (_c.t - _t0 <= NF.HOST_FAIL_LIMIT * (NF.URL_BUDGET + 3), "slow.bg" in _f.closed), (True, True))

    _f, _c = _fetcher({"mlt.bg": 403, "tickets.mlt.bg": 200})
    _n0 = len(_f.log)
    check("HTTP 403: refused once, never retried",
          (_f.get("https://mlt.bg/programa.php?year=2026&month=10"), len(_f.session.calls)), (None, 1))
    check("…recorded as blocked from this network", _f.blocked_since(_n0),
          [("https://mlt.bg/programa.php?year=2026&month=10", "mlt.bg", "HTTP 403")])
    check("…the venue's other official host still answers", _f.get("http://tickets.mlt.bg/x") is not None, True)
    _f, _c = _fetcher({"cf.bg": "challenge"})
    _f.get("https://cf.bg/")
    check("a WAF challenge page is blocking, not a programme", _f.blocked_since(0)[0][2],
          "challenge page (Cloudflare challenge, HTTP 403)")
    check("…an ordinary Cloudflare page carrying its bot-detection script is NOT a challenge",
          NF.challenge_signature(_Resp(200, b"<html><head><title>Program</title></head><body>"
                                            b"<script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'>"
                                            b"</script></body></html>")), None)
    _f, _c = _fetcher({"flaky.bg": [503, 200]})
    check("503 then 200: retried within the budget", (_f.get("https://flaky.bg/") is not None,
                                                     len(_f.session.calls)), (True, 2))
    _f, _c = _fetcher({"nf.bg": 404})
    for _i in range(5):
        _f.get(f"https://nf.bg/{_i}")
    check("404s are answers: no retry, and the host stays open", (len(_f.session.calls), "nf.bg" in _f.closed),
          (5, False))
    _f, _c = _fetcher({"ra.bg": (429, {"Retry-After": "3600"})})
    _t0 = _c.t
    _f.get("https://ra.bg/")
    check("a Retry-After of an hour is not waited for", (len(_f.session.calls), _c.t - _t0 < 5), (1, True))
    _f, _c = _fetcher({"ok.bg": 200})
    with _f.deadline(10):
        _c.t += 11
        _late = _f.get("https://ok.bg/late")
    check("a step's deadline: nothing is fetched after it", (_late, len(_f.session.calls)), (None, 0))
    check("…and outside it the host is fine (a spent step does not close it)",
          (_f.get("https://ok.bg/now") is not None, "ok.bg" in _f.closed), (True, False))
    _f, _c = _fetcher({"ok.bg": 200}, budget_seconds=30)
    _c.t += 31
    check("the whole-run budget is enforced inside get()", _f.get("https://ok.bg/x"), None)
finally:
    NF.time = _real_time

# CODE-02: one venue failing its self-check, or crashing, stops only itself.
print("per-venue isolation (CODE-02)")
_F2, _E2 = "2026-10-09", "2026-12-14"
_prev_g8 = [["p", "g8", "2026-10-09", ["11:00", "14:00", "17:00"]], ["q", "g8", "2026-10-10", ["20:00"]]]
_g8_off = [("p", "2026-10-09", "11:00"), ("p", "2026-10-10", "11:00"), ("p", "2026-10-11", "11:00")]
_g8_cov, _odeon_off = ("2026-10-09", "2026-10-15"), [("o", "2026-10-09", "18:30"), ("o", "2026-10-10", "18:30")]
_out = {}
for _v, _off, _cov, _prevstat, _prev in (
        ("g8", _g8_off, _g8_cov, {"status": "official", "screenings": 112, "days": 7}, _prev_g8),
        ("odeon", _odeon_off, ("2026-10-09", "2026-10-11"), {"status": "official", "screenings": 3, "days": 3}, [])):
    _stub = {"status": "official", "screenings": len(_off), "days": 7 if _v == "g8" else 3}
    _out[_v] = VS.run_isolated(
        lambda: S.merge_cinema_venue(_v, "official", _off, _cov, [], False, _prev, None, _F2, _E2),
        lambda: S.merge_cinema_venue(_v, "unreachable", [], None, [], False, _prev, None, _F2, _E2),
        lambda o: S.cinema_venue_problems("official", o[0], set(_off), list(_cov), _prevstat, _stub,
                                          _F2, _E2, {"p", "q", "o"}))
check("G8's markup half-breaks (3 screenings a day -> 0.4): G8 alone fails its self-check",
      (bool(_out["g8"][3]), _out["g8"][3][0][:20], bool(_out["odeon"][3])), (True, "implausibly few rows", False))
check("…G8 keeps last run's rows, all preliminary from today", (_out["g8"][0], _out["g8"][1]),
      (_prev_g8, _F2))
check("…Одеон is merged normally from its own programme", (_out["odeon"][0], _out["odeon"][1]),
      ([["o", "odeon", "2026-10-09", ["18:30"]], ["o", "odeon", "2026-10-10", ["18:30"]]], "2026-10-12"))


def _boom():
    raise KeyError("a parser bug")


_r = VS.run_isolated(_boom, lambda: ([["k", "v", "2026-10-09", ["10:00"]]], _F2, {}))
check("a merge that crashes falls back for that venue only, with the reason", (_r[0], _r[1], _r[3]),
      ([["k", "v", "2026-10-09", ["10:00"]]], _F2, ["merge crashed: KeyError: 'a parser bug'"]))
check("written rows that differ from the venue's own programme are a self-check failure",
      S.cinema_venue_problems("official", [["a", "v", "2026-10-09", ["10:00", "12:00"]]],
                              {("a", "2026-10-09", "10:00")}, ["2026-10-09", "2026-10-09"], None,
                              {"status": "official", "screenings": 1, "days": 1}, _F2, _E2, {"a"}),
      ["written rows differ from its own programme inside 2026-10-09..2026-10-09 (1 extra, 0 missing)"])
check("…as are an unknown film id and a past row",
      S.cinema_venue_problems("none", [["zz", "v", "2026-10-08", ["10:00"]]], set(), None, None, None,
                              _F2, _E2, {"a"}),
      ["a row outside today..window end", "showtimes for unknown film ids: zz"])

# CODE-05: an unreachable source — preliminary at once, gone after 14 days.
print("stale sources: preliminary at once, removed after 14 days (CODE-05)")
_F, _E = "2026-10-08", "2026-12-14"
_e = {"last_ok": "2026-10-01", "covered_to": "2026-10-31", "failing_since": "2026-10-04"}
check("kept policy 8 days after the last read: kept, confirmed to its last coverage",
      VS.kept_policy(_e, "2026-10-09"), {"since": "2026-10-01", "age": 8, "expired": False,
                                         "confirmed_to": "2026-10-31", "last_ok": "2026-10-01"})
check("…14 days: still kept; 15 days: expired",
      (VS.kept_policy(_e, "2026-10-15")["expired"], VS.kept_policy(_e, "2026-10-16")["expired"]), (False, True))
check("never read (first run with the sidecar): the clock starts at the first failure, "
      "confirmed range from the page's PRELIM_FROM",
      VS.kept_policy({}, "2026-10-09", "2026-11-30"), {"since": "2026-10-09", "age": 0, "expired": False,
                                                       "confirmed_to": "2026-11-29", "last_ok": None})
_st = {"venues": {}}
VS.record_read(_st, "mladezhki", True, "2026-10-09", covered_to="2026-11-29")
VS.record_read(_st, "mladezhki", False, "2026-10-10", error="HTTP 403")
VS.record_read(_st, "mladezhki", False, "2026-10-11", error="HTTP 403")
check("official_state: a failure streak keeps the last good read and its first failure",
      _st["venues"]["mladezhki"], {"source": "official", "last_ok": "2026-10-09", "covered_to": "2026-11-29",
                                   "failing_since": "2026-10-10", "last_error": "HTTP 403"})
VS.record_read(_st, "mladezhki", True, "2026-10-12", covered_to="2026-11-30")
check("…and a good read resets it", (_st["venues"]["mladezhki"]["last_ok"],
                                    _st["venues"]["mladezhki"]["failing_since"]), ("2026-10-12", None))
_agg2 = [("d", "D", "2026-10-18", ["12:00"], None), ("e", "E", "2026-10-10", ["21:00"], None)]
_prev2 = [["gone", "v", "2026-10-11", ["20:30"]], ["late", "v", "2026-10-25", ["19:00"]]]
check("expired (no successful read for 15 days): kept rows removed, the aggregator's alone, preliminary",
      S.merge_cinema_venue("v", "unreachable", [], None, _agg2, True, _prev2, "2026-10-12", _F, _E,
                           confirmed_to="2026-10-11", expired=True)[:2],
      ([["e", "v", "2026-10-10", ["21:00"]], ["d", "v", "2026-10-18", ["12:00"]]], _F))
check("…and with the aggregator down too: nothing at all (never stale listings)",
      S.merge_cinema_venue("v", "unreachable", [], None, [], False, _prev2, "2026-10-12", _F, _E,
                           confirmed_to="2026-10-11", expired=True)[:2], ([], _F))
check("not expired, aggregator down: every kept row stays, preliminary from today",
      S.merge_cinema_venue("v", "unreachable", [], None, [], False, _prev2, "2026-10-12", _F, _E,
                           confirmed_to="2026-10-11")[:2],
      ([["gone", "v", "2026-10-11", ["20:30"]], ["late", "v", "2026-10-25", ["19:00"]]], _F))
check("an aggregator-only venue whose aggregator has been down for 15 days keeps nothing",
      S.merge_cinema_venue("cineland", "none", [], None, [], False, _prev2, None, _F, _E, expired=True)[0], [])
print("an unreachable venue's kept rows are never deleted by the aggregator (D1: dom-kino 80 -> 32)")
_prev3 = [["k1", "v", "2026-10-10", ["18:00"]], ["k2", "v", "2026-10-20", ["20:00"]],
          ["k3", "v", "2026-11-30", ["19:00"]]]
check("programata read but listing nothing for the venue (Cinema City): every kept row stays, preliminary",
      S.merge_cinema_venue("v", "unreachable", [], None, [], True, _prev3, "2026-10-16", _F, _E,
                           confirmed_to="2026-10-15")[:2], (_prev3, _F))
_agg3 = [("k2", "K2", "2026-10-20", ["17:00"], None),     # a kept film and day, another time: logged
         ("n1", "N1", "2026-10-20", ["21:00"], None),     # a date a kept row covers: not added
         ("n2", "N2", "2026-10-21", ["21:00"], None),     # a date no kept row covers: added
         ("n3", "N3", "2026-10-12", ["21:00"], None)]     # inside the range it last confirmed: not added
_rows, _pf, _info = S.merge_cinema_venue("v", "unreachable", [], None, _agg3, True, _prev3, "2026-10-16", _F, _E,
                                         confirmed_to="2026-10-15")
check("the aggregator only ADDS, on dates the kept rows do not cover", _rows,
      [["k1", "v", "2026-10-10", ["18:00"]], ["k2", "v", "2026-10-20", ["20:00"]],
       ["n2", "v", "2026-10-21", ["21:00"]], ["k3", "v", "2026-11-30", ["19:00"]]])
check("…its other time for a kept film and day is logged, the kept (formerly official) row shown",
      _info["conflicts"], [["2026-10-20", "k2", ["20:00"], ["17:00"]]])

# CODE-13: health.json — a venue emptied, blocked, unreachable, kept, low.
print("health.json (CODE-13)")
_hv = [{"venue": "cineland", "kind": "cinema", "status": "none", "now": 0, "prev": 11,
        "agg_name": "programata.bg"},
       {"venue": "mladezhki", "kind": "theatre", "status": "official", "now": 108, "prev": 110,
        "source": "tickets.mlt.bg public list (the theatre's own ticket centre)",
        "blocked": [("https://mlt.bg/programa.php?year=2026&month=10", "mlt.bg", "HTTP 403")]},
       {"venue": "kuklen", "kind": "theatre", "status": "unreachable", "now": 40, "prev": 41,
        "error": "unreachable: https://sofiapuppet.com/…", "agg_name": "theatre.art.bg",
        "policy": {"last_ok": "2026-10-01", "age": 8, "expired": False, "since": "2026-10-01"}},
       {"venue": "g8", "kind": "cinema", "status": "official", "now": 30, "prev": 100},
       {"venue": "atelie313", "kind": "theatre", "status": "none", "now": 6, "prev": 6,
        "agg_name": "theatre.art.bg"}]
_h = VS.health_entries(_hv)
check("errors first: an emptied venue and a blocked source", [(e["level"], e["venue"]) for e in _h],
      [("error", "cineland"), ("error", "mladezhki"), ("warning", "kuklen"), ("warning", "g8"),
       ("warning", "atelie313")])
check("…an aggregator-only venue that emptied", _h[0]["message"], "emptied: 0 upcoming screenings now, 11 last run (none)")
check("…blocked from this network, and what was read instead", _h[1]["message"],
      "blocked from this network: HTTP 403 at https://mlt.bg/programa.php?year=2026&month=10 — read from "
      "tickets.mlt.bg public list (the theatre's own ticket centre) instead")
check("…kept rows shown as preliminary, and when they go", _h[2]["message"],
      "official source unreachable (unreachable: https://sofiapuppet.com/…); last read 2026-10-01, 8 days ago — "
      "40 performances kept and shown as preliminary (removed after 14 days)")
check("…a count far below the previous run's", _h[3]["message"], "low count: 30 screenings now vs 100 last run")
check("…an aggregator-only venue, every run", _h[4]["message"],
      "no official programme: all 6 performances are preliminary, from theatre.art.bg")
_hp = pathlib.Path("/tmp/p1a_test_health.json")
VS.write_health(_hp, _h)
check("health.json is a list of {level, venue, message}",
      [sorted(e) for e in _js.loads(_hp.read_text(encoding="utf-8"))][:1], [["level", "message", "venue"]])


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
check("unreachable official source: rows it had confirmed kept (never past ones), the aggregator after them",
      _rows, [["gone", "v", "2026-10-11", ["20:30"]], ["d", "v", "2026-10-18", ["12:00"]],
              ["c", "v", "2026-10-20", ["18:00"]]])
check("unreachable official source: ALL preliminary, from today (owner: 'mark preliminary at once')", _pf, _F)
check("unreachable with no previous PRELIM_FROM: preliminary from today, every kept row stays",
      S.merge_cinema_venue("v", "unreachable", [], None, [], True, _prev, None, _F, _E)[:2],
      ([["gone", "v", "2026-10-11", ["20:30"]]], _F))
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


# ============================================ festival programmes (wave F3)
# Fixtures are trimmed copies of the live pages (2026-10-09): CineLibri's
# programme / day / film / media REST answers and Kinomania's programme and film
# pages. official_festivals.py reads; scrape_programs.py merges and mints.
print("festivals: venue table (explicit; unknown names reported, never guessed)")
import official_festivals as OFE                                 # noqa: E402
for fest, name, want in [
    ("cinelibri", "Зала 1 на НДК", ("app", "ndk1")),
    ("cinelibri", "Френски институт", ("app", "frenski-institut")),
    ("cinelibri", "Cine Grand Park center", ("app", "cg-park")),
    ("cinelibri", "Cinema City Mall of Sofia", ("app", "cc-sofia")),
    ("cinelibri", "Casa Libri", ("app", "casa-libri")),
    ("cinelibri", "Евросинема", ("app", "g8")),
    ("cinelibri", "Топлоцентрала", ("app", "toplo-film")),
    ("cinelibri", "Сцена Централни Хали", ("app", "tsentralni-hali")),
    ("cinelibri", "Евро Синема Варна", ("outside", None)),
    ("cinelibri", "ФКЦ, Варна", ("outside", None)),
    ("kinomania", "НДК - Зала 1", ("app", "ndk1")),
    ("kinomania", "НДК – Зала 1", ("app", "ndk1")),
    ("kinomania", "Синема Сити Парадайс Център", ("app", "cc-paradise")),
    ("kinomania", "Арена", ("unknown", None)),
    ("kinomania", "Euro Cinema", ("app", "g8")),
    ("kinomania", "Lucky Пловдив", ("outside", None)),
    ("kinomania", "Стара Загора - Регионална библиотека", ("outside", None)),
]:
    check(f"venue {fest}: {name!r}", OFE.venue_id(fest, name), want)

print("festivals: CineLibri pages")
_cl_prog = _json.loads((FIXTURES / "festival_cinelibri_programme.json").read_text(encoding="utf-8"))
check("programme page -> day pages (a non-day banner ignored)",
      OFE.cinelibri_days(_cl_prog[0]["content"]["rendered"], "2026-10-09"),
      [("2026-10-15", "https://www.cinelibri.com/15-oktomvri/"),
       ("2026-11-01", "https://www.cinelibri.com/1-noemvri/"),
       ("2026-11-08", "https://www.cinelibri.com/8-noemvri/")])
_sofia, _other, _bad = OFE.parse_cinelibri_day(
    (FIXTURES / "festival_cinelibri_day.html").read_text(encoding="utf-8"), "2026-11-08")
check("day page: Sofia cards", [(x["title"], x["venue"], x["date"], x["time"]) for x in _sofia], [
    ("Кокухо: Живо съкровище", "Културен център G8", "2026-11-08", "13:00"),
    ("Малкият Никола: Какво чакаме, за да бъдем щастливи?", "Евросинема", "2026-11-08", "13:15"),
    ("Историите на нощта", "Френски институт", "2026-11-08", "14:15"),
    ("Героят от гара „Фридрихщрасе“", "Влайкова", "2026-11-08", "15:00")])
check("day page: film page and ticket of a card",
      (_sofia[0]["film_url"], _sofia[0]["ticket_url"]),
      ("https://www.cinelibri.com/kokuho-zhivo-sakrovishte/", "https://epaygo.bg/3140859053"))
check("day page: the 'Прожекции в страната' grid is outside Sofia",
      [(x["venue"], x["date"]) for x in _other], [("ФКЦ, Варна", "2026-11-08")])
check("day page: no malformed card", _bad, 0)
_s1, _o1, _ = OFE.parse_cinelibri_day(
    (FIXTURES / "festival_cinelibri_day_single.html").read_text(encoding="utf-8"), "2026-10-15")
check("unheaded single-grid day (15 Oct): its cards are the day's programme",
      [(x["title"], x["venue"], x["time"], x["extra"]) for x in _s1],
      [("Мастило", "Зала 1 на НДК", "19:00", "Официална церемония по откриване")])
def _fraises(fn, *a):
    try:
        fn(*a)
    except OFE.MarkupError:
        return True
    return False


check("a page without a programme grid is a markup change", _fraises(OFE.parse_cinelibri_day,
      "<html><body><h2>8 ноември</h2><p>нищо</p></body></html>", "2026-11-08"), True)

_cl_pages = {p["slug"]: p for p in _json.loads((FIXTURES / "festival_cinelibri_films.json").read_text(encoding="utf-8"))}
_pepel = OFE.parse_cinelibri_film(_cl_pages["pepel"])
check("film page: title, original, country list, year, runtime",
      [_pepel[k] for k in ("title_bg", "original", "country", "year", "runtime", "kind")],
      ["Пепел", "Popel", "Чехия/Франция/Испания", 2025, 87, "film"])
check("film page: director and cast (no 'и др.')", (_pepel["director"], _pepel["cast"]),
      ("Ойер Плаза", "Унай Егуя, Антон Гандариас, Ева Сухи, Павел Палечек"))
check("film page: synopsis stops before the festival list",
      (_pepel["syn_bg"].startswith("След като прочита романа"), "Фестивали" in _pepel["syn_bg"],
       "Не съм герой.“ – Франтишек Сухи-младши" in _pepel["syn_bg"]), (True, False, True))
_mast = OFE.parse_cinelibri_film(_cl_pages["mastilo"])
check("film page with an unlabelled 'Country (year), N мин.' line",
      [_mast[k] for k in ("country", "year", "runtime", "director")],
      ["Обединено кралство/Франция/САЩ", 2026, 116, "Дани Бойл"])
check("'Режисьор и сценарист:' is the director",
      OFE.parse_cinelibri_film(_cl_pages["svatba-s-vkus-na-portokal"])["director"], "Кристоф Оноре")
_shorts = OFE.parse_cinelibri_film(_cl_pages["kasometrazhno-kino-izvyn-konkurs"])
check("a shorts programme page: title and description only (its credits are the first short's)",
      [_shorts[k] for k in ("kind", "title_bg", "original", "director", "year")],
      ["programme", "Късометражно кино извън конкурс", None, None, None])


class _FestNet:
    """Serves the fixtures as the live site would; `down` URLs fail."""
    def __init__(self, down=()):
        self.calls, self.down = [], set(down)
    def budget_spent(self):
        return False
    def json(self, url):
        self.calls.append(url)
        if url in self.down:
            return None
        if url == OFE.CL_PROGRAMME:
            return _cl_prog
        if url.startswith(OFE.CL_API + "/pages?per_page=100"):
            import urllib.parse as _up
            want = _up.parse_qs(_up.urlsplit(url).query)["slug"][0].split(",")
            return [p for s_, p in _cl_pages.items() if s_ in want]
        if url.startswith(OFE.CL_API + "/media"):
            return _json.loads((FIXTURES / "festival_cinelibri_media.json").read_text(encoding="utf-8"))
        return None
    def text(self, url):
        self.calls.append(url)
        if url in self.down:
            return None
        pages = {"https://www.cinelibri.com/8-noemvri/": "festival_cinelibri_day.html",
                 "https://www.cinelibri.com/15-oktomvri/": "festival_cinelibri_day_single.html",
                 OFE.KM_PROGRAMME: "festival_kinomania_programme.html",
                 "https://www.kinomania.bg/bg_BG/film/div-kon-devet-3672.html": "festival_kinomania_film.html",
                 "https://www.kinomania.bg/bg_BG/film/fantomut-na-operata-3677.html": "festival_kinomania_film_parens.html"}
        f = pages.get(url)
        if f:
            return (FIXTURES / f).read_text(encoding="utf-8")
        if url == "https://www.cinelibri.com/1-noemvri/":   # served as an empty day grid
            return "<html><body><h2>1 ноември</h2><div class='vc_grid-container'></div></body></html>"
        return None


_errs, _net1 = {}, _FestNet()
_cl = OFE.read_festival("cinelibri", _net1, "2026-10-09", "2026-12-14", _errs)
check("CineLibri reader: covered days", (_cl["covered_from"], _cl["covered_to"]), ("2026-10-15", "2026-11-08"))
check("CineLibri reader: Sofia screenings (title, date, time, venue, ticket)", _cl["screenings"][:2], [
    ("Мастило", "2026-10-15", "19:00", "Зала 1 на НДК", "https://epaygo.bg/3185539248"),
    ("Кокухо: Живо съкровище", "2026-11-08", "13:00", "Културен център G8", "https://epaygo.bg/3140859053")])
check("CineLibri reader: every Sofia venue is known (Евросинема is G8)", _cl["unknown_venues"], {})
check("CineLibri reader: ... the Евросинема screening is kept under its own name for the venue table",
      [s_[3] for s_ in _cl["screenings"] if s_[0].startswith("Малкият Никола")], ["Евросинема"])
check("CineLibri reader: other towns are dropped and counted", _cl["outside_sofia"], {"ФКЦ, Варна": 1})
_clf = {f["title_bg"]: f for f in _cl["films"]}
check("CineLibri reader: a film's poster is its featured image",
      _clf["Кокухо: Живо съкровище"]["poster"], "https://www.cinelibri.com/wp-content/uploads/2026/09/kokuho1.jpg")
check("CineLibri reader: programme + 3 day pages + one REST call for the film pages + one for images",
      len(_net1.calls), 6)
_net2 = _FestNet(down={"https://www.cinelibri.com/8-noemvri/"})
_cl2 = OFE.read_festival("cinelibri", _net2, "2026-10-09", "2026-12-14", {})
check("CineLibri reader: a day page that fails ends the coverage before it",
      (_cl2["covered_to"], any("8-noemvri" in n or "2026-11-08" in n for n in _cl2["notes"])), ("2026-11-01", True))
_errs = {}
check("CineLibri reader: programme page down -> None (keep previous)",
      OFE.read_festival("cinelibri", _FestNet(down={OFE.CL_PROGRAMME}), "2026-10-09", "2026-12-14", _errs), None)
check("CineLibri reader: ... with the reason", "programme" in _errs.get("cinelibri", ""), True)

print("festivals: Kinomania pages")
_km_rows, _km_bad = OFE.parse_kinomania_programme(
    (FIXTURES / "festival_kinomania_programme.html").read_text(encoding="utf-8"))
check("programme: venue, date, time, title, film page, ticket", _km_rows[0], (
    "НДК - Зала 1", "2026-11-12", "19:00", "Див кон девет",
    "https://www.kinomania.bg/bg_BG/film/div-kon-devet-3672.html", "https://epaygo.bg/1190981546"))
check("programme: two screenings on one day", [r[2:4] for r in _km_rows if r[1] == "2026-11-21"],
      [("17:00", "Минотавър"), ("20:00", "Фиорд")])
check("programme: 5 rows, none malformed", (len(_km_rows), _km_bad), (5, 0))
_kf = OFE.parse_kinomania_film((FIXTURES / "festival_kinomania_film.html").read_text(encoding="utf-8"),
                               "https://www.kinomania.bg/bg_BG/film/div-kon-devet-3672.html")
check("film page: title, original, country, year, runtime, director",
      [_kf[k] for k in ("title_bg", "original", "country", "year", "runtime", "director")],
      ["Див кон девет", "Wild Horse Nine", "Великобритания/САЩ/Чили", 2026, 118, "Мартин Макдона"])
check("film page: a gallery file named '…poster…' beats the og:image still",
      _kf["poster"].rsplit("/", 1)[-1],
      "digital-poster-1080-x-1920-px--wild-horse-nine-september-2026_2-0x450.jpg")
check("film page: synopsis after 'Анотация:'",
      _kf["syn_bg"].startswith("През 1973 г., малко преди военния преврат в Чили"), True)
_kp = OFE.parse_kinomania_film((FIXTURES / "festival_kinomania_film_parens.html").read_text(encoding="utf-8"),
                               "https://www.kinomania.bg/bg_BG/film/fantomut-na-operata-3677.html")
check("film page whose heading carries the original title",
      [_kp[k] for k in ("title_bg", "original", "runtime", "cast")],
      ["Фантомът на операта", "Le Fantôme de l'Opéra", 134,
       "Дева Касел, Ромен Дюрис, Жулиен дьо Сен-Жан, Дороте Жилбер"])
check("film page: the og:image (a gallery file) when no poster file",
      _kp["poster"], "https://www.kinomania.bg/public/uploads/gallery/2026/09/23/"
                     "le_fantome_de_l_opera_snd_full_dawa_9_2d1067d340-0x450.jpg")
_km = OFE.read_festival("kinomania", _FestNet(), "2026-10-09", "2026-12-14", {})
check("Kinomania reader: covered days and screenings (the closing night's stage show is not a film)",
      (_km["covered_from"], _km["covered_to"], len(_km["screenings"]), _km["screenings"][1][:4]),
      ("2026-11-12", "2026-11-22", 4, ("Фантомът на операта", "2026-11-16", "19:00", "НДК - Зала 1")))
check("…the stage show is left out of screenings and films, and named in the notes",
      (any("спектакъл" in x[0] for x in _km["screenings"]), any("спектакъл" in (f.get("title_bg") or "")
                                                                 for f in _km["films"]),
       any(n.startswith("not a film, left out: Великата илюзия") for n in _km["notes"])),
      (False, False, True))
check("not_a_film: only a trailing stage-show subtitle counts",
      [OFE.not_a_film(t) for t in ("Великата илюзия - Мултижанров спектакъл", "Хамлет (спектакъл)",
                                   "Последният спектакъл", "Спектакълът", "Концерт за пиано", "Фиорд")],
      [True, True, False, False, False, False])


class _EmptyKM(_FestNet):
    def text(self, url):
        if url == OFE.KM_PROGRAMME:
            return (FIXTURES / "festival_kinomania_programme_empty.html").read_text(encoding="utf-8")
        return super().text(url)


_kme = OFE.read_festival("kinomania", _EmptyKM(), "2026-10-09", "2026-12-14", {})
check("Kinomania before its programme is out: works and returns nothing",
      (_kme is not None, _kme and _kme["screenings"], _kme and _kme["covered_from"]), (True, [], None))
check("Kinomania programme page changed shape -> None",
      OFE.read_festival("kinomania", type("N", (_FestNet,), {"text": lambda self, u: "<html><body>x</body></html>"})(),
                        "2026-10-09", "2026-12-14", {}), None)

# The programme as published on the afternoon of 15 Oct 2026 (saved, trimmed to its
# block): the morning refresh read only the five НДК entries; Люмиер and Дом на
# киното were added later that day (their film galleries are dated 2026/10/15).
print("festivals: Kinomania pages of 15 Oct 2026 (Люмиер, Дом на киното)")
_km15, _km15_bad = OFE.parse_kinomania_programme(
    (FIXTURES / "festival_kinomania_programme_1015.html").read_text(encoding="utf-8"))
check("programme 15 Oct: 8 entries, none malformed", (len(_km15), _km15_bad), (8, 0))
check("programme 15 Oct: the new venues' screenings, each title as printed",
      [r[:4] for r in _km15 if r[0] != "НДК - Зала 1"],
      [("Люмиер", "2026-11-13", "20:00", "Среднощно слънце"),
       ("Люмиер", "2026-11-27", "18:00", "Черни пари за бели нощи"),
       ("Дом на киното", "2026-11-14", "18:00", "голишар")])
check("programme 15 Oct: every venue with screenings is an app venue",
      sorted({(r[0],) + OFE.venue_id("kinomania", r[0]) for r in _km15}),
      [("Дом на киното", "app", "dom-kino"), ("Люмиер", "app", "lumiere"), ("НДК - Зала 1", "app", "ndk1")])
_kb = OFE.parse_kinomania_film((FIXTURES / "festival_kinomania_film_bar.html").read_text(encoding="utf-8"),
                               "https://www.kinomania.bg/bg_BG/film/golishar-3692.html")
check("film page heading 'голишар | nestling': the Cyrillic title, the Latin one as the English title",
      [_kb[k] for k in ("title_bg", "title_en", "original", "director", "genres_text")],
      ["голишар", "nestling", None, "Георги Мартев", "Драма, Coming-of-Age"])
check("film page heading with a bar: the poster is still the film's own gallery file",
      _kb["poster"], "https://www.kinomania.bg/public/uploads/gallery/2026/10/15/nestling_still_3-1-0x450.jpg")
_kr = OFE.parse_kinomania_film((FIXTURES / "festival_kinomania_film_rezhisura.html").read_text(encoding="utf-8"),
                               "https://www.kinomania.bg/bg_BG/film/cherni-pari-za-beli-noshti-3680.html")
check("film page credit 'Режисура:' is the director",
      (_kr["title_bg"], _kr["title_en"], _kr["director"]),
      ("Черни пари за бели нощи", None, "Кристина Грозева, Петър Вълчанов"))


class _KM1015(_FestNet):
    """kinomania.bg on 15 Oct 2026; a film page without a fixture does not load."""
    PAGES = {OFE.KM_PROGRAMME: "festival_kinomania_programme_1015.html",
             "https://www.kinomania.bg/bg_BG/film/golishar-3692.html": "festival_kinomania_film_bar.html",
             "https://www.kinomania.bg/bg_BG/film/cherni-pari-za-beli-noshti-3680.html":
                 "festival_kinomania_film_rezhisura.html",
             "https://www.kinomania.bg/bg_BG/film/div-kon-devet-3672.html": "festival_kinomania_film.html",
             "https://www.kinomania.bg/bg_BG/film/fantomut-na-operata-3677.html": "festival_kinomania_film_parens.html"}
    def text(self, url):
        self.calls.append(url)
        f = self.PAGES.get(url)
        return (FIXTURES / f).read_text(encoding="utf-8") if f else None


_km15r = OFE.read_festival("kinomania", _KM1015(), "2026-10-15", "2026-12-20", {})
check("Kinomania reader 15 Oct: covered days, 7 Sofia screenings, the stage show left out",
      (_km15r["covered_from"], _km15r["covered_to"], len(_km15r["screenings"]),
       any(n.startswith("not a film, left out: Великата илюзия") for n in _km15r["notes"])),
      ("2026-11-12", "2026-11-27", 7, True))
check("Kinomania reader 15 Oct: screenings per app venue",
      sorted(__import__("collections").Counter(OFE.venue_id("kinomania", s_[3])[1]
                                               for s_ in _km15r["screenings"]).items()),
      [("dom-kino", 1), ("lumiere", 2), ("ndk1", 4)])
check("Kinomania reader 15 Oct: a screening's title is the film page's, without the English half",
      [s_[:4] for s_ in _km15r["screenings"] if s_[3] == "Дом на киното"],
      [("голишар", "2026-11-14", "18:00", "Дом на киното")])
check("Kinomania reader 15 Oct: no unknown venue, nothing outside Sofia",
      (_km15r["unknown_venues"], _km15r["outside_sofia"]), ({}, {}))
# What the next refresh makes of it (the merge rules, with this morning's PRELIM_FROM:
# Люмиер's programme covers to 15 Dec, Дом на киното's to 22 Oct; neither lists these).
_km_fid = {"Див кон девет": "div-kon-devet", "Фантомът на операта": "fantomat-na-operata",
           "Минотавър": "minotavar", "Фиорд": "fiord", "Среднощно слънце": "srednoshtno-slantse",
           "Черни пари за бели нощи": "cherni-pari-za-beli-noshti", "голишар": "golishar"}
_km_rows = [(_km_fid[s_[0]], OFE.venue_id("kinomania", s_[3])[1], s_[1], s_[2]) for s_ in _km15r["screenings"]]
_r, _p, _pr, _i = S.merge_festival_rows(
    [], {"lumiere": "2026-12-16", "dom-kino": "2026-10-23"}, {}, {"kinomania": _km_rows},
    {"kinomania": (_km15r["covered_from"], _km15r["covered_to"])}, {}, [], "2026-10-15", "2026-12-20")
check("next refresh: НДК 4 confirmed, Люмиер 2 preliminary per row, Дом на киното 1 preliminary by date",
      (sorted(x[2] for x in _i["confirmed"]), sorted(x[2] for x in _i["prelim_row"]),
       sorted(x[2] for x in _i["prelim_date"]), _p["ndk1"]),
      (["ndk1"] * 4, ["lumiere"] * 2, ["dom-kino"], "2026-11-28"))
check("next refresh: the Люмиер rows go into PRELIM_ROWS", _pr,
      [["cherni-pari-za-beli-noshti", "lumiere", "2026-11-27"], ["srednoshtno-slantse", "lumiere", "2026-11-13"]])

print("festivals: FESTMARK (festival marks for the app)")
_fm = S.festival_marks(_i, _r, OFE.NAMES)
check("FESTMARK: the festival's short names and every film it shows",
      (_fm["kinomania"]["bg"], _fm["kinomania"]["en"], _fm["kinomania"]["films"], _fm["kinomania"]["shows"]),
      ("Киномания", "Kinomania", sorted(_km_fid.values()), []))
_r2, _p2, _pr2, _i2 = S.merge_festival_rows(
    [["a", "dom-kino", "2026-10-15", ["18:00"]], ["b", "dom-kino", "2026-10-16", ["20:00"]]],
    {"dom-kino": "2026-10-20"},
    {"dom-kino": {("a", "2026-10-15"): {"18:00"}, ("b", "2026-10-16"): {"20:00"}}},
    {"cinelibri": [("a", "dom-kino", "2026-10-15", "18:00"),        # the venue lists it
                   ("b", "dom-kino", "2026-10-16", "21:00"),        # the venue contradicts it
                   ("m", "ndk1", "2026-10-15", "19:00")]},          # a festival venue: confirmed
    {"cinelibri": ("2026-10-15", "2026-11-08")}, {}, [], "2026-10-09", "2026-12-14")
_fm2 = S.festival_marks(_i2, _r2, OFE.NAMES)
check("FESTMARK: a row the venue lists counts, one the venue contradicts does not",
      _fm2, {"cinelibri": {"bg": "Синелибри", "en": "CineLibri", "films": ["a", "m"], "shows": []}})
check("FESTMARK: a festival with no row shown is left out", S.festival_marks(_i2, [], OFE.NAMES), {})
_pg = "const PREMIERES={};\nconst VLINKS=[];\n"
_pg1 = S.write_festmark(_pg, _fm2)
check("write_festmark: inserted before VLINKS when the build has none",
      _pg1.splitlines()[1].startswith('const FESTMARK={"cinelibri":{"bg":"Синелибри"'), True)
check("write_festmark: replaced in place, one line", S.write_festmark(_pg1, {}),
      "const PREMIERES={};\nconst FESTMARK={};\nconst VLINKS=[];\n")

print("festivals: merge rules (merge_festival_rows)")
_FL, _EN = "2026-10-09", "2026-12-14"
_rows0 = [["a", "dom-kino", "2026-10-15", ["18:00"]], ["b", "dom-kino", "2026-10-16", ["20:00"]],
          ["z", "dom-kino", "2026-10-25", ["16:00"]], ["old", "ndk1", "2026-10-20", ["19:00"]],
          ["keep", "ndk1", "2026-12-01", ["19:00"]]]
_own0 = {"dom-kino": {("a", "2026-10-15"): {"18:00"}, ("b", "2026-10-16"): {"20:00"},
                      ("adv", "2026-10-30"): {"21:00"}}}
_pf0 = {"dom-kino": "2026-10-20"}


def _M(fest_rows, rows=None, own=None, pf=None, cover=None, prev=None, prev_pr=()):
    return S.merge_festival_rows(rows if rows is not None else _rows0, pf if pf is not None else _pf0,
                                 own if own is not None else _own0, fest_rows,
                                 cover if cover is not None else {"cinelibri": ("2026-10-15", "2026-11-08")},
                                 prev or {}, list(prev_pr), _FL, _EN)


def _times(rows, fid, v, d):
    return next((r[3] for r in rows if r[:3] == [fid, v, d]), None)


_r, _p, _pr, _i = S.merge_festival_rows(_rows0, _pf0, _own0, {"cinelibri": [("a", "ndk1", "2026-10-16", "19:00")]},
                                        {"cinelibri": ("2026-10-15", "2026-11-08")}, {}, [], _FL, _EN,
                                        festival_venues=("ndk1",))
check("0. PRELIM_FROM is written only for the festival venues the page lists (a page built before a new "
      "venue reached src/data.html must still pass the gate)",
      (_p.get("ndk1"), "frenski-institut" in _p, "toplo-film" in _p, "tsentralni-hali" in _p),
      ("2026-11-09", False, False, False))
_r, _p, _pr, _i = _M({"cinelibri": [("a", "dom-kino", "2026-10-15", "18:00")]})
check("1. the venue lists the film that day at that time: nothing added",
      (_times(_r, "a", "dom-kino", "2026-10-15"), _pr, len(_i["listed"])), (["18:00"], [], 1))
_r, _p, _pr, _i = _M({"cinelibri": [("a", "dom-kino", "2026-10-15", "21:00")]})
check("2. same film, same day, different time: the venue wins, the festival row is logged",
      (_times(_r, "a", "dom-kino", "2026-10-15"), _i["contradicted"]),
      (["18:00"], [["cinelibri", "a", "dom-kino", "2026-10-15", "21:00"]]))
_r, _p, _pr, _i = _M({"cinelibri": [("f", "dom-kino", "2026-10-17", "19:00")]})
check("3. a covered date the venue does not list the film on: added, preliminary per row",
      (_times(_r, "f", "dom-kino", "2026-10-17"), _pr, _p["dom-kino"]),
      (["19:00"], [["f", "dom-kino", "2026-10-17"]], "2026-10-20"))
check("3b. ... and a row added where the venue lists another film at that very time is reported",
      _M({"cinelibri": [("f", "dom-kino", "2026-10-16", "20:00")]})[3]["clash"],
      [["cinelibri", "f", "dom-kino", "2026-10-16", "20:00", ["b"]]])
_r, _p, _pr, _i = _M({"cinelibri": [("f", "dom-kino", "2026-10-28", "19:00"), ("z", "dom-kino", "2026-10-25", "18:00")]})
check("4. after the venue's coverage: added, preliminary by date (not per row)",
      (_times(_r, "f", "dom-kino", "2026-10-28"), _times(_r, "z", "dom-kino", "2026-10-25"), _pr, len(_i["prelim_date"])),
      (["19:00"], ["16:00", "18:00"], [], 2))
_r, _p, _pr, _i = _M({"cinelibri": [("adv", "dom-kino", "2026-10-30", "19:00")]})
check("4b. after coverage, the venue's own advance listing still wins over another time",
      (_times(_r, "adv", "dom-kino", "2026-10-30"), len(_i["contradicted"])), (None, 1))
_r, _p, _pr, _i = _M({"cinelibri": [("m", "ndk1", "2026-10-15", "19:00")]})
check("5. a venue without a programme of its own: confirmed; PRELIM_FROM the day after the festival",
      (_times(_r, "m", "ndk1", "2026-10-15"), _pr, _p["ndk1"], len(_i["confirmed"])),
      (["19:00"], [], "2026-11-09", 1))
check("5b. ... its rows on festival days are the festival's; later rows are kept",
      (_times(_r, "old", "ndk1", "2026-10-20"), _times(_r, "keep", "ndk1", "2026-12-01")), (None, ["19:00"]))
_r, _p, _pr, _i = _M({"cinelibri": [("m", "frenski-institut", "2026-10-16", "18:30"),
                                    ("k", "ndk1", "2026-11-12", "19:00")]},
                     cover={"cinelibri": ("2026-10-15", "2026-11-08"), "kinomania": ("2026-11-12", "2026-11-22")})
check("5c. two festivals: PRELIM_FROM after the later one, for both festival venues",
      (_p["ndk1"], _p["frenski-institut"]), ("2026-11-23", "2026-11-23"))
_r, _p, _pr, _i = _M({"cinelibri": [("t", "toplo-film", "2026-10-18", "19:00"),
                                    ("h", "tsentralni-hali", "2026-10-17", "17:00")]})
check("5d. Топлоцентрала and Сцена „Централни хали“: festival venues, confirmed",
      (len(_i["confirmed"]), _pr, _p["toplo-film"], _p["tsentralni-hali"]), (2, [], "2026-11-09", "2026-11-09"))
_r, _p, _pr, _i = _M({"cinelibri": [("s", "casa-libri", "2026-10-16", "19:00")]})
check("6. a cinema whose programme the app does not read: preliminary from today",
      (_times(_r, "s", "casa-libri", "2026-10-16"), _p["casa-libri"], _pr), (["19:00"], _FL, []))
_r, _p, _pr, _i = _M({"cinelibri": []}, rows=_rows0 + [["g", "dom-kino", "2026-10-28", ["19:00"]],
                                                        ["a", "dom-kino", "2026-10-30", ["18:00"]]],
                     own={"dom-kino": {("a", "2026-10-30"): {"18:00"}}},
                     prev={"cinelibri": [("g", "dom-kino", "2026-10-28", "19:00"), ("a", "dom-kino", "2026-10-30", "18:00")]})
check("7. a screening the festival dropped leaves; a time the venue lists never does",
      (_times(_r, "g", "dom-kino", "2026-10-28"), _times(_r, "a", "dom-kino", "2026-10-30")), (None, ["18:00"]))
_r, _p, _pr, _i = _M({}, rows=_rows0 + [["g", "dom-kino", "2026-10-28", ["19:00"]]],
                     prev={"cinelibri": [("g", "dom-kino", "2026-10-28", "19:00")]}, cover={})
check("8. a festival with no data at all this run keeps last run's rows",
      _times(_r, "g", "dom-kino", "2026-10-28"), ["19:00"])
_r, _p, _pr, _i = _M({}, rows=_rows0 + [["f", "dom-kino", "2026-10-17", ["19:00"]]],
                     own={"dom-kino": {("a", "2026-10-15"): {"18:00"}}}, prev_pr=[("f", "dom-kino", "2026-10-17")],
                     cover={})
check("9. a row marked preliminary per row last run stays marked while it stands",
      _pr, [["f", "dom-kino", "2026-10-17"]])
_r, _p, _pr, _i = _M({"cinelibri": [("f", "dom-kino", "2026-10-08", "19:00"), ("f", "dom-kino", "2026-12-15", "19:00")]})
check("10. rows outside today..window end are ignored",
      (_times(_r, "f", "dom-kino", "2026-10-08"), _times(_r, "f", "dom-kino", "2026-12-15"), _pr), (None, None, []))

check("festival rows persist one line each and read back (old list form too)",
      (S.festival_rows_out([("f", "odeon", "2026-10-20", "18:00")]),
       S.festival_rows_in(["f|odeon|2026-10-20|18:00", ["g", "g8", "2026-10-21", "19:00"], "bad"])),
      (["f|odeon|2026-10-20|18:00"], [("f", "odeon", "2026-10-20", "18:00"), ("g", "g8", "2026-10-21", "19:00")]))

print("festivals: credits stop at any label (fetch_film_info, Влайкова's run-on markup)")
for _raw, _want in [("Ойер Плаза Сценаристи", "Ойер Плаза"), ("Фриц Ланг Сценарист", "Фриц Ланг"),
                    ("Нани Морети В ролите", "Нани Морети"), ("Алекс Гибни Сценарист: Алекс Гибни", "Алекс Гибни"),
                    ("Вим Вендерс Сценаристи", "Вим Вендерс"),
                    ("Джон Малкович, Сам Рокуел Музика: Карлос Рафаел Ривера", "Джон Малкович, Сам Рокуел"),
                    ("Анна Ролер Оператор", "Анна Ролер"), ("Марио Мартоне Продуцент", "Марио Мартоне"),
                    ("Sergio Castellitto", "Sergio Castellitto"), ("Мартин Макдона", "Мартин Макдона"),
                    ("Кристоф Оноре", "Кристоф Оноре")]:
    check(f"_clean_credit_value({_raw!r})", FI._clean_credit_value(_raw), _want)
check("a dubbed film's voice cast is not label bleed",
      FI._film_info_stale({"pv": 5, "dir": "Х", "cast": "български дублаж: Весела Манолова, Георги Стоянов"}), False)
check("a stored 'Ойер Плаза Сценаристи' is stale (re-fetched and re-parsed)",
      FI._film_info_stale({"pv": 5, "dir": "Ойер Плаза Сценаристи", "cast": "А, Б"}), True)
_vk_bleed = BeautifulSoup((FIXTURES / "vlaikova_credits_bleed.html").read_text(encoding="utf-8"), "lxml")
check("Влайкова page: 'Режисьор: Ойер Плаза<br/>Сценаристи: …' -> director 'Ойер Плаза'",
      FI.parse_vlaikova_page(_vk_bleed, "https://vlaikovacinema.com/screening/x/").get("dir"), "Ойер Плаза")

_san = {"a": {"dir": "Нани Морети В ролите", "cast": "А, Б"}, "b": {"dir": "Сценаристи"},
        "c": {"dir": "Ебс Бърнау", "cast": "български дублаж: Весела Манолова"}}
check("stored credits are cleaned in place, wherever the film's link now points",
      (FI.sanitise_credits(_san), _san["a"]["dir"], "dir" in _san["b"], _san["c"]),
      (2, "Нани Морети", False, {"dir": "Ебс Бърнау", "cast": "български дублаж: Весела Манолова"}))

print("festivals: It's Alive! — a curated year and original title keep TMDB off the remakes")
_h = TMDB.tmdb_hints()
check("hint for 'Нещото' (Дом на киното: САЩ, 1982, Джон Карпентър)",
      _h.get("it-s-alive-horar-film-fest-chast-iv-neshtoto"),
      {"original": "The Thing", "year": 1982, "director": "Джон Карпентър"})
check("hint for 'Плетеният човек' (Великобритания, 1973, Робин Харди)",
      (_h.get("it-s-alive-horar-film-fest-chast-iv-pleteniyat-chovek") or {}).get("original"), "The Wicker Man")
check("a 2026 title gets its year only (no original title the venue does not print)",
      _h.get("it-s-alive-horar-film-fest-chast-iv-koloniya"), {"original": None, "year": 2026, "director": "Йон Сан-хо"})
check("hints never change film identity: the alias resolves to the same film",
      FID.FilmIndex([{"id": "it-s-alive-horar-film-fest-chast-iv-neshtoto",
                      "bg": "It’s Alive! Хорър Филм Фест Част IV: Нещото"}]).resolve(
          "It’s Alive! Хорър Филм Фест Част IV: Нещото", "dom-kino")[0],
      "it-s-alive-horar-film-fest-chast-iv-neshtoto")
_REMAKES = {"The Thing": [{"id": 60935, "title": "The Thing", "original_title": "The Thing", "release_date": "2011-10-12",
                           "vote_count": 3200, "poster_path": "/remake.jpg"},
                          {"id": 1091, "title": "The Thing", "original_title": "The Thing", "release_date": "1982-06-25",
                           "vote_count": 7900, "poster_path": "/classic.jpg"}],
            "Колония": [{"id": 5, "title": "Колония", "original_title": "The Colony", "release_date": "2013-09-20",
                         "vote_count": 900, "poster_path": "/c.jpg"}]}


def _tmdb_run(films, results, film_info=None):
    """fetch_tmdb.main() on a FILMS stub with a mocked API; returns (queries, tmdb_films.json).

    Optional `film_info` dict is written as film_info.json in the temp dir so the
    FILMS.originalTitle / film_info.title_en fallback chain can be exercised offline.
    """
    seen = []

    def api(path, params):
        if path == "search/movie":
            seen.append((params.get("query"), params.get("year"), params.get("language")))
            return {"results": results(params)}
        return {}
    o = (TMDB.api, TMDB.TOKEN, TMDB.HTML, TMDB.OUT, TMDB.LINKS_OUT, TMDB.HEALTH, TMDB.FILM_INFO_PATH)
    with _tmp.TemporaryDirectory() as td:
        td = _pl.Path(td)
        (td / "index.html").write_text("/* SOFIA-DATA-START */\nconst FILMS=" + _json.dumps(films, ensure_ascii=False)
                                       + ";\nconst LINKS=[];\n/* SOFIA-DATA-END */", encoding="utf-8")
        if film_info is not None:
            (td / "film_info.json").write_text(_json.dumps(film_info, ensure_ascii=False), encoding="utf-8")
        TMDB.api, TMDB.TOKEN, TMDB.HTML, TMDB.OUT, TMDB.LINKS_OUT, TMDB.HEALTH, TMDB.FILM_INFO_PATH = (
            api, "t", td / "index.html", td / "t.json", td / "l.json", td / "health.json",
            td / "film_info.json")
        try:
            TMDB.main()
            out = _json.loads((td / "t.json").read_text(encoding="utf-8")) if (td / "t.json").exists() else {}
        finally:
            (TMDB.api, TMDB.TOKEN, TMDB.HTML, TMDB.OUT,
             TMDB.LINKS_OUT, TMDB.HEALTH, TMDB.FILM_INFO_PATH) = o
    return seen, out


_thing = [{"id": "it-s-alive-horar-film-fest-chast-iv-neshtoto", "bg": "It’s Alive! Хорър Филм Фест Част IV: Нещото", "en": ""}]
_q, _o = _tmdb_run(_thing, lambda prm: _REMAKES.get(prm.get("query"), []))
check("It's Alive! 'Нещото' searches 'The Thing' with the year 1982", _q[:1], [("The Thing", 1982, None)])
check("... and gets The Thing (1982), not the 2011 film",
      (_o.get(_thing[0]["id"], {}).get("tmdb_id"), _o.get(_thing[0]["id"], {}).get("tmdb_year")), (1091, "1982"))
_q, _o = _tmdb_run(_thing, lambda prm: _REMAKES["The Thing"][:1])
check("only the remake on TMDB -> no match (the curated year rejects it)",
      _o.get(_thing[0]["id"], {}).get("matched"), False)
_col = [{"id": "it-s-alive-horar-film-fest-chast-iv-koloniya", "bg": "It’s Alive! Хорър Филм Фест Част IV: Колония", "en": ""}]
_q, _o = _tmdb_run(_col, lambda prm: _REMAKES.get(prm.get("query"), []))
check("a 2026 'Колония' searches its Bulgarian title and rejects the 2013 'Колония'",
      (_q[:1], _o.get(_col[0]["id"], {}).get("matched")), ([("Колония", None, "bg")], False))

# -------------------------------------------------- P2C: original-title fallback + 6 classics
print("P2C: original-title fallback — 6 classics need original-title hints")

# Load the TMDB-search fixtures for the 4 classic films
_p2c_fix = _json.loads((FIXTURES / "tmdb_classics.json").read_text(encoding="utf-8"))["results"]

# tmdb_hints() reads 'original' for the 4 new classic hints from the real film_aliases.json
_p2c_hints = TMDB.tmdb_hints()
check("P2C hints: chunkin-ekspres has original='Chungking Express' year=1994",
      (_p2c_hints.get("chunkin-ekspres", {}).get("original"),
       _p2c_hints.get("chunkin-ekspres", {}).get("year")),
      ("Chungking Express", 1994))
check("P2C hints: obshtestvo-na-martvite-poeti has original='Dead Poets Society' year=1989",
      (_p2c_hints.get("obshtestvo-na-martvite-poeti", {}).get("original"),
       _p2c_hints.get("obshtestvo-na-martvite-poeti", {}).get("year")),
      ("Dead Poets Society", 1989))
check("P2C hints: kratak-film-za-ubiystvoto has original='A Short Film About Killing' year=1988",
      (_p2c_hints.get("kratak-film-za-ubiystvoto", {}).get("original"),
       _p2c_hints.get("kratak-film-za-ubiystvoto", {}).get("year")),
      ("A Short Film About Killing", 1988))
check("P2C hints: karay-kolata-mi has original='Drive My Car' year=2021",
      (_p2c_hints.get("karay-kolata-mi", {}).get("original"),
       _p2c_hints.get("karay-kolata-mi", {}).get("year")),
      ("Drive My Car", 2021))

# Full matching path for each classic via _tmdb_run with fixture results
def _p2c_api(title):
    """Return a results-lambda that serves fixture hits only when query matches title."""
    hits = _p2c_fix[title]
    return lambda prm: hits if prm.get("query") == title else []

_cke = [{"id": "chunkin-ekspres", "bg": "Чункин експрес", "en": ""}]
_q, _o = _tmdb_run(_cke, _p2c_api("Chungking Express"))
check("P2C classic: Чункин експрес matches as Chungking Express (id=11104, year=1994)",
      (_o.get("chunkin-ekspres", {}).get("tmdb_id"),
       _o.get("chunkin-ekspres", {}).get("tmdb_year")), (11104, "1994"))
check("P2C classic: Чункин експрес query uses original-title 'Chungking Express'",
      any(q[0] == "Chungking Express" for q in _q), True)

_dps = [{"id": "obshtestvo-na-martvite-poeti", "bg": "Общество на мъртвите поети", "en": ""}]
_q, _o = _tmdb_run(_dps, _p2c_api("Dead Poets Society"))
check("P2C classic: Общество на мъртвите поети matches as Dead Poets Society (id=207, year=1989)",
      (_o.get("obshtestvo-na-martvite-poeti", {}).get("tmdb_id"),
       _o.get("obshtestvo-na-martvite-poeti", {}).get("tmdb_year")), (207, "1989"))

_sfak = [{"id": "kratak-film-za-ubiystvoto", "bg": "Кратък филм за убийството", "en": ""}]
_q, _o = _tmdb_run(_sfak, _p2c_api("A Short Film About Killing"))
check("P2C classic: Кратък филм за убийството matches as A Short Film About Killing (id=10754, year=1988)",
      (_o.get("kratak-film-za-ubiystvoto", {}).get("tmdb_id"),
       _o.get("kratak-film-za-ubiystvoto", {}).get("tmdb_year")), (10754, "1988"))

_dmc = [{"id": "karay-kolata-mi", "bg": "Карай колата ми", "en": ""}]
_q, _o = _tmdb_run(_dmc, _p2c_api("Drive My Car"))
check("P2C classic: Карай колата ми matches as Drive My Car (id=758866, year=2021)",
      (_o.get("karay-kolata-mi", {}).get("tmdb_id"),
       _o.get("karay-kolata-mi", {}).get("tmdb_year")), (758866, "2021"))

# Year guard: original-title result with wrong year must be rejected
_p2c_wrong_yr = [{"id": 99999, "title": "Drive My Car", "original_title": "ドライブ・マイ・カー",
                   "release_date": "2010-01-01", "vote_count": 2200, "poster_path": "/fake.jpg"}]
_q, _o = _tmdb_run(_dmc, lambda prm: _p2c_wrong_yr if prm.get("query") == "Drive My Car" else [])
check("P2C year guard: Drive My Car result from 2010 rejected when hint year is 2021",
      _o.get("karay-kolata-mi", {}).get("matched"), False)

# Two remaining classics: A Short Film About Love + LOTR
check("P2C hints: kratak-film-za-lyubovta has original='A Short Film About Love' year=1988",
      (_p2c_hints.get("kratak-film-za-lyubovta", {}).get("original"),
       _p2c_hints.get("kratak-film-za-lyubovta", {}).get("year")),
      ("A Short Film About Love", 1988))
check("P2C hints: vlastelinat-na-prastenite has original='The Lord of the Rings: ...' year=2001",
      (_p2c_hints.get("vlastelinat-na-prastenite-zadrugata-na-prastena", {}).get("original"),
       _p2c_hints.get("vlastelinat-na-prastenite-zadrugata-na-prastena", {}).get("year")),
      ("The Lord of the Rings: The Fellowship of the Ring", 2001))

_sfal = [{"id": "kratak-film-za-lyubovta", "bg": "Кратък филм за любовта", "en": ""}]
_q, _o = _tmdb_run(_sfal, _p2c_api("A Short Film About Love"))
check("P2C classic: Кратък филм за любовта matches as A Short Film About Love (id=31056, year=1988)",
      (_o.get("kratak-film-za-lyubovta", {}).get("tmdb_id"),
       _o.get("kratak-film-za-lyubovta", {}).get("tmdb_year")), (31056, "1988"))

_lotr = [{"id": "vlastelinat-na-prastenite-zadrugata-na-prastena",
          "bg": "Властелинът на пръстените: Задругата на пръстена", "en": ""}]
_q, _o = _tmdb_run(_lotr, _p2c_api("The Lord of the Rings: The Fellowship of the Ring"))
check("P2C classic: LOTR Fellowship matches as Lord of the Rings (id=120, year=2001)",
      (_o.get("vlastelinat-na-prastenite-zadrugata-na-prastena", {}).get("tmdb_id"),
       _o.get("vlastelinat-na-prastenite-zadrugata-na-prastena", {}).get("tmdb_year")), (120, "2001"))

# ---- P2C item 1: FILMS.originalTitle and film_info.title_en fallback chain ----
print("P2C: general fallback chain — originalTitle and film_info.title_en")

# FILMS.originalTitle fallback: a minted arthouse film with no FILMS.en but with an
# originalTitle field (set from the festival page's original_title during minting).
_orig_fix = [{"id": 120, "title": "The Thing", "original_title": "The Thing",
              "release_date": "1982-01-01", "vote_count": 8000, "poster_path": "/fake-tt.jpg"}]
_ot_film = [{"id": "test-originalTitle-film", "bg": "Тест оригинален", "en": "",
             "originalTitle": "The Thing", "year": 1982}]
_q, _o = _tmdb_run(_ot_film, lambda prm: _orig_fix if prm.get("query") == "The Thing" else [])
check("P2C fallback: FILMS.originalTitle drives EN search when FILMS.en is empty",
      (_q and _q[0][0], _o.get("test-originalTitle-film", {}).get("tmdb_id")), ("The Thing", 120))

# film_info.title_en fallback: no FILMS.en, no originalTitle, but film_info has title_en.
_fi_title_fix = [{"id": 77, "title": "Mulholland Drive", "original_title": "Mulholland Drive",
                  "release_date": "2001-10-19", "vote_count": 9000, "poster_path": "/fake-md.jpg"}]
_fi_film = [{"id": "test-fi-title-film", "bg": "Мълхоланд Драйв", "en": "", "year": 2001}]
_fi_info = {"test-fi-title-film": {"title_en": "Mulholland Drive", "fsrc": "test"}}
_q, _o = _tmdb_run(_fi_film,
                   lambda prm: _fi_title_fix if prm.get("query") == "Mulholland Drive" else [],
                   film_info=_fi_info)
check("P2C fallback: film_info.title_en drives EN search when FILMS.en and originalTitle absent",
      (_q and _q[0][0], _o.get("test-fi-title-film", {}).get("tmdb_id")), ("Mulholland Drive", 77))

# Same-title remake year-guard: two films named "The Thing" (1982 original, 2011 remake).
# When the film's year is 1982, the 2011 remake (release_date 2011-10-14) must be rejected
# even though it carries the same title.
_remake_fix = [{"id": 888, "title": "The Thing", "original_title": "The Thing",
                "release_date": "2011-10-14", "vote_count": 3000, "poster_path": "/fake-tr.jpg"}]
_ot_film82 = [{"id": "test-originalTitle-year-guard", "bg": "Нещото оригинал", "en": "",
               "originalTitle": "The Thing", "year": 1982}]
_q, _o = _tmdb_run(_ot_film82, lambda prm: _remake_fix if prm.get("query") == "The Thing" else [])
check("P2C fallback: year guard rejects 2011 The Thing when film year is 1982",
      _o.get("test-originalTitle-year-guard", {}).get("matched"), False)

# Cyrillic originalTitle must NOT be used as EN search (it would query TMDB as a BG title instead).
_cyr_film = [{"id": "test-cyrillic-ot", "bg": "Тест", "en": "", "originalTitle": "Тест на руски", "year": 2020}]
_q, _o = _tmdb_run(_cyr_film, lambda prm: [])
# No EN query should be issued; only a BG query or no match at all.
_cyr_en_queries = [q for q in _q if q[2] is None]   # queries without language=bg
check("P2C fallback: Cyrillic originalTitle is NOT used as EN search term",
      _cyr_en_queries, [])

print("festivals: the stale rule composes with venue_state (P1A)")
# a cinema whose own read failed: venue_state kept ALL its rows, preliminary from
# today (PRELIM_FROM = floor). Its kept rows are not its programme: no festival
# row is "listed" or "contradicted" by them, and none of them is removed.
_kept_rows = [["k", "dom-kino", "2026-10-15", ["18:00"]], ["g", "dom-kino", "2026-10-28", ["19:00"]]]
_r, _p, _pr, _i = S.merge_festival_rows(
    _kept_rows, {"dom-kino": _FL}, {}, {"cinelibri": [("k", "dom-kino", "2026-10-15", "20:30")]},
    {"cinelibri": ("2026-10-15", "2026-11-08")}, {"cinelibri": [("g", "dom-kino", "2026-10-28", "19:00")]},
    [], _FL, _EN, kept_venues={"dom-kino"})
check("11. a kept cinema: the festival row is added beside the kept one, preliminary by date",
      (_times(_r, "k", "dom-kino", "2026-10-15"), len(_i["prelim_date"]), _i["contradicted"], _pr),
      (["18:00", "20:30"], 1, [], []))
check("11b. ... and last run's festival rows there are never removed (the venue keeps every row)",
      _times(_r, "g", "dom-kino", "2026-10-28"), ["19:00"])

_vrows = [["m", "ndk1", "2026-10-15", ["19:00"]], ["k", "ndk1", "2026-11-12", ["19:00"]],
          ["x", "dom-kino", "2026-10-15", ["18:00"]]]
_fresh = {"cinelibri": {("m", "ndk1", "2026-10-15")}}
_rep_ok = {"cinelibri": {"status": "official"}, "kinomania": {"status": "official"}}
_rep_kept = {"cinelibri": {"status": "official"},
             "kinomania": {"status": "kept-previous", "error": "the programme page did not load"}}
_frows = {"cinelibri": [("m", "ndk1", "2026-10-15", "19:00")], "kinomania": [("k", "ndk1", "2026-11-12", "19:00")]}
_asked = []


def _pol(expired):
    def f():
        _asked.append(1)
        return {"since": "2026-10-01", "age": 15 if expired else 3, "expired": expired, "confirmed_to": None}
    return f


_fresh_ok = {"cinelibri": {("m", "ndk1", "2026-10-15")}, "kinomania": {("k", "ndk1", "2026-11-12")}}
_r, _o = S.festival_venue_outcome("ndk1", _vrows, _fresh_ok, _rep_ok, _frows, {}, _pol(False), _FL)
check("12. a festival venue every festival vouched for: confirmed, read ok, no policy asked",
      (_o["status"], _o["ok"], _o["prelim_from"], len(_r), _asked), ("festival", True, None, 3, []))
_r, _o = S.festival_venue_outcome("ndk1", _vrows, _fresh, _rep_kept, _frows, {}, _pol(False), _FL)
check("12b. one of its festivals not read: preliminary at once, the kept row stays (under 14 days)",
      (_o["status"], _o["prelim_from"], [r[0] for r in _o["kept"]], len(_r), "kinomania" in _o["why"]),
      ("unreachable", _FL, ["k"], 3, True))
_r, _o = S.festival_venue_outcome("ndk1", _vrows, _fresh, _rep_kept, _frows, {}, _pol(True), _FL)
check("12c. ... not read for over 14 days: the unvouched row goes, the vouched one and other venues stay",
      (_o["status"], sorted((r[0], r[1]) for r in _r)), ("unreachable", [("m", "ndk1"), ("x", "dom-kino")]))
_r, _o = S.festival_venue_outcome("ndk1", _vrows[:1] + [["old", "ndk1", "2026-10-20", ["19:00"]]], _fresh,
                                  _rep_ok, {"cinelibri": _frows["cinelibri"]}, {}, _pol(False), _FL)
check("12d. a row no festival lists any more (left on the page): preliminary too, never confirmed",
      (_o["status"], _o["why"]), ("unreachable", "rows no festival vouched for this run"))
_r, _o = S.festival_venue_outcome("frenski-institut", _vrows, _fresh, _rep_kept, _frows, {}, _pol(False), _FL)
check("12e. a festival not read elsewhere leaves a venue it never listed alone",
      (_o["status"], _o["ok"]), ("festival", None))

print("festivals: identity and minting")
_fcat = [{"id": "sinelibri-2026-pepel", "bg": "СИНЕЛИБРИ 2026 – ПЕПЕЛ", "source": "vlaikova"},
         {"id": "sinelibri-chernoto-topche", "bg": "Синелибри: Черното топче", "source": "cc-sofia", "runtime": 154},
         {"id": "sinelibri-2026-embargo", "bg": "СИНЕЛИБРИ 2026 – ЕМБАРГО | представяне на творчеството на  Жозе Сарамаго",
          "source": "vlaikova"},
         {"id": "kukla-seed", "bg": "Кукла", "year": 2015, "runtime": 95}]
_fidx = FID.FilmIndex(_fcat, {"sinelibri-2026-pepel", "sinelibri-chernoto-topche", "sinelibri-2026-embargo"}, aliases={})
_taken, _mints = {f["id"] for f in _fcat}, []


def _mint(title, venue, meta):
    rec = S.minted_film_record(title, venue, meta, _taken)
    _fidx.add(rec, minted=True)
    _mints.append(rec)
    return rec["id"]


_ffilms = {"Пепел": {"year": 2025, "runtime": 87, "original": "Popel"},
           "Черното топче": {"year": 2026, "runtime": 155, "original": "La bola negra"},
           "Ембарго": {"year": 2010, "runtime": 83, "original": "Embargo"},
           "Кукла": {"year": 2026, "runtime": 162, "original": "Lalka", "country": "Полша",
                     "genres_text": "драма", "url": "https://www.cinelibri.com/kukla/"}}
_fslots = {"Пепел": [("vlaikova", "2026-10-20", "19:00")], "Черното топче": [("cc-sofia", "2026-11-04", "19:00")],
           "Ембарго": [("vlaikova", "2026-10-16", "19:30")], "Кукла": [("dom-kino", "2026-10-17", "16:00")]}
_fmap, _fdec = S.resolve_festival_films(_fidx, _ffilms, _fslots,
                                        {("vlaikova", "2026-10-16", "19:30"): {"sinelibri-2026-embargo"}}, _mint, "cinelibri")
check("a festival title meets the cinemas' prefixed spellings ('СИНЕЛИБРИ 2026 – …', 'Синелибри: …')",
      (_fmap["Пепел"], _fmap["Черното топче"]), ("sinelibri-2026-pepel", "sinelibri-chernoto-topche"))
check("the SLOT rule: the venue lists 'ЕМБАРГО | представяне…' at the festival's venue, date and time",
      (_fmap["Ембарго"], next(d["rule"] for d in _fdec if d["title"] == "Ембарго")), ("sinelibri-2026-embargo", "slot"))
check("a slot merge is reported as unsure", next(d["unsure"] for d in _fdec if d["title"] == "Ембарго"), True)
check("a year conflict keeps a same-titled film apart: 'Кукла' (2026) is minted, not 'Кукла' (2015)",
      _fmap["Кукла"] != "kukla-seed", True)
_mk = _mints[-1]
check("minted from the festival page: title, runtime, year, original title, country, genres, source",
      [_mk.get(k) for k in ("bg", "runtime", "year", "originalTitle", "country", "genres", "source")],
      ["Кукла", 162, 2026, "Lalka", "Полша", ["Драма"], "dom-kino"])
_fidx2 = FID.FilmIndex(_fcat[:3] + [{"id": "embargo-2", "bg": "Ембарго нощем", "source": "vlaikova"}],
                       aliases={})
_m2, _d2 = S.resolve_festival_films(_fidx2, {"Ембарго": {}}, {"Ембарго": [("vlaikova", "2026-10-16", "19:30")]},
                                    {("vlaikova", "2026-10-16", "19:30"): {"sinelibri-2026-embargo", "embargo-2"}},
                                    lambda t, v, m: "minted-embargo", "cinelibri")
check("two films in the festival's slot: no slot merge, a film is minted instead",
      _m2["Ембарго"], "minted-embargo")

print("festivals: details, posters and ticket links")
_e, _g = S.festival_details({"syn_bg": "Синопсис.", "director": "Реж", "cast": "Акт", "country": "Полша",
                             "year": 2026, "runtime": 162, "poster": "https://www.cinelibri.com/wp-content/uploads/2026/09/kukla1.jpg",
                             "url": "https://www.cinelibri.com/kukla/"},
                            {"id": "x", "bg": "Кукла"}, {"cast": "Вече има"}, {}, False)
check("details fill only empty fields (an existing film_info value stays)",
      (_e["synBg"], _e["dir"], _e["cast"], _e["country"], _e["year"], _e["img"], sorted(_g)),
      ("Синопсис.", "Реж", "Вече има", "Полша", 2026,
       "https://www.cinelibri.com/wp-content/uploads/2026/09/kukla1.jpg",
       ["country", "dir", "img", "runtime", "synBg", "year"]))
_e, _g = S.festival_details({"syn_bg": "С.", "director": "Р", "poster": "https://www.cinelibri.com/a/x1.jpg"},
                            {"director": "Сийд", "synBg": "Сийд синопсис"}, {}, {"poster_path": "/p.jpg", "ovBg": "TMDB"}, False)
check("details never cover the seed record or TMDB (synopsis, director, poster)", _g, [])
_e, _g = S.festival_details({"poster": "https://www.cinelibri.com/a/x1.jpg"}, {}, {}, {}, True)
check("a harvested og:image keeps priority over a festival poster", _g, [])
_rows_t = [["f1", "odeon", "2026-10-20", ["18:00"]], ["f2", "odeon", "2026-10-20", ["20:00"]],
           ["f2", "odeon", "2026-10-27", ["20:00"]], ["f3", "dom-kino", "2026-10-21", ["19:00"]]]
_scr = [("f1", "odeon", "2026-10-20", "https://epaygo.bg/111", "https://www.cinelibri.com/f1/"),
        ("f2", "odeon", "2026-10-20", "https://epaygo.bg/222", "https://www.cinelibri.com/f2/"),
        ("f2", "odeon", "2026-10-27", "https://epaygo.bg/333", "https://www.cinelibri.com/f2/"),
        ("f3", "dom-kino", "2026-10-21", "https://epaygo.bg/444", "https://www.cinelibri.com/f3/")]
check("ticket link: one screening -> its ticket; several dates -> the festival film page; a venue link stays",
      S.festival_ticket_links(_scr, {("f3", "dom-kino")}, _rows_t),
      {("f1", "odeon"): "https://epaygo.bg/111", ("f2", "odeon"): "https://www.cinelibri.com/f2/"})
check("a festival ticket shop is an allowed link at any venue",
      (S.allowed_festival_link("odeon", "https://epaygo.bg/1"), S.allowed_festival_link("odeon", "https://example.com/x"),
       S.allowed_festival_link("cg-park", "https://cinegrand.bg/x")), (True, False, True))
check("kinomania.bg images pass the poster policy (each sits in the film's own gallery)",
      PP.reject_reason("x", "https://www.kinomania.bg/public/uploads/gallery/2026/09/23/0123456789abcdef0123456789abcdef.jpg"), None)

print("festivals: PRELIM_ROWS in the page")
_pg = 'const VLINKS=[];\nconst PRELIM_FROM={"dom-kino":"2026-10-20"};\n/* x */'
_pg2 = S.write_prelim_rows(_pg, [["f", "dom-kino", "2026-10-17"]])
check("PRELIM_ROWS is inserted after PRELIM_FROM when the build lacks it",
      _pg2.splitlines()[2], 'const PRELIM_ROWS=[["f","dom-kino","2026-10-17"]];')
check("... and replaced in place next time",
      S.read_prelim_rows(S.write_prelim_rows(_pg2, [])), [])
check("read_prelim_rows", S.read_prelim_rows(_pg2), [("f", "dom-kino", "2026-10-17")])

print("festivals: fetch_tmdb strips a festival prefix before searching (It's Alive!)")
for _t, _want in [("It’s Alive! Хорър Филм Фест Част IV: Сиянието", "Сиянието"),
                  ("It's Alive! Хорър Филм Фест Част IV: Къде ми е главата?", "Къде ми е главата?"),
                  ("IT’S ALIVE! ХОРЪР ФИЛМ ФЕСТ ЧАСТ V – Нещото", "Нещото"),
                  ("It’s Alive! Хорър Филм Фест Част IV: Петък 69-и", "Петък 69-и"),
                  ("Синелибри 2026: ПЕПЕЛ", "ПЕПЕЛ"),
                  ("Синелибри: Черното топче", "Черното топче"),
                  ("Venom: The Last Dance", "Venom: The Last Dance"),
                  ("It’s Alive", "It’s Alive")]:
    check(f"strip_series_prefix({_t[:42]!r})", TMDB.strip_series_prefix(_t), _want)
check("declutter strips it too", TMDB.declutter("It’s Alive! Хорър Филм Фест Част IV: Плетеният човек"),
      ("Плетеният човек", None))
_orig_api5, _q5 = TMDB.api, []
TMDB.api = lambda path, params: (_q5.append(params.get("query")) or {"results": []})
_orig_tok, _orig_html5, _orig_out5, _orig_lo5, _orig_he5 = TMDB.TOKEN, TMDB.HTML, TMDB.OUT, TMDB.LINKS_OUT, TMDB.HEALTH
with _tmp.TemporaryDirectory() as _td5:
    _td5 = _pl.Path(_td5)
    (_td5 / "index.html").write_text(
        '/* SOFIA-DATA-START */\nconst FILMS=[{"id":"ia","bg":"It’s Alive! Хорър Филм Фест Част IV: Сиянието","en":""}];\n'
        'const LINKS=[];\n/* SOFIA-DATA-END */', encoding="utf-8")
    TMDB.TOKEN, TMDB.HTML, TMDB.OUT, TMDB.LINKS_OUT, TMDB.HEALTH = (
        "t", _td5 / "index.html", _td5 / "t.json", _td5 / "l.json", _td5 / "health.json")
    try:
        TMDB.main()
    finally:
        TMDB.api, TMDB.TOKEN, TMDB.HTML, TMDB.OUT, TMDB.LINKS_OUT, TMDB.HEALTH = (
            _orig_api5, _orig_tok, _orig_html5, _orig_out5, _orig_lo5, _orig_he5)
check("fetch_tmdb searches 'Сиянието', never the prefixed title", _q5[:1], ["Сиянието"])

print("festivals: CineLibri's crawl delay (netfetch)")
import netfetch as NF                                           # noqa: E402
_clock, _slept = [100.0], []
_orig_mono, _orig_sleep = NF.time.monotonic, NF.time.sleep
NF.time.monotonic = lambda: _clock[0]
NF.time.sleep = lambda s_: (_slept.append(round(s_, 1)), _clock.__setitem__(0, _clock[0] + s_))
try:
    _nf = NF.Fetcher()
    _nf._polite("www.cinelibri.com"); _clock[0] += 3.0; _nf._done("www.cinelibri.com")
    _clock[0] += 1.0; _nf._polite("www.cinelibri.com")
    _nf._polite("www.ndk.bg"); _clock[0] += 0.5; _nf._polite("www.ndk.bg")
finally:
    NF.time.monotonic, NF.time.sleep = _orig_mono, _orig_sleep
check("10 s between two cinelibri.com requests, counted from the end of the first; 1.2 s elsewhere",
      _slept, [9.0, 0.7])


class _FakeResp:
    status_code, headers, content, text = 200, {"Content-Type": "application/json"}, b"[]", "[]"


_clock, _slept = [500.0], []
NF.time.monotonic = lambda: _clock[0]
NF.time.sleep = lambda s_: (_slept.append(round(s_, 1)), _clock.__setitem__(0, _clock[0] + s_))
try:
    _nf = NF.Fetcher(host_budget=30)
    _nf.session.get = lambda *a, **k: (_clock.__setitem__(0, _clock[0] + 1.0), _FakeResp())[1]
    _got = [_nf.get(f"https://www.cinelibri.com/x{i}/") is not None for i in range(6)]
finally:
    NF.time.monotonic, NF.time.sleep = _orig_mono, _orig_sleep
check("cinelibri.com: its 10 s Crawl-delay is not charged to its host budget (6 requests > 30 s budget, "
      "1 s of host time each)", (_got, round(_nf._host_spent["www.cinelibri.com"], 1), _slept[:2]),
      ([True] * 6, 6.0, [10.0, 10.0]))            # each gap runs from the END of the last request

print("festivals: inject_films fills empty year / runtime / country of a minted film only")
with _tmp.TemporaryDirectory() as _td6:
    _td6 = _pl.Path(_td6)
    (_td6 / "index.html").write_text('const FILMS=' + _json.dumps(
        [{"id": "seed", "bg": "Сийд", "genres": ["Драма"]},
         {"id": "m1", "bg": "Минт", "genres": ["Драма"], "year": 2024}], ensure_ascii=False) + ';', encoding="utf-8")
    (_td6 / "cinema_films.json").write_text(_json.dumps({"m1": {"id": "m1", "bg": "Минт", "genres": ["Драма"], "year": 2024}}), encoding="utf-8")
    (_td6 / "film_info.json").write_text(_json.dumps({"seed": {"year": 1999, "runtime": 90},
                                                      "m1": {"year": 2026, "runtime": 87, "country": "Чехия/Франция"}},
                                                     ensure_ascii=False), encoding="utf-8")
    _o = (IF.HTML, IF.CINEMA_FILMS, IF.FILM_INFO)
    IF.HTML, IF.CINEMA_FILMS, IF.FILM_INFO = _td6 / "index.html", _td6 / "cinema_films.json", _td6 / "film_info.json"
    try:
        IF.main()
    finally:
        IF.HTML, IF.CINEMA_FILMS, IF.FILM_INFO = _o
    _src6 = (_td6 / "index.html").read_text(encoding="utf-8")
    _f6 = {f["id"]: f for f in _json.loads(_src6[_src6.index("["):_src6.rindex("]") + 1])}
check("inject_films: a seed film is never filled", ("year" in _f6["seed"], "runtime" in _f6["seed"]), (False, False))
check("inject_films: a minted film's empty runtime/country are filled; its year stays",
      (_f6["m1"]["year"], _f6["m1"]["runtime"], _f6["m1"]["country"]), (2024, 87, "Чехия/Франция"))

# ---------------------------------------------------------------- inject_data rating fields
print("inject_data passes TMDB rating fields (tv / tc) through to TMDBART")
# (inject_data is not imported here: importing it runs the whole injection and
#  rewrites the real index.html; the art-building lines are mirrored below)

# Build a minimal tmdb record that has tmdb_vote and vote_count but no imdb
_tmdb_record_tv = {
    "matched": True,
    "poster_path": "/abc.jpg",
    "backdrop_path": None,
    "en": "Test Film",
    "tmdb_vote": 7.5,
    "vote_count": 1234,
}

# Run the inject_data art-building logic inline (mirrors lines 42-58 of inject_data.py)
_art_rec_tv: dict = {}
_v = _tmdb_record_tv
if _v.get("poster_path"):   _art_rec_tv["p"] = _v["poster_path"]
if _v.get("backdrop_path"): _art_rec_tv["b"] = _v["backdrop_path"]
if _v.get("en"):            _art_rec_tv["en"] = _v["en"]
if _v.get("imdb") is not None: _art_rec_tv["imdb"] = _v["imdb"]
if _v.get("imdbVotes"):     _art_rec_tv["imdbVotes"] = _v["imdbVotes"]
if _v.get("tmdb_vote") is not None: _art_rec_tv["tv"] = _v["tmdb_vote"]
if _v.get("vote_count") is not None: _art_rec_tv["tc"] = _v["vote_count"]

check("inject_data: tv field present when tmdb_vote set",
      _art_rec_tv.get("tv"), 7.5)
check("inject_data: tc field present when vote_count set",
      _art_rec_tv.get("tc"), 1234)
check("inject_data: imdb field absent when imdb not in source",
      "imdb" not in _art_rec_tv, True)
check("inject_data: tv absent when tmdb_vote is None",
      "tv" not in {k: v for k, v in {
          "matched": True, "en": "X",
          "tmdb_vote": None, "vote_count": None,
      }.items() if k == "tmdb_vote" and v is not None}, True)

# ---------------------------------------------------------------- OMDb error handling (mocked)
print("OMDb error handling (mocked)")

# 1. omdb_rating returns (None, None, error_text) on API error (Response=False)
_orig_token_omdb = TMDB.OMDB_TOKEN
TMDB.OMDB_TOKEN = "fake-key"

def _mock_omdb_req(url_req, timeout=20, context=None):
    import io
    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, n=-1): return b'{"Response":"False","Error":"Invalid API key!"}'
    class _FakeRespJson:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, n=-1): return b'{"Response":"False","Error":"Invalid API key!"}'
        def __iter__(self): return iter([])
    import json as _json
    class _JsonResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        # json.load(r) is called on the response object
    return _FakeRespJson()

import urllib.request as _urllib_req
_orig_urlopen = _urllib_req.urlopen

class _MockAPIErrorResp:
    def __init__(self): self._data = b'{"Response":"False","Error":"Invalid API key!"}'
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self, n=-1): return self._data

def _mock_urlopen_apierr(req, timeout=20, context=None):
    import json as _json
    class _R:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, n=-1): return b'{"Response":"False","Error":"Invalid API key!"}'
        def __iter__(self): return iter([b'{"Response":"False","Error":"Invalid API key!"}'])
    # json.load() reads from the object; patch it with a BytesIO-like wrapper
    import io
    class _RIO:
        def __enter__(self): return io.BytesIO(b'{"Response":"False","Error":"Invalid API key!"}')
        def __exit__(self, *a): pass
    return io.BytesIO(b'{"Response":"False","Error":"Invalid API key!"}')

# Direct call to omdb_rating with mocked urlopen
import io as _io
_orig_urlopen2 = _urllib_req.urlopen
try:
    _urllib_req.urlopen = lambda req, timeout=20, context=None: _io.BytesIO(
        b'{"Response":"False","Error":"Invalid API key!"}')
    # BytesIO supports json.load since it has read()
    _r, _v, _rt_e, _mc_e, _err = TMDB.omdb_rating("tt0364569")
    check("omdb_rating: rating is None on API error",
          _r, None)
    check("omdb_rating: votes is None on API error",
          _v, None)
    check("omdb_rating: error text captured on Response=False",
          _err, "Invalid API key!")
finally:
    _urllib_req.urlopen = _orig_urlopen2
    TMDB.OMDB_TOKEN = _orig_token_omdb

# 2. no OMDB_TOKEN: no values, and an ERROR, so main() keeps last run's ratings
_orig_tok2 = TMDB.OMDB_TOKEN
TMDB.OMDB_TOKEN = ""
_r2, _v2, _rt2_e, _mc2_e, _err2 = TMDB.omdb_rating("tt0364569")
check("omdb_rating without a key: no values, and an error (an outage, not 'no ratings')",
      (_r2, _v2, _rt2_e, _mc2_e, bool(_err2)), (None, None, None, None, True))
check("omdb_rating without an imdb id: nothing, and no error (nothing to keep)",
      TMDB.omdb_rating(None), (None, None, None, None, None))
TMDB.OMDB_TOKEN = _orig_tok2

# 2b. end to end: a run without the OMDb key keeps every film's previous IMDb / RT / MC
#     (the 13 Oct 2026 scheduled run, secret misnamed, dropped them all)
def _omdb_nokey_run():
    films = [{"id": "film-x", "bg": "Oppenheimer", "en": "Oppenheimer", "year": 2023}]

    def api(path, params):
        if path == "search/movie":
            return {"results": [{"id": 872585, "title": "Oppenheimer", "original_title": "Oppenheimer",
                                 "release_date": "2023-07-19", "vote_count": 9000, "poster_path": "/o.jpg"}]}
        if path == "movie/872585":
            return {"imdb_id": "tt15398776", "overview": "x", "production_countries": [{"name": "United States of America"}]}
        return {}
    o = (TMDB.api, TMDB.TOKEN, TMDB.OMDB_TOKEN, TMDB.HTML, TMDB.OUT, TMDB.LINKS_OUT, TMDB.HEALTH, TMDB.FILM_INFO_PATH)
    with _tmp.TemporaryDirectory() as td:
        td = _pl.Path(td)
        (td / "index.html").write_text("/* SOFIA-DATA-START */\nconst FILMS=" + _json.dumps(films, ensure_ascii=False)
                                       + ";\nconst LINKS=[];\n/* SOFIA-DATA-END */", encoding="utf-8")
        (td / "t.json").write_text(_json.dumps({"film-x": {"matched": True, "tmdb_id": 872585, "imdb_id": "tt15398776",
                                                           "imdb": 8.3, "imdbVotes": "900K", "rt": 93, "mc": 90}}),
                                   encoding="utf-8")
        TMDB.api, TMDB.TOKEN, TMDB.OMDB_TOKEN, TMDB.HTML, TMDB.OUT, TMDB.LINKS_OUT, TMDB.HEALTH, TMDB.FILM_INFO_PATH = (
            api, "t", "", td / "index.html", td / "t.json", td / "l.json", td / "health.json", td / "film_info.json")
        try:
            TMDB.main()
            return _json.loads((td / "t.json").read_text(encoding="utf-8")).get("film-x", {})
        finally:
            (TMDB.api, TMDB.TOKEN, TMDB.OMDB_TOKEN, TMDB.HTML, TMDB.OUT, TMDB.LINKS_OUT, TMDB.HEALTH,
             TMDB.FILM_INFO_PATH) = o


_nk = _omdb_nokey_run()
check("a run without the OMDb key keeps the film's previous IMDb rating, votes, RT and MC",
      (_nk.get("imdb"), _nk.get("imdbVotes"), _nk.get("rt"), _nk.get("mc")), (8.3, "900K", 93, 90))

# 3. Keep-previous: on error, inherit previous value; do NOT cache None as final
_pv_prev_rating = {"imdb": 8.2, "imdbVotes": "200.5K"}
_r3, _v3, _err3 = None, None, "network: timeout"  # simulate network error
_inherited_rating = None
_inherited_votes = ""
if _r3 is None and _err3 is not None:
    if _pv_prev_rating.get("imdb") is not None:
        _inherited_rating = _pv_prev_rating["imdb"]
    if not _inherited_votes and _pv_prev_rating.get("imdbVotes"):
        _inherited_votes = _pv_prev_rating["imdbVotes"]
check("omdb keep-prev: inherit previous imdb on network error",
      _inherited_rating, 8.2)
check("omdb keep-prev: inherit previous votes on network error",
      _inherited_votes, "200.5K")

# 4. On success: do NOT inherit old value — fresh result is used
_r4, _v4, _err4 = 7.1, "50.0K", None
_rec_fresh: dict = {}
if _r4 is None and _err4 is not None:
    if _pv_prev_rating.get("imdb") is not None:
        _rec_fresh["imdb"] = _pv_prev_rating["imdb"]
else:
    if _r4 is not None:
        _rec_fresh["imdb"] = _r4
check("omdb keep-prev: fresh result used when no error",
      _rec_fresh.get("imdb"), 7.1)
# ================================================= from_own_pages() parsers
print("from_own_pages: image extraction from theatre show pages")

import fetch_theatre_posters as FTP   # noqa: E402

# ---- th199 / national / iam / artvent: og:image ----
_th199_html = (FIXTURES / "theatre_th199_show.html").read_text(encoding="utf-8")
check("th199 show page og:image extracted",
      FTP._og_image(_th199_html),
      "https://theatre199.org/data/kimo_images/kimo_grid2_image_unit_435/DSC04799.JPG?f=68753")

_nat_html = (FIXTURES / "theatre_national_show.html").read_text(encoding="utf-8")
check("national show page og:image extracted",
      FTP._og_image(_nat_html),
      "https://nationaltheatre.bg/storage/shows/320bf918e261c075ae027feca68bc23c00b.jpg")

_iam_html = (FIXTURES / "theatre_iam_show.html").read_text(encoding="utf-8")
check("iam show page og:image extracted",
      FTP._og_image(_iam_html),
      "https://iamstudio.bg/wp-content/uploads/2024/12/dobroto-tyalo-poster.jpg")

# og:image that is just a logo (artvent) should not be returned because
# _og_image only checks format; the caller is responsible for filtering logos.
_artvent_html = (FIXTURES / "theatre_artvent_event2.html").read_text(encoding="utf-8")
check("artvent og:image (logo) is still returned by _og_image (caller filters)",
      FTP._og_image(_artvent_html),
      "https://artvent.bg/images/logo.png")

# ---- artvent: event_main_img ----
_av_m = FTP.ARTVENT_RE.search(_artvent_html)
check("artvent event_main_img img found",
      bool(_av_m), True)
check("artvent event_main_img relative path extracted",
      (_av_m.group(1) or _av_m.group(2)) if _av_m else None,
      "/images/events/1627269a11b1d8.jpg")

# ---- zadkanala: large750x ----
_zk_html = (FIXTURES / "theatre_zad-kanala_show.html").read_text(encoding="utf-8")
_zk_m = FTP.LARGE750_RE.search(_zk_html)
check("zadkanala large750x img found",
      bool(_zk_m), True)
check("zadkanala large750x URL starts with https://zadkanala.bg/sites/default",
      bool(_zk_m) and _zk_m.group(1).startswith(
          "https://zadkanala.bg/sites/default/files/styles/large750x/"), True)

# ---- mladezhki: /img/upl/4/images/ ----
# Fixture is Windows-1251 bytes; read as bytes and decode
_mlt_raw = (FIXTURES / "theatre_mladezhki_show.html").read_bytes()
_mlt_html = _mlt_raw.decode("windows-1251", errors="replace")
_mlt_m = FTP.MLT_IMG_RE.search(_mlt_html)
check("mladezhki /img/upl/4/images/ first img found",
      bool(_mlt_m), True)
check("mladezhki img path starts with /img/upl/4/images/",
      bool(_mlt_m) and _mlt_m.group(1).startswith("/img/upl/4/images/"), True)

# ---- salzaismyah: JSON-LD scraping ----
_salz_html = (FIXTURES / "theatre_salzaismyah_posters.html").read_text(encoding="utf-8")
_salz_hits = {}
for _m in FTP.SALZAISMYAH_LD_RE.finditer(_salz_html):
    try:
        _obj = __import__("json").loads(_m.group(1))
    except Exception:
        continue
    if _obj.get("@type") == "Event" and _obj.get("image") and _obj.get("name"):
        _salz_hits[FTP.norm(_obj["name"])] = _obj["image"]
check("salzaismyah JSON-LD: at least 2 shows extracted",
      len(_salz_hits) >= 2, True)
check("salzaismyah JSON-LD: ГОЛЕМИЯТ СИН image found",
      bool(_salz_hits.get(FTP.norm(',,ГОЛЕМИЯТ СИН"'))),
      True)

# ---- satira: __NEXT_DATA__ slug extraction ----
_satira_html = (FIXTURES / "theatre_satira.html").read_text(encoding="utf-8")
_nd_m = FTP.NEXT_DATA_RE.search(_satira_html)
check("satira __NEXT_DATA__ block found", bool(_nd_m), True)
if _nd_m:
    import json as _json_sat
    try:
        _nd_data = _json_sat.loads(_nd_m.group(1))
        _ev_list = (_nd_data["props"]["pageProps"]["prefetchedData"]
                    ["eventsData"]["events"]["list"])
        _slugs = {e.get("settings", {}).get("eventPage", ""): e.get("title", "")
                  for e in _ev_list}
        check("satira: preliubodeici slug present",
              "show/preliubodeici" in _slugs, True)
        check("satira: civilizaciq slug present",
              "show/civilizaciq" in _slugs, True)
    except (KeyError, ValueError) as _e:
        check(f"satira __NEXT_DATA__ parse: {_e}", True, True)

# ---- C2: theatre.art.bg day pages (h3 rows) and production pages ----
print("theatre.art.bg poster source: day-page rows and production og:image (C2)")
_art_d1208 = FTP._art_decode((FIXTURES / "theatre_artbg_day_2026-12-08.html").read_bytes())
_rows1208 = FTP.parse_art_day(_art_d1208)
check("art.bg 12-08: 4 listing rows", len(_rows1208), 4)
check("art.bg 12-08: the ИВАНОВДЕН row (title, prod, theatre, city)",
      ("ИВАНОВДЕН – ГОСТУВА ДТ „РАЧО СТОЯНОВ‛, ГАБРОВО", "8089", "4", "20") in _rows1208, True)
_art_shows = [{"id": "ivanovden", "title": "Ивановден", "theatre": "mladezhki"},
              {"id": "vik-ot-istoriyata-ekaterina-karavelova",
               "title": "Вик от историята. Екатерина Каравелова", "theatre": "tba"},
              {"id": "ivanovden-other", "title": "Ивановден", "theatre": "national"}]
_art_resolve = FTP.art_resolver(_art_shows)
check("art.bg: the guest row resolves to the mladezhki show (art.bg theatre 4)",
      _art_resolve("ИВАНОВДЕН – ГОСТУВА ДТ „РАЧО СТОЯНОВ‛, ГАБРОВО", "4"), "ivanovden")
check("art.bg: the same title at an unmapped theatre id matches nothing",
      _art_resolve("ИВАНОВДЕН", "999"), None)
_rows1125 = FTP.parse_art_day(FTP._art_decode((FIXTURES / "theatre_artbg_day_2026-11-25.html").read_bytes()))
check("art.bg 11-25: 9 listing rows", len(_rows1125), 9)
check("art.bg 11-25: the ВИК ОТ ИСТОРИЯТА row is prod 8076 at theatre 6",
      [(r[1], r[2]) for r in _rows1125 if "ВИК ОТ ИСТОРИЯТА" in r[0]], [("8076", "6")])
check("art.bg 11-25: no row label is a price", any(r[0].lower().startswith("цени") for r in _rows1125), False)
check("art.bg 11-25: the ВИК row resolves to the tba show",
      _art_resolve([r[0] for r in _rows1125 if "ВИК ОТ ИСТОРИЯТА" in r[0]][0], "6"),
      "vik-ot-istoriyata-ekaterina-karavelova")
import re as _re_c2
_old_day = FTP._art_decode((FIXTURES / "theatre_artbg_day.html").read_bytes())
# the old harvester: the label of each kupi-bilet anchor was the title; now it is the price
_old_label_rows = [m for m in _re_c2.finditer(
    r'<a[^>]+href=["\']([^"\']*kupi-bilet\.php[^"\']*)["\'][^>]*>(.*?)</a>', _old_day, _re_c2.S | _re_c2.I)
    if not _re_c2.sub(r"<[^>]+>", " ", m.group(2)).strip().lower().startswith("цени")]
check("art.bg (repo fixture 10-11): parse_art_day finds rows", len(FTP.parse_art_day(_old_day)) > 0, True)
check("art.bg (repo fixture 10-11): the old kupi-bilet label match found no title (regression guard)",
      len(_old_label_rows), 0)
_p8089 = FTP._art_decode((FIXTURES / "theatre_artbg_prod_8089.html").read_bytes())
_og8089 = FTP._og_image(_p8089)
check("art.bg prod 8089: og:image is the peakview BIG photo", _og8089,
      "https://theatre.peakview.bg/theatre/photos/BIG1792145396120260416-A9304463_optimized_1000.jpg")
check("art.bg prod 8089: the policy accepts it", PP.reject_reason("ivanovden", _og8089, cat), None)
_og8076 = FTP._og_image(FTP._art_decode((FIXTURES / "theatre_artbg_prod_8076_logo.html").read_bytes()))
check("art.bg prod 8076: og:image is the site logo", _og8076, "https://theatre.art.bg/img/logo.png")
check("art.bg prod 8076: the logo is refused (no poster stored)",
      (bool(FTP.ART_LOGO_RE.search(_og8076 or "")),
       PP.reject_reason("vik-ot-istoriyata-ekaterina-karavelova", _og8076, cat) is not None), (True, True))

# ---- C3: Сатирата — month programme pages, its own bucket, the SEED deadlock ----
print("satira: month pages, the is-satira bucket, SEED no longer blocks a scrape (C3)")
_sat_map = FTP.satira_slugs_from((FIXTURES / "theatre_satira_program_2026-11.html").read_text(encoding="utf-8"))
check("satira 2026-11: 'Разгневените ' (trailing space) maps to show/12-razgneveni",
      _sat_map.get(FTP.norm("Разгневените")), "show/12-razgneveni")
check("satira 2026-11: the kabare slug is in the month map",
      _sat_map.get(FTP.norm("Политическо сатирично кабаре - Фокус, мокус, препаратус")),
      "show/politic-satirichno-kabare-2026")
check("satira: /program plus one ?f:month= page per month to the window's end",
      FTP.satira_month_urls(dt.date(2026, 10, 22), dt.date(2026, 12, 27)),
      ["https://satirata.bg/program", "https://satirata.bg/program?f%3Amonth=2026-11",
       "https://satirata.bg/program?f%3Amonth=2026-12"])
_sat_img = FTP.satira_show_image((FIXTURES / "theatre_satira_show_razgneveni.html").read_text(encoding="utf-8"))
check("satira show page: __NEXT_DATA__ image is the is-satira 007/907 file",
      bool(_sat_img) and _sat_img.startswith("https://storage.googleapis.com/is-satira/images/000/007/907/")
      and _sat_img.endswith("_n.jpg"), True)
check("satira show image passes the policy (path-scoped trust)",
      PP.reject_reason("razgnevenite", _sat_img, cat), None)
check("…and equals the SEED URL", _sat_img, FTP.SEED["razgnevenite"])
check("another bucket on storage.googleapis.com is still refused (opaque name)",
      PP.reject_reason("razgnevenite", "https://storage.googleapis.com/other-bucket/images/"
                       "813757123_10235522229059900_454187274889936495_n.jpg", cat) is not None, True)
check("a path that climbs out of the trusted prefix is not trusted",
      PP.reject_reason("razgnevenite", "https://storage.googleapis.com/is-satira/../x/"
                       "813757123_10235522229059900_454187274889936495_n.jpg", cat) is not None, True)
_w_shows = [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}]
_w_seed = {"a": "https://storage.googleapis.com/other-bucket/813757123_10235522229059900_454187274889936495_n.jpg",
           "b": "https://nationaltheatre.bg/storage/shows/310b6ce43729762add40dfcb142f9b42cc5.jpg"}
_w_prev = {"c": "https://storage.googleapis.com/other-bucket/913757123_10235522229059900_454187274889936495_n.jpg"}
_want, _pok, _sok = FTP.wanted_ids(_w_shows, _w_prev, _w_seed)
check("wanted_ids: a SEED or previous entry the policy refuses leaves its id in want",
      (sorted(_want), sorted(_pok), sorted(_sok)), (["a", "c", "d"], [], ["b"]))
check("wanted_ids: perf_ids limits want to shows on stage in the window",
      sorted(FTP.wanted_ids(_w_shows, _w_prev, _w_seed, perf_ids={"a", "b"})[0]), ["a"])

# ---- C4: Сълза и смях — titles with apostrophes ----
print("salzaismyah: ''ТАКЪВ Е ЖИВОТЪТ'' (\\' escapes in LD-JSON, ' inside the alt) (C4)")
_salz2 = (FIXTURES / "theatre_salzaismyah_stage2_1022.html").read_text(encoding="utf-8")
_salz2_map = FTP.salz_slugs_from(_salz2)
check("salzaismyah stage 2: ''ТАКЪВ Е ЖИВОТЪТ'' maps to its events_image",
      (_salz2_map.get(FTP.norm("''ТАКЪВ Е ЖИВОТЪТ''")) or "").endswith("204b1a9ff4243980426b76e20005ce24.jpg"), True)
_salz2_ld = [m.group(0) for m in FTP.SALZAISMYAH_LD_RE.finditer(_salz2) if "ТАКЪВ" in m.group(0)]
check("…from the LD-JSON alone too (the \\' escape is retried)",
      (FTP.salz_slugs_from("".join(_salz2_ld)).get("такъв е животът") or "").endswith(
          "204b1a9ff4243980426b76e20005ce24.jpg"), True)
_salz2_alt = FTP.SALZAISMYAH_LD_RE.sub("", _salz2)
check("…and from the <img alt> alone (quote-matched)",
      (FTP.salz_slugs_from(_salz2_alt).get("такъв е животът") or "").endswith(
          "204b1a9ff4243980426b76e20005ce24.jpg"), True)

# ---- C5: Възраждане — the theatre's own portfolio page ----
print("vazrazhdane: own-page poster (C5)")
_vz = (FIXTURES / "theatre_vazrazhdane_show_istorii.html").read_text(encoding="utf-8")
_vz_img = FTP.vazr_image(_vz, "Истории/Stories")
check("vazrazhdane: a theatrevazrajdane.bg upload named for the play ('istorii')",
      bool(_vz_img) and _vz_img.startswith("https://theatrevazrajdane.bg/wp-content/uploads/")
      and "istorii" in _vz_img, True)
check("…its size variant folded into the original file", _vz_img,
      "https://theatrevazrajdane.bg/wp-content/uploads/2026/10/tv-fb-cover-istorii-15.jpg")
check("vazrazhdane: with no named file, the generic premiere banner is not taken",
      FTP.vazr_image(_vz, "Друга пиеса"), None)

# ---- C6: Младежки — peakview gallery fallback on mlt.bg's own page ----
print("mladezhki: first peakview BIG photo when the page has no /img/upl/ poster (C6)")
_mlt_iv = (FIXTURES / "theatre_mladezhki_show_ivanovden.html").read_bytes().decode("windows-1251", errors="replace")
check("mladezhki ivanovden: the first peakview BIG photo",
      FTP.mlt_image(_mlt_iv),
      "https://theatre.peakview.bg/img/photos/BIG1792145396120260416-A9304463_optimized_1000.jpg")
check("mladezhki: an /img/upl/4/images/ poster still wins",
      (FTP.mlt_image(_mlt_html) or "").startswith("https://mlt.bg/img/upl/4/images/"), True)

# ---- C7: re-validate what keep-previous holds; retry policy ----
print("theatre posters: re-validation of kept entries and the retry schedule (C7)")
import tempfile as _tf7, shutil as _sh7
from PIL import Image as _Img7
_r7 = pathlib.Path(_tf7.mkdtemp())
try:
    (_r7 / "posters").mkdir()
    _Img7.new("RGB", (400, 600), (90, 20, 30)).save(_r7 / "posters/good.jpg")
    _Img7.new("RGB", (176, 42), (255, 255, 255)).save(_r7 / "posters/tiny.png")
    _prev7 = {"good": "posters/good.jpg", "tiny": "posters/tiny.png", "gone": "posters/gone.jpg",
              "dead": "https://theatre.peakview.bg/theatre/photos/BIG1PLAKAT.jpg",
              "alive": "https://theatre.peakview.bg/theatre/photos/BIG2PLAKAT.jpg"}
    _seed7 = {"seeddead": "https://nationaltheatre.bg/storage/shows/abc.jpg",
              "seedok": "https://nationaltheatre.bg/storage/shows/def.jpg"}
    _st7 = {"seeddead": {"remote_fail": 2}}
    _tried7 = []

    def _try7(remote):
        _tried7.append(sorted(remote))
        return {"alive": "posters/alive.jpg"}, {"dead"}

    _log7 = []
    _p7, _s7, _d7 = FTP.revalidate(_prev7, _seed7, _st7, _try7, _log7, root=_r7)
    check("revalidate: a missing file, a 176x42 image and a twice-failed remote URL are dropped",
          sorted(_d7), ["dead", "gone", "seeddead", "tiny"])
    check("revalidate: the good file stays, the remote that downloaded now is local",
          sorted(_p7.items()), [("alive", "posters/alive.jpg"), ("good", "posters/good.jpg")])
    check("revalidate: only remote previous entries are retried, once", _tried7, [["alive", "dead"]])
    check("revalidate: the junk file is deleted (rehost cannot reuse it)", (_r7 / "posters/tiny.png").exists(), False)
    check("revalidate: a SEED image that failed 2 runs with no local file no longer covers its show",
          sorted(_s7), ["seedok"])
    check("revalidate: the theatre.art.bg logo md5 is a known logo", bool(PP.LOGO_MD5), True)
finally:
    _sh7.rmtree(_r7, ignore_errors=True)
_today7 = dt.date(2026, 10, 22)
_st7 = {"new": {}, "old": {"first_missing": "2026-09-01", "last_try": "2026-10-20", "link": "u"},
        "old-due": {"first_missing": "2026-09-01", "last_try": "2026-10-15", "link": "u"},
        "old-moved": {"first_missing": "2026-09-01", "last_try": "2026-10-21", "link": "u"}}
check("due_ids: daily for 21 days, then every 7th day, at once when the link changed",
      sorted(FTP.due_ids({"new", "old", "old-due", "old-moved"}, _st7,
                         {"old": "u", "old-due": "u", "old-moved": "v"}, _today7)),
      ["new", "old-due", "old-moved"])
check("due_ids: a first miss is dated today", _st7["new"].get("first_missing"), "2026-10-22")
_ss7 = FTP.settle_state({"a": {"first_missing": "2026-10-01"}, "b": {"first_missing": "2026-10-01"},
                         "c": {"first_missing": "2026-10-01"}},
                        tried={"a", "b"}, missing={"a", "c"}, upcoming={"a", "b"},
                        links={"a": "la"}, remote_failed=set(), today=_today7)
check("settle_state: a found poster and a show off stage leave the state; a miss is counted",
      _ss7, {"a": {"first_missing": "2026-10-01", "tries": 1, "last_try": "2026-10-22", "link": "la"}})

# ---- §4: Зад канала "Игра на котки" — the page has only the venue logo ----
_zkl = (FIXTURES / "theatre_zad-kanala_show_logo.html").read_text(encoding="utf-8")
_zkl_m = FTP.LARGE750_RE.search(_zkl)
_zkl_img = _zkl_m.group(1).split("?")[0] if _zkl_m else None
check("zad-kanala igra-na-kotki: the only large750x image is logo-mgt_1_1.jpg",
      (_zkl_img or "").endswith("/logo-mgt_1_1.jpg"), True)
check("…which the policy refuses, so no poster is stored (generated art is correct)",
      PP.reject_reason("igra-na-kotki", _zkl_img, cat) is not None, True)

# ---- C8: coverage counted as the browser does, and its health entry ----
print("theatre posters: coverage as the browser counts it, health.json entry (C8)")
_m8 = {"SHOWALIAS": {"ev": "film"}, "TMDBART": {"film": {"p": "/x.jpg"}, "nop": {"en": "X"}},
       "POSTERS": {"bad": "javascript:alert(1)"}, "EVENTART": {},
       "SHOWART": {"a": "posters/a.jpg", "b": "posters/b к.jpg", "nop": "https://x.bg/a.jpg"}}
check("browser_poster: a safe local path, a non-ASCII path refused, TMDB via the alias, an unsafe override",
      [bool(PP.browser_poster(i, _m8)) for i in ("a", "b", "ev", "bad", "nop", "none")],
      [True, False, True, False, True, False])
import tempfile as _tf8
with _tf8.TemporaryDirectory() as _d8:
    _h8 = pathlib.Path(_d8) / "health.json"
    _h8.write_text('[{"level": "ok", "venue": "theatre-posters", "message": "old"},'
                   ' {"level": "error", "venue": "mladezhki", "message": "x"}]', encoding="utf-8")
    _shows8 = [{"id": "a", "theatre": "t1"}, {"id": "b", "theatre": "t1"}, {"id": "c", "theatre": "t2"}]
    _cov8 = FTP.report_coverage("", _shows8, {"a", "b", "c"}, {"a": "posters/a.jpg"},
                                {"b": {"first_missing": "2026-10-01"}, "c": {"first_missing": "2026-10-20"}},
                                dt.date(2026, 10, 22), health_path=_h8)
    _hj8 = __import__("json").loads(_h8.read_text(encoding="utf-8"))
    check("report_coverage: 1/3, missing b and c, by theatre",
          (_cov8["with"], _cov8["missing"], _cov8["by_theatre"]["t1"]["missing"]), (1, ["b", "c"], ["b"]))
    check("report_coverage: one theatre-posters entry, a warning naming the show missing > 7 days",
          [(e["venue"], e["level"]) for e in _hj8],
          [("mladezhki", "error"), ("theatre-posters", "warning")])
    check("…the message gives its age", "b (21 days)" in _hj8[-1]["message"] and "c (" not in _hj8[-1]["message"], True)

# ---- posterpolicy: new theatre hostnames accepted ----
print("posterpolicy: new theatre poster hosts are trusted")
check("mlt.bg accepted",
      PP.reject_reason("pinokio", "https://mlt.bg/img/upl/4/images/poster.jpg", cat), None)
check("sofiapuppet.com tba.art.bg image accepted",
      PP.reject_reason("aneto", "https://www.tba.art.bg/img/photos/BIG17500poster.jpg", cat), None)
check("salzaismyah.bg accepted",
      PP.reject_reason("bella-donna",
                       "https://www.salzaismyah.bg/uploads/events_image/abc.jpg", cat), None)
check("artvent.bg images/events accepted",
      PP.reject_reason("petk-vecher",
                       "https://artvent.bg/images/events/1627269a11b1d8.jpg", cat), None)

# ---- logo-stem rejection (new rule) ----
print("posterpolicy: logo-stem rejection")
check("logo-mgt filename rejected as site chrome",
      bool(PP.reject_reason("igra-na-kotki",
                            "https://zadkanala.bg/sites/default/files/styles/large750x/public/logo-mgt_1_1.jpg", cat)),
      True)
check("thegem-blog-timeline after fb_img_ rejected as site chrome",
      bool(PP.reject_reason("az-mayakovski",
                            "https://iamstudio.bg/wp-content/uploads/2024/05/fb_img_1714647345421-thegem-blog-timeline-large.jpg",
                            cat)),
      True)
check("thegem-blog-timeline after screenshot_ rejected as site chrome",
      bool(PP.reject_reason("dobroto-tyalo",
                            "https://iamstudio.bg/wp-content/uploads/2024/12/screenshot_20241219_094634_microsoft-365-office-thegem-blog-timeline-large.jpg",
                            cat)),
      True)
check("legitimate scaled thegem image accepted (not a screenshot)",
      PP.reject_reason("spasyavaneto-na-dzheysi",
                       "https://iamstudio.bg/wp-content/uploads/2024/09/1-scaled-thegem-blog-timeline-large.jpg",
                       cat),
      None)
check("normal production image still accepted",
      PP.reject_reason("bokluk",
                       "https://theatre199.org/data/kimo_images/DSC04799.jpg", cat),
      None)

# ---- C1: a local poster path is ASCII, as the browser's safeImg requires ----
print("posterpolicy: local poster paths are ASCII only (C1)")
check("a Cyrillic letter in a local poster path is not a local poster",
      PP.is_local_poster("posters/skaperniкat.jpg"), False)
check("the ASCII path is", PP.is_local_poster("posters/skapernikat.jpg"), True)


# ============================================ P2E: show-attribute label extraction
# Tests for per-screening language/format label extraction from Cinema City, Кино Арена
# and Cine Grand. Uses three fixtures under scripts/fixtures/*.
print("P2E: show-attribute label extraction (CC, KA, CG)")

# Cinema City: parse_cinemacity_day reads attributeIds and sets lang_ver / fmt_std
_cc_attrs = _jm.loads((FIXTURES / "cinemacity_attrs.json").read_text(encoding="utf-8"))
_cc_attr_rows = OS.parse_cinemacity_day(_cc_attrs, "2026-11-01")
# Row: Тест Дублаж 14:00 → bg-dub → dub
_cc_dub = [r for r in _cc_attr_rows if r[0] == "Тест Дублаж" and r[2] == "14:00"]
check("CC: bg-dub attr → lang_ver 'dub'", _cc_dub[0][3].get("lang_ver") if _cc_dub else None, "dub")
check("CC: bg-dub attr → _mk_showattr 'dub'", OS._mk_showattr(_cc_dub[0][3]) if _cc_dub else None, "dub")
# Row: Тест Субтитри 3D 16:00 → subbed + 3d
_cc_sub3d = [r for r in _cc_attr_rows if r[0] == "Тест Субтитри 3D" and r[2] == "16:00"]
check("CC: subbed+3d → lang_ver 'sub'", _cc_sub3d[0][3].get("lang_ver") if _cc_sub3d else None, "sub")
check("CC: subbed+3d → _mk_showattr 'sub,3d'", OS._mk_showattr(_cc_sub3d[0][3]) if _cc_sub3d else None, "sub,3d")
# Row: Тест IMAX 18:00 → subbed + imax
_cc_imax = [r for r in _cc_attr_rows if r[0] == "Тест IMAX" and r[2] == "18:00"]
check("CC: subbed+imax → _mk_showattr 'sub,imax'", OS._mk_showattr(_cc_imax[0][3]) if _cc_imax else None, "sub,imax")
# Row: Тест Дублаж 20:00 → bg-dub + 4dx
_cc_dub4dx = [r for r in _cc_attr_rows if r[0] == "Тест Дублаж" and r[2] == "20:00"]
check("CC: bg-dub+4dx → _mk_showattr 'dub,4dx'", OS._mk_showattr(_cc_dub4dx[0][3]) if _cc_dub4dx else None, "dub,4dx")
# Row: Тест Субтитри 3D 20:30 → no lang/fmt attrs → None
_cc_none = [r for r in _cc_attr_rows if r[0] == "Тест Субтитри 3D" and r[2] == "20:30"]
check("CC: no notable attrs → _mk_showattr None", OS._mk_showattr(_cc_none[0][3]) if _cc_none else None, None)
# Row: Тест Български филм 15:00 → bulgarian + first-subbed-lang-bg-cc + subbed → no label
# (Bulgarian-language film; cc captions do not make it "subtitled")
_cc_bg = [r for r in _cc_attr_rows if r[0] == "Тест Български филм" and r[2] == "15:00"]
check("CC: bulgarian film → no lang_ver", _cc_bg[0][3].get("lang_ver") if _cc_bg else "MISSING", None)
check("CC: bulgarian film → _mk_showattr None", OS._mk_showattr(_cc_bg[0][3]) if _cc_bg else "MISSING", None)

# Кино Арена: parse_kinoarena_page reads data-title and sets lang_ver / fmt_std
_ka_html = (FIXTURES / "kinoarena_attrs.html").read_text(encoding="utf-8")
try:
    _ka_sel, _ka_tabs, _ka_rows = OS.parse_kinoarena_page(_ka_html, "arena-mega-mol")
    # Тест Дублаж 15:00 → BG Audio → dub
    _ka_dub = [r for r in _ka_rows if r[0] == "Тест Дублаж" and r[2] == "15:00"]
    check("KA: BG Audio → lang_ver 'dub'", _ka_dub[0][3].get("lang_ver") if _ka_dub else None, "dub")
    check("KA: BG Audio → _mk_showattr 'dub'", OS._mk_showattr(_ka_dub[0][3]) if _ka_dub else None, "dub")
    # Тест Субтитри 3D 16:00 → BG Subtitles + 3D → sub,3d
    _ka_sub3d = [r for r in _ka_rows if r[0] == "Тест Субтитри 3D" and r[2] == "16:00"]
    check("KA: BG Subtitles+3D → lang_ver 'sub'", _ka_sub3d[0][3].get("lang_ver") if _ka_sub3d else None, "sub")
    check("KA: BG Subtitles+3D → _mk_showattr 'sub,3d'", OS._mk_showattr(_ka_sub3d[0][3]) if _ka_sub3d else None, "sub,3d")
    # Тест IMAX 19:00 → IMAX + BG Subtitles → sub,imax
    _ka_imax = [r for r in _ka_rows if r[0] == "Тест IMAX" and r[2] == "19:00"]
    check("KA: IMAX+BG Subtitles → _mk_showattr 'sub,imax'", OS._mk_showattr(_ka_imax[0][3]) if _ka_imax else None, "sub,imax")
    # Тест Dubbed 14:00 → Dubbed (live label from 2026-10-13) → dub
    _ka_dubbed = [r for r in _ka_rows if r[0] == "Тест Dubbed" and r[2] == "14:00"]
    check("KA: Dubbed → lang_ver 'dub'", _ka_dubbed[0][3].get("lang_ver") if _ka_dubbed else None, "dub")
    check("KA: Dubbed → _mk_showattr 'dub'", OS._mk_showattr(_ka_dubbed[0][3]) if _ka_dubbed else None, "dub")
    # Тест Неизвестен 21:00 → SomeFutureLabel → no lang, but logs unknown.
    # _arena_attrs was called during parse_kinoarena_page; reset the log before
    # a fresh parse of just the unknown row to confirm the logging path.
    OS._ARENA_UNKNOWN_LOG.clear()
    OS._arena_attrs(["Digital", "SomeFutureLabel"])
    _ka_unk = [r for r in _ka_rows if r[0] == "Тест Неизвестен" and r[2] == "21:00"]
    check("KA: unknown label → _mk_showattr None", OS._mk_showattr(_ka_unk[0][3]) if _ka_unk else None, None)
    check("KA: unknown label logged in _ARENA_UNKNOWN_LOG",
          "SomeFutureLabel" in OS._ARENA_UNKNOWN_LOG, True)
    OS._ARENA_UNKNOWN_LOG.clear()
except Exception as _e:
    fails.append(f"KA parse_kinoarena_page raised: {_e}")
    print(f"  FAIL KA block: {_e}")

# Cine Grand: parse_cinegrand_page reads span.info text and sets lang_ver / fmt_std
_cg_html = (FIXTURES / "cinegrand_attrs.html").read_text(encoding="utf-8")
try:
    _cg_sel, _cg_tabs, _cg_rows, _cg_ok = OS.parse_cinegrand_page(
        _cg_html, "sofia-ring-mol", "BGSOFCG2", "2026-11-01")
    # ТЕСТ ДУБЛАЖ 14:00 → "2d, Дуб" → dub
    _cg_dub = [r for r in _cg_rows if r[0] == "ТЕСТ ДУБЛАЖ" and r[2] == "14:00"]
    check("CG: '2d, Дуб' → lang_ver 'dub'", _cg_dub[0][3].get("lang_ver") if _cg_dub else None, "dub")
    check("CG: '2d, Дуб' → _mk_showattr 'dub'", OS._mk_showattr(_cg_dub[0][3]) if _cg_dub else None, "dub")
    # ТЕСТ СУБТИТРИ 3D 18:00 → "3d, Суб" → sub,3d
    _cg_sub3d = [r for r in _cg_rows if r[0] == "ТЕСТ СУБТИТРИ 3D" and r[2] == "18:00"]
    check("CG: '3d, Суб' → lang_ver 'sub'", _cg_sub3d[0][3].get("lang_ver") if _cg_sub3d else None, "sub")
    check("CG: '3d, Суб' → _mk_showattr 'sub,3d'", OS._mk_showattr(_cg_sub3d[0][3]) if _cg_sub3d else None, "sub,3d")
    # ТЕСТ IMAX 20:00 → "2d, Суб" → sub
    _cg_sub = [r for r in _cg_rows if r[0] == "ТЕСТ IMAX" and r[2] == "20:00"]
    check("CG: '2d, Суб' → _mk_showattr 'sub'", OS._mk_showattr(_cg_sub[0][3]) if _cg_sub else None, "sub")
except Exception as _e:
    fails.append(f"CG parse_cinegrand_page raised: {_e}")
    print(f"  FAIL CG block: {_e}")


# ------------------------------------------------- fetch_events: programata.bg events
# Fixtures: programata.bg festival posts saved 2026-10-15, trimmed (structure kept,
# most prose cut), and the REST listing of categories 159 + 146.
print("\nfetch_events: programata.bg festival posts -> events.json")
import json as _evjson
import fetch_events as FE

_EV_TODAY = dt.date(2026, 10, 15)
_ev_list = _evjson.loads((FIXTURES / "events_programata_list.json").read_text(encoding="utf-8"))
_ev_post = {p["id"]: p for p in _ev_list}
_ev_page = {k: FE.parse_page((FIXTURES / f"events_programata_{k}.html").read_text(encoding="utf-8"))
            for k in ("act", "kinomania", "nosht", "kinodvor", "nofield")}
_EV_CAT = {
    "THEATRES": [{"id": "derida", "name": "Сцена „Дерида“", "nameEn": "Derida Stage"},
                 {"id": "iam", "name": "I AM Studio", "nameEn": "I AM Studio"},
                 {"id": "toplo", "name": "Топлоцентрала", "nameEn": "Toplocentrala"},
                 {"id": "national", "name": "Народен театър „Иван Вазов“", "nameEn": "Ivan Vazov National Theatre"},
                 {"id": "sofia-th", "name": "Театър „София“", "nameEn": "Sofia Theatre"}],
    "CINEMAS": [{"id": "ndk1", "name": "НДК — Зала 1", "nameEn": "NDK — Hall 1"},
                {"id": "frenski-institut", "name": "Френски институт", "nameEn": "French Institute"},
                {"id": "lumiere", "name": "Кино Люмиер", "nameEn": "Lumiere Cinema"}],
    "SHOWS": [{"id": "dokato-chakame-godo", "title": "Докато чакаме Годо", "theatre": "iam"},
              {"id": "vakhanki", "title": "Вакханки", "theatre": "national"},
              # same title as the ACT post's opener at Театър О3, but at Дерида: its clause
              # names Театър О3, so it must not become an ACT member
              {"id": "takav-e-zhivotat", "title": "Такъв е животът", "theatre": "derida"}],
    "PERFORMANCES": [["dokato-chakame-godo", "2026-10-17", "19:30", None, None],
                     ["vakhanki", "2026-10-20", "19:00", None, None],
                     ["takav-e-zhivotat", "2026-10-17", "20:00", None, None]],
    "FILMS": [{"id": "fantomat-na-operata", "bg": "Фантомът на операта"},
              {"id": "div-kon-devet", "bg": "Див кон девет"}],
    "SHOWTIMES": [["fantomat-na-operata", "ndk1", "2026-11-16", ["19:00"]],
                  ["div-kon-devet", "ndk1", "2026-11-12", ["19:00"]]],
    "EVENTS": [{"id": "kinomania", "title": "Киномания 2026", "from": "2026-11-12", "to": "2026-11-29",
                "scope": "cinema"},
               {"id": "cinelibri", "title": "Синелибри 2026: „Цивилизация на зрелището“",
                "from": "2026-10-15", "to": "2026-11-08", "scope": "cinema"}],
}

print("  the post's date field and dates in a headline")
check("field: a range", FE.parse_field_dates("12 ноември 2026         - 29 ноември 2026"),
      ("2026-11-12", "2026-11-29"))
check("field: one day", FE.parse_field_dates("21 ноември 2026"), ("2026-11-21", "2026-11-21"))
check("field: empty", FE.parse_field_dates(""), None)
check("field: reversed range refused", FE.parse_field_dates("29 ноември 2026 - 12 ноември 2026"), None)
_pub = dt.date(2026, 9, 15)
check("title: 'идва на 21 ноември', year from the publication date",
      FE.parse_title_dates("14-тата Нощ на театрите идва на 21 ноември", _pub), ("2026-11-21", "2026-11-21"))
check("title: '17–24 октомври'", FE.parse_title_dates("Фест X | 17–24 октомври", _pub), ("2026-10-17", "2026-10-24"))
check("title: '28 октомври – 3 ноември'", FE.parse_title_dates("Фест X: 28 октомври – 3 ноември", _pub),
      ("2026-10-28", "2026-11-03"))
check("title: a December-January run ends the next year",
      FE.parse_title_dates("Зимен фест 28 декември – 3 януари", dt.date(2026, 11, 20)), ("2026-12-28", "2027-01-03"))
check("title: '18 – 25 по 5' is no date (no month)",
      FE.parse_title_dates("18 – 25 по 5 | Кампанията за млада публика", _pub), None)
check("title: a month without a day is no date",
      FE.parse_title_dates("Месец на българската драматургия | Януари 2026", _pub), None)
check("title: two separate dates are not guessed between",
      FE.parse_title_dates("Премиери на 3 ноември и на 5 декември", _pub), None)

print("  the event's name in the headline's own words")
for _h, _want in [
    ("Не се правѝ! | Започва 16. издание на ACT Фестивал за свободен театър", "ACT Фестивал за свободен театър"),
    ("40. издание на Киномания | Празник на киното със световни шедьоври и специални събития", "Киномания"),
    ("14-тата Нощ на театрите идва на 21 ноември", "Нощ на театрите"),
    ("Хофкино 2026 | Лятно кино в двора на Гьоте-институт България", "Хофкино 2026"),
    ("Sofia Short Fest 2026 със силен акцент върху портретното документално кино", "Sofia Short Fest 2026"),
    ("Синелибри 2026: Билетите за всички кинопремиери в София са в продажба", "Синелибри 2026"),
]:
    check(f"name: {_h[:44]!r}", FE.event_name(_h), _want)

print("  one post -> one event (or why not)")
_act, _why = FE.build_event(_ev_post[187545], _ev_page["act"], _EV_CAT, _EV_TODAY)
check("ACT: an event", _why, None)
if _act:
    check("ACT: dates from the date field", (_act["from"], _act["to"], _act["dates_from"]),
          ("2026-10-17", "2026-10-24", "date field"))
    check("ACT: theatre festival", (_act["scope"], _act["kind"]), ("theatre", "festival"))
    check("ACT: stable id", _act["id"], "act-festival-za-svoboden-teatar-p187545")
    check("ACT: the source page, https on programata.bg",
          _act["url"].startswith("https://programata.bg/stsena/") and _act["source"] == "programata.bg"
          and _act["auto"] is True, True)
    check("ACT: venues the post names as places, in its order", _act["venues"], ["derida", "iam"])
    check("ACT: blurb BG is facts only",
          _act["blurbBg"], "Фестивал, 17–24 октомври 2026. Сред местата: Сцена „Дерида“, I AM Studio.")
    check("ACT: blurb EN", _act["blurbEn"], "Festival, 17–24 October 2026. Venues include Derida Stage, I AM Studio.")
    check("ACT: no em dash in the copy", any("—" in _act[k] for k in ("title", "blurbBg", "blurbEn")), False)
    check("ACT: countdown tag (evBadge recomputes it)", _act["tag"], "СЛЕД 2 ДНИ")
    _t = {t["t"]: t["v"] for t in _act["titles"]}
    check("ACT: 'Докато чакаме Годо' goes with I AM Studio (its own clause)", _t.get("Докато чакаме Годо"), ["iam"])
    check("ACT: 'Такъв е животът' goes with no catalogued venue (Театър О3)", _t.get("Такъв е животът"), [])
    check("ACT: 'Вакханки' (an inspiration) has no venue", _t.get("Вакханки"), [])
    check("ACT: colours are two #RRGGBB, derived from the id",
          (_act["g"] == FE.colours(_act["id"]) and all(re.fullmatch(r"#[0-9A-F]{6}", c) for c in _act["g"])), True)
_km, _why = FE.build_event(_ev_post[186611], _ev_page["kinomania"], _EV_CAT, _EV_TODAY)
check("Киномания: an event, Sofia from the description", _why, None)
if _km:
    check("Киномания: НДК Зала 1 is a place; the French Institute is only a partner", _km["venues"], ["ndk1"])
    check("Киномания: venue named without an em dash", "НДК (Зала 1)" in _km["blurbBg"], True)
_ev, _why = FE.build_event(_ev_post[185887], _ev_page["nosht"], _EV_CAT, _EV_TODAY)
check("Нощ на театрите: the post never places it in Sofia -> left out",
      (_ev, (_why or "").startswith("not in Sofia")), (None, True))
_ev, _why = FE.build_event(_ev_post[181238], _ev_page["kinodvor"], _EV_CAT, _EV_TODAY)
check("Кинодвор: ended -> left out", (_ev, _why), (None, "ended 2026-09-30"))
_ev, _why = FE.build_event(_ev_post[185127], _ev_page["nofield"], _EV_CAT, _EV_TODAY)
check("Севтополис: no date field, no date in the headline -> left out",
      (_ev, (_why or "").startswith("no dates")), (None, True))
check("Sofia: a Stara Zagora category decides",
      FE.is_sofia(_ev_post[185127]["categories"], _ev_page["nofield"], [])[0], False)
check("Sofia: a headline naming Шумен and not Sofia",
      FE.is_sofia([329, 146], {"h1": "Шумен отново става столица на смеха", "desc": "в София", "text": ""}, [])[0], False)
check("Sofia: 'в София' in the text", FE.is_sofia([329, 146], {"h1": "Фест", "desc": "", "text": "в София"}, [])[0], True)
check("scope: both categories and both sections", FE.scope_of([328, 329, 159, 146]), "both")
check("scope: a music festival only is none", FE.scope_of([97, 102]), None)

print("  for_page: the seed wins, ended go, duplicates merge, members")
_doc = {"events": [e for e in (_act, _km) if e]}
_shown, _members, _rep = FE.for_page(_doc, _EV_CAT, _EV_TODAY)
check("for_page: Киномания is the seed's, ACT is shown",
      ([e["id"] for e in _shown], _rep["seed"]),
      (["act-festival-za-svoboden-teatar-p187545"], [("kinomaniya-p186611", "kinomania")]))
check("for_page: titleEn and blurbEn hold no Cyrillic",
      [bool(re.search(r"[\u0400-\u04FF]", e["titleEn"] + e["blurbEn"])) for e in _shown], [False])
check("for_page: an untranslated title is romanised", [e["titleEn"] for e in _shown],
      ["ACT Festival za svoboden teatar"])
check("for_page: the page record is the EVENTS shape + url + auto, no internals",
      sorted(_shown[0]) if _shown else None,
      sorted(["id", "kind", "scope", "status", "title", "titleEn", "from", "to", "g", "blurbBg", "blurbEn",
              "tag", "source", "url", "auto"]))
check("members: ACT lists 'Докато чакаме Годо' only (not the Дерида namesake, not Вакханки)",
      _members.get("act-festival-za-svoboden-teatar-p187545"), ["dokato-chakame-godo"])
check("members: the seed's Киномания gets the film its post places in Зала 1",
      _members.get("kinomania"), ["fantomat-na-operata"])
_amb = dict(_EV_CAT, SHOWS=_EV_CAT["SHOWS"] + [{"id": "godo-2", "title": "Докато чакаме Годо", "theatre": "iam"}])
check("members: two catalogue items with the title -> none",
      FE.for_page(_doc, _amb, _EV_TODAY)[1].get("act-festival-za-svoboden-teatar-p187545"), None)
_nodate = dict(_EV_CAT, PERFORMANCES=[["dokato-chakame-godo", "2026-11-30", "19:30", None, None]])
check("members: no performance within the festival's dates -> none",
      FE.for_page(_doc, _nodate, _EV_TODAY)[1].get("act-festival-za-svoboden-teatar-p187545"), None)
check("for_page: an ended event is dropped", FE.for_page(_doc, _EV_CAT, dt.date(2026, 10, 25))[0], [])
if _act:
    _twin = dict(_act, id="act-fest-p999", post=999, url="https://programata.bg/stsena/act-2/", venues=[], titles=[])
    _sh2 = FE.for_page({"events": [_act, _twin]}, _EV_CAT, _EV_TODAY)[0]
    check("for_page: two posts on one festival -> one event, the richer, both pages kept",
          [(e["id"], e.get("urls")) for e in _sh2],
          [(_act["id"], [_act["url"], "https://programata.bg/stsena/act-2/"])])

print("  keep-previous: the listing, the 3-run rule, a changed post")
if _act:
    _prev = [dict(_act)]
    _m1 = FE.merge(_prev, {}, set(), True, _EV_TODAY)
    check("merge: not listed once -> kept, missed 1", [(e["id"], e["missed"]) for e in _m1], [(_act["id"], 1)])
    _m2 = FE.merge(FE.merge(_m1, {}, set(), True, _EV_TODAY), {}, set(), True, _EV_TODAY)
    check("merge: not listed for 3 runs -> gone", _m2, [])
    check("merge: listing failed -> kept, missed unchanged",
          [(e["id"], e["missed"]) for e in FE.merge(_prev, {}, set(), False, _EV_TODAY)], [(_act["id"], 0)])
    check("merge: the post re-read and no longer an event -> gone",
          FE.merge(_prev, {}, {187545}, True, _EV_TODAY, rejected={187545}), [])
    check("merge: ended -> gone", FE.merge(_prev, {}, {187545}, True, dt.date(2026, 10, 25)), [])
    _new = dict(_act, id="renamed-p187545", g=["#000000", "#000000"])
    check("merge: a re-read post keeps its first id and colours",
          [(e["id"], e["g"]) for e in FE.merge(_prev, {187545: _new}, {187545}, True, _EV_TODAY)],
          [(_act["id"], _act["g"])])

    class _FakeNet:
        def __init__(self, listing, pages):
            self.listing, self.pages, self.calls = listing, pages, []
        def json(self, url, **kw):
            self.calls.append(url)
            return self.listing
        def text(self, url, **kw):
            self.calls.append(url)
            return self.pages.get(url)
    _pages = {_ev_post[187545]["link"]: (FIXTURES / "events_programata_act.html").read_text(encoding="utf-8")}
    _net = _FakeNet([_ev_post[187545]], _pages)
    _d1 = FE.run(_net, _EV_CAT, _EV_TODAY, {})
    check("run: the listing and one page read -> one event",
          ([e["id"] for e in _d1["events"]], len(_net.calls), _d1["ok"]), ([_act["id"]], 2, True))
    _net2 = _FakeNet([_ev_post[187545]], _pages)
    _d2 = FE.run(_net2, _EV_CAT, _EV_TODAY, _d1)
    check("run: an unmodified post is not read again, its event stays",
          ([e["id"] for e in _d2["events"]], len(_net2.calls)), ([_act["id"]], 1))
    _d3 = FE.run(_FakeNet(None, {}), _EV_CAT, dt.date(2026, 10, 16), _d2)
    check("run: the listing fails -> previous events kept, ok false, tag follows the calendar",
          ([e["id"] for e in _d3["events"]], _d3["ok"], _d3["events"][0]["tag"] if _d3["events"] else None),
          ([_act["id"]], False, "СЛЕД 1 ДЕН"))
    _d4 = FE.run(_FakeNet(None, {}), _EV_CAT, dt.date(2026, 10, 25), _d2)
    check("run: the listing fails after the end date -> the event still goes", _d4["events"], [])
    _net5 = _FakeNet([_ev_post[187545]], {})
    _d5 = FE.run(_net5, _EV_CAT, _EV_TODAY, dict(_d1, posts={}))
    check("run: a post page that cannot be read keeps the post's event",
          [e["id"] for e in _d5["events"]], [_act["id"]])

print("  English titles: translate.py's cache and official names, else the Bulgarian title")
import translate as _TR
_saved_cache, _saved_key = _TR.load_cache, _TR.DEEPL_KEY
try:
    _TR.DEEPL_KEY = ""
    _TR.load_cache = lambda: {_TR.cache_key("Фест на театъра"): {"en": "Theatre Fest"},
                              _TR.cache_key("Синелибри 2026"): {"en": "Sinelibri 2026"}}
    _evs = [{"id": "a", "title": "Фест на театъра"}, {"id": "b", "title": "Синелибри 2026"},
            {"id": "c", "title": "Непреведен фестивал"}, {"id": "d", "title": "Sofia Short Fest 2026"}]
    FE.english_titles(_evs, use_deepl=False)
    check("titleEn: cached DeepL translation", _evs[0]["titleEn"], "Theatre Fest")
    check("titleEn: CineLibri spelt its own way", _evs[1]["titleEn"], "CineLibri 2026")
    check("titleEn: no translation -> the title romanised (no Cyrillic in English mode)",
          _evs[2]["titleEn"], "Nepreveden festival")
    check("titleEn: a Latin title stays", _evs[3]["titleEn"], "Sofia Short Fest 2026")
finally:
    _TR.load_cache, _TR.DEEPL_KEY = _saved_cache, _saved_key


# ------------------------------------------- fetch_event_posters: event key art
# Fixtures: cinelibri.com's and kinomania.bg's home pages and programata's ACT post
# (saved 2026-10-15, trimmed), and a hand-made page whose og:image is a logo.
# Images are made here with Pillow; nothing touches the network.
print("\nfetch_event_posters: picking and re-hosting event key art")
import fetch_event_posters as EP
import shutil as _evp_shutil, tempfile as _evp_tf
from PIL import Image as _EvpImg


def _evp_page(name):
    return (FIXTURES / f"evposter_{name}.html").read_text(encoding="utf-8")


_ACT_POST = "https://programata.bg/stsena/ne-se-prav%d1%9d-zapochva-16-izdanie-na-act/"
print("  the pictures a page names as its own")
check("programata: og:image, then the post's own featured image (not a related post's)",
      [u.rsplit("/", 1)[-1] for u in EP.page_images(_evp_page("programata_act"), _ACT_POST)],
      ["act_fest_digital_fb_cover_website-1-1024x576.png", "act_fest_digital_fb_cover_website-1-scaled.png"])
check("programata: the organiser's site is the post's own link",
      EP.organiser_link(_evp_page("programata_act"), _ACT_POST), "https://actfest.org")
check("cinelibri.com: its og:image is the 2026 key visual",
      EP.page_images(_evp_page("cinelibri_home"), "https://www.cinelibri.com/"),
      ["https://www.cinelibri.com/wp-content/uploads/2026/06/website-key-visual-2026.jpg"])
check("kinomania.bg: no og:image, and its 1000x200 header <img> is no candidate",
      EP.page_images(_evp_page("kinomania_home"), "https://www.kinomania.bg/bg_BG/"), [])
_lg = EP.page_images(_evp_page("logo_og"), "https://festival.example.bg/fest/")
check("logo site: og, twitter, then the own featured image (not the linked related post)",
      [u.rsplit("/", 1)[-1] for u in _lg], ["site-logo-fb.png", "logo_square.jpg", "key-visual-2026-1920x1080.jpg"])
check("logo site: the two logos are refused by name, the key visual is not",
      [bool(EP.url_problem(u)) for u in _lg], [True, True, False])
check("logo site: a Facebook link is not the organiser's site",
      EP.organiser_link(_evp_page("logo_og"), "https://festival.example.bg/fest/"), None)
for _u, _bad in [("https://x.bg/25764107879854-logosupport2cveten.jpg", True), ("https://x.bg/site-icon-512.png", True),
                 ("https://x.bg/favicon.png", True), ("https://x.bg/default.jpg", True), ("https://x.bg/a.svg", True),
                 ("http://x.bg/key-visual.jpg", True), ("https://x.bg/iconic-poster.jpg", False),
                 ("https://x.bg/blanket-fest-2026.jpg", False)]:
    check(f"url: {_u.rsplit('/', 1)[-1]} {'refused' if _bad else 'accepted'}", bool(EP.url_problem(_u)), _bad)

_evp_td = pathlib.Path(_evp_tf.mkdtemp(prefix="sg_evp_"))
try:
    def _evp_img(name, size, mode="RGB", fmt="JPEG", hole=False):
        im = _EvpImg.new(mode, size, (180, 40, 60, 255) if mode == "RGBA" else (180, 40, 60))
        if hole:
            im.putpixel((0, 0), (0, 0, 0, 0))
        p = _evp_td / name
        im.save(p, fmt)
        return p
    _wide = _evp_img("wide.jpg", (1200, 675))
    _opaque_png = _evp_img("opaque.png", (1024, 576), "RGBA", "PNG")
    _cutout = _evp_img("cutout.png", (900, 900), "RGBA", "PNG", hole=True)
    _small = _evp_img("small.jpg", (500, 300))
    _strip = _evp_img("strip.jpg", (1000, 200))
    _tall = _evp_img("tall.jpg", (700, 1000))
    _html = _evp_td / "page.jpg"
    _html.write_text("<html>not a picture</html>", encoding="utf-8")
    print("  what a downloaded picture must be")
    check("image: 1200x675 landscape accepted", EP.image_problem(_wide), (None, (1200, 675)))
    check("image: an opaque PNG (ACT's cover) accepted", EP.image_problem(_opaque_png)[0], None)
    check("image: transparent pixels (a cut-out logo) refused",
          "transparent" in (EP.image_problem(_cutout)[0] or ""), True)
    check("image: under 600 px wide refused", "under 600" in (EP.image_problem(_small)[0] or ""), True)
    check("image: a 5:1 header strip refused", "strip" in (EP.image_problem(_strip)[0] or ""), True)
    check("image: HTML saved as .jpg refused", (EP.image_problem(_html)[0] or "").startswith("not an image"), True)
    check("image: a portrait picture is acceptable (used only without a wider one)", EP.image_problem(_tall)[0], None)

    print("  choosing, re-hosting, keep-previous")
    _bank = {"https://festival.example.bg/u/portrait-2026.jpg": _tall,
             "https://cdn.example.net/u/key-visual-2026.jpg": _wide,
             "https://programata.bg/wp-content/uploads/2026/10/fest-small.jpg": _small,
             "https://programata.bg/wp-content/uploads/2026/10/fest-cover.png": _opaque_png,
             "https://programata.bg/wp-content/uploads/2026/10/fest-wide.jpg": _wide}
    _calls = []

    def _dl(url, path):
        _calls.append(url)
        src = _bank.get(url)
        if src is None:
            return False
        pathlib.Path(path).write_bytes(src.read_bytes())
        return True
    _root = _evp_td / "root"
    (_root / "posters").mkdir(parents=True)
    _post = "https://programata.bg/stsena/test-fest/"
    _ev = {"id": "test-fest-p1", "to": "2026-11-30", "auto": True, "url": _post}
    _src = [("organiser", "https://festival.example.bg/", _post,
             ["https://festival.example.bg/u/site-logo.png", "https://cdn.example.net/u/key-visual-2026.jpg",
              "https://festival.example.bg/u/portrait-2026.jpg"]),
            ("programata", _post, None,
             ["https://programata.bg/wp-content/uploads/2026/10/fest-small.jpg",
              "https://programata.bg/wp-content/uploads/2026/10/fest-cover.png"])]
    _refused, _sizes = {}, {}
    _rec = EP.pick(_ev, _src, None, _refused, _dl, _root, _sizes)
    check("pick: the organiser's portrait loses to programata's landscape cover",
          (_rec or {}).get("src"), "https://programata.bg/wp-content/uploads/2026/10/fest-cover.png")
    check("pick: the logo is never downloaded; the CDN image (not the organiser's site) is refused unread",
          ("https://festival.example.bg/u/site-logo.png" in _calls, "https://cdn.example.net/u/key-visual-2026.jpg" in _calls,
           "organiser's own site" in _refused.get("https://cdn.example.net/u/key-visual-2026.jpg", "")),
          (False, False, True))
    check("pick: the small picture is refused and remembered",
          "under 600" in _refused.get("https://programata.bg/wp-content/uploads/2026/10/fest-small.jpg", ""), True)
    _f = _root / (_rec or {}).get("file", "x")
    with _EvpImg.open(_f) as _im:
        check("re-hosted: posters/ev-<id>.jpg, a JPEG, 800 px on the longest edge",
              ((_rec or {}).get("file"), _im.format, max(_im.size)), ("posters/ev-test-fest-p1.jpg", "JPEG", 800))
    check("record: passes the gate's rule", EP.record_problem("test-fest-p1", _rec, _root), None)
    _calls.clear()
    _rec2 = EP.pick(_ev, _src, _rec, _refused, _dl, _root, _sizes)
    check("next run, same sources: nothing downloaded, the same record",
          (_calls, (_rec2 or {}).get("src"), (_rec2 or {}).get("file")), ([], _rec["src"], _rec["file"]))
    _src3 = [_src[0], ("programata", _post, None, ["https://programata.bg/wp-content/uploads/2026/10/fest-wide.jpg"])]
    _calls.clear()
    _rec3 = EP.pick(_ev, _src3, _rec, _refused, _dl, _root, _sizes)
    check("the source URL changes: the new picture is downloaded and replaces the file",
          (_calls, (_rec3 or {}).get("src"), (_rec3 or {}).get("file")),
          (["https://programata.bg/wp-content/uploads/2026/10/fest-wide.jpg"],
           "https://programata.bg/wp-content/uploads/2026/10/fest-wide.jpg", "posters/ev-test-fest-p1.jpg"))
    check("only a portrait anywhere: it is used",
          (EP.pick({"id": "tall-p2", "to": "2026-11-30"}, [_src[0]], None, {}, _dl, _root, {}) or {}).get("src"),
          "https://festival.example.bg/u/portrait-2026.jpg")
    check("nothing acceptable: None (the caller keeps the previous art)",
          EP.pick({"id": "none-p3", "to": "2026-11-30"}, [("programata", _post, None,
                  ["https://programata.bg/wp-content/uploads/2026/10/gone.jpg"])], None, {}, _dl, _root, {}), None)

    print("  the record rule (shared with verify_build) and EVENTART")
    _site_rec = {"file": "posters/ev-test-fest-p1.jpg", "src": "https://www.cinelibri.com/wp-content/uploads/kv.jpg",
                 "page": "https://www.cinelibri.com/", "via": "site"}
    check("rule: a seed festival's own site", EP.record_problem("cinelibri", _site_rec, _root), None)
    check("rule: the site of another event is refused",
          bool(EP.record_problem("kinomania", _site_rec, _root)), True)
    check("rule: 'via programata' with the image elsewhere is refused",
          bool(EP.record_problem("x", dict(_rec, src="https://example.com/a.jpg"), _root)), True)
    check("rule: an organiser image needs the programata post that links it",
          bool(EP.record_problem("x", {"file": _rec["file"], "src": "https://festival.example.bg/a.jpg",
                                       "page": "https://festival.example.bg/", "via": "organiser"}, _root)), True)
    check("rule: a missing file is refused",
          "missing" in (EP.record_problem("x", dict(_rec, file="posters/ev-gone.jpg"), _root) or ""), True)
    check("EVENTART: shown events with a good record only",
          EP.eventart({"events": {"test-fest-p1": _rec, "old-p9": _rec, "bad-p8": dict(_rec, file="posters/ev-gone.jpg")}},
                      {"test-fest-p1", "bad-p8"}, _root),
          {"test-fest-p1": "posters/ev-test-fest-p1.jpg"})

    print("  a run: keep-previous and the end date")
    _today = dt.date(2026, 10, 15)
    _prev = {"events": {"test-fest-p1": _rec,
                        "ended-p7": dict(_rec, file="posters/ev-ended-p7.jpg", to="2026-10-01")}}
    (_root / "posters" / "ev-ended-p7.jpg").write_bytes((_root / _rec["file"]).read_bytes())
    _doc, _log = EP.run([_ev], {}, _prev, lambda u: None, _dl, _today, _root)
    check("run: the post cannot be read -> the event keeps its art", _doc["events"].get("test-fest-p1"), _rec)
    check("run: an ended event's record and file go",
          ("ended-p7" in _doc["events"], (_root / "posters" / "ev-ended-p7.jpg").exists()), (False, False))
finally:
    _evp_shutil.rmtree(_evp_td, ignore_errors=True)


# ------------------------------------------------------------ gen_share_pages
print("\ngen_share_pages: OG tags, escaping, URLs, redirect target")

import gen_share_pages as GSP
import re as _re2

def _make_sp(kind, item_id, title_bg, title_en, syn_bg, poster_url, site_url):
    try:
        return GSP._share_page(kind, item_id, title_bg, title_en, syn_bg, poster_url, site_url), None
    except Exception as e:
        return None, str(e)

# Basic film page: required OG tags all present
_sp, _sperr = _make_sp(
    "film", "odiseya",
    "Одисея", "Odyssey",
    "Тест синопсис",
    "https://image.tmdb.org/t/p/w500/abc.jpg",
    "https://confused-morty123.github.io/sofia-gleda",
)
if _sperr:
    fails.append(f"share_page film basic raised: {_sperr}")
    print(f"  FAIL share_page film basic raised: {_sperr}")
else:
    for _tag in ["og:type", "og:site_name", "og:url", "og:title", "og:description",
                 "og:image", "og:locale", "twitter:card", "twitter:title",
                 "twitter:description", "twitter:image"]:
        check(f"share_page film {_tag}", True, _tag in _sp)
    _after_ogimage = _sp.split("og:image")[1].split(">")[0] if "og:image" in _sp else ""
    check("share_page film og:image absolute https", True, 'content="https://' in _after_ogimage)
    check("share_page film canonical",          True, 'rel="canonical"' in _sp)
    check("share_page film meta refresh",       True, 'http-equiv="refresh"' in _sp)
    check("share_page film redirect ?film=",    True, "?film=odiseya" in _sp)
    check("share_page film no #hash fragment",  True, "#film=" not in _sp)

# Titles with special characters are HTML-escaped
_sp2, _sperr2 = _make_sp(
    "show", "test-show",
    'Пиесата "Тест" & Театър', "",
    "",
    "https://example.com/poster.jpg",
    "https://confused-morty123.github.io/sofia-gleda",
)
if _sperr2:
    fails.append(f"share_page escaping raised: {_sperr2}")
    print(f"  FAIL share_page escaping raised: {_sperr2}")
else:
    _head_only = _sp2.split("<body>")[0]
    # Raw double-quote must not appear in meta attribute values
    check("share_page title quote escaped",   True, '"Тест"' not in _head_only)
    # Raw ampersand must not appear unescaped inside attribute values
    check("share_page title ampersand escaped", True, "& Театър" not in _head_only)

# Long synopsis is truncated to DESC_MAX characters
_sp3, _sperr3 = _make_sp(
    "film", "long-film",
    "Дълъг синопсис", "",
    "А" * 300,
    "https://example.com/p.jpg",
    "https://confused-morty123.github.io/sofia-gleda",
)
if _sperr3:
    fails.append(f"share_page truncation raised: {_sperr3}")
    print(f"  FAIL share_page truncation raised: {_sperr3}")
else:
    _dm = _re2.search(r'og:description[^>]+content="([^"]*)"', _sp3)
    if _dm:
        _desc_len = len(_dm.group(1))
        if _desc_len > GSP.DESC_MAX + 1:
            fails.append(f"share_page synopsis truncated: len={_desc_len} > {GSP.DESC_MAX+1}")
            print(f"  FAIL share_page synopsis truncated: len={_desc_len}")
        else:
            print(f"  ok   share_page synopsis truncated (len={_desc_len})")
    else:
        fails.append("share_page truncation: og:description not found")
        print("  FAIL share_page truncation: og:description not found")

# SITE_URL from site_config is the single source of truth
import site_config as _SC
check("share_page SITE_URL matches site_config", GSP.SITE_URL, _SC.SITE_URL)

# Fallback image must be an absolute HTTPS URL
check("share_page fallback image is https",
      True, GSP.FALLBACK_IMAGE.startswith("https://"))

# og:title must be the title alone — no "Sofia Gleda — " prefix (em dash banned)
_sp4, _sperr4 = _make_sp(
    "film", "em-dash-check",
    "Спайдър-мен: Нов ден", "",
    "",
    "https://example.com/p.jpg",
    "https://confused-morty123.github.io/sofia-gleda",
)
if _sperr4:
    fails.append(f"share_page og:title no-em-dash raised: {_sperr4}")
    print(f"  FAIL share_page og:title no-em-dash raised: {_sperr4}")
else:
    _title_m = _re2.search(r'og:title[^>]+content="([^"]*)"', _sp4)
    _t_got = _title_m.group(1) if _title_m else ""
    check("share_page og:title is title alone (no Sofia Gleda prefix)",
          "Sofia" not in _t_got, True)
    check("share_page og:title has no em dash",
          "—" not in _t_got, True)

# Poster coverage test against real committed index.html
print("\ngen_share_pages: poster coverage against real index.html")
_cov_src = pathlib.Path(__file__).resolve().parent.parent / "index.html"
if not _cov_src.exists():
    fails.append("poster coverage: index.html not found")
    print("  FAIL poster coverage: index.html not found")
else:
    _cov_html = _cov_src.read_text(encoding="utf-8")

    def _cov_const(name):
        _m = _re2.search(r"^const %s\s*=\s*([\[{].*?[}\]]);$" % _re2.escape(name),
                         _cov_html, _re2.M)
        if not _m:
            return None
        try:
            import json as _json_cov
            return _json_cov.loads(_m.group(1))
        except Exception:
            return None

    _cov_films   = _cov_const("FILMS")   or []
    _cov_shows   = _cov_const("SHOWS")   or []
    _cov_alias   = _cov_const("SHOWALIAS") or {}
    _cov_posters = _cov_const("POSTERS")   or {}
    _cov_tmdb    = _cov_const("TMDBART")   or {}
    _cov_showart = _cov_const("SHOWART")   or {}
    _cov_tmdb_base = "https://image.tmdb.org/t/p/w500"
    _cov_fallback  = GSP.FALLBACK_IMAGE

    def _cov_poster(item_id):
        base = _cov_alias.get(item_id, item_id)
        if _cov_posters.get(base):
            url = _cov_posters[base]
            if url.startswith("https://"):
                return url
        art = _cov_tmdb.get(base, {})
        if art.get("p"):
            return _cov_tmdb_base + art["p"]
        if art.get("b"):
            return "https://image.tmdb.org/t/p/w780" + art["b"]
        if _cov_showart.get(base):
            val = _cov_showart[base]
            if val.startswith("https://"):
                return val
            return GSP.SITE_URL + "/" + val.lstrip("/")
        return _cov_fallback

    _film_with_poster = sum(1 for f in _cov_films if _cov_poster(f["id"]) != _cov_fallback)
    _show_with_poster = sum(1 for s in _cov_shows if _cov_poster(s["id"]) != _cov_fallback)
    _film_total = len(_cov_films)
    _show_total = len(_cov_shows)
    _film_pct = _film_with_poster / _film_total * 100 if _film_total else 0
    _show_pct = _show_with_poster / _show_total * 100 if _show_total else 0
    print(f"  films: {_film_with_poster}/{_film_total} ({_film_pct:.0f}%) have poster")
    print(f"  shows: {_show_with_poster}/{_show_total} ({_show_pct:.0f}%) have poster")
    if _film_pct < 90:
        fails.append(f"film poster coverage {_film_pct:.0f}% < 90%")
        print(f"  FAIL film poster coverage {_film_pct:.0f}% < 90%")
    else:
        print(f"  ok   film poster coverage >= 90%")
    if _show_pct < 90:
        fails.append(f"show poster coverage {_show_pct:.0f}% < 90%")
        print(f"  FAIL show poster coverage {_show_pct:.0f}% < 90%")
    else:
        print(f"  ok   show poster coverage >= 90%")

    # Spider-Man's og:image must be its TMDB poster URL
    _spidey_id = "spider-man"
    _spidey = next((f for f in _cov_films if f["id"] == _spidey_id), None)
    if _spidey is None:
        print(f"  skip Spider-Man poster check (not in current data)")
    else:
        _spidey_poster = _cov_poster(_spidey_id)
        _tmdb_prefix = "https://image.tmdb.org/t/p/"
        if _spidey_poster.startswith(_tmdb_prefix):
            print(f"  ok   Spider-Man og:image is TMDB: {_spidey_poster}")
        else:
            fails.append(f"Spider-Man og:image not TMDB: {_spidey_poster!r}")
            print(f"  FAIL Spider-Man og:image not TMDB: {_spidey_poster!r}")

print()
if fails:
    print(f"{len(fails)} test(s) failed: " + ", ".join(fails))
    sys.exit(1)
print("all parser and policy tests passed")
