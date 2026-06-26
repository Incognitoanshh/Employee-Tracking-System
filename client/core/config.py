import os
import sys

from dotenv import load_dotenv

if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
else:
    base_path = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(base_path, ".env"))

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

SCREENSHOT_ENCRYPTION_KEY = os.getenv("SCREENSHOT_ENCRYPTION_KEY")

API_BASE_URL = os.getenv(
    "API_BASE_URL",
    "http://65.21.212.85:8000/api"
)
