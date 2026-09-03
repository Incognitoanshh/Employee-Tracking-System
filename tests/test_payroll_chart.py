"""
The payroll cost chart: an axis somebody can read a value off.

WHAT WAS WRONG WITH IT, and both faults were in the same two lines.

THE SCALE. The top of the chart was the tallest bar plus fifteen per cent, and
the gridlines were quarters of that. A year peaking at ₹6.70L was therefore
labelled 1.93L / 3.85L / 5.78L / 7.70L. Every one of those is a true number
and not one of them is a number anybody thinks in, which defeats the only
thing an axis is for — reading a bar's height without hovering it.

THE EMPTY YEAR. There is an empty state, and it could never be reached:
set_months fills the whole financial year with placeholder months on purpose,
so the months that have not happened yet are visible as months that have not
happened yet — which means `if not self._months` was never true. A year with
no payroll drew twelve empty slots under an axis scaled to a peak of 1, with
gridlines labelled 1, 1, 1, 0, 0.

Run:  python3 tests/test_payroll_chart.py
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


from PySide6.QtWidgets import QApplication                            # noqa: E402

app = QApplication.instance() or QApplication([])

from client.presentation.windows import admin_config_panel as panel   # noqa: E402

app.setStyleSheet(panel._global_stylesheet())

Chart = panel._PayrollCostChart

print("\nThe payroll cost chart\n")


def row(month, gross, overtime, deductions, status="FINALIZED"):
    """The shape /admin/payroll returns.

    net + deductions == gross + overtime is the identity the stacked bar is
    drawn on, so net is derived here rather than written down separately.
    """
    return {"month": month, "status": status, "employees": 12,
            "gross": gross, "overtime": overtime, "deductions": deductions,
            "net_before_adjustments": gross + overtime - deductions,
            "adjustments_total": 0,
            "total_payout": gross + overtime - deductions,
            "payroll_cost": gross + overtime}


# ── the scale ───────────────────────────────────────────────────────────
#
# ROUND STEPS, whatever the magnitude — this chart is read at thousands for a
# small company and at tens of lakhs for a larger one.
NICE = {1, 1.5, 2, 2.5, 5, 10}


def step_is_round(step: float) -> bool:
    """A step is round when it is 1, 1.5, 2, 2.5 or 5 times a power of ten."""
    import math
    if step <= 0:
        return False
    power = 10 ** math.floor(math.log10(step))
    return any(abs(step - m * power) < step * 1e-9 for m in NICE)


for peak in (950, 12_345, 95_000, 480_000, 670_000, 2_400_000, 41_500_000):
    ceiling = Chart._nice_ceiling(peak)
    check(f"a peak of {peak:,} gets a round step",
          step_is_round(ceiling / 4), f"ceiling {ceiling:,} -> step {ceiling / 4:,}")
    # THE CEILING MUST CLEAR THE PEAK, or the tallest bar is drawn past the
    # top of the plot and the chart reports a figure it has not got room for.
    check(f"  …and a ceiling above it", ceiling >= peak,
          f"{ceiling:,} < {peak:,}")
    # AND NOT SO FAR ABOVE IT that a busy year looks like a quiet one. Four
    # times the step is the whole plot; the tallest bar should use most of it.
    check(f"  …without wasting the plot", peak / ceiling >= 0.5,
          f"tallest bar fills {peak / ceiling:.0%} of the height")

check("a peak of zero does not divide by zero",
      Chart._nice_ceiling(0) > 0, str(Chart._nice_ceiling(0)))

# ── a year with runs ────────────────────────────────────────────────────
chart = Chart()
chart.resize(900, 280)
chart.set_months([row("2026-04", 400000, 25000, 41000),
                  row("2026-07", 640000, 30000, 58000)], "this")

check("the whole financial year is on the axis, run or not",
      len(chart._months) == 12, str(len(chart._months)))
check("April leads it — the year is April to March",
      chart._months[0]["month"].endswith("-04"), chart._months[0]["month"])
check("a year with runs draws the bars",
      chart._has_any_payroll())

area, peak, bars = chart._geometry()
check("the scale clears the tallest month",
      peak >= 670000, f"peak {peak:,}")
check("and its quarter is a round number",
      step_is_round(peak / 4), f"step {peak / 4:,}")
check("there is a bar slot for every month",
      len(bars) == 12, str(len(bars)))

# ── a year with none ────────────────────────────────────────────────────
#
# THE STATE THAT COULD NOT BE REACHED. _months is full of placeholders, so
# emptiness has to be asked about the payroll, not about the list.
blank = Chart()
blank.resize(900, 280)
blank.set_months([], "this")
check("a year with no runs still lays out twelve months",
      len(blank._months) == 12, str(len(blank._months)))
check("but knows there is no payroll in it",
      not blank._has_any_payroll())

# A month present but never generated is not payroll either — a run row with
# nothing on it must not scale an axis.
nothing = Chart()
nothing.set_months([{"month": "2026-04", "status": "DRAFT", "payroll_cost": 0}],
                   "this")
check("nor is a month whose run came to nothing",
      not nothing._has_any_payroll())

# AND paintEvent HAS TO ASK. Checking _has_any_payroll() on its own only
# proves the method is right — the fault being guarded was that the drawing
# code consulted something else (`if not self._months`, which is never true).
# So the empty year is drawn twice, once with the answer forced the other way:
# if the two renders are identical, paintEvent is not reading it.
blank.resize(900, 280)
blank.show()
app.processEvents()
as_empty = blank.grab().toImage()
blank._has_any_payroll = lambda: True          # pretend there is payroll
app.processEvents()
as_full = blank.grab().toImage()
del blank._has_any_payroll
check("the drawing consults it, rather than the length of the month list",
      as_empty != as_full,
      "an empty year renders the same either way — paintEvent is not asking")
blank.hide()

# ── the identity the stacked bar rests on ───────────────────────────────
#
# net + deductions == gross + overtime. The two segments are drawn touching,
# and they can only touch without a rounding gap because both figures are the
# server's frozen ones rather than two calculations that happen to be close.
sample = row("2026-07", 640000, 30000, 58000)
check("net plus deductions is exactly the payroll cost",
      sample["net_before_adjustments"] + sample["deductions"]
      == sample["payroll_cost"],
      f"{sample['net_before_adjustments']} + {sample['deductions']} "
      f"vs {sample['payroll_cost']}")

# ── it draws without raising ────────────────────────────────────────────
#
# paintEvent is where every one of these numbers is actually used, and a
# chart that raises inside it leaves a blank frame and no error anybody sees.
for label, target in (("a year with runs", chart), ("an empty year", blank)):
    try:
        target.show()
        app.processEvents()
        target.grab()
        drew = True
        why = ""
    except Exception as error:                       # noqa: BLE001
        drew, why = False, repr(error)
    check(f"{label} paints without raising", drew, why)
    target.hide()

print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILED'}\n")

# os._exit, for the reason test_theme and test_payroll_tab both record.
sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if failures else 0)
