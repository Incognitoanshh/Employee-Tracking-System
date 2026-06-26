import requests
from client.core.config import API_BASE_URL
from client.application.managers.session_manager import SessionManager
from client.infrastructure.database.database import Database
from client.services.logger_service import LoggerService
from datetime import datetime


class AuthService:

    @staticmethod
    def login(username: str, password: str) -> dict:
        try:
            response = requests.post(
                f"{API_BASE_URL}/auth/login",
                json={
                    "username":  username,
                    "password":  password,
                    "device_id": SessionManager.get_device_id(),
                },
                timeout=10,
            )

            # ✅ HTTP error check
            if response.status_code != 200:
                try:
                    msg = response.json().get("message", "Login failed")
                except Exception:
                    msg = f"Server error ({response.status_code})"
                return {"success": False, "message": msg}

            data = response.json()
            # ✅ Server response normalize karo — spec format handle karo
            # Server bhejta hai: { "employee": { "employee_id": ..., "full_name": ... } }
            employee    = data.get("employee", {})
            shift       = data.get("shift", {})
            config      = data.get("config", {})
            token       = data.get("token", "")
            role        = data.get("role", "employee")
            
            if not token:
                return {"success": False, "message": "No token received"}
            
            employee_id = employee.get("employee_id") or data.get("employee_id")
            full_name   = employee.get("full_name", "")
            
            if not employee_id:
                return {"success": False, "message": "Invalid server response"}
            # ✅ Session DB mein insert karo — id wapas lao
            session_id = AuthService._create_db_session(
                employee_id = employee_id,
                auth_token  = token,
                shift_start = shift.get("start_ist", ""),
                shift_end   = shift.get("end_ist",   ""),
            )

            return {
            "success":     True,
            "employee_id": employee_id,
            "full_name":   full_name,
            "token":       token,
            "role":        role,
            "shift_start": shift.get("start_ist"),
            "shift_end":   shift.get("end_ist"),
            "session_id":  session_id,
            "config":      config,
        }

        except requests.exceptions.ConnectionError:
            return {"success": False, "message": "Cannot connect to server"}
        except requests.exceptions.Timeout:
            return {"success": False, "message": "Server timed out — try again"}
        except Exception as e:
            LoggerService.log_error(f"AuthService.login error: {e}")
        return {"success": False, "message": "Unexpected error occurred"}

    @staticmethod
    def _create_db_session(
        employee_id: str,
        auth_token:  str,
        shift_start: str,
        shift_end:   str,
    ) -> int | None:
        """DB sessions table mein row insert karo — session_id return karo."""
        try:
            from client.security.crypto_engine import CryptoEngine
            encrypted_token = CryptoEngine.encrypt_token(auth_token)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            with Database.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO sessions
                    (employee_id, auth_token, device_id,
                    login_time, shift_start, shift_end, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE')
                    """,
                    (
                        employee_id,
                        encrypted_token,
                        SessionManager.get_device_id(),
                        now,
                        shift_start,
                        shift_end,
                    ),
                )
                return cursor.lastrowid
        except Exception as e:
            LoggerService.log_error(f"AuthService DB session error: {e}")
            return None

    @staticmethod
    def logout(session_id: int | None = None) -> bool:
        """Logout — DB session close karo aur server notify karo."""
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # DB update
            if session_id:
                with Database.get_connection() as conn:
                    conn.cursor().execute(
                        """
                        UPDATE sessions
                        SET logout_time = ?, status = 'COMPLETED'
                        WHERE id = ?
                        """,
                        (now, session_id),
                    )

                    # Server notify
                if SessionManager.auth_token:
                    try:
                        requests.post(
                            f"{API_BASE_URL}/auth/logout",
                            json={"device_id": SessionManager.get_device_id()},
                            headers={
                                "Authorization": f"Bearer {SessionManager.auth_token}"
                            },
                            timeout=5,
                        )
                    except Exception:
                        pass  # Logout locally ho gaya — server notify fail theek hai
                    return True
        except Exception as e:
            LoggerService.log_error(f"AuthService.logout error: {e}")
            return False