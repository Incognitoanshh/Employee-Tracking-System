"""
The calls that only work on a real Windows or a real Mac — actually called.

WHY THIS FILE EXISTS. Every other test replaces these with a stand-in, because
`OpenInputDesktop` and `CGSessionCopyCurrentDictionary` cannot run on the
machine the suite is usually run on. That is the right way to test the logic
around them, and it is blind to one whole class of break: the call itself.

It happened. The macOS lock check named a module-level `Quartz` this file does
not have, so on every real Mac it raised NameError, the caller swallowed it,
the lock check silently never fired — and the six tests written for it went on
passing, because each one replaces that function. Nothing in a stubbed suite
can see that. Only running it can.

So: no stubs at all in here. Every call below is the shipping code, talking to
the operating system the runner is actually on, and what is checked is the
contract the rest of the product relies on —

  * the calls return, and return the shape they promise;
  * a capture either produces a real image, or fails in a way the product
    turns into a sentence an administrator can act on and a correct decision
    about whether to try again;
  * "how long since the last input" answers a number.

A CAPTURE THAT IS REFUSED IS NOT A FAILURE OF THIS TEST. A CI runner has no
signed-in person at a screen: Windows may hand back a black desktop and macOS
will refuse the screen outright. Both are correct behaviour to verify — what
must not happen is a crash, a silent nothing, or an unclassified error.

Run:  python3 tests/test_real_screen_smoke.py
"""

import os
import platform
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ETS_DATA_DIR", tempfile.mkdtemp(prefix="ets_smoke_"))
os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:9/api")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMP = tempfile.mkdtemp(prefix="ets_smoke_db_")
import client.core.config as config                                          # noqa: E402
config.STORAGE_DIR = _TMP
from client.infrastructure.database import database as database_module       # noqa: E402
database_module.Database.DB_PATH = os.path.join(_TMP, "ets.db")
database_module.Database.initialize()

failures = 0
SYSTEM = platform.system()


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + ("" if ok or not detail else f"  — {detail}"))
    sys.stdout.flush()


print(f"\nOn a real {SYSTEM}, with nothing replaced\n")

from client.application.managers import screenshot_manager as sm             # noqa: E402

# ── HOW MANY SCREENS ──────────────────────────────────────────────────────
#
# Wrong, this is how an employee with two monitors is monitored on one of them
# and the other half of their day does not exist — which is what it did.
count = sm._screen_count()
check("the machine can say how many displays it has",
      isinstance(count, int) and count >= 1, repr(count))

# ── IS THERE ANYTHING ON THE SCREEN TO COPY ───────────────────────────────
ready, why = sm._desktop_ready()
check("and whether there is anything on them worth copying",
      isinstance(ready, bool) and isinstance(why, str), f"{ready!r} {why!r}")
check("with a reason whenever the answer is no",
      ready or why.strip() != "", f"{ready!r} {why!r}")
print(f"      it answered: ready={ready}"
      + (f", because {why}" if why else ""))

if SYSTEM == "Windows":
    # THE TWO CALLS THE REST OF THE SUITE CAN ONLY PRETEND TO MAKE.
    state = sm._win_session_state()
    check("Windows says what kind of session this is",
          isinstance(state, int), repr(state))
    name = sm._win_input_desktop_name()
    check("and which desktop is in front, or that it will not say",
          name is None or isinstance(name, str), repr(name))
    print(f"      session state {state}, input desktop {name!r}")

if SYSTEM == "Darwin":
    # THE CALL THAT WAS BROKEN, checked against macOS's own answer rather than
    # against an expectation.
    #
    # A build machine may have no console session at all, and then there is
    # nothing to hand over — which must not read as a failure. But "no session"
    # and "our function is broken" both come back as None, so the comparison is
    # with the same question asked directly here: if macOS answers and the
    # product does not, that is the bug, whatever the machine is.
    direct, direct_error = None, None
    try:
        import Quartz as _Quartz
        direct = dict(_Quartz.CGSessionCopyCurrentDictionary() or {}) or None
    except Exception as raised:                                  # noqa: BLE001
        direct_error = raised
    check("Quartz is importable and has the session call — the NameError case",
          direct_error is None, f"{type(direct_error).__name__}: {direct_error}")

    info = sm._mac_session_info()
    check("and whatever macOS answers reaches the product unchanged",
          (direct is None and not info) or (isinstance(info, dict) and info == direct),
          f"product={str(info)[:80]} direct={str(direct)[:80]}")
    if direct is None:
        print("      this machine has no console session — nothing to hand over")
    else:
        check("the session says whether somebody is at the screen",
              "kCGSSessionOnConsoleKey" in info, str(info)[:120])
        print(f"      on console={info.get('kCGSSessionOnConsoleKey')},"
              f" locked={bool(info.get('CGSSessionScreenIsLocked'))}")

# ── THE CAPTURE ITSELF ────────────────────────────────────────────────────
#
# The shipping path, including the multi-display branch. Either an image comes
# back or something is raised — and what is checked about the raise is that the
# product turns it into a sentence and a correct decision, because an
# administrator looking at a day with no screenshots in it reads that sentence.
print("\nThe capture, for real")

image, error = None, None
try:
    image = sm._grab_every_screen()
except Exception as raised:                                  # noqa: BLE001
    error = raised

if image is not None:
    width, height = image.size
    check("a picture came back", width > 0 and height > 0, f"{width}x{height}")
    # A ONE-PIXEL IMAGE IS NOT A SCREEN. Something answered, but not with a
    # screen — worth knowing, because it would encrypt and upload perfectly.
    check("and it is the size of a screen, not a placeholder",
          width >= 640 and height >= 400, f"{width}x{height}")
    print(f"      {width}x{height} from {count} display(s)")

    # AND IT SURVIVES THE PIPELINE. Encrypt, write, read back, decrypt — the
    # same steps the real capture takes, on this platform's own bytes.
    import io                                                # noqa: E402
    from client.security.crypto_engine import CryptoEngine    # noqa: E402

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=60)
    original = buffer.getvalue()
    path = os.path.join(_TMP, "smoke.enc")
    CryptoEngine.save_encrypted(original, path)
    check("it encrypts to a file that is not the picture",
          os.path.exists(path) and open(path, "rb").read()[:3] != original[:3])
    check("and decrypts back to exactly the bytes that went in",
          CryptoEngine.load_decrypted(path) == original)
else:
    print(f"      refused: {type(error).__name__}: {error}")
    hint, again = sm._capture_failure_hint(error, sys.platform, False)
    check("a refusal is explained in words, not in a stack trace",
          isinstance(hint, str) and len(hint.strip()) > 20, repr(hint)[:160])
    check("and carries a decision about trying again",
          isinstance(again, bool), repr(again))
    # NOT A FAILURE OF THIS TEST. A runner has nobody signed in at a screen;
    # what matters is that it was classified rather than crashing.
    print(f"      classified, retry={again}")

# ── HOW LONG SINCE THE LAST INPUT ─────────────────────────────────────────
#
# The idle tracker's only question, asked of the operating system. On Windows
# this is GetLastInputInfo + GetTickCount; on macOS it is Quartz. Neither has
# ever been run by the suite on the platform it belongs to.
print("\nThe idle clock, for real")

from client.application.managers.idle_tracker import IdleTracker             # noqa: E402

idle_seconds = IdleTracker()._get_idle_seconds()
check("the machine says how long since the last input",
      isinstance(idle_seconds, float) and idle_seconds >= 0, repr(idle_seconds))
check("and it is not a week — which would mean the units are wrong",
      idle_seconds < 7 * 24 * 3600, repr(idle_seconds))
print(f"      {idle_seconds:.1f}s since the last keyboard or mouse event")

# ── WHAT KIND OF MINUTE IT IS ─────────────────────────────────────────────
#
# The three doors the activity scorer looks through. On Windows these are
# GetAsyncKeyState, GetCursorPos and GetForegroundWindow, none of which the
# suite has ever called on Windows.
print("\nThe counters the scoring reads")

from client.application.managers import activity_tracker as at               # noqa: E402

counters = at.read_counters(None)
check("the running input totals are a dictionary of numbers, or nothing",
      counters is None
      or (isinstance(counters, dict)
          and all(isinstance(counters.get(k), int)
                  for k in ("keystrokes", "clicks", "scrolls", "mouse_moves"))),
      str(counters)[:140])

stamp = at.last_input_ms()
check("the last input has a timestamp in milliseconds, or none",
      stamp is None or (isinstance(stamp, int) and stamp > 10 ** 12), repr(stamp))

window = at.foreground_window()
check("and the front window has a name, or an empty string",
      isinstance(window, str), repr(window))
print(f"      counters={'yes' if counters else 'unavailable'},"
      f" last input={'yes' if stamp else 'unavailable'},"
      f" front window={window[:40]!r}")

# NOTHING HERE MAY BE UNAVAILABLE ON THE TWO PLATFORMS THIS SHIPS TO. Linux
# is a development convenience, and the suite runs there — but a Windows or a
# Mac answering "unavailable" means the scoring has nothing to score, which is
# the silent failure this whole file exists to catch.
if SYSTEM in ("Windows", "Darwin"):
    # A BUILD MACHINE WITH NOTHING ON ITS SCREEN can fail to answer these, and
    # that is the machine, not the code. On anything with a session — every
    # employee laptop this ships to — "unavailable" means the scoring has
    # nothing to score, silently, which is the failure this file exists for.
    has_session = ready or SYSTEM == "Windows"
    if has_session:
        check("on a platform this ships to, the counters are actually available",
              counters is not None, "read_counters returned None")
        check("and so is the time of the last input",
              stamp is not None, "last_input_ms returned None")
    else:
        print("      no screen session here, so the counters are not required")

print(f"\n{'ALL PASS' if not failures else str(failures) + ' FAILED'}\n")

sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if failures else 0)
