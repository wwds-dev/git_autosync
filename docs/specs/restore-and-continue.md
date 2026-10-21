# Spec: lose the Mac, keep working

Status: **Define and Architect approved by the owner 2026-10-21**, all four decisions as
recommended (hourly; worktrees as branches; the listed app-data paths, no `~/.ssh` or gh
token; app data to Drive now in plaintext until item 4). **Released 2026-10-21.**
Follows `full-mac-backup.md` (released 2026-10-21, v2.118).

## Define

**Problem.** Since v2.118 every listed repo is in GitHub plus the private
`wwds-dev/mac_backup`, and Backup Control Center (BCC) copies `~/Documents/*` to Google
Drive daily. Three things still stand between a lost Mac and carrying on with the work:

1. **No way back in.** Restoring means, for each of 21 repos: clone, fetch
   `refs/backup/<entry>/*` by hand, recreate every branch, put the uncommitted work back.
   The repo list itself lives only in `~/Library/Application Support/git_autosync/` on the
   lost Mac, so even *which* repos to restore is gone.
2. **Up to a day of work at risk.** The backup runs once, at 03:30.
3. **App data is backed up nowhere.** `~/Library/Application Support/` holds each app's own
   state — Backstage's SQLite database, SONAR's experiment state, Provisio (20 MB), SYNDUSTRYX,
   Imprint, Sentinel, Autopilot, Lab Hub, BCC — plus git_autosync's repo list and allowlist.
   The Drive job only takes folders under `~/Documents`. About 43 MB in all (2026-10-21).

**User.** The owner, on a new or wiped Mac, wanting every project back exactly as it was —
branches, half-finished edits, app data — with as little to remember as possible.

**Required behaviour.**
1. **One restore command.** On a new Mac, after `gh auth login`:
   `git clone https://github.com/wwds-dev/git_autosync && git_autosync/restore.sh`
   brings back every listed repo at its original path:
   - every local branch, at its backed-up SHA;
   - the main worktree on the branch it was on, with its uncommitted and untracked files back
     in place (as uncommitted changes, not as a commit);
   - each linked worktree's and each stash's work as a branch (`restored/wt/<id>`,
     `restored/stash/<sha>`);
   - the repo list (with modes) and the allowlist, so git_autosync can run again unchanged.
   It never writes into a directory that already holds files, and prints a report of what
   it restored and what it could not.
2. **A backup every hour.** A `--backup-only` run (no staging, no commits, no origin push)
   pushes changed backup refs hourly; the 03:30 run stays as it is. Two runs never overlap.
3. **App data in the Drive copy.** The app folders above, `~/.gitconfig` and the lab's
   LaunchAgent plists are copied to Drive with the rest. SQLite databases are copied from a
   consistent snapshot, not mid-write.

**Success measure.**
- **Restore drill**, scripted, into an empty scratch home: every repo in the live list comes
  back; every branch SHA matches; each main worktree's files hash-match its `wip/main`
  snapshot; `git_autosync.sh --dry-run` then runs clean against the restored list.
- **Hourly:** an untracked file created at T is in `mac_backup` by T + 65 min; hourly runs make
  zero commits and zero origin pushes (checked over a day of runs); a run started while
  another is going exits at once and says so.
- **App data:** the Drive copy holds each app folder; every copied `*.db`/`*.sqlite` passes
  `PRAGMA integrity_check`.

**Non-goals.**
- Secrets and their encryption: `.env` files, `~/.ssh/id_rsa` and gh's token. That is item
  4, next. Until then, app data joins `.env` files in the plaintext Drive copy.
- Reinstalling apps, Homebrew, Python venvs: rebuildable, and BCC already lists the rebuild
  commands.
- Restoring repos that were never listed.

## Proposed Architect (same gate: it adds state and a scheduled job)

- **Manifest.** Each run that backs anything up also pushes `refs/backup-meta/manifest`
  (outside `refs/backup/`, because `ref_key` keeps `_`, so an entry named `_manifest` could
  otherwise collide with it): one commit holding `manifest.tsv` (entry, mode, resolved path with `$HOME` written as `~`,
  origin URL, checked-out branch per worktree, worktree id → path), the live repo list
  and git_autosync's allowlist (fingerprints only: commit:file:rule:line, no secret
  values). Deterministic like the snapshots, gitleaks-gated like everything else.
- **`restore.sh`** (bash 3.2, beside the engine): reads the manifest from `mac_backup`; per
  entry `git clone` origin (or `git init` with no origin), fetches its backup refs, creates
  branches, checks out the recorded branch, then lays the `wip/main` tree over the work tree
  (`git restore --source=<wip> --worktree -- .`) so the edits come back uncommitted. Refuses a
  non-empty target. `--dry-run` lists what it would do. `--only <entry>` for one repo.
- **`--backup-only`** engine flag: the same plan, gate (history + unreached) and backup push,
  skipping staging, the sweep, fetch and origin push. A run lock (`mkdir` lock in STATE_DIR,
  stale after 2 h) covers every run, nightly and hourly, CLI and GUI. Second LaunchAgent
  `com.wwds-dev.git-autosync-hourly` (`StartInterval` 3600), installed and removed from the
  GUI's schedule dialog. Cost: a full dry-run over 21 repos takes ~65 s; an hourly run is
  about the same in CPU and pushes only changed refs.
- **App data** (lab root repo, `_Admin/backup/`): `backup_to_gdrive.sh` gains a second list,
  `backup_paths.txt`, of `~/`-anchored paths, copied to `Backups/MacBook/Home/…` on Drive.
  Before rsync, each `*.db`/`*.sqlite*` under those paths is snapshotted with
  `sqlite3 .backup` into a staging folder that rsync copies instead. BCC shows the list (read
  only in v1). The paths are in Decision 3.

## Decisions for the owner

1. **Hourly backup interval.** Recommended: every hour. Alternative: every 30 minutes (twice
   the scans; laptops asleep skip runs either way).
2. **Linked worktrees on restore.** Recommended: their work comes back as branches
   (`restored/wt/<id>`), and you open a worktree when you need one. Alternative: recreate each
   worktree folder at its old path (most are stale Claude worktrees under `.claude/worktrees/`).
3. **App data paths.** Recommended: `~/Library/Application Support/` → Imprint, Backstage,
   SONAR, Lab Hub, Sentinel, Provisio, git_autosync, Backup Control Center, SYNDUSTRYX,
   syndustryx, Autopilot; plus `~/.gitconfig` and `~/Library/LaunchAgents/com.wwds-dev.*.plist`
   and `com.andreas.gdrive-backup.plist`. Not `~/.ssh` and not `~/.config/gh` (credentials:
   item 4).
4. **App data goes to Drive in plaintext now** (as `.env` files already do), and item 4 moves
   secrets to encrypted storage. Recommended: yes, now — losing a database is worse than its
   staying in plaintext a few more days. Alternative: wait until item 4 is done.

## Release (2026-10-21)

Review and security findings (no P0/P1) fixed: `docs/audit/restore-and-continue-*.md`. Suite:
189 passed. Installed as v2.125; the LaunchAgents run the master checkout's engine.

- **Hourly backup on:** `com.wwds-dev.git-autosync-hourly` loaded; kicked once through launchd:
  21 repos `BACKUP … ok`, manifest pushed (21 repos), exit 0, `last_backup.txt` written.
- **Live restore drill:** `restore.sh --only sentinel --into <scratch>` from GitHub and
  `mac_backup` in 4 s: every branch at its backed-up SHA (one branch created three minutes after
  the last hourly backup was, as expected, not yet there), on `main`, 7 `restored/*` branches
  (from worktrees, including ones removed since — stale refs are kept).
- **App data to Drive: not yet run for real.** This session's sandbox may not write into the
  Google Drive folder ("Operation not permitted"), and the daily job runs through the Backup
  Control Center app, which a launchd kick from here could not start (exit 78). The script was
  verified against a scratch folder (35 MB, all 10 databases pass `integrity_check`); the real
  copy happens on the next daily run or **Run backup now** in BCC.
- **Found in last night's log and fixed:** offline, the gate fell back to default rules and
  reported allowlisted fixtures as "secret in history" and four repos as "scanner config
  changed". An unreachable origin is now reported as a network error and the repo skipped.
