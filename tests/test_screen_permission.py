"""
macOS Screen Recording — asked at launch, and never faked.

Two failures, both reported from a real Mac, both of which look exactly like
working software:

  * The prompt appeared at the first scheduled capture — a random minute in
    the middle of a shift — and macOS only honours the permission from the
    next launch. Everything scheduled before the person happened to quit and
    reopen was lost. "quit and reopen kiya to ss to gayab na."

  * Without the permission, screen capture on macOS does not fail. It returns
    the desktop picture with no windows in it. Blank screenshots upload and
    store like any other, so the tracking looks alive and shows wallpaper.

Run:  python3 tests/test_screen_permission.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SCREENSHOT_ENCRYPTION_KEY",
                      "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))
    sys.stdout.flush()


def main():
    from client.services import screen_permission as sp

    print("Where no permission exists")
    real_platform = sys.platform
    sys.platform = "win32"
    try:
        check("Windows is not asked for anything macOS invented",
              sp.has_screen_access() is True and sp.ensure_at_startup() is True)
    finally:
        sys.platform = real_platform

    print("\nOn a Mac that has already been allowed")
    real_has = sp.has_screen_access
    sys.platform = "darwin"
    sp.has_screen_access = lambda: True
    asked = []
    real_request = sp.request_screen_access
    sp.request_screen_access = lambda: asked.append(1) or True
    try:
        ok = sp.ensure_at_startup()
    finally:
        sys.platform = real_platform
        sp.has_screen_access = real_has
        sp.request_screen_access = real_request
    check("startup carries straight on", ok is True, str(ok))
    check("and nobody is prompted for a permission they already gave",
          asked == [], str(asked))

    print("\nOn a Mac that has not")
    sys.platform = "darwin"
    sp.has_screen_access = lambda: False
    asked = []
    sp.request_screen_access = lambda: asked.append(1) or False
    told = []
    real_log = sp.LoggerService.log
    sp.LoggerService.log = lambda m, *a, **k: told.append(str(m))
    # The dialog is the part a person sees; suppress it and keep the decision.
    import PySide6.QtWidgets as _qt
    real_box = _qt.QMessageBox
    _qt.QMessageBox = type("_Box", (), {"__init__": lambda self, *a: None})
    try:
        ok = sp.ensure_at_startup()
    finally:
        sys.platform = real_platform
        sp.has_screen_access = real_has
        sp.request_screen_access = real_request
        sp.LoggerService.log = real_log
        _qt.QMessageBox = real_box

    check("the system prompt is shown AT LAUNCH", asked == [1], str(asked))
    check("startup reports that captures will not work", ok is False, str(ok))
    check("and it is written down, so an empty day can be explained",
          any("SCREEN RECORDING" in m for m in told), str(told))

    print("\nAnd a capture without it takes nothing")
    # The important half. A blank capture is worse than no capture: it fills
    # the day with wallpaper and hides the fact that nothing is being seen.
    from client.application.managers.screenshot_manager import ScreenshotManager
    from client.application.managers.session_manager import SessionManager
    from client.services import screen_permission as sp2
    # capture_screenshot reads the daily limit from the local database, which
    # a machine that has never run the app does not have.
    from client.infrastructure.database.database import Database
    Database.initialize()

    SessionManager.role = "employee"
    SessionManager.employee_id = "TEST_PERM"
    real_has2 = sp2.has_screen_access
    sp2.has_screen_access = lambda: False
    said = []
    import client.application.managers.screenshot_manager as sm
    real_log2 = sm.LoggerService.log
    sm.LoggerService.log = lambda m, *a, **k: said.append(str(m))
    try:
        result = ScreenshotManager.capture_screenshot()
    finally:
        sp2.has_screen_access = real_has2
        sm.LoggerService.log = real_log2

    check("nothing is captured", result is None, str(result))
    check("and the reason names the permission, not 'capture failed'",
          any("Screen Recording permission" in m for m in said), str(said))

    print("\nWho is captured at all")
    # The owner's rule: an administrator is tracked like anybody else; only
    # the super admin — the owner — is not.
    from datetime import datetime
    from client.core.time_ist import now_ist
    day = now_ist().date()
    start = datetime.combine(day, datetime.strptime("09:00", "%H:%M").time())
    end = datetime.combine(day, datetime.strptime("23:00", "%H:%M").time())
    for role in ("employee", "admin"):
        SessionManager.role = role
        check(f"an {role} has captures scheduled",
              len(ScreenshotManager.generate_daily_schedule(start, end)) > 0)

    import inspect
    from client.application.schedulers import scheduler_service
    source = inspect.getsource(scheduler_service.SchedulerService)
    check("and the scheduler exempts the super admin ONLY",
          "super_admin" in source and "'admin'" not in source
          and '"admin"' not in source,
          "an exemption for plain admins would silently stop tracking them")

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.stdout.flush()
        sys.exit(1)
    print("all screen permission checks passed")
    sys.stdout.flush()
    sys.exit(0)


main()
