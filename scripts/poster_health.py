#!/usr/bin/env python3
"""Sofia Gleda — poster diagnostics and fix, run by refresh_all.py after the
poster steps (owner spec 2026-10-30 / 2026-11-04).

  1. Measures the TRUE poster coverage of the titles on the listing, films and
     plays, per venue: an image counts only when it is the title's own
     (posterpolicy.true_coverage: read from the title's own page and recorded in
     poster_sources.json, a TMDB poster, or a film's programme-page image).
  2. Reads, for the titles still without one, their own pages again
     (own_page_posters.py: official poster, then a production photo; never a
     search by title). Only titles that are due are read (a page that had
     nothing is retried after a few days), within a time budget.
  3. When that attached anything, re-inlines the poster maps (inject_data.py)
     and measures again.
  4. Writes poster_health.json (refresh_all.py copies it into
     build_report.json["poster_health"]) and poster_health.md (every title still
     missing, with its cause and the pages tried), and prints one
     "POSTER_HEALTH {json}" line.

It never fails the refresh: every error is reported and the step exits 0. The
publish gate (verify_build.py check 5c2) decides. Keep-previous: a page that
cannot be read never takes a poster away.

    python3 scripts/poster_health.py              # measure, fix what is due, report
    python3 scripts/poster_health.py --no-fix     # measure and report only
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, pathlib, re, subprocess, sys, traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import posterpolicy as PP                                         # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
OUT_JSON = ROOT / "poster_health.json"
OUT_MD = ROOT / "poster_health.md"
TARGET = 95.0
RECHECKS = 40


def _const(src, name):
    m = re.search(r"^const %s\s*=\s*(.*?);\s*$" % name, src, re.M)
    try:
        return json.loads(m.group(1)) if m else None
    except ValueError:
        return None


def upcoming(src):
    """(show ids, film ids) on the listing from today (Sofia) to the window end."""
    snap = _const(src, "SNAPSHOT") or {}
    to = ((snap.get("window") or {}).get("to")) or "9999-12-31"
    try:
        import listing_window
        frm = listing_window.sofia_today().isoformat()
    except Exception:
        frm = dt.date.today().isoformat()
    shows = {r[0] for r in (_const(src, "PERFORMANCES") or [])
             if isinstance(r, list) and len(r) > 1 and frm <= str(r[1]) <= to}
    films = {r[0] for r in (_const(src, "SHOWTIMES") or [])
             if isinstance(r, list) and len(r) > 2 and frm <= str(r[2]) <= to}
    return shows, films


def measure(html=HTML, root=ROOT):
    src = pathlib.Path(html).read_text(encoding="utf-8")
    maps = {n: (_const(src, n) or {}) for n in ("SHOWALIAS", "EVENTART", "POSTERS", "TMDBART", "SHOWART")}
    maps["VLINKS"] = _const(src, "VLINKS") or []
    sources = PP.load_sources(root)
    shows = {s["id"]: s for s in (_const(src, "SHOWS") or [])}
    films = {f["id"] for f in (_const(src, "FILMS") or [])}
    up_s, up_f = upcoming(src)
    up_s &= set(shows)
    up_f &= films
    theatre = {i: shows[i].get("theatre") or "?" for i in up_s}
    s_cov = PP.true_coverage(up_s, maps, sources, "shows", theatre, root)
    f_cov = PP.true_coverage(up_f, maps, sources, "films", None, root)
    shown = sum(1 for i in up_s if PP.browser_poster(i, maps))
    return {"shows": s_cov, "films": f_cov, "shows_displayed": shown,
            "ids": {"shows": sorted(up_s), "films": sorted(up_f)},
            "titles": {**{i: shows[i].get("title") for i in up_s}}}


def report_md(m, sources, fixed, today):
    s, f = m["shows"], m["films"]
    L = [f"# Poster health, {today}",
         "",
         f"- Films: {f['with']}/{f['upcoming']} ({f['pct']} %) show their own poster.",
         f"- Plays: {s['with']}/{s['upcoming']} ({s['pct']} %) show their own poster or a "
         f"production photo from their own page ({m['shows_displayed']} display some image).",
         f"- Target: {TARGET:.0f} % each. This run attached: "
         + (", ".join(f"{k} {v}" for k, v in sorted(fixed.items())) or "nothing") + ".",
         "", "## Plays per theatre", "", "| theatre | own image | missing |", "|---|---|---|"]
    for th, g in s["by_group"].items():
        L.append(f"| {th} | {g['with']}/{g['upcoming']} | {len(g['missing'])} |")
    for label, cov, key in (("Plays", s, "show"), ("Films", f, "film")):
        if not cov["missing"]:
            continue
        L += ["", f"## {label} without their own image", ""]
        by_cause = {}
        for i, why in sorted(cov["missing"].items()):
            by_cause.setdefault(why, []).append(i)
        for why, ids in sorted(by_cause.items(), key=lambda kv: -len(kv[1])):
            L.append(f"### {why} ({len(ids)})")
            for i in ids:
                miss = sources["misses"].get(f"{key}:{i}") or {}
                tried = ", ".join(miss.get("tried") or []) or "no own page linked"
                note = "; ".join(miss.get("why") or [])[:240]
                L.append(f"- `{i}`: tried {tried}" + (f" ({note})" if note else ""))
            L.append("")
    return "\n".join(L).rstrip() + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-fix", action="store_true", help="measure and report only")
    ap.add_argument("--budget", type=int, default=900, help="seconds of network time for the fix")
    a = ap.parse_args(argv)
    today = dt.date.today().isoformat()
    fixed, errors = {}, []
    try:
        before = measure()
    except Exception as e:
        print(f"poster health: could not measure ({e.__class__.__name__}: {e})")
        return 0
    if not a.no_fix:
        try:
            import own_page_posters as OPP
            todo = {f"show:{i}" for i in before["shows"]["missing"]} | \
                   {f"film:{i}" for i in before["films"]["missing"]}
            titles, _venue, links = OPP.catalogue(HTML)
            srcs = OPP.load_sources()
            d = dt.date.today()
            todo = {k for k in todo if k in titles and OPP.due(k, srcs, d)}
            # and a few recorded posters whose page is due for a re-read (a theatre
            # that replaces its poster): at most RECHECKS per run
            def _rec(k):
                return srcs["shows" if k.startswith("show:") else "films"].get(k.split(":", 1)[1]) or {}
            stale = sorted((k for k in titles if k not in todo and _rec(k).get("checked")
                            and OPP.due(k, srcs, d)), key=lambda k: (_rec(k)["checked"], k))
            todo |= set(stale[:RECHECKS])
            print(f"poster health: {len(todo)} title(s) without their own image are due for a retry")
            if todo:
                net = OPP.Net(budget=a.budget, cache_dir=os.environ.get("POSTER_CACHE_DIR"))
                fixed = OPP.harvest(todo, titles, links, net, srcs, d,
                                    film_need=OPP.film_needs_poster())
                OPP.save_sources(srcs)
                if any(not k.endswith("nothing") for k in fixed):
                    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "inject_data.py")],
                                       cwd=str(ROOT), capture_output=True, text=True, timeout=600)
                    if r.returncode:
                        errors.append("inject_data.py failed after the fix: "
                                      + (r.stdout + r.stderr).strip().splitlines()[-1][:200])
        except Exception as e:
            errors.append(f"fix step crashed: {e.__class__.__name__}: {e}")
            traceback.print_exc()
    try:
        after = measure()
    except Exception as e:
        after = before
        errors.append(f"could not measure again: {e}")
    sources = PP.load_sources()
    out = {
        "date": today,
        "target": TARGET,
        "films": {k: after["films"][k] for k in ("upcoming", "with", "pct", "how")},
        "shows": {k: after["shows"][k] for k in ("upcoming", "with", "pct", "how")},
        "shows_displayed": after["shows_displayed"],
        "per_theatre": {th: f"{g['with']}/{g['upcoming']}" for th, g in after["shows"]["by_group"].items()},
        "below_target": [k for k in ("films", "shows") if after[k]["pct"] < TARGET],
        "missing": {"shows": after["shows"]["missing"], "films": after["films"]["missing"]},
        "fixed_this_run": fixed,
        "before": {"films": before["films"]["pct"], "shows": before["shows"]["pct"]},
        "errors": errors,
    }
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
    OUT_MD.write_text(report_md(after, sources, fixed, today), encoding="utf-8")
    brief = {k: out[k] for k in ("films", "shows", "below_target", "fixed_this_run", "errors")}
    for k in ("films", "shows"):
        brief[k] = f"{out[k]['with']}/{out[k]['upcoming']} ({out[k]['pct']} %)"
    print("POSTER_HEALTH " + json.dumps(brief, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                         # never fails the refresh by itself
        print(f"poster health: crashed ({e.__class__.__name__}: {e}); the gate decides")
        sys.exit(0)
