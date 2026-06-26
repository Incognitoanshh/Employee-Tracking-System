import platform

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QSizePolicy,
    QSpacerItem,
)

from client.application.managers.session_log_manager import SessionLogManager
from client.application.managers.shift_manager import ShiftManager
from client.services.logger_service import LoggerService
from client.presentation.windows.base_window import BaseWindow
from client.presentation.windows.dashboard_window import DashboardWindow
from client.presentation.windows.admin_config_panel import AdminConfigPanel
from client.application.services.auth_service import AuthService
from client.application.managers.session_manager import SessionManager


class LoginWindow(BaseWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ETS — Login")
        self.resize(460, 580)
        self.setMinimumSize(400, 520)
        self.setup_ui()

    def setup_ui(self):
        outer_layout = QVBoxLayout()
        outer_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame()
        card.setFixedWidth(360)
        card.setObjectName("loginCard")

        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(36, 40, 36, 36)
        card_layout.setSpacing(0)

        # ── Brand ────────────────────────────────────────────
        brand_label = QLabel("ETS")
        brand_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_label.setStyleSheet(
            """
            font-size: 13px;
            font-weight: 700;
            letter-spacing: 4px;
            color: #2563eb;
            background: transparent;
            """
        )

        title = QLabel("Employee Tracking")
        title_font = QFont()
        title_font.setPointSize(20)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #f1f5f9; background: transparent;")

        subtitle = QLabel("Amaze Internet Services Pvt. Ltd.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(
            "color: #475569; font-size: 12px; background: transparent;"
        )

        card_layout.addWidget(brand_label)
        card_layout.addSpacing(6)
        card_layout.addWidget(title)
        card_layout.addSpacing(4)
        card_layout.addWidget(subtitle)
        card_layout.addSpacing(32)

        # ── Fields ───────────────────────────────────────────
        field_style = """
        QLineEdit {
            background-color: #0f172a;
            border: 1px solid #1e3a5f;
            border-radius: 10px;
            padding: 12px 14px;
            color: #f1f5f9;
            font-size: 14px;
        }
        QLineEdit:focus {
            border: 1px solid #2563eb;
        }
        QLineEdit::placeholder {
            color: #475569;
        }
        """

        user_label = QLabel("Username")
        user_label.setStyleSheet(
            "color: #94a3b8; font-size: 12px; font-weight: 600; background: transparent;"
        )

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Enter your username")
        self.username_input.setFixedHeight(46)
        self.username_input.setStyleSheet(field_style)

        pass_label = QLabel("Password")
        pass_label.setStyleSheet(
            "color: #94a3b8; font-size: 12px; font-weight: 600; background: transparent;"
        )

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Enter your password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setFixedHeight(46)
        self.password_input.setStyleSheet(field_style)
        self.password_input.returnPressed.connect(self.handle_login)

        card_layout.addWidget(user_label)
        card_layout.addSpacing(6)
        card_layout.addWidget(self.username_input)
        card_layout.addSpacing(16)
        card_layout.addWidget(pass_label)
        card_layout.addSpacing(6)
        card_layout.addWidget(self.password_input)
        forgot_password = QPushButton("Forgot Password?")
        forgot_password.setCursor(Qt.PointingHandCursor)
        forgot_password.setFlat(True)

        forgot_password.setStyleSheet("""
        QPushButton {
            border: none;
            background: transparent;
            color: #3b82f6;
                font-size: 12px;
                font-weight: 500;
        }
        QPushButton:hover {
            color: #60a5fa;
                text-decoration: underline;
        }
        """)

        forgot_layout = QHBoxLayout()
        forgot_layout.addStretch()
        forgot_layout.addWidget(forgot_password)

        card_layout.addLayout(forgot_layout)
        card_layout.addSpacing(10)

        forgot_password.clicked.connect(self.show_reset_message)
        card_layout.addSpacing(2)

        # ── Login Button ─────────────────────────────────────
        self.login_button = QPushButton("Sign In")
        self.login_button.setFixedHeight(46)
        self.login_button.setStyleSheet(
            """
            QPushButton {
                background-color: #2563eb;
                border: none;
                border-radius: 10px;
                color: white;
                font-weight: 700;
                font-size: 14px;
                letter-spacing: 0.3px;
            }
            QPushButton:hover { background-color: #3b82f6; }
            QPushButton:pressed { background-color: #1d4ed8; }
            QPushButton:disabled { background-color: #1e3a5f; color: #475569; }
            """
        )
        self.login_button.clicked.connect(self.handle_login)
        card_layout.addWidget(self.login_button)
        card_layout.addSpacing(1)

        # ── Status ───────────────────────────────────────────
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-size: 13px; background: transparent;")
        card_layout.addWidget(self.status_label)

        # ── Version ──────────────────────────────────────────
        card_layout.addSpacing(10)
        version_label = QLabel(f"v1.0  ·  {platform.system()}  ·  IST")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_label.setStyleSheet(
            "color: #334155; font-size: 11px; background: transparent;"
        )
        card_layout.addWidget(version_label)

        card.setLayout(card_layout)
        card.setStyleSheet(
            """
            QFrame#loginCard {
                background-color: #111827;
                border: 1px solid #1e2d3d;
                border-radius: 18px;
            }
            """
        )

        outer_layout.addWidget(card)
        self.setLayout(outer_layout)

    def handle_login(self):
        self.login_button.setEnabled(False)
        self.login_button.setText("Signing in…")
        self.status_label.setText("")

        username = self.username_input.text().strip()
        password = self.password_input.text()

        if not username or not password:
            self._set_status("⚠  Please enter username and password.", "warning")
            self._reset_button()
            return

        # ✅ Thread mein run karo — UI freeze nahi hogi
        import threading
        threading.Thread(
            target=self._do_login,
            args=(username, password),
            daemon=True,
        ).start()
        
    def _do_login(self, username: str, password: str):
        """Background thread mein login karo."""
        from PySide6.QtCore import QMetaObject, Qt

        # ✅ Store result FIRST, then notify UI
        result = AuthService.login(username, password)
        self._login_result = result

        # ✅ UI update main thread pe — Qt requirement (after result is stored)
        QMetaObject.invokeMethod(
            self,
            "_on_login_result",
            Qt.ConnectionType.QueuedConnection,
        )

    from PySide6.QtCore import Slot

    @Slot()
    def _on_login_result(self):
        result = getattr(self, "_login_result", {})
        current_username = self.username_input.text().strip()

        try:
            if result.get("success"):
                LoggerService.log(f"LOGIN SUCCESS: {result['employee_id']}")

                # ✅ session_id aur full_name bhi pass karo
                SessionManager.create_session(
                    employee_id = result["employee_id"],
                    auth_token  = result["token"],
                    role        = result.get("role", "employee"),
                    full_name   = result.get("full_name", ""),
                    shift_start = result.get("shift_start"),
                    shift_end   = result.get("shift_end"),
                    session_id  = result.get("session_id"),
                )

                ShiftManager.start_shift()
                SessionLogManager.start_session()

                role = result.get("role", "employee")
                if role == "admin":
                    self.next_window = AdminConfigPanel()
                else:
                    self.next_window = DashboardWindow()

                # ✅ Show next window and close login
                self.next_window.show()
                self.close()

            else:
                LoggerService.log(f"LOGIN FAILED: {result.get('message')}")
                self._set_status(
                    f"✕  {result.get('message', 'Login failed')}",
                    "error"
                )

        finally:
            self._reset_button()  # ✅ Hamesha reset — even on exception

    def _set_status(self, message: str, kind: str = "error"):
        colors = {
            "error":   "#ef4444",
            "warning": "#f59e0b",
            "info":    "#3b82f6",
        }
        color = colors.get(kind, "#ef4444")
        self.status_label.setStyleSheet(
            f"color: {color}; font-size: 13px; background: transparent;"
        )
        self.status_label.setText(message)

    def _reset_button(self):
        self.login_button.setEnabled(True)
        self.login_button.setText("Sign In")


    def show_reset_message(self):
        self.status_label.setStyleSheet(
            "color: #3b82f6; font-size: 13px; background: transparent;"
            )
        self.status_label.setText(
             "Please contact your administrator to reset your password."
         )
        