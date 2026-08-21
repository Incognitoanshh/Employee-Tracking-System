"""
Logging out must close the shift on the SERVER, local record or not.

THE BUG THIS EXISTS FOR, seen on 21 August 2026 on two accounts at once.
Both had just installed a fresh build, signed in around midnight, signed out
a minute later — and the attendance page went on showing "Incomplete" for
both, for ever.

ShiftManager.end_shift() looked for an open shift in the client's own SQLite
`shifts` table and, finding none, returned. The server was never told. And a
local row is exactly what a machine does NOT have the first time somebody
runs the app: a new install, a new machine, or a database that was rebuilt
after corruption.

The truth about a shift lives on the server. The local table is bookkeeping,
and its absence is not evidence that the shift is still running.

Run:  python3 tests/test_shift_close.py
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if not ok and detail else ""))
    if not ok:
        failures += 1


workspace = tempfile.mkdtemp(prefix="ets-shift-")
os.environ["ETS_DATA_DIR"] = workspace
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from client.application.managers import shift_manager as sm      # noqa: E402
from client.application.managers.session_manager import SessionManager  # noqa: E402
from client.infrastructure.database.database import Database     # noqa: E402

Database.initialize()
SessionManager.employee_id = "E001"
SessionManager.auth_token = "test-token"

told = []


def fake_post(url, **kwargs):
    told.append((url, kwargs.get("json") or {}))

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {"success": True}
    return _Response()


sm._http.post = fake_post

print("A machine with no local shift row — a fresh install")
with Database.get_connection() as conn:
    rows = conn.execute(
        "SELECT COUNT(*) FROM shifts WHERE employee_id = ? AND logout_time IS NULL",
        ("E001",)).fetchone()[0]
check("there is nothing recorded locally", rows == 0, f"{rows} rows")

sm.ShiftManager.end_shift()
check("the server is still told the shift ended",
      any("/attendance/logout" in url for url, _body in told),
      "signing out left the shift open on the server for ever")
check("and it is told which employee",
      any(body.get("employee_id") == "E001" for _url, body in told),
      str(told))

print("\nA machine that DOES have one still works as it did")
told.clear()
with Database.get_connection() as conn:
    conn.execute(
        "INSERT INTO shifts (employee_id, login_time) VALUES (?, ?)",
        ("E001", "2026-08-21 09:00:00"))

sm.ShiftManager.end_shift()
check("the server is told", any("/attendance/logout" in url for url, _b in told))
check("with the duration it worked out",
      any("total_hours" in body for _u, body in told), str(told))

with Database.get_connection() as conn:
    still_open = conn.execute(
        "SELECT COUNT(*) FROM shifts WHERE employee_id = ? AND logout_time IS NULL",
        ("E001",)).fetchone()[0]
check("and the local row is closed too", still_open == 0, f"{still_open} left open")

shutil.rmtree(workspace, ignore_errors=True)
print("\nall shift close checks passed" if failures == 0 else f"\n{failures} FAILED")
sys.exit(0 if failures == 0 else 1)
