#!/usr/bin/env python3
"""Gate-level tests for verify_build.py (CODE-04, CODE-02, CODE-12, F3 festival rows).

Each test builds a minimal HTML page in /tmp and runs verify_build.py on it,
checking whether it exits 0 (pass) or non-zero (fail).  No network, ~2 seconds.

    python3 scripts/test_verify.py

Exit 0 = gate behaves correctly on all four probes.
"""
import os, sys, pathlib, subprocess, textwrap, tempfile, datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERIFY = str(ROOT / "scripts" / "verify_build.py")
PYTHON = sys.executable

fails: list[str] = []


def check(name: str, passed: bool) -> None:
    if passed:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}")
        fails.append(name)


# ---------------------------------------------------------------------------
# Helpers for minimal HTML skeleton
# ---------------------------------------------------------------------------

_FUTURE = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
_NOW_ISO = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

# Floor values from verify_build.py:
# FILMS>=20, SHOWTIMES>=80, CINEMAS>=8, SHOWS>=20, PERFORMANCES>=40, THEATRES>=6
import json as _json

def _bulk_films(n=20):
    return _json.dumps([{"id": f"f{i}", "bg": f"Филм {i}", "en": f"Film {i}",
                         "year": 2026, "country": "BG", "duration": 90, "genres": []}
                        for i in range(n)])

def _bulk_cinemas(n=8):
    return _json.dumps([{"id": f"cinema{i}", "chain": "CC", "name": f"Cinema {i}", "city": "Sofia"}
                        for i in range(n)])

def _bulk_booking(n=8):
    """BOOKING for all cinemas so the gate does not complain about missing ticket links."""
    return _json.dumps({f"cinema{i}": {"label": f"CC{i}", "url": f"https://example{i}.bg/"}
                        for i in range(n)})

def _bulk_showtimes(n=80):
    # Use cinema0..cinema7 with f0..f19, future date
    rows = []
    for i in range(n):
        rows.append([f"f{i % 20}", f"cinema{i % 8}", _FUTURE, ["10:00"]])
    return _json.dumps(rows)

def _bulk_theatres(n=6):
    return _json.dumps([{"id": f"th{i}", "name": f"Театър {i}", "nameEn": f"Theatre {i}"}
                        for i in range(n)])

def _bulk_shows(n=20):
    return _json.dumps([{"id": f"show{i}", "title": f"Спектакъл {i}",
                         "titleEn": f"Show {i}", "theatre": f"th{i % 6}"}
                        for i in range(n)])

def _bulk_performances(n=40, date_offset=0):
    """Generate n unique performances (different show+date+time combos)."""
    rows = []
    base = datetime.date.today() + datetime.timedelta(days=1 + date_offset)
    for i in range(n):
        # Each row gets a unique date (day base+i//4) and a unique time slot
        d = (base + datetime.timedelta(days=i // 4)).isoformat()
        times = ["10:00", "14:00", "18:00", "20:00"]
        rows.append([f"show{i % 20}", d, times[i % 4], "Основна сцена", ""])
    return _json.dumps(rows)


# the gate compares the page with site_config, so the tests use the same value
from site_config import SITE_URL as _SITE_URL_FOR_TEST

def _minimal_html(snapshot_extra: str = "", extra_js: str = "",
                  showtimes_override: str = "",
                  performances_override: str = "",
                  vlinks_override: str = "",
                  site_url: str = "") -> str:
    """Build a valid-enough page for the gate, meeting all floor requirements.

    All three SHOWTIMES/PERFORMANCES/VLINKS can be overridden for special test cases.
    The <head> includes the OG / Twitter Card meta tags required by check 6a.
    site_url defaults to _SITE_URL_FOR_TEST; pass a different value to test the
    SITE_URL consistency check (6a2), or "" to omit the SITE_URL declaration.
    """
    # Build floor-meeting defaults
    films_js      = _bulk_films()
    cinemas_js    = _bulk_cinemas()
    theatres_js   = _bulk_theatres()
    shows_js      = _bulk_shows()

    # When overriding we must not also re-generate bulk rows that create cross-ref errors
    if showtimes_override:
        st_data = showtimes_override
    else:
        st_data = _bulk_showtimes()

    if performances_override:
        pf_data = performances_override
    else:
        pf_data = _bulk_performances()

    vlinks_data = vlinks_override if vlinks_override else "[]"

    # SYN_EN / TITLE_EN must cover every film id
    film_ids = [f"f{i}" for i in range(20)]
    syn_en_js   = _json.dumps({fid: "Synopsis." for fid in film_ids})
    title_en_js = _json.dumps({fid: f"Film {fid}" for fid in film_ids})

    snap_gen = f',"generated":"{_NOW_ISO}"' + snapshot_extra
    _og_image = f"{_SITE_URL_FOR_TEST}/share/share-preview.jpg"
    _site_url_line = (f'const SITE_URL="{site_url or _SITE_URL_FOR_TEST}";'
                      if site_url != "OMIT" else "")
    return textwrap.dedent(f"""\
        <!doctype html><html><head><meta charset="utf-8"/>
        <meta property="og:type" content="website"/>
        <meta property="og:site_name" content="Sofia Gleda"/>
        <meta property="og:url" content="{_SITE_URL_FOR_TEST}/"/>
        <meta property="og:title" content="София гледа"/>
        <meta property="og:description" content="Афишът на кината и театрите в София."/>
        <meta property="og:image" content="{_og_image}"/>
        <meta property="og:locale" content="bg_BG"/>
        <meta name="twitter:card" content="summary_large_image"/>
        <meta name="twitter:title" content="София гледа"/>
        <meta name="twitter:image" content="{_og_image}"/>
        </head><body>
        <script>/* SOFIA-DATA-START */
        const SNAPSHOT={{"window":{{"from":"2026-09-15","to":"2026-12-31"}}{snap_gen}}};
        const CINEMAS={cinemas_js};
        const FILMS={films_js};
        const SHOWTIMES={st_data};
        const THEATRES={theatres_js};
        const SHOWS={shows_js};
        const PERFORMANCES={pf_data};
        const EVENTS=[];
        const BOOKING={_bulk_booking()};
        const TICKET_HUBS=[];
        const FILM_EXTRA={{}};
        const SHOW_EXTRA={{}};
        const TASTE_FILMS=[];
        const TASTE_SHOWS=[];
        const VENUE_UNTIL={{}};
        const LINKS=[];
        const VLINKS={vlinks_data};
        const PRELIM_FROM={{}};
        {_site_url_line}
        {extra_js}
        /* SOFIA-DATA-END */</script>
        <script>/* SOFIA-POSTERS-START */
        const POSTERS={{}};
        const SHOWART={{}};
        const SYN_EN={syn_en_js};
        const TITLE_EN={title_en_js};
        const FILMINFO={{}};
        /* SOFIA-POSTERS-END */</script>
        </body></html>
    """)


sys.path.insert(0, str(ROOT / "scripts"))
import csp as _csp  # noqa: E402


def _finish(html: str) -> str:
    """What build_index.py and csp.py give every real page: the referrer policy meta
    and the Content-Security-Policy meta for its scripts (the gate requires both).
    Pages for the other checks get them here; the CSP tests below pass finish=False."""
    if 'name="referrer"' not in html:
        html = html.replace("<head>", '<head><meta name="referrer" content="strict-origin-when-cross-origin">', 1)
    try:
        return _csp.stamp(html, strict=False)
    except _csp.CSPError:
        return html


def _run(html_content: str, skip_smoke: bool = True,
         skip_freshness: bool = False,
         extra_env: dict | None = None, finish: bool = True) -> int:
    """Write HTML to a tmp file, run verify_build.py, return exit code.

    verify_build.py reads the HTML path from SOFIA_HTML env var (not argv).
    """
    if finish:
        html_content = _finish(html_content)
    env = dict(os.environ)
    env["SOFIA_SKIP_SMOKE"] = "1" if skip_smoke else "0"
    if skip_freshness:
        env["SOFIA_SKIP_FRESHNESS"] = "1"
    if extra_env:
        env.update(extra_env)
    with tempfile.NamedTemporaryFile(suffix=".html", mode="w",
                                     encoding="utf-8", delete=False) as fh:
        fh.write(html_content)
        path = fh.name
    env["SOFIA_HTML"] = path
    try:
        result = subprocess.run(
            [PYTHON, VERIFY],
            capture_output=True, text=True, env=env
        )
        return result.returncode
    finally:
        os.unlink(path)


def _run_out(html_content: str,
             extra_env: dict | None = None, finish: bool = True) -> tuple[int, str]:
    """Like _run (smoke and freshness skipped), but also returns the gate's output,
    so a test can tell WHICH check failed."""
    if finish:
        html_content = _finish(html_content)
    env = dict(os.environ, SOFIA_SKIP_SMOKE="1", SOFIA_SKIP_FRESHNESS="1")
    if extra_env:
        env.update(extra_env)
    with tempfile.NamedTemporaryFile(suffix=".html", mode="w",
                                     encoding="utf-8", delete=False) as fh:
        fh.write(html_content)
        path = fh.name
    env["SOFIA_HTML"] = path
    try:
        result = subprocess.run([PYTHON, VERIFY], capture_output=True, text=True, env=env)
        return result.returncode, result.stdout + result.stderr
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Test 1: a good build passes (baseline)
# ---------------------------------------------------------------------------
print("CODE-04 / baseline: a good build passes")
check("good build exits 0",
      _run(_minimal_html(), skip_freshness=True) == 0)


# ---------------------------------------------------------------------------
# Test 2 (CODE-04): a page with a JS syntax error must FAIL the gate
# ---------------------------------------------------------------------------
print("\nCODE-04: broken JS must fail")
# Inject a syntax error into the page (const node parens mismatch)
broken_js = "const _SYNTAX_ERROR = function({unclosed{;"
check("broken-JS page exits non-zero",
      _run(_minimal_html(extra_js=broken_js), skip_freshness=True) != 0)

# ---------------------------------------------------------------------------
# Test 3 (CODE-02): missing SNAPSHOT.generated must FAIL
# ---------------------------------------------------------------------------
print("\nCODE-02: freshness gate")

# Replace the generated field with nothing by using snapshot_extra trick:
# _minimal_html() inserts `,"generated":"<NOW>"` + snapshot_extra in SNAPSHOT.
# To drop generated we build the page and then strip the generated field.
_good_html = _minimal_html()
no_gen_html = _good_html.replace(f',"generated":"{_NOW_ISO}"', "")
check("missing generated field exits non-zero",
      _run(no_gen_html, skip_freshness=False) != 0)

# Stale build: generated = 48 hours ago
stale_ts = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
stale_html = _good_html.replace(f'"generated":"{_NOW_ISO}"', f'"generated":"{stale_ts}"')
check("stale generated (48h ago) exits non-zero",
      _run(stale_html, skip_freshness=False) != 0)

# SOFIA_SKIP_FRESHNESS=1 must bypass freshness failure
check("SOFIA_SKIP_FRESHNESS=1 bypasses stale-freshness failure",
      _run(stale_html, skip_freshness=True) == 0)

# No upcoming rows: all SHOWTIMES and PERFORMANCES in the past
_PAST = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
# Override SHOWTIMES and PERFORMANCES with past-only data (but keep floor counts)
_past_showtimes = _json.dumps([[f"f{i % 20}", f"cinema{i % 8}", _PAST, ["10:00"]] for i in range(80)])
_past_perfs     = _json.dumps([[f"show{i % 20}", _PAST, "20:00", "Основна сцена", ""] for i in range(40)])
empty_upcoming = _minimal_html(
    showtimes_override=_past_showtimes,
    performances_override=_past_perfs,
)
check("no upcoming rows exits non-zero",
      _run(empty_upcoming, skip_freshness=False) != 0)


# ---------------------------------------------------------------------------
# Test 4 (CODE-12): non-http(s) URLs in data constants must FAIL
# ---------------------------------------------------------------------------
print("\nCODE-12: non-http(s) URL in data constants must fail")

# javascript: in LINKS
bad_links_html = _good_html.replace(
    "const LINKS=[];", 'const LINKS=[["f0","javascript:alert(1)"]];')
check("javascript: in LINKS exits non-zero",
      _run(bad_links_html, skip_freshness=True) != 0)

# ftp:// scheme in VLINKS — use cinema0 which is in VENUE_LINK_ALLOWLIST via our cc-sofia analog
# We need a venue that IS in VENUE_LINK_ALLOWLIST, e.g. cc-sofia.  Our cinemas are cinema0..7.
# cinema0 is NOT in VENUE_LINK_ALLOWLIST, so the allowlist check won't fire; only the scheme check does.
# But the BOOKING key is cinema0 too, so no deep=true issue.
bad_vlinks_js = _json.dumps([["f0", "vlaikova", "ftp://vlaikovacinema.com/ticket"]])
bad_vlinks_html = _good_html.replace("const VLINKS=[];", f"const VLINKS={bad_vlinks_js};")
check("ftp: in VLINKS exits non-zero",
      _run(bad_vlinks_html, skip_freshness=True) != 0)


# ---------------------------------------------------------------------------
# Test 5 (CODE-12): substring host trick must be caught
# ---------------------------------------------------------------------------
print("\nCODE-12: substring host bypass must fail")

# Bad URL: "notcinemacity.bg" contains "cinemacity.bg" as a substring but is
# not an exact match or a subdomain — old substring code would allow it.
trick_vlinks_js = _json.dumps([["f0", "cc-sofia", "https://notcinemacity.bg/ticket"]])
# For cc-sofia we need the VLINKS entry to be checked: use an updated CINEMAS
# that maps cinema0 -> cc-sofia id, but actually simpler to keep the VENUE_LINK_ALLOWLIST
# key "cc-sofia".  However our CINEMAS don't have id "cc-sofia". The host check still
# triggers regardless of whether the venue is registered: if the allowed list is non-empty
# (cc-sofia IS in VENUE_LINK_ALLOWLIST), the host must match exactly or as subdomain.
# Since CINEMAS uses cinema0..7 (no cc-sofia), the SHOWTIMES won't cross-ref "cc-sofia"
# so no orphan error. Only the VLINKS host check applies.
trick_vlinks_html = _good_html.replace("const VLINKS=[];", f"const VLINKS={trick_vlinks_js};")
check("substring host trick (notcinemacity.bg) exits non-zero",
      _run(trick_vlinks_html, skip_freshness=True) != 0)

# Real subdomain must PASS (sub.cinemacity.bg is fine)
subdomain_vlinks_js = _json.dumps([["f0", "cc-sofia", "https://sub.cinemacity.bg/ticket"]])
subdomain_vlinks_html = _good_html.replace("const VLINKS=[];", f"const VLINKS={subdomain_vlinks_js};")
check("legitimate subdomain (sub.cinemacity.bg) exits 0",
      _run(subdomain_vlinks_html, skip_freshness=True) == 0)

# Cine Grand: a film page must open for the row's own cinema (cinema_slug)
print("\nCine Grand film pages: the venue's own cinema_slug")
_cg_ring = "https://www.cinegrand.bg/m/veriti-imdbtt32261958?ignoreMissing=1&cinema_slug=%D1%81%D0%BE%D1%84%D0%B8%D1%8F-%D1%80%D0%B8%D0%BD%D0%B3-%D0%BC%D0%BE%D0%BB"
_cg_plain = "https://www.cinegrand.bg/m/veriti-imdbtt32261958"
check("Ring Mall film page with Ring Mall's cinema_slug exits 0",
      _run(_good_html.replace("const VLINKS=[];", "const VLINKS=" + _json.dumps([["f0", "cg-ring", _cg_ring]]) + ";"),
           skip_freshness=True) == 0)
_rc, _out = _run_out(_good_html.replace("const VLINKS=[];", "const VLINKS=" + _json.dumps([["f0", "cg-park", _cg_ring]]) + ";"))
check("Park Center row linking Ring Mall's film page exits non-zero", _rc != 0 and "other cinema" in _out)
_rc, _out = _run_out(_good_html.replace("const VLINKS=[];", "const VLINKS=" + _json.dumps([["f0", "cg-ring", _cg_plain]]) + ";"))
check("a film page without cinema_slug (older runs) is noted, not failed", _rc == 0 and "without cinema_slug" in _out)


# ---------------------------------------------------------------------------
# Test 6 (F3): festival rows — festival hosts, PRELIM_ROWS, a BOOKING per cinema
# ---------------------------------------------------------------------------
print("\nF3: festival rows")

# A festival row may link the festival's own film page / ticket shop at any
# venue, matched like CODE-12 (exact host or subdomain, never a substring)
fest_ok_js = _json.dumps([["f0", "cc-sofia", "https://www.cinelibri.com/film/x/"]])
check("festival host (www.cinelibri.com) at an allowlisted cinema exits 0",
      _run(_good_html.replace("const VLINKS=[];", f"const VLINKS={fest_ok_js};"),
           skip_freshness=True) == 0)
fest_trick_js = _json.dumps([["f0", "cc-sofia", "https://notcinelibri.com/film/x/"]])
_rc, _out = _run_out(_good_html.replace("const VLINKS=[];", f"const VLINKS={fest_trick_js};"))
check("look-alike festival host (notcinelibri.com) exits non-zero",
      _rc != 0 and "notcinelibri.com" in _out)

# PRELIM_ROWS: [[filmId, venueId, "YYYY-MM-DD"]], each a real SHOWTIMES row at a
# listed cinema (f0 plays cinema0 on _FUTURE; f1 never plays cinema0)
check("PRELIM_ROWS naming a real SHOWTIMES row exits 0",
      _run(_minimal_html(extra_js=f'const PRELIM_ROWS=[["f0","cinema0","{_FUTURE}"]];'),
           skip_freshness=True) == 0)
_rc, _out = _run_out(_minimal_html(extra_js=f'const PRELIM_ROWS=[["f1","cinema0","{_FUTURE}"]];'))
check("PRELIM_ROWS row with no SHOWTIMES row exits non-zero",
      _rc != 0 and "have no SHOWTIMES row" in _out)
_rc, _out = _run_out(_minimal_html(extra_js=f'const PRELIM_ROWS=[["f0","nowhere","{_FUTURE}"]];'))
check("PRELIM_ROWS naming a venue the app does not list exits non-zero",
      _rc != 0 and "PRELIM_ROWS names venues the app does not list" in _out)
_rc, _out = _run_out(_minimal_html(extra_js='const PRELIM_ROWS=[["f0","cinema0","13.10.2026"]];'))
check("malformed PRELIM_ROWS date exits non-zero",
      _rc != 0 and "PRELIM_ROWS entr(ies) are not" in _out)

# every cinema needs its BOOKING entry (url or inPerson)
_last = '"cinema7": {"label": "CC7", "url": "https://example7.bg/"}'
assert _last in _good_html
_rc, _out = _run_out(_good_html.replace(", " + _last, ""))
check("a cinema without a BOOKING entry exits non-zero",
      _rc != 0 and "cinemas without a BOOKING entry" in _out and "cinema7" in _out)

# SHOWATTR: {"filmId|venueId": {"YYYY-MM-DD HH:MM": "dub,3d"}, …}
# Valid entry for a known film/venue with a SHOWTIMES row should exit 0.
_sa_good = _json.dumps({"f0|cinema0": {f"{_FUTURE} 10:00": "dub"}})
check("SHOWATTR with a valid entry exits 0",
      _run(_minimal_html(extra_js=f"const SHOWATTR={_sa_good};"),
           skip_freshness=True) == 0)
_sa_badkey = _json.dumps({"f0cinema0": {f"{_FUTURE} 10:00": "dub"}})
_rc, _out = _run_out(_minimal_html(extra_js=f"const SHOWATTR={_sa_badkey};"))
check("SHOWATTR key missing pipe separator exits non-zero",
      _rc != 0 and "SHOWATTR key" in _out)
_sa_badtok = _json.dumps({"f0|cinema0": {f"{_FUTURE} 10:00": "dubbed"}})
_rc, _out = _run_out(_minimal_html(extra_js=f"const SHOWATTR={_sa_badtok};"))
check("SHOWATTR unknown token 'dubbed' exits non-zero",
      _rc != 0 and "unknown tokens" in _out)
_sa_orphan = _json.dumps({"f0|cinema0": {f"{_FUTURE} 23:59": "dub"}})
_rc, _out = _run_out(_minimal_html(extra_js=f"const SHOWATTR={_sa_orphan};"))
check("SHOWATTR entry with no matching SHOWTIMES row exits non-zero",
      _rc != 0 and "no matching SHOWTIMES row" in _out)


# ---------------------------------------------------------------------------
# Test 7 (P2F): PRELIM_ROWS — theatre show rows accepted and rejected
# PRELIM_ROWS can now contain [showId, theatreId, "YYYY-MM-DD"] theatre rows.
# The show must exist at that theatre AND have a PERFORMANCES entry on that date.
# ---------------------------------------------------------------------------
print("\nP2F: PRELIM_ROWS — theatre show rows in the gate")

# show0 is at th0; _bulk_performances has show0 on _FUTURE at 10:00, 14:00, ...
# Build a PRELIM_ROWS entry for show0 at th0 on _FUTURE (matches a PERFORMANCES row)
_perf_show0_date = _FUTURE
check("PRELIM_ROWS with a matching show/theatre/date PERFORMANCES row exits 0",
      _run(_minimal_html(extra_js=f'const PRELIM_ROWS=[["show0","th0","{_perf_show0_date}"]];'),
           skip_freshness=True) == 0)

# show0 at th0 but on a date with NO PERFORMANCES row
_rc, _out = _run_out(_minimal_html(
    extra_js=f'const PRELIM_ROWS=[["show0","th0","2025-01-01"]];'))
check("PRELIM_ROWS theatre row with no PERFORMANCES entry exits non-zero",
      _rc != 0 and ("theatre row" in _out.lower() or "PERFORMANCES" in _out))

# show0 at th1 — wrong theatre (show0 belongs to th0, not th1)
_rc, _out = _run_out(_minimal_html(
    extra_js=f'const PRELIM_ROWS=[["show0","th1","{_perf_show0_date}"]];'))
check("PRELIM_ROWS theatre row with wrong theatre exits non-zero",
      _rc != 0 and ("theatre row" in _out.lower() or "PERFORMANCES" in _out))

# Test 7 (P2D): aggregator-domain VLINKS only allowed for preliminary-only pairs
# ---------------------------------------------------------------------------
print("\nP2D: aggregator domain VLINKS rules")

# _FUTURE is today+1 (from the top of this file). For these tests we need a
# showtime date strictly after PRELIM_FROM, so use today+2 for showtimes.
_DAY_AFTER = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
_p2d_showtimes = _json.dumps(
    [[f"f{i % 20}", f"cinema{i % 8}", _DAY_AFTER, ["10:00"]] for i in range(80)])

def _with_prelim_from(base_html, pf_dict):
    """Replace `const PRELIM_FROM={};` with the given dict in the HTML."""
    return base_html.replace("const PRELIM_FROM={};",
                             f"const PRELIM_FROM={_json.dumps(pf_dict)};")

# PRELIM_FROM[cinema0] = _FUTURE (today+1). Showtimes for cinema0 use _DAY_AFTER
# (today+2). Since today+2 >= today+1, all rows are preliminary → programata OK.
_prelim_html = _with_prelim_from(
    _minimal_html(showtimes_override=_p2d_showtimes),
    {f"cinema{i}": _FUTURE for i in range(8)},
)
prel_ok_js = _json.dumps([["f0", "cinema0", "https://www.programata.bg/film/x"]])
_rc, _out = _run_out(_prelim_html.replace("const VLINKS=[];", f"const VLINKS={prel_ok_js};"))
check("programata.bg VLINKS for preliminary-only pair exits 0", _rc == 0)

# PRELIM_FROM[cinema0] = a far-future date. Showtimes for cinema0 use _DAY_AFTER
# (today+2). Since today+2 < far_future, the row is confirmed → programata FAILS.
_far_future = (datetime.date.today() + datetime.timedelta(days=365)).isoformat()
_confirmed_html = _with_prelim_from(
    _minimal_html(showtimes_override=_p2d_showtimes),
    {"cinema0": _far_future},
)
_rc2, _out2 = _run_out(_confirmed_html.replace("const VLINKS=[];", f"const VLINKS={prel_ok_js};"))
check("programata.bg VLINKS for confirmed pair exits non-zero",
      _rc2 != 0 and ("programata" in _out2 or "confirmed" in _out2))

# theatre.art.bg URL in VLINKS for a confirmed pair must FAIL.
tart_confirmed_js = _json.dumps([["f0", "cinema0", "https://theatre.art.bg/show/123"]])
_rc3, _out3 = _run_out(_confirmed_html.replace("const VLINKS=[];", f"const VLINKS={tart_confirmed_js};"))
check("theatre.art.bg VLINKS for confirmed pair exits non-zero",
      _rc3 != 0 and ("theatre.art.bg" in _out3 or "aggregator" in _out3))


# ---------------------------------------------------------------------------
# Test 8 (P2D Item 2): aggregator homepage gate in VLINKS and LINKS
# ---------------------------------------------------------------------------
print("\nP2D Item 2: aggregator homepage gate")

# VLINKS entry with programata.bg root URL (no path) must FAIL.
_vlink_homepage_prog = _json.dumps([["f0", "cinema0", "https://programata.bg"]])
_rc4, _out4 = _run_out(_prelim_html.replace("const VLINKS=[];",
                                            f"const VLINKS={_vlink_homepage_prog};"))
check("VLINKS programata.bg homepage (no path) exits non-zero",
      _rc4 != 0 and ("homepage" in _out4 or "aggregator" in _out4 or "empty" in _out4 or "path" in _out4))

# VLINKS entry with programata.bg root + trailing slash (/) must FAIL.
_vlink_homepage_slash = _json.dumps([["f0", "cinema0", "https://programata.bg/"]])
_rc5, _out5 = _run_out(_prelim_html.replace("const VLINKS=[];",
                                            f"const VLINKS={_vlink_homepage_slash};"))
check("VLINKS programata.bg homepage (root slash) exits non-zero",
      _rc5 != 0 and ("homepage" in _out5 or "aggregator" in _out5 or "empty" in _out5 or "path" in _out5))

# VLINKS entry with theatre.art.bg + query-only (e.g. ?city=20) must FAIL.
_vlink_homepage_query = _json.dumps([["f0", "cinema0", "https://theatre.art.bg/?city=20"]])
_rc6, _out6 = _run_out(_prelim_html.replace("const VLINKS=[];",
                                            f"const VLINKS={_vlink_homepage_query};"))
check("VLINKS theatre.art.bg query-only homepage exits non-zero",
      _rc6 != 0 and ("homepage" in _out6 or "aggregator" in _out6 or "empty" in _out6 or "path" in _out6))

# VLINKS with a real programata.bg film path still passes (not a homepage).
_vlink_real_path = _json.dumps([["f0", "cinema0", "https://www.programata.bg/film/abc-2026"]])
_rc7, _out7 = _run_out(_prelim_html.replace("const VLINKS=[];",
                                            f"const VLINKS={_vlink_real_path};"))
check("VLINKS programata.bg with real film path exits 0", _rc7 == 0)

# LINKS with theatre.art.bg homepage must FAIL.
# _minimal_html() already emits `const LINKS=[];` — just replace it.
_links_homepage = _json.dumps([["show-x", "https://theatre.art.bg"]])
_links_base_html = _minimal_html()
_rc8, _out8 = _run_out(_links_base_html.replace("const LINKS=[];",
                                                  f"const LINKS={_links_homepage};"))
check("LINKS theatre.art.bg homepage exits non-zero",
      _rc8 != 0 and ("homepage" in _out8 or "aggregator" in _out8 or "empty" in _out8 or "LINKS" in _out8))

# LINKS with a real theatre.art.bg show page passes.
_links_real = _json.dumps([["show-x", "https://theatre.art.bg/spectakyl/show-x"]])
_rc9, _out9 = _run_out(_links_base_html.replace("const LINKS=[];",
                                                  f"const LINKS={_links_real};"))
check("LINKS theatre.art.bg with real show path exits 0", _rc9 == 0)


# ---------------------------------------------------------------------------
# Test 9 (P2D Item 1): AGGLINKS integrity checks
# ---------------------------------------------------------------------------
print("\nP2D AGGLINKS integrity")

# Helper: build a page with LINKS, VLINKS, PRELIM_FROM, and AGGLINKS set.
# Uses _prelim_html as the base (all cinema rows are preliminary-only).
_al_vlink_url = "https://www.programata.bg/film/test-film"
_al_vlink = _json.dumps([["f0", "cinema0", _al_vlink_url]])
_al_link_url = "https://theatre.art.bg/spectakyl/show-x"
_al_link = _json.dumps([["show-x", _al_link_url]])

def _al_page(agglinks_dict, vlinks=_al_vlink, links=_al_link):
    """Build a page with given AGGLINKS, VLINKS, and LINKS constants."""
    base = _prelim_html   # has PRELIM_FROM so all rows are preliminary
    base = base.replace("const VLINKS=[];", f"const VLINKS={vlinks};")
    base = base.replace("const LINKS=[];", f"const LINKS={links};")
    ag_js = f"const AGGLINKS={_json.dumps(agglinks_dict)};"
    # Inject AGGLINKS via extra_js pattern: append before SOFIA-DATA-END
    return base.replace("/* SOFIA-DATA-END */", f"{ag_js}\n/* SOFIA-DATA-END */")

# 9a: correct page (VLINKS + LINKS entries, hosts match, preliminary pair) → passes
_rc_al_ok, _out_al_ok = _run_out(_al_page({
    "f0|cinema0": "programata.bg",
    "show-x": "theatre.art.bg",
}))
check("AGGLINKS correct page exits 0", _rc_al_ok == 0)

# 9b: key with no corresponding VLINKS entry → fails
_rc_al_nolink, _out_al_nolink = _run_out(_al_page({"f99|cinema0": "programata.bg"}))
check("AGGLINKS key with no VLINKS entry exits non-zero",
      _rc_al_nolink != 0 and ("AGGLINKS" in _out_al_nolink or "key" in _out_al_nolink.lower()))

# 9c: host mismatch (AGGLINKS says programata.bg but URL is on theatre.art.bg) → fails
_al_mismatch_vlink = _json.dumps([["f0", "cinema0", "https://theatre.art.bg/show/123"]])
_rc_al_host, _out_al_host = _run_out(_al_page({"f0|cinema0": "programata.bg"}, vlinks=_al_mismatch_vlink))
check("AGGLINKS host mismatch exits non-zero",
      _rc_al_host != 0 and ("AGGLINKS" in _out_al_host or "host" in _out_al_host.lower()))

# 9d: confirmed pair in AGGLINKS → fails
# _confirmed_html has PRELIM_FROM set to far future so all cinema0 rows are confirmed.
_al_confirmed_vlink = _json.dumps([["f0", "cinema0", _al_vlink_url]])
_rc_al_conf, _out_al_conf = _run_out(
    _confirmed_html
    .replace("const VLINKS=[];", f"const VLINKS={_al_confirmed_vlink};")
    .replace("const LINKS=[];", f"const LINKS={_al_link};")
    .replace("/* SOFIA-DATA-END */",
             f'const AGGLINKS={_json.dumps({"f0|cinema0": "programata.bg"})};\n/* SOFIA-DATA-END */')
)
check("AGGLINKS confirmed pair exits non-zero",
      _rc_al_conf != 0 and ("AGGLINKS" in _out_al_conf or "confirmed" in _out_al_conf.lower()))

# 9e: unrecognised aggregator value → fails
_rc_al_badval, _out_al_badval = _run_out(_al_page({"f0|cinema0": "evil.com"}))
check("AGGLINKS unrecognised aggregator value exits non-zero",
      _rc_al_badval != 0 and ("AGGLINKS" in _out_al_badval or "aggregator" in _out_al_badval.lower()))


# ---------------------------------------------------------------------------
# P3A: PREMIERES integrity
# ---------------------------------------------------------------------------
print("\nP3A: PREMIERES integrity")

# Helper: build a page with a PREMIERES constant injected via extra_js
def _with_premieres(prem_dict, extra_prelim=None, extra_perfs=None, extra_st=None):
    """Minimal page with a custom PREMIERES constant (and optional overrides).

    PREMIERES is injected via extra_js; PRELIM_FROM is replaced in the template
    string so verify_build always sees exactly one declaration of each constant.

    When extra_prelim is given, all 6 bulk theatre IDs (th0-th5) are covered
    (far-future default) so the "theatres with performances but no PRELIM_FROM
    entry" gate does not fire on theatres the test does not care about.
    """
    prem_js = f"const PREMIERES={_json.dumps(prem_dict)};"
    pf = _json.dumps(extra_perfs) if extra_perfs is not None else None
    st = _json.dumps(extra_st) if extra_st is not None else None
    html = _minimal_html(
        extra_js=prem_js,
        performances_override=pf or "",
        showtimes_override=st or "",
    )
    if extra_prelim is not None:
        _far_future = (datetime.date.today() + datetime.timedelta(days=365)).isoformat()
        full_prelim = {f"th{i}": _far_future for i in range(6)}
        full_prelim.update(extra_prelim)
        html = html.replace(
            "const PRELIM_FROM={};",
            f"const PRELIM_FROM={_json.dumps(full_prelim)};",
        )
    return html

# Dates used in tests
_PREM_FUTURE = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
_PREM_PAST   = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
_PREM_PRE    = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()  # PRELIM_FROM

# PA-1: show: date in PERFORMANCES, before PRELIM_FROM → passes
_pa1_perfs = _json.loads(_bulk_performances())
_pa1_perfs.append(["show0", _PREM_FUTURE, "19:00", "Основна сцена", ""])
_pa1_rc, _pa1_out = _run_out(_with_premieres(
    {"show:show0": [_PREM_FUTURE]},
    extra_prelim={"th0": _PREM_PRE},
    extra_perfs=_pa1_perfs,
))
check("P3A PREMIERES show date in PERFS before PRELIM_FROM passes", _pa1_rc == 0)

# PA-2: show: date NOT in PERFORMANCES → fails
_pa2_rc, _pa2_out = _run_out(_with_premieres(
    {"show:show0": ["2099-01-01"]},
    extra_prelim={"th0": "2099-06-01"},
))
check("P3A PREMIERES show date missing from PERFORMANCES fails",
      _pa2_rc != 0 and ("PERFORMANCES" in _pa2_out or "integrity" in _pa2_out.lower()))

# PA-3: show: date on or after PRELIM_FROM → fails
_pa3_perfs = _json.loads(_bulk_performances())
_pa3_perfs.append(["show0", _PREM_FUTURE, "19:00", "Основна сцена", ""])
_pa3_prelim_date = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
_pa3_rc, _pa3_out = _run_out(_with_premieres(
    {"show:show0": [_PREM_FUTURE]},
    extra_prelim={"th0": _pa3_prelim_date},
    extra_perfs=_pa3_perfs,
))
check("P3A PREMIERES show date on or after PRELIM_FROM fails",
      _pa3_rc != 0 and ("PRELIM_FROM" in _pa3_out or "integrity" in _pa3_out.lower() or "preliminary" in _pa3_out.lower()))

# PA-4: film: key with SHOWTIMES row at cc-sofia → passes
# cc-sofia must appear in CINEMAS (for SHOWTIMES orphan check) and BOOKING, plus
# SHOWTIMES must have a row for that film at cc-sofia.
_pa4_cinemas_data = _json.loads(_bulk_cinemas())
_pa4_cinemas_data.append({"id": "cc-sofia", "name": "Cinema City София", "coverage": [_FUTURE]})
_pa4_booking_data = _json.loads(_bulk_booking())
_pa4_booking_data["cc-sofia"] = {"label": "Cinema City", "url": "https://www.cinemacity.bg/tickets"}
_pa4_st_data = _json.loads(_bulk_showtimes())
_pa4_st_data.append(["f0", "cc-sofia", _FUTURE, ["18:00"]])
_pa4_extra = f"const PREMIERES={_json.dumps({'film:f0': [_PREM_FUTURE]})};"
_pa4_html = _minimal_html(
    extra_js=_pa4_extra,
    showtimes_override=_json.dumps(_pa4_st_data),
)
# Patch CINEMAS and BOOKING in place (template uses _bulk_* exactly once each)
_pa4_html = _pa4_html.replace(
    f"const CINEMAS={_bulk_cinemas()};",
    f"const CINEMAS={_json.dumps(_pa4_cinemas_data)};",
)
_pa4_html = _pa4_html.replace(
    f"const BOOKING={_bulk_booking()};",
    f"const BOOKING={_json.dumps(_pa4_booking_data)};",
)
_pa4_rc, _pa4_out = _run_out(_pa4_html)
check("P3A PREMIERES film with CC SHOWTIMES passes", _pa4_rc == 0)

# PA-5: film: key with no SHOWTIMES row at CC → fails
_pa5_rc, _pa5_out = _run_out(_with_premieres({"film:f0": [_PREM_FUTURE]}))
check("P3A PREMIERES film without CC SHOWTIMES fails",
      _pa5_rc != 0 and ("SHOWTIMES" in _pa5_out or "cc-" in _pa5_out.lower() or "integrity" in _pa5_out.lower()))


# ---------------------------------------------------------------------------
# FESTMARK: a festival mark names only titles the page lists (owner, 2026-10-15)
# ---------------------------------------------------------------------------
print("\nFESTMARK: festival marks")
def _with_festmark(fm):
    return _minimal_html(extra_js=f"const FESTMARK={_json.dumps(fm, ensure_ascii=False)};")

_fm1_rc, _fm1_out = _run_out(_with_festmark(
    {"kinomania": {"bg": "Киномания", "en": "Kinomania", "films": ["f0", "f1"], "shows": ["show0"]}}))
check("FESTMARK naming listed films and a listed play passes", _fm1_rc == 0)
_fm2_rc, _fm2_out = _run_out(_with_festmark(
    {"kinomania": {"bg": "Киномания", "en": "Kinomania", "films": ["f0", "no-such-film"], "shows": []}}))
check("FESTMARK naming a film with no SHOWTIMES row fails", _fm2_rc != 0 and "FESTMARK" in _fm2_out)
_fm3_rc, _fm3_out = _run_out(_with_festmark({"kinomania": {"films": ["f0"]}}))
check("FESTMARK entry without its names fails", _fm3_rc != 0 and "FESTMARK" in _fm3_out)
_fm4_rc, _fm4_out = _run_out(_minimal_html())
check("a page without FESTMARK passes (marks from the listing titles alone)", _fm4_rc == 0)


# merged film ids (film_merges.json -> FILM_REDIRECT): the old id opens the kept film,
# and the duplicate card the merge removed must not come back
print("merged film ids (FILM_REDIRECT)")
_fr1_rc, _fr1_out = _run_out(_minimal_html(extra_js='const FILM_REDIRECT={"old-dup":"f0"};'))
check("a merged-away id that is gone from FILMS and SHOWTIMES passes", _fr1_rc == 0 and "FILM_REDIRECT: 1" in _fr1_out)
_fr2_rc, _fr2_out = _run_out(_minimal_html(extra_js='const FILM_REDIRECT={"f1":"f0"};'))
check("a merged-away id still listed fails", _fr2_rc != 0 and "merged-away film id" in _fr2_out)
_fr3_rc, _fr3_out = _run_out(_minimal_html(extra_js='const FILM_REDIRECT={"old-dup":"not-listed"};'))
check("…a kept film not listed now is only noted", _fr3_rc == 0 and "kept film is not listed" in _fr3_out)


# ---------------------------------------------------------------------------
# P3C: OG/Twitter meta, icon files, and share page spot checks (6a/6b/6c)
# ---------------------------------------------------------------------------
# These tests use SOFIA_ROOT to point verify_build at a controlled temp tree
# so we can test both positive and negative scenarios independently of the
# live worktree.

import shutil as _shutil
from PIL import Image as _PIL_Image_tv

sys.path.insert(0, str(ROOT / "scripts"))
import gen_share_pages as _GSP


def _share_html(kind: str, item_id: str, og_image: str = "https://image.tmdb.org/t/p/w500/test.jpg") -> str:
    """A share page as gen_share_pages.py writes it (the gate checks the real template)."""
    return _GSP._share_page(kind, item_id, f"Заглавие {item_id}", "", "Описание.", og_image,
                            _SITE_URL_FOR_TEST, image_w=500, image_h=750)


def _make_file_root(with_icons: bool = True,
                    film_ids_ok: list[str] | None = None,
                    film_ids_relative_og: list[str] | None = None,
                    show_ids_ok: list[str] | None = None,
                    film_dir_exists: bool = True,
                    film_pages: dict[str, str] | None = None) -> pathlib.Path:
    """Create a temp directory tree with icon and share-page fixtures.

    film_pages: {id: html} written as film/<id>/index.html verbatim (broken pages).
    Returns the path; caller is responsible for cleanup with shutil.rmtree.
    """
    root = pathlib.Path(tempfile.mkdtemp(prefix="sv_test_root_"))
    if with_icons:
        icons = root / "icons"
        icons.mkdir()
        for fname, sz in [("apple-touch-icon.png", 180),
                          ("icon-192.png", 192), ("icon-512.png", 512)]:
            img = _PIL_Image_tv.new("RGB", (sz, sz), color=(10, 10, 20))
            img.save(str(icons / fname))
    # Share pages with correct og:url and absolute og:image
    if film_ids_ok:
        for fid in film_ids_ok:
            p = root / "film" / fid
            p.mkdir(parents=True)
            (p / "index.html").write_text(_share_html("film", fid), encoding="utf-8")
    elif film_dir_exists:
        # Create an empty film/ dir so 6c's "directory exists" branch fires
        (root / "film").mkdir(parents=True)
    if film_ids_relative_og:
        for fid in film_ids_relative_og:
            p = root / "film" / fid
            p.mkdir(parents=True)
            (p / "index.html").write_text(_share_html("film", fid, "/share/share-preview.jpg"), encoding="utf-8")
    if show_ids_ok:
        for sid in show_ids_ok:
            p = root / "show" / sid
            p.mkdir(parents=True)
            (p / "index.html").write_text(_share_html("show", sid), encoding="utf-8")
    for fid, page in (film_pages or {}).items():
        p = root / "film" / fid
        p.mkdir(parents=True, exist_ok=True)
        (p / "index.html").write_text(page, encoding="utf-8")
    # Like csp.py leaves them: the CSP meta over each share page's one script.
    for _kind in ("film", "show", "event"):
        for _pg in (root / _kind).glob("*/index.html") if (root / _kind).is_dir() else []:
            _pg.write_text(_csp.stamp(_pg.read_text(encoding="utf-8"), kind="share", strict=False), encoding="utf-8")
    (root / "fonts").mkdir(exist_ok=True)
    (root / "fonts" / "fonts.css").write_text("/* test */\n", encoding="utf-8")
    return root

print("\nP3C: OG/Twitter meta and share page checks")

# 6a negative: page missing OG tags fails
_p3c_noOG_html = textwrap.dedent(f"""\
    <!doctype html><html><head><meta charset="utf-8"/></head><body>
    <script>/* SOFIA-DATA-START */
    const SNAPSHOT={{"window":{{"from":"2026-09-15","to":"2026-12-31"}},"generated":"{_NOW_ISO}"}};
    const CINEMAS={_bulk_cinemas()};
    const FILMS={_bulk_films()};
    const SHOWTIMES={_bulk_showtimes()};
    const THEATRES={_bulk_theatres()};
    const SHOWS={_bulk_shows()};
    const PERFORMANCES={_bulk_performances()};
    const EVENTS=[];
    const BOOKING={_bulk_booking()};
    const TICKET_HUBS=[];
    const FILM_EXTRA={{}};
    const SHOW_EXTRA={{}};
    const TASTE_FILMS=[];
    const TASTE_SHOWS=[];
    const VENUE_UNTIL={{}};
    const LINKS=[];
    const VLINKS=[];
    const PRELIM_FROM={{}};
    /* SOFIA-DATA-END */</script>
    <script>/* SOFIA-POSTERS-START */
    const POSTERS={{}};
    const SHOWART={{}};
    const SYN_EN={_json.dumps({fid: "Synopsis." for fid in [f"f{{i}}" for i in range(20)]})};
    const TITLE_EN={_json.dumps({fid: f"Film {{fid}}" for fid in [f"f{{i}}" for i in range(20)]})};
    const FILMINFO={{}};
    /* SOFIA-POSTERS-END */</script>
    </body></html>
""")
_p3c_root_ok = _make_file_root(with_icons=True,
                                film_ids_ok=["test-film"],
                                show_ids_ok=["test-show"])
try:
    _p3c_noOG_rc, _p3c_noOG_out = _run_out(_p3c_noOG_html,
                                             extra_env={"SOFIA_ROOT": str(_p3c_root_ok)})
    check("missing OG tags fails",
          _p3c_noOG_rc != 0 and ("og:type" in _p3c_noOG_out or "OG/Twitter" in _p3c_noOG_out))
finally:
    _shutil.rmtree(str(_p3c_root_ok), ignore_errors=True)

# 6a negative: relative og:image fails
_p3c_rel_html = _minimal_html().replace(
    f'content="{_SITE_URL_FOR_TEST}/share/share-preview.jpg"',
    'content="/share/share-preview.jpg"',
)
_p3c_root_ok2 = _make_file_root(with_icons=True,
                                 film_ids_ok=["test-film"],
                                 show_ids_ok=["test-show"])
try:
    _p3c_rel_rc, _p3c_rel_out = _run_out(_p3c_rel_html,
                                          extra_env={"SOFIA_ROOT": str(_p3c_root_ok2)})
    check("relative og:image fails",
          _p3c_rel_rc != 0 and ("og:image" in _p3c_rel_out or "absolute" in _p3c_rel_out))
finally:
    _shutil.rmtree(str(_p3c_root_ok2), ignore_errors=True)

# 6c negative: missing share page for a listed film fails
# film/ dir exists with a dir but no index.html inside it
_p3c_root_miss = _make_file_root(with_icons=True,
                                  film_ids_ok=[],
                                  film_dir_exists=True,
                                  show_ids_ok=[])
_p3c_miss_fid = "missing-film"
(_p3c_root_miss / "film" / _p3c_miss_fid).mkdir(parents=True)
# No index.html created — the page is missing
_p3c_miss_rc, _p3c_miss_out = _run_out(_minimal_html(),
                                         extra_env={"SOFIA_ROOT": str(_p3c_root_miss)})
_shutil.rmtree(str(_p3c_root_miss), ignore_errors=True)
check("missing share page for a listed film fails",
      _p3c_miss_rc != 0 and ("share page missing" in _p3c_miss_out or "missing" in _p3c_miss_out))

# 6c negative: share page with relative og:image fails
_p3c_root_relshare = _make_file_root(with_icons=True,
                                      film_ids_relative_og=["rel-film"],
                                      show_ids_ok=[])
_p3c_relshare_rc, _p3c_relshare_out = _run_out(_minimal_html(),
                                                 extra_env={"SOFIA_ROOT": str(_p3c_root_relshare)})
_shutil.rmtree(str(_p3c_root_relshare), ignore_errors=True)
check("share page with relative og:image fails",
      _p3c_relshare_rc != 0 and ("og:image" in _p3c_relshare_out or "absolute" in _p3c_relshare_out))

# 6b/6c positive: good icons + good share pages passes
_p3c_root_full = _make_file_root(with_icons=True,
                                  film_ids_ok=["good-film"],
                                  show_ids_ok=["good-show"])
_p3c_good_rc = _run(_minimal_html(), skip_freshness=True,
                     extra_env={"SOFIA_ROOT": str(_p3c_root_full)})
_shutil.rmtree(str(_p3c_root_full), ignore_errors=True)
check("good icons + good share pages passes", _p3c_good_rc == 0)

# 6c (16 Oct 2026): every share page keeps link-preview fetchers on its own tags
print("\nShare pages: no meta refresh, a bot-aware redirect, the tags, a link")
_good = _share_html("film", "probe")
_sp_cases = {
    "meta refresh": (_good.replace("<script>", '<meta http-equiv="refresh" content="0; url=/?film=probe">\n<script>', 1),
                     "meta refresh"),
    "plain redirect": (_good.replace(_GSP.REDIRECT_JS, '/* SOFIA-SHARE */\nlocation.replace("/home/?film=probe");'),
                       "without skipping link-preview fetchers"),
    "no redirect": (_good.replace("<script>" + _GSP.REDIRECT_JS + "</script>", ""), "no redirect"),
    "no og:title": (_good.replace('property="og:title"', 'property="og:x"'), "og:title"),
    "no twitter:card": (_good.replace('content="summary_large_image"', 'content="summary"'), "twitter:card"),
    "no fallback link": (_good.replace('href="/home/?film=probe"', 'href="/"'), "without JavaScript"),
    "relative twitter:image": (_good.replace('name="twitter:image" content="https://', 'name="twitter:image" content="//'),
                               "twitter:image"),
}
for _label, (_page, _word) in _sp_cases.items():
    _root = _make_file_root(with_icons=True, film_ids_ok=["good-film"], show_ids_ok=["good-show"],
                            film_pages={"probe": _page})
    try:
        _rc, _out = _run_out(_minimal_html(), extra_env={"SOFIA_ROOT": str(_root)})
        check(f"share page with {_label} fails", _rc != 0 and _word in _out)
    finally:
        _shutil.rmtree(str(_root), ignore_errors=True)

# an event page from the generator passes; the generator writes no meta refresh at all
_root = _make_file_root(with_icons=True, film_ids_ok=["good-film"], show_ids_ok=["good-show"])
(_root / "event" / "probe-fest").mkdir(parents=True)
(_root / "event" / "probe-fest" / "index.html").write_text(_csp.stamp(_GSP._event_page(
    {"id": "probe-fest", "title": "Фестивал", "titleEn": "Festival", "from": "2026-10-17", "to": "2026-10-24",
     "blurbBg": "Описание."}, {}, {}, {}, {}, {}, {}, {}), kind="share"), encoding="utf-8")
try:
    _rc = _run(_minimal_html(), skip_freshness=True, extra_env={"SOFIA_ROOT": str(_root)})
    check("a generated event share page passes", _rc == 0)
finally:
    _shutil.rmtree(str(_root), ignore_errors=True)
check("the share page template has no meta refresh",
      "refresh" not in _good.lower() and "facebookexternalhit" in _good and "/?film=probe" in _good)


# ---------------------------------------------------------------------------
# P3C: SITE_URL in page JS matches site_config (check 6a2)
# ---------------------------------------------------------------------------
print("\nP3C: SITE_URL consistency check")

# Positive: correct SITE_URL matches site_config → passes
_p3c_su_ok_root = _make_file_root(with_icons=True,
                                   film_ids_ok=["test-film"],
                                   show_ids_ok=["test-show"])
try:
    _su_ok_rc = _run(_minimal_html(), skip_freshness=True,
                     extra_env={"SOFIA_ROOT": str(_p3c_su_ok_root)})
    check("correct SITE_URL in page JS passes", _su_ok_rc == 0)
finally:
    _shutil.rmtree(str(_p3c_su_ok_root), ignore_errors=True)

# Negative: unreplaced __SITE_URL__ placeholder → fails
_p3c_su_bad_root = _make_file_root(with_icons=True,
                                    film_ids_ok=["test-film"],
                                    show_ids_ok=["test-show"])
try:
    _su_ph_rc, _su_ph_out = _run_out(
        _minimal_html(site_url="__SITE_URL__"),
        extra_env={"SOFIA_ROOT": str(_p3c_su_bad_root)})
    check("unreplaced __SITE_URL__ placeholder fails",
          _su_ph_rc != 0 and ("SITE_URL" in _su_ph_out or "site_config" in _su_ph_out))
finally:
    _shutil.rmtree(str(_p3c_su_bad_root), ignore_errors=True)

# Negative: wrong SITE_URL value → fails
_p3c_su_mism_root = _make_file_root(with_icons=True,
                                     film_ids_ok=["test-film"],
                                     show_ids_ok=["test-show"])
try:
    _su_mism_rc, _su_mism_out = _run_out(
        _minimal_html(site_url="https://wrong.example.com"),
        extra_env={"SOFIA_ROOT": str(_p3c_su_mism_root)})
    check("mismatched SITE_URL fails",
          _su_mism_rc != 0 and ("SITE_URL" in _su_mism_out or "site_config" in _su_mism_out))
finally:
    _shutil.rmtree(str(_p3c_su_mism_root), ignore_errors=True)


# ---------------------------------------------------------------------------
# EVENTS_AUTO / EVENT_MEMBERS (fetch_events.py -> events.json -> inject_data.py)
# ---------------------------------------------------------------------------
print("\nEVENTS_AUTO: aggregator-listed events are well-formed")
_ev_from = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
_ev_to = (datetime.date.today() + datetime.timedelta(days=9)).isoformat()


def _ev_auto(**over):
    ev = {"id": "act-festival-p187545", "kind": "festival", "scope": "theatre", "status": "upcoming",
          "title": "ACT Фестивал за свободен театър", "titleEn": "ACT Festival", "from": _ev_from,
          "to": _ev_to, "g": ["#952343", "#23060E"], "blurbBg": "Фестивал.", "blurbEn": "Festival.",
          "tag": "СЛЕД 2 ДНИ", "source": "programata.bg",
          "url": "https://programata.bg/stsena/ne-se-prav%d1%9d-zapochva-16-izdanie-na-act/", "auto": True}
    ev.update(over)
    return {k: v for k, v in ev.items() if v is not None}


def _ev_page(events, members=None, seed="[]"):
    js = "const EVENTS_AUTO=" + _json.dumps(events, ensure_ascii=False) + ";"
    if members is not None:
        # each const on its own line, indented like the template, so dedent keeps them at column 0
        js += "\n        const EVENT_MEMBERS=" + _json.dumps(members, ensure_ascii=False) + ";"
    html = _minimal_html(extra_js=js)
    return html.replace("const EVENTS=[];", "const EVENTS=" + seed + ";", 1)


def _ev_fails(events, members=None, seed="[]", word="EVENTS_AUTO has"):
    rc, out = _run_out(_ev_page(events, members, seed))
    return rc != 0 and word in out


_rc, _out = _run_out(_ev_page([_ev_auto()], {"act-festival-p187545": ["show0"]}))
check("a well-formed auto event and its members pass", _rc == 0)
if _rc != 0:
    print("      " + "\n      ".join(_out.strip().splitlines()[-4:]))
check("a date that is not ISO fails", _ev_fails([_ev_auto(**{"from": "17.10.2026"})]))
check("from after to fails", _ev_fails([_ev_auto(**{"from": _ev_to, "to": _ev_from})]))
_past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
check("an event that has ended fails", _ev_fails([_ev_auto(**{"from": _past, "to": _past})]))
check("an http (not https) source url fails", _ev_fails([_ev_auto(url="http://programata.bg/stsena/act/")]))
check("a source url off the allowlist fails", _ev_fails([_ev_auto(url="https://example.com/act/")]))
check("a lookalike host fails", _ev_fails([_ev_auto(url="https://programata.bg.evil.example/act/")]))
check("a sofialive.bg source url passes",
      _run_out(_ev_page([_ev_auto(url="https://www.sofialive.bg/arts/theater/x-2637121.html",
                                  source="sofialive.bg")]))[0] == 0)
check("duplicate ids fail", _ev_fails([_ev_auto(), _ev_auto(title="Друго")]))
check("an id the seed EVENTS already uses fails",
      _ev_fails([_ev_auto()], seed='[{"id":"act-festival-p187545","kind":"festival","scope":"theatre",'
                                   '"title":"ACT","titleEn":"ACT","from":"2026-10-17","to":"2026-10-24",'
                                   '"g":["#000000","#111111"]}]'))
check("an invalid scope fails", _ev_fails([_ev_auto(scope="music")]))
check("an invalid kind fails", _ev_fails([_ev_auto(kind="concert")]))
check("missing colours fail", _ev_fails([_ev_auto(g=None)]))
check("auto not true fails", _ev_fails([_ev_auto(auto=False)]))
check("Cyrillic in titleEn fails (English mode shows none)",
      _ev_fails([_ev_auto(titleEn="ACT Фестивал за свободен театър")]))
check("EVENT_MEMBERS naming no film or show fails",
      _ev_fails([_ev_auto()], {"act-festival-p187545": ["no-such-show"]}, word="EVENT_MEMBERS has"))
check("EVENT_MEMBERS for no such event fails",
      _ev_fails([_ev_auto()], {"no-such-event": ["show0"]}, word="EVENT_MEMBERS has"))
check("no EVENTS_AUTO at all is a note, not a failure", _run_out(_minimal_html())[0] == 0)


# ---------------------------------------------------------------------------
# EVENTART (fetch_event_posters.py -> event_posters.json + posters/ev-*)
# ---------------------------------------------------------------------------
print("\nEVENTART: event key art is re-hosted, exists and has an allowed source")
_ea_root = _make_file_root(with_icons=True, film_ids_ok=["test-film"], show_ids_ok=["test-show"])
(_ea_root / "posters").mkdir()
for _n in ("ev-act-festival-p187545.jpg", "ev-cinelibri.jpg"):
    _PIL_Image_tv.new("RGB", (800, 450), color=(150, 30, 40)).save(str(_ea_root / "posters" / _n), "JPEG")
_ea_rec = {"file": "posters/ev-act-festival-p187545.jpg", "via": "programata",
           "src": "https://programata.bg/wp-content/uploads/2026/10/act_fest_cover-1024x576.png",
           "page": "https://programata.bg/stsena/act/", "w": 1024, "h": 576, "to": _ev_to}
_ea_seed = ('[{"id":"cinelibri","kind":"festival","scope":"cinema","title":"Синелибри","titleEn":"CineLibri",'
            '"from":"2026-10-15","to":"2026-11-08","g":["#B3122D","#26040A"]}]')


def _ea_run(art, recs, seed="[]"):
    (_ea_root / "event_posters.json").write_text(_json.dumps({"events": recs}), encoding="utf-8")
    js = ("const EVENTS_AUTO=" + _json.dumps([_ev_auto()], ensure_ascii=False) + ";"
          + "\n        const EVENTART=" + _json.dumps(art) + ";")
    html = _minimal_html(extra_js=js).replace("const EVENTS=[];", "const EVENTS=" + seed + ";", 1)
    return _run_out(html, extra_env={"SOFIA_ROOT": str(_ea_root)})


def _ea_fails(art, recs, seed="[]"):
    rc, out = _ea_run(art, recs, seed)
    return rc != 0 and "EVENTART has" in out


try:
    _ok_art = {"act-festival-p187545": "posters/ev-act-festival-p187545.jpg"}
    _rc, _out = _ea_run(_ok_art, {"act-festival-p187545": _ea_rec})
    check("an auto event's programata art, re-hosted, passes", _rc == 0)
    if _rc != 0:
        print("      " + "\n      ".join(_out.strip().splitlines()[-4:]))
    _site_rec = {"file": "posters/ev-cinelibri.jpg", "via": "site", "page": "https://www.cinelibri.com/",
                 "src": "https://www.cinelibri.com/wp-content/uploads/2026/06/website-key-visual-2026.jpg"}
    check("a seed festival's art from its own site passes",
          _ea_run({"cinelibri": "posters/ev-cinelibri.jpg"}, {"cinelibri": _site_rec}, _ea_seed)[0] == 0)
    check("a missing file fails",
          _ea_fails({"act-festival-p187545": "posters/ev-gone.jpg"},
                    {"act-festival-p187545": dict(_ea_rec, file="posters/ev-gone.jpg")}))
    check("an entry for no event fails", _ea_fails({"no-such-event": _ok_art["act-festival-p187545"]},
                                                     {"no-such-event": _ea_rec}))
    check("an entry with no record in event_posters.json fails", _ea_fails(_ok_art, {}))
    check("a source that is not programata.bg (via programata) fails",
          _ea_fails(_ok_art, {"act-festival-p187545": dict(_ea_rec, src="https://example.com/act.jpg")}))
    check("a seed festival's art from another site fails",
          _ea_fails({"cinelibri": "posters/ev-cinelibri.jpg"},
                    {"cinelibri": dict(_site_rec, src="https://www.kinomania.bg/x.jpg")}, _ea_seed))
    check("a remote URL instead of a re-hosted file fails",
          _ea_fails({"act-festival-p187545": "https://programata.bg/act.png"},
                    {"act-festival-p187545": dict(_ea_rec, file="https://programata.bg/act.png")}))
    check("a file that is not a picture fails",
          (_ea_root / "posters" / "ev-text.jpg").write_text("<html>", encoding="utf-8") is not None
          and _ea_fails({"act-festival-p187545": "posters/ev-text.jpg"},
                        {"act-festival-p187545": dict(_ea_rec, file="posters/ev-text.jpg")}))
finally:
    _shutil.rmtree(str(_ea_root), ignore_errors=True)
# Legal pages and the cookie notice (6e)
# ---------------------------------------------------------------------------
# A page with the cookie notice (CONSENT_VERSION) gets the legal checks; a temp
# SOFIA_ROOT holds copies of this worktree's legal/ pages, edited per probe.
print("\nLegal pages: placeholders, TMDB logo, storage list, scripts, CSP, fonts")
import re
_LG_VERSION = re.search(r'(?m)^const CONSENT_VERSION="([^"]+)";',
                        (ROOT / "src" / "sofia-screen.artifact.html").read_text(encoding="utf-8")).group(1)
_LG_JS = (f'const CONSENT_VERSION="{_LG_VERSION}"; const LS="sofia-screen-v2"; const CONSENT_KEY="sofia-consent"; '
          'function _lgUse(){localStorage.getItem(LS);localStorage.setItem(CONSENT_KEY,"x");} '
          '/* links: href="legal/terms/" legal/en/ */')
_LG_CSP = ('<meta http-equiv="Content-Security-Policy" content="default-src \'self\'; '
           'script-src \'sha256-AAAA\'; img-src \'self\' https: data:; object-src \'none\'">')


def _lg_root(logo=True, edit=None):
    """A temp tree: icons, share pages, sw.js and this worktree's legal pages."""
    root = _make_file_root(with_icons=True, film_ids_ok=["lg-film"], show_ids_ok=["lg-show"])
    _shutil.copytree(ROOT / "legal", root / "legal")
    _shutil.copy(ROOT / "sw.js", root / "sw.js")
    if logo:
        (root / "icons" / "tmdb.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    for p in (root / "legal").rglob("index.html"):
        t = p.read_text(encoding="utf-8")
        if logo and "/terms/" in str(p):
            t = t.replace('<div class="lg-attr">', '<div class="lg-attr"><img src="../../icons/tmdb.svg" alt="TMDB">', 1)
        if edit:
            t = edit(p.relative_to(root).as_posix(), t)
        p.write_text(t, encoding="utf-8")
    return root


def _lg_run(html_text, root, owner_ok="0"):
    try:
        return _run_out(html_text, extra_env={"SOFIA_ROOT": str(root), "SOFIA_OWNER_PENDING_OK": owner_ok})
    finally:
        _shutil.rmtree(str(root), ignore_errors=True)


_lg_page = _minimal_html(extra_js=_LG_JS)
_rc, _out = _lg_run(_lg_page, _lg_root())
check("complete legal pages pass", _rc == 0)
if _rc != 0:
    print("      " + "\n      ".join(_out.strip().splitlines()[-4:]))
_ph = lambda r, t: t.replace("</main>", "<p>{CONTACT_EMAIL}</p></main>") if r == "legal/privacy/index.html" else t
_rc, _out = _lg_run(_lg_page, _lg_root(edit=_ph))
check("a leftover {CONTACT_EMAIL} placeholder fails the gate", _rc != 0 and "{CONTACT_EMAIL}" in _out)
_rc, _out = _lg_run(_lg_page, _lg_root(edit=_ph), owner_ok="1")
check("a leftover placeholder fails even with SOFIA_OWNER_PENDING_OK=1", _rc != 0 and "{CONTACT_EMAIL}" in _out)
_gh = lambda r, t: (t.replace("</footer>", '<a href="https://github.com/someone/sofia-gleda/issues">GitHub</a></footer>')
                    if r == "legal/en/cookies/index.html" else t)
_rc, _out = _lg_run(_lg_page, _lg_root(edit=_gh))
check("a GitHub issues link on a legal page fails (no contact channel)", _rc != 0 and "no contact channel" in _out)
_rc, _out = _lg_run(_lg_page.replace("</body>", '<a href="https://github.com/someone/sofia-gleda">GitHub</a></body>'),
                    _lg_root())
check("a GitHub project link in the app fails", _rc != 0 and "no contact channel" in _out)
_rc, _out = _lg_run(_lg_page, _lg_root(logo=False))
check("a missing TMDB logo fails the gate", _rc != 0 and "owner input pending" in _out and "TMDB's logo is missing" in _out)
check("SOFIA_OWNER_PENDING_OK=1 turns the missing TMDB logo into a note",
      _lg_run(_lg_page, _lg_root(logo=False), owner_ok="1")[0] == 0)
_rc, _out = _lg_run(_lg_page.replace("</body>", "<p>{OPERATOR_NAME}</p></body>"), _lg_root())
check("a dropped placeholder ({OPERATOR_NAME}) fails", _rc != 0 and "OPERATOR_NAME" in _out)
_miss = _lg_root()
_shutil.rmtree(str(_miss / "legal" / "en" / "privacy"))
_rc, _out = _lg_run(_lg_page, _miss)
check("a missing legal page fails", _rc != 0 and "legal page missing: legal/en/privacy/" in _out)
_rc, _out = _lg_run(_lg_page, _lg_root(edit=lambda r, t: t.replace("</body>", "<script>1</script></body>")
                                       if r == "legal/terms/index.html" else t))
check("a script in a legal page fails", _rc != 0 and "carries a script" in _out)
_rc, _out = _lg_run(_lg_page, _lg_root(edit=lambda r, t: t.replace("<main", '<main onclick="x()"')
                                       if r == "legal/en/terms/index.html" else t))
check("an inline event handler in a legal page fails", _rc != 0 and "inline event handler" in _out)
_rc, _out = _lg_run(_lg_page.replace("function _lgUse(){", 'function _lgUse(){localStorage.setItem("sofia-new",1);'),
                    _lg_root())
check("a localStorage key the cookie policy does not list fails", _rc != 0 and "'sofia-new'" in _out)
_rc, _out = _lg_run(_lg_page.replace("function _lgUse(){", 'function _lgUse(){document.cookie="a=1";'), _lg_root())
check("a page that sets a cookie fails", _rc != 0 and "sets a cookie" in _out)
_rc, _out = _lg_run(_lg_page, _lg_root(edit=lambda r, t: t.replace(f'<time datetime="{_LG_VERSION}">',
                                                                 '<time datetime="2000-01-01">')))
check("policies dated other than CONSENT_VERSION fail", _rc != 0 and "CONSENT_VERSION" in _out)
_rc, _out = _lg_run(_lg_page, _lg_root(edit=lambda r, t: t.replace('rel="canonical" href="https://',
                                                                 'rel="canonical" href="http://')))
check("a canonical URL off SITE_URL fails", _rc != 0 and "canonical URL" in _out)
# The app page and the built legal pages both carry csp.py's policy (the legal pages narrow
# script-src to 'none'); each case below changes only the legal pages.
_CSP_META = re.compile(r'<meta http-equiv="Content-Security-Policy"[^>]*>\n?')
_rc, _out = _lg_run(_lg_page, _lg_root(edit=lambda r, t: _CSP_META.sub("", t)))
check("a CSP on the app but not on the legal pages fails", _rc != 0 and "Content-Security-Policy missing" in _out)
_rc, _out = _lg_run(_lg_page, _lg_root())
check("the same CSP (script-src narrowed to 'none') passes", _rc == 0)
if _rc != 0:
    print("      " + "\n      ".join(_out.strip().splitlines()[-3:]))
_rc, _out = _lg_run(_lg_page, _lg_root(edit=lambda r, t: _CSP_META.sub(
    lambda m: re.sub(r"img-src [^;\"]*", "img-src *", m.group(0)), t)))
check("a CSP that differs from the app's fails", _rc != 0 and "differs from the app's in img-src" in _out)
# the pages use the self-hosted fonts; a privacy policy that still names Google Fonts is out of step
_rc, _out = _lg_run(_lg_page, _lg_root(edit=lambda r, t: t.replace("</main>", "<p>Шрифтовете се зареждат от Google Fonts.</p></main>", 1)
                                       if "/privacy/" in r else t))
check("a privacy policy naming Google Fonts the pages no longer load fails", _rc != 0 and "Google Fonts" in _out)
check("a page without the cookie notice skips the legal checks", _run_out(_minimal_html())[0] == 0)


# Security: the CSP meta, its hashes, the known scripts (scripts/csp.py)
# ---------------------------------------------------------------------------
print("\nsecurity: Content-Security-Policy and links")
_sec_root = _make_file_root(with_icons=True, film_ids_ok=["test-film"], show_ids_ok=["test-show"])
_sec_env = {"SOFIA_ROOT": str(_sec_root)}
try:
    _good = _finish(_minimal_html())
    _rc, _out = _run_out(_good, extra_env=_sec_env, finish=False)
    check("a stamped page passes the CSP checks", _rc == 0)
    if _rc:
        print("      " + "\n      ".join(_out.strip().splitlines()[-4:]))
    _rc, _out = _run_out(_csp.META_RE.sub("", _good), extra_env=_sec_env, finish=False)
    check("a page without the CSP meta fails", _rc != 0 and "Content-Security-Policy meta" in _out)
    _rc, _out = _run_out(_good.replace("const LINKS=[];", "const LINKS=[];const X=1;", 1),
                         extra_env=_sec_env, finish=False)
    check("a data edit after stamping (stale hash) fails", _rc != 0 and "script-src does not match" in _out)
    _inj = _minimal_html().replace("const LINKS=[];", 'const LINKS=[["x","</script><script>alert(1)//"]];', 1)
    _rc, _out = _run_out(_inj, extra_env=_sec_env)              # stamped non-strictly, as an attacker would want
    check("a value that closes the data script fails (data cut short + unknown script)",
          _rc != 0 and "ends before /* SOFIA-DATA-END */" in _out and "unknown inline script" in _out)
    _rc, _out = _run_out(_good.replace("</body>", "<script>alert(1)</script></body>", 1),
                         extra_env=_sec_env)
    check("an extra inline script (even with its hash allowed) fails", _rc != 0 and "unknown inline script" in _out)
    _rc, _out = _run_out(_good.replace("'unsafe-inline';", "'unsafe-inline' https:;", 1)
                         .replace("script-src ", "script-src 'unsafe-inline' ", 1), extra_env=_sec_env, finish=False)
    check("script-src 'unsafe-inline' fails", _rc != 0 and "script-src allows 'unsafe-inline'" in _out)
    _rc, _out = _run_out(_good.replace("object-src 'none'", "object-src *", 1), extra_env=_sec_env, finish=False)
    check("a weakened directive fails", _rc != 0 and "object-src" in _out)
    _rc, _out = _run_out(_good.replace('<meta name="referrer" content="strict-origin-when-cross-origin">', "", 1),
                         extra_env=_sec_env, finish=False)
    check("a page without the referrer policy fails", _rc != 0 and "referrer policy" in _out)
    _rc, _out = _run_out(_minimal_html().replace("</head>", '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Onest"></head>', 1),
                         extra_env=_sec_env)
    check("a page that loads Google Fonts fails", _rc != 0 and "fonts.googleapis.com" in _out)
    _rc, _out = _run_out(_minimal_html(extra_js='const A=`<a href="x" target="_blank" rel="noopener">`;'),
                         extra_env=_sec_env)
    check("a new-tab link without noreferrer fails", _rc != 0 and "noopener noreferrer" in _out)
    _rc, _out = _run_out(_minimal_html(extra_js='const B=`<img src="x" onerror="this.remove()">`;'),
                         extra_env=_sec_env)
    check("an inline event handler fails", _rc != 0 and "inline event handler" in _out)
    _rc, _out = _run_out(_minimal_html().replace('"id": "f0"', '"id": "f0\\"><img src=x>"', 1), extra_env=_sec_env)
    check("an id that is not a slug fails", _rc != 0 and "not slugs" in _out)
    _rc, _out = _run_out(_minimal_html().replace('["10:00"]', '["10:00<b>"]', 1), extra_env=_sec_env)
    check("a showtime that is not HH:MM fails", _rc != 0 and "not HH:MM" in _out)
    # a share page whose policy is missing or stale
    _sp = _sec_root / "film" / "test-film" / "index.html"
    _orig = _sp.read_text(encoding="utf-8")
    _sp.write_text(_csp.META_RE.sub("", _orig), encoding="utf-8")
    _rc, _out = _run_out(_good, extra_env=_sec_env, finish=False)
    check("a share page without its CSP meta fails", _rc != 0 and "share page(s) fail" in _out)
    # the script is the same on every page now (its target comes from the path, 16 Oct 2026),
    # so the edit goes into the script itself: its hash no longer matches the policy
    assert "location.replace(" in _orig
    _sp.write_text(_orig.replace("location.replace(", "location.assign(", 1), encoding="utf-8")
    _rc, _out = _run_out(_good, extra_env=_sec_env, finish=False)
    check("a share page with a stale hash fails", _rc != 0 and "share page(s) fail" in _out)
    _sp.write_text(_orig, encoding="utf-8")
finally:
    _shutil.rmtree(str(_sec_root), ignore_errors=True)

# csp.json_for_script keeps every value inside its script
_v = {"t": "</script><script>alert(1)</script><!-- x", "u": "https://a/b?c=<d>"}
_lit = _csp.json_for_script(_v)
check("json_for_script: no '<' survives", "<" not in _lit)
check("json_for_script: reads back identically", _json.loads(_lit) == _v)
try:
    _csp.stamp("<html><head><meta charset=\"utf-8\"></head><body><script>/* SOFIA-DATA-START */\n"
               "const X=\"</script><script>alert(1)</script>\";\n/* SOFIA-DATA-END */</script></body></html>")
    check("csp.stamp refuses a page whose data script was closed early", False)
except _csp.CSPError:
    check("csp.stamp refuses a page whose data script was closed early", True)


# ---------------------------------------------------------------------------
# Trailers (5a3) and the CSP's frame-src (6e)
# ---------------------------------------------------------------------------
print("\nTrailers: TMDBART v, the year guard, nothing from YouTube before the tap, frame-src")


def _tr_page(art, extra_js=""):
    return _minimal_html(extra_js=extra_js).replace(
        "const FILMINFO={};", "const FILMINFO={};\nconst TMDBART=" + _json.dumps(art) + ";", 1)


_tr_ok = {"f0": {"y": 2026, "v": {"k": "jxU8FU3o75A", "l": "en", "t": "Trailer"}},
          "f1": {"y": 2025, "v": {"k": "abc_DEF-123", "l": "bg", "t": "Teaser"}}}
_rc, _out = _run_out(_tr_page(_tr_ok))
check("the gate reads TMDBART and counts the trailers", "2 film(s) carry a trailer" in _out)
check("a trailer with an 11-character key, bg/en, Trailer/Teaser and the film's year (±1) passes", _rc == 0)
if _rc != 0:
    print("      " + "\n      ".join(_out.strip().splitlines()[-4:]))
for _name, _art in (("a key that is not a YouTube id fails", {"f0": {"y": 2026, "v": {"k": "jxU8FU3o75A/x", "l": "en", "t": "Trailer"}}}),
                    ("a language other than bg/en fails", {"f0": {"y": 2026, "v": {"k": "jxU8FU3o75A", "l": "de", "t": "Trailer"}}}),
                    ("a type other than Trailer/Teaser fails", {"f0": {"y": 2026, "v": {"k": "jxU8FU3o75A", "l": "en", "t": "Clip"}}}),
                    ("a TMDB year 26 years from the film's (a wrong match) fails", {"f0": {"y": 2000, "v": {"k": "jxU8FU3o75A", "l": "en", "t": "Trailer"}}}),
                    ("a trailer without TMDB's year fails", {"f0": {"v": {"k": "jxU8FU3o75A", "l": "en", "t": "Trailer"}}})):
    _rc, _out = _run_out(_tr_page(_art))
    check(_name, _rc != 0 and "trailer(s) that must not ship" in _out)
_rc, _out = _run_out(_tr_page(_tr_ok, extra_js='const X="https://i.ytimg.com/vi/jxU8FU3o75A/hqdefault.jpg";'))
check("a YouTube thumbnail in the page (a Google request before the tap) fails",
      _rc != 0 and "nothing from YouTube or Google may load before the play button" in _out)
_rc, _out = _run_out(_tr_page(_tr_ok, extra_js='const Y="https://www.youtube.com/embed/";'))
check("the tracking embed host (www.youtube.com/embed) instead of youtube-nocookie fails",
      _rc != 0 and "www.youtube.com/embed" in _out)
_rc, _out = _run_out(_tr_page({"f0": {"y": 2026, "nb": {"Roger Deakins": "Роджър Дийкинс"}}}))
check("TMDB's Bulgarian person names pass", _rc == 0)
for _name, _nb in (("a Russian form (ы/э) as a Bulgarian name fails", {"Scott Teems": "Скотт Тимс Мэйсон"}),
                   ("a Latin name as a Bulgarian name fails", {"Leigh Whannell": "Leigh Whannell"})):
    _rc, _out = _run_out(_tr_page({"f0": {"y": 2026, "nb": _nb}}))
    check(_name, _rc != 0 and "not Bulgarian Cyrillic" in _out)
_tr_stamped = _finish(_tr_page(_tr_ok))
check("the page's CSP allows exactly one frame host, www.youtube-nocookie.com",
      "frame-src https://www.youtube-nocookie.com;" in _tr_stamped)
_rc, _out = _run_out(_tr_stamped.replace("; frame-src https://www.youtube-nocookie.com", ""), finish=False)
check("a CSP without frame-src (the player would be blocked) fails", _rc != 0 and "frame-src" in _out)
_rc, _out = _run_out(_tr_stamped.replace("frame-src https://www.youtube-nocookie.com",
                                         "frame-src https://www.youtube-nocookie.com https://www.youtube.com"), finish=False)
check("a CSP that also allows www.youtube.com frames fails", _rc != 0 and "frame-src" in _out)

# ---------------------------------------------------------------------------
# 5a: ids are lower-case ASCII slugs; local poster paths pass the browser's rule
# ---------------------------------------------------------------------------
print("5a: ASCII ids and loadable poster paths")
_base5a = _minimal_html()
_rc, _out = _run_out(_base5a.replace('"id": "show3"', '"id": "showк3"', 1)
                     .replace('["show3",', '["showк3",'))
check("a non-ASCII SHOWS id fails 5a ids", _rc != 0 and "5a ids" in _out)
_rc, _out = _run_out(_base5a.replace("const SHOWART={};", 'const SHOWART={"show1":"posters/show1 x.jpg"};'))
check("a SHOWART path the JS safeImg rule refuses fails 5a", _rc != 0 and "safeImg" in _out)
_rc, _out = _run_out(_base5a.replace("const SHOWART={};", 'const SHOWART={"show1":"posters/no-such-file-5a.jpg"};'))
check("a SHOWART path that is not on disk fails 5a", _rc != 0 and "not on disk" in _out)
# The gate's copy of the browser rule is the browser's rule, character for character.
import re as _re5a
_art = (ROOT / "src" / "sofia-screen.artifact.html").read_text(encoding="utf-8")
_m5a = _re5a.search(r"const safeImg=.*?return /(\^\[[^\n]*?\]\+\$)/\.test\(s\)", _art, _re5a.S)
_vb = (ROOT / "scripts" / "posterpolicy.py").read_text(encoding="utf-8")
_m5b = _re5a.search(r'^JS_SAFEIMG_PATH = r"(.*)"$', _vb, _re5a.M)
check("posterpolicy's JS_SAFEIMG_PATH (used by verify_build 5a/5c) equals the regex in safeImg()",
      bool(_m5a and _m5b) and _m5a.group(1) == _m5b.group(1))

# ---------------------------------------------------------------------------
# 5c2: poster coverage of the upcoming shows (counted as the browser does)
# ---------------------------------------------------------------------------
print("5c2: poster coverage")
_cov_shows = [{"id": f"show{i}", "title": f"Спектакъл {i}", "titleEn": f"Show {i}", "theatre": f"th{i % 6}"}
              for i in range(120)]
_cov_perfs = [[f"show{i}", (datetime.date.today() + datetime.timedelta(days=1 + i // 4)).isoformat(),
               ["10:00", "14:00", "18:00", "20:00"][i % 4], "Основна сцена", ""] for i in range(120)]
_cov_base = (_minimal_html(performances_override=_json.dumps(_cov_perfs))
             .replace("const SHOWS=" + _bulk_shows() + ";", "const SHOWS=" + _json.dumps(_cov_shows) + ";"))
_all_art = {f"show{i}": f"https://theatre.peakview.bg/theatre/photos/BIG{i}PLAKAT.jpg" for i in range(120)}
_rc, _out = _run_out(_cov_base.replace("const SHOWART={};", "const SHOWART=" + _json.dumps(_all_art) + ";"))
check("every upcoming show with a poster passes and prints POSTER_COVERAGE 120/120",
      _rc == 0 and '"upcoming": 120' in _out and '"with": 120' in _out)
_some_art = dict(list(_all_art.items())[:100])
_rc, _out = _run_out(_cov_base.replace("const SHOWART={};", "const SHOWART=" + _json.dumps(_some_art) + ";"))
check("coverage below 90 % (100/120) fails 5c2", _rc != 0 and "below 90 %" in _out)
_bad_art = dict(_all_art, show5="posters/show5 x.jpg")
_rc, _out = _run_out(_cov_base.replace("const SHOWART={};", "const SHOWART=" + _json.dumps(_bad_art) + ";"))
check("…a path the browser refuses counts as missing", '"with": 119' in _out)

# ---------------------------------------------------------------------------
# 4c2a: BOXONLY — performances a theatre sells only at its box office
# ---------------------------------------------------------------------------
print("4c2a: BOXONLY")
_bo_fn = "function boxOnly(k,i,v,d,t){return null;}"


def _bo_page(entries_js, with_fn=True):
    """The minimal page with BOXONLY declared and, unless with_fn is False, the app's
    boxOnly() on a line of its own in the same script."""
    h = _minimal_html(extra_js=f"const BOXONLY={entries_js};")
    return h.replace("/* SOFIA-DATA-END */", f"{_bo_fn}\n/* SOFIA-DATA-END */", 1) if with_fn else h


_bo_ok = _json.dumps([["show0", _FUTURE, "10:00", None, "https://th0.example.bg/play/1"]])
_rc, _out = _run_out(_bo_page(_bo_ok))
check("a BOXONLY entry naming a real performance exits 0 (and is counted)",
      _rc == 0 and "BOXONLY: 1 performance(s)" in _out)
check("an empty BOXONLY exits 0 (no boxOnly() needed)",
      _run(_minimal_html(extra_js="const BOXONLY=[];"), skip_freshness=True) == 0)
_bo_lost = _json.dumps([["show0", _FUTURE, "23:59", None, None]])
_rc, _out = _run_out(_bo_page(_bo_lost))
check("a BOXONLY entry with no PERFORMANCES row exits non-zero",
      _rc != 0 and "name no PERFORMANCES row" in _out)
_bo_js = _json.dumps([["show0", _FUTURE, "10:00", "javascript:alert(1)", None]])
_rc, _out = _run_out(_bo_page(_bo_js))
check("a BOXONLY alt link that is not http(s) exits non-zero",
      _rc != 0 and "BOXONLY entr(ies) are not" in _out)
_bo_short = _json.dumps([["show0", _FUTURE, "10:00"]])
_rc, _out = _run_out(_bo_page(_bo_short))
check("a BOXONLY entry without its alt and page slots exits non-zero",
      _rc != 0 and "BOXONLY entr(ies) are not" in _out)
_bo_dup = _json.dumps([["show0", _FUTURE, "10:00", None, None]] * 2)
_rc, _out = _run_out(_bo_page(_bo_dup))
check("a duplicate BOXONLY entry exits non-zero", _rc != 0 and "duplicate BOXONLY" in _out)
_rc, _out = _run_out(_bo_page(_bo_ok, with_fn=False))
check("BOXONLY entries in an app without boxOnly() exit non-zero (they would offer Купи билет)",
      _rc != 0 and "no boxOnly()" in _out)

# ---------------------------------------------------------------------------
# 4c2a2: FESTROWS — festival screenings at box-office-only cinemas (Одеон, G8)
# ---------------------------------------------------------------------------
print("4c2a2: FESTROWS")
_fr_fn = "function festSaleUrl(i,v,d,t){return '';}"
_C0 = '"cinema0": {"label": "CC0", "url": "https://example0.bg/"}'


def _fr_page(entries_js, with_fn=True, c0_url="https://example0.bg/"):
    """cinema0 sells its own programme only at its box office (BOOKING inPerson)."""
    h = _minimal_html(extra_js=f"const FESTROWS={entries_js};")
    assert _C0 in h
    h = h.replace(_C0, '"cinema0": {"label": "CC0", "url": "%s", "inPerson": true}' % c0_url, 1)
    return h.replace("/* SOFIA-DATA-END */", f"{_fr_fn}\n/* SOFIA-DATA-END */", 1) if with_fn else h


_fr_ok = _json.dumps([["f0", "cinema0", _FUTURE, "10:00", "cinelibri", "https://epaygo.bg/1050441144"]])
_rc, _out = _run_out(_fr_page(_fr_ok))
check("a FESTROWS entry for a screening at a box-office cinema exits 0 (and is counted)",
      _rc == 0 and "FESTROWS: 1 festival" in _out)
_rc, _out = _run_out(_fr_page(_json.dumps([["f0", "cinema0", _FUTURE, "10:00", "cinelibri", None]])))
check("a FESTROWS entry without a link exits 0", _rc == 0)
_rc, _out = _run_out(_fr_page(_json.dumps([["f1", "cinema1", _FUTURE, "10:00", "cinelibri", None]])))
check("a FESTROWS entry at a cinema that sells online anyway exits non-zero",
      _rc != 0 and "sells online anyway" in _out)
_rc, _out = _run_out(_fr_page(_json.dumps([["f0", "cinema0", _FUTURE, "23:59", "cinelibri", None]])))
check("a FESTROWS entry with no SHOWTIMES row exits non-zero", _rc != 0 and "name no SHOWTIMES row" in _out)
_rc, _out = _run_out(_fr_page(_json.dumps([["f0", "cinema0", _FUTURE, "10:00", "cinelibri", "https://example0.bg/x"]])))
check("a FESTROWS link that is not a festival or ticket-shop page exits non-zero",
      _rc != 0 and "FESTROWS entr(ies) are not" in _out)
_rc, _out = _run_out(_fr_page(_fr_ok, with_fn=False))
check("FESTROWS entries in an app without festSaleUrl() exit non-zero", _rc != 0 and "no festSaleUrl()" in _out)
# the sale state (7th field, 2026-10-29): onsale / soldout / notyet / null
_fr7 = lambda st: _json.dumps([["f0", "cinema0", _FUTURE, "10:00", "cinelibri", "https://epaygo.bg/1050441144", st]])
_fr_fn_both = _fr_fn + "\nfunction festSaleState(i,v,d,t){return '';}"
_fr_fn0 = _fr_fn
_fr_fn = _fr_fn_both
for _st in ("soldout", "notyet", "onsale", None):
    _rc, _out = _run_out(_fr_page(_fr7(_st)))
    check(f"a FESTROWS entry with sale state {_st!r} exits 0", _rc == 0)
_rc, _out = _run_out(_fr_page(_fr7("soldout")))
check("a sold-out FESTROWS entry is counted", "1 sold out, 0 not on sale yet" in _out)
_rc, _out = _run_out(_fr_page(_fr7("maybe")))
check("a FESTROWS sale state outside onsale/soldout/notyet exits non-zero", _rc != 0 and "FESTROWS entr(ies) are not" in _out)
# the day the state was read (9th field, 2026-10-29): YYYY-MM-DD or null
_fr9 = lambda rd: _json.dumps([["f0", "cinema0", _FUTURE, "10:00", "cinelibri", "https://epaygo.bg/1050441144", "onsale",
                                None, rd]])
for _rd in ("2026-10-29", None):
    _rc, _out = _run_out(_fr_page(_fr9(_rd)))
    check(f"a FESTROWS entry read on {_rd!r} (9th field) exits 0", _rc == 0)
_rc, _out = _run_out(_fr_page(_fr9("yesterday")))
check("a FESTROWS read day that is not YYYY-MM-DD exits non-zero", _rc != 0 and "FESTROWS entr(ies) are not" in _out)
_fr_fn = _fr_fn0
_rc, _out = _run_out(_fr_page(_fr7("notyet")))
check("a not-on-sale FESTROWS entry in an app without festSaleState() exits non-zero",
      _rc != 0 and "no festSaleState()" in _out)
_rc, _out = _run_out(_fr_page(_fr7("onsale")))
check("an on-sale FESTROWS entry needs no festSaleState()", _rc == 0)
_rc, _out = _run_out(_fr_page("[]", c0_url="https://example0.bg/#buy-tickets"))
check("a box-office cinema whose BOOKING url is a '#buy' ticket anchor exits non-zero",
      _rc != 0 and "sells only at its box office but its url is a ticket page" in _out)
_rc, _out = _run_out(_fr_page("[]", c0_url="https://epaygo.bg/123"))
check("a box-office cinema whose BOOKING url is a ticket seller's exits non-zero",
      _rc != 0 and "sells only at its box office but its url is a ticket page" in _out)

# ---------------------------------------------------------------------------
# 4c2a2: a festival cinema (BOOKING festival: Кино Euro Cinema, owner 2026-10-29) and the
# epaygo place (FESTROWS 8th field): no row under G8 whose epaygo page names Euro Cinema
# ---------------------------------------------------------------------------
print("4c2a2: festival cinema + epaygo place")
_EC_FIELDS = ', "area": "бул. „Александър Стамболийски“ 17", "areaEn": "17 Aleksandar Stamboliyski Blvd.", ' \
             '"maps": "https://www.google.com/maps/search/?api=1&query=42.696825%2C23.319943"'
_EP = "https://epaygo.bg/1354611053"
_PL_EC, _PL_G8 = 'Кино "Euro Cinema"', "Културен център G8"


def _ec_page(entries, vlinks=None, ec_fields=_EC_FIELDS):
    """cinema0 -> g8 (box office only), cinema1 -> euro-cinema (BOOKING festival)."""
    _fn = "function festSaleUrl(i,v,d,t){return '';}\nfunction festSaleState(i,v,d,t){return '';}"
    h = _minimal_html(extra_js="const FESTROWS=" + _json.dumps(entries) + ";",
                      vlinks_override=_json.dumps(vlinks) if vlinks else "")
    h = h.replace(_C0, '"cinema0": {"label": "CC0", "url": "https://example0.bg/", "inPerson": true}', 1)
    h = h.replace('"cinema1": {"label": "CC1", "url": "https://example1.bg/"}',
                  '"cinema1": {"label": "CC1", "url": "https://www.cinelibri.com/eurocinema/", "festival": true}', 1)
    h = h.replace('{"id": "cinema1", "chain": "CC", "name": "Cinema 1", "city": "Sofia"}',
                  '{"id": "cinema1", "chain": "CC", "name": "Cinema 1", "city": "Sofia"' + ec_fields + '}', 1)
    h = h.replace('"cinema0"', '"g8"').replace('"cinema1"', '"euro-cinema"')
    return h.replace("/* SOFIA-DATA-END */", _fn + "\n/* SOFIA-DATA-END */", 1)


_ec_ok = [["f1", "euro-cinema", _FUTURE, "10:00", "cinelibri", _EP, "onsale", _PL_EC],
          ["f0", "g8", _FUTURE, "10:00", "cinelibri", "https://epaygo.bg/2355774239", "onsale", _PL_G8]]
_rc, _out = _run_out(_ec_page(_ec_ok))
check("FESTROWS at a festival cinema (Euro Cinema) and at G8, each where epaygo places it, exit 0",
      _rc == 0 and "FESTROWS: 2 festival" in _out)
_rc, _out = _run_out(_ec_page([["f1", "euro-cinema", _FUTURE, "10:00", "cinelibri", _EP, None, None]]))
check("…a row whose epaygo place is not known (null) exits 0", _rc == 0)
_rc, _out = _run_out(_ec_page([["f0", "g8", _FUTURE, "10:00", "cinelibri", _EP, "onsale", _PL_EC]]))
check("a FESTROWS row under G8 whose epaygo page names Euro Cinema exits non-zero",
      _rc != 0 and "filed under a cinema their epaygo.bg page does not name" in _out)
_rc, _out = _run_out(_ec_page([["f1", "euro-cinema", _FUTURE, "10:00", "cinelibri", _EP, "onsale", _PL_G8]]))
check("…and vice versa (under Euro Cinema, epaygo names G8)",
      _rc != 0 and "filed under a cinema their epaygo.bg page does not name" in _out)
_rc, _out = _run_out(_ec_page(_ec_ok, vlinks=[["f0", "g8", _EP]]))
check("a G8 VLINKS entry linking an epaygo event stored as Euro Cinema's exits non-zero",
      _rc != 0 and "VLINKS entr(ies) link an epaygo.bg event at another cinema" in _out)
_rc, _out = _run_out(_ec_page([["f1", "euro-cinema", _FUTURE, "10:00", "cinelibri", _EP, "onsale", 5]]))
check("a FESTROWS place that is not a string or null exits non-zero", _rc != 0 and "FESTROWS entr(ies) are not" in _out)
_rc, _out = _run_out(_ec_page([], ec_fields=', "area": "бул. „Александър Стамболийски“ 17", "areaEn": "17 Aleksandar Stamboliyski Blvd."'))
check("a festival cinema without a map place exits non-zero", _rc != 0 and "has no map place" in _out)
_rc, _out = _run_out(_ec_page([], ec_fields=', "maps": "https://www.google.com/maps/search/?api=1&query=42.69%2C23.31"'))
check("a festival cinema without an address exits non-zero", _rc != 0 and "has no address" in _out)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print()
if fails:
    print(f"{len(fails)} test(s) FAILED: " + ", ".join(fails))
    sys.exit(1)
print("all verify gate tests passed")
