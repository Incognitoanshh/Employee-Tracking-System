from datetime import datetime
import requests

from client.application.managers.session_manager import SessionManager
from client.infrastructure.database.database import Database
from client.services.settings_service import SettingsService
from client.core.config import API_BASE_URL


class LoggerService:

    LOG_FILE = "storage/app.log"

    @staticmethod
    def log(message):
        import os
        os.makedirs("storage", exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # BUG FIX: File open/close alag karo DB se - pehle file mein likhna tha
        # lekin DB code file context ke andar tha (galat indent), isliye
        # file hamesha open rehta tha jab tak employee_id nahi milta tha
        try:
            with open(LoggerService.LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {message}\n")
        except Exception as e:
            print(f"[LOG FILE ERROR] {e}")

        employee_id = SessionManager.employee_id
        if not employee_id:
            return

        # Local DB mein save karo
        log_id = None
        try:
            connection = Database.connect()
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO pending_logs (employee_id, activity)
                VALUES (?, ?)
                """,
                (employee_id, message)
            )
            log_id = cursor.lastrowid
            connection.commit()
            connection.close()
        except Exception as e:
            print(f"[LOG DB ERROR] {e}")

        # API upload try karo
        try:
            response = requests.post(
                f"{API_BASE_URL}/logs/create",
                json={
                    "employee_id": employee_id,
                    "activity":    message,
                },
                headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                timeout=5,
            )

            if response.status_code == 200 and log_id is not None:
                conn = Database.connect()
                cur  = conn.cursor()
                cur.execute(
                    "UPDATE pending_logs SET uploaded = 1 WHERE id = ?",
                    (log_id,)
                )
                conn.commit()
                conn.close()

        except Exception as error:
            print("[LOG API ERROR]", error)

    @staticmethod
    def log_verbose(message):
        """
        Noisy/frequent logs ke liye — by default DISABLED.
        Admin panel se per-employee 'verbose_logging' flag ON karne par hi
        actual LoggerService.log() call hota hai.
        """
        verbose_enabled = SettingsService.get_setting(
            "verbose_logging", "false"
        )
        if str(verbose_enabled).strip().lower() != "true":
            return
        LoggerService.log(message)
