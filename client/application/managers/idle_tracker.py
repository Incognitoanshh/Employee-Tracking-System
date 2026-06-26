import ctypes
import platform
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

from client.services.logger_service import LoggerService
from client.services.settings_service import SettingsService
from client.infrastructure.database.database import Database
from client.application.managers.session_manager import SessionManager

# macOS support
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

        # Load once on init (can be reloaded explicitly / on timer)
        self.idle_threshold = int(
            SettingsService.get_setting(
                "idle_threshold_seconds",
                "60",
            )
        )

        print(
            f"[IDLE TRACKER] Threshold = "
            f"{self.idle_threshold} seconds"
        )

        self._reload_every_n_checks = 10
        self._check_counter = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_idle)


    def start(self):

        print(
            f"[IDLE TRACKER] Started "
            f"({platform.system()})"
        )

        self.timer.start(2000)

    def stop(self):
        self.timer.stop()

    def reload_threshold(self):

        self.idle_threshold = int(
            SettingsService.get_setting(
                "idle_threshold_seconds",
                "60"
            )
        )

        print(
            f"[IDLE TRACKER] "
            f"Threshold Reloaded = "
            f"{self.idle_threshold}"
        )

    def _get_idle_seconds(self):

        system = platform.system()

        # WINDOWS
        if system == "Windows":

            try:

                lii = _LASTINPUTINFO()
                lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)

                ctypes.windll.user32.GetLastInputInfo(
                    ctypes.byref(lii)
                )

                millis = (
                    ctypes.windll.kernel32.GetTickCount()
                    - lii.dwTime
                )

                return millis / 1000.0

            except Exception as error:

                print(
                    f"[WINDOWS IDLE ERROR] "
                    f"{error}"
                )

                return 0.0

            # MAC
        elif system == "Darwin":

            try:

                if Quartz is None:
                    return 0.0

                idle = Quartz.CGEventSourceSecondsSinceLastEventType(
                    Quartz.kCGEventSourceStateCombinedSessionState,
                    Quartz.kCGAnyInputEventType
                )

                return float(idle)

            except Exception as error:

                print(
                    f"[MAC IDLE ERROR] "
                    f"{error}"
                )

                return 0.0

            return 0.0


    def check_idle(self):

        # Reload threshold only periodically to keep settings responsive
        # without causing frequent DB reads.
        self._check_counter += 1
        if self._check_counter >= self._reload_every_n_checks:
            self.reload_threshold()
            self._check_counter = 0

        idle_seconds = self._get_idle_seconds()

        print(
            f"[IDLE CHECK] "
            f"idle={idle_seconds:.1f}s "
            f"threshold={self.idle_threshold}s"
        )

        if idle_seconds >= self.idle_threshold:
            # Became idle
            if not self.is_idle:
                self.is_idle = True
                self.status_changed.emit("IDLE")
                self.save_log("IDLE")
                LoggerService.log(
                    f"USER IDLE ({idle_seconds:.1f}s)"
                )
                print("[IDLE STATUS] IDLE")
        else:
            # Became working
            if self.is_idle:
                self.is_idle = False
                self.status_changed.emit("WORKING")
                self.save_log("WORKING")
                LoggerService.log("USER ACTIVE")
                print("[IDLE STATUS] WORKING")



    def save_log(self, status):

        try:

            connection = Database.connect()

            cursor = connection.cursor()

            cursor.execute(
                """
                INSERT INTO idle_logs
                (
                    employee_id,
                    status,
                    timestamp
                )
                VALUES (?, ?, ?)
                """,
                (
                    SessionManager.employee_id,
                    status,
                    datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                )
            )

            connection.commit()
            connection.close()

            print(
                f"[IDLE STATUS SAVED] {status}"
            )

        except Exception as error:

            print(
                f"[IDLE LOG ERROR] "
                f"{error}"
            )

    def reset_activity(self):

        if self.is_idle:
            self.is_idle = False

            self.status_changed.emit("WORKING")

            self.save_log("WORKING")

            LoggerService.log("USER ACTIVE")

            print("[IDLE STATUS] WORKING")