"""The scheduler must produce an identical plan on every client machine.

A Windows box in Los Angeles, a Mac in Mumbai and a Linux box in London,
all given the same IST shift and the same configuration, must schedule the
same captures at the same IST moments.

This file exists because three separate production incidents came from
that not being true:

  * the window was built in machine-local time, so a Pacific client
    scheduled 130 captures where 10 were configured;
  * two parsing branches disagreed, and the one production used was the
    one the tests did not cover;
  * consolidating the timezone helpers left a dangling reference, and
    every scheduling attempt raised NameError — zero screenshots, no
    visible error.

Run:  python3 tests/test_scheduler_timezones.py
      (it re-executes itself once per timezone)
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
from datetime import date, datetime

TIMEZONES = ["Asia/Kolkata", "UTC", "America/Los_Angeles", "Europe/London"]

# Every shape the client has been observed to hold, plus the malformed
# cases that must degrade safely rather than disable monitoring.
SHIFT_FORMATS = [
    ("ISO with +05:30",   "2026-08-04T09:00:00+05:30", "2026-08-04T18:00:00+05:30"),
    ("ISO in UTC",        "2026-08-04T03:30:00+00:00", "2026-08-04T12:30:00+00:00"),
    ("ISO, no offset",    "2026-08-04T09:00:00",       "2026-08-04T18:00:00"),
    ("ISO, stale date",   "2026-06-29T09:00:00+05:30", "2026-06-29T18:00:00+05:30"),
    ("HH:MM",             "09:00",                     "18:00"),
    ("HH:MM:SS",          "09:00:00",                  "18:00:00"),
    ("overnight ISO",     "2026-08-04T22:00:00+05:30", "2026-08-04T06:00:00+05:30"),
    ("overnight HH:MM",   "22:00",                     "06:00"),
    ("missing",           None,                        None),
    ("malformed",         "not a time",                "also not a time"),
]

# Formats 0-5 all describe the same 09:00-18:00 IST day shift; 6-7 describe
# a 22:00-06:00 overnight shift; the last two have no usable shift at all and
# fall back to a window around login.
DAY_SHIFT_FORMATS = {
    "ISO with +05:30", "ISO in UTC", "ISO, no offset", "ISO, stale date",
    "HH:MM", "HH:MM:SS",
}

# Captures are scheduled inside the shift and nowhere else. For a 09:00-18:00
# shift, a client still running at 20:00 or 23:00 has no shift left today and
# must schedule nothing — the day's remaining budget is simply not spent.
# 02:00 is *before* that day's shift, not after it, so it still plans a full
# day. Overnight and fallback shifts are never empty at any of these moments.
AFTER_DAY_SHIFT = {"after shift 20:00", "overnight inside 23:00"}


def expects_zero(fmt_label: str, moment_label: str) -> bool:
    return fmt_label in DAY_SHIFT_FORMATS and moment_label in AFTER_DAY_SHIFT


# (label, IST moment the scheduler sees)
MOMENTS = [
    ("mid-shift 12:00",        datetime(2026, 8, 4, 12, 0)),
    # Logging in before the shift used to spend the entire day's budget in
    # the gap before work started, leaving nothing for the shift itself.
    ("pre-shift 07:31",        datetime(2026, 8, 4, 7, 31)),
    ("before shift 07:00",     datetime(2026, 8, 4, 7, 0)),
    ("after shift 20:00",      datetime(2026, 8, 4, 20, 0)),
    ("overnight inside 23:00", datetime(2026, 8, 4, 23, 0)),
    ("overnight inside 02:00", datetime(2026, 8, 4, 2, 0)),
]


def collect() -> dict:
    """Run the real scheduler for every combination and return its plan."""
    os.environ["ETS_DATA_DIR"] = tempfile.mkdtemp()
    # And nowhere near a real server — the client's default is production.
    os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:9/api")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)

    from client.infrastructure.database.database import Database
    Database.initialize()
    from client.services.settings_service import SettingsService
    from client.application.managers.session_manager import SessionManager
    import client.application.schedulers.scheduler_service as ss
    import client.core.time_ist as tz

    SettingsService.save_setting("screenshots_per_day", "10")
    SettingsService.save_setting("screenshot_min_minutes", "1")
    SettingsService.save_setting("screenshot_max_minutes", "10")
    SessionManager.employee_id = "TZTEST"
    SessionManager.auth_token = "t"
    SessionManager.role = "employee"

    results = {}
    for fmt_label, start, end in SHIFT_FORMATS:
        for moment_label, moment in MOMENTS:
            # Freeze the clock. Patched on the scheduler's own module so we
            # exercise exactly the name it resolves at runtime.
            ss.now_ist = lambda m=moment: m
            tz.now_ist = lambda m=moment: m
            SessionManager.shift_start = start
            SessionManager.shift_end = end

            # Capture times are drawn randomly inside each slot. Seed
            # identically per scenario so the comparison across timezones
            # is about the schedule, not about the RNG.
            random.seed(20260804)

            armed = {}
            ss.SchedulerService._arm_timers = (
                lambda self, ts, now: armed.update(
                    n=len(ts),
                    times=[t.strftime("%d %H:%M:%S") for t in ts],
                )
            )
            key = f"{fmt_label} | {moment_label}"
            try:
                scheduler = ss.SchedulerService()
                scheduler._schedule_shift_screenshots()
                results[key] = {
                    "count": armed.get("n", 0),
                    "times": armed.get("times", []),
                }
            except Exception as error:
                results[key] = {"error": f"{type(error).__name__}: {error}"}
    return results


def main() -> int:
    if os.environ.get("ETS_TZ_CHILD"):
        print(json.dumps(collect()))
        return 0

    here = os.path.abspath(__file__)
    per_tz = {}
    for tzname in TIMEZONES:
        env = dict(os.environ, TZ=tzname, ETS_TZ_CHILD="1")
        proc = subprocess.run([sys.executable, here], capture_output=True,
                              text=True, env=env)
        line = next((l for l in proc.stdout.splitlines()
                     if l.startswith("{")), None)
        if line is None:
            print(f"FAIL  {tzname}: child produced no result\n{proc.stderr[-800:]}")
            return 1
        per_tz[tzname] = json.loads(line)

    baseline_tz = TIMEZONES[0]
    baseline = per_tz[baseline_tz]
    failures = []

    print(f"baseline timezone: {baseline_tz}\n")
    for key in baseline:
        row = baseline[key]
        if "error" in row:
            failures.append(f"{key}: raised {row['error']}")
            print(f"  FAIL  {key:44} {row['error']}")
            continue
        fmt_label, moment_label = key.split(" | ", 1)
        want_zero = expects_zero(fmt_label, moment_label)

        # Silently scheduling nothing is the failure mode that reached
        # production, so an unexpected 0 is still a hard failure. But an
        # expected 0 has to be asserted just as strictly the other way:
        # scheduling anything outside the shift is the bug this policy fixes.
        if row["count"] == 0 and not want_zero:
            failures.append(f"{key}: scheduled nothing")
            print(f"  FAIL  {key:44} scheduled 0")
            continue
        if row["count"] != 0 and want_zero:
            failures.append(f"{key}: scheduled {row['count']} outside the shift")
            print(f"  FAIL  {key:44} scheduled {row['count']} outside the shift")
            continue
        differing = [t for t in TIMEZONES if per_tz[t][key] != row]
        if differing:
            failures.append(f"{key}: differs in {', '.join(differing)}")
            print(f"  FAIL  {key:44} differs in {', '.join(differing)}")
        elif want_zero:
            print(f"  PASS  {key:44} n=0   off-shift, nothing scheduled anywhere")
        else:
            print(f"  PASS  {key:44} n={row['count']:<3} identical in all {len(TIMEZONES)} timezones")

    print()
    if failures:
        print(f"{len(failures)} failure(s)")
        return 1
    print(f"{len(baseline)} scenarios x {len(TIMEZONES)} timezones — all identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
