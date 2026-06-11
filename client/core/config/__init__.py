import os
from dotenv import load_dotenv

load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api")

APP_NAME    = os.getenv("APP_NAME", "ETS Client")
APP_VERSION = "1.0.0"

SCREENSHOT_MIN_INTERVAL = int(os.getenv("SCREENSHOT_MIN_INTERVAL", 180))
SCREENSHOT_MAX_INTERVAL = int(os.getenv("SCREENSHOT_MAX_INTERVAL", 600))
IDLE_THRESHOLD          = int(os.getenv("IDLE_THRESHOLD", 60))


class Settings:
    APP_NAME    = APP_NAME
    APP_VERSION = APP_VERSION
    SCREENSHOT_MIN_INTERVAL = SCREENSHOT_MIN_INTERVAL
    SCREENSHOT_MAX_INTERVAL = SCREENSHOT_MAX_INTERVAL
    IDLE_THRESHOLD          = IDLE_THRESHOLD
    DATABASE_URL = "sqlite:///storage/ets.db"
