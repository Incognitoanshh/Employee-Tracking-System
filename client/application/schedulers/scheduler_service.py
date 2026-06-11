"""
SchedulerService — Shift-based screenshot scheduling
=====================================================
Changes (TL requirements):
    • Fixed countdown HATAYA — ab login ke time shift ke andar N random
    timestamps generate karta hai
    • Har timestamp pe QTimer fire hota hai → screenshot_triggered signal
    • Dashboard pe countdown show nahi hoga (employee ko pata nahi chalega
    kab screenshot lega)
    • Config sync aur idle check same hai
    """

from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QTimer, Signal

from client.application.managers.sync_manager import SyncManager
from client.application.managers.config_sync_manager import ConfigSyncManager
from client.application.managers.screenshot_manager import ScreenshotManager
from client.application.managers.session_manager import SessionManager
from client.services.settings_service import SettingsService
from client.services.logger_service import LoggerService


class SchedulerService(QObject):

    screenshot_triggered  = Signal()   # screenshot lene ka waqt aa gaya
    force_logout          = Signal()   # config sync se force logout

    def __init__(self):
        super().__init__()
        self._pending_timers:  list[QTimer] = []   # shift ke random timers
        self._sync_timer = QTimer()
        self._sync_timer.timeout.connect(self._sync_tick)
        self._sync_counter = 0
        self._config_sync: ConfigSyncManager | None = None

        # ------------------------------------------------------------------ #
        #  Start / Stop                                                        #
        # ------------------------------------------------------------------ #

    def start(self):
        """Login ke baad call karo — schedule generate karo aur timers lagao."""
        self._schedule_shift_screenshots()
        self._sync_timer.start(1000)   # har second — sync retry counter ke liye
        self._start_config_sync()
        LoggerService.log("SchedulerService: started (shift-based mode)")

    def stop(self):
        self._sync_timer.stop()

        for t in self._pending_timers:
            t.stop()

        self._pending_timers.clear()

        if self._config_sync:
            self._config_sync.stop()
        LoggerService.log("SchedulerService: stopped")

    def _schedule_shift_screenshots(self):

        now = datetime.now()

        # Shift timings SessionManager se
        shift_start_str = SessionManager.shift_start
        shift_end_str   = SessionManager.shift_end

        if shift_start_str and shift_end_str:
            try:
                # ISO format parse karo
                shift_start = datetime.fromisoformat(shift_start_str)
                shift_end   = datetime.fromisoformat(shift_end_str)
            except Exception:
                # Fallback: aaj ki date pe HH:MM format
                try:
                    shift_start = datetime.strptime(
                        f"{now.date()} {shift_start_str}", "%Y-%m-%d %H:%M"
                    )
                    shift_end = datetime.strptime(
                        f"{now.date()} {shift_end_str}", "%Y-%m-%d %H:%M"
                    )
                except Exception:
                    shift_start = now
                    shift_end   = now + timedelta(hours=8)
        else:
            # Shift info nahi mili — login time se 8 ghante
            shift_start = now
            shift_end   = now + timedelta(hours=8)
            LoggerService.log("SchedulerService: shift times not found, using 8hr window from now")

        effective_start = max(shift_start, now)
        if effective_start >= shift_end:
            LoggerService.log("SchedulerService: shift already ended, no screenshots scheduled")
            return
        timestamps = ScreenshotManager.generate_random_schedule(effective_start, shift_end)

        for ts in timestamps:
            
            delay_ms = int((ts - now).total_seconds() * 1000)
            if delay_ms < 0:
                continue  # already past
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._fire_screenshot)
            timer.start(delay_ms)
            self._pending_timers.append(timer)
            LoggerService.log(
                f"SchedulerService: screenshot scheduled at {ts.strftime('%H:%M:%S')} "
                f"(in {delay_ms//1000}s)"
            )
        print(f"[SCHEDULER] {len(timestamps)} screenshots scheduled for this shift")

    def _fire_screenshot(self):
        """Timer fire hone par signal emit karo."""
        print("[SCHEDULER] Screenshot timer fired")
        LoggerService.log("SchedulerService: screenshot timer fired")
        self.screenshot_triggered.emit()


    def _sync_tick(self):
        self._sync_counter += 1
        if self._sync_counter >= 60:
            SyncManager.retry_uploads()
            SyncManager.retry_logs()
            self._sync_counter = 0

    def _start_config_sync(self):
        if not SessionManager.is_authenticated:
            return

        interval = int(
            SettingsService.get_setting("upload_interval_minutes", "5")
        ) * 60

        self._config_sync = ConfigSyncManager(
            employee_id     = SessionManager.employee_id,
            device_id       = SessionManager.get_device_id(),
            auth_token      = SessionManager.auth_token,
            on_new_config   = self._apply_new_config,
            on_force_logout = self._handle_force_logout,
            sync_interval   = 60,
        )
        self._config_sync.start()
        LoggerService.log("SchedulerService: ConfigSync started")

    def _apply_new_config(self, config: dict):
        """Server se naya config aaya — settings update karo."""
        min_m  = config.get("screenshot_min_minutes")
        max_m  = config.get("screenshot_max_minutes")
        count  = config.get("screenshot_count")

        if min_m is not None:
            SettingsService.save_setting("screenshot_min_minutes", str(min_m))
        if max_m is not None:
            SettingsService.save_setting("screenshot_max_minutes", str(max_m))
        if count is not None:
            SettingsService.save_setting("screenshot_count", str(count))
        LoggerService.log(f"SchedulerService: config updated — count={count}, "f"interval={min_m}–{max_m} min" )

    def _handle_force_logout(self):
        QTimer.singleShot(0, self.force_logout.emit)
        LoggerService.log("SchedulerService: force_logout signal emitted")

    def reschedule(self):
        for t in self._pending_timers:
            t.stop()
        self._pending_timers.clear()
        self._schedule_shift_screenshots()
        LoggerService.log("SchedulerService: rescheduled after config change")
