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
res = O.finish("x", "s", octo + nov_thin + [mk("2026-10-01")], TODAY)
check("finish: past dropped, thin month → extra_rows",
      (res["covered_from"], res["covered_to"], len(res["rows"]), len(res["extra_rows"])),
      ("2026-10-08", "2026-10-31", 24, 4))
raises("finish: nothing left → Unavailable", O.Unavailable, O.finish, "x", "s", [mk("2026-10-01")], TODAY)

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

print()
if fails:
    print(f"{len(fails)} FAILED: {', '.join(fails)}")
    sys.exit(1)
print("all theatre tests passed")
