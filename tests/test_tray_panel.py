"""The tray popup must take clicks while the app is inactive.

Activating the app is what drags the main window forward, so the menu cannot
rely on it. Qt backs a QMenu popup with a QNSPanel, and only a panel carrying
NSWindowStyleMaskNonactivatingPanel receives mouse events with its application
in the background.

This needs the real cocoa platform: offscreen has no NSWindow to style, so the
check would pass vacuously. Deliberately not forcing QT_QPA_PLATFORM here —
every other GUI test file sets offscreen, and that is what made the first
version of this test skip itself always.
"""
import os
import sys

import pytest

# conftest.py pins QT_QPA_PLATFORM=offscreen for the whole suite, which is
# right for CI but leaves no NSWindow to style — this check would pass without
# testing anything. So it is opt-in, and the command is written down:
#
#   QT_QPA_PLATFORM=cocoa GUI_TESTS=1 .venv/bin/python -m pytest tests/test_tray_panel.py
pytestmark = pytest.mark.skipif(
    sys.platform != "darwin"
    or os.environ.get("GUI_TESTS") != "1"
    or os.environ.get("QT_QPA_PLATFORM") == "offscreen",
    reason="opt-in: needs a real window server (GUI_TESTS=1 QT_QPA_PLATFORM=cocoa)",
)


def test_qt_popup_accepts_the_nonactivating_panel_style():
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QMenu
    from app.macos_dock import make_window_nonactivating

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    for label in ("Open git_autosync", "Dry-run", "Sync now", "Quit"):
        menu.addAction(label)
    menu.popup(QPoint(400, 400))
    for _ in range(5):
        app.processEvents()

    result = make_window_nonactivating(int(menu.winId()))
    menu.hide()
    assert "nonactivating ON" in result, result
    assert "QNSPanel" in result, f"Qt changed the popup's window class: {result}"
