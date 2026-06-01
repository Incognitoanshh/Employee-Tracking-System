import os
from datetime import datetime
import uuid
import pyautogui
import requests
from client.application.managers.session_manager import SessionManager
from client.application.managers.sync_manager import SyncManager
from client.infrastructure.database.database import Database
from client.services.logger_service import LoggerService


class ScreenshotManager:

    STORAGE_PATH = "storage/screenshots"

    @classmethod
    def capture_screenshot(cls):

        if not os.path.exists(cls.STORAGE_PATH):
            os.makedirs(cls.STORAGE_PATH)

        screenshot_id = str(uuid.uuid4())
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        filename = f"{screenshot_id}.png"
        filepath = os.path.join(cls.STORAGE_PATH, filename)

        # 1. screenshot.save()
        screenshot = pyautogui.screenshot()
        screenshot.save(filepath)
        print(f"[SCREENSHOT SAVED] {filepath}")
        LoggerService.log(f"SCREENSHOT CAPTURED : {filepath}")

        # 2. INSERT INTO screenshots & 3. connection.commit()
        connection = Database.connect()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO screenshots (
                id,
                employee_id,
                file_path,
                timestamp
            )
            VALUES (?, ?, ?, ?)
            """,
            (screenshot_id, SessionManager.employee_id, filepath, timestamp),
        )

        connection.commit()
        connection.close()

        # 4. Upload API & 5. mark_uploaded()
        try:
            with open(filepath, "rb") as file:
                response = requests.post(
                    "http://127.0.0.1:8000/api/screenshots/upload",
                    files={"screenshot": file},
                    timeout=10,
                )
                print("[UPLOAD RESPONSE]", response.text)

                if response.status_code == 200:
                    SyncManager.mark_uploaded(screenshot_id)

        except Exception as error:
            print("[UPLOAD ERROR]", error)

        return {"id": screenshot_id, "path": filepath, "timestamp": timestamp}