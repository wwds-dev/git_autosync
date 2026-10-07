"""Main window for the git_autosync GUI."""
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, QFileSystemWatcher, QSize, QTimer
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from . import config, leak_triage, login_item, paths, repo_state, scheduler, version
from .create_repo_dialog import CreateRepoDialog
from .documentation_dialog import DocumentationDialog
from .ignore_dialog import IgnoreDialog
from .macos_dock import make_window_nonactivating, set_dock_icon_visible
from . import repo_row
from .repo_row import RepoRow
from .rescan_dialog import RescanDialog, plan_changes
from .runner import AutosyncRunner
from .schedule_dialog import ScheduleDialog

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

TRAY_COLORS = {
    "ok":        QColor("#2e7d32"),
    "attention": QColor("#c62828"),
    None:        QColor("#9e9e9e"),
}

TOOLTIPS = {
    "dry_run_btn":    "Scan all repos and show what would happen — never commits or pushes.",
    "sync_btn":       "Commit and push every clean repo. Leak-gate must pass first.",
    "edit_btn":       "Open autosync_repos.txt in your default editor.",
    "create_repo_btn":"Create a new GitHub repo for a local project (scanned for secrets first).",
    "open_logs_btn":  "Reveal the log folder in Finder.",
    "tooltips_btn":   "Toggle explanatory tooltips on all controls.",
    "docs_btn":       "Open the project README inside the app.",
    "schedule_btn":   "Configure a background launchd schedule so syncs run automatically.",
    "msg_field":      "Optional: set a custom commit message instead of the default timestamp one.",
}

_GIT_STATUS_CODES = {
    "M ": "modified",
    " M": "modified (unstaged)",
    "MM": "modified (staged + unstaged)",
    "A ": "new file",
    "AM": "new file (modified)",
    "D ": "deleted",
    " D": "deleted (unstaged)",
    "R ": "renamed",
    "C ": "copied",
    "??": "untracked",
    "!!": "ignored",
    "UU": "conflict",
}


def _confirm_sync_dialog(parent, heading: str, preview: str) -> bool:
    """Scrollable sync-confirm dialog — buttons always on screen."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Confirm sync")
    dlg.setMinimumWidth(520)

    layout = QVBoxLayout(dlg)
    layout.setSpacing(12)

    lbl = QLabel(heading)
    lbl.setWordWrap(True)
    layout.addWidget(lbl)

    if preview:
        layout.addWidget(QLabel("Pending changes:"))
        pane = QPlainTextEdit(preview)
        pane.setReadOnly(True)
        pane.setMaximumHeight(300)
        pane.setStyleSheet(
            "background:#1E1E1E; color:#D4D4D4; font-family:monospace;"
            " font-size:12px; border-radius:6px; padding:8px;"
        )
        layout.addWidget(pane)
    else:
        layout.addWidget(QLabel("No uncommitted changes found."))

    btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    btns.button(QDialogButtonBox.Ok).setText("Sync now")
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)
    layout.addWidget(btns)

    return dlg.exec() == QDialog.Accepted


def _format_git_status(raw: str) -> str:
    """Convert git status --short output into human-readable lines."""
    out = []
    for line in raw.splitlines():
        if len(line) >= 3 and line[2] == " ":
            code = line[:2]
            path = line[3:]
            label = _GIT_STATUS_CODES.get(code, code.strip())
            out.append(f"  {label}: {path}")
        else:
            out.append(f"  {line}")
    return "\n".join(out)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"git_autosync {version.version_string()}")
        # The columns are fixed, so below this the actions clip rather than
        # reflow — the table needs ~870px plus margins.
        self.setMinimumSize(900, 480)
        # Sized to the table rather than the screen: the columns are fixed, so a
        # wider window only adds empty space to the right of the actions.
        self.resize(1000, 660)

        self.config_path = paths.user_config_path()
        self.runner = AutosyncRunner(self)
        self.runner.output_received.connect(self._append_output)
        self.runner.finished.connect(self._on_finished)

        self._current_dry_run = True
        self._current_single_repo = None
        self._row_widgets: dict[str, RepoRow] = {}
        self._last_findings: dict[str, dict] = {}
        # Repos known to be behind their remote. Held here because
        # _reload_repo_list rebuilds every row, and rebuilding used to
        # clear every other Pull button after one pull.
        self._behind: set[str] = set()
        self._tray_hint_shown = False
        # True while the window is parked in the menu bar. Activation
        # must not undo that — clicking the tray icon activates the app.
        self.hidden_to_tray = False
        self._panel_style_logged = False

        self._build_ui()
        self._reload_repo_list()
        self._check_gitleaks()
        self._apply_tooltips(False)
        self._refresh_last_sync_label()
        self._setup_tray()
        self._setup_file_watcher()
        self._migrate_legacy_agents()

    def _migrate_legacy_agents(self):
        """Carry a schedule/login item across the Netrunner3000 -> wwds-dev rename.

        A LaunchAgent under the old label keeps running after the rename, so
        leaving it would mean two agents doing the same job — re-install under
        the new label, then remove the old one.
        """
        try:
            was_login = login_item.is_enabled()
            removed, schedule = scheduler.migrate_legacy()
            schedule = schedule or scheduler.get_schedule()
            moved = removed + login_item.migrate_legacy()
            if not moved:
                return
            if schedule and not scheduler.is_installed():
                if schedule["mode"] == "interval":
                    scheduler.install_interval(
                        schedule["interval_seconds"], self.config_path,
                        schedule.get("run_at_load", False))
                else:
                    scheduler.install_calendar(
                        schedule["hour"], schedule["minute"], self.config_path,
                        schedule.get("run_at_load", False))
            if was_login and not login_item.is_enabled():
                login_item.enable()
            self._append_output(
                f"Migrated launchd agent(s) to the wwds-dev label: {', '.join(moved)}\n")
        except Exception as exc:
            self._append_output(f"Could not migrate legacy launchd agents: {exc}\n")

    # ── UI construction ────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        # gitleaks warning banner
        self.gitleaks_banner = QLabel()
        self.gitleaks_banner.setWordWrap(True)
        self.gitleaks_banner.setStyleSheet(
            "background:#FFF3CD; color:#664D03; padding:10px 12px;"
            " border:1px solid #FFE69C; border-radius:8px;"
        )
        self.gitleaks_banner.hide()
        root.addWidget(self.gitleaks_banner)

        # Repo-list drift banner — the list is a file, not a live view of disk,
        # so say plainly when the two have diverged instead of waiting for the
        # user to guess that 'Find repos' exists.
        self.drift_banner = QLabel()
        self.drift_banner.setWordWrap(True)
        self.drift_banner.setStyleSheet(
            "background:#EEF4FF; color:#1E3A8A; padding:9px 12px;"
            " border:1px solid #BFD3FF; border-radius:8px;"
        )
        self.drift_banner.hide()
        root.addWidget(self.drift_banner)

        # ── Repos section ──────────────────────────────────────────
        repos_header = QHBoxLayout()
        repos_lbl = QLabel("Repositories")
        repos_lbl.setObjectName("sectionLabel")
        repos_header.addWidget(repos_lbl)
        repos_header.addStretch(1)
        self.rescan_btn = QPushButton("Find repos…")
        self.rescan_btn.setProperty("class", "rowButton")
        self.rescan_btn.setToolTip(
            "Look at every git repo in your projects folder and compare it with "
            "this list: add repos that are missing from it, fix the path of any "
            "that moved, and remove ones that are gone.")
        self.rescan_btn.clicked.connect(self._on_rescan)
        repos_header.addWidget(self.rescan_btn)
        self.edit_btn = QPushButton("Edit list")
        self.edit_btn.clicked.connect(self._on_edit_repo_list)
        repos_header.addWidget(self.edit_btn)
        root.addLayout(repos_header)

        self._list_header = self._build_list_header()
        root.addWidget(self._list_header)

        self.repo_list = QListWidget()
        self.repo_list.setSelectionMode(QAbstractItemView.NoSelection)
        # Banding comes from the list, not the row widgets: an item widget sits
        # on the viewport and the item's background is drawn over it.
        self.repo_list.setAlternatingRowColors(True)
        root.addWidget(self.repo_list, stretch=1)

        # ── Commit message ─────────────────────────────────────────
        self.msg_field = QLineEdit()
        self.msg_field.setPlaceholderText(
            "Custom commit message (optional) — leave blank for the default timestamp"
        )
        root.addWidget(self.msg_field)

        # ── Primary actions ────────────────────────────────────────
        primary_row = QHBoxLayout()
        primary_row.setSpacing(10)
        self.dry_run_btn = QPushButton("Dry-run (safe)")
        self.dry_run_btn.setObjectName("secondaryButton")
        self.dry_run_btn.clicked.connect(self._on_dry_run)
        self.sync_btn = QPushButton("Sync now")
        self.sync_btn.setObjectName("primaryButton")
        self.sync_btn.clicked.connect(self._on_sync)
        primary_row.addWidget(self.dry_run_btn)
        primary_row.addWidget(self.sync_btn, stretch=1)
        root.addLayout(primary_row)

        # ── Secondary actions ──────────────────────────────────────
        secondary_row = QHBoxLayout()
        secondary_row.setSpacing(8)
        self.create_repo_btn = QPushButton("Create GitHub Repo…")
        self.create_repo_btn.clicked.connect(self._on_create_repo)
        self.schedule_btn = QPushButton("Schedule…")
        self.schedule_btn.clicked.connect(self._on_open_schedule_dialog)
        self.open_logs_btn = QPushButton("Logs")
        self.open_logs_btn.clicked.connect(self._on_open_logs)
        self.docs_btn = QPushButton("Docs")
        self.docs_btn.clicked.connect(self._on_open_documentation)
        self.tooltips_btn = QPushButton("Tooltips")
        self.tooltips_btn.setCheckable(True)
        self.tooltips_btn.toggled.connect(self._on_toggle_tooltips)
        for b in (self.create_repo_btn, self.schedule_btn,
                  self.open_logs_btn, self.docs_btn, self.tooltips_btn):
            secondary_row.addWidget(b)
        secondary_row.addStretch(1)

        # Cmd+Q and the Dock's Quit deliberately only hide to the menu bar, so
        # without this the only real exit is a menu the user has to know about.
        self.pull_selected_btn = QPushButton("Pull selected")
        self.pull_selected_btn.setToolTip(
            "Fast-forward every ticked repo that is behind its remote. "
            "Skips repos that are level, and refuses any that have diverged.")
        self.pull_selected_btn.clicked.connect(self._on_pull_selected)
        secondary_row.addWidget(self.pull_selected_btn)

        self.privacy_selected_btn = QPushButton("Privacy…")
        self.privacy_selected_btn.setToolTip(
            "Change public/private on every ticked repo, after confirming the "
            "list and the direction.")
        self.privacy_selected_btn.clicked.connect(self._on_privacy_selected)
        secondary_row.addWidget(self.privacy_selected_btn)

        self.hide_btn = QPushButton("Hide to menu bar")
        self.hide_btn.setToolTip(
            "Close this window. Scheduled syncs keep running; the menu bar "
            "icon brings it back.")
        self.hide_btn.clicked.connect(self._on_hide_to_tray)
        secondary_row.addWidget(self.hide_btn)

        self.quit_btn = QPushButton("Quit")
        self.quit_btn.setObjectName("quitButton")
        self.quit_btn.setToolTip(
            "Quit git_autosync completely — no window and no menu bar icon. "
            "A schedule set up with ‘Schedule…’ keeps running without the "
            "app; turn it off there to stop it.")
        self.quit_btn.clicked.connect(self._on_quit_clicked)
        secondary_row.addWidget(self.quit_btn)
        root.addLayout(secondary_row)

        # ── Summary banner ─────────────────────────────────────────
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.hide()
        root.addWidget(self.summary_label)

        # ── Output ────────────────────────────────────────────────
        output_lbl = QLabel("Output")
        output_lbl.setObjectName("sectionLabel")
        root.addWidget(output_lbl)

        self.output_pane = QPlainTextEdit()
        self.output_pane.setReadOnly(True)
        self.output_pane.setMaximumBlockCount(5000)
        self.output_pane.setMinimumHeight(120)
        root.addWidget(self.output_pane, stretch=1)

        # ── Status bar ─────────────────────────────────────────────
        self.last_sync_label = QLabel()
        self.statusBar().addPermanentWidget(self.last_sync_label)
        self.next_sync_label = QLabel()
        self.statusBar().addPermanentWidget(self.next_sync_label)

    # ── File watcher (auto-reload repo list) ──────────────────────

    def _setup_file_watcher(self):
        self._watcher = QFileSystemWatcher([str(self.config_path)], self)
        self._watcher.fileChanged.connect(self._on_config_file_changed)

    def _on_config_file_changed(self, _path: str):
        # Re-add path in case editor replaced the file (some editors do)
        self._watcher.addPath(str(self.config_path))
        self._reload_repo_list()

    # ── Sync timestamps ────────────────────────────────────────────

    def _refresh_last_sync_label(self):
        stamp = paths.read_last_sync()
        success = paths.read_last_success()
        status = paths.read_last_status()

        if not stamp:
            text = "  Last sync: never"
        elif status == "attention" and success and success != stamp:
            # The last run left something blocked or errored, so say when work
            # last actually got out — otherwise a red icon has no context.
            text = f"  Last run: {stamp} (had problems) · Last good sync: {success}"
        elif status == "attention":
            text = f"  Last run: {stamp} (had problems) · No fully clean sync yet"
        else:
            text = f"  Last successful sync: {stamp}"
        self.last_sync_label.setText(text)
        self._refresh_next_sync_label()

    def _refresh_next_sync_label(self):
        schedule = scheduler.get_schedule()
        if not schedule:
            self.next_sync_label.setText("   Next sync: not scheduled  ")
            return
        now = datetime.now()
        if schedule["mode"] == "interval":
            stamp = paths.read_last_sync()
            try:
                base = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S") if stamp else now
            except ValueError:
                base = now
            next_run = base + timedelta(seconds=schedule["interval_seconds"])
            if next_run < now:
                next_run = now
        else:
            next_run = now.replace(
                hour=schedule["hour"], minute=schedule["minute"], second=0, microsecond=0
            )
            if next_run <= now:
                next_run += timedelta(days=1)
        self.next_sync_label.setText(
            f"   Next sync: ~{next_run.strftime('%Y-%m-%d %H:%M')}  "
        )

    # ── Repo list ─────────────────────────────────────────────────

    def _reload_repo_list(self):
        self.repo_list.clear()
        self._row_widgets = {}
        no_remote = {p.name for p in paths.repos_without_remote()}
        for name in config.read_repos(self.config_path):
            publish_cb = self._on_publish_single if name in no_remote else None
            privacy_cb = None if name in no_remote else self._on_privacy_single
            row = RepoRow(name, self._on_dry_run_single, self._on_sync_single,
                          on_publish=publish_cb, on_privacy=privacy_cb,
                          on_ignore=self._on_open_ignore,
                          on_allowlist=self._on_allowlist_single,
                          on_remove=self._on_remove_entry,
                          on_pull=self._on_pull)
            self._apply_time(row, name)
            item = QListWidgetItem()
            # Width 0 lets the item span the viewport instead of stopping at the
            # row's natural width — otherwise the right-hand columns float short
            # of the edge and cannot line up with the header.
            item.setSizeHint(QSize(0, row.sizeHint().height()))
            item.setData(Qt.UserRole, name)
            self.repo_list.addItem(item)
            self.repo_list.setItemWidget(item, row)
            self._row_widgets[name] = row
            row.set_stripe(len(self._row_widgets) % 2 == 0)  # row is already counted
            row.set_behind(name in self._behind)
            if not paths.repo_exists(name):
                row.set_missing(True)
            row.checkbox.toggled.connect(self._refresh_select_all_box)
        self._apply_tooltips(self.tooltips_btn.isChecked())
        self._refresh_drift_banner()
        self._refresh_select_all_box()
        QTimer.singleShot(0, self._align_header_to_rows)
        # Fetch GitHub visibility for each repo in the background (non-blocking)
        QTimer.singleShot(0, self._fetch_all_visibility)

    def _fetch_all_visibility(self):
        gh = paths.find_gh()
        if not gh:
            return
        for name, row in self._row_widgets.items():
            if row.privacy_btn is None:
                continue
            slug = paths.repo_slug(name)
            if not slug:
                continue
            try:
                r = subprocess.run(
                    [gh, "api", f"repos/{slug}", "--jq", ".private"],
                    capture_output=True, text=True, timeout=8,
                )
                val = r.stdout.strip()
                if val == "true":
                    row.set_visibility(True)
                elif val == "false":
                    row.set_visibility(False)
            except Exception:
                pass

    def _build_list_header(self) -> QWidget:
        """Column headings for the repo list, using RepoRow's own geometry so
        the two stay aligned if a column width ever changes."""
        header = QWidget()
        header.setObjectName("listHeader")
        lay = QHBoxLayout(header)
        lay.setContentsMargins(*repo_row.ROW_MARGINS)
        lay.setSpacing(repo_row.ROW_SPACING)

        def cell(text, width=None, align=Qt.AlignLeft | Qt.AlignVCenter):
            lbl = QLabel(text)
            lbl.setAlignment(align)
            if width:
                lbl.setFixedWidth(width)
            return lbl

        # Select-all sits in the checkbox column, lined up with the rows'
        # own boxes, instead of as separate All/None buttons off to the side.
        self.select_all_box = QCheckBox()
        self.select_all_box.setFixedWidth(repo_row.CHECK_W)
        self.select_all_box.setToolTip("Check or uncheck every repo")
        self.select_all_box.clicked.connect(self._on_select_all_clicked)
        lay.addWidget(self.select_all_box)

        lay.addWidget(cell("Repository", repo_row.NAME_W))
        lay.addWidget(cell("Last commit", repo_row.TIME_W,
                           Qt.AlignRight | Qt.AlignVCenter))
        lay.addWidget(cell("Status", repo_row.BADGE_W, Qt.AlignCenter))
        # Width is set from a real row once one exists — the button cluster's
        # size depends on which buttons that row has.
        self._header_actions = cell("Actions", 1, Qt.AlignLeft | Qt.AlignVCenter)
        lay.addWidget(self._header_actions)
        lay.addStretch(1)

        header.setStyleSheet(
            "#listHeader { background:#F5F5F7; border:1px solid #E5E5EA;"
            " border-bottom:none; border-top-left-radius:8px;"
            " border-top-right-radius:8px; }"
            "#listHeader QLabel { color:#6E6E73; font-size:11px;"
            " font-weight:600; text-transform:uppercase;"
            " letter-spacing:0.4px; background:transparent; }"
        )
        return header

    def _apply_time(self, row, name: str):
        """Show when the repo last changed, and explain both clocks on hover.

        Last commit is the useful number: commits arrive from editors, agents
        and other tools, and the Status badge already says whether anything is
        outstanding. When git_autosync itself last pushed is bookkeeping, so it
        moves to the tooltip.
        """
        when = paths.last_commit_time(name)
        row.set_time(repo_state.humanize(when), stale=False)
        if when is None:
            row.set_time(None, stale=True)
            row.time_label.setText("no commits")
        synced = repo_state.time_since_synced(name)
        row.time_label.setToolTip(
            (f"Last commit: {when:%Y-%m-%d %H:%M}\n" if when else "No commits yet\n")
            + (f"git_autosync last pushed this: {synced}"
               if synced else "git_autosync has not pushed this repo itself")
            + "\n\nStatus says whether anything is still waiting to go up.")

    def _align_header_to_rows(self):
        """Reserve the same width for the header's Actions column as a real
        row's buttons occupy, so the columns line up instead of the header
        drifting right by the width of the button cluster."""
        rows = list(self._row_widgets.values())
        if not rows or not hasattr(self, "_header_actions"):
            return
        row = max(rows, key=lambda r: r.width())
        if row.width() <= 0:
            return
        buttons = [b for b in (row.dry_run_btn, row.sync_btn, row.publish_btn,
                               row.privacy_btn, row.ignore_btn) if b and b.isVisible()]
        if not buttons:
            return
        left = min(b.x() for b in buttons)
        right = max(b.x() + b.width() for b in buttons)
        actions_w = max(1, right - left)
        self._header_actions.setFixedWidth(actions_w)
        # The list has a frame and may show a scrollbar; match that inset so
        # the header's right edge lands where the rows' does.
        frame = self.repo_list.frameWidth()
        bar = (self.repo_list.verticalScrollBar().width()
               if self.repo_list.verticalScrollBar().isVisible() else 0)
        self._list_header.setContentsMargins(frame, 0, frame + bar, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._align_header_to_rows)

    def _refresh_drift_banner(self):
        """Say when the list and the disk disagree. A dry-run reports on the
        list as configured; it cannot notice a repo that was never listed."""
        try:
            entries = config.read_repos(self.config_path)
            plan = plan_changes(entries, paths.discover_repos())
        except Exception:
            self.drift_banner.hide()
            return
        gone = len(plan["relocate"]) + len(plan["drop"])
        new = len(plan["new"])
        if not gone and not new:
            self.drift_banner.hide()
            return
        bits = []
        if gone:
            bits.append(f"{gone} listed repo(s) moved or no longer exist")
        if new:
            bits.append(f"{new} repo(s) on disk are not in your list")
        self.drift_banner.setText(
            "Your repo list is out of date — " + ", and ".join(bits) +
            ". Click ‘Find repos…’ above to fix it. (Dry-run only checks "
            "the repos already in the list, so it cannot find these.)")
        self.drift_banner.show()

    def _on_rescan(self):
        dlg = RescanDialog(self, self.config_path)
        if dlg.exec() == QDialog.Accepted:
            self._reload_repo_list()
            self._refresh_last_sync_label()

    def _set_all_checked(self, checked: bool):
        for row in self._row_widgets.values():
            if not row.is_missing():
                row.checkbox.setChecked(checked)
        self._refresh_select_all_box()

    def _on_select_all_clicked(self, checked: bool):
        self._set_all_checked(checked)

    def _refresh_select_all_box(self):
        """Reflect the rows: all / none / partial, without re-triggering."""
        if not hasattr(self, "select_all_box"):
            return
        rows = [r for r in self._row_widgets.values() if not r.is_missing()]
        checked = sum(1 for r in rows if r.is_checked())
        box = self.select_all_box
        box.blockSignals(True)
        box.setTristate(0 < checked < len(rows))
        if not rows or checked == 0:
            box.setCheckState(Qt.Unchecked)
        elif checked == len(rows):
            box.setCheckState(Qt.Checked)
        else:
            box.setCheckState(Qt.PartiallyChecked)
        box.blockSignals(False)

    def _on_pull_selected(self):
        """Pull every ticked repo that is actually behind."""
        targets = [n for n in self._checked_repos() if n in self._behind]
        if not targets:
            QMessageBox.information(
                self, "Nothing to pull",
                "None of the ticked repos is known to be behind its remote.\n\n"
                "Run Dry-run first — that is what checks the remotes.")
            return
        if QMessageBox.question(
            self, "Pull selected",
            "Fast-forward these repos to their remotes?\n\n  "
            + "\n  ".join(targets)
            + "\n\nNothing local is discarded; a repo that has diverged is "
              "reported and left alone.",
            QMessageBox.Cancel | QMessageBox.Yes, QMessageBox.Yes,
        ) != QMessageBox.Yes:
            return
        done, refused = [], []
        for name in targets:
            ok, message = self._pull_one(name)
            (done if ok else refused).append(f"{name}: {message}")
        report = ""
        if done:
            report += "Fast-forwarded:\n  " + "\n  ".join(done)
        if refused:
            report += ("\n\n" if report else "") + "Left alone:\n  " + "\n  ".join(refused)
        self._append_output(report + "\n")
        QMessageBox.information(self, "Pull selected", report)

    def _on_privacy_selected(self):
        """Flip visibility on every ticked repo, in one confirmed step."""
        targets = [n for n, r in self._row_widgets.items()
                   if r.is_checked() and not r.is_missing() and r.privacy_btn]
        if not targets:
            QMessageBox.information(self, "Nothing selected",
                                    "Tick the repos whose visibility you want to change.")
            return
        choice = QMessageBox.question(
            self, "Change visibility",
            f"Make these {len(targets)} repo(s) PRIVATE?\n\n  "
            + "\n  ".join(targets)
            + "\n\nYes = private,  No = public,  Cancel = leave them alone.",
            QMessageBox.Cancel | QMessageBox.No | QMessageBox.Yes, QMessageBox.Cancel)
        if choice == QMessageBox.Cancel:
            return
        target = "private" if choice == QMessageBox.Yes else "public"
        gh = paths.find_gh()
        if not gh:
            QMessageBox.warning(self, "gh not found", "Install the GitHub CLI first.")
            return
        lines = []
        for name in targets:
            slug = paths.repo_slug(name)
            if not slug:
                lines.append(f"{name}: no GitHub remote — skipped")
                continue
            r = subprocess.run(
                [gh, "repo", "edit", slug, "--visibility", target,
                 "--accept-visibility-change-consequences"],
                capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                lines.append(f"{name}: now {target}")
                row = self._row_widgets.get(name)
                if row:
                    row.set_visibility(target == "private")
            else:
                lines.append(f"{name}: FAILED — {r.stderr.strip()[:80]}")
        report = "\n  ".join(lines)
        self._append_output("Visibility:\n  " + report + "\n")
        QMessageBox.information(self, "Visibility", "  " + report)

    def _pull_one(self, name: str) -> tuple[bool, str]:
        """fetch + pull --ff-only for one repo. Returns (succeeded, message)."""
        git = paths.find_git() or "git"
        path = paths.resolve_entry(name)
        try:
            subprocess.run([git, "-C", str(path), "fetch", "origin"],
                           capture_output=True, text=True, timeout=120)
            r = subprocess.run([git, "-C", str(path), "pull", "--ff-only"],
                               capture_output=True, text=True, timeout=120)
        except Exception as exc:
            return False, f"{exc}"
        if r.returncode == 0:
            self._behind.discard(name)
            row = self._row_widgets.get(name)
            if row:
                row.set_behind(False)
                self._apply_time(row, name)
            return True, "up to date"
        ahead = subprocess.run([git, "-C", str(path), "rev-list", "--count", "@{u}..HEAD"],
                               capture_output=True, text=True).stdout.strip() or "?"
        return False, f"diverged ({ahead} local commit(s)) — needs git pull --rebase"

    def _on_pull(self, name: str):
        """Fast-forward a repo that is behind, so the next sync can push.

        --ff-only on purpose: it either moves the branch forward cleanly or
        refuses. A merge or rebase here could leave conflicts in a repo the
        user was not even looking at.
        """
        git = paths.find_git() or "git"
        path = paths.resolve_entry(name)
        try:
            fetch = subprocess.run([git, "-C", str(path), "fetch", "origin"],
                                   capture_output=True, text=True, timeout=120)
            r = subprocess.run([git, "-C", str(path), "pull", "--ff-only"],
                               capture_output=True, text=True, timeout=120)
        except Exception as exc:
            QMessageBox.warning(self, "Pull failed", f"{name}\n\n{exc}")
            return

        out = (r.stdout + r.stderr).strip()
        if r.returncode == 0:
            QMessageBox.information(
                self, "Up to date",
                f"'{name}' is now level with its remote.\n\n{out[-500:]}")
            self._append_output(f"Pulled {name}: fast-forwarded.\n")
            self._behind.discard(name)
            row = self._row_widgets.get(name)
            if row:
                row.set_behind(False)
                row.set_status("SYNCED")
                self._apply_time(row, name)
            return

        # ff-only refuses when both sides have commits. That needs a decision
        # this app should not make silently.
        ahead = subprocess.run([git, "-C", str(path), "rev-list", "--count", "@{u}..HEAD"],
                               capture_output=True, text=True).stdout.strip() or "?"
        QMessageBox.warning(
            self, "Cannot fast-forward",
            f"'{name}' has diverged: {ahead} local commit(s) the remote does not "
            f"have, and commits on the remote you do not have. A fast-forward "
            f"would lose one side, so nothing was changed.\n\n"
            f"Resolve it yourself, in that repo:\n"
            f"    git -C \"{path}\" pull --rebase\n\n"
            f"git said:\n{out[-500:]}")

    def _on_remove_entry(self, name: str):
        """Drop one entry from the list. Touches the list only."""
        if QMessageBox.question(
            self, "Remove from list?",
            f"Remove '{name}' from the repo list?\n\nNothing on disk and "
            "nothing on GitHub is touched — git_autosync just stops tracking it.",
            QMessageBox.Cancel | QMessageBox.Yes, QMessageBox.Yes,
        ) != QMessageBox.Yes:
            return
        entries = [e for e in config.read_repos(self.config_path) if e != name]
        config.write_repos(self.config_path, entries)
        self._reload_repo_list()

    def _checked_repos(self) -> list[str]:
        """Names of repos whose checkbox is ticked. Falls back to all if none ticked."""
        checked = [n for n, r in self._row_widgets.items() if r.is_checked()]
        return checked if checked else list(self._row_widgets.keys())

    def _set_all_row_buttons_enabled(self, enabled: bool):
        for row in self._row_widgets.values():
            # A missing repo stays disabled: re-enabling it after a run made
            # dead entries look clickable again.
            row.set_buttons_enabled(enabled and not row.is_missing())

    def _check_gitleaks(self):
        found = paths.find_gitleaks()
        self.gitleaks_cmd = found or "gitleaks"
        if not found:
            self.gitleaks_banner.setText(
                "gitleaks not found — real syncs are disabled until it's installed.\n"
                "Run:  brew install gitleaks"
            )
            self.gitleaks_banner.show()
            self.sync_btn.setEnabled(False)
        else:
            self.gitleaks_banner.hide()
            self.sync_btn.setEnabled(True)

    # ── Diff preview ──────────────────────────────────────────────

    def _diff_preview(self, repo: str | None = None, repos: list | None = None) -> str:
        """Return a git status --short summary for repos that have changes."""
        git = paths.find_git() or "git"
        lab = paths.lab_active_dir()
        if repo:
            names = [repo]
        elif repos is not None:
            names = repos
        else:
            names = config.read_repos(self.config_path)
        lines = []
        for name in names:
            raw = name
            if raw.startswith("/") or raw.startswith("~"):
                repo_path = Path(raw).expanduser()
            else:
                repo_path = lab / raw
            if not repo_path.is_dir():
                continue
            result = subprocess.run(
                [git, "-C", str(repo_path), "status", "--short"],
                capture_output=True, text=True,
            )
            stat = result.stdout.strip()
            if stat:
                formatted = _format_git_status(stat)
                lines.append(f"{name}:\n{formatted}")
        return "\n\n".join(lines) if lines else ""

    # ── Actions ───────────────────────────────────────────────────

    def _on_dry_run(self):
        repos = self._checked_repos()
        if len(repos) == len(self._row_widgets):
            self._run(dry_run=True)
        else:
            # Run each checked repo sequentially via a single invocation isn't
            # possible with the current engine (one --repo at a time), so for
            # a subset we just run all and filter visually — or run per-repo.
            # Simplest correct approach: run all but only show status for checked.
            self._run(dry_run=True, repos=repos)

    def _on_sync(self):
        if not paths.find_gitleaks():
            QMessageBox.warning(self, "gitleaks missing",
                                "Install gitleaks before running a real sync.")
            return
        repos = self._checked_repos()
        preview = self._diff_preview(repos=repos)
        scope = "selected repos" if len(repos) < len(self._row_widgets) else "all repos"
        if not _confirm_sync_dialog(self,
                f"Commit and push changes for {scope} (after leak-gate clears each one).",
                preview):
            return
        self._run(dry_run=False, repos=repos)

    def _on_dry_run_single(self, name: str):
        self._run(dry_run=True, repo=name)

    def _on_sync_single(self, name: str):
        if not paths.find_gitleaks():
            QMessageBox.warning(self, "gitleaks missing",
                                "Install gitleaks before running a real sync.")
            return
        preview = self._diff_preview(name)
        if not _confirm_sync_dialog(self,
                f"Commit and push changes for '{name}' (after leak-gate clears it).",
                preview):
            return
        self._run(dry_run=False, repo=name)

    def _run(self, *, dry_run: bool, repo: str | None = None, repos: list | None = None):
        if self.runner.is_running():
            return
        self._current_dry_run = dry_run
        self._current_single_repo = repo
        self._current_repos = repos  # None means all
        self.output_pane.clear()
        self.summary_label.hide()
        self.dry_run_btn.setEnabled(False)
        self.sync_btn.setEnabled(False)
        self._set_all_row_buttons_enabled(False)
        # For a subset of repos, run them sequentially via --repo flag.
        # For a single repo or all repos, use existing path.
        if repos is not None and len(repos) == 1:
            repo = repos[0]
            repos = None
        if repos is not None and len(repos) < len(self._row_widgets):
            self._run_subset(dry_run=dry_run, repos=repos)
        else:
            self.runner.start(
                dry_run=dry_run,
                repo=repo,
                config_path=self.config_path,
                gitleaks_cmd=self.gitleaks_cmd,
                commit_message=self.msg_field.text().strip() or None,
            )

    def _run_subset(self, *, dry_run: bool, repos: list[str]):
        """Run the engine once per selected repo, collecting all output."""
        self._subset_repos = list(repos)
        self._subset_index = 0
        self._subset_dry_run = dry_run
        self._subset_summaries: list[dict] = []
        self._run_next_subset()

    def _run_next_subset(self):
        if self._subset_index >= len(self._subset_repos):
            self._finish_subset()
            return
        repo = self._subset_repos[self._subset_index]
        self._subset_index += 1
        self.runner.start(
            dry_run=self._subset_dry_run,
            repo=repo,
            config_path=self.config_path,
            gitleaks_cmd=self.gitleaks_cmd,
            commit_message=self.msg_field.text().strip() or None,
        )
        # Temporarily override finished handler for subset mode
        try:
            self.runner.finished.disconnect(self._on_finished)
        except RuntimeError:
            pass
        self.runner.finished.connect(self._on_subset_repo_finished)

    def _on_subset_repo_finished(self, exit_code: int, summary: dict):
        self._subset_summaries.append(summary)
        try:
            self.runner.finished.disconnect(self._on_subset_repo_finished)
        except RuntimeError:
            pass
        self.runner.finished.connect(self._on_finished)
        self._run_next_subset()

    def _finish_subset(self):
        # Merge summaries
        merged_repos = {}
        merged_counts = {"synced": 0, "blocked": 0, "skipped": 0, "errors": 0, "noop": 0}
        merged_findings = {}
        worst_exit = 0
        for s in self._subset_summaries:
            merged_repos.update(s.get("repos", {}))
            merged_findings.update(s.get("findings", {}))
            c = s.get("counts") or {}
            for k in merged_counts:
                merged_counts[k] += c.get(k, 0)
        merged = {"repos": merged_repos, "counts": merged_counts, "findings": merged_findings}
        self._on_finished(worst_exit, merged)

    def _append_output(self, text: str):
        self.output_pane.appendPlainText(_ANSI_RE.sub("", text).rstrip("\n"))

    def _on_finished(self, exit_code: int, summary: dict):
        self.dry_run_btn.setEnabled(True)
        self._check_gitleaks()
        self._set_all_row_buttons_enabled(True)

        if not self._current_dry_run:
            self._refresh_last_sync_label()
            synced_now = [n for n, i in summary["repos"].items() if i["status"] == "SYNCED"]
            repo_state.record_synced(synced_now)

        self._last_findings = summary.get("findings", {})

        # Update row badges, time labels, and blocked state
        for name, row in self._row_widgets.items():
            info = summary["repos"].get(name)
            if info:
                # OK + "would sync"/"would push" detail → "Pending": there is
                # work waiting (uncommitted changes, or commits not yet pushed).
                status = info["status"]
                detail = info.get("detail", "")
                if status == "OK" and ("would sync" in detail
                                       or "would push" in detail):
                    status = "PENDING"
                row.set_status(status)
                row.set_blocked(info["status"] == "BLOCKED")
                row.set_detail(detail or None)
                if "pull first" in detail or "behind remote" in detail:
                    self._behind.add(name)
                else:
                    self._behind.discard(name)
                row.set_behind(name in self._behind)
            if not row.is_missing():
                self._apply_time(row, name)

        # Leak report in output pane
        blocked = {n: f for n, f in self._last_findings.items()
                   if summary["repos"].get(n, {}).get("status") == "BLOCKED"}
        if blocked:
            lines = ["", "── Leak report ─────────────────────────────"]
            for name, f in blocked.items():
                lines.append(f"⛔  {name}")
                if f.get("file"):
                    loc = f['file']
                    if f.get("line"):
                        loc += f":{f['line']}"
                    rule = f"  ({f['rule']})" if f.get("rule") else ""
                    lines.append(f"     {loc}{rule}")
                if f.get("fingerprint"):
                    lines.append(f"     Fingerprint: {f['fingerprint']}")
                where = leak_triage.locate(paths.resolve_entry(name), f)
                lines.append(f"     Found {leak_triage.WHERE_TEXT[where]}.")
            lines.append("─────────────────────────────────────────────")
            lines.append("Nothing was pushed for these repos. Open 'Fix leak…' on the row "
                         "for what this means and how to clear it.")
            self.output_pane.appendPlainText("\n".join(lines))

        # Update "Ignore" button availability on blocked rows (via privacy_btn slot reuse
        # isn't ideal — we surface this through a right-click or just rely on the leak report)

        counts = summary["counts"]
        if counts:
            c = counts
            text = (f"synced {c['synced']} · blocked {c['blocked']} · "
                    f"skipped {c['skipped']} · errors {c['errors']} · "
                    f"no-op {c['noop']}")
        else:
            text = "Run finished — see output for details."

        if exit_code != 0:
            self.summary_label.setStyleSheet(
                "background:#FFE5E3; color:#C0392B; padding:8px 12px;"
                " border:1px solid #FFCDD2; border-radius:8px;"
            )
            problems = [f"{n}: {i.get('detail') or i['status'].lower()}"
                        for n, i in summary["repos"].items()
                        if i["status"] in ("ERROR", "BLOCKED")]
            if problems:
                shown = "; ".join(problems[:3])
                if len(problems) > 3:
                    shown += f"; +{len(problems) - 3} more"
                text = f"⚠  {shown}  —  " + text
            else:
                text = "⚠  Some repos were blocked or errored.  " + text
        else:
            self.summary_label.setStyleSheet(
                "background:#D1F2DC; color:#1A7A3A; padding:8px 12px;"
                " border:1px solid #A8E6B8; border-radius:8px;"
            )
        self.summary_label.setText(text)
        self.summary_label.show()
        self._refresh_tray_icon()

        # macOS notification
        if not self._current_dry_run:
            self._notify(exit_code, summary.get("counts"))

    def _notify(self, exit_code: int, counts: dict | None):
        if counts:
            body = (f"Synced {counts['synced']}, blocked {counts['blocked']}, "
                    f"skipped {counts['skipped']}")
        else:
            body = "Run complete — check the app for details."
        subtitle = "All clear" if exit_code == 0 else "Action needed"
        subprocess.Popen(
            ["osascript", "-e",
             f'display notification "{body}" with title "git_autosync" subtitle "{subtitle}"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _on_edit_repo_list(self):
        subprocess.run(["open", "-e", str(self.config_path)])

    def _on_create_repo(self):
        dialog = CreateRepoDialog(self, self.config_path, self.gitleaks_cmd)
        dialog.exec()
        self._reload_repo_list()
        self._refresh_last_sync_label()

    def _on_publish_single(self, name: str):
        dialog = CreateRepoDialog(self, self.config_path, self.gitleaks_cmd,
                                  preselect=name)
        dialog.exec()
        self._reload_repo_list()
        self._refresh_last_sync_label()

    def _on_privacy_single(self, name: str):
        gh = paths.find_gh()
        if not gh:
            QMessageBox.warning(self, "gh not found",
                                "Install the GitHub CLI first: brew install gh")
            return
        slug = paths.repo_slug(name)
        if not slug:
            QMessageBox.warning(
                self, "Not on GitHub",
                f"'{name}' has no GitHub 'origin' remote, so it has no visibility "
                "to change. Publish it to GitHub first.")
            return
        try:
            vis_r = subprocess.run(
                [gh, "api", f"repos/{slug}", "--jq", ".visibility"],
                capture_output=True, text=True, timeout=10,
            )
            current = vis_r.stdout.strip().lower()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not fetch visibility:\n{e}")
            return

        if current not in ("public", "private"):
            err = (vis_r.stderr or "").strip() or (vis_r.stdout or "").strip()
            if "404" in err or "not found" in err.lower():
                detail = (f"GitHub has no repository at {slug}.\n\n"
                          "Either it was renamed/deleted online, or your 'origin' "
                          "remote points somewhere that no longer exists.")
            else:
                detail = (f"Could not read visibility for {slug}.\n\n"
                          f"{err}\n\nCheck that 'gh auth login' has been run.")
            QMessageBox.warning(self, "Can't read visibility", detail)
            return

        target = "private" if current == "public" else "public"
        icon = "🔒" if current == "private" else "🌐"
        target_icon = "🌐" if target == "public" else "🔒"
        if QMessageBox.question(
            self, "Change visibility",
            f"'{name}' is currently {icon} {current.upper()}.\nMake it {target_icon} {target.upper()}?",
        ) != QMessageBox.Yes:
            return

        try:
            r = subprocess.run(
                [gh, "repo", "edit", slug,
                 "--visibility", target, "--accept-visibility-change-consequences"],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0:
                QMessageBox.information(self, "Done", f"'{name}' is now {target_icon} {target.upper()}.")
                # Update button label immediately
                row = self._row_widgets.get(name)
                if row:
                    row.set_visibility(target == "private")
            else:
                QMessageBox.warning(self, "Failed", f"gh repo edit failed:\n{r.stderr.strip()}")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _on_open_ignore(self, name: str):
        repo_path = paths.resolve_entry(name)
        if not repo_path.is_dir():
            QMessageBox.warning(self, "Not found", f"Could not find repo directory for '{name}'.")
            return
        finding = self._last_findings.get(name)
        if not (finding and finding.get("file")):
            IgnoreDialog(self, repo_path, finding=finding).exec()
            return

        where = leak_triage.locate(repo_path, finding)
        box = QMessageBox(self)
        box.setWindowTitle(f"Why is {name} blocked?")
        box.setTextFormat(Qt.RichText)
        box.setText(leak_triage.describe_block(name, finding, where))
        false_btn = box.addButton("False positive — allowlist…", QMessageBox.ActionRole)
        real_btn = None
        if where != leak_triage.UNCOMMITTED:
            real_btn = box.addButton("Real secret — remove from history…",
                                     QMessageBox.AcceptRole)
        manage_btn = box.addButton("Manage allowlist…", QMessageBox.ActionRole)
        box.addButton("Close", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is false_btn:
            self._allowlist_fingerprint(name, repo_path, finding)
        elif real_btn is not None and clicked is real_btn:
            self._purge_secret_from_history(name, repo_path, finding)
        elif clicked is manage_btn:
            IgnoreDialog(self, repo_path, finding=finding).exec()

    def _purge_secret_from_history(self, name: str, repo_path: Path, finding: dict):
        """Replace the flagged value with [REDACTED] in every commit.

        The engine's output is redacted, so the value is looked up again from
        the flagged commit (leak_triage.resolve_secret). Force-pushes only when
        the value had already reached origin, and only after a full scan of the
        rewritten history comes back clean.
        """
        import shutil

        git = paths.find_git() or "git"
        filter_repo = (paths.find_binary("git-filter-repo")
                       or shutil.which("git_filter_repo"))
        if not filter_repo:
            QMessageBox.warning(
                self, "git-filter-repo not found",
                "git-filter-repo is required to rewrite history.\n\n"
                "Install it with:\n  brew install git-filter-repo\n\nThen try again.")
            return

        secret = leak_triage.resolve_secret(repo_path, finding, self.gitleaks_cmd, git=git)
        if not secret:
            QMessageBox.warning(
                self, "Couldn't pin down the flagged value",
                "gitleaks no longer reports this exact finding in its commit, so "
                "there is nothing safe to rewrite. History was not touched.\n\n"
                "Run a dry-run to refresh the finding, then try again.")
            return

        published = leak_triage.published_with(repo_path, secret, git=git)
        loc = finding["file"] + (f":{finding['line']}" if finding.get("line") else "")
        if published:
            if QMessageBox.warning(
                self, "Rotate the key first",
                "This value is already on GitHub. Removing it from history does not "
                "un-leak it — anyone may already have a copy, and forks or caches "
                "keep old commits.\n\n"
                "Have you rotated or revoked this key with its provider?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) != QMessageBox.Yes:
                return
            steps = ("  2. Scan the rewritten history; push only if it is clean.\n"
                     "  3. Force-push the rewritten branches to origin.\n\n"
                     "This cannot be undone on GitHub.")
        else:
            steps = ("  2. Nothing is force-pushed — the value never reached GitHub.\n"
                     "     The next sync publishes the cleaned commits through the leak-gate.")
        if QMessageBox.question(
            self, "Rewrite git history?",
            f"Finding: {loc}\n\n"
            "  1. Replace the flagged value with [REDACTED] in every commit.\n"
            f"{steps}\n\n"
            "Committed copies of the file will read [REDACTED]; commit hashes from "
            "that point on change, so allowlist fingerprints for them go stale.\n\n"
            "Proceed?",
        ) != QMessageBox.Yes:
            return

        self.output_pane.appendPlainText(f"\nRewriting history for {name} — this may take a moment…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = leak_triage.purge_secret(repo_path, secret, filter_repo,
                                              self.gitleaks_cmd, git=git, push=published)
        except leak_triage.PurgeError as e:
            self.output_pane.appendPlainText(f"Stopped: {e}")
            QMessageBox.critical(self, "History rewrite stopped", str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.output_pane.appendPlainText(result + " Re-checking…")
        QMessageBox.information(self, "History cleaned",
                                f"{result}\n\nA dry-run will now re-check {name}.")
        self._on_dry_run_single(name)

    def _allowlist_fingerprint(self, name: str, repo_path: Path, finding: dict):
        """Confirm, add the fingerprint to .gitleaksignore, offer Undo, re-check."""
        fp = finding.get("fingerprint", "")
        if not fp:
            QMessageBox.warning(self, "No fingerprint",
                                "No fingerprint was captured for this finding. "
                                "Run a dry-run first, then try again.")
            return
        ignore_path = repo_path / ".gitleaksignore"
        if fp in leak_triage.ignore_entries(ignore_path):
            QMessageBox.information(self, "Already allowlisted",
                                    "This fingerprint is already in .gitleaksignore.\n"
                                    "Run a dry-run to refresh the status.")
            return

        loc = finding.get("file", "?") + (f":{finding['line']}" if finding.get("line") else "")
        pinned = ("\n\nThe entry is pinned to one commit: if that commit is amended "
                  "or rebased, the finding comes back and needs allowlisting again."
                  if leak_triage.finding_commit(finding) else "")
        if QMessageBox.question(
            self, "Allowlist as a false positive?",
            f"{loc}  ·  {finding.get('rule') or 'unknown rule'}\n\n"
            "Only do this if the value is NOT a real secret (a test dummy, an "
            "example value, a name that only looks like a key).\n\n"
            f"This adds one line to {name}/.gitleaksignore on this Mac. Nothing is "
            "committed or pushed, and you can undo it here or later via "
            f"Fix leak… → Manage allowlist.{pinned}",
        ) != QMessageBox.Yes:
            return

        leak_triage.append_ignore_entry(ignore_path, fp)
        box = QMessageBox(self)
        box.setWindowTitle("Allowlisted")
        box.setText(f"Added to {name}/.gitleaksignore:\n{fp}\n\n"
                    "A dry-run will now re-check the repo.")
        undo_btn = box.addButton("Undo", QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if box.clickedButton() is undo_btn:
            leak_triage.remove_ignore_entry(ignore_path, fp)
            self.output_pane.appendPlainText(f"\nUndone: {fp} removed from {name}/.gitleaksignore.")
            return
        self.output_pane.appendPlainText(f"\nAllowlisted {fp} in {name}. Re-checking…")
        self._on_dry_run_single(name)

    def _on_allowlist_single(self, name: str):
        repo_path = paths.resolve_entry(name)
        if not repo_path.is_dir():
            QMessageBox.warning(self, "Not found", f"Could not find repo directory for '{name}'.")
            return
        finding = self._last_findings.get(name)
        if not finding or not finding.get("fingerprint"):
            QMessageBox.information(
                self, "No finding recorded",
                "No blocked finding is recorded for this repo.\n"
                "Run a dry-run first so the app captures the fingerprint, then try again."
            )
            return
        self._allowlist_fingerprint(name, repo_path, finding)

    def _on_open_logs(self):
        subprocess.run(["open", str(paths.user_log_dir())])

    def _on_toggle_tooltips(self, enabled: bool):
        self.tooltips_btn.setText("Tooltips: on" if enabled else "Tooltips")
        self._apply_tooltips(enabled)

    def _apply_tooltips(self, enabled: bool):
        pairs = [
            (self.dry_run_btn,    "dry_run_btn"),
            (self.sync_btn,       "sync_btn"),
            (self.edit_btn,       "edit_btn"),
            (self.create_repo_btn,"create_repo_btn"),
            (self.open_logs_btn,  "open_logs_btn"),
            (self.tooltips_btn,   "tooltips_btn"),
            (self.docs_btn,       "docs_btn"),
            (self.schedule_btn,   "schedule_btn"),
            (self.msg_field,      "msg_field"),
        ]
        for widget, key in pairs:
            widget.setToolTip(TOOLTIPS[key] if enabled else "")
        for row in self._row_widgets.values():
            row.set_tooltips(enabled)

    def _on_open_schedule_dialog(self):
        dialog = ScheduleDialog(self, self.config_path)
        dialog.exec()
        self._refresh_next_sync_label()

    def _on_open_documentation(self):
        readme = paths.readme_path()
        if readme.exists():
            DocumentationDialog(self, readme.read_text(),
                                updated=paths.readme_updated()).exec()
        else:
            QMessageBox.information(self, "Documentation",
                                    "README.md wasn't found alongside the app.")

    # ── System tray ───────────────────────────────────────────────

    def _setup_tray(self):
        self._tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(self._status_icon(paths.read_last_status()))
        self._tray.setToolTip("git_autosync")

        # Parented and kept on self: a bare QMenu() local is garbage-collected
        # once _setup_tray returns, leaving the status item holding a freed C++
        # object.
        self._tray_menu = menu = QMenu(self)
        menu.addAction("Open git_autosync", self._tray_open)
        menu.addSeparator()
        menu.addAction("Dry-run", lambda: self._run(dry_run=True))
        menu.addAction("Sync now", self._on_sync)
        menu.addSeparator()
        login_action = menu.addAction("Start at login")
        login_action.setCheckable(True)
        login_action.setChecked(login_item.is_enabled())
        login_action.toggled.connect(self._on_toggle_login_item)
        menu.addSeparator()
        menu.addAction("Quit", self._tray_quit)
        # Not setContextMenu(): that hands the menu to AppKit, which opens it
        # inside an NSMenuTrackingSession during event dispatch. Qt observes
        # NSMenuDidBeginTracking and asks [NSApp currentEvent] for clickCount;
        # on macOS 27 that event is not a mouse event there, the assertion
        # raises an NSException, and it unwinds through libqcocoa's C++ frames
        # into std::terminate — SIGABRT, no log. Showing the menu ourselves one
        # event-loop turn later keeps it out of that callout.
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

        QApplication.instance().setQuitOnLastWindowClosed(False)

        self._tray_timer = QTimer(self)
        self._tray_timer.timeout.connect(self._on_tray_tick)
        self._tray_timer.start(60_000)

    def _status_icon(self, status: str | None) -> QIcon:
        color = TRAY_COLORS.get(status, TRAY_COLORS[None])
        px = QPixmap(32, 32)
        px.fill(Qt.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(color)
        p.setPen(Qt.NoPen)
        p.drawEllipse(4, 4, 24, 24)
        p.end()
        return QIcon(px)

    def _refresh_tray_icon(self):
        if not self._tray:
            return
        status = paths.read_last_status()
        self._tray.setIcon(self._status_icon(status))
        stamp = paths.read_last_sync()
        success = paths.read_last_success()
        label = {"ok": "all clear", "attention": "needs attention"}.get(status, "no runs yet")
        tip = f"git_autosync — {label}"
        if stamp:
            tip += f"\nLast run: {stamp}"
        if status == "attention":
            tip += (f"\nLast good sync: {success}" if success
                    else "\nNo fully clean sync yet")
            tip += "\nOpen the app and check the log for the failing repo."
        self._tray.setToolTip(tip)

    def _on_tray_tick(self):
        self._refresh_tray_icon()
        self._refresh_last_sync_label()

    def _on_tray_activated(self, reason):
        """Open the tray menu, but never from inside AppKit's callout.

        Opening it here synchronously is what aborts the process (see
        _setup_tray). Deferring to the next event-loop turn means the menu is
        opened from Qt's own loop, after AppKit has finished dispatching the
        click.
        """
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.Context,
                      QSystemTrayIcon.DoubleClick):
            QTimer.singleShot(0, self._popup_tray_menu)

    def _popup_tray_menu(self):
        """Show the tray menu without activating the app.

        The app must not be activated: activation is what drags the main window
        forward, and only "Open git_autosync" should do that. But an inactive
        app's first click normally goes to activation rather than to the item
        under the cursor, which is why the menu did nothing when activation was
        simply removed.

        The way out is the panel style. Qt backs a popup with a QNSPanel, and a
        panel carrying NSWindowStyleMaskNonactivatingPanel takes mouse events
        while its application is inactive. So the menu is shown first (which
        creates the window), the style is applied to it, and the click lands on
        the item with the app still in the background.

        The native alternative, setContextMenu, is not available here: it aborts
        on macOS 27 from an NSException in Qt's NSMenuDidBeginTracking observer,
        raised inside a CoreFoundation callout where the exception guard cannot
        reach it.
        """
        menu = getattr(self, "_tray_menu", None)
        if menu is None or self._tray is None:
            return
        if menu.isVisible():
            menu.hide()          # a second click on the icon dismisses it
            return
        try:
            menu.popup(QCursor.pos())
            result = make_window_nonactivating(int(menu.winId()))
            if not self._panel_style_logged:
                self._panel_style_logged = True
                if "nonactivating ON" not in result:
                    # Depends on Qt's private window class, so say so rather
                    # than quietly going back to stealing focus.
                    self._append_output(
                        f"Tray menu: could not make the popup non-activating "
                        f"({result}). Clicking the icon may bring the window "
                        f"forward.\n")
        except Exception as exc:          # never let the tray take the app down
            print(f"tray menu failed to open: {exc}")

    def _show_tray_hint(self):
        """Say what happened — a quit that visibly does nothing reads as a hang."""
        if self._tray_hint_shown or not self._tray:
            return
        self._tray.showMessage(
            "Still running in the menu bar",
            "Syncs keep running. To exit fully: menu bar icon → Quit.",
            QSystemTrayIcon.Information,
            4000,
        )
        self._tray_hint_shown = True

    def _tray_open(self):
        # First, or the raise below does nothing: an accessory app can't take
        # focus until its activation policy is back to Regular.
        self.hidden_to_tray = False
        set_dock_icon_visible(True)
        self.show()
        self.raise_()
        self.activateWindow()
        self.repaint()

    def _tray_quit(self):
        """Hard quit — the only path that actually terminates the process.

        Cmd+Q and Dock -> Quit deliberately only hide to the tray, so this has
        to be reliable: a stuck child process or a pending modal must not be
        able to keep the app alive.
        """
        app = QApplication.instance()
        if hasattr(app, "allow_quit"):
            app.allow_quit = True   # stop _App.event() from suppressing the quit
        if self._tray:
            self._tray.hide()
            self._tray.setVisible(False)
        # Kill any engine subprocess still running so it can't block exit.
        try:
            self.runner.stop()
        except Exception:
            pass
        app.quit()
        # Belt and braces: if the event loop is blocked (modal dialog, stuck
        # child), leave anyway rather than becoming unquittable.
        QTimer.singleShot(600, lambda: os._exit(0))

    def _on_hide_to_tray(self):
        self.close()          # closeEvent hides and drops the Dock tile

    def _on_quit_clicked(self):
        if self._tray and self._tray.isVisible():
            answer = QMessageBox.question(
                self, "Quit git_autosync?",
                "Quit completely?\n\nThe window and the menu bar icon both go "
                "away. A schedule set up with ‘Schedule…’ is a launchd job "
                "that runs the engine on its own, so it keeps syncing with "
                "the app closed — turn it off in ‘Schedule…’ if you want that "
                "to stop too.\n\nTo keep the menu bar icon, use ‘Hide to menu "
                "bar’ instead.",
                QMessageBox.Cancel | QMessageBox.Close,
                QMessageBox.Close,
            )
            if answer != QMessageBox.Close:
                return
        self._tray_quit()

    def _on_toggle_login_item(self, enabled: bool):
        try:
            if enabled:
                login_item.enable()
            else:
                login_item.disable()
        except Exception as e:
            QMessageBox.warning(self, "Login item error", str(e))

    def closeEvent(self, event):
        app = QApplication.instance()
        if getattr(app, "allow_quit", False):
            # A real quit gesture (Cmd+Q, Dock -> Quit, tray Quit) is in
            # progress — let the window close so the app can terminate.
            if self._tray:
                self._tray.hide()
            event.accept()
            return
        if self._tray and self._tray.isVisible():
            # Just closing the window: ignore rather than accept, so the
            # process and its menu bar icon stay alive in the background.
            event.ignore()
            self.hide()
            self.hidden_to_tray = True
            set_dock_icon_visible(False)
            self._show_tray_hint()
        else:
            event.accept()
