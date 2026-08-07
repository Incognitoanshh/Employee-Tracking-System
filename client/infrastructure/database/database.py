import sqlite3
import os
import contextlib
from datetime import datetime

from client.core.config import STORAGE_DIR

class Database:
    DB_PATH = os.path.join(STORAGE_DIR, "ets.db")

    @classmethod
    def connect(cls):
        os.makedirs(STORAGE_DIR, exist_ok=True)
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

            # How much of each IST day was spent idle, in seconds.
            #
            # idle_logs above records the MOMENT someone went idle or active.
            # Turning those into a daily total means pairing each IDLE with
            # the ACTIVE that follows it — and a crash, a dropped connection
            # or a logout leaves a pair open forever, so the total comes out
            # wrong by an unknown amount. A wrong idle figure in a payroll
            # report is worse than no figure at all, which is why reports
            # shipped without one.
            #
            # This is accumulated instead: the tracker already asks the OS how
            # long the machine has been idle, every two seconds, so the time
            # is simply added up as it passes. A crash costs at most one
            # tick's worth, and there is no pair left open to go wrong.
            #
            # `uploaded` marks rows the server has already been told about.
            # The total for a day keeps growing, so a day is re-sent whenever
            # it changes rather than being sent once.
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS idle_daily (
                employee_id TEXT NOT NULL,
                day         TEXT NOT NULL,
                idle_seconds INTEGER NOT NULL DEFAULT 0,
                uploaded    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (employee_id, day)
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

            # Chat messages waiting to reach the server.
            #
            # Same idea as pending_logs: the employee types, it is stored here
            # first, and the network is somebody else's problem. What is
            # different is client_msg_id — it is generated once, when the
            # message is composed, and sent again on every retry. The server
            # uses it to recognise a resend, which is what makes retrying safe
            # after the case that actually happens on a bad link: the message
            # arrived and only the reply was lost.
            #
            # A row stays here after delivery, holding the seq the server gave
            # it, so the panel can show a sent tick without another round trip.
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_outbox (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                client_msg_id TEXT UNIQUE,
                channel_id    INTEGER,
                body          TEXT,
                created_at    TEXT,
                attempts      INTEGER DEFAULT 0,
                last_error    TEXT,
                delivered_seq INTEGER
            )
            """)
            cursor.execute("""
            CREATE INDEX IF NOT EXISTS chat_outbox_pending_idx
                ON chat_outbox (id) WHERE delivered_seq IS NULL
            """)

            # Phase 2 additions. Existing installations already have the table,
            # so these are added rather than declared above — an employee who
            # updates mid-week must not lose whatever is still queued.
            cursor.execute("PRAGMA table_info(chat_outbox)")
            outbox_cols = {row[1] for row in cursor.fetchall()}
            for column, ddl in (
                ("reply_to",       "reply_to INTEGER"),
                ("mentions",       "mentions TEXT"),          # JSON array of employee_id
                ("attachment_ids", "attachment_ids TEXT"),    # JSON array of ids
            ):
                if column not in outbox_cols:
                    cursor.execute(f"ALTER TABLE chat_outbox ADD COLUMN {ddl}")

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
