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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bs4 import BeautifulSoup                                  # noqa: E402
import scrape_programs as S                                    # noqa: E402
import posterpolicy as PP                                      # noqa: E402

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

print()
if fails:
    print(f"{len(fails)} test(s) failed: " + ", ".join(fails))
    sys.exit(1)
print("all parser and policy tests passed")
