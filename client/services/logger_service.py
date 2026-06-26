import os
from datetime import datetime

from client.application.managers.session_manager import SessionManager
from client.infrastructure.database.database import Database
from client.services.settings_service import SettingsService


class LoggerService:

    LOG_FILE = "storage/app.log"

    @staticmethod
    def _ensure_log_dir():
        os.makedirs("storage", exist_ok=True)

    @staticmethod
    def log(message: str, level: str = "INFO") -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        LoggerService._ensure_log_dir()
        try:
            with open(LoggerService.LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] [{level}] {message}\n")
        except Exception as e:
            print(f"[LOG FILE ERROR] {e}")

        try:
            with Database.get_connection() as conn:
                conn.cursor().execute(
                    """
                    INSERT INTO app_logs
                        (timestamp, level, source, message)
                    VALUES (?, ?, ?, ?)
                    """,
                    (timestamp, level, "app", message),
                )
        except Exception as e:
            print(f"[LOG DB ERROR] {e}")

    @staticmethod
    def log_warning(message: str) -> None:
        LoggerService.log(message, level="WARNING")

    @staticmethod
    def log_error(message: str) -> None:
        LoggerService.log(message, level="ERROR")

    @staticmethod
    def log_verbose(message: str) -> None:
        verbose_enabled = SettingsService.get_setting("verbose_logging", "false")
        if str(verbose_enabled).strip().lower() != "true":
            return
        LoggerService.log(message, level="DEBUG")
