

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
    ) -> list[datetime]:
        """Shift ke andar N random timestamps generate karo.

        - count      = screenshot_count (settings se)
        - min_gap    = screenshot_min_minutes (settings se)
        - max_gap    = screenshot_max_minutes (settings se)

        Note: min_gap respect karta hai (too-close timestamps avoid).
        """
        count = int(SettingsService.get_setting("screenshot_count", "3"))
        min_gap = int(SettingsService.get_setting("screenshot_min_minutes", "3")) * 60
        # max_gap currently used only as a descriptive bound; actual distribution uses uniform offsets
        _max_gap = int(SettingsService.get_setting("screenshot_max_minutes", "10")) * 60

        shift_duration = (shift_end - shift_start).total_seconds()
        if shift_duration <= 0 or count <= 0:
            LoggerService.log(
                "ScreenshotManager: shift duration ya count invalid — schedule empty"
            )
            return []

        # Buffer: pehle 5 min aur aakhri 5 min chhod do
        buffer = 300  # 5 minutes
        available = shift_duration - 2 * buffer

        if available <= 0:
            # Shift bahut choti hai — ek middle mein le lo
            return [shift_start + (shift_end - shift_start) / 2]

        timestamps: list[datetime] = []
        attempts = 0

        current = shift_start + timedelta(minutes=2)

        while (
            current < shift_end
            and len(timestamps) < count
        ):

            gap = random.randint(
                min_gap,
                _max_gap
            )

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
        filename = f"{screenshot_id}.enc"
        filepath = os.path.join(cls.STORAGE_PATH, filename)

        screenshot = pyautogui.screenshot()
        buf = io.BytesIO()
        screenshot.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        CryptoEngine.save_encrypted(png_bytes, filepath)

        print(f"[SCREENSHOT ENCRYPTED & SAVED] {filepath}")
        LoggerService.log(f"SCREENSHOT CAPTURED : {filepath}")

        connection = Database.connect()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO screenshots (id, employee_id, file_path, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            (screenshot_id, SessionManager.employee_id, filepath, timestamp),
        )
        connection.commit()
        connection.close()

        # Upload try karo
        try:
            with open(filepath, "rb") as file:
                response = requests.post(
                    f"{API_BASE_URL}/screenshots/upload",
                    files={"screenshot": file},
                    headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                    timeout=10,
                )

                print(
                    "[UPLOAD RESPONSE]",
                    response.status_code,
                    response.text,
                )
                if response.status_code == 200:
                    SyncManager.mark_uploaded(screenshot_id)
        except Exception as error:
            print("[UPLOAD ERROR - will retry later]", error)

        return {
            "id": screenshot_id,
            "path": filepath,
            "timestamp": timestamp,
        }

