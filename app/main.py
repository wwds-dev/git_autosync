import sys

from PySide6.QtCore import QEvent, QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from .macos_dock import set_dock_icon_visible
from .macos_guard import install as install_exception_guard
from .style import STYLESHEET
from .ui_main import MainWindow

_SOCKET_NAME = "git_autosync_instance"


class _App(QApplication):
    """QApplication subclass that intercepts macOS Cmd+Q / Dock Quit.

    Overriding event() at the QApplication level is the only reliable way
    to catch these on macOS — an event filter on the app object receives
    the Close event before super().event() would call quit().
    """

    def __init__(self, *args):
        super().__init__(*args)
        self.allow_quit = False   # set True by the tray's own Quit action
        self._window: MainWindow | None = None
        # Guards against re-entering the hide below. Closing the window can
        # itself put a QEvent.Close on the application object, and acting on
        # that would call close() again on an already-hidden window.
        self._hiding_to_tray = False

    def event(self, e):
        # Cmd+Q and Dock -> Quit arrive as QEvent.Quit; the red button and some
        # window-manager paths arrive as QEvent.Close. Neither should terminate
        # a menu bar app: swallow them and hide instead, so the tray icon
        # survives. Only the tray's own Quit sets allow_quit and gets through.
        if e.type() in (QEvent.Quit, QEvent.Close) and not self.allow_quit:
            window = self._window
            tray = getattr(window, "_tray", None) if window else None
            if tray and tray.isVisible():
                # Through the window's own close path, not a hide repeated
                # here. `closeEvent` hides, drops the Dock tile, shows the
                # "still running in the menu bar" hint — and records
                # `hidden_to_tray`, which is what _DockActivateFilter reads.
                # Hiding directly left that flag False, so the window was gone
                # until the next activation (clicking the menu bar icon) put it
                # straight back: Cmd+Q looked like it had merely fronted the
                # app. The red button never had the bug because it goes through
                # closeEvent already.
                if not self._hiding_to_tray:
                    self._hiding_to_tray = True
                    try:
                        window.close()
                    finally:
                        self._hiding_to_tray = False
                e.ignore()
                return True   # suppress the quit — keep running in the tray
        return super().event(e)


class _DockActivateFilter(QObject):
    """Reopens the window when the macOS Dock icon is clicked while hidden.

    Only while the app still has a Dock icon. Once the window is hidden to the
    menu bar the app turns Accessory and has no Dock tile, so there is nothing
    to click — but activation still happens, e.g. the moment the tray icon is
    clicked. Acting on that re-opened the window behind the menu, which looked
    like the red button had done nothing and like Quit merely fronted the app.
    """

    def __init__(self, window: MainWindow):
        super().__init__()
        self._window = window

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.ApplicationActivate
                and not self._window.isVisible()
                and not getattr(self._window, "hidden_to_tray", False)):
            self._window.show()
            self._window.raise_()
            self._window.activateWindow()
            self._window.repaint()
        return False


def main():
    background = "--background" in sys.argv
    if background:
        sys.argv.remove("--background")
    # Before QApplication: NSApplication reads NSApplicationCrashOnExceptions
    # when it is created, and an ObjC exception escaping Qt's Cocoa plugin
    # otherwise aborts the process with nothing logged.
    guard_ok = install_exception_guard()
    # Recorded so a recurrence can be checked against a build that definitely
    # had the guard, instead of guessing which binary was running.
    try:
        from . import paths as _paths
        from datetime import datetime as _dt
        with open(_paths.user_log_dir() / "crash.log", "a") as _fh:
            _fh.write(f"[{_dt.now():%Y-%m-%d %H:%M:%S}] startup — "
                      f"exception guard {'active' if guard_ok else 'UNAVAILABLE'}\n")
    except Exception:
        pass
    app = _App(sys.argv)

    # Try to connect to an already-running instance.
    sock = QLocalSocket()
    sock.connectToServer(_SOCKET_NAME)
    if sock.waitForConnected(300):
        sock.write(b"background" if background else b"raise")
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        if not background:
            msg = QMessageBox()
            msg.setWindowTitle("git_autosync")
            msg.setText("git_autosync is already running.")
            msg.setInformativeText("The existing window has been brought to the front.")
            msg.setIcon(QMessageBox.Information)
            msg.exec()
        sys.exit(0)

    # Primary instance — claim the socket name and start listening.
    QLocalServer.removeServer(_SOCKET_NAME)
    server = QLocalServer()
    server.listen(_SOCKET_NAME)

    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    app._window = window

    if background:
        # Started at login: live in the menu bar only. Without this the app is
        # still a Regular app, so it keeps a Dock tile and macOS can bring it
        # forward during login even though no window was shown.
        set_dock_icon_visible(False)
        # And it is hidden to the tray in exactly the sense the filter means,
        # even though no window was ever shown to hide. Leaving the flag False
        # let the first activation — a click on the menu bar icon — open the
        # window a login start had deliberately not opened.
        window.hidden_to_tray = True
    else:
        window.show()

    def _on_new_connection():
        conn = server.nextPendingConnection()
        conn.waitForReadyRead(300)
        if bytes(conn.readAll()) == b"background":
            return
        set_dock_icon_visible(True)   # first: an accessory app can't take focus
        window.hidden_to_tray = False
        window.show()
        window.raise_()
        window.activateWindow()

    server.newConnection.connect(_on_new_connection)

    dock_filter = _DockActivateFilter(window)
    app.installEventFilter(dock_filter)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
