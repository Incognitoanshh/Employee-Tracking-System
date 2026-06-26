from __future__ import annotations
import os
from PySide6.QtWidgets import QLabel, QVBoxLayout, QHBoxLayout, QPushButton
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt

from client.presentation.windows.base_window import BaseWindow
from client.security.crypto_engine import CryptoEngine


class ScreenshotPreviewWindow(BaseWindow):
    def __init__(
        self,
        file_path: str = None,
        *,
        screenshot_id: str = None,
        employee_id: str = None,
        timestamp: str = None,
        filename: str = None,
    ):
        super().__init__()
        # Support both callers: file_path (logs) or screenshot_id (admin)
        self.file_path = file_path
        self.screenshot_id = screenshot_id
        self.employee_id = employee_id
        self.timestamp = timestamp
        self.filename = filename

        title = f"Screenshot Preview - {employee_id or filename or 'Preview'}"
        self.setWindowTitle(title)
        self.resize(1200, 800)
        self._setup_ui()
        self._load_image()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self.image_label = QLabel("Loading preview...")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet("background: #1e1e1e; color: #888; padding: 20px;")
        layout.addWidget(self.image_label)

        btn_layout = QHBoxLayout()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    def _load_image(self):
        try:
            file_path = self.file_path

            # Admin case: construct file_path from filename if not provided
            if not file_path and self.filename:
                file_path = os.path.join(
                    os.path.dirname(__file__),
                    "..", "..", "..", "storage", "screenshots", self.filename
                )
                file_path = os.path.normpath(file_path)

            if not file_path or not os.path.exists(file_path):
                self.image_label.setText("File not found")
                return

            image_bytes = CryptoEngine.load_decrypted(file_path)
            pixmap = QPixmap()
            pixmap.loadFromData(image_bytes)

            if pixmap.isNull():
                self.image_label.setText("Invalid image data")
                return

            self.image_label.setPixmap(
                pixmap.scaled(1100, 650, Qt.AspectRatioMode.KeepAspectRatio)
            )
        except Exception as e:
            self.image_label.setText(f"Failed to load: {str(e)}")
