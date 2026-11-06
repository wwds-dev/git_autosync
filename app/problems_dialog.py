"""Shows what went wrong in a run, and the fix for each one.

Fixes that need no input (pull, remove from list) can be ticked and run
together; the dialog stays open and reports each result on its card, then
offers to sync just the repos that were fixed, instead of a full rescan.
"""
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

_VERBS = {"pull": "Pull (fast-forward only)", "remove": "Remove from the list"}

_CARD_STYLE = ("QFrame#card { background:#FBFBFD; border:1px solid #E5E5EA;"
               " border-radius:8px; }")
_DONE_STYLE = ("QFrame#card { background:#F2F8F3; border:1px solid #CDE8D3;"
               " border-radius:8px; }")


class _FixWorker(QObject):
    """Runs the batch off the UI thread: a pull can take minutes."""
    one_done = Signal(int, bool, str)
    all_done = Signal()

    def __init__(self, jobs, fix_one):
        super().__init__()
        self._jobs = jobs
        self._fix_one = fix_one

    def run(self):
        for idx, action, repo in self._jobs:
            try:
                ok, message = self._fix_one(action, repo)
            except Exception as exc:
                ok, message = False, f"{exc}"
            self.one_done.emit(idx, ok, message)
        self.all_done.emit()


class _Card:
    def __init__(self, adv):
        self.adv = adv
        self.frame = None
        self.check = None
        self.status = None
        self.button = None
        self.done = False


class ProblemsDialog(QDialog):
    def __init__(self, parent, advices, run_action, *, fix_one=None,
                 after_fixes=None, sync_repos=None, busy=lambda: False,
                 resolved=lambda key, repo: None):
        """
        run_action(key, repo)    one interactive fix; may open its own window,
                                 so the dialog hides while it runs.
        fix_one(key, repo)       one batchable fix -> (ok, message). Runs on a
                                 worker thread: it must not touch widgets.
        after_fixes(results)     on the UI thread after a batch, with
                                 [(key, repo, ok, message)].
        sync_repos(names)        sync only these repos; the dialog closes first.
        busy()                   True when a fix started a run of its own; the
                                 dialog then closes, since that run reports anew.
        resolved(key, repo)      after an interactive fix: True fixed, False
                                 not, None cannot tell without a sync.
        """
        super().__init__(parent)
        self.setWindowTitle("Some repos need attention")
        self.setMinimumWidth(620)
        self._run_action = run_action
        self._fix_one = fix_one
        self._after_fixes = after_fixes
        self._sync_repos = sync_repos
        self._busy = busy
        self._resolved = resolved
        self._cards = [_Card(a) for a in advices]
        self._fixed: list[str] = []          # repos to offer for a re-sync
        self._thread = None
        self._worker = None
        self._results = []

        outer = QVBoxLayout(self)
        outer.setSpacing(10)

        self._head = QLabel()
        self._head.setWordWrap(True)
        outer.addWidget(self._head)

        self._select_all = None
        if fix_one and any(c.adv.batchable for c in self._cards):
            self._select_all = QCheckBox("Select all fixes that can run together")
            self._select_all.setTristate(False)
            self._select_all.clicked.connect(self._on_select_all)
            outer.addWidget(self._select_all)

        body = QWidget()
        body.setObjectName("problemsBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(10)
        for i, card in enumerate(self._cards):
            lay.addWidget(self._build_card(i, card))
        lay.addStretch(1)

        body.setStyleSheet(
            "#problemsBody { background:#FFFFFF; }"
            "#problemsBody QLabel, #problemsBody QCheckBox"
            " { color:#1D1D1F; background:transparent; }")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        scroll.setMinimumHeight(320)
        scroll.setStyleSheet(
            "QScrollArea { background:#FFFFFF; border:1px solid #E5E5EA;"
            " border-radius:8px; }"
            "QScrollArea > QWidget > QWidget { background:#FFFFFF; }")
        outer.addWidget(scroll, stretch=1)

        bar = QHBoxLayout()
        bar.addStretch(1)
        self._fix_btn = QPushButton()
        self._fix_btn.setStyleSheet("font-weight:600;")
        self._fix_btn.clicked.connect(self._on_fix_selected)
        self._fix_btn.setVisible(self._select_all is not None)
        bar.addWidget(self._fix_btn)
        self._sync_btn = QPushButton()
        self._sync_btn.clicked.connect(self._on_sync_fixed)
        self._sync_btn.setVisible(False)
        bar.addWidget(self._sync_btn)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.reject)
        bar.addWidget(self._close_btn)
        outer.addLayout(bar)

        self._refresh()

    # ── Cards ─────────────────────────────────────────────────────

    def _build_card(self, idx, card):
        adv = card.adv
        frame = QFrame()
        frame.setObjectName("card")
        frame.setStyleSheet(_CARD_STYLE)
        card.frame = frame
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(6)

        top = QHBoxLayout()
        if self._fix_one and adv.batchable:
            card.check = QCheckBox()
            card.check.setChecked(True)
            card.check.setAccessibleName(f"Include {adv.repo} in Fix selected")
            card.check.toggled.connect(lambda _=False: self._refresh())
            top.addWidget(card.check)
        title = QLabel(adv.headline)
        title.setWordWrap(True)
        title.setStyleSheet("font-weight:700; font-size:13px;")
        top.addWidget(title, stretch=1)
        lay.addLayout(top)

        body = QLabel(adv.explanation)
        body.setWordWrap(True)
        body.setStyleSheet("color:#3A3A3C; font-size:12px;")
        lay.addWidget(body)

        card.status = QLabel()
        card.status.setWordWrap(True)
        card.status.setVisible(False)
        lay.addWidget(card.status)

        if adv.action and adv.action_label:
            row = QHBoxLayout()
            row.addStretch(1)
            card.button = QPushButton(adv.action_label)
            card.button.setStyleSheet("font-weight:600;")
            card.button.clicked.connect(lambda _=False, i=idx: self._on_single(i))
            row.addWidget(card.button)
            lay.addLayout(row)
        return frame

    def _set_result(self, card, ok, text):
        card.status.setText(("✓  " if ok else "✗  ") + text)
        card.status.setStyleSheet(
            f"font-size:12px; font-weight:600; color:{'#1A7A3A' if ok else '#C0392B'};")
        card.status.setVisible(True)
        if ok:
            card.done = True
            card.frame.setStyleSheet(_DONE_STYLE)
            if card.check:
                card.check.setChecked(False)
                card.check.setEnabled(False)
            if card.button:
                card.button.setEnabled(False)
            if card.adv.action != "remove" and card.adv.repo not in self._fixed:
                self._fixed.append(card.adv.repo)
            return
        if card.check:                    # running it again would fail again
            card.check.setChecked(False)
        if card.button and card.adv.action == "pull":
            # Pull on its own explains a divergence and offers the rebase.
            card.button.setText("Resolve…")

    def _refresh(self):
        open_ = [c for c in self._cards if not c.done]
        total = len(self._cards)
        if not open_:
            self._head.setText(f"All {total} fixed. Sync them now to check, "
                               "or close.")
        elif len(open_) == total:
            self._head.setText(
                f"{total} repo(s) are not synced or not backed up. Each one below "
                "says why, and offers the fix where there is one. Tick several "
                "and use Fix selected to run them together.")
        else:
            self._head.setText(f"{len(open_)} of {total} still need attention.")

        running = self._thread is not None
        ticked = [c for c in self._cards if c.check and c.check.isChecked() and not c.done]
        self._fix_btn.setText(f"Fix selected ({len(ticked)})")
        self._fix_btn.setEnabled(bool(ticked) and not running)
        if self._select_all:
            avail = [c for c in self._cards if c.check and not c.done]
            self._select_all.setEnabled(bool(avail) and not running)
            self._select_all.blockSignals(True)
            self._select_all.setChecked(bool(avail) and len(ticked) == len(avail))
            self._select_all.blockSignals(False)
        self._sync_btn.setText(f"Sync fixed repos ({len(self._fixed)})")
        self._sync_btn.setVisible(bool(self._fixed) and self._sync_repos is not None)
        self._sync_btn.setEnabled(not running)
        self._sync_btn.setDefault(bool(self._fixed) and not open_)
        self._close_btn.setEnabled(not running)
        for c in self._cards:
            if c.button and not c.done:
                c.button.setEnabled(not running)

    def _on_select_all(self, checked):
        for c in self._cards:
            if c.check and not c.done:
                c.check.blockSignals(True)
                c.check.setChecked(checked)
                c.check.blockSignals(False)
        self._refresh()

    # ── Actions ───────────────────────────────────────────────────

    def _on_single(self, idx):
        """One fix that opens its own window: step aside while it runs."""
        card = self._cards[idx]
        self.hide()
        self._run_action(card.adv.action, card.adv.repo)
        if self._busy():
            self.accept()
            return
        self.show()
        self._recheck(card)
        self._refresh()

    def _recheck(self, card):
        """The handler reports its own outcome; the card only reflects it."""
        state = self._resolved(card.adv.action, card.adv.repo)
        if state is True:
            self._set_result(card, True, "Fixed.")
        elif state is None and card.adv.repo not in self._fixed:
            # Cannot tell from here (a triage may have been cancelled): offer
            # it for the re-sync, which says for certain, but keep it open.
            self._fixed.append(card.adv.repo)
            card.status.setText("Opened — sync it to check.")
            card.status.setStyleSheet("font-size:12px; color:#6E6E73;")
            card.status.setVisible(True)

    def confirm_text(self, cards) -> str:
        lines = []
        for key, verb in _VERBS.items():
            names = [c.adv.repo for c in cards if c.adv.action == key]
            if names:
                lines.append(f"{verb}:\n  " + "\n  ".join(names))
        return ("\n\n".join(lines)
                + "\n\nNothing local is discarded; a repo that has diverged is "
                  "reported and left alone. Remove touches only the list.")

    def _on_fix_selected(self):
        cards = [c for c in self._cards if c.check and c.check.isChecked() and not c.done]
        if not cards:
            return
        if QMessageBox.question(self, "Fix selected", self.confirm_text(cards),
                                QMessageBox.Cancel | QMessageBox.Yes,
                                QMessageBox.Yes) != QMessageBox.Yes:
            return
        self.start_batch(cards)

    def start_batch(self, cards):
        for c in cards:
            c.status.setText("…  working")
            c.status.setStyleSheet("font-size:12px; color:#6E6E73;")
            c.status.setVisible(True)
        self._results = []
        jobs = [(self._cards.index(c), c.adv.action, c.adv.repo) for c in cards]
        self._thread = QThread(self)
        self._worker = _FixWorker(jobs, self._fix_one)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.one_done.connect(self._on_one_done)
        self._worker.all_done.connect(self._on_all_done)
        self._thread.start()
        self._refresh()

    def _on_one_done(self, idx, ok, message):
        card = self._cards[idx]
        self._results.append((card.adv.action, card.adv.repo, ok, message))
        self._set_result(card, ok, message)
        self._refresh()

    def _on_all_done(self):
        # quit here, not by a signal: a queued quit would land after this
        # slot, and wait() would block forever.
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self._worker = None
        if self._after_fixes:
            self._after_fixes(list(self._results))
        self._refresh()

    def _on_sync_fixed(self):
        names = list(self._fixed)
        self.accept()
        self._sync_repos(names)

    def reject(self):
        if self._thread is not None:      # never abandon a running batch
            return
        super().reject()

    @property
    def fixed_repos(self) -> list[str]:
        return list(self._fixed)
