import os
import requests

from client.application.managers.session_manager import SessionManager
from client.core.config import API_BASE_URL
from client.infrastructure.database.database import Database
from client.security.crypto_engine import CryptoEngine


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
    def retry_uploads(max_retries: int = 5):
        headers = SyncManager._auth_headers()
        if headers is None:
            return

        # Lazy import to avoid circular import at module load time.
        from client.services.logger_service import LoggerService

        pending = SyncManager.get_pending_screenshots()

        for screenshot in pending[:max_retries]:
            try:
                file_path = screenshot["file_path"]
                screenshot_id = screenshot["id"]

                if not os.path.exists(file_path):
                    SyncManager.mark_uploaded(screenshot_id)
                    continue

                # BUG FIX: pehle yahan local .enc file ke RAW (still encrypted)
                # bytes seedha server ko upload ho jaate the. Server unhe
                # PNG samajh ke save kar leta tha, lekin wo encrypted garbage
                # hote — admin panel mein screenshot kabhi khulti nahi thi.
                # Ab upload se pehle decrypt karke plain PNG bytes bhejte hain.
                with open(file_path, "rb") as file:
                    encrypted_bytes = file.read()

                try:
                    plaintext_png = CryptoEngine.decrypt_bytes(encrypted_bytes)
                except Exception as decrypt_error:
                    LoggerService.log(
                        f"SyncManager: failed to decrypt {file_path} for retry "
                        f"upload — {decrypt_error}. Skipping, file kept for now."
                    )
                    continue

                response = requests.post(
                    f"{API_BASE_URL}/screenshots/upload",
                    files={"screenshot": (f"{screenshot_id}.png", plaintext_png, "image/png")},
                    headers=headers,
                    timeout=10,
                )

                if response.status_code == 200:
                    SyncManager.mark_uploaded(screenshot_id)
                else:
                    LoggerService.log(
                        f"SyncManager: retry upload failed for {screenshot_id} — "
                        f"HTTP {response.status_code} {response.text[:200]}"
                    )

            except Exception as error:
                # BUG FIX: pehle silently pass ho jaata tha, debugging
                # impossible thi. Ab file log mein likha jaata hai.
                LoggerService.log(f"SyncManager: retry_uploads error — {error}")

    @staticmethod
    def cleanup_old_orphans(days=7):
        """X days se purane unuploaded local records delete karo"""
        from client.services.logger_service import LoggerService
        try:
            from datetime import datetime, timedelta
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            connection = Database.connect()
            cursor = connection.cursor()
            # Purane unuploaded screenshots
            cursor.execute(
                "SELECT id, file_path FROM screenshots WHERE uploaded = 0 AND timestamp < ?",
                (cutoff,)
            )
            old_screenshots = cursor.fetchall()
            for row in old_screenshots:
                try:
                    if row["file_path"] and os.path.exists(row["file_path"]):
                        os.remove(row["file_path"])
                except Exception as e:
                    LoggerService.log(f"SyncManager: failed to remove orphan file {row['file_path']} — {e}")
            cursor.execute(
                "DELETE FROM screenshots WHERE uploaded = 0 AND timestamp < ?",
                (cutoff,)
            )
            # Purane unuploaded logs - cutoff se purane wale delete karo
            # BUG FIX: pehle yahan cutoff ignore ho rahi thi — saare unuploaded
            # logs delete ho jaate the chahe wo recent hi kyun na hon.
            # pending_logs mein timestamp nahi hai, isliye old IDs (top 1000
            # oldest) ko cutoff proxy ke taur pe use karo.
            cursor.execute(
                "DELETE FROM pending_logs WHERE uploaded = 0 AND id < (SELECT MIN(id) + 1000 FROM pending_logs WHERE uploaded = 0)",
            )
            connection.commit()
            connection.close()
        except Exception as e:
            LoggerService.log(f"SyncManager: cleanup_old_orphans error — {e}")

    @staticmethod
    def retry_logs(max_retries: int = 20):
        headers = SyncManager._auth_headers()
        if headers is None:
            return

        from client.services.logger_service import LoggerService

        pending_logs = SyncManager.get_pending_logs()

        for log in pending_logs[:max_retries]:
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

                if 200 <= response.status_code < 300:
                    SyncManager.mark_log_uploaded(log["id"])
                else:
                    LoggerService.log(
                        f"SyncManager: retry_logs failed for log {log['id']} — "
                        f"HTTP {response.status_code} {response.text[:200]}"
                    )

            except Exception as error:
                LoggerService.log(f"SyncManager: retry_logs error — {error}")

