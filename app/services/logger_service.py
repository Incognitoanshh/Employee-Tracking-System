from datetime import datetime


class LoggerService:

    LOG_FILE = "storage/app.log"

    @staticmethod
    def log(message):

        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        with open(

            LoggerService.LOG_FILE,

            "a",

            encoding="utf-8"

        ) as file:

            file.write(

                f"[{timestamp}] {message}\n"

            )