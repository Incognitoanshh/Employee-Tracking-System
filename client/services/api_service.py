import requests

from client.core.config import API_BASE_URL


class ApiService:

    @staticmethod
    def post(endpoint, data, auth_token=None):
        """
        BUG FIX: Auth headers support add kiya.
        Pehle koi bhi authenticated endpoint call nahi ho sakti thi is service se.
        """
        try:
            headers = {"Content-Type": "application/json"}
            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"

            response = requests.post(
                f"{API_BASE_URL}/{endpoint}",
                json=data,
                headers=headers,
                timeout=10
            )
            return response.json()

        except Exception as error:
            print(f"[API ERROR] {error}")
            return None

    @staticmethod
    def get(endpoint, auth_token=None, params=None):
        """GET requests ke liye - pehle yeh method tha hi nahi."""
        try:
            headers = {}
            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"

            response = requests.get(
                f"{API_BASE_URL}/{endpoint}",
                headers=headers,
                params=params,
                timeout=10
            )
            return response.json()

        except Exception as error:
            print(f"[API GET ERROR] {error}")
            return None
