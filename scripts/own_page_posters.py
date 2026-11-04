#!/usr/bin/env python3
"""Sofia Gleda — posters read from each title's OWN page, with their provenance.

Why this exists (2026-11-04). The gate counted 521/526 theatre shows "with a
poster", but what it counted was "SHOWART has a file for this id". Nothing
recorded where a file came from, so a stage photo matched by title on an
aggregator, another play's photo (СЕДЕМ НОЩИ НА ВИОЛА КАРАВЕЛОВА carried the
bytes of НОРА), or a photo standing in where the theatre's own page shows the
real poster all counted the same as the official poster. And once a show had any
file, fetch_theatre_posters.py never looked at its own page again.

This step reads, for every title on the listing, the page the programme already
links it to (LINKS for plays, VLINKS for films): the theatre's own show page, the
venue's or festival's film page. It never searches by title, so it cannot attach
another production's image (the hard rule after the 23 wrong posters of
2026-10-29). From that page it takes, in this order:

  1. the official poster: the page's key image (og:image or the theatre's own
     poster slot) in portrait shape;
  2. the theatre's key image for the show in another shape (still its own art);
  3. a production photo from the page's own gallery: the most poster-like one
     (closest to portrait, at least 600 px on the long side, croppable); never
     one from a site-wide popup, <nav> or <footer> (2026-11-04: Cineland's
     "лев → €" banner sat in a popup and was attached to a film);
  4. nothing: the app keeps its generated art.

The page must be the title's: its own headings (<title>, og:title, <h1>, an
unlinked <h2>) name the title (page_identity), and the request did not end on
another page (a redirect, or a canonical / og:url at the site's root: the home
page served for a dead URL). A nav menu, a repertoire list or a heading inside a
link naming the title does not count. On a recheck a recorded image is never
replaced by a lesser kind (poster > key > photo); another image of its kind is
held ("pending") and replaces it only when a run HOLD_DAYS later finds it again
and no other title's page has shown it as its og:image (og_seen, kept across
runs); the replaced one is noted ("replaced"). Otherwise the recorded one stays.
Placeholders and funders' marks (posterpolicy.PLACEHOLDER_RE) and images in a
site-wide popup, <nav> or <footer> are never candidates.

Every attached image is re-hosted under posters/ and recorded in
poster_sources.json with the page it came from, the image URL, its kind, size and
md5. posterpolicy.true_coverage() counts only images with such a record, which is
the number the gate and test_parsers report.

Two sites are not read directly: mlt.bg (behind a Cloudflare challenge) and
sofiapuppet.com (its robots.txt blocks ClaudeBot). Both run on the theatre.art.bg
system: the "___<id>" at the end of their show URLs is the same production id,
so the production's own page there, theatre.art.bg/x_<id>_<theatre>_20, is read
instead (its title is checked against the show's).

GET only, robots.txt obeyed, at least 2 s between two requests to one host
(longer where a robots.txt Crawl-delay asks), one worker per host.

    python3 scripts/own_page_posters.py                  # every title that is due
    python3 scripts/own_page_posters.py --only chayka,fitsroy --verbose
    python3 scripts/own_page_posters.py --all            # ignore the retry schedule
    POSTER_CACHE_DIR=/tmp/c python3 scripts/own_page_posters.py   # local cache
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, io, json, os, pathlib, re, sys, threading, time
import urllib.parse
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from posterpolicy import (LOGO_MD5, LOGO_STEM_RE, PLACEHOLDER_RE,    # noqa: E402
                          is_local_poster, norm_title)

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
SOURCES = ROOT / "poster_sources.json"
THEATRE_JSON = ROOT / "theatre_posters.json"
POSTERS_DIR = ROOT / "posters"
HOST_GAP = 2.0                  # seconds between two requests to one host
MAX_IMAGES_PER_TITLE = 6        # downloads tried for one title before giving up
RECHECK_DAYS = 14               # a recorded poster's page is read again after this
RETRY_DAYS = 3                  # a title with nothing found is tried again after this
HOLD_DAYS = 3                   # a recheck's new image replaces the recorded one only if found again this much later

# The theatre.art.bg theatre id of each venue whose own site is not read directly.
ART_MIRROR = {"mlt.bg": 4, "sofiapuppet.com": 10}
ART_PROD_URL = "https://theatre.art.bg/x_{prod}_{theatre}_20"
PEAKVIEW_ID_RE = re.compile(r"___(\d+)/?$")
NATIONAL_SLUG_RE = re.compile(
    r"(https://(?:www\.)?nationaltheatre\.bg/bg/predstavlenie/[^/|?#]+)", re.I)

# Images that are never production art: site chrome, people's headshots (cast
# lists), icons. Matched against the lower-cased URL path.
CHROME_RE = re.compile(
    r"logo|icon|favicon|sprite|placeholder|/social/|apple-touch|/flags?/|"
    r"(?:^|[/_-])(?:map|btn|button|arrow|rating\d*|maska|dot_tr|loading|check_via|"
    r"eng|search|phone|mail|clock|star|masks?|kid_background|og_image|banner-web|"
    r"premiera-banner|fb-cover-default|buy-ticket[\w-]*)\.(?:png|jpe?g|gif|svg|webp)|"
    r"/storage/profiles/|/storage/upper-banner/|/upper-banner/|/carousels/|"
    r"/theatre/photos/small|/img/photos/small|/kimo_banner_unit|"
    r"/actors?/|/people/|/team/|/avatars?/|/cast/|/artists?/|"
    r"youtube|ytimg|/media-youtube/|facebook\.com/tr",
    re.I)
# Hosts that serve no working HTTPS (bnf.bg: "wrong version number", 2026-11-04).
# Their images are fetched over plain http and re-hosted, so the page never loads
# them from there.
HTTP_ONLY = {"bnf.bg"}
POSTER_NAME_HOSTS = {"zadkanala.bg", "toplocentrala.bg", "tba.art.bg", "sfumato.info",
                     "nationaltheatre.bg", "salzaismyah.bg", "theatre199.org"}
IMG_EXT_RE = re.compile(r"\.(?:jpe?g|png|webp)(?:$|[?#])", re.I)
POSTER_WORD_RE = re.compile(r"poster|plakat|plakati|afish|плакат|афиш|kimo_grid2", re.I)
# A theme's resize suffix on every gallery file ("…_fit-detail.jpg" on all of
# Топлоцентрала's) is not part of the file's own name.
RESIZE_SUFFIX_RE = re.compile(r"[_-](?:fit|thumb|resize|crop|scale)[_-][a-z]+$|[_-]\d{2,4}x\d{2,4}$", re.I)


# ------------------------------------------------------------ polite fetching
class _Resp:
    def __init__(self, content, ctype=""):
        self.content, self.headers, self.status_code = content, {"Content-Type": ctype}, 200


class Net:
    """GET-only fetcher shared by the worker threads: robots.txt per host, one
    request at a time per host with HOST_GAP (or the host's Crawl-delay) between
    them, an optional on-disk cache for local runs (POSTER_CACHE_DIR)."""

    def __init__(self, budget=900, cache_dir=None, verbose=False):
        from netfetch import Fetcher
        # the run's own budget and pacing govern; netfetch's per-host budget and
        # its close-after-2-failures rule are for programme pages, where a host
        # failing twice is down — here a missing gallery image is normal
        self._mk = lambda: Fetcher(verbose=False, attempts=2, host_delay=0.0,
                                   budget_seconds=budget, host_budget=budget,
                                   host_fail_limit=10_000)
        self._local = threading.local()
        self._locks = defaultdict(threading.Lock)
        self._last = {}
        self._robots = {}
        self._robots_lock = threading.Lock()
        self.cache = pathlib.Path(cache_dir) if cache_dir else None
        self.t_end = time.monotonic() + budget
        self.verbose = verbose
        self.fetched = self.cached = self.refused = 0

    def _fetcher(self):
        f = getattr(self._local, "f", None)
        if f is None:
            f = self._local.f = self._mk()
        return f

    def over_budget(self):
        return time.monotonic() > self.t_end

    def _cache_path(self, url, kind):
        if not self.cache:
            return None
        d = self.cache / kind
        d.mkdir(parents=True, exist_ok=True)
        return d / hashlib.sha1(url.encode("utf-8")).hexdigest()

    def _allowed(self, url):
        import urllib.robotparser
        p = urllib.parse.urlsplit(url)
        host = (p.hostname or "").lower()
        with self._robots_lock:
            rp = self._robots.get(host)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            r = self._raw(f"{p.scheme or 'https'}://{p.hostname}/robots.txt", "robots")
            rp.parse((r.content.decode("utf-8", "replace") if r is not None else "").splitlines())
            delay = rp.crawl_delay("*")
            with self._robots_lock:
                self._robots[host] = rp
                if delay:
                    self._robots[host + "#delay"] = float(delay)
        return rp.can_fetch("*", url)

    def _gap(self, host):
        return max(HOST_GAP, self._robots.get(host + "#delay", 0.0))

    def _raw(self, url, kind):
        cp = self._cache_path(url, kind)
        if cp is not None and cp.exists():
            self.cached += 1
            data = cp.read_bytes()
            return _Resp(data) if data else None
        if self.over_budget():
            return None
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        with self._locks[host]:
            wait = self._last.get(host, 0.0) + self._gap(host) - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            r = self._fetcher().get(url, attempts=2,
                                    accept="image/*,*/*;q=0.8" if kind == "img" else None)
            self._last[host] = time.monotonic()
        self.fetched += 1
        if cp is not None and kind != "robots" and r is not None:
            cp.write_bytes(r.content)
        return r

    def get(self, url, kind="page"):
        if not self._allowed(url):
            self.refused += 1
            if self.verbose:
                print(f"  robots.txt disallows {url[:90]}", file=sys.stderr)
            return None
        return self._raw(url, kind)


def decode(content):
    head = content[:4096].decode("ascii", "ignore").lower()
    m = re.search(r"charset=[\"']?([\w-]+)", head)
    for enc in ([m.group(1)] if m else []) + ["utf-8", "windows-1251"]:
        try:
            return content.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", "replace")


# ------------------------------------------------------------ which page to read
def own_page(url):
    """(page URL to read, note) for a LINKS/VLINKS url, or (None, why not)."""
    if not url:
        return None, "no link"
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower().removeprefix("www.")
    if host in ART_MIRROR:
        m = PEAKVIEW_ID_RE.search(urllib.parse.unquote(p.path))
        if not m:
            return None, f"{host} is not read directly and the link carries no production id"
        why = "Cloudflare challenge" if host == "mlt.bg" else "robots.txt blocks ClaudeBot"
        return (ART_PROD_URL.format(prod=m.group(1), theatre=ART_MIRROR[host]),
                f"{host} not read ({why}): the same production on theatre.art.bg")
    if host == "nationaltheatre.bg":
        m = NATIONAL_SLUG_RE.match(url)
        return (m.group(1) if m else url), ""
    if host == "theatre199.org":
        return re.sub(r"/(?:booking|tickets|\d{4}-\d\d-\d\d)[^/]*$", "", url), ""
    if host in ("epaygo.bg", "embed.urboapp.com", "tickets.ndk.bg"):
        return None, f"{host} is a ticket page, not the title's page"
    return url, ""


# ------------------------------------------------------------ candidate images
_ATTR_RE = re.compile(r"""(?:src|data-src|data-lazy-src|data-original|href|content)\s*=\s*["']([^"']+)["']""", re.I)
_URLCSS_RE = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""", re.I)
_META_IMG_RE = re.compile(
    r"""<meta[^>]+(?:property|name)=["'](?:og:image|og:image:url|twitter:image)["'][^>]*>""", re.I)
_CONTENT_RE = re.compile(r"""content=["']([^"']+)["']""", re.I)
_JSONLD_IMG_RE = re.compile(r'"image"\s*:\s*(?:\[\s*)?"([^"]+)"')
_QUOTED_IMG_RE = re.compile(r"""\(\s*['"](https?://[^'"\s]+?\.(?:jpe?g|png|webp))['"]""", re.I)
_SRCSET_RE = re.compile(r"""srcset=["']([^"']+)["']""", re.I)


def _abs(u, base):
    u = (u or "").strip().replace("&amp;", "&")
    if not u or u.startswith("data:") or u.startswith("javascript:"):
        return None
    if u.startswith("//"):
        u = "https:" + u
    u = urllib.parse.urljoin(base, u)
    if u.startswith("http://") and (urllib.parse.urlsplit(u).hostname or "").removeprefix("www.") not in HTTP_ONLY:
        u = "https://" + u[7:]
    return u if u.startswith(("https://", "http://")) else None


def bigger(u):
    """The full-size variant a gallery thumbnail URL points at, where the naming
    says so (WordPress -WxH / -scaled-thegem-*, Drupal image styles, *_small)."""
    v = re.sub(r"-scaled-thegem-[\w-]+(?=\.[a-z]+$)", "-scaled", u, flags=re.I)
    v = re.sub(r"-thegem-[\w-]+(?=\.[a-z]+$)", "", v, flags=re.I)
    v = re.sub(r"-\d{2,4}x\d{2,4}(?=\.[a-z]+$)", "", v, flags=re.I)
    v = re.sub(r"/styles/[^/]+/public/", "/", v)
    v = re.sub(r"/(?:small|medium)_(?=[^/]+$)", "/big_", v) if "theatre199.org" in v else v
    v = re.sub(r"/small_(?=[^/]+$)", "/", v) if "sofiatheatre.eu" in v else v
    return v


_TEASER_RE = re.compile(
    r"""<a\s[^>]*href=["']([^"'#]+)["'][^>]*>(?:(?!</a>).){0,700}?<img[^>]+?src=["']([^"']+)["']""",
    re.I | re.S)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)
_IMG_URL_ANY_RE = re.compile(r"""(?:src|data-src|data-lazy-src|href)=["']([^"']+?\.(?:jpe?g|png|webp)[^"']*)["']""", re.I)
_A_TEXT_RE = re.compile(r"""<a\s[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>""", re.I | re.S)
_ALT_RE = re.compile(r"""\balt=["']([^"']*)["']""", re.I)
_SRC_RE = re.compile(r"""\b(?:src|data-src|data-lazy-src)=["']([^"']+)["']""", re.I)


def foreign_images(html, base, other_titles=()):
    """Image URLs on the page that belong to ANOTHER title: a teaser (the image
    sits inside a link to another page of the site: "you may also like",
    the season's other plays) or an <img> whose alt text is another title of
    the listing. The 2026-11-04 harvest put НОРА's teaser image, shown on the
    СЕДЕМ НОЩИ НА ВИОЛА КАРАВЕЛОВА page, on both plays."""
    out = set()
    here = urllib.parse.urlsplit(base)
    here_path = urllib.parse.unquote(here.path).rstrip("/")
    for m in _TEASER_RE.finditer(html):
        href, src = m.group(1), m.group(2)
        tgt = urllib.parse.urlsplit(urllib.parse.urljoin(base, href.replace("&amp;", "&")))
        if IMG_EXT_RE.search(tgt.path):
            continue                       # a gallery thumbnail linking to its big image
        if (tgt.hostname or "").removeprefix("www.") != (here.hostname or "").removeprefix("www."):
            continue
        tpath = urllib.parse.unquote(tgt.path).rstrip("/")
        if tpath and tpath != here_path and tpath not in ("", "/"):
            u = _abs(src, base)
            if u:
                out.add(u)
                out.add(_abs(bigger(src), base))
    # a grid of other productions: an image followed closely by a link to another
    # page of the site whose text is another title of the listing
    for m in _IMG_URL_ANY_RE.finditer(html):
        win = html[m.end():m.end() + 1500]
        for a in _A_TEXT_RE.finditer(win):
            txt = norm_title(re.sub(r"<[^>]+>", " ", a.group(2)))
            if txt and txt in other_titles:
                out.add(_abs(m.group(1), base))
                out.add(_abs(bigger(m.group(1)), base))
                break
    others = {t for t in other_titles if len(t) >= 4}
    if others:
        for tag in _IMG_TAG_RE.findall(html):
            a, sm = _ALT_RE.search(tag), _SRC_RE.search(tag)
            if a and sm and norm_title(a.group(1)) in others:
                out.add(_abs(sm.group(1), base))
                out.add(_abs(bigger(sm.group(1)), base))
    out.discard(None)
    return out


def placeholder_images(html, base):
    """<img> sources whose alt text names a placeholder or a funder."""
    out = set()
    for tag in _IMG_TAG_RE.findall(html):
        a, sm = _ALT_RE.search(tag), _SRC_RE.search(tag)
        if a and sm and PLACEHOLDER_RE.search(a.group(1)):
            out.add(_abs(sm.group(1), base))
            out.add(_abs(bigger(sm.group(1)), base))
    out.discard(None)
    return out


_SITE_BLOCK_HINT = re.compile(r"""data-elementor-type=["'](?:popup|header|footer)|<footer\b|<nav\b""", re.I)


def strip_site_blocks(html):
    """The page without its site-wide containers (an Elementor popup, header or
    footer; <footer>; <nav>): images there are the site's, not the title's
    (2026-11-04: Cineland's "лев → €" banner in a popup, attached to a film)."""
    if not _SITE_BLOCK_HINT.search(html):
        return html
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.select('[data-elementor-type="popup"], [data-elementor-type="header"], '
                          '[data-elementor-type="footer"], footer, nav'):
        el.decompose()
    return str(soup)


def candidates(html, base, other_titles=()):
    """[(url, tier, how)] in page order: tier 1 = the page's key image for the
    title (og:image, its poster slot), tier 2 = other content images. Images
    that belong to another title (foreign_images) are left out."""
    out, seen = [], set(foreign_images(html, base, other_titles))
    seen |= placeholder_images(html, base)
    html = strip_site_blocks(html)
    host = (urllib.parse.urlsplit(base).hostname or "").lower().removeprefix("www.")
    # latin words of the page's own slug ("spektakli/fitzroy"): a gallery file
    # named after them ("FITZROY-web-01.png") is the title's designed art
    slug = urllib.parse.unquote(urllib.parse.urlsplit(base).path.rstrip("/").rsplit("/", 1)[-1])
    slug_tokens = [t for t in re.split(r"[^a-z]+", slug.lower()) if len(t) >= 5]

    def add(u, tier, how):
        u = _abs(bigger(u) if "kimo_images" not in (u or "") else u, base)
        if not u or u in seen:
            return
        path = urllib.parse.urlsplit(u).path
        if not IMG_EXT_RE.search(path) and "kimo_images" not in u:
            return
        if CHROME_RE.search(path.lower()) or CHROME_RE.search(u.lower()):
            return
        stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if LOGO_STEM_RE.search(stem):
            return
        if PLACEHOLDER_RE.search(urllib.parse.unquote(u).lower()):
            return
        seen.add(u)
        fname = urllib.parse.unquote(path.rsplit("/", 1)[-1]).lower()
        core = fname.rsplit(".", 1)[0]
        for _ in range(2):
            core = RESIZE_SUFFIX_RE.sub("", core)
        # A gallery file named as a poster ("…plakat…") is the title's poster only
        # where a show page's gallery is that show's alone: Възраждане's pages list
        # the season's other posters ("2026_09_ISTORII_PLAKAT…" on РОГОНОСЦИ's).
        if tier == 2 and host in POSTER_NAME_HOSTS and POSTER_WORD_RE.search(urllib.parse.unquote(path)):
            tier, how = 1, how + " (named as a poster)"
        elif tier == 2 and any(t in core for t in slug_tokens):
            tier, how = 1, how + " (named after the title)"
        out.append((u, tier, how))

    for m in _META_IMG_RE.finditer(html):
        c = _CONTENT_RE.search(m.group(0))
        if c:
            add(c.group(1), 1, "og:image")
    for m in _JSONLD_IMG_RE.finditer(html):
        add(m.group(1).replace("\\/", "/"), 1, "JSON-LD image")
    host = (urllib.parse.urlsplit(base).hostname or "").lower().removeprefix("www.")
    # the theatres' own poster slots (seen on their show pages, 2026-11-04)
    slots = {
        "zadkanala.bg": r"/styles/large750x/public/",
        "toplocentrala.bg": r"/attachments/Event/\d+/main/",
        "salzaismyah.bg": r"/uploads/events_image/",
        "nationaltheatre.bg": r"/storage/shows/[0-9a-f]+\.",
        "artvent.bg": r"/images/events/",
        "sofiatheatre.eu": r"/uploads/repertoires/(?!small_)",
        "sfumato.info": r"(?:^|/)img/photos/BIG",
        "tba.art.bg": r"(?:^|/)img/photos/BIG",
        "theatre199.org": r"kimo_grid2_image",
        "satirata.bg": r"/image/",
        "bnf.bg": r"/uploads/(?!s_)",          # Одеон: the full poster behind its thumbnail
    }
    slot = slots.get(host)
    attrs = [m.group(1) for m in _ATTR_RE.finditer(html)] + \
            [m.group(1) for m in _URLCSS_RE.finditer(html)] + \
            [m.group(1) for m in _QUOTED_IMG_RE.finditer(html)]   # javascript: openPhoto('…')
    for m in _SRCSET_RE.finditer(html):
        attrs += [p.strip().split(" ")[0] for p in m.group(1).split(",")]
    if slot:
        for a in attrs:
            if re.search(slot, a, re.I):
                add(a, 1, "the theatre's poster slot")
    if host in ("theatre.art.bg",):
        return out                    # its other images are the cast's headshots
    for a in attrs:
        add(a, 2, "gallery")
    return [c for c in out if c[1] == 1] + [c for c in out if c[1] == 2]


# ------------------------------------------------------------ title sanity check
NOISE = {"на", "и", "в", "за", "от", "с", "по", "до", "the", "a", "of", "and", "или",
         "театър", "спектакъл", "премиера", "постановка"}


_LOOKALIKE = str.maketrans("аеорсхукмтнвіј", "aeopcxykmthbij")


def _fold(s):
    """norm_title with Cyrillic letters that look Latin folded onto Latin: a title
    typed with a Latin "O" ("Oскар", Сатирата 2026-11-04) still matches its page."""
    return norm_title(s).translate(_LOOKALIKE)


def page_mentions(html, title):
    """True when the page names the title (a token of 3+ letters from it appears
    in the page text), or when the title has no such token. A page about another
    production would not."""
    toks = [t for t in _fold(title).split() if len(t) >= 3 and t not in {_fold(n) for n in NOISE}]
    if not toks:
        return True
    text = _fold(re.sub(r"<[^>]+>", " ", html))
    return any(t in text for t in toks[:4])


_HEAD_RES = (re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S),
             re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.I | re.S))
_META_TITLE_RE = re.compile(r"""<meta\b[^>]*(?:property|name)=["'](?:og:title|twitter:title)["'][^>]*>""", re.I)
_H2_RE = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.I | re.S)


def _text(fragment):
    import html as _html
    return _fold(_html.unescape(re.sub(r"<[^>]+>", " ", fragment)))


_A_OPEN_RE, _A_CLOSE_RE = re.compile(r"<a\b", re.I), re.compile(r"</a\s*>", re.I)


def _inside_link(html, pos):
    """True when position pos sits inside an open <a>…</a> (a repertoire card
    `<a href="/spektakli/x"><h2>X</h2></a>` on a home page: a link, not a heading)."""
    before = html[max(0, pos - 4000):pos]
    opens = [m.end() for m in _A_OPEN_RE.finditer(before)]
    closes = [m.end() for m in _A_CLOSE_RE.finditer(before)]
    return bool(opens) and (not closes or opens[-1] > closes[-1])


_CANON_RES = (re.compile(r"""<link\b[^>]*rel=["']canonical["'][^>]*>""", re.I),
              re.compile(r"""<meta\b[^>]*property=["']og:url["'][^>]*>""", re.I))
_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)


def root_canonical(html, asked):
    """The page's canonical / og:url when it is the site's root while the page asked
    for is not (a soft 404: the home page served with HTTP 200 at a dead show URL),
    else None."""
    if not html or urllib.parse.urlsplit(asked).path.strip("/") == "":
        return None
    for rx in _CANON_RES:
        for m in rx.finditer(html[:200000]):
            c = _HREF_RE.search(m.group(0)) or _CONTENT_RE.search(m.group(0))
            if c and urllib.parse.urlsplit(urllib.parse.urljoin(asked, c.group(1).strip())).path.strip("/") == "":
                return c.group(1).strip()
    return None


def page_identity(html, title):
    """True when the page's OWN headings name the title: half or more of its words
    (3+ letters, up to 6) in <title>, og:title / twitter:title and <h1> together,
    or in one <h2> that is not a link (Одеон's film pages). A nav menu, a
    repertoire list or a sidebar naming the title does not count: a page that now
    shows another production, or the theatre's home page served for a dead URL,
    keeps the title only there (page_mentions alone let both through)."""
    noise = {_fold(n) for n in NOISE}
    toks = [t for t in _fold(title).split() if len(t) >= 3 and t not in noise][:6]
    if not toks:
        return True
    need = max(1, (len(toks) + 1) // 2)
    parts = [m.group(1) for rx in _HEAD_RES for m in rx.finditer(html)]
    for m in _META_TITLE_RE.finditer(html):
        c = _CONTENT_RE.search(m.group(0))
        if c:
            parts.append(c.group(1))
    head = _text(" ".join(parts))
    if sum(t in head for t in toks) >= need:
        return True
    for m in _H2_RE.finditer(html):
        if "<a" in m.group(1).lower() or _inside_link(html, m.start()):
            continue
        h2 = _text(m.group(1))
        if sum(t in h2 for t in toks) >= need:
            return True
    return False


def moved_page(asked, final):
    """True when a page request ended on another page (a redirect to the home
    page or the repertoire for a dead URL): not the title's page."""
    if not final:
        return False
    a, f = urllib.parse.urlsplit(asked), urllib.parse.urlsplit(final)
    key = lambda p: ((p.hostname or "").lower().removeprefix("www."),          # noqa: E731
                     urllib.parse.unquote(p.path).rstrip("/").lower())
    return key(a) != key(f)


# ------------------------------------------------------------ choosing an image
def measure(data):
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im.load()
    return im.size


def classify(w, h, tier):
    """'poster' | 'key' | 'photo' | None (not usable) for an image of w x h."""
    long_, short = max(w, h), min(w, h)
    if short < 150 or long_ < 300:
        return None
    if tier == 1:
        if h >= 1.15 * w and long_ >= 300 and short >= 200:
            return "poster"             # Народен's own posters are 220x310
        if long_ >= 400 and w <= 2.4 * h:
            return "key"                # the theatre's own image; the card crops its middle
        return None
    if long_ >= 600 and w <= 2.0 * h and h <= 2.5 * w:
        return "photo"
    return None


RANK = {"poster": 0, "key": 1, "photo": 2}


def photo_score(w, h):
    """Smaller is better: closest to a 2:3 portrait, then the larger image."""
    return (abs(h / w - 1.45), -(w * h))


def pick(net, cands, chrome, verbose=False):
    """Download candidates in order until an official poster turns up; return
    the best {img, kind, w, h, how, data} or None, and the notes of what failed."""
    best, tried, notes = None, 0, []
    for url, tier, how in cands:
        if url in chrome:
            continue
        if tried >= MAX_IMAGES_PER_TITLE or net.over_budget():
            break
        if best and (best["kind"] == "poster" or (tier == 2 and best["kind"] == "key")):
            break
        tried += 1
        r = net.get(url, kind="img")
        if r is None or not r.content:
            notes.append(f"{url[:80]}: not fetched")
            continue
        data = r.content
        if hashlib.md5(data).hexdigest() in LOGO_MD5:
            notes.append(f"{url[:80]}: a known site logo")
            continue
        try:
            w, h = measure(data)
        except Exception:
            notes.append(f"{url[:80]}: not an image")
            continue
        kind = classify(w, h, tier)
        if not kind:
            notes.append(f"{url[:80]}: {w}x{h}, not usable as {'a key image' if tier == 1 else 'a photo'}")
            continue
        rec = {"img": url, "kind": kind, "w": w, "h": h, "how": how, "data": data}
        if best is None or RANK[kind] < RANK[best["kind"]] or (
                kind == best["kind"] == "photo" and photo_score(w, h) < photo_score(best["w"], best["h"])):
            best = rec
    return best, notes


def store(data, name):
    """Re-host under posters/<name>.<ext> (fetch_theatre_posters._store_poster:
    downscaled to 800 px, JPEG unless transparent). Returns (path, md5)."""
    from fetch_theatre_posters import _store_poster
    POSTERS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = POSTERS_DIR / f"{name}.orig"
    tmp.write_bytes(data)
    try:
        path = _store_poster(tmp, name)
    finally:
        tmp.unlink(missing_ok=True)
    return path, hashlib.md5((ROOT / path).read_bytes()).hexdigest()


# ------------------------------------------------------------ the catalogue
def _const(src, name):
    m = re.search(r"^const %s\s*=\s*(.*?);\s*$" % name, src, re.M)
    try:
        return json.loads(m.group(1)) if m else None
    except ValueError:
        return None


def catalogue(html_path=HTML):
    """(titles, venue_of, links) for every title on the listing:
    titles {key: title}, keys "show:<id>" / "film:<id>"."""
    src = pathlib.Path(html_path).read_text(encoding="utf-8")
    shows = {s["id"]: s for s in (_const(src, "SHOWS") or [])}
    films = {f["id"]: f for f in (_const(src, "FILMS") or [])}
    up_shows = {r[0] for r in (_const(src, "PERFORMANCES") or []) if r}
    up_films = {r[0] for r in (_const(src, "SHOWTIMES") or []) if r}
    titles, venue, links = {}, {}, defaultdict(list)
    for sid in up_shows & set(shows):
        titles["show:" + sid] = shows[sid].get("title") or ""
        venue["show:" + sid] = shows[sid].get("theatre") or "?"
    for row in _const(src, "LINKS") or []:
        if isinstance(row, list) and len(row) >= 2 and ("show:" + row[0]) in titles:
            links["show:" + row[0]].append(row[1])
    for fid in up_films & set(films):
        titles["film:" + fid] = films[fid].get("bg") or films[fid].get("en") or ""
        venue["film:" + fid] = "film"
    for row in _const(src, "VLINKS") or []:
        if isinstance(row, list) and len(row) >= 3 and ("film:" + row[0]) in titles:
            links["film:" + row[0]].append(row[2])
    return titles, venue, links


def load_sources(path=SOURCES):
    try:
        d = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    for k in ("shows", "films", "misses"):
        d.setdefault(k, {})
    return d


def save_sources(d, path=SOURCES):
    pathlib.Path(path).write_text(json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True)
                                  + "\n", encoding="utf-8")


def due(key, sources, today, force=False):
    kind, ident = key.split(":", 1)
    rec = sources["shows" if kind == "show" else "films"].get(ident)
    if force:
        return True
    if rec:
        if not (ROOT / rec.get("path", "")).is_file():
            return True
        if rec.get("pending"):
            return (today - dt.date.fromisoformat(rec["pending"].get("on", "2000-01-01"))).days >= HOLD_DAYS
        last = rec.get("checked") or "2000-01-01"
        return (today - dt.date.fromisoformat(last)).days >= RECHECK_DAYS
    miss = sources["misses"].get(key)
    if miss:
        return (today - dt.date.fromisoformat(miss.get("last", "2000-01-01"))).days >= RETRY_DAYS
    return True


# ------------------------------------------------------------ the harvest
def harvest(keys, titles, links, net, sources, today, verbose=False, film_need=None):
    """Read each title's own page(s), choose and store an image, record it.
    keys: "show:<id>" / "film:<id>". Films are only given an image when
    film_need(id) is true (no TMDB/programme poster). Returns a summary dict."""
    pages = {}                                   # key -> [(page, note)]
    for k in keys:
        ps = []
        for u in links.get(k, []):
            pg, note = own_page(u)
            ps.append((pg, note or ""))
        pages[k] = ps or [(None, "no own page linked")]

    # phase 1: fetch the pages, one worker per host
    by_host = defaultdict(set)
    for k, ps in pages.items():
        for pg, _ in ps:
            if pg:
                by_host[urllib.parse.urlsplit(pg).hostname].add(pg)
    html_of, moved = {}, {}

    def work_pages(urls):
        for u in sorted(urls):
            r = net.get(u)
            html_of[u] = decode(r.content) if r is not None and r.content else None
            final = getattr(r, "url", None) if r is not None else None
            if isinstance(final, str) and moved_page(u, final):
                moved[u] = final
            elif root_canonical(html_of[u], u):
                moved[u] = root_canonical(html_of[u], u)
    _run(work_pages, by_host)

    # images on 3+ different pages of one host are site chrome
    seen_on = defaultdict(set)
    og_of = defaultdict(set)                     # image -> pages that name it their og:image
    cands = {}
    all_titles = {norm_title(t) for t in titles.values() if t}
    for k, ps in pages.items():
        cl = []
        mine = norm_title(titles.get(k, ""))
        for pg, _ in ps:
            h = html_of.get(pg) if pg else None
            if h:
                for c in candidates(h, pg, all_titles - {mine}):
                    seen_on[c[0]].add(pg)
                    if c[2] in ("og:image", "JSON-LD image"):
                        og_of[c[0]].add(pg)
                    cl.append(c + (pg,))
        cands[k] = cl
    # Site chrome: the og:image of 2+ pages (a site-wide default), or an image on 3+
    # pages that is no page's og:image. Kept across runs: a nightly run reads a
    # handful of pages, too few to see it. An image that is ONE page's og:image is
    # that page's key image: on any other page it is a teaser, another title's.
    page_titles = defaultdict(set)
    for k, ps in pages.items():
        for pg, _ in ps:
            if pg:
                page_titles[pg].add(norm_title(titles.get(k, "")))
    # (one production on two venues' pages, theatre.art.bg …_3262_8_20 and _3262_22_20,
    # shares its image legitimately: chrome only when the pages are different titles)
    # og:image observations kept across runs (a nightly run reads one page of a host:
    # a new site-wide share image is seen as such once a second title's page shows it)
    og_seen = sources.setdefault("og_seen", {})
    for u, pgs in og_of.items():
        seen = og_seen.setdefault(u, {})
        for pg in pgs:
            seen[pg] = sorted(page_titles[pg])[0] if page_titles[pg] else ""
    if len(og_seen) > 6000:
        for u in sorted(og_seen)[:len(og_seen) - 6000]:
            og_seen.pop(u, None)
    chrome = ({u for u, pgs in og_of.items()
               if len(pgs) >= 2 and len(set().union(*(page_titles[p] for p in pgs))) >= 2}
              | {u for u, seen in og_seen.items() if len(set(seen.values())) >= 2}
              | {u for u, pgs in seen_on.items() if len(pgs) >= 3 and u not in og_of}
              | set(sources.get("chrome") or []))
    sources["chrome"] = sorted(chrome)[:3000]
    owner_img = {}                               # image -> the title whose record it is
    for bucket, pre in (("shows", "show:"), ("films", "film:")):
        for ident, rec in sources[bucket].items():
            if rec.get("img") and pre + ident not in pages:      # titles read now decide afresh
                owner_img[rec["img"]] = norm_title(titles.get(pre + ident, "")) or pre + ident
    for k, cl in cands.items():
        mine = norm_title(titles.get(k, ""))
        cands[k] = [c for c in cl
                    if not (len(og_of.get(c[0], ())) == 1 and c[3] not in og_of[c[0]])
                    and owner_img.get(c[0], mine) == mine]

    # phase 2: choose an image per title, one worker per image host
    results, lock = {}, threading.Lock()
    by_img_host = defaultdict(set)
    for k, cl in cands.items():
        h = urllib.parse.urlsplit(cl[0][0]).hostname if cl else "-"
        by_img_host[h].add(k)

    def work_titles(ks):
        for k in sorted(ks):
            kind, ident = k.split(":", 1)
            notes = [n for _, n in pages[k] if n]
            okpages = [pg for pg, _ in pages[k] if pg and html_of.get(pg)]
            badpages = [pg for pg, _ in pages[k] if pg and not html_of.get(pg)]
            notes += [f"page not read: {pg[:90]}" for pg in badpages]
            mism = []
            for pg in okpages:
                if pg in moved:
                    mism.append(pg)
                    notes.append(f"the page now leads to {moved[pg][:80]}: not used")
                elif not page_identity(html_of[pg], titles[k]):
                    mism.append(pg)
                    notes.append(f"the page's headings do not name the title: not used ({pg[:70]})")
            if mism and len(mism) == len(okpages):
                best = None
            else:
                cl = [c for c in cands[k] if c[3] not in mism]
                best, n2 = pick(net, [(u, t, how) for u, t, how, _ in cl], chrome, verbose)
                notes += n2
                if best:
                    best["page"] = next(pg for u, t, how, pg in cl if u == best["img"])
            with lock:
                results[k] = (best, notes, okpages or [pg for pg, _ in pages[k] if pg])
    _run(work_titles, by_img_host)

    summary = defaultdict(int)
    shows_json = {}
    try:
        shows_json = json.loads(THEATRE_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    for k in sorted(results):
        best, notes, tried = results[k]
        kind, ident = k.split(":", 1)
        bucket = "shows" if kind == "show" else "films"
        if best and kind == "film" and film_need and not film_need(ident):
            summary["film already has a poster"] += 1
            continue
        prev = sources[bucket].get(ident) or {}
        if best and prev.get("md5"):
            new_md5 = hashlib.md5(best["data"]).hexdigest()
            held = prev.get("pending") or {}
            if new_md5 == prev["md5"]:
                pass                            # the same image: refreshed below
            elif RANK.get(best["kind"], 9) > RANK.get(prev.get("kind"), 9):
                # a recorded poster is never replaced by a lesser kind (a season banner
                # or a site-wide share image on a recheck): kept, noted
                prev["checked"] = today.isoformat()
                prev["recheck_lesser"] = {"img": best["img"], "kind": best["kind"], "on": today.isoformat()}
                prev.pop("pending", None)
                summary[f"{kind} kept (recheck found only a {best['kind']})"] += 1
                continue
            elif not (held.get("img") == best["img"] and held.get("md5") == new_md5
                      and (today - dt.date.fromisoformat(held.get("on", today.isoformat()))).days >= HOLD_DAYS
                      and best["img"] not in chrome):
                # another image of the same rank: held for review, the recorded one stays
                # until a later run (HOLD_DAYS on) finds the same image again and no
                # other title's page has shown it meanwhile
                if held.get("img") != best["img"] or held.get("md5") != new_md5:
                    prev["pending"] = {"img": best["img"], "md5": new_md5, "kind": best["kind"],
                                       "page": best["page"], "on": today.isoformat()}
                summary[f"{kind} replacement held for review"] += 1
                continue
        if best:
            name = ident if kind == "show" else f"film-{ident}"
            path, md5 = store(best["data"], name)
            sources[bucket][ident] = {"page": best["page"], "img": best["img"],
                                      "kind": best["kind"], "w": best["w"], "h": best["h"],
                                      "how": best["how"], "path": path, "md5": md5,
                                      "checked": today.isoformat()}
            if prev.get("md5") and prev.get("md5") != md5:
                # a recheck that found another image (the page's headings still name
                # the title): kept for review in poster_health.md
                sources[bucket][ident]["replaced"] = {"img": prev.get("img"), "md5": prev["md5"],
                                                      "on": today.isoformat()}
                summary[f"{kind} replaced on recheck"] += 1
            sources["misses"].pop(k, None)
            if kind == "show":
                shows_json[ident] = path
            summary[f"{kind} {best['kind']}"] += 1
            if verbose:
                print(f"  [{best['kind']:6s}] {k:50s} {best['w']}x{best['h']} {best['page'][:70]}")
        else:
            if sources[bucket].get(ident):     # keep-previous: the recorded image stays
                sources[bucket][ident]["checked"] = today.isoformat()
                sources[bucket][ident]["recheck_found_nothing"] = today.isoformat()
                summary[f"{kind} kept (page has nothing now)"] += 1
                continue
            sources["misses"][k] = {"last": today.isoformat(), "tried": tried[:3],
                                    "why": (notes or ["no usable image on the page"])[:6]}
            summary[f"{kind} nothing"] += 1
            if verbose:
                print(f"  [none  ] {k:50s} {(notes or ['no usable image'])[0][:90]}")
    if shows_json:
        THEATRE_JSON.write_text(json.dumps(shows_json, ensure_ascii=False, indent=1,
                                           sort_keys=True), encoding="utf-8")
    return dict(summary)


def _run(fn, groups):
    ts = [threading.Thread(target=fn, args=(items,), daemon=True) for items in groups.values()]
    for t in ts:
        t.start()
    for t in ts:
        t.join()


def film_needs_poster(root=ROOT):
    """fid -> True when the film has no TMDB poster and no programme-page image."""
    def j(name):
        try:
            return json.loads((root / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    tmdb, links, info = j("tmdb_films.json"), j("film_links_posters.json"), j("film_info.json")

    def need(fid):
        return not ((tmdb.get(fid) or {}).get("poster_path") or links.get(fid)
                    or (info.get(fid) or {}).get("img"))
    return need


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma-separated ids (show or film)")
    ap.add_argument("--all", action="store_true", help="ignore the retry schedule")
    ap.add_argument("--films", action="store_true", help="films only")
    ap.add_argument("--shows", action="store_true", help="shows only")
    ap.add_argument("--budget", type=int, default=900)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    today = dt.date.today()
    titles, venue, links = catalogue()
    sources = load_sources()
    need = film_needs_poster()
    keys = set(titles)
    if a.films:
        keys = {k for k in keys if k.startswith("film:")}
    if a.shows:
        keys = {k for k in keys if k.startswith("show:")}
    # films: only those without any poster are worth a request
    keys = {k for k in keys if not k.startswith("film:") or need(k[5:])}
    if a.only:
        want = {x.strip() for x in a.only.split(",") if x.strip()}
        keys = {k for k in keys if k.split(":", 1)[1] in want}
    keys = {k for k in keys if due(k, sources, today, force=a.all or bool(a.only))}
    print(f"own-page posters: {len(keys)} title(s) to read "
          f"({sum(k.startswith('show:') for k in keys)} plays, {sum(k.startswith('film:') for k in keys)} films)")
    net = Net(budget=a.budget, cache_dir=os.environ.get("POSTER_CACHE_DIR"), verbose=a.verbose)
    summary = harvest(keys, titles, links, net, sources, today, a.verbose, film_need=need)
    save_sources(sources)
    print(f"own-page posters: {json.dumps(summary, ensure_ascii=False, sort_keys=True)}; "
          f"{net.fetched} fetched, {net.cached} from cache, {net.refused} refused by robots.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
