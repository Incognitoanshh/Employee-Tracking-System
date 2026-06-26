import os
from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QLabel, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QHeaderView, QAbstractItemView,
    QMessageBox, QLineEdit, QComboBox, QHBoxLayout,
)

from client.presentation.windows.base_window import BaseWindow
from client.infrastructure.database.database import Database
from client.presentation.windows.screenshot_preview_window import (
    ScreenshotPreviewWindow,
)


class _LoadLogsWorker(QObject):
    finished = Signal(list)
    error    = Signal(str)

    @Slot()
    def run(self):
        try:
            rows = []

            with Database.get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute(
                    """
                    SELECT employee_id, idle_start, idle_end, duration_seconds
                    FROM idle_logs
                    ORDER BY idle_start DESC
                    LIMIT 500
                    """
                )
                for log in cursor.fetchall():
                    rows.append({
                        "employee_id": log["employee_id"],
                        "status":      "IDLE",
                        "timestamp":   log["idle_start"],
                        "file_path":   None,
                    })

                cursor.execute(
                    """
                    SELECT employee_id, file_path, timestamp
                    FROM screenshots
                    ORDER BY timestamp DESC
                    LIMIT 500
                    """
                )
                for log in cursor.fetchall():
                    rows.append({
                        "employee_id": log["employee_id"],
                        "status":      "📸 SCREENSHOT SAVED",
                        "timestamp":   log["timestamp"],
                        "file_path":   log["file_path"],
                    })

                cursor.execute(
                    """
                    SELECT source, message, timestamp
                    FROM app_logs
                    ORDER BY id DESC
                    LIMIT 200
                    """
                )
                for log in cursor.fetchall():
                    rows.append({
                        "employee_id": log["source"],
                        "status":      log["message"],
                        "timestamp":   log["timestamp"],
                        "file_path":   None,
                    })

            rows.sort(key=lambda x: x["timestamp"] or "", reverse=True)
            self.finished.emit(rows)

        except Exception as e:
            self.error.emit(str(e))


class LogsWindow(BaseWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ETS Logs")
        self.resize(1000, 650)

        self._all_logs  = []
        self._thread    = None
        self._worker    = None

        self.setup_ui()

    def setup_ui(self):
        main_layout = QVBoxLayout()

        title = QLabel("Employee Activity Logs")
        title.setStyleSheet(
            "font-size: 28px; font-weight: bold; color: white; margin-bottom: 16px;"
        )

        filter_layout = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍 Search logs...")
        self.filter_box = QComboBox()
        self.filter_box.addItems([
            "All", "Screenshots", "Idle", "Active", "Login", "Logout"
        ])
        filter_layout.addWidget(self.search_box)
        filter_layout.addWidget(self.filter_box)

        self.logs_table = QTableWidget()
        self.logs_table.setColumnCount(3)
        self.logs_table.setHorizontalHeaderLabels([
            "Employee ID", "Status / Event", "Timestamp"
        ])
        self.logs_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.logs_table.verticalHeader().setVisible(False)
        self.logs_table.setAlternatingRowColors(True)
        self.logs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.logs_table.setShowGrid(False)
        self.logs_table.setStyleSheet("""
            QTableWidget {
                background-color: rgba(17,24,39,0.85);
                border: 1px solid #2b3448;
                border-radius: 16px;
                color: white;
                padding: 8px;
            }
            QHeaderView::section {
                background-color: rgba(30,41,59,0.95);
                color: white; border: none;
                padding: 14px; font-size: 14px; font-weight: bold;
            }
            QTableWidget::item { padding: 12px; border-radius: 10px; }
            QTableWidget::item:selected {
                background-color: #2563eb; color: white;
            }
        """)

        self.logs_table.cellDoubleClicked.connect(self.open_screenshot)

        main_layout.addWidget(title)
        main_layout.addLayout(filter_layout)
        main_layout.addWidget(self.logs_table)
        self.setLayout(main_layout)

        self.search_box.textChanged.connect(self.apply_filters)
        self.filter_box.currentTextChanged.connect(self.apply_filters)

        self.load_logs()

    def load_logs(self):
        self.logs_table.setRowCount(0)
        self.logs_table.setUpdatesEnabled(False)
        self.logs_table.blockSignals(True)

        self._thread = QThread()
        self._worker = _LoadLogsWorker()
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_logs_loaded)
        self._worker.error.connect(self._on_logs_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    @Slot(list)
    def _on_logs_loaded(self, logs: list):
        self._all_logs = logs
        self.logs_table.blockSignals(False)
        self.logs_table.setUpdatesEnabled(True)
        self.apply_filters()

    @Slot(str)
    def _on_logs_error(self, message: str):
        self.logs_table.blockSignals(False)
        self.logs_table.setUpdatesEnabled(True)
        QMessageBox.critical(self, "Logs Error", message)

    def apply_filters(self):
        search      = self.search_box.text().lower()
        filter_type = self.filter_box.currentText()

        filtered = []
        for log in self._all_logs:
            status = str(log["status"]).upper()
            row_text = (
                f"{log['employee_id']} {status} {log['timestamp']}"
            ).lower()

            if search and search not in row_text:
                continue
            if filter_type == "Screenshots" and "SCREENSHOT" not in status:
                continue
            if filter_type == "Idle"        and "IDLE"       not in status:
                continue
            if filter_type == "Active"      and "ACTIVE"     not in status:
                continue
            if filter_type == "Login"       and "LOGIN"      not in status:
                continue
            if filter_type == "Logout"      and "LOGOUT"     not in status:
                continue

            filtered.append(log)

        self.logs_table.setUpdatesEnabled(False)
        self.logs_table.blockSignals(True)
        self.logs_table.setRowCount(len(filtered))

        for row, log in enumerate(filtered):
            self.logs_table.setItem(
                row, 0, QTableWidgetItem(str(log["employee_id"] or ""))
            )
            self.logs_table.setItem(
                row, 1, QTableWidgetItem(str(log["status"] or ""))
            )
            self.logs_table.setItem(
                row, 2, QTableWidgetItem(str(log["timestamp"] or ""))
            )
            item = self.logs_table.item(row, 1)
            if item and log["file_path"]:
                item.setData(Qt.UserRole, log["file_path"])

        self.logs_table.blockSignals(False)
        self.logs_table.setUpdatesEnabled(True)

    def open_screenshot(self, row: int, _col: int):
        item = self.logs_table.item(row, 1)
        if not item or "SCREENSHOT" not in item.text().upper():
            return

        file_path = item.data(Qt.UserRole)
        if not file_path:
            QMessageBox.warning(self, "Not Found", "Screenshot path not available.")
            return
        if not os.path.exists(file_path):
            QMessageBox.warning(
                self, "File Not Found",
                f"Screenshot file nahi mili:\n{file_path}"
            )
            return

        self.preview_window = ScreenshotPreviewWindow(file_path)
        self.preview_window.show()

    def closeEvent(self, event):
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(1000)
        event.accept()
