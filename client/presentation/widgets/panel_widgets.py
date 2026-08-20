"""
Employee panel ke reusable widgets — stat card (sparkline ke saath),
sidebar nav item, activity row, section header.
"""

from __future__ import annotations

from itertools import count

import random
from collections import deque

from PySide6.QtCore import Qt, QPointF, QSize
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QBrush, QLinearGradient
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget, QSizePolicy
)

from client.presentation.theme import C, R, R_SM, Radius, Type
from client.presentation.widgets import icons as _icons
from client.presentation.theme import dot_style


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

        # A LUCIDE GLYPH, NOT AN EMOJI.
        #
        # `icon` may still be an emoji from a caller not yet migrated; that
        # falls back to drawing it as text, so nothing breaks while the last
        # few are moved over. A known icon name is drawn as a stroke glyph
        # tinted with the card's own accent.
        badge = QLabel()
        badge.setFixedSize(40, 40)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background:{accent_bg};border-radius:{Radius.CONTROL}px;"
            f"font-size:{Type.SECTION}px;border:none;color:{accent};")
        if _icons.known(icon):
            badge.setPixmap(_icons.pixmap(icon, 20, accent))
        else:
            badge.setText(icon)

        text_col = QVBoxLayout()
        text_col.setSpacing(3)

        self._title = QLabel(title)
        self._title.setStyleSheet(
            f"color:{C.TEXT_MUTED};font-size:{Type.MICRO}px;"
            f"font-weight:500;border:none;")
        self._value = QLabel("—")
        # The number is the point of the card, so it is the display step.
        self._value.setStyleSheet(
            f"color:{C.TEXT};font-size:{Type.HEADING}px;"
            f"font-weight:600;border:none;")
        text_col.addWidget(self._title)
        text_col.addWidget(self._value)

        head.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        head.addLayout(text_col, 1)
        root.addLayout(head)

        self._sub = QLabel("")
        self._sub.setStyleSheet(
            f"color:{C.TEXT_DIM};font-size:12px;border:none;padding-top:4px;"
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
            f"color:{color or self._accent};font-size:24px;font-weight:800;border:none;"
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
        badge = QLabel()
        badge.setFixedSize(28, 28)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background:{accent_bg};border-radius:{Radius.CONTROL}px;"
            f"font-size:{Type.SMALL}px;border:none;color:{accent};")
        if _icons.known(icon):
            badge.setPixmap(_icons.pixmap(icon, 16, accent))
        else:
            badge.setText(icon)
        name = QLabel(label)
        name.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:{Type.MICRO}px;"
                           f"font-weight:500;border:none;")
        top.addWidget(badge)
        top.addWidget(name)
        top.addStretch()
        root.addLayout(top)

        self._value = QLabel("—")
        self._value.setStyleSheet(f"color:{C.TEXT};font-size:{Type.HEADING}px;"
                                  f"font-weight:600;border:none;")
        root.addWidget(self._value)

        self._caption = QLabel("")
        self._caption.setStyleSheet(f"color:{C.TEXT_DIM};font-size:{Type.MICRO}px;border:none;")
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
        super().__init__(f"   {label}" if _icons.known(icon)
                         else f"  {icon}   {label}", parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(52)
        if _icons.known(icon):
            self.setIcon(_icons.icon(icon, 18,
                                     C.RED if danger else C.TEXT_MUTED))
            self.setIconSize(QSize(18, 18))
        bg, border, fg, hover = (
            (C.RED_BG, C.DANGER_BORDER, C.RED, C.DANGER_BORDER) if danger
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
        badge = QLabel()
        badge.setFixedSize(40, 40)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(f"background:{accent_bg};border-radius:{Radius.CONTROL}px;"
                            f"font-size:{Type.SECTION}px;border:none;color:{accent};")
        if _icons.known(icon):
            badge.setPixmap(_icons.pixmap(icon, 20, accent))
        else:
            badge.setText(icon)
        name = QLabel(title)
        name.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:{Type.SMALL}px;"
                           f"font-weight:500;border:none;")
        top.addWidget(badge)
        top.addWidget(name)
        top.addStretch()
        root.addLayout(top)

        self._value = QLabel("—")
        self._value.setStyleSheet(f"color:{C.TEXT};font-size:{Type.HEADING}px;"
                                  f"font-weight:600;border:none;")
        root.addWidget(self._value)

        self._sub = QLabel("")
        self._sub.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:{Type.SMALL}px;border:none;")
        root.addWidget(self._sub)

        self._detail = QLabel("")
        self._detail.setStyleSheet(f"color:{C.TEXT_DIM};font-size:{Type.MICRO}px;border:none;")
        root.addWidget(self._detail)
        root.addStretch()

    def set(self, value, sub="", detail="", color=None):
        self._value.setText(str(value))
        self._value.setStyleSheet(
            f"color:{color or self._accent};font-size:24px;font-weight:800;border:none;"
        )
        if sub:
            self._sub.setText(sub)
        # The dot in front of this line was a character; the line is a
        # detail, not a status, so it simply reads better without one.
        self._detail.setText(detail or "")


class NavButton(QPushButton):
    """Sidebar navigation item."""

    def __init__(self, icon: str, label: str, parent=None):
        self._icon_name = icon
        # "&&", NOT "&". Qt reads a single ampersand in a button's text as a
        # keyboard mnemonic and does not draw it — so "Help & Support" was
        # rendered "Help  Support" and "Teams & Chat" as "Teams  Chat", in
        # both sidebars, for as long as those entries have existed.
        label = label.replace("&", "&&")
        text = f"   {label}" if _icons.known(icon) else f"   {icon}    {label}"
        super().__init__(text, parent)
        self._base_text = text
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(44)
        if _icons.known(icon):
            self.setIcon(_icons.icon(icon, 18, C.TEXT_MUTED))
            self.setIconSize(QSize(18, 18))
            # The pixmap is baked at one colour, so the active row would keep
            # a muted glyph beside its brighter text without this.
            self.toggled.connect(self._retint)
        # A soft field and a blue rule down the left edge, matching the admin
        # console. It used to fill the whole row with solid accent — a large
        # block of colour that drew more attention than the page it points at.
        self.setStyleSheet(f"""
            QPushButton {{
                background:transparent; border:none;
                border-left:2px solid transparent;
                border-radius:{Radius.CONTROL}px;
                color:{C.TEXT_MUTED}; font-size:{Type.BODY}px; font-weight:500;
                text-align:left; padding-left:12px;
            }}
            QPushButton:hover {{ background:{C.HOVER}; color:{C.TEXT}; }}
            QPushButton:checked {{
                background:{C.ACTIVE}; color:{C.TEXT};
                border-left:2px solid {C.PRIMARY}; font-weight:600;
            }}
            /* Something is waiting here. Red, because a count nobody notices
               is the same as no count. */
            QPushButton[unread="true"] {{ color:{C.RED}; font-weight:600; }}
        """)

    def _retint(self, checked: bool) -> None:
        self.setIcon(_icons.icon(self._icon_name, 18,
                                 C.TEXT if checked else C.TEXT_MUTED))

    def set_badge(self, count: int) -> None:
        """Show an unread count beside the label, or clear it at zero.

        The count goes in the button's own text rather than a floating label:
        a separate widget positioned over a button is the kind of thing that
        drifts when the sidebar is resized, and this never can.
        """
        count = max(0, int(count or 0))
        self.setText(self._base_text if count == 0
                     else f"{self._base_text}   ({count if count < 100 else '99+'})")

        # AND IT TURNS RED. The count was drawn in the same muted grey as the
        # label, so it read as part of the name rather than as something
        # waiting — reported after it started working: "3 dikha, but dhyan hi
        # nahi gaya."
        self.setProperty("unread", "true" if count else "false")
        self.style().unpolish(self)
        self.style().polish(self)


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

        # Drawn rather than typed — see the console's dot for why. Boxed to
        # the old 14px width so every row in this list stays on one grid.
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(dot_style(8, color))

        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet(f"color:{C.TEXT};font-size:13px;font-weight:600;border:none;")
        s = QLabel(subtitle)
        s.setStyleSheet(f"color:{C.TEXT_DIM};font-size:12px;border:none;")
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
        t.setStyleSheet(f"color:{C.TEXT};font-size:24px;font-weight:800;")
        col.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setStyleSheet(f"color:{C.TEXT_MUTED};font-size:13px;")
            col.addWidget(s)


class Card(QFrame):
    """Generic content card.

    THE STYLE IS SCOPED TO THIS FRAME. It used to be a plain `QFrame{...}`
    rule, which in Qt also styles every QFrame inside — so a divider within a
    card was given the card's own border and 14px radius and drew itself as a
    small glowing box. The same fault was found and fixed in the admin panel
    long before; this copy kept it.
    """

    _uid = count(1)

    def __init__(self, parent=None):
        super().__init__(parent)
        name = f"etsPanelCard{next(Card._uid)}"
        self.setObjectName(name)
        self.setStyleSheet(
            f"QFrame#{name}{{background:{C.CARD};border:1px solid {C.BORDER};"
            f"border-radius:{R}px;}}"
        )


# The console uses 52px rows; this is the same number, so a table on the
# employee side and a table on the admin side are the same height per row.
# How often a page re-reads what it is showing, while somebody is looking at
# it. Thirty seconds: long enough that it costs nothing, short enough that a
# decision made in the other panel appears without anybody pressing anything.
LIVE_REFRESH_MS = 30_000


def keep_fresh(page, seconds: int | None = None) -> None:
    """Re-read this page's data on a timer, but ONLY while it is on screen.

    WHY PAGES NEEDED THIS. A page fetched its data when it was shown and then
    never again. So an administrator approved a leave and the employee went
    on seeing "Pending"; an administrator forced a logout and the list went
    on saying "Online". The data was right on the server the whole time —
    the screen was simply the last answer anybody had asked for.

    The workaround people found was to leave the page and come back, which
    fires showEvent and re-reads. That is the bug reported, in the words it
    was reported in: "page change karke aane pe ho raha hai".

    ONLY WHILE VISIBLE, and that is the point. A hidden page polling in the
    background is a request nobody is waiting for, multiplied by every page
    in the application and every employee running it. The chat poll already
    works this way for the same reason.

    The page keeps its own timer, so hiding stops it and showing starts it
    again; nothing has to remember to clean it up.
    """
    from PySide6.QtCore import QTimer

    interval = LIVE_REFRESH_MS if seconds is None else seconds * 1000
    timer = QTimer(page)
    timer.setInterval(interval)
    timer.timeout.connect(page.refresh)
    page._live_timer = timer

    original_show = page.showEvent
    original_hide = page.hideEvent

    def showEvent(event):
        original_show(event)
        timer.start()

    def hideEvent(event):
        timer.stop()
        original_hide(event)

    page.showEvent = showEvent
    page.hideEvent = hideEvent


ROW_HEIGHT = 52


def fit_columns(table, stretch: int | None = None, pad: int = 30) -> None:
    """Size every column to the widest thing actually in it.

    THE SAME MEASUREMENT THE ADMIN PANEL MAKES, and it exists twice because
    the two panels share no base class. Qt's ResizeToContents asks the item
    delegate, and the delegate knows nothing about the stylesheet — these
    tables pad cells by twelve pixels a side, twenty-four the delegate never
    counts. On the admin side that drew "₹36,666." with the paise cut off.

    `stretch` takes the slack ONLY when there is slack: Qt's Stretch divides
    the available width without regard to what a column needs, so on a table
    wider than its window it shrinks the very column it was asked to help.
    """
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QHeaderView

    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    metrics = QFontMetrics(table.font())

    # ROW HEIGHT, WHILE WE ARE HERE. Every table that calls this also wants
    # rows a widget can fit inside — a status chip needs 21px and Qt's
    # default row leaves it nine once the cell padding is taken out. Set on
    # the header so it applies to rows added later, and on the existing rows
    # so it applies to the ones already there.
    table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
    for _row in range(table.rowCount()):
        table.setRowHeight(_row, ROW_HEIGHT)

    for column in range(table.columnCount()):
        widest = 0
        head = table.horizontalHeaderItem(column)
        if head is not None:
            widest = int(metrics.horizontalAdvance(head.text().upper()) * 1.25)
        for row in range(table.rowCount()):
            item = table.item(row, column)
            if item is not None:
                widest = max(widest, metrics.horizontalAdvance(item.text()))
            else:
                widget = table.cellWidget(row, column)
                if widget is not None:
                    widest = max(widest, widget.sizeHint().width())
        table.setColumnWidth(column, max(72, widest + pad))

    if stretch is not None and 0 <= stretch < table.columnCount():
        total = sum(table.columnWidth(c) for c in range(table.columnCount()))
        if table.viewport().width() > 0 and total < table.viewport().width():
            header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
