import os
import sys
import fcntl
import atexit
from datetime import datetime

LOCK_FILE = os.path.join(os.path.expanduser("~"), ".ets_app.lock")
DEBUG_LOG = os.path.expanduser("~/Library/Application Support/ETS/storage/app.log")

_lock_fd = None

def _debug(msg):
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] SingleInstanceLock: {msg}\n")
    except Exception:
        pass

def ensure_single_instance():
    global _lock_fd
    pid = os.getpid()
    _debug(f"attempt by PID {pid}, lock file = {LOCK_FILE}")

    _lock_fd = open(LOCK_FILE, "a+")
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError) as e:
        _debug(f"PID {pid} BLOCKED — another instance holds the lock ({e}). Exiting.")
        sys.exit(0)

    _lock_fd.seek(0)
    _lock_fd.truncate()
    _lock_fd.write(str(pid))
    _lock_fd.flush()
    _debug(f"PID {pid} ACQUIRED lock — proceeding as primary instance.")

    atexit.register(_release_lock)

def _release_lock():
    global _lock_fd
    pid = os.getpid()
    if _lock_fd:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            _lock_fd.close()
        except Exception:
            pass
    _debug(f"PID {pid} released lock on exit.")
