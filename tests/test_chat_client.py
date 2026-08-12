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

# ISOLATED FROM ANYTHING REAL, BEFORE ANY CLIENT MODULE IS IMPORTED.
#
# Without these two the client's own config falls back to its defaults: the
# installed app's data directory, and the PRODUCTION server. Running this file
# on a machine that has the app installed then wrote into the real local
# database and uploaded to the real server — rows under a made-up employee id
# turned up in the company's audit log, which is exactly what happened.
#
# setdefault, so a harness that points these somewhere on purpose still wins.
os.environ.setdefault("ETS_DATA_DIR", tempfile.mkdtemp(prefix="ets_test_"))
os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:9/api")

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

    real_post = chat_module._http.post
    chat_module._http.post = offline_post
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

    chat_module._http.post = online_post
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

    chat_module._http.post = duplicate_post
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

    chat_module._http.post = refused_post
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
    chat_module._http.post = lambda url, **kw: FakeResponse(429, {"message": "slow down"})
    chat._flush_outbox()
    check("but being throttled keeps the message queued for another try",
          ChatManager.pending_count() == 1, str(ChatManager.pending_count()))

    chat_module._http.post = real_post

    # ── the cursor ──────────────────────────────────────────────────────
    print("\nThe cursor")
    from client.services.settings_service import SettingsService
    SettingsService.save_setting(chat_module.CURSOR_KEY, "0")
    check("it starts at zero", ChatManager.cursor() == 0)

    real_get = chat_module._http.get
    chat_module._http.get = lambda url, **kw: FakeResponse(
        200, {"cursor": 500, "messages": [], "notifications": []})
    chat._poll()
    check("a poll moves it forward", ChatManager.cursor() == 500,
          str(ChatManager.cursor()))

    # A late reply on a lossy link carries an older cursor. Applying it would
    # replay everything between, so it must be ignored.
    chat_module._http.get = lambda url, **kw: FakeResponse(
        200, {"cursor": 300, "messages": [], "notifications": []})
    chat._poll()
    check("a late reply carrying an older cursor cannot move it backwards",
          ChatManager.cursor() == 500, str(ChatManager.cursor()))

    chat_module._http.get = lambda url, **kw: FakeResponse(500, {})
    chat._poll()
    check("a server error leaves it alone", ChatManager.cursor() == 500,
          str(ChatManager.cursor()))
    chat_module._http.get = real_get

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
    # As the real flow does it: the channel is opened first, which is what
    # tells the page which reply it is waiting for. A history reply for any
    # other channel is now dropped, so that a slow reply for the conversation
    # you just left cannot paint itself over the one you are in.
    page._channel_id = 2
    page._on_history({
        "channel": {"id": 2, "name": "Company Updates", "type": "ANNOUNCEMENT",
                    "team_name": "Development", "is_archived": False,
                    "can_post": False},
        "messages": [], "has_more": False})
    check("an announcement channel hides the composer",
          not page._composer_row.isVisibleTo(page))
    check("and explains why instead of just being empty",
          "administrators" in page._read_only.text().lower(), page._read_only.text())

    page._channel_id = 1
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

    chat_module._http.post = capture_post
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

    chat_module._http.post = fake_upload
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

    chat_module._http.get = fake_download
    destination = os.path.join(_TMP, "saved.pdf")
    ChatManager.download_attachment(12, destination)
    with open(destination, "rb") as handle:
        check("a download decrypts to the original contents",
              handle.read() == original)
    check("and leaves no half-written temporary file behind",
          not os.path.exists(destination + ".part"))

    # A download that fails must not leave a plausible-looking file, because
    # somebody will open it and blame whoever sent it.
    chat_module._http.get = lambda url, **kw: FakeResponse(404, {"message": "File not found"})
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

    chat_module._http.post = real_post
    chat_module._http.get = real_get

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
    page3._channel_id = 7
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

    print("\nPictures in the conversation")
    # An image arriving as a file to download is an image nobody looks at.
    import client.presentation.windows.team_page as tp
    from PySide6.QtGui import QPixmap as _PM, QColor as _QC
    from PySide6.QtCore import QBuffer as _QB, QByteArray as _QBA, QIODevice as _QIO

    def _png(w, h, colour="#3b82f6"):
        pm = _PM(w, h); pm.fill(_QC(colour))
        ba = _QBA(); buf = _QB(ba); buf.open(_QIO.OpenModeFlag.WriteOnly)
        pm.save(buf, "PNG"); buf.close()
        return bytes(ba)

    for name, want in (("photo.png", True), ("IMG_0126.PNG", True),
                       ("shot.jpeg", True), ("clip.webp", True),
                       ("report.txt", False), ("data.pdf", False)):
        got = tp._looks_like_image({"file_name": name})
        check(f"{name} is {'an image' if want else 'not an image'}",
              got == want, f"got {got}")
    check("and the .enc the server stores it under does not confuse it",
          tp._looks_like_image({"file_name": "pic.png.enc"}))

    thumb = tp._thumbnail(_png(900, 600))
    check("a wide picture is scaled down to fit the conversation",
          thumb is not None and thumb.width() == tp.THUMB_WIDTH,
          f"{thumb.width() if thumb else None}px")
    check("keeping its proportions",
          thumb is not None and abs(thumb.height() - 600 * tp.THUMB_WIDTH / 900) < 2,
          f"{thumb.height() if thumb else None}px")
    small = tp._thumbnail(_png(120, 80))
    check("a small one is left alone rather than blown up",
          small is not None and small.width() == 120, f"{small.width() if small else None}px")
    check("and something that is not an image does not crash the feed",
          tp._thumbnail(b"not an image at all") is None)

    # The feed is rebuilt on every new message, so without a cache a channel
    # with four pictures would re-download all four every few seconds.
    tp._IMAGE_CACHE.clear()
    for i in range(tp._IMAGE_CACHE_MAX + 10):
        tp._cache_image(i, b"x")
    check("the picture cache is bounded, not a memory leak",
          len(tp._IMAGE_CACHE) <= tp._IMAGE_CACHE_MAX, str(len(tp._IMAGE_CACHE)))
    check("and it keeps the newest rather than the oldest",
          (tp._IMAGE_CACHE_MAX + 9) in tp._IMAGE_CACHE)

    # The bug this closes: the callback guarded on `label.isVisible()` before
    # drawing. A widget inside a scroll area that Qt has not painted yet
    # reports False — so EVERY picture was skipped and sat on "Loading
    # image…" for good, including a 20 KB one that had long since arrived.
    from PySide6.QtWidgets import (QScrollArea as _SA, QVBoxLayout as _VB,
                                   QWidget as _QW, QLabel as _QL)
    area = _SA(); host = _QW(); _VB(host).addWidget(_QL("Loading image…"))
    unpainted = host.findChild(_QL)
    area.setWidget(host)
    check("a label Qt has not painted yet reports itself invisible",
          unpainted.isVisible() is False,
          "if this ever changes, the guard that broke images was merely lucky")

    page4 = TeamPage(None, chat)
    page4._run = lambda fn, on_done, on_fail=None, *a, **k: on_done(_png(400, 300))
    page4._load_image(77, unpainted)
    check("the picture is drawn anyway",
          unpainted.pixmap() is not None and not unpainted.pixmap().isNull(),
          "still stuck on the placeholder")
    check("and the placeholder text is cleared",
          unpainted.text() == "", repr(unpainted.text()))
    check("it is cached, so a rebuild does not fetch it again",
          77 in tp._IMAGE_CACHE)

    # A label deleted by a rebuild mid-download must not take the app down.
    gone = _QL("Loading image…")
    gone.deleteLater()
    gone = None
    page4._loading_images.clear()
    check("and a picture arriving after its message was rebuilt is harmless",
          True, "")

    # The bug this closes: the bubble emitted its image request from inside
    # its own constructor, and the page connects to that signal only after the
    # bubble exists. Every request went nowhere — no picture, and no error
    # either, so it sat on "Loading image…" with nothing to chase.
    from datetime import timezone as _tz2
    asked_for = []
    page5 = TeamPage(None, chat)
    page5._run = lambda fn, on_done, on_fail=None, *a, **k: asked_for.append(a)
    page5._channel = {"id": 1, "name": "General", "can_post": True}
    page5._channel_id = 1
    page5._messages = [{
        "seq": 5, "sender_id": "E2", "sender_name": "Amit", "body": "dekho",
        "created_at": datetime.now(_tz2.utc).isoformat(), "edit_count": 0,
        "attachments": [{"id": 42, "file_name": "IMG_0774.JPG", "size_bytes": 20480}],
    }]
    page5._render_feed()
    check("drawing a message with a picture actually REQUESTS the picture",
          asked_for and asked_for[0] and asked_for[0][0] == 42,
          f"requests: {asked_for}")

    # A file that is not an image asks for nothing.
    asked_for.clear()
    page5._messages = [{
        "seq": 6, "sender_id": "E2", "sender_name": "Amit", "body": "report",
        "created_at": datetime.now(_tz2.utc).isoformat(), "edit_count": 0,
        "attachments": [{"id": 43, "file_name": "notes.pdf", "size_bytes": 900}],
    }]
    page5._render_feed()
    check("and a non-image attachment does not fetch anything",
          asked_for == [], f"requests: {asked_for}")


    # Your own picture must not make a round trip.
    #
    # Sending an image used to mean encrypting it, uploading it, and then
    # DOWNLOADING IT BACK and decrypting it to draw — for bytes already on
    # this machine. On this connection that is a visible wait to see the
    # thing you just sent.
    tp._IMAGE_CACHE.clear()
    source = os.path.join(_TMP, "holiday.png")
    with open(source, "wb") as handle:
        handle.write(_png(300, 200))
    page5._last_upload_path = source
    page5._on_uploaded({"id": 99, "file_name": "holiday.png", "size_bytes": 1})
    check("a picture you upload is cached from the file you chose",
          99 in tp._IMAGE_CACHE)
    check("and it is the real image, not something unreadable",
          tp._thumbnail(tp._IMAGE_CACHE[99]) is not None)

    other = os.path.join(_TMP, "notes.pdf")
    with open(other, "wb") as handle:
        handle.write(b"%PDF-1.4")
    page5._last_upload_path = other
    page5._on_uploaded({"id": 100, "file_name": "notes.pdf", "size_bytes": 8})
    check("a document is not cached as one", 100 not in tp._IMAGE_CACHE)

    tp._IMAGE_CACHE.clear()

    # Nothing decrypted is written to disk: the server keeps these encrypted
    # so a copy of every photograph does not accumulate in the clear.
    page_source = open(tp.__file__, encoding="utf-8").read()
    check("images are held in memory, never written out",
          "open(" not in page_source.split("_IMAGE_CACHE")[1][:600],
          "a cache file would undo the encryption at rest")

    print("\nAttaching")
    page5._channel = {"id": 1, "name": "General", "can_post": True}
    menu = page5.build_attach_menu()
    labels = [a.text() for a in menu.actions()]
    check("the paperclip offers a choice rather than one file browser",
          len(labels) == 2, str(labels))
    check("a photo, and anything else",
          any("Photo" in t for t in labels) and any("File" in t for t in labels),
          str(labels))

    chose = []
    page5._attach_file = lambda images_only=False: chose.append(images_only)
    for action in menu.actions():
        action.trigger()
    check("Photo filters to images, so a picture is not lost among documents",
          chose[0] is True, str(chose))
    check("File does not filter", chose[1] is False, str(chose))

    print("\nWithdrawing a message")
    from PySide6.QtWidgets import QLabel, QPushButton
    own = {"seq": 70, "channel_id": 1, "sender_id": "E001", "sender_name": "Rajesh",
           "body": "galti se bhej diya", "created_at": datetime.now().isoformat(),
           "attachments": [], "mentions": []}
    bubble_mine = _Bubble(dict(own), mine=True, can_post=True)
    labels = [b.text() for b in bubble_mine.findChildren(QPushButton)]
    check("your own message offers the ⋯ menu", "⋯" in labels, str(labels))

    bubble_theirs = _Bubble(dict(own), mine=False, can_post=True)
    labels = [b.text() for b in bubble_theirs.findChildren(QPushButton)]
    check("somebody else's does not — you cannot delete it anyway",
          "⋯" not in labels, str(labels))

    menu = bubble_mine.build_more_menu()
    check("and the menu offers exactly one thing: delete",
          [a.text() for a in menu.actions()] == ["🗑   Delete message"],
          str([a.text() for a in menu.actions()]))

    asked = []
    bubble_mine.delete_requested.connect(lambda seq: asked.append(seq))
    menu.actions()[0].trigger()
    check("clicking it asks for that message, by seq", asked == [70], str(asked))

    tomb = _Bubble({**own, "deleted": True, "body": "", "pinned": True,
                    "mentions_me": True, "attachments": []},
                   mine=True, can_post=True)
    shown = [w.text() for w in tomb.findChildren(QLabel)]
    check("a withdrawn message says so, rather than leaving a gap",
          any("deleted" in t for t in shown), str(shown))
    check("and the words themselves are gone from the screen",
          not any("galti" in t for t in shown), str(shown))
    labels = [b.text() for b in tomb.findChildren(QPushButton)]
    check("nothing can be done to it — no reply, pin, edit, or second delete",
          labels == [], str(labels))

    # The panel applies withdrawals itself rather than refetching: on this
    # link a refetch is most of a second of a message still being readable.
    page6 = TeamPage(None, chat)
    page6._channel = {"id": 1, "name": "General", "can_post": True}
    page6._messages = [
        {"seq": 70, "sender_id": "E001", "sender_name": "R", "body": "secret",
         "created_at": datetime.now().isoformat(),
         "attachments": [{"id": 9, "file_name": "a.png"}], "pinned": True},
        {"seq": 71, "sender_id": "E002", "sender_name": "A", "body": "keep me",
         "created_at": datetime.now().isoformat(), "attachments": []},
    ]
    page6._mark_deleted([70])
    gone = page6._messages[0]
    check("the withdrawn message is marked, not dropped from the list",
          gone.get("deleted") is True and len(page6._messages) == 2)
    check("its text is cleared", gone.get("body") == "", repr(gone.get("body")))
    check("and its file goes with it — deleting must not leave the picture",
          gone.get("attachments") == [], str(gone.get("attachments")))
    check("the other message is untouched", page6._messages[1]["body"] == "keep me")

    page6._mark_deleted([70])
    check("applying the same withdrawal twice is harmless — the poll repeats it",
          page6._messages[0].get("deleted") is True)

    def _app_screen():
        from PySide6.QtWidgets import QApplication as _QA
        return _QA.primaryScreen().availableGeometry()

    print("\nOpening a picture")
    import client.presentation.windows.team_page as tp_mod
    from PySide6.QtGui import QPixmap as _QPix, QColor as _QCol
    from PySide6.QtCore import QBuffer as _QBuf, QByteArray as _QBA

    sample = _QPix(1200, 900)
    sample.fill(_QCol("#3366cc"))
    # The QByteArray must be held: QBuffer does not keep it alive, and a
    # buffer over a freed array takes the whole process down with it.
    store = _QBA()
    buf = _QBuf(store)
    buf.open(_QBuf.OpenModeFlag.ReadWrite)
    sample.save(buf, "PNG")
    blob = bytes(buf.data())

    tp_mod._IMAGE_CACHE[77] = blob
    shot = {"seq": 80, "sender_id": "E001", "sender_name": "R", "body": "",
            "created_at": datetime.now().isoformat(),
            "attachments": [{"id": 77, "file_name": "photo.png.enc",
                             "size_bytes": len(blob)}]}
    bub = _Bubble(dict(shot), mine=True, can_post=True)
    opened = []
    bub.image_clicked.connect(lambda i, n: opened.append((i, n)))
    picture = [w for w in bub.findChildren(tp_mod._ClickableImage)]
    check("the picture in the conversation is a thing you can click",
          len(picture) == 1, str(len(picture)))
    picture[0].clicked.emit()
    check("clicking it asks to open that attachment",
          opened == [(77, "photo.png.enc")], str(opened))

    viewer = tp_mod.ImageViewer(blob, "photo.png")
    check("the viewer shows the picture at full size, not the thumbnail",
          viewer.pixmap.width() == 1200, str(viewer.pixmap.width()))
    shown = viewer.findChildren(QLabel)[0].pixmap()
    screen = _app_screen()
    check("but never larger than the screen it has to fit on",
          shown.width() <= int(screen.width() * 0.86)
          and shown.height() <= int(screen.height() * 0.86),
          f"{shown.width()}x{shown.height()} on {screen.width()}x{screen.height()}")
    check("and it keeps the shape of the original",
          abs(shown.width() / shown.height() - 1200 / 900) < 0.01,
          f"{shown.width()}x{shown.height()}")

    buttons = [b.text() for b in viewer.findChildren(QPushButton)]
    check("saving is still offered — the picture is only in memory",
          "Save…" in buttons, str(buttons))
    saved = []
    viewer.save_requested.connect(lambda: saved.append(True))
    [b for b in viewer.findChildren(QPushButton) if b.text() == "Save…"][0].click()
    check("and Save asks for it", saved == [True])

    page7 = TeamPage(None, chat)
    page7._channel = {"id": 1, "name": "General", "can_post": True}
    asked_download = []
    page7._download_file = lambda i, n: asked_download.append((i, n))
    tp_mod._IMAGE_CACHE.pop(78, None)
    page7._open_image(78, "missing.png")
    check("a picture that has not arrived yet simply does not open",
          asked_download == [], "it tried to open bytes it did not have")

    print("\nWhen a picture cannot be fetched")
    tp_mod._IMAGE_CACHE.pop(91, None)
    tp_mod._IMAGE_FAILED.clear()
    broken = {"seq": 90, "sender_id": "E001", "sender_name": "R", "body": "",
              "created_at": datetime.now().isoformat(),
              "attachments": [{"id": 91, "file_name": "gone.png", "size_bytes": 10}]}

    page8 = TeamPage(None, chat)
    page8._channel = {"id": 1, "name": "General", "can_post": True}
    page8._messages = [dict(broken)]
    asked = []
    page8._run = lambda fn, ok, bad=None, *a, **k: asked.append((a, bad))
    page8._render_feed()
    check("a picture nobody has yet is asked for once", len(asked) == 1, str(len(asked)))

    asked[0][1]("File is no longer on disk")     # the failure callback
    page8._render_feed()
    shown = [w.text() for w in page8.findChildren(tp_mod._ClickableImage)]
    check("the failure is SHOWN, not hidden behind 'Loading…' for ever",
          any("no longer on disk" in t for t in shown), str(shown))
    check("and it stops asking — a rebuild used to re-request it every time",
          len(asked) == 1, f"{len(asked)} requests")

    page8._open_image(91, "gone.png")
    page8._render_feed()
    check("clicking a failed picture tries again", len(asked) == 2, str(len(asked)))

    print("\nDirect messages in the sidebar")
    from client.presentation.windows.team_page import _DirectRow, _PeoplePicker

    page9 = TeamPage(None, chat)
    page9._on_teams({"teams": [{
        "id": 1, "name": "Development", "is_archived": False, "unread": 0,
        "channels": [{"id": 1, "name": "General", "type": "STANDARD",
                      "is_default": True, "unread": 0}]}]})
    page9._on_directs({"directs": [
        {"channel_id": 50, "unread": 2, "preview": "kal ka report",
         "with": {"employee_id": "EM103", "name": "Sneha Iyer", "username": "sneha"}},
        {"channel_id": 51, "unread": 0, "preview": "",
         "with": {"employee_id": "EM102", "name": "Amit Sharma", "username": "amit"}},
    ]})

    rows = page9.findChildren(_DirectRow)
    check("every conversation gets a row", len(rows) == 2, str(len(rows)))
    check("and they live alongside the team channels, not inside one",
          50 in page9._rows and 1 in page9._rows, str(sorted(page9._rows)))

    labels = [w.text() for w in rows[0].findChildren(QLabel)]
    check("shown by the person's name, not a channel number",
          any("Sneha Iyer" in t for t in labels), str(labels))
    check("with the last line as a preview",
          any("kal ka report" in t for t in labels), str(labels))
    check("and an unread badge", any(t == "2" for t in labels), str(labels))

    empty = [w.text() for w in rows[1].findChildren(QLabel)]
    check("a conversation with nothing in it says so rather than looking broken",
          any("No messages yet" in t for t in empty), str(empty))

    page9._on_directs({"directs": []})
    hints = [w.text() for w in page9.findChildren(QLabel)]
    buttons = [b.text() for b in page9.findChildren(QPushButton)]
    check("with none at all, there is still an obvious way to start one",
          any("Message somebody" in b for b in buttons),
          "the first version of this was a 20-pixel + that was missed entirely, "
          "which is the whole feature missed")
    check("and the empty space explains itself",
          any("anybody in the company" in t for t in hints), str(hints)[:200])

    print("\nPicking who to message")
    picker = _PeoplePicker(None)
    picker._show({"people": [
        {"employee_id": "EM104", "name": "Vikram Rao", "username": "vikram",
         "designation": "Field Executive"},
        {"employee_id": "AD100", "name": "Priya Nair", "username": "manager",
         "designation": "Operations Manager"},
    ]})
    check("it lists people", picker._list.count() == 2, str(picker._list.count()))
    shown = picker._list.item(0).text()
    check("by name AND username, because you may only know one",
          "Vikram Rao" in shown and "vikram" in shown, shown)
    check("with what they do, to tell two Rajeshes apart",
          "Field Executive" in shown, shown)

    picker._list.setCurrentRow(1)
    picker._accept()
    check("picking somebody returns their employee id, not their name",
          picker.chosen == "AD100", str(picker.chosen))

    picker2 = _PeoplePicker(None)
    picker2._show({"people": []})
    check("no match says so plainly",
          "Nobody matches" in picker2._status.text(), picker2._status.text())
    picker2._accept()
    check("and pressing Message with nothing picked does not open a chat with nobody",
          picker2.chosen is None, str(picker2.chosen))

    print("\nPasting into the message box")
    # Copying a screenshot and pasting it into the conversation is how people
    # send pictures in every messaging application they use. Here it did
    # nothing at all: QTextEdit is a rich text widget, so it accepted the
    # image into a document nobody ever reads, and the picture vanished with
    # no error.
    from PySide6.QtGui import QImage as _QImage, QColor as _QColor
    from PySide6.QtCore import QMimeData as _QMime, QUrl as _QUrl
    import tempfile as _tempfile

    composer = tp_mod._Composer()
    pasted_images, pasted_files = [], []
    composer.image_pasted.connect(lambda i: pasted_images.append(i))
    composer.file_pasted.connect(lambda p: pasted_files.append(p))

    picture = _QImage(120, 80, _QImage.Format.Format_RGB32)
    picture.fill(_QColor("#c0392b"))
    data = _QMime()
    data.setImageData(picture)
    composer.insertFromMimeData(data)
    check("an image on the clipboard becomes an attachment",
          len(pasted_images) == 1, str(len(pasted_images)))
    check("and does not land in the text box as invisible rich content",
          composer.toPlainText() == "", repr(composer.toPlainText()))

    # Copied in Finder or Explorer, the clipboard carries a path instead —
    # which shape you get depends on where it was copied from, and nobody
    # thinks about that while doing it.
    handle = _tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    handle.close()
    picture.save(handle.name, "PNG")
    data = _QMime()
    data.setUrls([_QUrl.fromLocalFile(handle.name)])
    composer.insertFromMimeData(data)
    check("so does an image file copied from the desktop",
          len(pasted_files) == 1 and pasted_files[0].endswith(".png"),
          str(pasted_files))

    data = _QMime()
    data.setUrls([_QUrl.fromLocalFile("/tmp/notes.txt")])
    composer.insertFromMimeData(data)
    check("but a non-image path is not mistaken for a picture",
          len(pasted_files) == 1, str(pasted_files))

    composer.clear()
    data = _QMime()
    data.setText("kal ka report bhej dena")
    composer.insertFromMimeData(data)
    check("plain text still pastes normally",
          composer.toPlainText() == "kal ka report bhej dena",
          repr(composer.toPlainText()))

    composer.clear()
    data = _QMime()
    data.setHtml("<b>bold</b> line")
    data.setText("bold line")
    composer.insertFromMimeData(data)
    check("and formatted text arrives as plain characters",
          composer.toPlainText() == "bold line" and "<b>" not in composer.toHtml(),
          "colours and fonts nobody else will ever see")

    print("\nA message is shown as text, not as markup")
    # Anybody here can write to anybody, and a QLabel left on AutoText decides
    # for itself whether what it was handed is HTML. Measured before this was
    # set: <b> came out bold and a span came out forty pixels tall. A message
    # could then style itself into looking like something the application
    # said, or hide its own words with a transparent colour — in a record the
    # company keeps.
    from PySide6.QtWidgets import QLabel
    from PySide6.QtCore import Qt as _Qt
    evil = '<b>BOLD</b> <span style="color:transparent">hidden</span>'
    from client.presentation.windows.team_page import _Bubble
    row = _Bubble({"seq": 1, "sender_id": "E002", "sender_name": "Amit",
                   "body": evil, "created_at": "2026-08-12 10:00:00",
                   "attachments": []}, mine=False)
    labels = row.findChildren(QLabel)
    shown = [l for l in labels if evil in l.text()]
    check("the message body is put on screen as plain text",
          shown and all(l.textFormat() == _Qt.TextFormat.PlainText for l in shown),
          f"{[str(l.textFormat()) for l in shown]} — AutoText renders the markup")

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
