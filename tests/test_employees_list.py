"""
The Employees list, and the page a name opens.

WHAT THIS LIST USED TO BE. Six columns — Employee ID, Name, Role, Status, Last
Seen, Actions — with the id and the name each taking a column of their own and
nowhere at all to put the job title, the work email or the department. So "who
are the QA people", "what is Asha's work address" and "who is in Design" could
not be answered by looking at the list of employees; they were answered by
opening people one at a time, in a modal.

THE FILTERS ARE THE PART MOST EASILY GOT WRONG, and the checks below spend
most of their effort there. A filter applied to the fifty rows the panel is
already holding answers "which of these fifty are in Design" — a different
question, with a different answer, under a total that still reads as the whole
company. So what is tested is not that filtering *works* but that it is SENT:
the parameters go to the server, and the page resets when they change.

AND THE PAGE HAS TIMERS. As a modal it ran a one-second clock and a ten-second
refetch, and that was safe because closing the window destroyed the object. In
the stack it lives for the whole session, so the same two timers would poll
for somebody nobody is looking at until the app is quit.

Run:  python3 tests/test_employees_list.py
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


from PySide6.QtWidgets import QApplication, QDialog, QLabel                 # noqa: E402

app = QApplication.instance() or QApplication([])

from client.presentation.windows import admin_config_panel as panel         # noqa: E402

app.setStyleSheet(panel._global_stylesheet())

asked: list = []


class _CaptureFetch:
    """Records what the list asks the server for, and answers nothing."""

    def __init__(self, url, params=None, *a, **k):
        asked.append((url, dict(params or {})))

    def __getattr__(self, _name):
        return type("_Sig", (), {"connect": lambda *_a, **_k: None})()

    def start(self):
        pass


real_fetch, real_track = panel._FetchWorker, panel._track_worker
panel._FetchWorker = _CaptureFetch
panel._track_worker = lambda *a, **k: None

PEOPLE = [
    {"employee_id": "26AMZEM001", "username": "asha", "full_name": "Asha Verma",
     "role": "employee", "designation": "QA Engineer",
     "email": "asha@amazeinternet.com", "personal_email": "asha.v@gmail.com",
     "department": "Engineering",
     "status": "online", "last_seen": None, "suspended": False},
    {"employee_id": "26AMZEM002", "username": "bilal", "full_name": "Bilal Khan",
     "role": "admin", "designation": "Designer", "email": None,
     "department": None, "status": "offline",
     "last_seen": "2026-08-24 06:00:00", "suspended": True},
    # No login at all — the contractor a payroll-only record produces.
    {"employee_id": "C001", "username": None, "full_name": "Meera Contractor",
     "role": "employee", "designation": None, "email": None,
     "department": "Design", "status": "offline", "last_seen": None,
     "suspended": False},
]

PAYLOAD = {"data": PEOPLE, "total": 3,
           "departments": ["Design", "Engineering"],
           "role_counts": {}, "role_limits": {}}


def _row_text(page, key):
    """A missing row reports as a failure, not a KeyError that ends the run."""
    row = page._profile_rows.get(key)
    return row.text() if row is not None else f"<no {key} row>"


def build_tab():
    tab = panel._EmployeesTab.__new__(panel._EmployeesTab)
    panel.QWidget.__init__(tab)
    tab._workers = []
    tab._rows = []
    tab._search_text = ""
    tab._page = 1
    tab._total = 0
    tab._build_ui()
    return tab


print("\nThe Employees list\n")

try:
    tab = build_tab()

    columns = [tab._table.horizontalHeaderItem(i).text()
               for i in range(tab._table.columnCount())]
    # The list says which address it shows — a person now has two.
    check("the columns are the person, their official email, department, role and state",
          columns == ["Employee", "Official email", "Department", "Role",
                      "Status", "Actions"], str(columns))

    asked.clear()
    tab._on_employees_loaded(PAYLOAD)
    check("every person on the page has a row",
          tab._table.rowCount() == 3, str(tab._table.rowCount()))

    # ── the person cell ─────────────────────────────────────────────────
    cell = tab._table.cellWidget(0, 0)
    texts = [l.text() for l in cell.findChildren(QLabel) if l.text()]
    check("the name leads, with the id and job title under it",
          "Asha Verma" in texts
          and any("26AMZEM001" in t and "QA Engineer" in t for t in texts),
          str(texts))
    check("and an avatar beside it",
          any(isinstance(w, panel.Avatar) for w in cell.findChildren(panel.QWidget)),
          "no Avatar in the cell")

    # SOMEBODY WITH NO JOB TITLE STILL HAS AN ID. The line under the name is
    # built from what is there, not from a template with a gap in it.
    third = [l.text() for l in tab._table.cellWidget(2, 0).findChildren(QLabel)
             if l.text()]
    check("a person with no designation shows their id alone, with no stray dot",
          any(t.strip() == "C001" for t in third), str(third))

    # ── THE IDENTITY COLUMN IS THE LAST THING THAT MAY GIVE WAY ─────────
    #
    # It was the Stretch column, and a Stretch column is the one that absorbs
    # whatever is left over — so on a window narrower than the fixed columns
    # add up to, the person column took the entire shortfall. Measured at
    # 112px against the 197px the avatar, the name and "id · designation"
    # need: the names came out shredded around the avatar and the header read
    # "EMPLO'". Work email stretches instead, because a clipped address is
    # still recognisable and a clipped name is not.
    narrow = []
    for width in (1600, 1250, 1080, 900):
        tab.resize(width, 420)
        tab.show()
        app.processEvents()
        cell = tab._table.cellWidget(0, 0)
        if tab._table.columnWidth(0) < cell.sizeHint().width():
            narrow.append(f"{width}px window -> column "
                          f"{tab._table.columnWidth(0)} < needed "
                          f"{cell.sizeHint().width()}")
    check("the person column never shrinks below what it has to draw",
          not narrow, "; ".join(narrow))
    check("and the email column is the one that gives way",
          tab._table.horizontalHeader().sectionResizeMode(1)
          == panel.QHeaderView.ResizeMode.Stretch,
          str(tab._table.horizontalHeader().sectionResizeMode(1)))

    # ── clicking the name ───────────────────────────────────────────────
    opened: list = []
    tab.open_employee.connect(opened.append)
    name = next(l for l in cell.findChildren(panel._NameLink))
    name.clicked.emit()
    check("clicking a name asks the panel to open that person",
          len(opened) == 1 and opened[0].get("employee_id") == "26AMZEM001",
          str([o.get("employee_id") for o in opened]))

    opened.clear()
    tab._open_row(1, 0)
    check("and so does double-clicking their row",
          len(opened) == 1 and opened[0].get("employee_id") == "26AMZEM002",
          str([o.get("employee_id") for o in opened]))

    # ── nothing recorded is shown as nothing, not as blank ──────────────
    check("a missing work email is a dash",
          tab._table.item(1, 1).text() == "—", tab._table.item(1, 1).text())
    check("and a missing department too",
          tab._table.item(1, 2).text() == "—", tab._table.item(1, 2).text())
    check("the ones that are recorded are shown",
          tab._table.item(0, 1).text() == "asha@amazeinternet.com"
          and tab._table.item(0, 2).text() == "Engineering",
          f"{tab._table.item(0, 1).text()} / {tab._table.item(0, 2).text()}")

    # ── the chip ────────────────────────────────────────────────────────
    def chip_text(row):
        widget = tab._table.cellWidget(row, 4)
        return next((l.text() for l in widget.findChildren(QLabel) if l.text()), "")

    check("somebody signed in reads as online", chip_text(0) == "Online",
          chip_text(0))
    # A SUSPENDED ACCOUNT CANNOT SIGN IN, so "Offline" is true of it and is
    # not the fact anybody is scanning this column for.
    check("a suspended account reads as suspended, not offline",
          chip_text(1) == "Suspended", chip_text(1))
    check("and the colours for these come from the theme, not from the page",
          panel._theme.status_colors("suspended")
          != panel._theme.status_colors("neutral"),
          "suspended falls through to neutral grey")

    # ── the filters go to the server ────────────────────────────────────
    #
    # THE WHOLE POINT. Narrowing the rows already on screen answers a
    # different question and leaves the total reading the whole company.
    asked.clear()
    tab._role_filter.setCurrentIndex(tab._role_filter.findData("admin"))
    check("choosing a role asks the server for that role",
          asked and asked[-1][1].get("role") == "admin",
          str(asked[-1][1]) if asked else "nothing asked")
    check("and goes back to the first page",
          asked[-1][1].get("page") == 1, str(asked[-1][1]))

    asked.clear()
    tab._status_filter.setCurrentIndex(tab._status_filter.findData("suspended"))
    check("so does a status", asked and asked[-1][1].get("status") == "suspended",
          str(asked[-1][1]) if asked else "nothing asked")
    check("and the role it was already narrowed to is still sent",
          asked[-1][1].get("role") == "admin", str(asked[-1][1]))

    asked.clear()
    tab._dept_filter.setCurrentIndex(tab._dept_filter.findData("Design"))
    check("and a department", asked and asked[-1][1].get("department") == "Design",
          str(asked[-1][1]) if asked else "nothing asked")

    # ── the departments offered are the company's ───────────────────────
    check("the filter lists every department the server knows",
          [tab._dept_filter.itemData(i)
           for i in range(tab._dept_filter.count())] == ["", "Design", "Engineering"],
          str([tab._dept_filter.itemText(i)
               for i in range(tab._dept_filter.count())]))

    # A REFRESH MUST NOT WIDEN THE FILTER UNDER SOMEBODY. The list reloads on
    # a thirty-second timer; rebuilding the box each time would reset the
    # choice and quietly show everybody again.
    asked.clear()
    tab._on_employees_loaded(PAYLOAD)
    check("a refresh keeps the department that was chosen",
          tab._dept_filter.currentData() == "Design",
          str(tab._dept_filter.currentData()))
    check("and does not fire a second request on its own",
          asked == [], str(asked))

    # ── clearing ────────────────────────────────────────────────────────
    asked.clear()
    tab._reset_filters()
    check("Clear puts every filter back",
          tab._role_filter.currentData() == ""
          and tab._dept_filter.currentData() == ""
          and tab._status_filter.currentData() == "",
          f"{tab._role_filter.currentData()}/{tab._dept_filter.currentData()}"
          f"/{tab._status_filter.currentData()}")
    check("in ONE request, not one per filter", len(asked) == 1, str(len(asked)))
    check("and that request carries no filters at all",
          not any(k in asked[0][1] for k in ("role", "department", "status", "search")),
          str(asked[0][1]))

    # ── how many per page ───────────────────────────────────────────────
    asked.clear()
    tab._per_page.setCurrentIndex(tab._per_page.findData(100))
    check("changing the page size asks for that many",
          asked and asked[-1][1].get("limit") == 100,
          str(asked[-1][1]) if asked else "nothing asked")
    # Showing a hundred while standing on page four of a fifty-per-page list
    # lands past the end, and an empty table reads as "there is nobody".
    check("and starts again at the first page",
          asked[-1][1].get("page") == 1, str(asked[-1][1]))

    tab._total = 3
    tab._on_employees_loaded(PAYLOAD)
    check("the pager says where you are and how many there are",
          "Page 1 of 1" in tab._page_label.text()
          and "3 people" in tab._page_label.text(), tab._page_label.text())

    # ── the export carries what the page shows ──────────────────────────
    exported: list = []
    real_export = panel._export_to_csv
    panel._export_to_csv = lambda path, headers, rows: (
        exported.append((headers, rows)) or True)
    real_save = panel.QFileDialog.getSaveFileName
    panel.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("/tmp/x.csv", ""))
    real_info = panel.QMessageBox.information
    panel.QMessageBox.information = staticmethod(lambda *a, **k: None)
    try:
        tab._export_employees_csv()
        headers, rows = exported[0]
        check("the CSV carries both emails and the department",
              "Official email" in headers and "Personal email" in headers
              and "Department" in headers, str(headers))
        # A header without its value slides every later column one to the
        # left — the department would sit under "Personal email".
        check("and each row is as long as the header",
              all(len(r) == len(headers) for r in rows),
              str([len(r) for r in rows]) + " vs " + str(len(headers)))
        check("with each address under its own heading",
              rows[0][headers.index("Official email")] == "asha@amazeinternet.com"
              and rows[0][headers.index("Personal email")] == "asha.v@gmail.com"
              and rows[0][headers.index("Department")] == "Engineering",
              str(rows[0]))
        check("and a suspended account exports as suspended",
              rows[1][headers.index("Status")] == "Suspended",
              str(rows[1]))
    finally:
        panel._export_to_csv = real_export
        panel.QFileDialog.getSaveFileName = real_save
        panel.QMessageBox.information = real_info

    # ── the page a name opens ───────────────────────────────────────────
    print("\nThe employee page")
    page = panel.EmployeePage()
    check("it is a page, not a dialog",
          isinstance(page, panel.QWidget) and not isinstance(page, QDialog))
    check("and it can be left the way it was reached", hasattr(page, "back"))

    # NOT RUNNING BEFORE IT IS OPENED. It is built with the console at
    # start-up; a page nobody has looked at must not be polling the server.
    check("its timers are stopped until it is shown",
          not page._live_timer.isActive()
          and not page._details_refresh_timer.isActive())

    page.load(PEOPLE[0])
    check("it shows the person it was given",
          page._title.text() == "Asha Verma", page._title.text())
    check("with their id and login under the name",
          "26AMZEM001" in page._sub.text() and "asha" in page._sub.text(),
          page._sub.text())
    check("and what the company records about them",
          page._profile_rows["department"].text() == "Engineering"
          and page._profile_rows["email"].text() == "asha@amazeinternet.com",
          f"{page._profile_rows['department'].text()} / "
          f"{page._profile_rows['email'].text()}")

    check("and the personal email beside the official one",
          _row_text(page, "personal_email") == "asha.v@gmail.com",
          _row_text(page, "personal_email"))

    # SOMEBODY WITH NO LOGIN IS NOT "None". The payroll-only record has a
    # NULL username, and str(None) on a screen is a bug people report.
    page.load(PEOPLE[2])
    check("a person with no login says so, rather than showing None",
          "None" not in page._sub.text(), page._sub.text())
    check("and no personal email on record is a dash, not a made-up value",
          _row_text(page, "personal_email") == "—",
          _row_text(page, "personal_email"))

    # A NEW PERSON STARTS AT ZERO. These count up on the one-second timer,
    # and carrying them over shows the last employee's minutes under this
    # one's name.
    page._live_active_seconds = 4242
    page.load(PEOPLE[0])
    check("opening somebody else resets the running clock",
          page._live_active_seconds == 0, str(page._live_active_seconds))

    page.show()
    app.processEvents()
    check("showing it starts the clock and the refresh",
          page._live_timer.isActive() and page._details_refresh_timer.isActive())
    page.hide()
    app.processEvents()
    check("and leaving it stops both — nobody is looking",
          not page._live_timer.isActive()
          and not page._details_refresh_timer.isActive())

    # The panel's shutdown reaches it by name, and by a stop() it can call.
    check("the page is in the console's shutdown list",
          "_employee_page" in panel.AdminConfigPanel.TAB_ATTRS,
          str(panel.AdminConfigPanel.TAB_ATTRS))
    check("and it knows how to stop itself", callable(getattr(page, "stop", None)))
    page._live_timer.start()
    page.stop()
    check("stop() halts the timers a hidden page would have left running",
          not page._live_timer.isActive())

    check("nothing builds the old modal any more",
          not hasattr(panel, "EmployeeDetailsDialog"))

    tab.deleteLater()
    page.deleteLater()
    app.processEvents()
finally:
    panel._FetchWorker, panel._track_worker = real_fetch, real_track

print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILED'}\n")

# os._exit, for the reason test_theme and test_payroll_tab both record.
sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if failures else 0)
