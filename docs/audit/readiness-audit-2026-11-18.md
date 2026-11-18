# Readiness audit: promises vs code (2026-11-18)

Read-only audit by Claude (Opus 5.5) at commit `9c77a29` (`master`, clean). Foundry phase: **Verify / Secure
(read-only audit)**; no source, test, live config or LaunchAgent was changed, and no sync, sweep, snapshot or
push was run. Promises were collected from `README.md`, `docs/foundry/app-context.md`, the lab's
`docs/conventions/git-remotes-and-backup.md`, the GUI's wording and guarantee-stating comments in
`git_autosync.sh`; each was checked against the code. `git fetch` could not reach GitHub from this session
(sandbox), so remote state is as last fetched.

Suite: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` → **1 failed, 224 passed** in 378 s on a
loaded machine (eight audits running). The failure, `test_problems_dialog.py::test_batch_reports_each_result_and_keeps_the_dialog_open`
("batch did not finish"), passes alone in 0.7 s: its worker thread missed the test's 5 s `_wait` under load.
Timing-sensitive, not a product defect; not mutation-checked here.

## Verdict

**Not yet safe to rely on unattended for repos agents work in.** The leak gate itself is solid: every path
to origin and to `mac_backup` goes through a fail-closed gitleaks scan of exactly what is pushed, by SHA.
What is not safe is *what* it pushes and *who* checks it: the mode a repo runs in defaults to the
destructive one and is lost silently, `lab_hub` is being swept today with live Foundry work in it (B1), the
origin push has no identity check, and the scanner's own rules can be loosened by any hand push. For the
backup half (hourly `--backup-only` to the private repo) the answer is yes: it runs, the identity and
private-repo checks gate it, and the latest runs report every listed repo **ok**.

## Findings

### Blocker

**B1. Repos agents work in still run in sweep mode; `lab_hub` was swept three times in five days and has
12 uncommitted files of in-progress work now.**
- Promise: README:121 "Use **push-only** for any repo a person or an agent is actively working in"; the
  lab's hard rule that sessions commit their own work by name.
- Code: a line with no flag, or any flag other than the exact token `push-only`, is a sweep
  (`git_autosync.sh:1109-1110`); a sweep on the default branch runs `git add -A` and commits and pushes
  everything (`git_autosync.sh:819`, `1031-1033`, `1061`).
- Live config: `lab_hub`, `git_autosync`, `syndustryx`, `backup_manager`, `toolbox*`, the two `sonar`
  sub-repos, `bug_spray` and `shopfront_website` have no flag. `lab_hub` (Foundry app, on `main`) got
  `autosync:` commits on 2026-11-13, -16 and -17 (the last: 5 files, +425 lines, now on `origin/main`), and
  has 9 modified and 3 untracked files right now (`docs/foundry/approvals.jsonl`, a new spec and ADR). The
  next 12-hourly full run will commit and push them to `main`. `syndustryx` has six `autosync:` commits
  since 2026-10-18 (is on `main`, 7 worktrees). This is the 2026-09-18 failure (TODO P1) still live.
- Smallest fix: the owner adds `push-only` to every repo an agent works in (at least `lab_hub`,
  `syndustryx`, `git_autosync`, `backup_manager`). Code: make `push-only` the default and require an explicit
  `sweep` flag; treat an unknown flag as an ERROR row, not a sweep.

### High

**H1. The origin push has no identity check, and a failed identity check on the backup does not stop it.**
- Promise: README:223-225 "so a commit carrying a real address never reaches GitHub"; lab hard rule "no
  real name or email in any commit".
- Code: `bk_foreign_identities` runs only inside `backup_push` (`git_autosync.sh:689-693`); on a foreign
  identity it reports `BACKUP failed` and returns, and `process_repo` carries on to the origin push
  (`git_autosync.sh:1056-1061`) with no check. Author/committer **names** are never checked anywhere
  (`git_autosync.sh:631-633` compares emails only). With `AUTOSYNC_BACKUP_REMOTE=""` there is no check at all.
- Mitigation today: the global git identity is `wwds-dev` + the noreply address (checked, not printed); a
  sweep pins its identity (`git_autosync.sh:1031-1032`). The risk is per-repo config, a session's
  `GIT_AUTHOR_*` env, or a cherry-picked foreign commit.
- Fix: run the same identity check on `@{u}..HEAD_SCANNED` before the origin push and make a failure an
  ERROR row for origin too; add a name allowlist. Existing TODO item (P2), raised here to P1.

**H2. The scanner trusts whatever `.gitleaks.toml` / `.gitleaksignore` origin's default branch has; an
agent's hand push can loosen or empty it.**
- Promise: README:204-215 and the lab rule "changes to `.gitleaks.toml` / `.gitleaksignore` are pushed by
  hand after review".
- Code: config and allowlist come from `GL_PUB` (`git_autosync.sh:225-228`), the SHA `ls-remote` reports.
  Autosync itself refuses to publish a change to them (`git_autosync.sh:934-940`, delivered), but nothing
  checks *who* pushed them: agents push with the owner's credentials and the lab rule now tells them to
  push without asking. A published `.gitleaks.toml` without `[extend] useDefault = true` has no rules at all;
  published `.gitleaksignore` lines need not be commit-pinned (line 228, unlike the own allowlist at 230).
- Fix: pin a hash of each repo's published scanner files in App Support; when origin's copy differs from the
  pin, ERROR (`scanner config changed on origin - re-approve in the app`) until the owner re-pins it in the
  GUI. Existing TODO item (P2 `@me`), raised to P1.

**H3. Further ways a `push-only` flag is lost or never takes effect (beyond the stale-GUI case in TODO).**
- Promise: `app/config.py:15-18` "a round-trip through the GUI must not silently drop a flag".
- Code: **Find repos… → relocate** writes the new entry, but `write_repos` looks flags up by entry name, so
  the moved repo loses `push-only` and becomes a sweep (`app/rescan_dialog.py:126-139`,
  `app/config.py:79-81`). A typo (`push_only`, `--push-only`, `Push-only`) is a sweep in the engine
  (`git_autosync.sh:1110`) and is *erased* by the next GUI save (`app/config.py:34`, comment at 22-24 says a
  typo "degrades to the default", which is the destructive mode). Every GUI save also drops the user's own
  comments (`app/config.py:70-83`). New repos from **Find repos…** and **Create GitHub repo…** are added as
  sweeps (`app/rescan_dialog.py:134-136`, `app/create_repo_dialog.py:183-185`).
- Fix: carry flags across a relocate; reject unknown flags (engine ERROR, GUI warning); add new entries as
  `push-only`.

### Medium

**M1. Credential blobs still reach `.git/objects` (both leads confirmed).**
- Promise: README:173 "Credential files never reach `.git/objects` through a snapshot"; README:456-457.
- Code: a sweep's `git add -A` has no pathspec (`git_autosync.sh:819`); `bk_filter` drops credential files
  from the index afterwards (`823`), after the blob is written. The snapshot's `CRED_PATHSPEC`
  (`469-473`) is case-sensitive and narrower than `bk_sensitive` (`377-388`): `.ENV`, `Secrets.PEM`,
  `.ssh/`, `.aws/`, `.gnupg/`, `credentials*`, `env.*`, `*.crt`, `*.cer`, `*.jks`, `*.tfstate`, `*.ovpn`,
  `*.ppk`, `id_dsa*` are added, then dropped. Exposure: `.git` is copied to Google Drive daily. Ignored files
  are never added, so the risk is an unignored credential file. Both already in TODO (P2); no new item.

**M2. Files gitleaks cannot read are pushed unscanned when they are already committed.**
- Promise: README:51-52 "No clean scan, no push. This is what stops an API key, password, or token from
  ever reaching GitHub."
- Code: the "left out because the scanner cannot read it" filter (`bk_filter`, `git_autosync.sh:418-456`)
  is applied only to snapshots (`486`), stashes (`507`) and a sweep's own staging (`823`). Commits made by
  hand (every push-only repo) and every local branch tip go to origin (`1061`) and to `mac_backup`
  (`526`) as they are; a committed `*.sqlite`, `*.pdf`, `*.xlsx`, lock file, `*.svg` or other binary is
  skipped by gitleaks and published. Fix: reword the promise ("secrets gitleaks can read"), or run the same
  name/binary check over `@{u}..HEAD_SCANNED` and the planned branch tips and ERROR on a hit.

**M3. A failure in a scheduled run surfaces only as a tray colour.**
- Promise: README:758-760 "After every real sync the app fires a macOS notification".
- Code: the notification fires only when a run started from the app finishes (`app/ui_main.py:1562-1564`).
  The 12-hourly and hourly LaunchAgent runs write `last_status.txt` (`git_autosync.sh:1270-1287`) and the
  tray polls it once a minute (`app/ui_main.py:1899-1901`), and only if the app is running (it starts at
  login via `com.wwds-dev.git-autosync-login`). A blocked repo then needs the owner to notice a red dot and
  read the log; the Problems dialog appears only after GUI runs. `UNLISTED` also turns the dot red
  (`git_autosync.sh:1280`): `active/overseer` (parked, no origin) is unlisted, so the next full run will keep
  the dot red and a real block will look the same. The notification text omits the error count
  (`app/ui_main.py:1572`).
- Fix: have the engine itself post `osascript display notification` on BLOCKED/ERROR in a scheduled run;
  show UNLISTED amber, not red. Or reword the README promise.

**M4. The full sync runs the copy of the engine inside `/Applications`, the hourly run the repo's.**
- Code/config: `com.wwds-dev.git-autosync.plist` runs `/Applications/git_autosync.app/Contents/Frameworks/
  git_autosync.sh` every 43200 s; the hourly plist runs the repo's script. Today they are identical (`cmp`,
  build 161 = `0641e48`), but an engine fix reaches the full sync only after a rebuild and reinstall. The
  bundle has no `scripts/hygiene.py`, so full runs never write `hygiene.json` (`git_autosync.sh:1256`); it is
  the hourly backup-only run that does, against README:406 ("every real, full run"). README:321 calls the
  full run "the 03:30 sync"; it is a 12-hour interval.
- Fix: point the full-sync plist at the repo script (as the hourly one is), or make the build bundle
  `scripts/` and say in the README that engine changes need a rebuild. Correct the two README lines.

**M5. Coverage gaps (effective list vs disk).** The engine reads
`~/Library/Application Support/git_autosync/autosync_repos.txt` (plist `AUTOSYNC_CONFIG`; verified). It lists
23 entries; every git repo under `active/` (4 levels) and `websites/` is listed except:
- `active/overseer`: unlisted, no origin (parked; owner decision).
- `websites/altmerch.store`, `websites/bookadatewithme`, `websites/webwealth`: not git repos at all, so
  only the Drive copy holds them. The lab convention doc says `bookadatewithme` is a private GitHub repo; the
  local folder is not a clone of it.
- `~/Documents/lab/agents/bug_spray`: a stale clean clone at the lab root whose origin is a local path,
  outside every scan root.
- `shopfront_website` is listed twice (`~/Documents/lab/websites/shopfront_website` and `shopfront/website`,
  a symlink to it): two sweeps per run and two key sets in `mac_backup`; how `restore.sh` handles the pair is
  unverified.
- The repo's seed `autosync_repos.txt` is stale (5 entries, one of them `sentinel/vpn_agent`, which no
  longer exists).

### Low

- **L1. A sweep-mode dry-run is not read-only.** It runs `git add -A` (blobs into `.git/objects`) and then
  `git reset`, which also unstages anything staged by hand (`git_autosync.sh:819`, `733`, `994`), against
  README:459 "makes zero changes".
- **L2. The push goes to the branch's configured remote, not necessarily `origin`** (`git_autosync.sh:1060-
  1061`) while trust and the `insteadOf` check concern origin/backup only. No listed repo has a non-origin
  upstream today (checked).
- **L3. Hygiene's trunk.** `scripts/hygiene.py:85-92` uses `origin/HEAD`, then `main`/`master`: correct where
  integration happens on origin's default branch, wrong for `syndustryx` (TODO P2 item; lead confirmed as
  narrower, not "always main").
- **L4. Mid-operation states.** Conflict detection (`git_autosync.sh:782-783`) misses a cherry-pick or revert
  whose conflicts were resolved but not committed (`CHERRY_PICK_HEAD`/`REVERT_HEAD`): a sweep would finish it
  under an `autosync:` message. A sweep that is behind still commits before the push is rejected
  (`893-901`, `1031`), so local `autosync:` commits pile up until someone pulls (reported as ERROR each run).
  Diverged and mid-rebase/merge repos are otherwise handled as promised (backup only, ERROR row).
- **L5. Config parsing.** `echo "$line" | xargs` (`git_autosync.sh:1102`) fails on a quote and the entry is
  silently skipped; `#` inside a path truncates it.
- **L6. `--create-remote`** skips the scanner-file check (`git_autosync.sh:934`), so it publishes the repo's
  own `.gitleaks.toml`, and `gh repo create --push` pushes `HEAD`, not the scanned SHA (`1045`). Owner-invoked
  and confirmed in the dialog, so low.

## Delivered (checked in code)

Staged changes scanned as a commit from the index, not with gitleaks' broken `--staged` (`266-277`); history
scan over every ref with remerge diffs (`244`, `296-313`); fail-closed on gitleaks' own errors (`150-160`);
scanner config from `ls-remote`, never the checkout, with a refusal when `.git/.gitleaks*` exists
(`197-240`); autosync never publishes a scanner-file change to the default branch (`287-294`, `934-940`);
origin gets the scanned SHA (`1036-1040`, `1061`); sweep and snapshot identity pinned (`362-371`,
`1031-1032`); sweep only on the default branch (`792-801`); no sweep with a conflict or rebase/merge in
progress (`781-786`); fresh `index.lock` respected (`806-814`); single run lock with stale takeover
(`76-108`); backup only to a repo `gh` confirms private, `insteadOf` refused (`594-622`); backup identity
check (`625-635`, emails only); every unreached planned SHA scanned before the backup push (`572-588`);
manifest strips URL credentials and is scanned (`1143-1144`, `1179-1183`); mode flags preserved on a plain
GUI save (`app/config.py:61-83`).

## Unverifiable without a live run

Contents of `mac_backup` matching the plan; `gh` private check against the real repo; whether git ever echoes
URL credentials into the log on a failed push (no listed origin URL carries credentials today, checked);
`restore.sh` on a real new Mac and with the duplicated `shopfront_website` entry; notification delivery; the
GitHub visibility of `lab_hub` (where B1's sweeps landed).

## Checks only the owner can do

1. Add `push-only` to `lab_hub`, `syndustryx`, `git_autosync`, `backup_manager` (and any other repo agents
   work in) in the live list, before the next full run; decide whether the three `lab_hub` `autosync:`
   commits on `origin/main` need rewording.
2. Decide the default mode (H3/B1 code fix) and the scanner-config pin (H2).
3. Decide `active/overseer` (list it, or accept a red dot) and whether the three website folders should be
   git repos; confirm what `wwds-dev/bookadatewithme` holds.
4. Confirm the app is running at login (tray) — it is the only place a scheduled failure shows.
5. Remove the duplicate `shopfront/website` line (or keep it knowingly).
