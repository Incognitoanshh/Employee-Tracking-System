import io
import os
import random
from datetime import datetime, timedelta
import uuid

import pyautogui

from client.application.managers.session_manager import SessionManager
from client.application.managers.sync_manager import SyncManager
from client.infrastructure.database.database import Database
from client.security.crypto_engine import CryptoEngine
from client.services.logger_service import LoggerService
from client.services.settings_service import SettingsService


class ScreenshotManager:
    STORAGE_PATH = "storage/screenshots"

    @classmethod
    def generate_random_schedule(
        cls, shift_start: datetime, shift_end: datetime
    ) -> list[datetime]:
        count   = int(SettingsService.get_setting("screenshot_count", "3"))
        min_gap = int(SettingsService.get_setting("screenshot_min_minutes", "3")) * 60
        max_gap = int(SettingsService.get_setting("screenshot_max_minutes", "10")) * 60

        # Guard: agar settings galat hain
        if min_gap >= max_gap:
            max_gap = min_gap + 60  # minimum 1 minute buffer
        shift_duration = (shift_end - shift_start).total_seconds()
        if shift_duration <= 0 or count <= 0:
            LoggerService.log(
                "ScreenshotManager: shift duration ya count invalid — schedule empty"
            )
            return []

        if shift_duration < 600:  # 10 min se choti shift
            mid = shift_start + (shift_end - shift_start) / 2
            return [mid]
        timestamps: list[datetime] = []
        current = shift_start + timedelta(minutes=2)  # buffer start

        while current < shift_end and len(timestamps) < count:
            gap = random.randint(min_gap, max_gap)
            current += timedelta(seconds=gap)
            if current < shift_end - timedelta(minutes=2):  # buffer end
                timestamps.append(current)
        timestamps.sort()
        LoggerService.log(
            f"ScreenshotManager: {len(timestamps)} screenshots scheduled "
            f"{shift_start.strftime('%H:%M')}–{shift_end.strftime('%H:%M')}"
        )
        return timestamps

    @classmethod
    def capture_screenshot(cls):
        os.makedirs(cls.STORAGE_PATH, exist_ok=True)

        screenshot_id = str(uuid.uuid4())
        timestamp     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        filename      = f"{screenshot_id}.enc"
        filepath      = os.path.join(cls.STORAGE_PATH, filename)

        # Capture + Encrypt
        screenshot = pyautogui.screenshot()
        buf = io.BytesIO()
        screenshot.save(buf, format="PNG")
        png_bytes = buf.getvalue()
        CryptoEngine.save_encrypted(png_bytes, filepath)

        LoggerService.log(f"SCREENSHOT CAPTURED: {filepath}")

        # DB insert — try/finally se connection guaranteed close hoga
        connection = Database.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO screenshots
                (id, employee_id, session_id, file_path, timestamp, upload_status)
                VALUES (?, ?, ?, ?, ?, 'PENDING')
                """,
                (
                    screenshot_id,
                    SessionManager.employee_id,
                    SessionManager.session_id,   # session_id add kiya
                    filepath,
                    timestamp,
                ),
            )
            connection.commit()
        except Exception as e:
            LoggerService.log(f"SCREENSHOT DB ERROR: {e}")
            raise
        finally:
            connection.close()  # Always close

            # Upload SyncManager pe chhod do — yahan direct call nahi
        SyncManager.queue_screenshot(screenshot_id)

        return {
            "id":        screenshot_id,
            "path":      filepath,
            "timestamp": timestamp,
        }
