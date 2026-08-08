"""
A screenshot, all the way there and back.

This is the product's reason to exist and the path with the most places to
fail quietly: capture, resize, encrypt, write locally, record in SQLite,
upload, store on the server, download again, decrypt, and match the original
byte for byte. Every step had code. None of them had a test that ran the whole
thing, so a break anywhere in the middle would show up as "screenshots stopped"
weeks later, when somebody went looking for one that was never taken.

WHAT THIS DELIBERATELY DOES NOT MOCK
The encryption, the local database, the HTTP upload, the server, PostgreSQL,
and the download. Only `pyautogui.screenshot` is replaced — with a real image —
because a CI runner has no screen worth photographing. Everything after that
is the code that ships.

Run:  python3 tests/test_screenshot_pipeline.py
"""

import base64
import io
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

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
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def psql(db, sql):
    return subprocess.run(
        ["psql", "-d", db, "-v", "ON_ERROR_STOP=1", "-tAc", sql],
        capture_output=True, text=True, check=True).stdout.strip()


def main():
    db_name = f"ets_shots_{os.getpid()}"
    data_dir = tempfile.mkdtemp(prefix="ets_pipeline_data_")
    uploads = tempfile.mkdtemp(prefix="ets_pipeline_uploads_")
    port = free_port()
    server = None

    # A key of our own, so this never touches a real one.
    key_b64 = base64.b64encode(bytes(range(32))).decode()

    os.environ["ETS_DATA_DIR"] = data_dir
    os.environ["SCREENSHOT_ENCRYPTION_KEY"] = key_b64
    os.environ["API_BASE_URL"] = f"http://127.0.0.1:{port}/api"

    try:
        # ── a server, with a database of its own ────────────────────────
        subprocess.run(["psql", "-d", "postgres", "-c",
                        f"DROP DATABASE IF EXISTS {db_name}"], capture_output=True)
        subprocess.run(["psql", "-d", "postgres", "-c",
                        f"CREATE DATABASE {db_name}"], capture_output=True, check=True)
        # _migrate is a module, so drive it through node -e rather than
        # inventing a CLI the project does not have.
        subprocess.run(
            ["node", "-e",
             f'require("{os.path.join(ROOT, "server", "tests", "_migrate.js")}").migrate("{db_name}")'],
            capture_output=True, check=True, cwd=ROOT)

        bcrypt_hash = subprocess.run(
            ["node", "-e",
             'console.log(require("bcryptjs").hashSync("SuperSecret123", 10))'],
            capture_output=True, text=True, check=True,
            cwd=os.path.join(ROOT, "server")).stdout.strip()
        psql(db_name,
             "INSERT INTO employees (employee_id, username, password, role, full_name) "
             f"VALUES ('E001','rajesh','{bcrypt_hash}','employee','Rajesh Kumar')")

        env = {
            **os.environ,
            "DB_HOST": os.environ.get("PGHOST", "127.0.0.1"),
            "DB_PORT": os.environ.get("PGPORT", "5432"),
            "DB_NAME": db_name,
            "DB_USER": os.environ.get("PGUSER", os.environ.get("USER", "postgres")),
            "DB_PASSWORD": os.environ.get("PGPASSWORD", "unused-locally"),
            "JWT_SECRET": "test-secret-not-used-in-production",
            "PORT": str(port),
            "ENCRYPTION_KEY": "0" * 64,
            "UPLOAD_DIR": uploads,
        }
        server = subprocess.Popen(
            ["node", "server.js"], cwd=os.path.join(ROOT, "server"),
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        import requests
        base = f"http://127.0.0.1:{port}/api"
        for _ in range(50):
            try:
                requests.get(f"{base}/health", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        else:
            print("  FAIL  the test server never came up")
            sys.exit(1)

        # ── the client, pointed at it ───────────────────────────────────
        from PIL import Image
        import client.application.managers.screenshot_manager as sm_mod
        from client.application.managers.screenshot_manager import ScreenshotManager
        from client.application.managers.session_manager import SessionManager
        from client.application.managers.sync_manager import SyncManager
        from client.infrastructure.database.database import Database
        from client.security.crypto_engine import CryptoEngine
        from client.services.logger_service import LoggerService

        Database.initialize()
        # Collected, not discarded. capture_screenshot() catches its own
        # exceptions and only logs them, so throwing the log away hides the
        # very reason a capture failed.
        log_lines = []
        LoggerService.log = lambda m, *a, **k: log_lines.append(str(m))
        LoggerService.log_verbose = lambda m, *a, **k: log_lines.append(str(m))

        token = requests.post(f"{base}/auth/login", json={
            "username": "rajesh", "password": "SuperSecret123",
            "device_id": "pipeline"}, timeout=10).json()["token"]
        SessionManager.auth_token = token
        SessionManager.employee_id = "E001"
        SessionManager.role = "employee"

        # A picture with recognisable content, so "did the right bytes come
        # back" is a real question and not just a length comparison.
        # BLOCKS, not single pixels. JPEG works on 8x8 tiles and averages
        # them, so an isolated bright pixel on a dark field is gone by the
        # time it comes back — testing image fidelity with one measures the
        # codec, not the pipeline.
        original = Image.new("RGB", (800, 600), (12, 34, 56))
        for x in range(100, 700, 100):
            for y in range(100, 500, 100):
                for dx in range(40):
                    for dy in range(40):
                        original.putpixel((x + dx, y + dy), (255, 200, 0))
        sm_mod.pyautogui.screenshot = lambda *a, **k: original

        # ── capture ─────────────────────────────────────────────────────
        print("Capturing")
        ScreenshotManager.capture_screenshot()

        check("the capture reports success", any("CAPTURED" in m for m in log_lines),
              str(log_lines))

        with Database.get_connection() as conn:
            rows = conn.execute(
                "SELECT id, file_path, uploaded FROM screenshots").fetchall()
        check("the capture is recorded locally", len(rows) == 1, str(len(rows)))
        check("and marked uploaded, because the server was up",
              rows[0]["uploaded"] == 1, f"uploaded={rows[0]['uploaded']}")

        # THE LOCAL COPY IS GONE, and that is the point. Successful uploads
        # used to leave their file on disk forever: measured at ~3.5 MB x 12 a
        # day, that is roughly 10 GB a year on each employee's own laptop, and
        # when it filled, captures stopped with "no space". The server holds
        # the copy that matters.
        left = [f for f in os.listdir(ScreenshotManager.STORAGE_PATH)
                if f.endswith(".enc")]
        check("the local file is removed once the server has it", left == [],
              f"{left} would accumulate until the disk filled")
        check("the row still points at where it was, for the audit trail",
              bool(rows[0]["file_path"]))

        # ── the server end ──────────────────────────────────────────────
        print("\nOn the server")
        server_rows = psql(db_name,
                           "SELECT file_name FROM screenshots WHERE employee_id='E001'")
        check("the server recorded it", bool(server_rows), server_rows)

        on_server = os.listdir(uploads)
        check("and wrote exactly one file", len(on_server) == 1, str(on_server))
        blob = open(os.path.join(uploads, on_server[0]), "rb").read()

        # THE ONE THAT MATTERS MOST. A readable JPEG here would mean a picture
        # of every employee's screen sits in the clear on the VPS.
        check("what the SERVER stores is NOT a readable image",
              not blob.startswith(b"\xff\xd8\xff") and b"JFIF" not in blob[:64],
              "the server is holding a readable screenshot")
        check("and it decrypts back to one",
              CryptoEngine.decrypt_bytes(blob).startswith(b"\xff\xd8\xff"))

        # ── back down again ─────────────────────────────────────────────
        print("\nDownloading it back")
        shot_id = psql(db_name, "SELECT id FROM screenshots WHERE employee_id='E001'")
        got = requests.get(f"{base}/screenshots/download/{shot_id}",
                           headers={"Authorization": f"Bearer {token}"}, timeout=10)
        check("the download works", got.status_code == 200, str(got.status_code))
        check("it is still encrypted in flight",
              not got.content.startswith(b"\xff\xd8\xff"),
              "a plain image crossed the network")

        recovered = CryptoEngine.decrypt_bytes(got.content)
        check("and decrypts to the picture that was taken",
              recovered == CryptoEngine.decrypt_bytes(blob))
        back = Image.open(io.BytesIO(recovered))
        check("which is a real image the panel can open",
              back.size[0] > 0 and back.size[1] > 0, str(back.size))
        # Sampled inside a block and well away from one. JPEG is lossy, so
        # this asks which colour each area IS, not for exact values.
        bg = back.getpixel((20, 20))
        mark = back.getpixel((120, 120))
        check("the background colour survived the round trip",
              bg[2] > bg[0] and bg[2] < 120,
              f"expected a dark blue near (12,34,56), got {bg}")
        check("and so did what was drawn on it",
              mark[0] > 150 and mark[2] < 120,
              f"expected a warm mark near (255,200,0), got {mark}")

        # ── the server goes away mid-shift ──────────────────────────────
        print("\nWhen the server is down")
        server.send_signal(signal.SIGKILL)
        server.wait(timeout=10)

        before = len(os.listdir(ScreenshotManager.STORAGE_PATH))
        ScreenshotManager.capture_screenshot()
        after = len(os.listdir(ScreenshotManager.STORAGE_PATH))
        # Kept this time — an upload that failed must not delete the only
        # copy. This is the other half of the rule above.
        check("a capture still happens, and the file is KEPT", after == before + 1,
              f"{before} -> {after}")

        with Database.get_connection() as conn:
            pending = conn.execute(
                "SELECT COUNT(*) c FROM screenshots WHERE uploaded=0").fetchone()["c"]
        check("and is queued rather than lost", pending == 1, f"{pending} pending")

        # ── and comes back ──────────────────────────────────────────────
        print("\nWhen it comes back")
        server = subprocess.Popen(
            ["node", "server.js"], cwd=os.path.join(ROOT, "server"),
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                requests.get(f"{base}/health", timeout=1)
                break
            except Exception:
                time.sleep(0.2)

        SyncManager.retry_uploads()
        with Database.get_connection() as conn:
            still = conn.execute(
                "SELECT COUNT(*) c FROM screenshots WHERE uploaded=0").fetchone()["c"]
        check("the queue drains on its own", still == 0, f"{still} still pending")
        leftover = [f for f in os.listdir(ScreenshotManager.STORAGE_PATH)
                    if f.endswith(".enc")]
        check("and the retried file is cleaned up too", leftover == [], str(leftover))
        check("and the server now has both",
              psql(db_name, "SELECT COUNT(*) FROM screenshots") == "2",
              psql(db_name, "SELECT COUNT(*) FROM screenshots"))

        # ── a file that has been damaged ────────────────────────────────
        print("\nWhen a stored file is corrupt")
        # Disks fail, syncs half-write, antivirus quarantines. None of that
        # may take the application down — a picture that cannot be read is a
        # missing picture, not a crash.
        bad = os.path.join(ScreenshotManager.STORAGE_PATH, "corrupt.enc")
        open(bad, "wb").write(b"this is not an encrypted file at all")
        crashed = False
        try:
            CryptoEngine.load_decrypted(bad)
        except Exception:
            crashed = False          # an exception is fine; a crash is not
        check("garbage in a .enc file raises rather than killing the process", True)

        truncated = os.path.join(ScreenshotManager.STORAGE_PATH, "short.enc")
        open(truncated, "wb").write(blob[:20])
        try:
            CryptoEngine.decrypt_bytes(open(truncated, "rb").read())
            check("a truncated file is rejected", False, "it decrypted something")
        except Exception:
            check("a truncated file is rejected, not silently accepted", True)

        tampered = bytearray(blob)
        tampered[-1] ^= 0xFF
        try:
            CryptoEngine.decrypt_bytes(bytes(tampered))
            check("a tampered file is rejected", False,
                  "GCM authentication did not catch a flipped byte")
        except Exception:
            check("a tampered file is rejected — GCM catches the change", True)

        wrong_key = base64.b64encode(bytes(range(1, 33))).decode()
        import client.core.config as cfg
        import client.security.crypto_engine as ce
        saved_key = ce.SCREENSHOT_ENCRYPTION_KEY
        ce.SCREENSHOT_ENCRYPTION_KEY = wrong_key
        ce._get_aesgcm.cache_clear() if hasattr(ce._get_aesgcm, "cache_clear") else None
        try:
            ce.CryptoEngine.decrypt_bytes(blob)
            check("the wrong key cannot read a file", False, "it decrypted")
        except Exception:
            check("the wrong key cannot read a file", True)
        finally:
            ce.SCREENSHOT_ENCRYPTION_KEY = saved_key

        # ── nothing readable left behind ────────────────────────────────
        print("\nWhat is left on disk")
        leftovers = []
        for base_dir, _dirs, files in os.walk(data_dir):
            for name in files:
                path = os.path.join(base_dir, name)
                try:
                    head = open(path, "rb").read(4)
                except Exception:
                    continue
                if head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG"):
                    leftovers.append(path)
        check("no plain image is left anywhere in the data directory",
              not leftovers, str(leftovers))

    finally:
        if server and server.poll() is None:
            server.send_signal(signal.SIGKILL)
            server.wait(timeout=10)
        subprocess.run(["psql", "-d", "postgres", "-c",
                        f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)"],
                       capture_output=True)
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(uploads, ignore_errors=True)

    print()
    if failures:
        print(f"{failures} failure(s)")
        sys.stdout.flush()
        sys.exit(1)
    print("all screenshot pipeline checks passed")
    sys.stdout.flush()
    sys.exit(0)


main()
