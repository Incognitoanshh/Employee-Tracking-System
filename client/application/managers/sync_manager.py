import os
import requests
from client.core import http as _http

from client.application.managers.session_manager import SessionManager
from client.core.config import API_BASE_URL
from client.infrastructure.database.database import Database
from client.core.time_ist import end_of_ist_day, ist_day_str, now_ist
from datetime import timedelta


class SyncManager:
    @staticmethod
    def _auth_headers():
        if not SessionManager.auth_token:
            return None
        return {"Authorization": f"Bearer {SessionManager.auth_token}"}

    # THE RETRY RULE, as the owner put it: "1 baar try kro, nahi hoga to 1 hr
    # baad. 3 try krega, nahi hua to phir next day hoga."
    #
    # It used to retry every pending screenshot on every sync tick — once a
    # minute, all day — and write a log line each time. One picture the server
    # would not take became sixty audit-log rows an hour, uploaded from every
    # machine, about nothing that anybody could act on.
    UPLOAD_RETRY_GAP = timedelta(hours=1)
    UPLOAD_TRIES_PER_DAY = 3

    @staticmethod
    def get_pending_screenshots(due_only: bool = False):
        """This person's screenshots that have not reached the server.

        ONLY THIS PERSON'S. The query used to take every pending row on the
        machine and upload it with whoever was signed in now — so on a shared
        laptop, one employee's unsent pictures went up under the next
        person's account, and the server filed them as theirs.

        `due_only` leaves out the ones whose next try is still in the future.
        """
        employee_id = getattr(SessionManager, "employee_id", None)
        if not employee_id:
            return []
        connection = Database.connect()
        cursor = connection.cursor()
        if due_only:
            cursor.execute(
                """
                SELECT *
                FROM screenshots
                WHERE uploaded = 0 AND employee_id = ?
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY timestamp
                """,
                (employee_id, now_ist().strftime("%Y-%m-%d %H:%M:%S")),
            )
        else:
            cursor.execute(
                """
                SELECT *
                FROM screenshots
                WHERE uploaded = 0 AND employee_id = ?
                """,
                (employee_id,),
            )
        data = cursor.fetchall()
        connection.close()
        return data

    @staticmethod
    def hold_for_first_upload(screenshot_id):
        """Keep the retry loop off a screenshot whose first upload is running.

        A newly captured row has no next try yet, so it would be "due" — and
        the sync tick could send it a second time while the capture path is
        still sending it the first. Parking it an hour ahead is exactly where
        a failed first try would put it anyway; success clears it.
        """
        connection = Database.connect()
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE screenshots SET next_attempt_at = ? WHERE id = ?",
            ((now_ist() + SyncManager.UPLOAD_RETRY_GAP).strftime("%Y-%m-%d %H:%M:%S"),
             screenshot_id),
        )
        connection.commit()
        connection.close()

    @staticmethod
    def record_upload_failure(screenshot_id, reason: str):
        """Count a failed upload and decide when to try again.

        Up to three tries in an IST day, an hour apart. After the third the
        next one is tomorrow morning, and the count starts again then. The
        line written says which try this was and when the next will be — at
        most three lines a day per picture, each of them one that means
        something.

        Returns the time of the next try, as written.
        """
        from client.services.logger_service import LoggerService

        now = now_ist()
        today = ist_day_str(now)
        connection = Database.connect()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT attempts, attempt_day FROM screenshots WHERE id = ?",
            (screenshot_id,),
        )
        row = cursor.fetchone()
        tries = 0
        if row and row["attempt_day"] == today:
            tries = int(row["attempts"] or 0)
        tries += 1

        if tries >= SyncManager.UPLOAD_TRIES_PER_DAY:
            # The first moment of the next IST day.
            next_try = end_of_ist_day(now) + timedelta(seconds=1)
            when = "tomorrow"
        else:
            next_try = now + SyncManager.UPLOAD_RETRY_GAP
            when = f"at {next_try.strftime('%H:%M')}"

        cursor.execute(
            """
            UPDATE screenshots
               SET attempts = ?, attempt_day = ?, next_attempt_at = ?
             WHERE id = ?
            """,
            (tries, today, next_try.strftime("%Y-%m-%d %H:%M:%S"), screenshot_id),
        )
        connection.commit()
        connection.close()

        try:
            LoggerService.log(
                f"SCREENSHOT UPLOAD FAILED : try {tries} of "
                f"{SyncManager.UPLOAD_TRIES_PER_DAY} today — {reason} — "
                f"next try {when}"
            )
        except Exception:
            pass
        return next_try

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
                from client.services.logger_service import LoggerService
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

        # Only the ones whose next try has come round — see
        # record_upload_failure. Everything else waits its turn quietly.
        pending = SyncManager.get_pending_screenshots(due_only=True)

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
                    SyncManager.record_upload_failure(
                        screenshot_id,
                        f"HTTP {response.status_code} {response.text[:200]}")

            except Exception as error:
                # Never silent — but counted, so it is said at most three
                # times a day rather than once a minute.
                try:
                    SyncManager.record_upload_failure(screenshot["id"], str(error))
                except Exception:
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
    def push_activity_minutes(max_rows: int = 120):
        """Send the scored minutes the server has not been told about.

        A batch rather than a row at a time: a laptop that was off the
        network for an hour has sixty of these waiting, and sixty round
        trips on a hotel connection is how a sync falls behind and stays
        behind. They are marked sent only for the ids the server accepted.
        """
        headers = SyncManager._auth_headers()
        if headers is None:
            return

        try:
            connection = Database.connect()
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT id, minute, score, band, keystrokes, clicks, scrolls,
                       mouse_moves, window_changes, automation_suspected, reasons
                  FROM activity_minutes
                 WHERE employee_id = ? AND uploaded = 0
                 ORDER BY id LIMIT ?
                """,
                (SessionManager.employee_id, max_rows),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            connection.close()
        except Exception:
            return

        if not rows:
            return

        try:
            response = _http.post(
                f"{API_BASE_URL}/logs/activity-minutes",
                json={"minutes": [{k: v for k, v in row.items() if k != "id"}
                                  for row in rows]},
                headers=headers,
                timeout=15,
            )
            if response.status_code != 200:
                return
        except Exception:
            # Offline. They stay unsent and the next tick tries again.
            return

        try:
            connection = Database.connect()
            cursor = connection.cursor()
            cursor.executemany(
                "UPDATE activity_minutes SET uploaded = 1 WHERE id = ?",
                [(row["id"],) for row in rows])
            connection.commit()
            connection.close()
        except Exception:
            pass

    @staticmethod
    def retry_logs(max_retries: int = 20):
        headers = SyncManager._auth_headers()
        if headers is None:
            return

        from client.services.logger_service import LoggerService

        pending_logs = SyncManager.get_pending_logs()

        for log in pending_logs[:max_retries]:
            try:
                activity = str(log["activity"] or "")

                # A QUEUE FULL OF ITS OWN FAILURES, left by an older build.
                #
                # Before this loop stopped writing through LoggerService, every
                # failed send created a row saying so — and that row went into
                # the same queue. A machine that spent a while with a dead
                # session collected hundreds of them. They are diagnostics
                # about the queue, not anything an employee did, and uploading
                # them now puts "retry_logs failed — HTTP 401" into the
                # company's audit log a row at a time, which is exactly what it
                # looks like on the Audit Logs page: a flood with today's
                # timestamps and an old message inside.
                #
                # Dropped here rather than sent. Nothing is lost that anybody
                # would ever want to read, and a machine upgrading with a
                # backlog no longer empties it into the record.
                if activity.startswith("SyncManager: retry_logs failed"):
                    SyncManager.mark_log_uploaded(log["id"])
                    continue

                payload = {
                    "employee_id": log["employee_id"],
                    "activity": activity,
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
                    # TO A FILE, NOT THROUGH LoggerService.log — and this is the
                    # whole point.
                    #
                    # LoggerService.log writes a row into pending_logs, the very
                    # queue this loop is draining. So a failure to send a log
                    # created another log to send, which failed, which created
                    # another. Twenty failures a pass meant twenty new rows a
                    # pass: the queue grew instead of draining, and when the
                    # session finally worked the whole pile landed in the
                    # company's audit log. Seen in production — 861 rows of
                    # "retry_logs failed", climbing every second.
                    #
                    # A 401 here is the ordinary case, not an emergency: the
                    # session ended and the client has not signed in again yet.
                    LoggerService._fallback_critical_log(
                        f"SyncManager: retry_logs failed for log {log['id']} — "
                        f"HTTP {response.status_code} {response.text[:200]}"
                    )
                    # Nothing else in this pass will fare any better against
                    # the same dead session, so stop rather than burning
                    # nineteen more requests on it.
                    if response.status_code in (401, 403):
                        return

            except Exception as error:
                LoggerService._fallback_critical_log(
                    f"SyncManager: retry_logs error — {error}")
                return

