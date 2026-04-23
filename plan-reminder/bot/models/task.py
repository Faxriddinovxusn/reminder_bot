from datetime import datetime, date, timedelta, timezone
from typing import Optional, List, Dict, Any
import logging
from bson import ObjectId

from bot.services.db import get_db

TASKS_COLL = "tasks"

async def _resolve_user_refs(telegram_id: int) -> Dict[str, Any]:
    try:
        db = get_db()
        user = await db.users.find_one({
            "telegram_id": {"$in": [telegram_id, str(telegram_id)]}
        })
        return {
            "user": user,
            "user_id": user.get("_id") if user else telegram_id,
            "telegram_id": user.get("telegram_id", telegram_id) if user else telegram_id,
        }
    except Exception as e:
        logging.exception("_resolve_user_refs error: %s", e)
        return {"user": None, "user_id": telegram_id, "telegram_id": telegram_id}

def _normalize_time_value(time_value: Optional[Any]) -> Optional[str]:
    try:
        if time_value is None:
            return None
        if isinstance(time_value, datetime):
            return time_value.strftime("%H:%M")
        time_text = str(time_value).strip()
        if not time_text:
            return None
        parsed = datetime.strptime(time_text, "%H:%M")
        return parsed.strftime("%H:%M")
    except Exception:
        return None

def _user_today(user_tz: timezone) -> date:
    try:
        return datetime.now(user_tz).date()
    except Exception:
        return datetime.utcnow().date()

def _build_scheduled_time_utc(target_date: date, time_text: Optional[str], user_tz: timezone) -> Optional[datetime]:
    try:
        if not time_text:
            return None
        local_time = datetime.strptime(time_text, "%H:%M").time()
        local_dt = datetime.combine(target_date, local_time).replace(tzinfo=user_tz)
        
        # If time is in the past, move to tomorrow
        now_local = datetime.now(user_tz)
        if local_dt < now_local:
            local_dt += timedelta(days=1)
            
        return local_dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None

async def _insert_task_document(task_doc: Dict[str, Any]) -> str:
    try:
        db = get_db()
        result = await db[TASKS_COLL].insert_one(task_doc)
        return str(result.inserted_id)
    except Exception as e:
        logging.exception("_insert_task_document error: %s", e)
        raise

async def create_task(
    telegram_id: int,
    title: str,
    priority: str = "normal",
    time: Optional[Any] = None,
    is_recurring: bool = False,
    target_date: Optional[str] = None,
) -> str:
    try:
        user_refs = await _resolve_user_refs(telegram_id)
        user_doc = user_refs.get("user") or {}
        user_offset = int(user_doc.get("timezone_offset", 5) or 5)
        user_tz = timezone(timedelta(hours=user_offset))
        
        now_utc = datetime.utcnow()
        today_local = _user_today(user_tz)
        time_text = _normalize_time_value(time)
        reminder_offset = 10

        base_doc = {
            "user_id": user_refs["user_id"],
            "telegram_id": user_refs["telegram_id"],
            "title": title,
            "is_done": False,
            "priority": priority or "normal",
            "date": target_date if target_date else today_local.isoformat(),
            "time": time_text,
            "scheduled_time": _build_scheduled_time_utc(date.fromisoformat(target_date) if target_date else today_local, time_text, user_tz),
            "is_recurring": False,
            "recur_time": None,
            "reminder_offset": reminder_offset,
            "reminder_sent": False,
            "arrival_sent": False,
            "status": "pending",
            "created_at": now_utc,
        }

        if is_recurring:
            template_doc = {
                "user_id": user_refs["user_id"],
                "telegram_id": user_refs["telegram_id"],
                "title": title,
                "is_done": False,
                "priority": priority or "normal",
                "date": None,
                "time": time_text,
                "scheduled_time": None,
                "is_recurring": True,
                "recur_time": time_text,
                "recur_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                "reminder_offset": reminder_offset,
                "reminder_sent": False,
                "arrival_sent": False,
                "status": "pending",
                "created_at": now_utc,
            }
            template_id = await _insert_task_document(template_doc)
            base_doc["template_id"] = template_id

        return await _insert_task_document(base_doc)
    except Exception as e:
        logging.exception("create_task error: %s", e)
        raise

async def get_tasks_for_user_on_date(telegram_id: int, date_: Optional[date] = None) -> List[Dict[str, Any]]:
    try:
        db = get_db()
        user_refs = await _resolve_user_refs(telegram_id)
        if date_ is None:
            user_doc = user_refs.get("user") or {}
            user_offset = int(user_doc.get("timezone_offset", 5) or 5)
            user_tz = timezone(timedelta(hours=user_offset))
            date_ = _user_today(user_tz)
        query = {
            "date": date_.isoformat(),
            "$or": [
                {"user_id": user_refs["user_id"]},
                {"telegram_id": user_refs["telegram_id"]},
                {"telegram_id": telegram_id},
            ],
        }
        cursor = db[TASKS_COLL].find(query)
        tasks: List[Dict[str, Any]] = []
        async for doc in cursor:
            tasks.append(doc)
        return tasks
    except Exception as e:
        logging.exception("get_tasks_for_user_on_date error: %s", e)
        raise

async def mark_task_done(task_id) -> None:
    try:
        db = get_db()
        await db[TASKS_COLL].update_one(
            {"_id": ObjectId(task_id)},
            {"$set": {"is_done": True, "status": "done", "arrival_sent": True}},
        )
    except Exception as e:
        logging.exception("mark_task_done error: %s", e)
        raise

async def create_scheduled_task(user_id: int, title: str, scheduled_time: datetime, reminder_offset: int = 10) -> str:
    try:
        user_refs = await _resolve_user_refs(user_id)
        user_doc = user_refs.get("user") or {}
        user_offset = int(user_doc.get("timezone_offset", 5) or 5)
        user_tz = timezone(timedelta(hours=user_offset))
        
        now_utc = datetime.utcnow()
        if scheduled_time.tzinfo is not None:
            scheduled_time_utc = scheduled_time.astimezone(timezone.utc).replace(tzinfo=None)
            local_date = scheduled_time.astimezone(user_tz).date()
        else:
            local_dt = scheduled_time.replace(tzinfo=user_tz)
            scheduled_time_utc = local_dt.astimezone(timezone.utc).replace(tzinfo=None)
            local_date = local_dt.date()
        task_doc = {
            "user_id": user_refs["user_id"],
            "telegram_id": user_refs["telegram_id"],
            "title": title,
            "is_done": False,
            "priority": "normal",
            "date": local_date.isoformat(),
            "time": local_dt.strftime("%H:%M") if scheduled_time.tzinfo is None else scheduled_time.astimezone(user_tz).strftime("%H:%M"),
            "scheduled_time": scheduled_time_utc,
            "is_recurring": False,
            "recur_time": None,
            "reminder_offset": reminder_offset,
            "reminder_sent": False,
            "arrival_sent": False,
            "status": "pending",
            "created_at": now_utc,
        }
        return await _insert_task_document(task_doc)
    except Exception as e:
        logging.exception("create_scheduled_task error: %s", e)
        raise

async def update_task_reminder_offset(task_id: str, reminder_offset: int) -> None:
    try:
        db = get_db()
        update_fields = {"reminder_offset": reminder_offset}
        if reminder_offset == 0:
            update_fields["reminder_sent"] = True
        else:
            update_fields["reminder_sent"] = False
        await db[TASKS_COLL].update_one({"_id": ObjectId(task_id)}, {"$set": update_fields})
    except Exception as e:
        logging.exception("update_task_reminder_offset error: %s", e)
        raise

async def mark_reminder_sent(task_id: str) -> None:
    try:
        db = get_db()
        await db[TASKS_COLL].update_one({"_id": ObjectId(task_id)}, {"$set": {"reminder_sent": True}})
    except Exception as e:
        logging.exception("mark_reminder_sent error: %s", e)
        raise

async def mark_arrival_sent(task_id: str) -> None:
    try:
        db = get_db()
        await db[TASKS_COLL].update_one({"_id": ObjectId(task_id)}, {"$set": {"arrival_sent": True}})
    except Exception as e:
        logging.exception("mark_arrival_sent error: %s", e)
        raise

async def get_upcoming_tasks() -> List[Dict[str, Any]]:
    try:
        db = get_db()
        now = datetime.utcnow()
        cursor = db[TASKS_COLL].find({
            "scheduled_time": {"$exists": True, "$ne": None, "$gt": now},
            "is_done": False,
            "status": "pending",
            "is_recurring": {"$ne": True},
        })
        tasks: List[Dict[str, Any]] = []
        async for doc in cursor:
            tasks.append(doc)
        return tasks
    except Exception as e:
        logging.exception("get_upcoming_tasks error: %s", e)
        raise
