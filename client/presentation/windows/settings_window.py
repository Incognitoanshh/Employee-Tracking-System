from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QVBoxLayout,
    QComboBox,
    QWidget,
    QHBoxLayout
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

        self.resize(600, 500)

        self.setup_ui()

    def setup_ui(self):

        main_layout = QVBoxLayout()

        main_layout.addStretch()

        card = QWidget()

        card.setFixedWidth(420)
        card.setFixedHeight(520)

        card.setStyleSheet("""

            QWidget {
                background-color: #151a23;
                border: 1px solid #242938;
                border-radius: 16px;
                padding: 20px;
            }

        """)

        layout = QVBoxLayout()

        layout.setSpacing(12)

        title = QLabel(
            "System Settings"
        )

        title.setStyleSheet("""

            font-size: 28px;
            font-weight: bold;
            color: white;
            margin-bottom: 10px;

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

        self.theme_selector = QComboBox()

        self.theme_selector.addItems([
            "Dark",
            "Light"
        ])

        combo_style = """

            QComboBox {
                background-color: #1e2635;
                color: white;
                border: 1px solid #2d3748;
                border-radius: 10px;
                padding: 10px;
                min-height: 20px;
            }

        """

        self.screenshot_interval.setStyleSheet(
            combo_style
        )

        self.idle_threshold.setStyleSheet(
            combo_style
        )

        self.theme_selector.setStyleSheet(
            combo_style
        )

        save_button = QPushButton(
            "Save Settings"
        )

        save_button.setFixedHeight(50)

        save_button.setStyleSheet("""

            QPushButton {
                background-color: #2563eb;
                color: white;
                border: none;
                border-radius: 10px;
                font-weight: bold;
            }

            QPushButton:hover {
                background-color: #1d4ed8;
            }

        """)

        save_button.clicked.connect(
            self.save_settings
        )

        layout.addWidget(title)

        screenshot_label = QLabel("Screenshot Interval")
        screenshot_label.setStyleSheet("""
        color: white;
        font-size: 14px;
        font-weight: 600;
        """)

        idle_label = QLabel("Idle Threshold")
        idle_label.setStyleSheet("""
        color: white;
        font-size: 14px;
        font-weight: 600;
        """)

        theme_label = QLabel("Theme")
        theme_label.setStyleSheet("""
        color: white;
        font-size: 14px;
        font-weight: 600;
        """)

        layout.addWidget(screenshot_label)
        layout.addWidget(self.screenshot_interval)

        layout.addWidget(idle_label)
        layout.addWidget(self.idle_threshold)

        layout.addWidget(theme_label)
        layout.addWidget(self.theme_selector)

        layout.addSpacing(20)

        layout.addWidget(
            save_button
        )

        card.setLayout(layout)

        center_layout = QHBoxLayout()

        center_layout.addStretch()
        center_layout.addWidget(card)
        center_layout.addStretch()

        main_layout.addLayout(
            center_layout
        )

        main_layout.addStretch()

        self.setLayout(main_layout)

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

        SettingsService.save_setting(
            "theme",
            self.theme_selector.currentText()
        )

        LoggerService.log(
            f"SETTINGS UPDATED : "
            f"Screenshot={self.screenshot_interval.currentText()}s, "
            f"Idle={self.idle_threshold.currentText()}s, "
            f"Theme={self.theme_selector.currentText()}"
        )

        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(
            self,
            "Theme Updated",
            "Restart ETS to apply theme."
        )

        self.close()

    def load_settings(self):

        screenshot_interval = SettingsService.get_setting(
            "screenshot_interval",
            "300"
        )

        idle_threshold = SettingsService.get_setting(
            "idle_threshold",
            "15"
        )

        theme = SettingsService.get_setting(
            "theme",
            "Dark"
        )

        self.screenshot_interval.setCurrentText(
            screenshot_interval
        )

        self.idle_threshold.setCurrentText(
            idle_threshold
        )

        self.theme_selector.setCurrentText(
            theme
        )