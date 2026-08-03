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
from PySide6.QtCore import QTimer, Qt, QThread, Signal
from PySide6.QtGui import QCursor, QColor
from client.presentation.windows.admin_config_panel import AdminConfigPanel, _track_worker
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


# ──────────────────────────────────────────────────────────────────────────────
#  BUG FIX: "Recent Activity" feed me internal debug messages dikh rahe the
#  (jaise "ScreenshotManager: 6 screenshots scheduled across shift ...").
#
#  Wajah: noise filtering DO alag jagah, DO alag lists se hoti thi —
#    1. API path ka `noise` list  -> ScreenshotManager INCLUDED tha
#    2. display ka `IGNORE_LOGS`  -> ScreenshotManager MISSING tha
#  aur local-DB fallback path pe pehla filter lagta hi nahi tha. Is liye jab
#  bhi feed local DB se banta (offline ya sab logs filter ho jaane par),
#  internal ScreenshotManager/Scheduler messages employee ko dikh jaate the.
#
#  Ab ek hi source of truth hai jo dono paths use karte hain.
# ──────────────────────────────────────────────────────────────────────────────
_INTERNAL_LOG_PREFIXES = (
    "CONFIGSYNCMANAGER",
    "SCHEDULERSERVICE",
    "SCREENSHOTMANAGER",   # <- ye missing tha
    "SYNCMANAGER",
    "CONFIGSYNC",
    "STARTUPMANAGER",
    "AUTOLOGINMANAGER",
    "CRYPTOENGINE",
    "APISERVICE",
    "IDLETRACKER",
    "LOGGERSERVICE",
)


def _is_user_facing(activity: str) -> bool:
    """
    True sirf un logs ke liye jo employee ko dikhane laayak hain
    (LOGIN, USER IDLE/ACTIVE, SCREENSHOT CAPTURED, UPLOAD ...).
    Internal component diagnostics filter ho jaate hain.
    """
    text = str(activity or "").upper()
    if not text.strip():
        return False
    return not any(text.lstrip().startswith(p) or f"{p}:" in text
                   for p in _INTERNAL_LOG_PREFIXES)


class _CallWorker(QThread):
    """
    Generic background worker — koi bhi blocking call (jaise `requests.get`)
    ko UI thread se hataane ke liye.

    BUG FIX: DashboardWindow (jo HAR employee use karta hai, sirf admin
    nahi) pehle `check_network_status()` (har 5s), aur `refresh_dashboard()`
    ke andar `load_dashboard_stats()`/`load_recent_logs()` (har 30s) —
    teeno seedhe `requests.get(...)` MAIN/UI thread pe call karte the.
    Network slow/down hone par UI kai second ke liye freeze ho jaata
    (buttons click na hona, window drag na hona) — har employee ke
    daily-use experience ko affect karta. admin_config_panel.py mein
    already yehi QThread-worker pattern istemal hota hai — yahan bhi
    wahi consistent approach use kar rahe hain.
    """
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class DashboardWindow(BaseWindow):
    def __init__(self):
        super().__init__()
        self.last_mouse_position = QCursor.pos()

        self.setWindowTitle("ETS Dashboard")
        self.resize(1100, 720)

        # FIX #6: Cache login_time so shift timer doesn't hit DB every second
        self._shift_login_time: datetime | None = None
        self._load_shift_login_time()
        self._workers: list = []

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

        if SessionManager.role in ("admin", "super_admin"):
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
        def _fetch():
            response = requests.get(
                f"{API_BASE_URL}/logs/all",
                headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                # BUG FIX: timeout=2 tha. Employee ka laptop internet ke
                # through VPS se baat karta hai — 2 second bahut aggressive
                # hai, normal latency/packet-loss pe bhi request timeout ho
                # jaati thi aur dashboard chupchaap local DB fallback pe
                # chala jaata tha (isi wajah se "recent activity sync nahi
                # ho rahi" wala symptom aata tha). Ye call ab background
                # QThread me hota hai, is liye bada timeout UI ko freeze
                # nahi karta.
                timeout=10,
            )
            return response.json()

        w = _CallWorker(_fetch)
        w.finished.connect(self._on_dashboard_stats_loaded)
        w.error.connect(lambda e: (print("[SUMMARY ERROR]", e), self._load_stats_from_local_db()))
        _track_worker(self._workers, w)
        w.start()

    def _on_dashboard_stats_loaded(self, result):
        try:

            total_activity_logs = 0

            # BUG FIX: server ab asli `total` bhejta hai. Pehle ye poora
            # block sirf `len(data)` gin paata tha, aur /logs/all pe
            # LIMIT 100 hai — is liye "Logs Recorded" card 100 pe permanently
            # atak jaata tha (employee ne yehi report kiya: "start me 100
            # dikha, ab bhi 100"). `total` ko sabse pehle prefer karo.
            if isinstance(result, dict) and isinstance(result.get("total"), (int, float)):
                self.log_count_card.update_value(str(int(result["total"])))
                return

            try:

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
            # BUG FIX: pehle ye SAARE pending_logs count karta tha, chahe wo
            # kisi bhi employee ke hon. Shared machine pe (ya employee badalne
            # ke baad) "Logs Recorded" card galat, inflated number dikhata tha.
            # Server wala path already sirf apne logs deta hai — local
            # fallback ko bhi wahi scope follow karna chahiye.
            cur.execute(
                "SELECT COUNT(*) FROM pending_logs WHERE employee_id = ?",
                (SessionManager.employee_id,),
            )
            log_count = cur.fetchone()[0]
            conn.close()
            self.log_count_card.update_value(str(log_count))
        except Exception as e:
            print("[LOCAL STATS ERROR]", e)

    def load_recent_logs(self):
        print("=== LOGS START ===")

        def _fetch():
            response = requests.get(
                f"{API_BASE_URL}/logs/all",
                headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                timeout=10,   # BUG FIX: 2s tha — dekho load_dashboard_stats()
            )
            return response.json()

        w = _CallWorker(_fetch)
        w.finished.connect(self._on_recent_logs_loaded)
        w.error.connect(lambda e: (print("[API LOGS ERROR]", e), self._load_logs_from_local_db()))
        _track_worker(self._workers, w)
        w.start()

    def _on_recent_logs_loaded(self, result):
        try:
            all_logs = result.get("data", []) if isinstance(result, dict) else []
            # Filter: sirf meaningful logs dikhao
            logs = [l for l in all_logs if _is_user_facing(l.get('activity', ''))][:15]

            # BUG FIX: agar server ne success to diya lekin list KHAALI hai
            # (ya sab kuch noise filter me nikal gaya), to pehle feed
            # bilkul blank reh jaata tha — na koi row, na koi placeholder.
            # Ab aise case me local DB fallback dikhate hain, taaki
            # employee ko apni offline-buffered activity to dikhe.
            if not logs:
                self._load_logs_from_local_db()
                return

            self._populate_activity_list(logs)
        except Exception as error:
            print("[API LOGS ERROR]", error)
            self._load_logs_from_local_db()

    def _load_logs_from_local_db(self):
        try:
            conn = Database.connect()
            cur  = conn.cursor()
            # BUG FIX: pehle yahan employee filter nahi tha — shared machine
            # pe pichhle employee ke buffered logs bhi dikh jaate the.
            # LIMIT 15 nahi, 200 — kyunki internal diagnostics filter hone ke
            # baad hi 15 user-facing events chunne hain. Pehle raw 15 rows
            # uthate the, jo aksar poori tarah ScreenshotManager/Scheduler
            # noise hoti thin, aur feed khaali (ya noise se bhari) dikhta tha.
            cur.execute("""
                SELECT id, activity, timestamp
                FROM pending_logs
                WHERE employee_id = ?
                ORDER BY id DESC
                LIMIT 200
                """, (SessionManager.employee_id,))
            rows = [r for r in cur.fetchall() if _is_user_facing(r[1])][:15]
            conn.close()

            self.activity_list.clear()
            if not rows:
                placeholder = QListWidgetItem("  No recent activity found.")
                placeholder.setForeground(QColor("#475569"))
                self.activity_list.addItem(placeholder)
                self.feed_count_label.setText("0 events")
                return

            # BUG FIX: pehle yahan `"created_at": ""` hardcoded tha, jabki
            # pending_logs me `timestamp` column already maujood hai. Isi
            # wajah se jab bhi dashboard local fallback pe jaata (slow
            # network pe aksar hota tha), HAR row bina time ke dikhti thi —
            # "📸 · Screenshot Captured" with a blank timestamp.
            #
            # `local_time` flag zaroori hai: pending_logs.timestamp LOCAL
            # time me likha jaata hai (LoggerService datetime.now() use
            # karta hai) jabki server ka created_at UTC hota hai. Dono ko
            # ek jaisa treat karne se local rows 5:30 ghante aage shift ho
            # jaati thin.
            logs = [
                {
                    "created_at": r[2] or "",
                    "activity":   r[1],
                    "local_time": True,
                }
                for r in rows
            ]
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

        # BUG FIX: yahan pehle ek ALAG (aur adhoori) IGNORE_LOGS list thi
        # jisme "SCREENSHOTMANAGER" tha hi nahi — is liye ScreenshotManager
        # ke internal messages feed me dikh jaate the. Ab wahi ek shared
        # `_is_user_facing()` helper use hota hai jo API path bhi use karta
        # hai, to dono jagah behaviour hamesha same rahega.
        for log in logs:
            activity_raw = str(log.get("activity", "")).upper()

            if not _is_user_facing(activity_raw):
                continue

            ts = str(log.get("created_at", "") or log.get("timestamp", "") or "")
            time_part = ""
            is_local = bool(log.get("local_time"))

            # BUG FIX: pehle ISO ("T" wali) branch me `time_part` SIRF us
            # nested `if` ke andar assign hota tha jo 6 se zyada digit ke
            # microseconds handle karta hai. Yaani har NORMAL ISO string
            # ("2026-08-02T09:30:00Z", "...00.123Z", "...00.123456Z") ke
            # liye time_part khaali reh jaata tha aur feed me time hi nahi
            # dikhta tha. Abhi ye is liye chhupa hua tha kyunki server/db.js
            # timestamps ko raw string ("2026-08-02 09:30:00") bhejta hai jo
            # doosri branch me jaata hai — pg driver ka type-parser badalte
            # hi poora feed bina time ke ho jaata.
            #
            # Ab dono formats ek hi jagah, ek hi tarike se parse hote hain.
            if ts:
                try:
                    from zoneinfo import ZoneInfo
                    IST = ZoneInfo("Asia/Kolkata")

                    ts_clean = ts.strip().replace("Z", "+00:00")
                    if "T" not in ts_clean:
                        ts_clean = ts_clean.replace(" ", "T", 1)

                    # fromisoformat 6 se zyada fractional digits accept nahi
                    # karta — extra digits trim kar do.
                    if "." in ts_clean:
                        head, frac = ts_clean.split(".", 1)
                        offset = ""
                        for marker in ("+", "-"):
                            if marker in frac:
                                idx = frac.index(marker)
                                frac, offset = frac[:idx], frac[idx:]
                                break
                        ts_clean = f"{head}.{frac[:6]}{offset}"

                    dt = datetime.fromisoformat(ts_clean)

                    if dt.tzinfo is None:
                        # Naive string: local DB rows already local time me
                        # hain, server rows UTC me. Galat assume karne se
                        # 5:30 ghante ka shift aa jaata hai.
                        dt = dt.replace(
                            tzinfo=datetime.now().astimezone().tzinfo
                            if is_local else ZoneInfo("UTC")
                        )

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
        def _ping():
            requests.get("https://www.google.com", timeout=3)
            return True

        w = _CallWorker(_ping)
        w.finished.connect(lambda _r: self._on_network_status(True))
        w.error.connect(lambda _e: self._on_network_status(False))
        _track_worker(self._workers, w)
        w.start()

    def _on_network_status(self, is_online: bool):
        if is_online:
            self.status_label.setText("🟢 ONLINE")
            self.internet_card.update_value("CONNECTED")
            self.status_label.setStyleSheet("""
            color: #22c55e;
                font-size: 12px;
                font-weight: bold;
            """)
        else:
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

        # BUG FIX: LOGOUT kabhi log hi nahi hota tha. `_populate_activity_list`
        # ke icon_map me "LOGOUT" entry maujood thi, lekin poore codebase me
        # koi `LoggerService.log("LOGOUT")` call hi nahi thi — is liye
        # employee ke Recent Activity me kabhi "Logged Out" dikh hi nahi
        # sakta tha, aur admin ke Audit Logs me bhi session end ka koi
        # record nahi jaata tha.
        #
        # Ye clear_session() se PEHLE hona zaroori hai — uske baad
        # employee_id None ho jaata hai aur LoggerService.log() chup-chaap
        # return kar deta hai (yehi bug LOGIN pe bhi tha).
        try:
            LoggerService.log("LOGOUT")
        except Exception as e:
            print("LOGOUT LOG ERROR:", e)

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
        if SessionManager.role not in ("admin", "super_admin"):
            return
       
        if hasattr(self, "scheduler") and self.scheduler:
            self.scheduler.stop()
        if hasattr(self, "idle_tracker") and self.idle_tracker:
            self.idle_tracker.stop()

        self.admin_panel = AdminConfigPanel()
        # BUG FIX: pehle ye `destroyed` signal se resume karta tha. Qt me
        # window band karne se object DESTROY nahi hota — sirf hide hota
        # hai (deleteLater() kabhi call hi nahi hota). Yaani ye signal
        # practically kabhi fire nahi karta tha: admin ek baar Admin Panel
        # khol le, to uske apne screenshots + idle tracking HAMESHA ke liye
        # band ho jaate the. Ab close hote hi resume ho jaata hai.
        self.admin_panel.destroyed.connect(self._resume_tracking_after_admin_panel)
        self.admin_panel.installEventFilter(self)
        self.admin_panel.show()

    def eventFilter(self, watched, event):
        from PySide6.QtCore import QEvent
        if (
            watched is getattr(self, "admin_panel", None)
            and event.type() == QEvent.Type.Close
        ):
            self._resume_tracking_after_admin_panel()
        return super().eventFilter(watched, event)

    def _resume_tracking_after_admin_panel(self):
        if not SessionManager.is_authenticated:
            return
        if hasattr(self, "scheduler") and self.scheduler:
            self.scheduler.start()
        if hasattr(self, "idle_tracker") and self.idle_tracker:
            self.idle_tracker.start()
