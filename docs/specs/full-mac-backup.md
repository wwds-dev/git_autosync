# Spec: back up everything on this Mac, not just the checked-out branch

Status: **Define and Architect approved by the owner 2026-10-20** (all five decisions as
recommended; see the end). Built on `feat/full-mac-backup`; review and security findings fixed 2026-10-20
(see "After review" at the end).

Owner decisions already made:
- 2026-10-20: side branches go to the hidden backup area (`refs/backup/...`), not to
  visible branches on GitHub.
- 2026-10-20: all backup refs go to one private repo, `wwds-dev/mac_backup`; scan roots are
  `active/`, the lab root and `~/Documents/Websites/`; stale backup refs are kept; sweep
  commits only on the default branch; shopfront gets a private GitHub repo. Not answered:
  whether shopfront's website repo gets one — left without a remote and flagged.

## Define

**Problem.** git_autosync reports a repo *Clean* when its checked-out branch matches
GitHub. That says nothing about the rest of the Mac. A census on 2026-10-20 (read-only:
`git status --porcelain`, `git rev-list <branch> --not --remotes`, `git worktree list`
per repo) found:

| Gap | Where |
|---|---|
| Uncommitted edits (Mac only) | foundry 17 (15 untracked), sentinel 10, provisio 5, backstage 3, autopilot 1, syndustryx 1, imprint-deps worktree 1 |
| Local-only commits on side branches | sentinel 20, backstage 9, sonar 7, provisio 7, imprint 5, lab_hub 1 |
| Extra worktrees (each its own working tree, none scanned today) | imprint 12, sonar 12, sentinel 8, provisio 7, lab_hub 6, autopilot 3, backstage 3 |
| No remote, not in `autosync_repos.txt` | `shopfront` (2 local commits); its `website/` is a symlink to `~/Documents/Websites/shopfront`, a separate repo with no remote |
| Checked out on a side branch | lab_hub `fix/close-ghost-window`, sentinel `fix/no-thread-terminate`, autopilot `feat/acknowledge` |

`push-only` (rightly) never commits the working tree and never publishes a branch, so
since the actively-worked repos moved to it, none of the above leaves the Mac. AGENTS.md
already says it: "uncommitted now genuinely means unprotected".

**User.** The owner, who needs to be able to lose this Mac tonight and lose nothing that
was typed into a repo, without autosync touching anyone's branches or working trees.

**Required behaviour.**
1. Every local branch of every configured repo is backed up to a hidden ref, force-updated
   to its current tip. Real branches on GitHub are never created, moved or deleted by this.
2. Every worktree's uncommitted state — tracked edits, staged edits **and untracked files
   not ignored by `.gitignore`** — is snapshotted into a commit and pushed to a hidden ref.
   The working tree, index, `HEAD`, branches and stash list are byte-for-byte unchanged.
3. Stash entries are backed up the same way (none exist today; cheap to cover).
4. The gitleaks gate scans everything about to be pushed, snapshot commits included,
   before any push. A finding blocks that repo, as today.
5. Each row answers "is everything on this Mac backed up?": **Backed up**, or the gap
   in words ("3 branches and 2 worktrees not backed up", "blocked: secret in uncommitted
   file").
6. A repo found on disk but missing from the list, or with no remote, is flagged in the
   GUI and the summary — never silently skipped.
7. Restoring is one documented command per repo (README).
8. shopfront gets a private GitHub repo only after the owner confirms (see Decisions).

**Success measure.**
- After one real run, the backup audit (the same check that feeds requirement 5) reports
  every repo in scope **Backed up**, and lists no unlisted or remote-less repo.
- Restore test, scripted: fresh clone + `git fetch <backup> '+refs/backup/<repo>/*:refs/backup/*'`
  reproduces every local branch tip SHA, and every worktree snapshot's tree hash equals the
  tree of that worktree written through a temporary index.
- Before/after checksum of each repo's working tree, `.git/index`, `HEAD`, `refs/heads/`
  and `refs/stash` is identical (no side effects).
- `git ls-remote origin 'refs/heads/*'` is unchanged by the backup step.
- A planted fake secret in an untracked file blocks that repo and is named in the row.

**Non-goals.** Backing up ignored files (`.env`, `.venv`, `build/`); that is Backup
Control Center's Drive copy. Keeping a history of snapshots (v1 keeps the latest per
worktree). Changing sweep/push-only semantics for the real branches. Automatic deletion
of backup refs. Backing up non-git folders.

## Proposed Architect (for the same gate: it touches state and pushes)

Corrections to the brief's design, each found while checking it against the repos:

1. **`git stash create` misses untracked files** — it has no `-u`. 15 of foundry's 17
   files are untracked, so it would back up 2. Use a temporary index instead:
   `GIT_INDEX_FILE=$tmp` (copied from `.git/index`) → `git add -A` → `git write-tree` →
   `git commit-tree -p HEAD`. The real index is never written. Files over 50 MB are left out
   and reported (GitHub rejects >100 MB).
2. **`refs/backup/<branch>` and `refs/backup/wip/<branch>` collide.** A branch named `wip`
   (or `wip/x`) makes git refuse one of them (directory/file ref conflict). Use separate
   namespaces: `refs/backup/<repo>/heads/<branch>`, `.../wip/<worktree>`, `.../stash/<n>`.
3. **Key snapshots by worktree, not branch.** A worktree can be on a detached HEAD
   (`imprint-base` is), so a branch key does not exist for it.
4. **Hidden refs on a public repo are still public.** Anyone can `git ls-remote` and fetch
   `refs/backup/*`. sentinel, sonar, imprint and lab_hub are public and hold most of the
   gaps; uncommitted work in them would be published. Proposal: push all backup refs to one
   **private** repo, `wwds-dev/mac_backup`, namespaced by config entry. One rule, one
   restore place, no visibility check that goes stale if a repo is made public later.
   All repos' `.git` together are ~75 MB, so the first push is small.
5. **Push with `--force` per ref and `--atomic`**, refspec `+refs/heads/*:refs/backup/<repo>/heads/*`:
   rebased branches must overwrite. Only refs under `refs/backup/<repo>/` are ever written.
6. **Gate order per repo:** build snapshots locally → `gitleaks git` over history plus the
   snapshot commits (`--log-opts="<snapshots> --not --all"`) → push only if both are clean.
   Checked 2026-10-20 against gitleaks 8.30.1 in a throwaway repo: the default history
   scan already covers side branches (a token committed only on a side branch was found
   from `main`), and the `--not --all` scan finds a token in an unreferenced snapshot.
9. **Snapshot commits are deterministic** (fixed message, dates copied from the parent
   commit), so an unchanged worktree yields the same SHA and `git ls-remote` alone tells
   whether it is backed up. No local refs or state files are added to anyone's repo.
7. **Discovery** walks the scan roots for `.git` dirs and files (worktrees, submodules) and
   compares against the effective `autosync_repos.txt`; anything unmatched is flagged.
8. **Skip unchanged snapshots** by comparing the new tree hash with the backup ref's, so a
   quiet night pushes nothing.

ADR to write at Architect: `docs/adr/0001-backup-refs-in-private-repo.md` (where backup refs
live is expensive to reverse once the owner restores from it).

## Design (brief, for ux-designer)

Row status vocabulary: **Backed up** · **Not backed up: <what>** · **Blocked: <why>** ·
**Not in list** · **No remote**. A row's detail lists branches and worktrees not yet backed
up. The summary line counts repos not fully backed up. No new windows.

## Decisions for the owner

1. **Where backup refs live.** Recommended: one private `wwds-dev/mac_backup` repo (Architect
   point 4). Alternative: each repo's own origin, accepting that public repos expose WIP.
2. **Scan scope.** Recommended: `~/Documents/lab/active/`, the lab root repo
   (`github.com/wwds-dev/lab`, not in the list today though AGENTS.md says autosync can push
   it) and `~/Documents/Websites/` (holds shopfront's website and three other sites).
3. **shopfront.** Create private `wwds-dev/shopfront`? And the website repo — private
   `wwds-dev/shopfront_website`, or leave it to the Websites folder's own backup?
4. **Stale backup refs** (branch deleted locally). Recommended: keep them, report the count,
   no auto-delete in v1.
5. **Sweep mode on a side branch** (lab_hub is sweep and on `fix/close-ghost-window`): sweep
   commits onto that branch and `push -u` publishes it if it has no upstream, which the
   2026-10-20 decision says side branches should not do. Recommended: sweep only commits on
   the default branch; on any other branch the repo gets the backup refs only.

## After review (2026-10-20)

`docs/audit/full-mac-backup-review.md` and `docs/audit/full-mac-backup-security.md` found one
P0 and four P1s, all fixed with a regression test each, and every new defence mutation-checked
(14 of 14 planted faults caught):

- **P0** a gitleaks run whose own git step failed read as clean → any error line of its own
  fails the scan, and `git rev-list --all` must succeed first.
- **P1** branch tips moved between plan and scan → every planned SHA no ref reaches is scanned.
- **P1** an untracked `.gitleaks.toml` disabled the gate → rules come from the committed file.
- **P1** content gitleaks cannot read → credential files, unscannable formats and other new
  binaries are left out and reported; the row says **Partial**.
- P2s fixed: merges scanned (remerge), `stash -a` ignored files dropped, backup repo must be
  private (gh) and not rewritten by `insteadOf`, author/committer emails allow-listed, worktrees
  keyed by git's id, `-z` and grown files in the size filter, full ref-name encoding, `wip` refs
  point at HEAD when clean, no-origin and mid-conflict repos still backed up, sweep on a side
  branch leaves origin alone, `index.lock` only removed when stale, no `xargs`, `--no-verify`,
  numeric scan depth.
- Deferred to TODO: identity check on the origin push, nested-repo coverage, legacy-gitleaks
  fallback for the snapshot scan.
