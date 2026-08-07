"""Ticking weekly-off days and getting them back, through the real server.

The pieces were each tested separately — the checkboxes render, the server
stores a sorted string, the scheduler skips those days. What was never tested
is the path an admin actually takes: click some boxes, press Save, and see
whether the days come back when the page is read again.

That gap is where "it doesn't work" lives. A value that saves but does not
read back looks identical to one that never saved, and the only place the
difference shows up is a weekend when captures do or do not happen.

Run:  python3 tests/test_weekly_off_roundtrip.py
      (needs PostgreSQL, and node_modules installed under server/)
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB = f"ets_weekly_{os.getpid()}"
PORT = 9000 + (os.getpid() % 800)

failures = []
DAYS = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}


def check(label, ok, detail=""):
    if not ok:
        failures.append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          f"{'' if ok or not detail else f'  — {detail}'}")


def psql(db, sql):
    return subprocess.run(["psql", "-d", db, "-v", "ON_ERROR_STOP=1", "-tAc", sql],
                          capture_output=True, text=True, check=True).stdout.strip()


def infrastructure_missing():
    import shutil
    if not shutil.which("psql"):
        return "psql not found"
    if not shutil.which("node"):
        return "node not found"
    if not os.path.isdir(os.path.join(ROOT, "server", "node_modules")):
        return "server/node_modules not installed"
    try:
        subprocess.run(["psql", "-d", "postgres", "-tAc", "SELECT 1"],
                       capture_output=True, check=True, timeout=10)
    except Exception:
        return "PostgreSQL not reachable"
    return None


def main() -> int:
    import requests

    psql("postgres", f"CREATE DATABASE {DB}")
    server = None
    try:
        for name in ("ets.sql",):
            subprocess.run(["psql", "-d", DB, "-v", "ON_ERROR_STOP=1", "-q", "-f",
                            os.path.join(ROOT, name)], capture_output=True, check=True)
        for migration in ("2026_08_05_password_management",
                          "2026_08_05_username_case_insensitive",
                          "2026_08_06_single_session"):
            subprocess.run(["psql", "-d", DB, "-v", "ON_ERROR_STOP=1", "-q", "-f",
                            os.path.join(ROOT, "server", "migrations", f"{migration}.sql")],
                           capture_output=True, check=True)

        seeded = subprocess.run(
            ["node", "-e", "const b=require('bcryptjs');"
                           "process.stdout.write(b.hashSync('SuperSecret123',10))"],
            cwd=os.path.join(ROOT, "server"),
            capture_output=True, text=True, check=True).stdout.strip()
        psql(DB, "INSERT INTO employees (employee_id, username, password, role) VALUES "
                 f"('SA001','superadmin','{seeded}','super_admin'), "
                 f"('E001','emp1','{seeded}','employee')")

        env = dict(os.environ,
                   DB_HOST=os.environ.get("PGHOST", "127.0.0.1"),
                   DB_PORT=os.environ.get("PGPORT", "5432"),
                   DB_NAME=DB,
                   DB_USER=os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
                   DB_PASSWORD=os.environ.get("PGPASSWORD", "unused-locally"),
                   JWT_SECRET="test-secret-not-used-in-production",
                   ENCRYPTION_KEY="0" * 64,
                   PORT=str(PORT))
        server = subprocess.Popen(["node", os.path.join(ROOT, "server", "server.js")],
                                  env=env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{PORT}/api"
        for _ in range(120):
            try:
                requests.get(f"{base}/nope", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        else:
            print("server never came up")
            return 1

        # ── drive the real Configuration tab ────────────────────────────
        os.environ["ETS_DATA_DIR"] = tempfile.mkdtemp()
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)

        from client.infrastructure.database.database import Database
        Database.initialize()
        from client.application.managers.session_manager import SessionManager

        token = requests.post(f"{base}/auth/login", timeout=10, json={
            "username": "superadmin", "password": "SuperSecret123"}).json()["token"]
        SessionManager.create_session("SA001", token, "super_admin", full_name="SA")

        import client.presentation.windows.admin_config_panel as acp
        acp.API_BASE_URL = base

        tab = acp._ConfigTab()
        boxes = tab._weekly_offs

        def settle(seconds=3.0):
            end = time.time() + seconds
            while time.time() < end:
                app.processEvents()
                time.sleep(0.02)

        settle()

        # Nothing exclusive is holding them — a real click on each.
        check("no button group makes them mutually exclusive",
              not any(b.group() for b in boxes.values()))
        check("none use autoExclusive",
              not any(b.autoExclusive() for b in boxes.values()))

        # ── tick several, save, read back ───────────────────────────────
        wanted = [1, 3, 6, 7]          # Mon, Wed, Sat, Sun
        for iso in wanted:
            boxes[iso].click()
        app.processEvents()
        ticked = sorted(i for i, b in boxes.items() if b.isChecked())
        check("four days can be ticked at once",
              ticked == wanted, f"{[DAYS[i] for i in ticked]}")

        tab._shift_start.setText("09:00")
        tab._shift_end.setText("18:00")
        tab._save_config()
        settle(5.0)

        stored = psql(DB, "SELECT weekly_offs FROM employee_configs WHERE employee_id IS NULL")
        check("the server stored all four", stored == "1,3,6,7", repr(stored))
        check("the confirmation names them",
              all(DAYS[i] in tab._status_label.text() for i in wanted),
              tab._status_label.text())

        # Wipe the form the way switching employees would, then reload.
        for b in boxes.values():
            b.setChecked(False)
        tab._reload_after_save()
        settle(4.0)
        back = sorted(i for i, b in boxes.items() if b.isChecked())
        check("and they come back when the page is read again",
              back == wanted, f"{[DAYS[i] for i in back]}")

        # ── unticking must also stick ───────────────────────────────────
        boxes[3].click()               # drop Wednesday
        app.processEvents()
        tab._save_config()
        settle(5.0)
        stored = psql(DB, "SELECT weekly_offs FROM employee_configs WHERE employee_id IS NULL")
        check("removing one day saves too", stored == "1,6,7", repr(stored))

        for b in boxes.values():
            b.setChecked(False)
        tab._save_config()
        settle(5.0)
        stored = psql(DB, "SELECT weekly_offs FROM employee_configs WHERE employee_id IS NULL")
        check("clearing every day saves as empty, not ignored",
              stored == "", repr(stored))

        # ── all seven is refused, and says why ──────────────────────────
        for b in boxes.values():
            b.setChecked(True)
        tab._save_config()
        settle(2.0)
        check("all seven days is refused",
              "cannot be a weekly off" in tab._status_label.text(),
              tab._status_label.text())
        stored = psql(DB, "SELECT weekly_offs FROM employee_configs WHERE employee_id IS NULL")
        check("and nothing was written", stored == "", repr(stored))

        # ── the preview that makes it checkable ─────────────────────────
        #
        # This is the answer to "I set it but could not tell whether it
        # worked": the server says which of the coming days are off, so the
        # setting can be verified the moment it is made.
        for b in boxes.values():
            b.setChecked(False)
        boxes[7].setChecked(True)        # Sunday
        tab._save_config()
        settle(5.0)
        tab._refresh_upcoming()
        settle(4.0)
        preview = tab._weekly_preview.text()
        check("the preview says how many of the next days are off",
              "off" in preview and "Weekly off" in preview, preview[:120])
        check("and marks the non-working ones",
              "[" in preview and "]" in preview, preview[:120])

        upcoming = requests.get(f"{base}/admin/upcoming/global",
                                params={"days": 14},
                                headers={"Authorization": f"Bearer {token}"},
                                timeout=10).json()
        sundays = [d for d in upcoming["days"] if d["weekday"] == "Sun"]
        check("every Sunday in the next fortnight is marked non-working",
              len(sundays) == 2 and all(not d["working"] for d in sundays),
              str([(d["date"], d["working"]) for d in sundays]))
        others = [d for d in upcoming["days"] if d["weekday"] != "Sun"]
        check("and no other day is",
              all(d["working"] for d in others),
              str([(d["date"], d["weekday"]) for d in others if not d["working"]]))

        # A holiday must show up here too, with its name.
        psql(DB, "INSERT INTO holidays (holiday_date, name) "
                 "VALUES ((NOW() AT TIME ZONE 'Asia/Kolkata')::date + 3, 'Test Holiday')")
        upcoming = requests.get(f"{base}/admin/upcoming/global",
                                params={"days": 14},
                                headers={"Authorization": f"Bearer {token}"},
                                timeout=10).json()
        holiday = [d for d in upcoming["days"] if d.get("reason", "") and "Holiday" in d["reason"]]
        check("a holiday shows in the preview, named",
              len(holiday) == 1 and "Test Holiday" in holiday[0]["reason"],
              str(holiday))

        # ── a rejection from the server is explained, not swallowed ─────
        # This is what made it look broken: every failure read "Save failed".
        for b in boxes.values():
            b.setChecked(False)
        boxes[7].setChecked(True)
        tab._idle_spin.setValue(10)
        tab._on_save_done({"success": False, "message": "idle_threshold_seconds must be 10–150"})
        check("a server rejection shows the server's reason",
              "10–150" in tab._status_label.text(), tab._status_label.text())

    finally:
        if server:
            server.terminate()
            try:
                server.wait(timeout=10)
            except Exception:
                server.kill()
        psql("postgres", f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")

    print()
    if failures:
        print(f"{len(failures)} failure(s)")
        return 1
    print("all weekly-off round-trip checks passed")
    return 0


if __name__ == "__main__":
    print("Weekly off — ticking days and getting them back\n")
    reason = infrastructure_missing()
    if reason:
        print(f"  SKIP  {reason}")
        sys.exit(0)
    code = main()
    sys.stdout.flush()
    # Qt still holds background threads from the panel; a normal interpreter
    # exit tears them down mid-flight and macOS reports a crash. Nothing is
    # left to clean up at this point.
    os._exit(code)
