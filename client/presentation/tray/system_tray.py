import requests
from PySide6.QtWidgets import QStyle
from client.services.logger_service import LoggerService
from client.application.managers.session_manager import SessionManager
from client.application.managers.session_log_manager import SessionLogManager
from PySide6.QtWidgets import (
    QSystemTrayIcon,
    QMenu,
    QApplication
)

from PySide6.QtGui import (
    QAction,
    QIcon
)

from client.application.managers.shift_manager import ShiftManager


class SystemTray(QSystemTrayIcon):

    def __init__(
        self,
        parent=None
    ):

        super().__init__(parent)

        self.parent_window = parent

        self.setIcon(
            QApplication.style().standardIcon(
                QStyle.SP_ComputerIcon
            )   
        )

        self.setToolTip(
            "ETS Tracking Active"
        )

        tray_menu = QMenu()

        open_action = QAction(
            "Open Dashboard"
        )

        exit_action = QAction(
            "Exit ETS"
        )

        open_action.triggered.connect(
            self.show_dashboard
        )

        exit_action.triggered.connect(
            self.exit_application
        )

        tray_menu.addAction(
            open_action
        )

        tray_menu.addSeparator()

        tray_menu.addAction(
            exit_action
        )

        self.setContextMenu(
            tray_menu
        )

        self.activated.connect(
            self.tray_clicked
        )

    def tray_clicked(
        self,
        reason
    ):

        if reason == QSystemTrayIcon.Trigger:

            self.show_dashboard()

    def show_dashboard(
        self
    ):

        self.parent_window.show()

        self.parent_window.raise_()

        self.parent_window.activateWindow()

    def exit_application(self):

        try:

            requests.post(
                "http://localhost:8000/api/auth/logout",
                headers={
                    "Authorization":
                        f"Bearer {SessionManager.auth_token}"
                    },
                    timeout=5
            )
        except Exception as error:

            print("[LOGOUT API FAILED]", error)

        print("[EXIT STARTED]")

        LoggerService.log("LOGOUT")

        print("[SESSION END CALLED]")

        SessionLogManager.end_session()

        print("[SHIFT END CALLED]")

        ShiftManager.end_shift()

        SessionManager.clear_session()
        print("QUITTING APP")
        QApplication.quit()

    def show_message(
        self
    ):

        self.showMessage(

            "ETS Running",

            "Tracking active in background."

        )