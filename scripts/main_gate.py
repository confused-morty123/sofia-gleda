#!/usr/bin/env python3
"""One queue for every change to main: the merge gate and the agents' preflight.

Three kinds of worker change this repository: Claude project threads (pull requests
from claude/* branches), other Claude chats (feature branches such as *-trial), and
the Mac's scheduled "CI watch and fix" task. When they merged and tested at the same
time, two fixes for one failing test landed together and broke main. The rule now:

    merge one PR -> Tests run on the new main -> only when main is green, the next merge

and every worker looks before it acts. This script is both halves:

    python3 scripts/main_gate.py status          what is happening now, what you may do
    python3 scripts/main_gate.py status --json   the same, for a script
    python3 scripts/main_gate.py post            (workflow only) set the "main-gate"
                                                 commit status on every open PR to main

"main-gate" on a pull request is green only when
  1. main is green: the newest Tests run of main's newest tested commit passed, and no
     Tests run of main is queued or running (a merge is being tested: wait), and
  2. the PR branch already contains every commit of main except the nightly bot's
     data refreshes (so its own Tests run tested it on top of today's main).

A CI fix is "claimed" by an open pull request whose title starts with "CI fix" or that
carries the label "ci-fix". While one is open, nobody else starts another fix for a
failing test: comment on that PR or wait for it (AGENTS.md, "One pipeline").

Reads the public GitHub API without a token (60 requests an hour); GITHUB_TOKEN or
GH_TOKEN raises that. Falls back to curl when Python has no CA bundle (the Mac's
python.org build). Standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "confused-morty123/sofia-gleda")
API = "https://api.github.com"
BOT = "Sofia Gleda bot"                 # refresh.yml's committer: data only, never tested
CONTEXT = "main-gate"
TESTS = "tests.yml"
FRESH_MINUTES = 15                      # an untested main commit younger than this is
                                        # still waiting for its Tests run to appear
ACTIVE = {"queued", "in_progress", "waiting", "requested", "pending"}


# ---------------------------------------------------------------- GitHub access
def _token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def github(method: str, path: str, body: dict | None = None):
    url = path if path.startswith("http") else f"{API}/repos/{REPO}/{path}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "sofia-gleda-main-gate",
               "X-GitHub-Api-Version": "2022-11-28"}
    tok = _token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    data = json.dumps(body).encode() if body is not None else None
    try:
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"null")
    except urllib.error.URLError as e:
        if isinstance(e, urllib.error.HTTPError) or "CERTIFICATE" not in str(e).upper():
            raise
    # No CA bundle for GitHub in this Python: ask curl (the system's trust store).
    cmd = ["curl", "-sS", "-f", "--max-time", "30", "-X", method, url]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    if data is not None:
        cmd += ["--data-binary", "@-"]
    out = subprocess.run(cmd, input=data, capture_output=True, timeout=40)
    if out.returncode:
        raise RuntimeError(f"curl {method} {url}: {out.stderr.decode(errors='replace')[:200]}")
    return json.loads(out.stdout or b"null")


def get(path: str):
    return github("GET", path)


# ---------------------------------------------------------------- the decision
def _age_minutes(stamp: str, now: dt.datetime) -> float:
    t = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return (now - t).total_seconds() / 60


def is_bot(commit: dict) -> bool:
    c = commit.get("commit", {})
    return (c.get("author") or {}).get("name") == BOT


def main_state(commits: list[dict], runs: list[dict], now: dt.datetime) -> dict:
    """commits: main's newest commits, newest first (GET commits?sha=main).
    runs: Tests runs of main (event push), newest first.
    Returns {"state": green|red|testing, "sha", "why", "run"}."""
    active = [r for r in runs if r.get("status") in ACTIVE]
    if active:
        r = active[0]
        return {"state": "testing", "sha": r["head_sha"], "run": r.get("html_url"),
                "why": f"Tests run on main {r['head_sha'][:7]} is {r['status']}: wait for it"}
    by_sha: dict[str, list[dict]] = {}
    for r in runs:
        by_sha.setdefault(r["head_sha"], []).append(r)
    for c in commits:
        sha = c["sha"]
        tested = [r for r in by_sha.get(sha, []) if r.get("conclusion") not in (None, "cancelled", "skipped")]
        if tested:
            r = tested[0]                               # newest attempt of that commit
            if r["conclusion"] == "success":
                return {"state": "green", "sha": sha, "run": r.get("html_url"),
                        "why": f"main {sha[:7]} passed Tests"}
            return {"state": "red", "sha": sha, "run": r.get("html_url"),
                    "why": f"main {sha[:7]} failed Tests ({r['conclusion']}): fix main first"}
        if is_bot(c):
            continue                                    # data refresh: never tested
        when = ((c.get("commit") or {}).get("committer") or {}).get("date")
        if when and _age_minutes(when, now) < FRESH_MINUTES:
            return {"state": "testing", "sha": sha, "run": None,
                    "why": f"main {sha[:7]} was just pushed; its Tests run has not started yet"}
        # Older and never tested: a text-only (*.md) or [skip ci] commit. Look further back.
    return {"state": "red", "sha": commits[0]["sha"] if commits else "", "run": None,
            "why": "no Tests result for main's recent commits: run Tests on main (Run workflow)"}


def behind(compare: dict) -> list[str]:
    """compare: GET compare/{pr_head}...{main_head}. Non-bot main commits the PR lacks."""
    return [c["sha"][:7] for c in compare.get("commits", []) if not is_bot(c)]


def is_ci_fix(pr: dict) -> bool:
    return pr["title"].lower().startswith("ci fix") or any(
        lb.get("name") == "ci-fix" for lb in pr.get("labels", []))


def pr_gate(main: dict, missing: list[str]) -> tuple[str, str]:
    """(commit status state, description) for one PR."""
    if main["state"] == "testing":
        return "pending", "Main is being tested; merge after it is green"[:140]
    if main["state"] == "red":
        return "failure", ("Main is red: " + main["why"])[:140]
    if missing:
        return "failure", (f"Behind main by {len(missing)} commit(s) ({', '.join(missing[:3])}): "
                           "merge main into this branch")[:140]
    return "success", "Main is green and this branch is up to date: OK to merge"


# ---------------------------------------------------------------- gathering
def gather(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    commits = get("commits?sha=main&per_page=30")
    runs = get(f"actions/workflows/{TESTS}/runs?branch=main&event=push&per_page=30")["workflow_runs"]
    main = main_state(commits, runs, now)
    main["head"] = commits[0]["sha"] if commits else ""
    prs = []
    for pr in get("pulls?state=open&base=main&per_page=100"):
        missing = behind(get(f"compare/{pr['head']['sha']}...{main['head']}"))
        state, why = pr_gate(main, missing)
        prs.append({"number": pr["number"], "title": pr["title"], "branch": pr["head"]["ref"],
                    "sha": pr["head"]["sha"], "draft": pr.get("draft", False),
                    "ci_fix": is_ci_fix(pr), "gate": state, "gate_why": why,
                    "url": pr["html_url"], "same_repo": pr["head"]["repo"] is not None
                    and pr["head"]["repo"]["full_name"] == REPO})
    active = get("actions/runs?status=in_progress&per_page=50")["workflow_runs"] + \
        get("actions/runs?status=queued&per_page=50")["workflow_runs"]
    running = [{"name": r["name"], "branch": r["head_branch"], "event": r["event"],
                "sha": r["head_sha"][:7], "url": r["html_url"]} for r in active]
    return {"main": main, "prs": prs, "running": running}


def verdicts(s: dict) -> list[str]:
    out = []
    m = s["main"]
    out.append(f"MAIN      {m['state'].upper():8} {m['why']}" + (f"  {m['run']}" if m.get("run") else ""))
    fixes = [p for p in s["prs"] if p["ci_fix"]]
    if fixes:
        out.append("CI-FIX    CLAIMED  by " + ", ".join(f"#{p['number']} ({p['branch']})" for p in fixes)
                   + ": do not start another fix; review or comment on that PR")
    else:
        out.append("CI-FIX    FREE     open a PR titled 'CI fix: ...' to claim it before you push a fix")
    ready = [p for p in s["prs"] if p["gate"] == "success" and not p["draft"]]
    if m["state"] != "green":
        out.append("MERGE     WAIT     nothing may merge until main is green")
    elif ready:
        out.append(f"MERGE     ONE      at most one of: {', '.join('#%d' % p['number'] for p in ready)}"
                   " (then wait for main's Tests run)")
    else:
        out.append("MERGE     NONE     no open PR is up to date with main")
    for p in s["prs"]:
        out.append(f"  PR #{p['number']:<4} {p['gate']:8} {p['branch']}: {p['title'][:60]}"
                   f"{'  [CI fix]' if p['ci_fix'] else ''}{'  [draft]' if p['draft'] else ''}\n"
                   f"            {p['gate_why']}")
    for r in s["running"]:
        out.append(f"  RUNNING  {r['name']} on {r['branch']} ({r['event']}, {r['sha']}) {r['url']}")
    return out


def post(s: dict) -> int:
    """Set main-gate on every same-repository open PR (fork PRs cannot be written to)."""
    run_url = None
    if os.environ.get("GITHUB_RUN_ID"):
        run_url = f"https://github.com/{REPO}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    failed = 0
    for p in s["prs"]:
        if not p["same_repo"]:
            continue
        body = {"state": p["gate"], "context": CONTEXT, "description": p["gate_why"]}
        if run_url:
            body["target_url"] = run_url
        try:
            github("POST", f"statuses/{p['sha']}", body)
            print(f"PR #{p['number']} {p['sha'][:7]}: {p['gate']} - {p['gate_why']}")
        except Exception as e:  # noqa: BLE001 - one PR's error must not hide the others
            failed += 1
            print(f"PR #{p['number']}: could not set the status: {e}", file=sys.stderr)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["status", "post"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    s = gather()
    if a.command == "post":
        print("\n".join(verdicts(s)))
        return post(s)
    if a.json:
        print(json.dumps(s, indent=2, ensure_ascii=False))
    else:
        print("\n".join(verdicts(s)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
