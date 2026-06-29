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
