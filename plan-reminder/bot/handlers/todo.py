from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes
import logging
from datetime import datetime, date, timedelta, timezone
from typing import Optional
import os
import json
import re
from bson import ObjectId

from bot.models.task import create_task, get_tasks_for_user_on_date, mark_task_done, create_scheduled_task, update_task_reminder_offset
from bot.models.state import get_state, set_state, clear_state
from bot.models.user import get_user_by_telegram_id, get_subscription_status
from bot.messages import messages
from bot.services.ai import get_ai_response, extract_tasks_from_schedule, extract_tasks_from_text, generate_summary
from bot.services.db import get_db
from bot.config import MINI_APP_URL

# Store conversation history per user (last 6 messages)
user_histories = {}
AI_CONVERSATIONS_COLL = "ai_conversations"

async def infer_profile_learning(user_message: str, extracted_tasks: list, db_user: dict) -> dict:
    try:
        personality = dict(db_user.get("personality") or {})
        topics_discussed = list(db_user.get("topics_discussed") or [])
        lower_text = user_message.lower()
        word_count = len(user_message.split())

        if word_count <= 6:
            personality["message_style"] = "short"
        elif word_count <= 20:
            personality["message_style"] = "medium"
        else:
            personality["message_style"] = "long"

        formal_markers = [
            "please", "could you", "would you", "iltimos", "\u043f\u043e\u0436\u0430\u043b\u0443\u0439\u0441\u0442\u0430",
            "assalomu", "salom", "hello", "\u0437\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439\u0442\u0435", "rahmat", "thanks",
        ]
        personality["tone"] = "formal" if any(marker in lower_text for marker in formal_markers) else "casual"
        personality["asks_questions"] = "?" in user_message
        personality["uses_emojis"] = any(ord(char) > 10000 for char in user_message)

        detected_topics = []
        topic_keywords = {
            "planning": ["plan", "reja", "schedule", "\u0440\u0430\u0441\u043f\u0438\u0441", "jadval"],
            "tasks": ["task", "todo", "vazifa", "\u0434\u0435\u043b", "\u0437\u0430\u0434\u0430\u0447"],
            "time_management": ["time", "vaqt", "deadline", "\u0441\u0440\u043e\u043a", "\u0432\u0440\u0435\u043c\u044f"],
            "productivity": ["productive", "samarador", "focus", "\u043f\u0440\u043e\u0434\u0443\u043a\u0442\u0438\u0432", "\u0444\u043e\u043a\u0443\u0441"],
            "study": ["study", "lesson", "dars", "\u0443\u0447\u0435\u0431", "\u044d\u043a\u0437\u0430\u043c"],
            "work": ["work", "job", "ish", "\u0440\u0430\u0431\u043e\u0442", "\u043e\u0444\u0438\u0441"],
            "motivation": ["motivation", "motiv", "ilhom", "\u0432\u0434\u043e\u0445\u043d\u043e\u0432", "\u0446\u0435\u043b\u044c"],
            "daily_life": ["home", "family", "uy", "oila", "\u0434\u043e\u043c", "\u0441\u0435\u043c\u044c\u044f"],
        }

        if extracted_tasks:
            detected_topics.extend(["planning", "tasks"])

        for topic, keywords in topic_keywords.items():
            if any(keyword in lower_text for keyword in keywords):
                detected_topics.append(topic)

        if not detected_topics:
            detected_topics.append("general")

        for topic in detected_topics:
            if topic not in topics_discussed:
                topics_discussed.append(topic)

        return {
            "personality": personality,
            "topics_discussed": topics_discussed[-15:],
        }
    except Exception as e:
        logging.exception("infer_profile_learning error: %s", e)
        return {
            "personality": dict(db_user.get("personality") or {}),
            "topics_discussed": list(db_user.get("topics_discussed") or []),
        }

async def get_user_segment(interaction_count: int) -> str:
    try:
        if interaction_count >= 20:
            return "power_user"
        if interaction_count >= 6:
            return "active"
        return "new"
    except Exception as e:
        logging.exception("get_user_segment error: %s", e)
        return "new"

async def store_conversation_summary(
    telegram_id: int,
    username: str,
    language: str,
    segment: str,
    topics_discussed: list,
    user_message: str,
    ai_response: str,
) -> None:
    try:
        db = get_db()
        clean_user_message = re.sub(r"\s+", " ", user_message).strip()
        clean_ai_response = re.sub(r"\s+", " ", ai_response).strip()
        summary = f"User asked: {clean_user_message[:100]} | AI replied: {clean_ai_response[:120]}"
        await db[AI_CONVERSATIONS_COLL].insert_one({
            "telegram_id": telegram_id,
            "username": username,
            "language": language,
            "segment": segment,
            "topics_discussed": topics_discussed[-5:],
            "summary": summary,
            "user_message": clean_user_message[:500],
            "ai_response": clean_ai_response[:500],
            "created_at": datetime.utcnow(),
        })
    except Exception as e:
        logging.exception("store_conversation_summary error: %s", e)

async def update_user_profile_after_message(
    telegram_id: int,
    username: str,
    db_user: dict,
    language: str,
    user_message: str,
    extracted_tasks: list,
    profile_updates: dict,
    ai_response: str = "",
) -> None:
    try:
        learned_profile = await infer_profile_learning(user_message, extracted_tasks, db_user)
        interaction_count = int(profile_updates.get("interaction_count", 0) or 0)
        segment = await get_user_segment(interaction_count)
        db = get_db()
        await db.users.update_one(
            {"telegram_id": telegram_id},
            {
                "$set": {
                    "last_active": profile_updates.get("last_active", datetime.utcnow()),
                    "interaction_count": interaction_count,
                    "segment": segment,
                    "personality": learned_profile.get("personality", {}),
                    "topics_discussed": learned_profile.get("topics_discussed", []),
                    "username": username or db_user.get("username"),
                }
            },
        )
        if ai_response:
            await store_conversation_summary(
                telegram_id=telegram_id,
                username=username or db_user.get("username") or "User",
                language=language,
                segment=segment,
                topics_discussed=learned_profile.get("topics_discussed", []),
                user_message=user_message,
                ai_response=ai_response,
            )
    except Exception as e:
        logging.exception("update_user_profile_after_message error: %s", e)

async def format_plan_confirmation(tasks: list, lang: str = "uz", target_date: str = None) -> str:
    try:
        headers = {
            "uz": "📋 Rejalaringizni tushundim:",
            "ru": "📋 Я понял ваши планы:",
            "en": "📋 I understood your plans:"
        }
        if target_date:
            headers = {
                "uz": f"📅 {target_date} uchun tahlil qilingan reja:",
                "ru": f"📅 Ваш план на {target_date}:",
                "en": f"📅 Plan for {target_date}:"
            }
        footers = {
            "uz": "To'g'rimi?",
            "ru": "Всё верно?",
            "en": "Is this correct?"
        }
        priority_icons = {"high": "🔴", "normal": "🟡", "low": "🟢"}
        lines = [headers.get(lang, headers["uz"]), ""]
        for index, task in enumerate(tasks, 1):
            title = (task.get("title") or "").strip()
            time_text = task.get("time")
            priority = task.get("priority", "normal")
            icon = priority_icons.get(priority, "🟡")
            if time_text:
                lines.append(f"{index}. {icon} 🕖 {time_text} — {title}")
            else:
                lines.append(f"{index}. {icon} {title}")
        lines.extend(["", footers.get(lang, footers["uz"])])
        return "\n".join(lines)
    except Exception as e:
        logging.exception("format_plan_confirmation error: %s", e)
        return "📋"

async def send_plan_confirmation_message(message, tasks: list, lang: str = "uz", target_date: str = None) -> None:
    try:
        confirmation_text = await format_plan_confirmation(tasks, lang, target_date)
        confirm_labels = {
            "uz": ("✅ To'g'ri", "✏️ O'zgartirish"),
            "ru": ("✅ Верно", "✏️ Изменить"),
            "en": ("✅ Correct", "✏️ Edit"),
        }
        yes_label, no_label = confirm_labels.get(lang, confirm_labels["uz"])
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(yes_label, callback_data="plan_confirm_yes")],
            [InlineKeyboardButton(no_label, callback_data="plan_confirm_no")],
        ])
        await message.reply_text(confirmation_text, reply_markup=keyboard)
    except Exception as e:
        logging.exception("send_plan_confirmation_message error: %s", e)
        raise

async def send_reminder_choice_message(message, lang: str = "uz") -> None:
    try:
        no_reminder = {"uz": "🔕 Kerak emas", "ru": "🔕 Не нужно", "en": "🔕 No reminder"}
        prompts = {
            "uz": "⏰ Eslatma qachon yuborilsin?",
            "ru": "⏰ Когда отправить напоминание?",
            "en": "⏰ When should I send a reminder?"
        }
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("10 min", callback_data="reminder_plan_10"),
                InlineKeyboardButton("30 min", callback_data="reminder_plan_30"),
            ],
            [
                InlineKeyboardButton("60 min", callback_data="reminder_plan_60"),
                InlineKeyboardButton(no_reminder.get(lang, no_reminder["uz"]), callback_data="reminder_plan_0"),
            ],
        ])
        await message.reply_text(prompts.get(lang, prompts["uz"]), reply_markup=keyboard)
    except Exception as e:
        logging.exception("send_reminder_choice_message error: %s", e)
        raise

async def save_pending_tasks(telegram_id: int, pending_tasks: list, reminder_offset: int = 10) -> list:
    try:
        db = get_db()
        saved_titles = []
        for task in pending_tasks:
            title = (task.get("title") or "").strip()
            if not title:
                continue
            priority = task.get("priority") or "normal"
            time_str = task.get("time")
            is_recurring = bool(task.get("is_recurring", False))

            if time_str:
                try:
                    time_obj = datetime.strptime(time_str, "%H:%M").time()
                    scheduled_dt = datetime.combine(date.today(), time_obj)
                    task_id = await create_scheduled_task(telegram_id, title, scheduled_dt, reminder_offset=reminder_offset)
                    await db.tasks.update_one(
                        {"_id": ObjectId(task_id)},
                        {"$set": {"priority": priority, "is_recurring": is_recurring}},
                    )
                    if reminder_offset <= 0:
                        await db.tasks.update_one(
                            {"_id": ObjectId(task_id)},
                            {"$set": {"reminder_sent": True, "reminder_offset": 0}},
                        )
                    saved_titles.append((time_str, title))
                    continue
                except Exception as e:
                    logging.exception("save_pending_tasks scheduled task error: %s", e)

            task_id = await create_task(telegram_id, title, priority=priority)
            await db.tasks.update_one(
                {"_id": ObjectId(task_id)},
                {"$set": {"is_recurring": is_recurring}},
            )
            saved_titles.append((None, title))

        return saved_titles
    except Exception as e:
        logging.exception("save_pending_tasks error: %s", e)
        return []

async def send_saved_tasks_message(message, saved_titles: list, lang: str = "uz") -> None:
    try:
        if not saved_titles:
            no_save = {"uz": "Rejalar saqlanmadi.", "ru": "Планы не сохранены.", "en": "Plans were not saved."}
            await message.reply_text(no_save.get(lang, no_save["uz"]))
            return
        lines = [f"• {time_text} — {title}" if time_text else f"• {title}" for time_text, title in saved_titles]
        headers = {
            "uz": "✅ Quyidagi rejalar qo'shildi:\n",
            "ru": "✅ Следующие планы добавлены:\n",
            "en": "✅ The following plans were added:\n"
        }
        confirm = headers.get(lang, headers["uz"]) + "\n".join(lines)
        await message.reply_text(confirm)
        btn_labels = {"uz": "📱 Mini appda ko'rish", "ru": "📱 Открыть в Mini App", "en": "📱 View in Mini App"}
        mini_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                btn_labels.get(lang, btn_labels["uz"]),
                web_app=WebAppInfo(url=MINI_APP_URL)
            )
        ]])
        await message.reply_text(btn_labels.get(lang, btn_labels["uz"]), reply_markup=mini_keyboard)
    except Exception as e:
        logging.exception("send_saved_tasks_message error: %s", e)
        raise

async def finalize_pending_plan(message, telegram_id: int, pending_tasks: list, reminder_offset: int, lang: str = "uz") -> None:
    try:
        saved_titles = await save_pending_tasks(telegram_id, pending_tasks, reminder_offset=reminder_offset)
        await clear_state(telegram_id)
        await send_saved_tasks_message(message, saved_titles, lang)
    except Exception as e:
        logging.exception("finalize_pending_plan error: %s", e)
        raise

async def build_plan_reminder_keyboard(lang: str = "uz") -> InlineKeyboardMarkup:
    try:
        min_labels = {"uz": "daq", "ru": "мин", "en": "min"}
        hour_labels = {"uz": "1 soat", "ru": "1 час", "en": "1 hour"}
        no_labels = {"uz": "🔕 Kerak emas", "ru": "🔕 Не нужно", "en": "🔕 Not needed"}
        m = min_labels.get(lang, "min")
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"10 {m}", callback_data="reminder_10"),
                InlineKeyboardButton(f"30 {m}", callback_data="reminder_30"),
                InlineKeyboardButton(hour_labels.get(lang, hour_labels["uz"]), callback_data="reminder_60"),
            ],
            [InlineKeyboardButton(no_labels.get(lang, no_labels["uz"]), callback_data="reminder_0")]
        ])
    except Exception as e:
        logging.exception("build_plan_reminder_keyboard error: %s", e)
        return InlineKeyboardMarkup([])

async def get_plan_reminder_prompt(lang: str) -> str:
    try:
        msgs = {
            "uz": "Har bir vazifadan qancha vaqt oldin eslatay?",
            "ru": "За сколько времени до каждой задачи напомнить?",
            "en": "How much time before each task should I remind you?",
        }
        return msgs.get(lang, msgs["uz"])
    except Exception as e:
        logging.exception("get_plan_reminder_prompt error: %s", e)
        return "Har bir vazifadan qancha vaqt oldin eslatay?"

async def save_confirmed_plan_tasks(telegram_id: int, tasks: list, target_date: Optional[str] = None) -> list:
    try:
        db = get_db()
        saved_task_ids = []
        for task in tasks:
            title = (task.get("title") or "").strip()
            if not title:
                continue
            priority = task.get("priority", "normal")
            time_str = task.get("time")
            is_recurring = bool(task.get("is_recurring", False))
            task_id = await create_task(telegram_id, title, priority, time_str, is_recurring, target_date)
            await db.tasks.update_one(
                {"_id": ObjectId(task_id)},
                {"$set": {
                    "telegram_id": telegram_id,
                    "status": "pending",
                    "reminder_offset": 0,
                    "reminder_sent": True,
                    "arrival_sent": False,
                }},
            )
            if is_recurring:
                await db.tasks.update_one(
                    {"telegram_id": telegram_id, "title": title, "is_recurring": True},
                    {"$set": {"reminder_offset": 0, "recur_time": time_str}},
                )
            saved_task_ids.append(task_id)
        return saved_task_ids
    except Exception as e:
        logging.exception("save_confirmed_plan_tasks error: %s", e)
        return []

async def apply_plan_reminder_choice(telegram_id: int, offset: int, state_doc: dict) -> dict:
    try:
        db = get_db()
        tasks = state_doc.get("pending_tasks", [])
        pending_task_ids = state_doc.get("pending_task_ids", [])

        update_fields = {"reminder_offset": offset}
        if offset == 0:
            update_fields["reminder_sent"] = True
        else:
            update_fields["reminder_sent"] = False

        for i, task_id in enumerate(pending_task_ids):
            task_title = tasks[i].get("title", "") if i < len(tasks) else ""
            query = {"_id": ObjectId(task_id)}
            await db.tasks.update_one(query, {"$set": update_fields})
            
            if i < len(tasks) and tasks[i].get("is_recurring"):
                await db.tasks.update_one(
                    {"telegram_id": telegram_id, "title": task_title, "is_recurring": True},
                    {"$set": {"reminder_offset": offset}},
                )

        await clear_state(telegram_id)
        return {"done": True}
    except Exception as e:
        logging.exception("apply_plan_reminder_choice error: %s", e)
        return {"done": True}




async def _handle_task_reminder_preference(query, data: str) -> None:
    try:
        if not query:
            return
        parts = data.split("_")
        if len(parts) < 3:
            return

        task_id = parts[1]
        offset_str = parts[2]
        reminder_offset = int(offset_str)
        await update_task_reminder_offset(task_id, reminder_offset)

        offset_map = {
            10: "10 daqiqa oldin",
            30: "30 daqiqa oldin",
            60: "1 soat oldin"
        }
        offset_text = offset_map.get(reminder_offset, f"{reminder_offset} daqiqa oldin")
        await query.edit_message_text(f"вњ… Eslatma: {offset_text}")
    except Exception as e:
        logging.exception("_handle_task_reminder_preference error: %s", e)
        raise


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
        now = datetime.utcnow()
        trial_end = db_user.get("trial_end")
        is_paid = db_user.get("is_paid", False)
        paid_until = db_user.get("paid_until")
        
        admin_id_str = os.getenv("ADMIN_ID")
        is_admin = (admin_id_str and str(tg_id) == admin_id_str)
        if not is_admin and not (trial_end and now < trial_end) and not (is_paid and paid_until and now < paid_until):
            await update.message.reply_text(messages.get(lang, messages["en"])["trial_expired"])
            return
        text = update.message.text or ""
        if not text.startswith("/add "):
            return
        title = text[5:].strip()
        if not title:
            await update.message.reply_text(messages.get(lang, messages["en"])["provide_title"])
            return
        await create_task(tg_id, title)
        await update.message.reply_text(messages.get(lang, messages["en"])["task_added"])
    except Exception as e:
        logging.exception("add_command error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass

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
        now = datetime.utcnow()
        trial_end = db_user.get("trial_end")
        is_paid = db_user.get("is_paid", False)
        paid_until = db_user.get("paid_until")
        
        admin_id_str = os.getenv("ADMIN_ID")
        is_admin = (admin_id_str and str(tg_id) == admin_id_str)
        if not is_admin and not (trial_end and now < trial_end) and not (is_paid and paid_until and now < paid_until):
            await update.message.reply_text(messages.get(lang, messages["en"])["trial_expired"])
            return
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
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass

async def plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user:
            return
        tg_id = user.id
        db_user = await get_user_by_telegram_id(tg_id)
        if not db_user:
            return
        lang = db_user.get("language", "uz")
        
        from bot.handlers.admin import is_admin as check_is_admin
        if await check_is_admin(update):
            await update.message.reply_text("Siz adminsiz. Reja tuzish faqat foydalanuvchilar uchun.")
            return
            
        await set_state(tg_id, "awaiting_plan_type")
        
        msgs = {
            "uz": "📅 Qaysi turdagi rejani tuzmoqchisiz?",
            "ru": "📅 Какой план вы хотите создать?",
            "en": "📅 What type of plan do you want to create?"
        }
        keyboard = [
            [InlineKeyboardButton("📅 Kunlik reja", callback_data="plan_type_daily")],
            [InlineKeyboardButton("📆 Haftalik reja", callback_data="plan_type_weekly")],
            [InlineKeyboardButton("🗓 Oylik reja", callback_data="plan_type_monthly")]
        ]
        await update.message.reply_text(msgs.get(lang, msgs["uz"]), reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logging.exception("plan_command error: %s", e)

async def plan_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        if not query or not query.from_user:
            return
        await query.answer()
        
        data = query.data or ""
        if not data.startswith("plan_type_"):
            return
            
        plan_type = data.split("_")[2]
        telegram_id = query.from_user.id
        
        user = await get_user_by_telegram_id(telegram_id) or {}
        lang = user.get("language", "uz")
        
        if plan_type == "monthly":
            await set_state(telegram_id, "awaiting_monthly_input", plan_type=plan_type)
            msgs = {
                "uz": "📝 Bu oyda qaysi kunlar muhim? (masalan: 15-may shifokor, 20-may uchrashuv)",
                "ru": "📝 Какие дни важны в этом месяце? (например: 15 мая врач, 20 мая встреча)",
                "en": "📝 Which dates are important this month? (e.g., May 15 doctor, May 20 meeting)"
            }
            await query.edit_message_text(msgs.get(lang, msgs["uz"]))
            return

        tashkent_now = datetime.now(timezone(timedelta(hours=5)))
        today_local = tashkent_now.date()
        target_dates = []
        days_to_add = 7 if plan_type == "weekly" else 1
            
        for i in range(days_to_add):
            target_dates.append((today_local + timedelta(days=i)).isoformat())
            
        await set_state(telegram_id, "awaiting_plan_day", plan_type=plan_type, target_dates=target_dates, current_day_index=0, collected_task_ids=[])
        
        first_date_str = target_dates[0]
        first_date_obj = datetime.strptime(first_date_str, "%Y-%m-%d")
        
        day_names_uz = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]
        day_names_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        day_names_en = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        
        d_uz = day_names_uz[first_date_obj.weekday()]
        d_ru = day_names_ru[first_date_obj.weekday()]
        d_en = day_names_en[first_date_obj.weekday()]
        formatted_date = first_date_obj.strftime("%d.%m.%Y")
        
        if plan_type == "daily":
            msgs = {
                "uz": f"📝 Bugun, {formatted_date} uchun rejangizni yozing (masalan soat 6:00 da uyg'onaman, v.h.)",
                "ru": f"📝 Сегодня, {formatted_date} — напишите ваши планы (например, проснусь в 6:00 и т.д.)",
                "en": f"📝 Today, {formatted_date} — write your plans (e.g., wake up at 6:00, etc.)"
            }
        else:
            msgs = {
                "uz": f"📝 {d_uz}, {formatted_date} uchun rejangizni yozing:",
                "ru": f"📝 {d_ru}, {formatted_date} — напишите ваши планы:",
                "en": f"📝 {d_en}, {formatted_date} — write your plans:"
            }
        await query.edit_message_text(msgs.get(lang, msgs["uz"]))
    except Exception as e:
        logging.exception("plan_type_callback error: %s", e)

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
        # Edit message to reflect change
        await query.edit_message_text("Task marked as done!")
    except Exception as e:
        logging.exception("done_callback error: %s", e)
        try:
            if update.callback_query and update.callback_query.message:
                await update.callback_query.message.reply_text("An error occurred.")
        except Exception:
            pass



async def handle_postpone_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        from bot.services.ai import get_ai_response
        telegram_id = update.effective_user.id
        db = get_db()
        user = await get_user_by_telegram_id(telegram_id) or {}
        lang = user.get("language", "uz")
        
        state_doc = await get_state(telegram_id)
        task_id = state_doc.get("postpone_task_id")
        
        if not task_id:
            await clear_state(telegram_id)
            return True
            
        task = await db.tasks.find_one({"_id": ObjectId(task_id)})
        if not task:
            await clear_state(telegram_id)
            return True

        user_message = update.message.text or ""
        tashkent = timezone(timedelta(hours=5))
        now_tashkent = datetime.now(tashkent)
        
        prompt = f"User wants to postpone a task. Current time: {now_tashkent.strftime('%H:%M')}. User said: '{user_message}'. Return ONLY a JSON string with 'time' in HH:MM format. Example: {{\"time\": \"15:30\"}}. If no time found, return {{\"time\": null}}."
        ai_result = await get_ai_response(prompt, "en", [])
        
        import json, re
        match = re.search(r"\{.*?\}", ai_result[0] if isinstance(ai_result, tuple) else ai_result, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                new_time = parsed.get("time")
                if new_time:
                    time_obj = datetime.strptime(new_time, "%H:%M").time()
                    scheduled_dt = datetime.combine(now_tashkent.date(), time_obj).replace(tzinfo=tashkent)
                    scheduled_utc = scheduled_dt.astimezone(timezone.utc).replace(tzinfo=None)
                    
                    if scheduled_utc <= datetime.utcnow():
                        scheduled_dt += timedelta(days=1)
                        scheduled_utc = scheduled_dt.astimezone(timezone.utc).replace(tzinfo=None)
                    
                    await db.tasks.update_one(
                        {"_id": ObjectId(task_id)},
                        {"$set": {
                            "time": scheduled_dt.strftime("%H:%M"),
                            "scheduled_time": scheduled_utc,
                            "reminder_sent": False if int(task.get("reminder_offset", 10) or 10) > 0 else True,
                            "arrival_sent": False,
                            "status": "pending",
                            "is_done": False
                        }}
                    )
                    await clear_state(telegram_id)
                    conf = {
                        "uz": f"✅ \"{task['title']}\" {scheduled_dt.strftime('%H:%M')} ga surildi.",
                        "ru": f"✅ \"{task['title']}\" перенесено на {scheduled_dt.strftime('%H:%M')}.",
                        "en": f"✅ \"{task['title']}\" postponed to {scheduled_dt.strftime('%H:%M')}."
                    }
                    await update.message.reply_text(conf.get(lang, conf["uz"]))
                    return True
            except Exception:
                pass
                
        return False
    except Exception as e:
        logging.exception("handle_postpone_input error: %s", e)
        return True

async def ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user or not update.message:
            return
        tg_id = user.id
        user_message = update.message.text or ""

        db_user = await get_user_by_telegram_id(tg_id)
        if not db_user:
            return

        lang = db_user.get("language", "en")
        now = datetime.utcnow()
        trial_end = db_user.get("trial_end")
        is_paid = db_user.get("is_paid", False)
        paid_until = db_user.get("paid_until")

        from bot.handlers.admin import is_admin as check_is_admin
        is_admin_user = await check_is_admin(update)

        if not is_admin_user and not (trial_end and now < trial_end) and not (is_paid and paid_until and now < paid_until):
            await update.message.reply_text(messages.get(lang, messages["en"])["trial_expired"])
            return

        state_doc = await get_state(tg_id)
        current_state = state_doc.get("state", "idle")
        
        user_message_to_ai = user_message
        
        if is_admin_user:
            if current_state == "admin_awaiting_broadcast":
                from bot.services.db import get_db
                import asyncio
                
                await update.message.reply_text("⏳ Xabarni yuborish boshlandi. Iltimos kuting...")
                await clear_state(tg_id)
                
                async def broadcast_task(text):
                    db_local = get_db()
                    users_cur = await db_local.users.find({}).to_list(length=15000)
                    count = 0
                    for u in users_cur:
                        try:
                            await context.bot.send_message(chat_id=u["telegram_id"], text=text)
                            count += 1
                            await asyncio.sleep(0.05)  # Max 20 messages per second
                        except Exception:
                            pass
                    try:
                        await context.bot.send_message(chat_id=tg_id, text=f"✅ Xabar hamma {count} ta odamga yetib bordi!")
                    except Exception:
                        pass
                
                asyncio.create_task(broadcast_task(user_message))
                return
                
            elif current_state == "admin_awaiting_price":
                try:
                    amount = int(user_message.strip())
                    from bot.services.db import get_db
                    await get_db().settings.update_one(
                        {"key": "subscription_price"},
                        {"$set": {"key": "subscription_price", "value": amount}},
                        upsert=True
                    )
                    await clear_state(tg_id)
                    await update.message.reply_text(f"✅ Narx yangilandi: {amount:,} so'm")
                except ValueError:
                    await update.message.reply_text("❌ Noto'g'ri raqam. Iltimos, faqat raqam kiriting (masalan: 20000).")
                return
                
            elif current_state == "admin_awaiting_promo_code":
                code = user_message.strip().upper()
                await set_state(tg_id, "admin_awaiting_promo_discount", promo_code=code)
                await update.message.reply_text("💯 Yaxshi! Endi chegirma foizini kiriting (faqat raqam, masalan: 20):")
                return
                
            elif current_state == "admin_awaiting_promo_discount":
                try:
                    discount = int(user_message.strip())
                    code = state_doc.get("promo_code")
                    await set_state(tg_id, "admin_awaiting_promo_uses", promo_code=code, promo_discount=discount)
                    await update.message.reply_text("👥 Ajoyib! Bu promokoddan maksimal necha marta foydalanish mumkin? (masalan: 100):")
                except ValueError:
                    await update.message.reply_text("❌ Noto'g'ri raqam. Iltimos, faqat raqam kiriting (masalan: 20).")
                return
                
            elif current_state == "admin_awaiting_promo_uses":
                try:
                    uses = int(user_message.strip())
                    code = state_doc.get("promo_code")
                    discount = state_doc.get("promo_discount")
                    await set_state(tg_id, "admin_awaiting_promo_days", promo_code=code, promo_discount=discount, promo_uses=uses)
                    await update.message.reply_text("⏳ Va oxirgisi: Bu promokod necha kun amal qiladi? (masalan: 30):")
                except ValueError:
                    await update.message.reply_text("❌ Noto'g'ri raqam. Iltimos, faqat raqam kiriting.")
                return
                
            elif current_state == "admin_awaiting_promo_days":
                try:
                    days = int(user_message.strip())
                    code = state_doc.get("promo_code")
                    discount = state_doc.get("promo_discount")
                    uses = state_doc.get("promo_uses")
                    
                    from datetime import timedelta
                    valid_until = datetime.utcnow() + timedelta(days=days)
                    
                    from bot.services.db import get_db
                    await get_db().promos.update_one(
                        {"code": code},
                        {"$set": {
                            "code": code,
                            "discount_percent": discount,
                            "max_uses": uses,
                            "used_count": 0,
                            "valid_until": valid_until,
                            "created_at": datetime.utcnow()
                        }},
                        upsert=True
                    )
                    await clear_state(tg_id)
                    await update.message.reply_text(f"✅ Promokod muvaffaqiyatli yaratildi!\n\n🎟 Kod: {code}\n📉 Chegirma: {discount}%\n👥 Limit: {uses} ta\n⏳ Amal qilish muddati: {days} kun")
                except ValueError:
                    await update.message.reply_text("❌ Noto'g'ri raqam. Iltimos, faqat raqam kiriting.")
                return

            # If not awaiting admin input, clear state and proceed to standard AI admin reply
            current_state = "idle"
            await clear_state(tg_id)
            user_message_to_ai = f"<system>SYSTEM INFO: Siz tizim adminiga xizmat ko'rsatyapsiz! Unga reja qilishni aytmang. Faqat uning so'rovlariga botning holati bo'yicha to'g'ri, qisqa va aniq javob bering.</system>\n\nADMIN MESSAGE: {user_message}"
        
        if current_state == "awaiting_confirmation":
            if await handle_confirmation_text(update, context):
                return
            user_message_to_ai = f"<system>SYSTEM INFO: User was asked to confirm the pending plan ('ha' or 'yoq'). They replied with: '{user_message}'. Answer naturally.</system>\n\nUSER MESSAGE: {user_message}"
        elif current_state == "awaiting_reminder":
            if await handle_reminder_input(update, context):
                return
            user_message_to_ai = f"<system>SYSTEM INFO: User was asked to pick a reminder offset (0, 10, 30, 60 minutes). They said '{user_message}'. Guide them gently or respond intelligently.</system>\n\nUSER MESSAGE: {user_message}"
        elif current_state == "awaiting_postpone_time":
            if await handle_postpone_input(update, context):
                return
            postpone_task_id = state_doc.get("postpone_task_id")
            user_message_to_ai = f"<system>SYSTEM INFO: User wants to postpone task {postpone_task_id}. Provide a natural response or ask for an exact time naturally.</system>\n\nUSER MESSAGE: {user_message}"
        elif current_state == "evening_checkin_1":
            await handle_evening_response_1(update, context, db_user, user_message)
            return
        elif current_state == "evening_checkin_2":
            await handle_evening_response_2(update, context, db_user, user_message)
            return
        elif current_state == "custdev_answering":
            await handle_custdev_answer(update, context, db_user, user_message)
            return
        elif current_state == "awaiting_plan_edit":
            pending_tasks = state_doc.get("pending_tasks", [])
            tasks_str = json.dumps(pending_tasks, ensure_ascii=False)
            user_message_to_ai = f"<system>SYSTEM INFO: The user is currently editing the following pending plan. You MUST output action 'propose_tasks' JSON block with the updated list of tasks.\nCURRENT TASKS: {tasks_str}</system>\n\nUSER EDITS: {user_message}"

        # Inject today's tasks so AI knows full context seamlessly
        from bot.services.db import get_db
        db_conn = get_db()
        tashkent_now = datetime.now(timezone(timedelta(hours=5)))
        today_date_str = tashkent_now.date().isoformat()
        today_tasks_cur = db_conn.tasks.find({"telegram_id": tg_id, "date": today_date_str, "status": {"$ne": "deleted"}})
        t_tasks = []
        async for t in today_tasks_cur:
            t.pop("_id", None)
            t.pop("user_id", None)
            if "scheduled_time" in t: t["scheduled_time"] = str(t["scheduled_time"])
            if "created_at" in t: t.pop("created_at", None)
            t_tasks.append(t)
        db_user["today_tasks"] = t_tasks
        db_user["current_state"] = current_state

        # Inject future planned tasks so AI sees full schedule
        future_tasks_cur = db_conn.tasks.find({
            "telegram_id": tg_id,
            "date": {"$gt": today_date_str},
            "status": {"$ne": "deleted"},
            "is_recurring": {"$ne": True}
        }).sort("date", 1).limit(50)
        f_tasks = []
        async for ft in future_tasks_cur:
            ft.pop("_id", None)
            ft.pop("user_id", None)
            if "scheduled_time" in ft: ft["scheduled_time"] = str(ft["scheduled_time"])
            if "created_at" in ft: ft.pop("created_at", None)
            f_tasks.append(ft)
        db_user["future_tasks"] = f_tasks
        
        # Inject current plan type
        if state_doc.get("plan_type"):
            db_user["active_plan_type"] = state_doc.get("plan_type")

        history = db_user.get("chat_history", [])

        extracted_tasks = []

        if current_state == "awaiting_monthly_input":
            from bot.services.ai import extract_monthly_dates_and_tasks
            monthly_dict = await extract_monthly_dates_and_tasks(user_message, lang)
            if monthly_dict:
                sorted_dates = sorted(monthly_dict.keys())
                first_date = sorted_dates[0]
                first_tasks = monthly_dict[first_date]
                await set_state(
                    tg_id, "awaiting_confirmation",
                    plan_type="monthly", target_dates=sorted_dates, current_day_index=0,
                    collected_task_ids=[], monthly_extracted_dict=monthly_dict,
                    target_date=first_date, pending_tasks=first_tasks
                )
                await send_plan_confirmation_message(update.message, first_tasks, lang, first_date)
                return
            else:
                no_data = {"uz": "Kechirasiz, sanalar topilmadi. Iltimos, qaytadan kiriting.", "ru": "Извините, даты не найдены. Попробуйте ещё раз.", "en": "Sorry, no dates found. Please try again."}
                await update.message.reply_text(no_data.get(lang, no_data["uz"]))
                return

        user_profile = {
            "username": db_user.get("username") or user.username or "User",
            "interaction_count": db_user.get("interaction_count", 0),
            "personality": db_user.get("personality", {}),
            "topics_discussed": db_user.get("topics_discussed", []),
            "communication_style": db_user.get("communication_style", "casual"),
            "habits": db_user.get("habits", []),
            "today_tasks": db_user.get("today_tasks", []),
            "future_tasks": db_user.get("future_tasks", []),
            "active_plan_type": db_user.get("active_plan_type"),
            "current_state": current_state,
            "building_date": state_doc.get("target_date"),
            "is_admin": is_admin_user,
        }
        ai_result = await get_ai_response(user_message_to_ai, lang, history, user_profile=user_profile)
        if isinstance(ai_result, tuple):
            ai_response, profile_updates = ai_result
        else:
            ai_response = ai_result
            profile_updates = {
                "last_active": datetime.utcnow(),
                "interaction_count": int(db_user.get("interaction_count", 0) or 0) + 1,
            }

        # Analyze personality every 5 messages
        from bot.services.ai import analyze_user_personality
        from bot.services.db import get_db
        profile_update = await analyze_user_personality(
            tg_id, user_message, lang,
            {"personality": db_user.get("personality",{}), 
             "habits": db_user.get("habits",[]),
             "interaction_count": db_user.get("interaction_count",0)}
        )

        from bot.models.user import calculate_segment
        new_count = db_user.get("interaction_count", 0) + 1
        new_segment = calculate_segment(new_count)

        if profile_update:
            update_data = {
                "interaction_count": new_count,
                "last_active": datetime.utcnow(),
                "segment": new_segment
            }
            if "communication_style" in profile_update:
                update_data["communication_style"] = profile_update["communication_style"]
            if "detected_habits" in profile_update and profile_update["detected_habits"]:
                existing_habits = db_user.get("habits", [])
                new_habits = list(set(existing_habits + profile_update["detected_habits"]))
                update_data["habits"] = new_habits[:20]  # max 20 habits
            if "personality_traits" in profile_update:
                update_data["personality"] = profile_update
            
            db = get_db()
            await db.users.update_one(
                {"telegram_id": tg_id},
                {"$set": update_data}
            )
        else:
            # Always increment count and update segment
            db = get_db()
            await db.users.update_one(
                {"telegram_id": tg_id},
                {"$inc": {"interaction_count": 1},
                 "$set": {"last_active": datetime.utcnow(), "segment": new_segment}}
            )

        await update_user_profile_after_message(
            telegram_id=tg_id,
            username=user.username or db_user.get("username") or "User",
            db_user=db_user,
            language=lang,
            user_message=user_message,
            extracted_tasks=extracted_tasks,
            profile_updates=profile_updates,
            ai_response=ai_response,
        )

        from bot.api.routes import detect_and_execute_action
        from bot.services.db import get_db
        action_result = await detect_and_execute_action(user_message, ai_response, str(tg_id), get_db(), history[-6:])

        import re
        clean_ai_response = re.sub(r"```json\s*\{.*?\}\s*```", "", ai_response, flags=re.DOTALL).strip()

        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": clean_ai_response})
        new_history = history[-10:]
        
        # Intercept propose_tasks BEFORE sending normal response
        if not is_admin_user and action_result and action_result.get("action") == "propose_tasks":
            extracted_tasks = action_result.get("data") or []
            if extracted_tasks:
                await set_state(tg_id, "awaiting_confirmation", pending_tasks=extracted_tasks, current_task_index=0)
                await get_db().users.update_one({"telegram_id": tg_id}, {"$set": {"chat_history": new_history}})
                if clean_ai_response:
                    await update.message.reply_text(clean_ai_response)
                await send_plan_confirmation_message(update.message, extracted_tasks, lang)
                return

        # Intercept unknown_intent BEFORE sending normal response
        if not is_admin_user and action_result and action_result.get("action") == "unknown_intent":
            await get_db().users.update_one({"telegram_id": tg_id}, {"$set": {"chat_history": new_history}})
            fallback_text = {
                "uz": "Quyidagilardan birini tanlang:\n/plan — Reja tuzish\n/app — Mini ilova\n/web — Veb sayt\n/free — Erkin suhbat\n/language — Tilni o'zgartirish",
                "ru": "Выберите одно из следующих:\n/plan — Составить план\n/app — Мини приложение\n/web — Веб сайт\n/free — Свободный чат\n/language — Изменить язык",
                "en": "Choose one of the following:\n/plan — Create a plan\n/app — Mini app\n/web — Web dashboard\n/free — Free chat\n/language — Change language"
            }
            await update.message.reply_text(fallback_text.get(lang, fallback_text["uz"]))
            return

        await get_db().users.update_one({"telegram_id": tg_id}, {"$set": {"chat_history": new_history}})

        # Add reminder message every 4th interaction if idle
        if current_state == "idle" and new_count > 0 and new_count % 4 == 0:
            reminder_text = {
                "uz": "\n\n📌 Kuningizni rejalashtirish uchun /plan ni bosing!",
                "ru": "\n\n📌 Нажмите /plan, чтобы запланировать день!",
                "en": "\n\n📌 Press /plan to schedule your day!"
            }
            clean_ai_response += reminder_text.get(lang, reminder_text["uz"])

        await update.message.reply_text(clean_ai_response)
    except Exception as e:
        logging.exception("ai_chat error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass

async def reminder_preference_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await reminder_choice_callback(update, context)
    except Exception as e:
        logging.exception("reminder_preference_callback error: %s", e)
        try:
            if update.callback_query and update.callback_query.message:
                await update.callback_query.message.reply_text("An error occurred.")
        except Exception:
            pass

async def handle_confirmation_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        if not update.message or not update.effective_user:
            return True
        telegram_id = update.effective_user.id
        user = await get_user_by_telegram_id(telegram_id) or {}
        lang = user.get("language", "uz")
        state_doc = await get_state(telegram_id)
        tasks = state_doc.get("pending_tasks", [])
        if not tasks:
            await clear_state(telegram_id)
            not_found = {"uz": "Rejangiz topilmadi. Iltimos, qaytadan yuboring.", "ru": "План не найден. Отправьте снова.", "en": "Plan not found. Please send again."}
            await update.message.reply_text(not_found.get(lang, not_found["uz"]))
            return True

        text = (update.message.text or "").strip().lower()
        yes_words = {"ha", "xa", "yes", "да", "to'g'ri", "togri", "ok", "okay", "yaxshi"}
        no_words = {"yo'q", "yoq", "no", "нет", "o'zgartirish", "ozgartirish", "edit"}

        if text in yes_words:
            target_date = state_doc.get("target_date")
            saved_task_ids = await save_confirmed_plan_tasks(telegram_id, tasks, target_date)
            if not saved_task_ids:
                await clear_state(telegram_id)
                fail_msg = {"uz": "Rejalarni saqlab bo'lmadi.", "ru": "Не удалось сохранить.", "en": "Failed to save."}
                await update.message.reply_text(fail_msg.get(lang, fail_msg["uz"]))
                return True
                
            collected = state_doc.get("collected_task_ids", [])
            collected.extend(saved_task_ids)
            target_dates = state_doc.get("target_dates", [])
            idx = state_doc.get("current_day_index", 0) + 1
            
            if len(target_dates) > 0 and idx < len(target_dates):
                next_date_str = target_dates[idx]
                
                if state_doc.get("plan_type") == "monthly":
                    monthly_extracted = state_doc.get("monthly_extracted_dict", {})
                    pending_tasks = monthly_extracted.get(next_date_str, [])
                    await set_state(
                        telegram_id, "awaiting_confirmation",
                        plan_type="monthly", target_dates=target_dates, current_day_index=idx,
                        collected_task_ids=collected, monthly_extracted_dict=monthly_extracted,
                        target_date=next_date_str, pending_tasks=pending_tasks
                    )
                    await send_plan_confirmation_message(update.message, pending_tasks, lang, next_date_str)
                else:
                    next_date_obj = datetime.strptime(next_date_str, "%Y-%m-%d")
                    day_names_uz = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]
                    day_names_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
                    day_names_en = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                    
                    d_uz = day_names_uz[next_date_obj.weekday()]
                    d_ru = day_names_ru[next_date_obj.weekday()]
                    d_en = day_names_en[next_date_obj.weekday()]
                    formatted_date = next_date_obj.strftime("%d.%m.%Y")
                    
                    msgs = {
                        "uz": f"📝 Keyingi kun: {d_uz}, {formatted_date} uchun rejangiz?",
                        "ru": f"📝 Следующий день: {d_ru}, {formatted_date} — ваши планы?",
                        "en": f"📝 Next day: {d_en}, {formatted_date} — your plan?"
                    }
                    await set_state(telegram_id, "awaiting_plan_day", plan_type=state_doc.get("plan_type"), target_dates=target_dates, current_day_index=idx, collected_task_ids=collected)
                    await update.message.reply_text(msgs.get(lang, msgs["uz"]))
            else:
                await set_state(
                    telegram_id,
                    "awaiting_reminder",
                    pending_tasks=tasks,
                    pending_task_ids=collected,
                )
                await update.message.reply_text(
                    await get_plan_reminder_prompt(lang),
                    reply_markup=await build_plan_reminder_keyboard(lang),
                )
            return True

        if text in no_words:
            await set_state(telegram_id, "awaiting_plan_edit", pending_tasks=tasks)
            edit_msgs = {
                "uz": "Nimasini o'zgartiramiz?",
                "ru": "Что нужно изменить?",
                "en": "What needs to be changed?"
            }
            await update.message.reply_text(edit_msgs.get(lang, edit_msgs["uz"]))
            return True

        return False
    except Exception as e:
        logging.exception("handle_confirmation_text error: %s", e)
        return True

async def handle_reminder_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        if not update.message or not update.effective_user:
            return True
        telegram_id = update.effective_user.id
        user = await get_user_by_telegram_id(telegram_id) or {}
        lang = user.get("language", "uz")
        state_doc = await get_state(telegram_id)
        tasks = state_doc.get("pending_tasks", [])
        if not tasks:
            await clear_state(telegram_id)
            not_found = {"uz": "Rejangiz topilmadi.", "ru": "План не найден.", "en": "Plan not found."}
            await update.message.reply_text(not_found.get(lang, not_found["uz"]))
            return True

        text = (update.message.text or "").strip().lower()

        # Word-based number parsing (uz/ru/en)
        word_numbers = {
            "bir": 1, "ikki": 2, "uch": 3, "to'rt": 4, "tort": 4, "besh": 5,
            "olti": 6, "yetti": 7, "sakkiz": 8, "to'qqiz": 9, "toqqiz": 9, "o'n": 10, "on": 10,
            "один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5,
            "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "fifteen": 15, "twenty": 20, "thirty": 30,
            "o'n besh": 15, "yigirma": 20, "o'ttiz": 30, "ottiz": 30,
            "пятнадцать": 15, "двадцать": 20, "тридцать": 30,
        }

        offset = None

        # First try word-based numbers
        for word, num in sorted(word_numbers.items(), key=lambda x: -len(x[0])):
            if word in text:
                offset = num
                break

        # Then try numeric extraction
        if offset is None:
            match = re.search(r"\d+", text)
            if match:
                offset = int(match.group())

        if offset is None:
            return False

        # Clamp to valid range (0 = no reminder, 1-1440 = valid minutes)
        if offset < 0:
            offset = 0
        elif offset > 1440:
            offset = 1440

        result = await apply_plan_reminder_choice(telegram_id, offset, state_doc)

        if offset == 0:
            confirm = {
                "uz": "✅ Tayyor! Vazifa vaqtida eslataman 🔔",
                "ru": "✅ Готово! Напомню в назначенное время 🔔",
                "en": "✅ All set! I'll remind you at the scheduled time 🔔"
            }
        else:
            confirm = {
                "uz": f"✅ Tayyor! Har bir vazifadan {offset} daqiqa oldin eslataman 🔔",
                "ru": f"✅ Готово! Напомню за {offset} минут до каждой задачи 🔔",
                "en": f"✅ All set! I'll remind you {offset} minutes before each task 🔔"
            }

        btn = {"uz": "📱 Rejalarni ko'rish", "ru": "📱 Посмотреть планы", "en": "📱 View plans"}
        keyboard = [[InlineKeyboardButton(btn.get(lang, btn["uz"]), web_app=WebAppInfo(url=MINI_APP_URL))]]
        await update.message.reply_text(confirm.get(lang, confirm["uz"]), reply_markup=InlineKeyboardMarkup(keyboard))
        return True
    except Exception as e:
        logging.exception("handle_reminder_input error: %s", e)
        return True


async def confirm_plan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        if not query or not query.from_user:
            return
        await query.answer()
        telegram_id = query.from_user.id
        state_doc = await get_state(telegram_id)
        user = await get_user_by_telegram_id(telegram_id) or {}
        lang = user.get("language", "uz")
        not_found = {"uz": "Rejangiz topilmadi. Iltimos, qaytadan yuboring.", "ru": "План не найден. Пожалуйста, отправьте снова.", "en": "Plan not found. Please send again."}
        tasks = state_doc.get("pending_tasks", [])
        if not tasks:
            await clear_state(telegram_id)
            await query.edit_message_text(not_found.get(lang, not_found["uz"]))
            return

        if query.data == "plan_confirm_yes":
            timed_tasks = [t for t in tasks if t.get("time")]
            target_date = state_doc.get("target_date")
            saved_task_ids = await save_confirmed_plan_tasks(telegram_id, tasks, target_date)
            if not saved_task_ids:
                await clear_state(telegram_id)
                fail_msgs = {"uz": "Rejalarni saqlab bo'lmadi.", "ru": "Не удалось сохранить планы.", "en": "Failed to save plans."}
                await query.edit_message_text(fail_msgs.get(lang, fail_msgs["uz"]))
                return

            collected = state_doc.get("collected_task_ids", [])
            collected.extend(saved_task_ids)
            target_dates = state_doc.get("target_dates", [])
            idx = state_doc.get("current_day_index", 0) + 1
            
            if len(target_dates) > 0 and idx < len(target_dates):
                next_date_str = target_dates[idx]
                
                if state_doc.get("plan_type") == "monthly":
                    monthly_extracted = state_doc.get("monthly_extracted_dict", {})
                    pending_tasks = monthly_extracted.get(next_date_str, [])
                    await set_state(
                        telegram_id, "awaiting_confirmation",
                        plan_type="monthly", target_dates=target_dates, current_day_index=idx,
                        collected_task_ids=collected, monthly_extracted_dict=monthly_extracted,
                        target_date=next_date_str, pending_tasks=pending_tasks
                    )
                    confirmation_text = await format_plan_confirmation(pending_tasks, lang, next_date_str)
                    confirm_labels = {"uz": ("✅ To'g'ri", "✏️ O'zgartirish"), "ru": ("✅ Верно", "✏️ Изменить"), "en": ("✅ Correct", "✏️ Edit")}
                    yes_l, no_l = confirm_labels.get(lang, confirm_labels["uz"])
                    k_b = InlineKeyboardMarkup([[InlineKeyboardButton(yes_l, callback_data="plan_confirm_yes")], [InlineKeyboardButton(no_l, callback_data="plan_confirm_no")]])
                    await query.edit_message_text(confirmation_text, reply_markup=k_b)
                else:
                    next_date_obj = datetime.strptime(next_date_str, "%Y-%m-%d")
                    day_names_uz = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]
                    day_names_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
                    day_names_en = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                    
                    d_uz = day_names_uz[next_date_obj.weekday()]
                    d_ru = day_names_ru[next_date_obj.weekday()]
                    d_en = day_names_en[next_date_obj.weekday()]
                    formatted_date = next_date_obj.strftime("%d.%m.%Y")
                    
                    msgs = {
                        "uz": f"📝 Keyingi kun: {d_uz}, {formatted_date} uchun rejangiz?",
                        "ru": f"📝 Следующий день: {d_ru}, {formatted_date} — ваши планы?",
                        "en": f"📝 Next day: {d_en}, {formatted_date} — your plan?"
                    }
                    await set_state(telegram_id, "awaiting_plan_day", plan_type=state_doc.get("plan_type"), target_dates=target_dates, current_day_index=idx, collected_task_ids=collected)
                    await query.edit_message_text(msgs.get(lang, msgs["uz"]))
            else:
                if len(timed_tasks) > 0 or len(collected) > 0:
                    await set_state(
                        telegram_id,
                        "awaiting_reminder",
                        pending_tasks=tasks,
                        pending_task_ids=collected,
                    )
                    await query.edit_message_text(
                        (await get_plan_reminder_prompt(lang)),
                        reply_markup=await build_plan_reminder_keyboard(lang),
                    )
                else:
                    confirmed = {"uz": "✅ Rejangiz tasdiqlandi.", "ru": "✅ Ваш план подтверждён.", "en": "✅ Your plan is confirmed."}
                    await query.edit_message_text(confirmed.get(lang, confirmed["uz"]))
                    await finalize_pending_plan(query.message, telegram_id, tasks, reminder_offset=0, lang=lang)
            return

        if query.data == "plan_confirm_no":
            await set_state(telegram_id, "awaiting_plan_edit", pending_tasks=tasks)
            edit_msgs = {
                "uz": "Nimasini o'zgartiramiz? (masalan: 'tushlik 13:00 da' yoki 'yugurishni o'chir')",
                "ru": "Что нужно изменить? (например: 'обед в 13:00' или 'удали бег')",
                "en": "What needs to be changed? (e.g.: 'lunch at 13:00' or 'remove running')"
            }
            await query.edit_message_text(edit_msgs.get(lang, edit_msgs["uz"]))
    except Exception as e:
        logging.exception("confirm_plan_callback error: %s", e)
        try:
            if update.callback_query and update.callback_query.message:
                await update.callback_query.message.reply_text("An error occurred.")
        except Exception:
            pass

async def reminder_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        if not query or not query.from_user:
            return
        await query.answer()
        telegram_id = query.from_user.id
        state_doc = await get_state(telegram_id)
        user = await get_user_by_telegram_id(telegram_id) or {}
        lang = user.get("language", "uz")

        try:
            parts = (query.data or "").split("_")
            if len(parts) >= 3 and not parts[1].isdigit():
                await _handle_task_reminder_preference(query, query.data or "")
                return
            offset = int(parts[1])
        except Exception:
            return

        tasks = state_doc.get("pending_tasks", [])
        if not tasks:
            await clear_state(telegram_id)
            not_found = {"uz": "Rejangiz topilmadi.", "ru": "План не найден.", "en": "Plan not found."}
            await query.edit_message_text(not_found.get(lang, not_found["uz"]))
            return

        result = await apply_plan_reminder_choice(telegram_id, offset, state_doc)

        msgs = {
            "uz": "✅ Tayyor! Vaqti kelganda eslataman 🔔",
            "ru": "✅ Готово! Напомню вовремя 🔔",
            "en": "✅ All set! I'll remind you on time 🔔"
        }
        btn = {"uz": "📱 Rejalarni ko'rish", "ru": "📱 Посмотреть планы", "en": "📱 View plans"}
        keyboard = [[InlineKeyboardButton(btn.get(lang, btn["uz"]), web_app=WebAppInfo(url=MINI_APP_URL))]]
        await query.edit_message_text(msgs.get(lang, msgs["uz"]), reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logging.exception("reminder_choice_callback error: %s", e)
        try:
            if update.callback_query and update.callback_query.message:
                await update.callback_query.message.reply_text("An error occurred.")
        except Exception:
            pass

async def task_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        if not query or not query.from_user:
            return
        await query.answer()
        data = query.data or ""
        parts = data.split("_")
        if len(parts) < 4:
            return

        status = parts[2]
        task_id = parts[3]
        telegram_id = query.from_user.id
        user = await get_user_by_telegram_id(telegram_id) or {}
        lang = user.get("language", "uz")
        status_map = {"done": True, "inprogress": False, "skipped": False, "postponed": False}
        if status not in status_map:
            return

        db = get_db()
        task = await db.tasks.find_one({"_id": ObjectId(task_id)})
        if not task:
            return

        await db.tasks.update_one(
            {"_id": ObjectId(task_id)},
            {"$set": {"is_done": status_map[status], "status": status}}
        )

        if status == "postponed":
            await set_state(telegram_id, "awaiting_postpone_time", postpone_task_id=task_id)
            prompts = {
                "uz": f"\"{task['title']}\" ni qaysi vaqtga qo'yay? (Misol: 18:30 yoki 2 soatdan keyin)",
                "ru": f"На какое время перенести \"{task['title']}\"? (Например: 18:30 или через 2 часа)",
                "en": f"What time should I postpone \"{task['title']}\" to? (Example: 18:30 or in 2 hours)"
            }
            await query.edit_message_text(prompts.get(lang, prompts["uz"]))
            return

        responses = {
            "done": {"uz": "Zo'r! 💪 Davom eting!", "ru": "Отлично! 💪", "en": "Great job! 💪"},
            "inprogress": {"uz": "Davom eting! ⚡", "ru": "Продолжайте! ⚡", "en": "Keep going! ⚡"},
            "skipped": {"uz": "Keyingi safar! 💫", "ru": "В следующий раз! 💫", "en": "Next time! 💫"}
        }
        await query.edit_message_text(responses[status].get(lang, responses[status]["en"]))
    except Exception as e:
        logging.exception("task_status_callback error: %s", e)
        try:
            if update.callback_query and update.callback_query.message:
                await update.callback_query.message.reply_text("An error occurred.")
        except Exception:
            pass



async def handle_evening_response_1(update, context, user, message):
    """Handle first evening check-in response: what did you do today"""
    try:
        telegram_id = update.effective_user.id
        lang = user.get("language", "uz")
        db = get_db()

        # Save first response and move to step 2
        await db.user_states.update_one(
            {"telegram_id": telegram_id},
            {"$set": {
                "state": "evening_checkin_2",
                "evening_response_1": message,
                "updated_at": datetime.utcnow()
            }}
        )

        # Ask follow-up question
        msgs = {
            "uz": "Yaxshi! \ud83d\udc4d\n\nRejadan tashqari yana biron qo'shimcha ish qildingizmi?",
            "ru": "\u041e\u0442\u043b\u0438\u0447\u043d\u043e! \ud83d\udc4d\n\n\u0421\u0434\u0435\u043b\u0430\u043b\u0438 \u0447\u0442\u043e-\u043d\u0438\u0431\u0443\u0434\u044c \u0434\u043e\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0435, \u043f\u043e\u043c\u0438\u043c\u043e \u043f\u043b\u0430\u043d\u0430?",
            "en": "Great! \ud83d\udc4d\n\nDid you do anything extra beyond your plan?"
        }
        await update.message.reply_text(msgs.get(lang, msgs["uz"]))
    except Exception as e:
        logging.exception("handle_evening_response_1 error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass


async def handle_evening_response_2(update, context, user, message):
    """Handle second evening check-in response: extra tasks done"""
    try:
        telegram_id = update.effective_user.id
        lang = user.get("language", "uz")
        db = get_db()

        # Get first response from state
        state_doc = await get_state(telegram_id)
        response_1 = state_doc.get("evening_response_1", "")

        # Reset state to idle
        await db.user_states.update_one(
            {"telegram_id": telegram_id},
            {"$set": {
                "state": "idle",
                "evening_response_2": message,
                "updated_at": datetime.utcnow()
            }}
        )

        # Save evening log to DB
        await db.evening_logs.insert_one({
            "telegram_id": telegram_id,
            "date": state_doc.get("evening_date", datetime.utcnow().date().isoformat()),
            "response_1": response_1,
            "response_2": message,
            "created_at": datetime.utcnow()
        })

        # Trigger report generation
        await generate_and_send_report(context.bot, telegram_id, user, response_1, message)
    except Exception as e:
        logging.exception("handle_evening_response_2 error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass


async def generate_and_send_report(bot, telegram_id: int, user: dict, response_1: str, response_2: str):
    """Generate evening report with yesterday comparison and send it"""
    try:
        from bot.services.ai import generate_evening_report

        lang = user.get("language", "uz")
        db = get_db()
        today = datetime.utcnow().date()
        yesterday = today - timedelta(days=1)

        # Get today's tasks from DB
        today_tasks = await db.tasks.find({
            "user_id": user["_id"],
            "date": today.isoformat()
        }).to_list(length=100)

        done_today = [t for t in today_tasks if t.get("is_done") or t.get("status") == "done"]
        skipped_today = [t for t in today_tasks if t.get("status") == "skipped"]
        total_today = len(today_tasks)
        done_count = len(done_today)
        productivity_today = round((done_count / total_today * 100)) if total_today > 0 else 0

        # Get yesterday's report for comparison
        yesterday_log = await db.daily_reports.find_one({
            "telegram_id": telegram_id,
            "date": yesterday.isoformat()
        })
        yesterday_productivity = yesterday_log.get("productivity", 0) if yesterday_log else 0

        # Calculate difference
        diff = productivity_today - yesterday_productivity
        if diff > 0:
            diff_text = {
                "uz": f"Kechagidan {diff}% yaxshiroq 📈",
                "ru": f"На {diff}% лучше, чем вчера 📈",
                "en": f"{diff}% better than yesterday 📈"
            }
        elif diff < 0:
            diff_text = {
                "uz": f"Kechagidan {abs(diff)}% past 📉",
                "ru": f"На {abs(diff)}% хуже, чем вчера 📉",
                "en": f"{abs(diff)}% lower than yesterday 📉"
            }
        else:
            diff_text = {
                "uz": "Kechagi bilan bir xil darajada ➡️",
                "ru": "На том же уровне, что и вчера ➡️",
                "en": "Same as yesterday ➡️"
            }

        # Build task lists
        done_list = "\n".join([f"✅ {t['title']}" for t in done_today]) or "—"
        skip_list = "\n".join([f"❌ {t['title']}" for t in skipped_today]) or "—"

        # Generate AI summary
        summary = await generate_evening_report(
            done_tasks=[t["title"] for t in done_today],
            skipped_tasks=[t["title"] for t in skipped_today],
            extra_work=response_1,
            extra_notes=response_2,
            productivity=productivity_today,
            diff=diff,
            language=lang
        )

        # Build full report
        reports = {
            "uz": f"""🌙 Bugungi hisobot

✅ Bajarildi: {done_count}/{total_today}
📊 Samaradorlik: {productivity_today}%
{diff_text['uz']}

Bajarilgan ishlar:
{done_list}

Qoldirilgan:
{skip_list}

Qo'shimcha ishlar:
📝 {response_1}
➕ {response_2}

🤖 Xulosa:
{summary}""",
            "ru": f"""🌙 Итоги дня

✅ Выполнено: {done_count}/{total_today}
📊 Продуктивность: {productivity_today}%
{diff_text['ru']}

Выполненные задачи:
{done_list}

Пропущено:
{skip_list}

Дополнительно:
📝 {response_1}
➕ {response_2}

🤖 Итог:
{summary}""",
            "en": f"""🌙 Daily Report

✅ Done: {done_count}/{total_today}
📊 Productivity: {productivity_today}%
{diff_text['en']}

Completed:
{done_list}

Skipped:
{skip_list}

Extra work:
📝 {response_1}
➕ {response_2}

🤖 Summary:
{summary}"""
        }

        # Send report
        await bot.send_message(
            chat_id=telegram_id,
            text=reports.get(lang, reports["uz"])
        )

        # Save today's report to DB for tomorrow's comparison
        await db.daily_reports.update_one(
            {"telegram_id": telegram_id, "date": today.isoformat()},
            {"$set": {
                "telegram_id": telegram_id,
                "date": today.isoformat(),
                "productivity": productivity_today,
                "done_count": done_count,
                "total_count": total_today,
                "done_tasks": [t["title"] for t in done_today],
                "skipped_tasks": [t["title"] for t in skipped_today],
                "extra_work": response_1,
                "extra_notes": response_2,
                "created_at": datetime.utcnow()
            }},
            upsert=True
        )
    except Exception as e:
        logging.exception("generate_and_send_report error for %s: %s", telegram_id, e)


async def handle_custdev_answer(update, context, user, message):
    try:
        telegram_id = update.effective_user.id
        db = get_db()
        state_doc = await get_state(telegram_id)
        survey_id = state_doc.get("custdev_survey_id")
        lang = user.get("language", "uz")

        # Save response
        from bson import ObjectId
        await db.custdev_surveys.update_one(
            {"_id": ObjectId(survey_id)},
            {"$push": {"responses": {
                "telegram_id": telegram_id,
                "username": user.get("username", ""),
                "answer": message,
                "created_at": datetime.utcnow()
            }}}
        )

        await clear_state(telegram_id)

        msgs = {
            "uz": "✅ Fikringiz uchun rahmat! 🙏",
            "ru": "✅ Спасибо за ваш ответ! 🙏",
            "en": "✅ Thank you for your feedback! 🙏"
        }
        await update.message.reply_text(msgs.get(lang, msgs["uz"]))
    except Exception as e:
        logging.exception("handle_custdev_answer error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass
