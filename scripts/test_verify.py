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


def _minimal_html(snapshot_extra: str = "", extra_js: str = "",
                  showtimes_override: str = "",
                  performances_override: str = "",
                  vlinks_override: str = "") -> str:
    """Build a valid-enough page for the gate, meeting all floor requirements.

    All three SHOWTIMES/PERFORMANCES/VLINKS can be overridden for special test cases.
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
    return textwrap.dedent(f"""\
        <!doctype html><html><head><meta charset="utf-8"/></head><body>
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


def _run(html_content: str, skip_smoke: bool = True,
         skip_freshness: bool = False,
         extra_env: dict | None = None) -> int:
    """Write HTML to a tmp file, run verify_build.py, return exit code.

    verify_build.py reads the HTML path from SOFIA_HTML env var (not argv).
    """
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


def _run_out(html_content: str) -> tuple[int, str]:
    """Like _run (smoke and freshness skipped), but also returns the gate's output,
    so a test can tell WHICH check failed."""
    env = dict(os.environ, SOFIA_SKIP_SMOKE="1", SOFIA_SKIP_FRESHNESS="1")
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
# Summary
# ---------------------------------------------------------------------------
print()
if fails:
    print(f"{len(fails)} test(s) FAILED: " + ", ".join(fails))
    sys.exit(1)
print("all verify gate tests passed")
