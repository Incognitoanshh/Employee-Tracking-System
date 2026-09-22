"""
The payroll table: fourteen columns, and the figures that land in them.

WHY THIS EXISTS. A table that fills its cells by index has one silent failure
mode: insert a column and every cell after it moves one place right. Nothing
raises, nothing is logged, and the page shows the wrong values under the right
headings. It has happened in this panel before, and on a payroll page the
wrong value under "Net salary" is not a display bug.

This one goes further than counting headings, because two of the new columns
can be wrong while still looking plausible:

  * DEDUCTIONS must be the server's own total. The line carries absence,
    unpaid leave, lateness and whatever was entered by hand; a panel that adds
    up a subset of those shows a smaller number than the payslip it describes,
    and the footer stops matching the column above it.

  * LATE MINUTES must be the minutes, not the days. Three late mornings and
    ninety minutes are different facts, and swapping them is invisible.

So the tab is built, given a payload, and read back cell by cell.

Run:  python3 tests/test_payroll_tab.py
"""
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# This suite builds a real AdminConfigPanel near the end to check a payroll
# page through a theme rebuild.  Keep that construction away from a developer's
# installed client data and from any reachable server, just as test_theme does.
# The panel's normal startup path assumes main.py has already created the local
# schema; this stand-alone test must provide that same precondition itself.
os.environ.setdefault("ETS_DATA_DIR", tempfile.mkdtemp(prefix="ets_test_"))
os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:9/api")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMP = tempfile.mkdtemp(prefix="ets_payroll_tab_test_")
import client.core.config as config                                  # noqa: E402
config.STORAGE_DIR = _TMP
from client.infrastructure.database import database as database_module  # noqa: E402
database_module.Database.DB_PATH = os.path.join(_TMP, "ets.db")

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok or not detail else f"  — {detail}"))


from PySide6.QtWidgets import QApplication                       # noqa: E402

app = QApplication.instance() or QApplication([])

from client.infrastructure.database.database import Database      # noqa: E402
from client.presentation.windows import admin_config_panel as panel  # noqa: E402

# AdminConfigPanel is normally reached through main.py, which has already
# made every local table ChatManager and the settings services use.
Database.initialize()

print("\nThe payroll table\n")

# Built without _set_default_month, which fires a network request on
# construction. The tab under test is the rendering, not the fetch.
tab = panel._PayrollTab.__new__(panel._PayrollTab)
panel.QWidget.__init__(tab)
tab._workers = []
tab._month = "2026-06"
tab._lines = []
tab._status = "NONE"
tab._selected_employee = None
tab._build_ui()

HEADINGS = ["Employee", "Gross salary", "Working days", "Present", "Leave",
            "Absent", "Late", "Late minutes", "Overtime hours",
            "Overtime amount", "Adjustments", "Deductions", "Net salary",
            "Status", ""]

check("the table has one column per heading",
      tab._table.columnCount() == len(HEADINGS),
      f"{tab._table.columnCount()} columns, {len(HEADINGS)} headings")

actual = [tab._table.horizontalHeaderItem(i).text()
          for i in range(tab._table.columnCount())]
check("and they are the headings the brief asks for", actual == HEADINGS,
      str(actual))

# ── a line with something in every column ───────────────────────────────
LINE = {
    "employee_id": "E001", "employee_name": "Asha", "department": "Engineering",
    "designation": "Senior Developer",
    "gross_monthly": 26000, "working_days": 26, "present_days": 24,
    "paid_leave_days": 1, "unpaid_leave_days": 1, "absent_days": 0,
    "late_days": 3, "late_minutes": 90, "late_deduction": 0,
    "other_deductions": 1800,
    "deductions": [{"kind": "PF", "amount": 1800, "reason": "Provident fund"}],
    # 1000 a day, one unpaid day, plus 1800 entered — the server's own total.
    "total_deductions": 2800,
    "overtime_hours": 10, "overtime_amount": 2000,
    "adjustments": [], "adjustments_total": 0,
    "net_before_adjustments": 25200, "net_pay": 25200,
}

tab._populate({
    "run": {"month": "2026-06", "status": "DRAFT", "working_days": 26},
    "lines": [LINE],
    "totals": {"gross": 26000, "deductions": 2800, "net": 25200},
})

cell = lambda column: tab._table.item(0, column).text()      # noqa: E731

# THE PERSON IS AN ITEM, NOT A CELL WIDGET — so the table paints it and the
# text follows the row: selected, hovered, light theme or dark. A QLabel with
# a colour baked into it disappeared entirely on a light row.
check("the employee cell names the person", "Asha" in cell(0), cell(0))
check("with the id and job title on a second line",
      "E001" in cell(0) and "Senior Developer" in cell(0)
      and "\n" in cell(0), repr(cell(0)))
check("and a face beside it",
      not tab._table.item(0, 0).icon().isNull())
check("nothing is a cell widget in that column, which would paint over it",
      tab._table.cellWidget(0, 0) is None)
check("the gross is money", cell(1) == "₹26,000.00", cell(1))
check("the working days are the month's", cell(2) == "26", cell(2))
check("present days", cell(3) == "24", cell(3))
# A NUMBER, NOT A SENTENCE. "0 (+1 unpaid)" needed a column as wide as a
# phrase and was cut to "0 (+1 unp…" in any normal window.
check("leave is a plain number", cell(4) == "2", cell(4))
check("and the paid/unpaid split is on the tooltip",
      "unpaid" in (tab._table.item(0, 4).toolTip() or ""),
      tab._table.item(0, 4).toolTip())
check("absent", cell(5) == "0", cell(5))

check("LATE is the number of mornings", cell(6) == "3", cell(6))
check("LATE MINUTES is the minutes, not the days", cell(7) == "90", cell(7))
check("and it says whether the lateness was charged",
      "not charged" in (tab._table.item(0, 7).toolTip() or ""),
      tab._table.item(0, 7).toolTip())

check("overtime hours are separate from what they came to", cell(8) == "10", cell(8))
check("overtime amount is money", cell(9) == "₹2,000.00", cell(9))
check("adjustments show a dash when there are none", cell(10) == "—", cell(10))

# THE ONE THAT MATTERS. 2800 is the server's figure; 1000 would be what the
# panel gets by adding up only absence and unpaid leave, which is what it used
# to do before lateness and entered deductions existed.
check("DEDUCTIONS is the server's total, every kind included",
      cell(11) == "₹2,800.00", f"{cell(11)} — expected ₹2,800.00")
check("and the itemised deductions are on the tooltip",
      "PF" in (tab._table.item(0, 11).toolTip() or ""),
      tab._table.item(0, 11).toolTip())

check("net salary", cell(12) == "₹25,200.00", cell(12))
check("and the row carries the month's status", cell(13) == "Draft", cell(13))

# THE PERSON IS A WIDGET, NOT A STRING — a face, a name and a job title. The
# item behind it stays so that sizing and selection still work.
check("there is an action button on the row",
      tab._table.cellWidget(0, 14) is not None)

# ── the cards above the table ───────────────────────────────────────────
# They are read at a glance and believed. A card that adds up a different set
# of the same numbers than the table under it is worse than no card.
check("the employee count is the number of rows",
      tab._kpis["employees"].text() == "1", tab._kpis["employees"].text())
check("total gross", tab._kpis["gross"].text() == "₹26,000.00",
      tab._kpis["gross"].text())
check("total deductions matches the column beneath it",
      tab._kpis["deductions"].text() == "₹2,800.00",
      tab._kpis["deductions"].text())
check("total payout", tab._kpis["net"].text() == "₹25,200.00",
      tab._kpis["net"].text())

# ── benefits and deductions ─────────────────────────────────────────────
# A DASH, NOT ZERO, for a head nobody is enrolled in. "Nobody pays ESI" and
# "ESI came to nothing this year" are different facts, and ₹0.00 states the
# second while meaning the first — which is the figure somebody would then go
# and try to remit.
tab._fill_benefits({
    "epf": {"total": 1800, "employees": 1, "configured": True},
    "esi": {"total": 0, "employees": 0, "configured": False},
    "professional_tax": {"total": 400, "employees": 2, "configured": True},
    "tax": {"total": 0, "employees": 0, "configured": False},
})
check("a head that was deducted shows the money",
      tab._benefits["epf"][0].text() == "₹1,800.00",
      tab._benefits["epf"][0].text())
check("and how many people it covered",
      tab._benefits["epf"][1].text() == "1 employee",
      tab._benefits["epf"][1].text())
check("professional tax pluralises properly",
      tab._benefits["professional_tax"][1].text() == "2 employees",
      tab._benefits["professional_tax"][1].text())
check("a head nobody is enrolled in reads as a dash, not zero",
      tab._benefits["esi"][0].text() == "—", tab._benefits["esi"][0].text())
check("and says so plainly",
      tab._benefits["esi"][1].text() == "not deducted",
      tab._benefits["esi"][1].text())

# ── the breakdown, which now opens OVER the page rather than beside it ──
# THE TABLE KEEPS ITS WIDTH. A 390px panel next to fifteen columns left three
# of them visible and cut the last row in half; the breakdown is worth a panel
# but not two thirds of the table it describes.
check("nothing on the page steals the table's width",
      not hasattr(tab, "_details"))

tab._table.selectRow(0)
check("selecting a row only remembers who it is",
      tab._selected_employee == "E001", str(tab._selected_employee))

breakdown = panel._PayrollDetails()
breakdown.show_line(LINE, "DRAFT")
tab._details = breakdown          # what the dialog builds, checked below
check("the breakdown names the person",
      tab._details._name.text() == "Asha", tab._details._name.text())

# EVERY DEDUCTION, COMPUTED AND ENTERED, IN ONE LIST. The panel is where
# somebody answers "why is this number what it is", and a provident fund that
# appears in the total but not in the list makes that unanswerable.
labels = []
grid = tab._details._grid_deductions
for i in range(grid.count()):
    widget = grid.itemAt(i).widget()
    if widget is not None:
        labels.append(widget.text())
check("the deductions tab itemises the entered ones",
      any("Pf" == text or "PF" == text for text in labels), str(labels))
check("and shows the computed ones beside them",
      "Absence" in labels and "Lateness" in labels, str(labels))
check("with a total that matches the table",
      "₹2,800.00" in labels, str(labels))

earnings = []
grid = tab._details._grid_earnings
for i in range(grid.count()):
    widget = grid.itemAt(i).widget()
    if widget is not None:
        earnings.append(widget.text())
check("the earnings tab totals gross and overtime",
      "₹28,000.00" in earnings, str(earnings))
check("and says what the overtime was paid at",
      any("hrs" in text for text in earnings), str(earnings))

# ── WHAT THE GROSS IS MADE OF ──────────────────────────────────────────
#
# This tab was one line — "Gross salary" — on the panel whose whole job is
# answering "how did that number happen". The parts are what everything else
# is computed from: provident fund is a share of Basic plus DA, not of the
# gross, so a payslip that hides them cannot be checked by the person it
# belongs to.
#
# The figures come from the server, frozen onto the line at generation. See
# server/tests/test_payslip_components.js for why they are copied rather than
# looked up: correcting a salary in place rewrites its split, and a finalised
# payslip must not pick that up.
SPLIT = [
    {"name": "Basic", "rule": "PERCENT_CTC", "value": 50, "monthly": 13000},
    {"name": "DA", "rule": "PERCENT_BASIC", "value": 20, "monthly": 2600},
    {"name": "House Rent Allowance", "rule": "PERCENT_BASIC", "value": 50,
     "monthly": 6500},
    {"name": "Conveyance Allowance", "rule": "PERCENT_BASIC", "value": 15,
     "monthly": 1950},
    {"name": "Fixed Allowance", "rule": "BALANCE", "value": 0, "monthly": 1950},
]
tab._details.show_line({**LINE, "components": SPLIT,
                        "components_total": 26000}, "DRAFT")
earnings = []
grid = tab._details._grid_earnings
for i in range(grid.count()):
    widget = grid.itemAt(i).widget()
    if widget is not None:
        earnings.append(widget.text())

check("every component is named",
      all(any(part["name"] in text for text in earnings) for part in SPLIT),
      str(earnings))
check("with how it was worked out, not just what it came to",
      any("50% of CTC" in text for text in earnings)
      and any("20% of Basic" in text for text in earnings)
      and any("balance" in text for text in earnings), str(earnings))
check("and the amounts themselves", "₹13,000.00" in earnings, str(earnings))

# THE PARTS ADD BACK TO THE WHOLE, on screen. A reader must be able to run
# down the column and reach the gross printed under it — a payslip whose
# components do not sum to its gross is one somebody has to explain.
shown = [float(t.replace("₹", "").replace(",", "")) for t in earnings
         if t.startswith("₹")]
check("the components on screen add up to the gross on screen",
      round(sum(shown[:len(SPLIT)]), 2) == 26000.0,
      f"{sum(shown[:len(SPLIT)])} from {shown[:len(SPLIT)]}")

# ── AND A MONTH THAT HAS NO SPLIT SAYS SO ──────────────────────────────
#
# Every month generated before the split was recorded is in this state. The
# honest answer is to say nothing was recorded; filling it in from the salary
# in effect today would put a figure nobody froze onto a frozen payslip.
tab._details.show_line({**LINE, "components": [], "components_total": None},
                       "DRAFT")
earnings = []
grid = tab._details._grid_earnings
for i in range(grid.count()):
    widget = grid.itemAt(i).widget()
    if widget is not None:
        earnings.append(widget.text())
check("a month with no recorded split says so rather than inventing one",
      any("not recorded" in text for text in earnings), str(earnings))
check("and still shows the gross it did pay",
      "₹26,000.00" in earnings, str(earnings))
check("with no component names on it",
      not any(part["name"] in text for part in SPLIT for text in earnings),
      str(earnings))

tab._details.show_line(LINE, "DRAFT")

# REFILLING A TAB MUST REPLACE ITS ROWS, NOT PILE ON TOP OF THEM.
# deleteLater() alone does not remove a widget: it stays parented and stays
# PAINTED until the event loop comes round, floating over whatever replaced
# it. That is what put "None on this month" across the tab buttons and made
# pressing Summary look like it did nothing — the old tab was still drawn on
# top of the new one.
#
# Counted on the page itself, because a widget that has been taken out of the
# layout but not reparented is still a child of it.
adjustments_page = tab._details._stack.widget(3)
before = len(adjustments_page.findChildren(panel.QLabel))
for _ in range(3):
    tab._details.show_line(LINE, "DRAFT")
after = len(adjustments_page.findChildren(panel.QLabel))
check("refilling a tab replaces its rows rather than stacking them",
      after == before, f"{before} rows became {after} after three refills")

check("the net is stated plainly at the bottom",
      "₹25,200.00" in tab._details._net.text(), tab._details._net.text())

# ── what finalising closes ──────────────────────────────────────────────
check("a draft may be deleted", tab._delete_btn.isEnabled())
check("and finalised", tab._finalize_btn.isEnabled())

tab._populate({
    "run": {"month": "2026-06", "status": "FINALIZED", "working_days": 26},
    "lines": [LINE],
    "totals": {"gross": 26000, "deductions": 2800, "net": 25200},
})
check("a finalised month cannot be finalised again",
      not tab._finalize_btn.isEnabled())
check("nor deleted — it is the record of what people were paid",
      not tab._delete_btn.isEnabled())
check("and every row says so", tab._table.item(0, 13).text() == "Finalised",
      tab._table.item(0, 13).text())

# ── and a month that was never generated ────────────────────────────────
tab._populate({"run": None, "lines": [], "totals": {}})
check("and the panel closes with it — no stale figures left on screen",
      tab._details.isHidden())
check("an ungenerated month offers neither button",
      not tab._finalize_btn.isEnabled() and not tab._delete_btn.isEnabled())
check("and shows an empty table", tab._table.rowCount() == 0,
      str(tab._table.rowCount()))

# ── an empty month has to say where the data IS ─────────────────────────
#
# Reported off a live screen: the page opens on last month, that month had no
# run, so the table was empty, all five cards read "—" and the chip said "Not
# generated" — and it was read as the product being broken. Nothing was
# broken; the data was two months back and the page would not say so.
#
# The months come from the history the chart already fetches, so this costs
# no extra request — and that request RACES the month's own, which is why the
# note is written from both and checked from both here.
HISTORY = {"data": [
    {"month": "2026-04", "status": "DRAFT", "employees": 4},
    {"month": "2026-06", "status": "FINALIZED", "employees": 4},
    {"month": "2026-07", "status": "FINALIZED", "employees": 4},
]}

tab._history = []
tab._month = "2026-08"
tab._month_box.setText("2026-08")
tab._populate({"run": None, "lines": [], "totals": {}})
check("an empty month explains itself", not tab._empty_row.isHidden())
# Before the history lands there is nothing to offer, and offering a button
# that goes nowhere is worse than none.
check("with nothing to open before the history arrives",
      tab._open_latest.isHidden(), tab._open_latest.text())
check("and it still says what to press",
      "Generate draft" in tab._empty_note.text(), tab._empty_note.text())

tab._fill_chart(HISTORY)
check("once the history lands it names the nearest month that has one",
      "2026-08" in tab._empty_note.text() and "2026-07" in tab._empty_note.text(),
      tab._empty_note.text())
check("says which kind it is, and how many people are on it",
      "finalised" in tab._empty_note.text() and "4 people" in tab._empty_note.text(),
      tab._empty_note.text())
# NOT "nothing is missing" as a reassurance — as the fact. An empty month
# that was never generated is not a failed load, and the difference is the
# whole report.
check("and that nothing here is missing",
      "nothing here is missing" in tab._empty_note.text(), tab._empty_note.text())
check("the way there is one press", not tab._open_latest.isHidden()
      and tab._open_latest.text() == "Open 2026-07", tab._open_latest.text())

loaded = []
real_load = tab._load
tab._load = lambda: loaded.append(tab._month_box.text())
try:
    tab._open_latest.click()
finally:
    tab._load = real_load
check("and pressing it opens that month", loaded == ["2026-07"], str(loaded))

# THE MONTH ON SCREEN IS NEVER THE ONE OFFERED. A run deleted while the
# history still lists the month would otherwise offer "Open 2026-07" to
# somebody already looking at an empty 2026-07, and pressing it would do
# nothing at all.
tab._month = "2026-07"
tab._month_box.setText("2026-07")
tab._populate({"run": None, "lines": [], "totals": {}})
check("the month already on screen is not offered back",
      tab._open_latest.text() == "Open 2026-06", tab._open_latest.text())

tab._populate({
    "run": {"month": "2026-06", "status": "FINALIZED", "working_days": 26},
    "lines": [LINE],
    "totals": {"gross": 26000, "deductions": 2800, "net": 25200},
})
check("and a month with figures in it says none of this",
      tab._empty_row.isHidden())

# ── a zero that explains itself ─────────────────────────────────────────
#
# Reported from a live screen: a new employee's salary had been set, the month
# showed ₹0.00, and the Set salary page showed ₹25,000 two clicks away —
# "rajesh ka salary set h already kya h bhai har ek cheez ka mapping dekh na".
# Both figures were right. A month is paid on the salary in force DURING it,
# and that one starts on the first of the next month. Nothing said so.
#
# The two zeroes are not the same thing and do not want the same answer: one
# needs a salary set, the other needs its month to arrive.
tab._populate({
    "run": {"month": "2026-08", "status": "DRAFT", "working_days": 26},
    "lines": [
        dict(LINE, employee_id="E900", employee_name="adi",
             gross_monthly=0, net_pay=0, salary_starts_on=None),
        dict(LINE, employee_id="E901", employee_name="rajesh r",
             gross_monthly=0, net_pay=0, salary_starts_on="2026-09-01"),
        dict(LINE, employee_id="E902", employee_name="Asha", gross_monthly=52000),
    ],
    "totals": {"gross": 52000, "deductions": 0, "net": 52000},
})
note = tab._zero_note.text()
check("a month that pays somebody nothing says who", not tab._zero_note.isHidden())
check("naming both of them", "adi" in note and "rajesh r" in note, note)
check("and NOT the person who was paid", "Asha" not in note, note)
check("one of them needs a salary set", "no salary set" in note, note)
check("the other is waiting for the month its pay starts in",
      "01 Sep 2026" in note or "1 Sep 2026" in note, note)
check("and the rule is stated, not left to be guessed",
      "in force during it" in note, note)

# ON THE FIGURE ITSELF TOO. The note is above the table; somebody reading a
# row wants the answer on the row.
tip_none = tab._table.item(0, 1).toolTip()
tip_later = tab._table.item(1, 1).toolTip()
check("the ₹0.00 cell carries the reason as well",
      "no salary on record" in tip_none, tip_none)
check("and the other says when the pay begins",
      "starts on" in tip_later and "Sep 2026" in tip_later, tip_later)
check("a figure that was paid explains nothing",
      not tab._table.item(2, 1).toolTip(), tab._table.item(2, 1).toolTip())

tab._populate({
    "run": {"month": "2026-08", "status": "DRAFT", "working_days": 26},
    "lines": [dict(LINE, employee_id="E902", employee_name="Asha", gross_monthly=52000)],
    "totals": {"gross": 52000, "deductions": 0, "net": 52000},
})
check("and a month where everybody is paid says none of it",
      tab._zero_note.isHidden())

# ── everybody on the run is on the page ─────────────────────────────────
#
# Reported with the count: "total sab mila kr 8 hai but yaha 5 show ho rha
# hai… ye scrollable hona chahiye yaha saare employee aur admins dikhne
# chahiye". Six people were on the run and five were on screen. The table had
# a 340px minimum and a stretch factor, and a stretch factor inside a scroll
# area means nothing — so it stayed 340px whether it held five people or
# fifty, and the rest were inside its own small scroll, in a page that
# scrolls too. The bottom was hidden twice.
#
# MEASURED IN PIXELS, not "does it have a scrollbar": the table is not on
# screen in this test, and a scrollbar's range is not settled until it is.
row_h = tab._table.verticalHeader().defaultSectionSize()
head = tab._table.horizontalHeader().height()

MANY = [dict(LINE, employee_id=f"E1{n:02}", employee_name=f"Person {n}")
        for n in range(6)]
tab._populate({
    "run": {"month": "2026-08", "status": "DRAFT", "working_days": 26},
    "lines": MANY,
    "totals": {"gross": 156000, "deductions": 0, "net": 156000},
})
check("six people make a table six rows tall",
      tab._table.height() >= head + 6 * row_h,
      f"{tab._table.height()}px for {head} + 6 x {row_h}")
# THE VIEWPORT, NOT THE WIDGET. This table is fifteen columns wide and always
# carries a horizontal scrollbar, and that bar takes its height out of the
# rows: the first fix left the viewport ten pixels short, so the last person
# was still cut in half and the table still grew a scrollbar of its own.
app.processEvents()
check("and the rows fit INSIDE it, under the horizontal scrollbar",
      tab._table.viewport().height() >= 6 * row_h,
      f"viewport {tab._table.viewport().height()}px for 6 x {row_h}")

# AND IT KEEPS GROWING. Forty people is the case the 340px box was worst
# for — thirty-four of them behind an inner scrollbar.
FORTY = [dict(LINE, employee_id=f"E2{n:02}", employee_name=f"Person {n}")
         for n in range(40)]
tab._populate({
    "run": {"month": "2026-08", "status": "DRAFT", "working_days": 26},
    "lines": FORTY,
    "totals": {"gross": 1040000, "deductions": 0, "net": 1040000},
})
check("and forty make it forty rows tall, not a 340px window onto them",
      tab._table.height() >= head + 40 * row_h,
      f"{tab._table.height()}px for {head} + 40 x {row_h}")

# A FILTER SHRINKS IT AGAIN. Hidden rows are still rows to rowCount, so
# searching one name used to leave that name with two thousand pixels of
# empty table under it.
tab._search.setText("Person 7")
app.processEvents()
check("one match is one row tall",
      tab._table.height() < head + 3 * row_h,
      f"{tab._table.height()}px for {head} + 1 x {row_h}")
tab._search.setText("")
app.processEvents()
check("and clearing the search brings the height back",
      tab._table.height() >= head + 40 * row_h, str(tab._table.height()))

# ── the salary form is a PAGE, not a dialog ─────────────────────────────
#
# It was three modals — a list, a form and a history window — that could not
# be open together, so checking what somebody was on before changing it meant
# closing one to open another and holding the figure in your head. The form
# also had nowhere to grow: the salary structure is a table of five
# components, and in a dialog that was the part that got cut off.
#
# THE BUG THIS ALSO GUARDS. The old form used QDoubleSpinBox without importing
# it, so pressing "Set salary" raised NameError inside the click handler —
# which Qt swallows. No dialog, no error, no log line: the button did nothing.
print("\nThe salary page")

from PySide6.QtWidgets import QDialog                             # noqa: E402

page = panel._SalaryPage()
check("it is a page, not a dialog",
      isinstance(page, panel.QWidget) and not isinstance(page, QDialog))
check("with the annual CTC on it",
      page._ctc.suffix().strip() == "per year", repr(page._ctc.suffix()))
check("the monthly gross is derived, not typed", page._gross.isReadOnly())
check("and the statutory toggles are there",
      page._epf is not None and page._esi is not None and page._pt is not None)

# NOTHING IS EDITABLE UNTIL SOMEBODY IS CHOSEN. Saving against nobody is the
# one thing this page must not allow.
check("nothing can be set before an employee is chosen",
      not page._save.isEnabled() and not page._ctc.isEnabled())

page._fill({"data": [{"employee_id": "E001", "employee_name": "Asha Verma",
                      "gross_monthly": 23333, "overtime_hourly": 300,
                      "effective_from": "2026-01-01", "ctc_annual": 280000,
                      "epf_enabled": True, "esi_enabled": False,
                      "pt_enabled": True}]})
page._table.selectRow(0)
check("choosing somebody opens the form", page._save.isEnabled())
check("and loads their CTC", page._ctc.value() == 280000, str(page._ctc.value()))
check("and their statutory switches",
      page._epf.isChecked() and page._pt.isChecked() and not page._esi.isChecked())

# ── the split, shown as the CTC is typed ────────────────────────────────
#
# THE ARRANGEMENT COMES FROM THE SERVER, so the test states it the way the
# server does. It used to be written into the client, which meant this check
# passed against a copy rather than against the policy — and the copy could
# not go out of date because nothing ever compared them.
panel._remember_salary_template([
    {"name": "Basic", "rule": "PERCENT_CTC", "value": 50},
    {"name": "DA", "rule": "PERCENT_BASIC", "value": 20},
    {"name": "House Rent Allowance", "rule": "PERCENT_BASIC", "value": 50},
    {"name": "Conveyance Allowance", "rule": "PERCENT_BASIC", "value": 15},
    {"name": "Fixed Allowance", "rule": "BALANCE", "value": 0},
])
page._ctc.setValue(280000)
# Redrawn by hand: the CTC is ALREADY 280000 from the row that was selected
# above, so setValue changes nothing and valueChanged never fires.
page._restate()
parts = [page._components.item(r, 0).text()
         for r in range(page._components.rowCount())]
check("the CTC divides into components", len(parts) == 5, str(parts))
check("starting with Basic", parts[0] == "Basic", str(parts))

amounts = [float(page._components.item(r, 2).text()
                 .replace("₹", "").replace(",", ""))
           for r in range(page._components.rowCount())]
check("Basic is half the monthly CTC", abs(amounts[0] - 280000 / 24) < 0.01,
      str(amounts[0]))
# THE ONE THAT MATTERS: the parts add back to the whole, and the gross shown
# is that sum. A preview whose parts do not total the CTC is worse than none.
check("the parts add back to the monthly CTC",
      abs(sum(amounts) - 280000 / 12) < 0.01, f"{sum(amounts):.2f}")
check("and the monthly gross shown is that sum",
      abs(page._gross.value() - sum(amounts)) < 0.01,
      f"{page._gross.value()} vs {sum(amounts):.2f}")

# ── THE WHOLE SALARY STRUCTURE CAN BE SEEN ──────────────────────────────
#
# REPORTED AS: "set salary kar rahe to full view nahi hai". Measured, two
# faults stacked on top of each other:
#
#   * the component table had setMinimumHeight(200) — five rows in a box
#     three rows deep — so Conveyance and Fixed Allowance lived inside the
#     TABLE's own scroll, even with the page scrolled to the bottom;
#   * the page was split evenly, so on a 1180px window the table got 503px
#     against the 652px its columns need, grew a horizontal scrollbar, and
#     that scrollbar took the height the last row needed.
#
# So the check is taken at a small laptop's size, with the page scrolled to
# its end, and asks the three things that together mean "you can read it":
# the table does not scroll inside itself, it does not scroll sideways, and
# its last row is inside the window.
from PySide6.QtCore import QPoint                                      # noqa: E402

cut = []
for width, height in ((1180, 640), (1180, 760), (1520, 900)):
    page.resize(width, height)
    page.show()
    for _ in range(4):
        app.processEvents()
    page._restate()
    for _ in range(3):
        app.processEvents()
    bar = page._scroll.verticalScrollBar()
    bar.setValue(bar.maximum())
    app.processEvents()
    table, viewport = page._components, page._scroll.viewport()
    last = table.rowCount() - 1
    bottom = table.viewport().mapTo(
        viewport, QPoint(0, table.rowViewportPosition(last)
                         + table.rowHeight(last))).y()
    if table.verticalScrollBar().maximum() > 0:
        cut.append(f"{width}x{height}: the table scrolls inside itself")
    if table.horizontalScrollBar().isVisible():
        cut.append(f"{width}x{height}: the table scrolls sideways")
    if bottom > viewport.height():
        cut.append(f"{width}x{height}: last row ends at {bottom}px of {viewport.height()}")
    bar.setValue(0)
check("every component row can be read, on a small laptop as well",
      not cut, "; ".join(cut))
check("and every figure is shown in full, not ellipsised",
      not any("…" in page._components.item(r, c).text()
              for r in range(page._components.rowCount()) for c in (2, 3)))
page.hide()

# ── A COMPONENT SET BY HAND ─────────────────────────────────────────────
#
# "Dono option rakho — auto calculation bhi, aur zaroorat pade to haath se,
# kyunki bahut saare components variable hote hain." The CTC fills every row;
# any row but the balance can be typed over; the balance takes the difference
# so the parts still equal the CTC. server/tests/test_salary_ctc.js holds the
# arithmetic; these hold what the page lets somebody do and what it sends.
print("\nA component set by hand")

posted_salaries: list = []


class _CaptureSalary:
    def __init__(self, url, body=None, *a, **k):
        posted_salaries.append((url, body))

    def __getattr__(self, _name):
        return type("_S", (), {"connect": lambda *a, **k: None})()

    def start(self):
        pass


real_post_worker, real_track = panel._PostWorker, panel._track_worker
panel._PostWorker = _CaptureSalary
panel._track_worker = lambda *a, **k: None
try:
    page._ctc.setValue(600000)                 # 50,000 a month
    page._template = [dict(c) for c in panel._SALARY_TEMPLATE]
    page._restate()
    names = [page._components.item(r, 0).text()
             for r in range(page._components.rowCount())]
    hra_row, balance_row = names.index("House Rent Allowance"), len(names) - 1

    def monthly(row):
        return float(page._components.item(row, 2).text()
                     .replace("₹", "").replace(",", ""))

    editable = panel.Qt.ItemFlag.ItemIsEditable
    check("an ordinary component's monthly figure can be edited",
          bool(page._components.item(hra_row, 2).flags() & editable))
    # THE BALANCE IS NOT TYPED. It is whatever the others leave, which is the
    # only thing that keeps the parts equal to the CTC after an edit.
    check("the balance row cannot be — it is what the others leave",
          not page._components.item(balance_row, 2).flags() & editable)

    # Typed over, as a double-click and Enter would.
    page._components.item(hra_row, 2).setText("15000")
    check("the typed figure is taken", monthly(hra_row) == 15000.0,
          str(monthly(hra_row)))
    check("and the row now says it was set by hand",
          page._components.item(hra_row, 1).text() == "Manual",
          page._components.item(hra_row, 1).text())
    total = round(sum(monthly(r) for r in range(page._components.rowCount())), 2)
    check("the balance moves so the parts still equal the CTC",
          total == 50000.0, f"parts come to {total}")
    check("and the monthly gross shown is still that sum",
          abs(page._gross.value() - 50000.0) < 0.01, str(page._gross.value()))
    check("Reset is offered once something has been set by hand",
          page._reset_split.isEnabled())

    # WHAT IS SENT. The split goes with the save only when it was changed —
    # sending the template anyway would freeze today's policy into this
    # person, so a later change to the arrangement would pass them by.
    page._selected = {"employee_id": "E001"}
    page._save.setEnabled(True)
    page._save_salary()
    body = posted_salaries[-1][1] if posted_salaries else {}
    sent = {c["name"]: c for c in body.get("components") or []}
    check("the save carries the split, with the typed figure as FIXED",
          sent.get("House Rent Allowance", {}).get("rule") == "FIXED"
          and sent["House Rent Allowance"].get("value") == 15000.0,
          str(sent.get("House Rent Allowance")))
    check("and nothing the server does not use", all(
        set(c) == {"name", "rule", "value"} for c in body.get("components") or []),
        str(body.get("components")))

    # ── TOO MUCH, BY HAND ───────────────────────────────────────────────
    # The balance cannot go below zero, so an overshoot is not an error the
    # arithmetic raises: it is a gross bigger than the CTC. Held back here.
    basic_row = names.index("Basic")
    page._components.item(basic_row, 2).setText("40000")
    check("figures that come to more than the CTC hold the save back",
          not page._save.isEnabled())
    check("and say by how much", "more than the CTC" in page._split_note.text(),
          page._split_note.text())

    # ── BACK TO THE CTC SPLIT ───────────────────────────────────────────
    page._reset_template()
    check("Reset puts every row back on its rule",
          all(page._components.item(r, 1).text() != "Manual"
              for r in range(page._components.rowCount())))
    check("the save is allowed again", page._save.isEnabled())
    posted_salaries.clear()
    page._save_salary()
    check("and a save with nothing set by hand sends no split of its own",
          posted_salaries and "components" not in posted_salaries[-1][1],
          str(posted_salaries[-1][1]) if posted_salaries else "nothing sent")

    # ── IT COMES BACK AS IT WAS SAVED ───────────────────────────────────
    #
    # The page used to rebuild every person's split from the company template
    # when they were opened: an override was saved, shown as gone the next
    # time, and erased by the next save.
    page._fill({"data": [{
        "employee_id": "E777", "employee_name": "Override Person",
        "gross_monthly": 50000, "overtime_hourly": 0,
        "effective_from": "2026-03-01", "ctc_annual": 600000,
        "epf_enabled": False, "esi_enabled": False, "pt_enabled": False,
        "components": [
            {"name": "Basic", "rule": "PERCENT_CTC", "value": 50, "monthly": 25000},
            {"name": "DA", "rule": "PERCENT_BASIC", "value": 20, "monthly": 5000},
            {"name": "House Rent Allowance", "rule": "FIXED", "value": 15000,
             "monthly": 15000},
            {"name": "Conveyance Allowance", "rule": "PERCENT_BASIC", "value": 15,
             "monthly": 3750},
            {"name": "Fixed Allowance", "rule": "BALANCE", "value": 0,
             "monthly": 1250}]}], "template": None})
    page._table.selectRow(0)
    app.processEvents()
    reopened = {page._components.item(r, 0).text(): r
                for r in range(page._components.rowCount())}
    hra = reopened.get("House Rent Allowance")
    check("opening somebody shows the figure they had set by hand",
          hra is not None and monthly(hra) == 15000.0,
          str(monthly(hra)) if hra is not None else "no HRA row")
    check("still marked as set by hand",
          hra is not None and page._components.item(hra, 1).text() == "Manual")
finally:
    panel._PostWorker, panel._track_worker = real_post_worker, real_track

# ── a page that is not in the menu ──────────────────────────────────────
#
# THE BUG THIS GUARDS, reported as "theme change karke khol raha hoon to nahi
# khul raha". Switching the theme saves the current stack index and restores
# it after rebuilding. The employee pay-history page sits after the fifteen
# the sidebar knows about, so with it open the saved index was 15 — and
# PAGES[15] raised IndexError in the middle of the rebuild. The console was
# left half-built: the theme changed and nothing worked afterwards, with no
# error anybody could see.
print("\nA page the sidebar does not know about")

from client.application.managers.session_manager import SessionManager  # noqa: E402
SessionManager.role = "super_admin"
SessionManager.employee_id = "SA001"
SessionManager.auth_token = "test"


class _Dead:
    """No network during a layout test."""
    def __init__(self, *a, **k):
        pass

    class _Signal:
        def connect(self, _f):
            pass

    result = _Signal()
    error = _Signal()
    finished = _Signal()

    def start(self):
        pass


from client.application.managers import chat_manager as chat_module  # noqa: E402
from client.presentation.windows import team_page                    # noqa: E402

# This is a layout test, not an application-startup test.  The real console
# starts ChatManager, the scheduler and idle tracking, while TeamPage schedules
# its own QThread-backed refresh.  Do not let those unrelated services start
# merely because the test needs to rebuild the widget tree for a theme check.
with (
    patch.object(panel, "_FetchWorker", _Dead),
    patch.object(panel, "_track_worker", lambda *a, **k: None),
    patch.object(chat_module.ChatManager, "start", lambda self: None),
    patch.object(panel.SchedulerService, "start", lambda self: None),
    patch.object(panel.IdleTracker, "start", lambda self: None),
    patch.object(team_page.TeamPage, "refresh", lambda self: None),
):
    try:
        console = panel.AdminConfigPanel()
        # The test-only startup guards above keep these services inert.  Calling
        # the normal shutdown path still verifies the console can clean itself up.
        console._stop_background_services()
        check("the stack holds more pages than the sidebar has entries",
              console.stack.count() > len(panel.PAGES),
              f"{console.stack.count()} pages, {len(panel.PAGES)} menu entries")

        console._payroll_tab.open_employee.emit("E002")
        check("double-clicking somebody opens their page",
              type(console.stack.currentWidget()).__name__ == "_EmployeePayrollPage",
              type(console.stack.currentWidget()).__name__)

        # The crash was here.
        console._toggle_theme()
        check("switching the theme with it open does not break the console",
              type(console.stack.currentWidget()).__name__ == "_EmployeePayrollPage",
              type(console.stack.currentWidget()).__name__)
        # AND IT STILL KNOWS WHOSE PAGE WAS OPEN. The rebuild makes a fresh page,
        # and a fresh page knows nobody: every figure went to a dash and "Set
        # salary" did nothing at all, because it returns early without an
        # employee id. Reported as "dark theme kiya empty, aur set salary button
        # bhi kaam nahi kar raha jab tak page change karke wapas na aa jaun".
        console._employee_payroll._employee_id = "E002"
        console._toggle_theme()
        check("and it still knows whose page it is after a theme switch",
              console._employee_payroll._employee_id == "E002",
              repr(console._employee_payroll._employee_id))

        check("and the page still opens afterwards",
              (console._payroll_tab.open_employee.emit("E002") or True)
              and type(console.stack.currentWidget()).__name__ == "_EmployeePayrollPage",
              type(console.stack.currentWidget()).__name__)
    finally:
        # STOP ITS BACKGROUND WORK BEFORE LETTING GO OF IT. A real console starts
        # timers and threads; left running, Qt aborts at interpreter exit with
        # "QThread: Destroyed while thread is still running" and the test process
        # dies with signal 6 — every check having passed. An exit code is part of
        # the result, so this is not tidiness.
        try:
            console._stop_tab_work()
            console._drain_workers()
            console.deleteLater()
            app.processEvents()
        except Exception:
            pass

print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILED'}\n")

# os._exit, NOT sys.exit — the same thing test_theme does, for the same
# reason. Building a real console starts threads that Qt tears down after the
# interpreter has begun shutting down, and it aborts the process with signal 6
# while every check has passed. An exit code is part of a test's result, so a
# run that says ALL PASS and exits 134 is a failing run.
sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if failures else 0)
