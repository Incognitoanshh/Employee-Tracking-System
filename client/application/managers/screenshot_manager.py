import io
import os
import random
from datetime import datetime, timedelta
import uuid

import pyautogui
import requests

from client.application.managers.session_manager import SessionManager
from client.application.managers.sync_manager import SyncManager
from client.infrastructure.database.database import Database
from client.security.crypto_engine import CryptoEngine
from client.services.logger_service import LoggerService
from client.services.settings_service import SettingsService
from client.core.config import API_BASE_URL, STORAGE_DIR
from client.core.config.settings import Settings


class ScreenshotManager:
    STORAGE_PATH = os.path.join(STORAGE_DIR, "screenshots")

    @classmethod
    def generate_random_schedule(
        cls, shift_start: datetime, shift_end: datetime
    ) -> list:
        """Shift ke andar N random timestamps generate karo."""
        # Fallback defaults ab .env-driven Settings se aate hain (DB me
        # koi override save nahi hua to yehi use hoga) — pehle yaha
        # hardcoded "3"/"10" tha jo .env ke SCREENSHOT_MIN_INTERVAL/
        # SCREENSHOT_MAX_INTERVAL se independent ho sakta tha (config
        # drift risk: .env badlo, ye fallback kabhi sync na ho).
        count = int(SettingsService.get_setting("screenshot_count", "3"))
        min_gap = int(SettingsService.get_setting(
            "screenshot_min_minutes", str(Settings.SCREENSHOT_MIN_INTERVAL // 60)
        )) * 60
        _max_gap = int(SettingsService.get_setting(
            "screenshot_max_minutes", str(Settings.SCREENSHOT_MAX_INTERVAL // 60)
        )) * 60

        shift_duration = (shift_end - shift_start).total_seconds()
        if shift_duration <= 0 or count <= 0:
            LoggerService.log(
                "ScreenshotManager: shift duration ya count invalid — schedule empty"
            )
            return []

        # BUG FIX: pehle ye loop shift_start se aage random min_gap..max_gap
        # steps leta tha aur `count` pe ruk jaata tha — yaani SAARE
        # screenshots shift ke pehle ~20 minute me hi ho jaate the, aur uske
        # baad poore din (8-9 ghante) ek bhi capture nahi hota tha.
        # Example (09:00–18:00, count=3, gap 3–10 min): 09:08, 09:16, 09:22
        # — phir 18:00 tak kuch nahi. `buffer`/`available` calculate to hote
        # the lekin kabhi use hi nahi hote the.
        #
        # Ab: shift ko `count` barabar slots me baanto aur HAR slot ke andar
        # ek random moment chuno. Isse poori shift cover hoti hai (admin ke
        # "Screenshots per shift" label ke mutabik) aur timing phir bhi
        # unpredictable rehti hai — employee guess nahi kar sakta.
        #
        # Shift ke bilkul kinare pe capture na ho (login/logout ke exact
        # waqt pe) is liye dono taraf chhota buffer chhodte hain.
        buffer = min(120.0, shift_duration / (count * 4))
        window_start = shift_start + timedelta(seconds=buffer)
        window_duration = shift_duration - 2 * buffer

        if window_duration <= 0:
            return [shift_start + (shift_end - shift_start) / 2]

        slot_seconds = window_duration / count

        # min_gap ek FLOOR hai (do captures itne paas na hon). Agar slot
        # max_gap se bada hai to iska matlab hai ki is count pe poori shift
        # cover karne ke liye gap max_gap se zyada hoga — us case me admin ka
        # `count` binding constraint hai, isliye log kar dete hain taaki
        # admin ko dikhe ki uska max-interval effective nahi ho raha.
        if slot_seconds > _max_gap:
            LoggerService.log(
                f"ScreenshotManager: screenshot_count={count} is shift ke liye "
                f"~{int(slot_seconds // 60)} min ka gap deta hai, jo configured "
                f"max interval ({_max_gap // 60} min) se zyada hai. Poori shift "
                f"cover karne ke liye count badhao."
            )

        timestamps: list[datetime] = []
        previous: datetime | None = None

        for index in range(count):
            slot_start = window_start + timedelta(seconds=index * slot_seconds)
            slot_end = slot_start + timedelta(seconds=slot_seconds)

            candidate = slot_start + timedelta(
                seconds=random.uniform(0, slot_seconds)
            )

            # min_gap floor enforce karo — lekin apne slot se bahar mat jao.
            if previous is not None:
                earliest = previous + timedelta(seconds=min_gap)
                if candidate < earliest:
                    candidate = min(earliest, slot_end)

            if candidate >= shift_end:
                break

            timestamps.append(candidate)
            previous = candidate

        timestamps.sort()

        if timestamps:
            LoggerService.log(
                f"ScreenshotManager: {len(timestamps)} screenshots scheduled across "
                f"shift {shift_start.strftime('%H:%M')}–{shift_end.strftime('%H:%M')} "
                f"(first {timestamps[0].strftime('%H:%M')}, "
                f"last {timestamps[-1].strftime('%H:%M')})"
            )
        else:
            LoggerService.log(
                f"ScreenshotManager: no screenshots scheduled for shift "
                f"{shift_start.strftime('%H:%M')}–{shift_end.strftime('%H:%M')}"
            )
        return timestamps

    @classmethod
    def capture_screenshot(cls):
        screenshot_id = str(uuid.uuid4())
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
            screenshot = pyautogui.screenshot()
            buf = io.BytesIO()
            screenshot.save(buf, format="PNG")
            png_bytes = buf.getvalue()

            # Encrypted file local storage pe save karo
            enc_filename = f"{screenshot_id}.enc"
            enc_filepath = os.path.join(cls.STORAGE_PATH, enc_filename)
            CryptoEngine.save_encrypted(png_bytes, enc_filepath)

            LoggerService.log(f"SCREENSHOT CAPTURED : {enc_filepath}")

            # Local DB mein record karo
            connection = Database.connect()
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO screenshots (id, employee_id, file_path, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (screenshot_id, SessionManager.employee_id, enc_filepath,
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
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
            response = requests.post(
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

