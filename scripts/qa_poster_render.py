#!/usr/bin/env python3
"""Sofia Gleda — poster coverage as a visitor sees it (local QA, Playwright).

Opens the built page (file://) in Chromium, asks the app itself which image each
title on the listing shows (realPoster() + safeImg(), the code the cards run),
loads every one of those images in the page and counts the titles whose real
image actually loaded (naturalWidth > 0, at least 150 px on its short side): the
rest show the generated art. It then crosses that with the true metric
(posterpolicy.true_coverage) so the two numbers can be compared:

  displayed   an image loaded (what the old gate counted, plus the load check)
  own         an image loaded AND it is provably the title's own

    python3 scripts/qa_poster_render.py                 # index.html
    python3 scripts/qa_poster_render.py --html other.html --json out.json

Network: only the images the page itself would load (re-hosted posters are
local files; TMDB posters come from image.tmdb.org, like for a visitor).
"""
from __future__ import annotations
import argparse, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import posterpolicy as PP                                    # noqa: E402
import poster_health as PH                                   # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent

JS = r"""
async () => {
  const keys = (m) => m instanceof Map ? [...m.keys()] : Object.keys(m || {});
  const shows = keys(typeof _PERFS_BY_SHOW === 'object' ? _PERFS_BY_SHOW : null);
  const films = keys(typeof _ROWS_BY_FILM === 'object' ? _ROWS_BY_FILM : null);
  const one = (id) => new Promise((res) => {
    const src = safeImg(realPoster(id, 'w342'));
    if (!src) return res([id, null, 0, 0]);
    const im = new Image();
    const t = setTimeout(() => res([id, src, -1, -1]), 25000);
    im.onload = () => { clearTimeout(t); res([id, src, im.naturalWidth, im.naturalHeight]); };
    im.onerror = () => { clearTimeout(t); res([id, src, 0, 0]); };
    im.src = src;
  });
  const out = {shows: [], films: []};
  for (const [k, ids] of [['shows', shows], ['films', films]]) {
    for (let i = 0; i < ids.length; i += 40) out[k].push(...await Promise.all(ids.slice(i, i + 40).map(one)));
  }
  return out;
}
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--html", default=str(ROOT / "index.html"))
    ap.add_argument("--json", help="write the per-title results here")
    a = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright
    html = pathlib.Path(a.html).resolve()
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 393, "height": 852}, locale="bg-BG")
        pg.goto(html.as_uri(), wait_until="load", timeout=30000)
        pg.wait_for_timeout(1500)
        res = pg.evaluate(JS)
        b.close()
    m = PH.measure(html, html.parent)
    report = {}
    for kind in ("shows", "films"):
        universe = set(m["ids"][kind])
        rows = [r for r in res[kind] if r[0] in universe]
        true_missing = set(m[kind]["missing"])
        ids_true = {r[0] for r in rows}
        loaded = {r[0] for r in rows if r[2] and r[3] and min(r[2], r[3]) >= 150}
        failed = sorted(r[0] for r in rows if r[1] and r[0] not in loaded)
        none = sorted(r[0] for r in rows if not r[1])
        own = loaded - true_missing
        n = len(ids_true)
        report[kind] = {"listed": n, "displayed": len(loaded), "own": len(own),
                        "displayed_pct": round(100 * len(loaded) / n, 1) if n else 100.0,
                        "own_pct": round(100 * len(own) / n, 1) if n else 100.0,
                        "image_failed_or_tiny": failed, "generated_art": none,
                        "loaded_but_not_own": sorted(loaded & true_missing)}
        print(f"{kind}: {n} on the listing; an image loads for {len(loaded)} "
              f"({report[kind]['displayed_pct']} %); own image {len(own)} ({report[kind]['own_pct']} %); "
              f"{len(none)} generated art, {len(failed)} failed or tiny")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
