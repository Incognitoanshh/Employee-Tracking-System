from PySide6.QtWidgets import (
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton
)

from PySide6.QtGui import QPixmap

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

        pixmap = QPixmap(
            self.image_path
        )

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

    def zoom_in(self):

        self.scale_factor += 0.2

        self.load_image()

    def zoom_out(self):

        if self.scale_factor > 0.4:

            self.scale_factor -= 0.2

            self.load_image()