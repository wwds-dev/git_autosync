"""The non-activating-panel experiment, kept as a record of what was tried.

Applying NSWindowStyleMaskNonactivatingPanel to the tray popup does work as a
window-level change — Qt backs the popup with a QNSPanel and the mask sticks.
It is *not* enough to make the menu usable: with the app left inactive the menu
opens but its items do not respond, confirmed on hardware.

The test asserts only the window-level fact, so the finding is not lost if
someone tries this again. The app does not use it; see _popup_tray_menu for why
activation is there instead.

Run with:
  QT_QPA_PLATFORM=cocoa GUI_TESTS=1 .venv/bin/python -m pytest tests/test_tray_panel.py
"""
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin"
    or os.environ.get("GUI_TESTS") != "1"
    or os.environ.get("QT_QPA_PLATFORM") == "offscreen",
    reason="opt-in: needs a real window server (GUI_TESTS=1 QT_QPA_PLATFORM=cocoa)",
)


def test_qt_popup_window_accepts_the_nonactivating_style():
    """Documents that the style applies, and that Qt recreates the native
    window on show — so styling before showing is pointless."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QMenu
    from app.macos_dock import make_window_nonactivating

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    for label in ("Open git_autosync", "Quit"):
        menu.addAction(label)

    before_show = int(menu.winId())
    menu.popup(QPoint(400, 400))
    for _ in range(5):
        app.processEvents()
    after_show = int(menu.winId())

    result = make_window_nonactivating(after_show)
    menu.hide()

    assert "QNSPanel" in result, f"Qt changed the popup's window class: {result}"
    assert "nonactivating ON" in result, result
    assert before_show != after_show, (
        "Qt used to recreate the popup's native window on show; if that stopped "
        "being true, styling it earlier becomes possible")
