# git_autosync — Suggestions

Status: `IDEA` · `CONSIDERING` · `PLANNED` · `DONE` · `REJECTED`

---

| # | Suggestion | Category | Effort | Status |
|---|---|---|---|---|
| 3 | Per-repo commit-message templates | feature | M | CONSIDERING |
| 4 | GUI dry run — show the exact diff that would be pushed | feature | M | CONSIDERING |
| 5 | Parallel repo scanning; gitleaks dominates runtime | performance | M | IDEA |
| 6 | Notification on a blocked repo, rather than a line in the log | feature | S | IDEA |
| 7 | A "publish this project" wizard wrapping `--create-remote`, `.gitignore` check and first push | feature | L | IDEA |

## Done

| Suggestion | When |
|---|---|
| Fail closed (and say so loudly) when `gitleaks` is missing | Aug 2026 |
| Last-sync state surfaced in the GUI | Aug 2026 |
| Privacy button shows live repo visibility (🔒/🌐), fetched via `gh api` on a background timer | Aug 2026 |
| Fix leak… button turns red while a repo is currently blocked | Aug 2026 |
| `--background` launch flag — starts hidden, or tells an already-running instance to stay hidden, without the "already running" alert | Aug 2026 |
| launchd scheduling — fixed interval and daily-at-a-time | Aug 2026 |
| Tray icon and background behaviour | Aug 2026 |
| `--create-remote` publishing flow | Aug 2026 |
| User-writable config/log location, `AUTOSYNC_LOG_DIR` | Aug 2026 |
| Time column reads git commit history ("Last commit") instead of git_autosync's own push bookkeeping | Sep 2026 |
| Fixed clipped "LAST SYNCED" header label and dark-on-dark contrast in the Find repos… panel | Sep 2026 |
| Select-all is a tristate header checkbox, replacing the separate All/None buttons | Sep 2026 |
| Remove button for a repo row whose folder is gone (list-only, no files/GitHub touched) | Sep 2026 |
| macOS 27 crash guard — catches AppKit ObjC exceptions instead of a silent SIGABRT, logs to crash.log | Sep 2026 |
| Docs viewer shows a "Last updated" badge from the README's own commit date | Sep 2026 |
| Engine fetches before deciding; dry-run flags a repo that is behind its remote; a failed push names its cause | Oct 2026 |
| Pull button on rows that are behind (`--ff-only`), plus **Pull selected** and bulk **Privacy…** for ticked rows | Oct 2026 |
| Hiding to the menu bar survives tray clicks and app activation; a login start has no Dock icon | Oct 2026 |
| Repo table packs left with banded rows; tray menu readable in Dark Mode | Oct 2026 |
| ⌘Q parks the window the way the red button does; a push-only dry-run reports behind-remote; Quit stops claiming it stops scheduled syncs | Oct 2026 |

## Rejected

| Suggestion | Why |
|---|---|
| Developer ID notarisation | $99/yr for a personal single-machine tool |
