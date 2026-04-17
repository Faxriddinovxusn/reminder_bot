from datetime import datetime
from typing import Dict, Any
import logging

from bot.services.db import get_db

USER_STATES_COLL = "user_states"

def _default_state() -> Dict[str, Any]:
    return {"state": "idle", "pending_tasks": [], "current_task_index": 0}

async def get_state(telegram_id: int) -> dict:
    try:
        db = get_db()
        state = await db[USER_STATES_COLL].find_one({"telegram_id": telegram_id})
        return state or _default_state()
    except Exception as e:
        logging.exception("get_state error: %s", e)
        return _default_state()

async def set_state(telegram_id: int, state: str, **kwargs):
    try:
        db = get_db()
        await db[USER_STATES_COLL].update_one(
            {"telegram_id": telegram_id},
            {"$set": {"telegram_id": telegram_id, "state": state, "updated_at": datetime.utcnow(), **kwargs}},
            upsert=True,
        )
    except Exception as e:
        logging.exception("set_state error: %s", e)
        raise

async def clear_state(telegram_id: int):
    try:
        db = get_db()
        await db[USER_STATES_COLL].update_one(
            {"telegram_id": telegram_id},
            {"$set": {"telegram_id": telegram_id, "state": "idle", "pending_tasks": [], "current_task_index": 0, "updated_at": datetime.utcnow()}},
            upsert=True,
        )
    except Exception as e:
        logging.exception("clear_state error: %s", e)
        raise
