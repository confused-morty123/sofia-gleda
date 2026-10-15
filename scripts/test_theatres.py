#!/usr/bin/env python3
"""Offline tests for official_theatres.py — every theatre's own programme.
No network, about a second.

Every scripts/fixtures/theatre_* file is a trimmed copy of the live page as it
stood on 2026-10-08, kept in the site's own encoding (windows-1251 for mlt.bg,
sofiapuppet.com, sfumato.info, theatre.art.bg and tba.art.bg). The tests pin
what the 2026-10-08 audit found wrong:

  * Artvent touring dates (Враца, Смолян …) filed as Sofia — and a Sofia date
    at another hall filed at Театър ARTVENT;
  * I AM Studio's Кай (24 Oct) and София (18 Oct) swapped;
  * Сатирата's UTC timestamps on both sides of the 25 Oct 2026 clock change;
  * the Youth / Puppet theatres read from their month pages (their calendar
    popup takes a 0-based month and answered October with November);
  * ТБА: the programme and the ticket centre merged, disagreements reported;
  * a half-published month is never treated as covered.

    python3 scripts/test_theatres.py
"""
import datetime as dt
import json
import re
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
FIX = HERE / "fixtures"
sys.path.insert(0, str(HERE))
from bs4 import BeautifulSoup                                  # noqa: E402
import official_theatres as O                                  # noqa: E402

fails = []
TODAY = dt.date(2026, 10, 8)                 # a Thursday
TZ = O.sofia_tz()


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}: got {got!r}, wanted {want!r}")
        fails.append(name)


def raises(name, exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc:
        print(f"  ok   {name}")
        return
    except Exception as e:                                     # wrong exception
        print(f"  FAIL {name}: raised {type(e).__name__}: {e}")
        fails.append(name)
        return
    print(f"  FAIL {name}: did not raise {exc.__name__}")
    fails.append(name)


def raw(name):
    return (FIX / name).read_bytes()


def fx(name):
    return O.decode(raw(name))


def brief(rows):
    return [(r[0], r[1], r[2]) for r in rows]


class FakeResponse:
    def __init__(self, body):
        self.content, self.status_code = body, 200


class FakeNet:
    """url → bytes; any other url is unreachable (None), like netfetch.Fetcher.get."""
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url, **kw):
        self.asked.append(url)
        body = self.pages.get(url)
        return FakeResponse(body) if body is not None else None


# ------------------------------------------------------------------- helpers
print("helpers")
for word, want in [("октомври", 10), ("Октомври", 10), ("Окт", 10), ("Ное.", 11),
                   ("ноем", 11), ("Дек", 12), ("юни", 6), ("юли", 7), ("Зала", None), ("ок", None)]:
    check(f"bg_month {word!r}", O.bg_month(word), want)
check("weekday matches (Thu 8 Oct 2026)", O.weekday_conflict(TODAY, "08 октомври, четвъртък"), False)
check("weekday contradicts", O.weekday_conflict(TODAY, "08 Октомври (Петък)"), True)
check("no weekday printed", O.weekday_conflict(TODAY, "08.10"), False)
check("year: January after October", O.nearest_date(5, 1, TODAY), dt.date(2027, 1, 5))
check("year: earlier this month", O.nearest_date(1, 10, TODAY), dt.date(2026, 10, 1))
check("year: December", O.nearest_date(31, 12, TODAY), dt.date(2026, 12, 31))
check("times: several in one line", O.times_in("11.00 часа, Камерна сцена 12.30 часа"), ["11:00", "12:30"])
check("times: a dotted date is not a time", O.times_in("08.10.2026 19:00"), ["19:00"])
check("times: ticket-centre cell", O.times_in("08-10-2026 г. 19:00 ч."), ["19:00"])
check("decode windows-1251", O.decode("Програма".encode("cp1251")), "Програма")
check("decode UTF-8 that claims windows-1251", O.decode("Феята Ванилия".encode("utf-8")), "Феята Ванилия")
check("strip_quotes: wrapped", O.strip_quotes('"Фейк"'), "Фейк")
check("strip_quotes: partial quotes kept", O.strip_quotes('"Хитранка Лисанка" Спектакъл'), '"Хитранка Лисанка" Спектакъл')
check("split_guest", O.split_guest("Тяло в лед - Гостува ДТ-Русе"), ("Тяло в лед", "ДТ-Русе"))
check("split_guest: lower-case 'гостува'", O.split_guest('Лекция №3 за "СИЛАТА НА СЛОВОТО" гостува ДТ-Пловдив'),
      ('Лекция №3 за "СИЛАТА НА СЛОВОТО"', "ДТ-Пловдив"))
check("split_guest: own production", O.split_guest("Фейк"), ("Фейк", None))

# --------------------------------------------------------- title normalisation
print("show-title normalisation")
N = O.normalise_show_title
for a, b in [
    ("ИСТОРИЯ НА ЕДНА СТРАСТ I ПРЕМИЕРА", "История на една страст"),     # Топлоцентрала's "I" separator
    ("Господин Колперт премиера", "ГОСПОДИН КОЛПЕРТ"),
    ("Жената пита ChatGPT предпремиера", "Жената пита ChatGPT"),
    ("Прелюбодейци | ПРЕМИЕРА", "Прелюбодейци"),
    ("Хамлет - гостуване", "Хамлет"),
    ("Тяло в лед - Гостува ДТ-Русе", "Тяло в лед"),
    ("„Фейк“", '"Фейк"'),
    ("Светици и перверзници 16+", "Светици и перверзници"),
    ("НАШАТА ГОЛЯМА ФРЕНСКА СВАТБА - ПРЕДСТАВЛЕНИЕ 200", "Нашата голяма френска сватба"),
    ("СПАНАК С КАРТОФИ (ОТМЕНЕНО)", "Спанак с картофи"),
    ("Бaлдахинът", "Балдахинът"),                                         # Latin 'a' inside
    ("Защото на мама така ѝ харесва", "ЗАЩОТО НА МАМА ТАКА И ХАРЕСВА"),   # ѝ (Възраждане) vs И (art.bg)
    ("E.E.", "Е.Е."),                                                     # Latin / Cyrillic
    ("Иван Ланджев. За неизбежната случайност. последно представление",
     "ИВАН ЛАНДЖЕВ. ЗА НЕИЗБЕЖНАТА СЛУЧАЙНОСТ."),
]:
    check(f"same: {a[:40]!r}", N(a), N(b))
for a, b in [
    ("Отчаяни съпрузи", "Отчаяни съпрузи 2: Бракувани"),
    ("АСТ ФЕСТ 2026 I История на една страст", "История на една страст"),
    ("Баща ми се казва Мария", "Баща ми се казва Мария - на село"),
    ("Inter Alia", "Интер Алиа"),
]:
    check(f"different: {a[:22]!r} / {b[:22]!r}", N(a) != N(b), True)
check("digits kept", N("12-те гневни"), "12 те гневни")
check("English words not cyrillised", N("The Tempest"), "the tempest")

print("matching to SHOWS of the same theatre")
SHOWS = [
    {"id": "beket", "title": "Програма Бекет: Последната лента. Не аз", "theatre": "sfumato"},
    {"id": "litseto", "title": 'ПРОГРАМА "ИЗГОНВАНЕТО НА БЕСОВЕТЕ" ЛИЦЕТО НА БЛИЖНИЯ', "theatre": "sfumato"},
    {"id": "feyk", "title": "Фейк", "titleEn": "Fake", "theatre": "tba"},
    {"id": "hamlet-tba", "title": "Хамлет", "theatre": "tba"},
    {"id": "hamlet-nat", "title": "Хамлет", "theatre": "national"},
    {"id": "baldahinat", "title": "Балдахинът", "theatre": "tba"},
    {"id": "baldahinat-2", "title": "Бaлдахинът", "theatre": "tba"},
]
IDX = O.show_index(SHOWS)
check("Сфумато cycle: quotes vs colon", O.match_show("sfumato", "Програма „Бекет” Последната лента. Не АЗ.", IDX), ["beket"])
check("Сфумато cycle: prefix on both sides",
      O.match_show("sfumato", 'Програма "Изгонването на бесовете" Лицето на ближния', IDX), ["litseto"])
check("via titleEn", O.match_show("tba", "FAKE", IDX), ["feyk"])
check("same theatre only", O.match_show("tba", '"Хамлет"', IDX), ["hamlet-tba"])
check("homoglyph duplicate surfaces as ambiguous", O.match_show("tba", "БАЛДАХИНЪТ", IDX), ["baldahinat", "baldahinat-2"])
check("unknown title → new production", O.match_show("tba", "Дама пика", IDX), [])

# ------------------------------------------------------------------- coverage
print("coverage — a thin later month is not covered")


def mk(date, time="19:00", title="X"):
    return O.mkrow(title, date, time)


octo = [mk(f"2026-10-{d:02d}") for d in range(8, 32)]                # 1 a day
nov_full = [mk(f"2026-11-{d:02d}") for d in range(1, 31)]
nov_thin = [mk(f"2026-11-{d:02d}") for d in (3, 10, 17, 24)]
check("complete month extends coverage", O.complete_until(octo + nov_full, TODAY), "2026-11-30")
check("thin month stops coverage", O.complete_until(octo + nov_thin, TODAY), "2026-10-31")
check("a month gap stops coverage", O.complete_until(octo + [mk("2026-12-05")], TODAY), "2026-10-31")
nov_quiet = [mk(f"2026-11-{d:02d}") for d in range(1, 31, 2)]          # 15 evenings, every week to the 29th
nov_front = [mk(f"2026-11-{d:02d}") for d in range(1, 16)]             # 15, all in the first half
check("a quieter month published right through is complete (Сълза и смях, Топлоцентрала)",
      O.complete_until(octo + nov_quiet, TODAY), "2026-11-29")
check("…the same count entered only to mid-month is not",
      O.complete_until(octo + nov_front, TODAY), "2026-10-31")
check("…nor a thin one (Възраждане's December: eight evenings to the 19th)",
      O.complete_until(octo + [mk(f"2026-11-{d:02d}") for d in (2, 9, 16, 23, 27)], TODAY), "2026-10-31")
check("spread: every week and the last seven days", (O.spread_through([r[1] for r in nov_quiet], 2026, 11),
      O.spread_through([r[1] for r in nov_front], 2026, 11)), (True, False))
res = O.finish("x", "s", octo + nov_thin + [mk("2026-10-01")], TODAY)
check("finish: past dropped, thin month → extra_rows",
      (res["covered_from"], res["covered_to"], len(res["rows"]), len(res["extra_rows"])),
      ("2026-10-08", "2026-10-31", 24, 4))
raises("finish: nothing left → Unavailable", O.Unavailable, O.finish, "x", "s", [mk("2026-10-01")], TODAY)

print("coverage — a stage that is regular in the next four weeks must not vanish from a later month")
# Сълза и смях as published on 2026-10-09: the main stage every evening, the
# chamber stage (Славянска беседа) on 9 October dates — then 21 November
# evenings on the main stage, every week to the 30th, and ONE chamber date.
OPEN, CHAMBER = "Открита сцена СЪЛЗА И СМЯХ", "Камерна сцена СЛАВЯНСКА БЕСЕДА"
s_oct = ([O.mkrow("A", f"2026-10-{d:02d}", "19:00", stage=OPEN) for d in range(8, 32)]
         + [O.mkrow("B", f"2026-10-{d:02d}", "19:30", stage=CHAMBER) for d in (9, 14, 15, 20, 22, 23, 27, 30)]
         + [O.mkrow("B", "2026-10-27", "20:00", stage=CHAMBER)])
s_nov = ([O.mkrow("A", f"2026-11-{d:02d}", "19:00", stage=OPEN)
          for d in (1, 3, 6, 7, 8, 9, 11, 12, 13, 15, 16, 18, 20, 21, 24, 25, 26, 27, 28, 29, 30)]
         + [O.mkrow("B", "2026-11-05", "19:30", stage=CHAMBER)])
bare = [O.mkrow(r[0], r[1], r[2]) for r in s_oct + s_nov]
check("the count rules alone accept that November (22 evenings, every week to the 30th)",
      O.complete_until(bare, TODAY), "2026-11-30")
why = []
check("…but its chamber stage nearly vanished (1 date against 9): November is half-entered",
      O.complete_until(s_oct + s_nov, TODAY, why), "2026-10-31")
check("…and the reason is given", why,
      ["2026-11: not completely published — камерна сцена славянска беседа: 1 performance(s) against 9 "
       "in the 24 days before it"])
res = O.finish("salzaismyah", "s", s_oct + s_nov, TODAY)
check("finish: November becomes extra (preliminary) rows, the reason a note",
      (res["covered_to"], len(res["extra_rows"]), res["notes"][0][:8]), ("2026-10-31", 22, "2026-11:"))
check("regular = playing in ≥ 3 of the next four weeks; its rate per day",
      sorted((s, round(r * O.REF_DAYS)) for s, r in O.regular_stages(s_oct + s_nov, TODAY).items()),
      [("камерна сцена славянска беседа", 9), ("открита сцена сълза и смях", 26)])
check("the published hall is the stage when no other is given",
      O.stage_of(O.mkrow("X", "2026-11-01", "19:00", hall=" Зала  1 ")), "зала 1")
# Топлоцентрала as published on 2026-10-09: November is quieter in every hall,
# but each of its regular halls still plays — a published month.
hall_days = {"Зала 1": ((9, 10, 11, 14, 16, 17, 18, 21, 23, 24, 25, 28, 30), (1, 4, 6, 8, 11, 13, 15, 18, 20, 22, 25, 27)),
             "Зала 2": ((8, 9, 10, 12, 13, 15, 16, 17, 19, 20, 22, 23, 24, 26, 27, 29), (2, 5, 7, 9, 12, 14, 17, 19, 21, 24, 28)),
             "Сцена бар": ((10, 17, 24, 31), (14, 28))}
toplo = [O.mkrow(h, f"2026-{m}-{d:02d}", "19:00", hall=h)
         for h, (o, n) in hall_days.items() for m, ds in (("10", o), ("11", n)) for d in ds]
check("every regular hall still playing → the quieter month is complete (Топлоцентрала)",
      O.complete_until(toplo, TODAY), "2026-11-28")
fest = [O.mkrow("F", f"2026-10-{d:02d}", "20:00", hall="Фоайе") for d in (10, 11, 12, 13, 14)]
check("a stage busy in one week only is not regular: its quiet November proves nothing",
      O.complete_until(toplo + fest, TODAY), "2026-11-28")

# ===================================================================== national
print("national — nationaltheatre.bg/bg/programa")
nat = fx("theatre_national.html")
rows, exc, blocks = O.parse_national(nat, 2026, 10)
check("all blocks read", blocks, 4)
check("rows", brief(rows), [("Есенна соната", "2026-10-08", "19:00"),
                           ("Теремин: Музика, любов и шпионаж", "2026-10-10", "19:00"),
                           ("СВИДЕТЕЛ НА ОБВИНЕНИЕТО", "2026-11-23", "19:00")])
check("stage", rows[0][3]["hall"], "Камерна сцена")
check("visiting production flagged", rows[2][3].get("guest"), "Гостуващ")
check("ticket deep link", rows[0][3]["url"],
      "https://www.nationaltheatre.bg/bg/predstavlenie/esenna-sonata/2026-10-08|19:00:00")
check("no stage (ВАКХАНКИ at НДК) → excluded", brief(exc), [("ВАКХАНКИ", "2026-10-09", "19:00")])


def keep_blocks(html, keep):
    soup = BeautifulSoup(html, "lxml")
    for b in soup.select("div.show"):
        if not keep(b):
            b.decompose()
    return str(soup).encode("utf-8")


net = FakeNet({O.NATIONAL_URL.format(y=2026, m=10): keep_blocks(nat, lambda b: not b.select_one(".guest-label")),
               O.NATIONAL_URL.format(y=2026, m=11): keep_blocks(nat, lambda b: bool(b.select_one(".guest-label")))})
res = O.fetch_national(net, TODAY)
check("fetch: covered", (res["covered_from"], res["covered_to"]), ("2026-10-08", "2026-10-10"))
check("fetch: rows", brief(res["rows"]), [("Есенна соната", "2026-10-08", "19:00"),
                                         ("Теремин: Музика, любов и шпионаж", "2026-10-10", "19:00")])
check("fetch: a one-show November is extra, not covered", brief(res["extra_rows"]),
      [("СВИДЕТЕЛ НА ОБВИНЕНИЕТО", "2026-11-23", "19:00")])
check("fetch: page holding another month's rows is refused",
      O.fetch_venue("national", FakeNet({O.NATIONAL_URL.format(y=2026, m=10): nat.encode("utf-8")}), TODAY), None)

# ===================================================================== sofia-th
print("sofia-th — sofiatheatre.eu/program")
listing, mobile, nxt = O.parse_sofia_th(fx("theatre_sofia-th.html"))
check("table rows", [(e["title"], e["date"].isoformat(), e["time"]) for e in listing],
      [("Последният страстен любовник", "2026-10-08", "19:00"), ("Любов и други аварии", "2026-10-20", None),
       ("Чудните приключения на Пинокио", "2026-10-25", "11:00")])
check("sold-out table row", listing[1]["sold_out"], True)
check("cards", [(c["title"], c["day"], c["time"], c["label"]) for c in mobile],
      [("Последният страстен любовник", 8, "19:00", "Представление"),
       ("Любов и други аварии", 20, "19:00", "Изчерпани билети"),
       ("Чудните приключения на Пинокио", 25, "11:00", "Представление")])
check("next page", nxt, "http://sofiatheatre.eu/program?page=2")
net = FakeNet({O.SOFIA_TH_URL.format(y=2026, m=10): raw("theatre_sofia-th.html"),
               "http://sofiatheatre.eu/program?page=2": b"<html><body></body></html>"})
res = O.fetch_sofia_th(net, TODAY)
check("fetch: rows", brief(res["rows"]), [("Последният страстен любовник", "2026-10-08", "19:00"),
                                         ("Любов и други аварии", "2026-10-20", "19:00")])
check("fetch: stage without the theatre's name", res["rows"][0][3]["hall"], "Камерна сцена")
check("fetch: sold-out row gets stage+time from its card",
      (res["rows"][1][3]["hall"], res["rows"][1][3].get("sold_out")), ("Камерна сцена", True))
check("fetch: co-production staged at Сълза и смях excluded", [(e[0], e[1], e[3].split(" (")[0]) for e in res["excluded"]],
      [("Чудните приключения на Пинокио", "2026-10-25", "staged at Театър СЪЛЗА И СМЯХ")])
check("fetch: page 2 followed", "http://sofiatheatre.eu/program?page=2" in net.asked, True)

# ======================================================================== th199
print("th199 — theatre199.org/bg/schedule (GET window)")
listing, featured, more = O.parse_th199(fx("theatre_th199.html"), TODAY)
check("list", brief(listing), [("КАКТО В НАЙ-ДОБРИТЕ ДНИ", "2026-10-09", "19:30"), ("БОКЛУК", "2026-10-10", "19:30"),
                               ("ЗАСЕКРЕТЕНО ИЗСЛЕДВАНЕ", "2026-10-11", "19:30")])
check("today's featured card", featured[:3], ("ТОРТИЛА ФЛЕТ", "2026-10-08", "19:30"))
check("more pages (Livewire)", more, True)
res = O.fetch_th199(FakeNet({O.TH199_URL: raw("theatre_th199.html")}), TODAY)
check("fetch: last listed day may be partial → not covered", (res["covered_from"], res["covered_to"]),
      ("2026-10-09", "2026-10-10"))
check("fetch: today + the edge day are extra rows", [r[1] for r in res["extra_rows"]], ["2026-10-08", "2026-10-11"])

# =================================================================== zad-kanala
print("zad-kanala — zadkanala.bg/programa/YYYY-MM")
rows = O.parse_zadkanala(fx("theatre_zad-kanala.html"), TZ)
check("rows (offset +03:00 then +02:00)", brief(rows), [("СЛУЧАЯТ ДЖЕМ", "2026-10-08", "19:00"), ("ВЕЛИКА", "2026-10-24", "19:00"),
                                                         ("СЛУЧАЯТ ДЖЕМ", "2026-10-25", "19:00")])
check("detail link", rows[0][3]["url"], "https://zadkanala.bg/spektakli/sluchayat-dzhem")
bad = fx("theatre_zad-kanala.html").replace("2026-10-24T19:00:00+03:00", "2026-10-24T20:00:00+03:00")
raises("ISO attribute and printed text must agree", O.MarkupError, O.parse_zadkanala, bad, TZ)

# ================================================================== vazrazhdane
print("vazrazhdane — theatrevazrajdane.bg/programa/")
rows, exc = O.parse_vazrazhdane(fx("theatre_vazrazhdane.html"), TODAY)
check("first rows", brief(rows[:4]), [("Двама в делириум", "2026-10-08", "19:00"), ("Обикновено чудо", "2026-10-09", "19:00"),
                                      ("Трите прасенца", "2026-10-11", "11:00"), ("Честна мускетарска", "2026-10-16", "17:00")])
check("visiting production", rows[0][3].get("guest"), "гостуващ спектакъл")
check("children's, sold out", (rows[2][3].get("kind"), rows[2][3].get("sold_out")), ("за деца", True))
check("premiere category", rows[3][3]["premiere"], True)
check("'08 Дек вторник' → 2026-12-08", [r[1] for r in rows if r[1] >= "2026-12"], ["2026-12-08", "2026-12-09"])
check("all in Sofia at Възраждане", exc, [])
soup = BeautifulSoup(fx("theatre_vazrazhdane.html"), "lxml")
soup.select_one(".event_entase_location_cityName").string = "Plovdiv"
_, exc = O.parse_vazrazhdane(str(soup), TODAY)
check("a date in another city is excluded", [(e[0], e[1], e[3]) for e in exc],
      [("Двама в делириум", "2026-10-08", "at Театър Възраждане, Plovdiv")])

# ==================================================================== mladezhki
print("mladezhki — mlt.bg/programa.php (windows-1251)")
rows = O.parse_mlt(fx("theatre_mladezhki.html"), 2026, 10)
check("rows", [(r[0], r[1], r[2], r[3]["hall"]) for r in rows],
      [("Лисабон", "2026-10-07", "19:00", "Камерна сцена"), ("E.E.", "2026-10-08", "19:00", "Голяма сцена"),
       ("Феята Ванилия", "2026-10-10", "11:00", "Камерна сцена"),
       ("Бременските музиканти", "2026-10-10", "11:00", "Голяма сцена")])
raises("page heading must name the month asked for", O.MarkupError, O.parse_mlt, fx("theatre_mladezhki.html"), 2026, 11)
check("1-based month URL (the popup is 0-based)", O.MLT_URL.format(y=2026, m=10),
      "https://mlt.bg/programa.php?year=2026&month=10")

# ======================================================================= kuklen
print("kuklen — sofiapuppet.com/programa.php (windows-1251)")
rows, exc = O.parse_kuklen(fx("theatre_kuklen.html"), 2026, 10)
check("rows", brief(rows), [("ФРАНКЕНЩАЙН", "2026-10-08", "19:00"), ("Алиса в страната на чудесата", "2026-10-10", "11:00"),
                           ("НЕБИВАЛИЦИ С БУКВИ", "2026-10-03", "11:00"), ("Когато цъфне маргаритката", "2026-10-04", "11:00")])
check("hall", (rows[0][3]["hall"], rows[2][3]["hall"]), ("Салон “Ген. Гурко 14”", "Салон “Я. Сакъзов 19”"))
check("audience label kept apart from the title", rows[0][3].get("kind"), "за възрастни")
check("'Представления на други сцени' excluded", brief(exc), [("Златка, златното момиче", "2026-10-18", "11:00")])
raises("page heading must name the month asked for", O.MarkupError, O.parse_kuklen, fx("theatre_kuklen.html"), 2026, 9)

# ======================================================================= satira
print("satira — satirata.bg/program (__NEXT_DATA__, UTC)")
rows = O.parse_satira(fx("theatre_satira.html"), TZ)
check("UTC → Sofia, 16:30Z on 8 Oct and 24 Oct (EEST), 17:30Z on 26 Oct (EET)", brief(rows),
      [("Криворазбраната цивилизация", "2026-10-08", "19:30"), ("Светици и перверзници 16+", "2026-10-24", "19:30"),
       ("Прелюбодейци предпремиера", "2026-10-26", "19:30")])
check("stage names from the rendered table", [r[3]["hall"] for r in rows],
      ["Голяма сцена", "Камерна зала „Методи Андонов“", "Голяма сцена"])
check("price and ticket link", (rows[0][3]["price"][:20], rows[0][3]["url"]), ("31.29 лв./16.00 €, 3", "https://epaygo.bg/1205336983"))
check("pre-premiere flagged, not premiere", (rows[2][3].get("preview"), rows[2][3]["premiere"]), (True, False))


def next_data(events, pages=1):
    nd = {"props": {"pageProps": {"prefetchedData": {"eventsData": {"events": {
        "list": events, "pagination": {"pagesCount": pages}}}}}}}
    return '<html><body><script id="__NEXT_DATA__" type="application/json">' + json.dumps(nd) + "</script></body></html>"


edge = [{"title": "A", "startsAt": "2026-10-24T21:30:00Z", "settings": {"stage": "stage-main"}},
        {"title": "B", "startsAt": "2026-10-25T00:30:00Z", "settings": {"stage": "stage-main"}},
        {"title": "C", "startsAt": "2026-10-25T01:30:00Z", "settings": {"stage": "stage-main"}}]
check("the night of the switch: 21:30Z → 00:30, 00:30Z → 03:30 EEST, 01:30Z → 03:30 EET",
      brief(O.parse_satira(next_data(edge), TZ)),
      [("A", "2026-10-25", "00:30"), ("B", "2026-10-25", "03:30"), ("C", "2026-10-25", "03:30")])
raises("no tz database → refuse, never guess", O.Unavailable, O.parse_satira, next_data(edge), None)
raises("a second page is never silently ignored", O.MarkupError, O.parse_satira, next_data(edge, pages=2), TZ)

# ================================================================== salzaismyah
print("salzaismyah — salzaismyah.bg/site/calendar")
rows, exc = O.parse_salza(fx("theatre_salzaismyah.html"), 2026, 10)
check("today's links", [r[:3] for r in rows if r[1] == "2026-10-08"],
      [("ПЪТУВАНЕ ДО МАРС", "2026-10-08", "19:00"), ("“Как господин Мокинпот се спаси от нещастието”", "2026-10-08", "19:30")])
check("booking link", rows[[r[1] for r in rows].index("2026-10-08")][3]["url"], "https://www.salzaismyah.bg/site/selector/3106")
check("odd minutes kept", [r[2] for r in rows if r[0] == "АЗ ДОСАДНИКЪТ"], ["19:05"])
check("cancelled (text-danger / ОТМЕНЕНО) excluded", [(e[0], e[1]) for e in exc],
      [("ЛЮБОВЪРТЕЖ", "2026-10-06"), ("СПАНАК С КАРТОФИ", "2026-10-10")])
rows, exc = O.parse_salza(fx("theatre_salzaismyah_11.html"), 2026, 11)
check("empty days are bare cells", brief(rows), [("ЖЕНА МИ СЕ КАЗВА БОРИС", "2026-11-01", "19:00")])
raises("grid must be the month asked for", O.MarkupError, O.parse_salza, fx("theatre_salzaismyah.html"), 2026, 11)

print("salzaismyah — per-stage pages /site/events/N (the grid does not name the stage)")
stg = [O.parse_salza_stage(fx(f"theatre_salzaismyah_stage{n}.html")) for n in (1, 2, 3)]
check("each page names its stage", [s for s, _ in stg],
      ["Открита сцена СЪЛЗА И СМЯХ", "Камерна сцена СЛАВЯНСКА БЕСЕДА", "ЪНДЪРГРАУНД - Сцена"])
check("performances: wall time and the grid's selector id",
      stg[1][1], [("2026-10-09", "19:30", "3151", "ЖЕНСКА ЛОГИКА"), ("2026-10-30", "19:30", "3123", "ЖЕНИ ГОВОРЯТ ЗА..."),
                  ("2026-11-05", "19:30", "3182", "“Как господин Мокинпот се спаси от нещастието”")])
check("box-office-only and cancelled cards still carry their selector id (schema.org offers)",
      [(e[0], e[2]) for e in stg[0][1]], [("2026-10-10", "3148"), ("2026-10-10", "3147"), ("2026-11-01", "3159")])
check("an empty stage", stg[2][1], [])
raises("a page without its stage heading is not a stage page", O.MarkupError, O.parse_salza_stage,
       fx("theatre_salzaismyah.html"))
grid, gexc = [], []
for f, m in (("theatre_salzaismyah.html", 10), ("theatre_salzaismyah_11.html", 11)):
    r_, e_ = O.parse_salza(fx(f), 2026, m)
    grid += r_
    gexc += e_
unplaced, unlisted = O.salza_stage_rows(grid, stg, TODAY, gexc)
on = {(r[1], r[2]): r[3].get("stage") for r in grid}
check("grid rows put on their stage by selector id (a box-office-only one too)",
      (on[("2026-10-09", "19:30")], on[("2026-10-10", "19:05")], on[("2026-11-01", "19:00")]),
      (CHAMBER, OPEN, OPEN))
slot_row = O.mkrow("ЖЕНИ ГОВОРЯТ ЗА...", "2026-10-30", "19:30")       # a grid entry without its link
O.salza_stage_rows([slot_row], stg, TODAY)
check("…or, without a link, by the only stage playing at that date and time", slot_row[3].get("stage"), CHAMBER)
check("later grid rows on no stage page are reported (these pages are trimmed)",
      brief(unplaced), [("НЕ ТЕ ПОЗНАВАМ ВЕЧЕ", "2026-10-09", "19:00"),
                        ("БАЩА МИ СЕ КАЗВА МАРИЯ - НА СЕЛО", "2026-10-11", "19:00")])
check("stage performances missing from the grid are reported; a cancelled one is not",
      [u[:2] for u in unlisted], [("2026-10-30", "19:30"), ("2026-11-05", "19:30")])
salza_pages = {O.SALZA_URL.format(y=2026, m=10): raw("theatre_salzaismyah.html"),
               O.SALZA_URL.format(y=2026, m=11): raw("theatre_salzaismyah_11.html")}
salza_pages.update({O.SALZA_STAGE_URL.format(n=n): raw(f"theatre_salzaismyah_stage{n}.html") for n in (1, 2, 3)})
res = O.fetch_salzaismyah(FakeNet(salza_pages), TODAY)
check("fetch: a later grid row on no stage page → coverage ends with this month, noted",
      (res["covered_to"], any("on no stage page" in n and "coverage ends with this month" in n
                              for n in res["notes"])), ("2026-10-11", True))
check("fetch: the stage is kept with each row",
      [r[3].get("stage") for r in res["rows"] if r[1:3] == ("2026-10-09", "19:30")], [CHAMBER])
check("fetch: hall = the stage's display name for rows whose stage is known",
      [r[3].get("hall") for r in res["rows"] if r[1:3] == ("2026-10-09", "19:30")],
      ["Камерна сцена „Славянска беседа“"])
check("hall names: the capitalised stage headings map to the app's names; unknown kept",
      [O.salza_hall_name(OPEN), O.salza_hall_name(CHAMBER), O.salza_hall_name("ЪНДЪРГРАУНД  -  Сцена"),
       O.salza_hall_name("Нова сцена")],
      ["Открита сцена", "Камерна сцена „Славянска беседа“", "Сцена „Ъндърграунд“", "Нова сцена"])
check("fetch: a row whose stage cannot be determined has no hall",
      [r[3].get("hall") for r in res["rows"] if r[1:3] == ("2026-10-09", "19:00")], [None])
res_nostg = O.fetch_salzaismyah(FakeNet({k: v for k, v in salza_pages.items() if "/site/events/" not in k}), TODAY)
check("fetch: stage pages down → the grid is still read, coverage ends with this month",
      (len(res_nostg["rows"]), res_nostg["covered_to"], any(n.startswith("stage pages not read") for n in res_nostg["notes"])),
      (6, "2026-10-11", True))
check("fetch: stage pages down → hall is None for all rows",
      all(r[3].get("hall") is None for r in res_nostg["rows"]), True)

print("salzaismyah — re-homed Театър София co-productions get no guessed hall")
import scrape_programs as _S  # noqa: E402 (already imported below, but needed here)
_why_coproduction = "staged at Театър СЪЛЗА И СМЯХ (co-production listed by Театър София)"
# another show plays on the open stage at the same time: two shows at one time are on
# different stages, so the co-production must not take that hall
_salza_open_row = O.mkrow("ДРУГО ПРЕДСТАВЛЕНИЕ", "2026-10-10", "19:05", hall="Открита сцена")
_rehome_res = {
    "sofia-th": {"rows": [], "extra_rows": [], "covered_from": "2026-10-08", "covered_to": "2026-11-29",
                 "excluded": [("ДА ОБИЧАШ НЕЩО ЖИВО", "2026-10-10", "19:05", _why_coproduction)]},
    "salzaismyah": {"rows": [_salza_open_row], "extra_rows": [],
                    "covered_from": "2026-10-08", "covered_to": "2026-11-30", "excluded": []},
}
_S.rehome_excluded(_rehome_res)
check("re-homed co-production: added once, with no hall guessed from the slot",
      [(r[1], r[2], r[3].get("hall")) for r in _rehome_res["salzaismyah"]["rows"]
       if r[0] == "ДА ОБИЧАШ НЕЩО ЖИВО"], [("2026-10-10", "19:05", None)])
check("re-homed co-production: the host's own show keeps its hall",
      [r[3].get("hall") for r in _rehome_res["salzaismyah"]["rows"] if r[0] == "ДРУГО ПРЕДСТАВЛЕНИЕ"],
      ["Открита сцена"])

# ======================================================================== toplo
print("toplo — toplocentrala.bg/program/performance")
rows = O.parse_toplo(fx("theatre_toplo.html"))
check("rows", brief(rows), [("5Сола", "2026-10-01", "19:00"), ("LifeLines: Blavatsky Freud Experience", "2026-10-08", "19:00"),
                           ("Природа Фест", "2026-10-10", "10:00"), ("Миш-Маш | ХаХаХа ИмПро театър", "2026-10-25", "19:00"),
                           ("ИСТОРИЯ НА ЕДНА СТРАСТ I ПРЕМИЕРА", "2026-10-25", "19:00")])
check("director credit not in the title", rows[0][0], "5Сола")
check("hall and event type", (rows[2][3]["hall"], rows[2][3]["kind"]), ("Тераса бар", "Фестивал"))
check("premiere from 'I ПРЕМИЕРА'", rows[4][3]["premiere"], True)

# ========================================================================== iam
print("iam — iamstudio.bg/programa/ (the Кай / София swap)")
rows, exc = O.parse_iam(fx("theatre_iam.html"), TODAY)
check("dates read from each production's own card", brief(rows),
      [("Доброто тяло", "2026-10-12", "19:30"), ("София и хвърчащото креватче", "2026-10-18", "12:00"),
       ("Кай – тигърът от голямото езеро", "2026-10-24", "12:00"),
       ("Асансьорът", "2026-10-26", "19:30"), ("Асансьорът", "2026-10-27", "19:30")])
soup = BeautifulSoup(fx("theatre_iam.html"), "lxml")
soup.select(".portfolio-item")[0].select_one(".labels-outer-schedule").append(
    BeautifulSoup('<span class="iam-venue">Созопол - Читалище „Възраждане“</span>', "lxml").span)
_, exc = O.parse_iam(str(soup), TODAY)
check("a schedule naming another hall is excluded", [(e[0], e[1], e[3]) for e in exc],
      [("Доброто тяло", "2026-10-12", "at another venue: Созопол - Читалище „Възраждане“")])
soup = BeautifulSoup(fx("theatre_iam.html"), "lxml")
links = soup.select("a.product-link")
links[1]["href"] = links[2]["href"]
raises("poster and title of different productions → refuse", O.MarkupError, O.parse_iam, str(soup), TODAY)

# ====================================================================== artvent
print("artvent — artvent.bg (Sofia, at Театър ARTVENT only)")
venue = O.parse_artvent_venue(fx("theatre_artvent_venue.html"), TODAY)
check("venue programme", [v[:4] for v in venue],
      [("Това не го казвай!", "2026-10-12", "19:30", "tova-ne-go-kazvay"),
       ("Не залагай на англичаните!", "2026-10-14", "19:30", "ne-zalagai-na-anglicanite"),
       ("Заклеваш ли се в децата?", "2026-10-15", "19:30", "zaklevas-li-se-v-decata")])
title, tickets = O.parse_artvent_event(fx("theatre_artvent_event.html"))
check("show page title", title, "Аз, която те обича")
check("every ticket read", len(tickets), 5)
rows, exc = O.artvent_split(title, tickets, "https://artvent.bg/event/az-koiato-te-obica")
check("only София + Театър ARTVENT become rows", brief(rows),
      [("Аз, която те обича", "2026-11-21", "19:30"), ("Аз, която те обича", "2026-12-18", "19:30")])
check("touring dates (Враца, Смолян) dropped", sorted(e[1] for e in exc if e[3].startswith("touring")),
      ["2026-10-16", "2026-11-16"])
check("a Sofia date at another hall dropped", [(e[1], e[3]) for e in exc if e[3].startswith("Sofia")],
      [("2026-10-08", "Sofia, other venue: Theatro отсам канала")])
net = FakeNet({O.ARTVENT_VENUE_URL: raw("theatre_artvent_venue.html"),
               O.ARTVENT_EVENT_URL.format(slug="tova-ne-go-kazvay"): raw("theatre_artvent_event.html")})
res = O.fetch_artvent(net, TODAY)
check("fetch: unreachable show pages fall back to the venue list", brief(res["rows"]),
      [("Не залагай на англичаните!", "2026-10-14", "19:30"), ("Заклеваш ли се в децата?", "2026-10-15", "19:30")])
check("fetch: a show page's own dates win (one-show months stay extra)", brief(res["extra_rows"]),
      [("Това не го казвай!", "2026-11-21", "19:30"), ("Това не го казвай!", "2026-12-18", "19:30")])
check("fetch: the venue list / show page disagreement is reported",
      any("on the venue list but not on its show page" in n for n in res["notes"]), True)

# ====================================================================== sfumato
print("sfumato — sfumato.info (windows-1251)")
rows = O.parse_sfumato(fx("theatre_sfumato.html"), TODAY)
check("rows", brief(rows), [("Господин Колперт премиера", "2026-10-08", "19:00"),
                           ('Програма "Изгонването на бесовете" Лицето на ближния', "2026-10-13", "19:00"),
                           ("Вирхова приказка", "2026-10-12", "19:00")])
check("stage per section", [r[3]["hall"] for r in rows], ["Основна сцена", "Основна сцена", "Зала Underground"])
check("premiere", rows[0][3]["premiere"], True)

# ===================================================================== citymark
print("citymark — theatre.art.bg (official channel, windows-1251)")
rows, months = O.parse_art_venue(fx("theatre_citymark.html"), TODAY)
check("rows", brief(rows), [("Момче и вятър", "2026-10-03", "11:00"), ("Петьо и вълкът", "2026-10-04", "11:00"),
                           ("Канкун", "2026-10-12", "19:30")])
check("months published", dict(months), {(2026, 10): 3, (2026, 11): 0, (2026, 12): 0})
res = O.fetch_citymark(FakeNet({O.CITYMARK_URL: raw("theatre_citymark.html")}), TODAY)
check("fetch: covered", (res["covered_from"], res["covered_to"], brief(res["rows"])),
      ("2026-10-08", "2026-10-12", [("Канкун", "2026-10-12", "19:30")]))

# ========================================================================== tba
print("tba — tba.art.bg programme + tickets.tba.bg public list")
prog = O.parse_tba_programme(fx("theatre_tba_programme.html"), 2026, 10)
check("three stages", sorted({r[3]["hall"] for r in prog}),
      ['Голяма сцена', 'Камерна сцена "МИРАКЪЛ"', 'сцена-клуб "МаксиМ"'])
check("quotes stripped, '19.00 ч.' → 19:00", prog[0][:3], ("Фейк", "2026-10-08", "19:00"))
check("visiting company split off", (prog[1][0], prog[1][3].get("guest")), ("Тяло в лед", "ДТ-Русе"))
raises("weekday guard: the wrong year never passes", O.MarkupError, O.parse_tba_programme,
       fx("theatre_tba_programme.html"), 2025, 10)
raises("heading guard: the wrong month never passes", O.MarkupError, O.parse_tba_programme,
       fx("theatre_tba_programme.html"), 2026, 11)
tix = O.parse_tba_tickets(fx("theatre_tba_tickets.html"))
check("ticket-centre rows", len(tix), 10)
check("ticket-centre row", tix[0][:3], ("ФЕЙК", "2026-10-08", "19:00"))
check("ticket-centre guest", (tix[2][0], tix[2][3].get("guest")), ("ТЯЛО В ЛЕД", "ДТ - Русе"))
union, dis, variants = O.reconcile_tba(prog, tix)
check("union", len(union), 10)
fejk = [r for r in union if r[0] == "Фейк"][0]
check("paired: programme title + stage, both sources",
      (fejk[3]["hall"], fejk[3]["sources"], fejk[3].get("tickets_title")),
      ("Голяма сцена", ["programme", "tickets"], "ФЕЙК"))
check("one-sided performance kept and reported", [(d[0], d[1], d[2]) for d in dis],
      [("only on tickets.tba.bg", "2026-10-30", "ДАМА ПИКА")])
dama = [r for r in union if r[0] == "ДАМА ПИКА"][0]
check("ticket-only row: no stage invented", (dama[3]["hall"], dama[3]["sources"]), (None, ["tickets"]))
check("title variant paired", sorted({v[1:] for v in variants}), [("Урок по български", "УРОК ПО БЪЛГАРСКИ по Ив. Вазов")])
moved = [O.mkrow("Фейк", "2026-10-08", "20:00")]
_, dis2, _ = O.reconcile_tba(prog[:1], moved)
check("time disagreement: programme time kept, reported", [(d[0], d[3]) for d in dis2],
      [("time differs", "programme 19:00 / tickets 20:00 — programme kept")])
renamed = [O.mkrow("ЗА СИЛАТА НА СЛОВОТО спектакъл на Камен Донев", "2026-11-24", "19:00")]
lecture = [O.mkrow('Лекция №3 за "СИЛАТА НА СЛОВОТО"', "2026-11-24", "19:00", hall="Голяма сцена")]
u3, dis3, _ = O.reconcile_tba(lecture, renamed)
check("same slot, differently worded → one performance, reported", (len(u3), dis3[0][0]), (1, "title differs"))
net = FakeNet({O.TBA_URL.format(y=2026, m=10): raw("theatre_tba_programme.html"),
               O.TBA_TICKETS_URL: raw("theatre_tba_tickets.html")})
res = O.fetch_tba(net, TODAY)
check("fetch: covered today → later last date of the two", (res["covered_from"], res["covered_to"]),
      ("2026-10-08", "2026-10-30"))
check("fetch: rows", len(res["rows"]), 10)
check("fetch: disagreement in notes", any(n.startswith("DISAGREE only on tickets.tba.bg: 2026-10-30 ДАМА ПИКА")
                                          for n in res["notes"]), True)
check("fetch: the login form is never touched", [u for u in net.asked if "tickets.tba.bg" in u], [O.TBA_TICKETS_URL])
res = O.fetch_tba(FakeNet({O.TBA_URL.format(y=2026, m=10): raw("theatre_tba_programme.html")}), TODAY)
check("fetch: ticket centre down → programme alone, noted",
      (len(res["rows"]), any("only one of the two" in n for n in res["notes"])), (9, True))

# ================================================================= fetch_venue
print("fetch_venue never raises")
check("no official source → None", O.fetch_venue("natfiz", FakeNet({}), TODAY), None)
check("…with the reason", O.LAST_STATUS["natfiz"].startswith("no official source"), True)
check("unreachable → None", O.fetch_venue("toplo", FakeNet({}), TODAY), None)
check("…with the reason", O.LAST_STATUS["toplo"].startswith("unreachable"), True)
broken = b"<html><body><ul><li class='program-list-item'><div>?</div></li></ul></body></html>"
check("changed markup → None", O.fetch_venue("toplo", FakeNet({O.TOPLO_URL: broken}), TODAY), None)
check("every theatre id is accounted for", sorted(list(O.FETCHERS) + list(O.NO_OFFICIAL_SOURCE)),
      sorted(["national", "sofia-th", "th199", "tba", "zad-kanala", "vazrazhdane", "mladezhki", "kuklen",
              "satira", "salzaismyah", "toplo", "iam", "artvent", "sfumato", "citymark",
              "atelie313", "natfiz", "new-ndk", "derida"]))

# ================================================================ wave M3: merge
# scrape_programs.py wires the programmes above into the app: each theatre's own
# programme is authoritative for its covered dates, theatre.art.bg only fills
# the days after it (preliminary), shows are matched inside one theatre only.
import scrape_programs as S                                    # noqa: E402
import show_identity as SI                                     # noqa: E402
import film_identity as FID                                    # noqa: E402

print("theatre.art.bg day page — every time of a listing (trimmed, windows-1251)")


class _Soup:
    def __init__(self, body):
        self.body = body

    def soup(self, url, attempts=None):
        return BeautifulSoup(O.decode(self.body), "lxml")


day = S.scrape_theatre_day("2026-10-11", _Soup(raw("theatre_artbg_day.html")))
check("a matinée pair is two performances, each with its stage",
      [(r[0], r[2], r[3], r[5]) for r in day if r[0] == "ФЕЯТА ВАНИЛИЯ"],
      [("ФЕЯТА ВАНИЛИЯ", "11:00", "4", "Камерна сцена"), ("ФЕЯТА ВАНИЛИЯ", "12:30", "4", "Камерна сцена")])
check("a single listing and its hall", [(r[2], r[3], r[5]) for r in day if r[0] == "ДЯДОВАТА РЪКАВИЧКА"],
      [("11:00", "10", "Салон Гурко 14")])
check("a bare 'сцена' is no hall", [(r[3], r[5]) for r in day if "ПРАСЕНЦА" in r[0]], [("173", None)])
check("a venue the app does not list keeps its own id", [r[3] for r in day if r[0] == "ЗА МИШКИТЕ И ХОРАТА"], ["178"])
check("theatre ids map only to verified theatres",
      (S.THEATRE_ART_IDS.get("4"), S.THEATRE_ART_IDS.get("6"), S.THEATRE_ART_IDS.get("178")),
      ("mladezhki", "tba", None))
check("each listing's own theatre.art.bg event page and printed price",
      [(r[6], r[7]) for r in day if r[0] == "ДЯДОВАТА РЪКАВИЧКА"],
      [("https://theatre.art.bg/дядовата-ръкавичка_7900_10_20", "от 6.00 до 7.00 €")])
check("no price printed → none", sorted({r[7] for r in day if r[0] == "ФЕЯТА ВАНИЛИЯ"}, key=str), [None])
check("every listing's own schema.org Event is dated the requested day → the page is genuine",
      S.art_day_verdict(BeautifulSoup(O.decode(raw("theatre_artbg_day.html")), "lxml"), "2026-10-11"), "ok")
check("asked for another day, the same page lists nothing for it (its Events say 11 Oct)",
      S.scrape_theatre_day("2026-10-12", _Soup(raw("theatre_artbg_day.html"))), [])
fb = BeautifulSoup(O.decode(raw("theatre_artbg_fallback.html")), "lxml")
check("fallback: 14 Dec answered with TODAY's listings — the breadcrumb still echoes 14.12.2026",
      (S.page_echoes_date(fb, "2026-12-14"), S.art_day_verdict(fb, "2026-12-14")), (True, "other"))
check("…so the whole page is discarded: nothing is filed under 14 Dec",
      S.scrape_theatre_day("2026-12-14", _Soup(raw("theatre_artbg_fallback.html"))), [])
_bare = O.decode(raw("theatre_artbg_day.html"))
_bare = __import__("re").sub(r'<script type="application/ld\+json">.*?</script>', "", _bare, flags=__import__("re").S)
check("listings without their dated Events cannot be verified → None (last run's rows stand)",
      S.scrape_theatre_day("2026-10-11", _Soup(_bare.encode("cp1251"))), None)

print("performance price, the app's euro style")
check("Сатирата's лв./€ pairs → euro range", S.theatre_price("35.20 лв./18.00 €, 43.03 лв./22.00 €"), "18,00–22,00 €")
check("one euro amount", S.theatre_price("31.29 лв./16.00 €"), "16,00 €")
check("no price", S.theatre_price(None), None)
check("theatre.art.bg 'от … до … €'", S.theatre_price("от 6.00 до 7.00 €"), "6,00–7,00 €")
check("a lone 'от' stays a minimum", S.theatre_price("от 8.00 €"), "от 8,00 €")
check("leva only: never converted", S.theatre_price("30 лв."), None)

print("minted show titles: as published, minus decorations")
for a, b in [("Инсомния премиера", "Инсомния"), ("Прелюбодейци | ПРЕМИЕРА", "Прелюбодейци"),
             ("“Как господин Мокинпот се спаси от нещастието”", "Как господин Мокинпот се спаси от нещастието"),
             (',,ГОЛЕМИЯТ СИН"', "ГОЛЕМИЯТ СИН"),
             ("НАШАТА ГОЛЯМА ФРЕНСКА СВАТБА - ПРЕДСТАВЛЕНИЕ 200", "НАШАТА ГОЛЯМА ФРЕНСКА СВАТБА"),
             ("Тяло в лед - Гостува ДТ-Русе", "Тяло в лед"), ("Светици и перверзници 16+", "Светици и перверзници"),
             ("Xензел и Гретел", "Хензел и Гретел"), ("Бaлдахинът", "Балдахинът"),
             ("Neoдачници", "Neoдачници"), ("Бурята | The Tempest", "Бурята | The Tempest")]:
    check(f"display {a[:34]!r}", SI.display_title(a), b)
check("normal case beats ALL CAPS", SI.choose_display(["ИНСОМНИЯ", "Инсомния премиера", "ИНСОМНИЯ"]), "Инсомния")

print("show identity — inside one theatre only")
CAT = [{"id": "baldahinat", "title": "Балдахинът", "titleEn": "The Baldachin", "theatre": "tba",
        "synBg": "Пиеса.", "genres": ["Драма"], "duration": 95},
       {"id": "baldahinat-2", "title": "Бaлдахинът", "theatre": "tba", "genres": []},
       {"id": "prasenca-vaz", "title": "Трите прасенца", "theatre": "vazrazhdane"},
       {"id": "hamlet-nat", "title": "Хамлет", "theatre": "national"},
       {"id": "suprugi", "title": "Отчаяни съпрузи", "theatre": "satira"},
       {"id": "suprugi-2", "title": "Отчаяни съпрузи 2: Бракувани", "theatre": "satira"},
       {"id": "urok", "title": "Урок по български", "theatre": "tba"}]
MERGE = {"merge": [{"theatre": "tba", "keep": "baldahinat", "drop": "baldahinat-2", "reason": "test"}]}
ix = SI.ShowIndex(CAT, aliases=MERGE)
check("the Latin-'a' duplicate resolves to the record with data", ix.resolve("Бaлдахинът", "tba"), ("baldahinat", "exact"))
check("…so does the ticket centre's ALL CAPS", ix.resolve("БАЛДАХИНЪТ", "tba")[0], "baldahinat")
check("a merged-away id maps to the kept record", (ix.canonical("baldahinat-2"), ix.theatre_of("baldahinat-2")),
      ("baldahinat", "tba"))
check("the same title at ANOTHER theatre is not that show", ix.resolve("Трите прасенца", "mladezhki"), (None, "unmatched"))
check("…but it is at its own theatre", ix.resolve("ТРИТЕ ПРАСЕНЦА", "vazrazhdane")[0], "prasenca-vaz")
check("a sequel number keeps productions apart", ix.resolve("Отчаяни съпрузи", "satira")[0], "suprugi")
ix2 = SI.ShowIndex(CAT, aliases={})
check("no curated merge: the duplicate still goes to the record with data", ix2.resolve("Бaлдахинът", "tba"),
      ("baldahinat", "duplicate"))
check("…and the pair is listed as a suspected duplicate",
      [(d["show"], d["other"]) for d in ix2.suspected_duplicates(["baldahinat"])], [("baldahinat", "baldahinat-2")])
ix3 = SI.ShowIndex(CAT, aliases={"groups": [{"theatre": "national", "show": "hamlet-nat",
                                             "titles": ["Хамлет", "Хамлет, принц датски"], "reason": "test"}]})
check("a curated alias applies at its own theatre only",
      (ix3.resolve("Хамлет, принц датски", "national")[0], ix3.resolve("Хамлет, принц датски", "tba")[0]),
      ("hamlet-nat", None))

print("minting: one show per new production, at its own theatre")
mint_log = []


def _mint(title, theatre, url):
    sid = f"m-{len(mint_log) + 1}"
    ix.add({"id": sid, "title": title, "theatre": theatre}, minted=True)
    mint_log.append((sid, title, theatre, url))
    return sid


results = {"tba": {"rows": [("Бaлдахинът", "2026-10-22", "19:30", {"hall": "Камерна сцена", "url": "u"}),
                            ("ДАМА ПИКА", "2026-10-30", "19:00", {"url": None}),
                            ("Дама пика премиера", "2026-10-09", "19:00", {"url": "https://www.tba.art.bg/дама-пика"})],
                   "extra_rows": [("Урок по български по Ив. Вазов", "2026-12-20", "11:00", {"url": "x"})]},
           "mladezhki": {"rows": [("Трите прасенца", "2026-10-11", "11:00", {"url": "https://mlt.bg/x"})],
                         "extra_rows": []}}
res_m, ids = S.resolve_theatre_rows(ix, results, _mint)
check("new productions minted once each, title as published minus decorations, with the official page",
      mint_log, [("m-1", "Дама пика", "tba", "https://www.tba.art.bg/дама-пика"),
                 ("m-2", "Урок по български по Ив. Вазов", "tba", "x"),
                 ("m-3", "Трите прасенца", "mladezhki", "https://mlt.bg/x")])
check("every row placed (the homoglyph duplicate on the kept record)",
      [(r[0], r[1]) for r in res_m["tba"]["rows"]],
      [("baldahinat", "2026-10-22"), ("m-1", "2026-10-30"), ("m-1", "2026-10-09")])
check("a near miss is minted, not merged — and listed for a human",
      [(d["show"], d["other"]) for d in ix.suspected_duplicates(["m-2"])], [("m-2", "urok")])

print("minted titles follow the theatre's own spelling (minted records only)")
rt = S.retitle_minted({"kolpert": {"title": "ГОСПОДИН КОЛПЕРТ ПРЕМИЕРА"}, "kankun": {"title": '"КАНКУН"'},
                       "mars": {"title": "ПЪТУВАНЕ ДО МАРС"}, "zh": {"title": "Жената пита"}},
                      {"kolpert": ["Господин Колперт премиера"], "kankun": ["Канкун"],
                       "mars": ["ПЪТУВАНЕ ДО МАРС"], "zh": ["Жената пита ChatGPT"]})
check("decorations and ALL CAPS give way to the official spelling; a different title is never adopted",
      rt, {"kolpert": ("ГОСПОДИН КОЛПЕРТ ПРЕМИЕРА", "Господин Колперт"), "kankun": ('"КАНКУН"', "Канкун")})

print("theatre merge rules (pure)")
F, E = "2026-10-08", "2026-12-14"
off = [("a", "2026-10-08", "19:00", "Голяма сцена", "18,00 €"),
       ("b", "2026-10-09", "11:00", None, None), ("b", "2026-10-09", "12:30", None, None)]
extra = [("c", "2026-11-03", "19:00", "Камерна сцена", None), ("c", "2026-12-20", "19:00", None, None)]
agg = [("a", "A", "2026-10-08", "19:00", None),              # agrees with the theatre
       ("a", "A", "2026-10-10", "19:30", None),              # not on its programme → discarded
       (None, "Unknown", "2026-10-09", "19:00", None),       # unknown, inside coverage → discarded
       ("d", "D", "2026-11-02", "19:00", "Камерна сцена"),   # after coverage → preliminary
       ("c", "C", "2026-11-03", "20:00", None),              # the theatre's own extra row wins that show/day
       ("e", "E", "2026-11-04", "19:00", None),              # listed by the theatre as touring → never shown
       (None, "New", "2026-11-05", "19:00", None),           # after coverage, unknown → not minted
       ("a", "A", "2026-10-01", "19:00", None)]              # past
agg_dates = {r[2] for r in agg} | {"2026-10-12"}
prev = [["a", "2026-10-01", "19:00", None, None], ["z", "2026-10-12", "19:00", None, None],
        ["y", "2026-11-20", "19:00", "Сцена", None]]
rows, pf, info = S.merge_theatre_venue("t", "official", off, ("2026-10-08", "2026-10-31"), extra, agg, agg_dates,
                                       prev, None, F, E, {("e", "2026-11-04", "19:00")})
check("inside coverage exactly the official rows (with hall and price); after it the theatre's own extra "
      "rows, aggregator rows and — where the aggregator was not read — last run's rows", rows,
      [["a", "2026-10-08", "19:00", "Голяма сцена", "18,00 €"], ["b", "2026-10-09", "11:00", None, None],
       ["b", "2026-10-09", "12:30", None, None], ["d", "2026-11-02", "19:00", "Камерна сцена", None],
       ["c", "2026-11-03", "19:00", "Камерна сцена", None], ["y", "2026-11-20", "19:00", "Сцена", None]])
check("contradicting aggregator rows are discarded and logged", info["discarded"],
      [["2026-10-10", "19:30", "A", "a"], ["2026-10-09", "19:00", "Unknown", None]])
check("PRELIM_FROM = the day after the official coverage", pf, "2026-11-01")
check("an excluded (touring) performance is never shown", info["excluded_blocked"], 1)
check("an unknown aggregator title is reported, not minted", info["unplaced"], [["2026-11-05", "19:00", "New"]])
check("preliminary rows counted", info["prelim_rows"], 3)
check("coverage past the window: PRELIM_FROM the day after the window",
      S.merge_theatre_venue("t", "official", off, ("2026-10-08", "2026-12-31"), [], [], set(), [], None, F, E)[1],
      "2026-12-15")
rows, pf, _ = S.merge_theatre_venue("t", "unreachable", [], None, [], agg, agg_dates, prev, "2026-10-20", F, E)
check("official source unreachable: the rows it had confirmed (to 10-19) kept, never past ones; after them "
      "theatre.art.bg on the dates it was read, last run's rows on the others; ALL preliminary from today",
      (rows, pf),
      ([["z", "2026-10-12", "19:00", None, None], ["d", "2026-11-02", "19:00", "Камерна сцена", None],
        ["c", "2026-11-03", "20:00", None, None], ["e", "2026-11-04", "19:00", None, None],
        ["y", "2026-11-20", "19:00", "Сцена", None]], F))
check("unreachable with no previous PRELIM_FROM: every kept row stays, preliminary from today",
      S.merge_theatre_venue("t", "unreachable", [], None, [], [], set(), prev, None, F, E)[:2],
      ([["z", "2026-10-12", "19:00", None, None], ["y", "2026-11-20", "19:00", "Сцена", None]], F))
agg_a = [("h", "ХЕНЗЕЛ И ГРЕТЕЛ", "2026-10-11", "11:00", None), (None, "НЕЩО НОВО", "2026-10-12", "11:00", None)]
prev_a = [["h", "2026-10-11", "16:00", None, None], ["h", "2026-10-25", "11:00", None, None]]
rows, pf, info = S.merge_theatre_venue("atelie313", "none", [], None, [], agg_a, {"2026-10-11", "2026-10-12"},
                                       prev_a, None, F, E)
check("no official source (Ателие 313): aggregator rows on dates read, last run's elsewhere, "
      "all preliminary from today", (rows, pf),
      ([["h", "2026-10-11", "11:00", None, None], ["h", "2026-10-25", "11:00", None, None]], F))
check("…an unknown aggregator title is not minted", info["unplaced"], [["2026-10-12", "11:00", "НЕЩО НОВО"]])
check("no official source and nothing listed (Натфиз): no rows",
      S.merge_theatre_venue("natfiz", "none", [], None, [], [], {"2026-10-11"}, [], None, F, E)[:2], ([], F))
rows, pf, _ = S.merge_theatre_venue("th199", "official", [("k", "2026-10-09", "19:30", None, None)],
                                    ("2026-10-09", "2026-10-18"),
                                    [("t", "2026-10-08", "19:30", None, None), ("k", "2026-10-19", "19:30", None, None)],
                                    [("q", "Q", "2026-10-08", "19:00", None)], {"2026-10-08"},
                                    [["p", "2026-10-08", "17:00", None, None]], "2026-10-09", F, E)
check("coverage starting after today: the theatre's own card for today, last run's confirmed rows, "
      "never the aggregator", rows,
      [["p", "2026-10-08", "17:00", None, None], ["t", "2026-10-08", "19:30", None, None],
       ["k", "2026-10-09", "19:30", None, None], ["k", "2026-10-19", "19:30", None, None]])
check("…PRELIM_FROM after the listed window", pf, "2026-10-19")
check("a theatre programme that collapses stops the scrape",
      bool(S.implausible_drop({"status": "official", "screenings": 60, "days": 30},
                              {"status": "official", "screenings": 5, "days": 30}, min_rate=1)), True)
check("…the cinema threshold alone would not have caught it",
      S.implausible_drop({"status": "official", "screenings": 60, "days": 30},
                         {"status": "official", "screenings": 5, "days": 30}), None)

print("theatre.art.bg titles in the preliminary range are minted (owner's rule: shown, marked preliminary)")
aix = SI.ShowIndex([{"id": "kakto", "title": "Както в най-добрите дни", "theatre": "th199"},
                    {"id": "feya-s", "title": "Феята от захарницата", "theatre": "salzaismyah"}])
alog = []


def _amint(title, theatre, url):
    sid = f"a-{len(alog) + 1}"
    aix.add({"id": sid, "title": title, "theatre": theatre}, minted=True)
    alog.append((sid, title, theatre, url))
    return sid


P = "https://theatre.art.bg/"
arows = [("ШВЕЙЦАРИЯ", "2026-10-23", "19:30", None, "buy1", P + "швейцария_1_8_20", None),
         ("ШВЕЙЦАРИЯ", "2026-10-21", "19:30", None, "buy2", P + "швейцария_2_8_20", None),   # earliest → its page
         ("КАКТО В НАЙ-ДОБРИТЕ ДНИ", "2026-10-22", "19:30", None, "b", P + "x", None),       # known show → placed
         ("ЗАСЕКРЕТЕНО ИЗСЛЕДВАНЕ", "2026-10-11", "19:30", None, "b", P + "y", None),        # inside coverage → never
         ("ФЕЯТА ОТ ЗАХАРНИЦАТА ГОСТУВА В ТЕАТЪР СЪЛЗА И СМЯХ", "2026-12-05", "11:00", None, "b", P + "z", None),
         ("ВАКХАНКИ", "2026-11-02", "19:00", None, "b", P + "v", None),                      # the theatre excludes it
         ("ДАЛЕЧНО", "2026-12-20", "19:00", None, "b", P + "w", None)]                       # after the window
got = S.mint_aggregator_titles(aix, "th199", arows, "2026-10-20", "2026-12-14",
                               {O.normalise_show_title("Вакханки")}, _amint)
check("minted once per new title, theatre from the listing's theatre id, earliest event page as its link",
      (got, alog), (["a-1"], [("a-1", "ШВЕЙЦАРИЯ", "th199", P + "швейцария_2_8_20")]))
check("…a known show, a date inside coverage or past the window, a title the theatre excludes and a "
      "production staged at another venue are never minted", len(alog), 1)
check("no official source: the whole window is preliminary",
      S.mint_aggregator_titles(aix, "atelie313", [("ХРАБРИЯТ ШИВАЧ", "2026-10-18", "11:00", None, "b", P + "s", None)],
                               "2026-10-08", "2026-12-14", set(), _amint), ["a-2"])
rows, pf, info = S.merge_theatre_venue("atelie313", "none", [], None, [],
                                       [("a-2", "ХРАБРИЯТ ШИВАЧ", "2026-10-18", "11:00", None, "6,00 €")],
                                       {"2026-10-18"}, [], None, "2026-10-08", "2026-12-14")
check("a minted aggregator title is a preliminary performance, with the price the listing prints",
      (rows, pf), ([["a-2", "2026-10-18", "11:00", None, "6,00 €"]], "2026-10-08"))

print("a listing staged at another theatre the app lists becomes that theatre's row")
check("host from the co-production reason", S.excluded_host(
      "staged at Театър СЪЛЗА И СМЯХ (co-production listed by Театър София)", "sofia-th"), "salzaismyah")
check("a venue the app does not list → no host",
      (S.excluded_host("Sofia, other venue: Theatro отсам канала", "artvent"),
       S.excluded_host("touring: Варна, ФКЦ, зала 1", "artvent"),
       S.excluded_host("no stage on the programme — staged off-site (venue not given)", "national")),
      (None, None, None))
_why = "staged at Театър СЪЛЗА И СМЯХ (co-production listed by Театър София)"
_res = {"sofia-th": {"rows": [], "extra_rows": [], "covered_from": "2026-10-08", "covered_to": "2026-11-29",
                     "excluded": [("Феята от захарницата", "2026-10-31", "11:00", _why),
                                  ("Феята от захарницата", "2026-11-14", "11:00", _why),
                                  ("Чудните приключения на Пинокио", "2026-12-05", "11:00", _why)]},
        "salzaismyah": {"rows": [O.mkrow("ФЕЯТА ОТ ЗАХАРНИЦАТА", "2026-10-31", "11:00")], "extra_rows": [],
                        "covered_from": "2026-10-08", "covered_to": "2026-11-30", "excluded": []}}
_moved = S.rehome_excluded(_res)
check("re-homed: the host's own entry wins; a missing one joins its programme (inside coverage) or "
      "its preliminary rows (after it)", [(m[2][:5], m[3], m[5]) for m in _moved],
      [("Феята", "2026-10-31", "the host lists it itself"),
       ("Феята", "2026-11-14", "added inside the host's covered range"),
       ("Чудни", "2026-12-05", "added as a preliminary row")])
check("…the host's rows, the source's exclusions unchanged",
      ([r[1] for r in _res["salzaismyah"]["rows"]], [r[1] for r in _res["salzaismyah"]["extra_rows"]],
       _res["salzaismyah"]["rows"][-1][3].get("listed_by"), len(_res["sofia-th"]["excluded"])),
      (["2026-10-31", "2026-11-14"], ["2026-12-05"], "sofia-th", 3))

print("a day a theatre's own list may cut short is confirmed only by the aggregator's verified page")
def _th199(extra):
    return {"rows": [O.mkrow("Боклук", "2026-10-19", "19:30")], "extra_rows": extra,
            "covered_from": "2026-10-09", "covered_to": "2026-10-19", "confirm_by_aggregator": ["2026-10-20"],
            "notes": []}
_same = lambda a, b: O.normalise_show_title(a) == O.normalise_show_title(b)
_r = _th199([O.mkrow("Тортила Флет", "2026-10-20", "19:30")])
check("confirmed when theatre.art.bg lists nothing more for the day",
      (S.confirm_cut_days(_r, [("ТОРТИЛА ФЛЕТ", "2026-10-20", "19:30")], {"2026-10-20"}, _same),
       _r["covered_to"], [r[1] for r in _r["rows"]], _r["extra_rows"]),
      (["2026-10-20"], "2026-10-20", ["2026-10-19", "2026-10-20"], []))
_r = _th199([O.mkrow("Тортила Флет", "2026-10-20", "19:30")])
check("…not when it lists another performance that day",
      (S.confirm_cut_days(_r, [("ТОРТИЛА ФЛЕТ", "2026-10-20", "19:30"), ("ШВЕЙЦАРИЯ", "2026-10-20", "11:30")],
                          {"2026-10-20"}, _same), _r["covered_to"]), ([], "2026-10-19"))
_r = _th199([O.mkrow("Тортила Флет", "2026-10-20", "19:30")])
check("…nor when its page for the day was not verified",
      S.confirm_cut_days(_r, [("ТОРТИЛА ФЛЕТ", "2026-10-20", "19:30")], set(), _same), [])

print("cinema follow-ups: orphan minted films, Cinema City ticket links, ЕМБАРГО")
cat_f = [{"id": "kosa", "bg": "Коса", "en": "Hair", "year": 1979},
         {"id": "magyosnika", "bg": "Магьосникът от Кремъл"}]
minted_f = {"kinoklasiki-kosa-1979": {"id": "kinoklasiki-kosa-1979", "bg": "КИНОКЛАСИКИ: КОСА | 1979 |",
                                      "source": "vlaikova"},
            "magyosnika-ot-kremal": {"id": "magyosnika-ot-kremal", "bg": "Магьосника от Кремъл", "source": "g8"},
            "matriarhat": {"id": "matriarhat", "bg": "Матриархат", "source": "odeon"},
            "v-bryuzh": {"id": "v-bryuzh", "bg": "В Брюж", "source": "dom-kino"}}
fix = FID.FilmIndex(cat_f + list(minted_f.values()), minted_ids=set(minted_f),
                    aliases={"groups": [{"titles": ["Магьосника от Кремъл", "Магьосникът от Кремъл"],
                                         "film": "magyosnika", "reason": "test"}]})
sup = S.superseded_films(minted_f, fix, [["v-bryuzh", "dom-kino", "2026-10-10", ["19:00"]]])
check("an orphan superseded by a catalogue film or an alias is retired; one merely off-screen stays",
      sorted(sup), ["kinoklasiki-kosa-1979", "magyosnika-ot-kremal"])
check("…each with its successor", (sup["kinoklasiki-kosa-1979"][0], tuple(sup["magyosnika-ot-kremal"])),
      ("kosa", ("magyosnika", "alias")))
emb = [{"id": "sinelibri-2026-embargo", "bg": "СИНЕЛИБРИ 2026 – ЕМБАРГО", "source": "vlaikova"},
       {"id": "sinelibri-2026-embargo-predstavyane-na-tvorchestvoto-na-zhoz",
        "bg": "СИНЕЛИБРИ 2026 – ЕМБАРГО | представяне на творчеството на  Жозе Сарамаго", "source": "vlaikova"}]
eix = FID.FilmIndex(emb, minted_ids={e["id"] for e in emb})
check("ЕМБАРГО: the short title is the same Влайкова screening (curated alias)",
      eix.resolve("СИНЕЛИБРИ 2026 – ЕМБАРГО", "vlaikova")[0], emb[1]["id"])
check("…scoped to Влайкова", eix.resolve("СИНЕЛИБРИ 2026 – ЕМБАРГО", "odeon")[0], emb[0]["id"])
kept, deep = S.drop_deep_vlinks({("f", "cc-sofia"): "https://www.cinemacity.bg/films/x",
                                 ("f", "arena-mega"): "https://www.kinoarena.com/x"},
                                {"cc-sofia": {"url": "u", "deep": "https://x/{date}"}, "arena-mega": {"url": "u"}})
check("no VLINKS for a venue whose BOOKING has a dated deep page", (sorted(kept), deep),
      ([("f", "arena-mega")], {"cc-sofia"}))
check("BOOKING read from the data block", S.read_const_obj('x\nconst BOOKING = {"a":{"deep":"d"}};\n', "BOOKING"),
      {"a": {"deep": "d"}})

# ===================================================== P1A (2026-10-09 audit)
print("month-end completeness on the live programme (CODE-06): mlt.bg, October + November 2026")
MLT10 = O.parse_mlt(fx("theatre_mladezhki_2026-10_full.html"), 2026, 10)
MLT11 = O.parse_mlt(fx("theatre_mladezhki_2026-11_full.html"), 2026, 11)
MLT = MLT10 + MLT11
check("the live pages: 62 October and 61 November performances, three stages each",
      (len(MLT10), len(MLT11), sorted({r[3]["hall"] for r in MLT11})),
      (62, 61, ["В Лунната стая", "Голяма сцена", "Камерна сцена"]))
for day in (9, 25, 31):
    check(f"a published November, run on {day} Oct: covered to its last performance",
          O.complete_until(MLT, dt.date(2026, 10, day)), "2026-11-29")
HALF = MLT10 + [r for r in MLT11 if r[3]["hall"] != "Камерна сцена"]
for day in (9, 25, 31):
    why = []
    check(f"November typed in for two stages of three, run on {day} Oct: NOT covered (judged against "
          f"the 28 days before it, never itself)",
          (O.complete_until(HALF, dt.date(2026, 10, day), why), why),
          ("2026-10-31", ["2026-11: not completely published — камерна сцена: 0 performance(s) against 22 "
                          "in the 28 days before it"]))


def memory(rows, today, since):
    """The stage memory daily runs since `since` would hold on `today`
    (venue_state.seen_history's shape): every day before today observed, with
    the rows the page listed on it."""
    dates, d = set(), since
    while d < today:
        dates.add(d.isoformat())
        d += dt.timedelta(days=1)
    return {"rows": [(r[1], r[2], O.stage_of(r)) for r in rows if since.isoformat() <= r[1] < today.isoformat()],
            "dates": dates}


for day in (25, 30, 31):
    today, up = dt.date(2026, 10, day), [r for r in MLT if r[1] >= f"2026-10-{day}"]
    why = []
    check(f"the same pages as a list of upcoming dates only, on {day} Oct with NO memory: November is not judged "
          f"({32 - day} known day(s) before it cannot show a weekly stage)",
          (O.complete_until(up, today, why), why[0][:46]),
          ("2026-10-31", f"2026-11: 61 performances — only {32 - day} known day(s)"))
    check(f"…with the memory of daily runs since 2 Oct: judged against the 28 days before it, covered",
          O.complete_until(up, today, history=memory(MLT, today, dt.date(2026, 10, 2))), "2026-11-29")
    why = []
    check(f"…and November without its chamber stage is caught",
          (O.complete_until([r for r in HALF if r[1] >= f"2026-10-{day}"], today, why,
                            history=memory(MLT, today, dt.date(2026, 10, 2))), why[0][:52]),
          ("2026-10-31", "2026-11: not completely published — камерна сцена: 0"))
res = O.finish("mladezhki", "s", HALF, dt.date(2026, 10, 31))
check("finish on 31 Oct: the half-entered November becomes preliminary extra rows",
      (res["covered_from"], res["covered_to"], len(res["rows"]), len(res["extra_rows"])),
      ("2026-10-31", "2026-10-31", 5, 37))
res = O.finish("mladezhki", "s", MLT, dt.date(2026, 10, 31))
check("…a published one is covered (October's past days are the reference)",
      (res["covered_to"], len(res["rows"]), len(res["extra_rows"])), ("2026-11-29", 66, 0))
MLT_PAGES = {O.MLT_URL.format(y=2026, m=10): raw("theatre_mladezhki_2026-10_full.html"),
             O.MLT_URL.format(y=2026, m=11): raw("theatre_mladezhki_2026-11_full.html")}
res = O.fetch_venue("mladezhki", FakeNet(MLT_PAGES), dt.date(2026, 10, 25))
check("the fetcher on 25 Oct, month pages as served: covered 25 Oct .. 29 Nov",
      (res["covered_from"], res["covered_to"], res["source"]), ("2026-10-25", "2026-11-29", O.MLT_SOURCE))

print("mladezhki — its own ticket centre when mlt.bg refuses this network (403 on GitHub's runners)")
TIX = O.parse_tba_tickets(fx("theatre_mladezhki_tickets.html"), "mladezhki tickets")
check("tickets.mlt.bg: the same public list as tickets.tba.bg", brief(TIX[:3]),
      [("E.E.", "2026-10-09", "19:00"), ("Бременските музиканти", "2026-10-10", "11:00"),
       ("Феята Ванилия", "2026-10-10", "11:00")])
net = FakeNet({O.MLT_TICKETS_URL: raw("theatre_mladezhki_tickets.html")})       # mlt.bg: no answer
res = O.fetch_venue("mladezhki", net, dt.date(2026, 10, 9))
check("mlt.bg unreachable: the ticket centre's list is read instead (no halls), and says so",
      (res["source"], len(res["rows"]) + len(res["extra_rows"]), res["rows"][0][3]["hall"],
       res["notes"][-1][:40]),
      (O.MLT_TICKETS_SOURCE, 17, None, "mlt.bg programme not read (unreachable: "))
check("…the programme is always tried first", net.asked,
      ["https://mlt.bg/programa.php?year=2026&month=10", O.MLT_TICKETS_URL])
net = FakeNet({**MLT_PAGES, O.MLT_TICKETS_URL: raw("theatre_mladezhki_tickets.html")})
res = O.fetch_venue("mladezhki", net, dt.date(2026, 10, 9))
check("both read: the programme wins (halls, its 12:30), the ticket centre's 12:13 typo is a note",
      (res["source"], len(res["rows"]), [n for n in res["notes"] if "time differs" in n]),
      (O.MLT_SOURCE, 110, ["tickets.mlt.bg: time differs on 2026-11-21, Малката Баба Яга: "
                           "programme 12:30 / tickets 12:13 — programme kept"]))
check("neither read: unreachable (keep-previous decides)",
      (O.fetch_venue("mladezhki", FakeNet({}), dt.date(2026, 10, 9)), O.LAST_STATUS["mladezhki"][:12]),
      (None, "unreachable:"))

print("the December -> January rollover (CODE-01): month paging, dates without a year, completeness")
check("month pages from 20 Dec: December, then January and February of the next year",
      list(O.months_from(dt.date(2026, 12, 20), 3)), [(2026, 12), (2027, 1), (2027, 2)])
for today, dm, want in [(dt.date(2026, 12, 20), (5, 1), dt.date(2027, 1, 5)),
                        (dt.date(2026, 12, 20), (28, 12), dt.date(2026, 12, 28)),
                        (dt.date(2027, 1, 2), (28, 12), dt.date(2026, 12, 28)),
                        (dt.date(2027, 1, 2), (5, 1), dt.date(2027, 1, 5))]:
    check(f"nearest_date{dm} on {today}", O.nearest_date(dm[0], dm[1], today), want)


def shifted(name, frm, to, label):
    """A real month page re-dated to another month (the rows and stages as published)."""
    text = fx(name).replace(frm[0], label)
    text = re.sub(r"<span>(\d{2})/" + frm[1] + "</span>", lambda m: f"<span>{m.group(1)}/{to}</span>", text)
    return text.encode("utf-8")


DEC = shifted("theatre_mladezhki_2026-10_full.html", ("Октомври 2026", "10"), "12", "Декември 2026")
JAN = shifted("theatre_mladezhki_2026-11_full.html", ("Ноември 2026", "11"), "01", "Януари 2027")
net = FakeNet({O.MLT_URL.format(y=2026, m=12): DEC, O.MLT_URL.format(y=2027, m=1): JAN})
res = O.fetch_venue("mladezhki", net, dt.date(2026, 12, 20))
check("fetching on 20 Dec asks for January 2027 after December 2026",
      net.asked[:3], ["https://mlt.bg/programa.php?year=2026&month=12", "https://mlt.bg/programa.php?year=2027&month=01",
                      "https://mlt.bg/programa.php?year=2027&month=02"])
check("…and its coverage runs across New Year, January judged against December",
      (res["covered_from"], res["covered_to"], res["rows"][0][1], [r[1] for r in res["rows"]] == sorted(r[1] for r in res["rows"])),
      ("2026-12-20", "2027-01-29", "2026-12-20", True))
daily = lambda y, m, n: [mk(f"{y}-{m:02d}-{d:02d}") for d in range(1, n + 1)]
check("completeness across New Year (20 Dec): a month page's December is the reference for January",
      O.complete_until(daily(2026, 12, 31) + daily(2027, 1, 31), dt.date(2026, 12, 20)), "2027-01-31")
check("…a thin January is not covered",
      O.complete_until(daily(2026, 12, 31) + [mk("2027-01-05"), mk("2027-01-12")], dt.date(2026, 12, 20)), "2026-12-31")
DEC = daily(2026, 12, 31)
check("…an upcoming-only list on 20 Dec with no memory: 12 days before January are too few to judge it",
      O.complete_until([r for r in DEC if r[1] >= "2026-12-20"] + daily(2027, 1, 31), dt.date(2026, 12, 20)),
      "2026-12-31")
check("…with the memory of December's earlier days: January judged across New Year and covered",
      O.complete_until([r for r in DEC if r[1] >= "2026-12-20"] + daily(2027, 1, 31), dt.date(2026, 12, 20),
                       history=memory(DEC, dt.date(2026, 12, 20), dt.date(2026, 12, 1))), "2027-01-31")
check("…on 31 Dec with memory: January covered",
      O.complete_until([mk("2026-12-31")] + daily(2027, 1, 31), dt.date(2026, 12, 31),
                       history=memory(DEC, dt.date(2026, 12, 31), dt.date(2026, 12, 1))), "2027-01-31")
check("…but a January typed in only to the 15th is not",
      O.complete_until([mk("2026-12-31")] + daily(2027, 1, 15), dt.date(2026, 12, 31),
                       history=memory(DEC, dt.date(2026, 12, 31), dt.date(2026, 12, 1))), "2026-12-31")
check("on 2 Jan: February judged against January", O.complete_until(daily(2027, 1, 31) + daily(2027, 2, 28),
                                                                   dt.date(2027, 1, 2)), "2027-02-28")

print("an unreachable theatre: preliminary at once, gone after 14 days, theatre.art.bg fills in (CODE-05)")
prev_m = [["ee", "2026-10-10", "19:00", "Голяма сцена", None], ["ee", "2026-11-20", "19:00", "Голяма сцена", None]]
agg_m = [("ee", "E.E.", "2026-10-10", "19:00", None), ("ee", "E.E.", "2026-11-20", "19:30", None),
         ("pin", "Пинокио", "2026-12-05", "11:00", None), (None, "Ново", "2026-12-06", "11:00", None)]
dates_m = {"2026-10-10", "2026-11-20", "2026-12-05", "2026-12-06"}
rows, pf, info = S.merge_theatre_venue("mladezhki", "unreachable", [], None, [], agg_m, dates_m, prev_m, None,
                                       F, E, confirmed_to="2026-11-29")
check("within 14 days: its own rows to 29 Nov kept (with halls), theatre.art.bg after it; all preliminary",
      (rows, pf, info["kept"], info["unplaced"]),
      ([["ee", "2026-10-10", "19:00", "Голяма сцена", None], ["ee", "2026-11-20", "19:00", "Голяма сцена", None],
        ["pin", "2026-12-05", "11:00", None, None]], F, 2, [["2026-12-06", "11:00", "Ново"]]))
check("…theatre.art.bg's other time for a kept show and day is logged; the kept row is shown (D1)",
      info["conflicts"], [["2026-11-20", "ee", ["19:00"], "19:30"]])
print("an unreachable theatre's kept rows are never deleted by theatre.art.bg (D1: Топлоцентрала 62 -> 54)")
prev_t = [["s1", "2026-11-03", "19:00", "Зала 1", None], ["s2", "2026-11-05", "19:00", "Зала 2", None],
          ["s3", "2026-12-02", "19:00", "Зала 1", None], ["s4", "2026-12-04", "19:00", "Зала 2", None]]
agg_t = [("s9", "S9", "2026-11-03", "19:30", None),       # inside the range it last confirmed: not added
         ("s3", "S3", "2026-12-02", "20:00", None),       # a kept show, another time: logged, kept row stays
         ("s8", "S8", "2026-12-02", "18:00", None),       # a kept date: not added
         ("s7", "S7", "2026-12-09", "19:00", None)]       # a date no kept row covers: added
rows, pf, info = S.merge_theatre_venue("toplo", "unreachable", [], None, [], agg_t,
                                       {"2026-11-03", "2026-12-02", "2026-12-04", "2026-12-09"}, prev_t, None,
                                       F, E, confirmed_to="2026-11-29")
check("every kept row stays — also after the confirmed range, on dates theatre.art.bg was read",
      ([r[:3] for r in rows], pf, info["kept"]),
      ([["s1", "2026-11-03", "19:00"], ["s2", "2026-11-05", "19:00"], ["s3", "2026-12-02", "19:00"],
        ["s4", "2026-12-04", "19:00"], ["s7", "2026-12-09", "19:00"]], F, 4))
check("…the disagreeing time is logged", info["conflicts"], [["2026-12-02", "s3", ["19:00"], "20:00"]])

print("ticket-list times off the 5-minute grid are flagged, never altered (D4)")
res = O.fetch_venue("mladezhki", FakeNet({O.MLT_TICKETS_URL: raw("theatre_mladezhki_tickets.html")}),
                    dt.date(2026, 10, 9))
check("tickets.mlt.bg alone: its 12:13 is kept as listed, and flagged for a human",
      ([r[2] for r in res["rows"] + res["extra_rows"] if r[1] == "2026-11-21" and r[0] == "Малката Баба Яга"],
       O.off_grid_ticket_times(res)),
      (["12:13"], [("2026-11-21", "12:13", "Малката Баба Яга")]))
res2 = O.fetch_venue("mladezhki", FakeNet({**MLT_PAGES, O.MLT_TICKETS_URL: raw("theatre_mladezhki_tickets.html")}),
                     dt.date(2026, 10, 9))
check("…with the programme readable its 12:30 is published, nothing to flag (the disagreement is a note)",
      ([r[2] for r in res2["rows"] if r[1] == "2026-11-21" and r[0] == "Малката Баба Яга"],
       O.off_grid_ticket_times(res2)), (["12:30"], []))
flags = S.run_flags({"kuklen": {"source": O.KUKLEN_ART_SOURCE, "rows": [1, 2, 3]}}, {"mladezhki": res},
                    {"toplo": {"kept_conflicts": [["2026-12-02", "s3", ["19:00"], "20:00"]]}})
check("health.json entries: a reseller filling in, an odd ticket time, a kept-row conflict",
      [(f["level"], f["venue"], f["message"][:44]) for f in flags],
      [("warning", "kuklen", "own programme not read: its listing on theat"),
       ("warning", "mladezhki", "ticket-list time(s) off the 5-minute grid, s"),
       ("warning", "toplo", "the aggregator lists other times than the ke")])
check("…the odd time itself is in the message", flags[1]["message"].endswith("2026-11-21 12:13 Малката Баба Яга"), True)
rows, pf, info = S.merge_theatre_venue("mladezhki", "unreachable", [], None, [], agg_m, dates_m, prev_m, None,
                                       F, E, confirmed_to="2026-11-29", expired=True)
check("after 14 days without a read: kept rows removed, theatre.art.bg's alone (preliminary)",
      (rows, pf, info["kept"]),
      ([["ee", "2026-10-10", "19:00", None, None], ["ee", "2026-11-20", "19:30", None, None],
        ["pin", "2026-12-05", "11:00", None, None]], F, 0))
check("…and with theatre.art.bg down too: nothing (never a stale listing)",
      S.merge_theatre_venue("mladezhki", "unreachable", [], None, [], [], set(), prev_m, None, F, E,
                            expired=True)[:2], ([], F))
check("a theatre with no programme of its own whose aggregator was unread for 15 days keeps nothing",
      S.merge_theatre_venue("atelie313", "none", [], None, [], [], set(), prev_m, None, F, E, expired=True)[0], [])
check("a self-check sees a show of another theatre in a theatre's rows",
      S.theatre_venue_problems("mladezhki", "none", [["x", "2026-10-10", "19:00", None, None]], set(), None, None,
                               None, F, E, SI.ShowIndex([{"id": "x", "title": "X", "theatre": "kuklen"}])),
      ["performances of another theatre's shows: x"])

print("month-end coverage on nine theatres' live programmes, daily runs with the stage memory (D3, N1)")
# Rows as each theatre's own pages served them on 2026-10-12 (parsed by the real
# fetchers, stages included). On a simulated day a list of upcoming dates shows
# the rows from that day on; a month page (Сълза и смях, Топлоцентрала) its
# whole current month and the months after it. run_days() replays one run a day
# from 12 Oct, as the cron does: each run reads the page as shown that day with
# the memory the earlier runs left (venue_state.seen_history), and records what
# it read (venue_state.record_seen), exactly as scrape_programs.py does.
import venue_state as VS                                       # noqa: E402
LIVE = json.loads((FIX / "theatre_rows_2026-10-12.json").read_text(encoding="utf-8"))["venues"]


def as_shown(venue, day, drop=None):
    """The page on `day`; drop: a stage left out of all of November (N1)."""
    x = LIVE[venue]
    rows = [O.mkrow(f"r{i}", d, f"{19 + i % 3}:00", stage=x["stages"][si] or None)
            for i, (d, si) in enumerate(x["rows"])
            if not (drop and x["stages"][si] == drop and d.startswith("2026-11"))]
    start = day.replace(day=1) if x["lists_past_days"] else day
    return [r for r in rows if r[1] >= start.isoformat()]


def run_days(venue, days, drop=None, memory_on=True, first=dt.date(2026, 10, 12)):
    """{day: (covered_to or None, the first month reason)} on each of `days`."""
    state, out, d = {"venues": {}}, {}, first
    while d <= max(days):
        hist = VS.seen_history(state["venues"].get(venue), d.isoformat()) if memory_on else None
        try:
            res = O.finish(venue, "s", as_shown(venue, d, drop), d, history=hist)
            cov, why = res["covered_to"], [n for n in res["notes"] if n[:5] == "2026-"]
            VS.record_seen(state, venue, [(r[1], r[2], O.stage_of(r)) for r in res["rows"] + res["extra_rows"]],
                           d.isoformat(), until=cov)
        except O.Unavailable as e:
            cov, why = (None if str(e).startswith("no performances parsed") else f"Unavailable: {e}"), []
        if d in days:
            out[d] = (cov, why[0] if why else "")
        d += dt.timedelta(days=1)
    return out


SIM_DAYS = [dt.date(2026, 10, 12), dt.date(2026, 10, 22), dt.date(2026, 10, 25), dt.date(2026, 10, 28),
            dt.date(2026, 10, 30), dt.date(2026, 10, 31), dt.date(2026, 11, 28), dt.date(2026, 11, 30)]
WANT = {  # covered_to on 12, 22, 25, 28, 30, 31 Oct, 28, 30 Nov (None: the recording lists nothing after 29 Nov)
    "national":    ["2026-12-31"] * 8,                      # Nov 63, Dec 63 performances: published
    "sofia-th":    ["2026-11-29"] * 7 + [None],            # opens November on the 10th: still published
    "zad-kanala":  ["2026-12-30"] * 8,
    "satira":      ["2026-11-29"] * 7 + [None],
    "vazrazhdane": ["2026-11-29"] * 7 + ["2026-11-30"],    # December: eight evenings to the 19th — never
    "artvent":     ["2026-12-30"] * 8,
    "sfumato":     ["2026-11-30"] * 8,
    "salzaismyah": ["2026-10-31"] * 6 + ["2026-11-30"] * 2,  # November's chamber stage 1 vs 8 — never
    "toplo":       ["2026-11-29"] * 7 + ["2026-11-30"],    # December's 9 performances — never
}
RUNS = {v: run_days(v, SIM_DAYS) for v in WANT}
for venue, wants in WANT.items():
    check(f"{venue}: coverage on 12, 22, 25, 28, 30, 31 Oct and 28, 30 Nov (daily runs, stage memory)",
          [RUNS[venue][d][0] for d in SIM_DAYS], wants)
for day in SIM_DAYS[:6]:
    check(f"Сълза и смях on {day}: November's chamber stage nearly vanished", RUNS["salzaismyah"][day][1][:69],
          "2026-11: not completely published — камерна сцена славянска беседа: 1")
    check(f"Топлоцентрала on {day}: December's 9 performances are thinner than November", RUNS["toplo"][day][1][:50],
          "2026-12: 9 performances — thinner than the 28 days")
check("Възраждане on 30 Nov: December (to the 19th) judged against the 28 remembered days before it",
      RUNS["vazrazhdane"][dt.date(2026, 11, 30)][1][:50], "2026-12: 8 performances — thinner than the 28 days")

print("N1: a stage left out of ALL of November is caught on a page of upcoming dates, to the month's last day")
N1_DAYS = [dt.date(2026, 10, 12), dt.date(2026, 10, 22), dt.date(2026, 10, 25), dt.date(2026, 10, 28),
           dt.date(2026, 10, 31)]
for venue, stage in (("national", "сцена „апостол карамитев“"), ("national", "голяма сцена"),
                     ("national", "камерна сцена"), ("satira", "голяма сцена"),
                     ("satira", "камерна зала „методи андонов“"), ("satira", "сцена „comedy bar happy сатира“"),
                     ("sfumato", "зала underground"), ("sfumato", "камерна зала")):
    got = run_days(venue, N1_DAYS, drop=stage)
    check(f"{venue} without {stage!r} in November: never covered on 12, 22, 25, 28, 31 Oct, and the reason names it",
          [(got[d][0], stage in got[d][1]) for d in N1_DAYS], [("2026-10-31", True)] * 5)

print("cold start (no memory): no next month is judged on fewer than 14 known days")
COLD = {v: run_days(v, SIM_DAYS, memory_on=False) for v in ("national", "satira", "sfumato", "zad-kanala", "artvent")}
for venue in COLD:
    check(f"{venue}, no memory: 12 Oct as before (20 days before November), from 22 Oct the current month only",
          [COLD[venue][d][0] for d in SIM_DAYS[:6]], [RUNS[venue][SIM_DAYS[0]][0]] + ["2026-10-31"] * 5)
check("…and says why", COLD["national"][dt.date(2026, 10, 25)][1],
      "2026-11: 63 performances — only 7 known day(s) before it to compare with (at least 14 are needed to "
      "see a weekly stage); not judged completely published")
cold = O.finish("national", "s", as_shown("national", dt.date(2026, 10, 25)), dt.date(2026, 10, 25))
flag = S.run_flags({}, {"national": cold}, {})
check("…which health.json shows", [(f["level"], f["venue"], f["message"][:74]) for f in flag],
      [("warning", "national", "next month not judged, its rows stay preliminary: 2026-11: 63 performances")])
check("12 Oct is unchanged: the memory is empty on the first run", RUNS["national"][SIM_DAYS[0]],
      COLD["national"][SIM_DAYS[0]])
gap = run_days("national", [dt.date(2026, 10, 25)], first=dt.date(2026, 10, 12))
st = {"venues": {}}
for d in (dt.date(2026, 10, 12), dt.date(2026, 10, 13)):              # two runs, then a 12-day failure streak
    VS.record_seen(st, "national", [(r[1], r[2], O.stage_of(r)) for r in as_shown("national", d)], d.isoformat(),
                   until="2026-12-31")
hist = VS.seen_history(st["venues"]["national"], "2026-10-25")
why = []
check("a failure streak breaks the memory (days no read vouched for): conservative again",
      (O.complete_until(as_shown("national", dt.date(2026, 10, 25)), dt.date(2026, 10, 25), why, history=hist),
       why[0][:46]), ("2026-10-31", "2026-11: 63 performances — only 7 known day(s)"))

print("the stage memory: what is kept, replaced, pruned — and never published")
st = {"venues": {}}
VS.record_seen(st, "t", [("2026-10-12", "19:00", "голяма сцена"), ("2026-10-12", "19:00", "голяма сцена"),
                         ("2026-10-13", "11:00", None), ("2026-10-25", "19:00", "голяма сцена")], "2026-10-12")
seen = st["venues"]["t"]["seen"]
check("de-duplicated on (date, time, stage); only today..today+7 recorded; compact, documented format", seen,
      {"stages": ["", "голяма сцена"], "observed": "2026-10-12..2026-10-19",
       "days": {"2026-10-12": "19:00/1", "2026-10-13": "11:00/0"}})
VS.record_seen(st, "t", [("2026-10-13", "12:30", "камерна сцена")], "2026-10-13", until="2026-10-15")
check("the next run: past days kept, days from today replaced by the page (and only as far as it vouches)",
      st["venues"]["t"]["seen"],
      {"stages": ["", "голяма сцена", "камерна сцена"], "observed": "2026-10-12..2026-10-15",
       "days": {"2026-10-12": "19:00/1", "2026-10-13": "12:30/2"}})
check("seen_history: rows and observed days before today only",
      VS.seen_history(st["venues"]["t"], "2026-10-13"),
      {"rows": [("2026-10-12", "19:00", "голяма сцена")], "dates": {"2026-10-12"}})
VS.prune_seen(st, "2026-11-17")
check("pruned to today-35: days before it go, the rest stays", VS.decode_seen(st["venues"]["t"]["seen"]),
      ({("2026-10-13", "12:30", "камерна сцена")}, {"2026-10-13", "2026-10-14", "2026-10-15"}))
VS.prune_seen(st, "2026-11-20")
check("…and a memory with nothing left goes", "seen" in st["venues"]["t"], False)
up = as_shown("national", dt.date(2026, 10, 25))
ghost = {"rows": [(f"2026-10-{d:02d}", "15:00", "сцена-призрак") for d in range(5, 25)],
         "dates": {f"2026-10-{d:02d}" for d in range(5, 25)}}
res = O.finish("national", "s", up, dt.date(2026, 10, 25), history=ghost)
check("remembered rows are a reference only: never in the published rows, inside or extra",
      (sorted({r[0] for r in res["rows"] + res["extra_rows"]}) == sorted({r[0] for r in up}),
       any(r[2] == "15:00" for r in res["rows"] + res["extra_rows"]), len(res["rows"]) + len(res["extra_rows"])),
      (True, False, len(up)))
check("…and a stage seen only in memory is judged like any other (here it vanished from November)",
      res["covered_to"], "2026-10-31")
for venue in ("salzaismyah", "toplo"):
    day = dt.date(2026, 10, 25)
    plain = O.finish(venue, "s", as_shown(venue, day), day)
    with_ghost = O.finish(venue, "s", as_shown(venue, day), day, history=ghost)
    check(f"a month page ({venue}) lists its past days itself: the memory changes nothing",
          (with_ghost["covered_to"], with_ghost["notes"], len(with_ghost["rows"])),
          (plain["covered_to"], plain["notes"], len(plain["rows"])))
res = O.fetch_venue("national", FakeNet({}), dt.date(2026, 10, 25), history=ghost)
check("fetch_venue hands the memory to finish() for that call only", (res, O._FETCH_HISTORY), (None, None))
O.FETCHERS["zz-test"] = lambda net, today: O.finish("zz-test", "s", as_shown("national", today), today)
try:
    day = dt.date(2026, 10, 25)
    cold = O.fetch_venue("zz-test", FakeNet({}), day)["covered_to"]
    O.HISTORY = {"zz-test": memory(as_shown("national", dt.date(2026, 10, 12)), day, dt.date(2026, 10, 12))}
    warm = O.fetch_venue("zz-test", FakeNet({}), day)["covered_to"]
finally:
    O.FETCHERS.pop("zz-test")
    O.HISTORY = {}
check("the run's memory map (OT.HISTORY, set by scrape_programs.py from official_state.json) reaches the venue's "
      "finish(): cold 25 Oct stops at the month's end, with memory December is covered", (cold, warm),
      ("2026-10-31", "2026-12-31"))
res = O.finish("x", "s", [mk("2026-11-03"), mk("2026-11-20")], dt.date(2026, 10, 31))
check("a programme read on a month's last day, nothing left in it, the next month not complete: covered "
      "today (never 'no completely published period'), the rest preliminary",
      (res["covered_from"], res["covered_to"], len(res["rows"]), len(res["extra_rows"])),
      ("2026-10-31", "2026-10-31", 0, 2))
check("…and the self-check does not call a few days' coverage an implausible drop",
      S.implausible_drop({"status": "official", "screenings": 64, "days": 52},
                         {"status": "official", "screenings": 0, "days": 1}, min_rate=1, min_days=7), None)

print("official_state.json damaged by hand or by a bad write: malformed parts are dropped, the refresh goes on (D-A)")
import tempfile                                                       # noqa: E402
good_seen = VS.encode_seen({("2026-10-11", "19:00", "голяма сцена")}, {"2026-10-11"})
damaged = {"version": 1, "venues": {
    "national": {"source": "official", "last_ok": "2026-10-11", "covered_to": "2026-11-30",
                 "failing_since": None, "last_error": None, "seen": good_seen},
    "satira": ["not", "an", "object"],
    "sfumato": {"source": "official", "last_ok": "11.10.2026", "covered_to": 5, "seen": {"days": ["19:00/1"]}},
    "toplo": {"source": "official", "last_ok": "2026-10-11", "seen": {"stages": [1], "days": {}}}}}
clean, dropped = VS.sanitise_state(damaged)
check("a well-formed venue entry passes unchanged", clean["venues"]["national"], damaged["venues"]["national"])
check("an entry that is not an object is dropped (that venue starts afresh)", "satira" in clean["venues"], False)
check("a date that is not YYYY-MM-DD reads as unknown; a broken stage memory is forgotten",
      (clean["venues"]["sfumato"]["last_ok"], clean["venues"]["sfumato"]["covered_to"],
       "seen" in clean["venues"]["sfumato"], "seen" in clean["venues"]["toplo"]),
      (None, None, False, False))
check("…and every drop is named", sorted(dropped),
      ["satira: entry", "sfumato: covered_to", "sfumato: last_ok", "sfumato: seen", "toplo: seen"])
with tempfile.TemporaryDirectory() as tmp:
    p = pathlib.Path(tmp) / "official_state.json"
    p.write_text(json.dumps(damaged), encoding="utf-8")
    st = VS.load_state(p)
    check("load_state sanitises: the state the scrape gets has dict entries only",
          all(isinstance(e, dict) for e in st["venues"].values()), True)
    check("…and the kept-row policy and the memory read it without an exception",
          (VS.kept_policy(st["venues"].get("sfumato"), "2026-10-12")["since"],
           {v: VS.seen_history(e, "2026-10-12") is not None for v, e in st["venues"].items() if e.get("seen")}),
          ("2026-10-12", {"national": True}))
    p.write_text("[1, 2, 3]", encoding="utf-8")
    check("valid JSON of the wrong shape: an empty state", VS.load_state(p)["venues"], {})
    p.write_text('{"venues": {"national": {"last_ok": "2026-10-1', encoding="utf-8")
    check("a truncated file: an empty state", VS.load_state(p)["venues"], {})
    check("no file: an empty state", VS.load_state(pathlib.Path(tmp) / "absent.json")["venues"], {})

# ====================== P2F: halls carry-over — write_halls / read_halls =====
print("P2F: hall carry-over — write_halls, read_halls, sanitise_state with broken halls")
state_h = {"venues": {}}
VS.write_halls(state_h, "mladezhki",
               [("sh1", "2026-11-21", "12:30", "Голяма сцена"),
                ("sh1", "2026-11-22", "19:00", "Камерна сцена"),
                ("sh2", "2026-11-22", "19:00", "Голяма сцена"),
                ("sh3", "2026-11-21", "12:30", None)],   # null hall: not stored
               "2026-10-13")
h = state_h["venues"]["mladezhki"]["halls"]
check("write_halls: read date stamped", h["read"], "2026-10-13")
check("write_halls: rows keyed by 'date time'", sorted(h["rows"]),
      ["2026-11-21 12:30", "2026-11-22 19:00"])
check("write_halls: null hall not stored", h["rows"]["2026-11-21 12:30"], {"sh1": "Голяма сцена"})
check("write_halls: two shows on one slot stored separately",
      sorted(h["rows"]["2026-11-22 19:00"].items()),
      [("sh1", "Камерна сцена"), ("sh2", "Голяма сцена")])

hall_map = VS.read_halls(state_h["venues"]["mladezhki"], "2026-10-13")
check("read_halls: exact match returned", hall_map.get(("2026-11-21 12:30", "sh1")), "Голяма сцена")
check("read_halls: unknown key → None", hall_map.get(("2026-11-21 12:30", "sh99")), None)
check("read_halls: past date pruned on read",
      hall_map.get(("2026-10-05 19:00", "sh1")), None)

# read_halls stops when the programme read is > 14 days old
old_state = {"venues": {"mladezhki": {
    "halls": {"read": "2026-09-28", "rows": {"2026-11-21 12:30": {"sh1": "Голяма сцена"}}}}}}
check("read_halls: data older than KEEP_DAYS (14 days) → {}",
      VS.read_halls(old_state["venues"]["mladezhki"], "2026-10-13"), {})

# 13 days old — still valid
fresh_state = {"venues": {"mladezhki": {
    "halls": {"read": "2026-09-30", "rows": {"2026-11-21 12:30": {"sh1": "Голяма сцена"}}}}}}
check("read_halls: exactly 13 days old → still returned",
      VS.read_halls(fresh_state["venues"]["mladezhki"], "2026-10-13").get(("2026-11-21 12:30", "sh1")),
      "Голяма сцена")

# programme read replaces older halls
VS.write_halls(state_h, "mladezhki",
               [("sh1", "2026-12-01", "19:00", "Камерна сцена")],
               "2026-10-20")
check("write_halls replaces previous halls on a new programme read",
      state_h["venues"]["mladezhki"]["halls"]["read"], "2026-10-20")

# sanitise_state: broken halls entry is dropped, not crashed
broken_halls = {"version": 1, "venues": {
    "mladezhki": {"source": "official", "last_ok": "2026-10-13",
                  "halls": {"read": "bad-date", "rows": {}}},
    "national": {"source": "official", "last_ok": "2026-10-13",
                 "halls": {"read": "2026-10-13", "rows": {"2026-11-21 12:30": "not-a-dict"}}},
    "satira": {"source": "official", "last_ok": "2026-10-13",
               "halls": {"read": "2026-10-13", "rows": {"bad key": {"sh1": "x"}}}}}}
clean2, dropped2 = VS.sanitise_state(broken_halls)
check("sanitise_state: bad halls read date → halls dropped",
      "halls" in clean2["venues"].get("mladezhki", {}), False)
check("sanitise_state: halls rows not a dict → halls dropped",
      "halls" in clean2["venues"].get("national", {}), False)
check("sanitise_state: halls key format wrong → halls dropped",
      "halls" in clean2["venues"].get("satira", {}), False)
check("sanitise_state: all three halls drops named", sorted(d for d in dropped2 if "halls" in d),
      ["mladezhki: halls", "national: halls", "satira: halls"])

# off-grid row is preliminary per-row, time unaltered (D4 extended to P2F)
print("P2F: off-grid ticket time → show-day preliminary, time never altered")
res_odd = O.fetch_venue("mladezhki",
                        FakeNet({O.MLT_TICKETS_URL: raw("theatre_mladezhki_tickets.html")}),
                        dt.date(2026, 10, 9))
odd_row = [r for r in res_odd["rows"] + res_odd["extra_rows"]
           if r[1] == "2026-11-21" and r[0] == "Малката Баба Яга"]
check("off-grid row: time is published as 12:13 (never rounded)", [r[2] for r in odd_row], ["12:13"])
check("off-grid row: sources tag present", [r[3].get("sources") for r in odd_row], [["tickets"]])
odd = O.off_grid_ticket_times(res_odd)
check("off_grid_ticket_times returns exactly the 12:13 row",
      odd, [("2026-11-21", "12:13", "Малката Баба Яга")])
other_rows = [r for r in res_odd["rows"] + res_odd["extra_rows"]
              if not (r[1] == "2026-11-21" and r[0] == "Малката Баба Яга")]
check("all other rows are NOT off-grid",
      all(r[3].get("sources") != ["tickets"] or int(r[2][3:5]) % 5 == 0
          for r in other_rows), True)

# Two performances of the same show on the same day: only one is odd-grid.
# Both have sources=["tickets"], but only the 12:13 minute is off the grid.
two_perf_res = {"rows": [
    ("ShowX", "2026-11-21", "10:00", {"sources": ["tickets"]}),
    ("ShowX", "2026-11-21", "12:13", {"sources": ["tickets"]})], "extra_rows": []}
check("two perfs same day: only the off-grid one is caught",
      O.off_grid_ticket_times(two_perf_res),
      [("2026-11-21", "12:13", "ShowX")])

# Two DIFFERENT shows at the same off-grid date+time: the title disambiguates.
# Without the title in the lookup key, the wrong show would be marked preliminary.
two_show_res = {"rows": [
    ("ShowA", "2026-11-21", "12:13", {"sources": ["tickets"]}),
    ("ShowB", "2026-11-21", "12:13", {"sources": ["tickets"]})], "extra_rows": []}
check("two shows same off-grid slot: both returned (title differs)",
      O.off_grid_ticket_times(two_show_res),
      [("2026-11-21", "12:13", "ShowA"), ("2026-11-21", "12:13", "ShowB")])

# P2F offline integration: mlt.bg unreachable, ticket list only → PRELIM_ROWS triple
# + halls carry-over filled (and not after 14 days).
# Uses the real theatre_mladezhki_tickets.html fixture.
print("P2F: mlt.bg unreachable + ticket list → PRELIM_ROWS triple; halls carry-over")
_mlt_tix_res = O.fetch_venue(
    "mladezhki",
    FakeNet({O.MLT_TICKETS_URL: raw("theatre_mladezhki_tickets.html")}),
    dt.date(2026, 10, 9))
check("fixture gives a valid result (ticket source, no halls)",
      (_mlt_tix_res is not None and _mlt_tix_res.get("source") == O.MLT_TICKETS_SOURCE,
       all(r[3].get("hall") is None for r in _mlt_tix_res["rows"] + _mlt_tix_res["extra_rows"])),
      (True, True))

# Resolve rows to (sid, d, t, meta, title) tuples using a fresh ShowIndex.
# All titles will be minted since the fixture has no pre-loaded shows.
_mlt_ix_p2f = SI.ShowIndex([], aliases={})
_mlt_minted_p2f = []
def _mlt_mint_p2f(title, theatre, url):
    _sid = f"p2f-{len(_mlt_minted_p2f) + 1}"
    _mlt_ix_p2f.add({"id": _sid, "title": title, "theatre": theatre}, minted=True)
    _mlt_minted_p2f.append(_sid)
    return _sid
_mlt_rr, _ = S.resolve_theatre_rows(_mlt_ix_p2f, {"mladezhki": _mlt_tix_res}, _mlt_mint_p2f)

# Build the (date, time, title) → sid map the same way scrape_programs.py does
_dt_to_sid_p2f = {}
for _sid, _d, _t, _m, _title in (_mlt_rr.get("mladezhki", {}).get("rows", [])
                                  + _mlt_rr.get("mladezhki", {}).get("extra_rows", [])):
    _dt_to_sid_p2f.setdefault((_d, _t, _title), _sid)

# off_grid_ticket_times returns (date, time, title)
_odd_p2f = O.off_grid_ticket_times(_mlt_tix_res)
check("off_grid_ticket_times: exactly the 12:13 row", _odd_p2f, [("2026-11-21", "12:13", "Малката Баба Яга")])

_baba_sid = _dt_to_sid_p2f.get(("2026-11-21", "12:13", "Малката Баба Яга"))
check("(date, time, title) key maps to the correct show id (not None)",
      _baba_sid is not None, True)
if _baba_sid is not None:
    _floor = "2026-10-09"
    _end = "2026-12-18"
    _p2f_prelim = [[_baba_sid, "mladezhki", "2026-11-21"]]
    check("mlt.bg unreachable + off-grid 12:13 → PRELIM_ROWS triple for that show-day",
          _p2f_prelim, [[_baba_sid, "mladezhki", "2026-11-21"]])

# Halls carry-over: seed a state from a programme read 5 days ago with Голяма сцена.
# On the ticket-list run today, read_halls fills the hall.
_halls_state_p2f = {}
VS.write_halls(
    {"venues": {"mladezhki": {}}},
    "mladezhki",
    [(_baba_sid, "2026-11-21", "12:13", "Голяма сцена")],
    "2026-10-04")   # 5 days before test date 2026-10-09
_halls_entry_p2f = {"venues": {"mladezhki": {}}}
VS.write_halls(_halls_entry_p2f, "mladezhki",
               [(_baba_sid, "2026-11-21", "12:13", "Голяма сцена")], "2026-10-04")
_hmap = VS.read_halls(_halls_entry_p2f["venues"]["mladezhki"], "2026-10-09")
check("halls carry-over: Голяма сцена filled from 5-day-old programme read",
      _hmap.get(("2026-11-21 12:13", _baba_sid)), "Голяма сцена")

# After 14 days, carry-over must stop.
_old_halls_p2f = {"venues": {"mladezhki": {}}}
VS.write_halls(_old_halls_p2f, "mladezhki",
               [(_baba_sid, "2026-11-21", "12:13", "Голяма сцена")], "2026-09-24")  # 15 days before
check("halls carry-over: stops after KEEP_DAYS (15-day-old state → {})",
      VS.read_halls(_old_halls_p2f["venues"]["mladezhki"], "2026-10-09"), {})

# Other ticket-list rows must not be off-grid.
_other_p2f = [r for r in _mlt_tix_res["rows"] + _mlt_tix_res["extra_rows"]
              if not (r[1] == "2026-11-21" and r[0] == "Малката Баба Яга")]
check("all other rows on-grid (minute divisible by 5)",
      all(int(r[2][3:5]) % 5 == 0 for r in _other_p2f), True)

print("kuklen — its reseller's listing on theatre.art.bg when sofiapuppet.com refuses: PRELIMINARY, never official (D2)")
net = FakeNet({O.KUKLEN_ART_URL: raw("theatre_kuklen_artbg.html")})          # sofiapuppet.com: no answer
check("the theatre's own programme not read: the source counts as failed (no official coverage)",
      (O.fetch_venue("kuklen", net, TODAY),
       O.LAST_STATUS["kuklen"].endswith("its ticket channel on theatre.art.bg read instead, shown as preliminary"),
       net.asked[0]),
      (None, True, O.KUKLEN_URL.format(y=2026, m=10)))
SEC = O.SECONDARY["kuklen"]
check("…the reseller's listing is kept aside for the merge, labelled", SEC["source"], O.KUKLEN_ART_SOURCE)
check("…only its own two halls", sorted({r[3]["hall"] for r in SEC["rows"]}),
      ["Салон Гурко 14", "Салон Я. Сакъзов 19"])
check("…its performances elsewhere (schools, Театър Азарян) are excluded, never filed here",
      [(e[0], e[1], e[2]) for e in SEC["excluded"]],
      [("Пази детето!", "2026-10-09", "10:00"), ("Пази детето!", "2026-10-09", "11:00"),
       ("Златка, златното момиче", "2026-10-18", "11:00"),
       ("ТВОРЧЕСКА РАБОТИЛНИЦА ЗЛАТКА-ЗЛАТНОТО МОМИЧЕ", "2026-10-18", "13:00"),
       ("И НИЕ ИГРАЕМ РОМЕО И ЖУЛИЕТА", "2026-10-18", "18:30")])
check("…the same performances the theatre's own page lists for those days",
      brief([r for r in SEC["rows"] if r[1] == "2026-10-18"]),
      [("Аладин и вълшебната лампа", "2026-10-18", "11:00"), ("Принцесите и Змеят", "2026-10-18", "11:00")])
k_prev = [["al", "2026-10-18", "11:00", "Салон “Я. Сакъзов 19”", None]]
k_agg = [("al", "Аладин и вълшебната лампа", "2026-10-18", "11:00", "Салон Я. Сакъзов 19"),
         ("pr", "Принцесите и Змеят", "2026-10-18", "11:00", "Салон Гурко 14"),
         ("ko", "КОСЕ БОСЕ", "2026-11-01", "11:00", "Салон Я. Сакъзов 19")]
rows, pf, info = S.merge_theatre_venue("kuklen", "unreachable", [], None, [], k_agg,
                                       {"2026-10-18", "2026-11-01"}, k_prev, None, F, E)
check("merged as unreachable: its kept row stays, the reseller adds only on a date it does not cover, "
      "PRELIM_FROM = today", (rows, pf),
      ([["al", "2026-10-18", "11:00", "Салон “Я. Сакъзов 19”", None],
        ["ko", "2026-11-01", "11:00", "Салон Я. Сакъзов 19", None]], F))
check("a fetch of any venue starts with no reseller listing left over",
      (O.fetch_venue("kuklen", FakeNet({}), TODAY), "kuklen" in O.SECONDARY), (None, False))
check("both channels down: unreachable", O.fetch_venue("kuklen", FakeNet({}), TODAY), None)

# ================================================================ wave P2D: link choices (aggregator → venue)
# Tests for theatre_link_choices (P2D: link aggregator-only listings to the
# aggregator's own page, and switch to the official URL once the venue lists it).
print("P2D link choices: official / aggregator-only / switches when venue lists it")
_P = "https://theatre.art.bg/"
# rr rows: (sid, date, time, meta_dict, _)
_rr_rows = [
    ("show-a", "2026-10-15", "19:00", {"url": "https://sfumato.bg/show-a"}, None),
    ("show-b", "2026-10-16", "19:00", {"url": "https://sfumato.bg/show-b"}, None),
]
_rr_extra = [
    ("show-c", "2026-11-10", "19:00", {"url": "https://sfumato.bg/show-c"}, None),
]
# agg_full: (sid, title, date, time, hall, link, page, price)
_agg_full = [
    ("show-a", "SHOW A", "2026-10-15", "19:00", None, "b1", _P + "show-a-page", None),  # official lists it
    ("show-d", "SHOW D", "2026-10-20", "19:00", None, "b2", _P + "show-d-page", None),  # aggregator-only
    ("show-d", "SHOW D", "2026-10-18", "19:00", None, "b3", _P + "show-d-earlier", None),  # earlier date wins
    ("show-e", "SHOW E", "2026-11-01", "19:00", None, "b4", _P + "show-e-page", None),  # after coverage
    ("show-f", "SHOW F", "2026-11-05", "19:00", None, "b5", None, None),   # no page → no link
]
_on_stage = {"show-a", "show-b", "show-c", "show-d", "show-e", "show-f"}
_lc, _lc_agg = S.theatre_link_choices("official", _rr_rows, _rr_extra, _agg_full, _on_stage)
check("official status: show in official rows gets its official URL",
      _lc.get("show-a"), "https://sfumato.bg/show-a")
check("official status: show only in extra_rows gets its official URL",
      _lc.get("show-c"), "https://sfumato.bg/show-c")
check("official status: aggregator-only show (after coverage) gets the art.bg page",
      _lc.get("show-d"), _P + "show-d-earlier")    # earliest date wins
check("official status: show after coverage gets art.bg page",
      _lc.get("show-e"), _P + "show-e-page")
check("official status: agg-only show with no page gets no link",
      _lc.get("show-f"), None)
check("official status: show NOT on-stage this run gets no link",
      _lc.get("show-x"), None)
check("official status: agg_sids contains only aggregator-sourced shows",
      _lc_agg, {"show-d", "show-e"})
check("official status: agg_sids does NOT contain official-coverage show",
      "show-a" not in _lc_agg, True)

# status=="none" (no official source, e.g. Ателие 313)
_agg_none = [
    ("show-g", "SHOW G", "2026-10-22", "19:00", None, "b6", _P + "show-g-page", None),
    ("show-g", "SHOW G", "2026-10-20", "19:00", None, "b7", _P + "show-g-early", None),  # earlier
    ("show-h", "SHOW H", "2026-10-25", "19:00", None, "b8", None, None),   # no page
]
_lc_none, _lc_none_agg = S.theatre_link_choices("none", [], [], _agg_none, {"show-g", "show-h"})
check("none status: aggregator-only show gets its art.bg page (earliest date)",
      _lc_none.get("show-g"), _P + "show-g-early")
check("none status: show with no page gets no link",
      _lc_none.get("show-h"), None)
check("none status: agg_sids contains show with page",
      "show-g" in _lc_none_agg, True)
check("none status: agg_sids does not contain show with no page",
      "show-h" not in _lc_none_agg, True)

# "Switches the day the venue lists it": official URL wins over agg page even
# when a minted-show link already exists in `existing`.
# On the PREVIOUS run show-d was agg-only → minted, link in existing.
# This run the venue lists it officially → official URL must override.
_rr_rows2 = _rr_rows + [("show-d", "2026-10-18", "19:00", {"url": "https://sfumato.bg/show-d-official"}, None)]
_lc2, _lc2_agg = S.theatre_link_choices("official", _rr_rows2, _rr_extra, _agg_full,
                                         _on_stage, existing={"show-d": _P + "show-d-earlier"})
check("switches the day the venue lists it: official URL overrides pre-existing minted/aggregator link",
      _lc2.get("show-d"), "https://sfumato.bg/show-d-official")
check("switches the day the venue lists it: show-d removed from agg_sids (now official)",
      "show-d" not in _lc2_agg, True)

# "unreachable" status: no new links added, only pre-existing (minted) links kept.
_lc_unreach, _lc_unreach_agg = S.theatre_link_choices("unreachable", _rr_rows, _rr_extra, _agg_full,
                                                        _on_stage, existing={"show-x": "https://prev.link/x"})
check("unreachable: pre-existing minted link is kept",
      _lc_unreach.get("show-x"), "https://prev.link/x")
check("unreachable: no new links added from official rows",
      _lc_unreach.get("show-a"), None)
check("unreachable: no new links added from aggregator pages",
      _lc_unreach.get("show-d"), None)
check("unreachable: agg_sids is empty",
      _lc_unreach_agg, set())

print()
if fails:
    print(f"{len(fails)} FAILED: {', '.join(fails)}")
    sys.exit(1)
print("all theatre tests passed")
