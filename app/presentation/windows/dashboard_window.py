from PySide6.QtCore import Qt

from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout
)

from app.presentation.windows.base_window import BaseWindow

from app.presentation.widgets.status_card import StatusCard

from app.application.managers.session_manager import SessionManager


class DashboardWindow(BaseWindow):

    def __init__(self):

        super().__init__()

        self.setWindowTitle(
            "ETS Dashboard"
        )

        self.resize(1200, 750)

        self.setup_ui()

    def setup_ui(self):

        main_layout = QVBoxLayout()

        header_layout = QHBoxLayout()

        title = QLabel(
            "Employee Tracking Dashboard"
        )

        title.setStyleSheet("""

            font-size: 26px;
            font-weight: bold;

        """)

        employee_label = QLabel(
            f"Employee: "
            f"{SessionManager.employee_id}"
        )

        employee_label.setStyleSheet("""

            color: #9ca3af;
            font-size: 14px;

        """)

        header_layout.addWidget(title)

        header_layout.addStretch()

        header_layout.addWidget(employee_label)

        cards_layout = QGridLayout()

        cards_layout.setSpacing(20)

        tracking_card = StatusCard(
            "Tracking Status",
            "ACTIVE"
        )

        idle_card = StatusCard(
            "Idle Status",
            "WORKING"
        )

        shift_card = StatusCard(
            "Shift Timing",
            "09:00 - 18:00"
        )

        screenshot_card = StatusCard(
            "Next Screenshot",
            "03:24"
        )

        upload_card = StatusCard(
            "Upload Status",
            "SYNCED"
        )

        internet_card = StatusCard(
            "Internet",
            "CONNECTED"
        )

        cards_layout.addWidget(
            tracking_card,
            0,
            0
        )

        cards_layout.addWidget(
            idle_card,
            0,
            1
        )

        cards_layout.addWidget(
            shift_card,
            0,
            2
        )

        cards_layout.addWidget(
            screenshot_card,
            1,
            0
        )

        cards_layout.addWidget(
            upload_card,
            1,
            1
        )

        cards_layout.addWidget(
            internet_card,
            1,
            2
        )

        bottom_layout = QHBoxLayout()

        logs_button = QPushButton(
            "Open Logs"
        )

        settings_button = QPushButton(
            "Settings"
        )

        logs_button.setFixedHeight(45)

        settings_button.setFixedHeight(45)

        logs_button.setStyleSheet("""

            QPushButton {
                background-color: #2563eb;
                border: none;
                border-radius: 10px;
                color: white;
                padding: 12px;
                font-weight: bold;
            }

        """)

        settings_button.setStyleSheet("""

            QPushButton {
                background-color: #1f2937;
                border: none;
                border-radius: 10px;
                color: white;
                padding: 12px;
                font-weight: bold;
            }

        """)

        bottom_layout.addWidget(
            logs_button
        )

        bottom_layout.addWidget(
            settings_button
        )

        main_layout.addLayout(
            header_layout
        )

        main_layout.addSpacing(30)

        main_layout.addLayout(
            cards_layout
        )

        main_layout.addStretch()

        main_layout.addLayout(
            bottom_layout
        )

        self.setLayout(main_layout)