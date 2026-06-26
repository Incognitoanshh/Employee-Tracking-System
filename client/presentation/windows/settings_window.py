from __future__ import annotations
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from client.core.config import API_BASE_URL
from client.infrastructure.database.database import Database

BASE_DIR = Path(os.getenv("ETS_DATA_DIR", "C:\\ETS"))

DEFAULTS = {
    "server_url":        API_BASE_URL,
    "local_data_folder": str(BASE_DIR),
}


def _get_config(key: str) -> str:
    try:
        with Database.get_connection() as conn:  # ✅ context manager
            cursor = conn.cursor()
            cursor.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            )
            row = cursor.fetchone()
        return row[0] if row else DEFAULTS.get(key, "")
    except Exception:
        return DEFAULTS.get(key, "")


class _SectionTitle(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        self.setFont(font)
        self.setStyleSheet(
            "color:#555; padding-top:10px; padding-bottom:4px;"
                "border-bottom:1px solid #DDD; margin-bottom:4px;"
            )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)


class _ReadOnlyEdit(QLineEdit):
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setReadOnly(True)
        self.setStyleSheet(
            "background:#F5F5F5; color:#666; "
                "border:1px solid #DDD; border-radius:4px; padding:4px 8px;"
            )


class SettingsWindow(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ETS Settings")
        self.setMinimumWidth(520)
        self.setMaximumWidth(640)
        self.setSizeGripEnabled(True)
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(0)

        scroll  = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout  = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 12, 0)
        layout.setSpacing(6)
        scroll.setWidget(content)
        root.addWidget(scroll)

        # General
        layout.addWidget(_SectionTitle("General"))
        general_form = QFormLayout()
        general_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        general_form.setHorizontalSpacing(16)
        general_form.setVerticalSpacing(8)
        layout.addLayout(general_form)

        self._edit_tz = _ReadOnlyEdit("IST (fixed)")
        general_form.addRow("Timezone:", self._edit_tz)

        folder_row = QHBoxLayout()
        self._edit_folder = _ReadOnlyEdit()
        self._edit_folder.setMinimumWidth(220)
        btn_browse = QPushButton("Browse…")
        btn_browse.setFixedWidth(80)
        btn_browse.setEnabled(False)
        folder_row.addWidget(self._edit_folder)
        folder_row.addWidget(btn_browse)
        general_form.addRow("Local data folder:", folder_row)

        # Security
        layout.addSpacing(8)
        layout.addWidget(_SectionTitle("Security"))
        security_form = QFormLayout()
        security_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        security_form.setHorizontalSpacing(16)
        security_form.setVerticalSpacing(8)
        layout.addLayout(security_form)

        self._edit_enc = _ReadOnlyEdit("AES-256-GCM (Enabled)")
        security_form.addRow("Encryption:", self._edit_enc)

        self._edit_server = _ReadOnlyEdit()
        security_form.addRow("Server URL:", self._edit_server)

        layout.addStretch(1)
        root.addSpacing(12)

        btn_close = QPushButton("Close")
        btn_close.setFixedHeight(32)
        btn_close.clicked.connect(self.close)
        root.addWidget(btn_close)

    def _load_values(self):
        self._edit_folder.setText(
            _get_config("local_data_folder") or DEFAULTS["local_data_folder"]
        )
        self._edit_server.setText(
            _get_config("server_url") or DEFAULTS["server_url"]
        )