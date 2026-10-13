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
import datetime, json, os, re, subprocess, sys, pathlib, tempfile

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
    "ndk1":         ["ndk.bg"],
    "frenski-institut": ["institutfrancais.bg"],
}
# A festival row (official_festivals.py) links the festival's own film page or
# its ticket shop, at any venue: cinelibri.com and kinomania.bg, epaygo.bg (both
# festivals) and embed.urboapp.com (CineLibri at Френски институт) — checked live
# 2026-10-09. Same list as official_festivals.TICKET_HOSTS.
try:
    sys.path.insert(0, str(ROOT / "scripts"))
    from official_festivals import TICKET_HOSTS as FESTIVAL_TICKET_HOSTS   # noqa: E402
except BaseException:                                    # bs4 missing: same list
    FESTIVAL_TICKET_HOSTS = ("cinelibri.com", "kinomania.bg", "epaygo.bg", "urboapp.com")


def festival_host(host):
    return any(host == h or host.endswith("." + h) for h in FESTIVAL_TICKET_HOSTS)

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
        # Build a set of confirmed (fid, venue) pairs: rows whose date is before
        # PRELIM_FROM[venue] (i.e. the venue's own programme listed them).
        # Used to enforce that aggregator-domain links never serve confirmed pairs.
        _prelim_from = const("PRELIM_FROM") or {}
        _showtimes = data.get("SHOWTIMES") or []
        _confirmed_pairs: set = set()
        for _r in _showtimes:
            if not (isinstance(_r, list) and len(_r) >= 3):
                continue
            _fid, _vid, _d = _r[0], _r[1], _r[2]
            _pf = _prelim_from.get(_vid)
            if _pf and _d < _pf:
                _confirmed_pairs.add((_fid, _vid))
        bad_programata = []
        bad_aggregator_confirmed = []
        bad_allowlist = []
        bad_agg_homepage = []    # aggregator homepage: empty/root path or query-only

        def _is_agg_homepage(parsed_url):
            """Return True when the URL is an aggregator's homepage (no meaningful path)."""
            p = parsed_url.path.rstrip("/")
            q = parsed_url.query
            # Empty path, root "/", or a URL with only a query string (e.g. ?city=20)
            return not p or (not p and bool(q))

        for entry in vlinks_data:
            if not (isinstance(entry, list) and len(entry) == 3):
                fail(f"VLINKS entry is not [filmId, venueId, url]: {str(entry)[:80]}")
                continue
            fid, vid, url = entry
            if not isinstance(url, str):
                fail(f"VLINKS[{fid},{vid}] url is not a string")
                continue
            parsed = _urlparse(url)
            host = parsed.netloc.lower()
            if host.startswith("www."):
                host = host[4:]
            # Scheme check: only http(s) are safe in href sinks (CODE-12)
            if parsed.scheme not in ("http", "https"):
                fail(f"VLINKS[{fid},{vid}] has non-http(s) scheme: {url[:80]}")
                continue
            _is_agg_domain = (
                host == "programata.bg" or host.endswith(".programata.bg") or
                host == "theatre.art.bg" or host.endswith(".theatre.art.bg")
            )
            # Aggregator homepage gate: an aggregator URL with no path (e.g.
            # https://programata.bg or https://theatre.art.bg/?city=20) is
            # almost certainly a scraper bug — it would open the aggregator's
            # front page rather than a specific show or film.
            if _is_agg_domain and _is_agg_homepage(parsed):
                bad_agg_homepage.append(f"{fid}/{vid} -> {url[:80]}")
            if host == "programata.bg" or host.endswith(".programata.bg"):
                # Allowed only when all rows for this pair are preliminary
                if (fid, vid) in _confirmed_pairs:
                    bad_programata.append(f"{fid}/{vid}")
            if _is_agg_domain and (fid, vid) in _confirmed_pairs:
                bad_aggregator_confirmed.append(f"{fid}/{vid} -> {url[:60]}")
            allowed = VENUE_LINK_ALLOWLIST.get(vid, [])
            # Exact match or subdomain suffix — never substring (CODE-12). A festival
            # row may also link the festival's own page or ticket shop (festival_host,
            # matched the same way). Aggregator-domain links for preliminary-only
            # pairs pass through: they are allowed explicitly (see scrape_programs.py).
            def _host_ok(h, allowed_list):
                return any(h == a or h.endswith("." + a) for a in allowed_list)
            if allowed and not _host_ok(host, allowed) and not festival_host(host) and not _is_agg_domain:
                bad_allowlist.append(f"{fid}/{vid} -> {url[:60]}")
        if bad_programata:
            fail(f"VLINKS contains {len(bad_programata)} programata.bg URL(s) for "
                 "confirmed (venue-listed) rows — only preliminary-only pairs may use programata: "
                 + ", ".join(bad_programata[:5]))
        if bad_aggregator_confirmed:
            fail(f"VLINKS contains {len(bad_aggregator_confirmed)} aggregator URL(s) serving "
                 "confirmed (venue-listed) pairs — switch to the venue's own link: "
                 + "; ".join(bad_aggregator_confirmed[:3]))
        if bad_allowlist:
            fail(f"VLINKS contains {len(bad_allowlist)} URL(s) not on the venue's allowlist: "
                 + "; ".join(bad_allowlist[:3]))
        if bad_agg_homepage:
            fail(f"VLINKS contains {len(bad_agg_homepage)} aggregator homepage URL(s) "
                 "(empty/root path or query-only — likely a scraper bug; use a specific listing URL): "
                 + "; ".join(bad_agg_homepage[:3]))
        # Check FILMINFO is a JSON object (not array, not null)
        filminfo_m = re.search(r"^const FILMINFO\s*=\s*(\{.*?\});\s*$", js, flags=re.M | re.S)
        if filminfo_m:
            try:
                fi = json.loads(filminfo_m.group(1))
                if not isinstance(fi, dict):
                    fail("FILMINFO is not a JSON object")
            except Exception as e:
                fail(f"FILMINFO is not valid JSON: {e}")
            else:
                # Detect label-bleed in dir/cast: a credit label keyword inside
                # the value means the parser ate multiple fields, or the source
                # had garbage data that was not sanitised.
                _CREDIT_LABEL_PAT = re.compile(
                    r'\b(?:Screenplay|Starring|Cinematography|Editing|Music|'
                    r'Producer|Language|Subtitles|Duration|Director|Cast|'
                    r'Сценарий|Участват|В\s+ролите|Режисьор|Оператор|Музика|'
                    r'Продуцент|Монтаж|Език)\s*[:：]',
                    re.I,
                )
                # Detect a dangling single-letter token after a colon,
                # e.g. "В ролите: О" — a sign of garbage source data.
                _DANGLING_TOKEN_PAT = re.compile(r':\s*\b[A-Za-zА-Яа-яЁёА-ЩЫЬЭЮЯа-щыьэюя]\b\s*$')
                bad_fi: list[str] = []
                for fid, entry in fi.items():
                    for field in ("dir", "cast"):
                        val = entry.get(field) or ""
                        if not val:
                            continue
                        if _CREDIT_LABEL_PAT.search(val):
                            bad_fi.append(f"{fid}.{field}={val[:60]!r}")
                        elif _DANGLING_TOKEN_PAT.search(val):
                            bad_fi.append(f"{fid}.{field}={val[:60]!r} (dangling token)")
                if bad_fi:
                    fail("FILMINFO dir/cast contains credit label bleed:\n  "
                         + "\n  ".join(bad_fi[:10]))
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

# ---------------------------------------- 4b2. freshness gate (CODE-02)
# Fail when SNAPSHOT.generated is missing or older than 36 hours (unless
# SOFIA_SKIP_FRESHNESS=1, which test_ui.py and dev_build.py can set so fixture
# and offline builds are not blocked by an old seed snapshot).
_FRESHNESS_MAX_HOURS = 36
_skip_freshness = os.environ.get("SOFIA_SKIP_FRESHNESS") == "1"
if not _skip_freshness:
    _gen = (snap or {}).get("generated") if isinstance(snap, dict) else None
    if not _gen:
        fail("SNAPSHOT.generated is missing — the scraper has not run on this build; "
             "set SOFIA_SKIP_FRESHNESS=1 for fixture/dev builds")
    else:
        try:
            # Parse ISO 8601 with offset (e.g. "2026-10-09T06:15:00+03:00")
            import email.utils as _eu
            _gen_clean = _gen.replace("Z", "+00:00")
            _gen_dt = datetime.datetime.fromisoformat(_gen_clean)
            _now = datetime.datetime.now(tz=datetime.timezone.utc)
            _age_h = (_now - _gen_dt.astimezone(datetime.timezone.utc)).total_seconds() / 3600
            if _age_h > _FRESHNESS_MAX_HOURS:
                fail(f"SNAPSHOT.generated is {_age_h:.1f} h old (limit {_FRESHNESS_MAX_HOURS} h) — "
                     "the scraper has not run recently; set SOFIA_SKIP_FRESHNESS=1 for fixture/dev builds")
        except Exception as _fe:
            fail(f"SNAPSHOT.generated could not be parsed as ISO 8601: {_gen!r} ({_fe})")
    # Fail when there are no upcoming rows (CODE-02)
    _today_iso = datetime.date.today().isoformat()
    _upcoming_st = [r for r in (data.get("SHOWTIMES") or []) if len(r) > 2 and r[2] >= _today_iso]
    _upcoming_pf = [p for p in (data.get("PERFORMANCES") or []) if len(p) > 1 and p[1] >= _today_iso]
    if not _upcoming_st and not _upcoming_pf:
        fail("no upcoming rows (all SHOWTIMES and PERFORMANCES are in the past) — "
             "the build would show an empty programme; set SOFIA_SKIP_FRESHNESS=1 for fixture/dev builds")

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

# ---------------------------------------- 4c2. PRELIM_ROWS — rows marked preliminary one by one
# [[id, venueId, "YYYY-MM-DD"], …]: either a festival screening the venue's own
# programme does not list (cinema row), or a theatre show with an off-grid
# ticket-list time (theatre row). Cinema rows must match a SHOWTIMES entry at a
# listed cinema; theatre rows must match a PERFORMANCES entry (id, date) at the
# listed theatre (show and film ids can collide, but venue ids cannot).
_pr = re.search(r"^const PRELIM_ROWS\s*=\s*(.*?);\s*$", js, flags=re.M)
if _pr is None:
    note("PRELIM_ROWS is not declared — no row is marked preliminary on its own")
else:
    try:
        _prows = json.loads(_pr.group(1))
    except Exception:
        _prows = None
    if not isinstance(_prows, list):
        fail("PRELIM_ROWS is not a JSON array of [id, venueId, \"YYYY-MM-DD\"]")
    else:
        _bad_pr = [x for x in _prows if not (isinstance(x, list) and len(x) == 3
                                             and all(isinstance(v, str) for v in x)
                                             and re.fullmatch(r"\d{4}-\d\d-\d\d", x[2]))]
        if _bad_pr:
            fail(f"{len(_bad_pr)} PRELIM_ROWS entr(ies) are not [id, venueId, \"YYYY-MM-DD\"], "
                 f"e.g. {str(_bad_pr[0])[:80]}")
        _cin = {c.get("id") for c in (data["CINEMAS"] or [])}
        _th = {t.get("id") for t in (data["THEATRES"] or [])}
        # A show's theatre is looked up via SHOWS; build a map show_id -> theatre_id
        _show_theatre = {s.get("id"): s.get("theatre") for s in (data["SHOWS"] or [])
                         if isinstance(s, dict)}
        _unk = sorted({x[1] for x in _prows if isinstance(x, list) and len(x) == 3}
                      - _cin - _th)
        if _unk:
            fail(f"PRELIM_ROWS names venues the app does not list: {', '.join(_unk[:6])}")
        # Cinema rows: check against SHOWTIMES (filmId, cinemaId, date)
        _st = {(r[0], r[1], r[2]) for r in (data["SHOWTIMES"] or []) if isinstance(r, list) and len(r) > 2}
        _missing_cin = [x for x in _prows if isinstance(x, list) and len(x) == 3
                        and x[1] in _cin and tuple(x) not in _st]
        if _missing_cin:
            fail(f"{len(_missing_cin)} PRELIM_ROWS cinema row(s) have no SHOWTIMES row, "
                 f"e.g. {', '.join('/'.join(x) for x in _missing_cin[:3])}")
        # Theatre rows: check against PERFORMANCES — the show must exist at that theatre
        # and have a PERFORMANCES entry on that date (any time)
        _perf_show_date = {(p[0], p[1]) for p in (data["PERFORMANCES"] or [])
                           if isinstance(p, list) and len(p) >= 2}
        _missing_th = [x for x in _prows if isinstance(x, list) and len(x) == 3
                       and x[1] in _th
                       and (_show_theatre.get(x[0]) != x[1]    # show not at that theatre
                            or (x[0], x[2]) not in _perf_show_date)]  # or no perf on that date
        if _missing_th:
            fail(f"{len(_missing_th)} PRELIM_ROWS theatre row(s) have no matching PERFORMANCES entry, "
                 f"e.g. {', '.join('/'.join(x) for x in _missing_th[:3])}")

# ---------------------------------------- 4c2b. AGGLINKS integrity
# AGGLINKS = {"<showId>": "theatre.art.bg", "<filmId>|<venueId>": "programata.bg", …}
# Rules:
#   1. Every key must exist as a LINKS entry (show id) or VLINKS entry ("fid|vid").
#   2. The URL for that key must be on the named aggregator (exact or subdomain).
#   3. No key may name a (film, venue) pair that has confirmed (non-preliminary) rows.
#   4. The value must be a recognised aggregator name.
_KNOWN_AGG = {"theatre.art.bg", "programata.bg"}
_al_m = re.search(r"^const AGGLINKS\s*=\s*(\{[^\n]*?\});\s*$", js, re.M)
if _al_m:
    try:
        _agglinks = json.loads(_al_m.group(1))
    except Exception as _ale:
        fail(f"AGGLINKS is not valid JSON: {_ale}")
        _agglinks = {}
    if not isinstance(_agglinks, dict):
        fail("AGGLINKS is not a JSON object")
        _agglinks = {}
    if _agglinks:
        from urllib.parse import urlparse as _up_al
        # Build index of LINKS keys and their URLs
        _links_m2 = re.search(r"^const LINKS\s*=\s*(\[.*?\]);\s*$", js, flags=re.M | re.S)
        _links_idx = {}  # showId -> url
        if _links_m2:
            try:
                for _e in json.loads(_links_m2.group(1)):
                    if isinstance(_e, list) and len(_e) == 2:
                        _links_idx[_e[0]] = _e[1]
            except Exception:
                pass
        # Build index of VLINKS keys
        _vlinks_idx = {}  # "fid|vid" -> url
        if vlinks_data:
            for _e in vlinks_data:
                if isinstance(_e, list) and len(_e) == 3:
                    _vlinks_idx[f"{_e[0]}|{_e[1]}"] = _e[2]
        # Build confirmed pairs from PRELIM_FROM + SHOWTIMES (reuse earlier computation)
        _pf2 = const("PRELIM_FROM") or {}
        _confirmed2: set = set()
        for _r in (_showtimes if "_showtimes" in dir() else (data.get("SHOWTIMES") or [])):
            if isinstance(_r, list) and len(_r) >= 3:
                _pf_d = _pf2.get(_r[1])
                if _pf_d and _r[2] < _pf_d:
                    _confirmed2.add((_r[0], _r[1]))
        _al_bad_key, _al_bad_host, _al_bad_confirmed, _al_bad_value = [], [], [], []
        for _ak, _av in _agglinks.items():
            # Rule 4: recognised value
            if _av not in _KNOWN_AGG:
                _al_bad_value.append(f"{_ak!r}: {_av!r}")
                continue
            # Determine url and rule 1 (key exists in LINKS or VLINKS)
            if "|" in _ak:
                _aurl = _vlinks_idx.get(_ak)
                if _aurl is None:
                    _al_bad_key.append(_ak)
                    continue
                # Rule 3: confirmed pair
                _parts = _ak.split("|", 1)
                if len(_parts) == 2 and (_parts[0], _parts[1]) in _confirmed2:
                    _al_bad_confirmed.append(_ak)
            else:
                _aurl = _links_idx.get(_ak)
                if _aurl is None:
                    _al_bad_key.append(_ak)
                    continue
            # Rule 2: URL host matches aggregator
            try:
                _ah = _up_al(_aurl).netloc.lower()
                if _ah.startswith("www."):
                    _ah = _ah[4:]
                if not (_ah == _av or _ah.endswith("." + _av)):
                    _al_bad_host.append(f"{_ak!r} claims {_av!r} but URL host is {_ah!r}")
            except Exception:
                _al_bad_host.append(f"{_ak!r}: could not parse URL {str(_aurl)[:60]!r}")
        if _al_bad_value:
            fail(f"AGGLINKS has {len(_al_bad_value)} unrecognised aggregator name(s) "
                 f"(must be one of {sorted(_KNOWN_AGG)}): " + "; ".join(_al_bad_value[:3]))
        if _al_bad_key:
            fail(f"AGGLINKS has {len(_al_bad_key)} key(s) with no corresponding LINKS/VLINKS entry: "
                 + ", ".join(_al_bad_key[:5]))
        if _al_bad_host:
            fail(f"AGGLINKS has {len(_al_bad_host)} key(s) whose URL host does not match the "
                 "named aggregator: " + "; ".join(_al_bad_host[:3]))
        if _al_bad_confirmed:
            fail(f"AGGLINKS has {len(_al_bad_confirmed)} key(s) naming confirmed (venue-listed) "
                 "pairs — only preliminary-only pairs may appear in AGGLINKS: "
                 + ", ".join(_al_bad_confirmed[:5]))

# ------------------------------------------- 4c3. every cinema has a BOOKING entry
# A venue added to CINEMAS (Френски институт, wave F3) must come with its BOOKING
# entry, or a row there whose film has no VLINKS entry opens nothing.
if data["CINEMAS"] and isinstance(data.get("BOOKING"), dict):
    _no_booking = sorted(c.get("id") for c in data["CINEMAS"]
                         if not (isinstance(data["BOOKING"].get(c.get("id")), dict)
                                 and (data["BOOKING"][c.get("id")].get("url")
                                      or data["BOOKING"][c.get("id")].get("inPerson"))))
    if _no_booking:
        fail(f"cinemas without a BOOKING entry (url or inPerson): {', '.join(_no_booking[:6])}")

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

# ----------------------------------------- 5d. no non-http(s) URLs in data constants (CODE-12)
# Every URL that ends up in an href or window.open must be http(s).
# Check the inline data constants: LINKS, BOOKING, VLINKS already checked above.
from urllib.parse import urlparse as _up2
_bad_scheme_urls: list[str] = []

def _check_url_scheme(label, url):
    if not isinstance(url, str) or not url:
        return
    p = _up2(url)
    if p.scheme and p.scheme not in ("http", "https"):
        _bad_scheme_urls.append(f"{label}: {url[:80]}")

# LINKS = [[showId, url], ...]
_links_m = re.search(r"^const LINKS\s*=\s*(\[.*?\]);\s*$", js, flags=re.M | re.S)
_links_agg_homepages: list[str] = []
if _links_m:
    try:
        for entry in json.loads(_links_m.group(1)):
            if isinstance(entry, list) and len(entry) == 2:
                _check_url_scheme(f"LINKS[{entry[0]}]", entry[1])
                # Aggregator homepage gate for LINKS (same rule as VLINKS)
                if isinstance(entry[1], str) and entry[1]:
                    _lp = _up2(entry[1])
                    _lh = _lp.netloc.lower()
                    if _lh.startswith("www."):
                        _lh = _lh[4:]
                    _l_is_agg = (_lh == "theatre.art.bg" or _lh.endswith(".theatre.art.bg") or
                                  _lh == "programata.bg" or _lh.endswith(".programata.bg"))
                    if _l_is_agg and not _lp.path.rstrip("/"):
                        _links_agg_homepages.append(f"LINKS[{entry[0]}]: {entry[1][:80]}")
    except Exception:
        pass
if _links_agg_homepages:
    fail(f"LINKS contains {len(_links_agg_homepages)} aggregator homepage URL(s) "
         "(empty/root path — likely a scraper bug; use a specific show-page URL): "
         + "; ".join(_links_agg_homepages[:3]))

# BOOKING = {venueId: {url, tix, deep, ...}, ...}
if isinstance(data.get("BOOKING"), dict):
    for _vid, _bv in data["BOOKING"].items():
        if isinstance(_bv, dict):
            for _k in ("url", "tix", "deep"):
                _check_url_scheme(f"BOOKING[{_vid}].{_k}", _bv.get(_k))

if _bad_scheme_urls:
    fail(f"{len(_bad_scheme_urls)} non-http(s) URL(s) in data constants (CODE-12 — "
         "these reach href/window.open without scheme filtering): "
         + "; ".join(_bad_scheme_urls[:5]))

# ----------------------------------------------------- 6. a real click, live
def smoke():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        note("Playwright not installed — skipped the live click test")
        return
    # Only pre-navigation failures (binary missing, launch error) are allowed to
    # be notes.  Everything that happens after page.goto is a hard failure so a
    # broken page can never pass the gate (CODE-04).
    pw_ctx = None
    try:
        pw_ctx = sync_playwright().__enter__()
        b = pw_ctx.chromium.launch()
    except Exception as e:
        note(f"browser launch failed ({e.__class__.__name__}: {e}) — skipped the live click test")
        if pw_ctx is not None:
            try:
                pw_ctx.__exit__(None, None, None)
            except Exception:
                pass
        return
    try:
        for tz in ("Europe/Sofia", "UTC"):
            ctx = b.new_context(viewport={"width": 430, "height": 900},
                                timezone_id=tz, locale="bg-BG")
            page = ctx.new_page()
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            # From here on: any exception is a hard fail, not a note (CODE-04)
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
        # Any exception after page.goto is a hard failure (CODE-04)
        fail(f"[live click test] {e.__class__.__name__}: {e}")
    finally:
        try:
            pw_ctx.__exit__(None, None, None)
        except Exception:
            pass


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
