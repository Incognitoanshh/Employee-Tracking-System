import sys
import os

# ✅ Add parent directory to path for client module imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from client.infrastructure.database.database import Database
from client.presentation.windows.login_window import LoginWindow


def main():
    # ✅ DB initialize — tables create karo
    Database.initialize()

    app = QApplication(sys.argv)
    app.setApplicationName("ETS")
    app.setApplicationDisplayName("Employee Tracking System")

    # ✅ False — tray mein rehne ke liye
    app.setQuitOnLastWindowClosed(False)

    window = LoginWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()