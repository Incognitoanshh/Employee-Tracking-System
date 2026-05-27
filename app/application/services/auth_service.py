import time


class AuthService:

    @staticmethod
    def login(username, password):

        """
        Temporary mock login
        Replace with API later
        """

        time.sleep(1)

        if (
            username == "admin"
            and
            password == "admin"
        ):

            return {
                "success": True,
                "employee_id": "EMP001",
                "token": "mock_jwt_token"
            }

        return {
            "success": False,
            "message": "Invalid credentials"
        }