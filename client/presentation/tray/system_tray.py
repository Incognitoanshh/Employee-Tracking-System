from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from client.application.managers.session_manager     import SessionManager
from client.application.managers.session_log_manager import SessionLogManager
from client.application.managers.shift_manager       import ShiftManager
from client.application.services.auth_service        import AuthService
from client.services.logger_service                  import LoggerService

_STATUS_MAP = {
    "active":   ("#22c55e", "ETS — Tracking Active"),
    "idle":     ("#f59e0b", "ETS — User Idle"),
    "error":    ("#ef4444", "ETS — Upload Error"),
    "offshift": ("#64748b", "ETS — Off Shift"),
}


def _color_icon(hex_color: str, size: int = 16) -> QIcon:
    px = QPixmap(size, size)
    px.fill(QColor(hex_color))
    return QIcon(px)


class SystemTray(QSystemTrayIcon):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window    = parent
        self._current_status  = "active"
        self._apply_status("active")

        menu = QMenu()

        open_act = QAction("📊  Open Dashboard", menu)
        open_act.triggered.connect(self.show_dashboard)
        menu.addAction(open_act)

        logs_act = QAction("📋  View Logs", menu)
        logs_act.triggered.connect(self._open_logs)
        menu.addAction(logs_act)

        settings_act = QAction("⚙  Settings", menu)
        settings_act.triggered.connect(self._open_settings)
        menu.addAction(settings_act)

        menu.addSeparator()

        exit_act = QAction("🚪  Exit ETS", menu)
        exit_act.triggered.connect(self.exit_application)
        menu.addAction(exit_act)

        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def set_status(self, status: str):
        if status == self._current_status:
            return
        self._current_status = status
        self._apply_status(status)

    def show_message(self):
        self.showMessage(
            "ETS Running",
            "Tracking active in background.",
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )

    def _apply_status(self, status: str):
        color, tooltip = _STATUS_MAP.get(status, ("#22c55e", "ETS"))
        self.setIcon(_color_icon(color))
        self.setToolTip(tooltip)

    def _on_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_dashboard()

    def show_dashboard(self):
        if self.parent_window:
            self.parent_window.show()
            self.parent_window.raise_()
            self.parent_window.activateWindow()

    def _open_logs(self):
        try:
            from client.presentation.windows.logs_window import LogsWindow
            if not hasattr(self, "_logs_win") or not self._logs_win.isVisible():
                self._logs_win = LogsWindow()
            self._logs_win.show()
            self._logs_win.raise_()
        except Exception as e:
            LoggerService.log_error(f"Tray logs error: {e}")

    def _open_settings(self):
        try:
            from client.presentation.windows.settings_window import SettingsWindow
            if not hasattr(self, "_settings_win") \
                    or not self._settings_win.isVisible():
                self._settings_win = SettingsWindow()
            self._settings_win.show()
            self._settings_win.raise_()
        except Exception as e:
            LoggerService.log_error(f"Tray settings error: {e}")

    def exit_application(self):
        """✅ AuthService.logout() — proper cleanup."""
        try:
            AuthService.logout(SessionManager.session_id)
        except Exception as e:
            LoggerService.log_error(f"Tray logout error: {e}")

        try:
            SessionLogManager.end_session()
            ShiftManager.end_shift()
        except Exception:
            pass
        SessionManager.clear_session()
        LoggerService.log("LOGOUT via tray")
        QApplication.quit()