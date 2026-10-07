#!/usr/bin/env python3
"""Offline preview build: today's UI source + the last committed listings.

build_index.py wipes the scraped data by design, so a UI change could otherwise
only be looked at after a full network refresh (~5 min, tokens for posters).
This builds index.dev.html instead, with no network and no tokens:

  1. build_index.py --out <PATH>               (UI from src/, seed data)
  2. transplant the constants scrape_programs.py writes (SNAPSHOT, SHOWTIMES,
     PERFORMANCES, LINKS, VLINKS) from the committed index.html (git HEAD or REF)
  3. inject_films.py / inject_shows.py / inject_data.py against it, reading the
     sidecar JSON files exactly as the real pipeline does

The result is what the next refresh would publish, minus any newly scraped
listings. index.dev.html is gitignored; never deploy it.

    python3 scripts/dev_build.py                         # -> index.dev.html
    python3 scripts/dev_build.py --from REF              # transplant from another git ref
    python3 scripts/dev_build.py --out index.test.html   # write to a different output file
    python3 scripts/dev_build.py --from REF --out PATH   # combine both options
"""
import os, re, subprocess, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent          # webapp/
SCRAPED = ["SNAPSHOT", "SHOWTIMES", "PERFORMANCES", "LINKS", "VLINKS"]


def run(*cmd, env=None):
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
    print(f"  {' '.join(cmd[1:])[:60]:60s} {'ok' if r.returncode == 0 else 'FAILED'}  {tail[0][:90]}")
    if r.returncode:
        sys.exit((r.stdout + r.stderr)[-2000:])


def const_line(text, name):
    m = re.search(rf"^const {name}\s*=.*$", text, flags=re.M)
    return m


def main():
    ref = sys.argv[sys.argv.index("--from") + 1] if "--from" in sys.argv else "HEAD"
    out_path = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "index.dev.html"
    OUT = ROOT / out_path
    print(f"dev_build: src/ + listings from {ref}:index.html -> {OUT.name}")
    run(sys.executable, "scripts/build_index.py", "--out", str(OUT))

    good = subprocess.run(["git", "show", f"{ref}:index.html"], cwd=ROOT,
                          capture_output=True, text=True)
    if good.returncode:
        sys.exit(f"cannot read {ref}:index.html — {good.stderr.strip()}")
    good = good.stdout
    page = OUT.read_text(encoding="utf-8")  # OUT is now defined in this scope
    moved = []
    for name in SCRAPED:
        src_m, dst_m = const_line(good, name), const_line(page, name)
        if src_m and dst_m:
            page = page[:dst_m.start()] + src_m.group(0) + page[dst_m.end():]
            moved.append(name)
    OUT.write_text(page, encoding="utf-8")
    print(f"  transplanted: {', '.join(moved)}")

    env = dict(os.environ, SOFIA_HTML=str(OUT))
    for step in ("inject_films.py", "inject_shows.py", "inject_data.py"):
        run(sys.executable, f"scripts/{step}", env=env)
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes) — preview with: open {OUT.name}")



if __name__ == "__main__":
    main()
