import os
import sys
from dotenv import load_dotenv

if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
else:
    # __file__ se ek bar upar gaye (core), ek aur bar upar gaye (client), fir root par pahunche
    # Agar directory structure client/core/config.py hai:
    base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ab os.path.join bilkul sahi project root ke .env ko point karega
load_dotenv(os.path.join(base_path, ".env"))

APP_NAME = os.getenv("APP_NAME", "ETS")

# ✅ Safe defaults
SCREENSHOT_MIN_INTERVAL      = int(os.getenv("SCREENSHOT_MIN_INTERVAL", "3"))
SCREENSHOT_MAX_INTERVAL      = int(os.getenv("SCREENSHOT_MAX_INTERVAL", "10"))
IDLE_THRESHOLD               = int(os.getenv("IDLE_THRESHOLD", "60"))
SCREENSHOT_ENCRYPTION_KEY    = os.getenv("SCREENSHOT_ENCRYPTION_KEY", "")
API_BASE_URL                 = os.getenv("API_BASE_URL", "http://65.21.212.85:8000/api")