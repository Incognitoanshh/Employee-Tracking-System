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
from client.core.config import API_BASE_URL
from client.core.config.settings import Settings


class ScreenshotManager:
    STORAGE_PATH = "storage/screenshots"

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

        buffer = 300  # 5 minutes
        available = shift_duration - 2 * buffer

        if available <= 0:
            return [shift_start + (shift_end - shift_start) / 2]

        timestamps = []
        current = shift_start + timedelta(minutes=2)

        while (current < shift_end and len(timestamps) < count):
            gap = random.randint(min_gap, _max_gap)
            current += timedelta(seconds=gap)
            if current < shift_end:
                timestamps.append(current)

        timestamps.sort()

        LoggerService.log(
            f"ScreenshotManager: {len(timestamps)} screenshots scheduled for shift "
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
