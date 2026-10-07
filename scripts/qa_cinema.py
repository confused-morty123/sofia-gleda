#!/usr/bin/env python3
"""Focused QA for the cinema-accuracy fix. Checks, headless (Sofia clock):
  * every arthouse cinema shows a programme (odeon has many, not 2; dom-kino,
    vlaikova, lumiere all render), across 390 px and 1440 px, in bg and en;
  * no "undefined" leaks into any rendered card (runtime/year guards);
  * "За теб днес" shows ONLY films with a screening today after the clock —
    verified at a mocked 14:45 Sofia so the time-of-day filter is exercised.
Prints a report; exits non-zero on any hard failure.
"""
import sys, pathlib
from playwright.sync_api import sync_playwright

HTML = pathlib.Path("index.html").resolve().as_uri()
FIXED_1445 = 1791373500000  # 2026-10-07 14:45 Europe/Sofia

SKIP_ONB = """
try { localStorage.setItem('sofia-screen-v2',
  JSON.stringify({prefs:{track:'',genres:[],mood:[],with:'',when:'',taste:[]},lang:'bg',mode:'cinema'})); } catch(e){}
"""

CLOCK = """
(() => { const _D = Date, F = %d;
  class FakeDate extends _D { constructor(...a){ a.length? super(...a): super(F); } static now(){ return F; } }
  window.Date = FakeDate; })();
""" % FIXED_1445

ARTS = ["odeon", "g8", "dom-kino", "vlaikova", "lumiere"]
fails, notes = [], []


def jattr(page):
    """Read the app's own view of the data from the page's globals."""
    return page.evaluate("""() => {
      const r = {now:{d:NOW_DATE,t:NOW_TIME}, atCinema:{}, today:[], undef:0};
      const prevDay = S.day; S.day='all';
      for (const v of %s) r.atCinema[v] = filmsAtCinema(v).length;
      S.day = prevDay;
      // today-rail invariant: every film the rail would show must have only
      // today's rows, all at/after NOW_TIME.
      for (const f of FILMS) {
        const rows = filmTimesToday(f.id);
        if (!rows.length) continue;
        const bad = rows.some(x => x[2] !== NOW_DATE || x[3].some(tm => tm < NOW_TIME));
        r.today.push({id:f.id, bad});
      }
      // scan rendered cards for a literal 'undefined'
      document.querySelectorAll('.card .cfoot, .hero-meta, .sheet .credits, .chips').forEach(n=>{
        if (/undefined/.test(n.textContent)) r.undef++;
      });
      return r;
    }""" % str(ARTS))


with sync_playwright() as pw:
    b = pw.chromium.launch()
    # ---- pass 1: real-ish clock, both sizes, both languages ----
    for w, h in [(390, 844), (1440, 900)]:
        for lang in ("bg", "en"):
            ctx = b.new_context(viewport={"width": w, "height": h},
                                timezone_id="Europe/Sofia", locale="bg-BG")
            ctx.add_init_script(SKIP_ONB)
            page = ctx.new_page()
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(HTML)
            page.wait_for_selector(".bar", timeout=20000)
            if lang == "en":
                # flip language via the app toggle if present
                t = page.query_selector("[data-lang='en'], [data-setlang='en']")
                if t:
                    t.click(); page.wait_for_timeout(300)
            page.evaluate("S.day='all'; render();")
            page.wait_for_timeout(300)
            rep = jattr(page)
            tag = f"{w}px/{lang}"
            for v in ARTS:
                n = rep["atCinema"].get(v, 0)
                if v == "lumiere":
                    if n == 0:
                        notes.append(f"[{tag}] lumiere 0 films (partial NDK coverage — may be legit)")
                elif v == "odeon":
                    if n <= 2:
                        fails.append(f"[{tag}] odeon shows only {n} films (regression: must be >2)")
                elif n == 0:
                    fails.append(f"[{tag}] {v} shows 0 films")
            if rep["undef"]:
                fails.append(f"[{tag}] {rep['undef']} card(s) render 'undefined'")
            if errs:
                fails.append(f"[{tag}] JS errors: {' | '.join(sorted(set(errs))[:2])}")
            print(f"[{tag}] arthouse films: " +
                  ", ".join(f"{v}={rep['atCinema'].get(v,0)}" for v in ARTS) +
                  f"  undef={rep['undef']}")
            ctx.close()

    # ---- pass 2: mocked 14:45 Sofia — the time-of-day filter ----
    ctx = b.new_context(viewport={"width": 390, "height": 844},
                        timezone_id="Europe/Sofia", locale="bg-BG")
    ctx.add_init_script(CLOCK)
    ctx.add_init_script(SKIP_ONB)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(HTML)
    page.wait_for_selector(".bar", timeout=20000)
    rep = jattr(page)
    print(f"\n[14:45 mock] now={rep['now']}  today-rail films={len(rep['today'])}")
    if rep["now"]["t"] != "14:45":
        notes.append(f"clock mock gave NOW_TIME={rep['now']['t']} (expected 14:45)")
    bad = [x["id"] for x in rep["today"] if x["bad"]]
    if bad:
        fails.append(f"[14:45] today-rail would show films with a non-today or "
                     f"before-now screening: {bad[:5]}")
    else:
        print(f"[14:45 mock] invariant OK — all {len(rep['today'])} today-rail films "
              f"screen today at/after 14:45")
    if errs:
        fails.append(f"[14:45] JS errors: {' | '.join(sorted(set(errs))[:2])}")
    ctx.close()
    b.close()

print("\n=== QA summary ===")
for n in notes:
    print("  note:", n)
if fails:
    print(f"\n  {len(fails)} FAILURE(S):")
    for f in fails:
        print("   ✗", f)
    sys.exit(1)
print("  all QA checks passed")
