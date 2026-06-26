import sqlite3
import os
import contextlib


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
        with cls.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT UNIQUE NOT NULL,
                full_name TEXT,
                email TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT NOT NULL,
                auth_token BLOB,
                device_id TEXT,
                login_time TEXT,
                logout_time TEXT,
                shift_start TEXT,
                shift_end TEXT,
                status TEXT DEFAULT 'ACTIVE'
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS screenshots (
                id TEXT PRIMARY KEY,
                employee_id TEXT NOT NULL,
                session_id INTEGER,
                file_path TEXT,
                timestamp TEXT,
                upload_status TEXT DEFAULT 'PENDING',
                upload_attempts INTEGER DEFAULT 0,
                last_upload_attempt TEXT
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS idle_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT NOT NULL,
                session_id INTEGER,
                idle_start TEXT,
                idle_end TEXT,
                duration_seconds INTEGER,
                upload_status TEXT DEFAULT 'PENDING',
                upload_attempts INTEGER DEFAULT 0
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS app_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                level TEXT,
                source TEXT,
                message TEXT
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT NOT NULL,
                login_time TEXT,
                logout_time TEXT,
                total_seconds INTEGER DEFAULT 0,
                status TEXT DEFAULT 'ACTIVE'
            )
            """)

            cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_screenshots_status
            ON screenshots(employee_id, upload_status)
            """)

            cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_idle_logs_status
            ON idle_logs(employee_id, upload_status)
            """)

            cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shifts_employee
            ON shifts(employee_id, status)
            """)
