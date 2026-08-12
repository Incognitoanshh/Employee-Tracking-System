"""
Chat, from the client's side: polling, the outbox, and the unread counts.

The panel does no networking of its own. It asks this for what to draw and
hands it what the employee typed; everything between those two things happens
on a background thread so the interface cannot freeze on a slow link — which,
on the connection this runs over, is most of the time.

TWO THINGS ARE WORTH UNDERSTANDING BEFORE CHANGING ANYTHING HERE.

THE CURSOR. There is no socket. This holds the highest `seq` it has seen and
asks the server "what is there after this?". One request covers every channel
the employee can see, however many teams they are in. The cursor is stored, so
closing the panel and opening it an hour later resumes rather than reloads.

THE OUTBOX. A message the employee types is written to the local database
first and only then sent. If the send fails it stays there and is retried; if
it succeeds the row keeps the seq the server assigned. The case this is really
built for is not "no network" — it is the message that ARRIVED and whose reply
was lost, where a naive retry silently posts the same thing twice. Every
attempt carries the same client_msg_id, and the server recognises it.

Poll intervals move with what the employee is doing, because a chat window
they are looking at should feel immediate and one they are not should not cost
the server anything: 3s watching, 15s app open, 60s minimised.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Callable, Optional

import requests
from client.core import http as _http
from PySide6.QtCore import QObject, Signal

from client.core.config import API_BASE_URL
from client.infrastructure.database.database import Database
from client.application.managers.session_manager import SessionManager
from client.services.logger_service import LoggerService
from client.services.settings_service import SettingsService
from client.security.crypto_engine import CryptoEngine


# How often to ask, by what the employee is doing.
INTERVAL_ACTIVE_CHAT = 3
INTERVAL_APP_OPEN = 15
INTERVAL_BACKGROUND = 60

# Backoff when the server cannot be reached. Capped: an employee who opens
# their laptop after a long outage should not wait ten minutes for their first
# poll to come round again.
MAX_BACKOFF = 120

# Administrative alerts are about hours and days, not seconds, and the query
# behind them is the expensive one. Five minutes is as timely as this needs
# to be.
ALERT_POLL_SECONDS = 300

CURSOR_KEY = "chat_cursor"
MAX_BODY = 2000
# Matches the server's limit. Checked here too so a large file is
# refused before it is encrypted and pushed over a slow link only to
# be rejected at the far end.
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024


class ChatManager(QObject):
    """Owns the chat connection. One per signed-in session."""

    # New messages, oldest first. The panel decides what to do with them —
    # append to the open channel, bump a badge, or both.
    messages = Signal(list)
    # Things that must be noticed rather than counted: announcements now,
    # mentions in Phase 2.
    notifications = Signal(list)
    # Messages somebody has withdrawn, as a list of seqs. Separate from
    # `messages` because these arrive outside the cursor — see the note on the
    # server's getUpdates — and because they are removals, not arrivals.
    deletions = Signal(list)
    # The outbox changed — something was queued, sent, or failed.
    outbox_changed = Signal()
    # Connected / disconnected, so the panel can say so instead of looking
    # broken while the network is down.
    online_changed = Signal(bool)

    #: The server no longer accepts this session — an administrator forced a
    #: logout, or the account was suspended or taken over on another machine.
    #: Carries the words to show the person. Nothing else in the client can
    #: notice this as early as the chat poll, which runs every few seconds.
    session_ended = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._active_channel: Optional[int] = None
        self._app_focused = True
        self._online = True
        self._failures = 0
        # Said once. Without it a dead session writes a log line per poll,
        # and every one of those is itself uploaded — the flood that filled
        # the production audit log.
        self._session_over = False
        # Administrative alerts are worked out fresh by the server on demand;
        # they are not rows anybody inserts, so they never travelled on this
        # poll. See _poll_alerts.
        self._alerts_checked_at = 0.0
        self._alerts_seen: set[str] = set()
        self._lock = threading.Lock()

    # ── lifecycle ───────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ChatThread", daemon=True)
        self._thread.start()
        LoggerService.log_verbose("ChatManager: started")

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
        LoggerService.log_verbose("ChatManager: stopped")

    def set_active_channel(self, channel_id: Optional[int]) -> None:
        """Which channel the employee is looking at, or None."""
        with self._lock:
            changed = channel_id != self._active_channel
            self._active_channel = channel_id
        if changed:
            # Poll straight away rather than waiting out the current sleep —
            # opening a channel and staring at a stale screen for fifteen
            # seconds is the difference between "chat" and "email".
            self._wake.set()

    def set_app_focused(self, focused: bool) -> None:
        with self._lock:
            changed = focused != self._app_focused
            self._app_focused = focused
        if changed and focused:
            self._wake.set()

    def poll_now(self) -> None:
        self._wake.set()

    # ── sending ─────────────────────────────────────────────────────────

    def send(self, channel_id: int, body: str, *, reply_to: int | None = None,
             mentions: list | None = None, attachment_ids: list | None = None) -> dict:
        """
        Queue a message and try to send it.

        Returns the row as the panel should draw it immediately — pending,
        with no seq yet. Nothing here blocks on the network.

        `attachment_ids` are files ALREADY uploaded (see upload_attachment).
        The file goes up first and the message claims it, so the conversation
        never shows a blank line while an upload is in progress.
        """
        text = (body or "").strip()
        attachment_ids = [int(a) for a in (attachment_ids or [])]
        # A file needs no words with it.
        if not text and not attachment_ids:
            raise ValueError("Message cannot be empty")
        if len(text) > MAX_BODY:
            raise ValueError(f"Message is too long — {len(text)} of {MAX_BODY} characters")

        client_msg_id = str(uuid.uuid4())
        created = datetime.now().isoformat(timespec="seconds")
        with Database.get_connection() as conn:
            conn.execute(
                "INSERT INTO chat_outbox (client_msg_id, channel_id, body, created_at, "
                "reply_to, mentions, attachment_ids) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (client_msg_id, channel_id, text, created,
                 int(reply_to) if reply_to else None,
                 json.dumps(list(mentions or [])),
                 json.dumps(attachment_ids)),
            )
        self.outbox_changed.emit()
        self._wake.set()
        return {
            "client_msg_id": client_msg_id,
            "channel_id": channel_id,
            "body": text,
            "created_at": created,
            "reply_to": reply_to,
            "attachment_count": len(attachment_ids),
            "pending": True,
        }

    @staticmethod
    def pending(channel_id: int | None = None) -> list[dict]:
        """Queued messages not yet confirmed, oldest first."""
        with Database.get_connection() as conn:
            if channel_id is None:
                rows = conn.execute(
                    "SELECT * FROM chat_outbox WHERE delivered_seq IS NULL ORDER BY id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM chat_outbox WHERE delivered_seq IS NULL AND channel_id = ? "
                    "ORDER BY id", (channel_id,)
                ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def pending_count() -> int:
        with Database.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM chat_outbox WHERE delivered_seq IS NULL"
            ).fetchone()
        return int(row["n"] if row else 0)

    # ── the loop ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._flush_outbox()
                self._poll()
                self._poll_alerts()
            except Exception as error:                  # never kill the thread
                LoggerService.log_verbose(f"ChatManager: unexpected error — {error}")

            self._wake.wait(timeout=self._interval())
            self._wake.clear()

    def _interval(self) -> float:
        if self._failures:
            # Doubling, but never past MAX_BACKOFF.
            return min(INTERVAL_APP_OPEN * (2 ** self._failures), MAX_BACKOFF)
        with self._lock:
            if not self._app_focused:
                return INTERVAL_BACKGROUND
            return INTERVAL_ACTIVE_CHAT if self._active_channel else INTERVAL_APP_OPEN

    def _poll_alerts(self) -> None:
        """Administrative alerts, for the people they are addressed to.

        THE GAP THIS CLOSES. "admin or super admin ko alert ka bhi message
        aaye" was built at both ends and joined at neither: the panel knows
        how to announce an alert, and the server knows how to work one out —
        but nothing carried them between the two. /admin/alerts computes them
        fresh on every request and writes nothing down, and the poll only ever
        carried rows from the notifications table. So the Alerts page could
        show three things needing attention while the desktop stayed silent,
        which is how it was found.

        Asked on a slow clock of its own. Alerts are about hours and days —
        an app that has not reported since Tuesday, a shift nobody logged in
        for — so five minutes is as good as five seconds, and the query is
        expensive enough that asking it every few seconds for every admin
        would be a poor trade.

        Each alert is announced ONCE. They are recomputed every time and stay
        true until somebody acts, so without a memory of what has been said
        the same three would pop up all day and be turned off by lunchtime.
        """
        role = getattr(SessionManager, "role", "")
        if role not in ("admin", "super_admin"):
            return
        now = time.time()
        if now - self._alerts_checked_at < ALERT_POLL_SECONDS:
            return
        self._alerts_checked_at = now

        try:
            response = _http.get(f"{API_BASE_URL}/admin/alerts",
                                 headers=self._headers(), timeout=15)
            if response.status_code != 200:
                return
            alerts = response.json().get("alerts") or []
        except Exception:
            return          # a missed alert check is not worth a log line

        fresh = []
        for alert in alerts:
            # Identity, not equality: "No data for 3 d" becomes "4 d"
            # tomorrow, and that is the same problem, not a new one.
            key = f"{alert.get('employee_id')}:{alert.get('type')}"
            if key in self._alerts_seen:
                continue
            self._alerts_seen.add(key)
            fresh.append(alert)

        # What has stopped being true can be announced again if it returns.
        self._alerts_seen &= {
            f"{a.get('employee_id')}:{a.get('type')}" for a in alerts}

        if fresh:
            self.notifications.emit(fresh)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {SessionManager.auth_token}",
            "Content-Type": "application/json",
        }

    def _mark_online(self, online: bool) -> None:
        if online == self._online:
            return
        self._online = online
        self.online_changed.emit(online)

    # ── outbox ──────────────────────────────────────────────────────────

    def _flush_outbox(self) -> None:
        queued = self.pending()
        if not queued:
            return

        sent = False
        for row in queued:
            if self._stop.is_set():
                break
            try:
                payload = {
                    "body": row["body"],
                    "client_msg_id": row["client_msg_id"],
                }
                if row.get("reply_to"):
                    payload["reply_to"] = row["reply_to"]
                # Stored as JSON because SQLite has no array type. A row
                # written by an older build has NULL here, which must not
                # crash the queue it is sitting in.
                for key in ("mentions", "attachment_ids"):
                    try:
                        value = json.loads(row.get(key) or "[]")
                    except (TypeError, ValueError):
                        value = []
                    if value:
                        payload[key] = value

                response = _http.post(
                    f"{API_BASE_URL}/chat/channels/{row['channel_id']}/messages",
                    json=payload,
                    headers=self._headers(),
                    timeout=30,
                )
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                # The whole queue waits. Sending later messages while an
                # earlier one is stuck would reorder the conversation, and a
                # reply arriving before what it replies to reads as nonsense.
                self._bump_attempt(row["client_msg_id"], "offline")
                self._mark_online(False)
                return
            except Exception as error:
                self._bump_attempt(row["client_msg_id"], str(error)[:120])
                return

            if response.status_code in (200, 201):
                # 200 means the server recognised a resend — it already had
                # this message. Either way it is delivered and must leave the
                # queue, or it would be retried forever.
                confirmed = {}
                try:
                    confirmed = response.json().get("message") or {}
                except Exception:
                    pass
                seq = int(confirmed.get("seq") or 0)
                self._mark_delivered(row["client_msg_id"], seq)

                # Hand the confirmed message straight to the panel.
                #
                # BUG this fixes: marking it delivered takes it out of the
                # pending queue, and the panel redraws — but the real message
                # has not been polled yet, so it vanishes from the screen
                # until the next poll comes round. On this connection that is
                # several seconds of somebody's own message simply not being
                # there, and it reappearing only when they leave the channel
                # and come back.
                #
                # The poll will deliver it again; _on_messages ignores a seq
                # it already holds.
                if confirmed:
                    self.messages.emit([confirmed])
                sent = True
                continue

            if response.status_code in (400, 403, 404, 409):
                # Refused for a reason retrying cannot fix — too long, an
                # announcement channel, a team that has been archived, a
                # channel this person is no longer in. Keeping it would mean
                # retrying it every few seconds for the rest of the session.
                message = ""
                try:
                    message = str(response.json().get("message", ""))
                except Exception:
                    pass
                LoggerService.log(f"CHAT MESSAGE REJECTED : {message or response.status_code}")
                self._mark_failed(row["client_msg_id"], message or f"HTTP {response.status_code}")
                sent = True
                continue

            # 429 or a server error — worth trying again shortly.
            self._bump_attempt(row["client_msg_id"], f"HTTP {response.status_code}")
            return

        if sent:
            self.outbox_changed.emit()

    @staticmethod
    def _mark_delivered(client_msg_id: str, seq: int) -> None:
        with Database.get_connection() as conn:
            conn.execute(
                "UPDATE chat_outbox SET delivered_seq = ?, last_error = NULL "
                "WHERE client_msg_id = ?", (seq, client_msg_id))

    @staticmethod
    def _mark_failed(client_msg_id: str, reason: str) -> None:
        # delivered_seq 0 means "will not be sent" — it leaves the queue but
        # the row stays, so the panel can show it struck through with why
        # rather than having the message vanish and the employee assume it
        # went.
        with Database.get_connection() as conn:
            conn.execute(
                "UPDATE chat_outbox SET delivered_seq = 0, last_error = ? "
                "WHERE client_msg_id = ?", (reason, client_msg_id))

    @staticmethod
    def _bump_attempt(client_msg_id: str, reason: str) -> None:
        with Database.get_connection() as conn:
            conn.execute(
                "UPDATE chat_outbox SET attempts = attempts + 1, last_error = ? "
                "WHERE client_msg_id = ?", (reason, client_msg_id))

    @staticmethod
    def discard(client_msg_id: str) -> None:
        """Give up on a message the employee no longer wants sent."""
        with Database.get_connection() as conn:
            conn.execute("DELETE FROM chat_outbox WHERE client_msg_id = ?", (client_msg_id,))

    # ── polling ─────────────────────────────────────────────────────────

    @staticmethod
    def cursor() -> int:
        try:
            return int(SettingsService.get_setting(CURSOR_KEY, "0") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _save_cursor(value: int) -> None:
        SettingsService.save_setting(CURSOR_KEY, str(int(value)))

    def _poll(self) -> None:
        if self._session_over:
            # Nothing this token asks for will ever be answered again.
            return
        since = self.cursor()
        try:
            response = _http.get(
                f"{API_BASE_URL}/chat/updates",
                params={"since": since},
                headers=self._headers(),
                timeout=15,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            self._failures += 1
            self._mark_online(False)
            return
        except Exception as error:
            self._failures += 1
            LoggerService.log_verbose(f"ChatManager: poll failed — {error}")
            return

        if response.status_code in (401, 403):
            # THE SESSION IS GONE, not a bad connection. Reported from a real
            # machine: an administrator forced a logout, the panel stayed open
            # saying ONLINE · Tracking Active, and this loop went on asking a
            # dead session every minute — one "poll HTTP 401" line per attempt,
            # for as long as the app was left running.
            #
            # Once, then stop asking, and tell the panel so it can return the
            # person to the login screen. The loop is torn down rather than
            # backed off: there is nothing this token can ever be right for
            # again.
            self._mark_online(False)
            if not self._session_over:
                self._session_over = True
                LoggerService.log("SESSION ENDED : the server no longer "
                                  "accepts this session")
                self.session_ended.emit(
                    "Your session was ended. Please sign in again.")
            self.stop()
            return

        if response.status_code != 200:
            self._failures += 1
            LoggerService.log_verbose(
                f"ChatManager: poll HTTP {response.status_code} — {response.text[:120]}")
            return

        self._failures = 0
        self._mark_online(True)

        try:
            payload = response.json()
        except Exception:
            return

        cursor = int(payload.get("cursor") or 0)
        arrived = payload.get("messages") or []
        alerts = payload.get("notifications") or []
        withdrawn = payload.get("deletions") or []

        # Only ever forwards. A reply that arrives late — which happens on a
        # lossy link — must not walk the cursor back and replay messages the
        # employee has already seen.
        if cursor > since:
            self._save_cursor(cursor)

        if arrived:
            self.messages.emit(arrived)
        if alerts:
            self.notifications.emit(alerts)
        if withdrawn:
            self.deletions.emit([int(seq) for seq in withdrawn])

    # ── one-off reads the panel needs ───────────────────────────────────
    #  Plain calls, made on a worker thread by the panel. They are here so
    #  every chat route lives in one file and the panel holds no URLs.

    @staticmethod
    def _get(path: str, params: dict | None = None, timeout: int = 15) -> dict:
        response = _http.get(
            f"{API_BASE_URL}{path}",
            params=params or {},
            headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    @classmethod
    def fetch_teams(cls) -> dict:
        return cls._get("/chat/me/teams")

    @classmethod
    def fetch_history(cls, channel_id: int, before: int | None = None,
                      limit: int = 50) -> dict:
        params = {"limit": limit}
        if before:
            params["before"] = before
        return cls._get(f"/chat/channels/{channel_id}/messages", params)

    @classmethod
    def fetch_members(cls, channel_id: int) -> dict:
        return cls._get(f"/chat/channels/{channel_id}/members")

    @classmethod
    def search(cls, text: str, channel_id: int | None = None) -> dict:
        params = {"q": text}
        if channel_id:
            params["channel_id"] = channel_id
        return cls._get("/chat/search", params)

    @classmethod
    def mark_read(cls, channel_id: int, seq: int) -> None:
        _http.post(
            f"{API_BASE_URL}/chat/channels/{channel_id}/read",
            json={"seq": int(seq)},
            headers={"Authorization": f"Bearer {SessionManager.auth_token}",
                     "Content-Type": "application/json"},
            timeout=12,
        )

    @classmethod
    def edit(cls, seq: int, body: str) -> dict:
        response = _http.patch(
            f"{API_BASE_URL}/chat/messages/{seq}",
            json={"body": body},
            headers={"Authorization": f"Bearer {SessionManager.auth_token}",
                     "Content-Type": "application/json"},
            timeout=12,
        )
        payload = {}
        try:
            payload = response.json()
        except Exception:
            pass
        if response.status_code != 200:
            raise RuntimeError(payload.get("message") or f"HTTP {response.status_code}")
        return payload

    # ── files ───────────────────────────────────────────────────────────
    #  Encrypted here, before anything leaves the machine — the same
    #  CryptoEngine the screenshots already use. The server stores bytes it
    #  cannot read; decryption happens back on a client that holds the key.

    @classmethod
    def upload_attachment(cls, channel_id: int, file_path: str) -> dict:
        """
        Encrypt a file and upload it. Returns the attachment the server made.

        Blocking — call it on a worker thread. It is deliberately separate
        from send(): the file goes up FIRST and the message claims it
        afterwards, so the conversation never carries a blank line for the
        length of an upload, nor a permanent one if the upload fails.
        """
        name = os.path.basename(file_path)
        size = os.path.getsize(file_path)
        if size > MAX_ATTACHMENT_BYTES:
            raise ValueError(
                f"{name} is {size // (1024 * 1024)} MB — the limit is "
                f"{MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB.")
        if size == 0:
            raise ValueError(f"{name} is empty.")

        with open(file_path, "rb") as handle:
            blob = CryptoEngine.encrypt_bytes(handle.read())

        response = _http.post(
            f"{API_BASE_URL}/chat/channels/{channel_id}/attachments",
            files={"file": (name + ".enc", blob, "application/octet-stream")},
            data={"file_name": name, "mime_type": "application/octet-stream"},
            headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
            timeout=120,
        )
        payload = {}
        try:
            payload = response.json()
        except Exception:
            pass
        if response.status_code != 201:
            raise RuntimeError(payload.get("message") or f"HTTP {response.status_code}")
        return payload["attachment"]

    @classmethod
    def attachment_bytes(cls, attachment_id: int) -> bytes:
        """Fetch a file and decrypt it in memory. Blocking.

        Used to show images inside the conversation. Nothing is written to
        disk: the server holds these encrypted precisely so a copy of every
        picture anybody sent does not accumulate in plain sight, and a cache
        folder full of decrypted ones would undo that.
        """
        response = _http.get(
            f"{API_BASE_URL}/chat/attachments/{attachment_id}",
            headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
            timeout=120,
        )
        if response.status_code != 200:
            message = f"HTTP {response.status_code}"
            try:
                message = response.json().get("message", message)
            except Exception:
                pass
            raise RuntimeError(message)
        return CryptoEngine.decrypt_bytes(response.content)

    @classmethod
    def download_attachment(cls, attachment_id: int, dest_path: str) -> str:
        """Fetch a file and decrypt it to `dest_path`. Blocking."""
        response = _http.get(
            f"{API_BASE_URL}/chat/attachments/{attachment_id}",
            headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
            timeout=120,
        )
        if response.status_code != 200:
            message = f"HTTP {response.status_code}"
            try:
                message = response.json().get("message", message)
            except Exception:
                pass
            raise RuntimeError(message)

        # Written to a temporary name and moved into place only once the
        # decryption has succeeded. Otherwise a failed or truncated download
        # leaves a file with the right name and the wrong contents sitting in
        # the employee's Downloads folder, which they will open and blame on
        # whoever sent it.
        plain = CryptoEngine.decrypt_bytes(response.content)
        temporary = dest_path + ".part"
        with open(temporary, "wb") as handle:
            handle.write(plain)
        os.replace(temporary, dest_path)
        return dest_path

    # ── pins ────────────────────────────────────────────────────────────

    @classmethod
    def set_pinned(cls, seq: int, pinned: bool) -> None:
        response = _http.post(
            f"{API_BASE_URL}/chat/messages/{seq}/pin",
            json={"pinned": bool(pinned)},
            headers={"Authorization": f"Bearer {SessionManager.auth_token}",
                     "Content-Type": "application/json"},
            timeout=12,
        )
        if response.status_code != 200:
            message = f"HTTP {response.status_code}"
            try:
                message = response.json().get("message", message)
            except Exception:
                pass
            raise RuntimeError(message)

    @classmethod
    def delete_message(cls, seq: int) -> None:
        """Withdraw one of your own messages.

        The server keeps the row and the text — this takes it off screens, it
        does not erase it from the record. Named `delete` because that is what
        the person clicking it means.
        """
        response = _http.delete(
            f"{API_BASE_URL}/chat/messages/{seq}",
            headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
            timeout=12,
        )
        if response.status_code != 200:
            message = f"HTTP {response.status_code}"
            try:
                message = response.json().get("message", message)
            except Exception:
                pass
            raise RuntimeError(message)

    @classmethod
    def search_people(cls, query: str) -> dict:
        """Who this person can start a conversation with.

        Anybody may write to anybody — the owner's decision, and what an
        office expects. Suspended accounts and yourself are left out by the
        server, so the panel never has to filter them.
        """
        return cls._get("/chat/people", {"q": query})

    @classmethod
    def open_direct(cls, employee_id: str) -> dict:
        """Open the conversation with somebody, creating it the first time."""
        response = _http.post(
            f"{API_BASE_URL}/chat/direct",
            json={"employee_id": employee_id},
            headers={"Authorization": f"Bearer {SessionManager.auth_token}",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        if response.status_code != 200:
            message = f"HTTP {response.status_code}"
            try:
                message = response.json().get("message", message)
            except Exception:
                pass
            raise RuntimeError(message)
        return response.json()

    @classmethod
    def fetch_directs(cls) -> dict:
        """Every conversation this person has, newest first."""
        return cls._get("/chat/directs")

    @classmethod
    def fetch_pinned(cls, channel_id: int) -> dict:
        return cls._get(f"/chat/channels/{channel_id}/pinned")

    @classmethod
    def mark_notifications_read(cls, ids: list | None = None) -> None:
        _http.post(
            f"{API_BASE_URL}/chat/notifications/read",
            json={"ids": ids} if ids else {},
            headers={"Authorization": f"Bearer {SessionManager.auth_token}",
                     "Content-Type": "application/json"},
            timeout=12,
        )
