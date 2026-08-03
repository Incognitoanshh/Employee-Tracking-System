"""
Single-instance guard — ek waqt me app ki sirf EK copy chalni chahiye.

BUG FIX (production blocker): pehle ye module top-level pe `import fcntl`
karta tha. `fcntl` sirf Unix pe exist karta hai — Windows ke Python me ye
module hai hi nahi. main.py isko module level pe import karta hai, is liye
WINDOWS BUILD launch hote hi ModuleNotFoundError se crash ho jaati thi
(app kabhi khulti hi nahi thi). Ab dono platforms handle hote hain:
Windows pe `msvcrt.locking`, macOS/Linux pe `fcntl.flock`.

Doosra fix: debug log path pehle hardcoded macOS path tha
("~/Library/Application Support/ETS/..."), is liye Windows/Linux pe
single-instance ka koi diagnostic kabhi likha hi nahi jaata tha. Ab wahi
STORAGE_DIR use hota hai jo baaki poori app use karti hai.
"""

import atexit
import os
import sys
from datetime import datetime

from client.core.config import STORAGE_DIR

if sys.platform.startswith("win"):
    import msvcrt
else:
    import fcntl

LOCK_FILE = os.path.join(STORAGE_DIR, "ets_app.lock")
DEBUG_LOG = os.path.join(STORAGE_DIR, "app.log")

_lock_fd = None


def _debug(msg):
    try:
        os.makedirs(STORAGE_DIR, exist_ok=True)
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"SingleInstanceLock: {msg}\n"
            )
    except Exception:
        pass


def _try_acquire(fd) -> bool:
    """Non-blocking exclusive lock. True if acquired, False if held elsewhere."""
    try:
        if sys.platform.startswith("win"):
            # msvcrt locks a byte RANGE, not the whole file, so we lock a
            # fixed byte (offset 0). The OS releases the lock as soon as the
            # process exits, so a crash never leaves a stale lock behind.
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (IOError, OSError):
        return False


def _release(fd) -> None:
    try:
        if sys.platform.startswith("win"):
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass


def ensure_single_instance():
    global _lock_fd
    pid = os.getpid()
    _debug(f"attempt by PID {pid}, lock file = {LOCK_FILE}")

    try:
        os.makedirs(STORAGE_DIR, exist_ok=True)
        _lock_fd = open(LOCK_FILE, "a+")
    except Exception as e:
        # If the lock file cannot be opened at all (permissions, read-only
        # disk), do not block the app. Single-instance is a convenience, not
        # a hard requirement — failing here would leave the employee with a
        # completely dead app and no tracking at all.
        _debug(f"PID {pid} could not open lock file ({e}) — continuing without lock.")
        return

    if not _try_acquire(_lock_fd):
        _debug(f"PID {pid} BLOCKED — another instance holds the lock. Exiting.")
        try:
            _lock_fd.close()
        except Exception:
            pass
        _lock_fd = None
        sys.exit(0)

    try:
        # On Windows byte 0 is currently locked, so write past it —
        # overwriting our own locked byte would raise an error.
        _lock_fd.seek(1)
        _lock_fd.truncate(1)
        _lock_fd.write(str(pid))
        _lock_fd.flush()
    except Exception:
        pass

    _debug(f"PID {pid} ACQUIRED lock — proceeding as primary instance.")
    atexit.register(_release_lock)


def _release_lock():
    global _lock_fd
    pid = os.getpid()
    if _lock_fd:
        _release(_lock_fd)
        try:
            _lock_fd.close()
        except Exception:
            pass
        _lock_fd = None
    _debug(f"PID {pid} released lock on exit.")
