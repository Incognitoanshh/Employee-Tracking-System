from datetime import datetime

from client.infrastructure.database.database import Database
from client.application.managers.session_manager import SessionManager
from client.security.crypto_engine import CryptoEngine


class SessionLogManager:

    @staticmethod
    def get_last_session():
        """
        Sabse recent ACTIVE session return karo (auto-login ke liye).
        Explicit logout ke baad status 'LOGGED_OUT'/'FORCE_CLOSED' ho jata
        hai — wo session dobara auto-login se resurrect nahi hogi, sirf
        genuinely-open session (app crash/reboot se band hui) restore hogi.
        Auth token yahan decrypt karke return hota hai; expiry check
        AutoLoginManager karta hai, yaha sirf raw data milta hai.
        Returns dict {employee_id, auth_token} ya None.
        """
        conn = Database.connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT employee_id, auth_token
            FROM sessions
            WHERE status = 'ACTIVE'
            ORDER BY id DESC LIMIT 1
            """
        )
        row = cur.fetchone()
        conn.close()

        if not row or not row["auth_token"]:
            return None

        try:
            # start_session() encrypt_token() use karta hai (base64 string
            # return karta hai) — isliye yahan decrypt_token() hi use karo,
            # decrypt_bytes() nahi (wo raw blob expect karta hai).
            decrypted_token = CryptoEngine.decrypt_token(row["auth_token"])
        except Exception:
            return None

        return {
            "employee_id": row["employee_id"],
            "auth_token": decrypted_token,
        }

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
    def update_active_token():
        """
        Rewrite the stored token for the open session from SessionManager.

        The token saved at login is what AutoLoginManager presents on the
        next app start. Changing a password issues a new token and ends
        every session tied to the old one, so without this the stored copy
        is dead the moment the change succeeds: the app keeps working until
        it is closed, then silently fails to auto-login and drops the
        employee at the sign-in screen with no explanation.
        """
        if not SessionManager.employee_id or not SessionManager.auth_token:
            return

        conn = Database.connect()
        cur  = conn.cursor()
        cur.execute(
            """
            UPDATE sessions
            SET auth_token = ?
            WHERE employee_id = ? AND status = 'ACTIVE'
            """,
            (
                CryptoEngine.encrypt_token(SessionManager.auth_token),
                SessionManager.employee_id,
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
