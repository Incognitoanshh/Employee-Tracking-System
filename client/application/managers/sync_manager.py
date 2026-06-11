import os
import requests

from client.application.managers.session_manager import SessionManager
from client.core.config import API_BASE_URL
from client.infrastructure.database.database import Database


class SyncManager:
    @staticmethod
    def _auth_headers():
        if not SessionManager.auth_token:
            return None
        return {"Authorization": f"Bearer {SessionManager.auth_token}"}

    @staticmethod
    def get_pending_screenshots():
        connection = Database.connect()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT *
            FROM screenshots
            WHERE uploaded = 0
            """
        )
        data = cursor.fetchall()
        connection.close()
        return data

    @staticmethod
    def mark_uploaded(screenshot_id):
        connection = Database.connect()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE screenshots
            SET uploaded = 1
            WHERE id = ?
            """,
            (screenshot_id,),
        )
        connection.commit()
        connection.close()

    @staticmethod
    def get_pending_logs():
        connection = Database.connect()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT *
            FROM pending_logs
            WHERE uploaded = 0
            """
        )
        data = cursor.fetchall()
        connection.close()
        return data

    @staticmethod
    def mark_log_uploaded(log_id):
        connection = Database.connect()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE pending_logs
            SET uploaded = 1
            WHERE id = ?
            """,
            (log_id,),
        )
        connection.commit()
        connection.close()

    @staticmethod
    def retry_uploads():
        headers = SyncManager._auth_headers()
        if headers is None:
            print("[SYNC SKIP] No auth token present")
            return

        pending = SyncManager.get_pending_screenshots()

        for screenshot in pending:
            try:
                file_path = screenshot["file_path"]
                screenshot_id = screenshot["id"]

                if not os.path.exists(file_path):
                    print(f"[SYNC SKIP] File not found: {screenshot_id}")
                    SyncManager.mark_uploaded(screenshot_id)
                    continue

                with open(file_path, "rb") as file:
                    response = requests.post(
                        f"{API_BASE_URL}/screenshots/upload",
                        files={"screenshot": file},
                        headers=headers,
                        timeout=10,
                    )

                print("[SCREENSHOT SYNC]", response.status_code, response.text)

                if response.status_code == 200:
                    SyncManager.mark_uploaded(screenshot_id)
                    print("[SYNC SUCCESS]", screenshot_id)

            except Exception as error:
                print("[SYNC FAILED]", error)

    @staticmethod
    def retry_logs():
        headers = SyncManager._auth_headers()
        if headers is None:
            print("[SYNC SKIP] No auth token present")
            return

        pending_logs = SyncManager.get_pending_logs()

        for log in pending_logs:
            try:
                payload = {
                    "employee_id": log["employee_id"],
                    "activity": log["activity"],
                }

                response = requests.post(
                    f"{API_BASE_URL}/logs/create",
                    json=payload,
                    headers=headers,
                    timeout=5,
                )

                print("[LOG SYNC]", response.status_code, response.text)

                if 200 <= response.status_code < 300:
                    SyncManager.mark_log_uploaded(log["id"])
                    print("[LOG SYNC SUCCESS]", log["id"])

            except Exception as error:
                print("[LOG SYNC FAILED]", error)

