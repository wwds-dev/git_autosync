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
