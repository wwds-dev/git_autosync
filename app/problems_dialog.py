"""Shows what went wrong in a run, and the button that fixes each one."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)


class ProblemsDialog(QDialog):
    def __init__(self, parent, advices, run_action):
        """`run_action(key, repo_name)` performs one fix; the dialog closes
        first so the handler can open its own window."""
        super().__init__(parent)
        self.setWindowTitle("Some repos need attention")
        self.setMinimumWidth(620)
        self._run_action = run_action

        outer = QVBoxLayout(self)
        outer.setSpacing(10)

        head = QLabel(f"{len(advices)} repo(s) did not sync. Each one below says "
                      "why, and offers the fix where there is one.")
        head.setWordWrap(True)
        outer.addWidget(head)

        body = QWidget()
        body.setObjectName("problemsBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(10)

        for adv in advices:
            lay.addWidget(self._card(adv))
        lay.addStretch(1)

        body.setStyleSheet(
            "#problemsBody { background:#FFFFFF; }"
            "#problemsBody QLabel { color:#1D1D1F; background:transparent; }")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        scroll.setMinimumHeight(320)
        scroll.setStyleSheet(
            "QScrollArea { background:#FFFFFF; border:1px solid #E5E5EA;"
            " border-radius:8px; }"
            "QScrollArea > QWidget > QWidget { background:#FFFFFF; }")
        outer.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.reject)
        outer.addWidget(buttons)

    def _card(self, adv):
        card = QFrame()
        card.setFrameShape(QFrame.NoFrame)
        card.setStyleSheet(
            "QFrame { background:#FBFBFD; border:1px solid #E5E5EA;"
            " border-radius:8px; }")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(6)

        title = QLabel(adv.headline)
        title.setWordWrap(True)
        title.setStyleSheet("font-weight:700; font-size:13px; border:none;")
        lay.addWidget(title)

        body = QLabel(adv.explanation)
        body.setWordWrap(True)
        body.setStyleSheet("color:#3A3A3C; font-size:12px; border:none;")
        lay.addWidget(body)

        if adv.action and adv.action_label:
            row = QHBoxLayout()
            row.addStretch(1)
            btn = QPushButton(adv.action_label)
            btn.setStyleSheet("font-weight:600;")
            # Close first: several handlers open a window of their own, and a
            # dialog stacked on this one is hard to get out of.
            btn.clicked.connect(
                lambda _=False, a=adv: (self.accept(),
                                        self._run_action(a.action, a.repo)))
            row.addWidget(btn)
            lay.addLayout(row)
        return card
