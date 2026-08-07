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


    _warm_native_modules()

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

    sys.exit(app.exec())


if __name__ == "__main__":

    main()