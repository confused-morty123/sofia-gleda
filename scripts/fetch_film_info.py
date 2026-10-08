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
  vlaikovacinema.com /screening/.../  — BG description + cast/dir
  ndk.bg/en/...                       — English description (synEn) + poster
  Other hosts                         — skipped

Genre vocabulary: mapped to the app's fixed list only. Unknown words dropped.
"Български" added when the page's country is България.

Keep-previous: never blanks an entry a failed fetch could not refresh; skips
films whose existing entry already has synopsis + dir + cast (but still
harvests img for poster-less entries).

    python3 scripts/fetch_film_info.py          # -> film_info.json
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
_PARSER_VERSION = 4


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
    - Its dir field is suspiciously long (>80 chars) — sign of bleed.
    """
    if existing.get("pv", 0) < _PARSER_VERSION:
        return True
    cast = existing.get("cast") or ""
    if len(cast) > 160:
        return True
    dir_ = existing.get("dir") or ""
    if len(dir_) > 80:
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
            d = summary_map[key].strip().rstrip(",. ")
            if d and d != "—" and len(d) <= 80:
                rec["dir"] = d
            break

    # Cast (Участват or В ролите)
    for key in ("участват", "в ролите", "cast", "starring"):
        if key in summary_map:
            c = summary_map[key].strip().rstrip(",. ")
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

def parse_vlaikova_page(soup, url):
    """Parse a vlaikovacinema.com screening page for synopsis/dir/cast."""
    rec: dict = {}

    # Synopsis: .entry-content p, .screening-description, .film-description
    for sel in (".entry-content p", ".entry-content",
                ".screening-description p", ".screening-description",
                ".film-description p", ".film-description",
                ".content p", ".content"):
        nodes = soup.select(sel)
        for node in nodes:
            text = node.get_text(" ", strip=True)
            if len(text) >= 80 and not _PLACEHOLDER_RE.search(text):
                rec["synBg"] = text
                break
        if "synBg" in rec:
            break

    # Director / cast — look for labelled spans/divs.
    # Stop at the next label (word followed by colon) to avoid swallowing
    # crew credits (Музика:, Оператор:, Монтаж:, Език:, Субтитри:, etc.).
    _STOP_LABEL = r'(?=\s*\w[\w\s]{1,20}\s*[:：])'   # lookahead for next "Word: "
    for span in soup.find_all(["span", "div", "p", "li"]):
        t = span.get_text(" ", strip=True)
        if not rec.get("dir"):
            m = re.search(r'(?:Режисьор\s*[:：]\s*)([^\n;:]{3,80})', t, re.I)
            if m:
                d = m.group(1).strip().rstrip(",.")
                if d and d != "—":
                    rec["dir"] = d
        if not rec.get("cast"):
            # Match up to the next labelled field (stops at "Музика:", "Език:", etc.)
            m = re.search(
                r'(?:Актьори\s*[:：]\s*|В\s+ролите\s*[:：]\s*|Участват\s*[:：]\s*)'
                r'((?:(?!\s+\w[\w\s]{0,20}\s*[:：]).){3,200})',
                t, re.I | re.S)
            if m:
                c = m.group(1).strip().rstrip(",.")
                # Only take comma-separated names (stop at dash-separated role lists too)
                # Split on comma; limit to 5 names
                parts = [n.strip() for n in re.split(r',|–| – ', c) if n.strip()]
                # Discard parts that look like crew labels (contain ":")
                parts = [p for p in parts if ':' not in p]
                c = ", ".join(parts[:5])
                if c and c != "—":
                    rec["cast"] = c

    # Genres
    for sel in (".film-genres", ".genres", "[class*='genre']"):
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

    # Director / cast from page text
    for span in soup.find_all(["span", "div", "p", "li"]):
        t = span.get_text(" ", strip=True)
        if not rec.get("dir"):
            m = re.search(r'(?:Director\s*[:：]\s*)([^\n;]{3,80})', t, re.I)
            if m:
                d = m.group(1).strip().rstrip(",.")
                if d and d != "—":
                    rec["dir"] = d
        if not rec.get("cast"):
            # Stop at next crew label (Cinematography:, Editing:, Music:, Language:, etc.)
            m = re.search(
                r'(?:Starring\s*[:：]\s*|Cast\s*[:：]\s*)'
                r'((?:(?!\s+\w[\w\s]{0,20}\s*[:：]).){3,200})',
                t, re.I | re.S)
            if m:
                c = m.group(1).strip().rstrip(",.")
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


def harvest_img(soup, url):
    """Dispatch image harvesting to the right function based on URL host."""
    from urllib.parse import urlparse
    host = urlparse(url).netloc.lstrip("www.")
    if "ndk.bg" in host:
        return harvest_ndk_img(soup, url)
    elif "programata.bg" in host:
        return harvest_programata_img(soup, url)
    elif "vlaikovacinema.com" in host:
        return harvest_vlaikova_img(soup, url)
    return None


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

def parse_film_page(soup, url):
    """Dispatch to the right parser based on URL host."""
    from urllib.parse import urlparse
    host = urlparse(url).netloc.lstrip("www.")
    if "programata.bg" in host:
        return parse_programata(soup, url)
    elif "vlaikovacinema.com" in host:
        return parse_vlaikova_page(soup, url)
    elif "ndk.bg" in host:
        return parse_ndk_page(soup, url)
    else:
        return {}   # unsupported host — skip


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
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

    # Track stats
    n_fetched = n_synopsis = n_dir = n_cast = n_genres = n_img = 0

    out: dict = dict(prev)   # start from previous; only update what we can

    for f in films:
        fid = f.get("id")
        if not fid:
            continue

        existing = out.get(fid, {})
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
        if details_complete and not needs_img:
            continue

        # Determine if this film (in FILMS) needs details
        details_needed = needs_details(f)
        if not details_needed and not needs_img:
            continue

        # Get the link for this film
        url = links.get(fid)
        if not url:
            continue

        from urllib.parse import urlparse
        host = urlparse(url).netloc.lstrip("www.")
        # Only supported parsers
        if not any(h in host for h in ("programata.bg", "vlaikovacinema.com", "ndk.bg")):
            continue

        soup = session.soup(url)
        if soup is None:
            # Keep previous if fetch failed
            continue
        n_fetched += 1

        # Merge: never blank a field that had a value
        entry = dict(out.get(fid, {}))

        # ----- Parse details (synopsis, dir, cast, genres) -----
        if details_needed and not details_complete:
            rec = parse_film_page(soup, url)
            for field in ("synBg", "synEn", "dir", "cast", "genres"):
                val = rec.get(field)
                if val:
                    if field == "genres" and not isinstance(val, list):
                        continue
                    if field == "genres" and not val:
                        continue
                    entry[field] = val

            entry["src"] = url
            entry["pv"] = _PARSER_VERSION   # parser version stamp

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
    print(f"  {len(out_clean)} total entries in {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
