import os
from datetime import datetime, timezone, timedelta

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QHeaderView, QAbstractItemView, QMessageBox, QLineEdit,
    QComboBox, QHBoxLayout, QPushButton,
)

from client.presentation.windows.base_window import BaseWindow
from client.services.log_service import LogService
from client.application.managers.session_manager import SessionManager
from client.presentation.windows.screenshot_preview_window import ScreenshotPreviewWindow

IST = timezone(timedelta(hours=5, minutes=30))

def to_ist(ts):
    for fmt in ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ"]:
        try:
            dt = datetime.strptime(str(ts)[:26], fmt)
            return dt.replace(tzinfo=timezone.utc).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        except:
            continue
    return str(ts)


class _LoadLogsWorker(QObject):
    finished = Signal(object, object)
    error = Signal(str)

    def __init__(self, auth_token=None, role="employee", employee_filter=None):
        super().__init__()
        self._auth_token = auth_token
        self._role = role
        self._employee_filter = employee_filter

    @Slot()
    def run(self):
        try:
            idle_logs = LogService.get_idle_logs(token=self._auth_token)
            screenshot_logs = LogService.get_screenshot_logs(token=self._auth_token)

            total_logs = []
            screenshot_paths = {}

            for log in idle_logs:
                emp_id = log[0]
                if self._role != "admin" and emp_id != SessionManager.employee_id:
                    continue
                if self._employee_filter and emp_id != self._employee_filter:
                    continue
                total_logs.append([emp_id, log[1], log[2], None])

            for log in screenshot_logs:
                screenshot_id = log[0]
                emp_id = log[1]
                file_name = log[2]
                timestamp = log[3]
                if self._role != "admin" and emp_id != SessionManager.employee_id:
                    continue
                if self._employee_filter and emp_id != self._employee_filter:
                    continue
                total_logs.append([emp_id, "📸 SCREENSHOT", timestamp, None])

            total_logs.sort(key=lambda x: str(x[2]), reverse=True)

            for row, entry in enumerate(total_logs):
                if "SCREENSHOT" in str(entry[1]):
                    for log in screenshot_logs:
                        if str(log[3]) == str(entry[2]) and log[1] == entry[0]:
                            screenshot_paths[row] = {
                                'screenshot_id': log[0],
                                'employee_id': log[1],
                                'filename': str(log[2]),
                                'timestamp': str(log[3])
                            }
                            break

            self.finished.emit(total_logs, screenshot_paths)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))


class LogsWindow(BaseWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ETS Logs")
        self.resize(1100, 700)
        self._screenshot_paths = {}
        self._thread = None
        self._worker = None
        self.all_logs = []
        self._role = getattr(SessionManager, 'role', 'employee')
        self.setup_ui()

    def setup_ui(self):
        main_layout = QVBoxLayout()

        title = QLabel("Employee Activity Logs")
        title.setStyleSheet("font-size: 32px; font-weight: bold; color: white; margin-bottom: 20px;")

        filter_layout = QHBoxLayout()

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍 Search logs...")

        self.filter_box = QComboBox()
        self.filter_box.addItems(["All", "Screenshots", "Idle", "Active"])

        filter_layout.addWidget(self.search_box)
        filter_layout.addWidget(self.filter_box)

        # Admin ke liye employee filter
        if self._role == "admin":
            self.emp_filter_box = QComboBox()
            self.emp_filter_box.addItem("All Employees", None)
            # Employees load honge logs ke saath
            filter_layout.addWidget(QLabel("Employee:"))
            filter_layout.addWidget(self.emp_filter_box)
            self.emp_filter_box.currentIndexChanged.connect(self._on_employee_filter_changed)

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.load_logs)
        filter_layout.addWidget(refresh_btn)

        self.logs_table = QTableWidget()
        self.logs_table.setColumnCount(3)
        self.logs_table.setHorizontalHeaderLabels(["Employee ID", "Status / Screenshot", "Timestamp"])
        self.logs_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.logs_table.verticalHeader().setVisible(False)
        self.logs_table.setAlternatingRowColors(True)
        self.logs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.logs_table.setShowGrid(False)
        self.logs_table.setStyleSheet("""
            QTableWidget { background-color: rgba(17,24,39,0.85); border: 1px solid #2b3448;
                border-radius: 16px; color: white; padding: 8px; }
            QHeaderView::section { background-color: rgba(30,41,59,0.95); color: white;
                border: none; padding: 14px; font-size: 14px; font-weight: bold; }
            QTableWidget::item { padding: 12px; }
            QTableWidget::item:selected { background-color: #2563eb; color: white; }
        """)

        self.load_logs()
        self.logs_table.cellDoubleClicked.connect(self.open_screenshot)

        main_layout.addWidget(title)
        main_layout.addLayout(filter_layout)
        main_layout.addWidget(self.logs_table)
        self.setLayout(main_layout)

        self.search_box.textChanged.connect(self.apply_filters)
        self.filter_box.currentTextChanged.connect(self.apply_filters)

    def _on_employee_filter_changed(self):
        self.load_logs()

    def load_logs(self):
        self.logs_table.setRowCount(0)
        self._screenshot_paths = {}

        employee_filter = None
        if self._role == "admin" and hasattr(self, 'emp_filter_box'):
            employee_filter = self.emp_filter_box.currentData()

        self._thread = QThread()
        self._worker = _LoadLogsWorker(
            auth_token=SessionManager.auth_token,
            role=self._role,
            employee_filter=employee_filter
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_logs_loaded)
        self._worker.error.connect(self._on_logs_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    @Slot(object, object)
    def _on_logs_loaded(self, total_logs, screenshot_paths):
        self._screenshot_paths = screenshot_paths
        self.all_logs = total_logs

        # Admin ke liye employee list populate karo
        if self._role == "admin" and hasattr(self, 'emp_filter_box'):
            current = self.emp_filter_box.currentData()
            employees = sorted(set(log[0] for log in total_logs if log[0]))
            self.emp_filter_box.blockSignals(True)
            self.emp_filter_box.clear()
            self.emp_filter_box.addItem("All Employees", None)
            for emp in employees:
                self.emp_filter_box.addItem(emp, emp)
            # Restore selection
            idx = self.emp_filter_box.findData(current)
            if idx >= 0:
                self.emp_filter_box.setCurrentIndex(idx)
            self.emp_filter_box.blockSignals(False)

        self.apply_filters()

    @Slot(str)
    def _on_logs_error(self, message):
        QMessageBox.critical(self, "Logs Error", message)

    def open_screenshot(self, row, column):
        item = self.logs_table.item(row, 1)
        if item is None or "SCREENSHOT" not in item.text().upper():
            return

        # Visible row ka actual index dhundho
        visible_row = 0
        actual_info = None
        search = self.search_box.text().lower()
        filter_type = self.filter_box.currentText()

        for log in self.all_logs:
            status = str(log[1])
            emp = str(log[0])
            ts = str(log[2])
            row_text = f"{emp} {status} {ts}".lower()

            if search and search not in row_text:
                continue
            if filter_type == "Screenshots" and "SCREENSHOT" not in status.upper():
                continue
            if filter_type == "Idle" and "IDLE" not in status.upper():
                continue
            if filter_type == "Active" and "ACTIVE" not in status.upper():
                continue

            if visible_row == row:
                # Find in screenshot_paths
                for k, v in self._screenshot_paths.items():
                    if str(v.get('timestamp')) == str(log[2]) and v.get('employee_id') == log[0]:
                        actual_info = v
                        break
                break
            visible_row += 1

        if not actual_info:
            QMessageBox.warning(self, "Not Found", "Screenshot data not available.")
            return

        self.preview_window = ScreenshotPreviewWindow(
            screenshot_id=actual_info['screenshot_id'],
            employee_id=actual_info['employee_id'],
            timestamp=actual_info['timestamp'],
            filename=actual_info['filename']
        )
        self.preview_window.show()

    def closeEvent(self, event):
        try:
            if self._thread is not None:
                try:
                    if self._thread.isRunning():
                        self._thread.quit()
                        self._thread.wait(1000)
                except RuntimeError:
                    pass
        except Exception:
            pass
        event.accept()

    def apply_filters(self):
        search = self.search_box.text().lower()
        filter_type = self.filter_box.currentText()

        filtered = []
        for log in self.all_logs:
            emp = str(log[0])
            status = str(log[1])
            ts = str(log[2])
            row_text = f"{emp} {status} {ts}".lower()

            if search and search not in row_text:
                continue
            if filter_type == "Screenshots" and "SCREENSHOT" not in status.upper():
                continue
            if filter_type == "Idle" and "IDLE" not in status.upper():
                continue
            if filter_type == "Active" and "ACTIVE" not in status.upper():
                continue

            filtered.append(log)

        self.logs_table.setRowCount(len(filtered))
        for row, entry in enumerate(filtered):
            display = [str(entry[0]), str(entry[1]), to_ist(entry[2])]
            for col, value in enumerate(display):
                self.logs_table.setItem(row, col, QTableWidgetItem(value))
