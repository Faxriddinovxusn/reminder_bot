import logging
import os
from datetime import datetime, date, timedelta, timezone
from typing import Optional, Dict, Any, List

from dotenv import load_dotenv
from groq import Groq
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from bson import ObjectId

from bot.services.db import connect, get_db, close
from bot.models.user import create_user, get_user_by_telegram_id, set_language, ensure_indexes, get_subscription_status
from bot.models.task import create_task, get_tasks_for_user_on_date, mark_task_done
from bot.services.ai import generate_summary
from bot.handlers.start import start, language_callback, timezone_callback, handle_custom_timezone, web_command, app_command, free_command, language_command
from bot.handlers.todo import (
    plan_command, add_command, tasks_command, done_callback, ai_chat, 
    reminder_preference_callback, confirm_plan_callback, reminder_choice_callback, task_status_callback, plan_type_callback
)
from bot.handlers.admin import (
    admin_add_admin, admin_remove_admin, admin_promo,
    admin_help, admin_send, admin_cancel
)
from bot.handlers.custdev import admin_custdev_create, admin_custdev_send, custdev_response_handler
from bot.handlers.payment import payment_screenshot_handler, payment_callback
from bot.handlers.voice import voice_handler
from bot.messages import messages

# Load environment
load_dotenv(dotenv_path="../.env")

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MONGODB_URI = os.getenv("MONGODB_URI")
ADMIN_ID = os.getenv("ADMIN_ID")
DB_NAME = os.getenv("MONGODB_DB", "plan_reminder")

# Setup logging
logging.basicConfig(level=logging.INFO)

# Scheduler
scheduler = AsyncIOScheduler()

async def check_reminders(application) -> None:
    try:
        now_utc = datetime.utcnow()
        db = get_db()

        tasks = await db.tasks.find({
            "scheduled_time": {"$exists": True, "$ne": None},
            "reminder_sent": False,
            "is_done": False,
            "status": "pending"
        }).to_list(length=500)

        for task in tasks:
            try:
                scheduled = task.get("scheduled_time")
                if not scheduled:
                    continue
                # Ensure scheduled is a naive UTC datetime for comparison
                if scheduled.tzinfo is not None:
                    scheduled = scheduled.astimezone(timezone.utc).replace(tzinfo=None)
                
                offset = int(task.get("reminder_offset", 10) or 10)
                remind_at = scheduled - timedelta(minutes=offset)

                # Only remind if the current time has actually hit the reminder time,
                # but don't notify if the task scheduled time has already passed.
                # Adding a small buffer (e.g. 5 minutes) past the scheduled time just in case.
                if remind_at <= now_utc < (scheduled + timedelta(minutes=5)):
                    user = await db.users.find_one({"telegram_id": {
                        "$in": [task.get("telegram_id"), str(task.get("telegram_id")), task.get("user_id")]
                    }})
                    if not user:
                        # Fallback try find by _id if user_id is ObjectId
                        user = await db.users.find_one({"_id": task.get("user_id")})
                    
                    if not user:
                        continue
                        
                    lang = user.get("language", "uz")
                    msgs = {
                        "uz": f'⏰ "{task["title"]}" ga {offset} daqiqa qoldi. Tayyorlaning!',
                        "ru": f'⏰ До "{task["title"]}" осталось {offset} минут. Приготовьтесь!',
                        "en": f'⏰ {offset} minutes left until "{task["title"]}". Get ready!'
                    }
                    text = msgs.get(lang, msgs["uz"])

                    # Previous scheduled task (if any)
                    prev_task_cur = await db.tasks.find({
                        "user_id": task["user_id"],
                        "date": task["date"],
                        "scheduled_time": {"$lt": scheduled.astimezone(timezone.utc).replace(tzinfo=None)},
                        "is_recurring": {"$ne": True}
                    }).sort("scheduled_time", -1).to_list(length=1)
                    
                    prev_task = prev_task_cur[0] if prev_task_cur else None

                    keyboard = []
                    if prev_task and prev_task.get("status") == "pending":
                        prev_options = {
                            "uz": f"\n\n\"{prev_task['title']}\" ni bajardingizmi?",
                            "ru": f"\n\nВыполнили \"{prev_task['title']}\"?",
                            "en": f"\n\nDid you complete \"{prev_task['title']}\"?",
                        }
                        text += prev_options.get(lang, prev_options["uz"])
                        prev_id = str(prev_task["_id"])
                        keyboard = [
                            [
                                InlineKeyboardButton("✅ Bajarildi", callback_data=f"task_status_done_{prev_id}"),
                                InlineKeyboardButton("🔄 Jarayonda", callback_data=f"task_status_inprogress_{prev_id}")
                            ],
                            [
                                InlineKeyboardButton("❌ B.qilindi", callback_data=f"task_status_skipped_{prev_id}"),
                                InlineKeyboardButton("⏰ Vaqtni suring", callback_data=f"task_status_postponed_{prev_id}")
                            ]
                        ]
                        
                    await application.bot.send_message(
                        chat_id=user["telegram_id"], 
                        text=text,
                        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
                    )
                    await db.tasks.update_one({"_id": task["_id"]}, {"$set": {"reminder_sent": True}})
            except Exception as e:
                logging.exception("check_reminders reminder loop error: %s", e)

        arrived_tasks = await db.tasks.find({
            "scheduled_time": {"$exists": True, "$ne": None},
            "arrival_sent": False,
            "is_done": False,
            "status": "pending"
        }).to_list(length=500)

        for task in arrived_tasks:
            try:
                scheduled = task.get("scheduled_time")
                if not scheduled:
                    continue
                if scheduled.tzinfo is not None:
                    scheduled = scheduled.astimezone(timezone.utc).replace(tzinfo=None)
                if now_utc >= scheduled:
                    user = await db.users.find_one({"telegram_id": {
                        "$in": [task.get("telegram_id"), str(task.get("telegram_id")), task.get("user_id")]
                    }})
                    if not user:
                        user = await db.users.find_one({"_id": task.get("user_id")})
                    if not user:
                        continue
                    
                    lang = user.get("language", "uz")
                    user_offset = int(user.get("timezone_offset", 5) or 5)
                    user_tz = timezone(timedelta(hours=user_offset))
                    task_time_str = scheduled.astimezone(user_tz).strftime("%H:%M")
                    
                    motivations = {
                        "uz": ["Olg'a! Siz buni eplaysiz!", "Harakatdan to'xtamang!", "Yana bir qadam oldinga!", "Bugun sizning kuningiz!"],
                        "ru": ["Вперёд!", "У вас всё получится!", "Не останавливайтесь!", "Ещё один шаг вперёд!"],
                        "en": ["Go for it!", "You can do this!", "Keep moving!", "One more step forward!"]
                    }
                    import random
                    mot = random.choice(motivations.get(lang, motivations["uz"]))
                    text = f"🔔 Soat {task_time_str}. \"{task['title']}\" vaqti keldi! 💪 {mot}"

                    await application.bot.send_message(
                        chat_id=user["telegram_id"],
                        text=text
                    )
                    from bot.models.user import log_command_to_history
                    await log_command_to_history(user["telegram_id"], f"[Vazifa eslatmasi ({task['title']})]", text)
                    await db.tasks.update_one({"_id": task["_id"]}, {"$set": {"arrival_sent": True}})
            except Exception as e:
                logging.exception("check_reminders arrival loop error: %s", e)
    except Exception as e:
        logging.exception("check_reminders error: %s", e)

async def send_evening_report(application) -> None:
    try:
        db = get_db()

        users = await db.users.find({}).to_list(length=1000)
        for user in users:
            try:
                telegram_id = user["telegram_id"]
                lang = user.get("language", "uz")
                
                user_offset = int(user.get("timezone_offset", 5) or 5)
                user_tz = timezone(timedelta(hours=user_offset))
                now_local = datetime.now(user_tz)
                
                # Only send if it's ~22:00 in user's local time (21:30–22:30 window)
                if not (21 <= now_local.hour <= 22):
                    continue
                
                today = now_local.date()
                web_pin = user.get("web_pin")
                if not web_pin:
                    import random
                    web_pin = str(random.randint(10000, 99999))
                    db.users.update_one({"_id": user["_id"]}, {"$set": {"web_pin": web_pin}})
                
                trial_end = user.get("trial_end")
                is_paid = user.get("is_paid", False)
                paid_until = user.get("paid_until")
                now_utc = datetime.utcnow()
                if not (trial_end and now_utc < trial_end) and not (is_paid and paid_until and now_utc < paid_until):
                    continue

                tasks = await db.tasks.find({
                    "date": today.isoformat(),
                    "is_recurring": {"$ne": True},
                    "$or": [
                        {"user_id": user["_id"]},
                        {"telegram_id": telegram_id},
                        {"user_id": telegram_id},
                    ],
                }).to_list(length=100)
                if not tasks:
                    continue

                done = [task for task in tasks if task.get("status") == "done" or task.get("is_done")]
                skipped = [task for task in tasks if task.get("status") == "skipped"]
                total = len(tasks)
                done_count = len(done)
                productivity = round((done_count / total) * 100) if total > 0 else 0

                done_list = "\n".join([f"✅ {task['title']}" for task in done]) or "—"
                skip_list = "\n".join([f"❌ {task['title']}" for task in skipped]) or "—"
                summary = await generate_summary([task["title"] for task in done], [task["title"] for task in skipped], lang)

                from bot.config import MINI_APP_URL
                dash_url = f"{MINI_APP_URL}/dashboard/" if MINI_APP_URL else "https://your-domain.com/dashboard/"
                
                reports = {
                    "uz": f"📊 Bugungi hisobot:\n\n✅ Bajarildi: {done_count}/{total}\n📈 Samaradorlik: {productivity}%\n\n{done_list}\n\nQoldirilgan:\n{skip_list}\n\n🤖 {summary}\n\nErtaga ham reja tuzasizmi?\n\n💻 *Saytga kirish:*\n🔗 {dash_url}\n🔑 *PIN*: `{web_pin}`",
                    "ru": f"📊 Итоги дня:\n\n✅ Выполнено: {done_count}/{total}\n📈 Продуктивность: {productivity}%\n\n{done_list}\n\nПропущено:\n{skip_list}\n\n🤖 {summary}\n\nПланируете завтра?\n\n💻 *Вход на сайт:*\n🔗 {dash_url}\n🔑 *PIN*: `{web_pin}`",
                    "en": f"📊 Daily Report:\n\n✅ Done: {done_count}/{total}\n📈 Productivity: {productivity}%\n\n{done_list}\n\nSkipped:\n{skip_list}\n\n🤖 {summary}\n\nPlan tomorrow?\n\n💻 *Dashboard access:*\n🔗 {dash_url}\n🔑 *PIN*: `{web_pin}`"
                }

                keyboard = [[
                    InlineKeyboardButton("✅ Ha", callback_data="plan_tomorrow_yes"),
                    InlineKeyboardButton("😴 Ertaga", callback_data="plan_tomorrow_no")
                ]]

                report_text = reports.get(lang, reports["uz"])
                await application.bot.send_message(
                    chat_id=telegram_id,
                    text=report_text,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                from bot.models.user import log_command_to_history
                await log_command_to_history(telegram_id, "[Avtomatik tizim xabari / Botning yuborgan xabari]", report_text)
            except Exception as e:
                logging.error(f"Evening report error for {user.get('telegram_id')}: {e}")
    except Exception as e:
        logging.exception("send_evening_report error: %s", e)

async def send_weekly_report(application) -> None:
    try:
        db = get_db()

        users = await db.users.find({}).to_list(length=1000)
        for user in users:
            try:
                telegram_id = user["telegram_id"]
                lang = user.get("language", "uz")
                
                user_offset = int(user.get("timezone_offset", 5) or 5)
                user_tz = timezone(timedelta(hours=user_offset))
                now_local = datetime.now(user_tz)
                today = now_local.date()
                
                status = await get_subscription_status(user)
                if status == "expired":
                    continue

                # Fetch tasks for each of the last 7 days
                day_names_uz = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]
                day_stats = []
                total_done = 0
                total_all = 0
                best_day = None
                worst_day = None
                best_pct = -1
                worst_pct = 101

                for i in range(7):
                    d = today - timedelta(days=(6 - i))
                    d_str = d.isoformat()
                    day_tasks = await db.tasks.find({
                        "date": d_str,
                        "is_recurring": {"$ne": True},
                        "$or": [
                            {"user_id": user["_id"]},
                            {"telegram_id": telegram_id},
                            {"user_id": telegram_id},
                        ],
                    }).to_list(length=100)
                    
                    d_total = len(day_tasks)
                    d_done = sum(1 for t in day_tasks if t.get("status") == "done" or t.get("is_done"))
                    total_done += d_done
                    total_all += d_total
                    d_pct = round((d_done / d_total) * 100) if d_total > 0 else 0
                    
                    dn = day_names_uz[d.weekday()]
                    bar = "\u2588" * (d_pct // 10) + "\u2591" * (10 - d_pct // 10)
                    day_stats.append(f"{dn[:3]} {d.strftime('%d.%m')}: {bar} {d_pct}% ({d_done}/{d_total})")
                    
                    if d_total > 0:
                        if d_pct > best_pct:
                            best_pct = d_pct
                            best_day = f"{dn}, {d.strftime('%d.%m')}"
                        if d_pct < worst_pct:
                            worst_pct = d_pct
                            worst_day = f"{dn}, {d.strftime('%d.%m')}"

                if total_all == 0:
                    continue

                productivity = round((total_done / total_all) * 100) if total_all > 0 else 0
                days_chart = "\n".join(day_stats)

                all_tasks = await db.tasks.find({
                    "date": {"$gte": (today - timedelta(days=6)).isoformat(), "$lte": today.isoformat()},
                    "is_recurring": {"$ne": True},
                    "$or": [{"user_id": user["_id"]}, {"telegram_id": telegram_id}, {"user_id": telegram_id}],
                }).to_list(length=500)
                done = [t for t in all_tasks if t.get("status") == "done" or t.get("is_done")]
                skipped = [t for t in all_tasks if t.get("status") == "skipped"]
                summary = await generate_summary([t["title"] for t in done], [t["title"] for t in skipped], lang)

                best_str = f"\n\n\U0001f3c6 Eng yaxshi kun: {best_day} ({best_pct}%)" if best_day else ""
                worst_str = f"\n\U0001f614 Eng past kun: {worst_day} ({worst_pct}%)" if worst_day and worst_day != best_day else ""

                reports = {
                    "uz": f"\U0001f451 Haftalik Hisobot:\n\n\u2705 Bajarildi: {total_done}/{total_all}\n\U0001f4c8 Samaradorlik: {productivity}%\n\n{days_chart}{best_str}{worst_str}\n\n\U0001f916 {summary}\n\nYangi reja tuzasizmi?",
                    "ru": f"\U0001f451 \u0418\u0442\u043e\u0433\u0438 \u043d\u0435\u0434\u0435\u043b\u0438:\n\n\u2705 \u0412\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u043e: {total_done}/{total_all}\n\U0001f4c8 \u041f\u0440\u043e\u0434\u0443\u043a\u0442\u0438\u0432\u043d\u043e\u0441\u0442\u044c: {productivity}%\n\n{days_chart}{best_str}{worst_str}\n\n\U0001f916 {summary}\n\n\u041f\u043b\u0430\u043d\u0438\u0440\u0443\u0435\u0442\u0435 \u043d\u043e\u0432\u0443\u044e \u043d\u0435\u0434\u0435\u043b\u044e?",
                    "en": f"\U0001f451 Weekly Report:\n\n\u2705 Done: {total_done}/{total_all}\n\U0001f4c8 Productivity: {productivity}%\n\n{days_chart}{best_str}{worst_str}\n\n\U0001f916 {summary}\n\nPlan for next week?"
                }

                keyboard = [
                    [InlineKeyboardButton("\U0001f4c5 Kunlik reja", callback_data="plan_type_daily")],
                    [InlineKeyboardButton("\U0001f4c6 Haftalik reja", callback_data="plan_type_weekly")],
                    [InlineKeyboardButton("\U0001f5d3 Oylik reja", callback_data="plan_type_monthly")]
                ]

                report_text = reports.get(lang, reports["uz"])
                await application.bot.send_message(
                    chat_id=telegram_id,
                    text=report_text,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                from bot.models.user import log_command_to_history
                await log_command_to_history(telegram_id, "[Avtomatik tizim xabari / Haftalik hisobot]", report_text)
            except Exception as e:
                logging.error(f"Weekly report error for {user.get('telegram_id')}: {e}")
    except Exception as e:
        logging.exception("send_weekly_report error: %s", e)

async def send_monthly_report(application) -> None:
    try:
        db = get_db()

        users = await db.users.find({}).to_list(length=1000)
        for user in users:
            try:
                telegram_id = user["telegram_id"]
                lang = user.get("language", "uz")
                
                user_offset = int(user.get("timezone_offset", 5) or 5)
                user_tz = timezone(timedelta(hours=user_offset))
                now_local = datetime.now(user_tz)
                today = now_local.date()
                month_ago = today - timedelta(days=30)
                
                status = await get_subscription_status(user)
                if status == "expired":
                    continue

                tasks = await db.tasks.find({
                    "date": {"$gte": month_ago.isoformat(), "$lte": today.isoformat()},
                    "is_recurring": {"$ne": True},
                    "$or": [
                        {"user_id": user["_id"]},
                        {"telegram_id": telegram_id},
                        {"user_id": telegram_id},
                    ],
                }).to_list(length=500)
                if not tasks:
                    continue

                done = [task for task in tasks if task.get("status") == "done" or task.get("is_done")]
                skipped = [task for task in tasks if task.get("status") == "skipped"]
                total = len(tasks)
                done_count = len(done)
                max_display = 15
                productivity = round((done_count / total) * 100) if total > 0 else 0

                done_list = "\n".join([f"✅ {task['title']}" for task in done[:max_display]]) or "—"
                skip_list = "\n".join([f"❌ {task['title']}" for task in skipped[:max_display]]) or "—"
                
                if len(done) > max_display: done_list += f"\n... va yana {len(done)-max_display} ta"
                if len(skipped) > max_display: skip_list += f"\n... va yana {len(skipped)-max_display} ta"

                summary = await generate_summary([task["title"] for task in done], [task["title"] for task in skipped], lang)

                reports = {
                    "uz": f"🏆 Oylik Hisobot:\n\n✅ Bajarildi: {done_count}/{total}\n📈 Samaradorlik: {productivity}%\n\n{done_list}\n\nQoldirilgan:\n{skip_list}\n\n🤖 {summary}\n\nYangi reja tuzasizmi?",
                    "ru": f"🏆 Итоги месяца:\n\n✅ Выполнено: {done_count}/{total}\n📈 Продуктивность: {productivity}%\n\n{done_list}\n\nПропущено:\n{skip_list}\n\n🤖 {summary}\n\nПланируете новый месяц?",
                    "en": f"🏆 Monthly Report:\n\n✅ Done: {done_count}/{total}\n📈 Productivity: {productivity}%\n\n{done_list}\n\nSkipped:\n{skip_list}\n\n🤖 {summary}\n\nPlan for next month?"
                }

                keyboard = [
                    [InlineKeyboardButton("📅 Kunlik reja", callback_data="plan_type_daily")],
                    [InlineKeyboardButton("📆 Haftalik reja", callback_data="plan_type_weekly")],
                    [InlineKeyboardButton("🗓 Oylik reja", callback_data="plan_type_monthly")]
                ]

                report_text = reports.get(lang, reports["uz"])
                await application.bot.send_message(
                    chat_id=telegram_id,
                    text=report_text,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                from bot.models.user import log_command_to_history
                await log_command_to_history(telegram_id, "[Avtomatik tizim xabari / Oylik hisobot]", report_text)
            except Exception as e:
                logging.error(f"Monthly report error for {user.get('telegram_id')}: {e}")
    except Exception as e:
        logging.exception("send_monthly_report error: %s", e)

async def create_recurring_tasks(application) -> None:
    try:
        db = get_db()

        recurring = await db.tasks.find({"is_recurring": True}).to_list(length=1000)
        for template in recurring:
            try:
                # Look up the task owner's timezone
                task_user = None
                if template.get("telegram_id"):
                    task_user = await db.users.find_one({"telegram_id": template["telegram_id"]})
                if not task_user and template.get("user_id"):
                    task_user = await db.users.find_one({"_id": template["user_id"]})
                
                user_offset = int((task_user or {}).get("timezone_offset", 5) or 5)
                user_tz = timezone(timedelta(hours=user_offset))
                today = datetime.now(user_tz).date()
                day_name = today.strftime("%a").lower()
                
                recur_days = template.get("recur_days", ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
                if day_name not in recur_days:
                    continue

                exists = await db.tasks.find_one({
                    "user_id": template["user_id"],
                    "title": template["title"],
                    "date": today.isoformat(),
                    "is_recurring": False
                })
                if exists:
                    continue

                recur_time = template.get("recur_time", "09:00") or "09:00"
                local_scheduled = datetime.combine(today, datetime.strptime(recur_time, "%H:%M").time()).replace(tzinfo=user_tz)
                scheduled_utc = local_scheduled.astimezone(timezone.utc).replace(tzinfo=None)
                reminder_offset = int(template.get("reminder_offset", 10) or 10)

                new_task = {
                    "user_id": template["user_id"],
                    "telegram_id": template.get("telegram_id"),
                    "title": template["title"],
                    "priority": template.get("priority", "normal"),
                    "time": recur_time,
                    "scheduled_time": scheduled_utc,
                    "recur_time": None,
                    "reminder_offset": reminder_offset,
                    "is_done": False,
                    "is_recurring": False,
                    "status": "pending",
                    "reminder_sent": False if reminder_offset > 0 else True,
                    "arrival_sent": False,
                    "date": today.isoformat(),
                    "created_at": datetime.utcnow()
                }
                await db.tasks.insert_one(new_task)
            except Exception as e:
                logging.exception("create_recurring_tasks loop error: %s", e)
    except Exception as e:
        logging.exception("create_recurring_tasks error: %s", e)

async def evening_checkin(app) -> None:
    """Evening check-in: ask users what they accomplished today (runs every hour, checks user's local time)"""
    try:
        db = get_db()
        users = await db.users.find({}).to_list(length=1000)

        for user in users:
            try:
                telegram_id = user["telegram_id"]
                lang = user.get("language", "uz")
                user_offset = int(user.get("timezone_offset", 5) or 5)
                user_tz = timezone(timedelta(hours=user_offset))
                now_local = datetime.now(user_tz)
                
                # Only send if it's ~21:00 in user's local time (20:30–21:30 window)
                if now_local.hour != 21:
                    continue
                
                now = datetime.utcnow()

                # Skip expired users
                status = await get_subscription_status(user)
                if status == "expired":
                    continue

                # Save state: waiting for evening response
                await db.user_states.update_one(
                    {"telegram_id": telegram_id},
                    {"$set": {
                        "state": "evening_checkin_1",
                        "evening_date": now.date().isoformat(),
                        "updated_at": now
                    }},
                    upsert=True
                )

                msgs = {
                    "uz": "🌙 Kechqurun yaxshi!\n\nBugun qaysi ishlarni qildingiz? (text yoki ovoz orqali ayting)",
                    "ru": "🌙 Добрый вечер!\n\nЧто вы сделали сегодня? (напишите или отправьте голосовое)",
                    "en": "🌙 Good evening!\n\nWhat did you accomplish today? (text or voice)"
                }
                await app.bot.send_message(chat_id=telegram_id, text=msgs.get(lang, msgs["uz"]))
            except Exception as e:
                logging.error(f"Evening checkin error {user.get('telegram_id')}: {e}")
    except Exception as e:
        logging.exception("evening_checkin error: %s", e)

async def post_init(application) -> None:
    await connect()
    await ensure_indexes()
    scheduler.add_job(create_recurring_tasks, CronTrigger(hour=0, minute=1), args=[application])
    scheduler.add_job(evening_checkin, CronTrigger(hour=16, minute=0), args=[application])
    scheduler.add_job(send_evening_report, CronTrigger(hour=17, minute=0), args=[application])
    scheduler.add_job(send_weekly_report, CronTrigger(day_of_week='sun', hour=17, minute=0), args=[application])
    scheduler.add_job(send_monthly_report, CronTrigger(day='last', hour=17, minute=0), args=[application])
    scheduler.add_job(check_reminders, "interval", minutes=1, args=[application])
    scheduler.start()

async def post_shutdown(application) -> None:
    await close()
    scheduler.shutdown()

async def master_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user or not update.message: 
            return
            
        # Check if awaiting broadcast
        from bot.models.state import get_state
        state_doc = await get_state(user.id)
        if state_doc.get("state") == "awaiting_broadcast_message":
            from bot.handlers.admin import handle_broadcast_message
            await handle_broadcast_message(update, context)
            return
            
        if state_doc.get("state") == "awaiting_custom_timezone":
            from bot.handlers.start import handle_custom_timezone
            consumed = await handle_custom_timezone(update, context)
            if consumed:
                return

        # Route to original handlers based on content
        if update.message.photo:
            from bot.handlers.payment import payment_screenshot_handler
            await payment_screenshot_handler(update, context)
        elif update.message.voice:
            from bot.handlers.voice import voice_handler
            await voice_handler(update, context)
        elif update.message.text:
            from bot.handlers.todo import ai_chat
            await ai_chat(update, context)
    except Exception as e:
        logging.exception("master_message_handler error: %s", e)

if __name__ == "__main__":
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("web", web_command))
    app.add_handler(CommandHandler("app", app_command))
    app.add_handler(CommandHandler("free", free_command))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(CommandHandler("plan", plan_command))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("add_admin", admin_add_admin))
    app.add_handler(CommandHandler("remove_admin", admin_remove_admin))
    app.add_handler(CommandHandler("promo", admin_promo))
    app.add_handler(CommandHandler("adminhelp", admin_help))
    app.add_handler(CommandHandler("send", admin_send))
    app.add_handler(CommandHandler("cancel", admin_cancel))
    
    app.add_handler(CallbackQueryHandler(payment_callback, pattern="^pay_"))
    app.add_handler(CallbackQueryHandler(custdev_response_handler, pattern="^custdev_answer_"))
    app.add_handler(CallbackQueryHandler(language_callback, pattern="^lang_"))
    app.add_handler(CallbackQueryHandler(timezone_callback, pattern="^tz_"))
    app.add_handler(CallbackQueryHandler(plan_type_callback, pattern="^plan_type_"))
    app.add_handler(CallbackQueryHandler(confirm_plan_callback, pattern="^plan_confirm_"))
    app.add_handler(CallbackQueryHandler(done_callback, pattern="^done_"))
    app.add_handler(CallbackQueryHandler(reminder_choice_callback, pattern="^reminder_"))
    app.add_handler(CallbackQueryHandler(task_status_callback, pattern="^task_status_"))
    
    # Unified handler for all messages that are not commands
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, master_message_handler))
    
    app.run_polling()
