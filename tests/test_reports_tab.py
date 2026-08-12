"""
The Reports tab's employee dropdown.

One thing is checked here, because one thing went wrong: the list is REBUILT
each time it loads, not appended to. It used to append, and since the page
reloads the list every time it is opened, the dropdown listed every employee
once per visit — five names became twenty-five, in a list taller than the
window, with the same person appearing over and over.

That is worth a test rather than a careful reading, because the bug is
invisible on the first load. It only appears on the second, which is exactly
the case nobody tries by hand.

Run:  python3 tests/test_reports_tab.py
"""

import os
import sys
import tempfile

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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))
    sys.stdout.flush()


def main():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from client.presentation.windows import admin_config_panel as panel

    # The page fetches over the network on construction. Nothing here needs a
    # server: the worker is replaced so the reply can be delivered by hand,
    # as many times as a person might reopen the page.
    delivered = []

    class _NoNetwork:
        def __init__(self, *_a, **_k):
            self.result = _Signal()
            self.error = _Signal()

        def start(self):
            delivered.append(self)

    class _Signal:
        def __init__(self):
            self._slots = []

        def connect(self, slot):
            self._slots.append(slot)

        def emit(self, value):
            for slot in list(self._slots):
                slot(value)

    original_worker = panel._FetchWorker
    original_track = panel._track_worker
    panel._FetchWorker = _NoNetwork
    panel._track_worker = lambda *_a, **_k: None
    try:
        tab = panel._ReportsTab()

        people = {"employees": [
            {"employee_id": "AD100", "username": "manager", "role": "admin"},
            {"employee_id": "EM101", "username": "rajesh", "role": "employee"},
            {"employee_id": "EM102", "username": "amit", "role": "employee"},
            {"employee_id": "SA100", "username": "owner", "role": "super_admin"},
        ]}

        print("The employee dropdown")
        delivered[-1].result.emit(people)
        first = [tab._emp.itemText(i) for i in range(tab._emp.count())]
        check("it lists everybody once, plus All employees",
              len(first) == 4, str(first))
        check("the super admin is not offered — they are not tracked",
              not any("owner" in t for t in first), str(first))

        # Reopening the page. This is the whole point of the test.
        tab._load_employees()
        delivered[-1].result.emit(people)
        second = [tab._emp.itemText(i) for i in range(tab._emp.count())]
        check("loading it again does not double the list",
              second == first, f"{len(first)} became {len(second)}")

        for _ in range(4):
            tab._load_employees()
            delivered[-1].result.emit(people)
        after = [tab._emp.itemText(i) for i in range(tab._emp.count())]
        check("nor does opening it six times",
              after == first, f"{len(after)} entries: {after}")
        check("and nobody appears twice",
              len(set(after)) == len(after), str(after))

        # A reload must not quietly change what the report is about.
        index = tab._emp.findData("EM102")
        tab._emp.setCurrentIndex(index)
        tab._load_employees()
        delivered[-1].result.emit(people)
        check("the employee you picked is still picked after a reload",
              tab._emp.currentData() == "EM102", str(tab._emp.currentData()))
    finally:
        panel._FetchWorker = original_worker
        panel._track_worker = original_track

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.stdout.flush()
        sys.exit(1)
    print("all reports tab checks passed")
    sys.stdout.flush()
    sys.exit(0)


main()
