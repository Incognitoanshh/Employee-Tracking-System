from datetime import datetime
import requests

from client.application.managers.session_manager import SessionManager
from client.infrastructure.database.database import Database
from client.core.config import API_BASE_URL


class LoggerService:

    LOG_FILE = "storage/app.log"

    @staticmethod
    def log(message):

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Local file log
        with open(LoggerService.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")

            employee_id = SessionManager.employee_id
            if not employee_id:
                return

            connection = Database.connect()
            cursor     = connection.cursor()

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

            # API upload try karo
            try:
                response = requests.post(
                    f"{API_BASE_URL}/logs/create",   # hardcoded URL bhi fix hua
                    json={
                        "employee_id": employee_id,
                        "activity":    message,
                    },
                    headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                    timeout=5,
                )

                if response.status_code == 200:
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
