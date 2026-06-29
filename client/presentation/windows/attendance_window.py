
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QHeaderView,
    QPushButton,
    QFileDialog,
    QMessageBox
)
from datetime import datetime, timezone, timedelta
import csv
from client.presentation.windows.base_window import BaseWindow
import requests
import ast
from client.core.config import API_BASE_URL
from client.application.managers.session_manager import SessionManager
from client.presentation.widgets.status_card import StatusCard


class AttendanceWindow(BaseWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Attendance History")
        self.resize(1000, 650)

        self.setup_ui()
        self.load_data()

    def setup_ui(self):

        layout = QVBoxLayout()

        cards_layout = QHBoxLayout()

        self.today_card = StatusCard(
            "Today",
            "0h"
        )

        self.week_card = StatusCard(
            "This Week",
            "0h"
        )

        self.month_card = StatusCard(
            "This Month",
            "0h"
        )

        cards_layout.addWidget(
            self.today_card
        )

        cards_layout.addWidget(
            self.week_card
        )

        cards_layout.addWidget(
            self.month_card
        )

        title = QLabel("Attendance History")
        title.setStyleSheet("""
        font-size: 30px;
        font-weight: bold;
        color: white;
        margin-bottom: 15px;
        """)

        self.summary = QLabel("")
        self.summary.setStyleSheet("""
        color: #94a3b8;
            font-size: 14px;
            margin-bottom: 15px;
            """)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels([
            "Employee",
            "Login Time",
            "Logout Time",
            "Duration"
        ])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.export_button = QPushButton("📥 Export CSV")

        self.export_button.setStyleSheet("""
        QPushButton {
            background-color: #16a34a;
                color: white;
                border-radius: 10px;
                padding: 10px;
                font-weight: bold;
        }

        QPushButton:hover {
            background-color: #15803d;
        }
        """)

        self.export_button.clicked.connect(
            self.export_csv
        )

        layout.addWidget(title)
        layout.addLayout(cards_layout)
        layout.addWidget(self.summary)
        layout.addWidget(self.export_button)
        layout.addWidget(self.table)

        self.setLayout(layout)

    def format_hours(self, seconds):

        hours = seconds // 3600
        minutes = (seconds % 3600) // 60

        return f"{hours}h {minutes}m"

    def load_data(self):

        try:

            response = requests.get(
                f"{API_BASE_URL}/attendance/all",
                headers={
                    "Authorization":
                        f"Bearer {SessionManager.auth_token}"
                    },
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            shifts = []
            for row in data.get("data", []):
                
                shifts.append(
                    (
                        row.get("employee_id"),
                        str(row.get("login_time", "")),
                        str(row.get("logout_time") or ""),
                        str(row.get("total_hours") or "")
                    )
                )
        except Exception as error:
        
            print(
                "[ATTENDANCE LOAD ERROR]",
                error
            )
            shifts = []

        self.table.setRowCount(len(shifts))


        today_seconds = 0

        week_seconds = 0

        month_seconds = 0

        for shift in shifts:

            login_time = shift[1]
            duration = shift[3]

            if (
                not duration
                or duration == "None"
            ):
                continue
            
            try:
            
                if duration.startswith("{"):
                
                    d = ast.literal_eval(duration)
                    total_sec = (
                        d.get("hours", 0) * 3600 +
                        d.get("minutes", 0) * 60 +
                        d.get("seconds", 0)
                    )

                else:

                    duration = duration.split(".")[0]

                    h, m, s = map(
                        int,
                        duration.split(":")
                    )

                    total_sec = (
                        h * 3600 +
                        m * 60 +
                        s
                    ) 

            except Exception:
            
                print(
                    "[BAD DURATION]",
                    duration
                )

                continue


            try:

                shift_date = datetime.fromisoformat(
                    login_time.replace("Z", "")
                )

            except Exception:
            
                shift_date = datetime.strptime(
                    login_time.split(".")[0],
                    "%Y-%m-%d %H:%M:%S"
                )

            now = datetime.now()

            if shift_date.date() == now.date():
                today_seconds += total_sec

            if shift_date.isocalendar()[1] == now.isocalendar()[1]:
                week_seconds += total_sec
            if (
                shift_date.month == now.month
                and
                shift_date.year == now.year
            ):
                month_seconds += total_sec


            self.today_card.update_value(
                self.format_hours(today_seconds)
            )

            self.week_card.update_value(
                self.format_hours(week_seconds)
            )

            self.month_card.update_value(
                self.format_hours(month_seconds)
            )

            self.summary.hide()


        for row, shift in enumerate(shifts):

            # print("[SHIFT]", row, shift)

            employee = shift[0]
            login_time = shift[1]
            logout_time = shift[2]
            duration = shift[3]

            if (
                not logout_time
                or logout_time == "None"
                or logout_time == ""
            ):
                logout_time = "🟢 ACTIVE"
            if (duration and duration != "None" and duration != ""):

                if duration.startswith("{"):
                
                    d = ast.literal_eval(duration)

                    duration = (
                        f"{d.get('hours', 0):02}:"
                        f"{d.get('minutes', 0):02}:"
                        f"{d.get('seconds', 0):02}"
                    )
                else:
                
                    duration = duration.split(".")[0]
            else:
                # logout_time filled hai toh duration unknown, ACTIVE nahi
                if not logout_time or logout_time == "🟢 ACTIVE":
                    duration = "🟢 ACTIVE"
                else:
                    duration = "--"

            IST = timezone(timedelta(hours=5, minutes=30))
            def to_ist(ts):
                if not ts or ts in ["🟢 ACTIVE", "--", "None", ""]:
                    return ts
                for fmt in ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"]:
                    try:
                        dt = datetime.strptime(str(ts)[:26], fmt)
                        return dt.replace(tzinfo=timezone.utc).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        continue
                return ts
            values = [
                employee,
                to_ist(login_time),
                to_ist(logout_time) if logout_time != "🟢 ACTIVE" else "🟢 ACTIVE",
                duration
            ]

            for col, value in enumerate(values):
            
                self.table.setItem(
                    row,
                    col,
                    QTableWidgetItem(str(value))
                )
    def export_csv(self):

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Attendance",
            "attendance_report.csv",
            "CSV Files (*.csv)"
        )

        if not file_path:
            return

        try:

            with open(
                file_path,
                "w",
                newline="",
                encoding="utf-8"
            ) as csv_file:

                writer = csv.writer(csv_file)

                writer.writerow([
                    "Employee",
                    "Login Time",
                    "Logout Time",
                    "Duration"
                ])

                for row in range(self.table.rowCount()):

                    row_data = []

                    for col in range(
                        self.table.columnCount()
                    ):

                        item = self.table.item(
                            row,
                            col
                        )

                        row_data.append(
                            item.text()
                            if item
                            else ""
                        )

                    writer.writerow(row_data)

                QMessageBox.information(
                    self,
                    "Success",
                    f"CSV exported successfully:\n{file_path}"
                )

        except Exception as error:

            QMessageBox.critical(
                self,
                "Export Failed",
                str(error)
            )