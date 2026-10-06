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


def test_repo_rows_are_actually_banded_on_screen(window):
    """Striping the RepoRow widget looked right when the widget was grabbed in
    isolation but showed nothing in the list: an item widget sits on the
    viewport and the item's own background is painted over it. Measure what the
    viewport composites, not what the row reports."""
    from collections import Counter

    app = QtWidgets.QApplication.instance()
    window.resize(1200, 700)
    for _ in range(6):
        app.processEvents()
    rows = list(window._row_widgets.values())
    if len(rows) < 2:
        pytest.skip("needs at least two repos")
    img = window.repo_list.viewport().grab().toImage()
    h = rows[0].height()

    def band(i):
        y = i * h + h // 2
        px = [img.pixelColor(x, y) for x in range(0, img.width(), 8)]
        return Counter((p.red(), p.green(), p.blue()) for p in px).most_common(1)[0][0]

    first, second = band(0), band(1)
    assert first != second, f"rows are not banded: both {first}"


def test_behind_state_survives_a_row_rebuild(window):
    """Pulling one repo rebuilt every row, which cleared every other Pull
    button and forced another dry-run just to get them back."""
    app = QtWidgets.QApplication.instance()
    names = list(window._row_widgets)[:3]
    if len(names) < 3:
        pytest.skip("needs at least three repos")
    for n in names:
        window._behind.add(n)
        window._row_widgets[n].set_behind(True)

    window._reload_repo_list()
    for _ in range(4):
        app.processEvents()

    still = [n for n in names
             if n in window._row_widgets and window._row_widgets[n].pull_btn.isVisible()]
    assert len(still) == len(names), f"lost Pull on {set(names) - set(still)}"
