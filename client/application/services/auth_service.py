import requests


class AuthService:

    @staticmethod
    def login(username, password):

        try:

            print("LOGIN START")

            response = requests.post(
                "http://127.0.0.1:8000/api/auth/login",
                json={
                    "username": username,
                    "password": password
                },
                timeout=5
            )

            print("STATUS:", response.status_code)
            print("BODY:", response.text)

            return response.json()

        except Exception as error:

            print("ERROR:", error)

            return {
                "success": False,
                "message": str(error)
            }