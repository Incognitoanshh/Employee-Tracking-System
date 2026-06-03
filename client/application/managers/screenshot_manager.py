import io
import os
from datetime import datetime
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import pyautogui
import requests

from client.application.managers.session_manager import SessionManager
from client.application.managers.sync_manager import SyncManager
from client.infrastructure.database.database import Database
from client.services.logger_service import LoggerService


class ScreenshotManager:
    STORAGE_PATH = "storage/screenshots"  # Apne hisab se badal lein
    ENCRYPTION_KEY = (os.environ.get("SCREENSHOT_AES_KEY","2a0d030fe8ae1386b14972e800448c8d").encode())

    @classmethod
    def capture_screenshot(cls):
        if not os.path.exists(cls.STORAGE_PATH):
            os.makedirs(cls.STORAGE_PATH)

        screenshot_id = str(uuid.uuid4())
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        filename = f"{screenshot_id}.enc"
        filepath = os.path.join(cls.STORAGE_PATH, filename)
        # Screenshot lena aur bytes mein convert karna
        screenshot = pyautogui.screenshot()
        buf = io.BytesIO()
        screenshot.save(buf, format="PNG")
        png_bytes = buf.getvalue()
        # Encryption process
        aesgcm = AESGCM(cls.ENCRYPTION_KEY)
        nonce = os.urandom(12)
        encrypted = aesgcm.encrypt(nonce, png_bytes, None)
        # File write karna
        with open(filepath, "wb") as f:
            f.write(nonce + encrypted)
            print(f"[SCREENSHOT ENCRYPTED & SAVED] {filepath}")
            LoggerService.log(f"SCREENSHOT CAPTURED : {filepath}")
            # Database operations
            connection = Database.connect()
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO screenshots
                (id, employee_id, file_path, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (screenshot_id, SessionManager.employee_id, filepath, timestamp),
            )
            connection.commit()
            connection.close()
            # API Upload block
            try:
                with open(filepath, "rb") as file:
                    response = requests.post(
                        "http://127.0.0.1:8000/api/screenshots/upload",
                        files={"screenshot": file},
                        headers={
                            "Authorization": f"Bearer {SessionManager.auth_token}"
                        },
                        timeout=10,
                    )
                    print("[UPLOAD RESPONSE]", response.status_code, response.text)
                    if response.status_code == 200:
                        SyncManager.mark_uploaded(screenshot_id)
            except Exception as error:
                print("[UPLOAD ERROR]", error)
            return {"id": screenshot_id,
                    "path": filepath,
                    "timestamp": timestamp
                }