import os

import uuid

from datetime import datetime

import pyautogui

from app.services.logger_service import LoggerService

from app.infrastructure.database.database import Database

from app.application.managers.session_manager import SessionManager


class ScreenshotManager:

    STORAGE_PATH = (
        "storage/screenshots"
    )

    @classmethod
    def capture_screenshot(cls):

        if not os.path.exists(
            cls.STORAGE_PATH
        ):

            os.makedirs(
                cls.STORAGE_PATH
            )

        screenshot_id = str(
            uuid.uuid4()
        )

        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        filename = (
            f"{screenshot_id}.png"
        )

        filepath = os.path.join(
            cls.STORAGE_PATH,
            filename
        )

        screenshot = pyautogui.screenshot()

        screenshot.save(filepath)

        connection = Database.connect()

        cursor = connection.cursor()

        cursor.execute("""

            INSERT INTO screenshots (

                id,
                employee_id,
                file_path,
                timestamp

            )

            VALUES (?, ?, ?, ?)

        """, (

            screenshot_id,

            SessionManager.employee_id,

            filepath,

            timestamp

        ))

        connection.commit()

        connection.close()

        print(
            f"[SCREENSHOT SAVED] {filepath}"
        )
        LoggerService.log(
            f"SCREENSHOT CAPTURED : {filepath}" 
        )

        return {

            "id": screenshot_id,

            "path": filepath,

            "timestamp": timestamp

        }