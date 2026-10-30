#!/usr/bin/env python3
"""Tests for the refresh schedule: .github/workflows/refresh.yml and its gate,
scripts/schedule_gate.py. Fake clocks, no network, ~1 second.

    python3 scripts/test_schedule.py

Exit 0 = every check passed.
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import schedule_gate as G  # noqa: E402

WF = (ROOT / ".github" / "workflows" / "refresh.yml").read_text(encoding="utf-8")
UTC = dt.timezone.utc
fails: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got  {got!r}\n         want {want!r}")
        fails.append(name)


def sofia(y, mo, d, h, mi, fold=0) -> float:
    return dt.datetime(y, mo, d, h, mi, tzinfo=G.SOFIA, fold=fold).timestamp()


def utc(y, mo, d, h, mi) -> float:
    return dt.datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp()


def run_gate(start: float, last: float = 0.0, event: str = "schedule",
             others: list[dict] | None = None, last_err: bool = False):
    """The whole gate with a fake clock; returns (refresh?, refresh start epoch, slept s, log)."""
    clock = {"t": start}
    slept, log = [], []

    def sleep(s):
        slept.append(s)
        clock["t"] += s

    def last_fn():
        if last_err:
            raise OSError("no git")
        return last

    ok = G.gate(event, True, clock=lambda: clock["t"], sleep=sleep, last=last_fn,
                runs=lambda: others or [], my_id=99, log=log.append)
    return ok, clock["t"], sum(slept), log


def hm(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, G.SOFIA).strftime("%m-%d %H:%M %Z")


print("refresh.yml: the schedule")
crons = re.findall(r'- cron: "([^"]+)"\n\s+timezone: "([^"]+)"', WF)
check("five evening crons, each in Europe/Sofia",
      crons, [(c, "Europe/Sofia") for c in ("17 16 * * *", "17 18 * * *", "17 20 * * *",
                                             "17 22 * * *", "47 0 * * *")])
check("no cron at minute 0", [c for c, _ in crons if c.split()[0] == "0"], [])
check("manual button kept", "workflow_dispatch:" in WF, True)
check("gate job runs the gate script", 'run: python3 scripts/schedule_gate.py --event "$GITHUB_EVENT_NAME"\n' in WF, True)
check("gate job may run 360 min (it sleeps at most 350)",
      bool(re.search(r"  gate:\n(?:.*\n){0,2}    timeout-minutes: 360", WF)), True)
check("gate reads runs, writes nothing",
      bool(re.search(r"  gate:\n(?:.*\n){0,4}    permissions:\n      contents: read\n      actions: read", WF)), True)
check("refresh job only when the gate says so",
      "  refresh:\n    needs: gate\n    if: needs.gate.outputs.run == 'true'\n" in WF, True)
check("refresh job re-checks without waiting", "schedule_gate.py --event \"$GITHUB_EVENT_NAME\" --no-wait" in WF, True)
check("refresh job holds the concurrency lock, never cancels",
      bool(re.search(r"  refresh:\n(?:.*\n)*?    concurrency:\n      group: sofia-gleda-refresh\n"
                     r"      cancel-in-progress: false\n", WF)), True)
check("no workflow-level lock (a sleeping gate must not block a manual run)",
      bool(re.search(r"^concurrency:", WF, re.M)), False)
check("scheduled refresh builds on main as it is now",
      "ref: ${{ github.event_name == 'schedule' && 'main' || '' }}" in WF, True)
steps = re.split(r"\n      - name: ", WF.split("  refresh:\n", 1)[1])[1:]
check("every refresh step after the re-check depends on it",
      [b.split("\n")[0] for b in steps[2:]
       if not re.search(r"\n        if: (always\(\) && |failure\(\) && )?steps\.recheck\.outputs\.run == 'true'\n", b)], [])
check("every action pinned to a full SHA",
      [u for u in re.findall(r"uses: (\S+)", WF) if not re.search(r"@[0-9a-f]{40}$", u)], [])
check("only the refresh job writes", WF.count("      contents: write\n"), 1)
check("push still rebases with autostash", WF.count("git pull --rebase --autostash origin main"), 2)
check("commit message unchanged", 'git commit -m "Scheduled refresh: $(date -u +' in WF, True)
try:
    import yaml
    d = yaml.safe_load(WF)
    check("YAML parses: jobs gate, refresh", list(d["jobs"]), ["gate", "refresh"])
except ImportError:
    print("  note YAML not parsed (PyYAML not installed)")

print("decide(): a winter night, 2026-10-30 to 31 (EET, UTC+2)")
done_31 = sofia(2026, 10, 31, 3, 40)          # the night's refresh committed at 03:40
ok, at, slept, log = run_gate(sofia(2026, 10, 30, 21, 30), last=sofia(2026, 10, 30, 3, 40))
check("21:30 waits, refreshes at 03:05", (ok, hm(at), round(slept / 60)), (True, "10-31 03:05 EET", 335))
check("03:05 Sofia is 01:05 UTC in winter", G.local_epoch(dt.date(2026, 10, 31), G.TARGET), utc(2026, 10, 31, 1, 5))
ok, at, slept, _ = run_gate(sofia(2026, 10, 31, 3, 10), last=sofia(2026, 10, 30, 3, 40))
check("03:10, not done yet: refreshes now", (ok, slept), (True, 0))
ok, at, slept, log = run_gate(sofia(2026, 10, 31, 5, 0), last=sofia(2026, 10, 30, 3, 40))
check("05:00, not done yet: refreshes late", (ok, slept, "115 min after 03:05" in log[0]), (True, 0, True))
ok, _, slept, log = run_gate(sofia(2026, 10, 31, 5, 0), last=done_31)
check("05:00, a second firing after the night's refresh: skips", (ok, slept), (False, 0))
ok, _, slept, _ = run_gate(sofia(2026, 10, 31, 3, 6), last=sofia(2026, 10, 31, 3, 6))
check("03:06, a refresh committed 03:06 counts as tonight's", ok, False)
ok, _, _, _ = run_gate(sofia(2026, 10, 31, 3, 10), last=sofia(2026, 10, 31, 1, 0))
check("a manual refresh at 01:00 does not replace 03:05", ok, True)
ok, _, slept, log = run_gate(sofia(2026, 10, 30, 22, 0), last=sofia(2026, 10, 30, 3, 40),
                             others=[{"id": 98, "run_started_at": "2026-10-30T19:30:00Z"},
                                     {"id": 99, "run_started_at": "2026-10-30T20:00:00Z"}])
check("22:00, an earlier firing is already waiting: stops", (ok, slept), (False, 0))
ok, _, slept, _ = run_gate(sofia(2026, 10, 30, 22, 0), last=sofia(2026, 10, 30, 3, 40),
                           others=[{"id": 98, "run_started_at": "2026-10-30T19:59:00Z"},
                                   {"id": 99, "run_started_at": "2026-10-30T20:00:00Z"}])
check("22:00, the other run started 1 min earlier (still deciding): waits", ok, True)
ok, _, slept, _ = run_gate(sofia(2026, 10, 30, 22, 0), last=sofia(2026, 10, 30, 3, 40),
                           others=[{"id": 98, "run_started_at": "2026-10-29T20:00:00Z"}])
check("22:00, a run stuck since yesterday is ignored: waits", ok, True)
ok, _, slept, _ = run_gate(sofia(2026, 10, 30, 18, 20), last=sofia(2026, 10, 30, 3, 40))
check("18:20, today's refresh done: skips (too early for tomorrow)", (ok, slept), (False, 0))
ok, _, slept, _ = run_gate(sofia(2026, 10, 30, 18, 20), last=sofia(2026, 10, 29, 3, 40))
check("18:20, today's refresh failed: retries now", (ok, slept), (True, 0))
ok, at, slept, _ = run_gate(sofia(2026, 10, 30, 21, 15), last=sofia(2026, 10, 30, 3, 40))
check("21:15, exactly 350 min before 03:05: waits", (ok, hm(at)), (True, "10-31 03:05 EET"))
ok, _, slept, _ = run_gate(sofia(2026, 10, 30, 21, 14), last=sofia(2026, 10, 30, 3, 40))
check("21:14, done today, 351 min to wait: skips", ok, False)
ok, at, slept, _ = run_gate(sofia(2026, 10, 31, 1, 0), last=sofia(2026, 10, 30, 3, 40))
check("01:00 waits 125 min", (ok, round(slept / 60)), (True, 125))
ok, at, slept, _ = run_gate(sofia(2026, 10, 30, 21, 30), last=sofia(2026, 10, 30, 3, 40), event="workflow_dispatch")
check("manual run at 21:30 refreshes at once", (ok, slept), (True, 0))
ok, at, slept, _ = run_gate(sofia(2026, 10, 31, 5, 0), last=done_31, event="workflow_dispatch")
check("manual run after the night's refresh still refreshes", ok, True)
ok, _, _, log = run_gate(sofia(2026, 10, 31, 5, 0), last_err=True)
check("git unreadable: fails open (refreshes)", ok, True)
check("re-check (--no-wait) at 21:30 does not refresh",
      G.gate("schedule", False, clock=lambda: sofia(2026, 10, 30, 21, 30), last=lambda: 0.0, log=lambda m: None), False)

print("decide(): the October clock change, Sun 2026-10-25 (04:00 EEST -> 03:00 EET)")
ok, at, slept, _ = run_gate(sofia(2026, 10, 24, 21, 30), last=sofia(2026, 10, 24, 3, 40))
check("Sat 21:30 EEST waits 5 h 35 min, refreshes at the first 03:05 (00:05 UTC)",
      (ok, at // 60 * 60, round(slept / 60)), (True, utc(2026, 10, 25, 0, 5), 335))
ok, _, _, _ = run_gate(sofia(2026, 10, 25, 3, 10, fold=1), last=utc(2026, 10, 25, 0, 40))
check("the second 03:10 (EET) after the first night's refresh: skips", ok, False)
ok, _, _, _ = run_gate(sofia(2026, 10, 25, 3, 10, fold=1), last=sofia(2026, 10, 24, 3, 40))
check("the second 03:10 (EET), no refresh yet: refreshes", ok, True)
ok, at, slept, _ = run_gate(sofia(2026, 10, 25, 22, 0), last=utc(2026, 10, 25, 0, 40))
check("Sun 22:00 EET waits 5 h 05 min for Mon 03:05 EET", (ok, hm(at), round(slept / 60)), (True, "10-26 03:05 EET", 305))

print("decide(): the March clock change, Sun 2027-03-28 (03:00 EET -> 04:00 EEST)")
ok, at, slept, _ = run_gate(sofia(2027, 3, 27, 21, 30), last=sofia(2027, 3, 27, 3, 40))
check("Sat 21:30 EET waits 5 h 35 min; 03:05 does not exist, refreshes 04:05 EEST (01:05 UTC)",
      (ok, at // 60 * 60, hm(at), round(slept / 60)), (True, utc(2027, 3, 28, 1, 5), "03-28 04:05 EEST", 335))
ok, at, slept, _ = run_gate(sofia(2027, 3, 28, 2, 30), last=sofia(2027, 3, 27, 3, 40))
check("02:30 EET waits 35 min", (ok, round(slept / 60)), (True, 35))
ok, _, _, _ = run_gate(sofia(2027, 3, 28, 4, 30), last=utc(2027, 3, 28, 1, 40))
check("04:30 EEST after the night's refresh: skips", ok, False)
ok, at, slept, _ = run_gate(sofia(2027, 3, 28, 22, 0), last=utc(2027, 3, 28, 1, 40))
check("Sun 22:00 EEST waits 5 h 05 min for Mon 03:05 EEST", (ok, hm(at), round(slept / 60)), (True, "03-29 03:05 EEST", 305))

print("the five firings against GitHub's delay (one night, each firing alone)")
FIRINGS = [(16, 17), (18, 17), (20, 17), (22, 17), (24, 47)]


def first_refresh(delay_min: int, night=dt.date(2026, 10, 30)) -> float | None:
    """Earliest refresh start when every firing is `delay_min` late (yesterday's refresh done)."""
    starts = []
    for h, m in FIRINGS:
        t = dt.datetime.combine(night, dt.time(0, 0), tzinfo=G.SOFIA) + dt.timedelta(hours=h, minutes=m + delay_min)
        ok, at, _, _ = run_gate(t.timestamp(), last=sofia(night.year, night.month, night.day, 3, 40))
        if ok:
            starts.append(at)
    return min(starts) if starts else None


target = sofia(2026, 10, 31, 3, 5)
on_time = [d for d in range(0, 24 * 60, 15) if (r := first_refresh(d)) and target <= r < target + 60]
check("refresh at 03:05 for every delay from 0 to 10 h 45 min (15 min steps)",
      on_time[:44], list(range(0, 10 * 60 + 46, 15)))
in_window = [d for d in range(0, 24 * 60, 15)
             if (r := first_refresh(d)) and target <= r < sofia(2026, 10, 31, 4, 0)]
check("refresh between 03:05 and 04:00 for delays up to 11 h 30 min", max(in_window), 11 * 60 + 30)
check("a refresh every night for any delay up to 23 h",
      [d for d in range(0, 23 * 60, 15) if first_refresh(d) is None], [])

print("last_refresh_epoch(): a real git repository")
with tempfile.TemporaryDirectory() as tmp:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

    def git(*a, cwd, **kw):
        return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True,
                              env={**env, **kw}).stdout

    origin, work = pathlib.Path(tmp, "origin"), pathlib.Path(tmp, "work")
    git("init", "-q", "-b", "main", str(origin), cwd=tmp)
    for i, (who, msg, when) in enumerate([("Sofia Gleda bot", "Scheduled refresh: 2026-10-30", "2026-10-30T01:40:00Z"),
                                          ("Owner", "Release: something", "2026-10-30T09:00:00Z"),
                                          ("Owner", "Scheduled refresh: by hand?", "2026-10-30T10:00:00Z")]):
        (origin / "f").write_text(str(i))
        git("add", "f", cwd=origin)
        git("commit", "-q", "-m", msg, cwd=origin, GIT_AUTHOR_NAME=who, GIT_AUTHOR_EMAIL="a@b",
            GIT_COMMITTER_NAME=who, GIT_COMMITTER_EMAIL="a@b", GIT_COMMITTER_DATE=when, GIT_AUTHOR_DATE=when)
    git("clone", "-q", "--depth=1", origin.as_uri(), str(work), cwd=tmp)
    cwd = os.getcwd()
    os.chdir(work)
    try:
        check("the bot's last 'Scheduled refresh' commit, behind a release, in a shallow clone",
              G.last_refresh_epoch(), utc(2026, 10, 30, 1, 40))
    finally:
        os.chdir(cwd)
    full = pathlib.Path(tmp, "full")
    git("clone", "-q", origin.as_uri(), str(full), cwd=tmp)
    os.chdir(full)
    try:
        check("a full clone: same answer, and the clone stays full",
              (G.last_refresh_epoch(), git("rev-parse", "--is-shallow-repository", cwd=full).strip()),
              (utc(2026, 10, 30, 1, 40), "false"))
    finally:
        os.chdir(cwd)

print(f"\ntest_schedule: {'FAIL ' + str(len(fails)) if fails else 'all passed'}")
sys.exit(1 if fails else 0)
