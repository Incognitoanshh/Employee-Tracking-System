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

# ---------------------------------------------------------------------------
# BUG FIX: DB/logs/screenshots/keys pehle sab "storage/..." (RELATIVE path)
# use karte the. Relative path hamesha process ke CURRENT WORKING DIRECTORY
# ke against resolve hota hai — aur frozen .exe ka CWD launch ke tareeke pe
# depend karta hai:
#   - Desktop shortcut se double-click        -> CWD = exe ki apni folder
#   - Windows "Run" registry key se auto-start -> CWD kabhi System32 /
#                                                  user-profile-root hota hai
# Isi wajah se: (1) generated files hamesha exe ke pass hi ban rahe the
# (jaha admin/employee unhe dhundh na paaye), (2) Program Files jaisi
# read-only jagah install hone par write hi fail ho jata (silent crash /
# missing logs), aur (3) auto-start ke baad CWD badalne se app ko apna
# hi purana session/DB/crypto-key nahi milta tha — isi se restart ke baad
# auto-login bhi tootta tha.
#
# Fix: ek FIXED, per-user, OS-appropriate, hamesha-writable folder use karo
# (CWD se independent) — `ETS_DATA_DIR` env var se override ho sakta hai.
def _resolve_data_dir() -> str:
    override = os.getenv("ETS_DATA_DIR")
    if override:
        data_dir = override
    elif sys.platform.startswith("win"):
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        data_dir = os.path.join(root, "ETS")
    elif sys.platform == "darwin":
        data_dir = os.path.expanduser("~/Library/Application Support/ETS")
    else:
        root = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        data_dir = os.path.join(root, "ETS")

    os.makedirs(data_dir, exist_ok=True)
    return data_dir


DATA_DIR = _resolve_data_dir()
STORAGE_DIR = os.path.join(DATA_DIR, "storage")
os.makedirs(STORAGE_DIR, exist_ok=True)

API_BASE_URL = os.getenv("API_BASE_URL", "http://65.21.212.85:8000/api")
APP_NAME    = os.getenv("APP_NAME", "Amaze ETS")
# SINGLE SOURCE OF TRUTH — poora UI yahi padhta hai.
# BUG FIX: pehle version 4 jagah alag-alag hardcoded tha (login "v1.0",
# employee panel "2.1.0", admin console "v2.1.0", settings "1.0") — employee
# support pe jo version batata wo screen pe depend karta tha.
APP_VERSION = "2.1.0"
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
    DATABASE_URL = "sqlite:///" + os.path.join(STORAGE_DIR, "ets.db")

