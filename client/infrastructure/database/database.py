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
                uploaded INTEGER DEFAULT 0
            )
            """)

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