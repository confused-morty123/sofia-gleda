#!/usr/bin/env python3
"""Sofia Gleda — longer synopses for plays whose shown text is thin (owner spec
2026-11-07). Two honest routes, applied to SHOWS by inject_shows.py (so the
nightly refresh and dev_build.py both read them):

  1. scripts/synopsis_overrides.json — owner-curated. Texts the owner copies by
     hand from the theatre's own page in his own browser (mlt.bg sits behind a
     Cloudflare challenge we never bypass; some theatres disallow crawling):

         {"ee": {"bg": "…", "en": "… (optional)",
                 "source_url": "https://mlt.bg/ee___8013", "copied_on": "2026-11-07"}}

     Keys starting with "_" are comments. An entry always replaces the shown text
     (the owner chose it). An entry missing bg, an https source_url or an ISO
     copied_on is skipped and reported.

  2. synopsis_fills.json — read nightly (`python3 scripts/synopses.py`) from the
     production's own page on theatre.art.bg, only where SHOWS' LINKS entry
     already points there (Театър 199, Ателие 313, Сити Марк sell through it;
     robots.txt allows it). The lead paragraphs of the page's description are
     accepted only when the page's own title equals the show's title AND the
     page names the show's theatre: never text from another production. Each
     fill keeps its source_url and fetched_on; a fill replaces the shown text
     only while that is shorter than THIN characters and the fill is longer.
     Keep-previous: a page that cannot be read never removes a fill.

tickets.mlt.bg (Младежки театър's ticket centre) was checked on 2026-11-07: its
public page lists only date / time / title, and every event link goes to
DoLogin.aspx (sign-in required), so it carries no description to read.

    python3 scripts/synopses.py            # read the due theatre.art.bg pages, write synopsis_fills.json
    python3 scripts/synopses.py --apply    # also apply overrides + fills to index.html now
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, pathlib, re, sys, urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
OVERRIDES = ROOT / "scripts" / "synopsis_overrides.json"
FILLS = ROOT / "synopsis_fills.json"

THIN = 280                 # characters: shorter than this counts as a thin synopsis
MIN_FILL = 120             # a fill shorter than this is not worth replacing anything with
RECHECK_DAYS = 14          # a page that gave a text is read again after this many days
RETRY_DAYS = 5             # a page that gave nothing is retried after this many days
ART_HOSTS = {"theatre.art.bg"}
# How theatre.art.bg's venue line names a theatre when it differs from THEATRES[].name
# (checked 2026-11-07 on its production pages).
ART_VENUE_NAMES = {"citymark": "Сити Марк Арт Център"}
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _const(src, name):
    m = re.search(r"^const %s\s*=\s*(.*?);\s*$" % name, src, re.M)
    try:
        return json.loads(m.group(1)) if m else None
    except ValueError:
        return None


def slen(s):
    return len((s or "").strip())


# ------------------------------------------------------------------ overrides
def load_overrides(path=OVERRIDES, log=None):
    """{showId: {"bg", "en"?, "source_url", "copied_on"}} — valid entries only."""
    log = log if log is not None else []
    try:
        raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as e:
        log.append(f"{pathlib.Path(path).name}: not valid JSON ({e}); ignored")
        return {}
    out = {}
    for sid, rec in (raw.items() if isinstance(raw, dict) else []):
        if sid.startswith("_"):
            continue
        why = None
        if not SLUG_RE.fullmatch(sid):
            why = "id is not a lower-case ASCII slug"
        elif not isinstance(rec, dict) or not isinstance(rec.get("bg"), str) or not rec["bg"].strip():
            why = "no bg text"
        elif not str(rec.get("source_url") or "").startswith("https://"):
            why = "no https source_url"
        elif not _DATE_RE.fullmatch(str(rec.get("copied_on") or "")):
            why = "copied_on is not YYYY-MM-DD"
        elif "en" in rec and not (isinstance(rec["en"], str) and rec["en"].strip()):
            why = "en is empty"
        if why:
            log.append(f"override {sid!r} skipped: {why}")
            continue
        out[sid] = {"bg": rec["bg"].strip(), "source_url": rec["source_url"],
                    "copied_on": rec["copied_on"]}
        if rec.get("en"):
            out[sid]["en"] = rec["en"].strip()
    return out


def load_fills(path=FILLS):
    try:
        d = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------- apply
def apply(shows, overrides, fills, thin=THIN):
    """Edit the SHOWS list in place. Returns {"override": n, "fill": n}."""
    n = {"override": 0, "fill": 0}
    for sh in shows:
        sid = sh.get("id")
        ov = overrides.get(sid)
        if ov:
            if sh.get("synBg") != ov["bg"] or (ov.get("en") and sh.get("synEn") != ov["en"]):
                sh["synBg"] = ov["bg"]
                if ov.get("en"):
                    sh["synEn"] = ov["en"]
            n["override"] += 1
            continue
        f = fills.get(sid)
        text = (f or {}).get("bg") or ""
        if f and slen(text) >= MIN_FILL:
            if sh.get("synBg") == text:
                n["fill"] += 1                       # applied on an earlier run
            elif slen(sh.get("synBg")) < thin and slen(text) > slen(sh.get("synBg")):
                sh["synBg"] = text
                n["fill"] += 1
    return n


# ------------------------------------------------------- theatre.art.bg parser
_QUOTES = re.compile(r"[\"'„“”«»‘’`]")


def norm(s):
    s = (s or "").replace("\xa0", " ").lower().replace("ё", "е").replace("ѝ", "и")
    s = _QUOTES.sub(" ", s)
    s = re.sub(r"[^\w]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_art_production(html, title, theatre_names):
    """The lead description of a theatre.art.bg production page
    (div.postanovka div.anons: its <p> children before the "Повече" block), or
    None unless the page's own title (h2 in div.postanovka-people) equals `title`
    and its venue line names one of `theatre_names` (a name or a list)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    people = soup.select_one("div.postanovka div.postanovka-people")
    anons = soup.select_one("div.postanovka div.anons")
    if people is None or anons is None:
        return None
    h2 = people.find("h2")
    if h2 is None or norm(h2.get_text(" ", strip=True)) != norm(title):
        return None
    venue = norm(" ".join(t for t in people.stripped_strings if t != h2.get_text(strip=True))[:300])
    wants = [norm(n) for n in ([theatre_names] if isinstance(theatre_names, str) else theatre_names)]
    if not any(w and w in venue for w in wants):
        return None
    paras = []
    for ch in anons.children:
        name = getattr(ch, "name", None)
        if name == "div":                         # #myanons ("Повече": awards, duration) and after
            break
        if name == "p":
            t = re.sub(r"\s+", " ", ch.get_text(" ", strip=True)).strip()
            if t and norm(t) != norm(title):        # a paragraph that only repeats the title
                paras.append(t)
    text = "\n\n".join(paras)
    return text if slen(text) >= MIN_FILL else None


# ---------------------------------------------------------------------- fetch
def candidates(src, thin=THIN):
    """[(showId, title, theatre name, url)] — shows on the page whose synopsis is
    thin and whose LINKS entry is a theatre.art.bg production page."""
    shows = {s["id"]: s for s in (_const(src, "SHOWS") or [])}
    names = {t["id"]: t.get("name") or "" for t in (_const(src, "THEATRES") or [])}
    out = []
    for row in _const(src, "LINKS") or []:
        if not (isinstance(row, list) and len(row) == 2):
            continue
        sid, url = row
        sh = shows.get(sid)
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if sh and host in ART_HOSTS and slen(sh.get("synBg")) < thin:
            th = sh.get("theatre")
            out.append((sid, sh.get("title") or "",
                        [n for n in (names.get(th), ART_VENUE_NAMES.get(th)) if n], url))
    return out


def due(rec, url, today):
    if not rec or rec.get("source_url") != url:
        return True
    last = rec.get("fetched_on") if rec.get("bg") else rec.get("tried_on")
    try:
        age = (today - dt.date.fromisoformat(last)).days
    except (TypeError, ValueError):
        return True
    return age >= (RECHECK_DAYS if rec.get("bg") else RETRY_DAYS)


def fetch(src, fills, net, today, decode):
    stats = {"candidates": 0, "read": 0, "filled": 0, "no_match": 0, "unreadable": 0}
    for sid, title, theatre, url in candidates(src):
        stats["candidates"] += 1
        rec = fills.get(sid)
        if not due(rec, url, today):
            continue
        r = net.get(url)
        if r is None:
            stats["unreadable"] += 1                 # keep-previous
            continue
        stats["read"] += 1
        text = parse_art_production(decode(r.content), title, theatre)
        if text:
            fills[sid] = {"bg": text, "source_url": url, "fetched_on": today.isoformat(),
                          "title": title, "theatre": theatre[0] if theatre else ""}
            stats["filled"] += 1
        elif not (rec and rec.get("bg") and rec.get("source_url") == url):
            fills[sid] = {"source_url": url, "tried_on": today.isoformat(), "title": title}
            stats["no_match"] += 1
    return stats


def apply_to_html(html=HTML):
    from inject_shows import extract_array
    from csp import json_for_script
    src = pathlib.Path(html).read_text(encoding="utf-8")
    start, end, literal = extract_array(src, "SHOWS")
    shows = json.loads(literal)
    log = []
    n = apply(shows, load_overrides(log=log), load_fills())
    for line in log:
        print("  " + line, file=sys.stderr)
    new = json_for_script(shows)
    if new != literal:
        pathlib.Path(html).write_text(src[:start] + new + src[end:], encoding="utf-8")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="also apply to index.html")
    ap.add_argument("--budget", type=int, default=240)
    a = ap.parse_args()
    try:
        from official_theatres import decode
        from own_page_posters import Net
        src = HTML.read_text(encoding="utf-8")
        fills = load_fills()
        try:
            from listing_window import sofia_today
            today = sofia_today()
        except Exception:
            today = dt.date.today()
        stats = fetch(src, fills, Net(budget=a.budget), today, decode)
        FILLS.write_text(json.dumps(fills, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                         encoding="utf-8")
        print("SYNOPSES " + json.dumps(stats))
        if a.apply:
            print("applied " + json.dumps(apply_to_html()))
    except Exception as e:                           # never fails the refresh
        print(f"synopses: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
