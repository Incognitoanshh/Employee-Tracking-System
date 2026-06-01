import logging
import os


if not os.path.exists("logs"):
    os.makedirs("logs")


logging.basicConfig(
    level=logging.INFO,
    filename="logs/app.log",
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(message)s"
    )
)

logger = logging.getLogger("ETS")