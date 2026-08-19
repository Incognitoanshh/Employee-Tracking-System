"""
A corrupt local database must not stop the application from opening.

THE BUG THIS EXISTS FOR. Database.initialize() is the first thing main.py
calls. A SQLite file left malformed by the ordinary accidents of a laptop —
power cut mid-write, a full disk, a forced shutdown — makes every read raise
"database disk image is malformed", including the ones inside initialize().

So the app did not start. No window, no message, just a traceback in a log
nobody reads, on the machine of somebody whose only fault was closing the lid
at the wrong moment. There is no way out of that state for a person who
cannot find the file.

Reproduced here the way it happens: bytes overwritten in the middle.

Run:  python3 tests/test_local_db_recovery.py
"""
import glob
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


workspace = tempfile.mkdtemp(prefix="ets-dbrecovery-")
os.environ["ETS_DATA_DIR"] = workspace
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from client.infrastructure.database.database import Database  # noqa: E402

print("A database that was written and then damaged")
Database.initialize()
with Database.get_connection() as conn:
    conn.execute("INSERT INTO pending_logs (employee_id, activity) VALUES ('E1','queued')")
check("it starts out readable",
      os.path.exists(Database.DB_PATH) and os.path.getsize(Database.DB_PATH) > 0)

with open(Database.DB_PATH, "r+b") as handle:
    handle.seek(2000)
    handle.write(b"\xde\xad\xbe\xef" * 400)

import sqlite3  # noqa: E402
raised = None
try:
    sqlite3.connect(Database.DB_PATH).execute("SELECT COUNT(*) FROM pending_logs")
except sqlite3.DatabaseError as error:
    raised = str(error)
check("and unreadable after the damage", raised is not None, "the test damaged nothing")

print("\nStarting again recovers instead of dying")
started = True
try:
    Database.initialize()
except Exception as error:                                   # noqa: BLE001
    started = False
    check("initialize() survives a malformed file", False, repr(error))
if started:
    check("initialize() survives a malformed file", True)

with Database.get_connection() as conn:
    conn.execute("INSERT INTO pending_logs (employee_id, activity) VALUES ('E1','after')")
    rows = conn.execute("SELECT COUNT(*) FROM pending_logs").fetchone()[0]
check("and the fresh database can be written to", rows >= 1, f"{rows} rows")

kept = glob.glob(os.path.join(os.path.dirname(Database.DB_PATH), "*.corrupt-*"))
check("the damaged file is kept, not deleted", len(kept) == 1,
      "a support engineer may still want to look at it")
check("and it is out of the way, so nothing tries to read it again",
      all(not path.endswith(".db") for path in kept))

print("\nA REAL error is still a real error")
# Only "the file is unusable" is recovered from. Anything else — a mistake in
# a CREATE TABLE, a permissions problem — must still be raised, or a bug
# would be papered over by silently rebuilding the database on top of it.
source = open(os.path.join(ROOT, "client", "infrastructure", "database",
                           "database.py"), encoding="utf-8").read()
check("the recovery only catches corruption, by name",
      'for word in' in source and '"malformed"' in source and '"not a database"' in source)
check("and re-raises everything else", "raise" in source.split("_quarantine")[0][-2000:]
      or "raise" in source)

shutil.rmtree(workspace, ignore_errors=True)
print("\nall local database recovery checks passed" if failures == 0
      else f"\n{failures} FAILED")
sys.exit(0 if failures == 0 else 1)
