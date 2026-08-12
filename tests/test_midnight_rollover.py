"""What happens when a client is still running at IST midnight.

Employees do not shut their laptops. The app runs for days, and at some point
the day it planned for ends and a new one has to be planned. That handover is
the least-exercised code in the project and has the worst record: it once left
a client running with a spent budget and no new schedule, producing zero
screenshots for a whole day with nothing in any log to say why.

It is also the one thing CI could not reach, because CI runs a single process
with a frozen clock. This drives the clock forward through midnight instead —
minute by minute, through the real `_sync_tick` the app runs every second —
so the rollover happens the way it does in production rather than by calling
reschedule() directly and assuming that is the same thing.

Not a substitute for two machines left running overnight, which is the only
way to catch an operating system suspending a timer. It does cover the logic.

Run:  python3 tests/test_midnight_rollover.py
"""
from __future__ import annotations

import os
import random
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ISOLATED FROM ANYTHING REAL, BEFORE ANY CLIENT MODULE IS IMPORTED.
#
# Without these two the client's own config falls back to its defaults: the
# installed app's data directory, and the PRODUCTION server. Running this file
# on a machine that has the app installed then wrote into the real local
# database and uploaded to the real server — rows under a made-up employee id
# turned up in the company's audit log, which is exactly what happened.
#
# setdefault, so a harness that points these somewhere on purpose still wins.
os.environ.setdefault("ETS_DATA_DIR", tempfile.mkdtemp(prefix="ets_test_"))
os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:9/api")

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
    from client.core.time_ist import ist_day_str

    SettingsService.save_setting("screenshot_min_minutes", "1")
    SettingsService.save_setting("weekly_offs", "")
    SettingsService.save_setting("holidays", "")
    SessionManager.employee_id = "ROLLOVER"
    SessionManager.auth_token = "t"
    SessionManager.role = "employee"

    clock = {"now": datetime(2026, 8, 6, 9, 0)}
    ss.now_ist = lambda: clock["now"]
    smod.now_ist = lambda: clock["now"]

    # Record every plan the scheduler makes, and pretend each capture happened
    # so the daily budget is really consumed — a rollover that hands out a
    # fresh allowance is exactly the failure worth catching.
    plans = []
    captures = []

    def fake_arm(self, timestamps, now):
        plans.append({"at": now, "times": list(timestamps)})

    ss.SchedulerService._arm_timers = fake_arm

    def record_capture(day, when):
        connection = Database.connect()
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO screenshots (id, employee_id, file_path, timestamp) "
            "VALUES (?, ?, ?, ?)",
            (f"{day}-{len(captures)}", "ROLLOVER", "/tmp/x.enc",
             when.strftime("%Y-%m-%d %H:%M:%S")),
        )
        connection.commit()
        connection.close()
        captures.append((day, when))

    def run_until(target, scheduler):
        """Advance a minute at a time through the real tick, capturing on cue."""
        while clock["now"] < target:
            clock["now"] += timedelta(minutes=1)
            # Anything the current plan scheduled for this minute "happens".
            if plans:
                # Compare to the minute. Scheduled times carry random seconds
                # and microseconds; the driven clock does not.
                minute = clock["now"].strftime("%Y-%m-%d %H:%M")
                for when in plans[-1]["times"]:
                    if when.strftime("%Y-%m-%d %H:%M") == minute:
                        if smod.ScreenshotManager.captures_today() < \
                           smod.ScreenshotManager.screenshots_per_day():
                            record_capture(ist_day_str(clock["now"]), clock["now"])
            # _check_shift_rollover only acts once a minute, and is driven by
            # a per-second counter — so give it sixty ticks per minute.
            scheduler._rollover_counter = 59
            scheduler._check_shift_rollover()

    print("Shift 09:00-18:00, 10 per day, client left running for two days\n")
    SettingsService.save_setting("screenshots_per_day", "10")
    SessionManager.shift_start = "09:00"
    SessionManager.shift_end = "18:00"

    random.seed(20260806)
    scheduler = ss.SchedulerService()
    scheduler._schedule_shift_screenshots()
    check("day one is planned on sign-in", len(plans) == 1 and len(plans[0]["times"]) == 10,
          f"{len(plans)} plan(s)")

    run_until(datetime(2026, 8, 6, 23, 59), scheduler)
    day_one = [c for c in captures if c[0] == "2026-08-06"]
    check("day one captures exactly the configured number", len(day_one) == 10,
          str(len(day_one)))
    check("all of them fall inside the shift",
          all(9 <= w.hour < 18 for _, w in day_one),
          f"{min(w for _, w in day_one):%H:%M}-{max(w for _, w in day_one):%H:%M}")

    plans_before_midnight = len(plans)

    # ── across midnight ─────────────────────────────────────────────────
    run_until(datetime(2026, 8, 7, 9, 30), scheduler)

    check("the rollover produced a new plan after midnight",
          len(plans) > plans_before_midnight,
          f"{len(plans)} vs {plans_before_midnight}")

    new_plan = plans[-1]
    check("the new plan is for the NEW day",
          new_plan["times"] and new_plan["times"][0].date() == datetime(2026, 8, 7).date(),
          str(new_plan["times"][:1]))
    check("and it is a full day's allowance again, not a leftover",
          len(new_plan["times"]) == 10, str(len(new_plan["times"])))
    check("planned inside day two's shift",
          all(9 <= w.hour < 18 for w in new_plan["times"]),
          f"{new_plan['times'][0]:%H:%M}-{new_plan['times'][-1]:%H:%M}")

    run_until(datetime(2026, 8, 7, 23, 59), scheduler)
    day_two = [c for c in captures if c[0] == "2026-08-07"]
    check("day two captures exactly the configured number too", len(day_two) == 10,
          str(len(day_two)))
    check("day one's total was not disturbed",
          len([c for c in captures if c[0] == "2026-08-06"]) == 10)
    check("nothing was captured between the shifts",
          not any(18 <= w.hour or w.hour < 9 for _, w in captures),
          str([f"{w:%d %H:%M}" for _, w in captures if w.hour >= 18 or w.hour < 9][:3]))

    # ── the overnight case, where the rollover lands mid-shift ──────────
    print("\nOvernight shift 22:00-06:00, 10 per day — rollover happens MID-shift\n")
    plans.clear()
    captures.clear()
    connection = Database.connect()
    connection.cursor().execute("DELETE FROM screenshots")
    connection.commit()
    connection.close()

    SessionManager.shift_start = "22:00"
    SessionManager.shift_end = "06:00"
    clock["now"] = datetime(2026, 8, 6, 21, 30)
    random.seed(20260806)
    scheduler = ss.SchedulerService()
    scheduler._schedule_shift_screenshots()

    run_until(datetime(2026, 8, 7, 7, 0), scheduler)
    before = [c for c in captures if c[0] == "2026-08-06"]
    after = [c for c in captures if c[0] == "2026-08-07"]
    check("captures happen before midnight", len(before) > 0, str(len(before)))
    check("and after it", len(after) > 0, str(len(after)))
    check("neither day exceeds the configured number",
          len(before) <= 10 and len(after) <= 10,
          f"{len(before)} / {len(after)}")
    check("every capture is inside the overnight window",
          all(w.hour >= 22 or w.hour < 6 for _, w in captures),
          str([f"{w:%d %H:%M}" for _, w in captures if 6 <= w.hour < 22][:3]))


    # ── the lid was shut for three hours ────────────────────────────────
    #
    # Qt's single-shot timers do not survive a sleeping machine: every
    # deadline that passed while it slept comes due the instant it wakes.
    # Measured — thirteen overdue timers fired inside a tenth of a second.
    # The daily cap stopped it at ten, so nothing broke, but the result was
    # ten identical pictures taken in the same moment and the whole day's
    # allowance gone, leaving the rest of the shift blank.
    print("\nAfter the machine has been asleep")
    from PySide6.QtCore import QObject
    from client.application.schedulers.scheduler_service import SchedulerService

    sched = SchedulerService.__new__(SchedulerService)
    QObject.__init__(sched)
    fired = []
    sched.screenshot_triggered = type(
        "S", (), {"emit": lambda self: fired.append(1)})()

    # The clock the SCHEDULER sees, not the real one. This test drives a
    # frozen clock through ss.now_ist; reading the wall clock here would
    # compare two different times and make nothing look late at all.
    right_now = ss.now_ist()
    for minutes in (180, 150, 120, 90, 60, 30):
        sched._fire_screenshot(right_now - timedelta(minutes=minutes))
    check("captures that are hours overdue are dropped, not taken at once",
          len(fired) == 0,
          f"{len(fired)} identical pictures in one instant, and the day's "
          f"allowance spent")

    fired.clear()
    for seconds in (0, 30, 120):
        sched._fire_screenshot(right_now - timedelta(seconds=seconds))
    check("but ordinary jitter still counts as on time", len(fired) == 3,
          f"{len(fired)} of 3 — a busy machine would lose captures")

    fired.clear()
    sched._fire_screenshot(None)
    check("a capture with no scheduled time is still taken",
          len(fired) == 1, "anything not armed by the scheduler would be lost")

    print()
    if failures:
        print(f"{len(failures)} failure(s)")
        return 1
    print("all midnight rollover checks passed")
    return 0


if __name__ == "__main__":
    print("Midnight rollover — the handover from one IST day to the next\n")
    sys.exit(main())
