from telegram import Update
from telegram.ext import BaseMiddleware, ContextTypes
from datetime import datetime
import logging

from bot.models.user import get_user_by_telegram_id
from bot.messages import messages
from bot.services.db import get_db
import os


class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(self, handler, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            # Allow /start to bypass subscription check
            if update.message and update.message.text and update.message.text.startswith("/start"):
                return await handler(update, context)

            user = update.effective_user
            if not user:
                return await handler(update, context)

            db_user = await get_user_by_telegram_id(user.id)
            if not db_user:
                return await handler(update, context)

            admin_id = os.getenv("ADMIN_ID")
            if admin_id and user.id == int(admin_id):
                return await handler(update, context)
                
            db = get_db()
            admin_doc = await db.admins.find_one({"telegram_id": user.id})
            if admin_doc:
                return await handler(update, context)

            now = datetime.utcnow()
            trial_end = db_user.get("trial_end")
            is_paid = db_user.get("is_paid", False)
            paid_until = db_user.get("paid_until")

            if trial_end and now < trial_end:
                return await handler(update, context)

            if is_paid and paid_until and now < paid_until:
                return await handler(update, context)

            lang = db_user.get("language", "en")
            text = messages.get(lang, messages["en"])["trial_expired"]
            if update.effective_message:
                await update.effective_message.reply_text(text)
            return
        except Exception as e:
            logging.exception("Subscription middleware error: %s", e)
            return await handler(update, context)
