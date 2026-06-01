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
            self.parent_window.windowIcon()
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

    def exit_application(
        self
    ):
        ShiftManager.end_shift()

        QApplication.quit()

    def show_message(
        self
    ):

        self.showMessage(

            "ETS Running",

            "Tracking active in background."

        )