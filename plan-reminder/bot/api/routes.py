import os
import logging
from datetime import datetime, timedelta, timezone, date
from typing import List, Optional, Union, Any
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pydantic import BaseModel
from bson import ObjectId
from pathlib import Path

# Helper to avoid ObjectId serialization errors
def sanitize_mongo_doc(doc):
    if not doc:
        return doc
    if "_id" in doc:
        doc["id"] = str(doc["_id"])
        doc["_id"] = str(doc["_id"])
    for key, value in list(doc.items()):
        if isinstance(value, ObjectId):
            doc[key] = str(value)
        elif isinstance(value, datetime):
            doc[key] = value.isoformat()
    return doc

TASHKENT_TZ = timezone(timedelta(hours=5))

# Load environment using robust path resolution
current_dir = Path(__file__).resolve().parent
while current_dir != current_dir.parent:
    env_file = current_dir / '.env'
    if env_file.exists():
        load_dotenv(dotenv_path=str(env_file))
        break
    current_dir = current_dir.parent


MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("MONGODB_DB", "plan_reminder")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

import hmac
import hashlib
from urllib.parse import parse_qsl
from fastapi import Depends, Header, HTTPException, Request
import json

async def verify_telegram_auth(request: Request):
    if auth_header := request.headers.get("Authorization"):
        parts = auth_header.split()
        if len(parts) == 2 and parts[0] == "Bearer" and parts[1].startswith("web_"):
            token_parts = parts[1].split("_")
            if len(token_parts) == 3:
                telegram_id = token_parts[1]
                pin = token_parts[2]
                user = await db.users.find_one({
                    "$or": [
                        {"telegram_id": int(telegram_id) if telegram_id.isdigit() else telegram_id},
                        {"telegram_id": str(telegram_id)}
                    ],
                    "web_pin": pin
                })
                if user:
                    return str(telegram_id)
                    
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing Telegram authentication")

    try:
        parsed_data = dict(parse_qsl(init_data))
        if "hash" not in parsed_data:
            raise HTTPException(status_code=401, detail="Invalid authentication data")
            
        hash_val = parsed_data.pop("hash")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calculated_hash != hash_val:
            raise HTTPException(status_code=401, detail="Authentication failed")
            
        user_data = json.loads(parsed_data.get("user", "{}"))
        return str(user_data.get("id"))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Telegram Auth Error: {e}")
        raise HTTPException(status_code=401, detail="Authentication error")


# MongoDB client
db_client = None
db = None

async def connect_db():
    global db_client, db
    db_client = AsyncIOMotorClient(MONGODB_URI)
    db = db_client[DB_NAME]
    await db.command("ping")
    logger.info("Connected to MongoDB")

async def close_db():
    global db_client
    if db_client:
        db_client.close()
        logger.info("Disconnected from MongoDB")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await connect_db()
    yield
    # Shutdown
    await close_db()

# FastAPI app
app = FastAPI(title="Plan Reminder API", version="1.0.0", lifespan=lifespan)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for Mini App
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models
class LoginRequest(BaseModel):
    pin: str

class TaskCreate(BaseModel):
    userId: Union[str, int]
    title: str
    priority: str = "medium"
    scheduled_time: Optional[str] = None
    time: Optional[str] = None  # Alternative field name

class TaskUpdate(BaseModel):
    done: bool
    userId: Optional[Union[str, int]] = None

class TaskEdit(BaseModel):
    title: Optional[str] = None
    priority: Optional[str] = None
    time: Optional[str] = None
    userId: Optional[Union[str, int]] = None


class NoteCreate(BaseModel):
    userId: Union[str, int]
    title: str
    content: str = ""

class AiChatRequest(BaseModel):
    userId: Union[str, int]
    message: str
    history: list = []
    language: str = "en"

class TaskSuggestion(BaseModel):
    title: str
    priority: str = "medium"
    time: Optional[str] = None


# ==================== ADMIN AUTH ====================
ADMIN_CODE = "1342b"

async def verify_admin_auth(request: Request):
    """Verify admin authentication token"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header == f"Bearer admin_{ADMIN_CODE}":
        return True
    raise HTTPException(status_code=403, detail="Admin access required")


# Helper functions
@app.post("/api/auth/login")
async def login_with_pin(req: LoginRequest):
    pin = req.pin.strip()
    
    # Admin login check
    if pin == ADMIN_CODE:
        return {
            "success": True,
            "is_admin": True,
            "user_id": "admin",
            "username": "Admin",
            "auth_token": f"admin_{ADMIN_CODE}"
        }
    
    if not pin or len(pin) != 5:
        raise HTTPException(status_code=400, detail="Noto'g'ri PIN format")
        
    user = await db.users.find_one({"web_pin": pin})
    if not user:
        raise HTTPException(status_code=401, detail="PIN yoki foydalanuvchi topilmadi")
        
    return {
        "success": True,
        "is_admin": False,
        "user_id": str(user["telegram_id"]),
        "username": user.get("username", "User"),
        "language": user.get("language", "uz"),
        "auth_token": f"web_{user['telegram_id']}_{pin}"
    }

async def get_user(user_id: str):
    """Get user from database, create if not exists"""
    user = await db.users.find_one({
        "telegram_id": {
            "$in": [user_id, int(user_id) if str(user_id).isdigit() else user_id]
        }
    })
    if not user:
        await db.users.insert_one({
            "telegram_id": user_id,
            "language": "en",
            "trial_end": None,
            "is_paid": False,
            "paid_until": None,
            "created_at": datetime.utcnow()
        })
        user = await db.users.find_one({"telegram_id": user_id})
    return user

async def validate_user(user_id: str):
    """Validate user exists and has access"""
    try:
        user = await db.users.find_one({
            "$or": [
                {"telegram_id": user_id},
                {"telegram_id": int(user_id) if str(user_id).isdigit() else None}
            ]
        })
        return user
    except:
        return None

# ==================== TASK ENDPOINTS ====================

@app.get("/api/tasks/{user_id}")
async def get_tasks(user_id: str, auth_user_id: str = Depends(verify_telegram_auth)):
    """Get today's tasks for user"""
    if str(user_id) != str(auth_user_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    user = await db.users.find_one({
        "$or": [
            {"telegram_id": user_id},
            {"telegram_id": int(user_id) if str(user_id).isdigit() else user_id}
        ]
    })
    if not user:
        return {"data": []}
    
    # Use Tashkent timezone for "today" to match how tasks are stored
    now_tashkent = datetime.now(TASHKENT_TZ)
    today = now_tashkent.date()
    today_str = today.isoformat()
    
    # Get tasks for today (support tasks saved by bot via telegram_id as well)
    or_conditions = [
        {"user_id": user["_id"], "date": today_str},
        {
            "user_id": {"$in": [user_id, int(user_id) if str(user_id).isdigit() else user_id]},
            "date": today_str
        },
        {"telegram_id": {"$in": [user_id, int(user_id) if str(user_id).isdigit() else user_id]}, "date": today_str}
    ]
    
    tasks = await db.tasks.find({"$or": or_conditions}).to_list(None)
    
    # Deduplicate by _id
    seen_ids = set()
    unique_tasks = []
    for t in tasks:
        tid = str(t["_id"])
        if tid not in seen_ids:
            seen_ids.add(tid)
            unique_tasks.append(t)
    
    # Convert ObjectId to string for JSON serialization
    tasks_data = []
    for task in unique_tasks:
        task = sanitize_mongo_doc(task)
        # Ensure camelCase support for frontend
        task["createdAt"] = task.get("created_at")
        task["done"] = task.get("done", task.get("is_done", False) or task.get("status") == "done")
        p = task.get("priority", "medium")
        if p == "normal": p = "medium"
        task["priority"] = p
        tasks_data.append(task)
    
    return {"data": tasks_data}

@app.get("/api/tasks/{user_id}/future")
async def get_future_tasks(user_id: str, auth_user_id: str = Depends(verify_telegram_auth)):
    """Get all future planned tasks grouped by date"""
    if str(user_id) != str(auth_user_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    user = await db.users.find_one({
        "$or": [
            {"telegram_id": user_id},
            {"telegram_id": int(user_id) if str(user_id).isdigit() else user_id}
        ]
    })
    if not user:
        return {"data": []}

    now_tashkent = datetime.now(TASHKENT_TZ)
    today_str = now_tashkent.date().isoformat()

    or_conditions = [
        {"user_id": user["_id"], "date": {"$gt": today_str}},
        {
            "user_id": {"$in": [user_id, int(user_id) if str(user_id).isdigit() else user_id]},
            "date": {"$gt": today_str}
        },
        {"telegram_id": {"$in": [user_id, int(user_id) if str(user_id).isdigit() else user_id]}, "date": {"$gt": today_str}}
    ]

    tasks = await db.tasks.find({"$or": or_conditions, "status": {"$ne": "deleted"}}).sort("date", 1).to_list(500)

    seen_ids = set()
    unique_tasks = []
    for t in tasks:
        tid = str(t["_id"])
        if tid not in seen_ids:
            seen_ids.add(tid)
            unique_tasks.append(t)

    grouped = {}
    for task in unique_tasks:
        date_str = task.get("date", "unknown")
        if date_str not in grouped:
            grouped[date_str] = []
        task = sanitize_mongo_doc(task)
        task["done"] = task.get("done", task.get("is_done", False) or task.get("status") == "done")
        p = task.get("priority", "medium")
        if p == "normal": p = "medium"
        task["priority"] = p
        grouped[date_str].append(task)

    result = [
        {"date": date, "count": len(t_list), "tasks": t_list}
        for date, t_list in sorted(grouped.items())
    ]

    return {"data": result}

@app.post("/api/tasks")
async def create_task_route(task_data: TaskCreate, auth_user_id: str = Depends(verify_telegram_auth)):
    """Create new task"""
    if str(task_data.userId) != str(auth_user_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    from bot.models.task import create_task as db_create_task
    try:
        task_id = await db_create_task(
            telegram_id=int(task_data.userId) if str(task_data.userId).isdigit() else task_data.userId,
            title=task_data.title,
            priority=task_data.priority or "normal",
            time=task_data.time or task_data.scheduled_time
        )
        # Fetch the created task to return it
        task = await db.tasks.find_one({"_id": ObjectId(task_id)})
        if task:
            task = sanitize_mongo_doc(task)
            return {"data": task}
    except Exception as e:
        logger.error(f"Error creating task: {e}")
        return {"data": None, "error": str(e)}
    
    return {"data": None, "error": "Failed to create task"}


@app.patch("/api/tasks/{task_id}/done")
async def mark_task_done(task_id: str, update: TaskUpdate, auth_user_id: str = Depends(verify_telegram_auth)):
    """Mark task as done/undone"""
    try:
        oid = ObjectId(task_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid task ID")
        
    task_exists = await db.tasks.find_one({"_id": oid})
    if not task_exists:
        raise HTTPException(status_code=404, detail="Task not found")
        
    if str(task_exists.get("telegram_id")) != str(auth_user_id) and str(task_exists.get("user_id")) != str(auth_user_id):
        # We also check user_id if telegram_id is not string format
        user_doc = await db.users.find_one({"_id": task_exists.get("user_id")})
        if not user_doc or str(user_doc.get("telegram_id")) != str(auth_user_id):
            raise HTTPException(status_code=403, detail="Forbidden: You don't own this task")

    
    result = await db.tasks.update_one(
        {"_id": oid},
        {"$set": {
            "done": update.done,
            "is_done": update.done,
            "status": "done" if update.done else "pending",
            "updated_at": datetime.utcnow()
        }}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = await db.tasks.find_one({"_id": oid})
    return {"data": sanitize_mongo_doc(task)}

@app.put("/api/tasks/{task_id}")
async def edit_task(task_id: str, edit_data: TaskEdit, auth_user_id: str = Depends(verify_telegram_auth)):
    """Edit task title, priority, time"""
    try:
        oid = ObjectId(task_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid task ID")
        
    task_exists = await db.tasks.find_one({"_id": oid})
    if not task_exists:
        raise HTTPException(status_code=404, detail="Task not found")
        
    if str(task_exists.get("telegram_id")) != str(auth_user_id) and str(task_exists.get("user_id")) != str(auth_user_id):
        user_doc = await db.users.find_one({"_id": task_exists.get("user_id")})
        if not user_doc or str(user_doc.get("telegram_id")) != str(auth_user_id):
            raise HTTPException(status_code=403, detail="Forbidden: You don't own this task")

    update_fields = {"updated_at": datetime.utcnow()}
    if edit_data.title is not None:
        update_fields["title"] = edit_data.title
    if edit_data.priority is not None:
        update_fields["priority"] = edit_data.priority
    if edit_data.time is not None:
        update_fields["scheduled_time"] = edit_data.time
        update_fields["time"] = edit_data.time

    result = await db.tasks.update_one(
        {"_id": oid},
        {"$set": update_fields}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = await db.tasks.find_one({"_id": oid})
    return {"data": sanitize_mongo_doc(task)}

@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, auth_user_id: str = Depends(verify_telegram_auth)):
    """Delete task"""
    try:
        oid = ObjectId(task_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid task ID")
        
    task_exists = await db.tasks.find_one({"_id": oid})
    if not task_exists:
        raise HTTPException(status_code=404, detail="Task not found")
        
    if str(task_exists.get("telegram_id")) != str(auth_user_id) and str(task_exists.get("user_id")) != str(auth_user_id):
        user_doc = await db.users.find_one({"_id": task_exists.get("user_id")})
        if not user_doc or str(user_doc.get("telegram_id")) != str(auth_user_id):
            raise HTTPException(status_code=403, detail="Forbidden: You don't own this task")

    
    result = await db.tasks.delete_one({"_id": oid})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return {"data": {"deleted": True}}

# ==================== NOTES ENDPOINTS ====================

@app.get("/api/notes/{user_id}")
async def get_notes(user_id: str, auth_user_id: str = Depends(verify_telegram_auth)):
    """Get all notes for user"""
    if str(user_id) != str(auth_user_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    user = await db.users.find_one({
        "$or": [
            {"telegram_id": user_id},
            {"telegram_id": int(user_id) if str(user_id).isdigit() else user_id}
        ]
    })
    if not user:
        return {"data": []}
    
    notes = await db.notes.find({"user_id": user["_id"]}).to_list(None)
    
    notes_data = []
    for note in notes:
        notes_data.append(sanitize_mongo_doc(note))
    
    return {"data": notes_data}

@app.post("/api/notes")
async def create_note(note_data: NoteCreate, auth_user_id: str = Depends(verify_telegram_auth)):
    """Create or update note"""
    user_id_val = note_data.userId
    if str(user_id_val) != str(auth_user_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    
    user = await db.users.find_one({
        "$or": [
            {"telegram_id": user_id_val},
            {"telegram_id": int(user_id_val) if str(user_id_val).isdigit() else user_id_val}
        ]
    })
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check if note with this title exists
    existing = await db.notes.find_one({
        "user_id": user["_id"],
        "title": note_data.title
    })
    
    if existing:
        # Update existing note
        result = await db.notes.update_one(
            {"_id": existing["_id"]},
            {"$set": {
                "content": note_data.content,
                "updated_at": datetime.utcnow()
            }}
        )
        existing["content"] = note_data.content
        return {"data": sanitize_mongo_doc(existing)}
    else:
        # Create new note
        note = {
            "user_id": user["_id"],
            "title": note_data.title,
            "content": note_data.content,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        result = await db.notes.insert_one(note)
        note["_id"] = result.inserted_id
        
        return {"data": sanitize_mongo_doc(note)}

# ==================== AI ACTION SYSTEM ====================

async def detect_and_execute_action(message: str, ai_response: str, user_id: str, db, history: list) -> dict:
    action_result = {"action": None, "data": None}
    import re, json
    
    # Check for AI autonomous JSON command block
    match = re.search(r"```json\s*(\{.*?\})\s*```", ai_response, re.DOTALL)
    if match:
        try:
            command = json.loads(match.group(1))
            action = command.get("action")
            data = command.get("data")
            target_title = command.get("target_title")
            
            user = await db.users.find_one({
                "$or": [{"telegram_id": user_id}, {"telegram_id": int(user_id) if str(user_id).isdigit() else user_id}]
            })
            if user:
                from datetime import datetime, date
                today = date.today().isoformat()
                
                if action == "propose_tasks" and isinstance(data, list):
                    # We just return the proposed tasks without saving them yet
                    return {"action": "propose_tasks", "data": data}
                
                elif action == "delete_task" and target_title:
                    regex = re.compile(re.escape(target_title), re.IGNORECASE)
                    task = await db.tasks.find_one({"user_id": user["_id"], "date": today, "title": regex})
                    if task:
                        await db.tasks.delete_one({"_id": task["_id"]})
                    return {"action": "tasks_added", "data": []} # reload hack
                    
                elif action == "mark_done" and target_title:
                    regex = re.compile(re.escape(target_title), re.IGNORECASE)
                    task = await db.tasks.find_one({"user_id": user["_id"], "date": today, "title": regex})
                    if task:
                        await db.tasks.update_one({"_id": task["_id"]}, {"$set": {"is_done": True, "done": True, "status": "done"}})
                    return {"action": "tasks_added", "data": []} # reload hack
        except Exception as e:
            pass

    msg_lower = message.lower()
    
    # ADD TASK detection
    add_keywords = ["qo'sh", "add", "добавь", "yoz", "joylab", "to-do", "todo", "vazifa qo'sh", "reja qo'sh"]
    if any(kw in msg_lower for kw in add_keywords):
        # Extract tasks from message using AI
        from bot.services.ai import extract_tasks_from_text
        context_text = message
        if history:
            text_lines = [f"{m.get('role', 'user')}: {m.get('content', '')}" for m in history[-6:]]
            text_lines.append(f"user: {message}")
            context_text = "\n".join(text_lines)
            
        tasks = await extract_tasks_from_text(context_text, "uz")
        if tasks:
            action_result = {"action": "propose_tasks", "data": tasks}
    
    # DELETE TASK detection
    delete_keywords = ["o'chir", "delete", "удали", "remove", "olib tashla"]
    if any(kw in msg_lower for kw in delete_keywords):
        action_result = {"action": "delete_requested", "data": None}
    
    # MARK DONE detection
    done_keywords = ["bajardim", "done", "выполнено", "finished", "tugat", "qildim"]
    if any(kw in msg_lower for kw in done_keywords):
        action_result = {"action": "mark_done_requested", "data": None}
    
    # ADD NOTE detection
    note_keywords = ["note", "eslatma yoz", "yozib qo'y", "запись", "zapiski"]
    if any(kw in msg_lower for kw in note_keywords):
        action_result = {"action": "note_requested", "data": None}
    
    return action_result

# ==================== AI CHAT ENDPOINT ====================

@app.post("/api/ai/chat")
async def ai_chat(request: Request, auth_user_id: str = Depends(verify_telegram_auth)):
    try:
        body = await request.json()
        user_id = str(body.get("userId", ""))
        if str(user_id) != str(auth_user_id):
            raise HTTPException(status_code=403, detail="Forbidden")

        message = body.get("message", "")
        history = body.get("history", [])
        language = body.get("language", "en")
        
        user = await db.users.find_one({
            "$or": [
                {"telegram_id": user_id},
                {"telegram_id": int(user_id) if user_id.isdigit() else user_id}
            ]
        })
        
        today_tasks = []
        user_profile = {}
        if user:
            today = datetime.now(TASHKENT_TZ).date().isoformat()
            tasks = await db.tasks.find({"date": today, "$or": [{"telegram_id": user_id}, {"telegram_id": int(user_id) if user_id.isdigit() else user_id}, {"user_id": user["_id"]}]}).to_list(20)
            today_tasks = [{"title": t.get("title",""), "time": t.get("time", ""), "is_done": t.get("is_done",False), "id": str(t["_id"])} for t in tasks]
            user_profile = {
                "username": user.get("username",""),
                "segment": user.get("segment","new"),
                "interaction_count": user.get("interaction_count",0),
                "habits": user.get("habits",[]),
                "communication_style": user.get("communication_style","casual"),
                "today_tasks": today_tasks,
            }
            language = user.get("language", language)
            history = user.get("chat_history", []) or []
        
        # Call AI with automatic key rotation (handled by call_groq in ai.py)
        from bot.services.ai import get_ai_response
        try:
            ai_result = await get_ai_response(message, language, history[-10:], user_profile)
            if isinstance(ai_result, tuple):
                ai_response, _ = ai_result
            else:
                ai_response = ai_result
        except RuntimeError:
            # All API keys exhausted
            busy_messages = {
                "uz": "Hozir band, bir daqiqadan keyin urinib ko'ring 🙏",
                "ru": "Сейчас занято, попробуйте через минуту 🙏",
                "en": "Currently busy, please try again in a minute 🙏",
            }
            return {
                "message": busy_messages.get(language, busy_messages["en"]),
                "action": None,
                "data": None,
                "tasks": [],
            }
        except Exception as api_err:
            error_code = getattr(api_err, "status_code", None)
            if error_code in (429, 403):
                busy_messages = {
                    "uz": "Hozir band, bir daqiqadan keyin urinib ko'ring 🙏",
                    "ru": "Сейчас занято, попробуйте через минуту 🙏",
                    "en": "Currently busy, please try again in a minute 🙏",
                }
                return {
                    "message": busy_messages.get(language, busy_messages["en"]),
                    "action": None,
                    "data": None,
                    "tasks": [],
                }
            raise
        
        # Detect and execute action
        action_result = await detect_and_execute_action(message, ai_response, user_id, db, history)
        
        # Remove JSON from ai_response so user doesn't see it
        import re
        clean_ai_response = re.sub(r"```json\s*\{.*?\}\s*```", "", ai_response, flags=re.DOTALL).strip()
        
        # Save chat history
        if user:
            chat_history = history if history is not None else []
            chat_history.append({"role": "user", "content": message})
            chat_history.append({"role": "assistant", "content": clean_ai_response})
            await db.users.update_one({"_id": user["_id"]}, {"$set": {"chat_history": chat_history[-15:]}})
            history = chat_history[-6:] # Update history for action detection
        
        return {
            "message": clean_ai_response,
            "action": action_result.get("action"),
            "data": action_result.get("data"),
            "tasks": action_result.get("data") if action_result.get("action") == "tasks_added" else []
        }
    except Exception as e:
        logger.error(f"AI chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ai/debug")
async def ai_debug(request: Request):
    body = await request.json()
    return {"received": body}

# ==================== ARCHIVE ENDPOINT ====================

@app.get("/api/archive/{user_id}")
async def get_archive(user_id: str, auth_user_id: str = Depends(verify_telegram_auth)):
    """Get archived tasks — all tasks from past days (date < today)"""
    if str(user_id) != str(auth_user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    await validate_user(user_id)
    
    # Search user by both int and string telegram_id
    user = await db.users.find_one({
        "$or": [
            {"telegram_id": user_id},
            {"telegram_id": int(user_id) if str(user_id).isdigit() else user_id}
        ]
    })
    if not user:
        return {"data": []}
    
    now_tashkent = datetime.now(TASHKENT_TZ)
    today_str = now_tashkent.date().isoformat()
    
    # Get all tasks from past days
    past_tasks = await db.tasks.find({
        "$and": [
            {"$or": [
                {"user_id": user["_id"]},
                {"telegram_id": user_id},
                {"telegram_id": int(user_id) if str(user_id).isdigit() else user_id},
            ]},
            {"date": {"$lt": today_str}},
            {"status": {"$ne": "deleted"}},
            {"is_recurring": {"$ne": True}},
        ]
    }).sort("date", -1).to_list(500)
    
    # Group by date
    grouped = {}
    for task in past_tasks:
        date_str = task.get("date") or task.get("created_at", datetime.utcnow()).date().isoformat()
        if date_str not in grouped:
            grouped[date_str] = []
        
        task = sanitize_mongo_doc(task)
        task["done"] = task.get("done", task.get("is_done", False) or task.get("status") == "done")
        p = task.get("priority", "medium")
        if p == "normal": p = "medium"
        task["priority"] = p
        grouped[date_str].append(task)
    
    # Convert to list format
    result = [
        {
            "date": date,
            "count": len(tasks),
            "done_count": sum(1 for t in tasks if t.get("done")),
            "tasks": tasks
        }
        for date, tasks in sorted(grouped.items(), reverse=True)
    ]
    
    return {"data": result}

# ==================== STATS ENDPOINT ====================

@app.get("/api/stats/{user_id}")
async def get_stats(user_id: str, auth_user_id: str = Depends(verify_telegram_auth)):
    """Get user statistics"""
    if str(user_id) != str(auth_user_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    await validate_user(user_id)
    
    # Search user by both int and string telegram_id
    user = await db.users.find_one({
        "$or": [
            {"telegram_id": user_id},
            {"telegram_id": int(user_id) if str(user_id).isdigit() else user_id}
        ]
    })
    if not user:
        return {"data": {}}
    
    now = datetime.utcnow()
    today = now.date()
    week_ago = today - timedelta(days=7)
    
    # Build user query that matches both user_id and telegram_id
    user_match = {"$or": [
        {"user_id": user["_id"]},
        {"telegram_id": user_id},
        {"telegram_id": int(user_id) if str(user_id).isdigit() else user_id},
    ]}
    
    # Get today's tasks using 'date' string field (consistent with how bot stores tasks)
    today_tasks = await db.tasks.find({
        **user_match,
        "date": today.isoformat()
    }).to_list(None)
    
    today_completed = len([t for t in today_tasks if t.get("done") or t.get("is_done", False) or t.get("status") == "done"])
    today_total = len(today_tasks)
    
    # Get this week's tasks — generate date strings for the week
    week_dates = [(week_ago + timedelta(days=i)).isoformat() for i in range(8)]
    week_tasks = await db.tasks.find({
        **user_match,
        "date": {"$in": week_dates}
    }).to_list(None)
    
    # Fallback: also fetch by created_at range for tasks without 'date' field
    fallback_tasks = await db.tasks.find({
        **user_match,
        "date": {"$exists": False},
        "created_at": {
            "$gte": datetime.combine(week_ago, datetime.min.time()),
            "$lt": datetime.combine(today + timedelta(days=1), datetime.min.time())
        }
    }).to_list(None)
    
    # Merge and deduplicate
    seen_ids = {str(t["_id"]) for t in week_tasks}
    for t in fallback_tasks:
        if str(t["_id"]) not in seen_ids:
            week_tasks.append(t)
    
    week_completed = len([t for t in week_tasks if t.get("done") or t.get("is_done", False) or t.get("status") == "done"])
    week_total = len(week_tasks)
    
    # Priority distribution
    high_priority = len([t for t in week_tasks if t.get("priority") == "high" and not (t.get("done") or t.get("is_done", False) or t.get("status") == "done")])
    medium_priority = len([t for t in week_tasks if (t.get("priority") in ("medium", "normal")) and not (t.get("done") or t.get("is_done", False) or t.get("status") == "done")])
    low_priority = len([t for t in week_tasks if t.get("priority") == "low" and not (t.get("done") or t.get("is_done", False) or t.get("status") == "done")])
    
    # Streak calculation using 'date' field
    streak = 0
    check_date = today
    while True:
        date_str = check_date.isoformat()
        date_tasks = await db.tasks.find({
            **user_match,
            "date": date_str
        }).to_list(None)
        
        if not date_tasks or not any((t.get("done") or t.get("is_done", False) or t.get("status") == "done") for t in date_tasks):
            break
        
        streak += 1
        check_date -= timedelta(days=1)
        
        if streak > 365:  # Safety limit
            break
    
    return {
        "data": {
            "todayCompleted": today_completed,
            "todayTotal": today_total,
            "weekCompleted": week_completed,
            "weekTotal": week_total,
            "completionRate": round((week_completed / week_total * 100) if week_total > 0 else 0),
            "streak": streak,
            "highPriority": high_priority,
            "mediumPriority": medium_priority,
            "lowPriority": low_priority,
        }
    }

# ==================== ADMIN ENDPOINTS ====================

@app.get("/api/admin/dashboard")
async def admin_dashboard(is_admin: bool = Depends(verify_admin_auth)):
    """Admin overview: total users, revenue, promos, segments"""
    now = datetime.utcnow()
    today = now.date()
    month_start = today.replace(day=1)

    total_users = await db.users.count_documents({})
    
    # Today active = users with last_active today
    today_start = datetime.combine(today, datetime.min.time())
    today_active = await db.users.count_documents({"last_active": {"$gte": today_start}})
    
    # This week active
    week_ago = now - timedelta(days=7)
    week_active = await db.users.count_documents({"last_active": {"$gte": week_ago}})

    # Subscriptions
    paid_users = await db.users.count_documents({"is_paid": True})
    trial_users = await db.users.count_documents({"subscription_status": "trial"})
    expired_users = await db.users.count_documents({"subscription_status": "expired"})

    # Revenue this month
    month_start_dt = datetime.combine(month_start, datetime.min.time())
    month_paid = await db.users.count_documents({
        "is_paid": True,
        "paid_until": {"$gte": now}
    })
    price_doc = await db.settings.find_one({"key": "subscription_price"})
    price = price_doc["value"] if price_doc else 15000
    monthly_revenue = month_paid * price

    # Promo stats
    promos = await db.promos.find({}).to_list(100)
    total_promo_created = len(promos)
    total_promo_used = sum(p.get("used_count", 0) for p in promos)

    # Segments
    new_count = await db.users.count_documents({"segment": "new"})
    active_count = await db.users.count_documents({"segment": "active"})
    power_count = await db.users.count_documents({"segment": "power_user"})

    # Total tasks & done tasks
    total_tasks = await db.tasks.count_documents({})
    done_tasks = await db.tasks.count_documents({"$or": [{"done": True}, {"is_done": True}, {"status": "done"}]})

    # New users this week
    new_this_week = await db.users.count_documents({"created_at": {"$gte": week_ago}})

    return {
        "totalUsers": total_users,
        "todayActive": today_active,
        "weekActive": week_active,
        "paidUsers": paid_users,
        "trialUsers": trial_users,
        "expiredUsers": expired_users,
        "monthlyRevenue": monthly_revenue,
        "subscriptionPrice": price,
        "totalPromos": total_promo_created,
        "totalPromoUsed": total_promo_used,
        "promos": [{"code": p.get("code"), "discount": p.get("discount_percent", 0), "used": p.get("used_count", 0), "max": p.get("max_uses", 0)} for p in promos],
        "segments": {"new": new_count, "active": active_count, "power_user": power_count},
        "totalTasks": total_tasks,
        "doneTasks": done_tasks,
        "newThisWeek": new_this_week
    }


@app.get("/api/admin/users")
async def admin_users(is_admin: bool = Depends(verify_admin_auth), q: str = Query(default="", description="Search query")):
    """Get all users with optional search"""
    query_filter = {}
    if q:
        # Search by username or telegram_id
        try:
            tid = int(q)
            query_filter = {"$or": [
                {"username": {"$regex": q, "$options": "i"}},
                {"telegram_id": tid},
                {"telegram_id": q}
            ]}
        except ValueError:
            query_filter = {"username": {"$regex": q, "$options": "i"}}

    users = await db.users.find(query_filter).sort("last_active", -1).to_list(500)

    users_data = []
    for u in users:
        users_data.append({
            "id": str(u.get("telegram_id", "")),
            "username": u.get("username", "—"),
            "language": u.get("language", "uz"),
            "segment": u.get("segment", "new"),
            "interactionCount": u.get("interaction_count", 0),
            "isPaid": u.get("is_paid", False),
            "subscriptionStatus": u.get("subscription_status", "—"),
            "lastActive": u.get("last_active").isoformat() if u.get("last_active") else "—",
            "createdAt": u.get("created_at").isoformat() if u.get("created_at") else "—",
        })

    return {"users": users_data, "total": len(users_data)}


@app.get("/api/admin/users/{user_id}")
async def admin_user_detail(user_id: str, is_admin: bool = Depends(verify_admin_auth)):
    """Get detailed info for a single user"""
    user = await db.users.find_one({
        "$or": [
            {"telegram_id": user_id},
            {"telegram_id": int(user_id) if user_id.isdigit() else user_id}
        ]
    })
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Count tasks for this user
    user_match = {"$or": [
        {"telegram_id": user_id},
        {"telegram_id": int(user_id) if user_id.isdigit() else user_id},
        {"user_id": user["_id"]}
    ]}
    total_tasks = await db.tasks.count_documents(user_match)
    done_tasks = await db.tasks.count_documents({**user_match, "$or": [{"done": True}, {"is_done": True}]})

    return {
        "id": str(user.get("telegram_id", "")),
        "username": user.get("username", "—"),
        "language": user.get("language", "uz"),
        "segment": user.get("segment", "new"),
        "interactionCount": user.get("interaction_count", 0),
        "communicationStyle": user.get("communication_style", "unknown"),
        "habits": user.get("habits", []),
        "personality": user.get("personality", {}),
        "isPaid": user.get("is_paid", False),
        "subscriptionStatus": user.get("subscription_status", "—"),
        "paidUntil": user.get("paid_until").isoformat() if user.get("paid_until") else "—",
        "trialEnd": user.get("trial_end").isoformat() if user.get("trial_end") else "—",
        "lastActive": user.get("last_active").isoformat() if user.get("last_active") else "—",
        "createdAt": user.get("created_at").isoformat() if user.get("created_at") else "—",
        "webPin": user.get("web_pin", "—"),
        "totalTasks": total_tasks,
        "doneTasks": done_tasks,
        "completionRate": round((done_tasks / total_tasks * 100) if total_tasks > 0 else 0),
    }


@app.get("/api/admin/analytics")
async def admin_analytics(is_admin: bool = Depends(verify_admin_auth)):
    """Analytics: segments, activity hours, top users, weekly trend"""
    now = datetime.utcnow()

    # Segments
    new_count = await db.users.count_documents({"segment": "new"})
    active_count = await db.users.count_documents({"segment": "active"})
    power_count = await db.users.count_documents({"segment": "power_user"})

    # Language distribution
    uz_count = await db.users.count_documents({"language": "uz"})
    ru_count = await db.users.count_documents({"language": "ru"})
    en_count = await db.users.count_documents({"language": "en"})

    # Top 10 most active users
    top_users_cursor = db.users.find({}).sort("interaction_count", -1).limit(10)
    top_users = []
    async for u in top_users_cursor:
        top_users.append({
            "id": str(u.get("telegram_id", "")),
            "username": u.get("username", "—"),
            "interactions": u.get("interaction_count", 0),
            "segment": u.get("segment", "new"),
        })

    # Activity by hour (estimate from last_active across all users)
    hourly_activity = [0] * 24
    recent_users = await db.users.find({"last_active": {"$exists": True}}).to_list(1000)
    for u in recent_users:
        la = u.get("last_active")
        if isinstance(la, datetime):
            # Convert to Tashkent
            tashkent_hour = (la.hour + 5) % 24
            hourly_activity[tashkent_hour] += 1

    # New users per day (last 7 days)
    daily_new_users = []
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).date()
        d_start = datetime.combine(d, datetime.min.time())
        d_end = datetime.combine(d + timedelta(days=1), datetime.min.time())
        count = await db.users.count_documents({"created_at": {"$gte": d_start, "$lt": d_end}})
        daily_new_users.append({"date": d.isoformat(), "count": count})

    return {
        "segments": {"new": new_count, "active": active_count, "power_user": power_count},
        "languages": {"uz": uz_count, "ru": ru_count, "en": en_count},
        "topUsers": top_users,
        "hourlyActivity": hourly_activity,
        "dailyNewUsers": daily_new_users,
    }


@app.get("/api/admin/system")
async def admin_system(is_admin: bool = Depends(verify_admin_auth)):
    """System status: bot health, API keys, error log"""
    from bot.services.ai import GROQ_API_KEYS, _current_key_index, _mask_key

    # API health
    api_ok = True
    try:
        await db.command("ping")
    except Exception:
        api_ok = False

    # Groq keys info
    keys_info = []
    for i, k in enumerate(GROQ_API_KEYS):
        keys_info.append({
            "index": i + 1,
            "masked": _mask_key(k),
            "active": i == _current_key_index,
        })

    # Error log from recent API logs (last 20 entries)
    error_logs = []
    try:
        logs = await db.error_logs.find({}).sort("timestamp", -1).to_list(20)
        for log in logs:
            error_logs.append({
                "timestamp": log.get("timestamp", "").isoformat() if isinstance(log.get("timestamp"), datetime) else str(log.get("timestamp", "")),
                "message": log.get("message", ""),
                "source": log.get("source", ""),
            })
    except Exception:
        pass  # error_logs collection may not exist yet

    return {
        "dbConnected": api_ok,
        "apiKeysTotal": len(GROQ_API_KEYS),
        "apiKeysCurrent": _current_key_index + 1,
        "apiKeys": keys_info,
        "uptime": True,
        "errorLogs": error_logs,
    }


# ==================== ADMIN AI CHAT ====================

class AdminChatRequest(BaseModel):
    message: str

@app.post("/api/admin/ai/chat")
async def admin_ai_chat(req: AdminChatRequest, is_admin: bool = Depends(verify_admin_auth)):
    """AI chat for admin — AI knows it is talking to the system admin"""
    try:
        # Gather context data for AI
        total_users = await db.users.count_documents({})
        paid_users = await db.users.count_documents({"is_paid": True})
        today = datetime.now(TASHKENT_TZ).date()
        today_str = today.isoformat()
        today_tasks = await db.tasks.count_documents({"date": today_str})
        today_done = await db.tasks.count_documents({"date": today_str, "$or": [{"done": True}, {"is_done": True}]})

        new_seg = await db.users.count_documents({"segment": "new"})
        active_seg = await db.users.count_documents({"segment": "active"})
        power_seg = await db.users.count_documents({"segment": "power_user"})

        admin_system_prompt = f"""You are the AI assistant for the ADMIN of "PlanAI" productivity bot.
You are NOT talking to a regular user — you are talking to the SYSTEM ADMINISTRATOR / OWNER.

CURRENT TIME: {datetime.now(TASHKENT_TZ).strftime('%H:%M')}, DATE: {today_str} (Tashkent UTC+5)

YOUR ROLE:
- You are the admin's strategic advisor and analytics assistant
- Provide insights about user behavior, trends, and system health
- Give actionable recommendations for growing the user base
- Be professional, data-driven, but friendly
- Use Uzbek language by default (admin's native language), but switch if admin writes in another language

SYSTEM DATA (live):
- Total users: {total_users}
- Paid subscribers: {paid_users}
- Trial/free users: {total_users - paid_users}
- Segments: New={new_seg}, Active={active_seg}, Power={power_seg}
- Today's tasks created: {today_tasks}
- Today's tasks completed: {today_done}

RULES:
- Always address the admin respectfully but as a peer/colleague
- If asked about users, provide analytical insights
- If asked for advice, give strategic business recommendations
- Keep responses concise (2-4 sentences max)
- Use 1-2 relevant emojis
- Never pretend to be a regular user assistant
"""

        from bot.services.ai import call_groq
        response = await call_groq(
            messages=[
                {"role": "system", "content": admin_system_prompt},
                {"role": "user", "content": req.message}
            ],
            max_tokens=500,
        )

        return {"message": response}
    except RuntimeError:
        return {"message": "AI hozir band, bir daqiqadan keyin urinib ko'ring 🙏"}
    except Exception as e:
        logger.error(f"Admin AI chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== HEALTH CHECK ====================

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok"}

# Redirect /dashboard -> /dashboard/
from starlette.responses import RedirectResponse

@app.get("/dashboard")
async def dashboard_redirect():
    return RedirectResponse(url="/dashboard/", status_code=301)

# Serve Dashboard static files
dashboard_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "web-dashboard")
app.mount("/dashboard", StaticFiles(directory=dashboard_path, html=True), name="dashboard_static")

# Serve Mini App static files (must be after API routes)
static_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "mini-app")
app.mount("/", StaticFiles(directory=static_path, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
