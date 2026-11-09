"""A single row in the repo list with checkbox, status badge, time label, and actions."""
from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

_BADGE = {
    "SYNCED":   ("#D1F2DC", "#1A7A3A", "✓ Synced"),
    "BLOCKED":  ("#FFE5E3", "#C0392B", "✕ Blocked"),
    "SKIP":     ("#F0F0F5", "#6E6E73", "⊘ Skipped"),
    "ERROR":    ("#FFF0E0", "#B45309", "⚠ Error"),
    "OK":       ("#D1F2DC", "#1A7A3A", "✓ Synced"),
    "PENDING":  ("#EEF4FF", "#2563EB", "● Pending"),
    "MISSING":  ("#FFE5E3", "#C0392B", "⚠ Missing"),
}
# The Backup column: is everything on this Mac (every branch, worktree and
# stash) in the private backup repo? Independent of the GitHub column, which
# says whether the project itself is committed and pushed.
_BACKUP = {
    "ok":      ("#D1F2DC", "#1A7A3A", "✓ Backed up"),
    # Files were left out (credentials, unscannable, too big) or a worktree
    # could not be read: the hover text says which.
    "partial": ("#FFF8E7", "#92400E", "⚠ Partial"),
    "missing": ("#EEF4FF", "#2563EB", "● Not yet"),
    "failed":  ("#FFF0E0", "#B45309", "⚠ Failed"),
}
# Column geometry, shared with the header row above the list so the two line
# up. Changing a width here changes the header too. Every column is fixed-width
# and the actions come last, so no button's text can push another out of line.
ROW_MARGINS = (10, 7, 10, 7)
ROW_SPACING = 10
CHECK_W = 20
NAME_W  = 280  # the name column is fixed, so the actions sit just after it
               # instead of being flung to the far edge of a wide window
VIS_W   = 70   # fits the VISIBILITY heading; Public / Private
TIME_W  = 96   # fits the 'LAST COMMIT' heading; see tests/test_header_fit.py
BADGE_W = 108
SYNC_W  = 76   # Sync, or Publish… on a repo with no remote yet
MENU_W  = 34   # the ⋯ menu: Dry-run, Privacy, Fix leak…
EXTRA_W = 76   # the one conditional button: Fix leak… or Pull
ACTIONS_W = SYNC_W + ROW_SPACING + MENU_W

_TIME_STYLE  = "color:#6E6E73; font-size:11px;"
_STALE_STYLE = "background:#FFF8E7; color:#92400E; border-radius:5px; padding:2px 7px; font-size:11px; font-weight:600;"
_EMPTY_STYLE = "background:transparent;"


_STRIPE_BG = "#EFEFF4"   # ~6% below white: visible as a band, still quiet


class _ElidedLabel(QLabel):
    """One line of muted text that ends in … instead of widening its column."""

    def __init__(self):
        super().__init__()
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def set_full_text(self, text: str):
        self._full = text or ""
        self._elide()

    def _elide(self):
        self.setText(self.fontMetrics().elidedText(
            self._full, Qt.ElideRight, max(self.width(), 10)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class RepoRow(QWidget):
    def __init__(self, name: str, on_dry_run, on_sync, on_publish=None, on_privacy=None,
             on_ignore=None, on_allowlist=None, on_remove=None, on_pull=None):
        super().__init__()
        self.name = name
        self._status = None
        self._missing = False
        self.github_url: str | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(*ROW_MARGINS)
        layout.setSpacing(ROW_SPACING)

        # Selection checkbox
        self.checkbox = QCheckBox()
        self.checkbox.setFixedWidth(CHECK_W)
        self.checkbox.setChecked(True)
        self.checkbox.setToolTip("Include in bulk Dry-run / Sync now")
        layout.addWidget(self.checkbox)

        # Sits at the front, not with the action buttons: appended at the end it
        # pushed a missing row's whole cluster left, out of line with every
        # other row. Hidden widgets take no layout space, so normal rows are
        # unaffected.
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setProperty("class", "rowButton")
        self.remove_btn.setToolTip(
            "Drop this entry from the repo list. Only the list changes — no "
            "files and no GitHub repo are touched.")
        self.remove_btn.setVisible(False)
        if on_remove is not None:
            self.remove_btn.clicked.connect(lambda: on_remove(name))
        layout.addWidget(self.remove_btn)

        # Two lines: the name, and under it why the row has the status it has
        # (what a dry-run would push, what is not backed up) — that used to
        # live only in a tooltip.
        name_col = QWidget()
        self._name_col = name_col
        name_col.setFixedWidth(NAME_W)
        col = QVBoxLayout(name_col)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        self.label = QLabel()
        self.label.setObjectName("repoName")
        self.label.setTextFormat(Qt.RichText)
        self.label.setText(self._name_markup(name))
        self.label.setToolTip(name)
        self.label.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        col.addWidget(self.label)
        self.detail_label = _ElidedLabel()
        self.detail_label.setStyleSheet("color:#6E6E73; font-size:11px; background:transparent;")
        col.addWidget(self.detail_label)
        layout.addWidget(name_col)

        self.vis_label = QLabel("")
        self.vis_label.setFixedWidth(VIS_W)
        self.vis_label.setStyleSheet("color:#6E6E73; font-size:11px; background:transparent;")
        layout.addWidget(self.vis_label)

        # Last-synced time (subtle, always shown when known)
        self.time_label = QLabel()
        self.time_label.setFixedWidth(TIME_W)
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time_label.setStyleSheet(_EMPTY_STYLE)
        self.time_label.setToolTip(
            "When git_autosync last pushed this repo. Commits you or another "
            "tool already pushed still show as Clean — this is not the date of "
            "the last commit.")
        layout.addWidget(self.time_label)

        # Colored status badge — hidden until first run
        self.badge = QLabel()
        self.badge.setFixedWidth(BADGE_W)
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setStyleSheet(_EMPTY_STYLE)
        layout.addWidget(self.badge)

        # Backup state, separate from the GitHub state above: a project needs
        # both, and either can be fine while the other is not.
        self.backup_badge = QLabel()
        self.backup_badge.setFixedWidth(BADGE_W)
        self.backup_badge.setAlignment(Qt.AlignCenter)
        self.backup_badge.setStyleSheet(_EMPTY_STYLE)
        self._backup = None
        layout.addWidget(self.backup_badge)

        # The one action people reach for. Everything else is in the ⋯ menu.
        if on_publish is not None:
            self.sync_btn    = None
            self.publish_btn = QPushButton("Publish…")
            self.publish_btn.setProperty("class", "rowButton")
            self.publish_btn.setFixedWidth(SYNC_W)
            self.publish_btn.clicked.connect(lambda: on_publish(name))
            layout.addWidget(self.publish_btn)
        else:
            self.publish_btn = None
            self.sync_btn = QPushButton("Sync")
            self.sync_btn.setProperty("class", "rowButton")
            self.sync_btn.setFixedWidth(SYNC_W)
            self.sync_btn.clicked.connect(lambda: on_sync(name))
            layout.addWidget(self.sync_btn)

        self.menu_btn = QPushButton("⋯")
        self.menu_btn.setProperty("class", "rowButton")
        self.menu_btn.setFixedWidth(MENU_W)
        self.menu_btn.setToolTip("More: Dry-run, Privacy, Fix leak…")
        self.menu_btn.setStyleSheet(
            "QPushButton::menu-indicator { image: none; width: 0; }")
        menu = QMenu(self.menu_btn)
        self.dry_run_btn = menu.addAction("Dry-run")
        self.dry_run_btn.triggered.connect(lambda: on_dry_run(name))
        if on_privacy is not None:
            self.privacy_btn = menu.addAction("Privacy…")
            self.privacy_btn.triggered.connect(lambda: on_privacy(name))
        else:
            self.privacy_btn = None
        self.ignore_menu_action = None
        if on_ignore is not None:
            self.ignore_menu_action = menu.addAction("Fix leak…")
            self.ignore_menu_action.triggered.connect(lambda: on_ignore(name))
        self.pull_action = menu.addAction("Pull…")
        self.pull_action.setVisible(False)
        if on_pull is not None:
            self.pull_action.triggered.connect(lambda: on_pull(name))
        self._behind = False
        self._blocked = False
        self.menu_btn.setMenu(menu)
        layout.addWidget(self.menu_btn)

        # The rest appear only when they matter, after the fixed columns, so
        # showing one never moves another row's buttons.
        if on_ignore is not None:
            self.ignore_btn = QPushButton("Fix leak…")
            self.ignore_btn.setProperty("class", "rowButton")
            self.ignore_btn.setToolTip("Explain why this repo is blocked and how to clear it: allowlist a false positive, remove a real secret from history, or manage the allowlist.")
            self.ignore_btn.setVisible(False)
            self.ignore_btn.setFixedWidth(EXTRA_W)
            self.ignore_btn.clicked.connect(lambda: on_ignore(name))
            layout.addWidget(self.ignore_btn)
        else:
            self.ignore_btn = None

        # Shown only when the repo is behind its remote, which is the one
        # failure the user cannot clear from this app otherwise.
        self.pull_btn = QPushButton("Pull")
        self.pull_btn.setProperty("class", "rowButton")
        self.pull_btn.setToolTip(
            "Fetch and fast-forward this repo to its remote, so the next sync "
            "can push. Never discards local work.")
        self.pull_btn.setVisible(False)
        self.pull_btn.setFixedWidth(EXTRA_W)
        if on_pull is not None:
            self.pull_btn.clicked.connect(lambda: on_pull(name))
        layout.addWidget(self.pull_btn)

        # Allowlisting lives in the ⋯ menu and shows only while a finding is
        # active: a third conditional button made a blocked row wider than the
        # window, which squeezed every column out of line.
        if on_allowlist is not None:
            self.allowlist_btn = menu.addAction("Allowlist (false positive)…")
            self.allowlist_btn.setToolTip("Add this finding to .gitleaksignore as a false positive. Asks first, local only, undoable.")
            self.allowlist_btn.setVisible(False)
            self.allowlist_btn.triggered.connect(lambda: on_allowlist(name))
        else:
            self.allowlist_btn = None

        # Slack goes here, after the actions — so the whole table packs to the
        # left and the name stays next to its own buttons on a wide window.
        layout.addStretch(1)
        self.set_status(None)
        self.set_backup(None)

    def set_name_width(self, width: int):
        """The Repository column is the one users resize (drag its header edge)."""
        self._name_col.setFixedWidth(width)
        self.detail_label.set_full_text(self.detail_label._full)

    def _markup(self) -> str:
        return self._name_markup(self.name, self.github_url)

    def set_github_url(self, url: str | None):
        """Show a ↗ link to the repo's GitHub page after its name, or none."""
        self.github_url = url
        if not self._missing:
            self.label.setText(self._markup())
            self.label.setToolTip(f"{self.name}\n{url}" if url else self.name)

    @staticmethod
    def _name_markup(entry: str, url: str | None = None) -> str:
        """Repo name in front, the folders it sits in behind it in grey.

        A nested entry like "sonar/sonar/macro" is otherwise read as one long
        string, and the doubled segment (repo dir + package dir of the same
        name) looks like a bug rather than a real path. The optional ↗ opens
        the repo on GitHub; it sits right after the name so no column moves.
        """
        parent, _, base = entry.rstrip("/").rpartition("/")
        link = (f"&nbsp;<a href='{url}' style='color:#0A66C2; text-decoration:none;'>↗</a>"
                if url else "")
        if not parent:
            return f"<b>{base}</b>{link}"
        return (f"<b>{base}</b>{link}"
                f"<span style='color:#9A9AA0;'>&nbsp;&nbsp;{parent}/</span>")

    # ── public API ────────────────────────────────────────────────

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def set_status(self, status: str | None):
        self._status = status
        if status and status in _BADGE:
            bg, fg, text = _BADGE[status]
            self.badge.setText(text)
            self.badge.setStyleSheet(
                f"background:{bg}; color:{fg}; border-radius:9px;"
                f" padding:3px 8px; font-size:11px; font-weight:600;"
            )
        else:
            self.badge.setText("—")
            self.badge.setStyleSheet(
                "color:#9A9AA0; background:transparent; font-size:11px;")

    def set_backup(self, state: str | None):
        """state: ok / partial / missing / failed, or None when unknown."""
        self._backup = state
        if state in _BACKUP:
            bg, fg, text = _BACKUP[state]
            self.backup_badge.setText(text)
            self.backup_badge.setStyleSheet(
                f"background:{bg}; color:{fg}; border-radius:9px;"
                f" padding:3px 8px; font-size:11px; font-weight:600;")
        else:
            self.backup_badge.setText("—")
            self.backup_badge.setStyleSheet(
                "color:#9A9AA0; background:transparent; font-size:11px;")

    def is_synced(self) -> bool:
        return self._status in ("SYNCED", "OK")

    def is_backed_up(self) -> bool:
        return self._backup == "ok"

    def set_time(self, time_str: str | None, stale: bool = False):
        """Show last-synced time. Amber pill if stale, subtle grey if recent."""
        if time_str is None:
            self.time_label.setText("never")
            self.time_label.setStyleSheet(_STALE_STYLE)
        elif stale:
            self.time_label.setText(time_str)
            self.time_label.setStyleSheet(_STALE_STYLE)
        else:
            self.time_label.setText(time_str)
            self.time_label.setStyleSheet(_TIME_STYLE)

    def set_stale(self, days: int | None):
        """Legacy shim — callers that pass days still work."""
        if days is None or days < 0:
            self.time_label.setText("")
            self.time_label.setStyleSheet(_EMPTY_STYLE)
        elif days == 0:
            self.set_time("today", stale=False)
        else:
            self.set_time(f"{days}d ago", stale=days >= 3)

    def set_visibility(self, is_private: bool | None):
        """Show Public / Private in its own column and in the menu entry."""
        if self.privacy_btn is None:
            return
        if is_private is None:
            self.vis_label.setText("")
            self.privacy_btn.setText("Privacy…")
        elif is_private:
            self.vis_label.setText("Private")
            self.privacy_btn.setText("Make public…")
        else:
            self.vis_label.setText("Public")
            self.privacy_btn.setText("Make private…")

    def is_missing(self) -> bool:
        return self._missing

    def set_stripe(self, odd: bool):
        """No-op: banding is the list's job.

        A widget set with setItemWidget sits on the viewport and the item's own
        background is painted over it, so styling the row itself showed nothing
        on screen even though the widget grabbed as tinted. QListWidget's
        alternating row colours paint the item, which does show.
        """
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAutoFillBackground(False)

    def set_detail(self, text: str | None):
        """Hover text explaining a status — why a sync errored or was blocked."""
        tip = text or ""
        for w in (self, self.label, self.badge, self.backup_badge, self.detail_label):
            w.setToolTip(tip)
        self.detail_label.set_full_text(tip)

    def set_missing(self, missing: bool):
        """Entry no longer resolves to a repo on disk — say so and disable it.

        Without this a stale entry looks identical to a healthy one: same row,
        same working buttons, and the failure only shows up mid-run.
        """
        self._missing = missing
        if missing:
            self.set_status("MISSING")
            self.set_backup(None)
            # A last-synced time for a repo that is gone is noise at best.
            self.time_label.setText("")
            self.time_label.setStyleSheet(_EMPTY_STYLE)
            # Plain text here: an inline span colour would beat the stylesheet.
            self.label.setText(self.name)
            self.label.setStyleSheet("color:#C0392B; text-decoration: line-through;")
            self.setToolTip(f"{self.name} no longer exists on disk. "
                            "Use 'Find repos…' to fix or remove it.")
            self.set_buttons_enabled(False)
            self.checkbox.setChecked(False)
            self.checkbox.setEnabled(False)
            # Removing it is the only action that still makes sense here.
            self.remove_btn.setVisible(True)
            self.remove_btn.setEnabled(True)
        else:
            self.remove_btn.setVisible(False)
            self.label.setText(self._markup())
            self.label.setStyleSheet("")
            self.setToolTip("")
            self.checkbox.setEnabled(True)

    def set_behind(self, behind: bool):
        """Offer the fix next to the problem, rather than only naming it."""
        self._behind = behind
        self._show_conditional()

    def _show_conditional(self):
        """One conditional button at most — Fix leak… beats Pull — so a row
        never grows past the fixed columns. Pull stays reachable in ⋯."""
        show_fix = self._blocked and self.ignore_btn is not None
        show_pull = self._behind and not show_fix
        self.pull_btn.setVisible(show_pull)
        self.pull_action.setVisible(self._behind)
        if show_pull:
            self.pull_btn.setStyleSheet(
                "QPushButton { color:#92400E; background:#FFF8E7;"
                " border:1px solid #F0D9A8; border-radius:5px;"
                " padding:3px 9px; font-size:11px; font-weight:700; }"
                "QPushButton:hover { background:#FDEFD0; }")
        else:
            self.pull_btn.setStyleSheet("")
        if self.ignore_btn:
            self.ignore_btn.setVisible(show_fix)

    def set_blocked(self, blocked: bool):
        """Show Fix leak… (red) and the Allowlist menu entry while a leak is active."""
        self._blocked = blocked
        if self.allowlist_btn:
            self.allowlist_btn.setVisible(blocked)
        if self.ignore_btn:
            self.ignore_btn.setStyleSheet(
                "QPushButton { color:#C0392B; background:#FDEDEC;"
                " border:1px solid #E6A9A1; border-radius:5px;"
                " padding:3px 9px; font-size:11px; font-weight:700; }"
                "QPushButton:hover { background:#FAD9D5; border-color:#D98B80; }"
            )
        self._show_conditional()

    def set_buttons_enabled(self, enabled: bool):
        self.menu_btn.setEnabled(enabled)
        if self.sync_btn:    self.sync_btn.setEnabled(enabled)
        if self.publish_btn: self.publish_btn.setEnabled(enabled)
        if self.ignore_btn:     self.ignore_btn.setEnabled(enabled)
        if self.remove_btn:     self.remove_btn.setEnabled(enabled or self._missing)
        if self.pull_btn:       self.pull_btn.setEnabled(enabled)

    def set_tooltips(self, enabled: bool):
        if self.sync_btn:
            self.sync_btn.setToolTip(
                f"Sync just {self.name} (leak-gate must clear first)." if enabled else ""
            )
        if self.publish_btn:
            self.publish_btn.setToolTip(
                f"Create a GitHub repo for {self.name} and push (leak-gated)." if enabled else ""
            )
        self.menu_btn.setToolTip(
            f"More for {self.name}: Dry-run, Privacy, Fix leak…" if enabled else "")
