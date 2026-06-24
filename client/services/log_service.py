from client.infrastructure.database.database import Database


class LogService:

    @staticmethod
    def get_idle_logs():

        connection = Database.connect()

        cursor = connection.cursor()

        cursor.execute("""

            SELECT
                employee_id,
                status,
                timestamp

            FROM idle_logs

            ORDER BY id DESC

        """)

        logs = cursor.fetchall()

        connection.close()

        return logs

    @staticmethod
    def get_screenshot_logs():

        connection = Database.connect()

        cursor = connection.cursor()

        cursor.execute("""

            SELECT
                employee_id,
                file_path,
                timestamp

            FROM screenshots

            ORDER BY timestamp DESC

        """)

        logs = cursor.fetchall()

        connection.close()

        return logs