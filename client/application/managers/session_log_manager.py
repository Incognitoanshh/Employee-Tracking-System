from datetime import datetime
from client.infrastructure.database.database import Database
from client.application.managers.session_manager import SessionManager
from client.services.logger_service import LoggerService


class SessionLogManager:

    @staticmethod
    def start_session():
            try:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                with Database.get_connection() as conn:
                    # Purani sessions close karo — current session_id chhod ke
                    conn.cursor().execute(
                        """
                        UPDATE sessions
                        SET logout_time = ?, status = 'FORCE_CLOSED'
                        WHERE employee_id = ?
                        AND status = 'ACTIVE'
                        AND id != ?
                        """,
                        (
                            now,
                            SessionManager.employee_id,
                            SessionManager.session_id or -1,
                        ),
                    )

                LoggerService.log(
                    f"SessionLogManager: session started "
                    f"id={SessionManager.session_id}"
                )

            except Exception as e:
                LoggerService.log_error(f"SessionLogManager.start_session error: {e}")

    @staticmethod
    def end_session():
        """Active session close karo — logout/force logout pe call karo."""
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            with Database.get_connection() as conn:
                conn.cursor().execute(
                    """
                    UPDATE sessions
                    SET logout_time = ?, status = 'LOGGED_OUT'
                    WHERE id = ?
                    AND status = 'ACTIVE'
                    """,
                    (now, SessionManager.session_id),
                )

            LoggerService.log("SessionLogManager: session ended")

        except Exception as e:
            LoggerService.log_error(f"SessionLogManager.end_session error: {e}")