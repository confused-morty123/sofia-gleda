#!/usr/bin/env python3
"""The UI waves of scripts/test_ui.py, listed, sharded and run for GitHub Actions
(.github/workflows/tests.yml). No network, no browser needed to list or shard.

    python3 scripts/ci_waves.py list                       # every wave, in test_ui.py order, with its estimate
    python3 scripts/ci_waves.py matrix                     # the GitHub matrix (JSON) for all waves, both engines
    python3 scripts/ci_waves.py matrix --waves "HF1 HS1"   # a smoke run: only these waves
    python3 scripts/ci_waves.py matrix --browsers webkit   # one engine only
    python3 scripts/ci_waves.py run --browser webkit HF1 HS1   # run waves, one test_ui.py process each

The wave list is read from the WAVES dict in test_ui.py (ast, nothing imported), so a
new wave is sharded the day it is added, with DEFAULT_ESTIMATE until it is timed here.

Engines: Chromium runs every wave. WebKit runs WEBKIT_WAVES, the waves written for
both engines (they read the engine and name their checks after it); the others assume
Chromium (CDP touch input, Chromium-only geometry) and are not meant for WebKit.
Waves J and P3B also open WebKit inside the Chromium run, so every shard installs both.

Sharding is deterministic: longest estimate first into the fewest shards whose wave
time stays under --target seconds (a wave longer than the target gets a shard of its
own; one wave cannot be split). Within a shard the waves keep test_ui.py's order.

`run` gives every wave its own process and log (--log-dir), re-runs a wave ONCE when it
failed on the known 30 s page-load timeout (as on the Mac), prints each wave's time
next to its estimate, writes a table to $GITHUB_STEP_SUMMARY, and exits 1 when any wave
failed. An unknown wave name is an error everywhere (test_ui.py itself skips it silently).
"""
import argparse
import ast
import json
import math
import os
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEST_UI = ROOT / "scripts" / "test_ui.py"

# Waves that run in WebKit too (they use the engine in their checks; see the module docstring).
WEBKIT_WAVES = ["HF1", "HF2", "UX1", "B24", "BO1", "DM1", "GA1", "HS1", "LG1", "PL1", "RT1", "NOW1"]

# Seconds per wave, measured on the owner's Mac (shared with other jobs) on a real build
# (SOFIA_HTML=index.html), 2026-11-03 and 2026-11-04 (RT1 2026-11-05), one wave per process. Update from the times `run` prints.
ESTIMATES = {
    "chromium": {
        "A1": 46, "B1": 47, "C1": 56, "D1": 42, "H": 41, "I": 41, "J": 89, "L": 162,
        "M": 38, "N": 3, "P": 14, "P2A": 3, "P2C": 5, "P2D": 3, "P2E": 19,
        "P3D": 4, "P3A": 24, "P3B": 33, "P3C": 7, "P3E": 46, "P3F": 12,
        "HF1": 489, "HF2": 289, "EV1": 3, "EV2": 40, "EV3": 6, "EV4": 12,
        "LG1": 14, "LG2": 12, "LG3": 4, "TR1": 17, "TR2": 4, "UX1": 238,
        "B24": 700, "BO1": 99, "PL1": 61, "DM1": 57, "GA1": 104, "HS1": 38, "RT1": 13,
        "NOW1": 30,
    },
    "webkit": {
        "HF1": 526, "HF2": 289, "UX1": 221, "B24": 760, "BO1": 107, "DM1": 64,
        "GA1": 106, "HS1": 46, "LG1": 15, "PL1": 70, "RT1": 4, "NOW1": 30,
    },
}
DEFAULT_ESTIMATE = 120      # a wave not timed yet
PER_WAVE_OVERHEAD = 6       # one process: Python start, two browser launches
DEFAULT_TARGET = 480        # wave seconds per shard; with ~2 min of setup a shard takes <= ~10 min
BROWSERS = ("chromium", "webkit")
TIMEOUT_MARKERS = ("Timeout 30000ms", "TimeoutError")


def waves():
    """The keys of WAVES = {...} in test_ui.py, in order."""
    tree = ast.parse(TEST_UI.read_text(encoding="utf-8"), filename=str(TEST_UI))
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                and any(isinstance(t, ast.Name) and t.id == "WAVES" for t in node.targets)):
            return [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
    sys.exit("ci_waves: no WAVES dict found in scripts/test_ui.py")


def estimate(browser, wave):
    return ESTIMATES[browser].get(wave, DEFAULT_ESTIMATE) + PER_WAVE_OVERHEAD


def parse_waves(text, known):
    """'HF1, hs1  B24' -> ['HF1', 'HS1', 'B24'] in test_ui.py order; unknown names are an error."""
    names = [t.upper() for t in re.split(r"[\s,]+", text or "") if t]
    bad = [n for n in names if n not in known]
    if bad:
        raise SystemExit(f"ci_waves: unknown wave(s) {' '.join(bad)!r}; known: {' '.join(known)}")
    return [w for w in known if w in names]


def parse_browsers(text):
    t = (text or "").strip().lower().replace(",", " ")
    if t in ("", "both", "all"):
        return list(BROWSERS)
    out = t.split()
    bad = [b for b in out if b not in BROWSERS]
    if bad:
        raise SystemExit(f"ci_waves: unknown browser(s) {bad}; use chromium, webkit or both")
    return [b for b in BROWSERS if b in out]


def from_commit_message(msg):
    """'CI-Waves: HF1 HS1' and 'CI-Browsers: webkit' lines in a commit message (a smoke push)."""
    found = {}
    for line in (msg or "").splitlines():
        m = re.match(r"\s*CI-(Waves|Browsers)\s*:\s*(.*?)\s*$", line, re.I)
        if m:
            found[m.group(1).lower()] = m.group(2)
    return found.get("waves", ""), found.get("browsers", "")


def shard(items, target):
    """items: [(wave, seconds)] -> list of shards, each a list of waves. Longest first
    into the fewest bins whose total stays <= target (a single long wave may exceed it)."""
    if not items:
        return []
    total = sum(s for _, s in items)
    order = sorted(items, key=lambda x: (-x[1], [w for w, _ in items].index(x[0])))
    k = max(1, math.ceil(total / target))
    while True:
        bins = [[0, []] for _ in range(k)]
        for w, s in order:
            b = min(bins, key=lambda x: x[0])      # first of the least loaded (stable)
            b[0] += s
            b[1].append(w)
        if all(t <= target or len(ws) == 1 for t, ws in bins) or k >= len(items):
            break
        k += 1
    pos = {w: i for i, (w, _) in enumerate(items)}
    out = [sorted(ws, key=pos.get) for _, ws in bins if ws]
    return sorted(out, key=lambda ws: pos[ws[0]])


def plan(selected, browsers, explicit, target):
    """The matrix include list: one entry per shard."""
    include = []
    for br in browsers:
        ws = selected if (br == "chromium" or explicit) else [w for w in selected if w in WEBKIT_WAVES]
        shards = shard([(w, estimate(br, w)) for w in ws], target)
        for i, s in enumerate(shards, 1):
            est = sum(estimate(br, w) for w in s)
            include.append({"browser": br, "name": f"{br} {i}/{len(shards)}",
                            "waves": " ".join(s), "estimate_s": est})
    return include


def cmd_list(_args):
    for w in waves():
        wk = ESTIMATES["webkit"].get(w, DEFAULT_ESTIMATE) if w in WEBKIT_WAVES else None
        print(f"{w:5s} chromium {ESTIMATES['chromium'].get(w, DEFAULT_ESTIMATE):4d} s"
              + (f"   webkit {wk:4d} s" if wk is not None else ""))


def cmd_matrix(args):
    known = waves()
    msg_waves, msg_browsers = from_commit_message(os.environ.get("CI_COMMIT_MESSAGE", ""))
    wave_text = args.waves if args.waves is not None else os.environ.get("CI_WAVES", "") or msg_waves
    br_text = args.browsers if args.browsers is not None else os.environ.get("CI_BROWSERS", "") or msg_browsers
    selected = parse_waves(wave_text, known) if wave_text.strip() else known
    browsers = parse_browsers(br_text)
    # a WebKit-only run asked for by name runs the named waves in WebKit, whatever WEBKIT_WAVES says
    explicit = bool(wave_text.strip()) and browsers == ["webkit"]
    include = plan(selected, browsers, explicit, args.target)
    out = {"include": include}
    if args.github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"matrix={json.dumps(out, separators=(',', ':'))}\n")
            fh.write(f"count={len(include)}\n")
        scope = "all waves" if not wave_text.strip() else "smoke: " + " ".join(selected)
        lines = [f"## UI shards ({scope}; {', '.join(browsers)})", "",
                 "| shard | waves | estimate |", "|---|---|---|"]
        lines += [f"| {e['name']} | {e['waves']} | {e['estimate_s'] // 60} min {e['estimate_s'] % 60:02d} s |"
                  for e in include]
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
    print(json.dumps(out, indent=None if args.github_output else 1))


def _annotation(text, limit=3800):
    """Text made safe for one workflow-command line: % CR LF escaped as GitHub requires,
    so nothing in it can end the annotation or start another command."""
    text = text if len(text) <= limit else text[:limit] + " [...]"
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def run_wave(browser, wave, log):
    env = dict(os.environ, SOFIA_BROWSER=browser)
    env.setdefault("SOFIA_HTML", "index.html")
    env.setdefault("SOFIA_SKIP_FRESHNESS", "1")
    with open(log, "w", encoding="utf-8") as fh:
        rc = subprocess.run([sys.executable, "scripts/test_ui.py", wave], cwd=ROOT, env=env,
                            stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT).returncode
    return rc, pathlib.Path(log).read_text(encoding="utf-8", errors="replace")


def cmd_run(args):
    known = waves()
    selected = parse_waves(" ".join(args.waves), known)
    if not selected:
        sys.exit("ci_waves run: no waves given")
    logs = pathlib.Path(args.log_dir)
    logs.mkdir(parents=True, exist_ok=True)
    rows, failures = [], []
    for w in selected:
        log = logs / f"{args.browser}_{w}.log"
        t0 = time.time()
        rc, text = run_wave(args.browser, w, log)
        rerun = False
        if rc != 0 and any(m in text for m in TIMEOUT_MARKERS):
            log.rename(logs / f"{args.browser}_{w}.first.log")
            print(f"ci_waves: {w} hit a timeout; re-running it once", flush=True)
            rerun = True
            rc, text = run_wave(args.browser, w, log)
        secs = round(time.time() - t0)
        total = (re.findall(r"^Total: (.*)$", text, re.M) or ["no summary (crashed?)"])[-1]
        fails = re.findall(r"^\s*FAIL \[[^\]]*\] (.*)$", text, re.M)
        status = "PASS" if rc == 0 else "FAIL"
        rows.append((w, status, total, secs, rerun))
        if rc != 0:
            failures.append((w, fails or [f"exit code {rc}; see {log.name}"]))
            # one annotation per failed wave (public through the check-runs API, unlike the
            # logs): the failed check names only, which are identifiers, never page text
            names = [re.split(r" \u2014 | - ", f, maxsplit=1)[0].strip() for f in fails]
            names = [n for n in names if re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", n)]
            more = f" (+{len(names) - 8} more)" if len(names) > 8 else ""
            print(f"::error title=UI wave {w} ({args.browser})::{len(fails)} failed check(s): "
                  f"{', '.join(names[:8]) or 'see ' + log.name}{more}", flush=True)
            # a second annotation with what the run's logs would show (the logs need a
            # sign-in; annotations do not): each failed check's detail, or, for a wave that
            # crashed or timed out before its summary, the end of its log
            detail = "\n".join(f[:400] for f in fails) if fails else "\n".join(
                [l[:300] for l in text.rstrip().splitlines()[-30:]])
            print(f"::notice title=UI wave {w} ({args.browser}) details::{_annotation(detail)}", flush=True)
        print(f"ci_waves: {args.browser} {w} {status} {total} in {secs} s "
              f"(estimate {ESTIMATES[args.browser].get(w, DEFAULT_ESTIMATE)} s){' after a re-run' if rerun else ''}",
              flush=True)
    if os.environ.get("GITHUB_ACTIONS"):
        # the shard's times, public with the annotations (the summary page needs a sign-in)
        times = "; ".join(f"{w} {st} {sec} s (estimate {ESTIMATES[args.browser].get(w, DEFAULT_ESTIMATE)} s)"
                          f"{', re-run' if r else ''}" for w, st, _, sec, r in rows)
        print(f"::notice title=UI shard times ({args.browser})::{_annotation(times)}", flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        lines = [f"## UI waves, {args.browser}: {', '.join(selected)}", "",
                 "| wave | result | checks | time | note |", "|---|---|---|---|---|"]
        lines += [f"| {w} | {s} | {t} | {sec} s | {'re-run after a timeout' if r else ''} |"
                  for w, s, t, sec, r in rows]
        if failures:
            # check details quote page data (titles), so they go in a code fence longer than any inside
            body = "\n".join(f"[{w}] {f}" for w, fs in failures for f in fs)
            fence = "`" * max(3, 1 + max((len(m) for m in re.findall(r"`+", body)), default=0))
            lines += ["", "### Failed checks", fence, body, fence]
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    sys.exit(1 if failures else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="every wave with its estimate")
    m = sub.add_parser("matrix", help="the GitHub Actions matrix as JSON")
    m.add_argument("--waves", default=None, help="only these waves (default: $CI_WAVES, a CI-Waves: line "
                   "in $CI_COMMIT_MESSAGE, else all)")
    m.add_argument("--browsers", default=None, help="chromium, webkit or both (default: $CI_BROWSERS, "
                   "a CI-Browsers: line, else both)")
    m.add_argument("--target", type=int, default=DEFAULT_TARGET, help="wave seconds per shard")
    m.add_argument("--github-output", action="store_true", help="also write matrix/count to $GITHUB_OUTPUT")
    r = sub.add_parser("run", help="run waves, one test_ui.py process each")
    r.add_argument("--browser", choices=BROWSERS, default="chromium")
    r.add_argument("--log-dir", default="ci-logs")
    r.add_argument("waves", nargs="+")
    args = ap.parse_args()
    {"list": cmd_list, "matrix": cmd_matrix, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    main()
