import os
from datetime import datetime
import requests

from client.application.managers.session_manager import SessionManager
from client.core.config import API_BASE_URL
from client.infrastructure.database.database import Database
from client.services.logger_service import LoggerService


class SyncManager:

    @staticmethod
    def _auth_headers():
        if not SessionManager.auth_token:
            return None
        return {"Authorization": f"Bearer {SessionManager.auth_token}"}

    @staticmethod
    def queue_screenshot(screenshot_id: str):
        LoggerService.log(f"SyncManager: queued screenshot {screenshot_id}")

    @staticmethod
    def get_pending_screenshots():
        with Database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM screenshots
                WHERE upload_status = 'PENDING'
                ORDER BY timestamp ASC
                LIMIT 50
                """
            )
            return cursor.fetchall()

    @staticmethod
    def get_pending_idle_logs():
        with Database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM idle_logs
                WHERE upload_status = 'PENDING'
                  AND idle_end IS NOT NULL
                ORDER BY idle_start ASC
                LIMIT 50
                """
            )
            return cursor.fetchall()

    @staticmethod
    def mark_uploaded(screenshot_id: str):
        with Database.get_connection() as conn:
            conn.cursor().execute(
                """
                UPDATE screenshots
                SET upload_status = 'UPLOADED',
                    last_upload_attempt = ?
                WHERE id = ?
                """,
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), screenshot_id),
            )

    @staticmethod
    def mark_upload_failed(screenshot_id: str):
        with Database.get_connection() as conn:
            conn.cursor().execute(
                """
                UPDATE screenshots
                SET upload_status  = 'FAILED',
                    upload_attempts = upload_attempts + 1,
                    last_upload_attempt = ?
                WHERE id = ?
                """,
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), screenshot_id),
            )

    @staticmethod
    def mark_idle_log_uploaded(log_id: int):
        with Database.get_connection() as conn:
            conn.cursor().execute(
                """
                UPDATE idle_logs
                SET upload_status = 'UPLOADED'
                WHERE id = ?
                """,
                (log_id,),
            )

    @staticmethod
    def retry_uploads():
        headers = SyncManager._auth_headers()
        if headers is None:
            LoggerService.log("SyncManager: no auth token — skip upload")
            return

        with Database.get_connection() as conn:
            conn.cursor().execute(
                """
                UPDATE screenshots
                SET upload_status = 'PENDING'
                WHERE upload_status = 'FAILED'
                  AND upload_attempts < 5
                """
            )

        pending = SyncManager.get_pending_screenshots()
        LoggerService.log(f"SyncManager: {len(pending)} screenshots pending")

        for screenshot in pending:
            screenshot_id = screenshot["id"]
            file_path     = screenshot["file_path"]

            try:
                if not os.path.exists(file_path):
                    LoggerService.log(f"SyncManager: file missing {screenshot_id}")
                    SyncManager.mark_upload_failed(screenshot_id)
                    continue

                with open(file_path, "rb") as f:
                    response = requests.post(
                        f"{API_BASE_URL}/screenshots/upload",
                        files={"screenshot": f},
                        data={
                            "employee_id": screenshot["employee_id"],
                            "session_id":  screenshot["session_id"],
                            "timestamp":   screenshot["timestamp"],
                        },
                        headers=headers,
                        timeout=15,
                    )

                if response.status_code == 200:
                    SyncManager.mark_uploaded(screenshot_id)
                    LoggerService.log(f"SyncManager: uploaded {screenshot_id}")
                else:
                    SyncManager.mark_upload_failed(screenshot_id)

            except Exception as e:
                SyncManager.mark_upload_failed(screenshot_id)
                LoggerService.log(f"SyncManager: error {screenshot_id}: {e}")

    @staticmethod
    def retry_logs():
        headers = SyncManager._auth_headers()
        if headers is None:
            return

        pending_logs = SyncManager.get_pending_idle_logs()

        for log in pending_logs:
            try:
                payload = {
                    "employee_id":      log["employee_id"],
                    "session_id":       log["session_id"],
                    "idle_start_ist":   log["idle_start"],
                    "idle_end_ist":     log["idle_end"],
                    "duration_seconds": log["duration_seconds"],
                }

                response = requests.post(
                    f"{API_BASE_URL}/logs/upload",
                    json=payload,
                    headers=headers,
                    timeout=10,
                )

                if 200 <= response.status_code < 300:
                    SyncManager.mark_idle_log_uploaded(log["id"])

            except Exception as e:
                LoggerService.log(f"SyncManager: idle log error: {e}")

    @staticmethod
    def cleanup_old_orphans(days: int = 7):
        try:
            from datetime import timedelta
            cutoff = (
                datetime.now() - timedelta(days=days)
            ).strftime("%Y-%m-%d %H:%M:%S")

            with Database.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, file_path FROM screenshots
                    WHERE upload_status = 'UPLOADED'
                      AND timestamp < ?
                    """,
                    (cutoff,),
                )
                old = cursor.fetchall()

                for row in old:
                    try:
                        if row["file_path"] and os.path.exists(row["file_path"]):
                            os.remove(row["file_path"])
                    except Exception as fe:
                        LoggerService.log(f"SyncManager cleanup error: {fe}")

                cursor.execute(
                    """
                    DELETE FROM screenshots
                    WHERE upload_status = 'UPLOADED'
                      AND timestamp < ?
                    """,
                    (cutoff,),
                )

        except Exception as e:
            LoggerService.log(f"SyncManager cleanup error: {e}")
