#!/usr/bin/env python3
"""Sofia Gleda — synopsis diagnostics (owner spec 2026-11-07), run by
refresh_all.py after the poster steps. Measures the synopsis each listed title
actually shows (the UI's chain: plays SHOWSYN -> synBg; films synBg -> FILMINFO ->
TMDB ovBg; EN likewise) and counts, per theatre / for films, the titles whose
Bulgarian text is shorter than synopses.THIN characters (or missing).

Writes synopsis_health.json (refresh_all.py copies it into
build_report.json["synopsis_health"]) and synopsis_health.md (every thin play by
theatre, with its own page when the sheet links one), and prints one
"SYNOPSIS_HEALTH {json}" line. Never fails the refresh: always exits 0.

    python3 scripts/synopsis_health.py
"""
from __future__ import annotations
import collections, datetime as dt, json, os, pathlib, re, sys, traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import synopses as SY                                              # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
OUT_JSON = ROOT / "synopsis_health.json"
OUT_MD = ROOT / "synopsis_health.md"


def _obj_literal(src, name):
    """SHOWSYN / SHOWSYN_EN are hand-written multi-line {id: "…"} objects."""
    m = re.search(r"const %s\s*=\s*\{(.*?)\n\};" % name, src, re.S)
    out = {}
    for k, v in re.findall(r'^\s*"([^"]+)"\s*:\s*("(?:[^"\\]|\\.)*")', m.group(1) if m else "", re.M):
        try:
            out[k] = json.loads(v)
        except ValueError:
            pass
    return out


def measure(html=HTML, thin=SY.THIN):
    import poster_health as PH
    src = pathlib.Path(html).read_text(encoding="utf-8")
    c = lambda n: SY._const(src, n)                               # noqa: E731
    shows = {s["id"]: s for s in (c("SHOWS") or [])}
    films = {f["id"]: f for f in (c("FILMS") or [])}
    fi, art = c("FILMINFO") or {}, c("TMDBART") or {}
    synen = c("SYN_EN") or {}
    ssyn, ssyn_en = _obj_literal(src, "SHOWSYN"), _obj_literal(src, "SHOWSYN_EN")
    names = {t["id"]: t.get("name") or t["id"] for t in (c("THEATRES") or [])}
    links = {r[0]: r[1] for r in (c("LINKS") or []) if isinstance(r, list) and len(r) == 2}
    agg = c("AGGLINKS") or {}
    up_s, up_f = PH.upcoming(src)
    up_s &= set(shows)
    up_f &= set(films)
    ne = lambda v: v if v and v.strip() and v != "—" else ""       # noqa: E731

    per = collections.defaultdict(lambda: {"titles": 0, "thin": 0, "empty": 0, "thin_en": 0, "linked": 0})
    thin_rows = []
    for sid in sorted(up_s):
        sh = shows[sid]
        bg = ne(ssyn.get(sid)) or ne(sh.get("synBg"))
        en = ne(sh.get("synEn")) or ne(ssyn_en.get(sid)) or ne(synen.get(sid))
        p = per[sh.get("theatre") or "?"]
        p["titles"] += 1
        p["thin_en"] += SY.slen(en) < thin
        if SY.slen(bg) < thin:
            p["thin"] += 1
            p["empty"] += not bg
            own = links.get(sid) if sid not in agg else None
            p["linked"] += bool(own)
            thin_rows.append((sh.get("theatre") or "?", sid, sh.get("title") or sid, SY.slen(bg), own))
    f = {"titles": 0, "thin": 0, "empty": 0, "thin_en": 0}
    thin_films = []
    for fid in sorted(up_f):
        fl, i, a = films[fid], fi.get(fid) or {}, art.get(fid) or {}
        bg = ne(fl.get("synBg")) or ne(i.get("synBg")) or ne(a.get("ovBg"))
        en = ne(fl.get("synEn")) or ne(a.get("ov")) or ne(i.get("synEn")) or ne(synen.get(fid))
        f["titles"] += 1
        f["thin_en"] += SY.slen(en) < thin
        if SY.slen(bg) < thin:
            f["thin"] += 1
            f["empty"] += not bg
            thin_films.append((fid, fl.get("bg") or fid, SY.slen(bg)))
    ov, fills = SY.load_overrides(), SY.load_fills()
    routes = {
        "overrides": sum(1 for s in up_s if s in ov),
        "theatre_art_fills": sum(1 for s in up_s if (fills.get(s) or {}).get("bg")
                                 and (fills[s].get("source") or "theatre.art.bg") != "own-page"
                                 and shows[s].get("synBg") == fills[s]["bg"]),
        "own_page_fills": sum(1 for s in up_s if (fills.get(s) or {}).get("bg")
                              and fills[s].get("source") == "own-page"
                              and shows[s].get("synBg") == fills[s]["bg"]),
        "link_to_theatre": sum(1 for r in thin_rows if r[4]),
    }
    tot = {k: sum(p[k] for p in per.values()) for k in ("titles", "thin", "empty", "thin_en", "linked")}
    return {
        "date": dt.date.today().isoformat(), "threshold": thin,
        "shows": {**tot, "by_theatre": {k: dict(v, name=names.get(k, k)) for k, v in sorted(per.items())}},
        "films": f, "routes": routes,
        "_rows": {"shows": thin_rows, "films": thin_films},
    }


def write_md(r, path=OUT_MD):
    s, f = r["shows"], r["films"]
    out = [f"# Synopsis health — {r['date']}", "",
           f"Thin = Bulgarian synopsis shorter than {r['threshold']} characters (or none).", "",
           f"- Plays: {s['thin']} of {s['titles']} thin ({s['empty']} with none); "
           f"{s['linked']} of them link the theatre's own page under the text.",
           f"- Films: {f['thin']} of {f['titles']} thin ({f['empty']} with none).",
           f"- Routes: {r['routes']['overrides']} owner override(s) (scripts/synopsis_overrides.json), "
           f"{r['routes']['own_page_fills']} fill(s) from the plays' own production pages, "
           f"{r['routes']['theatre_art_fills']} fill(s) from theatre.art.bg production pages.", "",
           "| Theatre | Plays | Thin | None | Own-page link |", "|---|---:|---:|---:|---:|"]
    for k, v in s["by_theatre"].items():
        out.append(f"| {v['name']} | {v['titles']} | {v['thin']} | {v['empty']} | {v['linked']} |")
    out += ["", "## Thin plays", ""]
    for th, sid, title, n, own in r["_rows"]["shows"]:
        out.append(f"- `{sid}` ({th}) {title} — {n} chars" + (f" — {own}" if own else ""))
    out += ["", "## Thin films", ""]
    for fid, title, n in r["_rows"]["films"]:
        out.append(f"- `{fid}` {title} — {n} chars")
    pathlib.Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")


def main():
    try:
        r = measure()
        write_md(r)
        pub = {k: v for k, v in r.items() if not k.startswith("_")}
        OUT_JSON.write_text(json.dumps(pub, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("SYNOPSIS_HEALTH " + json.dumps({"shows": {k: r["shows"][k] for k in ("titles", "thin", "empty", "linked")},
                                               "films": r["films"], "routes": r["routes"]}))
    except Exception:
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    sys.exit(main())
