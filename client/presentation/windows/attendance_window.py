import csv
import threading
from datetime import datetime

import requests
from PySide6.QtCore import QMetaObject, Qt, Slot
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QHeaderView, QPushButton,
    QFileDialog, QMessageBox,
)

from client.application.managers.session_manager import SessionManager
from client.core.config import API_BASE_URL
from client.presentation.widgets.status_card import StatusCard
from client.presentation.windows.base_window import BaseWindow


class AttendanceWindow(BaseWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Attendance History")
        self.resize(1000, 650)
        self._shifts_data = []
        self.setup_ui()
        # ✅ Background load — UI freeze nahi
        threading.Thread(target=self._fetch_data, daemon=True).start()

    def setup_ui(self):
        layout = QVBoxLayout()

        title = QLabel("Attendance History")
        title.setStyleSheet(
            "font-size: 30px; font-weight: bold; "
            "color: white; margin-bottom: 15px;"
        )

        cards_layout = QHBoxLayout()
        self.today_card = StatusCard("Today",      "0h 0m")
        self.week_card  = StatusCard("This Week",  "0h 0m")
        self.month_card = StatusCard("This Month", "0h 0m")
        cards_layout.addWidget(self.today_card)
        cards_layout.addWidget(self.week_card)
        cards_layout.addWidget(self.month_card)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels([
            "Employee", "Login Time", "Logout Time", "Duration"
        ])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)

        export_btn = QPushButton("📥 Export CSV")
        export_btn.setStyleSheet("""
        QPushButton {
            background-color: #16a34a; color: white;
            border-radius: 10px; padding: 10px; font-weight: bold;
        }
        QPushButton:hover { background-color: #15803d; }
            """)
        export_btn.clicked.connect(self.export_csv)
        layout.addWidget(title)
        layout.addLayout(cards_layout)
        layout.addWidget(export_btn)
        layout.addWidget(self.table)
        self.setLayout(layout)
    @staticmethod
    def _format_hours(seconds: int) -> str:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m"

    def _fetch_data(self):
        """Background thread mein API call."""
        try:
            response = requests.get(
                f"{API_BASE_URL}/attendance/all",
                headers={
                    "Authorization": f"Bearer {SessionManager.auth_token}"
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json().get("data", [])
        except Exception as e:
            print(f"[ATTENDANCE LOAD ERROR] {e}")
            data = []

            self._shifts_raw = data
            QMetaObject.invokeMethod(
                self, "_apply_data",
                Qt.ConnectionType.QueuedConnection,
            )

    @Slot()
    def _apply_data(self):
        data = getattr(self, "_shifts_raw", [])

        today_sec = week_sec = month_sec = 0
        now = datetime.now()

        self.table.setRowCount(len(data))

        for row, shift in enumerate(data):
            employee    = str(shift.get("employee_id", ""))
            login_str   = str(shift.get("login_time",  ""))
            logout_str  = str(shift.get("logout_time", ""))
            # ✅ total_seconds integer — ShiftManager fix ke baad
            total_sec   = shift.get("total_seconds", 0) or 0

            # Summary cards
            try:
                shift_dt = datetime.fromisoformat(
                    login_str.replace("Z", "")
                )
                if shift_dt.date() == now.date():
                    today_sec += total_sec
                    if shift_dt.isocalendar()[1] == now.isocalendar()[1]:
                        week_sec += total_sec
                        if (shift_dt.month == now.month
                        and shift_dt.year == now.year):
                            month_sec += total_sec
            except Exception:
                pass

            duration_display = (
                self._format_hours(total_sec) if total_sec else "🟢 ACTIVE"
            )
            logout_display = logout_str if logout_str not in ("", "None") \
                else "🟢 ACTIVE"

            for col, value in enumerate([
                employee, login_str, logout_display, duration_display
            ]):
                self.table.setItem(
                    row, col, QTableWidgetItem(value)
                )
                # ✅ Cards loop ke BAAD update
        self.today_card.update_value(self._format_hours(today_sec))
        self.week_card.update_value(self._format_hours(week_sec))
        self.month_card.update_value(self._format_hours(month_sec))

    def export_csv(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Attendance",
            "attendance_report.csv", "CSV Files (*.csv)"
        )
        if not file_path:
            return
        try:
            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Employee", "Login Time", "Logout Time", "Duration"
                ])
                for row in range(self.table.rowCount()):
                    writer.writerow([
                        (self.table.item(row, col).text()
                        if self.table.item(row, col) else "")
                        for col in range(4)
                    ])
            QMessageBox.information(
                self, "Success",
                f"CSV exported:\n{file_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))