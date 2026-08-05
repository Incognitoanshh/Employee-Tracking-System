from __future__ import annotations

import ast
import requests
from datetime import date, datetime
from datetime import datetime, timezone, timedelta
from PySide6.QtCore    import Qt, QThread, Signal, QDate, QTimer
from client.presentation.windows.screenshot_preview_window import ScreenshotPreviewWindow
from PySide6.QtGui     import QFont, QColor, QAction, QIcon
from PySide6.QtWidgets import (
    QSystemTrayIcon,
    QMenu,
    QScrollArea,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QSizePolicy
)

from client.application.managers.session_manager import SessionManager
from client.infrastructure.database.database import Database
from client.application.schedulers.scheduler_service import SchedulerService
from client.application.managers.screenshot_manager import ScreenshotManager
from client.application.managers.idle_tracker import IdleTracker
from client.core.config import API_BASE_URL, APP_VERSION


# ──────────────────────────────────────────────────────────────────────────────
#  Design tokens — single source of truth for the whole admin panel
# ──────────────────────────────────────────────────────────────────────────────
C = {
    "bg_app":         "#0a0e16",
    "bg_sidebar":     "#0b0f1a",
    "bg_surface":     "#111827",
    "bg_surface_alt": "#0f172a",
    "bg_elevated":    "#18222f",
    "border":         "#1e293b",
    "border_light":   "#27344a",
    "text_primary":   "#f1f5f9",
    "text_secondary": "#94a3b8",
    "text_muted":     "#64748b",
    "accent":         "#2563eb",
    "accent_hover":   "#3b82f6",
    "accent_pressed": "#1d4ed8",
    "accent_soft":    "rgba(37, 99, 235, 0.16)",
    "success":        "#22c55e",
    "warning":        "#f59e0b",
    "danger":         "#ef4444",
    "danger_strong":  "#dc2626",
    "danger_soft":    "rgba(239, 68, 68, 0.14)",
    "warning_soft":   "rgba(245, 158, 11, 0.14)",
}

ACCENTS = {
    "blue":   "#2563eb",
    "green":  "#22c55e",
    "amber":  "#f59e0b",
    "violet": "#8b5cf6",
    "cyan":   "#06b6d4",
    "slate":  "#64748b",
    "red":    "#ef4444",
}

PAGES = [
    {"key": "dashboard",   "icon": "📊", "title": "Dashboard",
     "subtitle": "Live overview of your workforce and activity."},
    {"key": "config",      "icon": "⚙️", "title": "Configuration",
     "subtitle": "Set screenshot intervals, idle thresholds and upload frequency — globally or per employee."},
    {"key": "employees",   "icon": "👥", "title": "Employees",
     "subtitle": "Manage accounts, roles and live status."},
    {"key": "attendance",  "icon": "📅", "title": "Attendance",
     "subtitle": "Track login, logout times and shift hours."},
    {"key": "screenshots", "icon": "📸", "title": "Screenshots",
     "subtitle": "Browse captured screenshots by employee and date."},
    {"key": "logs",        "icon": "📝", "title": "Audit Logs",
     "subtitle": "Detailed activity history for compliance and review."},
]


from client.core.time_ist import IST  # single source of truth


def _parse_server_ts(ts) -> datetime | None:
    """
    Server ke kisi bhi timestamp format ko aware UTC datetime me badlo.

    BUG FIX: panel me 3 alag jagah alag-alag parsing thi, aur do jagah
    `ts = ...` assignment galti se `if dt.tzinfo is None:` block ke ANDAR
    tha — matlab tz-aware timestamp aane par conversion hoti hi nahi thi.
    Abhi ye isliye chhupa hua tha kyunki db.js naive strings bhejta hai.
    """
    raw = str(ts or "").strip()
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
        # BUG FIX (tables me kuch timestamps raw dikhte the):
        # Python 3.10 ka `fromisoformat` sirf 3 ya 6 fractional digits
        # accept karta hai — 3.11+ lenient hai. Postgres trailing zeros
        # trim kar deta hai, to values aksar 5 digit ki aati hain
        # (jaise "2026-08-03 06:48:13.34181"). Build 3.10 pe hota hai,
        # is liye un rows pe parse fail hoti thi aur _fmt_ts raw string
        # laut deta tha — usi table me kuch rows "03 Aug 2026 09:44 PM"
        # aur kuch "2026-08-03 06:48:13.34181" dikhte the.
        #
        # Dev machines pe 3.11+ hone ki wajah se ye kabhi reproduce nahi
        # hota tha, sirf built app me dikhta tha.
        #
        # Sirf truncate karna kaafi nahi — 6 tak PAD karna zaroori hai.
        cleaned = f"{head}.{frac[:6].ljust(6, '0')}{offset}"
    try:
        dt = datetime.fromisoformat(cleaned)
    except Exception:
        return None
    # Server naive strings UTC me likhta hai.
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _fmt_ts(ts, fallback="—") -> str:
    """Server timestamp -> IST display string."""
    dt = _parse_server_ts(ts)
    if dt is None:
        return str(ts) if ts else fallback
    return dt.astimezone(IST).strftime("%d %b %Y %I:%M:%S %p")


def _fmt_relative(ts) -> str:
    """'Just now' / '5 min ago' / absolute date."""
    dt = _parse_server_ts(ts)
    if dt is None:
        return str(ts) if ts else "—"
    diff = int((datetime.now(timezone.utc) - dt).total_seconds())
    if diff < 0:
        diff = 0
    if diff < 60:
        return "Just now"
    if diff < 3600:
        return f"{diff // 60} min ago"
    if diff < 86400:
        return f"{diff // 3600} hr ago"
    return dt.astimezone(IST).strftime("%d %b %Y %I:%M %p")


def _hex_to_rgb(h: str) -> str:
    h = h.lstrip("#")
    return f"{int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}"


def _global_stylesheet() -> str:
    return f"""
    QMainWindow {{ background: {C['bg_app']}; }}
    QWidget {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 13px; color: {C['text_primary']}; }}
    QLabel {{ background: transparent; }}

    QWidget#sidebar {{ background: {C['bg_sidebar']}; border-right: 1px solid {C['border']}; }}
    QFrame#topHeader {{ background: {C['bg_app']}; border-bottom: 1px solid {C['border']}; }}

    /* Inputs */
    QLineEdit, QComboBox, QDateEdit, QSpinBox {{
        background: {C['bg_surface_alt']};
        border: 1px solid {C['border']};
        border-radius: 8px;
        padding: 7px 10px;
        color: {C['text_primary']};
        selection-background-color: {C['accent']};
    }}
    QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus {{
        border: 1px solid {C['accent']};
    }}
    QLineEdit::placeholder {{ color: {C['text_muted']}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {C['bg_surface']};
        border: 1px solid {C['border_light']};
        border-radius: 8px;
        color: {C['text_primary']};
        selection-background-color: {C['accent']};
        outline: none;
        padding: 4px;
    }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; }}

    /* BUG FIX: QCheckBox::indicator ka koi rule tha hi nahi. Native indicator
       is dark theme pe bilkul dikhta hi nahi tha — "Verbose logging" ke aage
       khaali jagah dikhti thi aur pata hi nahi chalta tha ki wo ON hai ya OFF. */
    QCheckBox {{ background: transparent; spacing: 8px; }}
    QCheckBox::indicator {{
        width: 20px; height: 20px;
        border: 1px solid {C['border_light']};
        border-radius: 6px;
        background: {C['bg_surface_alt']};
    }}
    QCheckBox::indicator:hover {{ border: 1px solid {C['accent']}; }}
    QCheckBox::indicator:checked {{
        background: {C['accent']};
        border: 1px solid {C['accent']};
        image: none;
    }}

    QCalendarWidget QWidget {{ background: {C['bg_surface']}; color: {C['text_primary']}; }}
    QCalendarWidget QToolButton {{ background: transparent; color: {C['text_primary']}; padding: 4px; }}
    QCalendarWidget QAbstractItemView:enabled {{
        background: {C['bg_surface']}; color: {C['text_primary']};
        selection-background-color: {C['accent']}; selection-color: white;
    }}

    /* Tables */
    QTableWidget {{
        background: {C['bg_surface']};
        alternate-background-color: {C['bg_surface_alt']};
        gridline-color: transparent;
        border: 1px solid {C['border']};
        border-radius: 12px;
        color: {C['text_primary']};
        selection-background-color: {C['accent_soft']};
        selection-color: {C['text_primary']};
    }}
    /* Row separator halka rakha hai — border wali line har row pe bahut
       loud lagti thi. Zebra striping (alternate-background) hi structure
       de deti hai, uske upar full-contrast line shor banti hai. */
    QTableWidget::item {{
        padding: 10px 12px;
        border: none;
        border-bottom: 1px solid {C['bg_surface_alt']};
    }}
    QTableWidget::item:selected {{
        background: {C['accent_soft']};
        color: {C['text_primary']};
    }}
    QTableWidget::item:hover {{ background: {C['bg_elevated']}; }}
    QHeaderView::section {{
        background: {C['bg_app']};
        color: {C['text_muted']};
        padding: 12px 12px;
        border: none;
        border-bottom: 1px solid {C['border_light']};
        font-weight: 700;
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.6px;
    }}
    QTableCornerButton::section {{ background: {C['bg_surface_alt']}; border: none; }}

    /* Scrollbars */
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 2px; }}
    QScrollBar::handle:vertical {{ background: {C['border_light']}; border-radius: 4px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {C['text_muted']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px 4px; }}
    QScrollBar::handle:horizontal {{ background: {C['border_light']}; border-radius: 4px; min-width: 24px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    /* Buttons */
    QPushButton {{
        background: {C['bg_surface_alt']};
        border: 1px solid {C['border']};
        border-radius: 8px;
        padding: 7px 14px;
        color: {C['text_primary']};
        font-weight: 600;
    }}
    QPushButton:hover {{ background: {C['bg_elevated']}; }}
    QPushButton:disabled {{ color: {C['text_muted']}; background: {C['bg_surface']}; border-color: {C['border']}; }}

    QPushButton[variant="primary"] {{ background: {C['accent']}; border: 1px solid {C['accent']}; color: white; }}
    QPushButton[variant="primary"]:hover {{ background: {C['accent_hover']}; }}
    QPushButton[variant="primary"]:pressed {{ background: {C['accent_pressed']}; }}
    QPushButton[variant="primary"]:disabled {{ background: {C['border_light']}; color: {C['text_muted']}; border-color: {C['border_light']}; }}

    QPushButton[variant="secondary"] {{ background: {C['bg_surface_alt']}; border: 1px solid {C['border_light']}; color: {C['text_primary']}; }}
    QPushButton[variant="secondary"]:hover {{ background: {C['bg_elevated']}; }}

    QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; color: {C['text_secondary']}; }}
    QPushButton[variant="ghost"]:hover {{ background: rgba(255,255,255,0.05); color: {C['text_primary']}; }}

    QPushButton[variant="warning"] {{ background: {C['warning_soft']}; border: 1px solid rgba(245,158,11,0.4); color: {C['warning']}; }}
    QPushButton[variant="warning"]:hover {{ background: rgba(245,158,11,0.24); }}

    QPushButton[variant="danger"] {{ background: {C['danger_soft']}; border: 1px solid rgba(239,68,68,0.4); color: {C['danger']}; }}
    QPushButton[variant="danger"]:hover {{ background: rgba(239,68,68,0.24); }}

    QPushButton[variant="danger-solid"] {{ background: {C['danger_strong']}; border: 1px solid {C['danger_strong']}; color: white; }}
    QPushButton[variant="danger-solid"]:hover {{ background: #b91c1c; }}

    QPushButton[variant="navitem"] {{
        background: transparent;
        border: none;
        border-left: 3px solid transparent;
        text-align: left;
        padding: 11px 18px 11px 19px;
        color: {C['text_secondary']};
        font-weight: 600;
        font-size: 13px;
        border-radius: 0px;
    }}
    QPushButton[variant="navitem"]:hover {{ background: rgba(255,255,255,0.04); color: {C['text_primary']}; }}
    QPushButton[variant="navitem"]:checked {{ background: {C['accent_soft']}; border-left: 3px solid {C['accent']}; color: white; }}

    /* Dialogs / message boxes */
    QDialog {{ background: {C['bg_app']}; }}
    QMessageBox {{ background: {C['bg_surface']}; }}
    QMessageBox QLabel {{ color: {C['text_primary']}; }}
    QMessageBox QPushButton {{
        min-width: 84px; padding: 7px 14px; border-radius: 8px;
        background: {C['bg_surface_alt']}; border: 1px solid {C['border_light']}; color: {C['text_primary']};
    }}
    QMessageBox QPushButton:hover {{ background: {C['bg_elevated']}; }}

    QToolTip {{
        background: {C['bg_elevated']}; color: {C['text_primary']};
        border: 1px solid {C['border_light']}; padding: 4px 8px; border-radius: 6px;
    }}

    QListWidget {{
        background: {C['bg_surface_alt']}; border: 1px solid {C['border']}; border-radius: 12px;
        padding: 6px; outline: none;
    }}
    QListWidget::item {{ padding: 10px 12px; margin: 2px 0px; border-radius: 8px; color: {C['text_secondary']}; }}
    QListWidget::item:hover {{ background: {C['bg_elevated']}; color: {C['text_primary']}; }}
    """


def _track_worker(workers_list: list, w) -> None:
    """
    Worker (QThread) ko tracking list mein daalo, AUR jab wo complete ho
    jaye (success ya error, dono cases) to khud list se hata do.

    BUG FIX: Pehle sirf `_track_worker(self._workers, w)` hota tha, koi cleanup
    nahi tha. Dashboard tab har 5 second pe 3 naye worker banata hai
    (summary/feed/charts) — agar app kai ghante/din chalti rahe (jaisa
    real-world mein hota hai), ye list hazaron/lakhon purane (already
    finished) QThread objects se bhar jaati — genuine memory leak, jo
    lambe time tak chalne par app ko slow/heavy bana deta hai. Ab har
    worker khatam hote hi khud ko list se remove kar leta hai.
    """
    workers_list.append(w)

    # RACE FIX: cleanup ko QThread ke BUILT-IN `finished` se jodte hain, aur
    # remove karne ke liye deleteLater() ka intezaar karte hain — taaki
    # main thread pe queued `result` delivery ke waqt worker zinda rahe.
    # Pehle cleanup custom `finished` pe tha aur worker turant list se hat
    # jaata tha; GC use uda deta tha aur pending slot call drop ho jaati.
    def _cleanup():
        def _drop():
            if w in workers_list:
                workers_list.remove(w)
        # Ek event-loop turn ke baad hataao — tab tak `result`/`error`
        # deliver ho chuka hoga.
        QTimer.singleShot(0, _drop)

    w.finished.connect(_cleanup)


def _btn(text: str, variant: str = "secondary", height: int = 36, width: int | None = None) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("variant", variant)
    b.setFixedHeight(height)
    if width:
        b.setFixedWidth(width)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def _shadow(widget, blur=28, dy=8, alpha=70):
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    eff.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(eff)
    return eff


_CARD_UID = [0]


def _app_icon() -> QIcon | None:
    """assets/icon.png — frozen build me bundle ke andar, dev me repo root se."""
    import os
    import sys
    if getattr(sys, "frozen", False):
        base = os.path.join(sys._MEIPASS, "assets")
    else:
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))), "assets")
    path = os.path.join(base, "icon.png")
    return QIcon(path) if os.path.exists(path) else None


def _tune_table(table: "QTableWidget"):
    """Har tab ki table pe ek jaisa polish.

    Pehle har tab apni table alag alag configure karti thi, is liye row
    height, grid aur focus-rectangle teeno pages pe alag dikhte the.
    """
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(42)   # rows tang lag rahi thin
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)       # dotted focus box hata do
    table.setWordWrap(False)
    table.horizontalHeader().setHighlightSections(False)
    table.horizontalHeader().setFixedHeight(40)
    # Header default center-aligned hota hai lekin cells left-aligned hain —
    # dono match karne chahiye, warna column ka text header se khisak kar
    # dikhta hai.
    table.horizontalHeader().setDefaultAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return table


def _cell(text: str, *, mono: bool = False, muted: bool = False,
          align_right: bool = False, tooltip: str | None = None):
    """Ek styled read-only cell."""
    item = QTableWidgetItem(text)
    if mono:
        f = QFont("SF Mono, Menlo, Consolas, monospace")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPointSize(11)
        item.setFont(f)
    if muted:
        item.setForeground(QColor(C["text_muted"]))
    if align_right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    else:
        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    if tooltip:
        item.setToolTip(tooltip)
    return item


def _short_filename(name: str) -> str:
    """EMP002-1785773657379-1263bfcf-....enc -> '1263bfcf'

    Poora encrypted filename column me padha hi nahi jaata tha — 60+
    characters ka UUID blob. Employee prefix bhi bekaar hai kyunki
    Employee ka apna column hai. Sirf chhota unique handle dikhate hain;
    poora naam tooltip me rehta hai (aur download UserRole se hota hai).
    """
    stem = name[:-4] if name.endswith(".enc") else name
    parts = stem.split("-")
    if len(parts) >= 3:
        return parts[2][:8]
    return stem[:32]


def _card(padding: int = 0) -> QFrame:
    """Ek surface card.

    BUG FIX: pehle stylesheet plain `QFrame { ... }` tha. Qt me aisa selector
    sirf is widget pe nahi, iske ANDAR ke har QFrame child pe bhi apply hota
    hai. Card ke andar rakhi har `_divider()` (jo khud QFrame hai) ko card ka
    `border: 1px solid` + `border-radius: 14px` mil jaata tha — 1px ki patli
    line chaaron taraf border wala chamakta box ban jaati thi. Screenshot me
    yehi "white white lines" dikh rahi thi.

    Ab har card ka apna objectName hai aur selector `QFrame#cardN` — is se
    style sirf usi card pe lagta hai, children bilkul untouched rehte hain.
    """
    _CARD_UID[0] += 1
    name = f"etsCard{_CARD_UID[0]}"
    f = QFrame()
    f.setObjectName(name)
    f.setStyleSheet(
        f"QFrame#{name} {{ background: {C['bg_surface']};"
        f" border: 1px solid {C['border']}; border-radius: 14px; }}"
    )
    return f


def _muted_label(text: str) -> QLabel:
    l = QLabel(text)
    l.setStyleSheet(f"color:{C['text_secondary']}; font-size:12px; font-weight:600; background:transparent;")
    return l


def _divider() -> QFrame:
    """1px separator — border/radius explicitly none, taaki koi bhi parent
    stylesheet ise box me na badal de."""
    d = QFrame()
    d.setObjectName("etsDivider")
    d.setFixedHeight(1)
    d.setStyleSheet(
        f"QFrame#etsDivider {{ background:{C['border']}; border:none;"
        f" border-radius:0px; }}"
    )
    return d



class _BarChartWidget(QFrame):
    """Simple bar chart using QPainter"""
    def __init__(self, title: str, color: str, parent=None):
        super().__init__(parent)
        self._title = title
        self._color = color
        self._data = []
        self.setMinimumHeight(180)
        self.setStyleSheet(f"""
            QFrame {{
                background: {C['bg_surface']};
                border: 1px solid {C['border']};
                border-radius: 12px;
            }}
        """)

    def set_data(self, rows: list):
        """rows: [{date, count}, ...]"""
        self._data = rows
        self.update()

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QColor, QFont, QPen
        from PySide6.QtCore import Qt, QRect
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        pad = 16

        # Title
        painter.setPen(QColor(C['text_primary']))
        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRect(pad, 8, w - pad*2, 20), Qt.AlignmentFlag.AlignLeft, self._title)

        if not self._data:
            painter.setPen(QColor(C['text_muted']))
            font.setBold(False)
            painter.setFont(font)
            painter.drawText(QRect(0, h//2, w, 20), Qt.AlignmentFlag.AlignCenter, "No data")
            painter.end()
            return

        chart_top = 36
        chart_bottom = h - 28
        chart_h = chart_bottom - chart_top
        chart_w = w - pad * 2

        max_val = max(int(r.get('count', 0)) for r in self._data) or 1
        bar_w = max(8, chart_w // (len(self._data) * 2 + 1))
        gap = bar_w

        color = QColor(self._color)
        color.setAlpha(200)

        for i, row in enumerate(self._data):
            val = int(row.get('count', 0))
            bar_h = int((val / max_val) * chart_h)
            x = pad + i * (bar_w + gap)
            y = chart_bottom - bar_h

            # Bar
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(x, y, bar_w, bar_h, 3, 3)

            # Value label
            painter.setPen(QColor(C['text_primary']))
            font.setPointSize(7)
            font.setBold(False)
            painter.setFont(font)
            painter.drawText(QRect(x - 4, y - 16, bar_w + 8, 14), Qt.AlignmentFlag.AlignCenter, str(val))

            # Date label
            date_str = str(row.get('date', ''))[:10]
            short = date_str[5:] if len(date_str) >= 7 else date_str
            painter.drawText(QRect(x - 8, chart_bottom + 4, bar_w + 16, 16), Qt.AlignmentFlag.AlignCenter, short)

        painter.end()

_StatCardCounter = [0]


class StatCard(QFrame):
    """
    Dashboard metric card — icon badge + big value + label + trend sparkline.

    Sparkline sirf ASLI data se banti hai (`push_point`/`set_series`). Koi
    fake random series kabhi nahi banate — warna admin ko lagta hai activity
    ho rahi hai jabki kuch nahi ho raha.
    """

    def __init__(self, label: str, accent: str, icon: str = "●", value="—",
                 sparkline: bool = True):
        super().__init__()
        self._accent = accent
        # In Qt, QLabel is a subclass of QFrame, so a bare `QFrame { border:
        # 1px solid ... }` rule also applies to every QLabel inside the card.
        # That drew an outline around the value, the caption and the subtitle
        # — the stray boxes visible around "2", "Total Employees" and
        # "2 registered" on the dashboard strip.
        #
        # panel_widgets.py avoids this by setting `border:none` on each child
        # label; this class never did. Scoping the rule to this widget's own
        # objectName is the more robust fix, since it cannot be undone by
        # forgetting a guard on some label added later.
        _StatCardCounter[0] += 1
        name = f"statCard{_StatCardCounter[0]}"
        self.setObjectName(name)
        self.setStyleSheet(
            f"QFrame#{name} {{ background: {C['bg_surface']};"
            f" border: 1px solid {C['border']}; border-radius: 14px; }}"
        )
        # BUG: minimumHeight 100 tha, lekin card ke andar badge (36) + value
        # (24px font) + label + subtitle + spacing + margins milkar 157px
        # maangte hain — aur sparkline ke saath 207px. Layout compress hota
        # tha aur value ka upar ka hissa kat jaata tha (dashboard ke neeche
        # wali strip me "97" aur "19464" aadhe dikhte the).
        #
        # Hardcoded number likhne ke bajaye layout ko hi height decide karne
        # dete hain, taaki font ya padding badalne pe ye dobara na tootey.
        self.setMinimumHeight(0)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(8)
        lay.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)

        badge = QLabel(icon)
        badge.setFixedSize(36, 36)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background: rgba({_hex_to_rgb(accent)}, 0.16); border-radius: 10px; font-size: 16px;"
        )
        lay.addWidget(badge)

        self._value_label = QLabel(str(value))
        self._value_label.setStyleSheet(
            f"color:{C['text_primary']}; font-size:24px; font-weight:700; background:transparent;"
        )
        lay.addWidget(self._value_label)

        cap = QLabel(label)
        cap.setStyleSheet(f"color:{C['text_secondary']}; font-size:12px; font-weight:600; background:transparent;")
        lay.addWidget(cap)

        self._sub_label = QLabel("")
        self._sub_label.setStyleSheet(
            f"color:{C['text_muted']}; font-size:11px; background:transparent;"
        )
        lay.addWidget(self._sub_label)

        self._spark = None
        if sparkline:
            from client.presentation.widgets.panel_widgets import Sparkline
            self._spark = Sparkline(accent)
            self._spark.setStyleSheet("background:transparent;border:none;")
            lay.addWidget(self._spark)

        _shadow(self, blur=26, dy=10, alpha=55)

    def set_value(self, value):
        self._value_label.setText(str(value))

    def set_subtitle(self, text: str):
        self._sub_label.setText(str(text))

    def push_point(self, value: float):
        if self._spark:
            self._spark.push_value(value)

    def set_series(self, values: list):
        if self._spark:
            self._spark.set_series(values)


# ──────────────────────────────────────────────────────────────────────────────
#  Background workers
# ──────────────────────────────────────────────────────────────────────────────
def _auth_headers():
    return {
    "Authorization": f"Bearer {SessionManager.auth_token}",
    "Content-Type": "application/json",
    }


def _export_to_csv(filename: str, headers: list[str], rows: list[list]) -> bool:
    """Export rows to CSV file with UTF-8 encoding."""
    try:
        import csv
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
        return True
    except Exception as e:
        return False


class _FetchWorker(QThread):
    # RACE FIX: pehle is signal ka naam `finished` tha, jo QThread ke
    # BUILT-IN `finished` signal ko shadow karta tha. `_track_worker()` bhi
    # `finished` pe hi cleanup connect karta hai — to sequence banti thi:
    #   run() -> self.finished.emit(data)   [main thread pe QUEUE hota hai]
    #   run() return -> QThread apna finished emit karta hai
    #   _cleanup chalta hai -> worker list se hat jaata hai
    #   koi reference nahi bachta -> Python worker ko GC kar deta hai
    #   ...aur queued `_populate(data)` call CHUP-CHAAP DROP ho jaati hai.
    # Nateeja: table khali, koi error nahi, koi log nahi. Kaunsa tab khali
    # rahega ye GC timing pe depend karta tha — production me ye "kabhi
    # kabhi Screenshots tab khali aata hai" jaisa random bug banta.
    result = Signal(dict)
    error  = Signal(str)

    def __init__(self, url: str, params: dict | None = None):
        super().__init__()
        self._url    = url
        self._params = params or {}

    def run(self):
        try:
            r = requests.get(
                self._url,
                params=self._params,
                headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                timeout=10,
            )
            self.result.emit(r.json())
        except Exception as e:
            self.error.emit(str(e))


class _PostWorker(QThread):
    result = Signal(dict)      # QThread.finished ko shadow na karo (upar dekho)
    error  = Signal(str)

    def __init__(self, url: str, body: dict):
        super().__init__()
        self._url  = url
        self._body = body

    def run(self):
        try:
            r = requests.post(
                self._url,
                json=self._body,
                headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                timeout=10,
            )
            self.result.emit(r.json())
        except Exception as e:
            self.error.emit(str(e))

class _ExportWorker(QThread):
    """
    BUG FIX: pehle Export CSV sirf CURRENT PAGE (50 rows) export karta tha,
    lekin message "Exported N records" dikha ke lagta tha sab kuch export ho
    gaya. 181 attendance records me se sirf 50 milte the — payroll ke liye
    ye chup-chaap adhoora data tha.

    Ab ye worker saare pages ghoom kar poora filtered data laata hai
    (sane cap ke saath, taaki 10,000 employees pe browser/DB na mare).
    """
    result = Signal(list)      # QThread.finished ko shadow na karo
    error  = Signal(str)

    MAX_ROWS = 5000

    def __init__(self, url: str, params: dict, page_size: int):
        super().__init__()
        self._url = url
        self._params = dict(params or {})
        self._page_size = page_size

    def run(self):
        try:
            rows, page = [], 1
            while len(rows) < self.MAX_ROWS:
                q = dict(self._params); q["page"] = page
                r = requests.get(
                    self._url, params=q,
                    headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                    timeout=30,
                )
                data = r.json()
                batch = data.get("data", []) or []
                rows.extend(batch)
                total = data.get("total", len(rows))
                if len(batch) < self._page_size or len(rows) >= total:
                    break
                page += 1
            self.result.emit(rows[: self.MAX_ROWS])
        except Exception as e:
            self.error.emit(str(e))


class _DeleteWorker(QThread):
    result = Signal(dict)      # QThread.finished ko shadow na karo
    error  = Signal(str)

    def __init__(self, url: str):
        super().__init__()
        self._url = url

    def run(self):
        try:
            r = requests.delete(
                self._url,
                headers=_auth_headers(),
                timeout=20
            )

            data = r.json()

            if r.ok:
                self.result.emit(data)
            else:
                self.error.emit(
                    data.get("message", "Delete failed")
                )

        except Exception as e:
            self.error.emit(str(e))

class _ConfigTab(QWidget):

    def __init__(self):
        super().__init__()
        self._employees: list[dict] = []
        self._workers:   list       = []
        self._build_ui()
        self._load_employees()
        self._refresh_holidays()

    def _setting_row(self, label_text: str, desc: str, widget, suffix: str = ""):
        """Ek setting ki row — koi divider nahi, sirf spacing aur alignment."""
        row = QWidget()
        row.setObjectName("cfgRow")
        row.setStyleSheet("QWidget#cfgRow { background: transparent; }")
        row.setMinimumHeight(58)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 8, 0, 8)
        lay.setSpacing(18)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.setContentsMargins(0, 0, 0, 0)
        name = QLabel(label_text)
        name.setStyleSheet(
            f"color:{C['text_primary']}; font-size:13px; font-weight:600;"
            f"background:transparent;"
        )
        hint = QLabel(desc)
        hint.setWordWrap(True)
        hint.setMinimumWidth(240)
        hint.setStyleSheet(
            f"color:{C['text_muted']}; font-size:11px; background:transparent;"
        )
        text_col.addWidget(name)
        text_col.addWidget(hint)
        lay.addLayout(text_col, 1)

        
        field = QWidget()
        field.setObjectName("cfgField")
        field.setStyleSheet("QWidget#cfgField { background: transparent; }")
        f_lay = QHBoxLayout(field)
        f_lay.setContentsMargins(0, 0, 0, 0)
        f_lay.setSpacing(8)

        if isinstance(widget, QCheckBox):
            widget.setFixedWidth(24)
        elif widget.objectName() == "cfgWeekly":
            # Seven labelled checkboxes need their natural width. Forcing the
            # 104px used for spin boxes squeezed them into an unreadable strip
            # with no visible day names — the admin could not tell which day
            # was ticked, or that one was ticked at all.
            widget.setSizePolicy(QSizePolicy.Policy.Preferred,
                                 QSizePolicy.Policy.Fixed)
        else:
            widget.setFixedWidth(104)
        f_lay.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)

        if suffix:
            unit = QLabel(suffix)
            unit.setFixedWidth(58)
            unit.setStyleSheet(
                f"color:{C['text_muted']}; font-size:11px; background:transparent;"
            )
            f_lay.addWidget(unit, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            f_lay.addSpacing(58)

        lay.addWidget(field, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _build_section(self, icon: str, title: str, subtitle: str, rows: list) -> QFrame:
        card = _card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(10)
        badge = QLabel(icon)
        badge.setFixedSize(30, 30)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background:{C['accent_soft']}; border-radius:9px; font-size:14px;"
        )
        titles = QVBoxLayout()
        titles.setSpacing(0)
        heading = QLabel(title)
        heading.setStyleSheet(
            f"color:{C['text_primary']}; font-size:14px; font-weight:700;"
            f"background:transparent;"
        )
        sub = QLabel(subtitle)
        sub.setStyleSheet(
            f"color:{C['text_muted']}; font-size:11px; background:transparent;"
        )
        titles.addWidget(heading)
        titles.addWidget(sub)
        head.addWidget(badge)
        head.addLayout(titles)
        head.addStretch()
        lay.addLayout(head)
        lay.addSpacing(8)

        for row in rows:
            lay.addWidget(row)
        return card

    # ── Holidays ─────────────────────────────────────────────────────────
    #
    # Company-wide, unlike everything else on this page, which is why it
    # says so on the card. Selecting an employee above does not scope it —
    # a public holiday is a property of the calendar, not of a person.
    def _build_holidays_section(self) -> QFrame:
        self._holiday_date = QDateEdit()
        self._holiday_date.setCalendarPopup(True)
        self._holiday_date.setDisplayFormat("dd MMM yyyy")
        self._holiday_date.setDate(QDate.currentDate())
        self._holiday_date.setFixedHeight(36)

        self._holiday_name = QLineEdit()
        self._holiday_name.setPlaceholderText("Name, for example Diwali")
        self._holiday_name.setFixedHeight(36)
        self._holiday_name.setMaxLength(120)
        self._holiday_name.returnPressed.connect(self._add_holiday)

        add_btn = _btn("＋  Add", variant="primary", height=36)
        add_btn.clicked.connect(self._add_holiday)

        entry = QWidget()
        entry.setObjectName("holEntry")
        entry.setStyleSheet("QWidget#holEntry { background: transparent; }")
        entry_lay = QHBoxLayout(entry)
        entry_lay.setContentsMargins(0, 0, 0, 0)
        entry_lay.setSpacing(10)
        entry_lay.addWidget(self._holiday_date, 0)
        entry_lay.addWidget(self._holiday_name, 1)
        entry_lay.addWidget(add_btn, 0)

        self._holiday_list = QWidget()
        self._holiday_list.setObjectName("holList")
        self._holiday_list.setStyleSheet("QWidget#holList { background: transparent; }")
        self._holiday_list_lay = QVBoxLayout(self._holiday_list)
        self._holiday_list_lay.setContentsMargins(0, 6, 0, 0)
        self._holiday_list_lay.setSpacing(4)

        self._holiday_status = QLabel("")
        self._holiday_status.setWordWrap(True)
        self._holiday_status.setStyleSheet(
            f"color:{C['text_muted']}; font-size:11px; background:transparent;"
        )

        card = self._build_section(
            "🎌", "Holidays  ·  applies to everyone",
            "No screenshots on these dates, whichever employee is selected above.",
            [entry, self._holiday_list, self._holiday_status],
        )
        return card

    def _refresh_holidays(self):
        w = _FetchWorker(f"{API_BASE_URL}/admin/holidays")
        w.result.connect(self._populate_holidays)
        w.error.connect(lambda e: self._holiday_status.setText(f"Could not load holidays: {e}"))
        _track_worker(self._workers, w)
        w.start()

    def _populate_holidays(self, data: dict):
        while self._holiday_list_lay.count():
            item = self._holiday_list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        rows = data.get("holidays", []) if data.get("success") else []
        if not rows:
            self._holiday_status.setText("No holidays set.")
            return
        self._holiday_status.setText(f"{len(rows)} holiday(s) set.")

        for row in rows:
            iso = str(row.get("holiday_date", ""))
            line = QWidget()
            line.setObjectName("holRow")
            line.setStyleSheet("QWidget#holRow { background: transparent; }")
            line_lay = QHBoxLayout(line)
            line_lay.setContentsMargins(0, 0, 0, 0)
            line_lay.setSpacing(10)

            pretty = QDate.fromString(iso, "yyyy-MM-dd")
            when = QLabel(pretty.toString("ddd, dd MMM yyyy") if pretty.isValid() else iso)
            when.setFixedWidth(160)
            when.setStyleSheet(
                f"color:{C['text_primary']}; font-size:12px; font-weight:600;"
                f"background:transparent;"
            )
            name = QLabel(str(row.get("name", "")))
            name.setStyleSheet(
                f"color:{C['text_muted']}; font-size:12px; background:transparent;"
            )
            remove = _btn("Remove", variant="danger", height=28, width=84)
            remove.clicked.connect(lambda _=False, d=iso: self._remove_holiday(d))

            line_lay.addWidget(when)
            line_lay.addWidget(name, 1)
            line_lay.addWidget(remove)
            self._holiday_list_lay.addWidget(line)

    def _add_holiday(self):
        iso = self._holiday_date.date().toString("yyyy-MM-dd")
        name = self._holiday_name.text().strip()
        if not name:
            self._holiday_status.setText("Give the holiday a name before adding it.")
            self._holiday_name.setFocus()
            return

        w = _PostWorker(f"{API_BASE_URL}/admin/holidays",
                        {"holiday_date": iso, "name": name})

        def done(result: dict):
            if result.get("success"):
                self._holiday_name.clear()
                self._refresh_holidays()
            else:
                self._holiday_status.setText(
                    result.get("message", "Could not add that holiday."))

        w.result.connect(done)
        w.error.connect(lambda e: self._holiday_status.setText(f"Could not reach the server: {e}"))
        _track_worker(self._workers, w)
        w.start()

    def _remove_holiday(self, iso: str):
        w = _DeleteWorker(f"{API_BASE_URL}/admin/holidays/{iso}")

        def done(_result=None):
            self._refresh_holidays()

        w.result.connect(done)
        w.error.connect(lambda e: self._holiday_status.setText(f"Could not remove it: {e}"))
        _track_worker(self._workers, w)
        w.start()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        host = QWidget()
        host.setObjectName("cfgHost")
        host.setStyleSheet("QWidget#cfgHost { background: transparent; }")
        body = QVBoxLayout(host)
        body.setContentsMargins(28, 22, 22, 22)
        body.setSpacing(14)
        scroll.setWidget(host)
        root.addWidget(scroll)

        # ── Scope selector ────────────────────────────────────────────────
        toolbar = _card()
        t_lay = QHBoxLayout(toolbar)
        t_lay.setContentsMargins(20, 14, 20, 14)
        t_lay.setSpacing(12)

        scope_label = QLabel("Applying to")
        scope_label.setStyleSheet(
            f"color:{C['text_muted']}; font-size:12px; font-weight:600;"
            f"background:transparent;"
        )
        self._emp_combo = QComboBox()
        self._emp_combo.setMinimumWidth(300)
        self._emp_combo.setFixedHeight(38)
        self._emp_combo.currentIndexChanged.connect(self._on_employee_changed)

        refresh_btn = _btn("↻  Refresh", variant="secondary", height=38, width=110)
        refresh_btn.clicked.connect(self._load_employees)

        t_lay.addWidget(scope_label)
        t_lay.addWidget(self._emp_combo)
        t_lay.addStretch()
        t_lay.addWidget(refresh_btn)
        body.addWidget(toolbar)

       
        self._scope_banner = QLabel("")
        self._scope_banner.setWordWrap(True)
        self._scope_banner.setStyleSheet(
            f"background:{C['accent_soft']}; color:{C['text_secondary']};"
            f"border:1px solid {C['border']}; border-radius:10px;"
            f"padding:11px 16px; font-size:12px;"
        )
        body.addWidget(self._scope_banner)

        # ── Widgets (naam wahi — save/load logic inhi pe depend karta hai) ──
        self._min_spin = QSpinBox(); self._min_spin.setRange(1, 60)
        self._max_spin = QSpinBox(); self._max_spin.setRange(1, 120)
        self._cnt_spin = QSpinBox(); self._cnt_spin.setRange(1, 20)   # daily budget
        self._upl_spin = QSpinBox(); self._upl_spin.setRange(1, 240)
        self._idle_spin = QSpinBox(); self._idle_spin.setRange(10, 150)
        self._grace_spin = QSpinBox(); self._grace_spin.setRange(0, 120)
        for s in (self._min_spin, self._max_spin, self._cnt_spin,
                  self._upl_spin, self._idle_spin, self._grace_spin):
            s.setFixedHeight(36)
            s.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._verbose_check = QCheckBox()
        self._shift_start = QLineEdit(); self._shift_start.setPlaceholderText("09:00")
        self._shift_end   = QLineEdit(); self._shift_end.setPlaceholderText("18:00")
        for e in (self._shift_start, self._shift_end):
            e.setFixedHeight(36)
            e.setAlignment(Qt.AlignmentFlag.AlignCenter)
            e.setMaxLength(5)

        # Weekly offs — ISO weekday numbers, matching the server and the
        # client's work_calendar (1 = Monday ... 7 = Sunday).
        self._weekly_offs = {}
        weekly_row = QWidget()
        weekly_row.setObjectName("cfgWeekly")
        weekly_row.setStyleSheet("QWidget#cfgWeekly { background: transparent; }")
        weekly_lay = QHBoxLayout(weekly_row)
        weekly_lay.setContentsMargins(0, 0, 0, 0)
        weekly_lay.setSpacing(6)
        for iso, short in ((1, "Mon"), (2, "Tue"), (3, "Wed"), (4, "Thu"),
                           (5, "Fri"), (6, "Sat"), (7, "Sun")):
            day = QCheckBox(short)
            day.setCursor(Qt.CursorShape.PointingHandCursor)
            self._weekly_offs[iso] = day
            weekly_lay.addWidget(day)
        self._weekly_offs_row = weekly_row

        # ── Sections ──────────────────────────────────────────────────────
        body.addWidget(self._build_section(
            "📸", "Screenshot Capture",
            "How many captures per day, and how far apart.",
            [
                self._setting_row("Screenshots per day",
                                  "Exactly this many captures per calendar day (IST). "
                                  "Never more, however long the employee stays "
                                  "logged in.",
                                  self._cnt_spin, "captures"),
                self._setting_row("Minimum interval",
                                  "Shortest gap allowed between two captures.",
                                  self._min_spin, "minutes"),
                self._setting_row("Maximum interval",
                                  "Advisory only — with a daily budget the spacing follows from how "
                                  "much of the day is left.",
                                  self._max_spin, "minutes"),
            ]))

        body.addWidget(self._build_section(
            "🖥", "Activity Tracking",
            "When an employee counts as idle.",
            [
                self._setting_row("Idle threshold",
                                  "Marked idle after this long with no input.",
                                  self._idle_spin, "seconds"),
            ]))

        body.addWidget(self._build_section(
            "🕐", "Shift Schedule",
            "Screenshots are only scheduled inside this window (IST).",
            [
                self._setting_row("Shift start time", "24-hour format, for example 09:00.",
                                  self._shift_start, "HH:MM"),
                self._setting_row("Shift end time",
                                  "For an overnight shift set end before start (22:00 → 06:00).",
                                  self._shift_end, "HH:MM"),
                self._setting_row("Late after",
                                  "How much lateness to ignore. Signing in later "
                                  "than this past the shift start is marked Late "
                                  "on the Attendance page. 0 flags any lateness "
                                  "at all.",
                                  self._grace_spin, "minutes"),
                self._setting_row("Weekly off",
                                  "No screenshots on these days. An overnight shift "
                                  "belongs to the day it starts — a Saturday 22:00 shift "
                                  "running into Sunday is still captured.",
                                  self._weekly_offs_row),
            ]))

        body.addWidget(self._build_holidays_section())

        body.addWidget(self._build_section(
            "⚙", "Advanced",
            "Only change these if you need to.",
            [
                self._setting_row("Upload interval",
                                  "How often captured data is synced to the server.",
                                  self._upl_spin, "minutes"),
                self._setting_row("Verbose logging",
                                  "Logs every sync and schedule event. Turn on only for "
                                  "debugging — it fills the database quickly.",
                                  self._verbose_check),
            ]))

        # ── Save ──────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        self._save_btn = _btn("💾  Save Config", variant="primary", height=42, width=170)
        self._save_btn.clicked.connect(self._save_config)
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(
            f"color:{C['text_muted']}; font-size:12px; background:transparent;"
        )
        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(self._status_label, 1)
        body.addLayout(btn_row)
        body.addStretch()

    def _load_employees(self):
        self._status_label.setText("Loading employees…")
        w = _FetchWorker(f"{API_BASE_URL}/admin/employees")
        w.result.connect(self._on_employees_loaded)
        w.error.connect(lambda e: self._status_label.setText(f"Error: {e}"))
        _track_worker(self._workers, w)
        w.start()

    def _on_employees_loaded(self, data: dict):
        self._employees = data.get("data", [])
        self._emp_combo.blockSignals(True)
        self._emp_combo.clear()
        self._emp_combo.addItem("🌐  Global Default", "global")
        for emp in self._employees:
            # BUG FIX: `full_name` field /admin/employees kabhi return hi nahi
            # karta (wo employee_id, username, role deta hai) — is liye har
            # employee dropdown me "?  (EMP001)" dikhta tha. Ab username.
            label = f"{emp.get('username', '?')}  ({emp.get('employee_id', '')})"
            self._emp_combo.addItem(label, emp.get("employee_id"))
        self._emp_combo.blockSignals(False)
        self._on_employee_changed()
        self._status_label.setText("")

    def _on_employee_changed(self):
        emp_id = self._emp_combo.currentData() or "global"
        self._update_scope_banner()
        w = _FetchWorker(f"{API_BASE_URL}/admin/config/{emp_id}")
        w.result.connect(self._populate_form)
        w.error.connect(lambda e: self._status_label.setText(f"Config load error: {e}"))
        _track_worker(self._workers, w)
        w.start()

    def _populate_form(self, data: dict):
        cfg = data.get("config", {})
        self._min_spin.setValue(cfg.get("screenshot_min_minutes",  3))
        self._max_spin.setValue(cfg.get("screenshot_max_minutes",  10))
        self._cnt_spin.setValue(cfg.get("screenshots_per_day",     10))
        self._upl_spin.setValue(cfg.get("upload_interval_minutes", 60))
        self._idle_spin.setValue(cfg.get("idle_threshold_seconds", 60))
        self._grace_spin.setValue(cfg.get("late_grace_minutes", 10))
        self._verbose_check.setChecked(bool(cfg.get("verbose_logging", False)))
        self._shift_start.setText(str(cfg.get("shift_start") or "")[:5])
        self._shift_end.setText(str(cfg.get("shift_end") or "")[:5])

        # weekly_offs arrives as ISO weekday numbers in a comma-separated
        # string ("6,7"). Anything unrecognised leaves the box unticked, so a
        # value the admin cannot see is never one they can accidentally save.
        selected = {
            int(piece.strip())
            for piece in str(cfg.get("weekly_offs") or "").split(",")
            if piece.strip().isdigit() and 1 <= int(piece.strip()) <= 7
        }
        for iso, box in self._weekly_offs.items():
            box.blockSignals(True)
            box.setChecked(iso in selected)
            box.blockSignals(False)

        self._update_scope_banner(bool(cfg.get("inherited")))

    def _update_scope_banner(self, inherited: bool = False):
        """Saaf batata hai ki abhi jo values dikh rahi hain wo kiske liye hain.

        Ye isliye zaroori hai: global aur per-employee form bilkul ek jaise
        dikhte the. Admin ek employee select karke value badalta, aur use
        yakeen nahi hota tha ki ye sirf usi employee pe lagi ya sabpe.
        """
        emp_id = self._emp_combo.currentData()
        if not emp_id or emp_id == "global":
            self._scope_banner.setStyleSheet(
                f"background:{C['warning_soft']}; color:{C['text_secondary']};"
                f"border:1px solid {C['border']}; border-radius:10px;"
                f"padding:11px 16px; font-size:12px;"
            )
            self._scope_banner.setText(
                "🌐  <b>Global Default</b> — applies to every employee who has "
                "no override of their own. Employees with an override keep "
                "their own values."
            )
            return

        label = self._emp_combo.currentText()
        self._scope_banner.setStyleSheet(
            f"background:{C['accent_soft']}; color:{C['text_secondary']};"
            f"border:1px solid {C['border']}; border-radius:10px;"
            f"padding:11px 16px; font-size:12px;"
        )
        if inherited:
            self._scope_banner.setText(
                f"👤  <b>{label}</b> — no override of their own yet, so these "
                f"values come from the <b>Global Default</b>. Saving creates "
                f"an override for this employee only; nobody else is "
                f"affected."
            )
        else:
            self._scope_banner.setText(
                f"👤  <b>{label}</b> — these are this employee's own settings. "
                f"Changes here apply to <b>this employee only</b>, nobody "
                f"else."
            )

    # Actions

    def _save_config(self):
        emp_id = self._emp_combo.currentData()
        body = {
            "screenshot_min_minutes":  self._min_spin.value(),
            "screenshot_max_minutes":  self._max_spin.value(),
            "screenshots_per_day":     self._cnt_spin.value(),
            "upload_interval_minutes": self._upl_spin.value(),
            "idle_threshold_seconds":  self._idle_spin.value(),
            "verbose_logging":        self._verbose_check.isChecked(),
        }
        if emp_id and emp_id != "global":
            body["employee_id"] = emp_id

        # Persist shift times via /admin/config so they survive logout/login.
        #
        # Client-side validation — server bhi yehi check karta hai, lekin
        # yahan turant feedback milta hai (round-trip ke bina) aur galat
        # value kabhi DB tak jaati hi nahi.
        import re as _re
        TIME_RE = _re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
        shift_start = self._shift_start.text().strip()
        shift_end   = self._shift_end.text().strip()
        if shift_start or shift_end:
            for value, label in ((shift_start, "Shift start"), (shift_end, "Shift end")):
                if not TIME_RE.match(value):
                    self._status_label.setText(
                        f"❌ {label} time must be HH:MM (00:00–23:59) — got \"{value}\""
                    )
                    self._status_label.setStyleSheet(
                        f"color:{C['danger']}; font-size:12px; background:transparent;"
                    )
                    return
            body["shift_start"] = shift_start
            body["shift_end"]   = shift_end

        # Always sent, including when empty — that is how an admin clears a
        # weekly off. Omitting it would leave the previous value in place and
        # make unticking every box look like it did nothing.
        offs = sorted(iso for iso, box in self._weekly_offs.items() if box.isChecked())
        if len(offs) == 7:
            self._status_label.setText(
                "❌ Every day cannot be a weekly off — leave at least one working day."
            )
            self._status_label.setStyleSheet(
                f"color:{C['danger']}; font-size:12px; background:transparent;"
            )
            return
        body["weekly_offs"] = offs
        body["late_grace_minutes"] = self._grace_spin.value()

        self._save_btn.setEnabled(False)
        self._save_btn.setText("Saving…")
        w = _PostWorker(f"{API_BASE_URL}/admin/config", body)

        w.result.connect(self._on_save_done)
        w.error.connect(lambda e: (
            self._status_label.setText(f"❌ Error: {e}"),
            self._save_btn.setEnabled(True),
            self._save_btn.setText("💾  Save Config"),
        ))
        _track_worker(self._workers, w)
        w.start()

    def _on_save_done(self, data: dict):
        self._save_btn.setEnabled(True)
        self._save_btn.setText("💾  Save Config")
        if data.get("success"):
            self._status_label.setStyleSheet(f"color: {C['success']}; font-size:12px; background:transparent;")
            self._status_label.setText("✅ Config saved successfully!")
        else:
            self._status_label.setStyleSheet(f"color: {C['danger']}; font-size:12px; background:transparent;")
            self._status_label.setText(f"❌ {data.get('error', 'Save failed')}")

    def _force_logout(self):
        emp_id = self._emp_combo.currentData()
        if not emp_id or emp_id == "global":
            QMessageBox.warning(self, "Select Employee", "Select employee")
            return

        name = self._emp_combo.currentText()
        reply = QMessageBox.question(
            self, "Force Logout",
            f"{name} you want to force logout?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        w = _PostWorker(f"{API_BASE_URL}/admin/force-logout", {"employee_id": emp_id})
        w.result.connect(lambda d: self._status_label.setText(
            "✅ Force logout set!" if d.get("success") else f"❌ {d.get('error')}"
        ))
        w.error.connect(lambda e: self._status_label.setText(f"❌ {e}"))
        _track_worker(self._workers, w)
        w.start()


# ──────────────────────────────────────────────────────────────────────────────
#  Screenshots Tab
# ──────────────────────────────────────────────────────────────────────────────

class _ScreenshotsTab(QWidget):

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._page = 1
        self._user_searched = False
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        toolbar = _card()
        filter_row = QHBoxLayout(toolbar)
        filter_row.setContentsMargins(18, 12, 18, 12)
        filter_row.setSpacing(10)

        filter_row.addWidget(_muted_label("Employee ID"))
        self._emp_filter = QLineEdit()
        self._emp_filter.setPlaceholderText("e.g. EMP001")
        self._emp_filter.setFixedWidth(150)
        filter_row.addWidget(self._emp_filter)

        filter_row.addWidget(_muted_label("Date"))
        self._date_filter = QDateEdit(QDate.currentDate())
        self._date_filter.setCalendarPopup(True)
        self._date_filter.setFixedWidth(130)
        filter_row.addWidget(self._date_filter)

        search_btn = _btn("🔍  Search", variant="primary", height=34, width=110)
        search_btn.clicked.connect(self._on_search_clicked)
        filter_row.addWidget(search_btn)
        clear_btn = _btn("✕  Clear", variant="secondary", height=34, width=80)
        clear_btn.clicked.connect(self._on_clear_clicked)
        filter_row.addWidget(clear_btn)
        filter_row.addStretch()
        root.addWidget(toolbar)

        self._table = _tune_table(QTableWidget(0, 4))
        self._table.setHorizontalHeaderLabels(["ID", "Employee", "File", "Captured"])
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 70)
        self._table.setColumnWidth(1, 110)
        self._table.setColumnWidth(3, 210)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.cellDoubleClicked.connect(self._open_preview)
        root.addWidget(self._table, 1)

        pag_row = QHBoxLayout()
        self._prev_btn  = _btn("◀  Prev", variant="secondary", height=32, width=92)
        self._prev_btn.clicked.connect(self._prev_page)
        self._next_btn  = _btn("Next  ▶", variant="secondary", height=32, width=92)
        self._next_btn.clicked.connect(self._next_page)
        self._page_label = _muted_label("Page 1")
        pag_row.addWidget(self._prev_btn)
        pag_row.addWidget(self._page_label)
        pag_row.addWidget(self._next_btn)
        pag_row.addStretch()
        root.addLayout(pag_row)
        self._load()
        # SCALE FIX: 5s -> 30s. Har admin ka har khula tab server pe
        # constant load daalta tha; screenshots/logs itni tezi se badalte
        # bhi nahi ki 5 second ka refresh chahiye.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(30000)
        self._refresh_timer.timeout.connect(
            lambda: self._load(self._page)
        )
        self._refresh_timer.start()


    def _load(self, page=1):
        self._page = page
        params = {"page": page}
        emp = self._emp_filter.text().strip()
        if emp:
            params["employee_id"] = emp
        if self._user_searched:
            dt = self._date_filter.date().toString("yyyy-MM-dd")
            params["date"] = dt
        w = _FetchWorker(f"{API_BASE_URL}/admin/screenshots", params)
        w.result.connect(self._populate)
        w.error.connect(lambda e: print("Screenshots error:", e))
        _track_worker(self._workers, w)
        w.start()

    def _populate(self, data: dict):
        rows  = data.get("data", [])
        total = data.get("total", 0)
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            fname = row.get("file_name", "")
            self._table.setItem(i, 0, _cell(str(row.get("id", "")), mono=True, muted=True))
            self._table.setItem(i, 1, _cell(row.get("employee_id", ""), mono=True))
            # Poora .enc naam tooltip me — column me chhota handle.
            item = _cell(_short_filename(fname), mono=True, tooltip=fname)
            item.setData(Qt.ItemDataRole.UserRole, fname)
            self._table.setItem(i, 2, item)
            # BUG FIX: pehle yahan `ts = ...` assignment `if dt.tzinfo is
            # None:` block ke ANDAR thi — tz-aware timestamp par timestamp
            # kabhi format hi nahi hota tha. Ab shared helper.
            self._table.setItem(i, 3, _cell(_fmt_ts(row.get("created_at")), muted=True))
        self._page_label.setText(f"Page {self._page}  •  Total: {total}")
        self._prev_btn.setEnabled(self._page > 1)
        self._next_btn.setEnabled(self._page * 20 < total)

    
    def _on_search_clicked(self):
        self._user_searched = True
        self._load(page=1)

    def _on_clear_clicked(self):
        self._user_searched = False
        self._emp_filter.clear()
        self._date_filter.setDate(QDate.currentDate())
        self._load(page=1)
        
    def _prev_page(self): self._load(self._page - 1)
    def _next_page(self): self._load(self._page + 1)
    def _open_preview(self, row, column):
        # Get screenshot ID
        id_item = self._table.item(row, 0)
        emp_item = self._table.item(row, 1)
        file_item = self._table.item(row, 2)
        ts_item = self._table.item(row, 3)

        if not id_item:
            return

        screenshot_id = id_item.text()
        employee_id = emp_item.text() if emp_item else "?"
        # File column ab chhota handle dikhata hai ("1263bfcf"), poora
        # naam UserRole me hai. Preview window ko ASLI filename chahiye —
        # display text bhejne se wo galat file maangta.
        filename = "?"
        if file_item:
            filename = (file_item.data(Qt.ItemDataRole.UserRole)
                        or file_item.text())
        timestamp = ts_item.text() if ts_item else "?"

        self.preview_window = ScreenshotPreviewWindow(
            screenshot_id=screenshot_id,
            employee_id=employee_id,
            timestamp=timestamp,
            filename=filename
        )
        self.preview_window.show()


# ──────────────────────────────────────────────────────────────────────────────
#  Dashboard Tab
# ──────────────────────────────────────────────────────────────────────────────

class _DashboardTab(QWidget):

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._build_ui()
        self._load_all()

        # SCALE FIX: pehle poora dashboard (cards + feed + charts) har 5
        # SECOND refresh hota tha. Charts wali query 20 lakh logs pe 135ms
        # leti hai (parallel seq scan) — 2 crore logs pe ~1.3 SECOND. 20
        # admins × har 5 second = database ke liye 5x se zyada kaam jitna
        # wo kar sakta hai. Ab:
        #   cards + feed -> 30s (ye actually badalte rehte hain)
        #   charts       -> 120s (7-din ke aggregate, jaldi badalte hi nahi)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(30000)
        self._refresh_timer.timeout.connect(self._load_light)
        self._refresh_timer.start()

        self._charts_timer = QTimer(self)
        self._charts_timer.setInterval(120000)
        self._charts_timer.timeout.connect(self._load_charts)
        self._charts_timer.start()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        host = QWidget()
        host.setObjectName("cfgHost")
        host.setStyleSheet("QWidget#cfgHost { background: transparent; }")
        root = QVBoxLayout(host)
        root.setContentsMargins(28, 22, 22, 22)
        root.setSpacing(16)
        scroll.setWidget(host)
        outer.addWidget(scroll)

        def card_frame():
            f = QFrame()
            f.setStyleSheet(
                f"QFrame{{background:{C['bg_surface']};border:1px solid {C['border']};"
                f"border-radius:14px;}}"
            )
            return f

        # ── Today's Summary strip ────────────────────────────────────────
        summary = card_frame()
        sl = QVBoxLayout(summary)
        sl.setContentsMargins(18, 15, 18, 16)
        sl.setSpacing(13)
        head = QHBoxLayout()
        ico = QLabel("📈"); ico.setStyleSheet("font-size:15px;border:none;background:transparent;")
        ttl = QLabel("Today's Summary")
        ttl.setStyleSheet(
            f"color:{C['text_primary']};font-size:15px;font-weight:700;"
            f"border:none;background:transparent;"
        )
        head.addWidget(ico); head.addWidget(ttl); head.addStretch()
        sl.addLayout(head)

        from client.presentation.widgets.panel_widgets import MiniStat, StatusTile, QuickAction
        from client.presentation.theme import C as TC

        strip = QHBoxLayout(); strip.setSpacing(12)
        self.m_employees = MiniStat("👥", "Employees",   TC.BLUE,   TC.BLUE_BG)
        self.m_online    = MiniStat("🟢", "Online Now",  TC.GREEN,  TC.GREEN_BG)
        self.m_shots     = MiniStat("🖼", "Screenshots", TC.PURPLE, TC.PURPLE_BG)
        self.m_logs      = MiniStat("📝", "Activity Logs", TC.CYAN, TC.CYAN_BG)
        self.m_coverage  = MiniStat("🎯", "Coverage",    TC.AMBER,  TC.AMBER_BG)
        for c in (self.m_employees, self.m_online, self.m_shots,
                  self.m_logs, self.m_coverage):
            strip.addWidget(c)
        sl.addLayout(strip)
        root.addWidget(summary)

        # ── This admin's OWN session ─────────────────────────────────────
        # Everything above is org-wide. Admins are tracked users themselves —
        # screenshots are captured for them, and the idle tracker runs — but
        # the panel never showed them any of that, so an admin had no way to
        # tell whether their own tracking was working. Employees see exactly
        # these three on their dashboard.
        mine = _card()
        m_lay = QVBoxLayout(mine)
        m_lay.setContentsMargins(20, 16, 20, 16)
        m_lay.setSpacing(12)

        m_head = QHBoxLayout(); m_head.setSpacing(10)
        m_ico = QLabel("👤"); m_ico.setFixedSize(28, 28)
        m_ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
        m_ico.setStyleSheet(f"background:{C['accent_soft']}; border-radius:9px; font-size:14px;")
        m_ttl = QLabel("Your Session")
        m_ttl.setStyleSheet(
            f"color:{C['text_primary']}; font-size:15px; font-weight:700; background:transparent;"
        )
        m_sub = QLabel("Your own tracking status — admins are tracked too")
        m_sub.setStyleSheet(f"color:{C['text_muted']}; font-size:11px; background:transparent;")
        m_head.addWidget(m_ico); m_head.addWidget(m_ttl)
        m_head.addSpacing(8); m_head.addWidget(m_sub); m_head.addStretch()
        m_lay.addLayout(m_head)

        my_row = QHBoxLayout(); my_row.setSpacing(12)
        self.m_session  = MiniStat("⏱", "Session Duration", TC.AMBER,  TC.AMBER_BG)
        self.m_activity = MiniStat("🖥", "Activity Status",  TC.GREEN,  TC.GREEN_BG)
        self.m_myshots  = MiniStat("📸", "Screenshots Today", TC.PURPLE, TC.PURPLE_BG)
        for c in (self.m_session, self.m_activity, self.m_myshots):
            my_row.addWidget(c)
        m_lay.addLayout(my_row)
        root.addWidget(mine)

        # ── Status tiles ─────────────────────────────────────────────────
        tiles = QHBoxLayout(); tiles.setSpacing(14)
        self.t_server   = StatusTile("🖥", "Server Status",   TC.GREEN, TC.GREEN_BG)
        self.t_database = StatusTile("🗄", "Database",        TC.GREEN, TC.CYAN_BG)
        self.t_tracking = StatusTile("🎯", "Tracking",        TC.GREEN, TC.BLUE_BG)
        self.t_sync     = StatusTile("☁️", "Sync Health",     TC.GREEN, TC.PURPLE_BG)
        for tl in (self.t_server, self.t_database, self.t_tracking, self.t_sync):
            tiles.addWidget(tl)
        root.addLayout(tiles)

        # ── Legacy stat cards (existing feature — hataya nahi) ───────────
        grid = QGridLayout()
        grid.setSpacing(14)
        self._card_total_employees = StatCard("Total Employees",      ACCENTS["blue"],   "👥", sparkline=False)
        self._card_online          = StatCard("Online Now",           ACCENTS["green"],  "🟢", sparkline=False)
        self._card_offline         = StatCard("Offline",              ACCENTS["slate"],  "🌙", sparkline=False)
        self._card_total_screens   = StatCard("Screenshots Captured", ACCENTS["violet"], "📸", sparkline=False)
        self._card_total_logs      = StatCard("Activity Logs",        ACCENTS["cyan"],   "📝", sparkline=False)
        for i, c in enumerate([
            self._card_total_employees, self._card_online, self._card_offline,
            self._card_total_screens, self._card_total_logs,
        ]):
            grid.addWidget(c, 0, i)
            grid.setColumnStretch(i, 1)
        root.addLayout(grid)

        # ── Charts ───────────────────────────────────────────────────────
        charts_header = QLabel("Last 7 Days Overview")
        charts_header.setStyleSheet(
            f"color:{C['text_primary']}; font-weight:700; font-size:15px; background:transparent;"
        )
        root.addWidget(charts_header)
        charts_row = QHBoxLayout()
        charts_row.setSpacing(14)
        self._chart_screenshots = _BarChartWidget("Screenshots / Day", ACCENTS["violet"])
        self._chart_attendance  = _BarChartWidget("Active Employees / Day", ACCENTS["green"])
        self._chart_activity    = _BarChartWidget("Activity Logs / Day", ACCENTS["cyan"])
        charts_row.addWidget(self._chart_screenshots)
        charts_row.addWidget(self._chart_attendance)
        charts_row.addWidget(self._chart_activity)
        root.addLayout(charts_row)

        # ── Recent Activity ──────────────────────────────────────────────
        feed_card = card_frame()
        fc = QVBoxLayout(feed_card)
        fc.setContentsMargins(0, 0, 0, 0)
        fc.setSpacing(0)
        fh = QHBoxLayout(); fh.setContentsMargins(20, 15, 16, 10)
        fl = QLabel("Recent Activity")
        fl.setStyleSheet(
            f"color:{C['text_primary']};font-weight:700;font-size:15px;"
            f"border:none;background:transparent;"
        )
        self._feed_count = QLabel("")
        self._feed_count.setStyleSheet(
            f"color:{C['text_muted']};font-size:12px;border:none;background:transparent;"
        )
        fh.addWidget(fl); fh.addWidget(self._feed_count); fh.addStretch()
        fc.addLayout(fh)
        self._feed = QListWidget()
        self._feed.setMinimumHeight(190)
        self._feed.setStyleSheet(
            f"QListWidget{{background:transparent;border:none;color:{C['text_primary']};"
            f"font-size:13px;outline:none;}}"
            f"QListWidget::item{{padding:9px 20px;border-bottom:1px solid {C['border']};}}"
            f"QListWidget::item:hover{{background:{C['bg_elevated']};}}"
        )
        fc.addWidget(self._feed, 1)
        root.addWidget(feed_card)

        # ── Quick Actions ────────────────────────────────────────────────
        qa = card_frame()
        qc = QVBoxLayout(qa)
        qc.setContentsMargins(18, 15, 18, 16)
        qc.setSpacing(12)
        qt = QLabel("Quick Actions")
        qt.setStyleSheet(
            f"color:{C['text_primary']};font-size:15px;font-weight:700;"
            f"border:none;background:transparent;"
        )
        qc.addWidget(qt)
        qrow = QHBoxLayout(); qrow.setSpacing(12)
        self._quick_buttons = {}
        for icon, label, key in (
            ("👥", "Employees", 2),
            ("⚙", "Configuration", 1),
            ("📅", "Attendance", 3),
            ("📷", "Screenshots", 4),
            ("📋", "Audit Logs", 5),
        ):
            btn = QuickAction(icon, label)
            self._quick_buttons[label] = (btn, key)
            qrow.addWidget(btn)
        qc.addLayout(qrow)
        root.addWidget(qa)
        root.addStretch()

    def _load_charts(self):
        w = _FetchWorker(f"{API_BASE_URL}/dashboard/charts")
        w.result.connect(self._on_charts)
        w.error.connect(lambda e: print("Charts error:", e))
        _track_worker(self._workers, w)
        w.start()

    def _on_charts(self, data: dict):
        d = data.get("data", {})
        shots = d.get("screenshots_per_day", [])
        attend = d.get("attendance_per_day", [])
        activity = d.get("activity_per_day", [])
        self._chart_screenshots.set_data(shots)
        self._chart_attendance.set_data(attend)
        self._chart_activity.set_data(activity)

        
        def series(rows):
            return [float(r.get("count", 0) or 0) for r in (rows or [])]

        try:
            self.m_shots.set_series(series(shots))
            self.m_logs.set_series(series(activity))
            if shots:
                self.m_shots.set_value(int(series(shots)[-1]), "Captured today")
            if activity:
                self.m_logs.set_value(int(series(activity)[-1]), "Logged today")
        except Exception:
            pass
        if shots:
            self._card_total_screens.set_subtitle(
                f"{int(series(shots)[-1])} captured today")
        if activity:
            self._card_total_logs.set_subtitle(
                f"{int(series(activity)[-1])} logged today")

    def _load_all(self):
        self._load_summary()
        self._load_feed()
        self._load_charts()

    def _load_light(self):
        """Sirf cards + feed — charts apne alag (slow) timer pe chalte hain."""
        self._load_summary()
        self._load_feed()

    def _load_summary(self):
        url = f"{API_BASE_URL}/dashboard/summary"
        w = _FetchWorker(url)

        w.result.connect(self._on_summary)
        w.error.connect(lambda e: print("[SUMMARY ERROR]", e))
        _track_worker(self._workers, w)
        w.start()

    def _load_feed(self):
        w = _FetchWorker(f"{API_BASE_URL}/dashboard/recent-activity", params={"limit": 50})

        w.result.connect(self._on_feed)
        w.error.connect(lambda e: print("Dashboard feed error:", e))
        _track_worker(self._workers, w)
        w.start()

    def _on_summary(self, data: dict):
        s = data.get("data", data)

        total   = s.get('total_employees', 0) or 0
        online  = s.get('online_employees', 0) or 0
        offline = s.get('offline_employees', 0) or 0
        shots   = s.get('total_screenshots', 0) or 0
        logs    = s.get('total_activity_logs', 0) or 0

        self._card_total_employees.set_value(total)
        self._card_online.set_value(online)
        self._card_offline.set_value(offline)
        self._card_total_screens.set_value(shots)
        self._card_total_logs.set_value(logs)

        # ── Control Center strip + status tiles (naya) ──
        try:
            from client.presentation.theme import C as TC
            self.m_employees.set_value(total, "Registered")
            self.m_employees.push_point(total)
            self.m_online.set_value(online, "Currently working")
            self.m_online.push_point(online)
            self.m_shots.set_value(shots, "All time")
            self.m_logs.set_value(logs, "All time")

            coverage = (online / total * 100) if total else 0
            self.m_coverage.set_value(f"{coverage:.0f}%", "Workforce online")
            self.m_coverage.push_point(coverage)

            self.t_server.set("ONLINE", "API responding",
                              f"{total} accounts managed", TC.GREEN)
            self.t_database.set("CONNECTED", "Queries healthy",
                                f"{logs:,} log rows", TC.GREEN)
            self.t_tracking.set(
                "ACTIVE" if online else "IDLE",
                f"{online} of {total} employees online",
                "Screenshots scheduled per day",
                TC.GREEN if online else TC.AMBER,
            )
            self.t_sync.set("SYNCED", "All uploads current",
                            f"Last refresh: {datetime.now():%I:%M:%S %p}", TC.GREEN)
        except Exception as error:
            print("[DASHBOARD] control center tiles:", error)

        # Trend subtitles + sparklines — sirf ASLI values se.
        try:
            pct = (online / total * 100) if total else 0
            self._card_total_employees.set_subtitle(f"{total} registered")
            self._card_online.set_subtitle(f"{pct:.0f}% of workforce active")
            self._card_offline.set_subtitle("Not currently signed in")
            self._card_total_screens.set_subtitle("All time")
            self._card_total_logs.set_subtitle("All time")

            # Har refresh pe live point — online count ka trend banta jaata hai.
            self._card_online.push_point(online)
            self._card_offline.push_point(offline)
            self._card_total_employees.push_point(total)
        except Exception as error:
            print("[DASHBOARD] subtitle error:", error)

    def _on_feed(self, data: dict):
        rows = data.get("data", data).get("recent_activity", []) if isinstance(data, dict) else []
        if rows is None:
            rows = []
        self._feed.clear()
        # BUG FIX: server ka getRecentActivity ScreenshotManager ke internal
        # messages filter nahi karta, aur feed me timestamp bhi nahi dikhta
        # tha. Admin ko "ScreenshotManager: 6 screenshots scheduled across
        # shift..." jaisi diagnostic lines dikhti thin, bina time ke.
        internal = ("SCREENSHOTMANAGER", "CONFIGSYNCMANAGER", "SCHEDULERSERVICE",
                    "SYNCMANAGER", "STARTUPMANAGER", "AUTOLOGINMANAGER")
        shown = 0
        for r in rows:
            msg = (r.get("message") if isinstance(r, dict) else str(r)) or ""
            upper = msg.upper()
            if any(upper.lstrip().startswith(p) or f"{p}:" in upper for p in internal):
                continue
            when = _parse_server_ts(r.get("created_at")) if isinstance(r, dict) else None
            prefix = f"{when.astimezone(IST):%H:%M}  ·  " if when else ""
            self._feed.addItem(f"{prefix}{msg}")
            shown += 1
        if shown == 0:
            self._feed.addItem("No recent activity.")
        try:
            self._feed_count.setText(f"·  {shown} events")
        except Exception:
            pass


class _LogsTab(QWidget):

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._page = 1
        self._logs: list[dict] = []
        self._user_searched = False
        self._build_ui()
        self._load()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        toolbar = _card()
        filter_row = QHBoxLayout(toolbar)
        filter_row.setContentsMargins(18, 12, 18, 12)
        filter_row.setSpacing(10)

        filter_row.addWidget(_muted_label("Employee ID"))
        self._emp_filter = QLineEdit()
        self._emp_filter.setPlaceholderText("e.g. EMP001")
        self._emp_filter.setFixedWidth(150)
        filter_row.addWidget(self._emp_filter)

        filter_row.addWidget(_muted_label("Date"))
        self._date_filter = QDateEdit(QDate.currentDate())
        self._date_filter.setCalendarPopup(True)
        self._date_filter.setFixedWidth(130)
        filter_row.addWidget(self._date_filter)

        search_btn = _btn("🔍  Search", variant="primary", height=34, width=110)
        search_btn.clicked.connect(self._on_search_clicked)
        filter_row.addWidget(search_btn)

        self._export_btn = _btn("📥  Export CSV", variant="secondary", height=34, width=140)
        self._export_btn.clicked.connect(self._export_logs_csv)
        filter_row.addWidget(self._export_btn)

        filter_row.addStretch()
        root.addWidget(toolbar)

        self._table = _tune_table(QTableWidget(0, 4))
        self._table.setHorizontalHeaderLabels(["ID", "Employee", "Activity", "Timestamp"])
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 80)   # 60 pe 4-digit ID "27…" ho jaati thi
        self._table.setColumnWidth(1, 110)
        self._table.setColumnWidth(3, 210)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        root.addWidget(self._table, 1)

        pag_row = QHBoxLayout()
        self._prev_btn  = _btn("◀  Prev", variant="secondary", height=32, width=92)
        self._prev_btn.clicked.connect(self._prev_page)
        self._next_btn  = _btn("Next  ▶", variant="secondary", height=32, width=92)
        self._next_btn.clicked.connect(self._next_page)
        self._page_label = _muted_label("Page 1")
        pag_row.addWidget(self._prev_btn)
        pag_row.addWidget(self._page_label)
        pag_row.addWidget(self._next_btn)
        pag_row.addStretch()
        root.addLayout(pag_row)
        # SCALE FIX: 5s -> 30s. Har admin ka har khula tab server pe
        # constant load daalta tha; screenshots/logs itni tezi se badalte
        # bhi nahi ki 5 second ka refresh chahiye.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(30000)
        self._refresh_timer.timeout.connect(
            lambda: self._load(self._page)
        )
        self._refresh_timer.start()


    def _load(self, page=1):
        self._page = page
        params = {"page": page}
        emp = self._emp_filter.text().strip()
        if emp:
            params["employee_id"] = emp
        if self._user_searched:
            dt = self._date_filter.date().toString("yyyy-MM-dd")
            params["date"] = dt

        w = _FetchWorker(f"{API_BASE_URL}/admin/logs", params)
        w.result.connect(self._populate)
        w.error.connect(lambda e: print("Logs error:", e))
        _track_worker(self._workers, w)
        w.start()

    def _populate(self, data: dict):
        rows  = data.get("data", [])
        self._logs = rows
        total = data.get("total", 0)
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            activity = row.get("activity", "")
            self._table.setItem(i, 0, _cell(str(row.get("id", "")), mono=True, muted=True))
            self._table.setItem(i, 1, _cell(row.get("employee_id", ""), mono=True))
            # Lambi activity lines column me kat jaati thin — poori tooltip me.
            self._table.setItem(i, 2, _cell(activity, tooltip=activity))
            self._table.setItem(i, 3, _cell(_fmt_ts(row.get("created_at")), muted=True))
        self._page_label.setText(f"Page {self._page}  •  Total: {total}")
        self._prev_btn.setEnabled(self._page > 1)
        self._next_btn.setEnabled(self._page * 50 < total)

    def _export_logs_csv(self):
        if not self._logs:
            QMessageBox.warning(self, "Export", "No logs loaded. Please search first.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Activity Logs CSV", "activity_logs.csv", "CSV Files (*.csv)"
        )
        if not path:
            return

        params = {}
        emp = self._emp_filter.text().strip()
        if emp:
            params["employee_id"] = emp
        if self._user_searched:
            params["date"] = self._date_filter.date().toString("yyyy-MM-dd")

        self._export_btn.setEnabled(False)
        self._export_btn.setText("Exporting…")

        def _done(all_rows):
            self._export_btn.setEnabled(True)
            self._export_btn.setText("📥  Export CSV")
            headers = ["ID", "Employee ID", "Activity", "Timestamp (IST)"]
            rows = [[r.get("id", ""), r.get("employee_id", ""),
                     r.get("activity", ""), _fmt_ts(r.get("created_at"))]
                    for r in all_rows]
            if _export_to_csv(path, headers, rows):
                QMessageBox.information(
                    self, "Export", f"Exported {len(rows)} logs (all pages) to:\n{path}")
            else:
                QMessageBox.warning(self, "Export", "Failed to export CSV.")

        def _fail(e):
            self._export_btn.setEnabled(True)
            self._export_btn.setText("📥  Export CSV")
            QMessageBox.warning(self, "Export failed", str(e))

        w = _ExportWorker(f"{API_BASE_URL}/admin/logs", params, page_size=50)
        w.result.connect(_done)
        w.error.connect(_fail)
        _track_worker(self._workers, w)
        w.start()

    def _on_search_clicked(self):
        self._user_searched = True
        self._load(page=1)

    def _prev_page(self): self._load(self._page - 1)
    def _next_page(self): self._load(self._page + 1)


class _AttendanceTab(QWidget):

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._attendance: list[dict] = []
        self._page = 1
        self._user_searched = False
        self._build_ui()
        self._load()

        # BUG: is tab me refresh timer tha hi nahi — baaki paanchon tabs me
        # hai. Attendance panel khulte waqt ek baar load hoti thi aur uske
        # baad kabhi khud se update nahi hoti thi; naya login/logout dekhne
        # ke liye Refresh dabana padta tha.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(30000)
        self._refresh_timer.timeout.connect(lambda: self._load(self._page))
        self._refresh_timer.start()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        toolbar = _card()
        filter_row = QHBoxLayout(toolbar)
        filter_row.setContentsMargins(18, 12, 18, 12)
        filter_row.setSpacing(10)

        filter_row.addWidget(_muted_label("Employee ID"))
        self._emp_filter = QLineEdit()
        self._emp_filter.setPlaceholderText("e.g. EMP001")
        self._emp_filter.setFixedWidth(150)
        filter_row.addWidget(self._emp_filter)

        filter_row.addWidget(_muted_label("Date"))
        self._date_filter = QDateEdit(QDate.currentDate())
        self._date_filter.setCalendarPopup(True)
        self._date_filter.setFixedWidth(130)
        filter_row.addWidget(self._date_filter)

        search_btn = _btn("🔍  Search", variant="primary", height=34, width=110)
        search_btn.clicked.connect(self._on_search_clicked)
        filter_row.addWidget(search_btn)

        clear_btn = _btn("✕  Clear", variant="secondary", height=34, width=80)
        clear_btn.clicked.connect(self._on_clear_clicked)
        filter_row.addWidget(clear_btn)

        self._export_btn = _btn("📥  Export CSV", variant="secondary", height=34, width=140)
        self._export_btn.clicked.connect(self._export_attendance_csv)
        filter_row.addWidget(self._export_btn)

        filter_row.addStretch()
        root.addWidget(toolbar)

        self._table = _tune_table(QTableWidget(0, 6))
        self._table.setHorizontalHeaderLabels(
            ["ID", "Employee", "Login Time", "Logout Time", "Total Hours", "Status"])
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 80)
        self._table.setColumnWidth(1, 110)
        self._table.setColumnWidth(4, 120)
        self._table.setColumnWidth(5, 130)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        root.addWidget(self._table, 1)

        pag_row = QHBoxLayout()
        self._prev_btn  = _btn("◀  Prev", variant="secondary", height=32, width=92)
        self._prev_btn.clicked.connect(self._prev_page)
        self._next_btn  = _btn("Next  ▶", variant="secondary", height=32, width=92)
        self._next_btn.clicked.connect(self._next_page)
        self._page_label = _muted_label("Page 1")
        pag_row.addWidget(self._prev_btn)
        pag_row.addWidget(self._page_label)
        pag_row.addWidget(self._next_btn)
        pag_row.addStretch()
        root.addLayout(pag_row)

    def _on_search_clicked(self):
        self._user_searched = True
        self._load(page=1)

    def _on_clear_clicked(self):
        self._user_searched = False
        self._emp_filter.clear()
        self._date_filter.setDate(QDate.currentDate())
        self._load(page=1)

    def _prev_page(self): self._load(self._page - 1)
    def _next_page(self): self._load(self._page + 1)

    def _load(self, page=1):
        # BUG FIX: date picker ka koi asar nahi tha (param bheja hi nahi
        # jaata tha), aur pagination bilkul missing thi — server 50 rows
        # per page deta hai, to admin ko sirf latest 50 attendance records
        # hi dikhte the aur uske aage jaane ka koi tarika nahi tha.
        self._page = max(1, page)
        params = {"page": self._page}
        emp = self._emp_filter.text().strip()
        if emp:
            params["employee_id"] = emp
        if self._user_searched:
            params["date"] = self._date_filter.date().toString("yyyy-MM-dd")
        w = _FetchWorker(f"{API_BASE_URL}/attendance/all", params)
        w.result.connect(self._populate)
        w.error.connect(lambda e: print("Attendance error:", e))
        _track_worker(self._workers, w)
        w.start()

    def _populate(self, data: dict):
        rows = data.get("data", [])
        total = data.get("total", 0)
        self._attendance = rows
        self._page_label.setText(f"Page {self._page}  •  Total: {total}")
        self._prev_btn.setEnabled(self._page > 1)
        self._next_btn.setEnabled(self._page * 50 < total)
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self._table.setItem(i, 0, _cell(str(row.get("id", "")), mono=True, muted=True))
            self._table.setItem(i, 1, _cell(row.get("employee_id", ""), mono=True))
            self._table.setItem(i, 2, _cell(_fmt_ts(row.get("login_time")), muted=True))

            if row.get("logout_time"):
                self._table.setItem(i, 3, _cell(_fmt_ts(row.get("logout_time")), muted=True))
            else:
                # Abhi chal raha session — emoji ki jagah rang, jo baaki
                # status indicators se match karta hai.
                active = _cell("● ACTIVE")
                active.setForeground(QColor(C["success"]))
                self._table.setItem(i, 3, active)

            # Duration right-align — numbers ko align hona chahiye, warna
            # column me zigzag dikhta hai.
            self._table.setItem(i, 4, _cell(
                self._format_total_hours(row.get("total_hours")),
                mono=True, align_right=True))

            # Computed by the server against the shift the employee has now,
            # so a corrected shift fixes the history with it. Older servers do
            # not send these fields at all — fall back to a dash rather than
            # showing every row as on time.
            status = row.get("status")
            cell = _cell(row.get("status_label") or "—")
            cell.setForeground(QColor({
                "late":          C["danger"],
                "on_time":       C["success"],
                "day_off":       C["text_muted"],
                "outside_shift": C["warning"],
            }.get(status, C["text_muted"])))
            self._table.setItem(i, 5, cell)

    def _format_total_hours(self, value):
        """Backend may send None, an HH:MM:SS string, or a dict-like
        string such as "{'hours': 0, 'minutes': 6, 'seconds': 0}".
        Normalize all of these into a clean HH:MM:SS display string."""
        if value is None or value == "" or value == "None":
            return "—"

        value = str(value)

        try:
            if value.startswith("{"):
                d = ast.literal_eval(value)
                # BUG FIX: `days` ignore ho raha tha — Postgres INTERVAL 24h+
                # ki duration ko days me todta hai ({'days': 1, 'hours': 2}),
                # to 26-ghante ki session admin panel me "02:00:00" dikhti thi.
                h = int(d.get("days", 0)) * 24 + int(d.get("hours", 0))
                m = int(d.get("minutes", 0))
                s = int(d.get("seconds", 0))
            else:
                parts = value.split(".")[0].split(":")
                h, m, s = (int(p) for p in parts)
        except Exception:
            return "—"

        return f"{h:02}:{m:02}:{s:02}"

    def _export_attendance_csv(self):
        if not self._attendance:
            QMessageBox.warning(self, "Export", "No attendance records loaded.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Attendance CSV", "attendance.csv", "CSV Files (*.csv)"
        )
        if not path:
            return

        params = {}
        emp = self._emp_filter.text().strip()
        if emp:
            params["employee_id"] = emp
        if self._user_searched:
            params["date"] = self._date_filter.date().toString("yyyy-MM-dd")

        self._export_btn.setEnabled(False)
        self._export_btn.setText("Exporting…")

        def _done(all_rows):
            self._export_btn.setEnabled(True)
            self._export_btn.setText("📥  Export CSV")
            headers = ["ID", "Employee ID", "Login Time (IST)", "Logout Time (IST)",
                       "Total Hours", "Status", "Late (minutes)"]
            rows = []
            for row in all_rows:
                rows.append([
                    row.get("id", ""),
                    row.get("employee_id", ""),
                    # BUG FIX: pehle raw UTC string export hoti thi jabki table
                    # me IST dikhta tha — CSV 5:30 ghante peeche hota tha.
                    _fmt_ts(row.get("login_time")),
                    _fmt_ts(row.get("logout_time"), fallback="ACTIVE"),
                    self._format_total_hours(row.get("total_hours")),
                    # Payroll reads the CSV, not the screen. Leaving Status out
                    # of the export would mean the one place lateness actually
                    # gets used is the one place it is missing.
                    row.get("status_label") or "",
                    row.get("late_minutes") if row.get("late_minutes") is not None else "",
                ])
            if _export_to_csv(path, headers, rows):
                QMessageBox.information(
                    self, "Export",
                    f"Exported {len(rows)} records (all pages) to:\n{path}"
                )
            else:
                QMessageBox.warning(self, "Export", "Failed to export CSV.")

        def _fail(e):
            self._export_btn.setEnabled(True)
            self._export_btn.setText("📥  Export CSV")
            QMessageBox.warning(self, "Export failed", str(e))

        w = _ExportWorker(f"{API_BASE_URL}/attendance/all", params, page_size=50)
        w.result.connect(_done)
        w.error.connect(_fail)
        _track_worker(self._workers, w)
        w.start()


class EmployeeDetailsDialog(QDialog):
    def __init__(self, employee: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Employee Details")
        self.setMinimumWidth(760)
        self.setStyleSheet(f"QDialog {{ background: {C['bg_app']}; }}")
        self._employee = employee

        self._workers: list[QThread] = []

        
        self._token_error_shown  = False
        self._live_active_seconds = 0
        self._live_idle_seconds   = 0
        self._live_state          = None   # "ACTIVE" | "IDLE" | None
        self._employee_online     = False

        self._build_ui()
        self._load_details()

        # Live timers (UI only). Backend values are fetched periodically.
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(1000)
        self._live_timer.timeout.connect(self._tick_live_times)
        self._live_timer.start()

        self._details_refresh_timer = QTimer(self)
        self._details_refresh_timer.setInterval(10000)  # 10 seconds
        self._details_refresh_timer.timeout.connect(self._load_details)
        self._details_refresh_timer.start()




    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 22)
        root.setSpacing(16)

        header_card = _card()
        h_lay = QVBoxLayout(header_card)
        h_lay.setContentsMargins(20, 16, 20, 16)
        h_lay.setSpacing(4)

        name_row = QHBoxLayout()
        title = QLabel(self._employee.get('username', '—'))
        title.setStyleSheet(f"color:{C['text_primary']}; font-size:17px; font-weight:700; background:transparent;")
        name_row.addWidget(title)
        name_row.addStretch()
        role_pill = QLabel(str(self._employee.get('role', '—')).title())
        role_pill.setStyleSheet(
            f"background:{C['accent_soft']}; color:{C['accent_hover']}; padding:4px 12px; "
            "border-radius:10px; font-size:11px; font-weight:700;"
        )
        name_row.addWidget(role_pill)
        h_lay.addLayout(name_row)

        sub = QLabel(f"Employee ID: {self._employee.get('employee_id', '—')}")
        sub.setStyleSheet(f"color:{C['text_secondary']}; font-size:12px; background:transparent;")
        h_lay.addWidget(sub)

        root.addWidget(header_card)

        stats_grid = QGridLayout()
        stats_grid.setSpacing(14)
        self._active_time = StatCard("Active Time",   ACCENTS["green"],  "⏱")
        self._idle_time   = StatCard("Idle Time",      ACCENTS["amber"], "💤")
        self._shot_count  = StatCard("Screenshots",    ACCENTS["violet"], "📸")
        self._log_count   = StatCard("Activity Logs",  ACCENTS["cyan"],  "📝")
        for i, c in enumerate([self._active_time, self._idle_time, self._shot_count, self._log_count]):
            stats_grid.addWidget(c, 0, i)
        root.addLayout(stats_grid)

        feed_title = QLabel("Latest 10 Activity Logs")
        feed_title.setStyleSheet(f"color:{C['text_primary']}; font-weight:700; font-size:13px; background:transparent;")
        root.addWidget(feed_title)

        self._logs_table = _tune_table(QTableWidget(0, 2))
        self._logs_table.setHorizontalHeaderLabels(["Time", "Activity"])
        self._logs_table.horizontalHeader().setStretchLastSection(True)
        self._logs_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._logs_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._logs_table.setAlternatingRowColors(True)
        self._logs_table.setShowGrid(False)
        self._logs_table.verticalHeader().setVisible(False)
        root.addWidget(self._logs_table, 1)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        root.addWidget(btns)

    def _set_stats(self, details: dict):
        s = details.get('data', details)
        self._active_time.set_value(s.get('active_time', '—'))
        self._idle_time.set_value(s.get('idle_time', '—'))
        self._shot_count.set_value(s.get('screenshot_count', '—'))
        self._log_count.set_value(s.get('activity_log_count', '—'))

        rows = s.get('recent_activity', s.get('logs', []))
        if not isinstance(rows, list):
            rows = []

        self._logs_table.setRowCount(0)
        for i, row in enumerate(rows[:10]):
            self._logs_table.insertRow(i)
            t = row.get('created_at', row.get('time', '—')) if isinstance(row, dict) else '—'
            a = row.get('activity', row.get('message', str(row))) if isinstance(row, dict) else str(row)
            # BUG FIX: raw UTC string dikh rahi thi, baaki poore panel me IST hai.
            self._logs_table.setItem(i, 0, _cell(_fmt_ts(t), muted=True))
            self._logs_table.setItem(i, 1, _cell(str(a), tooltip=str(a)))

    def _load_details(self):
        emp_id = self._employee.get('employee_id')
        if not emp_id:
            return

        url = f"{API_BASE_URL}/admin/employee/{emp_id}"
        w = _FetchWorker(url)
        w.result.connect(self._on_details)
        # Guard to prevent popup spam on worker errors
        def _on_worker_error(e: str):
            self._live_timer.stop()
            self._details_refresh_timer.stop()
            # Logout ke baad aayi hui error — chup-chaap band karo (upar dekho).
            if not getattr(SessionManager, "is_authenticated", False):
                self.close()
                return
            if self._token_error_shown:
                return
            self._token_error_shown = True
            QMessageBox.warning(self, "Error", f"Failed to load details: {e}")

        w.error.connect(_on_worker_error)

        _track_worker(self._workers, w)
        w.start()

    # AFTER
    def _on_details(self, data: dict):

        # FIX: Handle expired token error with guard to prevent popup spam
        if not data.get('success'):
            error_msg = data.get('message', 'Unknown error')

            # Only show the popup once
            # BUG FIX: agar admin ne LOGOUT kar diya hai to ye popup bilkul
            # bekaar hai — user ko login screen ke upar "Session Expired,
            # please log out and log in again" dikhta tha, jabki wo already
            # logout kar chuka hai. In-flight request logout ke baad 401
            # deti hai, aur ye dialog use error samajh leta tha.
            # Ab: session already clear ho to chup-chaap band ho jao.
            self._live_timer.stop()
            self._details_refresh_timer.stop()

            if not getattr(SessionManager, "is_authenticated", False):
                self.close()
                return

            if not self._token_error_shown:
                self._token_error_shown = True
                QMessageBox.warning(
                    self,
                    "Session Expired",
                    f"Unable to load details: {error_msg}\n\nPlease log out and log in again."
                )
            return

        self._set_stats(data)

        s = data.get("data", data)

        self._live_active_seconds = self._hhmmss_to_seconds(
            s.get("active_time", "00:00:00")
        )

        self._live_idle_seconds = self._hhmmss_to_seconds(
            s.get("idle_time", "00:00:00")
        )

        # Use backend status only.
        raw_status = str(s.get("status", "")).lower()
        self._employee_online = (raw_status == "online")

        # BUG FIX: online employee ka state hamesha "ACTIVE" hardcode tha, is
        # liye "Idle Time" card kabhi tick hi nahi karta tha — employee idle
        # hone par bhi Active Time badhta rehta tha (galat reporting).
        # Ab asli latest state recent_activity se nikalte hain.
        if self._employee_online:
            self._live_state = "ACTIVE"
            for row in (s.get("recent_activity") or []):
                act = str(row.get("activity", "")).upper() if isinstance(row, dict) else ""
                if "USER IDLE" in act:
                    self._live_state = "IDLE"
                    break
                if "USER ACTIVE" in act:
                    self._live_state = "ACTIVE"
                    break
        else:
            self._live_state = None


    def _hhmmss_to_seconds(self, value: str) -> int:

        try:
            h, m, s = map(int, value.split(":"))
            return h * 3600 + m * 60 + s
        except Exception:
            return 0


    def _seconds_to_hhmmss(self, total: int) -> str:
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        return f"{h:02}:{m:02}:{s:02}"


    # AFTER
    def _tick_live_times(self):
        if not self._employee_online:
            self._active_time.set_value(self._seconds_to_hhmmss(self._live_active_seconds))
            self._idle_time.set_value(self._seconds_to_hhmmss(self._live_idle_seconds))
            return

        if self._live_state == "ACTIVE":
            self._live_active_seconds += 1
        elif self._live_state == "IDLE":
            self._live_idle_seconds += 1

        self._active_time.set_value(self._seconds_to_hhmmss(self._live_active_seconds))
        self._idle_time.set_value(self._seconds_to_hhmmss(self._live_idle_seconds))

    # Clean up timers and workers when dialog closes
    def closeEvent(self, event):
        self._live_timer.stop()
        self._details_refresh_timer.stop()
        for w in self._workers:
            w.quit()
            w.wait(1000)
        event.accept()


class _EmployeesTab(QWidget):
    def __init__(self):
        super().__init__()
        self._workers: list[QThread] = []
        self._rows: list[dict] = []
        self._search_text: str = ""
        self._page = 1
        self._total = 0

        self._build_ui()
        self._load_employees()

        # SCALE FIX: refresh 5s -> 30s. 1000+ employees aur 20 admins ke
        # saath har 5 second ka poll server pe bekaar ka load daalta hai;
        # employee list itni tezi se badalti bhi nahi.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(30000)
        self._refresh_timer.timeout.connect(lambda: self._load_employees(self._page))
        self._refresh_timer.start()

        # Search typing pe har keystroke request na bheje — 400ms debounce.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(400)
        self._search_timer.timeout.connect(lambda: self._load_employees(1))

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        header = QHBoxLayout()
        header.setSpacing(10)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("🔍  Search by Employee ID, Username or Role")
        self._search_input.setFixedHeight(38)
        self._search_input.textChanged.connect(self._on_search_changed)
        header.addWidget(self._search_input, 1)

        self._role_summary = QLabel("")
        self._role_summary.setStyleSheet(
            f"color:{C['text_secondary']};font-size:12px;font-weight:600;background:transparent;"
        )

        self._export_btn = _btn("📥  Export CSV", variant="secondary", height=38, width=140)
        self._export_btn.clicked.connect(self._export_employees_csv)
        header.addWidget(self._export_btn)

        add_btn = _btn("+  Add Employee", variant="primary", height=38, width=160)
        add_btn.clicked.connect(self._add_employee)
        header.addWidget(add_btn)

        root.addLayout(header)

        # Role caps ki live summary — search bar ke neeche
        summary_row = QHBoxLayout()
        summary_row.setContentsMargins(2, 0, 2, 6)
        summary_row.addWidget(self._role_summary)
        summary_row.addStretch()
        root.addLayout(summary_row)

        self._table = _tune_table(QTableWidget(0, 6))
        self._table.setHorizontalHeaderLabels([
            "Employee ID", "Username", "Role", "Status", "Last Seen", "Actions"
        ])
        
        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(False)
        widths = {0: 120, 1: 190, 2: 150, 3: 110, 4: 150, 5: 220}
        for col, w in widths.items():
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self._table.setColumnWidth(col, w)
        # Username ko bachi hui jagah lene do — sabse zyada variable hai.
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        # Action buttons 30px ke hain + 4px upar-neeche margin = 38px. 42px
        # ki default row me wo thik se saans nahi le pate.
        self._table.verticalHeader().setDefaultSectionSize(48)

        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        root.addWidget(self._table, 1)

        pag_row = QHBoxLayout()
        self._prev_btn = _btn("◀  Prev", variant="secondary", height=32, width=92)
        self._prev_btn.clicked.connect(self._prev_page)
        self._next_btn = _btn("Next  ▶", variant="secondary", height=32, width=92)
        self._next_btn.clicked.connect(self._next_page)
        self._page_label = _muted_label("Page 1")
        pag_row.addWidget(self._prev_btn)
        pag_row.addWidget(self._page_label)
        pag_row.addWidget(self._next_btn)
        pag_row.addStretch()
        root.addLayout(pag_row)

    def _role_label(self, role: str) -> str:
        r = (role or "").lower()
        if r == "super_admin":
            return "👑 Super Admin"
        if r == "admin":
            return "🛡 Admin"
        return "Employee"

    def _status_to_text_color(self, status: str):
        s = (status or "").lower()
        if s in ("online", "online_user"):
            return "🟢 Online", C["success"]
        if s in ("idle", "idling"):
            return "🟡 Idle", C["warning"]
        if s in ("offline", "logged_out", "disconnected"):
            return "🔴 Offline", C["danger"]
        return f"{status}", C["text_secondary"]

    def _load_employees(self, page: int | None = None):
        # SCALE FIX: pehle SAARE employees ek saath aate the aur search
        # client-side hota tha. 1000–10,000 employees pe wo request 55–117
        # second leti thi (measured) aur har 5s chalti thi. Ab server-side
        # pagination + search.
        if page is not None:
            self._page = max(1, page)
        params = {"page": self._page, "limit": 50}
        if self._search_text:
            params["search"] = self._search_text
        w = _FetchWorker(f"{API_BASE_URL}/admin/employees", params)
        w.result.connect(self._on_employees_loaded)
        w.error.connect(lambda e: print("Employees load error:", e))
        _track_worker(self._workers, w)
        w.start()

    def _on_employees_loaded(self, data: dict):
        self._rows = data.get('data', []) if isinstance(data, dict) else []
        self._total = data.get('total', len(self._rows)) if isinstance(data, dict) else 0

        # Role caps — admin ko ADD karne se pehle pata chale ki jagah bachi
        # hai ya nahi (server 409 dene se behtar hai pehle hi dikhana).
        counts = (data or {}).get("role_counts", {}) or {}
        limits = (data or {}).get("role_limits", {}) or {}
        try:
            supers = counts.get("super_admin", 0)
            admins = counts.get("admin", 0)
            emps   = counts.get("employee", 0)
            s_max  = limits.get("super_admin", 3)
            a_max  = limits.get("admin", 20)
            near = (supers >= s_max) or (admins >= a_max)
            self._role_summary.setText(
                f"👑 {supers}/{s_max} super admins     "
                f"🛡 {admins}/{a_max} admins     "
                f"👤 {emps} employees"
            )
            self._role_summary.setStyleSheet(
                f"color:{C['warning'] if near else C['text_secondary']};"
                f"font-size:12px;font-weight:600;background:transparent;"
            )
        except Exception as error:
            print("[EMPLOYEES] role summary:", error)
        self._page_label.setText(f"Page {self._page}  •  Total: {self._total}")
        self._prev_btn.setEnabled(self._page > 1)
        self._next_btn.setEnabled(self._page * 50 < self._total)
        self._display_employees(self._rows)

    def _prev_page(self): self._load_employees(self._page - 1)
    def _next_page(self): self._load_employees(self._page + 1)

    def _on_search_changed(self, text: str):
        self._search_text = text.strip()
        self._search_timer.start()

    def _apply_filter(self):
        # Search ab server-side hota hai — yahan sirf current page dikhana hai.
        self._display_employees(self._rows)

    def _export_employees_csv(self):
        filtered = self._rows
        if not filtered:
            QMessageBox.warning(self, "Export", "No employees to export.")
            return

        # Show save dialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Employees CSV", "employees.csv", "CSV Files (*.csv)"
        )
        if not path:
            return

        # Build CSV data
        headers = ["Employee ID", "Username", "Role", "Status", "Last Seen (IST)"]
        rows = []
        for emp in filtered:
            status_text, _ = self._status_to_text_color(emp.get('status'))
            rows.append([
                emp.get('employee_id', ''),
                emp.get('username', ''),
                emp.get('role', ''),
                status_text,
                _fmt_ts(emp.get('last_seen'), fallback='—'),
            ])

        if _export_to_csv(path, headers, rows):
            QMessageBox.information(self, "Export", f"Exported {len(filtered)} employees to:\n{path}")
        else:
            QMessageBox.warning(self, "Export", "Failed to export CSV.")

    def _display_employees(self, employees: list[dict]):
        self._table.setRowCount(0)
        for i, emp in enumerate(employees):
            self._table.insertRow(i)
            emp_id = emp.get('employee_id', '')
            username = emp.get('username', '')
            role = emp.get('role', '')

            status_text, status_color = self._status_to_text_color(emp.get('status'))
            
            # BUG FIX: server naive UTC string bhejta hai
            # ("2026-08-02 16:00:00"). Pehle usko `datetime.now(timezone.utc)`
            # se subtract kiya jaata tha -> TypeError (naive vs aware) -> except
            # -> raw timestamp dikh jaata tha. "Just now"/"5 min ago" kabhi
            # dikhta hi nahi tha.
            last_seen = _fmt_relative(emp.get("last_seen"))

            self._table.setItem(i, 0, _cell(str(emp_id), mono=True))
            self._table.setItem(i, 1, _cell(str(username)))
            self._table.setItem(i, 2, QTableWidgetItem(self._role_label(role)))

            st_item = QTableWidgetItem(status_text)
            st_item.setForeground(QColor(status_color))
            font = st_item.font()
            font.setBold(True)
            st_item.setFont(font)
            self._table.setItem(i, 3, st_item)

            self._table.setItem(i, 4, _cell(str(last_seen), muted=True))

            my_role  = getattr(SessionManager, "role", "employee")
            my_id    = getattr(SessionManager, "employee_id", None)
            target_r = (role or "").lower()
            tgt_super = target_r == "super_admin"
            tgt_admin = target_r == "admin"
            is_self   = my_id is not None and my_id == emp_id
            i_am_super = my_role == "super_admin"

            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(6, 4, 6, 4)
            actions_layout.setSpacing(8)

            view_btn = _btn("View", variant="secondary", height=30, width=88)
            view_btn.clicked.connect(lambda _=False, e=emp: self._open_details(e))

            manage_btn = _btn("Manage  ▾", variant="secondary", height=30, width=108)
            menu = QMenu(manage_btn)
            menu.setStyleSheet(
                "QMenu{background:#111827;border:1px solid #1e2a42;border-radius:8px;"
                "padding:6px;color:#e8edf7;}"
                "QMenu::item{padding:8px 18px;border-radius:6px;font-size:13px;}"
                "QMenu::item:selected{background:#1d4ed8;color:white;}"
                "QMenu::item:disabled{color:#5a6b85;}"
                "QMenu::separator{height:1px;background:#1e2a42;margin:5px 4px;}"
            )

            def add_action(label, slot, enabled=True, tip=""):
                act = menu.addAction(label)
                act.setEnabled(enabled)
                if tip:
                    act.setToolTip(tip)
                if enabled:
                    act.triggered.connect(slot)
                return act

            # Verbose logging — super admin ko koi aur nahi chhoo sakta;
            # admin doosre admin ka config nahi badal sakta.
            verbose_on = bool(emp.get("verbose_logging"))
            can_config = i_am_super or (not tgt_super and (not tgt_admin or is_self))
            add_action(
                "🔕  Turn verbose logging OFF" if verbose_on
                else "🔔  Turn verbose logging ON",
                lambda _=False, e=emp: self._toggle_verbose(e),
                enabled=can_config,
                tip="" if can_config else (
                    "Only a super admin can manage this account." if tgt_super
                    else "Admins cannot modify other admin accounts."),
            )

            # Force logout — admin KISI KO BHI kar sakta hai (admin ya
            # employee); sirf super admin protected hai.
            can_force = i_am_super or not tgt_super
            if is_self and tgt_super:
                can_force = False
            add_action(
                "⏻  Force logout",
                lambda _=False, e=emp: self._force_logout(e),
                enabled=can_force,
                tip="" if can_force else "The super admin cannot be force logged out.",
            )

            # Reset password — same rule as any other write on the account
            # (`can_config`), which is what the server enforces too. An admin
            # resetting another admin's password would be a way to become
            # them, so the server refuses it and the menu greys it out.
            add_action(
                "🔑  Reset password",
                lambda _=False, e=emp: self._reset_password(e),
                enabled=can_config,
                tip="" if can_config else (
                    "Only a super admin can manage this account." if tgt_super
                    else "Admins cannot modify other admin accounts."),
            )

            # Role management — sirf super admin
            if i_am_super and not is_self:
                menu.addSeparator()
                if tgt_super:
                    add_action("⬇  Remove super admin",
                               lambda _=False, e=emp: self._change_role(e, "admin"))
                else:
                    add_action(
                        "⬇  Make employee" if tgt_admin else "⬆  Make admin",
                        lambda _=False, e=emp: self._change_role(e),
                    )
                    if tgt_admin:
                        # POWER TRANSFER — super admin apni power kisi doosre
                        # admin ko de sakta hai; isi ke baad wo khud hat sakta hai.
                        add_action("👑  Make super admin",
                                   lambda _=False, e=emp: self._change_role(e, "super_admin"))

            menu.addSeparator()
            can_delete = (i_am_super or (not tgt_super and not (tgt_admin and not is_self))) \
                         and not is_self
            delete_tip = ""
            if is_self:
                delete_tip = ("You cannot delete your own super admin account. Promote "
                              "another admin to super admin first." if tgt_super
                              else "You cannot delete your own account.")
            elif tgt_super and not i_am_super:
                delete_tip = "Only a super admin can manage this account."
            elif tgt_admin and not i_am_super:
                delete_tip = "Admins cannot modify other admin accounts."
            add_action("🗑  Delete employee",
                       lambda _=False, e=emp: self._delete_employee(e),
                       enabled=can_delete, tip=delete_tip)

            manage_btn.setMenu(menu)
            actions_layout.addWidget(view_btn)
            actions_layout.addWidget(manage_btn)
            actions_layout.addStretch()

            self._table.setCellWidget(i, 5, actions_widget)

    def _open_details(self, emp: dict):
        dlg = EmployeeDetailsDialog(emp, self)
        dlg.exec()

    def _force_logout(self, emp: dict):
        emp_id = emp.get('employee_id')
        username = emp.get('username', emp_id)
        if not emp_id:
            return

        reply = QMessageBox.question(
            self,
            "Force Logout",
            f"{username} ko force logout karna chahte ho?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        w = _PostWorker(f"{API_BASE_URL}/admin/force-logout", {"employee_id": emp_id})
        w.result.connect(lambda d: QMessageBox.information(
            self,
            "Force Logout",
            "✅ Force logout set!" if d.get('success') else f"❌ {d.get('error')}"
        ))
        w.error.connect(lambda e: QMessageBox.warning(self, "Error", f"Force logout failed: {e}"))
        _track_worker(self._workers, w)
        w.start()
        
    def _reset_password(self, emp: dict):
        emp_id   = emp.get("employee_id")
        username = emp.get("username", emp_id)
        if not emp_id:
            return

        reply = QMessageBox.question(
            self,
            "Reset Password",
            f"Reset the password for {username}?\n\n"
            "They will be signed out on every device, and a temporary "
            "password will be shown here once. They must choose their own "
            "the next time they sign in.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        def show(data: dict):
            if not data.get("success"):
                QMessageBox.warning(
                    self, "Reset failed",
                    data.get("message", "The password could not be reset."),
                )
                return

            temporary = data.get("temporary_password", "")
            # Shown exactly once — the server stores only the bcrypt hash,
            # so there is no way to look this up again afterwards.
            box = QMessageBox(self)
            box.setWindowTitle("Password reset")
            box.setIcon(QMessageBox.Icon.Information)
            box.setText(f"Temporary password for {username}")
            box.setInformativeText(
                f"{temporary}\n\n"
                "Give this to them directly. It is shown only now and cannot "
                "be recovered later. They will be asked to choose their own "
                "password as soon as they sign in with it."
            )
            box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            box.exec()

        w = _PostWorker(f"{API_BASE_URL}/admin/employees/{emp_id}/password", {})
        w.result.connect(show)
        w.error.connect(lambda e: QMessageBox.warning(
            self, "Reset failed", f"Could not reach the server: {e}"))
        _track_worker(self._workers, w)
        w.start()

    def _toggle_verbose(self, emp: dict):
        emp_id = emp.get("employee_id")
        if not emp_id:
            return

        new_state = not bool(emp.get("verbose_logging"))

        w = _PostWorker(
            f"{API_BASE_URL}/admin/toggle-verbose-logging",
            {"employee_id": emp_id, "verbose_logging": new_state}
        )
        w.result.connect(lambda d: (
            self._load_employees() if d.get("success")
            else QMessageBox.warning(self, "Error", f"❌ {d.get('error', 'Toggle failed')}")
        ))
        w.error.connect(lambda e: QMessageBox.warning(self, "Error", f"Toggle failed: {e}"))
        _track_worker(self._workers, w)
        w.start()

    def _change_role(self, emp: dict, new_role: str | None = None):
        emp_id  = emp.get("employee_id")
        current = (emp.get("role") or "employee").lower()
        if not emp_id:
            return
        if new_role is None:
            new_role = "employee" if current == "admin" else "admin"

        extra = ""
        if new_role == "super_admin":
            extra = ("\n\n⚠️  A super administrator has full access. "
                     "You cannot be removed by any other user.")

        reply = QMessageBox.question(
            self, "Change Role",
            f"{emp_id} ko '{current}' se '{new_role}' banana hai?\n\n"
            f"Unki current session turant khatam ho jayegi — dobara login karna hoga."
            + extra,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        w = _PostWorker(
            f"{API_BASE_URL}/admin/employees/{emp_id}/role", {"role": new_role}
        )

        def _done(d):
            if d.get("success"):
                QMessageBox.information(self, "Role Changed", d.get("message", "Updated"))
                self._load_employees()
            else:
                QMessageBox.warning(
                    self, "Role change failed",
                    d.get("message") or d.get("error") or "Unknown error"
                )

        w.result.connect(_done)
        w.error.connect(lambda e: QMessageBox.warning(self, "Role change failed", str(e)))
        _track_worker(self._workers, w)
        w.start()

    def _delete_employee(self, emp):

        emp_id = emp.get("employee_id")

        reply = QMessageBox.question(
            self,
            "Delete Employee",
            f"{emp_id} you would like to delete this employee? This action cannot be undone.",
            QMessageBox.StandardButton.Yes |
            QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        w = _DeleteWorker(
            f"{API_BASE_URL}/admin/employees/{emp_id}"
        )

        # BUG FIX: `w.error` kabhi connect hi nahi tha — delete fail hone par
        # (404, 500, network down) admin ko KUCH nahi dikhta tha, list waisi
        # ki waisi rehti thi aur lagta tha click hi register nahi hua.
        def _on_deleted(d):
            if d.get("success"):
                QMessageBox.information(self, "Success", f"Employee {emp_id} deleted")
                self._load_employees()
            else:
                QMessageBox.warning(
                    self, "Delete failed",
                    d.get("message") or d.get("error") or "Unknown error"
                )

        w.result.connect(_on_deleted)
        w.error.connect(lambda e: QMessageBox.warning(self, "Delete failed", str(e)))

        _track_worker(self._workers, w)
        w.start()

        
    def _add_employee(self):

        dlg = QDialog(self)
        dlg.setWindowTitle("Add Employee")
        dlg.setMinimumWidth(380)
        dlg.setStyleSheet(f"QDialog {{ background: {C['bg_surface']}; }}")

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(8)

        emp_id = QLineEdit()
        username = QLineEdit()
        password = QLineEdit()
        role = QComboBox()

        password.setEchoMode(QLineEdit.EchoMode.Password)

        # Creation rules (server bhi yehi enforce karta hai):
        #   super_admin -> super_admin (max 3), admin (max 20), employee
        #   admin       -> employee HI
        #
        # Admin ko admin banane ka haq sirf super admin ke paas hai — warna
        # koi bhi admin apne aap ko unlimited admins de sakta tha.
        if getattr(SessionManager, "role", "employee") == "super_admin":
            role.addItems(["employee", "admin", "super_admin"])
        else:
            role.addItems(["employee"])
            role.setEnabled(False)
            role.setToolTip(
                "Admins can create employees.\n"
                "Only a super admin can create admin or super admin accounts."
            )

        layout.addWidget(_muted_label("Employee ID"))
        layout.addWidget(emp_id)
        layout.addSpacing(6)

        layout.addWidget(_muted_label("Username"))
        layout.addWidget(username)
        layout.addSpacing(6)

        layout.addWidget(_muted_label("Password"))
        layout.addWidget(password)
        layout.addSpacing(6)

        layout.addWidget(_muted_label("Role"))
        layout.addWidget(role)
        layout.addSpacing(16)

        save_btn = _btn("Create Employee", variant="primary", height=40)
        layout.addWidget(save_btn)

        def submit():
        
            payload = {
                "employee_id": emp_id.text().strip(),
                "username": username.text().strip(),
                "password": password.text(),
                "role": role.currentText()
            }

            worker = _PostWorker(
                f"{API_BASE_URL}/admin/employees",
                payload
            )

            # BUG FIX: pehle server ka response check kiye BINA hamesha
            # "Employee created successfully" dikhta tha. Duplicate
            # employee_id (409) ya validation error (400) pe bhi success ka
            # message aata tha aur dialog band ho jaata tha — admin ko lagta
            # employee ban gaya, jabki bana hi nahi.
            def _on_created(d):
                if d.get("success"):
                    QMessageBox.information(self, "Success", "Employee created successfully")
                    dlg.accept()
                    self._load_employees()
                else:
                    QMessageBox.warning(
                        self, "Could not create employee",
                        d.get("message") or d.get("error") or "Unknown error"
                    )

            worker.result.connect(_on_created)

            worker.error.connect(
                lambda e: QMessageBox.warning(
                    self,
                    "Error",
                    str(e)
                )
            )

            _track_worker(self._workers, worker)
            worker.start()

        save_btn.clicked.connect(submit)

        dlg.exec()


# ──────────────────────────────────────────────────────────────────────────────
#  Sidebar navigation + top header
# ──────────────────────────────────────────────────────────────────────────────

class _Sidebar(QFrame):
    pageChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("sidebar")
        self.setFixedWidth(248)
        self.logout_btn: QPushButton | None = None
        self.password_btn: QPushButton | None = None
        self._user_searched = False
        self._build()

    def select(self, index: int) -> None:
        """Programmatically ek page pe jao (Dashboard ke Quick Actions se)."""
        if 0 <= index < len(getattr(self, "_buttons", [])):
            self._buttons[index].setChecked(True)
            self.pageChanged.emit(index)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Brand
        brand = QWidget()
        b_lay = QVBoxLayout(brand)
        b_lay.setContentsMargins(24, 28, 24, 20)
        b_lay.setSpacing(2)

        badge = QLabel("ETS")
        badge.setStyleSheet(f"color:{C['accent']}; font-size:13px; font-weight:800; background:transparent;")
        title = QLabel("Employee Tracking")
        title.setWordWrap(True)
        title.setStyleSheet(f"color:{C['text_primary']}; font-size:16px; font-weight:700; background:transparent;")
        sub = QLabel("Admin Console")
        sub.setStyleSheet(f"color:{C['text_muted']}; font-size:11px; font-weight:600; background:transparent;")

        b_lay.addWidget(badge)
        b_lay.addWidget(title)
        b_lay.addWidget(sub)
        root.addWidget(brand)
        root.addWidget(_divider())

        # Nav
        nav_wrap = QWidget()
        nav_lay = QVBoxLayout(nav_wrap)
        nav_lay.setContentsMargins(0, 18, 0, 0)
        nav_lay.setSpacing(2)

        eyebrow = QLabel("MAIN MENU")
        eyebrow.setStyleSheet(f"color:{C['text_muted']}; font-size:10px; font-weight:700; background:transparent;")
        eyebrow.setContentsMargins(19, 0, 0, 10)
        nav_lay.addWidget(eyebrow)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[QPushButton] = []
        for i, page in enumerate(PAGES):
            btn = QPushButton(f"{page['icon']}    {page['title']}")
            btn.setProperty("variant", "navitem")
            btn.setCheckable(True)
            btn.setFixedHeight(42)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._group.addButton(btn, i)
            nav_lay.addWidget(btn)
            self._buttons.append(btn)

        self._buttons[0].setChecked(True)
        self._group.idClicked.connect(self.pageChanged.emit)

        nav_lay.addStretch()
        root.addWidget(nav_wrap, 1)

        # Footer
        footer = QWidget()
        f_lay = QVBoxLayout(footer)
        f_lay.setContentsMargins(16, 12, 16, 18)
        f_lay.setSpacing(12)
        f_lay.addWidget(_divider())

        role_row = QHBoxLayout()
        role_row.setSpacing(10)
        # BUG FIX: pehle yahan "A" / "Administrator" / "Full Access"
        # HARDCODED tha — chahe koi bhi login kare, sidebar hamesha yehi
        # dikhata tha. Header chip me sahi values (EMP001 / Super Admin)
        # aati thin, to ek hi screen pe do alag identities dikhti thin.
        # Ab asli session se.
        display_name = (getattr(SessionManager, "full_name", None)
                        or getattr(SessionManager, "employee_id", None)
                        or "Administrator")
        actual_role = getattr(SessionManager, "role", "admin")
        role_text = {
            "super_admin": "Super Admin  ·  Full Access",
            "admin": "Admin  ·  Employee Management",
        }.get(actual_role, "Admin")

        avatar = QLabel(display_name[:1].upper())
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(f"background:{C['accent']}; color:white; border-radius:16px; font-weight:700;")

        role_col = QVBoxLayout()
        role_col.setSpacing(0)
        name = QLabel(display_name)
        name.setStyleSheet(f"color:{C['text_primary']}; font-size:12px; font-weight:700; background:transparent;")
        role = QLabel(role_text)
        role.setStyleSheet(f"color:{C['text_muted']}; font-size:10px; background:transparent;")
        role_col.addWidget(name)
        role_col.addWidget(role)

        role_row.addWidget(avatar)
        role_row.addLayout(role_col)
        role_row.addStretch()
        f_lay.addLayout(role_row)

        # Admins are accounts too — before this there was no way for one to
        # change their own password anywhere in the app.
        self.password_btn = _btn("🔑  Change Password", variant="secondary", height=34)
        f_lay.addWidget(self.password_btn)

        self.logout_btn = _btn("🔒  Logout", variant="danger", height=38)
        f_lay.addWidget(self.logout_btn)

        root.addWidget(footer)


class _TopHeader(QFrame):
    """
    Control Center header — title + tagline + quick actions + admin chip.

    Pehle yahan sirf page ka title aur ek "Live" dot tha. Ab admin ke sabse
    common actions header me hain (pehle inke liye tab badalna padta tha):
      Refresh   — current page ka data turant reload
      Export    — current page ka CSV export
      Sync Now  — server health re-check + poora refresh
    """

    refresh_clicked = Signal()
    export_clicked  = Signal()
    sync_clicked    = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("topHeader")
        self.setFixedHeight(88)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(28, 0, 28, 0)
        lay.setSpacing(10)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self._title = QLabel("ETS Control Center")
        self._title.setStyleSheet(
            f"color:{C['text_primary']}; font-size:22px; font-weight:800; background:transparent;"
        )
        self._subtitle = QLabel("Real-time Monitoring & Management")
        self._subtitle.setStyleSheet(
            f"color:{C['text_secondary']}; font-size:12px; background:transparent;"
        )
        text_col.addWidget(self._title)
        text_col.addWidget(self._subtitle)
        lay.addLayout(text_col)
        lay.addStretch()

        def action(icon, label, slot):
            btn = QPushButton(f"  {icon}   {label}")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(44)
            btn.setStyleSheet(
                f"QPushButton{{background:{C['bg_surface']};border:1px solid {C['border']};"
                f"border-radius:10px;color:{C['text_primary']};font-size:13px;"
                f"font-weight:600;padding:0 16px;}}"
                f"QPushButton:hover{{background:{C['bg_elevated']};border-color:{C['accent']};}}"
                f"QPushButton:disabled{{color:{C['text_muted']};}}"
            )
            btn.clicked.connect(slot)
            lay.addWidget(btn)
            return btn

        self.btn_refresh = action("↻", "Refresh", self.refresh_clicked.emit)
        self.btn_export  = action("📥", "Export", self.export_clicked.emit)
        self.btn_sync    = action("☁", "Sync Now", self.sync_clicked.emit)

        chip = QFrame()
        chip.setStyleSheet(
            f"QFrame{{background:{C['bg_surface']};border:1px solid {C['border']};"
            f"border-radius:10px;}}"
        )
        cl = QHBoxLayout(chip)
        cl.setContentsMargins(12, 7, 16, 7)
        cl.setSpacing(11)
        avatar = QLabel("👤")
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(
            f"background:{C['bg_elevated']};border-radius:16px;font-size:15px;border:none;"
        )
        who = QVBoxLayout(); who.setSpacing(0)
        self._chip_id = QLabel(getattr(SessionManager, "employee_id", None) or "—")
        self._chip_id.setStyleSheet(
            f"color:{C['text_primary']};font-size:13px;font-weight:700;border:none;"
        )
        self._chip_role = QLabel(
            "Super Admin" if getattr(SessionManager, "role", "") == "super_admin" else "Admin"
        )
        self._chip_role.setStyleSheet(
            f"color:{C['success']};font-size:11px;font-weight:600;border:none;"
        )
        who.addWidget(self._chip_id); who.addWidget(self._chip_role)
        cl.addWidget(avatar); cl.addLayout(who)
        lay.addWidget(chip)

    def set_page(self, icon: str, title: str, subtitle: str):
        # Page badalne par tagline update hoti hai; brand title fixed rehta hai.
        #
        # BUG FIX: pehle poori subtitle string seedha set hoti thi. Lambi
        # subtitles (jaise Configuration ki) header ke action buttons ke
        # NEECHE chali jaati thin — screen pe aadha text kata hua dikhta tha
        # ("...upload frequency — g"). Ab available width ke hisaab se
        # elide (…) hoti hai aur poora text tooltip me milta hai.
        self._page_text = f"{icon}  {title}  ·  {subtitle}"
        self._subtitle.setToolTip(self._page_text)
        self._relayout_subtitle()

    def _relayout_subtitle(self):
        from PySide6.QtGui import QFontMetrics
        text = getattr(self, "_page_text", "")
        if not text:
            return
        # Buttons + chip ki jagah chhod ke jo bacha usi me fit karo.
        available = max(200, self.width() - 700)
        metrics = QFontMetrics(self._subtitle.font())
        self._subtitle.setText(
            metrics.elidedText(text, Qt.TextElideMode.ElideRight, available)
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_subtitle()

    def set_busy(self, busy: bool, which: str = ""):
        for name, btn in (("refresh", self.btn_refresh),
                          ("export", self.btn_export),
                          ("sync", self.btn_sync)):
            btn.setEnabled(not busy or which != name)


class AdminConfigPanel(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Amaze ETS — Control Center")
        self.setMinimumSize(1080, 680)
        self.resize(1300, 820)
        self._logging_out = False  # Guard flag to prevent recursion
        self._force_close = False  # True = asli exit (tray se), minimise nahi
        self.setStyleSheet(_global_stylesheet())

        central = QWidget()
        central.setObjectName("rootContainer")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = _Sidebar()
        root.addWidget(self.sidebar)

        content = QWidget()
        content.setObjectName("contentArea")
        c_lay = QVBoxLayout(content)
        c_lay.setContentsMargins(0, 0, 0, 0)
        c_lay.setSpacing(0)

        self.header = _TopHeader()
        # Header ke quick actions — pehle inke liye har baar tab badalna
        # padta tha; ab kisi bhi page se seedha chal jaate hain.
        self.header.refresh_clicked.connect(self._refresh_current_page)
        self.header.export_clicked.connect(self._export_current_page)
        self.header.sync_clicked.connect(self._sync_now)
        c_lay.addWidget(self.header)

        self.stack = QStackedWidget()
        # BUG FIX: pehle tabs seedha `self.stack.addWidget(_DashboardTab())`
        # se banti thin — kisi bhi tab ka reference kahin store nahi hota
        # tha. Lekin `_stop_background_services()` `self._dashboard_tab`,
        # `self._logs_tab` waghairah dhoondhta hai — wo attributes kabhi
        # EXIST hi nahi karte the, is liye getattr() hamesha None deta tha
        # aur KISI BHI tab ka 5-second refresh timer kabhi band nahi hota
        # tha. Nateeja: admin logout karne ke baad bhi Dashboard/Employees/
        # Screenshots/Logs tabs har 5 second pe server ko request bhejte
        # rehte the — cleared token ke saath, yaani endless failing calls
        # aur destroyed widgets pe callbacks (crash risk).
        self._dashboard_tab   = _DashboardTab()
        self._config_tab      = _ConfigTab()
        self._employees_tab   = _EmployeesTab()
        self._attendance_tab  = _AttendanceTab()
        self._screenshots_tab = _ScreenshotsTab()
        self._logs_tab        = _LogsTab()

        # Workers ka intezaar timers band karne ke baad, destroy se pehle.
        leftover = self._drain_workers()
        if leftover:
            print(f"[ADMIN] {leftover} worker(s) timeout ke baad bhi chal rahe hain")

        for tab in (
            self._dashboard_tab,
            self._config_tab,
            self._employees_tab,
            self._attendance_tab,
            self._screenshots_tab,
            self._logs_tab,
        ):
            self.stack.addWidget(tab)
        c_lay.addWidget(self.stack, 1)

        # ── Bottom status bar ──
        status = QFrame()
        status.setFixedHeight(36)
        status.setStyleSheet(
            f"QFrame{{background:{C['bg_surface']};border:none;"
            f"border-top:1px solid {C['border']};}}"
        )
        sb = QHBoxLayout(status)
        sb.setContentsMargins(28, 0, 28, 0)
        ver = QLabel(f"ETS Admin Console v{APP_VERSION}")
        ver.setStyleSheet(f"color:{C['text_muted']};font-size:11px;border:none;background:transparent;")
        self._status_server = QLabel("●  Connected to Production Server")
        self._status_server.setStyleSheet(
            f"color:{C['success']};font-size:11px;border:none;background:transparent;"
        )
        enc = QLabel("🔒  Encryption: AES-256 GCM")
        enc.setStyleSheet(f"color:{C['text_muted']};font-size:11px;border:none;background:transparent;")
        sb.addWidget(ver); sb.addStretch()
        sb.addWidget(self._status_server); sb.addStretch()
        sb.addWidget(enc)
        c_lay.addWidget(status)

        root.addWidget(content, 1)
        self.setCentralWidget(central)

        # Dashboard ke Quick Actions ko sidebar navigation se joda
        for label, (btn, page_index) in getattr(
            self._dashboard_tab, "_quick_buttons", {}
        ).items():
            btn.clicked.connect(
                lambda _=False, idx=page_index: self.sidebar.select(idx)
            )

        self.sidebar.pageChanged.connect(self._on_page_changed)
        self.sidebar.logout_btn.clicked.connect(self.logout)
        self.sidebar.password_btn.clicked.connect(self._change_own_password)
        self._on_page_changed(0)
        self.scheduler = SchedulerService()
        self.scheduler.screenshot_triggered.connect(self.capture_screenshot)
        if hasattr(self.scheduler, "force_logout"):
            self.scheduler.force_logout.connect(self.logout)
        self.scheduler.start()

        self.idle_tracker = IdleTracker()
        # BUG: the tracker was started but its signal was connected to
        # nothing, so an admin's own idle/active state never reached the UI.
        # (Tracking itself still worked — IdleTracker writes its own log and
        # DB rows inside check_idle — but the admin could not see any of it.)
        self.idle_tracker.status_changed.connect(self._on_own_idle)
        self.idle_tracker.start()

        # Session start comes from the local `shifts` row that
        # ShiftManager.start_shift_local() writes at login.
        self._session_start = None
        try:
            conn = Database.connect()
            row = conn.execute(
                "SELECT login_time FROM shifts WHERE employee_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (SessionManager.employee_id,),
            ).fetchone()
            conn.close()
            if row and row[0]:
                self._session_start = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

        self._own_timer = QTimer(self)
        self._own_timer.timeout.connect(self._tick_own_session)
        self._own_timer.start(1000)
        self._tick_own_session()
        self._on_own_idle("WORKING")

        self._setup_tray()

    # ── This admin's own session ─────────────────────────────────────────

    def _tick_own_session(self):
        card = getattr(self._dashboard_tab, "m_session", None)
        if card is None:
            return
        if not self._session_start:
            card.set_value("00:00:00")
            return
        secs = int((datetime.now() - self._session_start).total_seconds())
        if secs < 0:
            secs = 0
        card.set_value(
            f"{secs // 3600:02}:{(secs % 3600) // 60:02}:{secs % 60:02}",
            f"Started {self._session_start.strftime('%I:%M %p')}",
        )

    def _on_own_idle(self, status: str):
        card = getattr(self._dashboard_tab, "m_activity", None)
        if card is None:
            return
        idle = str(status).upper() == "IDLE"
        card.set_value(
            "IDLE" if idle else "WORKING",
            "No input detected" if idle else "Keyboard & mouse active",
        )
        card.push_point(0 if idle else 1)

    def _update_own_shots(self, count):
        card = getattr(self._dashboard_tab, "m_myshots", None)
        if card is not None:
            card.set_value(str(count), "Captured today")
            card.push_point(count)

    def _refresh_current_page(self):
        """Header ka Refresh — jo page khula hai usi ka data reload."""
        self.header.set_busy(True, "refresh")
        self._refresh_page(self.stack.currentWidget())
        QTimer.singleShot(900, lambda: self.header.set_busy(False))

    def _export_current_page(self):
        """Header ka Export — current page ka CSV (jahan export supported hai)."""
        page = self.stack.currentWidget()
        for method in ("_export_attendance_csv", "_export_logs_csv",
                       "_export_employees_csv"):
            fn = getattr(page, method, None)
            if callable(fn):
                fn()
                return
        QMessageBox.information(
            self, "Export",
            "This page has no CSV export.\n\n"
            "Export is available on Employees, Attendance and Audit Logs.",
        )

    def _sync_now(self):
        """Server health re-check + poora dashboard refresh."""
        self.header.set_busy(True, "sync")

        def probe():
            import requests as _rq
            _rq.get(f"{API_BASE_URL}/health", timeout=8).raise_for_status()
            return True

        def ok(_r):
            self.header.set_busy(False)
            self._status_server.setText("●  Connected to Production Server")
            self._status_server.setStyleSheet(
                f"color:{C['success']};font-size:11px;border:none;background:transparent;"
            )
            self._dashboard_tab._load_all()

        def fail(error):
            self.header.set_busy(False)
            self._status_server.setText("●  Server unreachable")
            self._status_server.setStyleSheet(
                f"color:{C['danger']};font-size:11px;border:none;background:transparent;"
            )
            QMessageBox.warning(self, "Sync failed", str(error))

        worker = _FetchWorker(f"{API_BASE_URL}/health")
        worker.result.connect(ok)
        worker.error.connect(fail)
        _track_worker(getattr(self, "_workers", []), worker)
        self._workers = getattr(self, "_workers", [])
        worker.start()

    def _on_page_changed(self, idx: int):
        self.stack.setCurrentIndex(idx)
        page = PAGES[idx]
        self.header.set_page(page["icon"], page["title"], page["subtitle"])

        # Saari tabs panel khulte waqt EK BAAR load hoti hain. Uske baad tab
        # switch karne pe kuch nahi hota tha — Attendance pe jaate to wahi
        # data dikhta jo panel khulte waqt aaya tha, jab tak 30s ka timer na
        # chale (aur Attendance me to timer tha hi nahi). Isi liye lagta tha
        # ki data sirf Refresh dabane pe aata hai.
        #
        # Ab har baar tab kholne pe uska data taaza hota hai — sirf US page
        # ka jo khula hai, saare tabs ka nahi.
        self._refresh_page(self.stack.currentWidget())

    @staticmethod
    def _refresh_page(page):
        """Jo bhi load method us tab pe maujood ho, use call karo."""
        for method in ("_load_all", "_load", "_load_employees", "refresh"):
            fn = getattr(page, method, None)
            if callable(fn):
                try:
                    fn()
                except TypeError:
                    fn(1)
                except Exception:
                    pass
                break

    def capture_screenshot(self):
        result = ScreenshotManager.capture_screenshot()
        # Reflect it on the admin's own card. None means the capture was
        # skipped (super admin) or failed, so the count must not move.
        if result is not None:
            self._own_shots = getattr(self, "_own_shots", 0) + 1
            self._update_own_shots(self._own_shots)

    def _drain_workers(self, timeout_ms: int = 3000) -> int:
        """
        Chal rahe network workers ka bounded intezaar (logout se pehle).

        BUG FIX: Qt me chalte hue QThread ka object destroy hone par
        "QThread: Destroyed while thread is still running" -> std::terminate
        -> app crash. Admin panel har tab pe workers banata hai; slow server
        pe logout dabate hi ye crash trigger ho sakta tha.
        """
        pending = []
        for attr in ("_dashboard_tab", "_config_tab", "_employees_tab",
                     "_attendance_tab", "_screenshots_tab", "_logs_tab"):
            tab = getattr(self, attr, None)
            if tab is not None:
                pending.extend(getattr(tab, "_workers", []) or [])
        pending.extend(getattr(self, "_workers", []) or [])

        still_running = 0
        for worker in pending:
            try:
                if not worker.isRunning():
                    continue
                for signal in ("finished", "error"):
                    sig = getattr(worker, signal, None)
                    if sig is not None:
                        try:
                            sig.disconnect()
                        except (RuntimeError, TypeError):
                            pass
                worker.requestInterruption()
                if not worker.wait(timeout_ms):
                    still_running += 1
            except RuntimeError:
                pass
        return still_running

    def _stop_background_services(self):
        """Sirf timers/threads/workers rokta hai — session ko touch nahi
        karta. closeEvent aur logout() dono isko use karte hain."""
        if hasattr(self, 'scheduler'):
            self.scheduler.stop()
        if hasattr(self, 'idle_tracker'):
            self.idle_tracker.stop()

        for tab_attr in ['_config_tab', '_screenshots_tab', '_logs_tab', '_attendance_tab', '_employees_tab', '_dashboard_tab']:
            tab = getattr(self, tab_attr, None)
            if tab is None:
                continue

            # Har timer band karo — sirf `_refresh_timer` nahi. Dashboard tab
            # ka `_charts_timer` aur Employees tab ka `_search_timer` alag
            # hain; unhe chhodne se logout ke baad bhi requests jaati rehtin.
            for timer_attr in ('_refresh_timer', '_charts_timer', '_search_timer'):
                timer = getattr(tab, timer_attr, None)
                if timer is not None:
                    try:
                        timer.stop()
                    except Exception:
                        pass

            # BUG FIX: workers har TAB pe hote hain (`tab._workers`), panel pe
            # nahi. Pehle sirf `self._workers` dekha jaata tha jo
            # AdminConfigPanel pe kabhi define hi nahi hota — yaani koi bhi
            # in-flight request thread kabhi properly band nahi hota tha.
            # Logout ke waqt ye threads apne callbacks ke saath zinda rehte
            # the aur already-destroyed widgets ko touch kar sakte the.
            # NOTE: yahan `quit()` jaan-boojh kar nahi hai — override kiye
            # gaye `run()` wale QThread me event loop hota hi nahi, to
            # quit() bekaar hai. Asli intezaar `_drain_workers()` upar kar
            # chuka hai (signals disconnect karke, bounded wait ke saath).
            # Yahan bas ek aakhri short wait, aur jo phir bhi chal raha ho
            # use DELETE nahi karte — wahi crash ki wajah banta hai.
            for w in list(getattr(tab, '_workers', [])):
                try:
                    if w.isRunning():
                        w.wait(300)
                except Exception:
                    pass

    def _change_own_password(self):
        from client.presentation.windows.change_password_dialog import (
            ChangePasswordDialog,
        )
        dialog = ChangePasswordDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            QMessageBox.information(
                self, "Password changed",
                "Your password has been changed.\n\n"
                "Any other device you were signed in on has been signed out.",
            )

    def logout(self):
        from client.application.managers.session_manager import SessionManager
        from client.application.managers.shift_manager import ShiftManager
        from client.application.managers.session_log_manager import SessionLogManager
        from client.presentation.windows.login_window import LoginWindow
        if self._logging_out:
            return

        self._logging_out = True

        self._stop_background_services()

        # BUG FIX: admin panel se logout karne par LOGOUT kabhi log nahi hota
        # tha (employee dashboard aur system tray dono me hota hai). Is wajah
        # se admin/super-admin ka session end kabhi Audit Logs me record hi
        # nahi hota tha — audit trail me gap.
        # clear_session() se PEHLE, warna employee_id None ho jaata hai aur
        # LoggerService.log() chup-chaap return kar deta hai.
        try:
            LoggerService.log("LOGOUT")
        except Exception:
            pass

        try:
            SessionLogManager.end_session()
        except Exception as e:
            pass

        try:
            ShiftManager.end_shift()
        except Exception as e:
            pass

        # Tray hata do — warna logout ke baad bhi icon padha rehta hai aur
        # uska menu ek band ho chuke panel ko point karta hai.
        tray = getattr(self, "tray", None)
        if tray is not None:
            try:
                tray.hide()
                tray.deleteLater()
            except Exception:
                pass
            self.tray = None

        SessionManager.clear_session()

        self.login_window = LoginWindow()
        self.login_window.show()

        QMainWindow.close(self)

    def closeEvent(self, event):

        if self._logging_out or self._force_close:
            event.accept()
            return

        # MINIMISE TO TRAY — pehle admin panel band karte hi poora app quit
        # ho jaata tha (QApplication.setQuitOnLastWindowClosed(True) hai).
        # Employee panel me ye pehle se tha, admin me chhoot gaya tha.
        #
        # Admin ke liye ye zaroori hai kyunki panel band karne par background
        # services (scheduler, config sync) bhi ruk jaati thin — admin ko
        # dobara pura login karna padta tha sirf ek employee dekhne ke liye.
        if getattr(self, "tray", None) is not None and self.tray.isVisible():
            event.ignore()
            self.hide()
            if not getattr(self, "_tray_hint_shown", False):
                self._tray_hint_shown = True
                self.tray.showMessage(
                    "Amaze ETS",
                    "Still running in the background. Use the tray icon to reopen.",
                    QSystemTrayIcon.MessageIcon.Information,
                    4000,
                )
            return

        # Tray available nahi (kuch Linux desktops) — purana behaviour
        self._stop_background_services()
        event.accept()

    # ── SYSTEM TRAY ──────────────────────────────────────────────────────

    def _setup_tray(self):
        """Admin panel ka tray icon.

        Employee wali `SystemTray` yahan reuse nahi ki kyunki uska menu
        employee ke windows kholta hai (View Logs / Settings) aur uska
        Exit employee ka session end karta hai — admin ke liye galat.
        """
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(_app_icon() or self.windowIcon())
        self.tray.setToolTip("Amaze ETS — Control Center")

        menu = QMenu()
        act_open = QAction("🖥  Open Control Center", menu)
        act_open.triggered.connect(self._restore_from_tray)
        menu.addAction(act_open)

        act_refresh = QAction("↻  Refresh Current Page", menu)
        act_refresh.triggered.connect(self._refresh_current_page)
        menu.addAction(act_refresh)

        menu.addSeparator()

        act_logout = QAction("↪  Logout", menu)
        act_logout.triggered.connect(self.logout)
        menu.addAction(act_logout)

        act_quit = QAction("🚪  Quit Amaze ETS", menu)
        act_quit.triggered.connect(self._quit_from_tray)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        # macOS single-click (Trigger) deta hai, Windows aksar DoubleClick —
        # dono handle karna padta hai warna ek platform pe icon dead lagta hai.
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._restore_from_tray()

    def _restore_from_tray(self):
        self.show()
        self.setWindowState(
            self.windowState() & ~Qt.WindowState.WindowMinimized
            | Qt.WindowState.WindowActive
        )
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self):
        self._force_close = True
        self._stop_background_services()
        if getattr(self, "tray", None) is not None:
            self.tray.hide()
        QApplication.quit()

