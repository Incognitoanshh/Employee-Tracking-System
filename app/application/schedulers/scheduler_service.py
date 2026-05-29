
from app.services.settings_service import SettingsService

from PySide6.QtCore import (
    QObject,
    QTimer,
    Signal
)


class SchedulerService(QObject):

    countdown_updated = Signal(str)

    screenshot_triggered = Signal()

    def __init__(self):

        super().__init__()

        self.remaining_seconds = 0

        self.timer = QTimer()

        self.timer.timeout.connect(
            self.update_countdown
        )

    def start(self):

        self.generate_random_time()

        self.timer.start(1000)

    def generate_random_time(self):

        self.remaining_seconds = int(
            SettingsService.get_setting(
                "screenshot_interval",
                "60"
            )
        )
        print(f"Screenshot Interval: {self.remaining_seconds} seconds")

    def update_countdown(self):

        minutes = (
            self.remaining_seconds // 60
        )

        seconds = (
            self.remaining_seconds % 60
        )

        formatted = (
            f"{minutes:02}:{seconds:02}"
        )

        self.countdown_updated.emit(
            formatted
        )

        self.remaining_seconds -= 1

        if self.remaining_seconds < 0:

            self.screenshot_triggered.emit()

            self.generate_random_time()

    def reload_interval(self):

        self.generate_random_time()