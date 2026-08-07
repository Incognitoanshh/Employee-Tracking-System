"""
The client half of chat: the outbox, the cursor, and the page that draws them.

What is worth testing here is not "does a message send" — that is the server's
suite. It is the behaviour on a bad connection, because that is where a chat
client quietly loses or duplicates what somebody typed:

  * A message must survive being typed while offline, and go later.
  * A retry of one that already arrived must not post it twice. The client
    half of that is sending the SAME client_msg_id every time; if it generates
    a fresh one per attempt the server's deduplication cannot help.
  * The queue must not reorder. Sending later messages while an earlier one is
    stuck puts a reply above the thing it replies to.
  * A message that will never send must not vanish. Silently dropping it means
    the employee believes it went.
  * The cursor must never move backwards, or messages already seen replay.

Run:  python3 tests/test_chat_client.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# A throwaway storage directory, so a developer's real ets.db is never touched.
_TMP = tempfile.mkdtemp(prefix="ets_chat_test_")
os.environ["ETS_STORAGE_DIR"] = _TMP

import client.core.config as config
config.STORAGE_DIR = _TMP

from client.infrastructure.database import database as database_module
database_module.STORAGE_DIR = _TMP
database_module.Database.DB_PATH = os.path.join(_TMP, "ets.db")

from client.infrastructure.database.database import Database
from client.application.managers import chat_manager as chat_module
from client.application.managers.chat_manager import ChatManager

failures = 0


def check(label, ok, detail=""):
    global failures
    if not ok:
        failures += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or not detail else f"  — {detail}"))


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = str(payload)

    def json(self):
        return self._payload


def main():
    print("Chat — the client side\n")
    Database.initialize()

    # ── the outbox ──────────────────────────────────────────────────────
    print("The outbox")
    chat = ChatManager()

    first = chat.send(3, "kal ka report bhej dena")
    check("a message is queued the moment it is typed",
          ChatManager.pending_count() == 1, str(ChatManager.pending_count()))
    check("and is handed back as pending, so it can be drawn straight away",
          first["pending"] is True and first["client_msg_id"])
    check("with no seq yet — the server has not seen it",
          "seq" not in first)

    chat.send(3, "second one")
    queued = ChatManager.pending()
    check("the queue keeps the order it was typed in",
          [row["body"] for row in queued] == ["kal ka report bhej dena", "second one"],
          str([row["body"] for row in queued]))

    try:
        chat.send(3, "   ")
        check("an empty message is refused", False, "no error raised")
    except ValueError:
        check("an empty message is refused", True)

    try:
        chat.send(3, "x" * (chat_module.MAX_BODY + 1))
        check("an over-long message is refused", False, "no error raised")
    except ValueError as error:
        check("an over-long message is refused", "too long" in str(error).lower(), str(error))

    # ── offline, then back ──────────────────────────────────────────────
    print("\nWhen the network is down")
    import requests

    calls = []

    def offline_post(url, **kwargs):
        calls.append(kwargs.get("json", {}))
        raise requests.exceptions.ConnectionError("no route to host")

    real_post = chat_module.requests.post
    chat_module.requests.post = offline_post
    chat._flush_outbox()
    check("nothing is lost when the send fails",
          ChatManager.pending_count() == 2, str(ChatManager.pending_count()))
    check("and the queue stops at the first failure rather than reordering",
          len(calls) == 1, f"{len(calls)} attempt(s)")

    attempts = ChatManager.pending()[0]["attempts"]
    check("the attempt is counted", attempts >= 1, str(attempts))

    # The important one: the retry must reuse the id, not mint a new one.
    first_id = calls[0]["client_msg_id"]
    calls.clear()
    chat._flush_outbox()
    check("a retry sends the SAME client_msg_id, so the server can deduplicate",
          calls and calls[0]["client_msg_id"] == first_id,
          f"{calls[0]['client_msg_id'] if calls else None} vs {first_id}")

    # Now the network comes back.
    sent = []

    def online_post(url, **kwargs):
        body = kwargs.get("json", {})
        sent.append(body)
        return FakeResponse(201, {"message": {"seq": 100 + len(sent)}})

    chat_module.requests.post = online_post
    chat._flush_outbox()
    check("everything queued goes when the connection returns",
          ChatManager.pending_count() == 0, str(ChatManager.pending_count()))
    check("in the order it was typed",
          [b["body"] for b in sent] == ["kal ka report bhej dena", "second one"],
          str([b["body"] for b in sent]))

    with Database.get_connection() as conn:
        rows = conn.execute(
            "SELECT body, delivered_seq FROM chat_outbox ORDER BY id").fetchall()
    check("and each row keeps the seq the server gave it",
          [r["delivered_seq"] for r in rows] == [101, 102],
          str([r["delivered_seq"] for r in rows]))

    # ── a resend the server already had ─────────────────────────────────
    print("\nA reply that was lost")
    chat.send(3, "arrived but the reply was lost")

    def duplicate_post(url, **kwargs):
        # This is what the server answers when it recognises the id: 200 with
        # duplicate set, carrying the seq it stored the first time.
        return FakeResponse(200, {"duplicate": True, "message": {"seq": 55}})

    chat_module.requests.post = duplicate_post
    chat._flush_outbox()
    check("a recognised resend leaves the queue instead of retrying forever",
          ChatManager.pending_count() == 0, str(ChatManager.pending_count()))
    with Database.get_connection() as conn:
        row = conn.execute(
            "SELECT delivered_seq FROM chat_outbox WHERE body LIKE 'arrived%'").fetchone()
    check("and settles on the seq the server already had",
          row["delivered_seq"] == 55, str(row["delivered_seq"]))

    # ── refused for good ────────────────────────────────────────────────
    print("\nA message that will never send")
    chat.send(9, "into an archived team")

    def refused_post(url, **kwargs):
        return FakeResponse(409, {"message": "Development is archived — it is read-only."})

    chat_module.requests.post = refused_post
    chat._flush_outbox()
    check("it stops being retried", ChatManager.pending_count() == 0,
          str(ChatManager.pending_count()))
    with Database.get_connection() as conn:
        row = conn.execute(
            "SELECT delivered_seq, last_error FROM chat_outbox "
            "WHERE body = 'into an archived team'").fetchone()
    check("but the row survives, so the message does not silently disappear",
          row is not None)
    check("and it carries the reason, to show the employee why",
          "archived" in (row["last_error"] or "").lower(), row["last_error"])

    # 429 is the opposite case — worth trying again shortly.
    chat.send(3, "too fast")
    chat_module.requests.post = lambda url, **kw: FakeResponse(429, {"message": "slow down"})
    chat._flush_outbox()
    check("but being throttled keeps the message queued for another try",
          ChatManager.pending_count() == 1, str(ChatManager.pending_count()))

    chat_module.requests.post = real_post

    # ── the cursor ──────────────────────────────────────────────────────
    print("\nThe cursor")
    from client.services.settings_service import SettingsService
    SettingsService.save_setting(chat_module.CURSOR_KEY, "0")
    check("it starts at zero", ChatManager.cursor() == 0)

    real_get = chat_module.requests.get
    chat_module.requests.get = lambda url, **kw: FakeResponse(
        200, {"cursor": 500, "messages": [], "notifications": []})
    chat._poll()
    check("a poll moves it forward", ChatManager.cursor() == 500,
          str(ChatManager.cursor()))

    # A late reply on a lossy link carries an older cursor. Applying it would
    # replay everything between, so it must be ignored.
    chat_module.requests.get = lambda url, **kw: FakeResponse(
        200, {"cursor": 300, "messages": [], "notifications": []})
    chat._poll()
    check("a late reply carrying an older cursor cannot move it backwards",
          ChatManager.cursor() == 500, str(ChatManager.cursor()))

    chat_module.requests.get = lambda url, **kw: FakeResponse(500, {})
    chat._poll()
    check("a server error leaves it alone", ChatManager.cursor() == 500,
          str(ChatManager.cursor()))
    chat_module.requests.get = real_get

    # ── poll pacing ─────────────────────────────────────────────────────
    print("\nHow often it asks")
    chat._failures = 0
    chat.set_app_focused(True)
    chat.set_active_channel(None)
    check("app open, no channel — the slower rate",
          chat._interval() == chat_module.INTERVAL_APP_OPEN, str(chat._interval()))
    chat.set_active_channel(4)
    check("watching a channel — the fast rate",
          chat._interval() == chat_module.INTERVAL_ACTIVE_CHAT, str(chat._interval()))
    chat.set_app_focused(False)
    check("minimised — the slowest, so a background panel costs the server little",
          chat._interval() == chat_module.INTERVAL_BACKGROUND, str(chat._interval()))

    chat._failures = 9
    check("and a long outage backs off but stays capped",
          chat._interval() == chat_module.MAX_BACKOFF, str(chat._interval()))

    # ── the page draws without a server ─────────────────────────────────
    print("\nThe page")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from client.presentation.windows.team_page import TeamPage, _Bubble, _ChannelRow
    from client.presentation.theme import C
    from datetime import datetime

    page = TeamPage(None, chat)
    check("it builds with no network at all", page is not None)

    page._on_teams({"teams": []})
    check("and says so when the employee is in no team",
          len(page._rows) == 0)

    page._on_teams({"teams": [{
        "id": 1, "name": "Development", "is_archived": False, "unread": 3,
        "channels": [
            {"id": 1, "name": "General", "type": "STANDARD", "is_default": True,
             "is_private": False, "unread": 3, "last_seq": 10, "last_read_seq": 7},
            {"id": 2, "name": "Company Updates", "type": "ANNOUNCEMENT",
             "is_default": False, "is_private": False, "unread": 0,
             "last_seq": 4, "last_read_seq": 4},
        ]}]})
    check("channels are listed", len(page._rows) == 2, str(len(page._rows)))

    row = _ChannelRow({"id": 1, "name": "General", "type": "STANDARD",
                       "is_private": False, "unread": 5})
    check("an unread channel shows its count", row._badge.isVisibleTo(row))
    row.set_unread(0)
    check("and hides it once read", not row._badge.isVisibleTo(row))

    # An announcement channel must be read-only in the interface, not just at
    # the server — a composer that accepts text and then fails is worse than
    # no composer.
    page._on_history({
        "channel": {"id": 2, "name": "Company Updates", "type": "ANNOUNCEMENT",
                    "team_name": "Development", "is_archived": False,
                    "can_post": False},
        "messages": [], "has_more": False})
    check("an announcement channel hides the composer",
          not page._composer_row.isVisibleTo(page))
    check("and explains why instead of just being empty",
          "administrators" in page._read_only.text().lower(), page._read_only.text())

    page._on_history({
        "channel": {"id": 1, "name": "General", "type": "STANDARD",
                    "team_name": "Development", "is_archived": True,
                    "can_post": False},
        "messages": [], "has_more": False})
    check("an archived team says it is read-only",
          "archived" in page._read_only.text().lower(), page._read_only.text())

    bubble = _Bubble({"seq": 1, "sender_name": "Rajesh Kumar", "former": True,
                      "body": "haan wo kar dunga", "created_at": "2026-08-07T09:00:00Z",
                      "edit_count": 0}, mine=False)
    labels = [w.text() for w in bubble.findChildren(type(bubble.findChild(
        __import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)))]
    check("a former employee's message keeps their name, marked as former",
          any("Rajesh Kumar" in t and "Former" in t for t in labels), str(labels))


    # ── Phase 2: replies, mentions, files ───────────────────────────────
    print("\nReplies and mentions in the queue")
    queued = chat.send(3, "@rajesh dekh lo", reply_to=77, mentions=["E001"])
    check("a reply keeps what it answers", queued["reply_to"] == 77, str(queued["reply_to"]))

    payloads = []

    def capture_post(url, **kwargs):
        payloads.append(kwargs.get("json", {}))
        return FakeResponse(201, {"message": {"seq": 900}})

    chat_module.requests.post = capture_post
    chat._flush_outbox()
    sent_payload = payloads[-1]
    check("and the send carries reply_to through to the server",
          sent_payload.get("reply_to") == 77, str(sent_payload.get("reply_to")))
    check("along with the resolved mentions",
          sent_payload.get("mentions") == ["E001"], str(sent_payload.get("mentions")))

    # A row written by a build that predates these columns has NULL in them.
    # The queue it is sitting in must not crash on it.
    with Database.get_connection() as conn:
        conn.execute(
            "INSERT INTO chat_outbox (client_msg_id, channel_id, body, created_at) "
            "VALUES (?,?,?,?)", ("legacy-row-0000", 3, "from an older build", "2026-08-07"))
    payloads.clear()
    chat._flush_outbox()
    check("a queued row from an older build still sends",
          any(p.get("body") == "from an older build" for p in payloads),
          str([p.get("body") for p in payloads]))
    check("and simply carries no reply or mentions",
          "reply_to" not in payloads[-1] and "mentions" not in payloads[-1],
          str(payloads[-1]))

    print("\nFiles")
    import tempfile as _tempfile
    from client.security.crypto_engine import CryptoEngine
    original = b"the quarterly report, in plain text" * 40
    source = os.path.join(_TMP, "report.pdf")
    with open(source, "wb") as handle:
        handle.write(original)

    uploaded = {}

    def fake_upload(url, **kwargs):
        uploaded["bytes"] = kwargs["files"]["file"][1]
        uploaded["name"] = kwargs["data"]["file_name"]
        return FakeResponse(201, {"attachment": {"id": 12, "file_name": "report.pdf",
                                                 "size_bytes": len(uploaded["bytes"])}})

    chat_module.requests.post = fake_upload
    attachment = ChatManager.upload_attachment(3, source)
    check("a file uploads and comes back with an id", attachment["id"] == 12)
    check("the display name is the one the person chose",
          uploaded["name"] == "report.pdf", uploaded["name"])
    # The whole point: what leaves the machine is not readable.
    check("what is SENT is encrypted, not the file itself",
          uploaded["bytes"] != original and original[:20] not in uploaded["bytes"],
          "plaintext left the machine")
    check("and it decrypts back to exactly the original",
          CryptoEngine.decrypt_bytes(uploaded["bytes"]) == original)

    def fake_download(url, **kwargs):
        response = FakeResponse(200, {})
        response.content = uploaded["bytes"]
        return response

    chat_module.requests.get = fake_download
    destination = os.path.join(_TMP, "saved.pdf")
    ChatManager.download_attachment(12, destination)
    with open(destination, "rb") as handle:
        check("a download decrypts to the original contents",
              handle.read() == original)
    check("and leaves no half-written temporary file behind",
          not os.path.exists(destination + ".part"))

    # A download that fails must not leave a plausible-looking file, because
    # somebody will open it and blame whoever sent it.
    chat_module.requests.get = lambda url, **kw: FakeResponse(404, {"message": "File not found"})
    broken = os.path.join(_TMP, "broken.pdf")
    try:
        ChatManager.download_attachment(99, broken)
        check("a failed download raises", False, "no error raised")
    except RuntimeError as error:
        check("a failed download raises with the server's wording",
              "not found" in str(error).lower(), str(error))
    check("and writes nothing at all", not os.path.exists(broken))

    big = os.path.join(_TMP, "big.bin")
    with open(big, "wb") as handle:
        handle.write(b"0" * (chat_module.MAX_ATTACHMENT_BYTES + 1))
    try:
        ChatManager.upload_attachment(3, big)
        check("an over-sized file is refused before it is sent", False, "no error")
    except ValueError as error:
        check("an over-sized file is refused BEFORE encrypting and uploading",
              "limit" in str(error).lower(), str(error))

    chat_module.requests.post = real_post
    chat_module.requests.get = real_get

    print("\nThe composer")
    from client.presentation.windows.team_page import _Composer, _within_edit_window
    from PySide6.QtGui import QTextCursor

    def typed(text):
        composer = _Composer()
        composer.setPlainText(text)
        cursor = composer.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        composer.setTextCursor(cursor)
        return composer

    check("an @ at the start of a word begins a mention",
          typed("hey @raj").current_mention() == "raj")
    check("an email address does NOT — otherwise the list opens mid-sentence",
          typed("mail me at someone@exam").current_mention() is None)
    check("a completed word closes it again",
          typed("hey @rajesh done").current_mention() is None)
    composer = typed("hey @raj")
    composer.replace_mention("rajesh")
    check("picking a name writes the username, which has no spaces in it",
          composer.toPlainText() == "hey @rajesh ", repr(composer.toPlainText()))

    from datetime import timezone as _tz, timedelta as _td
    now = datetime.now(_tz.utc)
    check("Edit is offered inside the five minute window",
          _within_edit_window((now - _td(minutes=2)).isoformat()))
    check("and not offered outside it",
          not _within_edit_window((now - _td(minutes=9)).isoformat()))

    print("\nDrawing the new parts")
    reply_bubble = _Bubble({
        "seq": 5, "sender_name": "Amit Sharma", "body": "bhej diya",
        "created_at": now.isoformat(), "edit_count": 0,
        "reply": {"seq": 4, "sender_name": "Rajesh Kumar",
                  "excerpt": "kal ka report bhej dena"},
    }, mine=False)
    from PySide6.QtWidgets import QLabel as _L, QPushButton as _B
    texts = [w.text() for w in reply_bubble.findChildren(_L)] + \
            [w.text() for w in reply_bubble.findChildren(_B)]
    check("a reply shows what it answers", any("kal ka report" in t for t in texts),
          str(texts))

    file_bubble = _Bubble({
        "seq": 6, "sender_name": "Rajesh Kumar", "body": "", "created_at": now.isoformat(),
        "edit_count": 0,
        "attachments": [{"id": 12, "file_name": "report.pdf", "size_bytes": 2 * 1024 * 1024}],
    }, mine=True)
    texts = [w.text() for w in file_bubble.findChildren(_B)]
    check("a file is shown with its name and size",
          any("report.pdf" in t and "2.0 MB" in t for t in texts), str(texts))

    mention_bubble = _Bubble({
        "seq": 7, "sender_name": "Priya Nair", "body": "@rajesh ye dekh lena",
        "created_at": now.isoformat(), "edit_count": 0, "mentions_me": True,
    }, mine=False)
    check("a message that names you is marked, not just counted",
          C.AMBER in mention_bubble.styleSheet(), mention_bubble.styleSheet()[:80])

    plain_bubble = _Bubble({
        "seq": 8, "sender_name": "Priya Nair", "body": "general chatter",
        "created_at": now.isoformat(), "edit_count": 0, "mentions_me": False,
    }, mine=False)
    check("and one that does not is left alone",
          C.AMBER not in plain_bubble.styleSheet())

    readonly_bubble = _Bubble({
        "seq": 9, "sender_name": "Priya Nair", "body": "notice",
        "created_at": now.isoformat(), "edit_count": 0,
    }, mine=False, can_post=False)
    check("no Reply or Pin is offered where posting is not allowed",
          [b.text() for b in readonly_bubble.findChildren(_B)] == [],
          str([b.text() for b in readonly_bubble.findChildren(_B)]))

    print("\nOpening a channel")
    # The bug this covers: the member and pin requests read `self._channel`,
    # which is only set when the history reply lands — so on the first open it
    # was None and nothing was ever asked for. The member list stayed empty,
    # and because the @ autocomplete is fed from it, mentions appeared not to
    # exist at all. It fixed itself after the 30-second refresh, which made it
    # look intermittent rather than broken.
    asked = []
    page2 = TeamPage(None, chat)
    page2._run = lambda fn, on_done, on_fail=None, *a, **k: asked.append((fn.__name__, a))
    page2._on_teams({"teams": [{
        "id": 1, "name": "Development", "is_archived": False, "unread": 0,
        "channels": [{"id": 7, "name": "Backend", "type": "STANDARD",
                      "is_default": False, "is_private": False, "unread": 0,
                      "last_seq": 3, "last_read_seq": 3}]}]})
    asked.clear()
    page2.open_channel(7)
    names = [name for name, _ in asked]
    check("opening a channel asks for its history", "fetch_history" in names, str(names))
    check("and for its members, on the FIRST open with nothing loaded yet",
          "fetch_members" in names, str(names))
    check("and for its pinned messages",
          "fetch_pinned" in names, str(names))
    check("every one of them for the channel just clicked",
          all(args and args[0] == 7 for name, args in asked
              if name in ("fetch_history", "fetch_members", "fetch_pinned")),
          str(asked))

    # A reply for a channel the employee has already clicked away from must be
    # dropped, or a slow link leaves the wrong people in the member list.
    page2._on_members({"channel_id": 999, "members": [
        {"employee_id": "E009", "name": "Wrong Channel", "status": "ACTIVE",
         "idle_minutes": None, "is_me": False}]})
    check("a late reply for a different channel is ignored",
          page2._members_cache == [], str(page2._members_cache))

    print("\nThe @ list")
    page2._channel = {"id": 7, "name": "Backend", "can_post": True}
    page2._on_members({"channel_id": 7, "members": [
        {"employee_id": "E001", "name": "Rajesh Kumar", "username": "rajesh",
         "status": "ACTIVE", "idle_minutes": None, "is_me": True},
        {"employee_id": "E002", "name": "Amit Sharma", "username": "amit",
         "status": "IDLE", "idle_minutes": 19, "is_me": False},
        {"employee_id": "E003", "name": "Sneha Iyer", "username": "sneha",
         "status": "ACTIVE", "idle_minutes": None, "is_me": False},
    ]})
    check("the member list is kept for the autocomplete",
          len(page2._members_cache) == 3, str(len(page2._members_cache)))

    page2._composer.setPlainText("hey @am")
    cursor = page2._composer.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    page2._composer.setTextCursor(cursor)
    page2._on_mention_typed("am")
    check("typing @am offers Amit", page2._mention_list.count() == 1,
          str(page2._mention_list.count()))
    check("and the list is actually shown", page2._mention_list.isVisibleTo(page2))

    page2._composer.setPlainText("hey @")
    cursor = page2._composer.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    page2._composer.setTextCursor(cursor)
    page2._on_mention_typed("")
    names = [page2._mention_list.item(i).text() for i in range(page2._mention_list.count())]
    check("a bare @ offers everybody else in the channel",
          page2._mention_list.count() == 2, str(names))
    check("but never yourself", not any("Rajesh" in n for n in names), str(names))

    page2._accept_mention()
    check("picking one writes the username into the message",
          page2._composer.toPlainText().startswith("hey @amit "),
          repr(page2._composer.toPlainText()))
    check("and closes the list", not page2._mention_list.isVisibleTo(page2))
    check("the send will carry that person's id",
          page2._mentions_in(page2._composer.toPlainText()) == ["E002"],
          str(page2._mentions_in(page2._composer.toPlainText())))

    print("\nLosing access while looking at it")
    # An administrator can take somebody out of a channel while they are
    # reading it. The server stops serving it immediately, but the panel used
    # to keep the messages on screen: the only sign was that sending failed,
    # with nothing to explain why. Refreshing silently would be no better —
    # the channel would vanish from under them.
    page3 = TeamPage(None, chat)
    page3._run = lambda fn, on_done, on_fail=None, *a, **k: None
    page3._on_teams({"teams": [{
        "id": 1, "name": "Development", "is_archived": False, "unread": 0,
        "channels": [{"id": 7, "name": "Backend", "type": "STANDARD",
                      "is_default": False, "is_private": False, "unread": 0,
                      "last_seq": 3, "last_read_seq": 3}]}]})
    page3.open_channel(7)
    page3._on_history({
        "channel": {"id": 7, "name": "Backend", "type": "STANDARD",
                    "team_name": "Development", "is_archived": False,
                    "can_post": True},
        "messages": [{"seq": 3, "sender_name": "Amit Sharma", "body": "hi",
                      "created_at": now.isoformat(), "edit_count": 0}],
        "has_more": False})
    check("the channel is open to begin with", page3._channel_id == 7)

    told = []
    import client.presentation.windows.team_page as team_page_module
    real_box = team_page_module.QMessageBox.information
    team_page_module.QMessageBox.information = \
        lambda parent, title, text: told.append(text)
    try:
        page3._maybe_lost_access("HTTP 404", 7)
        check("a 404 on the open channel clears it",
              page3._channel_id is None and page3._messages == [],
              f"{page3._channel_id} / {len(page3._messages)}")
        check("and the employee is told why, rather than it just vanishing",
              told and "no longer have access" in told[0].lower(), str(told))

        # Anything else must be left alone — a dropped connection is not a
        # revoked permission, and treating it as one would throw somebody out
        # of a channel every time the network hiccuped.
        page3._channel_id = 7
        page3._channel = {"id": 7, "name": "Backend"}
        told.clear()
        page3._maybe_lost_access("Connection aborted", 7)
        check("but a network error does NOT close the channel",
              page3._channel_id == 7 and told == [], str(told))

        # A late 404 for a channel already navigated away from must not
        # disturb the one now open.
        told.clear()
        page3._maybe_lost_access("HTTP 404", 99)
        check("and a 404 for some other channel is ignored",
              page3._channel_id == 7 and told == [], str(told))
    finally:
        team_page_module.QMessageBox.information = real_box

    check("the channel list refreshes on its own, not only when a message arrives",
          page3._teams_timer.interval() == 60_000,
          str(page3._teams_timer.interval()))

    print()
    if failures:
        print(f"{failures} failure(s)")
    else:
        print("all client chat checks passed")
    # os._exit because Qt leaves non-daemon threads behind and a normal exit
    # can hang — but it skips stdio flushing, so everything printed above is
    # discarded when stdout is a pipe. Flush first.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1 if failures else 0)


if __name__ == "__main__":
    main()
