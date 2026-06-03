from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QVBoxLayout
)


class StatusCard(QFrame):

    def __init__(self, title, value):
        super().__init__()
        self.setup_ui(title, value)

    def setup_ui(self, title, value):
        self.setFixedHeight(110)
        self.setMinimumWidth(160)

        self.setStyleSheet("""
            QFrame {
                border-radius: 14px;
                border: 1px solid #1e2d3d;
                background-color: #0f172a;
            }
            QFrame:hover {
                border: 1px solid #2563eb;
                background-color: #111827;
            }
        """)

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        title_label = QLabel(title.upper())
        title_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        title_label.setStyleSheet("""
            color: #64748b;
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1px;
            border: none;
            background: transparent;
        """)

        self.value_label = QLabel(str(value))
        value_font = QFont()
        value_font.setPointSize(22)
        value_font.setBold(True)
        self.value_label.setFont(value_font)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.value_label.setStyleSheet("""
            color: #f1f5f9;
            border: none;
            background: transparent;
        """)

        layout.addWidget(title_label)
        layout.addSpacing(4)
        layout.addWidget(self.value_label)
        layout.addStretch()
        self.setLayout(layout)

    def update_value(self, value):
        self.value_label.setText(str(value))

    def set_status_color(self, color):
        self.value_label.setStyleSheet(f"""
            color: {color};
            border: none;
            background: transparent;
        """)
