"""The client half of a password change, against a real server.

test_password.js proves the endpoints. This proves the client does the two
things it must do with the response, both of which fail silently:

  * replace SessionManager.auth_token — the change ends every session tied
    to the old token, including the one the app is holding, so keeping the
    old one turns a successful change into "everything stopped working".
  * rewrite the encrypted token stored for auto-login. That copy is what the
    app presents on its next start. Miss it and nothing looks wrong today;
    the employee just finds themselves at the login screen tomorrow.

Also checks that the forced dialog cannot be dismissed, since that is the
only thing stopping an admin-issued temporary password from becoming
someone's permanent one.

Run:  python3 tests/test_password_client.py
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

DB = f"ets_pwclient_{os.getpid()}"
PORT = 9000 + (os.getpid() % 900)

failures = []


def check(label, ok, detail=""):
    if not ok:
        failures.append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          f"{'' if ok or not detail else f'  — {detail}'}")


def psql(db, sql):
    return subprocess.run(["psql", "-d", db, "-v", "ON_ERROR_STOP=1", "-tAc", sql],
                          capture_output=True, text=True, check=True).stdout.strip()


def infrastructure_missing() -> str | None:
    """Why this test cannot run here, or None if it can.

    It needs a live PostgreSQL and the server's node_modules. Neither is
    present on the build machines, and a test that cannot run should say so
    rather than report a failure that means nothing.
    """
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
        for path in (os.path.join(ROOT, "ets.sql"),
                     os.path.join(ROOT, "server", "migrations",
                                  "2026_08_05_password_management.sql")):
            subprocess.run(["psql", "-d", DB, "-v", "ON_ERROR_STOP=1", "-q", "-f", path],
                           capture_output=True, check=True)

        env = dict(
            os.environ,
            DB_HOST=os.environ.get("PGHOST", "127.0.0.1"),
            DB_PORT=os.environ.get("PGPORT", "5432"),
            DB_NAME=DB,
            DB_USER=os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
            DB_PASSWORD=os.environ.get("PGPASSWORD", "unused-locally"),
            JWT_SECRET="test-secret-not-used-in-production",
            ENCRYPTION_KEY="0" * 64,
            PORT=str(PORT),
        )
        server = subprocess.Popen([
            "node", os.path.join(ROOT, "server", "server.js")],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        base = f"http://127.0.0.1:{PORT}/api"
        for _ in range(100):
            try:
                requests.get(f"{base}/nope", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        else:
            print("server never came up")
            return 1

        # Seed a super admin directly, then create the employee through the
        # API so its password goes through the same path production uses.
        seeded = subprocess.run(
            ["node", "-e",
             "const b=require('bcryptjs');"
             "process.stdout.write(b.hashSync('SuperSecret123',10))"],
            cwd=os.path.join(ROOT, "server"),
            capture_output=True, text=True, check=True).stdout.strip()
        psql(DB, "INSERT INTO employees (employee_id, username, password, role) "
                 f"VALUES ('SA001','superadmin','{seeded}','super_admin')")

        sa = requests.post(f"{base}/auth/login", json={
            "username": "superadmin", "password": "SuperSecret123"}, timeout=10).json()
        requests.post(f"{base}/admin/employees",
                      headers={"Authorization": f"Bearer {sa['token']}"},
                      json={"employee_id": "E001", "username": "emp1",
                            "password": "FirstPass99", "role": "employee"},
                      timeout=10)

        # ── now drive the real client code ──────────────────────────────
        os.environ["ETS_DATA_DIR"] = tempfile.mkdtemp()
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

        from PySide6.QtWidgets import QApplication, QDialog
        QApplication.instance() or QApplication(sys.argv)

        import client.core.config as core_config
        core_config.API_BASE_URL = base
        import client.application.services.auth_service as auth_service
        auth_service.API_BASE_URL = base

        from client.infrastructure.database.database import Database
        Database.initialize()
        from client.application.managers.session_manager import SessionManager
        from client.application.managers.session_log_manager import SessionLogManager
        from client.application.services.auth_service import AuthService

        result = AuthService.login("emp1", "FirstPass99")
        check("client logs in", result.get("success") is True, str(result.get("message")))
        SessionManager.create_session(
            employee_id=result["employee_id"], auth_token=result["token"],
            role=result.get("role", "employee"))
        SessionLogManager.start_session()
        original = SessionManager.auth_token

        stored = SessionLogManager.get_last_session()
        check("the token is stored for auto-login",
              stored["auth_token"] == original)

        # One-second JWT `iat` resolution — without this the replacement
        # token is byte-identical and nothing below can be observed.
        time.sleep(1.1)

        result = AuthService.change_password("WrongPass99", "SecondPass99")
        check("a wrong current password is refused", result.get("success") is not True)
        check("a refused change leaves the token alone",
              SessionManager.auth_token == original)

        result = AuthService.change_password("FirstPass99", "SecondPass99")
        check("the change succeeds", result.get("success") is True,
              str(result.get("message")))
        check("SessionManager holds the new token",
              SessionManager.auth_token not in (None, original))

        stored = SessionLogManager.get_last_session()
        check("the stored auto-login token was refreshed too",
              stored["auth_token"] == SessionManager.auth_token)

        def reachable(token):
            return requests.get(f"{base}/dashboard/me",
                                headers={"Authorization": f"Bearer {token}"},
                                timeout=10).status_code

        check("the new token works", reachable(SessionManager.auth_token) == 200)
        check("the old token is dead", reachable(original) == 401)

        # ── the forced dialog refuses to be dismissed ───────────────────
        from client.presentation.windows.change_password_dialog import (
            ChangePasswordDialog,
        )
        forced = ChangePasswordDialog(forced=True)
        forced.close()
        check("a forced change cannot be closed", forced.isVisible() is False
              and forced.result() != QDialog.DialogCode.Accepted)
        check("a forced change has no Cancel button",
              not any(w.text() == "Cancel" for w in forced.findChildren(type(forced._submit))))
        optional = ChangePasswordDialog()
        check("an ordinary change does offer Cancel",
              any(w.text() == "Cancel" for w in optional.findChildren(type(optional._submit))))

    finally:
        if server:
            server.terminate()
            server.wait(timeout=10)
        psql("postgres", f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")

    print()
    if failures:
        print(f"{len(failures)} failure(s)")
        return 1
    print("all client password checks passed")
    return 0


if __name__ == "__main__":
    print("Password change, client against a real server\n")
    reason = infrastructure_missing()
    if reason:
        print(f"  SKIP  {reason}")
        sys.exit(0)
    sys.exit(main())
