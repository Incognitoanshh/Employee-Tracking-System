import os
import sys
from dotenv import load_dotenv

if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
else:
    base_path = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(base_path, ".env"))

API_BASE_URL = os.getenv("API_BASE_URL", "http://65.21.212.85:8000/api")
APP_NAME    = os.getenv("APP_NAME", "ETS Client")
APP_VERSION = "1.0.0"
SCREENSHOT_MIN_INTERVAL = int(os.getenv("SCREENSHOT_MIN_INTERVAL", 180))
SCREENSHOT_MAX_INTERVAL = int(os.getenv("SCREENSHOT_MAX_INTERVAL", 600))
IDLE_THRESHOLD          = int(os.getenv("IDLE_THRESHOLD", 60))
SCREENSHOT_ENCRYPTION_KEY = os.getenv("SCREENSHOT_ENCRYPTION_KEY")

class Settings:
    APP_NAME    = APP_NAME
    APP_VERSION = APP_VERSION
    SCREENSHOT_MIN_INTERVAL = SCREENSHOT_MIN_INTERVAL
    SCREENSHOT_MAX_INTERVAL = SCREENSHOT_MAX_INTERVAL
    IDLE_THRESHOLD          = IDLE_THRESHOLD
    DATABASE_URL = "sqlite:///storage/ets.db"
