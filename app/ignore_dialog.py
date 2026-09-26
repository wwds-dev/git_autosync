"""Dialog for managing a repo's .gitleaksignore file (fingerprint allowlist).

gitleaks treats each line as an ignored fingerprint, formatted as:
  <commit>:<file>:<rule>:<line>
The dialog lets the user view, add (from a last finding), and remove entries.
Comment lines are left alone: edits go line by line through leak_triage, so
the notes explaining why an entry exists survive.
"""
from pathlib import Path

from . import leak_triage

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
    QMessageBox, QPushButton, QVBoxLayout,
)


class IgnoreDialog(QDialog):
    def __init__(self, parent, repo_dir: Path, finding: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Gitleaks ignore — {repo_dir.name}")
        self.resize(560, 380)
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

        self.entry_list = QListWidget()
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

        self.entry_list.currentRowChanged.connect(
            lambda i: self.remove_btn.setEnabled(i >= 0)
        )

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load()

    def _load(self):
        self.entry_list.clear()
        for fp in leak_triage.ignore_entries(self._ignore_path):
            self.entry_list.addItem(fp)

    def _add_finding(self):
        fp = self._finding["fingerprint"]
        # Check for duplicates
        for i in range(self.entry_list.count()):
            if self.entry_list.item(i).text() == fp:
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
        row = self.entry_list.currentRow()
        if row < 0:
            return
        item = self.entry_list.item(row)
        reply = QMessageBox.question(
            self, "Remove entry",
            f"Remove this fingerprint from the ignore list?\n\n{item.text()}"
        )
        if reply == QMessageBox.Yes:
            leak_triage.remove_ignore_entry(self._ignore_path, item.text())
            self._load()
