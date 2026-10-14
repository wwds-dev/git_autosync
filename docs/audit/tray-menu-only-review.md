# Review: tray-menu-only (e2ebcbb, f6bb573)

Reviewer: code-reviewer (read-only), 2026-10-14
Scope: `git diff master..tray-menu-only` against `docs/specs/tray-menu-only.md`
Playbook: `docs/playbooks/code-reviewer.md` is `status: baseline` (seeded 2026-10-10, never
verified by a radar run), so it added nothing beyond AGENTS.md.

## Verdict

**No P0 or P1 findings.** No P2 findings either. The removal is complete, the guard is
byte-identical to Lab Hub's and installed before `QApplication`, the native menu is
wired with nothing on `activated`, and the suite is green. Every finding below is P3:
test strength, doc precision, and one pre-existing UX dead end the spec now makes
explicit.

## Evidence gathered

| Check | Command / method | Result |
|---|---|---|
| Full suite | `.venv/bin/python -m pytest -q` | `100 passed in 63.08s` |
| Leftover symbols | `grep -rnE "window_order_out\|window_order_back\|deactivate_app\|make_window_nonactivating\|_popup_tray_menu\|_on_tray_activated\|_menu_closed_at\|_tray_opened_window\|_panel_style_logged\|test_tray_panel\|QCursor" app tests README.md TODO.md docs` | No hits in code or tests. Only hits are the spec's "Delete …" table, plus history in TODO.md's done items. `import time` and `QCursor` were removed from `app/ui_main.py`, and nothing else used them. |
| Drift | Compared the text after the closing docstring `"""` of `app/appkit_guard.py` and `../lab_hub/ui/appkit_guard.py` | Both start with `"""`, so the slice is right. Code equal: `True` (3061 chars). |
| Guard control really hits the trap | Ran `tests.test_appkit_guard._probe(False)` | rc `-6`, stdout `mouse 2`, stderr `Terminating app due to uncaught exception 'NSInternalInconsistencyException', reason: 'Invalid message sent to event "NSEvent: type=AppDefined …"'`, frame 3 `-[NSEvent clickCount]` |
| Guarded probe | `_probe(True)` | rc `0`, stdout `mouse 2` / `other 0`. A real click count passes through. A non-mouse event answers 0. |
| `receivers()` check is not vacuous | Scratch script: offscreen `QSystemTrayIcon`, `receivers("2activated(QSystemTrayIcon::ActivationReason)")` before and after `connect` | `0` -> `1` (lambda) -> `2` (bound method). The tray test does catch a reconnected click handler. |
| Install-order test vs. a commented-out call | Applied the test's own logic to `main.py` with the call commented out | Still passes. See F1. |
| `activate_app()` in tests steals focus? | Called `set_dock_icon_visible(True)` + `activate_app()` under offscreen and read `lsappinfo front` | The front app stayed the same and was not the test pid. No focus theft from the suite. |

## Findings

### F1 · P3 · The "installed before QApplication" test passes with the call commented out
`tests/test_appkit_guard.py:97-102`

The test checks for the substring `appkit_guard.install()` in the source of `main()`.
A line like `# click_guard_ok = appkit_guard.install()` (followed by
`click_guard_ok = False`) still contains it and still sits before `_App(sys.argv)`.
**Scenario:** someone comments the call out while bisecting a startup problem. The suite
stays green and the next build aborts on the first tray click on macOS 27.
**Evidence:** reproduced with the test's own logic on a mutated copy of the string:
`in body: True before _App: True`. The spec's Verify section says the mutation "guard not
installed in `main()`" is caught. That holds only for deleting the line.
**Suggestion:** parse `main()` with `ast` and look for a real `Call` node, or monkeypatch
`appkit_guard.install` and `_App` and assert the call order.

### F2 · P3 · The control test accepts any non-zero exit, not the specific trap
`tests/test_appkit_guard.py:76-81`

`returncode != 0 and "other" not in stdout` would also pass if the probe died for an
unrelated reason after `mouse 2`, for example an `otherEventWithType:` signature mismatch
that raises a different exception or segfaults. The probe does reach the real trap today
(stderr above), so this is about robustness against future edits, not a current false pass.
**Suggestion:** also assert `r.returncode == -6` and that
`"NSInternalInconsistencyException"` and `"clickCount"` appear in `r.stderr`. Both are
present today.

### F3 · P3 · The `_tray_open` ordering is only partly pinned by tests
`app/ui_main.py:1618-1629`, `tests/test_header_fit.py:253-272`

The code order is right: flag cleared, then `set_dock_icon_visible(True)`, `show`,
`raise_`, `activate_app`, `activateWindow`. The app becomes Regular before it tries to
take focus, and it is active before `activateWindow` asks AppKit to make the window key.
The test only records `["dock=True", "activate"]`. It would not catch `activate_app()`
moving after `activateWindow()`, or `show()` moving after `activate_app()`. Under Stage
Manager, either change brings back the "window filed into a background stage" bug, which
only shows up on hardware.
**Suggestion:** also stub `show` and `activateWindow` into the same `calls` list.

### F4 · P3 · The tray test mutates the shared session app and the module-scoped window
`tests/test_header_fit.py:225-250`

`window._setup_tray()` runs a second time on the module-scoped `window` fixture. It leaves
three things behind:
- `QApplication.setQuitOnLastWindowClosed(False)` set on the session-wide `_App`. Before,
  no test did this under offscreen, because `_setup_tray` returned early.
- A new `QSystemTrayIcon` and `QMenu` parented to the window.
- `window._tray_menu` replaced.

The `finally` restores only `_tray`.
**Scenario:** a later test that relies on default quit-on-last-window semantics behaves
differently depending on test order. Nothing fails today (100 passed).
**Suggestion:** restore `setQuitOnLastWindowClosed`, and `deleteLater()` the extra tray and
menu in the `finally`.

### F5 · P3 · A window parked in the menu bar can only come back through the status item
`app/main.py:62-88` (behaviour unchanged by this branch; the spec now makes it explicit as
requirement 2)

While `hidden_to_tray` is set, `_DockActivateFilter` ignores every activation. A
double-click on git_autosync.app in Finder, Spotlight or Launchpad does not start a second
process: LaunchServices activates the running one, so `_on_new_connection` never runs and
nothing appears. The app is Accessory then, so it has no Dock tile and is not in Cmd-Tab.
**Scenario:** the status item is not visible. Causes include a notched display with a full
menu bar, the item hidden in System Settings › Menu Bar, or a third-party bar manager. The
window is then unreachable and the app can only be ended from Activity Monitor. Syncing
continues, so no data is at risk.
Spec requirement 4 ("Launching the app still shows … the window") reads as covering
re-launching a running app. Today it does not.
**Not verified:** whether Qt 6.11 delivers the reopen Apple event
(`applicationShouldHandleReopen:`) as something distinguishable from a plain
`ApplicationActivate`. If it does, a Finder/Spotlight launch could show the window without
also undoing the park on Cmd-Tab. This is an owner decision, not a defect in this diff.

### F6 · P3 · Docs overstate the verification against the success measure
`docs/specs/tray-menu-only.md:23-27` and `:126-141`, `TODO.md:84`

The success measure asks for 20 clicks with the window hidden that never show it, and says
"each item fires". The recorded run did 21 clicks with the window open, then **4 + 2 = 6**
clicks with it hidden. Of the items, it exercised Dry-run, Open and Quit. Sync now and
Start at login are not reported as tested on hardware. TODO.md summarises this as
"25+ clicks with the window open and hidden, items fire".
**Scenario:** the docs claim the success measure is met in full, but the hidden-window
half is about a third of the stated count.
**Suggestion:** either reword it as what was done, or run the remaining clicks (and the
two untested items) during the Verify/Release smoke test.

### F7 · P3 · A test and the stylesheet comment still describe the tray menu as Qt-styled
`tests/test_header_fit.py:37-40`, `app/style.py:29-33`

The tray menu is now native, so the `QMenu` stylesheet no longer reaches it. The spec's
Design section says so. The rule still matters for the standard context menus of
`QLineEdit` and `QPlainTextEdit` in the window. But the test is still named
`test_tray_menu_text_contrasts_with_its_background`, and it builds a menu with the tray's
labels. README line 13 correctly dropped "the tray menu stays readable".
**Scenario:** a future reader deletes the `QMenu` rule as dead tray styling and makes the
text widgets' right-click menus unreadable in Dark Mode.
**Suggestion:** rename the test and reword the comment to say "context menus".

### F8 · P3 · No build-time check that the guard installs in the frozen bundle
`build_app.sh` (no `--selftest`), `app/main.py:96-109`

Lab Hub, SONAR, Imprint and Provisio fail their build when the guard does not install, via
`--selftest`. git_autosync only records `clickCount guard active|UNAVAILABLE` in
`crash.log` at runtime. `_runtime()` falls back to `/usr/lib/libobjc.A.dylib` when
`find_library` fails in a frozen bundle, and the hardware run recorded `active` for
v2.097, so it works today.
**Scenario:** a PyInstaller or Python change makes `install()` return False. The bundle
ships, and the first tray click aborts.
**Suggestion:** at release time, check that the startup line says `active`, or add a
`--selftest` like Lab Hub's. This belongs to the Release phase, not to this diff.

## Checked, no issue

- **Removal.** No caller is left for any deleted symbol. `test_tray_panel.py` is gone, and
  so are README's reference to it and TODO's `_panel_style_logged` item. `macos_dock.py`
  keeps only `activate_app` and `set_dock_icon_visible`, and both are still imported and
  used in `main.py` and `ui_main.py`.
- **`_DockActivateFilter` / `hidden_to_tray`.**
  - Every deliberate hide sets the flag: the red button, Hide to menu bar, Cmd+Q and Dock
    Quit (all through `closeEvent`), and a `--background` start.
  - Two paths clear it: `_tray_open`, and a second-process "raise" (`main.py:158-165`).
  - The flag cannot be left set with the window shown, so the filter cannot re-open a
    parked window.
  - The reopen branch can now only be reached when the window is hidden by something other
    than `closeEvent`, which is effectively never. It is harmless, and its control test
    still proves the filter runs.
  - When `activate_app()` in `_tray_open` fires `ApplicationActivate`, the window is
    already visible, so the filter does not double-show it.
- **Guard install order.** `appkit_guard.install()` runs at `main.py:100`, before
  `_App(sys.argv)` at line 110, next to `install_exception_guard()`. `install()` is
  idempotent (`_KEEP` check) and swallows `OSError`, `AttributeError`, `TypeError` and
  `ValueError`, so it can never stop startup.
- **QMenu lifetime.** `QSystemTrayIcon.setContextMenu` does not take ownership. The menu
  is parented to `MainWindow` and also held on `self._tray_menu`. The window lives until
  the process exits, and `_tray_quit` hides the tray before `app.quit()`. I found no path
  that destroys the window while the status item still references the menu.
- **Ownership claim in the comment** (`ui_main.py:1541-1544`). Correct: without a parent
  or a Python reference, the menu would be collected.
- **Spec "Architect" table vs. the diff.** Every row is implemented as written.
- **Lab AGENTS.md.** It already records git_autosync as the sixth guard copy
  (`~/Documents/lab/AGENTS.md:881-883`). Note that the lab root's `AGENTS.md` is
  uncommitted in that repo, which is outside this repo's scope.

## Not verified

- Behaviour on hardware. This review is static plus the offscreen suite. I relied on the
  spec's recorded hardware run.
- The GIL and interpreter-finalization behaviour of the Python IMP during quit. It is
  shared with the five other copies, and the hardware Quit test found nothing.
- F5's reopen-event question.

The review/done activity was not logged with `log_activity.py`: that would write
`docs/company/activity.jsonl`, and this review was restricted to writing only this file.
