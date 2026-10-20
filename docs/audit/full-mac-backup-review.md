# Review: feat/full-mac-backup (code-reviewer)

Date: 2026-10-20 · Reviewed: `git diff master...155da53` against the spec and ADR 0001 ·
Suite at review: 123 passed, 1 skipped · Saved by the orchestrator (reviewer is read-only).
Process notes from the reviewer: `docs/playbooks/code-reviewer.md` is `status: baseline`
(stale); `docs/company/app-context.md` is still the unfilled template.

## P0
None.

## P1
1. **A branch tip recorded before the scan is pushed after it** (`git_autosync.sh` plan →
   history scan → push). An agent dropping a secret commit (`reset`, `amend`, `rebase`)
   between plan and scan makes the scan miss it; it is still pushed to the backup.
   Reproduced with a gitleaks wrapper. Fix: scan every planned SHA with `--not --all`.

## P2
1. Two worktrees with the same folder name map to one `wip/wt/<name>` ref; the atomic push
   is rejected and the repo is never backed up. Fix: key by git's worktree id.
2. The big-file filter misses non-ASCII names (no `-z`) and grown tracked files
   (`--diff-filter=A` only); a >100 MB file then fails the whole repo's push.
3. `ref_key` encodes only `%`, `/` and space: a `~/…` entry (documented as valid) can never
   be backed up. Encode every byte outside `[A-Za-z0-9_-]`, before the first real run.
4. Commits on a detached HEAD in a clean worktree are not backed up, yet the row says
   **Backed up**.
5. A `wip` ref left from an earlier dirty state survives after the work is committed; the
   README restore step then writes older files over committed work.
6. `rm -f .git/index.lock` (pre-existing) removes another process's live lock in push-only
   repos.
7. **Backed up** is shown even when big files were left out.
8. Listed repos with no `origin`, or with a conflict in progress, get no backup though the
   README says they can be restored.

## P3
1. One unreadable worktree fails the backup of the whole repo.
2. A `pre-push` hook can block the backup push (no `--no-verify`).
3. The snapshot scan has no legacy-gitleaks fallback.
4. Staged-then-edited files keep only the working-tree version.
5. Unborn branches and missing worktree folders count as nothing to save.
6. A sweep moved to push-only on a side branch still pushes that branch to origin; a
   detached sweep reports `'main' is local-only` (`branch:-main`).
7. The sweep's own commit is not in the backup until the next run; a comment says otherwise.
8. Repos with a `.git` *file* (submodules, `--separate-git-dir`) can be flagged UNLISTED but
   `process_repo` requires a `.git` directory.
9. Worktree names ending in `.lock` or containing `..` produce invalid refs.
10. Missing tests for the cases above, `_finish_subset` merging, the summary gap text and
    advice ordering.
11. `app/problem_advice.py` names `wwds-dev/mac_backup` even when the remote is overridden.

Checked and fine: `[ a ] || [ b ] && return` precedence, nested `emit_wt` scoping, no values
lost to subshells, `set -u`, paths with spaces, overlapping scan roots, dry-run never pushes,
`unstage` in push-only, deterministic snapshot SHAs with `commit.gpgsign`, older-engine
summary parsing.
