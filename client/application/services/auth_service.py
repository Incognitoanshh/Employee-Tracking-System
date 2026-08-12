
from client.core import http as _http
from client.core.config import API_BASE_URL
from client.application.managers.session_manager import SessionManager
from client.services.logger_service import LoggerService


class AuthService:

    @staticmethod
    def login(username, password):

        try:
            response = _http.post(
                f"{API_BASE_URL}/auth/login",
                json={
                    "username": username,
                    "password": password,
                    "device_id": SessionManager.get_device_id()
                },
                timeout=5
            )

            return response.json()

        except Exception as error:
            return {
                "success": False,
                "message": str(error)
            }

    @staticmethod
    def sign_out_on_server() -> bool:
        """Tell the server this session is over. Best effort, never raises.

        THE BUG THIS FIXES: nothing called it. /auth/logout was reached only
        from the tray's Exit, so the Logout button in either panel cleared the
        session locally and left it open on the server. That was survivable
        while the same device could reclaim its own session — and stopped
        being survivable the moment one login at a time became strict, because
        signing out and straight back in was then refused for two minutes.

        Failure is not fatal and must not block the sign-out: if the network
        is down, the session goes stale on its own within a couple of minutes,
        which is the same safety valve a crash relies on.
        """
        token = getattr(SessionManager, "auth_token", None)
        if not token:
            return False
        try:
            response = _http.post(
                f"{API_BASE_URL}/auth/logout",
                headers={"Authorization": f"Bearer {token}"},
                timeout=8,
            )
            return response.status_code == 200
        except Exception as error:
            try:
                LoggerService.log(
                    f"LOGOUT: server not told — {error}. The session will "
                    f"expire on its own."
                )
            except Exception:
                pass
            return False

    @staticmethod
    def change_password(current_password: str, new_password: str) -> dict:
        """
        Change the signed-in account's own password.

        On success the server returns a replacement token, because setting a
        new password ends every session for the account — including this one.
        Storing it here is what keeps the app the employee is looking at
        signed in; skip it and a successful change looks like being thrown
        out to the login screen.
        """
        try:
            response = _http.post(
                f"{API_BASE_URL}/auth/password",
                json={
                    "current_password": current_password,
                    "new_password":     new_password,
                },
                headers={
                    "Authorization": f"Bearer {SessionManager.auth_token}",
                    "Content-Type":  "application/json",
                },
                timeout=10,
            )
            data = response.json()

            if data.get("success") and data.get("token"):
                SessionManager.auth_token = data["token"]
                # The copy kept for auto-login was signed against the old
                # password and is now rejected, so refresh it too.
                try:
                    from client.application.managers.session_log_manager import (
                        SessionLogManager,
                    )
                    SessionLogManager.update_active_token()
                except Exception:
                    # Auto-login degrades to a normal sign-in; not worth
                    # failing a password change that already succeeded.
                    pass

            return data

        except Exception as error:
            return {
                "success": False,
                "message": str(error)
            }
