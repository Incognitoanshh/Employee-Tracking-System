"""
The Alerts page in the admin panel.

Two things are worth checking here, and neither is about layout.

FIRST, an empty table must never be mistaken for "all clear". If the request
itself fails, the page has to say so. Showing nothing would be the single most
damaging thing this page could do: it would report peace while the server is
unreachable, which is exactly when something is wrong.

SECOND, the tab list and the sidebar list must stay in step. The panel
switches pages by index, so a tab inserted in one list and not the other
silently shows the wrong page for every entry after it.

Run:  python3 tests/test_alerts_tab.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
    QApplication.instance() or QApplication([])

    from client.presentation.windows import admin_config_panel as panel

    started = []

    class _NoNetwork:
        def __init__(self, *_a, **_k):
            self.result = _Signal()
            self.error = _Signal()

        def start(self):
            started.append(self)

    class _Signal:
        def __init__(self):
            self._slots = []

        def connect(self, slot):
            self._slots.append(slot)

        def emit(self, value):
            for slot in list(self._slots):
                slot(value)

    original = panel._FetchWorker
    original_track = panel._track_worker
    panel._FetchWorker = _NoNetwork
    panel._track_worker = lambda *_a, **_k: None
    try:
        print("The page")
        tab = panel._AlertsTab()
        check("it builds without a server", tab is not None)
        check("and asks the server once on opening", len(started) == 1, str(len(started)))

        print("\nWhen there is nothing wrong")
        started[-1].result.emit({
            "enabled": True, "total": 0, "counts": {"HIGH": 0, "MEDIUM": 0, "LOW": 0},
            "alerts": []})
        check("it says so plainly", "Nothing needs attention" in tab._headline.text(),
              tab._headline.text())
        check("with an empty table", tab._table.rowCount() == 0)

        print("\nWhen there is")
        started[-1].result.emit({
            "enabled": True, "total": 2,
            "counts": {"HIGH": 1, "MEDIUM": 1, "LOW": 0},
            "alerts": [
                {"type": "NOT_REPORTING", "severity": "HIGH", "employee_id": "EM103",
                 "employee_name": "Sneha Iyer", "title": "No data for 3 d",
                 "detail": "The app has sent nothing."},
                {"type": "NO_LOGIN", "severity": "MEDIUM", "employee_id": "EM104",
                 "employee_name": "Vikram Rao", "title": "Not logged in",
                 "detail": "Shift begins at 09:00."},
            ]})
        check("every alert gets a row", tab._table.rowCount() == 2, str(tab._table.rowCount()))
        check("the headline counts them", "2 thing" in tab._headline.text(),
              tab._headline.text())
        check("the employee is named, not just numbered",
              "Sneha Iyer" in tab._table.item(0, 1).text(), tab._table.item(0, 1).text())
        check("and what is wrong is spelled out",
              "3 d" in tab._table.item(0, 2).text(), tab._table.item(0, 2).text())

        print("\nWhen the check itself fails")
        # THE ONE THAT MATTERS. An empty table here would read as "all clear"
        # at the exact moment the panel has no idea what is going on.
        started[-1].error.emit("Connection refused")
        check("it says the check failed",
              "Could not check" in tab._headline.text(), tab._headline.text())
        check("and does NOT quietly show an all-clear",
              "Nothing needs attention" not in tab._headline.text(), tab._headline.text())

        print("\nWhen alerts are switched off")
        started[-1].result.emit({
            "enabled": False, "total": 0, "counts": {}, "alerts": []})
        check("the page says they are off, rather than claiming all is well",
              "switched off" in tab._headline.text(), tab._headline.text())

        print("\nThe timer, and logout")
        check("its timer is named so the panel's shutdown can find it",
              hasattr(tab, "_refresh_timer"),
              "a timer under any other name keeps firing after logout, "
              "with a cleared token, at a widget being destroyed")
        check("the Alerts tab is in the list that gets shut down",
              "_alerts_tab" in panel.AdminConfigPanel.TAB_ATTRS,
              str(panel.AdminConfigPanel.TAB_ATTRS))

        print("\nThe sidebar and the tabs must agree")
        keys = [p["key"] for p in panel.PAGES]
        check("Alerts has a place in the menu", "alerts" in keys, str(keys))
        check("right after the Dashboard, where it will be seen",
              keys.index("alerts") == 1, str(keys))
        # Every page key needs a tab, in the same order.
        check("the shutdown list covers every page",
              len(panel.AdminConfigPanel.TAB_ATTRS) == len(keys),
              f"{len(keys)} pages but {len(panel.AdminConfigPanel.TAB_ATTRS)} tabs listed")
    finally:
        panel._FetchWorker = original
        panel._track_worker = original_track

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.stdout.flush()
        sys.exit(1)
    print("all alerts tab checks passed")
    sys.stdout.flush()
    sys.exit(0)


main()
