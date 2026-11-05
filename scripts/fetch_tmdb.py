#!/usr/bin/env python3
"""Match every film in the app to TMDB for real posters + canonical English
titles + ratings. Writes tmdb_films.json next to the app.

Reads the film list from the inline dataset of index.html (between the
SOFIA-DATA markers). The TMDB read token comes from the TMDB_TOKEN environment
variable (a GitHub Actions secret in the hosted setup) — never hard-coded.

    TMDB_TOKEN=... python3 scripts/fetch_tmdb.py            # -> tmdb_films.json

If a run matches nothing (e.g. TMDB unreachable) and a previous tmdb_films.json
exists, the previous file is kept — a bad run never empties the posters.
"""
import json, os, re, time, urllib.parse, urllib.request, ssl, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
try:
    from posterpolicy import DISTRUSTED_HOSTS      # hosts whose images can't be tied to a title
except ImportError:                                # pragma: no cover
    DISTRUSTED_HOSTS = set()

try:
    import certifi
    SSLCTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSLCTX = ssl._create_unverified_context()

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
OUT  = ROOT / "tmdb_films.json"
LINKS_OUT = ROOT / "film_links_posters.json"
FILM_INFO_PATH = ROOT / "film_info.json"
HEALTH = ROOT / "health.json"           # the run's health report (scrape_programs writes it first)
# Curated per-film facts: film_aliases.json groups that carry a 'year' (and maybe
# an 'original' title) as the venue's own film page prints them — see tmdb_hints().
ALIASES_PATH = pathlib.Path(__file__).resolve().parent / "film_aliases.json"
TOKEN = os.environ.get("TMDB_TOKEN", "").strip()
# Genuine IMDb rating + vote count come from OMDb (TMDB only exposes its own
# vote_average, a different metric we deliberately never show as "IMDb"). Keyed by
# the imdb_id TMDB gives us. A GitHub Actions secret in the hosted setup — never
# hard-coded. Absent token => ratings are inherited from the previous run.
OMDB_TOKEN = os.environ.get("OMDB_TOKEN", "").strip()

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15")

# Re-release / restoration decoration that is NOT part of a film's canonical title
# and makes TMDB return nothing (e.g. "Cars (20th Anniversary)", "The Shining
# (Restored)"). Stripped only as a FALLBACK, after the decorated title misses, so a
# film with an IMDb rating and a synopsis stops falling through to the grey gradient.
_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")
_DECOR_RE = re.compile(
    r"\s*[-–—]\s*(?:\d+\s*(?:th|st|nd|rd)?\s*)?"
    r"(?:anniversary|restored|restoration|remastered|re-?release|director'?s cut|"
    r"final cut|extended cut|uncut|special edition|redux|4k|imax|70\s*mm)\b.*$",
    re.I)

# The same decoration as Bulgarian venues print it: "Отмъстителите: Краят – Специално
# издание" (Avengers: Endgame, 2019, re-released 2026), "Колите: 20 години от
# премиерата". TMDB knows the film by its own title only. "N години по-късно" names a
# film too (28 години по-късно), so it is decoration only after a dash or in brackets,
# never after a colon or as the whole title.
_REREL_BG = (r"специално\s+издание|юбилейно\s+издание|ново\s+издание|"
             r"ремастериран[аои]?(?:\s+версия)?|реставриран[аои]?(?:\s+версия)?|"
             r"възстановен[аои]?\s+версия|режисьорска\s+версия|разширена\s+версия|"
             r"удължена\s+версия|4\s*[KК](?:\s+версия)?|\d+\s+години\s+от\s+премиерата|"
             r"\d+(?:-?(?:та|ти|а))?\s+годишнина|годишнина")
_DECOR_BG_RE = re.compile(
    r"\s*(?:(?:[-–—:|]|\()\s*(?:" + _REREL_BG + r")|(?:[-–—|]|\()\s*\d+\s+години\s+по-късно)\s*\)?\s*$",
    re.I)
# English re-release markers (a subset of _DECOR_RE: IMAX and 70 mm are formats a new
# film is shown in, not a sign of an old one).
_REREL_EN_RE = re.compile(
    r"(?:[-–—:|(]\s*)(?:\d+\s*(?:th|st|nd|rd)?\s*)?(?:anniversary|restored|restoration|remastered|"
    r"re-?release|director'?s cut|final cut|extended cut|special edition|redux|4k)\b", re.I)


def rerelease_marker(title):
    """True when the title carries an explicit re-release / edition marker
    ("– Специално издание", "(20th Anniversary)", "– 4K"): the film is an old one shown
    again, so its TMDB year may be decades before the screening (year_ok)."""
    t = (title or "").strip()
    return bool(t) and bool(_DECOR_BG_RE.search(t) or _REREL_EN_RE.search(t))


def strip_rerelease(title):
    """The title without its trailing re-release markers (_DECOR_BG_RE): those words
    are decoration, never words a match must keep (stripped_words)."""
    t, prev = (title or "").strip(), None
    while t and t != prev:
        prev = t
        t = _DECOR_BG_RE.sub("", t).strip()
    return t


# Trailing "| 1979 |" year decoration — also captures the year (group 1).
_TRAILING_YEAR_BARS_RE = re.compile(r"\s*\|\s*(\d{4})\s*\|\s*$")
# Festival / series prefixes, e.g. "СИНЕЛИБРИ 2026 – ", "ЕВРОПЕЙСКИ КИНОКЛАСИКИ: "
# A prefix is ALL-CAPS (possibly with digits/spaces) followed by a dash or colon.
_FESTIVAL_PREFIX_RE = re.compile(
    r"^(?:[А-ЯA-Z][А-ЯA-Z0-9\s]{2,30})\s*(?:[-–—]|[:：])\s*", re.U)
# Festival / series names a cinema prepends to the film's own title. They are
# never part of the title, so they go BEFORE the first search (strip_series_prefix):
# "It’s Alive! Хорър Филм Фест Част IV: Сиянието" (Дом на киното) is "Сиянието".
# film_identity.strip_prefix() adds the festivals the identity rules know
# (Синелибри, Киномания, Кинокласики, Sofia Documental…), in either case.
_SERIES_PREFIX_RE = re.compile(
    r"^\s*it[’'`ʼ]?\s?s\s+alive\s*!?\s*(?:хорър\s+филм\s+фест(?:ивал)?)?"
    r"(?:\s*[-–—,]?\s*(?:част|vol\.?|volume)\s*[ivxlc\d]+)?\s*[:\-–—|]\s*", re.I)
# Person-name prefix: 2–4 words, letters only (Latin or Cyrillic), no digits.
# Matches "Акира Куросава", "Alejandro Jodorowsky", etc.
# Does NOT match "2001", "Spider-Man", "Venom", "Mission", "Oasis" (single word
# would need the title to also end with a year — see _is_director_prefix()).
_PERSON_NAME_PREFIX_RE = re.compile(
    r"^(?:[А-Яа-яA-Za-zЀ-ӿ]+(?:\s+[А-Яа-яA-Za-zЀ-ӿ]+){1,3})$", re.U)

# og:image fallback: a film TMDB cannot match (Bulgarian/festival titles) still has
# a real still/poster on its own programme page, whose URL is already in the app's
# LINKS array. These are the film's OWN images — honest, never a fabricated match.
_OG_RE  = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I)
_OG_RE2 = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.I)
# Site-wide chrome that is NOT the film's image — never adopt these as a poster.
_BAD_IMG = ("logo", "placeholder", "default-", "/default", "fallback",
            "avatar", "sprite", "share-", "/share.", "og-image.png")


# Films TMDB matches WRONGLY (mostly Bulgarian/festival titles whose English
# name collides with a famous foreign film — e.g. "adat-v-neya" matched "The
# Good, the Bad and the Ugly"). TMDB has no correct entry, so any match here is
# a false poster/title. Keep these on the generated SVG artwork instead. Verified
# 2026-09-17; revisit if TMDB later adds a correct entry for one of them.
BLOCKLIST = {
    "adat-v-neya", "cherno-more", "divo-sarce", "fevruari", "kokoshka",
    "pomilvane", "sentimentalno", "vreme-za-zhivot", "zalog",
}


def grab(data, name):
    i = data.find("const " + name + "=")
    j = data.find("=", i) + 1
    depth = 0; start = None
    for k in range(j, len(data)):
        c = data[k]
        if c in "[{":
            if depth == 0: start = k
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                return json.loads(data[start:k + 1])


def api(path, params):
    q = urllib.parse.urlencode(params)
    url = f"https://api.themoviedb.org/3/{path}?{q}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}",
                                               "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=25, context=SSLCTX) as r:
                return json.load(r)
        except Exception as e:
            if attempt == 2:
                print("  ! api error", e, file=sys.stderr); return None
            time.sleep(1.5)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def cnorm(s):
    """Like norm but keeps Cyrillic, so Bulgarian titles can be compared."""
    return re.sub(r"[^a-z0-9а-я]", "", (s or "").lower())


_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")

def _has_cyrillic(s: str) -> bool:
    """Return True if s contains any Cyrillic character."""
    return bool(_CYRILLIC_RE.search(s or ""))


# A match whose release year is further than this from the year the venue prints is
# another film, whatever its title: "Паяци" (2026) is not "Spiders" (2000), "Минотавър"
# (2026) not "Hercules in the Maze of the Minotaur" (1994). A Bulgarian release lags the
# premiere by up to about four years (Stories Not to Be Told: 2022, here 2025), so the
# gap is wider than the ±1 a curated or title-embedded year gets.
MAX_YEAR_GAP = 5


def _release_year(r):
    ry = ((r or {}).get("release_date") or "")[:4]
    return int(ry) if ry.isdigit() else None


def year_gap(year, r):
    """|film year - the TMDB result's release year|, or None when either is unknown."""
    try:
        y = int(year) if year else None
    except (TypeError, ValueError):
        y = None
    ry = _release_year(r)
    return abs(ry - y) if (y and ry) else None


def plausible_year(year, r):
    """False when the venue's year and the match's release year are more than
    MAX_YEAR_GAP apart (a different film); True when they agree or one is unknown."""
    g = year_gap(year, r)
    return g is None or g <= MAX_YEAR_GAP


def year_ok(year, r, rerelease=False):
    """plausible_year(), except that a title with an explicit re-release marker
    (rerelease_marker) may match a film any number of years OLDER than the year the
    venue prints (its re-release year): never a newer one."""
    if plausible_year(year, r):
        return True
    if not rerelease:
        return False
    try:
        y = int(year)
    except (TypeError, ValueError):
        return True
    ry = _release_year(r)
    return ry is not None and ry <= y


# ── Match acceptance: the venue's own facts against the candidate's ───────────
# A title can only nominate a candidate. "ЕВЕРЕСТ: ОТ ДРУГАТА СТРАНА" (Влайкова,
# a 2026 documentary) became The Departed (2006, BG "От другата страна"), "Обич"
# (Sorogoyen, 2026) became Loving (2016), "Диаманти" (Özpetek) a Bond film. The
# venue already says who directed it, how long it runs, whether it is a
# documentary and, on Cine Grand's links, its IMDb id; a candidate that
# contradicts any of these is another film, and "no match" (the cinema's own
# poster) beats a wrong one. Every rule abstains when either side is unknown.
DOC_GENRE = "Документален"
MUSIC_GENRE = "Музикален"
RUNTIME_SLACK = 20            # minutes; venues round, add intermissions, cut credits
_IMDB_IN_URL_RE = re.compile(r"imdb(tt\d{6,10})", re.I)


def _name_tokens(s):
    """A credit line ('Ферзан Йозпетек', 'Jimmy Chin, E. Chai Vasarhelyi') as
    rough Latin sound-alike tokens, so the two scripts compare."""
    from film_identity import _TRANSLIT
    import unicodedata
    s = (s or "").lower().replace("ё", "е")
    s = "".join(_TRANSLIT.get(ch, ch) for ch in s)
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    for a, b in (("dzh", "j"), ("zh", "j"), ("dj", "j"), ("kh", "h"), ("ph", "f"), ("th", "t"),
                 ("ck", "k"), ("w", "v"), ("y", "i"), ("q", "k"), ("x", "ks")):
        s = s.replace(a, b)
    s = re.sub(r"c(?!h)", "k", s)
    s = re.sub(r"(.)\1+", r"\1", s)
    return [t for t in re.split(r"[^a-z]+", s) if len(t) >= 3]


def _skeleton(t):
    """A name token's consonants: Джейкъб Чейс / Jacob Chase -> jkb chs."""
    return re.sub(r"[aeiou]", "", t)


def names_agree(venue, tmdb, bg_names=()):
    """True when a name in the venue's credit line is one of TMDB's (either script),
    False when both are known and none is, None when either is missing."""
    import difflib
    vt = _name_tokens(venue)
    tt = _name_tokens(tmdb) + [t for n in bg_names for t in _name_tokens(n)]
    if not vt or not tt:
        return None
    def same(a, b):
        if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.72:
            return True
        sa, sb = _skeleton(a), _skeleton(b)
        return len(sa) >= 3 and len(sb) >= 3 and difflib.SequenceMatcher(None, sa, sb).ratio() >= 0.8
    return any(same(a, b) for a in vt for b in tt)


def venue_imdb_ids(urls):
    """IMDb ids a venue carries in its own film links (Cine Grand: /m/zvart-imdbtt29256561)."""
    return {m.group(1).lower() for u in urls or () for m in _IMDB_IN_URL_RE.finditer(u or "")}


def contradiction(facts, cand):
    """Why a TMDB candidate cannot be the film the venue lists, or None.

    facts: what the venue/curated record says — year, runtime, genres, director,
           imdb (a set of ids from its links). cand: the candidate as stored in
           tmdb_films.json — tmdb_year, runtime, genres, dir, nb (BG names),
           imdb_id. The same rule runs in main() before a match is kept and in
           verify_build / the regression check over the stored sidecar."""
    facts, cand = facts or {}, cand or {}
    _cy = {"release_date": str(cand.get("tmdb_year") or "")}
    # a marked re-release ("– Специално издание") is the old film: its TMDB year may be
    # long before the year the venue prints, never after it
    older_rerelease = bool(facts.get("rerelease")) and not plausible_year(facts.get("year"), _cy) \
        and year_ok(facts.get("year"), _cy, rerelease=True)
    if not plausible_year(facts.get("year"), _cy) and not older_rerelease:
        return f"year {facts.get('year')} vs TMDB {cand.get('tmdb_year')}"
    ids = {i.lower() for i in facts.get("imdb") or ()}
    if ids and cand.get("imdb_id") and cand["imdb_id"].lower() not in ids:
        return f"venue IMDb {'/'.join(sorted(ids))} vs TMDB {cand['imdb_id']}"
    try:
        vr, tr = int(facts.get("runtime") or 0), int(cand.get("runtime") or 0)
    except (TypeError, ValueError):
        vr = tr = 0
    vg, tg = set(facts.get("genres") or ()), set(cand.get("genres") or ())
    # a concert recording is cut to fit a cinema slot (TXT "ACT: TOMORROW": 160 min
    # on TMDB, 100 at Cinema City), so its runtime proves nothing
    if (vr > 0 and tr > 0 and MUSIC_GENRE not in tg | vg
            and abs(vr - tr) > max(RUNTIME_SLACK, 0.25 * max(vr, tr))):
        return f"runtime {vr} vs TMDB {tr} min"
    if vg and tg and ((DOC_GENRE in vg) != (DOC_GENRE in tg)) and not (vg & tg):
        return (f"venue {'a documentary' if DOC_GENRE in vg else ', '.join(sorted(vg))} vs "
                f"TMDB {', '.join(sorted(tg))}")
    # nb holds the Bulgarian names of all TMDB's people (cast too): it helps the
    # director's own name match across scripts, but with no TMDB director there is
    # nothing to compare (2026-10-29: Топки, TMDB lists its cast and no director, was
    # failed for "Любомир Печев vs the cast")
    nb = (cand.get("nb") or {}).values() if isinstance(cand.get("nb"), dict) else ()
    if cand.get("dir") and names_agree(facts.get("director"), cand.get("dir"), list(nb)) is False:
        return f"director {facts.get('director')} vs TMDB {cand.get('dir')}"
    # The venue's own Latin title (Влайкова's info line "| EVEREST: THE OTHER SIDE |
    # САЩ | 2026 |", a festival's EN/original title): a candidate whose titles share
    # none of them AND whose year is not the venue's (±1) is another film. Both are
    # needed: a festival's international title may differ from TMDB's, and a remake
    # may share the title. Abstains when either side has no title or no year.
    vt = [t for t in facts.get("titles") or () if norm(t)]
    ct = [t for t in (cand.get("en"), cand.get("original_title")) if norm(t)]
    g = year_gap(facts.get("year"), {"release_date": str(cand.get("tmdb_year") or "")})
    if vt and ct and g is not None and g > 1 and not older_rerelease \
            and not any(titles_agree(a, b) for a in vt for b in ct):
        return f"title {vt[0]} ({facts.get('year')}) vs TMDB {ct[0]} ({cand.get('tmdb_year')})"
    return None


def titles_agree(a, b):
    """Two Latin titles name the same film: equal or one contains the other once
    folded (norm), or nearly the same spelling (a dropped article, a typo)."""
    import difflib
    na, nb_ = norm(a), norm(b)
    if not na or not nb_:
        return False
    if na == nb_ or (min(len(na), len(nb_)) >= 4 and (na in nb_ or nb_ in na)):
        return True
    return difflib.SequenceMatcher(None, na, nb_).ratio() >= 0.85


def venue_facts(film, fi=None, urls=()):
    """The facts contradiction() checks, from the film's own record (FILMS: the
    curated seed or the venue-minted record) and film_info.json (the venue's film
    page), never from TMDB."""
    film, fi = film or {}, fi or {}
    d = film.get("director") or fi.get("dir") or ""
    titles = [t.strip() for t in (fi.get("title_en"), fi.get("original"), film.get("originalTitle"))
              if isinstance(t, str) and t.strip() and not _has_cyrillic(t)]
    return {"year": film.get("year") or fi.get("year"),
            "runtime": film.get("runtime") or fi.get("runtime"),
            "genres": film.get("genres") or fi.get("genres") or [],
            "director": "" if d.strip() in ("", "—", "-") else d,
            "imdb": venue_imdb_ids(urls),
            "titles": list(dict.fromkeys(titles)),
            "rerelease": any(rerelease_marker(t) for t in (film.get("bg"), film.get("en"), fi.get("title_bg")))}


# Per-film TMDB ids verified to be ANOTHER film (tmdb_blocklist.json, committed):
# {film id: [tmdb id, …]}. A blocked id is never matched again; any other
# candidate still is, so a correct entry TMDB adds later is picked up.
TMDB_BLOCKLIST_PATH = ROOT / "tmdb_blocklist.json"


def load_tmdb_blocklist(path=None):
    try:
        d = json.load(open(path or TMDB_BLOCKLIST_PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for fid, v in (d.get("blocked") or {}).items() if isinstance(d, dict) else ():
        ids = v.get("tmdb_ids") if isinstance(v, dict) else v
        if isinstance(ids, int):
            ids = [ids]
        out[fid] = {int(i) for i in ids or () if str(i).isdigit()}
    return out


def title_agrees(r, en):
    """The result's title or original title equals or contains ours (or ours contains it)."""
    ten = norm(en)
    if not ten:
        return False
    for t in (norm(r.get("title")), norm(r.get("original_title"))):
        if t and (t == ten or ten in t or t in ten):
            return True
    return False


def older_namesake(results, best, same_title, year):
    """True when `best` fits the venue's year only as a re-release (more than
    MAX_YEAR_GAP before it, year_ok's exemption) and another result with the same
    title is also older than that year: Mulan 1998 and Mulan 2020 for "Мулан –
    Специално издание" (2026). Search order and vote counts then chose between two
    films, so neither is the venue's: no match, unless a curated year, the venue's
    IMDb id or a year within MAX_YEAR_GAP settles it."""
    try:
        y = int(year)
    except (TypeError, ValueError):
        return False
    if best is None or plausible_year(y, best):
        return False
    return any(r is not best and same_title(r) and (_release_year(r) or y + 1) <= y for r in results)


def pick(results, en, year):
    """The best search result for an English title. A result whose title does not agree
    with ours (TMDB also searches alternative titles) is a candidate only when its year
    is within ±1 of the film's: "The Upstairs Neighbours" (2026, Spain) must not become
    "Die Nachbarn von Oben" (2023, Germany) just because nothing else came back."""
    if not results: return None
    results = [r for r in results
               if title_agrees(r, en) or (year_gap(year, r) is not None and year_gap(year, r) <= 1)
               or not year]
    if not results: return None
    ten = norm(en)
    def score(r):
        s = 0
        rt = norm(r.get("title")); ro = norm(r.get("original_title"))
        if rt == ten or ro == ten: s += 100
        elif ten in rt or rt in ten: s += 40
        ry = (r.get("release_date") or "")[:4]
        if ry and year:
            if ry == str(year): s += 50
            elif abs(int(ry) - int(year)) <= 1: s += 20
            else: s -= 10 * min(abs(int(ry) - int(year)), 5)
        s += min(r.get("vote_count", 0), 5000) / 5000.0
        return s
    best = max(results, key=score)
    if older_namesake(results, best, lambda r: ten in (norm(r.get("title")), norm(r.get("original_title"))), year):
        return None
    return best


def pick_bg(results, bg, year):
    """Conservative match for a Bulgarian-only title (synthesised arthouse films
    have no English title). TMDB is queried with language=bg, so each result's
    `title` is its Bulgarian title; accept ONLY a result whose Bulgarian title
    actually matches ours (exact, or strong containment for a title long enough
    to be unambiguous). A loose Cyrillic match is rejected so an arthouse film
    can never pick up a wrong, fabricated poster — it keeps its placeholder."""
    nb = cnorm(bg)
    if not nb or not results:
        return None
    best, bestscore = None, 0.0
    for r in results:
        rt = cnorm(r.get("title")); ro = cnorm(r.get("original_title"))
        s = 0.0
        ry = (r.get("release_date") or "")[:4]
        if nb == rt or nb == ro:
            s = 100.0
        elif len(nb) >= 6 and (nb in rt or (rt and rt in nb)):
            # A title inside another only counts when it is most of it, and a
            # one-word title never on its own: "Началото" is inside "Батман в
            # началото", "Една история" inside a Bob Dylan documentary's long BG
            # title. Then only the venue's year (±1) can confirm it.
            short, long_ = sorted((nb, rt), key=len)
            weak = len((bg or "").split()) < 2 or len(short) < 0.5 * len(long_)
            if weak and not (ry.isdigit() and year and abs(int(ry) - int(year)) <= 1):
                continue
            s = 60.0
        if not s:
            continue
        if ry and year and ry == str(year):
            s += 20.0
        s += min(r.get("vote_count", 0), 5000) / 5000.0
        if s > bestscore:
            best, bestscore = r, s
    if best is None or bestscore < 60.0:
        return None
    if older_namesake(results, best, lambda r: nb in (cnorm(r.get("title")), cnorm(r.get("original_title"))), year):
        return None
    return best


TRENDING_PAGES = 5   # TMDB's weekly trending films, 20 a page: the first 100


def trending_ranks():
    """TMDB's weekly trending films as {tmdb_id: rank}, 1 = first (the home page's
    "На фокус" card shows the highest-ranked film that is playing). None when TMDB does
    not answer at all, so the caller keeps the last ranks."""
    ranks = {}
    for page in range(1, TRENDING_PAGES + 1):
        d = api("trending/movie/week", {"language": "en-US", "page": page})
        if not d or not isinstance(d.get("results"), list):
            break
        for r in d["results"]:
            if isinstance(r, dict) and isinstance(r.get("id"), int) and r["id"] not in ranks:
                ranks[r["id"]] = len(ranks) + 1
    return ranks or None


def apply_trending(out, prev, ranks):
    """Write each matched film's weekly trending rank ("trend") into out. ranks None
    (TMDB did not answer): keep-previous, a film keeps last run's rank if it is still
    the same TMDB title."""
    for fid, rec in out.items():
        rec.pop("trend", None)
        if ranks is None:
            old = prev.get(fid) or {}
            if old.get("trend") and old.get("tmdb_id") == rec.get("tmdb_id"):
                rec["trend"] = old["trend"]
        elif ranks.get(rec.get("tmdb_id")):
            rec["trend"] = ranks[rec["tmdb_id"]]


def strip_series_prefix(title):
    """The title without a festival / series prefix: _SERIES_PREFIX_RE (It's
    Alive!) and film_identity's festival list. A title with no such prefix is
    returned as written."""
    t = (title or "").strip()
    m = _SERIES_PREFIX_RE.match(t)
    if m and t[m.end():].strip():
        t = t[m.end():].strip()
    try:
        from film_identity import strip_prefix
        t = strip_prefix(t) or t
    except ImportError:                            # pragma: no cover
        pass
    return t


def tmdb_hints(path=None):
    """{film id: {"original", "year", "director"}} from the film_aliases.json groups
    that carry a year. A festival title often prints no year ("It’s Alive! Хорър
    Филм Фест Част IV: Нещото"), and a bare Bulgarian title is shared by a classic
    and its remake (The Thing 1982 / 2011, The Wicker Man 1973 / 2006): the year and
    director come from the venue's own film page, the original title only where
    they identify the film. main() searches with them and keeps only a match of
    that year (±1)."""
    try:
        data = json.load(open(path or ALIASES_PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for g in data.get("groups", []) if isinstance(data, dict) else []:
        fid, yr = g.get("film"), g.get("year")
        if fid and isinstance(yr, int) and 1890 <= yr <= 2100:
            out[fid] = {"original": (g.get("original") or "").strip() or None, "year": yr,
                        "director": g.get("director")}
    return out


def stripped_words(title, clean):
    """Words of the title (3 letters or more) that the decluttered query lost."""
    have = set(cnorm(w) for w in (clean or "").split())
    return {cnorm(w) for w in (title or "").split() if len(cnorm(w)) >= 3} - have


def keeps_dropped(r, dropped):
    """The result's own title still carries every word the query lost (Tom & Jerry:
    "ТОМ И ДЖЕРИ: ЗАБРАНЕНИЯТ КОМПАС" may be found as "ЗАБРАНЕНИЯТ КОМПАС" because
    its BG title still says "Том и Джери")."""
    t = cnorm(r.get("title")) + " " + cnorm(r.get("original_title"))
    return all(w in t for w in dropped)


def declutter(title):
    """Strip clutter from a title and return (clean_title, year_or_None).

    Handles:
    - "Режисьор: Заглавие (Година)" — director prefix stripped ONLY when the
      prefix looks like a person's name (2–4 letter-only words) AND the full
      title ends with a 4-digit year in parens or bars.  This avoids stripping
      legitimate subtitle colons ("Venom: The Last Dance", "2001: A Space
      Odyssey", "Mission: Impossible").
    - Festival / ALL-CAPS series prefixes like "СИНЕЛИБРИ 2026 – ",
      "ЕВРОПЕЙСКИ КИНОКЛАСИКИ: " — stripped unconditionally (they match the
      ALL-CAPS guard in _FESTIVAL_PREFIX_RE).
    - Trailing "| 1979 |"-style bars — also extract the year.
    - Re-release / restoration decoration (e.g. "Cars (20th Anniversary)").

    Returns (clean_title_str, extracted_year_int_or_None).
    Callers must search with the FULL title first and use the decluttered form
    only as a fallback (see main()).
    """
    t = strip_series_prefix(title)
    extracted_year = None

    # 1. Festival / ALL-CAPS series prefix — strip unconditionally.
    #    e.g. "СИНЕЛИБРИ 2026 – Заглавие", "ЕВРОПЕЙСКИ КИНОКЛАСИКИ: Филм"
    m = _FESTIVAL_PREFIX_RE.match(t)
    if m:
        t = t[m.end():].strip()

    # 2. Trailing "| 1979 |" bars — strip and capture year.
    m = _TRAILING_YEAR_BARS_RE.search(t)
    if m:
        extracted_year = int(m.group(1))
        t = t[:m.start()].strip()

    # 3. Trailing year in parens, e.g. "Сънища (1990)"
    if extracted_year is None:
        m = re.search(r"\((\d{4})\)\s*$", t)
        if m:
            extracted_year = int(m.group(1))
            t = t[:m.start()].strip()

    # 4. "Director: Title" shape — only when:
    #    a) a year was extracted in steps 2–3 (the year is what marks this as an
    #       arthouse retrospective title, not a franchise subtitle), AND
    #    b) the prefix before ":" looks like a person's name (2–4 letter-only words).
    #    This leaves "Venom: The Last Dance", "2001: A Space Odyssey", etc. intact.
    if extracted_year is not None:
        colon_m = re.match(r"^([^:：]+)[:：]\s*(.+)$", t)
        if colon_m:
            prefix = colon_m.group(1).strip()
            if _PERSON_NAME_PREFIX_RE.match(prefix):
                t = colon_m.group(2).strip()

    # 5. Strip remaining re-release decoration from the tail (parens, then dash-keywords)
    prev = None
    while t and t != prev:
        prev = t
        t = _PAREN_RE.sub("", t).strip()
    t = _DECOR_RE.sub("", t).strip()
    prev = None
    while t and t != prev:
        prev = t
        t = _DECOR_BG_RE.sub("", t).strip()

    return t, extracted_year


# Searches that failed (an API error, not "no results") this run. A film whose search
# failed keeps last run's match instead of dropping to NO MATCH (keep-previous).
def search_bg(bg, syear, blocked=()):
    """The TMDB film for a Bulgarian title, searched with language=bg (pick_bg), or
    None: (best, match_source, match_query). The full title first, then the
    decluttered one, which must keep the words it lost or have the year confirm it;
    a year printed in the title must match (±1)."""
    best, match_source, match_query = None, None, None
    # Always search with the FULL Bulgarian title first.
    res = search_api({"query": bg, "language": "bg",
                               "include_adult": "false"})
    results = [r for r in (res or {}).get("results", []) if r.get("id") not in blocked]
    best = pick_bg(results, bg, syear)
    if best:
        match_source = "bg"
        match_query = bg
    # Fallback: if full title found nothing, try decluttered form
    # (strips "Director: Title (Year)" and festival prefixes).
    clean_bg, extracted_yr_bg = declutter(bg)
    if not best and clean_bg and clean_bg.lower() != bg.lower():
        use_year_bg = extracted_yr_bg if extracted_yr_bg is not None else syear
        res2 = search_api({"query": clean_bg, "language": "bg",
                                    "include_adult": "false"})
        results2 = [r for r in (res2 or {}).get("results", []) if r.get("id") not in blocked]
        best = pick_bg(results2, clean_bg, use_year_bg)
        # The decluttered query lost words that were the title, not clutter:
        # Влайкова prints every title in capitals, so "ЕВЕРЕСТ:" looked like a
        # series prefix and "ОТ ДРУГАТА СТРАНА" found The Departed. Such a match
        # stands only when the result keeps those words or the year confirms it.
        lost = stripped_words(strip_rerelease(bg), clean_bg)
        ry_b = ((best or {}).get("release_date") or "")[:4]
        if best and lost and not keeps_dropped(best, lost) and not (
                use_year_bg and ry_b.isdigit() and abs(int(ry_b) - int(use_year_bg)) <= 1):
            print(f"      (bg match rests on part of the title: {clean_bg!r} without "
                  f"{', '.join(sorted(lost))} — rejected)")
            best = None
        if best:
            match_source = "bg-BG"
            match_query = clean_bg
            print(f"      (bg matched after declutter: {bg!r} -> {clean_bg!r})")
    else:
        extracted_yr_bg = None  # full-title matched; no embedded year to enforce
    # Year mismatch guard — only when the decluttered year was used
    if best and extracted_yr_bg is not None:
        tmdb_yr_str = (best.get("release_date") or "")[:4]
        if tmdb_yr_str and abs(int(tmdb_yr_str) - extracted_yr_bg) > 1:
            print(f"      (bg year mismatch: title year {extracted_yr_bg} "
                  f"vs TMDB {tmdb_yr_str} — rejected)")
            best = None
            match_source = None
    return best, match_source, match_query


SEARCH_ERRORS = 0


def search_api(params):
    """TMDB search/movie, counting a failure in SEARCH_ERRORS."""
    global SEARCH_ERRORS
    res = api("search/movie", params)
    if res is None:
        SEARCH_ERRORS += 1
    return res


def search_en(en, year, blocked=()):
    """Query TMDB by English title (with, then without, the year) and pick best.
    Results whose id is blocked for this film (tmdb_blocklist.json) never count."""
    res = search_api({"query": en, "year": year, "include_adult": "false"})
    results = [r for r in (res or {}).get("results", []) if r.get("id") not in blocked]
    if not results:
        res = search_api({"query": en, "include_adult": "false"})
        results = [r for r in (res or {}).get("results", []) if r.get("id") not in blocked]
    return pick(results, en, year)


def details_raw(tmdb_id):
    """The film's en-US detail record with its credits and videos appended: one call.
    include_video_language widens `videos` (otherwise filtered to en-US) to Bulgarian
    and English trailers. None on an API error."""
    return api(f"movie/{tmdb_id}", {"language": "en-US",
                                     "append_to_response": "credits,videos",
                                     "include_video_language": "bg,en"})


# A YouTube video id, as the page embeds it (youtube-nocookie.com/embed/<key>).
YT_KEY_RE = re.compile(r"[A-Za-z0-9_-]{11}")


def pick_trailer(d):
    """The trailer to offer, from the detail record's appended `videos`: official and on
    YouTube only; a Trailer before a Teaser; Bulgarian before English; the newest of the
    best group. Returns {"k": key, "l": "bg"|"en", "t": "Trailer"|"Teaser"} or None."""
    vids = [v for v in (((d or {}).get("videos") or {}).get("results") or []) if isinstance(v, dict)
            and v.get("site") == "YouTube" and v.get("official") is True
            and YT_KEY_RE.fullmatch(v.get("key") or "")]
    for typ in ("Trailer", "Teaser"):
        for lang in ("bg", "en"):
            group = [v for v in vids if v.get("type") == typ and v.get("iso_639_1") == lang]
            if group:
                best = max(group, key=lambda v: v.get("published_at") or "")
                return {"k": best["key"], "l": lang, "t": typ}
    return None


# TMDB genre id -> the dataset's own (Bulgarian) genre name, so a film the venues
# give no genres can show TMDB's. Ids without a dataset genre (War, TV Movie) are left out.
TMDB_GENRES = {28: "Екшън", 12: "Приключенски", 16: "Анимация", 35: "Комедия", 80: "Криминален",
               99: "Документален", 18: "Драма", 10751: "Семеен", 14: "Фентъзи", 36: "Исторически",
               27: "Хорър", 10402: "Музикален", 9648: "Мистерия", 10749: "Романтика",
               878: "Фантастика", 53: "Трилър", 37: "Уестърн"}


def _crew(crew, jobs, n=3):
    out = []
    for p in crew:
        nm = (p.get("name") or "").strip()
        if p.get("job") in jobs and nm and nm not in out:
            out.append(nm)
    return ", ".join(out[:n]) or None


def details_extra(d):
    """Fields from the same detail record that the pipeline used to drop: runtime,
    genres (dataset names), writer, composer, cinematographer, every production
    country (names and ISO codes) and the trailer. Keys absent when TMDB has nothing."""
    out = {}
    if not d:
        return out
    rt = d.get("runtime")
    if isinstance(rt, int) and 20 <= rt <= 600:
        out["runtime"] = rt
    gs = []
    for g in d.get("genres") or []:
        name = TMDB_GENRES.get((g or {}).get("id"))
        if name and name not in gs:
            gs.append(name)
    if gs:
        out["genres"] = gs[:3]
    crew = ((d.get("credits") or {}).get("crew")) or []
    for key, jobs in (("wr", ("Screenplay", "Writer")), ("mu", ("Original Music Composer", "Music")),
                      ("dop", ("Director of Photography",))):
        v = _crew(crew, jobs)
        if v:
            out[key] = v
    pcs = [c for c in d.get("production_countries") or [] if isinstance(c, dict)]
    names = [(c.get("name") or "").strip() for c in pcs if (c.get("name") or "").strip()]
    if names:
        out["countries"] = " / ".join(names)
    ccs = [c.get("iso_3166_1") for c in pcs if re.fullmatch(r"[A-Z]{2}", c.get("iso_3166_1") or "")]
    if ccs:
        out["cc"] = ccs
    tr = pick_trailer(d)
    if tr:
        out["trailer"] = tr
    pid = credited_people(d)
    if pid:
        out["pid"] = pid
    return out


def credited_people(d):
    """{Latin name: TMDB person id} for everyone the page credits: the director(s), the
    top-5 cast, and up to 3 each for screenplay, music and cinematography (the same
    selection details_en and details_extra make). The ids key person_names.json."""
    credits = (d or {}).get("credits") or {}
    crew = [p for p in credits.get("crew") or [] if isinstance(p, dict)]
    cast = sorted([p for p in credits.get("cast") or [] if isinstance(p, dict)], key=lambda p: p.get("order", 9999))[:5]
    picked = [p for p in crew if p.get("job") == "Director"] + cast
    for jobs in (("Screenplay", "Writer"), ("Original Music Composer", "Music"), ("Director of Photography",)):
        seen = []
        for p in crew:
            nm = (p.get("name") or "").strip()
            if p.get("job") in jobs and nm and nm not in seen:
                seen.append(nm)
                if len(seen) <= 3:
                    picked.append(p)
    out = {}
    for p in picked:
        nm, i = (p.get("name") or "").strip(), p.get("id")
        if nm and isinstance(i, int) and nm not in out:
            out[nm] = i
    return out


# ---------------------------------------------------------------- Bulgarian names of people
# TMDB keeps a person's name per language in /person/{id}/translations: each translation's
# data object has "name" (and "biography"). The Bulgarian entry (iso_639_1 "bg") is the
# spelling TMDB's Bulgarian editors chose ("Роджър Дийкинс"). The person record's
# also_known_as list is NOT used: it mixes scripts and languages with no language tag, so
# a Cyrillic alias there is as likely Russian or Ukrainian as Bulgarian. A bg name is
# accepted only when it is Cyrillic, has no Latin letter and no letter Bulgarian does not
# use (ы э ё і ї є ґ: a Russian or Ukrainian form pasted into the bg slot).
# Cached by person id in person_names.json (committed): {id: {"n": Latin name, "bg": name
# or null, "t": date checked}}. Only people not in the cache, and misses older than
# PERSON_RECHECK_DAYS, cost a call, at most PERSON_LOOKUPS_PER_RUN a run (the first runs
# backfill over a few days); an API error keeps the cached entry as it was.
PERSON_NAMES = None         # None: person_names.json next to OUT (tmdb_films.json), so a run on a temp OUT keeps its cache there
PERSON_LOOKUPS_PER_RUN = 600
PERSON_RECHECK_DAYS = 30
PERSON_SLEEP = 0.03          # ~30 requests a second at most, under TMDB's ~40/s
_NOT_BG_RE = re.compile(r"[ыэёіїєґЫЭЁІЇЄҐ]")


def bg_person_name(tr):
    """The Bulgarian name from a /person/{id}/translations response, or None."""
    for t in ((tr or {}).get("translations") or []):
        if not isinstance(t, dict) or t.get("iso_639_1") != "bg":
            continue
        nm = re.sub(r"\s+", " ", ((t.get("data") or {}).get("name") or "")).strip()
        if nm and _CYRILLIC_RE.search(nm) and not re.search(r"[A-Za-z]", nm) and not _NOT_BG_RE.search(nm):
            return nm
    return None


def update_person_names(out, cache, today, lookups=PERSON_LOOKUPS_PER_RUN, sleep=PERSON_SLEEP):
    """Look up the Bulgarian names of people new to the cache (and stale misses), then
    write each matched film's nb = {Latin name: Bulgarian name}. Returns (cache, stats)."""
    import datetime as _dt
    want = {}
    for rec in out.values():
        for nm, pid in ((rec or {}).get("pid") or {}).items():
            want.setdefault(str(pid), nm)
    stale = (_dt.date.fromisoformat(today) - _dt.timedelta(days=PERSON_RECHECK_DAYS)).isoformat()
    todo = [k for k in sorted(want, key=int) if k not in cache
            or (cache[k].get("bg") is None and (cache[k].get("t") or "") < stale)]
    stats = {"people": len(want), "asked": 0, "found": 0, "errors": 0, "left": max(0, len(todo) - lookups)}
    for k in todo[:lookups]:
        tr = api(f"person/{k}/translations", {})
        stats["asked"] += 1
        if tr is None:
            stats["errors"] += 1          # keep whatever the cache had
        else:
            bg = bg_person_name(tr)
            cache[k] = {"n": want[k], "bg": bg, "t": today}
            stats["found"] += bool(bg)
        if sleep:
            time.sleep(sleep)
    for rec in out.values():
        nb = {nm: cache[str(pid)]["bg"] for nm, pid in ((rec or {}).get("pid") or {}).items()
              if (cache.get(str(pid)) or {}).get("bg")}
        if nb:
            rec["nb"] = nb
        elif isinstance(rec, dict):
            rec.pop("nb", None)
    return cache, stats


# Extra detail fields carried from the previous run when this run's detail call fails.
EXTRA_KEYS = ("runtime", "genres", "wr", "mu", "dop", "countries", "cc", "trailer", "pid")


def details_en(tmdb_id, d=None):
    """English overview + production country + imdb_id + credits from the film's
    detail record (details_raw, or `d` when the caller already has it).
    Owner's choice is 'TMDB English where it exists, else Bulgarian' — no machine
    translation, so this authoritative English fills the detail card when the dataset
    has none. The imdb_id is used to look up the genuine IMDb rating via OMDb.
    Returns (ov, country, imdb_id, dir, cast) where dir and cast are comma-joined
    strings (or None if absent)."""
    if d is None:
        d = details_raw(tmdb_id)
    if not d:
        return None, None, None, None, None
    ov = (d.get("overview") or "").strip() or None
    pcs = d.get("production_countries") or []
    country = (pcs[0].get("name") or "").strip() if pcs else None
    imdb_id = (d.get("imdb_id") or "").strip() or None
    # Extract director(s) and top-5 cast from credits
    credits = d.get("credits") or {}
    crew = credits.get("crew") or []
    directors = [p["name"] for p in crew if p.get("job") == "Director" and p.get("name")]
    dir_ = ", ".join(directors) or None
    cast_list = credits.get("cast") or []
    cast_list_sorted = sorted(cast_list, key=lambda p: p.get("order", 9999))
    cast_names = [p["name"] for p in cast_list_sorted[:5] if p.get("name")]
    cast_ = ", ".join(cast_names) or None
    return ov, (country or None), imdb_id, dir_, cast_


def details_bg(tmdb_id, with_title=False):
    """Bulgarian overview from the film's detail record (language=bg-BG).
    Returns the Bulgarian overview string, or None if absent/empty. with_title:
    (overview, title) instead, title being TMDB's bg-BG title ("" when TMDB answered
    with no Bulgarian one, None when the call failed)."""
    d = api(f"movie/{tmdb_id}", {"language": "bg-BG"})
    if not d:
        return (None, None) if with_title else None
    ov = (d.get("overview") or "").strip() or None
    if not with_title:
        return ov
    t = (d.get("title") or "").strip()
    return ov, (t if _has_cyrillic(t) else "")


# A film without a venue year is matched by its IMDb id only when TMDB dates it this
# recently (a cinema's programme is mostly new films; an old id is a typo or a
# placeholder, Cine Grand's Топки carries tt1234654) or the title marks a re-release.
FIND_RECENT_YEARS = 3


def find_by_venue_imdb(urls, year, blocked=(), rerelease=False, hint=None, today=None):
    """TMDB's film for the IMDb id the venue itself prints in its film link (Cine
    Grand: /m/godzilla-minus-zero-imdbtt34384661), or None. Only one id may be named;
    the film must not be blocked for this film id, and its year must fit: the curated
    year ±1 (film_aliases.json), the venue's year (year_ok), or with no year at all a
    release within FIND_RECENT_YEARS (any older one only for a marked re-release).
    contradiction() still checks it like any other match. The id is returned as
    "_imdb" on the result."""
    ids = venue_imdb_ids(urls)
    if len(ids) != 1:
        return None
    imdb_id = next(iter(ids))
    d = api(f"find/{imdb_id}", {"external_source": "imdb_id", "language": "en-US"})
    if d is None:
        global SEARCH_ERRORS
        SEARCH_ERRORS += 1
        return None
    res = [r for r in (d.get("movie_results") or []) if isinstance(r, dict) and isinstance(r.get("id"), int)]
    if len(res) != 1 or res[0]["id"] in (blocked or ()):
        return None
    r = dict(res[0], _imdb=imdb_id)
    ry = _release_year(r)
    if ry is None:
        return None
    if hint and hint.get("year"):
        return r if abs(ry - int(hint["year"])) <= 1 else None
    if year:
        return r if year_ok(year, r, rerelease) else None
    if imdb_id.lower().startswith("tt1234"):
        # a keyboard-run id with no year to check it against (Топки tt1234654,
        # Безсмъртният и красавицата 2 tt12345871): a placeholder, not the film's id
        return None
    import datetime as _dt
    this_year = (today or _dt.date.today()).year
    return r if (ry >= this_year - FIND_RECENT_YEARS or rerelease) else None


def trailer_year_ok(film_year, tmdb_year):
    """A trailer is offered only when the match's year agrees with the film's within 1
    year (or the film prints no year, so the year shown is TMDB's own). A looser match
    can be another film, and a wrong trailer is worse than none."""
    try:
        fy = int(film_year) if film_year else None
    except (TypeError, ValueError):
        fy = None
    ty = str(tmdb_year or "")[:4]
    if not ty.isdigit():
        return False
    return fy is None or abs(int(ty) - fy) <= 1


def art_record(v, film_year=None):
    """One film's TMDBART entry (the page's short keys) from its tmdb_films.json record.
    inject_data.py writes these; film_year is the year FILMS prints, for the trailer's
    year guard. Returns {} for an unmatched record, except one rated through the IMDb
    id its venue names (OMDB_VIA): only its IMDb / RT / Metacritic scores."""
    if v and not v.get("matched") and v.get("via") == OMDB_VIA and v.get("imdb_id"):
        return {k: v[k] for k in ("imdb", "imdbVotes", "rt", "mc") if v.get(k) is not None and v.get(k) != ""}
    if not v or not v.get("matched"):
        return {}
    rec = {}
    if v.get("poster_path"):   rec["p"] = v["poster_path"]
    if v.get("backdrop_path"): rec["b"] = v["backdrop_path"]
    if v.get("en"):            rec["en"] = v["en"]
    if v.get("ov"):            rec["ov"] = v["ov"]                    # TMDB English synopsis
    if v.get("countries") or v.get("country"):                       # TMDB English, every country
        rec["country"] = v.get("countries") or v["country"]
    if v.get("imdb") is not None: rec["imdb"] = v["imdb"]            # genuine IMDb rating (OMDb)
    if v.get("imdbVotes"):     rec["imdbVotes"] = v["imdbVotes"]     # IMDb vote count (OMDb)
    if v.get("rt") is not None:  rec["rt"] = v["rt"]                 # Rotten Tomatoes critics % (OMDb)
    if v.get("mc") is not None:  rec["mc"] = v["mc"]                 # Metacritic score /100 (OMDb)
    if v.get("tmdb_vote") is not None: rec["tv"] = v["tmdb_vote"]   # TMDB vote_average
    if v.get("vote_count") is not None: rec["tc"] = v["vote_count"] # TMDB vote count
    if v.get("dir"):           rec["dir"] = v["dir"]                 # director(s) from TMDB credits
    if v.get("cast"):          rec["cast"] = v["cast"]               # top-5 cast from TMDB credits
    if v.get("ovBg"):          rec["ovBg"] = v["ovBg"]               # Bulgarian overview from TMDB
    if _has_cyrillic(v.get("tbg")): rec["tb"] = v["tbg"]               # TMDB's bg-BG title of the match
    if v.get("trend"):         rec["tr"] = v["trend"]                # rank on TMDB's weekly trending list (1 = first)
    ty = str(v.get("tmdb_year") or "")[:4]
    if ty.isdigit():           rec["y"] = int(ty)                    # release year (fallback)
    if isinstance(v.get("runtime"), int): rec["rn"] = v["runtime"]   # minutes (fallback)
    if v.get("genres"):        rec["g"] = list(v["genres"])          # dataset genre names (fallback)
    for k in ("wr", "mu", "dop"):                                    # writer, composer, cinematographer
        if v.get(k):           rec[k] = v[k]
    if isinstance(v.get("nb"), dict) and v["nb"]:                    # TMDB's Bulgarian names of the people
        rec["nb"] = {k: n for k, n in v["nb"].items() if isinstance(k, str) and isinstance(n, str)}
    if v.get("orig_lang"):     rec["ol"] = v["orig_lang"]            # ISO 639-1 original language
    if v.get("cc"):            rec["cc"] = list(v["cc"])             # ISO 3166-1 production countries
    tr = v.get("trailer")
    if (isinstance(tr, dict) and YT_KEY_RE.fullmatch(str(tr.get("k") or ""))
            and tr.get("l") in ("bg", "en") and tr.get("t") in ("Trailer", "Teaser")
            and trailer_year_ok(film_year, v.get("tmdb_year"))):
        rec["v"] = {"k": tr["k"], "l": tr["l"], "t": tr["t"]}       # YouTube trailer (key, language, type)
    return rec


def _votes_compact(n):
    """491700 -> '491.7K', 2_000_000 -> '2.0M' — matches the dataset's vote style."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


OMDB_RETRY_SLEEP = 1.5      # seconds before the one in-run retry of a network error
_OMDB_NO_RETRY = ("invalid api key", "request limit", "no api key", "incorrect imdb id")


def _omdb_get(imdb_id):
    """One OMDb answer for imdb_id as a dict, or raises. A network error (timeout,
    connection reset, a 5xx) is retried once in the run after OMDB_RETRY_SLEEP; an
    answer OMDb gives on purpose (Response=False: a wrong key, the daily limit) is not."""
    q = urllib.parse.urlencode({"i": imdb_id, "apikey": OMDB_TOKEN})
    last = None
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(f"https://www.omdbapi.com/?{q}",
                                         headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20, context=SSLCTX) as r:
                return json.load(r)
        except Exception as e:                       # network, HTTP 5xx, a broken body
            last = e
            if attempt == 1:
                print(f"    ! omdb network error {imdb_id}: {e} (retrying once)", file=sys.stderr)
                time.sleep(OMDB_RETRY_SLEEP)
    raise last


def _omdb_int(v):
    m = re.match(r"\s*(\d+)", str(v or ""))
    return int(m.group(1)) if m else None


def omdb_lookup(imdb_id):
    """Everything OMDb says about imdb_id, as a dict: rating (float), votes (compact
    text), rt (Rotten Tomatoes critics' %, int), mc (Metacritic /100, int), err (None
    on success, a string on any failure: no key, an API error, the network), and the
    film's own facts for an identity check: title, year (int), runtime (int minutes),
    type ("movie", "series", ...). Never fabricates: 'N/A' and every failure give None.
    TMDB's own vote_average is NOT used here: it is a different metric, not IMDb."""
    out = {"rating": None, "votes": None, "rt": None, "mc": None, "err": None,
           "title": None, "year": None, "runtime": None, "type": None}
    if not imdb_id:
        return out
    if not OMDB_TOKEN:
        # No key this run (a missing or misnamed secret) is an outage, not "this film
        # has no ratings": the error makes main() keep last run's values. Returning
        # no error here dropped every IMDb rating on 13 Oct 2026.
        out["err"] = "OMDB_TOKEN not set"
        return out
    try:
        d = _omdb_get(imdb_id)
    except Exception as e:
        err = str(e)
        print(f"    ! omdb network error {imdb_id}: {err}", file=sys.stderr)
        out["err"] = f"network: {err}"
        return out
    if not isinstance(d, dict) or d.get("Response") == "False":
        # OMDb returns an Error field: "Invalid API key!", "Request limit reached!", etc.
        err = (d.get("Error") if isinstance(d, dict) else None) or "Response=False"
        print(f"    ! omdb api error {imdb_id}: {err}", file=sys.stderr)
        out["err"] = err
        return out
    rating = d.get("imdbRating")
    try:
        out["rating"] = float(rating) if rating and rating != "N/A" else None
    except (ValueError, TypeError):
        out["rating"] = None
    votes_raw = (d.get("imdbVotes") or "").replace(",", "")
    out["votes"] = _votes_compact(int(votes_raw)) if votes_raw.isdigit() else None
    # Rotten Tomatoes critics' score from the Ratings array ("90%"), Metacritic from
    # the array ("66/100") or the Metascore field.
    for src in (d.get("Ratings") or []):
        if src.get("Source") == "Rotten Tomatoes":
            val = (src.get("Value") or "").rstrip("%")
            out["rt"] = int(val) if val.isdigit() else None
        elif src.get("Source") == "Metacritic":
            val = (src.get("Value") or "").split("/")[0]
            out["mc"] = int(val) if val.isdigit() else None
    if out["mc"] is None:
        ms = (d.get("Metascore") or "")
        if ms.isdigit():
            out["mc"] = int(ms)
    t = d.get("Title")
    out["title"] = t.strip() if isinstance(t, str) and t.strip() and t != "N/A" else None
    y = re.match(r"\s*(\d{4})", str(d.get("Year") or ""))
    out["year"] = int(y.group(1)) if y else None
    out["runtime"] = _omdb_int(d.get("Runtime"))
    ty = d.get("Type")
    out["type"] = ty.strip().lower() if isinstance(ty, str) and ty.strip() else None
    return out


def omdb_rating(imdb_id):
    """Genuine IMDb rating + vote count + RT critics % + Metacritic score via OMDb,
    keyed by the film's imdb_id. Returns
    (rating_float, votes_compact, rt_int, mc_int, error_text): omdb_lookup() without
    the film's facts. error_text is None on success, a string on any failure."""
    o = omdb_lookup(imdb_id)
    return o["rating"], o["votes"], o["rt"], o["mc"], o["err"]


# How a rating reached a film TMDB could not give it: the venue's own page names the
# film's IMDb id (Cine Grand: /m/zvart-imdbtt29256561) and OMDb's facts for that id
# agree with the venue's (omdb_identity). The record stays unmatched (no TMDB art,
# title or synopsis); art_record emits only the scores.
OMDB_VIA = "omdb-venue-imdb"


def omdb_identity(facts, o, tmdb_year=None, imdb_id=None):
    """Why OMDb's film o (omdb_lookup) cannot be the film the venue lists, or None.
    A venue's IMDb id is not proof by itself (Cine Grand has printed placeholder ids
    such as tt1234654 for a 2026 film), so OMDb's own year, runtime and title go
    through contradiction() like a TMDB candidate, and something the venue (or the
    TMDB match, tmdb_year) prints must confirm it: its year (±1) or a Latin title."""
    if not o or o.get("err") is not None:
        return "no OMDb answer"
    if o.get("type") and o["type"] != "movie":
        return f"OMDb lists a {o['type']}, not a film"
    cand = {"tmdb_year": o.get("year"), "runtime": o.get("runtime"),
            "en": o.get("title"), "original_title": None}
    why = contradiction(facts, cand)
    if why:
        return why.replace("TMDB", "OMDb")
    yr = facts.get("year") or tmdb_year
    # a keyboard-run id (tt1234654, tt12345871: find_by_venue_imdb) needs the title too
    keyboard = str(imdb_id or "").lower().startswith("tt1234")
    try:
        if yr and o.get("year") and abs(int(yr) - int(o["year"])) <= 1 and not keyboard:
            return None
    except (TypeError, ValueError):
        pass
    if o.get("title") and any(titles_agree(t, o["title"]) for t in facts.get("titles") or ()):
        return None
    return (f"nothing the venue prints confirms OMDb's {o.get('title') or '?'} "
            f"({o.get('year') or '?'})")


def omdb_only_record(fid, en, facts, urls, prev):
    """The record of a film TMDB did not match, rated through the IMDb id its venue
    names (OMDB_VIA), or None. Exactly one venue IMDb id, OMDb's facts confirmed by
    the venue's (omdb_identity). On an OMDb error, last run's record for the same id
    is kept; an id the venue stopped naming, or one OMDb now contradicts, drops it."""
    ids = sorted(venue_imdb_ids(urls))
    if len(ids) != 1:
        return None
    iid = ids[0]
    pv = (prev or {}).get(fid) or {}
    same = pv.get("via") == OMDB_VIA and pv.get("imdb_id") == iid
    o = omdb_lookup(iid)
    if o["err"] is not None:
        return dict(pv) if same else None
    why = omdb_identity(facts, o, imdb_id=iid)
    if why:
        print(f"      (venue IMDb {iid}: {why} — no rating taken)")
        return None
    rec = {"matched": False, "en": en, "via": OMDB_VIA, "imdb_id": iid,
           "omdb": {k: v for k, v in (("t", o["title"]), ("y", o["year"]), ("rn", o["runtime"])) if v}}
    rating, votes, rt, mc = o["rating"], o["votes"], o["rt"], o["mc"]
    if same and rating is None:            # an IMDb rating does not disappear (keep-previous)
        rating = pv.get("imdb")
        votes = votes or pv.get("imdbVotes")
        rt = rt if rt is not None else pv.get("rt")
        mc = mc if mc is not None else pv.get("mc")
    for k, v in (("imdb", rating), ("imdbVotes", votes), ("rt", rt), ("mc", mc)):
        if v is not None and v != "":
            rec[k] = v
    print(f"      rated through the venue's IMDb id {iid}: {o['title']} ({o['year']})"
          f" imdb={rec.get('imdb')} rt={rec.get('rt')} mc={rec.get('mc')}")
    return rec


def ascii_url(url):
    """`url` with every non-ASCII character (and space) percent-encoded as UTF-8:
    urllib sends ASCII only, and Дом на киното's film pages carry a typographic
    apostrophe in their path ("…/it’s-alive-…"). Everything else — reserved
    characters, existing %-escapes — is left as it is."""
    return re.sub(r"[^\x21-\x7e]", lambda m: urllib.parse.quote(m.group(0)), url)


def og_image(url):
    """Best-effort og:image from a film's own programme page. Returns a usable image
    URL or None — site chrome (logos, share images) is rejected, never fabricated."""
    if not url:
        return None
    try:
        req = urllib.request.Request(ascii_url(url), headers={"User-Agent": UA, "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=20, context=SSLCTX) as r:
            html = r.read(300000).decode("utf-8", "replace")
    except Exception as e:
        print("    ! og fetch error", e, file=sys.stderr)
        return None
    for rx in (_OG_RE, _OG_RE2):
        m = rx.search(html)
        if m:
            u = m.group(1).strip()
            if u.startswith("//"):
                u = "https:" + u
            if not u.lower().startswith("http"):
                continue
            if any(b in u.lower() for b in _BAD_IMG):
                return None
            host = urllib.parse.urlparse(u).netloc.lower()
            if any(host == h or host.endswith("." + h) for h in DISTRUSTED_HOSTS):
                return None     # a ticketing backend's image can't be tied to the title
            return u
    return None


def main():
    if not TOKEN:
        print("TMDB_TOKEN not set — skipping film posters, keeping previous.", file=sys.stderr)
        return 0
    data = HTML.read_text(encoding="utf-8").split("/* SOFIA-DATA-START */")[1].split("/* SOFIA-DATA-END */")[0]
    films = grab(data, "FILMS")
    # id -> detail-page URL (for the og:image fallback). LINKS is a list of [id, url].
    links = {}
    try:
        for row in (grab(data, "LINKS") or []):
            if isinstance(row, (list, tuple)) and len(row) >= 2 and row[0]:
                links.setdefault(row[0], row[1])
    except Exception as e:
        print("  (could not read LINKS for og:image fallback:", e, ")", file=sys.stderr)
    # Carry forward previously-harvested og:image posters so a transient fetch
    # failure never blanks a film that had a real image last run (keep-previous).
    film_links = {}
    if LINKS_OUT.exists():
        try:
            film_links = json.load(open(LINKS_OUT, encoding="utf-8")) or {}
        except Exception:
            film_links = {}
    # Previous run's records, so a missing OMDB_TOKEN or a transient OMDb failure
    # inherits last run's IMDb rating/votes rather than blanking the score tile.
    prev = {}
    if OUT.exists():
        try:
            prev = json.load(open(OUT, encoding="utf-8")) or {}
        except Exception:
            prev = {}

    out = {}
    hints = tmdb_hints()
    blocked_ids = load_tmdb_blocklist()
    # Every venue URL per film (LINKS + VLINKS): a Cine Grand link names the IMDb id.
    film_urls = {}
    for name in ("LINKS", "VLINKS"):
        try:
            for row in (grab(data, name) or []):
                if isinstance(row, (list, tuple)) and len(row) >= 2 and row[0]:
                    film_urls.setdefault(row[0], []).append(row[-1])
        except Exception:
            pass
    # film_info.json: festival/venue details (title_bg, title_en, original, …).
    # Used as fallback sources for the EN title when FILMS carries nothing.
    film_info: dict = {}
    try:
        film_info = json.load(open(FILM_INFO_PATH, encoding="utf-8")) if FILM_INFO_PATH.exists() else {}
    except Exception as e:
        print(f"  (could not read film_info.json: {e})", file=sys.stderr)
    for i, f in enumerate(films):
        fid = f["id"]
        en, bg, year = f.get("en"), f.get("bg"), f.get("year")
        fi = film_info.get(fid) or {}
        year = year or fi.get("year")            # the venue's / festival's own page
        blocked = blocked_ids.get(fid, set())
        # A curated year / original title (film_aliases.json) for a yearless title:
        # search "The Thing" (1982), never the bare "Нещото" that is also the 2011 film.
        hint = hints.get(fid)
        if hint:
            year = year or hint["year"]
            en = en or hint["original"]
        # Fallback chain for films the alias list doesn't cover:
        #   1. FILMS.originalTitle (set during minting from festival `original_title`)
        #   2. festival/venue title_en from film_info.json (written by festival_details)
        # Both require non-Cyrillic content; the year guard ±1 still applies downstream.
        if not en:
            ot = (f.get("originalTitle") or "").strip()
            if ot and not _has_cyrillic(ot):
                en = ot
        if not en:
            for fi_en in ((fi.get("title_en") or "").strip(), (fi.get("original") or "").strip()):
                if fi_en and not _has_cyrillic(fi_en):
                    en = fi_en
                    break
        # "– Специално издание", "(20th Anniversary)": an old film shown again, whose
        # TMDB year may be decades before the year the venue prints (year_ok)
        rerel = any(rerelease_marker(t) for t in (f.get("bg"), f.get("en"), fi.get("title_bg")))
        # ...and its curated year (film_aliases.json) names WHICH old film: searched with
        # it, Mulan 1998 is told from Mulan 2020 (older_namesake rejects such a tie)
        syear = hint["year"] if (hint and rerel) else year
        if fid in BLOCKLIST:
            print(f"[{i+1}/{len(films)}] {fid:24s} BLOCKED (known false match — generated art kept)")
            out[fid] = {"matched": False, "en": en}
            # TMDB has no correct entry, but the film's own programme page does — a
            # real image for this exact title beats the grey gradient.
            img = og_image(links.get(fid))
            if img:
                film_links[fid] = img
                print(f"      og:image -> {img}")
            continue
        # Synthesised arthouse films carry only a Bulgarian title; fall back to it
        # (confirmed via language=bg in pick_bg) so posters/ratings fill where TMDB
        # has them, without ever accepting a loose, fabricated match.
        via_bg = False
        match_source = None   # for the log: bg / original / en
        match_query = None    # the query that found it (stored as "q": verify_build reads it)
        _search_err0 = SEARCH_ERRORS
        # A festival prefix is not part of the title: search without it from the
        # start ("Хорър Филм Фест: Сиянието" -> "Сиянието").
        en, bg = strip_series_prefix(en) or en, strip_series_prefix(bg) or bg
        # Track whether 'en' came from a fallback source (alias original, FILMS.originalTitle,
        # or film_info.title_en) rather than the film's own FILMS.en field.  Used for the
        # match_source log label ("original") which downstream code stores verbatim.
        _f_en = f.get("en")   # original FILMS.en value before any fallback was applied
        _en_is_original = (not _f_en) and bool(en)
        # The venue names the film's IMDb id itself (Cine Grand: /m/zvart-imdbtt29256561):
        # TMDB's film with that id is the venue's film, whatever the title, provided its
        # year fits (find_by_venue_imdb). The searches below run only when it gives none.
        best = find_by_venue_imdb(film_urls.get(fid), year, blocked, rerel, hint)
        if best:
            match_source, match_query = "imdb", best.get("_imdb")
            print(f"      (found by the venue's IMDb id {best.get('_imdb')})")
        elif en:
            best = search_en(en, syear, blocked)
            if best:
                match_source = "original" if _en_is_original else "en"
                match_query = en
            if not best:
                # Re-release decoration / Director-prefix clutter blocks the match;
                # retry with the canonical title (and any year extracted from it).
                clean, extracted_yr = declutter(en)
                use_year = extracted_yr if extracted_yr is not None else syear
                if clean and clean.lower() != en.lower():
                    best = search_en(clean, use_year, blocked)
                    if best:
                        match_source = "original" if _en_is_original else "en"
                        match_query = clean
                        print(f"      (matched after declutter: {en!r} -> {clean!r})")
            # Nothing for the Latin title (a festival's working title, a transliteration
            # TMDB does not carry): the venue's Bulgarian title, with every guard below
            # (the year printed in the title, year_ok, contradiction) still applying.
            if not best and bg and _has_cyrillic(bg) and norm(bg) != norm(en):
                best, match_source, match_query = search_bg(bg, syear, blocked)
                if best:
                    via_bg = True
                    print(f"      (matched by the Bulgarian title {bg!r}: nothing for {en!r})")
            # When a year was embedded in the title, REQUIRE the match's release
            # year to be within ±1 so a retrospective can't pick up a remake.
            if best:
                _, embedded_yr = declutter(en)
                if embedded_yr is not None:
                    tmdb_yr_str = (best.get("release_date") or "")[:4]
                    if tmdb_yr_str and abs(int(tmdb_yr_str) - embedded_yr) > 1:
                        print(f"      (year mismatch: title year {embedded_yr} "
                              f"vs TMDB {tmdb_yr_str} — rejected)")
                        best = None
                        match_source = None
        elif bg:
            via_bg = True
            best, match_source, match_query = search_bg(bg, syear, blocked)
        else:
            best = None
        if best and hint:
            tmdb_yr_str = (best.get("release_date") or "")[:4]
            if not tmdb_yr_str.isdigit() or abs(int(tmdb_yr_str) - hint["year"]) > 1:
                print(f"      (curated year {hint['year']} vs TMDB {tmdb_yr_str or '?'} — rejected)")
                best = None
        # When the EN title came from a fallback source (FILMS.originalTitle or
        # film_info.title_en) and we know the film's year, apply the same ±1 year
        # guard so a same-title remake in a different decade cannot steal the poster.
        if best and _en_is_original and year and not hint and match_source != "imdb":
            tmdb_yr_str = (best.get("release_date") or "")[:4]
            if tmdb_yr_str.isdigit() and abs(int(tmdb_yr_str) - int(year)) > 1 and not (
                    rerel and int(tmdb_yr_str) <= int(year)):
                print(f"      (fallback year {year} vs TMDB {tmdb_yr_str} — rejected)")
                best = None
        # Whatever path found it, a match far from the venue's own year is another film
        # (Паяци 2026 -> Spiders 2000, Минотавър 2026 -> a 1994 TV film).
        if best and not year_ok(year, best, rerel):
            print(f"      (year {year} vs TMDB {(best.get('release_date') or '')[:4]}: more than "
                  f"{MAX_YEAR_GAP} years apart, another film — rejected)")
            best = None
        _pv = prev.get(fid) or {}
        if (not best and SEARCH_ERRORS > _search_err0 and _pv.get("matched")
                and _pv.get("tmdb_id") not in blocked
                and plausible_year(year, {"release_date": str(_pv.get("tmdb_year") or "")})
                and not contradiction(venue_facts(f, fi, film_urls.get(fid)), _pv)):
            # The search itself failed (TMDB error, not "no results"): keep last run's
            # record rather than blanking the film's poster and details until the next run.
            print(f"[{i+1}/{len(films)}] {fid:24s} search failed — keeping last run's match")
            out[fid] = _pv
            continue
        if not best:
            print(f"[{i+1}/{len(films)}] {fid:24s} NO MATCH ({en or bg} {year})")
            # TMDB has no film for it, but the venue may name its IMDb id: the IMDb,
            # RT and Metacritic scores of that id, when OMDb's facts agree with the venue's
            out[fid] = (omdb_only_record(fid, en, venue_facts(f, fi, film_urls.get(fid)),
                                         film_urls.get(fid), prev)
                        or {"matched": False, "en": en})
            img = og_image(links.get(fid))
            if img:
                film_links[fid] = img
                print(f"      og:image -> {img}")
            continue
        # When matched via the Bulgarian title the result's `title` is Bulgarian,
        # so only trust `original_title` as an English name when the film is
        # actually English-language; otherwise leave en empty and let the UI show
        # the Bulgarian title rather than invent an English one.
        if via_bg:
            en_title = best.get("original_title") if best.get("original_language") == "en" else (en or "")
        else:
            en_title = best.get("title") or en
        rec = {
            "matched": True,
            "tmdb_id": best["id"],
            "poster_path": best.get("poster_path"),
            "backdrop_path": best.get("backdrop_path"),
            "en": en_title,
            "original_title": best.get("original_title"),
            "orig_lang": best.get("original_language"),
            "tmdb_year": (best.get("release_date") or "")[:4],
            "tmdb_vote": best.get("vote_average"),
            "vote_count": best.get("vote_count"),
            # how it was found: verify_build re-checks a match found on part of the title
            "via": match_source,
            "q": match_query,
        }
        if via_bg and _has_cyrillic(best.get("title")):
            rec["tbg"] = best.get("title")      # TMDB's Bulgarian title of the match
        # Owner's choice: fill English synopsis/country from TMDB where it exists,
        # Bulgarian otherwise. Theatre shows aren't in TMDB, so this is films only.
        # Also fetch director/cast from credits (append_to_response=credits).
        d = details_raw(best["id"])
        ov, country, imdb_id, dir_, cast_ = details_en(best["id"], d)
        if ov:
            rec["ov"] = ov
        if country:
            rec["country"] = country
        # Keep-previous: inherit previous dir/cast/ovBg if this call returns nothing.
        pv = prev.get(fid, {})
        # Runtime, genres, writer, composer, cinematographer, all countries, the trailer:
        # from the same call. On an API error (d is None) last run's values stay, if it
        # was the same TMDB film; when TMDB answers without a trailer, none is offered.
        if d:
            rec.update(details_extra(d))
        elif pv.get("tmdb_id") == rec["tmdb_id"]:
            rec.update({k: pv[k] for k in EXTRA_KEYS if pv.get(k)})
        if dir_:
            rec["dir"] = dir_
        elif pv.get("dir"):
            rec["dir"] = pv["dir"]
        # The venue's own facts against the candidate's (contradiction()): a match
        # whose year, runtime, documentary-ness, director or IMDb id the venue
        # contradicts is another film. No match keeps the cinema's own poster.
        # rec carries no imdb_id yet (it is stored further down): pass it here, or the
        # venue-IMDb rule (Cine Grand's /m/zvart-imdbtt29256561) could never fire.
        why = contradiction(venue_facts(f, fi, film_urls.get(fid)), dict(rec, imdb_id=imdb_id))
        if why:
            print(f"[{i+1}/{len(films)}] {fid:24s} REJECTED {rec['en'] or rec['original_title']} "
                  f"({rec['tmdb_year']}) id={rec['tmdb_id']}: {why}")
            out[fid] = (omdb_only_record(fid, en, venue_facts(f, fi, film_urls.get(fid)),
                                         film_urls.get(fid), prev)
                        or {"matched": False, "en": en})
            img = og_image(links.get(fid))
            if img:
                film_links[fid] = img
                print(f"      og:image -> {img}")
            continue
        if cast_:
            rec["cast"] = cast_
        elif pv.get("cast"):
            rec["cast"] = pv["cast"]
        # Bulgarian overview and title: a separate call with language=bg-BG. The title
        # (tbg, TMDB's bg-BG translation of this verified match) is what the page may
        # show in Bulgarian when the venues print only a Latin title that is not the
        # film's international one (art_record "tb").
        ov_bg, t_bg = details_bg(best["id"], with_title=True)
        if ov_bg:
            rec["ovBg"] = ov_bg
        elif pv.get("ovBg") and pv.get("tmdb_id") == rec["tmdb_id"]:
            rec["ovBg"] = pv["ovBg"]
        if t_bg and _has_cyrillic(t_bg):
            rec.setdefault("tbg", t_bg)
        elif t_bg is None and pv.get("tbg") and pv.get("tmdb_id") == rec["tmdb_id"]:
            rec.setdefault("tbg", pv["tbg"])
        # Genuine IMDb rating via OMDb (keyed by TMDB's imdb_id). The score tile is
        # labelled "IMDb", so only a real IMDb number may fill it — never TMDB's own
        # vote_average. Retry on every run: films with imdb_id but no cached imdb are
        # attempted again so a transient error or fixed token recovers automatically.
        # Keep-previous: inherit last run's rating only on network/API error so a
        # None is never cached as "final" — the next run will retry.
        pv = prev.get(fid, {})
        if not imdb_id and d is None and pv.get("tmdb_id") == rec["tmdb_id"] and pv.get("imdb_id"):
            # The detail call failed: last run's IMDb id of this same TMDB film, so its
            # rating is looked up (or kept) instead of dropped until the next run.
            imdb_id = pv["imdb_id"]
        # TMDB's film carries no IMDb id, but the venue names one (Cine Grand): it is
        # used when OMDb's facts for it agree with the venue's and with this TMDB film's
        # year (omdb_identity); an outage keeps last run's id for this same TMDB film.
        omdb_pre = None
        if not imdb_id:
            _vids = sorted(venue_imdb_ids(film_urls.get(fid)))
            if len(_vids) == 1:
                omdb_pre = omdb_lookup(_vids[0])
                if omdb_pre["err"] is not None:
                    if pv.get("tmdb_id") == rec["tmdb_id"] and pv.get("imdb_id") == _vids[0]:
                        imdb_id = _vids[0]
                else:
                    _why_o = omdb_identity(venue_facts(f, fi, film_urls.get(fid)), omdb_pre,
                                           tmdb_year=rec.get("tmdb_year"), imdb_id=_vids[0])
                    if _why_o:
                        print(f"      (venue IMDb {_vids[0]}: {_why_o} — not used)")
                        omdb_pre = None
                    else:
                        imdb_id = _vids[0]
                        print(f"      (IMDb id {imdb_id} from the venue: TMDB lists none)")
        if imdb_id:
            rec["imdb_id"] = imdb_id
        _o = (omdb_pre if (omdb_pre and omdb_pre["err"] is None) else omdb_lookup(imdb_id) if imdb_id
              else {"rating": None, "votes": None, "rt": None, "mc": None, "err": None})
        rating, votes, rt_score, mc_score, omdb_err = _o["rating"], _o["votes"], _o["rt"], _o["mc"], _o["err"]
        # Last run's values are inherited only for the SAME IMDb id (a film matched to
        # another TMDB entry this run never shows the old film's score), on an OMDb error
        # or when OMDb answers without a number it had before (an IMDb rating does not
        # disappear; OMDb's N/A for a rated film is a glitch).
        if imdb_id and pv.get("imdb_id") == imdb_id and (omdb_err is not None or rating is None):
            if rating is None and pv.get("imdb") is not None:
                rating = pv["imdb"]
            if not votes and pv.get("imdbVotes"):
                votes = pv["imdbVotes"]
            if rt_score is None and pv.get("rt") is not None:
                rt_score = pv["rt"]
            if mc_score is None and pv.get("mc") is not None:
                mc_score = pv["mc"]
        if rating is not None:
            rec["imdb"] = rating
        if votes:
            rec["imdbVotes"] = votes
        if rt_score is not None:
            rec["rt"] = rt_score
        if mc_score is not None:
            rec["mc"] = mc_score
        out[fid] = rec
        # A matched film with no TMDB poster still has its own programme-page image.
        if not rec["poster_path"]:
            img = og_image(links.get(fid))
            if img:
                film_links[fid] = img
                print(f"      og:image -> {img}")
        else:
            # Now has a verified TMDB poster: drop any stale og fallback so the
            # higher-priority POSTERS override can never shadow the better image.
            film_links.pop(fid, None)
        flag = "" if rec["poster_path"] else "  (no poster!)"
        src_tag = f"  [{match_source}]" if match_source else ""
        print(f"[{i+1}/{len(films)}] {fid:24s} -> {rec['en']} ({rec['tmdb_year']}) id={rec['tmdb_id']}{src_tag}{flag}")
        time.sleep(0.12)

    # Bulgarian spellings of the credited people (person_names.json, keep-previous)
    _pn_path = PERSON_NAMES or OUT.parent / "person_names.json"
    try:
        _pn = json.load(open(_pn_path, encoding="utf-8")) if _pn_path.exists() else {}
    except (OSError, ValueError):
        _pn = {}
    import datetime as _dt
    _pn, _ps = update_person_names(out, _pn, _dt.date.today().isoformat())
    print(f"people: {_ps['people']} credited, {_ps['asked']} looked up ({_ps['found']} with a Bulgarian name, "
          f"{_ps['errors']} errors kept), {_ps['left']} left for later runs")
    apply_trending(out, prev, trending_ranks())
    print(f"trending this week: {sum(1 for v in out.values() if v.get('trend'))} listed film(s) ranked")
    posters = sum(1 for v in out.values() if v.get("poster_path"))
    # keep-previous guard: never overwrite a good cache with an empty run
    if posters == 0 and OUT.exists():
        print("0 posters this run — keeping previous tmdb_films.json", file=sys.stderr)
        return 0
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(dict(sorted(_pn.items(), key=lambda kv: int(kv[0]))), open(_pn_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)
    # Only films without a TMDB poster appear here, so this override map stays a
    # pure fallback and never shadows a verified TMDB poster.
    film_links = {k: v for k, v in film_links.items() if v}
    json.dump(film_links, open(LINKS_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    matched = sum(1 for v in out.values() if v.get("matched"))
    rated = sum(1 for v in out.values() if v.get("imdb") is not None)
    rt_count = sum(1 for v in out.values() if v.get("rt") is not None)
    mc_count = sum(1 for v in out.values() if v.get("mc") is not None)
    omdb_tried = sum(1 for v in out.values() if v.get("imdb_id"))
    omdb_ok = rated  # films with an actual IMDb number
    omdb_fail = omdb_tried - omdb_ok
    print(f"\nDONE: {matched}/{len(films)} matched, {posters} with posters, "
          f"{len(film_links)} og:image fallbacks, {rated} with IMDb ratings, "
          f"{rt_count} with RT, {mc_count} with MC, "
          f"{sum(1 for v in out.values() if v.get('via') == OMDB_VIA)} rated through the venue's IMDb id "
          f"(OMDb {'on' if OMDB_TOKEN else 'OFF — ratings inherited'}, "
          f"{omdb_ok} ok / {omdb_fail} missing) -> {OUT.name}")
    # Append a health entry to health.json (created by P1A; create if absent).
    health_path = HEALTH
    try:
        health = json.load(open(health_path, encoding="utf-8")) if health_path.exists() else []
        # Remove any previous omdb entries so we don't accumulate stale ones
        health = [e for e in health if e.get("venue") != "omdb"]
        if OMDB_TOKEN:
            level = "warn" if omdb_fail > 0 else "ok"
            msg = (f"OMDb: {omdb_ok}/{omdb_tried} ok, {omdb_fail} missing IMDb ratings"
                   if omdb_fail > 0 else f"OMDb: {omdb_ok}/{omdb_tried} ok")
        else:
            level = "warn"
            msg = "OMDb: OMDB_TOKEN not set — IMDb ratings inherited from previous run"
        health.append({"level": level, "venue": "omdb", "message": msg})
        json.dump(health, open(health_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"  (could not update health.json: {e})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
