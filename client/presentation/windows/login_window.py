from PySide6.QtCore import Qt

from PySide6.QtGui import QFont

from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QFrame
)
from client.application.managers.shift_manager import ShiftManager

from client.services.logger_service import LoggerService

from client.presentation.windows.base_window import BaseWindow

from client.presentation.windows.dashboard_window import DashboardWindow

from client.application.services.auth_service import AuthService

from client.application.managers.session_manager import SessionManager


class LoginWindow(BaseWindow):

    def __init__(self):

        super().__init__()

        self.setWindowTitle("ETS Login")

        self.resize(500, 650)

        self.setup_ui()

    def setup_ui(self):

        layout = QVBoxLayout()

        layout.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        card = QFrame()

        card.setFixedWidth(350)

        card_layout = QVBoxLayout()

        card_layout.setSpacing(20)

        title = QLabel(
            "Employee Tracking System"
        )

        title_font = QFont()

        title_font.setPointSize(18)

        title_font.setBold(True)

        title.setFont(title_font)

        title.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        subtitle = QLabel(
            "Secure Enterprise Monitoring"
        )

        subtitle.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self.username_input = QLineEdit()

        self.username_input.setPlaceholderText(
            "Username"
        )

        self.password_input = QLineEdit()

        self.password_input.setPlaceholderText(
            "Password"
        )

        self.password_input.setEchoMode(
            QLineEdit.EchoMode.Password
        )

        self.login_button = QPushButton(
            "Login"
        )

        self.status_label = QLabel("")

        self.status_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self.login_button.clicked.connect(
            self.handle_login
        )

        card_layout.addWidget(title)

        card_layout.addWidget(subtitle)

        card_layout.addSpacing(20)

        card_layout.addWidget(
            self.username_input
        )

        card_layout.addWidget(
            self.password_input
        )

        card_layout.addWidget(
            self.login_button
        )

        card_layout.addWidget(
            self.status_label
        )

        card.setLayout(card_layout)

        layout.addWidget(card)

        self.setLayout(layout)

        self.apply_styles()

    def apply_styles(self):

        self.setStyleSheet("""

            QWidget {
                background-color: #111111;
                color: white;
                font-family: Arial;
                font-size: 14px;
            }

            QFrame {
                background-color: #1a1a1a;
                border-radius: 16px;
                padding: 30px;
            }

            QLineEdit {
                background-color: #262626;
                border: 1px solid #333333;
                border-radius: 10px;
                padding: 12px;
            }

            QPushButton {
                background-color: #2563eb;
                border: none;
                border-radius: 10px;
                padding: 14px;
                font-weight: bold;
            }

            QPushButton:hover {
                background-color: #1d4ed8;
            }

        """)

    def handle_login(self):

        username = (
            self.username_input.text()
        )

        password = (
            self.password_input.text()
        )

        result = AuthService.login(
            username,
            password
        )

        if result["success"]:

            LoggerService.log(
                f"LOGIN SUCCESS : {username}"
            )

            # ShiftManager.start_shift()

            SessionManager.create_session(
                employee_id=result[
                    "employee_id"
                ],
                auth_token=result["token"]
            )

            ShiftManager.start_shift()

            self.dashboard = DashboardWindow()

            self.dashboard.show()

            self.hide()

        else:

            LoggerService.log(
                f"LOGIN FAILED : {username}"
            )

            self.status_label.setText(
                result["message"]
            )