import ctypes
import platform
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

from client.services.logger_service import LoggerService
from client.services.settings_service import SettingsService
from client.infrastructure.database.database import Database
from client.application.managers.session_manager import SessionManager

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

        self.is_idle        = False
        self._idle_start_dt: datetime | None = None  # ✅ Duration track ke liye

        self.idle_threshold = int(
            SettingsService.get_setting("idle_threshold_seconds", "60")
        )

        # Threshold reload: har 10 checks = har 20 seconds (timer=2s × 10)
        self._reload_every_n_checks = 10
        self._check_counter         = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_idle)

    def start(self):
        LoggerService.log(f"IdleTracker: started on {platform.system()}")
        self.timer.start(2000)

    def stop(self):
        self.timer.stop()

    def reload_threshold(self):
        self.idle_threshold = int(
            SettingsService.get_setting("idle_threshold_seconds", "60")
        )

    def _get_idle_seconds(self) -> float:
        system = platform.system()

        if system == "Windows":
            try:
                lii        = _LASTINPUTINFO()
                lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
                ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
                millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
                return millis / 1000.0
            except Exception as e:
                LoggerService.log(f"IdleTracker Windows error: {e}")
                return 0.0

        elif system == "Darwin":
            try:
                if Quartz is None:
                    return 0.0
                idle = Quartz.CGEventSourceSecondsSinceLastEventType(
                    Quartz.kCGEventSourceStateCombinedSessionState,
                    Quartz.kCGAnyInputEventType,
                )
                return float(idle)
            except Exception as e:
                LoggerService.log(f"IdleTracker macOS error: {e}")
                return 0.0

            # Linux / other: not supported in v1
        return 0.0


    def check_idle(self):
        self._check_counter += 1
        if self._check_counter >= self._reload_every_n_checks:
            self.reload_threshold()
            self._check_counter = 0

        idle_seconds = self._get_idle_seconds()

        if idle_seconds >= self.idle_threshold:
            # User abhi idle hai
            if not self.is_idle:
                self.is_idle        = True
                self._idle_start_dt = datetime.now()
                self.status_changed.emit("IDLE")
                LoggerService.log(f"IdleTracker: user IDLE ({idle_seconds:.1f}s)")
        else:
            # User active ho gaya
            if self.is_idle:
                self.is_idle     = False
                idle_end_dt      = datetime.now()
                duration_seconds = int(
                    (idle_end_dt - self._idle_start_dt).total_seconds()
                ) if self._idle_start_dt else 0
                self._save_idle_period(
                    idle_start = self._idle_start_dt or idle_end_dt,
                    idle_end   = idle_end_dt,
                    duration   = duration_seconds,
                )
                self._idle_start_dt = None
                self.status_changed.emit("WORKING")
                LoggerService.log(
                    f"IdleTracker: user ACTIVE — idle was {duration_seconds}s"
                )

    def _save_idle_period(
        self,
        idle_start: datetime,
        idle_end:   datetime,
        duration:   int,
    ):
        """Complete idle period DB mein save karo — start+end+duration."""
        try:
            with Database.get_connection() as conn:
                conn.cursor().execute(
                    """
                    INSERT INTO idle_logs
                    (employee_id, session_id,
                    idle_start, idle_end,
                    duration_seconds, upload_status)
                    VALUES (?, ?, ?, ?, ?, 'PENDING')
                    """,
                    (
                        SessionManager.employee_id,
                        SessionManager.session_id,
                        idle_start.strftime("%Y-%m-%d %H:%M:%S"),
                        idle_end.strftime("%Y-%m-%d %H:%M:%S"),
                        duration,
                    ),
                )
            LoggerService.log(
                f"IdleTracker: idle period saved "
                f"{idle_start.strftime('%H:%M:%S')}–"
                f"{idle_end.strftime('%H:%M:%S')} "
                f"({duration}s)"
            )
        except Exception as e:
            LoggerService.log(f"IdleTracker DB error: {e}")

    def reset_activity(self):
        """Manual override — jab force active karna ho."""
        if self.is_idle:
            self.is_idle     = False
            idle_end_dt      = datetime.now()
            duration_seconds = int(
                (idle_end_dt - self._idle_start_dt).total_seconds()
            ) if self._idle_start_dt else 0

            self._save_idle_period(
                idle_start = self._idle_start_dt or idle_end_dt,
                idle_end   = idle_end_dt,
                duration   = duration_seconds,
            )
            self._idle_start_dt = None
            self.status_changed.emit("WORKING")
            LoggerService.log("IdleTracker: manually reset to ACTIVE")