#!/usr/bin/env python3
"""Offline preview build: today's UI source + the last committed listings.

build_index.py wipes the scraped data by design, so a UI change could otherwise
only be looked at after a full network refresh (~5 min, tokens for posters).
This builds index.dev.html instead, with no network and no tokens:

  1. build_index.py --out <PATH>               (UI from src/, seed data)
  2. transplant the constants scrape_programs.py writes (SNAPSHOT, SHOWTIMES,
     PERFORMANCES, LINKS, VLINKS, PRELIM_FROM, PRELIM_ROWS, …, FESTMARK) from the
     committed index.html (git HEAD or REF); a constant the UI source does not
     declare is skipped. A ref from before FESTMARK gets it from that ref's
     build_report.json festival rows, counted the way the scraper counts them
  3. inject_films.py / inject_shows.py / inject_data.py against it, reading the
     sidecar JSON files exactly as the real pipeline does
  4. the Content-Security-Policy meta (scripts/csp.py), after the last edit

The result is what the next refresh would publish, minus any newly scraped
listings. index.dev.html is gitignored; never deploy it.

    python3 scripts/dev_build.py                         # -> index.dev.html
    python3 scripts/dev_build.py --from REF              # transplant from another git ref
    python3 scripts/dev_build.py --out index.test.html   # write to a different output file
    python3 scripts/dev_build.py --from REF --out PATH   # combine both options
"""
import json, os, re, subprocess, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from csp import json_for_script, stamp_file  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent          # webapp/
SCRAPED = ["SNAPSHOT", "SHOWTIMES", "PERFORMANCES", "LINKS", "VLINKS", "PRELIM_FROM", "PRELIM_ROWS", "AGGLINKS", "SHOWATTR", "PREMIERES", "FESTMARK"]


def run(*cmd, env=None):
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
    print(f"  {' '.join(cmd[1:])[:60]:60s} {'ok' if r.returncode == 0 else 'FAILED'}  {tail[0][:90]}")
    if r.returncode:
        sys.exit((r.stdout + r.stderr)[-2000:])


def const_line(text, name):
    m = re.search(rf"^const {name}\s*=.*$", text, flags=re.M)
    return m


def festmark_from_report(ref, page):
    """FESTMARK for a ref whose index.html predates it: the festival rows in that
    ref's build_report.json ("fid|venue|date|time"), counted only where the page's
    SHOWTIMES has that very time, as scrape_programs.festival_marks counts them."""
    rep = subprocess.run(["git", "show", f"{ref}:build_report.json"], cwd=ROOT,
                         capture_output=True, text=True)
    st = const_line(page, "SHOWTIMES")
    try:
        fests = json.loads(rep.stdout).get("festivals") or {} if rep.returncode == 0 else {}
        rows = json.loads(st.group(0).split("=", 1)[1].strip().rstrip(";")) if st else []
    except ValueError:
        return None
    shown = {}
    for r in rows:
        shown.setdefault(tuple(r[:3]), set()).update(r[3])
    names = {"cinelibri": ("Синелибри", "CineLibri"), "kinomania": ("Киномания", "Kinomania")}
    out = {}
    for fest, rep_f in sorted(fests.items()):
        films = sorted({r.split("|")[0] for r in rep_f.get("rows") or []
                        if r.count("|") == 3 and r.split("|")[3] in shown.get(tuple(r.split("|")[:3]), ())})
        if films:
            bg, en = names.get(fest, (fest, fest))
            out[fest] = {"bg": bg, "en": en, "films": films, "shows": []}
    return out


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
    # A show id renamed since REF was built (show_aliases.json "renamed") is mapped
    # in the transplanted rows, as the next scrape would (ShowIndex.canonical).
    from show_identity import renamed_ids
    renames = renamed_ids()
    # A film id merged into the film it duplicated (film_merges.json) is mapped the
    # same way, rows of one film, venue and date joined (FilmIndex.canonical).
    from film_identity import load_merges, film_redirects, remap_scraped
    film_redirect = film_redirects(load_merges())
    for name in SCRAPED:
        src_m, dst_m = const_line(good, name), const_line(page, name)
        if src_m and dst_m:
            line = src_m.group(0)
            for old, new in renames.items():
                for q in (json.dumps(old, ensure_ascii=False), json.dumps(old)):
                    line = line.replace(q, json.dumps(new))
                line = line.replace(f'"show:{old}"', f'"show:{new}"')
            if film_redirect and any(o in line for o in film_redirect):
                head, _, lit = line.partition("=")
                lit = lit.strip()
                if lit.endswith(";"):
                    lit = lit[:-1]
                try:
                    val = remap_scraped(name, json.loads(lit), film_redirect)
                    line = f"{head.rstrip()}={json_for_script(val)};"
                    moved.append(f"{name} (merged films mapped)")
                except ValueError:
                    pass
            page = page[:dst_m.start()] + line + page[dst_m.end():]
            moved.append(name)
    # A VLINKS link kept from REF is stored the way the next scrape stores it for
    # its venue (scrape_programs.venue_vlink): a Cine Grand film page carries the
    # row's own cinema_slug — without it cinegrand.bg opens Парк Център for both.
    vl = const_line(page, "VLINKS")
    if vl:
        from official_sources import CG_CINEMAS, cinegrand_film_url
        try:
            rows = json.loads(vl.group(0).split("=", 1)[1].strip().rstrip(";"))
        except ValueError:
            rows = None
        # A venue REF still read from the aggregator alone (official_state.json
        # source "programata.bg") that now has its own programme (Cineland since
        # 2026-10-24): the next scrape confirms its rows and drops their programata
        # links (scrape_programs: confirmed_pairs), so they are dropped here too and
        # the row opens the venue's own BOOKING page.
        from official_sources import OFFICIAL
        st = subprocess.run(["git", "show", f"{ref}:official_state.json"], cwd=ROOT,
                            capture_output=True, text=True)
        try:
            agg_only = {v for v, x in (json.loads(st.stdout).get("venues") or {}).items()
                        if isinstance(x, dict) and "programata.bg" in str(x.get("source"))
                        and v in OFFICIAL} if st.returncode == 0 else set()
        except ValueError:
            agg_only = set()
        if isinstance(rows, list):
            fixed = [[r[0], r[1], cinegrand_film_url(r[2], r[1])]
                     if isinstance(r, list) and len(r) == 3 and r[1] in CG_CINEMAS else r for r in rows]
            n = sum(a != b for a, b in zip(rows, fixed))
            kept = [r for r in fixed if not (isinstance(r, list) and len(r) == 3 and r[1] in agg_only
                                             and "programata.bg" in str(r[2]))]
            dropped = len(fixed) - len(kept)
            if n or dropped:
                page = page[:vl.start()] + f"const VLINKS={json_for_script(kept)};" + page[vl.end():]
                if n:
                    moved.append(f"VLINKS ({n} Cine Grand film pages given their cinema)")
                if dropped:
                    moved.append(f"VLINKS ({dropped} programata links of {', '.join(sorted(agg_only))} "
                                 "dropped: its own programme is read now)")
            gone = {f"{r[0]}|{r[1]}" for r in fixed if r not in kept}
            al = const_line(page, "AGGLINKS") if gone else None
            if al:                              # their aggregator marks go with them
                try:
                    agg = json.loads(al.group(0).split("=", 1)[1].strip().rstrip(";"))
                except ValueError:
                    agg = None
                if isinstance(agg, dict):
                    agg = {k: v for k, v in agg.items() if k not in gone}
                    page = page[:al.start()] + f"const AGGLINKS={json_for_script(agg)};" + page[al.end():]
    if not const_line(good, "FESTMARK") and const_line(page, "FESTMARK"):
        fm = festmark_from_report(ref, page)          # a ref from before the scraper wrote it
        if fm:
            m = const_line(page, "FESTMARK")
            lit = json_for_script(fm, sort_keys=True)
            page = page[:m.start()] + f"const FESTMARK={lit};" + page[m.end():]
            moved.append("FESTMARK (from build_report.json)")
    OUT.write_text(page, encoding="utf-8")
    print(f"  transplanted: {', '.join(moved)}")

    env = dict(os.environ, SOFIA_HTML=str(OUT))
    for step in ("inject_films.py", "inject_shows.py", "inject_data.py"):
        run(sys.executable, f"scripts/{step}", env=env)
    # Last: the Content-Security-Policy meta for the page as it now is (script
    # hashes), exactly as refresh_all.py's final csp step does (scripts/csp.py).
    stamp_file(OUT, kind="page")
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes) — preview with: open {OUT.name}")



if __name__ == "__main__":
    main()
