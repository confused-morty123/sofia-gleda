#!/usr/bin/env python3
"""Sofia Gleda — key art for the events the page lists -> event_posters.json + posters/ev-<id>.<ext>.

The owner (2026-10-15): "for the limited events that appear - I need posters for
them as well … just as you did for cinelibri". CineLibri's card had real art only
because posters/cinelibri.jpg was hand-added to theatre_posters.json (SHOWART).

Which events: every event the page shows that has not ended, the hand-curated
EVENTS plus EVENTS_AUTO (fetch_events.for_page over events.json). A cinema event
that only mirrors a catalogued film is skipped: it shows the film's TMDB poster
(SHOWALIAS).

Where the image comes from, in order. Nothing is guessed:
  1. the event's own site: a seed event through EVENT_SITES (cinelibri ->
     www.cinelibri.com, kinomania -> www.kinomania.bg); an auto event through the
     organiser link in its programata.bg post ("…следим ТУК" -> actfest.org);
  2. the programata.bg post's featured image: an auto event's own post, or the
     post fetch_events matched to a seed event (Киномания's).
On a page only these count: og:image, twitter:image and the WordPress featured
image (img.wp-post-image). An organiser's image must sit on the organiser's own
site (the same domain as the page); a third-party CDN cannot be attributed.

Refused: a filename that says logo / icon / placeholder / default (posterpolicy's
LOGO_STEM_RE and CHROME_RE below: the artvent pitfall, where og:image is always the
site logo), SVG/ICO, anything that is not a JPEG/PNG/WebP/GIF picture, an image
under MIN_WIDTH px wide, a strip wider than MAX_ASPECT:1 (Kinomania's 1000x200
header), a picture with transparent pixels (a cut-out logo). Preferred: landscape
or near-square (aspect >= WIDE, the event card is 344 x 184 px); a portrait picture
is taken only when no source has a wider one.

Re-hosted like the theatre posters (fetch_theatre_posters' POSTER_MAXPX on the
longest edge, JPEG at its POSTER_JPEG_Q):
posters/ev-<event id>.jpg. Keep-previous: a page or an image that cannot be read
keeps the event's art; the chosen image is downloaded again only when its source
URL changes; a refused URL, and the size of every picture read, are remembered so
nothing is downloaded twice to be judged. When an event's end date passes, its
record and file go.

event_posters.json:
    {"generated": ISO,
     "events": {"<event id>": {"file": "posters/ev-<id>.jpg", "src": image URL,
                               "page": the page that names it, "via": "site" |
                               "organiser" | "programata", "post": the programata
                               post (via organiser), "w", "h": source pixels,
                               "to": the event's end date, "at": YYYY-MM-DD}},
     "refused": {"<image URL>": "why"}, "sizes": {"<image URL>": [w, h]}}

EVENTART (inject_data.py): {"<event id>": "posters/ev-<id>.jpg"} for the events
the page shows whose record passes record_problem(); realPoster() reads it before
SHOWART, so the automated CineLibri art replaces the hand-added file (which stays
as the fallback). verify_build checks every EVENTART entry with record_problem().

    python3 scripts/fetch_event_posters.py     # live: GET only, polite
"""
from __future__ import annotations

import argparse, datetime as dt, json, os, pathlib, re, sys, tempfile
from urllib.parse import urljoin, urlsplit

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetch_events as FE                                        # noqa: E402
from posterpolicy import LOGO_STEM_RE, is_local_poster          # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
OUT = ROOT / "event_posters.json"
POSTERS_DIR = ROOT / "posters"
EVENTS_JSON = ROOT / "events.json"

# A seed event's own site (the hand-curated EVENTS have no link). Add one when a
# festival joins the seed; its og:image / twitter:image / featured image is used.
EVENT_SITES = {
    "cinelibri": "https://www.cinelibri.com/",
    "kinomania": "https://www.kinomania.bg/bg_BG/",
}
AGGREGATOR_HOSTS = ("programata.bg",)
# Links in a post that are never the organiser's own site.
NOT_ORGANISER = ("programata.bg", "sofialive.bg", "facebook.com", "fb.me", "instagram.com",
                 "youtube.com", "youtu.be", "tiktok.com", "twitter.com", "x.com", "linkedin.com",
                 "vimeo.com", "spotify.com", "google.com", "goo.gl", "eventim.bg", "ticketstation.bg",
                 "bilet.bg", "epaygo.bg", "imdb.com", "wikipedia.org")
MIN_WIDTH = 600             # px: smaller art would be upscaled on a 344 px card at 2x
MAX_ASPECT = 3.2            # wider than this is a header strip, not key art
WIDE = 0.8                  # aspect (w/h) from which a picture counts as landscape or near-square
from fetch_theatre_posters import POSTER_MAXPX, POSTER_JPEG_Q   # noqa: E402  (re-hosted like the theatre posters)
HOST_DELAY = 2.5            # seconds between requests to one host (cinelibri.com asks for 10)
RUN_BUDGET = 240

CHROME_RE = re.compile(r"favicon|apple[-_]?touch|(?:^|[-_])icons?(?:$|[-_\d])|sprite|placeholder|"
                       r"(?:^|[-_\d])logos?|(?:^|[-_])default(?:$|[-_])|no[-_]?image|"
                       r"(?:^|[-_])blank(?:$|[-_])|spacer|og[-_]?default|share[-_]?default|"
                       r"(?:^|[-_])avatar(?:$|[-_])", re.I)
IMAGE_EXT_REFUSED = (".svg", ".ico", ".bmp", ".tif", ".tiff")


# ------------------------------------------------------------------ helpers

def host(url):
    h = (urlsplit(url or "").hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def same_site(a, b):
    """Two URLs on one site: the same host, or one a subdomain of the other."""
    a, b = host(a), host(b)
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def on_hosts(url, hosts):
    h = host(url)
    return any(h == x or h.endswith("." + x) for x in hosts)


def page_images(page_html, page_url):
    """The pictures a page names as its own: og:image (and its secure_url),
    twitter:image, the post's WordPress featured image. Absolute URLs, in that
    order, no repeats. Nothing else on the page is a candidate."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page_html or "", "lxml")
    found = []

    def add(u):
        u = (u or "").strip()
        if u:
            u = urljoin(page_url, u)
            if u not in found:
                found.append(u)
    for key in ("og:image:secure_url", "og:image", "twitter:image", "twitter:image:src"):
        for m in soup.find_all("meta", attrs={"property": key}) + soup.find_all("meta", attrs={"name": key}):
            add(m.get("content"))
    # the post's own featured image: the first one not wrapped in a link (the
    # "related posts" thumbnails at the foot of a programata post are links)
    own = next((im for im in soup.select("img.wp-post-image") if im.find_parent("a") is None), None)
    if own is not None:
        add(own.get("src"))
    return found


def organiser_link(post_html, post_url):
    """The organiser's own site as the programata post links it: the first link
    in the article to a host that is not an aggregator, a social network, a
    ticket shop or a map. None when the post links none."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(post_html or "", "lxml")
    body = soup.select_one("div.text")
    for a in (body.find_all("a", href=True) if body else []):
        u = urljoin(post_url, a["href"].strip())
        if urlsplit(u).scheme in ("http", "https") and host(u) and not on_hosts(u, NOT_ORGANISER):
            return u
    return None


def url_problem(url):
    """Why an image URL is refused before it is downloaded, or None."""
    if not url or not url.startswith("https://"):
        return "not an https URL"
    path = urlsplit(url).path
    name = path.rsplit("/", 1)[-1]
    if name.lower().endswith(IMAGE_EXT_REFUSED):
        return f"not a photo format ({name[-4:]})"
    stem = name.rsplit(".", 1)[0]
    if LOGO_STEM_RE.search(stem) or CHROME_RE.search(stem):
        return f"filename '{stem[:40]}' is site chrome (logo / icon / placeholder)"
    return None


def image_problem(path):
    """(why the downloaded file is refused or None, (w, h))."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            fmt = (im.format or "").upper()
            im.load()
            w, h = im.size
            if fmt not in ("JPEG", "PNG", "WEBP", "GIF", "MPO"):
                return f"not a photo ({fmt or 'unknown format'})", (w, h)
            if w < MIN_WIDTH:
                return f"{w}x{h}: under {MIN_WIDTH} px wide", (w, h)
            if w / h > MAX_ASPECT:
                return f"{w}x{h}: a strip, wider than {MAX_ASPECT}:1", (w, h)
            if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
                alpha = im.convert("RGBA").getchannel("A")
                if alpha.getextrema()[0] < 255:
                    return f"{w}x{h}: transparent pixels (a cut-out logo)", (w, h)
            return None, (w, h)
    except Exception as e:
        return f"not an image ({type(e).__name__})", (0, 0)


def store_poster(src, name, dest_dir=None):
    """Downscale and re-encode like fetch_theatre_posters._store_poster: the
    longest edge at POSTER_MAXPX, JPEG at POSTER_JPEG_Q (always JPEG: a picture
    with transparency never gets here). Returns 'posters/<name>.jpg' and removes
    a stale sibling with another extension."""
    from PIL import Image
    dest_dir = pathlib.Path(dest_dir or POSTERS_DIR)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im.load()
        w, h = im.size
        if max(w, h) > POSTER_MAXPX:
            k = POSTER_MAXPX / max(w, h)
            im = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
        im = im.convert("RGB")
        dest = dest_dir / f"{name}.jpg"
        for stale in dest_dir.glob(f"{name}.*"):
            if stale != dest:
                stale.unlink()
        im.save(dest, quality=POSTER_JPEG_Q, optimize=True, progressive=True)
    return f"posters/{dest.name}"


def file_ok(rel, root=None):
    """The re-hosted file exists, is not empty and starts like a picture."""
    if not is_local_poster(rel):
        return False
    p = pathlib.Path(root or ROOT) / rel
    try:
        head = p.read_bytes()[:12]
    except OSError:
        return False
    return (head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n"
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP") or head[:6] in (b"GIF87a", b"GIF89a"))


def record_problem(eid, rec, root=None):
    """Why an event_posters.json record may not reach EVENTART, or None. Shared with
    verify_build: the file is ours and exists; the image came from programata.bg,
    from the seed event's EVENT_SITES site, or from the organiser site its programata
    post links (and sits on that site)."""
    if not isinstance(rec, dict):
        return "no record"
    f = rec.get("file")
    if not (isinstance(f, str) and f.startswith("posters/ev-") and is_local_poster(f)):
        return f"file {str(f)[:40]!r} is not a posters/ev-… image"
    if not file_ok(f, root):
        return f"{f} is missing or not a picture"
    src, page, via = rec.get("src") or "", rec.get("page") or "", rec.get("via")
    if not src.startswith("https://"):
        return f"source {src[:40]!r} is not https"
    if via == "programata":
        if not (on_hosts(src, AGGREGATOR_HOSTS) and on_hosts(page, AGGREGATOR_HOSTS)):
            return f"via programata, but the image is on {host(src)!r}"
    elif via == "site":
        site = EVENT_SITES.get(eid)
        if not site or not same_site(page, site) or not same_site(src, site):
            return f"via site, but {host(src)!r} is not {eid!r}'s site in EVENT_SITES"
    elif via == "organiser":
        if not on_hosts(rec.get("post") or "", AGGREGATOR_HOSTS):
            return "via organiser, but no programata post links the organiser"
        if not page or on_hosts(page, NOT_ORGANISER) or not same_site(src, page):
            return f"via organiser, but the image ({host(src)!r}) is not on the organiser's site ({host(page)!r})"
    else:
        return f"unknown source kind {via!r}"
    return None


# ------------------------------------------------------------------ the events

def page_events(src, events_doc, today):
    """(events the page shows and that have not ended, {seed id: [programata urls]}).
    Seed EVENTS first, then EVENTS_AUTO as fetch_events.for_page shapes them; a
    cinema event mirroring a catalogued film is left out (it shows the film's art)."""
    cat = FE.catalogue(src)
    shown, _members, rep = FE.for_page(events_doc or {}, cat, today)
    by_id = {e.get("id"): e for e in (events_doc or {}).get("events") or [] if isinstance(e, dict)}
    seed_urls = {}
    for auto_id, seed_id in rep.get("seed") or []:
        u = (by_id.get(auto_id) or {}).get("url")
        if u:
            seed_urls.setdefault(seed_id, []).append(u)
    mirrors = set()
    try:
        from posterpolicy import Catalogue
        pc = Catalogue(cat["FILMS"], cat["SHOWS"], cat["EVENTS"])
        mirrors = {e["id"] for e in cat["EVENTS"] if pc.mirrors_film(e.get("id"))}
    except Exception:
        pass
    out = []
    for e in list(cat["EVENTS"]) + list(shown):
        if (isinstance(e, dict) and e.get("id") and e.get("to", "") >= today.isoformat()
                and e["id"] not in mirrors and all(x["id"] != e["id"] for x in out)):
            out.append(e)
    return out, seed_urls


# ------------------------------------------------------------------ one event

def pick(ev, sources, prev, refused, download, root=None, sizes=None):
    """Choose and store the event's art. `sources` = [(via, page, post, [image urls])]
    in priority order; `download(url, path)` fetches an image (True/False). The
    first landscape or near-square picture wins; a portrait one only when no source
    has a wider picture. Only the chosen picture is written, and only when it is
    not already the stored one. `refused` ({url: why}) and `sizes` ({url: [w, h]}
    of pictures read before) spare repeat downloads. Returns the new record, or
    None (nothing acceptable now: the caller keeps `prev`)."""
    eid, root = ev["id"], pathlib.Path(root or ROOT)
    sizes = {} if sizes is None else sizes
    today = dt.date.today().isoformat()
    with tempfile.TemporaryDirectory() as td:
        n = [0]

        def fetch(url):
            """(path, (w, h)) of an acceptable download, or None (refusal remembered)."""
            n[0] += 1
            tmp = pathlib.Path(td) / f"c{n[0]}"
            if not download(url, tmp):
                return None                               # unreachable now: maybe next run
            why, wh = image_problem(tmp)
            if why:
                if not why.startswith("not an image"):    # a broken download is not remembered
                    refused[url] = why
                sizes.pop(url, None)
                return None
            sizes[url] = list(wh)
            return tmp, wh

        def commit(rec, tmp):
            if rec.get("file"):
                return rec                                # the stored file is this picture
            if tmp is None:
                got = fetch(rec["src"])                   # chosen from a remembered size
                if not got:
                    return None
                tmp, (rec["w"], rec["h"]) = got
            rec["file"] = store_poster(tmp, f"ev-{eid}", root / "posters")
            rec["at"] = today
            return rec

        portrait = None
        for via, page, post, urls in sources:
            for url in urls:
                if url_problem(url) or url in refused:
                    continue
                if via in ("site", "organiser") and not same_site(url, page):
                    refused[url] = f"not on the {via}'s own site ({host(page)})"
                    continue
                rec = {"src": url, "page": page, "via": via, "to": ev.get("to")}
                if via == "organiser":
                    rec["post"] = post
                tmp = None
                if prev and prev.get("src") == url and file_ok(prev.get("file"), root):
                    rec.update(file=prev["file"], w=prev.get("w"), h=prev.get("h"), at=prev.get("at"))
                elif url in sizes:
                    rec["w"], rec["h"] = sizes[url]             # read before: no download unless chosen
                else:
                    got = fetch(url)
                    if not got:
                        continue
                    tmp, (rec["w"], rec["h"]) = got
                if (rec.get("w") or 0) / max(1, rec.get("h") or 1) >= WIDE:
                    done = commit(rec, tmp)
                    if done:
                        return done
                    continue
                if portrait is None:
                    portrait = (rec, tmp)
        return commit(*portrait) if portrait else None


def sources_for(ev, seed_urls, fetch_page):
    """[(via, page, post, [image urls])] for one event, in priority order.
    `fetch_page(url)` returns HTML or None."""
    out = []
    if not ev.get("auto"):
        site = EVENT_SITES.get(ev["id"])
        if site:
            html = fetch_page(site)
            if html is not None:
                out.append(("site", site, None, page_images(html, site)))
        for post in seed_urls.get(ev["id"], []):
            html = fetch_page(post)
            if html is not None:
                out.append(("programata", post, None, page_images(html, post)))
        return out
    post = ev.get("url") or ""
    if not on_hosts(post, AGGREGATOR_HOSTS):
        return out
    post_html = fetch_page(post)
    if post_html is None:
        return out
    org = organiser_link(post_html, post)
    if org:
        html = fetch_page(org)
        if html is not None:
            out.append(("organiser", org, post, page_images(html, org)))
    out.append(("programata", post, None, page_images(post_html, post)))
    return out


# ------------------------------------------------------------------ the run

def run(events, seed_urls, prev_doc, fetch_page, download, today, root=None):
    """The new event_posters.json document (keep-previous)."""
    prev = dict((prev_doc or {}).get("events") or {})
    refused = dict((prev_doc or {}).get("refused") or {})
    sizes = dict((prev_doc or {}).get("sizes") or {})
    out, log = {}, []
    for ev in events:
        eid = ev["id"]
        old = prev.get(eid)
        rec = pick(ev, sources_for(ev, seed_urls, fetch_page), old, refused, download, root, sizes)
        if rec:
            out[eid] = rec
            log.append(f"  {eid:44s} {rec['via']:10s} {rec.get('w')}x{rec.get('h')}  {rec['src'][:90]}")
        elif old:
            out[eid] = dict(old, to=ev.get("to") or old.get("to"))
            log.append(f"  {eid:44s} kept the previous art ({old.get('src', '')[:70]})")
        else:
            log.append(f"  {eid:44s} no acceptable art found")
    for eid, old in prev.items():                  # events not shown this build keep their art…
        if eid in out:
            continue
        if (old.get("to") or "") >= today.isoformat():
            out[eid] = old
        else:                                       # …until their end date passes
            f = pathlib.Path(root or ROOT) / (old.get("file") or "")
            if (old.get("file") or "").startswith("posters/ev-") and f.is_file():
                f.unlink()
            log.append(f"  {eid:44s} ended {old.get('to')}: record and file removed")
    refused = dict(list(refused.items())[-200:])    # bounded memories
    sizes = dict(list(sizes.items())[-200:])
    return {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "events": out, "refused": refused, "sizes": sizes}, log


def load_doc(path=OUT):
    try:
        d = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_doc(doc, path=OUT):
    path = pathlib.Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def eventart(doc, event_ids, root=None):
    """EVENTART for the page: {event id: file} for the shown events whose record
    passes record_problem(). (inject_data.py)"""
    out = {}
    for eid, rec in sorted(((doc or {}).get("events") or {}).items()):
        if eid in event_ids and not record_problem(eid, rec, root):
            out[eid] = rec["file"]
    return out


def main(argv=None):
    argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)
    from netfetch import Fetcher
    today = FE.sofia_today()
    events, seed_urls = page_events(HTML.read_text(encoding="utf-8"), FE.load_doc(EVENTS_JSON), today)
    prev = load_doc()
    net = Fetcher(host_delay=HOST_DELAY, budget_seconds=RUN_BUDGET, attempts=3)
    pages = {}

    def fetch_page(url):
        if url not in pages:
            pages[url] = net.text(url)
        return pages[url]

    def download(url, path):
        return bool(net.download(url, path))
    try:
        with net.deadline(RUN_BUDGET):
            doc, log = run(events, seed_urls, prev, fetch_page, download, today)
    except Exception as e:                          # keep-previous: never write a half result
        print(f"  ! fetch_event_posters failed ({e!r}); event_posters.json left as it was")
        net.print_report()
        return 0
    save_doc(doc)
    print(f"event_posters.json: art for {len(doc['events'])} event(s), "
          f"{len(events)} shown and not ended")
    for line in log:
        print(line)
    net.print_report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
