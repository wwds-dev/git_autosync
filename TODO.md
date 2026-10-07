# git_autosync — TODO

> **Legend** — priority `P0` critical · `P1` high · `P2` normal · `P3` low
> categories `security` `bug` `feature` `performance` `design` `docs` `testing` `infra` `research`
> owner `@me` (needs you — accounts, keys, money, judgement) · `@ai` (Claude can do this)

---

## v2 — current

- [ ] `P1` `bug` `@ai` **A mode flag can be silently dropped from the live config.** `push-only` was set on `imprint` and `imprint/vidforge` on 2026-09-14; by the config's next write (2026-09-15 03:33) both were bare again, autosync reverted to sweep mode, and on 2026-09-18 04:05 it committed a broken working tree — `agents/audiobook/panel.py` with four connects to methods that did not exist, so Imprint would not start at all. The suite reports 3 failures and 159 errors against that commit; autosync does not run tests, which is precisely why sweep mode is wrong for a repo being worked in. `app/config.py.write_repos()` has preserved flags since 2026-09-14, but a **GUI process started before that build stays on the old code in memory** — PID 2029 was still running a pre-rebuild binary five days later. Options: have the engine warn when a repo loses a flag it previously had, keep modes in a file the GUI never rewrites, or make the GUI reload its own code on config change. Restoring the flag by hand is not a fix.
- [x] `P1` `feature` `@ai` **push-only mode.** A sweep in a repo someone is working in rolls unrelated half-finished changes into one `autosync: <timestamp>` commit — `imprint` collected three on consecutive nights, one burying most of a docs rewrite. A config line may now carry `push-only`: push existing commits, never stage or commit, never publish an untracked branch. History scan still runs; staged scan skipped because nothing is staged. `imprint` and `imprint/vidforge` are set to it in the live config.
- [x] `P2` `testing` `@ai` **The engine had no tests.** Uncomfortable for the one tool whose job is writing to every other repo. `tests/test_push_only.py` drives the real shell script against throwaway repos and a throwaway remote. Two self-inflicted bugs it caught: the first version *skipped* when its fixture was misconfigured, so seven useless skips read as success; and the no-upstream branch in the push block was unreachable, because `ahead` is only counted when an upstream exists — the protection worked by accident and logged "nothing to push" while sitting on commits.
- [ ] `P0` `security` `@me` **Rotate any credential that was ever committed.** Repos with secrets in history stay out of `autosync_repos.txt`; publishing one requires history cleanup first, and the credential should be rotated regardless of what GitHub sees.
- [ ] `P1` `infra` `@me` **Coverage has fallen far behind the repo count.** The Sep 2026
  reorg left 21 repos under `active/`; `autosync_repos.txt` lists 5. Uncovered:
  `lab_hub`, `imprint`, `imprint/vidforge`, `sentinel`, `sentinel/bug_spray`,
  `sonar`, `sonar/sonar/macro`, `sonar/sonar/playmaker`, `toolbox`,
  `toolbox/unblock_tracker`, `bazaar`, and the five `sentinel/agents/*` repos.
  Consequences already visible: **`imprint` is 30 commits ahead of its remote**,
  `sentinel/vpn_agent` 1, and `bug_spray` has uncommitted work.
- [ ] `P1` `infra` `@me` **Nine repos have no remote at all**, so nothing is backing them
  up but the nightly Drive rsync: `bazaar`, `sentinel/bug_spray`, `toolbox`,
  `sonar/sonar/macro`, `sonar/sonar/playmaker`, and the five `sentinel/agents/*`.
  A repo without a remote cannot be autosynced — `--create-remote` is the way in.
- [ ] `P2` `research` `@ai` Nested repos need a coverage rule. `toolbox/` is a repo that
  *contains* three repos, and `sentinel/` contains three more. Decide whether the
  parent, the children, or both belong in `autosync_repos.txt`, and make sure the
  parent's `.gitignore` keeps `git add -A` from turning a child into an embedded gitlink.
- [ ] `P1` `security` `@me` Confirm `gitleaks` is installed (`brew install gitleaks`) — without it the leak gate is a stub, and the whole safety argument for autosync rests on it
- [x] `P1` `testing` `@ai` Assert the app fails closed when `gitleaks` is missing, rather than syncing unscanned
- [x] `P2` `bug` `@ai` Repos with no `origin` should be skipped with a clear status, not treated as a failure
- [x] `P2` `feature` `@ai` Surface the last-sync state file in the GUI — it exists on disk and nothing shows it
- [x] `P3` `docs` `@ai` HANDOVER.md is the original spec and the README is the source of truth; add a one-line note at the top of HANDOVER saying so
- [x] `P1` `feature` `@ai` launchd scheduling (fixed-interval and daily-at-a-time) via the *Schedule sync…* dialog
- [x] `P2` `infra` `@ai` User-writable config and log location plus the `AUTOSYNC_LOG_DIR` knob
- [x] `P2` `feature` `@ai` `--create-remote` for publishing a new project to GitHub
- [x] `P1` `feature` `@ai` **Find repos… reconciles the effective config with repositories on disk.** The preview identifies additions and missing paths; missing rows stay disabled and the main view warns when the list has drifted.
- [x] `P1` `bug` `@ai` **Nested repository status is keyed by config entry.** Repositories with the same leaf-name context no longer lose their per-entry last-sync state or display “never” incorrectly.
- [x] `P2` `design` `@ai` **Repository rows are readable at a glance.** Column headers align the controls, the repository name leads, the containing folders trail in muted text, and the None button no longer clips.
- [x] `P2` `design` `@ai` **Select-all is a tristate checkbox in the header, not separate All/None buttons.** Lined up in the same column as each row's own checkbox; checked/unchecked/partial reflects the rows, and clicking it checks or clears every one.
- [x] `P2` `feature` `@ai` **Remove button for a repo whose folder is gone.** A missing row goes grey and disabled except for **Remove**, which drops just that entry from `autosync_repos.txt` — no files and no GitHub repo are touched.
- [x] `P1` `bug` `@ai` **macOS 27 crash guard.** An ObjC exception raised inside AppKit event dispatch (seen via `-[NSEvent clickCount]` in libqcocoa on a tray click) unwound into C++ and aborted the process with nothing logged. `app/macos_guard.py` registers `NSApplicationCrashOnExceptions = NO` before `QApplication` is built and installs an uncaught-exception handler that appends to `crash.log`, so a crash that still slips through leaves evidence.
- [x] `P3` `docs` `@ai` **Docs viewer shows when the README last changed.** `paths.readme_updated()` reads the file's own commit date (mtime fallback for a frozen bundle), displayed as a "Last updated" badge in the in-app Documentation dialog.
- [x] `P2` `maintenance` `@ai` Follow SONAR's `sports` → `playmaker` repository rename in the tracked-project list.
- [x] `P2` `bug` `@ai` **The time column read git_autosync's own push log, not the repo.** A repo committed to by an editor, agent, or another tool still showed an old "last synced" time and could look stale (or trigger the amber pill) even though nothing was actually outstanding. The column is now **Last commit**, read from `git log` itself; git_autosync's own last-push time moved to the tooltip alongside the exact commit date.
- [x] `P3` `bug` `@ai` **"LAST SYNCED" clipped to "AST SYNCED"** in its 72px column; widened to 96px and added `tests/test_header_fit.py`, which measures every header label against its cell so a future wording/font change can't silently re-break it. Also fixed the **Find repos…** reconciliation panel painting dark text on a dark background (it now paints its own white panel explicitly instead of inheriting the window palette).

- [x] `P2` `infra` `@ai` **Versioned `v<MAJOR>.<BUILD>`, shown in the app.** The arc
  lives in `VERSION`; the build is `git rev-list --count HEAD`, so it cannot be forgotten.
  `app/version.py` reads live git from a checkout and a `_build_info.json` stamped by
  `scripts/stamp_version.py` from a frozen bundle, and says `v2.???` rather than guessing
  when it has neither. Shown in the window title, and read by Lab Hub's tile so you can see which
  build its Launch button would open. Lab-wide scheme, same two inputs as the Lab Project
  Monitor.

- [x] `P1` `bug` `@ai` **Hiding to the menu bar now survives activation.** Clicking the tray icon activates the app, and `_DockActivateFilter` treated that as a Dock click and re-opened the window behind the menu — the red button looked broken and Quit looked like it only fronted the app. The window now records `hidden_to_tray`, the filter ignores activation while it is set, and the tray menu no longer activates the app before opening. A `--background` (login) start also drops the Dock tile. Commits `5010f00`, `b50a1fb`.
- [x] `P2` `feature` `@ai` **Behind-remote is caught before the push, and failures say why.** The engine fetches `origin` before comparing with `@{u}`; a sweep dry-run reports `behind remote by N - pull first` as an ERROR; a failed push names the cause (pull first / auth / network / repo not found / branch protection). The row tooltip and the red summary banner carry that reason. Commits `51eca5f`, `370b12e`.
- [x] `P2` `feature` `@ai` **Pull and bulk actions.** A row that is behind gets an amber **Pull** button (`git pull --ff-only`; a diverged repo is left alone with the `pull --rebase` command). **Pull selected** and a bulk **Privacy…** act on ticked rows. Behind state is held by the window, so pulling one repo no longer clears every other Pull button. Commits `370b12e`, `2965144`.
- [x] `P2` `design` `@ai` **Repo table packs left and is banded.** Fixed 320px name column with the slack after the actions, window minimum 900px; alternating row colours come from the `QListWidget` (styling the row widget never reached the screen). The tray menu paints its own light surface so it is readable in Dark Mode. Commits `2b592db`, `f235813`, `849cd81`, `51eca5f`.
- [x] `P3` `testing` `@ai` **`tests/test_header_fit.py` covers the window, not just the header.** Offscreen Qt checks: header labels fit, tray menu text contrasts with its background, rows are banded as composited, Pull buttons survive a row rebuild, and a window hidden to the menu bar stays hidden on activation. Commits `849cd81`–`46a288c`.
- [x] `P3` `infra` `@ai` Follow Sentinel's `sentinel_fork` → `sentinel` checkout rename in the seed config, comments and tests (`c4690ab`). The live config in Application Support is separate and was not checked here.
- [x] `P2` `bug` `@ai` **⌘Q / Dock Quit parks the window like the red button does.** `_App.event()` hid the window and dropped the Dock tile itself instead of closing it, so it skipped `closeEvent` and left `hidden_to_tray` False — and the next activation, which is any click on the menu bar icon, put the window straight back. ⌘Q read as having merely fronted the app. It now calls `window.close()` and lets the one close path do all four things (hide, drop the tile, record the park, show the hint), guarded against re-entering since closing can itself put a `QEvent.Close` on the application object. A `--background` login start sets the same flag: the window was never shown, but it is parked in exactly the sense the filter means, and leaving it False let the first activation open a window the login start had deliberately not opened. **The old test could not have caught this**: nothing set `app._window`, which only `main()` does, so the quit fell through to Qt and the window closed by the ordinary route — and nothing installed `_DockActivateFilter`, so "activation did not re-open it" was a fact about a test with no filter in it. The fixture now does both, and `test_the_dock_filter_does_reopen_a_merely_hidden_window` is the control that proves the filter runs. `tests/conftest.py` makes the shared QApplication an `_App`, since only one can exist per process and the quit path being tested is its `event()`.
- [x] `P2` `bug` `@ai` **The Quit confirmation no longer claims scheduled syncs stop.** They do not: **Schedule…** installs a launchd agent that runs `git_autosync.sh` directly (`app/scheduler.py`), and the GUI has no sync timer of its own, so quitting the window has no power over it at all. Decided the wording was the thing to fix rather than the behaviour — a schedule set up separately should not be silently disabled by closing a window, and **Hide to menu bar** already promised the opposite. The Quit tooltip and dialog now say the window and menu bar icon go away and point at Schedule… for stopping the job itself; the README's parenthetical deferring to this entry is gone.
- [x] `P3` `feature` `@ai` **A push-only dry-run checks behind-remote too.** It was the one dry-run branch that skipped it, so a repo behind its remote was reported `would push N commit(s), push-only` and the real run's push was then rejected with `fetch first` — the surprise the check exists to stop, in the mode most likely to hit it, since push-only repos are the ones someone is working in on more than one machine. `tests/test_push_only.py::TestADryRunRefusesToCallABehindRepoSafe` covers push-only, keeps sweep as the branch that always worked, and keeps a level repo as the contrast so the new check cannot start swallowing the answer.

## v3 — later

- [ ] `P2` `feature` `@ai` Per-repo commit-message templates rather than one global custom message
- [ ] `P3` `feature` `@ai` Dry-run mode in the GUI that shows exactly what would be committed and pushed
- [ ] `P3` `performance` `@ai` Parallel scanning across repos — currently serial, and gitleaks dominates the runtime

## Resolved decisions

- Install destination: `/Applications`
- Code signing: ad-hoc only. No Developer ID — not worth $99/yr for a personal single-machine tool.
