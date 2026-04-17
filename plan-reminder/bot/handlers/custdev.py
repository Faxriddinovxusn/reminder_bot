import logging
from datetime import datetime
from bot.services.db import get_db
from bot.handlers.admin import is_admin

async def admin_custdev_create(update, context):
    if not await is_admin(update):
        return
    
    # Format: /custdev create "Question text" [target: all/active/power_user]
    args = context.args
    if not args or args[0] != "create":
        await update.message.reply_text(
            "Format: /custdev create [savol] [target]\n"
            "Target: all / active / power_user\n\n"
            "Misol: /custdev create 'Qaysi funksiya eng foydali?' all"
        )
        return
    
    # Parse question and target
    full_text = " ".join(args[1:])
    target = "all"
    if full_text.endswith((" all", " active", " power_user")):
        parts = full_text.rsplit(" ", 1)
        question = parts[0].strip("'\"")
        target = parts[1]
    else:
        question = full_text.strip("'\"")
    
    db = get_db()
    custdev_id = await db.custdev_surveys.insert_one({
        "question": question,
        "target": target,
        "status": "pending",
        "responses": [],
        "created_at": datetime.utcnow(),
        "created_by": update.effective_user.id
    })
    
    await update.message.reply_text(
        f"✅ Custdev yaratildi!\n"
        f"ID: {custdev_id.inserted_id}\n"
        f"Savol: {question}\n"
        f"Target: {target}\n\n"
        f"Yuborish uchun: /custdev send {custdev_id.inserted_id}"
    )

async def admin_custdev_send(update, context, application):
    if not await is_admin(update):
        return
    
    args = context.args
    if not args or args[0] != "send" or len(args) < 2:
        await update.message.reply_text("Format: /custdev send [ID]")
        return
    
    from bson import ObjectId
    db = get_db()
    survey = await db.custdev_surveys.find_one({"_id": ObjectId(args[1])})
    if not survey:
        await update.message.reply_text("Custdev topilmadi.")
        return
    
    # Get target users
    target = survey.get("target", "all")
    if target == "all":
        users = await db.users.find({}).to_list(length=10000)
    elif target == "power_user":
        users = await db.users.find({"segment": "power_user"}).to_list(length=10000)
    else:
        users = await db.users.find({"segment": target}).to_list(length=10000)
    
    await update.message.reply_text(f"📤 {len(users)} ta userga yuborilmoqda...")
    
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    import asyncio
    survey_id = str(survey["_id"])
    
    sent = 0
    failed = 0
    # Batch: 10 users per minute
    for i, user in enumerate(users):
        try:
            lang = user.get("language", "uz")
            msgs = {
                "uz": f"🤝 Sizning fikringiz muhim!\n\n{survey['question']}",
                "ru": f"🤝 Ваше мнение важно!\n\n{survey['question']}",
                "en": f"🤝 Your opinion matters!\n\n{survey['question']}"
            }
            keyboard = [[InlineKeyboardButton("✍️ Javob berish", callback_data=f"custdev_answer_{survey_id}")]]
            await application.bot.send_message(
                chat_id=user["telegram_id"],
                text=msgs.get(lang, msgs["uz"]),
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            sent += 1
            # Batch control: pause every 10 users
            if (i + 1) % 10 == 0:
                await asyncio.sleep(60)
        except:
            failed += 1
    
    await update.message.reply_text(f"✅ Yuborildi: {sent}\n❌ Xato: {failed}")

async def custdev_response_handler(update, context):
    query = update.callback_query
    await query.answer()
    telegram_id = query.from_user.id
    survey_id = query.data.split("_")[2]
    
    # Save state: waiting for custdev answer
    db = get_db()
    await db.user_states.update_one(
        {"telegram_id": telegram_id},
        {"$set": {
            "state": "custdev_answering",
            "custdev_survey_id": survey_id,
            "updated_at": datetime.utcnow()
        }},
        upsert=True
    )
    
    user = await db.users.find_one({"telegram_id": telegram_id})
    lang = user.get("language", "uz") if user else "uz"
    msgs = {
        "uz": "✍️ Fikringizni yozing (matn yoki ovoz):",
        "ru": "✍️ Напишите ваш ответ (текст или голос):",
        "en": "✍️ Write your answer (text or voice):"
    }
    await query.edit_message_text(msgs.get(lang, msgs["uz"]))
