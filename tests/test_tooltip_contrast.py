"""Tooltips must be readable: dark text on the system's dark tooltip surface
was not (owner screenshot, 2026-10-20)."""
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QToolTip

from app.style import STYLESHEET


def _luma(c) -> float:
    return 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()


def test_a_tooltip_is_drawn_dark_on_light(qapp):
    # macOS in Dark Mode hands Qt a dark tooltip surface; offscreen does not,
    # so give it one, or this test passes with or without the fix.
    dark = QPalette(QToolTip.palette())
    for role in (QPalette.ToolTipBase, QPalette.Window, QPalette.Base):
        dark.setColor(role, QColor("#5A5A5A"))
    QToolTip.setPalette(dark)
    qapp.setStyleSheet(STYLESHEET)
    anchor = QLabel("x")
    anchor.show()
    QApplication.processEvents()
    QToolTip.showText(QPoint(10, 10), "Last commit: 2026-10-20 01:47", anchor)
    for _ in range(20):
        QApplication.processEvents()
    tip = next(w for w in QApplication.topLevelWidgets()
               if w.metaObject().className() == "QTipLabel")
    img = tip.grab().toImage()
    bg = img.pixelColor(3, img.height() // 2)
    text = tip.palette().color(tip.foregroundRole())
    QToolTip.hideText()
    assert _luma(bg) > 0.9, f"tooltip background is dark: {bg.name()}"
    assert _luma(bg) - _luma(text) > 0.6, f"text {text.name()} on {bg.name()}"
