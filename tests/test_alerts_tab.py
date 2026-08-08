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
    from PySide6.QtWidgets import QPushButton

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

        print("\nThe Employees list shows people by name")
        # Chat, reports and the audit log all show a person by their full
        # name. This list showed the login username alone, so one account read
        # as "Priya Nair" in a conversation and "manager" here — and an admin
        # who had just read a message from her could not find her in her own
        # employee list.
        emp = panel._EmployeesTab()
        emp._display_employees([
            {"employee_id": "AD100", "username": "manager", "full_name": "Priya Nair",
             "role": "admin", "status": "online", "last_seen": None},
            {"employee_id": "EM101", "username": "rajesh", "full_name": "",
             "role": "employee", "status": "offline", "last_seen": None},
        ])
        shown = emp._table.item(0, 1).text()
        check("the name somebody is shown by everywhere else is here too",
              "Priya Nair" in shown, shown)
        check("with the login username kept beside it, not thrown away",
              "manager" in shown, shown)
        check("an account with no name falls back to the username",
              emp._table.item(1, 1).text() == "rajesh", emp._table.item(1, 1).text())
        header = emp._table.horizontalHeaderItem(1).text()
        check("and the column says Name", header == "Name", header)
        check("the search box says a name can be typed into it",
              "name" in emp._search_input.placeholderText().lower(),
              emp._search_input.placeholderText())

        print("\nCreating somebody, with the name you want shown")
        # The create dialog never asked for a name, so the server fell back to
        # the login username and every account made from the panel was shown
        # by its login for the rest of its life — there was no way to correct
        # one afterwards either.
        sent = []

        class _CapturePost:
            def __init__(self, url, body):
                self.url, self.body = url, body
                self.result = _Signal()
                self.error = _Signal()

            def start(self):
                sent.append((self.url, self.body))

        original_post = panel._PostWorker
        panel._PostWorker = _CapturePost
        warned = []
        original_warn = panel.QMessageBox.warning
        panel.QMessageBox.warning = staticmethod(
            lambda *a, **k: warned.append(a[2] if len(a) > 2 else ""))
        original_exec = panel.QDialog.exec
        panel.QDialog.exec = lambda self_: None
        try:
            emp._add_employee()
            fields = emp.findChildren(panel.QLineEdit)
            check("the create dialog asks for a full name",
                  any(f.placeholderText() == "Rajesh Kumar"
                      for d in emp.findChildren(panel.QDialog)
                      for f in d.findChildren(panel.QLineEdit)),
                  "no name field — every new account would be shown by its login")

            dialog = emp.findChildren(panel.QDialog)[-1]
            boxes = dialog.findChildren(panel.QLineEdit)
            by_hint = {f.placeholderText(): f for f in boxes}
            buttons = [b for b in dialog.findChildren(QPushButton)
                       if "Create" in b.text()]

            # Submitting with no name must not quietly create the account.
            by_hint["rajesh"].setText("newguy")
            buttons[0].click()
            check("creating without a name is refused, with the reason",
                  len(sent) == 0 and warned, str(warned))

            by_hint["Rajesh Kumar"].setText("Sunita Verma")
            by_hint["QA Engineer"].setText("Accounts")
            buttons[0].click()
            check("with a name, it is sent to the server",
                  len(sent) == 1, str(len(sent)))
            check("as full_name, the field the rest of the product reads",
                  sent[0][1].get("full_name") == "Sunita Verma", str(sent[0][1]))
            check("with the designation",
                  sent[0][1].get("designation") == "Accounts", str(sent[0][1]))
            check("and the login username kept separate",
                  sent[0][1].get("username") == "newguy", str(sent[0][1]))

            print("\nCorrecting a name later")
            sent.clear()
            emp._edit_profile({"employee_id": "EM101", "username": "rajesh",
                               "full_name": "Rajesh Kumar", "designation": "QA"})
            dialog = emp.findChildren(panel.QDialog)[-1]
            boxes = dialog.findChildren(panel.QLineEdit)
            check("the dialog opens with the name they have now",
                  boxes[0].text() == "Rajesh Kumar", boxes[0].text())
            boxes[0].setText("Rajesh Kumar Singh")
            [b for b in dialog.findChildren(QPushButton) if b.text() == "Save"][0].click()
            check("saving sends it to that employee's profile",
                  sent and sent[0][0].endswith("/admin/employees/EM101/profile"),
                  str(sent[0][0]) if sent else "nothing sent")
            check("with the new name",
                  sent[0][1].get("full_name") == "Rajesh Kumar Singh", str(sent[0][1]))
        finally:
            panel._PostWorker = original_post
            panel.QMessageBox.warning = original_warn
            panel.QDialog.exec = original_exec

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
