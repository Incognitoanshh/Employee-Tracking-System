from client.application.managers.sync_manager import SyncManager
from client.services.settings_service import SettingsService

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
        self.sync_counter = 0
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_countdown)

    def start(self):
        self.load_interval()
        self.timer.start(1000)

    def load_interval(self):
        self.remaining_seconds = int(
            SettingsService.get_setting("screenshot_interval", "300")
        )
        print(f"Next Screenshot In: {self.remaining_seconds} seconds")

    def update_countdown(self):
        # BUG FIX: countdown har second update hona chahiye, sync sirf har 60s
        self.sync_counter += 1

        if self.sync_counter >= 60:
            SyncManager.retry_uploads()
            SyncManager.retry_logs()
            self.sync_counter = 0

        # Countdown update — ye sync se bahar hona chahiye tha
        minutes = self.remaining_seconds // 60
        seconds = self.remaining_seconds % 60
        formatted = f"{minutes:02}:{seconds:02}"
        self.countdown_updated.emit(formatted)

        self.remaining_seconds -= 1

        if self.remaining_seconds < 0:
            print("[SCREENSHOT TRIGGERED]")
            self.screenshot_triggered.emit()
            self.load_interval()

    def reload_interval(self):
        self.load_interval()
