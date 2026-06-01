from PySide6.QtWidgets import (
    QLabel,
    QVBoxLayout
)

from PySide6.QtGui import (
    QPixmap
)

from PySide6.QtCore import (
    Qt
)

from client.presentation.windows.base_window import BaseWindow


class ScreenshotPreviewWindow(BaseWindow):

    def __init__(
        self,
        image_path
    ):

        super().__init__()

        self.image_path = image_path

        self.setWindowTitle(
            "Screenshot Preview"
        )

        self.resize(1200, 700)

        self.setup_ui()

    def setup_ui(self):

        layout = QVBoxLayout()

        image_label = QLabel()

        pixmap = QPixmap(
            self.image_path
        )

        image_label.setPixmap(

            pixmap.scaled(

                1100,
                650,

                Qt.AspectRatioMode.KeepAspectRatio,

                Qt.TransformationMode.SmoothTransformation
            )
        )

        image_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        layout.addWidget(
            image_label
        )

        self.setLayout(
            layout
        )