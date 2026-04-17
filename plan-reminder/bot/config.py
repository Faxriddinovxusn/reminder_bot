import os
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_API_KEYS = [
    k for k in [
        os.getenv("GROQ_API_KEY_1"),
        os.getenv("GROQ_API_KEY_2"),
        os.getenv("GROQ_API_KEY_3"),
    ] if k
]
# Fallback: if no numbered keys set, use the single GROQ_API_KEY
if not GROQ_API_KEYS and GROQ_API_KEY:
    GROQ_API_KEYS = [GROQ_API_KEY]
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_NAME = os.getenv("MONGODB_DB", "plan_reminder")
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://your-project.up.railway.app")

def is_admin(telegram_id: int) -> bool:
    try:
        return telegram_id == ADMIN_ID
    except Exception:
        return False
