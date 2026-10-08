#!/usr/bin/env python3
"""Sofia Gleda — weekly refresh orchestrator.

Runs the whole data pipeline in order, isolates each step so one failure can't
abort the rest, and writes build_report.json. This is what the GitHub Action
calls every Sunday.

    TMDB_TOKEN=... python3 scripts/refresh_all.py

Steps (all best-effort; a step that fails leaves the previous data in place):
    1. scrape_programs.py        cinema + theatre programmes -> index.html
    2. inject_films.py           merge synthesised arthouse films -> index.html
    3. inject_shows.py           merge synthesised theatre shows -> index.html
    4. fetch_tmdb.py             film posters + English titles -> tmdb_films.json
    5. fetch_theatre_posters.py  theatre posters              -> theatre_posters.json
    6. inject_data.py            inline both poster maps       -> index.html
    7. verify_build.py           gate: is the result actually usable?

Steps 1-6 are best-effort and never abort the run. Step 7 is not: if the rebuilt
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
OFFICIAL_KEYS = ("status", "source", "covered", "days", "screenings", "written_screenings",
                 "prelim_from", "prelim_screenings", "discarded_aggregator", "minted", "error")


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
    ("theatre posters", "fetch_theatre_posters.py",  []),
    ("translations",    "translate.py",              []),    # auto-translate BG-only syn/titles -> EN cache
    ("inline posters",  "inject_data.py",            []),
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


def main():
    if not os.environ.get("TMDB_TOKEN"):
        print("note: TMDB_TOKEN not set — film posters will be skipped, "
              "previous posters kept.", file=sys.stderr)
    started = time.time()
    try:
        previous = json.loads(REPORT.read_text(encoding="utf-8")).get("official") or {}
    except (OSError, ValueError):
        previous = {}
    results = [run(*s) for s in STEPS]
    official, aborted = official_summary(started, previous)

    # The gate. Everything above may fail softly; this may not.
    verdict = run(*VERIFY)
    results.append(verdict)

    report = {
        "ran": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "ok": all(r["ok"] for r in results),
        "publishable": verdict["ok"],
        "steps": results,
        "official": official,
    }
    if aborted:
        report["official_aborted"] = aborted
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== summary ===")
    for r in results:
        print(f"  {'OK ' if r['ok'] else 'FAIL'} {r['step']:16s} {r['seconds']:>6}s")
    if official:
        print("\n=== cinema listings: each venue's own programme ===")
        for vid, o in official.items():
            cov = "..".join(o.get("covered") or []) or "—"
            print(f"  {vid:12s} {o.get('status') or '?':11s} {cov:23s} "
                  f"{o.get('screenings') or 0:>4} official, {o.get('prelim_screenings') or 0:>4} preliminary "
                  f"from {o.get('prelim_from') or '—'}"
                  + (f"  ! {o['error']}" if o.get("error") and o.get("status") == "unreachable" else ""))
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
