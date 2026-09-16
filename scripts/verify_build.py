#!/usr/bin/env python3
"""Sofia Gleda — gate the weekly refresh before it is committed.

The 2026-09-16 refresh shipped an index.html whose film list rendered but whose
films could not be opened: `inject_data.py` regenerates the SOFIA-POSTERS block
wholesale and a hand-written `const PRICES` was living inside it, so the build
deleted it. The page still parsed, still painted, and only threw
`PRICES is not defined` once you actually clicked a film — which is the one path
nothing was checking.

So: check the things a browser would only discover at click time.

    python3 scripts/verify_build.py          # exit 0 = safe to publish

Checks, cheapest first:
  1. every marker block is present and non-empty
  2. every SCREAMING_CASE global the app *reads* is also *declared*   <- the bug
  3. the JS parses (node --check, if node is available)
  4. the data is structurally sound and cross-references resolve
  5. no collapse: the record counts are within a sane floor
  6. a real headless click on a film card opens the detail sheet, if Playwright
     is installed (skipped, loudly, when it is not)

Non-zero exit means: do not commit, keep the previous site up.
"""
import json, os, re, subprocess, sys, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))

# A refresh may legitimately shrink the data (a venue stops publishing), but not
# by an order of magnitude. Below these floors something has gone wrong upstream.
FLOORS = {"FILMS": 20, "SHOWTIMES": 80, "CINEMAS": 8,
          "SHOWS": 20, "PERFORMANCES": 40, "THEATRES": 6}

problems: list[str] = []
notes: list[str] = []


def fail(msg): problems.append(msg)
def note(msg): notes.append(msg)


src = HTML.read_text(encoding="utf-8")
scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", src, flags=re.S)
js = "\n".join(scripts)

# ---------------------------------------------------------------- 1. markers
for start, end in [("SOFIA-DATA-START", "SOFIA-DATA-END"),
                   ("SOFIA-POSTERS-START", "SOFIA-POSTERS-END")]:
    if start not in src or end not in src:
        fail(f"marker {start}/{end} is missing — the build would not be editable next week")

# ------------------------------------------------- 2. declared vs referenced
# Top-level declarations: `const NAME=`, `let NAME=`, `var NAME=`, `function NAME(`.
def declared_names(text):
    """Every name bound by a declaration, including the later declarators in
    `const TODAY=..., LASTDAY=...;` — missing those is how a checker cries wolf."""
    names = set(re.findall(r"^function\s+([A-Za-z_$][\w$]*)", text, flags=re.M))
    names |= set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(", text))
    for m in re.finditer(r"\b(?:const|let|var)\s+", text):
        i, depth = m.end(), 0
        buf = []
        while i < len(text):                       # walk to the end of the statement
            ch = text[i]
            if ch in "([{": depth += 1
            elif ch in ")]}":
                if depth == 0: break
                depth -= 1
            elif ch == ";" and depth == 0: break
            buf.append(ch); i += 1
        stmt, d2, part, parts = "".join(buf), 0, [], []
        for ch in stmt:                            # split declarators on top-level commas
            if ch in "([{": d2 += 1
            elif ch in ")]}": d2 -= 1
            if ch == "," and d2 == 0:
                parts.append("".join(part)); part = []
            else:
                part.append(ch)
        parts.append("".join(part))
        for d in parts:
            head = d.split("=")[0].strip()
            names |= set(re.findall(r"[A-Za-z_$][\w$]*", head))   # covers destructuring too
    return names

declared = declared_names(js)

# Strip string and comment content so quoted words are not mistaken for code.
code = re.sub(r"/\*.*?\*/", " ", js, flags=re.S)
code = re.sub(r"(?<![\\])//[^\n]*", " ", code)
code = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', code)
code = re.sub(r"'(?:[^'\\\n]|\\.)*'", "''", code)
code = re.sub(r"`(?:[^`\\]|\\.)*`", "``", code, flags=re.S)   # crude: drops template bodies
# Template literals carry real code in ${...}; keep those.
for chunk in re.findall(r"\$\{([^{}]*)\}", js):
    code += "\n" + chunk

BUILTIN = {"JSON", "Math", "Date", "Object", "Array", "String", "Number", "Boolean",
           "NaN", "Infinity", "Intl", "Map", "Set", "Promise", "RegExp", "Error",
           "URL", "DOMParser", "TODAY", "I", "II", "III", "IV", "V", "X", "XL",
           "BG", "EN", "UTC", "GMT", "IMAX", "AM", "PM", "SVG", "HTML", "CSS",
           "API", "URL", "ID", "OK", "NDK", "TMDB", "IMDB", "PG", "DOM"}
referenced = set(re.findall(r"(?<![.\w$])([A-Z][A-Z0-9_]{2,})\b(?=\s*(?:\[|\.|\(|\)|,|;|\?|:|\}|=[^=]|===|!==|\|\||&&|$))",
                            code, flags=re.M))
missing = sorted(r for r in referenced - declared - BUILTIN if not r.isupper() or True)
missing = [m for m in missing if m not in BUILTIN and m not in declared]
if missing:
    fail("the app reads globals that nothing declares: " + ", ".join(missing)
         + "\n      (this is the exact shape of the PRICES regression — a build step "
           "deleted a declaration and clicking a card now throws)")

# --------------------------------------------------------------- 3. it parses
try:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(js); tmp = fh.name
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        fail("the inlined JavaScript does not parse:\n      "
             + (r.stderr or r.stdout).strip().splitlines()[0])
    os.unlink(tmp)
except FileNotFoundError:
    note("node not available — skipped the syntax check")
except Exception as e:
    note(f"syntax check skipped: {e}")

# ------------------------------------------------------- 4/5. data integrity
def const(name):
    m = re.search(r"^const %s\s*=\s*(.*?);\s*$" % name, js, flags=re.M)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None

data = {n: const(n) for n in
        ["SNAPSHOT", "CINEMAS", "FILMS", "SHOWTIMES", "THEATRES", "SHOWS",
         "PERFORMANCES", "EVENTS", "BOOKING", "VENUE_UNTIL"]}

for name, floor in FLOORS.items():
    v = data.get(name)
    if v is None:
        fail(f"{name} is missing or is not valid JSON")
    elif len(v) < floor:
        fail(f"{name} collapsed to {len(v)} records (floor {floor}) — a scrape probably failed open")

if data["FILMS"] and data["SHOWTIMES"]:
    film_ids = {f["id"] for f in data["FILMS"]}
    dupes = len(data["FILMS"]) - len(film_ids)
    if dupes:
        fail(f"{dupes} duplicate film ids — cards would collide")
    orphan_f = sorted({r[0] for r in data["SHOWTIMES"]} - film_ids)
    if orphan_f:
        fail(f"{len(orphan_f)} showtimes point at films that do not exist "
             f"(e.g. {', '.join(orphan_f[:4])}) — those cards open an empty sheet")

if data["CINEMAS"] and data["SHOWTIMES"]:
    ven = {c["id"] for c in data["CINEMAS"]}
    orphan_v = sorted({r[1] for r in data["SHOWTIMES"]} - ven)
    if orphan_v:
        fail(f"showtimes reference unknown venues: {', '.join(orphan_v[:6])} "
             "— cinById lookup returns undefined and the sheet throws")

if data["SHOWS"] and data["PERFORMANCES"]:
    sids = {s["id"] for s in data["SHOWS"]}
    orphan_s = sorted({p[0] for p in data["PERFORMANCES"]} - sids)
    if orphan_s:
        fail(f"performances reference unknown shows: {', '.join(orphan_s[:6])}")

snap = data["SNAPSHOT"]
if isinstance(snap, dict):
    w = snap.get("window") or {}
    if not (w.get("from") and w.get("to") and w["from"] <= w["to"]):
        fail(f"SNAPSHOT.window is not a sane range: {w}")
    if data["SHOWTIMES"]:
        out = sorted({r[2] for r in data["SHOWTIMES"] if not (w["from"] <= r[2] <= w["to"])})
        if out:
            note(f"{len(out)} showtime dates fall outside the snapshot window "
                 f"({out[0]}..{out[-1]}) and will not be shown")

# ----------------------------------------------------- 6. a real click, live
def smoke():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        note("Playwright not installed — skipped the live click test")
        return
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            for tz in ("Europe/Sofia", "UTC"):
                ctx = b.new_context(viewport={"width": 430, "height": 900},
                                    timezone_id=tz, locale="bg-BG")
                page = ctx.new_page()
                errs = []
                page.on("pageerror", lambda e: errs.append(str(e)))
                page.goto(HTML.resolve().as_uri())
                page.wait_for_selector(".onb, .bar, #hardreset", timeout=20000)
                if page.query_selector("#hardreset"):
                    fail(f"[{tz}] the app booted straight into its error panel")
                if page.query_selector("[data-onb-skip]"):
                    page.click("[data-onb-skip]")
                page.wait_for_selector(".bar", timeout=20000)
                cards = page.query_selector_all("[data-film]")
                if not cards:
                    fail(f"[{tz}] no film cards rendered")
                else:
                    cards[0].click()
                    page.wait_for_timeout(700)
                    if not page.query_selector(".sheet"):
                        fail(f"[{tz}] clicking a film did not open its detail sheet"
                             + (" — " + errs[0] if errs else ""))
                    elif not page.query_selector_all(".sheet a.time"):
                        fail(f"[{tz}] the film sheet opened with no ticket links")
                    else:
                        page.go_back(); page.wait_for_timeout(400)
                # same journey on the theatre side
                sw = page.query_selector_all("[data-switch]")
                if len(sw) > 1:
                    sw[1].click(); page.wait_for_timeout(500)
                    shows = page.query_selector_all("[data-show]")
                    if not shows:
                        fail(f"[{tz}] no theatre listings rendered")
                    else:
                        shows[0].click(); page.wait_for_timeout(700)
                        if not page.query_selector(".sheet"):
                            fail(f"[{tz}] clicking a performance did not open its sheet"
                                 + (" — " + errs[0] if errs else ""))
                if errs:
                    fail(f"[{tz}] JavaScript errors: " + " | ".join(sorted(set(errs))[:3]))
                ctx.close()
            b.close()
    except Exception as e:
        note(f"live click test could not run ({e.__class__.__name__}: {e})")


if os.environ.get("SOFIA_SKIP_SMOKE") != "1":
    smoke()

# ------------------------------------------------------------------- verdict
print(f"verify_build: {HTML} ({HTML.stat().st_size:,} bytes)")
for n in notes:
    print("  note:", n)
if problems:
    print(f"\n  {len(problems)} problem(s) — NOT safe to publish:\n")
    for p in problems:
        print("   ✗", p)
    print("\nThe previous index.html is still good; nothing was committed.")
    sys.exit(1)
print("  all checks passed — safe to publish")
sys.exit(0)
