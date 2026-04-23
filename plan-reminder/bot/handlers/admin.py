import logging
import os
from datetime import datetime
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bot.services.db import get_db

async def is_admin(update: Update) -> bool:
    try:
        if not update.effective_user:
            return False
        admin_id = os.getenv("ADMIN_ID")
        if admin_id and update.effective_user.id == int(admin_id):
            return True
        db = get_db()
        admin_doc = await db.admins.find_one({"telegram_id": update.effective_user.id})
        return bool(admin_doc)
    except Exception as e:
        logging.exception("is_admin error: %s", e)
        return False

async def admin_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger mass broadcast — /send"""
    try:
        if not await is_admin(update):
            return
        if not update.message:
            return
            
        from bot.models.state import set_state
        await set_state(update.effective_user.id, "awaiting_broadcast_message")
        
        await update.message.reply_text(
            "📢 Barcha foydalanuvchilarga yuboriladigan xabarni yuboring (Matn, Rasm, Video yoki Ovozli xabar).\n\n"
            "Bekor qilish uchun /cancel tugmasini bosing."
        )
    except Exception as e:
        logging.exception("admin_send error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass

async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        from bot.models.state import clear_state
        await clear_state(update.effective_user.id)
        if update.message:
            await update.message.reply_text("❌ Amaliyot bekor qilindi.")
    except Exception as e:
        logging.exception("admin_cancel error: %s", e)

async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
            
        admin_id = update.effective_user.id
        from bot.models.state import clear_state
        await clear_state(admin_id)
        
        message = update.message
        if not message:
            return
            
        db = get_db()
        users = await db.users.find({}).to_list(length=100000)
        
        status_msg = await message.reply_text(f"⏳ {len(users)} ta foydalanuvchiga xabar yuborish boshlandi...")
        
        success_count = 0
        fail_count = 0
        
        import asyncio
        for user in users:
            try:
                await context.bot.copy_message(
                    chat_id=user["telegram_id"],
                    from_chat_id=message.chat_id,
                    message_id=message.message_id
                )
                success_count += 1
                await asyncio.sleep(0.05) # Rate limiting avoidance (20 msg/sec)
            except Exception:
                fail_count += 1
                
        await status_msg.edit_text(f"✅ Xabar yuborish yakunlandi.\n\nBorganlar: {success_count}\nXatoliklar: {fail_count}")
    except Exception as e:
        logging.exception("handle_broadcast_message error: %s", e)
        if update.message:
            await update.message.reply_text("Xatolik yuz berdi.")

async def admin_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        if not update.message:
            return
        if not context.args:
            await update.message.reply_text("Usage: /add_admin [telegram_id]")
            return
        
        try:
            new_admin_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Noto'g'ri ID format.")
            return
            
        db = get_db()
        await db.admins.update_one(
            {"telegram_id": new_admin_id},
            {"$set": {"telegram_id": new_admin_id, "created_at": datetime.utcnow()}},
            upsert=True
        )
        await update.message.reply_text(f"✅ Yangi admin qo'shildi: {new_admin_id}")
    except Exception as e:
        logging.exception("admin_add_admin error: %s", e)

async def admin_remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        if not update.message:
            return
        if not context.args:
            await update.message.reply_text("Usage: /remove_admin [telegram_id]")
            return
        
        try:
            target_admin_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Noto'g'ri ID format.")
            return
            
        db = get_db()
        result = await db.admins.delete_one({"telegram_id": target_admin_id})
        if result.deleted_count > 0:
            await update.message.reply_text(f"✅ Admin o'chirildi: {target_admin_id}")
        else:
            await update.message.reply_text("❌ Bunday admin topilmadi.")
    except Exception as e:
        logging.exception("admin_remove_admin error: %s", e)

async def admin_promo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        if not update.message:
            return
            
        from bot.models.state import set_state
        await set_state(update.effective_user.id, "admin_awaiting_promo_code")
        await update.message.reply_text("🎟 Promokod nomini kiriting (masalan: YOZ20):")
    except Exception as e:
        logging.exception("admin_promo error: %s", e)

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        if not update.message:
            return
            
        import os
        from dotenv import load_dotenv
        load_dotenv(dotenv_path="../.env")
        url = os.getenv("MINI_APP_URL", "https://app.somly.ai")
            
        text = (
            "👑 Admin buyruqlari:\n"
            "/send [xabar] — Barcha userlarga xabar\n"
            "/promo — Promo kodlar\n"
            "/add_admin [id] — Admin qo'shish\n"
            "/remove_admin [id] — Admin o'chirish\n\n"
            f"Batafsil statistika: {url}/admin"
        )
        await update.message.reply_text(text)
    except Exception as e:
        logging.exception("admin_help error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass
