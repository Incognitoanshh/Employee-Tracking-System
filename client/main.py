import sys

from PySide6.QtWidgets import QApplication

from client.presentation.windows.login_window import LoginWindow

from client.infrastructure.database.database import Database


def main():

    Database.initialize()

    app = QApplication(sys.argv)

    app.setQuitOnLastWindowClosed(False)

    window = LoginWindow()

    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":

    main()