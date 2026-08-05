import requests
from client.core.config import API_BASE_URL
from client.application.managers.session_manager import SessionManager


class AuthService:

    @staticmethod
    def login(username, password):

        try:
            response = requests.post(
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
            response = requests.post(
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
