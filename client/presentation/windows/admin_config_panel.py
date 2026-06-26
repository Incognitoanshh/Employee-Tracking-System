from __future__ import annotations

import ast
import requests
from datetime import date, datetime
from datetime import datetime, timezone
from PySide6.QtCore    import Qt, QThread, Signal, QDate, QTimer
from client.presentation.windows.screenshot_preview_window import ScreenshotPreviewWindow
from PySide6.QtGui     import QFont, QColor
from PySide6.QtWidgets import (
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
    QHeaderView
)

from client.application.managers.session_manager import SessionManager
from client.application.schedulers.scheduler_service import SchedulerService
from client.application.managers.screenshot_manager import ScreenshotManager
from client.application.managers.idle_tracker import IdleTracker
from client.core.config import API_BASE_URL


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
    QTableWidget::item {{ padding: 6px 10px; border-bottom: 1px solid {C['border']}; }}
    QHeaderView::section {{
        background: {C['bg_surface_alt']};
        color: {C['text_secondary']};
        padding: 10px 10px;
        border: none;
        border-bottom: 1px solid {C['border']};
        font-weight: 600;
        font-size: 12px;
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


def _card(padding: int = 0) -> QFrame:
    f = QFrame()
    f.setStyleSheet(
        f"QFrame {{ background: {C['bg_surface']}; border: 1px solid {C['border']}; border-radius: 14px; }}"
    )
    return f


def _muted_label(text: str) -> QLabel:
    l = QLabel(text)
    l.setStyleSheet(f"color:{C['text_secondary']}; font-size:12px; font-weight:600; background:transparent;")
    return l


def _divider() -> QFrame:
    d = QFrame()
    d.setFixedHeight(1)
    d.setStyleSheet(f"background:{C['border']};")
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

class StatCard(QFrame):
    """Premium dashboard metric card: icon badge + big value + label."""

    def __init__(self, label: str, accent: str, icon: str = "●", value="—"):
        super().__init__()
        self.setStyleSheet(
            f"QFrame {{ background: {C['bg_surface']}; border: 1px solid {C['border']}; border-radius: 14px; }}"
        )
        self.setMinimumHeight(100)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(8)

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

        _shadow(self, blur=26, dy=10, alpha=55)

    def set_value(self, value):
        self._value_label.setText(str(value))


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
        print("[CSV EXPORT ERROR]", e)
        return False


class _FetchWorker(QThread):
    finished = Signal(dict)
    error    = Signal(str)

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
            self.finished.emit(r.json())
        except Exception as e:
            self.error.emit(str(e))


class _PostWorker(QThread):
    finished = Signal(dict)
    error    = Signal(str)

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
            self.finished.emit(r.json())
        except Exception as e:
            self.error.emit(str(e))

class _DeleteWorker(QThread):
    finished = Signal(dict)
    error = Signal(str)

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
                self.finished.emit(data)
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

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(18)

        # Employee selector toolbar
        toolbar = _card()
        t_lay = QHBoxLayout(toolbar)
        t_lay.setContentsMargins(18, 14, 18, 14)
        t_lay.setSpacing(12)

        t_lay.addWidget(_muted_label("Employee"))
        self._emp_combo = QComboBox()
        self._emp_combo.setMinimumWidth(260)
        self._emp_combo.currentIndexChanged.connect(self._on_employee_changed)
        t_lay.addWidget(self._emp_combo)
        t_lay.addStretch()

        refresh_btn = _btn("↻  Refresh", variant="secondary", height=34, width=110)
        refresh_btn.clicked.connect(self._load_employees)
        t_lay.addWidget(refresh_btn)

        root.addWidget(toolbar)

        # Settings rows
        self._min_spin = QSpinBox()
        self._min_spin.setRange(1, 60)
        self._min_spin.setSuffix(" min")
        self._min_spin.setMinimumWidth(120)

        self._max_spin = QSpinBox()
        self._max_spin.setRange(1, 120)
        self._max_spin.setSuffix(" min")
        self._max_spin.setMinimumWidth(120)

        self._cnt_spin = QSpinBox()
        self._cnt_spin.setRange(1, 20)
        self._cnt_spin.setMinimumWidth(120)

        self._upl_spin = QSpinBox()
        self._upl_spin.setRange(1, 240)
        self._upl_spin.setSuffix(" min")
        self._upl_spin.setMinimumWidth(120)

        self._idle_spin = QSpinBox()
        self._idle_spin.setRange(10, 600)
        self._idle_spin.setSuffix(" sec")
        self._idle_spin.setMinimumWidth(120)

        self._verbose_check = QCheckBox()
        self._verbose_check.setMinimumWidth(120)

        rows_data = [
            ("Screenshot min interval", "Shortest gap allowed before the next capture.", self._min_spin),
            ("Screenshot max interval", "Longest gap allowed between captures.",          self._max_spin),
            ("Screenshots per shift",   "Maximum captures taken in a single shift.",       self._cnt_spin),
            ("Upload interval",         "How often captured data syncs to the server.",    self._upl_spin),
            ("Idle threshold",          "Seconds of inactivity before marked idle.",        self._idle_spin),
            ("Verbose logging",         "Log every sync/schedule event. Turn on only while debugging an issue.", self._verbose_check),
        ]

        form_card = _card()
        form_vbox = QVBoxLayout(form_card)
        form_vbox.setContentsMargins(22, 6, 22, 6)
        form_vbox.setSpacing(0)

        for idx, (label_text, desc, spin_widget) in enumerate(rows_data):
            row = QHBoxLayout()
            row.setSpacing(12)
            row.setContentsMargins(0, 16, 0, 16)

            text_col = QVBoxLayout()
            text_col.setSpacing(2)
            lbl = QLabel(label_text)
            lbl.setStyleSheet(f"color:{C['text_primary']}; font-size:13px; font-weight:600; background:transparent;")
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet(f"color:{C['text_muted']}; font-size:11px; background:transparent;")
            text_col.addWidget(lbl)
            text_col.addWidget(desc_lbl)

            row.addLayout(text_col, 1)
            row.addWidget(spin_widget)
            form_vbox.addLayout(row)

            if idx < len(rows_data) - 1:
                form_vbox.addWidget(_divider())

        root.addWidget(form_card)

        # Save
        btn_row = QHBoxLayout()
        self._save_btn = _btn("💾  Save Config", variant="primary", height=40, width=170)
        self._save_btn.clicked.connect(self._save_config)
        btn_row.addWidget(self._save_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("font-size:12px; background:transparent;")
        root.addWidget(self._status_label)
        root.addStretch()

    # Data loading

    def _load_employees(self):
        self._status_label.setText("Employees load ho rahe hain…")
        w = _FetchWorker(f"{API_BASE_URL}/admin/employees")
        w.finished.connect(self._on_employees_loaded)
        w.error.connect(lambda e: self._status_label.setText(f"Error: {e}"))
        self._workers.append(w)
        w.start()

    def _on_employees_loaded(self, data: dict):
        print("EMPLOYEE API RESPONSE =", data)
        self._employees = data.get("data", [])
        self._emp_combo.blockSignals(True)
        self._emp_combo.clear()
        self._emp_combo.addItem("🌐  Global Default", "global")
        for emp in self._employees:
            label = f"{emp.get('full_name', '?')}  ({emp.get('employee_id', '')})"
            self._emp_combo.addItem(label, emp.get("employee_id"))
        self._emp_combo.blockSignals(False)
        self._on_employee_changed()
        self._status_label.setText("")

    def _on_employee_changed(self):
        emp_id = self._emp_combo.currentData() or "global"
        w = _FetchWorker(f"{API_BASE_URL}/admin/config/{emp_id}")
        w.finished.connect(self._populate_form)
        w.error.connect(lambda e: self._status_label.setText(f"Config load error: {e}"))
        self._workers.append(w)
        w.start()

    def _populate_form(self, data: dict):
        cfg = data.get("config", {})
        self._min_spin.setValue(cfg.get("screenshot_min_minutes",  3))
        self._max_spin.setValue(cfg.get("screenshot_max_minutes",  10))
        self._cnt_spin.setValue(cfg.get("screenshot_count",        3))
        self._upl_spin.setValue(cfg.get("upload_interval_minutes", 60))
        self._idle_spin.setValue(cfg.get("idle_threshold_seconds", 60))
        self._verbose_check.setChecked(bool(cfg.get("verbose_logging", False)))

    # Actions

    def _save_config(self):
        emp_id = self._emp_combo.currentData()
        body = {
            "screenshot_min_minutes":  self._min_spin.value(),
            "screenshot_max_minutes":  self._max_spin.value(),
            "screenshot_count":        self._cnt_spin.value(),
            "upload_interval_minutes": self._upl_spin.value(),
            "idle_threshold_seconds":  self._idle_spin.value(),
            "verbose_logging":        self._verbose_check.isChecked(),
        }
        if emp_id and emp_id != "global":
            body["employee_id"] = emp_id

        self._save_btn.setEnabled(False)
        self._save_btn.setText("Saving…")
        w = _PostWorker(f"{API_BASE_URL}/admin/config", body)
        w.finished.connect(self._on_save_done)
        w.error.connect(lambda e: (
            self._status_label.setText(f"❌ Error: {e}"),
            self._save_btn.setEnabled(True),
            self._save_btn.setText("💾  Save Config"),
        ))
        self._workers.append(w)
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
        w.finished.connect(lambda d: self._status_label.setText(
            "✅ Force logout set!" if d.get("success") else f"❌ {d.get('error')}"
        ))
        w.error.connect(lambda e: self._status_label.setText(f"❌ {e}"))
        self._workers.append(w)
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

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["ID", "Employee", "File", "Timestamp"])
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 50)
        self._table.setColumnWidth(1, 90)
        self._table.setColumnWidth(3, 200)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(38)
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
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
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
        w.finished.connect(self._populate)
        w.error.connect(lambda e: print("Screenshots error:", e))
        self._workers.append(w)
        w.start()

    def _populate(self, data: dict):
        print("SCREENSHOTS RESPONSE =", data)
        rows  = data.get("data", [])
        total = data.get("total", 0)
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(str(row.get("id", ""))))
            self._table.setItem(i, 1, QTableWidgetItem(row.get("employee_id", "")))
            item = QTableWidgetItem(row.get("file_name", ""))
            item.setData( Qt.ItemDataRole.UserRole,row.get("file_name", ""))
            self._table.setItem(i, 2, item)
            ts = row.get("created_at", "")

            try:
                dt = datetime.fromisoformat(
                    ts.replace("Z", "+00:00")
                )

                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)

                    ts = dt.astimezone().strftime(
                        "%d %b %Y %I:%M:%S %p"
                    )

            except Exception:
                pass
            
            self._table.setItem(i, 3, QTableWidgetItem(ts))
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
        filename = file_item.text() if file_item else "?"
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

        # Auto refresh every 5 seconds
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
        self._refresh_timer.timeout.connect(self._load_all)
        self._refresh_timer.start()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(20)

        grid = QGridLayout()
        grid.setSpacing(16)

        self._card_total_employees = StatCard("Total Employees",       ACCENTS["blue"],   "👥")
        self._card_online          = StatCard("Online Now",            ACCENTS["green"],  "🟢")
        self._card_offline         = StatCard("Offline",                ACCENTS["slate"],  "🌙")
        self._card_total_screens   = StatCard("Screenshots Captured",  ACCENTS["violet"], "📸")
        self._card_total_logs      = StatCard("Activity Logs",         ACCENTS["cyan"],   "📝")

        for i, c in enumerate([
            self._card_total_employees,
            self._card_online,
            self._card_offline,
            self._card_total_screens,
            self._card_total_logs,
        ]):
            grid.addWidget(c, 0, i)

        root.addLayout(grid)

        # Charts Section
        charts_header = QLabel("Last 7 Days Overview")
        charts_header.setStyleSheet(f"color:{C['text_primary']}; font-weight:700; font-size:14px; background:transparent;")
        root.addWidget(charts_header)
        charts_row = QHBoxLayout()
        charts_row.setSpacing(16)
        self._chart_screenshots = _BarChartWidget("Screenshots / Day", ACCENTS["violet"])
        self._chart_attendance  = _BarChartWidget("Active Employees / Day", ACCENTS["green"])
        self._chart_activity    = _BarChartWidget("Activity Logs / Day", ACCENTS["cyan"])
        charts_row.addWidget(self._chart_screenshots)
        charts_row.addWidget(self._chart_attendance)
        charts_row.addWidget(self._chart_activity)
        root.addLayout(charts_row)

        # Recent Activity Feed
        feed_header = QLabel("Recent Activity")
        feed_header.setStyleSheet(f"color:{C['text_primary']}; font-weight:700; font-size:14px; background:transparent;")
        root.addWidget(feed_header)
        self._feed = QListWidget()
        self._feed.setMaximumHeight(150)
        root.addWidget(self._feed)


    def _load_charts(self):
        w = _FetchWorker(f"{API_BASE_URL}/dashboard/charts")
        w.finished.connect(self._on_charts)
        w.error.connect(lambda e: print("Charts error:", e))
        self._workers.append(w)
        w.start()

    def _on_charts(self, data: dict):
        print("[CHARTS DATA RECEIVED]", data)
        d = data.get("data", {})
        self._chart_screenshots.set_data(d.get("screenshots_per_day", []))
        self._chart_attendance.set_data(d.get("attendance_per_day", []))
        self._chart_activity.set_data(d.get("activity_per_day", []))

    def _load_all(self):
        self._load_summary()
        self._load_feed()
        self._load_charts()

    def _load_summary(self):
        url = f"{API_BASE_URL}/dashboard/summary"
        print("[ADMIN DASHBOARD] GET", url)
        w = _FetchWorker(url)

        w.finished.connect(self._on_summary)
        w.error.connect(lambda e: print("[SUMMARY ERROR]", e))
        self._workers.append(w)
        w.start()

    def _load_feed(self):
        w = _FetchWorker(f"{API_BASE_URL}/dashboard/recent-activity", params={"limit": 50})

        w.finished.connect(self._on_feed)
        w.error.connect(lambda e: print("Dashboard feed error:", e))
        self._workers.append(w)
        w.start()

    def _on_summary(self, data: dict):
        # Debug: print exact payload so we can bind to the real keys.
        print("[ADMIN SUMMARY RESPONSE]", data)

        s = data.get("data", data)

        self._card_total_employees.set_value(s.get('total_employees', '—'))
        self._card_online.set_value(s.get('online_employees', '—'))
        self._card_offline.set_value(s.get('offline_employees', '—'))
        self._card_total_screens.set_value(s.get('total_screenshots', '—'))
        self._card_total_logs.set_value(s.get('total_activity_logs', '—'))

    def _on_feed(self, data: dict):
        rows = data.get("data", data).get("recent_activity", []) if isinstance(data, dict) else []
        if rows is None:
            rows = []
        self._feed.clear()
        for r in rows:
            # Expect shape: { 'message': str, 'created_at': optional }
            msg = r.get("message") if isinstance(r, dict) else str(r)
            self._feed.addItem(msg)


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

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["ID", "Employee", "Activity", "Timestamp"])
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 60)
        self._table.setColumnWidth(1, 90)
        self._table.setColumnWidth(3, 200)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(38)
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
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
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
        w.finished.connect(self._populate)
        w.error.connect(lambda e: print("Logs error:", e))
        self._workers.append(w)
        w.start()

    def _populate(self, data: dict):
        print("LOGS RESPONSE =", data)
        rows  = data.get("data", [])
        self._logs = rows
        total = data.get("total", 0)
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(str(row.get("id", ""))))
            self._table.setItem(i, 1, QTableWidgetItem(row.get("employee_id", "")))
            self._table.setItem(i, 2, QTableWidgetItem(row.get("activity", "")))
            ts = row.get("created_at", "")

            try:
                dt = datetime.fromisoformat(
                    ts.replace("Z", "+00:00")
                )

                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)

                ts = dt.astimezone().strftime(
                    "%d %b %Y %I:%M:%S %p"
                )

            except Exception:
                pass

            self._table.setItem(i, 3, QTableWidgetItem(ts))
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

        headers = ["ID", "Employee ID", "Activity", "Timestamp"]
        rows = []
        for row in self._logs:
            rows.append([
                row.get("id", ""),
                row.get("employee_id", ""),
                row.get("activity", ""),
                row.get("created_at", ""),
            ])

        if _export_to_csv(path, headers, rows):
            QMessageBox.information(self, "Export", f"Exported {len(self._logs)} logs to:\n{path}")
        else:
            QMessageBox.warning(self, "Export", "Failed to export CSV.")

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
        search_btn.clicked.connect(self._load)
        filter_row.addWidget(search_btn)

        self._export_btn = _btn("📥  Export CSV", variant="secondary", height=34, width=140)
        self._export_btn.clicked.connect(self._export_attendance_csv)
        filter_row.addWidget(self._export_btn)

        filter_row.addStretch()
        root.addWidget(toolbar)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["ID", "Employee", "Login Time", "Logout Time", "Total Hours"])
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 50)
        self._table.setColumnWidth(1, 90)
        self._table.setColumnWidth(4, 110)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(38)
        root.addWidget(self._table, 1)

    def _load(self):
        emp = self._emp_filter.text().strip()
        params = {}
        if emp:
            params["employee_id"] = emp
        w = _FetchWorker(f"{API_BASE_URL}/attendance/all", params)
        w.finished.connect(self._populate)
        w.error.connect(lambda e: print("Attendance error:", e))
        self._workers.append(w)
        w.start()

    def _populate(self, data: dict):
        print("ATTENDANCE RESPONSE =", data)
        rows = data.get("data", [])
        self._attendance = rows
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(str(row.get("id", ""))))
            self._table.setItem(i, 1, QTableWidgetItem(row.get("employee_id", "")))

            login_ts = row.get("login_time", "")
            try:
                dt = datetime.fromisoformat(str(login_ts).replace("Z", "+00:00"))
                login_ts = dt.astimezone().strftime("%d %b %Y %I:%M:%S %p")
            except Exception:
                pass
            self._table.setItem(i, 2, QTableWidgetItem(login_ts))

            logout_ts = row.get("logout_time", "")
            if logout_ts:
                try:
                    dt = datetime.fromisoformat(str(logout_ts).replace("Z", "+00:00"))
                    logout_ts = dt.astimezone().strftime("%d %b %Y %I:%M:%S %p")
                except Exception:
                    pass
            else:
                logout_ts = "—"
            self._table.setItem(i, 3, QTableWidgetItem(logout_ts))

            total_hours = self._format_total_hours(row.get("total_hours"))
            self._table.setItem(i, 4, QTableWidgetItem(total_hours))

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
                h = int(d.get("hours", 0))
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

        headers = ["ID", "Employee ID", "Login Time", "Logout Time", "Total Hours"]
        rows = []
        for row in self._attendance:
            rows.append([
                row.get("id", ""),
                row.get("employee_id", ""),
                row.get("login_time", ""),
                row.get("logout_time", ""),
                self._format_total_hours(row.get("total_hours")),
            ])

        if _export_to_csv(path, headers, rows):
            QMessageBox.information(self, "Export", f"Exported {len(self._attendance)} records to:\n{path}")
        else:
            QMessageBox.warning(self, "Export", "Failed to export CSV.")


class EmployeeDetailsDialog(QDialog):
    def __init__(self, employee: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Employee Details")
        self.setMinimumWidth(760)
        self.setStyleSheet(f"QDialog {{ background: {C['bg_app']}; }}")
        self._employee = employee

        self._workers: list[QThread] = []

        self._build_ui()
        self._load_details()

        # Live timers (UI only). Backend values are fetched periodically.
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(1000)
        self._live_timer.timeout.connect(self._tick_live_times)
        print("[LIVE TIMER STARTED]")
        self._live_timer.start()

        self._details_refresh_timer = QTimer(self)
        self._details_refresh_timer.setInterval(10000)  # 10 seconds
        self._details_refresh_timer.timeout.connect(self._load_details)
        self._details_refresh_timer.start()

        # Guard flag to prevent token error popup spam
        self._token_error_shown = False

        self._live_active_seconds = 0
        self._live_idle_seconds = 0
        self._live_state = None  # "ACTIVE" | "IDLE" | None
        self._employee_online = False




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

        self._logs_table = QTableWidget(0, 2)
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
            self._logs_table.setItem(i, 0, QTableWidgetItem(str(t)))
            self._logs_table.setItem(i, 1, QTableWidgetItem(str(a)))

    def _load_details(self):
        emp_id = self._employee.get('employee_id')
        if not emp_id:
            return

        print("[EmployeeDetails] employee_id passed:", emp_id)
        url = f"{API_BASE_URL}/admin/employee/{emp_id}"
        print("[EmployeeDetails] API URL:", url)
        w = _FetchWorker(url)
        w.finished.connect(self._on_details)
        # Guard to prevent popup spam on worker errors
        def _on_worker_error(e: str):
            if self._token_error_shown:
                return
            print("[EmployeeDetails] ERROR:", e)
            self._token_error_shown = True
            self._live_timer.stop()
            self._details_refresh_timer.stop()
            QMessageBox.warning(self, "Error", f"Failed to load details: {e}")

        w.error.connect(_on_worker_error)

        self._workers.append(w)
        w.start()

    # AFTER
    def _on_details(self, data: dict):
        print("[EmployeeDetails] raw JSON response:", data)
        print("[EmployeeDetails] success:", data.get('success'), "data:", data.get('data'))

        # FIX: Handle expired token error with guard to prevent popup spam
        if not data.get('success'):
            error_msg = data.get('message', 'Unknown error')
            print("[EmployeeDetails] ERROR:", error_msg)

            # Only show the popup once
            if not self._token_error_shown:
                self._token_error_shown = True

                # Stop all timers and workers to prevent further errors
                self._live_timer.stop()
                self._details_refresh_timer.stop()
                for w in self._workers:
                    w.quit()
                    w.wait(1000)

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

        if self._employee_online:
            self._live_state = "ACTIVE"
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
            print("[TICK] FROZEN (employee offline)",
                self._live_active_seconds, self._live_idle_seconds)
            return

        if self._live_state == "ACTIVE":
            self._live_active_seconds += 1
        elif self._live_state == "IDLE":
            self._live_idle_seconds += 1

        print("[TICK]", self._live_active_seconds, self._live_idle_seconds)
        self._active_time.set_value(self._seconds_to_hhmmss(self._live_active_seconds))
        self._idle_time.set_value(self._seconds_to_hhmmss(self._live_idle_seconds))

    # Clean up timers and workers when dialog closes
    def closeEvent(self, event):
        print("[EmployeeDetails] closeEvent - stopping timers and workers")
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

        self._build_ui()
        self._load_employees()

        # Auto refresh every 5 seconds
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
        self._refresh_timer.timeout.connect(self._load_employees)
        self._refresh_timer.start()

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

        self._export_btn = _btn("📥  Export CSV", variant="secondary", height=38, width=140)
        self._export_btn.clicked.connect(self._export_employees_csv)
        header.addWidget(self._export_btn)

        add_btn = _btn("+  Add Employee", variant="primary", height=38, width=160)
        add_btn.clicked.connect(self._add_employee)
        header.addWidget(add_btn)

        root.addLayout(header)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            "Employee ID", "Username", "Role", "Status", "Last Seen", "Actions"
        ])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(48)
        root.addWidget(self._table, 1)

    def _status_to_text_color(self, status: str):
        s = (status or "").lower()
        if s in ("online", "online_user"):
            return "🟢 Online", C["success"]
        if s in ("idle", "idling"):
            return "🟡 Idle", C["warning"]
        if s in ("offline", "logged_out", "disconnected"):
            return "🔴 Offline", C["danger"]
        return f"{status}", C["text_secondary"]

    def _load_employees(self):
        w = _FetchWorker(f"{API_BASE_URL}/admin/employees")
        w.finished.connect(self._on_employees_loaded)
        w.error.connect(lambda e: print("Employees load error:", e))
        self._workers.append(w)
        w.start()

    def _on_employees_loaded(self, data: dict):
        self._rows = data.get('data', []) if isinstance(data, dict) else []
        self._apply_filter()

    def _on_search_changed(self, text: str):
        self._search_text = text.lower().strip()
        self._apply_filter()

    def _apply_filter(self):
        # Apply filter to self._rows and redisplay
        if not self._search_text:
            filtered = self._rows
        else:
            filtered = [
                emp for emp in self._rows
                if (self._search_text in (emp.get('employee_id') or "").lower())
                or (self._search_text in (emp.get('username') or "").lower())
                or (self._search_text in (emp.get('role') or "").lower())
            ]

        self._display_employees(filtered)

    def _export_employees_csv(self):
        # Get filtered data (current search results)
        if not self._search_text:
            filtered = self._rows
        else:
            filtered = [
                emp for emp in self._rows
                if (self._search_text in (emp.get('employee_id') or "").lower())
                or (self._search_text in (emp.get('username') or "").lower())
                or (self._search_text in (emp.get('role') or "").lower())
            ]

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
        headers = ["Employee ID", "Username", "Role", "Status", "Last Seen"]
        rows = []
        for emp in filtered:
            status_text, _ = self._status_to_text_color(emp.get('status'))
            rows.append([
                emp.get('employee_id', ''),
                emp.get('username', ''),
                emp.get('role', ''),
                status_text,
                emp.get('last_seen', '—'),
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
            
            from datetime import datetime, timezone

            raw_last_seen = emp.get("last_seen")

            if raw_last_seen:
                try:
                    dt = datetime.fromisoformat(
                        str(raw_last_seen).replace("Z", "+00:00")
                    )

                    now = datetime.now(timezone.utc)
                    diff = int((now - dt).total_seconds())

                    if diff < 60:
                        last_seen = "Just now"
                    elif diff < 3600:
                        last_seen = f"{diff // 60} min ago"
                    elif diff < 86400:
                        last_seen = f"{diff // 3600} hr ago"
                    else:
                        last_seen = dt.strftime("%d %b %Y %I:%M %p")

                except Exception:
                    last_seen = str(raw_last_seen)
            else:
                last_seen = "—"

            self._table.setItem(i, 0, QTableWidgetItem(str(emp_id)))
            self._table.setItem(i, 1, QTableWidgetItem(str(username)))
            self._table.setItem(i, 2, QTableWidgetItem(str(role)))

            st_item = QTableWidgetItem(status_text)
            st_item.setForeground(QColor(status_color))
            font = st_item.font()
            font.setBold(True)
            st_item.setFont(font)
            self._table.setItem(i, 3, st_item)

            self._table.setItem(i, 4, QTableWidgetItem(str(last_seen)))

            # Actions
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(6, 4, 6, 4)
            actions_layout.setSpacing(8)

            view_btn = _btn("View Details", variant="secondary", height=30, width=104)
            view_btn.clicked.connect(lambda _=False, e=emp: self._open_details(e))

            is_verbose = bool(emp.get("verbose_logging"))
            verbose_btn = _btn(
                "🔔 Verbose: ON" if is_verbose else "🔕 Verbose: OFF",
                variant="warning" if is_verbose else "secondary",
                height=30, width=128,
            )
            verbose_btn.clicked.connect(lambda _=False, e=emp: self._toggle_verbose(e))

            force_btn = _btn("Force Logout", variant="warning", height=30, width=108)
            force_btn.clicked.connect(lambda _=False, e=emp: self._force_logout(e))

            delete_btn = _btn("Delete", variant="danger-solid", height=30, width=72)
            delete_btn.clicked.connect(lambda _=False, e=emp: self._delete_employee(e))

            actions_layout.addWidget(view_btn)
            actions_layout.addWidget(verbose_btn)
            actions_layout.addWidget(force_btn)
            actions_layout.addWidget(delete_btn)
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
        w.finished.connect(lambda d: QMessageBox.information(
            self,
            "Force Logout",
            "✅ Force logout set!" if d.get('success') else f"❌ {d.get('error')}"
        ))
        w.error.connect(lambda e: QMessageBox.warning(self, "Error", f"Force logout failed: {e}"))
        self._workers.append(w)
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
        w.finished.connect(lambda d: (
            self._load_employees() if d.get("success")
            else QMessageBox.warning(self, "Error", f"❌ {d.get('error', 'Toggle failed')}")
        ))
        w.error.connect(lambda e: QMessageBox.warning(self, "Error", f"Toggle failed: {e}"))
        self._workers.append(w)
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

        w.finished.connect(
            lambda d: (
                QMessageBox.information(
                    self,
                    "Success",
                    "Employee deleted"
                ),
                self._load_employees()
            )
        )

        self._workers.append(w)
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

        role.addItems(["employee", "admin"])

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

            worker.finished.connect(
                lambda d: (
                    QMessageBox.information(
                        self,
                        "Success",
                        "Employee created successfully"
                    ),
                    dlg.accept(),
                    self._load_employees()
                )
            )

            worker.error.connect(
                lambda e: QMessageBox.warning(
                    self,
                    "Error",
                    str(e)
                )
            )

            self._workers.append(worker)
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
        self._user_searched = False
        self._build()

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
        avatar = QLabel("A")
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(f"background:{C['accent']}; color:white; border-radius:16px; font-weight:700;")

        role_col = QVBoxLayout()
        role_col.setSpacing(0)
        name = QLabel("Administrator")
        name.setStyleSheet(f"color:{C['text_primary']}; font-size:12px; font-weight:700; background:transparent;")
        role = QLabel("Full Access")
        role.setStyleSheet(f"color:{C['text_muted']}; font-size:10px; background:transparent;")
        role_col.addWidget(name)
        role_col.addWidget(role)

        role_row.addWidget(avatar)
        role_row.addLayout(role_col)
        role_row.addStretch()
        f_lay.addLayout(role_row)

        self.logout_btn = _btn("🔒  Logout", variant="danger", height=38)
        f_lay.addWidget(self.logout_btn)

        root.addWidget(footer)


class _TopHeader(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("topHeader")
        self.setFixedHeight(76)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(28, 0, 28, 0)
        lay.setSpacing(12)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self._title = QLabel("")
        self._title.setStyleSheet(f"color:{C['text_primary']}; font-size:19px; font-weight:700; background:transparent;")
        self._subtitle = QLabel("")
        self._subtitle.setStyleSheet(f"color:{C['text_secondary']}; font-size:12px; background:transparent;")
        text_col.addWidget(self._title)
        text_col.addWidget(self._subtitle)
        lay.addLayout(text_col)
        lay.addStretch()

        live_wrap = QWidget()
        live_lay = QHBoxLayout(live_wrap)
        live_lay.setContentsMargins(0, 0, 0, 0)
        live_lay.setSpacing(6)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background:{C['success']}; border-radius:4px;")
        live_lbl = QLabel("Live")
        live_lbl.setStyleSheet(f"color:{C['success']}; font-size:11px; font-weight:700; background:transparent;")
        live_lay.addWidget(dot)
        live_lay.addWidget(live_lbl)
        lay.addWidget(live_wrap)

    def set_page(self, icon: str, title: str, subtitle: str):
        self._title.setText(f"{icon}  {title}")
        self._subtitle.setText(subtitle)


class AdminConfigPanel(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ETS — Admin Panel")
        self.setMinimumSize(1080, 680)
        self.resize(1300, 820)
        self._logging_out = False  # Guard flag to prevent recursion
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
        c_lay.addWidget(self.header)

        self.stack = QStackedWidget()
        self.stack.addWidget(_DashboardTab())
        self.stack.addWidget(_ConfigTab())
        self.stack.addWidget(_EmployeesTab())
        self.stack.addWidget(_AttendanceTab())
        self.stack.addWidget(_ScreenshotsTab())
        self.stack.addWidget(_LogsTab())
        c_lay.addWidget(self.stack, 1)

        root.addWidget(content, 1)
        self.setCentralWidget(central)

        self.sidebar.pageChanged.connect(self._on_page_changed)
        self.sidebar.logout_btn.clicked.connect(self.logout)
        self._on_page_changed(0)

        # ── Tracking Services ─────────────────────────────────────
        # FIX: Admin users also need tracking services like regular employees
        self.scheduler = SchedulerService()
        self.scheduler.screenshot_triggered.connect(self.capture_screenshot)
        if hasattr(self.scheduler, "force_logout"):
            self.scheduler.force_logout.connect(self.logout)
        self.scheduler.start()

        self.idle_tracker = IdleTracker()
        self.idle_tracker.start()

    def _on_page_changed(self, idx: int):
        self.stack.setCurrentIndex(idx)
        page = PAGES[idx]
        self.header.set_page(page["icon"], page["title"], page["subtitle"])

    def capture_screenshot(self):
        result = ScreenshotManager.capture_screenshot()
        print(result)

    def logout(self):
        from client.application.managers.session_manager import SessionManager
        from client.application.managers.shift_manager import ShiftManager
        from client.application.managers.session_log_manager import SessionLogManager
        from client.presentation.windows.login_window import LoginWindow

        # Guard flag - prevent duplicate execution
        if self._logging_out:
            return

        self._logging_out = True

        # Stop tracking services (cleanup timers)
        if hasattr(self, 'scheduler'):
            self.scheduler.stop()
        if hasattr(self, 'idle_tracker'):
            self.idle_tracker.stop()

        # Stop all tab refresh timers explicitly (no Qt parent set)
        for tab_attr in ['_config_tab', '_screenshots_tab', '_logs_tab', '_attendance_tab', '_employees_tab', '_dashboard_tab']:
            tab = getattr(self, tab_attr, None)
            if tab and hasattr(tab, '_refresh_timer'):
                tab._refresh_timer.stop()
        # Stop all background workers
        for w in getattr(self, '_workers', []):
            try:
                w.quit()
                w.wait(500)
            except Exception:
                pass

        try:
            SessionLogManager.end_session()
        except Exception as e:
            print("SESSION END ERROR:", e)

        try:
            ShiftManager.end_shift()
        except Exception as e:
            print("SHIFT END ERROR:", e)

        SessionManager.clear_session()

        self.login_window = LoginWindow()
        self.login_window.show()

        # Close the window directly - ignore closeEvent since we handled cleanup
        QMainWindow.close(self)

    def closeEvent(self, event):
        self.logout()
        event.accept()
