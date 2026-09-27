"""
The tracker board, on screen.

WHAT IT IS FOR. "Ek tracker window bnao... sare employee listed hongay aur
tracking chalega — working green, non working idle, aur count hoga kitne der
idle tha, red signal continuously working for 1 hr." One board, instead of
eight clicks through eight pages while the first person changes state.

WHAT IS CHECKED, and each is a way a live board misleads the person reading
it:

  * OFFLINE IS NOT IDLE, and does not wear idle's colour. Somebody whose app
    is closed is not sitting at a desk doing nothing.
  * EVERY STATE CARRIES ITS CLOCK. "Idle" is worth little; "idle 23 min" is
    what somebody acts on.
  * AN HOUR WITHOUT A BREAK IS RED. That was the ask, and it is the one
    thing on the board that is about the person rather than about the work.
  * THE BUTTON IS OFF FOR SOMEBODY OFFLINE, and says why.
  * AND IT ALL FITS. The page this replaced had a horizontal scrollbar
    hiding the column somebody came to press, which is what started this:
    "har jagah UI me chod kiye hue ho".

Run:  python3 tests/test_tracker_tab.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ETS_DATA_DIR", tempfile.mkdtemp(prefix="ets_trk_"))
os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:9/api")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMP = tempfile.mkdtemp(prefix="ets_trk_db_")
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


from PySide6.QtWidgets import QApplication, QLabel, QPushButton               # noqa: E402

app = QApplication.instance() or QApplication([])

from client.presentation.windows import admin_config_panel as panel          # noqa: E402

app.setStyleSheet(panel._global_stylesheet())

asked = []


class _Dead:
    """Answers nothing — the board is what is under test, not the fetch."""

    def __init__(self, url, *a, **k):
        asked.append(url)

    def __getattr__(self, _name):
        return type("_Sig", (), {"connect": lambda *_a, **_k: None})()

    def start(self):
        pass


real_fetch, real_post, real_track = (
    panel._FetchWorker, panel._PostWorker, panel._track_worker)
panel._FetchWorker = _Dead
panel._PostWorker = _Dead
panel._track_worker = lambda *a, **k: None

BOARD = {"data": [
    {"employee_id": "E001", "name": "Asha Verma", "designation": "Senior Developer",
     "state": "ACTIVE", "working_seconds": 5 * 3600 + 4 * 60, "idle_seconds": 0,
     "active_today": "05:10:00", "idle_today": "00:12:00",
     "last_screenshot_at": None},
    {"employee_id": "E002", "name": "Bilal Khan", "designation": "Designer",
     "state": "IDLE", "working_seconds": 0, "idle_seconds": 23 * 60,
     "active_today": "01:07:00", "idle_today": "00:23:00",
     "last_screenshot_at": None},
    {"employee_id": "E003", "name": "Chitra Rao", "designation": "QA",
     "state": "OFFLINE", "working_seconds": 0, "idle_seconds": 0,
     "active_today": "00:00:00", "idle_today": "00:00:00",
     "last_screenshot_at": None},
    {"employee_id": "E004", "name": "Deepak Roy", "designation": "Support",
     "state": "ACTIVE", "working_seconds": 12 * 60, "idle_seconds": 0,
     "active_today": "00:12:00", "idle_today": "00:00:00",
     "last_screenshot_at": None},
]}

try:
    tab = panel._TrackerTab()
    tab.resize(1120, 700)
    tab._fill(BOARD)

    print("\nWhat the board says")

    check("everybody is on it", tab._table.rowCount() == 4,
          str(tab._table.rowCount()))

    def chip(row):
        holder = tab._table.cellWidget(row, 1)
        label = holder.findChild(QLabel) if holder else None
        return label.text() if label else "(none)"

    check("somebody at work reads as working", chip(0) == "Working", chip(0))
    check("somebody idle reads as idle", chip(1) == "Idle", chip(1))
    # OFFLINE IS ITS OWN STATE. Painting a closed app the same amber as an
    # idle person says they are at a desk doing nothing.
    check("and a closed app reads as offline, not idle", chip(2) == "Offline",
          chip(2))

    for_cell = lambda row: tab._table.item(row, 2).text()                # noqa: E731
    check("an unbroken stretch is spelled out",
          "5 hr 04 min" in for_cell(0), for_cell(0))
    check("and marked, because it is over an hour",
          for_cell(0).startswith("●"), for_cell(0))
    check("idle carries how long it has been",
          for_cell(1) == "23 min", for_cell(1))
    check("a short stretch is not marked",
          for_cell(3) == "12 min", for_cell(3))
    check("somebody offline has no clock at all", for_cell(2) == "—", for_cell(2))

    colours = {
        "long stretch": tab._table.item(0, 2).foreground().color().name(),
        "idle": tab._table.item(1, 2).foreground().color().name(),
    }
    check("the hour without a break is red, not just worded",
          colours["long stretch"].lower() == panel.C["danger"].lower(),
          f"{colours['long stretch']} vs {panel.C['danger']}")
    check("and idle is amber", colours["idle"].lower() == panel.C["warning"].lower(),
          f"{colours['idle']} vs {panel.C['warning']}")

    check("the day's totals are there beside the state",
          tab._table.item(0, 3).text() == "05:10:00", tab._table.item(0, 3).text())

    print("\nThe counts above it")
    counts = {key: label.text() for key, label in tab._counts.items()}
    check("working, idle and offline are counted",
          counts["ACTIVE"] == "2" and counts["IDLE"] == "1" and counts["OFFLINE"] == "1",
          str(counts))
    check("and so is anybody over an hour without a break",
          counts["LONG"] == "1", str(counts))

    print("\nThe screenshot button")

    def button(row):
        holder = tab._table.cellWidget(row, 6)
        return holder.findChild(QPushButton) if holder else None

    check("it can be pressed for somebody online", button(0).isEnabled())
    check("and not for somebody offline", not button(2).isEnabled())
    check("which it explains rather than just greying out",
          "not online" in button(2).toolTip(), button(2).toolTip())
    check("while the one that works says what it does, and that it is recorded",
          "right now" in button(0).toolTip() and "audit" in button(0).toolTip(),
          button(0).toolTip())

    asked.clear()
    button(0).click()
    check("pressing it asks for that person",
          any(url.endswith("/admin/employees/E001/screenshot") for url in asked),
          str(asked))
    check("and the row says it is waiting rather than claiming a picture",
          "Waiting" in (button(0).text() or ""), button(0).text())

    print("\nIt fits, and it stops when nobody is looking")

    # SHOWN, then resized. A hidden widget never lays its children out, so
    # the viewport keeps a default size and the measurement is of nothing.
    tab.show()
    for width in (1120, 1000, 880):
        tab.resize(width, 700)
        for _ in range(3):
            app.processEvents()
        need = sum(tab._table.columnWidth(c) for c in range(tab._table.columnCount()))
        have = tab._table.viewport().width()
        check(f"at {width}px wide nothing spills off the edge", need <= have,
              f"{need} > {have}")

    check("it refreshes while it is open", tab._timer.isActive())
    tab.hide()
    app.processEvents()
    # A CONSOLE KEEPS EVERY TAB ALIVE. A board that polled regardless would
    # ask the server for the whole company every ten seconds, all day, for
    # nobody.
    check("and stops the moment it is not", not tab._timer.isActive())

    check("the console shuts it down with the rest",
          "_tracker_tab" in panel.AdminConfigPanel.TAB_ATTRS,
          str(panel.AdminConfigPanel.TAB_ATTRS))
    check("and it has a page of its own in the sidebar",
          any(page.get("key") == "tracker" for page in panel.PAGES),
          str([p.get("key") for p in panel.PAGES]))

    print("\nAnd an empty company is not an empty screen")
    tab._fill({"data": []})
    check("it says why there is nothing", not tab._empty.isHidden()
          and "appear here" in tab._empty.text(), tab._empty.text())

    tab.deleteLater()
    app.processEvents()
finally:
    panel._FetchWorker, panel._PostWorker, panel._track_worker = (
        real_fetch, real_post, real_track)

print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILED'}\n")

sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if failures else 0)
