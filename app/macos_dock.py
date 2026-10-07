"""Show or hide the macOS dock tile by switching the app's activation policy.

Qt does not expose NSApplicationActivationPolicy, so this goes straight to the
Objective-C runtime through ctypes. That keeps it dependency-free — PyObjC is
deliberately not in requirements.txt, and an `import AppKit` here would simply
raise and do nothing.

Hiding the window is only half of a menu-bar-app quit: without the policy
switch the dock tile stays behind and the app looks like it failed to close.
NSStatusItem survives the switch, so the menu-bar icon stays put.
"""
import ctypes
import ctypes.util

_NS_ACTIVATION_POLICY_REGULAR = 0
_NS_ACTIVATION_POLICY_ACCESSORY = 1

try:
    _objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    _objc.objc_getClass.restype = ctypes.c_void_p
    _objc.objc_getClass.argtypes = [ctypes.c_char_p]
    _objc.sel_registerName.restype = ctypes.c_void_p
    _objc.sel_registerName.argtypes = [ctypes.c_char_p]
except Exception:
    _objc = None


def activate_app() -> bool:
    """Bring this app forward, ignoring which app is currently active.

    A menu bar app is normally not the active app, and macOS gives an inactive
    app's first click to activation rather than to the control under the
    cursor — so the tray menu never opens without this.
    """
    if _objc is None:
        return False
    try:
        send_id = ctypes.cast(
            _objc.objc_msgSend,
            ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p))
        send_activate = ctypes.cast(
            _objc.objc_msgSend,
            ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool))
        ns_app = send_id(_objc.objc_getClass(b"NSApplication"),
                         _objc.sel_registerName(b"sharedApplication"))
        if not ns_app:
            return False
        send_activate(ns_app,
                      _objc.sel_registerName(b"activateIgnoringOtherApps:"), True)
        return True
    except Exception:
        return False


# NSWindowStyleMaskNonactivatingPanel. A panel carrying this takes mouse
# events while its application is inactive, which is the whole point: the tray
# menu can then be clicked without activating the app and dragging its window
# forward.
_NS_NONACTIVATING_PANEL = 1 << 7
_NS_POPUP_MENU_LEVEL = 101


def make_window_nonactivating(win_id: int) -> str:
    """Turn a Qt popup's backing window into a non-activating panel.

    `win_id` is what QWidget.winId() returns on macOS: an NSView*. Returns a
    short description of what happened, for logging — this depends on Qt's
    private window class, so it must fail visibly rather than silently.
    """
    if _objc is None or not win_id:
        return "objc unavailable"
    try:
        send_id = ctypes.cast(
            _objc.objc_msgSend,
            ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p))
        send_mask = ctypes.cast(
            _objc.objc_msgSend,
            ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p))
        set_mask = ctypes.cast(
            _objc.objc_msgSend,
            ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong))
        set_level = ctypes.cast(
            _objc.objc_msgSend,
            ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long))

        window = send_id(ctypes.c_void_p(win_id), _objc.sel_registerName(b"window"))
        if not window:
            return "no backing NSWindow yet"

        _objc.object_getClassName.restype = ctypes.c_char_p
        _objc.object_getClassName.argtypes = [ctypes.c_void_p]
        cls = _objc.object_getClassName(ctypes.c_void_p(window)).decode()

        before = send_mask(window, _objc.sel_registerName(b"styleMask"))
        set_mask(window, _objc.sel_registerName(b"setStyleMask:"),
                 before | _NS_NONACTIVATING_PANEL)
        set_level(window, _objc.sel_registerName(b"setLevel:"), _NS_POPUP_MENU_LEVEL)
        after = send_mask(window, _objc.sel_registerName(b"styleMask"))

        applied = bool(after & _NS_NONACTIVATING_PANEL)
        return (f"{cls}: styleMask {before} -> {after}, "
                f"nonactivating {'ON' if applied else 'REFUSED'}")
    except Exception as exc:
        return f"failed: {exc}"


def set_dock_icon_visible(visible: bool) -> bool:
    """Show or hide the dock tile. True if the policy was applied."""
    if _objc is None:
        return False
    try:
        # objc_msgSend needs a distinct prototype per signature, so cast twice.
        send_id = ctypes.cast(
            _objc.objc_msgSend,
            ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p))
        send_policy = ctypes.cast(
            _objc.objc_msgSend,
            ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_long))
        ns_app = send_id(_objc.objc_getClass(b"NSApplication"),
                         _objc.sel_registerName(b"sharedApplication"))
        if not ns_app:
            return False
        policy = (_NS_ACTIVATION_POLICY_REGULAR if visible
                  else _NS_ACTIVATION_POLICY_ACCESSORY)
        return bool(send_policy(
            ns_app, _objc.sel_registerName(b"setActivationPolicy:"), policy))
    except Exception:
        return False
