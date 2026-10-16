#!/usr/bin/env python3
"""Sofia Gleda: security tests (findings and fixes: notes/qa/SECURITY_REPORT.md).

    python3 scripts/test_security.py                  # page: $SOFIA_HTML or index.html
    SOFIA_BROWSER=webkit python3 scripts/test_security.py   # one engine (default: both)

1. XSS fuzzing. index.sec.html (gitignored) is a copy of the page with hostile strings
   in every field type of every data constant: titles, synopses, credits, venue names
   and addresses, badges, notes, halls, prices, times, ratings, colours, URLs
   (javascript:, data:, vbscript:, quote break-outs), festival marks, aggregator labels,
   translations, auto events, very long and right-to-left strings. Every view is
   rendered (home with every rail, search, film/show/event sheets, the time popup, the
   calendar step, the drawer with its calendar; BG and EN; desktop and phone) in
   Chromium and WebKit, with the page's CSP and with the CSP removed, so the escaping
   is proven on its own. Nothing may run, no markup may appear, no link may carry a
   script or data: URL, no new-tab link may lack noopener noreferrer, and with the CSP
   no violation may fire.
2. The pipeline: "</script>" in a scraped title, a TMDB overview or a venue page's
   synopsis stays inside the data script (inject_films / inject_shows / inject_data /
   scrape_programs write through csp.json_for_script), and the page stays well formed.
3. Frames: inside another origin's frame the app does not render; same-origin is fine.
4. Share pages: the redirect runs under their own CSP.
5. Requests: the page contacts no font host and makes no http:// request.
Never prints the page or the data files. Run it alone (Playwright timing).
"""
import copy, http.server, json, os, pathlib, re, shutil, socket, subprocess, sys, tempfile, threading, datetime
from functools import partial

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import csp  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PAGE = ROOT / os.environ.get("SOFIA_HTML", "index.html")
FUZZ = ROOT / "index.sec.html"            # gitignored (index.*.html)
FRAME = ROOT / "index.frame.html"         # gitignored, only while the frame test runs
SERVED = ROOT / "index.http.html"         # gitignored: the page as served over plain http (below)
results = []


def check(name, ok, detail=""):
    results.append(("PASS" if ok else "FAIL", name, detail))
    print(f"{'PASS' if ok else 'FAIL'} {name} — {detail}"[:400], flush=True)


# ─────────────────────────────────────────────────────────────── hostile values
# Each payload calls __x(tag) if it ever runs and marks any element it creates with
# data-xss, so a run is caught by window.__xss and a break-out by [data-xss].
def text_payloads(tag):
    return [
        f'<img src=x onerror="__x(\'{tag}\')" data-xss="{tag}"> xssprobe',
        f'"><svg onload="__x(\'{tag}\')" data-xss="{tag}"> xssprobe',
        f"'><script data-xss=\"{tag}\">__x('{tag}')</script> xssprobe",
        f"</script><script data-xss=\"{tag}\">__x('{tag}')</script><!-- xssprobe",
        f'<a href="javascript:__x(\'{tag}\')" data-xss="{tag}">xssprobe</a>',
        f"${{__x('{tag}')}} {{{{constructor.constructor('__x(1)')()}}}} xssprobe",
        f'<style data-xss="{tag}">*{{background:red}}</style><iframe srcdoc="<script>parent.__x(\'{tag}\')</script>" data-xss="{tag}"></iframe>xssprobe',
        f"\u202eevil\u202c \u200b\u0301 <b data-xss=\"{tag}\">bold</b> xssprobe",
    ]


def url_payloads(tag):
    return [
        f"javascript:__x('{tag}')",
        f" JaVaScRiPt:__x('{tag}')",
        f"java\tscript:__x('{tag}')",
        f"data:text/html,<script>__x('{tag}')</script>",
        f"vbscript:msgbox('{tag}')",
        f'https://example.com/x"><img src=x onerror="__x(\'{tag}\')" data-xss="{tag}">',
        f"https://example.com/?q=<script>__x('{tag}')</script>&a='\"",
    ]


def colour_payloads(tag):
    return [f'#fff" onmouseover="__x(\'{tag}\')" data-xss="{tag}', f"red;background:url(javascript:__x('{tag}'))",
            f"#123</style><script>__x('{tag}')</script>"]


LONG = "Ш" * 2500 + " xssprobe"


class Fuzzer:
    """Rewrites the data constants of a page with hostile values, field by field."""

    def __init__(self):
        self.n = 0

    def text(self, v, tag):
        self.n += 1
        if self.n % 37 == 0:
            return f"{v} {LONG}"
        p = text_payloads(tag)
        return f"{v or ''} {p[self.n % len(p)]}"

    def url(self, tag):
        self.n += 1
        p = url_payloads(tag)
        return p[self.n % len(p)]

    def colour(self, tag):
        self.n += 1
        p = colour_payloads(tag)
        return p[self.n % len(p)]

    def record(self, o, tag, text=(), urls=(), lists=(), nums=(), odd=False):
        for k in text:
            if k in o or k in ("note", "noteEn"):
                o[k] = self.text(o.get(k) if isinstance(o.get(k), str) else "", f"{tag}.{k}")
        for k in urls:
            if o.get(k):
                o[k] = self.url(f"{tag}.{k}")
        for k in lists:
            if isinstance(o.get(k), list):
                o[k] = o[k] + [self.text("", f"{tag}.{k}")]
        if odd:                                   # numbers as strings, on every other record
            for k in nums:
                if k in o:
                    o[k] = f"<img src=x onerror=\"__x('{tag}.{k}')\" data-xss=\"{tag}.{k}\">"
        if isinstance(o.get("g"), list) and odd:
            o["g"] = [self.colour(f"{tag}.g0"), self.colour(f"{tag}.g1")]


def data_lines(html):
    """(start, end, name, prefix, value) of every one-line `const NAME=<json>;` in the
    two data scripts."""
    out = []
    for marker in ("/* SOFIA-DATA-START */", "/* SOFIA-POSTERS-START */"):
        s = html.index(marker)
        e = html.index("</script>", s)
        for m in re.finditer(r"^(const ([A-Z_][A-Z0-9_]*)\s*=\s*)(.*);[ \t]*$", html[s:e], re.M):
            try:
                out.append((s + m.start(), s + m.end(), m.group(2), m.group(1), json.loads(m.group(3))))
            except ValueError:
                pass
    return out


def fuzz_page(html, today):
    F = Fuzzer()
    consts = {name: (a, b, pre, val) for a, b, name, pre, val in data_lines(html)}
    D = {k: copy.deepcopy(v[3]) for k, v in consts.items()}
    for i, f in enumerate(D.get("FILMS", [])):
        F.record(f, f"F{i}", text=("bg", "en", "country", "director", "cast", "synBg", "synEn", "note", "noteEn"),
                 lists=("genres",), nums=("year", "runtime", "imdb", "rt", "mc", "imdbVotes"), odd=i % 2 == 1)
    for i, s in enumerate(D.get("SHOWS", [])):
        F.record(s, f"S{i}", text=("title", "titleEn", "author", "director", "cast", "synBg", "synEn", "badge"),
                 lists=("genres",), nums=("duration",), odd=i % 2 == 1)
        if i % 5 == 0:
            s["reviews"] = [{"text": F.text("Ревю", f"S{i}.rv"), "src": F.text("Вестник", f"S{i}.rs")}]
    for key in ("CINEMAS", "THEATRES"):
        for i, v in enumerate(D.get(key, [])):
            F.record(v, f"{key[0]}V{i}", text=("name", "nameEn", "area", "areaEn", "chain"))
            v["maps"] = F.url(f"{key[0]}V{i}.maps") if i % 2 else F.text("ул. Тест", f"{key[0]}V{i}.mapsq")
    for i, e in enumerate(D.get("EVENTS", [])):
        F.record(e, f"E{i}", text=("title", "titleEn", "blurbBg", "blurbEn", "source"), urls=("url",), odd=True)
    for i, (vid, b) in enumerate(sorted((D.get("BOOKING") or {}).items())):
        b["label"] = F.text(b.get("label", ""), f"B{i}.label")
        for k in ("url", "deep", "tix"):
            if b.get(k):
                b[k] = F.url(f"B{i}.{k}")
    for i, h in enumerate(D.get("TICKET_HUBS", [])):
        F.record(h, f"H{i}", text=("label", "note"), urls=("url",))
    for key in ("FILM_EXTRA", "SHOW_EXTRA"):
        for i, (k, x) in enumerate(sorted((D.get(key) or {}).items())):
            for f_ in list(x):
                if isinstance(x[f_], str):
                    x[f_] = F.text(x[f_], f"{key[0]}X{i}.{f_}")
                elif isinstance(x[f_], list):
                    x[f_] = x[f_] + [F.text("", f"{key[0]}X{i}.{f_}")]
    for key in ("TASTE_FILMS", "TASTE_SHOWS"):
        for i, t in enumerate(D.get(key, [])):
            F.record(t, f"T{key[6]}{i}", text=("title", "sub"))
    D["LINKS"] = [[r[0], F.url(f"L{i}")] for i, r in enumerate(D.get("LINKS", []))]
    D["VLINKS"] = [[r[0], r[1], F.url(f"VL{i}")] for i, r in enumerate(D.get("VLINKS", []))]
    if isinstance(D.get("AGGLINKS"), dict):
        D["AGGLINKS"] = {k: F.text(v, f"AG{i}") for i, (k, v) in enumerate(sorted(D["AGGLINKS"].items()))}
    if isinstance(D.get("SHOWATTR"), dict):
        for i, (k, m) in enumerate(sorted(D["SHOWATTR"].items())):
            for j, t in enumerate(sorted(m)):
                m[t] = m[t] + "," + text_payloads(f"SA{i}.{j}")[0]
    for r_i, r in enumerate(D.get("PERFORMANCES", [])):
        if len(r) > 4:
            r[3] = F.text(r[3] or "", f"P{r_i}.hall")
            r[4] = F.text(r[4] or "", f"P{r_i}.price")
        if r_i % 4 == 0 and isinstance(r[2], str):
            r[2] = r[2] + text_payloads(f"P{r_i}.time")[0]
    for r_i, r in enumerate(D.get("SHOWTIMES", [])):
        if r_i % 4 == 0 and r[3]:
            r[3] = [r[3][0] + text_payloads(f"ST{r_i}.time")[0]] + r[3][1:]
    fm = D.get("FESTMARK") or {}
    for i, (k, v) in enumerate(sorted(fm.items())):
        v["bg"], v["en"] = F.text(v.get("bg", ""), f"FM{i}.bg"), F.text(v.get("en", ""), f"FM{i}.en")
    film_ids = [f["id"] for f in D.get("FILMS", [])]
    show_ids = [s["id"] for s in D.get("SHOWS", [])]
    fm["xssfest"] = {"bg": F.text("Фест", "FMX.bg"), "en": F.text("Fest", "FMX.en"),
                     "films": film_ids[:8], "shows": show_ids[:8]}
    D["FESTMARK"] = fm
    if "POSTERS" in D:
        for i, fid in enumerate(film_ids[:12]):
            D["POSTERS"][fid] = F.url(f"PO{i}")
    for i, (k, v) in enumerate(sorted((D.get("TMDBART") or {}).items())):
        F.record(v, f"TA{i}", text=("en", "ov", "ovBg", "country", "imdbVotes"), nums=("imdb", "tv", "rt", "mc"),
                 odd=i % 2 == 1)
        if i % 3 == 0:
            v["p"] = f'/x.jpg" onerror="__x(\'TA{i}.p\')" data-xss="TA{i}.p'
    for i, k in enumerate(sorted(D.get("SHOWART") or {})):
        if i % 2 == 0:
            D["SHOWART"][k] = F.url(f"SH{i}") if i % 4 == 0 else f'posters/x.jpg"><img src=x onerror="__x(\'SH{i}\')" data-xss="SH{i}">'
    for i, (k, v) in enumerate(sorted((D.get("FILMINFO") or {}).items())):
        for f_ in list(v):
            if isinstance(v[f_], str):
                v[f_] = F.url(f"FI{i}.{f_}") if f_ == "img" else F.text(v[f_], f"FI{i}.{f_}")
            elif isinstance(v[f_], list):
                v[f_] = v[f_] + [F.text("", f"FI{i}.{f_}")]
    for key in ("SYN_EN", "TITLE_EN"):
        if isinstance(D.get(key), dict):
            D[key] = {k: F.text(v, f"{key[0]}E{i}") for i, (k, v) in enumerate(sorted(D[key].items()))}
    d0 = datetime.date.fromisoformat(today)
    iso = lambda n: (d0 + datetime.timedelta(days=n)).isoformat()
    auto = []
    for i in range(4):
        auto.append({"id": f"xss-event-{i}", "kind": "festival", "scope": "both", "status": "active",
                     "tag": F.text("ФЕСТИВАЛ", f"EA{i}.tag"), "title": F.text("Фестивал", f"EA{i}.title"),
                     "titleEn": F.text("Festival", f"EA{i}.titleEn"), "blurbBg": F.text("Описание", f"EA{i}.bbg"),
                     "blurbEn": F.text("About", f"EA{i}.ben"), "source": F.text("programata.bg", f"EA{i}.src"),
                     "auto": True, "url": url_payloads(f"EA{i}.url")[i] if i < 3 else url_payloads(f"EA{i}.url")[5],
                     "from": iso(-1), "to": iso(4 + i), "g": [colour_payloads(f"EA{i}.g")[i % 3], "#000000"]})
    D["EVENTS_AUTO"] = auto
    D["EVENT_MEMBERS"] = {"xss-event-0": film_ids[:3] + show_ids[:3]}
    if isinstance(D.get("SNAPSHOT"), dict):
        D["SNAPSHOT"]["lastValidated"] = (D["SNAPSHOT"].get("lastValidated") or today) + text_payloads("SN")[0]
    # write back, last line first so the offsets stay valid
    for name, (a, b, pre, _) in sorted(consts.items(), key=lambda kv: -kv[1][0]):
        html = html[:a] + pre + csp.json_for_script(D[name]) + ";" + html[b:]
    for name in ("EVENTS_AUTO", "EVENT_MEMBERS"):          # declared in the build? (inject_data always writes them)
        if name not in consts:
            raise SystemExit(f"{name} is not declared in {PAGE.name}: rebuild it first")
    return csp.stamp(html), F.n


# ─────────────────────────────────────────────────────────────── page checks
AUDIT_JS = r"""() => {
  const o = {xss: (window.__xss||[]).slice(0,6), markup: [], handlers: [], links: [], blank: [], imgs: [],
             styles: [], calurls: [], scripts: document.scripts.length};
  document.querySelectorAll('[data-xss]').forEach(e => o.markup.push(e.tagName + ' ' + (e.getAttribute('data-xss')||'')));
  for (const el of document.querySelectorAll('*')) {
    for (const a of el.attributes) if (/^on/i.test(a.name)) o.handlers.push(el.tagName + '[' + a.name + ']');
    const st = el.getAttribute('style');
    if (st && /javascript:|expression\(|data:text|<|>/i.test(st)) o.styles.push(el.tagName + ' ' + st.slice(0, 80));
  }
  for (const a of document.querySelectorAll('a[href]')) {
    if (!['http:', 'https:', 'blob:', 'file:'].includes(a.protocol)) o.links.push(a.getAttribute('href').slice(0, 80));
    if (a.target === '_blank') {
      const r = (a.getAttribute('rel') || '').split(/\s+/);
      if (!(r.includes('noopener') && r.includes('noreferrer'))) o.blank.push(a.href.slice(0, 80));
    }
  }
  for (const im of document.querySelectorAll('img')) {
    const s = im.getAttribute('src') || '';
    if (/^\s*(javascript|vbscript|data:(?!image\/))/i.test(s)) o.imgs.push(s.slice(0, 80));
  }
  for (const el of document.querySelectorAll('[data-calurl]')) {
    const u = el.getAttribute('data-calurl'); if (u && !/^https:\/\//.test(u)) o.calurls.push(u.slice(0, 80));
  }
  return o;
}"""

INIT_JS = """window.__xss=[];window.__x=function(t){window.__xss.push(String(t));};
window.__cspv=[];document.addEventListener('securitypolicyviolation',function(e){
  window.__cspv.push(e.violatedDirective+' '+(e.blockedURI||'')+' '+(e.sample||''));},true);"""


class Run:
    def __init__(self, engine, variant, nscripts):
        self.engine, self.variant, self.nscripts = engine, variant, nscripts
        self.problems, self.views, self.errors = [], 0, []

    def audit(self, page, view):
        self.views += 1
        try:
            o = page.evaluate(AUDIT_JS)
            v = page.evaluate("window.__cspv||[]")
        except Exception as e:                                   # a navigation away is itself a finding
            self.problems.append(f"{view}: page could not be audited ({type(e).__name__})")
            return
        for k in ("xss", "markup", "handlers", "links", "blank", "imgs", "styles", "calurls"):
            if o[k]:
                self.problems.append(f"{view}: {k} {o[k][:3]}")
        if o["scripts"] != self.nscripts:
            self.problems.append(f"{view}: {o['scripts']} <script> elements, expected {self.nscripts}")
        if self.variant == "csp" and v:
            self.problems.append(f"{view}: {len(v)} CSP violation(s) {v[:2]}")


def fixed_clock(today):
    y, m, d = map(int, today.split("-"))
    ms = int(datetime.datetime(y, m, d, 11, 45, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    return ("(()=>{const _D=Date,F=%d;class FakeDate extends _D{constructor(...a){a.length?super(...a):super(F);}"
            "static now(){return F;}}window.Date=FakeDate;})();" % ms)


def new_page(browser, today, lang, mode, phone=False):
    kw = dict(viewport={"width": 390, "height": 844} if phone else {"width": 1280, "height": 900},
              timezone_id="Europe/Sofia", locale="bg-BG")
    if phone:
        kw.update(has_touch=True, is_mobile=True)
    ctx = browser.new_context(**kw)
    ctx.add_init_script(fixed_clock(today))
    ctx.add_init_script(INIT_JS)
    ctx.add_init_script("try{localStorage.setItem('sofia-screen-v2',JSON.stringify({prefs:{track:'both',genres:[],"
                        "mood:[],with:'',when:'any',taste:[]},lang:'%s',mode:'%s'}));}catch(e){}" % (lang, mode))
    ctx.route("http://**/*", lambda r: r.abort())             # offline: posters and outbound links
    ctx.route("https://**/*", lambda r: r.abort())
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)[:160]))
    return ctx, pg, errs


def mount_all_rails(pg):
    for _ in range(60):
        if not pg.evaluate("!!document.getElementById('rail-sentinel')"):
            break
        pg.mouse.wheel(0, 2400)
        pg.wait_for_timeout(60)
    pg.evaluate("window.scrollTo(0,0)")


def open_sheet(pg, kind, id_):
    pg.evaluate("([k,i]) => _openById(k,i)", [kind, id_])
    pg.wait_for_selector(".sheet", timeout=4000)
    pg.wait_for_timeout(450)


def close_all(pg):
    for _ in range(3):
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(420)


def popup_and_cal(R, pg, label):
    """Open the time popup from the open sheet's first visible pill, then its calendar step."""
    pill = pg.locator(".sheet [data-sspill]:visible").first
    if not pill.count():
        return
    pill.click()
    pg.wait_for_timeout(500)
    R.audit(pg, f"{label} time popup")
    cal = pg.locator("[data-sscal]:visible").first
    if cal.count():
        cal.click()
        pg.wait_for_timeout(500)
        R.audit(pg, f"{label} calendar step")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(450)


def step(R, label, fn):
    """One UI action; a failure to perform it is recorded, not fatal."""
    try:
        fn()
        return True
    except Exception as e:
        R.problems.append(f"{label}: could not run ({type(e).__name__}: {str(e)[:90]})")
        return False


def views(R, browser, page_uri, today, ids):
    for lang in ("bg", "en"):
        for mode in ("cinema", "theatre"):
            ctx, pg, errs = new_page(browser, today, lang, mode)
            pg.goto(page_uri)
            pg.wait_for_selector(".bar", timeout=20000)
            mount_all_rails(pg)
            R.audit(pg, f"{lang}/{mode} home + all rails")
            if lang == "bg":
                def drawer():
                    pg.click("[data-drawer]")
                    pg.wait_for_selector(".drawer", timeout=4000)
                    pg.wait_for_timeout(450)
                    R.audit(pg, f"{lang}/{mode} drawer + calendar")
                    pg.keyboard.press("Escape")
                    pg.wait_for_timeout(450)
                step(R, f"{lang}/{mode} drawer", drawer)

                def search():
                    pg.fill("#q", "xssprobe")
                    pg.wait_for_timeout(700)
                    R.audit(pg, f"{lang}/{mode} search")
                    pg.fill("#q", "")
                    pg.wait_for_timeout(500)
                step(R, f"{lang}/{mode} search", search)
            kind = "film" if mode == "cinema" else "show"
            for i, id_ in enumerate(ids[kind] if lang == "bg" else ids[kind][:4]):
                if step(R, f"{lang} {kind} sheet {id_}", lambda: open_sheet(pg, kind, id_)):
                    R.audit(pg, f"{lang} {kind} sheet {id_}")
                    if i < 3:
                        step(R, f"{lang} {kind} {id_} popup", lambda: popup_and_cal(R, pg, f"{lang} {kind} {id_}"))
                close_all(pg)
            if mode == "theatre":
                for id_ in ids["event"]:
                    if step(R, f"{lang} event sheet {id_}", lambda: open_sheet(pg, "event", id_)):
                        R.audit(pg, f"{lang} event sheet {id_}")
                    close_all(pg)
            R.errors += errs
            ctx.close()
    # phone: home, a sheet, its popup (a bottom sheet there) and the calendar step
    for mode, kind in (("cinema", "film"), ("theatre", "show")):
        ctx, pg, errs = new_page(browser, today, "bg", mode, phone=True)
        pg.goto(page_uri)
        pg.wait_for_selector(".bar", timeout=20000)
        R.audit(pg, f"phone {mode} home")
        if step(R, f"phone {kind} sheet", lambda: open_sheet(pg, kind, ids[kind][0])):
            R.audit(pg, f"phone {kind} sheet")
            step(R, f"phone {kind} popup", lambda: popup_and_cal(R, pg, f"phone {kind}"))
        R.errors += errs
        ctx.close()


def pick_ids(browser, page_uri, today):
    ctx, pg, _ = new_page(browser, today, "bg", "cinema")
    pg.goto(page_uri)
    pg.wait_for_selector(".bar", timeout=20000)
    ids = pg.evaluate("""() => {
      const f = [...new Set(SHOWTIMES.filter(r => r[2] >= NOW_DATE).map(r => r[0]))].filter(id => filmById[id]);
      const s = [...new Set(PERFORMANCES.filter(p => p[1] >= NOW_DATE).map(p => p[0]))].filter(id => showById[id]);
      return {film: f.slice(0, 14), show: s.slice(0, 14), event: EVENTS.map(e => e.id)};
    }""")
    ctx.close()
    return ids


# ─────────────────────────────────────────────────────────────── the pipeline
BREAK = "</script><script>__x('{t}')</script><!--<script>"


def pipeline_test():
    """Hostile sidecar files through inject_films / inject_shows / inject_data in a
    throwaway copy of the repo: the page must stay well formed, the data intact."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sg_sec_pipe_"))
    try:
        shutil.copytree(ROOT / "scripts", tmp / "scripts", ignore=shutil.ignore_patterns("__pycache__", "fixtures"))
        for f in ("tmdb_films.json", "theatre_posters.json", "film_links_posters.json", "film_info.json",
                  "translations.json", "events.json", "cinema_films.json", "theatre_shows.json"):
            if (ROOT / f).exists():
                shutil.copy(ROOT / f, tmp / f)
        shutil.copy(PAGE, tmp / "index.html")
        cf = json.loads((tmp / "cinema_films.json").read_text(encoding="utf-8"))
        cf["xss-film"] = {"id": "xss-film", "bg": "Филм " + BREAK.format(t="pipe-film"), "en": "", "genres": [],
                          "g": ["#332A1B", "#5F4527"], "source": "odeon"}
        (tmp / "cinema_films.json").write_text(json.dumps(cf, ensure_ascii=False), encoding="utf-8")
        ts = json.loads((tmp / "theatre_shows.json").read_text(encoding="utf-8"))
        ts["xss-show"] = {"id": "xss-show", "title": "Шоу " + BREAK.format(t="pipe-show"), "titleEn": "",
                          "theatre": "sfumato", "author": "", "director": "", "cast": "", "genres": [],
                          "g": ["#331B1B", "#5F2727"], "duration": None, "synBg": BREAK.format(t="pipe-syn"),
                          "synEn": "", "source": "sfumato"}
        (tmp / "theatre_shows.json").write_text(json.dumps(ts, ensure_ascii=False), encoding="utf-8")
        tm = json.loads((tmp / "tmdb_films.json").read_text(encoding="utf-8"))
        hit = next(k for k, v in tm.items() if v.get("matched"))
        tm[hit]["ov"] = "A film. " + BREAK.format(t="pipe-ov")       # TMDB overviews are community-edited
        tm[hit]["en"] = "Title " + BREAK.format(t="pipe-en")
        (tmp / "tmdb_films.json").write_text(json.dumps(tm, ensure_ascii=False), encoding="utf-8")
        fi = json.loads((tmp / "film_info.json").read_text(encoding="utf-8"))
        fk = next(iter(fi))
        fi[fk]["synBg"] = BREAK.format(t="pipe-fi")
        (tmp / "film_info.json").write_text(json.dumps(fi, ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ, SOFIA_HTML=str(tmp / "index.html"))
        for step in ("inject_films.py", "inject_shows.py", "inject_data.py"):
            r = subprocess.run([sys.executable, str(tmp / "scripts" / step)], cwd=tmp, env=env,
                               capture_output=True, text=True)
            check(f"pipeline_{step}_runs", r.returncode == 0, (r.stdout + r.stderr).strip().splitlines()[-1][:160]
                  if (r.stdout + r.stderr).strip() else "")
        page = (tmp / "index.html").read_text(encoding="utf-8")
        check("pipeline_page_well_formed", csp.check(page, "page") == [], "; ".join(csp.check(page, "page"))[:200])
        check("pipeline_no_raw_breakout_in_page", "</script><script>__x(" not in page, "")
        return tmp, hit, fk
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def writer_tests():
    """scrape_programs' page writers keep a value inside the data script."""
    import scrape_programs as SP
    hostile = BREAK.format(t="sp")
    lit = SP.emit_rows([["id", hostile]])
    check("scrape_programs_emit_rows_escapes", "<" not in lit and json.loads(lit) == [["id", hostile]], lit[:60])
    page = "const PRELIM_ROWS=[];\nconst AGGLINKS={};\nconst SHOWATTR={};\nconst VLINKS=[];\n"
    out = SP.write_agglinks(page, {"x": hostile})
    check("scrape_programs_write_agglinks_escapes", "</script" not in out and "\\u003c/script" in out, "")
    out = SP.write_festmark(page, {"f": {"bg": hostile, "en": hostile, "films": [], "shows": []}})
    check("scrape_programs_write_festmark_escapes", "</script" not in out, "")


# ─────────────────────────────────────────────────────────────── frames, share pages
class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class _Server(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass                                   # a browser closing a connection early is no news


def serve(directory):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    httpd = _Server(("127.0.0.1", port), partial(_Quiet, directory=str(directory)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def frame_tests(browser, engine, today):
    """The app inside another origin's frame shows only an "open in its own tab" link;
    in a frame of its own origin it renders. Two ports of 127.0.0.1 = two origins.
    Served over plain http, so the copy drops upgrade-insecure-requests: WebKit would
    otherwise upgrade even same-origin http://127.0.0.1 requests to https (the live
    site is https-only, where the directive changes nothing)."""
    other = pathlib.Path(tempfile.mkdtemp(prefix="sg_sec_frame_"))
    SERVED.write_text(PAGE.read_text(encoding="utf-8").replace("; upgrade-insecure-requests", "", 1), encoding="utf-8")
    FRAME.write_text(f'<!doctype html><meta charset="utf-8"><iframe id="f" src="{SERVED.name}" '
                     'style="width:900px;height:700px"></iframe>', encoding="utf-8")
    h1, p1 = serve(ROOT)
    (other / "frame.html").write_text(f'<!doctype html><meta charset="utf-8"><iframe id="f" '
                                      f'src="http://127.0.0.1:{p1}/{SERVED.name}" style="width:900px;height:700px">'
                                      f'</iframe>', encoding="utf-8")
    h2, p2 = serve(other)
    try:
        for label, url, foreign in ((f"foreign", f"http://127.0.0.1:{p2}/frame.html", True),
                                    (f"same-origin", f"http://127.0.0.1:{p1}/{FRAME.name}", False)):
            ctx = browser.new_context(viewport={"width": 1000, "height": 800})
            ctx.add_init_script(fixed_clock(today))
            ctx.add_init_script("try{localStorage.setItem('sofia-screen-v2',JSON.stringify({prefs:{track:'both',"
                                "genres:[],mood:[],with:'',when:'any',taste:[]},lang:'bg',mode:'cinema'}));}catch(e){}")
            ctx.route("https://**/*", lambda r: r.abort())
            pg = ctx.new_page()
            pg.goto(url)
            fr = pg.frame_locator("#f")
            if foreign:
                try:
                    fr.locator("[data-framed]").wait_for(timeout=15000)
                    shown = True
                except Exception:
                    shown = False
                bars = fr.locator(".bar").count()
                href = fr.locator("[data-framed]").get_attribute("href") if shown else ""
                tgt = fr.locator("[data-framed]").get_attribute("target") if shown else ""
                check(f"frame_{engine}_foreign_frame_not_rendered", shown and bars == 0 and tgt == "_top"
                      and href.startswith(f"http://127.0.0.1:{p1}/"), f"link={shown} bars={bars} target={tgt}")
            else:
                try:
                    fr.locator(".bar").wait_for(timeout=20000)
                    ok = True
                except Exception:
                    ok = False
                check(f"frame_{engine}_same_origin_frame_renders", ok and fr.locator("[data-framed]").count() == 0, "")
            ctx.close()
        # top level over http: renders, and every request stays on this origin
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx.add_init_script(fixed_clock(today))
        reqs = []
        ctx.on("request", lambda r: reqs.append(r.url))
        ctx.route("https://**/*", lambda r: r.abort())
        pg = ctx.new_page()
        pg.goto(f"http://127.0.0.1:{p1}/{SERVED.name}")
        pg.wait_for_selector(".bar", timeout=20000)
        pg.wait_for_timeout(800)
        fonts = [u for u in reqs if "fonts.g" in u]
        insecure = [u for u in reqs if u.startswith("http://") and not u.startswith(f"http://127.0.0.1:{p1}/")]
        loaded = pg.evaluate("[...document.fonts].filter(f=>f.status==='loaded').length")
        check(f"requests_{engine}_no_font_host", not fonts and loaded > 0, f"{len(fonts)} font-host requests, "
              f"{loaded} faces loaded from fonts/")
        check(f"requests_{engine}_no_insecure_request", not insecure, "; ".join(insecure[:3]))
        ctx.close()
    finally:
        h1.shutdown()
        h2.shutdown()
        FRAME.unlink(missing_ok=True)
        SERVED.unlink(missing_ok=True)
        shutil.rmtree(other, ignore_errors=True)


def sw_test(browser, today):
    """The service worker keeps ONE copy of the page however many ?film= links are
    opened (it used to keep a ~1.5 MB copy per link, for good), and caches nothing
    from a font host (there is none)."""
    SERVED.write_text(PAGE.read_text(encoding="utf-8").replace("; upgrade-insecure-requests", "", 1), encoding="utf-8")
    h, port = serve(ROOT)
    base = f"http://127.0.0.1:{port}/{SERVED.name}"
    try:
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx.add_init_script(fixed_clock(today))
        ctx.route("https://**/*", lambda r: r.abort())
        pg = ctx.new_page()
        pg.goto(base)
        pg.wait_for_selector(".bar", timeout=20000)
        pg.evaluate("navigator.serviceWorker.ready.then(()=>true)")
        for q in ("?film=a", "?film=b", "?show=c", "?event=d", ""):
            pg.goto(base + q)
            pg.wait_for_selector(".bar", timeout=20000)
        keys = pg.evaluate("""async () => { const out = {};
            for (const n of await caches.keys()) { const c = await caches.open(n);
              out[n] = (await c.keys()).map(r => r.url.replace(location.origin, '')); }
            return {controlled: !!navigator.serviceWorker.controller, caches: out}; }""")
        shell = next((v for k, v in keys["caches"].items() if k.endswith("-shell")), [])
        pages = [u for u in shell if SERVED.name in u]
        check("sw_one_cached_copy_per_page", keys["controlled"] and pages == [f"/{SERVED.name}"],
              f"controlled={keys['controlled']} cached page entries={pages} caches={list(keys['caches'])}")
        ctx.close()
    finally:
        h.shutdown()
        SERVED.unlink(missing_ok=True)


def share_page_test(browser, engine):
    """A share page's redirect script runs under its CSP (it is the hash allowed)."""
    pages = list(csp.share_pages(ROOT))
    if not pages:
        check(f"share_{engine}_redirect", False, "no share pages: run gen_share_pages.py")
        return
    sp = pages[0]
    ctx = browser.new_context()
    ctx.add_init_script(INIT_JS)
    hit = []

    def stub(route):
        hit.append(route.request.url)
        route.fulfill(status=200, content_type="text/html", body="<!doctype html><title>app</title>ok")
    ctx.route("https://**/*", stub)
    pg = ctx.new_page()
    v = []
    pg.on("console", lambda m: v.append(m.text) if "Content Security Policy" in m.text else None)
    pg.goto(sp.as_uri())
    pg.wait_for_timeout(1200)
    want = f"?{sp.parent.parent.name}="
    check(f"share_{engine}_redirect_runs_under_csp", any(want in u for u in hit) and not v,
          f"{sp.parent.parent.name}/{sp.parent.name} -> {hit[:1]} violations={v[:1]}")
    ctx.close()


# ─────────────────────────────────────────────────────────────── main
def main():
    src = PAGE.read_text(encoding="utf-8")
    m = re.search(r'"window"\s*:\s*\{[^}]*"from"\s*:\s*"(\d{4}-\d{2}-\d{2})"', src)
    today = m.group(1)
    print(f"test_security: {PAGE.name} (clock {today} 14:45 Sofia)")
    # 0. the real page
    check("page_csp_sound", csp.check(src, "page") == [], "; ".join(csp.check(src, "page"))[:200])
    http_imgs = [u for u in re.findall(r'"(http://[^"]+)"', src) if re.search(r"\.(jpe?g|png|webp|gif|avif)\b", u, re.I)]
    check("page_no_http_image_urls", not http_imgs, f"{len(http_imgs)}: {http_imgs[:2]}")
    # 1. fuzz copy
    html, n = fuzz_page(src, today)
    FUZZ.write_text(html, encoding="utf-8")
    check("fuzz_page_stays_well_formed", csp.check(html, "page") == [], f"{n} hostile values written")
    nocsp = ROOT / "index.secnocsp.html"
    nocsp.write_text(csp.META_RE.sub("", html), encoding="utf-8")
    nscripts = len(csp.inline_scripts(html))
    engines = [os.environ["SOFIA_BROWSER"]] if os.environ.get("SOFIA_BROWSER") else ["chromium", "webkit"]
    try:
        writer_tests()
        tmp, film_hit, fi_hit = pipeline_test()
        with sync_playwright() as pw:
            for engine in engines:
                browser = getattr(pw, engine).launch()
                ids = pick_ids(browser, FUZZ.as_uri(), today)
                for variant, path in (("csp", FUZZ), ("nocsp", nocsp)):
                    R = Run(engine, variant, nscripts)
                    views(R, browser, path.as_uri(), today, ids)
                    check(f"fuzz_{engine}_{variant}_nothing_runs_nothing_breaks_out", not R.problems,
                          f"{R.views} views; " + (" | ".join(R.problems[:4]) if R.problems else "clean"))
                    errs = sorted(set(R.errors))
                    print(f"  note: {engine}/{variant}: {len(R.errors)} page error(s) with hostile data"
                          + (f": {errs[:3]}" if errs else ""))
                # the pipeline copy in the browser: no run, the hostile text arrives as text
                ctx, pg, errs = new_page(browser, today, "en", "cinema")
                pg.goto((tmp / "index.html").as_uri())
                pg.wait_for_selector(".bar", timeout=20000)
                got = pg.evaluate("""([f]) => ({x: window.__xss, film: (FILMS.find(x => x.id === 'xss-film') || {}).bg || '',
                    show: (SHOWS.find(x => x.id === 'xss-show') || {}).title || '', ov: (TMDBART[f] || {}).ov || ''})""",
                                  [film_hit])
                open_sheet(pg, "film", "xss-film")
                syn = pg.evaluate("(document.querySelector('.sheet h2') || {}).textContent || ''")
                check(f"pipeline_{engine}_hostile_values_stay_text",
                      not got["x"] and "</script>" in got["film"] and "</script>" in got["show"]
                      and "</script>" in got["ov"] and "</script>" in syn and not pg.evaluate("window.__xss.length"),
                      f"ran={got['x']} film/show/ov intact={'</script>' in got['film']}/{'</script>' in got['show']}/"
                      f"{'</script>' in got['ov']} shown as text={'</script>' in syn}")
                ctx.close()
                frame_tests(browser, engine, today)
                share_page_test(browser, engine)
                if engine == "chromium":
                    sw_test(browser, today)
                browser.close()
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        nocsp.unlink(missing_ok=True)
    failed = [r for r in results if r[0] == "FAIL"]
    print(f"\nTotal: {len(results) - len(failed)}/{len(results)} passed")
    for _, name, detail in failed:
        print(f"  FAIL {name} — {detail[:300]}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
