from datetime import datetime

from client.infrastructure.database.database import Database
from client.application.managers.session_manager import SessionManager


class SessionLogManager:

    @staticmethod
    def start_session():

        conn = Database.connect()
        cur = conn.cursor()

        # Purani ACTIVE sessions close karo
        cur.execute(
            """
            UPDATE sessions
            SET
            logout_time = ?,
            status = ?
            WHERE employee_id = ?
            AND status = 'ACTIVE'
            """,
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "FORCE_CLOSED",
                SessionManager.employee_id
            )
        )

        # Nayi session create karo
        cur.execute(
            """
            INSERT INTO sessions (
                employee_id,
                auth_token,
                login_time,
                status
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                SessionManager.employee_id,
                SessionManager.auth_token,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "ACTIVE"
            )
        )

        conn.commit()
        conn.close()

        print("[SESSION STARTED]")

    @staticmethod
    def end_session():

        conn = Database.connect()
        cur = conn.cursor()

        print("[SESSION END STARTED]")

        cur.execute(
            """
            UPDATE sessions
            SET
            logout_time = ?,
            status = ?
            WHERE id = (
                SELECT id
                FROM sessions
                WHERE status = 'ACTIVE'
                ORDER BY id DESC
                LIMIT 1
            )
            """,
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "LOGGED_OUT"
            )
        )

        print("[ROWS UPDATED]", cur.rowcount)

        conn.commit()
        conn.close()

        print("[SESSION ENDED]")