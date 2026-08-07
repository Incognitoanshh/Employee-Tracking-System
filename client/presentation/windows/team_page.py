"""
The Team page — channels down the left, the conversation in the middle, who
is in it down the right.

All networking belongs to ChatManager; this file draws what it is given and
hands back what the employee typed. Nothing here blocks on a socket, because
on the connection this runs over a blocking call is a frozen window.

THREE THINGS THIS DOES DIFFERENTLY FROM THE OBVIOUS VERSION

A message appears the moment it is typed, greyed, with a tick once the server
has it. On a link this slow, waiting for a round trip before showing your own
words makes the application feel broken even when it is working perfectly.

A message that will never send does not disappear. It stays, struck through,
with the reason. A message that silently vanishes is worse than one that
visibly failed — the employee assumes it went.

Presence is measured, not declared. "Idle 14 min" is real, taken from the
tracking that is already running. This is the one place where being a
monitoring product makes the chat better rather than worse.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

from client.presentation.theme import C, R, R_SM, button, input_style, scrollbar
from client.presentation.widgets.panel_widgets import PageHeader
from client.application.managers.chat_manager import ChatManager, MAX_BODY
from client.application.managers.session_manager import SessionManager
from client.core.time_ist import IST


# ──────────────────────────────────────────────────────────────────────────────
#  Small pieces
# ──────────────────────────────────────────────────────────────────────────────

def _parse(value) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    cleaned = raw.replace("Z", "+00:00")
    if "T" not in cleaned:
        cleaned = cleaned.replace(" ", "T", 1)
    if "." in cleaned:
        head, frac = cleaned.split(".", 1)
        offset = ""
        for marker in ("+", "-"):
            if marker in frac:
                i = frac.index(marker)
                frac, offset = frac[:i], frac[i:]
                break
        cleaned = f"{head}.{frac[:6]}{offset}"
    try:
        dt = datetime.fromisoformat(cleaned)
    except Exception:
        return None
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _clock(value) -> str:
    dt = _parse(value)
    return dt.astimezone(IST).strftime("%I:%M %p") if dt else ""


def _day_label(value) -> str:
    dt = _parse(value)
    if not dt:
        return ""
    local = dt.astimezone(IST).date()
    today = datetime.now(IST).date()
    delta = (today - local).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    return local.strftime("%d %b %Y")


PRESENCE = {
    "ACTIVE":      ("●", C.GREEN,      "Active"),
    "IDLE":        ("●", C.AMBER,      "Idle"),
    "OFFLINE":     ("●", C.TEXT_DIM,   "Offline"),
    "SHIFT_ENDED": ("●", C.PURPLE,     "Shift ended"),
}


class _Worker(QThread):
    """One background call. The panel never touches the network directly."""
    done = Signal(object)
    fail = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn, self._args, self._kwargs = fn, args, kwargs

    def run(self):
        try:
            self.done.emit(self._fn(*self._args, **self._kwargs))
        except Exception as error:
            self.fail.emit(str(error))


class _ChannelRow(QPushButton):
    """One channel in the left-hand list, with its unread badge."""

    def __init__(self, channel: dict, parent=None):
        super().__init__(parent)
        self.channel_id = channel["id"]
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)
        self.setObjectName("channelRow")

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 10, 0)
        row.setSpacing(8)

        icon = "📢" if channel.get("type") == "ANNOUNCEMENT" else (
            "🔒" if channel.get("is_private") else "#")
        self._icon = QLabel(icon)
        self._icon.setStyleSheet(f"color:{C.TEXT_DIM};font-size:12px;border:none;background:transparent;")
        self._name = QLabel(channel["name"])
        self._name.setStyleSheet(
            f"color:{C.TEXT_MUTED};font-size:13px;border:none;background:transparent;")

        self._badge = QLabel("")
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setFixedHeight(18)
        self._badge.setMinimumWidth(18)
        self._badge.hide()

        row.addWidget(self._icon)
        row.addWidget(self._name, 1)
        row.addWidget(self._badge)

        # Scoped by objectName. A bare `QPushButton {}` here would repaint
        # every button on the page, and a bare QFrame rule would reach the
        # labels inside it — QLabel is a QFrame.
        self.setStyleSheet(f"""
            QPushButton#channelRow {{
                background:transparent; border:none; border-radius:{R_SM}px;
                text-align:left;
            }}
            QPushButton#channelRow:hover {{ background:{C.CARD_HOVER}; }}
            QPushButton#channelRow:checked {{ background:{C.PRIMARY_DIM}; }}
        """)
        self.set_unread(int(channel.get("unread") or 0))

    def set_unread(self, count: int) -> None:
        if count > 0:
            self._badge.setText(str(count) if count < 100 else "99+")
            self._badge.setStyleSheet(
                f"background:{C.RED};color:#fff;font-size:10px;font-weight:800;"
                f"border-radius:9px;padding:0 5px;border:none;")
            self._badge.show()
            self._name.setStyleSheet(
                f"color:{C.TEXT};font-size:13px;font-weight:700;border:none;background:transparent;")
        else:
            self._badge.hide()
            self._name.setStyleSheet(
                f"color:{C.TEXT_MUTED};font-size:13px;border:none;background:transparent;")


class _Bubble(QFrame):
    """
    One message, and everything hanging off it.

    Actions are shown as small text buttons rather than on hover: a hover
    menu means the reply and pin controls are invisible until somebody
    happens to move the mouse over the right pixels, and on a list people
    scroll rather than point at, that is the same as not having them.
    """

    reply_requested = Signal(int)               # seq
    edit_requested = Signal(int, str)           # seq, current body
    pin_requested = Signal(int, bool)           # seq, pinned
    download_requested = Signal(int, str)       # attachment id, file name
    jump_requested = Signal(int)                # seq of the message replied to

    def __init__(self, message: dict, mine: bool, can_post: bool = True, parent=None):
        super().__init__(parent)
        self.seq = message.get("seq")
        self.client_msg_id = message.get("client_msg_id")
        self.setObjectName("bubble")

        mentions_me = bool(message.get("mentions_me"))
        # A message that names you gets a marked edge. Being named is the one
        # thing that has to survive being scrolled past, and colouring the
        # whole row would fight with every other state the bubble can be in.
        self.setStyleSheet(
            f"QFrame#bubble {{ background:{C.AMBER_BG if mentions_me else 'transparent'};"
            f"border:none;"
            f"border-left:{'3px solid ' + C.AMBER if mentions_me else '0px'};"
            f"border-radius:{R_SM if mentions_me else 0}px; }}")

        col = QVBoxLayout(self)
        col.setContentsMargins(8 if mentions_me else 0, 3, 6, 3)
        col.setSpacing(2)

        # ── header ──────────────────────────────────────────────────────
        head = QHBoxLayout()
        head.setSpacing(8)

        name = message.get("sender_name") or "Unknown"
        if message.get("former"):
            # The account is gone; the name it sent under is not. Three former
            # employees all reading as "Removed User" makes a conversation
            # impossible to attribute, which defeats keeping it.
            name = f"{name} (Former Employee)"

        who = QLabel(name)
        who.setStyleSheet(
            f"color:{C.PRIMARY if mine else C.TEXT};font-size:12px;"
            f"font-weight:700;border:none;background:transparent;")
        head.addWidget(who)

        when = QLabel(_clock(message.get("created_at")))
        when.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:11px;border:none;background:transparent;")
        head.addWidget(when)

        if message.get("edited"):
            tag = QLabel("edited")
            tag.setStyleSheet(
                f"color:{C.TEXT_DIM};font-size:10px;font-style:italic;"
                f"border:none;background:transparent;")
            head.addWidget(tag)

        if message.get("pinned"):
            pin = QLabel("📌")
            pin.setStyleSheet("font-size:10px;border:none;background:transparent;")
            head.addWidget(pin)

        if message.get("pending"):
            state = QLabel("sending…")
            state.setStyleSheet(
                f"color:{C.AMBER};font-size:10px;border:none;background:transparent;")
            head.addWidget(state)
        elif message.get("failed"):
            state = QLabel(f"not sent — {message.get('failed')}")
            state.setStyleSheet(
                f"color:{C.RED};font-size:10px;border:none;background:transparent;")
            head.addWidget(state)

        head.addStretch()

        # Actions are only offered where they can succeed. A button that
        # always fails teaches people the panel is unreliable.
        if self.seq and can_post and not message.get("pending"):
            head.addWidget(self._action("Reply",
                                        lambda: self.reply_requested.emit(self.seq)))
            if mine and _within_edit_window(message.get("created_at")):
                head.addWidget(self._action(
                    "Edit",
                    lambda: self.edit_requested.emit(self.seq, message.get("body") or "")))
            head.addWidget(self._action(
                "Unpin" if message.get("pinned") else "Pin",
                lambda: self.pin_requested.emit(self.seq, not message.get("pinned"))))
        col.addLayout(head)

        # ── what this replies to ────────────────────────────────────────
        reply = message.get("reply")
        if reply:
            quote = QPushButton(f"↩  {reply.get('sender_name')}: {reply.get('excerpt', '')}")
            quote.setCursor(Qt.CursorShape.PointingHandCursor)
            quote.setObjectName("replyQuote")
            quote.setStyleSheet(
                f"QPushButton#replyQuote {{ color:{C.TEXT_DIM};font-size:11px;"
                f"text-align:left;background:transparent;border:none;"
                f"border-left:2px solid {C.BORDER};padding:1px 0 1px 8px; }}"
                f"QPushButton#replyQuote:hover {{ color:{C.TEXT_MUTED}; }}")
            quote.clicked.connect(lambda: self.jump_requested.emit(reply["seq"]))
            col.addWidget(quote)

        # ── the message ─────────────────────────────────────────────────
        text = str(message.get("body") or "")
        if text:
            body = QLabel(text)
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            style = (f"color:{C.TEXT_MUTED};font-size:13px;border:none;"
                     f"background:transparent;padding:2px 0;")
            if message.get("failed"):
                style += "text-decoration:line-through;"
            elif message.get("pending"):
                style = style.replace(C.TEXT_MUTED, C.TEXT_DIM)
            body.setStyleSheet(style)
            col.addWidget(body)

        # ── files ───────────────────────────────────────────────────────
        for attachment in message.get("attachments") or []:
            col.addWidget(self._file_row(attachment))

        # A queued message knows only how many files it carries, not what the
        # server will call them — it has not been sent yet.
        queued_files = int(message.get("attachment_count") or 0)
        if queued_files and not message.get("attachments"):
            waiting = QLabel(f"📎 {queued_files} file(s) uploading…")
            waiting.setStyleSheet(
                f"color:{C.TEXT_DIM};font-size:11px;border:none;background:transparent;")
            col.addWidget(waiting)

    def _action(self, text: str, slot) -> QPushButton:
        button_ = QPushButton(text)
        button_.setCursor(Qt.CursorShape.PointingHandCursor)
        button_.setObjectName("bubbleAction")
        button_.setStyleSheet(
            f"QPushButton#bubbleAction {{ color:{C.TEXT_DIM};font-size:10px;"
            f"background:transparent;border:none;padding:0 3px; }}"
            f"QPushButton#bubbleAction:hover {{ color:{C.PRIMARY}; }}")
        button_.clicked.connect(slot)
        return button_

    def _file_row(self, attachment: dict) -> QWidget:
        size = int(attachment.get("size_bytes") or 0)
        if size >= 1024 * 1024:
            pretty = f"{size / (1024 * 1024):.1f} MB"
        elif size >= 1024:
            pretty = f"{size / 1024:.0f} KB"
        else:
            pretty = f"{size} B"

        row = QPushButton(f"📎  {attachment.get('file_name')}   ·   {pretty}")
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setObjectName("fileRow")
        row.setStyleSheet(
            f"QPushButton#fileRow {{ color:{C.BLUE};font-size:12px;text-align:left;"
            f"background:{C.BLUE_BG};border:1px solid {C.BORDER};"
            f"border-radius:{R_SM}px;padding:7px 10px; }}"
            f"QPushButton#fileRow:hover {{ border-color:{C.BLUE}; }}")
        row.clicked.connect(
            lambda: self.download_requested.emit(
                int(attachment["id"]), str(attachment.get("file_name") or "file")))
        return row


def _within_edit_window(created_at) -> bool:
    """Is this message still inside the five minutes the server allows?

    Checked here only so the Edit button is not offered on something that
    would be refused. The server decides — this is politeness, not security.
    """
    sent = _parse(created_at)
    if not sent:
        return False
    from datetime import timezone
    return (datetime.now(timezone.utc) - sent).total_seconds() < EDIT_WINDOW_SECONDS


EDIT_WINDOW_SECONDS = 5 * 60


class _Composer(QTextEdit):
    """Enter sends, Shift+Enter starts a new line."""
    send = Signal()
    # The word being typed after an "@", or empty when there is not one.
    # The page turns this into a member list; the composer only reports it,
    # so the two can be tested apart.
    mention_typed = Signal(str)
    navigate = Signal(int)     # -1 / +1 while the member list is open
    accept_mention = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
    def __init__(self, parent=None):
        super().__init__(parent)
        self._picking = False
        self.setPlaceholderText("Write a message…   @ to mention somebody")
        self.setFixedHeight(44)
        self.setStyleSheet(
            f"QTextEdit {{ background:{C.CARD}; color:{C.TEXT}; border:1px solid {C.BORDER};"
            f"border-radius:{R_SM}px; padding:10px 12px; font-size:13px; }}"
            + scrollbar(C.CARD))

    def set_picking(self, picking: bool) -> None:
        """True while the member list is open, so Enter picks instead of sends."""
        self._picking = picking

    def current_mention(self) -> str | None:
        """The partial handle being typed, or None."""
        text = self.toPlainText()[:self.textCursor().position()]
        at = text.rfind("@")
        if at < 0:
            return None
        # An "@" only starts a mention at the beginning of a word — otherwise
        # every email address would open the member list mid-sentence.
        if at > 0 and not text[at - 1].isspace():
            return None
        word = text[at + 1:]
        return None if (" " in word or "\n" in word) else word

    def replace_mention(self, handle: str) -> None:
        text = self.toPlainText()
        position = self.textCursor().position()
        at = text[:position].rfind("@")
        if at < 0:
            return
        self.setPlainText(text[:at] + "@" + handle + " " + text[position:])
        cursor = self.textCursor()
        cursor.setPosition(at + len(handle) + 2)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        # While the member list is open it owns the arrows and Enter —
        # otherwise picking a name from it would send the half-typed message.
        if self._picking:
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self.navigate.emit(1 if key == Qt.Key.Key_Down else -1)
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
                self.accept_mention.emit()
                return
            if key == Qt.Key.Key_Escape:
                self.mention_typed.emit("")
                self._picking = False
                return

        enter = key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if enter and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.send.emit()
            return
        super().keyPressEvent(event)
        handle = self.current_mention()
        self.mention_typed.emit(handle if handle is not None else "")


# ──────────────────────────────────────────────────────────────────────────────
#  The page
# ──────────────────────────────────────────────────────────────────────────────

class TeamPage(QWidget):
    """Channels, the conversation, and who is in it."""

    unread_changed = Signal(int)

    def __init__(self, panel, chat: ChatManager):
        super().__init__()
        self._panel = panel
        self._chat = chat
        self._workers: list[_Worker] = []

        self._teams: list[dict] = []
        self._rows: dict[int, _ChannelRow] = {}
        self._channel: dict | None = None
        # Which channel was ASKED for, set the moment it is clicked.
        # `_channel` only arrives with the history reply, so anything that
        # fires before that must not read it — see open_channel.
        self._channel_id: int | None = None
        self._messages: list[dict] = []
        self._oldest_seq: int | None = None
        self._searching = False
        self._reply_to: dict | None = None
        self._staged: list[dict] = []      # files uploaded, message not sent yet
        self._members_cache: list[dict] = []
        self._pinned: list[dict] = []

        self._build()

        self._chat.messages.connect(self._on_messages)
        self._chat.outbox_changed.connect(self._on_outbox_changed)
        self._chat.online_changed.connect(self._on_online_changed)

        # The member list is presence, so it goes stale on its own even when
        # nothing is said. Cheap query, and only while a channel is open.
        self._member_timer = QTimer(self)
        self._member_timer.setInterval(30_000)
        self._member_timer.timeout.connect(self._load_members)

        # The channel list has to refresh even when nothing is said.
        #
        # It used to update only when a message arrived or the page was
        # reopened. So an administrator taking somebody out of a channel had
        # no visible effect on that person's screen — the channel stayed in
        # their sidebar, and clicking it produced a failure they had no
        # explanation for. Quiet, and only in the situation where being
        # correct matters most.
        self._teams_timer = QTimer(self)
        self._teams_timer.setInterval(60_000)
        self._teams_timer.timeout.connect(self.refresh)

    # ── layout ──────────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addWidget(PageHeader("Team", "Channels you belong to"))

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._channel_pane())
        body.addWidget(self._chat_pane(), 1)
        body.addWidget(self._member_pane())
        root.addLayout(body, 1)

    def _channel_pane(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("channelPane")
        pane.setFixedWidth(232)
        pane.setStyleSheet(
            f"QFrame#channelPane {{ background:{C.CARD}; border:1px solid {C.BORDER};"
            f"border-radius:{R}px; }}")

        col = QVBoxLayout(pane)
        col.setContentsMargins(10, 12, 10, 12)
        col.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search messages…")
        self._search.setStyleSheet(input_style())
        self._search.returnPressed.connect(self._on_search)
        col.addWidget(self._search)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setStyleSheet("QScrollArea{background:transparent;border:none;}" + scrollbar(C.CARD))
        host = QWidget()
        host.setStyleSheet("background:transparent;")
        self._channel_list = QVBoxLayout(host)
        self._channel_list.setContentsMargins(0, 0, 0, 0)
        self._channel_list.setSpacing(2)
        self._channel_list.addStretch()
        area.setWidget(host)
        col.addWidget(area, 1)

        self._offline_note = QLabel("Offline — messages will send when reconnected")
        self._offline_note.setWordWrap(True)
        self._offline_note.setStyleSheet(
            f"color:{C.AMBER};font-size:11px;border:none;background:transparent;")
        self._offline_note.hide()
        col.addWidget(self._offline_note)
        return pane

    def _chat_pane(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("chatPane")
        pane.setStyleSheet(
            f"QFrame#chatPane {{ background:{C.CARD}; border:1px solid {C.BORDER};"
            f"border-radius:{R}px; }}")

        col = QVBoxLayout(pane)
        col.setContentsMargins(16, 14, 16, 14)
        col.setSpacing(10)

        head = QHBoxLayout()
        self._title = QLabel("Select a channel")
        self._title.setStyleSheet(
            f"color:{C.TEXT};font-size:15px;font-weight:700;border:none;background:transparent;")
        self._subtitle = QLabel("")
        self._subtitle.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:12px;border:none;background:transparent;")
        titles = QVBoxLayout()
        titles.setSpacing(1)
        titles.addWidget(self._title)
        titles.addWidget(self._subtitle)
        head.addLayout(titles)
        head.addStretch()
        self._back = QPushButton("← Back to channel")
        self._back.setStyleSheet(button("secondary"))
        self._back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back.clicked.connect(self._exit_search)
        self._back.hide()
        head.addWidget(self._back)
        col.addLayout(head)

        # A strip for the channel's pinned messages. Hidden when there are
        # none, so an empty shelf never takes space from the conversation.
        self._pinned_bar = QPushButton("")
        self._pinned_bar.setObjectName("pinnedBar")
        self._pinned_bar.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pinned_bar.setStyleSheet(
            f"QPushButton#pinnedBar {{ color:{C.AMBER};font-size:11px;text-align:left;"
            f"background:{C.AMBER_BG};border:1px solid {C.BORDER};"
            f"border-radius:{R_SM}px;padding:7px 10px; }}"
            f"QPushButton#pinnedBar:hover {{ border-color:{C.AMBER}; }}")
        self._pinned_bar.clicked.connect(self._show_pinned)
        self._pinned_bar.hide()
        col.addWidget(self._pinned_bar)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}" + scrollbar(C.CARD))
        host = QWidget()
        host.setStyleSheet("background:transparent;")
        self._feed = QVBoxLayout(host)
        self._feed.setContentsMargins(0, 0, 6, 0)
        self._feed.setSpacing(0)
        self._feed.addStretch()
        self._scroll.setWidget(host)
        col.addWidget(self._scroll, 1)

        # The member list that opens while an "@" is being typed. A plain
        # widget above the composer rather than a popup window: a popup on
        # macOS steals focus from the text box it is meant to be helping.
        self._mention_list = QListWidget()
        self._mention_list.setObjectName("mentionList")
        self._mention_list.setFixedHeight(120)
        self._mention_list.setStyleSheet(
            f"QListWidget#mentionList {{ background:{C.ELEVATED};color:{C.TEXT};"
            f"border:1px solid {C.BORDER};border-radius:{R_SM}px;font-size:12px; }}"
            f"QListWidget#mentionList::item {{ padding:5px 8px; }}"
            f"QListWidget#mentionList::item:selected {{ background:{C.PRIMARY_DIM}; }}")
        self._mention_list.itemClicked.connect(lambda _i: self._accept_mention())
        self._mention_list.hide()
        col.addWidget(self._mention_list)

        # What this message is answering, with a way out of it.
        self._reply_bar = QWidget()
        self._reply_bar.setStyleSheet("background:transparent;")
        reply_row = QHBoxLayout(self._reply_bar)
        reply_row.setContentsMargins(2, 0, 2, 0)
        reply_row.setSpacing(8)
        self._reply_label = QLabel("")
        self._reply_label.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:11px;border:none;background:transparent;"
            f"border-left:2px solid {C.PRIMARY};padding-left:8px;")
        cancel_reply = QPushButton("✕")
        cancel_reply.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_reply.setFixedWidth(22)
        cancel_reply.setStyleSheet(
            f"color:{C.TEXT_DIM};background:transparent;border:none;font-size:12px;")
        cancel_reply.clicked.connect(self._cancel_reply)
        reply_row.addWidget(self._reply_label, 1)
        reply_row.addWidget(cancel_reply)
        self._reply_bar.hide()
        col.addWidget(self._reply_bar)

        # Files already uploaded and waiting for the message that carries them.
        self._staged_bar = QWidget()
        self._staged_bar.setStyleSheet("background:transparent;")
        self._staged_row = QHBoxLayout(self._staged_bar)
        self._staged_row.setContentsMargins(2, 0, 2, 0)
        self._staged_row.setSpacing(6)
        self._staged_bar.hide()
        col.addWidget(self._staged_bar)

        self._composer_row = QWidget()
        self._composer_row.setStyleSheet("background:transparent;")
        row = QHBoxLayout(self._composer_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        attach = QPushButton("📎")
        attach.setCursor(Qt.CursorShape.PointingHandCursor)
        attach.setFixedSize(44, 44)
        attach.setToolTip("Attach a file")
        attach.setStyleSheet(
            f"QPushButton {{ background:{C.CARD};color:{C.TEXT_MUTED};"
            f"border:1px solid {C.BORDER};border-radius:{R_SM}px;font-size:15px; }}"
            f"QPushButton:hover {{ color:{C.TEXT};border-color:{C.PRIMARY}; }}")
        attach.clicked.connect(self._attach_file)
        row.addWidget(attach)

        self._composer = _Composer()
        self._composer.send.connect(self._on_send)
        self._composer.mention_typed.connect(self._on_mention_typed)
        self._composer.navigate.connect(self._navigate_mentions)
        self._composer.accept_mention.connect(self._accept_mention)
        send_btn = QPushButton("Send")
        send_btn.setStyleSheet(button("primary"))
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.setFixedHeight(44)
        send_btn.clicked.connect(self._on_send)
        row.addWidget(self._composer, 1)
        row.addWidget(send_btn)
        col.addWidget(self._composer_row)

        self._read_only = QLabel("")
        self._read_only.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:12px;border:none;background:transparent;"
            f"padding:12px 0;")
        self._read_only.hide()
        col.addWidget(self._read_only)
        return pane

    def _member_pane(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("memberPane")
        pane.setFixedWidth(212)
        pane.setStyleSheet(
            f"QFrame#memberPane {{ background:{C.CARD}; border:1px solid {C.BORDER};"
            f"border-radius:{R}px; }}")
        col = QVBoxLayout(pane)
        col.setContentsMargins(14, 14, 10, 14)
        col.setSpacing(8)

        heading = QLabel("Members")
        heading.setStyleSheet(
            f"color:{C.TEXT};font-size:13px;font-weight:700;border:none;background:transparent;")
        col.addWidget(heading)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setStyleSheet("QScrollArea{background:transparent;border:none;}" + scrollbar(C.CARD))
        host = QWidget()
        host.setStyleSheet("background:transparent;")
        self._members = QVBoxLayout(host)
        self._members.setContentsMargins(0, 0, 6, 0)
        self._members.setSpacing(6)
        self._members.addStretch()
        area.setWidget(host)
        col.addWidget(area, 1)

        note = QLabel("Team chat is kept in the company record.")
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:10px;border:none;background:transparent;")
        col.addWidget(note)
        return pane

    # ── loading ─────────────────────────────────────────────────────────

    def _run(self, fn, on_done, on_fail=None, *args, **kwargs):
        worker = _Worker(fn, *args, **kwargs)
        worker.done.connect(on_done)
        worker.fail.connect(on_fail or (lambda _: None))
        worker.finished.connect(lambda: self._workers.remove(worker)
                                if worker in self._workers else None)
        self._workers.append(worker)
        worker.start()

    def refresh(self):
        self._run(ChatManager.fetch_teams, self._on_teams, self._on_teams_failed)

    def _on_teams_failed(self, _error: str):
        if not self._teams:
            self._title.setText("Cannot reach the server")
            self._subtitle.setText("Channels will appear once the connection is back.")

    def _on_teams(self, payload):
        self._teams = payload.get("teams") or []
        self._render_channels()
        total = sum(int(t.get("unread") or 0) for t in self._teams)
        self.unread_changed.emit(total)

        if self._channel is None:
            first = next((c for t in self._teams for c in t["channels"]), None)
            if first:
                self.open_channel(first["id"])

    def _render_channels(self):
        while self._channel_list.count() > 1:
            item = self._channel_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._rows.clear()

        if not self._teams:
            empty = QLabel("You are not in any team yet.\nAn administrator adds you to one.")
            empty.setWordWrap(True)
            empty.setStyleSheet(
                f"color:{C.TEXT_DIM};font-size:12px;padding:14px 6px;"
                f"border:none;background:transparent;")
            self._channel_list.insertWidget(0, empty)
            return

        index = 0
        for team in self._teams:
            label = QLabel(team["name"].upper() +
                           ("  · archived" if team.get("is_archived") else ""))
            label.setStyleSheet(
                f"color:{C.TEXT_DIM};font-size:10px;font-weight:800;letter-spacing:1px;"
                f"padding:10px 10px 2px;border:none;background:transparent;")
            self._channel_list.insertWidget(index, label)
            index += 1
            for channel in team["channels"]:
                row = _ChannelRow(channel)
                row.clicked.connect(
                    lambda _checked=False, cid=channel["id"]: self.open_channel(cid))
                self._channel_list.insertWidget(index, row)
                self._rows[channel["id"]] = row
                row.setChecked(self._channel is not None
                               and self._channel["id"] == channel["id"])
                index += 1

    # ── one channel ─────────────────────────────────────────────────────

    def open_channel(self, channel_id: int):
        self._searching = False
        self._back.hide()
        for cid, row in self._rows.items():
            row.setChecked(cid == channel_id)
        self._chat.set_active_channel(channel_id)
        self._member_timer.start()
        self._cancel_reply()
        self._mention_list.hide()

        # BUG this fixes: the member and pin requests used to read
        # `self._channel`, which still held the PREVIOUS channel at this point
        # — it is only replaced when the history reply lands. On the very
        # first open it was None, so `_load_members` returned without asking
        # for anything and the member list stayed empty. Nothing errored. The
        # only visible symptom was that typing "@" offered nobody, because the
        # autocomplete is fed from that list, and the panel looked as though
        # mentions had simply not been built.
        #
        # It corrected itself after thirty seconds, when the refresh timer
        # called the same method with `self._channel` finally set, which made
        # it look intermittent rather than wrong.
        self._channel_id = channel_id
        self._members_cache = []
        self._run(ChatManager.fetch_history, self._on_history,
                  self._on_history_failed, channel_id)
        self._load_members(channel_id)
        self._load_pinned(channel_id)

    def _on_history_failed(self, _error: str):
        self._title.setText("Cannot load this channel")
        self._subtitle.setText("It will load when the connection is back.")

    def _on_history(self, payload):
        channel = payload.get("channel") or {}
        self._channel = channel
        self._messages = payload.get("messages") or []
        self._oldest_seq = self._messages[0]["seq"] if self._messages else None

        self._title.setText(channel.get("name", "—"))
        bits = [channel.get("team_name", "")]
        if channel.get("type") == "ANNOUNCEMENT":
            bits.append("announcements — only administrators post here")
        if channel.get("is_archived"):
            bits.append("archived — read only")
        self._subtitle.setText("  ·  ".join(b for b in bits if b))

        can_post = bool(channel.get("can_post"))
        self._composer_row.setVisible(can_post)
        self._read_only.setVisible(not can_post)
        if not can_post:
            self._read_only.setText(
                "This is an announcement channel — only administrators can post."
                if channel.get("type") == "ANNOUNCEMENT"
                else "This team is archived. You can read it, but not add to it.")

        self._render_feed()
        self._mark_read()

    def _render_feed(self):
        while self._feed.count() > 1:
            item = self._feed.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        me = SessionManager.employee_id
        last_day = None
        index = 0

        rows = list(self._messages)
        if self._channel and not self._searching:
            # Queued and failed messages belong at the end of the channel they
            # were typed into — they are part of the conversation as far as the
            # person who wrote them is concerned.
            for row in ChatManager.pending(self._channel["id"]):
                import json as _json
                try:
                    staged = _json.loads(row.get("attachment_ids") or "[]")
                except (TypeError, ValueError):
                    staged = []
                rows.append({
                    "sender_name": "You", "body": row["body"],
                    "created_at": row["created_at"], "pending": True,
                    "client_msg_id": row["client_msg_id"],
                    "reply_to": row.get("reply_to"),
                    "attachment_count": len(staged),
                })

        if not rows:
            empty = QLabel("No messages yet." if not self._searching else "Nothing found.")
            empty.setStyleSheet(
                f"color:{C.TEXT_DIM};font-size:13px;padding:24px 4px;"
                f"border:none;background:transparent;")
            self._feed.insertWidget(0, empty)
            return

        for message in rows:
            day = _day_label(message.get("created_at"))
            if day and day != last_day and not self._searching:
                divider = QLabel(day)
                divider.setAlignment(Qt.AlignmentFlag.AlignCenter)
                divider.setStyleSheet(
                    f"color:{C.TEXT_DIM};font-size:10px;font-weight:700;"
                    f"letter-spacing:1px;padding:12px 0 6px;"
                    f"border:none;background:transparent;")
                self._feed.insertWidget(index, divider)
                index += 1
                last_day = day
            bubble = _Bubble(message, mine=message.get("sender_id") == me,
                             can_post=bool(self._channel and self._channel.get("can_post"))
                             and not self._searching)
            bubble.reply_requested.connect(self._start_reply)
            bubble.edit_requested.connect(self._start_edit)
            bubble.pin_requested.connect(self._toggle_pin)
            bubble.download_requested.connect(self._download_file)
            bubble.jump_requested.connect(self._jump_to)
            self._feed.insertWidget(index, bubble)
            index += 1

        QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _mark_read(self):
        if not self._channel or not self._messages:
            return
        seq = max(int(m.get("seq") or 0) for m in self._messages)
        if seq:
            self._run(ChatManager.mark_read, lambda _=None: None,
                      None, self._channel["id"], seq)
            row = self._rows.get(self._channel["id"])
            if row:
                row.set_unread(0)
            self._recount()

    def _recount(self):
        for team in self._teams:
            for channel in team["channels"]:
                if self._channel and channel["id"] == self._channel["id"]:
                    channel["unread"] = 0
            team["unread"] = sum(int(c.get("unread") or 0) for c in team["channels"])
        self.unread_changed.emit(sum(int(t.get("unread") or 0) for t in self._teams))

    # ── members ─────────────────────────────────────────────────────────

    def _load_members(self, channel_id: int | None = None):
        channel_id = channel_id or self._channel_id
        if not channel_id:
            return
        self._run(ChatManager.fetch_members, self._on_members,
                  lambda error, cid=channel_id: self._maybe_lost_access(error, cid),
                  channel_id)

    def _maybe_lost_access(self, error: str, channel_id: int):
        """
        A 404 on a channel that was open means access has just been taken away.

        Without this the employee keeps looking at a conversation they can no
        longer reach: the messages already on screen stay there, and the only
        sign is that sending fails. Refreshing quietly would be worse — the
        channel would vanish from under them with no explanation.
        """
        if "404" not in str(error) and "not found" not in str(error).lower():
            return
        if channel_id != self._channel_id:
            return
        name = (self._channel or {}).get("name", "that channel")
        self._channel = None
        self._channel_id = None
        self._messages = []
        self._members_cache = []
        self._chat.set_active_channel(None)
        self._member_timer.stop()
        self.refresh()
        QMessageBox.information(
            self, "No longer available",
            f"You no longer have access to {name}. "
            f"An administrator may have changed who can see it.")

    def _on_members(self, payload):
        # Compared against what was asked for, not against the loaded channel:
        # on a slow link a reply for the channel just clicked can arrive
        # before its own history does.
        if payload.get("channel_id") != self._channel_id:
            return
        # Kept for the @ autocomplete, which must not make a request per
        # keystroke on a link this slow.
        self._members_cache = payload.get("members") or []
        while self._members.count() > 1:
            item = self._members.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for index, member in enumerate(payload.get("members") or []):
            dot, colour, label = PRESENCE.get(member.get("status", "OFFLINE"),
                                              PRESENCE["OFFLINE"])
            if member.get("status") == "IDLE" and member.get("idle_minutes") is not None:
                label = f"Idle {member['idle_minutes']} min"

            row = QWidget()
            row.setStyleSheet("background:transparent;")
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(8)

            bullet = QLabel(dot)
            bullet.setStyleSheet(
                f"color:{colour};font-size:14px;border:none;background:transparent;")
            names = QVBoxLayout()
            names.setSpacing(0)
            name = QLabel(member["name"] + (" (you)" if member.get("is_me") else ""))
            name.setStyleSheet(
                f"color:{C.TEXT};font-size:12px;border:none;background:transparent;")
            state = QLabel(label)
            state.setStyleSheet(
                f"color:{C.TEXT_DIM};font-size:10px;border:none;background:transparent;")
            names.addWidget(name)
            names.addWidget(state)
            line.addWidget(bullet)
            line.addLayout(names, 1)
            self._members.insertWidget(index, row)

    # ── sending ─────────────────────────────────────────────────────────

    def _on_send(self):
        if not self._channel or self._searching:
            return
        text = self._composer.toPlainText().strip()
        if not text and not self._staged:
            return
        if len(text) > MAX_BODY:
            QMessageBox.warning(self, "Too long",
                                f"A message can be {MAX_BODY} characters. "
                                f"This one is {len(text)}.")
            return
        try:
            self._chat.send(
                self._channel["id"], text,
                reply_to=self._reply_to["seq"] if self._reply_to else None,
                mentions=self._mentions_in(text),
                attachment_ids=[f["id"] for f in self._staged],
            )
        except ValueError as error:
            QMessageBox.warning(self, "Not sent", str(error))
            return
        self._composer.clear()
        self._cancel_reply()
        self._clear_staged()
        self._render_feed()

    def _on_outbox_changed(self):
        if self._channel and not self._searching:
            self._render_feed()

    def _on_online_changed(self, online: bool):
        self._offline_note.setVisible(not online)

    # ── incoming ────────────────────────────────────────────────────────

    def _on_messages(self, arrived: list):
        if not arrived:
            return
        touched = False
        for message in arrived:
            channel_id = message.get("channel_id")
            if self._channel and channel_id == self._channel["id"] and not self._searching:
                if not any(m.get("seq") == message.get("seq") for m in self._messages):
                    self._messages.append(message)
                    touched = True
                continue
            # Somewhere else — bump its badge.
            row = self._rows.get(channel_id)
            for team in self._teams:
                for channel in team["channels"]:
                    if channel["id"] == channel_id:
                        channel["unread"] = int(channel.get("unread") or 0) + 1
                        if row:
                            row.set_unread(channel["unread"])
        if touched:
            self._render_feed()
            self._mark_read()
        self._recount()

    # ── search ──────────────────────────────────────────────────────────

    def _on_search(self):
        text = self._search.text().strip()
        if len(text) < 2:
            return
        self._run(ChatManager.search, self._on_search_done, self._on_search_failed, text)

    def _on_search_failed(self, _error: str):
        QMessageBox.information(self, "Search", "Could not search right now.")

    def _on_search_done(self, payload):
        self._searching = True
        self._back.show()
        self._composer_row.hide()
        self._read_only.hide()
        results = payload.get("results") or []
        self._title.setText(f"{payload.get('total', 0)} result(s)")
        self._subtitle.setText(f"for “{payload.get('query', '')}”")
        self._messages = [
            {**row, "sender_name": f"{row.get('sender_name')} · "
                                   f"{row.get('team_name')} / {row.get('channel_name')}"}
            for row in results
        ]
        self._render_feed()

    def _exit_search(self):
        self._search.clear()
        self._searching = False
        self._back.hide()
        if self._channel:
            self.open_channel(self._channel["id"])
        else:
            self.refresh()

    # ── replying ────────────────────────────────────────────────────────

    def _start_reply(self, seq: int):
        target = next((m for m in self._messages if m.get("seq") == seq), None)
        if not target:
            return
        self._reply_to = target
        excerpt = str(target.get("body") or "")[:70]
        self._reply_label.setText(
            f"Replying to {target.get('sender_name')}: {excerpt}"
            + ("…" if len(str(target.get("body") or "")) > 70 else ""))
        self._reply_bar.show()
        self._composer.setFocus()

    def _cancel_reply(self):
        self._reply_to = None
        self._reply_bar.hide()

    def _jump_to(self, seq: int):
        """Scroll to the message a reply points at, and mark it briefly."""
        for i in range(self._feed.count()):
            widget = self._feed.itemAt(i).widget()
            if isinstance(widget, _Bubble) and widget.seq == seq:
                self._scroll.ensureWidgetVisible(widget, 0, 60)
                return
        # Not on screen — it is further back than the page that is loaded.
        QMessageBox.information(
            self, "Not loaded",
            "That message is further back than what is loaded. "
            "Scroll up to load more of the conversation.")

    # ── editing ─────────────────────────────────────────────────────────

    def _start_edit(self, seq: int, current: str):
        text, ok = QInputDialog.getMultiLineText(self, "Edit message", "Message", current)
        if not ok:
            return
        text = text.strip()
        if not text or text == current:
            return
        self._run(ChatManager.edit, lambda _p: self._reload_channel(),
                  self._on_edit_failed, seq, text)

    def _on_edit_failed(self, error: str):
        # The server's own wording. "Messages can only be edited within 5
        # minutes of sending" tells somebody what happened; "Edit failed"
        # leaves them retrying.
        QMessageBox.information(self, "Not edited", error)

    # ── pinning ─────────────────────────────────────────────────────────

    def _toggle_pin(self, seq: int, pinned: bool):
        self._run(ChatManager.set_pinned, lambda _p: self._reload_channel(),
                  lambda error: QMessageBox.information(self, "Not pinned", error),
                  seq, pinned)

    def _load_pinned(self, channel_id: int | None = None):
        channel_id = channel_id or self._channel_id
        if not channel_id:
            return
        self._run(ChatManager.fetch_pinned, self._on_pinned, None, channel_id)

    def _on_pinned(self, payload):
        if payload.get("channel_id") != self._channel_id:
            return
        self._pinned = payload.get("messages") or []
        if self._pinned:
            first = str(self._pinned[0].get("body") or "")[:60] or "(file)"
            more = f"   +{len(self._pinned) - 1} more" if len(self._pinned) > 1 else ""
            self._pinned_bar.setText(f"📌  {first}{more}")
            self._pinned_bar.show()
        else:
            self._pinned_bar.hide()

    def _show_pinned(self):
        if not self._pinned:
            return
        self._searching = True          # reuses the "not the live channel" mode
        self._back.show()
        self._composer_row.hide()
        self._reply_bar.hide()
        self._read_only.hide()
        self._title.setText(f"{len(self._pinned)} pinned")
        self._subtitle.setText(self._channel.get("name", ""))
        self._messages = list(self._pinned)
        self._render_feed()

    # ── files ───────────────────────────────────────────────────────────

    def _attach_file(self):
        if not self._channel:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Attach a file")
        if not path:
            return
        self._staged_note(f"Uploading {path.split('/')[-1]}…")
        self._run(ChatManager.upload_attachment, self._on_uploaded,
                  self._on_upload_failed, self._channel["id"], path)

    def _on_uploaded(self, attachment: dict):
        self._staged.append(attachment)
        self._render_staged()

    def _on_upload_failed(self, error: str):
        self._render_staged()
        QMessageBox.warning(self, "Upload failed", error)

    def _staged_note(self, text: str):
        self._clear_staged_widgets()
        label = QLabel(text)
        label.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:11px;border:none;background:transparent;")
        self._staged_row.addWidget(label)
        self._staged_bar.show()

    def _render_staged(self):
        self._clear_staged_widgets()
        if not self._staged:
            self._staged_bar.hide()
            return
        for attachment in self._staged:
            chip = QPushButton(f"📎 {attachment['file_name']}   ✕")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip("Remove")
            chip.setStyleSheet(
                f"color:{C.BLUE};background:{C.BLUE_BG};border:1px solid {C.BORDER};"
                f"border-radius:{R_SM}px;padding:4px 8px;font-size:11px;")
            chip.clicked.connect(
                lambda _c=False, a=attachment: self._unstage(a))
            self._staged_row.addWidget(chip)
        self._staged_row.addStretch()
        self._staged_bar.show()

    def _unstage(self, attachment: dict):
        # The file stays on the server, unclaimed. The nightly purge removes
        # anything never attached to a message — there is nothing to undo here
        # and no request worth making on a slow link.
        self._staged = [a for a in self._staged if a["id"] != attachment["id"]]
        self._render_staged()

    def _clear_staged(self):
        self._staged = []
        self._clear_staged_widgets()
        self._staged_bar.hide()

    def _clear_staged_widgets(self):
        while self._staged_row.count():
            item = self._staged_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _download_file(self, attachment_id: int, file_name: str):
        path, _ = QFileDialog.getSaveFileName(self, "Save file", file_name)
        if not path:
            return
        self._run(ChatManager.download_attachment,
                  lambda saved: QMessageBox.information(
                      self, "Saved", f"Saved to:\n{saved}"),
                  lambda error: QMessageBox.warning(self, "Download failed", error),
                  attachment_id, path)

    # ── mentions ────────────────────────────────────────────────────────

    def _on_mention_typed(self, partial: str):
        if not partial and partial != "":
            return
        handle = self._composer.current_mention()
        if handle is None:
            self._mention_list.hide()
            self._composer.set_picking(False)
            return

        needle = handle.lower()
        matches = [m for m in self._members_cache
                   if not m.get("is_me")
                   and (needle in (m.get("name") or "").lower()
                        or needle in (m.get("username") or "").lower()
                        or needle in m["employee_id"].lower())][:8]
        self._mention_list.clear()
        for member in matches:
            item = QListWidgetItem(
                f"{member['name']}   ·   {member.get('username') or member['employee_id']}")
            item.setData(Qt.ItemDataRole.UserRole, member)
            self._mention_list.addItem(item)

        if matches:
            self._mention_list.setCurrentRow(0)
            self._mention_list.show()
            self._composer.set_picking(True)
        else:
            self._mention_list.hide()
            self._composer.set_picking(False)

    def _navigate_mentions(self, delta: int):
        count = self._mention_list.count()
        if not count:
            return
        row = (self._mention_list.currentRow() + delta) % count
        self._mention_list.setCurrentRow(row)

    def _accept_mention(self):
        item = self._mention_list.currentItem()
        if not item:
            return
        member = item.data(Qt.ItemDataRole.UserRole)
        # The handle written into the text is the USERNAME, never the display
        # name: a name with a space in it cannot be recovered from the text
        # afterwards, and the server parses handles out of the body as a
        # fallback for people who type them from memory.
        self._composer.replace_mention(
            member.get("username") or member["employee_id"])
        self._mention_list.hide()
        self._composer.set_picking(False)
        self._composer.setFocus()

    def _mentions_in(self, text: str) -> list:
        """Employee ids for the handles present in the text.

        Sent alongside the body so the server does not have to guess. It parses
        the text as well, but only this can resolve somebody whose username
        differs from what is displayed.
        """
        lowered = text.lower()
        return [m["employee_id"] for m in self._members_cache
                if m.get("username") and f"@{m['username'].lower()}" in lowered]

    def _reload_channel(self):
        if self._channel:
            self.open_channel(self._channel["id"])

    # ── housekeeping ────────────────────────────────────────────────────

    def hideEvent(self, event):
        # Nothing is being watched, so stop asking every three seconds.
        self._chat.set_active_channel(None)
        self._member_timer.stop()
        self._teams_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self._channel:
            self._chat.set_active_channel(self._channel["id"])
            self._member_timer.start()
        self._teams_timer.start()
        self.refresh()
