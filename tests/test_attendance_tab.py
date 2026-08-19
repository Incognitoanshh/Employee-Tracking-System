"""
The attendance table: its columns, and the clock that runs in one of them.

WHY A COLUMN-COUNT TEST EXISTS AT ALL. Inserting a column into a table that
fills its cells by index is the quietest bug this project has produced: every
cell after the insertion point moves one place right, nothing raises, and the
page simply shows the wrong values under the right headings. It happened once
already, in the reports tab, and was found by eye rather than by anything here.

Run:  python3 tests/test_attendance_tab.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PANEL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "client", "presentation", "windows", "admin_config_panel.py")
FULL = open(PANEL, encoding="utf-8").read()

# ONLY THE ATTENDANCE TAB. The panel holds a dozen tabs and several of them
# have a _populate of their own; the first draft of this test read another
# tab's method and reported five columns for a ten-column table. Slice the
# class out first, and everything below is unambiguous.
SOURCE = FULL.split("class _AttendanceTab(QWidget):")[1].split("\nclass ")[0]

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))


print("\nThe headings, and the cells that fill them")

headers = re.search(
    r'\["ID", "Employee", "Name", "Date", "Shift", "Check In",\s*'
    r'"Check Out", "Hours", "Attendance", "Shift"\]', SOURCE)
check("the attendance table declares its ten columns", headers is not None)

table = re.search(r'self\._table = _tune_table\(QTableWidget\(0, (\d+)\)\)\s*\n'
                  r'\s*self\._table\.setHorizontalHeaderLabels\(\s*\n?\s*\["ID", "Employee", "Name"',
                  SOURCE)
check("and is built with that many", table is not None and table.group(1) == "10",
      table.group(1) if table else "not found")

# Every setItem in _populate, in order. The highest index used must be one
# less than the column count — no more (writing off the end of the table) and
# no fewer (a column left permanently blank).
populate = SOURCE.split("def _populate(self, data: dict):")[1].split("\n    def ")[0]
# setItem AND setCellWidget. The status columns hold chips, which have to be
# real widgets — a QTableWidgetItem cannot carry a stylesheet. Counting only
# setItem made a migrated column look like a skipped one.
indices = sorted({int(m) for m in re.findall(
    r"self\._table\.set(?:Item|CellWidget)\(i, (\d+),", populate)})
check("every column from 0 to 9 is filled, none skipped",
      indices == list(range(10)), str(indices))

print("\nThe two status columns stay apart")
check("Attendance status is a chip in column 8",
      re.search(r"setCellWidget\(i, 8, _badge_cell\(", populate) is not None)
check("Shift status is a chip in column 9",
      re.search(r"setCellWidget\(i, 9, _badge_cell\(", populate) is not None)
check("the record column reads attendance_label",
      'row.get("attendance_label")' in populate)
check("the shift column reads shift_label",
      'row.get("shift_label")' in populate)
check("neither picks its own colours any more",
      'C["danger"]' not in populate and 'C["success"]' not in populate,
      "status colours belong in theme.status_colors, once")

print("\nNothing says 'signed in' or 'signed out' in a status cell again")
# The words that produced the contradiction: "Not signed out" beside
# "Signed in again". The logout column is a time or a dash now.
# Comments stripped: both phrases appear in the note explaining why they were
# removed, and a test that cannot tell a comment from code would forbid
# writing that explanation down.
code_only = "\n".join(line for line in populate.splitlines()
                      if not line.strip().startswith("#"))
check("no ACTIVE label is drawn in the check-out column",
      "● ACTIVE" not in code_only,
      "that belongs to the Attendance column, and saying it twice is what confused it")
check("no 'Not signed out' text either",
      "Not signed out" not in code_only)

print("\nThe running clock")
check("a live shift shows elapsed seconds, not a dash",
      'row.get("elapsed_seconds")' in populate and "_fmt_elapsed" in populate)
check("and only when the record is actually active",
      re.search(r'attendance_status"\) == "active"', populate) is not None,
      "a closed shift must show its final total, not a clock")

tick = SOURCE.split("def _tick_running_clocks(self):")[1].split("\n    def ")[0]
check("the tick advances only active rows",
      'attendance_status") != "active"' in tick)
check("it writes to the Hours column, column 7",
      "self._table.item(i, 7)" in tick, "the column the header calls Hours")
check("and survives the table being shorter than the data",
      "is not None" in tick,
      "a page swap between a tick and its cell lookup is a crash otherwise")
check("a one-second timer drives it",
      "self._tick_timer.setInterval(1000)" in SOURCE)
check("and the periodic refresh is left alone at 30s",
      "self._refresh_timer.setInterval(30000)" in SOURCE,
      "ticking must not turn into a fetch every second")

print("\nThe clock is the server's, not this laptop's")
check("elapsed time comes from the server's own count",
      "elapsed_seconds" in populate and "datetime.now" not in tick,
      "a laptop five minutes out would invent five minutes of work")

print("\nFormatting")
from client.presentation.windows.admin_config_panel import _fmt_elapsed  # noqa: E402

check("9420 seconds reads 02:37:00", _fmt_elapsed(9420) == "02:37:00", _fmt_elapsed(9420))
check("zero reads 00:00:00", _fmt_elapsed(0) == "00:00:00", _fmt_elapsed(0))
check("past a day it keeps counting hours rather than wrapping",
      _fmt_elapsed(90000) == "25:00:00", _fmt_elapsed(90000))
check("a negative count cannot appear", _fmt_elapsed(-5) == "00:00:00", _fmt_elapsed(-5))
check("nonsense gives a dash, not a crash", _fmt_elapsed("abc") == "—", _fmt_elapsed("abc"))
check("every width is the same, so the column cannot jitter",
      len({len(_fmt_elapsed(s)) for s in (0, 59, 600, 35999)}) == 1)


print("\nThe detail dialog, actually opened")
# EVERYTHING ABOVE READS THE SOURCE. This part runs it, because the faults
# that survive a source read are the ones only running finds: a missing key,
# a None where a string was assumed, a signal wired to a method that does not
# exist. An earlier draft of this dialog indexed row["elapsed_seconds"]
# directly and would have raised KeyError against every server older than the
# deploy that adds it — which is every server until it lands.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtWidgets import QTableWidgetItem  # noqa: E402

app = QApplication.instance() or QApplication([])

from client.presentation.windows import admin_config_panel as panel_module  # noqa: E402

# __new__ skips _AttendanceTab.__init__ (which would fetch from a server that
# is not running here) — but the QWidget base still has to be initialised, or
# Qt refuses to use the object as a dialog's parent.
tab = panel_module._AttendanceTab.__new__(panel_module._AttendanceTab)
panel_module.QWidget.__init__(tab)
opened = {}

# A REAL QDialog with only exec() replaced. A hand-written stub is not a
# QWidget, so QVBoxLayout(dialog) refuses it and the test fails on the stub
# rather than on the code — which is exactly what the first version did.
# Everything but the blocking call has to be genuine for this to prove
# anything.
class _StubDialog(panel_module.QDialog):
    def exec(self):
        opened["shown"] = True
        return 0

ROWS = [
    # A live shift, as a current server sends it.
    {"id": 1, "employee_id": "E001", "employee_name": "Rajesh",
     "login_time": "2026-08-17 04:00:00", "logout_time": None,
     "total_hours": None, "elapsed_seconds": 3600, "shift_window": "09:00–18:00",
     "attendance_status": "active", "attendance_label": "Active",
     "shift_status": "on_time", "shift_label": "On Time",
     "shift_notes": [], "leave_type": None},
    # A finished one, with notes and leave attached.
    {"id": 2, "employee_id": "E002", "employee_name": None,
     "login_time": "2026-08-17 04:00:00", "logout_time": "2026-08-17 12:00:00",
     "total_hours": "08:00:00", "elapsed_seconds": None, "shift_window": None,
     "attendance_status": "completed", "attendance_label": "Completed",
     "shift_status": "late", "shift_label": "Late 1h",
     "shift_notes": ["Early Exit 30m"], "leave_type": "SICK"},
    # AN OLD SERVER'S ROW: none of the new fields exist at all.
    {"id": 3, "employee_id": "E003", "login_time": "2026-08-17 04:00:00",
     "logout_time": None, "total_hours": None},
]

original_dialog = panel_module.QDialog
panel_module.QDialog = _StubDialog
try:
    for index, row in enumerate(ROWS):
        tab._attendance = ROWS
        tab._workers = []
        tab._page = 1
        tab._table = panel_module.QTableWidget(len(ROWS), 10)
        item = QTableWidgetItem("")
        tab._table.setItem(index, 0, item)
        opened.clear()
        try:
            tab._row_detail(tab._table.item(index, 0))
            check(f"row {row['id']} opens without raising", opened.get("shown") is True)
        except Exception as error:  # noqa: BLE001
            check(f"row {row['id']} opens without raising", False, repr(error))

    # A double-click landing past the end of the data must do nothing rather
    # than raise — the table and the data are replaced separately on refresh.
    tab._table = panel_module.QTableWidget(1, 10)
    tab._table.setItem(0, 0, QTableWidgetItem(""))
    tab._attendance = []
    try:
        tab._row_detail(tab._table.item(0, 0))
        check("a click past the end of the data is ignored", True)
    except Exception as error:  # noqa: BLE001
        check("a click past the end of the data is ignored", False, repr(error))
finally:
    panel_module.QDialog = original_dialog

print("\nHours: a row with two ends never shows a dash")
# A CHECK-IN, A CHECK-OUT, AND "—" FOR THE HOURS is the page contradicting
# itself, and it is what every row written before the server computed
# total_hours looked like. The difference between the two timestamps is not a
# guess; it is the same subtraction the server does.
tab2 = panel_module._AttendanceTab.__new__(panel_module._AttendanceTab)
panel_module.QWidget.__init__(tab2)

check("total_hours is used when it is there",
      tab2._hours_for({"total_hours": "07:29:38"}) == "07:29:38",
      tab2._hours_for({"total_hours": "07:29:38"}))
check("and the two timestamps are used when it is not",
      tab2._hours_for({"total_hours": None,
                       "login_time": "2026-07-14 04:20:00",
                       "logout_time": "2026-07-14 12:00:00"}) == "07:40:00",
      tab2._hours_for({"total_hours": None,
                       "login_time": "2026-07-14 04:20:00",
                       "logout_time": "2026-07-14 12:00:00"}))
check("an open shift still shows a dash — there is no end to subtract",
      tab2._hours_for({"total_hours": None,
                       "login_time": "2026-07-14 04:20:00",
                       "logout_time": None}) == "—")
check("and a logout before the login is refused rather than shown negative",
      tab2._hours_for({"total_hours": None,
                       "login_time": "2026-07-14 12:00:00",
                       "logout_time": "2026-07-14 04:20:00"}) == "—")

print("\nThe header chip reads a field that exists")
# TWICE NOW. SessionManager.token does not exist (the avatars went blank);
# SessionManager.employee_name does not exist either (the chip drew "?" where
# the initials belong). getattr with a default turns both into silence, so
# the only defence is naming the real attributes here.
from client.application.managers.session_manager import SessionManager  # noqa: E402

chip = FULL.split("chip = QFrame()")[1].split("lay.addWidget(chip)")[0]
for field in re.findall(r'getattr\(SessionManager, "(\w+)"', chip):
    check(f"SessionManager.{field} is a real attribute",
          hasattr(SessionManager, field),
          "getattr would quietly return None and the chip would show '?'")
check("the chip asks for full_name", '"full_name"' in chip, chip[:200])

print("\nColumn widths leave the Name column readable")
build = SOURCE.split("def _build_ui(self):")[1].split("\n    def ")[0]
check("the name column stretches", 'setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)' in build)
check("and has a floor, so stretch cannot collapse to nothing",
      "setMinimumSectionSize" in build,
      "nine fixed columns left the names as 'S…', 'R…', 'A…'")

print("\nall attendance tab checks passed" if failures == 0 else f"\n{failures} FAILED")
sys.exit(0 if failures == 0 else 1)
