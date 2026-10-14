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
    4. inject_shows.py              merge synthesised theatre shows -> index.html
    5. fetch_tmdb.py                film posters + English titles -> tmdb_films.json
    6. harvest_cinema_posters.py    Cinema City / Кино Арена posters for titles TMDB lacks -> film_info.json
    7. fetch_theatre_posters.py     theatre posters              -> theatre_posters.json
    8. translate.py                 DeepL English for BG-only synopses/titles -> translations.json
    9. inject_data.py               inline the poster maps and film details -> index.html
   10. gen_share_pages.py           static /film/<id>/ and /show/<id>/ share pages -> film/, show/
   11. verify_build.py              gate: is the result actually usable?

Steps 1-10 are best-effort and never abort the run. Step 11 is not: if the rebuilt
index.html is broken, this exits non-zero so the workflow stops before the commit
and the previous, working site stays up. That gate exists because the 2026-09-16
refresh published a page that rendered perfectly and threw the moment you clicked
a film.
"""
import json, os, subprocess, sys, time, pathlib, datetime as dt

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
    ("film posters",    "fetch_tmdb.py",             []),
    ("cinema posters",  "harvest_cinema_posters.py", []),    # the cinemas' own posters for titles TMDB lacks
    ("theatre posters", "fetch_theatre_posters.py",  []),
    ("translations",    "translate.py",              []),    # auto-translate BG-only syn/titles -> EN cache
    ("inline posters",  "inject_data.py",            []),
    ("share pages",     "gen_share_pages.py",        []),
]

# Run after the steps above, and treated as a gate rather than best-effort.
VERIFY = ("verify",  "verify_build.py", [])


def run(name, script, extra):
    start = time.time()
    print(f"\n=== {name}: {script} ===", flush=True)
    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / script), *extra],
            cwd=str(ROOT), env=os.environ.copy(),
            capture_output=True, text=True, timeout=60 * 30,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        print(out, flush=True)
        ok = proc.returncode == 0
        tail = "\n".join(out.strip().splitlines()[-8:])
        return {"step": name, "script": script, "ok": ok, "code": proc.returncode,
                "seconds": round(time.time() - start, 1), "tail": tail}
    except Exception as e:
        print(f"  ! {script} crashed: {e}", file=sys.stderr, flush=True)
        return {"step": name, "script": script, "ok": False, "code": -1,
                "seconds": round(time.time() - start, 1), "tail": str(e)}


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
    results = [run(*s) for s in STEPS]
    official, aborted = official_summary(started, previous)
    festivals = festival_summary(started, prev_report.get("festivals") or {})

    # The gate. Everything above may fail softly; this may not.
    verdict = run(*VERIFY)
    results.append(verdict)

    report = {
        "ran": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "ok": all(r["ok"] for r in results),
        "publishable": verdict["ok"],
        "steps": results,
        "official": official,
        "festivals": festivals,
    }
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
