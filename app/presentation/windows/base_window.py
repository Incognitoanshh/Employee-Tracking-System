from PySide6.QtWidgets import QWidget


class BaseWindow(QWidget):

    def __init__(self):
        super().__init__()

        self.setup_window()

    def setup_window(self):

        self.setStyleSheet("""

            QWidget {
                background-color: #111111;
                color: white;
                font-family: Arial;
                font-size: 14px;
            }

        """)