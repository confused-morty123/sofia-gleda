#!/usr/bin/env python3
"""Sofia Gleda — cached auto-translation step (Wave L2).

Translates Bulgarian-only film/show synopses and titles into English using
DeepL, caching results in translations.json so each text is sent at most once.

    DEEPL_AUTH_KEY=<key> python3 scripts/translate.py   # real run
    python3 scripts/translate.py                         # no key → skip cleanly

The output — SYN_EN and TITLE_EN maps — is consumed by inject_data.py, which
emits them into the SOFIA-POSTERS block so the UI can serve English content.

Cache format: translations.json
    { sha1(normalised_text): {"src": first_60_chars, "en": translation,
                              "kind": "syn|title", "at": "2026-10-08"} }
Cache entries are never deleted.

Load (2026-11-11). New SYNOPSIS text is sent at most SYN_CHARS_PER_RUN characters
a run, the texts that have waited longest first (translations.json["_pending"]:
{cache key: the date the text was first seen untranslated}); the rest waits for the
next nights. Titles are short and are not capped. A text without English yet is
simply absent from SYN_EN: the English sheet then says "No English description
available yet." (never the Bulgarian text). When DeepL refuses (quota used up: HTTP
456; rate limit: 429; any other failure) the run keeps what it has, exits 0 and
leaves a "translations" warning in health.json: never a failed refresh.
"""
import hashlib, json, os, re, sys, pathlib, datetime, unicodedata, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
CACHE_FILE = ROOT / "translations.json"
FILM_INFO_JSON = ROOT / "film_info.json"
TMDB_JSON = ROOT / "tmdb_films.json"

DEEPL_KEY = os.environ.get("DEEPL_AUTH_KEY", "").strip()
BATCH_SIZE = 50  # DeepL max texts per request
BATCH_BYTES = 96_000  # …and its request body may not exceed 128 KiB (HTTP 413): stay well under it
# Characters of NEW synopsis text sent per run (DeepL Free: 500,000 a month). The
# own-page synopses (own_page_synopses.py) add a few hundred texts of ~600 characters
# within a few nights; this spreads them out, oldest-missing first.
SYN_CHARS_PER_RUN = 40_000
PENDING = "_pending"   # translations.json: {cache key: date first seen untranslated}; not a cache entry
HEALTH_ITEMS = ROOT / "health.json"
VENUE = "translations"            # its health.json entries
LAST_FAILURE: dict = {}           # why translate_batch last failed: {"why": "quota"|"rate"|"error", "detail": str}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Bulgaria's official romanisation (Streamlined System, 2009), plus the other
# Cyrillic letters that turn up in names. DeepL sometimes leaves a name or a
# quoted Bulgarian title in Cyrillic inside its English, and verify_build
# rejects any Cyrillic in SYN_EN/TITLE_EN, so leftovers are romanised.
_ROMAN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
    "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "sht", "ъ": "a", "ь": "y", "ю": "yu", "я": "ya", "ѝ": "i", "ѐ": "e",
    "ё": "yo", "ы": "y", "э": "e", "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ў": "u",
    "ђ": "dj", "ћ": "c", "џ": "dz", "љ": "lj", "њ": "nj", "ј": "j", "ѓ": "gj", "ќ": "kj",
    "ѕ": "dz",
}
_CYRILLIC = re.compile(r"[\u0400-\u04ff]")


# Names whose owners spell them in Latin letters their own way: DeepL renders
# the festival "Синелибри" as "Sinelibri", and so would the romanisation table,
# but the festival writes itself "CineLibri". All-caps text keeps its capitals.
_OFFICIAL_NAMES = [
    (re.compile(r"(?<![\w-])(?:S[iy]n[ae]libri|Синелибри)(?![\w-])", re.I), "CineLibri"),
]


def official_names(text: str) -> str:
    """`text` with the names in _OFFICIAL_NAMES spelt as their owners spell them
    ("Sinelibri 2026: …" -> "CineLibri 2026: …", "SINELIBRI" -> "CINELIBRI")."""
    for rx, name in _OFFICIAL_NAMES:
        text = rx.sub(lambda m, n=name: n.upper() if m.group(0).isupper() else n, text)
    return text


def latinise(text: str) -> str:
    """Romanise any Cyrillic left in an English text, keeping each word's case
    ("София" -> "Sofia", "ЩЕ" -> "SHTE"); "-ия" at a word end is "-ia", as the
    official system has it. Letters outside the table are dropped, so the result
    never contains Cyrillic. Official names are spelt their owners' way first
    (official_names): every English text the page shows passes through here."""
    if text:
        text = official_names(text)
    if not _CYRILLIC.search(text or ""):
        return text
    out = []
    for i, ch in enumerate(text):
        low = ch.lower()
        if low not in _ROMAN:
            out.append("" if _CYRILLIC.match(ch) else ch)
            continue
        prv = text[i - 1] if i else ""
        nxt = text[i + 1] if i + 1 < len(text) else ""
        rom = _ROMAN[low]
        if low == "я" and prv.lower() == "и" and not _CYRILLIC.match(nxt or " "):
            rom = "a"
        if ch != low:                                    # an upper-case letter
            whole_upper = (nxt.isalpha() and nxt.isupper()) or (prv.isalpha() and prv.isupper())
            rom = rom.upper() if whole_upper else rom.capitalize()
        out.append(rom)
    return "".join(out)


def _norm(text: str) -> str:
    """Normalise text for cache key: NFC, collapse whitespace."""
    text = unicodedata.normalize("NFC", text)
    return " ".join(text.split())


def cache_key(text: str) -> str:
    return hashlib.sha1(_norm(text).encode("utf-8")).hexdigest()


def has_cyrillic(text: str) -> bool:
    return bool(re.search(r"[Ѐ-ӿ]", text))


# ---------------------------------------------------------------------------
# DeepL transport
# ---------------------------------------------------------------------------

def _deepl_endpoint() -> str:
    if DEEPL_KEY.endswith(":fx"):
        return "https://api-free.deepl.com/v2/translate"
    return "https://api.deepl.com/v2/translate"


def translate_batch(texts: list[str], kind: str) -> tuple[list[str], bool]:
    """
    Send up to BATCH_SIZE texts to DeepL.

    Returns (translations, ok) where ok=False on quota/network errors.
    translations may be shorter than texts if the call failed. A request DeepL
    finds too large (HTTP 413) is split in half and sent again, so one long
    batch never stops the run.
    """
    import urllib.request, urllib.error

    body: dict = {
        "text": texts,
        "source_lang": "BG",
        "target_lang": "EN-GB",
    }
    if kind == "title":
        body["preserve_formatting"] = True

    # UTF-8 as is: the default ASCII escapes (\uXXXX) triple the size of Cyrillic
    # text, and 50 synopses then exceed DeepL's 128 KiB request limit
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        _deepl_endpoint(),
        data=payload,
        method="POST",
        headers={
            "Authorization": f"DeepL-Auth-Key {DEEPL_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.load(resp)
            return [t["text"] for t in result.get("translations", [])], True
    except urllib.error.HTTPError as e:
        if e.code == 413 and len(texts) > 1:
            half = len(texts) // 2
            first, ok = translate_batch(texts[:half], kind)
            if not ok:
                return first, False
            second, ok = translate_batch(texts[half:], kind)
            return first + second, ok
        if e.code in (429, 456):
            print(f"  DeepL quota/rate-limit ({e.code}) — stopping, keeping results so far.",
                  file=sys.stderr)
            LAST_FAILURE.update(why="quota" if e.code == 456 else "rate", detail=f"HTTP {e.code}")
        else:
            print(f"  DeepL HTTP {e.code} — stopping.", file=sys.stderr)
            LAST_FAILURE.update(why="error", detail=f"HTTP {e.code}")
        return [], False
    except Exception as e:
        print(f"  DeepL error: {e} — stopping.", file=sys.stderr)
        LAST_FAILURE.update(why="error", detail=type(e).__name__)
        return [], False


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.load(open(CACHE_FILE, encoding="utf-8"))
        except Exception as e:
            print(f"  (could not read {CACHE_FILE.name}: {e} — starting fresh)", file=sys.stderr)
    return {}


def save_cache(cache: dict) -> None:
    """Write atomically by writing to a temp file then renaming."""
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(CACHE_FILE)


def load_html_consts() -> tuple[list, list]:
    """Parse FILMS and SHOWS from the SOFIA-DATA block in index.html."""
    src = HTML.read_text(encoding="utf-8")
    films_m = re.search(r"const FILMS=(\[.*?\]);", src, flags=re.S)
    shows_m = re.search(r"const SHOWS=(\[.*?\]);", src, flags=re.S)
    films = json.loads(films_m.group(1)) if films_m else []
    shows = json.loads(shows_m.group(1)) if shows_m else []
    return films, shows


# ---------------------------------------------------------------------------
# Build work lists
# ---------------------------------------------------------------------------

def films_needing_translation(films: list, tmdb: dict, film_info: dict
                               ) -> tuple[list, list]:
    """
    Returns (syn_list, title_list) where each element is (id, source_text).
    Only includes texts that have no English equivalent from any source.
    """
    syn_list = []
    title_list = []
    for f in films:
        fid = f["id"]
        # --- synopsis ---
        has_syn_en = (bool(f.get("synEn"))
                      or bool((tmdb.get(fid) or {}).get("ov"))
                      or bool((film_info.get(fid) or {}).get("synEn")))
        if not has_syn_en:
            # Source text: seed synBg takes priority, else film_info synBg
            src = f.get("synBg") or (film_info.get(fid) or {}).get("synBg") or ""
            if src:
                syn_list.append((fid, src))
        # --- title ---
        has_en = (bool(f.get("en"))
                  or bool((tmdb.get(fid) or {}).get("en")))
        if not has_en:
            bg = f.get("bg") or ""
            if bg:
                title_list.append((fid, bg))
    return syn_list, title_list


def shows_needing_translation(shows: list) -> tuple[list, list]:
    """Returns (syn_list, title_list) for theatre shows."""
    syn_list = []
    title_list = []
    for s in shows:
        sid = s["id"]
        if not s.get("synEn") and s.get("synBg"):
            syn_list.append((sid, s["synBg"]))
        if not s.get("titleEn") and s.get("title"):
            title_list.append((sid, s["title"]))
    return syn_list, title_list


# ---------------------------------------------------------------------------
# Translate missing texts in batches
# ---------------------------------------------------------------------------

def request_batches(items, size=BATCH_SIZE, max_bytes=BATCH_BYTES):
    """[(id, text)] in DeepL-sized requests: at most `size` texts and about
    `max_bytes` of UTF-8 text each (DeepL answers 413 above 128 KiB). A single
    text over the limit still goes alone."""
    batch, used = [], 0
    for item in items:
        n = len(item[1].encode("utf-8")) + 16          # + its JSON quoting
        if batch and (len(batch) >= size or used + n > max_bytes):
            yield batch
            batch, used = [], 0
        batch.append(item)
        used += n
    if batch:
        yield batch


def translate_missing(work: list[tuple[str, str]], kind: str,
                       cache: dict) -> bool:
    """
    Translate texts in `work` not yet in `cache`.
    Mutates `cache` in place.
    Returns False if a quota/error stopped the run early.
    """
    missing = [(entity_id, text) for entity_id, text in work
               if cache_key(text) not in cache]
    if not missing:
        return True

    today = datetime.date.today().isoformat()
    for batch in request_batches(missing):
        texts = [t for _, t in batch]
        translations, ok = translate_batch(texts, kind)
        for i, (entity_id, src_text) in enumerate(batch):
            if i >= len(translations):
                break  # truncated on error
            en = translations[i].strip()
            key = cache_key(src_text)
            cache[key] = {
                "src": src_text[:60],
                "en": en,
                "kind": kind,
                "at": today,
            }
        # Save after every batch so quota-stop keeps partial results
        save_cache(cache)
        if not ok:
            return False
    return True


def plan_synopses(work, cache, today, limit=None):
    """Which of the not-yet-translated synopsis texts go to DeepL this run:
    (allowed cache keys, sent_chars, deferred_texts, deferred_chars). The texts that
    have been missing longest go first (cache[PENDING] remembers since when), in
    `work` order within one day, until `limit` characters are reached; the first
    text always goes. cache[PENDING] is rewritten to the texts missing now."""
    limit = SYN_CHARS_PER_RUN if limit is None else limit
    was = cache.get(PENDING) if isinstance(cache.get(PENDING), dict) else {}
    missing = {}
    for _id, text in work:
        k = cache_key(text)
        if k not in cache and k not in missing:
            missing[k] = text
    since = {k: (was.get(k) if isinstance(was.get(k), str) else today) for k in missing}
    order = sorted(missing, key=lambda k: since[k])          # stable: `work` order within a day
    allowed, used = set(), 0
    for k in order:
        if allowed and used + len(missing[k]) > limit:
            break
        allowed.add(k)
        used += len(missing[k])
    cache.pop(PENDING, None)
    if since:
        cache[PENDING] = since
    rest = [k for k in missing if k not in allowed]
    return allowed, used, len(rest), sum(len(missing[k]) for k in rest)


def prune_pending(cache):
    """cache[PENDING] without the texts translated meanwhile."""
    pend = cache.get(PENDING)
    if isinstance(pend, dict):
        left = {k: d for k, d in pend.items() if k not in cache}
        cache.pop(PENDING, None)
        if left:
            cache[PENDING] = left


def health_note(message, path=None):
    """Replace this step's health.json entry with `message` (a warning the workflow
    annotates the run with; None just removes an earlier one). Never raises."""
    path = pathlib.Path(path or HEALTH_ITEMS)
    try:
        items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(items, list):
            items = []
        keep = [e for e in items if not (isinstance(e, dict) and e.get("venue") == VENUE)]
        if message:
            keep.append({"level": "warning", "venue": VENUE, "message": message})
        if keep != items:
            path.write_text(json.dumps(keep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    except (OSError, ValueError) as e:
        print(f"  (could not update health.json: {e})", file=sys.stderr)


def failure_message(failure, waiting_texts, waiting_chars):
    """The health.json warning for a run DeepL refused."""
    why = {"quota": "DeepL's character quota is used up",
           "rate": "DeepL is rate-limiting this key"}.get(failure.get("why"), "DeepL did not answer")
    return (f"{why} ({failure.get('detail') or 'no detail'}): {waiting_texts} text(s), {waiting_chars} characters, "
            "still wait for their English translation. The English sheets show 'No English description "
            "available yet.' for them; the cached translations are served and the next runs try again.")


# ---------------------------------------------------------------------------
# Build output mappings
# ---------------------------------------------------------------------------

def build_mappings(films: list, shows: list, tmdb: dict, film_info: dict,
                   cache: dict) -> tuple[dict, dict]:
    """
    Returns (syn_en, title_en) dicts with only the ids that need translation
    and have a cached translation.
    """
    syn_en: dict[str, str] = {}
    title_en: dict[str, str] = {}

    def lookup(src_text: str) -> str | None:
        entry = cache.get(cache_key(src_text))
        return latinise(entry["en"]) if entry and entry.get("en") else None

    # Films
    for f in films:
        fid = f["id"]
        has_syn_en = (bool(f.get("synEn"))
                      or bool((tmdb.get(fid) or {}).get("ov"))
                      or bool((film_info.get(fid) or {}).get("synEn")))
        if not has_syn_en:
            src = f.get("synBg") or (film_info.get(fid) or {}).get("synBg") or ""
            if src:
                en = lookup(src)
                if en:
                    syn_en[fid] = en
        has_en = (bool(f.get("en")) or bool((tmdb.get(fid) or {}).get("en")))
        if not has_en:
            bg = f.get("bg") or ""
            if bg:
                en = lookup(bg)
                if en:
                    title_en[fid] = en

    # Shows
    for s in shows:
        sid = s["id"]
        if not s.get("synEn") and s.get("synBg"):
            en = lookup(s["synBg"])
            if en:
                syn_en[sid] = en
        if not s.get("titleEn") and s.get("title"):
            en = lookup(s["title"])
            if en:
                title_en[sid] = en

    return syn_en, title_en


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    cache = load_cache()
    films, shows = load_html_consts()

    tmdb: dict = {}
    if TMDB_JSON.exists():
        try:
            tmdb = json.load(open(TMDB_JSON, encoding="utf-8"))
        except Exception as e:
            print(f"  (could not read {TMDB_JSON.name}: {e})", file=sys.stderr)

    film_info: dict = {}
    if FILM_INFO_JSON.exists():
        try:
            film_info = json.load(open(FILM_INFO_JSON, encoding="utf-8"))
        except Exception as e:
            print(f"  (could not read {FILM_INFO_JSON.name}: {e})", file=sys.stderr)

    film_syn, film_title = films_needing_translation(films, tmdb, film_info)
    show_syn, show_title = shows_needing_translation(shows)

    print(f"  texts needing translation: "
          f"film syn {len(film_syn)}, film title {len(film_title)}, "
          f"show syn {len(show_syn)}, show title {len(show_title)}")
    total_chars = (sum(len(t) for _, t in film_syn)
                   + sum(len(t) for _, t in film_title)
                   + sum(len(t) for _, t in show_syn)
                   + sum(len(t) for _, t in show_title))
    print(f"  total characters to translate: {total_chars}")

    # the per-run cap on NEW synopsis text: oldest-missing first, the rest waits
    today = datetime.date.today().isoformat()
    allowed, sent_chars, deferred, deferred_chars = plan_synopses(film_syn + show_syn, cache, today)
    print(f"  new synopsis text this run: {len(allowed)} text(s), {sent_chars} characters "
          f"(cap {SYN_CHARS_PER_RUN}); waiting for the next runs: {deferred} text(s), {deferred_chars} characters")

    def _now(work):
        return [(i, t) for i, t in work if cache_key(t) in cache or cache_key(t) in allowed]

    ok = bool(DEEPL_KEY)
    LAST_FAILURE.clear()
    if not DEEPL_KEY:
        print("  DEEPL_AUTH_KEY not set — no new translations; serving the cached ones.")
    for work, kind in [(_now(film_syn), "syn"), (film_title, "title"),
                       (_now(show_syn), "syn"), (show_title, "title")]:
        if not ok:
            if DEEPL_KEY:
                print("  stopping further batches (quota/error).", file=sys.stderr)
            break
        ok = translate_missing(work, kind, cache)
    prune_pending(cache)
    if DEEPL_KEY:
        try:
            save_cache(cache)                    # the waiting list (and nothing else new)
        except OSError as e:
            print(f"  (could not save {CACHE_FILE.name}: {e})", file=sys.stderr)
        if not ok:                               # a warning in health.json, never a failed step
            waiting = {cache_key(t): t for _, t in film_syn + film_title + show_syn + show_title
                       if cache_key(t) not in cache}
            health_note(failure_message(LAST_FAILURE, len(waiting), sum(len(t) for t in waiting.values())))
        else:
            health_note(None)

    syn_en, title_en = build_mappings(films, shows, tmdb, film_info, cache)
    print(f"  cache now covers SYN_EN: {len(syn_en)} ids, TITLE_EN: {len(title_en)} ids")

    # inject_data.py builds the same maps from translations.json with
    # build_mappings, so nothing else is written here.
    return 0


if __name__ == "__main__":
    sys.exit(main())
