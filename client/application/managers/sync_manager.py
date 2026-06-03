import requests
import os

from client.infrastructure.database.database import Database
from client.application.managers.session_manager import SessionManager


class SyncManager:

    @staticmethod
    def get_pending_screenshots():
        connection = Database.connect()
        cursor = connection.cursor()

        cursor.execute("""
        SELECT *
        FROM screenshots
        WHERE uploaded = 0
        """)

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
            (screenshot_id,)
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
            (log_id,)
        )

        connection.commit()
        connection.close()

    @staticmethod
    def retry_uploads():

        pending = SyncManager.get_pending_screenshots()

        for screenshot in pending:

            try:

                file_path = screenshot["file_path"]
                screenshot_id = screenshot["id"]

                if not os.path.exists(file_path):

                    print(
                        f"[SYNC SKIP] File not found: {screenshot_id}"
                    )

                    SyncManager.mark_uploaded(
                        screenshot_id
                    )

                    continue

                with open(file_path, "rb") as file:

                    response = requests.post(

                        "http://127.0.0.1:8000/api/screenshots/upload",

                        files={
                            "screenshot": file
                        },

                        headers={
                            "Authorization":
                                f"Bearer {SessionManager.auth_token}"
                            },
                        timeout=10
                    )

                print(
                    "[SCREENSHOT SYNC]",
                    response.status_code,
                    response.text
                )

                if response.status_code == 200:
                    SyncManager.mark_uploaded(
                        screenshot_id
                    )
                    print(
                        "[SYNC SUCCESS]",
                        screenshot_id
                    )

            except Exception as error:

                print(
                    "[SYNC FAILED]",
                    error
                )

    @staticmethod
    def retry_logs():

        pending_logs = SyncManager.get_pending_logs()

        for log in pending_logs:

            try:

                response = requests.post(

                    "http://127.0.0.1:8000/api/logs/create",

                    json={
                        "employee_id":
                            log["employee_id"],

                            "activity":
                                log["activity"]
                            },

                    headers={
                        "Authorization":
                            f"Bearer {SessionManager.auth_token}"
                        },
                    timeout=5
                )

                print(
                    "[LOG SYNC]",
                    response.status_code,
                    response.text
                    )

                if response.status_code == 200:
                    SyncManager.mark_log_uploaded(
                        log["id"]
                    )

                    print(
                        "[LOG SYNC SUCCESS]",
                        log["id"]
                    )
            except Exception as error:
                print(
                    "[LOG SYNC FAILED]",
                    os.error
                )