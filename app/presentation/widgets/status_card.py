from PySide6.QtCore import Qt

from PySide6.QtGui import QFont

from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QVBoxLayout
)


class StatusCard(QFrame):

    def __init__(
        self,
        title,
        value
    ):

        super().__init__()

        self.setup_ui(
            title,
            value
        )

    def setup_ui(
        self,
        title,
        value
    ):

        self.setFixedHeight(120)

        self.setStyleSheet("""

            QFrame {
                background-color: #1a1a1a;
                border-radius: 14px;
                border: 1px solid #2d2d2d;
            }

            QLabel {
                color: white;
            }

        """)

        layout = QVBoxLayout()

        layout.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        title_label = QLabel(title)

        title_font = QFont()

        title_font.setPointSize(10)

        title_label.setFont(title_font)

        self.value_label = QLabel(value)

        value_font = QFont()

        value_font.setPointSize(18)

        value_font.setBold(True)

        self.value_label.setFont(
            value_font
        )

        layout.addWidget(title_label)

        layout.addSpacing(10)

        layout.addWidget(
            self.value_label
        )

        self.setLayout(layout)

    def update_value(
        self,
        value
    ):

        self.value_label.setText(value)