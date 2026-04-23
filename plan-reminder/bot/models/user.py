from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import logging

from bot.services.db import get_db

USERS_COLL = "users"

def calculate_segment(interaction_count: int) -> str:
    if interaction_count < 6:
        return "new"
    elif interaction_count < 21:
        return "active"
    else:
        return "power_user"


def get_user_tz(user_doc: Optional[Dict[str, Any]] = None) -> timezone:
    """Return a timezone object from the user's timezone_offset field.
    Defaults to UTC+5 (Tashkent) if not set."""
    offset = 5  # default
    if user_doc:
        try:
            offset = int(user_doc.get("timezone_offset", 5) or 5)
        except (ValueError, TypeError):
            offset = 5
    return timezone(timedelta(hours=offset))


def get_user_tz_offset_str(user_doc: Optional[Dict[str, Any]] = None) -> str:
    """Return a string like 'UTC+5' or 'UTC-5' from the user's timezone_offset."""
    offset = 5
    if user_doc:
        try:
            offset = int(user_doc.get("timezone_offset", 5) or 5)
        except (ValueError, TypeError):
            offset = 5
    if offset >= 0:
        return f"UTC+{offset}"
    else:
        return f"UTC{offset}"


async def set_timezone(telegram_id: int, country: str, timezone_str: str, timezone_offset: int) -> Optional[Dict[str, Any]]:
    """Save user's timezone info to DB."""
    try:
        db = get_db()
        await db[USERS_COLL].update_one(
            {"telegram_id": telegram_id},
            {"$set": {
                "country": country,
                "timezone": timezone_str,
                "timezone_offset": timezone_offset,
            }}
        )
        return await get_user_by_telegram_id(telegram_id)
    except Exception as e:
        logging.exception("set_timezone error: %s", e)
        raise


async def log_command_to_history(telegram_id: int, command: str, bot_reply: str) -> None:
    db = get_db()
    user = await get_user_by_telegram_id(telegram_id)
    if not user:
        return
    history = user.get("chat_history", [])
    history.append({"role": "user", "content": command})
    history.append({"role": "assistant", "content": bot_reply})
    await db[USERS_COLL].update_one(
        {"telegram_id": telegram_id}, 
        {"$set": {"chat_history": history[-20:]}}
    )


async def ensure_user_profile_fields(
    telegram_id: int,
    username: Optional[str] = None,
    language: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    try:
        db = get_db()
        user = await db[USERS_COLL].find_one({"telegram_id": telegram_id})
        if not user:
            return None

        now = datetime.utcnow()
        updates: Dict[str, Any] = {}

        # Backfill timezone fields for existing users
        if not user.get("timezone_offset") and user.get("timezone_offset") != 0:
            updates["timezone_offset"] = 5
        if not user.get("timezone"):
            updates["timezone"] = "UTC+5"
        if not user.get("country"):
            updates["country"] = "O'zbekiston"

        if not isinstance(user.get("personality"), dict):
            updates["personality"] = {}
        if not isinstance(user.get("topics_discussed"), list):
            updates["topics_discussed"] = []
        if not isinstance(user.get("chat_history"), list):
            updates["chat_history"] = []
        if not isinstance(user.get("interaction_count"), int):
            updates["interaction_count"] = 0
        if not user.get("last_active"):
            updates["last_active"] = user.get("created_at") or now
        if not isinstance(user.get("segment"), str) or not user.get("segment"):
            updates["segment"] = "new"
        if not isinstance(user.get("communication_style"), str):
            updates["communication_style"] = "unknown"
        if not isinstance(user.get("habits"), list):
            updates["habits"] = []
        if username is not None and username != user.get("username"):
            updates["username"] = username
        if language is not None and language != user.get("language"):
            updates["language"] = language
        
        if not user.get("web_pin"):
            import random
            updates["web_pin"] = str(random.randint(10000, 99999))

        if updates:
            await db[USERS_COLL].update_one({"telegram_id": telegram_id}, {"$set": updates})
            user.update(updates)

        return user
    except Exception as e:
        logging.exception("ensure_user_profile_fields error: %s", e)
        raise

async def create_user(telegram_id: int, username: Optional[str] = None, language: str = "uz") -> Dict[str, Any]:
    try:
        db = get_db()
        now = datetime.utcnow()
        trial_end = now + timedelta(days=5)
        user_doc = {
            "telegram_id": telegram_id,
            "username": username,
            "language": language,
            "trial_start": now,
            "trial_end": trial_end,
            "is_paid": False,
            "paid_until": None,
            "subscription_status": "trial",
            "created_at": now,
            "personality": {},
            "interaction_count": 0,
            "topics_discussed": [],
            "chat_history": [],
            "last_active": now,
            "segment": "new",
            "communication_style": "unknown",
            "habits": [],
            "country": "O'zbekiston",
            "timezone": "UTC+5",
            "timezone_offset": 5,
        }
        await db[USERS_COLL].update_one({"telegram_id": telegram_id}, {"$setOnInsert": user_doc}, upsert=True)
        return await ensure_user_profile_fields(telegram_id, username=username, language=language)
    except Exception as e:
        logging.exception("create_user error: %s", e)
        raise

async def get_user_by_telegram_id(telegram_id: int) -> Optional[Dict[str, Any]]:
    try:
        return await ensure_user_profile_fields(telegram_id)
    except Exception as e:
        logging.exception("get_user_by_telegram_id error: %s", e)
        raise

async def set_language(telegram_id: int, language: str) -> Optional[Dict[str, Any]]:
    try:
        db = get_db()
        await db[USERS_COLL].update_one({"telegram_id": telegram_id}, {"$set": {"language": language}})
        return await ensure_user_profile_fields(telegram_id, language=language)
    except Exception as e:
        logging.exception("set_language error: %s", e)
        raise

async def ensure_indexes() -> None:
    try:
        db = get_db()
        await db[USERS_COLL].create_index("telegram_id", unique=True)
    except Exception as e:
        logging.exception("ensure_indexes error: %s", e)
        raise

async def get_subscription_status(user: dict) -> str:
    try:
        now = datetime.utcnow()
        trial_end = user.get("trial_end")
        is_paid = user.get("is_paid", False)
        paid_until = user.get("paid_until")
        if is_paid and paid_until and now < paid_until:
            return "paid"
        if trial_end and now < trial_end:
            return "trial"
        return "expired"
    except Exception as e:
        logging.exception("get_subscription_status error: %s", e)
        return "expired"
