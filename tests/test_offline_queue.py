"""
What happens to work done while the network is gone.

This link drops constantly — around 20% packet loss is normal here, not an
incident — so the queue is not an edge case, it is the ordinary path. Every
capture, every activity line and every idle total is written locally first and
sent afterwards, which means the interesting failures are all silent ones: a
row marked delivered that never arrived, a queue that stops draining, a retry
that sends the same thing twice.

Nothing is mocked except the network being down, which is done by pointing the
client at a port nothing listens on — the same thing the operating system does
to a real client on a dead connection.

Run:  python3 tests/test_offline_queue.py
"""

import base64
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DATA_DIR = tempfile.mkdtemp(prefix="ets_offline_")
os.environ["ETS_DATA_DIR"] = DATA_DIR
os.environ["SCREENSHOT_ENCRYPTION_KEY"] = base64.b64encode(bytes(range(32))).decode()
# Port 9 is discard: reachable, and nothing answers. A dead server, without
# needing one.
os.environ["API_BASE_URL"] = "http://127.0.0.1:9/api"

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))
    sys.stdout.flush()


def main():
    from client.infrastructure.database.database import Database
    from client.application.managers.sync_manager import SyncManager
    from client.application.managers.session_manager import SessionManager
    from client.security.crypto_engine import CryptoEngine
    from client.services.logger_service import LoggerService

    logged = []
    LoggerService.log = lambda m, *a, **k: logged.append(str(m))
    LoggerService.log_verbose = lambda m, *a, **k: logged.append(str(m))

    SessionManager.auth_token = "a-token"
    SessionManager.employee_id = "E001"
    Database.initialize()

    shots_dir = os.path.join(DATA_DIR, "storage", "screenshots")
    os.makedirs(shots_dir, exist_ok=True)

    def queue_shot(sid, with_file=True):
        path = os.path.join(shots_dir, f"{sid}.enc")
        if with_file:
            CryptoEngine.save_encrypted(b"pretend this is a screenshot", path)
        with Database.get_connection() as conn:
            conn.execute(
                "INSERT INTO screenshots (id, employee_id, file_path, timestamp, uploaded)"
                " VALUES (?,?,?,?,0)",
                (sid, "E001", path, "2026-08-08 10:00:00"))
        return path

    def pending():
        with Database.get_connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) c FROM screenshots WHERE uploaded=0").fetchone()["c"]

    print("With the server unreachable")
    for i in range(3):
        queue_shot(f"queued-{i}")
    check("captures are queued, not lost", pending() == 3, str(pending()))

    SyncManager.retry_uploads()
    check("a retry against a dead server leaves them queued",
          pending() == 3, f"{pending()} left")
    check("and the files are still on disk to send later",
          len([f for f in os.listdir(shots_dir) if f.endswith(".enc")]) == 3)
    check("the failure is written down rather than swallowed",
          any("retry" in m.lower() or "error" in m.lower() for m in logged),
          str(logged))

    print("\nA queued file that has disappeared")
    # Antivirus quarantine, a disk cleanup, a half-written file. The row used
    # to be marked delivered with NOTHING logged — the record claimed a
    # success the server never received, and the only symptom was fewer
    # screenshots than expected with nothing to explain it.
    logged.clear()
    queue_shot("vanished", with_file=False)
    before = pending()
    SyncManager.retry_uploads()
    with Database.get_connection() as conn:
        row = conn.execute(
            "SELECT uploaded FROM screenshots WHERE id='vanished'").fetchone()
    check("it stops being retried for ever", row["uploaded"] == 1)
    check("and it SAYS the screenshot was lost",
          any("LOST" in m for m in logged),
          "silently marked delivered — nothing anywhere would explain the gap")
    check("naming which one, so it can be chased",
          any("vanished" in m for m in logged), str(logged))

    print("\nHow much it attempts at a time")
    # Bounded on purpose: a client back after a day should not open twenty
    # uploads at once on a link that is already the bottleneck.
    with Database.get_connection() as conn:
        conn.execute("DELETE FROM screenshots")
    for i in range(10):
        queue_shot(f"batch-{i}")
    attempts = []
    import client.application.managers.sync_manager as sync_mod
    real_post = sync_mod._http.post

    def counting_post(*args, **kwargs):
        attempts.append(1)
        return real_post(*args, **kwargs)

    sync_mod._http.post = counting_post
    try:
        SyncManager.retry_uploads()
        check("one pass tries a bounded number, not everything at once",
              len(attempts) == 5, f"{len(attempts)} uploads attempted")
        check("and the rest stay queued for the next pass",
              pending() == 10, f"{pending()} pending")
    finally:
        sync_mod._http.post = real_post

    print("\nOld entries nobody can ever send")
    with Database.get_connection() as conn:
        conn.execute("DELETE FROM screenshots")
    stale = queue_shot("ancient")
    with Database.get_connection() as conn:
        conn.execute("UPDATE screenshots SET timestamp='2020-01-01 10:00:00'"
                     " WHERE id='ancient'")
    SyncManager.cleanup_old_orphans(days=7)
    with Database.get_connection() as conn:
        left = conn.execute(
            "SELECT COUNT(*) c FROM screenshots WHERE id='ancient'").fetchone()["c"]
    check("a months-old failed capture is cleaned up", left == 0,
          "the queue would grow for ever")
    check("and its file goes with it", not os.path.exists(stale),
          "the row went but the file stayed, filling the disk")

    print("\nActivity lines queue the same way")
    with Database.get_connection() as conn:
        conn.execute("INSERT INTO pending_logs (employee_id, activity, timestamp, uploaded)"
                     " VALUES ('E001','TEST ACTIVITY','2026-08-08 10:00:00',0)")
    SyncManager.retry_logs()
    with Database.get_connection() as conn:
        still = conn.execute(
            "SELECT COUNT(*) c FROM pending_logs WHERE uploaded=0").fetchone()["c"]
    check("an activity line survives a failed send", still == 1, str(still))

    print("\nA failing log must not create another log")
    # THE FEEDBACK LOOP, seen in production. LoggerService.log writes into
    # pending_logs — the very queue retry_logs is draining. So a failure to
    # send a log wrote another log to send, which failed, which wrote another.
    # Twenty failures a pass meant twenty new rows a pass: the queue grew
    # instead of draining, and 900 rows of "retry_logs failed" landed in the
    # company's audit log, climbing every second.
    with Database.get_connection() as conn:
        conn.execute("DELETE FROM pending_logs")
    for i in range(5):
        with Database.get_connection() as conn:
            conn.execute(
                "INSERT INTO pending_logs (employee_id, activity, timestamp, uploaded)"
                " VALUES ('E001', ?, '2026-08-11 10:00:00', 0)", (f"REAL ACTIVITY {i}",))

    def pending_log_count():
        with Database.get_connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) c FROM pending_logs WHERE uploaded=0").fetchone()["c"]

    before = pending_log_count()
    SyncManager.retry_logs()
    after = pending_log_count()
    check("a failed send does not add to the queue it is draining",
          after <= before,
          f"{before} became {after} — every failure wrote another log to fail")

    for _ in range(3):
        SyncManager.retry_logs()
    check("and it still does not after several passes",
          pending_log_count() <= before,
          f"{pending_log_count()} pending after four passes, started at {before}")

    check("nothing about the failure reached the uploadable queue",
          not any("retry_logs failed" in (r["activity"] or "")
                  for r in Database.connect().execute(
                      "SELECT activity FROM pending_logs").fetchall()),
          "the failure message is queued for upload — that is the loop")

    print("\nIdle totals")
    with Database.get_connection() as conn:
        tables = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    check("the local schema has somewhere to keep them",
          any("idle" in t for t in tables), str(tables))
    crashed = False
    try:
        SyncManager.push_idle_totals()
    except Exception as error:
        crashed = True
        detail = str(error)
    check("pushing them against a dead server does not raise", not crashed,
          detail if crashed else "")

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.stdout.flush()
        sys.exit(1)
    print("all offline queue checks passed")
    sys.stdout.flush()
    sys.exit(0)


try:
    main()
finally:
    shutil.rmtree(DATA_DIR, ignore_errors=True)
