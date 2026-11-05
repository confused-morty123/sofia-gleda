#!/usr/bin/env python3
"""Sofia Gleda — the poster diagnostic and fix pass (owner 2026-11-05).

refresh_all.py runs this right after poster_health.py, and only when the TRUE
poster coverage (own image, posterpolicy.true_coverage) of plays or films is
under TRIGGER_PCT (95 %, the bar of gate 5c2). It never blocks publishing: it
always exits 0, and the gate decides as before.

  1. Diagnoses every title still without its own image by cause: no own page
     linked, only a ticket page linked, the page blocked (robots.txt or a bot
     challenge: never bypassed), the page could not be fetched, the page is not
     the title's (redirect, home page, another title), the page has no usable
     image, its images were rejected by the policy (and why), a new image held
     as pending, not read from its own page yet, the re-hosted file changed, or
     (films) only an aggregator's image. A theatre with several titles on stage
     and not one own image is flagged as a broken poster source / parser.
  2. Fixes what can be fixed honestly, within a time and request budget:
     re-reads the own pages of the missing titles now instead of waiting for the
     retry schedule (own_page_posters.harvest: the official poster, then the
     theatre's key image, then a production photo from the title's OWN page:
     owner rule, no poster but photos from the play -> one of the photos), with
     more of each page's images tried than the nightly step does (DEEP_MAX_IMAGES);
     retries pages that failed to load with a backoff (never one that is blocked
     or gone); rechecks pending holds whose time has come. It never searches by
     title and never uses an aggregator's or another production's image.
  3. Re-inlines the poster maps (inject_data.py) when it attached anything, so
     the same night's build shows them, and measures again.
  4. Writes the diagnosis into poster_health.json["fix"] (refresh_all.py copies
     it into build_report.json["poster_fix"]) and poster_health.md, adds one
     "poster-fix" warning to health.json (the workflow annotates the run with it,
     the local dashboard lists it) and prints one "POSTER_FIX {json}" line.

GET only, robots.txt obeyed, the pacing of own_page_posters.Net (2 s between two
requests to one host, a robots.txt Crawl-delay honoured), at most MAX_REQUESTS
requests and BUDGET seconds. When nothing can be fetched it says so in the report.

    python3 scripts/poster_fix.py               # only when under the bar
    python3 scripts/poster_fix.py --force       # run even at or above the bar
    python3 scripts/poster_fix.py --no-fix      # diagnose only, no requests
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, pathlib, re, subprocess, sys, time, traceback
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import posterpolicy as PP                                         # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH_JSON = ROOT / "poster_health.json"
HEALTH_MD = ROOT / "poster_health.md"
HEALTH_ITEMS = ROOT / "health.json"
TRIGGER_PCT = 95.0              # = posterpolicy.TRUE_BAR (test_parsers checks)
BUDGET = 300                    # seconds of network time for the whole pass
MAX_REQUESTS = 160              # pages + images + robots.txt, the whole pass
MAX_TITLES = 80                 # titles read in one pass
DEEP_MAX_IMAGES = 12            # images tried per title (the nightly step: 6)
RETRY_BACKOFF = (15, 45)        # seconds before the 2nd and 3rd try of a failed page
BROKEN_MIN = 3                  # titles on stage for a theatre-wide zero to mean "broken"
VENUE = "poster-fix"            # its health.json entries

# Causes (poster_health.md and build_report["poster_fix"]["causes"]).
NO_LINK = "no own page linked"
TICKET_ONLY = "only a ticket page linked, not the title's page"
BLOCKED = "own page blocked (robots.txt or a bot challenge; not bypassed)"
ROBOTS_UNREACHABLE = "robots.txt unreachable (5xx or no answer): the host is not read this run"
FETCH_FAILED = "own page or its image could not be fetched"
NOT_TITLES_PAGE = "the linked page is not the title's (redirect, home page or another title)"
NO_IMAGE = "own page has no usable image"
REJECTED = "own page's images rejected by the poster policy"
PENDING = "a new image is held as pending (hold not over)"
NOT_READ = "not read from its own page yet"
FILE_CHANGED = "the re-hosted file changed since it was read"
AGGREGATOR = "only an aggregator's image (the film does not link that page)"
OTHER = "other"

_ATTACHED_RE = re.compile(r"^(?:show|film) (?:poster|key|photo)$|replaced on recheck")
_MIRROR_NOTE_RE = re.compile(r"^[\w.-]+ not read \(")    # own_page_posters.own_page's ART_MIRROR note
_REJECT_NOTE_RE = re.compile(r"not usable as|a known site logo|not an image|^removed ", re.I)


# ------------------------------------------------------------ the trigger
def pct_of(cov):
    """Own-image coverage in % of {"with", "upcoming"} (None: nothing upcoming)."""
    try:
        return 100.0 * cov["with"] / cov["upcoming"] if cov.get("upcoming") else None
    except (AttributeError, KeyError, TypeError, ZeroDivisionError):
        return None


def below_trigger(health):
    """["shows"] / ["films"] / both: the metrics of a poster_health.json record (or
    measure() result) whose own-image coverage is under TRIGGER_PCT. Exact ratio,
    not the rounded pct: 505/532 (94.92 %) triggers, 95.0 % does not."""
    out = []
    for k in ("shows", "films"):
        cov = (health or {}).get(k) if isinstance(health, dict) else None
        if isinstance(cov, dict) and cov.get("upcoming") and 100.0 * cov["with"] < TRIGGER_PCT * cov["upcoming"]:
            out.append(k)
    return out


def needed(path=None):
    """True when poster_health.json (this run's) shows coverage under the trigger."""
    try:
        return bool(below_trigger(json.loads(pathlib.Path(path or HEALTH_JSON).read_text(encoding="utf-8"))))
    except (OSError, ValueError):
        return False


# ------------------------------------------------------------ polite, counted fetching
def make_net(budget, max_requests, cache_dir=None):
    """own_page_posters.Net that also stops at max_requests and remembers, per
    URL, how a request ended: "ok", "robots", "blocked: …", "gone: HTTP 404",
    "failed: …", "budget", or "robots: robots.txt unreachable (…)" when the host's
    robots.txt answered 5xx or not at all (RFC 9309: the host is not read this run)."""
    import own_page_posters as OPP

    class FixNet(OPP.Net):
        def __init__(self):
            super().__init__(budget=budget, cache_dir=cache_dir)
            self.max_requests = max_requests
            self.outcome = {}

        def over_budget(self):
            return super().over_budget() or self.fetched >= self.max_requests

        def get(self, url, kind="page"):
            if not self._allowed(url):
                self.refused += 1
                host = (OPP.urllib.parse.urlsplit(url).hostname or "").lower()
                why = self.robots_unreachable.get(host)
                self.outcome[url] = f"robots: {why}" if why else "robots"
                return None
            if self.over_budget():
                self.outcome[url] = "budget"
                return None
            f = self._fetcher()
            n = len(f.log)
            r = self._raw(url, kind)
            if r is not None and getattr(r, "content", None) is not None:
                self.outcome[url] = "ok"
            else:
                e = f.log[-1] if len(f.log) > n else {}
                if e.get("blocked"):
                    self.outcome[url] = f"blocked: {e['blocked']}"
                elif e.get("status") in (404, 410):
                    self.outcome[url] = f"gone: HTTP {e['status']}"
                else:
                    self.outcome[url] = "failed: " + str(e.get("error") or "no answer")[:80]
            return r

    return FixNet()


# ------------------------------------------------------------ diagnosis
def diagnose(key, true_cause, links, srcs, outcome, today):
    """(cause, detail) for one title still without its own image. key: "show:<id>"
    or "film:<id>"; true_cause: posterpolicy.true_poster's reason; links: the
    catalogue's {key: [urls]}; srcs: poster_sources.json; outcome: {url: how the
    request ended} of this pass (may be empty)."""
    import own_page_posters as OPP
    kind, ident = key.split(":", 1)
    rec = (srcs.get("shows" if kind == "show" else "films") or {}).get(ident) or {}
    tc = str(true_cause or "")
    if tc.startswith("the file changed"):
        return FILE_CHANGED, tc
    if "aggregator" in tc:
        return AGGREGATOR, tc
    held = rec.get("pending") or {}
    if held:
        try:
            ripe = (today - dt.date.fromisoformat(held.get("on", "2000-01-01"))).days >= OPP.HOLD_DAYS
        except ValueError:
            ripe = True
        if not ripe:
            return PENDING, f"{str(held.get('img', ''))[:90]} (held since {held.get('on')})"
    urls = [u for u in links.get(key, []) if u]
    if not urls:
        return NO_LINK, "the programme links no page for this title"
    pages = [OPP.own_page(u) for u in urls]
    readable = [pg for pg, _ in pages if pg]
    if not readable:
        return TICKET_ONLY, "; ".join(n for _, n in pages if n)[:200]
    outs = [outcome.get(pg) for pg in readable if outcome.get(pg)]
    if outs and not any(o == "ok" for o in outs):
        if any(o.startswith("robots: robots.txt unreachable") for o in outs):
            return ROBOTS_UNREACHABLE, "; ".join(f"{pg[:70]}: {outcome[pg]}" for pg in readable
                                                 if outcome.get(pg))[:240]
        if any(o.startswith("robots") or o.startswith("blocked") for o in outs):
            return BLOCKED, "; ".join(f"{pg[:70]}: {outcome[pg]}" for pg in readable if outcome.get(pg))[:240]
        return FETCH_FAILED, "; ".join(f"{pg[:70]}: {outcome[pg]}" for pg in readable if outcome.get(pg))[:240]
    miss = (srcs.get("misses") or {}).get(key)
    if not miss:
        return (NOT_READ, "") if not outs else (NO_IMAGE, "the page was read; nothing recorded")
    notes = [str(n) for n in (miss.get("why") or [])]
    if any(n == NO_LINK for n in notes):
        # recorded when the programme had no link; it has one now
        return NOT_READ, "a page is linked now; it was not linked when last tried"
    if any(n.startswith("page not read:") for n in notes):
        return FETCH_FAILED, "; ".join(n for n in notes if n.startswith("page not read:"))[:240]
    if any("do not name the title" in n or "now leads to" in n for n in notes):
        return NOT_TITLES_PAGE, "; ".join(n for n in notes if "do not name" in n or "now leads to" in n)[:240]
    rej = [n for n in notes if _REJECT_NOTE_RE.search(n)]
    if rej:
        return REJECTED, "; ".join(rej)[:300]
    if any(n.endswith(": not fetched") for n in notes):
        det = []
        for n in notes:
            if n.endswith(": not fetched"):
                pre = n.rsplit(": not fetched", 1)[0]
                how = next((o for u, o in outcome.items() if u.startswith(pre)), None)
                det.append(n + (f" ({how})" if how else ""))
        return FETCH_FAILED, "; ".join(det)[:240]
    mirror = [n for n in notes if _MIRROR_NOTE_RE.search(n)]
    if mirror and all(n in mirror or n == "no usable image on the page" for n in notes):
        # the theatre's own site is not read (a bot challenge / robots.txt); the
        # same production's theatre.art.bg page, read instead, has no key image
        return BLOCKED, mirror[0][:160] + "; that page has no usable image"
    return NO_IMAGE, "; ".join(notes)[:240]


def broken_parsers(by_group, causes, min_titles=BROKEN_MIN):
    """[{theatre, upcoming, with, causes, why}] for each theatre with at least
    min_titles titles on stage and not one own image: its poster source or the
    parser that reads it has returned nothing. by_group: true_coverage's
    "by_group"; causes: {id: cause}."""
    out = []
    for th, g in sorted((by_group or {}).items()):
        if th in ("?", "all") or g.get("upcoming", 0) < min_titles or g.get("with", 0) > 0:
            continue
        cc = Counter(causes.get(i, OTHER) for i in g.get("missing") or [])
        if cc and set(cc) == {NO_LINK}:
            why = "the programme parser returned no own-page link for any of its titles"
        elif cc and set(cc) <= {BLOCKED, FETCH_FAILED, ROBOTS_UNREACHABLE}:
            why = "none of its pages could be read"
        else:
            why = "its poster source returned nothing for the whole theatre: a parser has probably broken"
        out.append({"theatre": th, "upcoming": g["upcoming"], "with": 0, "causes": dict(cc), "why": why})
    return out


def diagnose_all(m, links, srcs, outcome, today):
    """{"shows": {id: (cause, detail)}, "films": {...}} for a measure() result."""
    return {label: {i: diagnose(f"{key}:{i}", why, links, srcs, outcome, today)
                    for i, why in sorted(m[label]["missing"].items())}
            for label, key in (("shows", "show"), ("films", "film"))}


# ------------------------------------------------------------ the fix
def select_todo(m, titles, links, srcs, today, film_need=None, cap=MAX_TITLES):
    """The titles worth a request in this pass, most promising first: the missing
    titles that have a readable own page (a page that failed tonight first), not
    those whose page was read tonight with nothing found (unless many of its
    images were tried: the pass tries more), plus every title whose pending hold
    is over."""
    import own_page_posters as OPP
    first, rest = [], []
    iso = today.isoformat()
    for label, key in (("shows", "show"), ("films", "film")):
        for i in sorted(m[label]["missing"]):
            k = f"{key}:{i}"
            if k not in titles or not any(OPP.own_page(u)[0] for u in links.get(k, []) if u):
                continue
            if key == "film" and film_need is not None and not film_need(i):
                continue
            miss = (srcs.get("misses") or {}).get(k) or {}
            notes = [str(n) for n in miss.get("why") or []]
            failed = any(n.startswith("page not read:") or n.endswith(": not fetched") for n in notes)
            if miss.get("last") == iso and not failed and \
                    sum(1 for n in notes if _REJECT_NOTE_RE.search(n)) < OPP.MAX_IMAGES_PER_TITLE - 1:
                continue
            (first if failed else rest).append(k)
    held = []
    for bucket, key in (("shows", "show"), ("films", "film")):
        for i, rec in sorted((srcs.get(bucket) or {}).items()):
            k = f"{key}:{i}"
            if not (rec.get("pending") and k in titles and k not in first and k not in rest):
                continue
            try:
                ripe = (today - dt.date.fromisoformat(rec["pending"].get("on", ""))).days >= OPP.HOLD_DAYS
            except ValueError:
                ripe = False
            if ripe:
                held.append(k)
    return (first + rest + held)[:cap]


def _retryable(key, srcs, outcome, links):
    """The title's own page failed to load and may answer later (not blocked by
    robots.txt or a challenge, not gone, not out of budget)."""
    import own_page_posters as OPP
    notes = [str(n) for n in ((srcs.get("misses") or {}).get(key) or {}).get("why") or []]
    pages = [OPP.own_page(u)[0] for u in links.get(key, []) if u]
    if any(n.endswith(": not fetched") for n in notes) and not any(n.startswith("page not read:") for n in notes):
        # the page was read, an image of it did not download
        imgs = [o for u, o in outcome.items() if any(u.startswith(n.rsplit(": not fetched", 1)[0][:80])
                                                     for n in notes if n.endswith(": not fetched"))]
        return not imgs or any(o.startswith("failed") for o in imgs)
    if not any(n.startswith("page not read:") for n in notes):
        return False
    outs = [outcome.get(pg) for pg in pages if pg]
    return any(o is None or o.startswith("failed") for o in outs) and \
        not any(o and o.startswith(("robots", "blocked", "gone")) for o in outs)


def fix_titles(todo, titles, links, net, srcs, today, film_need=None,
               backoff=RETRY_BACKOFF, sleep=time.sleep, t_end=None):
    """Read the own pages of `todo` (own_page_posters.harvest, with DEEP_MAX_IMAGES
    images per title), then retry the pages that failed to load, after each
    backoff, while the budget allows. Returns (summary {what: n}, retried keys)."""
    import own_page_posters as OPP
    summary, retried = Counter(), set()
    saved = OPP.MAX_IMAGES_PER_TITLE
    OPP.MAX_IMAGES_PER_TITLE = max(saved, DEEP_MAX_IMAGES)
    try:
        if todo:
            summary.update(OPP.harvest(set(todo), titles, links, net, srcs, today, film_need=film_need))
        outcome = getattr(net, "outcome", {})
        for wait in backoff:
            again = {k for k in todo if _retryable(k, srcs, outcome, links)}
            if not again or net.over_budget():
                break
            if t_end is not None and time.monotonic() + wait + 20 > t_end:
                break
            sleep(wait)
            retried |= again
            got = OPP.harvest(again, titles, links, net, srcs, today, film_need=film_need)
            for k, v in got.items():
                summary["retry: " + k] += v
    finally:
        OPP.MAX_IMAGES_PER_TITLE = saved
    return dict(summary), sorted(retried)


def attached(summary):
    return sum(v for k, v in (summary or {}).items() if _ATTACHED_RE.search(k.removeprefix("retry: ")))


# ------------------------------------------------------------ reporting
def report_section(fix):
    """poster_health.md's "Diagnostic and fix pass" section."""
    L = ["", "## Diagnostic and fix pass", "",
         f"- Triggered by: {', '.join(fix['triggered_by']) or 'a forced run'} under {fix['trigger']:g} %.",
         "- Coverage before -> after: " + "; ".join(
             f"{k} {fix['before'].get(k)} % -> {fix['after'].get(k)} %" for k in ("shows", "films")) + ".",
         f"- Read {fix['attempted']} title(s), attached {fix['attached']}, retried {len(fix['retried'])}; "
         f"{fix['requests']} request(s), {fix['refused']} refused by robots.txt, {fix['seconds']} s.",
         f"- Network: {fix['network']}."]
    if fix.get("errors"):
        L.append("- Errors: " + "; ".join(fix["errors"]))
    if fix["broken_parsers"]:
        L += ["", "### Theatres with no own image at all (a broken poster source or parser)", ""]
        for b in fix["broken_parsers"]:
            L.append(f"- {b['theatre']}: 0/{b['upcoming']}: {b['why']} "
                     f"({', '.join(f'{c} {n}' for c, n in sorted(b['causes'].items()))})")
    L += ["", "### Missing titles by cause", "", "| cause | plays | films |", "|---|---|---|"]
    for c in sorted(set(fix["causes"]["shows"]) | set(fix["causes"]["films"]),
                    key=lambda c: -(fix["causes"]["shows"].get(c, 0) + fix["causes"]["films"].get(c, 0))):
        L.append(f"| {c} | {fix['causes']['shows'].get(c, 0)} | {fix['causes']['films'].get(c, 0)} |")
    L += [""]
    for key, rows in (("show", fix["titles"]["shows"]), ("film", fix["titles"]["films"])):
        for i, (cause, detail) in sorted(rows.items(), key=lambda kv: (kv[1][0], kv[0])):
            L.append(f"- `{key}:{i}`: {cause}" + (f" ({detail[:200]})" if detail else ""))
    return "\n".join(L).rstrip() + "\n"


def health_message(fix):
    parts = []
    for k, name in (("shows", "plays"), ("films", "films")):
        b, a = fix["before"].get(k), fix["after"].get(k)
        if b is not None:
            parts.append(f"{name} {b} % -> {a} %")
    top = Counter()
    for label in ("shows", "films"):
        top.update(fix["causes"][label])
    msg = (f"poster diagnostic and fix pass (own-image coverage under {fix['trigger']:g} %): "
           + ", ".join(parts) + f"; attached {fix['attached']}"
           + ("; causes: " + ", ".join(f"{c} {n}" for c, n in top.most_common(4)) if top else ""))
    if fix["broken_parsers"]:
        msg += "; no own image for a whole theatre: " + ", ".join(b["theatre"] for b in fix["broken_parsers"])
    if not fix["network"].startswith("ok"):
        msg += f"; network: {fix['network']}"
    return msg + " (poster_health.md)"


def add_health_item(path, message, level="warning"):
    """Replace this pass's entries in health.json with `message`."""
    try:
        items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, ValueError):
        items = []
    if not isinstance(items, list):
        items = []
    items = [e for e in items if not (isinstance(e, dict) and e.get("venue") == VENUE)]
    items.append({"level": level, "venue": VENUE, "message": message})
    try:
        path.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    except OSError as e:
        print(f"poster fix: could not update health.json ({e})")


# ------------------------------------------------------------ the pass
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="run even at or above the bar")
    ap.add_argument("--no-fix", action="store_true", help="diagnose only, no requests")
    ap.add_argument("--budget", type=int, default=BUDGET, help="seconds of network time")
    ap.add_argument("--max-requests", type=int, default=MAX_REQUESTS)
    a = ap.parse_args(argv)
    import poster_health as PH
    import own_page_posters as OPP
    started = time.monotonic()
    today = dt.date.today()
    try:
        before = PH.measure()
    except Exception as e:
        print(f"poster fix: could not measure ({e.__class__.__name__}: {e}); nothing done")
        return 0
    trig = below_trigger(before)
    if not trig and not a.force:
        print("poster fix: not needed (own-image coverage of plays and films at or above "
              f"{TRIGGER_PCT:g} %)")
        return 0
    errors, summary, retried, todo = [], {}, [], []
    titles, links = {}, {}
    srcs = OPP.load_sources()
    net = None
    causes_before = {}
    try:
        titles, _venue, links = OPP.catalogue(PH.HTML)
        causes_before = diagnose_all(before, links, srcs, {}, today)
    except Exception as e:
        errors.append(f"could not read the catalogue: {e.__class__.__name__}: {e}")
    network = "not used (--no-fix)" if a.no_fix else "ok"
    if not a.no_fix and titles:
        try:
            need = OPP.film_needs_poster()
            todo = select_todo(before, titles, links, srcs, today, film_need=need)
            print(f"poster fix: {len(todo)} title(s) to read "
                  f"(under {TRIGGER_PCT:g} %: {', '.join(trig) or 'forced'})")
            if todo:
                net = make_net(a.budget, a.max_requests, os.environ.get("POSTER_CACHE_DIR"))
                summary, retried = fix_titles(todo, titles, links, net, srcs, today, film_need=need,
                                              t_end=started + a.budget)
                OPP.save_sources(srcs)
                pages_tried = [o for u, o in net.outcome.items() if not u.endswith("/robots.txt")]
                if pages_tried and all(o.startswith("failed") for o in pages_tried) and not net.cached:
                    network = f"unavailable: none of {len(pages_tried)} request(s) answered"
                elif net.over_budget():
                    network = f"ok, stopped at the budget ({a.budget} s / {a.max_requests} requests)"
                if attached(summary):
                    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "inject_data.py")],
                                       cwd=str(ROOT), capture_output=True, text=True, timeout=600)
                    if r.returncode:
                        errors.append("inject_data.py failed after the fix: "
                                      + ((r.stdout + r.stderr).strip().splitlines() or ["?"])[-1][:200])
        except Exception as e:
            errors.append(f"fix crashed: {e.__class__.__name__}: {e}")
            traceback.print_exc()
            if net is None:
                network = f"unavailable: {e.__class__.__name__}"
    try:
        after = PH.measure()
    except Exception as e:
        after = before
        errors.append(f"could not measure again: {e}")
    srcs = OPP.load_sources()
    diag = diagnose_all(after, links, srcs, getattr(net, "outcome", {}), today)
    all_causes = {i: c for i, (c, _) in diag["shows"].items()}
    fix = {
        "date": today.isoformat(),
        "trigger": TRIGGER_PCT,
        "triggered_by": trig,
        "before": {k: round(pct_of(before[k]), 1) if pct_of(before[k]) is not None else None
                   for k in ("shows", "films")},
        "after": {k: round(pct_of(after[k]), 1) if pct_of(after[k]) is not None else None
                  for k in ("shows", "films")},
        "counts": {k: {"before": f"{before[k]['with']}/{before[k]['upcoming']}",
                       "after": f"{after[k]['with']}/{after[k]['upcoming']}"} for k in ("shows", "films")},
        "causes_before": {k: dict(Counter(c for c, _ in v.values())) for k, v in causes_before.items()},
        "causes": {k: dict(Counter(c for c, _ in v.values())) for k, v in diag.items()},
        "titles": diag,
        "broken_parsers": broken_parsers(after["shows"]["by_group"], all_causes),
        "attempted": len(todo),
        "attached": attached(summary),
        "summary": summary,
        "retried": retried,
        "requests": getattr(net, "fetched", 0),
        "refused": getattr(net, "refused", 0),
        "seconds": round(time.monotonic() - started, 1),
        "network": network,
        "errors": errors,
    }
    # poster_health.json / .md now describe the state after the pass, with the pass
    try:
        rec = json.loads(HEALTH_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rec = {}
    fixed = dict(rec.get("fixed_this_run") or {})
    for k, v in summary.items():
        fixed["poster fix: " + k] = v
    new = PH.health_record({"films": {"pct": (rec.get("before") or {}).get("films", before["films"]["pct"])},
                            "shows": {"pct": (rec.get("before") or {}).get("shows", before["shows"]["pct"])}},
                           after, fixed, list(rec.get("errors") or []) + errors, today.isoformat())
    new["fix"] = fix
    HEALTH_JSON.write_text(json.dumps(new, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    HEALTH_MD.write_text(PH.report_md(after, PP.load_sources(), fixed, today.isoformat()) + report_section(fix),
                         encoding="utf-8")
    add_health_item(HEALTH_ITEMS, health_message(fix))
    brief = {k: fix[k] for k in ("before", "after", "attempted", "attached", "network", "errors")}
    brief["causes"] = fix["causes"]
    brief["broken_parsers"] = [b["theatre"] for b in fix["broken_parsers"]]
    print("POSTER_FIX " + json.dumps(brief, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                         # never fails the refresh by itself
        print(f"poster fix: crashed ({e.__class__.__name__}: {e}); the gate decides")
        sys.exit(0)
