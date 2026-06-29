from datetime import datetime

import requests

from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QLabel,
    QPushButton,
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout,
    QFrame,
    QApplication,
)
from client.core.config import API_BASE_URL
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QCursor, QColor
from client.presentation.windows.admin_config_panel import AdminConfigPanel
from client.application.managers.session_log_manager import SessionLogManager
from client.infrastructure.database.database import Database
from client.application.managers.shift_manager import ShiftManager
from client.application.managers.sync_manager import SyncManager
from client.presentation.windows.logs_window import LogsWindow
from client.presentation.tray.system_tray import SystemTray
from client.presentation.windows.settings_window import SettingsWindow
from client.presentation.windows.base_window import BaseWindow
from client.presentation.widgets.status_card import StatusCard
from client.application.managers.session_manager import SessionManager
from client.application.schedulers.scheduler_service import SchedulerService
from client.application.managers.screenshot_manager import ScreenshotManager
from client.application.managers.idle_tracker import IdleTracker
from client.services.logger_service import LoggerService
from client.presentation.windows.attendance_window import AttendanceWindow


class DashboardWindow(BaseWindow):
    def __init__(self):
        super().__init__()
        self.last_mouse_position = QCursor.pos()

        self.setWindowTitle("ETS Dashboard")
        self.resize(1100, 720)

        # FIX #6: Cache login_time so shift timer doesn't hit DB every second
        self._shift_login_time: datetime | None = None
        self._load_shift_login_time()

        self.setup_ui()
        self.network_timer = QTimer()
        self.network_timer.timeout.connect(self.check_network_status)
        self.network_timer.start(5000)

        self.check_network_status()
        self.tray = SystemTray(self)
        self.tray.show()
        self.tray.show_message()

        self.activity_timer = QTimer()
        self.activity_timer.timeout.connect(self.track_activity)
        self.activity_timer.start(1000)

        # FIX #6: Shift timer now uses cached login_time — no DB query per tick
        self.shift_timer = QTimer()
        self.shift_timer.timeout.connect(self.update_shift_timer)
        self.shift_timer.start(1000)

        # FIX #1/#8: Dashboard refresh at 30s, not 15s — reduces blocking
        self.dashboard_refresh_timer = QTimer()
        self.dashboard_refresh_timer.timeout.connect(self.refresh_dashboard)
        self.dashboard_refresh_timer.start(30000)

        print("DASHBOARD CREATED")

    def _load_shift_login_time(self):
        """DB se login_time ek baar read karo — cache karo for timer."""
        try:
            connection = Database.connect()
            cursor = connection.cursor()
            cursor.execute("""
                SELECT login_time FROM shifts
                WHERE employee_id = ?
                ORDER BY id DESC LIMIT 1
                """, (SessionManager.employee_id,))
            shift = cursor.fetchone()
            connection.close()
            if shift:
                self._shift_login_time = datetime.strptime(shift[0], "%Y-%m-%d %H:%M:%S")
        except Exception as e:
            print("[SHIFT LOGIN TIME LOAD ERROR]", e)

    def setup_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(28, 22, 28, 20)
        main_layout.setSpacing(0)

        # ── Header ──────────────────────────────────────────
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("ETS Control Center")
        title.setStyleSheet("""
            font-size: 26px;
            font-weight: 700;
            color: #f1f5f9;
            letter-spacing: -0.5px;
            """)

        employee_container = QVBoxLayout()
        employee_container.setSpacing(2)

        employee_label = QLabel(f"👤  {SessionManager.employee_id}")
        employee_label.setStyleSheet("""
            color: #94a3b8;
            font-size: 13px;
            """)

        self.status_label = QLabel("🟢  ONLINE")
        self.status_label.setStyleSheet("""
            color: #22c55e;
            font-size: 12px;
            font-weight: bold;
            """)

        employee_container.addWidget(employee_label, alignment=Qt.AlignmentFlag.AlignRight)
        employee_container.addWidget(self.status_label, alignment=Qt.AlignmentFlag.AlignRight)

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addLayout(employee_container)

        # ── Divider ─────────────────────────────────────────
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background-color: #1e2d3d; margin: 16px 0px 20px 0px;")

        # ── Cards Grid ─────────────────────────────────────
        cards_layout = QGridLayout()
        cards_layout.setSpacing(16)
        cards_layout.setContentsMargins(0, 0, 0, 0)

        tracking_card = StatusCard("Tracking Status", "ACTIVE")
        tracking_card.set_status_color("#22c55e")

        self.idle_card = StatusCard("Idle Status", "WORKING")
        self.idle_card.set_status_color("#22c55e")

        # FIX #6: This card shows SHIFT DURATION, not offline employees
        self.shift_card = StatusCard("Session Duration", "00:00:00")

        upload_card = StatusCard("Upload Status", "SYNCED")
        upload_card.set_status_color("#22c55e")

        self.internet_card = StatusCard("Internet", "CONNECTED")
        self.internet_card.set_status_color("#22c55e")

        self.log_count_card = StatusCard("Logs Recorded", "—")

        cards_layout.addWidget(tracking_card, 0, 0)
        cards_layout.addWidget(self.idle_card, 0, 1)
        cards_layout.addWidget(self.shift_card, 0, 2)
        cards_layout.addWidget(upload_card, 1, 0)
        cards_layout.addWidget(self.internet_card, 1, 1)
        cards_layout.addWidget(self.log_count_card, 1, 2)

        # ── Activity Feed ───────────────────────────────────
        feed_header = QHBoxLayout()
        feed_label = QLabel("Recent Activity")
        feed_label.setStyleSheet("""
            font-size: 16px;
            font-weight: 700;
            color: #e2e8f0;
            """)

        self.feed_count_label = QLabel("0 events")
        self.feed_count_label.setStyleSheet("""
            font-size: 12px;
            color: #475569;
            """)

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
                outline: none;
            }
            QListWidget::item {
                padding: 9px 12px;
                margin: 2px 0px;
                border-radius: 8px;
                background-color: #0f172a;
                color: #cbd5e1;
            }
            QListWidget::item:hover { background-color: #1e293b; }
            QListWidget::item:selected { background-color: #1e3a5f; color: white; }
            QScrollBar:vertical { background: #0a0f1a; width: 6px; border-radius: 3px; }
            QScrollBar::handle:vertical { background: #334155; border-radius: 3px; }
            """)

        # ── Buttons ──────────────────────────────────────────
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(12)

        logs_button       = QPushButton("📋  Activity Logs")
        settings_button   = QPushButton("⚙  Settings")
        logout_button     = QPushButton("🔒  Logout")
        attendance_button = QPushButton("📊 Attendance")

        admin_button = None

        if SessionManager.role == "admin":
            admin_button = QPushButton("🛠 Admin Panel")
            admin_button.setFixedHeight(42)

        for btn in [logs_button, attendance_button, settings_button, logout_button]:
            btn.setFixedHeight(42)

        # BUG FIX: logout_button ka style missing tha — unstyled button dikha raha tha
        logout_button.setStyleSheet("""
            QPushButton {
                background-color: #7f1d1d;
                border: 1px solid #991b1b;
                border-radius: 10px;
                color: white;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #991b1b; }
            QPushButton:pressed { background-color: #450a0a; }
        """)

        logs_button.setStyleSheet("""
            QPushButton {
                background-color: #1d4ed8;
                border: 1px solid #2563eb;
                border-radius: 10px;
                color: white;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #2563eb; }
            QPushButton:pressed { background-color: #1e40af; }
            """)

        settings_button.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 10px;
                color: #e2e8f0;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #334155; }
            QPushButton:pressed { background-color: #0f172a; }
            """)

        logout_button.clicked.connect(self.logout)
        attendance_button.clicked.connect(self.open_attendance_window)
        logs_button.clicked.connect(self.open_logs_window)
        settings_button.clicked.connect(self.open_settings_window)

        bottom_layout.addWidget(logs_button)
        bottom_layout.addWidget(settings_button)
        bottom_layout.addWidget(logout_button, alignment=Qt.AlignmentFlag.AlignRight)
        bottom_layout.addWidget(attendance_button, alignment=Qt.AlignmentFlag.AlignRight)

        if admin_button:
            admin_button.clicked.connect(self.open_admin_panel)
            bottom_layout.addWidget(admin_button)

        # ── Assemble ─────────────────────────────────────────
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

        # ── Services ─────────────────────────────────────────
        self.scheduler = SchedulerService()
        self.scheduler.screenshot_triggered.connect(self.capture_screenshot)
        if hasattr(self.scheduler, "force_logout"):
            self.scheduler.force_logout.connect(self.logout)
        self.scheduler.start()

        self.idle_tracker = IdleTracker()
        self.idle_tracker.status_changed.connect(self.update_idle_status)
        self.idle_tracker.start()

        from PySide6.QtCore import QTimer

        self.check_pending_sync()

        QTimer.singleShot(1000, self.load_dashboard_stats)
        QTimer.singleShot(2000, self.load_recent_logs)

    def capture_screenshot(self):
        result = ScreenshotManager.capture_screenshot()
        print(result)
        self.load_recent_logs()

    def update_idle_status(self, status: str):
        if hasattr(self, "tray"):
            self.tray.set_status("idle" if status == "IDLE" else "active")
        if status == "IDLE":
            self.idle_card.update_value("IDLE")
            self.idle_card.set_status_color("#f59e0b")
        else:
            self.idle_card.update_value("WORKING")
            self.idle_card.set_status_color("#22c55e")

    def update_shift_timer(self):
        """FIX #6: Use cached login_time — no DB query every second."""
        if not self._shift_login_time:
            return
        try:
            duration = datetime.now() - self._shift_login_time
            self.shift_card.update_value(str(duration).split(".")[0])
        except Exception as e:
            print("[SHIFT TIMER ERROR]", e)

    def track_activity(self):
        current_position = QCursor.pos()
        if current_position != self.last_mouse_position:
            self.last_mouse_position = current_position
            self.idle_tracker.reset_activity()

    def open_logs_window(self):
        self.logs_window = LogsWindow()
        self.logs_window.show()
        self.logs_window.raise_()
        self.logs_window.activateWindow()

    def open_settings_window(self):
        self.settings_window = SettingsWindow()
        self.settings_window.saved = None
        self.settings_window.show()

    def closeEvent(self, event):
        if getattr(self, "_force_closing", False):
            event.accept()
            return
        event.ignore()
        self.hide()

    def check_pending_sync(self):
        pending = SyncManager.get_pending_screenshots()
        print(f"PENDING SCREENSHOTS: {len(pending)}")

    def load_dashboard_stats(self):
        try:
            # ROOT CAUSE FIX: "Logs Recorded" card was stuck on "—" for every
            # employee because this previously called /dashboard/stats, which
            # is adminOnly on the server (see dashboard.routes.js) and always
            # returns 403 for a normal employee token. The employee-safe,
            # per-employee-scoped endpoint is /logs/all (log.routes.js ->
            # log.controller.getLogs), which is also what load_recent_logs()
            # already uses. Switching to that endpoint here as well.
            response = requests.get(
                f"{API_BASE_URL}/logs/all",
                headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                timeout=2,
            )
            result = response.json()

            # Safely parse and extract the employee-specific logs count if present,
            # otherwise fallback to local queued logs.
            total_activity_logs = 0

            try:
                # Defensive: always work with dicts
                if isinstance(result, dict):
                    # Prefer nested data structures
                    data_block = result.get("data")
                    if isinstance(data_block, dict):
                        data = data_block
                    elif isinstance(data_block, list):
                        # Expected shape from /logs/all: {"success": true, "data": [...]}
                        data = {"_list": data_block}
                    else:
                        data = result

                    # Keys to try in order (as per requirement)
                    candidate_keys = (
                        "employee_logs_count",
                        "activity_logs",
                        "logs_count",
                        "user_logs",
                        "logsRecorded",
                        "total_logs",
                        "total_activity_logs",
                        "count",
                        "total",
                        "logs",
                    )

                    found = False

                    # 1) The common case: data.data is a list of log rows.
                    if isinstance(data_block, list):
                        total_activity_logs = len(data_block)
                        found = True

                    # 2) Direct keys in data dict
                    if not found and isinstance(data, dict):
                        for k in candidate_keys:
                            if k in data and data.get(k) not in (None, ""):
                                total_activity_logs = data.get(k)
                                found = True
                                break

                    # 3) Deep: data.logs
                    if not found and isinstance(data, dict) and isinstance(data.get("logs"), (list, dict)):
                        logs_val = data.get("logs")
                        if isinstance(logs_val, list):
                            total_activity_logs = len(logs_val)
                            found = True
                        elif isinstance(logs_val, dict):
                            for k in ("count", "employee_logs_count", "logs_count", "total", "total_logs"):
                                if k in logs_val and logs_val.get(k) not in (None, ""):
                                    total_activity_logs = logs_val.get(k)
                                    found = True
                                    break

                    # 4) Root-level deep: result.data.logs
                    if not found and isinstance(result.get("data"), dict):
                        logs_root = result["data"].get("logs")
                        if isinstance(logs_root, list):
                            total_activity_logs = len(logs_root)
                            found = True
                        elif isinstance(logs_root, dict):
                            for k in ("count", "employee_logs_count", "logs_count", "total", "total_logs"):
                                if k in logs_root and logs_root.get(k) not in (None, ""):
                                    total_activity_logs = logs_root.get(k)
                                    found = True
                                    break

                    # 5) last resort: if success but no keys, keep 0 and fallback to local
                    if not found:
                        raise KeyError("No employee logs key found")

            except Exception:
                # Fallback to local pending logs count
                self._load_stats_from_local_db()
                return

            # Normalize numeric output and update UI; never show '—'
            try:
                total_activity_logs_int = int(total_activity_logs)
            except Exception:
                total_activity_logs_int = 0

            self.log_count_card.update_value(str(total_activity_logs_int))

        except Exception as error:
            print("[SUMMARY ERROR]", error)
            self._load_stats_from_local_db()


    def _load_stats_from_local_db(self):
        try:
            conn = Database.connect()
            cur  = conn.cursor()
            # BUG FIX: local SQLite DB mein 'activity_logs' table nahi hai,
            # 'pending_logs' hai. Galat table name se SQL error aata tha aur
            # stat card hamesha empty rehta tha.
            cur.execute("SELECT COUNT(*) FROM pending_logs")
            log_count = cur.fetchone()[0]
            conn.close()
            self.log_count_card.update_value(str(log_count))
        except Exception as e:
            print("[LOCAL STATS ERROR]", e)

    def load_recent_logs(self):
        print("=== LOGS START ===")
        loaded = self._load_logs_from_api()
        if not loaded:
            self._load_logs_from_local_db()

    def _load_logs_from_api(self):
        print("=== API LOGS CALL ===")
        
        try:
            response = requests.get(
                f"{API_BASE_URL}/logs/all",
                headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                timeout=2,
            )
            result = response.json()
            all_logs = result.get("data", [])
            # Filter: sirf meaningful logs dikhao
            noise = ['ConfigSyncManager', 'SchedulerService', 'SyncManager', 'ScreenshotManager']
            logs = [l for l in all_logs if not any(n in l.get('activity','') for n in noise)][:15]
            self.activity_list.clear()
            self._populate_activity_list(logs)
            return True
        except Exception as error:
            print("[API LOGS ERROR]", error)
            return False

    def _load_logs_from_local_db(self):
        try:
            conn = Database.connect()
            cur  = conn.cursor()
            # BUG FIX: local SQLite mein activity_logs nahi, pending_logs hai.
            # created_at column bhi nahi — activity column se kaam chalao.
            cur.execute("""
                SELECT id, activity
                FROM pending_logs
                ORDER BY id DESC
                LIMIT 15
                """)
            rows = cur.fetchall()
            conn.close()

            self.activity_list.clear()
            if not rows:
                placeholder = QListWidgetItem("  No recent activity found.")
                placeholder.setForeground(QColor("#475569"))
                self.activity_list.addItem(placeholder)
                self.feed_count_label.setText("0 events")
                return

            logs = [{"created_at": "", "activity": r[1]} for r in rows]
            self._populate_activity_list(logs)
        except Exception as e:
            print("[LOCAL LOGS ERROR]", e)
            self.activity_list.clear()
            self.activity_list.addItem(QListWidgetItem("  Unable to load activity."))

    def _populate_activity_list(self, logs):
        self.activity_list.clear()

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

        IGNORE_LOGS = [
            "CONFIGSYNCMANAGER",
            "SCHEDULERSERVICE",
            "CONFIGSYNC",
            "SYNCMANAGER",
        ]

        for log in logs:
            activity_raw = str(log.get("activity", "")).upper()

            if any(x in activity_raw for x in IGNORE_LOGS):
                continue

            ts = str(log.get("created_at", "") or log.get("timestamp", "") or "")
            time_part = ""

            # 🔥 FIX: Dynamic Timezone parser from UTC to local IST (Asia/Kolkata)
            if ts:
                try:
                    from zoneinfo import ZoneInfo
                    IST = ZoneInfo("Asia/Kolkata")

                    # Normalize ISO strings formats
                    if "T" in ts:
                        ts_clean = ts.replace("Z", "+00:00")
                        # Handle microsecond trimming if necessary for fromisoformat
                        if "." in ts_clean and len(ts_clean.split(".")[1].split("+")[0]) > 6:
                            parts = ts_clean.split(".")
                            subparts = parts[1].split("+")
                            ts_clean = f"{parts[0]}.{subparts[0][:6]}+{subparts[1]}"
                            dt = datetime.fromisoformat(ts_clean)
                            time_part = dt.astimezone(IST).strftime("%H:%M")
                    else:
                        # Clean raw datetime formats from DB strings
                        ts_clean = ts.split(".")[0] if "." in ts else ts
                        dt = datetime.strptime(ts_clean, "%Y-%m-%d %H:%M:%S")
                        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
                        time_part = dt.astimezone(IST).strftime("%H:%M")
                except Exception as e:
                    print(f"[TIMEZONE PARSE DEBUG ERROR] string was: {ts}, error: {e}")
                    time_part = ts[11:16] if len(ts) >= 16 else ts[:5]
    
            icon, color, label = "◾", "#94a3b8", log.get("activity", "Event")
            for key, (ic, col, lbl) in icon_map.items():
                if key in activity_raw:
                    icon, color, label = ic, col, lbl
                    break
                
            text = f"  {icon}  {time_part}  ·  {label}"
            item = QListWidgetItem(text)
            item.setForeground(QColor(color))
            self.activity_list.addItem(item)
            count = self.activity_list.count()
            self.feed_count_label.setText(f"{count} event{'s' if count != 1 else ''}")

    def check_network_status(self):
        try:
            requests.get("https://www.google.com", timeout=3)

            self.status_label.setText("🟢 ONLINE")
            self.internet_card.update_value("CONNECTED")
            self.status_label.setStyleSheet("""
            color: #22c55e;
                font-size: 12px;
                font-weight: bold;
            """)

        except Exception:
            self.status_label.setText("🔴 OFFLINE")
            self.internet_card.update_value("DISCONNECTED")
            self.status_label.setStyleSheet("""
            color: #ef4444;
                font-size: 12px;
                font-weight: bold;
            """)


    def refresh_dashboard(self):
        from client.application.managers.session_manager import SessionManager
        if SessionManager.is_token_expired():
            print("[TOKEN EXPIRED] Auto-logout triggered")
            self.logout()
            return
        try:
            self.load_dashboard_stats()
            self.load_recent_logs()
        except Exception as error:
            print("[REFRESH ERROR]", error)

    def logout(self):
        print("[LOGOUT] Starting...")

        try:
            SessionLogManager.end_session()
        except Exception as e:
            print("END_SESSION ERROR:", e)

        try:
            ShiftManager.end_shift()
        except Exception as e:
            print("END_SHIFT ERROR:", e)

        # FIX #9: Stop ALL timers before destroying objects
        try:
            self.activity_timer.stop()
        except Exception:
            pass
        try:
            self.shift_timer.stop()
        except Exception:
            pass
        try:
            self.dashboard_refresh_timer.stop()
        except Exception:
            pass

        try:
            if hasattr(self, "scheduler"):
                self.scheduler.stop()
                self.scheduler.deleteLater()
                self.scheduler = None
        except Exception as e:
            print("SCHEDULER ERROR:", e)

        try:
            if hasattr(self, "idle_tracker"):
                self.idle_tracker.stop()
                self.idle_tracker.deleteLater()
                self.idle_tracker = None
        except Exception as e:
            print("IDLE ERROR:", e)

        try:
            if hasattr(self, "tray"):
                self.tray.hide()
                self.tray.deleteLater()
                self.tray = None
        except Exception as e:
            print("TRAY ERROR:", e)

        SessionManager.clear_session()

        from client.presentation.windows.login_window import LoginWindow

        self._force_closing = True
        self.close()
        self.deleteLater()

        self.login_window = LoginWindow()
        self.login_window.show()

    def open_attendance_window(self):
        self.attendance_window = AttendanceWindow()
        self.attendance_window.show()

    def open_admin_panel(self):
        if SessionManager.role != "admin":
            return
        # BUG FIX: Pehle dashboard ka scheduler/idle_tracker chalta rehta tha
        # jab admin panel khulta tha — dono apna apna SchedulerService +
        # IdleTracker chalate the same employee/device ke liye, jisse
        # duplicate screenshots, duplicate idle logs, aur duplicate
        # config-sync calls ho rahe the. Ab dashboard ka tracking pause
        # karte hain jab tak admin panel khula hai.
        if hasattr(self, "scheduler") and self.scheduler:
            self.scheduler.stop()
        if hasattr(self, "idle_tracker") and self.idle_tracker:
            self.idle_tracker.stop()

        self.admin_panel = AdminConfigPanel()
        self.admin_panel.destroyed.connect(self._resume_tracking_after_admin_panel)
        self.admin_panel.show()

    def _resume_tracking_after_admin_panel(self):
        # Admin panel band hua (ya logout hua) — dashboard ka apna tracking
        # resume karo, agar session abhi bhi active hai.
        if not SessionManager.is_authenticated:
            return
        if hasattr(self, "scheduler") and self.scheduler:
            self.scheduler.start()
        if hasattr(self, "idle_tracker") and self.idle_tracker:
            self.idle_tracker.start()
