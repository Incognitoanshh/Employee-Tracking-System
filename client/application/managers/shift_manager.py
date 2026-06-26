from datetime import datetime
import requests

from client.infrastructure.database.database import Database
from client.application.managers.session_manager import SessionManager
from client.services.logger_service import LoggerService
from client.core.config import API_BASE_URL


class ShiftManager:

    @staticmethod
    def start_shift():
        employee_id = SessionManager.employee_id
        if not employee_id:
            LoggerService.log_error("ShiftManager.start_shift: no employee_id")
            return

        # Use ISO8601 with timezone for consistent timestamps
        login_time = datetime.now().astimezone().isoformat()

        # ✅ DB: shifts table mein insert
        try:
            with Database.get_connection() as conn:
                cursor = conn.cursor()

                # Purani open shifts force-close karo
                cursor.execute(
                    """
                    UPDATE shifts
                    SET logout_time = ?, total_seconds = 0,
                    status = 'FORCE_CLOSED'
                    WHERE employee_id = ? AND logout_time IS NULL
                    """,
                    (login_time, employee_id),
                )

                cursor.execute(
                    """
                    INSERT INTO shifts (employee_id, login_time, status)
                    VALUES (?, ?, 'ACTIVE')
                    """,
                    (employee_id, login_time),
                )

            LoggerService.log(f"ShiftManager: shift started for {employee_id}")

        except Exception as e:
            LoggerService.log_error(f"ShiftManager.start_shift DB error: {e}")
            return

        # ✅ Server notify — fail hone pe sirf log karo, crash nahi
        ShiftManager._notify_server_login(employee_id, login_time)

    @staticmethod
    def end_shift():
        employee_id = SessionManager.employee_id
        if not employee_id:
            return

        # Use ISO8601 with timezone for consistent server storage
        logout_time = datetime.now().astimezone()
        logout_str  = logout_time.isoformat()

        try:
            with Database.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, login_time FROM shifts
                    WHERE employee_id = ? AND logout_time IS NULL
                    ORDER BY id DESC LIMIT 1
                    """,
                    (employee_id,),
                )
                shift = cursor.fetchone()

                if not shift:
                    LoggerService.log("ShiftManager: no open shift to close")
                    return

                # ✅ Dict-style access (row_factory) - handle both formats
                login_time_str = shift["login_time"]
                try:
                    login_dt = datetime.fromisoformat(login_time_str)
                except ValueError:
                    login_dt = datetime.strptime(
                        login_time_str, "%Y-%m-%d %H:%M:%S"
                    )
                total_seconds  = int(
                    (logout_time - login_dt).total_seconds()
                )

                cursor.execute(
                    """
                    UPDATE shifts
                    SET logout_time = ?, total_seconds = ?, status = 'CLOSED'
                    WHERE id = ?
                    """,
                    (logout_str, total_seconds, shift["id"]),
                )

            LoggerService.log(
                f"ShiftManager: shift ended — {total_seconds}s total"
            )

        except Exception as e:
            LoggerService.log_error(f"ShiftManager.end_shift DB error: {e}")
            return

        ShiftManager._notify_server_logout(employee_id, logout_str, total_seconds)

    @staticmethod
    def _notify_server_login(employee_id: str, login_time: str):
        try:
            response = requests.post(
                f"{API_BASE_URL}/attendance/login",
                json={
                    "employee_id": employee_id,
                    "login_time":  login_time,
                },
                headers={
                    "Authorization": f"Bearer {SessionManager.auth_token}"
                },
                timeout=10,
            )
            LoggerService.log(
                f"ShiftManager: attendance login "
                f"status={response.status_code}"
            )
        except Exception as e:
            LoggerService.log_error(f"ShiftManager login notify error: {e}")

    @staticmethod
    def _notify_server_logout(
        employee_id:   str,
        logout_time:   str,
        total_seconds: int,
    ):
        try:
            response = requests.post(
                f"{API_BASE_URL}/attendance/logout",
                json={
                    "employee_id":   employee_id,
                    "logout_time":   logout_time,
                    "total_seconds": total_seconds,
                },
                headers={
                    "Authorization": f"Bearer {SessionManager.auth_token}"
                },
                timeout=10,
            )
            LoggerService.log(
                f"ShiftManager: attendance logout "
                f"status={response.status_code}"
            )
        except Exception as e:
            LoggerService.log_error(f"ShiftManager logout notify error: {e}")