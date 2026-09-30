"""
What the last minute of input actually looked like.

WHY THIS EXISTS BESIDE IdleTracker AND NOT INSIDE IT. That one answers a
single question — how long since the last input — and answers it well. This
one asks a different question: what KIND of input, in what rhythm, and did
anything on the screen change because of it. Folding the second into the
first would have made a file that does two jobs and tests neither.

NO NEW PERMISSION. Everything read here is available to any process on both
platforms:

    macOS    CGEventSourceCounterForEventType gives an exact running count
             per event type — keystrokes, clicks, scrolls, movements — and
             CGEventSourceSecondsSinceLastEventType gives the moment of the
             last one. NSWorkspace names the frontmost application.
    Windows  GetAsyncKeyState reports which keys and buttons have been
             pressed since it was last asked; GetCursorPos shows movement;
             GetLastInputInfo gives the tick of the last input; and
             GetForegroundWindow says which window is in front.

THE PLATFORM CALLS ARE THREE SMALL FUNCTIONS so the minute's arithmetic can
be tested without a keyboard, a mouse or a particular operating system —
which is the only way this could be tested at all on the machine it is
written on.

ONE SAMPLE A SECOND. Enough to catch a jiggler's rhythm (they rarely move
more slowly than once a second) and cheap enough to leave running all day: a
few counter reads and one key-state sweep.
"""

from __future__ import annotations

import platform
import time

from PySide6.QtCore import QObject, QTimer, Signal

from client.application.managers.activity_score import Minute, score_minute
from client.application.managers.session_manager import SessionManager
from client.infrastructure.database.database import Database
from client.services.logger_service import LoggerService
from client.core.time_ist import now_ist
from client.core.win_ticks import ticks_ago_ms

try:
    import Quartz
except Exception:                                     # pragma: no cover
    Quartz = None


# ── the platform, behind three doors ───────────────────────────────────────

def _mac_counters() -> dict | None:
    """Running totals per event type, or None where they cannot be read."""
    if Quartz is None:
        return None
    try:
        state = Quartz.kCGEventSourceStateCombinedSessionState
        count = Quartz.CGEventSourceCounterForEventType
        return {
            "keystrokes": int(count(state, Quartz.kCGEventKeyDown)),
            "clicks": int(count(state, Quartz.kCGEventLeftMouseDown))
                      + int(count(state, Quartz.kCGEventRightMouseDown)),
            "scrolls": int(count(state, Quartz.kCGEventScrollWheel)),
            "mouse_moves": int(count(state, Quartz.kCGEventMouseMoved)),
        }
    except Exception:
        return None


def _windows_counters(previous: dict) -> dict | None:
    """The same four numbers, assembled from what Windows will tell us.

    GetAsyncKeyState's low bit means "pressed since you last asked", which
    makes a one-second sweep of the key range a count of keys used rather
    than of keystrokes. It undercounts somebody typing fast and that is
    fine: the question here is whether a keyboard is being used at all, and
    no automation that moves a mouse produces any keys whatsoever.
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        keys = 0
        clicks = 0
        for code in range(0x08, 0xFF):
            if code in (0x01, 0x02, 0x04):               # the mouse buttons
                continue
            if user32.GetAsyncKeyState(code) & 0x0001:
                keys += 1
        for button in (0x01, 0x02, 0x04):
            if user32.GetAsyncKeyState(button) & 0x0001:
                clicks += 1

        class _Point(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        point = _Point()
        user32.GetCursorPos(ctypes.byref(point))
        moved = (point.x, point.y) != previous.get("cursor")
        return {
            "keystrokes": previous.get("keystrokes", 0) + keys,
            "clicks": previous.get("clicks", 0) + clicks,
            "scrolls": previous.get("scrolls", 0),
            "mouse_moves": previous.get("mouse_moves", 0) + (1 if moved else 0),
            "cursor": (point.x, point.y),
        }
    except Exception:
        return None


def read_counters(previous: dict | None = None) -> dict | None:
    """Running totals of each kind of input, or None if unavailable."""
    system = platform.system()
    if system == "Darwin":
        return _mac_counters()
    if system == "Windows":
        return _windows_counters(previous or {})
    return None


def last_input_ms() -> int | None:
    """When the last input happened, in milliseconds — not how long ago.

    The GAPS between these are the strongest evidence there is: a person's
    are ragged, an automation's are identical to the millisecond.
    """
    system = platform.system()
    try:
        if system == "Darwin":
            if Quartz is None:
                return None
            since = Quartz.CGEventSourceSecondsSinceLastEventType(
                Quartz.kCGEventSourceStateCombinedSessionState,
                Quartz.kCGAnyInputEventType)
            return int((time.time() - float(since)) * 1000)
        if system == "Windows":
            import ctypes

            class _LastInput(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            info = _LastInput()
            info.cbSize = ctypes.sizeof(_LastInput)
            ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
            # THE SAME CLOCK AS macOS, which this promised and did not give.
            #
            # It returned dwTime as it stood: milliseconds since the machine
            # booted. Gaps between consecutive calls come out right, which is
            # all the scoring uses, so nothing was visibly wrong — and the
            # number meant something different on Windows from what it meant
            # on a Mac, went stale every 49.7 days at the counter's wrap, and
            # sat under a docstring promising when the input happened. Caught
            # by running this on a real Windows machine, which answered 49734
            # — the runner had been up for fifty seconds.
            return int(time.time() * 1000) - ticks_ago_ms(
                ctypes.windll.kernel32.GetTickCount(), info.dwTime)
    except Exception:
        return None
    return None


def foreground_window() -> str:
    """Which application is in front. The name only — never the title.

    A window title carries the document somebody has open, the customer they
    are writing to, the site they are reading. None of that is needed to
    answer "did they change what they were doing", and a monitoring product
    that collects it when it does not need it is one that will be asked why.
    """
    system = platform.system()
    try:
        if system == "Darwin":
            from AppKit import NSWorkspace
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            return str(app.localizedName()) if app else ""
        if system == "Windows":
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            handle = user32.GetForegroundWindow()
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))

            # THE PROGRAM'S NAME, as macOS already gives. This returned the
            # process id and nothing else — a real Windows run reported the
            # front window as "9312". Changes were still counted, because a
            # different program has a different id, so nothing looked broken;
            # but the docstring above promises a name, an id means nothing to
            # anybody reading the evidence later, and the same program
            # restarted read as a change of application.
            #
            # The executable's base name only — "chrome", "excel". Not the
            # window title, for the reason in the docstring.
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            process = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if process:
                try:
                    buffer = ctypes.create_unicode_buffer(512)
                    size = wintypes.DWORD(len(buffer))
                    if kernel32.QueryFullProcessImageNameW(
                            process, 0, buffer, ctypes.byref(size)):
                        name = buffer.value.rsplit("\\", 1)[-1]
                        if name.lower().endswith(".exe"):
                            name = name[:-4]
                        if name:
                            return name
                finally:
                    kernel32.CloseHandle(process)
            # Some processes refuse to be asked (a system window, another
            # user's). The id still answers "did this change", which is all
            # the scoring needs.
            return str(pid.value)
    except Exception:
        return ""
    return ""


class ActivityTracker(QObject):
    """Scores each minute of input and keeps the score where it can be read."""

    minute_scored = Signal(dict)

    SAMPLE_MS = 1000
    SECONDS_IN_A_MINUTE = 60

    def __init__(self):
        super().__init__()
        self._minute = Minute()
        self._counters: dict | None = None
        self._last_input: int | None = None
        self._window = ""
        self._minute_started = time.monotonic()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.sample)

    def start(self):
        # The owner is not a tracked employee — the same rule screenshots and
        # the idle tracker already follow.
        if getattr(SessionManager, "role", "") == "super_admin":
            return
        self._timer.start(self.SAMPLE_MS)

    def stop(self):
        self._timer.stop()

    # ── one second at a time ───────────────────────────────────────────────

    def sample(self):
        counters = read_counters(self._counters)
        if counters is not None:
            if self._counters is not None:
                for key in ("keystrokes", "clicks", "scrolls", "mouse_moves"):
                    moved = int(counters.get(key, 0)) - int(self._counters.get(key, 0))
                    # A counter that went backwards means the session's own
                    # counters were reset (a lock, a fast user switch). The
                    # step is dropped rather than counted as a negative.
                    if moved > 0:
                        setattr(self._minute, key,
                                getattr(self._minute, key) + moved)
            self._counters = counters

        stamp = last_input_ms()
        if stamp is not None and self._last_input is not None and stamp != self._last_input:
            gap = stamp - self._last_input
            if 0 < gap < 60_000:
                self._minute.gaps_ms.append(gap)
        if stamp is not None:
            self._last_input = stamp

        window = foreground_window()
        if window and self._window and window != self._window:
            self._minute.window_changes += 1
        if window:
            self._window = window

        if time.monotonic() - self._minute_started >= self.SECONDS_IN_A_MINUTE:
            self.close_minute()

    def close_minute(self):
        """Score what was seen, store it, and start the next minute clean."""
        scored = score_minute(self._minute)
        self._minute = Minute()
        self._minute_started = time.monotonic()
        try:
            self._store(scored)
        except Exception as error:                      # noqa: BLE001
            # A minute that cannot be written is not worth interrupting
            # tracking for; the next one will be.
            LoggerService.log_verbose(
                f"ActivityTracker: could not store a minute — {error}")
        self.minute_scored.emit(scored)
        return scored

    def _store(self, scored: dict):
        connection = Database.connect()
        try:
            connection.execute(
                """
                INSERT INTO activity_minutes
                    (employee_id, minute, score, band, keystrokes, clicks,
                     scrolls, mouse_moves, window_changes, automation_suspected,
                     reasons, uploaded)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                -- THE SAME MINUTE AGAIN CORRECTS IT. A clock that steps
                -- backwards (an NTP correction after sleep is the common
                -- one) makes the next minute land on a stamp already
                -- written; two rows with one stamp are a batch Postgres
                -- refuses, and then nothing from this employee uploads
                -- again. Marked unsent, so the corrected copy is sent.
                ON CONFLICT (employee_id, minute) DO UPDATE SET
                    score = excluded.score, band = excluded.band,
                    keystrokes = excluded.keystrokes, clicks = excluded.clicks,
                    scrolls = excluded.scrolls, mouse_moves = excluded.mouse_moves,
                    window_changes = excluded.window_changes,
                    automation_suspected = excluded.automation_suspected,
                    reasons = excluded.reasons, uploaded = 0
                """,
                (SessionManager.employee_id,
                 now_ist().strftime("%Y-%m-%d %H:%M:00"),
                 scored["score"], scored["band"], scored["keystrokes"],
                 scored["clicks"], scored["scrolls"], scored["mouse_moves"],
                 scored["window_changes"],
                 1 if scored["automation_suspected"] else 0,
                 ", ".join(scored["reasons"])),
            )
            connection.commit()
        finally:
            connection.close()
