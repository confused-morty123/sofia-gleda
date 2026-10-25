#!/usr/bin/env python3
"""Sofia Gleda — festivals and limited runs that programata.bg announces -> events.json.

The owner (2026-10-15): "Limited cinema or theatre events can be taken from
sofialive or programata and should have their listing in the limited and
exclusive section." Research: notes/sg_sources/EVENTS_RESEARCH.md.

  * programata.bg files festival announcements under two WordPress categories,
    kino/festivali (159, cinema) and stsena/festivals (146, stage). Their post
    template has a date field, `<div class="red-text">12 ноември 2026 - 29
    ноември 2026</div>`; the REST API lists the posts (title, link, categories,
    publication date) but not that field, so each post page is read once (and
    again only when the post is modified).
  * sofialive.bg is a magazine: articles with no event dates, venues or
    categories, only prose. Nothing there can be read without guessing, so it
    is not used.

Rules. Accuracy first: a post we cannot read without guessing is left out.
  * Dates: the post's date field; else one date (or range) written in the
    post's TITLE ("14-тата Нощ на театрите идва на 21 ноември"), its year taken
    from the publication date; else the post is skipped. Never from the prose.
  * Sofia: a Sofia city category, or "София"/"Sofia" in the title, description
    or text, or a catalogued Sofia venue named in the text as a place. A post
    filed under another city's category (Варна, Бургас…) and not under Sofia's,
    or whose headline names another town and not Sofia, is skipped.
  * Scope from the category: 159 cinema, 146 theatre (both, when a post is in
    both and in both sections).
  * The listing: festivals and limited runs ("festival" when the name says so,
    else "limited"). The categories hold nothing else, so ordinary repertoire
    (film and production posts, categories 160 and 143) never comes in.
  * Copy: the title is the post headline's own words (the segment that names
    the event, minus "Започва 16. издание на" and similar); the blurb is built
    from facts only (kind, dates, catalogued venues named in the post), never
    copied from the article. titleEn: translate.py's DeepL path and cache
    (english_titles), else the title romanised: English mode shows no Cyrillic.

events.json (keep-previous):
    {"generated": ISO, "ok": bool, "source": "programata.bg",
     "events": [EVENT], "posts": {"<post id>": {"modified", "verdict", "id"}}}
    EVENT = the app's EVENTS shape (id, kind, scope, status, title, titleEn,
    from, to, g, blurbBg, blurbEn, tag, source) plus url (the source page),
    auto: true, post (programata post id), modified, venues (catalogued venue
    ids), titles ([{"t": emphasised title, "v": [venue ids in its sentence]}],
    for the membership map) and missed (runs the source did not list it).
  * A run whose listing fails keeps every previous event (only ended ones go).
  * An event is removed when its end date passes, or when the listing has not
    carried its post for MISS_LIMIT runs in a row.
  * A post page that cannot be read keeps that post's previous record.

for_page() shapes the file for one build (inject_data.py calls it): ended
events dropped, an event the hand-curated seed already lists dropped (the seed
wins), duplicates among programata posts merged, and EVENT_MEMBERS computed:

    EVENT_MEMBERS = {"<event id>": ["<film or show id>", …]}

A film or show is a member only when the event's post emphasises its exact
title (normalised), names its venue in the same sentence (in the same clause
when the sentence lists several titles), and the build has a screening or
performance of it at that venue within the event's dates. No fuzzy matching:
an ambiguous title (two catalogue items) gives nothing.

    python3 scripts/fetch_events.py      # live: GET only, polite (2.5 s/host)
"""
from __future__ import annotations

import argparse, datetime as dt, hashlib, html as _html, json, os, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
OUT = ROOT / "events.json"

SOURCE = "programata.bg"
API = "https://programata.bg/wp-json/wp/v2"
CATEGORIES = {159: "cinema", 146: "theatre"}      # kino/festivali, stsena/festivals
SECTION = {328: "cinema", 329: "theatre"}         # kino, stsena
# programata's city sub-categories (children of kino-saloni, teatri, muzei,
# galerii, kontsertni-zali). A post filed under another city and not Sofia is
# not a Sofia event.
SOFIA_CATS = {215, 220, 376, 400, 425}
OTHER_CITY_CATS = {216, 217, 218, 219, 221, 222, 223, 224, 377, 378, 379, 380,
                   401, 402, 403, 404, 426, 427, 428, 429}
LOOKBACK_DAYS = 180         # posts published earlier are not listed (festivals are announced weeks ahead)
MISS_LIMIT = 3              # runs in a row the listing may omit a post before its event goes
MAX_PAGES = 40              # post pages read per run (the rest wait for the next run)
HOST_DELAY = 2.5            # seconds between requests to programata.bg
RUN_BUDGET = 300            # seconds for the whole step

BG_MONTHS = ("януари", "февруари", "март", "април", "май", "юни", "юли", "август",
             "септември", "октомври", "ноември", "декември")
EN_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December")
MONTHNUM = {m: i + 1 for i, m in enumerate(BG_MONTHS)}
_MON = "(" + "|".join(BG_MONTHS) + ")"
_DASH = r"\s*(?:-|–|—|до)\s*"

# ------------------------------------------------------------------ helpers

def sofia_today():
    # listing_window knows Sofia's summer/winter offset even without a tz database
    # (the old fallback here added a fixed +3 h, a day off late on winter evenings).
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import listing_window
    return listing_window.sofia_today()


def ws(text):
    return " ".join((text or "").split())


def norm(text):
    """Lower case, ё -> е, quotes and punctuation -> spaces, one space."""
    s = (text or "").lower().replace("ё", "е").replace("ѝ", "и")
    s = re.sub(r"[„“”\"'’‘«»`.,!?:;\-–—_()\[\]/#*|&+]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def grab(src, name):
    """`const NAME=[…]` / `{…}` from a page, by bracket matching that skips strings."""
    m = re.search(r"\bconst\s+" + re.escape(name) + r"\s*=\s*", src)
    if not m:
        return None
    depth, start, in_str, esc = 0, None, False, False
    for k in range(m.end(), len(src)):
        c = src[k]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "[{":
            if depth == 0:
                start = k
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(src[start:k + 1])
                except ValueError:
                    return None
    return None


def catalogue(src):
    """The page's catalogue as fetch_events needs it."""
    out = {}
    for name in ("EVENTS", "FILMS", "SHOWS", "THEATRES", "CINEMAS", "PERFORMANCES", "SHOWTIMES"):
        v = grab(src, name)
        out[name] = v if isinstance(v, list) else []
    return out


# ------------------------------------------------------------------ dates

def _date(y, m, d):
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def parse_field_dates(text):
    """The post's date field ("12 ноември 2026 - 29 ноември 2026", or one date):
    (from, to) as ISO strings, or None when it holds no full date."""
    found = []
    for d, mon, y in re.findall(r"(\d{1,2})\s+" + _MON + r"\s+(\d{4})", (text or "").lower()):
        got = _date(int(y), MONTHNUM[mon], int(d))
        if got:
            found.append(got)
    if not found or len(found) > 2:
        return None
    frm, to = found[0], found[-1]
    if frm > to:
        return None
    return frm.isoformat(), to.isoformat()


def _anchor_year(day, month, published):
    """The year of "21 ноември" written in a post published on `published`:
    the publication year, or the next one when that date would be more than
    two months before the post."""
    for y in (published.year, published.year + 1):
        got = _date(y, month, day)
        if got and got >= published - dt.timedelta(days=60):
            return got
    return None


_T_CROSS = re.compile(r"(?<!\d)(\d{1,2})\s+" + _MON + _DASH + r"(\d{1,2})\s+" + _MON + r"(?![а-я])")
_T_RANGE = re.compile(r"(?<!\d)(?:от\s+)?(\d{1,2})" + _DASH + r"(\d{1,2})\s+" + _MON + r"(?![а-я])")
_T_ONE = re.compile(r"(?<!\d)(\d{1,2})\s+" + _MON + r"(?![а-я])")


def parse_title_dates(title, published):
    """One date or range written in a headline, e.g. "… идва на 21 ноември",
    "… 17–24 октомври", "… 28 октомври – 3 ноември". (from, to) or None; None
    also when the headline holds more than one date expression."""
    t = (title or "").lower()
    if re.search(r"\b\d{1,2}\s+" + _MON + r"\s+\d{4}", t):
        return None                      # a dated year in a headline is a different shape; leave it
    m = _T_CROSS.search(t)
    if m:
        a = _anchor_year(int(m.group(1)), MONTHNUM[m.group(2)], published)
        b = _anchor_year(int(m.group(3)), MONTHNUM[m.group(4)], published)
        rest = t[:m.start()] + t[m.end():]
    else:
        m = _T_RANGE.search(t)
        if m:
            mon = MONTHNUM[m.group(3)]
            a = _anchor_year(int(m.group(1)), mon, published)
            b = _date(a.year, mon, int(m.group(2))) if a else None
            rest = t[:m.start()] + t[m.end():]
        else:
            ms = list(_T_ONE.finditer(t))
            if len(ms) != 1:
                return None
            m = ms[0]
            a = b = _anchor_year(int(m.group(1)), MONTHNUM[m.group(2)], published)
            rest = t[:m.start()] + t[m.end():]
    if _T_ONE.search(rest) or not a or not b:
        return None
    if b < a:
        b = _date(b.year + 1, b.month, b.day)
        if not b:
            return None
    return a.isoformat(), b.isoformat()


def date_words(frm, to, lang):
    """"17–24 октомври 2026", "28 октомври – 3 ноември 2026", "21 ноември 2026"."""
    a, b = dt.date.fromisoformat(frm), dt.date.fromisoformat(to)
    months = BG_MONTHS if lang == "bg" else EN_MONTHS
    if a == b:
        return f"{a.day} {months[a.month - 1]} {a.year}"
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{b.day} {months[a.month - 1]} {a.year}"
    if a.year == b.year:
        return f"{a.day} {months[a.month - 1]} – {b.day} {months[b.month - 1]} {a.year}"
    return f"{a.day} {months[a.month - 1]} {a.year} – {b.day} {months[b.month - 1]} {b.year}"


# ------------------------------------------------------------------ the post page

def parse_page(page_html):
    """The facts a programata post page holds: headline, date field (None when
    the template has none, "" when it is empty), description, the article text
    and its emphasised phrases (<strong>/<em>), in order."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page_html, "lxml")
    h1 = soup.find("h1")
    red = soup.select_one("div.red-text")
    body = soup.select_one("div.text")
    desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    pub = soup.find("meta", attrs={"property": "article:published_time"})
    dates = None if red is None else ws(red.get_text(" ", strip=True))
    text, emph = "", []
    if body is not None:
        for t in body.find_all(["script", "style", "figure", "noscript"]):
            t.decompose()
        for t in body.select("div.red-text"):
            t.decompose()                # the date field is read above, not as prose
        text = ws(body.get_text(" ", strip=True))
        for e in body.find_all(["strong", "em", "b", "i"]):
            if e.find_parent(["strong", "em", "b", "i"]) is not None:
                continue
            phrase = ws(e.get_text(" ", strip=True)).strip(" ,.;:!?–—-")
            if phrase:
                emph.append(phrase)
    return {
        "h1": ws(h1.get_text(" ", strip=True)) if h1 else "",
        "dates": dates,
        "desc": ws((desc or {}).get("content", "")) if desc else "",
        "published": ((pub or {}).get("content") or "")[:10] if pub else "",
        "text": text,
        "emph": emph,
    }


# ------------------------------------------------------------------ names

# A segment that names a festival; without one the headline's lead segment is the name
# ("Хофкино 2026 | Лятно кино в двора на …", "Master of Art 2026 | Кинодвор G8").
_KEYWORD = re.compile(r"фестивал|\bфест\b|\bfest\b|festival|киномания|синелибри|cinelibri|"
                      r"нощ на театрите", re.I)
_FESTIVAL = re.compile(r"фестивал|\bфест\b|\bfest\b|festival|киномания|синелибри|cinelibri", re.I)
_YEAR_CUT = re.compile(r"^(.*?(?:фестивал|\bфест\b|\bfest\b|festival)[^|:]*?\b20\d\d)\b.+$", re.I)
_LEAD_VERB = re.compile(r"^(?:започва|идва|стартира|предстои|открива\s+се|завръща\s+се)\s+", re.I)
_ORDINAL = r"\d+\s*[-.]?\s*(?:[a-zа-я]{1,4})?"
_EDITION = re.compile(r"^(?:.*?\s)?" + _ORDINAL + r"\s+издание\s+на\s+", re.I)
_EDITION_WORD = re.compile(r"^(?:.*?\s)?(?:новото|поредното|юбилейното)\s+издание\s+на\s+", re.I)
_LEAD_ORDINAL = re.compile(r"^\d+\s*(?:[-.]?\s*(?:о|то|ия|ият|ата|тата|ти|ри|ви)|\.)\s+", re.I)
_TAIL_VERB = re.compile(r"\s+(?:идва|започва|стартира|предстои|се\s+завръща|ще\s+се\s+проведе|"
                        r"представя|открива|превзема)\b.*$", re.I)


def event_name(headline):
    """The event's name in the headline's own words: the " | " segment that
    names the event, without "Започва 16. издание на" or a trailing "идва на
    21 ноември". Falls back to the headline's first segment."""
    head = ws(headline)
    segs = [s.strip() for s in re.split(r"\s+\|\s+", head) if s.strip()] or [head]
    seg = next((s for s in segs if _KEYWORD.search(s)), segs[0])
    if ":" in seg:
        before, after = seg.split(":", 1)
        if _KEYWORD.search(before):
            seg = before
        elif re.search(r"издание\s+на\s+", after, re.I):
            seg = after.strip()
    seg = _LEAD_VERB.sub("", seg)
    seg = _EDITION.sub("", seg)
    seg = _EDITION_WORD.sub("", seg)
    seg = _LEAD_ORDINAL.sub("", seg)
    seg = _TAIL_VERB.sub("", seg)
    seg = _YEAR_CUT.sub(r"\1", seg)    # "Sofia Short Fest 2026 със силен акцент …" -> "Sofia Short Fest 2026"
    seg = seg.strip(" :–—-,.")
    if len(seg) < 3:
        seg = segs[0]
    return seg[:1].upper() + seg[1:]


_ROMAN = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z",
          "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
          "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch",
          "ш": "sh", "щ": "sht", "ъ": "a", "ь": "y", "ю": "yu", "я": "ya", "ѝ": "i", "ё": "e"}


def slug(text, limit=40):
    s = "".join(_ROMAN.get(c, c) for c in (text or "").lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if len(s) > limit:
        s = s[:limit].rsplit("-", 1)[0] if "-" in s[:limit] else s[:limit]
    return s or "event"


def event_id(name, post_id):
    """A stable slug: the name romanised, plus the post id (unique, and the
    same post always gives the same id; the first id is kept for good)."""
    return f"{slug(name)}-p{post_id}"


def colours(eid):
    """Two hex colours derived from the id (same id, same colours): a deep
    tone and a near-black of the same hue, like the seed's pairs."""
    import colorsys
    hue = int(hashlib.sha1(eid.encode("utf-8")).hexdigest()[:8], 16) % 360 / 360.0

    def hexc(light, sat):
        r, g, b = colorsys.hls_to_rgb(hue, light, sat)
        return "#{:02X}{:02X}{:02X}".format(round(r * 255), round(g * 255), round(b * 255))
    return [hexc(0.36, 0.62), hexc(0.08, 0.70)]


# ------------------------------------------------------------------ venues

_STOP = {"на", "и", "в", "във", "с", "за", "от", "the", "of", "a"}
_PERSON = re.compile(r"^[А-ЯA-Z][а-яa-z]+(?:\s+[А-ЯA-Z][а-яa-z]+)+$")
_LOCATIVE = {"в", "във", "на", "към", "до", "пред", "от"}


def venue_aliases(venues):
    """{venue id: [token tuples]} from the catalogue's names: the full name, the
    English name, and the name inside the quotes („Дерида“) unless that is a
    person's name or "София"."""
    out = {}
    for v in venues:
        names = [v.get("name") or "", v.get("nameEn") or ""]
        for inner in re.findall(r"„([^“”]+)[“”]", v.get("name") or ""):
            if len(inner) >= 4 and norm(inner) != "софия" and not _PERSON.match(inner.strip()):
                names.append(inner)
        toks = []
        for n in names:
            t = tuple(w for w in norm(n).split() if w not in _STOP)
            if t and t not in toks:
                toks.append(t)
        if toks:
            out[v["id"]] = toks
    return out


def venues_in(text, aliases):
    """Catalogued venues named in `text` in a place phrase ("в I AM Studio",
    "на Сцена Дерида", "в зала 1 на НДК", "към РЦСИ Топлоцентрала"): every word
    of a name within a short window, preceded within two words by в / на /
    към / до / пред / от. "в партньорство с Френски институт" is not a place.
    In the order the text names them."""
    words = norm(text).split()
    found = {}
    for vid, forms in aliases.items():
        for toks in forms:
            n = len(toks)
            for i, w in enumerate(words):
                if (n == 1 and w != toks[0]) or (n > 1 and w not in toks):
                    continue
                if not all(t in words[i:i + n + 2] for t in toks):
                    continue
                if any(b in _LOCATIVE for b in words[max(0, i - 2):i]):
                    found[vid] = min(found.get(vid, i), i)
                    break
    return sorted(found, key=found.get)


def _span(text, i, j, stops):
    left = max(text.rfind(c, 0, i) for c in stops)
    ends = [k for k in (text.find(c, j) for c in stops) if k >= 0]
    return text[left + 1:(min(ends) if ends else len(text))]


def sentence_of(text, phrase):
    """The sentence of `text` that holds `phrase` (first occurrence)."""
    i = text.find(phrase)
    return _span(text, i, i + len(phrase), ".!?;") if i >= 0 else ""


def venues_for(text, phrase, phrases, aliases):
    """Venues that go with an emphasised title: those named in its sentence;
    when the sentence lists several titles ("Х в Театър О3, Y на Сцена Дерида,
    и Z в I AM Studio"), only those in its own comma clause."""
    sent = sentence_of(text, phrase)
    if sum(1 for p in set(phrases) if p and p in sent) <= 1:
        return venues_in(sent, aliases)
    i = sent.find(phrase)
    return venues_in(_span(sent, i, i + len(phrase), ","), aliases)


# ------------------------------------------------------------------ one post -> one event

def scope_of(categories):
    cats = set(categories or [])
    kinds = {CATEGORIES[c] for c in cats if c in CATEGORIES}
    if len(kinds) == 1:
        return kinds.pop()
    if len(kinds) == 2:
        secs = {SECTION[c] for c in cats if c in SECTION}
        return secs.pop() if len(secs) == 1 else "both"
    return None


_SOFIA_WORD = re.compile(r"(?<![а-яa-z])(?:софия|sofia|софийск[а-я]*)(?![а-яa-z])")
# Another town (or its adjective: "Варненско лято", "Родопски …") in a headline
# that does not say Sofia: the event is there, whatever the text mentions.
_OTHER_TOWN = re.compile(
    r"(?<![а-я])(?:варн|пловдив|бургас|стара загора|старозагор|русе|русенск|велико търново|"
    r"търновск|благоевград|плевен|шумен|габрово|хасково|сливен|ямбол|казанлък|добрич|"
    r"кърджали|смолян|пазарджик|перник|кюстендил|видин|враца|монтана|ловеч|търговище|"
    r"разград|силистра|созопол|несебър|балчик|банско|родоп|севтополис)[а-я]*")


def is_sofia(categories, page, venue_ids):
    """(verdict, why). A city category decides; then a headline naming another
    town and not Sofia; else the post must say София / Sofia, or name a
    catalogued venue as a place."""
    cats = set(categories or [])
    if cats & SOFIA_CATS:
        return True, "Sofia category"
    if cats & OTHER_CITY_CATS:
        return False, "filed under another city"
    head = (page.get("h1") or "").lower()
    if _OTHER_TOWN.search(head) and not _SOFIA_WORD.search(head):
        return False, "the headline names another town"
    blob = " ".join((page.get("h1", ""), page.get("desc", ""), page.get("text", "")))
    if _SOFIA_WORD.search(blob.lower()):
        return True, "says Sofia"
    if venue_ids:
        return True, "names a Sofia venue"
    return False, "the post does not place it in Sofia"


def build_event(post, page, cat, today):
    """(event, None) or (None, why it is left out). `post` is the REST record
    (id, date, modified, link, title, categories); `page` is parse_page()."""
    title_raw = _html.unescape(re.sub(r"<[^>]+>", "", ((post.get("title") or {}).get("rendered") or "")))
    headline = page.get("h1") or ws(title_raw)
    published = dt.date.fromisoformat((post.get("date") or page.get("published") or today.isoformat())[:10])
    scope = scope_of(post.get("categories"))
    if not scope:
        return None, "not in a cinema or stage festival category"
    if not re.match(r"^https://(?:www\.)?programata\.bg/\S+$", post.get("link") or ""):
        return None, "no https link on programata.bg"
    dates = parse_field_dates(page.get("dates") or "")
    how = "date field"
    if not dates:
        dates = parse_title_dates(headline, published)
        how = "title"
    if not dates:
        return None, "no dates (the post's date field is empty and the title has none)"
    frm, to = dates
    if to < today.isoformat():
        return None, f"ended {to}"
    theatres = [v for v in cat.get("THEATRES", [])]
    cinemas = [v for v in cat.get("CINEMAS", [])]
    pool = (theatres if scope == "theatre" else cinemas if scope == "cinema" else theatres + cinemas)
    aliases = venue_aliases(pool)
    venues = venues_in(page.get("text", ""), aliases)
    ok, why = is_sofia(post.get("categories"), page, venues)
    if not ok:
        return None, f"not in Sofia ({why})"
    name = event_name(headline)
    eid = event_id(name, post["id"])
    titles, emph = [], page.get("emph", [])
    for phrase in emph:
        if not (2 <= len(phrase) <= 80) or len(phrase.split()) > 10 or re.fullmatch(r"[\d\s.:,–-]+", phrase):
            continue
        if re.search(_MON, phrase.lower()) or norm(phrase) == norm(name):
            continue                     # "От 17 до 24 октомври" is a date; the event's own name is no title
        if any(t["t"] == phrase for t in titles):
            continue
        titles.append({"t": phrase, "v": venues_for(page.get("text", ""), phrase, emph, aliases)})
    kind = "festival" if _FESTIVAL.search(name) or _FESTIVAL.search(headline) else "limited"
    ev = {
        "id": eid, "kind": kind, "scope": scope,
        "status": "active" if frm <= today.isoformat() else "upcoming",
        "title": name, "titleEn": latin(name),
        "from": frm, "to": to, "g": colours(eid),
        "tag": countdown_tag(frm, today),
        "source": SOURCE, "url": post.get("link") or "", "auto": True,
        "post": post["id"], "modified": post.get("modified") or "",
        "dates_from": how, "venues": venues, "titles": titles, "missed": 0,
    }
    ev["blurbBg"], ev["blurbEn"] = blurbs(ev, pool)
    return ev, None


def countdown_tag(frm, today):
    """"СЛЕД N ДНИ": evBadge() recomputes it from the dates on every render."""
    n = max(0, (dt.date.fromisoformat(frm) - today).days)
    return "СЛЕД 1 ДЕН" if n == 1 else f"СЛЕД {n} ДНИ"


def blurbs(ev, venues_pool):
    """A short factual line in each language: kind, dates, venues named in the post."""
    multi = ev["from"] != ev["to"]
    kind_bg = {"festival": "Фестивал"}.get(ev["kind"], "Поредица" if multi else "Еднократно събитие")
    kind_en = {"festival": "Festival"}.get(ev["kind"], "Series" if multi else "One-off event")
    by_id = {v["id"]: v for v in venues_pool}
    names_bg, names_en = [], []
    def plain(name):                     # "НДК — Зала 1" -> "НДК (Зала 1)": no em dash in the copy
        a, sep, b = (name or "").partition(" — ")
        return f"{a} ({b})" if sep else (name or "").replace("—", "–")
    for vid in ev.get("venues") or []:
        v = by_id.get(vid) or {}
        bg, en = plain(v.get("name")), latin(plain(v.get("nameEn") or v.get("name")))
        if bg and bg not in names_bg:
            names_bg.append(bg)
            names_en.append(en)
    bg = f"{kind_bg}, {date_words(ev['from'], ev['to'], 'bg')}."
    en = f"{kind_en}, {date_words(ev['from'], ev['to'], 'en')}."
    if names_bg:
        bg += " Сред местата: " + ", ".join(names_bg) + "."
        en += " Venues include " + ", ".join(names_en) + "."
    return bg, en


# ------------------------------------------------------------------ English titles

def latin(text):
    """English-mode text never shows Cyrillic (the app's rule, test_ui L1): any
    left is romanised with translate.latinise (Bulgaria's official system, and
    names spelt their owners' way: "Синелибри" -> "CineLibri")."""
    try:
        import translate as TR
        return TR.latinise(text or "")
    except Exception:                           # pragma: no cover - the module ships with the repo
        out = []
        for c in text or "":
            r = _ROMAN.get(c.lower())
            out.append(c if r is None else (r.capitalize() if c.isupper() else r))
        return "".join(out)


def english_titles(events, use_deepl=True):
    """titleEn from translate.py's cache (translations.json), asking DeepL for the
    missing ones when DEEPL_AUTH_KEY is set; CineLibri-style official names and
    romanisation come from translate.latinise. Without a translation the title
    is romanised ("ACT Фестивал за свободен театър" -> "ACT Festival za svoboden
    teatar"): English mode shows no Cyrillic. Returns how many were translated."""
    try:
        import translate as TR
    except Exception as e:                      # pragma: no cover - the module ships with the repo
        print(f"  (translate.py not importable: {e}; English titles romanised)")
        for ev in events:
            ev["titleEn"] = latin(ev.get("title"))
        return 0
    cache = TR.load_cache()
    work = [(e["id"], e["title"]) for e in events if TR.has_cyrillic(e["title"])]
    if use_deepl and TR.DEEPL_KEY and work:
        TR.translate_missing(work, "title", cache)
    n = 0
    for e in events:
        hit = cache.get(TR.cache_key(e["title"])) if TR.has_cyrillic(e["title"]) else None
        if hit and hit.get("en"):
            e["titleEn"] = TR.latinise(hit["en"])
            n += 1
        else:
            e["titleEn"] = TR.latinise(e["title"])
    return n


# ------------------------------------------------------------------ dedupe + membership

_GENERIC = {"фестивал", "фестивала", "фестивалът", "фест", "fest", "festival", "международен",
            "международния", "международният", "издание", "изданието", "програма", "програмата",
            "кино", "кинофестивал", "театрален", "театър", "театъра", "филм", "филмов", "филмите",
            "софия", "sofia", "в", "на", "и", "за", "с", "от", "the", "of", "and", "a"}
_ALIAS = {"cinelibri": "синелибри", "kinomania": "киномания"}


def title_key(title):
    toks = set()
    for w in norm(title).split():
        w = _ALIAS.get(w, w)
        if re.fullmatch(r"\d+[a-zа-я]*", w) or w in _GENERIC or len(w) < 2:
            continue
        toks.add(w)
    return toks


def same_event(a, b):
    """Same festival: the dates overlap and one title's words hold the other's,
    or both share a distinctive word (6+ letters: "киномания", "синелибри")."""
    if a["from"] > b["to"] or b["from"] > a["to"]:
        return False
    ka, kb = title_key(a.get("title")), title_key(b.get("title"))
    if not ka or not kb:
        return False
    if ka <= kb or kb <= ka:
        return True
    return any(len(w) >= 6 for w in ka & kb)


def _richness(e):
    return (len(e.get("venues") or []), len(e.get("titles") or []), e.get("dates_from") == "date field",
            e.get("modified") or "")


def for_page(doc, cat, today):
    """(EVENTS_AUTO, EVENT_MEMBERS, report) for one build. `doc` is events.json,
    `cat` the page's catalogue (catalogue()), `today` a date."""
    seed = cat.get("EVENTS") or []
    seed_ids = {e.get("id") for e in seed}
    report = {"ended": [], "seed": [], "merged": []}
    live = []
    for e in (doc or {}).get("events") or []:
        if (not isinstance(e, dict) or not e.get("id") or not e.get("from")
                or (e.get("to") or "") < today.isoformat()):
            report["ended"].append((e or {}).get("id"))
            continue
        live.append(e)
    # the seed wins: a seed event with the same id, or the same festival on the same dates
    kept, to_seed = [], {}
    for e in live:
        twin = next((s for s in seed if s.get("id") == e["id"] or
                     (s.get("from") and s.get("to") and same_event(e, s))), None)
        if twin:
            report["seed"].append((e["id"], twin.get("id")))
            to_seed.setdefault(twin.get("id"), []).append(e)
        else:
            kept.append(e)
    # duplicates among programata posts: keep the richer record, remember the other page
    kept.sort(key=_richness, reverse=True)
    final = []
    for e in kept:
        twin = next((f for f in final if same_event(e, f)), None)
        if twin:
            twin.setdefault("_also", []).append(e)
            report["merged"].append((e["id"], twin["id"]))
        else:
            final.append(e)
    final.sort(key=lambda e: (e["from"], e["to"], e["id"]))
    shown = []
    for e in final:
        rec = {k: e[k] for k in ("id", "kind", "scope", "status", "title", "titleEn", "from", "to",
                                 "g", "blurbBg", "blurbEn", "tag", "source", "url") if k in e}
        rec["status"] = "active" if e["from"] <= today.isoformat() else "upcoming"
        rec["titleEn"] = latin(rec.get("titleEn") or rec.get("title"))
        rec["blurbEn"] = latin(rec.get("blurbEn") or "")
        urls = [e["url"]] + [x["url"] for x in e.get("_also", []) if x.get("url") and x["url"] != e["url"]]
        if len(urls) > 1:
            rec["urls"] = urls
        rec["auto"] = True
        shown.append(rec)
    members = {}
    for e in final:
        ids = event_members(e, [e] + e.get("_also", []), cat)
        if ids:
            members[e["id"]] = ids
    for sid, recs in to_seed.items():
        s = next((x for x in seed if x.get("id") == sid), None)
        if s and s.get("from") and s.get("to"):
            ids = event_members({"from": s["from"], "to": s["to"], "scope": s.get("scope")}, recs, cat)
            if ids:
                members[sid] = ids
    for e in final:
        e.pop("_also", None)
    return shown, members, report


def event_members(ev, records, cat):
    """Film / show ids the event's posts list: exact normalised title, one
    catalogue item only, its venue named in the same sentence, and a
    screening or performance there within the event's dates."""
    scope = ev.get("scope") or "both"
    frm, to = ev["from"], ev["to"]
    shows, films = cat.get("SHOWS") or [], cat.get("FILMS") or []
    by_title = {}
    if scope in ("theatre", "both"):
        for s in shows:
            for t in {norm(s.get("title")), norm(s.get("titleEn"))} - {""}:
                by_title.setdefault(t, set()).add(("show", s["id"]))
    if scope in ("cinema", "both"):
        for f in films:
            for t in {norm(f.get("bg")), norm(f.get("en"))} - {""}:
                by_title.setdefault(t, set()).add(("film", f["id"]))
    theatre_of = {s["id"]: s.get("theatre") for s in shows}
    perf_at = {}
    for p in cat.get("PERFORMANCES") or []:
        if isinstance(p, list) and len(p) >= 2 and frm <= str(p[1]) <= to:
            perf_at.setdefault(p[0], set()).add(theatre_of.get(p[0]))
    for r in cat.get("SHOWTIMES") or []:
        if isinstance(r, list) and len(r) >= 3 and frm <= str(r[2]) <= to:
            perf_at.setdefault(r[0], set()).add(r[1])
    out = []
    for rec in records:
        for t in rec.get("titles") or []:
            hits = by_title.get(norm(t.get("t")))
            if not hits or len(hits) != 1:
                continue
            _, item = next(iter(hits))
            if set(t.get("v") or []) & (perf_at.get(item) or set()) and item not in out:
                out.append(item)
    return sorted(out)


# ------------------------------------------------------------------ the run

def merge(prev, fresh, listed, listing_ok, today, rejected=()):
    """The new event list. `prev` previous events, `fresh` {post id: event} read
    this run, `listed` the post ids this run's listing carried, `rejected` the
    posts re-read this run that no longer make an event (the source changed
    them: their dates went, or the event moved out of Sofia)."""
    out, seen = [], set()
    by_post_prev = {e.get("post"): e for e in prev if isinstance(e, dict)}
    for pid, ev in fresh.items():
        old = by_post_prev.get(pid)
        if old and old.get("id"):
            ev["id"] = old["id"]                 # the first id is kept for good
            ev["g"] = old.get("g") or ev["g"]
        out.append(ev)
        seen.add(pid)
    for e in prev:
        pid = e.get("post")
        if pid in seen or pid in rejected or (e.get("to") or "") < today.isoformat():
            continue
        e = dict(e)
        if listing_ok and pid not in listed:
            e["missed"] = int(e.get("missed") or 0) + 1
            if e["missed"] >= MISS_LIMIT:
                continue
        out.append(e)
        seen.add(pid)
    return out


def load_doc(path=OUT):
    try:
        doc = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def save_doc(doc, path=OUT):
    path = pathlib.Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def run(net, cat, today, prev_doc):
    """One live pass. Returns the new events.json document."""
    prev_events = prev_doc.get("events") or []
    posts_state = dict(prev_doc.get("posts") or {})
    oldest = today - dt.timedelta(days=LOOKBACK_DAYS)
    for e in prev_events:                # keep the posts of current events in the listing window
        try:
            pub = dt.date.fromisoformat((posts_state.get(str(e.get("post"))) or {}).get("date", "")[:10])
            oldest = min(oldest, pub - dt.timedelta(days=1))
        except ValueError:
            pass
    url = (f"{API}/posts?categories={','.join(str(c) for c in CATEGORIES)}"
           f"&after={oldest.isoformat()}T00:00:00&per_page=100&orderby=date&order=desc"
           f"&_fields=id,date,modified,link,title,categories")
    listing = net.json(url)
    if not isinstance(listing, list):
        print("  ! programata.bg listing unavailable — keeping the previous events (ended ones dropped)")
        kept = [dict(e, tag=countdown_tag(e["from"], today),
                     status="active" if e["from"] <= today.isoformat() else "upcoming")
                for e in prev_events if (e.get("to") or "") >= today.isoformat() and e.get("from")]
        return {**prev_doc, "ok": False, "events": kept,
                "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    fresh, listed, pages, rejected, unreadable = {}, set(), 0, set(), 0
    for post in listing:
        if not isinstance(post, dict) or not post.get("id"):
            continue
        pid = post["id"]
        listed.add(pid)
        st = posts_state.get(str(pid)) or {}
        old = next((e for e in prev_events if e.get("post") == pid), None)
        if (st and st.get("modified") == post.get("modified")
                and (old or not st.get("verdict", "").startswith("event"))):
            if old and (old.get("to") or "") >= today.isoformat():
                fresh[pid] = dict(old, missed=0)
            continue
        host_ok = re.match(r"^https://(?:www\.)?programata\.bg/", post.get("link") or "")
        if not host_ok:
            posts_state[str(pid)] = {"modified": post.get("modified"), "date": post.get("date"),
                                     "verdict": "skip: link not on programata.bg"}
            continue
        if pages >= MAX_PAGES:
            if old:
                fresh[pid] = dict(old, missed=0)
            continue
        pages += 1
        page_html = net.text(post["link"])
        if page_html is None:
            if old:
                fresh[pid] = dict(old, missed=0)
            continue
        page = parse_page(page_html)
        if page["dates"] is None:
            # No date field at all. Some posts use another template (an opera
            # post, Севтополис 2026); if a post that made an event loses it, the
            # page is unreadable rather than undated: keep what we had.
            unreadable += 1
            if old:
                fresh[pid] = dict(old, missed=0)
                continue
        ev, why = build_event(post, page, cat, today)
        posts_state[str(pid)] = {"modified": post.get("modified"), "date": post.get("date"),
                                 "verdict": "event" if ev else f"skip: {why}",
                                 **({"id": (old or ev)["id"]} if ev else {})}
        if ev:
            fresh[pid] = ev
        elif old:
            rejected.add(pid)
    if pages >= 3 and unreadable == pages:
        print(f"  ! none of the {pages} post pages read has the date field: has programata.bg "
              "changed its post template? Posts that made events keep them.")
    events = merge(prev_events, fresh, listed, True, today, rejected)
    for e in events:                     # date-derived fields follow the calendar
        e["tag"] = countdown_tag(e["from"], today)
        e["status"] = "active" if e["from"] <= today.isoformat() else "upcoming"
    for pid in list(posts_state):
        if int(pid) not in listed and not any(e.get("post") == int(pid) for e in events):
            del posts_state[pid]
    return {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "ok": True, "source": SOURCE, "events": events, "posts": posts_state,
            "read": {"listed": len(listed), "pages": pages, "unreadable": unreadable}}


def main(argv=None):
    argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from netfetch import Fetcher
    today = sofia_today()
    cat = catalogue(HTML.read_text(encoding="utf-8"))
    prev = load_doc()
    net = Fetcher(host_delay=HOST_DELAY, budget_seconds=RUN_BUDGET, attempts=3)
    try:
        with net.deadline(RUN_BUDGET):
            doc = run(net, cat, today, prev)
    except Exception as e:                               # keep-previous: never write a half result
        print(f"  ! fetch_events failed ({e!r}); events.json left as it was")
        net.print_report()
        return 0
    try:
        english_titles(doc["events"])
    except Exception as e:                               # a translation problem never costs the events
        print(f"  ! English titles not updated ({e!r}); titles romanised")
        for ev in doc["events"]:
            ev["titleEn"] = latin(ev.get("titleEn") or ev.get("title"))
    save_doc(doc)
    shown, members, rep = for_page(doc, cat, today)
    print(f"events.json: {len(doc['events'])} programata event(s), listing "
          f"{'ok' if doc.get('ok') else 'UNAVAILABLE (previous kept)'}; "
          f"{len(shown)} for the page, {len(rep['seed'])} already in the seed, "
          f"{len(rep['merged'])} merged, {sum(len(v) for v in members.values())} member title(s)")
    for e in shown:
        print(f"  {e['from']}..{e['to']}  {e['scope']:7s} {e['id']:44s} {e['title'][:50]}")
    for a, s in rep["seed"]:
        print(f"  = {a} is the seed's {s} (the seed wins)")
    skips = [(pid, st.get("verdict")) for pid, st in (doc.get("posts") or {}).items()
             if str(st.get("verdict", "")).startswith("skip")]
    for pid, why in skips[:30]:
        print(f"  - post {pid}: {why}")
    net.print_report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
