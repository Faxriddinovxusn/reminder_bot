import asyncio
import logging
import os
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List

from dotenv import load_dotenv
from groq import Groq
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from bson import ObjectId

# Load environment
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MONGODB_URI = os.getenv("MONGODB_URI")
ADMIN_ID = os.getenv("ADMIN_ID")
DB_NAME = os.getenv("MONGODB_DB", "plan_reminder")

# Setup logging
logging.basicConfig(level=logging.INFO)

# Messages
messages = {
    "uz": {
        "choose_language": "Tilni tanlang:",
        "welcome": "Xush kelibsiz! 👋",
        "task_added": "Vazifa qo'shildi ✅",
        "no_tasks": "Bugun vazifalar yoq.",
        "trial_expired": "Sinov muddati tugadi. Obuna bo'ling.",
        "done_tasks": "Bajarilgan",
        "undone_tasks": "Bajarilmagan",
        "summary": "Xulosa"
    },
    "ru": {
        "choose_language": "Выберите язык:",
        "welcome": "Добро пожаловать! 👋",
        "task_added": "Задача добавлена ✅",
        "no_tasks": "Сегодня задач нет.",
        "trial_expired": "Пробный период закончился. Оформите подписку.",
        "done_tasks": "Выполненные",
        "undone_tasks": "Невыполненные",
        "summary": "Резюме"
    },
    "en": {
        "choose_language": "Choose language:",
        "welcome": "Welcome! 👋",
        "task_added": "Task added ✅",
        "no_tasks": "No tasks for today.",
        "trial_expired": "Trial expired. Please subscribe.",
        "done_tasks": "Done",
        "undone_tasks": "Undone",
        "summary": "Summary"
    }
}

# DB
client: Optional[AsyncIOMotorClient] = None
db: Optional[AsyncIOMotorDatabase] = None

def _validate_uri() -> None:
    if not MONGODB_URI:
        raise RuntimeError("MONGODB_URI is not set")

async def connect_db() -> None:
    global client, db
    try:
        _validate_uri()
        client = AsyncIOMotorClient(MONGODB_URI)
        db = client[DB_NAME]
        logging.info("Connected to MongoDB")
    except Exception as e:
        logging.exception("DB connect error: %s", e)
        raise

def get_db() -> AsyncIOMotorDatabase:
    if db is None:
        raise RuntimeError("DB not connected")
    return db

async def close_db() -> None:
    global client
    try:
        if client:
            client.close()
            client = None
    except Exception as e:
        logging.exception("DB close error: %s", e)

# User model
USERS_COLL = "users"

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
            "created_at": now,
        }
        await db[USERS_COLL].update_one({"telegram_id": telegram_id}, {"$setOnInsert": user_doc}, upsert=True)
        return await get_user_by_telegram_id(telegram_id)
    except Exception as e:
        logging.exception("create_user error: %s", e)
        raise

async def get_user_by_telegram_id(telegram_id: int) -> Optional[Dict[str, Any]]:
    try:
        db = get_db()
        return await db[USERS_COLL].find_one({"telegram_id": telegram_id})
    except Exception as e:
        logging.exception("get_user error: %s", e)
        raise

async def set_language(telegram_id: int, language: str) -> Optional[Dict[str, Any]]:
    try:
        db = get_db()
        await db[USERS_COLL].update_one({"telegram_id": telegram_id}, {"$set": {"language": language}})
        return await get_user_by_telegram_id(telegram_id)
    except Exception as e:
        logging.exception("set_language error: %s", e)
        raise

async def ensure_user_indexes() -> None:
    try:
        db = get_db()
        await db[USERS_COLL].create_index("telegram_id", unique=True)
    except Exception as e:
        logging.exception("ensure_user_indexes error: %s", e)

# Task model
TASKS_COLL = "tasks"

async def create_task(user_id: int, title: str, priority: str = "normal", date_: Optional[date] = None) -> str:
    try:
        db = get_db()
        if date_ is None:
            date_ = date.today()
        now = datetime.utcnow()
        task_doc = {
            "user_id": user_id,
            "title": title,
            "is_done": False,
            "priority": priority,
            "date": date_.isoformat(),
            "created_at": now,
        }
        result = await db[TASKS_COLL].insert_one(task_doc)
        return str(result.inserted_id)
    except Exception as e:
        logging.exception("create_task error: %s", e)
        raise

async def get_tasks_for_user_on_date(user_id: int, date_: Optional[date] = None) -> List[Dict[str, Any]]:
    try:
        db = get_db()
        if date_ is None:
            date_ = date.today()
        cursor = db[TASKS_COLL].find({"user_id": user_id, "date": date_.isoformat()})
        tasks: List[Dict[str, Any]] = []
        async for doc in cursor:
            tasks.append(doc)
        return tasks
    except Exception as e:
        logging.exception("get_tasks error: %s", e)
        raise

async def mark_task_done(task_id: str) -> None:
    try:
        db = get_db()
        await db[TASKS_COLL].update_one({"_id": ObjectId(task_id)}, {"$set": {"is_done": True}})
    except Exception as e:
        logging.exception("mark_task_done error: %s", e)
        raise

# AI service
groq_client = Groq(api_key=GROQ_API_KEY)

async def generate_summary(done_tasks: List[str], undone_tasks: List[str], language: str) -> str:
    try:
        prompt = f"Generate a 1-2 sentence motivational summary in {language} about today's tasks. Done: {', '.join(done_tasks) if done_tasks else 'none'}. Undone: {', '.join(undone_tasks) if undone_tasks else 'none'}."
        response = groq_client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logging.exception("generate_summary error: %s", e)
        return "Summary generation failed."

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if user is None:
            return
        tg_id = user.id
        username = user.username
        existing = await get_user_by_telegram_id(tg_id)
        if not existing:
            await create_user(tg_id, username)

        keyboard = [
            [InlineKeyboardButton("🇺🇿 O'zbek", callback_data="lang_uz")],
            [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
            [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(messages["uz"]["choose_language"], reply_markup=reply_markup)
    except Exception as e:
        logging.exception("start error: %s", e)
        if update.message:
            await update.message.reply_text("An error occurred.")

async def language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        data = query.data or ""
        parts = data.split("_")
        lang = parts[1] if len(parts) > 1 else "uz"
        user = query.from_user
        if user:
            await set_language(user.id, lang)
        text = messages.get(lang, messages["en"])["welcome"]
        await query.edit_message_text(text)
    except Exception as e:
        logging.exception("language_callback error: %s", e)
        if update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text("An error occurred.")

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user:
            return
        tg_id = user.id
        db_user = await get_user_by_telegram_id(tg_id)
        if not db_user:
            return
        lang = db_user.get("language", "en")
        text = update.message.text or ""
        if not text.startswith("/add "):
            return
        title = text[5:].strip()
        if not title:
            await update.message.reply_text("Please provide a task title.")
            return
        await create_task(tg_id, title)
        await update.message.reply_text(messages.get(lang, messages["en"])["task_added"])
    except Exception as e:
        logging.exception("add_command error: %s", e)
        if update.message:
            await update.message.reply_text("An error occurred.")

async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user:
            return
        tg_id = user.id
        db_user = await get_user_by_telegram_id(tg_id)
        if not db_user:
            return
        lang = db_user.get("language", "en")
        tasks = await get_tasks_for_user_on_date(tg_id)
        if not tasks:
            await update.message.reply_text(messages.get(lang, messages["en"])["no_tasks"])
            return
        text = ""
        keyboard = []
        for i, task in enumerate(tasks, 1):
            status = "✅" if task["is_done"] else "❌"
            text += f"{i}. {status} {task['title']}\n"
            if not task["is_done"]:
                keyboard.append([InlineKeyboardButton(f"✅ Done {task['title']}", callback_data=f"done_{task['_id']}")])
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await update.message.reply_text(text, reply_markup=reply_markup)
    except Exception as e:
        logging.exception("tasks_command error: %s", e)
        if update.message:
            await update.message.reply_text("An error occurred.")

async def done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        if not query:
            return
        await query.answer()
        data = query.data or ""
        if not data.startswith("done_"):
            return
        task_id = data[5:]
        await mark_task_done(task_id)
        await query.edit_message_text("Task marked as done!")
    except Exception as e:
        logging.exception("done_callback error: %s", e)
        if update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text("An error occurred.")

# Scheduler
scheduler = AsyncIOScheduler()

async def send_evening_report() -> None:
    try:
        db = get_db()
        cursor = db[USERS_COLL].find({})
        async for user in cursor:
            tg_id = user["telegram_id"]
            lang = user.get("language", "en")
            now = datetime.utcnow()
            trial_end = user.get("trial_end")
            is_paid = user.get("is_paid", False)
            paid_until = user.get("paid_until")
            if not (trial_end and now < trial_end) and not (is_paid and paid_until and now < paid_until):
                continue
            tasks = await get_tasks_for_user_on_date(tg_id)
            done = [t["title"] for t in tasks if t["is_done"]]
            undone = [t["title"] for t in tasks if not t["is_done"]]
            summary = await generate_summary(done, undone, lang)
            msg_done = messages.get(lang, messages["en"])["done_tasks"]
            msg_undone = messages.get(lang, messages["en"])["undone_tasks"]
            msg_summary = messages.get(lang, messages["en"])["summary"]
            report = f'✅ {msg_done}: {", ".join(done) if done else "yo\'q"}\n❌ {msg_undone}: {", ".join(undone) if undone else "yoq"}\n📊 {msg_summary}: {summary}'
            if app:
                await app.bot.send_message(chat_id=tg_id, text=report)
    except Exception as e:
        logging.exception("send_evening_report error: %s", e)

# Main
async def main() -> None:
    global app
    await connect_db()
    await ensure_user_indexes()

    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CallbackQueryHandler(language_callback, pattern="^lang_"))
    app.add_handler(CallbackQueryHandler(done_callback, pattern="^done_"))

    # Scheduler
    scheduler.add_job(send_evening_report, CronTrigger(hour=16, minute=0))  # 21:00 Tashkent = 16:00 UTC
    scheduler.start()

    try:
        await app.run_polling()
    finally:
        await close_db()
        scheduler.shutdown()

if __name__ == "__main__":
    asyncio.run(main())