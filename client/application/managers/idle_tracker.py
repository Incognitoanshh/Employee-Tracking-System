import ctypes
import platform
import time
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

from client.services.logger_service import LoggerService
from client.services.settings_service import SettingsService
from client.infrastructure.database.database import Database
from client.application.managers.session_manager import SessionManager
from client.core.config.settings import Settings
from client.core.time_ist import ist_day_str, now_ist

try:
    import Quartz
except Exception:
    Quartz = None


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_uint),
    ]


class IdleTracker(QObject):

    status_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.is_idle = False
        self.idle_threshold = int(
            SettingsService.get_setting(
                "idle_threshold_seconds", str(Settings.IDLE_THRESHOLD)
            )
        )
        self._reload_every_n_checks = 10
        self._check_counter = 0
        # Idle time is accumulated as it passes rather than derived from
        # IDLE/ACTIVE pairs — see the idle_daily table for why.
        self._last_tick: float | None = None
        self._idle_carry = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_idle)

    def start(self):
        # Super admin is the owner/manager, not a tracked employee — the same
        # rule screenshots already follow (scheduler_service.py and
        # screenshot_manager.py both skip this role).
        #
        # Without this guard the tracker ran for super admins too and wrote
        # USER IDLE / USER ACTIVE straight into the audit log via
        # LoggerService.log() inside check_idle(). Those are not verbose
        # messages, so quieting SchedulerService did not cover them.
        if getattr(SessionManager, "role", "") == "super_admin":
            LoggerService.log_verbose(
                "IdleTracker: super admin — idle tracking disabled for this account"
            )
            return
        self.timer.start(2000)

    def stop(self):
        self.timer.stop()

    def reload_threshold(self):
        self.idle_threshold = int(
            SettingsService.get_setting(
                "idle_threshold_seconds", str(Settings.IDLE_THRESHOLD)
            )
        )

    def _get_idle_seconds(self):
        system = platform.system()
        if system == "Windows":
            try:
                lii = _LASTINPUTINFO()
                lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
                ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
                millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
                return millis / 1000.0
            except Exception as e:
                LoggerService.log(f"[IdleTracker] Windows idle detection failed: {e}")
                return 0.0
        elif system == "Darwin":
            try:
                if Quartz is None:
                    LoggerService.log("[IdleTracker] Quartz not available on macOS — idle detection disabled")
                    return 0.0
                idle = Quartz.CGEventSourceSecondsSinceLastEventType(
                    Quartz.kCGEventSourceStateCombinedSessionState,
                    Quartz.kCGAnyInputEventType
                )
                return float(idle)
            except Exception as e:
                LoggerService.log(f"[IdleTracker] macOS idle detection failed: {e}")
                return 0.0
        LoggerService.log(f"[IdleTracker] Unsupported platform: {system} — idle detection disabled")
        return 0.0

    def _accumulate_idle(self, idle_seconds: float):
        """
        Add the time since the last check to today's idle total.

        Counted from wall-clock elapsed rather than from the OS idle figure,
        so a machine that slept or a timer that was starved does not inflate
        the total. `_last_tick` is None on the first check after start, which
        is why nothing is added then.

        Anything longer than a minute between ticks is discarded: that is the
        machine having been asleep or suspended, not somebody sitting there
        idle, and counting it would quietly turn an overnight sleep into
        eight hours of idle time.
        """
        now = time.monotonic()
        previous, self._last_tick = self._last_tick, now
        if previous is None:
            return

        elapsed = now - previous
        if elapsed <= 0 or elapsed > 60:
            return
        if idle_seconds < self.idle_threshold:
            return

        self._idle_carry += elapsed
        # Written in whole seconds so the row is not rewritten on every tick.
        whole = int(self._idle_carry)
        if whole < 1:
            return
        self._idle_carry -= whole

        try:
            day = ist_day_str(now_ist())
            connection = Database.connect()
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO idle_daily (employee_id, day, idle_seconds, uploaded)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(employee_id, day) DO UPDATE SET
                    idle_seconds = idle_seconds + excluded.idle_seconds,
                    uploaded = 0
                """,
                (SessionManager.employee_id, day, whole),
            )
            connection.commit()
            connection.close()
        except Exception:
            # Losing a second of idle time is not worth interrupting
            # tracking for. The next tick tries again.
            self._idle_carry += whole

    def check_idle(self):
        self._check_counter += 1
        if self._check_counter >= self._reload_every_n_checks:
            self.reload_threshold()
            self._check_counter = 0

        idle_seconds = self._get_idle_seconds()
        self._accumulate_idle(idle_seconds)

        if idle_seconds >= self.idle_threshold:
            if not self.is_idle:
                self.is_idle = True
                self.status_changed.emit("IDLE")
                self.save_log("IDLE")
                LoggerService.log(f"USER IDLE ({idle_seconds:.1f}s)")
        else:
            if self.is_idle:
                self.is_idle = False
                self.status_changed.emit("WORKING")
                self.save_log("WORKING")
                LoggerService.log("USER ACTIVE")

    def save_log(self, status):
        try:
            connection = Database.connect()
            cursor = connection.cursor()
            cursor.execute(
                "INSERT INTO idle_logs (employee_id, status, timestamp) VALUES (?, ?, ?)",
                (SessionManager.employee_id, status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            connection.commit()
            connection.close()
        except Exception:
            pass

    def reset_activity(self):
        if self.is_idle:
            self.is_idle = False
            self.status_changed.emit("WORKING")
            self.save_log("WORKING")
            LoggerService.log("USER ACTIVE")
