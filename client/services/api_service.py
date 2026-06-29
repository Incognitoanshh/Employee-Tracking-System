import requests

from client.core.config import API_BASE_URL


class ApiService:

    @staticmethod
    def post(endpoint, data, auth_token=None):
        """POST JSON requests with centralized error logging."""
        try:
            headers = {"Content-Type": "application/json"}
            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"

            response = requests.post(
                f"{API_BASE_URL}/{endpoint}",
                json=data,
                headers=headers,
                timeout=10,
            )
            return response.json()

        except Exception as error:
            # stdout/stderr can be invisible in PyInstaller windowed builds.
            try:
                from client.services.logger_service import LoggerService
                LoggerService.log(f"ApiService.post error endpoint={endpoint}: {error}")
            except Exception:
                pass
            return None


    @staticmethod
    def get(endpoint, auth_token=None, params=None):
        """GET requests with centralized error logging."""
        try:
            headers = {}
            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"

            response = requests.get(
                f"{API_BASE_URL}/{endpoint}",
                headers=headers,
                params=params,
                timeout=10,
            )
            return response.json()

        except Exception as error:
            try:
                from client.services.logger_service import LoggerService
                LoggerService.log(f"ApiService.get error endpoint={endpoint}: {error}")
            except Exception:
                pass
            return None

