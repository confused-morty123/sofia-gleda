#!/usr/bin/env python3
"""Sofia Gleda — per-venue isolation, kept-row ageing and the health report.

The owner's rule is "no wrong listing, ever", and the 2026-10-09 audit found
three ways a refresh broke it while still going green (CODE-02/05/13):

  * one venue's self-check aborted the WHOLE scrape, so all 31 venues froze;
  * a venue whose own programme could not be read kept last run's rows as
    CONFIRMED, run after run, with no limit (Младежки театър, 110 rows);
  * an aggregator-only venue could drop to zero rows and nobody was told
    (Cineland 11 -> 0).

So, venue by venue:

  run_isolated()   a merge that crashes or fails its self-check is merged again
                   as "unreachable" — that venue only; every other venue is
                   merged normally.
  kept_policy()    an unreachable venue's kept rows are PRELIMINARY at once
                   (PRELIM_FROM = today, the owner's decision), and gone once
                   its source has not been read for more than KEEP_DAYS days.
                   The date of the last successful read lives in
                   official_state.json, a sidecar the workflow commits.
  health_entries() health.json, [{level, venue, message}], which the workflow
                   prints: a venue emptied, a source blocked from this network
                   or unreachable, rows kept as preliminary, a self-check
                   fallback, an aggregator-only venue, a count far below the
                   previous run's.
  record_seen()    the stage memory (N1, 2026-10-12): what each theatre's own
  seen_history()   programme listed on earlier runs, so a page that lists only
                   upcoming dates still has four weeks of past performances to
                   judge a next month against (official_theatres.complete_until),
                   as a month page does. Reference only — never published.

official_state.json, one entry per venue:
  {"source": "official" | "theatre.art.bg" | "programata.bg",
   "last_ok": "YYYY-MM-DD" | null,     last run whose read succeeded
   "covered_to": "YYYY-MM-DD" | null,  the last date its programme vouched for then
   "failing_since": "YYYY-MM-DD" | null, "last_error": str | null,
   "seen": {                           theatres read from their own programme only
     "stages": ["", "голяма сцена", …],          index 0 = no stage given
     "observed": "2026-09-07..2026-10-19,…",     days the programme vouched for
     "days": {"2026-10-05": "19:00/1 19:30/2"}}} that day's rows, HH:MM/stage index
"seen" keeps the days today-SEEN_BACK .. today+SEEN_AHEAD. Each run the days
from today on are replaced by this run's page (it is authoritative for upcoming
dates, as far as it vouches for them); earlier days keep their last record —
on daily runs, what the page listed on that very day. Rows are unique on
(date, time, stage).

Everything here is pure (no network) and tested offline in test_parsers.py and
test_theatres.py.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib

KEEP_DAYS = 14            # kept rows outlive the last successful read by at most this
SEEN_BACK = 35            # the stage memory keeps this many past days…
SEEN_AHEAD = 7            # …and records this run's page up to today + this
LOW_RATIO = 0.5           # "low count": under half the previous run's listings…
LOW_MIN = 10              # …when the previous run had at least this many
STATE_VERSION = 1


# ---------------------------------------------------------------- the sidecar
def _iso_or_none(v):
    if not isinstance(v, str):
        return None
    try:
        dt.date.fromisoformat(v)
        return v
    except ValueError:
        return None


def _seen_ok(seen):
    return (isinstance(seen, dict)
            and isinstance(seen.get("stages", []), list)
            and all(isinstance(s, str) for s in seen.get("stages", []))
            and isinstance(seen.get("observed", ""), str)
            and isinstance(seen.get("days", {}), dict)
            and all(_iso_or_none(d) and isinstance(c, str) for d, c in seen.get("days", {}).items()))


def sanitise_state(data):
    """The state with every malformed part dropped, and what was dropped. A
    hand-edited or damaged official_state.json must never stop the refresh: a
    venue entry that is not an object starts afresh (its kept rows age from this
    run), a date that is not YYYY-MM-DD reads as unknown, and a broken stage
    memory is forgotten (the next month then stays preliminary until the memory
    is rebuilt — the conservative side)."""
    dropped = []
    venues = data.get("venues") if isinstance(data, dict) else None
    if not isinstance(venues, dict):
        return {"version": STATE_VERSION, "venues": {}}, (["the whole file"] if data is not None else [])
    clean = {}
    for v, e in venues.items():
        if not isinstance(v, str) or not isinstance(e, dict):
            dropped.append(f"{v}: entry")
            continue
        e = dict(e)
        for k in ("last_ok", "covered_to", "failing_since"):
            if e.get(k) is not None and _iso_or_none(e.get(k)) is None:
                dropped.append(f"{v}: {k}")
                e[k] = None
        if e.get("last_error") is not None and not isinstance(e.get("last_error"), str):
            e["last_error"] = str(e["last_error"])[:300]
        if "seen" in e and not _seen_ok(e["seen"]):
            dropped.append(f"{v}: seen")
            del e["seen"]
        clean[v] = e
    out = dict(data)
    out["venues"] = clean
    return out, dropped


def load_state(path):
    """{"venues": {venue: entry}} from official_state.json ({} entries if absent);
    malformed parts are dropped (sanitise_state) and named in the log."""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except OSError:
        return {"version": STATE_VERSION, "venues": {}}
    except ValueError:
        data = "unreadable"
    state, dropped = sanitise_state(data)
    if dropped:
        print(f"  official_state.json: ignored malformed {', '.join(dropped[:8])}"
              f"{f' (+{len(dropped) - 8} more)' if len(dropped) > 8 else ''}")
    return state


def save_state(path, state, stamp):
    state = dict(state)
    state["version"] = STATE_VERSION
    state["updated"] = stamp
    state["venues"] = dict(sorted(state.get("venues", {}).items()))
    pathlib.Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n",
                                  encoding="utf-8")


def record_read(state, venue, ok, today, covered_to=None, error=None, source="official"):
    """Note this run's outcome for one venue. ok: its source was read and passed
    its self-check. covered_to: the last date its own programme vouched for
    (None for an aggregator). Returns the entry."""
    e = state.setdefault("venues", {}).setdefault(venue, {})
    e["source"] = source
    if ok:
        e["last_ok"] = today
        e["covered_to"] = covered_to
        e["failing_since"] = None
        e["last_error"] = None
    else:
        e.setdefault("last_ok", None)
        e.setdefault("covered_to", None)
        if not e.get("failing_since"):
            e["failing_since"] = today
        e["last_error"] = (error or "not read")[:300]
    return e


def _day_before(iso):
    return (dt.date.fromisoformat(iso) - dt.timedelta(days=1)).isoformat()


def kept_policy(entry, today, prev_prelim=None, keep_days=KEEP_DAYS):
    """What may be kept of a venue whose source was not read this run.

    entry        its official_state.json entry ({} on the first run)
    prev_prelim  its PRELIM_FROM on the page before this run
    Returns {"since": the last successful read (or, never seen read, the first
    failed run), "age": days since then, "expired": age > keep_days — kept rows
    are dropped —, "confirmed_to": the last date its own programme confirmed
    at that read (None: unknown, every kept row counts as confirmed)}."""
    entry = entry or {}
    since = entry.get("last_ok") or entry.get("failing_since") or today
    age = (dt.date.fromisoformat(today) - dt.date.fromisoformat(since)).days
    if entry.get("last_ok"):
        confirmed_to = entry.get("covered_to")
    else:
        confirmed_to = _day_before(prev_prelim) if prev_prelim else None
    return {"since": since, "age": age, "expired": age > keep_days,
            "confirmed_to": confirmed_to, "last_ok": entry.get("last_ok")}


# ------------------------------------------------------------- stage memory
def _day(iso, n=0):
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=n)).isoformat()


def decode_seen(seen):
    """(rows {(date, "HH:MM", stage or None)}, observed {date}) of a "seen" record."""
    if not isinstance(seen, dict):
        return set(), set()
    stages = list(seen.get("stages") or [""])
    rows = set()
    for d, cell in (seen.get("days") or {}).items():
        for item in str(cell).split():
            tm, _, k = item.partition("/")
            try:
                st = stages[int(k)]
            except (ValueError, IndexError):
                st = ""
            rows.add((d, tm, st or None))
    observed = set()
    for span in str(seen.get("observed") or "").split(","):
        a, _, b = span.strip().partition("..")
        if not a:
            continue
        try:
            d0, d1 = dt.date.fromisoformat(a), dt.date.fromisoformat(b or a)
        except ValueError:
            continue
        while d0 <= d1:
            observed.add(d0.isoformat())
            d0 += dt.timedelta(days=1)
    return rows, observed


def encode_seen(rows, observed):
    """The compact "seen" record (see the module docstring)."""
    stages = [""] + sorted({st for _d, _t, st in rows if st})
    idx = {st: i for i, st in enumerate(stages)}
    days = {}
    for d, tm, st in sorted(rows, key=lambda r: (r[0], r[1], r[2] or "")):
        days.setdefault(d, []).append(f"{tm}/{idx[st or '']}")
    spans, start, prev = [], None, None
    for d in sorted(observed):
        if prev is not None and d == _day(prev, 1):
            prev = d
            continue
        if start is not None:
            spans.append(start if start == prev else f"{start}..{prev}")
        start = prev = d
    if start is not None:
        spans.append(start if start == prev else f"{start}..{prev}")
    return {"stages": stages, "observed": ",".join(spans), "days": {d: " ".join(v) for d, v in days.items()}}


def record_seen(state, venue, page_rows, today, until=None, back=SEEN_BACK, ahead=SEEN_AHEAD):
    """Remember what `venue`'s own programme listed this run. page_rows:
    [(date, "HH:MM", stage key or None)] of its rows (official_theatres.stage_of).
    The days today .. min(today + ahead, until) — those the page vouches for —
    are replaced by this run's rows; earlier days keep their last record, back
    to today - back. Returns the entry."""
    e = state.setdefault("venues", {}).setdefault(venue, {})
    rows, observed = decode_seen(e.get("seen"))
    lo, hi = _day(today, -back), _day(today, ahead)
    if until and until < hi:
        hi = until
    keep = {r for r in rows if lo <= r[0] < today}
    seen_days = {d for d in observed if lo <= d < today}
    new = {(d, tm, st or None) for d, tm, st in page_rows if today <= d <= hi and tm}
    d = today
    while d <= hi:
        seen_days.add(d)
        d = _day(d, 1)
    e["seen"] = encode_seen(keep | new, seen_days)
    return e


def prune_seen(state, today, back=SEEN_BACK):
    """Drop remembered days before today - back, for every venue."""
    lo = _day(today, -back)
    for e in (state.get("venues") or {}).values():
        if isinstance(e, dict) and e.get("seen"):
            rows, observed = decode_seen(e["seen"])
            rows = {r for r in rows if r[0] >= lo}
            observed = {d for d in observed if d >= lo}
            if rows or observed:
                e["seen"] = encode_seen(rows, observed)
            else:
                e.pop("seen")


def seen_history(entry, today):
    """For official_theatres.fetch_venue(history=…): the remembered rows dated
    before today and the days observed then. A reference for the completeness
    rule only — they are never published, and the page wins on every day it
    lists itself."""
    rows, observed = decode_seen((entry or {}).get("seen"))
    return {"rows": sorted((r for r in rows if r[0] < today), key=lambda r: (r[0], r[1], r[2] or "")),
            "dates": {d for d in observed if d < today}}


# ------------------------------------------------------------- isolation
def run_isolated(primary, fallback, check=None):
    """One venue's merge, isolated (CODE-02). `primary()` returns (rows,
    prelim_from, info); when it raises, or `check(result)` returns problems, the
    venue is merged again by `fallback()` — rule 3: unreachable, its kept rows
    preliminary — and the problems come back with the result.
    Returns (rows, prelim_from, info, problems); problems == [] when the primary
    merge stood."""
    try:
        out = primary()
        problems = list(check(out) if check else [])
        if not problems:
            return out[0], out[1], out[2], []
    except Exception as e:                            # a bug in one venue's data path
        problems = [f"merge crashed: {type(e).__name__}: {e}"]
    try:
        rows, pf, info = fallback()
    except Exception as e:
        problems.append(f"the fallback merge crashed too: {type(e).__name__}: {e}")
        rows, pf, info = [], None, {"status": "unreachable", "discarded": [], "added": 0,
                                    "prelim_rows": 0, "unplaced": [], "excluded_blocked": 0}
    return rows, pf, info, problems


# ---------------------------------------------------------------- health
def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def venue_health(v):
    """Health entries for one venue. v: {venue, kind ("cinema"|"theatre"),
    status ("official"|"unreachable"|"none"), source, error, now, prev (upcoming
    listings this run / last run), blocked [(url, host, why)], selfcheck
    [problems], policy (kept_policy or None), agg_read (bool), agg_name}."""
    out = []
    name, unit = v["venue"], ("screening" if v.get("kind") == "cinema" else "performance")
    now, prev = v.get("now", 0), v.get("prev", 0)
    status = v.get("status")

    def add(level, msg):
        out.append({"level": level, "venue": name, "message": msg})

    for url, host, why in v.get("blocked") or []:
        add("error", f"blocked from this network: {why} at {url}"
            + (f" — read from {v.get('source')} instead" if status == "official" else ""))
        break                                          # one line per venue is enough
    for p in (v.get("selfcheck") or [])[:2]:
        add("warning", f"self-check failed, this venue alone kept its previous listings "
                       f"as preliminary: {p}")
    if now == 0 and prev > 0:
        add("error", f"emptied: 0 upcoming {unit}s now, {prev} last run ({status}"
                     + (f": {v.get('error')}" if v.get("error") else "") + ")")
    elif now == 0 and status == "none":
        add("warning", f"no listings: no official programme, and {v.get('agg_name') or 'the aggregator'} "
                       f"lists nothing for it")
    elif prev >= LOW_MIN and now < LOW_RATIO * prev:
        add("warning", f"low count: {_plural(now, unit)} now vs {prev} last run")
    if status == "unreachable":
        pol = v.get("policy") or {}
        when = (f"last read {pol.get('last_ok')}, {_plural(pol.get('age', 0), 'day')} ago"
                if pol.get("last_ok") else f"not read since {pol.get('since')}")
        if pol.get("expired"):
            add("warning", f"official source unreachable ({v.get('error') or 'not read'}); {when} — "
                           f"kept listings removed after {KEEP_DAYS} days"
                           + (f", {_plural(now, unit)} from {v.get('agg_name')} shown as preliminary"
                              if now and v.get("agg_name") else ""))
        else:
            add("warning", f"official source unreachable ({v.get('error') or 'not read'}); {when} — "
                           f"{_plural(now, unit)} kept and shown as preliminary "
                           f"(removed after {KEEP_DAYS} days)")
    elif status == "none" and now:
        add("warning", "no official programme: "
                       + (f"its one {unit} is" if now == 1 else f"all {now} {unit}s are")
                       + f" preliminary, from {v.get('agg_name') or 'the aggregator'}"
                       + ("" if v.get("agg_read", True) else " (not read this run: previous rows kept)"))
    return out


def health_entries(venues, extra=()):
    """The whole report: errors first, then warnings, each in venue order."""
    out = list(extra)
    for v in venues:
        out += venue_health(v)
    rank = {"error": 0, "warning": 1}
    return sorted(out, key=lambda e: rank.get(e["level"], 2))


def write_health(path, entries):
    pathlib.Path(path).write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n",
                                  encoding="utf-8")
