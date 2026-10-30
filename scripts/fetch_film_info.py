#!/usr/bin/env python3
"""Fetch missing details for films from their own programme pages.

For every film in FILMS that is missing details (empty/short/placeholder
synopsis, or empty director/cast), this script fetches the film's LINKS
info page and parses Bulgarian synopsis, director, cast and genres.

Also harvests a poster image `img` (absolute URL) for films that have no TMDB
poster and no film_links_posters entry:
  - NDK pages: the content <img> whose _resize1000x1000 variant is in the main
    content — never the 182x136 sidebar thumbnails.  The alt must match the
    page's event title (normalised), or the single _resize1000x1000 image is
    taken if only one exists.
  - programata / vlaikova pages: og:image, or the main hero/poster image.

Keep-previous: a failed fetch never removes an existing `img`.
The "skip complete entries" shortcut must not prevent harvesting `img`.

Parsers:
  programata.bg /kino/filmi/<slug>/  — the richest source for BG cinema
  vlaikovacinema.com /screening/.../  — the info line (English/original title,
                                        country, year), runtime, genres, director(s),
                                        cast and the BG synopsis. Read for every film
                                        Влайкова shows, also when its LINKS entry
                                        points elsewhere (the programme grid's titles
                                        matched by identity key; a merged listing's page)
  ndk.bg/en/...                       — English description (synEn) + poster
  Other hosts                         — skipped

Genre vocabulary: mapped to the app's fixed list only. Unknown words dropped.
"Български" added when the page's country is България.

Keep-previous: never blanks an entry a failed fetch could not refresh; skips
films whose existing entry already has synopsis + dir + cast (but still
harvests img for poster-less entries).

    python3 scripts/fetch_film_info.py                   # -> film_info.json
    python3 scripts/fetch_film_info.py --venue vlaikova  # Влайкова's film pages only
"""
from __future__ import annotations
import json, os, re, sys, pathlib

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("pip install beautifulsoup4 lxml")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from netfetch import Fetcher

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
OUT  = ROOT / "film_info.json"
CINEMA_FILMS = ROOT / "cinema_films.json"
TMDB_JSON    = ROOT / "tmdb_films.json"
LINKS_JSON   = ROOT / "film_links_posters.json"

# App's canonical genre vocabulary. Only words from this list may appear in
# the genres field; unknown words from the page are silently dropped.
GENRE_VOCAB = {
    "Драма", "Комедия", "Документален", "Анимация", "Хорър",
    "Трилър", "Фантастика", "Фентъзи", "Приключенски", "Романтика",
    "Криминален", "Биографичен", "Исторически", "Музикален", "Семеен",
    "Екшън", "Мистерия", "Спорт", "Аниме",
}

# Map lower-case variants from page to canonical form.
# Programata and vlaikova use different capitalisations and declensions.
_GENRE_MAP: dict[str, str] = {}
for _g in GENRE_VOCAB:
    _GENRE_MAP[_g.lower()] = _g

# Additional Bulgarian declension aliases
_EXTRA_MAP = {
    "драматичен": "Драма", "драматична": "Драма", "комедийен": "Комедия",
    "документален": "Документален", "документална": "Документален",
    "анимационен": "Анимация", "анимационна": "Анимация",
    "ужаси": "Хорър", "horror": "Хорър",
    "трилъри": "Трилър",
    "научна фантастика": "Фантастика", "sci-fi": "Фантастика",
    "fantasy": "Фентъзи",
    "приключения": "Приключенски", "adventure": "Приключенски",
    "романтичен": "Романтика", "romance": "Романтика",
    "криминал": "Криминален", "crime": "Криминален",
    "биографичен": "Биографичен", "biography": "Биографичен", "biopic": "Биографичен",
    "исторически": "Исторически", "historical": "Исторически",
    "музикален": "Музикален", "musical": "Музикален", "music": "Музикален",
    "семеен": "Семеен", "family": "Семеен",
    "екшън": "Екшън", "action": "Екшън", "екшн": "Екшън",
    "мистерия": "Мистерия", "mystery": "Мистерия",
    "спорт": "Спорт", "sports": "Спорт",
    "аниме": "Аниме", "anime": "Аниме",
    "drama": "Драма", "comedy": "Комедия", "animation": "Анимация",
    "documentary": "Документален", "thriller": "Трилър",
}
_GENRE_MAP.update(_EXTRA_MAP)

# Placeholder synopsis patterns (synBg that is really a venue/screening note,
# not actual plot summary).
_PLACEHOLDER_RE = re.compile(r'прожекция в|прожекция във|screening at', re.I)

# Parser version — bump this to force re-fetch of all entries that were
# written by an older parser (old entries have no "pv" key or a smaller int).
_PARSER_VERSION = 5

# Credit-label words that must never appear inside a dir/cast value.
# Used both for sanitisation and for stale-detection.
_CREDIT_LABEL_RE = re.compile(
    r'\b(?:Screenplay|Starring|Cinematography|Editing|Music|Producer|Language|'
    r'Subtitles|Duration|Director|Cast|Сценарий|Участват|В\s+ролите|Режисьор|'
    r'Оператор|Музика|Продуцент|Монтаж|Език)\s*[:：]',
    re.I,
)


# The same labels as whole words, colon or not. Влайкова's markup runs the next
# label into the value — "<strong>Режисьор: </strong>Ойер Плаза<br/><strong>
# Сценаристи: </strong>…" reads "Режисьор : Ойер Плаза Сценаристи: …" — so a
# value cut at the next colon still ends in "Сценаристи" (2026-10-09: six films).
# Credits stop at any of these words; no person is named after one. ("Дублаж"
# is not among them: a dubbed film's voice cast legitimately reads "дублаж: …".)
_CREDIT_WORD_RE = re.compile(
    r'(?<![\w-])(?:Сценаристи|Сценарист|Сценарий|Оператори|Оператор|Музика|Композитор|'
    r'Продуценти|Продуцент|Копродуценти|Монтаж|Художник|Костюми|В\s+ролите|Участват|'
    r'Актьори|Актьорски\s+състав|Език|Субтитри|Държава|Година|Времетраене|'
    r'Продължителност|Жанр|Режисьори|Режисьор|Screenplay|Writers?|Cinematography|'
    r'Music|Producers?|Editing|Starring|Cast|Language|Subtitles|Country|Runtime|'
    r'Duration|Genre|Directors?)(?![\w-])',
    re.I,
)


def _clean_credit_value(text: str) -> str:
    """Strip any trailing label-bleed from a director or cast value.

    The value is cut at the first credit label — with a colon ("Screenplay:",
    "В ролите:") or as a bare word ("Ойер Плаза Сценаристи" -> "Ойер Плаза") —
    and trailing punctuation is stripped.  Returns the cleaned string (possibly
    empty).
    """
    m = _CREDIT_LABEL_RE.search(text)
    if m:
        text = text[:m.start()]
    w = _CREDIT_WORD_RE.search(text)
    if w:
        text = text[:w.start()]
    return text.strip().rstrip(",.;: ")


def _has_label_bleed(value: str) -> bool:
    """A stored dir/cast value that still carries a credit label."""
    return bool(value) and bool(_CREDIT_LABEL_RE.search(value) or _CREDIT_WORD_RE.search(value))


def sanitise_credits(entries):
    """Cut every stored dir/cast value at its first credit label (_clean_credit_value);
    a value that is nothing but a label is dropped. Returns how many were changed."""
    n = 0
    for entry in entries.values():
        for field in ("dir", "cast"):
            v = entry.get(field)
            if isinstance(v, str) and _has_label_bleed(v):
                cleaned = _clean_credit_value(v)
                if cleaned:
                    entry[field] = cleaned
                else:
                    del entry[field]
                n += 1
    return n


def extract_array(src, name):
    m = re.search(rf"const\s+{name}\s*=\s*\[", src)
    if not m:
        raise KeyError(name)
    start = m.end() - 1
    depth, i, in_str, quote, esc = 0, start, False, "", False
    while i < len(src):
        c = src[i]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == quote: in_str = False
        elif c in "\"'": in_str, quote = True, c
        elif c == "[": depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return start, i + 1, src[start:i + 1]
        i += 1
    raise ValueError(f"unterminated array {name}")


def needs_details(f):
    """Return True if the film's record (from FILMS) is missing meaningful details."""
    syn = f.get("synBg") or ""
    dir_ = f.get("dir") or ""
    cast = f.get("cast") or ""
    missing_syn = not syn or len(syn) < 80 or bool(_PLACEHOLDER_RE.search(syn))
    missing_dir = not dir_ or dir_ == "—"
    missing_cast = not cast or cast == "—"
    return missing_syn or missing_dir or missing_cast


def _film_info_stale(existing: dict) -> bool:
    """Return True if the film_info entry should be re-fetched.

    An entry is stale if:
    - It was written by an older parser version (pv < _PARSER_VERSION), OR
    - Its cast field is suspiciously long (>160 chars) — sign of synopsis bleed, OR
    - Its dir field is suspiciously long (>80 chars) — sign of bleed, OR
    - Its dir or cast value contains a credit-label keyword (label bleed),
      with a colon or as a bare word.
    """
    if existing.get("pv", 0) < _PARSER_VERSION:
        return True
    cast = existing.get("cast") or ""
    if len(cast) > 160:
        return True
    dir_ = existing.get("dir") or ""
    if len(dir_) > 80:
        return True
    # Detect label-bleed: a credit label inside dir or cast means bad data —
    # also a bare label word ("Ойер Плаза Сценаристи").
    if _has_label_bleed(dir_) or _has_label_bleed(cast):
        return True
    return False


def map_genres(text, is_bulgarian_country=False):
    """Extract genre words from page text and map to the app's vocabulary."""
    genres = set()
    # Split on common separators
    words = re.split(r'[,/|•\n\r]+', text.lower())
    for w in words:
        w = w.strip(" ·-–—\t")
        if not w:
            continue
        g = _GENRE_MAP.get(w)
        if g:
            genres.add(g)
        else:
            # Try partial match for compound expressions
            for key, val in _GENRE_MAP.items():
                if len(key) >= 4 and key in w:
                    genres.add(val)
                    break
    if is_bulgarian_country:
        genres.add("Български")
    return sorted(genres)


# --------------------------------------------------------------------------
# Programata.bg film page parser
# URL pattern: https://programata.bg/kino/filmi/<slug>/
# --------------------------------------------------------------------------

def parse_programata(soup, url):
    """Parse a programata.bg /kino/filmi/<slug>/ film page.

    The page structure uses `.text-summary-item` divs with a <span> label:
        <div class="text-summary-item movie-N"><span>Жанр: </span>комедия, драма</div>
        <div class="text-summary-item movie-N"><span>Режисьор: </span>Иван Иванов</div>
        <div class="text-summary-item movie-N"><span>Участват: </span>Актьор 1, Актьор 2</div>
        <div class="text-summary-item movie-N"><span>Държава: </span>България</div>

    Synopsis is in a <p> inside a <div class="text mb-5 pb-5">.

    Returns a dict with synBg, dir, cast, genres (all optional).
    """
    rec: dict = {}

    # ---- Structured fields from .text-summary-item ----
    # Build a label->value map by reading each div's text after stripping its span label.
    summary_map: dict[str, str] = {}
    for item in soup.select(".text-summary-item"):
        label_el = item.find("span")
        if label_el:
            label = label_el.get_text().strip().rstrip(": ：").lower()
            # Value = full text minus the label span text
            full = item.get_text(" ", strip=True)
            label_text = label_el.get_text(" ", strip=True)
            value = full[len(label_text):].strip().lstrip(": ：").strip()
            summary_map[label] = value

    # Director
    for key in ("режисьор", "реж", "director"):
        if key in summary_map:
            d = _clean_credit_value(summary_map[key])
            if d and d != "—" and len(d) <= 80:
                rec["dir"] = d
            break

    # Cast (Участват or В ролите)
    for key in ("участват", "в ролите", "cast", "starring"):
        if key in summary_map:
            c = _clean_credit_value(summary_map[key])
            names = [n.strip() for n in c.split(",") if n.strip()]
            c = ", ".join(names[:5])
            if c and c != "—":
                rec["cast"] = c
            break

    # Genre
    genre_raw = ""
    for key in ("жанр", "genre", "genres"):
        if key in summary_map:
            genre_raw = summary_map[key]
            break

    # Country (for "Български" genre)
    is_bg = False
    for key in ("държава", "country"):
        if key in summary_map and "българия" in summary_map[key].lower():
            is_bg = True
            break

    # ---- Synopsis: <p> inside div.text.mb-5.pb-5 ----
    # (Programata uses: <div class="text mb-5 pb-5"><p>synopsis text…</p></div>)
    for sel in ("div.text.mb-5.pb-5 p", "div.text p",
                ".film-description p", ".film-description",
                ".description p", ".description",
                "[itemprop='description']"):
        nodes = soup.select(sel)
        for node in nodes:
            text = node.get_text(" ", strip=True)
            if len(text) >= 80 and not _PLACEHOLDER_RE.search(text):
                rec["synBg"] = text
                break
        if "synBg" in rec:
            break
    # Fallback: og:description meta
    if "synBg" not in rec:
        og = soup.find("meta", attrs={"property": "og:description"})
        if og:
            text = (og.get("content") or "").strip()
            if len(text) >= 80 and not _PLACEHOLDER_RE.search(text):
                rec["synBg"] = text

    # ---- Genres ----
    if genre_raw:
        genres = map_genres(genre_raw, is_bg)
        if genres:
            rec["genres"] = genres
    elif is_bg:
        rec["genres"] = ["Български"]

    return rec


# --------------------------------------------------------------------------
# vlaikovacinema.com screening page parser
# URL pattern: https://vlaikovacinema.com/screening/<slug>/
# --------------------------------------------------------------------------
#
# A Влайкова film page (2026-10-30, read on eight pages) is
#   <div class="screening-single-content">
#     <h1 class="screening-single-title">ЕВЕРЕСТ: ОТ ДРУГАТА СТРАНА</h1>
#     <div class="screening-single-badges"><span class="cinema-badge
#          cinema-badge-documentary">Документален</span> …</div>
#     <div class="screening-single-meta">102 мин.</div>            (not always)
#     <div class="screening-single-description">
#       <p><strong>| EVEREST: THE OTHER SIDE | САЩ | 2026 |</strong><br/>synopsis…</p>
#       <p><strong>Режисьор: </strong>…<br/><strong>В ролите:</strong> …</p>
#       <p><strong>Език: </strong>…</p> <p>★ Фестивални селекции: …</p>
# The info line names the English title (and the original one when it differs:
# "| THE BELOVED | EL SER QUERIDO | Испания | 2026 |"), the country and the year;
# a listing may lead with the genre ("Документален | EVEREST: …") and a typo may
# open it with "l" for "|". Older pages put the same text in .entry-content.

# Badge classes Влайкова uses for a genre (the rest are "Премиера", "Дублиран" …).
_VLAIKOVA_BADGE_GENRES = {
    "cinema-badge-documentary": "Документален",
    "cinema-badge-animation": "Анимация",
    "cinema-badge-bulgarian": "Български",
}
# The parser version a Влайкова entry is stamped with: entries parsed before the
# info line was read (pv 5) are re-read once.
_VLAIKOVA_PARSER_VERSION = 6

_INFO_LINE_RE = re.compile(
    r"^\s*(?:\||[lI](?=\s))?\s*((?:[^|]{1,150}\|\s*)*?)((?:18|19|20)\d{2})\s*\|?\s*(.*)$", re.S)
_LATIN_ONLY_RE = re.compile(r"^[^Ѐ-ӿ]*[A-Za-z][^Ѐ-ӿ]*$")
_VLAIKOVA_LABEL_RE = re.compile(
    r"^\s*(В\s+ролите|Участват|Актьори|С\s+участието\s+на|Режисьори|Режисьор(?:\s+и\s+сценарист\w*)?|Сценаристи|"
    r"Сценарист|Сценарий|Жанр|Език|Субтитри|Оператори|Оператор|Музика|Композитор|"
    r"Продуценти|Продуцент|Монтаж|Държава|Година|Времетраене|Продължителност|"
    r"Фестивали[^:：]{0,30})\s*[:：]\s*(.*)$", re.I | re.S)
# A screening note, a festival list or an award line is not the film's synopsis.
_VLAIKOVA_NOTE_RE = re.compile(
    r"^\s*(?:★|\*|Прожекци|На\s+прожекцията|Специалн\w*\s+прожекци|Фестивал\w*\s+(?:селекции|и\s+отличия)|"
    r"Награди|Билети|Вход\b)|Влайкова|ВЛАЙКОВА|прожекцията\s+ще|след\s+прожекцията", re.I)
_ONE_DIRECTOR_RE = re.compile(
    r"\b(?:Филм|Документален\s+филм)\s+на\s+режисьора\s+([А-ЯA-Z][\w’'-]+(?:\s+[А-ЯA-Z][\w’'-]+){1,2})")
_EN_SMALL_WORDS = {"a", "an", "the", "and", "or", "but", "nor", "of", "in", "on", "at", "to",
                   "for", "by", "from", "with", "as", "vs", "via", "into"}


def _nbsp(s):
    return re.sub(r"[ \t   ]+", " ", s or "").strip()


def title_case_caps(t):
    """An English title Влайкова prints in capitals, as a person would write it:
    "EVEREST: THE OTHER SIDE" -> "Everest: The Other Side". A title that is not
    all capitals is kept as printed; Roman numerals and words with digits stay."""
    t = _nbsp(t)
    if not t or t != t.upper():
        return t
    out, first = [], True
    for w in t.split(" "):
        low = w.lower()
        core = re.sub(r"^\W+|\W+$", "", w)
        if re.fullmatch(r"[IVXLC]+", core) and core not in ("I",) or re.search(r"\d", w):
            out.append(w)
        elif low in _EN_SMALL_WORDS and not first:
            out.append(low)
        else:
            out.append(re.sub(r"[^\W\d_]", lambda m: m.group(0).upper(), low, count=1))
        first = w.endswith(":")
    return " ".join(out)


def parse_info_line(text):
    """The fields of a Влайкова info line, "| EVEREST: THE OTHER SIDE | САЩ | 2026 |".
    Shapes seen (2026-10-30): a leading genre ("Документален | EVEREST: …"), the
    Bulgarian title first and the country after the year ("КОСА | HAIR | 1979 | САЩ |"),
    the genre after the year ("… | 2026 | Психологическа драма |"), "І" (Cyrillic I)
    or a typo "l" for "|". Returns (fields, rest): fields holds title_en / original /
    country / year / genres when printed; rest is the text after the line (a synopsis
    that shares its paragraph). None when the text is not an info line: it needs a
    "|" and a year field, and every field before the year must be a title, a country
    or a genre."""
    t = _nbsp(text)
    if t.count("|") == 0 and len(re.findall(r"(?:^|\s)І(?=\s|$)", t)) >= 2:
        t = re.sub(r"(?:^|(?<=\s))І(?=\s|$)", "|", t)          # "І LALKA І Полша І 2026 І"
    t = re.sub(r"^[lI](?=\s)", "|", t)                        # "l HOW TO SAVE …"
    if "|" not in t:
        return None
    raw = t.split("|")
    fields = [f.strip(" :") for f in raw]
    yi = next((i for i, f in enumerate(fields) if re.fullmatch(r"(?:18|19|20)\d{2}", f)), None)
    if yi is None or yi == 0 and not t.lstrip().startswith("|"):
        return None
    before = [f for f in fields[:yi] if f]
    if not before and yi > 1 or len(before) > 6:
        return None
    # After the year: complete fields ("| САЩ |", "| Психологическа драма |") belong to
    # the line; the last piece, with no closing "|", is the text that follows it.
    after_complete, rest = [f for f in fields[yi + 1:-1] if f], fields[-1] if yi + 1 < len(fields) else ""
    rec, latin, genres = {}, [], []
    country = ""

    def genre_of(f):
        g = _GENRE_MAP.get(f.lower())
        if g:
            return [g]
        gs = map_genres(re.sub(r"\s+", ",", f))
        return gs if gs and len(f) <= 40 and len(f.split()) <= 3 else []

    for f in before:
        if _LATIN_ONLY_RE.match(f):
            latin.append(f)
        elif genre_of(f):
            genres.extend(g for g in genre_of(f) if g not in genres)
        elif re.fullmatch(r"[Ѐ-ӿ][Ѐ-ӿ .,\-–]*", f):
            if latin or f is before[-1]:
                country = f                # the Cyrillic field after the titles
            # else: the Bulgarian title printed first ("КОСА | HAIR | …")
        else:
            return None                    # a sentence, not an info line
    for f in after_complete:
        if len(f) > 60:
            break
        if genre_of(f):
            genres.extend(g for g in genre_of(f) if g not in genres)
        elif not country and re.fullmatch(r"[Ѐ-ӿ][Ѐ-ӿ .,\-–]*", f):
            country = f
    if latin:
        rec["title_en"] = title_case_caps(latin[0])
        if len(latin) > 1:
            rec["original"] = title_case_caps(latin[1])
    if country:
        parts = []
        for c in re.split(r"\s*[,\-–]\s*", country):
            if c and c not in parts:
                parts.append(c)            # "Франция, Япония, Германия, Белгия, Белгия"
        rec["country"] = ", ".join(parts)
    rec["year"] = int(fields[yi])
    if genres:
        rec["genres"] = list(dict.fromkeys(genres))
    return rec, rest.strip()


def _segments(p):
    """A <p> split at its <br/>s: [(text, emphasised)], where emphasised is True when
    the segment is (nearly) all bold/italic — a note or a credit, never a synopsis."""
    out = []
    for frag in re.split(r"<br\s*/?>", str(p), flags=re.I):
        fs = BeautifulSoup(frag, "lxml")
        text = _nbsp(fs.get_text(" ", strip=True))
        if not text:
            continue
        emph = sum(len(_nbsp(e.get_text(" ", strip=True)))
                   for e in fs.find_all(["strong", "b", "em", "i"])
                   if not e.find_parent(["strong", "b", "em", "i"]))
        out.append((text, emph >= 0.8 * len(text)))
    return out


def _credit_names(value, limit=5):
    v = _clean_credit_value(value)
    v = re.sub(r"\s+и\s+др\.?\s*$", "", v)
    v = re.sub(r",?\s+и\s+(?=[А-ЯA-Z])", ", ", v)
    names = [n.strip(" .") for n in v.split(",") if n.strip(" .")]
    names = [n for n in names if ":" not in n and len(n) <= 40]
    return ", ".join(names[:limit])


def parse_vlaikova_description(soup):
    """The structured fields of a Влайкова film page: title_en, original, country,
    year, runtime, genres, dir, cast and the synopsis (synBg). Only what the page
    prints; a field it does not print is absent."""
    rec: dict = {}
    scope = soup.select_one(".screening-single-content") or soup
    desc = (scope.select_one(".screening-single-description")
            or soup.select_one(".screening-single-description")
            or soup.select_one(".entry-content"))
    genres: list = []
    for b in scope.select(".cinema-badge"):
        for c in b.get("class") or ():
            g = _VLAIKOVA_BADGE_GENRES.get(c)
            if g and g not in genres:
                genres.append(g)
    meta = scope.select_one(".screening-single-meta")
    if meta:
        rm = re.search(r"(\d{2,3})\s*мин", meta.get_text(" ", strip=True))
        if rm and 20 <= int(rm.group(1)) <= 400:
            rec["runtime"] = int(rm.group(1))
    if desc is None:
        if genres:
            rec["genres"] = sorted(genres)
        return rec

    prose, started, info_seen = [], False, False
    for p in desc.find_all("p"):
        for text, emph in _segments(p):
            if not info_seen:
                parsed = parse_info_line(text)
                if parsed:
                    info_seen = True
                    fields, rest = parsed
                    for k, v in fields.items():
                        if k == "genres":
                            genres.extend(g for g in v if g not in genres)
                        else:
                            rec[k] = v
                    text, emph = rest, False
                    if not text:
                        continue
            lm = _VLAIKOVA_LABEL_RE.match(text)
            if lm:
                label, value = lm.group(1).lower(), lm.group(2)
                if label.startswith("режисьор") and not rec.get("dir"):
                    d = _credit_names(value, limit=3)
                    if d and len(d) <= 80:
                        rec["dir"] = d
                elif re.match(r"(в\s+ролите|участват|актьори|с\s+участието)", label) and not rec.get("cast"):
                    c = _credit_names(value)
                    if c:
                        rec["cast"] = c
                elif label == "жанр":
                    for g in map_genres(re.sub(r"\s+", ",", value)):
                        if g not in genres:
                            genres.append(g)
                elif label in ("времетраене", "продължителност") and "runtime" not in rec:
                    rm = re.search(r"(\d{2,3})", value)
                    if rm and 20 <= int(rm.group(1)) <= 400:
                        rec["runtime"] = int(rm.group(1))
                if started:
                    started = None          # credits after the synopsis end it
                continue
            if not rec.get("dir"):
                dm = _ONE_DIRECTOR_RE.search(text)
                if dm:
                    rec["dir"] = dm.group(1)
            if emph or _VLAIKOVA_NOTE_RE.search(text) or _PLACEHOLDER_RE.search(text):
                if started:
                    started = None
                continue
            if started is None:
                continue
            if len(text) >= 40:
                prose.append(text)
                started = True
    syn = "\n\n".join(prose).strip()
    if len(syn) >= 80:
        rec["synBg"] = syn
    if rec.get("country") and rec["country"].strip() == "България" and "Български" not in genres:
        genres.append("Български")
    if genres:
        rec["genres"] = sorted(set(genres))
    return rec


def parse_vlaikova_page(soup, url):
    """Parse a vlaikovacinema.com screening page: the info line (English and
    original title, country, year), runtime, genres, director(s), cast and the
    synopsis (parse_vlaikova_description), then the older label search for any
    credit the structured pass did not find."""
    rec: dict = parse_vlaikova_description(soup)

    # Synopsis: .entry-content p, .screening-description, .film-description
    if "synBg" not in rec:
        for sel in (".screening-description p", ".screening-description",
                    ".film-description p", ".film-description",
                    ".content p", ".content"):
            nodes = soup.select(sel)
            for node in nodes:
                text = node.get_text(" ", strip=True)
                if len(text) >= 80 and not _PLACEHOLDER_RE.search(text) and "|" not in text:
                    rec["synBg"] = text
                    break
            if "synBg" in rec:
                break

    # Director / cast — look for labelled spans/divs.
    # Stop at the next label (word followed by colon) to avoid swallowing
    # crew credits (Музика:, Оператор:, Монтаж:, Език:, Субтитри:, etc.).
    for span in soup.find_all(["span", "div", "p", "li"]):
        t = span.get_text(" ", strip=True)
        if not rec.get("dir"):
            m = re.search(r'(?:Режисьор\s*[:：]\s*)([^\n;:]{3,80})', t, re.I)
            if m:
                d = _clean_credit_value(m.group(1))
                if d and d != "—":
                    rec["dir"] = d
        if not rec.get("cast"):
            # Match up to the next labelled field (stops at "Музика:", "Език:", etc.)
            m = re.search(
                r'(?:Актьори\s*[:：]\s*|В\s+ролите\s*[:：]\s*|Участват\s*[:：]\s*)'
                r'((?:(?!\s+\w[\w\s]{0,20}\s*[:：]).){3,200})',
                t, re.I | re.S)
            if m:
                c = _clean_credit_value(m.group(1))
                # Only take comma-separated names (stop at dash-separated role lists too)
                # Split on comma; limit to 5 names
                parts = [n.strip() for n in re.split(r',|–| – ', c) if n.strip()]
                # Discard parts that look like crew labels (contain ":")
                parts = [p for p in parts if ':' not in p]
                c = ", ".join(parts[:5])
                if c and c != "—":
                    rec["cast"] = c

    # Genres (an older page's genre block, when the badges and info line gave none)
    for sel in (() if rec.get("genres") else (".film-genres", ".genres", "[class*='genre']")):
        node = soup.select_one(sel)
        if node:
            text = node.get_text(" ", strip=True)
            # Strip "Жанр:" prefix
            text = re.sub(r'^Жанр\s*[:：]\s*', '', text, flags=re.I)
            genres = map_genres(text, False)
            if genres:
                rec["genres"] = genres
            break

    return rec


# --------------------------------------------------------------------------
# ndk.bg event page parser (English)
# URL pattern: https://www.ndk.bg/en/...
# --------------------------------------------------------------------------

def parse_ndk_page(soup, url):
    """Parse a ndk.bg/en/ event page. Returns synEn (English), dir, cast, genres."""
    rec: dict = {}

    # NDK event pages are English (URL contains /en/)
    for sel in (".event-description p", ".event-description",
                ".entry-content p", ".entry-content",
                ".event-detail p", ".event-detail",
                ".single-event p", ".single-event",
                ".description p", ".description",
                "[itemprop='description']"):
        nodes = soup.select(sel)
        for node in nodes:
            text = node.get_text(" ", strip=True)
            if len(text) >= 80:
                rec["synEn"] = text
                break
        if "synEn" in rec:
            break

    # Director / cast from page text.
    # The inline-credits pattern used by ndk.bg places all credits in a single
    # paragraph: "Director: X Screenplay: X Starring: A, B Cinematography: Y".
    # We must stop each field at the next credit label.
    for span in soup.find_all(["span", "div", "p", "li"]):
        t = span.get_text(" ", strip=True)
        if not rec.get("dir"):
            # Match Director: value, stopping at the next label keyword.
            m = re.search(r'(?:Director\s*[:：]\s*)([^\n;]{3,160})', t, re.I)
            if m:
                d = _clean_credit_value(m.group(1))
                if d and d != "—":
                    rec["dir"] = d
        if not rec.get("cast"):
            # Stop at next crew label (Cinematography:, Editing:, Music:, Language:, etc.)
            m = re.search(
                r'(?:Starring\s*[:：]\s*|Cast\s*[:：]\s*)'
                r'((?:(?!\s+\w[\w\s]{0,20}\s*[:：]).){3,200})',
                t, re.I | re.S)
            if m:
                c = _clean_credit_value(m.group(1))
                # Discard parts that look like crew labels (contain ":")
                parts = [p.strip() for p in c.split(",") if p.strip()]
                parts = [p for p in parts if ':' not in p]
                c = ", ".join(parts[:5])
                if c and c != "—":
                    rec["cast"] = c

    # Genres
    for sel in (".event-genre", ".genres", ".genre", "[class*='genre']"):
        node = soup.select_one(sel)
        if node:
            genres = map_genres(node.get_text(" ", strip=True), False)
            if genres:
                rec["genres"] = genres
            break

    return rec


# --------------------------------------------------------------------------
# Poster image harvesting
# --------------------------------------------------------------------------

# Patterns for NDK thumbnail sizes that must never be used as the main poster.
_NDK_THUMB_RE = re.compile(r'_182x136\b|_182x\d+\b|_\d+x136\b', re.I)

# Bad image markers: site chrome / shared artwork
_BAD_IMG_HINTS = ("logo", "placeholder", "default-", "/default", "fallback",
                  "avatar", "sprite", "share-", "/share.", "og-image.png")


def _is_bad_img(url):
    return any(b in url.lower() for b in _BAD_IMG_HINTS)


def _norm_title(s):
    """Normalise a title for alt-text matching: lower-case, strip punctuation."""
    s = (s or "").lower()
    s = re.sub(r'[„“”"\'\'\'«»`.,!?:;—–\-_\(\)\[\]/\\]', " ", s)
    return re.sub(r"\s+", " ", s).strip()


def harvest_ndk_img(soup, url):
    """Extract the main event poster from an ndk.bg/en/ page.

    Selects the _resize1000x1000 <img> in the main page content.
    Never selects a 182×136 thumbnail (sidebar / "other events" section).
    Preference order:
      1. The _resize1000x1000 image whose alt text matches the page <h1> title
         (normalised).
      2. The single _resize1000x1000 image found anywhere in the page.
    Returns an absolute https URL, or None.
    """
    # Collect all ndk.bg/storage images that are the large resize variant
    candidates = []
    for img in soup.find_all("img"):
        src = img.get("src") or ""
        if not src.startswith("https://"):
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                src = "https://ndk.bg" + src
        if "ndk.bg/storage" not in src:
            continue
        if _NDK_THUMB_RE.search(src):
            continue   # sidebar thumbnail — skip
        if "_resize1000x1000" not in src:
            continue   # only the large variant is useful
        if _is_bad_img(src):
            continue
        alt = img.get("alt") or ""
        candidates.append((src, alt))

    if not candidates:
        return None

    # Try to match by alt text against the page's <h1> / <title>
    page_title = ""
    h1 = soup.find("h1")
    if h1:
        page_title = h1.get_text(" ", strip=True)
    if not page_title:
        t = soup.find("title")
        if t:
            page_title = t.get_text(" ", strip=True)
    norm_page = _norm_title(page_title)

    for src, alt in candidates:
        if norm_page and _norm_title(alt) and norm_page == _norm_title(alt):
            return src

    # Fallback: if there is exactly one large-resize image, use it
    if len(candidates) == 1:
        return candidates[0][0]

    return None


def harvest_programata_img(soup, url):
    """Extract the poster/hero image from a programata.bg film page.

    Tries og:image first; then the main film-poster/hero image.
    Returns an absolute https URL, or None.
    """
    og = soup.find("meta", attrs={"property": "og:image"})
    if og:
        u = (og.get("content") or "").strip()
        if u.startswith("//"):
            u = "https:" + u
        if u.startswith("https://") and not _is_bad_img(u):
            return u
    # Fallback: film hero image inside the page
    for sel in (".film-poster img", ".film-image img", ".poster img",
                "[class*='poster'] img", ".film-cover img", ".hero img"):
        img = soup.select_one(sel)
        if img:
            src = img.get("src") or ""
            if src.startswith("//"):
                src = "https:" + src
            if src.startswith("https://") and not _is_bad_img(src):
                return src
    return None


def harvest_vlaikova_img(soup, url):
    """Extract the poster/hero image from a vlaikovacinema.com page.

    Tries og:image first; then the main content image.
    Returns an absolute https URL, or None.
    """
    og = soup.find("meta", attrs={"property": "og:image"})
    if og:
        u = (og.get("content") or "").strip()
        if u.startswith("//"):
            u = "https:" + u
        if u.startswith("https://") and not _is_bad_img(u):
            return u
    # Fallback: first image inside the main content
    for sel in (".entry-content img", ".screening-description img",
                ".film-description img", ".content img"):
        img = soup.select_one(sel)
        if img:
            src = img.get("src") or ""
            if src.startswith("//"):
                src = "https:" + src
            if src.startswith("https://") and not _is_bad_img(src):
                return src
    return None


def harvest_kinoarena_img(soup, url):
    """Extract the poster from a kinoarena.com /bg/movie/<slug> film page.

    Tries og:image (the standard poster image on every KA movie page).
    Returns an absolute https URL, or None.
    """
    og = soup.find("meta", attrs={"property": "og:image"})
    if og:
        u = (og.get("content") or "").strip()
        if u.startswith("//"):
            u = "https:" + u
        if u.startswith("https://") and not _is_bad_img(u):
            return u
    return None


def _host_is(url, *domains):
    """The URL's host is one of `domains` or a subdomain of one: an exact or
    dot-suffix match, so "programata.bg.example.com" or "evilndk.bg" is neither.
    (A substring test, and netloc.lstrip("www."), which strips any leading w and
    dots, both let lookalike hosts through.)"""
    from urllib.parse import urlparse
    try:
        h = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(h == d or h.endswith("." + d) for d in domains)


def harvest_img(soup, url):
    """Dispatch image harvesting to the right function based on URL host."""
    if _host_is(url, "ndk.bg"):
        return harvest_ndk_img(soup, url)
    elif _host_is(url, "programata.bg"):
        return harvest_programata_img(soup, url)
    elif _host_is(url, "vlaikovacinema.com"):
        return harvest_vlaikova_img(soup, url)
    elif _host_is(url, "kinoarena.com"):
        return harvest_kinoarena_img(soup, url)
    return None


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

def parse_film_page(soup, url):
    """Dispatch to the right parser based on URL host."""
    if _host_is(url, "programata.bg"):
        return parse_programata(soup, url)
    elif _host_is(url, "vlaikovacinema.com"):
        return parse_vlaikova_page(soup, url)
    elif _host_is(url, "ndk.bg"):
        return parse_ndk_page(soup, url)
    else:
        return {}   # unsupported host — skip


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

VLAIKOVA_HOME = "https://vlaikovacinema.com/"
# Fields the Влайкова info line adds. A value a festival page or a person already
# stored is kept (fill-only); the page's synopsis and credits refresh as before.
_FILL_ONLY_FIELDS = ("title_en", "original", "country", "year", "runtime")


def vlaikova_programme_links(soup):
    """{title as printed: film page URL} from Влайкова's own programme grid (the
    page scrape_programs.parse_vlaikova reads: a.cinema-title, href = film page)."""
    out = {}
    for a in (soup.select("a.cinema-title") if soup is not None else ()):
        href, title = a.get("href") or "", a.get_text(" ", strip=True)
        if title and _host_is(href, "vlaikovacinema.com") and "/screening/" in href:
            out.setdefault(title, href)
    return out


def vlaikova_pages_for(films, programme, merges, prev):
    """{film id: its Влайкова film page} for every film that has one, whatever
    venue its LINKS entry points to (Еверест: Другата страна links to Дом на
    киното, the page that reads nothing): a programme title whose identity key
    (film_identity.key) is the film's own, or that of a listing merged into it
    (film_merges.json); and a merged-away listing's stored Влайкова page. A key
    that names two films is skipped, never guessed."""
    from film_identity import key as _key, film_redirects
    redirect = film_redirects(merges)
    ids = {f.get("id") for f in films if f.get("id")}
    by_key: dict = {}
    for f in films:
        k = _key(f.get("bg") or "")
        if k:
            by_key.setdefault(k, set()).add(f["id"])
    for old, m in (merges or {}).items():
        k = _key(((m.get("record") or {}).get("bg")) or "")
        new = redirect.get(old)
        if k and new in ids:
            by_key.setdefault(k, set()).add(new)
    out = {}
    for title, url in sorted((programme or {}).items()):
        hit = by_key.get(_key(title)) or set()
        if len(hit) == 1:
            out.setdefault(next(iter(hit)), url)
    for old, new in redirect.items():
        src = ((prev or {}).get(old) or {}).get("src") or ""
        if new in ids and _host_is(src, "vlaikovacinema.com"):
            out.setdefault(new, src)
    return out


def merge_details(entry, rec, fill_only=False):
    """Merge a parsed page (rec) into a film_info entry: synopsis, credits and
    genres refresh when the page prints them; the info-line fields only fill an
    empty one (all fields do with fill_only: an entry that was already complete,
    e.g. from a festival's page, read again only for the info line); nothing is
    ever blanked. Returns the fields that changed."""
    changed = []
    for field in ("synBg", "synEn", "dir", "cast", "genres") + _FILL_ONLY_FIELDS:
        val = rec.get(field)
        if not val or (field == "genres" and not isinstance(val, list)):
            continue
        if (fill_only or field in _FILL_ONLY_FIELDS) and entry.get(field):
            continue
        if entry.get(field) != val:
            entry[field] = val
            changed.append(field)
    return changed


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # --venue vlaikova: only Влайкова's film pages (the programme grid and each film's
    # page); every other film and host is left as it is.
    only = argv[argv.index("--venue") + 1] if "--venue" in argv else None
    if only not in (None, "vlaikova"):
        sys.exit(f"--venue {only}: only 'vlaikova' can be run on its own")
    src = HTML.read_text(encoding="utf-8")

    # Read FILMS
    try:
        _, _, films_lit = extract_array(src, "FILMS")
        films = json.loads(films_lit)
    except Exception as e:
        sys.exit(f"could not read FILMS: {e}")

    # Read LINKS
    links: dict[str, str] = {}
    try:
        _, _, links_lit = extract_array(src, "LINKS")
        for row in json.loads(links_lit):
            if isinstance(row, list) and len(row) >= 2:
                links[row[0]] = row[1]
    except Exception as e:
        print(f"  (could not read LINKS: {e})")

    # Also add links from cinema_films.json (minted arthouse films)
    if CINEMA_FILMS.exists():
        try:
            cinema = json.loads(CINEMA_FILMS.read_text(encoding="utf-8"))
            for fid, rec in cinema.items():
                if fid not in links and rec.get("source") in ("vlaikova", "lumiere", "odeon", "g8", "dom-kino"):
                    pass  # no dedicated link for cinema-films; they use the venue page
        except Exception:
            pass

    # Load previous film_info.json (keep-previous)
    prev: dict = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            prev = {}

    # Build sets of films that already have a verified poster from TMDB or
    # film_links_posters.json — we only need to harvest img for films with
    # neither (lowest-priority fallback).
    tmdb_with_poster: set = set()
    if TMDB_JSON.exists():
        try:
            tmdb_data = json.loads(TMDB_JSON.read_text(encoding="utf-8"))
            tmdb_with_poster = {fid for fid, v in tmdb_data.items()
                                if v.get("poster_path")}
        except Exception:
            pass

    film_links_covered: set = set()
    if LINKS_JSON.exists():
        try:
            fl = json.loads(LINKS_JSON.read_text(encoding="utf-8"))
            film_links_covered = {fid for fid, v in fl.items() if v}
        except Exception:
            pass

    session = Fetcher(budget_seconds=300)

    # Влайкова's own film pages, for films whose LINKS entry points elsewhere.
    try:
        from film_identity import load_merges
        merges = load_merges()
    except Exception:
        merges = {}
    vl_programme = vlaikova_programme_links(session.soup(VLAIKOVA_HOME))
    vl_pages = vlaikova_pages_for(films, vl_programme, merges, prev)
    print(f"  Влайкова: {len(vl_programme)} titles on the programme, "
          f"{len(vl_pages)} film page(s) matched to a film")

    # Track stats
    n_fetched = n_synopsis = n_dir = n_cast = n_genres = n_img = 0

    out: dict = dict(prev)   # start from previous; only update what we can

    for f in films:
        fid = f.get("id")
        if not fid:
            continue

        existing = out.get(fid, {})
        # The page to read: the film's LINKS entry on a host this script parses;
        # else its Влайкова page (a LINKS entry on Дом на киното reads nothing).
        url = links.get(fid)
        if not url or not _host_is(url, "programata.bg", "vlaikovacinema.com", "ndk.bg", "kinoarena.com"):
            url = vl_pages.get(fid)
        if not url:
            continue
        is_vlaikova = _host_is(url, "vlaikovacinema.com")
        if only == "vlaikova" and not is_vlaikova:
            continue
        # Does this film still need a poster image (img)?
        needs_img = (fid not in tmdb_with_poster
                     and fid not in film_links_covered
                     and not existing.get("img"))

        # Skip films whose existing film_info entry is up-to-date and complete,
        # UNLESS they still need an img — in that case we must fetch the page.
        has_syn = bool(existing.get("synBg") or existing.get("synEn"))
        has_dir = bool(existing.get("dir") and existing.get("dir") != "—")
        has_cast = bool(existing.get("cast") and existing.get("cast") != "—")
        details_complete = (has_syn and has_dir and has_cast
                            and not _film_info_stale(existing))
        # A Влайкова page read before its info line was parsed is read once more.
        vl_stale = is_vlaikova and existing.get("pv", 0) < _VLAIKOVA_PARSER_VERSION
        if details_complete and not needs_img and not vl_stale:
            continue

        # Determine if this film (in FILMS) needs details
        details_needed = needs_details(f) or vl_stale
        if not details_needed and not needs_img:
            continue

        soup = session.soup(url)
        if soup is None:
            # Keep previous if fetch failed
            continue
        n_fetched += 1

        # Merge: never blank a field that had a value
        entry = dict(out.get(fid, {}))

        # If the existing dir or cast contains label bleed, pre-clear them so
        # the keep-previous logic does not preserve contaminated values.
        for _cf in ("dir", "cast"):
            _cv = entry.get(_cf) or ""
            if _has_label_bleed(_cv):
                del entry[_cf]

        # ----- Parse details (synopsis, dir, cast, genres; Влайкова's info line) -----
        if details_needed and (not details_complete or vl_stale):
            rec = parse_film_page(soup, url)
            # A value read from another page (a festival's, programata's) is only
            # completed from this one, never replaced; this page's own earlier
            # reading is refreshed (an older parser's label bleed is corrected).
            from urllib.parse import unquote
            same_page = unquote(entry.get("src") or "") == unquote(url)
            changed = merge_details(entry, rec, fill_only=details_complete or not same_page)
            if changed and is_vlaikova:
                print(f"  {fid}: {', '.join(changed)}")

            entry["src"] = url
            # parser version stamp
            entry["pv"] = _VLAIKOVA_PARSER_VERSION if is_vlaikova else _PARSER_VERSION

            # Count coverage
            if rec.get("synBg") or rec.get("synEn"):
                n_synopsis += 1
            if rec.get("dir"):
                n_dir += 1
            if rec.get("cast"):
                n_cast += 1
            if rec.get("genres"):
                n_genres += 1

        # ----- Harvest poster image (img) -----
        if needs_img:
            img_url = harvest_img(soup, url)
            if img_url:
                entry["img"] = img_url
                n_img += 1
                print(f"  img {fid}: {img_url[:80]}")
            # Keep-previous: if fetch succeeded but no img found and there was
            # a previous img, that was already in `entry` (copied from prev above).

        if entry:
            out[fid] = entry

    # A stored credit that still carries a label ("Нани Морети В ролите", parsed
    # before the label-word rule) is cleaned in place, whether or not its page was
    # fetched again this run: the film's link may have moved to a host this script
    # does not parse (Дом на киното), and keep-previous would otherwise keep it.
    n_sanitised = sanitise_credits(out)

    # Write output
    out_clean = {k: v for k, v in out.items() if v}
    OUT.write_text(json.dumps(out_clean, ensure_ascii=False, indent=1), encoding="utf-8")

    n_total = len([f for f in films if needs_details(f)])
    print(f"\nfilm_info coverage:")
    print(f"  {n_total} films needed details, fetched {n_fetched} pages")
    print(f"  synopsis:  {n_synopsis} new")
    print(f"  director:  {n_dir} new")
    print(f"  cast:      {n_cast} new")
    print(f"  genres:    {n_genres} new")
    print(f"  img:       {n_img} new poster images harvested")
    print(f"  credits:   {n_sanitised} stored dir/cast value(s) cleaned of a label")
    print(f"  {len(out_clean)} total entries in {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
