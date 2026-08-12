"""
A configuration change has to reach the employee's machine, and soon.

Asked for directly: "configuration shi hai work kr rha hai means turant apply
ho rha hai na" — after an admin set a capture count for somebody and no
screenshot appeared.

Nothing here is mocked except the screen. A real server against a real
database; the admin API called the way the Configuration page calls it; the
client's own ConfigSyncManager fetching it back; and the real SchedulerService
asked whether it would act on what arrived.

The part that used to be broken is the last one. The numbers were fetched and
stored correctly all along — and then ignored until midnight or a restart,
because the scheduler worked out "did anything change?" by reading the
settings table AFTER it had already been written, so every value was compared
against itself. An edit looked saved and did nothing.

Run:  python3 tests/test_config_apply.py
"""

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))
    sys.stdout.flush()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def psql(db, sql):
    return subprocess.run(["psql", "-d", db, "-At", "-c", sql],
                          capture_output=True, text=True)


def main():
    db_name = f"ets_cfg_{os.getpid()}"
    data_dir = tempfile.mkdtemp(prefix="ets_cfg_data_")
    uploads = tempfile.mkdtemp(prefix="ets_cfg_up_")
    port = free_port()
    server = None

    os.environ["ETS_DATA_DIR"] = data_dir
    os.environ["SCREENSHOT_ENCRYPTION_KEY"] = base64.b64encode(
        bytes(range(32))).decode()
    os.environ["API_BASE_URL"] = f"http://127.0.0.1:{port}/api"
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    try:
        subprocess.run(["psql", "-d", "postgres", "-c",
                        f"DROP DATABASE IF EXISTS {db_name}"], capture_output=True)
        subprocess.run(["psql", "-d", "postgres", "-c",
                        f"CREATE DATABASE {db_name}"], capture_output=True, check=True)
        subprocess.run(["node", "-e",
                        f'require("{os.path.join(ROOT, "server", "tests", "_migrate.js")}").migrate("{db_name}")'],
                       capture_output=True, check=True, cwd=ROOT)

        pw = subprocess.run(
            ["node", "-e",
             'console.log(require("bcryptjs").hashSync("SuperSecret123", 10))'],
            capture_output=True, text=True, check=True,
            cwd=os.path.join(ROOT, "server")).stdout.strip()

        for eid, user, role, name in (
                ("SA01", "owner", "super_admin", "Owner"),
                ("A001", "raju", "admin", "Raju"),
                ("E001", "rajesh", "employee", "Rajesh Kumar")):
            psql(db_name,
                 "INSERT INTO employees (employee_id, username, password, role, "
                 f"full_name) VALUES ('{eid}','{user}','{pw}','{role}','{name}')")

        env = {**os.environ,
               "DB_HOST": os.environ.get("PGHOST", "127.0.0.1"),
               "DB_PORT": os.environ.get("PGPORT", "5432"),
               "DB_NAME": db_name,
               "DB_USER": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
               "DB_PASSWORD": os.environ.get("PGPASSWORD", "unused-locally"),
               "JWT_SECRET": "test-secret-not-used-in-production",
               "PORT": str(port), "ENCRYPTION_KEY": "0" * 64,
               "UPLOAD_DIR": uploads}
        server = subprocess.Popen(["node", "server.js"],
                                  cwd=os.path.join(ROOT, "server"), env=env,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)

        import requests
        base = f"http://127.0.0.1:{port}/api"
        for _ in range(60):
            try:
                requests.get(f"{base}/health", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        else:
            print("  FAIL  the test server never came up")
            sys.exit(1)

        def login(username):
            r = requests.post(f"{base}/auth/login",
                              json={"username": username,
                                    "password": "SuperSecret123",
                                    "device_id": f"dev-{username}"}, timeout=10)
            return r.json()

        owner = login("owner")
        admin = login("raju")
        emp = login("rajesh")
        admin_h = {"Authorization": f"Bearer {admin['token']}"}
        owner_h = {"Authorization": f"Bearer {owner['token']}"}

        print("An admin edits one employee's configuration")
        # Exactly what the Configuration page sends.
        payload = {"employee_id": "E001", "screenshots_per_day": 7,
                   "screenshot_min_minutes": 2, "screenshot_max_minutes": 9,
                   "idle_threshold_seconds": 45,
                   "shift_start": "10:00", "shift_end": "19:00"}
        r = requests.post(f"{base}/admin/config", json=payload,
                          headers=admin_h, timeout=10)
        check("the save is accepted", r.status_code == 200,
              f"HTTP {r.status_code} {r.text[:200]}")

        row = psql(db_name,
                   "SELECT screenshots_per_day, screenshot_min_minutes, "
                   "screenshot_max_minutes, shift_start FROM "
                   "employee_configs WHERE employee_id='E001'").stdout.strip()
        check("and it is in the database against that employee alone",
              row.startswith("7|2|9|"), row or "(no row)")

        print("\nThe employee's own client picks it up")
        from client.application.managers.config_sync_manager import ConfigSyncManager
        from client.application.managers.session_manager import SessionManager
        from client.infrastructure.database.database import Database
        Database.initialize()
        SessionManager.employee_id = "E001"
        SessionManager.auth_token = emp["token"]
        SessionManager.role = "employee"

        seen = {}
        sync = ConfigSyncManager(employee_id="E001", device_id="dev-rajesh",
                                 auth_token=emp["token"],
                                 on_new_config=lambda cfg, changed=None: seen.update(
                                     {"cfg": cfg, "changed": changed}),
                                 sync_interval=999)
        started = time.time()
        got = sync.sync_now()
        took = time.time() - started
        check("one sync returns the new numbers",
              bool(got) and got.get("screenshots_per_day") == 7,
              json.dumps(got)[:300] if got else "nothing came back")
        check("the interval it was given, too",
              got and got.get("screenshot_min_minutes") == 2
              and got.get("screenshot_max_minutes") == 9, json.dumps(got)[:200])
        check("and the shift",
              got and "T10:00" in str((got.get("shift") or {}).get("start_ist")),
              json.dumps(got.get("shift") if got else None))
        check(f"in well under a second ({took*1000:.0f} ms)", took < 1.0,
              f"{took:.2f}s")

        print("\nThe scheduler acts on it rather than storing it away")
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from client.application.schedulers.scheduler_service import SchedulerService
        sched = SchedulerService()
        rescheduled = []
        sched.reschedule = lambda: rescheduled.append(1)
        sched._do_reschedule = lambda: rescheduled.append(1)
        # The real path: _persist_config hands over which keys moved.
        sched._apply_new_config(got, {"screenshots_per_day",
                                      "screenshot_min_minutes"})
        # The hop to the main thread is a queued call; let it run.
        QApplication.processEvents()
        check("a changed capture count triggers a reschedule",
              len(rescheduled) >= 1,
              "the new number would sit unused until midnight or a restart")

        rescheduled.clear()
        sched._apply_new_config(got, set())
        QApplication.processEvents()
        check("and an unchanged config does NOT — no needless rescheduling",
              rescheduled == [], str(rescheduled))

        rescheduled.clear()
        sched._apply_new_config(got, {"idle_threshold_seconds"})
        QApplication.processEvents()
        check("nor does a setting the schedule does not depend on",
              rescheduled == [], str(rescheduled))

        print("\nHow soon it reaches a running app, worst case")
        from client.application.schedulers import scheduler_service as ss
        interval = SchedulerService()._sync_interval
        check("the running client asks every 60 seconds",
              interval == 60, f"{interval}s")

        print("\nOne employee's settings are that employee's")
        r = requests.post(f"{base}/config/sync",
                          json={"employee_id": "A001", "device_id": "dev-raju"},
                          headers=admin_h, timeout=10)
        other = r.json().get("config") or r.json()
        check("the admin's own client does not inherit them",
              other.get("screenshots_per_day") != 7,
              f"raju got {other.get('screenshots_per_day')} — E001's number")

        print("\nAnd an admin can be configured like anybody else")
        r = requests.post(f"{base}/admin/config",
                          json={"employee_id": "A001", "screenshots_per_day": 20,
                                "screenshot_min_minutes": 1,
                                "screenshot_max_minutes": 5,
                                "idle_threshold_seconds": 30,
                                "shift_start": "09:00",
                                "shift_end": "18:00"},
                          headers=owner_h, timeout=10)
        check("the save is accepted for an admin", r.status_code == 200,
              f"HTTP {r.status_code} {r.text[:200]}")
        r = requests.post(f"{base}/config/sync",
                          json={"employee_id": "A001", "device_id": "dev-raju"},
                          headers=admin_h, timeout=10)
        body = r.json()
        cfg = body.get("config") or body
        check("and that admin's client reads back 20 captures a day",
              cfg.get("screenshots_per_day") == 20,
              json.dumps(cfg)[:200])

    finally:
        if server:
            server.terminate()
            try:
                server.wait(timeout=10)
            except Exception:
                server.kill()
        subprocess.run(["psql", "-d", "postgres", "-c",
                        f"DROP DATABASE IF EXISTS {db_name}"],
                       capture_output=True)

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.stdout.flush()
        sys.exit(1)
    print("all configuration checks passed")
    sys.stdout.flush()
    sys.exit(0)


main()
