from datetime import datetime
import os
import requests

from client.application.managers.session_manager import SessionManager
from client.infrastructure.database.database import Database
from client.services.settings_service import SettingsService
from client.core.config import API_BASE_URL, STORAGE_DIR


class LoggerService:

    LOG_FILE = os.path.join(STORAGE_DIR, "app.log")
    CRITICAL_LOG_FILE = os.path.join(STORAGE_DIR, "critical_errors.log")

    # LOG ROTATION — pehle bilkul nahi thi.
    #
    # app.log sirf append hoti thi, kabhi rotate/truncate nahi. Verbose
    # logging ON hone par ye ~1 MB/din badhti hai (config sync har 5s +
    # har schedule event) — mahino chalti app pe sau MB tak pahunch jaati,
    # aur employee ki disk ka koi hisaab hi nahi rehta.
    #
    # Simple size-based rotation: 5 MB pe app.log -> app.log.1 (purani .1
    # delete). Ek backup rakhte hain — debugging ke liye kaafi hai, aur
    # disk usage 10 MB pe hard-capped ho jaata hai.
    MAX_LOG_BYTES = 5 * 1024 * 1024
    LOG_BACKUPS = 1

    @staticmethod
    def _rotate_if_needed(path: str) -> None:
        """Size limit cross hone par log file rotate karo. Kabhi raise nahi karta."""
        try:
            if not os.path.exists(path):
                return
            if os.path.getsize(path) < LoggerService.MAX_LOG_BYTES:
                return
            backup = f"{path}.1"
            if os.path.exists(backup):
                os.remove(backup)
            os.replace(path, backup)
        except Exception:
            # Rotation kabhi logging ko fail na kare.
            pass

    @staticmethod
    def _fallback_critical_log(message: str) -> None:
        """Last-resort file sink. Never raises."""
        try:
            os.makedirs(STORAGE_DIR, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            LoggerService._rotate_if_needed(LoggerService.CRITICAL_LOG_FILE)
            with open(LoggerService.CRITICAL_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {message}\n")
        except Exception:
            pass

    @staticmethod
    def log(message):
        import sys

        os.makedirs(STORAGE_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Primary persistent sink
        try:
            LoggerService._rotate_if_needed(LoggerService.LOG_FILE)
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
                INSERT INTO pending_logs (employee_id, activity, timestamp)
                VALUES (?, ?, ?)
                """,
                (employee_id, message, timestamp)
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


