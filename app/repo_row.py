"""A single row in the repo list with checkbox, status badge, time label, and actions."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget,
)

_BADGE = {
    "SYNCED":   ("#D1F2DC", "#1A7A3A", "✓ Synced"),
    "BLOCKED":  ("#FFE5E3", "#C0392B", "✕ Blocked"),
    "SKIP":     ("#F0F0F5", "#6E6E73", "⊘ Skipped"),
    "ERROR":    ("#FFF0E0", "#B45309", "⚠ Error"),
    "OK":       ("#F0F0F5", "#6E6E73", "Clean"),
    "PENDING":  ("#EEF4FF", "#2563EB", "● Pending"),
    "MISSING":  ("#FFE5E3", "#C0392B", "⚠ Missing"),
}
# Column geometry, shared with the header row above the list so the two line
# up. Changing a width here changes the header too.
ROW_MARGINS = (8, 6, 10, 6)
ROW_SPACING = 8
CHECK_W = 20
TIME_W  = 96   # fits the 'LAST SYNCED' heading; see tests/test_header_fit.py
BADGE_W = 88

_TIME_STYLE  = "color:#6E6E73; font-size:11px;"
_STALE_STYLE = "background:#FFF8E7; color:#92400E; border-radius:5px; padding:2px 7px; font-size:11px; font-weight:600;"
_EMPTY_STYLE = "background:transparent;"


_STRIPE_BG = "#EFEFF4"   # ~6% below white: visible as a band, still quiet


class RepoRow(QWidget):
    def __init__(self, name: str, on_dry_run, on_sync, on_publish=None, on_privacy=None,
             on_ignore=None, on_allowlist=None, on_remove=None):
        super().__init__()
        self.name = name
        self._status = None
        self._missing = False

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

        self.label = QLabel()
        self.label.setObjectName("repoName")
        self.label.setTextFormat(Qt.RichText)
        self.label.setText(self._name_markup(name))
        self.label.setToolTip(name)
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self.label, stretch=1)

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

        self.dry_run_btn = QPushButton("Dry-run")
        self.dry_run_btn.setProperty("class", "rowButton")
        self.dry_run_btn.clicked.connect(lambda: on_dry_run(name))
        layout.addWidget(self.dry_run_btn)

        if on_publish is not None:
            self.sync_btn    = None
            self.privacy_btn = None
            self.publish_btn = QPushButton("Publish to GitHub…")
            self.publish_btn.setProperty("class", "rowButton")
            self.publish_btn.clicked.connect(lambda: on_publish(name))
            layout.addWidget(self.publish_btn)
        else:
            self.publish_btn = None
            self.sync_btn = QPushButton("Sync")
            self.sync_btn.setProperty("class", "rowButton")
            self.sync_btn.clicked.connect(lambda: on_sync(name))
            layout.addWidget(self.sync_btn)

            self.privacy_btn = QPushButton("Privacy…")
            self.privacy_btn.setProperty("class", "rowButton")
            self.privacy_btn.clicked.connect(lambda: on_privacy(name))
            layout.addWidget(self.privacy_btn)
            self._privacy_known = False

        if on_ignore is not None:
            self.ignore_btn = QPushButton("Fix leak…")
            self.ignore_btn.setProperty("class", "rowButton")
            self.ignore_btn.setToolTip("Explain why this repo is blocked and how to clear it: allowlist a false positive, remove a real secret from history, or manage the allowlist.")
            self.ignore_btn.clicked.connect(lambda: on_ignore(name))
            layout.addWidget(self.ignore_btn)
        else:
            self.ignore_btn = None

        if on_allowlist is not None:
            self.allowlist_btn = QPushButton("Allowlist")
            self.allowlist_btn.setProperty("class", "rowButton")
            self.allowlist_btn.setToolTip("Add this finding to .gitleaksignore as a false positive. Asks first, local only, undoable.")
            self.allowlist_btn.setVisible(False)  # shown only when a finding is active
            self.allowlist_btn.clicked.connect(lambda: on_allowlist(name))
            layout.addWidget(self.allowlist_btn)
        else:
            self.allowlist_btn = None

    @staticmethod
    def _name_markup(entry: str) -> str:
        """Repo name in front, the folders it sits in behind it in grey.

        A nested entry like "sonar/sonar/macro" is otherwise read as one long
        string, and the doubled segment (repo dir + package dir of the same
        name) looks like a bug rather than a real path.
        """
        parent, _, base = entry.rstrip("/").rpartition("/")
        if not parent:
            return f"<b>{base}</b>"
        return (f"<b>{base}</b>"
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
                f"background:{bg}; color:{fg}; border-radius:5px;"
                f" padding:2px 7px; font-size:11px; font-weight:600;"
            )
        else:
            self.badge.setText("")
            self.badge.setStyleSheet(_EMPTY_STYLE)

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
        """Update the Privacy button label to show current state."""
        if self.privacy_btn is None:
            return
        if is_private is None:
            self.privacy_btn.setText("Privacy…")
        elif is_private:
            self.privacy_btn.setText("🔒 Private")
        else:
            self.privacy_btn.setText("🌐 Public")

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
        for w in (self, self.label, self.badge):
            w.setToolTip(tip)

    def set_missing(self, missing: bool):
        """Entry no longer resolves to a repo on disk — say so and disable it.

        Without this a stale entry looks identical to a healthy one: same row,
        same working buttons, and the failure only shows up mid-run.
        """
        self._missing = missing
        if missing:
            self.set_status("MISSING")
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
            self.label.setText(self._name_markup(self.name))
            self.label.setStyleSheet("")
            self.setToolTip("")
            self.checkbox.setEnabled(True)

    def set_blocked(self, blocked: bool):
        """Show the Allowlist button and colour Fix leak… red when a leak is active."""
        if self.allowlist_btn:
            self.allowlist_btn.setVisible(blocked)
        if self.ignore_btn:
            if blocked:
                self.ignore_btn.setStyleSheet(
                    "QPushButton { color:#C0392B; background:#FDEDEC;"
                    " border:1px solid #E6A9A1; border-radius:5px;"
                    " padding:3px 9px; font-size:11px; font-weight:700; }"
                    "QPushButton:hover { background:#FAD9D5; border-color:#D98B80; }"
                )
            else:
                self.ignore_btn.setStyleSheet("")

    def set_buttons_enabled(self, enabled: bool):
        self.dry_run_btn.setEnabled(enabled)
        if self.sync_btn:    self.sync_btn.setEnabled(enabled)
        if self.publish_btn: self.publish_btn.setEnabled(enabled)
        if self.privacy_btn: self.privacy_btn.setEnabled(enabled)
        if self.ignore_btn:     self.ignore_btn.setEnabled(enabled)
        if self.remove_btn:     self.remove_btn.setEnabled(enabled or self._missing)
        if self.allowlist_btn:  self.allowlist_btn.setEnabled(enabled)

    def set_tooltips(self, enabled: bool):
        self.dry_run_btn.setToolTip(f"Dry-run just {self.name}." if enabled else "")
        if self.sync_btn:
            self.sync_btn.setToolTip(
                f"Sync just {self.name} (leak-gate must clear first)." if enabled else ""
            )
        if self.publish_btn:
            self.publish_btn.setToolTip(
                f"Create a GitHub repo for {self.name} and push (leak-gated)." if enabled else ""
            )
        if self.privacy_btn:
            self.privacy_btn.setToolTip(
                f"Toggle {self.name} between public and private on GitHub." if enabled else ""
            )
