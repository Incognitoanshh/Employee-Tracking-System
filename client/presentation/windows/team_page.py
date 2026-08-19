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
from time import monotonic

from PySide6.QtCore import Qt, QTimer, Signal, QThread, QSize
from PySide6.QtGui import QAction, QCursor, QImage, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPushButton, QScrollArea, QStackedWidget, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

from client.presentation.widgets import icons as _icons
from client.presentation.theme import (
    C, R, R_SM, Radius, Space, Type, button, dot_style, input_style, scrollbar)
from client.presentation.widgets.avatar import Avatar
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


# How long after asking for the bottom we keep following it down. Long enough
# for a layout pass and a picture to decode, short enough that it never fights
# somebody who has started scrolling back.
FOLLOW_SECONDS = 0.6


# Colour and word per state. THE DOT USED TO BE IN HERE as a "●" character
# and is not any more: the member panel draws it as a small round label, so
# its size is the panel's decision rather than the text font's.
PRESENCE = {
    "ACTIVE":      (C.GREEN,    "Active"),
    "IDLE":        (C.AMBER,    "Idle"),
    "OFFLINE":     (C.TEXT_DIM, "Offline"),
    "SHIFT_ENDED": (C.PURPLE,   "Shift ended"),
}



# Pictures already fetched and decrypted, keyed by attachment id.
#
# IN MEMORY, never on disk. The server keeps these encrypted so that a copy of
# every photograph anybody sent does not sit around in the clear; a cache
# folder of decrypted ones would hand that back. The cost is re-fetching after
# a restart, which is a second on a conversation nobody scrolls twice.
#
# The feed is rebuilt from scratch on every new message, so without a cache a
# channel with four images would re-download all four every few seconds.
_IMAGE_CACHE: dict[int, bytes] = {}
_IMAGE_CACHE_MAX = 40

# Pictures that could not be fetched, and why.
#
# WITHOUT THIS a failing image is indistinguishable from a slow one. The feed
# is rebuilt on every new message, and each rebuild made a fresh label saying
# "Loading image…" and asked again — so a picture whose file the server cannot
# find sat on "Loading…" for ever while quietly re-requesting itself for the
# life of the session. It looked like the image system was stuck. It was not;
# it was retrying, forever, and never saying so.
#
# Found when a restarted demo server was pointed at the wrong upload folder,
# which is exactly the shape of a real accident on a server.
_IMAGE_FAILED: dict[int, str] = {}

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
THUMB_WIDTH = 340


def _looks_like_image(attachment: dict) -> bool:
    name = str(attachment.get("file_name") or "").lower()
    if name.endswith(".enc"):          # the stored name, not what it is
        name = name[:-4]
    return name.endswith(IMAGE_SUFFIXES)


def _cache_image(attachment_id: int, blob: bytes) -> None:
    if len(_IMAGE_CACHE) >= _IMAGE_CACHE_MAX:
        # Oldest first. Nothing clever — a chat holds a handful of these.
        _IMAGE_CACHE.pop(next(iter(_IMAGE_CACHE)), None)
    _IMAGE_CACHE[attachment_id] = blob


def _thumbnail(blob: bytes) -> QPixmap | None:
    pixmap = QPixmap()
    if not pixmap.loadFromData(blob):
        return None
    if pixmap.width() > THUMB_WIDTH:
        pixmap = pixmap.scaledToWidth(
            THUMB_WIDTH, Qt.TransformationMode.SmoothTransformation)
    return pixmap


class _ClickableImage(QLabel):
    """The picture in the conversation. Clicking it opens it properly."""

    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ImageViewer(QDialog):
    """One picture, as large as the screen allows.

    IN MEMORY, NOT VIA A TEMPORARY FILE. Handing the picture to the system
    viewer would mean writing the decrypted bytes to disk, and the whole
    reason chat attachments are stored encrypted is that a copy of every
    photograph anybody sent must not sit around in the clear. A temporary file
    is exactly that copy — and one nothing reliably deletes. So the image is
    shown here, from the bytes already in memory.

    Save is still offered, because wanting to keep a picture is legitimate.
    The difference is that it then goes where the employee chose, knowingly.
    """

    save_requested = Signal()

    def __init__(self, blob: bytes, file_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(file_name or "Image")
        self.setStyleSheet(f"QDialog {{ background:{C.BG}; }}")

        pixmap = QPixmap()
        pixmap.loadFromData(blob)
        self.pixmap = pixmap

        # Fit the screen rather than the picture: a phone photograph is far
        # larger than any monitor, and a window bigger than the screen has its
        # close button somewhere nobody can reach.
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None and not pixmap.isNull():
            room = screen.availableGeometry()
            limit_w, limit_h = int(room.width() * 0.85), int(room.height() * 0.85)
            if pixmap.width() > limit_w or pixmap.height() > limit_h:
                pixmap = pixmap.scaled(
                    limit_w, limit_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)

        col = QVBoxLayout(self)
        col.setContentsMargins(12, 12, 12, 12)
        col.setSpacing(10)

        view = QLabel()
        view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        view.setStyleSheet("background:transparent;border:none;")
        if pixmap.isNull():
            view.setText("This picture could not be displayed.")
            view.setStyleSheet(f"color:{C.TEXT_DIM};background:transparent;border:none;")
        else:
            view.setPixmap(pixmap)
        col.addWidget(view, 1)

        row = QHBoxLayout()
        row.addStretch()
        save = QPushButton("Save…")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.setStyleSheet(button("primary"))
        save.clicked.connect(self.save_requested.emit)
        row.addWidget(save)
        close = QPushButton("Close")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(button("ghost"))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        col.addLayout(row)

    def keyPressEvent(self, event: QKeyEvent):
        # Escape closes it. Every picture viewer anybody has used does this,
        # and a modal window that ignores Escape feels stuck.
        if event.key() == Qt.Key.Key_Escape:
            self.accept()
            return
        super().keyPressEvent(event)


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

        icon = "" if channel.get("type") == "ANNOUNCEMENT" else (
"" if channel.get("is_private") else "#")
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
        self._unread = 0
        self._selected = False

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
            QPushButton#channelRow:checked {{ background:{C.SELECTED_BG}; }}
        """)
        self.set_unread(int(channel.get("unread") or 0))

    def set_selected(self, selected: bool) -> None:
        """Recolour the labels inside the row.

        The row's own :checked rule paints the background, but the name and
        the icon are separate QLabels with their own colour — so a selected
        row went dark-blue while its text stayed dark, and the channel you
        were looking at became the one you could not read.
        """
        self._selected = selected
        self._icon.setStyleSheet(
            f"color:{C.SELECTED_TEXT if selected else C.TEXT_DIM};font-size:12px;"
            f"border:none;background:transparent;")
        self.set_unread(self._unread)

    def set_unread(self, count: int) -> None:
        self._unread = count
        if getattr(self, "_selected", False):
            self._name.setStyleSheet(
                f"color:{C.SELECTED_TEXT};font-size:13px;font-weight:600;"
                f"border:none;background:transparent;")
            self._badge.setVisible(count > 0)
            if count > 0:
                self._badge.setText(str(count) if count < 100 else "99+")
                self._badge.setStyleSheet(
                    f"background:{C.SELECTED_TEXT};color:{C.SELECTED_BG};font-size:12px;"
                    f"font-weight:800;border-radius:12px;padding:0 5px;border:none;")
            return
        if count > 0:
            self._badge.setText(str(count) if count < 100 else "99+")
            self._badge.setStyleSheet(
                f"background:{C.RED};color:#fff;font-size:12px;font-weight:800;"
                f"border-radius:12px;padding:0 5px;border:none;")
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
    delete_requested = Signal(int)              # seq
    download_requested = Signal(int, str)       # attachment id, file name
    jump_requested = Signal(int)                # seq of the message replied to
    image_wanted = Signal(int, object)          # attachment id, the label to fill
    image_clicked = Signal(int, str)            # attachment id, file name
    react_requested = Signal(int, str)          # seq, emoji
    thread_requested = Signal(int)              # seq of the root

    def __init__(self, message: dict, mine: bool, can_post: bool = True, parent=None):
        super().__init__(parent)
        self.seq = message.get("seq")
        self.client_msg_id = message.get("client_msg_id")
        self._pending_images: list = []
        self.deleted = bool(message.get("deleted"))
        self.setObjectName("bubble")

        # A withdrawn message keeps no highlight. Leaving the amber edge on a
        # tombstone would draw the eye to the one line with nothing in it.
        mentions_me = bool(message.get("mentions_me")) and not self.deleted
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

        # THE SENDER'S FACE, before their name.
        #
        # This is the place the request was actually about: a conversation
        # reads as people when you can see who is speaking, and as a log file
        # when you cannot. Initials until somebody uploads a photo, and
        # nothing is asked of the server for a face already drawn once — see
        # widgets/avatar.py.
        #
        # Not for a former employee: their account is gone, so there is no
        # photo to ask for and the request would only earn a 404 per message.
        if not message.get("former"):
            face = Avatar(22)
            face.show_person(message.get("sender_id"), message.get("sender_name") or "")
            head.addWidget(face)

        who = QLabel(name)
        who.setStyleSheet(
            f"color:{C.PRIMARY if mine else C.TEXT};font-size:12px;"
            f"font-weight:700;border:none;background:transparent;")
        head.addWidget(who)

        when = QLabel(_clock(message.get("created_at")))
        when.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:12px;border:none;background:transparent;")
        head.addWidget(when)

        if message.get("edited"):
            tag = QLabel("edited")
            tag.setStyleSheet(
                f"color:{C.TEXT_DIM};font-size:12px;font-style:italic;"
                f"border:none;background:transparent;")
            head.addWidget(tag)

        if message.get("pinned") and not self.deleted:
            pin = QLabel("")
            pin.setStyleSheet("font-size:12px;border:none;background:transparent;")
            head.addWidget(pin)

        if message.get("pending"):
            state = QLabel("sending…")
            state.setStyleSheet(
                f"color:{C.AMBER};font-size:12px;border:none;background:transparent;")
            head.addWidget(state)
        elif message.get("failed"):
            state = QLabel(f"not sent — {message.get('failed')}")
            state.setStyleSheet(
                f"color:{C.RED};font-size:12px;border:none;background:transparent;")
            head.addWidget(state)

        head.addStretch()

        # Actions are only offered where they can succeed. A button that
        # always fails teaches people the panel is unreliable.
        # Nothing is offered on a withdrawn message: there is nothing to
        # reply to, quote, pin, or take back a second time.
        if self.seq and can_post and not message.get("pending") and not self.deleted:
            head.addWidget(self._action("React", self._offer_reactions))
            head.addWidget(self._action("Reply",
                                        lambda: self.reply_requested.emit(self.seq)))
            if mine and _within_edit_window(message.get("created_at")):
                head.addWidget(self._action(
                    "Edit",
                    lambda: self.edit_requested.emit(self.seq, message.get("body") or "")))
            head.addWidget(self._action(
                "Unpin" if message.get("pinned") else "Pin",
                lambda: self.pin_requested.emit(self.seq, not message.get("pinned"))))
            # Delete lives behind "⋯" rather than beside Pin, and only on your
            # own messages. A destructive action sitting in the same row as
            # three harmless ones is a mis-click waiting to happen, and one
            # that cannot be undone.
            if mine:
                head.addWidget(self._more_button())
        col.addLayout(head)

        # ── what this replies to ────────────────────────────────────────
        reply = message.get("reply")
        if reply:
            quote = QPushButton(
                f"  {reply.get('sender_name')}: {reply.get('excerpt', '')}")
            quote.setIcon(_icons.icon("corner-up-left", 13, C.TEXT_DIM))
            quote.setIconSize(QSize(13, 13))
            quote.setCursor(Qt.CursorShape.PointingHandCursor)
            quote.setObjectName("replyQuote")
            quote.setStyleSheet(
                f"QPushButton#replyQuote {{ color:{C.TEXT_DIM};font-size:12px;"
                f"text-align:left;background:transparent;border:none;"
                f"border-left:2px solid {C.BORDER};padding:1px 0 1px 8px; }}"
                f"QPushButton#replyQuote:hover {{ color:{C.TEXT_MUTED}; }}")
            quote.clicked.connect(lambda: self.jump_requested.emit(reply["seq"]))
            col.addWidget(quote)

        # ── the message ─────────────────────────────────────────────────
        if self.deleted:
            # The row stays. Closing the gap would rewrite the conversation
            # around it — replies would answer nothing, and the exchange would
            # read as if it never happened.
            stone = QLabel("This message was deleted")
            stone.setStyleSheet(
                f"color:{C.TEXT_DIM};font-size:12px;font-style:italic;"
                f"border:none;background:transparent;padding:2px 0;")
            col.addWidget(stone)
            return

        text = str(message.get("body") or "")
        if text:
            body = QLabel(text)
            # PLAIN TEXT, EXPLICITLY.
            #
            # A QLabel left on AutoText decides for itself whether what it was
            # given is HTML, and anybody here can write to anybody. Measured:
            # <b> came out bold and <span style="font-size:40px"> came out
            # forty pixels tall, so a message could style itself into looking
            # like something the application said, hide its own words with a
            # transparent colour, or break the layout of everyone who opened
            # the channel. What is kept in the company record has to be the
            # characters somebody actually typed.
            #
            # Remote images were tested and are NOT fetched by QLabel, so this
            # was never a way to reach the network — it is about the text
            # being the text.
            body.setTextFormat(Qt.TextFormat.PlainText)
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
            if _looks_like_image(attachment):
                col.addWidget(self._image(attachment))
            col.addWidget(self._file_row(attachment))

        # A queued message knows only how many files it carries, not what the
        # server will call them — it has not been sent yet.
        queued_files = int(message.get("attachment_count") or 0)
        if queued_files and not message.get("attachments"):
            waiting = QLabel(f"{queued_files} file(s) uploading…")
            waiting.setStyleSheet(
                f"color:{C.TEXT_DIM};font-size:12px;border:none;background:transparent;")
            col.addWidget(waiting)

        # ── reactions ───────────────────────────────────────────────────
        # Under the message, and only when there are any: an empty strip on
        # every message would take a line of height from the conversation to
        # say nothing.
        reactions = message.get("reactions") or []
        if reactions and not self.deleted:
            col.addWidget(self._reaction_row(reactions))

        # ── the thread hanging off this message ─────────────────────────
        #
        # Shown only when there IS one. A "0 replies" affordance on every
        # message is a column of noise, and it invites a thread where a
        # reply would do.
        replies = int(message.get("reply_count") or 0)
        if replies and self.seq:
            open_thread = QPushButton(
                f"{replies} repl{'y' if replies == 1 else 'ies'}")
            open_thread.setCursor(Qt.CursorShape.PointingHandCursor)
            open_thread.setStyleSheet(
                f"QPushButton{{color:{C.PRIMARY};font-size:{Type.MICRO}px;"
                f"background:transparent;border:none;text-align:left;"
                f"padding:2px 0;}}"
                f"QPushButton:hover{{text-decoration:underline;}}")
            open_thread.clicked.connect(
                lambda _checked=False: self.thread_requested.emit(self.seq))
            col.addWidget(open_thread, 0, Qt.AlignmentFlag.AlignLeft)

    def _more_button(self) -> QPushButton:
        # A DRAWN ELLIPSIS. "⋯" is one character whose spacing is the font's
        # choice, so it sat off-centre in a 24px button and vanished entirely
        # in fonts without it.
        button_ = self._action("", self._more_menu)
        button_.setIcon(_icons.icon("more-horizontal", 14, C.TEXT_DIM))
        button_.setIconSize(QSize(14, 14))
        button_.setToolTip("More")
        return button_

    def _more_menu(self):
        menu = self.build_more_menu()
        origin = self.sender()
        where = (origin.mapToGlobal(origin.rect().bottomLeft())
                 if isinstance(origin, QPushButton) else QCursor.pos())
        menu.exec(where)

    def build_more_menu(self) -> QMenu:
        """The menu on its own, so it can be checked without a modal loop.

        exec() blocks until something is clicked, so a test that called the
        handler would simply hang. Building and showing are separate for that
        reason — see build_attach_menu, which was split for the same one.
        """
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background:{C.CARD}; color:{C.TEXT}; border:1px solid {C.BORDER};"
            f"border-radius:{R_SM}px; padding:4px; }}"
            f"QMenu::item {{ padding:7px 18px 7px 12px; border-radius:{R_SM}px; }}"
            f"QMenu::item:selected {{ background:{C.SELECTED_BG}; color:{C.SELECTED_TEXT}; }}")
        remove = QAction("Delete message", menu)
        # The icon lives on the action rather than in its text, where it used
        # to be a 🗑 typed into the label.
        remove.setIcon(_icons.icon("trash-2", 16, C.RED))
        remove.triggered.connect(lambda: self.delete_requested.emit(self.seq))
        menu.addAction(remove)
        return menu

    def _action(self, text: str, slot) -> QPushButton:
        button_ = QPushButton(text)
        button_.setCursor(Qt.CursorShape.PointingHandCursor)
        button_.setObjectName("bubbleAction")
        button_.setStyleSheet(
            f"QPushButton#bubbleAction {{ color:{C.TEXT_DIM};font-size:12px;"
            f"background:transparent;border:none;padding:0 3px; }}"
            f"QPushButton#bubbleAction:hover {{ color:{C.PRIMARY}; }}")
        button_.clicked.connect(slot)
        return button_

    # The choices the server will accept. Fetched once per run and cached on
    # the class — hard-coding them here is how a client ends up offering an
    # emoji the server refuses, which reads as a broken button.
    REACTION_CHOICES: list = []

    def _offer_reactions(self):
        """A small menu under the message, not a full emoji keyboard.

        Six choices cover what reactions are for — acknowledging, agreeing,
        thanking, laughing. A picker over the whole Unicode table turns a
        one-tap gesture into a search, and fills the row under a message with
        pictures nobody can scan.
        """
        menu = QMenu(self)
        # A fallback for the moment before the server's list arrives. These
        # are content, not interface icons — see the marker.
        fallback = ["👍", "❤️", "😂", "🎉", "👀", "✅"]  # reaction content
        for emoji in (self.REACTION_CHOICES or fallback):
            action = menu.addAction(emoji)
            action.triggered.connect(
                lambda _checked=False, e=emoji: self.react_requested.emit(self.seq, e))
        menu.exec(self.mapToGlobal(self.rect().topRight()))

    def _reaction_row(self, reactions: list) -> QWidget:
        """The chips under a message: one per emoji, with its count.

        PRESSED WHEN IT IS YOURS. Without that the row says how many people
        reacted but not whether you are one of them, so the only way to find
        out is to press it and watch the number move — which either adds a
        reaction you did not want or removes one you did.
        """
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(6)

        for entry in reactions:
            emoji = str(entry.get("emoji", ""))
            count = int(entry.get("count", 0) or 0)
            mine = bool(entry.get("mine"))
            if not emoji or count <= 0:
                continue
            chip = QPushButton(f"{emoji}  {count}")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip("You reacted — press to remove" if mine
                            else "Press to react")
            chip.setStyleSheet(
                f"QPushButton{{background:{C.ACTIVE if mine else C.ELEVATED};"
                f"color:{C.TEXT if mine else C.TEXT_MUTED};"
                f"border:1px solid {C.PRIMARY if mine else C.BORDER};"
                f"border-radius:{Radius.PILL}px;padding:2px 10px;"
                f"font-size:{Type.MICRO}px;}}"
                f"QPushButton:hover{{border-color:{C.PRIMARY};}}")
            chip.clicked.connect(
                lambda _checked=False, e=emoji: self.react_requested.emit(self.seq, e))
            row.addWidget(chip)

        row.addStretch()
        return holder

    def _image(self, attachment: dict) -> QWidget:
        """A picture shown in the conversation, not a file to go and fetch.

        Cached ones appear immediately. The rest say so and fill in when they
        arrive — the alternative is a blank gap for a second on this
        connection, which reads as something having gone wrong.
        """
        holder = _ClickableImage()
        holder.setObjectName("chatImage")
        holder.setStyleSheet(
            f"QLabel#chatImage {{ background:{C.ELEVATED};border:1px solid {C.BORDER};"
            f"border-radius:{R_SM}px;padding:6px;color:{C.TEXT_DIM};font-size:12px; }}")
        holder.setCursor(Qt.CursorShape.PointingHandCursor)
        holder.setToolTip("Click to open")

        attachment_id = int(attachment["id"])
        holder.clicked.connect(
            lambda: self.image_clicked.emit(
                attachment_id, str(attachment.get("file_name") or "image")))
        cached = _IMAGE_CACHE.get(attachment_id)
        if cached is not None:
            pixmap = _thumbnail(cached)
            if pixmap is not None:
                holder.setPixmap(pixmap)
                return holder

        problem = _IMAGE_FAILED.get(attachment_id)
        if problem:
            # Say what went wrong, and stop asking. Clicking tries again —
            # a server that has come back should not need a restart here.
            holder.setText(f"{problem}\nClick to try again")
            holder.setToolTip("Click to try again")
            return holder

        holder.setText("Loading image…")
        # QUEUED, not emitted.
        #
        # BUG this fixes: this used to emit image_wanted right here — inside
        # the constructor — while the page connects to that signal only after
        # the bubble has been built. Every request fired into nothing, so no
        # picture ever loaded and no error ever appeared either: just
        # "Loading image…" for good.
        self._pending_images.append((attachment_id, holder))
        return holder

    def request_images(self) -> None:
        """Ask for the pictures. Called once the page has connected."""
        for attachment_id, holder in self._pending_images:
            self.image_wanted.emit(attachment_id, holder)
        self._pending_images = []

    def _file_row(self, attachment: dict) -> QWidget:
        size = int(attachment.get("size_bytes") or 0)
        if size >= 1024 * 1024:
            pretty = f"{size / (1024 * 1024):.1f} MB"
        elif size >= 1024:
            pretty = f"{size / 1024:.0f} KB"
        else:
            pretty = f"{size} B"

        row = QPushButton(f"{attachment.get('file_name')}   ·   {pretty}")
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


class _DirectRow(QPushButton):
    """One conversation in the left-hand list.

    Deliberately built like _ChannelRow rather than sharing it: a channel row
    shows a # and a name, this shows a person and the last thing they said.
    Forcing one widget to be both would mean a pile of branches inside it.
    """

    def __init__(self, direct: dict, parent=None):
        super().__init__(parent)
        self.channel_id = direct["channel_id"]
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(44)
        self.setObjectName("directRow")

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 10, 0)
        row.setSpacing(8)

        person = direct.get("with") or {}
        # THE FACE, here too. This row drew its own initials circle and never
        # asked for a photograph, so the conversation list showed a blue "A"
        # for somebody whose picture was on every message inside it. The
        # shared widget does both — the photo when there is one, the initials
        # until then — and one place decides how a person looks.
        face = Avatar(24)
        face.show_person(person.get("employee_id"),
                         person.get("name") or person.get("username") or "")
        row.addWidget(face)

        column = QVBoxLayout()
        column.setSpacing(0)
        self._name = QLabel(person.get("name") or person.get("username") or "Unknown")
        self._name.setStyleSheet(
            f"color:{C.TEXT_MUTED};font-size:13px;border:none;background:transparent;")
        self._preview = QLabel(str(direct.get("preview") or "No messages yet"))
        self._preview.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:12px;border:none;background:transparent;")
        column.addWidget(self._name)
        column.addWidget(self._preview)
        row.addLayout(column, 1)

        self._badge = QLabel("")
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setFixedHeight(18)
        self._badge.setMinimumWidth(18)
        self._badge.hide()
        row.addWidget(self._badge)

        self._selected = False
        self.setStyleSheet(f"""
            QPushButton#directRow {{
                background:transparent; border:none; border-radius:{R_SM}px;
                text-align:left;
            }}
            QPushButton#directRow:hover {{ background:{C.CARD_HOVER}; }}
            QPushButton#directRow:checked {{ background:{C.SELECTED_BG}; }}
        """)
        self.set_unread(int(direct.get("unread") or 0))

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        colour = C.SELECTED_TEXT if selected else C.TEXT_MUTED
        dim = C.SELECTED_TEXT if selected else C.TEXT_DIM
        self._name.setStyleSheet(
            f"color:{colour};font-size:13px;border:none;background:transparent;")
        self._preview.setStyleSheet(
            f"color:{dim};font-size:12px;border:none;background:transparent;")

    def set_unread(self, count: int) -> None:
        if count > 0:
            self._badge.setText(str(count if count < 100 else "99+"))
            self._badge.setStyleSheet(
                # Red, matching the menu badge: one colour means "unread"
                # everywhere, and it is not the colour of the selected row.
                f"background:{C.RED};color:#ffffff;border-radius:12px;"
                f"font-size:12px;font-weight:700;padding:0 5px;border:none;")
            self._badge.show()
        else:
            self._badge.hide()


class _PeoplePicker(QDialog):
    """Search for somebody, pick them, start a conversation.

    Everybody is listed before anything is typed. A search box that shows
    nothing until you guess a name is unusable in a company where you may not
    know how somebody spells theirs — and the whole point of this feature is
    reaching a person you do not already share a team with.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.chosen: str | None = None
        self._workers: list = []
        self.setWindowTitle("Message somebody")
        self.setMinimumSize(380, 440)
        self.setStyleSheet(f"QDialog {{ background:{C.BG}; }}")

        column = QVBoxLayout(self)
        column.setContentsMargins(16, 16, 16, 16)
        column.setSpacing(10)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by name, username or role…")
        self._search.setFixedHeight(36)
        self._search.setStyleSheet(input_style())
        self._search.textChanged.connect(self._typed)
        column.addWidget(self._search)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background:{C.CARD};border:1px solid {C.BORDER};"
            f"border-radius:{R_SM}px;color:{C.TEXT};font-size:13px;padding:4px; }}"
            f"QListWidget::item {{ padding:8px 10px;border-radius:{R_SM}px; }}"
            f"QListWidget::item:selected {{ background:{C.SELECTED_BG};"
            f"color:{C.SELECTED_TEXT}; }}" + scrollbar(C.CARD))
        self._list.itemDoubleClicked.connect(lambda _i: self._accept())
        column.addWidget(self._list, 1)

        self._status = QLabel("Loading…")
        self._status.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:12px;background:transparent;")
        column.addWidget(self._status)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(button("ghost"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        start = QPushButton("Message")
        start.setCursor(Qt.CursorShape.PointingHandCursor)
        start.setStyleSheet(button("primary"))
        start.clicked.connect(self._accept)
        buttons.addWidget(start)
        column.addLayout(buttons)

        # Typing races the network on a link this slow, so the search is
        # debounced rather than fired per keystroke.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self._search_now)

        self._search_now()

    def _typed(self, _text):
        self._debounce.start()

    def _search_now(self):
        worker = _Worker(ChatManager.search_people, self._search.text().strip())
        worker.done.connect(self._show)
        worker.fail.connect(lambda error: self._status.setText(str(error)))
        worker.finished.connect(lambda: self._workers.remove(worker)
                                if worker in self._workers else None)
        self._workers.append(worker)
        worker.start()

    def _show(self, payload):
        people = payload.get("people") or []
        self._list.clear()
        for person in people:
            label = person.get("name") or person.get("username")
            extra = person.get("designation") or person.get("role") or ""
            item = QListWidgetItem(f"{label}   ·   {person.get('username')}"
                                   + (f"   ·   {extra}" if extra else ""))
            item.setData(Qt.ItemDataRole.UserRole, person.get("employee_id"))
            self._list.addItem(item)
        self._status.setText(
            "Nobody matches that." if not people else f"{len(people)} people")
        if people:
            self._list.setCurrentRow(0)

    def _accept(self):
        item = self._list.currentItem()
        if item is None:
            self._status.setText("Pick somebody first.")
            return
        self.chosen = item.data(Qt.ItemDataRole.UserRole)
        self.accept()


class _Composer(QTextEdit):
    """Enter sends, Shift+Enter starts a new line."""
    send = Signal()
    # The word being typed after an "@", or empty when there is not one.
    # The page turns this into a member list; the composer only reports it,
    # so the two can be tested apart.
    mention_typed = Signal(str)
    navigate = Signal(int)     # -1 / +1 while the member list is open
    accept_mention = Signal()
    # A picture pasted straight into the box — the clipboard carries either
    # the image itself or a path to one, depending on where it came from.
    image_pasted = Signal(object)   # QImage
    file_pasted = Signal(str)       # a path on this machine

    def __init__(self, parent=None):
        # ONE CONSTRUCTOR. There were two, identical name, the first holding
        # only a super() call — dead from the moment Python read the second.
        super().__init__(parent)
        self._picking = False
        self.setPlaceholderText("Write a message…   @ to mention somebody")
        self.setFixedHeight(38)
        # FLAT, BECAUSE THE BAR AROUND IT IS THE FRAME. A bordered box inside
        # a bordered bar is two rectangles saying the same thing, and it is
        # what made the old composer read as three loose controls rather than
        # one place to write.
        self.setStyleSheet(
            f"QTextEdit {{ background:transparent; color:{C.TEXT}; border:none;"
            f"padding:8px 4px; font-size:13px; }}"
            + scrollbar(C.CARD))
        # NO GUTTER INSIDE THE BAR. A QTextEdit reserves room for its vertical
        # scrollbar, which showed as a pale sliver wedged against the Send
        # button — visible in the render. The view still follows the cursor,
        # so a message longer than the box remains reachable by typing and by
        # the arrow keys, which is how every chat composer behaves.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

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

    def insertFromMimeData(self, source) -> None:
        """Paste. A picture becomes an attachment; anything else is text.

        WHAT WAS MISSING: pasting an image did nothing at all. QTextEdit
        happily accepts one — it is a rich text widget — and drops it into a
        document nobody ever reads, so the picture vanished with no error.
        Copying a screenshot and pasting it into the conversation is how
        people send pictures in every messaging application they use, and it
        silently failed here.

        Two shapes arrive on a clipboard: the image itself (a screenshot, or
        a copy out of a browser) and a file URL (copied in Finder or
        Explorer). Both are handled, because which one you get depends on
        where it was copied from and nobody thinks about that.
        """
        if source.hasImage():
            image = source.imageData()
            if image is not None and not QImage(image).isNull():
                self.image_pasted.emit(QImage(image))
                return

        for url in (source.urls() if source.hasUrls() else []):
            local = url.toLocalFile()
            if local and local.lower().endswith(IMAGE_SUFFIXES):
                self.file_pasted.emit(local)
                return

        # Plain text, deliberately. Pasting formatted text into a box whose
        # contents are sent as plain characters would show colours and fonts
        # that nobody else will ever see.
        self.insertPlainText(source.text())

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
    """The chat page.

    SURVIVING A THEME SWITCH. Changing the theme rebuilds every page — see
    theme.py for why — and a rebuilt chat page starts with no channel open
    and an empty composer. Reported from use: a conversation was in progress,
    the theme was switched, and the chat closed with a half-typed message in
    it.

    `snapshot()` and `restore()` are what the panel carries across that
    rebuild. They deliberately hold only what a person would notice losing:
    which channel was open, and what they had typed.
    """
    """Channels, the conversation, and who is in it."""

    unread_changed = Signal(int)

    def __init__(self, panel, chat: ChatManager):
        super().__init__()
        self._panel = panel
        self._chat = chat
        self._workers: list[_Worker] = []

        self._teams: list[dict] = []
        # One-to-one conversations, alongside the teams rather than inside
        # them: a direct message belongs to no team.
        self._directs: list[dict] = []
        self._rows: dict = {}
        self._channel: dict | None = None
        # Which channel was ASKED for, set the moment it is clicked.
        # `_channel` only arrives with the history reply, so anything that
        # fires before that must not read it — see open_channel.
        self._channel_id: int | None = None
        self._messages: list[dict] = []
        # Which thread is open, if any. None means the members panel is up.
        self._thread_root: int | None = None
        self._oldest_seq: int | None = None
        self._searching = False
        self._reply_to: dict | None = None
        self._staged: list[dict] = []      # files uploaded, message not sent yet
        self._members_cache: list[dict] = []
        self._pinned: list[dict] = []
        # Requests in flight, so a rebuild mid-download does not start a
        # second fetch for the same picture.
        self._loading_images: set[int] = set()

        self._build()

        self._chat.messages.connect(self._on_messages)
        # Withdrawals arrive on their own signal — they are removals, not
        # arrivals, and they come from outside the cursor.
        self._chat.deletions.connect(self._mark_deleted)
        self._chat.outbox_changed.connect(self._on_outbox_changed)
        self._chat.online_changed.connect(self._on_online_changed)

        # The member list is presence, so it goes stale on its own even when
        # nothing is said. Cheap query, and only while a channel is open.
        self._member_timer = QTimer(self)
        self._member_timer.setInterval(30_000)
        self._member_timer.timeout.connect(self._load_members)

        # ── typing ──────────────────────────────────────────────────────
        #
        # THREE SECONDS, AND ONLY WHILE A CHANNEL IS OPEN. Typing is worth
        # knowing for about as long as it takes somebody to finish a
        # sentence; polling faster spends requests on a line nobody has sent,
        # and slower makes the indicator arrive after the message does.
        self._typing_timer = QTimer(self)
        self._typing_timer.setInterval(3_000)
        self._typing_timer.timeout.connect(self._load_typing)

        # A ping is sent at most this often however fast somebody types. One
        # request per keystroke is a hundred writes a minute per person, for
        # a message that does not exist yet.
        self._typing_ping_gap = 2.5
        self._typing_last_ping = 0.0

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
        # STARTED HERE, NOT ONLY IN showEvent.
        #
        # The unread badge on the menu comes from this page. It used to poll
        # only once the page had been SHOWN, so a panel where nobody had ever
        # opened the chat never asked — and the count sat at zero all day
        # while messages arrived. Seen live: the employee panel showed a
        # count because its Team page had been opened once; the admin
        # console's My Chat, never opened in that run, showed nothing.
        #
        # A page nobody has looked at still owes its badge an answer.
        self._teams_timer.start()
        QTimer.singleShot(1500, self.refresh)
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
        # THE THREAD TAKES THE MEMBER PANEL'S PLACE, it does not add a third
        # column. Three panels side by side leaves the conversation itself
        # about four hundred pixels wide, which is the thing everybody is
        # actually reading. The members are still one click away.
        self._side = QStackedWidget()
        self._side.setFixedWidth(212)
        self._side.addWidget(self._member_pane())     # index 0
        self._side.addWidget(self._thread_pane())     # index 1
        body.addWidget(self._side)
        root.addLayout(body, 1)

    def _thread_pane(self) -> QWidget:
        """A root message and its replies, with a box that replies to it."""
        pane = QFrame()
        pane.setObjectName("threadPane")
        pane.setStyleSheet(
            f"QFrame#threadPane {{ background:{C.CARD}; border:1px solid {C.BORDER};"
            f"border-radius:{R}px; }}")
        col = QVBoxLayout(pane)
        col.setContentsMargins(14, 14, 10, 14)
        col.setSpacing(8)

        head = QHBoxLayout()
        heading = QLabel("Thread")
        heading.setStyleSheet(
            f"color:{C.TEXT};font-size:13px;font-weight:700;"
            f"border:none;background:transparent;")
        head.addWidget(heading)
        head.addStretch()
        close = QPushButton()
        close.setIcon(_icons.icon("x", 14, C.TEXT_DIM))
        close.setIconSize(QSize(14, 14))
        close.setFixedSize(24, 24)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip("Back to members")
        close.setStyleSheet("background:transparent;border:none;")
        close.clicked.connect(self.close_thread)
        head.addWidget(close)
        col.addLayout(head)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setStyleSheet("QScrollArea{background:transparent;border:none;}"
                           + scrollbar(C.CARD))
        host = QWidget()
        host.setStyleSheet("background:transparent;")
        self._thread_body = QVBoxLayout(host)
        self._thread_body.setContentsMargins(0, 0, 6, 0)
        self._thread_body.setSpacing(8)
        self._thread_body.addStretch()
        area.setWidget(host)
        self._thread_area = area
        col.addWidget(area, 1)

        self._thread_input = QLineEdit()
        self._thread_input.setPlaceholderText("Reply in thread…")
        self._thread_input.returnPressed.connect(self._send_thread_reply)
        col.addWidget(self._thread_input)
        return pane

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
            f"color:{C.AMBER};font-size:12px;border:none;background:transparent;")
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
            f"color:{C.TEXT};font-size:16px;font-weight:700;border:none;background:transparent;")
        self._subtitle = QLabel("")
        self._subtitle.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:12px;border:none;background:transparent;")
        titles = QVBoxLayout()
        titles.setSpacing(1)
        titles.addWidget(self._title)
        titles.addWidget(self._subtitle)
        head.addLayout(titles)
        head.addStretch()
        self._back = QPushButton("  Back to channel")
        self._back.setIcon(_icons.icon("chevron-left", 15, C.TEXT))
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
            f"QPushButton#pinnedBar {{ color:{C.AMBER};font-size:12px;text-align:left;"
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

        # Follow the conversation down as the feed grows — see _scroll_to_bottom
        # for why asking for the bottom once is not enough.
        self._scroll.verticalScrollBar().rangeChanged.connect(self._range_changed)

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
            f"color:{C.TEXT_DIM};font-size:12px;border:none;background:transparent;"
            f"border-left:2px solid {C.PRIMARY};padding-left:8px;")
        # The ✕ that drops the reply you are composing. It was that glyph as
        # text; with the emoji gone this was a 22px invisible button, and the
        # only way out of a reply was to send it.
        cancel_reply = QPushButton()
        cancel_reply.setIcon(_icons.icon("x", 14, C.TEXT_DIM))
        cancel_reply.setIconSize(QSize(14, 14))
        cancel_reply.setToolTip("Cancel this reply")
        cancel_reply.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_reply.setFixedWidth(24)
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

        # ── the composer ────────────────────────────────────────────────
        #
        # ONE BAR, NOT THREE CONTROLS. This was a paperclip button, a
        # bordered text box and a Send button sitting side by side with a gap
        # between each — three rectangles of three different heights, which is
        # what "composer saada hai" meant. Slack and Teams both draw a single
        # rounded container and put the controls inside it, and the reason is
        # that writing a message is ONE act.
        #
        # Every button in here is the same 34px square, the same radius, the
        # same Lucide stroke and the same hover, so the row reads as a
        # toolbar. Send keeps its label because it is the one control whose
        # meaning must not depend on recognising an icon.
        self._composer_row = QFrame()
        self._composer_row.setObjectName("composerBar")
        self._composer_row.setStyleSheet(
            f"QFrame#composerBar {{ background:{C.CARD};"
            f"border:1px solid {C.BORDER};border-radius:{R_SM + 2}px; }}"
            f"QFrame#composerBar:focus-within {{ border-color:{C.PRIMARY}; }}")
        row = QHBoxLayout(self._composer_row)
        row.setContentsMargins(6, 6, 6, 6)
        row.setSpacing(6)
        # Bottom-aligned: when the text runs to a second line the buttons stay
        # level with the last line, which is where the eye is.
        row.setAlignment(Qt.AlignmentFlag.AlignBottom)

        def tool(icon_name: str, tip: str, on_click) -> QPushButton:
            """One square in the toolbar. All of them, so none can drift."""
            btn = QPushButton()
            btn.setIcon(_icons.icon(icon_name, 17, C.TEXT_MUTED))
            btn.setIconSize(QSize(17, 17))
            btn.setFixedSize(34, 34)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tip)
            btn.setStyleSheet(
                f"QPushButton {{ background:transparent;border:none;"
                f"border-radius:{R_SM}px; }}"
                f"QPushButton:hover {{ background:{C.ELEVATED}; }}"
                f"QPushButton:pressed {{ background:{C.CARD_HOVER}; }}")
            btn.clicked.connect(on_click)
            return btn

        row.addWidget(tool("paperclip", "Attach a file", self._attach_menu))
        row.addWidget(tool("smile", "Insert an emoji", self._insert_emoji))
        row.addWidget(tool("at-sign", "Mention somebody", self._insert_mention))

        self._composer = _Composer()
        self._composer.send.connect(self._on_send)
        self._composer.textChanged.connect(self._on_typed)
        self._composer.mention_typed.connect(self._on_mention_typed)
        self._composer.navigate.connect(self._navigate_mentions)
        self._composer.accept_mention.connect(self._accept_mention)
        self._composer.image_pasted.connect(self._paste_image)
        self._composer.file_pasted.connect(self._paste_file)
        row.addWidget(self._composer, 1)

        send_btn = QPushButton("Send")
        send_btn.setIcon(_icons.icon("send", 15, C.ON_ACCENT))
        send_btn.setIconSize(QSize(15, 15))
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.setFixedHeight(34)
        send_btn.setStyleSheet(
            f"QPushButton {{ background:{C.PRIMARY};color:{C.ON_ACCENT};border:none;"
            f"border-radius:{R_SM}px;padding:0 14px;font-size:13px;font-weight:600; }}"
            f"QPushButton:hover {{ background:{C.PRIMARY_DIM}; }}")
        send_btn.clicked.connect(self._on_send)
        row.addWidget(send_btn)

        # WHERE THE INDICATOR LIVES: between the conversation and the box,
        # which is where the next message will appear. Hidden when nobody is
        # typing, so it takes no height rather than leaving a blank line.
        self._typing_label = QLabel("")
        self._typing_label.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:{Type.MICRO}px;"
            f"background:transparent;border:none;padding:0 4px 2px 4px;")
        self._typing_label.hide()
        col.addWidget(self._typing_label)

        col.addWidget(self._composer_row)

        self._read_only = QLabel("")
        self._read_only.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:12px;border:none;background:transparent;"
            f"padding:12px 0;")
        self._read_only.hide()
        col.addWidget(self._read_only)
        return pane

    def _member_pane(self) -> QWidget:
        """Who is in this conversation, and whether they are at their desk.

        WHAT WAS WRONG WITH IT. A heading, then rows of a bullet character and
        two lines of 12px text at six pixels apart, then a sentence — three
        different rhythms in one narrow column, and no avatars, so the panel
        did not look like it belonged to the same product as the message list
        beside it, which is full of them.

        WHAT CHANGED, AND ONLY THIS. The heading is the same small-caps label
        the rest of the product uses for a section and now carries the count.
        Each person is a row of avatar, name and state on the panel's own
        grid, with the status shown as a drawn dot rather than a "●" from the
        text font. Rules separate the three parts. Nothing about who is
        fetched, or when, moved.
        """
        pane = QFrame()
        pane.setObjectName("memberPane")
        pane.setFixedWidth(224)
        pane.setStyleSheet(
            f"QFrame#memberPane {{ background:{C.CARD}; border:1px solid {C.BORDER};"
            f"border-radius:{R}px; }}")
        col = QVBoxLayout(pane)
        col.setContentsMargins(Space.MD, Space.MD, Space.SM, Space.MD)
        col.setSpacing(Space.SM)

        self._member_heading = QLabel("MEMBERS")
        self._member_heading.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:{Type.MICRO}px;font-weight:800;"
            f"letter-spacing:1px;border:none;background:transparent;")
        col.addWidget(self._member_heading)
        col.addWidget(self._rule())

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setStyleSheet("QScrollArea{background:transparent;border:none;}" + scrollbar(C.CARD))
        host = QWidget()
        host.setStyleSheet("background:transparent;")
        self._members = QVBoxLayout(host)
        self._members.setContentsMargins(0, 0, 6, 0)
        self._members.setSpacing(2)
        self._members.addStretch()
        area.setWidget(host)
        col.addWidget(area, 1)

        col.addWidget(self._rule())
        note = QLabel("Team chat is kept in the company record.")
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:{Type.MICRO}px;line-height:16px;"
            f"border:none;background:transparent;")
        col.addWidget(note)
        return pane

    def _rule(self) -> QFrame:
        """A hairline. One definition, so both rules in this panel match."""
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{C.BORDER};border:none;")
        return line

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
        # Two requests, deliberately. Folding conversations into the teams
        # reply would have meant the team list carrying rows it has no shape
        # for, and every existing caller of it learning about direct messages.
        self._run(ChatManager.fetch_directs, self._on_directs, None)

    def _on_teams_failed(self, _error: str):
        if not self._teams:
            self._title.setText("Cannot reach the server")
            self._subtitle.setText("Channels will appear once the connection is back.")

    def _on_teams(self, payload):
        self._teams = payload.get("teams") or []
        self._render_channels()
        self._emit_unread()

        if self._channel_id is None:
            # `_channel` is only set when the history reply lands, so testing
            # it here re-opened the same channel on every thirty-second
            # refresh while the first reply was still in flight — two
            # histories racing, and the older one able to win.
            first = next((c for t in self._teams for c in t["channels"]), None)
            if first:
                self.open_channel(first["id"])

    def _on_directs(self, payload):
        self._directs = payload.get("directs") or []
        self._emit_unread()
        self._render_channels()

    def _new_message(self):
        """Search for somebody and open a conversation with them."""
        dialog = _PeoplePicker(self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.chosen:
            return
        self._run(ChatManager.open_direct,
                  self._on_direct_opened,
                  lambda error: QMessageBox.information(
                      self, "Could not open chat", error),
                  dialog.chosen)

    def _on_direct_opened(self, payload):
        channel = payload.get("channel") or {}
        # Refresh the list first, so the conversation has a row to select —
        # otherwise a brand new one opens with nothing highlighted on the left
        # and looks like it did not open at all.
        self._run(ChatManager.fetch_directs, self._on_directs, None)
        if channel.get("id"):
            self.open_channel(channel["id"])

    def _render_channels(self):
        while self._channel_list.count() > 1:
            item = self._channel_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._rows.clear()

        index = self._render_directs(0)

        if not self._teams:
            if index:
                return
            empty = QLabel("You are not in any team yet.\nAn administrator adds you to one.")
            empty.setWordWrap(True)
            empty.setStyleSheet(
                f"color:{C.TEXT_DIM};font-size:12px;padding:14px 6px;"
                f"border:none;background:transparent;")
            self._channel_list.insertWidget(0, empty)
            return

        for team in self._teams:
            label = QLabel(team["name"].upper() +
                           ("  · archived" if team.get("is_archived") else ""))
            label.setStyleSheet(
                f"color:{C.TEXT_DIM};font-size:12px;font-weight:800;letter-spacing:1px;"
                f"padding:10px 10px 2px;border:none;background:transparent;")
            self._channel_list.insertWidget(index, label)
            index += 1
            for channel in team["channels"]:
                row = _ChannelRow(channel)
                row.clicked.connect(
                    lambda _checked=False, cid=channel["id"]: self.open_channel(cid))
                self._channel_list.insertWidget(index, row)
                self._rows[channel["id"]] = row
                selected = (self._channel is not None
                            and self._channel["id"] == channel["id"])
                row.setChecked(selected)
                row.set_selected(selected)
                index += 1

    def _render_directs(self, index: int) -> int:
        """The conversations section, above the teams. Returns the next index.

        Above rather than below because it is the part somebody opens the
        panel for most often — a team channel is read, a message to one person
        is answered.
        """
        # A FULL-WIDTH BUTTON THAT SAYS WHAT IT DOES.
        #
        # The first version of this was a 20-pixel "+" beside the heading. It
        # was missed entirely — which is the whole feature missed, because
        # finding somebody outside your team is the only way in. An affordance
        # nobody sees is not an affordance.
        new_chat = QPushButton("Message somebody")
        new_chat.setCursor(Qt.CursorShape.PointingHandCursor)
        new_chat.setFixedHeight(34)
        new_chat.setObjectName("newDm")
        new_chat.setStyleSheet(
            f"QPushButton#newDm {{ background:{C.PRIMARY};color:{C.ON_ACCENT};"
            f"border:none;border-radius:{R_SM}px;font-size:12px;font-weight:700;"
            f"text-align:center;padding:0 10px; }}"
            f"QPushButton#newDm:hover {{ background:{C.PRIMARY_DIM}; }}")
        new_chat.clicked.connect(self._new_message)

        holder = QWidget()
        holder.setObjectName("dmHeader")
        holder.setStyleSheet("QWidget#dmHeader{background:transparent;}")
        bar = QVBoxLayout(holder)
        bar.setContentsMargins(8, 8, 8, 4)
        bar.setSpacing(6)
        bar.addWidget(new_chat)

        title = QLabel("DIRECT MESSAGES")
        title.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:12px;font-weight:800;letter-spacing:1px;"
            f"border:none;background:transparent;padding-left:2px;")
        bar.addWidget(title)

        self._channel_list.insertWidget(index, holder)
        index += 1

        # ONE ROW PER CHANNEL. The list is keyed by channel_id, so two
        # entries pointing at the same conversation would overwrite each
        # other in self._rows — and both would be drawn as selected at once,
        # because selection is decided by comparing that same id.
        #
        # Seen for real: a direct channel briefly had three members, so the
        # server offered it twice — once under each of the other two people —
        # and picking one highlighted both. The membership was the fault, but
        # a list that cannot survive a duplicate is a second one.
        seen_channels = set()
        for direct in self._directs:
            if direct.get("channel_id") in seen_channels:
                continue
            seen_channels.add(direct.get("channel_id"))
            row = _DirectRow(direct)
            row.clicked.connect(
                lambda _checked=False, cid=direct["channel_id"]: self.open_channel(cid))
            self._channel_list.insertWidget(index, row)
            self._rows[direct["channel_id"]] = row
            selected = (self._channel is not None
                        and self._channel["id"] == direct["channel_id"])
            row.setChecked(selected)
            row.set_selected(selected)
            index += 1

        if not self._directs:
            hint = QLabel("Nobody yet — use the button above to write to\n"
                          "anybody in the company, team or not.")
            hint.setWordWrap(True)
            hint.setStyleSheet(
                f"color:{C.TEXT_DIM};font-size:12px;padding:2px 12px 8px;"
                f"border:none;background:transparent;")
            self._channel_list.insertWidget(index, hint)
            index += 1
        return index

    # ── one channel ─────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """What is worth carrying across a rebuild."""
        draft = ""
        try:
            draft = self._composer.toPlainText()
        except Exception:
            pass
        return {"channel_id": self._channel_id, "draft": draft}

    def restore(self, state: dict) -> None:
        """Put back what snapshot() took, if it is still valid."""
        if not state:
            return
        channel_id = state.get("channel_id")
        if channel_id:
            try:
                self.open_channel(channel_id)
            except Exception:
                # A channel that has since been left or archived simply does
                # not reopen; losing the draft as well would be worse.
                pass
        draft = state.get("draft") or ""
        if draft:
            try:
                self._composer.setPlainText(draft)
                cursor = self._composer.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                self._composer.setTextCursor(cursor)
            except Exception:
                pass

    def _on_typed(self):
        """A keystroke — tell the server, at most once every few seconds.

        THROTTLED HERE RATHER THAN ON THE SERVER. The server cannot tell a
        fast typist from a loop; the client knows it has already said so two
        seconds ago and that nothing has changed since.

        An empty box means "stopped" — somebody who selects all and deletes
        has abandoned the message, and leaving the dots up would be a lie
        that lasts until the row expires.
        """
        if not self._channel_id:
            return
        import time

        if not self._composer.toPlainText().strip():
            self._typing_last_ping = 0.0
            self._run(ChatManager.stop_typing, lambda _r: None,
                      lambda _e: None, self._channel_id)
            return

        now = time.monotonic()
        if now - self._typing_last_ping < self._typing_ping_gap:
            return
        self._typing_last_ping = now
        self._run(ChatManager.ping_typing, lambda _r: None,
                  lambda _e: None, self._channel_id)

    def _load_typing(self):
        """Who is typing here, other than me."""
        if not self._channel_id:
            return
        self._run(ChatManager.who_is_typing, self._show_typing,
                  lambda _error: None, self._channel_id)

    def _show_typing(self, people: list):
        """One name, two names, or a count — never a wall of them."""
        names = [str(p.get("name") or "").strip() for p in (people or [])]
        names = [n for n in names if n]
        if not names:
            self._typing_label.hide()
            self._typing_label.setText("")
            return

        if len(names) == 1:
            text = f"{names[0]} is typing…"
        elif len(names) == 2:
            text = f"{names[0]} and {names[1]} are typing…"
        else:
            # NOT a list of six names. On a busy channel that line grows
            # wider than the panel and pushes the composer around.
            text = f"{len(names)} people are typing…"
        self._typing_label.setText(text)
        self._typing_label.show()

    # ── threads ─────────────────────────────────────────────────────────

    def open_thread(self, seq: int):
        """Show the discussion hanging off a message."""
        self._thread_root = int(seq)
        self._side.setCurrentIndex(1)
        self._load_thread()

    def close_thread(self):
        self._thread_root = None
        self._side.setCurrentIndex(0)

    def _load_thread(self):
        if not getattr(self, "_thread_root", None):
            return
        self._run(ChatManager.thread, self._fill_thread,
                  lambda error: QMessageBox.information(
                      self, "Thread", error),
                  self._thread_root)

    def _fill_thread(self, payload: dict):
        """Root at the top, replies under it, oldest first."""
        while self._thread_body.count():
            item = self._thread_body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        root = (payload or {}).get("root")
        replies = (payload or {}).get("replies") or []
        # THE ROOT SEQ COMES FROM THE SERVER. Clicking a reply opens the same
        # thread, so what is on screen may not be what was clicked — sending
        # against the clicked message would start a second thread beside the
        # first.
        if root and root.get("seq"):
            self._thread_root = int(root["seq"])

        me = SessionManager.employee_id
        for message in ([root] if root else []) + list(replies):
            bubble = _Bubble(message, mine=message.get("sender_id") == me,
                             can_post=False)
            bubble.react_requested.connect(self._toggle_reaction)
            bubble.thread_requested.connect(self.open_thread)
            self._thread_body.addWidget(bubble)

        count = len(replies)
        note = QLabel("No replies yet — the first one starts the thread."
                      if not count else
                      f"{count} repl{'y' if count == 1 else 'ies'}")
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:{Type.MICRO}px;"
            f"border:none;background:transparent;")
        self._thread_body.addWidget(note)
        self._thread_body.addStretch()

    def _send_thread_reply(self):
        text = self._thread_input.text().strip()
        root = getattr(self, "_thread_root", None)
        if not text or not root or not self._channel:
            return
        try:
            self._chat.send(self._channel["id"], text, reply_to=int(root))
        except Exception as error:              # noqa: BLE001
            QMessageBox.information(self, "Not sent", str(error))
            return
        self._thread_input.clear()
        # The reply is queued locally, so the thread is redrawn from the
        # server a moment later rather than optimistically here — one source
        # for what the thread contains.
        QTimer.singleShot(600, self._load_thread)

    def _load_reaction_choices(self):
        """Ask the server what it accepts, once per run.

        A hard-coded list here is the classic drift: somebody adds an emoji
        server-side, the client never offers it; somebody removes one, the
        client offers a button that returns 400. The fallback in _Bubble is
        only for the moment before this answers.
        """
        if _Bubble.REACTION_CHOICES:
            return
        self._run(ChatManager.reaction_choices,
                  lambda choices: setattr(_Bubble, "REACTION_CHOICES",
                                          list(choices or [])),
                  lambda _error: None)

    def open_channel(self, channel_id: int):
        self._searching = False
        self._back.hide()
        for cid, row in self._rows.items():
            row.setChecked(cid == channel_id)
            row.set_selected(cid == channel_id)
        self._chat.set_active_channel(channel_id)
        self._member_timer.start()
        # Clear whatever the previous channel was showing before the first
        # poll of the new one answers — otherwise "Priya is typing…" follows
        # you into a conversation Priya is not in.
        self._show_typing([])
        # A thread belongs to the channel it was opened from. Carrying it
        # across would leave somebody replying into a conversation they are
        # no longer looking at.
        self.close_thread()
        self._typing_last_ping = 0.0
        self._typing_timer.start()
        self._load_typing()
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
        self._load_reaction_choices()
        self._run(ChatManager.fetch_history, self._on_history,
                  self._on_history_failed, channel_id)
        self._load_members(channel_id)
        self._load_pinned(channel_id)

    def _on_history_failed(self, _error: str):
        self._title.setText("Cannot load this channel")
        self._subtitle.setText("It will load when the connection is back.")

    def _on_history(self, payload):
        channel = payload.get("channel") or {}
        # A reply for a channel nobody is looking at any more — two opens in
        # quick succession, and the slower reply arriving second — must not
        # paint the previous conversation over the current one.
        if channel.get("id") and self._channel_id and \
                channel["id"] != self._channel_id:
            return
        self._channel = channel
        history = payload.get("messages") or []
        # Anything that arrived on the poll while this request was in flight
        # is newer than the reply and is not in it. Dropping it looked like a
        # message that was sent and then vanished until the next one came.
        newest = history[-1]["seq"] if history else 0
        later = [m for m in self._messages
                 if m.get("channel_id") == channel.get("id")
                 and (m.get("seq") or 0) > (newest or 0)]
        self._messages = history + later
        self._oldest_seq = self._messages[0]["seq"] if self._messages else None

        self._title.setTextFormat(Qt.TextFormat.PlainText)
        self._title.setText(channel.get("name", "—"))
        bits = [channel.get("team_name", "")]
        if channel.get("type") == "ANNOUNCEMENT":
            bits.append("announcements — only administrators post here")
        if channel.get("is_archived"):
            bits.append("archived — read only")
        self._subtitle.setTextFormat(Qt.TextFormat.PlainText)
        self._subtitle.setText("  ·  ".join(b for b in bits if b))

        can_post = bool(channel.get("can_post"))
        self._composer_row.setVisible(can_post)
        self._read_only.setVisible(not can_post)
        if not can_post:
            self._read_only.setText(
                "This is an announcement channel — only administrators can post."
                if channel.get("type") == "ANNOUNCEMENT"
                else "This team is archived. You can read it, but not add to it.")

        self._render_feed(force_bottom=True)
        self._mark_read()

    def _render_feed(self, force_bottom: bool = False):
        # Opening a channel always lands on the newest message. Without the
        # override it would inherit the scroll position of whatever channel
        # was open before, so switching from halfway up one conversation
        # dropped you halfway up the next.
        at_bottom = force_bottom or self._at_bottom()
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
                    f"color:{C.TEXT_DIM};font-size:12px;font-weight:700;"
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
            bubble.delete_requested.connect(self._delete_message)
            bubble.download_requested.connect(self._download_file)
            bubble.jump_requested.connect(self._jump_to)
            bubble.image_wanted.connect(self._load_image)
            bubble.image_clicked.connect(self._open_image)
            bubble.react_requested.connect(self._toggle_reaction)
            # Only now is anything listening — see _Bubble.request_images.
            bubble.request_images()
            self._feed.insertWidget(index, bubble)
            index += 1

        # Only follow the conversation down if it was already at the bottom.
        #
        # Rebuilding the feed resets the scrollbar, so somebody reading back
        # through history was thrown to the end every time anything arrived —
        # or, worse, snapped away mid-sentence. If they have scrolled up, they
        # are reading something; leave them there.
        if at_bottom:
            QTimer.singleShot(0, self._scroll_to_bottom)

    def _at_bottom(self, slack: int = 60) -> bool:
        bar = self._scroll.verticalScrollBar()
        # A fresh, empty feed has maximum 0 and counts as "at the bottom", so
        # the first load still lands on the newest message.
        return bar.maximum() - bar.value() <= slack

    def _scroll_to_bottom(self):
        """Go to the end, and keep going there until the feed stops growing.

        Asking for the bottom once does not reach it. Sending a message makes
        the feed taller, but the scrollbar's maximum only grows after Qt has
        laid the new bubble out, and that has not happened yet when this runs.
        So `maximum()` is still the height from BEFORE the message — we scroll
        to the old end, the feed then grows past it, and the conversation looks
        like it jumped upwards on every send. That was the actual bug; it was
        not a scroll going up, it was a scroll stopping short.

        So we also stay hungry for a moment: any range change in the next
        FOLLOW_SECONDS pins us to the new bottom. That window also covers a
        picture finishing its decode and pushing the feed down.
        """
        self._follow_until = monotonic() + FOLLOW_SECONDS
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _range_changed(self, _minimum: int, maximum: int) -> None:
        # Deliberately time-limited rather than a flag somebody has to clear.
        # A flag left set would yank a person who had scrolled up back to the
        # end the moment anything changed height.
        if monotonic() < getattr(self, "_follow_until", 0.0):
            self._scroll.verticalScrollBar().setValue(maximum)

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
        # Same rule as _emit_unread: a channel is only "being read" while the
        # page is actually on screen. Zeroing it from a hidden page threw
        # away counts for the very conversation somebody was last in.
        reading = self.isVisible() and self._channel is not None
        for team in self._teams:
            for channel in team["channels"]:
                if reading and channel["id"] == self._channel["id"]:
                    channel["unread"] = 0
            team["unread"] = sum(int(c.get("unread") or 0) for c in team["channels"])
        self._emit_unread()

    def _emit_unread(self):
        """Teams AND direct messages.

        THE TOTAL USED TO COUNT TEAM CHANNELS ONLY. A direct message — the
        one kind that is always addressed to you personally — added nothing
        to the badge, so somebody wrote to you and the menu said nothing.
        Reported as "jab tak My Chat nahi khol raha, pata hi nahi chalta".
        """
        # THE OPEN CONVERSATION IS ONLY "OPEN" WHILE THE PAGE IS VISIBLE.
        #
        # This excluded whatever `self._channel` held — and that attribute
        # keeps its value after the page is closed. So the one conversation
        # somebody had been reading was excluded from the badge for the rest
        # of the session: messages arrived, the count stayed at zero, and it
        # looked as though notifications were dead.
        #
        # Seen live, and it explains why the employee panel looked right and
        # the admin console did not: the employee's count came from team
        # channels, which were never excluded, while the admin's traffic was
        # all in the one direct message they had opened.
        looking = self.isVisible() and self._channel is not None
        open_id = self._channel.get("id") if looking else None

        teams = sum(int(t.get("unread") or 0) for t in self._teams)
        directs = sum(int(d.get("unread") or 0)
                      for d in getattr(self, "_directs", []) or []
                      if d.get("channel_id") != open_id)
        self.unread_changed.emit(teams + directs)

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
        self._typing_timer.stop()
        self._show_typing([])
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

        members = payload.get("members") or []
        if getattr(self, "_member_heading", None) is not None:
            self._member_heading.setText(
                f"MEMBERS  ·  {len(members)}" if members else "MEMBERS")

        for index, member in enumerate(members):
            colour, label = PRESENCE.get(member.get("status", "OFFLINE"),
                                         PRESENCE["OFFLINE"])
            if member.get("status") == "IDLE" and member.get("idle_minutes") is not None:
                label = f"Idle {member['idle_minutes']} min"

            row = QWidget()
            row.setStyleSheet("background:transparent;")
            line = QHBoxLayout(row)
            line.setContentsMargins(4, 5, 4, 5)
            line.setSpacing(Space.SM)

            face = Avatar(26)
            face.show_person(member.get("employee_id"), member.get("name") or "")
            line.addWidget(face)

            names = QVBoxLayout()
            names.setSpacing(1)
            names.setContentsMargins(0, 0, 0, 0)
            name = QLabel(member["name"] + (" (you)" if member.get("is_me") else ""))
            name.setStyleSheet(
                f"color:{C.TEXT};font-size:13px;font-weight:600;"
                f"border:none;background:transparent;")

            # THE STATE, WITH THE DOT DRAWN RATHER THAN TYPED. "●" is a
            # character out of the text font: its size and its baseline are
            # the font's business, so it never lined up with the word beside
            # it. A four-pixel rounded label is the same shape at every font.
            state_row = QHBoxLayout()
            state_row.setSpacing(6)
            state_row.setContentsMargins(0, 0, 0, 0)
            dot = QLabel()
            dot.setFixedSize(6, 6)
            dot.setStyleSheet(dot_style(6, colour))
            state = QLabel(label)
            state.setStyleSheet(
                f"color:{C.TEXT_DIM};font-size:{Type.MICRO}px;"
                f"border:none;background:transparent;")
            state_row.addWidget(dot)
            state_row.addWidget(state)
            state_row.addStretch()

            names.addWidget(name)
            names.addLayout(state_row)
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
        # Sent — so stop saying "typing". Without this the dots stay up for
        # the rest of the window, which reads as though the next message is
        # already on its way.
        self._typing_last_ping = 0.0
        if self._channel_id:
            self._run(ChatManager.stop_typing, lambda _r: None,
                      lambda _e: None, self._channel_id)
        self._cancel_reply()
        self._clear_staged()
        # Your own message always scrolls into view — you just wrote it.
        self._render_feed(force_bottom=True)

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
            # `_channel_id` is set the moment the channel is opened;
            # `_channel` only when its history comes back. A message landing
            # in that gap used to be filed as unread for the very channel
            # being looked at.
            if channel_id == self._channel_id and not self._searching:
                if not any(m.get("seq") == message.get("seq") for m in self._messages):
                    self._messages.append(message)
                    touched = True
                continue
            # Somewhere else — bump its badge.
            #
            # BOTH LISTS, NOT JUST THE TEAMS. This walked self._teams alone,
            # so a direct message raised nothing here and its count only
            # appeared when fetch_directs next ran — on a 30-to-60 second
            # timer. The badge did arrive, ten to twenty seconds late, which
            # reads as broken rather than slow: "raju ko message kiya to ye 1
            # se 2 nahi hua".
            #
            # The message itself is already in hand at this point; the count
            # should not need a second round trip to the server to change.
            row = self._rows.get(channel_id)
            bumped = False
            for team in self._teams:
                for channel in team["channels"]:
                    if channel["id"] == channel_id:
                        channel["unread"] = int(channel.get("unread") or 0) + 1
                        if row:
                            row.set_unread(channel["unread"])
                        bumped = True
            if not bumped:
                for direct in getattr(self, "_directs", []) or []:
                    if direct.get("channel_id") == channel_id:
                        direct["unread"] = int(direct.get("unread") or 0) + 1
                        if row:
                            row.set_unread(direct["unread"])
                        bumped = True
            if not bumped:
                # A conversation this panel has never listed — somebody
                # writing for the first time. The row cannot be bumped
                # because it does not exist yet, so ask for the list; without
                # this the very first message from a new person is silent
                # until the slow timer comes round.
                #
                # Throttled, because "not in either list" also describes a
                # public announcement channel somebody is not a member of,
                # and that would otherwise fetch the whole conversation list
                # once per announcement.
                if monotonic() - getattr(self, "_directs_asked_at", 0.0) > 10:
                    self._directs_asked_at = monotonic()
                    self._run(ChatManager.fetch_directs, self._on_directs, None)
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
        self._subtitle.setTextFormat(Qt.TextFormat.PlainText)
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
        self._reply_label.setTextFormat(Qt.TextFormat.PlainText)
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

    def _toggle_reaction(self, seq: int, emoji: str):
        """React, or take it back — and redraw only that message.

        NOT A CHANNEL RELOAD. Pinning reloads because it changes the pinned
        shelf as well; a reaction changes one row. Reloading would scroll the
        conversation back to the bottom, which on a long thread throws away
        where somebody was reading.
        """
        def done(payload):
            reactions = (payload or {}).get("reactions") or []
            for message in self._messages:
                if message.get("seq") == seq:
                    message["reactions"] = reactions
                    break
            # _render_feed, and NOT with force_bottom: reacting to a message
            # eight screens up must not throw the reader back to the newest
            # one. That is what a channel reload would have done.
            self._render_feed()

        self._run(ChatManager.react, done,
                  lambda error: QMessageBox.information(
                      self, "Not reacted", error),
                  seq, emoji)

    def _toggle_pin(self, seq: int, pinned: bool):
        self._run(ChatManager.set_pinned, lambda _p: self._reload_channel(),
                  lambda error: QMessageBox.information(self, "Not pinned", error),
                  seq, pinned)

    def _open_image(self, attachment_id: int, file_name: str):
        """Show the picture full size.

        Only from the cache. If it is on screen it has already been fetched
        and decrypted, so opening it is instant — and if it has not arrived
        yet, there is nothing to open and the bubble still says so.
        """
        blob = _IMAGE_CACHE.get(int(attachment_id))
        if blob is None:
            # Nothing to open. If it failed, this click means "try again";
            # if it is merely still arriving, leave it alone.
            if _IMAGE_FAILED.pop(int(attachment_id), None) is not None:
                self._render_feed()
            return
        # .enc is the name it is STORED under. Showing that to somebody who
        # sent "invoice.png" is confusing, and the viewer's title is the only
        # place the name appears.
        clean = file_name[:-4] if file_name.lower().endswith(".enc") else file_name
        viewer = ImageViewer(blob, clean, self)
        viewer.save_requested.connect(
            lambda: self._download_file(int(attachment_id), clean))
        viewer.exec()

    def _delete_message(self, seq: int):
        """Confirm, then withdraw. Nothing about this can be undone."""
        answer = QMessageBox.question(
            self, "Delete message",
            "Delete this message?\n\n"
            "Everyone in the channel will see that a message was deleted. "
            "It stays in the company record.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run(ChatManager.delete_message,
                  lambda _p: self._mark_deleted([seq]),
                  lambda error: QMessageBox.information(self, "Not deleted", error),
                  seq)

    def _mark_deleted(self, seqs: list):
        """Turn messages already on screen into tombstones.

        Applied locally rather than by refetching the channel: on this link a
        refetch is most of a second, and a message that stays readable for that
        long after its author took it back is the one thing this must not do.
        Everything here is idempotent, because the poll re-sends the same seqs
        for several minutes.
        """
        wanted = {int(seq) for seq in seqs}
        touched = False
        for message in self._messages:
            if int(message.get("seq") or 0) in wanted and not message.get("deleted"):
                message["deleted"] = True
                message["body"] = ""
                message["attachments"] = []
                message["pinned"] = False
                touched = True
        if touched:
            self._render_feed()
            # A withdrawn message may have been on the pinned shelf.
            self._load_pinned()

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
            # setTextFormat() WAS CALLED HERE AND QPushButton HAS NO SUCH
            # METHOD — this raised the moment a channel had anything pinned,
            # which is to say the pinned bar never once appeared. A button
            # does not interpret markup anyway, so the call was trying to buy
            # something it already had.
            #
            # What a button DOES do to text is read "&" as a mnemonic and
            # swallow it, so a pinned message about "R&D" showed as "RD".
            # Doubling it is how Qt is told to draw one.
            self._pinned_bar.setText(f"{first}{more}".replace("&", "&&"))
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

    def _insert_emoji(self):
        """A short menu of emoji, dropped into the box at the cursor.

        THE SAME LIST THE REACTIONS USE, and for the same reason: the server
        keeps a whitelist, and offering a full keyboard would mean offering
        characters it will refuse. Nothing is sent from here — the emoji is
        typed for the person, and they still press Send.
        """
        menu = QMenu(self)
        # Content, not interface icons — the same marker the reaction menu
        # carries, so the "no emoji in the UI" check knows the difference.
        fallback = ["👍", "❤️", "😂", "🎉", "👀", "✅"]  # reaction content
        for emoji in (_Bubble.REACTION_CHOICES or fallback):
            action = menu.addAction(str(emoji))
            action.triggered.connect(
                lambda _checked=False, e=str(emoji): self._type_into_composer(e))
        menu.exec(QCursor.pos())

    def _insert_mention(self):
        """Types the "@" for somebody, which opens the member list.

        The list is driven by what is in the box — the composer reports the
        partial handle and the page answers with names — so this needs no new
        path of its own: it types the character and the existing flow runs.
        """
        text = self._composer.toPlainText()
        lead = "" if (not text or text[-1:].isspace()) else " "
        self._type_into_composer(f"{lead}@")

    def _type_into_composer(self, text: str) -> None:
        """Insert at the cursor and keep the focus in the box."""
        cursor = self._composer.textCursor()
        cursor.insertText(text)
        self._composer.setTextCursor(cursor)
        self._composer.setFocus()

    def _attach_menu(self):
        """A small menu, rather than dropping straight into a file browser.

        Picking a photograph and picking a document are different errands, and
        a plain "choose a file" box makes finding a picture among a folder of
        everything else the employee's problem. This is the one place people
        will use most, so it gets the filter.
        """
        if not self._channel:
            return
        menu = self.build_attach_menu()
        button = self.sender()
        origin = button.mapToGlobal(button.rect().bottomLeft()) if button else None
        menu.exec(origin or QCursor.pos())

    def build_attach_menu(self) -> QMenu:
        """The menu itself, separate from showing it.

        exec() runs a modal loop, so anything that calls it and then looks at
        the result never gets there. Building and showing are split so the
        contents can be checked without opening a window.
        """
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background:{C.ELEVATED};color:{C.TEXT};"
            f"border:1px solid {C.BORDER};border-radius:{R_SM}px;padding:6px; }}"
            f"QMenu::item {{ padding:9px 22px;border-radius:12px;font-size:13px; }}"
            f"QMenu::item:selected {{ background:{C.SELECTED_BG};"
            f"color:{C.SELECTED_TEXT}; }}")

        photos = QAction("Photo", menu)
        photos.triggered.connect(lambda: self._attach_file(images_only=True))
        menu.addAction(photos)

        any_file = QAction("File", menu)
        any_file.triggered.connect(lambda: self._attach_file(images_only=False))
        menu.addAction(any_file)
        return menu

    def _paste_image(self, image):
        """A picture pasted into the box. Save it, then send it as a file.

        Written to a temporary file because the upload path takes a path —
        the same one the paperclip uses, so encryption, progress and the
        local cache all behave identically. The file is JPEG at the same
        quality a capture uses: a pasted screenshot is often several
        megabytes of PNG, and on this link that is a visible wait.
        """
        if not self._channel:
            return
        try:
            import tempfile
            handle = tempfile.NamedTemporaryFile(
                prefix="pasted-", suffix=".jpg", delete=False)
            handle.close()
            if not image.save(handle.name, "JPEG", 85):
                raise RuntimeError("the pasted image could not be saved")
        except Exception as error:
            QMessageBox.information(
                self, "Could not paste", f"That picture could not be used — {error}")
            return
        self._send_pasted(handle.name, "Pasting picture…")

    def _paste_file(self, path: str):
        """A picture copied in Finder or Explorer, arriving as a path."""
        if not self._channel:
            return
        self._send_pasted(path, f"Pasting {path.split('/')[-1]}…")

    def _send_pasted(self, path: str, note: str):
        self._staged_note(note)
        self._last_upload_path = path
        self._run(ChatManager.upload_attachment, self._on_uploaded,
                  self._on_upload_failed, self._channel["id"], path)

    def _attach_file(self, images_only: bool = False):
        if not self._channel:
            return
        if images_only:
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose a photo", "",
                "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp)")
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Choose a file")
        if not path:
            return
        self._staged_note(f"Uploading {path.split('/')[-1]}…")
        # Remembered so the picture can be cached from the copy already on
        # this machine — see _on_uploaded.
        self._last_upload_path = path
        self._run(ChatManager.upload_attachment, self._on_uploaded,
                  self._on_upload_failed, self._channel["id"], path)

    def _on_uploaded(self, attachment: dict):
        self._staged.append(attachment)

        # Cache the picture from the file the employee just chose.
        #
        # Without this, sending an image meant encrypting it, uploading it,
        # and then DOWNLOADING IT BACK and decrypting it to draw — a round
        # trip and a decryption for bytes that were already sitting on this
        # machine. On this connection that is a visible wait to see the thing
        # you just sent.
        path = getattr(self, "_last_upload_path", None)
        if path and _looks_like_image({"file_name": path}):
            try:
                with open(path, "rb") as handle:
                    _cache_image(int(attachment["id"]), handle.read())
            except OSError:
                # Unreadable now for some reason; it will be fetched the
                # ordinary way when it is drawn.
                pass
        self._last_upload_path = None
        self._render_staged()

    def _on_upload_failed(self, error: str):
        self._render_staged()
        QMessageBox.warning(self, "Upload failed", error)

    def _staged_note(self, text: str):
        self._clear_staged_widgets()
        label = QLabel(text)
        label.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:12px;border:none;background:transparent;")
        self._staged_row.addWidget(label)
        self._staged_bar.show()

    def _render_staged(self):
        self._clear_staged_widgets()
        if not self._staged:
            self._staged_bar.hide()
            return
        for attachment in self._staged:
            chip = QPushButton(f"{attachment['file_name']}")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip("Remove")
            chip.setStyleSheet(
                f"color:{C.BLUE};background:{C.BLUE_BG};border:1px solid {C.BORDER};"
                f"border-radius:{R_SM}px;padding:4px 8px;font-size:12px;")
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

    # ── pictures ────────────────────────────────────────────────────────

    def _load_image(self, attachment_id: int, label):
        """Fetch and decrypt one image, then put it in the label that asked.

        The label is passed by reference rather than looked up afterwards
        because the feed is rebuilt constantly — by the time this returns, the
        widget that wanted it may already have been replaced, and writing into
        a deleted one is a crash. Hence the liveness check on the way back.
        """
        if attachment_id in self._loading_images:
            return
        self._loading_images.add(attachment_id)

        def done(blob: bytes, _id=attachment_id, _label=label):
            self._loading_images.discard(_id)
            _cache_image(_id, blob)
            pixmap = _thumbnail(blob)
            try:
                # NOT `isVisible()`. A widget inside a scroll area that is
                # scrolled past — or one Qt has not painted yet — reports
                # False, so every picture was skipped here and sat on
                # "Loading image…" for good. The only thing worth guarding
                # against is the C++ object having been deleted by a rebuild,
                # and that raises RuntimeError on touch.
                if pixmap is not None:
                    _label.setText("")
                    _label.setPixmap(pixmap)
                    _label.setFixedSize(pixmap.size())
                else:
                    _label.setText("Could not read this image")
            except RuntimeError:
                # The label was deleted by a rebuild while this was in
                # flight. The bytes are cached; the redraw below puts them on
                # whatever took its place.
                pass

            # Redraw once the picture is in hand.
            #
            # BUG this fixes: the feed is rebuilt more than once while a
            # download is running — opening a channel renders, then the
            # history reply renders again. The first render's label was
            # deleted, so the picture landed on a dead widget; the second
            # render asked for the same image and was turned away by
            # _loading_images because the first request was still in flight.
            # The bytes ended up cached and NOTHING on screen ever changed:
            # four labels stuck on "Loading image…" with the images sitting
            # in memory beside them.
            #
            # Rebuilding here is safe from looping: a bubble only asks for a
            # picture that is not already cached, and by now it is.
            if not self._loading_images:
                self._render_feed()

        def failed(error: str, _id=attachment_id, _label=label):
            self._loading_images.discard(_id)
            _IMAGE_FAILED[_id] = str(error) or "Image could not be loaded"
            try:
                if _label is not None:
                    _label.setText(f"{_IMAGE_FAILED[_id]}\nClick to try again")
            except RuntimeError:
                pass

        self._run(ChatManager.attachment_bytes, done, failed, attachment_id)

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
        # THE CHANNEL LIST KEEPS REFRESHING WHILE THE PAGE IS HIDDEN.
        #
        # It used to stop with everything else, so the unread badge on the
        # menu froze at whatever it was when the page was last closed — and
        # somebody could be messaged all afternoon with no sign of it
        # anywhere. That is the one number that has to stay right while the
        # page is NOT open; the members and the typing indicator do not.
        #
        # Slower, because nobody is reading it: once a minute against the
        # same query the page already makes.
        self._teams_timer.setInterval(60_000)
        self._teams_timer.start()
        # And say so: leaving the page with the dots still up leaves a
        # colleague waiting for a message that is not coming.
        self._typing_timer.stop()
        if self._channel_id:
            self._run(ChatManager.stop_typing, lambda _r: None,
                      lambda _e: None, self._channel_id)
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        # Back to the open-page cadence: somebody watching the list should
        # see a channel appear or a count clear without a minute's wait.
        self._teams_timer.setInterval(30_000)
        self._teams_timer.start()
        self.refresh()

        # AND RE-READ THE OPEN CONVERSATION. Reported live: "abhi empty tha
        # load ni hua jab tak general pe dobaara ni click kiya".
        #
        # hideEvent stops the poll for this channel, so anything said while
        # the page was on another tab never reached `_on_messages` — and
        # coming back only refreshed the LIST of channels, never the messages
        # in the one already open. The feed showed whatever it had when the
        # page was last visible, which after a first visit that failed is
        # nothing at all. Clicking the channel again called open_channel,
        # which is the only thing that fetches history — so it looked as
        # though the click was what was missing.
        #
        # One request on becoming visible. `open_channel` also restores the
        # poll and the member timer, which is what the old two lines did.
        if self._channel_id:
            self.open_channel(self._channel_id)
