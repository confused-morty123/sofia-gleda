#!/usr/bin/env python3
"""Sofia Gleda — the refresh orchestrator.

Runs the whole data pipeline in order, isolates each step so one failure can't
abort the rest, and writes build_report.json. This is what the GitHub Action
calls every day (.github/workflows/refresh.yml). It takes no options: keys and
switches come from the environment (TMDB_TOKEN, OMDB_TOKEN, DEEPL_AUTH_KEY).

    TMDB_TOKEN=... python3 scripts/refresh_all.py

Steps (all best-effort; a step that fails leaves the previous data in place):
    1. scrape_programs.py           cinema + theatre programmes -> index.html, and the
                                    festivals: official_festivals.py reads CineLibri and
                                    Kinomania (screenings -> SHOWTIMES / PRELIM_ROWS,
                                    film details and posters -> film_info.json)
    2. fetch_film_info.py           synopsis/director/cast from the films' own pages -> film_info.json
    3. inject_films.py              merge synthesised arthouse films -> index.html
    4. inject_shows.py              merge synthesised theatre shows (+ synopsis overrides/fills) -> index.html
   4b. synopses.py --apply          thin play synopses from the production's own theatre.art.bg page
                                    -> synopsis_fills.json, applied with scripts/synopsis_overrides.json
    5. fetch_tmdb.py                film posters + English titles -> tmdb_films.json
    6. harvest_cinema_posters.py    Cinema City / Кино Арена posters for titles TMDB lacks -> film_info.json
    7. fetch_theatre_posters.py     theatre posters              -> theatre_posters.json
    8. translate.py                 DeepL English for BG-only synopses/titles -> translations.json
    9. fetch_events.py              festivals / limited runs programata.bg announces -> events.json
   10. fetch_event_posters.py       those events' key art, re-hosted -> posters/ev-*, event_posters.json
   11. inject_data.py               inline the poster maps, film details and auto events -> index.html
  11b. poster_health.py             TRUE poster coverage (own image), the own pages of the titles
                                    still missing (own_page_posters.py), re-inline -> poster_sources.json,
                                    poster_health.json/.md, build_report.json["poster_health"]
  11c. poster_fix.py                only when own-image coverage of plays or films is under 95 %:
                                    every missing title diagnosed by cause, broken poster sources
                                    flagged, own pages re-read / failed pages retried / ripe holds
                                    rechecked, re-inline -> poster_health.json["fix"],
                                    build_report.json["poster_fix"], a "poster-fix" health.json warning
  11d. synopsis_health.py          thin synopses per theatre + films -> synopsis_health.json/.md,
                                    build_report.json["synopsis_health"] (never blocks)
   12. gen_share_pages.py           the app at /home/ and /home/theatre/, and static /film/<id>/,
                                    /show/<id>/, /event/<id>/ share pages -> home/, film/, show/, event/
   13. csp.py                       the Content-Security-Policy meta (script hashes) of
                                    index.html and every share page, after the last edit
   14. verify_build.py              gate: is the result actually usable?

Every step's output is scrubbed of keys (a URL's apikey=…, a Bearer token, the
secrets' own values) before it is printed or kept in build_report.json, which is
committed to the public repository; health.json likewise.

Steps 1-12 are best-effort and never abort the run. Step 13 is not: if the rebuilt
index.html is broken, this exits non-zero so the workflow stops before the commit
and the previous, working site stays up. That gate exists because the 2026-09-16
refresh published a page that rendered perfectly and threw the moment you clicked
a film.
"""
import json, os, re, subprocess, sys, time, pathlib, datetime as dt

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORT = ROOT / "build_report.json"
CHANGES = ROOT / "changes.json"
# Per-venue official-programme facts kept in build_report.json. The next scrape
# reads them back to notice a source that suddenly yields a fraction of its rows.
OFFICIAL_KEYS = ("kind", "status", "source", "covered", "days", "screenings", "written_screenings",
                 "prelim_from", "prelim_screenings", "discarded_aggregator", "minted", "error")


# The scrape's festival state (changes.json "festivals": per festival its status,
# covered days, rows at app venues and run report). The next scrape reads it back
# from build_report.json: last run's festival rows leave before this run's are
# added, and a festival whose site is down keeps them (keep-previous).
def festival_summary(started, previous):
    """This run's festival state, or the previous run's when the scrape did not
    run or stopped itself."""
    try:
        ch = json.loads(CHANGES.read_text(encoding="utf-8"))
        fresh = dt.datetime.fromisoformat(ch.get("ran", "")).timestamp() >= started - 5
    except (OSError, ValueError, TypeError):
        return previous
    if not fresh or ch.get("aborted") or not isinstance(ch.get("festivals"), dict):
        return previous
    return ch["festivals"]


def official_summary(started, previous):
    """The scrape's per-venue official-source summary (changes.json), or the
    previous run's when this run's scrape aborted or did not run — a failed run
    must not become the baseline the next run is compared against."""
    try:
        ch = json.loads(CHANGES.read_text(encoding="utf-8"))
        fresh = dt.datetime.fromisoformat(ch.get("ran", "")).timestamp() >= started - 5
    except (OSError, ValueError, TypeError):
        return previous, None
    if not fresh or not ch.get("official"):
        return previous, None
    if ch.get("aborted"):
        return previous, ch["aborted"]
    return ({vid: {k: r.get(k) for k in OFFICIAL_KEYS} for vid, r in ch["official"].items()},
            None)

STEPS = [
    ("programmes",      "scrape_programs.py",        []),
    ("film info",       "fetch_film_info.py",        []),    # best-effort: synopsis/dir/cast from own pages
    ("arthouse films",  "inject_films.py",           []),
    ("theatre shows",   "inject_shows.py",           []),
    ("synopses",        "synopses.py",               ["--apply"]),  # thin play synopses: theatre.art.bg own pages + owner overrides
    ("film posters",    "fetch_tmdb.py",             []),
    ("cinema posters",  "harvest_cinema_posters.py", []),    # the cinemas' own posters for titles TMDB lacks
    ("theatre posters", "fetch_theatre_posters.py",  []),
    ("translations",    "translate.py",              []),    # auto-translate BG-only syn/titles -> EN cache
    ("events",          "fetch_events.py",           []),    # programata.bg festivals -> events.json (keep-previous)
    ("event posters",   "fetch_event_posters.py",    []),    # their key art -> posters/ev-*, event_posters.json
    ("inline posters",  "inject_data.py",            []),
    ("poster health",   "poster_health.py",          []),    # true coverage; own pages of the titles still missing; re-inlines
    ("poster fix",      "poster_fix.py",             []),    # only under 95 %: diagnosis by cause + deeper fix (poster_fix_needed)
    ("synopsis health", "synopsis_health.py",        []),    # thin synopses per theatre + films -> synopsis_health.json/.md (never blocks)
    ("share pages",     "gen_share_pages.py",        []),
    ("csp",             "csp.py",                    []),    # LAST edit of the pages: their CSP script hashes
]

# Lines of each step's output kept in build_report.json (default 8).
TAIL_LINES = {"theatre posters": 25, "poster health": 12, "poster fix": 12, "synopses": 6, "synopsis health": 4}

# Run after the steps above, and treated as a gate rather than best-effort.
VERIFY = ("verify",  "verify_build.py", [])


SECRET_ENV = ("TMDB_TOKEN", "OMDB_TOKEN", "DEEPL_AUTH_KEY", "GITHUB_TOKEN", "GH_TOKEN")
_KEY_PARAM = re.compile(r"(?i)\b((?:api|auth|access|client)?[_-]?(?:key|token|secret)|apikey|password|sig(?:nature)?)=([^&#\s\"'<>]+)")
_AUTH_HDR = re.compile(r"(?i)\b(Bearer|DeepL-Auth-Key|token)\s+[A-Za-z0-9._~+/=:-]{8,}")
_USERINFO = re.compile(r"(?i)\b(https?://)[^/\s:@]+:[^/\s@]+@")


def scrub(text):
    """text without credentials: the secrets' own values, ?apikey=…-style URL
    parameters, Authorization header values and user:password@ in URLs. Run on
    everything printed to the public Actions log and kept in build_report.json."""
    text = str(text or "")
    for name in SECRET_ENV:
        v = os.environ.get(name, "").strip()
        if len(v) >= 6:
            text = text.replace(v, "***")
    text = _KEY_PARAM.sub(lambda m: m.group(1) + "=***", text)
    text = _AUTH_HDR.sub(lambda m: m.group(1) + " ***", text)
    return _USERINFO.sub(lambda m: m.group(1) + "***@", text)


def scrub_health(path):
    """health.json messages pass through scrub() too (the workflow prints them)."""
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(items, list):
        return
    clean = [{k: scrub(v) if isinstance(v, str) else v for k, v in e.items()} if isinstance(e, dict) else e
             for e in items]
    if clean != items:
        path.write_text(json.dumps(clean, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def run(name, script, extra):
    start = time.time()
    print(f"\n=== {name}: {script} ===", flush=True)
    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / script), *extra],
            cwd=str(ROOT), env=os.environ.copy(),
            capture_output=True, text=True, timeout=60 * 30,
        )
        out = scrub((proc.stdout or "") + (proc.stderr or ""))
        print(out, flush=True)
        ok = proc.returncode == 0
        # the theatre-posters step ends with its policy log, the coverage line and
        # the missing list: keep enough of it to read them in build_report.json
        tail = "\n".join(out.strip().splitlines()[-TAIL_LINES.get(name, 8):])
        return {"step": name, "script": script, "ok": ok, "code": proc.returncode,
                "seconds": round(time.time() - start, 1), "tail": tail, "_out": out}
    except Exception as e:
        print(f"  ! {script} crashed: {scrub(e)}", file=sys.stderr, flush=True)
        return {"step": name, "script": script, "ok": False, "code": -1,
                "seconds": round(time.time() - start, 1), "tail": scrub(e)}


def poster_fix_needed(path=None):
    """Run poster_fix.py (step 11c)? Only when this run's poster_health.json shows
    own-image coverage of plays or films under poster_fix.TRIGGER_PCT (95 %)."""
    try:
        sys.path.insert(0, str(SCRIPTS))
        import poster_fix
        return poster_fix.needed(path or ROOT / "poster_health.json")
    except Exception as e:                          # a broken import must not stop the refresh
        print(f"  (poster fix: could not decide: {scrub(e)})", file=sys.stderr)
        return False


def drop_health_entries(path, venue):
    """health.json without the entries of `venue`."""
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(items, list):
        keep = [e for e in items if not (isinstance(e, dict) and e.get("venue") == venue)]
        if keep != items:
            path.write_text(json.dumps(keep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def run_steps(steps=None):
    """STEPS in order; the poster fix only when poster_fix_needed()."""
    results = []
    for s in steps or STEPS:
        if s[1] == "poster_fix.py" and not poster_fix_needed():
            print(f"\n=== {s[0]}: {s[1]} — not needed (own-image coverage at or above 95 %) ===", flush=True)
            results.append({"step": s[0], "script": s[1], "ok": True, "code": 0, "seconds": 0.0,
                            "skipped": "coverage at or above 95 %", "tail": ""})
            drop_health_entries(ROOT / "health.json", "poster-fix")   # no stale note from an earlier night
            continue
        results.append(run(*s))
    return results


def gate_warnings_of(out):
    """The "  warning: ..." lines verify_build.py prints: coverage shortfalls that do
    not block publishing (check 5c2 below its bar, 2026-11-05)."""
    return [line.split("warning:", 1)[1].strip() for line in str(out or "").splitlines()
            if line.startswith("  warning: ")]


def add_gate_warnings(path, warnings):
    """Replace the "publish-gate" entries of health.json with this run's gate
    warnings, so the workflow annotates them and the dashboard shows them."""
    try:
        items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, ValueError):
        items = []
    if not isinstance(items, list):
        items = []
    items = [e for e in items if not (isinstance(e, dict) and e.get("venue") == "publish-gate")]
    items += [{"level": "warning", "venue": "publish-gate", "message": scrub(w)} for w in warnings]
    try:
        path.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    except OSError as e:
        print(f"  (could not update health.json: {scrub(e)})", file=sys.stderr)


def poster_coverage_of(out):
    """The "POSTER_COVERAGE {json}" line verify_build.py prints (check 5c2)."""
    for line in reversed(str(out or "").splitlines()):
        if line.startswith("POSTER_COVERAGE "):
            try:
                cov = json.loads(line.split(" ", 1)[1])
                return cov if isinstance(cov, dict) else None
            except ValueError:
                return None
    return None


# verify_build 5c2 compares poster coverage with a BASELINE, kept here in
# build_report.json["posters"]["baseline"] = {"plays": pct, "films": pct}: the
# highest own-image coverage seen. Comparing only with the previous run let a
# poster source that dies slowly (2 points a night) slide past the 3-point warning
# for ever. The baseline rises with every better run and falls by
# POSTER_BASELINE_DECAY points per run, but only on a run the gate passed: an
# accepted, lasting change stops warning after a few runs, while a collapse that
# failed the gate keeps failing until the posters are back (or the owner deletes
# "baseline"). A run with fewer upcoming titles than verify_build's COVERAGE_MIN_*
# (POSTER_BASELINE_MIN, kept equal by test_verify) does not move it. An older
# build_report.json without "baseline" starts from its previous numbers.
POSTER_BASELINE_DECAY = 0.5
POSTER_BASELINE_MIN = {"plays": 100, "films": 40}


def _own_pct(cov):
    """Own-image coverage in % of a POSTER_COVERAGE block (or its "films" part)."""
    try:
        return 100.0 * cov["with"] / cov["upcoming"] if cov.get("upcoming") else None
    except (AttributeError, KeyError, TypeError, ZeroDivisionError):
        return None


def poster_baseline(prev, cur, published):
    """The baseline to store with this run's coverage `cur`, given the previous
    build_report.json["posters"] `prev` and whether the gate passed."""
    prev = prev if isinstance(prev, dict) and prev.get("metric") == "own-page" else {}
    cur = cur if isinstance(cur, dict) else {}
    old = prev.get("baseline") if isinstance(prev.get("baseline"), dict) else {}
    out = {}
    for label, p, c in (("plays", prev, cur),
                        ("films", prev.get("films") or {}, cur.get("films") or {})):
        b = old.get(label)
        if not isinstance(b, (int, float)) or isinstance(b, bool):
            b = _own_pct(p)
        now = _own_pct(c) if (c.get("upcoming") or 0) >= POSTER_BASELINE_MIN[label] else None
        if b is not None and published and now is not None:
            b -= POSTER_BASELINE_DECAY
        best = max(x for x in (b, now, -1.0) if x is not None)
        if best >= 0:
            out[label] = round(best, 2)
    return out


def ratings_coverage_of(out):
    """The "RATINGS_COVERAGE {json}" line verify_build.py prints (check 5c4): films
    with a genuine score, per source and in all (scripts/ratings_coverage.py)."""
    for line in reversed(str(out or "").splitlines()):
        if line.startswith("RATINGS_COVERAGE "):
            try:
                rep = json.loads(line.split(" ", 1)[1])
                return rep if isinstance(rep, dict) else None
            except ValueError:
                return None
    return None


def bg_titles_of(out):
    """The "BG_TITLES {json}" line verify_build.py prints (report 5c3): the titles
    shown in BG mode without a Bulgarian form."""
    for line in reversed(str(out or "").splitlines()):
        if line.startswith("BG_TITLES "):
            try:
                rep = json.loads(line.split(" ", 1)[1])
                return rep if isinstance(rep, dict) else None
            except ValueError:
                return None
    return None


def main(argv=None):
    # No options, but parse anyway: `--help` prints this docstring and exits, and
    # a stray argument is an error. Without this, `refresh_all.py --help` started
    # a full live refresh (13 Oct 2026).
    import argparse
    argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)
    if not os.environ.get("TMDB_TOKEN"):
        print("note: TMDB_TOKEN not set — film posters will be skipped, "
              "previous posters kept.", file=sys.stderr)
    started = time.time()
    try:
        prev_report = json.loads(REPORT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev_report = {}
    previous = prev_report.get("official") or {}
    results = run_steps()
    official, aborted = official_summary(started, previous)
    festivals = festival_summary(started, prev_report.get("festivals") or {})

    scrub_health(ROOT / "health.json")

    # The gate. Everything above may fail softly; this may not.
    verdict = run(*VERIFY)
    results.append(verdict)
    posters = poster_coverage_of(verdict.get("_out", ""))
    bg_titles = bg_titles_of(verdict.get("_out", ""))
    gate_warnings = gate_warnings_of(verdict.get("_out", ""))
    add_gate_warnings(ROOT / "health.json", gate_warnings)
    ratings = ratings_coverage_of(verdict.get("_out", ""))
    for r in results:
        r.pop("_out", None)

    report = {
        "ran": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "ok": all(r["ok"] for r in results),
        "publishable": verdict["ok"],
        "steps": results,
        "official": official,
        "festivals": festivals,
    }
    baseline = poster_baseline(prev_report.get("posters"), posters, verdict["ok"])
    if posters:
        report["posters"] = posters               # verify_build 5c2 compares with it next run
        if baseline:
            posters["baseline"] = baseline
    elif baseline:                                # no numbers this run: keep the comparison point
        report["posters"] = {"metric": "own-page", "baseline": baseline}
    try:                                          # poster_health.py's notes (the dashboard reads them)
        report["poster_health"] = json.loads((ROOT / "poster_health.json").read_text(encoding="utf-8"))
        _fix = report["poster_health"].get("fix")
        if isinstance(_fix, dict) and _fix.get("date") == dt.date.today().isoformat():
            report["poster_fix"] = report["poster_health"]["fix"]   # poster_fix.py ran this night
    except (OSError, ValueError, AttributeError):
        pass
    try:                                          # synopsis_health.py's notes
        report["synopsis_health"] = json.loads((ROOT / "synopsis_health.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if gate_warnings:
        report["gate_warnings"] = gate_warnings   # published anyway: coverage, not data integrity
    if bg_titles is not None:
        report["bg_titles"] = bg_titles           # titles shown in BG mode without a Bulgarian form
    if ratings is not None:
        report["ratings_coverage"] = ratings      # verify_build 5c4 compares with it next run; the dashboard reads it
    if aborted:
        report["official_aborted"] = aborted
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== summary ===")
    for r in results:
        print(f"  {'OK ' if r['ok'] else 'FAIL'} {r['step']:16s} {r['seconds']:>6}s")
    if official:
        print("\n=== cinema and theatre listings: each venue's own programme ===")
        for vid, o in official.items():
            cov = "..".join(o.get("covered") or []) or "—"
            print(f"  {vid:12s} {o.get('status') or '?':11s} {cov:23s} "
                  f"{o.get('screenings') or 0:>4} official, {o.get('prelim_screenings') or 0:>4} preliminary "
                  f"from {o.get('prelim_from') or '—'}"
                  + (f"  ! {o['error']}" if o.get("error") and o.get("status") == "unreachable" else ""))
    if festivals:
        print("\n=== festival programmes (official_festivals.py) ===")
        for fest, f in festivals.items():
            cov = "..".join(f.get("covered") or []) or "—"
            print(f"  {fest:10s} {f.get('status') or '?':13s} {cov:23s} "
                  f"{f.get('confirmed') or 0:>3} confirmed, {f.get('prelim_row') or 0:>3} preliminary per row, "
                  f"{f.get('prelim_date') or 0:>3} by date, {len(f.get('contradicted') or [])} contradicted"
                  + (f"  ! {f['error']}" if f.get("error") else ""))
    if aborted:
        print("\nThe programme scrape stopped itself (official-source self-check):", file=sys.stderr)
        for a in aborted:
            print("  ✗ " + a, file=sys.stderr)

    if not verdict["ok"]:
        print("\nThe rebuilt index.html did not pass verification, so it will NOT be "
              "published. The live site keeps the last good build. See the 'verify' "
              "output above for what is wrong.", file=sys.stderr)
        return 1
    # A partial refresh still deploys with whatever data survived, as long as the
    # page itself is sound.
    return 0


if __name__ == "__main__":
    sys.exit(main())
