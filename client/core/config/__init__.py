import os
import sys
from dotenv import load_dotenv

if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
else:
    # BUG FIX: pehle base_path = dirname(__file__) tha, jo
    # client/core/config/ resolve hota tha. .env file actually client/
    # root mein hoti hai (client/.env) — load_dotenv() ko file kabhi
    # milti hi nahi thi. Saari env vars (SCREENSHOT_ENCRYPTION_KEY
    # included) silently missing rehte the, koi error bhi nahi aata tha,
    # aur CryptoEngine har machine-reset pe naya random key generate
    # karta rehta tha — purane saare .enc screenshots undecryptable ho
    # jaate the.
    base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
