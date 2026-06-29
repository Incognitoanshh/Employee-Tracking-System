from datetime import datetime

from client.infrastructure.database.database import Database
from client.application.managers.session_manager import SessionManager
from client.security.crypto_engine import CryptoEngine


class SessionLogManager:

    @staticmethod
    def start_session():

        conn = Database.connect()
        cur  = conn.cursor()

        # Close any existing ACTIVE sessions for this employee
        cur.execute(
            """
            UPDATE sessions
            SET logout_time = ?, status = ?
            WHERE employee_id = ? AND status = 'ACTIVE'
            """,
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "FORCE_CLOSED",
                SessionManager.employee_id,
            )
        )

        # BUG 4 FIX: CryptoEngine() instance nahi, static method use karo
        encrypted_token = CryptoEngine.encrypt_token(SessionManager.auth_token)

        cur.execute(
            """
            INSERT INTO sessions (employee_id, auth_token, login_time, status)
            VALUES (?, ?, ?, ?)
            """,
            (
                SessionManager.employee_id,
                encrypted_token,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "ACTIVE",
            )
        )

        conn.commit()
        conn.close()

    @staticmethod
    def end_session():

        conn = Database.connect()
        cur  = conn.cursor()

        # FIX: Filter by employee_id to close the correct session
        cur.execute(
            """
            UPDATE sessions
            SET logout_time = ?, status = ?
            WHERE employee_id = ? AND status = 'ACTIVE'
            """,
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "LOGGED_OUT",
                SessionManager.employee_id,
            )
        )

        conn.commit()
        conn.close()
