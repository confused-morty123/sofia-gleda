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
"""
import hashlib, json, os, re, sys, pathlib, datetime, unicodedata, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
CACHE_FILE = ROOT / "translations.json"
FILM_INFO_JSON = ROOT / "film_info.json"
TMDB_JSON = ROOT / "tmdb_films.json"

DEEPL_KEY = os.environ.get("DEEPL_AUTH_KEY", "").strip()
BATCH_SIZE = 50  # DeepL max texts per request

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    translations may be shorter than texts if the call failed.
    """
    import urllib.request, urllib.error

    body: dict = {
        "text": texts,
        "source_lang": "BG",
        "target_lang": "EN-GB",
    }
    if kind == "title":
        body["preserve_formatting"] = True

    payload = json.dumps(body).encode("utf-8")
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
        if e.code in (429, 456):
            print(f"  DeepL quota/rate-limit ({e.code}) — stopping, keeping results so far.",
                  file=sys.stderr)
        else:
            print(f"  DeepL HTTP {e.code} — stopping.", file=sys.stderr)
        return [], False
    except Exception as e:
        print(f"  DeepL error: {e} — stopping.", file=sys.stderr)
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
    idx = 0
    while idx < len(missing):
        batch = missing[idx: idx + BATCH_SIZE]
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
        idx += BATCH_SIZE
    return True


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
        return entry["en"] if entry else None

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
    if not DEEPL_KEY:
        print("DEEPL_AUTH_KEY not set — skipping translations, keeping previous.")
        return 0

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

    ok = True
    for work, kind in [(film_syn, "syn"), (film_title, "title"),
                       (show_syn, "syn"), (show_title, "title")]:
        if not ok:
            print("  stopping further batches (quota/error).", file=sys.stderr)
            break
        ok = translate_missing(work, kind, cache)

    syn_en, title_en = build_mappings(films, shows, tmdb, film_info, cache)
    print(f"  cache now covers SYN_EN: {len(syn_en)} ids, TITLE_EN: {len(title_en)} ids")

    # Write the mappings as JSON sidecar files so inject_data.py can read them
    # without re-parsing the cache. These are ephemeral (regenerated each run).
    (ROOT / "translations_syn_en.json").write_text(
        json.dumps(syn_en, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    (ROOT / "translations_title_en.json").write_text(
        json.dumps(title_en, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
