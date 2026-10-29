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
# Cine Grand's per-cinema slug (official_sources.CG_CINEMAS): a film page in VLINKS
# must carry its own venue's cinema_slug.
CG_SLUGS = {"cg-ring": "софия-ринг-мол", "cg-park": "парк-център-софия"}
# A festival row (official_festivals.py) links the festival's own film page or
# its ticket shop, at any venue: cinelibri.com and kinomania.bg, epaygo.bg (both
# festivals) and embed.urboapp.com (CineLibri at Френски институт) — checked live
# 2026-10-09. Same list as official_festivals.TICKET_HOSTS.
try:
    sys.path.insert(0, str(ROOT / "scripts"))
    from official_festivals import TICKET_HOSTS as FESTIVAL_TICKET_HOSTS   # noqa: E402
except BaseException:                                    # bs4 missing: same list
    FESTIVAL_TICKET_HOSTS = ("cinelibri.com", "kinomania.bg", "epaygo.bg", "urboapp.com")
# epaygo.bg's own place name -> app venue (official_festivals.EPAYGO_PLACES): the gate
# reads the place FESTROWS stored, never the network
try:
    from official_festivals import epaygo_place_venue as _epaygo_place_venue   # noqa: E402
except BaseException:                                    # bs4 missing: the same rule
    def _epaygo_place_venue(place):
        k = re.sub(r"\s+", " ", re.sub(r"[„“”\"«»‘’'`]", "", (place or "").lower())).strip(" ,.-")
        return {"кино euro cinema": "euro-cinema", "euro cinema": "euro-cinema", "евросинема": "euro-cinema",
                "евро синема": "euro-cinema", "културен център g8": "g8", "културен център g8 зала 1": "g8",
                "културен център g8 зала 2": "g8", "одеон филмотечно кино": "odeon",
                "филмотечно кино одеон": "odeon", "кино одеон": "odeon"}.get(k)


# whether an epaygo.bg page's own title (FESTROWS' 10th field, stored at scrape time) names the
# row's film (film_identity.same_film_title: stdlib only; skeptic, 2026-10-29: CineLibri's two
# 4 Nov 19:45 films at G8 and Euro Cinema had their epaygo links crossed)
try:
    from film_identity import same_film_title as _same_film_title   # noqa: E402
except BaseException:                                    # never silently pass: the check fails
    _same_film_title = None


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
    from urllib.parse import urlparse as _urlparse, parse_qs
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
        bad_cg_cinema = []       # a Cine Grand film page opened for the OTHER cinema
        cg_no_slug = []          # …or for none (the site then shows Парк Център)

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
            # Cine Grand: cinegrand.bg/m/<film> without cinema_slug shows Парк Център
            # София for both Sofia cinemas; a row's film page must name ITS cinema
            # (?cinema_slug=софия-ринг-мол / парк-център-софия, the site's own links).
            if vid in CG_SLUGS and (host == "cinegrand.bg" or host.endswith(".cinegrand.bg")) \
                    and parsed.path.startswith("/m/"):
                _slug = parse_qs(parsed.query).get("cinema_slug", [""])[0]
                if not _slug:
                    cg_no_slug.append(f"{fid}/{vid}")
                elif _slug != CG_SLUGS[vid]:
                    bad_cg_cinema.append(f"{fid}/{vid} -> cinema_slug={_slug}")
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
        if bad_cg_cinema:
            fail(f"VLINKS has {len(bad_cg_cinema)} Cine Grand film page(s) opened for the other "
                 "cinema: " + "; ".join(bad_cg_cinema[:3]))
        if cg_no_slug:
            note(f"VLINKS: {len(cg_no_slug)} Cine Grand film page(s) without cinema_slug (they open "
                 "Парк Център's screenings; the next refresh adds the venue's slug): "
                 + ", ".join(cg_no_slug[:5]))
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

# ---------------------------------------- 4c2a. BOXONLY: performances sold only at the box office
# [[showId, "YYYY-MM-DD", "HH:MM", alt, page], …] (scrape_programs.theatre_box_office):
# the time panel says "Билети само на касата" instead of "Купи билет". Every entry
# must name a performance PERFORMANCES lists, once; alt and page are null or http(s).
# The live click test (7) renders each one and checks no buy button appears.
_bo = re.search(r"^const BOXONLY\s*=\s*(.*?);\s*$", js, flags=re.M)
boxonly_data = []
if _bo is None:
    note("BOXONLY is not declared — no performance is marked box office only")
else:
    try:
        boxonly_data = json.loads(_bo.group(1))
    except Exception:
        boxonly_data = None
    if not isinstance(boxonly_data, list):
        fail("BOXONLY is not a JSON array of [showId, date, time, alt, page]")
        boxonly_data = []
    else:
        _url_ok = lambda u: u is None or (isinstance(u, str) and re.match(r"https?://[^\s\"<>]+$", u))
        _bad_bo = [x for x in boxonly_data
                   if not (isinstance(x, list) and len(x) == 5 and all(isinstance(v, str) for v in x[:3])
                           and re.fullmatch(r"\d{4}-\d\d-\d\d", x[1]) and re.fullmatch(r"\d\d:\d\d", x[2])
                           and _url_ok(x[3]) and _url_ok(x[4]))]
        if _bad_bo:
            fail(f"{len(_bad_bo)} BOXONLY entr(ies) are not [showId, date, time, alt|null, page|null], "
                 f"e.g. {str(_bad_bo[0])[:100]}")
        _perf3 = {tuple(p[:3]) for p in (data["PERFORMANCES"] or []) if isinstance(p, list) and len(p) >= 3}
        _good_bo = [x for x in boxonly_data if x not in _bad_bo]
        _lost_bo = [x for x in _good_bo if tuple(x[:3]) not in _perf3]
        if _lost_bo:
            fail(f"{len(_lost_bo)} BOXONLY entr(ies) name no PERFORMANCES row, "
                 f"e.g. {', '.join('/'.join(x[:3]) for x in _lost_bo[:3])}")
        _dup_bo = len(_good_bo) - len({tuple(x[:3]) for x in _good_bo})
        if _dup_bo:
            fail(f"{_dup_bo} duplicate BOXONLY entr(ies) (same show, date and time)")
        if boxonly_data and not re.search(r"\bfunction boxOnly\(", js):
            fail("BOXONLY has entries but the app has no boxOnly() — they would still offer \"Купи билет\"")
        note(f"BOXONLY: {len(boxonly_data)} performance(s) sold only at the box office")

# ---------------------------------------- 4c2a2. FESTROWS: festival screenings at box-office-only cinemas
# [[filmId, venueId, date, time, festival, url|null, sale|null], …] (scrape_programs.box_office_festival_rows,
# festival_sale_states: sale is "onsale", "soldout" or "notyet"; a build before it has 6 fields):
# Одеон and G8 (BOOKING inPerson) sell their own programme only at the box office, the
# festivals there sell online. Every entry names a SHOWTIMES row at such a cinema, once;
# url is null or a festival / ticket-shop page. A box-office cinema's own BOOKING url is
# its plain page, never a ticket seller's or a "#buy" anchor (the stub's site line and
# the calendar link use it).
_SALE_HOSTS = ("cinelibri.com", "kinomania.bg", "epaygo.bg", "urboapp.com")
_SELLER_HOSTS = _SALE_HOSTS + ("pricelesstickets.bg", "eventim.bg", "ticketportal.bg", "bilet.bg", "ticketstation.bg")


def _host_of(u):
    m = re.match(r"https?://([^/:?#]+)", u or "")
    h = (m.group(1).lower() if m else "")
    return h[4:] if h.startswith("www.") else h


def _is_seller(u):
    h = _host_of(u)
    return bool(h) and any(h == x or h.endswith("." + x) for x in _SELLER_HOSTS)


_box_cin = sorted(v for v, b in (data["BOOKING"] or {}).items() if isinstance(b, dict) and b.get("inPerson"))
# festival cinemas (BOOKING festival: Кино Euro Cinema, owner 2026-10-29): the app lists only
# the festivals' screenings there, sold online; FESTROWS covers them too. Such a venue is new
# to most visitors: its card must say where it is (address in both languages, a map place).
_fest_cin = sorted(v for v, b in (data["BOOKING"] or {}).items() if isinstance(b, dict) and b.get("festival"))
_cin_by = {c.get("id"): c for c in (data["CINEMAS"] or []) if isinstance(c, dict)}
for _v in _fest_cin:
    _c = _cin_by.get(_v) or {}
    if not _c:
        fail(f"BOOKING[{_v}] is a festival cinema but CINEMAS has no {_v}")
        continue
    if (data["BOOKING"][_v] or {}).get("inPerson"):
        fail(f"BOOKING[{_v}] is both a festival cinema and box office only")
    if not (str(_c.get("area") or "").strip() and str(_c.get("areaEn") or "").strip()):
        fail(f"cinema {_v} has no address (area / areaEn)")
    _mp = str(_c.get("maps") or "")
    if not (re.match(r"https://www\.google\.com/maps/", _mp) or (_mp and not _mp.startswith("http"))):
        fail(f"cinema {_v} has no map place (maps): {_mp[:80]!r}")
for _v in _box_cin:
    _u = (data["BOOKING"][_v] or {}).get("url") or ""
    if _is_seller(_u) or "#buy" in _u.lower():
        fail(f"BOOKING[{_v}] sells only at its box office but its url is a ticket page: {_u}")
_fr = re.search(r"^const FESTROWS\s*=\s*(.*?);\s*$", js, flags=re.M)
if _fr is None:
    note("FESTROWS is not declared — festival screenings at box-office cinemas count by film id / epaygo link only")
else:
    try:
        _fr_data = json.loads(_fr.group(1))
    except Exception:
        _fr_data = None
    if not isinstance(_fr_data, list):
        fail("FESTROWS is not a JSON array of [filmId, venueId, date, time, festival, url]")
    else:
        _st4 = {(r[0], r[1], r[2], t) for r in (data["SHOWTIMES"] or []) if isinstance(r, list) and len(r) >= 4
                for t in (r[3] or [])}
        _bad_fr = [x for x in _fr_data
                   if not (isinstance(x, list) and len(x) in (6, 7, 8, 9, 10, 11) and all(isinstance(v, str) for v in x[:5])
                           and (len(x) == 6 or x[6] in (None, "onsale", "soldout", "notyet"))
                           and (len(x) < 8 or x[7] is None or (isinstance(x[7], str) and 0 < len(x[7]) < 160))
                           and (len(x) < 9 or x[8] is None or (isinstance(x[8], str)
                                                               and re.fullmatch(r"\d{4}-\d{2}-\d{2}", x[8])))
                           and (len(x) < 10 or x[9] is None or (isinstance(x[9], str) and 0 < len(x[9]) <= 150))
                           and (len(x) < 11 or x[10] is None or (isinstance(x[10], str)
                                                                 and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", x[10])))
                           and (x[5] is None or (isinstance(x[5], str) and re.match(r"https?://[^\s\"<>]+$", x[5])
                                                 and any(_host_of(x[5]) == h or _host_of(x[5]).endswith("." + h)
                                                         for h in _SALE_HOSTS))))]
        if _bad_fr:
            fail(f"{len(_bad_fr)} FESTROWS entr(ies) are not [filmId, venueId, date, time, festival, festival/ticket-shop url|null, "
                 f"onsale/soldout/notyet|null, epaygo place|null, day the state was read|null, epaygo title|null, "
                 f"seller's start time|null], "
                 f"e.g. {str(_bad_fr[0])[:120]}")
        _ok_fr = [x for x in _fr_data if x not in _bad_fr]
        _lost_fr = [x for x in _ok_fr if tuple(x[:4]) not in _st4]
        if _lost_fr:
            fail(f"{len(_lost_fr)} FESTROWS entr(ies) name no SHOWTIMES row, e.g. {'/'.join(_lost_fr[0][:4])}")
        _off_fr = [x for x in _ok_fr if x[1] not in _box_cin and x[1] not in _fest_cin]
        if _off_fr:
            fail(f"{len(_off_fr)} FESTROWS entr(ies) at a cinema that sells online anyway, e.g. {'/'.join(_off_fr[0][:4])}")
        if len(_ok_fr) != len({tuple(x[:4]) for x in _ok_fr}):
            fail("duplicate FESTROWS entries (same film, cinema, date and time)")
        # the seller's time is shown (owner, 2026-10-29: Ехото между нас at Одеон 29 Oct, venue
        # 20:00, CineLibri and epaygo 20:30 -> 20:30): no row whose verified seller start time
        # (11th field, scrape_programs.festival_seller_times) differs from its shown time
        _seller_off = [x for x in _ok_fr if len(x) > 10 and x[10] and x[10] != x[3]]
        if _seller_off:
            fail(f"{len(_seller_off)} FESTROWS entr(ies) show the venue's time, not the ticket seller's, "
                 f"e.g. {'/'.join(_seller_off[0][:4])} (seller {_seller_off[0][10]})")
        _seller_fixed = [x for x in _ok_fr if len(x) > 10 and x[10]]
        if _seller_fixed:
            note(f"FESTROWS: {len(_seller_fixed)} screening(s) with the seller's verified start time")
        # the venue epaygo.bg names decides (owner, 2026-10-29: G8 and Euro Cinema are two
        # cinemas; CineLibri runs both at the same hour): no row filed under one of them
        # whose epaygo page places it at the other, and no VLINKS entry of one of them that
        # links an epaygo event stored as the other's. The place FESTROWS stored, no network.
        _wrong_place = [x for x in _ok_fr if len(x) > 7 and x[7]
                        and _epaygo_place_venue(x[7]) not in (None, x[1])
                        and _epaygo_place_venue(x[7]) in set(_box_cin) | set(_fest_cin)]
        if _wrong_place:
            fail(f"{len(_wrong_place)} FESTROWS entr(ies) filed under a cinema their epaygo.bg page does not name, "
                 f"e.g. {'/'.join(_wrong_place[0][:4])} at {_wrong_place[0][7]!r}")
        _place_of = {}
        for x in _ok_fr:
            if len(x) > 7 and x[7] and isinstance(x[5], str) and _host_of(x[5]) == "epaygo.bg":
                _place_of.setdefault(x[5].rstrip("/"), _epaygo_place_venue(x[7]))
        _wrong_vl = [x for x in (vlinks_data if isinstance(vlinks_data, list) else [])
                     if isinstance(x, list) and len(x) >= 3 and isinstance(x[2], str)
                     and _place_of.get(x[2].rstrip("/")) not in (None, x[1])]
        if _wrong_vl:
            fail(f"{len(_wrong_vl)} VLINKS entr(ies) link an epaygo.bg event at another cinema, "
                 f"e.g. {'/'.join(_wrong_vl[0][:2])} -> {_wrong_vl[0][2]}")
        # an epaygo.bg link sells the row's film: the page's own title, stored at scrape time
        # (10th field), names it — film by film, never by the slot (two films can share one).
        # No network: the stored title against FILMS' bg / en / original title.
        _ftitles = {f.get("id"): [t for t in (f.get("bg"), f.get("en"), f.get("originalTitle")) if isinstance(t, str) and t]
                    for f in (data["FILMS"] or []) if isinstance(f, dict)}
        _ep_ev = re.compile(r"^https://epaygo\.bg/\d+/?$")
        _no_title = [x for x in _ok_fr if isinstance(x[5], str) and _ep_ev.match(x[5]) and not (len(x) > 9 and x[9])]
        if _no_title:
            fail(f"{len(_no_title)} FESTROWS entr(ies) link an epaygo.bg event without its stored title (never checked "
                 f"against the film), e.g. {'/'.join(_no_title[0][:4])} -> {_no_title[0][5]}")
        if _same_film_title is None:
            fail("film_identity.same_film_title is not importable: FESTROWS' epaygo titles cannot be checked")
        else:
            _wrong_film = [x for x in _ok_fr if isinstance(x[5], str) and _ep_ev.match(x[5]) and len(x) > 9 and x[9]
                           and not _same_film_title(x[9], _ftitles.get(x[0]) or [])]
            if _wrong_film:
                fail(f"{len(_wrong_film)} FESTROWS entr(ies) link an epaygo.bg event that sells another film, "
                     f"e.g. {'/'.join(_wrong_film[0][:4])} -> {_wrong_film[0][5]} ({_wrong_film[0][9]!r})")
            _title_of = {x[5].rstrip("/"): x[9] for x in _ok_fr if isinstance(x[5], str) and _ep_ev.match(x[5])
                         and len(x) > 9 and x[9]}
            _wrong_vf = [x for x in (vlinks_data if isinstance(vlinks_data, list) else [])
                         if isinstance(x, list) and len(x) >= 3 and isinstance(x[2], str)
                         and x[2].rstrip("/") in _title_of
                         and not _same_film_title(_title_of[x[2].rstrip("/")], _ftitles.get(x[0]) or [])]
            if _wrong_vf:
                fail(f"{len(_wrong_vf)} VLINKS entr(ies) link an epaygo.bg event FESTROWS stores for another film, "
                     f"e.g. {'/'.join(_wrong_vf[0][:2])} -> {_wrong_vf[0][2]}")
        if _fr_data and not re.search(r"\bfunction festSaleUrl\(", js):
            fail("FESTROWS has entries but the app has no festSaleUrl() — they would show the box-office stub")
        _shut_fr = [x for x in _ok_fr if len(x) > 6 and x[6] in ("soldout", "notyet")]
        if _shut_fr and not re.search(r"\bfunction festSaleState\(", js):
            fail(f"{len(_shut_fr)} FESTROWS entr(ies) are sold out / not on sale yet but the app has no festSaleState() "
                 f"(they would still offer \"Купи билет\")")
        if _shut_fr:
            note(f"FESTROWS: {sum(1 for x in _shut_fr if x[6] == 'soldout')} sold out, "
                 f"{sum(1 for x in _shut_fr if x[6] == 'notyet')} not on sale yet")
        note(f"FESTROWS: {len(_fr_data)} festival screening(s) at box-office-only / festival cinemas")

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

# ------------------------------------------- 5a3. trailers (TMDBART[id].v)
# A trailer is a YouTube key the page puts into a youtube-nocookie.com iframe URL when
# the visitor presses play: it must be an 11-character YouTube id (nothing else can
# reach that URL), its language bg or en, its type Trailer or Teaser, and the film's
# year must agree with TMDB's within 1 (a looser match can be another film). The
# iframe host must be the one frame-src allows, and nothing from YouTube or Google may
# be in the page itself (no thumbnail, no script): it loads only on the tap.
_tart = _const_obj_tr("TMDBART")
if isinstance(_tart, dict):
    _fy = {f.get("id"): f.get("year") for f in (data.get("FILMS") or []) if isinstance(f, dict)}
    _tr_bad, _tr_n = [], 0
    for _k, _r in _tart.items():
        _v = (_r or {}).get("v") if isinstance(_r, dict) else None
        if _v is None:
            continue
        _tr_n += 1
        if not (isinstance(_v, dict) and isinstance(_v.get("k"), str) and re.fullmatch(r"[A-Za-z0-9_-]{11}", _v["k"])
                and _v.get("l") in ("bg", "en") and _v.get("t") in ("Trailer", "Teaser")):
            _tr_bad.append(f"{_k}: {str(_v)[:40]}")
            continue
        _ty = str(_r.get("y") or "")
        try:
            _fyv = int(_fy.get(_k)) if _fy.get(_k) else None
        except (TypeError, ValueError):
            _fyv = None
        if not _ty.isdigit() or (_fyv and abs(int(_ty) - _fyv) > 1):
            _tr_bad.append(f"{_k}: TMDB year {_ty or '?'} vs film year {_fy.get(_k)}")
    if _tr_bad:
        fail(f"TMDBART has {len(_tr_bad)} trailer(s) that must not ship: " + "; ".join(_tr_bad[:4]))
    # TMDB's Bulgarian names of people (nb: Latin name -> Bulgarian): Cyrillic only, no
    # Latin letter and no letter Bulgarian does not use (a Russian/Ukrainian form)
    _nb_bad = [f"{_k}: {_n!r}->{_b!r}" for _k, _r in _tart.items() if isinstance(_r, dict) and "nb" in _r
               for _n, _b in ((_r.get("nb") or {}).items() if isinstance(_r.get("nb"), dict) else [("nb", _r.get("nb"))])
               if not (isinstance(_n, str) and isinstance(_b, str) and re.search(r"[Ѐ-ӿ]", _b)
                       and not re.search(r"[A-Za-zыэёіїєґЫЭЁІЇЄҐ]", _b))]
    if _nb_bad:
        fail(f"TMDBART has {len(_nb_bad)} Bulgarian name(s) that are not Bulgarian Cyrillic: " + "; ".join(_nb_bad[:4]))
    if _tr_n:
        note(f"{_tr_n} film(s) carry a trailer (TMDBART v)")

# ------------------------------------------- 5a4. a TMDB match is the venue's film
# 2026-10-28: „ЕВЕРЕСТ: ОТ ДРУГАТА СТРАНА“ (Влайкова, a 2026 documentary) shipped
# with The Departed's poster and IMDb 8.5; twelve more same-title matches (Обич ->
# Loving, Диаманти -> a Bond film, Звярът -> Beast 2022…) had shipped for weeks.
# A shipped match (TMDBART) fails the gate when
#   - the venue's own facts contradict it (fetch_tmdb.contradiction: year beyond
#     MAX_YEAR_GAP, runtime, documentary vs fiction, director, IMDb id);
#   - (reported only, never failed: a re-release looks the same) the film premieres
#     now (PREMIERES) but TMDB's film is more than three years older, and nothing
#     the venue prints dates the film;
#   - it was found on part of a Bulgarian title (tmdb_films.json via "bg-BG": the
#     query lost words such as "ЕВЕРЕСТ:") that TMDB's own title does not carry,
#     with no venue year (±1) to confirm it.
_tart4 = _tart if isinstance(_tart, dict) else (_const_obj_tr("TMDBART") or {})
if _tart4:
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import fetch_tmdb as _FT                                   # noqa: E402
        from film_identity import title_year as _title_year, strip_prefix as _strip_prefix  # noqa: E402
    except BaseException as _e:                                    # pragma: no cover
        _FT = None
        note(f"5a4 skipped: cannot import fetch_tmdb ({_e})")
    if _FT:
        _films4 = {f.get("id"): f for f in (data.get("FILMS") or []) if isinstance(f, dict)}
        _fi4 = _const_obj_tr("FILMINFO") or {}
        _prem4 = _const_obj_tr("PREMIERES") or {}
        _urls4 = {}
        for _cn in ("LINKS", "VLINKS"):
            for _row in (const(_cn) or []):
                if isinstance(_row, list) and len(_row) >= 2 and isinstance(_row[-1], str):
                    _urls4.setdefault(_row[0], []).append(_row[-1])
        try:
            _side4 = json.loads((_FILE_ROOT / "tmdb_films.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _side4 = {}
        _id_bad4, _id_warn4 = [], []
        for _k, _r in _tart4.items():
            if not isinstance(_r, dict) or _k not in _films4:
                continue
            _f, _fi = _films4[_k], (_fi4.get(_k) or {})
            _sd = _side4.get(_k) if isinstance(_side4.get(_k), dict) else {}
            _cand = {"tmdb_year": _r.get("y"), "runtime": _r.get("rn"), "genres": _r.get("g"),
                     "dir": _r.get("dir"), "nb": _r.get("nb"), "imdb_id": _sd.get("imdb_id")}
            _facts = _FT.venue_facts(_f, _fi, _urls4.get(_k))
            _why = _FT.contradiction(_facts, _cand)
            _own_y = _facts.get("year") or _title_year(_f.get("bg") or "")
            _ty = int(_r["y"]) if str(_r.get("y") or "").isdigit() else None
            _pd = [d for d in (_prem4.get("film:" + _k) or []) if isinstance(d, str) and d[:4].isdigit()]
            # A premiere date with an old TMDB film is only a hint: a re-release
            # (Cinema City dates one as a premiere and may print no year) is the old
            # film, and fetch_tmdb cannot apply this rule, so it would block every
            # refresh until someone blocklists a correct match. It is reported, not failed.
            if not _why and _ty and _pd and _ty < int(min(_pd)[:4]) - 3 and not (
                    _own_y and abs(int(_own_y) - _ty) <= _FT.MAX_YEAR_GAP):
                _id_warn4.append(f"{_k} -> {_r.get('en') or '?'} ({_ty}): premieres {min(_pd)}, "
                                 "and the venue prints no year")
            if not _why and _sd.get("via") == "bg-BG" and _sd.get("q"):
                _lost = _FT.stripped_words(_strip_prefix(_f.get("bg") or ""), _sd["q"])
                if _lost and not _FT.keeps_dropped({"title": _sd.get("tbg"), "original_title": _sd.get("original_title")},
                                                   _lost) and not (_own_y and _ty and abs(int(_own_y) - _ty) <= 1):
                    _why = (f"found as {_sd['q']!r}, part of the title: TMDB's title has no "
                            f"{', '.join(sorted(_lost))}")
            if _why:
                _id_bad4.append(f"{_k} -> {_r.get('en') or '?'} ({_r.get('y') or '?'}): {_why}")
        if _id_warn4:
            note(f"5a4 TMDB identity: {len(_id_warn4)} premiering film(s) matched to a much older TMDB film "
                 "(a re-release, or another film: check, then tmdb_blocklist.json): " + "; ".join(_id_warn4[:5]))
        if _id_bad4:
            fail(f"5a4 TMDB identity: {len(_id_bad4)} match(es) the venue contradicts "
                 "(unmatch them: tmdb_blocklist.json): " + "; ".join(_id_bad4[:5]))
if "youtube-nocookie.com/embed/" in js:
    if not re.search(r'"https://www\.youtube-nocookie\.com/embed/"', js):
        fail("the trailer iframe must load from https://www.youtube-nocookie.com/embed/ (the CSP's frame-src)")
for _yt in ("youtube.com/iframe_api", "ytimg.com", "img.youtube.com", "www.youtube.com/embed"):
    if _yt in src:
        fail(f"the page references {_yt}: nothing from YouTube or Google may load before the play button")

# ------------------------------------------- 5a. ids are ASCII slugs; local poster paths load
# Every id is a lower-case ASCII slug. A look-alike Cyrillic letter in an id
# ("skaperniкat", 2026-10-22) passed every Python check (\w is Unicode there) while
# the browser's safeImg (ASCII \w, no `u` flag) refused its poster path, so the
# play silently showed generated art. Every local poster path must also pass the
# JS rule itself and exist on disk.
ID_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# The browser's rule for a plain path in safeImg() (src/sofia-screen.artifact.html):
# posterpolicy.JS_SAFEIMG_PATH is its exact source text (test_verify.py checks it),
# compiled with re.ASCII because JS \w without the `u` flag is ASCII.
sys.path.insert(0, str(ROOT / "scripts"))
from posterpolicy import SAFEIMG_PATH_RE                     # noqa: E402


def _const_any(name):
    m = re.search(r"^const %s\s*=\s*(.*?);\s*$" % name, js, flags=re.M)
    try:
        return json.loads(m.group(1)) if m else None
    except Exception:
        return None


_id_bad = []
for _n in ("FILMS", "SHOWS", "EVENTS", "CINEMAS", "THEATRES"):
    for _r in (data.get(_n) or []):
        _i = _r.get("id") if isinstance(_r, dict) else None
        if not (isinstance(_i, str) and ID_SLUG_RE.match(_i)):
            _id_bad.append(f"{_n} id {_i!r}")
_maps = {}
for _n in ("SHOWART", "POSTERS", "TMDBART", "EVENTART"):
    _maps[_n] = _const_any(_n)
    _bad_keys = [k for k in (_maps[_n] or {}) if not ID_SLUG_RE.match(k)]
    _id_bad += [f"{_n} key {k!r}" for k in _bad_keys]
for _n in ("LINKS", "PERFORMANCES"):
    _rows = data.get(_n) if _n in data else _const_any(_n)
    _bad_keys = sorted({r[0] for r in (_rows or []) if isinstance(r, list) and r
                        and not (isinstance(r[0], str) and ID_SLUG_RE.match(r[0]))}, key=str)
    _id_bad += [f"{_n} row id {k!r}" for k in _bad_keys]
if _id_bad:
    fail(f"5a ids: {len(_id_bad)} id(s) are not lower-case ASCII slugs (the browser refuses "
         "their poster paths): " + "; ".join(_id_bad[:6]))
_path_bad = []
for _n in ("SHOWART", "POSTERS", "EVENTART"):
    for _k, _v in (_maps.get(_n) or {}).items():
        if not isinstance(_v, str) or re.match(r"^(?:https?:|data:)", _v, re.I):
            continue
        if not SAFEIMG_PATH_RE.match(_v) or _v.startswith("//"):
            _path_bad.append(f"{_n}[{_k!r}] = {_v[:40]!r} fails the browser's safeImg rule")
        elif not (_FILE_ROOT / _v).is_file():
            _path_bad.append(f"{_n}[{_k!r}] = {_v[:40]!r} is not on disk")
if _path_bad:
    fail(f"5a poster paths: {len(_path_bad)} problem(s): " + "; ".join(_path_bad[:6]))

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

# --------------------------------------------- 5c2. poster coverage of upcoming shows
# Counted exactly as the browser shows a poster (realPoster() + safeImg(), mirrored
# by posterpolicy.browser_poster). A note, not a failure, on a normal day; a
# failure when it falls below 90 % or by 3+ points against the previous
# build_report.json — that is a poster source that died, not churn. The numbers go
# out as one "POSTER_COVERAGE {json}" line, which refresh_all.py keeps in
# build_report.json["posters"]. A page with fewer than COVERAGE_MIN_SHOWS upcoming
# shows (a test fixture; a collapsed scrape is caught by the floors) is only noted.
COVERAGE_MIN_SHOWS = 100
try:
    from posterpolicy import poster_coverage, LOGO_MD5                 # noqa: E402
    import hashlib as _hl
    _snap = data.get("SNAPSHOT") or {}
    _to = ((_snap.get("window") or {}).get("to")) or "9999-12-31"
    try:
        import listing_window as _LW
        _from = _LW.sofia_today().isoformat()
    except Exception:
        _from = datetime.date.today().isoformat()
    _up = {r[0] for r in (data.get("PERFORMANCES") or [])
           if isinstance(r, list) and len(r) > 1 and _from <= str(r[1]) <= _to}
    _pmaps = {n: (_const_any(n) or {}) for n in ("SHOWALIAS", "EVENTART", "POSTERS", "TMDBART", "SHOWART")}
    _cov = poster_coverage(data.get("SHOWS") or [], _up, _pmaps)
    print("POSTER_COVERAGE " + json.dumps(_cov, ensure_ascii=False, sort_keys=True))
    _pct = 100.0 * _cov["with"] / _cov["upcoming"] if _cov["upcoming"] else 100.0
    _cov_msg = (f"poster coverage: {_cov['with']}/{_cov['upcoming']} upcoming shows ({_pct:.1f} %)"
                + (f"; missing: {', '.join(_cov['missing'][:12])}" if _cov["missing"] else ""))
    try:
        _prev_cov = (json.loads((_FILE_ROOT / "build_report.json").read_text(encoding="utf-8"))
                     .get("posters") or {})
    except (OSError, ValueError, AttributeError):
        _prev_cov = {}
    _prev_pct = (100.0 * _prev_cov["with"] / _prev_cov["upcoming"]
                 if isinstance(_prev_cov, dict) and _prev_cov.get("upcoming") else None)
    if _cov["upcoming"] < COVERAGE_MIN_SHOWS:
        note(_cov_msg + f" (fewer than {COVERAGE_MIN_SHOWS} upcoming shows: not judged)")
    elif _pct < 90.0:
        fail(f"5c2 {_cov_msg} — below 90 %: a poster source has probably stopped working")
    elif _prev_pct is not None and _prev_pct - _pct >= 3.0:
        fail(f"5c2 {_cov_msg} — down {_prev_pct - _pct:.1f} points since the last build "
             f"({_prev_pct:.1f} %): a poster source has probably stopped working")
    else:
        note(_cov_msg + ("" if _prev_pct is not None else " (no previous coverage to compare)"))
    # One logo file standing in as the "poster" of several plays (the theatre.art.bg
    # logo on two Сфумато shows, 2026-10-08) is a scrape that went wrong.
    _by_md5 = {}
    for _n in ("SHOWART", "POSTERS", "EVENTART"):
        for _k, _v in _pmaps[_n].items():
            if isinstance(_v, str) and SAFEIMG_PATH_RE.match(_v) and (_FILE_ROOT / _v).is_file():
                _by_md5.setdefault(_hl.md5((_FILE_ROOT / _v).read_bytes()).hexdigest(), set()).add(_k)
    for _h, _ids in _by_md5.items():
        if _h in LOGO_MD5 and len(_ids) >= 2:
            fail(f"5c2 posters: {len(_ids)} ids share one known site-logo file as their poster: "
                 + ", ".join(sorted(_ids)[:6]))
except ImportError:
    note("posterpolicy.py not found — skipped the poster coverage check")

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

# ----------------------------------------- 6b2. merged film ids (FILM_REDIRECT)
# A film record merged into the film it duplicated (film_merges.json) is gone from
# FILMS and lists nothing; its old id opens the kept film. A merged-away id still in
# FILMS or SHOWTIMES is the duplicate card the merge removed, come back.
try:
    _frm = re.search(r"^const FILM_REDIRECT\s*=\s*(.*?);\s*$", js, flags=re.M)
    _fr = json.loads(_frm.group(1)) if _frm else {}
    if not isinstance(_fr, dict):
        fail("FILM_REDIRECT is not an object")
        _fr = {}
    _f_ids = {f.get("id") for f in (data.get("FILMS") or []) if isinstance(f, dict)}
    _st_ids = {r[0] for r in (data.get("SHOWTIMES") or []) if isinstance(r, list) and r}
    _back = sorted(k for k in _fr if k in _f_ids or k in _st_ids)
    if _back:
        fail(f"merged-away film id(s) still listed in FILMS/SHOWTIMES: {', '.join(_back[:5])}")
    _dead = sorted(k for k, v in _fr.items() if v not in _f_ids)
    if _dead:
        note(f"FILM_REDIRECT: {len(_dead)} merged film(s) whose kept film is not listed now: {', '.join(_dead[:5])}")
    if _fr:
        note(f"FILM_REDIRECT: {len(_fr)} merged film id(s) open the film they were merged into")
except Exception as _fr_err:
    fail(f"FILM_REDIRECT check error: {_fr_err}")

# ----------------------------------------- 6c. share pages (film/, show/, event/)
# Every share page gives a link-preview fetcher the title's own tags and does not send
# it away: no meta refresh. Apple's previewer (iMessage, Notes; it runs in WebKit and
# follows redirects) read the home page's card for every film shared from
# sofiagleda.com (16 Oct 2026). A browser is sent on to the app by a script that
# skips link-preview fetchers, and a browser without JavaScript gets a link.
# Each page: canonical and og:url = SITE_URL/<kind>/<id>/, og:title, og:description,
# og:image and twitter:image absolute https, twitter:card summary_large_image, that
# script, the link. A listed film, show or event without a page is only noted: its
# share link still reaches the app (404.html), and the listings must not wait for it.
try:
    sys.path.insert(0, str(ROOT / "scripts"))
    from site_config import SITE_URL as _SITE_URL

    _SP_TAGS = [
        (r'<meta property="og:title" content="[^"]+"', "og:title"),
        (r'<meta property="og:description" content="[^"]+"', "og:description"),
        (r'<meta name="twitter:card" content="summary_large_image"', "twitter:card summary_large_image"),
        (r'<meta name="twitter:title" content="[^"]+"', "twitter:title"),
    ]
    _sp_bad: list[str] = []
    _sp_count = 0
    _idr_m = re.search(r"^const ID_REDIRECT\s*=\s*(.*?);\s*$", js, flags=re.M)
    try:
        _id_redirect = json.loads(_idr_m.group(1)) if _idr_m else {}
    except ValueError:
        _id_redirect = {}
    if not isinstance(_id_redirect, dict):
        _id_redirect = {}
    try:                       # the source gen_share_pages.write_redirects reads
        from show_identity import renamed_ids as _renamed_ids
        _id_redirect = {**_renamed_ids(), **_id_redirect}
    except Exception:
        pass
    # merged film ids (FILM_REDIRECT; film_merges.json): film/<old>/ is the kept film's page
    _fr_m = re.search(r"^const FILM_REDIRECT\s*=\s*(.*?);\s*$", js, flags=re.M)
    try:
        _film_redirect = json.loads(_fr_m.group(1)) if _fr_m else {}
    except ValueError:
        _film_redirect = {}
    if not isinstance(_film_redirect, dict):
        _film_redirect = {}
    try:
        from film_identity import load_merges as _load_merges, film_redirects as _film_redirects
        _film_redirect = {**_film_redirects(_load_merges()), **_film_redirect}
    except Exception:
        pass

    def _check_share_page_file(kind, item_id):
        global _sp_count
        _page_path = _FILE_ROOT / kind / item_id / "index.html"
        if not _page_path.exists():
            fail(f"share page missing: {kind}/{item_id}/index.html")
            return
        _sp_count += 1
        _page_src = _page_path.read_text(encoding="utf-8")
        _where = f"share page {kind}/{item_id}"
        # a renamed show id's old directory is the share page of the new record
        # (ID_REDIRECT, gen_share_pages.write_redirects): its canonical is the new id
        _target = (_id_redirect.get(item_id, item_id) if kind == "show"
                   else _film_redirect.get(item_id, item_id) if kind == "film" else item_id)
        _expected_url = f"{_SITE_URL}/{kind}/{_target}/"
        if (f'rel="canonical" href="{_expected_url}"' not in _page_src
                or f'property="og:url" content="{_expected_url}"' not in _page_src):
            _sp_bad.append(f"{_where}: og:url/canonical not {_expected_url!r}")
        for _prop in ("og:image", "twitter:image"):
            _img_m = re.search(r'(?:property|name)="%s"[^>]*content="([^"]*)"' % _prop, _page_src)
            if not _img_m:
                _sp_bad.append(f"{_where}: {_prop} missing")
            elif not _img_m.group(1).startswith("https://"):
                _sp_bad.append(f"{_where}: {_prop} not absolute HTTPS")
        for _rx, _label in _SP_TAGS:
            if not re.search(_rx, _page_src):
                _sp_bad.append(f"{_where}: {_label} missing")
        if re.search(r'http-equiv\s*=\s*["\']?refresh', _page_src, re.I):
            _sp_bad.append(f"{_where}: meta refresh (link-preview fetchers follow it)")
        _scripts = re.findall(r"<script[^>]*>(.*?)</script>", _page_src, flags=re.S)
        _redir = [x for x in _scripts if re.search(r"location\.(replace|assign|href)", x)]
        if not _redir:
            _sp_bad.append(f"{_where}: no redirect to the app for a browser")
        elif not all("facebookexternalhit" in x and "twitterbot" in x.lower() for x in _redir):
            _sp_bad.append(f"{_where}: redirects without skipping link-preview fetchers")
        if not re.search(r'<a [^>]*href="[^"]*\?%s=[^"]+"' % kind, _page_src):
            _sp_bad.append(f"{_where}: no link to the app for a browser without JavaScript")

    _listed = {"film": {f.get("id") for f in (data.get("FILMS") or []) if isinstance(f, dict)},
               "show": {x.get("id") for x in (data.get("SHOWS") or []) if isinstance(x, dict)},
               "event": {e.get("id") for e in (data.get("EVENTS") or []) if isinstance(e, dict)}}
    _ea_sp = re.search(r"^const EVENTS_AUTO\s*=\s*(.*?);\s*$", js, flags=re.M)
    try:
        _listed["event"] |= {e.get("id") for e in json.loads(_ea_sp.group(1)) if isinstance(e, dict)} if _ea_sp else set()
    except Exception:
        pass
    for _kind in ("film", "show", "event"):
        _kdir = _FILE_ROOT / _kind
        if not _kdir.is_dir():
            note(f"{_kind}/ directory not yet created: its share pages were not checked")
            continue
        _have = set()
        for _d in sorted(_kdir.iterdir()):
            if _d.is_dir():
                _have.add(_d.name)
                _check_share_page_file(_kind, _d.name)
        _missing = sorted(i for i in _listed[_kind] if i and i not in _have)
        if _missing:
            note(f"{len(_missing)} listed {_kind}(s) have no share page yet: " + ", ".join(_missing[:5]))
    if _sp_bad:
        fail(f"{len(_sp_bad)} share page problem(s):\n  " + "\n  ".join(_sp_bad[:12])
             + (f"\n  … and {len(_sp_bad) - 12} more" if len(_sp_bad) > 12 else ""))
    elif _sp_count:
        note(f"share pages: {_sp_count} checked, all carry their tags and no meta refresh")

except ImportError:
    note("site_config.py not importable — share page URL checks skipped")
except Exception as _sp_err:
    note(f"share page check error: {_sp_err}")

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

# ----------------------------------------- 6e. security: CSP, referrer, links, frames
# The Content-Security-Policy meta must allow exactly the page's inline scripts (by
# hash: it is computed by scripts/csp.py after the last edit), with the directives
# csp.DIRECTIVES lists; a <script> the build does not make, or a data script cut
# short by a value that closed it ("</script>" in a title), fails here. Every share
# page carries its own policy too. A stale hash would block the whole app in the
# browser, so this is a hard failure, not a note.
try:
    sys.path.insert(0, str(ROOT / "scripts"))
    import csp as _csp                                              # noqa: E402
    for _p in _csp.check(src, "page"):
        fail(f"CSP: {_p}")
    _share_bad, _share_n = [], 0
    for _sp in _csp.share_pages(_FILE_ROOT):
        _share_n += 1
        _probs = _csp.check(_sp.read_text(encoding="utf-8"), "share")
        if _probs:
            _share_bad.append(f"{_sp.parent.parent.name}/{_sp.parent.name}: {_probs[0]}")
    if _share_bad:
        fail(f"CSP: {len(_share_bad)} of {_share_n} share page(s) fail: " + "; ".join(_share_bad[:3])
             + " — run scripts/csp.py")
    # The listings' own addresses, /home/ and /home/theatre/, are copies of index.html
    # (gen_share_pages.py): each must be today's page, apart from its canonical address.
    _canon = lambda t: re.sub(r'(rel="canonical" href|property="og:url" content)="[^"]*"', r'\1=""', t)
    for _hp in _csp.home_pages(_FILE_ROOT):
        _ht = _hp.read_text(encoding="utf-8")
        _rel = _hp.parent.relative_to(_FILE_ROOT).as_posix() + "/"
        for _p in _csp.check(_ht, "page"):
            fail(f"CSP {_rel}: {_p}")
        if HTML.resolve() == (_FILE_ROOT / "index.html").resolve() and _canon(_ht) != _canon(src):
            fail(f"{_rel} is not this index.html (gen_share_pages.py copies it there after the last data edit)")
except ImportError:
    fail("scripts/csp.py not importable — the CSP cannot be checked")

if '<meta name="referrer" content="strict-origin-when-cross-origin">' not in _head_src:
    fail('referrer policy meta missing from <head> (strict-origin-when-cross-origin)')
for _host in ("fonts.googleapis.com", "fonts.gstatic.com"):
    if _host in src:
        fail(f"the page still contacts {_host}: the webfonts are self-hosted (fonts/fonts.css)")
if not (_FILE_ROOT / "fonts" / "fonts.css").exists():
    fail("fonts/fonts.css missing: the page links it for its webfonts")
# Every link that opens a new tab gives the other site no handle on this one
# (noopener) and no referrer (noreferrer); window.open likewise.
_blank_bad = re.findall(r'target="_blank"(?! rel="noopener noreferrer")[^>]{0,40}', src)
if _blank_bad:
    fail(f"{len(_blank_bad)} target=\"_blank\" link(s) without rel=\"noopener noreferrer\": {_blank_bad[0][:60]!r}")
_wo_bad = [m for m in re.findall(r'window\.open\(([^)]*)\)', js) if "noopener,noreferrer" not in m]
if _wo_bad:
    fail(f"window.open without \"noopener,noreferrer\": {_wo_bad[0][:60]!r}")
# no inline event handlers anywhere in what the app writes (the CSP blocks them)
_inline_h = re.findall(r'<[a-z][^>]*\son(?:error|load|click|mouseover|focus|input|change)\s*=\s*["\']', src, re.I)
if _inline_h:
    fail(f"{len(_inline_h)} inline event handler(s) in the page (blocked by the CSP): {_inline_h[0][:60]!r}")
if re.search(r"\beval\s*\(|new Function\s*\(", js):
    fail("eval / new Function in the page JS (blocked by the CSP)")
# Ids and times have one shape each. An id names a share page directory (film/<id>/)
# and goes into attributes and URLs: letters (any script), digits, hyphens. A time
# is HH:MM. Anything else is a scraper bug and never ships.
_SAFE_ID = re.compile(r"[^\W_][\w-]{0,150}")
_ea_ids = []
try:
    _ea_m2 = re.search(r"^const EVENTS_AUTO\s*=\s*(.*?);\s*$", js, flags=re.M)
    _ea_ids = [e.get("id") for e in (json.loads(_ea_m2.group(1)) if _ea_m2 else []) if isinstance(e, dict)]
except ValueError:
    pass
_bad_ids = [f"{k}:{o.get('id')!r}" for k in ("FILMS", "SHOWS", "EVENTS", "CINEMAS", "THEATRES")
            for o in (data.get(k) or []) if isinstance(o, dict)
            and not (isinstance(o.get("id"), str) and _SAFE_ID.fullmatch(o["id"]))]
_bad_ids += [f"EVENTS_AUTO:{i!r}" for i in _ea_ids if not (isinstance(i, str) and _SAFE_ID.fullmatch(i))]
if _bad_ids:
    fail(f"{len(_bad_ids)} id(s) are not slugs (letters, digits, hyphens): {', '.join(_bad_ids[:4])}")
_bad_times = [r for r in (data.get("SHOWTIMES") or [])
              if not (isinstance(r, list) and len(r) >= 4 and isinstance(r[3], list)
                      and all(isinstance(t, str) and re.fullmatch(r"\d\d:\d\d", t) for t in r[3]))]
if _bad_times:
    fail(f"{len(_bad_times)} SHOWTIMES row(s) whose times are not HH:MM, e.g. {str(_bad_times[0])[:80]}")


# ----------------------------------------- 6f. legal pages and the cookie notice
# The app links a terms page, a privacy policy and a cookie policy in BG and EN
# (legal/, written by scripts/gen_legal_pages.py). They must match the page they
# describe:
#   - each exists, has its canonical URL under SITE_URL and carries no script;
#   - no page holds a leftover placeholder ({CONTACT_…}, {OPERATOR_…}): the site names
#     no person and gives no email, by the owner's decision of 2026-10-15, and no page
#     links a GitHub project or issues page: no contact channel at all (2026-10-22);
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
        # no contact channel (owner, 2026-10-22): no link to a GitHub page (the project, its
        # issues); GitHub's own privacy statement (docs.github.com, the host's policy) stays
        gh = sorted(set(re.findall(r'href="(https?://(?:www\.)?github\.com(?:/[^"]*)?)"', t)))
        if gh:
            fail(f"{r}: links {gh[0]}: the site names no contact channel (no GitHub page or issues link)")
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
    # the page's only cookie writes are deletions (Max-Age=0: the _ga cookies when the
    # visitor withdraws consent for statistics); it never sets a cookie of its own
    for _cw in re.finditer(r"document\.cookie\s*=", js):
        if "Max-Age=0" not in js[_cw.end():_cw.end() + 60]:
            fail("the page sets a cookie, but the cookie policy says it sets none")
            break
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

    # Google Analytics 4: the page, the policies and site_config.py agree (on or off)
    try:
        from site_config import ga_measurement_id
        want_ga = ga_measurement_id()
    except (ImportError, ValueError) as _ga_err:
        want_ga = None
        fail(f"GA Measurement ID: {_ga_err}")
    import csp as _csp_ga                                            # noqa: E402
    have_ga = _csp_ga.ga_id_of(src)
    if want_ga is not None and have_ga != want_ga:
        fail(f"Google Analytics: scripts/site_config.py says {want_ga or 'off'}, the page was built with "
             f"{have_ga or 'none'}: rebuild (dev_build.py / build_index.py, gen_share_pages.py, "
             "gen_legal_pages.py, csp.py)")
    if have_ga:
        if "googletagmanager.com/gtag/js" not in js or 'consentRun("stats",gaStart)' not in js:
            fail("Google Analytics: the page has the GA bootstrap but no consent-gated loader")
        if re.search(r'<script[^>]+src="[^"]*googletagmanager', src):
            fail("Google Analytics: gtag.js is in the page's markup: it may only load after consent")
        suffix = have_ga[2:]
        for r in ("legal/cookies/", "legal/en/cookies/"):
            for c in ("_ga", f"_ga_{suffix}"):
                if r in pages and f"<code>{c}</code>" not in pages[r]:
                    fail(f"{r}: the page loads Google Analytics after consent, but the cookie {c} is not listed: "
                         "run scripts/gen_legal_pages.py")
    for r in ("legal/privacy/", "legal/en/privacy/", "legal/cookies/", "legal/en/cookies/"):
        if r in pages and ("Google Analytics" in pages[r]) != bool(have_ga):
            fail(f"{r}: {'says nothing about' if have_ga else 'describes'} Google Analytics, but the page "
                 f"{'uses' if have_ga else 'does not use'} it: run scripts/gen_legal_pages.py")
    for r, t in pages.items():
        if "googletagmanager" in t or "SOFIA_GA" in t:
            fail(f"{r}: loads analytics (the legal pages never do)")
    _shares = list(_csp_ga.share_pages(_FILE_ROOT))
    _ga_shares = [sp for sp in _shares if "googletagmanager" in sp.read_text(encoding="utf-8")]
    if _ga_shares:
        fail(f"{len(_ga_shares)} share page(s) load analytics, e.g. {_ga_shares[0].parent.name}: they never do")
    sw_text = sw.read_text(encoding="utf-8") if sw.exists() else ""
    if have_ga and sw.exists() and "google-analytics.com" not in sw_text:
        fail("sw.js does not leave Google's analytics requests alone (they must never be cached)")

    # the same Content-Security-Policy as the app (a legal page may leave out the GA4
    # sources: it never loads analytics)
    main_csp = _csp_of(src)
    if main_csp and have_ga:
        for _d, _v in _csp_ga.GA_SOURCES.items():
            if _d in main_csp:
                main_csp[_d] = main_csp[_d] - set(_v.split())
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
            # Any Content-Security-Policy violation during the journey fails the gate
            # (a stale hash blocks a script; a poster host outside img-src, etc.).
            ctx.add_init_script("window.__cspv=[];document.addEventListener("
                                "'securitypolicyviolation',function(e){window.__cspv.push("
                                "e.violatedDirective+' '+(e.blockedURI||'')+' '+(e.sample||''))},true);")
            # Google Analytics may load only after consent, and this journey refuses it: any
            # request to Google's analytics hosts fails the gate (and is never let through)
            google_hits = []
            ctx.route(re.compile(r"^https?://([^/]*\.)?(googletagmanager\.com|google-analytics\.com|"
                                 r"analytics\.google\.com)(/|$)"),
                      lambda route: (google_hits.append(route.request.url), route.abort()))
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
            # a first visit: the cookie notice is up, and "Разбрах" (with analytics on:
            # "Само необходимите") closes it and keeps the choice
            if re.search(r'(?m)^const CONSENT_VERSION="', js):
                close = "#sg-consent [data-consent-none]" if "/* SOFIA-GA */" in js else "#sg-consent [data-consent-ok]"
                if not page.is_visible(close):
                    fail(f"[{tz}] the cookie notice did not show on a first visit")
                else:
                    page.click(close)
                    page.wait_for_timeout(200)
                    if (page.is_visible("#sg-consent")
                            or not page.evaluate("!!localStorage.getItem('sofia-consent')")):
                        fail(f"[{tz}] the cookie notice's button did not close it and keep the choice")
            cards = page.query_selector_all("[data-film]")
            if google_hits:
                fail(f"[{tz}] the page contacted Google Analytics without consent: {google_hits[0][:80]}")
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
            # box office only: every BOXONLY performance and every screening at a
            # BOOKING inPerson or festival cinema render their time panel as the rule says: a
            # box-office-only one with the stub, no "Купи билет", and its site line and
            # calendar link never a ticket seller's page; a festival screening there
            # with "Купи билет" (owner, 2026-10-28), unless FESTROWS says sold out, not on
            # sale yet, or gives it no ticket link of its own ("via": the festival's page)
            if tz == "Europe/Sofia":
                _bo_bad = page.evaluate("""(sellers)=>{
                  if(typeof screeningSheetHtml!=="function")return ["screeningSheetHtml is missing"];
                  const host=u=>{try{return new URL(u).hostname.replace(/^www\\./,'')}catch(e){return''}};
                  const seller=u=>{const h=host(u);return !!h&&sellers.some(x=>h===x||h.endsWith('.'+x));};
                  const FRA=(typeof FESTROWS!=="undefined"&&Array.isArray(FESTROWS))?FESTROWS:[];
                  const FR=new Set(FRA.map(r=>r.slice(0,4).join('|')));
                  const SHUT=new Map(FRA.filter(r=>r[6]==='soldout'||r[6]==='notyet').map(r=>[r.slice(0,4).join('|'),r[6]]));
                  // a FESTROWS row with no ticket link of its own (url null): "via", the
                  // festival's page as a quiet link, never "Купи билет" (2026-10-29)
                  FRA.forEach(r=>{const k=r.slice(0,4).join('|');if(!SHUT.has(k)&&r.length>5&&!r[5]&&festSaleVenue(r[1]))SHUT.set(k,'via');});
                  const VL=(typeof VLINKS!=="undefined"&&Array.isArray(VLINKS))?VLINKS:[];
                  const out=[], probe=(k,id,v,d,t)=>{const el=document.createElement('div');
                    el.innerHTML=screeningSheetHtml(k,id,v,d,t);
                    const tag=k+':'+id+'|'+v+'|'+d+'|'+t, bo=boxOnly(k,id,v,d,t);
                    // the rule itself, from the data (not through boxOnly): a FESTROWS row, a
                    // festival-named film id or an epaygo.bg link is sold online; any other
                    // screening at a box-office-only cinema is not
                    if(k==='film'){
                      const fest=!!(BOOKING[v]||{}).festival||FR.has(id+'|'+v+'|'+d+'|'+t)||/(?:^|-)(sinelibri|kinomaniya)-/.test(id)
                        ||host((VL.find(x=>x[0]===id&&x[1]===v)||[])[2]||'')==='epaygo.bg';
                      if(fest&&bo)out.push(tag+' (festival screening shows the box-office stub)');
                      if(!fest&&!bo)out.push(tag+' (box-office-only screening offers online sale)');}
                    // a festival screening sold out / not on sale yet: its note, no buy button, no stub
                    const shut=k==='film'?SHUT.get(id+'|'+v+'|'+d+'|'+t):null;
                    if(shut){ const n=el.querySelector('[data-festsale]');
                      if(bo||el.querySelector('.ss-btn.pri')||el.querySelector('[data-boxonly]')||!n||n.dataset.festsale!==shut
                         ||n.querySelector('a.ss-btn,button'))out.push(tag+' ('+shut+': a buy button, a stub or no note)'); return; }
                    if(!bo){ if(!el.querySelector('.ss-btn.pri[href]')||el.querySelector('[data-boxonly]'))out.push(tag+' (festival: no buy button)'); return; }
                    if(!el.querySelector('[data-boxonly]')||el.querySelector('.ss-btn.pri'))out.push(tag+' (buy button or no note)');
                    const site=el.querySelector('a.ss-site'), ev=calEvent(k,id,v,d,t);
                    if(site&&seller(site.getAttribute('href')))out.push(tag+' (site line '+site.getAttribute('href')+')');
                    if(ev&&(seller(ev.url)||sellers.some(x=>(ev.description||'').includes(x+'/'))))out.push(tag+' (calendar '+ev.url+')');};
                  const bx=(typeof BOXONLY!=="undefined"&&Array.isArray(BOXONLY))?BOXONLY:[];
                  for(const r of bx){const sh=showById[r[0]]; if(sh)probe('show',r[0],sh.theatre,r[1],r[2]);}
                  // a festival cinema (BOOKING festival: Euro Cinema) the same way: every
                  // screening there is the festival's, sold online
                  for(const v of Object.keys(BOOKING).filter(v=>(BOOKING[v].inPerson||BOOKING[v].festival)&&cinById[v]))
                    for(const r of SHOWTIMES.filter(r=>r[1]===v&&r[2]>=TODAY))for(const t of (r[3]||[]))probe('film',r[0],v,r[2],t);
                  return out;}""", list(_SELLER_HOSTS))
                if _bo_bad:
                    fail(f"{len(_bo_bad)} box-office-only / festival showing(s) render wrongly, "
                         f"e.g. {', '.join(_bo_bad[:3])}")
            if errs:
                fail(f"[{tz}] JavaScript errors: " + " | ".join(sorted(set(errs))[:3]))
            _v = page.evaluate("window.__cspv||[]")
            if _v:
                fail(f"[{tz}] {len(_v)} Content-Security-Policy violation(s): " + " | ".join(_v[:3]))
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
