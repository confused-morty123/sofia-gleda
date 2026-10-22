#!/usr/bin/env python3
"""Generate per-title share pages for Sofia Gleda.

Creates or updates:
    film/<id>/index.html    one page per film
    show/<id>/index.html    one page per show
    event/<id>/index.html   one page per event (the hand-curated EVENTS seed and the
                            festivals fetch_events.py adds, EVENTS_AUTO)

Each page carries OG / Twitter Card meta tags, a canonical <link>, a small script
that takes a real browser on to the app (/?film=<id>, /?show=<id>, /?event=<id>,
which opens the title's sheet), and a visible link for a browser without
JavaScript.

There is deliberately NO meta refresh, and the script skips link-preview fetchers
(BOT_UA below): Apple's (iMessage, Notes, Mail; its user agent is
"... facebookexternalhit/1.1 Facebot Twitterbot/1.0") loads the page in WebKit
and follows redirects, so with an instant redirect it read the home page's tags
and every film was previewed as the generic site card (16 Oct 2026).

The script is the same on every page (the target comes from location.pathname),
so a Content-Security-Policy can allow it with one hash.

Run this AFTER inject_data.py (step 10) so FILMS, SHOWS, EVENTS, EVENTS_AUTO and
the poster maps are all inlined in index.html.

    python3 scripts/gen_share_pages.py

Existing pages for titles no longer in index.html are kept so shared links
keep working; the app shows a "no longer in programme" note for them.
"""
import datetime
import html as html_mod
import json
import pathlib
import re
import sys
from urllib.parse import quote
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
FALLBACK_FILE = ROOT / "share" / "share-preview.jpg"

# Maximum length of og:description (characters)
DESC_MAX = 200

# Link-preview fetchers: they must stay on the page and read its tags. Matched
# case-insensitively anywhere in the user agent. Only the fetchers' own tokens:
# "Viber/<version>" is also in the user agent of Viber's in-app browser on
# Android (a person, who must go on to the app), so Viber's bot is "ViberBot";
# its other preview requests (CFNetwork, okhttp) run no script anyway. The same
# goes for Pinterest's in-app browser ("[Pinterest/iOS]") against its crawler
# ("Pinterestbot", "Pinterest/0.2").
BOT_UA = ("facebookexternalhit|facebot|facebookcatalog|twitterbot|whatsapp|telegrambot|viberbot"
          "|slackbot|slack-imgproxy|discordbot|linkedinbot|pinterestbot|pinterest/0\\."
          "|skypeuripreview|microsoftpreview|iframely|embedly|google-pagerenderer|redditbot"
          "|applebot|vkshare|line-poker|kakaotalk-scrap|cardyb|mastodon|bitlybot"
          "|xing-contenttabreceiver|snap url preview|yahoo link preview|quora link preview")

# The page's one script (csp.py: first line SHARE_MARK), the same on every page: a
# browser goes on to the app's listing with the title open, /film/<id>/ -> /home/?film=<id>
# (the app opens the card and puts /film/<id>/ back in the address bar). A reload keeps
# the entry's state, so the listing it was opened from comes back: /home/theatre/ when
# the state says so. Link-preview fetchers stay.
REDIRECT_JS = ('/* SOFIA-SHARE */\n(function(){if(/' + BOT_UA.replace("/", "\\/") + '/i.test(navigator.userAgent||""))return;'
               'var m=/^\\/(film|show|event)\\/([^\\/?#]+)\\/(?:index\\.html)?$/.exec(location.pathname),s=history.state;'
               'location.replace((s&&s.m==="theatre"?"/home/theatre/":"/home/")+(m?"?"+m[1]+"="+m[2]:""));})();')

MONTHS_BG = ["януари", "февруари", "март", "април", "май", "юни", "юли", "август",
             "септември", "октомври", "ноември", "декември"]

TMDB_POSTER = "https://image.tmdb.org/t/p/w500"
TMDB_BACKDROP = "https://image.tmdb.org/t/p/w780"


def _load_data():
    """Parse FILMS, SHOWS, the events and the poster maps from index.html without
    running its JS."""
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
    # -> TMDBART (TMDB art) -> EVENTART (events' key art) -> SHOWART (theatre posters).
    # POSTER_ALIAS / POSTER_MAP are JS expressions, not JSON; read their
    # backing constants SHOWALIAS and POSTERS instead.
    poster_alias = _const("SHOWALIAS") or {}   # mirrors / redirects: {id: canonical_id}
    poster_map   = _const("POSTERS")   or {}   # direct URL overrides: {id: url}
    tmdbart      = _const("TMDBART")   or {}   # TMDB data: {id: {p, b, en, …}}
    showart      = _const("SHOWART")   or {}   # theatre poster URLs: {id: url}
    tmdb_base = TMDB_POSTER

    return films, shows, poster_alias, poster_map, tmdbart, showart, tmdb_base


def _load_events():
    """The events the app lists: the EVENTS seed, then EVENTS_AUTO entries whose id the
    seed does not use (the app's own merge rule), plus EVENTART, EVENT_VENUE (event
    -> host venue id) and the venues' names."""
    src = HTML_PATH.read_text(encoding="utf-8")

    def _const(name):
        m = re.search(r"^const %s\s*=\s*([\[{].*?[}\]]);$" % re.escape(name), src, re.M)
        try:
            return json.loads(m.group(1)) if m else None
        except Exception:
            return None

    seed = _const("EVENTS") or []
    auto = _const("EVENTS_AUTO") or []
    events, seen = [], set()
    for e in seed:
        if isinstance(e, dict) and e.get("id") and e["id"] not in seen:
            seen.add(e["id"])
            events.append(e)
    for e in auto:
        if (isinstance(e, dict) and e.get("id") and e["id"] not in seen and e.get("from") and e.get("to")
                and isinstance(e.get("g"), list) and len(e["g"]) == 2):
            seen.add(e["id"])
            events.append(e)
    # EVENT_VENUE is a small hand-written object in the app code, not a JSON line
    venue_of = {}
    m = re.search(r"^const EVENT_VENUE=\{(.*?)\};", src, re.M | re.S)
    if m:
        venue_of = dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', m.group(1)))
    names = {}
    for v in (_const("CINEMAS") or []) + (_const("THEATRES") or []):
        if isinstance(v, dict) and v.get("id"):
            names[v["id"]] = v.get("name") or ""
    return events, (_const("EVENTART") or {}), venue_of, names


def _truncate(text, max_chars):
    """Truncate `text` to at most `max_chars` characters, ending on a word boundary."""
    if not text or len(text) <= max_chars:
        return text or ""
    cut = text[:max_chars].rsplit(" ", 1)[0]
    return cut.rstrip(".,;:—–") + "…"


def _local_size(rel):
    """(width, height) of a file the site serves, or None."""
    path = ROOT / rel.lstrip("/")
    if not path.is_file():
        return None
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def _image(item_id, poster_alias, poster_map, tmdbart, showart, eventart=None):
    """(url, width, height) of the share image, mirroring the app's realPoster();
    width/height are None when not known. TMDB sizes are fixed by the URL: w500
    posters are 2:3, w780 backdrops 16:9."""
    base = poster_alias.get(item_id, item_id)
    if poster_map.get(base):
        url = poster_map[base]
        if url.startswith("https://"):
            return url, None, None
    art = tmdbart.get(base, {})
    if art.get("p"):
        return TMDB_POSTER + art["p"], 500, 750
    if art.get("b"):
        return TMDB_BACKDROP + art["b"], 780, 439
    for table in (eventart or {}), showart:
        val = table.get(base) or table.get(item_id)
        if not val:
            continue
        if val.startswith("https://"):
            return val, None, None
        # posters are stored as relative paths like "posters/uroci.jpg", served
        # from SITE_URL/posters/…; a file that is not there would preview blank
        size = _local_size(val)
        if size:
            return SITE_URL + "/" + val.lstrip("/"), size[0], size[1]
    size = _local_size("share/share-preview.jpg")
    return FALLBACK_IMAGE, (size[0] if size else None), (size[1] if size else None)


def _poster_url(item_id, poster_alias, poster_map, tmdbart, showart, tmdb_base=TMDB_POSTER):
    """Resolve the best poster URL for item_id, mirroring the app's realPoster() logic."""
    return _image(item_id, poster_alias, poster_map, tmdbart, showart)[0]


def _bg_range(a, b):
    """'17–24 октомври 2026', '15 октомври – 8 ноември 2026', '17 септември 2026'."""
    try:
        da, db = datetime.date.fromisoformat(a), datetime.date.fromisoformat(b)
    except (TypeError, ValueError):
        return ""
    if da == db:
        return f"{da.day} {MONTHS_BG[da.month - 1]} {da.year}"
    if (da.year, da.month) == (db.year, db.month):
        return f"{da.day}–{db.day} {MONTHS_BG[db.month - 1]} {db.year}"
    if da.year == db.year:
        return f"{da.day} {MONTHS_BG[da.month - 1]} – {db.day} {MONTHS_BG[db.month - 1]} {db.year}"
    return f"{da.day} {MONTHS_BG[da.month - 1]} {da.year} – {db.day} {MONTHS_BG[db.month - 1]} {db.year}"


def _e(text):
    """HTML-escape for attribute values and text content."""
    return html_mod.escape(str(text), quote=True)


def _share_page(
    kind,           # "film", "show" or "event"
    item_id,        # slug, e.g. "odiseya"
    title_bg,       # Bulgarian title
    title_en,       # English title (may be empty)
    syn_bg,         # Bulgarian synopsis (may be empty)
    poster_url,     # absolute https:// URL
    site_url,       # SITE_URL constant
    image_w=None,   # the image's size, when known
    image_h=None,
    line=None,      # a line under the title on the visible page (an event's dates and venue)
):
    """Return the full HTML string for one share page."""
    display_title = title_bg
    if title_en and title_en != title_bg:
        display_title = f"{title_bg} / {title_en}"

    # og:title / twitter:title carry the title alone; og:site_name already carries
    # "Sofia Gleda". A combined "Sofia Gleda — Title" would introduce an em dash,
    # which is against the copy rules.
    og_title = _e(title_bg)
    og_desc = _e(_truncate(syn_bg, DESC_MAX)) if syn_bg else _e(display_title)
    og_image = _e(poster_url)
    qid = urllib.parse.quote(item_id, safe="")
    canonical = _e(f"{site_url}/{kind}/{item_id}/")
    # root-relative: the visitor stays on the host that served this page
    app_url = _e(f"{'/home/theatre/' if kind == 'show' else '/home/'}?{kind}={quote(item_id, safe='')}")
    page_title = _e(f"{title_bg} | Sofia Gleda")
    sub = line if line is not None else (title_en if title_en and title_en != title_bg else "")
    dims = ""
    if image_w and image_h:
        dims = (f'<meta property="og:image:width" content="{int(image_w)}">\n'
                f'<meta property="og:image:height" content="{int(image_h)}">\n')

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
{dims}<meta property="og:image:alt" content="{og_title}">
<meta property="og:locale" content="bg_BG">
<!-- Twitter Card -->
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{og_title}">
<meta name="twitter:description" content="{og_desc}">
<meta name="twitter:image" content="{og_image}">
<!-- A browser goes on to the app; a link-preview fetcher stays and reads the tags above -->
<script>{REDIRECT_JS}</script>
<style>
body{{font-family:system-ui,sans-serif;background:#0C0A0F;color:#F4EFF6;
     display:flex;flex-direction:column;align-items:center;justify-content:center;
     min-height:100vh;margin:0;padding:1.5rem;text-align:center;box-sizing:border-box}}
.brand{{font-size:12px;font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:#E0163A;margin:0 0 14px}}
h1{{font-size:24px;line-height:1.25;margin:0 0 8px;font-weight:700}}
.sub{{color:#B6ABBC;margin:0 0 22px}}
.open{{display:inline-block;padding:11px 20px;border-radius:10px;background:#E0163A;color:#fff;
      font-weight:600;text-decoration:none}}
.en{{margin-top:12px;font-size:14px}}
.en a{{color:#B6ABBC}}
</style>
</head>
<body>
<p class="brand">Sofia Gleda</p>
<h1>{_e(title_bg)}</h1>
{f'<p class="sub">{_e(sub)}</p>' if sub else ''}
<p><a class="open" href="{app_url}">Отвори в Sofia Gleda</a></p>
<p class="en" lang="en"><a href="{app_url}">Open in Sofia Gleda</a></p>
</body>
</html>
"""


def _event_page(ev, eventart, venue_of, venue_names, poster_alias, poster_map, tmdbart, showart):
    """The share page of one event: its title, a dates-and-venue line, its key art
    (or the site's preview image)."""
    rng = _bg_range(ev.get("from"), ev.get("to"))
    venue = venue_names.get(venue_of.get(ev["id"], ""), "")
    line = " · ".join(x for x in (rng, venue) if x)
    blurb = (ev.get("blurbBg") or "").strip()
    if blurb and rng and rng in blurb:       # a programata blurb already gives the dates
        desc = f"{venue}. {blurb}" if venue else blurb
    else:
        desc = ". ".join(x for x in (line, blurb) if x)
    url, w, h = _image(ev["id"], poster_alias, poster_map, tmdbart, showart, eventart)
    return _share_page("event", ev["id"], ev.get("title") or ev["id"], ev.get("titleEn") or "",
                       desc, url, SITE_URL, image_w=w, image_h=h, line=line)


def _write(path, content):
    # The page's Content-Security-Policy meta: the hash of its one redirect script.
    content = csp.stamp(content, kind="share")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main():
    films, shows, poster_alias, poster_map, tmdbart, showart, tmdb_base = _load_data()
    events, eventart, venue_of, venue_names = _load_events()

    film_ids = {f["id"] for f in films}
    show_ids = {s["id"] for s in shows}
    event_ids = {e["id"] for e in events}

    written = 0
    kept = 0

    def _img(item_id):
        return _image(item_id, poster_alias, poster_map, tmdbart, showart)

    # --- films ---
    skipped = []
    for film in films:
        fid = film["id"]
        if not isinstance(fid, str) or not SAFE_ID.fullmatch(fid):
            skipped.append(f"film:{fid!r}")
            continue
        url, w, h = _img(fid)
        content = _share_page(
            kind="film",
            item_id=fid,
            title_bg=film.get("bg", fid),
            title_en=film.get("en", ""),
            syn_bg=film.get("synBg", ""),
            poster_url=url,
            site_url=SITE_URL,
            image_w=w, image_h=h,
        )
        _write(ROOT / "film" / fid / "index.html", content)
        written += 1

    # --- shows ---
    for show in shows:
        sid = show["id"]
        if not isinstance(sid, str) or not SAFE_ID.fullmatch(sid):
            skipped.append(f"show:{sid!r}")
            continue
        url, w, h = _img(sid)
        content = _share_page(
            kind="show",
            item_id=sid,
            title_bg=show.get("title", sid),
            title_en=show.get("titleEn", ""),
            syn_bg=show.get("synBg", ""),
            poster_url=url,
            site_url=SITE_URL,
            image_w=w, image_h=h,
        )
        _write(ROOT / "show" / sid / "index.html", content)
        written += 1

    # --- events ---
    for ev in events:
        if not isinstance(ev.get("id"), str) or not SAFE_ID.fullmatch(ev["id"]):
            skipped.append(f"event:{ev.get('id')!r}")
            continue
        _write(ROOT / "event" / ev["id"] / "index.html",
               _event_page(ev, eventart, venue_of, venue_names, poster_alias, poster_map, tmdbart, showart))
        written += 1

    # The listings' own addresses are the app itself (no redirect): /home/ (cinema) is
    # index.html as it is, /home/theatre/ the same page with its own canonical address.
    app = HTML_PATH.read_text(encoding="utf-8")
    for rel, path in (("home", "/home/"), ("home/theatre", "/home/theatre/")):
        page = app.replace(f'href="{SITE_URL}/home/"', f'href="{SITE_URL}{path}"', 1).replace(
            f'property="og:url" content="{SITE_URL}/home/"', f'property="og:url" content="{SITE_URL}{path}"', 1)
        d = ROOT / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(page, encoding="utf-8")

    # Keep (do not delete) any existing pages for titles no longer in the data.
    for kind, ids in (("film", film_ids), ("show", show_ids), ("event", event_ids)):
        d = ROOT / kind
        if d.is_dir():
            kept += sum(1 for p in d.glob("*/index.html") if p.parent.name not in ids)

    print(f"gen_share_pages: wrote {written} share pages ({len(films)} films, {len(shows)} shows, "
          f"{len(events)} events)" + (f", kept {kept} pages for unlisted titles" if kept else ""))
    if skipped:
        print(f"gen_share_pages: skipped {len(skipped)} id(s) that are not slugs: "
              + ", ".join(skipped[:10]), file=sys.stderr)


if __name__ == "__main__":
    main()
