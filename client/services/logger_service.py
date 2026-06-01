from datetime import datetime
import requests

from client.infrastructure.database.database import Database


class LoggerService:

    LOG_FILE = "storage/app.log"

    @staticmethod
    def log(message):

        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # Local file log
        with open(
            LoggerService.LOG_FILE,
            "a",
            encoding="utf-8"
        ) as file:

            file.write(
                f"[{timestamp}] {message}\n"
            )

        # Save locally in SQLite first
        connection = Database.connect()

        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO pending_logs (
                employee_id,
                activity
            )
            VALUES (?, ?)
            """,
            (
                "EMP001",
                message
            )
        )

        log_id = cursor.lastrowid

        connection.commit()

        connection.close()

        # Try API upload
        try:

            response = requests.post(

                "http://127.0.0.1:8000/api/logs/create",

                json={

                    "employee_id": "EMP001",

                    "activity": message

                },

                timeout=1

            )

            if response.status_code == 200:

                connection = Database.connect()

                cursor = connection.cursor()

                cursor.execute(
                    """
                    UPDATE pending_logs
                    SET uploaded = 1
                    WHERE id = ?
                    """,
                    (
                        log_id,
                    )
                )

                connection.commit()

                connection.close()

        except Exception as error:

            print(
                "[LOG API ERROR]",
                error
            )