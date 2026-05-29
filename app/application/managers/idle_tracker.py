from datetime import datetime

from PySide6.QtCore import (
    QObject,
    QTimer,
    Signal
)

from app.services.logger_service import LoggerService
from app.services.settings_service import SettingsService

from app.infrastructure.database.database import Database

from app.application.managers.session_manager import SessionManager


class IdleTracker(QObject):

    status_changed = Signal(str)

    def __init__(self):

        super().__init__()

        self.is_idle = False

        self.counter = 0

        self.idle_threshold = int(
            SettingsService.get_setting(
                "idle_threshold",
                "15"
            )
        )
        print(f"Idle Threshold: {self.idle_threshold} seconds")

        self.timer = QTimer()

        self.timer.timeout.connect(
            self.check_idle
        )

    def start(self):

        self.timer.start(1000)

    def check_idle(self):

        self.counter += 1

        if self.counter >= self.idle_threshold:

            if not self.is_idle:

                self.is_idle = True

                self.status_changed.emit(
                    "IDLE"
                )

                self.save_log(
                    "IDLE"
                )
                LoggerService.log(
                    "USER IDLE"
                )

    def reset_activity(self):

        self.counter = 0

        if self.is_idle:

            self.is_idle = False

            self.status_changed.emit(
                "WORKING"
            )

            self.save_log(
                "WORKING"
            )
            LoggerService.log(
                "USER ACTIVE"
            )

    def save_log(
        self,
        status
    ):

        connection = Database.connect()

        cursor = connection.cursor()

        cursor.execute("""

            INSERT INTO idle_logs (

                employee_id,
                status,
                timestamp

            )

            VALUES (?, ?, ?)

        """, (

            SessionManager.employee_id,

            status,

            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        ))

        connection.commit()

        connection.close()

        print(
            f"[IDLE STATUS] {status}"
        )