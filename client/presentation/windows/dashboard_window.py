from datetime import datetime
import threading

import requests

from PySide6.QtWidgets import (
    QListWidget, QListWidgetItem, QLabel,
    QPushButton, QGridLayout, QHBoxLayout,
    QVBoxLayout, QFrame,
)
from PySide6.QtCore import QTimer, Qt, QMetaObject, Slot
from PySide6.QtGui import QCursor, QColor

from client.core.config import API_BASE_URL
from client.application.managers.session_log_manager import SessionLogManager
from client.application.managers.session_manager import SessionManager
from client.application.managers.shift_manager import ShiftManager
from client.application.managers.sync_manager import SyncManager
from client.application.managers.screenshot_manager import ScreenshotManager
from client.application.managers.idle_tracker import IdleTracker
from client.application.schedulers.scheduler_service import SchedulerService
from client.application.services.auth_service import AuthService
from client.infrastructure.database.database import Database
from client.presentation.windows.admin_config_panel import AdminConfigPanel
from client.presentation.windows.base_window import BaseWindow
from client.presentation.widgets.status_card import StatusCard
from client.presentation.windows.logs_window import LogsWindow
from client.presentation.windows.settings_window import SettingsWindow
from client.presentation.windows.attendance_window import AttendanceWindow
from client.presentation.tray.system_tray import SystemTray
from client.services.logger_service import LoggerService


class DashboardWindow(BaseWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ETS Dashboard")
        self.resize(1100, 720)

        # Shift timer cache — DB hit sirf ek baar
        self._shift_login_time: datetime | None = None
        self._load_shift_login_time()

        self.setup_ui()
        self._start_timers()

        # ── Setup ─────────────────────────────────────────────────────────────

    def _start_timers(self):
        # Network check — 30s (server ping, not google)
        self.network_timer = QTimer()
        self.network_timer.timeout.connect(self.check_network_status)
        self.network_timer.start(30_000)
        self.check_network_status()

        # Shift duration display — 1s, no DB
        self.shift_timer = QTimer()
        self.shift_timer.timeout.connect(self.update_shift_timer)
        self.shift_timer.start(1000)

        # Dashboard refresh — 30s
        self.dashboard_refresh_timer = QTimer()
        self.dashboard_refresh_timer.timeout.connect(self.refresh_dashboard)
        self.dashboard_refresh_timer.start(30_000)

        # Tray
        self.tray = SystemTray(self)
        self.tray.show()

        # Services
        self.scheduler = SchedulerService()
        self.scheduler.screenshot_triggered.connect(self.capture_screenshot)
        self.scheduler.force_logout.connect(self.logout)
        self.scheduler.start()

        self.idle_tracker = IdleTracker()
        self.idle_tracker.status_changed.connect(self.update_idle_status)
        self.idle_tracker.start()

        # Deferred loads — UI render ke baad
        QTimer.singleShot(1000, self.load_dashboard_stats)
        QTimer.singleShot(2000, self.load_recent_logs)

    def _load_shift_login_time(self):
        try:
            with Database.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT login_time FROM shifts
                    WHERE employee_id = ? AND status = 'ACTIVE'
                    ORDER BY id DESC LIMIT 1
                    """,
                    (SessionManager.employee_id,),
                )
                shift = cursor.fetchone()
            if shift:
                login_time_str = shift["login_time"]
                try:
                    # Try ISO8601 first (with timezone)
                    self._shift_login_time = datetime.fromisoformat(login_time_str)
                except ValueError:
                    # Fallback to simple format
                    self._shift_login_time = datetime.strptime(
                        login_time_str, "%Y-%m-%d %H:%M:%S"
                    )
        except Exception as e:
            LoggerService.log_error(f"DashboardWindow shift time load error: {e}")

    def setup_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(28, 22, 28, 20)
        main_layout.setSpacing(0)

        # Header
        header_layout = QHBoxLayout()
        title = QLabel("ETS Control Center")
        title.setStyleSheet(
            "font-size: 26px; font-weight: 700; color: #f1f5f9;"
            )

        employee_container = QVBoxLayout()
        employee_label = QLabel(
            f"👤  {SessionManager.full_name or SessionManager.employee_id}"
        )
        employee_label.setStyleSheet("color: #94a3b8; font-size: 13px;")
        self.status_label = QLabel("🟢  ONLINE")
        self.status_label.setStyleSheet(
            "color: #22c55e; font-size: 12px; font-weight: bold;"
            )
        employee_container.addWidget(
            employee_label, alignment=Qt.AlignmentFlag.AlignRight
        )
        employee_container.addWidget(
            self.status_label, alignment=Qt.AlignmentFlag.AlignRight
        )
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addLayout(employee_container)
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(
            "background-color: #1e2d3d; margin: 16px 0px 20px 0px;"
        )

        # Cards
        cards_layout = QGridLayout()
        cards_layout.setSpacing(16)
        self.idle_card  = StatusCard("Idle Status",       "WORKING")
        self.shift_card = StatusCard("Session Duration",  "00:00:00")
        self.internet_card  = StatusCard("Internet",      "CONNECTED")
        self.log_count_card = StatusCard("Logs Recorded", "—")
        tracking_card = StatusCard("Tracking Status", "ACTIVE")
        upload_card   = StatusCard("Upload Status",   "SYNCED")
        self.idle_card.set_status_color("#22c55e")
        self.internet_card.set_status_color("#22c55e")
        tracking_card.set_status_color("#22c55e")
        upload_card.set_status_color("#22c55e")
        cards_layout.addWidget(tracking_card,       0, 0)
        cards_layout.addWidget(self.idle_card,      0, 1)
        cards_layout.addWidget(self.shift_card,     0, 2)
        cards_layout.addWidget(upload_card,         1, 0)
        cards_layout.addWidget(self.internet_card,  1, 1)
        cards_layout.addWidget(self.log_count_card, 1, 2)
        # Activity feed
        feed_header = QHBoxLayout()
        feed_label  = QLabel("Recent Activity")
        feed_label.setStyleSheet(
            "font-size: 16px; font-weight: 700; color: #e2e8f0;"
            )
        self.feed_count_label = QLabel("0 events")
        self.feed_count_label.setStyleSheet("font-size: 12px; color: #475569;")
        feed_header.addWidget(feed_label)
        feed_header.addStretch()
        feed_header.addWidget(self.feed_count_label)
        self.activity_list = QListWidget()
        self.activity_list.setMinimumHeight(180)
        self.activity_list.setMaximumHeight(220)
        self.activity_list.setStyleSheet("""
            QListWidget {
                background-color: #0a0f1a;
                color: #cbd5e1;
                border: 1px solid #1e2d3d;
                border-radius: 12px;
                padding: 8px;
                font-size: 13px;
                }
            QListWidget::item {
                padding: 9px 12px;
                margin: 2px 0px;
                border-radius: 8px;
                background-color: #0f172a;
            }
            QListWidget::item:hover { background-color: #1e293b; }
            QListWidget::item:selected {
                background-color: #1e3a5f; color: white;
                }
            """)
                        # Buttons
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(12)
        logs_btn       = QPushButton("📋  Activity Logs")
        settings_btn   = QPushButton("⚙  Settings")
        logout_btn     = QPushButton("🔒  Logout")
        attendance_btn = QPushButton("📊  Attendance")
        for btn in [logs_btn, attendance_btn, settings_btn, logout_btn]:
            btn.setFixedHeight(42)
        logs_btn.setStyleSheet("""
            QPushButton {
                background-color: #1d4ed8; border: 1px solid #2563eb;
                border-radius: 10px; color: white;
                font-weight: 600; font-size: 13px;
            }
            QPushButton:hover { background-color: #2563eb; }
                """)
        settings_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; border: 1px solid #334155;
                border-radius: 10px; color: #e2e8f0;
                font-weight: 600; font-size: 13px;
            }
            QPushButton:hover { background-color: #334155; }
            """)
        logout_btn.setStyleSheet("""
            QPushButton {
                background-color: #7f1d1d; border: 1px solid #991b1b;
                border-radius: 10px; color: white;
                font-weight: 600; font-size: 13px;
            }
            QPushButton:hover { background-color: #991b1b; }
            """)
        logs_btn.clicked.connect(self.open_logs_window)
        settings_btn.clicked.connect(self.open_settings_window)
        logout_btn.clicked.connect(self.logout)
        attendance_btn.clicked.connect(self.open_attendance_window)
        bottom_layout.addWidget(logs_btn)
        bottom_layout.addWidget(settings_btn)
        bottom_layout.addStretch()
        bottom_layout.addWidget(attendance_btn)
        bottom_layout.addWidget(logout_btn)
        if SessionManager.role == "admin":
            admin_btn = QPushButton("🛠  Admin Panel")
            admin_btn.setFixedHeight(42)
            admin_btn.clicked.connect(self.open_admin_panel)
            bottom_layout.addWidget(admin_btn)
            # Assemble
        main_layout.addLayout(header_layout)
        main_layout.addWidget(divider)
        main_layout.addLayout(cards_layout)
        main_layout.addSpacing(20)
        main_layout.addLayout(feed_header)
        main_layout.addSpacing(8)
        main_layout.addWidget(self.activity_list)
        main_layout.addSpacing(16)
        main_layout.addLayout(bottom_layout)
        self.setLayout(main_layout)
                                                                        # ── Timer callbacks ───────────────────────────────────────────────────

    def update_shift_timer(self):
        if not self._shift_login_time:
            return
        try:
            now = datetime.now()
            login_time = self._shift_login_time
            # If login_time is timezone-aware, make now aware too
            if hasattr(login_time, 'tzinfo') and login_time.tzinfo is not None:
                now = datetime.now().astimezone()
            elapsed = now - login_time
            self.shift_card.update_value(str(elapsed).split(".")[0])
        except Exception as e:
            LoggerService.log_error(f"DashboardWindow shift timer: {e}")

    def update_idle_status(self, status: str):
        if hasattr(self, "tray"):
            self.tray.set_status("idle" if status == "IDLE" else "active")
        if status == "IDLE":
            self.idle_card.update_value("IDLE")
            self.idle_card.set_status_color("#f59e0b")
        else:
            self.idle_card.update_value("WORKING")
            self.idle_card.set_status_color("#22c55e")

                # ── Screenshot ────────────────────────────────────────────────────────

    def capture_screenshot(self):
        """✅ Background thread mein — UI freeze nahi hogi."""
        threading.Thread(
            target=self._do_capture,
            daemon=True,
        ).start()

    def _do_capture(self):
        try:
            ScreenshotManager.capture_screenshot()
            QMetaObject.invokeMethod(
                self,
                "_on_capture_done",
                Qt.ConnectionType.QueuedConnection,
            )
        except Exception as e:
            LoggerService.log_error(f"DashboardWindow capture error: {e}")

    @Slot()
    def _on_capture_done(self):
        self.load_recent_logs()

        # ── Network ───────────────────────────────────────────────────────────

    def check_network_status(self):
        """✅ Server ping use karo — Google nahi."""
        threading.Thread(
            target=self._do_network_check,
            daemon=True,
        ).start()

    def _do_network_check(self):
        online = False
        try:
            # Ping endpoint is at /status/ping (no /api prefix)
            base_url = API_BASE_URL.replace("/api", "")
            r = requests.get(
                f"{base_url}/status/ping",
                timeout=3,
            )
            online = r.status_code == 200
        except Exception:
            pass
        # Set value BEFORE queuing UI update to avoid race condition
        self._network_online = online
        QMetaObject.invokeMethod(
            self,
            "_update_network_ui",
            Qt.ConnectionType.QueuedConnection,
        )

    @Slot()
    def _update_network_ui(self):
        online = getattr(self, "_network_online", False)
        if online:
            self.status_label.setText("🟢 ONLINE")
            self.status_label.setStyleSheet(
                "color: #22c55e; font-size: 12px; font-weight: bold;"
                )
            self.internet_card.update_value("CONNECTED")
            self.internet_card.set_status_color("#22c55e")
        else:
            self.status_label.setText("🔴 OFFLINE")
            self.status_label.setStyleSheet(
                "color: #ef4444; font-size: 12px; font-weight: bold;"
                )
            self.internet_card.update_value("DISCONNECTED")
            self.internet_card.set_status_color("#ef4444")

                # ── Dashboard data ────────────────────────────────────────────────────

    def refresh_dashboard(self):
        if SessionManager.is_token_expired():
            LoggerService.log("DashboardWindow: token expired — auto logout")
            self.logout()
            return
        self.load_dashboard_stats()
        self.load_recent_logs()

    def load_dashboard_stats(self):
        threading.Thread(
            target=self._fetch_stats,
            daemon=True,
        ).start()

    def _fetch_stats(self):
        try:
            response = requests.get(
                f"{API_BASE_URL}/dashboard/stats",
                headers={
                    "Authorization": f"Bearer {SessionManager.auth_token}"
                },
                timeout=5,
            )
            data = response.json().get("data", {})
            self._stats_data = data
        except Exception:
            self._stats_data = None
        QMetaObject.invokeMethod(
            self, "_apply_stats",
            Qt.ConnectionType.QueuedConnection,
        )

    @Slot()
    def _apply_stats(self):
        data = getattr(self, "_stats_data", None)
        if data:
            count = data.get("activity_logs")
            self.log_count_card.update_value(
                str(count) if count is not None else "—"
            )
        else:
            self._load_stats_from_local_db()

    def _load_stats_from_local_db(self):
        """✅ app_logs use karo — activity_logs nahi."""
        try:
            with Database.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) as cnt FROM app_logs")
                row = cursor.fetchone()
                self.log_count_card.update_value(str(row["cnt"]))
        except Exception as e:
            LoggerService.log_error(f"DashboardWindow local stats: {e}")

    def load_recent_logs(self):
        threading.Thread(
            target=self._fetch_logs,
            daemon=True,
        ).start()

    def _fetch_logs(self):
        logs = None
        try:
            response = requests.get(
                f"{API_BASE_URL}/logs/all",
                headers={
                    "Authorization": f"Bearer {SessionManager.auth_token}"
                },
                timeout=5,
            )
            result = response.json()
            logs   = result.get("data", [])[:15]
        except Exception:
            pass

        self._logs_data = logs
        QMetaObject.invokeMethod(
            self, "_apply_logs",
            Qt.ConnectionType.QueuedConnection,
        )

    @Slot()
    def _apply_logs(self):
        logs = getattr(self, "_logs_data", None)
        if logs is not None:
            self._populate_activity_list(logs)
        else:
            self._load_logs_from_local_db()

    def _load_logs_from_local_db(self):
        """✅ app_logs + correct column names."""
        try:
            with Database.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT timestamp, message FROM app_logs
                    ORDER BY id DESC LIMIT 15
                    """
                )
                rows = cursor.fetchall()

            logs = [
                {
                    "created_at": row["timestamp"],
                    "activity":   row["message"],
                }
                for row in rows
            ]
            self._populate_activity_list(logs)

        except Exception as e:
            LoggerService.log_error(f"DashboardWindow local logs: {e}")
            self.activity_list.clear()
            self.activity_list.addItem(
                QListWidgetItem("  Unable to load activity.")
            )

    def _populate_activity_list(self, logs: list):
        IGNORE = {"CONFIGSYNCMANAGER", "SCHEDULERSERVICE", "SYNCMANAGER"}

        icon_map = {
            "SCREENSHOT CAPTURED": ("📸", "#60a5fa", "Screenshot Captured"),
            "USER IDLE":           ("🟡", "#f59e0b", "User Became Idle"),
            "USER ACTIVE":         ("🟢", "#22c55e", "User Active"),
            "LOGIN SUCCESS":       ("🔵", "#818cf8", "Login Successful"),
            "LOGIN FAILED":        ("🔴", "#ef4444", "Login Failed"),
            "LOGOUT":              ("⬜", "#94a3b8", "Logged Out"),
            "UPLOAD SUCCESS":      ("✅", "#34d399", "Upload Success"),
            "UPLOAD FAILED":       ("❌", "#f87171", "Upload Failed"),
        }

        self.activity_list.clear()
        shown = 0

        for log in logs:
            activity_raw = str(log.get("activity", "")).upper()
            if any(x in activity_raw for x in IGNORE):
                continue

            ts = str(log.get("created_at", ""))
            try:
                dt        = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                time_part = dt.astimezone().strftime("%H:%M")
            except Exception:
                time_part = ts[11:16] if len(ts) >= 16 else ts

            icon, color, label = "◾", "#94a3b8", log.get("activity", "Event")
            for key, (ic, col, lbl) in icon_map.items():
                if key in activity_raw:
                    icon, color, label = ic, col, lbl
                    break
            item = QListWidgetItem(f"  {icon}  {time_part}  ·  {label}")
            item.setForeground(QColor(color))
            self.activity_list.addItem(item)
            shown += 1
        if shown == 0:
            placeholder = QListWidgetItem("  No recent activity found.")
            placeholder.setForeground(QColor("#475569"))
            self.activity_list.addItem(placeholder)
        self.feed_count_label.setText(
            f"{shown} event{'s' if shown != 1 else ''}"
        )
                        # ── Window events ─────────────────────────────────────────────────────

    def closeEvent(self, event):
        if getattr(self, "_force_closing", False):
            event.accept()
            return
        event.ignore()
        self.hide()

        # ── Logout ────────────────────────────────────────────────────────────

    def logout(self):
        LoggerService.log("DashboardWindow: logout initiated")

        # ✅ Server token revoke
        try:
            AuthService.logout(SessionManager.session_id)
        except Exception as e:
            LoggerService.log_error(f"AuthService logout error: {e}")

        try:
            SessionLogManager.end_session()
        except Exception as e:
            LoggerService.log_error(f"SessionLogManager end error: {e}")

        try:
            ShiftManager.end_shift()
        except Exception as e:
            LoggerService.log_error(f"ShiftManager end error: {e}")

        # Stop all timers
        for timer_name in [
            "network_timer", "shift_timer", "dashboard_refresh_timer"
        ]:
            try:
                getattr(self, timer_name).stop()
            except Exception:
                pass
            # Stop services
        for attr in ["scheduler", "idle_tracker", "tray"]:
            try:
                obj = getattr(self, attr, None)
                if obj:
                    obj.stop() if hasattr(obj, "stop") else None
                    obj.hide() if hasattr(obj, "hide") else None
                    obj.deleteLater()
                    setattr(self, attr, None)
            except Exception:
                pass
        SessionManager.clear_session()
        self._force_closing = True
        self.close()
        self.deleteLater()

        from client.presentation.windows.login_window import LoginWindow
        self.login_window = LoginWindow()
        self.login_window.show()

        # ── Navigation ────────────────────────────────────────────────────────

    def open_logs_window(self):
        self.logs_window = LogsWindow()
        self.logs_window.show()
        self.logs_window.raise_()

    def open_settings_window(self):
        self.settings_window = SettingsWindow()
        self.settings_window.show()

    def open_attendance_window(self):
        self.attendance_window = AttendanceWindow()
        self.attendance_window.show()

    def open_admin_panel(self):
        if SessionManager.role != "admin":
            return
        self.admin_panel = AdminConfigPanel()
        self.admin_panel.show()