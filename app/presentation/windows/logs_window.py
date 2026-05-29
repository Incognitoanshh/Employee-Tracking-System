from PySide6.QtWidgets import QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout

from app.presentation.windows.base_window import BaseWindow

from app.services.log_service import LogService

from app.presentation.windows.screenshot_preview_window import ScreenshotPreviewWindow


class LogsWindow(BaseWindow):

    def __init__(self):

        super().__init__()

        self.setWindowTitle("ETS Logs")

        self.resize(1000, 700)

        self.setup_ui()

    def setup_ui(self):

        main_layout = QVBoxLayout()

        title = QLabel("Employee Activity Logs")

        title.setStyleSheet("""

            font-size: 24px;
            font-weight: bold;
            margin-bottom: 20px;

        """)

        self.logs_table = QTableWidget()

        self.logs_table.setColumnCount(3)

        self.logs_table.setHorizontalHeaderLabels(
            ["Employee ID", "Status / Screenshot", "Timestamp"]
        )

        self.load_logs()

        self.logs_table.cellDoubleClicked.connect(self.open_screenshot)

        main_layout.addWidget(title)

        main_layout.addWidget(self.logs_table)

        self.setLayout(main_layout)

    def load_logs(self):

        idle_logs = LogService.get_idle_logs()

        screenshot_logs = LogService.get_screenshot_logs()

        total_logs = []

        for log in idle_logs:

            total_logs.append([log[0], log[1], log[2]])

        for log in screenshot_logs:

            total_logs.append([log[0], "SCREENSHOT SAVED", log[2]])

        self.logs_table.setRowCount(len(total_logs))

        for row, log in enumerate(total_logs):

            for column, value in enumerate(log):

                self.logs_table.setItem(row, column, QTableWidgetItem(str(value)))

    def open_screenshot(self, row, column):

        log_type = self.logs_table.item(row, 1).text()

        if "SCREENSHOT" in log_type:

            screenshot_logs = LogService.get_screenshot_logs()

            screenshot_index = row - len(LogService.get_idle_logs())

            if screenshot_index >= 0:

                image_path = screenshot_logs[screenshot_index][1]

                self.preview_window = ScreenshotPreviewWindow(image_path)

                self.preview_window.show()
