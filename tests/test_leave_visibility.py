"""
Leave: the reason and the decision's remarks are READ, not hovered for.

WHAT WAS REPORTED, in the owner's words:

  * "admin ko reason tabhi dikhta hai jab wo approve par click karta hai, jo ki
    galat hai — by default reason show hona chahiye, uske basis pe approve ya
    reject hoga";
  * "employee panel me leave reject karne pe reason kahan show hoga?";
  * "HR reject ya approve kare, dono me employee ko remarks dikhne chahiye".

NOTHING WAS MISSING FROM THE DATA. The server has always stored the
employee's reason and the decision's remarks, sent both to both panels, and
emailed the remarks. Every piece of it was on screen — in the hover text of
the Status chip, which is a place nobody looks. So the fault was invisible to
anybody reading the code and total for anybody using the product, and there
was no test of either leave page to notice.

These checks are about what is DRAWN, cell by cell, because "the data is in
the row" was always true and never enough.

Run:  python3 tests/test_leave_visibility.py
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


from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget  # noqa: E402

app = QApplication.instance() or QApplication([])

from client.presentation.windows import admin_config_panel as panel      # noqa: E402

app.setStyleSheet(panel._global_stylesheet())


class _Dead:
    def __init__(self, *a, **k):
        pass
    def __getattr__(self, _n):
        return type("_S", (), {"connect": lambda *a, **k: None})()
    def start(self):
        pass


posted: list = []


class _CapturePost(_Dead):
    def __init__(self, url, body=None, *a, **k):
        posted.append((url, body))


real = (panel._FetchWorker, panel._PostWorker, panel._track_worker,
        panel.QInputDialog.getText, panel.QMessageBox.question)
# The approval used to be a Yes/No box rather than a prompt. Answered here so
# that, run against that older code, this test FAILS rather than waiting for
# a click that never comes.
panel.QMessageBox.question = staticmethod(
    lambda *a, **k: panel.QMessageBox.StandardButton.Yes)
panel._FetchWorker = _Dead
panel._PostWorker = _CapturePost
panel._track_worker = lambda *a, **k: None

ROWS = [
    {"id": 7, "employee_id": "26AMZEM002", "employee_name": "Shailabh",
     "leave_type": "SICK", "start_date": "2026-09-04", "end_date": "2026-09-04",
     "total_days": 1, "status": "PENDING", "reason": "bimaar hu", "remarks": None},
    {"id": 6, "employee_id": "26AMZEM001", "employee_name": "Ansh",
     "leave_type": "CASUAL", "start_date": "2026-08-20", "end_date": "2026-08-21",
     "total_days": 2, "status": "REJECTED", "reason": "family function",
     "remarks": "Release week — please move it to next month"},
]


def text_at(table, row, column):
    if column < 0:
        return ""
    item = table.item(row, column)
    if item is not None:
        return item.text()
    widget = table.cellWidget(row, column)
    if widget is None:
        return ""
    labels = [w.text() for w in widget.findChildren(QLabel) if w.text()]
    return " ".join(labels)


print("\nThe admin sees why, before deciding\n")

try:
    tab = panel._LeaveTab()
    heads = [tab._table.horizontalHeaderItem(c).text()
             for c in range(tab._table.columnCount())]
    check("the table has a Reason column", "Reason" in heads, str(heads))
    check("and a Remarks column", "Remarks" in heads, str(heads))

    tab._populate({"data": ROWS, "total": 2, "pending": 1})
    col = lambda name: heads.index(name) if name in heads else -1  # noqa: E731
    reason_col, remarks_col = col("Reason"), col("Remarks")
    status_col = col("Status")

    check("the employee's reason is written in the row, not in a tooltip",
          text_at(tab._table, 0, reason_col) == "bimaar hu",
          repr(text_at(tab._table, 0, reason_col)))
    check("the decision's remarks too",
          "Release week" in text_at(tab._table, 1, remarks_col),
          repr(text_at(tab._table, 1, remarks_col)))
    check("a request not yet decided shows a dash for remarks, not a blank",
          text_at(tab._table, 0, remarks_col) == "—",
          repr(text_at(tab._table, 0, remarks_col)))
    check("the status chip is still in its own column",
          text_at(tab._table, 0, status_col) == "Pending"
          and text_at(tab._table, 1, status_col) == "Rejected",
          f"{text_at(tab._table, 0, status_col)!r} / "
          f"{text_at(tab._table, 1, status_col)!r}")
    check("and the Approve button is still on the pending row",
          any(b.text() == "Approve" for b in
              tab._table.cellWidget(0, col("Actions"))
              .findChildren(QPushButton)))

    # ── the decision itself ─────────────────────────────────────────────
    shown: list = []

    def answer(text):
        def fake(_parent, _title, label, *a, **k):
            shown.append(label)
            return text, True
        return fake

    # REJECT: their reason is in the prompt, because this is the moment it is
    # being weighed. It used to ask "why are you rejecting?" and show nothing.
    posted.clear()
    panel.QInputDialog.getText = staticmethod(answer("Clashes with the release"))
    tab._decide(ROWS[0], "reject")
    check("rejecting shows the employee's reason while asking why",
          shown and "bimaar hu" in shown[-1], shown[-1] if shown else "no prompt")
    check("and sends the remarks typed", posted
          and posted[-1][1].get("remarks") == "Clashes with the release",
          str(posted[-1][1]) if posted else "nothing sent")

    # APPROVE: a note can go with it. It always sent "" before.
    posted.clear()
    shown.clear()
    panel.QInputDialog.getText = staticmethod(answer("Approved — hand over first"))
    tab._decide(ROWS[0], "approve")
    check("approving shows their reason too", shown and "bimaar hu" in shown[-1])
    check("and an approval carries its note to the employee",
          posted and posted[-1][1].get("remarks") == "Approved — hand over first",
          str(posted[-1][1]) if posted else "nothing sent")
    check("to the approve endpoint", posted
          and posted[-1][0].endswith("/admin/leave/7/approve"),
          posted[-1][0] if posted else "nothing sent")

    # A note stays optional on an approval…
    posted.clear()
    panel.QInputDialog.getText = staticmethod(answer(""))
    tab._decide(ROWS[0], "approve")
    check("an approval without a note still goes through", len(posted) == 1,
          str(posted))

    # …and is not optional on a rejection.
    posted.clear()
    tab._decide(ROWS[0], "reject")
    check("a rejection with no remarks is not sent", posted == [], str(posted))

    tab.deleteLater()
finally:
    (panel._FetchWorker, panel._PostWorker, panel._track_worker) = real[:3]
    panel.QInputDialog.getText = real[3]
    panel.QMessageBox.question = real[4]

print("\nThe employee sees what they were told\n")

from client.presentation import theme as _theme                        # noqa: E402
from client.presentation.windows import leave_page                     # noqa: E402

app.setStyleSheet(_theme.app_style())


class _Panel:
    def go(self, *a, **k): pass
    def mark_server(self, *a, **k): pass
    def shift_text(self, *a, **k): return ""


real_run = leave_page.LeavePage._run
leave_page.LeavePage._run = lambda self, *a, **k: None     # no network
try:
    page = leave_page.LeavePage(_Panel())
    heads = [page._table.horizontalHeaderItem(c).text()
             for c in range(page._table.columnCount())]
    check("the history shows the employee's own reason",
          "Your reason" in heads, str(heads))
    check("and the remarks they were given", "Remarks" in heads, str(heads))

    page._fill([
        {"id": 7, "leave_type": "SICK", "start_date": "2026-09-04",
         "end_date": "2026-09-04", "total_days": 1, "status": "PENDING",
         "reason": "bimaar hu", "remarks": None},
        {"id": 6, "leave_type": "CASUAL", "start_date": "2026-08-20",
         "end_date": "2026-08-21", "total_days": 2, "status": "REJECTED",
         "reason": "family function",
         "remarks": "Release week — please move it to next month",
         "approved_by_name": "Ansh"},
    ])
    col = lambda name: heads.index(name) if name in heads else -1  # noqa: E731
    remarks_col, status_col = col("Remarks"), col("Status")
    last = page._table.columnCount() - 1

    # THE ONE THAT WAS REPORTED: a rejection, and why, on the row.
    check("a rejected request says why, without hovering",
          "Release week" in text_at(page._table, 1, remarks_col),
          repr(text_at(page._table, 1, remarks_col)))
    check("and who said it", "Ansh" in text_at(page._table, 1, remarks_col),
          repr(text_at(page._table, 1, remarks_col)))
    check("a pending request says it is waiting, not a bare dash",
          "Awaiting" in text_at(page._table, 0, remarks_col),
          repr(text_at(page._table, 0, remarks_col)))
    check("their own reason is shown beside it",
          text_at(page._table, 0, col("Your reason")) == "bimaar hu")

    # THE STATUS CHIP SURVIVES THE CANCEL BUTTON. Cancel was written into the
    # status column's index, which would have replaced the chip.
    check("the status chip is not replaced by the Cancel button",
          text_at(page._table, 0, status_col) == "Pending"
          and text_at(page._table, 1, status_col) == "Rejected",
          f"{text_at(page._table, 0, status_col)!r} / "
          f"{text_at(page._table, 1, status_col)!r}")
    check("Cancel is offered on the pending request",
          isinstance(page._table.cellWidget(0, last), QPushButton)
          and page._table.cellWidget(0, last).text() == "Cancel")
    # An empty QWidget in a cell paints near-black: the dark blank box after
    # every decided row, reported with this table.
    check("and a decided request has no blank widget where Cancel would be",
          page._table.cellWidget(1, last) is None,
          type(page._table.cellWidget(1, last)).__name__)

    # A ROW THAT WAS PENDING AND IS NOW DECIDED loses its Cancel. The table is
    # refilled in place, so a widget left over is drawn over the new row.
    page._fill([dict(ROWS[1], status="REJECTED",
                     remarks="Release week", approved_by_name="Ansh")])
    check("refilling removes a Cancel left over from before",
          page._table.cellWidget(0, last) is None,
          type(page._table.cellWidget(0, last)).__name__)
    page.deleteLater()
finally:
    leave_page.LeavePage._run = real_run

app.processEvents()
print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILED'}\n")

sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if failures else 0)
