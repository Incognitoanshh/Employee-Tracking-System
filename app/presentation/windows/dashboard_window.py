from PySide6.QtWidgets import QLabel, QPushButton, QGridLayout, QHBoxLayout, QVBoxLayout

from app.presentation.windows.logs_window import LogsWindow

from app.presentation.tray.system_tray import SystemTray

from app.presentation.windows.settings_window import SettingsWindow

from PySide6.QtCore import QTimer

from PySide6.QtGui import QCursor

from app.presentation.tray.system_tray import SystemTray

from app.presentation.windows.base_window import BaseWindow

from app.presentation.widgets.status_card import StatusCard

from app.application.managers.session_manager import SessionManager

from app.application.schedulers.scheduler_service import SchedulerService

from app.application.managers.screenshot_manager import ScreenshotManager

from app.application.managers.idle_tracker import IdleTracker


class DashboardWindow(BaseWindow):

    def __init__(self):

        super().__init__()

        self.last_mouse_position = QCursor.pos()

        self.setWindowTitle("ETS Dashboard")

        self.resize(1200, 750)

        self.setup_ui()

        self.tray = SystemTray(self)

        self.tray.show()

        self.tray.show_message()

        self.activity_timer = QTimer()

        self.activity_timer.timeout.connect(self.track_activity)

        self.activity_timer.start(1000)

        print("DASHBOARD CREATED")

    def setup_ui(self):

        main_layout = QVBoxLayout()

        header_layout = QHBoxLayout()

        title = QLabel("Employee Tracking Dashboard")

        title.setStyleSheet("""

            font-size: 26px;
            font-weight: bold;

        """)

        employee_label = QLabel(f"Employee: " f"{SessionManager.employee_id}")

        employee_label.setStyleSheet("""

            color: #9ca3af;
            font-size: 14px;

        """)

        header_layout.addWidget(title)

        header_layout.addStretch()

        header_layout.addWidget(employee_label)

        cards_layout = QGridLayout()

        cards_layout.setSpacing(20)

        tracking_card = StatusCard("Tracking Status", "ACTIVE")

        self.idle_card = StatusCard("Idle Status", "WORKING")

        shift_card = StatusCard("Shift Timing", "09:00 - 18:00")

        self.screenshot_card = StatusCard("Next Screenshot", "00:00")

        upload_card = StatusCard("Upload Status", "SYNCED")

        internet_card = StatusCard("Internet", "CONNECTED")

        cards_layout.addWidget(tracking_card, 0, 0)

        cards_layout.addWidget(self.idle_card, 0, 1)

        cards_layout.addWidget(shift_card, 0, 2)

        cards_layout.addWidget(self.screenshot_card, 1, 0)

        cards_layout.addWidget(upload_card, 1, 1)

        cards_layout.addWidget(internet_card, 1, 2)

        bottom_layout = QHBoxLayout()

        logs_button = QPushButton("Open Logs")
        logs_button.clicked.connect(self.open_logs_window)

        settings_button = QPushButton("Settings")

        settings_button.clicked.connect(
                self.open_settings_window
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

        bottom_layout.addWidget(logs_button)

        bottom_layout.addWidget(settings_button)

        main_layout.addLayout(header_layout)

        main_layout.addSpacing(30)

        main_layout.addLayout(cards_layout)

        main_layout.addStretch()

        main_layout.addLayout(bottom_layout)

        self.setLayout(main_layout)

        self.scheduler = SchedulerService()

        self.scheduler.countdown_updated.connect(self.update_screenshot_timer)

        self.scheduler.screenshot_triggered.connect(self.capture_screenshot)

        self.scheduler.start()

        self.idle_tracker = IdleTracker()

        self.idle_tracker.status_changed.connect(self.update_idle_status)

        self.idle_tracker.start()

    def update_screenshot_timer(self, value):

        self.screenshot_card.update_value(value)

    def capture_screenshot(self):

        result = ScreenshotManager.capture_screenshot()

        print(result)

    def update_idle_status(self, status):

        self.idle_card.update_value(status)

    def track_activity(self):

        current_position = QCursor.pos()

        if current_position != self.last_mouse_position:

            self.last_mouse_position = current_position

            self.idle_tracker.reset_activity()

    def open_logs_window(self):

        self.logs_window = LogsWindow()

        self.logs_window.show()


    def closeEvent(self, event):

        print("TRAY CLOSE EVENT CALLED")

        event.ignore()

        self.hide()

    def open_settings_window(self):

        self.settings_window = SettingsWindow()

        self.settings_window.destroyed.connect(
            self.scheduler.reload_interval,
        )
        self.settings_window.show()
