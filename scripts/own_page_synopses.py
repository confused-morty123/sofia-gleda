#!/usr/bin/env python3
"""Sofia Gleda — play synopses read from each play's OWN production page (owner
spec 2026-11-09), the page own_page_posters.py already reads for its poster.

Which pages. Every listed play whose shown Bulgarian synopsis is shorter than
synopses.THIN characters (or is this route's own earlier fill, due for a re-read)
and whose LINKS entry is its production page. own_page_posters.own_page decides the
page: ticket pages are not read; mlt.bg (Cloudflare challenge) and sofiapuppet.com
(robots.txt) are never read, their production's page on theatre.art.bg is, with
synopses.parse_art_production's own checks, and such a fill is recorded as what it
is: source "theatre.art.bg".

What is accepted (positive evidence only; a missing text is fine, a wrong one is not):

  * the page is the play's: the request did not end on another page
    (own_page_posters.moved_page), its canonical is not the site's root
    (root_canonical) and its own headings name the title (page_identity);
  * the text comes from the page's description block: a per-host container
    (HOST_BLOCKS, each checked on two real pages, 2026-11-11) or, on any other
    host, the page's one main content block (own_page_posters.main_content) below
    the heading that names the title. Related / teaser blocks
    (own_page_posters.related_blocks), navigation, footers, cookie bars, comments,
    galleries and ticket boxes are dropped first. Never the whole page;
  * inside it, the LEAD: consecutive prose paragraphs. Cast and crew lines, dates,
    times, tickets, prices, running time, age notes, awards and funding lines are
    not prose: before the lead they are skipped, after it they end it. A section
    heading ends it too, and nothing is read under a heading such as "Участват",
    "Отзиви", "Награди", "Вижте още". A biography, an interview, a review or a
    paragraph whose subject is another listed production ends the reading for good;
  * checked by reading the live sample of 2026-11-11 and tightened where it let
    something through: a signature under the text ("Елза Лалева - режисьор"), a
    casting note ("*В ролята на Дара се редуват …"), an age note in brackets, the
    cast and crew written as one sentence, crew named with their crafts, a quoted
    statement with its speaker ("„…“ – сподели авторът"), a sentence about the
    author ("… е сред най-поставяните съвременни автори"), the production's prizes
    and tours year by year and a dated call to come ("Гледайте … през месец март")
    are not description; nor is a fragment of the source text that opens with an
    ellipsis, or a first line that starts in lower case (the rest of a broken line);
  * the result is Bulgarian (>= 70 % Cyrillic letters), at least synopses.MIN_FILL
    (120) characters, and carries no "€" glued to a Cyrillic letter (a site-side
    "лв" -> "€" replacement that cut through words, 2026-11-09).

Load. One request per page, through own_page_posters.Net (GET only, robots.txt
obeyed, >= 2 s between two requests to one host), hosts taken in turn. A page read
here is kept in the run's page cache (own_page_posters.RUN_CACHE_ENV) for the poster
steps, so a theatre is asked for a page once a night. Per run at most MAX_REQUESTS
requests, MAX_PER_HOST pages of one host and BUDGET_S seconds: the backlog fills
over a few nights, never-read pages first. A filled play is read again after
synopses.RECHECK_DAYS (14) days, a page that gave nothing after RETRY_DAYS (7).

Each fill is stored in synopsis_fills.json as
    {"bg", "source": "own-page", "source_url", "fetched_on", "title", "theatre"}
and a miss as {"source", "source_url", "tried_on", "why", "title"}. synopses.apply
puts them on the page: owner override > own page > theatre.art.bg > the seed text,
only over a shown text shorter than THIN. English is not produced here
(translate.py, bounded per night).

    python3 scripts/synopses.py --apply     # runs this route inside the synopses step
"""
from __future__ import annotations
import datetime as dt, re, sys, pathlib, urllib.parse
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import synopses as SY                                              # noqa: E402

SOURCE = "own-page"
ART_SOURCE = "theatre.art.bg"
MAX_CHARS = 1500            # whole paragraphs up to about this many characters
LEAD_MIN = 50               # the first paragraph of a lead is at least this long (a sentence)
PARA_MIN = 40               # a later paragraph shorter than this ends the lead
CYR_MIN = 0.70
MAX_REQUESTS = 120          # per run: pages + robots.txt (poster_fix.py: 160)
MAX_PER_HOST = 20           # pages of one host per run
BUDGET_S = 240              # seconds of network time per run
RETRY_DAYS = 7              # a page that gave nothing is tried again after this many days

# The description container of each theatre's production page (CSS; every match is
# tried in page order and the first that gives a lead wins). Small hints only: the
# reading inside them is the same for every host. Checked on two real pages each.
HOST_BLOCKS = {
    "satirata.bg": ["div.show-details"],
    "theatrevazrajdane.bg": ["article.portfolio_page"],
    "toplocentrala.bg": ["div.program-description-cms"],
    "natfiz.bg": ["div.single-event-detail div.event-content"],
    "sfumato.info": ["div.play-details-col"],
    "nationaltheatre.bg": ["section#synopsis"],
    "iamstudio.bg": ["div.woocommerce-product-details__short-description"],
    "salzaismyah.bg": ["div.mr-md-3"],
    "artvent.bg": ["section#main-events div.m-m-0"],
    "zadkanala.bg": ["div.field-name-body div.field-item"],
    "tba.art.bg": ["div.text-content"],
    "theatre199.org": ["article.page-headings-wrapper"],
    "sofiatheatre.eu": ["div.repertoire-info div.pb-3"],
}
ART_HOSTS = {"theatre.art.bg"}

_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_DROP_SEL = "script, style, noscript, nav, footer, header, form, iframe, button, ul.menu, table, select, aside"
_DROP_CLASS_RE = re.compile(r"cookie|popup|modal|subscribe|newsletter|share|social|related|teaser|"
                            r"slider|carousel|swiper|breadcrumb|comment|coment|gallery|galler|ticket|"
                            r"remodal|sidebar|widget", re.I)
_BLOCK_TAGS = {"p", "div", "li", "br", "section", "article", "blockquote", "tr", "dd", "dt", "ul", "ol"}
HEAD = "\x00"               # blocks() marks a heading's text with this prefix

# ------------------------------------------------------------------ what a block is
_CREDIT_WORD = (r"(?:режис\w*|сценограф\w*|сценография|костюм\w*|музика\w*|композитор\w*|превод\w*|"
                r"драматург\w*|хореограф\w*|участват|участие\w*|ролите|изпълняват|актьор\w*|"
                r"помощник\w*|осветление|светлинн\w*|постанов\w*|автор\w*|текст|адаптация|"
                r"продуцент\w*|асистент\w*|сценична версия|видео\w*|звук\w*|кукли|пластика|"
                r"художник\w*|фотограф\w*|диригент\w*|концепция|идея|словесно действие|вокали|бенд|"
                r"сценарий|екип\w*|мултимедия|плакат\w*|дизайн\w*|разпространение|реклама|съставител\w*|"
                r"корепетитор\w*|консултант\w*|грим\w*|реквизит\w*|суфльор\w*|сценичн\w+ бой|вокал\w*)")
CREDIT_START_RE = re.compile(r"^\W*(?:от\s+[А-ЯA-Z]|по\s+[А-ЯA-Z„\"“«]|с\s+участието|жанр\b|" + _CREDIT_WORD + r"\b)", re.I)
CREDIT_ANY_RE = re.compile(r"\b" + _CREDIT_WORD + r"\b", re.I)
# "Г-н Боман – ЯВОР БОРИСОВ", "Иван – Георги Георгиев": a role and its actor
ROLE_RE = re.compile(r"^[^.!?…]{1,60}\s[-–—]\s+[А-ЯЁA-Z][\w.\-]+(?:\s+[А-ЯЁA-Zи][\w.\-]*){0,5}\W*$")
# "Елза Лалева - режисьор", "Иван Иванов, сценограф": a signature under the text, or a
# name with its craft inside a sentence ("… са Ана Петрова – сценограф и Петър Петров – композитор")
_NAME_ROLE = r"[А-ЯЁA-Z][\w.\-]+(?:\s+[А-ЯЁA-Z][\w.\-]+){1,3}\s*[-–—,]\s*" + _CREDIT_WORD + r"\b"
SIGN_RE = re.compile(r"^\W*" + _NAME_ROLE + r"\W*$")
NAME_ROLE_RE = re.compile(_NAME_ROLE)
# "*В ролята на Дара се редуват актрисите …": a casting note
CAST_NOTE_RE = re.compile(r"^\W*в\s+рол(?:ята|ите)\b|\bсе\s+редуват\b|\bдублира\w*\b", re.I)

# dates, times, tickets, prices, running time, age notes, house rules, funding, awards
BOILER_START_RE = re.compile(
    r"^\W*(?:билет\w*|цена|цени|продължителност|времетраене|премиера|антракт|възраст\w*|подходящ\w*|"
    r"награ\w*|номинац\w*|носител\w*|лауреат\w*|аскеер|икар\b|повече|прочети|виж\w*|гледайте (?:още|и|също)|бисквит\w*|"
    r"работно време|каса\w*|резерв\w*|купи\w*|абонамент\w*|галерия|снимки|снимка|партньор\w*|спонсор\w*|"
    r"с подкрепата|с любезното|със съдействието|проект\w*|спектакълът се играе|постановката ще се играе|"
    r"представлението се играе|дата|час|място|зала|сцена|уважаеми|скъпи зрители|моля|препоръчваме|внимание|"
    r"важно|след представлението|преди представлението|очаквайте|заповядайте|вход\w*)\b|"
    r"^\W*(?:\d{1,2}\s*(?:и\s*\d{1,2}\s*)?(?:януари|февруари|март|април|май|юни|юли|август|септември|октомври|ноември|декември)|"
    r"\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)", re.I)
BOILER_ANY_RE = re.compile(
    r"бисквитк|cookies|\bлв\b\.?|€|\beur\b|работно време|продължителност|времетраене|антракт|"
    r"\d{1,2}[.:]\d{2}\s*ч|\bот\s+\d{1,2}[.:]\d{2}\b|\+\s?\d{1,2}\b|\bбилет|\bкаса(?:та)?\b|epay|eventim|"
    r"не се препоръчва|не е подходящ|неподходящ|подходящ\w* за (?:деца|зрители|лица)|(?:лица|деца|зрители) (?:под|над) \d|"
    r"\(\s*(?:над|под|от|за)\s+\d{1,2}\s*(?:\+|г\.?|год\w*)|"
    r"\d+\s*(?:\+|г\.|години)\s*$|не се допускат|закъснели|мобилни\w* телефон|"
    r"финансов\w+ подкрепа|с подкрепата на|национален фонд|министерство\w* на културата|столична община|"
    r"европейск\w+ съюз|nextgeneration|план за възстановяване|се реализира (?:по|с|в рамките)|съфинансира|"
    r"програма\w* [„\"“]?култура|с любезното съдействие|медиен партньор|"
    r"ще бъде проведена дискусия|след (?:представлението|спектакъла) ще", re.I)
AWARD_RE = re.compile(r"номинац|номинир|аскеер|икар\b|награда\w*|награди\w*|наградата|отличи\w*|"
                      r"\bприз(?:ът|а|ове|овете)?\b|носител\w*|лауреат\w*", re.I)
_MONTHS = r"(?:януари|февруари|март|април|май|юни|юли|август|септември|октомври|ноември|декември)"
# "Гледайте премиерните представления … през месец март": a dated call to come, stale within weeks
CALL_RE = re.compile(r"^\W*(?:гледайте|очаквайте|заповядайте|елате|не пропускайте|побързайте)\b.*"
                     r"(?:\bмесец\b|\b" + _MONTHS + r"\b|премиер|\d{1,2}[./]\d{1,2})", re.I | re.S)
_YEAR_RE = re.compile(r"\b(?:19|20)\d\d\b")
PROMO_RE = re.compile(r"\bvip\b|meet & greet|промоцион|отстъпк|[\U0001F300-\U0001FAFF☀-➿]", re.I)

# nothing is read under these headings (a cast / crew heading is not one of them:
# Сатирата prints the description in the same column, below the credits)
SKIP_HEAD_RE = re.compile(
    r"^\W*(?:отзиви|ревюта|мнения|коментари|пресата|медиите|критиката|награди|номинации|отличия|"
    r"вижте още|виж още|още от|други|подобни|галерия|фотоалбум|фотогалерия|видео\w*|снимки|билети|програма|"
    r"дати|предстоящи|календар|контакти|адрес|интервю\w*|за автора|за режисьора|за авторите|биография|"
    r"януари|февруари|март|април|май|юни|юли|август|септември|октомври|ноември|декември|"
    r"влезте|регистрация|търсене|форма за|медии|новини)", re.I)
# the reading is over for good at a block like these
INTERVIEW_RE = re.compile(r"интервю|^\s*[-–—]\s", re.I)
REVIEW_RE = re.compile(r"^\W*(?:отзиви|из рецензии|пресата за|медиите за|критиката за|зрители\w* за)\b", re.I)
# "Ерик-Еманюел Шмит е френски драматург", "ЕЙПРИЛ ДЕ АНЖЕЛИС  е сред …", "Завършва …"
_NAME = r"[А-ЯЁA-Z][\w.\-]+(?:[ \-]+(?:(?:де|ди|дю|фон|ван|дер|ла|ле|о|и)\s+)?[А-ЯЁA-Z][\w.\-]+){1,3}"
_BIO_WORD = (r"(?:драматург\w*|режисьор\w*|писател\w*|актьор\w*|актрис\w*|поет\w*|хореограф\w*|композитор\w*|"
             r"автор\w*|сценарист\w*|роден\w*|сред|носител\w*|лауреат\w*|един от|една от|възпитани\w*|"
             r"професор\w*|преподавател\w*|романист\w*|есеист\w*|музикант\w*|певец|певица|художник\w*)")
BIO_RE = re.compile(r"^\W*" + _NAME + r"\s*,?\s+(?:е|e|са)\s+(?:[\w\-]+,?\s+){0,4}?" + _BIO_WORD + r"|"
                    r"^\W*(?:е|e)\s+(?:[\w\-]+,?\s+){0,4}?" + _BIO_WORD + r"|"
                    r"^\W*(?:[Рр]оден|[Рр]одена|[Зз]авършва|[Зз]авършил|[Зз]авършила)\b|[Зз]авършва НАТФИЗ|"
                    r"^\W*(?:Той|Тя|Те)\s+(?:е|e|са)\s+(?:[\w\-]+,?\s+){0,6}?" + _BIO_WORD)
# "… е сред най-поставяните съвременни руски автори", "… са едни от най-успешните тандеми
# в новата френска драматургия": a sentence about the author, wherever it stands in the paragraph
BIO_ANY_RE = re.compile(r"\b(?:е|e|са)\s+(?:сред|(?:един|една|едни|едно)\s+от)\s+най-[\w\-]+(?:\s+[\w\-,]+){0,8}?\s+"
                        r"(?:драматур\w*|автор\w*|писател\w*|тандем\w*|режисьор\w*|поет\w*|композитор\w*|творци)", re.I)
# A site-side "лв" -> "€" replacement that cut through words ("допъ€ат" for "допълват"):
# the text is corrupted at its source, never copied.
CORRUPT_RE = re.compile(r"[а-яА-Я]€|€[а-яА-Я]")
_PROD_NOUN = (r"(?:спектакъл\w*|представлени\w+|постановк\w+|пиес\w+|комеди\w+|драма\w*|мюзикъл\w*|"
              r"моноспектакъл\w*|шоу\w*|опер\w+|балет\w*|концерт\w*)")
_OPEN_Q, _CLOSE_Q = "„\"“«,'", "“”\"»'"


def _host(url):
    return (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")


def cyr_ratio(s):
    letters = [c for c in s if c.isalpha()]
    return sum("Ѐ" <= c <= "ӿ" for c in letters) / len(letters) if letters else 0.0


def _clean(t):
    return re.sub(r"\s+", " ", t.replace("\xa0", " ")).strip()


def blocks(el):
    """The container's text split at block-level tags and <br>, in page order; a
    heading's text comes as one block prefixed with HEAD."""
    out, cur = [], []

    def flush():
        t = _clean("".join(cur))
        if t:
            out.append(t)
        cur.clear()

    def walk(node):
        for ch in node.children:
            name = getattr(ch, "name", None)
            if name is None:
                cur.append(str(ch))
            elif name in _HEADINGS:
                flush()
                t = _clean(ch.get_text(" ", strip=True))
                if t:
                    out.append(HEAD + t)
            elif name in _BLOCK_TAGS:
                flush()
                walk(ch)
                flush()
            else:
                walk(ch)
    walk(el)
    flush()
    return out


def is_credit(b):
    """A cast / crew line or list: "режисьор Х", "Иван – Георги Георгиев", a run of names."""
    if CREDIT_START_RE.search(b) or (len(b) < 90 and ROLE_RE.search(b)):
        return True
    if SIGN_RE.search(b) or len(NAME_ROLE_RE.findall(b)) >= 2 or (len(b) < 160 and CAST_NOTE_RE.search(b)):
        return True                                  # a signature, crew named with their crafts, a casting note
    if len(b) < 600 and len(_PERSON_RE.findall(b)) >= 5 and len(CREDIT_ANY_RE.findall(b)) >= 2:
        return True                                  # the cast and crew written as one sentence
    if len(b) < 60 and b.rstrip().endswith(":"):
        return True                                  # a label: "Участват:"
    hits = len(CREDIT_ANY_RE.findall(b))
    seps = len(re.findall(r"\s[-–—]\s|:", b))
    if hits >= 2 and seps >= 2 and len(b) < 300:
        return True
    words = re.findall(r"[^\W\d_]+", b)
    caps = sum(w[0].isupper() for w in words)
    return len(words) >= 4 and caps / len(words) > 0.6 and not re.search(r"[.!?…]\s", b)


def is_boiler(b):
    """Dates, times, tickets, prices, running time, age notes, house rules, funding
    and awards lines: never part of a description."""
    if BOILER_START_RE.search(b) or PROMO_RE.search(b) and len(b) < 400:
        return True
    if BOILER_ANY_RE.search(b) and len(b) < 400:
        return True
    if len(b) < 300 and CALL_RE.search(b):
        return True
    if len(b) < 600 and AWARD_RE.search(b) and len(set(_YEAR_RE.findall(b))) >= 2:
        return True                                  # the production's prizes and tours, year by year
    return bool(len(b) < 300 and AWARD_RE.search(b) and re.search(r"\b(?:19|20)\d\d\b|^\W*(?:награ|номинац|носител)", b, re.I))


_PERSON_RE = re.compile(r"[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z]+(?:[ \-]+[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z]+)+")
_ABOUT_PERSON_RE = re.compile(r"^\W*(" + _NAME + r")\s*,?\s+(?:е|e|са|бе|беше)\s")


def people_of(credit):
    """The names in a credits line ("Автор: Щефан Фьогел" -> {"щефан фьогел"})."""
    return {SY.norm(m.group(0)) for m in _PERSON_RE.finditer(credit)}


def is_bio(b, people=()):
    """A person's biography: "Ерик-Еманюел Шмит е френски драматург …", "Завършва
    НАТФИЗ …", or a paragraph that opens with "<a name from the page's credits> е …"
    ("Щефан Фьогел е вече познат у нас": the author, not a character)."""
    if BIO_RE.search(b) or BIO_ANY_RE.search(b):
        return True
    m = _ABOUT_PERSON_RE.match(b)
    return bool(m and people and SY.norm(m.group(1)) in people)


def is_prose(b, first=False):
    if len(b) < (LEAD_MIN if first else PARA_MIN) or cyr_ratio(b) < CYR_MIN:
        return False
    return bool(re.search(r"[.!?…]\W*$|[.!?…]\s", b)) or len(b) >= 150


def whole_quote(b):
    """True when the whole block is ONE quotation: it opens with a quote mark that
    closes only at its very end (inner „…“ pairs are fine). A critic's or the
    author's words standing alone are not the theatre's description (Сфумато,
    2026-11-11: an essay passage about Бекет)."""
    b = b.strip().rstrip(" .…!?")
    if len(b) < 2 or b[0] not in "„“«\"'‘" or b[-1] not in "“”»\"'’":
        return False
    depth = 0
    for i, ch in enumerate(b):
        if ch in "„«‘":
            depth += 1
        elif ch in "”»’":
            depth -= 1
        elif ch == "“":
            depth += 1 if i == 0 else -1
        elif ch in "\"'" and (i == 0 or i == len(b) - 1 or not (b[i - 1].isalpha() and b[i + 1].isalpha())):
            depth += 1 if depth == 0 else -1
        if depth <= 0 and i < len(b) - 1:
            return False
    return True


def is_epigraph(b):
    """A quotation standing alone (an epigraph, a line from the play, somebody's
    words about it): it opens with a quote mark and either never closes it or
    closes it at the very end (whole_quote); or a speaker and a quoted line
    ("Офелия: „…“")."""
    b = b.strip()
    if re.match(r"^[А-ЯЁA-Z][\w.\- ]{1,30}:\s*[„\"“«]", b) or whole_quote(b):
        return True
    if re.match(r"^(?:…|\.\.\.)", b):
        return True                                  # a fragment of the source text ("… Да бъде ясно твоето небе")
    if b[:1] in "„“«\"" and SAID_RE.search(b):
        return True                                  # „…“ – сподели авторът: somebody's words, not a description
    if not b or b[0] not in _OPEN_Q:
        return False
    rest = b.lstrip(_OPEN_Q)
    hits = [i for i in (rest.find(c) for c in _CLOSE_Q) if i >= 0]
    return not hits or min(hits) >= len(rest.rstrip(" .…!?")) - 1


SAID_RE = re.compile(r"[“”\"»]\s*[-–—,]\s*(?:сподел\w+|каз\w+|разказ\w+|коментира\w*|пише|отбеляз\w+|обясн\w+|"
                     r"допъл\w+|призна\w+)\b", re.I)
_QUOTED_START_RE = re.compile(r"^\s*[„\"“«'‘]\s*([^“”\"»'’]{2,80})[“”\"»'’]")


def about_other(b, title, other_titles):
    """True when the paragraph's SUBJECT is another listed production: it opens with
    that production's title (a title of two or more words; a one-word title only
    when the paragraph opens with it in quotation marks: "Театър Вилекула" is not
    about the play "Театър"), or names it right after a word such as "спектакъла" /
    "постановката" (not "по пиесата …": the source of an adaptation) while this
    play's own title is not in the paragraph. A title merely mentioned in passing
    ("да изиграят Дон Кихот и Санчо Панса") is not a subject."""
    nb, mine = SY.norm(b), SY.norm(title)
    if not nb:
        return False
    padded = f" {nb} "
    has_mine = bool(mine) and f" {mine} " in padded
    q = _QUOTED_START_RE.match(b)
    quoted = SY.norm(q.group(1)) if q else ""
    for o in other_titles:
        if not o or o == mine or (len(o) < 4 and " " not in o):
            continue
        if o in mine or (mine and mine in o):
            continue                                 # a shared name ("Хамлет" / "Хамлет. Машина")
        multi = len(o) >= 8 and " " in o
        if (nb == o or nb.startswith(o + " ")) and (multi or quoted == o):
            if not (has_mine and nb.startswith(mine)):
                return True
        if has_mine or not multi or f" {o} " not in padded:
            continue
        if re.search(r"(?<!по )(?<!по едноименната )\b" + _PROD_NOUN + r"\s+" + re.escape(o) + r"\b", nb):
            return True
    return False


# "Анотация :" — the label some pages print above a description typeset line by line
ANNOT_RE = re.compile(r"^\W*(?:анотация|описание|за спектакъла|за представлението|за пиесата|синопсис|сюжет|резюме)\W*$", re.I)
LINE_MAX = 90


def join_lines(bl):
    """The blocks with the short lines under an "Анотация" label joined into
    paragraphs (Сълза и смях breaks its descriptions with <br> every few words);
    the label itself is dropped. Credits and practical lines are never joined."""
    out, run, on = [], [], False

    def flush():
        if run:
            out.append(" ".join(run))
            run.clear()

    for b in bl:
        if b.startswith(HEAD):
            flush()
            on = bool(ANNOT_RE.match(b[len(HEAD):]))
            out.append(b)
        elif ANNOT_RE.match(b):
            flush()
            on = True
        elif on and len(b) < LINE_MAX and not is_boiler(b) and not is_credit(b):
            run.append(b)
        else:
            flush()
            out.append(b)
    flush()
    return out


def lead(bl, title, other_titles=()):
    """(the lead description, why not) from a container's blocks."""
    t = SY.norm(title)
    out, n, skipping, why = [], 0, False, "no description paragraph"
    pend = []                                        # short lines since the last paragraph
    stub = 0                                         # the longest lead that stayed under MIN_FILL
    people = set()                                   # the names in the credits lines read so far

    def done():
        return "\n\n".join(out)

    for b in join_lines(bl):
        if b.startswith(HEAD):                       # a heading
            h = b[len(HEAD):]
            if SY.norm(h) == t or not SY.norm(h):
                continue
            if n >= SY.MIN_FILL:
                return done(), ""
            stub = max(stub, n)
            out, n, pend = [], 0, []                 # a stub above a heading is not a description
            skipping = bool(SKIP_HEAD_RE.search(h))
            if REVIEW_RE.search(h) or "интервю" in h.lower():
                return "", "reviews / interview only"
            continue
        if skipping:
            continue
        b = re.sub(r"^(?:more|read more|прочети още|виж повече|повече)\b[ \t]*", "", b, flags=re.I)
        if SY.norm(b) in ("", t):
            if out and not SY.norm(b) and len(pend) < 2:
                pend.append("")                      # a separator ("*", "---") inside the text
            continue
        if about_other(b, title, other_titles):
            return (done(), "") if n >= SY.MIN_FILL else ("", "the text is about another listed production")
        if is_bio(b, people) or REVIEW_RE.search(b) or (INTERVIEW_RE.search(b) and len(b) < 200):
            return (done(), "") if n >= SY.MIN_FILL else ("", "biography / interview / reviews, no description before it")
        dull = is_boiler(b) or is_credit(b)
        if dull:
            why = "cast / credits / practical lines only"
            if len(b) < 300:
                people |= people_of(b)
        if not out:                                  # before the lead: credits, dates, labels, an epigraph
            if dull or is_epigraph(b) or not is_prose(b, first=True) or b[:1].islower():
                continue                             # (a lower-case start: the rest of a line broken above, not a paragraph)
            out, n = [b], len(b)
            continue
        if not dull and not is_epigraph(b) and not is_prose(b) and len(pend) < 2 \
                and len(b) < LINE_MAX and cyr_ratio(b) >= CYR_MIN:
            pend.append(b)                           # a tagline between two paragraphs: kept if the text goes on
            continue
        if dull or is_epigraph(b) or not is_prose(b) or n + len(b) + sum(map(len, pend)) > MAX_CHARS:
            if n >= SY.MIN_FILL:
                return done(), ""
            stub = max(stub, n)
            out, n, pend = [], 0, []                 # a stub before a credits block: look further
            if not dull and not is_epigraph(b) and is_prose(b, first=True):
                out, n = [b], len(b)
            continue
        out += [x for x in pend if x] + [b]
        n += sum(map(len, pend)) + len(b)
        pend = []
    if n >= SY.MIN_FILL:
        return done(), ""
    return "", (f"description under {SY.MIN_FILL} characters" if max(stub, n) else why)


def _strip(c, base, other_titles=()):
    """The container without navigation, forms, cookie bars, comments, galleries,
    ticket boxes and the page's related / teaser blocks."""
    import own_page_posters as O
    drop = list(c.select(_DROP_SEL))
    drop += [el for el in c.find_all(True)
             if _DROP_CLASS_RE.search(" ".join(el.get("class") or []) + " " + (el.get("id") or ""))]
    try:
        drop += O.related_blocks(c, base, other_titles)
    except Exception:                                # noqa: BLE001  (a page bs4 cannot walk: nothing extra dropped)
        pass
    for el in drop:
        try:
            el.decompose()
        except Exception:                            # noqa: BLE001  (inside an already dropped one)
            pass
    return c


def _below_title(bl, title):
    """The blocks after the heading that names the title (the generic route reads
    the main block below it); all of them when no heading there names it."""
    t = SY.norm(title)
    for i, b in enumerate(bl):
        if b.startswith(HEAD) and t and (SY.norm(b[1:]) == t or f" {t} " in f" {SY.norm(b[1:])} "):
            return bl[i + 1:]
    return bl


def containers(soup, html, url):
    """([description containers], hinted?) of the page."""
    found = []
    for sel in HOST_BLOCKS.get(_host(url)) or []:
        found += soup.select(sel)
    if found:
        return found, True
    import own_page_posters as O
    mc = O.main_content(html)
    if mc is None:
        return [], False
    from bs4 import BeautifulSoup
    return [BeautifulSoup(mc, "lxml")], False


def read_page(html, url, title, theatre_names=(), other_titles=(), final_url=None):
    """(text, "") — the play's own description from its own page — or (None, why)."""
    import own_page_posters as O
    if not html:
        return None, "empty page"
    if final_url and O.moved_page(url, final_url):
        return None, "the request ended on another page"
    if O.root_canonical(html, url):
        return None, "the site's home page is served at this address"
    if _host(url) in ART_HOSTS:
        # the theatre.art.bg page of a production whose own site is not read: its own
        # title and theatre checks; the lead in <p>, or (Младежки's pages) in plain
        # <div>s, always above the "Повече" block (#myanons: awards, quotes, duration)
        text, why = SY.parse_art_production(html, title, list(theatre_names)), ""
        if not text:
            anons = SY.art_anons(html, title, list(theatre_names))
            if anons is None:
                return None, "theatre.art.bg: the page's title / theatre is not this play's"
            for el in anons.select("#myanons"):
                el.decompose()
            text, why = lead(blocks(_strip(anons, url, other_titles)), title, other_titles)
    else:
        if not O.page_identity(html, title):
            return None, "the page's headings do not name the play"
        from bs4 import BeautifulSoup
        found, hinted = containers(BeautifulSoup(html, "lxml"), html, url)
        if not found:
            return None, "no description block on the page"
        text, why = "", ""
        for c in found:
            bl = blocks(_strip(c, url, other_titles))
            text, w = lead(bl if hinted else _below_title(bl, title), title, other_titles)
            why = why or w
            if text:
                break
        if not text:
            return None, why or "no description paragraph"
    if not text or SY.slen(text) < SY.MIN_FILL:
        return None, why or f"description under {SY.MIN_FILL} characters"
    if cyr_ratio(text) < CYR_MIN:
        return None, "the text is not Bulgarian"
    if CORRUPT_RE.search(text):
        return None, "the text is corrupted on the page (€ inside a word)"
    return text, ""


def parse_own_page(html, url, title, theatre_names=(), other_titles=(), final_url=None):
    """read_page's text, or None."""
    return read_page(html, url, title, theatre_names, other_titles, final_url)[0]


# ---------------------------------------------------------------------- schedule
def due(rec, url, today, source=SOURCE):
    """Read this page now? Never-read: yes. A fill of this route: RECHECK_DAYS after
    it was fetched (and RETRY_DAYS after a re-read that gave nothing). A miss:
    RETRY_DAYS after it was tried. Another route's record does not count, except
    for its note of this page's last miss ("own_tried")."""
    def age(v):
        try:
            return (today - dt.date.fromisoformat(v)).days
        except (TypeError, ValueError):
            return None
    if not rec:
        return True
    if rec.get("source_url") != url or (rec.get("source") or ART_SOURCE) != source:
        t = rec.get("own_tried") or {}
        return not (t.get("url") == url and age(t.get("on")) is not None and age(t.get("on")) < RETRY_DAYS)
    tried = age(rec.get("tried_on"))
    if rec.get("bg"):
        fetched = age(rec.get("fetched_on"))
        return fetched is None or (fetched >= SY.RECHECK_DAYS and (tried is None or tried >= RETRY_DAYS))
    return tried is None or tried >= RETRY_DAYS


def candidates(src, fills=None, thin=SY.THIN):
    """[(showId, title, [theatre names], page url, theatre id)] — listed plays whose
    shown synopsis is thin, or is this route's own fill (so its page is re-read on
    schedule), and whose LINKS entry is a production page of their own (a
    theatre.art.bg LINKS entry is synopses.py's route)."""
    import own_page_posters as O
    fills = fills or {}
    shows = {s["id"]: s for s in (SY._const(src, "SHOWS") or [])}
    up = {r[0] for r in (SY._const(src, "PERFORMANCES") or []) if r}
    names = {t["id"]: t.get("name") or "" for t in (SY._const(src, "THEATRES") or [])}
    agg = SY._const(src, "AGGLINKS") or {}
    natfiz = {}
    for r in sorted(r for r in (SY._const(src, "PLINKS") or []) if isinstance(r, list) and len(r) >= 4):
        if isinstance(r[3], str) and O.NATFIZ_EVENT_RE.match(r[3]):
            natfiz.setdefault(r[0], r[3])
    out, seen = [], set()
    for row in SY._const(src, "LINKS") or []:
        if not (isinstance(row, list) and len(row) == 2):
            continue
        sid, url = row
        sh = shows.get(sid)
        if not sh or sid not in up or sid in agg or sid in seen:
            continue
        rec = fills.get(sid) or {}
        own_fill = bool(rec.get("bg")) and rec.get("source") == SOURCE and sh.get("synBg") == rec["bg"]
        if SY.slen(sh.get("synBg")) >= thin and not own_fill:
            continue
        if not isinstance(url, str) or _host(url) in ART_HOSTS:
            continue
        if url.rstrip("/") == O.NATFIZ_LIST.rstrip("/"):
            url = natfiz.get(sid)
            if not url:
                continue
        page, _ = O.own_page(url)
        if not page:
            continue
        th = sh.get("theatre")
        seen.add(sid)
        out.append((sid, sh.get("title") or "",
                    [n for n in (names.get(th), SY.ART_VENUE_NAMES.get(th)) if n], page, th or "?"))
    return out


def plan(cands, fills, today, max_per_host=MAX_PER_HOST):
    """The pages to read this run, in order: per host the never-read ones first,
    then misses and fills by the age of their last read; hosts taken in turn (one
    page of each, then the next of each), at most max_per_host of one host."""
    per = defaultdict(list)
    for c in cands:
        sid, _title, _th, url = c[:4]
        rec = fills.get(sid) or {}
        src = ART_SOURCE if _host(url) in ART_HOSTS else SOURCE
        if rec.get("bg") and (rec.get("source") or ART_SOURCE) != src and src == ART_SOURCE:
            continue                                 # the play's own page already gave its text
        if not due(rec, url, today, src):
            continue
        mine = rec.get("source_url") == url and (rec.get("source") or ART_SOURCE) == src
        last = (rec.get("tried_on") or rec.get("fetched_on") or "") if mine else ""
        per[_host(url)].append(((1 if last else 0, last, sid), c))
    queues = [[c for _, c in sorted(v, key=lambda x: x[0])][:max_per_host] for _, v in sorted(per.items())]
    out = []
    for i in range(max((len(q) for q in queues), default=0)):
        out += [q[i] for q in queues if len(q) > i]
    return out


NOT_READ = "page not read (robots.txt, an HTTP error or no answer)"


def _miss(fills, sid, rec, source, url, today, why, title):
    """Note that `url` gave no text today, so that it is not asked again before
    RETRY_DAYS. Keep-previous: a text read earlier (this page's, or another
    route's) stays where it is."""
    if rec.get("bg"):
        if rec.get("source_url") == url and (rec.get("source") or ART_SOURCE) == source:
            rec["tried_on"], rec["why"] = today.isoformat(), why
        else:
            rec["own_tried"] = {"url": url, "on": today.isoformat(), "why": why}
    else:
        fills[sid] = {"source": source, "source_url": url, "tried_on": today.isoformat(),
                      "why": why, "title": title}


def fetch(src, fills, net, today, decode, verbose=False, max_per_host=MAX_PER_HOST):
    """Read the due own pages; write fills (keep-previous). Returns stats, with
    stats["by_theatre"][theatre id] = {"read", "filled", "rejected": {why: n}}."""
    stats = {"candidates": 0, "due": 0, "read": 0, "filled": 0, "no_match": 0, "unreadable": 0,
             "skipped_budget": 0, "by_theatre": {}}
    cands = candidates(src, fills)
    stats["candidates"] = len(cands)
    others = {SY.norm(s.get("title") or "") for s in (SY._const(src, "SHOWS") or [])} - {""}
    todo = plan(cands, fills, today, max_per_host)
    stats["due"] = len(todo)
    for sid, title, theatre, url, th in todo:
        if net.over_budget():
            stats["skipped_budget"] += 1
            continue
        per = stats["by_theatre"].setdefault(th, {"read": 0, "filled": 0, "rejected": {}})
        rec = fills.get(sid) or {}
        source = ART_SOURCE if _host(url) in ART_HOSTS else SOURCE
        r = net.get(url)
        if r is None or not getattr(r, "content", None):
            if net.over_budget():                    # the cap was reached inside the request: not a miss
                stats["skipped_budget"] += 1
                continue
            stats["unreadable"] += 1                 # robots.txt, a block, an HTTP error, no answer
            per["rejected"][NOT_READ] = per["rejected"].get(NOT_READ, 0) + 1
            _miss(fills, sid, rec, source, url, today, NOT_READ, title)
            continue
        stats["read"] += 1
        per["read"] += 1
        final = getattr(r, "url", None)
        text, why = read_page(decode(r.content), url, title, theatre, others - {SY.norm(title)},
                              final if isinstance(final, str) else None)
        if verbose:
            print(f"  {sid}: {'FILL ' + str(len(text)) if text else 'no (' + why + ')'} {url}", file=sys.stderr)
        if text:
            fills[sid] = {"bg": text, "source": source, "source_url": url,
                          "fetched_on": today.isoformat(), "title": title,
                          "theatre": theatre[0] if theatre else ""}
            stats["filled"] += 1
            per["filled"] += 1
            continue
        stats["no_match"] += 1
        per["rejected"][why] = per["rejected"].get(why, 0) + 1
        _miss(fills, sid, rec, source, url, today, why, title)
    return stats


def make_net(budget=BUDGET_S, max_requests=MAX_REQUESTS, cache_dir=None):
    """own_page_posters.Net that also stops after max_requests fetches."""
    from own_page_posters import Net

    class _Net(Net):
        def over_budget(self):
            return super().over_budget() or self.fetched >= max_requests
    return _Net(budget=budget, cache_dir=cache_dir)
