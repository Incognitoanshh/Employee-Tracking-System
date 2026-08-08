import os
import requests
from client.core import http as _http

from client.application.managers.session_manager import SessionManager
from client.core.config import API_BASE_URL
from client.infrastructure.database.database import Database


class SyncManager:
    @staticmethod
    def _auth_headers():
        if not SessionManager.auth_token:
            return None
        return {"Authorization": f"Bearer {SessionManager.auth_token}"}

    @staticmethod
    def get_pending_screenshots():
        connection = Database.connect()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT *
            FROM screenshots
            WHERE uploaded = 0
            """
        )
        data = cursor.fetchall()
        connection.close()
        return data

    @staticmethod
    def mark_uploaded(screenshot_id):
        """
        Upload safal — DB me flag set karo AUR local .enc file delete karo.

        BUG FIX: pehle sirf `uploaded = 1` set hota tha, file disk pe hamesha
        ke liye padi rehti thi. `cleanup_old_orphans()` sirf `uploaded = 0`
        (failed) files hataata hai — yaani har SAFAL upload ki file kabhi
        delete hi nahi hoti thi.

        Production pe measure kiya: average file 3.5 MB, 12 captures/din
        => ~10 GB per employee per saal, employee ke apne laptop pe. Kai
        mahine baad employee ki disk bhar jaati aur app screenshot lena
        band kar deti (capture failed — no space).

        Upload safal hone ka matlab hai server ke paas copy hai (wahi
        source of truth hai — admin panel aur preview dono server se hi
        fetch karte hain), is liye local copy rakhne ka koi fayda nahi.
        """
        connection = Database.connect()
        cursor = connection.cursor()

        file_path = None
        try:
            cursor.execute(
                "SELECT file_path FROM screenshots WHERE id = ?", (screenshot_id,)
            )
            row = cursor.fetchone()
            if row:
                file_path = row[0]
        except Exception:
            pass

        cursor.execute(
            """
            UPDATE screenshots
            SET uploaded = 1
            WHERE id = ?
            """,
            (screenshot_id,),
        )
        connection.commit()
        connection.close()

        if file_path:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as error:
                # Delete fail hona upload ko fail nahi karta — file agli
                # cleanup pass me hat jayegi.
                LoggerService.log_verbose(
                    f"SyncManager: uploaded file delete nahi hui {file_path} — {error}"
                )

    @staticmethod
    def get_pending_logs():
        connection = Database.connect()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT *
            FROM pending_logs
            WHERE uploaded = 0
            """
        )
        data = cursor.fetchall()
        connection.close()
        return data

    @staticmethod
    def mark_log_uploaded(log_id):
        connection = Database.connect()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE pending_logs
            SET uploaded = 1
            WHERE id = ?
            """,
            (log_id,),
        )
        connection.commit()
        connection.close()

    @staticmethod
    def retry_uploads(max_retries: int = 5):
        headers = SyncManager._auth_headers()
        if headers is None:
            return

        # Lazy import to avoid circular import at module load time.
        from client.services.logger_service import LoggerService

        pending = SyncManager.get_pending_screenshots()

        for screenshot in pending[:max_retries]:
            try:
                file_path = screenshot["file_path"]
                screenshot_id = screenshot["id"]

                if not os.path.exists(file_path):
                    # The file is gone and was never delivered. The row is
                    # closed so this does not retry for ever — but it is SAID
                    # OUT LOUD, which it was not before.
                    #
                    # Silently writing uploaded=1 made the record claim a
                    # success the server never received. Antivirus quarantine,
                    # a disk cleanup, or a half-written file all land here, and
                    # the only symptom was fewer screenshots than expected with
                    # nothing anywhere to explain it. log(), not
                    # log_verbose(): this has to reach the audit log without
                    # anybody having switched verbose logging on first.
                    LoggerService.log(
                        f"SCREENSHOT LOST : {screenshot_id} was never uploaded "
                        f"and its file is gone from {file_path} — "
                        f"nothing to send, giving up on it"
                    )
                    SyncManager.mark_uploaded(screenshot_id)
                    continue

                # .enc file ke RAW encrypted bytes hi server ko upload karo.
                # Decrypt sirf app se open karte waqt hoga (preview window
                # me), server pe plain PNG kabhi nahi jaani chahiye.
                with open(file_path, "rb") as file:
                    encrypted_bytes = file.read()

                response = _http.post(
                    f"{API_BASE_URL}/screenshots/upload",
                    files={"screenshot": (f"{screenshot_id}.enc", encrypted_bytes, "application/octet-stream")},
                    headers=headers,
                    timeout=10,
                )

                if response.status_code == 200:
                    SyncManager.mark_uploaded(screenshot_id)
                else:
                    LoggerService.log(
                        f"SyncManager: retry upload failed for {screenshot_id} — "
                        f"HTTP {response.status_code} {response.text[:200]}"
                    )

            except Exception as error:
                # BUG FIX: pehle silently pass ho jaata tha, debugging
                # impossible thi. Ab file log mein likha jaata hai.
                LoggerService.log(f"SyncManager: retry_uploads error — {error}")

    @staticmethod
    def cleanup_old_orphans(days=7):
        """X days se purane unuploaded local records delete karo"""
        from client.services.logger_service import LoggerService
        try:
            from datetime import datetime, timedelta
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            connection = Database.connect()
            cursor = connection.cursor()
            # Purane unuploaded screenshots
            cursor.execute(
                "SELECT id, file_path FROM screenshots WHERE uploaded = 0 AND timestamp < ?",
                (cutoff,)
            )
            old_screenshots = cursor.fetchall()
            for row in old_screenshots:
                try:
                    if row["file_path"] and os.path.exists(row["file_path"]):
                        os.remove(row["file_path"])
                except Exception as e:
                    LoggerService.log(f"SyncManager: failed to remove orphan file {row['file_path']} — {e}")
            cursor.execute(
                "DELETE FROM screenshots WHERE uploaded = 0 AND timestamp < ?",
                (cutoff,)
            )
            # Already-uploaded purane logs delete karo — safe hai, server ke
            # paas already ye data hai, local copy sirf backlog badha rahi thi.
            cursor.execute(
                "DELETE FROM pending_logs WHERE uploaded = 1 AND timestamp < ?",
                (cutoff,)
            )

            # Unsent (uploaded = 0) logs kabhi silently delete nahi karte —
            # ye data-loss hota (server ne kabhi receive nahi kiya). Agar
            # backlog bahut bada ho gaya hai to sirf warn karo taaki koi
            # dekh sake ki server/network issue hai, data khoyega nahi.
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM pending_logs WHERE uploaded = 0 AND timestamp < ?",
                (cutoff,)
            )
            stale_pending = cursor.fetchone()["cnt"]

            connection.commit()
            connection.close()

            # LoggerService.log() apna alag DB connection kholta hai — isko
            # transaction commit/close hone ke BAAD hi call karo, warna
            # SQLite "database is locked" error deta hai (ek hi DB file pe
            # do open connections, ek abhi tak uncommitted write hold kiye
            # hue).
            if stale_pending > 0:
                LoggerService.log(
                    f"SyncManager: {stale_pending} unsent logs older than "
                    f"{days} days — server/network issue likely, not deleting "
                    f"(would cause data loss)."
                )
        except Exception as e:
            LoggerService.log(f"SyncManager: cleanup_old_orphans error — {e}")

    @staticmethod
    def push_idle_totals(max_days: int = 7):
        """
        Send any day whose idle total has moved since it was last sent.

        A day's total keeps growing while somebody is signed in, so a day is
        re-sent rather than sent once — the `uploaded` flag is cleared every
        time the tracker adds to it. Only recent days are attempted: a total
        from three weeks ago that has never gone through is not going to
        start working now, and retrying it forever would hold up today's.
        """
        headers = SyncManager._auth_headers()
        if headers is None:
            return

        try:
            connection = Database.connect()
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT day, idle_seconds FROM idle_daily
                 WHERE employee_id = ? AND uploaded = 0
                 ORDER BY day DESC LIMIT ?
                """,
                (SessionManager.employee_id, max_days),
            )
            pending = [(row["day"], row["idle_seconds"]) for row in cursor.fetchall()]
            connection.close()
        except Exception:
            return

        for day, seconds in pending:
            try:
                response = _http.post(
                    f"{API_BASE_URL}/logs/idle-daily",
                    json={"day": day, "idle_seconds": int(seconds)},
                    headers=headers,
                    timeout=10,
                )
                if response.status_code != 200:
                    continue
                connection = Database.connect()
                cursor = connection.cursor()
                # Guarded on the value that was actually sent: the tracker may
                # have added more while the request was in flight, and marking
                # the row clean would lose that.
                cursor.execute(
                    """
                    UPDATE idle_daily SET uploaded = 1
                     WHERE employee_id = ? AND day = ? AND idle_seconds = ?
                    """,
                    (SessionManager.employee_id, day, seconds),
                )
                connection.commit()
                connection.close()
            except Exception:
                # Offline or the server is down — the row stays unuploaded
                # and the next sync tick tries again.
                break

    @staticmethod
    def retry_logs(max_retries: int = 20):
        headers = SyncManager._auth_headers()
        if headers is None:
            return

        from client.services.logger_service import LoggerService

        pending_logs = SyncManager.get_pending_logs()

        for log in pending_logs[:max_retries]:
            try:
                payload = {
                    "employee_id": log["employee_id"],
                    "activity": log["activity"],
                }

                response = _http.post(
                    f"{API_BASE_URL}/logs/create",
                    json=payload,
                    headers=headers,
                    timeout=5,
                )

                if 200 <= response.status_code < 300:
                    SyncManager.mark_log_uploaded(log["id"])
                else:
                    LoggerService.log(
                        f"SyncManager: retry_logs failed for log {log['id']} — "
                        f"HTTP {response.status_code} {response.text[:200]}"
                    )

            except Exception as error:
                LoggerService.log(f"SyncManager: retry_logs error — {error}")

