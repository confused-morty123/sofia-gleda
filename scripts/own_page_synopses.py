#!/usr/bin/env python3
"""Sofia Gleda — thin play synopses read from each play's OWN production page
(owner spec 2026-11-09), the page own_page_posters.py already reads for posters.

Route: for every listed play whose Bulgarian synopsis is shorter than
synopses.THIN, the page its LINKS entry points to (own_page_posters.own_page:
mlt.bg / sofiapuppet.com are not read, their production's theatre.art.bg page is)
is fetched with own_page_posters.Net (GET only, robots.txt obeyed, 2 s per host,
a request and time budget) and a description is accepted only when

  * the page is the play's: page_identity (its own headings name the title), the
    request did not end on another page (moved_page) and its canonical is not the
    site's root (root_canonical);
  * the text comes from the page's description block: a per-host container
    (HOST_BLOCKS, checked on real pages 2026-11-09) or, for any other host, the
    page's one main content block (own_page_posters.main_content). Never the whole
    page: positive evidence only;
  * it is the LEAD of that block: consecutive prose paragraphs from the first one,
    skipping a credits list before it and stopping at the first credits / tickets /
    duration / awards / cookie / interview / biography paragraph after it;
  * it names no other listed title, is Bulgarian (>= 70 % Cyrillic letters), is
    at least synopses.MIN_FILL characters long and carries no "€" glued to a
    Cyrillic letter (a site-side "лв" -> "€" replacement that corrupted words);
    ticket / promo lines (билет, VIP, emoji) and short awards lines end the lead.

theatre.art.bg pages (the mirror of mlt.bg / sofiapuppet.com) are parsed with
synopses.parse_art_production (its own title and venue checks).

Each fill is stored in synopsis_fills.json as
    {"bg", "source": "own-page", "source_url", "fetched_on", "title", "theatre"}
and applied by synopses.apply (owner override > own page > theatre.art.bg > seed;
only over a shown text shorter than THIN). English is not produced here:
translate.py sends BG-only synopses to DeepL.

    python3 scripts/synopses.py --apply     # runs this route as part of the synopses step
"""
from __future__ import annotations
import re, sys, pathlib, urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import synopses as SY                                              # noqa: E402

SOURCE = "own-page"
MAX_CHARS = 1500            # whole paragraphs up to about this many characters
LEAD_MIN = 50               # the first paragraph of a lead is at least this long (a sentence)
PARA_MIN = 40               # a later paragraph shorter than this ends the lead
CYR_MIN = 0.70
MAX_REQUESTS = 120          # per run (pages + robots.txt), like poster_fix
BUDGET_S = 300              # seconds of network time per run

# Description containers per host (CSS, all matches tried in page order; the first
# giving a lead wins). Checked on two real pages per theatre, 2026-11-09.
HOST_BLOCKS = {
    "satirata.bg": ["div.show-details"],
    "theatrevazrajdane.bg": ["article.portfolio_page"],
    "toplocentrala.bg": ["div.program-description-cms"],
    "natfiz.bg": ["div.single-event-detail div.event-content"],
    "sfumato.info": ["div.play-details-col"],
    "nationaltheatre.bg": ["div.container div.row div.col-12"],
    "iamstudio.bg": ["div.woocommerce-product-details__short-description"],
    "salzaismyah.bg": ["div.mr-md-3"],
    "artvent.bg": ["div.col-md-12 > div.m-m-0"],
    "zadkanala.bg": ["div.field-name-body div.field-item"],
    "tba.art.bg": ["div.text-content"],
    "theatre199.org": ["article.page-headings-wrapper"],
    "sofiatheatre.eu": ["div.repertoire-info"],
}
ART_HOSTS = {"theatre.art.bg"}

_DROP_SEL = ("script, style, noscript, nav, footer, header, form, iframe, button, "
             "h1, h2, h3, h4, h5, h6, ul.menu, table")
_DROP_CLASS_RE = re.compile(r"cookie|popup|modal|subscribe|newsletter|share|social|related|teaser|"
                            r"slider|carousel|swiper|breadcrumb|comment|gallery|galler|ticket|"
                            r"remodal|sidebar", re.I)
_BLOCK_TAGS = {"p", "div", "li", "br", "section", "article", "blockquote", "tr", "dd", "dt"}

_CREDIT_WORD = (r"(?:режис\w*|сценограф\w*|сценография|костюм\w*|музика\w*|композитор\w*|превод\w*|"
                r"драматург\w*|хореограф\w*|участват|участие\w*|ролите|изпълняват|актьор\w*|"
                r"помощник\w*|осветление|светлинн\w*|постанов\w*|автор\w*|текст|адаптация|"
                r"продуцент\w*|асистент\w*|сценична версия|видео\w*|звук\w*|кукли|пластика|"
                r"художник\w*|фотограф\w*|диригент\w*|концепция|идея|словесно действие|по идея|вокали|бенд)")
CREDIT_START_RE = re.compile(r"^\W*(?:от\s+[А-ЯA-Z]|по\s+[А-ЯA-Z„\"]|с\s+участието|жанр|времетраене|" + _CREDIT_WORD + r"\b)", re.I)
CREDIT_ANY_RE = re.compile(r"\b" + _CREDIT_WORD + r"\b", re.I)
BOILER_START_RE = re.compile(
    r"^\W*(?:билет\w*|цена|цени|продължителност|премиера|антракт|възраст|подходящ\w*|"
    r"награ\w*|номинац\w*|аскеер|повече|прочети|бисквит\w*|работно време|каса|резерв\w*|"
    r"купи\w*|абонамент\w*|интервю|галерия|снимки|снимка|партньор\w*|носител\w*|лауреат\w*|"
    r"с подкрепата|проект\w*|представлението на \d|спектакълът се играе|дата|час)\b", re.I)
BOILER_ANY_RE = re.compile(r"бисквитк|cookies|\bлв\.|€|работно време|продължителност|антракт|"
                           r"\d{1,2}[.:]\d{2}\s*ч|\+\d{1,2}\b", re.I)
INTERVIEW_RE = re.compile(r"^\s*[-–—]\s|^\W*интервю|^\W*г-[нжц]\b", re.I)
# "Ерик-Еманюел Шмит е френски драматург", "ЕЙПРИЛ ДЕ АНЖЕЛИС  е сред …", "Завършва …"
BIO_RE = re.compile(r"^\W*[А-ЯЁA-Z][\w-]+(?:[ \-]+[А-ЯЁA-Z][\w-]+){1,3}\s+(?:е|e|са)\s|"
                    r"^\W*(?:роден|родена|завършва|завършил|завършила)\b|завършва натфиз")


def _host(url):
    return (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")


def cyr_ratio(s):
    letters = [c for c in s if c.isalpha()]
    return sum("Ѐ" <= c <= "ӿ" for c in letters) / len(letters) if letters else 0.0


def _clean(t):
    return re.sub(r"\s+", " ", t.replace("\xa0", " ")).strip()


def blocks(el):
    """The container's text split at block-level tags and <br>, in order."""
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
            elif name in _BLOCK_TAGS:
                flush()
                walk(ch)
                flush()
            else:
                walk(ch)
    walk(el)
    flush()
    return out


_HARD_REST_RE = re.compile(INTERVIEW_RE.pattern + r"|^(?=.{0,300}$).*(?:номинац|номинир|аскеер|награда)" +
                           r"|^\W*(?:награ\w*|номинац\w*|носител\w*|лауреат\w*|за автора|за режисьора)\b"
                           r"|^(?=.{0,200}$).*интервю|^(?=[^.!…]{0,250}$).*\?\W*$", re.I)


def hard_stop(b):
    """A biography, interview or awards block: the description is over."""
    return bool(BIO_RE.search(b) or _HARD_REST_RE.search(b))


def is_credit(b):
    if CREDIT_START_RE.search(b):
        return True
    hits = len(CREDIT_ANY_RE.findall(b))
    seps = len(re.findall(r"\s[-–—]\s|:", b))
    if hits >= 2 and seps >= 2:
        return True
    words = re.findall(r"[^\W\d_]+", b)
    caps = sum(w[0].isupper() for w in words)
    return len(words) >= 6 and caps / len(words) > 0.6 and not re.search(r"[.!?…]\s", b)


TICKET_RE = re.compile(r"билет|vip\b|meet & greet|[\U0001F300-\U0001FAFF]", re.I)

# A site-side "лв" -> "€" replacement that cut through words (2026-11-09: "допъ€ат" for
# "допълват" on a theatre page): the text is corrupted at its source, never copied.
CORRUPT_RE = re.compile(r"[а-яА-Я]€|€[а-яА-Я]")


def is_stop(b):
    return bool((len(b) < 400 and TICKET_RE.search(b)) or BOILER_START_RE.search(b) or (len(b) < 220 and BOILER_ANY_RE.search(b))
                or INTERVIEW_RE.search(b) or BIO_RE.search(b)
                or (b.rstrip().endswith("?") and len(b) < 250 and not re.search(r"[.!…]\s", b)))


def is_prose(b, first=False):
    if len(b) < (LEAD_MIN if first else PARA_MIN) or cyr_ratio(b) < CYR_MIN:
        return False
    return bool(re.search(r"[.!?…]\W*$|[.!?…]\s", b)) or len(b) >= 150


_OPEN_Q, _CLOSE_Q = "„\"“«,'", "“”\"»'"


def is_epigraph(b):
    """A quotation standing alone (an epigraph or a line from the play): it opens
    with a quote mark and either never closes it or closes it at the very end."""
    b = b.strip()
    if not b or b[0] not in _OPEN_Q:
        return False
    rest = b.lstrip(_OPEN_Q)
    hits = [i for i in (rest.find(c) for c in _CLOSE_Q) if i >= 0]
    return not hits or min(hits) >= len(rest.rstrip(" .…!?")) - 1


def lead(bl, title):
    """The lead description from a container's blocks, or ""."""
    t = SY.norm(title)
    out, n = [], 0
    for b in bl:
        b = re.sub(r"^(?:more|read more|прочети още)\b[ \t]*", "", b, flags=re.I)
        if SY.norm(b) == t or SY.norm(b) in ("", "повече", "виж повече"):
            continue
        if hard_stop(b):
            if not out or n < SY.MIN_FILL:
                return ""                          # biography / interview / awards: nothing after it
            break
        if not out:                                # before the lead: credits, dates, labels, an epigraph
            if is_stop(b) or is_credit(b) or is_epigraph(b) or not is_prose(b, first=True):
                continue
            out.append(b)
            n = len(b)
            continue
        if is_stop(b) or is_credit(b) or not is_prose(b) or n + len(b) > MAX_CHARS:
            if n >= SY.MIN_FILL:
                break
            out, n = [], 0                         # a stub before a credits block: look further
            if not (is_stop(b) or is_credit(b) or is_epigraph(b)) and is_prose(b, first=True):
                out, n = [b], len(b)
            continue
        out.append(b)
        n += len(b)
    return "\n\n".join(out) if n >= SY.MIN_FILL else ""


def names_other(text, title, other_titles):
    """True when the text names another listed title (a normalised title of 8+
    characters and 2+ words that is not part of this play's own title)."""
    nt, mine = " " + SY.norm(text) + " ", SY.norm(title)
    for o in other_titles:
        if len(o) >= 8 and " " in o and o not in mine and mine not in o and f" {o} " in nt:
            return True
    return False


def containers(soup, url):
    hints = HOST_BLOCKS.get(_host(url))
    if hints:
        found = []
        for sel in hints:
            found += soup.select(sel)
        return found
    import own_page_posters as O
    mc = O.main_content(str(soup))
    if mc is None:
        return []
    from bs4 import BeautifulSoup
    return [BeautifulSoup(mc, "lxml")]


def parse_own_page(html, url, title, theatre_names=(), other_titles=(), final_url=None):
    """The play's own description from its own page, or None (see module doc)."""
    import own_page_posters as O
    if not html or (final_url and O.moved_page(url, final_url)) or O.root_canonical(html, url):
        return None
    if _host(url) in ART_HOSTS:
        text = SY.parse_art_production(html, title, list(theatre_names))
    else:
        if not O.page_identity(html, title):
            return None
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        text = ""
        for c in containers(soup, url):
            drop = [el for el in c.select(_DROP_SEL)]
            drop += [el for el in c.find_all(True)
                     if _DROP_CLASS_RE.search(" ".join(el.get("class") or []) + " " + (el.get("id") or ""))]
            for el in drop:
                try:
                    el.decompose()
                except Exception:                # noqa: BLE001  (inside an already dropped one)
                    pass
            text = lead(blocks(c), title)
            if SY.slen(text) >= SY.MIN_FILL:
                break
    if not text or SY.slen(text) < SY.MIN_FILL or cyr_ratio(text) < CYR_MIN or CORRUPT_RE.search(text):
        return None
    if names_other(text, title, other_titles):
        return None
    return text


# ---------------------------------------------------------------------- fetch
def candidates(src, thin=SY.THIN):
    """[(showId, title, [theatre names], page url)] — listed plays with a thin
    synopsis whose own page is not a theatre.art.bg LINKS entry (synopses.py
    reads those)."""
    import own_page_posters as O
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
        if not sh or sid not in up or sid in agg or sid in seen or SY.slen(sh.get("synBg")) >= thin:
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
                    [n for n in (names.get(th), SY.ART_VENUE_NAMES.get(th)) if n], page))
    return out


def fetch(src, fills, net, today, decode, verbose=False):
    """Read the due own pages; write fills (keep-previous). Returns stats."""
    stats = {"candidates": 0, "read": 0, "filled": 0, "no_match": 0, "unreadable": 0, "skipped_budget": 0}
    cands = candidates(src)
    others = {SY.norm(s.get("title") or "") for s in (SY._const(src, "SHOWS") or [])}
    for sid, title, theatre, url in cands:
        stats["candidates"] += 1
        rec = fills.get(sid)
        if rec and rec.get("bg") and rec.get("source") != SOURCE:
            continue                                 # an owner-side / theatre.art.bg fill stays
        if not SY.due(rec, url, today):
            continue
        if net.over_budget():
            stats["skipped_budget"] += 1
            continue
        r = net.get(url)
        if r is None:
            stats["unreadable"] += 1
            continue
        stats["read"] += 1
        text = parse_own_page(decode(r.content), url, title, theatre,
                              others - {SY.norm(title)}, getattr(r, "url", None))
        if verbose:
            print(f"  {sid}: {'FILL ' + str(len(text)) if text else 'no'} {url}", file=sys.stderr)
        if text:
            fills[sid] = {"bg": text, "source": SOURCE, "source_url": url,
                          "fetched_on": today.isoformat(), "title": title,
                          "theatre": theatre[0] if theatre else ""}
            stats["filled"] += 1
        elif not (rec and rec.get("bg") and rec.get("source_url") == url):
            fills[sid] = {"source": SOURCE, "source_url": url, "tried_on": today.isoformat(),
                          "title": title}
            stats["no_match"] += 1
    return stats


def make_net(budget=BUDGET_S, max_requests=MAX_REQUESTS, cache_dir=None):
    """own_page_posters.Net that also stops after max_requests fetches."""
    from own_page_posters import Net

    class _Net(Net):
        def over_budget(self):
            return super().over_budget() or self.fetched >= max_requests
    return _Net(budget=budget, cache_dir=cache_dir)
