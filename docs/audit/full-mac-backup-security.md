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
