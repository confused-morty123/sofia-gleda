#!/usr/bin/env python3
"""Tests for the deploy guard: .github/workflows/deploy-guard.yml and its logic,
scripts/deploy_guard.py. A fake GitHub, a fake site and a fake clock: no network,
under a second.

    python3 scripts/test_deploy_guard.py

Exit 0 = every check passed.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import deploy_guard as D  # noqa: E402
import site_config  # noqa: E402

WF = (ROOT / ".github" / "workflows" / "deploy-guard.yml").read_text(encoding="utf-8")
TESTS_WF = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
fails: list[str] = []

A, B = "a" * 40, "b" * 40               # two commits: A first, B newer
OLD, NEW = "2026-11-10T03:24:45+02:00", "2026-11-11T03:24:45+02:00"


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got  {got!r}\n         want {want!r}")
        fails.append(name)


def page(stamp: str, extra: str = "") -> bytes:
    return ('<!doctype html>\n<script>\nconst SNAPSHOT={"window":{"from":"2026-11-11"},"generated":"%s"};\n'
            'const FILMS=[];%s\n</script>\n' % (stamp, extra)).encode()


def run(conclusion="success", attempt=1, status="completed", sha=A, rid=7, started="2026-11-11T01:42:54Z"):
    return {"id": rid, "path": D.PAGES_PATH, "head_sha": sha, "status": status, "run_attempt": attempt,
            "conclusion": conclusion if status == "completed" else None, "run_started_at": started,
            "html_url": f"https://github.com/confused-morty123/sofia-gleda/actions/runs/{rid}"}


class Hub:
    """A fake GitHub and a fake live site. Counts every call; `outcomes` are the
    conclusions of the re-runs it is asked for, in order (each takes `takes` s)."""

    def __init__(self, tip=A, runs=None, live=OLD, outcomes=(), takes=40, api_ok=True,
                 rerun_status=201, cancel_status=202, tip_after_post=None, stuck=False):
        self.tip, self.runs, self.live_stamp = tip, [dict(r) for r in (runs or [])], live
        self.outcomes, self.takes, self.api_ok = list(outcomes), takes, api_ok
        self.rerun_status, self.cancel_status = rerun_status, cancel_status
        self.tip_after_post, self.stuck = tip_after_post, stuck
        self.t, self.timers, self.posts, self.live_gets, self.log = 0.0, [], [], [], []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s
        for when, fn in sorted(self.timers, key=lambda x: x[0]):
            if when <= self.t:
                fn()
        self.timers = [x for x in self.timers if x[0] > self.t]

    def at(self, when, fn):
        self.timers.append((when, fn))

    def live(self, n):
        self.live_gets.append(self.t)
        return page(self.live_stamp) if self.live_stamp else None

    def api(self, method, path):
        if not self.api_ok:
            return 503, None
        if method == "GET" and path == "/git/ref/heads/main":
            return 200, {"object": {"sha": self.tip}}
        if method == "GET" and path.startswith("/actions/runs?head_sha="):
            sha = path.split("head_sha=")[1].split("&")[0]
            other = {"id": 1, "path": ".github/workflows/tests.yml", "head_sha": sha, "status": "completed",
                     "conclusion": "failure", "run_attempt": 5}          # another workflow: never counted
            return 200, {"workflow_runs": [other] + [dict(r) for r in self.runs if r["head_sha"] == sha]}
        if method == "POST":
            self.posts.append(path)
            r = next((r for r in self.runs if path.startswith(f"/actions/runs/{r['id']}/")), None)
            if path.endswith("/rerun-failed-jobs"):
                if self.rerun_status != 201 or r is None:
                    return self.rerun_status, None
                if self.tip_after_post:                  # a push lands while the guard is asking
                    self.tip = self.tip_after_post
                if not self.stuck:
                    r.update(status="in_progress", conclusion=None, run_attempt=r["run_attempt"] + 1)
                    outcome = self.outcomes.pop(0) if self.outcomes else "failure"

                    def done(r=r, outcome=outcome):
                        r.update(status="completed", conclusion=outcome)
                        if outcome == "success":
                            self.live_stamp = NEW
                    self.at(self.t + self.takes, done)
                return 201, None
            if path.endswith("/cancel"):
                if r is not None and self.cancel_status == 202:
                    r.update(status="completed", conclusion="cancelled")
                    self.timers = []
                return self.cancel_status, None
        return 404, None

    def guard(self, head=A, committed=NEW, dry_run=False):
        return D.guard(head, page(committed), self.api, self.live, clock=self.clock, sleep=self.sleep,
                       log=self.log.append, dry_run=dry_run)

    def reruns(self):
        return [p for p in self.posts if p.endswith("/rerun-failed-jobs")]


print("deploy-guard.yml: triggers, permissions, safety")
check("started by GitHub's Pages workflow, under both its names, when a run completes",
      bool(re.search(r'  workflow_run:\n(?:\s*#.*\n)*    workflows: \["pages-build-deployment", '
                     r'"pages build and deployment"\]\n    types: \[completed\]\n', WF)), True)
check("one daily timer in Sofia time, off the top of the hour",
      re.findall(r'- cron: "([^"]+)"\n\s+timezone: "([^"]+)"', WF), [("23 4 * * *", "Europe/Sofia")])
check("manual button", "  workflow_dispatch: {}" in WF, True)
check("not started by its own runs or by any push",
      [k for k in ("  push:", "  pull_request", "Deploy guard\"]", "workflows: [\"Deploy guard") if k in WF], [])
check("top-level permissions: none", bool(re.search(r"^permissions: \{\}$", WF, re.M)), True)
check("the one job: contents read, actions write, nothing else",
      re.findall(r"^    permissions:\n((?:      .*\n)+)", WF, re.M),
      ["      contents: read              # check out the guard script and the committed page\n"
       "      actions: write              # read the Pages runs; re-run the failed jobs of one (or cancel that re-run)\n"])
check("no other permission anywhere", sorted(set(re.findall(r"^\s+([a-z-]+): (?:read|write)\b", WF, re.M))),
      ["actions", "contents"])
check("no secrets", [w for w in ("secrets.", "secrets[", "TMDB", "OMDB", "DEEPL") if w in WF], [])
check("one lock for every guard run, never cancelling a run that is working",
      bool(re.search(r"^concurrency:\n  group: sofia-gleda-deploy-guard\n  cancel-in-progress: false\n", WF, re.M)), True)
check("runs for main only", "    if: github.ref == 'refs/heads/main'\n" in WF, True)
check("every action pinned to a full SHA",
      [u for u in re.findall(r"uses: (\S+)", WF) if not re.search(r"@[0-9a-f]{40}$", u)], [])
check("only actions/checkout is used, the commit refresh.yml and tests.yml pin",
      re.findall(r"uses: (\S+)", WF),
      re.findall(r"uses: (actions/checkout@\S+)", TESTS_WF)[:1])
check("checks out main, exactly two files, and keeps no credentials",
      ("          ref: main\n" in WF, "          persist-credentials: false\n" in WF,
       "          sparse-checkout-cone-mode: false\n" in WF,
       "          sparse-checkout: |\n            /scripts/deploy_guard.py\n            /index.html\n" in WF),
      (True, True, True, True))
check("never checks out the commit of the run that started it",
      [w for w in ("workflow_run.head_branch }}\n          ref", "ref: ${{") if w in WF], [])
run_lines = re.findall(r"^\s+run: (.*)$", WF, re.M)
check("one run step: the guard script, with nothing pasted into the shell",
      run_lines, ["python3 scripts/deploy_guard.py"])
check("nothing installed (no pip, no setup-python): the script is standard library only",
      [w for w in ("pip ", "setup-python", "npm ", "curl ", "wget ") if w in WF], [])
check("event values reach the script as environment variables only",
      sorted(re.findall(r"^          ([A-Z_]+): \$\{\{ ([^}]+) \}\}$", WF, re.M)),
      [("GH_TOKEN", "github.token"), ("WOKEN_BY_ATTEMPT", "github.event.workflow_run.run_attempt"),
       ("WOKEN_BY_CONCLUSION", "github.event.workflow_run.conclusion"),
       ("WOKEN_BY_SHA", "github.event.workflow_run.head_sha")])
check("no other ${{ }} in the file", len(re.findall(r"\$\{\{", WF)), 4)
check("the job may run long enough for two re-runs and the live check",
      int(re.search(r"^    timeout-minutes: (\d+)\b", WF, re.M).group(1)) * 60
      > D.MAX_RETRIES * (D.RERUN_WAIT + D.POLL) + D.LIVE_TRIES * D.LIVE_GAP, True)
try:
    import yaml
    d = yaml.safe_load(WF)
    on = d.get("on", d.get(True))
    check("YAML parses: one job, three triggers", (list(d["jobs"]), sorted(on)),
          (["guard"], ["schedule", "workflow_dispatch", "workflow_run"]))
    check("YAML: permissions as read above", (d["permissions"], d["jobs"]["guard"]["permissions"]),
          ({}, {"contents": "read", "actions": "write"}))
    check("YAML: tests.yml still parses, unit job runs this test",
          "python3 scripts/test_deploy_guard.py" in [s.get("run") for s in yaml.safe_load(TESTS_WF)["jobs"]["unit"]["steps"]], True)
except ImportError:
    print("  note YAML not parsed (PyYAML not installed)")
check("tests.yml: the unit job runs this test, as its other tests run",
      "      - name: test_deploy_guard\n        if: ${{ !cancelled() && steps.build.outcome == 'success' }}\n"
      "        run: python3 scripts/test_deploy_guard.py\n" in TESTS_WF, True)

print("the script: constants and the stamp")
check("the site is site_config's", D.SITE, site_config.SITE_URL)
src = (ROOT / "scripts" / "deploy_guard.py").read_text(encoding="utf-8")
check("standard library only (the workflow installs nothing)",
      sorted(set(re.findall(r"^(?:import|from) (\w+)", src, re.M)) - {"__future__"}),
      ["argparse", "hashlib", "json", "os", "pathlib", "re", "subprocess", "sys", "time", "urllib"])
check("the only writes it can ask GitHub for: re-run failed jobs, cancel",
      sorted(set(re.findall(r'api\("POST", f"([^"]+)"\)', src))),
      ["/actions/runs/{int(run_id)}/cancel", "/actions/runs/{int(run_id)}/rerun-failed-jobs"])
check("...and no other POST, PUT, PATCH or DELETE", len(re.findall(r'"(?:POST|PUT|PATCH|DELETE)"', src)), 3)
real = D.stamp_of((ROOT / "index.html").read_bytes())
check("the committed index.html has a readable ISO stamp", bool(real and D.STAMP.fullmatch(real)), True)
check("stamp of a page", D.stamp_of(page(NEW)), NEW)
check("stamp of a page given as text", D.stamp_of(page(NEW).decode()), NEW)
check("no page, no SNAPSHOT, broken JSON, no 'generated': None",
      [D.stamp_of(x) for x in (None, b"", b"<html></html>", b"const SNAPSHOT={oops};\n",
                               b'const SNAPSHOT={"window":{}};\n', b'const SNAPSHOT=[1];\n')], [None] * 6)
check("a stamp that is not a date is refused (nothing else can be printed)",
      [D.stamp_of(page(x)) for x in ("yesterday", "2026-11-11", "2026-11-11T03:24:45+02:00\\n::error::x",
                                     "<b>2026-11-11T03:24:45+02:00</b>")], [None] * 4)
check("short(): a full commit id only", [D.short(x) for x in (A, "abc", None, "g" * 40, A + "\n")],
      ["aaaaaaa", "", "", "", ""])

print("decide(): the table")


def act(*a, **k):
    return D.decide(*a, **k)[0]


check("failed, attempt 1, still main's tip: retry", act("failure", 1, A, A, None, NEW), "retry")
check("failed, attempt 2: retry (the second and last)", act("failure", 2, A, A, None, NEW), "retry")
check("failed, attempt 3: fail, no third retry", act("failure", 3, A, A, None, NEW), "fail")
check("failed, attempt 7 (re-run by hand many times): fail", act("failure", 7, A, A, None, NEW), "fail")
check("timed out counts as failed", [act("timed_out", n, A, A, None, NEW) for n in (1, 2, 3)], ["retry", "retry", "fail"])
check("failed, but the attempts cannot be read: fail, never retry blind",
      [act("failure", n, A, A, None, NEW) for n in (0, -1, None, "x")], ["fail"] * 4)
check("attempts given as text still count", act("failure", "2", A, A, None, NEW), "retry")
check("failed, but main moved on: skip, whatever the attempt",
      [act("failure", n, A, B, None, NEW) for n in (1, 2, 3)], ["skip"] * 3)
check("succeeded, but main moved on: skip (the newer deployment is checked instead)",
      act("success", 1, A, B, OLD, NEW), "skip")
check("no commit id, or not a commit id: skip",
      [act("failure", 1, x, x, None, NEW) for x in ("", None, "main", A[:39])], ["skip"] * 4)
check("still running: skip", act(None, 1, A, A, None, NEW), "skip")
check("cancelled, skipped, startup failure, action required: fail, not retried",
      [act(c, 1, A, A, None, NEW) for c in ("cancelled", "skipped", "startup_failure", "action_required", "weird")],
      ["fail"] * 5)
check("succeeded, live stamp is the committed one: ok", act("success", 1, A, A, NEW, NEW), "ok")
check("succeeded after two retries: ok", act("success", 3, A, A, NEW, NEW), "ok")
check("succeeded, live still old: look again", act("success", 1, A, A, OLD, NEW), "verify")
check("succeeded, live not readable: look again", act("success", 1, A, A, None, NEW), "verify")
check("succeeded, live still old on the last look: fail", act("success", 1, A, A, OLD, NEW, last_check=True), "fail")
check("succeeded, live not readable on the last look: fail", act("success", 1, A, A, None, NEW, last_check=True), "fail")
check("the last look still passes when the site is fresh", act("success", 1, A, A, NEW, NEW, last_check=True), "ok")
check("succeeded, but the committed page has no stamp: fail (cannot check)",
      [act("success", 1, A, A, x, None) for x in (None, NEW)], ["fail", "fail"])
check("no Pages run for the commit: the live site decides",
      [act(D.NO_RUN, 0, A, A, NEW, NEW), act(D.NO_RUN, 0, A, A, OLD, NEW),
       act(D.NO_RUN, 0, A, A, OLD, NEW, last_check=True)], ["ok", "verify", "fail"])
check("a retry is decided by the run alone: a fresh-looking site does not hide a failed deployment",
      act("failure", 1, A, A, NEW, NEW), "retry")
for name, args, kw, words in [
        ("failed 3 times", ("failure", 3, A, A, None, NEW), {},
         ["aaaaaaa", "failed 3 times", "at most 2 times", "Re-run failed jobs", "TOP run only", "Daily refresh"]),
        ("stale", ("success", 1, A, A, OLD, NEW), {"last_check": True},
         ["aaaaaaa", OLD, NEW, "6 looks over 5 minutes", "Deploy guard", "Run workflow", "Daily refresh"]),
        ("unreadable site", ("success", 1, A, A, None, NEW), {"last_check": True},
         ["could not be read", NEW, "Run workflow"]),
        ("cancelled", ("cancelled", 1, A, A, None, NEW), {}, ["'cancelled'", "does not retry", "Re-run failed jobs"]),
        ("main moved on", ("failure", 1, A, B, None, NEW), {}, ["aaaaaaa", "bbbbbbb", "moved on", "OLD content"]),
        ("retry", ("failure", 2, A, A, None, NEW), {}, ["aaaaaaa", "attempt 2", "retry 2 of 2"])]:
    msg = D.decide(*args, **kw)[1]
    check(f"message, {name}: says what happened and what to click", [w for w in words if w not in msg], [])
check("a conclusion that is not a plain word is never printed",
      "<img" in D.decide("<img src=x>", 1, A, A, None, NEW)[1], False)

print("summarise(): one commit's Pages runs")
check("no run", D.summarise([]), (D.NO_RUN, 0, None))
check("one failed run, first attempt", D.summarise([run("failure")])[:2], ("failure", 1))
check("one run on its third attempt", D.summarise([run("failure", 3)])[:2], ("failure", 3))
check("running", D.summarise([run(status="in_progress", attempt=2)])[:2], (None, 2))
check("queued counts as running", D.summarise([run(status="queued")])[:2], (None, 1))
two = [run("failure", 2, rid=7, started="2026-11-11T01:00:00Z"), run("failure", 1, rid=9, started="2026-11-11T02:00:00Z")]
check("two runs of one commit: attempts add up, the later one is re-run",
      (D.summarise(two)[:2], D.summarise(two)[2]["id"]), (("failure", 3), 9))
check("one of two succeeded: the commit is deployed",
      D.summarise([run("failure", 1, rid=7), run("success", 1, rid=9)])[:2], ("success", 2))
check("one of two still running: running",
      D.summarise([run("success", 1, rid=7), run(status="in_progress", rid=9)])[0], None)

print("guard(): a deployment that worked")
h = Hub(runs=[run("success")], live=NEW)
check("fresh at once: ok, one GET of the site, nothing asked of GitHub",
      (h.guard()[0], len(h.live_gets), h.posts, h.t), ("ok", 1, [], 0.0))
check("...and says the page is the committed one", "byte for byte the committed one" in h.log[-1], True)
h = Hub(runs=[run("success")], live=OLD)
h.at(100, lambda: setattr(h, "live_stamp", NEW))
check("the CDN catches up after 100 s: ok on the third look", (h.guard()[0], h.live_gets, h.posts),
      ("ok", [0.0, 60.0, 120.0], []))
h = Hub(runs=[run("success")], live=OLD)
r = h.guard()
check("still yesterday's data after 5 minutes: fail, six polite GETs a minute apart, nothing re-run",
      (r[0], h.live_gets, h.posts), ("fail", [0.0, 60.0, 120.0, 180.0, 240.0, 300.0], []))
check("...the message names both stamps and what to do", [w for w in (OLD, NEW, "What to do") if w not in r[1]], [])
h = Hub(runs=[run("success")], live=None)
check("the site cannot be read at all: fail after the six looks", (h.guard()[0], len(h.live_gets)), ("fail", 6))
h = Hub(runs=[run("success")], live=NEW)
check("a release that keeps the listings: same stamp, other bytes -> ok, with a note",
      (D.guard(A, page(NEW, "const X=1;"), h.api, h.live, log=h.log.append)[0],
       "not byte for byte" in h.log[-1]), ("ok", True))

print("guard(): a deployment that failed (the 2026-11-09 and 2026-11-10 case)")
h = Hub(runs=[run("failure")], live=OLD, outcomes=["success"])
r = h.guard()
check("first re-run works: one re-run asked, then the live site is checked: ok",
      (r[0], h.reruns(), len(h.live_gets), h.runs[0]["run_attempt"]),
      ("ok", ["/actions/runs/7/rerun-failed-jobs"], 1, 2))
h = Hub(runs=[run("failure")], live=OLD, outcomes=["failure", "success"], takes=640)
r = h.guard()
check("first re-run fails after 10 min 40 s, the second works: two re-runs, ok",
      (r[0], len(h.reruns()), h.runs[0]["run_attempt"], h.t < D.MAX_RETRIES * (D.RERUN_WAIT + D.POLL)),
      ("ok", 2, 3, True))
h = Hub(runs=[run("failure")], live=OLD, outcomes=["failure", "failure", "failure"], takes=640)
r = h.guard()
check("both re-runs fail: fail after exactly two re-runs, the site never polled",
      (r[0], len(h.reruns()), h.runs[0]["run_attempt"], h.live_gets), ("fail", 2, 3, []))
check("...the message says so, what to click, and links the run",
      [w for w in ("failed 3 times", "Re-run failed jobs", "https://github.com/confused-morty123/sofia-gleda/actions/runs/7")
       if w not in r[1]], [])
h2 = Hub(runs=h.runs, live=OLD)
check("the next guard run for that commit (timer, button, another event): fail again, asks nothing",
      (h2.guard()[0], h2.posts), ("fail", []))
h = Hub(runs=[run("failure", 2)], live=OLD, outcomes=["failure"])
check("the owner already re-ran it once by hand: the guard has one retry left",
      (h.guard()[0], len(h.reruns()), h.runs[0]["run_attempt"]), ("fail", 1, 3))
h = Hub(runs=[run("timed_out")], live=OLD, outcomes=["success"])
check("a timed-out deployment is re-run like a failed one", (h.guard()[0], len(h.reruns())), ("ok", 1))
h = Hub(runs=[run("failure")], live=OLD, outcomes=["success"])
h.at(41, lambda: setattr(h, "live_stamp", OLD))        # deployed, but the site stays old
check("the re-run succeeds but the site stays old: fail after the six looks, no further re-run",
      (h.guard()[0], len(h.reruns()), len(h.live_gets)), ("fail", 1, 6))
h = Hub(runs=[run("cancelled")], live=OLD)
check("a cancelled deployment is not restarted: fail, nothing asked", (h.guard()[0], h.posts, h.live_gets), ("fail", [], []))
h = Hub(runs=[run("failure")], live=OLD)
check("dry run: decides, asks nothing", (h.guard(dry_run=True)[0], h.posts), ("skip", []))

print("guard(): old content can never be put live")
h = Hub(tip=B, runs=[run("failure")], live=OLD)
r = h.guard()
check("main moved on before the guard looked: skip, nothing asked, the site not polled",
      (r[0], h.posts, h.live_gets, "moved on" in r[1]), ("skip", [], [], True))
h = Hub(tip=B, runs=[run("success")], live=OLD)
check("...the same after a deployment that worked (the newer one is checked by its own run)",
      (h.guard()[0], h.posts, h.live_gets), ("skip", [], []))
h = Hub(runs=[run("failure")], live=OLD, tip_after_post=B)
r = h.guard()
check("a push lands while the guard is asking: the re-run is cancelled at once, skip",
      (r[0], h.posts, h.runs[0]["conclusion"]),
      ("skip", ["/actions/runs/7/rerun-failed-jobs", "/actions/runs/7/cancel"], "cancelled"))
h = Hub(runs=[run("failure")], live=OLD, tip_after_post=B, cancel_status=500)
r = h.guard()
check("...and if GitHub will not cancel it: fail, saying the site may show OLD content",
      (r[0], "OLD content" in r[1], "Daily refresh" in r[1]), ("fail", True, True))
h = Hub(runs=[run("failure")], live=OLD, outcomes=["failure", "success"], takes=640)
h.at(300, lambda: setattr(h, "tip", B))
r = h.guard()
check("main moves on while the first re-run is running: no second re-run, skip",
      (r[0], len(h.reruns()), h.live_gets), ("skip", 1, []))
h = Hub(runs=[run("success")], live=OLD)
h.at(90, lambda: setattr(h, "tip", B))
check("main moves on while the site is being polled: skip, not a false alarm",
      (h.guard()[0], len(h.live_gets)), ("skip", 2))
tip_reads = []
h = Hub(runs=[run("failure")], live=OLD, outcomes=["success"])
_api = h.api


def spy(method, path):
    tip_reads.append("tip" if path == "/git/ref/heads/main" else ("POST" if method == "POST" else "-"))
    return _api(method, path)


D.guard(A, page(NEW), spy, h.live, clock=h.clock, sleep=h.sleep, log=h.log.append)
i = tip_reads.index("POST")
check("main's tip is read right before asking for the re-run and right after", (tip_reads[i - 1], tip_reads[i + 1]), ("tip", "tip"))
check("every write is for the run of the commit that is main's tip",
      [p for hub in (Hub(runs=[run("failure", rid=7), run("failure", sha=B, rid=8)], outcomes=["success"]),)
       for _ in [hub.guard()] for p in hub.posts if not p.startswith("/actions/runs/7/")], [])

print("guard(): no endless loop")
h = Hub(runs=[run("failure")], live=OLD, stuck=True)       # GitHub says yes, nothing ever changes
r = h.guard()
check("a GitHub that accepts re-runs and never starts them: two asked, then fail",
      (r[0], len(h.reruns()), h.t <= D.MAX_RETRIES * (D.RERUN_WAIT + 2 * D.POLL)), ("fail", 2, True))
h = Hub(runs=[run("failure")], live=OLD, rerun_status=403)
r = h.guard()
check("GitHub refuses the re-run (403): fail at once with the HTTP status, asked once",
      (r[0], len(h.posts), "HTTP 403" in r[1]), ("fail", 1, True))
h = Hub(runs=[run("failure")], live=OLD, rerun_status=409)
_api2 = h.api


def already(method, path):
    out = _api2(method, path)
    if method == "POST":
        h.runs[0].update(status="in_progress", conclusion=None, run_attempt=2)      # someone re-ran it first
    return out


check("GitHub refuses because someone is already re-running it: skip",
      D.guard(A, page(NEW), already, h.live, clock=h.clock, sleep=h.sleep, log=h.log.append)[0], "skip")
h = Hub(runs=[run(status="in_progress", attempt=2)], live=OLD)
check("a deployment still running: skip, nothing asked, the site not polled",
      (h.guard()[0], h.posts, h.live_gets), ("skip", [], []))
h = Hub(runs=[run("failure")], live=OLD, api_ok=False)
r = h.guard()
check("GitHub's API cannot be read: fail (the guard cannot guard), nothing asked",
      (r[0], h.posts, "could not be read" in r[1]), ("fail", [], True))
worst = 0
for outs in (["failure", "failure"], ["failure", "success"], ["success"], []):
    for first in (1, 2, 3, 4):
        h = Hub(runs=[run("failure", first)], live=OLD, outcomes=list(outs), takes=640)
        h.guard()
        worst = max(worst, len(h.reruns()) + first - 1)
        if len(h.reruns()) > max(0, D.MAX_RETRIES + 1 - first):
            fails.append(f"too many re-runs from attempt {first} with {outs}")
check("whatever the re-runs do, a commit's deployment is never re-run past attempt 3 by the guard",
      worst <= 3 or worst, 3 if worst > 3 else True)
check("one guard run always ends within the job's time limit",
      D.MAX_RETRIES * (D.RERUN_WAIT + 2 * D.POLL) + D.LIVE_TRIES * D.LIVE_GAP < 45 * 60, True)

print("guard(): no Pages run for the commit")
h = Hub(runs=[], live=NEW)
check("the site serves the commit's data anyway: ok", (h.guard()[0], h.posts), ("ok", []))
h = Hub(runs=[], live=OLD)
h.at(50, lambda: h.runs.append(run(status="in_progress")))
check("its deployment starts a moment later: skip (its completion starts the guard)", h.guard()[0], "skip")
h = Hub(runs=[], live=OLD)
r = h.guard()
check("none ever appears and the site stays old: fail", (r[0], "No deployment run was found" in r[1]), ("fail", True))

print("data(): workflow-command escaping")
check("%, CR and LF are escaped", D.data("a%b\r\nc"), "a%25b%0D%0Ac")

print(f"\ntest_deploy_guard: {'FAIL ' + str(len(fails)) if fails else 'all passed'}")
sys.exit(1 if fails else 0)
