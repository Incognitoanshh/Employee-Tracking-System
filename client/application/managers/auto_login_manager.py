import requests

from client.core.config import API_BASE_URL
from client.application.managers.session_manager import SessionManager
from client.application.managers.session_log_manager import SessionLogManager
from client.application.managers.shift_manager import ShiftManager
from client.services.logger_service import LoggerService


class AutoLoginManager:
    """
    App start hote hi pichli saved session (SessionLogManager ke 'sessions'
    table me encrypted auth_token) ko restore karne ki koshish karta hai,
    taaki restart/reboot ke baad employee ko manually login na karna pade.

    Agar saved session nahi hai, token expired hai, ya server unreachable
    hai — silently fail ho jata hai aur normal LoginWindow dikhta hai.
    Koi bhi network/DB error yahan se crash nahi karega.
    """

    @staticmethod
    def try_auto_login():
        """
        Returns dict {"employee_id": ..., "role": ...} on success (aur
        SessionManager already populate ho chuka hoga), ya None on failure.
        """
        try:
            saved = SessionLogManager.get_last_session()
            if not saved or not saved.get("auth_token"):
                return None

            old_token = saved["auth_token"]
            payload = SessionManager.decode_token_payload(old_token)
            if payload is None:
                LoggerService.log("AutoLoginManager: saved token unreadable, skipping auto-login")
                return None

            import time
            exp = payload.get("exp")
            if exp is not None and time.time() > exp:
                LoggerService.log("AutoLoginManager: saved token already expired, skipping auto-login")
                return None

            # Token abhi expire nahi hua — server se fresh 24h token le lo
            # taaki session server-side bhi valid rahe (active_sessions table).
            try:
                response = requests.post(
                    f"{API_BASE_URL}/auth/refresh",
                    headers={"Authorization": f"Bearer {old_token}"},
                    timeout=10,
                )
            except Exception as error:
                LoggerService.log(f"AutoLoginManager: refresh request failed — {error}")
                return None

            if response.status_code != 200:
                LoggerService.log(
                    f"AutoLoginManager: refresh rejected by server — "
                    f"HTTP {response.status_code}, falling back to login screen"
                )
                return None

            data = response.json()
            new_token = data.get("token")
            if not new_token:
                return None

            new_payload = SessionManager.decode_token_payload(new_token) or {}
            employee_id = new_payload.get("employee_id") or payload.get("employee_id")
            role = new_payload.get("role") or payload.get("role", "employee")

            if not employee_id:
                return None

            # Restore se pehle is employee ki koi bhi purani dangling
            # ACTIVE shift/session (pichhli run crash hui ho to) safety-net
            # ke taur pe close kar do — taaki naya session start hote hi
            # do "ACTIVE" rows kabhi ek saath na rahen.
            try:
                from client.infrastructure.database.database import Database
                Database.enforce_single_active_shift(employee_id)
            except Exception:
                pass

            SessionManager.create_session(
                employee_id=employee_id,
                auth_token=new_token,
                role=role,
                shift_start=None,
                shift_end=None,
            )

            # Shift timing config server se fetch karo (login ke waqt jo
            # milta hai, wahi ab config/sync se milega).
            try:
                from client.application.managers.config_sync_manager import ConfigSyncManager
                temp_sync = ConfigSyncManager(
                    employee_id=employee_id,
                    device_id=SessionManager.get_device_id(),
                    auth_token=new_token,
                )
                config = temp_sync.sync_now()
                shift = (config or {}).get("shift") or {}
                if shift.get("start_ist") and shift.get("end_ist"):
                    SessionManager.shift_start = shift["start_ist"]
                    SessionManager.shift_end = shift["end_ist"]
            except Exception as error:
                LoggerService.log(f"AutoLoginManager: shift/config fetch failed — {error}")
                # Non-fatal — SchedulerService will fall back to an 8hr window.

            ShiftManager.start_shift()
            SessionLogManager.start_session()

            LoggerService.log(f"AutoLoginManager: auto-login succeeded for {employee_id}")
            return {"employee_id": employee_id, "role": role}

        except Exception as error:
            LoggerService.log(f"AutoLoginManager: unexpected error — {error}")
            return None
