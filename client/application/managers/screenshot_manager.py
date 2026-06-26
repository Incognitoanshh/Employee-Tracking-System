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


class ScreenshotManager:
    STORAGE_PATH = "storage/screenshots"

    @classmethod
    def generate_random_schedule(
        cls, shift_start: datetime, shift_end: datetime
    ) -> list:
        """Shift ke andar N random timestamps generate karo."""
        count = int(SettingsService.get_setting("screenshot_count", "3"))
        min_gap = int(SettingsService.get_setting("screenshot_min_minutes", "3")) * 60
        _max_gap = int(SettingsService.get_setting("screenshot_max_minutes", "10")) * 60

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
        os.makedirs(cls.STORAGE_PATH, exist_ok=True)

        screenshot_id = str(uuid.uuid4())
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # BUG FIX: Pehle .enc file seedha upload ho rahi thi — server PNG chahiye.
        # Fix: PNG bytes in-memory rakho — encrypted version local disk pe save karo,
        # aur PNG bytes server ko upload karo (in-memory, koi plain file disk pe nahi).
        screenshot = pyautogui.screenshot()
        buf = io.BytesIO()
        screenshot.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        # Encrypted file local storage pe save karo
        enc_filename = f"{screenshot_id}.enc"
        enc_filepath = os.path.join(cls.STORAGE_PATH, enc_filename)
        CryptoEngine.save_encrypted(png_bytes, enc_filepath)

        print(f"[SCREENSHOT ENCRYPTED & SAVED] {enc_filepath}")
        LoggerService.log(f"SCREENSHOT CAPTURED : {enc_filepath}")

        # Local DB mein record karo
        connection = Database.connect()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO screenshots (id, employee_id, file_path, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            (screenshot_id, SessionManager.employee_id, enc_filepath, timestamp),
        )
        connection.commit()
        connection.close()

        # Server ko PNG bytes upload karo (in-memory — disk pe plain PNG nahi)
        try:
            upload_filename = f"{screenshot_id}.png"
            response = requests.post(
                f"{API_BASE_URL}/screenshots/upload",
                files={"screenshot": (upload_filename, png_bytes, "image/png")},
                headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                timeout=10,
            )

            print("[UPLOAD RESPONSE]", response.status_code, response.text)
            if response.status_code == 200:
                SyncManager.mark_uploaded(screenshot_id)

        except Exception as error:
            print("[UPLOAD ERROR - will retry later]", error)

        return {
            "id": screenshot_id,
            "path": enc_filepath,
            "timestamp": timestamp,
        }
