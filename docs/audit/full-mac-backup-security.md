# Security review: feat/full-mac-backup (security-engineer)

Date: 2026-10-20 · gitleaks 8.30.1, git 2.56.0, /bin/bash 3.2 · Saved by the orchestrator
(reviewer is read-only). Experiments used throwaway repos with random fake `ghp_` tokens.
No dependency audit tool was available; the diff adds no dependencies (`PySide6` unpinned).

Threat model in short: assets are secrets in working trees, unpublished work of the public
repos (sentinel, sonar, imprint, lab_hub), the owner's real email, and the truth of the
"Backed up" claim. Boundaries: Mac ↔ GitHub (private backup repo, often-public origins),
repo contents ↔ the scanner that reads its config from the same repo, and the environment.

## P0
1. **A scanner error reads as clean.** gitleaks exits 0 when its own `git log` fails
   (`[git] fatal…`, `stderr is not empty`, `0 commits scanned`, `no leaks found`); the engine
   treats only rc ≥ 2 as an error. Reproduced end to end with one broken tag ref: a side-branch
   secret and an untracked-file secret both reached the backup repo. Pre-existing for origin
   pushes; this branch widens it. No repo triggers it today. Fix: fail closed on error output
   and pre-check `git rev-list --all`.

## P1
1. **TOCTOU between plan and scan** (same as the code review's P1): a branch tip moved
   mid-run is pushed unscanned. Fix: add every planned SHA to the `--not --all` scan.
2. **Content gitleaks cannot see is pushed**: SQLite DBs, PDFs/XLSX (gitleaks path
   allowlist), DER keys, `.netrc`, low-entropy passwords in `.env.local`. Fix: leave out and
   report a name/extension denylist and untracked binaries, like big files.
3. **The scanned repo's working tree controls the scanner config**: an untracked
   `.gitleaks.toml` with `paths = ['''.*''']` disabled the gate. Fix: use the committed config.

## P2
1. Merge commits are not scanned; a secret introduced only in a merge resolution is pushed.
   `--diff-merges` fixes it.
2. `stash push -a` stashes carry ignored files (`.env`) into the backup.
3. The "backup repo is private" invariant is never checked at run time, and a repo's
   `url.*.insteadOf` / `pushInsteadOf` could redirect the push.
4. Commits with any author/committer email can now reach GitHub. Census: 3 local-only commits
   (lab_hub 1, sonar 2) carry `noreply@anthropic.com`; none carries the owner's address.

## P3
- `xargs` quote-parses refspecs: a branch named `it's-fine` fails the repo's backup.
- Worktree basename collisions fail the push (same as review P2-1).
- Nested repos and submodules are backed up as gitlinks only, yet the row says Backed up.
- `$((depth+1))` evaluates `AUTOSYNC_SCAN_ROOTS` input arithmetically; validate digits.
- Big-file loop misses C-quoted paths (same as review P2-2).
- Dry and blocked runs leave unreferenced blobs in `.git/objects` (local, pruned by gc).
- git-lfs would bypass gitleaks if adopted (none today).

Checked and sound: snapshot/stash pushes are by SHA and fully inside the `--not --all` scan;
dry runs never push; refspecs stay under `refs/backup/<key>/`; `ref_key` is injective; no
`eval` or unquoted expansion of untrusted names; fail-closed snapshot/ls-remote/push paths;
`mktemp -d` 0700 temp dir with an EXIT trap that fires on INT/TERM/HUP.

Recommendation: do not run the backup push for real until P0-1, P1-1 and P1-3 are fixed;
land P1-2 and P2-2 before the scope grows.

## Re-verification (second round, 2026-10-20, at 75b38e9)

Fixed: P0-1, P1-1, P2-1, P2-2, P2-3, P2-4, quote-in-branch, worktree collision, scan-depth
arithmetic, quoted paths. Partly fixed then: P1-2 (text files under media names, lock files,
`.env-local` / `env.production` still pushed) and **P1-3, two more routes**: a loosened
`.gitleaks.toml` in an unpushed local commit, and a commit-less `file:rule:line` line in an
untracked `.gitleaksignore` (it matches that line in every commit). New P3s: a ref-set race
between the two scans, and a `git log` error in the identity check reading as "no foreign
identities". Not fixed: nested repos shown as backed up; PySide6 unpinned.

Fixed after it (orchestrator): scanner rules and allowlist come only from the published
default branch plus git_autosync's own allowlist file (the GUI's Allowlist button writes it);
gitleaks scans the `.git` directory so it cannot pick up a checkout's `.gitleaksignore`;
scanner files inside `.git` stop the scan; the ref set is captured once for both scans; the
identity check fails closed; lock files, dependency trees, SVGs, text under media names and the
two `.env` variants are left out. Each has a test; 7 of the new defences were mutation-checked
(the identity-check fail-closed path is defence in depth behind the `rev-list` checks and has
no test that can reach it).

## Final check before release (third round, 2026-10-20, at 4e5a1b4)

Earlier routes all closed. New: **P0** — the staged scan, pointed at `.git` with
`GIT_WORK_TREE`, scanned nothing and reported clean, so a sweep published a token to origin
(a regression from the second-round fix). **P1-A** — the "published" config could be published
by autosync itself (push-only: a loosened `.gitleaks.toml` in a local commit was pushed, then
trusted on the next run; sweep: an untracked one was committed and pushed), and a rewritten
`refs/remotes/origin/*` or `origin/HEAD` was trusted in one run. **P1-B** — origin got
whatever HEAD was at push time, so a commit made after the scan was pushed unscanned. **P2** —
the own allowlist fell back to the checkout when `AUTOSYNC_STATE_DIR` was unset, and accepted
commit-less lines.

Fixed after it: staged changes are scanned as a deterministic commit from the `.git` source;
the sweep filters credentials and unscannable formats like the backup; the published commit is
what `git ls-remote origin` reports; autosync never pushes a range that changes either scanner
file; origin is pushed the scanned SHA explicitly, and a sweep commit whose tree is not the
scanned tree is not pushed; the allowlist always lives in App Support and only full
fingerprints count from it. Each has a regression test, and 7 of 7 planted faults were caught.
Live dry-run over all 19 entries: nothing blocked, 65 s.
