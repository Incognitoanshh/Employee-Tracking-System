from datetime import datetime

from client.infrastructure.database.database import Database

from client.application.managers.session_manager import SessionManager


class ShiftManager:

    @staticmethod
    def start_shift():

        connection = Database.connect()

        cursor = connection.cursor()

        cursor.execute("""

            INSERT INTO shifts (

                employee_id,

                login_time

            )

            VALUES (?, ?)

        """, (

            SessionManager.employee_id,

            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        ))

        connection.commit()

        connection.close()

    @staticmethod
    def end_shift():

        connection = Database.connect()

        cursor = connection.cursor()

        cursor.execute("""

            SELECT id, login_time

            FROM shifts

            ORDER BY id DESC

            LIMIT 1

        """)

        shift = cursor.fetchone()

        logout_time = datetime.now()

        login_time = datetime.strptime(

            shift[1],

            "%Y-%m-%d %H:%M:%S"

        )

        total_time = str(
            logout_time - login_time
        )

        cursor.execute("""

            UPDATE shifts

            SET

                logout_time = ?,

                total_hours = ?

            WHERE id = ?

        """, (

            logout_time.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

            total_time,

            shift[0]

        ))

        connection.commit()

        connection.close()