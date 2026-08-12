"""
Ask macOS for Screen Recording at LAUNCH, not at capture time.

REPORTED FROM A REAL MAC: "jab bhi app first time launch ho tabhi permission
mang le, ye ni ki jab screenshot lene ka time aaye tab permission and quit and
reopen ho — abhi same hua, permission manga, quit and reopen kiya to ss to
gayab".

What was happening. macOS gates screen capture behind Screen Recording, and
the prompt appears the first time an application actually tries to read the
screen. For us that was the first scheduled capture — some random minute
during the shift. Worse, macOS only lets a process see the screen from its
NEXT launch after the permission is granted, so that capture and every one
scheduled before the person happened to quit and reopen were lost, silently:
`CGWindowListCreateImage` does not fail, it returns the desktop picture with
no windows in it. A wallpaper is indistinguishable from "tracking is working"
until somebody opens the screenshots and sees nothing.

So we ask at startup, before the login window, while somebody is sitting in
front of the machine and can answer — and we ask through CoreGraphics rather
than by taking a throwaway screenshot, because the two calls for exactly this
exist:

    CGPreflightScreenCaptureAccess()   has it been granted? (no prompt)
    CGRequestScreenCaptureAccess()     ask, once, showing the system prompt

Windows and Linux need no permission for this and are left alone.
"""

import ctypes
import sys

from client.services.logger_service import LoggerService

_CG = ("/System/Library/Frameworks/CoreGraphics.framework/"
       "Versions/A/CoreGraphics")


def is_macos() -> bool:
    return sys.platform == "darwin"


def has_screen_access() -> bool:
    """True when this machine will hand us real pixels.

    Anything other than a clear "no" counts as yes: on a macOS too old to
    have the check — the permission itself arrived in Catalina — capture has
    always simply worked, and refusing to run there would be inventing a
    problem.
    """
    if not is_macos():
        return True
    try:
        cg = ctypes.cdll.LoadLibrary(_CG)
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        return bool(cg.CGPreflightScreenCaptureAccess())
    except Exception:
        return True


def request_screen_access() -> bool:
    """Show the system prompt. Returns what it was before the asking.

    macOS shows this once per application; afterwards the call returns
    immediately and the person has to go to System Settings themselves,
    which is what the caller tells them.
    """
    if not is_macos():
        return True
    try:
        cg = ctypes.cdll.LoadLibrary(_CG)
        cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
        return bool(cg.CGRequestScreenCaptureAccess())
    except Exception:
        return True


def ensure_at_startup(parent=None) -> bool:
    """Called once, from main(), before any window.

    Returns True when captures will work. When they will not, it says so in
    plain words and opens the right pane of System Settings — being told
    "screenshots are not being taken" now is worth far more than finding out
    at the end of a shift.
    """
    if not is_macos() or has_screen_access():
        return True

    granted = request_screen_access()
    if granted and has_screen_access():
        return True

    LoggerService.log(
        "SCREEN RECORDING : permission not granted — captures will be blank "
        "until it is allowed and the app is restarted")

    try:
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Screen Recording permission needed")
        box.setText("Amaze Connect cannot record the screen yet.")
        box.setInformativeText(
            "Open System Settings → Privacy & Security → Screen Recording, "
            "switch on Amaze Connect, then quit and open the app again.\n\n"
            "Until that is done, screenshots will be empty.")
        open_settings = box.addButton("Open System Settings",
                                      QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_settings:
            import subprocess
            subprocess.Popen([
                "open",
                "x-apple.systempreferences:com.apple.preference.security"
                "?Privacy_ScreenCapture"])
    except Exception:
        pass

    return False
