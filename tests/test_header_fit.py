"""The list header must not clip its own labels.

'LAST SYNCED' needed 83px in a 72px column and rendered as 'AST SYNCED'.
Column widths are constants, so a font or wording change can silently
re-break this; measure it instead of eyeballing a screenshot.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def window():
    from app.ui_main import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = MainWindow()
    win.show()
    for _ in range(4):
        app.processEvents()
    yield win
    win._tray = None
    win.close()


def test_header_labels_are_not_clipped(window):
    clipped = []
    for label in window._list_header.findChildren(QtWidgets.QLabel):
        needed = label.fontMetrics().horizontalAdvance(label.text().upper())
        if label.width() and needed > label.width():
            clipped.append((label.text(), label.width(), needed))
    assert not clipped, f"header labels clipped: {clipped}"


def test_tray_menu_text_contrasts_with_its_background():
    """The global `QWidget { color: #1D1D1F }` rule also hits popup menus, which
    macOS draws on the system surface — dark in Dark Mode, giving 1.3:1 and an
    unreadable menu. The stylesheet must paint the menu's own background."""
    from collections import Counter
    from PySide6.QtGui import QColor
    from app.style import STYLESHEET

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setStyleSheet(STYLESHEET)
    menu = QtWidgets.QMenu()
    for label in ("Open git_autosync", "Dry-run", "Sync now", "Quit"):
        menu.addAction(label)
    menu.show()
    menu.resize(menu.sizeHint())
    for _ in range(4):
        app.processEvents()
    img = menu.grab().toImage()
    menu.hide()

    def luminance(c):
        def chan(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126*chan(c.red()) + 0.7152*chan(c.green()) + 0.0722*chan(c.blue())

    px = [img.pixelColor(x, y)
          for y in range(img.height()) for x in range(0, img.width(), 2)]
    common = Counter((p.red(), p.green(), p.blue()) for p in px).most_common(4)
    bg = common[0][0]
    fg = max((p for p, _ in common[1:]), key=lambda t: abs(sum(t) - sum(bg)))
    hi, lo = sorted([luminance(QColor(*bg)), luminance(QColor(*fg))], reverse=True)
    ratio = (hi + 0.05) / (lo + 0.05)
    assert ratio >= 4.5, f"menu text contrast {ratio:.1f}:1 on bg={bg}, fg={fg}"
