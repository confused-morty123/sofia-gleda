#!/usr/bin/env python3
"""Generate per-title share pages for Sofia Gleda.

Creates or updates:
    film/<id>/index.html   — one page per film
    show/<id>/index.html   — one page per show

Each page carries OG / Twitter Card meta tags, a canonical <link>, a meta
refresh + JS redirect back to the app (with ?film= or ?show= so messenger
previews work while deep-linking still works), and a minimal no-JS fallback.

Run this AFTER inject_data.py (step 9) so FILMS, SHOWS, and POSTERS are
all inlined in index.html.

    python3 scripts/gen_share_pages.py

Existing pages for titles no longer in index.html are kept so shared links
keep working; they just show a "no longer in programme" note in the redirect
target (the app handles this via the ?film= query parameter).
"""
import html as html_mod
import json
import pathlib
import re
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
from site_config import SITE_URL  # noqa: E402
import csp  # noqa: E402

# An id names a directory (film/<id>/) and goes into a URL: letters (any script),
# digits and hyphens only, so no id can climb out of film/ or show/ ("../") or
# carry markup. The scraper mints slugs; anything else is skipped, loudly.
SAFE_ID = re.compile(r"[^\W_][\w-]{0,150}")

HTML_PATH = pathlib.Path(__file__).resolve().parent.parent / "index.html"

FALLBACK_IMAGE = SITE_URL + "/share/share-preview.jpg"

# Maximum length of og:description (characters)
DESC_MAX = 200


def _load_data():
    """Parse FILMS, SHOWS, and poster data from index.html without loading the full JS."""
    src = HTML_PATH.read_text(encoding="utf-8")

    def _const(name):
        """Extract a top-level JS constant that inject_data writes as a single line:
            const NAME = <json>;
        Works for both array and object values of any size.
        """
        m = re.search(r"^const %s\s*=\s*([\[{].*?[}\]]);$" % re.escape(name),
                      src, re.M)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except Exception:
            return None

    films   = _const("FILMS")   or []
    shows   = _const("SHOWS")   or []

    # Poster resolution mirrors the app's realPoster() priority chain:
    # POSTER_ALIAS (backed by SHOWALIAS) -> POSTER_MAP (backed by POSTERS)
    # -> TMDBART (TMDB art) -> SHOWART (theatre posters).
    # POSTER_ALIAS / POSTER_MAP are JS expressions, not JSON; read their
    # backing constants SHOWALIAS and POSTERS instead.
    poster_alias = _const("SHOWALIAS") or {}   # mirrors / redirects: {id: canonical_id}
    poster_map   = _const("POSTERS")   or {}   # direct URL overrides: {id: url}
    tmdbart      = _const("TMDBART")   or {}   # TMDB data: {id: {p, b, en, …}}
    showart      = _const("SHOWART")   or {}   # theatre poster URLs: {id: url}
    tmdb_base = "https://image.tmdb.org/t/p/w500"

    return films, shows, poster_alias, poster_map, tmdbart, showart, tmdb_base


def _truncate(text, max_chars):
    """Truncate `text` to at most `max_chars` characters, ending on a word boundary."""
    if not text or len(text) <= max_chars:
        return text or ""
    cut = text[:max_chars].rsplit(" ", 1)[0]
    return cut.rstrip(".,;:—–") + "…"


def _poster_url(item_id, poster_alias, poster_map, tmdbart, showart, tmdb_base):
    """Resolve the best poster URL for item_id, mirroring the app's realPoster() logic."""
    base = poster_alias.get(item_id, item_id)
    if poster_map.get(base):
        url = poster_map[base]
        if url.startswith("https://"):
            return url
    art = tmdbart.get(base, {})
    if art.get("p"):
        return tmdb_base + art["p"]
    if art.get("b"):
        return "https://image.tmdb.org/t/p/w780" + art["b"]
    if showart.get(base):
        val = showart[base]
        if val.startswith("https://"):
            return val
        # Theatre posters are stored as relative paths like "posters/uroci.jpg";
        # the site serves them from SITE_URL/posters/…
        return SITE_URL + "/" + val.lstrip("/")
    return FALLBACK_IMAGE


def _e(text):
    """HTML-escape for attribute values and text content."""
    return html_mod.escape(str(text), quote=True)


def _share_page(
    kind,           # "film" or "show"
    item_id,        # slug, e.g. "odiseya"
    title_bg,       # Bulgarian title
    title_en,       # English title (may be empty)
    syn_bg,         # Bulgarian synopsis (may be empty)
    poster_url,     # absolute https:// URL
    site_url,       # SITE_URL constant
):
    """Return the full HTML string for one share page."""
    display_title = title_bg
    if title_en:
        display_title = f"{title_bg} / {title_en}"

    # og:title / twitter:title carry the title alone; og:site_name already carries
    # "Sofia Gleda". A combined "Sofia Gleda — Title" would introduce an em dash,
    # which is against the copy rules.
    og_title = _e(title_bg)
    og_desc = _e(_truncate(syn_bg, DESC_MAX)) if syn_bg else _e(display_title)
    og_image = _e(poster_url)
    qid = urllib.parse.quote(item_id, safe="")
    canonical = _e(f"{site_url}/{kind}/{item_id}/")
    redirect_url = _e(f"{site_url}/?{kind}={qid}")
    # Human-readable fallback link text uses the BG title only (safe for Cyrillic)
    title_safe = _e(title_bg)
    page_title = _e(f"{title_bg} | Sofia Gleda")

    return f"""<!doctype html>
<html lang="bg">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="strict-origin-when-cross-origin">
<title>{page_title}</title>
<meta name="description" content="{og_desc}">
<link rel="canonical" href="{canonical}">
<!-- Open Graph -->
<meta property="og:type" content="website">
<meta property="og:site_name" content="Sofia Gleda">
<meta property="og:url" content="{canonical}">
<meta property="og:title" content="{og_title}">
<meta property="og:description" content="{og_desc}">
<meta property="og:image" content="{og_image}">
<meta property="og:locale" content="bg_BG">
<!-- Twitter Card -->
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{og_title}">
<meta name="twitter:description" content="{og_desc}">
<meta name="twitter:image" content="{og_image}">
<!-- Redirect to the app -->
<meta http-equiv="refresh" content="0; url={redirect_url}">
<script>/* SOFIA-SHARE */location.replace({csp.json_for_script(f"{site_url}/?{kind}={qid}")});</script>
<style>
body{{font-family:system-ui,sans-serif;background:#0C0A0F;color:#F4EFF6;
     display:flex;flex-direction:column;align-items:center;justify-content:center;
     min-height:100vh;margin:0;padding:1.5rem;text-align:center;}}
a{{color:#E0163A;}}
</style>
</head>
<body>
<p>Пренасочване към Sofia Gleda&hellip;</p>
<p><a href="{redirect_url}">{title_safe}</a></p>
</body>
</html>
"""


def _write(path, content):
    # The page's Content-Security-Policy meta: the hash of its one redirect script.
    content = csp.stamp(content, kind="share")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main():
    films, shows, poster_alias, poster_map, tmdbart, showart, tmdb_base = _load_data()

    film_ids = {f["id"] for f in films}
    show_ids = {s["id"] for s in shows}

    written = 0
    kept = 0

    def _pu(item_id):
        return _poster_url(item_id, poster_alias, poster_map, tmdbart, showart, tmdb_base)

    # --- films ---
    skipped = []
    for film in films:
        fid = film["id"]
        if not isinstance(fid, str) or not SAFE_ID.fullmatch(fid):
            skipped.append(f"film:{fid!r}")
            continue
        page = ROOT / "film" / fid / "index.html"
        content = _share_page(
            kind="film",
            item_id=fid,
            title_bg=film.get("bg", fid),
            title_en=film.get("en", ""),
            syn_bg=film.get("synBg", ""),
            poster_url=_pu(fid),
            site_url=SITE_URL,
        )
        _write(page, content)
        written += 1

    # --- shows ---
    for show in shows:
        sid = show["id"]
        if not isinstance(sid, str) or not SAFE_ID.fullmatch(sid):
            skipped.append(f"show:{sid!r}")
            continue
        page = ROOT / "show" / sid / "index.html"
        content = _share_page(
            kind="show",
            item_id=sid,
            title_bg=show.get("title", sid),
            title_en=show.get("titleEn", ""),
            syn_bg=show.get("synBg", ""),
            poster_url=_pu(sid),
            site_url=SITE_URL,
        )
        _write(page, content)
        written += 1

    # Keep (do not delete) any existing pages for titles no longer in the data.
    # This is a no-op here: we simply never delete anything.
    for existing in list((ROOT / "film").rglob("index.html")):
        fid = existing.parent.name
        if fid not in film_ids:
            kept += 1
    for existing in list((ROOT / "show").rglob("index.html")):
        sid = existing.parent.name
        if sid not in show_ids:
            kept += 1

    print(f"gen_share_pages: wrote {written} share pages"
          + (f", kept {kept} pages for unlisted titles" if kept else ""))
    if skipped:
        print(f"gen_share_pages: skipped {len(skipped)} id(s) that are not slugs: "
              + ", ".join(skipped[:10]), file=sys.stderr)


if __name__ == "__main__":
    main()
