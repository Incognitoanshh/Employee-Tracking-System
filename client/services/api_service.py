import requests

from client.core.config import API_BASE_URL


class ApiService:

    @staticmethod
    def post(
        endpoint,
        data
    ):

        try:

            response = requests.post(

                f"{API_BASE_URL}/{endpoint}",

                json=data,

                timeout=10

            )

            return response.json()

        except Exception as error:

            print(
                f"[API ERROR] {error}"
            )

            return None