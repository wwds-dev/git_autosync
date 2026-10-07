"""Dialog for managing a repo's .gitleaksignore file (fingerprint allowlist).

gitleaks treats each line as an ignored fingerprint, formatted as:
  <commit>:<file>:<rule>:<line>
The dialog lets the user view, add (from a last finding), and remove entries.
Comment lines are left alone: edits go line by line through leak_triage, so
the notes explaining why an entry exists survive.
"""
from pathlib import Path

from . import leak_triage

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)


class IgnoreDialog(QDialog):
    def __init__(self, parent, repo_dir: Path, finding: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Gitleaks ignore — {repo_dir.name}")
        # Wide enough for a path and a rule side by side: the single-column
        # list truncated every entry mid-hash, so the file it referred to —
        # the only part that lets you judge it — was never visible.
        self.resize(820, 420)
        self._ignore_path = repo_dir / ".gitleaksignore"
        self._finding = finding

        layout = QVBoxLayout(self)

        info = QLabel(
            "Fingerprints listed here are skipped by gitleaks. "
            "Only add entries you are certain are false positives — "
            "ignoring a real secret is a security risk. This file is local "
            "until committed; removing an entry re-enables the block on the "
            "next scan."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #6E6E73; font-size: 12px;")
        layout.addWidget(info)

        self.entry_list = QTreeWidget()
        self.entry_list.setColumnCount(4)
        self.entry_list.setHeaderLabels(["File", "Line", "Rule", "Commit"])
        self.entry_list.setRootIsDecorated(False)
        self.entry_list.setAlternatingRowColors(True)
        self.entry_list.setSelectionMode(QAbstractItemView.SingleSelection)
        header = self.entry_list.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        layout.addWidget(self.entry_list, stretch=1)

        row = QHBoxLayout()
        if finding and finding.get("fingerprint"):
            self.add_btn = QPushButton("Add last finding to ignore list")
            self.add_btn.clicked.connect(self._add_finding)
            row.addWidget(self.add_btn)
        row.addStretch(1)
        self.remove_btn = QPushButton("Remove selected")
        self.remove_btn.setEnabled(False)
        self.remove_btn.clicked.connect(self._remove_selected)
        row.addWidget(self.remove_btn)
        layout.addLayout(row)

        self.entry_list.currentItemChanged.connect(
            lambda cur, _prev: self.remove_btn.setEnabled(cur is not None)
        )

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load()

    def _load(self):
        self.entry_list.clear()
        for fp in leak_triage.ignore_entries(self._ignore_path):
            parts = leak_triage.parse_fingerprint(fp)
            item = QTreeWidgetItem([
                parts["file"],
                parts["line"],
                parts["rule"],
                parts["commit"][:7],
            ])
            # The full fingerprint is what gets removed, and what the user may
            # want to copy — keep it on the row rather than on screen.
            item.setData(0, Qt.UserRole, fp)
            item.setToolTip(0, fp)
            self.entry_list.addTopLevelItem(item)

    def _add_finding(self):
        fp = self._finding["fingerprint"]
        # Check for duplicates
        for i in range(self.entry_list.topLevelItemCount()):
            if self.entry_list.topLevelItem(i).data(0, Qt.UserRole) == fp:
                QMessageBox.information(self, "Already ignored",
                                        "This fingerprint is already in the ignore list.")
                return
        leak_triage.append_ignore_entry(self._ignore_path, fp)
        self._load()
        QMessageBox.information(
            self, "Added",
            f"Added to .gitleaksignore:\n{fp}\n\n"
            "Run a dry-run to confirm gitleaks no longer flags this repo."
        )

    def _remove_selected(self):
        item = self.entry_list.currentItem()
        if item is None:
            return
        fp = item.data(0, Qt.UserRole)
        parts = leak_triage.parse_fingerprint(fp)
        where = parts["file"] + (f":{parts['line']}" if parts["line"] else "")
        reply = QMessageBox.question(
            self, "Remove entry",
            f"Stop ignoring this finding?\n\n{where}\nrule {parts['rule']}\n\n"
            "gitleaks will flag it again on the next scan, and the repo will be "
            "blocked until it is dealt with."
        )
        if reply == QMessageBox.Yes:
            leak_triage.remove_ignore_entry(self._ignore_path, fp)
            self._load()
