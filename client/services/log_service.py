import requests
from client.infrastructure.database.database import Database
from client.core.config import API_BASE_URL
from client.application.managers.session_manager import SessionManager


class LogService:

    @staticmethod
    def _auth_headers(token=None):
        t = token or SessionManager.auth_token
        if not t:
            return None
        return {"Authorization": f"Bearer {t}"}

    @staticmethod
    def _fetch_activity_logs(token=None):
        """Activity logs fetch karo (idle/active events)"""
        headers = LogService._auth_headers(token)
        if not headers:
            return None
        role = getattr(SessionManager, 'role', 'employee')
        if role == "admin":
            endpoint = f"{API_BASE_URL}/admin/logs"
            params = {"page": 1, "limit": 500}
        else:
            endpoint = f"{API_BASE_URL}/logs/all"
            params = {}
        try:
            response = requests.get(endpoint, headers=headers, params=params, timeout=10)
            data = response.json()
            if data.get("success") and data.get("data"):
                return data["data"]
        except Exception as e:
            print(f"[LOG SERVICE ERROR] {e}")
        return None

    @staticmethod
    def get_idle_logs(token=None):
        """Server se idle/active logs fetch karo"""
        server_data = LogService._fetch_activity_logs(token)
        if server_data is not None:
            logs = []
            for item in server_data:
                activity = item.get("activity", "")
                if any(k in activity.upper() for k in ["USER IDLE", "USER ACTIVE"]):
                    logs.append((
                        item.get("employee_id"),
                        activity,
                        item.get("created_at"),
                    ))
            return logs

        # Fallback local DB
        connection = Database.connect()
        cursor = connection.cursor()
        cursor.execute("SELECT employee_id, status, timestamp FROM idle_logs ORDER BY id DESC")
        logs = cursor.fetchall()
        connection.close()
        return logs

    @staticmethod
    def get_screenshot_logs(token=None):
        """Screenshots/all API se real screenshot records fetch karo with proper IDs"""
        headers = LogService._auth_headers(token)
        if not headers:
            return []
        try:
            response = requests.get(
                f"{API_BASE_URL}/screenshots/all",
                headers=headers,
                timeout=10
            )
            data = response.json()
            if data.get("success") and data.get("data"):
                logs = []
                for item in data["data"]:
                    logs.append((
                        item.get("id"),           # real DB id
                        item.get("employee_id"),
                        item.get("file_name", ""),
                        item.get("created_at"),
                    ))
                return logs
        except Exception as e:
            print(f"[SCREENSHOT LOG ERROR] {e}")

        # Fallback local DB
        connection = Database.connect()
        cursor = connection.cursor()
        cursor.execute("SELECT id, employee_id, file_path, timestamp FROM screenshots ORDER BY timestamp DESC")
        logs = cursor.fetchall()
        connection.close()
        return logs
