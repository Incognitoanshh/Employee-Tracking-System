"""
A screen that cannot be read is a capture POSTPONED, not a capture lost.

THE REPORT, from production:

    ScreenshotManager: capture failed — screen grab failed

logged against one employee, with USER IDLE (61.4s) the line before it. The
sentence is Pillow's, from its Windows grabber, and it means one thing: the
GDI copy of the screen was refused because there was no visible desktop to
copy — the machine was locked, a security prompt had the input desktop, or a
Remote Desktop window was minimised or disconnected. That machine is a
Windows Server worked on over Remote Desktop, where this is ordinary.

TWO THINGS WERE WRONG WITH IT.

    It read as a broken build. "capture failed" against a day with no
    screenshots in it sends somebody looking at the app, which is the one
    place the fault is not.

    The capture was thrown away. The schedule is single-shot timers, so the
    slot was gone: an hour of lunch, with the screen locked, silently cost
    the day those captures and the count never reached what the admin set.

WHAT HAPPENS NOW. The state of the desktop is asked for before the picture is
taken, and a refusal — either from that check or from Windows itself — is
logged as POSTPONED with the reason in plain words, leaves the day's count
untouched, and asks the scheduler to spread what is left over the rest of the
shift. The budget is counted from rows in the database, so a capture that
never happened was never spent.

WHY THE WINDOWS CALLS ARE BEHIND THREE SMALL FUNCTIONS. They cannot run on a
Mac or on CI, and a rule nobody can exercise is a rule nobody can trust. The
two that touch Windows are replaced here, and every branch of the decision —
locked, wrong desktop, disconnected session, and the check itself breaking —
is run.

Run:  python3 tests/test_capture_postponed.py
"""

import base64
import json
import os
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Isolated from anything real before a client module is imported — the reason
# is written at the top of test_screenshot_pipeline.py.
os.environ.setdefault("ETS_DATA_DIR", tempfile.mkdtemp(prefix="ets_post_"))
os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:9/api")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMP = tempfile.mkdtemp(prefix="ets_postponed_")
import client.core.config as config                                          # noqa: E402
config.STORAGE_DIR = _TMP
from client.infrastructure.database import database as database_module       # noqa: E402
database_module.Database.DB_PATH = os.path.join(_TMP, "ets.db")

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok or not detail else f"  — {detail}"))
    sys.stdout.flush()


from PIL import Image                                                        # noqa: E402
from PySide6.QtWidgets import QApplication                                   # noqa: E402

app = QApplication.instance() or QApplication([])

import client.application.managers.screenshot_manager as sm                   # noqa: E402
from client.application.managers.screenshot_manager import ScreenshotManager  # noqa: E402
from client.application.managers.session_manager import SessionManager        # noqa: E402
from client.infrastructure.database.database import Database                  # noqa: E402
from client.services.logger_service import LoggerService                      # noqa: E402

def a_token(minutes=60):
    """A token shaped like the real one, good for an hour.

    NOT "not-a-real-token". The panel reads the `exp` claim itself before it
    refreshes anything, treats a token it cannot decode as expired, and logs
    out — which is a sign-in window and a stopped panel in the middle of the
    test. Only the claim is read on this side; the signature is the server's
    business.
    """
    part = lambda value: base64.urlsafe_b64encode(
        json.dumps(value).encode()).rstrip(b"=").decode()
    return (part({"alg": "HS256", "typ": "JWT"})
            + "." + part({"employee_id": "E001",
                          "exp": int(time.time()) + minutes * 60})
            + ".not-a-real-signature")


Database.initialize()
SessionManager.employee_id = "E001"
SessionManager.role = "employee"
SessionManager.auth_token = a_token()

# Collected rather than discarded: capture_screenshot catches its own
# exceptions and only logs, so the log IS the behaviour under test.
lines = []
LoggerService.log = lambda m, *a, **k: lines.append(str(m))
LoggerService.log_verbose = lambda m, *a, **k: lines.append(str(m))


def rows_now():
    with Database.get_connection() as conn:
        return conn.execute("SELECT COUNT(*) c FROM screenshots").fetchone()["c"]


print("\nWhat Windows is asked, before the picture is taken")

# The two calls that only exist on Windows, replaced by what they would have
# answered. Everything between them is the code that ships.
real_state, real_name = sm._win_session_state, sm._win_input_desktop_name
try:
    sm._win_session_state = lambda: sm.WINDOWS_SESSION_ACTIVE
    sm._win_input_desktop_name = lambda: "Default"
    ready, why = sm._windows_desktop_ready()
    check("an unlocked machine is ready", ready and why == "", f"{ready} {why}")

    # OpenInputDesktop refusing IS the lock: there is no other call that
    # answers "is it locked" from an ordinary session.
    sm._win_input_desktop_name = lambda: None
    ready, why = sm._windows_desktop_ready()
    check("a locked screen is not ready", not ready, str(ready))
    check("and the reason says locked, not 'failed'",
          "locked" in why.lower(), why)

    sm._win_input_desktop_name = lambda: "Winlogon"
    ready, why = sm._windows_desktop_ready()
    check("the lock screen's own desktop is not ready either", not ready, why)
    check("and it is named", "winlogon" in why.lower(), why)

    sm._win_input_desktop_name = lambda: "Default"
    sm._win_session_state = lambda: 4                    # WTSDisconnected
    ready, why = sm._windows_desktop_ready()
    check("a disconnected Remote Desktop session is not ready", not ready, why)
    check("and says so in words an admin can act on",
          "remote desktop" in why.lower(), why)

    # A CHECK THAT BREAKS MUST NOT STOP MONITORING. A screenshot that might
    # have worked is worth more than a tidy reason for never trying.
    def explode():
        raise OSError("wtsapi32 is not where it should be")
    sm._win_session_state = explode
    ready, why = sm._windows_desktop_ready()
    check("if the check itself breaks, the capture still goes ahead",
          ready and why == "", f"{ready} {why}")
finally:
    sm._win_session_state, sm._win_input_desktop_name = real_state, real_name

check("on this machine, which is not Windows, nothing is in the way",
      sm._desktop_ready() == (True, ""), str(sm._desktop_ready()))


print("\nWhat the log line says, and whether to try again")

hint, again = sm._capture_failure_hint(
    OSError("screen grab failed"), "win32", False)
check("Windows refusing the copy is worth trying again", again)
check("and the line explains lock, prompt or Remote Desktop",
      "locked" in hint.lower() and "remote desktop" in hint.lower(), hint[:120])
check("with the fix for a minimised Remote Desktop window",
      "SuppressWhenMinimized" in hint, hint[:160])

hint, again = sm._capture_failure_hint(
    Exception("Command '['screencapture', '-x', '/var/x.png']' returned 1"),
    "darwin", True)
check("macOS refusing it is a permission, and says which",
      "Screen Recording" in hint and "code-signed" in hint, hint[:120])
check("and is NOT retried — a permission does not pass on its own", not again)

hint, again = sm._capture_failure_hint(OSError("No space left"), "linux", False)
check("anything else gets no invented explanation",
      hint == "" and not again, f"{hint!r} {again}")


print("\nA capture that cannot happen")

real_ready = sm._desktop_ready
before = rows_now()
lines.clear()
try:
    sm._desktop_ready = lambda: (False, "the screen is locked, or Windows is "
                                        "showing a security prompt")
    result = ScreenshotManager.capture_screenshot()
finally:
    sm._desktop_ready = real_ready

check("nothing is returned", result is None, str(result))
check("nothing is recorded", rows_now() == before, f"{before} -> {rows_now()}")
check("the log says POSTPONED, not failed",
      any("SCREENSHOT POSTPONED" in m for m in lines)
      and not any("capture failed" in m for m in lines), str(lines))
check("with the reason in it",
      any("locked" in m for m in lines), str(lines))
check("the day's count is untouched",
      any("/10" in m or f"/{ScreenshotManager.screenshots_per_day()}" in m
          for m in lines), str(lines))
check("and the panel is told to ask for it again",
      ScreenshotManager.should_try_again(), ScreenshotManager.last_outcome)


print("\nWindows refusing it mid-capture")

real_grab, real_sys = sm._grab_every_screen, sm.sys
before = rows_now()
lines.clear()


def refuse():
    raise OSError("screen grab failed")


try:
    sm._grab_every_screen = refuse
    sm.sys = types.SimpleNamespace(platform="win32")
    result = ScreenshotManager.capture_screenshot()
finally:
    sm._grab_every_screen, sm.sys = real_grab, real_sys

check("it is postponed as well", ScreenshotManager.should_try_again(),
      ScreenshotManager.last_outcome)
check("nothing is recorded for it", rows_now() == before,
      f"{before} -> {rows_now()}")
check("and the line carries the explanation",
      any("SCREENSHOT POSTPONED" in m and "Remote Desktop" in m for m in lines),
      str(lines)[:300])


print("\nA real failure is still a failure")

before = rows_now()
lines.clear()
try:
    sm._grab_every_screen = lambda: (_ for _ in ()).throw(
        OSError("No space left on device"))
    ScreenshotManager.capture_screenshot()
finally:
    sm._grab_every_screen = real_grab

check("it is reported as a failure",
      any("capture failed" in m for m in lines), str(lines)[:200])
check("and is NOT retried — retrying a full disk only fills the log",
      not ScreenshotManager.should_try_again(), ScreenshotManager.last_outcome)
check("nothing is recorded for it either", rows_now() == before,
      f"{before} -> {rows_now()}")


print("\nA capture that works")

before = rows_now()
lines.clear()
real_quartz, real_auto = sm._grab_via_quartz, sm.pyautogui.screenshot
try:
    # The in-process macOS path would hand back this Mac's real desktop; the
    # fake screen is what should travel through. test_quartz_capture covers
    # the CoreGraphics path itself.
    sm._grab_via_quartz = lambda: None
    sm.pyautogui.screenshot = lambda *a, **k: Image.new("RGB", (400, 300),
                                                        (20, 40, 60))
    result = ScreenshotManager.capture_screenshot()
finally:
    sm._grab_via_quartz, sm.pyautogui.screenshot = real_quartz, real_auto

check("it is recorded", rows_now() == before + 1, f"{before} -> {rows_now()}")
check("and nothing is asked for again — that would double the day's count",
      not ScreenshotManager.should_try_again(), ScreenshotManager.last_outcome)


print("\nThe scheduler is asked to plan it again")

from client.application.schedulers.scheduler_service import SchedulerService  # noqa: E402

scheduler = SchedulerService()
planned = []
scheduler.reschedule = lambda: planned.append(1)
scheduler.capture_postponed()
check("one replan is armed",
      getattr(scheduler, "_replan_timer", None) is not None
      and scheduler._replan_timer.isActive())
first = scheduler._replan_timer
scheduler.capture_postponed()
scheduler.capture_postponed()
check("and a burst of them still arms only one",
      scheduler._replan_timer is first)

deadline = SchedulerService.REPLAN_DEBOUNCE_MS / 1000 + 1.5
end = time.time() + deadline
while time.time() < end and not planned:
    app.processEvents()
    time.sleep(0.02)
check("the plan is actually made", planned == [1], str(planned))
scheduler.stop()


print("\nThe panels wire it up")


class _FakeScheduler:
    def __init__(self):
        self.asked = 0

    def capture_postponed(self):
        self.asked += 1


from client.presentation.windows import employee_panel as ep                  # noqa: E402

ep.EmployeePanel._start_services = lambda self: None      # no timers, no captures
SessionManager.full_name = "Rajesh Kumar"
panel = ep.EmployeePanel()
panel.scheduler = _FakeScheduler()

real_capture = ScreenshotManager.capture_screenshot


def postponed_capture():
    ScreenshotManager.last_outcome = ScreenshotManager.POSTPONED
    return None


def good_capture():
    ScreenshotManager.last_outcome = ScreenshotManager.CAPTURED
    return {"id": "x", "path": "x.enc", "timestamp": "2026-09-18 10:00:00"}


try:
    ScreenshotManager.capture_screenshot = staticmethod(postponed_capture)
    panel._capture()
    check("the employee panel asks for a postponed capture again",
          panel.scheduler.asked == 1, str(panel.scheduler.asked))

    ScreenshotManager.capture_screenshot = staticmethod(good_capture)
    panel._capture()
    check("and asks for nothing after one that worked",
          panel.scheduler.asked == 1, str(panel.scheduler.asked))
finally:
    ScreenshotManager.capture_screenshot = real_capture

panel._teardown_pages()
panel.deleteLater()

SessionManager.role = "admin"
from client.presentation.windows.admin_config_panel import AdminConfigPanel   # noqa: E402

console = AdminConfigPanel()
console._stop_background_services()
console.scheduler = _FakeScheduler()
try:
    ScreenshotManager.capture_screenshot = staticmethod(postponed_capture)
    console.capture_screenshot()
    check("the admin console does too — admins are tracked as well",
          console.scheduler.asked == 1, str(console.scheduler.asked))
finally:
    ScreenshotManager.capture_screenshot = real_capture
console.deleteLater()

print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILED'}\n")

# os._exit, for the reason test_theme and test_payroll_tab both record.
sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if failures else 0)
