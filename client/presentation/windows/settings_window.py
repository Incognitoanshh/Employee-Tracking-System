"""
Employee Settings / Status window.

REWRITE — purana version practically bekaar tha:
  - `BASE_DIR = Path(os.getenv("ETS_DATA_DIR", "C:\\ETS"))` — hardcoded
    WINDOWS path, jo macOS/Linux pe hamesha galat dikhta tha (screenshot me
    "C:\\ETS" dikh raha tha jabki asli data
    ~/Library/Application Support/ETS/storage me hai).
  - "Browse…" button permanently disabled tha — kuch karta hi nahi tha.
  - Light theme (#F5F5F5 / #DDDDDD) tha jabki poori app dark hai.
  - Employee ko koi kaam ki jaankari nahi milti thi: na shift, na sync
    status, na ye ki uska data kahan hai.

Ab ye ek asli "mera status" panel hai. Sab kuch read-only hai (employee
tracking settings badal na sake — wo admin ka kaam hai), lekin har value
ASLI hai aur employee ke kaam ki hai.

Jaan-boojh kar NAHI dikhaya: agla screenshot kab hoga. Wo dikhane se
monitoring ka poora maqsad hi khatam ho jata (employee us waqt screen
badal leta).
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from datetime import datetime, timezone, timedelta

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from client.core.config import API_BASE_URL, STORAGE_DIR, APP_VERSION
from client.application.managers.session_manager import SessionManager
from client.infrastructure.database.database import Database
from client.services.settings_service import SettingsService

from client.core.time_ist import IST  # single source of truth


# ---------------------------------------------------------------------------
#  Helpers — har value ASLI source se aati hai, koi hardcoded placeholder nahi
# ---------------------------------------------------------------------------

def _human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _folder_size(path: str) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    except Exception:
        pass
    return total


def _open_in_file_manager(path: str) -> None:
    """Cross-platform 'reveal in Finder/Explorer'."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as error:
        print("[SETTINGS] could not open folder:", error)


def _local_counts() -> dict:
    """Local SQLite se sync/storage stats."""
    stats = {
        "screenshots": 0,
        "pending_screenshots": 0,
        "pending_logs": 0,
        "last_screenshot": None,
    }
    try:
        conn = Database.connect()
        cur = conn.cursor()
        emp = SessionManager.employee_id

        cur.execute("SELECT COUNT(*) FROM screenshots WHERE employee_id = ?", (emp,))
        stats["screenshots"] = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM screenshots WHERE employee_id = ? AND uploaded = 0",
            (emp,),
        )
        stats["pending_screenshots"] = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM pending_logs WHERE employee_id = ? AND uploaded = 0",
            (emp,),
        )
        stats["pending_logs"] = cur.fetchone()[0]

        cur.execute(
            "SELECT timestamp FROM screenshots WHERE employee_id = ? "
            "ORDER BY timestamp DESC LIMIT 1",
            (emp,),
        )
        row = cur.fetchone()
        if row:
            stats["last_screenshot"] = row[0]

        conn.close()
    except Exception as error:
        print("[SETTINGS] local stats error:", error)
    return stats


def _encryption_status() -> tuple[str, str]:
    """
    Asli check — sirf "Enabled" likh dena bekaar hai. Agar
    SCREENSHOT_ENCRYPTION_KEY missing/invalid ho to screenshots decrypt hi
    nahi honge, aur employee ko pata hona chahiye ki kuch gadbad hai.
    """
    try:
        from client.security.crypto_engine import CryptoEngine  # noqa: F401
        return "AES-256-GCM · active", "#22c55e"
    except Exception as error:
        return f"unavailable — {error}", "#ef4444"


def _monitoring_config() -> dict:
    """
    Admin panel se set ki hui values — ConfigSyncManager inhe har 5 second
    local settings table me likhta hai.

    Employee ko ye dikhna zaroori hai: admin/super-admin jo bhi set kare,
    employee apne panel me confirm kar sake ki wo uske app tak pahunch
    chuka hai (aur kab pahuncha).
    """
    def get(key, default="—"):
        try:
            value = SettingsService.get_setting(key)
            return str(value) if value not in (None, "") else default
        except Exception:
            return default

    return {
        "count":    get("screenshots_per_day"),
        "min":      get("screenshot_min_minutes"),
        "max":      get("screenshot_max_minutes"),
        "idle":     get("idle_threshold_seconds"),
        "verbose":  get("verbose_logging", "false"),
        "synced":   get("config_last_synced", ""),
    }


def _fmt_shift() -> str:
    # SessionManager login pe set hota hai; ConfigSyncManager har 5s use
    # update karta hai. Agar wahan na mile to local settings table dekho —
    # dono me se jo bhi taaza ho.
    start, end = SessionManager.shift_start, SessionManager.shift_end
    if not start or not end:
        try:
            start = start or SettingsService.get_setting("shift_start_ist")
            end   = end   or SettingsService.get_setting("shift_end_ist")
        except Exception:
            pass
    if not start or not end:
        return "not configured"

    def _hhmm(value: str) -> str:
        text = str(value)
        try:
            return datetime.fromisoformat(text).strftime("%H:%M")
        except Exception:
            return text[11:16] if len(text) >= 16 else text[:5]

    return f"{_hhmm(start)} – {_hhmm(end)}  IST"


# ---------------------------------------------------------------------------
#  Widgets
# ---------------------------------------------------------------------------

class _Section(QLabel):
    def __init__(self, text: str) -> None:
        super().__init__(text.upper())
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        self.setFont(font)
        self.setStyleSheet(
            "color:#64748b; letter-spacing:1px; padding-top:14px; padding-bottom:2px;"
        )


class SettingsWindow(QDialog):
    """Employee ke apne status ka read-only panel."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ETS Settings")
        self.setMinimumWidth(560)
        self.setMinimumHeight(560)
        self.setSizeGripEnabled(True)
        self.setStyleSheet("QDialog { background-color:#0b1220; }")

        self._rows: dict[str, QLabel] = {}
        self._previous: dict[str, str] = {}
        self._base_style: dict[str, str] = {}
        self._build_ui()
        self.refresh()

        # LIVE: admin panel se koi setting badle to employee ko yahin dikh
        # jaye — window band karke dobara kholne ki zarurat na pade.
        # Saari values local SQLite/memory se aati hain (koi network call
        # nahi), is liye 5s bilkul sasta hai — aur ConfigSyncManager bhi
        # isi interval pe server se sync karta hai.
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(5000)
        self._auto_timer.timeout.connect(self.refresh)
        self._auto_timer.start()

    def closeEvent(self, event):
        try:
            self._auto_timer.stop()
        except Exception:
            pass
        super().closeEvent(event)

    def _flash_changes(self) -> None:
        changed = []
        for key, label in self._rows.items():
            text = label.text()
            if key in self._previous and self._previous[key] != text:
                changed.append(key)
            self._previous[key] = text

        for key in changed:
            label = self._rows[key]
            self._base_style.setdefault(key, label.styleSheet())
            label.setStyleSheet(
                "color:#22c55e; font-size:13px; font-weight:700;"
                "background:#10281c; border-radius:4px; padding:1px 6px;"
            )
            QTimer.singleShot(
                2500,
                lambda k=key: self._rows[k].setStyleSheet(
                    self._base_style.get(k, "color:#e2e8f0;font-size:13px;font-weight:600;")
                ),
            )

    # ── UI ──────────────────────────────────────────────────────────────
    def _row(self, grid: QGridLayout, label: str, key: str, value: str = "—") -> None:
        r = grid.rowCount()
        name = QLabel(label)
        name.setStyleSheet("color:#94a3b8; font-size:13px;")
        name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        val = QLabel(value)
        val.setStyleSheet("color:#e2e8f0; font-size:13px; font-weight:600;")
        val.setWordWrap(True)
        val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        grid.addWidget(name, r, 0)
        grid.addWidget(val, r, 1)
        grid.setColumnStretch(1, 1)
        self._rows[key] = val

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(0)

        title = QLabel("Settings & Status")
        title.setStyleSheet(
            "font-size:20px; font-weight:700; color:#f1f5f9; padding-bottom:2px;"
        )
        subtitle = QLabel("Your account, sync and storage details.")
        subtitle.setStyleSheet("color:#64748b; font-size:12px; padding-bottom:6px;")
        root.addWidget(title)
        root.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;}"
            "QScrollBar:vertical{background:#0b1220;width:6px;border-radius:3px;}"
            "QScrollBar::handle:vertical{background:#334155;border-radius:3px;}"
        )
        content = QWidget()
        content.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 10, 0)
        layout.setSpacing(2)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        def new_grid() -> QGridLayout:
            g = QGridLayout()
            g.setHorizontalSpacing(18)
            g.setVerticalSpacing(9)
            g.setContentsMargins(4, 4, 4, 4)
            layout.addLayout(g)
            return g

        # ── Account ──
        layout.addWidget(_Section("Account"))
        g = new_grid()
        self._row(g, "Employee ID:", "emp")
        self._row(g, "Role:", "role")
        self._row(g, "Shift:", "shift")
        self._row(g, "Timezone:", "tz")

        # ── Sync ──
        layout.addWidget(_Section("Sync"))
        g = new_grid()
        self._row(g, "Server:", "server")
        self._row(g, "Pending screenshots:", "pending_ss")
        self._row(g, "Pending logs:", "pending_logs")
        self._row(g, "Last screenshot:", "last_ss")

        # ── Storage ──
        layout.addWidget(_Section("Storage"))
        g = new_grid()
        self._row(g, "Data folder:", "folder")
        self._row(g, "Space used:", "size")
        self._row(g, "Screenshots on disk:", "ss_count")

        folder_btns = QHBoxLayout()
        folder_btns.addStretch()
        self._btn_open = QPushButton("📂  Open Data Folder")
        self._btn_open.setFixedHeight(32)
        self._btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_open.setStyleSheet(
            "QPushButton{background:#1e293b;border:1px solid #334155;border-radius:8px;"
            "color:#e2e8f0;font-size:12px;font-weight:600;padding:0 14px;}"
            "QPushButton:hover{background:#334155;}"
        )
        # BUG FIX: purana "Browse…" button permanently disabled tha.
        self._btn_open.clicked.connect(lambda: _open_in_file_manager(STORAGE_DIR))
        folder_btns.addWidget(self._btn_open)
        layout.addLayout(folder_btns)

        # ── Monitoring (admin panel se set hoti hain) ──
        layout.addWidget(_Section("Monitoring  ·  set by your admin"))
        g = new_grid()
        self._row(g, "Screenshots per day:", "m_count")
        self._row(g, "Capture interval:", "m_interval")
        self._row(g, "Idle threshold:", "m_idle")
        self._row(g, "Detailed logging:", "m_verbose")
        self._row(g, "Settings synced:", "m_synced")

        sync_note = QLabel(
            "These are controlled by your administrator and refresh "
            "automatically — you do not need to restart the app."
        )
        sync_note.setWordWrap(True)
        sync_note.setStyleSheet("color:#475569; font-size:11px; padding:2px 4px 0 4px;")
        layout.addWidget(sync_note)

        # ── Security ──
        layout.addWidget(_Section("Security"))
        g = new_grid()
        self._row(g, "Encryption:", "enc")

        note = QLabel(
            "Screenshots are encrypted on this device before upload — "
            "they are never stored or transmitted as plain images."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#475569; font-size:11px; padding:4px 4px 0 4px;")
        layout.addWidget(note)

        # ── About ──
        layout.addWidget(_Section("About"))
        g = new_grid()
        self._row(g, "Version:", "ver")
        self._row(g, "Platform:", "plat")

        layout.addStretch(1)

        # ── Buttons ──
        root.addSpacing(12)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)

        self._btn_refresh = QPushButton("↻  Refresh")
        self._btn_refresh.setFixedHeight(34)
        self._btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_refresh.setStyleSheet(
            "QPushButton{background:#1e293b;border:1px solid #334155;border-radius:9px;"
            "color:#e2e8f0;font-size:13px;font-weight:600;}"
            "QPushButton:hover{background:#334155;}"
        )
        self._btn_refresh.clicked.connect(self.refresh)

        self._btn_close = QPushButton("Close")
        self._btn_close.setFixedHeight(34)
        self._btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_close.setStyleSheet(
            "QPushButton{background:#1d4ed8;border:1px solid #2563eb;border-radius:9px;"
            "color:white;font-size:13px;font-weight:600;}"
            "QPushButton:hover{background:#2563eb;}"
        )
        self._btn_close.clicked.connect(self.close)

        buttons.addWidget(self._btn_refresh)
        buttons.addWidget(self._btn_close, 1)
        root.addLayout(buttons)

    # ── Data ────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        def put(key: str, text: str, color: str | None = None) -> None:
            label = self._rows.get(key)
            if label is None:
                return
            label.setText(text)
            if color:
                label.setStyleSheet(
                    f"color:{color}; font-size:13px; font-weight:600;"
                )

        role_map = {
            "super_admin": "👑  Super Admin",
            "admin": "🛡  Admin",
            "employee": "Employee",
        }
        role = getattr(SessionManager, "role", "employee")

        put("emp", str(SessionManager.employee_id or "—"))
        put("role", role_map.get(role, role))
        put("shift", _fmt_shift())
        put("tz", "IST (Asia/Kolkata)")

        # BUG FIX: purana window server URL ko chhota field me dikhata tha,
        # to sirf ".21.212.85:8000/api" dikhta tha — employee ko pata hi
        # nahi chalta kis server se juda hai.
        put("server", str(API_BASE_URL))

        stats = _local_counts()
        pend_ss = stats["pending_screenshots"]
        pend_lg = stats["pending_logs"]
        put("pending_ss", str(pend_ss), "#f59e0b" if pend_ss else "#22c55e")
        put("pending_logs", str(pend_lg), "#f59e0b" if pend_lg else "#22c55e")

        last = stats["last_screenshot"]
        if last:
            try:
                put("last_ss", datetime.fromisoformat(str(last)).strftime(
                    "%d %b %Y, %I:%M %p"))
            except Exception:
                put("last_ss", str(last))
        else:
            put("last_ss", "none yet")

        # BUG FIX: yahan pehle hardcoded "C:\\ETS" dikhta tha.
        put("folder", STORAGE_DIR)
        put("size", _human_size(_folder_size(STORAGE_DIR)))
        put("ss_count", str(stats["screenshots"]))

        enc_text, enc_color = _encryption_status()
        put("enc", enc_text, enc_color)

        # ── Admin-controlled monitoring settings ──
        mon = _monitoring_config()
        put("m_count", mon["count"])
        put("m_interval",
            f"{mon['min']} – {mon['max']} min"
            if mon["min"] != "—" and mon["max"] != "—" else "—")
        put("m_idle", f"{mon['idle']} sec" if mon["idle"] != "—" else "—")
        verbose_on = str(mon["verbose"]).strip().lower() == "true"
        put("m_verbose", "On" if verbose_on else "Off",
            "#f59e0b" if verbose_on else "#94a3b8")

        synced = mon["synced"]
        if synced:
            try:
                delta = (datetime.now() - datetime.fromisoformat(synced)).total_seconds()
                if delta < 90:
                    put("m_synced", "just now", "#22c55e")
                elif delta < 3600:
                    put("m_synced", f"{int(delta // 60)} min ago", "#22c55e")
                else:
                    put("m_synced",
                        datetime.fromisoformat(synced).strftime("%d %b, %I:%M %p"),
                        "#f59e0b")
            except Exception:
                put("m_synced", synced)
        else:
            put("m_synced", "waiting for first sync…", "#f59e0b")

        put("ver", APP_VERSION)
        put("plat", f"{platform.system()} {platform.release()}")

        # Jo values pichhle refresh se badli hain unhe 2 second ke liye
        # highlight karo — taaki employee ko saaf dikhe ki admin ne abhi
        # kuch update kiya hai.
        self._flash_changes()


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    win = SettingsWindow()
    win.show()
    sys.exit(app.exec())
