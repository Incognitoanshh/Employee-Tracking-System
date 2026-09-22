"""
My Profile, on the employee's own screen.

THE PROMISE THIS PAGE MAKES, and the one worth a test: an employee may change
ONE thing about themselves — their photo. Everything else it shows, their
phone number and both email addresses included, is the company's record of
them and is drawn read-only. It used to allow the phone and the email; the
owner's rule since: "employee khud se koi bhi value change nahi kar sakta
apne profile ka", with the photo agreed as the exception.

The server holds the same line independently (server/tests/test_profile.js).
Neither side relies on the other; a page that only *looks* read-only in front
of somebody who cannot be bothered to open a network tool is not a control.

Run:  python3 tests/test_profile_page.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ISOLATED FROM ANYTHING REAL, BEFORE ANY CLIENT MODULE IS IMPORTED.
os.environ.setdefault("ETS_DATA_DIR", tempfile.mkdtemp(prefix="ets_test_"))
os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:9/api")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SCREENSHOT_ENCRYPTION_KEY",
                      "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))
    sys.stdout.flush()


PROFILE = {
    "employee_id": "E001", "username": "rajesh", "full_name": "Rajesh Kumar",
    "designation": "Developer", "role": "employee", "phone": "+91 90000 00000",
    "email": "rajesh@amaze.co", "personal_email": "rajesh.k@gmail.com",
    "department": "Engineering", "team": "Development",
    "reporting_manager": "Priya Nair", "joining_date": "2025-06-01",
    "employment_status": "probation", "photo": None, "status": "online",
}

SUMMARY = {
    "today": {"login_time": "2026-08-12T09:31:00", "worked_seconds": 7200,
              "idle_seconds": 1800, "active_seconds": 5400, "screenshots": 6},
    "week": {"worked_seconds": 36000, "days_present": 5},
    "month": {"worked_seconds": 144000, "days_present": 10,
              "average_daily_seconds": 14400, "attendance_percent": 83},
    "last_7_days": [
        {"day": f"2026-08-0{n}", "worked_seconds": n * 3600,
         "idle_seconds": n * 600, "screenshots": n} for n in range(1, 8)],
}

SESSIONS = {
    "sessions": [
        {"device_id": "rajesh-laptop", "ip": "10.0.0.4",
         "login_time": "2026-08-12T09:31:00", "last_seen": "2026-08-12T12:05:00",
         "is_live": True, "is_this_device": True},
        {"device_id": "old-desktop", "ip": None,
         "login_time": "2026-08-01T10:00:00", "last_seen": "2026-08-01T18:00:00",
         "is_live": False, "is_this_device": False},
    ],
    "history": [
        {"login_time": "2026-08-12T09:31:00", "logout_time": None},
        {"login_time": "2026-08-11T09:20:00", "logout_time": "2026-08-11T18:40:00"},
    ],
}


def main():
    from PySide6.QtWidgets import (QApplication, QLineEdit, QPushButton,
                                   QCheckBox, QScrollArea)
    from PySide6.QtCore import Qt
    app = QApplication.instance() or QApplication([])

    from client.infrastructure.database.database import Database
    Database.initialize()

    from client.presentation.windows import profile_page as pp
    from client.presentation.windows.profile_page import ProfilePage

    page = ProfilePage(panel=None)

    print("It builds with no network at all")
    check("the page exists", page is not None)
    check("and asks for nothing until it is shown", page._profile == {}, str(page._profile))

    print("\nWhat it shows when the profile arrives")
    page._on_profile(dict(PROFILE))
    check("the name is the person's, not their username",
          page._name.text() == "Rajesh Kumar", page._name.text())
    check("the employee id is shown", page._rows["employee_id"].text() == "E001",
          page._rows["employee_id"].text())
    check("the department", page._rows["department"].text() == "Engineering")
    check("the manager, by name", page._rows["reporting_manager"].text() == "Priya Nair")
    check("the joining date, as a date rather than a timestamp",
          page._rows["joining_date"].text() == "2025-06-01",
          page._rows["joining_date"].text())
    check("and the employment status in words a person reads",
          page._rows["employment_status"].text() == "Probation",
          page._rows["employment_status"].text())
    # Shown, not edited — see THE RULE below.
    check("the phone is shown", page._phone.text() == "+91 90000 00000",
          page._phone.text())
    check("and so is the official email",
          page._email.text() == "rajesh@amaze.co", page._email.text())
    check("with initials standing in for a photo nobody has set",
          page._avatar.text() == "RK", page._avatar.text())

    print("\nTHE RULE: a photo, and no more")
    # Not "the fields are disabled" — COUNTED. Anything editable at all is a
    # way to change something that is not the employee's to change, and the
    # count is what notices a box added later without anybody deciding it
    # belonged here.
    #
    # It was two — the phone and the email — until the owner's rule: "employee
    # khud se koi bhi value change nahi kar sakta apne profile ka." The photo
    # is the one exception, and it is not a text box. This number should not
    # move again without the same kind of decision.
    boxes = page.findChildren(QLineEdit)
    check("NOTHING on the page can be typed into", len(boxes) == 0,
          f"{len(boxes)}: {[b.placeholderText() for b in boxes]}")
    check("and the page has no way to save contact details any more",
          not hasattr(page, "_save_contact"))

    # THE ONE EXCEPTION. A photo is still theirs to change.
    photo_buttons = [b.text() for b in page.findChildren(QPushButton)
                     if b.text() in ("Change photo", "Remove")]
    check("but the photo is still theirs to change",
          sorted(photo_buttons) == ["Change photo", "Remove"], str(photo_buttons))

    # BOTH ADDRESSES, SHOWN. "Employee profile me 2 email hoga — ek official
    # email, aur ek personal email."
    check("the official address is shown", page._email.text() == "rajesh@amaze.co",
          page._email.text())
    check("and the personal one beside it",
          page._personal_email.text() == "rajesh.k@gmail.com",
          page._personal_email.text())
    check("the phone is shown too, as text", page._phone.text() == "+91 90000 00000",
          page._phone.text())
    # An address nobody has given reads as a dash, not as an empty line that
    # looks like a page which failed to load.
    page._on_profile({**PROFILE, "personal_email": None, "phone": None})
    check("and what nobody has given reads as a dash",
          page._personal_email.text() == "—" and page._phone.text() == "—",
          f"{page._personal_email.text()} / {page._phone.text()}")
    page._on_profile(dict(PROFILE))

    labels = [page._rows[k] for k in ("employee_id", "department", "designation",
                                      "reporting_manager", "employment_status")]
    check("id, department, designation, manager and status are labels, not inputs",
          all(l.__class__.__name__ == "QLabel" for l in labels))
    check("none of them can be typed into",
          all(not (l.textInteractionFlags() & Qt.TextInteractionFlag.TextEditable)
              for l in labels))
    check("but they can be selected and copied — people need their own id",
          all(l.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
              for l in labels))

    print("\nA message from somebody else is shown as text, not markup")
    page._on_profile({**PROFILE, "full_name": "<b>BOLD</b>", "department": "<i>x</i>"})
    check("the department label is plain text",
          page._rows["department"].textFormat() == Qt.TextFormat.PlainText,
          str(page._rows["department"].textFormat()))
    page._on_profile(dict(PROFILE))

    print("\nThe week, from the server's figures — not recomputed here")
    page._on_summary(dict(SUMMARY))
    check("worked today reads as hours and minutes",
          page._rows["today_worked"].text() == "2h 00m", page._rows["today_worked"].text())
    check("active today is what the server said, not worked minus something local",
          page._rows["today_active"].text() == "1h 30m", page._rows["today_active"].text())
    check("idle today", page._rows["today_idle"].text() == "0h 30m")
    check("screenshots today", page._rows["today_shots"].text() == "6")
    check("this week names the days present out of seven",
          page._rows["week_days"].text() == "5 of 7", page._rows["week_days"].text())
    check("the month average", page._rows["month_avg"].text() == "4h 00m",
          page._rows["month_avg"].text())
    check("and attendance as a percentage",
          page._rows["month_attendance"].text() == "83%",
          page._rows["month_attendance"].text())
    check("all three charts were given seven points",
          all(len(c._values) == 7 for c in page._charts.values()),
          str({k: len(c._values) for k, c in page._charts.items()}))

    print("\nMissing figures must read as missing, never as zero")
    blank = ProfilePage(panel=None)
    blank._on_summary({})
    check("an empty reply leaves a dash, not a confident 0",
          blank._rows["today_worked"].text() in ("—", "0h 00m"),
          blank._rows["today_worked"].text())
    crashed = False
    try:
        blank._on_profile({})
        blank._on_sessions({})
    except Exception as error:
        crashed = True
    check("and nothing raises on an empty profile or session list", not crashed)

    print("\nWhere I am signed in")
    page._on_sessions(dict(SESSIONS))
    lines = [page._sessions_box.itemAt(i).widget().text()
             for i in range(page._sessions_box.count())]
    check("both devices are listed", len(lines) == 2, str(lines))
    check("this one is marked as this one",
          any("this device" in l for l in lines), str(lines))
    check("an IP is shown where there is one", any("10.0.0.4" in l for l in lines))
    check("and a device with no IP does not print the word None",
          not any("None" in l for l in lines), str(lines))
    check("the current status follows the live session",
          page._rows["device_status"].text() == "Online",
          page._rows["device_status"].text())

    history = [page._history_box.itemAt(i).widget().text()
               for i in range(page._history_box.count())]
    check("recent sign-ins are listed", len(history) == 2, str(history))
    check("an open one says so rather than showing a blank",
          any("still open" in h for h in history), str(history))

    print("\nPreferences are this machine's, and they persist")
    from client.services.settings_service import SettingsService
    page._save_pref(pp.PREF_SOUND, False)
    check("switching one off is written down",
          SettingsService.get_setting(pp.PREF_SOUND) == "0",
          str(SettingsService.get_setting(pp.PREF_SOUND)))
    check("and read back as off", pp.pref_enabled(pp.PREF_SOUND) is False)
    page._save_pref(pp.PREF_SOUND, True)
    check("and on again", pp.pref_enabled(pp.PREF_SOUND) is True)
    check("anything never set defaults to ON — a silent app looks broken",
          pp.pref_enabled("notify_never_set_by_anybody") is True)

    print("\nSigning out everywhere is asked about first")
    asked = []
    real_question = pp.QMessageBox.question
    pp.QMessageBox.question = staticmethod(
        lambda *a, **k: asked.append(1) or pp.QMessageBox.StandardButton.No)
    called = []
    page._run = lambda *a, **k: called.append(1)
    try:
        page._logout_all()
    finally:
        pp.QMessageBox.question = real_question
    check("it asks before ending every session", asked == [1], str(asked))
    check("and saying no does nothing at all", called == [], str(called))

    print("\nVerified, or not, and never quietly in between")
    # A typed address and a proved address are not the same thing, and the
    # difference only matters at the moment something is sent to it. The page
    # has to say which it is looking at — silence here is what lets an
    # unverified address be treated later as a working one.
    page._on_profile({**PROFILE, "email": "rajesh@amaze.co", "email_verified": True})
    check("a verified address says so", "Verified" in page._email_state.text(),
          page._email_state.text())
    check("and offers nothing to press", page._verify_btn.isHidden())

    page._on_profile({**PROFILE, "email": "rajesh@amaze.co", "email_verified": False})
    check("an unverified one says THAT", page._email_state.text() == "Not verified",
          page._email_state.text())
    check("and offers the way to fix it", not page._verify_btn.isHidden())

    page._on_profile({**PROFILE, "email": None, "email_verified": False})
    check("with no address at all there is nothing to verify",
          page._email_state.text() == "" and page._verify_btn.isHidden(),
          page._email_state.text())

    print("\nThe page never tries to change an address")
    # This used to check that a mistyped address was caught before the
    # network. There is no longer any way to send one: nothing on the page
    # edits the phone or either email, and the server refuses the route. What
    # is worth holding is that no code path here reaches it — a patch to
    # /profile/me from this page would mean a box came back.
    sent = []
    real_patch = pp._http.patch
    pp._http.patch = lambda *a, **k: sent.append(a) or (_ for _ in ()).throw(
        AssertionError("the profile page must not edit the profile"))
    try:
        page._on_profile(dict(PROFILE))
        page.refresh = lambda: None
        check("loading and showing the profile sends no edit", sent == [], str(sent))
    finally:
        pp._http.patch = real_patch

    print("\nA photo that is too large is refused before it is uploaded")
    big = os.path.join(os.environ["ETS_DATA_DIR"], "huge.png")
    with open(big, "wb") as handle:
        handle.write(b"\0" * (pp.PHOTO_MAX_BYTES + 1))
    sent = []
    page._run = lambda *a, **k: sent.append(1)
    real_dialog = pp.QFileDialog.getOpenFileName
    pp.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (big, ""))
    try:
        page._pick_photo()
    finally:
        pp.QFileDialog.getOpenFileName = real_dialog
    check("nothing is sent over the network", sent == [], str(sent))
    check("and the person is told why, before waiting for an upload",
          "5 MB" in page._status.text(), page._status.text())

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.stdout.flush()
        sys.exit(1)
    print("\nYour own pay, and nobody else's")

    # THE SECTION IS FED BY A ROUTE THAT TAKES NO EMPLOYEE ID. That is what
    # makes it safe to put a salary on a page every employee opens: there is
    # no parameter here to point at a colleague. If this fetch ever grows one,
    # this test should be the thing that stops it.
    import client.presentation.windows.profile_page as _pp
    source = open(_pp.__file__, encoding="utf-8").read()
    check("the pay section asks only for the caller's own",
          "/payroll/mine/salary" in source
          and "employee_id" not in source.split("_fetch_pay")[1].split("def ")[1],
          "the pay fetch mentions an employee id")

    page._on_pay({"salary": {"gross_monthly": 31000, "overtime_hourly": 220,
                             "effective_from": "2026-07-01",
                             "remarks": "Annual review"},
                  "latest_payroll": {"month": "2026-06", "net_pay": 25800}})
    check("the monthly gross is shown as money",
          page._rows["salary_gross"].text() == "\u20b931,000.00",
          page._rows["salary_gross"].text())
    check("with the date it took effect",
          page._rows["salary_from"].text() == "2026-07-01",
          page._rows["salary_from"].text())
    check("and the overtime rate",
          page._rows["salary_overtime"].text() == "\u20b9220.00",
          page._rows["salary_overtime"].text())
    check("the last payslip names its month and its net",
          page._rows["salary_latest"].text() == "2026-06  \u00b7  \u20b925,800.00",
          page._rows["salary_latest"].text())
    check("and the reason the salary was set is kept within reach",
          page._rows["salary_from"].toolTip() == "Annual review",
          page._rows["salary_from"].toolTip())

    # NOTHING SET YET MUST NOT READ AS ZERO. "\u20b90.00" against somebody's
    # salary is a statement that they are paid nothing, and it is the same
    # mistake this file already guards against for the work summary.
    page._on_pay({"salary": None, "latest_payroll": None})
    check("an unset salary says so rather than showing zero",
          page._rows["salary_gross"].text() == "Not set",
          page._rows["salary_gross"].text())
    check("no overtime rate reads as not paid, not as nothing",
          page._rows["salary_overtime"].text() == "Not paid",
          page._rows["salary_overtime"].text())
    check("and no payslip yet says so plainly",
          page._rows["salary_latest"].text() == "None yet",
          page._rows["salary_latest"].text())

    # ── THE PAGE IN TWO COLUMNS WHEN THERE IS ROOM ──────────────────────
    #
    # Reported: "kosis kro ki screen scrollable na ho jyda aur agar hota v hai
    # to scroll bar dikhe". Measured at the window's own default size: 1771px
    # of cards in a 665px window, with the right half of every card empty.
    #
    # What is checked is not "it looks nicer". It is that the SAME cards are
    # moved rather than rebuilt — a card built again on a resize leaves the
    # page updating labels nobody can see, which is how a page ends up showing
    # a dash where a value arrived — and that the page is genuinely shorter.
    print("\nTwo columns when the page is wide enough")
    # SHOWN, then resized. A hidden widget does not lay its children out, so
    # a measurement taken without this reads the same number at every width —
    # and one pass of the event loop is not always enough either: the resize
    # is delivered in the first, the columns rearrange in the next.
    def settle():
        for _ in range(3):
            app.processEvents()

    page.show()
    page.resize(1010, 900)
    settle()
    wide_cards = page._columns.cards()
    scroll = page.findChild(QScrollArea)
    wide_height = scroll.widget().sizeHint().height()
    lefts = sorted({card.mapTo(page, card.rect().topLeft()).x()
                    for card in wide_cards})
    check("the cards stand in two columns", page._columns.columns() == 2,
          str(page._columns.columns()))
    check("side by side, not on top of each other", len(lefts) == 2, str(lefts))

    page.resize(750, 900)
    settle()
    narrow_height = scroll.widget().sizeHint().height()
    narrow_lefts = sorted({card.mapTo(page, card.rect().topLeft()).x()
                           for card in page._columns.cards()})
    check("a narrow window stacks them again", page._columns.columns() == 1,
          f"{page._columns.columns()} columns; page {page.width()}px, "
          f"cards area {page._columns.width()}px, page minimum "
          f"{page.minimumSizeHint().width()}px")
    check("in one column", len(narrow_lefts) == 1, str(narrow_lefts))
    check("and two columns really are shorter than one",
          wide_height < narrow_height - 300,
          f"{wide_height}px wide vs {narrow_height}px narrow")

    page.resize(1010, 900)
    settle()
    check("every card is still there after both moves",
          page._columns.cards() == wide_cards,
          f"{len(page._columns.cards())} of {len(wide_cards)}")
    check("and each one is on screen, not orphaned by the move",
          all(card.parentWidget() is not None and card.isVisibleTo(page)
              for card in page._columns.cards()))
    # THE ONE THAT MATTERS. The page writes into these labels by reference.
    check("the values the page had written are still in them",
          page._rows["employee_id"].text() == "E001"
          and page._rows["department"].text() == "Engineering",
          f"{page._rows['employee_id'].text()} / {page._rows['department'].text()}")
    page._on_profile(dict(PROFILE, department="Design"))
    check("and the page can still update them after a resize",
          page._rows["department"].text() == "Design",
          page._rows["department"].text())

    page.hide()

    print("all profile page checks passed")
    sys.stdout.flush()
    sys.exit(0)


main()
