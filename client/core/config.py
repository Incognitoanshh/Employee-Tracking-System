import os

from dotenv import load_dotenv

load_dotenv()

APP_NAME = os.getenv("APP_NAME")

SCREENSHOT_MIN_INTERVAL = int(
    os.getenv("SCREENSHOT_MIN_INTERVAL")
)

SCREENSHOT_MAX_INTERVAL = int(
    os.getenv("SCREENSHOT_MAX_INTERVAL")
)

IDLE_THRESHOLD = int(
    os.getenv("IDLE_THRESHOLD")
)

API_BASE_URL = os.getenv(
    "API_BASE_URL",
    "http://localhost:5000/api"
)