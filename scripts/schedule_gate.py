#!/usr/bin/env python3
"""Sofia Gleda: when the daily refresh runs (the gate in .github/workflows/refresh.yml).

The owner wants the refresh between 03:00 and 04:00 Sofia time. GitHub starts
scheduled runs of this repository hours late (5 to 6 h in October 2026: a
03:05 cron started at 08:00-09:00), so the workflow fires several times in the
evening and this gate decides, for each firing, what to do:

  * before 03:05 Sofia (and after the evening cut-off EVENING): sleep until
    03:05 Sofia, then refresh;
  * from 03:05 to EVENING: refresh now (late is better than never), unless
    today's refresh is already done;
  * a manual run (workflow_dispatch) always refreshes at once.

"Today's refresh is done" = the bot committed "Scheduled refresh: ..." on main
at or after 03:00 Sofia today. A refresh that failed the publish gate commits
no such commit, so a later firing retries it.

Every time is computed in Europe/Sofia with zoneinfo, so summer and winter time
and the two clock-change nights are handled by the time zone database, never by
a fixed UTC offset. On the last Sunday of October 03:05 happens twice: the gate
takes the first one (summer time). On the last Sunday of March 03:05 does not
exist (03:00 jumps to 04:00): the gate's target is then 04:05 summer time.

    python3 scripts/schedule_gate.py --event schedule   # gate job: may sleep
    python3 scripts/schedule_gate.py --event schedule --no-wait --day 2026-10-31
        # the refresh job's re-check: is the night the gate chose still not refreshed?

A re-run of a run (GITHUB_RUN_ATTEMPT > 1, "Re-run jobs" in the Actions tab) is
the owner asking for a refresh, so it refreshes at once like a manual run.

Writes run=true|false and day=YYYY-MM-DD (the Sofia date the gate refreshes
for) to $GITHUB_OUTPUT. Unexpected errors (git, the GitHub
API) fail open: an extra refresh is harmless, a missed one is not.
Tests: python3 scripts/test_schedule.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

SOFIA = ZoneInfo("Europe/Sofia")
TARGET = dt.time(3, 5)          # the refresh starts at 03:05 Sofia
DONE_FROM = dt.time(3, 0)       # a refresh committed after 03:00 counts for the day
MAX_SLEEP = 350 * 60            # the gate job may run 360 min; keep 10 for overhead
# Firings from EVENING on wait for tomorrow's 03:05 (at most MAX_SLEEP away).
EVENING = (dt.datetime.combine(dt.date(2000, 1, 2), TARGET)
           - dt.timedelta(seconds=MAX_SLEEP)).time()          # 21:15
COMMIT_PREFIX = "Scheduled refresh: "
BOT = "Sofia Gleda bot"
OTHER_WAITER_MIN_AGE = 180      # s: an earlier firing that is still running after 3 min is waiting


def local_epoch(day: dt.date, t: dt.time) -> float:
    """Epoch of wall-clock time t on day in Sofia (fold=0: the first 03:05 in
    October; the summer-time instant for a time skipped in March)."""
    return dt.datetime.combine(day, t, tzinfo=SOFIA).timestamp()


def decide(now: float, last_refresh: float, event: str) -> tuple[str, float, str]:
    """('run' | 'wait' | 'skip', seconds to sleep, reason). Pure: no clock, no IO."""
    if event != "schedule":
        return "run", 0.0, f"{event}: manual runs refresh at once"
    local = dt.datetime.fromtimestamp(now, SOFIA)
    today = local.date()
    stamp = local.strftime("%Y-%m-%d %H:%M %Z")
    target = local_epoch(today, TARGET)
    if now < target:
        return "wait", target - now, f"{stamp}: waiting until 03:05 Sofia"
    if now < local_epoch(today, EVENING):
        if last_refresh >= local_epoch(today, DONE_FROM):
            return "skip", 0.0, f"{stamp}: today's refresh is already done"
        late = (now - target) / 60
        return "run", 0.0, f"{stamp}: refreshing now" + (f" ({late:.0f} min after 03:05)" if late >= 1 else "")
    tomorrow = today + dt.timedelta(days=1)
    wait = local_epoch(tomorrow, TARGET) - now
    if wait > MAX_SLEEP:
        return "skip", 0.0, f"{stamp}: too early to wait for 03:05, a later firing will"
    return "wait", wait, f"{stamp}: waiting until 03:05 Sofia on {tomorrow}"


def last_refresh_epoch() -> float:
    """Committer time of the bot's last 'Scheduled refresh' commit on main (0 = none found)."""
    # A runner's checkout is shallow (depth 1): deepen it to 100 commits, enough to
    # reach the last refresh past a day's releases. A full clone (a developer's
    # machine) is fetched as it is: --depth there would make the whole clone shallow.
    shallow = subprocess.run(["git", "rev-parse", "--is-shallow-repository"], check=True,
                             capture_output=True, text=True).stdout.strip() == "true"
    subprocess.run(["git", "fetch", "--quiet", *(["--depth=100"] if shallow else []), "origin", "main"],
                   check=True)
    out = subprocess.run(
        ["git", "log", "-1", "--format=%ct", f"--author={BOT}",
         f"--grep=^{COMMIT_PREFIX}", "FETCH_HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()
    return float(out) if out else 0.0


def other_waiter(runs: list[dict], my_id: int, now: float) -> bool:
    """True if another scheduled run of this workflow started earlier than this
    one (by OTHER_WAITER_MIN_AGE, so it is waiting or refreshing, not just
    deciding) within the last 12 h and is still in progress: it covers tonight."""
    def started(r):
        try:
            return dt.datetime.fromisoformat(str(r.get("run_started_at")).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    mine = next((started(r) for r in runs if r.get("id") == my_id), None) or now
    for r in runs:
        s = started(r)
        if r.get("id") != my_id and s is not None and now - 12 * 3600 < s <= mine - OTHER_WAITER_MIN_AGE:
            return True
    return False


def in_progress_runs() -> list[dict]:
    repo, token = os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"]
    url = (f"https://api.github.com/repos/{repo}/actions/workflows/refresh.yml/runs"
           "?event=schedule&status=in_progress&per_page=50")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp).get("workflow_runs") or []


def gate(event: str, may_wait: bool, clock=time.time, sleep=time.sleep,
         last=last_refresh_epoch, runs=in_progress_runs, my_id: int = 0,
         log=print) -> bool:
    """The whole gate with its IO injectable (the tests pass fake clocks)."""
    def last_or_zero():
        try:
            return last()
        except Exception as e:                  # fail open: refresh rather than miss a day
            log(f"could not read the last refresh ({type(e).__name__}); assuming none")
            return 0.0
    action, wait, why = decide(clock(), last_or_zero(), event)
    log(why)
    if action == "wait":
        if not may_wait:
            log("not refreshing: this job does not wait")
            return False
        try:
            if other_waiter(runs(), my_id, clock()):
                log("another scheduled run is already waiting for tonight; this one stops")
                return False
        except Exception as e:
            log(f"could not list the other runs ({type(e).__name__}); waiting anyway")
        log(f"sleeping {wait / 60:.0f} min")
        sleep(wait + 5)
        action, _, why = decide(clock(), last_or_zero(), event)
        log(why)
    return action == "run"


def effective_event(event: str, attempt: int) -> str:
    """A re-run of a scheduled run (attempt > 1) is the owner asking for a refresh:
    it refreshes at once, like workflow_dispatch."""
    if event == "schedule" and attempt > 1:
        print(f"re-run (attempt {attempt}) of a scheduled run: refreshing at once")
        return "re-run"
    return event


def recheck(day: dt.date, event: str, attempt: int = 1, last=last_refresh_epoch,
            log=print) -> bool:
    """The refresh job's re-check, after it has the lock: is the night the gate chose
    (day) still not refreshed? It never looks at the clock window: the gate already
    decided, and a decision at 21:14:59 must not be undone at 21:15:01. Manual runs
    and re-runs always refresh."""
    if event != "schedule" or attempt > 1:
        log(f"{event}, attempt {attempt}: refreshing")
        return True
    try:
        done = last() >= local_epoch(day, DONE_FROM)
    except Exception as e:                      # fail open: refresh rather than miss a day
        log(f"could not read the last refresh ({type(e).__name__}); refreshing")
        return True
    log(f"the {day} refresh is " + ("already done; not refreshing" if done else "still needed; refreshing"))
    return not done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", "schedule"))
    ap.add_argument("--no-wait", action="store_true", help="decide only, never sleep")
    ap.add_argument("--day", help="with --no-wait: the Sofia date the gate chose (YYYY-MM-DD)")
    a = ap.parse_args()
    attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT") or 1)
    day = None
    try:
        if a.no_wait and a.day:
            day = dt.date.fromisoformat(a.day)
            run = recheck(day, a.event, attempt)
        else:
            event = effective_event(a.event, attempt)
            run = gate(event, not a.no_wait, my_id=int(os.environ.get("GITHUB_RUN_ID") or 0))
    except Exception as e:                      # fail open
        print(f"gate error ({type(e).__name__}: {e}); refreshing")
        run = True
    if day is None:                             # the gate refreshes for the night it decided on
        day = dt.datetime.now(SOFIA).date()
    print(f"run={'true' if run else 'false'} day={day}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"run={'true' if run else 'false'}\nday={day}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
