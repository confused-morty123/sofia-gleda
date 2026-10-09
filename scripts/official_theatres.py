#!/usr/bin/env python3
"""Sofia Gleda — every theatre's OWN official programme (wave M2).

Why this exists: the theatre listings were assembled from an aggregator plus a
generic "find a date and a time near each other" page scan, and the 2026-10-08
audit found them wrong in ways a visitor acts on — 39 Artvent touring dates in
Варна / Козлодуй / Велико Търново filed as Sofia, two I AM Studio shows with
their dates swapped, whole weeks of the Youth and Puppet theatres missing. The
owner's rule: a listing must match the venue's own programme, always. This
module reads those programmes and nothing else; it never writes a file.

One fetcher per theatre id. Each returns None — the source is unreachable, its
markup no longer has the shape the parser expects, or it is implausibly empty
(keep the previous rows) — or a dict:

    {"venue": id,
     "rows": [(title_as_published, "YYYY-MM-DD", "HH:MM",
               {"hall": str|None, "price": str|None, "url": str|None,
                "premiere": bool, ...optional: "preview", "guest", "kind",
                "sold_out", "sources", "tickets_title"})],
     "covered_from": "YYYY-MM-DD", "covered_to": "YYYY-MM-DD",
     "source": label,
     # informational extras (always present, possibly empty):
     "extra_rows": [...],     # official rows OUTSIDE covered_from..covered_to
     # optional: "confirm_by_aggregator": [date] — a listed day the source may
     # show only in part (th199's page boundary); covered when the aggregator's
     # verified listing for that day adds nothing to the source's own rows
     "excluded": [(title, date, time, reason)],   # listed, but not a Sofia
                                                  # performance at this venue
     "notes": [str]}

Contract:
  * `rows` holds only dates inside covered_from..covered_to, and every date in
    that range is authoritative: a covered date without rows means "no
    performances at this venue that day".
  * `extra_rows` are real, official performances on dates the source does NOT
    cover exhaustively — a month it has only partly published, the edge of a
    paginated window. They confirm themselves but must never be used to remove
    other listings on those dates.
  * Only performances in Sofia AT THIS VENUE become rows. Touring dates,
    off-site performances, co-productions staged at another theatre and
    cancelled performances go to `excluded` with the reason.
  * covered_from is today (Europe/Sofia) unless the source shows less.

A month is "covered" only while it looks completely published: later months
must carry at least COMPLETE_RATIO of the performance density of the next four
weeks, or SPREAD_RATIO of it with performances right through the month — AND
every stage that plays regularly in those four weeks must still be there (at
least STAGE_RATIO of its own rate). Възраждане's December (eight evenings to
the 19th, no matinées) is the half-entered state; treating it as complete would
delete real listings the theatre simply has not typed in yet. Сълза и смях's
November 2026 passed the count rules (22 evenings, every week to the 30th) but
is half-entered too: its chamber stage (Славянска беседа) has 1 date against 9
in the rest of October, and none of the children's matinées Театър София lists
for its stage that month are on its grid. Топлоцентрала's November (25
performances, its three regular halls all playing) is published.

    python3 scripts/official_theatres.py                 # every theatre, summary table
    python3 scripts/official_theatres.py --venue tba -v  # one theatre, rows printed
    python3 scripts/official_theatres.py --json /tmp/official.json
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.parse
from collections import Counter, OrderedDict, defaultdict

try:
    from zoneinfo import ZoneInfo
except ImportError:                                    # pragma: no cover
    ZoneInfo = None

try:
    from bs4 import BeautifulSoup
except ImportError:                                    # pragma: no cover
    sys.exit("pip install beautifulsoup4 lxml")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

TZ_NAME = "Europe/Sofia"
COMPLETE_RATIO = 0.6      # later month ≥ 60% of the next-four-weeks density, or
SPREAD_RATIO = 0.4        # ≥ 40% when its performances run through the whole month,
STAGE_WEEKS = 3           # and a stage playing in ≥ 3 of the next four weeks (regular)
STAGE_RATIO = 0.25        # keeps ≥ 25% of its own rate there (else it "nearly vanished")
REF_DAYS = 28
MONTHS_AHEAD = 6          # month-paged sources: current month + up to 5 more


class Unavailable(Exception):
    """This run cannot trust the source: it is unreachable, changed shape, or
    came back implausibly empty. The fetcher yields None (keep previous rows)."""


class MarkupError(Unavailable):
    """The page arrived but no longer has the shape the parser was written for."""


# --------------------------------------------------------------- time & text
def sofia_tz():
    """Europe/Sofia, or None when the tz database is missing — then the one
    source that needs it (Сатирата's UTC timestamps) refuses instead of
    guessing an offset."""
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(TZ_NAME)
    except Exception:
        return None


def sofia_today():
    tz = sofia_tz()
    return (dt.datetime.now(tz) if tz else dt.datetime.now()).date()


BG_MONTHS = ("януари", "февруари", "март", "април", "май", "юни", "юли",
             "август", "септември", "октомври", "ноември", "декември")
_MONTH3 = {m[:3]: i + 1 for i, m in enumerate(BG_MONTHS)}
BG_WEEKDAYS = ("понеделник", "вторник", "сряда", "четвъртък", "петък",
               "събота", "неделя")
_WEEKDAY_RE = re.compile(r"\b(" + "|".join(BG_WEEKDAYS) + r")\b", re.I)
_TIME_RE = re.compile(r"(?<![\d.:])([01]?\d|2[0-3])[:.]([0-5]\d)(?![\d.:]*\d)")
_DAY_MONTH_RE = re.compile(r"(\d{1,2})\s+([А-Яа-я]{3,})")


def bg_month(word):
    """'октомври' / 'Октомври' / 'Окт' / 'Ное.' → 10 / 10 / 10 / 11, else None."""
    w = (word or "").strip().lower().rstrip(".")
    m = _MONTH3.get(w[:3]) if len(w) >= 3 else None
    return m if m and BG_MONTHS[m - 1].startswith(w) else None


def weekday_conflict(day, text):
    """True when `text` names a weekday that is not `day`'s. Programmes rarely
    print the year, so this is the cheap proof that a year was inferred right."""
    m = _WEEKDAY_RE.search(text or "")
    return bool(m) and BG_WEEKDAYS.index(m.group(1).lower()) != day.weekday()


def decode(body):
    """Bytes → text. Several of these sites declare one charset and send another
    (mlt.bg's calendar fragments say windows-1251 and are UTF-8; tba.art.bg's
    programme is windows-1251), so try strict UTF-8 first — windows-1251
    Cyrillic is never valid UTF-8 — and fall back to windows-1251."""
    if body is None or isinstance(body, str):
        return body
    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return body.decode("cp1251", errors="replace")


def soup_of(text):
    return BeautifulSoup(text, "lxml")


def clean(s):
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def times_in(text):
    return [f"{int(h):02d}:{m}" for h, m in _TIME_RE.findall(text or "")]


def hhmm(text):
    t = times_in(text)
    return t[0] if t else None


def nearest_date(day, month, today, back_days=45):
    """The year is rarely printed: the first (day, month) on or after
    today - back_days. December running into January needs no special case."""
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            d = dt.date(y, month, day)
        except ValueError:
            continue
        if d >= today - dt.timedelta(days=back_days):
            return d
    return None


def months_from(today, n=MONTHS_AHEAD):
    y, m = today.year, today.month
    for _ in range(n):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def strip_quotes(title):
    """'"Фейк"' → 'Фейк', but '"Хитранка" Спектакъл…' is left alone."""
    t = clean(title)
    if (len(t) >= 2 and t[0] in "\"„“«" and t[-1] in "\"“”»"
            and not any(q in t[1:-1] for q in "\"„“”«»")):
        return t[1:-1].strip()
    return t


_GUEST_RE = re.compile(r"^(.*?)\s*(?:[-–—]\s*)?\bгостува\b\s*(.*)$", re.I)


def split_guest(title):
    """'Тяло в лед - Гостува ДТ-Русе' → ('Тяло в лед', 'ДТ-Русе')."""
    m = _GUEST_RE.match(clean(title))
    if m and m.group(1).strip(" -–—"):
        return m.group(1).strip(" -–—"), (clean(m.group(2)) or None)
    return clean(title), None


_PREVIEW_RE = re.compile(r"предпремиер", re.I)
_PREMIERE_RE = re.compile(r"(?<!пред)премиер", re.I)


def mkrow(title, date, time, hall=None, url=None, price=None, premiere=None, **extra):
    title = clean(title)
    meta = {"hall": clean(hall) or None,
            "price": clean(price) or None,
            "url": url or None,
            "premiere": (bool(_PREMIERE_RE.search(title)) if premiere is None
                         else bool(premiere))}
    if _PREVIEW_RE.search(title):
        meta["preview"] = True
    for k, v in extra.items():
        if v not in (None, False, "", [], {}):
            meta[k] = v
    iso = date.isoformat() if isinstance(date, dt.date) else date
    return (title, iso, time, meta)


def absolute(base, href):
    if not href:
        return None
    return urllib.parse.urljoin(base, href)


# ------------------------------------------------------- show-title matching
_HOMO_FROM, _HOMO_TO = "abcehkmoptxy", "авсенкмортху"
_HOMO = str.maketrans(_HOMO_FROM, _HOMO_TO)
_CYR_RE = re.compile(r"[а-яё]")
_CYCLE_PREFIX = re.compile(r"^\s*програма\s*[„“\"«”]([^„“\"«»”]*)[„“\"»”]\s*", re.I)
_DECOR = re.compile(r"""
      (?:\s*[|/\-–—:,]\s*|\s+I\s+)?\b(?:пред\s*)?премиер[аи]\b
    | (?:\s*[|/\-–—:,]\s*)?\bпоследно\s+представление\b
    | (?:\s*[|/\-–—:,]\s*)?\bпредставление\s+№?\s*\d+\b
    | (?:\s*[|/\-–—:,]\s*)?\bгостуване\b
    | \s*(?:[-–—]\s*)?\bгостува(?:щ[аио]?)?\b.*$
    | \(\s*отменен[оа]?\s*\)
    | (?<![\w+])\d{1,2}\s*\+(?!\w)
""", re.I | re.X)


def _homoglyphs(token):
    """A Latin 'a' typed inside a Cyrillic word ('Бaлдахинът' on tba.art.bg —
    the catalogue already holds it twice because of that letter) must not split
    one production into two. Pure-Latin tokens are left alone except one- and
    two-letter ones made only of look-alikes ('E.E.' typed either way)."""
    letters = [c for c in token if c.isalpha()]
    if _CYR_RE.search(token) or (0 < len(letters) <= 2 and all(c in _HOMO_FROM for c in letters)):
        return token.translate(_HOMO)
    return token


def normalise_show_title(title):
    """Matching key for a published title against SHOWS[].title / titleEn of the
    SAME theatre. Drops what venues add around a title — "| ПРЕМИЕРА",
    "предпремиера", "гостуване", "- Гостува ДТ-Русе", "последно представление",
    "- представление 200", "(ОТМЕНЕНО)", an age rating "16+" and Сфумато's
    'Програма "Бекет"' cycle prefix — then quotes, case (ё/ѝ too), punctuation and
    Latin look-alike letters. It never touches the words of the title itself, so
    "Отчаяни съпрузи" and "Отчаяни съпрузи 2: Бракувани" stay different, and a
    festival edition ("АСТ ФЕСТ 2026 I История на една страст") stays apart
    from the repertoire title (the catalogue holds both)."""
    return _normalise(title, strip_cycle=True)


def _normalise(title, strip_cycle):
    s = clean(title)
    if strip_cycle:
        s = _CYCLE_PREFIX.sub("", s)
    s = _DECOR.sub(" ", s)
    s = s.lower().replace("ё", "е").replace("ѝ", "и")      # "така ѝ харесва" = "ТАКА И ХАРЕСВА"
    s = re.sub(r"[„“”\"'’‘‛`«»]", "", s)
    s = re.sub(r"[^\w\s]|_", " ", s)
    return " ".join(_homoglyphs(t) for t in s.split())


def title_keys(title):
    """Every key a title is known by: normalised with and without Сфумато's
    cycle prefix — the catalogue writes 'Програма Бекет: Последната лента…'
    where the theatre writes 'Програма „Бекет” Последната лента…'."""
    return {k for k in (_normalise(title, True), _normalise(title, False)) if k}


def show_index(shows):
    """{theatre: {title key: {show ids}}} from SHOWS (title and titleEn)."""
    idx = defaultdict(lambda: defaultdict(set))
    for s in shows:
        for t in (s.get("title"), s.get("titleEn")):
            for key in title_keys(t) if t else ():
                idx[s.get("theatre")][key].add(s["id"])
    return idx


def match_show(venue, title, index):
    """Sorted ids of `venue`'s SHOWS whose title or titleEn shares a key with
    `title` (empty → a new production; more than one → ambiguous)."""
    keys = index.get(venue, {})
    return sorted(set().union(*(keys.get(k, set()) for k in title_keys(title))))


# ------------------------------------------------------------ result builder
def spread_through(days, year, month):
    """True when a month's performances run through all of it: some in each of
    its first four weeks (days 1-7, 8-14, 15-21, 22-28) and one in its last
    seven days. A half-entered month stops part-way (Възраждане's December
    2026: eight evenings to the 19th); a quieter but complete one does not."""
    have = {int(d[8:10]) for d in days}
    last = calendar.monthrange(year, month)[1]
    return (bool(have) and all(any(7 * w + 1 <= x <= 7 * w + 7 for x in have) for w in range(4))
            and max(have) >= last - 6)


def stage_of(row):
    """The stage a row is played on, as a comparison key: meta["stage"] (set by
    a fetcher that learns it outside its grid — Сълза и смях's per-stage pages)
    or the published hall. None when the source names neither."""
    meta = row[3] if len(row) > 3 and isinstance(row[3], dict) else {}
    return clean(meta.get("stage") or meta.get("hall")).lower() or None


def regular_stages(rows, today):
    """{stage: performances per day over the next REF_DAYS days} for every stage
    that plays in at least STAGE_WEEKS of those four weeks."""
    weeks, count = defaultdict(set), Counter()
    for r in rows:
        k = (dt.date.fromisoformat(r[1]) - today).days
        s = stage_of(r)
        if s and 0 <= k < REF_DAYS:
            weeks[s].add(k // 7)
            count[s] += 1
    return {s: count[s] / REF_DAYS for s, w in weeks.items() if len(w) >= STAGE_WEEKS}


def complete_until(rows, today, why=None):
    """Last date of the programme that looks completely published: the current
    month, then each following month while it is clearly published —
      * its density (rows per day) at least COMPLETE_RATIO of the density of
        the next REF_DAYS days, or at least SPREAD_RATIO of it with
        performances right through the month (spread_through: a quieter month
        such as Топлоцентрала's November 2026, 25 performances to the 30th);
      * and every stage that is regular in those REF_DAYS days (regular_stages)
        keeps at least STAGE_RATIO of its own rate in the month. A theatre that
        has typed in only part of a month leaves a whole stage out: Сълза и
        смях's chamber stage had 1 November date against 9 in the rest of
        October, though the month's 22 evenings ran every week to the 30th.
    Stops at the first month that is empty or thinner — the theatre has not
    finished entering it, so it cannot speak for the days it leaves blank. The
    reason for stopping at a listed month is appended to `why` (a list)."""
    dates = sorted(r[1] for r in rows)
    if not dates:
        return None
    t0 = today.isoformat()
    t1 = (today + dt.timedelta(days=REF_DAYS - 1)).isoformat()
    ref = sum(1 for d in dates if t0 <= d <= t1) / REF_DAYS
    regular = regular_stages(rows, today)
    by_month, staged = defaultdict(list), defaultdict(Counter)
    for r in rows:
        ym = (int(r[1][:4]), int(r[1][5:7]))
        by_month[ym].append(r[1])
        s = stage_of(r)
        if s:
            staged[ym][s] += 1
    y, m = today.year, today.month
    last = max(by_month[(y, m)]) if (y, m) in by_month else None
    while True:
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        days = by_month.get((y, m))
        if not days:
            break
        n_days = calendar.monthrange(y, m)[1]
        density = len(days) / n_days
        if ref <= 0 or (density < COMPLETE_RATIO * ref
                        and not (density >= SPREAD_RATIO * ref and spread_through(days, y, m))):
            if why is not None:
                why.append(f"{y}-{m:02d}: {len(days)} performances — thinner than the next "
                           f"four weeks ({ref * n_days:.0f} at their rate); not completely published")
            break
        gone = sorted(s for s, rate in regular.items() if staged[(y, m)][s] < STAGE_RATIO * rate * n_days)
        if gone:
            if why is not None:
                why.append(f"{y}-{m:02d}: not completely published — " + "; ".join(
                    f"{s}: {staged[(y, m)][s]} performance(s) against {regular[s] * REF_DAYS:.0f} "
                    f"in the next four weeks" for s in gone))
            break
        last = max(days)
    return last


def finish(venue, source, rows, today, *, excluded=(), notes=(), covered_from=None,
           covered_to=None, month_check=True):
    """Assemble the contract dict, or raise Unavailable when nothing usable is
    left. Rows are de-duplicated, past rows dropped, and split into `rows`
    (inside the covered range) and `extra_rows` (outside it)."""
    t0 = today.isoformat()
    uniq = OrderedDict()
    for r in rows:
        if r[1] >= t0:
            uniq.setdefault((r[0], r[1], r[2]), r)
    rows = sorted(uniq.values(), key=lambda r: (r[1], r[2], r[0]))
    if not rows:
        raise Unavailable("no performances parsed (implausibly empty)")
    cf = max(covered_from or t0, t0)
    ct = covered_to or rows[-1][1]
    notes = list(notes)
    if month_check:
        why = []
        cu = complete_until(rows, today, why)
        if cu is None:
            raise Unavailable("no completely published period")
        if cu < ct:
            notes += why
        ct = min(ct, cu)
    inside = [r for r in rows if cf <= r[1] <= ct]
    extra = [r for r in rows if not (cf <= r[1] <= ct)]
    if not inside:
        raise Unavailable("no performances inside the covered range")
    exc = sorted({(clean(t), d, tm, why) for t, d, tm, why in excluded if d >= t0},
                 key=lambda e: (e[1], e[2] or "", e[0]))
    return {"venue": venue, "rows": inside, "covered_from": cf, "covered_to": ct,
            "source": source, "extra_rows": extra, "excluded": exc,
            "notes": notes}


def _get(net, url, **kw):
    r = net.get(url, **kw)
    if r is None:
        raise Unavailable(f"unreachable: {url}")
    return decode(r.content)


# ===================================================================== national
NATIONAL_URL = "https://www.nationaltheatre.bg/bg/programa?day=&month={m}&year={y}"
NATIONAL_SOURCE = "nationaltheatre.bg/bg/programa (month pages)"
_NAT_SLUG = re.compile(r"/(\d{4})-(\d{2})-(\d{2})\|(\d{2}):(\d{2})")


def parse_national(html, year, month):
    """One month of nationaltheatre.bg/bg/programa → (rows, excluded, blocks).

    Each `div.show` is one performance: title in `h2 a`, stage in `.stage`, the
    date+time both in the ticket link (…/YYYY-MM-DD|HH:MM:SS) and as text
    (`.mobile-date`: "08 октомври, четвъртък 19:00 ч."); the two must agree. A
    show with no `.stage` is staged off-site — ВАКХАНКИ on 9 Oct 2026 plays НДК
    Зала 1 — so it is excluded rather than filed at ул. Дякон Игнатий 5."""
    soup = soup_of(html)
    blocks = soup.select("div.show")
    rows, excluded = [], []
    for b in blocks:
        a = b.select_one("h2 a") or b.select_one("h2")
        title = clean(a.get_text(" ", strip=True)) if a else ""
        md_el = b.select_one(".mobile-date")
        md = clean(md_el.get_text(" ", strip=True)) if md_el else ""
        dm = _DAY_MONTH_RE.search(md)
        if not title or not dm or not bg_month(dm.group(2)):
            raise MarkupError(f"national: unreadable show block {title!r} / {md!r}")
        day, mon = int(dm.group(1)), bg_month(dm.group(2))
        hour_el = b.select_one(".hour")
        time_txt = hhmm(hour_el.get_text(" ", strip=True) if hour_el else "") or hhmm(md)
        slug, link = None, None
        for el in b.select("a[href]"):
            m = _NAT_SLUG.search(urllib.parse.unquote(el["href"]))
            if m:
                slug, link = m, el["href"]
                break
        if slug:
            date = dt.date(int(slug.group(1)), int(slug.group(2)), int(slug.group(3)))
            time_ = f"{slug.group(4)}:{slug.group(5)}"
            if (date.day, date.month) != (day, mon) or (time_txt and time_txt != time_):
                raise MarkupError(f"national: ticket link and text disagree for {title!r}")
        else:
            y = year + 1 if (month == 12 and mon == 1) else year
            date, time_ = dt.date(y, mon, day), time_txt
            show_a = b.select_one("h2 a[href]")
            link = show_a["href"] if show_a else None
        if not time_ or weekday_conflict(date, md):
            raise MarkupError(f"national: bad time/weekday for {title!r} {md!r}")
        stage_el = b.select_one(".stage")
        stage = clean(stage_el.get_text(" ", strip=True)) if stage_el else ""
        guest_el = b.select_one(".guest-label")
        guest = clean(guest_el.get_text(" ", strip=True)) if guest_el else None
        if not stage:
            excluded.append((title, date.isoformat(), time_,
                             "no stage on the programme — staged off-site (venue not given)"))
            continue
        rows.append(mkrow(title, date, time_, hall=stage,
                          url=absolute("https://www.nationaltheatre.bg/", link),
                          guest=guest))
    return rows, excluded, len(blocks)


def fetch_national(net, today):
    rows, excluded, notes = [], [], []
    for i, (y, m) in enumerate(months_from(today)):
        try:
            html = _get(net, NATIONAL_URL.format(y=y, m=m))
        except Unavailable:
            if i == 0:
                raise
            notes.append(f"{y}-{m:02d}: page did not load — coverage ends before it")
            break
        page, exc, blocks = parse_national(html, y, m)
        if not page and not exc:
            if blocks:
                raise MarkupError("national: show blocks but nothing parsed")
            break                                   # month not published yet
        if any(r[1][:7] != f"{y}-{m:02d}" for r in page):
            notes.append(f"{y}-{m:02d}: page shows another month — stopped")
            break
        rows += page
        excluded += exc
    return finish("national", NATIONAL_SOURCE, rows, today, excluded=excluded, notes=notes)


# ===================================================================== sofia-th
SOFIA_TH_URL = "https://sofiatheatre.eu/program?month={m}&year={y}"
SOFIA_TH_SOURCE = "sofiatheatre.eu/program (month pages)"
_STH_LABEL_DATE = re.compile(r"-\s*(\d{2})\.(\d{2})\.(\d{4})\s*$")


def parse_sofia_th(html):
    """One page of sofiatheatre.eu/program → (listing, mobile, next_url).

    The page carries its month twice. A desktop table, identical on every page
    of the month: date in the first link's aria-label ("Преглед на репертоар:
    TITLE - DD.MM.YYYY"), stage and time in their own columns — except sold-out
    rows, which drop both. And a paginated mobile list whose cards always print
    "DD Месец, weekday <br> STAGE HH:MM" with a label (Представление /
    Премиера / Изчерпани билети). The table is the master list; the cards fill
    in a sold-out row's stage and time."""
    soup = soup_of(html)
    listing, mobile = [], []
    for card in soup.select("div.box-shadow-default"):
        title_el = card.select_one(".font-24")
        title = clean(title_el.get_text(" ", strip=True)) if title_el else ""
        dates = card.select_one(".program-dates-wrapper a[aria-label]")
        if dates is not None:
            m = _STH_LABEL_DATE.search(dates.get("aria-label") or "")
            if not m or not title:
                raise MarkupError("sofia-th: unreadable programme row")
            date = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            cols = [clean(c.get_text(" ", strip=True)) for c in card.select("div.row > div")]
            hall = time_ = None
            for j, c in enumerate(cols):
                if re.fullmatch(r"\d{1,2}:\d{2}", c):
                    time_ = hhmm(c)
                    hall = cols[j - 1] if j > 0 else None
                    break
            buy = card.select_one("a.buy-button[href]")
            rep = dates.get("href")
            listing.append({"title": title, "date": date, "time": time_, "hall": hall,
                            "sold_out": card.select_one(".no-tickets") is not None,
                            "url": buy["href"] if buy else rep})
        elif title_el is not None:
            info = card.select_one("div.mt-4")
            if info is None:
                continue
            parts = [clean(p) for p in info.get_text("|", strip=True).split("|") if clean(p)]
            # dark-red- "Представление", yellow- "Премиера", gray- "Изчерпани билети"
            label_el = card.select_one("[class*='-label-program']")
            dm = _DAY_MONTH_RE.search(parts[0]) if parts else None
            rest = " ".join(parts[1:])
            mobile.append({"title": title,
                           "day": int(dm.group(1)) if dm else None,
                           "month": bg_month(dm.group(2)) if dm else None,
                           "daytext": parts[0] if parts else "",
                           "time": hhmm(rest),
                           "hall": clean(_TIME_RE.sub("", rest)) or None,
                           "label": clean(label_el.get_text(" ", strip=True)) if label_el else ""})
    nxt = None
    for a in soup.select("a[href*='page=']"):
        if clean(a.get_text(" ", strip=True)).lower() in ("напред", "»", "›", "next"):
            nxt = a["href"]
            break
    return listing, mobile, nxt


def fetch_sofia_th(net, today):
    rows, excluded, notes = [], [], []
    for i, (y, m) in enumerate(months_from(today)):
        url = SOFIA_TH_URL.format(y=y, m=m)
        try:
            listing, mobile, nxt = parse_sofia_th(_get(net, url))
            pages, seen = 1, {url}
            while nxt and pages < 10:
                nxt = absolute(url, nxt)
                if nxt in seen:
                    break
                seen.add(nxt)
                _, more, nxt = parse_sofia_th(_get(net, nxt))
                mobile += more
                pages += 1
        except MarkupError:
            raise
        except Unavailable:
            if i == 0:
                raise
            notes.append(f"{y}-{m:02d}: page did not load — coverage ends before it")
            break
        if not listing:
            if mobile:
                raise MarkupError("sofia-th: mobile cards but no programme table")
            break
        if any((e["date"].year, e["date"].month) != (y, m) for e in listing):
            notes.append(f"{y}-{m:02d}: page shows another month — stopped")
            break
        if len(mobile) != len(listing):
            notes.append(f"{y}-{m:02d}: {len(listing)} table rows vs {len(mobile)} cards")
        for e in listing:
            cards = [c for c in mobile if c["title"].lower() == e["title"].lower()
                     and (c["day"], c["month"]) == (e["date"].day, e["date"].month)]
            card = cards[0] if cards else None
            hall, time_ = e["hall"], e["time"]
            if not time_ and card:
                hall, time_ = card["hall"], card["time"]
            if not time_:
                raise MarkupError(f"sofia-th: no time for {e['title']} {e['date']}")
            if card and weekday_conflict(e["date"], card["daytext"]):
                raise MarkupError(f"sofia-th: weekday mismatch for {e['title']}")
            label = card["label"].lower() if card else ""
            hall = clean(hall)
            if not re.match(r"^театър софия\b", hall, re.I):
                excluded.append((e["title"], e["date"].isoformat(), time_,
                                 f"staged at {hall or 'an unnamed venue'} "
                                 "(co-production listed by Театър София)"))
                continue
            rows.append(mkrow(e["title"], e["date"], time_,
                              hall=re.sub(r"^театър софия\s*[-–—]\s*", "", hall, flags=re.I),
                              url=e["url"],
                              premiere=True if _PREMIERE_RE.search(label) else None,
                              sold_out=e["sold_out"] or "изчерпани" in label))
    return finish("sofia-th", SOFIA_TH_SOURCE, rows, today, excluded=excluded, notes=notes)


# ======================================================================= th199
TH199_URL = "https://theatre199.org/bg/schedule"
TH199_SOURCE = "theatre199.org/bg/schedule (GET window)"


def parse_th199(html, today):
    """theatre199.org/bg/schedule (plain GET) → (listing, featured, more_pages).

    `.js-card`: `.date` "09.10", `.list-time` "19:30", title `article h4 a`.
    Today's performance is not in the list but in a featured card ("Днес,
    четвъртък, 8 октомври" + `.list-time`). The list is Livewire-paginated
    (15 cards); further pages are POST-only and deliberately not automated."""
    soup = soup_of(html)
    listing = []
    for c in soup.select(".js-card"):
        d_el, t_el = c.select_one("span.date"), c.select_one(".list-time")
        a = c.select_one("article h4 a") or c.select_one("h4 a")
        dm = re.fullmatch(r"\s*(\d{1,2})\.(\d{1,2})\s*", d_el.get_text() if d_el else "")
        time_ = hhmm(t_el.get_text(" ", strip=True) if t_el else "")
        if not (dm and time_ and a):
            raise MarkupError("th199: unreadable schedule card")
        date = nearest_date(int(dm.group(1)), int(dm.group(2)), today)
        age = c.select_one(".for-age")
        listing.append(mkrow(a.get_text(" ", strip=True), date, time_, url=a.get("href"),
                             kind=clean(age.get_text(" ", strip=True)) if age else None))
    featured = None
    acc = soup.select_one(".accent-wrapper")
    if acc is not None:
        li = acc.select_one("ul.calendar-list li")
        a = acc.select_one("h2 a") or acc.select_one("h4 a")
        dm = _DAY_MONTH_RE.search(li.get_text(" ", strip=True)) if li else None
        tt = li.select_one(".list-time") if li else None
        if dm and a and tt and bg_month(dm.group(2)):
            date = nearest_date(int(dm.group(1)), bg_month(dm.group(2)), today)
            if date and not weekday_conflict(date, li.get_text(" ", strip=True)):
                featured = mkrow(a.get_text(" ", strip=True), date,
                                 hhmm(tt.get_text(" ", strip=True)), url=a.get("href"))
    more = False
    for el in soup.find_all(attrs={"wire:click": True}):
        m = re.match(r"gotoPage\((\d+)\)", el.get("wire:click") or "")
        if m and int(m.group(1)) > 1:
            more = True
    return listing, featured, more


def fetch_th199(net, today):
    listing, featured, more = parse_th199(_get(net, TH199_URL), today)
    if not listing:
        raise Unavailable("th199: empty schedule")
    first, last = min(r[1] for r in listing), max(r[1] for r in listing)
    notes = []
    if more:
        # a full page may cut its last day short — that day is not covered
        last = (dt.date.fromisoformat(last) - dt.timedelta(days=1)).isoformat()
        notes.append("schedule continues on Livewire (POST-only) pages; its last listed "
                     "day may be partial, so coverage stops the day before")
    if featured and featured[1] < first:
        notes.append(f"today's featured performance ({featured[1]}) is outside the list — "
                     "kept as an extra row, the day itself is not covered")
    rows = listing + ([featured] if featured else [])
    res = finish("th199", TH199_SOURCE, rows, today, covered_from=first, covered_to=last,
                 month_check=False, notes=notes)
    if more:
        # the cut day is listed, maybe only in part: scrape_programs confirms it
        # when the aggregator's genuine page for that day lists nothing more
        res["confirm_by_aggregator"] = [_next_day(last)]
    return res


def _next_day(iso):
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=1)).isoformat()


# ================================================================== zad-kanala
ZK_URL = "https://zadkanala.bg/programa/{y}-{m:02d}"
ZK_SOURCE = "zadkanala.bg/programa/YYYY-MM"
_ZK_TEXT = re.compile(r"(\d{1,2})\s+([А-Яа-я]{3,})\.?\s+(\d{4})\s*-\s*(\d{1,2}:\d{2})")


def parse_zadkanala(html, tz=None):
    """One month of zadkanala.bg/programa/YYYY-MM (Drupal views table): the
    `content` attribute of `.date-display-single` is an ISO datetime with its
    UTC offset ("2026-10-25T19:00:00+02:00"); the visible text ("25 Окт. 2026 -
    19:00") must say the same."""
    soup = soup_of(html)
    rows = []
    for span in soup.select("span.date-display-single[content]"):
        try:
            when = dt.datetime.fromisoformat(span["content"])
        except ValueError:
            raise MarkupError(f"zad-kanala: bad datetime {span['content']!r}")
        if when.tzinfo is not None and tz is not None:
            when = when.astimezone(tz)
        txt = _ZK_TEXT.search(clean(span.get_text(" ", strip=True)))
        if (not txt or int(txt.group(1)) != when.day or bg_month(txt.group(2)) != when.month
                or int(txt.group(3)) != when.year or hhmm(txt.group(4)) != when.strftime("%H:%M")):
            raise MarkupError(f"zad-kanala: date text disagrees with {span['content']}")
        tr = span.find_parent("tr")
        a = tr.select_one(".views-field-title a") if tr else None
        if a is None:
            raise MarkupError("zad-kanala: row without a title")
        rows.append(mkrow(a.get_text(" ", strip=True), when.date(), when.strftime("%H:%M"),
                          url=absolute("https://zadkanala.bg/", a.get("href"))))
    return rows


def fetch_zadkanala(net, today):
    rows, notes, tz = [], [], sofia_tz()
    for i, (y, m) in enumerate(months_from(today)):
        try:
            page = parse_zadkanala(_get(net, ZK_URL.format(y=y, m=m)), tz)
        except MarkupError:
            raise
        except Unavailable:
            if i == 0:
                raise
            notes.append(f"{y}-{m:02d}: page did not load — coverage ends before it")
            break
        if not page:
            break
        if any(r[1][:7] != f"{y}-{m:02d}" for r in page):
            notes.append(f"{y}-{m:02d}: page shows another month — stopped")
            break
        rows += page
    return finish("zad-kanala", ZK_SOURCE, rows, today, notes=notes)


# ================================================================= vazrazhdane
VAZ_URL = "https://theatrevazrajdane.bg/programa/"
VAZ_SOURCE = "theatrevazrajdane.bg/programa/"


def parse_vazrazhdane(html, today):
    """theatrevazrajdane.bg/programa/ — Entase widgets, one `.event_item` per
    performance: title, date ("08 Окт" + weekday), time ("19:00 ч."), city and
    place. Categories ride on the class (category-premiera,
    category-gostuvashto…). Anything not in Sofia at Театър Възраждане is
    excluded."""
    soup = soup_of(html)
    rows, excluded = [], []
    for it in soup.select(".event_item"):
        def g(sel):
            el = it.select_one(sel)
            return clean(el.get_text(" ", strip=True)) if el else ""
        title, dtext, ttext = g(".event_entase_title"), g(".event_entase_dateonly"), g(".event_entase_timeonly")
        city, place = g(".event_entase_location_cityName"), g(".event_entase_location_placeName")
        dm = _DAY_MONTH_RE.search(dtext)
        time_ = hhmm(ttext)
        if not (title and dm and bg_month(dm.group(2)) and time_):
            raise MarkupError(f"vazrazhdane: unreadable event {title!r} {dtext!r}")
        date = nearest_date(int(dm.group(1)), bg_month(dm.group(2)), today)
        if date is None or weekday_conflict(date, dtext):
            raise MarkupError(f"vazrazhdane: weekday mismatch {title!r} {dtext!r}")
        cats = {urllib.parse.unquote(c[len("category-"):])
                for c in it.get("class", []) if c.startswith("category-")}
        a = it.select_one("a[href]")
        book = g(".event_entase_book").lower()
        if city.lower() not in ("sofia", "софия") or "възраждане" not in place.lower():
            excluded.append((title, date.isoformat(), time_, f"at {place or '?'}, {city or '?'}"))
            continue
        rows.append(mkrow(title, date, time_, url=a["href"] if a else None,
                          premiere=True if "premiera" in cats else None,
                          guest="гостуващ спектакъл" if "gostuvashto" in cats else None,
                          kind="за деца" if "za-deca" in cats else None,
                          sold_out="разпродадено" in book))
    return rows, excluded


def fetch_vazrazhdane(net, today):
    rows, excluded = parse_vazrazhdane(_get(net, VAZ_URL), today)
    return finish("vazrazhdane", VAZ_SOURCE, rows, today, excluded=excluded)


# =================================================================== mladezhki
MLT_URL = "https://mlt.bg/programa.php?year={y}&month={m:02d}"
MLT_SOURCE = "mlt.bg/programa.php (month pages)"


def _check_heading(soup, year, month, venue):
    h = soup.select_one("h1")
    text = clean(h.get_text(" ", strip=True)) if h else clean(soup.title.get_text() if soup.title else "")
    m = re.search(r"([А-Яа-я]{3,})\s+(\d{4})", text)
    if not m or bg_month(m.group(1)) != month or int(m.group(2)) != year:
        raise MarkupError(f"{venue}: page heading {text[:60]!r} is not {year}-{month:02d}")


def parse_mlt(html, year, month):
    """mlt.bg/programa.php?year=Y&month=MM — the theatre's own month programme:
    `tr[data-href]` rows of DD/MM | HH:MM | title | hall.

    Not the calendar's popup-calendar.php: that endpoint takes a 0-based month
    (month=10 answers for NOVEMBER), which is how the 2026-10-08 audit came to
    compare the app's October against the theatre's November."""
    soup = soup_of(html)
    _check_heading(soup, year, month, "mladezhki")
    rows = []
    for tr in soup.select("tr[data-href]"):
        tds = tr.find_all("td")
        dm = re.fullmatch(r"(\d{1,2})/(\d{1,2})", clean(tds[0].get_text()) if tds else "")
        time_ = hhmm(tds[1].get_text(" ", strip=True)) if len(tds) > 1 else None
        if len(tds) < 4 or not dm or not time_ or int(dm.group(2)) != month:
            raise MarkupError("mladezhki: unreadable programme row")
        rows.append(mkrow(tds[2].get_text(" ", strip=True), dt.date(year, month, int(dm.group(1))),
                          time_, hall=tds[3].get_text(" ", strip=True),
                          url=absolute("https://mlt.bg/", tr.get("data-href"))))
    return rows


def fetch_mladezhki(net, today):
    rows, notes = [], []
    for i, (y, m) in enumerate(months_from(today)):
        try:
            page = parse_mlt(_get(net, MLT_URL.format(y=y, m=m)), y, m)
        except MarkupError:
            if i == 0:
                raise
            notes.append(f"{y}-{m:02d}: not this month's page — stopped")
            break
        except Unavailable:
            if i == 0:
                raise
            notes.append(f"{y}-{m:02d}: page did not load — coverage ends before it")
            break
        if not page:
            break
        rows += page
    return finish("mladezhki", MLT_SOURCE, rows, today, notes=notes)


# ====================================================================== kuklen
KUKLEN_URL = "https://sofiapuppet.com/programa.php?year={y}&month={m:02d}"
KUKLEN_SOURCE = "sofiapuppet.com/programa.php (month pages)"


def parse_kuklen(html, year, month):
    """sofiapuppet.com/programa.php?year=Y&month=MM — one column per hall
    (`.hall-prog-title`: Салон “Ген. Гурко 14”, Салон “Я. Сакъзов 19”), items
    "01 Октомври, четвъртък, от 19:00 часа" + title. The column "Представления
    на други сцени" (performances on other stages, place not given) is not at
    this theatre, so it is excluded. Same 0-based popup trap as mlt.bg."""
    soup = soup_of(html)
    _check_heading(soup, year, month, "kuklen")
    rows, excluded = [], []
    for col in soup.select(".hall-prog-row > div"):
        h = col.select_one(".hall-prog-title")
        hall = clean(h.get_text(" ", strip=True)) if h else ""
        for it in col.select(".play-prog-item"):
            d_el, a = it.find("div"), it.select_one("a.play-prog-name")
            dtext = clean(d_el.get_text(" ", strip=True)) if d_el else ""
            dm = _DAY_MONTH_RE.search(dtext)
            time_ = hhmm(dtext)
            if not (a and dm and time_) or bg_month(dm.group(2)) != month:
                raise MarkupError(f"kuklen: unreadable item {dtext!r}")
            rec = a.select_one(".recommended-for")
            kind = clean(rec.get_text(" ", strip=True)).lstrip("-– ").strip() if rec else None
            if rec:
                rec.extract()
            date = dt.date(year, month, int(dm.group(1)))
            if weekday_conflict(date, dtext):
                raise MarkupError(f"kuklen: weekday mismatch {dtext!r}")
            title = a.get_text(" ", strip=True)
            if not hall.lower().startswith("салон"):
                excluded.append((title, date.isoformat(), time_,
                                 f"listed under '{hall}' — not one of the theatre's halls"))
                continue
            rows.append(mkrow(title, date, time_, hall=hall, kind=kind,
                              url=absolute("https://sofiapuppet.com/", a.get("href"))))
    return rows, excluded


def fetch_kuklen(net, today):
    rows, excluded, notes = [], [], []
    for i, (y, m) in enumerate(months_from(today)):
        try:
            page, exc = parse_kuklen(_get(net, KUKLEN_URL.format(y=y, m=m)), y, m)
        except MarkupError:
            if i == 0:
                raise
            notes.append(f"{y}-{m:02d}: not this month's page — stopped")
            break
        except Unavailable:
            if i == 0:
                raise
            notes.append(f"{y}-{m:02d}: page did not load — coverage ends before it")
            break
        if not page and not exc:
            break
        rows += page
        excluded += exc
    return finish("kuklen", KUKLEN_SOURCE, rows, today, excluded=excluded, notes=notes)


# ====================================================================== satira
SATIRA_URL = "https://satirata.bg/program"
SATIRA_MONTH_URL = "https://satirata.bg/program?f%3Amonth={y}-{m:02d}"
SATIRA_SOURCE = "satirata.bg/program (__NEXT_DATA__)"
# verified 2026-10-08 against the rendered table; re-derived from the page each run
SATIRA_STAGES = {"stage-main": "Голяма сцена",
                 "stage-chamber": "Камерна зала „Методи Андонов“",
                 "stage-bar": "сцена „COMEDY BAR Happy Сатира“"}
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def parse_satira(html, tz):
    """satirata.bg/program — Next.js: props.pageProps.prefetchedData.eventsData
    .events.list. `startsAt` is UTC ("2026-10-26T17:30:00Z") and becomes Sofia
    wall time through zoneinfo (EEST +03:00 until 25 Oct 2026 03:00, EET +02:00
    after) — never a hard-coded offset. Stage names come from the rendered
    table, which lists the same events in the same order."""
    if tz is None:
        raise Unavailable("satira: no Europe/Sofia tz database — refusing to guess offsets")
    m = _NEXT_DATA.search(html)
    if not m:
        raise MarkupError("satira: no __NEXT_DATA__")
    try:
        ev = json.loads(m.group(1))["props"]["pageProps"]["prefetchedData"]["eventsData"]["events"]
        lst = ev["list"]
    except (KeyError, TypeError, ValueError):
        raise MarkupError("satira: __NEXT_DATA__ path moved")
    if int((ev.get("pagination") or {}).get("pagesCount") or 1) > 1:
        raise MarkupError("satira: programme is paginated — page 2 is not read")
    names = dict(SATIRA_STAGES)
    trs = soup_of(html).select("tr.event")
    if len(trs) == len(lst):
        for tr, e in zip(trs, lst):
            h3, tds = tr.select_one("h3"), tr.find_all("td", recursive=False)
            if (h3 and len(tds) > 3 and normalise_show_title(h3.get_text())
                    == normalise_show_title(e.get("title"))):
                key = (e.get("settings") or {}).get("stage")
                if key and clean(tds[3].get_text(" ", strip=True)):
                    names[key] = clean(tds[3].get_text(" ", strip=True))
    rows = []
    for e in lst:
        if e.get("visibility", "public") != "public":
            continue
        try:
            start = dt.datetime.fromisoformat(e["startsAt"].replace("Z", "+00:00"))
        except (KeyError, ValueError, AttributeError):
            raise MarkupError("satira: event without startsAt")
        if start.tzinfo is None:
            raise MarkupError("satira: startsAt without a UTC offset")
        local = start.astimezone(tz)
        st = e.get("settings") or {}
        url = st.get("ticketUrlNoRegistration") or (
            "https://satirata.bg/" + st["eventPage"] if st.get("eventPage") else None)
        rows.append(mkrow(e.get("title"), local.date(), local.strftime("%H:%M"),
                          hall=names.get(st.get("stage")),
                          price=(st.get("custom") or {}).get("price"), url=url))
    return rows


def fetch_satira(net, today):
    rows, notes, tz = [], [], sofia_tz()
    for i, (y, m) in enumerate(months_from(today)):
        url = SATIRA_URL if i == 0 else SATIRA_MONTH_URL.format(y=y, m=m)
        try:
            page = parse_satira(_get(net, url), tz)
        except MarkupError:
            raise
        except Unavailable:
            if i == 0:
                raise
            notes.append(f"{y}-{m:02d}: page did not load — coverage ends before it")
            break
        if not page:
            break
        if i and any(r[1][:7] != f"{y}-{m:02d}" for r in page):
            notes.append(f"{y}-{m:02d}: page shows another month — stopped")
            break
        rows += page
    return finish("satira", SATIRA_SOURCE, rows, today, notes=notes)


# ================================================================ salzaismyah
SALZA_URL = "https://www.salzaismyah.bg/site/calendar/{y}/{m:02d}"
SALZA_STAGE_URL = "https://www.salzaismyah.bg/site/events/{n}"
SALZA_STAGE_PAGES = (1, 2, 3)     # Открита сцена, Камерна сцена (Славянска беседа), Ъндърграунд
SALZA_SOURCE = "salzaismyah.bg/site/calendar (month grid) + /site/events/N (stages)"
_SALZA_ENTRY = re.compile(r"^(\d{1,2}[:.]\d{2})\s*[-–—]\s*(.+)$")
_SALZA_SELECTOR = re.compile(r"/site/selector/(\d+)")
_SALZA_STAGE_DAY = re.compile(r"^\d{2}\.\d{2}\s+\S+\s+\d{1,2}:\d{2}$")


def parse_salza(html, year, month):
    """salzaismyah.bg/site/calendar/YYYY/MM — a month grid: `div.cday` cells
    (`div.day`, or `div.highlight` for today; `.other-month` cells belong to the
    neighbouring months), entries "HH:MM - TITLE" as a link (bookable), a span
    (past) or `span.text-danger` "… (ОТМЕНЕНО)" (cancelled → excluded)."""
    soup = soup_of(html)
    head = soup.select_one(".calendar .h3")
    hm = re.search(r"([А-Яа-я]{3,})\D+(\d{4})", clean(head.get_text(" ", strip=True)) if head else "")
    if not hm or bg_month(hm.group(1)) != month or int(hm.group(2)) != year:
        raise MarkupError(f"salzaismyah: grid is not {year}-{month:02d}")
    rows, excluded = [], []
    for cell in soup.select("div.cday"):
        classes = cell.get("class") or []
        if "other-month" in classes:
            continue
        # a day without performances is a bare <div class="day … cday">2</div>
        dn = cell if ("day" in classes or "highlight" in classes) else \
            cell.select_one("div.day, div.highlight")
        if dn is None or not clean(dn.get_text()).isdigit():
            raise MarkupError("salzaismyah: day cell without a day number")
        date = dt.date(year, month, int(clean(dn.get_text())))
        for cr in cell.select("div.content-row"):
            el = cr.find(["a", "span"])
            text = clean(el.get_text(" ", strip=True)) if el else ""
            m = _SALZA_ENTRY.match(text)
            if not m:
                raise MarkupError(f"salzaismyah: unreadable entry {text!r}")
            time_ = hhmm(m.group(1))
            title = clean(re.sub(r"\(\s*отменено\s*\)", "", m.group(2), flags=re.I))
            if "text-danger" in (el.get("class") or []) or re.search(r"отменено", text, re.I):
                excluded.append((title, date.isoformat(), time_, "cancelled (ОТМЕНЕНО)"))
                continue
            rows.append(mkrow(title, date, time_, url=el.get("href")))
    return rows, excluded


def fetch_salzaismyah(net, today):
    rows, excluded, notes = [], [], []
    for i, (y, m) in enumerate(months_from(today)):
        try:
            page, exc = parse_salza(_get(net, SALZA_URL.format(y=y, m=m)), y, m)
        except MarkupError:
            if i == 0:
                raise
            notes.append(f"{y}-{m:02d}: not this month's grid — stopped")
            break
        except Unavailable:
            if i == 0:
                raise
            notes.append(f"{y}-{m:02d}: page did not load — coverage ends before it")
            break
        if not page and not exc:
            break
        rows += page
        excluded += exc
    # The grid does not say on which stage a performance is; the stage pages do,
    # and the completeness rule needs it (complete_until: a regular stage that
    # nearly vanishes from a month means the month is half-entered). Without a
    # full stage account no later month can be checked, so coverage then ends
    # with the current month.
    covered_to = None
    try:
        stages = [parse_salza_stage(_get(net, SALZA_STAGE_URL.format(n=n))) for n in SALZA_STAGE_PAGES]
    except Unavailable as e:                           # MarkupError included
        why = f"stage pages not read ({e})"
    else:
        unplaced, unlisted = salza_stage_rows(rows, stages, today, excluded)
        why = (f"{len(unplaced)} later performance(s) on no stage page, first "
               f"{unplaced[0][1]} {unplaced[0][2]} {unplaced[0][0]}") if unplaced else None
        if unlisted:
            notes.append(f"{len(unlisted)} stage-page performance(s) not on the month grid, first "
                         + " ".join(unlisted[0][:3]))
    if why:
        covered_to = today.replace(day=calendar.monthrange(today.year, today.month)[1]).isoformat()
        notes.append(why + " — later months cannot be checked stage by stage; coverage ends with this month")
    return finish("salzaismyah", SALZA_SOURCE, rows, today, excluded=excluded, notes=notes,
                  covered_to=covered_to)


def parse_salza_stage(html):
    """salzaismyah.bg/site/events/N — one stage's own programme, every upcoming
    performance on one page ("Няма намерени представления." when none): the
    stage in h2.page-heading-accent ("Камерна сцена СЛАВЯНСКА БЕСЕДА") and per
    performance a card headed "09.10 петък 19:30" followed by a schema.org Event
    (startDate in Sofia wall time; offers.url = the /site/selector/NNNN link the
    month grid uses, present even when tickets are sold only at the box office).
    Returns (stage, [(date, time, selector id or None, title)])."""
    soup = soup_of(html)
    h = soup.select_one("h2.page-heading-accent")
    stage = clean(h.get_text(" ", strip=True)) if h else ""
    if not stage:
        raise MarkupError("salzaismyah: stage page without its stage heading")
    events = []
    for sc in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(sc.string or sc.get_text() or "")
        except ValueError:
            raise MarkupError(f"salzaismyah: unreadable schema.org block on {stage}")
        for ev in data if isinstance(data, list) else [data]:
            if not isinstance(ev, dict) or ev.get("@type") != "Event":
                continue
            m = re.match(r"(\d{4}-\d\d-\d\d)T(\d\d:\d\d)", ev.get("startDate") or "")
            if not m:
                raise MarkupError(f"salzaismyah: event without startDate on {stage}")
            sel = _SALZA_SELECTOR.search(str((ev.get("offers") or {}).get("url") or ""))
            events.append((m.group(1), m.group(2), sel.group(1) if sel else None, clean(ev.get("name"))))
    cards = [d for d in soup.select("div.w-100.text-center") if _SALZA_STAGE_DAY.match(clean(d.get_text(" ")))]
    if len(cards) != len(events):
        raise MarkupError(f"salzaismyah: {stage} shows {len(cards)} performances, "
                          f"its schema.org data {len(events)}")
    return stage, events


def salza_stage_rows(rows, stages, today, excluded=()):
    """Put each month-grid row on its stage (meta["stage"]): by its
    /site/selector/ id, else by date and time when only one stage plays then.
    stages: [(stage, events)] from parse_salza_stage; excluded: the grid's
    cancelled entries (the stage pages still list them). Returns (grid rows
    dated after today that no stage page lists, stage-page performances after
    today that the grid does not list)."""
    by_id, by_slot = {}, defaultdict(set)
    for stage, events in stages:
        for d, t, sel, _title in events:
            if sel:
                by_id[sel] = stage
            by_slot[(d, t)].add(stage)
    t0 = today.isoformat()
    unplaced, slots = [], {(e[1], e[2]) for e in excluded}
    for r in rows:
        slots.add((r[1], r[2]))
        sel = _SALZA_SELECTOR.search(r[3].get("url") or "")
        stage = by_id.get(sel.group(1)) if sel else None
        if stage is None and len(by_slot.get((r[1], r[2]), ())) == 1:
            stage = next(iter(by_slot[(r[1], r[2])]))
        if stage:
            r[3]["stage"] = stage
        elif r[1] > t0:
            unplaced.append(r)
    unlisted = [(d, t, title, stage) for stage, events in stages for d, t, _s, title in events
                if d > t0 and (d, t) not in slots]
    return unplaced, unlisted


# ======================================================================= toplo
TOPLO_URL = "https://toplocentrala.bg/program/performance"
TOPLO_MONTH_URL = "https://toplocentrala.bg/program/performance/{y}/{m:02d}"
TOPLO_SOURCE = "toplocentrala.bg/program/performance (+ /YYYY/MM month pages, schema.org)"


def parse_toplo(html):
    """toplocentrala.bg — `li.program-list-item` with schema.org startDate
    ("2026-10-08T19:00", Sofia wall time), title `.program-title > div` (the
    director's credit sits in a sibling span), `.program-type`, `.program-hall`.
    The printed date/time must agree with startDate. Every listed event is kept
    (concerts and screenings too — the venue's own programme lists them), with
    the event type in meta['kind']."""
    soup = soup_of(html)
    rows = []
    for li in soup.select("li.program-list-item"):
        sd = li.select_one("[itemprop=startDate]")
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", sd.get("content", "") if sd else "")
        tn = li.select_one(".program-title > div")
        if not (m and tn):
            raise MarkupError("toplo: item without startDate or title")
        date, time_ = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))), f"{m.group(4)}:{m.group(5)}"
        pd, pt = li.select_one(".program-date"), li.select_one(".program-time")
        ptext = clean(pd.get_text(" ", strip=True)) if pd else ""
        dm = _DAY_MONTH_RE.search(ptext)
        if (not dm or (int(dm.group(1)), bg_month(dm.group(2))) != (date.day, date.month)
                or hhmm(pt.get_text(" ", strip=True) if pt else "") != time_
                or weekday_conflict(date, ptext)):
            raise MarkupError(f"toplo: printed date disagrees with startDate {sd.get('content')}")
        kind, hall = li.select_one(".program-type"), li.select_one(".program-hall")
        a = li.select_one("a[href]")
        rows.append(mkrow(tn.get_text(" ", strip=True), date, time_,
                          hall=hall.get_text(" ", strip=True) if hall else None,
                          url=a["href"] if a else None,
                          kind=clean(kind.get_text(" ", strip=True)) if kind else None))
    return rows


def fetch_toplo(net, today):
    """The programme page shows the current month; later months live at
    /program/performance/YYYY/MM (the page's own "next month" link). Read until
    a month is empty — the partial-month rule in finish() decides how far the
    programme is complete (2026-10-08: November and December were published)."""
    rows, notes = [], []
    for i, (y, m) in enumerate(months_from(today)):
        url = TOPLO_URL if i == 0 else TOPLO_MONTH_URL.format(y=y, m=m)
        try:
            page = parse_toplo(_get(net, url))
        except MarkupError:
            raise
        except Unavailable:
            if i == 0:
                raise
            notes.append(f"{y}-{m:02d}: page did not load — coverage ends before it")
            break
        if not page:
            break
        if i and any(r[1][:7] != f"{y}-{m:02d}" for r in page):
            notes.append(f"{y}-{m:02d}: page shows another month — stopped")
            break
        rows += page
    return finish("toplo", TOPLO_SOURCE, rows, today, notes=notes)


# ========================================================================= iam
IAM_URL = "https://iamstudio.bg/programa/"
IAM_SOURCE = "iamstudio.bg/programa/"
_IAM_DT = re.compile(r"(\d{1,2})\s+([А-Яа-я]{3,})\s*\(?\s*(\d{1,2})[:.](\d{2})\s*\)?")


def parse_iam(html, today):
    """iamstudio.bg/programa/ — one `.portfolio-item` per production. Its
    schedule ("18 октомври (12:00)", or several: "26 октомври (19:30), 27
    октомври (19:30)") is read only from the SAME item as its title, and the
    item's poster link must point at the same product as the title link: the
    old generic scan paired neighbours and swapped Кай (24 Oct) with София
    (18 Oct). A schedule naming another hall (`.iam-venue`, e.g. "Созопол -
    Читалище…") is not at I AM Studio and is excluded."""
    soup = soup_of(html)
    rows, excluded = [], []
    for it in soup.select(".portfolio-item"):
        sched, tlink = it.select_one(".labels-outer-schedule"), it.select_one(".caption .title a")
        if sched is None or tlink is None:
            continue
        plink = it.select_one("a.product-link[href]")
        if plink is not None and plink["href"].rstrip("/") != (tlink.get("href") or "").rstrip("/"):
            raise MarkupError("iam: poster and title link point at different products")
        venue_el = sched.select_one(".iam-venue")
        venue = clean(venue_el.get_text(" ", strip=True)) if venue_el else None
        if venue_el is not None:
            venue_el.extract()
        text = clean(sched.get_text(" ", strip=True))
        found = _IAM_DT.findall(text)
        bare = [d for d in _DAY_MONTH_RE.findall(text) if bg_month(d[1])]
        if len(bare) != len(found):
            raise MarkupError(f"iam: a date without a time in {text!r}")
        title = tlink.get_text(" ", strip=True)
        for d, mon, h, mi in found:
            if not bg_month(mon):
                raise MarkupError(f"iam: unknown month {mon!r}")
            date = nearest_date(int(d), bg_month(mon), today)
            time_ = f"{int(h):02d}:{mi}"
            if venue:
                excluded.append((title, date.isoformat(), time_, f"at another venue: {venue}"))
            else:
                rows.append(mkrow(title, date, time_, url=tlink.get("href")))
    return rows, excluded


def fetch_iam(net, today):
    rows, excluded = parse_iam(_get(net, IAM_URL), today)
    return finish("iam", IAM_SOURCE, rows, today, excluded=excluded)


# ===================================================================== artvent
ARTVENT_VENUE_URL = "https://artvent.bg/teatar/artvent/programa"
ARTVENT_EVENT_URL = "https://artvent.bg/event/{slug}"
ARTVENT_SOURCE = "artvent.bg per-show pages (София, Театър ARTVENT only)"


def parse_artvent_venue(html, today):
    """artvent.bg/teatar/artvent/programa ("Събития за Театър Артвент") →
    [(title, date, time, slug, location)]. Used to discover the productions and
    as a cross-check of the per-show pages."""
    soup = soup_of(html)
    out = []
    for b in soup.select(".cd-timeline-block"):
        d_el, h2, cd = b.select_one(".cd-timeline-img"), b.select_one("h2"), b.select_one(".cd-date")
        a = b.select_one("a[href*='/event/']")
        dm = re.search(r"(\d{1,2})\.(\d{1,2})", d_el.get_text(" ", strip=True) if d_el else "")
        strong = cd.select_one("strong") if cd else None
        if not (dm and h2 and strong and a):
            raise MarkupError("artvent: unreadable programme entry")
        slug = re.search(r"/event/([^/?#]+)", a["href"]).group(1)
        parts = [clean(p) for p in cd.get_text("|", strip=True).split("|") if clean(p)]
        out.append((clean(h2.get_text(" ", strip=True)),
                    nearest_date(int(dm.group(1)), int(dm.group(2)), today).isoformat(),
                    hhmm(strong.get_text()), slug, parts[-1] if parts else ""))
    return out


def parse_artvent_event(html):
    """artvent.bg/event/<slug> → (page title, [(city, address, date, time)]).
    Each `.ticket.item`: `.ticket-city`, `.meta-address` and two more
    `.event-meta-sub` spans, "08-10-2026" and "19:30"."""
    soup = soup_of(html)
    title = clean(re.sub(r"\|\s*Artvent\s*$", "", soup.title.get_text() if soup.title else ""))
    out = []
    for tk in soup.select(".ticket.item"):
        city = tk.select_one(".ticket-city")
        addr = tk.select_one(".meta-address")
        subs = " ".join(clean(s.get_text(" ", strip=True)) for s in tk.select(".event-meta-sub"))
        dm = re.search(r"\b(\d{2})-(\d{2})-(\d{4})\b", subs)
        time_ = hhmm(re.sub(r"\b\d{2}-\d{2}-\d{4}\b", "", subs))
        if not (city and dm and time_):
            raise MarkupError("artvent: unreadable ticket item")
        out.append((clean(city.get_text(" ", strip=True)),
                    clean(addr.get_text(" ", strip=True)) if addr else "",
                    dt.date(int(dm.group(3)), int(dm.group(2)), int(dm.group(1))).isoformat(),
                    time_))
    return title, out


def artvent_split(title, tickets, slug_url):
    """Keep only tickets in София AT Театър ARTVENT. A Sofia date at another
    hall (8 Oct 2026, "Аз, която те обича" at Theatro отсам канала) is not at
    this venue either — filing it here would send people to the wrong door."""
    rows, excluded = [], []
    for city, addr, date, time_ in tickets:
        in_sofia = city.lower() in ("софия", "sofia")
        if in_sofia and "artvent" in addr.lower():
            rows.append(mkrow(title, date, time_, url=slug_url))
        elif in_sofia:
            excluded.append((title, date, time_, f"Sofia, other venue: {addr or '?'}"))
        else:
            excluded.append((title, date, time_, f"touring: {city}, {addr}".rstrip(", ")))
    return rows, excluded


def fetch_artvent(net, today):
    venue = parse_artvent_venue(_get(net, ARTVENT_VENUE_URL), today)
    if not venue:
        raise Unavailable("artvent: empty venue programme")
    titles = OrderedDict()
    for title, _d, _t, slug, _loc in venue:
        titles.setdefault(slug, title)
    rows, excluded, notes = [], [], []
    for slug, title in titles.items():
        url = ARTVENT_EVENT_URL.format(slug=slug)
        try:
            page_title, tickets = parse_artvent_event(_get(net, url))
            if not tickets:
                raise Unavailable("no ticket items on the show page")
        except MarkupError:
            raise
        except Unavailable as e:
            # the venue list is official too: fall back to it for this show
            notes.append(f"{slug}: {e} — venue-list dates used")
            rows += [mkrow(t, d, tm, url=url) for t, d, tm, s, loc in venue
                     if s == slug and "artvent" in loc.lower()]
            continue
        r, e = artvent_split(title or page_title, tickets, url)
        rows += r
        excluded += e
    shown = {(normalise_show_title(r[0]), r[1], r[2]) for r in rows if r[1] >= today.isoformat()}
    listed = {(normalise_show_title(t), d, tm) for t, d, tm, s, loc in venue
              if d >= today.isoformat() and "artvent" in loc.lower()}
    for k in sorted(listed - shown):
        notes.append(f"on the venue list but not on its show page: {k}")
    for k in sorted(shown - listed):
        notes.append(f"on a show page but not on the venue list: {k}")
    return finish("artvent", ARTVENT_SOURCE, rows, today, excluded=excluded, notes=notes)


# ===================================================================== sfumato
SFUMATO_URL = "http://sfumato.info/"
SFUMATO_SOURCE = "sfumato.info (own programme, HTTP only)"


def parse_sfumato(html, today):
    """sfumato.info (HTTP only, windows-1251) — one section per stage
    (`.scena_opt_scena_N`, named by the `#opt_scena_N` switcher), blocks of
    `.date-box-month` "Октомври", `.date-box-day` "08", `.date-box-time" "19.00",
    `a.programme-title`. No year is printed."""
    soup = soup_of(html)
    names = {}
    for a in soup.select("a.programme-option[id]"):
        div = a.find("div")
        names[a["id"]] = clean(div.get_text(" ", strip=True)) if div else None
    rows = []
    for sec in soup.select("div[class*='scena_opt_scena_']"):
        key = next((c for c in sec.get("class", []) if c.startswith("scena_opt_scena_")), "")
        hall = names.get(key[len("scena_"):])
        for b in sec.select(".programme-block"):
            def g(sel):
                el = b.select_one(sel)
                return clean(el.get_text(" ", strip=True)) if el else ""
            mon, day, time_ = bg_month(g(".date-box-month")), g(".date-box-day"), hhmm(g(".date-box-time"))
            a = b.select_one("a.programme-title")
            if not (mon and day.isdigit() and time_ and a):
                raise MarkupError("sfumato: unreadable programme block")
            buy = b.select_one("a.buy-ticket-btn[href]")
            rows.append(mkrow(a.get_text(" ", strip=True), nearest_date(int(day), mon, today),
                              time_, hall=hall,
                              url=buy["href"] if buy else absolute(SFUMATO_URL, a.get("href"))))
    return rows


def fetch_sfumato(net, today):
    return finish("sfumato", SFUMATO_SOURCE, parse_sfumato(_get(net, SFUMATO_URL), today), today)


# ==================================================================== citymark
CITYMARK_URL = ("https://theatre.art.bg/" + urllib.parse.quote("сити-марк-арт-център") + "___173")
CITYMARK_SOURCE = "theatre.art.bg (official channel)"


def parse_art_venue(html, today):
    """A theatre's page on theatre.art.bg — "Месечна програма": tabs (Октомври,
    Ноември, …) over panes of `.kupi-bilet-box` (day number, `h3 a` title,
    "17 Събота 11.00 часа, сцена" — possibly several times). An empty pane is a
    month not published yet. → (rows, {(year, month): count})."""
    soup = soup_of(html)
    tabs = [clean(a.get_text(" ", strip=True)) for a in soup.select("#parentHorizontalTab ul.resp-tabs-list li a")]
    cont = soup.select_one("#parentHorizontalTab .resp-tabs-container")
    panes = cont.find_all("div", recursive=False) if cont else []
    if not tabs or len(tabs) != len(panes):
        raise MarkupError("theatre.art.bg: monthly programme tabs not found")
    rows, months = [], OrderedDict()
    for name, pane in zip(tabs, panes):
        mon = bg_month(name)
        if not mon:
            raise MarkupError(f"theatre.art.bg: tab {name!r} is not a month")
        n = 0
        for box in pane.select(".kupi-bilet-box"):
            num, a, p = box.select_one(".number"), box.select_one("h3 a"), box.select_one("p")
            ptext = clean(p.get_text(" ", strip=True)) if p else ""
            pm = re.match(r"(\d{1,2})\b", ptext)
            if not (num and a and pm) or int(pm.group(1)) != int(clean(num.get_text()) or 0):
                raise MarkupError("theatre.art.bg: unreadable programme entry")
            date = nearest_date(int(pm.group(1)), mon, today)
            if date is None or weekday_conflict(date, ptext):
                raise MarkupError(f"theatre.art.bg: weekday mismatch {ptext!r}")
            hall = re.sub(r"^.*часа\s*,?\s*", "", ptext)
            hall = None if hall.lower() in ("", "сцена") else hall
            for time_ in times_in(ptext):
                rows.append(mkrow(strip_quotes(a.get_text(" ", strip=True)), date, time_, hall=hall,
                                  url=absolute("https://theatre.art.bg/", a.get("href"))))
                n += 1
        first = nearest_date(1, mon, today.replace(day=1))
        months[(first.year, first.month)] = n
    return rows, months


def fetch_citymark(net, today):
    rows, months = parse_art_venue(_get(net, CITYMARK_URL), today)
    notes = [f"months on the page: " + ", ".join(f"{y}-{m:02d}={n}" for (y, m), n in months.items())]
    return finish("citymark", CITYMARK_SOURCE, rows, today, notes=notes)


# ========================================================================= tba
TBA_URL = "https://www.tba.art.bg/programabg.php?year={y}&month={m}"
TBA_TICKETS_URL = "https://tickets.tba.bg/Account/Login.aspx?ReturnUrl=%2f"
TBA_SOURCE = "tba.art.bg/programabg.php + tickets.tba.bg public list"


def parse_tba_programme(html, year, month):
    """tba.art.bg/programabg.php?year=Y&month=M (windows-1251, no charset
    header): "Програма за месец Октомври", then per stage a
    `div.antetka-programa-2` heading (Голяма сцена, Камерна сцена "МИРАКЪЛ",
    сцена-клуб "МаксиМ") followed by `div.program-row-2` entries:
    "08 Октомври (Четвъртък)" | "19.00 ч." | '"Фейк"' or 'Тяло в лед - Гостува
    ДТ-Русе' (a visiting company: title "Тяло в лед", meta guest)."""
    soup = soup_of(html)
    text = soup.get_text(" ", strip=True)
    hm = re.search(r"Програма за месец\s+([А-Яа-я]+)", text)
    if not hm or bg_month(hm.group(1)) != month:
        raise MarkupError(f"tba: page is not the programme for month {month}")
    rows, stage = [], None
    for el in soup.select("div.antetka-programa-2, div.program-row-2"):
        if "antetka-programa-2" in el.get("class", []):
            stage = clean(el.get_text(" ", strip=True))
            continue
        d_el, right = el.select_one(".program-left .time"), el.select_one(".program-right")
        dtext = clean(d_el.get_text(" ", strip=True)) if d_el else ""
        dm = _DAY_MONTH_RE.search(dtext)
        sp = right.select_one("span") if right else None
        time_ = hhmm(sp.get_text(" ", strip=True) if sp else "")
        a = right.select_one("a") if right else None
        raw = a.get_text(" ", strip=True) if a else ""
        if not (stage and dm and time_ and raw) or bg_month(dm.group(2)) != month:
            raise MarkupError(f"tba: unreadable programme entry {dtext!r}")
        date = dt.date(year, month, int(dm.group(1)))
        if weekday_conflict(date, dtext):
            raise MarkupError(f"tba: weekday mismatch {dtext!r} in {year}")
        title, guest = split_guest(strip_quotes(raw))
        rows.append(mkrow(strip_quotes(title), date, time_, hall=stage, guest=guest,
                          url=absolute("https://www.tba.art.bg/", a.get("href"))))
    return rows


def parse_tba_tickets(html):
    """tickets.tba.bg's public sign-in page (plain GET, nothing submitted)
    carries "Предстоящи представления": table#MainContent_grdPlan rows of
    Ден | "08-10-2026 г. 19:00 ч." | "ФЕЙК" — every stage, upper-case titles."""
    soup = soup_of(html)
    table = soup.select_one("table#MainContent_grdPlan")
    if table is None:
        for t in soup.select("table"):
            if t.find("table") is None and "Дата/час" in t.get_text():
                table = t
                break
    if table is None:
        raise MarkupError("tba tickets: upcoming-performances table not found")
    rows = []
    for tr in table.select("tr"):
        tds = tr.find_all("td")
        if len(tds) != 3:
            continue
        day, when, title = (clean(td.get_text(" ", strip=True)) for td in tds)
        dm = re.match(r"(\d{2})-(\d{2})-(\d{4})", when)
        time_ = hhmm(when[dm.end():] if dm else "")
        if not (dm and time_ and title):
            raise MarkupError(f"tba tickets: unreadable row {when!r}")
        date = dt.date(int(dm.group(3)), int(dm.group(2)), int(dm.group(1)))
        if weekday_conflict(date, day):
            raise MarkupError(f"tba tickets: weekday mismatch {day!r} {when!r}")
        name, guest = split_guest(title)
        rows.append(mkrow(name, date, time_, guest=guest))
    return rows


def _prefix_related(a, b):
    ta, tb = a.split(), b.split()
    n = min(len(ta), len(tb))
    return n >= 2 and ta[:n] == tb[:n]


def _shared_words(a, b):
    """Content words (4+ letters) two normalised titles share, when they are at
    least half of the shorter title's content words; else 0."""
    wa = {w for w in a.split() if len(w) >= 4}
    wb = {w for w in b.split() if len(w) >= 4}
    common = wa & wb
    return len(common) if wa and wb and 2 * len(common) >= min(len(wa), len(wb)) else 0


def reconcile_tba(programme, tickets):
    """Union of the two official ТБА lists → (rows, disagreements, variants).

    Passes, each only over entries not yet paired:
      1. same date, time and normalised title;
      2. same date and time, one title a word-prefix of the other ("Урок по
         български" / "УРОК ПО БЪЛГАРСКИ по Ив. Вазов") → a title variant;
      3. same date and time, the titles share most content words and the slot
         holds exactly one unpaired entry on each side that does ('Лекция №3 за
         "СИЛАТА НА СЛОВОТО"' / "ЗА СИЛАТА НА СЛОВОТО спектакъл на Камен
         Донев") → one performance, reported as "title differs";
      4. same date and title at another time → the programme's time is kept
         (the ticket-centre time is used only when tba.art.bg has no entry for
         that date+title) and reported as "time differs";
      5. the rest stay, from whichever list has them, reported as one-sided.
    A pair takes the programme's stage and proper-case title. Nothing is ever
    dropped."""
    norm = {id(r): normalise_show_title(r[0]) for r in list(programme) + list(tickets)}
    used, out, dis, variants = set(), [], [], []

    def merged(p, t, **more):
        used.update((id(p), id(t)))
        meta = dict(p[3])
        meta["sources"] = ["programme", "tickets"]
        if t[0] != p[0]:
            meta["tickets_title"] = t[0]
        meta.update(more)
        out.append((p[0], p[1], p[2], meta))

    def free(rows):
        return [r for r in rows if id(r) not in used]

    for test in (lambda a, b: a == b, _prefix_related):           # passes 1-2
        for t in free(tickets):
            cands = [p for p in free(programme) if (p[1], p[2]) == (t[1], t[2])
                     and test(norm[id(p)], norm[id(t)])]
            if cands:
                if norm[id(cands[0])] != norm[id(t)]:
                    variants.append((t[1], cands[0][0], t[0]))
                merged(cands[0], t)
    for t in free(tickets):                                        # pass 3
        cands = [p for p in free(programme) if (p[1], p[2]) == (t[1], t[2])
                 and _shared_words(norm[id(p)], norm[id(t)])]
        rivals = [x for x in free(tickets) if (x[1], x[2]) == (t[1], t[2])
                  and any(_shared_words(norm[id(p)], norm[id(x)]) for p in cands)]
        if len(cands) == 1 and len(rivals) == 1:
            dis.append(("title differs", t[1], cands[0][0], f"{t[2]} — tickets.tba.bg calls it "
                        f"'{t[0]}'; one performance kept, programme title"))
            merged(cands[0], t)
    for t in free(tickets):                                        # pass 4
        same = [p for p in free(programme) if p[1] == t[1]
                and (norm[id(p)] == norm[id(t)] or _prefix_related(norm[id(p)], norm[id(t)]))]
        if same:
            p = same[0]
            dis.append(("time differs", t[1], p[0], f"programme {p[2]} / tickets {t[2]} — programme kept"))
            merged(p, t, tickets_time=t[2])
    for t in free(tickets):                                        # pass 5
        meta = dict(t[3])
        meta["sources"] = ["tickets"]
        out.append((t[0], t[1], t[2], meta))
        dis.append(("only on tickets.tba.bg", t[1], t[0], f"{t[2]} — kept, stage unknown"))
    for p in free(programme):
        meta = dict(p[3])
        meta["sources"] = ["programme"]
        out.append((p[0], p[1], p[2], meta))
        dis.append(("only on tba.art.bg", p[1], p[0], f"{p[2]} {p[3].get('hall') or ''} — kept".strip()))
    out.sort(key=lambda r: (r[1], r[2], r[0]))
    dis.sort(key=lambda d: (d[1], d[0], d[2]))
    return out, dis, variants


def fetch_tba(net, today):
    programme, notes, prog_ok = [], [], False
    for i, (y, m) in enumerate(months_from(today)):
        try:
            page = parse_tba_programme(_get(net, TBA_URL.format(y=y, m=m)), y, m)
        except Unavailable as e:
            notes.append(f"tba.art.bg {y}-{m:02d}: {e} — programme read up to the month before")
            break
        prog_ok = True
        if not page:
            break
        programme += page
    tickets, tix_ok = [], False
    try:
        tickets = parse_tba_tickets(_get(net, TBA_TICKETS_URL))
        tix_ok = True
    except Unavailable as e:
        notes.append(f"tickets.tba.bg: {e}")
    if not prog_ok and not tix_ok:
        raise Unavailable("tba: neither official list could be read")
    t0 = today.isoformat()
    programme = [r for r in programme if r[1] >= t0]
    tickets = [r for r in tickets if r[1] >= t0]
    if prog_ok and tix_ok:
        rows, dis, variants = reconcile_tba(programme, tickets)
        notes += [f"DISAGREE {kind}: {d} {title} — {detail}" for kind, d, title, detail in dis]
        notes += sorted({f"title variant: '{p}' = tickets '{t}'" for _d, p, t in variants})
    else:
        rows = programme or tickets
        notes.append("only one of the two official lists was read — no reconciliation")
    last = max([r[1] for r in programme] + [r[1] for r in tickets] or [t0])
    return finish("tba", TBA_SOURCE, rows, today, covered_to=last, month_check=False, notes=notes)


# ========================================================== no official source
NO_OFFICIAL_SOURCE = {
    "atelie313": "atelie313.com is a JavaScript-only super.website app (no programme in the "
                 "HTML); theatre.art.bg lists it (theatre id 22) but that is an aggregator — "
                 "its rows stay preliminary",
    "natfiz": "the monthly programme is a single JPG poster (natfiz.bg/udt-mesechna-programa/)",
    "new-ndk": "no programme of its own: tickets.ndk.bg lists every NDK hall without "
               "identifying Нов театър",
    "derida": "derida-dance.com carries no current programme (archive only)",
}

FETCHERS = OrderedDict([
    ("national", fetch_national), ("sofia-th", fetch_sofia_th), ("th199", fetch_th199),
    ("tba", fetch_tba), ("zad-kanala", fetch_zadkanala), ("vazrazhdane", fetch_vazrazhdane),
    ("mladezhki", fetch_mladezhki), ("kuklen", fetch_kuklen), ("satira", fetch_satira),
    ("salzaismyah", fetch_salzaismyah), ("toplo", fetch_toplo), ("iam", fetch_iam),
    ("artvent", fetch_artvent), ("sfumato", fetch_sfumato), ("citymark", fetch_citymark),
])

# why the last fetch_* of each venue returned None (for the run report)
LAST_STATUS: dict = {}


def fetch_venue(venue, net=None, today=None):
    """The contract dict for `venue`, or None. Never raises."""
    today = today or sofia_today()
    if venue in NO_OFFICIAL_SOURCE:
        LAST_STATUS[venue] = "no official source: " + NO_OFFICIAL_SOURCE[venue]
        return None
    fn = FETCHERS.get(venue)
    if fn is None:
        LAST_STATUS[venue] = "unknown venue id"
        return None
    if net is None:
        from netfetch import Fetcher
        net = Fetcher()
    try:
        res = fn(net, today)
        LAST_STATUS[venue] = "ok"
        return res
    except Unavailable as e:
        LAST_STATUS[venue] = str(e)
    except Exception as e:                               # a parser bug must not stop the run
        LAST_STATUS[venue] = f"error: {type(e).__name__}: {e}"
    print(f"  ! official {venue}: {LAST_STATUS[venue]}", file=sys.stderr, flush=True)
    return None


def fetch_all(net=None, today=None, venues=None):
    """{venue id: result or None} for every theatre (or `venues`)."""
    if net is None:
        from netfetch import Fetcher
        net = Fetcher()
    ids = venues or list(FETCHERS) + list(NO_OFFICIAL_SOURCE)
    return OrderedDict((v, fetch_venue(v, net, today)) for v in ids)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--venue", action="append", help="theatre id (repeatable)")
    ap.add_argument("--json", help="write the results to this path")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every row")
    args = ap.parse_args(argv)
    res = fetch_all(venues=args.venue)
    print(f"\n  {'venue':12s} {'rows':>5s} {'extra':>5s} {'excl':>5s}  covered                  source / status")
    for v, r in res.items():
        if r is None:
            print(f"  {v:12s} {'—':>5s} {'':>5s} {'':>5s}  {'':24s} {LAST_STATUS.get(v, '')[:90]}")
            continue
        print(f"  {v:12s} {len(r['rows']):>5d} {len(r['extra_rows']):>5d} {len(r['excluded']):>5d}  "
              f"{r['covered_from']} → {r['covered_to']}  {r['source']}")
        if args.verbose:
            for row in r["rows"]:
                print(f"      {row[1]} {row[2]}  {row[0]}  [{row[3].get('hall') or ''}]")
            for row in r["extra_rows"]:
                print(f"    + {row[1]} {row[2]}  {row[0]}  (extra)")
            for e in r["excluded"]:
                print(f"    x {e[1]} {e[2]}  {e[0]}  — {e[3]}")
            for n in r["notes"]:
                print(f"    ~ {n}")
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
