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
# SOFIA_ROOT overrides the directory used for icon/share-page file existence
# checks (6b, 6c). Set it in test environments to point at a temp fixture tree.
_FILE_ROOT = pathlib.Path(os.environ["SOFIA_ROOT"]) if "SOFIA_ROOT" in os.environ else ROOT

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

# ----------------------------------------- 4c. PREMIERES structural checks
# PREMIERES: {"show:<showId>": ["YYYY-MM-DD", …], "film:<filmId>": ["YYYY-MM-DD", …]}
# Keys must start with "show:" or "film:"; dates must be ISO 8601 YYYY-MM-DD.
# No entry may originate from an aggregator-only venue (those lack official data).
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_prem_m = re.search(r"^const PREMIERES\s*=\s*(\{[^\n]*?\});\s*$", js, flags=re.M)
if _prem_m:
    try:
        _prem = json.loads(_prem_m.group(1))
    except Exception as _e:
        fail(f"PREMIERES is not valid JSON: {_e}")
    else:
        if not isinstance(_prem, dict):
            fail("PREMIERES is not a JSON object")
        else:
            _prem_bad_keys = [k for k in _prem if not (k.startswith("show:") or k.startswith("film:"))]
            if _prem_bad_keys:
                fail("PREMIERES keys must start with 'show:' or 'film:': "
                     + ", ".join(_prem_bad_keys[:6]))
            _prem_bad_dates = []
            for _k, _dates in _prem.items():
                if not isinstance(_dates, list):
                    _prem_bad_dates.append(f"{_k}: not an array")
                else:
                    for _d in _dates:
                        if not isinstance(_d, str) or not _ISO_DATE_RE.match(_d):
                            _prem_bad_dates.append(f"{_k}: {_d!r}")
            if _prem_bad_dates:
                fail("PREMIERES dates must be YYYY-MM-DD: " + "; ".join(_prem_bad_dates[:6]))
            # P3A item 4: PREMIERES integrity — dates must be cross-referenceable.
            # show: date must be in PERFORMANCES (matching showId+date) AND before PRELIM_FROM.
            # film: key must have at least one SHOWTIMES row at cc-sofia or cc-paradise.
            _prelim = (const("PRELIM_FROM") or {}) if data else {}
            _perfs_set = {(p[0], p[1]) for p in (data.get("PERFORMANCES") or []) if len(p) >= 2}
            _CC_VIDS = {"cc-sofia", "cc-paradise"}
            _cc_film_ids = {r[0] for r in (data.get("SHOWTIMES") or []) if len(r) >= 2 and r[1] in _CC_VIDS}
            _show_th = {s["id"]: s.get("theatre","") for s in (data.get("SHOWS") or [])}
            _prem_integrity_errs = []
            for _pk, _pdates in _prem.items():
                if _pk.startswith("show:"):
                    _sid = _pk[5:]
                    _th = _show_th.get(_sid, "")
                    _prelim_from = _prelim.get(_th, "")
                    for _pd in (_pdates or []):
                        if (_sid, _pd) not in _perfs_set:
                            _prem_integrity_errs.append(
                                f"{_pk} date {_pd} has no matching PERFORMANCES row")
                        if _prelim_from and _pd >= _prelim_from:
                            _prem_integrity_errs.append(
                                f"{_pk} date {_pd} is on or after PRELIM_FROM {_prelim_from} for {_th!r}")
                elif _pk.startswith("film:"):
                    _fid = _pk[5:]
                    if _fid not in _cc_film_ids:
                        _prem_integrity_errs.append(
                            f"{_pk} has no SHOWTIMES row at cc-sofia or cc-paradise")
            if _prem_integrity_errs:
                fail("PREMIERES integrity: " + "; ".join(_prem_integrity_errs[:6]))

# ----------------------------------------- 4c1. FESTMARK — festival marks
# FESTMARK: {"<festival>": {"bg": str, "en": str, "films": [filmId…], "shows": [showId…]}}
# A mark must never name a title the page does not list: every film needs a
# SHOWTIMES row and every show a PERFORMANCES row.
_fm_m = re.search(r"^const FESTMARK\s*=\s*(\{[^\n]*?\});\s*$", js, flags=re.M)
if not _fm_m:
    note("FESTMARK is not declared: festival marks come from the listing titles alone")
else:
    try:
        _fm = json.loads(_fm_m.group(1))
    except Exception as _e:
        fail(f"FESTMARK is not valid JSON: {_e}")
    else:
        _fm_films = {r[0] for r in (data.get("SHOWTIMES") or []) if r}
        _fm_shows = {p[0] for p in (data.get("PERFORMANCES") or []) if p}
        _fm_bad = []
        for _fk, _fv in (_fm.items() if isinstance(_fm, dict) else [("FESTMARK", None)]):
            if not (isinstance(_fv, dict) and isinstance(_fv.get("bg"), str) and _fv["bg"].strip()
                    and isinstance(_fv.get("en", ""), str)
                    and all(isinstance(_fv.get(k_, []), list) for k_ in ("films", "shows"))):
                _fm_bad.append(f"{_fk}: not {{bg, en, films, shows}}")
                continue
            _fm_bad += [f"{_fk}: film {i!r} has no SHOWTIMES row" for i in _fv.get("films", [])
                        if i not in _fm_films]
            _fm_bad += [f"{_fk}: show {i!r} has no PERFORMANCES row" for i in _fv.get("shows", [])
                        if i not in _fm_shows]
        if _fm_bad:
            fail("FESTMARK: " + "; ".join(_fm_bad[:6]))

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

# ------------------------------------------ 4c3. SHOWATTR — per-screening labels
# {"filmId|venueId": {"YYYY-MM-DD HH:MM": "dub,3d"}, …}
_sa_m = re.search(r"^const SHOWATTR\s*=\s*(\{[^\n]*?\});\s*$", js, flags=re.M)
if _sa_m is None:
    note("SHOWATTR is not declared — no per-screening language/format labels")
else:
    try:
        _sa = json.loads(_sa_m.group(1))
    except Exception:
        _sa = None
    if not isinstance(_sa, dict):
        fail("SHOWATTR is not a JSON object")
    else:
        _VALID_TOKENS = frozenset(("dub", "sub", "3d", "imax", "4dx", "screenx", "d-box"))
        _KNOWN_FILMS = {f.get("id") for f in (data.get("FILMS") or [])}
        _KNOWN_VENUES = {c.get("id") for c in (data.get("CINEMAS") or [])}
        # Build a set of (filmId, venueId, date, time) for quick lookup
        _st_lookup = set()
        for _r in (data.get("SHOWTIMES") or []):
            if isinstance(_r, list) and len(_r) >= 4 and isinstance(_r[3], list):
                for _tm in _r[3]:
                    _st_lookup.add((_r[0], _r[1], _r[2], _tm))
        _bad_sa_keys, _bad_sa_vals, _orphan_sa = [], [], []
        for _k, _v in _sa.items():
            _parts = _k.split("|", 1)
            if len(_parts) != 2:
                _bad_sa_keys.append(_k); continue
            _fid, _vid = _parts
            if _fid not in _KNOWN_FILMS:
                _bad_sa_keys.append(_k); continue
            if _vid not in _KNOWN_VENUES:
                _bad_sa_keys.append(_k); continue
            if not isinstance(_v, dict):
                _bad_sa_vals.append(_k); continue
            for _dt, _attr in _v.items():
                if not re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d", _dt):
                    _bad_sa_vals.append(f"{_k}/{_dt}"); continue
                _tokens = set(_attr.split(",")) if _attr else set()
                _bad_tok = _tokens - _VALID_TOKENS
                if _bad_tok:
                    _bad_sa_vals.append(f"{_k}/{_dt}: unknown tokens {_bad_tok}")
                _date, _tm = _dt.split(" ")
                if (_fid, _vid, _date, _tm) not in _st_lookup:
                    _orphan_sa.append(f"{_k}/{_dt}")
        if _bad_sa_keys:
            fail(f"{len(_bad_sa_keys)} SHOWATTR key(s) are not 'filmId|venueId' or reference "
                 f"unknown films/venues: {', '.join(_bad_sa_keys[:4])}")
        if _bad_sa_vals:
            fail(f"{len(_bad_sa_vals)} SHOWATTR value(s) have bad format or unknown tokens: "
                 f"{'; '.join(_bad_sa_vals[:4])}")
        if _orphan_sa:
            fail(f"{len(_orphan_sa)} SHOWATTR entry/entries have no matching SHOWTIMES row: "
                 f"{', '.join(_orphan_sa[:4])}")
        note(f"SHOWATTR: {len(_sa)} film+venue key(s), "
             f"{sum(len(v) for v in _sa.values() if isinstance(v, dict))} timed entr(ies)")

# ------------------------------------------- 4c4. every cinema has a BOOKING entry
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

# ------------------------------------------- 4f. EVENTS_AUTO / EVENT_MEMBERS
# Festivals and limited runs an aggregator announces (fetch_events.py -> events.json
# -> inject_data.py). The UI merges them into EVENTS and links each one's source
# page, so: ISO dates in order, not ended, a valid scope and kind, two colours for
# the card, English text with no Cyrillic, an https URL on an aggregator we read,
# ids that clash with nothing.
# EVENT_MEMBERS (event id -> film/show ids) must name things that exist.
_EV_KINDS = {"festival", "city", "limited", "premiere"}
_EV_SCOPES = {"cinema", "theatre", "both"}
_EV_HOSTS = ("programata.bg", "sofialive.bg")
_ISO = re.compile(r"\d{4}-\d\d-\d\d")
from urllib.parse import urlparse as _up_ev
_ea_m = re.search(r"^const EVENTS_AUTO\s*=\s*(.*?);\s*$", js, flags=re.M)
_events_auto: list = []
if _ea_m is None:
    note("EVENTS_AUTO is not declared: no aggregator-listed events in this build")
else:
    try:
        _events_auto = json.loads(_ea_m.group(1))
    except Exception as _eae:
        fail(f"EVENTS_AUTO is not valid JSON: {_eae}")
    if not isinstance(_events_auto, list) or not all(isinstance(e, dict) for e in _events_auto):
        fail("EVENTS_AUTO is not an array of objects")
        _events_auto = []
    _ev_today = datetime.date.today().isoformat()
    _seed_ev_ids = {e.get("id") for e in (data.get("EVENTS") or []) if isinstance(e, dict)}
    _ev_bad: list[str] = []
    _ev_seen: set = set()
    for _e in _events_auto:
        _eid = _e.get("id")
        _who = str(_eid)[:48]
        if not (isinstance(_eid, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]*", _eid)):
            _ev_bad.append(f"{_who}: id is not a slug")
        elif _eid in _ev_seen:
            _ev_bad.append(f"{_who}: duplicate id")
        elif _eid in _seed_ev_ids:
            _ev_bad.append(f"{_who}: id is also a seed EVENTS id")
        _ev_seen.add(_eid)
        _f, _t = _e.get("from"), _e.get("to")
        _dates_ok = False
        if not (isinstance(_f, str) and isinstance(_t, str) and _ISO.fullmatch(_f) and _ISO.fullmatch(_t)):
            _ev_bad.append(f"{_who}: from/to not ISO dates ({_f!r}, {_t!r})")
        else:
            try:
                datetime.date.fromisoformat(_f); datetime.date.fromisoformat(_t)
                _dates_ok = True
            except ValueError:
                _ev_bad.append(f"{_who}: from/to not real dates ({_f!r}, {_t!r})")
        if _dates_ok and _f > _t:
            _ev_bad.append(f"{_who}: from {_f} is after to {_t}")
        if _dates_ok and _t < _ev_today:
            _ev_bad.append(f"{_who}: ended {_t}")
        if _e.get("scope") not in _EV_SCOPES:
            _ev_bad.append(f"{_who}: scope {_e.get('scope')!r}")
        if _e.get("kind") not in _EV_KINDS:
            _ev_bad.append(f"{_who}: kind {_e.get('kind')!r}")
        if _e.get("auto") is not True:
            _ev_bad.append(f"{_who}: auto is not true")
        if not (isinstance(_e.get("title"), str) and _e["title"].strip()
                and isinstance(_e.get("titleEn"), str) and _e["titleEn"].strip()):
            _ev_bad.append(f"{_who}: title/titleEn missing")
        elif any(re.search(r"[\u0400-\u04FF]", str(_e.get(k) or "")) for k in ("titleEn", "blurbEn")):
            _ev_bad.append(f"{_who}: Cyrillic in titleEn/blurbEn (English mode shows none)")
        _g = _e.get("g")
        if not (isinstance(_g, list) and len(_g) == 2
                and all(isinstance(c, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", c) for c in _g)):
            _ev_bad.append(f"{_who}: g is not two #RRGGBB colours")
        for _u in [_e.get("url")] + list(_e.get("urls") or []):
            _p = _up_ev(_u) if isinstance(_u, str) else None
            _h = (_p.hostname or "").lower() if _p else ""
            if not (_p and _p.scheme == "https"
                    and any(_h == a or _h.endswith("." + a) for a in _EV_HOSTS)):
                _ev_bad.append(f"{_who}: url {str(_u)[:60]!r} is not https on {'/'.join(_EV_HOSTS)}")
    if _ev_bad:
        fail(f"EVENTS_AUTO has {len(_ev_bad)} problem(s): " + "; ".join(_ev_bad[:5]))
_em_m = re.search(r"^const EVENT_MEMBERS\s*=\s*(.*?);\s*$", js, flags=re.M)
if _em_m is not None:
    try:
        _members = json.loads(_em_m.group(1))
    except Exception as _eme:
        fail(f"EVENT_MEMBERS is not valid JSON: {_eme}")
        _members = {}
    if not isinstance(_members, dict):
        fail("EVENT_MEMBERS is not an object")
        _members = {}
    _all_ev = ({e.get("id") for e in (data.get("EVENTS") or []) if isinstance(e, dict)}
               | {e.get("id") for e in _events_auto})
    _items = ({f.get("id") for f in (data.get("FILMS") or [])}
              | {s.get("id") for s in (data.get("SHOWS") or [])})
    _em_bad = [f"{k}: no such event" for k in _members if k not in _all_ev]
    for _k, _v in _members.items():
        if not isinstance(_v, list) or not _v:
            _em_bad.append(f"{_k}: not a non-empty list")
            continue
        _em_bad += [f"{_k}: {i!r} is no film or show" for i in _v if i not in _items]
    if _em_bad:
        fail(f"EVENT_MEMBERS has {len(_em_bad)} problem(s): " + "; ".join(_em_bad[:5]))

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

# ------------------------------------------- 5b2. EVENTART: event key art is ours
# fetch_event_posters.py re-hosts each event's key art under posters/ev-<id> and
# records where it came from in event_posters.json. Every EVENTART entry must
# name an event the page has (EVENTS or EVENTS_AUTO), point at a posters/ev-*
# file that exists and is a picture, and have a record whose source passes
# fetch_event_posters.record_problem(): https, from programata.bg, from the seed
# festival's own site (EVENT_SITES), or from the organiser site its programata
# post links. A missing file is a failure: the card would show a broken image.
_eart_m = re.search(r"^const EVENTART\s*=\s*(\{.*?\});\s*$", js, flags=re.M)
if _eart_m is None:
    note("EVENTART is not declared: no event key art in this build")
else:
    try:
        _eventart = json.loads(_eart_m.group(1))
    except Exception as _eae2:
        fail(f"EVENTART is not valid JSON: {_eae2}")
        _eventart = {}
    if not isinstance(_eventart, dict):
        fail("EVENTART is not an object")
        _eventart = {}
    if _eventart:
        try:
            sys.path.insert(0, str(ROOT / "scripts"))
            import fetch_event_posters as _EPV
            try:
                _ep_side = json.loads((_FILE_ROOT / "event_posters.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                _ep_side = {}
            _ep_recs = (_ep_side.get("events") or {}) if isinstance(_ep_side, dict) else {}
            _ev_ids_all = ({e.get("id") for e in (data.get("EVENTS") or []) if isinstance(e, dict)}
                           | {e.get("id") for e in (_events_auto or []) if isinstance(e, dict)})
            _ea_bad = []
            for _eid, _path in _eventart.items():
                if _eid not in _ev_ids_all:
                    _ea_bad.append(f"{_eid!r}: no event in EVENTS or EVENTS_AUTO has this id")
                    continue
                _rec = _ep_recs.get(_eid)
                if not isinstance(_rec, dict) or _rec.get("file") != _path:
                    _ea_bad.append(f"{_eid!r}: event_posters.json has no record for {str(_path)[:40]!r}")
                    continue
                _why = _EPV.record_problem(_eid, _rec, _FILE_ROOT)
                if _why:
                    _ea_bad.append(f"{_eid!r}: {_why}")
            if _ea_bad:
                fail(f"EVENTART has {len(_ea_bad)} problem(s): " + "; ".join(_ea_bad[:4]))
        except ImportError:
            fail("EVENTART is set but fetch_event_posters.py (its provenance rule) cannot be imported")

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

# ----------------------------------------- 6a. OG / Twitter Card meta in head
# OG tags are required for share previews; their absence silently degrades to a
# bare URL in Viber/WhatsApp/Telegram so the check is hard (fail, not note).
_head_m = re.search(r"<head>(.*?)</head>", src, re.S | re.I)
_head_src = _head_m.group(1) if _head_m else src

for _og_tag in [
    ('property="og:type"',          "og:type"),
    ('property="og:site_name"',     "og:site_name"),
    ('property="og:url"',           "og:url"),
    ('property="og:title"',         "og:title"),
    ('property="og:description"',   "og:description"),
    ('property="og:image"',         "og:image"),
    ('property="og:locale"',        "og:locale"),
    ('name="twitter:card"',         "twitter:card"),
    ('name="twitter:title"',        "twitter:title"),
    ('name="twitter:image"',        "twitter:image"),
]:
    _attr, _label = _og_tag
    if _attr not in _head_src:
        fail(f"OG/Twitter meta missing from <head>: {_label}")

# og:image must be an absolute HTTPS URL
_og_img_m = re.search(r'property="og:image"[^>]*content="([^"]*)"', _head_src)
if _og_img_m:
    _og_img_url = _og_img_m.group(1)
    if not _og_img_url.startswith("https://"):
        fail(f"og:image is not an absolute HTTPS URL: {_og_img_url!r}")
else:
    fail("og:image meta tag not found in <head>")

# ----------------------------------------- 6a2. SITE_URL in page JS matches site_config
# build_index.py injects SITE_URL from site_config.py via a __SITE_URL__ placeholder.
# If the artifact ever hard-codes a URL again, this check catches the mismatch.
try:
    sys.path.insert(0, str(ROOT / "scripts"))
    from site_config import SITE_URL as _CFG_SITE_URL                  # noqa: E402
    _su_m = re.search(r"(?m)^const SITE_URL=\"([^\"]*)\";", js)
    if _su_m is None:
        fail("const SITE_URL not found in page JS — build_index.py may not have run")
    elif _su_m.group(1) != _CFG_SITE_URL:
        fail(f"page JS has SITE_URL={_su_m.group(1)!r} but site_config.SITE_URL={_CFG_SITE_URL!r} "
             "— rebuild with scripts/build_index.py to sync them")
except ImportError:
    note("site_config.py not importable — SITE_URL consistency check skipped")
except Exception as _su_err:
    note(f"SITE_URL check skipped: {_su_err}")

# ----------------------------------------- 6b. PWA icon files exist and are plausible
for _icon_rel, _expected_size in [
    ("icons/apple-touch-icon.png", 180),
    ("icons/icon-192.png",         192),
    ("icons/icon-512.png",         512),
]:
    _icon_path = _FILE_ROOT / _icon_rel
    if not _icon_path.exists():
        fail(f"icon file missing: {_icon_rel}")
    else:
        try:
            from PIL import Image as _PIL_Image
            with _PIL_Image.open(_icon_path) as _img:
                _w, _h = _img.size
                if _w != _expected_size or _h != _expected_size:
                    fail(f"{_icon_rel}: expected {_expected_size}x{_expected_size}, "
                         f"got {_w}x{_h}")
                if _icon_rel == "icons/apple-touch-icon.png" and _img.mode == "RGBA":
                    fail(f"{_icon_rel}: apple-touch-icon must be opaque (no alpha channel)")
        except ImportError:
            note("PIL not available — skipped icon dimension checks")
        except Exception as _ie:
            note(f"icon check {_icon_rel}: {_ie}")

# ----------------------------------------- 6c. per-title share page spot checks
# Pick 3 film ids and 2 show ids from the data and verify each share page has the
# correct og:url/canonical pointing to SITE_URL, and og:image is absolute HTTPS.
# A missing share page is a hard failure once the film/ or show/ directory exists.
try:
    sys.path.insert(0, str(ROOT / "scripts"))
    from site_config import SITE_URL as _SITE_URL

    def _check_share_page_file(kind, item_id):
        _page_path = _FILE_ROOT / kind / item_id / "index.html"
        if not _page_path.exists():
            fail(f"share page missing: {kind}/{item_id}/index.html")
            return
        _page_src = _page_path.read_text(encoding="utf-8")
        _expected_url = f"{_SITE_URL}/{kind}/{item_id}/"
        if _expected_url not in _page_src:
            fail(f"share page {kind}/{item_id}: og:url/canonical not {_expected_url!r}")
        _img_m = re.search(r'og:image[^>]*content="([^"]*)"', _page_src)
        if _img_m:
            if not _img_m.group(1).startswith("https://"):
                fail(f"share page {kind}/{item_id}: og:image not absolute HTTPS")
        else:
            fail(f"share page {kind}/{item_id}: og:image missing")

    # Sample up to 3 films
    _film_dir = _FILE_ROOT / "film"
    if _film_dir.is_dir():
        _film_ids = [d.name for d in sorted(_film_dir.iterdir())
                     if d.is_dir()][:3]
        for _fid in _film_ids:
            _check_share_page_file("film", _fid)
    else:
        note("film/ directory not yet created — share page checks skipped")

    # Sample up to 2 shows
    _show_dir = _FILE_ROOT / "show"
    if _show_dir.is_dir():
        _show_ids = [d.name for d in sorted(_show_dir.iterdir())
                     if d.is_dir()][:2]
        for _sid in _show_ids:
            _check_share_page_file("show", _sid)
    else:
        note("show/ directory not yet created — share page checks skipped")

except ImportError:
    note("site_config.py not importable — share page URL checks skipped")
except Exception as _sp_err:
    note(f"share page spot-check error: {_sp_err}")

# ----------------------------------------- 6d. Maps links — every venue resolves
# venueMapUrl() falls back to name+area+", София" when no maps field is present.
# Verify: every CINEMA and THEATRE entry produces a non-empty URL starting with
# https://www.google.com/maps/.
try:
    from urllib.parse import quote as _quote

    def _venue_map_url(venue):
        if not venue:
            return ""
        m = venue.get("maps")
        if m:
            if m.startswith("http"):
                return m
            return "https://www.google.com/maps/search/?api=1&query=" + _quote(m)
        name = venue.get("name") or ""
        area = venue.get("area") or ""
        query = name + (", " + area if area else "") + ", София"
        return "https://www.google.com/maps/search/?api=1&query=" + _quote(query)

    _MAPS_PREFIX = "https://www.google.com/maps/"
    _map_bad: list[str] = []
    for _v in (data.get("CINEMAS") or []) + (data.get("THEATRES") or []):
        _vid = _v.get("id", "?")
        _url = _venue_map_url(_v)
        if not _url:
            _map_bad.append(f"{_vid}: empty URL")
        elif not _url.startswith(_MAPS_PREFIX):
            _map_bad.append(f"{_vid}: unexpected URL prefix {_url[:60]!r}")
    if _map_bad:
        fail("venue Maps URLs are malformed:\n  " + "\n  ".join(_map_bad))
except Exception as _mp_err:
    note(f"maps link check error: {_mp_err}")

# ----------------------------------------- 6e. legal pages and the cookie notice
# The app links a terms page, a privacy policy and a cookie policy in BG and EN
# (legal/, written by scripts/gen_legal_pages.py). They must match the page they
# describe:
#   - each exists, has its canonical URL under SITE_URL and carries no script;
#   - no page holds a leftover placeholder ({CONTACT_…}, {OPERATOR_…}): the site names
#     no person and gives no email, by the owner's decision of 2026-10-15;
#   - the terms show TMDB's logo (TMDB API terms, section 3; owner input: the file);
#   - the policies carry the page's CONSENT_VERSION as their date;
#   - every localStorage key the page uses is listed in the cookie policy, the page
#     sets no cookie, and the service worker's caches are listed too;
#   - the privacy policy names Google Fonts exactly while a page loads them;
#   - a Content-Security-Policy on the app means the same policy on every legal page
#     (a page with no script may narrow script-src to 'none').
# Owner input that is still missing fails the gate; test runs set
# SOFIA_OWNER_PENDING_OK=1 to list it as a note instead. A page without the cookie
# notice (CONSENT_VERSION) predates the legal pages and skips this section.
LEGAL_DOCS = ("terms", "privacy", "cookies")
LEGAL_PAGES = [f"legal/{d}/" for d in LEGAL_DOCS] + [f"legal/en/{d}/" for d in LEGAL_DOCS]
_OWNER_OK = os.environ.get("SOFIA_OWNER_PENDING_OK") == "1"


def owner_pending(msg):
    (note if _OWNER_OK else fail)("owner input pending: " + msg)


def _csp_of(text):
    """The page's CSP meta as {directive: sources}, hashes and nonces left out; None without one."""
    value = None
    for tag in re.findall(r"<meta\b[^>]*>", text, re.I):
        if re.search(r"""http-equiv\s*=\s*["']?Content-Security-Policy""", tag, re.I):
            m = re.search(r'content\s*=\s*"([^"]*)"', tag) or re.search(r"content\s*=\s*'([^']*)'", tag)
            value = m.group(1) if m else ""
            break
    if value is None:
        return None
    out = {}
    for part in value.split(";"):
        toks = part.split()
        if toks:
            srcs = {t for t in toks[1:] if not re.match(r"'(sha256|sha384|sha512|nonce)-", t)}
            out[toks[0].lower()] = srcs or {"'none'"}
    return out


def legal_checks():
    if not re.search(r'(?m)^const CONSENT_VERSION="', js):
        note("no cookie notice (CONSENT_VERSION) in this page: legal page checks skipped")
        return
    version = re.search(r'(?m)^const CONSENT_VERSION="([^"]*)";', js).group(1)
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from site_config import SITE_URL as site
    except ImportError:
        site = None
    pages = {}
    for rel in LEGAL_PAGES:
        p = _FILE_ROOT / rel / "index.html"
        if not p.exists():
            fail(f"legal page missing: {rel}index.html: run scripts/gen_legal_pages.py")
            continue
        pages[rel] = p.read_text(encoding="utf-8")
    if 'href="legal/terms/"' not in src or "legal/en/" not in src:
        fail("the page does not link the legal pages (footer / noscript)")

    # no placeholder may ship; owner input: TMDB's logo
    for r, t in list(pages.items()) + [("index.html", src)]:
        left = sorted(set(re.findall(r"\{(?:CONTACT|OPERATOR)_[A-Z_]*\}", t)))
        if left:
            fail(f"{r}: placeholder(s) {', '.join(left)} left in the page")
    if not (_FILE_ROOT / "icons" / "tmdb.svg").exists():
        owner_pending("TMDB's logo is missing: TMDB's API terms (section 3) require it: add icons/tmdb.svg "
                      "(themoviedb.org/about/logos-attribution) and run scripts/gen_legal_pages.py")
    else:
        for r in ("legal/terms/", "legal/en/terms/"):
            if r in pages and "icons/tmdb.svg" not in pages[r]:
                fail(f"{r}: icons/tmdb.svg exists but the page does not show it: run scripts/gen_legal_pages.py")

    for r, t in pages.items():
        if site and f'<link rel="canonical" href="{site}/{r}">' not in t:
            fail(f"{r}: canonical URL is not {site}/{r}: run scripts/gen_legal_pages.py")
        if re.search(r"<script\b", t, re.I) or re.search(r"<[a-z][^>]*\son[a-z]+\s*=", t, re.I):
            fail(f"{r}: carries a script or an inline event handler (the legal pages must have none)")
        if r.endswith(("privacy/", "cookies/")) and f'<time datetime="{version}">' not in t:
            fail(f"{r}: its date is not the page's CONSENT_VERSION {version}: run scripts/gen_legal_pages.py")

    # what the page keeps in the browser is what the cookie policy lists
    names = dict(re.findall(r'(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"([^"]+)"', js))
    keys = set()
    for arg in re.findall(r"localStorage\.(?:setItem|getItem|removeItem)\(\s*([^,)]+)", js):
        arg = arg.strip()
        lit = re.fullmatch(r"""(["'])(.*)\1""", arg)
        if lit:
            keys.add(lit.group(2))
        elif arg in names:
            keys.add(names[arg])
        else:
            fail(f"localStorage key {arg!r} cannot be resolved: name it in a constant and list it in the cookie policy")
    if re.search(r"document\.cookie\s*=", js):
        fail("the page sets a cookie, but the cookie policy says it sets none")
    sw = _FILE_ROOT / "sw.js"
    cache = re.search(r'CACHE_VERSION\s*=\s*"([a-z-]+)v\d+"', sw.read_text(encoding="utf-8")) if sw.exists() else None
    for r in ("legal/cookies/", "legal/en/cookies/"):
        t = pages.get(r, "")
        for k in sorted(keys):
            if f"<code>{k}</code>" not in t:
                fail(f"{r}: localStorage key {k!r} is used by the page but not listed: describe it in "
                     "scripts/gen_legal_pages.py and run it")
        for api in ("sessionStorage", "indexedDB"):
            if api in js and api not in t:
                fail(f"{r}: the page uses {api}, which the cookie policy does not list")
        if cache and cache.group(1) + "v" not in t:
            fail(f"{r}: the service worker's caches ({cache.group(1)}v…) are not listed")

    # the privacy policy names Google Fonts exactly while a page loads them
    google = any(re.search(r"fonts\.(googleapis|gstatic)\.com", t) for t in [src, *pages.values()])
    for r in ("legal/privacy/", "legal/en/privacy/"):
        if r in pages and ("Google Fonts" in pages[r]) != google:
            fail(f"{r}: says {'nothing about' if google else 'that it uses'} Google Fonts, but the pages "
                 f"{'load' if google else 'do not load'} them: run scripts/gen_legal_pages.py")

    # the same Content-Security-Policy as the app
    main_csp = _csp_of(src)
    for r, t in pages.items():
        csp = _csp_of(t)
        if main_csp is None and csp is None:
            continue
        if (main_csp is None) != (csp is None):
            fail(f"{r}: Content-Security-Policy {'missing' if csp is None else 'present'} while the app "
                 f"{'has one' if csp is None else 'has none'}: the legal pages must carry the same policy")
            continue
        diff = sorted(d for d in set(main_csp) | set(csp)
                      if main_csp.get(d) != csp.get(d)
                      and not (d == "script-src" and csp.get(d) == {"'none'"}))
        if diff:
            fail(f"{r}: Content-Security-Policy differs from the app's in {', '.join(diff)}")


try:
    legal_checks()
except Exception as _lg_err:
    fail(f"legal page checks crashed: {_lg_err.__class__.__name__}: {_lg_err}")


# ----------------------------------------------------- 7. a real click, live (gate)
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
            # a first visit: the cookie notice is up, and "Разбрах" closes it and keeps the choice
            if re.search(r'(?m)^const CONSENT_VERSION="', js):
                if not page.is_visible("#sg-consent [data-consent-ok]"):
                    fail(f"[{tz}] the cookie notice did not show on a first visit")
                else:
                    page.click("#sg-consent [data-consent-ok]")
                    page.wait_for_timeout(200)
                    if (page.is_visible("#sg-consent")
                            or not page.evaluate("!!localStorage.getItem('sofia-consent')")):
                        fail(f"[{tz}] the cookie notice's button did not close it and keep the choice")
            cards = page.query_selector_all("[data-film]")
            if not cards:
                fail(f"[{tz}] no film cards rendered")
            else:
                cards[0].click()
                page.wait_for_timeout(700)
                if not page.query_selector(".sheet"):
                    fail(f"[{tz}] clicking a film did not open its detail sheet"
                         + (" — " + errs[0] if errs else ""))
                elif not (page.query_selector_all(".sheet a.time") or
                          page.query_selector_all(".sheet .p3e-pill[data-sspill]") or
                          page.query_selector_all(".sheet .p3e-row")):
                    # P3E redesign: showtimes are now .p3e-pill buttons (not a.time links)
                    fail(f"[{tz}] the film sheet opened with no ticket links or showtime pills")
                # Close the sheet before testing the theatre side
                scrim = page.query_selector("#scrim")
                if scrim and not page.evaluate("document.getElementById('scrim').hidden"):
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(400)
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
