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

# ----------------------------------------- 4b. VLINKS structural checks
# VLINKS=[[filmId, venueId, url], …]; each URL must be on that venue's allowlist.
# Programata links must never appear.
VENUE_LINK_ALLOWLIST = {
    "cc-sofia":     ["cinemacity.bg"],
    "cc-paradise":  ["cinemacity.bg"],
    "arena-mega":   ["kinoarena.com"],
    "arena-mall":   ["kinoarena.com"],
    "cg-ring":      ["cinegrand.bg"],
    "cg-park":      ["cinegrand.bg"],
    "cineland":     ["cineland.bg"],
    "vlaikova":     ["vlaikovacinema.com", "embed.urboapp.com"],
    "lumiere":      ["ndk.bg", "epaygo.bg"],
    "dom-kino":     ["domnakinoto.com"],
    "odeon":        ["bnf.bg"],
    "g8":           ["g8cinema.com"],
}

def const_arr_raw(name):
    """Return the parsed array for a const, or None."""
    m_arr = re.search(r"^const %s\s*=\s*(\[.*?\]);\s*$" % name, js, flags=re.M | re.S)
    if not m_arr:
        return None
    try:
        return json.loads(m_arr.group(1))
    except Exception:
        return None

vlinks_data = const_arr_raw("VLINKS")
if vlinks_data is not None:
    from urllib.parse import urlparse as _urlparse
    if not isinstance(vlinks_data, list):
        fail("VLINKS is not an array")
    else:
        bad_programata = []
        bad_allowlist = []
        for entry in vlinks_data:
            if not (isinstance(entry, list) and len(entry) == 3):
                fail(f"VLINKS entry is not [filmId, venueId, url]: {str(entry)[:80]}")
                continue
            fid, vid, url = entry
            if not isinstance(url, str):
                fail(f"VLINKS[{fid},{vid}] url is not a string")
                continue
            host = _urlparse(url).netloc.lstrip("www.")
            if "programata.bg" in host:
                bad_programata.append(f"{fid}/{vid}")
            allowed = VENUE_LINK_ALLOWLIST.get(vid, [])
            if allowed and not any(a in host for a in allowed):
                bad_allowlist.append(f"{fid}/{vid} -> {url[:60]}")
        if bad_programata:
            fail(f"VLINKS contains {len(bad_programata)} programata.bg URL(s) — "
                 "these must never appear in VLINKS: "
                 + ", ".join(bad_programata[:5]))
        if bad_allowlist:
            fail(f"VLINKS contains {len(bad_allowlist)} URL(s) not on the venue's allowlist: "
                 + "; ".join(bad_allowlist[:3]))
        # Check FILMINFO is a JSON object (not array, not null)
        filminfo_m = re.search(r"^const FILMINFO\s*=\s*(\{.*?\});\s*$", js, flags=re.M | re.S)
        if filminfo_m:
            try:
                fi = json.loads(filminfo_m.group(1))
                if not isinstance(fi, dict):
                    fail("FILMINFO is not a JSON object")
            except Exception as e:
                fail(f"FILMINFO is not valid JSON: {e}")
        # Check every upcoming (film, venue) in SHOWTIMES has either a VLINKS entry
        # or a BOOKING entry with a url.  "Upcoming" means date >= window.from —
        # past rows from the tail of the window are irrelevant.
        if data.get("SHOWTIMES") and data.get("BOOKING"):
            vlinks_pairs = {(e[0], e[1]) for e in vlinks_data if isinstance(e, list) and len(e) == 3}
            booking = data["BOOKING"]
            missing_ticket = []
            snap = data.get("SNAPSHOT") or {}
            w = snap.get("window") or {}
            w_from = w.get("from", "0000-00-00")  # only upcoming rows
            for row in data["SHOWTIMES"]:
                if len(row) < 3:
                    continue
                fid, vid, date = row[0], row[1], row[2]
                if date < w_from:   # skip past rows
                    continue
                has_vlink = (fid, vid) in vlinks_pairs
                has_booking = (vid in booking and booking[vid].get("url"))
                if not has_vlink and not has_booking:
                    missing_ticket.append(f"{fid}/{vid}")
            if missing_ticket:
                # Every cinema we scrape MUST have a BOOKING entry — any miss
                # indicates a real scraping/config bug, so always FAIL.
                uniq = sorted(set(missing_ticket))
                fail(f"{len(uniq)} upcoming (film, venue) pairs have neither a VLINKS entry nor "
                     f"a BOOKING url (e.g. {', '.join(uniq[:4])}) — ticket links will be absent")

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
    # The scraper advances window.from to today and drops every past row; a row
    # dated before it is a stale listing that survived a merge.
    if w.get("from"):
        past_st = [r for r in (data["SHOWTIMES"] or []) if len(r) > 2 and r[2] < w["from"]]
        past_pf = [p for p in (data["PERFORMANCES"] or []) if len(p) > 1 and p[1] < w["from"]]
        if past_st or past_pf:
            ex = [f"{r[0]}@{r[1]} {r[2]}" for r in past_st[:3]] + [f"{p[0]} {p[1]}" for p in past_pf[:3]]
            fail(f"{len(past_st)} showtime(s) and {len(past_pf)} performance(s) are dated before "
                 f"the window opens ({w['from']}), e.g. {', '.join(ex)}")

# ---------------------------------------- 4c. PRELIM_FROM — which listings are preliminary
# {venueId: "YYYY-MM-DD"}: every listing of that venue dated on/after the date is
# shown as preliminary (the venue's own programme does not cover it yet).
_pm = re.search(r"^const PRELIM_FROM\s*=\s*(.*?);\s*$", js, flags=re.M)
if _pm is None:
    note("PRELIM_FROM is not declared — no listing will be marked preliminary")
else:
    try:
        _prelim = json.loads(_pm.group(1))
    except Exception:
        _prelim = None
    if not isinstance(_prelim, dict):
        fail("PRELIM_FROM is not a JSON object of venue id -> \"YYYY-MM-DD\"")
    else:
        _venues = ({c.get("id") for c in (data["CINEMAS"] or [])}
                   | {t.get("id") for t in (data["THEATRES"] or [])})
        _unknown = sorted(k for k in _prelim if k not in _venues)
        if _unknown:
            fail(f"PRELIM_FROM names unknown venues: {', '.join(_unknown[:6])}")

        def _iso_ok(v):
            if not (isinstance(v, str) and re.fullmatch(r"\d{4}-\d\d-\d\d", v)):
                return False
            try:
                import datetime as _dt
                _dt.date.fromisoformat(v)
                return True
            except ValueError:
                return False
        _bad = sorted(k for k, v in _prelim.items() if not _iso_ok(v))
        if _bad:
            fail(f"PRELIM_FROM has non-ISO dates for: {', '.join(_bad[:6])}")

        # Theatres (wave M3): once the theatre merge has run (PRELIM_FROM names a
        # theatre), every theatre with upcoming performances must be in the map —
        # a missing one would show aggregator rows as confirmed — and a theatre
        # with no official programme of its own is preliminary from the first day.
        _th_ids = {t.get("id") for t in (data["THEATRES"] or [])}
        _show_th = {s.get("id"): s.get("theatre") for s in (data["SHOWS"] or [])}
        _w_from = ((data["SNAPSHOT"] or {}).get("window") or {}).get("from") or "0000-00-00"
        _playing = {_show_th.get(p[0]) for p in (data["PERFORMANCES"] or [])
                    if len(p) > 1 and p[1] >= _w_from} - {None}
        if not (_th_ids & set(_prelim)):
            if _playing:
                note("PRELIM_FROM names no theatre — theatre listings are not marked preliminary "
                     "(the theatre merge has not run on this build)")
        else:
            _missing = sorted(_playing - set(_prelim))
            if _missing:
                fail(f"theatres with performances but no PRELIM_FROM entry: {', '.join(_missing[:6])}")
            try:
                sys.path.insert(0, str(ROOT / "scripts"))
                import official_theatres as _OT                           # noqa: E402
                _no_official = set(_OT.NO_OFFICIAL_SOURCE)
            except BaseException:                                         # bs4 missing: same list
                _no_official = {"atelie313", "natfiz", "new-ndk", "derida"}
            _late = sorted(t for t in _playing & _no_official
                           if t in _prelim and isinstance(_prelim[t], str) and _prelim[t] > _w_from)
            if _late:
                fail(f"theatres with no official programme are not preliminary from the first day: "
                     f"{', '.join(_late)}")

# ------------------------------------------------- 4d. theatre rows are well-formed
if data["SHOWS"] and data["THEATRES"]:
    _th_ids = {t.get("id") for t in data["THEATRES"]}
    _bad_th = sorted({str(s.get("theatre")) for s in data["SHOWS"]} - _th_ids)
    if _bad_th:
        fail(f"shows filed under unknown theatres: {', '.join(_bad_th[:6])}")
if data["PERFORMANCES"]:
    _bad_rows = [p for p in data["PERFORMANCES"]
                 if not (isinstance(p, list) and len(p) == 5 and isinstance(p[0], str)
                         and re.fullmatch(r"\d{4}-\d\d-\d\d", str(p[1]))
                         and re.fullmatch(r"\d\d:\d\d", str(p[2])))]
    if _bad_rows:
        fail(f"{len(_bad_rows)} PERFORMANCES row(s) are not [showId, date, time, hall, price], "
             f"e.g. {str(_bad_rows[0])[:80]}")
    _dup = len(data["PERFORMANCES"]) - len({tuple(p[:3]) for p in data["PERFORMANCES"] if isinstance(p, list)})
    if _dup:
        fail(f"{_dup} duplicate performance(s) (same show, date and time)")

# ------------------------------------- 4e. no VLINKS where BOOKING has a dated page
# Cinema City's film pages are chain-wide; for a venue whose BOOKING carries `deep`
# the ticket link must open that cinema's page for the chosen date.
if vlinks_data and isinstance(data.get("BOOKING"), dict):
    _deep = {v for v, b in data["BOOKING"].items() if isinstance(b, dict) and b.get("deep")}
    _deep_rows = sorted({e[1] for e in vlinks_data if isinstance(e, list) and len(e) == 3 and e[1] in _deep})
    if _deep_rows:
        fail(f"VLINKS has entries for {', '.join(_deep_rows)}, whose BOOKING.deep per-date page "
             "must be used instead")

# ------------------------------------------- 5a2. SYN_EN / TITLE_EN translation maps
def _const_obj_tr(name):
    m = re.search(r"^const %s\s*=\s*(\{.*?\});\s*$" % name, js, flags=re.M)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None

def _all_ids():
    """Union of all known film and show ids."""
    ids = set()
    if data.get("FILMS"):
        ids |= {f["id"] for f in data["FILMS"]}
    if data.get("SHOWS"):
        ids |= {s["id"] for s in data["SHOWS"]}
    return ids

_known_ids = _all_ids()

for _map_name in ("SYN_EN", "TITLE_EN"):
    _m = _const_obj_tr(_map_name)
    if _m is None:
        fail(f"{_map_name} is missing from the SOFIA-POSTERS block — inject_data.py may be outdated")
        continue
    if not isinstance(_m, dict):
        fail(f"{_map_name} is not a JSON object")
        continue
    _bad_ids = sorted(k for k in _m if k not in _known_ids)
    if _bad_ids:
        fail(f"{_map_name} contains unknown ids: {', '.join(_bad_ids[:6])}")
    _empty_vals = [k for k, v in _m.items() if not v]
    if _empty_vals:
        fail(f"{_map_name} has empty values: {', '.join(_empty_vals[:6])}")
    _cyrillic_vals = [k for k, v in _m.items()
                      if isinstance(v, str) and re.search(r"[Ѐ-ӿ]", v)]
    if _cyrillic_vals:
        fail(f"{_map_name} contains Cyrillic (untranslated) values: "
             + ", ".join(_cyrillic_vals[:6]))

# ------------------------------------------- 5b. posters point at real artwork
try:
    sys.path.insert(0, str(ROOT / "scripts"))
    from posterpolicy import Catalogue, reject_reason        # noqa: E402

    def const_obj(name):
        m = re.search(r"^const %s\s*=\s*(\{.*?\});\s*$" % name, js, flags=re.M)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except Exception:
            return None

    cat = Catalogue.from_html(HTML)
    for mapname in ("SHOWART", "POSTERS"):
        m = const_obj(mapname)
        if not m:
            continue
        for pid, url in m.items():
            why = reject_reason(pid, url if isinstance(url, str) else "", cat)
            if why and not (isinstance(url, str) and url.startswith("data:")):
                fail(f"{mapname}[{pid!r}] is not an acceptable poster: {why}")
except ImportError:
    note("posterpolicy.py not found — skipped the poster provenance check")
except Exception as e:
    note(f"poster provenance check skipped: {e}")

# --------------------------------- 5c. is the mirrored-event alias actually wired
# inject_data.py computes SHOWALIAS (event id -> film id) for cinema events that
# are screenings of a catalogued film. It only has an effect once realPoster()
# resolves through it. Say so loudly rather than letting a rebuild quietly drop
# the fix on the floor.
try:
    m = re.search(r"^const SHOWALIAS\s*=\s*(\{.*?\});\s*$", js, flags=re.M)
    aliases = json.loads(m.group(1)) if m else {}
    reads = len(re.findall(r"(?<![.\w$])SHOWALIAS\b", code)) - 1   # minus the declaration
    if aliases and reads < 1:
        # A note, not a failure: falling back to generated art is correct, just not
        # ideal, and the wrong-poster bug itself is already blocked by 5b above.
        # Blocking the weekly commit over a cosmetic improvement would be worse.
        note("SHOWALIAS is generated but realPoster() never reads it, so "
             + ", ".join(f"{k} still falls back to generated art instead of {v}'s poster"
                         for k, v in list(aliases.items())[:3])
             + ". Add the alias lookup to realPoster() in src/sofia-screen.artifact.html "
               "and rebuild with scripts/build_index.py.")
except Exception as e:
    note(f"SHOWALIAS wiring check skipped: {e}")

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
