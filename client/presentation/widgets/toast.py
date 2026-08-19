"""
A message that appears, says one thing, and goes away.

WHAT THIS REPLACES. Every page had its own way of telling somebody what just
happened: a QLabel that turns amber and stays amber until the next action, a
QMessageBox that has to be dismissed before anything else can be done, or —
most often — nothing at all, leaving a saved change indistinguishable from a
change that failed silently.

WHEN NOT TO USE IT. A toast is for something that WORKED. It disappears, so
it cannot carry anything the person has to act on: an error they must read,
a rule they broke, a decision they have to make. Those belong beside the
control that caused them, where they stay put. The failure mode this avoids
is the one where the only explanation of why a save failed vanishes after
four seconds.

It parents itself to the window rather than to the widget that raised it, so
it survives a page being swapped underneath it and always appears in the same
corner — a message that shows up somewhere different each time is read as a
new kind of thing each time.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel

from client.presentation import theme as _theme
from client.presentation.theme import C, Radius, Space, Type
from client.presentation.widgets import icons as _icons


_MARGIN = 24          # from the window's bottom-right corner
_GAP = 10             # between stacked toasts
_LIFETIME_MS = 3600
_FADE_MS = 180

# Toasts currently on screen, oldest first, per window.
_live: dict[int, list] = {}


class Toast(QFrame):
    """One message. Use `show_toast()` rather than building this directly."""

    def __init__(self, parent, text: str, kind: str = "success"):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFrameShape(QFrame.Shape.NoFrame)

        fg, bg = _theme.status_colors(
            {"success": "approved", "error": "rejected",
             "warning": "pending", "info": "info"}.get(kind, "neutral"))

        self.setStyleSheet(
            f"QFrame{{background:{bg};border:1px solid {fg};"
            f"border-radius:{Radius.CONTROL}px;}}")

        row = QHBoxLayout(self)
        row.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        row.setSpacing(Space.SM)

        icon = QLabel()
        icon.setPixmap(_icons.pixmap(
            {"success": "circle-check", "error": "x",
             "warning": "triangle-alert", "info": "info"}.get(kind, "info"),
            16, fg))
        icon.setStyleSheet("background:transparent;border:none;")
        label = QLabel(text)
        label.setWordWrap(True)
        label.setMaximumWidth(360)
        label.setStyleSheet(
            f"color:{C.TEXT};font-size:{Type.SMALL}px;"
            f"background:transparent;border:none;")

        row.addWidget(icon)
        row.addWidget(label, 1)

        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._effect.setOpacity(0.0)
        self.adjustSize()

    # ── appearing and leaving ─────────────────────────────────────────
    def _fade(self, to: float, then=None):
        animation = QPropertyAnimation(self._effect, b"opacity", self)
        animation.setDuration(_FADE_MS)
        animation.setEndValue(to)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        if then is not None:
            animation.finished.connect(then)
        # Held on the instance: a QPropertyAnimation that goes out of scope is
        # collected mid-flight and the toast simply never appears.
        self._animation = animation
        animation.start()

    def dismiss(self):
        stack = _live.get(id(self.window()), [])
        if self in stack:
            stack.remove(self)
        self._fade(0.0, then=self.deleteLater)
        _restack(self.window())

    def mousePressEvent(self, event):
        # Clicking one dismisses it. Somebody who has read it should not have
        # to wait for it.
        self.dismiss()
        super().mousePressEvent(event)


def _restack(window):
    """Sit the live toasts above one another in the bottom-right corner."""
    stack = _live.get(id(window), [])
    y = window.height() - _MARGIN
    for toast in reversed(stack):
        y -= toast.height()
        toast.move(window.width() - toast.width() - _MARGIN, y)
        y -= _GAP


def show_toast(parent, text: str, kind: str = "success", *, ms: int = _LIFETIME_MS):
    """Say `text` in the corner of `parent`'s window for a few seconds.

    Silently does nothing without a window to sit in — a toast is never worth
    an exception in the path of something that has already succeeded.
    """
    if parent is None:
        return None
    window = parent.window()
    if window is None:
        return None

    toast = Toast(window, text, kind)
    stack = _live.setdefault(id(window), [])
    # THREE AT A TIME. Six identical toasts stacked up the side of the window
    # is not six times the information; the oldest goes to make room.
    while len(stack) >= 3:
        stack[0].dismiss()
    stack.append(toast)

    _restack(window)
    toast.show()
    toast.raise_()
    toast._fade(1.0)

    QTimer.singleShot(ms, lambda: toast.dismiss() if toast.parent() else None)
    return toast
