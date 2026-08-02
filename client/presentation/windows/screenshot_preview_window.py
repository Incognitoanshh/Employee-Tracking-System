
from PySide6.QtWidgets import (
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QWidget,
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt, QThread, Signal
import tempfile
import requests
from client.presentation.windows.base_window import BaseWindow
from client.core.config import API_BASE_URL
from client.application.managers.session_manager import SessionManager
from client.security.crypto_engine import CryptoEngine


class _DownloadWorker(QThread):
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, screenshot_id):
        super().__init__()
        self.screenshot_id = screenshot_id
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            print(f"[DOWNLOAD WORKER] Fetching screenshot_id={self.screenshot_id}")
            response = requests.get(
                f"{API_BASE_URL}/screenshots/download/{self.screenshot_id}",
                headers={"Authorization": f"Bearer {SessionManager.auth_token}"},
                timeout=30
            )
            if self._cancelled:
                return
            if response.status_code == 200:
                self.finished.emit(response.content)
            else:
                self.error.emit(f"HTTP {response.status_code}:{response.text[:100]}")
        except Exception as e:
            if not self._cancelled:
                print(f"[DOWNLOAD WORKER ERROR] {e}")
                self.error.emit(str(e))


class ScreenshotPreviewWindow(BaseWindow):

    def __init__(self, screenshot_id: str, employee_id: str, timestamp: str, filename: str):
        super().__init__()

        self.screenshot_id = screenshot_id
        self.employee_id = employee_id
        self.timestamp = timestamp
        self.filename = filename
        self.scale_factor = 1.0
        self._worker = None
        self._original_pixmap = None

        self.setWindowTitle(f"Screenshot - {employee_id}")
        self.resize(1300, 800)

        self.setup_ui()
        self._load_image()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        meta_layout = QHBoxLayout()
        meta_layout.addWidget(QLabel(f"Employee: <b>{self.employee_id}</b>"))
        meta_layout.addWidget(QLabel(f"Time: <b>{self.timestamp}</b>"))
        meta_layout.addWidget(QLabel(f"File: <b>{self.filename}</b>"))
        meta_layout.addStretch()
        layout.addLayout(meta_layout)

        controls = QHBoxLayout()
        zoom_in = QPushButton("➕ Zoom In")
        zoom_out = QPushButton("➖ Zoom Out")
        zoom_in.clicked.connect(self.zoom_in)
        zoom_out.clicked.connect(self.zoom_out)
        controls.addWidget(zoom_in)
        controls.addWidget(zoom_out)
        controls.addStretch()

        close_btn = QPushButton("✕ Close")
        close_btn.clicked.connect(self.close)
        controls.addWidget(close_btn)
        layout.addLayout(controls)

        self.image_label = QLabel("Loading...")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet("background: #1e1e1e; color: #888;")
        layout.addWidget(self.image_label)

    def _stop_worker(self):
        if self._worker is not None:
            self._worker.cancel()
            if self._worker.isRunning():
                self._worker.quit()
                self._worker.wait(2000)
            self._worker.deleteLater()
            self._worker = None

    def _load_image(self):
        if self._original_pixmap is not None:
            self._render_pixmap(self._original_pixmap)
            return

        self._stop_worker()
        self._worker = _DownloadWorker(self.screenshot_id)
        self._worker.finished.connect(self._on_image_loaded)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_image_loaded(self, image_bytes):
        try:
            if self.filename.endswith(".enc"):
                try:
                    image_bytes = CryptoEngine.decrypt_bytes(image_bytes)
                except Exception:
                    self._on_error(
                        "This screenshot is encrypted with the employee's local key.\n"
                        "Only the employee who captured it can view it."
                    )
                    return

            temp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
            temp.write(image_bytes)
            temp.close()

            pixmap = QPixmap(temp.name)
            if pixmap.isNull():
                self._on_error(f"Invalid image data ({len(image_bytes)} bytes received)")
                return

            self._original_pixmap = pixmap
            self._render_pixmap(pixmap)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._on_error(str(e))

    def _is_png(self, data: bytes) -> bool:
        return data[:8] == b'\x89PNG\r\n\x1a\n'

    def _render_pixmap(self, pixmap: QPixmap):
        scaled = pixmap.scaled(
            int(1100 * self.scale_factor),
            int(650 * self.scale_factor),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.image_label.setPixmap(scaled)
        self.image_label.setStyleSheet("background: #000;")

    def _on_error(self, error_msg):
        self.image_label.setText(f"Failed to load image: {error_msg}")
        self.image_label.setStyleSheet("background: #2a1a1a; color: #f44; padding: 20px;")

    def closeEvent(self, event):
        self._stop_worker()
        event.accept()

    def zoom_in(self):
        self.scale_factor += 0.2
        self._load_image()

    def zoom_out(self):
        if self.scale_factor > 0.4:
            self.scale_factor -= 0.2
            self._load_image()