"""
Hiring somebody: four steps on a page, and every mistake shown at once.

WHAT THIS REPLACES. A 380-pixel dialog with six fields, which validated ONE AT
A TIME through message boxes: a name, then an id, then a password, each
discovered only after fixing the last. Everything a payroll actually needs —
the joining date that decides somebody's first month, the PAN that goes on the
filing, the bank details the money leaves by — had to be added afterwards from
three other screens, if anyone remembered.

So the check that matters most here is not that a field exists. It is that a
form with FOUR things wrong reports four things, not the first one. That is
the difference between one pass and four round trips, and it is the behaviour
that regresses the moment somebody adds a `return` to the validator.

THE OTHER HALF IS PORTAL ACCESS. An employee can be on the payroll without
being able to sign in — a contractor is paid and never runs the client. With
it off there is no username and no password in what gets sent, and the server
stores NULL in both. server/tests/test_employee_onboarding.js proves the
consequence: that such an employee cannot sign in, by any spelling, while
everybody else still can.

Run:  python3 tests/test_add_employee_page.py
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


from PySide6.QtCore import QDate                                    # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog                 # noqa: E402

app = QApplication.instance() or QApplication([])

from client.presentation.windows import admin_config_panel as panel  # noqa: E402

app.setStyleSheet(panel._global_stylesheet())


# Nothing here should reach the network. The page asks for a suggested id the
# moment it is built, and posts on save; both are captured instead.
class _Dead:
    def __init__(self, *a, **k):
        self.sent = a
    def __getattr__(self, _name):
        return type("_Sig", (), {"connect": lambda *_a, **_k: None,
                                 "emit": lambda *_a, **_k: None})()
    def start(self):
        pass


posted: list = []


class _CapturePost(_Dead):
    def __init__(self, url, payload=None, *a, **k):
        super().__init__(url, payload)
        posted.append((url, payload))


real_fetch, real_post, real_track = (
    panel._FetchWorker, panel._PostWorker, panel._track_worker)
panel._FetchWorker = _Dead
panel._PostWorker = _CapturePost
panel._track_worker = lambda *a, **k: None

print("\nAdding an employee\n")

try:
    page = panel._AddEmployeePage()

    check("it is a page, not a dialog",
          isinstance(page, panel.QWidget) and not isinstance(page, QDialog))
    check("with four steps, in the order they are asked",
          page.STEPS == ("Basic", "Salary", "Personal", "Payment"),
          str(page.STEPS))
    check("and a step for each one",
          page._stack.count() == 4, str(page._stack.count()))

    # ── EVERY MISTAKE AT ONCE ───────────────────────────────────────────
    #
    # The behaviour the dialog did not have. A blank form is wrong in four
    # separate ways and has to say so in one go.
    problems = page._problems()
    check("a blank form reports every problem, not the first",
          len(problems) == 4, f"{len(problems)}: "
          + "; ".join(w[:28] for _s, _wd, w in problems))

    page._submit()
    check("submitting a blank form does not post anything",
          posted == [], str(posted))
    check("and the problems appear in one panel",
          not page._errors_card.isHidden(),
          f"hidden={page._errors_card.isHidden()}")
    check("which says how many there are",
          "4 things" in page._errors_title.text(), page._errors_title.text())
    check("and names the step each one is on",
          page._errors_body.text().count("Basic") == 4,
          page._errors_body.text()[:120])

    # ── mistakes spread across steps ────────────────────────────────────
    page._name.setText("Rajesh Kumar")
    page._emp_id.setText("26AMZEM001")
    page._username.setText("rajesh")
    page._password.setText("GoodPass123")
    page._pan.setText("NOTAPAN")                       # step 3
    page._mode.setCurrentIndex(page._mode.findData("bank_transfer"))
    page._ifsc.setText("WRONG")                        # step 4
    page._email.setText("not-an-email")                # step 1

    problems = page._problems()
    steps = sorted({step for step, _w, _why in problems})
    check("problems on three different steps are all reported together",
          len(problems) == 3 and steps == [0, 2, 3],
          f"{len(problems)} problems on steps {steps}")

    page._submit()
    check("still nothing is posted", posted == [], str(posted))
    # STRAIGHT TO THE FIRST ONE. A list of problems on a step nobody is
    # looking at is a list nobody reads.
    check("and the form moves to the first step that has one",
          page._step == 0, str(page._step))
    check("each bad field is marked, not just listed",
          len(page._marked) == 3, str(len(page._marked)))

    # ── a form with nothing wrong ───────────────────────────────────────
    page._email.setText("rajesh@amazeinternet.com")
    page._pan.setText("abcde1234f")                    # lower case on purpose
    page._ifsc.setText("hdfc0001234")
    page._account.setText("0123 4567 8901")            # spaces on purpose
    page._bank.setText("HDFC Bank")
    page._phone.setText("9876543210")
    page._department.setText("Engineering")
    page._designation.setText("QA Engineer")
    page._location.setText("Head office")
    page._gender.setCurrentIndex(page._gender.findData("female"))
    page._joining.setDate(QDate(2026, 8, 1))
    page._address.setPlainText("14 MG Road, Bengaluru")

    check("a complete form has nothing to report", page._problems() == [],
          str(page._problems()))

    posted.clear()
    page._submit()
    check("it posts once", len(posted) == 1, str(len(posted)))
    url, body = posted[0]
    check("to the employees endpoint", url.endswith("/admin/employees"), url)
    check("the error panel is put away once there is nothing to say",
          page._errors_card.isHidden())

    # A PAN AND AN IFSC ARE ONE VALUE EACH, whatever they were typed as. Two
    # casings of the same PAN are the same PAN, and only one can be on a
    # filing; the server upper-cases too, and these must not disagree.
    check("the PAN goes up in case", body.get("pan") == "ABCDE1234F",
          str(body.get("pan")))
    check("and so does the IFSC", body.get("bank_ifsc") == "HDFC0001234",
          str(body.get("bank_ifsc")))
    # Spaces are how an account number is written down and never how it is
    # stored — a transfer to "0123 4567 8901" is not a transfer.
    check("spaces come out of the account number",
          body.get("bank_account_number") == "012345678901",
          str(body.get("bank_account_number")))
    check("the joining date is sent as a date",
          body.get("joining_date") == "2026-08-01", str(body.get("joining_date")))
    check("gender is sent as the stored value, not the shown one",
          body.get("gender") == "female", str(body.get("gender")))

    # A DATE FIELD CANNOT BE EMPTY, so an untouched date of birth must not be
    # sent as 1900-01-01 — a birthday nobody has.
    check("an untouched date of birth is not sent at all",
          "date_of_birth" not in body, str(body.get("date_of_birth")))
    page._dob.setDate(QDate(1994, 3, 12))
    posted.clear()
    page._submit()
    check("but one that was set is", posted[0][1].get("date_of_birth") == "1994-03-12",
          str(posted[0][1].get("date_of_birth")))

    # ── portal access ───────────────────────────────────────────────────
    check("portal access is on to begin with", page._portal.isChecked())
    check("so the credentials are asked for",
          page._username.isEnabled() and page._password.isEnabled())

    page._portal.setChecked(False)
    check("turning it off puts the credential fields beyond reach",
          not page._username.isEnabled() and not page._password.isEnabled())
    check("and empties them, so nothing typed is sent by accident",
          page._username.text() == "" and page._password.text() == "",
          f"{page._username.text()!r}/{page._password.text()!r}")
    check("a form with no portal access still has nothing wrong with it",
          page._problems() == [],
          "; ".join(w[:40] for _s, _wd, w in page._problems()))

    posted.clear()
    page._submit()
    body = posted[0][1]
    check("portal access is sent as off", body.get("portal_access") is False,
          str(body.get("portal_access")))
    check("with no username and no password in the body",
          "username" not in body and "password" not in body,
          str({k: v for k, v in body.items() if k in ("username", "password")}))

    # AN ADMIN WITHOUT A LOGIN IS NOBODY — the console is the whole of the
    # job. The server refuses it, so the form does not offer it either.
    page._role.addItems(["admin"])          # as a super admin would see it
    page._role.setCurrentText("admin")
    problems = [why for _s, _w, why in page._problems()]
    check("an admin cannot be added without portal access",
          any("Only an employee" in why for why in problems), str(problems))
    page._role.setCurrentText("employee")

    page._portal.setChecked(True)
    check("turning it back on asks for the credentials again",
          page._username.isEnabled() and page._password.isEnabled())

    # ── the salary step ─────────────────────────────────────────────────
    #
    # ONE FORMULA, TWO PAGES. The split shown here comes from the same helper
    # the salaries page uses; a second copy is how the two start disagreeing
    # about the same figure.
    # The company's split, as the server states it. The page fetches this;
    # here it is supplied directly, because what is under test is that the
    # preview USES it rather than a copy written into the client.
    panel._remember_salary_template([
        {"name": "Basic", "rule": "PERCENT_CTC", "value": 50},
        {"name": "DA", "rule": "PERCENT_BASIC", "value": 20},
        {"name": "House Rent Allowance", "rule": "PERCENT_BASIC", "value": 50},
        {"name": "Conveyance Allowance", "rule": "PERCENT_BASIC", "value": 15},
        {"name": "Fixed Allowance", "rule": "BALANCE", "value": 0},
    ])
    page._ctc.setValue(600000)
    rows = panel._ctc_preview(600000)
    check("the components are the shared split, not a second copy",
          page._components.rowCount() == len(rows) == 5,
          f"{page._components.rowCount()} rows")
    check("starting with Basic",
          page._components.item(0, 0).text() == "Basic",
          page._components.item(0, 0).text())
    # The parts must add back to the whole, or a payslip's components do not
    # sum to its gross and somebody has to explain the difference.
    total = round(sum(amount for _n, _k, amount in rows), 2)
    check("and the parts add back to the monthly CTC",
          total == round(600000 / 12, 2), f"{total} vs {round(600000 / 12, 2)}")
    check("the monthly gross shown is that sum",
          page._gross.text() == panel._money(total),
          f"{page._gross.text()} vs {panel._money(total)}")

    # ── bank details belong to bank transfers ───────────────────────────
    page._username.setText("rajesh")
    page._password.setText("GoodPass123")
    page._mode.setCurrentIndex(page._mode.findData("cash"))
    check("choosing cash puts the bank fields beyond reach",
          not page._account.isEnabled() and not page._ifsc.isEnabled())
    posted.clear()
    page._submit()
    body = posted[0][1]
    check("and none of them are sent",
          all(k not in body for k in ("bank_name", "bank_account_number",
                                      "bank_ifsc")),
          str([k for k in body if k.startswith("bank")]))
    check("but the mode is", body.get("payment_mode") == "cash",
          str(body.get("payment_mode")))

    # ── the next person starts blank ────────────────────────────────────
    #
    # The page lives in the stack and is reused, so anything left on it would
    # arrive prefilled — which is how one person's PAN ends up on their
    # colleague's record.
    page.reset()
    check("reset empties the name", page._name.text() == "", page._name.text())
    check("and the PAN", page._pan.text() == "", page._pan.text())
    check("and the address", page._address.toPlainText() == "",
          page._address.toPlainText())
    check("and the CTC", page._ctc.value() == 0, str(page._ctc.value()))
    check("and the statutory switches",
          not any(s.isChecked() for s in (page._epf, page._esi, page._pt)))
    check("and it starts back on the first step", page._step == 0, str(page._step))
    check("with the error panel put away", page._errors_card.isHidden())

    # ── the employees tab hands off rather than building a form ─────────
    tab = panel._EmployeesTab.__new__(panel._EmployeesTab)
    panel.QWidget.__init__(tab)
    check("the employees tab has a signal for it, not a dialog",
          hasattr(panel._EmployeesTab, "open_add_employee"))
    asked: list = []
    tab.open_add_employee.connect(lambda: asked.append(True))
    tab._workers = []
    panel._EmployeesTab._add_employee(tab)
    check("pressing Add Employee asks the panel to open the page",
          asked == [True], str(asked))

    page.deleteLater()
    app.processEvents()
finally:
    panel._FetchWorker, panel._PostWorker, panel._track_worker = (
        real_fetch, real_post, real_track)

print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILED'}\n")

# os._exit, for the reason test_theme and test_payroll_tab both record.
sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if failures else 0)
