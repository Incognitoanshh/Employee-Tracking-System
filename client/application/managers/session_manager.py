class SessionManager:

    employee_id = None

    auth_token = None

    device_id = None

    shift_start = None

    shift_end = None

    is_authenticated = False

    @classmethod
    def create_session(
        cls,
        employee_id,
        auth_token
    ):

        cls.employee_id = employee_id

        cls.auth_token = auth_token

        cls.is_authenticated = True

    @classmethod
    def clear_session(cls):

        cls.employee_id = None

        cls.auth_token = None

        cls.is_authenticated = False