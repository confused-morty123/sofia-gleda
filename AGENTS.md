# Working on Sofia Gleda: rules for every agent

Several workers change this repository: Claude project threads (pull requests from
`claude/*` branches), other Claude chats (feature branches such as `*-trial`), the
Mac's scheduled task "CI watch and fix", and the nightly refresh robot. On
2026-11-26 two of them fixed the same failing test (D1) in opposite ways, both
merged, and main went red. These rules stop that. They apply to every agent,
whoever started it.

## One pipeline

```
merge one PR  ->  Tests runs on the new main  ->  main green  ->  the next merge
```

1. **Look first.** Before you push, open a PR, start a fix or merge, run

   ```
   python3 scripts/main_gate.py status
   ```

   It prints whether main is green, red or being tested, who holds the CI-fix
   claim, which PR may merge, and every run in progress. Act on what it says.

2. **Every change reaches main through a pull request.** No direct pushes to main
   from agents (the nightly robot's data commits are the only exception). Not even
   a fast-forward the owner approved: open the PR, the owner merges it.

3. **One merge at a time, only onto a green main.** The `main-gate` status on each
   PR is green only when main's latest Tests run passed, no main run is queued or
   running, and the branch contains every non-robot commit of main. Merge only on a
   green `main-gate`, a green Tests run of the PR itself *and* a green `CodeQL` check
   (code scanning; red means the PR adds a serious new security alert). After a merge, nothing
   else merges until main's Tests run for it is green.

4. **One CI fix at a time.** A failing test on main is fixed by exactly one open PR,
   its title starting `CI fix:` (or labelled `ci-fix`). If `status` shows the claim
   is held, do not write your own fix: read that PR, comment on it, or wait. If it is
   free, open your `CI fix:` PR *before* pushing more fix commits, so the claim is
   visible. Fix only failures on main; another branch's red run belongs to that
   branch's owner.

5. **One test run per change.** Tests run on pull requests to main, on pushes to
   main, and on pushes to `*-trial` branches. A branch with a PR is tested by its PR
   run alone, so:
   - do not name a PR branch `*-trial` (it would be tested twice per push);
   - do not press "Run workflow" for a commit that already has a run (queued,
     running or finished); re-run the failed jobs of that run instead;
   - do not push the same commit to a second branch to get "another" run.

6. **Up to date before merge.** When `main-gate` says the branch is behind, merge
   main into it (`git fetch origin main && git merge origin/main`), rebuild the page
   if `src/` changed on either side (`index.html` is generated, so take neither
   side by hand: `python3 scripts/dev_build.py --from HEAD --out index.html`, as in
   the README's "Making a UI change"), push, and
   let the PR's run go green again.

## Who does what

| Worker | Branches | May merge to main | Fixes failing tests on main |
|---|---|---|---|
| Owner | any | yes (the only one who merges) | any time |
| Claude project threads | `claude/project-thread-*`, PR to main | no, asks the owner | only by holding the CI-fix claim |
| Other chats (features) | `<feature>-trial` until ready, then a PR from a non-trial branch | no | no: report to the CI-fix holder |
| Mac task "CI watch and fix" | `ci-fix-<yyyymmdd>`, PR titled `CI fix: ...` | no | yes, when the claim is free |
| Nightly refresh | main (data only, `Sofia Gleda bot`) | pushes data | no |

## Rulesets

Ruleset 1 ("main: no force-push or deletion", active since 2026-11-26) stops anyone,
the owner included, from force-pushing over main or deleting it.

The owner chose not to enforce a PR-required ruleset (2026-11-26): on a personal
repository GitHub cannot let the nightly refresh's Actions token bypass it, and the
alternative (a deploy key) was not wanted. So rules 2 and 3 above hold by agreement:
GitHub will not stop a direct push to main or a merge on a red `main-gate`. Follow
them anyway.

CodeQL default setup and Dependabot security updates are on (2026-11-26). A ruleset
that blocks new CodeQL alerts ("main: no new CodeQL alerts") exists but is disabled:
GitHub applies it to every push to main, and the nightly refresh's commits have no
CodeQL results, so it refused the robot's data push. The `CodeQL` check on each PR
does the same job by agreement (rule 3).
