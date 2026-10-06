#!/usr/bin/env python3
"""Sofia Gleda — weekly refresh orchestrator.

Runs the whole data pipeline in order, isolates each step so one failure can't
abort the rest, and writes build_report.json. This is what the GitHub Action
calls every Sunday.

    TMDB_TOKEN=... python3 scripts/refresh_all.py

Steps (all best-effort; a step that fails leaves the previous data in place):
    1. scrape_programs.py        cinema + theatre programmes -> index.html
    2. fetch_tmdb.py             film posters + English titles -> tmdb_films.json
    3. fetch_theatre_posters.py  theatre posters              -> theatre_posters.json
    4. inject_data.py            inline both poster maps       -> index.html
    5. verify_build.py           gate: is the result actually usable?

Steps 1-4 are best-effort and never abort the run. Step 5 is not: if the rebuilt
index.html is broken, this exits non-zero so the workflow stops before the commit
and the previous, working site stays up. That gate exists because the 2026-09-16
refresh published a page that rendered perfectly and threw the moment you clicked
a film.
"""
import json, os, subprocess, sys, time, pathlib, datetime as dt

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORT = ROOT / "build_report.json"

STEPS = [
    ("programmes",      "scrape_programs.py",        []),
    ("film posters",    "fetch_tmdb.py",             []),
    ("theatre posters", "fetch_theatre_posters.py",  []),
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
    results = [run(*s) for s in STEPS]

    # The gate. Everything above may fail softly; this may not.
    verdict = run(*VERIFY)
    results.append(verdict)

    report = {
        "ran": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "ok": all(r["ok"] for r in results),
        "publishable": verdict["ok"],
        "steps": results,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== summary ===")
    for r in results:
        print(f"  {'OK ' if r['ok'] else 'FAIL'} {r['step']:16s} {r['seconds']:>6}s")

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
