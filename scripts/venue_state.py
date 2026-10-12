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

Everything here is pure (no network) and tested offline in test_parsers.py.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib

KEEP_DAYS = 14            # kept rows outlive the last successful read by at most this
LOW_RATIO = 0.5           # "low count": under half the previous run's listings…
LOW_MIN = 10              # …when the previous run had at least this many
STATE_VERSION = 1


# ---------------------------------------------------------------- the sidecar
def load_state(path):
    """{"venues": {venue: entry}} from official_state.json ({} entries if absent)."""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("venues"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": STATE_VERSION, "venues": {}}


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
