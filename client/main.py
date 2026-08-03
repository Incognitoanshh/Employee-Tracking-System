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



def main():

    Database.initialize()

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