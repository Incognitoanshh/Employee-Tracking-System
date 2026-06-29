"""
SystemTray — Fixed version
===========================
Bugs fixed:
    1. Icon status update — green/amber/red based on tracking state
    2. Double-click on macOS (DoubleClick + Trigger both handled)
    3. showMessage() proper args (title, msg, icon, msecs)
    4. Dynamic tooltip reflects current state
    5. "View Logs" + "Settings" menu items added
    """

from __future__ import annotations

import requests
from PySide6.QtCore    import QTimer
from PySide6.QtGui     import QAction, QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMenu, QStyle, QSystemTrayIcon,
)

from client.core.config                              import API_BASE_URL
from client.application.managers.session_manager    import SessionManager
from client.application.managers.session_log_manager import SessionLogManager
from client.application.managers.shift_manager      import ShiftManager
from client.services.logger_service                 import LoggerService


def _color_icon(hex_color: str, size: int = 16) -> QIcon:
    """Solid color square icon — works without any image files."""
    px = QPixmap(size, size)
    px.fill(QColor(hex_color))
    return QIcon(px)


# Status → (hex color, tooltip text)
_STATUS_MAP = {
    "active":    ("#22c55e", "ETS — Tracking Active"),
    "idle":      ("#f59e0b", "ETS — User Idle"),
    "error":     ("#ef4444", "ETS — Upload Error"),
    "offshift":  ("#64748b", "ETS — Off Shift"),
}


class SystemTray(QSystemTrayIcon):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self._current_status = "active"

        # Default icon
        self._apply_status("active")

        # ── Menu ────────────────────────────────────────────
        tray_menu = QMenu()

        open_action = QAction("📊  Open Dashboard", tray_menu)
        open_action.triggered.connect(self.show_dashboard)
        tray_menu.addAction(open_action)

        logs_action = QAction("📋  View Logs", tray_menu)
        logs_action.triggered.connect(self._open_logs)
        tray_menu.addAction(logs_action)

        settings_action = QAction("⚙  Settings", tray_menu)
        settings_action.triggered.connect(self._open_settings)
        tray_menu.addAction(settings_action)

        tray_menu.addSeparator()

        exit_action = QAction("🚪  Exit ETS", tray_menu)
        exit_action.triggered.connect(self.exit_application)
        tray_menu.addAction(exit_action)

        self.setContextMenu(tray_menu)

        # ── Click handling ───────────────────────────────────
        # Both Trigger (single) and DoubleClick — macOS uses DoubleClick
        self.activated.connect(self._on_activated)

        # ── Public API ───────────────────────────────────────────

    def set_status(self, status: str):
        """
        Call this from Dashboard/IdleTracker to update icon.
        status: 'active' | 'idle' | 'error' | 'offshift'
        """
        if status == self._current_status:
            return
        self._current_status = status
        self._apply_status(status)

    def show_message(self):
        """Show startup notification."""
        self.showMessage(
            "ETS Running",
            "Tracking active in background.",
            QSystemTrayIcon.MessageIcon.Information,
            3000,   # 3 seconds
        )

        # ── Private helpers ──────────────────────────────────────

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
            print("[TRAY LOGS ERROR]", e)

    def _open_settings(self):
        try:
            from client.presentation.windows.settings_window import SettingsWindow
            if not hasattr(self, "_settings_win") or not self._settings_win.isVisible():
                self._settings_win = SettingsWindow()
            self._settings_win.show()
            self._settings_win.raise_()
        except Exception as e:
            print("[TRAY SETTINGS ERROR]", e)

    def exit_application(self):
        try:
            requests.post(
                f"{API_BASE_URL}/auth/logout",
                headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                timeout=5,
            )
        except Exception as error:
            print("[LOGOUT API FAILED]", error)

        LoggerService.log("LOGOUT")
        SessionLogManager.end_session()
        ShiftManager.end_shift()
        SessionManager.clear_session()
        print("[EXIT] QApplication.quit()")
        QApplication.quit()
