from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QTimer, Signal, QMetaObject, Qt, Slot

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
        self._sync_interval = 60
        self._sync_failures = 0
        self._config_sync: ConfigSyncManager | None = None


    def start(self):
        """Login ke baad call karo — schedule generate karo aur timers lagao."""
        self._schedule_shift_screenshots()
        self._sync_timer.start(1000)   # har second — sync retry counter ke liye
        self._start_config_sync()
        # Startup pe purane orphan records cleanup karo
        try:
            SyncManager.cleanup_old_orphans(days=7)
        except Exception as e:
            LoggerService.log(f"SchedulerService: cleanup_old_orphans failed on startup — {e}")
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
            LoggerService.log_verbose("SchedulerService: shift times not found, using 8hr window from now")

        # Timezone fix: dono ko naive bana do comparison ke liye
        if shift_start.tzinfo is not None:
            shift_start = shift_start.replace(tzinfo=None)
        if shift_end.tzinfo is not None:
            shift_end = shift_end.replace(tzinfo=None)
        effective_start = max(shift_start, now)
        if effective_start >= shift_end:
            LoggerService.log_verbose("SchedulerService: shift already ended, no screenshots scheduled")
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
            LoggerService.log_verbose(
                f"SchedulerService: screenshot scheduled at {ts.strftime('%H:%M:%S')} "
                f"(in {delay_ms//1000}s)"
            )

    def _fire_screenshot(self):
        """Timer fire hone par signal emit karo."""
        self.screenshot_triggered.emit()


    def _sync_tick(self):
        self._sync_counter += 1
        if self._sync_counter >= self._sync_interval:
            try:
                SyncManager.retry_uploads()
                SyncManager.retry_logs()
                # Success - reset to base interval (60s)
                self._sync_failures = 0
                self._sync_interval = 60
            except Exception as e:
                # Exponential backoff: 60 -> 120 -> 240 -> 480 -> 600 max
                self._sync_failures += 1
                self._sync_interval = min(60 * (2 ** self._sync_failures), 600)
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
            sync_interval   = 5,
        )
        self._config_sync.start()
        LoggerService.log_verbose("SchedulerService: ConfigSync started")

    def _apply_new_config(self, config: dict):
        """
        Server se naya config aaya — settings update karo.

        NOTE: ConfigSyncManager isko background thread se call karta hai,
        is liye yahan koi bhi QObject/QTimer creation seedha nahi karna —
        reschedule() ko main thread pe QMetaObject.invokeMethod se hi
        trigger karo. Saath hi, sirf tab reschedule karo jab values
        actually badli hon — warna har poll (5s) pe naya schedule
        generate hoga, chahe server same config hi baar baar bheje.
        """
        min_m  = config.get("screenshot_min_minutes")
        max_m  = config.get("screenshot_max_minutes")
        count  = config.get("screenshot_count")

        old_min   = SettingsService.get_setting("screenshot_min_minutes")
        old_max   = SettingsService.get_setting("screenshot_max_minutes")
        old_count = SettingsService.get_setting("screenshot_count")

        # Sirf un fields ko diff karo jo is payload mein actually present hain.
        # (Partial config aane par missing fields ko false-positive "changed"
        # na maana jaaye.)
        changed = (
            (min_m is not None and str(old_min) != str(min_m))
            or (max_m is not None and str(old_max) != str(max_m))
            or (count is not None and str(old_count) != str(count))
        )

        if min_m is not None:
            SettingsService.save_setting("screenshot_min_minutes", str(min_m))
        if max_m is not None:
            SettingsService.save_setting("screenshot_max_minutes", str(max_m))
        if count is not None:
            SettingsService.save_setting("screenshot_count", str(count))

        # Shift timing update karo SessionManager mein bhi
        shift = config.get("shift")
        if shift:
            start_ist = shift.get("start_ist")
            end_ist   = shift.get("end_ist")
            if start_ist and end_ist:
                old_start = SessionManager.shift_start
                old_end   = SessionManager.shift_end
                SessionManager.shift_start = start_ist
                SessionManager.shift_end   = end_ist
                if old_start != start_ist or old_end != end_ist:
                    changed = True
                    LoggerService.log(f"SchedulerService: shift updated {start_ist}–{end_ist}")

        if changed:
            LoggerService.log_verbose(
                f"SchedulerService: config updated — count={count}, "
                f"interval={min_m}–{max_m} min"
            )
            # Background thread se main thread pe safely hop karo before
            # reschedule() chalaye, kyunki reschedule() QTimer banata hai
            # (QObject children sirf apne owning thread pe create ho sakte hain).
            QMetaObject.invokeMethod(
                self,
                "_do_reschedule",
                Qt.ConnectionType.QueuedConnection,
            )

    @Slot()
    def _do_reschedule(self):
        self.reschedule()

    def _handle_force_logout(self):
        QMetaObject.invokeMethod(self, "_emit_force_logout", Qt.ConnectionType.QueuedConnection)

    @Slot()
    def _emit_force_logout(self):
        self.force_logout.emit()


    def reschedule(self):
        for t in self._pending_timers:
            t.stop()
        self._pending_timers.clear()
        self._schedule_shift_screenshots()
        LoggerService.log_verbose("SchedulerService: rescheduled after config change")
