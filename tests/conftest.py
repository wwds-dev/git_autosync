"""The application object every Qt test shares.

`_App` rather than a plain QApplication, because the Cmd+Q / Dock-Quit path
being tested *is* `_App.event()` — and only one QApplication can exist per
process, so it has to be the one the first fixture creates.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from app.main import _App

    existing = QtWidgets.QApplication.instance()
    if existing is not None:
        yield existing
        return
    app = _App([])
    yield app
