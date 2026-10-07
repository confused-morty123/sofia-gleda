#!/usr/bin/env python3
"""Sofia Gleda — one rule about which poster may be attached to which id.

The bug this exists for: the "Oasis: Don't Look Back In Anger" limited-screening
card was showing artwork for a completely different film. The id
`oasis-screening` had a hand-seeded poster from
`softwareforcinema.com/f/movies/q/7/<32-hex>.jpeg` — a cinema ticketing backend,
with a content-hash filename that carries no evidence of what it depicts. It was
wrong from the day it was added, it won every merge because SEED always won, and
nothing in the pipeline could tell.

Two general defences, so this class of mistake cannot recur from *any* tier —
last week's JSON, this week's scrape, or a future hand-added SEED entry:

  1. Provenance. A poster URL is acceptable when it comes from a host we trust to
     publish one image per production — the venues' own sites and the two theatre
     aggregators — or, from anywhere else, when its filename still says what it
     is. An opaque hash on an unknown host is exactly the shape of the bad URL
     and is refused.

  2. Identity. A cinema-scope event that simply mirrors a film already in the
     catalogue must use that film's verified TMDB poster. Harvesting a second,
     unverifiable image for the same title is what created the mismatch, so the
     policy refuses to store one and names the film to alias to instead.

    from posterpolicy import Catalogue, reject_reason
    cat = Catalogue.from_html("index.html")
    why = reject_reason("oasis-screening", url, cat)     # -> str, or None if OK
"""
from __future__ import annotations
import json, re, pathlib
from urllib.parse import urlsplit

# Venues and aggregators that key an image to a production themselves. An opaque
# filename from these is still trustworthy: it is the venue's own id for the show.
TRUSTED_HOSTS = {
    "nationaltheatre.bg", "sofiatheatre.eu", "theatre199.org", "theatrevazrajdane.bg",
    "toplocentrala.bg", "mlt.bg", "zadkanala.bg", "sofiapuppet.com", "iamsofia.bg",
    "cinelibri.com", "cmart.info", "theatre.art.bg", "theatre.peakview.bg",
    "programata.bg", "tba.art.bg", "natfiz.bg", "satirata.bg", "iamstudio.bg",
    "comedyclub.bg", "melpomenatheatre.com", "atelie313.com", "artvent.bg",
    "ndk.bg", "image.tmdb.org", "sofiaopera.bg", "operasofia.bg", "salzaismyah.bg",
}

# Backends that serve whatever a cinema's ticketing system happened to upload.
# Never production art we can attribute. Add to this list, never remove blindly.
DISTRUSTED_HOSTS = {
    "softwareforcinema.com",     # ticketing CDN; content-hash filenames
}

# A filename that is only a hash: no word you could read and check against a title.
OPAQUE_STEM_RE = re.compile(r"^[0-9a-f]{16,}$|^[A-Za-z0-9+/_-]{24,}$")
WORDY_RE = re.compile(r"[A-Za-zЀ-ӿ]{4,}")

# A poster we have already fetched and re-hosted beside index.html. Same-origin,
# served over the app's own HTTPS, so there is no third-party host to trust or
# distrust and no hotlink/cert risk — the provenance was checked once, when the
# bytes were downloaded. Identity checks (mirror/knows) below still apply.
LOCAL_POSTER_RE = re.compile(r"^posters/[A-Za-z0-9][\w.\-]*\.(?:jpe?g|png|webp|gif|avif)$",
                             re.I)


def is_local_poster(url):
    return isinstance(url, str) and ".." not in url and bool(LOCAL_POSTER_RE.match(url))

# Suffixes that mark "this event is a screening of a film we already list".
MIRROR_SUFFIXES = ("-screening", "-prozhekciya", "-prem", "-premiere", "-gala",
                   "-retro-screening", "-show")


def host_of(url):
    return (urlsplit(url).hostname or "").lower().lstrip("www.") or ""


def _trusted(host):
    return any(host == h or host.endswith("." + h) for h in TRUSTED_HOSTS)


def _distrusted(host):
    return any(host == h or host.endswith("." + h) for h in DISTRUSTED_HOSTS)


def norm_title(s):
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"[„“”\"'’‘«»`\.\,\!\?\:\;\-–—_\(\)\[\]/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class Catalogue:
    """FILMS / SHOWS / EVENTS as the app sees them, plus the mirror lookup."""

    def __init__(self, films, shows, events):
        self.films, self.shows, self.events = films, shows, events
        self.film_ids = {f["id"] for f in films}
        self.show_ids = {s["id"] for s in shows}
        self.event_by_id = {e["id"]: e for e in events}
        self.film_by_title = {}
        for f in films:
            for t in (f.get("bg"), f.get("en")):
                if t:
                    self.film_by_title.setdefault(norm_title(t), f["id"])

    @classmethod
    def from_html(cls, path):
        src = pathlib.Path(path).read_text(encoding="utf-8")
        body = src.split("/* SOFIA-DATA-START */")[1].split("/* SOFIA-DATA-END */")[0]

        def arr(name):
            m = re.search(r"^const %s=(.*?);$" % name, body, re.M)
            return json.loads(m.group(1)) if m else []

        return cls(arr("FILMS"), arr("SHOWS"), arr("EVENTS"))

    def mirrors_film(self, poster_id):
        """If this id is really a screening of a catalogued film, return that
        film's id — the poster should come from TMDB, not from a scrape."""
        ev = self.event_by_id.get(poster_id)
        if not ev or ev.get("scope") != "cinema":
            return None
        for t in (ev.get("title"), ev.get("titleEn")):
            hit = self.film_by_title.get(norm_title(t))
            if hit:
                return hit
        for suf in MIRROR_SUFFIXES:
            if poster_id.endswith(suf) and poster_id[: -len(suf)] in self.film_ids:
                return poster_id[: -len(suf)]
        return None

    def knows(self, poster_id):
        # A film id is a legitimate POSTERS key too: a film TMDB cannot match keeps
        # its own programme-page og:image as a fallback override (films-only, written
        # by fetch_tmdb.py). Orphan protection stays — a typo'd id is still refused.
        return (poster_id in self.show_ids or poster_id in self.event_by_id
                or poster_id in self.film_ids)


def reject_reason(poster_id, url, catalogue=None):
    """None means the poster may be stored. A string says why it may not."""
    if not url or not isinstance(url, str):
        return "empty value"
    if not is_local_poster(url):
        if not url.startswith("https://"):
            return f"not an https URL ({url[:40]})"
        host = host_of(url)
        if not host:
            return "no host in the URL"
        if _distrusted(host):
            return (f"{host} is a ticketing backend, not production art — its images "
                    "cannot be attributed to a title")
        stem = urlsplit(url).path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if not _trusted(host) and OPAQUE_STEM_RE.match(stem) and not WORDY_RE.search(stem):
            return (f"opaque filename '{stem[:24]}' on untrusted host {host} — nothing "
                    "ties this image to the title")
    if catalogue is not None:
        mirrored = catalogue.mirrors_film(poster_id)
        if mirrored:
            return (f"this is a screening of the catalogued film '{mirrored}' — use "
                    f"its verified TMDB poster, not a harvested one")
        if not catalogue.knows(poster_id):
            return "no film, show or event in the catalogue has this id"
    return None


def filter_map(posters, catalogue=None, label="", log=None):
    """Drop every unacceptable entry from a {id: url} map. Returns (kept, dropped)."""
    kept, dropped = {}, {}
    for pid, url in (posters or {}).items():
        why = reject_reason(pid, url, catalogue)
        if why:
            dropped[pid] = why
            if log is not None:
                log.append(f"  dropped {pid!r}{' from ' + label if label else ''}: {why}")
        else:
            kept[pid] = url
    return kept, dropped
