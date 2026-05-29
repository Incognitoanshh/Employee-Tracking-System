import sys

from PySide6.QtWidgets import QApplication

from app.presentation.windows.login_window import LoginWindow

from app.infrastructure.database.database import Database


def main():

    Database.initialize()

    app = QApplication(sys.argv)

    window = LoginWindow()

    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":

    main()