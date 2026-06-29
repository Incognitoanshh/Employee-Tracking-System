from datetime import datetime
import requests

from client.application.managers.session_manager import SessionManager
from client.infrastructure.database.database import Database
from client.services.settings_service import SettingsService
from client.core.config import API_BASE_URL


class LoggerService:

    LOG_FILE = "storage/app.log"
    CRITICAL_LOG_FILE = "storage/critical_errors.log"

    @staticmethod
    def _fallback_critical_log(message: str) -> None:
        """Last-resort file sink. Never raises."""
        import os

        try:
            os.makedirs("storage", exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(LoggerService.CRITICAL_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {message}\n")
        except Exception:
            pass

    @staticmethod
    def log(message):
        import os
        import sys

        os.makedirs("storage", exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Primary persistent sink
        try:
            with open(LoggerService.LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {message}\n")
        except Exception as e:
            # If primary file write fails, also try a dedicated fallback file.
            try:
                LoggerService._fallback_critical_log(
                    f"[LoggerService] file write failed: {e}; original={message}"
                )
            except Exception:
                # Last resort: stderr (may be invisible in windowed builds)
                print(f"[LoggerService] file write failed: {e}", file=sys.stderr)

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
            LoggerService._fallback_critical_log(
                f"[LoggerService] local DB insert failed: {e}; activity={message}"
            )
            print(f"[LoggerService] local DB insert failed: {e}", file=sys.stderr)


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
            LoggerService._fallback_critical_log(
                f"[LoggerService] API upload failed (will retry later): {error}; activity={message}"
            )
            print(
                f"[LoggerService] API upload failed (will retry later): {error}",
                file=sys.stderr,
            )


    @staticmethod
    def log_verbose(message):
        """Noisy/frequent logs ke liye — by default DISABLED."""
        verbose_enabled = SettingsService.get_setting(
            "verbose_logging", "false"
        )
        if str(verbose_enabled).strip().lower() != "true":
            return
        LoggerService.log(message)

