from __future__ import annotations

import ast
import re
import requests
from client.core import http as _http
from datetime import date, datetime
from datetime import datetime, timezone
from PySide6.QtCore    import (Qt, QThread, Signal, QDate, QTimer, QSize,
                               QRectF, QPointF)
from client.presentation.windows.screenshot_preview_window import ScreenshotPreviewWindow
# THE SAME DOWNLOADER THE PREVIEW WINDOW USES. It already streams, already
# cancels mid-flight and already knows the endpoint; a second copy here would
# be a second thing to fix when that endpoint changes.
from client.presentation.windows.screenshot_preview_window import (
    _DownloadWorker as _ShotDownloadWorker)
from client.security.crypto_engine import CryptoEngine
from PySide6.QtGui     import (QFont, QColor, QAction, QIcon, QPixmap,
                               QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    QPlainTextEdit,
    QPushButton,
    QSpinBox, QDoubleSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QInputDialog,
    QSizePolicy
)

from client.application.managers.session_manager import SessionManager
# NEVER IMPORTED, AND NOBODY COULD TELL.
#
# logout() has called LoggerService.log("LOGOUT") for a long time, wrapped in
# `except Exception: pass` — so every call raised NameError and was swallowed.
# The comment above that line says it was added because an admin signing out
# never reached the Audit Logs; the line went in, the import did not, and the
# gap it was written to close stayed open.
from client.services.logger_service import LoggerService
from client.application.services import notifier
from client.infrastructure.database.database import Database
from client.application.schedulers.scheduler_service import SchedulerService
from client.application.managers.screenshot_manager import ScreenshotManager
from client.application.managers.idle_tracker import IdleTracker
from client.presentation.theme import ADMIN as _THEME_ADMIN
from client.presentation import theme as _theme
from client.presentation.theme import Radius, Space, Type, Weight
from client.presentation.widgets.avatar import Avatar, ClickableAvatar
from client.core.config import API_BASE_URL, APP_VERSION


# ──────────────────────────────────────────────────────────────────────────────
#  Design tokens — single source of truth for the whole admin panel
# ──────────────────────────────────────────────────────────────────────────────
# The admin console's palette. It lives in theme.py so that one switch moves
# this console and the employee panel together; the name is kept here because
# ninety-odd call sites in this file and admin_teams_tab read `C[...]`.
#
# Bound to the SAME dict object theme.set_theme() mutates — rebinding it to a
# new dict would leave this module pointing at the old colours after a switch.
C = _THEME_ADMIN

# Card accents. Rebuilt from the palette on every read so they follow the
# theme — the light palette needs darker greens and ambers than the dark one,
# or a "success" tile is unreadable on white.
def _accents() -> dict:
    return {
        "blue":   C["accent"],
        "green":  C["success"],
        "amber":  C["warning"],
        "violet": "#8b5cf6",
        "cyan":   "#06b6d4",
        "slate":  C["text_muted"],
        "red":    C["danger"],
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

# HOW THE MENU IS GROUPED.
#
# Fifteen entries under one heading called MAIN MENU is a list, not a menu.
# Nothing tells you that Attendance, Leave and Payroll are three views of the
# same subject, or that My Leave is about YOU rather than about everybody —
# and the two were sitting next to each other, one row apart, with almost the
# same name.
#
# The order is by how often it is opened, not by what it is called: the
# dashboard first, the people and their time next, the monitoring after that,
# and the reader's own account last, because it is the one thing they can
# always find.
NAV_SECTIONS = [
    ("OVERVIEW",     ["dashboard", "alerts"]),
    ("PEOPLE",       ["employees", "teams", "mychat"]),
    ("TIME & PAY",   ["attendance", "leave", "payroll", "reports"]),
    ("MONITORING",   ["screenshots", "logs"]),
    ("YOU",          ["myleave", "mypayroll", "profile"]),
    ("SYSTEM",       ["config"]),
]

# The status bar's server name lives in client.core.config, because the
# employee panel says the same thing and said it wrongly in its own way.
from client.core.config import server_label as _server_label  # noqa: E402


def _section_of(key: str) -> str:
    """Which menu section a page sits in, for the breadcrumb.

    Reads NAV_SECTIONS rather than repeating it: a page moved between
    sections must not be able to say one thing in the menu and another in
    the breadcrumb.
    """
    for heading, keys in NAV_SECTIONS:
        if key in keys:
            return heading.title()
    return ""


PAGES = [
    {"key": "dashboard","icon": "", "title": "Dashboard",
     "subtitle": "Live overview of your workforce and activity."},
    {"key": "alerts","icon": "", "title": "Alerts",
     "subtitle": "What needs attention right now — apps that have stopped reporting, shifts nobody logged in for, and unusual idle time."},
    {"key": "config","icon": "️", "title": "Configuration",
     "subtitle": "Set screenshot intervals, idle thresholds and upload frequency — globally or per employee."},
    {"key": "employees","icon": "", "title": "Employees",
     "subtitle": "Manage accounts, roles and live status."},
    {"key": "attendance","icon": "", "title": "Attendance",
     "subtitle": "Track login, logout times and shift hours."},
    {"key": "screenshots", "icon": "", "title": "Screenshots",
     "subtitle": "Browse captured screenshots by employee and date."},
    {"key": "teams","icon": "", "title": "Teams & Chat",
     "subtitle": "Teams, channels and membership. Conversations are readable only by a super admin, and every read is recorded."},
    {"key": "mychat","icon": "️", "title": "My Chat",
     "subtitle": "The channels you are a member of. This is your own conversation — reading somebody else's is done from Teams & Chat, and is recorded."},
    {"key": "payroll","icon": "", "title": "Payroll",
     "subtitle": "Salaries, and a month's pay built from attendance and approved leave. A finalised month stops moving — anything after it is an adjustment, on the record."},
    {"key": "leave","icon": "", "title": "Leave",
     "subtitle": "Requests waiting on a decision, and every one already decided. Approving or rejecting is recorded against whoever did it."},
    {"key": "reports","icon": "", "title": "Reports",
     "subtitle": "Attendance summary over a date range — present, absent, late and hours."},
    {"key": "logs","icon": "", "title": "Audit Logs",
     "subtitle": "Detailed activity history for compliance and review."},
    # An administrator is an account like any other, and asked for it in
    # those words: "admin ka bhi profile hona chahiye". The same page the
    # employee panel shows — one screen, so a change to it reaches everybody.
    # AN ADMIN IS AN EMPLOYEE TOO. They take leave and they are on payroll —
    # the same pages the employee panel shows, because it is the same person
    # asking the same questions about themselves. Without these an admin could
    # approve everybody's leave and had nowhere to ask for their own.
    {"key": "myleave","icon": "️", "title": "My Leave",
     "subtitle": "Your own time off. An administrator cannot decide their own request — a super admin does."},
    {"key": "mypayroll","icon": "", "title": "My Payroll",
     "subtitle": "Your own payslips, once a month has been finalised."},
    {"key": "profile","icon": "", "title": "My Profile",
     "subtitle": "Your account, your devices and your week."},
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


def _fmt_date_only(ts, fallback="—") -> str:
    """17 Aug 2026. Split from the time because one column showing both is
    what forced the login and logout columns to be twice as wide as they
    needed to be."""
    dt = _parse_server_ts(ts)
    if dt is None:
        return fallback
    return dt.astimezone(IST).strftime("%d %b %Y")


def _fmt_time_only(ts, fallback="—") -> str:
    """07:37 PM."""
    dt = _parse_server_ts(ts)
    if dt is None:
        return fallback
    return dt.astimezone(IST).strftime("%I:%M %p")


def _fmt_elapsed(seconds) -> str:
    """9420 -> "02:37:00". Zero-padded so a running clock does not change
    width as it ticks, which makes the whole column jitter."""
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        return "—"
    return f"{total // 3600:02}:{(total % 3600) // 60:02}:{total % 60:02}"


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
    QWidget {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: {_theme.Type.BODY}px; color: {C['text_primary']}; }}
    QLabel {{ background: transparent; }}

    QWidget#sidebar {{ background: {C['bg_sidebar']}; border-right: 1px solid {C['border']}; }}
    QFrame#topHeader {{ background: {C['bg_app']}; border-bottom: 1px solid {C['border']}; }}

    /* Inputs */
    QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox {{
        background: {C['bg_surface_alt']};
        border: 1px solid {C['border']};
        border-radius: {_theme.Radius.CONTROL}px;
        padding: 10px 12px;
        color: {C['text_primary']};
        font-size: {_theme.Type.BODY}px;
        selection-background-color: {C['accent']};
    }}
    /* A FOCUS RING, not just a coloured edge. Qt has no box-shadow, so the
       ring is a second border drawn by thickening this one — enough that
       the focused field is obvious without the layout shifting, which is
       why the padding above absorbs the extra pixel. */
    QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus,
    QDoubleSpinBox:focus {{
        border: 2px solid {C['accent']};
        padding: 9px 11px;
        background: {C['bg_surface']};
    }}
    QLineEdit::placeholder {{ color: {C['text_muted']}; }}
    /* THE BLACK SPOT, AND WHY IT WAS THERE.
       Touching a sub-control at all takes it off the platform's own drawing
       and onto the stylesheet's — but only for what the rule mentions. These
       two set a width and nothing else, so the ARROW inside kept falling
       back to the native macOS stepper: a dark rounded block that reads as a
       black smudge on a dark card. It appeared on every row of the
       configuration page because every row carries a spin box, and it was
       reported in exactly those terms.
       Given an image, each arrow is a Lucide chevron like every other arrow
       in the product, and the button behind it is transparent so the field's
       own background shows through. */
    QComboBox::drop-down {{ border: none; width: 22px; background: transparent; }}
    QComboBox::down-arrow {{
        image: url("{_icons.icon_file('chevron-down', 12, C['text_muted'])}");
        width: 12px; height: 12px;
    }}
    QComboBox QAbstractItemView {{
        background: {C['bg_surface']};
        border: 1px solid {C['border_light']};
        border-radius: {_theme.Radius.CONTROL}px;
        color: {C['text_primary']};
        selection-background-color: {C['accent']};
        outline: none;
        padding: 4px;
    }}
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
    QDateEdit::up-button, QDateEdit::down-button {{
        width: 18px; border: none; background: transparent;
        margin-right: 4px;
    }}
    QSpinBox::up-button, QDoubleSpinBox::up-button, QDateEdit::up-button {{ subcontrol-position: top right; height: 14px; }}
    QSpinBox::down-button, QDoubleSpinBox::down-button,
    QDateEdit::down-button {{ subcontrol-position: bottom right; height: 14px; }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QDateEdit::up-arrow {{
        image: url("{_icons.icon_file('chevron-up', 10, C['text_muted'])}");
        width: 10px; height: 10px;
    }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QDateEdit::down-arrow {{
        image: url("{_icons.icon_file('chevron-down', 10, C['text_muted'])}");
        width: 10px; height: 10px;
    }}
    /* Hover tells the stepper apart from decoration. */
    QSpinBox::up-button:hover, QSpinBox::down-button:hover,
    QDateEdit::up-button:hover, QDateEdit::down-button:hover {{
        background: {C['bg_elevated']}; border-radius: {_theme.Radius.CHIP}px;
    }}

    /* BUG FIX: QCheckBox::indicator ka koi rule tha hi nahi. Native indicator
       is dark theme pe bilkul dikhta hi nahi tha — "Verbose logging" ke aage
       khaali jagah dikhti thi aur pata hi nahi chalta tha ki wo ON hai ya OFF. */
    QCheckBox {{ background: transparent; spacing: 10px;
                 font-size: {_theme.Type.BODY}px; color: {C['text_primary']}; }}
    QCheckBox::indicator {{
        width: 18px; height: 18px;
        border: 1.5px solid {C['border_light']};
        border-radius:12px;
        background: {C['bg_surface_alt']};
    }}
    QCheckBox::indicator:hover {{ border-color: {C['accent']}; }}
    /* A TICK, NOT A BLUE SQUARE.
     *
     * This rule ended in `image: none`, so a checked box was a filled blue
     * square with nothing in it — on from off told apart by colour alone.
     * Somebody scanning four of them in a settings panel cannot say which
     * are on, and a colourblind reader cannot say at all. The mark is an SVG
     * data URI so it needs no file on disk and cannot go missing from a
     * build. */
    QCheckBox::indicator:checked {{
        background: {C['accent']};
        border: 1.5px solid {C['accent']};
        image: url("{_icons.icon_file("check", 12, "#ffffff")}");
    }}
    QCheckBox::indicator:disabled {{ border-color: {C['border']};
                                     background: {C['bg_surface']}; }}

    QCalendarWidget QWidget {{ background: {C['bg_surface']}; color: {C['text_primary']}; }}
    QCalendarWidget QToolButton {{ background: transparent; color: {C['text_primary']}; padding: 4px; }}
    QCalendarWidget QAbstractItemView:enabled {{
        background: {C['bg_surface']}; color: {C['text_primary']};
        selection-background-color: {C['accent']}; selection-color: white;
    }}

    /* Tables */
    QTableWidget {{
        background: {C['bg_surface']};
        gridline-color: transparent;
        border: 1px solid {C['border']};
        border-radius: {_theme.Radius.CARD}px;
        color: {C['text_primary']};
        font-size: {_theme.Type.BODY}px;
        selection-background-color: {C['accent_soft']};
        selection-color: {C['text_primary']};
        outline: none;
    }}
    /* NO ZEBRA STRIPING. Alternating fills draw a pattern across the table
       that competes with the data in it; a very quiet hover tells you which
       row you are on, which is the only thing the stripes were doing. */
    QTableWidget::item {{
        /* 4px, not 10px, top and bottom.
         *
         * BUG this fixes: item padding is taken out of the cell BEFORE a cell
         * widget is given its geometry. At 10px it ate 21px of every row, so
         * a 42px row left 21px for the widget — and every button in every
         * table was drawn 32px tall into a 21px hole and clipped. It looked
         * like a rendering fault rather than a measurement, which is why it
         * survived two attempts to fix it by changing the button. */
        padding: 4px 12px;
        border: none;
        border-bottom: 1px solid {C['border']};
    }}
    QTableWidget::item:selected {{
        background: {C['accent_soft']};
        color: {C['text_primary']};
    }}
    QTableWidget::item:hover {{ background: {C['hover']}; }}
    /* THE TICK BOX INSIDE A TABLE IS NOT A QCheckBox.
     *
     * The Screenshots list checks rows with an ITEM check state, and that
     * indicator is `QTableWidget::indicator` — a different sub-control from
     * the QCheckBox one styled below, and it had no rule at all. So macOS
     * drew its own: on the light theme, a row of large glossy red circles
     * down the left of the table, which is how it was found. Same shape,
     * same accent and same tick as every other box in the product. */
    QTableWidget::indicator {{
        width: 16px; height: 16px;
        border: 1.5px solid {C['border_light']};
        border-radius: {_theme.Radius.CHIP}px;
        background: {C['bg_surface_alt']};
    }}
    QTableWidget::indicator:hover {{ border-color: {C['accent']}; }}
    QTableWidget::indicator:checked {{
        background: {C['accent']}; border: 1.5px solid {C['accent']};
        image: url("{_icons.icon_file("check", 11, "#ffffff")}");
    }}
    QHeaderView::section {{
        background: {C['bg_surface']};
        color: {C['text_muted']};
        padding: 14px 12px;
        border: none;
        border-bottom: 1px solid {C['border']};
        font-weight: 600;
        font-size: {_theme.Type.MICRO}px;
        text-transform: uppercase;
        letter-spacing: 0.6px;
    }}
    QTableCornerButton::section {{ background: {C['bg_surface_alt']}; border: none; }}

    /* Scrollbars — thin, and only as visible as they need to be. */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {C['border_light']};
                                   border-radius:12px; min-height: 32px; }}
    QScrollBar::handle:vertical:hover {{ background: {C['text_muted']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {C['border_light']};
                                     border-radius:12px; min-width: 32px; }}
    QScrollBar::handle:horizontal:hover {{ background: {C['text_muted']}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    /* Buttons — two styles and a soft danger, per the brief. */
    QPushButton {{
        background: {C['bg_surface']};
        border: 1px solid {C['border']};
        border-radius: {_theme.Radius.CONTROL}px;
        padding: 9px 16px;
        color: {C['text_primary']};
        font-weight: 500;
        font-size: {_theme.Type.BODY}px;
    }}
    QPushButton:hover {{ background: {C['hover']}; border-color: {C['border_light']}; }}
    QPushButton:disabled {{ color: {C['text_muted']}; background: {C['bg_surface_alt']};
                            border-color: {C['border']}; }}

    /* A DISABLED BUTTON HAS TO LOOK DISABLED, WHATEVER VARIANT IT IS.
       ─────────────────────────────────────────────────────────────────
       The rule above was the only one, and it lost to every variant below
       it: `QPushButton:disabled` and `QPushButton[variant="danger"]` have
       the SAME specificity in Qt's CSS, so the one written later wins — and
       the variants are all written later. Only `primary` had a disabled rule
       of its own.

       So a disabled Finalize rendered pixel for pixel like an enabled one:
       same soft-red fill, same red label. Measured rather than guessed — the
       fill and the text row came back as the same two colours either way, in
       both themes. tests/test_button_variants.py keeps that measurement.

       WHAT THAT LOOKED LIKE TO SOMEBODY USING IT: "Payroll → Finalize does
       nothing when clicked." Finalize is disabled until the month in the box
       has a draft, and disabled until it is not already finalised — both
       correct, and both invisible. The button looked ready, the click went
       nowhere, and there was no message because nothing had gone wrong.

       `[variant]:disabled` is (0,2,0) against a variant's (0,1,0), so it wins
       over all of them on specificity rather than on order. It is placed
       BEFORE the primary rule below so that primary's own disabled treatment,
       which is equally specific and comes later, still wins for primary. */
    QPushButton[variant]:disabled {{ background: {C['bg_surface_alt']};
                                     color: {C['text_muted']};
                                     border: 1px solid {C['border']};
                                     font-weight: 500; }}

    QPushButton[variant="primary"] {{ background: {C['accent']}; border: 1px solid {C['accent']};
                                      color: #ffffff; font-weight: 600; }}
    QPushButton[variant="primary"]:hover {{ background: {C['accent_hover']};
                                            border-color: {C['accent_hover']}; }}
    QPushButton[variant="primary"]:pressed {{ background: {C['accent_pressed']}; }}
    QPushButton[variant="primary"]:disabled {{ background: {C['bg_elevated']};
                                               color: {C['text_muted']};
                                               border-color: {C['border']}; }}

    QPushButton[variant="secondary"] {{ background: {C['bg_surface']};
                                        border: 1px solid {C['border']};
                                        color: {C['text_primary']}; }}
    QPushButton[variant="secondary"]:hover {{ background: {C['hover']};
                                              border-color: {C['border_light']}; }}

    QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent;
                                    color: {C['text_secondary']}; }}
    QPushButton[variant="ghost"]:hover {{ background: {C['hover']};
                                          color: {C['text_primary']}; }}

    QPushButton[variant="warning"] {{ background: {C['warning_soft']};
                                      border: 1px solid rgba(245,158,11,0.32);
                                      color: {C['warning']}; }}
    QPushButton[variant="warning"]:hover {{ background: rgba(245,158,11,0.20); }}

    /* SOFT, NOT BRIGHT. A saturated red button reads as an error message
       rather than as a control, and this one sits in the sidebar all day. */
    QPushButton[variant="danger"] {{ background: {C['danger_soft']};
                                     border: 1px solid rgba(239,68,68,0.32);
                                     color: {C['danger']}; font-weight: 600; }}
    QPushButton[variant="danger"]:hover {{ background: rgba(239,68,68,0.20); }}

    QPushButton[variant="danger-solid"] {{ background: {C['danger_strong']};
                                           border: 1px solid {C['danger_strong']};
                                           color: white; }}
    /* From the palette. A literal here belongs to whichever theme it was
       sampled from, and shows up unchanged in the other one. */
    QPushButton[variant="danger-solid"]:hover {{ background: {C['danger_strong']};
                                                 border-color: {C['danger_strong']}; }}

    QPushButton[variant="navitem"] {{
        background: transparent;
        border: none;
        border-left: 2px solid transparent;
        border-radius: {_theme.Radius.CONTROL}px;
        text-align: left;
        padding: 0px 12px 0px 12px;
        color: {C['text_secondary']};
        font-weight: 500;
        font-size: {_theme.Type.BODY}px;
    }}
    /* A WASH, NOT A BLOCK. The hover used a solid elevated colour, which on
       a 48px row is a large filled rectangle appearing under the pointer —
       the brief calls it out, and it is the difference between an app that
       feels responsive and one that feels like it is flashing at you. */
    QPushButton[variant="navitem"]:hover {{
        background: {C['hover']};
        color: {C['text_primary']};
    }}
    /* Active: a soft blue field and a blue rule down the left edge. The old
       one filled the whole row with solid accent and white text, which drew
       more attention than the page it pointed at. */
    /* Something is waiting on this page. Red, because the whole point of
       the count is that it is noticed without being looked for. */
    QPushButton[variant="navitem"][unread="true"] {{
        color: {C['danger']};
        font-weight: 600;
    }}
    QPushButton[variant="navitem"]:checked {{
        background: {C['accent_soft']};
        border-left: 2px solid {C['accent']};
        color: {C['text_primary']};
        font-weight: 600;
    }}

    /* Dialogs / message boxes */
    QDialog {{ background: {C['bg_app']}; }}
    QMessageBox {{ background: {C['bg_surface']}; }}
    QMessageBox QLabel {{ color: {C['text_primary']}; }}
    QMessageBox QPushButton {{
        min-width: 84px; padding: 7px 14px; border-radius: {_theme.Radius.CONTROL}px;
        background: {C['bg_surface_alt']}; border: 1px solid {C['border_light']}; color: {C['text_primary']};
    }}
    QMessageBox QPushButton:hover {{ background: {C['bg_elevated']}; }}

    QToolTip {{
        background: {C['bg_elevated']}; color: {C['text_primary']};
        border: 1px solid {C['border_light']}; padding: 4px 8px; border-radius: {_theme.Radius.CHIP}px;
    }}

    QListWidget {{
        background: {C['bg_surface_alt']}; border: 1px solid {C['border']}; border-radius: {_theme.Radius.CARD}px;
        padding: 6px; outline: none;
    }}
    QListWidget::item {{ padding: 10px 12px; margin: 2px 0px; border-radius: {_theme.Radius.CONTROL}px; color: {C['text_secondary']}; }}
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


# The shortest a button can be before the global stylesheet's own padding
# starts cutting it off. Measured, not guessed: with `padding: 7px 14px` and
# a 13px label, minimumSizeHint().height() comes out at 31.
# 36, not 32. The floor was measured against 13px text with 7px padding; the
# design brief moved those to 14px and 9px, and the same buttons started
# being clipped again — by four pixels, which reads as a rendering fault
# rather than as a size. Measured from minimumSizeHint, not guessed.
# 40 — the floor the design brief sets for a control, and above what any
# label in this panel needs at 14px with 9px of padding. minimumSizeHint is
# still consulted below, because it is measured and this is a policy.
_MIN_BUTTON_HEIGHT = 40


def _fit_columns(table: "QTableWidget", stretch: int | None = None,
                 pad: int = 30) -> None:
    """Size every column to the widest thing actually in it.

    WHY NOT ResizeToContents ALONE. Qt asks the item delegate for a size
    hint, and the delegate does not know about the stylesheet — this panel
    styles cells with `padding: 4px 12px`, twenty-four pixels the delegate
    never accounts for. So ResizeToContents produced columns eighteen pixels
    too narrow across the board: Payroll drew "₹36,666." with the paise cut
    off, and Reports drew its whole Employee column as "…".

    A truncated number on a payroll page is not a cosmetic fault. It is a
    figure somebody can read and act on.

    `stretch` names a column that takes any slack left over, so a table
    narrower than its window does not end in dead space.
    """
    from PySide6.QtGui import QFontMetrics

    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    metrics = QFontMetrics(table.font())

    for column in range(table.columnCount()):
        widest = 0
        head = table.horizontalHeaderItem(column)
        if head is not None:
            # Headers are uppercased and letter-spaced by the stylesheet, so
            # they measure wider than the string suggests.
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

    # STRETCH ONLY IF THERE IS SLACK TO TAKE.
    #
    # Qt's Stretch mode divides the AVAILABLE width, and it does not respect
    # what the column needs — so on the Reports table, whose twelve columns
    # already overflow the window, stretching the Employee column squeezed it
    # to 100px and drew every name as "…". The column it was meant to help
    # was the one it destroyed.
    #
    # When the content is wider than the viewport the right answer is to let
    # the table scroll sideways, which it now does.
    if stretch is not None and 0 <= stretch < table.columnCount():
        total = sum(table.columnWidth(c) for c in range(table.columnCount()))
        viewport = table.viewport().width()
        if viewport > 0 and total < viewport:
            header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)


def _btn(text: str, variant: str = "secondary", height: int = 36, width: int | None = None) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("variant", variant)
    # A floor, not the requested value.
    #
    # BUG this fixes: call sites asked for 24 or 26 to fit buttons into table
    # rows, and setFixedHeight honoured it — so the bottom of every one of
    # them was clipped, across the Employees, Screenshots, Attendance and
    # Teams tabs. It looked like a rendering glitch rather than a size anyone
    # had chosen, which is why it survived so long.
    # THE BUTTON'S OWN MEASUREMENT, not a constant.
    #
    # A fixed floor is only right until the type scale moves. It was 32,
    # measured against 13px text; the brief moved text to 14px and padding to
    # 9px, and buttons began clipping again — by one pixel on "Manage ▾",
    # which is invisible in a diff and looks like a rendering fault on screen.
    # minimumSizeHint knows what this label needs in this font, today.
    b.setFixedHeight(max(height, _MIN_BUTTON_HEIGHT, b.minimumSizeHint().height()))
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
    # Read-only, always.
    #
    # Every table in this panel displays data; none of them edits it in place.
    # Qt makes cells editable by default, so a double-click opens a text box
    # over the cell — the admin types, nothing is saved, and it looks as
    # though a rename silently failed. Each tab had been turning this off for
    # itself, which meant the Teams tab shipped without it and showed exactly
    # that. Setting it here makes it impossible to forget again.
    #
    # Checkbox cells still work: NoEditTriggers stops the editor opening, not
    # the check state changing, so the Screenshots tab's selection is
    # unaffected.
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setAlternatingRowColors(False)  # zebra striping competes with the data
    table.setShowGrid(False)
    table.verticalHeader().setVisible(False)
    # 44, and it has to stay at least ITEM_PADDING*2 + border above the
    # tallest thing a cell can hold — see _MIN_BUTTON_HEIGHT.
    # 52: a 40px control, the 4px of item padding above and below it, and the
    # few pixels Qt takes for the row itself. At 44 the row offered 35px to a
    # 36px button and clipped every one of them, across four tabs.
    table.verticalHeader().setDefaultSectionSize(52)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)       # dotted focus box hata do
    table.setWordWrap(False)
    table.horizontalHeader().setHighlightSections(False)
    table.horizontalHeader().setFixedHeight(40)
    # Header default center-aligned hota hai lekin cells left-aligned hain —
    # dono match karne chahiye, warna column ka text header se khisak kar
    # dikhta hai.
    table.horizontalHeader().setDefaultAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    # COLUMNS SIZE TO WHAT IS IN THEM, and the table scrolls sideways rather
    # than cutting anything off.
    #
    # THE FAULT THIS FIXES. Widths were chosen by hand, per table, against a
    # 13px font. The design brief moved text to 14px and every one of them
    # became too narrow at once: Payroll drew "₹36,666." with the paise cut
    # off, Reports drew the whole Employee column as "…", and the headers
    # read "WORKIN(", "TOTAL HOUI", "SCREENSH(". On a payroll page a
    # truncated number is not a cosmetic problem — it is a figure somebody
    # might read and act on.
    #
    # ResizeToContents asks the cell what it needs. Tables that want one
    # column to take the slack set Stretch on it afterwards; this is only the
    # floor, and a floor that cannot cut a number in half.
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setMinimumSectionSize(72)
    table.setHorizontalScrollMode(
        QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
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
        item.setToolTip(_theme.tip(tooltip))
    return item


# The shared chip, so this panel and the leave and payroll pages cannot end
# up with three different ideas of what a status looks like.
from client.presentation.widgets.card import card as _card_frame  # noqa: E402
from client.presentation.widgets.surface import (  # noqa: E402
    clear_background as _clear_bg, transparent_host as _transparent_host)
from client.presentation.widgets import icons as _icons  # noqa: E402
from client.presentation.widgets.brand import BrandLockup as _BrandLockup  # noqa: E402
from client.presentation.widgets.badge import (  # noqa: E402
    badge_cell, badge_label, badge_cell as _badge_cell)


def _centred(widget):
    """A cell widget, centred in its cell.

    setCellWidget fills the whole cell, so a 34px button in a 78px row is
    drawn stretched from top to bottom unless something holds it in the
    middle. Every table in this panel that puts a control in a cell wants
    this, so it lives here rather than in each of them.
    """
    holder = QWidget()
    _clear_bg(holder)
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    row.setAlignment(Qt.AlignmentFlag.AlignCenter)
    row.addWidget(widget)
    return holder


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


def _align_numeric_headings(table: "QTableWidget", columns) -> None:
    """Right-align the HEADING over a right-aligned column.

    A left-aligned heading does not sit above its own numbers: on a column
    wider than its title, the figures drift right until they are under the
    NEXT heading. Read quickly, Bilal's "2" absent days appeared under "Late
    minutes" — the data was right and the table said something else.
    """
    for column in columns:
        item = table.horizontalHeaderItem(column)
        if item is not None:
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                  | Qt.AlignmentFlag.AlignVCenter)


def _round_avatar(widget, size: int) -> QIcon:
    """A widget's rendering, clipped to a circle.

    WHY THE CLIP IS DONE HERE. Avatar is round because its stylesheet gives it
    a border-radius, and a stylesheet radius is a PAINTING instruction — it
    does not clip the widget. QWidget.grab() re-renders into a plain
    rectangle, so an avatar grabbed straight into a table icon came out as a
    blue square with the initials squeezed into it.
    """
    source = widget.grab()
    rounded = QPixmap(source.size())
    rounded.fill(Qt.GlobalColor.transparent)

    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addEllipse(0, 0, source.width(), source.height())
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, source)
    painter.end()
    return QIcon(rounded)


def _card(padding: int = 0) -> QFrame:
    """A surface card.

    The objectName scoping this used to do by hand now lives in
    widgets/card.py, because two other places built cards WITHOUT it and
    inherited the bug this docstring used to describe on its own: a plain
    QFrame selector styles every QFrame inside the card too, so each divider
    became a bordered, rounded box.
    """
    return _card_frame(bg=C["bg_surface"], border=C["border"])


def _optional_date(floor: QDate, *, unset_text: str = "—  not given") -> QDateEdit:
    """A date that may be left unset, and whose calendar opens somewhere sane.

    A QDateEdit cannot be empty, so "not given" has to be a value nobody would
    type: the minimum. Qt then shows `unset_text` in place of it, which is the
    right behaviour and produces a wrong one the moment the calendar is opened
    — the popup lands on the minimum, so an unset joining date opened on
    JANUARY 1970 and an unset date of birth on 1900. Somebody setting a date
    for the first time had to page through half a century to reach this year.
    Reported as "date me 1970 se start kyun ho raha hai".

    setCurrentPage moves the month the popup DISPLAYS without selecting
    anything, so the field still reads "not given" until a day is clicked.
    """
    field = QDateEdit()
    field.setCalendarPopup(True)
    field.setDisplayFormat("yyyy-MM-dd")
    field.setMinimumDate(floor)
    field.setSpecialValueText(unset_text)
    field.setDate(floor)
    today = QDate.currentDate()
    calendar = field.calendarWidget()
    if calendar is not None:
        calendar.setCurrentPage(today.year(), today.month())
    return field


def _size_table(table: "QTableWidget", cap: int = 340) -> None:
    """Make a table as tall as its rows need, and no taller.

    Inside a scroll area a stretch factor means nothing, so these tables were
    given fixed heights instead — which leaves a five-row table in a box three
    rows deep, and the vertical scrollbar that appears then eats the width the
    last column needed, so the figures in it are cut off too. A payroll page
    that truncates a number is not a cosmetic fault.

    The cap keeps a long table from pushing everything below it off the page;
    past that it scrolls on its own.
    """
    rows = table.rowCount()
    row_height = table.verticalHeader().defaultSectionSize()
    needed = (table.horizontalHeader().height() + rows * row_height
              + 2 * table.frameWidth())
    table.setFixedHeight(min(needed, cap))


# The company's salary structure, as the server states it. Filled by whoever
# fetches it first and shared by every page that previews a CTC.
#
# NOT A DEFAULT. It starts empty and stays empty until the server answers,
# and a page with no template shows no preview — because the alternative is a
# preview built from a guess, and a guess about how somebody's pay divides is
# the one thing this page must not show. The arrangement is configurable
# (app_settings.salary_template); a copy written into the client agreed with
# it on the day it was written and nothing kept them agreeing.
_SALARY_TEMPLATE: list[dict] = []


def _remember_salary_template(rows) -> None:
    """Keep the split the server just sent, for the pages that preview it."""
    if isinstance(rows, list) and rows:
        _SALARY_TEMPLATE[:] = rows


def _ctc_preview(ctc_annual: float,
                 template: list | None = None) -> list[tuple[str, str, float]]:
    """What an annual CTC divides into each month: (name, how, amount).

    THE SAME ARRANGEMENT THE SERVER APPLIES, because it IS the server's — the
    template is fetched, not written down here. Computed on this side only so
    the effect of a figure is visible while it is being typed; what gets SAVED
    is the CTC, and the server splits it again on arrival.

    Returns nothing at all when the template is not known yet. A blank preview
    says "not known"; a preview from a built-in default says something
    specific and possibly wrong about somebody's pay.
    """
    rows_in = template if template is not None else _SALARY_TEMPLATE
    if not rows_in:
        return []

    monthly = float(ctc_annual or 0) / 12.0

    def money2(value: float) -> float:
        return round(value + 1e-9, 2)

    # Basic is the one the allowances are shares OF, so it is found first and
    # kept unrounded — rounding it before taking a percentage of it spreads
    # that rounding into every allowance. The server does the same.
    basic_row = next((r for r in rows_in
                      if str(r.get("rule")) == "PERCENT_CTC"), None)
    exact_basic = monthly * float(basic_row.get("value") or 0) / 100 if basic_row else 0.0

    rows, spent = [], 0.0
    for row in rows_in:
        rule = str(row.get("rule") or "")
        value = float(row.get("value") or 0)
        if rule == "PERCENT_CTC":
            amount, how = money2(monthly * value / 100), f"{value:g}% of CTC"
        elif rule == "PERCENT_BASIC":
            amount, how = money2(exact_basic * value / 100), f"{value:g}% of Basic"
        elif rule == "FIXED":
            amount, how = money2(value), "Fixed"
        else:
            amount, how = None, "Balance"
        if amount is not None:
            spent += amount
        rows.append([str(row.get("name") or "—"), how, amount])

    # THE BALANCE IS WHAT IS LEFT, so the parts always add back to the whole.
    # Computing it as a percentage too would leave a few rupees unaccounted
    # for every month, and a payslip whose components do not sum to its gross
    # is a payslip somebody has to explain.
    for entry in rows:
        if entry[2] is None:
            entry[2] = money2(max(0.0, monthly - spent))
    return [(name, how, amount) for name, how, amount in rows]


def _stat_card(title: str, caption: str, icon_name: str,
               tint: str) -> tuple[QFrame, QLabel]:
    """A figure, with a tinted icon, a heading and a caption under it.

    Returns the card and the label the value goes in, so the caller keeps its
    own handle on the number and this stays about the chrome.

    IT STARTS AT AN EM DASH, not at zero. A card that says ₹0.00 while it is
    still loading is a statement about the month, and a wrong one; the dash
    says "not known yet" and is replaced by whatever the server sends.

    The icon names come from the set in widgets/icons — 75 of them. A name
    that is not in it returns a null pixmap, which draws as an empty square
    and looks exactly like a card that failed to load.
    """
    card = _card()
    outer = QHBoxLayout(card)
    outer.setContentsMargins(16, 12, 16, 12)
    outer.setSpacing(10)
    box = QVBoxLayout()
    box.setSpacing(2)
    outer.addLayout(box, 1)

    # THE ICON CARRIES THE MEANING BEFORE THE WORDS ARE READ. Five cards of
    # identical grey text is a row somebody scans past; the tint is what makes
    # "deductions" findable without reading.
    badge = QLabel()
    badge.setFixedSize(34, 34)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setPixmap(_icons.pixmap(icon_name, 17, tint))
    badge.setStyleSheet(
        f"background:{C['bg_surface_alt']};"
        f"border-radius:{Radius.CONTROL}px;"
        f"border:1px solid {C['border_light']};")
    outer.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)

    heading = QLabel(title.upper())
    heading.setStyleSheet(
        f"color:{C['text_muted']};font-size:{Type.MICRO}px;"
        f"font-weight:700;letter-spacing:0.6px;"
        f"background:transparent;border:none;")
    box.addWidget(heading)

    value = QLabel("—")
    value.setStyleSheet(
        f"color:{C['text_primary']};font-size:{Type.TITLE}px;"
        f"font-weight:700;background:transparent;border:none;")
    box.addWidget(value)

    note = QLabel(caption)
    note.setStyleSheet(
        f"color:{C['text_muted']};font-size:{Type.MICRO}px;"
        f"background:transparent;border:none;")
    box.addWidget(note)

    return card, value


def _fmt_minutes(total) -> str:
    """95 -> "1h 35m". Matches the Attendance page's Late column."""
    try:
        total = int(total or 0)
    except (TypeError, ValueError):
        return "—"
    if total <= 0:
        return "—"
    if total < 60:
        return f"{total}m"
    hours, rest = divmod(total, 60)
    return f"{hours}h {rest}m" if rest else f"{hours}h"


def _money(value) -> str:
    """Rupees, or a dash. One formatter, so every table on a page agrees."""
    try:
        return f"₹{float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "—"


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
        from PySide6.QtGui import QPainter, QColor, QFont
        from PySide6.QtCore import QSize, Qt, QRect
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

    def __init__(self, label: str, accent: str, icon: str = "activity", value="—",
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
            f" border: 1px solid {C['border']}; border-radius:16px; }}"
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

        # Lucide when the caller names one, the raw glyph otherwise, so a
        # card that has not been migrated still draws something.
        badge = QLabel()
        if _icons.known(icon):
            badge.setPixmap(_icons.pixmap(icon, 18, accent))
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            badge.setText(icon)
        badge.setFixedSize(36, 36)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background: rgba({_hex_to_rgb(accent)}, 0.16); "
            f"border-radius: {_theme.Radius.CONTROL}px; font-size: {_theme.Type.SECTION}px;"
        )
        lay.addWidget(badge)

        self._value_label = QLabel(str(value))
        self._value_label.setStyleSheet(
            f"color:{C['text_primary']}; font-size:{_theme.Type.HEADING}px; "
            f"font-weight:600; background:transparent;"
        )
        lay.addWidget(self._value_label)

        cap = QLabel(label)
        cap.setStyleSheet(f"color:{C['text_secondary']}; font-size:{_theme.Type.MICRO}px; "
                          f"font-weight:500; background:transparent;")
        lay.addWidget(cap)

        self._sub_label = QLabel("")
        self._sub_label.setStyleSheet(
            f"color:{C['text_muted']}; font-size:12px; background:transparent;"
        )
        lay.addWidget(self._sub_label)

        self._spark = None
        if sparkline:
            from client.presentation.widgets.panel_widgets import Sparkline
            self._spark = Sparkline(accent)
            _clear_bg(self._spark, "border:none;")
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
            r = _http.get(
                self._url,
                params=self._params,
                headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                # (connect, read) — NOT one number for both.
                #
                # A single `timeout=10` gives a dead or firewalled server ten
                # seconds of silence before it gives up, and this worker runs
                # a BLOCKING request: Qt's quit() cannot interrupt it, so for
                # those ten seconds the thread cannot be stopped. Quit the app
                # in that window and Qt destroys a running QThread, which
                # aborts the process — "Python quit unexpectedly", with no
                # traceback and nothing to go on.
                #
                # Three seconds is plenty to LEARN a server is unreachable,
                # while ten still allows a slow report to finish answering.
                timeout=(3, 10),
            )

            # BUG this fixes: the status code was never looked at. A 401, a
            # 500 or anything else came back as JSON, got emitted on
            # `result`, and every page treated it as data. Since the error
            # body has none of the expected fields, `.get(key, 0)` filled the
            # dashboard with zeros — "0 employees, 0 screenshots, 0 activity
            # logs" is indistinguishable from a wiped database, and that is
            # exactly how it was read when it happened.
            #
            # A failed request now goes to `error`, where callers already
            # leave the previous numbers on screen.
            if not r.ok:
                message = f"HTTP {r.status_code}"
                try:
                    body = r.json()
                    if isinstance(body, dict) and body.get("message"):
                        message = f"{message}: {body['message']}"
                except Exception:
                    pass
                self.error.emit(message)
                return

            payload = r.json()

            # A 200 carrying success:false is the same situation with a
            # friendlier status code.
            if isinstance(payload, dict) and payload.get("success") is False:
                self.error.emit(payload.get("message", "request failed"))
                return

            self.result.emit(payload)
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
            r = _http.post(
                self._url,
                json=self._body,
                headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                # (connect, read) — NOT one number for both.
                #
                # A single `timeout=10` gives a dead or firewalled server ten
                # seconds of silence before it gives up, and this worker runs
                # a BLOCKING request: Qt's quit() cannot interrupt it, so for
                # those ten seconds the thread cannot be stopped. Quit the app
                # in that window and Qt destroys a running QThread, which
                # aborts the process — "Python quit unexpectedly", with no
                # traceback and nothing to go on.
                #
                # Three seconds is plenty to LEARN a server is unreachable,
                # while ten still allows a slow report to finish answering.
                timeout=(3, 10),
            )
            self.result.emit(r.json())
        except Exception as e:
            self.error.emit(str(e))

class _RequestWorker(QThread):
    """Any method, off the UI thread, returning whatever the server said.

    _PostWorker above is POST-only. Rather than adding a near-identical
    _PatchWorker, this takes the method — the next one of these needs no new
    class at all.

    IT DOES NOT RAISE ON A 4xx. The server answers a refused edit with a
    sentence explaining which rule was broken, and that sentence is the whole
    value of the response — turning it into a generic transport error would
    throw away the only useful part.
    """
    result = Signal(dict)
    error  = Signal(str)

    def __init__(self, method: str, url: str, body: dict | None = None):
        super().__init__()
        self._method = method
        self._url = url
        self._body = body or {}

    def run(self):
        try:
            r = _http.request(
                self._method,
                self._url,
                json=self._body,
                headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                timeout=15,
            )
            try:
                self.result.emit(r.json())
            except ValueError:
                self.error.emit(f"The server replied with {r.status_code}.")
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
                r = _http.get(
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
            r = _http.delete(
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
    """Settings, global and per employee.

    NO AUTO-REFRESH HERE, DELIBERATELY. Every other page polls; this one is a
    form. A refresh that lands while somebody is halfway through changing the
    idle threshold would replace what they had typed with what the server
    still holds, and they would not necessarily notice. The page reloads when
    it is opened and when Refresh is pressed, which is when the reader is
    asking for it.
    """

    def __init__(self):
        super().__init__()
        self._employees: list[dict] = []
        self._workers:   list       = []
        self._build_ui()
        self._load_employees()
        self._refresh_holidays()
        self._refresh_retention()
        self._refresh_alert_settings()
        self._refresh_alert_email_settings()
        self._refresh_upcoming()

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
            f"color:{C['text_muted']}; font-size:12px; background:transparent;"
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
                f"color:{C['text_muted']}; font-size:12px; background:transparent;"
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
        head.setSpacing(Space.SM)
        # AN EMPTY BADGE IS WORSE THAN NO BADGE.
        #
        # This drew whatever string it was handed. When the emoji were
        # removed the callers were left passing "", so every section on the
        # Configuration page kept its tinted 30px square with nothing inside
        # — a row of black holes, reported as exactly that. It now takes an
        # icon NAME, and a caller that supplies nothing gets no badge at all
        # rather than an empty one.
        badge = None
        if _icons.known(icon):
            badge = QLabel()
            badge.setFixedSize(32, 32)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setPixmap(_icons.pixmap(icon, 17, C["accent"]))
            badge.setStyleSheet(
                f"background:{C['accent_soft']};"
                f"border-radius:{_theme.Radius.CONTROL}px;")
        titles = QVBoxLayout()
        titles.setSpacing(0)
        heading = QLabel(title)
        heading.setStyleSheet(
            f"color:{C['text_primary']}; font-size:{_theme.Type.SECTION}px; font-weight:600;"
            f"background:transparent;"
        )
        sub = QLabel(subtitle)
        # WRAPS. Without this a section's description is one unbreakable line,
        # and the longest of them asked for 1195px — wider than the page. The
        # whole Configuration tab then grew a horizontal scrollbar and pushed
        # every value box off the right-hand edge, so the settings could be
        # read but not seen.
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"color:{C['text_muted']}; font-size:{_theme.Type.SMALL}px;"
            f"background:transparent;")
        titles.addWidget(heading)
        titles.addWidget(sub)
        if badge is not None:
            head.addWidget(badge)
        head.addLayout(titles)
        head.addStretch()
        lay.addLayout(head)
        lay.addSpacing(8)

        for row in rows:
            lay.addWidget(row)
        return card

    # ── Alert thresholds ─────────────────────────────────────────────────
    #
    # These numbers, and not the code, decide what an alert means. That is
    # deliberate: what counts as late, and how much idle is too much, are the
    # owner's decisions about their own company. The defaults exist so the
    # feature works on day one, not as a recommendation.
    def _build_alerts_section(self) -> QFrame:
        self._alert_on = QCheckBox("Show alerts in the panel")
        # WHERE THE ALERTS GO, and what has actually gone out.
        #
        # An alert that only lives on a page is one somebody has to think to
        # open — and the alerts worth having are exactly the ones nobody is
        # thinking about that morning.
        self._alert_email_to = QLineEdit()
        self._alert_email_to.setPlaceholderText("owner@company.com, hr@company.com")
        self._alert_digest = QCheckBox("Send a summary every evening")
        self._alert_digest_hour = QSpinBox()
        self._alert_digest_hour.setRange(0, 23)
        self._alert_email_state = QLabel("")
        self._alert_email_state.setWordWrap(True)
        self._alert_email_state.setStyleSheet(
            f"color:{C['text_muted']};font-size:12px;background:transparent;")
        self._alert_silent = QSpinBox(); self._alert_silent.setRange(1, 720)
        self._alert_late   = QSpinBox(); self._alert_late.setRange(0, 1440)
        self._alert_idle   = QSpinBox(); self._alert_idle.setRange(15, 1440)
        for spin in (self._alert_silent, self._alert_late, self._alert_idle):
            spin.setFixedHeight(36)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._alert_status = QLabel("Loading…")
        self._alert_status.setWordWrap(True)
        self._alert_status.setStyleSheet(
            f"color:{C['text_muted']}; font-size:12px; background:transparent;")

        save = _btn("\U0001F4BE  Save alert settings", variant="primary", height=36)
        save.clicked.connect(self._save_alert_settings)
        row = QWidget()
        row.setObjectName("alertSaveRow")
        row.setStyleSheet("QWidget#alertSaveRow { background: transparent; }")
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.addStretch()
        row_lay.addWidget(save)

        return self._build_section(
            "bell", "Alerts  ·  applies to everyone",
            "The Alerts page shows what needs attention now. These numbers "
            "decide when something is worth saying. Nobody is ever alerted on "
            "a weekly off or a holiday.",
            [
                self._setting_row(
                    "Alerts on",
                    "Turn the whole thing off without losing these settings.",
                    self._alert_on),
                self._setting_row(
                    "App silent for",
                    "No heartbeat, no login and no screenshot for this long "
                    "means the app has stopped. This is the alert that matters "
                    "most — a stopped app and a day off look identical without it.",
                    self._alert_silent, "hours"),
                self._setting_row(
                    "Late by",
                    "Counted AFTER the shift's own grace period. An employee "
                    "with no shift set is never chased.",
                    self._alert_late, "minutes"),
                self._setting_row(
                    "Idle in a day",
                    "Total idle time in one day. A reason to look at the day, "
                    "not a conclusion about it.",
                    self._alert_idle, "minutes"),
                row,
                self._alert_status,
            ])

    def _email_section(self):
        """Who is told, without having to open the panel to find out."""
        send_now = _btn("Send now", variant="secondary", height=40, width=120)
        send_now.clicked.connect(self._run_alert_emails)
        save = _btn("Save", variant="primary", height=40, width=100)
        save.clicked.connect(self._save_alert_email_settings)

        buttons = QWidget()
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(save)
        row.addWidget(send_now)
        row.addStretch()

        return self._build_section(
            "cloud-upload", "Alerts by email",
            "The same alerts, sent to somebody rather than waiting to be "
            "found. Each person is emailed once per problem per day — the "
            "second time it is still true, nothing is sent, or the mail "
            "becomes noise nobody reads.",
            [
                self._setting_row(
                    "Send to",
                    "Comma separated. Leave empty and nothing is sent.",
                    self._alert_email_to),
                self._setting_row(
                    "Daily summary",
                    "Everything from the day in one message, including the "
                    "things not worth interrupting for. Sent even when there "
                    "is nothing — silence and a broken job look the same.",
                    self._alert_digest),
                self._setting_row(
                    "Summary at",
                    "IST, on the 24-hour clock.",
                    self._alert_digest_hour, "o'clock"),
                buttons,
                self._alert_email_state,
            ])

    def _refresh_alert_email_settings(self):
        w = _FetchWorker(f"{API_BASE_URL}/admin/alerts/email")

        def fill(data: dict):
            if not data.get("success"):
                return
            self._alert_email_to.setText(", ".join(data.get("recipients") or []))
            self._alert_digest.setChecked(bool(data.get("digest", True)))
            self._alert_digest_hour.setValue(int(data.get("digest_hour") or 19))

            counts = data.get("counts") or {}
            recent = data.get("recent") or []
            if not data.get("can_send"):
                # The reason comes from the server and names the missing
                # setting — far better than "email not working".
                self._alert_email_state.setText(
"" + (data.get("unavailable_reason") or "Email is not set up."))
                return
            last = recent[0] if recent else None
            self._alert_email_state.setText(
                f"Sent {counts.get('sent', 0)} · failed {counts.get('failed', 0)} "
                f"in the last 30 days."
                + (f"  Last: {last.get('subject','')[:60]} "
                   f"({last.get('status')})" if last else "  Nothing sent yet."))

        w.result.connect(fill)
        w.error.connect(lambda e: self._alert_email_state.setText(f"Could not read: {e}"))
        _track_worker(self._workers, w)
        w.start()

    def _save_alert_email_settings(self):
        body = {
            "recipients": self._alert_email_to.text().strip(),
            "digest": self._alert_digest.isChecked(),
            "digest_hour": self._alert_digest_hour.value(),
        }
        w = _PostWorker(f"{API_BASE_URL}/admin/alerts/email", body)

        def done(data):
            if data.get("success"):
                self._alert_email_state.setText("Saved.")
                self._refresh_alert_email_settings()
            else:
                self._alert_email_state.setText(
                    data.get("message") or "Could not save.")

        w.result.connect(done)
        w.error.connect(lambda e: self._alert_email_state.setText(str(e)))
        _track_worker(self._workers, w)
        w.start()

    def _run_alert_emails(self):
        self._alert_email_state.setText("Sending…")
        w = _PostWorker(f"{API_BASE_URL}/admin/alerts/email/run", {})

        def done(data):
            if data.get("reason"):
                self._alert_email_state.setText(data["reason"])
                return
            self._alert_email_state.setText(
                f"Sent {data.get('sent', 0)}, failed {data.get('failed', 0)}, "
                f"already sent today {data.get('skipped', 0)}.")
            self._refresh_alert_email_settings()

        w.result.connect(done)
        w.error.connect(lambda e: self._alert_email_state.setText(str(e)))
        _track_worker(self._workers, w)
        w.start()

    def _refresh_alert_settings(self):
        w = _FetchWorker(f"{API_BASE_URL}/admin/alerts/settings")

        def fill(data: dict):
            settings = data.get("settings") or {}
            self._alert_on.setChecked(bool(settings.get("alerts_enabled", True)))
            for key, spin in (("alert_silent_hours", self._alert_silent),
                              ("alert_late_login_minutes", self._alert_late),
                              ("alert_idle_minutes", self._alert_idle)):
                if settings.get(key) is not None:
                    spin.blockSignals(True)
                    spin.setValue(int(settings[key]))
                    spin.blockSignals(False)
            self._alert_status.setText("These are the values in force now.")

        w.result.connect(fill)
        w.error.connect(lambda e: self._alert_status.setText(f"Could not load: {e}"))
        _track_worker(self._workers, w)
        w.start()

    def _save_alert_settings(self):
        body = {
            "alerts_enabled": self._alert_on.isChecked(),
            "alert_silent_hours": self._alert_silent.value(),
            "alert_late_login_minutes": self._alert_late.value(),
            "alert_idle_minutes": self._alert_idle.value(),
        }
        w = _PostWorker(f"{API_BASE_URL}/admin/alerts/settings", body)
        w.result.connect(lambda _d: self._alert_status.setText(
            "Saved. The Alerts page uses these from its next check."))
        w.error.connect(lambda e: self._alert_status.setText(f"Not saved — {e}"))
        _track_worker(self._workers, w)
        w.start()

    # ── Data retention ───────────────────────────────────────────────────
    #
    # The only setting on this page whose effect is deleting things, which
    # is why it says how much it would delete before you touch it. Super
    # admin only — the server enforces that too.
    def _build_retention_section(self) -> QFrame:
        self._ret_logs   = QSpinBox(); self._ret_logs.setRange(7, 3650)
        self._ret_shots  = QSpinBox(); self._ret_shots.setRange(7, 3650)
        self._ret_att    = QSpinBox(); self._ret_att.setRange(90, 3650)
        self._ret_audit  = QSpinBox(); self._ret_audit.setRange(180, 3650)
        for spin in (self._ret_logs, self._ret_shots, self._ret_att, self._ret_audit):
            spin.setFixedHeight(36)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._ret_status = QLabel("Loading…")
        self._ret_status.setWordWrap(True)
        self._ret_status.setStyleSheet(
            f"color:{C['text_muted']}; font-size:12px; background:transparent;"
        )

        save = _btn("Save retention", variant="primary", height=36)
        save.clicked.connect(self._save_retention)
        row = QWidget()
        row.setObjectName("retRow")
        row.setStyleSheet("QWidget#retRow { background: transparent; }")
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.addStretch()
        row_lay.addWidget(save)
        self._ret_save_btn = save

        card = self._build_section(
            "database", "Data retention  ·  applies to everyone",
            "Anything older than these is deleted nightly. Nothing was being "
            "deleted before this existed, so the numbers below decide whether "
            "the disk keeps growing.",
            [
                self._setting_row("Keep activity logs",
                                  "Audit trail. Shorter means less to search "
                                  "through, and less to keep safe.",
                                  self._ret_logs, "days"),
                self._setting_row("Keep screenshots",
                                  "Both the database rows and the encrypted "
                                  "files on disk. This is what fills the disk.",
                                  self._ret_shots, "days"),
                self._setting_row("Keep attendance",
                                  "Payroll reads this — keep it well past any "
                                  "period you might be asked about.",
                                  self._ret_att, "days"),
                self._setting_row("Keep admin actions",
                                  "Password resets, screenshot deletions, role "
                                  "and retention changes — kept apart from the "
                                  "logs above, because \"who did that\" is asked "
                                  "months later or not at all.",
                                  self._ret_audit, "days"),
                row,
                self._ret_status,
            ],
        )
        return card

    def _refresh_upcoming(self):
        """Show the next fortnight for the selected employee.

        Asked of the SERVER rather than worked out here, so what is shown is
        what the scheduler will actually do — a preview computed from the
        form would agree with the form even when the save never landed, which
        is the failure it exists to catch.
        """
        emp_id = self._emp_combo.currentData() or "global"
        w = _FetchWorker(f"{API_BASE_URL}/admin/upcoming/{emp_id}", {"days": 14})

        def show(data: dict):
            days = data.get("days") or []
            if not days:
                self._weekly_preview.setText("")
                return
            parts = []
            for day in days:
                label = f"{day['weekday']} {day['date'][8:]}"
                parts.append(label if day.get("working") else f"[{label}]")
            offs = [d for d in days if not d.get("working")]
            summary = (f"{len(days) - len(offs)} working day(s), "
                       f"{len(offs)} off") if offs else f"all {len(days)} are working days"
            reasons = sorted({d["reason"] for d in offs if d.get("reason")})
            self._weekly_preview.setText(
                f"Next {len(days)} days — {summary}."
                + (f"  Off: {', '.join(reasons)}." if reasons else "")
                + "\n" + "  ".join(parts)
                + "\n[square brackets] = no screenshots that day."
            )

        w.result.connect(show)
        w.error.connect(lambda e: self._weekly_preview.setText(
            f"Could not check the coming days: {e}"))
        _track_worker(self._workers, w)
        w.start()

    def _refresh_retention(self):
        w = _FetchWorker(f"{API_BASE_URL}/admin/retention")

        def fill(data: dict):
            settings = data.get("settings") or {}
            for key, spin in (("log_retention_days", self._ret_logs),
                              ("screenshot_retention_days", self._ret_shots),
                              ("attendance_retention_days", self._ret_att),
                              ("audit_log_retention_days", self._ret_audit)):
                if settings.get(key):
                    spin.blockSignals(True)
                    spin.setValue(int(settings[key]))
                    spin.blockSignals(False)

            would = data.get("would_delete") or {}
            total = sum(int(v or 0) for v in would.values())
            if total:
                self._ret_status.setText(
                    f"Tonight's purge would remove {would.get('activity_logs', 0)} log(s), "
                    f"{would.get('screenshots', 0)} screenshot(s) and "
                    f"{would.get('attendance', 0)} attendance record(s) — "
                    f"they are already past these periods."
                )
            else:
                self._ret_status.setText(
                    "Nothing is currently past these periods."
                )

        def failed(error: str):
            # Super admin only. An admin seeing this has not hit a fault.
            self._ret_status.setText(
                "Only a super admin can view or change data retention."
                if "403" in str(error) else f"Could not load retention: {error}"
            )
            for spin in (self._ret_logs, self._ret_shots, self._ret_att, self._ret_audit):
                spin.setEnabled(False)
            self._ret_save_btn.setEnabled(False)

        w.result.connect(fill)
        w.error.connect(failed)
        _track_worker(self._workers, w)
        w.start()

    def _save_retention(self):
        body = {
            "log_retention_days":        self._ret_logs.value(),
            "screenshot_retention_days": self._ret_shots.value(),
            "attendance_retention_days": self._ret_att.value(),
            "audit_log_retention_days":   self._ret_audit.value(),
        }
        self._ret_save_btn.setEnabled(False)
        self._ret_status.setText("Saving…")

        w = _PostWorker(f"{API_BASE_URL}/admin/retention", body)

        def done(result: dict):
            self._ret_save_btn.setEnabled(True)
            if result.get("success"):
                # Re-read so the "would remove" line reflects the new numbers
                # rather than the ones it was showing a moment ago.
                self._refresh_retention()
            else:
                self._ret_status.setText(result.get("message", "Could not save."))

        w.result.connect(done)
        w.error.connect(lambda e: (
            self._ret_save_btn.setEnabled(True),
            self._ret_status.setText(f"Could not reach the server: {e}"),
        ))
        _track_worker(self._workers, w)
        w.start()

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
            f"color:{C['text_muted']}; font-size:12px; background:transparent;"
        )

        card = self._build_section(
            "calendar-off", "Holidays  ·  applies to everyone",
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

        refresh_btn = _btn("Refresh", variant="secondary", height=38, width=110)
        # A DRAWN ICON, NOT A CHARACTER. "↻" is a glyph out of the text
        # font: it sits on the baseline rather than centred on the label,
        # its weight is whatever the font decided, and it is missing on
        # machines whose font lacks it. Every other control here is Lucide.
        refresh_btn.setIcon(_icons.icon("refresh-cw", 15, C["text_primary"]))
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
            f"border:1px solid {C['border']}; border-radius:12px;"
            f"padding:11px 16px; font-size:12px;"
        )
        # HIDDEN UNTIL IT HAS SOMETHING TO SAY. An empty QLabel still takes
        # its padding and still paints its background, so before a scope was
        # chosen the page showed a blank blue bar under the toolbar — a
        # rectangle of colour meaning nothing. _update_scope_banner puts the
        # text in and shows it.
        self._scope_banner.hide()
        body.addWidget(self._scope_banner)

        # ── Widgets (naam wahi — save/load logic inhi pe depend karta hai) ──
        self._min_spin = QSpinBox(); self._min_spin.setRange(1, 60)
        self._max_spin = QSpinBox(); self._max_spin.setRange(1, 120)
        self._cnt_spin = QSpinBox(); self._cnt_spin.setRange(1, 20)   # daily budget
        # Kept only so the load/save round trip stays intact — the control
        # itself is gone from the page.
        #
        # It was labelled "how often captured data is synced" and did nothing:
        # screenshots upload the moment they are captured, and the 60 second
        # loop it appeared to control is the RETRY for uploads that failed.
        # Wiring it up would have made things worse, not better — the stored
        # default is 60, read as minutes, so a failed upload would have sat
        # for an hour instead of retrying within the minute.
        #
        # Rather than offer a knob that must not be turned, the page no longer
        # shows one. The column stays so no data is thrown away.
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

        # What the setting actually does, on real dates.
        #
        # Weekly off is the one control here whose effect cannot be seen when
        # you set it: tick Sunday and nothing changes until Sunday. If it
        # failed to save there was no way to tell — which is exactly how it
        # was reported ("set karta hoon to verify hi nahi kar paaya").
        self._weekly_preview = QLabel("")
        self._weekly_preview.setWordWrap(True)
        self._weekly_preview.setObjectName("weeklyPreview")
        self._weekly_preview.setStyleSheet(
            f"#weeklyPreview {{ color:{C['text_muted']}; font-size:12px;"
            f" background:{C['bg_elevated']}; border:1px solid {C['border']};"
            f" border-radius:12px; padding:9px 12px; }}"
        )

        # ── Sections ──────────────────────────────────────────────────────
        body.addWidget(self._build_section(
            "camera", "Screenshot Capture",
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
            "activity", "Activity Tracking",
            "When an employee counts as idle.",
            [
                self._setting_row("Idle threshold",
                                  "Marked idle after this long with no input.",
                                  self._idle_spin, "seconds"),
            ]))

        body.addWidget(self._build_section(
            "clock", "Shift Schedule",
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
                self._weekly_preview,
                self._setting_row("Weekly off",
                                  "No screenshots on these days. An overnight shift "
                                  "belongs to the day it starts — a Saturday 22:00 shift "
                                  "running into Sunday is still captured.",
                                  self._weekly_offs_row),
            ]))

        body.addWidget(self._build_alerts_section())
        body.addWidget(self._email_section())
        body.addWidget(self._build_retention_section())

        body.addWidget(self._build_holidays_section())

        body.addWidget(self._build_section(
            "settings", "Advanced",
            "Only change these if you need to.",
            [
                self._setting_row("Verbose logging",
                                  "Logs every sync and schedule event. Turn on only for "
                                  "debugging — it fills the database quickly.",
                                  self._verbose_check),
            ]))

        # ── Save ──────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        self._save_btn = _btn("Save Config", variant="primary", height=42, width=170)
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
        self._emp_combo.addItem("Global Default", "global")
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
        self._refresh_upcoming()

    def _update_scope_banner(self, inherited: bool = False):
        """Saaf batata hai ki abhi jo values dikh rahi hain wo kiske liye hain.

        Ye isliye zaroori hai: global aur per-employee form bilkul ek jaise
        dikhte the. Admin ek employee select karke value badalta, aur use
        yakeen nahi hota tha ki ye sirf usi employee pe lagi ya sabpe.
        """
        # Whatever branch below runs, it ends with text — so this is the one
        # place that needs to make the banner visible again.
        self._scope_banner.show()
        emp_id = self._emp_combo.currentData()
        if not emp_id or emp_id == "global":
            self._scope_banner.setStyleSheet(
                f"background:{C['warning_soft']}; color:{C['text_secondary']};"
                f"border:1px solid {C['border']}; border-radius:12px;"
                f"padding:11px 16px; font-size:12px;"
            )
            self._scope_banner.setText(
"<b>Global Default</b> — applies to every employee who has "
                "no override of their own. Employees with an override keep "
                "their own values."
            )
            return

        label = self._emp_combo.currentText()
        self._scope_banner.setStyleSheet(
            f"background:{C['accent_soft']}; color:{C['text_secondary']};"
            f"border:1px solid {C['border']}; border-radius:12px;"
            f"padding:11px 16px; font-size:12px;"
        )
        if inherited:
            self._scope_banner.setText(
                f"<b>{label}</b> — no override of their own yet, so these "
                f"values come from the <b>Global Default</b>. Saving creates "
                f"an override for this employee only; nobody else is "
                f"affected."
            )
        else:
            self._scope_banner.setText(
                f"<b>{label}</b> — these are this employee's own settings. "
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
                        f"{label} time must be HH:MM (00:00–23:59) — got \"{value}\""
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
        # An overnight shift gets the daily allowance on BOTH sides of
        # midnight, because the budget is per IST calendar day. That is the
        # design, not a fault — but an admin setting 22:00-06:00 with 10 per
        # day reasonably expects 10 for that shift, not 20, and nothing on
        # this page said otherwise until now.
        # Held rather than shown now: the save finishes a moment later and
        # its "saved" line would replace this before anyone read it.
        self._pending_note = ""
        if shift_start and shift_end and shift_end <= shift_start:
            count = self._cnt_spin.value()
            self._pending_note = (
                f"  ·  ℹ {shift_start}–{shift_end} crosses midnight, so it spans "
                f"two calendar days — up to {count} captures before midnight and "
                f"{count} after, {count * 2} across the shift."
            )

        offs = sorted(iso for iso, box in self._weekly_offs.items() if box.isChecked())
        if len(offs) == 7:
            self._status_label.setText(
" Every day cannot be a weekly off — leave at least one working day."
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
            self._status_label.setText(f"Error: {e}"),
            self._save_btn.setEnabled(True),
            self._save_btn.setText("Save Config"),
        ))
        _track_worker(self._workers, w)
        w.start()

    def _on_save_done(self, data: dict):
        self._save_btn.setEnabled(True)
        self._save_btn.setText("Save Config")
        if data.get("success"):
            self._status_label.setStyleSheet(f"color: {C['success']}; font-size:12px; background:transparent;")
            saved = self._describe_saved()
            self._status_label.setText(
                f"Saved{saved}{getattr(self, '_pending_note', '')}")
            # Read it back from the server rather than trusting the form.
            # "Saved" on its own does not prove the value survived the round
            # trip, and a weekly off that silently failed to stick is exactly
            # the kind of thing nobody notices until a Sunday.
            #
            # _on_employee_changed is the config fetch — it re-reads whichever
            # employee is selected and repopulates every field from the
            # server's answer.
            self._reload_after_save()
        else:
            # BUG this fixes: this read `error`, but every endpoint returns
            # `message`. Every rejection therefore showed "Save failed" with
            # no reason — a refused weekly off, an out-of-range value, a
            # permission problem, all identical and all unexplained. The
            # admin's only conclusion is that the page is broken.
            self._status_label.setStyleSheet(f"color: {C['danger']}; font-size:12px; background:transparent;")
            self._status_label.setText(
                f"{data.get('message') or data.get('error') or 'Save failed'}")

    def _reload_after_save(self):
        """Re-read the config from the server, keeping the status line.

        _on_employee_changed() also refreshes the scope banner and would
        overwrite the "Saved …" message with a load error on a slow link, so
        the fetch is done directly and only the form is repopulated.
        """
        emp_id = self._emp_combo.currentData() or "global"
        w = _FetchWorker(f"{API_BASE_URL}/admin/config/{emp_id}")
        w.result.connect(self._populate_form)
        # Silent on failure: the save already succeeded, and replacing a
        # confirmation with a fetch error would read as the save having
        # failed when it did not.
        w.error.connect(lambda _e: None)
        _track_worker(self._workers, w)
        w.start()

    def _describe_saved(self) -> str:
        """Name what was saved, so the confirmation is checkable.

        "Config saved successfully" tells an admin nothing they can verify.
        Naming the weekly off in particular matters: it is the one setting
        where "did that take?" has no visible answer until a weekend.
        """
        names = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu",
                 5: "Fri", 6: "Sat", 7: "Sun"}
        offs = [names[i] for i, box in sorted(self._weekly_offs.items())
                if box.isChecked()]
        who = self._emp_combo.currentText() or "Global Default"
        part = f" for {who}"
        if offs:
            part += f"  ·  weekly off: {', '.join(offs)}"
        else:
            part += "  ·  no weekly off"
        return part

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
        w.result.connect(lambda _d: self._load_employees())
        w.result.connect(lambda d: self._status_label.setText(
" Force logout set!" if d.get("success") else f"{d.get('error')}"
        ))
        w.error.connect(lambda e: self._status_label.setText(f"{e}"))
        _track_worker(self._workers, w)
        w.start()


# ──────────────────────────────────────────────────────────────────────────────
#  Screenshots Tab
# ──────────────────────────────────────────────────────────────────────────────

# ── screenshot thumbnails ────────────────────────────────────────────────
#
# The size of the picture in the list, and the picture itself once fetched.
# One cache for the tab: paging back and forth is the common way to use this
# list, and without it every step back re-downloads and re-decrypts twenty
# images that have not changed.
THUMB_W, THUMB_H = 104, 58
_THUMB_CACHE: dict[str, QPixmap] = {}
_THUMB_FAILED: set[str] = set()


class _ThumbCell(QLabel):
    """The picture in a row — a placeholder until its bytes arrive."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(THUMB_W, THUMB_H)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setScaledContents(False)
        self.setStyleSheet(
            f"QLabel {{ background:{C['bg_surface_alt']};"
            f"border:1px solid {C['border']};"
            f"border-radius:{_theme.Radius.CHIP}px;"
            f"color:{C['text_muted']};font-size:{_theme.Type.MICRO}px; }}")
        self.setText("…")

    def show_image(self, pixmap: QPixmap) -> None:
        self.setText("")
        self.setPixmap(pixmap.scaled(
            THUMB_W - 2, THUMB_H - 2,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    def show_nothing(self, why: str = "no preview") -> None:
        self.setPixmap(QPixmap())
        self.setText(why)


class _ScreenshotsTab(QWidget):

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._page = 1
        self._user_searched = False
        # Thumbnails still to fetch, and the one being fetched. See
        # _next_thumb for why there is only ever one.
        self._thumb_queue: list = []
        self._thumb_worker = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        toolbar = _card()
        filter_row = QHBoxLayout(toolbar)
        filter_row.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        filter_row.setSpacing(Space.SM)

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

        search_btn = _btn("Search", variant="primary", height=40, width=110)
        search_btn.clicked.connect(self._on_search_clicked)
        filter_row.addWidget(search_btn)
        clear_btn = _btn("Clear", variant="secondary", height=40, width=80)
        clear_btn.clicked.connect(self._on_clear_clicked)
        filter_row.addWidget(clear_btn)
        # A tick box per row and one that takes the page. Dragging a
        # selection is fine for two rows and hopeless for twenty — and a
        # delete you can only aim by dragging is one you will eventually aim
        # at the wrong row.
        self._select_all = QCheckBox("Select all")
        self._select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._select_all.stateChanged.connect(self._toggle_select_all)
        filter_row.addWidget(self._select_all)

        self._delete_btn = _btn("Delete selected", variant="danger", height=36)
        self._delete_btn.clicked.connect(self._delete_selected)
        self._delete_btn.setEnabled(False)
        filter_row.addWidget(self._delete_btn)

        filter_row.addStretch()
        root.addWidget(toolbar)

        # SEVEN COLUMNS, TWO OF THEM NEW: a thumbnail and a view button.
        #
        # The list said what had been captured and showed none of it, so
        # finding one screenshot meant opening rows until the right one came
        # up. The thumbnail is the whole point of a screenshot list.
        #
        # The new columns are APPENDED. Everything that reads this table by
        # index — the preview opener, the selection, the delete — counts from
        # the left, and putting a column in the middle is how the tick box
        # once shifted them all and opened the preview for an empty id.
        self._table = _tune_table(QTableWidget(0, 7))
        self._table.setHorizontalHeaderLabels(
            ["", "ID", "Employee", "File", "Captured", "Preview", ""])
        # Dragging still works for a quick pair, but the tick boxes are what
        # make a careful selection possible.
        self._table.setSelectionMode(
            QTableWidget.SelectionMode.ExtendedSelection)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 38)
        self._table.setColumnWidth(1, 70)
        self._table.setColumnWidth(2, 110)
        self._table.setColumnWidth(4, 210)
        self._table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(5, THUMB_W + 24)
        self._table.setColumnWidth(6, 64)
        # Tall enough for the thumbnail, which is what now sets the rhythm of
        # this table rather than the text in it.
        self._table.verticalHeader().setDefaultSectionSize(THUMB_H + 20)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.cellDoubleClicked.connect(self._open_preview)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.itemChanged.connect(lambda _i: self._on_selection_changed())
        root.addWidget(self._table, 1)

        pag_row = QHBoxLayout()
        self._prev_btn  = _btn("Prev", variant="secondary", height=36, width=96)
        self._prev_btn.setIcon(_icons.icon("chevron-left", 14, C["text_primary"]))
        self._prev_btn.clicked.connect(self._prev_page)
        self._next_btn  = _btn("Next", variant="secondary", height=36, width=96)
        self._next_btn.setIcon(_icons.icon("chevron-right", 14, C["text_primary"]))
        self._next_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
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

            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            tick.setCheckState(Qt.CheckState.Unchecked)
            self._table.setItem(i, 0, tick)

            self._table.setItem(i, 1, _cell(str(row.get("id", "")), mono=True, muted=True))
            self._table.setItem(i, 2, _cell(row.get("employee_id", ""), mono=True))
            # Poora .enc naam tooltip me — column me chhota handle.
            item = _cell(_short_filename(fname), mono=True, tooltip=fname)
            item.setData(Qt.ItemDataRole.UserRole, fname)
            self._table.setItem(i, 3, item)
            # BUG FIX: pehle yahan `ts = ...` assignment `if dt.tzinfo is
            # None:` block ke ANDAR thi — tz-aware timestamp par timestamp
            # kabhi format hi nahi hota tha. Ab shared helper.
            self._table.setItem(i, 4, _cell(_fmt_ts(row.get("created_at")), muted=True))

            # THE PICTURE, and a button that opens it full size. The button
            # calls the same opener a double-click has always used, so there
            # is one way in and nothing new to keep working.
            shot_id = str(row.get("id", ""))
            thumb = _ThumbCell()
            self._table.setCellWidget(i, 5, _centred(thumb))
            if shot_id in _THUMB_CACHE:
                thumb.show_image(_THUMB_CACHE[shot_id])
            elif shot_id in _THUMB_FAILED:
                thumb.show_nothing()
            else:
                self._thumb_queue.append((shot_id, thumb))

            eye = QPushButton()
            eye.setIcon(_icons.icon("eye", 16, C["text_secondary"]))
            eye.setIconSize(QSize(16, 16))
            eye.setFixedSize(34, 34)
            eye.setCursor(Qt.CursorShape.PointingHandCursor)
            eye.setToolTip("Open this screenshot")
            eye.setStyleSheet(
                f"QPushButton {{ background:transparent;border:1px solid {C['border']};"
                f"border-radius:{_theme.Radius.CHIP}px; }}"
                f"QPushButton:hover {{ border-color:{C['accent']};"
                f"background:{C['hover']}; }}")
            eye.clicked.connect(
                lambda _checked=False, r=i: self._open_preview(r, 0))
            self._table.setCellWidget(i, 6, _centred(eye))

        self._page_label.setText(f"Page {self._page}  •  Total: {total}")
        self._prev_btn.setEnabled(self._page > 1)
        self._next_btn.setEnabled(self._page * 20 < total)
        # A new page means a new set of rows — carrying a "select all" across
        # it would silently arm a delete for rows nobody looked at.
        self._select_all.blockSignals(True)
        self._select_all.setChecked(False)
        self._select_all.blockSignals(False)
        self._on_selection_changed()
        # Sized to the content, after it exists — see _fit_columns. The file
        # name column takes whatever is left: without a stretch column the
        # table stopped halfway across its card and left a wide empty band on
        # the right, which is what the list looked like before.
        _fit_columns(self._table, stretch=3)
        self._next_thumb()

    # ── thumbnails ──────────────────────────────────────────────────────

    def _next_thumb(self):
        """Fetch the queued thumbnails, ONE AT A TIME.

        Twenty rows means twenty encrypted downloads. Firing them together
        would put twenty threads and twenty connections up at once for a
        picture the size of a postage stamp, and this panel has already been
        bitten by threads outliving the widget that started them. One in
        flight, the next started when it lands: the pictures fill in from the
        top, which is the order somebody reads them in anyway.

        `_thumb_page` is the generation. A reply for the page somebody has
        already left is dropped rather than painted into whatever row now
        sits at that index.
        """
        if self._thumb_worker is not None or not self._thumb_queue:
            return
        shot_id, cell = self._thumb_queue.pop(0)
        page = self._page

        worker = _ShotDownloadWorker(shot_id)
        self._thumb_worker = worker

        def done(blob):
            self._thumb_worker = None
            if page == self._page:
                self._paint_thumb(shot_id, cell, blob)
            self._next_thumb()

        def failed(_error):
            self._thumb_worker = None
            _THUMB_FAILED.add(shot_id)
            try:
                cell.show_nothing()
            except RuntimeError:
                pass                      # the row is gone; nothing to draw on
            self._next_thumb()

        worker.result.connect(done)
        worker.error.connect(failed)
        _track_worker(self._workers, worker)
        worker.start()

    def _paint_thumb(self, shot_id: str, cell, blob):
        """Decrypt, decode, and put it in the cell — or say there is none."""
        try:
            data = bytes(blob)
            # Screenshots are stored encrypted; the preview window does the
            # same two steps, and an unencrypted file must still open.
            if not self._is_image(data):
                try:
                    data = CryptoEngine.decrypt_bytes(data)
                except Exception:
                    pass
            picture = QPixmap()
            if not picture.loadFromData(data) or picture.isNull():
                _THUMB_FAILED.add(shot_id)
                cell.show_nothing()
                return
            _THUMB_CACHE[shot_id] = picture
            cell.show_image(picture)
        except RuntimeError:
            # The table was rebuilt under us — the cell is a dead C++ object.
            pass

    @staticmethod
    def _is_image(data: bytes) -> bool:
        return data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff"

    def _selected_ids(self) -> list[int]:
        """Rows that are ticked, or failing that, rows that are highlighted.

        Both work. The tick boxes survive scrolling and clicking elsewhere,
        which is what makes a twenty-row selection possible; a dragged
        highlight is quicker for two.
        """
        ids = []
        for row in range(self._table.rowCount()):
            tick = self._table.item(row, 0)
            id_item = self._table.item(row, 1)
            if not id_item or not id_item.text().isdigit():
                continue
            if tick and tick.checkState() == Qt.CheckState.Checked:
                ids.append(int(id_item.text()))
        if ids:
            return ids
        for index in self._table.selectionModel().selectedRows():
            id_item = self._table.item(index.row(), 1)
            if id_item and id_item.text().isdigit():
                ids.append(int(id_item.text()))
        return ids

    def _toggle_select_all(self, state):
        want = (Qt.CheckState.Checked if self._select_all.isChecked()
                else Qt.CheckState.Unchecked)
        self._table.blockSignals(True)
        for row in range(self._table.rowCount()):
            tick = self._table.item(row, 0)
            if tick:
                tick.setCheckState(want)
        self._table.blockSignals(False)
        self._on_selection_changed()

    def _on_selection_changed(self):
        count = len(self._selected_ids())
        self._delete_btn.setEnabled(count > 0)
        self._delete_btn.setText(
"Delete selected" if count == 0 else f"Delete {count}")

    def _delete_selected(self):
        ids = self._selected_ids()
        if not ids:
            return

        # Named plainly, with no default button, because this cannot be
        # undone — the file is removed from disk along with the row.
        box = QMessageBox(self)
        box.setWindowTitle("Delete screenshots")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"Delete {len(ids)} screenshot(s)?")
        box.setInformativeText(
            "The encrypted files are removed from the server as well as the "
            "records. This cannot be undone.\n\n"
            "The deletion is written to the audit log."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Cancel
                               | QMessageBox.StandardButton.Yes)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        self._delete_btn.setEnabled(False)
        self._delete_btn.setText("Deleting…")

        w = _PostWorker(f"{API_BASE_URL}/admin/screenshots/delete", {"ids": ids})

        def done(result: dict):
            if result.get("success"):
                self._load()
            else:
                QMessageBox.warning(self, "Could not delete",
                                    result.get("message", "The server refused."))
                self._on_selection_changed()

        w.result.connect(done)
        w.error.connect(lambda e: (
            QMessageBox.warning(self, "Could not delete",
                                f"Could not reach the server: {e}"),
            self._on_selection_changed(),
        ))
        _track_worker(self._workers, w)
        w.start()

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
        # Columns shifted by one when the tick box was added at 0. Reading
        # the wrong ones here would have opened a preview for a screenshot id
        # of "" and looked like the preview was broken.
        id_item = self._table.item(row, 1)
        emp_item = self._table.item(row, 2)
        file_item = self._table.item(row, 3)
        ts_item = self._table.item(row, 4)

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

    # Emitted with a page key. The dashboard does not know what index a page
    # has, and hard-coded indices here are exactly what made all five Quick
    # Actions open the wrong page.
    open_page = Signal(str)

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

        # THE UNSCOPED VERSION LIVED HERE. `QFrame{...}` reaches every frame
        # inside the card, so the dividers on this dashboard were drawing
        # themselves as bordered rounded boxes — the fault the panel's own
        # _card had already been fixed for, in one place only.
        def card_frame():
            return _card_frame(bg=C["bg_surface"], border=C["border"])

        # ── Today's Summary strip ────────────────────────────────────────
        summary = card_frame()
        sl = QVBoxLayout(summary)
        sl.setContentsMargins(18, 15, 18, 16)
        sl.setSpacing(13)
        head = QHBoxLayout()
        ico = QLabel()
        ico.setPixmap(_icons.pixmap("bar-chart-3", 17, C["accent"]))
        _clear_bg(ico, "border:none;")
        ttl = QLabel("Today's Summary")
        ttl.setStyleSheet(
            f"color:{C['text_primary']};font-size:16px;font-weight:700;"
            f"border:none;background:transparent;"
        )
        head.addWidget(ico); head.addWidget(ttl); head.addStretch()
        sl.addLayout(head)

        from client.presentation.widgets.panel_widgets import MiniStat, StatusTile, QuickAction
        from client.presentation.theme import C as TC

        strip = QHBoxLayout(); strip.setSpacing(12)
        self.m_employees = MiniStat("users", "Employees",   TC.BLUE,   TC.BLUE_BG)
        self.m_online    = MiniStat("circle-check", "Online Now",  TC.GREEN,  TC.GREEN_BG)
        self.m_shots     = MiniStat("image", "Screenshots", TC.PURPLE, TC.PURPLE_BG)
        self.m_logs      = MiniStat("clipboard-list", "Activity Logs", TC.CYAN, TC.CYAN_BG)
        self.m_coverage  = MiniStat("crosshair", "Coverage",    TC.AMBER,  TC.AMBER_BG)
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
        m_ico = QLabel(); m_ico.setFixedSize(30, 30)
        m_ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
        m_ico.setPixmap(_icons.pixmap("user", 16, C["accent"]))
        m_ico.setStyleSheet(f"background:{C['accent_soft']};"
                            f"border-radius:{_theme.Radius.CONTROL}px;")
        m_ttl = QLabel("Your Session")
        m_ttl.setStyleSheet(
            f"color:{C['text_primary']}; font-size:16px; font-weight:700; background:transparent;"
        )
        m_sub = QLabel("Your own tracking status — admins are tracked too")
        m_sub.setStyleSheet(f"color:{C['text_muted']}; font-size:12px; background:transparent;")
        m_head.addWidget(m_ico); m_head.addWidget(m_ttl)
        m_head.addSpacing(8); m_head.addWidget(m_sub); m_head.addStretch()
        m_lay.addLayout(m_head)

        my_row = QHBoxLayout(); my_row.setSpacing(12)
        self.m_session  = MiniStat("timer", "Session Duration", TC.AMBER,  TC.AMBER_BG)
        self.m_activity = MiniStat("monitor", "Activity Status",  TC.GREEN,  TC.GREEN_BG)
        self.m_myshots  = MiniStat("camera", "Screenshots Today", TC.PURPLE, TC.PURPLE_BG)
        for c in (self.m_session, self.m_activity, self.m_myshots):
            my_row.addWidget(c)
        m_lay.addLayout(my_row)
        root.addWidget(mine)

        # ── Status tiles ─────────────────────────────────────────────────
        tiles = QHBoxLayout(); tiles.setSpacing(14)
        self.t_server   = StatusTile("server", "Server Status",   TC.GREEN, TC.GREEN_BG)
        self.t_database = StatusTile("database", "Database",        TC.GREEN, TC.CYAN_BG)
        self.t_tracking = StatusTile("crosshair", "Tracking",        TC.GREEN, TC.BLUE_BG)
        self.t_sync     = StatusTile("cloud", "Sync Health",     TC.GREEN, TC.PURPLE_BG)
        for tl in (self.t_server, self.t_database, self.t_tracking, self.t_sync):
            tiles.addWidget(tl)
        root.addLayout(tiles)

        # ── Today, for everybody ─────────────────────────────────────────
        #
        # ABOVE the older cards on purpose. Those answer "how many people and
        # how much data"; these answer "who is at work today", which is the
        # question somebody opens this page to ask. Online/Offline is a fact
        # about network sessions and was standing in for attendance because
        # nothing else was available.
        today_header = QLabel("Today")
        today_header.setStyleSheet(
            f"color:{C['text_primary']};font-weight:700;"
            f"font-size:{_theme.Type.SECTION}px;background:transparent;")
        root.addWidget(today_header)

        today_grid = QGridLayout()
        today_grid.setSpacing(14)
        # Present + Leave + Absent + Day Off adds up to the headcount, which
        # is the property that makes a wrong card findable.
        # The same colours the attendance page uses for the same words, so
        # the two screens cannot teach different meanings for one green.
        self._card_present  = StatCard("Present",     ACCENTS["green"],  "circle-check", sparkline=False)
        self._card_active   = StatCard("Active Now",  ACCENTS["green"],  "activity", sparkline=False)
        self._card_leave    = StatCard("On Leave",    ACCENTS["violet"], "palmtree", sparkline=False)
        self._card_absent   = StatCard("Absent",      ACCENTS["red"],    "circle-slash", sparkline=False)
        self._card_late     = StatCard("Late",        ACCENTS["amber"],  "clock", sparkline=False)
        self._card_day_off  = StatCard("Day Off",     ACCENTS["slate"],  "calendar-off", sparkline=False)
        for i, c in enumerate([
            self._card_present, self._card_active, self._card_leave,
            self._card_absent, self._card_late, self._card_day_off,
        ]):
            today_grid.addWidget(c, 0, i)
            today_grid.setColumnStretch(i, 1)
        root.addLayout(today_grid)

        # ── The company, and what has been collected ─────────────────────
        #
        # THESE ARE NOT THE SAME QUESTION AS THE ROW ABOVE, and two green
        # cards on one screen made it look as though they were. "Active Now"
        # counts open shifts; "Online Now" counts people signed in on a
        # device. They differ legitimately — an administrator at their desk
        # with no shift open is online and not active — and with no subtitles
        # the only conclusion available was that one of them was wrong.
        section = QLabel("Coverage & collection")
        section.setStyleSheet(
            f"color:{C['text_primary']};font-weight:700;"
            f"font-size:{_theme.Type.SECTION}px;background:transparent;")
        root.addWidget(section)

        grid = QGridLayout()
        grid.setSpacing(14)
        self._card_total_employees = StatCard("Total Employees",      ACCENTS["blue"],   "users", sparkline=False)
        self._card_online          = StatCard("Online Now",           ACCENTS["green"],  "activity", sparkline=False)
        self._card_offline         = StatCard("Offline",              ACCENTS["slate"],  "moon", sparkline=False)
        self._card_total_screens   = StatCard("Screenshots Captured", ACCENTS["violet"], "camera", sparkline=False)
        self._card_total_logs      = StatCard("Activity Logs",        ACCENTS["cyan"],   "clipboard-list", sparkline=False)
        self._card_online.set_subtitle("signed in on a device")
        self._card_offline.set_subtitle("no live session")
        self._card_total_employees.set_subtitle("on the books")
        self._card_total_screens.set_subtitle("all time")
        self._card_total_logs.set_subtitle("all time")
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
            f"color:{C['text_primary']}; font-weight:700; font-size:16px; background:transparent;"
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

        # ── What needs attention ─────────────────────────────────────────
        #
        # The alerts existed only on their own page, which meant they were
        # seen by somebody who had already decided to go looking. The three
        # most severe are here, where the day starts.
        #
        # THREE, AND A COUNT. A dashboard that reprints the whole alerts page
        # is the alerts page, and the reason to open the real one disappears.
        alerts_card = card_frame()
        ac = QVBoxLayout(alerts_card)
        ac.setContentsMargins(18, 15, 18, 16)
        ac.setSpacing(10)
        ah = QHBoxLayout()
        at = QLabel("Needs attention")
        at.setStyleSheet(
            f"color:{C['text_primary']};font-size:{_theme.Type.SECTION}px;"
            f"font-weight:700;border:none;background:transparent;")
        self._alerts_count = QLabel("")
        self._alerts_count.setStyleSheet(
            f"color:{C['text_muted']};font-size:{_theme.Type.SMALL}px;"
            f"border:none;background:transparent;")
        ah.addWidget(at); ah.addWidget(self._alerts_count); ah.addStretch()
        open_alerts = _btn("Open Alerts", variant="secondary", height=36, width=110)
        open_alerts.clicked.connect(lambda: self.open_page.emit("alerts"))
        ah.addWidget(open_alerts)
        ac.addLayout(ah)
        self._alerts_body = QVBoxLayout()
        self._alerts_body.setSpacing(6)
        ac.addLayout(self._alerts_body)
        root.addWidget(alerts_card)

        # ── Recent Activity ──────────────────────────────────────────────
        feed_card = card_frame()
        fc = QVBoxLayout(feed_card)
        fc.setContentsMargins(0, 0, 0, 0)
        fc.setSpacing(0)
        fh = QHBoxLayout(); fh.setContentsMargins(20, 15, 16, 10)
        fl = QLabel("Recent Activity")
        fl.setStyleSheet(
            f"color:{C['text_primary']};font-weight:700;font-size:16px;"
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
            f"color:{C['text_primary']};font-size:16px;font-weight:700;"
            f"border:none;background:transparent;"
        )
        qc.addWidget(qt)
        qrow = QHBoxLayout(); qrow.setSpacing(12)
        # BY KEY, NOT BY NUMBER.
        #
        # These were hard-coded indices into PAGES — 2 for Employees, 1 for
        # Configuration, and so on. A page was inserted into PAGES at some
        # point and the numbers stayed where they were, so ALL FIVE buttons
        # opened the page before the one they named: "Employees" opened
        # Configuration, "Configuration" opened Alerts, "Audit Logs" opened
        # Screenshots. Nothing raised, and the button that took you somewhere
        # else still took you somewhere plausible, which is why it survived.
        self._quick_buttons = {}
        for icon, label, key in (
            ("users", "Employees", "employees"),
            ("settings", "Configuration", "config"),
            ("calendar-days", "Attendance", "attendance"),
            ("camera", "Screenshots", "screenshots"),
            ("clipboard-list", "Audit Logs", "logs"),
        ):
            btn = QuickAction(icon, label)
            self._quick_buttons[label] = (btn, key)
            qrow.addWidget(btn)
        qc.addLayout(qrow)
        root.addWidget(qa)
        root.addStretch()

    def _load_alerts(self):
        """The three most severe, and how many there are altogether."""
        worker = _FetchWorker(f"{API_BASE_URL}/admin/alerts", {})

        def fill(data):
            payload = data or {}
            alerts = payload.get("alerts") or []
            while self._alerts_body.count():
                item = self._alerts_body.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            if not payload.get("enabled", True):
                self._alerts_count.setText("")
                self._alerts_body.addWidget(_muted_label(
                    "Alerts are switched off in Configuration."))
                return
            if not alerts:
                self._alerts_count.setText("")
                # NOT AN EMPTY BOX. "Nothing" and "failed to load" look
                # identical when a panel is simply blank.
                self._alerts_body.addWidget(_muted_label(
                    "Nothing needs attention right now."))
                return

            self._alerts_count.setText(f"·  {len(alerts)} in total")
            # Severity first, so the card cannot fill with three low ones
            # while something serious sits below the cut.
            # LOWERCASED FIRST. The server sends "HIGH", not "high" —
            # measured against the running API, not assumed. Without this
            # every alert fell through to the default: sorted last and drawn
            # as a blue "info" chip, including the critical ones.
            order = {"critical": 0, "high": 1, "warning": 2, "info": 3}
            worst = sorted(alerts, key=lambda a: order.get(
                str(a.get("severity") or "").lower(), 9))[:3]
            for alert in worst:
                row = QHBoxLayout()
                row.setSpacing(_theme.Space.SM)
                severity = str(alert.get("severity") or "info").lower()
                chip = badge_label(
                    {"critical": "rejected", "high": "rejected",
                     "warning": "pending"}.get(severity, "info"),
                    severity.title())
                text = QLabel(str(alert.get("title") or alert.get("message") or ""))
                text.setStyleSheet(
                    f"color:{C['text_secondary']};font-size:{_theme.Type.SMALL}px;"
                    f"border:none;background:transparent;")
                text.setWordWrap(True)
                row.addWidget(chip)
                row.addWidget(text, 1)
                holder = QWidget()
                holder.setLayout(row)
                self._alerts_body.addWidget(holder)

        worker.result.connect(fill)
        worker.error.connect(lambda _e: None)
        _track_worker(self._workers, worker)
        worker.start()

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
        self._load_today()
        self._load_alerts()
        self._load_feed()
        self._load_own_shots()

    def _load_today(self):
        """Today's counts, from the one endpoint that works them all out.

        NOT SIX SEPARATE REQUESTS. The whole value of these cards is that
        they add up to the headcount, and figures fetched separately can be
        taken a second apart — long enough for somebody to sign in between
        two of them and for the row of cards to stop adding up in front of
        the person reading it.
        """
        worker = _FetchWorker(f"{API_BASE_URL}/dashboard/today", {})

        def fill(data):
            board = (data or {}).get("data") or {}
            if not board:
                return
            self._card_present.set_value(str(board.get("present", 0)))
            self._card_present.set_subtitle(
                f"of {board.get('headcount', 0)} people")
            self._card_active.set_value(str(board.get("active", 0)))
            self._card_leave.set_value(str(board.get("on_leave", 0)))
            self._card_absent.set_value(str(board.get("absent", 0)))
            self._card_late.set_value(str(board.get("late", 0)))
            self._card_late.set_subtitle("of those present")
            self._card_day_off.set_value(str(board.get("day_off", 0)))
            self._card_day_off.set_subtitle("weekly off or holiday")
            # The one number that is a job rather than a fact: open shifts
            # with nobody in them, waiting to be closed.
            stale = board.get("not_signed_out", 0)
            self._card_active.set_subtitle(
                f"{stale} not signed out" if stale else "at work now")

        worker.result.connect(fill)
        worker.error.connect(lambda _e: None)
        _track_worker(self._workers, worker)
        worker.start()

    def _load_own_shots(self):
        """The admin's own "Screenshots Today", read from the local database.

        BUG this fixes: the card was driven ONLY by captures taken during the
        current session. It sat blank until the first one happened — so an
        admin signing in mid-morning saw nothing where the employee panel
        would have shown 8 — and after restarting the app it began again from
        1 while the day already held several.

        captures_today() is the same count the daily cap is enforced against,
        so the card now cannot disagree with the limit the scheduler applies.
        """
        try:
            from client.application.managers.screenshot_manager import ScreenshotManager
            self.m_myshots.set_value(str(ScreenshotManager.captures_today()))
            self.m_myshots.set_subtitle("Captured today")
        except Exception:
            # Never let a stat card take the dashboard down with it.
            pass

    def _load_summary(self):
        url = f"{API_BASE_URL}/dashboard/summary"
        w = _FetchWorker(url)

        w.result.connect(self._on_summary)
        w.error.connect(self._on_summary_failed)
        _track_worker(self._workers, w)
        w.start()

    def _load_feed(self):
        w = _FetchWorker(f"{API_BASE_URL}/dashboard/recent-activity", params={"limit": 50})

        w.result.connect(self._on_feed)
        w.error.connect(lambda e: print("Dashboard feed error:", e))
        _track_worker(self._workers, w)
        w.start()

    def _on_summary_failed(self, error: str):
        """Say the figures could not be fetched, rather than showing zeros.

        This is the failure that mattered. A dropped request used to leave
        the cards reading 0 employees, 0 screenshots, 0 activity logs — which
        is exactly what a wiped database looks like. It was read as one, and
        the data was fine the whole time.

        The counts are replaced with a dash and the subtitle says why. What
        must never happen again is a number on screen that was never measured.
        """
        for card in (self._card_total_employees, self._card_online,
                     self._card_offline, self._card_total_screens,
                     self._card_total_logs):
            try:
                card.set_value("—")
                card.set_subtitle("Could not reach the server")
            except Exception:
                pass
        print("[SUMMARY ERROR]", error)

    def _on_summary(self, data: dict):
        s = data.get("data", data)

        # A payload without the expected keys is a failure wearing a 200.
        # Filling in zeros for missing fields is what made a network blip
        # look like an empty database.
        expected = ("total_employees", "total_screenshots", "total_activity_logs")
        if not isinstance(s, dict) or not any(k in s for k in expected):
            self._on_summary_failed("unexpected response shape")
            return

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


class _PayrollCostChart(QFrame):
    """What payroll has cost, month by month, as a stacked bar.

    TWO SEGMENTS THAT ADD UP EXACTLY. The bar is the month's payroll COST,
    which is not the same as what was paid out: the deductions were part of
    the cost and went somewhere else — provident fund, a day of absence.

        net pay + deductions  ==  gross + overtime

    That identity is why the two segments can be drawn touching, with no
    rounding gap at the join. It holds because both figures are the frozen
    ones from payroll_lines, summed on the server, rather than two separate
    calculations that happen to be close.

    Everything drawn here comes from /admin/payroll. There is no sample
    series, no placeholder, and a month with no run is simply absent — an
    invented bar on a payroll chart is worse than an empty one.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("payrollChart")
        self.setMinimumHeight(230)
        self.setStyleSheet(
            f"QFrame#payrollChart{{background:{C['bg_surface']};"
            f"border:1px solid {C['border']};"
            f"border-radius:{Radius.CARD}px;}}")
        self._months: list[dict] = []
        self._hover = -1
        self.setMouseTracking(True)

    # THE INDIAN FINANCIAL YEAR, April to March. A payroll chart that ran
    # January to December would put a year-end bonus in the middle of one bar
    # and the tax year across two, which is not how anybody here reads it.
    RANGES = (("This year", "this"), ("Previous year", "previous"),
              ("Previous month", "month"))

    def set_months(self, months: list, span: str = "this"):
        """Every month of the span, whether or not payroll was run for it.

        THE EMPTY MONTHS ARE DRAWN TOO, as gaps on the axis. A chart that
        shows only the four months that have runs makes four months look like
        a full year — and the question this answers is "how has the year
        gone", which needs the months that have not happened yet to be
        visible as months that have not happened yet.
        """
        by_month = {str(m.get("month", "")): m for m in months}

        today = QDate.currentDate()
        # April is month 4; anything before it belongs to the year before.
        year = today.year() if today.month() >= 4 else today.year() - 1
        if span == "previous":
            year -= 1

        if span == "month":
            previous = today.addMonths(-1)
            keys = [previous.toString("yyyy-MM")]
        else:
            keys = []
            for offset in range(12):
                month = 4 + offset
                keys.append(f"{year + (month - 1) // 12}-{(month - 1) % 12 + 1:02d}")

        self._months = [by_month.get(key, {"month": key, "empty": True})
                        for key in keys]
        self._hover = -1
        self.update()

    # ── where the bars are, so drawing and hovering cannot disagree ─────
    def _geometry(self):
        left, right, top, bottom = 58, 18, 44, 62
        area = self.rect().adjusted(left, top, -right, -bottom)
        if not self._months or area.width() <= 0:
            return area, 0.0, []
        # `or 1.0` matters: a financial year that has not started yet is
        # twelve empty months, and dividing the plot height by a peak of zero
        # is a crash on a page somebody opened to see nothing in particular.
        peak = max((float(m.get("payroll_cost") or 0) for m in self._months),
                   default=0.0) or 1.0
        # A ceiling above the tallest bar, so the top one is not flush with
        # the frame and the gridline above it has somewhere to sit — AND a
        # ceiling that divides into four readable numbers.
        peak = self._nice_ceiling(peak)
        slot = area.width() / len(self._months)
        bar = min(38.0, slot * 0.5)
        bars = []
        for i in range(len(self._months)):
            centre = area.left() + slot * (i + 0.5)
            bars.append(QRectF(centre - bar / 2, area.top(), bar, area.height()))
        return area, peak, bars

    def mouseMoveEvent(self, event):
        _area, _peak, bars = self._geometry()
        x = event.position().x()
        found = -1
        for i, rect in enumerate(bars):
            # The whole column, not just the bar: a two-pixel-wide target is
            # a tooltip nobody ever sees.
            if abs(x - rect.center().x()) <= max(rect.width(), 26) / 2 + 8:
                found = i
                break
        if found != self._hover:
            self._hover = found
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(event)

    @staticmethod
    def _short(value: float) -> str:
        """1_50_000 -> "1.5L". Indian units, because the reader is reading
        rupees and "150K" is not how anybody here says it."""
        value = float(value or 0)
        if value >= 10_000_000:
            return f"{value / 10_000_000:.2f}Cr".replace(".00", "")
        if value >= 100_000:
            return f"{value / 100_000:.2f}L".replace(".00", "")
        if value >= 1_000:
            return f"{value / 1_000:.0f}K"
        return f"{value:.0f}"

    @staticmethod
    def _nice_ceiling(peak: float, ticks: int = 4) -> float:
        """A top of scale that divides into `ticks` numbers somebody can read.

        The ceiling used to be the tallest bar plus fifteen per cent, and the
        gridlines were quarters of it — so a year peaking at ₹6.70L was
        labelled 1.93L / 3.85L / 5.78L / 7.70L. Every one of those is a real
        number and none of them is a number anybody thinks in, which makes the
        axis useless for the one thing an axis is for: reading a bar's value
        off it without a tooltip.

        So the STEP is rounded up to something round — 1, 2, 2.5 or 5 times a
        power of ten — and the ceiling is four of those. The same year now
        reads 2L / 4L / 6L / 8L, and the tallest bar still has headroom
        because the step was rounded UP.
        """
        import math

        if peak <= 0:
            return 1.0
        rough = peak / ticks
        power = 10 ** math.floor(math.log10(rough))
        # 1.5 IS ON THE LIST, and it earns its place. Without it a peak of
        # ₹4.80L jumps to a ceiling of ₹8L and the tallest bar reaches only
        # three fifths of the plot — the chart looks like a quiet year when it
        # is a busy one. With it the ceiling is ₹6L and the bar reads at four
        # fifths. "1.5L" is still a number people say.
        for multiple in (1, 1.5, 2, 2.5, 5, 10):
            if rough <= multiple * power:
                return multiple * power * ticks
        return rough * ticks

    def _has_any_payroll(self) -> bool:
        """Whether any month in the span was actually run.

        NOT `self._months` being empty — it never is. set_months fills the
        whole financial year with placeholders so the months that have not
        happened yet are visible as months that have not happened yet, which
        means the empty-state test below was unreachable and a year with no
        runs drew an axis scaled to a peak of 1: gridlines labelled 1, 1, 1,
        0, 0. Twelve empty months under a nonsense scale.
        """
        return any(float(m.get("payroll_cost") or 0) > 0 for m in self._months)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        heading = QFont(); heading.setPointSize(10); heading.setBold(True)
        painter.setFont(heading)
        painter.setPen(QColor(C["text_muted"]))
        painter.drawText(QRectF(18, 12, self.width() - 36, 18),
                         Qt.AlignmentFlag.AlignLeft, "PAYROLL COST SUMMARY")

        if not self._has_any_payroll():
            small = QFont(); small.setPointSize(10)
            painter.setFont(small)
            painter.setPen(QColor(C["text_muted"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No payroll has been generated yet")
            painter.end()
            return

        area, peak, bars = self._geometry()
        label = QFont(); label.setPointSize(8)
        painter.setFont(label)

        # ── the scale ───────────────────────────────────────────────────
        for step in range(5):
            value = peak * step / 4
            y = area.bottom() - area.height() * step / 4
            painter.setPen(QPen(QColor(C["border_light"]), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            painter.setPen(QColor(C["text_muted"]))
            painter.drawText(QRectF(6, y - 8, 46, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             self._short(value))

        net_colour = QColor(C["success"])
        deduction_colour = QColor(C["accent"])

        for i, month in enumerate(self._months):
            rect = bars[i]
            net = float(month.get("net_before_adjustments") or 0)
            deductions = float(month.get("deductions") or 0)
            cost = float(month.get("payroll_cost") or 0) or (net + deductions)

            scale = area.height() / peak
            net_height = net * scale
            deduction_height = deductions * scale

            faded = self._hover not in (-1, i)
            painter.setPen(Qt.PenStyle.NoPen)

            colour = QColor(net_colour)
            if faded:
                colour.setAlpha(90)
            painter.setBrush(colour)
            painter.drawRoundedRect(
                QRectF(rect.left(), area.bottom() - net_height,
                       rect.width(), net_height), 3, 3)

            if deduction_height > 0.5:
                colour = QColor(deduction_colour)
                if faded:
                    colour.setAlpha(90)
                painter.setBrush(colour)
                painter.drawRoundedRect(
                    QRectF(rect.left(), area.bottom() - net_height - deduction_height,
                           rect.width(), deduction_height), 3, 3)

            painter.setPen(QColor(C["text_muted"]))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            # "Apr\n2026", two lines — twelve labels of "2026-04" side by
            # side overlap into an unreadable band.
            key = str(month.get("month", ""))
            try:
                year_part, month_part = key.split("-")
                caption = (f"{QDate(int(year_part), int(month_part), 1).toString('MMM')}"
                           f"\n{year_part}")
            except (ValueError, TypeError):
                caption = key
            painter.drawText(
                QRectF(rect.center().x() - 34, area.bottom() + 4, 68, 28),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                caption)
            # The figure above its own bar, so the chart can be read without
            # hovering — which is the only way it works in a screenshot.
            if cost > 0:
                painter.setPen(QColor(C["text_primary"]))
                painter.drawText(
                    QRectF(rect.center().x() - 40,
                           area.bottom() - net_height - deduction_height - 17, 80, 14),
                    Qt.AlignmentFlag.AlignCenter, self._short(cost))

        # ── legend, beside the heading ──────────────────────────────────
        # NOT ALONG THE BOTTOM. It was there, and it sat on top of the "Apr
        # 2026" month label — two different things printed in the same place.
        x = area.left() + 190
        for colour, text in ((net_colour, "Net pay"),
                             (deduction_colour, "Deductions")):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawEllipse(QRectF(x, 17, 8, 8))
            painter.setPen(QColor(C["text_muted"]))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            width = painter.fontMetrics().horizontalAdvance(text)
            painter.drawText(QRectF(x + 13, 13, width + 6, 16),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             text)
            x += width + 34

        # ── what the hovered month was made of ──────────────────────────
        if 0 <= self._hover < len(self._months):
            month = self._months[self._hover]
            key = str(month.get("month", ""))
            try:
                year_part, month_part = key.split("-")
                title = QDate(int(year_part), int(month_part), 1).toString("MMM yyyy")
            except (ValueError, TypeError):
                title = key

            if month.get("empty"):
                rows = [(None, "No payroll run for this month", "")]
            else:
                rows = [
                    (net_colour, "Net pay",
                     f"₹{float(month.get('net_before_adjustments') or 0):,.2f}"),
                    (deduction_colour, "Deductions",
                     f"₹{float(month.get('deductions') or 0):,.2f}"),
                    (QColor(C["warning"]), "Overtime",
                     f"₹{float(month.get('overtime') or 0):,.2f}"),
                    (None, "Payroll cost",
                     f"₹{float(month.get('payroll_cost') or 0):,.2f}"),
                    (None, f"{month.get('employees', 0)} employees",
                     str(month.get("status", ""))),
                ]

            metrics = painter.fontMetrics()
            label_width = max(metrics.horizontalAdvance(text) for _, text, _ in rows)
            value_width = max(metrics.horizontalAdvance(value) for _, _, value in rows)
            width = max(metrics.horizontalAdvance(title),
                        label_width + value_width + 30) + 34
            height = len(rows) * 17 + 40

            # BESIDE THE BAR IT DESCRIBES, NOT OVER IT. Centred on the bar,
            # the box covered the very column somebody was pointing at — and
            # on the leftmost month it covered the two beside it as well.
            rect = bars[self._hover]
            left = rect.right() + 14
            if left + width > self.width() - 8:
                left = rect.left() - width - 14
            left = max(8.0, min(left, self.width() - width - 8))
            box = QRectF(left, area.top() + 4, width, height)

            painter.setPen(QPen(QColor(C["border"]), 1))
            painter.setBrush(QColor(C["bg_elevated"]))
            painter.drawRoundedRect(box, 8, 8)

            bold = QFont(label); bold.setBold(True)
            painter.setFont(bold)
            painter.setPen(QColor(C["text_primary"]))
            painter.drawText(QRectF(box.left() + 14, box.top() + 8, width - 28, 17),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             title)
            painter.setFont(label)

            painter.setPen(QPen(QColor(C["border_light"]), 1))
            painter.drawLine(QPointF(box.left() + 12, box.top() + 29),
                             QPointF(box.right() - 12, box.top() + 29))

            for row, (dot, text, value) in enumerate(rows):
                y = box.top() + 34 + row * 17
                if dot is not None:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(dot)
                    painter.drawEllipse(QRectF(box.left() + 14, y + 5, 7, 7))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QColor(C["text_muted"]))
                painter.drawText(
                    QRectF(box.left() + (28 if dot is not None else 14), y,
                           width - 40, 17),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
                painter.setPen(QColor(C["text_primary"]))
                painter.drawText(QRectF(box.left(), y, width - 14, 17),
                                 Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter, value)

        painter.end()


class _SalaryPage(QWidget):
    """Who is on what, and setting it — on a page, not in a dialog.

    THIS REPLACES THREE DIALOGS: the Salaries list, the Set-salary form and
    the Salary-history window. They were three modals that could not be open
    together, so checking what somebody was on before changing it meant
    closing one to open another and remembering the figure in between.

    A form in a dialog also has nowhere to grow. The salary structure below is
    a table of five components; in a modal it was the thing that got cut off,
    and the effective date — the field that decides which months change — sat
    behind two other prompts nobody read by the time they reached it.

    THE SPLIT IS SHOWN, NOT EDITED. It is the company's arrangement, applied
    by the server, and it updates as the CTC is typed so the effect of a
    figure is visible before it is saved. Typing over it here would let a
    payslip disagree with the policy it was supposed to follow.
    """

    back = Signal()
    page_title = "Salaries"

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._rows: list[dict] = []
        self._selected: dict | None = None
        self._history: list[dict] = []
        self._build_ui()

    # ── layout ──────────────────────────────────────────────────────────
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}" + _theme.scrollbar())
        host = QWidget()
        _clear_bg(host)
        scroll.setWidget(host)
        outer.addWidget(scroll)

        root = QVBoxLayout(host)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(14)

        head = _card()
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(18, 14, 18, 14)
        head_row.setSpacing(12)
        back = _btn("Back to payroll", variant="secondary", height=34)
        back.setIcon(_icons.icon("chevron-left", 14, C["text_muted"]))
        back.clicked.connect(self.back.emit)
        head_row.addWidget(back)
        note = QLabel(
            "A salary takes effect from a date, and the one before it stays on "
            "the record. Months already finalised are never affected.")
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color:{C['text_muted']};font-size:{Type.SMALL}px;"
            f"background:transparent;border:none;")
        head_row.addWidget(note, 1)
        root.addWidget(head)

        body = QHBoxLayout()
        body.setSpacing(14)

        # ── who ─────────────────────────────────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by name or employee ID…")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(34)
        self._search.textChanged.connect(self._filter)
        left.addWidget(self._search)

        self._table = _tune_table(QTableWidget(0, 4))
        self._table.setHorizontalHeaderLabels(
            ["Employee", "Monthly gross", "Overtime / hour", "From"])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumHeight(420)
        self._table.setMinimumWidth(520)
        _align_numeric_headings(self._table, (1, 2))
        self._table.itemSelectionChanged.connect(self._person_chosen)
        left.addWidget(self._table, 1)
        body.addLayout(left, 1)

        # ── what they are on ────────────────────────────────────────────
        editor = _card()
        form_box = QVBoxLayout(editor)
        form_box.setContentsMargins(18, 16, 18, 16)
        form_box.setSpacing(12)

        self._who = QLabel("Choose somebody on the left")
        self._who.setStyleSheet(
            f"color:{C['text_primary']};font-size:{Type.SECTION}px;font-weight:700;"
            f"background:transparent;border:none;")
        form_box.addWidget(self._who)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)

        def field(caption, widget, line):
            label = QLabel(caption)
            label.setStyleSheet(
                f"color:{C['text_muted']};font-size:{Type.SMALL}px;"
                f"background:transparent;border:none;")
            grid.addWidget(label, line, 0)
            grid.addWidget(widget, line, 1)

        # ANNUAL, because that is the figure people agree on: an offer is
        # "three lakh a year", not "twenty-five thousand a month". Everything
        # monthly below is derived from it and shown, never typed.
        self._ctc = QDoubleSpinBox()
        self._ctc.setRange(0, 1000000000)
        self._ctc.setDecimals(0)
        self._ctc.setPrefix("₹ ")
        self._ctc.setSuffix("  per year")
        self._ctc.setGroupSeparatorShown(True)
        self._ctc.valueChanged.connect(self._restate)
        field("Annual CTC", self._ctc, 0)

        self._gross = QDoubleSpinBox()
        self._gross.setRange(0, 100000000)
        self._gross.setDecimals(2)
        self._gross.setPrefix("₹ ")
        self._gross.setGroupSeparatorShown(True)
        self._gross.setReadOnly(True)
        self._gross.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        # AND IT HAS TO LOOK READ-ONLY.
        #
        # setReadOnly stops the typing and changes nothing about the field, so
        # it still took focus, still drew the focus ring, and still looked
        # like the box above it. Somebody clicked it, typed, watched nothing
        # happen and reported the figure as unchangeable — which it is, on
        # purpose: the gross is the sum of the components, and a gross typed
        # over the top of them would put a payslip at odds with the split it
        # is supposed to be made of. The CTC is the field to change.
        self._gross.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._gross.setStyleSheet(
            f"QDoubleSpinBox{{background:{C['bg_surface_alt']};"
            f"color:{C['text_secondary']};border:1px solid {C['border']};"
            f"border-radius:{Radius.CONTROL}px;padding:10px 12px;}}")
        self._gross.setToolTip(
            "Worked out from the annual CTC and the company's salary "
            "structure — change the CTC above.")
        field("Monthly gross  (from CTC)", self._gross, 1)

        self._overtime = QDoubleSpinBox()
        self._overtime.setRange(0, 100000)
        self._overtime.setDecimals(2)
        self._overtime.setPrefix("₹ ")
        self._overtime.setSpecialValueText("₹ 0.00  (no overtime paid)")
        field("Overtime, per hour", self._overtime, 2)

        self._from = QDateEdit()
        self._from.setCalendarPopup(True)
        self._from.setDisplayFormat("yyyy-MM-dd")
        field("Effective from", self._from, 3)

        self._remarks = QLineEdit()
        self._remarks.setPlaceholderText("Optional — why this figure")
        field("Remarks", self._remarks, 4)
        form_box.addLayout(grid)

        # ── statutory, and off until somebody says otherwise ────────────
        statutory = QLabel("STATUTORY COMPONENTS")
        statutory.setStyleSheet(
            f"color:{C['text_muted']};font-size:{Type.MICRO}px;font-weight:700;"
            f"letter-spacing:0.6px;background:transparent;border:none;")
        form_box.addWidget(statutory)

        self._epf = QCheckBox("Employees' Provident Fund")
        self._esi = QCheckBox("ESI")
        self._pt = QCheckBox("Professional Tax")
        for box in (self._epf, self._esi, self._pt):
            form_box.addWidget(box)
        hint = QLabel(
            "Rates come from Configuration and apply only to people these are "
            "switched on for. Nothing statutory is deducted by default.")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color:{C['text_muted']};font-size:{Type.MICRO}px;"
            f"background:transparent;border:none;")
        form_box.addWidget(hint)

        # ── the split the CTC produces ──────────────────────────────────
        self._components = _tune_table(QTableWidget(0, 4))
        self._components.setHorizontalHeaderLabels(
            ["Salary component", "Calculation", "Monthly", "Annual"])
        self._components.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._components.verticalHeader().setVisible(False)
        self._components.setMinimumHeight(200)
        _align_numeric_headings(self._components, (2, 3))
        form_box.addWidget(self._components)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self._history_btn = _btn("Salary history", variant="secondary", height=34)
        self._history_btn.setIcon(_icons.icon("clock", 14, C["text_muted"]))
        self._history_btn.clicked.connect(self._show_history)
        buttons.addWidget(self._history_btn)
        self._save = _btn("Save salary", variant="primary", height=34, width=130)
        self._save.clicked.connect(self._save_salary)
        buttons.addWidget(self._save)
        form_box.addLayout(buttons)

        body.addWidget(editor, 1)
        root.addLayout(body, 1)

        # ── every version, underneath ───────────────────────────────────
        self._history_card = _card()
        history_box = QVBoxLayout(self._history_card)
        history_box.setContentsMargins(18, 14, 18, 14)
        history_box.setSpacing(8)
        heading = QLabel("SALARY HISTORY")
        heading.setStyleSheet(
            f"color:{C['text_muted']};font-size:{Type.MICRO}px;font-weight:700;"
            f"letter-spacing:0.6px;background:transparent;border:none;")
        history_box.addWidget(heading)

        self._history_filter = QLineEdit()
        self._history_filter.setPlaceholderText(
            "Filter by date — 2026, 2026-07, or July…")
        self._history_filter.setClearButtonEnabled(True)
        self._history_filter.setFixedHeight(32)
        self._history_filter.textChanged.connect(self._filter_history)
        history_box.addWidget(self._history_filter)

        self._history_table = _tune_table(QTableWidget(0, 6))
        self._history_table.setHorizontalHeaderLabels(
            ["Effective from", "Annual CTC", "Monthly gross", "Overtime / hour",
             "Set by", "Remarks"])
        self._history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._history_table.verticalHeader().setVisible(False)
        self._history_table.setMinimumHeight(180)
        _align_numeric_headings(self._history_table, (1, 2, 3))
        history_box.addWidget(self._history_table)
        self._history_card.hide()
        root.addWidget(self._history_card)

        self._set_enabled(False)

    def _set_enabled(self, on: bool):
        for widget in (self._ctc, self._overtime, self._from, self._remarks,
                       self._epf, self._esi, self._pt, self._save,
                       self._history_btn):
            widget.setEnabled(on)

    # ── reading ─────────────────────────────────────────────────────────
    def refresh(self):
        worker = _FetchWorker(f"{API_BASE_URL}/admin/payroll/salaries")
        worker.result.connect(self._fill)
        worker.error.connect(
            lambda e: QMessageBox.warning(self, "Could not load", str(e)))
        _track_worker(self._workers, worker)
        worker.start()

    def _fill(self, data: dict):
        # The company's split comes down with the list, and every page that
        # previews a CTC reads it from here rather than from a copy of its own.
        _remember_salary_template(data.get("template"))
        self._restate()
        self._rows = data.get("data") or []
        self._table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            self._table.setItem(i, 0, _cell(
                f"{row.get('employee_name', '')}\n{row.get('employee_id', '')}"))
            self._table.setItem(i, 1, _cell(
                _money(row.get("gross_monthly")) if row.get("gross_monthly")
                else "Not set", align_right=True))
            self._table.setItem(i, 2, _cell(
                _money(row.get("overtime_hourly"))
                if row.get("overtime_hourly") else "—", align_right=True))
            self._table.setItem(i, 3, _cell(row.get("effective_from") or "—",
                                            muted=True))
        _fit_columns(self._table, stretch=0)
        self._filter()

        # KEEP WHOEVER WAS OPEN. A save reloads this list, and without it the
        # form would empty itself the moment somebody pressed Save.
        if self._selected:
            for i, row in enumerate(self._rows):
                if row.get("employee_id") == self._selected.get("employee_id"):
                    self._table.selectRow(i)
                    break

    def _filter(self, text: str = ""):
        needle = (text or self._search.text()).strip().lower()
        for row, entry in enumerate(self._rows):
            haystack = " ".join(str(entry.get(key) or "") for key in
                                ("employee_name", "employee_id", "designation",
                                 "department")).lower()
            self._table.setRowHidden(row, needle not in haystack)

    def _person_chosen(self):
        rows = {index.row() for index in self._table.selectedIndexes()}
        if len(rows) != 1:
            return
        row = rows.pop()
        if row >= len(self._rows):
            return
        person = self._rows[row]
        self._selected = person

        self._who.setText(f"{person.get('employee_name', '')}  ·  "
                          f"{person.get('employee_id', '')}")
        self._ctc.blockSignals(True)
        self._ctc.setValue(float(person.get("ctc_annual") or 0))
        self._ctc.blockSignals(False)
        self._gross.setValue(float(person.get("gross_monthly") or 0))
        self._overtime.setValue(float(person.get("overtime_hourly") or 0))
        existing = str(person.get("effective_from") or "")
        self._from.setDate(QDate.fromString(existing, "yyyy-MM-dd") if existing
                           else QDate.currentDate().addDays(
                               1 - QDate.currentDate().day()))
        self._remarks.setText(str(person.get("remarks") or ""))
        self._epf.setChecked(bool(person.get("epf_enabled")))
        self._esi.setChecked(bool(person.get("esi_enabled")))
        self._pt.setChecked(bool(person.get("pt_enabled")))
        self._set_enabled(True)
        self._restate()
        self._history_card.hide()

    # ── the split, as the CTC is typed ──────────────────────────────────
    def _restate(self):
        """Show what this CTC divides into, before anything is saved.

        THE SAME ARRANGEMENT THE SERVER APPLIES, computed here only so the
        effect of a figure is visible while it is being typed. What is saved
        is the CTC; the server splits it again and its answer is the one that
        is stored, so these two can never drift into being different rules —
        this one is a preview, not a second implementation of the policy.
        """
        rows = _ctc_preview(self._ctc.value())
        if not rows:
            # The company's split has not arrived yet. Saying so beats an
            # empty table, and beats a made-up split by a much wider margin.
            self._components.setRowCount(1)
            self._components.setItem(0, 0, _cell(
                "The salary structure has not loaded yet.", muted=True))
            for column in range(1, self._components.columnCount()):
                self._components.setItem(0, column, _cell("", align_right=True))
            self._gross.setValue(0)
            return

        self._components.setRowCount(len(rows))
        for i, (name, kind, amount) in enumerate(rows):
            self._components.setItem(i, 0, _cell(name))
            self._components.setItem(i, 1, _cell(kind, muted=True))
            self._components.setItem(i, 2, _cell(_money(amount), align_right=True))
            self._components.setItem(i, 3, _cell(_money(amount * 12),
                                                 align_right=True))
        _fit_columns(self._components, stretch=0)

        self._gross.setValue(sum(r[2] for r in rows))

    # ── saving ──────────────────────────────────────────────────────────
    def _save_salary(self):
        if not self._selected:
            return
        if self._ctc.value() <= 0:
            QMessageBox.information(
                self, "No CTC", "Enter the annual cost to company first — the "
                                "monthly figures are worked out from it.")
            return

        payload = {
            "employee_id": self._selected["employee_id"],
            "ctc_annual": self._ctc.value(),
            "overtime_hourly": self._overtime.value(),
            "effective_from": self._from.date().toString("yyyy-MM-dd"),
            "remarks": self._remarks.text().strip() or None,
            "epf_enabled": self._epf.isChecked(),
            "esi_enabled": self._esi.isChecked(),
            "pt_enabled": self._pt.isChecked(),
        }
        worker = _PostWorker(f"{API_BASE_URL}/admin/payroll/salaries", payload)
        worker.result.connect(lambda d: (
            self.refresh() if d.get("success") else
            QMessageBox.warning(self, "Could not save",
                                d.get("message") or "Unknown error")))
        worker.error.connect(
            lambda e: QMessageBox.warning(self, "Could not save", str(e)))
        _track_worker(self._workers, worker)
        worker.start()

    # ── history, on the same page ───────────────────────────────────────
    def _show_history(self):
        if not self._selected:
            return
        worker = _FetchWorker(
            f"{API_BASE_URL}/admin/payroll/salaries/{self._selected['employee_id']}")
        worker.result.connect(self._fill_history)
        worker.error.connect(
            lambda e: QMessageBox.warning(self, "Could not load", str(e)))
        _track_worker(self._workers, worker)
        worker.start()

    def _fill_history(self, data: dict):
        self._history = data.get("data") or []
        self._history_table.setRowCount(len(self._history))
        for i, entry in enumerate(self._history):
            self._history_table.setItem(i, 0, _cell(
                entry.get("effective_from") or "—"))
            self._history_table.setItem(i, 1, _cell(
                _money(entry.get("ctc_annual")) if entry.get("ctc_annual")
                else "—", align_right=True))
            self._history_table.setItem(i, 2, _cell(
                _money(entry.get("gross_monthly")), align_right=True))
            self._history_table.setItem(i, 3, _cell(
                _money(entry.get("overtime_hourly"))
                if entry.get("overtime_hourly") else "—", align_right=True))
            created = str(entry.get("created_at") or "")[:10]
            who = entry.get("created_by_name") or "—"
            self._history_table.setItem(i, 4, _cell(
                f"{who}\n{created}" if created else who, muted=True))
            self._history_table.setItem(i, 5, _cell(
                entry.get("remarks") or "—", muted=not entry.get("remarks")))
        _fit_columns(self._history_table, stretch=5)
        self._history_card.show()
        self._filter_history()

    def _filter_history(self, text: str = ""):
        needle = (text or self._history_filter.text()).strip().lower()
        for row, entry in enumerate(self._history):
            key = str(entry.get("effective_from") or "")
            try:
                year, number, _day = key.split("-")
                spelled = QDate(int(year), int(number), 1).toString("MMMM yyyy")
            except (ValueError, TypeError):
                spelled = key
            haystack = f"{key} {spelled} {entry.get('remarks') or ''}".lower()
            self._history_table.setRowHidden(row, needle not in haystack)



class _EmployeePayrollPage(QWidget):
    """One person's pay, every month of it, on a page of its own.

    WHY A PAGE AND NOT A DIALOG. The question it answers — "what has this
    person actually been paid" — is not a detail of the month on screen; it
    spans every month there is. A dialog over the payroll table implies you
    are still looking at July, and you are not.

    NOTHING HERE IS RECOMPUTED. Every figure is the one frozen into
    payroll_lines when its month was generated, read back through
    /admin/payroll/employee/:id. A salary rise in July leaves June's row
    reading exactly what June's payslip read, which is the entire reason
    those figures are written down instead of derived.
    """

    back = Signal()
    open_salaries = Signal()

    # What the header shows when this page is current. It is not in PAGES —
    # see _on_page_changed — so it has to name itself.
    page_title = "Employee pay history"

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._employee_id = ""
        self._months: list[dict] = []
        # Newest first, as the server returns it. The one in force is [0].
        self._salaries: list[dict] = []
        self._build_ui()

    def _build_ui(self):
        # THIS PAGE SCROLLS TOO. A header, a salary strip, five cards, a
        # filter, a table of every month and a chart do not fit a laptop
        # screen — squeezed into one, the table collapsed to a single row and
        # the chart to a band. Everything keeps its own height and the page
        # moves instead.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}" + _theme.scrollbar())
        host = QWidget()
        _clear_bg(host)
        scroll.setWidget(host)
        outer.addWidget(scroll)

        root = QVBoxLayout(host)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(14)

        # ── who, and the way back ───────────────────────────────────────
        head = _card()
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(18, 14, 18, 14)
        head_row.setSpacing(14)

        back = _btn("Back to payroll", variant="secondary", height=34)
        back.setIcon(_icons.icon("chevron-left", 14, C["text_muted"]))
        back.clicked.connect(self.back.emit)
        head_row.addWidget(back)

        self._avatar = Avatar(44)
        head_row.addWidget(self._avatar)

        names = QVBoxLayout()
        names.setSpacing(1)
        self._name = QLabel("")
        self._name.setStyleSheet(
            f"color:{C['text_primary']};font-size:{Type.LARGE}px;font-weight:700;"
            f"background:transparent;border:none;")
        names.addWidget(self._name)
        self._subtitle = QLabel("")
        self._subtitle.setStyleSheet(
            f"color:{C['text_muted']};font-size:{Type.SMALL}px;"
            f"background:transparent;border:none;")
        names.addWidget(self._subtitle)
        head_row.addLayout(names, 1)

        self._salary_now = QLabel("")
        self._salary_now.setAlignment(Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
        self._salary_now.setStyleSheet(
            f"color:{C['text_primary']};font-size:{Type.SMALL}px;"
            f"background:transparent;border:none;")
        head_row.addWidget(self._salary_now)

        # SET FROM HERE, not only from the Salaries list. Somebody looking at
        # one person's pay history is exactly the person about to change it,
        # and sending them back to a list to find the same name again is the
        # kind of round trip that gets a salary set against the wrong row.
        # SECONDARY, LIKE EVERY OTHER BUTTON ON THIS PAGE.
        #
        # It was primary, whose rule is white text on the accent colour. The
        # accent fill was not being painted here — the control came out as an
        # outline — so on the light theme it was white text on white: an empty
        # box with no label at all. Reported twice, in both themes.
        #
        # A button nobody can read is worse than one that is not the accent
        # colour, and every other control in this header is secondary anyway,
        # so this now matches them instead of standing out by being blank.
        set_salary = _btn("Set salary", variant="secondary", height=34)
        set_salary.setIcon(_icons.icon("payroll", 14, C["text_muted"]))
        set_salary.clicked.connect(lambda: self.open_salaries.emit())
        head_row.addWidget(set_salary)

        self._amend_btn = _btn("Edit latest", variant="secondary", height=34)
        self._amend_btn.clicked.connect(lambda: self.open_salaries.emit())
        head_row.addWidget(self._amend_btn)
        root.addWidget(head)

        # ── the salary itself, broken out ───────────────────────────────
        salary_card = _card()
        salary_row = QHBoxLayout(salary_card)
        salary_row.setContentsMargins(18, 12, 18, 12)
        salary_row.setSpacing(28)

        self._salary_bits: dict[str, QLabel] = {}
        for key, caption in (("gross", "Monthly gross"),
                             ("overtime", "Overtime / hour"),
                             ("from", "In effect since"),
                             ("by", "Set by"),
                             ("remarks", "Why")):
            column = QVBoxLayout()
            column.setSpacing(1)
            name = QLabel(caption.upper())
            name.setStyleSheet(
                f"color:{C['text_muted']};font-size:{Type.MICRO}px;"
                f"font-weight:700;letter-spacing:0.5px;"
                f"background:transparent;border:none;")
            column.addWidget(name)
            value = QLabel("—")
            value.setStyleSheet(
                f"color:{C['text_primary']};font-size:{Type.SMALL}px;"
                f"font-weight:600;background:transparent;border:none;")
            value.setWordWrap(True)
            column.addWidget(value)
            self._salary_bits[key] = value
            salary_row.addLayout(column, 2 if key == "remarks" else 1)
        root.addWidget(salary_card)

        # ── the lifetime figures ────────────────────────────────────────
        self._stats: dict[str, QLabel] = {}
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        for key, title, caption, icon_name, tint in (
            ("paid_total", "Paid to date", "finalised months only",
             "wallet", C["success"]),
            ("average_net", "Average month", "of the finalised ones",
             "bar-chart-3", C["accent"]),
            ("overtime_total", "Overtime", "across every month",
             "clock", C["warning"]),
            ("deductions_total", "Deductions", "absence, lateness, PF",
             "receipt", C["danger"]),
            ("late_minutes_total", "Late", "minutes, all months",
             "timer", C["warning"]),
        ):
            card = _card()
            outer = QHBoxLayout(card)
            outer.setContentsMargins(16, 12, 16, 12)
            outer.setSpacing(10)
            column = QVBoxLayout()
            column.setSpacing(2)
            outer.addLayout(column, 1)

            heading = QLabel(title.upper())
            heading.setStyleSheet(
                f"color:{C['text_muted']};font-size:{Type.MICRO}px;font-weight:700;"
                f"letter-spacing:0.6px;background:transparent;border:none;")
            column.addWidget(heading)
            value = QLabel("—")
            value.setStyleSheet(
                f"color:{C['text_primary']};font-size:{Type.TITLE}px;"
                f"font-weight:700;background:transparent;border:none;")
            column.addWidget(value)
            self._stats[key] = value
            note = QLabel(caption)
            note.setStyleSheet(
                f"color:{C['text_muted']};font-size:{Type.MICRO}px;"
                f"background:transparent;border:none;")
            column.addWidget(note)

            badge = QLabel()
            badge.setFixedSize(34, 34)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setPixmap(_icons.pixmap(icon_name, 17, tint))
            badge.setStyleSheet(
                f"background:{C['bg_surface_alt']};"
                f"border-radius:{Radius.CONTROL}px;"
                f"border:1px solid {C['border_light']};")
            outer.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
            stats_row.addWidget(card, 1)
        root.addLayout(stats_row)

        # ── what they were paid, month by month ─────────────────────────
        #
        # A YEAR IS TWELVE ROWS AND FIVE YEARS IS SIXTY. Somebody checking
        # "what did I pay them last March" should not have to count down a
        # list to find it.
        find_row = QHBoxLayout()
        find_row.setSpacing(8)
        self._month_filter = QLineEdit()
        self._month_filter.setPlaceholderText(
            "Filter by month — 2026, 2026-07, or Jul…")
        self._month_filter.setClearButtonEnabled(True)
        self._month_filter.setFixedHeight(34)
        self._month_filter.textChanged.connect(self._filter_months)
        find_row.addWidget(self._month_filter, 1)
        self._month_matches = _muted_label("")
        find_row.addWidget(self._month_matches)
        root.addLayout(find_row)

        self._table = _tune_table(QTableWidget(0, 12))
        self._table.setHorizontalHeaderLabels(
            ["Month", "Gross", "Working days", "Present", "Leave", "Absent",
             "Late minutes", "Overtime", "Deductions", "Adjustments",
             "Net pay", "Status"])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(False)
        _align_numeric_headings(self._table, range(1, 11))
        # ROOM FOR A YEAR AT A TIME. Without a minimum the table is one row
        # tall inside a scroll area, which is how this page first looked.
        self._table.setMinimumHeight(320)
        root.addWidget(self._table)

        # ── the same months, drawn ──────────────────────────────────────
        chart_card = _card()
        chart_box = QVBoxLayout(chart_card)
        chart_box.setContentsMargins(0, 0, 0, 0)
        self._chart = _PayrollCostChart()
        self._chart.setMinimumHeight(260)
        chart_box.addWidget(self._chart)
        root.addWidget(chart_card)

        footer = QHBoxLayout()
        self._footnote = _muted_label("")
        footer.addWidget(self._footnote)
        footer.addStretch()
        history = _btn("Salary history", variant="secondary", height=34)
        history.setIcon(_icons.icon("clock", 14, C["text_muted"]))
        history.clicked.connect(self._salary_history_dialog)
        footer.addWidget(history)
        root.addLayout(footer)

    # ── reading ─────────────────────────────────────────────────────────
    def load(self, employee_id: str):
        self._employee_id = employee_id
        self._name.setText("Loading…")
        self._subtitle.setText("")
        worker = _FetchWorker(f"{API_BASE_URL}/admin/payroll/employee/{employee_id}")
        worker.result.connect(self._populate)
        worker.error.connect(
            lambda error: QMessageBox.warning(self, "Could not load", str(error)))
        _track_worker(self._workers, worker)
        worker.start()

    def refresh(self):
        if self._employee_id:
            self.load(self._employee_id)

    def _populate(self, data: dict):
        person = data.get("employee") or {}
        months = data.get("months") or []
        totals = data.get("totals") or {}
        self._months = months
        self._salaries = data.get("salaries") or []

        def money(value):
            try:
                return f"₹{float(value or 0):,.2f}"
            except (TypeError, ValueError):
                return "—"

        self._avatar.set_initials(str(person.get("employee_name") or "?"))
        self._name.setText(str(person.get("employee_name") or ""))
        parts = [person.get("employee_id"), person.get("designation"),
                 person.get("department")]
        if person.get("joined_on"):
            parts.append(f"joined {person['joined_on']}")
        self._subtitle.setText("  ·  ".join(str(p) for p in parts if p))

        # THE ONE IN FORCE IS THE FIRST — the history comes back newest
        # first. A salary dated next month is stored and is not this one.
        current = (self._salaries or [{}])[0]
        has_salary = current.get("gross_monthly") is not None
        self._salary_now.setText(
            f"{money(current.get('gross_monthly'))} a month"
            if has_salary else "No salary set")

        self._salary_bits["gross"].setText(
            money(current.get("gross_monthly")) if has_salary else "Not set")
        self._salary_bits["overtime"].setText(
            money(current.get("overtime_hourly"))
            if float(current.get("overtime_hourly") or 0) else "Not paid")
        self._salary_bits["from"].setText(current.get("effective_from") or "—")
        created = str(current.get("created_at") or "")[:10]
        who = current.get("created_by_name") or "—"
        self._salary_bits["by"].setText(f"{who}{f'  ·  {created}' if created else ''}")
        self._salary_bits["remarks"].setText(current.get("remarks") or "—")
        # Nothing to correct until there is something to correct.
        self._amend_btn.setEnabled(bool(current.get("effective_from")))

        self._stats["paid_total"].setText(money(totals.get("paid_total")))
        self._stats["average_net"].setText(money(totals.get("average_net")))
        self._stats["overtime_total"].setText(money(totals.get("overtime_total")))
        self._stats["deductions_total"].setText(money(totals.get("deductions_total")))
        self._stats["late_minutes_total"].setText(
            str(int(totals.get("late_minutes_total") or 0)))

        # NEWEST FIRST IN THE TABLE, oldest first in the chart. A list of
        # payslips is read from the most recent; a trend is read forwards.
        rows = list(reversed(months))
        self._table.setRowCount(len(rows))
        for i, month in enumerate(rows):
            def number(value):
                return f"{float(value or 0):g}"

            finalised = month.get("status") == "FINALIZED"
            self._table.setItem(i, 0, _cell(str(month.get("month", ""))))
            self._table.setItem(i, 1, _cell(money(month.get("gross_monthly")),
                                            align_right=True))
            self._table.setItem(i, 2, _cell(number(month.get("working_days")),
                                            align_right=True))
            self._table.setItem(i, 3, _cell(number(month.get("present_days")),
                                            align_right=True))
            leave = (float(month.get("paid_leave_days") or 0)
                     + float(month.get("unpaid_leave_days") or 0))
            self._table.setItem(i, 4, _cell(number(leave), align_right=True))
            absent = _cell(number(month.get("absent_days")), align_right=True)
            if float(month.get("absent_days") or 0):
                absent.setForeground(QColor(C["danger"]))
            self._table.setItem(i, 5, absent)
            minutes = int(float(month.get("late_minutes") or 0))
            late = _cell(str(minutes) if minutes else "—", align_right=True)
            if minutes:
                late.setForeground(QColor(C["warning"]))
            self._table.setItem(i, 6, late)
            self._table.setItem(i, 7, _cell(
                money(month.get("overtime_amount"))
                if float(month.get("overtime_amount") or 0) else "—",
                align_right=True))
            self._table.setItem(i, 8, _cell(
                money(month.get("total_deductions"))
                if float(month.get("total_deductions") or 0) else "—",
                align_right=True))
            self._table.setItem(i, 9, _cell(
                money(month.get("adjustments_total"))
                if float(month.get("adjustments_total") or 0) else "—",
                align_right=True))
            net = _cell(money(month.get("net_pay")), align_right=True)
            font = net.font(); font.setBold(True); net.setFont(font)
            # GREEN ONLY WHEN IT IS SETTLED. A draft's net can still move, and
            # colouring it the same as a paid month says it cannot.
            net.setForeground(QColor(C["success"] if finalised else C["text_muted"]))
            self._table.setItem(i, 10, net)
            self._table.setItem(i, 11, _cell(
                "Paid" if finalised else "Draft"))

        _fit_columns(self._table, stretch=None)
        # A filter typed before a refresh must survive it.
        self._filter_months()

        # The chart takes the same shape the payroll page's does, so one
        # widget draws both rather than two that can disagree about what a
        # bar means.
        self._chart.set_months([{
            "month": m.get("month"),
            "net_before_adjustments": m.get("net_pay"),
            "deductions": m.get("total_deductions"),
            "overtime": m.get("overtime_amount"),
            "payroll_cost": float(m.get("net_pay") or 0)
                          + float(m.get("total_deductions") or 0),
            "employees": 1,
            "status": m.get("status"),
        } for m in months], "this")

        paid = int(totals.get("months_paid") or 0)
        draft = int(totals.get("months_drafted") or 0)
        self._footnote.setText(
            f"{paid} month{'' if paid == 1 else 's'} paid"
            + (f"  ·  {draft} still in draft" if draft else "")
            + "  ·  drafts are not counted in the totals above")

    def _filter_months(self, text: str = ""):
        """Show only the months that match. Filtered here, not refetched —
        the whole history is already on screen."""
        needle = (text or self._month_filter.text()).strip().lower()
        rows = list(reversed(self._months))
        shown = 0
        for row, month in enumerate(rows):
            key = str(month.get("month") or "")
            # "Jul" as well as "2026-07": people say the month, not its number.
            try:
                year, number = key.split("-")
                spelled = QDate(int(year), int(number), 1).toString("MMMM yyyy")
            except (ValueError, TypeError):
                spelled = key
            match = needle in f"{key} {spelled}".lower()
            self._table.setRowHidden(row, not match)
            shown += 1 if match else 0
        self._month_matches.setText(
            f"{shown} of {len(rows)} months" if needle else "")


    def _salary_history_dialog(self):
        """Every version of this person's pay, with who set it and why."""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Salary history — {self._name.text()}")
        dialog.setMinimumSize(660, 360)
        box = QVBoxLayout(dialog)
        box.setContentsMargins(22, 20, 22, 20)
        box.setSpacing(12)

        find = QLineEdit()
        find.setPlaceholderText("Filter by date — 2026, 2026-07, or July…")
        find.setClearButtonEnabled(True)
        find.setFixedHeight(34)
        box.addWidget(find)

        table = _tune_table(QTableWidget(0, 5))
        table.setHorizontalHeaderLabels(
            ["Effective from", "Monthly gross", "Overtime / hour", "Set by",
             "Remarks"])
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        _align_numeric_headings(table, (1, 2))

        rows = getattr(self, "_salaries", [])
        table.setRowCount(len(rows))
        for i, entry in enumerate(rows):
            table.setItem(i, 0, _cell(entry.get("effective_from") or "—"))
            table.setItem(i, 1, _cell(
                f"₹{float(entry.get('gross_monthly') or 0):,.2f}", align_right=True))
            table.setItem(i, 2, _cell(
                f"₹{float(entry.get('overtime_hourly') or 0):,.2f}"
                if entry.get("overtime_hourly") else "—", align_right=True))
            created = str(entry.get("created_at") or "")[:16].replace("T", " ")
            who = entry.get("created_by_name") or "—"
            table.setItem(i, 3, _cell(f"{who}\n{created}" if created else who,
                                      muted=True))
            table.setItem(i, 4, _cell(entry.get("remarks") or "—",
                                      muted=not entry.get("remarks")))
        box.addWidget(table, 1)
        _fit_columns(table, stretch=4)

        def filter_rows(text: str):
            needle = text.strip().lower()
            for row, entry in enumerate(rows):
                key = str(entry.get("effective_from") or "")
                try:
                    year, number, _day = key.split("-")
                    spelled = QDate(int(year), int(number), 1).toString("MMMM yyyy")
                except (ValueError, TypeError):
                    spelled = key
                haystack = f"{key} {spelled} {entry.get('remarks') or ''}".lower()
                table.setRowHidden(row, needle not in haystack)
        find.textChanged.connect(filter_rows)

        close = _btn("Close", variant="secondary", height=34, width=90)
        close.clicked.connect(dialog.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close)
        box.addLayout(row)
        dialog.exec()


class _PayrollDetails(QFrame):
    """One person's month, broken into the parts it is made of.

    WHY A PANEL AND NOT A WIDER TABLE. A payslip has about twenty figures on
    it, and a row cannot hold twenty columns — the attempt is what pushed
    "Late minutes" off the right-hand edge. The table answers "who is being
    paid what"; this answers "and how did that number happen", for the one
    person somebody has clicked on.

    IT SHOWS, IT DOES NOT SET. Every figure here is the frozen one from the
    run. Changing any of them goes through the same menu the table's
    double-click opens, so there is one way to change a payslip rather than
    two that can disagree.
    """

    TABS = ("Summary", "Earnings", "Deductions", "Adjustments")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("payrollDetails")
        self.setFixedWidth(390)
        self.setStyleSheet(
            f"QFrame#payrollDetails{{background:{C['bg_surface']};"
            f"border:1px solid {C['border']};"
            f"border-radius:{Radius.CARD}px;}}")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        head = QHBoxLayout()
        self._name = QLabel("")
        self._name.setStyleSheet(
            f"color:{C['text_primary']};font-size:{Type.BODY}px;font-weight:700;"
            f"background:transparent;border:none;")
        head.addWidget(self._name)
        head.addStretch()
        close = _btn("", variant="secondary", height=26, width=30)
        close.setIcon(_icons.icon("x", 14, C["text_muted"]))
        close.setToolTip("Close the breakdown")
        close.clicked.connect(self.hide)
        head.addWidget(close)
        root.addLayout(head)

        self._subtitle = QLabel("")
        self._subtitle.setStyleSheet(
            f"color:{C['text_muted']};font-size:{Type.MICRO}px;"
            f"background:transparent;border:none;")
        root.addWidget(self._subtitle)

        # QStackedWidget with four flat buttons above it, rather than a
        # QTabWidget: this panel has to look like the rest of the console,
        # and a raw QTabWidget brings its own frame and its own tab styling
        # with it.
        # TWO BY TWO, NOT FOUR ACROSS. "Adjustments" needs about 90px of text
        # and a button adds 36px of padding to it; four of those is 440px in a
        # panel that is 390 wide, and the first attempt at this clipped every
        # tab to "ummar", "arning", "eductio", "justmen". Widening the panel
        # far enough would take it out of the table's space instead. Two rows
        # fit whatever the labels grow into.
        tab_grid = QGridLayout()
        tab_grid.setHorizontalSpacing(6)
        tab_grid.setVerticalSpacing(6)
        self._tab_buttons: list[QPushButton] = []
        for index, name in enumerate(self.TABS):
            button = _btn(name, variant="secondary", height=30)
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _=False, i=index: self._show_tab(i))
            tab_grid.addWidget(button, index // 2, index % 2)
            self._tab_buttons.append(button)
        root.addLayout(tab_grid)

        self._stack = QStackedWidget()

        def page() -> QGridLayout:
            host = QWidget()
            _clear_bg(host)
            grid = QGridLayout(host)
            grid.setContentsMargins(0, 4, 0, 0)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(7)
            grid.setColumnStretch(1, 1)
            self._stack.addWidget(host)
            return grid

        # NAMED ONE BY ONE, not written with setattr in a loop. The loop was
        # shorter and test_panel_attributes rejected it, correctly: an
        # attribute written by setattr is invisible to anything reading the
        # source, which is exactly how a typo in one of these names would
        # survive until somebody opened the tab.
        self._grid_summary = page()
        self._grid_earnings = page()
        self._grid_deductions = page()
        self._grid_adjustments = page()
        root.addWidget(self._stack, 1)

        self._net = QLabel("")
        self._net.setStyleSheet(
            f"color:{C['success']};font-size:{Type.SECTION}px;font-weight:700;"
            f"background:transparent;border:none;")
        root.addWidget(self._net)

        self._show_tab(0)

    def _show_tab(self, index: int):
        self._stack.setCurrentIndex(index)
        for i, button in enumerate(self._tab_buttons):
            button.setChecked(i == index)

    @staticmethod
    def _money(value):
        try:
            return f"₹{float(value or 0):,.2f}"
        except (TypeError, ValueError):
            return "—"

    def _fill(self, grid: QGridLayout, pairs: list[tuple[str, str]],
              emphasis: str = ""):
        """Rebuild one tab's rows. Cleared first, because a person with three
        deductions followed by one with none would otherwise keep showing the
        first person's."""
        while grid.count():
            item = grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # setParent(None) FIRST, AND THAT IS THE WHOLE POINT.
                # deleteLater only schedules the destruction; until the event
                # loop comes round the widget is still a child of the panel
                # and still PAINTED, at the position it last had. Taking it
                # out of the layout does not move it. So every switch between
                # these four tabs left the old rows floating over the new
                # ones: "None on this month" sat across the tab buttons, and
                # pressing Summary appeared to do nothing at all because the
                # previous tab was still drawn on top of it.
                widget.setParent(None)
                widget.deleteLater()

        for row, (caption, value) in enumerate(pairs):
            strong = caption == emphasis
            name = QLabel(caption)
            name.setStyleSheet(
                f"color:{C['text_muted'] if not strong else C['text_primary']};"
                f"font-size:{Type.MICRO}px;{'font-weight:700;' if strong else ''}"
                f"background:transparent;border:none;")
            name.setWordWrap(True)
            figure = QLabel(str(value))
            figure.setStyleSheet(
                f"color:{C['text_primary']};font-size:{Type.MICRO}px;font-weight:"
                f"{'700' if strong else '600'};background:transparent;border:none;")
            figure.setAlignment(Qt.AlignmentFlag.AlignRight
                                | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(name, row, 0)
            grid.addWidget(figure, row, 1)

        # HELD TO THE TOP. Without this the rows are stretched over the whole
        # height of the panel and each one is compressed to a line — legible
        # as a shape, unreadable as a number.
        grid.setRowStretch(len(pairs), 1)

    def show_line(self, line: dict, status: str):
        money = self._money
        self._name.setText(str(line.get("employee_name") or ""))
        self._subtitle.setText(
            f"{line.get('employee_id', '')}"
            + (f"  ·  {line.get('designation')}" if line.get("designation") else "")
            + f"  ·  {'Finalised' if status == 'FINALIZED' else 'Draft'}")

        minutes = int(float(line.get("late_minutes") or 0))
        self._fill(self._grid_summary, [
            ("Working days", f"{float(line.get('working_days') or 0):g}"),
            ("Present", f"{float(line.get('present_days') or 0):g}"),
            ("Paid leave", f"{float(line.get('paid_leave_days') or 0):g}"),
            ("Unpaid leave", f"{float(line.get('unpaid_leave_days') or 0):g}"),
            ("Absent", f"{float(line.get('absent_days') or 0):g}"),
            ("Late days", f"{float(line.get('late_days') or 0):g}"),
            ("Late minutes", f"{minutes}" if minutes else "—"),
            ("A day's pay", money(line.get("per_day"))),
        ])

        # ── WHAT THE GROSS IS MADE OF ───────────────────────────────────
        #
        # This tab was one line — "Gross salary" — on a page whose whole job
        # is showing how a figure happened. The parts are what everything else
        # is computed from: provident fund is a share of Basic plus DA, not of
        # the gross, so a payslip that hides them cannot be checked.
        #
        # FROM THE SALARY VERSION THE LINE WAS FROZEN AGAINST, sent by the
        # server. Not recomputed here from today's CTC: somebody whose salary
        # changed in April would have their March payslip redrawn with April's
        # split, and the parts would stop adding up to the total above them.
        earnings = []
        components = line.get("components") or []
        for part in components:
            rule = str(part.get("rule") or "")
            value = part.get("value")
            how = (f" ({value:g}% of CTC)" if rule == "PERCENT_CTC"
                   else f" ({value:g}% of Basic)" if rule == "PERCENT_BASIC"
                   else " (balance)" if rule == "BALANCE" else "")
            earnings.append((f"{part.get('name', '—')}{how}",
                             money(part.get("monthly"))))
        if components:
            earnings.append(("Gross salary", money(line.get("gross_monthly"))))
        else:
            # A MONTH GENERATED BEFORE THE SPLIT WAS RECORDED. Saying so is
            # the honest answer; filling it in from the salary in effect today
            # would put a figure nobody froze onto a frozen payslip.
            earnings.append(("Gross salary", money(line.get("gross_monthly"))))
            earnings.append(("Breakdown", "not recorded for this month"))
        earnings.append(
            (f"Overtime ({float(line.get('overtime_hours') or 0):g} hrs "
             f"at {money(line.get('overtime_rate'))})",
             money(line.get("overtime_amount"))))
        earnings.append(
            ("Total earnings",
             money(float(line.get("gross_monthly") or 0)
                   + float(line.get("overtime_amount") or 0))))
        self._fill(self._grid_earnings, earnings, emphasis="Total earnings")

        # THE COMPUTED ONES AND THE ENTERED ONES, IN ONE LIST. Absence and
        # lateness are worked out from attendance; provident fund and the rest
        # are typed in by somebody. A payslip shows them together because that
        # is how the money leaves — but the entered ones name who entered them.
        deductions = [
            ("Unpaid leave", money(line.get("unpaid_deduction"))),
            ("Absence", money(line.get("absent_deduction"))),
            ("Lateness", money(line.get("late_deduction"))),
        ]
        for entry in (line.get("deductions") or []):
            deductions.append((
                str(entry.get("kind", "")).replace("_", " ").title(),
                money(entry.get("amount"))))
        deductions.append(("Total deductions", money(line.get("total_deductions"))))
        self._fill(self._grid_deductions, deductions, emphasis="Total deductions")

        entries = line.get("adjustments") or []
        rows = [(f"{str(a.get('kind','')).title()} — {a.get('reason','')}",
                 money(a.get("amount"))) for a in entries]
        if not rows:
            rows = [("None on this month", "—")]
        rows.append(("Total adjustments", money(line.get("adjustments_total"))))
        self._fill(self._grid_adjustments, rows, emphasis="Total adjustments")

        self._net.setText(f"Net pay   {money(line.get('net_pay'))}")


class _PayrollSummaryPage(QWidget):
    """What the month cost, and who lost pay — on a page, not in a dialog.

    THIS REPLACES A MODAL. It was a fixed window holding a headline, a totals
    line and two tables, and on a laptop the second table was below the fold
    of a window that could not be resized past it. Worse, it was modal: the
    figure somebody came to check could not be held beside the payroll table
    it came from, so comparing the two meant closing one and remembering a
    number.

    THE BREAKDOWN IS A SUBTRACTION THAT REACHES ITS OWN TOTAL. The dialog
    printed gross, unpaid leave, absence, overtime and adjustments, then
    "TOTAL PAYROLL COST" underneath — and those did not add up, because the
    payslip had since grown a lateness charge and the entered deductions
    (provident fund, ESI, professional tax) and this endpoint had never been
    told. A reader had no way to know which of the two numbers to trust. The
    server now sends every component; the card below lays them out in the
    order they are applied and ends on the net, so the arithmetic can be
    followed down the column rather than taken on faith.

    Every figure comes from the server's own totals. Nothing on this page is
    added up here — a screen that re-derives a total is a second opinion about
    money, and two opinions is exactly the problem this page was fixing.
    """

    back = Signal()
    page_title = "Payroll summary"

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._month = ""
        self._build_ui()

    # ── layout ──────────────────────────────────────────────────────────
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}" + _theme.scrollbar())
        host = QWidget()
        _clear_bg(host)
        scroll.setWidget(host)
        outer.addWidget(scroll)

        root = QVBoxLayout(host)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(14)

        # ── which month, and how to get back ────────────────────────────
        head = _card()
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(18, 14, 18, 14)
        head_row.setSpacing(12)
        back = _btn("Back to payroll", variant="secondary", height=34)
        back.setIcon(_icons.icon("chevron-left", 14, C["text_muted"]))
        back.clicked.connect(self.back.emit)
        head_row.addWidget(back)

        self._title = QLabel("—")
        self._title.setStyleSheet(
            f"color:{C['text_primary']};font-size:{Type.TITLE}px;"
            f"font-weight:700;background:transparent;border:none;")
        head_row.addWidget(self._title)

        self._status_chip = badge_label("draft", "—")
        head_row.addWidget(self._status_chip)
        head_row.addStretch()
        self._working_days = _muted_label("")
        head_row.addWidget(self._working_days)
        root.addWidget(head)

        # A place for "could not read this", where it is read rather than
        # where it fits — above the figures it is explaining the absence of.
        self._notice = QLabel("")
        self._notice.setWordWrap(True)
        self._notice.setVisible(False)
        self._notice.setStyleSheet(
            f"color:{C['danger']};font-size:{Type.SMALL}px;"
            f"background:transparent;border:none;")
        root.addWidget(self._notice)

        # ── the month at a glance ───────────────────────────────────────
        self._kpis: dict[str, QLabel] = {}
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)
        for key, title, caption, icon_name, tint in (
            ("employees", "Employees", "on this payroll", "users", C["accent"]),
            ("gross", "Gross", "before anything", "payroll", C["success"]),
            ("total_deductions", "Deductions", "every kind", "receipt", C["warning"]),
            ("overtime_amount", "Overtime", "approved hours", "clock", C["accent"]),
            ("net", "Net payout", "leaves the account", "wallet", C["success"]),
        ):
            card, value = _stat_card(title, caption, icon_name, tint)
            self._kpis[key] = value
            kpi_row.addWidget(card, 1)
        root.addLayout(kpi_row)

        # ── the subtraction, in the order it is applied ─────────────────
        breakdown = _card()
        rows = QVBoxLayout(breakdown)
        rows.setContentsMargins(18, 16, 18, 16)
        rows.setSpacing(0)

        caption = _muted_label("WHERE THE MONTH'S MONEY WENT")
        rows.addWidget(caption)
        rows.addSpacing(10)

        self._lines_ui: dict[str, QLabel] = {}
        # (key, label, sign) — the sign is how the figure is DISPLAYED, not a
        # calculation. The server sends deductions as positive amounts; the
        # minus in front of them belongs to the reading, and doing arithmetic
        # here is what this page exists to stop.
        #
        # "±" means the sign comes from the figure itself. An adjustment is
        # the one row that can go either way — a bonus and a fine are both
        # adjustments — so it is the only one whose direction is not known
        # until the number arrives.
        for key, label, sign in (
            ("gross", "Gross salary", ""),
            ("unpaid_leave_deduction", "Unpaid leave", "−"),
            ("absent_deduction", "Absence", "−"),
            ("late_deduction", "Lateness", "−"),
            ("other_deductions", "Deductions entered  (PF, ESI, PT…)", "−"),
            ("overtime_amount", "Overtime", "+"),
            ("adjustments", "Adjustments", "±"),
        ):
            line = QHBoxLayout()
            line.setContentsMargins(0, 5, 0, 5)
            name = QLabel(label)
            name.setStyleSheet(
                f"color:{C['text_secondary']};font-size:{Type.BODY}px;"
                f"background:transparent;border:none;")
            line.addWidget(name)
            line.addStretch()
            value = QLabel("—")
            value.setStyleSheet(
                f"color:{C['text_primary']};font-size:{Type.BODY}px;"
                f"font-weight:600;background:transparent;border:none;")
            line.addWidget(value)
            self._lines_ui[key] = value
            self._lines_ui[f"{key}__sign"] = QLabel(sign)
            rows.addLayout(line)

        rows.addSpacing(6)
        rows.addWidget(_divider())
        rows.addSpacing(10)

        total_row = QHBoxLayout()
        total_line = QLabel("NET PAYOUT")
        total_line.setStyleSheet(
            f"color:{C['text_primary']};font-size:{Type.BODY}px;"
            f"font-weight:700;letter-spacing:0.4px;"
            f"background:transparent;border:none;")
        total_row.addWidget(total_line)
        total_row.addStretch()
        self._net_total = QLabel("—")
        self._net_total.setStyleSheet(
            f"color:{C['success']};font-size:{Type.TITLE}px;font-weight:700;"
            f"background:transparent;border:none;")
        total_row.addWidget(self._net_total)
        rows.addLayout(total_row)
        root.addWidget(breakdown)

        # ── who it happened to ──────────────────────────────────────────
        #
        # Four lists, each of which is a question somebody actually asks:
        # who lost pay to leave, who to lateness, who had something deducted,
        # and who was paid for extra hours.
        self._tables: dict[str, QTableWidget] = {}
        for key, title, headings, numeric in (
            ("leave_deductions", "LOST TO UNPAID LEAVE",
             ["Employee", "Unpaid days", "Deducted"], (1, 2)),
            ("lateness", "CHARGED FOR LATENESS",
             ["Employee", "Late days", "Late minutes", "Deducted"], (1, 2, 3)),
            ("deductions", "DEDUCTIONS ENTERED",
             ["Employee", "What for", "Amount"], (2,)),
            ("overtime", "OVERTIME PAID",
             ["Employee", "Hours", "Paid"], (1, 2)),
        ):
            root.addWidget(_muted_label(title))
            table = _tune_table(QTableWidget(0, len(headings)))
            table.setHorizontalHeaderLabels(headings)
            _align_numeric_headings(table, numeric)
            table.horizontalHeader().setStretchLastSection(True)
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self._tables[key] = table
            root.addWidget(table)

        root.addStretch()

    # ── loading ─────────────────────────────────────────────────────────
    def load(self, month: str):
        """Show `month`, and fetch it.

        The page is cleared FIRST. Left as it was, a slow request leaves last
        month's figures on screen under this month's heading, which is a wrong
        answer rather than a missing one.
        """
        self._month = month or ""
        self._title.setText(self._month or "—")
        self._status_chip.setText("—")
        self._working_days.setText("")
        self._notice.setVisible(False)
        for label in self._kpis.values():
            label.setText("—")
        for key, label in self._lines_ui.items():
            if not key.endswith("__sign"):
                label.setText("—")
        self._net_total.setText("—")
        for table in self._tables.values():
            table.setRowCount(0)

        if not self._month:
            return
        worker = _FetchWorker(f"{API_BASE_URL}/admin/payroll/{self._month}/summary")
        worker.result.connect(self._fill)
        worker.error.connect(self._failed)
        _track_worker(self._workers, worker)
        worker.start()

    def _failed(self, error: str):
        self._notice.setText(f"Could not read the summary: {error}")
        self._notice.setVisible(True)

    def _fill(self, data: dict):
        if not data.get("success"):
            self._failed(data.get("message") or "the month could not be read")
            return
        self._notice.setVisible(False)

        status = str(data.get("status") or "")
        self._status_chip.setText(status.title() or "—")
        self._status_chip.setStyleSheet(_theme.badge(status.lower() or "draft"))
        days = data.get("working_days")
        self._working_days.setText(
            f"{days} working days" if days not in (None, "") else "")

        totals = data.get("totals") or {}
        self._kpis["employees"].setText(str(totals.get("employees", "—")))
        for key in ("gross", "total_deductions", "overtime_amount", "net"):
            self._kpis[key].setText(_money(totals.get(key)))

        for key, label in self._lines_ui.items():
            if key.endswith("__sign"):
                continue
            sign = self._lines_ui[f"{key}__sign"].text()
            amount = totals.get(key)
            if amount is None:
                label.setText("—")
                continue
            if sign == "±":
                # The direction comes from the figure. Printing "±" in front
                # of a negative number gave "±₹-200.00", which reads as two
                # signs disagreeing about the same amount.
                value = float(amount or 0)
                label.setText(("+" if value >= 0 else "−") + _money(abs(value)))
            else:
                label.setText(f"{sign}{_money(amount)}" if sign else _money(amount))
        self._net_total.setText(_money(totals.get("net")))

        self._fill_table("leave_deductions", data.get("leave_deductions"), (
            lambda r: f"{r.get('name', '—')}  ·  {r.get('employee_id', '')}",
            lambda r: f"{float(r.get('days') or 0):g}",
            lambda r: _money(r.get("amount"))))
        self._fill_table("lateness", data.get("lateness"), (
            lambda r: f"{r.get('name', '—')}  ·  {r.get('employee_id', '')}",
            lambda r: str(r.get("days") or 0),
            lambda r: _fmt_minutes(r.get("minutes")),
            lambda r: _money(r.get("amount"))))
        self._fill_table("deductions", data.get("deductions"), (
            lambda r: f"{r.get('name', '—')}  ·  {r.get('employee_id', '')}",
            # The kinds, named. "₹1,800 deducted" and "₹1,800 of provident
            # fund" answer different questions, and the second is the one
            # asked when somebody queries their own payslip.
            lambda r: ", ".join(
                str(i.get("kind") or "").strip()
                for i in (r.get("items") or []) if i.get("kind")) or "—",
            lambda r: _money(r.get("amount"))))
        self._fill_table("overtime", data.get("overtime"), (
            lambda r: f"{r.get('name', '—')}  ·  {r.get('employee_id', '')}",
            lambda r: f"{float(r.get('hours') or 0):g}",
            lambda r: _money(r.get("amount"))))

    def _fill_table(self, key: str, rows, columns):
        """Fill one table, right-aligning everything after the name."""
        table = self._tables[key]
        rows = rows or []
        if not rows:
            # A DASH, NOT AN EMPTY BOX. Four blank tables down a page read as
            # "this did not load"; one row saying nothing happened is an
            # answer. It is also the truthful one — no rows means nobody was
            # charged, not that the figure is unknown.
            table.setRowCount(1)
            table.setItem(0, 0, _cell("—", muted=True))
            for column in range(1, table.columnCount()):
                table.setItem(0, column, _cell("", align_right=True))
        else:
            table.setRowCount(len(rows))
            for i, row in enumerate(rows):
                for column, render in enumerate(columns):
                    table.setItem(i, column,
                                  _cell(render(row), align_right=column > 0))
        _size_table(table)


class _PayrollTab(QWidget):
    """Salaries, a month's run, and the decision to finalise it.

    Emits `open_employee` with an employee id when somebody double-clicks a
    row. The tab does not know what page that opens — the panel owns the
    stack, and a tab reaching into it by index is what made five Quick
    Actions open the wrong page once already.

    THE WORKFLOW IS DRAFT → REVIEW → FINALIZE, and the buttons say which
    stage they are for. A draft can be regenerated as often as attendance is
    corrected; once finalised the figures stop moving and the only way to
    change the month is an adjustment, which stays on the record beside what
    it changed.
    """

    open_employee = Signal(str)
    open_salaries = Signal()
    # The month, because the page is told what to show rather than reaching
    # back into this tab for it.
    open_summary = Signal(str)

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._month = ""
        self._lines: list[dict] = []
        self._status = "NONE"
        # Which person's breakdown is open, so a refresh can re-point the
        # panel at them rather than leaving stale figures on screen.
        self._selected_employee: str | None = None
        # Every month the server knows about, so the chart's span selector can
        # redraw without asking again.
        self._history: list[dict] = []
        self._build_ui()
        self._set_default_month()

        # AUTO-REFRESH. Two administrators can have a month open at once, and one of them can finalise it. Sixty seconds is soon enough that the other does not go on editing a run that has already been frozen.
        #
        # Sixty seconds, not thirty: this page is read, not watched, and a
        # table that reorders itself under the pointer is its own problem.
        self._auto = QTimer(self)
        self._auto.setInterval(60000)
        self._auto.timeout.connect(self._load)
        self._auto.start()

    def _build_ui(self):
        # THE PAGE SCROLLS. Toolbar, five cards, a search box, a table and a
        # chart do not fit a laptop screen at once — the chart was the first
        # thing squeezed to nothing, and it is the part that needs height
        # most. Everything keeps its natural size and the page moves instead.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}" + _theme.scrollbar())
        host = QWidget()
        _clear_bg(host)
        scroll.setWidget(host)
        outer.addWidget(scroll)

        root = QVBoxLayout(host)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(14)

        # ── the month, and the two decisions about it ───────────────────
        #
        # ONLY THE TWO THAT CHANGE THE MONTH LIVE UP HERE. Generating and
        # finalising are the workflow; everything else — exporting it,
        # throwing a draft away, opening the salaries — is a side errand and
        # sits along the bottom. Six buttons in this row overflowed the card
        # on a 1440-wide window: "Generate draft" lost its last letters and
        # the working-day count was cut off the right-hand edge.
        toolbar = _card()
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(18, 12, 18, 12)
        bar.setSpacing(10)

        bar.addWidget(_muted_label("Month"))
        self._month_box = QLineEdit()
        self._month_box.setPlaceholderText("2026-08")
        self._month_box.setFixedWidth(110)
        self._month_box.returnPressed.connect(self._load)
        bar.addWidget(self._month_box)

        open_btn = _btn("Open", variant="secondary", height=36, width=84)
        open_btn.clicked.connect(self._load)
        bar.addWidget(open_btn)

        generate = _btn("Generate draft", variant="primary", height=36, width=140)
        generate.clicked.connect(self._generate)
        bar.addWidget(generate)

        self._finalize_btn = _btn("Finalize", variant="danger", height=36, width=100)
        self._finalize_btn.clicked.connect(self._finalize)
        bar.addWidget(self._finalize_btn)

        # SETTING PAY BELONGS AT THE TOP, beside generating and finalising.
        #
        # It was a "Salaries" button in the errands row under the chart —
        # below the table, past the fold — and somebody who had just added an
        # employee had no way of guessing that is where pay is set. It is not
        # an errand: a person with no salary is a person the next payroll run
        # cannot pay, so it belongs where the month's decisions are made.
        salaries = _btn("＋  Set salary", variant="secondary", height=36, width=140)
        salaries.setIcon(_icons.icon("payroll", 14, C["text_muted"]))
        salaries.setToolTip(
            "Set or change what somebody is paid. A salary takes effect from "
            "a date; months already finalised are never affected.")
        salaries.clicked.connect(self._open_salaries)
        bar.addWidget(salaries)

        bar.addStretch()

        # THE MONTH'S STATE AS A CHIP, not as a word inside a sentence.
        self._status_chip = badge_label("draft", "Not generated")
        bar.addWidget(self._status_chip)
        self._headline = _muted_label("")
        bar.addWidget(self._headline)
        root.addWidget(toolbar)

        # ── who is not on this run, and what to do about it ─────────────
        #
        # A run's lines are frozen when it is generated. That is the point of
        # them and it is invisible: somebody adds an employee, opens payroll,
        # counts one row short and has no way to know why. This says so, by
        # name, and says which of the two situations it is — a draft that can
        # simply be generated again, or a finalised month that never changes.
        self._missing = QLabel("")
        self._missing.setWordWrap(True)
        self._missing.setVisible(False)
        self._missing.setStyleSheet(
            f"color:{C['warning']};font-size:{Type.SMALL}px;"
            f"background:{C['warning_soft']};border:1px solid {C['border']};"
            f"border-radius:{Radius.CONTROL}px;padding:10px 14px;")
        root.addWidget(self._missing)

        # ── what the month comes to, before reading a single row ────────
        self._kpis: dict[str, QLabel] = {}
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)
        for key, title, caption, icon_name, tint in (
            ("employees", "Employees", "on this payroll",
             "users", C["accent"]),
            ("gross", "Gross", "before anything",
             "payroll", C["success"]),
            ("deductions", "Deductions", "absence, PF",
             "receipt", C["warning"]),
            ("overtime", "Overtime", "approved hours",
             "clock", C["accent"]),
            ("net", "Net payout", "leaves the account",
             "wallet", C["success"]),
        ):
            card, value = _stat_card(title, caption, icon_name, tint)
            self._kpis[key] = value
            kpi_row.addWidget(card, 1)
        root.addLayout(kpi_row)

        # ── finding one person among hundreds ───────────────────────────
        #
        # Scrolling works for four employees and not for four hundred, and
        # payroll is exactly the page somebody opens with one name already in
        # mind.
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by name, employee ID, or job title…")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(34)
        self._search.textChanged.connect(self._apply_search)
        search_row.addWidget(self._search, 1)
        self._match_count = _muted_label("")
        search_row.addWidget(self._match_count)
        root.addLayout(search_row)

        # ── the table ───────────────────────────────────────────────────
        self._table = _tune_table(QTableWidget(0, 15))
        self._table.setHorizontalHeaderLabels(
            ["Employee", "Gross salary", "Working days", "Present", "Leave",
             "Absent", "Late", "Late minutes", "Overtime hours",
             "Overtime amount", "Adjustments", "Deductions", "Net salary",
             "Status", ""])
        # A ROW TALL ENOUGH FOR A FACE AND TWO LINES OF TEXT. The default is
        # sized for one line, which crops an avatar to a band.
        _align_numeric_headings(self._table, range(1, 13))
        self._table.verticalHeader().setDefaultSectionSize(56)
        # The icon size the avatars are drawn at. Qt's default is 16px, which
        # shrinks a 28px face to a smudge and leaves the initials unreadable.
        self._table.setIconSize(QSize(28, 28))
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        # FOURTEEN COLUMNS DO NOT FIT, and pretending they do is what cut
        # "Late minutes" in half. The table scrolls sideways inside its own
        # area; stretching the last section instead squeezed every column
        # until the money stopped being readable.
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.setHorizontalScrollMode(
            QTableWidget.ScrollMode.ScrollPerPixel)
        # DOUBLE-CLICK OPENS THE PERSON, not a menu. "Show me Bilal" is what
        # a double-click on Bilal's row means everywhere else in this console;
        # the overtime/adjustment menu keeps the ⋯ button, which is where a
        # menu belongs.
        self._table.cellDoubleClicked.connect(self._open_employee)
        self._table.itemSelectionChanged.connect(self._selection_changed)
        # A HEIGHT OF ITS OWN, now that the page scrolls. Inside a scroll
        # area a stretch factor means nothing — without a minimum the table
        # collapses to a couple of rows and the page scrolls past it.
        self._table.setMinimumHeight(340)
        # FULL WIDTH, AND THAT IS THE POINT. The breakdown used to sit beside
        # this as a 390px panel, which left the table three columns wide out
        # of fifteen — "Chitra Rao" was cut in half and every figure after
        # "Working days" was off the edge. The breakdown is worth a panel; it
        # is not worth two thirds of the table it describes, so it opens over
        # the page instead of next to it.
        root.addWidget(self._table, 1)


        # ── what was deducted under each statutory head ─────────────────
        #
        # SUMMED FROM WHAT WAS ACTUALLY DEDUCTED. These are the figures a
        # finance person is asked for and has to remit, so they come from the
        # deductions entered against payslips — not from what the rates would
        # produce if everybody were enrolled.
        benefits_card = _card()
        benefits_box = QVBoxLayout(benefits_card)
        benefits_box.setContentsMargins(18, 14, 18, 14)
        benefits_box.setSpacing(10)

        benefits_head = QHBoxLayout()
        heading = QLabel("BENEFITS AND DEDUCTIONS")
        heading.setStyleSheet(
            f"color:{C['text_muted']};font-size:{Type.MICRO}px;font-weight:700;"
            f"letter-spacing:0.6px;background:transparent;border:none;")
        benefits_head.addWidget(heading)
        benefits_head.addStretch()
        self._benefits_span = QComboBox()
        self._benefits_span.setFixedWidth(150)
        for caption, key in (("This year", "this"), ("Previous year", "previous"),
                             ("Previous month", "month")):
            self._benefits_span.addItem(caption, key)
        self._benefits_span.currentIndexChanged.connect(self._load_benefits)
        benefits_head.addWidget(self._benefits_span)
        benefits_box.addLayout(benefits_head)

        self._benefits: dict[str, tuple] = {}
        benefits_row = QHBoxLayout()
        benefits_row.setSpacing(12)
        for key, title, icon_name, tint in (
            ("epf", "EPF", "shield", C["accent"]),
            ("esi", "ESI", "umbrella", C["success"]),
            ("professional_tax", "Professional tax", "receipt", C["warning"]),
            ("tax", "Tax", "payroll", C["danger"]),
        ):
            tile = _card()
            tile_box = QVBoxLayout(tile)
            tile_box.setContentsMargins(16, 12, 16, 12)
            tile_box.setSpacing(4)

            badge = QLabel()
            badge.setFixedSize(34, 34)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setPixmap(_icons.pixmap(icon_name, 17, tint))
            badge.setStyleSheet(
                f"background:{C['bg_surface_alt']};"
                f"border-radius:{Radius.CONTROL}px;"
                f"border:1px solid {C['border_light']};")
            tile_box.addWidget(badge)

            name = QLabel(title)
            name.setStyleSheet(
                f"color:{C['text_muted']};font-size:{Type.MICRO}px;"
                f"background:transparent;border:none;")
            tile_box.addWidget(name)

            value = QLabel("—")
            value.setStyleSheet(
                f"color:{C['text_primary']};font-size:{Type.SECTION}px;"
                f"font-weight:700;background:transparent;border:none;")
            tile_box.addWidget(value)

            detail = QLabel("")
            detail.setStyleSheet(
                f"color:{C['text_muted']};font-size:{Type.MICRO}px;"
                f"background:transparent;border:none;")
            tile_box.addWidget(detail)

            self._benefits[key] = (value, detail)
            benefits_row.addWidget(tile, 1)
        benefits_box.addLayout(benefits_row)
        root.addWidget(benefits_card)

        # ── the year behind the month ───────────────────────────────────
        #
        # UNDER THE TABLE, FULL WIDTH, NOT BESIDE IT. Everything on this page
        # is about one month; the chart is the only thing that is about the
        # year, so it goes last and takes the whole width rather than
        # competing with the table for it.
        chart_card = _card()
        chart_box = QVBoxLayout(chart_card)
        chart_box.setContentsMargins(0, 0, 0, 0)
        chart_box.setSpacing(0)

        chart_head = QHBoxLayout()
        chart_head.setContentsMargins(18, 12, 14, 0)
        chart_head.addStretch()
        self._chart_span = QComboBox()
        self._chart_span.setFixedWidth(150)
        for caption, key in _PayrollCostChart.RANGES:
            self._chart_span.addItem(caption, key)
        self._chart_span.currentIndexChanged.connect(self._redraw_chart)
        chart_head.addWidget(self._chart_span)
        chart_box.addLayout(chart_head)

        self._chart = _PayrollCostChart()
        self._chart.setMinimumHeight(260)
        chart_box.addWidget(self._chart)

        self._summary_rows: dict[str, QLabel] = {}
        strip = QGridLayout()
        strip.setContentsMargins(18, 8, 18, 14)
        strip.setHorizontalSpacing(24)
        for index, (key, caption) in enumerate((
            ("average", "Average net pay"),
            ("with_overtime", "Employees with overtime"),
            ("attendance", "Attendance this month"),
        )):
            name = QLabel(caption)
            name.setStyleSheet(
                f"color:{C['text_muted']};font-size:{Type.MICRO}px;"
                f"background:transparent;border:none;")
            value = QLabel("—")
            value.setStyleSheet(
                f"color:{C['text_primary']};font-size:{Type.MICRO}px;"
                f"font-weight:700;background:transparent;border:none;")
            strip.addWidget(name, 0, index * 2)
            strip.addWidget(value, 0, index * 2 + 1)
            self._summary_rows[key] = value
        strip.setColumnStretch(6, 1)
        chart_box.addLayout(strip)
        root.addWidget(chart_card)

        # ── the errands, on one line ────────────────────────────────────
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self._totals = _muted_label("")
        footer.addWidget(self._totals)
        footer.addStretch()

        for text, icon_name, slot in (
            # NO "Breakdown" BUTTON. It opened a panel with this month's
            # figures for one person — which is a strict subset of what
            # double-clicking them now shows, and it needed a row selected
            # first, so pressing it usually asked a question instead of
            # answering one. Two doors to the same room, and the smaller one
            # led to less.
            #
            # NOR "Salaries" ANY MORE. It has moved to the toolbar at the top
            # of the page — setting somebody's pay is the first thing done
            # for a new hire and the reason this page is opened at all, and
            # down here it was below the table, below the chart, past the
            # fold, in a row of errands.
            ("Summary", "reports", self._open_summary),
            ("Export", "download", self._export),
        ):
            button = _btn(text, variant="secondary", height=34)
            button.setIcon(_icons.icon(icon_name, 14, C["text_muted"]))
            button.clicked.connect(slot)
            footer.addWidget(button)

        self._delete_btn = _btn("Delete draft", variant="secondary", height=34)
        self._delete_btn.setIcon(_icons.icon("trash-2", 14, C["danger"]))
        self._delete_btn.clicked.connect(self._delete_draft)
        footer.addWidget(self._delete_btn)
        root.addLayout(footer)

        hint = _muted_label(
            "Double-click a row, or use the ⋯ button, for overtime, "
            "adjustments and deductions")
        root.addWidget(hint)
    def _set_default_month(self):
        # The month that has just ended is the one somebody is paying for.
        today = QDate.currentDate()
        previous = today.addMonths(-1)
        self._month_box.setText(previous.toString("yyyy-MM"))
        self._load()

        # TAAZA RAHE, JAB TAK DIKH RAHA HAI.
        #
        # Ye tab wo cheezein dikhata hai jo koi AUR badalta hai — employee
        # leave apply karta hai, doosra admin faisla leta hai. Bina iske
        # screen wahi jawab dikhata rehta tha jo aakhri baar poochha gaya,
        # aur badlav dekhne ke liye page badal kar wapas aana padta tha.
        # Wahi shikayat thi: "page change karke aane pe ho raha hai".
        #
        # Tees second: itna kam ki server par kuch nahi, itna jaldi ki
        # doosre panel ka faisla bina kuch dabaye aa jaaye.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(30000)
        # _PayrollTab._load() koi page nahi leta — is tab me pagination hai
        # hi nahi. `self._page` yahan maujood nahi hai, aur wo likhna har
        # tees second par ek AttributeError banata: timer chupchaap marta
        # rehta aur tab kabhi taaza na hota. test_panel_attributes ne isi
        # liye pakda — wo har `self._x` padhne ko `self._x` likhe jaane se
        # milata hai.
        self._refresh_timer.timeout.connect(self._load)
        self._refresh_timer.start()

    def refresh(self):
        self._load()
        self._load_history()
        self._load_benefits()

    def _load_benefits(self):
        """What has been deducted under each head over the chosen span."""
        span = self._benefits_span.currentData() or "this"
        worker = _FetchWorker(f"{API_BASE_URL}/admin/payroll/benefits?span={span}")
        worker.result.connect(self._fill_benefits)
        # Quiet on failure, like the chart: this is context beside the month,
        # not the month itself.
        worker.error.connect(lambda _error: None)
        _track_worker(self._workers, worker)
        worker.start()

    def _fill_benefits(self, data: dict):
        for key, (value, detail) in self._benefits.items():
            head = data.get(key) or {}
            if not head.get("configured"):
                # A DASH, NOT ZERO. "Nobody is enrolled in ESI" and "ESI came
                # to nothing this year" are different facts, and ₹0.00 says
                # the second when it means the first.
                value.setText("—")
                detail.setText("not deducted")
                continue
            value.setText(f"₹{float(head.get('total') or 0):,.2f}")
            people = int(head.get("employees") or 0)
            detail.setText(f"{people} employee{'' if people == 1 else 's'}")

    def _load_history(self):
        """The months behind the chart, from the server.

        A SEPARATE REQUEST FROM THE MONTH ON SCREEN, because it answers a
        different question: the table is about one month, the chart is about
        the year around it. Folding them into one call would mean re-reading
        every month's lines each time somebody opens a single month.
        """
        worker = _FetchWorker(f"{API_BASE_URL}/admin/payroll")
        worker.result.connect(self._fill_chart)
        # Quiet on failure: the chart is context, not the page. A red box
        # because a history request timed out would obscure the month
        # somebody actually came here to read.
        worker.error.connect(lambda _error: None)
        _track_worker(self._workers, worker)
        worker.start()

    def _fill_chart(self, data: dict):
        # KEPT, so changing the span does not need another request. The whole
        # history is at most thirty-six rows; re-fetching it to redraw the
        # same thirty-six rows a different way would be a round trip for
        # nothing.
        self._history = sorted(data.get("data") or [],
                               key=lambda m: str(m.get("month", "")))
        self._redraw_chart()

    def _redraw_chart(self):
        self._chart.set_months(getattr(self, "_history", []),
                               self._chart_span.currentData() or "this")

    def _apply_search(self, text: str = ""):
        """Hide the rows that do not match. FILTERED, NOT REFETCHED.

        The month is already on screen in full; asking the server again to
        show a subset of what is already here would put a round trip between
        each keystroke and the answer.
        """
        needle = (text or self._search.text()).strip().lower()
        shown = 0
        for row, line in enumerate(self._lines):
            haystack = " ".join(str(line.get(key) or "") for key in
                                ("employee_name", "employee_id",
                                 "designation", "department")).lower()
            match = needle in haystack
            self._table.setRowHidden(row, not match)
            shown += 1 if match else 0

        if needle:
            self._match_count.setText(
                f"{shown} of {len(self._lines)} match")
            self._totals.setText(
                f"Showing {shown} of {len(self._lines)} employees")
        else:
            self._match_count.setText("")
            self._totals.setText(
                f"Showing {len(self._lines)} of {len(self._lines)} employees")

    def _open_employee(self, row: int, _column: int = 0):
        if 0 <= row < len(self._lines):
            self.open_employee.emit(str(self._lines[row].get("employee_id") or ""))

    def _selection_changed(self):
        """Remember who is selected. Nothing opens on its own.

        THE BREAKDOWN NO LONGER FOLLOWS THE SELECTION. It used to, and it
        meant that arrowing down the table popped a panel open beside every
        row in turn. Opening it is now something somebody asks for — the
        Breakdown button, the row menu, or a double-click.
        """
        rows = {index.row() for index in self._table.selectedIndexes()}
        if len(rows) != 1:
            return
        row = rows.pop()
        if row < len(self._lines):
            self._selected_employee = self._lines[row].get("employee_id")


    # ── reading ─────────────────────────────────────────────────────────
    def _load(self):
        month = self._month_box.text().strip()
        if not month:
            return
        self._month = month
        self._load_history()
        self._load_benefits()
        worker = _FetchWorker(f"{API_BASE_URL}/admin/payroll/{month}")
        worker.result.connect(self._populate)
        worker.error.connect(lambda e: self._headline.setText(f"Error: {e}"))
        _track_worker(self._workers, worker)
        worker.start()

    def _show_missing(self, missing: list, status: str | None):
        """Name anybody the run does not cover, and say what that means.

        The two cases read differently and only one of them is fixable here:
        a DRAFT can be generated again and pick them up; a FINALISED month is
        the record of what was actually paid and never gains a row.
        """
        if not missing or not status:
            self._missing.setVisible(False)
            return

        names = [str(person.get("name") or person.get("employee_id"))
                 for person in missing]
        # Three names and a count, not fourteen names across four lines.
        shown = ", ".join(names[:3])
        if len(names) > 3:
            shown += f" and {len(names) - 3} more"
        count = len(names)
        person = "person is" if count == 1 else "people are"

        if str(status).upper() == "FINALIZED":
            self._missing.setText(
                f"{shown} — {count} {person} not on this month. It was "
                f"finalised before they were added, and a finalised month is "
                f"the record of what was paid, so it does not change. They "
                f"will be on the next month's run.")
        else:
            self._missing.setText(
                f"{shown} — {count} {person} not on this draft, which was "
                f"built before they were added. Press Generate draft to "
                f"include them; anything already entered by hand is kept.")
        self._missing.setVisible(True)

    def _populate(self, data: dict):
        run = data.get("run")
        lines = data.get("lines") or []
        self._lines = lines
        self._status = (run or {}).get("status", "NONE")

        self._show_missing(data.get("missing_employees") or [],
                           (run or {}).get("status"))

        if not run:
            self._status_chip.setStyleSheet(_theme.badge("neutral"))
            self._status_chip.setText("Not generated")
            self._headline.setText("")
            self._table.setRowCount(0)
            self._totals.setText("")
            self._finalize_btn.setEnabled(False)
            # A DISABLED BUTTON HAS TO SAY WHY IT IS DISABLED.
            #
            # Reported as "Finalize does nothing when clicked". It was doing
            # exactly the right thing — there is no draft for this month, so
            # there is nothing to finalise — and it said none of that. Now the
            # button explains itself where somebody is already pointing.
            self._finalize_btn.setToolTip(
                "Nothing to finalise yet — generate the draft for this month "
                "first.")
            self._delete_btn.setEnabled(False)
            self._delete_btn.setToolTip("There is no draft for this month.")
            for value in self._kpis.values():
                value.setText("—")
            for value in self._summary_rows.values():
                value.setText("—")
            return

        finalised = self._status == "FINALIZED"
        self._status_chip.setStyleSheet(
            _theme.badge("finalized" if finalised else "draft"))
        self._status_chip.setText("Finalised" if finalised else "Draft")
        self._status_chip.setToolTip(
            "Finalised. The figures are frozen; changes go through "
            "adjustments, which stay on the record."
            if finalised else
            "Draft. It can still be regenerated, and employees cannot see it.")
        self._headline.setText(
            f"{run['month']}  ·  {run.get('working_days')} working days")
        # A FINALISED MONTH CANNOT BE FINALISED AGAIN. The button goes rather
        # than refusing — the same rule the leave page follows.
        self._finalize_btn.setEnabled(not finalised)
        self._finalize_btn.setToolTip(
            f"{run['month']} is already finalised"
            + (f" ({str(run.get('finalized_at'))[:10]})"
               if run.get("finalized_at") else "")
            + ". The figures are frozen; changes go through adjustments."
            if finalised else
            f"Finalise {run['month']}. After this the figures cannot be "
            f"regenerated.")
        # Nor deleted. A finalised month is the record of what people were
        # paid, and "delete and regenerate" is the one operation finalising
        # exists to prevent.
        self._delete_btn.setEnabled(not finalised)
        self._delete_btn.setToolTip(
            "A finalised month cannot be deleted — it is the record of what "
            "people were paid." if finalised else
            f"Delete the draft for {run['month']}.")

        self._table.setRowCount(len(lines))
        for i, line in enumerate(lines):
            def money(value):
                try:
                    return f"₹{float(value):,.2f}"
                except (TypeError, ValueError):
                    return "—"

            def number(value):
                return f"{float(value or 0):g}"

            # THE SERVER'S OWN TOTAL, not one added up again here. The line
            # carries every kind of deduction — absence, unpaid leave,
            # lateness, and whatever was entered by hand — and re-adding a
            # subset of them in the panel is how a column starts disagreeing
            # with the payslip it is describing.
            deductions = float(line.get("total_deductions") or 0)

            # THE PERSON, NOT A STRING — a face, a name, and what they do.
            #
            # AN ITEM WITH AN ICON, NOT A CELL WIDGET. The first version put a
            # QLabel in the cell and gave it a colour from the theme, which
            # fixed that colour for ever: on a light row the near-white name
            # vanished completely, and on the blue selected row the grey
            # second line went almost as dark as the highlight. A table item
            # is painted by the table, so it follows the row it is in —
            # selected, hovered, light theme, dark theme — without being told.
            role = line.get("designation") or line.get("department") or ""
            person = _cell(
                f"{line.get('employee_name', '')}\n{line.get('employee_id', '')}"
                + (f"  ·  {role}" if role else ""))

            face = Avatar(28)
            face.set_initials(str(line.get("employee_name") or "?"))
            person.setIcon(_round_avatar(face, 28))
            self._table.setItem(i, 0, person)
            self._table.setItem(i, 1, _cell(money(line.get("gross_monthly")),
                                            align_right=True))
            self._table.setItem(i, 2, _cell(number(line.get("working_days")),
                                            align_right=True))
            self._table.setItem(i, 3, _cell(number(line.get("present_days")),
                                            align_right=True))
            # A NUMBER, AND ONLY A NUMBER. This used to read
            # "0 (+1 unpaid)", which needed a column wide enough for a
            # sentence and was cut to "0 (+1 unp…" in every window narrower
            # than that. The split between paid and unpaid still matters, so
            # it moves to the tooltip and to the breakdown, where there is
            # room to say it properly.
            paid_leave = float(line.get("paid_leave_days") or 0)
            unpaid_leave = float(line.get("unpaid_leave_days") or 0)
            leave_cell = _cell(number(paid_leave + unpaid_leave), align_right=True)
            if unpaid_leave:
                leave_cell.setToolTip(
                    f"{number(paid_leave)} paid  ·  {number(unpaid_leave)} unpaid")
                leave_cell.setForeground(QColor(C["warning"]))
            elif paid_leave:
                leave_cell.setToolTip(f"{number(paid_leave)} paid")
            self._table.setItem(i, 4, leave_cell)
            absent_cell = _cell(number(line.get("absent_days")), align_right=True)
            if float(line.get("absent_days") or 0):
                absent_cell.setForeground(QColor(C["danger"]))
            self._table.setItem(i, 5, absent_cell)

            late_days = float(line.get("late_days") or 0)
            late_cell = _cell(number(late_days) if late_days else "—",
                              align_right=True)
            if late_days:
                # Amber, not red. Being late is not being absent, and colouring
                # the two the same tells an administrator they are the same
                # thing when the pay says otherwise.
                late_cell.setForeground(QColor(C["warning"]))
            self._table.setItem(i, 6, late_cell)

            minutes = int(float(line.get("late_minutes") or 0))
            late_minutes = _cell(f"{minutes}" if minutes else "—", align_right=True)
            if minutes:
                late_minutes.setToolTip(
                    f"{minutes // 60}h {minutes % 60}m late across the month"
                    if minutes >= 60 else f"{minutes} minutes late across the month")
                charged = float(line.get("late_deduction") or 0)
                late_minutes.setToolTip(late_minutes.toolTip() + (
                    f"  ·  charged {money(charged)}" if charged
                    else "  ·  not charged"))
            self._table.setItem(i, 7, late_minutes)

            overtime_hours = float(line.get("overtime_hours") or 0)
            self._table.setItem(i, 8, _cell(
                number(overtime_hours) if overtime_hours else "—", align_right=True))
            self._table.setItem(i, 9, _cell(
                money(line.get("overtime_amount"))
                if float(line.get("overtime_amount") or 0) else "—", align_right=True))
            adjustments = float(line.get("adjustments_total") or 0)
            self._table.setItem(i, 10, _cell(money(adjustments) if adjustments else "—",
                                             align_right=True))
            deduction_cell = _cell(money(deductions) if deductions else "—",
                                   align_right=True)
            entered = float(line.get("other_deductions") or 0)
            if entered:
                itemised = "  ·  ".join(
                    f"{d.get('kind', '')} {money(d.get('amount'))}"
                    for d in (line.get("deductions") or []))
                deduction_cell.setToolTip(itemised or money(entered))
            self._table.setItem(i, 11, deduction_cell)

            net = _cell(money(line.get("net_pay")), align_right=True)
            font = net.font(); font.setBold(True); net.setFont(font)
            net.setForeground(QColor(C["success"]))
            self._table.setItem(i, 12, net)

            # The month's state, on every row. It decides what a double-click
            # is allowed to do, and an administrator scrolling a long table
            # should not have to look back up at the toolbar to remember.
            self._table.setItem(i, 13, _cell(
                "Finalised" if finalised else "Draft"))

            # THE SAME MENU THE DOUBLE-CLICK OPENS. Double-click is this
            # console's habit and stays; a visible button is what somebody
            # coming to the page for the first time looks for.
            action = _btn("", variant="secondary", height=28, width=30)
            action.setIcon(_icons.icon("more-horizontal", 15, C["text_muted"]))
            action.setToolTip("Overtime, adjustments and deductions")
            action.clicked.connect(lambda _=False, r=i: self._row_menu(r, 14))
            holder = QWidget()
            _clear_bg(holder)
            holder_row = QHBoxLayout(holder)
            holder_row.setContentsMargins(4, 4, 4, 4)
            holder_row.addWidget(action)
            self._table.setCellWidget(i, 14, holder)

        totals = data.get("totals") or {}
        self._kpis["employees"].setText(str(len(lines)))
        self._kpis["gross"].setText(f"₹{float(totals.get('gross', 0)):,.2f}")
        self._kpis["deductions"].setText(
            f"₹{float(totals.get('deductions', 0)):,.2f}")
        self._kpis["overtime"].setText(
            f"₹{float(totals.get('overtime', 0)):,.2f}")
        self._kpis["net"].setText(f"₹{float(totals.get('net', 0)):,.2f}")

        # THE FILTER SURVIVES A REFRESH. Thirty seconds after somebody types
        # a name the auto-refresh redraws the table; without this the rows
        # they filtered away come back while they are reading.
        self._apply_search()

        # ── the summary card ────────────────────────────────────────────
        count = len(lines) or 1
        with_overtime = sum(1 for l in lines
                            if float(l.get("overtime_hours") or 0) > 0)
        # ATTENDANCE AS A PERCENTAGE OF WHAT WAS EXPECTED, per person, then
        # averaged. Adding all the present days and dividing by all the
        # working days would let somebody on a 31-day month outweigh somebody
        # who joined on the 20th, and the figure would drift with headcount
        # rather than with attendance.
        rates = []
        for line in lines:
            working = float(line.get("working_days") or 0)
            if working <= 0:
                continue
            counted = (float(line.get("present_days") or 0)
                       + float(line.get("paid_leave_days") or 0))
            rates.append(min(100.0, counted / working * 100.0))
        attendance = sum(rates) / len(rates) if rates else 0.0

        self._summary_rows["average"].setText(
            f"₹{float(totals.get('net', 0)) / count:,.2f}")
        self._summary_rows["with_overtime"].setText(str(with_overtime))
        self._summary_rows["attendance"].setText(f"{attendance:.2f}%")

        # Sized to the content, after it exists — see _fit_columns.
        _fit_columns(self._table, stretch=None)
        # THESE TWO CANNOT BE MEASURED. _fit_columns sizes a column by the
        # text in its items, and both of these hold a widget and an empty
        # item — so measuring gives the width of nothing at all.
        self._table.setColumnWidth(0, 240)
        self._table.setColumnWidth(14, 52)

    # ── taking it away with you ─────────────────────────────────────────
    def _export(self):
        """The month as a spreadsheet, built from what is already on screen.

        FROM self._lines, NOT A SECOND REQUEST. The server can hand back the
        same CSV, and asking it would be one more round trip that can disagree
        with the table somebody is looking at — a run regenerated between the
        two would export figures nobody had seen. What is exported here is
        exactly what was checked before pressing the button.
        """
        if not self._lines:
            QMessageBox.information(self, "Nothing to export",
                                    "Open a month first — there are no payslips "
                                    "on screen to export.")
            return

        default = f"payroll-{self._month or 'month'}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Export payroll", default,
                                              "CSV (*.csv)")
        if not path:
            return

        headers = ["Employee ID", "Name", "Department", "Gross salary",
                   "Working days", "Present", "Paid leave", "Unpaid leave",
                   "Absent", "Late days", "Late minutes", "Overtime hours",
                   "Overtime amount", "Deductions", "Adjustments", "Net salary",
                   "Status"]
        rows = [[
            line.get("employee_id", ""),
            line.get("employee_name", ""),
            line.get("department") or line.get("designation") or "Unassigned",
            line.get("gross_monthly", 0),
            line.get("working_days", 0),
            line.get("present_days", 0),
            line.get("paid_leave_days", 0),
            line.get("unpaid_leave_days", 0),
            line.get("absent_days", 0),
            line.get("late_days", 0),
            line.get("late_minutes", 0),
            line.get("overtime_hours", 0),
            line.get("overtime_amount", 0),
            line.get("total_deductions", 0),
            line.get("adjustments_total", 0),
            line.get("net_pay", 0),
            "Finalised" if self._status == "FINALIZED" else "Draft",
        ] for line in self._lines]

        if _export_to_csv(path, headers, rows):
            QMessageBox.information(self, "Exported",
                                    f"{len(rows)} payslips written to\n{path}")
        else:
            QMessageBox.warning(self, "Could not export",
                                f"That file could not be written:\n{path}")

    # ── the workflow ────────────────────────────────────────────────────
    def _delete_draft(self):
        """Throw a draft away, with the count in the question.

        "Delete this draft?" is a question somebody answers yes to without
        reading. "Delete the 2026-06 draft — 14 employees, 3 adjustments?" is
        one they have to look at.
        """
        month = self._month_box.text().strip()
        if not month or self._status == "NONE":
            QMessageBox.information(self, "Nothing to delete",
                                    "There is no draft for that month.")
            return
        if self._status == "FINALIZED":
            QMessageBox.warning(self, "Finalised",
                                "That month is finalised. It is the record of "
                                "what people were paid, and cannot be deleted.")
            return

        adjustments = sum(len(line.get("adjustments") or []) for line in self._lines)
        deductions = sum(len(line.get("deductions") or []) for line in self._lines)
        detail = f"{len(self._lines)} employees"
        if adjustments:
            detail += f", {adjustments} adjustment{'s' if adjustments != 1 else ''}"
        if deductions:
            detail += f", {deductions} deduction{'s' if deductions != 1 else ''}"

        if QMessageBox.question(
            self, "Delete draft",
            f"Delete the {month} draft?\n\n{detail} will be removed, including "
            f"anything entered by hand.\n\nThis cannot be undone — the month "
            f"would have to be generated again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        worker = _DeleteWorker(f"{API_BASE_URL}/admin/payroll/{month}")
        worker.result.connect(lambda _d: (
            QMessageBox.information(self, "Deleted",
                                    f"The {month} draft was deleted."),
            self._load()))
        worker.error.connect(
            lambda message: QMessageBox.warning(self, "Could not delete", str(message)))
        _track_worker(self._workers, worker)
        worker.start()

    def _generate(self):
        month = self._month_box.text().strip()
        if not month:
            return
        answer = QMessageBox.question(
            self, "Generate payroll",
            f"Build the draft for {month}?\n\n"
            "It reads attendance and approved leave for the month. Running it "
            "again replaces the figures but keeps any adjustments already "
            "entered.")
        if answer != QMessageBox.StandardButton.Yes:
            return

        worker = _PostWorker(f"{API_BASE_URL}/admin/payroll/generate", {"month": month})
        worker.result.connect(lambda d: (
            self._headline.setText("Draft generated") if d.get("success")
            else QMessageBox.warning(self, "Could not generate",
                                     d.get("message") or "Unknown error"),
            self._load()))
        worker.error.connect(
            lambda e: QMessageBox.warning(self, "Could not generate", str(e)))
        _track_worker(self._workers, worker)
        worker.start()

    def _finalize(self):
        month = self._month_box.text().strip()
        total = self._totals.text().split("TOTAL PAYOUT")[-1].strip() or "—"
        answer = QMessageBox.warning(
            self, "Finalize payroll",
            f"Finalize {month}?\n\nTotal payout: {total}\n\n"
            "After this the figures cannot be regenerated. Anything that needs "
            "to change is entered as an adjustment, which stays on the record.\n\n"
            "Everybody with an email address will be told their payslip is ready.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return

        worker = _PostWorker(f"{API_BASE_URL}/admin/payroll/{month}/finalize", {})
        worker.result.connect(lambda d: (
            QMessageBox.information(self, "Finalized",
                                    f"{month} is finalised for "
                                    f"{d.get('employees', 0)} employees.")
            if d.get("success") else
            QMessageBox.warning(self, "Could not finalize",
                                d.get("message") or "Unknown error"),
            self._load()))
        worker.error.connect(
            lambda e: QMessageBox.warning(self, "Could not finalize", str(e)))
        _track_worker(self._workers, worker)
        worker.start()

    def _row_menu(self, row: int, _column: int):
        if row >= len(self._lines):
            return
        line = self._lines[row]
        menu = QMenu(self)
        overtime = menu.addAction("Set overtime hours…")
        adjust = menu.addAction("Add an adjustment…")
        deduct = menu.addAction("Add a deduction…")
        # A DEDUCTION IS PART OF THE PAYSLIP; AN ADJUSTMENT SITS BESIDE IT.
        # That is why only one of them survives finalisation: adding a
        # provident fund line to a finalised month would rewrite a payslip
        # somebody has already been given, while an adjustment leaves both the
        # original figure and the correction visible.
        if self._status == "FINALIZED":
            deduct.setEnabled(False)
            deduct.setToolTip("The month is finalised — use an adjustment.")
            overtime.setEnabled(False)
        # UNDER WHATEVER WAS PRESSED. This used to open at the centre of
        # column 0 — the name — so pressing the ⋯ at the far right of a
        # fifteen-column table put the menu on the opposite side of the
        # window, over the sidebar. The button knows where it is; ask it.
        anchor = self._table.cellWidget(row, 14)
        if anchor is not None:
            at = anchor.mapToGlobal(anchor.rect().bottomLeft())
        else:
            at = self._table.viewport().mapToGlobal(
                self._table.visualItemRect(self._table.item(row, 0)).center())
        chosen = menu.exec(at)
        if chosen == overtime:
            self._set_overtime(line)
        elif chosen == adjust:
            self._add_adjustment(line)
        elif chosen == deduct:
            self._add_deduction(line)

    def _set_overtime(self, line: dict):
        if self._status == "FINALIZED":
            QMessageBox.information(
                self, "Finalised",
                "This month is finalised. Add an adjustment for the overtime "
                "instead — it stays on the record beside the original figure.")
            return
        hours, ok = QInputDialog.getDouble(
            self, "Overtime hours",
            f"{line.get('employee_name')}\n\n"
            f"Hours at ₹{float(line.get('overtime_rate') or 0):,.2f} per hour:",
            float(line.get("overtime_hours") or 0), 0, 400, 2)
        if not ok:
            return
        worker = _PostWorker(f"{API_BASE_URL}/admin/payroll/{self._month}/overtime",
                             {"employee_id": line["employee_id"], "hours": hours})
        worker.result.connect(lambda d: (
            self._load() if d.get("success") else
            QMessageBox.warning(self, "Could not save",
                                d.get("message") or "Unknown error")))
        worker.error.connect(lambda e: QMessageBox.warning(self, "Could not save", str(e)))
        _track_worker(self._workers, worker)
        worker.start()

    def _add_deduction(self, line: dict):
        """Provident fund, professional tax, ESI, or a line entered by hand.

        NOTHING HERE IS CALCULATED. The statutory ones differ by company and
        by year, and a wrong statutory deduction is a legal problem rather
        than a bug — so somebody who knows the company's obligations types the
        figure, and the system records it with their name against it.
        """
        kinds = [("PF", "Provident fund"), ("ESI", "ESI"),
                 ("PROFESSIONAL_TAX", "Professional tax"), ("TAX", "Tax"),
                 ("MANUAL", "Manual deduction"), ("OTHER", "Other")]
        labels = [label for _, label in kinds]
        chosen, ok = QInputDialog.getItem(
            self, "Deduction", f"{line.get('employee_name')}\n\nWhat kind?",
            labels, 0, False)
        if not ok:
            return
        kind = next(key for key, label in kinds if label == chosen)

        amount, ok = QInputDialog.getDouble(
            self, "Deduction",
            "Amount:\n\nThis will be taken OFF the pay.\n"
            "To ADD money, use an adjustment instead.",
            0, 0.01, 10000000, 2)
        if not ok or amount <= 0:
            return

        reason, ok = QInputDialog.getText(
            self, "Deduction", "Why? This appears on the payslip.")
        if not ok or not reason.strip():
            return

        worker = _PostWorker(
            f"{API_BASE_URL}/admin/payroll/{self._month}/deductions",
            {"employee_id": line["employee_id"], "kind": kind,
             "amount": amount, "reason": reason.strip()})
        worker.result.connect(lambda d: (
            self._load() if d.get("success") else
            QMessageBox.warning(self, "Could not save",
                                d.get("message") or "Unknown error")))
        worker.error.connect(lambda e: QMessageBox.warning(self, "Could not save", str(e)))
        _track_worker(self._workers, worker)
        worker.start()

    def _add_adjustment(self, line: dict):
        kinds = ["BONUS", "INCENTIVE", "REIMBURSEMENT", "ADVANCE", "FINE", "OTHER"]
        kind, ok = QInputDialog.getItem(
            self, "Adjustment", f"{line.get('employee_name')}\n\nWhat kind?",
            [k.title() for k in kinds], 0, False)
        if not ok:
            return
        kind = kind.upper()

        amount, ok = QInputDialog.getDouble(
            self, "Adjustment",
            "Amount:\n\n"
            + ("This will be taken OFF the pay." if kind in ("ADVANCE", "FINE")
               else "This will be ADDED to the pay." if kind != "OTHER"
               else "Positive adds, negative takes away."),
            0, -10000000, 10000000, 2)
        if not ok or amount == 0:
            return

        # THE REASON IS NOT OPTIONAL — the server refuses without one, and it
        # is what somebody reads when they ask why their pay was different.
        reason, ok = QInputDialog.getText(
            self, "Adjustment", "Why? This appears on the payslip.")
        if not ok or not reason.strip():
            return

        worker = _PostWorker(
            f"{API_BASE_URL}/admin/payroll/{self._month}/adjustments",
            {"employee_id": line["employee_id"], "kind": kind,
             "amount": amount, "reason": reason.strip()})
        worker.result.connect(lambda d: (
            self._load() if d.get("success") else
            QMessageBox.warning(self, "Could not add",
                                d.get("message") or "Unknown error")))
        worker.error.connect(lambda e: QMessageBox.warning(self, "Could not add", str(e)))
        _track_worker(self._workers, worker)
        worker.start()

    def _open_summary(self):
        """Hand the month to the Payroll summary PAGE.

        This used to build a modal here — a fixed window whose second table
        was below its own fold, and which could not be held open beside the
        table it was summarising. See _PayrollSummaryPage for the rest.
        """
        month = self._month_box.text().strip()
        if not month:
            return
        self.open_summary.emit(month)

    def _open_salaries(self):
        """Hand off to the Salaries PAGE.

        This used to build a dialog here — a list, a form and a history window
        stacked as three modals. See _SalaryPage for why they became a page.
        """
        self.open_salaries.emit()



class _LeaveTab(QWidget):
    """Leave, from the deciding side.

    THE QUEUE IS THE POINT. Pending requests sort to the top, because this
    page is opened to answer them — a list ordered by date buries the thing
    somebody is waiting on under three months of settled history.

    Approve, reject and revoke each write to the audit log with who did it.
    Leave is the part of this product closest to somebody's pay, and "who
    approved that" is asked long after anybody remembers.
    """

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._page = 1
        self._rows: list[dict] = []
        self._build_ui()
        self._load()

        # TAAZA RAHE, JAB TAK DIKH RAHA HAI.
        #
        # Ye tab wo cheezein dikhata hai jo koi AUR badalta hai — employee
        # leave apply karta hai, doosra admin faisla leta hai. Bina iske
        # screen wahi jawab dikhata rehta tha jo aakhri baar poochha gaya,
        # aur badlav dekhne ke liye page badal kar wapas aana padta tha.
        # Wahi shikayat thi: "page change karke aane pe ho raha hai".
        #
        # Tees second: itna kam ki server par kuch nahi, itna jaldi ki
        # doosre panel ka faisla bina kuch dabaye aa jaaye.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(30000)
        self._refresh_timer.timeout.connect(lambda: self._load(self._page))
        self._refresh_timer.start()

        # AUTO-REFRESH. Requests arrive from employees while this page is open. Without this an administrator watching the queue saw nothing new until they pressed Refresh — and the queue is the entire reason the page exists.
        #
        # Sixty seconds, not thirty: this page is read, not watched, and a
        # table that reorders itself under the pointer is its own problem.
        self._auto = QTimer(self)
        self._auto.setInterval(60000)
        self._auto.timeout.connect(self._load)
        self._auto.start()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(14)

        toolbar = _card()
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(18, 12, 18, 12)
        bar.setSpacing(10)

        bar.addWidget(_muted_label("Status"))
        self._status_filter = QComboBox()
        for label, value in (("Pending", "PENDING"), ("All", ""),
                             ("Approved", "APPROVED"), ("Rejected", "REJECTED"),
                             ("Cancelled", "CANCELLED"), ("Revoked", "REVOKED")):
            self._status_filter.addItem(label, value)
        self._status_filter.setFixedWidth(130)
        self._status_filter.currentIndexChanged.connect(lambda _i: self._load(1))
        bar.addWidget(self._status_filter)

        bar.addWidget(_muted_label("Search"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("name, employee ID or reason")
        self._search.setFixedWidth(240)
        self._search.returnPressed.connect(lambda: self._load(1))
        bar.addWidget(self._search)

        find = _btn("Search", variant="primary", height=36, width=110)
        find.clicked.connect(lambda: self._load(1))
        bar.addWidget(find)

        clear = _btn("Clear", variant="secondary", height=36, width=90)
        clear.clicked.connect(self._clear)
        bar.addWidget(clear)

        bar.addStretch()
        self._count = _muted_label("")
        bar.addWidget(self._count)
        root.addWidget(toolbar)

        self._table = _tune_table(QTableWidget(0, 8))
        self._table.setHorizontalHeaderLabels(
            ["ID", "Employee", "Type", "From", "To", "Days", "Status", "Actions"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        root.addWidget(self._table, 1)

        pager = QHBoxLayout()
        self._prev = _btn("Prev", variant="secondary", height=36, width=96)
        self._prev.setIcon(_icons.icon("chevron-left", 14, C["text_primary"]))
        self._prev.clicked.connect(lambda: self._load(self._page - 1))
        self._next = _btn("Next", variant="secondary", height=36, width=96)
        self._next.setIcon(_icons.icon("chevron-right", 14, C["text_primary"]))
        self._next.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._next.clicked.connect(lambda: self._load(self._page + 1))
        self._page_label = _muted_label("Page 1")
        pager.addWidget(self._prev)
        pager.addWidget(self._page_label)
        pager.addWidget(self._next)
        pager.addStretch()
        self._status_line = _muted_label("")
        pager.addWidget(self._status_line)
        root.addLayout(pager)

    def _clear(self):
        self._search.clear()
        self._status_filter.setCurrentIndex(0)
        self._load(1)

    def refresh(self):
        self._load(self._page)

    def _load(self, page: int = 1):
        page = max(1, page)
        self._page = page
        params = {"page": page}
        status = self._status_filter.currentData()
        if status:
            params["status"] = status
        if self._search.text().strip():
            params["search"] = self._search.text().strip()

        worker = _FetchWorker(f"{API_BASE_URL}/admin/leave", params)
        worker.result.connect(self._populate)
        worker.error.connect(lambda e: self._status_line.setText(f"Error: {e}"))
        _track_worker(self._workers, worker)
        worker.start()

    def _populate(self, data: dict):
        rows = data.get("data") or []
        self._rows = rows
        self._page_label.setText(f"Page {self._page}  •  Total: {data.get('total', 0)}")
        pending = data.get("pending", 0)
        self._count.setText(
            f"{pending} waiting" if pending else "Nothing waiting")

        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self._table.setItem(i, 0, _cell(str(row.get("id", "")), mono=True, muted=True))
            self._table.setItem(i, 1, _cell(
                f"{row.get('employee_name', '')}  ·  {row.get('employee_id', '')}"))
            self._table.setItem(i, 2, _cell(str(row.get("leave_type", "")).title()))
            self._table.setItem(i, 3, _cell(row.get("start_date", ""), muted=True))
            self._table.setItem(i, 4, _cell(row.get("end_date", ""), muted=True))
            days = row.get("total_days")
            self._table.setItem(i, 5, _cell(
                f"{float(days):g}" if days is not None else "", align_right=True))

            # The fourth copy of the status colours, now the same one chip
            # the leave page and attendance use. CANCELLED was missing from
            # this dict and read as plain grey text, identical to a status
            # that had simply failed to load.
            status = str(row.get("status", ""))
            self._table.setCellWidget(i, 6, badge_cell(
                status, status.title(),
                (f"Reason: {row['reason']}"
                 + (f"\n\nRemarks: {row['remarks']}" if row.get("remarks") else ""))
                if row.get("reason") else None))

            actions = QWidget()
            lay = QHBoxLayout(actions)
            lay.setContentsMargins(6, 4, 6, 4)
            lay.setSpacing(8)
            # ONLY THE ACTIONS THAT APPLY. A pending request can be decided; a
            # decided one can only be undone if it was an approval. Showing
            # buttons that refuse is how people learn to distrust them.
            if status == "PENDING":
                yes = _btn("Approve", variant="primary", height=28, width=88)
                yes.clicked.connect(lambda _=False, r=row: self._decide(r, "approve"))
                no = _btn("Reject", variant="danger", height=28, width=80)
                no.clicked.connect(lambda _=False, r=row: self._decide(r, "reject"))
                lay.addWidget(yes)
                lay.addWidget(no)
            elif status == "APPROVED":
                undo = _btn("Revoke", variant="secondary", height=28, width=88)
                undo.clicked.connect(lambda _=False, r=row: self._decide(r, "revoke"))
                lay.addWidget(undo)
            lay.addStretch()
            self._table.setCellWidget(i, 7, actions)
        # Sized to the content, after it exists — see _fit_columns.
        _fit_columns(self._table, stretch=1)

    def _decide(self, row: dict, what: str):
        who = row.get("employee_name") or row.get("employee_id")
        span = (row.get("start_date") if row.get("start_date") == row.get("end_date")
                else f"{row.get('start_date')} to {row.get('end_date')}")

        # A REJECTION MUST CARRY A REASON — the server refuses one without,
        # and the employee reads it. Asking here rather than failing there
        # means the reason is typed once, by somebody who has the request in
        # front of them.
        remarks = ""
        if what in ("reject", "revoke"):
            remarks, ok = QInputDialog.getText(
                self, f"{what.title()} leave",
                f"{who} — {span}\n\n"
                + ("Why is it being rejected? They will read this."
                   if what == "reject"
                   else "Why is the approval being withdrawn? They will read this."))
            if not ok or not remarks.strip():
                return
        else:
            answer = QMessageBox.question(
                self, "Approve leave",
                f"Approve {who}'s leave?\n\n{span}  ·  {row.get('total_days')} day(s)\n\n"
                f"Reason given: {row.get('reason', '')}")
            if answer != QMessageBox.StandardButton.Yes:
                return

        worker = _PostWorker(f"{API_BASE_URL}/admin/leave/{row['id']}/{what}",
                             {"remarks": remarks.strip()})

        def done(data):
            if data.get("success"):
                self._status_line.setText(f"{what.title()}d.")
                self._load(self._page)
            else:
                QMessageBox.warning(self, "Could not do that",
                                    data.get("message") or "Unknown error")

        worker.result.connect(done)
        worker.error.connect(
            lambda e: QMessageBox.warning(self, "Could not do that", str(e)))
        _track_worker(self._workers, worker)
        worker.start()


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

        search_btn = _btn("Search", variant="primary", height=40, width=110)
        search_btn.clicked.connect(self._on_search_clicked)
        filter_row.addWidget(search_btn)

        self._export_btn = _btn("Export CSV", variant="secondary", height=40, width=140)
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
        self._prev_btn  = _btn("Prev", variant="secondary", height=36, width=96)
        self._prev_btn.setIcon(_icons.icon("chevron-left", 14, C["text_primary"]))
        self._prev_btn.clicked.connect(self._prev_page)
        self._next_btn  = _btn("Next", variant="secondary", height=36, width=96)
        self._next_btn.setIcon(_icons.icon("chevron-right", 14, C["text_primary"]))
        self._next_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
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
        # Sized to the content, after it exists — see _fit_columns.
        _fit_columns(self._table, stretch=2)

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
            self._export_btn.setText("Export CSV")
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
            self._export_btn.setText("Export CSV")
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


# ──────────────────────────────────────────────────────────────────────────────
#  Reports Tab
#
#  Every figure here already existed in the database and had never been added
#  up. The Attendance page answers "what happened on this row"; this answers
#  "how did this person do over the month", which is the question payroll
#  actually asks.
#
#  Absence lives here rather than on the Attendance page for a structural
#  reason: an absence is the absence of a row, so it only exists once you walk
#  a date range. This page has a date range; that one has pagination.
# ──────────────────────────────────────────────────────────────────────────────
class _AlertsTab(QWidget):
    """What is wrong right now, and nothing else.

    THIS PAGE EXISTS BECAUSE EVERYTHING ELSE HERE IS PULL. Every other tab
    answers a question somebody thought to ask. The failure that prompted this
    one — an employee's app quietly stopping — asks no question, produces no
    row anywhere, and looks exactly like somebody being on leave. It went
    unnoticed until a screenshot was wanted that had never been taken.

    Nothing here can be dismissed, on purpose. An alert disappears when it
    stops being true and not before. A dismiss button would let the one alert
    that matters be waved away on a busy morning and never come back.
    """

    COLUMNS = [("", 46), ("Employee", 190), ("What", 300), ("Detail", 420)]

    # How often the list refreshes itself. Slow: these are conditions measured
    # in hours, and a page that re-queries every five seconds costs the server
    # far more than the freshness is worth.
    REFRESH_MS = 60_000

    SEVERITY_LOOK = {
        "HIGH":   ("\U0001F534", "danger"),
        "MEDIUM": ("\U0001F7E0", "warning"),
        "LOW":    ("\U0001F7E1", "text_muted"),
    }

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._alerts: list[dict] = []
        self._build_ui()
        self.refresh()

        # NAMED _refresh_timer on purpose. _stop_background_services stops
        # timers by name, from a fixed list; a timer called anything else goes
        # on firing after logout, with a cleared token, at a widget that is
        # being torn down.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start(self.REFRESH_MS)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        bar_card = _card()
        bar = QHBoxLayout(bar_card)
        bar.setContentsMargins(16, 12, 16, 12)
        bar.setSpacing(12)

        self._headline = QLabel("Checking…")
        self._headline.setStyleSheet(
            f"color:{C['text_primary']};font-size:13px;font-weight:700;background:transparent;")
        bar.addWidget(self._headline)
        bar.addStretch()

        self._again = _btn("Check now", variant="secondary", height=40)
        self._again.setIcon(_icons.icon("refresh-cw", 15, C["text_primary"]))
        self._again.clicked.connect(self.refresh)
        bar.addWidget(self._again)
        root.addWidget(bar_card)

        holder = _card(padding=0)
        inner = QVBoxLayout(holder)
        inner.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget(0, len(self.COLUMNS))
        _tune_table(self._table)
        self._table.setHorizontalHeaderLabels([c[0] for c in self.COLUMNS])
        for i, (_title, width) in enumerate(self.COLUMNS):
            self._table.setColumnWidth(i, width)
        self._table.horizontalHeader().setStretchLastSection(True)
        inner.addWidget(self._table)
        root.addWidget(holder, 1)

        self._note = QLabel(
            "Alerts are worked out fresh each time — there is nothing to dismiss. "
            "One disappears when it stops being true. Thresholds live in Configuration.")
        self._note.setWordWrap(True)
        self._note.setStyleSheet(
            f"color:{C['text_muted']};font-size:12px;background:transparent;")
        root.addWidget(self._note)

    # ── data ────────────────────────────────────────────────────────────
    def refresh(self):
        w = _FetchWorker(f"{API_BASE_URL}/admin/alerts", {})
        w.result.connect(self._populate)
        w.error.connect(self._failed)
        _track_worker(self._workers, w)
        w.start()

    def _failed(self, message: str):
        # Say the check itself failed. Showing an empty table would read as
        # "all clear", which is the most damaging thing this page could lie
        # about.
        self._headline.setText("Could not check — " + str(message))
        self._table.setRowCount(0)

    def _populate(self, data: dict):
        self._alerts = data.get("alerts") or []
        if data.get("enabled") is False:
            self._headline.setText("Alerts are switched off in Configuration.")
        elif not self._alerts:
            self._headline.setText("Nothing needs attention.")
        else:
            counts = data.get("counts") or {}
            parts = [f"{counts.get(k, 0)} {k.lower()}" for k in ("HIGH", "MEDIUM", "LOW")
                     if counts.get(k)]
            self._headline.setText(
                f"{len(self._alerts)} thing(s) to look at  ·  " + ", ".join(parts))

        self._table.setRowCount(len(self._alerts))
        for row, alert in enumerate(self._alerts):
            mark, colour_key = self.SEVERITY_LOOK.get(
                alert.get("severity"), ("\u2022", "text_muted"))
            self._table.setItem(row, 0, _cell(mark))
            who = f"{alert.get('employee_id')} — {alert.get('employee_name') or ''}"
            self._table.setItem(row, 1, _cell(who))
            what = _cell(alert.get("title") or "")
            what.setForeground(QColor(C[colour_key]))
            self._table.setItem(row, 2, what)
            self._table.setItem(row, 3, _cell(alert.get("detail") or "", muted=True))
        # Sized to the content, after it exists — see _fit_columns.
        _fit_columns(self._table)


class _ReportsTab(QWidget):
    """Attendance over a range, generated on request.

    NO AUTO-REFRESH, DELIBERATELY. A report is the answer to a question
    somebody asked with the Generate button; re-running it on a timer would
    make a costly query every minute and could move the rows out from under
    whoever is reading them.
    """

    # LEAVE IS ITS OWN COLUMN, beside Absent rather than inside it. They are
    # different facts about a day and were the same number until leave
    # existed — which is what made the absence figure unusable for anything
    # somebody is paid on.
    COLUMNS = [
        ("Employee",    150), ("Shift",       110), ("Working",  80),
        ("Present",      80), ("Leave",        80), ("Absent",   80),
        ("Late",         70), ("Late time",   100), ("Total hours", 100),
        ("Avg/day",      90), ("Idle",         90), ("Screenshots", 100),
    ]

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._rows: list[dict] = []
        # Whether this tab has ever been looked at. See showEvent.
        self._opened = False
        self._build_ui()
        self._load_employees()

    def showEvent(self, event):
        """Run the default report the first time somebody opens the tab.

        THE PAGE USED TO OPEN EMPTY, every time, with twelve column headings
        over nothing and "Choose a range and press Generate." underneath. The
        range was already filled in — this month — so the first thing anybody
        did was press Generate without changing anything, and they did it
        again after every restart because nothing is kept between sessions.

        Still no timer and no reload on every visit: a report is a costly
        query and re-running it under somebody who is reading it moves the
        rows. Once, when the tab is first opened, and after that only when
        Generate is pressed.
        """
        super().showEvent(event)
        if not self._opened:
            self._opened = True
            self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        toolbar = _card()
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(16, 12, 16, 12)
        # EIGHT, NOT TWELVE. Ten controls on one line — two dates, two
        # dropdowns, their labels and two buttons — and every gap between
        # them is paid for nine times. At twelve this row alone pushed the
        # page past the width of a normal laptop window, and the controls on
        # the right went behind a horizontal scrollbar.
        bar.setSpacing(8)

        today = QDate.currentDate()
        self._from = QDateEdit(); self._from.setCalendarPopup(True)
        self._from.setDisplayFormat("dd MMM yyyy")
        # Opens on the current month, which is what a report is asked for
        # nine times out of ten.
        self._from.setDate(QDate(today.year(), today.month(), 1))
        self._to = QDateEdit(); self._to.setCalendarPopup(True)
        self._to.setDisplayFormat("dd MMM yyyy")
        self._to.setDate(today)
        for box in (self._from, self._to):
            box.setFixedHeight(36)

        self._emp = QComboBox()
        self._emp.setFixedHeight(36)
        # 160 is enough for "All employees" and for a name; a combo
        # elides what does not fit and opens to full width anyway. The extra
        # forty pixels were bought at the cost of the row fitting at all.
        self._emp.setMinimumWidth(160)
        self._emp.addItem("All employees", "all")

        # Two reports, one page. Attendance answers "how did people do";
        # audit answers "what did administrators do". Same range controls,
        # so the weekly habit is the same for both.
        self._kind = QComboBox()
        self._kind.setFixedHeight(36)
        self._kind.addItem("Attendance", "attendance")
        self._kind.addItem("Admin actions (audit)", "audit")
        self._kind.currentIndexChanged.connect(lambda _i: self._on_kind_changed())

        run = _btn("Generate", variant="primary", height=36)
        run.clicked.connect(self.refresh)
        self._export_btn = _btn("Export CSV", variant="secondary", height=36)
        self._export_btn.clicked.connect(self._export)
        self._export_btn.setEnabled(False)

        bar.addWidget(_muted_label("From"))
        bar.addWidget(self._from)
        bar.addWidget(_muted_label("To"))
        bar.addWidget(self._to)
        bar.addWidget(_muted_label("Report"))
        bar.addWidget(self._kind)
        bar.addWidget(_muted_label("Employee"))
        bar.addWidget(self._emp)
        bar.addWidget(run)
        bar.addWidget(self._export_btn)
        bar.addStretch()
        root.addWidget(toolbar)

        self._table = _tune_table(QTableWidget(0, len(self.COLUMNS)))
        self._table.setHorizontalHeaderLabels([c[0] for c in self.COLUMNS])
        self._table.horizontalHeader().setStretchLastSection(False)
        for i, (_, width) in enumerate(self.COLUMNS):
            mode = (QHeaderView.ResizeMode.Stretch if i == 0
                    else QHeaderView.ResizeMode.Fixed)
            self._table.horizontalHeader().setSectionResizeMode(i, mode)
            if i:
                self._table.setColumnWidth(i, width)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        root.addWidget(self._table, 1)

        self._status = QLabel("Choose a range and press Generate.")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            f"color:{C['text_muted']}; font-size:12px; background:transparent;"
        )
        root.addWidget(self._status)

    def _load_employees(self):
        w = _FetchWorker(f"{API_BASE_URL}/admin/employees", {"limit": 200})

        def fill(data: dict):
            # REBUILT, not appended to.
            #
            # This used to add straight onto the end. The page reloads the
            # list every time it is opened, so after four visits the dropdown
            # listed every employee four times over — with the same names
            # repeating down a list far taller than the window.
            #
            # The current choice is kept across the rebuild: reloading while
            # somebody has picked an employee must not quietly reset the
            # report to "All employees".
            chosen = self._emp.currentData()
            self._emp.blockSignals(True)
            self._emp.clear()
            self._emp.addItem("All employees", "all")
            for emp in data.get("employees", data.get("data", [])) or []:
                if emp.get("role") == "super_admin":
                    continue
                label = f"{emp.get('employee_id')} — {emp.get('username', '')}"
                self._emp.addItem(label, emp.get("employee_id"))
            if chosen:
                index = self._emp.findData(chosen)
                if index >= 0:
                    self._emp.setCurrentIndex(index)
            self._emp.blockSignals(False)

        w.result.connect(fill)
        w.error.connect(lambda _e: None)
        _track_worker(self._workers, w)
        w.start()

    def _on_kind_changed(self):
        # The employee filter belongs to the attendance report; the audit
        # report is about administrators, not about one employee.
        audit = self._kind.currentData() == "audit"
        self._emp.setEnabled(not audit)
        self._table.setRowCount(0)
        self._export_btn.setEnabled(False)
        self._status.setText(
            "Administrative actions over the range — password resets, "
            "screenshot deletions, role and retention changes. Press Generate."
            if audit else "Choose a range and press Generate.")

    def refresh(self):
        if self._kind.currentData() == "audit":
            return self._refresh_audit()
        params = {
            "from": self._from.date().toString("yyyy-MM-dd"),
            "to":   self._to.date().toString("yyyy-MM-dd"),
            "employee_id": self._emp.currentData() or "all",
        }
        if params["from"] > params["to"]:
            self._status.setText("The From date is after the To date.")
            return

        self._status.setText("Generating…")
        self._export_btn.setEnabled(False)
        w = _FetchWorker(f"{API_BASE_URL}/admin/reports/attendance", params)
        w.result.connect(self._populate)
        w.error.connect(lambda e: self._status.setText(f"Could not reach the server: {e}"))
        _track_worker(self._workers, w)
        w.start()

    AUDIT_COLUMNS = [("When (IST)", 140), ("By", 130), ("Role", 100), ("Action", 400)]

    def _refresh_audit(self):
        params = {
            "from": self._from.date().toString("yyyy-MM-dd"),
            "to":   self._to.date().toString("yyyy-MM-dd"),
        }
        if params["from"] > params["to"]:
            self._status.setText("The From date is after the To date.")
            return

        self._status.setText("Generating…")
        self._export_btn.setEnabled(False)
        w = _FetchWorker(f"{API_BASE_URL}/admin/reports/audit", params)
        w.result.connect(self._populate_audit)
        w.error.connect(lambda e: self._status.setText(
            "Only a super admin can read the audit report."
            if "403" in str(e) else f"Could not reach the server: {e}"))
        _track_worker(self._workers, w)
        w.start()

    def _populate_audit(self, data: dict):
        if not data.get("success"):
            self._status.setText(data.get("message", "The report could not be generated."))
            self._table.setRowCount(0)
            return

        entries = data.get("entries", [])
        self._rows = entries
        self._audit_summary = data

        self._table.setColumnCount(len(self.AUDIT_COLUMNS))
        self._table.setHorizontalHeaderLabels([c[0] for c in self.AUDIT_COLUMNS])
        for i, (_, width) in enumerate(self.AUDIT_COLUMNS):
            self._table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeMode.Stretch if i == 3
                else QHeaderView.ResizeMode.Fixed)
            if i != 3:
                self._table.setColumnWidth(i, width)

        self._table.setRowCount(len(entries))
        for i, row in enumerate(entries):
            self._table.setItem(i, 0, _cell(row.get("at", ""), mono=True, muted=True))
            self._table.setItem(i, 1, _cell(row.get("by", ""), mono=True))
            self._table.setItem(i, 2, _cell(row.get("role", "") or "—", muted=True))
            action = _cell(row.get("action", ""))
            # Anything that removes data is worth spotting at a glance.
            if any(row.get("action", "").startswith(p)
                   for p in ("SCREENSHOTS DELETED", "EMPLOYEE DELETED", "PASSWORD RESET")):
                action.setForeground(QColor(C["warning"]))
            self._table.setItem(i, 3, action)

        self._export_btn.setEnabled(bool(entries))
        if not entries:
            self._status.setText(
                f"No administrative actions between {data.get('from')} and "
                f"{data.get('to')}. That is the expected answer for most weeks.")
            return

        actions = ", ".join(f"{a['action']} ×{a['count']}"
                            for a in (data.get("by_action") or [])[:5])
        people = ", ".join(f"{p['username']} ×{p['count']}"
                           for p in (data.get("by_person") or [])[:5])
        self._status.setText(
            f"{data.get('total')} action(s) over {data.get('days')} day(s), "
            f"{data.get('from')} to {data.get('to')}.\n"
            f"By action: {actions}\nBy person: {people}"
            + ("\nOnly the first 5000 are shown." if data.get("truncated") else ""))
        # Sized to the content, after it exists — see _fit_columns.
        _fit_columns(self._table, stretch=None)
        # THESE TWO CANNOT BE MEASURED. _fit_columns sizes a column by the
        # text in its items, and both of these hold a widget and an empty
        # item — so measuring gives the width of nothing at all.
        self._table.setColumnWidth(0, 240)
        self._table.setColumnWidth(14, 52)

    def _populate(self, data: dict):
        # Coming back from the audit report — restore the attendance columns.
        if self._table.columnCount() != len(self.COLUMNS):
            self._table.setColumnCount(len(self.COLUMNS))
            self._table.setHorizontalHeaderLabels([c[0] for c in self.COLUMNS])
            for i, (_, width) in enumerate(self.COLUMNS):
                self._table.horizontalHeader().setSectionResizeMode(
                    i, QHeaderView.ResizeMode.Stretch if i == 0
                    else QHeaderView.ResizeMode.Fixed)
                if i:
                    self._table.setColumnWidth(i, width)
        if not data.get("success"):
            self._status.setText(data.get("message", "The report could not be generated."))
            self._table.setRowCount(0)
            return

        rows = data.get("rows", [])
        self._rows = rows
        self._table.setRowCount(len(rows))
        self._export_btn.setEnabled(bool(rows))

        for i, row in enumerate(rows):
            self._table.setItem(i, 0, _cell(
                f"{row.get('employee_id', '')} — {row.get('full_name', '')}"))
            self._table.setItem(i, 1, _cell(row.get("shift", "—"), mono=True, muted=True))
            self._table.setItem(i, 2, _cell(str(row.get("working_days", 0)),
                                            mono=True, align_right=True))

            # NUMBERS STAY NUMBERS. A chip belongs on a status, not on a
            # count — a column of right-aligned figures is read by scanning
            # down it, and twelve pills break that. Only the colours come
            # from the shared mapping, so "absent red" is the same red here
            # as on the attendance page.
            present = _cell(str(row.get("present_days", 0)), mono=True, align_right=True)
            present.setForeground(QColor(_theme.status_fg("present")))
            self._table.setItem(i, 3, present)

            leave_days = row.get("leave_days", 0)
            leave = _cell(f"{float(leave_days or 0):g}", mono=True, align_right=True)
            leave.setForeground(QColor(_theme.status_fg("on_leave") if leave_days
                                       else C["text_muted"]))
            leave_dates = row.get("leave_dates") or []
            if leave_dates:
                leave.setToolTip("Approved leave on:\n" + "\n".join(leave_dates))
            self._table.setItem(i, 4, leave)

            absent_days = row.get("absent_days", 0)
            absent = _cell(str(absent_days), mono=True, align_right=True)
            absent.setForeground(QColor(_theme.status_fg("absent") if absent_days
                                        else C["text_muted"]))
            dates = row.get("absent_dates") or []
            if dates:
                # The count alone prompts "which days?" every single time.
                absent.setToolTip("Absent on:\n" + "\n".join(dates))
            self._table.setItem(i, 5, absent)

            late_days = row.get("late_days", 0)
            late = _cell(str(late_days), mono=True, align_right=True)
            late.setForeground(QColor(_theme.status_fg("late") if late_days
                                      else C["text_muted"]))
            self._table.setItem(i, 6, late)

            self._table.setItem(i, 7, _cell(
                _fmt_minutes(row.get("late_minutes", 0)), mono=True, align_right=True))
            self._table.setItem(i, 8, _cell(
                f"{row.get('total_hours', 0):.2f}", mono=True, align_right=True))
            self._table.setItem(i, 9, _cell(
                f"{row.get('avg_hours', 0):.2f}", mono=True, align_right=True))

            # Only meaningful once every present day has reported one. An
            # older client never sends these, and a partial total presented as
            # complete would under-report somebody's idle time.
            reported = row.get("idle_days_reported", 0)
            present = row.get("present_days", 0)
            if reported == 0:
                idle_cell = _cell("—", mono=True, align_right=True, muted=True)
                idle_cell.setToolTip("No idle data reported for this range.")
            else:
                idle_cell = _cell(f"{row.get('idle_hours', 0):.2f}",
                                  mono=True, align_right=True)
                if reported < present:
                    idle_cell.setForeground(QColor(C["warning"]))
                    idle_cell.setToolTip(
                        f"Partial — {reported} of {present} present day(s) "
                        f"reported idle time.")
            self._table.setItem(i, 10, idle_cell)

            self._table.setItem(i, 11, _cell(
                str(row.get("screenshots", 0)), mono=True, align_right=True))

        span = data.get("days", 0)
        self._status.setText(
            f"{len(rows)} employee(s) over {span} day(s), "
            f"{data.get('from')} to {data.get('to')}.  "
            f"Weekly offs and holidays are not counted as absences. "
            f"Hover an absent count to see the dates."
        )
        # Sized to the content, after it exists — see _fit_columns.
        _fit_columns(self._table, stretch=None)
        # THESE TWO CANNOT BE MEASURED. _fit_columns sizes a column by the
        # text in its items, and both of these hold a widget and an empty
        # item — so measuring gives the width of nothing at all.
        self._table.setColumnWidth(0, 240)
        self._table.setColumnWidth(14, 52)

    def _export(self):
        if not self._rows:
            return
        if self._kind.currentData() == "audit":
            return self._export_audit()
        default = (f"ets-report-{self._from.date().toString('yyyyMMdd')}"
                   f"-{self._to.date().toString('yyyyMMdd')}.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export report", default, "CSV (*.csv)")
        if not path:
            return

        # LEAVE IS EXPORTED SEPARATELY TOO. The CSV is what somebody opens in
        # a spreadsheet to work out a month, so it has to draw the same line
        # the table does — approved leave is not absence.
        headers = ["Employee ID", "Name", "Shift", "Working days", "Present",
                   "Leave", "Leave dates",
                   "Absent", "Absent dates", "Late days", "Late minutes",
                   "Total hours", "Avg hours per present day",
                   "Idle hours", "Idle days reported", "Screenshots"]
        rows = [[
            r.get("employee_id", ""), r.get("full_name", ""), r.get("shift", ""),
            r.get("working_days", 0), r.get("present_days", 0),
            r.get("leave_days", 0), " ".join(r.get("leave_dates") or []),
            r.get("absent_days", 0), " ".join(r.get("absent_dates") or []),
            r.get("late_days", 0), r.get("late_minutes", 0),
            f"{r.get('total_hours', 0):.2f}", f"{r.get('avg_hours', 0):.2f}",
            f"{r.get('idle_hours', 0):.2f}", r.get("idle_days_reported", 0),
            r.get("screenshots", 0),
        ] for r in self._rows]

        if _export_to_csv(path, headers, rows):
            self._status.setText(f"Exported {len(rows)} row(s) to {path}")
        else:
            self._status.setText("Could not write that file.")

    def _export_audit(self):
        default = (f"ets-audit-{self._from.date().toString('yyyyMMdd')}"
                   f"-{self._to.date().toString('yyyyMMdd')}.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export audit report", default,
                                              "CSV (*.csv)")
        if not path:
            return
        headers = ["When (IST)", "By", "Role", "Action"]
        rows = [[r.get("at", ""), r.get("by", ""), r.get("role", "") or "",
                 r.get("action", "")] for r in self._rows]
        if _export_to_csv(path, headers, rows):
            self._status.setText(f"Exported {len(rows)} action(s) to {path}")
        else:
            self._status.setText("Could not write that file.")


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

        # THE RUNNING CLOCK, ticking between those refreshes.
        #
        # Separate from the refresh above on purpose: fetching the whole page
        # once a second to move a number would be thirty times the queries
        # for the same result. This adds a second to what the server last
        # said, and every refresh puts it back in step with the server — so
        # drift cannot accumulate past thirty seconds even on a machine whose
        # own clock is wrong.
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick_running_clocks)
        self._tick_timer.start()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        toolbar = _card()
        filter_row = QHBoxLayout(toolbar)
        filter_row.setContentsMargins(18, 12, 18, 12)
        filter_row.setSpacing(10)

        # NO LABEL BESIDE EVERY CONTROL.
        #
        # The row was label, control, label, control, label, control, three
        # buttons — eleven widgets on one line. On a 1400px window they no
        # longer fitted, Qt squeezed each below its minimum, and the labels
        # were drawn ON TOP of the fields: "From" over the date box, "Show"
        # over the dropdown. A placeholder says the same thing inside the
        # control and costs no width.
        self._emp_filter = QLineEdit()
        self._emp_filter.setPlaceholderText("Search id or name")
        self._emp_filter.setMinimumWidth(180)
        self._emp_filter.setClearButtonEnabled(True)
        self._emp_filter.returnPressed.connect(self._on_search_clicked)
        filter_row.addWidget(self._emp_filter, 1)

        # A RANGE, NOT A DAY. Attendance is read in weeks and months; one day
        # at a time made "last week" seven separate searches.
        filter_row.addWidget(_muted_label("From"))
        self._date_filter = QDateEdit(QDate.currentDate())
        self._date_filter.setCalendarPopup(True)
        self._date_filter.setDisplayFormat("dd MMM yyyy")
        self._date_filter.setFixedWidth(132)
        filter_row.addWidget(self._date_filter)

        filter_row.addWidget(_muted_label("to"))
        self._date_to = QDateEdit(QDate.currentDate())
        self._date_to.setCalendarPopup(True)
        self._date_to.setDisplayFormat("dd MMM yyyy")
        self._date_to.setFixedWidth(132)
        filter_row.addWidget(self._date_to)

        # THE RECORD'S STATE ONLY.
        #
        # "Still open, nobody there" is the one an administrator hunts for —
        # those are the rows that need closing, and finding them by eye
        # through pages of history is how they get left for weeks.
        #
        # Late is deliberately NOT here. It is worked out per employee
        # against their own shift, in one place; a filter for it would have
        # to repeat that rule in SQL, and a Late filter that disagreed with
        # the Late column would be worse than no filter at all.
        self._status_filter = QComboBox()
        self._status_filter.setFixedWidth(160)
        for _label, _value in (("All records", ""),
                               ("Active now", "active"),
                               ("Not signed out", "incomplete"),
                               ("Completed", "completed")):
            self._status_filter.addItem(_label, _value)
        # NOT _on_search_clicked. That marks the page as "the user searched",
        # which switches the date filter on — so picking "Not signed out"
        # would also silently restrict the list to today, and the shifts left
        # open days ago (the ones being hunted for) would not appear at all.
        self._status_filter.currentIndexChanged.connect(
            lambda _i: self._load(page=1))
        filter_row.addWidget(self._status_filter)

        search_btn = _btn("Search", variant="primary", height=40, width=112)
        search_btn.setIcon(_icons.icon("search", 16, "#ffffff"))
        search_btn.setIconSize(QSize(16, 16))
        search_btn.clicked.connect(self._on_search_clicked)
        filter_row.addWidget(search_btn)

        clear_btn = _btn("Clear", variant="secondary", height=40, width=92)
        clear_btn.setIcon(_icons.icon("x", 16, C["text_secondary"]))
        clear_btn.setIconSize(QSize(16, 16))
        clear_btn.clicked.connect(self._on_clear_clicked)
        filter_row.addWidget(clear_btn)

        self._export_btn = _btn("Export", variant="secondary", height=40, width=112)
        self._export_btn.setIcon(_icons.icon("download", 16, C["text_secondary"]))
        self._export_btn.setIconSize(QSize(16, 16))
        self._export_btn.clicked.connect(self._export_attendance_csv)
        filter_row.addWidget(self._export_btn)

        filter_row.addStretch()
        root.addWidget(toolbar)

        # ID, Employee, Name, Date, Shift, Check In, Check Out, Hours,
        # Attendance, Shift status.
        #
        # The name is here because an attendance list nobody can read without
        # memorising employee ids is not a list. The shift window is here
        # because "Late 1h 49m" is an accusation, and the reader is entitled
        # to see what it was measured against without opening another screen.
        self._table = _tune_table(QTableWidget(0, 10))
        self._table.setHorizontalHeaderLabels(
            ["ID", "Employee", "Name", "Date", "Shift", "Check In",
             "Check Out", "Hours", "Attendance", "Shift"])
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in range(10):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        # THE NAME TAKES WHAT IS LEFT, AND NEVER LESS THAN IT NEEDS.
        #
        # It was the stretch column with nine fixed ones beside it, so it got
        # whatever remained — about fifty pixels — and every person read
        # "Priy…", "Sha…", "Raj…". A Name column that cannot show a name is
        # not a Name column. The fixed widths below are smaller than they
        # were, and the two chip columns are wide enough for their own
        # contents: "Completed" was being drawn as "omplete".
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(64)
        # THE HEADER STAYS PUT while the rows scroll. Qt does this for a
        # QTableWidget by default; setting it here is a statement of intent so
        # nobody turns the header into a scrolling row later.
        header.setSectionsMovable(False)
        header.setHighlightSections(False)
        self._table.setColumnWidth(0, 52)     # id
        self._table.setColumnWidth(1, 116)    # employee id
        self._table.setColumnWidth(3, 104)    # date
        self._table.setColumnWidth(4, 104)    # shift window
        self._table.setColumnWidth(5, 88)     # check in
        self._table.setColumnWidth(6, 88)     # check out
        self._table.setColumnWidth(7, 92)     # hours
        self._table.setColumnWidth(8, 128)    # attendance chip
        self._table.setColumnWidth(9, 150)    # shift chip
        self._table.verticalHeader().setDefaultSectionSize(52)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.itemDoubleClicked.connect(self._row_detail)
        self._table.setToolTip("Double-click a row for the whole shift.")
        root.addWidget(self._table, 1)

        pag_row = QHBoxLayout()
        self._prev_btn  = _btn("Prev", variant="secondary", height=36, width=96)
        self._prev_btn.setIcon(_icons.icon("chevron-left", 14, C["text_primary"]))
        self._prev_btn.clicked.connect(self._prev_page)
        self._next_btn  = _btn("Next", variant="secondary", height=36, width=96)
        self._next_btn.setIcon(_icons.icon("chevron-right", 14, C["text_primary"]))
        self._next_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
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
        self._date_to.setDate(QDate.currentDate())
        # Clear means clear. Leaving the dropdown set was how a "no results"
        # page looked like a broken list rather than an active filter.
        self._status_filter.blockSignals(True)
        self._status_filter.setCurrentIndex(0)
        self._status_filter.blockSignals(False)
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
        # Sent to the server rather than applied here, so the page numbers
        # and the total count describe the rows actually being shown.
        state = self._status_filter.currentData()
        if state:
            params["status"] = state
        w = _FetchWorker(f"{API_BASE_URL}/attendance/all", params)
        w.result.connect(self._populate)
        w.error.connect(lambda e: print("Attendance error:", e))
        _track_worker(self._workers, w)
        w.start()

    def _populate(self, data: dict):
        rows = data.get("data", [])
        total = data.get("total", 0)
        self._attendance = rows
        # AN EMPTY LIST SAYS WHY IT IS EMPTY. A filter that matches nothing
        # and a page that failed to load look identical otherwise, and the
        # filter is by far the likelier of the two.
        if not rows:
            chosen = self._status_filter.currentText()
            self._page_label.setText(
                f"Nothing matches “{chosen}”."
                if self._status_filter.currentData()
                else "No attendance records here.")
        else:
            self._page_label.setText(f"Page {self._page}  •  Total: {total}")
        self._prev_btn.setEnabled(self._page > 1)
        self._next_btn.setEnabled(self._page * 50 < total)
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self._table.setItem(i, 0, _cell(str(row.get("id", "")), mono=True, muted=True))
            self._table.setItem(i, 1, _cell(row.get("employee_id", ""), mono=True))
            self._table.setItem(i, 2, _cell(row.get("employee_name") or "—"))
            self._table.setItem(i, 3, _cell(
                _fmt_date_only(row.get("login_time")), muted=True))
            self._table.setItem(i, 4, _cell(
                row.get("shift_window") or "—", mono=True, muted=True))
            self._table.setItem(i, 5, _cell(
                _fmt_time_only(row.get("login_time")), mono=True))

            # A TIME, OR NOTHING. This column used to say "● ACTIVE" or "Not
            # signed out" — both of which are the record's state, which now
            # has a column of its own. Saying it in two places at once is
            # what made a row look self-contradictory: "Not signed out" here
            # beside "Signed in again" there.
            self._table.setItem(i, 6, _cell(
                _fmt_time_only(row.get("logout_time")) if row.get("logout_time") else "—",
                mono=True, muted=True))

            # A RUNNING CLOCK FOR AN OPEN SHIFT, a fixed total for a closed
            # one. This cell used to show a dash for anybody currently at
            # work — the one row where the number is most wanted.
            #
            # The seconds come from the SERVER at each fetch and are counted
            # up locally from there. Working it out from login_time on this
            # machine would put the laptop's own clock into a payroll-facing
            # number, and laptops are wrong by minutes all the time.
            hours = _cell("", mono=True, align_right=True)
            if row.get("attendance_status") == "active" \
                    and row.get("elapsed_seconds") is not None:
                hours.setText(_fmt_elapsed(row["elapsed_seconds"]))
                # The same green the Active chip uses, from the same place.
                hours.setForeground(QColor(_theme.status_fg("active")))
                hours.setToolTip("Still running — counted from the server's clock.")
            else:
                hours.setText(self._hours_for(row))
            self._table.setItem(i, 7, hours)

            # TWO COLUMNS, BECAUSE THEY ANSWER TWO QUESTIONS.
            #
            # This was one "Status" cell carrying both, and beside a Logout
            # column reading "Not signed out" it produced a row that looked
            # like it contradicted itself — reported as exactly that. What
            # happened to the record and how the shift went are now separate,
            # and neither borrows the other's words.
            record_status = row.get("attendance_status")
            self._table.setCellWidget(i, 8, _badge_cell(
                record_status,
                # The badge already carries the state in its colour and its
                # word; the dot in front was a third telling of the same
                # thing, drawn from the text font.
                (row.get("attendance_label") or "—"),
                "This shift was never closed — the app was shut down without "
                "signing out, or the machine went offline.\n\nIt closes "
                "automatically once the session has been gone long enough, at "
                "the last moment the person was seen."
                if record_status == "incomplete" else None))

            # Older servers send neither field. A dash is the honest answer
            # there — better than colouring every row as if it were on time.
            # ONE MAPPING, IN theme.status_colors. This was a dict of nine
            # colours written here, another in the leave page and a third in
            # payroll — so the same green meant three different things.
            #
            # Late AND left early: the headline is lateness, but the rest is
            # not thrown away — it is in the tooltip, where somebody looking
            # into a particular row will find it.
            shift_status = row.get("shift_status") or row.get("status")
            notes = row.get("shift_notes") or []
            self._table.setCellWidget(i, 9, _badge_cell(
                shift_status,
                row.get("shift_label") or row.get("status_label") or "—",
                "Also: " + ", ".join(str(n) for n in notes) if notes else None))
        # Sized to the content, after it exists — see _fit_columns.
        _fit_columns(self._table, stretch=2)

    def _row_detail(self, item):
        """One shift, opened out — and the only place it can be corrected.

        THE CORRECTION LIVES HERE RATHER THAN IN A BUTTON ON THE ROW. A column
        of buttons beside fifty rows is a column of things to hit by accident,
        and this one rewrites the hours somebody is paid for. Two deliberate
        actions — open the row, then say why — is the right amount of friction
        for that.
        """
        row = self._attendance[item.row()] if item.row() < len(self._attendance) else None
        if not row:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Shift #{row.get('id')} — {row.get('employee_id')}")
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        facts = [
            ("Employee", f"{row.get('employee_name') or '—'}  ({row.get('employee_id')})"),
            ("Date", _fmt_date_only(row.get("login_time"))),
            ("Shift", row.get("shift_window") or "No shift set"),
            ("Check in", _fmt_ts(row.get("login_time"))),
            ("Check out", _fmt_ts(row.get("logout_time")) if row.get("logout_time")
                          else "Not signed out"),
            # .get, not [..]. A server that does not send elapsed_seconds is
            # not hypothetical — it is every server until this deploy lands,
            # and a KeyError here would take the whole dialog down.
            ("Hours",
             f"{_fmt_elapsed(row.get('elapsed_seconds'))}  (still running)"
             if row.get("attendance_status") == "active"
             and row.get("elapsed_seconds") is not None
             else self._format_total_hours(row.get("total_hours"))),
            ("Attendance", row.get("attendance_label") or "—"),
            ("Shift status", row.get("shift_label") or "—"),
        ]
        if row.get("shift_notes"):
            facts.append(("Also", ", ".join(str(n) for n in row["shift_notes"])))
        if row.get("leave_type"):
            facts.append(("Approved leave", str(row["leave_type"])))

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(7)
        for line, (name, value) in enumerate(facts):
            key = QLabel(name)
            key.setStyleSheet(
                f"color:{C['text_muted']};font-size:12px;background:transparent;")
            val = QLabel(str(value))
            val.setWordWrap(True)
            val.setStyleSheet(
                f"color:{C['text_primary']};font-size:12px;background:transparent;")
            grid.addWidget(key, line, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(val, line, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        note = QLabel(
            "Correcting the end time changes the hours this shift is paid for. "
            "The change, its previous value and your reason are written to the "
            "audit log.")
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color:{C['text_muted']};font-size:12px;background:transparent;")
        layout.addWidget(note)

        edit_row = QHBoxLayout()
        when = QLineEdit()
        when.setPlaceholderText("Leave empty to close it at this moment")
        when.setText("" if not row.get("logout_time")
                     else _parse_server_ts(row["logout_time"]).astimezone(IST)
                          .strftime("%Y-%m-%d %H:%M"))
        why = QLineEdit()
        why.setPlaceholderText("Reason (required)")
        edit_row.addWidget(QLabel("End (IST)"))
        edit_row.addWidget(when, 1)
        layout.addLayout(edit_row)
        layout.addWidget(why)

        message = QLabel("")
        message.setWordWrap(True)
        message.setStyleSheet(
            f"color:{C['warning']};font-size:12px;background:transparent;")
        layout.addWidget(message)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        apply_btn = _btn("Save end time", variant="primary", height=36, width=140)
        close_btn = _btn("Close", variant="secondary", height=36, width=90)
        close_btn.clicked.connect(dialog.reject)
        buttons.addWidget(close_btn)
        buttons.addWidget(apply_btn)
        layout.addLayout(buttons)

        def save():
            reason = why.text().strip()
            if len(reason) < 3:
                message.setText("A reason is required.")
                return
            apply_btn.setEnabled(False)
            message.setStyleSheet(
                f"color:{C['text_muted']};font-size:12px;background:transparent;")
            message.setText("Saving…")

            worker = _RequestWorker(
                "PATCH", f"{API_BASE_URL}/attendance/{row.get('id')}/checkout",
                {"logout_time": when.text().strip(), "reason": reason})

            def done(result):
                if result.get("success"):
                    dialog.accept()
                    self._load(self._page)
                else:
                    apply_btn.setEnabled(True)
                    message.setStyleSheet(
                        f"color:{C['danger']};font-size:12px;background:transparent;")
                    # The server's own words. It knows which rule was broken —
                    # repeating them here would let the two drift apart.
                    message.setText(result.get("message") or "That could not be saved.")

            worker.result.connect(done)
            worker.error.connect(lambda e: (apply_btn.setEnabled(True),
                                            message.setText(str(e))))
            _track_worker(self._workers, worker)
            worker.start()

        apply_btn.clicked.connect(save)
        dialog.exec()

    def _tick_running_clocks(self):
        """Advance the Hours cell of every shift that is still open.

        Touches only those cells. Redrawing the table would fight whatever
        the administrator is doing — a selection, a scroll position, a column
        being dragged — once a second, all day.
        """
        for i, row in enumerate(self._attendance):
            if row.get("attendance_status") != "active":
                continue
            if row.get("elapsed_seconds") is None:
                continue
            row["elapsed_seconds"] = row["elapsed_seconds"] + 1
            item = self._table.item(i, 7)
            # The table can be shorter than the data for a moment while a
            # page is being replaced.
            if item is not None:
                item.setText(_fmt_elapsed(row["elapsed_seconds"]))

    def _hours_for(self, row) -> str:
        """The shift's length — from total_hours, or from its own two ends.

        A ROW WITH A CHECK-IN, A CHECK-OUT AND A DASH FOR THE HOURS IS THE
        PAGE CALLING ITSELF A LIAR. It happens whenever total_hours was never
        filled in: rows written before the server started computing it, rows
        imported by hand, rows repaired directly in the database. The two
        timestamps are right there and their difference is not a guess — it
        is the same subtraction the server does.

        Still a dash for an open shift with no live session, because there
        genuinely is no end to subtract from.
        """
        formatted = self._format_total_hours(row.get("total_hours"))
        if formatted != "—":
            return formatted

        start = _parse_server_ts(row.get("login_time"))
        end = _parse_server_ts(row.get("logout_time"))
        if start is None or end is None or end < start:
            return "—"
        return _fmt_elapsed((end - start).total_seconds())

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
            self._export_btn.setText("Export CSV")
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
            self._export_btn.setText("Export CSV")
            QMessageBox.warning(self, "Export failed", str(e))

        w = _ExportWorker(f"{API_BASE_URL}/attendance/all", params, page_size=50)
        w.result.connect(_done)
        w.error.connect(_fail)
        _track_worker(self._workers, w)
        w.start()


class EmployeePage(QWidget):
    """One person, on a page — reached by clicking their name in the list.

    THIS WAS A MODAL. As a dialog it was built for one employee and thrown
    away, so looking at two people meant closing one and remembering it, and
    nothing on it could be held beside the list it came from.

    THE TIMERS ARE THE REASON THIS IS NOT A ONE-LINE CHANGE. The dialog ran a
    one-second clock and a ten-second refetch, and it was safe because the
    whole object was destroyed when the window closed. A page lives in the
    stack forever, so the same two timers would keep counting and keep
    fetching for somebody nobody is looking at — a request every ten seconds,
    for the rest of the session. They are started when the page is shown and
    stopped when it is hidden, which is the only difference in behaviour
    between this and the dialog it replaces.
    """

    back = Signal()
    page_title = "Employee"

    def __init__(self):
        super().__init__()
        self._employee: dict = {}

        self._workers: list[QThread] = []

        self._token_error_shown  = False
        self._live_active_seconds = 0
        self._live_idle_seconds   = 0
        self._live_state          = None   # "ACTIVE" | "IDLE" | None
        self._employee_online     = False

        self._build_ui()

        # Live timers (UI only). Backend values are fetched periodically.
        # NOT STARTED HERE — see showEvent. A page that is built at start-up
        # and never opened must not be polling the server.
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(1000)
        self._live_timer.timeout.connect(self._tick_live_times)

        self._details_refresh_timer = QTimer(self)
        self._details_refresh_timer.setInterval(10000)  # 10 seconds
        self._details_refresh_timer.timeout.connect(self._load_details)

    def load(self, employee: dict):
        """Show `employee`, from whatever the list already knows about them.

        The header is filled from the row that was clicked so the page is
        never blank while the details are fetched; the fetch then fills in
        what only the server knows.
        """
        self._employee = employee or {}
        # A NEW PERSON STARTS AT ZERO. These count up on the one-second timer,
        # and carrying them over would show the last employee's minutes under
        # this one's name.
        self._live_active_seconds = 0
        self._live_idle_seconds = 0
        self._live_state = None
        self._employee_online = False
        self._token_error_shown = False

        shown = (self._employee.get("full_name")
                 or self._employee.get("username") or "—")
        self._face.show_person(
            self._employee.get("employee_id"), str(shown))
        self._title.setText(str(shown))
        self._sub.setText(f"{self._employee.get('employee_id', '—')}  ·  "
                          f"{self._employee.get('username') or 'no login'}")
        self._role_pill.setText(
            str(self._employee.get("role", "—")).replace("_", " ").title())
        self._fill_profile(self._employee)
        for card in (self._active_time, self._idle_time,
                     self._shot_count, self._log_count):
            card.set_value("—")
        self._logs_table.setRowCount(0)
        self._load_details()

    def showEvent(self, event):
        super().showEvent(event)
        self._live_timer.start()
        self._details_refresh_timer.start()

    def hideEvent(self, event):
        """Stop counting and stop fetching for a page nobody is looking at."""
        self._live_timer.stop()
        self._details_refresh_timer.stop()
        super().hideEvent(event)




    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 22)
        root.setSpacing(16)

        header_card = _card()
        h_lay = QVBoxLayout(header_card)
        h_lay.setContentsMargins(20, 16, 20, 16)
        h_lay.setSpacing(4)

        # THE PERSON, NOT JUST THEIR NUMBERS.
        #
        # This dialog used to open on a username, a role pill, an id and four
        # stat cards. Everything the company actually records about somebody —
        # their name, photo, phone, email, department, who they report to,
        # when they joined — existed on the record and had no screen here at
        # all, so the only way to look somebody up was to read the database.
        back_row = QHBoxLayout()
        back = _btn("Back to employees", variant="secondary", height=34)
        back.setIcon(_icons.icon("chevron-left", 14, C["text_muted"]))
        back.clicked.connect(self.back.emit)
        back_row.addWidget(back)
        back_row.addStretch()
        root.addLayout(back_row)

        name_row = QHBoxLayout()
        name_row.setSpacing(12)

        # BUILT EMPTY AND FILLED IN load(). As a dialog these were built from
        # the employee it was constructed with; the page is built once and
        # shown for whoever is clicked, so every part of it that names a
        # person is an attribute rather than a local.
        self._face = Avatar(52)
        name_row.addWidget(self._face)

        who = QVBoxLayout()
        who.setSpacing(2)
        # The NAME first, with the login username under it. Every other part
        # of the product shows people by name; this used to lead with the
        # login name, so a person read as two different accounts across two
        # screens.
        self._title = QLabel("—")
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        self._title.setStyleSheet(f"color:{C['text_primary']}; font-size:18px; font-weight:700; background:transparent;")
        who.addWidget(self._title)
        self._sub = QLabel("—")
        self._sub.setTextFormat(Qt.TextFormat.PlainText)
        self._sub.setStyleSheet(f"color:{C['text_secondary']}; font-size:12px; background:transparent;")
        who.addWidget(self._sub)
        name_row.addLayout(who)

        name_row.addStretch()
        self._role_pill = QLabel("—")
        self._role_pill.setStyleSheet(
            f"background:{C['accent_soft']}; color:{C['accent_hover']}; padding:4px 12px; "
            f"border-radius:{Radius.CHIP}px; font-size:12px; font-weight:700;"
        )
        name_row.addWidget(self._role_pill)
        h_lay.addLayout(name_row)
        h_lay.addSpacing(10)

        # Two columns of label/value, in the same order My Profile uses — an
        # admin and the employee should be reading the same page about the
        # same person, not two different arrangements of it.
        details = QGridLayout()
        details.setHorizontalSpacing(18)
        details.setVerticalSpacing(6)
        self._profile_rows = {}
        FIELDS = [
            ("Email",             "email"),
            ("Phone",             "phone"),
            ("Designation",       "designation"),
            ("Department",        "department"),
            ("Reporting manager", "reporting_manager"),
            ("Joining date",      "joining_date"),
        ]
        for index, (caption, key) in enumerate(FIELDS):
            row, column = index % 3, (index // 3) * 2
            label = QLabel(caption)
            label.setStyleSheet(
                f"color:{C['text_muted']}; font-size:12px; background:transparent;")
            value = QLabel("—")
            # PLAIN TEXT. These are values somebody typed, and a QLabel
            # renders HTML by default — a name written as markup would be
            # drawn as markup on the admin's screen.
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setStyleSheet(
                f"color:{C['text_primary']}; font-size:12px; font-weight:600;"
                "background:transparent;")
            details.addWidget(label, row, column)
            details.addWidget(value, row, column + 1)
            self._profile_rows[key] = value
        details.setColumnStretch(1, 1)
        details.setColumnStretch(3, 1)
        h_lay.addLayout(details)

        root.addWidget(header_card)

        stats_grid = QGridLayout()
        stats_grid.setSpacing(14)
        self._active_time = StatCard("Active Time",   ACCENTS["green"],  "⏱")
        self._idle_time   = StatCard("Idle Time",      ACCENTS["amber"], "")
        self._shot_count  = StatCard("Screenshots",    ACCENTS["violet"], "")
        self._log_count   = StatCard("Activity Logs",  ACCENTS["cyan"],"")
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
        self._logs_table.setAlternatingRowColors(False)  # zebra striping competes with the data
        self._logs_table.setShowGrid(False)
        self._logs_table.verticalHeader().setVisible(False)
        root.addWidget(self._logs_table, 1)

    def _fill_profile(self, employee: dict):
        """Put what is known on screen; a dash where nothing is recorded.

        A dash here means the field is genuinely empty on the record — it is
        not a loading state and not a failure. The fields it fills only
        started arriving from the employee list once the list query carried
        them, which is why every one of them read "—" before.
        """
        for key, label in getattr(self, "_profile_rows", {}).items():
            value = employee.get(key)
            if key == "reporting_manager":
                # The name if the server resolved one, the id if it could
                # not — an id is a poor answer but it is still an answer,
                # and it is what somebody would search for.
                value = employee.get("reporting_manager_name") or value
            if key == "joining_date" and value:
                value = str(value)[:10]        # a date, not a timestamp
            label.setText(str(value) if value else "—")

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

    def stop(self):
        """Stop everything this page is running.

        The panel calls this on shutdown through TAB_ATTRS. hideEvent already
        stops the timers when the page is navigated away from; this is the
        harder case — the window is going away, and a running QThread whose
        object is then destroyed is not an exception, it is std::terminate.
        """
        self._live_timer.stop()
        self._details_refresh_timer.stop()
        for w in self._workers:
            w.quit()
            w.wait(1000)


class _NameLink(QLabel):
    """Somebody's name, which opens their page when it is clicked.

    A LABEL RATHER THAN A BUTTON. A button in every row of a list draws a
    border and a fill around each name and the table stops reading as a
    table; what is wanted is the name itself being the thing you press, the
    way it is in the products this list is modelled on. So it is a label that
    takes the accent colour and a hand cursor, which is what tells somebody
    it can be pressed before they try it.
    """

    clicked = Signal()

    def __init__(self, text: str):
        super().__init__(text)
        # PLAIN TEXT. This is a name somebody typed, and a QLabel renders
        # HTML by default — a name written as mark-up would be drawn as
        # mark-up on the admin's screen.
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"color:{C['text_primary']};font-size:13px;font-weight:600;"
            f"background:transparent;border:none;")

    def enterEvent(self, event):
        self.setStyleSheet(
            f"color:{C['accent']};font-size:13px;font-weight:600;"
            f"background:transparent;border:none;")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet(
            f"color:{C['text_primary']};font-size:13px;font-weight:600;"
            f"background:transparent;border:none;")
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


def _person_cell(employee: dict, on_click) -> QWidget:
    """The avatar, the name, and the id and job title under it.

    THREE FACTS IN THE WIDTH OF ONE COLUMN. The list used to spend a column
    each on the id and the name and had nowhere to put the designation at
    all, so "who are the QA people" could not be answered by looking. Stacked,
    the name leads and the two things that identify it sit under it in the
    muted weight — which is also how the product's other lists read.
    """
    holder = _clear_bg(QWidget())
    row = QHBoxLayout(holder)
    row.setContentsMargins(8, 4, 8, 4)
    row.setSpacing(10)

    face = Avatar(32)
    face.show_person(employee.get("employee_id"),
                     employee.get("full_name") or employee.get("username") or "")
    row.addWidget(face)

    column = QVBoxLayout()
    column.setSpacing(1)
    name = _NameLink(str(employee.get("full_name")
                         or employee.get("username") or "—"))
    name.clicked.connect(on_click)
    column.addWidget(name)

    # The id first — it is what reports and searches use — then the job
    # title, which is what somebody is actually looking for when they scan.
    parts = [str(employee.get("employee_id") or "—")]
    if employee.get("designation"):
        parts.append(str(employee["designation"]))
    under = QLabel("  ·  ".join(parts))
    under.setTextFormat(Qt.TextFormat.PlainText)
    under.setStyleSheet(
        f"color:{C['text_muted']};font-size:{Type.MICRO}px;"
        f"background:transparent;border:none;")
    column.addWidget(under)
    row.addLayout(column)
    row.addStretch()
    return holder


def _avatar_request(employee_id, on_ready):
    from client.presentation.widgets.avatar import request as _request
    _request(employee_id, on_ready)


def _set_row_face(cell, data):
    """Put a photo on a table row, if the row is still there.

    The request is in the air while the table can be rebuilt under it — a
    refresh, a search, a page change — and drawing onto a deleted cell is a
    hard crash rather than an exception.
    """
    from client.presentation.widgets.avatar import round_pixmap
    pixmap = round_pixmap(data, 26)
    if pixmap is None:
        return
    try:
        cell.setIcon(QIcon(pixmap))
    except RuntimeError:
        pass


class _EmployeesTab(QWidget):
    # The tab says "somebody wants to add a person"; the panel owns the stack
    # and decides where that goes. A tab reaching into the stack by index is
    # what made five Quick Actions open the wrong page once already.
    open_add_employee = Signal()
    # The whole row, not just an id: the page fills its header from what the
    # list already knows so it is never blank while the details are fetched.
    open_employee = Signal(dict)

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
        self._search_input.setPlaceholderText("Search by name, Employee ID, username or role")
        self._search_input.setFixedHeight(38)
        self._search_input.textChanged.connect(self._on_search_changed)
        header.addWidget(self._search_input, 1)

        self._role_summary = QLabel("")
        self._role_summary.setStyleSheet(
            f"color:{C['text_secondary']};font-size:12px;font-weight:600;background:transparent;"
        )

        self._export_btn = _btn("Export CSV", variant="secondary", height=38, width=140)
        self._export_btn.clicked.connect(self._export_employees_csv)
        header.addWidget(self._export_btn)

        add_btn = _btn("+  Add Employee", variant="primary", height=38, width=160)
        add_btn.clicked.connect(self._add_employee)
        header.addWidget(add_btn)

        root.addLayout(header)

        # ── the filters ─────────────────────────────────────────────────
        #
        # EVERY ONE OF THESE IS SENT TO THE SERVER. Narrowing the fifty rows
        # already on screen answers "which of these fifty are in Design",
        # which is not the question, and would leave the count underneath
        # reading the unfiltered total.
        filters = QHBoxLayout()
        filters.setSpacing(8)
        filters.setContentsMargins(2, 0, 2, 0)

        self._role_filter = QComboBox()
        self._role_filter.setFixedHeight(34)
        for value, shown in (("", "All roles"), ("employee", "Employees"),
                             ("admin", "Admins"), ("super_admin", "Super admins")):
            self._role_filter.addItem(shown, value)
        self._role_filter.currentIndexChanged.connect(lambda _i: self._load_employees(1))
        filters.addWidget(self._role_filter)

        self._dept_filter = QComboBox()
        self._dept_filter.setFixedHeight(34)
        self._dept_filter.setMinimumWidth(150)
        self._dept_filter.addItem("All departments", "")
        self._dept_filter.currentIndexChanged.connect(lambda _i: self._load_employees(1))
        filters.addWidget(self._dept_filter)

        self._status_filter = QComboBox()
        self._status_filter.setFixedHeight(34)
        # ACTIVE AND SUSPENDED, NOT ONLINE AND OFFLINE. Suspension is a
        # decision somebody made and is a column; online is live and changes
        # minute to minute, so it is shown as a chip and not filtered on —
        # a list that empties as you read it is not a list.
        for value, shown in (("", "Active and suspended"), ("active", "Active"),
                             ("suspended", "Suspended")):
            self._status_filter.addItem(shown, value)
        self._status_filter.currentIndexChanged.connect(lambda _i: self._load_employees(1))
        filters.addWidget(self._status_filter)

        self._clear_filters = _btn("Clear", variant="ghost", height=34, width=80)
        self._clear_filters.clicked.connect(self._reset_filters)
        filters.addWidget(self._clear_filters)

        filters.addStretch()
        filters.addWidget(self._role_summary)
        root.addLayout(filters)

        self._table = _tune_table(QTableWidget(0, 6))
        self._table.setHorizontalHeaderLabels([
            "Employee", "Work email", "Department", "Role", "Status", "Actions"
        ])

        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(False)
        # THE PERSON COLUMN IS FIXED AND THE EMAIL ONE STRETCHES, not the
        # other way round.
        #
        # It was the reverse, and the reverse collapses. A Stretch column is
        # the one that absorbs whatever is left over — so on a window narrower
        # than the fixed columns add up to, the person column took the whole
        # shortfall: measured at 112px against the 197px the avatar, name and
        # "id · designation" actually need. The names were shredded around the
        # avatar and the header read "EMPLO'". The identity column is the last
        # thing that should give way.
        #
        # So the widths below are floors, and Work email — the one field that
        # degrades gracefully, because a clipped address is still recognisable
        # — takes the slack instead. Past that the table scrolls sideways,
        # which is the same answer the payroll table reached: pretending
        # columns fit is what cut "Late minutes" in half.
        #
        # 5 IS 250: it holds an 88px View, a 126px Manage and the spacing and
        # margins between them, which came to more than the 220 it had — so
        # "Manage" was drawn clipped down its left edge.
        hdr.setMinimumSectionSize(110)
        widths = {0: 300, 2: 150, 3: 120, 4: 120, 5: 250}
        for col, w in widths.items():
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self._table.setColumnWidth(col, w)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setHorizontalScrollMode(
            QTableWidget.ScrollMode.ScrollPerPixel)

        # TALL ENOUGH FOR A FACE AND TWO LINES. The person column stacks the
        # name over the id and job title beside a 32px avatar; the old 52px
        # row cropped the second line to a band.
        self._table.verticalHeader().setDefaultSectionSize(60)

        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # Double-clicking a row opens the person too — the name is the
        # advertised way in, but a row that does nothing when double-clicked
        # reads as broken to anybody used to the payroll table.
        self._table.cellDoubleClicked.connect(self._open_row)
        root.addWidget(self._table, 1)

        pag_row = QHBoxLayout()
        pag_row.setSpacing(8)
        self._prev_btn = _btn("Prev", variant="secondary", height=36, width=96)
        self._prev_btn.setIcon(_icons.icon("chevron-left", 14, C["text_primary"]))
        self._prev_btn.clicked.connect(self._prev_page)
        self._next_btn = _btn("Next", variant="secondary", height=36, width=96)
        self._next_btn.setIcon(_icons.icon("chevron-right", 14, C["text_primary"]))
        self._next_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._next_btn.clicked.connect(self._next_page)
        self._page_label = _muted_label("Page 1")
        pag_row.addWidget(self._prev_btn)
        pag_row.addWidget(self._page_label)
        pag_row.addWidget(self._next_btn)
        pag_row.addStretch()

        pag_row.addWidget(_muted_label("Show"))
        self._per_page = QComboBox()
        self._per_page.setFixedHeight(36)
        self._per_page.setFixedWidth(80)
        for size in (25, 50, 100, 200):
            self._per_page.addItem(str(size), size)
        self._per_page.setCurrentIndex(1)          # 50, what it has always been
        # BACK TO PAGE ONE. Showing a hundred per page while standing on page
        # four of a fifty-per-page list lands past the end of the results, and
        # an empty table reads as "there is nobody" rather than "you are past
        # the last page".
        self._per_page.currentIndexChanged.connect(lambda _i: self._load_employees(1))
        pag_row.addWidget(self._per_page)
        root.addLayout(pag_row)

    def _reset_filters(self):
        """Everything back to unfiltered, in one request rather than three."""
        for box in (self._role_filter, self._dept_filter, self._status_filter):
            box.blockSignals(True)
            box.setCurrentIndex(0)
            box.blockSignals(False)
        self._search_input.blockSignals(True)
        self._search_input.clear()
        self._search_input.blockSignals(False)
        self._search_text = ""
        self._load_employees(1)

    def _open_row(self, row: int, _column: int = 0):
        if 0 <= row < len(self._rows):
            self._open_details(self._rows[row])

    def _role_label(self, role: str) -> str:
        r = (role or "").lower()
        if r == "super_admin":
            return " Super Admin"
        if r == "admin":
            return " Admin"
        return "Employee"

    def _status_to_text_color(self, status: str):
        s = (status or "").lower()
        if s in ("online", "online_user"):
            return " Online", C["success"]
        if s in ("idle", "idling"):
            return " Idle", C["warning"]
        if s in ("offline", "logged_out", "disconnected"):
            return " Offline", C["danger"]
        return f"{status}", C["text_secondary"]

    def _load_employees(self, page: int | None = None):
        # SCALE FIX: pehle SAARE employees ek saath aate the aur search
        # client-side hota tha. 1000–10,000 employees pe wo request 55–117
        # second leti thi (measured) aur har 5s chalti thi. Ab server-side
        # pagination + search.
        if page is not None:
            self._page = max(1, page)
        params = {"page": self._page, "limit": self._page_size()}
        if self._search_text:
            params["search"] = self._search_text
        # The filters go to the server with everything else — see the note on
        # the filter row about why narrowing the page is the wrong answer.
        for key, box in (("role", "_role_filter"),
                         ("department", "_dept_filter"),
                         ("status", "_status_filter")):
            widget = getattr(self, box, None)
            value = widget.currentData() if widget is not None else ""
            if value:
                params[key] = value
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
            # SEPARATED. These three were written as adjacent f-strings with
            # nothing between them, so the line read
            # "1/3 super admins1/20 admins3 employees" — three facts run into
            # one number-looking string, on the row that exists to tell an
            # admin whether there is room to add anybody.
            self._role_summary.setText(
                f"{supers}/{s_max} super admins"
                f"   ·   {admins}/{a_max} admins"
                f"   ·   {emps} employees"
            )
            self._role_summary.setStyleSheet(
                f"color:{C['warning'] if near else C['text_secondary']};"
                f"font-size:12px;font-weight:600;background:transparent;"
            )
        except Exception as error:
            print("[EMPLOYEES] role summary:", error)
        # THE DEPARTMENTS THE COMPANY HAS, not the ones on this page. Kept in
        # step with the server's list, and the current choice is preserved —
        # rebuilding the box would otherwise reset the filter on every
        # thirty-second refresh, and the list would silently widen under
        # somebody who had narrowed it.
        departments = (data or {}).get("departments")
        if isinstance(departments, list) and hasattr(self, "_dept_filter"):
            chosen = self._dept_filter.currentData()
            if [self._dept_filter.itemData(i)
                    for i in range(self._dept_filter.count())][1:] != departments:
                self._dept_filter.blockSignals(True)
                self._dept_filter.clear()
                self._dept_filter.addItem("All departments", "")
                for name in departments:
                    self._dept_filter.addItem(str(name), str(name))
                index = self._dept_filter.findData(chosen)
                self._dept_filter.setCurrentIndex(max(0, index))
                self._dept_filter.blockSignals(False)

        size = self._page_size()
        pages = max(1, -(-self._total // size)) if self._total else 1
        self._page_label.setText(
            f"Page {self._page} of {pages}  •  {self._total} "
            f"{'person' if self._total == 1 else 'people'}")
        self._prev_btn.setEnabled(self._page > 1)
        self._next_btn.setEnabled(self._page * size < self._total)
        self._display_employees(self._rows)

    def _page_size(self) -> int:
        box = getattr(self, "_per_page", None)
        return int(box.currentData()) if box is not None else 50

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
        # Name as its own column. A CSV of usernames is handed to somebody in
        # HR or payroll who has never seen a login name in their life.
        # THE COLUMNS THAT ARE ON SCREEN. A CSV that does not carry what the
        # page shows sends somebody back to the panel to read the two things
        # it left out — and the work email and department are exactly what an
        # export of an employee list is wanted for.
        headers = ["Employee ID", "Name", "Username", "Work email", "Department",
                   "Designation", "Role", "Status", "Last Seen (IST)"]
        rows = []
        for emp in filtered:
            if emp.get("suspended"):
                status_text = "Suspended"
            else:
                status_text, _ = self._status_to_text_color(emp.get('status'))
            rows.append([
                emp.get('employee_id', ''),
                emp.get('full_name', '') or '',
                emp.get('username', '') or '',
                emp.get('email', '') or '',
                emp.get('department', '') or '',
                emp.get('designation', '') or '',
                emp.get('role', ''),
                str(status_text).strip(),
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

            # BUG FIX: server naive UTC string bhejta hai
            # ("2026-08-02 16:00:00"). Pehle usko `datetime.now(timezone.utc)`
            # se subtract kiya jaata tha -> TypeError (naive vs aware) -> except
            # -> raw timestamp dikh jaata tha. "Just now"/"5 min ago" kabhi
            # dikhta hi nahi tha.
            last_seen = _fmt_relative(emp.get("last_seen"))

            # ── the person: face, name, and what identifies them ────────
            #
            # THE NAME LEADS. Every other part of the product shows people by
            # their full name — chat, reports, the audit log — and this list
            # led with the login username, so one account read as "Priya Nair"
            # in a conversation and "manager" here, and an admin who had just
            # read a message from her could not find her in her own list.
            #
            # A WIDGET IN THIS CELL, deliberately, where the rest of the row
            # is plain items: it holds three facts in two weights and a
            # clickable name, none of which an item can do. It is the same
            # trade the status chip already makes.
            self._table.setCellWidget(
                i, 0, _person_cell(emp, lambda e=emp: self._open_details(e)))

            # THE FACE. Asked for in one line — "photo agar employee lagayega
            # to sab jagah dikhna chahiye like instagram". Avatar draws the
            # initials itself and fetches the photograph for those who have
            # uploaded one; nobody gets a placeholder photograph.
            self._table.setItem(i, 1, _cell(str(emp.get("email") or "—"),
                                            muted=not emp.get("email")))
            self._table.setItem(i, 2, _cell(str(emp.get("department") or "—"),
                                            muted=not emp.get("department")))
            self._table.setItem(i, 3, _cell(self._role_label(role)))

            # ── the chip ────────────────────────────────────────────────
            #
            # SUSPENDED OUTRANKS ONLINE. A suspended account cannot sign in,
            # so "Online" against one is not a state it can be in — and it is
            # the fact somebody is looking for when they scan this column.
            # The last-seen time moves into the tooltip: it is the answer to a
            # question asked about one person, not something scanned down a
            # list, and it was costing a column.
            if emp.get("suspended"):
                status_key, status_text = "suspended", "Suspended"
            else:
                status_key = str(emp.get("status") or "offline").lower()
                status_text = "Online" if status_key == "online" else "Offline"
            self._table.setCellWidget(
                i, 4, _badge_cell(status_key, status_text,
                                  tooltip=(None if status_key == "online"
                                           else f"Last seen {last_seen}")))

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

            view_btn = _btn("View", variant="secondary", height=36, width=88)
            view_btn.clicked.connect(lambda _=False, e=emp: self._open_details(e))

            # 126, NOT 108. "Manage  ▾" plus the 16px of padding either side
            # needs more than 108 gave it, so Qt drew the label clipped down
            # its left edge — it read as "лanage", which looks like a font
            # fault rather than a button that is too narrow.
            manage_btn = _btn("Manage  ▾", variant="secondary", height=36, width=126)
            menu = QMenu(manage_btn)
            menu.setStyleSheet(
                f"QMenu{{background:{C['bg_surface']};border:1px solid {C['border']};"
                f"border-radius:12px;padding:6px;color:{C['text_primary']};}}"
                "QMenu::item{padding:8px 18px;border-radius:12px;font-size:13px;}"
                f"QMenu::item:selected{{background:{C['accent']};color:#ffffff;}}"
                f"QMenu::item:disabled{{color:{C['text_muted']};}}"
                f"QMenu::separator{{height:1px;background:{C['border']};margin:5px 4px;}}"
            )

            def add_action(label, slot, enabled=True, tip=""):
                act = menu.addAction(label)
                act.setEnabled(enabled)
                if tip:
                    act.setToolTip(tip)
                if enabled:
                    act.triggered.connect(slot)
                return act

            # Editing the name, first because it is the one people look for.
            # Every account created before this dialog asked for a name took
            # the login username instead, and there was no way at all to fix
            # one afterwards.
            can_rename = i_am_super or (not tgt_super and (not tgt_admin or is_self))
            add_action(
"️  Edit name",
                lambda _=False, e=emp: self._edit_profile(e),
                enabled=can_rename,
                tip="" if can_rename else (
                    "Only a super admin can manage this account." if tgt_super
                    else "Admins cannot modify other admin accounts."),
            )
            menu.addSeparator()

            # Verbose logging — super admin ko koi aur nahi chhoo sakta;
            # admin doosre admin ka config nahi badal sakta.
            verbose_on = bool(emp.get("verbose_logging"))
            can_config = i_am_super or (not tgt_super and (not tgt_admin or is_self))
            add_action(
"Turn verbose logging OFF" if verbose_on
                else "Turn verbose logging ON",
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

            # Suspend / restore.
            #
            # Shown to every admin so the capability is discoverable, and
            # enabled only where the hierarchy allows it — an admin sees the
            # entry on another admin's row but cannot use it, with the reason
            # in the tooltip rather than the item silently missing.
            #
            # `can_config` is the same rule the server enforces: admins may
            # act on employees and on themselves, super admins on anyone.
            suspended = bool(emp.get("suspended"))
            can_suspend = can_config and not is_self
            suspend_tip = ""
            if is_self:
                suspend_tip = "You cannot suspend your own account."
            elif not can_config:
                suspend_tip = ("Only a super admin can manage this account."
                               if tgt_super else
                               "Admins cannot suspend other admins — ask a super admin.")
            add_action(
                "Unsuspend account" if suspended else "Suspend account",
                lambda _=False, e=emp, now=suspended: self._set_suspended(e, not now),
                enabled=can_suspend,
                tip=suspend_tip,
            )

            # Reset password — same rule as any other write on the account
            # (`can_config`), which is what the server enforces too. An admin
            # resetting another admin's password would be a way to become
            # them, so the server refuses it and the menu greys it out.
            add_action(
"Reset password",
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
                    add_action("Remove super admin",
                               lambda _=False, e=emp: self._change_role(e, "admin"))
                else:
                    add_action(
"Make employee" if tgt_admin else "Make admin",
                        lambda _=False, e=emp: self._change_role(e),
                    )
                    if tgt_admin:
                        # POWER TRANSFER — super admin apni power kisi doosre
                        # admin ko de sakta hai; isi ke baad wo khud hat sakta hai.
                        add_action("Make super admin",
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
            add_action("Delete employee",
                       lambda _=False, e=emp: self._delete_employee(e),
                       enabled=can_delete, tip=delete_tip)

            manage_btn.setMenu(menu)
            actions_layout.addWidget(view_btn)
            actions_layout.addWidget(manage_btn)
            actions_layout.addStretch()

            self._table.setCellWidget(i, 5, actions_widget)

    def _open_details(self, emp: dict):
        """Ask the panel to open this person's page.

        This used to build EmployeeDetailsDialog and exec() it. The tab says
        who; the panel owns the stack and decides where that goes.
        """
        if emp:
            self.open_employee.emit(emp)

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

        def logged_out(d):
            QMessageBox.information(
                self, "Force Logout",
                "Force logout set!" if d.get("success") else f"{d.get('error')}")
            # AND RE-READ THE LIST. Without this the row went on saying
            # "Online" for somebody who had just been signed out — the change
            # only appeared if you left the page and came back, which is how
            # it was reported. The server was right the whole time; the
            # screen was the last answer anybody had asked for.
            self._load_employees()

        w.result.connect(logged_out)
        w.error.connect(lambda e: QMessageBox.warning(self, "Error", f"Force logout failed: {e}"))
        _track_worker(self._workers, w)
        w.start()
        
    def _set_suspended(self, emp: dict, suspend: bool):
        emp_id = emp.get("employee_id")
        username = emp.get("username", emp_id)
        if not emp_id:
            return

        if suspend:
            question = (
                f"Suspend {username}?\n\n"
                "They will be signed out immediately and cannot sign in again "
                "until an administrator restores the account. Force logout on "
                "its own does not do this — they could simply sign back in."
            )
        else:
            question = f"Restore {username}?\n\nThey will be able to sign in again."

        reply = QMessageBox.question(
            self, "Suspend account" if suspend else "Restore account", question,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        w = _PostWorker(f"{API_BASE_URL}/admin/employees/{emp_id}/suspend",
                        {"suspended": suspend})

        def done(result: dict):
            if result.get("success"):
                # Reload so the row — and the menu entry's label — reflect the
                # new state. A button that still says "Suspend" after
                # suspending is how people end up doing it twice.
                self._load_employees()
            else:
                QMessageBox.warning(self, "Could not change the account",
                                    result.get("message", "The server refused."))

        w.result.connect(done)
        w.error.connect(lambda e: QMessageBox.warning(
            self, "Could not change the account", f"Could not reach the server: {e}"))
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

            # Shown exactly once — the server stores only the bcrypt hash, so
            # there is no way to look this up again afterwards. That makes
            # getting it OUT of this dialog the whole job.
            #
            # BUG this fixes: the password sat in setInformativeText with
            # TextSelectableByMouse set on the box. That flag applies to the
            # main text, and informative text is not selectable on Windows —
            # so the one string that cannot be recovered could not be copied
            # or even highlighted. It had to be retyped by eye.
            #
            # Now it is in a read-only field that selects everything on
            # focus, with a button that puts it straight on the clipboard.
            dialog = QDialog(self)
            dialog.setWindowTitle("Password reset")
            dialog.setMinimumWidth(460)
            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(24, 22, 24, 20)
            layout.setSpacing(14)

            heading = QLabel(f"Temporary password for <b>{username}</b>")
            heading.setStyleSheet(
                f"color:{C['text_primary']}; font-size:13px; background:transparent;"
            )
            layout.addWidget(heading)

            field = QLineEdit(temporary)
            field.setReadOnly(True)
            field.setFixedHeight(40)
            field.setAlignment(Qt.AlignmentFlag.AlignCenter)
            field.setObjectName("tempPwd")
            field.setStyleSheet(
                f"#tempPwd {{ background:{C['bg_elevated']}; color:{C['text_primary']};"
                f" border:1px solid {C['accent']}; border-radius:12px;"
                f" font-family:'SF Mono','Menlo','Consolas',monospace;"
                f" font-size:16px; letter-spacing:1px; }}"
            )
            field.selectAll()
            layout.addWidget(field)

            note = QLabel(
                "Give this to them directly. It is shown only now and cannot be "
                "recovered later — if it is lost, reset the password again. They "
                "will be asked to choose their own as soon as they sign in with it."
            )
            note.setWordWrap(True)
            note.setStyleSheet(
                f"color:{C['text_muted']}; font-size:12px; background:transparent;"
            )
            layout.addWidget(note)

            buttons = QHBoxLayout()
            buttons.addStretch()
            copy_btn = _btn("Copy", variant="primary", height=36)
            done_btn = _btn("Done", variant="secondary", height=36)

            def copy_it():
                QApplication.clipboard().setText(temporary)
                copy_btn.setText("Copied")
                # Back to normal so a second copy is obviously possible.
                QTimer.singleShot(1800, lambda: copy_btn.setText("Copy"))

            copy_btn.clicked.connect(copy_it)
            done_btn.clicked.connect(dialog.accept)
            buttons.addWidget(copy_btn)
            buttons.addWidget(done_btn)
            layout.addLayout(buttons)

            dialog.setStyleSheet(f"QDialog {{ background:{C['bg_surface']}; }}")
            field.setFocus()
            dialog.exec()

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
            else QMessageBox.warning(self, "Error", f"{d.get('error', 'Toggle failed')}")
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
            extra = ("\n\n️  A super administrator has full access. "
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

        
    def _edit_profile(self, emp: dict):
        """Change the name somebody is shown by.

        Old messages keep the old name. Chat stamps sender_name onto each
        message when it is sent, so renaming today does not rewrite what
        somebody was called last year — which is what makes the archive a
        record rather than a view of the present.
        """
        emp_id = emp.get("employee_id", "")
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Edit — {emp_id}")
        dlg.setMinimumWidth(380)
        dlg.setStyleSheet(f"QDialog {{ background: {C['bg_surface']}; }}")

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(8)

        name = QLineEdit(str(emp.get("full_name") or ""))
        role_text = QLineEdit(str(emp.get("designation") or ""))

        layout.addWidget(_muted_label("Full name"))
        layout.addWidget(name)
        layout.addSpacing(6)
        layout.addWidget(_muted_label("Designation  (optional)"))
        layout.addWidget(role_text)
        layout.addSpacing(6)

        # WHERE THE DASHES CAME FROM.
        #
        # Department, reporting manager and joining date have existed on the
        # employee record and in the API since My Profile shipped — with no
        # form anywhere that could set them. So every profile showed "—" on
        # three lines for ever, and it read as a page that had not loaded.
        #
        # Only a super admin, which is the rule the server already enforces:
        # these describe somebody's place in the company, and an ordinary
        # admin moving people between departments or changing who they report
        # to is an organisational change, not an administrative one.
        # Contact details — any admin, because onboarding somebody is what an
        # admin does. The employee can change these on their own page too;
        # this is for the day they are set up, before they have signed in.
        email = QLineEdit(str(emp.get("email") or ""))
        email.setPlaceholderText("name@company.com")
        phone = QLineEdit(str(emp.get("phone") or ""))
        phone.setPlaceholderText("+91 98765 43210")
        layout.addWidget(_muted_label("Email  (optional)"))
        layout.addWidget(email)
        layout.addSpacing(6)
        layout.addWidget(_muted_label("Phone  (optional)"))
        layout.addWidget(phone)
        layout.addSpacing(6)

        i_am_super = getattr(SessionManager, "role", "") == "super_admin"
        department = QLineEdit(str(emp.get("department") or ""))
        manager = QComboBox()
        joining = _optional_date(QDate(1970, 1, 1), unset_text="Not set")

        if i_am_super:
            layout.addWidget(_muted_label("Department  (optional)"))
            layout.addWidget(department)
            layout.addSpacing(6)

            # A LIST, NOT A TYPED ID. A manager is another employee, and a
            # typed identifier is a typo away from pointing at nobody — which
            # the profile would then show as a blank line with no way to tell
            # whether it was unset or wrong.
            manager.addItem("— none —", "")
            # The people on the page in front of the admin. The list is
            # paginated, so a manager on another page is reached by searching
            # for them first — which is how this table is used anyway, and is
            # better than loading every employee in the company to fill a
            # dropdown that is opened rarely.
            for other in getattr(self, "_rows", []) or []:
                other_id = str(other.get("employee_id") or "")
                if not other_id or other_id == emp_id:
                    continue          # nobody reports to themselves
                label = str(other.get("full_name") or other.get("username") or other_id)
                manager.addItem(f"{label}  ·  {other_id}", other_id)
            current_manager = str(emp.get("reporting_manager") or "")
            index = manager.findData(current_manager)
            manager.setCurrentIndex(index if index >= 0 else 0)
            layout.addWidget(_muted_label("Reporting manager  (optional)"))
            layout.addWidget(manager)
            layout.addSpacing(6)

            existing = str(emp.get("joining_date") or "")[:10]
            parsed = QDate.fromString(existing, "yyyy-MM-dd") if existing else QDate()
            joining.setDate(parsed if parsed.isValid() else joining.minimumDate())
            layout.addWidget(_muted_label("Joining date  (optional)"))
            layout.addWidget(joining)
            layout.addSpacing(6)

        note = QLabel(
            f"Login username stays {emp.get('username', '')}. This changes how "
            "they appear in chat, reports and the audit log from now on — "
            "messages already sent keep the name they were sent under.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{C['text_muted']};font-size:12px;background:transparent;")
        layout.addWidget(note)
        layout.addSpacing(12)

        save_btn = _btn("Save", variant="primary", height=40)
        layout.addWidget(save_btn)

        def submit():
            typed = name.text().strip()
            if not typed:
                QMessageBox.warning(
                    self, "Name needed",
                    "A name is required. Leaving it empty would put this "
                    "account back to showing its login username.")
                return
            typed_email = email.text().strip()
            if typed_email and not re.match(
                    r"^[^\s@]+@[^\s@]+\.[^\s@]+$", typed_email):
                QMessageBox.warning(
                    self, "Check the email",
                    "That does not look like an email address. Leave it empty "
                    "if you do not have one yet.")
                return
            payload = {"full_name": typed,
                       "designation": role_text.text().strip(),
                       "email": typed_email,
                       "phone": phone.text().strip()}
            if i_am_super:
                payload["department"] = department.text().strip()
                payload["reporting_manager"] = manager.currentData() or ""
                # The minimum date is what "Not set" shows as, so it means
                # empty rather than 1 January 1970 — a date that would
                # otherwise be saved as somebody's first day at work.
                payload["joining_date"] = (
                    "" if joining.date() == joining.minimumDate()
                    else joining.date().toString("yyyy-MM-dd"))
            worker = _PostWorker(
                f"{API_BASE_URL}/admin/employees/{emp_id}/profile", payload)

            def done(data):
                if data.get("success"):
                    dlg.accept()
                    self._load_employees()
                else:
                    QMessageBox.warning(
                        self, "Could not save",
                        data.get("message") or "Unknown error")

            worker.result.connect(done)
            worker.error.connect(
                lambda e: QMessageBox.warning(self, "Could not save", str(e)))
            _track_worker(self._workers, worker)
            worker.start()

        save_btn.clicked.connect(submit)
        name.returnPressed.connect(submit)
        dlg.exec()

    def _add_employee(self):
        """Ask the panel to open the Add Employee PAGE.

        This used to build the whole form here — six fields in a 380px dialog
        that validated one at a time through message boxes. See
        _AddEmployeePage for what replaced it and why.
        """
        self.open_add_employee.emit()



# ──────────────────────────────────────────────────────────────────────────────
#  Sidebar navigation + top header
# ──────────────────────────────────────────────────────────────────────────────

class _AddEmployeePage(QWidget):
    """Hiring somebody, in four steps, on a page.

    THIS REPLACES A 380-PIXEL DIALOG that asked six questions: id, name,
    designation, username, password, role. Everything else a payroll needs —
    the joining date that decides somebody's first month, the PAN that goes on
    the filing, the bank details the money actually leaves by — had to be
    added afterwards from three other screens, if anyone remembered.

    WHY THE ERRORS ALL APPEAR TOGETHER. The dialog validated one field at a
    time and said so in a modal: a name, then an id, then a password, each
    discovered only after fixing the last. Filling in eighteen fields that way
    means eighteen possible round trips through a message box. Everything is
    checked at once, every problem is listed in one panel, each bad field is
    marked, and the form moves to the first step that has one.

    PORTAL ACCESS IS A REAL CHOICE, not a formality. Somebody on the payroll
    is not necessarily somebody who runs the client — a contractor is paid and
    never signs in. With it off there is no username and no password at all,
    and the login query cannot reach the row: LOWER(NULL) never equals
    anything. server/tests/test_employee_onboarding.js holds both halves of
    that down.

    NOTHING ON THE SALARY STEP IS TYPED TWICE. The CTC is entered and the
    components are shown from _ctc_preview — the same arrangement the server
    applies, previewed so the effect of a figure is visible before it is
    saved. The server splits it again on arrival and its answer is the stored
    one.
    """

    back = Signal()
    created = Signal(str)
    page_title = "Add Employee"

    STEPS = ("Basic", "Salary", "Personal", "Payment")

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._step = 0
        self._suggested_id = ""
        self._marked: list[QWidget] = []
        self._build_ui()
        self._suggest_id()
        self._fetch_salary_template()
        self._show_step(0)

    # ── layout ──────────────────────────────────────────────────────────
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}" + _theme.scrollbar())
        host = QWidget()
        _clear_bg(host)
        scroll.setWidget(host)
        outer.addWidget(scroll)

        root = QVBoxLayout(host)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(14)

        head = _card()
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(18, 14, 18, 14)
        head_row.setSpacing(12)
        back = _btn("Back to employees", variant="secondary", height=34)
        back.setIcon(_icons.icon("chevron-left", 14, C["text_muted"]))
        back.clicked.connect(self.back.emit)
        head_row.addWidget(back)
        title = QLabel("Add Employee")
        title.setStyleSheet(
            f"color:{C['text_primary']};font-size:{Type.TITLE}px;"
            f"font-weight:700;background:transparent;border:none;")
        head_row.addWidget(title)
        head_row.addStretch()
        root.addWidget(head)

        # ── the four steps, and where we are ────────────────────────────
        stepper = _card()
        # `rail`, not `strip` — _PayrollTab already has a `strip` that is a
        # QGridLayout, and tests/test_design_system.py resolves widget types by
        # variable name across the file. Two different layouts under one name
        # made it report setColumnStretch() as a call that does not exist.
        rail = QHBoxLayout(stepper)
        rail.setContentsMargins(18, 12, 18, 12)
        rail.setSpacing(0)
        self._step_chips: list[tuple[QLabel, QLabel]] = []
        for index, name in enumerate(self.STEPS):
            if index:
                rule = QFrame()
                rule.setObjectName("stepRule")
                rule.setFixedHeight(1)
                rule.setStyleSheet(
                    f"QFrame#stepRule{{background:{C['border']};border:none;}}")
                rail.addWidget(rule, 1)
            dot = QLabel(str(index + 1))
            dot.setFixedSize(26, 26)
            dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption = QLabel(name)
            self._step_chips.append((dot, caption))
            cell = QHBoxLayout()
            cell.setSpacing(8)
            cell.addWidget(dot)
            cell.addWidget(caption)
            rail.addLayout(cell)
        root.addWidget(stepper)

        # ── everything that is wrong, in one place ──────────────────────
        self._errors_card = _card()
        errs = QVBoxLayout(self._errors_card)
        errs.setContentsMargins(18, 14, 18, 14)
        errs.setSpacing(6)
        self._errors_title = QLabel("")
        self._errors_title.setStyleSheet(
            f"color:{C['danger']};font-size:{Type.BODY}px;font-weight:700;"
            f"background:transparent;border:none;")
        errs.addWidget(self._errors_title)
        self._errors_body = QLabel("")
        self._errors_body.setWordWrap(True)
        self._errors_body.setStyleSheet(
            f"color:{C['text_secondary']};font-size:{Type.SMALL}px;"
            f"background:transparent;border:none;")
        errs.addWidget(self._errors_body)
        self._errors_card.setVisible(False)
        root.addWidget(self._errors_card)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._basic_step())
        self._stack.addWidget(self._salary_step())
        self._stack.addWidget(self._personal_step())
        self._stack.addWidget(self._payment_step())
        root.addWidget(self._stack)

        # ── moving through it ───────────────────────────────────────────
        footer = QHBoxLayout()
        footer.setSpacing(10)
        self._prev_btn = _btn("Previous", variant="secondary", height=38, width=120)
        self._prev_btn.clicked.connect(lambda: self._show_step(self._step - 1))
        footer.addWidget(self._prev_btn)
        footer.addStretch()
        self._status = _muted_label("")
        footer.addWidget(self._status)
        self._next_btn = _btn("Next", variant="secondary", height=38, width=120)
        self._next_btn.clicked.connect(lambda: self._show_step(self._step + 1))
        footer.addWidget(self._next_btn)
        self._save_btn = _btn("Create employee", variant="primary", height=38, width=170)
        self._save_btn.clicked.connect(self._submit)
        footer.addWidget(self._save_btn)
        root.addLayout(footer)
        root.addStretch()

    # ── the steps ───────────────────────────────────────────────────────
    @staticmethod
    def _form_card() -> tuple[QFrame, QGridLayout]:
        card = _card()
        box = QVBoxLayout(card)
        box.setContentsMargins(18, 16, 18, 16)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        box.addLayout(grid)
        return card, grid

    def _field(self, grid: QGridLayout, caption: str, widget: QWidget,
               row: int, column: int = 0, *, required: bool = False):
        """One labelled field. Two per row, so a step is not a long column."""
        label = QLabel(caption + (" *" if required else ""))
        label.setStyleSheet(
            f"color:{C['text_muted']};font-size:{Type.SMALL}px;"
            f"background:transparent;border:none;")
        grid.addWidget(label, row, column * 2)
        grid.addWidget(widget, row, column * 2 + 1)
        return widget

    def _basic_step(self) -> QWidget:
        card, grid = self._form_card()

        self._name = QLineEdit()
        self._name.setPlaceholderText("Rajesh Kumar")
        self._field(grid, "Full name", self._name, 0, 0, required=True)

        # THE ID ARRIVES ALREADY FILLED IN. It is the primary key, printed on
        # reports and carried in URLs, and it cannot be changed afterwards —
        # a blank box invites a stray paste that outlives everybody.
        self._emp_id = QLineEdit()
        self._emp_id.setPlaceholderText("26AMZEM001")
        self._field(grid, "Employee ID", self._emp_id, 0, 1, required=True)

        self._joining = QDateEdit()
        self._joining.setCalendarPopup(True)
        self._joining.setDisplayFormat("yyyy-MM-dd")
        self._joining.setDate(QDate.currentDate())
        self._field(grid, "Joining date", self._joining, 1, 0)

        self._email = QLineEdit()
        self._email.setPlaceholderText("rajesh@amazeinternet.com")
        self._field(grid, "Work email", self._email, 1, 1)

        self._phone = QLineEdit()
        self._phone.setPlaceholderText("9876543210")
        self._field(grid, "Mobile", self._phone, 2, 0)

        self._gender = QComboBox()
        # The stored value and the shown one, kept together so the wording can
        # change without changing what lands in the column.
        for value, shown in (("", "—"), ("male", "Male"), ("female", "Female"),
                             ("other", "Other"),
                             ("prefer_not_to_say", "Prefer not to say")):
            self._gender.addItem(shown, value)
        self._field(grid, "Gender", self._gender, 2, 1)

        self._location = QLineEdit()
        self._location.setPlaceholderText("Head office")
        self._field(grid, "Work location", self._location, 3, 0)

        self._designation = QLineEdit()
        self._designation.setPlaceholderText("QA Engineer")
        self._field(grid, "Designation", self._designation, 3, 1)

        self._department = QLineEdit()
        self._department.setPlaceholderText("Engineering")
        self._field(grid, "Department", self._department, 4, 0)

        self._role = QComboBox()
        # The same rules the server enforces: a super admin may create any
        # role; an admin may create employees only.
        if getattr(SessionManager, "role", "employee") == "super_admin":
            self._role.addItems(["employee", "admin", "super_admin"])
        else:
            self._role.addItems(["employee"])
            self._role.setEnabled(False)
            self._role.setToolTip(
                "Admins can create employees.\n"
                "Only a super admin can create admin or super admin accounts.")
        self._role.currentTextChanged.connect(lambda _t: self._suggest_id())
        self._field(grid, "Role", self._role, 4, 1)

        box = card.layout()
        box.addSpacing(14)
        box.addWidget(_divider())
        box.addSpacing(10)

        self._portal = QCheckBox("Portal access — this person can sign in to Amaze Connect")
        self._portal.setChecked(True)
        self._portal.toggled.connect(self._portal_toggled)
        box.addWidget(self._portal)

        self._portal_note = QLabel(
            "With this off the employee is a payroll record only: no username, "
            "no password, and nothing to sign in with. Credentials can be "
            "issued later. Only an employee can be added this way — an admin "
            "account exists to use the console.")
        self._portal_note.setWordWrap(True)
        self._portal_note.setStyleSheet(
            f"color:{C['text_muted']};font-size:{Type.MICRO}px;"
            f"background:transparent;border:none;")
        box.addWidget(self._portal_note)
        box.addSpacing(10)

        creds = QGridLayout()
        creds.setHorizontalSpacing(16)
        creds.setVerticalSpacing(10)
        creds.setColumnStretch(1, 1)
        creds.setColumnStretch(3, 1)
        box.addLayout(creds)

        self._username = QLineEdit()
        self._username.setPlaceholderText("rajesh")
        self._field(creds, "Username", self._username, 0, 0, required=True)

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._field(creds, "Password", self._password, 0, 1, required=True)
        self._cred_widgets = [self._username, self._password]
        # The labels, so they grey out with the fields they belong to.
        self._cred_labels = [creds.itemAtPosition(0, 0).widget(),
                             creds.itemAtPosition(0, 2).widget()]
        return card

    def _salary_step(self) -> QWidget:
        card, grid = self._form_card()

        # ANNUAL, because that is the figure people agree on: an offer is
        # "three lakh a year", not "twenty-five thousand a month".
        self._ctc = QDoubleSpinBox()
        self._ctc.setRange(0, 1000000000)
        self._ctc.setDecimals(0)
        self._ctc.setPrefix("₹ ")
        self._ctc.setSuffix("  per year")
        self._ctc.setGroupSeparatorShown(True)
        self._ctc.valueChanged.connect(self._restate)
        self._field(grid, "Annual CTC", self._ctc, 0, 0)

        self._overtime = QDoubleSpinBox()
        self._overtime.setRange(0, 100000)
        self._overtime.setDecimals(2)
        self._overtime.setPrefix("₹ ")
        self._overtime.setSuffix("  per hour")
        self._field(grid, "Overtime rate", self._overtime, 0, 1)

        self._effective = QDateEdit()
        self._effective.setCalendarPopup(True)
        self._effective.setDisplayFormat("yyyy-MM-dd")
        self._effective.setDate(QDate.currentDate())
        self._field(grid, "Effective from", self._effective, 1, 0)

        self._gross = QLabel("—")
        self._gross.setStyleSheet(
            f"color:{C['text_primary']};font-size:{Type.BODY}px;font-weight:700;"
            f"background:transparent;border:none;")
        self._field(grid, "Monthly gross", self._gross, 1, 1)

        box = card.layout()
        box.addSpacing(12)
        caption = _muted_label("WHAT THIS CTC DIVIDES INTO, EACH MONTH")
        box.addWidget(caption)
        box.addSpacing(6)

        self._components = _tune_table(QTableWidget(0, 4))
        self._components.setHorizontalHeaderLabels(
            ["Salary component", "Calculation", "Monthly", "Annual"])
        self._components.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._components.verticalHeader().setVisible(False)
        _align_numeric_headings(self._components, (2, 3))
        box.addWidget(self._components)

        box.addSpacing(12)
        box.addWidget(_muted_label("STATUTORY"))
        box.addSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(20)
        self._epf = QCheckBox("Employees' Provident Fund")
        self._esi = QCheckBox("ESI")
        self._pt = QCheckBox("Professional Tax")
        for switch in (self._epf, self._esi, self._pt):
            row.addWidget(switch)
        row.addStretch()
        box.addLayout(row)

        note = QLabel(
            "These are applied automatically when a month is generated, at the "
            "rates on the payroll settings page. Nothing is deducted for a "
            "month that was finalised before they were turned on.")
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color:{C['text_muted']};font-size:{Type.MICRO}px;"
            f"background:transparent;border:none;")
        box.addSpacing(8)
        box.addWidget(note)

        self._restate()
        return card

    def _personal_step(self) -> QWidget:
        card, grid = self._form_card()

        # A SENTINEL THE FORM CAN RECOGNISE. A date field cannot be empty, so
        # "not given" needs a value nobody would type; the minimum is left as
        # the unset state and never sent. _optional_date also stops the popup
        # opening on the year 1900.
        self._dob = _optional_date(QDate(1900, 1, 1))
        self._field(grid, "Date of birth", self._dob, 0, 0)

        self._pan = QLineEdit()
        self._pan.setPlaceholderText("ABCDE1234F")
        self._pan.setMaxLength(10)
        self._field(grid, "PAN", self._pan, 0, 1)

        self._address = QPlainTextEdit()
        self._address.setPlaceholderText("14 MG Road, Bengaluru 560001")
        self._address.setFixedHeight(96)
        self._field(grid, "Address", self._address, 1, 0)
        # The address is worth the width of the card, not half of it.
        grid.addWidget(self._address, 1, 1, 1, 3)

        box = card.layout()
        box.addSpacing(10)
        note = QLabel(
            "All of this is optional. Somebody can be hired before their PAN "
            "has been handed over, and a form that will not save until every "
            "box is full is a form that gets filled with rubbish.")
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color:{C['text_muted']};font-size:{Type.MICRO}px;"
            f"background:transparent;border:none;")
        box.addWidget(note)
        return card

    def _payment_step(self) -> QWidget:
        card, grid = self._form_card()

        self._mode = QComboBox()
        for value, shown in (("", "—"), ("bank_transfer", "Bank Transfer"),
                             ("cheque", "Cheque"), ("cash", "Cash")):
            self._mode.addItem(shown, value)
        self._mode.currentIndexChanged.connect(self._mode_changed)
        self._field(grid, "Payment mode", self._mode, 0, 0)

        self._bank = QLineEdit()
        self._bank.setPlaceholderText("HDFC Bank")
        self._field(grid, "Bank name", self._bank, 1, 0)

        self._account = QLineEdit()
        self._account.setPlaceholderText("012345678901")
        self._field(grid, "Account number", self._account, 1, 1)

        self._ifsc = QLineEdit()
        self._ifsc.setPlaceholderText("HDFC0001234")
        self._ifsc.setMaxLength(11)
        self._field(grid, "IFSC", self._ifsc, 2, 0)

        self._bank_widgets = [self._bank, self._account, self._ifsc]
        self._bank_labels = [grid.itemAtPosition(1, 0).widget(),
                             grid.itemAtPosition(1, 2).widget(),
                             grid.itemAtPosition(2, 0).widget()]

        box = card.layout()
        box.addSpacing(10)
        self._bank_note = QLabel(
            "Bank details are only asked for when the money moves by transfer.")
        self._bank_note.setWordWrap(True)
        self._bank_note.setStyleSheet(
            f"color:{C['text_muted']};font-size:{Type.MICRO}px;"
            f"background:transparent;border:none;")
        box.addWidget(self._bank_note)
        self._mode_changed()
        return card

    # ── reacting ────────────────────────────────────────────────────────
    def _portal_toggled(self, on: bool):
        for widget in self._cred_widgets:
            widget.setEnabled(on)
            if not on:
                widget.clear()
        for label in self._cred_labels:
            label.setEnabled(on)
        # A ROLE THAT CANNOT SIGN IN IS NOT A ROLE. The server refuses it, so
        # the form does not offer it either.
        if not on:
            self._role.setCurrentText("employee")
        self._role.setEnabled(
            on and getattr(SessionManager, "role", "employee") == "super_admin")

    def _mode_changed(self, *_a):
        transfer = self._mode.currentData() == "bank_transfer"
        for widget in self._bank_widgets:
            widget.setEnabled(transfer)
            if not transfer:
                widget.clear()
        for label in self._bank_labels:
            label.setEnabled(transfer)

    def _restate(self):
        rows = _ctc_preview(self._ctc.value())
        if not rows:
            self._components.setRowCount(1)
            self._components.setItem(0, 0, _cell(
                "The salary structure has not loaded yet.", muted=True))
            for column in range(1, self._components.columnCount()):
                self._components.setItem(0, column, _cell("", align_right=True))
            self._gross.setText("—")
            _size_table(self._components, cap=400)
            return
        self._components.setRowCount(len(rows))
        for i, (name, kind, amount) in enumerate(rows):
            self._components.setItem(i, 0, _cell(name))
            self._components.setItem(i, 1, _cell(kind, muted=True))
            self._components.setItem(i, 2, _cell(_money(amount), align_right=True))
            self._components.setItem(i, 3, _cell(_money(amount * 12), align_right=True))
        # SIZED BEFORE THE COLUMNS ARE FITTED. A table shorter than its rows
        # grows a vertical scrollbar, and the scrollbar takes the width the
        # last column was measured for — so "₹300,000.00" came out as
        # "₹300,000…" on a page whose whole job is showing figures.
        _size_table(self._components, cap=400)
        # A WIDER PAD THAN THE DEFAULT, for this table only.
        #
        # _fit_columns measures with table.font(); these cells are drawn in
        # the stylesheet's font, which is larger, so the default 30px of slack
        # left "₹300,000.00" one or two pixels short and Qt drew "₹300,000…".
        # Widening the allowance here rather than in the shared helper keeps
        # every other table in the console measuring exactly as it does today.
        _fit_columns(self._components, stretch=0, pad=46)
        total = sum(amount for _n, _k, amount in rows)
        self._gross.setText(_money(total) if total else "—")

    def _fetch_salary_template(self):
        """Ask for the company's salary structure, so step 2 can preview it.

        The onboarding form does not otherwise touch the salaries endpoint,
        and without the template its components table stays empty — which is
        correct but unhelpful, so it is asked for as the page is built.
        """
        worker = _FetchWorker(f"{API_BASE_URL}/admin/payroll/salaries")

        def keep(data: dict):
            _remember_salary_template(data.get("template"))
            self._restate()

        worker.result.connect(keep)
        worker.error.connect(lambda _e: None)   # no preview is a fine fallback
        _track_worker(self._workers, worker)
        worker.start()

    def _suggest_id(self):
        """Ask the server for the next id in this company's series.

        It only ever replaces a value THIS page put there: somebody who has
        deliberately typed an id and then changes the role must not watch
        their entry vanish.
        """
        if not hasattr(self, "_emp_id"):
            return
        worker = _FetchWorker(f"{API_BASE_URL}/admin/employees/next-id",
                              {"role": self._role.currentText()})

        def fill(data: dict):
            if not data.get("success"):
                return
            typed = self._emp_id.text().strip()
            if typed and typed != self._suggested_id:
                return
            self._suggested_id = data.get("employee_id", "")
            self._emp_id.setText(self._suggested_id)

        worker.result.connect(fill)
        worker.error.connect(lambda _e: None)   # a blank box is a fine fallback
        _track_worker(self._workers, worker)
        worker.start()

    def _show_step(self, index: int):
        index = max(0, min(index, len(self.STEPS) - 1))
        self._step = index
        self._stack.setCurrentIndex(index)
        if index == 1:
            # FIT THE COMPONENTS ONCE THIS STEP IS THE VISIBLE ONE.
            #
            # _fit_columns divides whatever width the table has when it runs,
            # and a hidden page in a QStackedWidget has its layout-default
            # width — about 640px against the 1080 it gets on screen. So the
            # Annual column was sized for a narrower table and drew
            # "₹300,000.00" as "₹300,000…". Truncating a figure on a salary
            # page is not a cosmetic fault.
            #
            # Through a zero-delay timer, because the geometry is not settled
            # until Qt has run the layout that setCurrentIndex just queued.
            QTimer.singleShot(0, self._restate)
        for i, (dot, caption) in enumerate(self._step_chips):
            done, here = i < index, i == index
            fill = C["accent"] if here else (C["accent_soft"] if done else C["bg_surface_alt"])
            ink = C["on_accent"] if here else (C["accent"] if done else C["text_muted"])
            # THE TICK IS DRAWN, NOT TYPED. A "✓" in the source is an emoji as
            # far as the design system is concerned, and it renders in whatever
            # the platform has rather than in the palette's colour — which on
            # a filled accent dot is the difference between a mark and a smudge.
            if done:
                dot.setText("")
                dot.setPixmap(_icons.pixmap("check", 13, ink))
            else:
                dot.setPixmap(QPixmap())
                dot.setText(str(i + 1))
            dot.setStyleSheet(
                # PILL, from the scale. Qt clamps it to half the height, so a
                # 26px box comes out a circle without a literal 13 that would
                # stop being half the moment the dot is resized.
                f"background:{fill};color:{ink};border-radius:{Radius.PILL}px;"
                f"font-size:{Type.SMALL}px;font-weight:700;"
                f"border:1px solid {C['accent'] if (here or done) else C['border']};")
            caption.setStyleSheet(
                f"color:{C['text_primary'] if here else C['text_muted']};"
                f"font-size:{Type.SMALL}px;"
                f"font-weight:{'700' if here else '500'};"
                f"background:transparent;border:none;")
        self._prev_btn.setEnabled(index > 0)
        self._next_btn.setEnabled(index < len(self.STEPS) - 1)

    # ── checking it, all at once ────────────────────────────────────────
    def _problems(self) -> list[tuple[int, QWidget, str]]:
        """Every problem with the form: (step, widget to mark, what is wrong).

        ALL OF THEM, ALWAYS. Returning at the first one is what made the old
        dialog a sequence of message boxes — the point of this page is that
        somebody sees the whole list once.
        """
        found: list[tuple[int, QWidget, str]] = []

        if not self._name.text().strip():
            found.append((0, self._name,
                          "Full name — it is what this person appears as in "
                          "chat, in reports and in the audit log."))
        emp_id = self._emp_id.text().strip()
        if not re.match(r"^[A-Za-z0-9_-]{2,20}$", emp_id):
            found.append((0, self._emp_id,
                          "Employee ID — 2 to 20 characters: letters, digits, "
                          "hyphen or underscore, no spaces. It cannot be "
                          "changed afterwards."))
        email = self._email.text().strip()
        if email and ("@" not in email or "." not in email.split("@")[-1]):
            found.append((0, self._email, "Work email — that is not an address."))
        phone = self._phone.text().strip()
        if phone and not re.match(r"^[0-9+][0-9 \-]{6,19}$", phone):
            found.append((0, self._phone,
                          "Mobile — digits, with an optional leading +."))

        if self._portal.isChecked():
            if not self._username.text().strip():
                found.append((0, self._username,
                              "Username — what this person types to sign in."))
            if not self._password.text():
                found.append((0, self._password,
                              "Password — or turn portal access off, and issue "
                              "one later."))
        elif self._role.currentText() != "employee":
            found.append((0, self._role,
                          "Only an employee can be added without portal "
                          "access — an admin account exists to use the console."))

        pan = self._pan.text().strip().upper()
        if pan and not re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", pan):
            found.append((2, self._pan,
                          "PAN — five letters, four digits and a letter, "
                          "like ABCDE1234F."))
        if len(self._address.toPlainText().strip()) > 500:
            found.append((2, self._address, "Address — 500 characters at most."))

        if self._mode.currentData() == "bank_transfer":
            account = self._account.text().replace(" ", "")
            if account and not re.match(r"^[0-9]{6,20}$", account):
                found.append((3, self._account,
                              "Account number — 6 to 20 digits."))
            ifsc = self._ifsc.text().strip().upper()
            if ifsc and not re.match(r"^[A-Z]{4}0[A-Z0-9]{6}$", ifsc):
                found.append((3, self._ifsc,
                              "IFSC — four letters, a zero, then six more, "
                              "like HDFC0001234."))
        return found

    def _clear_marks(self):
        for widget in self._marked:
            widget.setStyleSheet("")
        self._marked = []
        self._errors_card.setVisible(False)

    def _mark(self, widget: QWidget):
        widget.setStyleSheet(f"border:1px solid {C['danger']};")
        self._marked.append(widget)

    # ── saving ──────────────────────────────────────────────────────────
    def _submit(self):
        self._clear_marks()
        problems = self._problems()
        if problems:
            for _step, widget, _why in problems:
                self._mark(widget)
            lines = []
            for step, _widget, why in problems:
                lines.append(f"•  <b>{self.STEPS[step]}</b> — {why}")
            self._errors_title.setText(
                f"{len(problems)} thing{'' if len(problems) == 1 else 's'} "
                f"to fix before this can be saved")
            self._errors_body.setText("<br>".join(lines))
            self._errors_card.setVisible(True)
            # STRAIGHT TO THE FIRST ONE. A list of problems on a step nobody
            # is looking at is a list nobody reads.
            self._show_step(problems[0][0])
            return

        payload = {
            "employee_id": self._emp_id.text().strip(),
            "role": self._role.currentText(),
            "full_name": self._name.text().strip(),
            "designation": self._designation.text().strip() or None,
            "department": self._department.text().strip() or None,
            "joining_date": self._joining.date().toString("yyyy-MM-dd"),
            "email": self._email.text().strip() or None,
            "phone": self._phone.text().strip() or None,
            "gender": self._gender.currentData() or None,
            "work_location": self._location.text().strip() or None,
            "pan": self._pan.text().strip().upper() or None,
            "address": self._address.toPlainText().strip() or None,
            "payment_mode": self._mode.currentData() or None,
            "portal_access": self._portal.isChecked(),
        }
        if self._portal.isChecked():
            payload["username"] = self._username.text().strip()
            payload["password"] = self._password.text()
        if self._dob.date() != self._dob.minimumDate():
            payload["date_of_birth"] = self._dob.date().toString("yyyy-MM-dd")
        if self._mode.currentData() == "bank_transfer":
            payload["bank_name"] = self._bank.text().strip() or None
            payload["bank_account_number"] = (
                self._account.text().replace(" ", "") or None)
            payload["bank_ifsc"] = self._ifsc.text().strip().upper() or None

        self._save_btn.setEnabled(False)
        self._status.setText("Creating…")
        worker = _PostWorker(f"{API_BASE_URL}/admin/employees", payload)
        worker.result.connect(self._created)
        worker.error.connect(self._failed)
        _track_worker(self._workers, worker)
        worker.start()

    def _failed(self, error: str):
        self._save_btn.setEnabled(True)
        self._status.setText("")
        self._errors_title.setText("Could not create this employee")
        self._errors_body.setText(str(error))
        self._errors_card.setVisible(True)

    def _created(self, data: dict):
        if not data.get("success"):
            self._failed(data.get("message") or data.get("error")
                         or "the server refused it")
            # The server names the field it objected to; the page can then
            # open the step holding it rather than leaving somebody to find it.
            field = data.get("field")
            widget = {"pan": (2, getattr(self, "_pan", None)),
                      "address": (2, getattr(self, "_address", None)),
                      "date_of_birth": (2, getattr(self, "_dob", None)),
                      "gender": (0, getattr(self, "_gender", None)),
                      "work_location": (0, getattr(self, "_location", None)),
                      "payment_mode": (3, getattr(self, "_mode", None)),
                      "bank_name": (3, getattr(self, "_bank", None)),
                      "bank_account_number": (3, getattr(self, "_account", None)),
                      "bank_ifsc": (3, getattr(self, "_ifsc", None))}.get(field)
            if widget and widget[1] is not None:
                self._mark(widget[1])
                self._show_step(widget[0])
            return

        employee_id = self._emp_id.text().strip()
        # THE SALARY IS A SECOND CALL, and it can fail on its own. Reporting
        # "created" while the CTC quietly did not save is how somebody ends up
        # on a payroll at zero.
        if self._ctc.value() > 0:
            self._status.setText("Saving the salary…")
            salary = {
                "employee_id": employee_id,
                "ctc_annual": self._ctc.value(),
                "overtime_hourly": self._overtime.value(),
                "effective_from": self._effective.date().toString("yyyy-MM-dd"),
                "epf_enabled": self._epf.isChecked(),
                "esi_enabled": self._esi.isChecked(),
                "pt_enabled": self._pt.isChecked(),
            }
            worker = _PostWorker(f"{API_BASE_URL}/admin/payroll/salaries", salary)
            worker.result.connect(
                lambda d: self._salary_done(employee_id, d.get("success"),
                                            d.get("message")))
            worker.error.connect(
                lambda e: self._salary_done(employee_id, False, str(e)))
            _track_worker(self._workers, worker)
            worker.start()
            return
        self._salary_done(employee_id, True, None)

    def _salary_done(self, employee_id: str, ok: bool, message: str | None):
        self._save_btn.setEnabled(True)
        self._status.setText("")
        self.created.emit(employee_id)
        if not ok:
            # The employee EXISTS. Saying otherwise would have somebody create
            # them a second time.
            self._errors_title.setText("Added, but the salary did not save")
            self._errors_body.setText(
                f"{employee_id} was created. Their salary was not: "
                f"{message or 'the server refused it'}. Set it from the "
                f"Salaries page.")
            self._errors_card.setVisible(True)
            return
        self.back.emit()

    # ── starting over ───────────────────────────────────────────────────
    def reset(self):
        """A blank form, for the next person.

        The page is kept between uses — it lives in the stack — so anything
        left on it would arrive prefilled with the last hire's details, which
        is how somebody's colleague's PAN ends up on their record.
        """
        self._clear_marks()
        for line in (self._name, self._emp_id, self._email, self._phone,
                     self._location, self._designation, self._department,
                     self._username, self._password, self._pan,
                     self._bank, self._account, self._ifsc):
            line.clear()
        self._address.clear()
        self._gender.setCurrentIndex(0)
        self._mode.setCurrentIndex(0)
        self._role.setCurrentIndex(0)
        self._portal.setChecked(True)
        self._joining.setDate(QDate.currentDate())
        self._effective.setDate(QDate.currentDate())
        self._dob.setDate(self._dob.minimumDate())
        self._ctc.setValue(0)
        self._overtime.setValue(0)
        for switch in (self._epf, self._esi, self._pt):
            switch.setChecked(False)
        self._status.setText("")
        self._save_btn.setEnabled(True)
        self._suggested_id = ""
        self._suggest_id()
        self._show_step(0)


class _Sidebar(QFrame):
    pageChanged = Signal(int)
    profile_clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("sidebar")
        # 270 per the brief. The old 248 was chosen when the entries were a
        # flat list with no icons.
        self.setFixedWidth(270)
        self.logout_btn: QPushButton | None = None
        self.password_btn: QPushButton | None = None
        self._user_searched = False
        self._build()

    def select(self, index: int) -> None:
        """Programmatically ek page pe jao (Dashboard ke Quick Actions se).

        THROUGH THE BUTTON GROUP, NOT THROUGH self._buttons[index].
        `index` is a position in PAGES; _buttons is in the order the menu
        DRAWS them, and since the menu is grouped into sections those two are
        no longer the same number. Indexing the list would open the right page
        and highlight the wrong entry — the sort of thing that looks like a
        rendering glitch and is actually a wrong lookup.
        """
        button = self._group.button(index)
        if button is not None:
            button.setChecked(True)
            self.pageChanged.emit(index)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Brand — one line, not three.
        #
        # "AMAZE" / "Connect" / "Admin Console" stacked in three weights and
        # three colours took 90px of the sidebar to say a name that is also
        # in the window's title bar. A mark and a word is what products of
        # this kind put here.
        # ONE LOCK-UP, in widgets/brand.py, so the sidebar, the login window
        # and anywhere else showing the brand cannot drift apart. The tile
        # used to be the letter "A" set in the interface font on a flat blue
        # square — which reads as a placeholder, because the one thing a mark
        # must not look like is text in a box.
        brand = QWidget()
        b_lay = QHBoxLayout(brand)
        b_lay.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.SM)
        b_lay.setSpacing(0)
        b_lay.addWidget(_BrandLockup("Admin Console", 40))
        root.addWidget(brand)

        # Nav — INSIDE A SCROLL AREA.
        #
        # On a 900px window the last entry was cut in half: fifteen rows plus
        # six headings plus the user card do not fit, and a fixed column
        # simply clipped whatever came last. Configuration was unreachable
        # without resizing the window, which is the worst kind of bug —
        # invisible on the machine it was built on.
        nav_scroll = QScrollArea()
        nav_scroll.setWidgetResizable(True)
        nav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        nav_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # A SCROLLBAR THAT CAN BE SEEN. The panel's global rule draws it in
        # the border colour, which on the sidebar's own background is very
        # nearly invisible — so a menu that scrolled looked like a menu that
        # ended. Wider and lighter, here only.
        nav_scroll.setStyleSheet(
            f"QScrollArea{{background:transparent;border:none;}}"
            f"QScrollBar:vertical{{background:transparent;width:8px;margin:4px 2px;}}"
            f"QScrollBar::handle:vertical{{background:{C['text_muted']};"
            f"border-radius:12px;min-height:40px;}}"
            f"QScrollBar::handle:vertical:hover{{background:{C['text_secondary']};}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}")
        nav_wrap = QWidget()
        _clear_bg(nav_wrap)
        nav_lay = QVBoxLayout(nav_wrap)
        nav_lay.setContentsMargins(Space.SM, Space.SM, Space.SM, Space.SM)
        nav_lay.setSpacing(2)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[QPushButton] = []
        # Kept by key as well as by position, so the unread count can find
        # its own button without counting menu entries.
        self._nav_by_key: dict = {}
        self._nav_base_text: dict = {}

        # THE BUTTON'S ID IS ITS INDEX IN PAGES, NOT ITS POSITION ON SCREEN.
        # The menu is grouped now, so the two are no longer the same number —
        # and everything else in this panel (select(), the page stack, the
        # profile jump) indexes into PAGES. Reordering the sections must not
        # be able to open the wrong page.
        by_key = {page["key"]: (i, page) for i, page in enumerate(PAGES)}
        placed = set()

        def add_section(heading: str, keys: list):
            entries = [by_key[k] for k in keys if k in by_key]
            if not entries:
                return
            label = QLabel(heading)
            label.setStyleSheet(
                f"color:{C['text_muted']};font-size:{Type.MICRO}px;"
                f"font-weight:600;background:transparent;letter-spacing:0.6px;")
            # SPACE ABOVE, NOT BELOW. A heading sitting hard against the last
            # entry of the previous section reads as a label for that entry —
            # which is exactly how it looked: "PEOPLE" appeared to belong to
            # Alerts.
            label.setContentsMargins(Space.SM, 0, 0, 2)
            nav_lay.addSpacing(Space.XS)
            nav_lay.addWidget(label)
            for index, page in entries:
                # && for the same reason NavButton documents: Qt would eat
                # the ampersand in "Teams & Chat" as a mnemonic.
                btn = QPushButton(f"   {page['title'].replace('&', '&&')}")
                btn.setProperty("variant", "navitem")
                btn.setCheckable(True)
                # 40. Fifteen rows, six headings and a user card do not fit a
                # 950px window at 44, so everything below Monitoring could be
                # reached only by scrolling — past a hairline scrollbar nobody
                # notices. Four pixels a row is sixty pixels back.
                btn.setFixedHeight(40)
                btn.setIconSize(QSize(18, 18))
                btn.setIcon(_icons.icon(page["key"], 18, C["text_secondary"]))
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                # WRAPPED. These subtitles are sentences; unwrapped, the
                # Payroll one drew 953px wide across the whole window.
                btn.setToolTip(_theme.tip(page["subtitle"]))
                self._group.addButton(btn, index)
                nav_lay.addWidget(btn)
                self._buttons.append(btn)
                self._nav_by_key[page["key"]] = btn
                self._nav_base_text[page["key"]] = \
                    f"   {page['title'].replace('&', '&&')}"
                placed.add(page["key"])

        # THE ICON FOLLOWS THE ROW'S STATE. An icon is baked into a pixmap at
        # one colour, so the active row would keep a muted glyph beside white
        # text without this.
        def _retint(checked_button):
            for key, button in self._nav_by_key.items():
                button.setIcon(_icons.icon(
                    key, 18,
                    C["text_primary"] if button is checked_button
                    else C["text_secondary"]))

        self._group.buttonToggled.connect(
            lambda button, on: _retint(button) if on else None)

        for heading, keys in NAV_SECTIONS:
            add_section(heading, keys)

        # ANY PAGE NOT LISTED IN A SECTION STILL APPEARS. Adding a page and
        # forgetting to file it would otherwise make it unreachable — a menu
        # that silently hides features is worse than an ugly one.
        add_section("OTHER", [p["key"] for p in PAGES if p["key"] not in placed])

        # The dashboard, by key. _buttons[0] happens to be the same button
        # today and would stop being it the moment the sections are reordered.
        if self._nav_by_key.get("dashboard") is not None:
            self._nav_by_key["dashboard"].setChecked(True)
        self._group.idClicked.connect(self.pageChanged.emit)

        # An unread count on the menu itself.
        #
        # Reported after a message arrived and nobody knew: the count was only
        # ever drawn on the channel row INSIDE My Chat, so it could only be
        # seen by somebody already looking at the page they would have needed
        # the count to tell them to open.
        #
        # The number goes into the button's own text rather than a badge
        # floating over it — a separate widget positioned on top of a button
        # drifts when the sidebar is resized, and this cannot.

        nav_lay.addStretch()
        nav_scroll.setWidget(nav_wrap)
        root.addWidget(nav_scroll, 1)

        # Footer
        footer = QWidget()
        f_lay = QVBoxLayout(footer)
        # Tighter than it was. The card, its two buttons and their margins
        # took a quarter of the sidebar's height, which is what pushed the
        # last two sections of the menu out of sight on a 950px window.
        f_lay.setContentsMargins(Space.SM, Space.XS, Space.SM, Space.SM)
        f_lay.setSpacing(Space.XS)
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

        avatar = ClickableAvatar(32)
        avatar.show_person(getattr(SessionManager, "employee_id", None), display_name)
        avatar.setToolTip("My Profile")
        avatar.clicked.connect(self.profile_clicked.emit)
        self._footer_avatar = avatar

        role_col = QVBoxLayout()
        role_col.setSpacing(0)
        name = QLabel(display_name)
        name.setStyleSheet(f"color:{C['text_primary']}; font-size:12px; font-weight:700; background:transparent;")
        role = QLabel(role_text)
        role.setStyleSheet(f"color:{C['text_muted']}; font-size:12px; background:transparent;")
        role_col.addWidget(name)
        role_col.addWidget(role)

        # The name and the role under it are part of the same target.
        for _label in (name, role):
            _label.setCursor(Qt.CursorShape.PointingHandCursor)
            _label.setToolTip("My Profile")
            _label.mousePressEvent = (
                lambda _event, _s=self: _s.profile_clicked.emit())

        role_row.addWidget(avatar)
        role_row.addLayout(role_col)
        role_row.addStretch()
        # A signed-in user is, by definition, online. Small and green, the
        # way every product of this kind marks it.
        # A DRAWN DOT, NOT THE CHARACTER "●". Its size came from the font,
        # so it changed with the type scale and never sat level with the text
        # beside it — and a font without the glyph draws nothing at all.
        dot = QLabel()
        dot.setToolTip("Signed in")
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(_theme.dot_style(8, C["success"]))
        role_row.addWidget(dot)
        f_lay.addLayout(role_row)

        # Admins are accounts too — before this there was no way for one to
        # change their own password anywhere in the app.
        # Lucide, not emoji. A 🔑 and a 🔒 are drawn by the operating
        # system's colour font: they arrive at their own weight and their own
        # palette, and two of them side by side never match each other or the
        # text they sit beside.
        self.password_btn = _btn("Change Password", variant="secondary", height=40)
        self.password_btn.setIcon(_icons.icon("key-round", 16, C["text_secondary"]))
        self.password_btn.setIconSize(QSize(16, 16))
        f_lay.addWidget(self.password_btn)

        self.logout_btn = _btn("Logout", variant="danger", height=40)
        self.logout_btn.setIcon(_icons.icon("log-out", 16, C["danger"]))
        self.logout_btn.setIconSize(QSize(16, 16))
        f_lay.addWidget(self.logout_btn)

        root.addWidget(footer)


    def set_unread(self, key: str, count: int) -> None:
        """Put an unread count on a menu entry, or take it off at zero.

        Capped at 99+, because the number stops being useful long before it
        stops fitting.
        """
        button = getattr(self, "_nav_by_key", {}).get(key)
        if button is None:
            return
        base = self._nav_base_text.get(key, button.text())
        count = max(0, int(count or 0))
        button.setText(base if count == 0
                       else f"{base}   ({count if count < 100 else '99+'})")

        # AND IT TURNS RED.
        #
        # The count was drawn in the same grey as every other menu entry, so
        # it read as part of the label rather than as something waiting.
        # Reported after it worked: "3 dikha, but dhyan hi nahi gaya."
        #
        # A dynamic property rather than a stylesheet per button: Qt can
        # restyle by property, and setting a sheet on one row would lose the
        # hover and active rules the sidebar's own sheet gives it.
        button.setProperty("unread", "true" if count else "false")
        button.style().unpolish(button)
        button.style().polish(button)


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
    theme_clicked   = Signal()
    profile_clicked = Signal()
    bell_clicked    = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("topHeader")
        self.setFixedHeight(88)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(28, 0, 28, 0)
        lay.setSpacing(10)

        # THE HEADING NAMES THE PAGE, NOT THE PRODUCT.
        #
        # It said "Amaze Connect" on all fifteen pages. The product's name is
        # already in the sidebar and on the window's own title bar; repeating
        # it here spent the largest text on the screen saying the one thing
        # the reader could not possibly need, and left the actual page name in
        # small grey type below it, prefixed by an emoji.
        #
        # A breadcrumb above it says where that page sits — Amaze Connect ›
        # Time & Pay — which is the piece a grouped menu makes meaningful.
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self._crumb = QLabel("Amaze Connect")
        self._crumb.setStyleSheet(
            f"color:{C['text_muted']};font-size:{_theme.Type.MICRO}px;"
            f"font-weight:{_theme.Weight.SEMIBOLD};background:transparent;"
            f"letter-spacing:0.4px;")
        self._title = QLabel("Dashboard")
        self._title.setStyleSheet(
            f"color:{C['text_primary']};font-size:{_theme.Type.TITLE}px;"
            f"font-weight:800;background:transparent;")
        self._subtitle = QLabel("")
        self._subtitle.setStyleSheet(
            f"color:{C['text_secondary']};font-size:{_theme.Type.SMALL}px;"
            f"background:transparent;")
        text_col.addWidget(self._crumb)
        text_col.addWidget(self._title)
        text_col.addWidget(self._subtitle)
        lay.addLayout(text_col)
        lay.addStretch()

        def action(icon_name, label, slot):
            btn = QPushButton(f"  {label}")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(40)
            btn.setIcon(_icons.icon(icon_name, 16, C["text_secondary"]))
            btn.setIconSize(QSize(16, 16))
            btn.setStyleSheet(
                f"QPushButton{{background:{C['bg_surface']};"
                f"border:1px solid {C['border']};"
                f"border-radius:{_theme.Radius.CONTROL}px;color:{C['text_primary']};"
                f"font-size:{_theme.Type.BODY}px;font-weight:500;padding:0 16px;}}"
                f"QPushButton:hover{{background:{C['hover']};"
                f"border-color:{C['border_light']};}}"
                f"QPushButton:disabled{{color:{C['text_muted']};}}"
            )
            btn.clicked.connect(slot)
            lay.addWidget(btn)
            return btn

        # WHAT NEEDS SOMEBODY, AND HOW MANY OF IT.
        #
        # There was no such indicator at all: leave requests waited until an
        # administrator happened to open the Leave page, and shifts left open
        # were found only by scrolling attendance. Both are things a person
        # has to do, and neither had anywhere to say so.
        #
        # It shows a number or it shows nothing. A bell that is always lit
        # stops being read within a week.
        self.btn_bell = QPushButton("")
        self.btn_bell.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_bell.setFixedSize(40, 40)
        self.btn_bell.setIcon(_icons.icon("bell", 18, C["text_secondary"]))
        self.btn_bell.setIconSize(QSize(18, 18))
        self.btn_bell.setToolTip("Nothing is waiting.")
        self.btn_bell.clicked.connect(self.bell_clicked.emit)
        self._style_bell(0)
        lay.addWidget(self.btn_bell)

        # The theme switch sits first, before the actions, because it is the
        # only one that changes how everything looks rather than what it says.
        self.btn_theme = QPushButton("")
        self.btn_theme.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_theme.setFixedSize(40, 40)
        self.btn_theme.setIcon(_icons.icon(
            "sun" if _theme.is_light() else "moon", 18, C["text_secondary"]))
        self.btn_theme.setIconSize(QSize(18, 18))
        self.btn_theme.setToolTip(
            "Switch to the dark theme" if _theme.is_light()
            else "Switch to the light theme")
        self.btn_theme.setStyleSheet(
            f"QPushButton{{background:{C['bg_surface']};border:1px solid {C['border']};"
            f"border-radius:{_theme.Radius.CONTROL}px;}}"
            f"QPushButton:hover{{background:{C['hover']};"
            f"border-color:{C['border_light']};}}")
        self.btn_theme.clicked.connect(self.theme_clicked.emit)
        lay.addWidget(self.btn_theme)

        self.btn_refresh = action("refresh-cw", "Refresh", self.refresh_clicked.emit)
        self.btn_export  = action("download", "Export", self.export_clicked.emit)
        self.btn_sync    = action("cloud-upload", "Sync Now", self.sync_clicked.emit)

        chip = QFrame()
        chip.setStyleSheet(
            f"QFrame{{background:{C['bg_surface']};border:1px solid {C['border']};"
            f"border-radius:12px;}}"
        )
        cl = QHBoxLayout(chip)
        cl.setContentsMargins(12, 7, 16, 7)
        cl.setSpacing(11)
        # THE SAME AVATAR WIDGET THE SIDEBAR USES, not a 👤 drawn here. This
        # chip used to be a fixed emoji, so the one place an administrator
        # looks at their own account showed a stranger's outline while their
        # photograph sat in the sidebar directly below it.
        avatar = ClickableAvatar(32)
        # full_name. NOT employee_name, which does not exist on SessionManager
        # — getattr returned None and the avatar drew a "?" where the person's
        # initials belong. The same shape of mistake as SessionManager.token,
        # which is not a thing either, and which cost this panel its
        # photographs once already.
        avatar.show_person(getattr(SessionManager, "employee_id", None),
                           getattr(SessionManager, "full_name", None)
                           or getattr(SessionManager, "employee_id", None) or "")
        avatar.setToolTip("My Profile")
        avatar.clicked.connect(self.profile_clicked.emit)
        self._chip_avatar = avatar
        who = QVBoxLayout(); who.setSpacing(0)
        self._chip_id = QLabel(getattr(SessionManager, "employee_id", None) or "—")
        self._chip_id.setStyleSheet(
            f"color:{C['text_primary']};font-size:13px;font-weight:700;border:none;"
        )
        self._chip_role = QLabel(
            "Super Admin" if getattr(SessionManager, "role", "") == "super_admin" else "Admin"
        )
        self._chip_role.setStyleSheet(
            f"color:{C['success']};font-size:12px;font-weight:600;border:none;"
        )
        who.addWidget(self._chip_id); who.addWidget(self._chip_role)
        cl.addWidget(avatar); cl.addLayout(who)
        lay.addWidget(chip)

    def _style_bell(self, count: int):
        colour = _theme.status_fg("pending") if count else C["text_muted"]
        border = C["accent"] if count else C["border"]
        self.btn_bell.setStyleSheet(
            f"QPushButton{{background:{C['bg_surface']};border:1px solid {border};"
            f"border-radius:{_theme.Radius.CONTROL}px;color:{colour};"
            f"font-size:{_theme.Type.MICRO}px;font-weight:600;}}"
            f"QPushButton:hover{{background:{C['hover']};}}")

    def set_attention(self, items: list):
        """`items` is [(count, "what it is"), ...] — only non-zero ones.

        The tooltip lists them rather than the button showing a total alone:
        "3" tells somebody there is work; "2 leave requests, 1 shift never
        signed out" tells them which page to open.
        """
        items = [(n, what) for n, what in items if n]
        total = sum(n for n, _what in items)
        self.btn_bell.setText("" if not total else
                              f"  {total if total < 100 else '99+'}")
        self.btn_bell.setIcon(_icons.icon(
            "bell", 18,
            _theme.status_fg("pending") if total else C["text_secondary"]))
        self.btn_bell.setFixedSize(40 if not total else 64, 40)
        self.btn_bell.setToolTip(
            "Nothing is waiting." if not items else
            "\n".join(f"{n}  {what}" for n, what in items))
        self._style_bell(total)

    def set_page(self, icon: str, title: str, subtitle: str, section: str = ""):
        """Name the page, say where it sits, and describe it underneath.

        The long subtitles still elide. That fix is kept as it was:
        Configuration's runs to two lines of prose, and set loose it slid
        under the header's buttons and was drawn cut off mid-word
        ("...upload frequency — g"). The full text is in the tooltip.
        """
        self._crumb.setText(
            f"Amaze Connect  ›  {section}" if section else "Amaze Connect")
        self._title.setText(f"{icon}  {title}" if icon else title)
        self._page_text = subtitle
        self._subtitle.setToolTip(subtitle)
        self._relayout_subtitle()

    def _relayout_subtitle(self):
        from PySide6.QtGui import QFontMetrics
        text = getattr(self, "_page_text", "")
        if not text:
            return
        # THE RESERVE IS MEASURED, NOT GUESSED.
        #
        # It was a flat 700px for "the buttons and the chip". Those grew — a
        # bell, a wider profile card, 14px labels — and the subtitle was cut
        # mid-word with no ellipsis to show it: "…built from attendance and".
        # Asking the row what it actually occupies cannot go stale that way.
        reserved = 0
        for child in (getattr(self, "btn_bell", None),
                      getattr(self, "btn_theme", None),
                      getattr(self, "btn_refresh", None),
                      getattr(self, "btn_export", None),
                      getattr(self, "btn_sync", None)):
            if child is not None:
                reserved += child.width() + _theme.Space.SM
        # The identity chip on the right, plus the page's own margins.
        reserved += 260
        available = max(220, self.width() - reserved)
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

    # Every tab that owns background workers or timers.
    #
    # This used to be written out by hand in _drain_workers and again in
    # _stop_background_services, and both copies were incomplete: neither
    # listed _reports_tab, and adding the Teams tab would have made a third
    # omission. A tab missing from these lists keeps its threads running past
    # logout, and a QThread destroyed while still running takes the whole
    # application down — which is the crash the comments in those two methods
    # are already about. One list, so the next tab cannot be forgotten.
    TAB_ATTRS = (
        "_dashboard_tab", "_alerts_tab", "_config_tab", "_employees_tab",
        "_attendance_tab", "_screenshots_tab", "_teams_tab", "_mychat_tab",
        "_payroll_tab", "_leave_tab", "_reports_tab", "_logs_tab",
        "_myleave_tab", "_mypayroll_tab", "_profile_tab",
        # NOT A SIDEBAR PAGE, BUT STILL A PAGE. This list is what drains
        # workers and stops timers on logout — leaving it out would let this
        # page's fetch outlive the window, which is the QThread-destroyed-
        # while-running crash that took the app down on sign-out before.
        "_employee_payroll", "_salary_page", "_summary_page",
        "_add_employee_page", "_employee_page",
    )

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Amaze Connect — Control Center")
        self.setMinimumSize(1080, 680)
        self.resize(1300, 820)
        self._logging_out = False  # Guard flag to prevent recursion
        self._force_close = False  # True = asli exit (tray se), minimise nahi
        self.setStyleSheet(_global_stylesheet())

        # Built here, NOT in _build_central: a theme switch rebuilds the tabs,
        # and a ChatManager created in there would be replaced each time,
        # leaving the previous one polling with nothing attached to it.
        #
        # An admin who is a member of a team is a member like anybody else —
        # the server has always served them their channels. They simply had no
        # screen to read them on, because chat lived only in the employee
        # panel and an admin never sees that panel.
        from client.application.managers.chat_manager import ChatManager
        self.chat = ChatManager(self)

        self._build_central()
        self._after_central()

    def _build_central(self):
        """Everything inside the window. Re-run when the theme changes.

        ACCENTS is refreshed first: the cards read it while they are being
        constructed, and the light palette needs a darker green and amber than
        the dark one — #22c55e on white is barely visible.

        Rebuilding rather than restyling is deliberate — see theme.py. Ninety
        call sites in this file bake their colours in at construction, so the
        only way to be certain none was missed is to construct them again.
        """
        ACCENTS.update(_accents())

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
        # Its signals are attached in _wire_central, not here — a theme switch
        # rebuilds this header and would otherwise connect each one twice, so
        # every Refresh would fire two requests.
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
        self._alerts_tab      = _AlertsTab()
        self._config_tab      = _ConfigTab()
        self._employees_tab   = _EmployeesTab()
        self._attendance_tab  = _AttendanceTab()
        self._screenshots_tab = _ScreenshotsTab()
        # Imported here rather than at module scope: admin_teams_tab borrows
        # this file's helpers, so importing it at the top would be circular.
        from client.presentation.windows.admin_teams_tab import _TeamsTab
        self._teams_tab       = _TeamsTab()
        from client.presentation.windows.team_page import TeamPage
        self._mychat_tab      = TeamPage(self, self.chat)
        # The count reaches the sidebar, so an unread message is visible from
        # any page — not only from inside the one it arrived in.
        self._mychat_tab.unread_changed.connect(
            lambda total: self.sidebar.set_unread("mychat", total))
        self._payroll_tab     = _PayrollTab()
        # ONE PERSON'S PAY, on its own page. Appended AFTER every sidebar
        # page below, deliberately: the sidebar switches the stack by index,
        # so a page inserted among them would silently show the wrong one for
        # every entry after it. This has no sidebar entry — it is reached by
        # double-clicking somebody, and it goes back where it came from.
        self._employee_payroll = _EmployeePayrollPage()
        self._salary_page = _SalaryPage()
        self._summary_page = _PayrollSummaryPage()
        self._add_employee_page = _AddEmployeePage()
        self._employee_page = EmployeePage()
        self._employees_tab.open_add_employee.connect(self._open_add_employee)
        self._employees_tab.open_employee.connect(self._open_employee_page)
        self._employee_page.back.connect(
            lambda: self.stack.setCurrentWidget(self._employees_tab))
        self._add_employee_page.back.connect(
            lambda: self.stack.setCurrentWidget(self._employees_tab))
        # The list reloads on its own timer, but not for thirty seconds — long
        # enough that somebody who has just added a person would think it had
        # not worked.
        self._add_employee_page.created.connect(
            lambda _id: self._employees_tab._load_employees())
        self._payroll_tab.open_employee.connect(self._open_employee_payroll)
        self._payroll_tab.open_salaries.connect(self._open_salary_page)
        self._payroll_tab.open_summary.connect(self._open_summary_page)
        self._salary_page.back.connect(
            lambda: self.stack.setCurrentWidget(self._payroll_tab))
        self._summary_page.back.connect(
            lambda: self.stack.setCurrentWidget(self._payroll_tab))
        self._employee_payroll.open_salaries.connect(self._open_salary_page)
        self._employee_payroll.back.connect(
            lambda: self.stack.setCurrentWidget(self._payroll_tab))
        self._leave_tab       = _LeaveTab()
        self._reports_tab     = _ReportsTab()
        self._logs_tab        = _LogsTab()
        from client.presentation.windows.leave_page import LeavePage
        self._myleave_tab     = LeavePage(self)
        from client.presentation.windows.payroll_page import PayrollPage
        self._mypayroll_tab   = PayrollPage(self)
        from client.presentation.windows.profile_page import ProfilePage
        self._profile_tab     = ProfilePage(self)

        # Workers ka intezaar timers band karne ke baad, destroy se pehle.
        leftover = self._drain_workers()
        if leftover:
            print(f"[ADMIN] {leftover} worker(s) timeout ke baad bhi chal rahe hain")

        # ORDER MUST MATCH PAGES. The sidebar switches by index, so a tab
        # added here but not there — or in the wrong place — silently shows
        # the wrong page for every entry after it.
        for tab in (
            self._dashboard_tab,
            self._alerts_tab,
            self._config_tab,
            self._employees_tab,
            self._attendance_tab,
            self._screenshots_tab,
            self._teams_tab,
            self._mychat_tab,
            self._payroll_tab,
            self._leave_tab,
            self._reports_tab,
            self._logs_tab,
            self._myleave_tab,
            self._mypayroll_tab,
            self._profile_tab,
            # LAST, and with no sidebar entry — see the note where it is made.
            self._employee_payroll,
            self._salary_page,
            self._summary_page,
            self._add_employee_page,
            self._employee_page,
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
        ver = QLabel(f"Amaze Connect · Admin Console v{APP_VERSION}")
        ver.setStyleSheet(f"color:{C['text_muted']};font-size:12px;border:none;background:transparent;")
        # THE SERVER IT IS ACTUALLY TALKING TO.
        #
        # This said "Connected to Production Server" whatever it was connected
        # to — a test build pointed at a laptop said it too. A status bar that
        # cannot be wrong about the one thing it reports is worth more than a
        # reassuring sentence, and "which server is this build on" is the
        # first question asked when two people see different data.
        self._status_server = QLabel(_server_label())
        self._status_server.setToolTip(API_BASE_URL)
        self._status_server.setStyleSheet(
            f"color:{C['success']};font-size:{_theme.Type.MICRO}px;"
            f"border:none;background:transparent;")
        enc = QLabel("Encryption: AES-256 GCM")
        enc.setStyleSheet(f"color:{C['text_muted']};font-size:12px;border:none;background:transparent;")
        sb.addWidget(ver); sb.addStretch()
        sb.addWidget(self._status_server); sb.addStretch()
        sb.addWidget(enc)
        c_lay.addWidget(status)

        root.addWidget(content, 1)

        # The previous central widget has to be destroyed EXPLICITLY.
        #
        # setCentralWidget removes it from the layout but leaves it parented
        # to the window, so after a theme switch the entire old console was
        # still alive underneath the new one — every tab, every chart, every
        # timer, in the colours nobody could see any more. Invisible, and
        # doubling with each switch.
        #
        # Found by the theme test walking the widget tree and finding dark
        # widgets in a light window; from the screen alone it looked perfect.
        previous = self.centralWidget()
        self.setCentralWidget(central)
        if previous is not None and previous is not central:
            previous.setParent(None)
            previous.deleteLater()

    def _wire_central(self, page_index: int = 0):
        """Signals that belong to the widgets _build_central just made.

        Separate from the services below because a theme switch rebuilds the
        widgets and must re-attach these — but must NOT start a second
        scheduler or a second idle tracker.
        """
        # Dashboard ke Quick Actions ko sidebar navigation se joda.
        # The key is resolved to an index HERE, once, against the live PAGES
        # list — so inserting a page can never again silently repoint every
        # one of these buttons at its neighbour.
        for label, (btn, page_key) in getattr(
            self._dashboard_tab, "_quick_buttons", {}
        ).items():
            index = next((i for i, page in enumerate(PAGES)
                          if page["key"] == page_key), None)
            if index is None:
                continue
            btn.clicked.connect(
                lambda _=False, idx=index: self.sidebar.select(idx)
            )

        # The dashboard asks for a page BY KEY; the index is resolved here,
        # against the live PAGES list.
        self._dashboard_tab.open_page.connect(
            lambda key: self.sidebar.select(
                next((i for i, page in enumerate(PAGES)
                      if page["key"] == key), 0)))

        self.sidebar.pageChanged.connect(self._on_page_changed)
        def _open_profile():
            self.sidebar.select(
                next(i for i, page in enumerate(PAGES) if page["key"] == "profile"))

        self.sidebar.profile_clicked.connect(_open_profile)
        self.header.profile_clicked.connect(_open_profile)
        self.sidebar.logout_btn.clicked.connect(self.logout)
        self.sidebar.password_btn.clicked.connect(self._change_own_password)
        self.header.refresh_clicked.connect(self._refresh_current_page)

        # The bell opens whichever page the biggest item belongs to. A bell
        # that shows a count and does nothing when pressed is worse than no
        # bell — it says there is work and refuses to say where.
        self.header.bell_clicked.connect(self._open_attention)

        # WHAT NEEDS ATTENTION, POLLED ONCE A MINUTE.
        #
        # Not on every page change: an administrator moving between pages
        # would fire it constantly, and the two counts it reads are the same
        # two on every page. A minute is well inside the time anybody takes
        # to act on a leave request.
        self._attention_timer = QTimer(self)
        self._attention_timer.setInterval(60_000)
        self._attention_timer.timeout.connect(self._load_attention)
        self._attention_timer.start()
        self._attention = {"leave": 0, "open_shifts": 0}
        QTimer.singleShot(1200, self._load_attention)
        self.header.export_clicked.connect(self._export_current_page)
        self.header.sync_clicked.connect(self._sync_now)
        self.header.theme_clicked.connect(self._toggle_theme)
        self.sidebar.select(page_index)
        self._on_page_changed(page_index)

    def _toggle_theme(self):
        """Switch palette, rebuild everything inside the window."""
        page_index = self.stack.currentIndex() if hasattr(self, "stack") else 0

        # WHOSE PAY HISTORY WAS OPEN. The rebuild makes a fresh page, and a
        # fresh page knows nobody: the theme changed and the page went blank —
        # every figure a dash — until you navigated away and came back.
        # Reported exactly that way: "dark se light aur light se dark to page
        # karke wapas aana pad raha hai, nahi to data empty ho ja raha".
        #
        # The id is enough. The figures are the server's, and asking for them
        # again is cheaper and more honest than carrying a copy across a
        # rebuild that exists to throw widgets away.
        open_employee = None
        page = getattr(self, "_employee_payroll", None)
        if page is not None and self.stack.currentWidget() is page:
            open_employee = getattr(page, "_employee_id", None)

        # The chat page's own state travels across the rebuild — which channel
        # was open, and anything typed but not sent. See TeamPage.snapshot.
        chat_state = None
        chat_page = getattr(self, "_mychat_tab", None)
        if chat_page is not None and hasattr(chat_page, "snapshot"):
            try:
                chat_state = chat_page.snapshot()
            except Exception:
                chat_state = None

        _theme.toggle_theme()
        # Icons are pixmaps baked at one colour; the cache would otherwise
        # hand out the old theme's glyphs for the rest of the session.
        _icons.clear_cache()
        self.setStyleSheet(_global_stylesheet())
        # Only the TABS are torn down — NOT the scheduler or the idle tracker.
        #
        # BUG this fixes: this called _stop_background_services(), which also
        # stops both of those, and nothing started them again. Switching the
        # theme therefore ended the admin's own tracking for the rest of the
        # session: no screenshots, no idle state, and the Activity Status card
        # stuck on "—" forever. The only trace was one line in the audit log —
        # "SchedulerService: stopped" — with nothing saying why.
        #
        # In a product whose entire job is tracking, that is the worst way to
        # fail: quietly, and while reporting itself healthy.
        self._stop_tab_work()
        self._build_central()
        self._wire_central(page_index)

        # RE-POLISH EVERY WIDGET STYLED BY A DYNAMIC PROPERTY.
        #
        # Qt matches [variant="primary"] when a widget is polished, and does
        # NOT re-evaluate those selectors just because the application
        # stylesheet changed. So after a theme switch the primary buttons kept
        # the rule they were matched against at birth: white text, no accent
        # fill. On the dark theme that was survivable; on the light one it was
        # white on white — "Generate draft" and "Set salary" became empty
        # outlined boxes with no label at all. Reported in both themes.
        #
        # unpolish/polish makes Qt look at the properties again against the
        # sheet that is current now.
        style = self.style()
        for widget in self.findChildren(QWidget):
            if widget.property("variant") is not None:
                style.unpolish(widget)
                style.polish(widget)

        if open_employee:
            self._employee_payroll.load(open_employee)

        fresh_chat = getattr(self, "_mychat_tab", None)
        if chat_state and fresh_chat is not None and hasattr(fresh_chat, "restore"):
            try:
                fresh_chat.restore(chat_state)
            except Exception:
                pass

        # The cards fed by signals rather than by a fetch have to be redrawn
        # by hand — nothing will send them their value again on its own.
        self._on_own_idle(getattr(self, "_own_idle_status", "WORKING"))

    def _after_central(self):
        self._wire_central(0)

        # Polls whether or not the tab is open, so the sidebar count is right
        # before somebody goes looking for it.
        self.chat.messages.connect(self._on_chat_messages)
        # Announcements reach everybody; the administrative alerts — an app
        # that has stopped reporting, a shift nobody logged in for — reach the
        # only people who can act on them.
        self.chat.notifications.connect(self._on_chat_alerts)
        # Same as the employee panel: the chat poll is the first thing to
        # notice that this session has been ended server-side.
        self.chat.session_ended.connect(self.logout)
        self.chat.start()

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
        # Remembered so the card can be repainted after a rebuild.
        #
        # IdleTracker only emits on a CHANGE of state, so a freshly built card
        # has nothing to draw and sits on "—" until the admin next goes idle
        # or comes back — which can be a long time, and reads as tracking
        # having stopped.
        self._own_idle_status = status
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

    def _load_attention(self):
        """Two counts: leave waiting on a decision, and shifts never closed.

        BOTH ARE THINGS A PERSON HAS TO DO, which is the test for belonging
        here. Alerts are deliberately not counted: they are conditions, they
        come and go on their own, and adding them would keep the bell lit
        permanently — at which point nobody reads it.
        """
        def pending(data):
            summary = (data or {}).get("data") or {}
            self._attention["leave"] = int(summary.get("pending") or 0)
            self._push_attention()

        def open_shifts(data):
            board = (data or {}).get("data") or {}
            self._attention["open_shifts"] = int(board.get("not_signed_out") or 0)
            self._push_attention()

        # The panel creates this list lazily elsewhere, and this runs on a
        # timer that can fire before that happens.
        self._workers = getattr(self, "_workers", [])
        for url, handler in ((f"{API_BASE_URL}/dashboard/summary", pending),
                             (f"{API_BASE_URL}/dashboard/today", open_shifts)):
            worker = _FetchWorker(url, {})
            worker.result.connect(handler)
            # Silent on failure. A count that could not be fetched must not
            # put an error in front of somebody doing something else.
            worker.error.connect(lambda _e: None)
            _track_worker(self._workers, worker)
            worker.start()

    def _push_attention(self):
        self.header.set_attention([
            (self._attention["leave"], "leave requests waiting on a decision"),
            (self._attention["open_shifts"], "shifts never signed out"),
        ])

    def _open_attention(self):
        """Offer what is waiting, and let the reader pick.

        IT USED TO GUESS. Whichever count was larger won, so a bell showing
        "1" opened Attendance when the reader expected Leave — the number is
        a total, and a total cannot say which page it belongs to. Reported as
        exactly that.

        A menu costs one extra click and removes the guess. With nothing
        waiting it goes straight to Alerts, because then there is nothing to
        choose between.
        """
        items = [
            (self._attention["leave"], "leave requests waiting on a decision",
             "leave"),
            (self._attention["open_shifts"], "shifts never signed out",
             "attendance"),
        ]
        waiting = [(count, what, key) for count, what, key in items if count]

        def go(key):
            index = next((i for i, page in enumerate(PAGES)
                          if page["key"] == key), 0)
            self.sidebar.select(index)

        if not waiting:
            go("alerts")
            return

        menu = QMenu(self)
        for count, what, key in waiting:
            action = menu.addAction(f"{count}  {what}")
            action.triggered.connect(lambda _checked=False, k=key: go(k))
        menu.addSeparator()
        everything = menu.addAction("Open Alerts")
        everything.triggered.connect(lambda _checked=False: go("alerts"))
        menu.exec(self.header.btn_bell.mapToGlobal(
            self.header.btn_bell.rect().bottomLeft()))

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
            self._status_server.setText(_server_label())
            self._status_server.setStyleSheet(
                f"color:{C['success']};font-size:{_theme.Type.MICRO}px;"
                f"border:none;background:transparent;")
            self._dashboard_tab._load_all()

        def fail(error):
            self.header.set_busy(False)
            self._status_server.setText("Server unreachable")
            self._status_server.setStyleSheet(
                f"color:{C['danger']};font-size:12px;border:none;background:transparent;"
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

        # NOT EVERY PAGE IS A MENU ENTRY. The employee pay-history page is
        # reached by double-clicking somebody, so it sits in the stack after
        # the fifteen the sidebar knows about — and PAGES[15] does not exist.
        #
        # THE BUG THIS FIXES, reported as "theme change karke khol raha hoon
        # to nahi khul raha": switching the theme saves the current stack
        # index and restores it afterwards. With that page open the index was
        # 15, this raised IndexError in the middle of the rebuild, and the
        # console was left half-built — the theme changed and nothing worked
        # after it. A crash inside a repaint is invisible; there is no dialog,
        # the window simply stops responding to the sidebar.
        if not 0 <= idx < len(PAGES):
            widget = self.stack.currentWidget()
            title = getattr(widget, "page_title", None)
            # An EMPTY icon, like every entry in PAGES. Passing a name here
            # printed the word "payroll" beside the title.
            self.header.set_page("", title or "Employee", "", "TIME & PAY")
            self._refresh_page(widget)
            return

        page = PAGES[idx]
        self.header.set_page(page["icon"], page["title"], page["subtitle"],
                             _section_of(page["key"]))

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
        # Reflect it immediately rather than waiting for the 30s refresh.
        # Read from the database rather than incrementing a counter: a
        # session counter starts at zero every launch and disagrees with the
        # cap, which is enforced on the same stored count.
        if result is not None:
            self._update_own_shots(ScreenshotManager.captures_today())

    def _open_salary_page(self):
        """Show the Salaries page, and load it."""
        self._salary_page.refresh()
        self.stack.setCurrentWidget(self._salary_page)

    def _open_employee_page(self, employee: dict):
        """Show one person's page, from the row that was clicked."""
        if not employee:
            return
        self._employee_page.load(employee)
        self.stack.setCurrentWidget(self._employee_page)

    def _open_add_employee(self):
        """Show the Add Employee page, blank.

        RESET FIRST. The page lives in the stack and is reused, so anything
        left on it from last time would arrive prefilled — which is how one
        person's PAN ends up on their colleague's record.
        """
        self._add_employee_page.reset()
        self.stack.setCurrentWidget(self._add_employee_page)

    def _open_summary_page(self, month: str):
        """Show the month's summary, on the page kept for it."""
        if not month:
            return
        self._summary_page.load(month)
        self.stack.setCurrentWidget(self._summary_page)

    def _open_employee_payroll(self, employee_id: str):
        """Show one person's pay history, on the page kept for it.

        ON THE PANEL, NOT ON A TAB. The stack belongs to the panel, and a tab
        that reaches into it is how five Quick Actions once opened the wrong
        page. The payroll tab says who; this decides where.
        """
        if not employee_id:
            return
        self._employee_payroll.load(employee_id)
        self.stack.setCurrentWidget(self._employee_payroll)

    def _drain_workers(self, timeout_ms: int = 3000) -> int:
        """
        Chal rahe network workers ka bounded intezaar (logout se pehle).

        BUG FIX: Qt me chalte hue QThread ka object destroy hone par
        "QThread: Destroyed while thread is still running" -> std::terminate
        -> app crash. Admin panel har tab pe workers banata hai; slow server
        pe logout dabate hi ye crash trigger ho sakta tha.
        """
        pending = []
        for attr in self.TAB_ATTRS:
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

    def _stop_tab_work(self):
        """Stop the TABS' timers and wait for their workers.

        Deliberately separate from _stop_background_services: rebuilding the
        window for a theme change must not touch the scheduler or the idle
        tracker, which have nothing to do with what the window looks like and
        which nothing restarts.
        """
        for tab_attr in self.TAB_ATTRS:
            tab = getattr(self, tab_attr, None)
            if tab is None:
                continue
            # A PAGE THAT KNOWS HOW TO STOP ITSELF IS ASKED TO.
            #
            # The sweep below only finds timers with one of three names, which
            # is a list that has to be remembered — the employee page runs a
            # one-second clock and a ten-second refetch under names that are
            # not on it, and both would have gone on running after the window
            # closed. Anything with a stop() gets it called; the sweep stays
            # for the pages that do not have one.
            stopper = getattr(tab, "stop", None)
            if callable(stopper):
                try:
                    stopper()
                except Exception:
                    pass
            for timer_attr in ('_refresh_timer', '_charts_timer', '_search_timer'):
                timer = getattr(tab, timer_attr, None)
                if timer is not None:
                    try:
                        timer.stop()
                    except Exception:
                        pass
            for w in list(getattr(tab, '_workers', []) or []):
                try:
                    if w.isRunning():
                        w.wait(300)
                except Exception:
                    pass

    def _on_chat_messages(self, arrived: list):
        # Switched off for messages on this machine — see My Profile.
        if not notifier.pref_enabled(notifier.PREF_CHAT):
            return

        """The same rules as the employee panel, from the same place.

        An admin is somebody's colleague as well as an administrator, and a
        message to them should read the same either side. Two copies of these
        rules would have drifted the first time one was changed.
        """
        if not arrived:
            return

        chat_tab = getattr(self, "_mychat_tab", None)
        looking_at = getattr(chat_tab, "_channel_id", None) if chat_tab else None
        on_top = self.isActiveWindow() and self.stack.currentWidget() is chat_tab

        names = {}
        directs = set()
        for team in getattr(chat_tab, "_teams", []) or []:
            for channel in team.get("channels") or []:
                names[channel["id"]] = f"#{channel['name']}"
        for direct in getattr(chat_tab, "_directs", []) or []:
            names[direct["channel_id"]] = (direct.get("with") or {}).get("name") or ""
            directs.add(direct["channel_id"])

        for item in notifier.collapse(notifier.for_messages(
                arrived,
                me=SessionManager.employee_id,
                open_channel_id=looking_at,
                window_active=on_top,
                channel_names=names,
                direct_channel_ids=directs)):
            self._notify_tray(item["title"], item["body"], item["kind"])

    def _on_chat_alerts(self, alerts: list):
        # Switched off for alerts on this machine — see My Profile.
        if not notifier.pref_enabled(notifier.PREF_ALERTS):
            return

        for item in notifier.collapse(notifier.for_alerts(
                alerts, role=getattr(SessionManager, "role", "admin"))):
            self._notify_tray(item["title"], item["body"], item["kind"])

    def _notify_tray(self, title: str, body: str, kind: str = notifier.NORMAL):
        # Same delivery path as the employee panel — see notifier.deliver for
        # why macOS needs a second door.
        notifier.deliver(getattr(self, "tray", None), title, body)

    def _stop_background_services(self):
        """Sirf timers/threads/workers rokta hai — session ko touch nahi
        karta. closeEvent aur logout() dono isko use karte hain."""
        chat = getattr(self, "chat", None)
        if chat is not None:
            try:
                chat.stop()
            except Exception:
                pass
        if hasattr(self, 'scheduler'):
            self.scheduler.stop()
        if hasattr(self, 'idle_tracker'):
            self.idle_tracker.stop()

        for tab_attr in self.TAB_ATTRS:
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

    def logout(self, reason: str = ""):
        """Sign out. `reason` is set when the SERVER ended the session.

        force_logout carries it — suspension, or an admin's force logout.
        Without showing it the app just returns to the login screen for no
        stated cause, which reads as a crash and gets reported as one.
        """
        # The guard comes FIRST. Two watchers can notice the same forced
        # logout a second apart, and the message box was outside it — so the
        # second one put a dialog on screen after the panel had already gone.
        #
        # AND IT LETS GO IF THE SIGN-OUT DOES NOT FINISH. It is a one-way
        # latch otherwise: anything raising between here and the login window
        # leaves it set, and every later click of Logout returns at this line
        # — a button that does nothing, with no way back but restarting the
        # app. Reported from a real machine.
        if self._logging_out:
            return
        self._logging_out = True
        try:
            self._do_logout(reason)
        except Exception as error:
            self._logging_out = False
            try:
                LoggerService.log(f"LOGOUT FAILED : {error}")
            except Exception:
                pass
            raise

    def _do_logout(self, reason: str = ""):

        if reason:
            try:
                QMessageBox.warning(self, "Signed out", reason)
            except Exception:
                pass
        from client.application.managers.session_manager import SessionManager
        from client.application.managers.shift_manager import ShiftManager
        from client.application.managers.session_log_manager import SessionLogManager
        from client.presentation.windows.login_window import LoginWindow

        # The server first, while the token still works — see the note in
        # employee_panel.logout. Not when the server ended it itself.
        if not reason:
            from client.application.services.auth_service import AuthService
            AuthService.sign_out_on_server()

        self._stop_background_services()

        # BUG FIX: admin panel se logout karne par LOGOUT kabhi log nahi hota
        # tha (employee dashboard aur system tray dono me hota hai). Is wajah
        # se admin/super-admin ka session end kabhi Audit Logs me record hi
        # nahi hota tha — audit trail me gap.
        # clear_session() se PEHLE, warna employee_id None ho jaata hai aur
        # LoggerService.log() chup-chaap return kar deta hai.
        try:
            LoggerService.log(f"LOGOUT : {reason or 'signed out from the console'}")
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
                    "Amaze Connect",
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
        self.tray.setToolTip("Amaze Connect — Control Center")

        menu = QMenu()
        act_open = QAction("Open Control Center", menu)
        act_open.triggered.connect(self._restore_from_tray)
        menu.addAction(act_open)

        act_refresh = QAction(_icons.icon("refresh-cw", 15, C["text_primary"]), "Refresh Current Page", menu)
        act_refresh.triggered.connect(self._refresh_current_page)
        menu.addAction(act_refresh)

        menu.addSeparator()

        act_logout = QAction(_icons.icon("log-out", 15, C["danger"]), "Logout", menu)
        act_logout.triggered.connect(self.logout)
        menu.addAction(act_logout)

        act_quit = QAction("Quit Amaze Connect", menu)
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

