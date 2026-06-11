from PySide6.QtWidgets import (
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton
)

from PySide6.QtGui import QPixmap
import tempfile
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from PySide6.QtCore import Qt

from client.presentation.windows.base_window import BaseWindow


class ScreenshotPreviewWindow(BaseWindow):

    def __init__(self, image_path):

        super().__init__()

        self.image_path = image_path

        self.scale_factor = 1.0

        self.setWindowTitle("Screenshot Preview")

        self.resize(1300, 800)

        self.setup_ui()

    def setup_ui(self):

        layout = QVBoxLayout()

        top_bar = QHBoxLayout()

        zoom_in_btn = QPushButton("➕ Zoom In")

        zoom_out_btn = QPushButton("➖ Zoom Out")

        zoom_in_btn.clicked.connect(
            self.zoom_in
        )

        zoom_out_btn.clicked.connect(
            self.zoom_out
        )

        top_bar.addWidget(zoom_in_btn)

        top_bar.addWidget(zoom_out_btn)

        top_bar.addStretch()

        self.image_label = QLabel()

        self.image_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        layout.addLayout(top_bar)

        layout.addWidget(
            self.image_label
        )

        self.setLayout(layout)

        self.load_image()

    def load_image(self):

        try:

            key = os.environ.get(
                "SCREENSHOT_AES_KEY",
                "2a0d030fe8ae1386b14972e800448c8d"
            ).encode()

            with open(self.image_path, "rb") as file:
            
                data = file.read()

            nonce = data[:12]
            ciphertext = data[12:]
            aesgcm = AESGCM(key)
            image_bytes = aesgcm.decrypt(
                nonce,
                ciphertext,
                None
            )
            temp_file = tempfile.NamedTemporaryFile(
                delete=False,
                suffix=".png"
            )
            temp_file.write(image_bytes)
            temp_file.close()
            pixmap = QPixmap(temp_file.name)
            width = int(
                1100 * self.scale_factor
            )
            height = int(
                650 * self.scale_factor
            )
            self.image_label.setPixmap(
            
                pixmap.scaled(
                
                    width,
                    height,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
            )

        except Exception as error:
        
            print(
                "[PREVIEW ERROR]",
                os.error
            )

    def zoom_in(self):

        self.scale_factor += 0.2

        self.load_image()

    def zoom_out(self):

        if self.scale_factor > 0.4:

            self.scale_factor -= 0.2

            self.load_image()