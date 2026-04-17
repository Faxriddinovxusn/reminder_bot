import logging
from datetime import datetime, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from bot.services.db import get_db


async def get_subscription_price() -> int:
    try:
        db = get_db()
        setting = await db.settings.find_one({"key": "subscription_price"})
        return setting.get("value", 15000) if setting else 15000
    except Exception as e:
        logging.exception("get_subscription_price error: %s", e)
        return 15000

async def send_expired_message(bot, telegram_id: int, language: str):
    try:
        price = await get_subscription_price()
        price_str = f"{price:,}"
        msgs = {
            "uz": f"⏰ Sinov muddatingiz tugadi.\n\nObuna xarid qiling 👇\n\n💳 Oylik: {price_str} so'm\nKarta: 9860 1201 0718 3945\nFaxriddinov X.\n\nTo'lovni amalga oshirib skrinshot yuboring.\nAdmin tez orada tasdiqlaydi ✅",
            "ru": f"⏰ Пробный период завершён.\n\nОформите подписку 👇\n\n💳 Месячная: {price_str} сум\nКарта: 9860 1201 0718 3945\nFaxriddinov X.\n\nОплатите и отправьте скриншот.\nАдминистратор подтвердит ✅",
            "en": f"⏰ Trial ended.\n\nSubscribe to continue 👇\n\n💳 Monthly: {price_str} UZS\nCard: 9860 1201 0718 3945\nFaxriddinov X.\n\nSend screenshot after payment.\nAdmin will confirm shortly ✅"
        }
        await bot.send_message(chat_id=telegram_id, text=msgs.get(language, msgs["uz"]))
    except Exception as e:
        logging.exception("send_expired_message error: %s", e)

async def payment_screenshot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        from bot.config import is_admin, ADMIN_ID
        from bot.models.user import get_user_by_telegram_id
        from bot.services.db import get_db
        from datetime import datetime
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        
        if not update.effective_user or not update.message:
            return
            
        telegram_id = update.effective_user.id
        if is_admin(telegram_id):
            return
        
        user = await get_user_by_telegram_id(telegram_id)
        if not user:
            return
        language = user.get("language", "uz")
        
        promo_code = context.user_data.get("promo_code")
        discount = 0
        if promo_code:
            db = get_db()
            promo = await db.promos.find_one({"code": promo_code.upper()})
            if promo and datetime.utcnow() < promo.get("valid_until", datetime.max) and promo.get("used_count", 0) < promo.get("max_uses", 0):
                discount = promo.get("discount_percent", 0)
        
        base_price = await get_subscription_price()
        final_price = int(base_price * (1 - discount/100))
        caption = f"💳 Yangi to'lov\n👤 @{update.effective_user.username} (ID: {telegram_id})\n💰 {final_price:,} so'm"
        if discount:
            caption += f" ({discount}% — {promo_code})"
        
        keyboard = [[
            InlineKeyboardButton("✅ TASDIQLANDI", callback_data=f"pay_approve_{telegram_id}_{promo_code or 'none'}"),
            InlineKeyboardButton("❌ TASDIQLANMADI", callback_data=f"pay_reject_{telegram_id}")
        ]]
        
        await context.bot.forward_message(chat_id=ADMIN_ID, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
        await context.bot.send_message(chat_id=ADMIN_ID, text=caption, reply_markup=InlineKeyboardMarkup(keyboard))
        
        ack = {
            "uz": "📨 Skrinshot qabul qilindi! Admin tez orada tasdiqlaydi.",
            "ru": "📨 Скриншот получен! Подтвердим в ближайшее время.",
            "en": "📨 Screenshot received! Admin will confirm shortly."
        }
        await update.message.reply_text(ack.get(language, ack["uz"]))
    except Exception as e:
        logging.exception("payment_screenshot_handler error: %s", e)

async def payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        from bot.models.user import get_user_by_telegram_id
        from bot.services.db import get_db
        from datetime import datetime, timedelta
        
        query = update.callback_query
        if not query:
            return
            
        await query.answer()
        data = query.data
        if not data:
            return
        
        if data.startswith("pay_approve_"):
            parts = data.split("_")
            user_telegram_id = int(parts[2])
            promo_code = parts[3] if parts[3] != "none" else None
            
            db = get_db()
            paid_until = datetime.utcnow() + timedelta(days=30)
            await db.users.update_one(
                {"telegram_id": user_telegram_id},
                {"$set": {"is_paid": True, "paid_until": paid_until, "subscription_status": "paid"}}
            )
            
            if promo_code:
                await db.promos.update_one({"code": promo_code.upper()}, {"$inc": {"used_count": 1}})
            
            user = await get_user_by_telegram_id(user_telegram_id)
            lang = user.get("language", "uz") if user else "uz"
            msgs = {
                "uz": "🎉 Obunangiz faollashtirildi! 30 kun to'liq foydalaning.",
                "ru": "🎉 Подписка активирована! 30 дней полного доступа.",
                "en": "🎉 Subscription activated! 30 days of full access."
            }
            await context.bot.send_message(chat_id=user_telegram_id, text=msgs.get(lang, msgs["uz"]))
            await query.edit_message_text(f"✅ Tasdiqlandi: {user_telegram_id}")
        
        elif data.startswith("pay_reject_"):
            user_telegram_id = int(data.split("_")[2])
            user = await get_user_by_telegram_id(user_telegram_id)
            lang = user.get("language", "uz") if user else "uz"
            msgs = {
                "uz": "❌ To'lovingiz tasdiqlanmadi. Qayta urinib ko'ring.",
                "ru": "❌ Платёж не подтверждён. Попробуйте снова.",
                "en": "❌ Payment not confirmed. Please try again."
            }
            await context.bot.send_message(chat_id=user_telegram_id, text=msgs.get(lang, msgs["uz"]))
            await query.edit_message_text(f"❌ Rad etildi: {user_telegram_id}")
    except Exception as e:
        logging.exception("payment_callback error: %s", e)
