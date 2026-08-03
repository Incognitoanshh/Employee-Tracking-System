"""
Employee panel ke reusable widgets — stat card (sparkline ke saath),
sidebar nav item, activity row, section header.
"""

from __future__ import annotations

import random
from collections import deque

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QBrush, QLinearGradient
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget, QSizePolicy
)

from client.presentation.theme import C, R, R_SM


class Sparkline(QWidget):
    """
    Chhota trend graph — stat cards ke neeche.

    Ye ek VISUAL indicator hai (activity trend), koi precise chart nahi.
    Values push_value() se aati hain; koi data na ho to flat line dikhti hai
    (fake random data kabhi nahi banate — warna employee ko lagta kuch ho
    raha hai jabki kuch nahi ho raha).
    """

    def __init__(self, color: str, points: int = 40, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._values: deque[float] = deque([0.0] * points, maxlen=points)
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def push_value(self, value: float) -> None:
        self._values.append(max(0.0, float(value)))
        self.update()

    def set_series(self, values: list[float]) -> None:
        n = self._values.maxlen or 40
        series = list(values)[-n:]
        if len(series) < n:
            series = [0.0] * (n - len(series)) + series
        self._values = deque(series, maxlen=n)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        values = list(self._values)
        if not values:
            return

        w, h = self.width(), self.height()
        top, bottom = 4.0, h - 3.0
        peak = max(values) or 1.0
        step = w / max(1, len(values) - 1)

        pts = [
            QPointF(i * step, bottom - (v / peak) * (bottom - top))
            for i, v in enumerate(values)
        ]

        # Fill
        fill = QPainterPath()
        fill.moveTo(0, h)
        for p in pts:
            fill.lineTo(p)
        fill.lineTo(w, h)
        fill.closeSubpath()

        grad = QLinearGradient(0, 0, 0, h)
        c1 = QColor(self._color); c1.setAlpha(70)
        c2 = QColor(self._color); c2.setAlpha(0)
        grad.setColorAt(0, c1)
        grad.setColorAt(1, c2)
        painter.fillPath(fill, QBrush(grad))

        # Line
        line = QPainterPath()
        line.moveTo(pts[0])
        for p in pts[1:]:
            line.lineTo(p)
        pen = QPen(self._color, 1.8)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(line)
        painter.end()


class StatCard(QFrame):
    """Icon + title + big value + subtitle + optional sparkline."""

    def __init__(self, icon: str, title: str, accent: str, accent_bg: str,
                 sparkline: bool = True, parent=None):
        super().__init__(parent)
        self._accent = accent
        self.setStyleSheet(
            f"QFrame{{background:{C.CARD};border:1px solid {C.BORDER};"
            f"border-radius:{R}px;}}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(13)

        badge = QLabel(icon)
        badge.setFixedSize(42, 42)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background:{accent_bg};border-radius:21px;font-size:18px;border:none;"
        )

        text_col = QVBoxLayout()
        text_col.setSpacing(3)

        self._title = QLabel(title)
        self._title.setStyleSheet(
            f"color:{C.TEXT_MUTED};font-size:12px;font-weight:600;border:none;"
        )
        self._value = QLabel("—")
        self._value.setStyleSheet(
            f"color:{accent};font-size:21px;font-weight:800;border:none;"
        )
        text_col.addWidget(self._title)
        text_col.addWidget(self._value)

        head.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        head.addLayout(text_col, 1)
        root.addLayout(head)

        self._sub = QLabel("")
        self._sub.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:11px;border:none;padding-top:4px;"
        )
        self._sub.setWordWrap(True)
        root.addWidget(self._sub)

        self._spark = None
        if sparkline:
            root.addSpacing(4)
            self._spark = Sparkline(accent)
            self._spark.setStyleSheet("border:none;background:transparent;")
            root.addWidget(self._spark)

        root.addStretch()

    def set_value(self, text: str, color: str | None = None) -> None:
        self._value.setText(text)
        self._value.setStyleSheet(
            f"color:{color or self._accent};font-size:21px;font-weight:800;border:none;"
        )

    def set_subtitle(self, text: str) -> None:
        self._sub.setText(text)

    def push_point(self, value: float) -> None:
        if self._spark:
            self._spark.push_value(value)

    def set_series(self, values: list[float]) -> None:
        if self._spark:
            self._spark.set_series(values)


class MiniStat(QFrame):
    """
    "Today's Summary" strip ka compact card — icon + label upar, bada value,
    chhota caption, aur neeche sparkline.
    """

    def __init__(self, icon: str, label: str, accent: str, accent_bg: str, parent=None):
        super().__init__(parent)
        self._accent = accent
        self.setStyleSheet(
            f"QFrame{{background:{C.CARD};border:1px solid {C.BORDER};border-radius:{R_SM}px;}}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(9)
        badge = QLabel(icon)
        badge.setFixedSize(26, 26)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background:{accent_bg};border-radius:7px;font-size:13px;border:none;"
        )
        name = QLabel(label)
        name.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:12px;font-weight:600;border:none;")
        top.addWidget(badge)
        top.addWidget(name)
        top.addStretch()
        root.addLayout(top)

        self._value = QLabel("—")
        self._value.setStyleSheet(f"color:{C.TEXT};font-size:26px;font-weight:800;border:none;")
        root.addWidget(self._value)

        self._caption = QLabel("")
        self._caption.setStyleSheet(f"color:{C.TEXT_DIM};font-size:11px;border:none;")
        root.addWidget(self._caption)

        root.addSpacing(2)
        self._spark = Sparkline(accent, 46)
        self._spark.setStyleSheet("border:none;background:transparent;")
        root.addWidget(self._spark)

    def set_value(self, text, caption=""):
        self._value.setText(str(text))
        if caption:
            self._caption.setText(caption)

    def push_point(self, value): self._spark.push_value(value)
    def set_series(self, values): self._spark.set_series(values)


class QuickAction(QPushButton):
    """Quick Actions bar ka button."""

    def __init__(self, icon: str, label: str, danger: bool = False, parent=None):
        super().__init__(f"  {icon}   {label}", parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(52)
        bg, border, fg, hover = (
            ("#3f1218", "#7f1d1d", "#fca5a5", "#7f1d1d") if danger
            else (C.CARD, C.BORDER, C.TEXT, C.CARD_HOVER)
        )
        self.setStyleSheet(f"""
            QPushButton {{
                background:{bg}; border:1px solid {border}; border-radius:{R_SM}px;
                color:{fg}; font-size:13px; font-weight:600;
            }}
            QPushButton:hover {{ background:{hover}; }}
        """)


class HeaderAction(QPushButton):
    """Header ke Refresh / Take Screenshot / Sync Now buttons."""

    def __init__(self, icon: str, label: str, parent=None):
        super().__init__(f"  {icon}   {label}", parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(46)
        self.setStyleSheet(f"""
            QPushButton {{
                background:{C.CARD}; border:1px solid {C.BORDER}; border-radius:{R_SM}px;
                color:{C.TEXT}; font-size:13px; font-weight:600; padding:0 16px;
            }}
            QPushButton:hover {{ background:{C.CARD_HOVER}; border-color:{C.PRIMARY}; }}
            QPushButton:disabled {{ color:{C.TEXT_DIM}; }}
        """)


class StatusTile(QFrame):
    """Tracking / Activity / Internet / Upload — icon + title + big value + 2 lines."""

    def __init__(self, icon: str, title: str, accent: str, accent_bg: str, parent=None):
        super().__init__(parent)
        self._accent = accent
        self.setStyleSheet(
            f"QFrame{{background:{C.CARD};border:1px solid {C.BORDER};border-radius:{R}px;}}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(13)
        badge = QLabel(icon)
        badge.setFixedSize(44, 44)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(f"background:{accent_bg};border-radius:11px;font-size:19px;border:none;")
        name = QLabel(title)
        name.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:13px;font-weight:600;border:none;")
        top.addWidget(badge)
        top.addWidget(name)
        top.addStretch()
        root.addLayout(top)

        self._value = QLabel("—")
        self._value.setStyleSheet(f"color:{accent};font-size:23px;font-weight:800;border:none;")
        root.addWidget(self._value)

        self._sub = QLabel("")
        self._sub.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:12px;border:none;")
        root.addWidget(self._sub)

        self._detail = QLabel("")
        self._detail.setStyleSheet(f"color:{C.TEXT_DIM};font-size:11px;border:none;")
        root.addWidget(self._detail)
        root.addStretch()

    def set(self, value, sub="", detail="", color=None):
        self._value.setText(str(value))
        self._value.setStyleSheet(
            f"color:{color or self._accent};font-size:23px;font-weight:800;border:none;"
        )
        if sub:
            self._sub.setText(sub)
        self._detail.setText(f"●  {detail}" if detail else "")


class NavButton(QPushButton):
    """Sidebar navigation item."""

    def __init__(self, icon: str, label: str, parent=None):
        super().__init__(f"   {icon}    {label}", parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(46)
        self.setStyleSheet(f"""
            QPushButton {{
                background:transparent; border:none; border-radius:{R_SM}px;
                color:{C.TEXT_MUTED}; font-size:14px; font-weight:600;
                text-align:left; padding-left:12px;
            }}
            QPushButton:hover {{ background:{C.ELEVATED}; color:{C.TEXT}; }}
            QPushButton:checked {{
                background:{C.PRIMARY_DIM}; color:#ffffff;
            }}
        """)


class ActivityRow(QFrame):
    """Recent Activity ka ek row — dot + title + subtitle + time."""

    def __init__(self, color: str, title: str, subtitle: str, time_text: str,
                 parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame{{background:transparent;border:none;"
            f"border-bottom:1px solid {C.BORDER_SOFT};}}"
            f"QFrame:hover{{background:{C.CARD_HOVER};}}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 11, 14, 11)
        row.setSpacing(14)

        dot = QLabel("●")
        dot.setFixedWidth(14)
        dot.setStyleSheet(f"color:{color};font-size:14px;border:none;")

        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet(f"color:{C.TEXT};font-size:13px;font-weight:600;border:none;")
        s = QLabel(subtitle)
        s.setStyleSheet(f"color:{C.TEXT_DIM};font-size:11px;border:none;")
        col.addWidget(t)
        col.addWidget(s)

        when = QLabel(time_text)
        when.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:12px;border:none;")
        when.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(col, 1)
        row.addWidget(when, 0)


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 14)
        col.setSpacing(3)
        t = QLabel(title)
        t.setStyleSheet(f"color:{C.TEXT};font-size:22px;font-weight:800;")
        col.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:13px;")
            col.addWidget(s)


class Card(QFrame):
    """Generic content card."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame{{background:{C.CARD};border:1px solid {C.BORDER};"
            f"border-radius:{R}px;}}"
        )
