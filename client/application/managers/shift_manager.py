from datetime import datetime
import requests
from client.infrastructure.database.database import Database
from client.application.managers.session_manager import SessionManager
from client.core.config import API_BASE_URL


class ShiftManager:

    @staticmethod
    def _has_open_server_session(employee_id, auth_token):
        """Ask the server whether an open session already exists."""
        try:
            response = requests.get(
                f"{API_BASE_URL}/attendance/all",
                headers={"Authorization": f"Bearer {auth_token}"},
                params={"employee_id": employee_id},
                timeout=10
            )
            data = response.json()
            if data.get("success") and data.get("data"):
                for record in data["data"]:
                    if not record.get("logout_time"):
                        return True  # Already active session hai
        except Exception:
            pass
        return False

    @staticmethod
    def start_shift():
        """Local write plus server sync — unchanged for existing callers."""
        login_time = ShiftManager.start_shift_local()
        if login_time:
            ShiftManager.start_shift_remote(login_time)

    @staticmethod
    def start_shift_local():
        """Local SQLite write only — returns immediately.

        This is split from the network part because EmployeePanel reads the
        latest `shifts` row in its own __init__ (that row is where Session
        Duration comes from). When the whole of start_shift() moved to a
        background thread, the panel read before the row was written and
        Session Duration sat at 00:00:00.

        The local write takes milliseconds, so keeping it on the UI thread
        is fine. Only the network calls needed to move off it.

        Returns the login_time string, or None if there is no session.
        """
        employee_id = getattr(SessionManager, 'employee_id', None)
        if not employee_id:
            return None

        login_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Close any shifts still open in the local DB
        try:
            connection = Database.connect()
            cursor = connection.cursor()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                "UPDATE shifts SET logout_time = ?, total_hours = ? WHERE employee_id = ? AND logout_time IS NULL",
                (now_str, 'FORCE_CLOSED', employee_id)
            )
            cursor.execute(
                "INSERT INTO shifts (employee_id, login_time) VALUES (?, ?)",
                (employee_id, login_time)
            )
            connection.commit()
            connection.close()
        except Exception:
            pass

        return login_time

    @staticmethod
    def start_shift_remote(login_time):
        """Record attendance on the server — the slow part, meant for a worker."""
        employee_id = getattr(SessionManager, 'employee_id', None)
        auth_token  = getattr(SessionManager, 'auth_token', None)
        if not employee_id or not login_time:
            return

        # Skip if the server already has an open session for this employee
        if ShiftManager._has_open_server_session(employee_id, auth_token):
            return

        try:
            requests.post(
                f"{API_BASE_URL}/attendance/login",
                json={"employee_id": employee_id, "login_time": login_time},
                headers={
                    "Authorization": f"Bearer {auth_token}",
                    "Content-Type": "application/json"
                },
                timeout=10
            )
        except Exception:
            pass

    @staticmethod
    def end_shift():
        try:
            connection = Database.connect()
        except Exception:
            return

        cursor = connection.cursor()
        cursor.execute(
            "SELECT id, login_time FROM shifts WHERE employee_id = ? AND logout_time IS NULL ORDER BY id DESC LIMIT 1",
            (SessionManager.employee_id,)
        )
        shift = cursor.fetchone()
        if not shift:
            connection.close()
            return

        logout_time   = datetime.now()
        try:
            login_time = datetime.strptime(shift[1], "%Y-%m-%d %H:%M:%S")
        except Exception:
            connection.close()
            # login_time is corrupt or unparseable, so the duration cannot
            # be computed — but leaving logout_time NULL is worse, because
            # the row then looks permanently "open".
            # Fall back to Database.close_current_shift(), which sets only
            # logout_time (best effort, no duration), so the row never stays
            # dangling
            # na rahe.
            try:
                Database.close_current_shift(SessionManager.employee_id)
            except Exception:
                pass
            return

        duration      = logout_time - login_time
        total_seconds = int(duration.total_seconds())
        hours         = total_seconds // 3600
        minutes       = (total_seconds % 3600) // 60

        if hours > 0 and minutes > 0:
            total_time = f"{hours} hour{'s' if hours != 1 else ''} {minutes} minutes"
        elif hours > 0:
            total_time = f"{hours} hour{'s' if hours != 1 else ''}"
        elif minutes > 0:
            total_time = f"{minutes} minutes"
        else:
            total_time = "0 minutes"

        cursor.execute(
            "UPDATE shifts SET logout_time = ?, total_hours = ? WHERE id = ?",
            (logout_time.strftime("%Y-%m-%d %H:%M:%S"), total_time, shift[0])
        )
        connection.commit()
        connection.close()

        try:
            requests.post(
                f"{API_BASE_URL}/attendance/logout",
                json={
                    "employee_id": SessionManager.employee_id,
                    "logout_time": logout_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "total_hours": total_time
                },
                headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                timeout=10
            )
        except Exception:
            pass
