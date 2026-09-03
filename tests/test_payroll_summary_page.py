"""
The payroll summary: a page, and a subtraction that reaches its own total.

WHY THIS EXISTS. The summary used to be a modal holding a headline, a totals
line and two tables. Two things were wrong with it and only one was cosmetic.

The cosmetic one: it was a fixed window whose second table sat below its own
fold on a laptop, and being modal it could not be held open beside the payroll
table it was summarising — so comparing a figure to its source meant closing
the window and remembering a number.

The one that mattered: it printed gross, unpaid leave, absence, overtime and
adjustments and then a TOTAL PAYROLL COST underneath, and those did not add
up. The payslip had grown a lateness charge and the entered deductions —
provident fund, ESI, professional tax — and this screen had never been told,
so it showed a subtraction that missed the total printed directly beneath it.
A reader had no way to know which number to believe. server/tests/
test_payroll_summary.js holds the other half of this, on the endpoint.

So the checks below are about arithmetic the READER can follow, not about
particular figures: every component the server sends is on screen, and the
components reconcile to the net shown under them.

AND NOTHING IS ADDED UP HERE. The page renders the server's totals and does
no arithmetic of its own — a screen that re-derives a total is a second
opinion about money, which is the whole problem being fixed. The test proves
that by feeding it totals whose parts do NOT reconcile and checking the page
reports them unchanged rather than quietly correcting them.

Run:  python3 tests/test_payroll_summary_page.py
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
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok or not detail else f"  — {detail}"))


from PySide6.QtWidgets import QApplication, QDialog                  # noqa: E402

app = QApplication.instance() or QApplication([])

from client.presentation.windows import admin_config_panel as panel  # noqa: E402

print("\nThe payroll summary page\n")

page = panel._PayrollSummaryPage()
check("it is a page, not a dialog",
      isinstance(page, panel.QWidget) and not isinstance(page, QDialog))
check("and it can be left the way it was reached",
      hasattr(page, "back"))

# ── nothing is invented before the server answers ───────────────────────
#
# A card reading ₹0.00 while the request is still out is a statement about the
# month, and a wrong one. The dash says "not known yet".
check("every figure starts at a dash, not at zero",
      all(v.text() == "—" for v in page._kpis.values())
      and page._net_total.text() == "—",
      ", ".join(f"{k}={v.text()}" for k, v in page._kpis.items()))

SUMMARY = {
    "success": True,
    "month": "2026-06",
    "status": "draft",
    "working_days": 26,
    "totals": {
        "employees": 2,
        "gross": 52000.0,
        "unpaid_leave_deduction": 500.0,
        "absent_deduction": 1000.0,
        "late_deduction": 2000.0,
        "other_deductions": 1800.0,
        "total_deductions": 5300.0,
        "late_minutes": 90,
        "overtime_hours": 4.0,
        "overtime_amount": 600.0,
        "adjustments": -200.0,
        "net": 47100.0,
    },
    "leave_deductions": [
        {"employee_id": "E001", "name": "Asha", "days": 0.5, "amount": 500.0}],
    "lateness": [
        {"employee_id": "E001", "name": "Asha", "days": 2, "minutes": 90,
         "amount": 2000.0}],
    "deductions": [
        {"employee_id": "E001", "name": "Asha", "amount": 1800.0,
         "items": [{"kind": "PF", "amount": 1800.0, "reason": "Provident fund"}]}],
    "overtime": [
        {"employee_id": "E002", "name": "Bilal", "hours": 4, "amount": 600.0}],
}

page.load("2026-06")          # clears, then would fetch
page._fill(SUMMARY)

check("the month and its state are on the page",
      page._title.text() == "2026-06" and page._status_chip.text() == "Draft",
      f"{page._title.text()!r} / {page._status_chip.text()!r}")
check("with the working days it was calculated over",
      "26" in page._working_days.text(), repr(page._working_days.text()))


def money(text: str) -> float:
    """Read a rendered figure back, sign and all."""
    cleaned = (text.replace("₹", "").replace(",", "")
                   .replace("−", "-").replace("±", "").strip())
    return float(cleaned or 0)


# ── the breakdown ───────────────────────────────────────────────────────
#
# EVERY COMPONENT IS SHOWN. The bug was a missing term, so the check that
# matters is that no term the server sends is left off the page.
for key in ("gross", "unpaid_leave_deduction", "absent_deduction",
            "late_deduction", "other_deductions", "overtime_amount",
            "adjustments"):
    shown = page._lines_ui.get(key)
    check(f"the breakdown shows {key}",
          shown is not None and shown.text() != "—",
          "missing" if shown is None else repr(shown.text()))

DEDUCTION_ROWS = ("unpaid_leave_deduction", "absent_deduction",
                  "late_deduction", "other_deductions")


def row_text(key: str) -> str:
    """What a breakdown row reads, or "" when the page has no such row."""
    label = page._lines_ui.get(key)
    return label.text() if label is not None else ""


check("deductions are shown as subtractions, not bare figures",
      all(row_text(k).startswith("−") for k in DEDUCTION_ROWS),
      ", ".join(f"{k}={row_text(k)!r}" for k in DEDUCTION_ROWS))

# THE ONE THAT MATTERS: what is on screen adds up to what is on screen.
def shown(key: str) -> float:
    """A figure on the page, or 0 if the page has no row for it.

    Missing rows are reported by the presence checks above; swallowing the
    KeyError here keeps a dropped term from ending the run in a traceback
    before the reconciliation below has had its say.
    """
    label = page._lines_ui.get(key)
    return money(label.text()) if label is not None else 0.0


gross = shown("gross")
subtracted = sum(shown(k) for k in
                 ("unpaid_leave_deduction", "absent_deduction",
                  "late_deduction", "other_deductions"))
overtime = shown("overtime_amount")
adjust = shown("adjustments")
reconciled = round(gross + subtracted + overtime + adjust, 2)
check("the column adds up to the NET PAYOUT printed under it",
      reconciled == money(page._net_total.text()),
      f"column reaches {reconciled}, total says {money(page._net_total.text())}")

check("and the deductions card shows every kind, not just two",
      money(page._kpis["total_deductions"].text()) == 5300.0,
      page._kpis["total_deductions"].text())

# ── the page reports, it does not compute ───────────────────────────────
#
# Fed totals whose parts do not reconcile, the page must show exactly what it
# was given. Quietly fixing the arithmetic here would hide a server bug that
# the payslips would still carry.
broken = {**SUMMARY, "totals": {**SUMMARY["totals"], "net": 99999.0}}
page._fill(broken)
check("a total that disagrees with its parts is shown, not corrected",
      money(page._net_total.text()) == 99999.0, page._net_total.text())
page._fill(SUMMARY)

# ── the four lists ──────────────────────────────────────────────────────
check("everyone who lost pay to unpaid leave is listed",
      page._tables["leave_deductions"].rowCount() == 1)
check("everyone charged for lateness, with the minutes",
      page._tables["lateness"].rowCount() == 1
      and "90" in page._tables["lateness"].item(0, 2).text().replace("1h 30m", "90"),
      page._tables["lateness"].item(0, 2).text() if
      page._tables["lateness"].rowCount() else "no rows")
check("every entered deduction, NAMED — 'PF', not just an amount",
      page._tables["deductions"].rowCount() == 1
      and "PF" in page._tables["deductions"].item(0, 1).text(),
      page._tables["deductions"].item(0, 1).text() if
      page._tables["deductions"].rowCount() else "no rows")
check("and everyone paid for overtime",
      page._tables["overtime"].rowCount() == 1)

# ── a month where nothing happened says so ──────────────────────────────
#
# Rather than four blank boxes down the page, which read as "this did not
# load". The dash is also the truthful answer: no rows means nobody was
# charged, not that the figure is unknown.
quiet = {**SUMMARY, "leave_deductions": [], "lateness": [],
         "deductions": [], "overtime": []}
page._fill(quiet)
check("a list with nobody on it shows a dash, not an empty box",
      all(page._tables[k].rowCount() == 1
          and page._tables[k].item(0, 0).text() == "—"
          for k in ("leave_deductions", "lateness", "deductions", "overtime")),
      ", ".join(f"{k}={page._tables[k].rowCount()}rows"
                for k in ("leave_deductions", "lateness", "deductions", "overtime")))

# ± ON A NEGATIVE FIGURE READ AS TWO SIGNS ARGUING: "±₹-200.00". An
# adjustment is the one row that can go either way, so its direction comes
# from the number rather than from the label.
check("a negative adjustment reads as a subtraction, not '±₹-200.00'",
      page._lines_ui["adjustments"].text() == "−₹200.00",
      page._lines_ui["adjustments"].text())
page._fill({**SUMMARY, "totals": {**SUMMARY["totals"], "adjustments": 750.0}})
check("and a positive one reads as an addition",
      page._lines_ui["adjustments"].text() == "+₹750.00",
      page._lines_ui["adjustments"].text())
page._fill(SUMMARY)

# ── a second month does not inherit the first one's figures ─────────────
#
# Left as it was, a slow request shows last month's totals under this month's
# heading, which is a wrong answer rather than a missing one.
page.load("2026-07")
check("loading another month clears the old figures first",
      page._net_total.text() == "—"
      and all(v.text() == "—" for v in page._kpis.values())
      and page._tables["overtime"].rowCount() == 0,
      f"net={page._net_total.text()}, "
      f"overtime rows={page._tables['overtime'].rowCount()}")

# ── a month that cannot be read says so ─────────────────────────────────
page._fill({"success": False, "message": "That month has not been generated."})
# isHidden(), NOT isVisible(). Nothing here is shown on screen, so isVisible()
# is False for every widget on the page and the check would pass whether the
# notice had been revealed or not — a test that cannot fail.
check("a month that was never generated explains itself",
      not page._notice.isHidden() and "not been generated" in page._notice.text(),
      f"hidden={page._notice.isHidden()} text={page._notice.text()!r}")
check("and shows no figures beside the explanation",
      page._net_total.text() == "—", page._net_total.text())

# ── the button on the payroll tab opens the page, not a modal ───────────
tab = panel._PayrollTab.__new__(panel._PayrollTab)
panel.QWidget.__init__(tab)
tab._workers = []
tab._month = "2026-06"
tab._lines = []
tab._status = "NONE"
tab._selected_employee = None
tab._history = []
tab._build_ui()

check("the payroll tab has no summary dialog left on it",
      not hasattr(tab, "_summary_dialog"))

asked: list = []
tab.open_summary.connect(asked.append)
tab._month_box.setText("2026-06")
tab._open_summary()
check("pressing Summary asks the panel to open the month",
      asked == ["2026-06"], repr(asked))

# WITH NO MONTH TYPED IT DOES NOTHING, rather than opening a page about "".
asked.clear()
tab._month_box.setText("   ")
tab._open_summary()
check("and with no month typed it opens nothing", asked == [], repr(asked))

print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILED'}\n")

# os._exit, for the reason test_theme and test_payroll_tab both record.
sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if failures else 0)
