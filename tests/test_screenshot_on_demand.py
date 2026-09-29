"""
A screenshot an administrator asks for, on the client's side of it.

THE REQUEST. "Agar employee online and working hai to button click and uska
current screenshot aa jaye." Nothing can call this app: it polls the server
every five seconds. So the answer rides back on that poll, the capture names
the request it answers, and the button waits for the picture rather than
announcing one.

WHAT IS CHECKED HERE, and each of these is a way the feature could look like
it works while being useless:

  * the poll notices the request and hands it on — with the id, because a
    capture that cannot say which request it answers leaves the button
    waiting for ever;
  * the capture happens even when the day's budget is spent. The budget
    exists to stop unattended monitoring running away; a person pressing a
    button is not that, and an administrator who exhausted somebody's
    monitoring by asking for pictures would have broken the thing they were
    using;
  * the request id travels with the upload, since that is what closes the
    request;
  * the button is off when the person is offline, and SAYS why — a request
    queued for whenever the app next opened would answer "now" with a
    picture from hours later.

Run:  python3 tests/test_screenshot_on_demand.py
"""

import base64
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ETS_DATA_DIR", tempfile.mkdtemp(prefix="ets_ondemand_"))
os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:9/api")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMP = tempfile.mkdtemp(prefix="ets_ondemand_db_")
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

from client.infrastructure.database.database import Database                 # noqa: E402
from client.application.managers.session_manager import SessionManager       # noqa: E402
from client.services.logger_service import LoggerService                     # noqa: E402

Database.initialize()
SessionManager.employee_id = "E001"
SessionManager.role = "employee"
SessionManager.auth_token = "not-checked-here"

lines = []
LoggerService.log = lambda m, *a, **k: lines.append(str(m))
LoggerService.log_verbose = lambda m, *a, **k: lines.append(str(m))


print("\nThe poll that collects the request")

import client.application.managers.config_sync_manager as sync_mod           # noqa: E402


class _Reply:
    """What the server says when somebody has pressed the button."""

    status_code = 200

    def __init__(self, config):
        self._config = config

    def json(self):
        return {"success": True, "config": self._config}


real_post = sync_mod._http.post
asked = []
try:
    sync_mod._http.post = lambda *a, **k: _Reply({"capture_now": 77,
                                                  "screenshots_per_day": 10})
    manager = sync_mod.ConfigSyncManager(
        employee_id="E001", device_id="d1", auth_token="t",
        on_capture_now=lambda request_id: asked.append(request_id))
    manager.sync_now()
finally:
    sync_mod._http.post = real_post

check("a request on the poll is handed on", asked == [77], str(asked))
check("and it carries the id, not just 'somebody asked'",
      asked and isinstance(asked[0], int), str(asked))

nothing = []
try:
    sync_mod._http.post = lambda *a, **k: _Reply({"screenshots_per_day": 10})
    manager = sync_mod.ConfigSyncManager(
        employee_id="E001", device_id="d1", auth_token="t",
        on_capture_now=lambda request_id: nothing.append(request_id))
    manager.sync_now()
finally:
    sync_mod._http.post = real_post
check("an ordinary poll asks for no picture at all", nothing == [], str(nothing))


print("\nThe scheduler hands it to the panel")

from client.application.schedulers.scheduler_service import SchedulerService  # noqa: E402

scheduler = SchedulerService()
seen = []
scheduler.capture_requested.connect(lambda request_id: seen.append(request_id))
scheduler._handle_capture_now(91)
# It crosses from the sync thread to this one, so it arrives on the loop.
end = time.time() + 2
while time.time() < end and not seen:
    app.processEvents()
    time.sleep(0.02)
check("the request reaches the panel, on the main thread", seen == [91], str(seen))
scheduler.stop()


print("\nThe capture that answers it")

import client.application.managers.screenshot_manager as sm                   # noqa: E402
from client.application.managers.screenshot_manager import ScreenshotManager  # noqa: E402


def rows_now():
    with Database.get_connection() as conn:
        return conn.execute("SELECT COUNT(*) c FROM screenshots").fetchone()["c"]


class _Upload:
    status_code = 200

    def json(self):
        return {"success": True}


posted = []
real_quartz, real_auto, real_http = sm._grab_via_quartz, sm.pyautogui.screenshot, sm._http.post
try:
    sm._grab_via_quartz = lambda: None
    sm.pyautogui.screenshot = lambda *a, **k: Image.new("RGB", (320, 240), (10, 20, 30))
    sm._http.post = lambda url, **kwargs: (posted.append(kwargs), _Upload())[1]

    # THE DAY'S BUDGET IS SPENT. A scheduled capture would be refused here.
    ScreenshotManager.screenshots_per_day = classmethod(lambda cls: 1)
    ScreenshotManager.captures_today = classmethod(lambda cls: 5)

    lines.clear()
    before = rows_now()
    refused = ScreenshotManager.capture_screenshot()
    check("a scheduled capture stops when the budget is spent",
          refused is None and any("SKIPPED" in m for m in lines), str(lines)[:120])

    lines.clear()
    posted.clear()
    taken = ScreenshotManager.capture_screenshot(request_id=77)
    check("but one that was asked for is taken anyway",
          taken is not None, str(lines)[:160])
    # AND IT LOOKS EXACTLY LIKE ANY OTHER CAPTURE. These lines are uploaded
    # and shown in the employee's own Recent Activity, so "SCREENSHOT ON
    # REQUEST" told them, on their own screen, the moment somebody chose to
    # look at it — "employee ko pata nahi chalna chahiye ki request kiya
    # hai". What answered which request lives on the server instead.
    check("and nothing in what the employee can see says it was asked for",
          not any("REQUEST" in m.upper() for m in lines), str(lines)[:200])
    scheduled_wording = [m for m in lines if "SCREENSHOT CAPTURED" in m]
    check("it is worded exactly like a scheduled capture",
          len(scheduled_wording) == 1, str(lines)[:200])
    check("it is stored like any other", rows_now() == before + 1,
          f"{before} -> {rows_now()}")
    # WITHOUT THIS THE BUTTON WAITS FOR EVER. The server closes the request
    # on the upload that names it.
    sent = posted[-1] if posted else {}
    check("and the upload says which request it answers",
          (sent.get("data") or {}).get("request_id") == "77",
          str(sent.get("data")))
    # AND A SCHEDULED ONE CARRIES NOTHING. Sending an id with every capture
    # would close whatever request happened to be open with a picture nobody
    # asked for at that moment.
    ScreenshotManager.captures_today = classmethod(lambda cls: 0)
    ScreenshotManager.screenshots_per_day = classmethod(lambda cls: 10)
    posted.clear()
    ScreenshotManager.capture_screenshot()
    check("while a scheduled one says nothing about requests",
          posted and not (posted[-1].get("data") or {}),
          str(posted[-1].get("data") if posted else "nothing uploaded"))
finally:
    sm._grab_via_quartz, sm.pyautogui.screenshot = real_quartz, real_auto
    sm._http.post = real_http
    del ScreenshotManager.screenshots_per_day
    del ScreenshotManager.captures_today


print("\nThe button an administrator presses")

# IT IS NOT ON THE PERSON'S PAGE, and that is deliberate. It was put beside
# their NAME, which is an identity and not a place for an action — said
# plainly: "screenshot wala naam ke andar kyu diya". It lives on the Tracker
# board now, where somebody is already deciding whose screen to look at, and
# tests/test_tracker_tab.py is where the button itself is checked.
from client.presentation.windows import admin_config_panel as panel          # noqa: E402

real_fetch, real_post_worker, real_track = (
    panel._FetchWorker, panel._PostWorker, panel._track_worker)


class _Dead:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, _name):
        return type("_Sig", (), {"connect": lambda *_a, **_k: None})()

    def start(self):
        pass


try:
    panel._FetchWorker = _Dead
    panel._PostWorker = _Dead
    panel._track_worker = lambda *a, **k: None

    page = panel.EmployeePage()
    page.load({"employee_id": "E001", "username": "rajesh",
               "full_name": "Rajesh Kumar", "role": "employee"})
    check("the person's own page carries no capture button beside their name",
          not hasattr(page, "_shot_now"))
    check("and none of its machinery is left behind either",
          not hasattr(page, "_request_screenshot"))
    check("while the board that does have it is a page of its own",
          any(entry.get("key") == "tracker" for entry in panel.PAGES),
          str([entry.get("key") for entry in panel.PAGES]))

    # ── THE NUMBER ON THE CARD IS TODAY'S ────────────────────────────────
    #
    # These cards showed ninety days of attendance with nothing to say so:
    # "268:09:17" here, "03:01:00" on the tracker board, same person, same
    # moment, neither screen admitting they answered different questions.
    # Somebody opening a person's page is asking about today — and it is
    # today that ticks while they watch.
    page._set_stats({"data": {
        "employee_id": "E001", "status": "online",
        "active_time": "268:09:17", "idle_time": "41:52:00",
        "active_window_days": 90,
        "active_today": "03:01:00", "idle_today": "00:19:00",
        "state_today": "IDLE",
        "screenshot_count": 12, "activity_log_count": 300,
        "recent_activity": [],
    }})
    check("the card leads with today, not with ninety days of it",
          page._active_time._value_label.text() == "03:01:00",
          page._active_time._value_label.text())
    check("and the long total is kept underneath, with its period said",
          "268:09:17" in page._active_time._sub_label.text()
          and "90 days" in page._active_time._sub_label.text(),
          page._active_time._sub_label.text())
    check("idle reads the same way", page._idle_time._value_label.text() == "00:19:00",
          page._idle_time._value_label.text())

    # AND THE CLOCK THAT TICKS IS TODAY'S. Ticking a ninety-day total by the
    # second is arithmetic nobody asked for.
    page._on_details({"success": True, "data": {
        "employee_id": "E001", "status": "online",
        "active_time": "268:09:17", "idle_time": "41:52:00",
        "active_window_days": 90,
        "active_today": "03:01:00", "idle_today": "00:19:00",
        "state_today": "IDLE", "recent_activity": [],
    }})
    check("the live clock starts from today's figure",
          page._live_active_seconds == 3 * 3600 + 60, str(page._live_active_seconds))
    # AND FROM THE STATE THE SERVER WORKED OUT, rather than guessed at by
    # reading the activity feed back — which disagrees whenever a line falls
    # outside a shift.
    check("and it advances the state the server reported",
          page._live_state == "IDLE", str(page._live_state))
    before = page._live_idle_seconds
    page._tick_live_times()
    check("so a second of somebody idle is counted as idle",
          page._live_idle_seconds == before + 1,
          f"{before} -> {page._live_idle_seconds}")

    # A SERVER THAT SAID NOTHING GETS A DASH, not a zero. "00:00:00" is a
    # statement that somebody has done nothing today.
    page._set_stats({"data": {"employee_id": "E001", "status": "offline"}})
    check("and a missing figure shows as a dash rather than a made-up zero",
          page._active_time._value_label.text() == "—", page._active_time._value_label.text())
    page.deleteLater()
    app.processEvents()
finally:
    panel._FetchWorker, panel._PostWorker, panel._track_worker = (
        real_fetch, real_post_worker, real_track)

print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILED'}\n")

sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if failures else 0)
