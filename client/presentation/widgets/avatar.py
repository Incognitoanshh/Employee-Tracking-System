"""
One face, drawn the same way everywhere.

WHY THIS IS SHARED. My Profile could show a photograph and nothing else
could — so the same person appeared as a picture on one page and as two grey
letters on every other. That is not a cosmetic complaint: in a list of twenty
rows the face is how somebody is found, and initials collide (two people
called "Amit Kumar" are both "AK").

So the widget lives here and the pages ask it for a person, not for a file:

    avatar = Avatar(32)
    avatar.show_person("EMP002", "Ansh")     # initials now, photo when it lands

WHAT IT DOES ABOUT THE SERVER. A team page with thirty rows would otherwise
be thirty requests every time it is drawn, and chat would ask again for every
message. So:

  * fetched ONCE per employee per run, in a background thread, and kept in
    _CACHE — including the fact that somebody has NO photo, which is the
    answer that would otherwise be re-asked most often;
  * a second widget wanting a face already being fetched waits for the same
    request rather than starting another;
  * nothing is written to disk. A cache on disk would be a folder of
    employee photographs sitting on every workstation, and there is no
    version of this product where that is worth saving one request.

A failure is silent ON PURPOSE. The consequence of not getting a photo is
initials, which is exactly what is drawn before it arrives — there is nothing
to tell anybody, and a page that toasts "could not load photo" for every row
of a list is the log flood that has already been fixed three times here.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QObject, QThread, Signal
from PySide6.QtGui import QPixmap, QPainter, QPainterPath
from PySide6.QtWidgets import QLabel

from client.core import http as _http
from client.core.config import API_BASE_URL
from client.application.managers.session_manager import SessionManager
from client.presentation.theme import C


# employee_id -> bytes, or None meaning "asked, and there is no photo".
_CACHE: dict[str, bytes | None] = {}
_LOCK = threading.Lock()
_IN_FLIGHT: dict[str, "_PhotoFetcher"] = {}
# Running QThreads, held so Python does not collect one mid-request — a
# crash that only appears while a long list is scrolling.
_KEEP: set = set()


def forget(employee_id: str | None = None):
    """Drop what is remembered, so the next draw asks again.

    Called when somebody changes or removes their own photo: without it the
    old picture stays on every other page until the app is restarted, which
    reads as "the upload did not work".
    """
    with _LOCK:
        if employee_id is None:
            _CACHE.clear()
        else:
            _CACHE.pop(str(employee_id), None)


class _PhotoFetcher(QThread):
    """One request, off the UI thread, its result shared by every waiter."""

    done = Signal(str, object)          # employee_id, bytes | None

    def __init__(self, employee_id: str):
        super().__init__()
        self._employee_id = str(employee_id)

    def run(self):
        data: bytes | None = None
        try:
            token = getattr(SessionManager, "token", None)
            if token:
                response = _http.get(
                    f"{API_BASE_URL}/profile/photo/{self._employee_id}",
                    headers={"Authorization": f"Bearer {token}"}, timeout=15)
                if response.status_code == 200 and response.content:
                    data = response.content
        except Exception:
            # Silent by design — see the module docstring. The widget is
            # already showing initials and will go on showing them.
            data = None
        with _LOCK:
            # A 403 or a network failure caches as "no photo" for this run.
            # Retrying on every repaint of a list is how a slow connection
            # turns into a hundred requests a minute.
            _CACHE[self._employee_id] = data
            _IN_FLIGHT.pop(self._employee_id, None)
        self.done.emit(self._employee_id, data)


def request(employee_id: str, on_ready):
    """Hand somebody's photo to `on_ready(bytes | None)`, once.

    Answers from the cache immediately when it can, joins a request already
    in the air when there is one, and starts exactly one otherwise. Used by
    the widget below and by the employee table, which draws faces as row
    icons rather than as widgets — thirty QLabels in a table make the rows
    tall and the scrolling coarse.
    """
    key = str(employee_id)
    with _LOCK:
        if key in _CACHE:
            cached = _CACHE[key]
            on_ready(cached)
            return
        running = _IN_FLIGHT.get(key)

    if running is not None:
        running.done.connect(lambda _id, data: on_ready(data))
        return

    fetcher = _PhotoFetcher(key)
    with _LOCK:
        _IN_FLIGHT[key] = fetcher
    _KEEP.add(fetcher)
    fetcher.done.connect(lambda _id, data: on_ready(data))
    fetcher.finished.connect(lambda: _KEEP.discard(fetcher))
    fetcher.finished.connect(fetcher.deleteLater)
    fetcher.start()


def round_pixmap(data: bytes | None, size: int) -> QPixmap | None:
    """Image bytes as a round pixmap, or None if they are not an image."""
    if not data:
        return None
    source = QPixmap()
    if not source.loadFromData(data):
        return None
    source = source.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation)
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    painter.setClipPath(path)
    painter.drawPixmap(
        (size - source.width()) // 2, (size - source.height()) // 2, source)
    painter.end()
    return canvas


class Avatar(QLabel):
    """A round photo, with initials until — or unless — one exists."""

    def __init__(self, size: int = 40, parent=None):
        super().__init__(parent)
        self._size = int(size)
        self.setFixedSize(self._size, self._size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pixmap: QPixmap | None = None
        self._initials = "?"
        self._employee_id: str | None = None
        self._fetcher: _PhotoFetcher | None = None
        self._restyle()

    # ── drawing ──────────────────────────────────────────────────────────
    def _restyle(self):
        self.setStyleSheet(
            f"QLabel{{background:{C.PRIMARY_DIM};color:#ffffff;"
            f"border-radius:{self._size // 2}px;"
            f"font-size:{max(10, int(self._size * 0.34))}px;"
            f"font-weight:700;border:none;}}")

    def set_initials(self, name: str):
        parts = [p for p in str(name or "").split() if p]
        self._initials = ("".join(p[0] for p in parts[:2]) or "?").upper()
        if self._pixmap is None:
            self.setText(self._initials)

    def set_image(self, data: bytes | None):
        if not data:
            self._pixmap = None
            self.setPixmap(QPixmap())
            self.setText(self._initials)
            self._restyle()
            return
        # Clipped with a painter rather than by a stylesheet: a CSS radius
        # leaves the corners of the image showing through on some platforms,
        # which looks like a rendering fault rather than a round photo.
        canvas = round_pixmap(data, self._size)
        if canvas is None:
            return
        self._pixmap = canvas
        self.setText("")
        self.setStyleSheet("QLabel{background:transparent;border:none;}")
        self.setPixmap(canvas)

    # ── the whole point ──────────────────────────────────────────────────
    def show_person(self, employee_id: str | None, name: str = ""):
        """Draw this person: initials at once, their photo when it arrives."""
        self._employee_id = str(employee_id) if employee_id else None
        self.set_initials(name)
        self.set_image(None)
        if not self._employee_id:
            return

        wanted = self._employee_id
        request(wanted, lambda data: self._arrived(wanted, data))

    def _arrived(self, employee_id: str, data):
        # The widget may have been given a different person while the request
        # was in the air — a list that scrolled, or a chat that moved on.
        if employee_id != self._employee_id:
            return
        try:
            self.set_image(data)
        except RuntimeError:
            # The widget was destroyed while its photo was arriving. Nothing
            # to draw on, and nothing worth reporting.
            pass


class ClickableAvatar(Avatar):
    """An Avatar that behaves like a button — for the header portrait."""

    clicked = Signal()

    def __init__(self, size: int = 40, parent=None):
        super().__init__(size, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)
