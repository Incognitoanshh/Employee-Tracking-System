import sys

from PySide6.QtWidgets import QApplication

from app.presentation.windows.login_window import LoginWindow

from app.infrastructure.logging.logger import logger


def main():

    logger.info("ETS Application Started")

    app = QApplication(sys.argv)

    app.setApplicationName("ETS Client")

    window = LoginWindow()

    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()