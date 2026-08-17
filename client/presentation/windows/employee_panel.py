"""
ETS Employee Panel — sidebar-driven employee workspace.

Purana `DashboardWindow` ek hi flat screen tha jisme 6 cards aur ek list thi;
Attendance/Logs alag popup windows me khulte the. Ab sab kuch ek panel ke
andar hai:

    Dashboard · Attendance · Activity Logs · Screenshots · Settings · Help

Design ek hi jagah se aata hai (`client/presentation/theme.py`) — pehle har
window apne inline hex codes use karti thi, is liye har screen alag dikhti
thi.

Saare network calls background QThread workers pe hote hain — UI kabhi
freeze nahi hoti, chahe server slow ho ya down.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

from client.core import http as _http
from PySide6.QtCore import Qt, QThread, QTimer, Signal, QDate
from PySide6.QtGui import QCursor, QColor
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDateEdit, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QAbstractItemView, QDialog,
)

from client.core.config import API_BASE_URL, STORAGE_DIR, APP_VERSION
from client.presentation.theme import (
    C, R, R_SM, app_style, button, table_style, scrollbar,
)
from client.presentation import theme as _theme
from client.presentation.windows.profile_page import ProfilePage
from client.presentation.widgets.avatar import Avatar, ClickableAvatar, forget as forget_avatar
from client.presentation.widgets.panel_widgets import (
    ActivityRow, Card, NavButton, PageHeader, StatCard,
)
from client.application.managers.session_manager import SessionManager
from client.application.services import notifier
from client.application.services.auth_service import AuthService
from client.application.managers.session_log_manager import SessionLogManager
from client.application.managers.shift_manager import ShiftManager
from client.application.managers.idle_tracker import IdleTracker
from client.application.managers.screenshot_manager import ScreenshotManager
from client.application.schedulers.scheduler_service import SchedulerService
from client.infrastructure.database.database import Database
from client.services.logger_service import LoggerService
from client.services.settings_service import SettingsService
from client.presentation.tray.system_tray import SystemTray
from client.presentation.windows.screenshot_preview_window import ScreenshotPreviewWindow
from client.presentation.windows.team_page import TeamPage
from client.application.managers.chat_manager import ChatManager

from client.core.time_ist import IST  # single source of truth


# ──────────────────────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────────────────────

def parse_ts(value) -> datetime | None:
    """Server ka koi bhi timestamp format -> aware UTC datetime."""
    raw = str(value or "").strip()
    if not raw:
        return None
    cleaned = raw.replace("Z", "+00:00")
    if "T" not in cleaned:
        cleaned = cleaned.replace(" ", "T", 1)
    if "." in cleaned:
        head, frac = cleaned.split(".", 1)
        offset = ""
        for marker in ("+", "-"):
            if marker in frac:
                i = frac.index(marker)
                frac, offset = frac[:i], frac[i:]
                break
        cleaned = f"{head}.{frac[:6]}{offset}"
    try:
        dt = datetime.fromisoformat(cleaned)
    except Exception:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def fmt_ist(value, pattern="%d %b %Y, %I:%M:%S %p", fallback="—") -> str:
    dt = parse_ts(value)
    return dt.astimezone(IST).strftime(pattern) if dt else fallback


def fmt_time(value) -> str:
    dt = parse_ts(value)
    return dt.astimezone(IST).strftime("%I:%M:%S %p") if dt else ""


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02}:{(seconds % 3600) // 60:02}:{seconds % 60:02}"


# Activity ko human-readable banane ki ek hi jagah — pehle ye mapping
# dashboard aur logs window me alag-alag (aur adhoori) thi.
ACTIVITY_MAP = {
    "SCREENSHOT CAPTURED": ("Screenshot Captured", "Screen captured and encrypted", C.BLUE),
    "USER IDLE":           ("User Became Idle",    "No activity detected",          C.AMBER),
    "USER ACTIVE":         ("User Active",         "Keyboard & mouse activity",     C.GREEN),
    "LOGIN SUCCESS":       ("Signed In",           "Session started",               C.PRIMARY),
    "LOGIN FAILED":        ("Sign-in Failed",      "Invalid credentials",           C.RED),
    "LOGOUT":              ("Signed Out",          "Session ended",                 C.TEXT_MUTED),
    "UPLOAD SUCCESS":      ("Upload Complete",     "Data synced to server",         C.GREEN),
    "UPLOAD FAILED":       ("Upload Failed",       "Will retry automatically",      C.RED),
}

INTERNAL_PREFIXES = (
    "CONFIGSYNCMANAGER", "SCHEDULERSERVICE", "SCREENSHOTMANAGER", "SYNCMANAGER",
    "CONFIGSYNC", "STARTUPMANAGER", "AUTOLOGINMANAGER", "CRYPTOENGINE",
    "APISERVICE", "IDLETRACKER", "LOGGERSERVICE",
)


def is_user_facing(activity: str) -> bool:
    text = str(activity or "").upper().strip()
    if not text:
        return False
    return not any(text.startswith(p) or f"{p}:" in text for p in INTERNAL_PREFIXES)


def describe(activity: str) -> tuple[str, str, str]:
    text = str(activity or "").upper()
    for key, (title, sub, color) in ACTIVITY_MAP.items():
        if key in text:
            return title, sub, color
    return str(activity)[:60], "Activity recorded", C.TEXT_MUTED


class Worker(QThread):
    """Generic background call — UI thread kabhi block na ho."""
    done = Signal(object)
    fail = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn, self._args, self._kwargs = fn, args, kwargs

    def run(self):
        try:
            self.done.emit(self._fn(*self._args, **self._kwargs))
        except Exception as error:
            self.fail.emit(str(error))


def api_get(path: str, params: dict | None = None, timeout: int = 12):
    response = _http.get(
        f"{API_BASE_URL}{path}",
        params=params or {},
        headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def export_csv(parent, default_name: str, headers: list, rows: list) -> None:
    path, _ = QFileDialog.getSaveFileName(parent, "Export CSV", default_name, "CSV Files (*.csv)")
    if not path:
        return
    try:
        import csv
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(rows)
        QMessageBox.information(parent, "Export", f"Exported {len(rows)} rows to:\n{path}")
    except Exception as error:
        QMessageBox.warning(parent, "Export failed", str(error))


# ──────────────────────────────────────────────────────────────────────────────
#  PAGE 1 — Dashboard
# ──────────────────────────────────────────────────────────────────────────────

class DashboardPage(QWidget):
    """
    Employee dashboard — "Today's Overview" (6 live cards) + Recent Activity.

    NOTE: Yahan jaan-boojh kar admin-style controls NAHI hain (Refresh /
    Take Screenshot / Sync Now / Quick Actions / Reports). Wo sab admin
    console ka hissa hain — employee ke paas apne tracking pe koi manual
    control nahi hona chahiye, warna monitoring ka matlab hi khatam.
    """

    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        self._workers: list = []
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        root.addWidget(PageHeader("Today's Overview",
                                  "Your live tracking status and activity."))

        grid = QGridLayout()
        grid.setSpacing(14)
        self.c_tracking = StatCard("🎯", "Tracking Status", C.GREEN, C.GREEN_BG)
        self.c_activity = StatCard("🖥", "Activity Status", C.BLUE, C.BLUE_BG)
        self.c_internet = StatCard("🌐", "Internet Status", C.CYAN, C.CYAN_BG)
        self.c_upload   = StatCard("☁️", "Upload Status", C.PURPLE, C.PURPLE_BG)
        self.c_session  = StatCard("⏱", "Session Duration", C.AMBER, C.AMBER_BG,
                                   sparkline=False)
        self.c_shots    = StatCard("📸", "Screenshots Today", C.AMBER, C.AMBER_BG)
        for i, card in enumerate((self.c_tracking, self.c_activity, self.c_internet,
                                  self.c_upload, self.c_session, self.c_shots)):
            grid.addWidget(card, i // 3, i % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        root.addLayout(grid)

        # ── Recent Activity ──
        card = Card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(18, 15, 14, 12)
        title = QLabel("Recent Activity")
        title.setStyleSheet(f"color:{C.TEXT};font-size:15px;font-weight:700;border:none;")
        self._count = QLabel("")
        self._count.setStyleSheet(f"color:{C.TEXT_DIM};font-size:12px;border:none;")
        view_all = QPushButton("View All")
        view_all.setCursor(Qt.CursorShape.PointingHandCursor)
        view_all.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;color:{C.PRIMARY};"
            f"font-size:13px;font-weight:600;}}"
            f"QPushButton:hover{{color:{C.BLUE};}}"
        )
        view_all.clicked.connect(lambda: self._panel.go("logs"))
        head.addWidget(title)
        head.addWidget(self._count)
        head.addStretch()
        head.addWidget(view_all)
        cl.addLayout(head)

        self._feed_area = QScrollArea()
        self._feed_area.setWidgetResizable(True)
        self._feed_area.setFrameShape(QFrame.Shape.NoFrame)
        self._feed_area.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}" + scrollbar(C.CARD)
        )
        self._feed_host = QWidget()
        self._feed_host.setStyleSheet("background:transparent;")
        self._feed = QVBoxLayout(self._feed_host)
        self._feed.setContentsMargins(0, 0, 0, 0)
        self._feed.setSpacing(0)
        self._feed.addStretch()
        self._feed_area.setWidget(self._feed_host)
        cl.addWidget(self._feed_area, 1)
        root.addWidget(card, 1)

    # ── data ────────────────────────────────────────────────────────────
    def refresh(self):
        self._load_summary()
        self._load_feed()

    def _run(self, fn, on_done, on_fail=None):
        worker = Worker(fn)
        worker.done.connect(on_done)
        worker.fail.connect(on_fail or (lambda e: None))
        self._workers = [w for w in self._workers if w.isRunning()] + [worker]
        worker.start()

    def _load_summary(self):
        self._run(lambda: api_get("/dashboard/me"), self._on_summary,
                  lambda e: self._on_summary_offline())

    def _on_summary(self, payload):
        d = (payload or {}).get("data", {})
        shots = d.get("screenshots_today", 0)
        self.c_shots.set_value(str(shots))
        self.c_shots.set_subtitle("Captured today")
        self.c_shots.push_point(shots)
        self._panel.mark_server(True)

    def _on_summary_offline(self):
        try:
            conn = Database.connect()
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM screenshots WHERE employee_id = ? "
                "AND date(timestamp) = date('now','localtime')",
                (SessionManager.employee_id,),
            )
            count = cur.fetchone()[0]
            conn.close()
            self.c_shots.set_value(str(count))
            self.c_shots.set_subtitle("Captured today (local)")
        except Exception:
            self.c_shots.set_subtitle("Unavailable")
        self._panel.mark_server(False)

    def _load_feed(self):
        self._run(lambda: api_get("/logs/all"), self._on_feed,
                  lambda e: self._on_feed_local())

    def _on_feed(self, payload):
        rows = [r for r in (payload or {}).get("data", [])
                if is_user_facing(r.get("activity"))][:20]
        if not rows:
            self._on_feed_local()
            return
        self._render_feed([(r.get("activity"), r.get("created_at")) for r in rows])

    def _on_feed_local(self):
        try:
            conn = Database.connect()
            cur = conn.cursor()
            cur.execute(
                "SELECT activity, timestamp FROM pending_logs WHERE employee_id = ? "
                "ORDER BY id DESC LIMIT 200",
                (SessionManager.employee_id,),
            )
            rows = [r for r in cur.fetchall() if is_user_facing(r[0])][:20]
            conn.close()
            self._render_feed(list(rows), local_time=True)
        except Exception:
            self._render_feed([])

    def _render_feed(self, entries, local_time: bool = False):
        while self._feed.count() > 1:
            item = self._feed.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not entries:
            empty = QLabel("  No activity recorded yet.")
            empty.setStyleSheet(f"color:{C.TEXT_DIM};font-size:13px;padding:24px;border:none;")
            self._feed.insertWidget(0, empty)
            self._count.setText("")
            return

        for activity, stamp in entries:
            title, subtitle, color = describe(activity)
            if local_time:
                try:
                    when = datetime.strptime(str(stamp)[:19], "%Y-%m-%d %H:%M:%S") \
                        .strftime("%I:%M:%S %p")
                except Exception:
                    when = str(stamp)[:19]
            else:
                when = fmt_time(stamp)
            self._feed.insertWidget(self._feed.count() - 1,
                                    ActivityRow(color, title, subtitle, when))
        self._count.setText(f"· {len(entries)} events")


class _TablePage(QWidget):
    """Attendance / Logs / Screenshots ke liye shared base — filter bar,
    table, pagination, export. Pehle ye teeno alag windows me duplicate tha."""

    PAGE_SIZE = 50

    def __init__(self, panel, title, subtitle, columns):
        super().__init__()
        self._panel = panel
        self._workers: list = []
        self._rows: list = []
        self._page = 1
        self._total = 0
        self._filtered = False
        self._build(title, subtitle, columns)

    def _build(self, title, subtitle, columns):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(PageHeader(title, subtitle))

        bar = Card()
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(14, 12, 14, 12)
        bar_layout.setSpacing(10)

        self._date = QDateEdit()
        self._date.setCalendarPopup(True)
        self._date.setDate(QDate.currentDate())
        self._date.setFixedWidth(150)

        search_btn = QPushButton("Search")
        search_btn.setStyleSheet(button("primary"))
        search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        search_btn.clicked.connect(self._on_search)

        clear_btn = QPushButton("Clear")
        clear_btn.setStyleSheet(button("secondary"))
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._on_clear)

        self._export_btn = QPushButton("Export CSV")
        self._export_btn.setStyleSheet(button("secondary"))
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.clicked.connect(self._on_export)

        label = QLabel("Date")
        label.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:13px;border:none;")
        bar_layout.addWidget(label)
        bar_layout.addWidget(self._date)
        bar_layout.addWidget(search_btn)
        bar_layout.addWidget(clear_btn)
        bar_layout.addStretch()
        self._extra_controls(bar_layout)
        bar_layout.addWidget(self._export_btn)
        root.addWidget(bar)

        self._table = QTableWidget()
        self._table.setColumnCount(len(columns))
        self._table.setHorizontalHeaderLabels(columns)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setDefaultSectionSize(42)
        self._table.setStyleSheet(table_style())
        root.addWidget(self._table, 1)

        pager = QHBoxLayout()
        pager.setSpacing(10)
        self._prev = QPushButton("← Prev")
        self._prev.setStyleSheet(button("secondary"))
        self._prev.clicked.connect(lambda: self.load(self._page - 1))
        self._next = QPushButton("Next →")
        self._next.setStyleSheet(button("secondary"))
        self._next.clicked.connect(lambda: self.load(self._page + 1))
        self._page_label = QLabel("")
        self._page_label.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:12px;")
        pager.addWidget(self._prev)
        pager.addWidget(self._page_label)
        pager.addWidget(self._next)
        pager.addStretch()
        root.addLayout(pager)

    def _extra_controls(self, layout): ...

    def _on_search(self):
        self._filtered = True
        self.load(1)

    def _on_clear(self):
        self._filtered = False
        self._date.setDate(QDate.currentDate())
        self.load(1)

    def _params(self) -> dict:
        params = {"page": self._page}
        if self._filtered:
            params["date"] = self._date.date().toString("yyyy-MM-dd")
        return params

    def load(self, page: int = 1):
        self._page = max(1, page)
        params = self._params()
        worker = Worker(lambda: api_get(self.ENDPOINT, params))
        worker.done.connect(self._on_loaded)
        worker.fail.connect(self._on_failed)
        self._workers = [w for w in self._workers if w.isRunning()] + [worker]
        worker.start()

    def refresh(self):
        self.load(self._page)

    def _on_failed(self, error):
        self._page_label.setText("Could not load — check connection")
        self._panel.mark_server(False)

    def _on_loaded(self, payload):
        self._rows = (payload or {}).get("data", [])
        self._total = (payload or {}).get("total", len(self._rows))
        self._page_label.setText(f"Page {self._page}  ·  {self._total} total")
        self._prev.setEnabled(self._page > 1)
        self._next.setEnabled(self._page * self.PAGE_SIZE < self._total)
        self._panel.mark_server(True)
        self._populate()

    def _populate(self): ...

    def _on_export(self):
        self._export_btn.setEnabled(False)
        self._export_btn.setText("Exporting…")

        def fetch_all():
            out, page = [], 1
            while len(out) < 5000:
                params = dict(self._params()); params["page"] = page
                data = api_get(self.ENDPOINT, params, timeout=30)
                batch = data.get("data", []) or []
                out.extend(batch)
                if len(batch) < self.PAGE_SIZE or len(out) >= data.get("total", len(out)):
                    break
                page += 1
            return out

        worker = Worker(fetch_all)
        worker.done.connect(self._write_csv)
        worker.fail.connect(lambda e: (self._reset_export(),
                                       QMessageBox.warning(self, "Export failed", e)))
        self._workers = [w for w in self._workers if w.isRunning()] + [worker]
        worker.start()

    def _reset_export(self):
        self._export_btn.setEnabled(True)
        self._export_btn.setText("Export CSV")

    def _write_csv(self, rows):
        self._reset_export()
        headers, data = self.csv_shape(rows)
        export_csv(self, self.CSV_NAME, headers, data)

    def csv_shape(self, rows): return [], []


# ──────────────────────────────────────────────────────────────────────────────
#  PAGE 2 — Attendance
# ──────────────────────────────────────────────────────────────────────────────

class AttendancePage(_TablePage):
    ENDPOINT = "/attendance/all"
    CSV_NAME = "my_attendance.csv"

    def __init__(self, panel):
        super().__init__(panel, "Attendance",
                         "Your login, logout and working hours.",
                         ["Date", "Login Time", "Logout Time", "Duration"])
        self._summary_cards = None

    def _extra_controls(self, layout):
        self._today = QLabel("")
        self._today.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:12px;border:none;")
        layout.addWidget(self._today)

    def _populate(self):
        rows = self._rows
        self._table.setRowCount(len(rows))
        today_seconds = week_seconds = 0
        now_ist = datetime.now(IST)

        for i, row in enumerate(rows):
            login = parse_ts(row.get("login_time"))
            logout = parse_ts(row.get("logout_time"))
            duration = self._duration(row.get("total_hours"))

            date_text = login.astimezone(IST).strftime("%d %b %Y") if login else "—"
            self._table.setItem(i, 0, QTableWidgetItem(date_text))
            self._table.setItem(i, 1, QTableWidgetItem(
                login.astimezone(IST).strftime("%I:%M:%S %p") if login else "—"))

            if logout:
                self._table.setItem(i, 2, QTableWidgetItem(
                    logout.astimezone(IST).strftime("%I:%M:%S %p")))
            else:
                active = QTableWidgetItem("● ACTIVE")
                active.setForeground(QColor(C.GREEN))
                self._table.setItem(i, 2, active)

            self._table.setItem(i, 3, QTableWidgetItem(
                fmt_duration(duration) if duration else "—"))

            if login and duration:
                local = login.astimezone(IST)
                if local.date() == now_ist.date():
                    today_seconds += duration
                if local.isocalendar()[1] == now_ist.isocalendar()[1]:
                    week_seconds += duration

        self._today.setText(
            f"Today: {fmt_duration(today_seconds)}    ·    This week: {fmt_duration(week_seconds)}"
        )

    @staticmethod
    def _duration(value) -> float:
        """Postgres interval -> seconds. `days` field kabhi drop na ho."""
        if value in (None, "", "None"):
            return 0.0
        if isinstance(value, dict):
            return (value.get("days", 0) * 86400 + value.get("hours", 0) * 3600
                    + value.get("minutes", 0) * 60 + value.get("seconds", 0))
        text = str(value)
        try:
            if text.startswith("{"):
                import ast
                d = ast.literal_eval(text)
                return (d.get("days", 0) * 86400 + d.get("hours", 0) * 3600
                        + d.get("minutes", 0) * 60 + d.get("seconds", 0))
            days = 0
            if "day" in text:
                parts = text.split("day")
                days = int(parts[0].strip())
                text = parts[1].lstrip("s, ").strip()
            h, m, s = text.split(".")[0].split(":")
            return days * 86400 + int(h) * 3600 + int(m) * 60 + int(s)
        except Exception:
            return 0.0

    def csv_shape(self, rows):
        return (
            ["Date", "Login Time (IST)", "Logout Time (IST)", "Duration"],
            [[
                fmt_ist(r.get("login_time"), "%d %b %Y"),
                fmt_ist(r.get("login_time"), "%I:%M:%S %p"),
                fmt_ist(r.get("logout_time"), "%I:%M:%S %p", "ACTIVE"),
                fmt_duration(self._duration(r.get("total_hours"))),
            ] for r in rows],
        )


# ──────────────────────────────────────────────────────────────────────────────
#  PAGE 3 — Activity Logs
# ──────────────────────────────────────────────────────────────────────────────

class LogsPage(_TablePage):
    ENDPOINT = "/logs/all"
    CSV_NAME = "my_activity_logs.csv"
    PAGE_SIZE = 100          # /logs/all LIMIT 100 deta hai

    def __init__(self, panel):
        super().__init__(panel, "Activity Logs",
                         "Everything recorded for your account.",
                         ["Event", "Details", "Time"])

    def _extra_controls(self, layout):
        self._type = QComboBox()
        self._type.addItems(["All events", "Screenshots", "Idle", "Active", "Sessions"])
        self._type.setFixedWidth(150)
        self._type.currentIndexChanged.connect(lambda _: self._populate())
        layout.addWidget(self._type)

    def _params(self):
        # /logs/all pagination support nahi karta — server hamesha latest 100
        # deta hai. Filter client-side hai; `total` phir bhi asli count hai.
        return {}

    def _on_loaded(self, payload):
        self._rows = [r for r in (payload or {}).get("data", [])
                      if is_user_facing(r.get("activity"))]
        self._total = (payload or {}).get("total", len(self._rows))
        self._prev.setEnabled(False)
        self._next.setEnabled(False)
        self._page_label.setText(
            f"Showing latest {len(self._rows)} of {self._total} total events"
        )
        self._panel.mark_server(True)
        self._populate()

    def _filter_rows(self):
        choice = self._type.currentText() if hasattr(self, "_type") else "All events"
        needle = {
            "Screenshots": "SCREENSHOT",
            "Idle": "USER IDLE",
            "Active": "USER ACTIVE",
            "Sessions": "LOGIN",
        }.get(choice)
        rows = self._rows
        if needle:
            rows = [r for r in rows
                    if needle in str(r.get("activity", "")).upper()
                    or (needle == "LOGIN" and "LOGOUT" in str(r.get("activity", "")).upper())]
        if self._filtered:
            target = self._date.date().toString("yyyy-MM-dd")
            rows = [r for r in rows
                    if (parse_ts(r.get("created_at")) or datetime.now(timezone.utc))
                    .astimezone(IST).strftime("%Y-%m-%d") == target]
        return rows

    def _populate(self):
        rows = self._filter_rows()
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            title, subtitle, color = describe(row.get("activity"))
            event = QTableWidgetItem(title)
            event.setForeground(QColor(color))
            self._table.setItem(i, 0, event)
            self._table.setItem(i, 1, QTableWidgetItem(subtitle))
            self._table.setItem(i, 2, QTableWidgetItem(fmt_ist(row.get("created_at"))))

    def csv_shape(self, rows):
        rows = [r for r in rows if is_user_facing(r.get("activity"))]
        return (
            ["Event", "Details", "Raw Activity", "Time (IST)"],
            [[*describe(r.get("activity"))[:2], r.get("activity", ""),
              fmt_ist(r.get("created_at"))] for r in rows],
        )


# ──────────────────────────────────────────────────────────────────────────────
#  PAGE 4 — Screenshots
# ──────────────────────────────────────────────────────────────────────────────

class ScreenshotsPage(_TablePage):
    ENDPOINT = "/screenshots/all"
    CSV_NAME = "my_screenshots.csv"
    PAGE_SIZE = 100

    def __init__(self, panel):
        super().__init__(panel, "Screenshots",
                         "Captures taken during your shifts — encrypted on this device.",
                         ["Captured At", "File", "Status", ""])
        self._preview = None

    def _params(self):
        return {}

    def _on_loaded(self, payload):
        self._rows = (payload or {}).get("data", [])
        self._total = len(self._rows)
        self._prev.setEnabled(False)
        self._next.setEnabled(False)
        self._page_label.setText(f"{self._total} screenshots")
        self._panel.mark_server(True)
        self._populate()

    def _populate(self):
        rows = self._rows
        if self._filtered:
            target = self._date.date().toString("yyyy-MM-dd")
            rows = [r for r in rows
                    if fmt_ist(r.get("created_at"), "%Y-%m-%d", "") == target]

        self._table.setRowCount(len(rows))
        self._table.setColumnWidth(3, 110)
        for i, row in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(fmt_ist(row.get("created_at"))))
            self._table.setItem(i, 1, QTableWidgetItem(str(row.get("file_name", ""))[:44]))
            status = QTableWidgetItem("● Encrypted")
            status.setForeground(QColor(C.GREEN))
            self._table.setItem(i, 2, status)

            view = QPushButton("View")
            view.setStyleSheet(button("secondary"))
            view.setCursor(Qt.CursorShape.PointingHandCursor)
            view.clicked.connect(lambda _=False, r=row: self._open(r))
            self._table.setCellWidget(i, 3, view)

    def _open(self, row):
        self._preview = ScreenshotPreviewWindow(
            screenshot_id=str(row.get("id")),
            employee_id=str(row.get("employee_id", "")),
            timestamp=fmt_ist(row.get("created_at")),
            filename=str(row.get("file_name", "")),
        )
        self._preview.show()

    def csv_shape(self, rows):
        return (["Captured At (IST)", "File"],
                [[fmt_ist(r.get("created_at")), r.get("file_name", "")] for r in rows])


# ──────────────────────────────────────────────────────────────────────────────
#  PAGE 5 — Settings   (read-only; sab admin panel se control hota hai)
# ──────────────────────────────────────────────────────────────────────────────

class SettingsPage(QWidget):
    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        self._rows: dict[str, QLabel] = {}
        self._previous: dict[str, str] = {}
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(PageHeader("Settings",
                                  "Your account and monitoring configuration."))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}" + scrollbar())
        host = QWidget()
        host.setStyleSheet("background:transparent;")
        body = QVBoxLayout(host)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(14)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        body.addWidget(self._section("Account", [
            ("Employee ID", "emp"), ("Name", "name"),
            ("Designation", "desig"), ("Role", "role"),
            ("Shift", "shift"), ("Timezone", "tz"),
        ]))
        body.addWidget(self._section("Monitoring  ·  set by your administrator", [
            ("Screenshots per day", "m_count"),
            ("Capture interval", "m_interval"),
            ("Idle threshold", "m_idle"),
            ("Detailed logging", "m_verbose"),
            ("Settings synced", "m_synced"),
        ], note="Controlled by your administrator. Changes apply automatically — "
                "no restart needed."))
        body.addWidget(self._section("Sync & Storage", [
            ("Server", "server"), ("Pending screenshots", "pending_ss"),
            ("Pending logs", "pending_logs"), ("Last screenshot", "last_ss"),
            ("Data folder", "folder"), ("Space used", "size"),
        ], button_text="Open Data Folder", button_slot=self._open_folder))
        body.addWidget(self._section("Security", [
            ("Encryption", "enc"), ("Client version", "ver"), ("Platform", "plat"),
        ], button_text="Change Password", button_slot=self._change_password,
           note="Screenshots are encrypted on this device before upload — they are "
                "never stored or transmitted as plain images. Changing your password "
                "signs you out on every other device."))
        body.addStretch()

    def _section(self, title, fields, note="", button_text="", button_slot=None):
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(10)

        heading = QLabel(title.upper())
        heading.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:11px;font-weight:700;"
            f"letter-spacing:1px;border:none;"
        )
        layout.addWidget(heading)

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(9)
        for label_text, key in fields:
            row = grid.rowCount()
            name = QLabel(label_text)
            name.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:13px;border:none;")
            value = QLabel("—")
            value.setStyleSheet(f"color:{C.TEXT};font-size:13px;font-weight:600;border:none;")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(name, row, 0)
            grid.addWidget(value, row, 1)
            self._rows[key] = value
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        if button_text:
            row = QHBoxLayout()
            row.addStretch()
            btn = QPushButton(button_text)
            btn.setStyleSheet(button("secondary"))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if button_slot:
                btn.clicked.connect(button_slot)
            row.addWidget(btn)
            layout.addLayout(row)

        if note:
            hint = QLabel(note)
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color:{C.TEXT_DIM};font-size:11px;border:none;")
            layout.addWidget(hint)
        return card

    def _change_password(self):
        from client.presentation.windows.change_password_dialog import (
            ChangePasswordDialog,
        )
        dialog = ChangePasswordDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            QMessageBox.information(
                self, "Password changed",
                "Your password has been changed.\n\n"
                "Any other device you were signed in on has been signed out.",
            )

    def _open_folder(self):
        import subprocess, sys as _sys
        try:
            if _sys.platform == "darwin":
                subprocess.Popen(["open", STORAGE_DIR])
            elif os.name == "nt":
                os.startfile(STORAGE_DIR)          # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", STORAGE_DIR])
        except Exception as error:
            QMessageBox.warning(self, "Could not open folder", str(error))

    def refresh(self):
        def put(key, text, color=None):
            label = self._rows.get(key)
            if not label:
                return
            label.setText(str(text))
            label.setStyleSheet(
                f"color:{color or C.TEXT};font-size:13px;font-weight:600;border:none;"
            )

        setting = lambda k, d="—": (SettingsService.get_setting(k) or d)

        put("emp", SessionManager.employee_id or "—")
        put("name", getattr(SessionManager, "full_name", None) or "—")
        put("desig", getattr(SessionManager, "designation", None) or "—")
        put("role", {"super_admin": "Super Admin", "admin": "Admin"}
            .get(SessionManager.role, "Employee"))
        put("shift", self._panel.shift_text())
        put("tz", "IST (Asia/Kolkata)")

        put("m_count", setting("screenshots_per_day"))
        mn, mx = setting("screenshot_min_minutes"), setting("screenshot_max_minutes")
        put("m_interval", f"{mn} – {mx} min" if mn != "—" and mx != "—" else "—")
        idle = setting("idle_threshold_seconds")
        put("m_idle", f"{idle} sec" if idle != "—" else "—")
        verbose = str(setting("verbose_logging", "false")).lower() == "true"
        put("m_verbose", "On" if verbose else "Off", C.AMBER if verbose else C.TEXT_MUTED)

        synced = setting("config_last_synced", "")
        if synced and synced != "—":
            try:
                delta = (datetime.now() - datetime.fromisoformat(synced)).total_seconds()
                put("m_synced",
                    "just now" if delta < 90 else
                    f"{int(delta // 60)} min ago" if delta < 3600 else
                    datetime.fromisoformat(synced).strftime("%d %b, %I:%M %p"),
                    C.GREEN if delta < 3600 else C.AMBER)
            except Exception:
                put("m_synced", synced)
        else:
            put("m_synced", "waiting for first sync…", C.AMBER)

        put("server", API_BASE_URL)
        stats = self._local_stats()
        put("pending_ss", stats["pending_ss"],
            C.AMBER if stats["pending_ss"] else C.GREEN)
        put("pending_logs", stats["pending_logs"],
            C.AMBER if stats["pending_logs"] else C.GREEN)
        put("last_ss", stats["last_ss"] or "none yet")
        put("folder", STORAGE_DIR)
        put("size", stats["size"])

        try:
            from client.security.crypto_engine import CryptoEngine  # noqa: F401
            put("enc", "AES-256-GCM · active", C.GREEN)
        except Exception as error:
            put("enc", f"unavailable — {error}", C.RED)

        import platform
        put("ver", f"ETS Client v{APP_VERSION}")
        put("plat", f"{platform.system()} {platform.release()}")

        self._flash()

    def _local_stats(self):
        out = {"pending_ss": 0, "pending_logs": 0, "last_ss": None, "size": "—"}
        try:
            conn = Database.connect()
            cur = conn.cursor()
            emp = SessionManager.employee_id
            cur.execute("SELECT COUNT(*) FROM screenshots WHERE employee_id=? AND uploaded=0", (emp,))
            out["pending_ss"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM pending_logs WHERE employee_id=? AND uploaded=0", (emp,))
            out["pending_logs"] = cur.fetchone()[0]
            cur.execute("SELECT timestamp FROM screenshots WHERE employee_id=? "
                        "ORDER BY timestamp DESC LIMIT 1", (emp,))
            row = cur.fetchone()
            conn.close()
            if row:
                out["last_ss"] = str(row[0])
        except Exception:
            pass
        try:
            total = sum(
                os.path.getsize(os.path.join(root, f))
                for root, _d, files in os.walk(STORAGE_DIR) for f in files
                if os.path.exists(os.path.join(root, f))
            )
            value = float(total)
            for unit in ("B", "KB", "MB", "GB"):
                if value < 1024 or unit == "GB":
                    out["size"] = f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
                    break
                value /= 1024
        except Exception:
            pass
        return out

    def _flash(self):
        """Admin ne kuch badla to employee ko dikhe — 2.5s green highlight."""
        changed = [k for k, lbl in self._rows.items()
                   if k in self._previous and self._previous[k] != lbl.text()]
        for key, lbl in self._rows.items():
            self._previous[key] = lbl.text()
        for key in changed:
            label = self._rows[key]
            base = label.styleSheet()
            label.setStyleSheet(
                f"color:{C.GREEN};font-size:13px;font-weight:700;"
                f"background:{C.GREEN_BG};border-radius:4px;padding:1px 6px;"
            )
            QTimer.singleShot(2500, lambda l=label, s=base: l.setStyleSheet(s))


# ──────────────────────────────────────────────────────────────────────────────
#  PAGE 6 — Help & Support
# ──────────────────────────────────────────────────────────────────────────────

class HelpPage(QWidget):
    FAQS = [
        ("Why are screenshots taken?",
         "Your organisation uses ETS to record work activity during your shift. "
         "Captures happen at random moments within your configured shift window."),
        ("Are my screenshots private?",
         "Every capture is encrypted with AES-256-GCM on this device before it "
         "leaves your machine. Plain images are never written to disk or sent "
         "over the network."),
        ("Am I tracked outside my shift?",
         "Capture follows the shift your administrator configured. If you sign in "
         "outside those hours while working, activity is still recorded so your "
         "time is credited."),
        ("What counts as idle?",
         "No keyboard or mouse input for the idle threshold shown in Settings. "
         "Moving the mouse or typing marks you active again immediately."),
        ("What if I lose internet?",
         "Nothing is lost. Screenshots and logs queue locally and upload "
         "automatically when the connection returns — check 'Pending' in Settings."),
        ("Can I change my own monitoring settings?",
         "No. Shift timings, capture frequency and idle threshold are controlled "
         "by your administrator. You can always see the current values in Settings."),
    ]

    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(PageHeader("Help & Support",
                                  "How ETS works and who to contact."))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}" + scrollbar())
        host = QWidget()
        host.setStyleSheet("background:transparent;")
        body = QVBoxLayout(host)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(12)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        for question, answer in self.FAQS:
            card = Card()
            layout = QVBoxLayout(card)
            layout.setContentsMargins(18, 14, 18, 14)
            layout.setSpacing(6)
            q = QLabel(question)
            q.setStyleSheet(f"color:{C.TEXT};font-size:14px;font-weight:700;border:none;")
            a = QLabel(answer)
            a.setWordWrap(True)
            a.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:13px;border:none;")
            layout.addWidget(q)
            layout.addWidget(a)
            body.addWidget(card)

        contact = Card()
        layout = QVBoxLayout(contact)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(6)
        heading = QLabel("Need help?")
        heading.setStyleSheet(f"color:{C.TEXT};font-size:14px;font-weight:700;border:none;")
        text = QLabel(
            "Contact your system administrator for anything related to your shift "
            "timings, monitoring settings or account access. Include your Employee "
            "ID and the time the issue occurred."
        )
        text.setWordWrap(True)
        text.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:13px;border:none;")
        layout.addWidget(heading)
        layout.addWidget(text)
        body.addWidget(contact)
        body.addStretch()

    def refresh(self): ...


# ──────────────────────────────────────────────────────────────────────────────
#  MAIN PANEL
# ──────────────────────────────────────────────────────────────────────────────

class EmployeePanel(QWidget):
    """Employee ka poora workspace — sidebar + pages + tracking services."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Amaze Connect")
        self.resize(1320, 880)
        self.setMinimumSize(1060, 700)
        self.setStyleSheet(app_style())

        self._force_close = False
        self._last_mouse = QCursor.pos()
        self._session_start: datetime | None = None
        self._server_ok = True
        self._online = True
        self._latency_ms: int | None = None

        self._load_session_start()
        # Built BEFORE _build(), and never inside it: switching the theme
        # rebuilds the pages, and a ChatManager created in there would be
        # replaced each time — leaving the old one polling with the panel's
        # signals still attached to it.
        self.chat = ChatManager(self)
        self._build()
        self._start_services()
        self._start_timers()

    # ── layout ──────────────────────────────────────────────────────────
    def _build(self):
        # Re-runnable: switching the theme calls this again so every widget is
        # constructed with the new palette. Anything left over from the
        # previous pass has to go first, or the old dark widgets stay stacked
        # underneath the new ones.
        existing = self.layout()
        if existing is not None:
            while existing.count():
                item = existing.takeAt(0)
                # Held once. setParent(None) detaches the widget from the
                # layout item, so a second item.widget() returns None — the
                # kind of thing that only shows up the first time the teardown
                # actually runs.
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()

            # The old layout has to be DETACHED, not merely scheduled for
            # deletion.
            #
            # BUG this fixes: deleteLater() queues the delete for later, so at
            # this moment the layout is still installed — and Qt then refuses
            # the new one outright:
            #
            #   QLayout: Attempting to add QLayout to EmployeePanel,
            #            which already has a layout
            #
            # The refusal is a console warning, not an exception. The panel
            # kept its old, now-empty layout and every rebuilt widget was
            # parented but never placed, so switching the theme produced a
            # completely blank window.
            #
            # Handing the layout to a throwaway widget transfers ownership
            # immediately, which is the only way to be rid of it here.
            QWidget().setLayout(existing)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._sidebar())

        right = QWidget()
        right.setStyleSheet(f"background:{C.BG};")
        col = QVBoxLayout(right)
        col.setContentsMargins(26, 20, 26, 20)
        col.setSpacing(18)
        col.addWidget(self._header())

        self._stack = QStackedWidget()
        self.pages = {
            "dashboard": DashboardPage(self),
            "team": TeamPage(self, self.chat),
            "attendance": AttendancePage(self),
            "logs": LogsPage(self),
            "screenshots": ScreenshotsPage(self),
            "profile": ProfilePage(self),
            "settings": SettingsPage(self),
            "help": HelpPage(self),
        }
        self.pages["team"].unread_changed.connect(self._on_chat_unread)
        for page in self.pages.values():
            self._stack.addWidget(page)
        col.addWidget(self._stack, 1)

        root.addWidget(right, 1)

        # Ab pages maujood hain — pehla paint safe hai.
        self._tick_clock()
        self._paint_status()
        # IdleTracker sirf STATE CHANGE pe signal bhejta hai, is liye pehla
        # signal aane tak Activity card khaali ("—") dikhta tha. Login ke
        # turant baad employee active hi hota hai — wahi initial state.
        self._on_idle("ACTIVE")
        self.go("dashboard")

    def _sidebar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedWidth(248)
        bar.setStyleSheet(f"background:{C.SIDEBAR};border-right:1px solid {C.BORDER};")
        col = QVBoxLayout(bar)
        col.setContentsMargins(16, 22, 16, 16)
        col.setSpacing(6)

        brand = QHBoxLayout()
        brand.setSpacing(12)
        logo = QLabel("🛡")
        logo.setFixedSize(44, 44)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet(
            f"background:{C.PRIMARY_DIM};border-radius:12px;font-size:20px;border:none;"
        )
        names = QVBoxLayout()
        names.setSpacing(0)
        title = QLabel("AMAZE")
        title.setStyleSheet(
            f"color:{C.PRIMARY};font-size:19px;font-weight:800;"
            f"letter-spacing:1px;border:none;"
        )
        sub = QLabel("CONNECT")
        sub.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:9px;font-weight:700;"
            f"letter-spacing:1.4px;border:none;"
        )
        names.addWidget(title)
        names.addWidget(sub)
        brand.addWidget(logo)
        brand.addLayout(names)
        brand.addStretch()
        col.addLayout(brand)
        col.addSpacing(24)

        self._nav: dict[str, NavButton] = {}
        for key, icon, label in (
            ("dashboard", "🏠", "Dashboard"),
            ("team", "💬", "Team"),
            ("attendance", "📅", "Attendance"),
            ("logs", "📋", "Activity Logs"),
            ("screenshots", "📷", "Screenshots"),
            ("profile", "👤", "My Profile"),
            ("settings", "⚙", "Settings"),
            ("help", "❓", "Help & Support"),
        ):
            btn = NavButton(icon, label)
            btn.clicked.connect(lambda _=False, k=key: self.go(k))
            col.addWidget(btn)
            self._nav[key] = btn

        col.addStretch()

        footer = Card()
        footer.setStyleSheet(
            f"QFrame{{background:{C.CARD};border:1px solid {C.BORDER};"
            f"border-radius:{R_SM}px;}}"
        )
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(14, 12, 14, 12)
        fl.setSpacing(7)
        ver = QLabel(f"ETS Client v{APP_VERSION}")
        ver.setStyleSheet(f"color:{C.TEXT};font-size:12px;font-weight:700;border:none;")
        self._srv_label = QLabel("● Connected to Server")
        self._srv_label.setStyleSheet(f"color:{C.GREEN};font-size:11px;border:none;")
        enc = QLabel("🔒 AES-256 GCM Encrypted")
        enc.setStyleSheet(f"color:{C.TEXT_DIM};font-size:11px;border:none;")
        fl.addWidget(ver)
        fl.addWidget(self._srv_label)
        fl.addWidget(enc)
        col.addWidget(footer)

        logout = QPushButton("  ⏻   Logout")
        logout.setStyleSheet(button("danger"))
        logout.setCursor(Qt.CursorShape.PointingHandCursor)
        logout.setFixedHeight(42)
        logout.clicked.connect(self.logout)
        col.addSpacing(8)
        col.addWidget(logout)
        return bar

    def _header(self) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet("background:transparent;")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)

        # THE WHOLE CORNER OPENS THE PROFILE. Asked for after the page
        # existed: the first thing anybody clicks to find their account is
        # their own name and picture, not an entry further down a menu.
        # Their own face, not a generic 👤 glyph — the picture somebody
        # uploaded should be the first place it shows up.
        avatar = ClickableAvatar(56)
        avatar.setToolTip("My Profile")
        avatar.show_person(
            SessionManager.employee_id,
            getattr(SessionManager, "full_name", None) or SessionManager.employee_id)
        avatar.clicked.connect(lambda: self.go("profile"))
        self._header_avatar = avatar

        who = QVBoxLayout()
        who.setSpacing(2)
        self._name = QLabel(getattr(SessionManager, "full_name", None)
                            or SessionManager.employee_id or "Employee")
        self._name.setStyleSheet(f"color:{C.TEXT};font-size:21px;font-weight:800;")
        designation = getattr(SessionManager, "designation", None) or "Employee"
        self._sub = QLabel(f"{SessionManager.employee_id}  ·  {designation}")
        self._sub.setStyleSheet(f"color:{C.PRIMARY};font-size:13px;font-weight:600;")
        who.addWidget(self._name)
        who.addWidget(self._sub)

        # The name and the line under it are part of the same target.
        for label in (self._name, self._sub):
            label.setCursor(Qt.CursorShape.PointingHandCursor)
            label.setToolTip("My Profile")
            label.mousePressEvent = (
                lambda _event, _self=self: _self.go("profile"))

        self._status_chip = QLabel()
        self._status_chip.setFixedHeight(56)
        self._status_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._clock = QLabel()
        self._clock.setFixedHeight(56)
        self._clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._clock.setStyleSheet(
            f"background:{C.CARD};border:1px solid {C.BORDER};border-radius:{R_SM}px;"
            f"padding:0 20px;font-size:13px;font-weight:600;color:{C.TEXT};"
        )

        self._theme_btn = QPushButton()
        self._theme_btn.setFixedSize(56, 56)
        self._theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._paint_theme_button()
        self._theme_btn.clicked.connect(self._toggle_theme)

        row.addWidget(avatar)
        row.addLayout(who)
        row.addStretch()
        row.addWidget(self._theme_btn)
        row.addWidget(self._status_chip)
        row.addWidget(self._clock)
        # _tick_clock()/_paint_status() yahan NAHI — dono `self.pages` padhte
        # hain jo is waqt bana nahi hota. _build() ke aakhir me paint hota hai.
        return wrap

    # ── theme ───────────────────────────────────────────────────────────
    def _paint_theme_button(self):
        light = _theme.is_light()
        self._theme_btn.setText("☀" if light else "☾")
        self._theme_btn.setToolTip(
            "Switch to the dark theme" if light else "Switch to the light theme")
        self._theme_btn.setStyleSheet(
            f"QPushButton {{ background:{C.CARD};border:1px solid {C.BORDER};"
            f"border-radius:{R_SM}px;font-size:20px;color:{C.TEXT}; }}"
            f"QPushButton:hover {{ border-color:{C.PRIMARY}; }}")

    def _toggle_theme(self):
        """Switch palette, then build the whole panel again.

        Rebuilding rather than restyling is deliberate — see theme.py. Every
        widget in here bakes its colours in when it is constructed, so the only
        way to be sure none was missed is to construct them all afresh.
        """
        current = self._stack.currentWidget()
        page_key = next((k for k, v in self.pages.items() if v is current), "dashboard")
        _theme.toggle_theme()
        self.setStyleSheet(app_style())
        self._teardown_pages()
        self._build()
        self.go(page_key)

        # Repaint the cards the TIMERS own, rather than leaving them blank
        # until the next tick.
        #
        # _check_network runs every fifteen seconds and was only ever called
        # once, at startup. After a rebuild the fresh cards showed "—" until
        # that timer came round — up to fifteen seconds of an empty Internet
        # Status card, which reads as the switch having broken something.
        # Nothing was wrong; the panel simply had nothing to draw yet.
        if hasattr(self, "_check_network"):
            self._check_network()

    def _teardown_pages(self):
        """Stop what the old pages were doing before they are thrown away.

        Their timers would otherwise keep firing into deleted widgets, and a
        QThread destroyed while still running takes the application down — the
        same hazard the admin console's shutdown path already guards against.
        """
        for page in getattr(self, "pages", {}).values():
            for attr in ("_member_timer", "_teams_timer", "_refresh_timer"):
                timer = getattr(page, attr, None)
                if timer is not None:
                    try:
                        timer.stop()
                    except Exception:
                        pass
            for worker in list(getattr(page, "_workers", []) or []):
                try:
                    if worker.isRunning():
                        worker.wait(300)
                except Exception:
                    pass

        # The panel's own probe, which belongs to no page.
        probe = getattr(self, "_net_worker", None)
        if probe is not None:
            try:
                if probe.isRunning():
                    probe.wait(500)
            except Exception:
                pass

    # ── navigation ──────────────────────────────────────────────────────
    def go(self, key: str):
        page = self.pages.get(key)
        if not page:
            return
        self._stack.setCurrentWidget(page)
        for name, btn in self._nav.items():
            btn.setChecked(name == key)
        if hasattr(page, "refresh"):
            page.refresh()

    # ── chat ────────────────────────────────────────────────────────────
    def _on_chat_unread(self, total: int):
        nav = getattr(self, "_nav", {}).get("team")
        if nav is not None:
            nav.set_badge(total)

    def _on_chat_messages(self, arrived: list):
        # Switched off for messages on this machine — see My Profile.
        if not notifier.pref_enabled(notifier.PREF_CHAT):
            return

        """Announce what arrived, and say who it was from and where.

        The deciding is in application/services/notifier — what to show, what
        to stay quiet about, and how it should be worded. Only the showing is
        here, because that part is Qt and the other part is the part that makes
        this either useful or the first thing everybody switches off.
        """
        if not arrived:
            return

        team_page = self.pages.get("team")
        looking_at = getattr(team_page, "_channel_id", None) if team_page else None
        # Focus, not just "the page is on screen". The same conversation left
        # open behind a browser window is not being read.
        on_top = self.isActiveWindow() and self._stack.currentWidget() is team_page

        for item in notifier.collapse(notifier.for_messages(
                arrived,
                me=SessionManager.employee_id,
                open_channel_id=looking_at,
                window_active=on_top,
                channel_names=self._channel_names(),
                direct_channel_ids=self._direct_channel_ids())):
            self._notify(item["title"], item["body"], kind=item["kind"])

        if team_page:
            team_page.refresh()

    def _channel_names(self) -> dict:
        """Channel id to what it should be called in a notification."""
        names = {}
        team_page = self.pages.get("team")
        for team in getattr(team_page, "_teams", []) or []:
            for channel in team.get("channels") or []:
                names[channel["id"]] = f"#{channel['name']}"
        for direct in getattr(team_page, "_directs", []) or []:
            names[direct["channel_id"]] = (direct.get("with") or {}).get("name") or ""
        return names

    def _direct_channel_ids(self) -> set:
        team_page = self.pages.get("team")
        return {d["channel_id"] for d in getattr(team_page, "_directs", []) or []}

    def _on_chat_notifications(self, alerts: list):
        # Switched off for alerts on this machine — see My Profile.
        if not notifier.pref_enabled(notifier.PREF_ALERTS):
            return

        """Announcements for everybody; the rest only for administrators."""
        for item in notifier.collapse(notifier.for_alerts(
                alerts, role=getattr(SessionManager, "role", "employee"))):
            self._notify(item["title"], item["body"], kind=item["kind"])

    def _notify(self, title: str, body: str, kind: str = notifier.NORMAL):
        # One delivery path for both panels, in notifier, because the reason
        # macOS showed nothing is a platform detail that has no business
        # being duplicated in two windows.
        notifier.deliver(getattr(self, "tray", None), title, body)

    # ── services ────────────────────────────────────────────────────────
    def _load_session_start(self):
        try:
            conn = Database.connect()
            cur = conn.cursor()
            cur.execute(
                "SELECT login_time FROM shifts WHERE employee_id = ? ORDER BY id DESC LIMIT 1",
                (SessionManager.employee_id,),
            )
            row = cur.fetchone()
            conn.close()
            if row:
                self._session_start = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        except Exception as error:
            print("[PANEL] session start load failed:", error)

    def _start_services(self):
        self.scheduler = SchedulerService()
        self.scheduler.screenshot_triggered.connect(self._capture)
        if hasattr(self.scheduler, "force_logout"):
            self.scheduler.force_logout.connect(self.logout)
        self.scheduler.start()

        self.idle_tracker = IdleTracker()
        self.idle_tracker.status_changed.connect(self._on_idle)
        self.idle_tracker.start()

        self.tray = SystemTray(self)
        self.tray.show()
        self.tray.show_message()

        # Chat polls whether or not the Team page is open — otherwise the
        # badge on the sidebar would only ever update while somebody was
        # already looking at the thing it is meant to draw them to.
        self.chat.messages.connect(self._on_chat_messages)
        self.chat.notifications.connect(self._on_chat_notifications)
        # The chat poll runs every few seconds — far sooner than the config
        # sync — so it is the first thing to learn that a forced logout has
        # happened. Reported live: the panel stayed open and "ONLINE" after an
        # administrator ended the session.
        self.chat.session_ended.connect(self.logout)
        self.chat.start()

    def _start_timers(self):
        self._timers: list[QTimer] = []

        def every(ms, slot):
            timer = QTimer(self)
            timer.timeout.connect(slot)
            timer.start(ms)
            self._timers.append(timer)
            return timer

        every(1000, self._tick_clock)
        every(1000, self._track_mouse)
        every(15000, self._check_network)
        every(30000, self._refresh_current)
        self._check_network()

    def _capture(self):
        result = ScreenshotManager.capture_screenshot()
        if result:
            self.pages["dashboard"].c_shots.push_point(1)
        self._refresh_current()

    def _on_idle(self, status: str):
        idle = status == "IDLE"
        if hasattr(self, "tray"):
            self.tray.set_status("idle" if idle else "active")
        card = self.pages["dashboard"].c_activity
        card.set_value("IDLE" if idle else "WORKING", C.AMBER if idle else C.BLUE)
        card.set_subtitle("No input detected" if idle else "Keyboard & mouse active")
        card.push_point(0 if idle else 1)

    def _track_mouse(self):
        position = QCursor.pos()
        if position != self._last_mouse:
            self._last_mouse = position
            if hasattr(self, "idle_tracker") and self.idle_tracker:
                self.idle_tracker.reset_activity()

    def _tick_clock(self):
        now = datetime.now()
        # Mockup ke header me alag clock widget nahi hai (uski jagah user chip
        # hai) — is liye clock optional hai. Ye timer ab bhi har second chalta
        # hai kyunki Session Duration isi se update hoti hai.
        clock = getattr(self, "_clock", None)
        if clock is not None:
            clock.setText(now.strftime("%I:%M:%S %p\n%a, %b %d, %Y"))

        card = self.pages["dashboard"].c_session
        if self._session_start:
            elapsed = (now - self._session_start).total_seconds()
            card.set_value(fmt_duration(elapsed))
            card.set_subtitle(f"Started at {self._session_start.strftime('%I:%M %p')}")
        else:
            card.set_value("00:00:00")

    def _check_network(self):
        # One probe at a time.
        #
        # BUG this fixes: the running worker was held in a single attribute,
        # so starting another dropped the only reference to the previous one.
        # Python then collected a QThread that was still running, which in Qt
        # is not an error but an abort — the whole application goes.
        #
        # It never showed up while this was called on a fifteen-second timer
        # and the probe timed out after six. Calling it after a theme switch
        # made two overlap, and the process died instantly.
        existing = getattr(self, "_net_worker", None)
        if existing is not None and existing.isRunning():
            return

        def probe():
            start = datetime.now()
            _http.get(f"{API_BASE_URL}/health", timeout=6)
            return int((datetime.now() - start).total_seconds() * 1000)

        worker = Worker(probe)
        worker.done.connect(self._on_network_ok)
        worker.fail.connect(lambda _e: self._on_network_fail())
        self._net_worker = worker
        worker.start()

    def _on_network_ok(self, latency_ms: int):
        self._online = True
        self._latency_ms = latency_ms
        self.mark_server(True)
        card = self.pages["dashboard"].c_internet
        card.set_value("CONNECTED", C.CYAN)
        card.set_subtitle(f"Stable connection  ·  Latency: {latency_ms} ms")
        card.push_point(max(1, 400 - min(latency_ms, 400)))
        self._paint_status()

    def _on_network_fail(self):
        self._online = False
        self.mark_server(False)
        card = self.pages["dashboard"].c_internet
        card.set_value("OFFLINE", C.RED)
        card.set_subtitle("Data will sync when the connection returns")
        card.push_point(0)
        self._paint_status()

    def mark_server(self, ok: bool):
        self._server_ok = ok
        self._srv_label.setText("● Connected to Server" if ok else "● Server unreachable")
        self._srv_label.setStyleSheet(
            f"color:{C.GREEN if ok else C.RED};font-size:11px;border:none;"
        )
        card = self.pages["dashboard"].c_upload
        if ok:
            card.set_value("SYNCED", C.PURPLE)
            card.set_subtitle(f"All data uploaded  ·  {datetime.now():%I:%M:%S %p}")
            card.push_point(1)
        else:
            card.set_value("QUEUED", C.AMBER)
            card.set_subtitle("Buffered locally — will upload automatically")
            card.push_point(0)

    def _paint_status(self):
        tracking = getattr(self, "scheduler", None) is not None
        online = self._online and self._server_ok
        colour = C.GREEN if online else C.AMBER
        text = "● ONLINE\nTracking Active" if online else "● OFFLINE\nBuffering locally"
        chip = getattr(self, "_status_chip", None)
        if chip is not None:
            chip.setText(text)
            chip.setStyleSheet(
                f"background:{C.CARD};border:1px solid {C.BORDER};border-radius:{R_SM}px;"
                f"padding:0 20px;font-size:12px;font-weight:700;color:{colour};"
            )
        card = self.pages["dashboard"].c_tracking
        card.set_value("ACTIVE" if tracking else "PAUSED",
                       C.GREEN if tracking else C.AMBER)
        card.set_subtitle("System is monitoring" if tracking else "Tracking paused")
        card.push_point(1 if tracking else 0)

    def _refresh_current(self):
        if SessionManager.is_token_expired():
            # Named, so this is never confused with somebody closing the app.
            self.logout("the session had expired")
            return
        page = self._stack.currentWidget()
        if hasattr(page, "refresh"):
            page.refresh()

    def shift_text(self) -> str:
        start, end = SessionManager.shift_start, SessionManager.shift_end
        if not start or not end:
            start = start or SettingsService.get_setting("shift_start_ist")
            end = end or SettingsService.get_setting("shift_end_ist")
        if not start or not end:
            return "not configured"

        def hhmm(value):
            text = str(value)
            try:
                return datetime.fromisoformat(text).strftime("%H:%M")
            except Exception:
                return text[11:16] if len(text) >= 16 else text[:5]

        return f"{hhmm(start)} – {hhmm(end)} IST"

    # ── lifecycle ───────────────────────────────────────────────────────
    def closeEvent(self, event):
        if self._force_close:
            event.accept()
            return
        event.ignore()
        self.hide()

    def _drain_workers(self, timeout_ms: int = 3000) -> int:
        """
        Saare pages ke chal rahe network workers ka intezaar karo.

        BUG FIX: pehle logout pe sirf timers/scheduler band hote the; chal
        rahe QThread workers ko chhod diya jaata tha. Qt me agar QThread
        object destroy ho jaye jab thread abhi chal raha ho, to Qt
        "QThread: Destroyed while thread is still running" ke saath
        std::terminate() call karta hai — app turant crash. Slow ya down
        server pe (jahan request 10-30s tak latak sakti hai) logout dabate
        hi crash ka poora chance banta tha.

        Ab har worker ko bounded wait dete hain. Timeout ke baad bhi na ruke
        to usse chhod dete hain (terminate() nahi karte — wo aur khatarnak
        hai); us waqt tak uske callbacks disconnect ho chuke hote hain.
        """
        pending = []
        for page in getattr(self, "pages", {}).values():
            pending.extend(getattr(page, "_workers", []) or [])
        for attr in ("_net_worker", "_shot_worker", "_sync_worker"):
            worker = getattr(self, attr, None)
            if worker is not None:
                pending.append(worker)

        still_running = 0
        for worker in pending:
            try:
                if not worker.isRunning():
                    continue
                # Callbacks pehle kaato — warna finished/error signal ek
                # aadhe-destroyed widget pe pahunch sakta hai.
                for signal in ("done", "fail", "finished", "error"):
                    sig = getattr(worker, signal, None)
                    if sig is not None:
                        try:
                            sig.disconnect()
                        except (RuntimeError, TypeError):
                            pass
                worker.requestInterruption()
                if not worker.wait(timeout_ms):
                    still_running += 1
            except RuntimeError:
                # Worker already delete ho chuka — koi baat nahi.
                pass
        return still_running

    def _stop_everything(self):
        # Workers pehle — timers/scheduler band karne se pehle, taaki koi
        # naya worker start na ho aur chal rahe complete ho jayen.
        for timer in getattr(self, "_timers", []):
            try:
                timer.stop()
            except Exception:
                pass
        leftover = self._drain_workers()
        if leftover:
            print(f"[PANEL] {leftover} worker(s) timeout ke baad bhi chal rahe hain")

        for timer in getattr(self, "_timers", []):
            try:
                timer.stop()
            except Exception:
                pass
        chat = getattr(self, "chat", None)
        if chat is not None:
            try:
                chat.stop()
            except Exception:
                pass

        for attr in ("scheduler", "idle_tracker"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.stop()
                    obj.deleteLater()
                except Exception:
                    pass
                setattr(self, attr, None)
        tray = getattr(self, "tray", None)
        if tray is not None:
            try:
                tray.hide()
                tray.deleteLater()
            except Exception:
                pass
            self.tray = None

    def logout(self, reason: str = ""):
        """Sign out. `reason` is set when the SERVER ended the session.

        force_logout carries it — suspension, or an admin's force logout.
        Without showing it the app just returns to the login screen for no
        stated cause, which reads as a crash and gets reported as one.
        """
        # Both the chat poll and the config sync can notice the same forced
        # logout within a second of each other. One sign-out, one message box.
        #
        # AND IT LETS GO IF THE SIGN-OUT DOES NOT FINISH — otherwise anything
        # raising below leaves the flag set and every later click of Logout
        # returns at this line, a button that does nothing until the app is
        # restarted.
        if getattr(self, "_signing_out", False):
            return
        self._signing_out = True
        try:
            self._do_logout(reason)
        except Exception as error:
            self._signing_out = False
            try:
                LoggerService.log(f"LOGOUT FAILED : {error}")
            except Exception:
                pass
            raise

    def _do_logout(self, reason: str = ""):

        if reason:
            try:
                QMessageBox.warning(self, "Signed out", reason)
            except Exception:
                pass
        # TELL THE SERVER FIRST, while the token is still valid.
        #
        # Nothing did. /auth/logout was reached only from the tray's Exit, so
        # this button cleared the session locally and left it open on the
        # server — and once one login at a time became strict, signing out and
        # straight back in was refused for two minutes.
        #
        # Skipped when the server is the one that ended it: force logout and
        # suspension have already cleared the row, and the token is dead.
        if not reason:
            AuthService.sign_out_on_server()

        # LOGOUT clear_session() se PEHLE log hota hai — warna employee_id
        # None ho jaata hai aur LoggerService chup-chaap drop kar deta hai.
        #
        # AND IT SAYS WHICH WAY IT HAPPENED.
        #
        # It used to log the bare word "LOGOUT" from four different paths: the
        # button, the tray's Exit, a session the server had ended, and the
        # token expiring. On the server they are indistinguishable, and the
        # lines that would have told them apart — "ChatManager: stopped" and
        # the rest — are logged AFTER this one and never reach the server,
        # because the app stops before the queue is uploaded.
        #
        # So an administrator seeing a LOGOUT a minute after a LOGIN has no
        # way to tell "they closed it" from "it signed itself out", which is
        # exactly the question that came up and could not be answered from
        # three days of logs.
        try:
            LoggerService.log(f"LOGOUT : {reason or 'signed out from the panel'}")
        except Exception:
            pass
        for fn in (SessionLogManager.end_session, ShiftManager.end_shift):
            try:
                fn()
            except Exception as error:
                print("[PANEL] logout step failed:", error)

        self._stop_everything()
        SessionManager.clear_session()

        from client.presentation.windows.login_window import LoginWindow
        self._force_close = True
        self.close()
        self.deleteLater()
        self.login_window = LoginWindow()
        self.login_window.show()
