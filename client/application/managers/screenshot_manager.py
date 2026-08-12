import io
import os
import random
from datetime import datetime, timedelta, timezone
import uuid

import sys
import pyautogui

from client.core import http as _http
from PIL import Image

from client.application.managers.session_manager import SessionManager
from client.application.managers.sync_manager import SyncManager
from client.infrastructure.database.database import Database
from client.security.crypto_engine import CryptoEngine
from client.services.logger_service import LoggerService
from client.core.time_ist import now_ist, end_of_ist_day, ist_day_str
from client.services.settings_service import SettingsService
from client.core.config import API_BASE_URL, STORAGE_DIR
from client.core.config.settings import Settings



# ── DAILY SCREENSHOT BUDGET ──────────────────────────────────────────────
#
# The count is per IST CALENDAR DAY, not per shift.
#
# It used to be per shift, spread across the shift window, with a separate
# off-shift path that ignored the count entirely and fired every min..max
# minutes. An employee who stayed logged in overnight therefore collected
# ~157 off-shift captures on top of the configured 10, and the rollover
# that generates the next day's schedule reset the 200-entry safety cap,
# so it repeated every day indefinitely. Measured: 167 captures in 24h and
# 334 in 48h where the admin had configured 10.
#
# Now there is one budget per IST day. Whatever remains of it is spread
# across whatever remains of the monitoring window, and capture_screenshot()
# refuses once the day's budget is spent — so the number can be reached
# late, but never exceeded, no matter how the schedule is regenerated.


def _screen_count() -> int:
    """How many displays are attached, cheaply and without needing a window."""
    try:
        if sys.platform == "win32":
            import ctypes
            return max(1, int(ctypes.windll.user32.GetSystemMetrics(80)))  # SM_CMONITORS
        if sys.platform == "darwin":
            from PySide6.QtGui import QGuiApplication
            app = QGuiApplication.instance()
            if app is not None:
                return max(1, len(app.screens()))
            # No Qt application yet — ask the window server directly.
            import subprocess as _sp
            out = _sp.run(["system_profiler", "SPDisplaysDataType"],
                          capture_output=True, text=True, timeout=10).stdout
            found = out.count("Resolution:")
            return max(1, found)
    except Exception:
        pass
    return 1


def _grab_every_screen():
    """Every attached display, not just the main one.

    WHAT THIS FIXES. `pyautogui.screenshot()` captures ONE display on both
    platforms, and it is not obvious from the call:

      * Windows — pyscreeze calls `ImageGrab.grab(all_screens=False)`, which
        is the primary monitor only.
      * macOS  — Pillow shells out to `screencapture -x <one file>`, which
        writes the main display.

    So an employee with two monitors was monitored on one of them, and the
    other half of their working day simply did not exist. Nothing failed and
    nothing was logged; the screenshots looked perfectly normal.

    ONE MONITOR TAKES THE ORDINARY PATH. That is the overwhelming majority of
    machines, it is the path that has always worked, and it is the one the
    pipeline test replaces to feed in a known image — a capture routine that
    cannot be given a fake screen cannot be tested end to end.

    Falls back to the same ordinary path if anything below fails: half a
    screenshot is worth more than none.
    """
    if _screen_count() <= 1:
        return pyautogui.screenshot()

    try:
        if sys.platform == "win32":
            from PIL import ImageGrab
            return ImageGrab.grab(all_screens=True)

        if sys.platform == "darwin":
            import subprocess as _sp
            import tempfile as _tf

            shots = []
            for display in range(1, 5):          # more than anybody plugs in
                path = _tf.mktemp(suffix=".png")
                done = _sp.run(["screencapture", "-x", "-D", str(display), path],
                               capture_output=True, timeout=20)
                if done.returncode != 0 or not os.path.exists(path):
                    break
                try:
                    img = Image.open(path)
                    img.load()
                    shots.append(img)
                finally:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
            if len(shots) == 1:
                return shots[0]
            if len(shots) > 1:
                # Side by side, in the order the displays report — one wide
                # image of the whole desk, which the resize below handles.
                width = sum(i.width for i in shots)
                height = max(i.height for i in shots)
                canvas = Image.new("RGB", (width, height), (0, 0, 0))
                x = 0
                for img in shots:
                    canvas.paste(img.convert("RGB"), (x, 0))
                    x += img.width
                return canvas
    except Exception as error:
        LoggerService.log_verbose(
            f"ScreenshotManager: multi-screen capture failed, falling back — {error}")

    return pyautogui.screenshot()


class ScreenshotManager:
    STORAGE_PATH = os.path.join(STORAGE_DIR, "screenshots")

    # REMOVED: generate_random_schedule() and generate_interval_schedule().
    #
    # Both tied the number of captures to the shift. The first spread
    # `screenshot_count` across the shift window; the second, used whenever
    # the employee was outside their shift, ignored the count completely and
    # fired every min..max minutes. That second path is what produced 157
    # extra captures a night on a machine left logged in.
    #
    # generate_daily_schedule() replaces both: one budget per IST day,
    # spread over whatever monitoring time is left.


    @classmethod
    def screenshots_per_day(cls) -> int:
        """Configured daily budget, clamped to the supported 1-20 range."""
        try:
            n = int(SettingsService.get_setting("screenshots_per_day", "10"))
        except (TypeError, ValueError):
            n = 10
        return max(1, min(20, n))

    @classmethod
    def captures_today(cls) -> int:
        """How many captures this employee already has for the current IST day.

        Read from the local database rather than an in-memory counter, so
        the budget survives an app restart or a logout/login. An in-memory
        counter would let anyone reset it by quitting the app.
        """
        employee_id = getattr(SessionManager, "employee_id", None)
        if not employee_id:
            return 0
        # Derive the day from THIS module's now_ist, the same one
        # capture_screenshot() stamps rows with. Calling ist_day_str()
        # with no argument would resolve now_ist inside time_ist instead —
        # identical in production, but two paths to "what day is it" is
        # exactly the split-brain that caused every timing bug here.
        day = ist_day_str(now_ist())
        try:
            connection = Database.connect()
            row = connection.execute(
                "SELECT COUNT(*) FROM screenshots "
                "WHERE employee_id = ? AND timestamp LIKE ?",
                (employee_id, f"{day}%"),
            ).fetchone()
            connection.close()
            return int(row[0]) if row else 0
        except Exception as error:
            # Fail CLOSED would stop monitoring entirely on a transient DB
            # error; fail open and let the scheduler's own spacing apply.
            LoggerService.log_verbose(
                f"ScreenshotManager: could not read today's count — {error}"
            )
            return 0

    @classmethod
    def remaining_today(cls) -> int:
        return max(0, cls.screenshots_per_day() - cls.captures_today())

    @classmethod
    def generate_daily_schedule(
        cls, window_start: datetime, window_end: datetime
    ) -> list:
        """Spread the day's REMAINING budget across the remaining window.

        The window is divided into as many equal slots as there are captures
        left, with one random moment inside each slot. That keeps the timing
        unpredictable — an employee cannot learn the cadence — while still
        covering the whole period rather than bunching at the start.

        `screenshot_min_minutes` acts as a floor between consecutive
        captures. The old `screenshot_max_minutes` no longer drives the
        count: with a daily budget the spacing follows from how much of the
        day is left, which is the honest relationship.
        """
        remaining = cls.remaining_today()
        if remaining <= 0:
            LoggerService.log_verbose(
                f"ScreenshotManager: daily budget of {cls.screenshots_per_day()} "
                f"already used — nothing scheduled"
            )
            return []

        duration = (window_end - window_start).total_seconds()
        if duration <= 0:
            return []

        try:
            min_gap = int(SettingsService.get_setting(
                "screenshot_min_minutes",
                str(Settings.SCREENSHOT_MIN_INTERVAL // 60))) * 60
        except (TypeError, ValueError):
            min_gap = 60
        min_gap = max(0, min_gap)

        # Keep captures off the exact edges of the window (login/logout
        # moments), but never let the buffer eat the whole window.
        buffer = min(120.0, duration / (remaining * 4))
        start = window_start + timedelta(seconds=buffer)
        usable = duration - 2 * buffer
        if usable <= 0:
            return [window_start + timedelta(seconds=duration / 2)]

        slot = usable / remaining
        timestamps: list[datetime] = []
        previous: datetime | None = None
        for i in range(remaining):
            slot_start = start + timedelta(seconds=i * slot)
            moment = slot_start + timedelta(seconds=random.uniform(0, slot))
            if previous is not None and (moment - previous).total_seconds() < min_gap:
                moment = previous + timedelta(seconds=min_gap)
            if moment >= window_end:
                break
            timestamps.append(moment)
            previous = moment

        LoggerService.log_verbose(
            f"ScreenshotManager: {len(timestamps)} of {cls.screenshots_per_day()} "
            f"daily captures scheduled between "
            f"{window_start.strftime('%d %b %H:%M')}-{window_end.strftime('%d %b %H:%M')} IST"
        )
        return timestamps

    @classmethod
    def capture_screenshot(cls):
        # Super admin ko monitor NAHI kiya jaata — wo company ka owner/manager
        # hai, tracked employee nahi. Ye guard scheduler ke guard ke alawa
        # defence-in-depth hai (agar kabhi koi aur code path capture trigger
        # kare to bhi super admin safe rahe).
        if getattr(SessionManager, "role", "") == "super_admin":
            LoggerService.log_verbose(
                "ScreenshotManager: super admin — screenshot skipped"
            )
            return None

        # macOS WITHOUT SCREEN RECORDING does not fail — it hands back the
        # desktop picture with every window missing, which uploads and stores
        # exactly like a real capture. Tracking then looks alive and shows
        # nothing but wallpaper. Say so instead, in a line an administrator
        # can find, and take nothing.
        from client.services.screen_permission import has_screen_access
        if not has_screen_access():
            LoggerService.log(
                "SCREENSHOT SKIPPED : Screen Recording permission not granted "
                "— allow it in System Settings and restart the app")
            return None

        # HARD CAP — the guarantee that the daily number is never exceeded.
        #
        # It lives here, not in the scheduler, because every capture path
        # goes through this method. Capping at schedule time would only
        # cover one path, and a reschedule (config change, day rollover)
        # would hand out a fresh allowance each time.
        #
        # Logged with log(), not log_verbose(): if captures stop, the admin
        # must be able to see why. Silently stopping would look identical to
        # tracking being broken.
        allowed = cls.screenshots_per_day()
        taken = cls.captures_today()
        if taken >= allowed:
            LoggerService.log(
                f"SCREENSHOT SKIPPED : daily limit reached ({taken}/{allowed})"
            )
            return None

        screenshot_id = str(uuid.uuid4())
        # IST, so the day a capture belongs to does not depend on the
        # client machine's timezone. captures_today() counts on this.
        timestamp = now_ist().strftime("%Y-%m-%d %H:%M:%S")

        # Capture + encrypt + local DB record — pehle yahan koi try/except
        # nahi tha. Agar pyautogui.screenshot() fail ho jaye (e.g. macOS pe
        # Screen Recording permission missing) to exception Qt signal-slot
        # ke through silently swallow ho jaata (sirf stderr pe print hota,
        # server ko kabhi pata nahi chalta screenshots kyun ruk gaye). Ab
        # failure explicitly log hoti hai.
        try:
            os.makedirs(cls.STORAGE_PATH, exist_ok=True)

            # PNG bytes in-memory rakho (disk pe kahi bhi plain PNG save nahi hoti).
            # Encrypted (.enc) version hi local disk pe save hoti hai, aur wahi
            # (.enc) server ko bhi upload hoti hai. Decrypt sirf app se open
            # karte waqt hota hai (screenshot_preview_window.py).
            # ── STORAGE FIX ────────────────────────────────────────────
            #  Pehle har capture full-resolution PNG me save hoti thi.
            #  Production pe measure kiya: average .enc file 3.5 MB.
            #      12 screenshots/din  = 41 MB per employee per din
            #      1 saal              = ~10 GB har employee ke laptop pe
            #      1000 employees      = ~41 GB/din server pe
            #
            #  Monitoring ke liye full 4K PNG ki zarurat hai hi nahi — screen
            #  padhne laayak honi chahiye, bas. Ab: lambi side ko
            #  SCREENSHOT_MAX_WIDTH tak scale karke JPEG me save karte hain.
            #  Preview window QPixmap se load karta hai jo format khud
            #  detect kar leta hai, to decrypt/display bina badle chalta hai.
            # ───────────────────────────────────────────────────────────
            screenshot = _grab_every_screen()

            max_width = int(os.getenv("SCREENSHOT_MAX_WIDTH", "1920"))
            quality   = int(os.getenv("SCREENSHOT_JPEG_QUALITY", "75"))
            if max_width > 0 and screenshot.width > max_width:
                ratio = max_width / float(screenshot.width)
                screenshot = screenshot.resize(
                    (max_width, max(1, int(screenshot.height * ratio))),
                    Image.LANCZOS,
                )

            buf = io.BytesIO()
            # JPEG ko RGB chahiye — macOS/Windows kabhi RGBA deta hai.
            if screenshot.mode not in ("RGB", "L"):
                screenshot = screenshot.convert("RGB")
            screenshot.save(buf, format="JPEG", quality=quality, optimize=True)
            png_bytes = buf.getvalue()

            # Encrypted file local storage pe save karo
            enc_filename = f"{screenshot_id}.enc"
            enc_filepath = os.path.join(cls.STORAGE_PATH, enc_filename)
            CryptoEngine.save_encrypted(png_bytes, enc_filepath)

            LoggerService.log(
                f"SCREENSHOT CAPTURED : {enc_filepath} "
                f"({len(png_bytes) // 1024} KB)"
            )

            # Local DB mein record karo
            connection = Database.connect()
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO screenshots (id, employee_id, file_path, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (screenshot_id, SessionManager.employee_id, enc_filepath,
                 timestamp),
            )
            connection.commit()
            connection.close()
        except Exception as error:
            LoggerService.log(f"ScreenshotManager: capture failed — {error}")
            return None

        # Server ko encrypted (.enc) bytes upload karo — plain PNG kabhi
        # network pe nahi jaani chahiye.
        try:
            with open(enc_filepath, "rb") as f:
                enc_bytes = f.read()

            upload_filename = f"{screenshot_id}.enc"
            response = _http.post(
                f"{API_BASE_URL}/screenshots/upload",
                files={"screenshot": (upload_filename, enc_bytes, "application/octet-stream")},
                headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                timeout=10,
            )

            if response.status_code == 200:
                SyncManager.mark_uploaded(screenshot_id)
            else:
                # BUG FIX: pehle yahan kuch log nahi hota tha — non-200 response
                # silently ignore ho jata tha. Row uploaded=0 hi rahegi,
                # retry_uploads() isko baad mein retry karega.
                LoggerService.log(
                    f"ScreenshotManager: upload failed, will retry — "
                    f"HTTP {response.status_code} {response.text[:200]}"
                )

        except Exception as error:
            # BUG FIX: pehle exception silently swallow ho jata tha bina kisi
            # log ke. Ab log hota hai taaki debugging possible ho.
            LoggerService.log(
                f"ScreenshotManager: upload error, will retry — {error}"
            )

        return {
            "id": screenshot_id,
            "path": enc_filepath,
            "timestamp": timestamp,
        }

