import uuid
import socket
import base64
import json
import time


class SessionManager:
    role = "employee"
    employee_id = None
    auth_token = None
    device_id = None   # BUG FIX: Pehle "_generate_device_id = None" tha - typo tha
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
    def is_token_expired(cls):
        """JWT exp claim check karo locally - no server call needed"""
        if not cls.auth_token:
            return True
        try:
            parts = cls.auth_token.split('.')
            if len(parts) != 3:
                return True
            padding = 4 - len(parts[1]) % 4
            payload_bytes = base64.urlsafe_b64decode(parts[1] + '=' * padding)
            payload = json.loads(payload_bytes)
            exp = payload.get('exp')
            if exp is None:
                return False
            return time.time() > exp
        except Exception:
            return True

    @classmethod
    def create_session(cls, employee_id, auth_token, role="employee", shift_start=None, shift_end=None):
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
        # device_id intentionally keep karo (machine-specific rehta hai)
