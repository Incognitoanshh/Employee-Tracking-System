import sys

from PySide6.QtWidgets import QApplication

from client.presentation.windows.login_window import LoginWindow
from client.presentation.windows.dashboard_window import DashboardWindow
from client.presentation.windows.admin_config_panel import AdminConfigPanel

from client.infrastructure.database.database import Database
from client.application.managers.auto_login_manager import AutoLoginManager
from client.application.managers.startup_manager import StartupManager
from client.single_instance import ensure_single_instance
ensure_single_instance()



def main():

    Database.initialize()

    StartupManager.enable_autostart()

    app = QApplication(sys.argv)

    app.setQuitOnLastWindowClosed(True)


    auto_login_result = AutoLoginManager.try_auto_login()

    if auto_login_result:
        if auto_login_result["role"] == "admin":
            window = AdminConfigPanel()
        else:
            window = DashboardWindow()
    else:
        Database.cleanup_stale_sessions_and_shifts()
        window = LoginWindow()

    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":

    main()