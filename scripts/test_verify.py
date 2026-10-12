#!/usr/bin/env python3
"""Gate-level tests for verify_build.py (CODE-04, CODE-02, CODE-12).

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
# Summary
# ---------------------------------------------------------------------------
print()
if fails:
    print(f"{len(fails)} test(s) FAILED: " + ", ".join(fails))
    sys.exit(1)
print("all verify gate tests passed")
