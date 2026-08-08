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

from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QAction, QCursor, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
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


# How long after asking for the bottom we keep following it down. Long enough
# for a layout pass and a picture to decode, short enough that it never fights
# somebody who has started scrolling back.
FOLLOW_SECONDS = 0.6


PRESENCE = {
    "ACTIVE":      ("●", C.GREEN,      "Active"),
    "IDLE":        ("●", C.AMBER,      "Idle"),
    "OFFLINE":     ("●", C.TEXT_DIM,   "Offline"),
    "SHIFT_ENDED": ("●", C.PURPLE,     "Shift ended"),
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
                    f"background:{C.SELECTED_TEXT};color:{C.SELECTED_BG};font-size:10px;"
                    f"font-weight:800;border-radius:9px;padding:0 5px;border:none;")
            return
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
    delete_requested = Signal(int)              # seq
    download_requested = Signal(int, str)       # attachment id, file name
    jump_requested = Signal(int)                # seq of the message replied to
    image_wanted = Signal(int, object)          # attachment id, the label to fill
    image_clicked = Signal(int, str)            # attachment id, file name

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

        if message.get("pinned") and not self.deleted:
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
        # Nothing is offered on a withdrawn message: there is nothing to
        # reply to, quote, pin, or take back a second time.
        if self.seq and can_post and not message.get("pending") and not self.deleted:
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
            waiting = QLabel(f"📎 {queued_files} file(s) uploading…")
            waiting.setStyleSheet(
                f"color:{C.TEXT_DIM};font-size:11px;border:none;background:transparent;")
            col.addWidget(waiting)

    def _more_button(self) -> QPushButton:
        button_ = self._action("⋯", self._more_menu)
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
        remove = QAction("🗑   Delete message", menu)
        remove.triggered.connect(lambda: self.delete_requested.emit(self.seq))
        menu.addAction(remove)
        return menu

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
            f"border-radius:{R_SM}px;padding:6px;color:{C.TEXT_DIM};font-size:11px; }}")
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
            holder.setText(f"⚠  {problem}\nClick to try again")
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
        initial = QLabel((person.get("name") or "?")[:1].upper())
        initial.setFixedSize(24, 24)
        initial.setAlignment(Qt.AlignmentFlag.AlignCenter)
        initial.setStyleSheet(
            f"background:{C.PRIMARY};color:{C.ON_ACCENT};border-radius:12px;"
            f"font-size:11px;font-weight:700;border:none;")
        row.addWidget(initial)

        column = QVBoxLayout()
        column.setSpacing(0)
        self._name = QLabel(person.get("name") or person.get("username") or "Unknown")
        self._name.setStyleSheet(
            f"color:{C.TEXT_MUTED};font-size:13px;border:none;background:transparent;")
        self._preview = QLabel(str(direct.get("preview") or "No messages yet"))
        self._preview.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:10px;border:none;background:transparent;")
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
            f"color:{dim};font-size:10px;border:none;background:transparent;")

    def set_unread(self, count: int) -> None:
        if count > 0:
            self._badge.setText(str(count if count < 100 else "99+"))
            self._badge.setStyleSheet(
                f"background:{C.PRIMARY};color:{C.ON_ACCENT};border-radius:9px;"
                f"font-size:10px;font-weight:700;padding:0 5px;border:none;")
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
            f"color:{C.TEXT_DIM};font-size:11px;background:transparent;")
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
        attach.clicked.connect(self._attach_menu)
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
        total = sum(int(t.get("unread") or 0) for t in self._teams)
        self.unread_changed.emit(total)

        if self._channel is None:
            first = next((c for t in self._teams for c in t["channels"]), None)
            if first:
                self.open_channel(first["id"])

    def _on_directs(self, payload):
        self._directs = payload.get("directs") or []
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
        new_chat = QPushButton("✉   Message somebody")
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
            f"color:{C.TEXT_DIM};font-size:10px;font-weight:800;letter-spacing:1px;"
            f"border:none;background:transparent;padding-left:2px;")
        bar.addWidget(title)

        self._channel_list.insertWidget(index, holder)
        index += 1

        for direct in self._directs:
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
                f"color:{C.TEXT_DIM};font-size:11px;padding:2px 12px 8px;"
                f"border:none;background:transparent;")
            self._channel_list.insertWidget(index, hint)
            index += 1
        return index

    # ── one channel ─────────────────────────────────────────────────────

    def open_channel(self, channel_id: int):
        self._searching = False
        self._back.hide()
        for cid, row in self._rows.items():
            row.setChecked(cid == channel_id)
            row.set_selected(cid == channel_id)
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
            bubble.delete_requested.connect(self._delete_message)
            bubble.download_requested.connect(self._download_file)
            bubble.jump_requested.connect(self._jump_to)
            bubble.image_wanted.connect(self._load_image)
            bubble.image_clicked.connect(self._open_image)
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
            f"QMenu::item {{ padding:9px 22px;border-radius:6px;font-size:13px; }}"
            f"QMenu::item:selected {{ background:{C.SELECTED_BG};"
            f"color:{C.SELECTED_TEXT}; }}")

        photos = QAction("🖼   Photo", menu)
        photos.triggered.connect(lambda: self._attach_file(images_only=True))
        menu.addAction(photos)

        any_file = QAction("📄   File", menu)
        any_file.triggered.connect(lambda: self._attach_file(images_only=False))
        menu.addAction(any_file)
        return menu

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
                    _label.setText(f"⚠  {_IMAGE_FAILED[_id]}\nClick to try again")
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
        self._teams_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self._channel:
            self._chat.set_active_channel(self._channel["id"])
            self._member_timer.start()
        self._teams_timer.start()
        self.refresh()
