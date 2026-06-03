from PySide6.QtWidgets import QWidget
from client.themes.theme_manager import ThemeManager


class BaseWindow(QWidget):

    def __init__(self):
        super().__init__()
        self.setup_window()

    def setup_window(self):
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {ThemeManager.background()};
                color: {ThemeManager.primary_text()};
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 13px;
            }}
            QScrollBar:vertical {{
                background: {ThemeManager.background()};
                width: 6px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: #334155;
                border-radius: 3px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)
