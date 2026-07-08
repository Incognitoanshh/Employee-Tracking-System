import sqlite3
import os
import contextlib
from datetime import datetime

class Database:
    DB_PATH = "storage/ets.db"

    @classmethod
    def connect(cls):
        os.makedirs("storage", exist_ok=True)
        connection = sqlite3.connect(cls.DB_PATH)
        connection.row_factory = sqlite3.Row
        return connection

    @classmethod
    @contextlib.contextmanager
    def get_connection(cls):
        conn = cls.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @classmethod
    def initialize(cls):
        connection = cls.connect()
        try:
            cursor = connection.cursor()

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS screenshots (
                id TEXT PRIMARY KEY,
                employee_id TEXT,
                file_path TEXT,
                timestamp TEXT,
                uploaded INTEGER DEFAULT 0
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS idle_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT,
                status TEXT,
                timestamp TEXT
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT,
                login_time TEXT,
                logout_time TEXT,
                total_hours TEXT
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT,
                activity TEXT,
                uploaded INTEGER DEFAULT 0,
                timestamp TEXT
            )
            """)

            # Migration: existing DBs (pre-timestamp-column) — add column if missing
            cursor.execute("PRAGMA table_info(pending_logs)")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if "timestamp" not in existing_cols:
                cursor.execute("ALTER TABLE pending_logs ADD COLUMN timestamp TEXT")

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT,
                auth_token BLOB,
                login_time TEXT,
                logout_time TEXT,
                status TEXT
            )
            """)
            connection.commit()
        finally:
            connection.close()

    @classmethod
    def cleanup_stale_sessions_and_shifts(cls):
        """
        Pichhli run agar crash/force-kill hui thi (proper logout call kabhi
        nahi hua), to yaha purani 'ACTIVE' shifts/sessions ko 'CLOSED' mark
        kar dete hain — taaki wo hamesha ke liye 'ACTIVE' na dikhti rahe.

        IMPORTANT: Ye method main.py me sirf tab call hota hai jab
        AutoLoginManager.try_auto_login() FAIL ho jaye. Agar auto-login
        SUCCEED hota hai, to SessionLogManager.start_session() already
        purani row ko sahi tarike se close karke nayi ACTIVE row banata
        hai — is method ko us case me chalana nayi-abhi-bani session ko
        bhi galti se 'CLOSED' kar dega.
        """
        connection = cls.connect()
        try:
            cursor = connection.cursor()

            cursor.execute("""
                UPDATE shifts
                SET logout_time = login_time, total_hours = '00:00:00'
                WHERE logout_time IS NULL OR logout_time = '' OR logout_time = 'ACTIVE'
                """)

            cursor.execute("""
            UPDATE sessions
            SET logout_time = login_time, status = 'CLOSED'
            WHERE logout_time IS NULL OR status = 'ACTIVE'
            """)

            connection.commit()
        finally:
            connection.close()

    @classmethod
    def enforce_single_active_shift(cls, employee_id):
        """
        Ye function ensure karega ki jab bhi koi naya session ya shift start ho,
        toh local DB me purani koi bhi dangling ya open ACTIVE shift automatic close ho jaye.
        """
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with cls.get_connection() as conn:
            cursor = conn.cursor()

            # 1. Shifts table me open entries ko close karo
            cursor.execute("""
            UPDATE shifts
            SET logout_time = ?, total_hours = 'AUTO_CLOSED'
            WHERE employee_id = ? AND (logout_time IS NULL OR logout_time = '' OR logout_time = 'ACTIVE')
            """, (current_time, employee_id))

            # 2. Sessions table me open active entries ko close karo
            cursor.execute("""
            UPDATE sessions
            SET logout_time = ?, status = 'CLOSED'
            WHERE employee_id = ? AND (logout_time IS NULL OR status = 'ACTIVE')
            """, (current_time, employee_id))

    @classmethod
    def close_current_shift(cls, employee_id):
        """
        User jab manual logout trigger kare, toh ye sirf sabse latest running session ko smoothly close karega.
        """
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with cls.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
            UPDATE shifts
            SET logout_time = ?
            WHERE id = (
                SELECT id FROM shifts
                WHERE employee_id = ? AND (logout_time IS NULL OR logout_time = '' OR logout_time = 'ACTIVE')
                ORDER BY id DESC LIMIT 1
            )
            """, (current_time, employee_id))