import uuid
import socket


class SessionManager:

    role = "employee"

    employee_id = None
    auth_token = None
    # FIX 4: device_id ab machine-based unique ID generate hoga
    device_id = _generate_device_id = None
    shift_start = None
    shift_end = None
    is_authenticated = False

    @classmethod
    def get_device_id(cls):
        """Machine-specific unique ID generate karo (MAC address based)"""
        if cls.device_id is None:
            try:
                hostname = socket.gethostname()
                mac = uuid.getnode()
                cls.device_id = f"{hostname}-{mac:012x}"
            except Exception:
                cls.device_id = str(uuid.uuid4())
        return cls.device_id

    @classmethod
    def create_session(
        cls,
        employee_id,
        auth_token,
        role="employee",
        shift_start=None,
        shift_end=None,
    ):
        print("CREATE_SESSION ROLE =", role)
        cls.employee_id = employee_id
        cls.auth_token = auth_token
        cls.role = role
        cls.shift_start = shift_start
        cls.shift_end = shift_end
        cls.is_authenticated = True
        cls.get_device_id()

    @classmethod
    def clear_session(cls):
        cls.employee_id = None
        cls.auth_token = None
        cls.role = "employee"
        cls.shift_start = None
        cls.shift_end = None
        cls.is_authenticated = False
