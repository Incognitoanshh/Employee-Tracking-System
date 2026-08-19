import os
import sys

from PySide6.QtGui     import QIcon
from PySide6.QtWidgets import QApplication

from client.presentation.windows.login_window import LoginWindow
from client.presentation.windows.employee_panel import EmployeePanel
from client.presentation.windows.admin_config_panel import AdminConfigPanel

from client.infrastructure.database.database import Database
from client.application.managers.auto_login_manager import AutoLoginManager
from client.application.managers.startup_manager import StartupManager
from client.core.config import APP_NAME
from client.single_instance import ensure_single_instance
ensure_single_instance()



def _warm_native_modules():
    """Force the heavy native libraries to load before any window is shown.

    BUG (Windows, reported as intermittent "Not Responding"): the app froze
    for up to a minute at login and once or twice during the first few
    minutes, then never again.

    Python imports and Windows DLL loading both hold the GIL. `import
    requests` alone pulls in 548 modules, and the first real HTTP call
    additionally loads the OpenSSL DLLs; the first screenshot loads
    Pillow's. In a PyInstaller --onefile build every one of those files is
    read out of a FRESH %TEMP% directory on each launch, so Defender scans
    them all over again each time. While that happens the GIL is held, the
    Qt event loop cannot run, and Windows paints the window as "Not
    Responding" — even though the worker thread is making progress, which
    is why login still succeeded.

    Once loaded they are cached, which is exactly why it stopped happening
    after a few minutes.

    Moving a background thread does NOT help: the GIL is held regardless of
    which thread does the importing. The fix is to pay the cost here, before
    there is a window to look frozen. A second or two of launch time is
    unremarkable; a frozen window mid-use is not.
    """
    try:
        import ssl
        # Builds the SSL context and loads libssl/libcrypto now rather than
        # inside the first login request.
        ssl.create_default_context()
    except Exception:
        pass
    try:
        import requests  # noqa: F401
        import urllib3   # noqa: F401
    except Exception:
        pass
    try:
        from PIL import Image
        # Touch the codec path so Pillow's native libraries load here and
        # not during the first screenshot capture.
        Image.new("RGB", (1, 1)).tobytes()
    except Exception:
        pass


def main():

    Database.initialize()

    # The chosen theme is read back before any window is built. Every widget
    # bakes its colours in when it is constructed, so this has to happen
    # first — after the settings table exists, and before the login window.
    from client.presentation.theme import load_saved_theme
    load_saved_theme()

    StartupManager.enable_autostart()

    app = QApplication(sys.argv)

    # AN EXCEPTION IN A SLOT MUST NOT TAKE THE WINDOW WITH IT.
    #
    # Qt calls Python slots from C++. When one raises, PySide prints the
    # traceback and — depending on the build — may abort the process: the
    # employee's app vanishes mid-shift with no window, no message, and
    # nothing said about tracking that was running. Screenshots stop. Nobody
    # finds out until somebody asks why a day is missing.
    #
    # This does not swallow the error. It writes it where support can read
    # it and lets the application carry on, which is the right trade for a
    # tool that is supposed to be running all day in the background.
    def _log_uncaught(kind, value, trace):
        if issubclass(kind, KeyboardInterrupt):
            sys.__excepthook__(kind, value, trace)
            return
        import traceback as _tb
        from client.services.logger_service import LoggerService
        detail = "".join(_tb.format_exception(kind, value, trace))
        try:
            LoggerService.log_verbose(f"Unhandled error: {detail}")
        except Exception:
            pass
        sys.__excepthook__(kind, value, trace)

    sys.excepthook = _log_uncaught

    # TOOLTIPS ARE STYLED HERE, NOT WITH THE REST OF THE PALETTE.
    #
    # A tooltip is its own top-level window, so a stylesheet set on a panel
    # never reaches it — it has to go on the application. And the application
    # does not exist yet when load_saved_theme() runs a few lines above, so
    # on a session where nobody touches the theme toggle the rule was never
    # applied at all: the tip fell through to the platform's, and on a Mac in
    # dark mode under a light-themed app that is a black box with dark text
    # in it. Reported, and this is the line that was missing.
    from client.presentation.theme import apply_tooltip_style
    apply_tooltip_style()

    # App identity — icon aur naam.
    #
    # Pehle kuch bhi set nahi tha, is liye Windows taskbar / macOS Dock me
    # PyInstaller ka default Python icon dikhta tha (peela saap + floppy).
    # Executable pe icon `--icon` flag se lagta hai, lekin CHALTE HUE app ka
    # icon Qt alag se leta hai — is liye dono jagah set karna zaroori hai.
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("Amaze Internet")

    # frozen build me assets/ bundle ke andar aata hai, dev me repo root se.
    if getattr(sys, "frozen", False):
        _assets = os.path.join(sys._MEIPASS, "assets")
    else:
        _assets = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets"
        )
    _icon = os.path.join(_assets, "icon.png")
    if os.path.exists(_icon):
        app.setWindowIcon(QIcon(_icon))

    app.setQuitOnLastWindowClosed(True)

    # Quit pe login/post-login workers ka intezaar — warna chal rahe
    # QThread destroy hone se app crash ke saath band hota hai.
    from client.presentation.windows.login_window import drain_login_workers
    app.aboutToQuit.connect(drain_login_workers)

    def drain_every_worker():
        """Wait for EVERY background thread, not only the login ones.

        THE CRASH THIS ENDS, and it is the one people actually saw:
        "Python quit unexpectedly", no traceback, nothing in the log.
        Qt aborts the process when a QThread is destroyed while it is still
        running, and these workers run a BLOCKING http request — quit() asks
        an event loop to stop and there is no event loop inside a blocking
        socket read, so a fetch cannot be interrupted at all. Close the app
        while one is in flight and the process dies on the way out.

        It needs an unreachable server to be common, which is exactly when it
        is worst: the network drops, every request sits waiting, and the app
        crashes the moment somebody gives up and quits.

        Bounded, because quitting must stay quick — four seconds is longer
        than a connection takes to fail and short enough that nobody waits.
        Whatever has not finished by then is handled by the exit below.
        """
        from PySide6.QtCore import QThread
        deadline = 4000
        for thread in list(QApplication.instance().findChildren(QThread)):
            try:
                if thread.isRunning():
                    thread.quit()
                    thread.wait(deadline)
            except RuntimeError:
                pass

    app.aboutToQuit.connect(drain_every_worker)


    _warm_native_modules()

    # AT LAUNCH, in front of whoever just opened the app — not at the first
    # scheduled capture, hours later, with nobody watching. See
    # services/screen_permission for what the old timing actually cost.
    from client.services.screen_permission import ensure_at_startup
    ensure_at_startup()

    auto_login_result = AutoLoginManager.try_auto_login()

    if auto_login_result:
        if auto_login_result["role"] in ("admin", "super_admin"):
            window = AdminConfigPanel()
        else:
            window = EmployeePanel()
    else:
        Database.cleanup_stale_sessions_and_shifts()
        window = LoginWindow()

    window.show()

    code = app.exec()

    # LEAVING WITHOUT LETTING Qt TEAR ITSELF DOWN, ON PURPOSE.
    #
    # The window is gone and the event loop has returned: everything after
    # this point is destructors. If any worker is STILL inside a blocking
    # request — the drain above waits, but a request can outlast it — Qt
    # destroys a running QThread and calls abort(), and the employee sees
    # "Python quit unexpectedly" as the last thing the app ever does. A clean
    # session ending in a crash dialog is not a clean session: it is a
    # support call, and it teaches people the app is unreliable at the exact
    # moment it finished working correctly.
    #
    # Nothing is lost by skipping teardown. Every database write here is a
    # short transaction committed by its context manager, and anything queued
    # for the server is already on disk in the outbox — that is what the
    # outbox is for. Logs are flushed first so nothing in flight is dropped.
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)


if __name__ == "__main__":

    main()