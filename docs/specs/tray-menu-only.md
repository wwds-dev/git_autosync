# Spec: the menu bar dot only opens its menu

Status: **approved by the owner 2026-10-14; built and verified on hardware 2026-10-14**

## Define

**Problem.** Clicking git_autosync's menu bar dot can bring the app forward,
and with Stage Manager on it can bring the whole window and its stage forward.
The user only wanted the menu. Commits d9fbdb4 and 69c85f2 tried to hide the
window off screen around the activation and put it back afterwards. That is a
workaround on top of a workaround. The activation exists only because the
Qt-drawn popup needs it, and the popup exists only because the native menu
aborted the app.

**User.** The owner, who has the window open beside other work and wants Dry-run,
Sync now or Quit from the menu bar without anything moving.

**Required behaviour** (the same as Backup Control Center):
1. Clicking the dot opens the dropdown and nothing else. No app activation, no
   window brought forward, no stage switch, no Dock tile appearing.
2. **Open git_autosync** in that menu is the only thing that shows the window.
3. The red button, Cmd+Q and Dock → Quit hide the window to the menu bar and drop
   the Dock tile. The menu's **Quit** is the only real exit (unchanged).
4. Launching the app still shows and activates the window (the Stage Manager fix
   in `app/main.py`).

**Success measure.** On hardware, with the window open behind another app's
window and Stage Manager on: 20 clicks on the dot each open the menu, none moves
the window or switches the stage, none aborts, and each item fires. The same
20 clicks with the window hidden to the menu bar never show the window. No new
`.ips` under `~/Library/Logs/DiagnosticReports/` and no new `crash.log` entry.

**Non-goals.** Changing what the menu contains. Changing the launch or login-item
behaviour. Fixing Backup Control Center, which is unguarded and has crashed too
(see below). That needs its own change, and it is flagged rather than done here.

## Root cause of the abort (why the native menu can come back)

The abort was never caused by anything git_autosync did differently.
`~/Library/Logs/DiagnosticReports/` holds **three** reports with the identical
stack, all from 2026-10-07:

| Report | App |
|---|---|
| `git_autosync-2026-10-07-153030.ips` | git_autosync (setContextMenu build) |
| `Backup Control Center-2026-10-07-151831.ips` | BCC |
| `Backup Control Center-2026-10-07-154310.ips` | BCC |

```
-[NSEvent clickCount]                      <- raises (not a mouse event)
__CFNOTIFICATIONCENTER_IS_CALLING_OUT_TO_AN_OBSERVER__
-[NSMenuTrackingSession sendBeginTrackingNotifications]
-[NSStatusItem popUpStatusItemMenu:]
```

So BCC is not immune. It hits the same macOS 27 trap, just less often, because
the trap depends on which event is current when the menu starts tracking. This
is the crash `lab_hub/ui/appkit_guard.py` already fixes by swizzling
`-[NSEvent clickCount]` to return 0 for non-mouse events. AGENTS.md lists
git_autosync and backup_manager as "still unguarded". git_autosync's
`macos_guard.py` is a different guard. It sets `NSApplicationCrashOnExceptions`
and logs, and commit 7265231 showed it cannot catch an exception raised inside a
CoreFoundation notification callout. That explains why `crash.log` holds only
startup lines.

The three suspects from the brief were the `activated` signal, the menu
stylesheet and the activation calls. The `.ips` stack rules out all three as the
cause: the exception is raised by AppKit's own notification before any Qt slot
runs. All three are still removed or left unused, because the target behaviour
does not need them.

## Design

There is no visual change. The menu becomes the native macOS menu (as in BCC and
Lab Hub) instead of the Qt-drawn popup, so the app's `QMenu` stylesheet no
longer applies to it. It will look like every other menu bar menu.
Items, order and the "Start at login" checkmark are unchanged.

## Architect

| Change | Where |
|---|---|
| Add `app/appkit_guard.py`: a **byte-identical code copy** of `lab_hub/ui/appkit_guard.py` (6th copy; only the docstring differs) | new |
| `appkit_guard.install()` in `main()` before the `QApplication` is built, beside `install_exception_guard()`; result written to the startup line in `crash.log` | `app/main.py` |
| `self._tray.setContextMenu(menu)`; no `activated` connection | `app/ui_main.py` `_setup_tray` |
| Delete `_on_tray_activated`, `_popup_tray_menu`, `_menu_closed_at` / `aboutToHide` timing, `_tray_opened_window` | `app/ui_main.py` |
| Delete `window_order_out`, `window_order_back`, `deactivate_app`, `_ns_window`, `_send_void`, `_ns_app`, `make_window_nonactivating` | `app/macos_dock.py` |
| Delete `tests/test_tray_panel.py` (it only records the non-activating-panel experiment, which no longer has a caller) | tests |
| `_tray_open`: add `activate_app()` after `show()`, the same Stage Manager reason as at launch. BCC does not do this, but this app's launch fix shows a window shown by an inactive app is filed away. | `app/ui_main.py` |
| Add a drift test: the code part of `app/appkit_guard.py` must match `lab_hub/ui/appkit_guard.py` when that file exists (skipped in a lone clone) | `tests/test_appkit_guard.py` |
| Add a behaviour test: the guarded `clickCount` returns 0 for a non-mouse `NSEvent`. Unguarded, that call aborts, so it runs in a subprocess, like Lab Hub's test | `tests/test_appkit_guard.py` |

**`_DockActivateFilter` and `hidden_to_tray`: kept.** With a native menu the dot
no longer activates the app, so the case the filter's docstring describes
(activation from the tray click reopening a parked window) goes away. The filter
is still what reopens the window when the **Dock tile** is clicked while the
window is closed but the app is still Regular. `hidden_to_tray` still stops any
other activation (Cmd-Tab, `open -a`) from undoing a deliberate hide to the
menu bar. Its docstring will be corrected to say this.

**`_App.event` (Cmd+Q / Dock Quit → `window.close()`): unchanged.**

No state, concurrency or module-boundary change beyond the new module, so no ADR.

## Verify

Stubbed Qt tests have missed this behaviour three times, so the decisive check is
on hardware, following the success measure above. Two ways to run it:
- **(a)** The owner clicks through the checklist on each build.
- **(b)** The owner grants computer-use screen access for **git_autosync** and one
  other app (e.g. Finder, used to put a window in front). I then run the 20-click
  check myself and attach screenshots.

Automated: the full suite, plus the guard's subprocess test and the drift test.
Mutation check: remove the `install()` call and confirm the startup check fails.
Remove a mouse type from the set and confirm the behaviour test fails.

## Release

Run `build_app.sh`, install, and smoke test: launch shows the window, the dot
opens the menu without fronting, Open, Dry-run, Cmd+Q hides, the red button
hides, and Quit exits. Commits are made as `wwds-dev
<243015673+wwds-dev@users.noreply.github.com>`.

## Verification result (2026-10-14, build v2.097 (e2ebcbb) installed in /Applications)

The test used computer-use on the real display, with Stage Manager on and Finder
as the active app:
- With the window open behind Finder, the dot was clicked 21 times to open the
  menu (plus as many clicks to close it). The menu opened every time, the menu
  bar kept saying **Finder**, and the window's title bar stayed inactive.
- **Dry-run** from the menu ran (output appeared in the window), and Finder
  stayed the active app.
- The red button hid the window. 4 more opens of the menu never showed it.
- **Open git_autosync** showed the window and made it active.
- Cmd+Q hid it. 2 more opens of the menu never showed it.
- **Quit** ended the process.
- No new `.ips` in DiagnosticReports. `crash.log` startup line:
  `exception guard active, clickCount guard active`.
- Suite: 100 passed. Mutations caught: `activated` connected, no activation in
  Open, a mouse type dropped from the guard, a guard that always calls the
  original, and the guard not installed in `main()`.
