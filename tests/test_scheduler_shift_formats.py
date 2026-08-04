"""Every shift-string format the client can actually hold must schedule.

This exists because a NameError on the tz-aware branch shipped to
production and produced zero screenshots for a whole session. The tests
at the time only used plain "09:00", which takes the strptime fallback
and never reaches the timezone-conversion line — so they all passed while
the real client, which stores full ISO with a +05:30 offset, raised on
every scheduling attempt.

The formats below are the ones observed in the wild:
  - ISO with offset : what /config/sync stores in settings
  - ISO in UTC      : same, if the server is ever changed to emit UTC
  - HH:MM           : what the admin config path writes
  - HH:MM:SS        : what a Postgres TIME column yields
  - None            : login response without shift information

Run: python3 tests/test_scheduler_shift_formats.py
"""
import os
import sys
import tempfile

os.environ.setdefault("ETS_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from client.infrastructure.database.database import Database
Database.initialize()
from client.services.settings_service import SettingsService
from client.application.managers.session_manager import SessionManager
import client.application.schedulers.scheduler_service as ss

CASES = [
    ("ISO with +05:30 offset", "2026-08-04T09:00:00+05:30", "2026-08-04T18:00:00+05:30"),
    ("ISO in UTC",             "2026-08-04T03:30:00+00:00", "2026-08-04T12:30:00+00:00"),
    ("plain HH:MM",            "09:00",                     "18:00"),
    ("HH:MM:SS",               "09:00:00",                  "18:00:00"),
    ("no shift information",   None,                        None),
    ("overnight, ISO",         "2026-08-04T22:00:00+05:30", "2026-08-04T06:00:00+05:30"),
    ("overnight, HH:MM",       "22:00",                     "06:00"),
]


def main() -> int:
    SettingsService.save_setting("screenshots_per_day", "10")
    SettingsService.save_setting("screenshot_min_minutes", "1")
    SettingsService.save_setting("screenshot_max_minutes", "10")
    SessionManager.employee_id = "TEST001"
    SessionManager.auth_token = "t"
    SessionManager.role = "employee"

    failures = 0
    for label, start, end in CASES:
        SessionManager.shift_start = start
        SessionManager.shift_end = end
        armed = {}
        ss.SchedulerService._arm_timers = (
            lambda self, ts, now: armed.update(n=len(ts))
        )
        try:
            scheduler = ss.SchedulerService()
            scheduler._schedule_shift_screenshots()
            count = armed.get("n", 0)
            # Zero is a failure too: the whole point is that some capture
            # gets scheduled for every format the client can hold.
            ok = count > 0
        except Exception as error:
            count = f"{type(error).__name__}: {error}"
            ok = False
        failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label:26} scheduled={count}")

    print()
    if failures:
        print(f"{failures} of {len(CASES)} shift formats did not schedule")
        return 1
    print(f"all {len(CASES)} shift formats schedule correctly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
