import os

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (

    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QHeaderView,
    QAbstractItemView,
    QMessageBox,
)

from client.presentation.windows.base_window import BaseWindow
from client.services.log_service import LogService
from client.presentation.windows.screenshot_preview_window import (
    ScreenshotPreviewWindow,
)


class _LoadLogsWorker(QObject):
    finished = Signal(list, dict)  # total_logs, screenshot_paths
    error = Signal(str)

    @Slot()
    def run(self):
        try:
            idle_logs = LogService.get_idle_logs()
            screenshot_logs = LogService.get_screenshot_logs()

            total_logs = []
            screenshot_paths = {}

            # Idle logs: (employee_id, status, timestamp)
            for log in idle_logs:
                total_logs.append([
                    log[0],  # employee_id
                    log[1],  # status
                    log[2],  # timestamp
                    None,    # file_path
                ])

            # Screenshot logs: (employee_id, file_path, timestamp)
            for log in screenshot_logs:
                total_logs.append([
                    log[0],  # employee_id
                    "📸 SCREENSHOT SAVED",
                    log[2],  # timestamp
                    log[1],  # file_path
                ])

            # Sort once
            total_logs.sort(key=lambda x: x[2], reverse=True)

            # Map screenshot file paths by *row* after sorting
            for row, entry in enumerate(total_logs):
                file_path = entry[3]
                if file_path is not None:
                    screenshot_paths[row] = file_path

            self.finished.emit(total_logs, screenshot_paths)
        except Exception as e:
            self.error.emit(str(e))


class LogsWindow(BaseWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("ETS Logs")
        self.resize(1000, 650)

        self._screenshot_paths = {}
        self._thread = None
        self._worker = None

        self.setup_ui()

    def setup_ui(self):
        main_layout = QVBoxLayout()

        title = QLabel("Employee Activity Logs")
        title.setStyleSheet(
            """
            font-size: 32px;
            font-weight: bold;
            color: white;
            margin-bottom: 20px;
            """
        )

        self.logs_table = QTableWidget()
        self.logs_table.setColumnCount(3)

        self.logs_table.setHorizontalHeaderLabels(
            [
                "Employee ID",
                "Status / Screenshot",
                "Timestamp",
            ]
        )

        self.logs_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.logs_table.verticalHeader().setVisible(False)
        self.logs_table.setAlternatingRowColors(True)
        self.logs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.logs_table.setShowGrid(False)

        self.logs_table.setStyleSheet(
            """
            QTableWidget {
                background-color: rgba(17, 24, 39, 0.85);
                border: 1px solid #2b3448;
                border-radius: 16px;
                color: white;
                gridline-color: transparent;
                padding: 8px;
            }

            QHeaderView::section {
                background-color: rgba(30, 41, 59, 0.95);
                color: white;
                border: none;
                padding: 14px;
                font-size: 14px;
                font-weight: bold;
                border-top-left-radius: 16px;
                border-top-right-radius: 16px;
            }

            QTableWidget::item {
                padding: 12px;
                border-radius: 10px;
            }

            QTableWidget::item:selected {
                background-color: #2563eb;
                color: white;
            }
            """
        )


        self.load_logs()

        self.logs_table.cellDoubleClicked.connect(self.open_screenshot)

        main_layout.addWidget(title)
        main_layout.addWidget(self.logs_table)
        self.setLayout(main_layout)

    def load_logs(self):
        # Reset UI immediately
        self.logs_table.setRowCount(0)
        self.logs_table.setUpdatesEnabled(False)
        self.logs_table.blockSignals(True)

        self._screenshot_paths = {}

        # Background load to prevent UI freeze
        self._thread = QThread()
        self._worker = _LoadLogsWorker()
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_logs_loaded)
        self._worker.error.connect(self._on_logs_error)

        # Cleanup
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    @Slot(list, dict)
    def _on_logs_loaded(self, total_logs, screenshot_paths):
        self._screenshot_paths = screenshot_paths

        self.logs_table.setUpdatesEnabled(False)
        self.logs_table.blockSignals(True)

        self.logs_table.setRowCount(len(total_logs))

        for row, entry in enumerate(total_logs):
            # entry: [employee_id, status_or_screenshot, timestamp, file_path]
            for column, value in enumerate(entry[:3]):
                self.logs_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(str(value)),
                )

        self.logs_table.blockSignals(False)
        self.logs_table.setUpdatesEnabled(True)

    @Slot(str)
    def _on_logs_error(self, message):
        self.logs_table.blockSignals(False)
        self.logs_table.setUpdatesEnabled(True)
        QMessageBox.critical(self, "Logs Error", message)

    def open_screenshot(self, row, column):
        item = self.logs_table.item(row, 1)
        if item is None:
            return

        log_type = item.text()
        if "SCREENSHOT" not in log_type:
            return

        image_path = self._screenshot_paths.get(row)

        if not image_path:
            QMessageBox.warning(
                self,
                "Not Found",
                "Screenshot path not available.",
            )
            return

        if not os.path.exists(image_path):
            QMessageBox.warning(
                self,
                "File Not Found",
                f"Screenshot file nahi mili:\n{image_path}",
            )
            return

        self.preview_window = ScreenshotPreviewWindow(image_path)
        self.preview_window.show()

