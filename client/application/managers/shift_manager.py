from datetime import datetime

import requests

from client.infrastructure.database.database import Database
from client.application.managers.session_manager import SessionManager
from client.core.config import API_BASE_URL


class ShiftManager:

    @staticmethod
    def start_shift():

        print("[DEBUG start_shift] START")

        # 1. Check if ShiftManager is actually being executed
        print("[DEBUG start_shift] method called")

        # 2. Log SessionManager values
        employee_id = getattr(SessionManager, 'employee_id', None)
        auth_token = getattr(SessionManager, 'auth_token', None)

        print(f"[DEBUG] employee_id={employee_id}, auth_token_present={auth_token is not None}")

        if not employee_id:
            print("[DEBUG] ERROR: SessionManager.employee_id is None!")
            return

        # 3. Log API_BASE_URL
        print(f"[DEBUG] API_BASE_URL={API_BASE_URL}")

        login_time = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # 7. Check if Database.connect() throws
        try:
            connection = Database.connect()
            print("[DEBUG] Database.connect() successful")
        except Exception as e:
            print(f"[DEBUG] Database.connect() FAILED: {e}")
            return

        cursor = connection.cursor()

        # FIX: Close any existing open shifts for this employee before creating new one
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            cursor.execute(
                """
                UPDATE shifts
                SET logout_time = ?, total_hours = 'FORCE_CLOSED'
                WHERE employee_id = ? AND logout_time IS NULL
                """,
                (now_str, employee_id)
            )
            print(f"[DEBUG] Closed existing shifts, rows affected: {cursor.rowcount}")
        except Exception as e:
            print(f"[DEBUG] UPDATE shifts FAILED: {e}")
            connection.close()
            return

        try:
            cursor.execute(
                """
                INSERT INTO shifts (
                    employee_id,
                    login_time
                )
                VALUES (?, ?)
                """,
                (
                    employee_id,
                    login_time
                )
            )
            print(f"[DEBUG] INSERT shifts successful, lastrowid={cursor.lastrowid}")
        except Exception as e:
            print(f"[DEBUG] INSERT shifts FAILED: {e}")
            connection.close()
            return

        connection.commit()
        connection.close()
        print("[DEBUG] Local shifts table committed and closed")

        # 4 & 5. Log request payload and send attendance/login request
        request_payload = {
            "employee_id": employee_id,
            "login_time": login_time
        }
        request_headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json"
        }

        print(f"[DEBUG] POST {API_BASE_URL}/attendance/login")
        print(f"[DEBUG] payload={request_payload}")
        print(f"[DEBUG] headers_authorized={request_headers['Authorization'] is not None}")

        try:

            response = requests.post(
                f"{API_BASE_URL}/attendance/login",
                json=request_payload,
                headers=request_headers,
                timeout=10
            )

            print(f"[DEBUG] RESPONSE status={response.status_code}")
            print(f"[DEBUG] RESPONSE body={response.text[:500] if response.text else 'empty'}")

            # 8 & 9. Check if row was inserted then closed
            if response.status_code == 200:
                try:
                    data = response.json()
                    print(f"[DEBUG] RESPONSE json={data}")
                except Exception as e:
                    print(f"[DEBUG] JSON parse error: {e}")

        except Exception as error:
            print(f"[DEBUG] attendance/login request FAILED: {error}")

        print("[DEBUG start_shift] END")

    @staticmethod
    def end_shift():

        connection = Database.connect()

        cursor = connection.cursor()

        # FIX: Filter by employee_id to close the correct shift
        cursor.execute(
            """
            SELECT id, login_time
            FROM shifts
            WHERE employee_id = ? AND logout_time IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (SessionManager.employee_id,)
        )

        shift = cursor.fetchone()

        if not shift:
            connection.close()
            print("[NO OPEN SHIFT TO CLOSE]")
            return

        logout_time = datetime.now()

        login_time = datetime.strptime(
            shift[1],
            "%Y-%m-%d %H:%M:%S"
        )

        # FIX: Format as PostgreSQL interval (e.g., '1 hour 30 minutes')
        duration = logout_time - login_time
        total_seconds = int(duration.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60

        if hours > 0 and minutes > 0:
            total_time = f"{hours} hour{'s' if hours != 1 else ''} {minutes} minutes"
        elif hours > 0:
            total_time = f"{hours} hour{'s' if hours != 1 else ''}"
        elif minutes > 0:
            total_time = f"{minutes} minutes"
        else:
            total_time = "0 minutes"

        cursor.execute(
            """
            UPDATE shifts
            SET
            logout_time = ?,
            total_hours = ?
            WHERE id = ?
            """,
            (
                logout_time.strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                total_time,
                shift[0]
            )
        )

        connection.commit()
        connection.close()

        try:

            response = requests.post(
                f"{API_BASE_URL}/attendance/logout",
                json={
                    "employee_id": SessionManager.employee_id,
                    "logout_time": logout_time.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "total_hours": total_time
                },
                headers={
                    "Authorization":
                        f"Bearer {SessionManager.auth_token}"
                    },
                    timeout=10
            )

            print(
                "[ATTENDANCE LOGOUT]",
                response.status_code,
                response.text
            )

        except Exception as error:

            print(
                "[ATTENDANCE LOGOUT ERROR]",
                error
            )