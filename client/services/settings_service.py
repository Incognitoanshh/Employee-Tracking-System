from datetime import datetime
from client.infrastructure.database.database import Database


class SettingsService:

    @staticmethod
    def save_setting(key: str, value: str) -> None:
        """Single setting save karo."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with Database.get_connection() as conn:
            conn.cursor().execute(
                """
                INSERT OR REPLACE INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                (key, str(value), now),
            )

    @staticmethod
    def save_settings_bulk(data: dict) -> None:
        """Multiple settings ek hi connection mein save karo — config sync ke liye."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with Database.get_connection() as conn:
            cursor = conn.cursor()
            for key, value in data.items():
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (key, str(value), now),
                )

    @staticmethod
    def get_setting(key: str, default: str = None) -> str | None:
        """Setting read karo — default return karo agar na mile."""
        try:
            with Database.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT value FROM settings WHERE key = ?",
                    (key,),
                )
                result = cursor.fetchone()
            return result[0] if result else default
        except Exception:
            return default

    @staticmethod
    def get_all_settings() -> dict:
        """Saari settings ek saath load karo — startup ke liye."""
        try:
            with Database.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT key, value FROM settings")
                rows = cursor.fetchall()
            return {row["key"]: row["value"] for row in rows}
        except Exception:
            return {}