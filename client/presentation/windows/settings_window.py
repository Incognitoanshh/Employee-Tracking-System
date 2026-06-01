from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QVBoxLayout,
    QComboBox
)
from client.services.logger_service import LoggerService

from client.presentation.windows.base_window import BaseWindow

from client.services.settings_service import SettingsService


class SettingsWindow(BaseWindow):

    def __init__(self):

        super().__init__()

        self.setWindowTitle(
            "ETS Settings"
        )

        self.resize(500, 400)

        self.setup_ui()
 
    def setup_ui(self):

        layout = QVBoxLayout()

        title = QLabel(
            "System Settings"
        )

        title.setStyleSheet("""

            font-size: 22px;
            font-weight: bold;

        """)

        self.screenshot_interval = QComboBox()

        self.screenshot_interval.addItems([

            "60",
            "120",
            "180",
            "240",
            "300"

        ])

        self.idle_threshold = QComboBox()

        self.idle_threshold.addItems([

            "15",
            "30",
            "60"

        ])

        save_button = QPushButton(
            "Save Settings"
        )

        save_button.clicked.connect(
            self.save_settings
        )

        layout.addWidget(title)

        layout.addWidget(
            QLabel(
                "Screenshot Interval (seconds)"
            )
        )

        layout.addWidget(
            self.screenshot_interval
        )

        layout.addWidget(
            QLabel(
                "Idle Threshold (seconds)"
            )
        )

        layout.addWidget(
            self.idle_threshold
        )

        layout.addWidget(
            save_button
        )

        self.setLayout(layout)

        self.load_settings()

    def save_settings(self):

        SettingsService.save_setting(

            "screenshot_interval",

            self.screenshot_interval.currentText()

        )

        SettingsService.save_setting(

            "idle_threshold",

            self.idle_threshold.currentText()

        )
        LoggerService.log(
            f"SETTINGS UPDATED : "
            f"Screenshot= {self.screenshot_interval.currentText()} seconds, "
            f"Idle = {self.idle_threshold.currentText()} seconds"
        )
        
        self.close()

    def load_settings(self):
        
        screenshot_interval = SettingsService.get_setting(
            "screenshot_interval",
            "60"
        )

        idle_threshold = SettingsService.get_setting(
            "idle_threshold",
            "30"
        )

        self.screenshot_interval.setCurrentText(
            screenshot_interval
        )

        self.idle_threshold.setCurrentText(
            idle_threshold
        )