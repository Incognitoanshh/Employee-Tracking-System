"""Weekly offs and holidays, as the scheduler sees them.

Two rules are being pinned here, and both are the kind that look right in
review and are wrong in production:

1. A non-working day means ZERO captures — not fewer, not spread differently.

2. An overnight shift belongs to the day it STARTS. A 22:00-06:00 shift
   beginning on Saturday night runs into Sunday; if Sunday is the weekly off,
   that shift must still be captured in full. Checking "is today a working
   day" against the current clock instead would silently cut every overnight
   shift in half at midnight, and only for night workers.

Everything the server can send is also fed in malformed on purpose. A
calendar that cannot be parsed has to mean "this is a working day": failing
towards capturing is a nuisance, failing the other way switches monitoring
off with no error anywhere, which this project has already shipped twice.

Run:  python3 tests/test_work_calendar.py
"""
from __future__ import annotations

import os
import random
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []


def check(label, ok, detail=""):
    if not ok:
        failures.append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          f"{'' if ok or not detail else f'  — {detail}'}")


def main() -> int:
    os.environ["ETS_DATA_DIR"] = tempfile.mkdtemp()
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)

    from client.infrastructure.database.database import Database
    Database.initialize()
    from client.services.settings_service import SettingsService
    from client.application.managers.session_manager import SessionManager
    import client.application.schedulers.scheduler_service as ss
    import client.application.managers.screenshot_manager as smod
    from client.core.work_calendar import is_working_day, day_off_reason

    SettingsService.save_setting("screenshots_per_day", "10")
    SettingsService.save_setting("screenshot_min_minutes", "1")
    SessionManager.employee_id = "CALTEST"
    SessionManager.role = "employee"

    def plan(shift_start, shift_end, moment, offs="", holidays=""):
        SettingsService.save_setting("weekly_offs", offs)
        SettingsService.save_setting("holidays", holidays)
        SessionManager.shift_start = shift_start
        SessionManager.shift_end = shift_end
        ss.now_ist = lambda m=moment: m
        smod.now_ist = lambda m=moment: m
        armed = {}
        ss.SchedulerService._arm_timers = (
            lambda self, ts, now: armed.update(ts=list(ts))
        )
        random.seed(20260805)
        ss.SchedulerService()._schedule_shift_screenshots()
        return armed.get("ts", [])

    # August 2026: 5th Wed, 8th Sat, 9th Sun, 10th Mon.
    print("Day shift 09:00-18:00, 10 per day, employee logged in at 10:00\n")

    check("an ordinary Wednesday is captured",
          len(plan("09:00", "18:00", datetime(2026, 8, 5, 10, 0), offs="7")) == 10)
    check("Sunday with Sunday off gets nothing",
          plan("09:00", "18:00", datetime(2026, 8, 9, 10, 0), offs="7") == [])
    check("Saturday with only Sunday off is still captured",
          len(plan("09:00", "18:00", datetime(2026, 8, 8, 10, 0), offs="7")) == 10)
    check("Saturday with a two-day weekend gets nothing",
          plan("09:00", "18:00", datetime(2026, 8, 8, 10, 0), offs="6,7") == [])
    check("a declared holiday gets nothing",
          plan("09:00", "18:00", datetime(2026, 8, 15, 10, 0),
               holidays="2026-08-15") == [])
    check("a holiday on another date changes nothing",
          len(plan("09:00", "18:00", datetime(2026, 8, 5, 10, 0),
                   holidays="2026-08-15")) == 10)

    print("\nOvernight shift 22:00-06:00, Sunday is the weekly off\n")

    check("Saturday night is captured",
          len(plan("22:00", "06:00", datetime(2026, 8, 8, 23, 0), offs="7")) == 10)
    check("02:00 Sunday is still Saturday's shift, so captured",
          len(plan("22:00", "06:00", datetime(2026, 8, 9, 2, 0), offs="7")) == 10)
    check("Sunday night is Sunday's shift, so nothing",
          plan("22:00", "06:00", datetime(2026, 8, 9, 23, 0), offs="7") == [])
    check("02:00 Monday belongs to Sunday's shift, so nothing",
          plan("22:00", "06:00", datetime(2026, 8, 10, 2, 0), offs="7") == [])
    check("02:00 Tuesday belongs to Monday's shift, so captured",
          len(plan("22:00", "06:00", datetime(2026, 8, 11, 2, 0), offs="7")) == 10)

    print("\nAnything unparseable must mean 'working day'\n")

    for label, offs, holidays in [
        ("empty strings",        "",            ""),
        ("the text 'None'",      "None",        "None"),
        ("whitespace",           "  ",          "  , "),
        ("weekday 0 and 8",      "0,8",         ""),
        ("non-numeric weekday",  "sunday",      ""),
        ("a half-written date",  "",            "2026-08"),
        ("a date with slashes",  "",            "2026/08/05"),
        ("trailing commas",      "7,",          "2026-08-15,"),
    ]:
        captured = plan("09:00", "18:00", datetime(2026, 8, 5, 10, 0), offs, holidays)
        # 5 Aug 2026 is a Wednesday and is not 15 Aug, so every one of these
        # must leave it a normal working day.
        check(f"{label} leaves Wednesday captured", len(captured) == 10,
              f"got {len(captured)}")

    # 'trailing commas' above also proves the valid parts of a messy value
    # still work, rather than the whole thing being discarded.
    SettingsService.save_setting("weekly_offs", "7,")
    SettingsService.save_setting("holidays", "2026-08-15,")
    check("a valid weekday survives a trailing comma",
          not is_working_day(datetime(2026, 8, 9).date()))
    check("a valid holiday survives a trailing comma",
          not is_working_day(datetime(2026, 8, 15).date()))
    check("the reason names the weekday",
          "Sunday" in (day_off_reason(datetime(2026, 8, 9).date()) or ""),
          str(day_off_reason(datetime(2026, 8, 9).date())))
    check("the reason for a holiday says holiday",
          day_off_reason(datetime(2026, 8, 15).date()) == "holiday")

    print()
    if failures:
        print(f"{len(failures)} failure(s)")
        return 1
    print("all work calendar checks passed")
    return 0


if __name__ == "__main__":
    print("Weekly offs and holidays, as the scheduler sees them\n")
    sys.exit(main())
