#!/usr/bin/env python3
"""Tests for the merge gate (scripts/main_gate.py) and the one-pipeline triggers of
tests.yml and main-gate.yml. No network, under a second.

    python3 scripts/test_main_gate.py

Exit 0 = every check passed.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import main_gate as G  # noqa: E402

WF = ROOT / ".github" / "workflows"
TESTS = (WF / "tests.yml").read_text(encoding="utf-8")
GATE = (WF / "main-gate.yml").read_text(encoding="utf-8")
NOW = dt.datetime(2026, 11, 26, 15, 0, tzinfo=dt.timezone.utc)
fails: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got  {got!r}\n         want {want!r}")
        fails.append(name)


def commit(sha: str, bot: bool = False, minutes_ago: int = 120) -> dict:
    when = (NOW - dt.timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")
    return {"sha": sha * 40, "commit": {"author": {"name": G.BOT if bot else "Georgi"},
                                        "committer": {"date": when}}}


def run(sha: str, status: str = "completed", conclusion: str | None = "success") -> dict:
    return {"head_sha": sha * 40, "status": status, "conclusion": conclusion, "html_url": "u"}


print("main_state")
check("green when main's head passed",
      G.main_state([commit("b"), commit("a")], [run("b"), run("a", conclusion="failure")], NOW)["state"], "green")
check("red when main's head failed",
      G.main_state([commit("b")], [run("b", conclusion="failure")], NOW)["state"], "red")
check("testing while any main run is active, even if an older one passed",
      G.main_state([commit("b"), commit("a")], [run("b", "in_progress", None), run("a")], NOW)["state"], "testing")
check("bot data refreshes on top are skipped: the tested commit below decides",
      G.main_state([commit("c", bot=True), commit("b")], [run("b")], NOW)["state"], "green")
check("a red commit under a bot refresh is still red",
      G.main_state([commit("c", bot=True), commit("b")], [run("b", conclusion="failure")], NOW)["state"], "red")
check("a just-pushed commit with no run yet is testing, not green from the older run",
      G.main_state([commit("b", minutes_ago=2), commit("a")], [run("a")], NOW)["state"], "testing")
check("an old untested commit (text-only) is passed over",
      G.main_state([commit("b", minutes_ago=300), commit("a")], [run("a")], NOW)["state"], "green")
check("a cancelled run does not count; the next result does",
      G.main_state([commit("b", minutes_ago=300), commit("a")],
                   [run("b", conclusion="cancelled"), run("a", conclusion="failure")], NOW)["state"], "red")
check("the newest attempt of a commit wins (a passing re-run)",
      G.main_state([commit("b")], [run("b"), run("b", conclusion="failure")], NOW)["state"], "green")
check("no result at all is red (run Tests on main)", G.main_state([commit("b")], [], NOW)["state"], "red")

print("behind and pr_gate")
cmp_ = {"commits": [commit("x", bot=True), commit("y")]}
check("only non-bot commits count as behind", G.behind(cmp_), ["y" * 7])
check("bot-only difference is up to date", G.behind({"commits": [commit("x", bot=True)]}), [])
green = {"state": "green", "why": ""}
check("green + up to date -> success", G.pr_gate(green, [])[0], "success")
check("green + behind -> failure", G.pr_gate(green, ["abc1234"])[0], "failure")
check("testing -> pending", G.pr_gate({"state": "testing", "why": ""}, [])[0], "pending")
check("red -> failure", G.pr_gate({"state": "red", "why": "x"}, [])[0], "failure")
check("descriptions fit GitHub's 140 characters",
      max(len(G.pr_gate(s, ["a" * 7] * 9)[1]) for s in
          (green, {"state": "red", "why": "y" * 300}, {"state": "testing", "why": ""})) <= 140, True)

print("CI-fix claim")
check("title 'CI fix: ...' claims", G.is_ci_fix({"title": "CI fix: D1 venue count", "labels": []}), True)
check("label ci-fix claims", G.is_ci_fix({"title": "D1", "labels": [{"name": "ci-fix"}]}), True)
check("an ordinary PR does not", G.is_ci_fix({"title": "Fix focus ring", "labels": []}), False)

print("workflows")
check("tests.yml: pushes tested only on main and *-trial",
      'branches: [main, "**-trial"]' in TESTS, True)
check("tests.yml: pull requests only to main", "  pull_request:\n    branches: [main]" in TESTS, True)
check("tests.yml: one group per branch, whatever started the run",
      "group: tests-${{ github.head_ref || github.ref_name }}" in TESTS, True)
check("tests.yml: a main run is never cancelled by the next merge",
      "cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}" in TESTS, True)
check("main-gate.yml: runs main's script, not the PR's", "ref: main" in GATE, True)
check("main-gate.yml: re-evaluated when main's Tests starts and finishes",
      "workflows: [\"Tests\"]" in GATE and "types: [requested, completed]" in GATE, True)
check("main-gate.yml: may write statuses and nothing else",
      "statuses: write" in GATE and "contents: write" not in GATE, True)

print()
if fails:
    print(f"{len(fails)} check(s) failed: {', '.join(fails)}")
    sys.exit(1)
print("all main-gate checks passed")
