#!/usr/bin/env python3
"""Sofia Gleda: the deploy guard (.github/workflows/deploy-guard.yml).

GitHub's own "pages build and deployment" run sometimes fails in its deploy job
("Timeout reached, aborting!") although its build job passed: on 2026-11-09 (fixed
by hand with "Re-run failed jobs") and on 2026-11-10 (not re-run: the site showed
the previous day's programme for 24 hours). The guard does that click itself and
then checks that the live site really serves the new data.

Every look is at how things are NOW, never at what the event that started the
run said, so a lost, late or duplicate event changes nothing:

  1. main's tip must be the commit this run checked out. If main has moved on:
     skip (the newer commit's own deployment starts the guard again). The guard
     never touches the deployment of a commit that is not main's tip: re-running
     an older one could put OLD content live.
  2. The Pages runs of that commit:
       still running          -> skip (its completion starts the guard again);
       failed / timed out     -> ask GitHub to re-run the failed jobs, wait for the
                                 re-run and look again: at most MAX_RETRIES times
                                 per commit, then fail;
       cancelled and the like -> fail (the guard does not restart what a person
                                 or GitHub cancelled);
       succeeded, or no run   -> compare the SNAPSHOT "generated" stamp of the live
                                 index.html with the committed one: LIVE_TRIES
                                 polite GETs, LIVE_GAP seconds apart. Still
                                 different -> fail.
  3. "fail" = exit 1 with one plain message that says what to click.

A re-run is asked for only while main's tip is that commit: checked right before
asking and again right after, and if main moved in between the re-run is
cancelled at once (a newer commit's deployment first has to build, about half a
minute, so the cancel arrives first).

The loop is bounded twice: GitHub counts the attempts of a run and the guard asks
only while a commit has had 1 or 2 (so at most 2 re-runs per commit, whoever
started the guard and however often), and one guard run never asks more than
MAX_RETRIES times.

    python3 scripts/deploy_guard.py             # in the workflow (needs GH_TOKEN)
    python3 scripts/deploy_guard.py --dry-run   # read and decide only, never re-run

Standard library only: the workflow installs nothing.
Tests: python3 scripts/test_deploy_guard.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = "https://sofiagleda.com"                     # = site_config.SITE_URL (test_deploy_guard.py)
PAGES_PATH = "dynamic/pages/pages-build-deployment" # GitHub's own Pages workflow
MAX_RETRIES = 2                 # re-runs the guard asks for, per commit
RETRYABLE = ("failure", "timed_out")
NO_RUN = "no_run"               # the commit has no Pages run (yet)
LIVE_TRIES = 6                  # GETs of the live page ...
LIVE_GAP = 60                   # ... this many seconds apart: 5 minutes in all
RERUN_WAIT = 14 * 60            # a re-run is over by then (GitHub's deploy gives up after 10 min)
POLL = 30                       # s between two looks at a re-run in progress
UA = "sofia-gleda-deploy-guard (+https://github.com/confused-morty123/sofia-gleda)"
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:\d\d)?")
RUN_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/actions/runs/\d+")
MAX_PAGE = 50_000_000           # bytes read of the live page at most (it is about 2.3 MB)

CLICK_RERUN = ("What to click: the repository's Actions tab > 'pages build and deployment' > the TOP "
               "run only (never an older one: that would put old content live) > 'Re-run jobs' > "
               "'Re-run failed jobs'. If that fails too, or more than a day has passed: Actions > "
               "'Daily refresh' > 'Run workflow' (a new refresh publishes a new deployment).")
CLICK_STALE = (f"What to do: reload {SITE} in ten minutes. If it is still old: Actions > 'Deploy "
               "guard' > 'Run workflow' checks again, and Actions > 'Daily refresh' > 'Run workflow' "
               "publishes a new deployment.")


def short(sha) -> str:
    """The first 7 characters of a commit id; '' for anything that is not one."""
    return sha[:7] if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) else ""


def stamp_of(page) -> str | None:
    """SNAPSHOT.generated of a built index.html (bytes or str), or None. Only an
    ISO 8601 stamp is accepted, so what the guard prints can carry nothing else."""
    if not page:
        return None
    text = page.decode("utf-8", "replace") if isinstance(page, bytes) else page
    m = re.search(r"^const SNAPSHOT\s*=\s*(\{.*?\});\s*$", text, flags=re.M)
    if not m:
        return None
    try:
        gen = json.loads(m.group(1)).get("generated")
    except (ValueError, AttributeError):
        return None
    return gen if isinstance(gen, str) and STAMP.fullmatch(gen) else None


def decide(conclusion, attempt, run_sha, main_sha, live_stamp, committed_stamp,
           last_check: bool = False) -> tuple[str, str]:
    """('retry' | 'verify' | 'ok' | 'skip' | 'fail', message). Pure: no clock, no IO.

    conclusion       of the commit's Pages run: GitHub's word once it has completed
                     ('success', 'failure', ...), None while it runs, NO_RUN if
                     the commit has none.
    attempt          deployment attempts of that commit so far (GitHub's count).
    run_sha          the commit the run deployed; main_sha: main's tip now.
    live_stamp       SNAPSHOT.generated the live site serves (None = not readable).
    committed_stamp  the same stamp in the index.html committed with run_sha.
    last_check       True on the last look at the live site: 'verify' becomes 'fail'.

    'verify' = look at the live site again in a minute; 'ok' = it serves the commit's data.
    """
    sha = short(run_sha)
    if not sha or run_sha != main_sha:
        return "skip", (f"main has moved on to {short(main_sha) or 'another commit'}: the deployment of "
                        f"{sha or 'that commit'} is left alone (re-running it could put OLD content live). "
                        "The newer commit's own deployment starts the guard again.")
    if conclusion is None:
        return "skip", f"The deployment of {sha} is still running; the guard starts again when it completes."
    if conclusion in RETRYABLE:
        try:
            n = int(attempt)
        except (TypeError, ValueError):
            n = 0
        if 1 <= n <= MAX_RETRIES:
            return "retry", (f"The deployment of {sha} failed (attempt {n}): asking GitHub to re-run its "
                             f"failed jobs (retry {n} of {MAX_RETRIES}).")
        times = f"failed {n} times" if n > MAX_RETRIES else "failed (the number of attempts could not be read)"
        return "fail", (f"The deployment of {sha} {times}; the guard re-runs a commit's deployment at most "
                        f"{MAX_RETRIES} times. {SITE} still shows the previous programme. {CLICK_RERUN}")
    if conclusion not in ("success", NO_RUN):
        word = conclusion if isinstance(conclusion, str) and re.fullmatch(r"[a-z_]{1,30}", conclusion) else "unknown"
        return "fail", (f"The deployment of {sha} ended as '{word}', which the guard does not retry. "
                        f"{SITE} may still show the previous programme. {CLICK_RERUN}")
    what = (f"GitHub reports the deployment of {sha} as successful" if conclusion == "success"
            else f"No deployment run was found for {sha}")
    if not committed_stamp:
        return "fail", (f"{what}, but the committed index.html has no readable SNAPSHOT 'generated' stamp, "
                        "so the guard cannot check the live site. Nothing to click: this is a fault in the "
                        "page or in scripts/deploy_guard.py (stamp_of).")
    if live_stamp == committed_stamp:
        return "ok", f"{what}; {SITE} serves its data (generated {committed_stamp})."
    shows = f"serves the data generated {live_stamp}" if live_stamp else "could not be read (or carries no stamp)"
    if not last_check:
        return "verify", f"{what}, but {SITE} {shows}, not {committed_stamp}: looking again in a minute."
    return "fail", (f"{what}, but after {LIVE_TRIES} looks over {(LIVE_TRIES - 1) * LIVE_GAP // 60} minutes "
                    f"{SITE} still {shows}, not the data committed with {sha} (generated {committed_stamp}). "
                    f"{CLICK_STALE}")


def summarise(runs: list[dict]) -> tuple[str | None, int, dict | None]:
    """One commit's Pages runs -> (conclusion for decide(), attempts so far, the run
    to re-run). Running beats everything; one success means the commit is deployed;
    otherwise the run that was active last speaks."""
    if not runs:
        return NO_RUN, 0, None
    attempts = sum(max(1, int(r.get("run_attempt") or 1)) for r in runs)
    last = max(runs, key=lambda r: (str(r.get("run_started_at") or r.get("created_at") or ""), int(r.get("id") or 0)))
    if any(r.get("status") != "completed" for r in runs):
        return None, attempts, last
    if any(r.get("conclusion") == "success" for r in runs):
        return "success", attempts, last
    return str(last.get("conclusion") or "unknown"), attempts, last


def main_tip(api) -> str | None:
    """main's tip commit now, or None when GitHub's API cannot be read."""
    status, body = api("GET", "/git/ref/heads/main")
    sha = ((body or {}).get("object") or {}).get("sha") if isinstance(body, dict) else None
    return sha if status == 200 and short(sha) else None


def pages_runs(api, sha: str) -> list[dict] | None:
    """The Pages runs of one commit, or None when GitHub's API cannot be read."""
    status, body = api("GET", f"/actions/runs?head_sha={sha}&per_page=100")
    if status != 200 or not isinstance(body, dict):
        return None
    return [r for r in body.get("workflow_runs") or []
            if isinstance(r, dict) and r.get("path") == PAGES_PATH and r.get("head_sha") == sha]


def rerun(api, run_id: int, sha: str) -> tuple[str, str]:
    """Ask GitHub to re-run the failed jobs of one Pages run, only while sha is
    main's tip: checked right before asking and again right after; if main moved
    in between (or cannot be read), the re-run is cancelled at once.
    -> ('asked' | 'moved' | 'refused' | 'not_cancelled', message)."""
    if main_tip(api) != sha:
        return "moved", f"main moved on just now: the deployment of {short(sha)} is not re-run."
    status, _ = api("POST", f"/actions/runs/{int(run_id)}/rerun-failed-jobs")
    if status not in (201, 202, 204):
        return "refused", (f"GitHub refused the re-run of the deployment of {short(sha)} (HTTP {status}). "
                           f"{CLICK_RERUN}")
    if main_tip(api) == sha:
        return "asked", "GitHub accepted the re-run."
    status, _ = api("POST", f"/actions/runs/{int(run_id)}/cancel")
    if status in (202, 204, 409):                   # 409: nothing left to cancel
        return "moved", (f"main moved on while the guard asked for a re-run of the deployment of {short(sha)}: "
                         "the re-run was cancelled; the newer commit's own deployment starts the guard again.")
    return "not_cancelled", (
        f"main moved on while the guard asked for a re-run of the deployment of {short(sha)}, and the re-run "
        f"could not be cancelled (HTTP {status}): {SITE} may show OLD content. What to click: Actions > "
        "'pages build and deployment': if the top run in the list did not finish last, Actions > 'Daily "
        "refresh' > 'Run workflow' publishes a new deployment.")


def guard(head: str, page: bytes, api, live, clock=time.time, sleep=time.sleep,
          log=print, dry_run: bool = False) -> tuple[str, str]:
    """The whole guard with its IO injectable (the tests pass fakes). Returns the
    final ('ok' | 'skip' | 'fail', message).

    head  the commit this run checked out; page: its index.html (bytes).
    api   (method, path under /repos/<repo>) -> (HTTP status, parsed JSON or None).
    live  (n) -> the live index.html as bytes, or None; n counts the GETs from 1.
    """
    committed = stamp_of(page)
    retries = looks = 0
    wait_until = seen = None
    while True:
        tip = main_tip(api)
        runs = pages_runs(api, head) if tip == head else []
        if tip is None or runs is None:
            return "fail", ("GitHub's API could not be read, so the guard could not check the deployment. "
                            f"Check that {SITE} shows today's programme; Actions > 'Deploy guard' > "
                            "'Run workflow' checks again.")
        conclusion, attempts, run = summarise(runs)
        if wait_until and tip == head and clock() < wait_until and (conclusion is None or attempts <= seen):
            sleep(POLL)                             # the re-run asked for is not over yet
            continue
        wait_until = None
        body = None
        if tip == head and conclusion in ("success", NO_RUN):
            looks += 1
            body = live(looks)
        action, msg = decide(conclusion, attempts, head, tip, stamp_of(body), committed,
                             last_check=looks >= LIVE_TRIES)
        link = str((run or {}).get("html_url") or "")
        if action in ("retry", "fail") and RUN_URL.fullmatch(link):
            msg += f" The run: {link}"
        if action == "ok" and body is not None:
            same = hashlib.sha256(body).digest() == hashlib.sha256(page).digest()
            msg += (" The live index.html is byte for byte the committed one." if same else
                    " Note: the stamps match, but the live index.html is not byte for byte the committed one.")
        log(msg)
        if action == "retry":
            if retries >= MAX_RETRIES:              # the second bound: cannot be passed
                return "fail", (f"The guard already asked for {retries} re-runs in this run and stops. "
                                f"{CLICK_RERUN}")
            if dry_run:
                return "skip", "Dry run: the guard would ask GitHub to re-run the failed jobs now."
            result, why = rerun(api, run["id"], head)
            log(why)
            if result == "refused" and summarise(pages_runs(api, head) or [])[0] is None:
                return "skip", (f"The deployment of {short(head)} is already being re-run by someone else; "
                                "the guard starts again when it completes.")
            if result != "asked":
                return ("skip" if result == "moved" else "fail"), why
            retries, seen, wait_until = retries + 1, attempts, clock() + RERUN_WAIT
            sleep(POLL)
            continue
        if action == "verify":
            sleep(LIVE_GAP)
            continue
        return action, msg


def github(method: str, url: str, token: str | None, timeout: int = 30) -> tuple[int, object]:
    """One GitHub REST call -> (HTTP status, parsed JSON or None); 0 = no answer."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, method=method, headers=headers, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        return e.code, None
    except (OSError, ValueError):
        return 0, None


def fetch_live(n: int, timeout: int = 30) -> bytes | None:
    """One polite GET of the live page, past the CDN's 10-minute cache (a query the
    cache has not seen); None when it cannot be read."""
    req = urllib.request.Request(f"{SITE}/index.html?deploy-guard={int(time.time())}-{int(n)}",
                                 headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(MAX_PAGE) if resp.status == 200 else None
    except (OSError, ValueError):
        return None


def data(s) -> str:
    """Escaped for a GitHub workflow command (as in refresh.yml's health report)."""
    return str(s).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> int:
    ap = argparse.ArgumentParser(description="Sofia Gleda deploy guard (see the top of this file)")
    ap.add_argument("--dry-run", action="store_true", help="read and decide only: never re-run anything")
    a = ap.parse_args()
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        print("GITHUB_REPOSITORY is not set (owner/name)")
        return 2
    token = os.environ.get("GH_TOKEN") or None
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True,
                          text=True).stdout.strip()
    page = (ROOT / "index.html").read_bytes()

    def api(method: str, path: str) -> tuple[int, object]:
        url = f"https://api.github.com/repos/{repo}{path}"
        for attempt in (1, 2, 3):                   # a GET is tried three times; a POST once
            status, body = github(method, url, token)
            if method != "GET" or (status and status < 500) or attempt == 3:
                return status, body
            time.sleep(5)
        return 0, None

    # What started this run: printed only. The guard decides on what it reads now.
    woke_sha = short(os.environ.get("WOKEN_BY_SHA"))
    if woke_sha:
        word = os.environ.get("WOKEN_BY_CONCLUSION") or ""
        tries = os.environ.get("WOKEN_BY_ATTEMPT") or ""
        print(f"Started by the deployment of {woke_sha}"
              + (f" ({word}" + (f", attempt {tries}" if tries.isdigit() else "") + ")"
                 if re.fullmatch(r"[a-z_]{1,30}", word) else "")
              + (f"; this run checked out {short(head)}, so main has moved on since: {woke_sha} is left alone."
                 if woke_sha != short(head) else "."))
    else:
        print(f"Started by {os.environ.get('GITHUB_EVENT_NAME') or 'hand'}; this run checked out {short(head)}.")

    action, msg = guard(head, page, api, fetch_live, dry_run=a.dry_run)
    print(f"::{'error' if action == 'fail' else 'notice'} title=Deploy guard::" + data(msg))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"## Deploy guard: {action}\n\n{msg}\n")
    return 1 if action == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
