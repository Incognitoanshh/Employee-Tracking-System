from PySide6.QtCore import Qt, QThread, Signal
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
    QDialog,
)

from client.application.managers.session_log_manager import SessionLogManager
from client.application.managers.shift_manager import ShiftManager
from client.services.logger_service import LoggerService
from client.presentation.windows.base_window import BaseWindow
from client.presentation.theme import C
from client.presentation.windows.change_password_dialog import ChangePasswordDialog
from client.presentation.windows.employee_panel import EmployeePanel
from client.presentation.windows.admin_config_panel import AdminConfigPanel
from client.application.services.auth_service import AuthService
from client.application.managers.session_manager import SessionManager


# Login ke UI thread se hate hue kaam.
#
# PROBLEM: pehle handle_login() sab kuch main thread pe SYNCHRONOUSLY
# karta tha —
#
#     AuthService.login()            timeout  5s
#     LoggerService.log("LOGIN ...") timeout  5s   (server pe upload)
#     ShiftManager.start_shift()     timeout 10s   (open-session check)
#              ...aur uske andar     timeout 10s   (attendance POST)
#
# Yaani worst case 30 second, jis dauran poori window jami rehti thi —
# na spinner chalta tha, na window move hoti thi, macOS use "not
# responding" dikhata tha. Achhe network pe ye milliseconds me nikal
# jaata tha is liye kabhi pakda nahi gaya; slow ya lossy link pe har
# call apna poora timeout kha jaati hai.
#
# In chaar me se sirf PEHLI ka result UI ko chahiye. Baaki teen
# fire-and-forget hain — unke liye user ko intezaar karwana bekaar hai.
_BG_WORKERS: list = []


def drain_login_workers(timeout_ms: int = 3000):
    """App band hote waqt chal rahe login workers ka intezaar karo.

    Agar user login ke turant baad app quit kar de, to post-login worker
    (attendance/shift calls) abhi chal raha ho sakta hai. Us waqt Qt
    thread object destroy hone se std::terminate() aata hai — app crash
    ke saath band hota hai. Bounded wait ke baad chhod dete hain;
    terminate() nahi karte, wo aur khatarnak hai.
    """
    for worker in list(_BG_WORKERS):
        try:
            if worker.isRunning():
                worker.wait(timeout_ms)
        except RuntimeError:
            pass
    _BG_WORKERS.clear()


def _track(worker):
    """Worker ka reference rakho jab tak wo chal raha hai.

    Iske bina Python use garbage-collect kar deta hai jab thread abhi
    chal raha hota hai — Qt tab std::terminate() call karta hai (wahi
    "QThread: Destroyed while thread is still running" wala crash).
    """
    _BG_WORKERS.append(worker)
    worker.finished.connect(lambda: _BG_WORKERS.remove(worker)
                            if worker in _BG_WORKERS else None)


class _LoginWorker(QThread):
    """Sirf authentication — iska result UI ko chahiye."""
    done = Signal(dict)

    def __init__(self, username, password):
        super().__init__()
        self._u, self._p = username, password

    def run(self):
        try:
            self.done.emit(AuthService.login(self._u, self._p) or {})
        except Exception as e:
            self.done.emit({"success": False, "message": str(e)})


class _PostLoginWorker(QThread):
    """Login ke baad ka kaam — UI ko iska intezaar nahi karna chahiye."""

    def __init__(self, username, login_time):
        super().__init__()
        self._username = username
        self._login_time = login_time

    def run(self):
        # Har step alag try me — ek fail ho to baaki phir bhi chalein.
        # Pehle ye ek hi sequence me the, to attendance ka timeout
        # LOGIN SUCCESS log ko bhi le dubta tha.
        for fn in (
            lambda: LoggerService.log(f"LOGIN SUCCESS : {self._username}"),
            lambda: ShiftManager.start_shift_remote(self._login_time),
            SessionLogManager.start_session,
        ):
            try:
                fn()
            except Exception:
                pass


class LoginWindow(BaseWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Amaze Connect — Sign in")
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
        brand_label = QLabel("AMAZE")
        brand_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_label.setStyleSheet(
            f"""
            font-size: 13px;
            font-weight: 700;
            letter-spacing: 4px;
            color: {C.PRIMARY};
            background: transparent;
            """
        )

        title = QLabel("Amaze Connect")
        title_font = QFont()
        title_font.setPointSize(20)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {C.TEXT}; background: transparent;")

        subtitle = QLabel("Amaze Internet Services Pvt. Ltd.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(
            f"color: {C.TEXT_MUTED}; font-size: 12px; background: transparent;"
        )

        card_layout.addWidget(brand_label)
        card_layout.addSpacing(6)
        card_layout.addWidget(title)
        card_layout.addSpacing(4)
        card_layout.addWidget(subtitle)
        card_layout.addSpacing(32)

        # ── Fields ───────────────────────────────────────────
        field_style = f"""
        QLineEdit {{
            background-color: {C.BG};
            border: 1px solid {C.BORDER};
            border-radius: 10px;
            padding: 12px 14px;
            color: {C.TEXT};
            font-size: 14px;
        }}
        QLineEdit:focus {{
            border: 1px solid {C.PRIMARY};
        }}
        QLineEdit::placeholder {{
            color: {C.TEXT_DIM};
        }}
        """

        user_label = QLabel("Username")
        user_label.setStyleSheet(
            f"color: {C.TEXT_MUTED}; font-size: 12px; font-weight: 600; background: transparent;"
        )

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Enter your username")
        self.username_input.setFixedHeight(46)
        self.username_input.setStyleSheet(field_style)

        pass_label = QLabel("Password")
        pass_label.setStyleSheet(
            f"color: {C.TEXT_MUTED}; font-size: 12px; font-weight: 600; background: transparent;"
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

        forgot_password.setStyleSheet(f"""
        QPushButton {{
            border: none;
            background: transparent;
            color: {C.PRIMARY};
            font-size: 12px;
            font-weight: 500;
        }}
        QPushButton:hover {{
            color: {C.PRIMARY_DIM};
            text-decoration: underline;
        }}
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
            f"""
            QPushButton {{
                background-color: {C.PRIMARY};
                border: none;
                border-radius: 10px;
                color: {C.ON_ACCENT};
                font-weight: 700;
                font-size: 14px;
                letter-spacing: 0.3px;
            }}
            QPushButton:hover {{ background-color: {C.PRIMARY_DIM}; }}
            QPushButton:pressed {{ background-color: {C.PRIMARY_DIM}; }}
            QPushButton:disabled {{ background-color: {C.ELEVATED};
                                    color: {C.TEXT_DIM}; }}
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
        # BUG FIX: "Windows" hardcoded tha — macOS pe bhi "Windows" dikhta
        # tha, aur version "v1.0" tha jabki client v2.1.0 hai. Support ticket
        # pe employee jo version batata, wo hamesha galat hota.
        import platform as _platform
        _os = {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}.get(
            _platform.system(), _platform.system()
        )
        from client.core.config import APP_VERSION
        version_label = QLabel(f"v{APP_VERSION}  ·  {_os}  ·  IST")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_label.setStyleSheet(
            f"color: {C.TEXT_DIM}; font-size: 11px; background: transparent;"
        )
        card_layout.addWidget(version_label)

        card.setLayout(card_layout)
        card.setStyleSheet(
            f"""
            QFrame#loginCard {{
                background-color: {C.CARD};
                border: 1px solid {C.BORDER};
                border-radius: 18px;
            }}
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
            self.status_label.setStyleSheet(
                f"color: {C.AMBER}; font-size: 13px; background: transparent;"
            )
            self.status_label.setText("⚠  Please enter username and password.")
            self.login_button.setEnabled(True)
            self.login_button.setText("Sign In")
            return

        # Auth background thread pe — UI responsive rehti hai.
        self._login_worker = _LoginWorker(username, password)
        self._login_worker.done.connect(self._on_login_result)
        _track(self._login_worker)
        self._login_worker.start()

    def _on_login_result(self, result: dict):
        username = self.username_input.text().strip()

        if result.get("success"):
            # BUG FIX: `LOGIN SUCCESS` pehle create_session() se PEHLE log
            # hota tha. LoggerService.log() shuru me hi ye check karta hai:
            #
            #     employee_id = SessionManager.employee_id
            #     if not employee_id: return
            #
            # ...aur pichhle logout ne session clear kar di hoti hai, to us
            # waqt employee_id None hota tha. Result: LOGIN event sirf local
            # app.log file me jaata tha — na local DB me, na server pe.
            # Isi liye logout karke wapas login karne par employee dashboard
            # ka "Recent Activity" purane events pe hi atka dikhta tha, naya
            # "Login Successful" kabhi aata hi nahi tha.
            #
            # Ab session pehle banti hai, phir log — to employee_id maujood
            # hota hai aur log server tak jaata hai.
            SessionManager.create_session(
                employee_id=result["employee_id"],
                auth_token=result["token"],
                role=result.get("role", "employee"),
                shift_start=result.get("shift_start"),
                shift_end=result.get("shift_end"),
                full_name=result.get("full_name"),
                designation=result.get("designation"),
            )
            # Shift ka LOCAL row abhi likho — millisecond ka kaam hai.
            #
            # BUG (maine hi introduce kiya tha jab login async banaya):
            # poora start_shift() background me chala gaya tha, lekin
            # EmployeePanel apne __init__ me `shifts` ki latest row padhta
            # hai (Session Duration wahin se banti hai). Panel row likhe
            # jaane se PEHLE padh leta tha, is liye Session Duration
            # hamesha 00:00:00 dikhati thi.
            login_time = ShiftManager.start_shift_local()

            # Baaki (LOGIN log, attendance POST, session start) ab bhi
            # background me — inka result panel kholne ke liye nahi chahiye.
            post = _PostLoginWorker(username, login_time)
            _track(post)
            post.start()

            role = result.get("role", "employee")

            # An admin has issued a temporary password. Replace it before the
            # panel opens — a temporary password that reaches the desktop is
            # one that stays in use, and it was handed over in the open.
            if result.get("must_change_password"):
                dialog = ChangePasswordDialog(self, forced=True)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    # The dialog cannot be dismissed, so this only happens if
                    # it failed to open at all. Do not fall through into the
                    # panel still holding the temporary password.
                    SessionManager.clear_session()
                    self.status_label.setStyleSheet(
                        f"color: {C.AMBER}; font-size: 13px; background: transparent;"
                    )
                    self.status_label.setText("⚠  You must set a new password to continue.")
                    self.login_button.setEnabled(True)
                    self.login_button.setText("Sign In")
                    return

            if role in ("admin", "super_admin"):
                self.next_window = AdminConfigPanel()
                self.next_window.show()
                self.close()
            else:
                self.next_window = EmployeePanel()
                self.next_window.show()
                self.close()
        else:
            LoggerService.log(f"LOGIN FAILED : {username}")
            self.status_label.setStyleSheet(
                f"color: {C.RED}; font-size: 13px; background: transparent;"
            )
            self.status_label.setText(f"✕  {result.get('message', 'Login failed')}")
            self.login_button.setEnabled(True)
            self.login_button.setText("Sign In")


    def show_reset_message(self):
        self.status_label.setStyleSheet(
            f"color: {C.PRIMARY}; font-size: 13px; background: transparent;"
            )
        self.status_label.setText(
             "Please contact your administrator to reset your password."
         )
        