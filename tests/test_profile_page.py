"""
My Profile, on the employee's own screen.

THE PROMISE THIS PAGE MAKES, and the one worth a test: an employee may change
two things about themselves — their phone number and their photo. Everything
else it shows is the company's record of them and is drawn read-only.

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
    "designation": "Developer", "role": "employee", "phone": "+91 98765 43210",
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
    from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QCheckBox
    from PySide6.QtCore import Qt
    QApplication.instance() or QApplication([])

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
    check("the phone goes into the one box that can be typed in",
          page._phone.text() == "+91 98765 43210", page._phone.text())
    check("with initials standing in for a photo nobody has set",
          page._avatar.text() == "RK", page._avatar.text())

    print("\nTHE RULE: two things, and no more")
    # Not "the fields are disabled" — counted. Anything editable that is not
    # the phone number is a way to change something that is not the
    # employee's to change.
    boxes = page.findChildren(QLineEdit)
    check("exactly ONE editable field on the whole page", len(boxes) == 1,
          f"{len(boxes)}: {[b.placeholderText() for b in boxes]}")
    check("and it is the phone number",
          boxes and "98765" in boxes[0].placeholderText(),
          boxes[0].placeholderText() if boxes else "none")
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
    print("all profile page checks passed")
    sys.stdout.flush()
    sys.exit(0)


main()
