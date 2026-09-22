from PySide6.QtWidgets import QWidget
from client.themes.theme_manager import ThemeManager
from client.presentation.theme import scrollbar


class BaseWindow(QWidget):

    def __init__(self):
        super().__init__()
        self.setup_window()

    def setup_window(self):
        # The shared scrollbar rather than a copy of one: this window's own
        # was six pixels in a fixed grey, vertical only, and did not follow
        # the theme. See theme.scrollbar().
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {ThemeManager.background()};
                color: {ThemeManager.primary_text()};
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 13px;
            }}
        """ + scrollbar(ThemeManager.background()))
