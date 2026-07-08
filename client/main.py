import sys

from PySide6.QtWidgets import QApplication

from client.presentation.windows.login_window import LoginWindow
from client.presentation.windows.dashboard_window import DashboardWindow
from client.presentation.windows.admin_config_panel import AdminConfigPanel

from client.infrastructure.database.database import Database
from client.application.managers.auto_login_manager import AutoLoginManager
from client.application.managers.startup_manager import StartupManager


def main():

    Database.initialize()

    # OS boot/login pe app khud-ba-khud chale — sirf packaged (frozen) exe
    # ke liye register hota hai, dev mode me no-op hai.
    StartupManager.enable_autostart()

    app = QApplication(sys.argv)

    app.setQuitOnLastWindowClosed(True)

    # Restart/reboot ke baad manual login se bachne ke liye — saved session
    # valid ho to seedha dashboard/admin panel khol do, warna normal login.
    # IMPORTANT: stale-session cleanup (naam ki ACTIVE rows ko CLOSED karna)
    # auto-login attempt se PEHLE nahi chalana — warna wahi row jo auto-login
    # ko chahiye, uske read hone se pehle hi close ho jayegi.
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