from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes
import logging

from bot.models.user import create_user, get_user_by_telegram_id, set_language
from bot.messages import messages
from bot.config import MINI_APP_URL

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if user is None:
            return
        tg_id = user.id
        username = user.username
        existing = await get_user_by_telegram_id(tg_id)
        
        # Determine if user is new
        if context.user_data is not None:
            context.user_data['is_new'] = not bool(existing)

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
        logging.exception("start handler error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass

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
        user_id = user.id if user else "N/A"
        if user:
            await set_language(user.id, lang)
            
        is_new = context.user_data.get('is_new', False) if context.user_data is not None else False
        
        # Voice instruction message
        voice_messages = {
            "uz": "Assalomu aleykum!\n🎤 Ovozli xabarlar orqali ham muloqot qilishimiz mumkin, faqat aniq gapirsangiz bas.\n\n📅 Reja tuzish uchun: /plan\n📊 Statistika va jarayonlar uchun: /web\n📱 Mini ilovaga kirish uchun: /app\n🌐 Tilni o'zgartirish uchun: /language",
            "ru": "Здравствуйте!\n🎤 Мы можем общаться голосовыми сообщениями, просто говорите чётко.\n\n📅 Создать план: /plan\n📊 Статистика и процессы: /web\n📱 Открыть мини-приложение: /app\n🌐 Изменить язык: /language",
            "en": "Hello!\n🎤 We can also communicate via voice messages, just speak clearly.\n\n📅 To create a plan: /plan\n📊 For statistics and progress: /web\n📱 To open the mini-app: /app\n🌐 To change language: /language"
        }
        
        voice_text = voice_messages.get(lang, voice_messages["en"])
        await query.edit_message_text(voice_text)
        
    except Exception as e:
        logging.exception("language callback error: %s", e)
        try:
            if update.callback_query and update.callback_query.message:
                await update.callback_query.message.reply_text("An error occurred.")
        except Exception:
            pass

async def web_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user:
            return
        
        db_user = await get_user_by_telegram_id(user.id)
        if not db_user:
            await update.message.reply_text("Botdan foydalanishni boshlash uchun avval /start ni bosing.")
            return
            
        lang = db_user.get("language", "uz")
        web_pin = db_user.get("web_pin")
        
        if not web_pin:
            import random
            from bot.services.db import get_db
            web_pin = str(random.randint(10000, 99999))
            db = get_db()
            await db.users.update_one({"_id": db_user["_id"]}, {"$set": {"web_pin": web_pin}})
            
        dash_url = f"{MINI_APP_URL}/dashboard/" if MINI_APP_URL else "https://your-domain.com/dashboard/"
        
        texts = {
            "uz": f"💻 *Vebsaytga kirish uchun ma'lumotlar:*\n\n🔑 Har doim sahifaga kirganda bir xil PIN so'raladi.\nSizning PIN kodingiz: `{web_pin}`\n\nPastdagi tugmani bosib Dashboardga kiring:",
            "ru": f"💻 *Данные для входа на сайт:*\n\n🔑 При каждом входе требуется этот PIN.\nВаш PIN код: `{web_pin}`\n\nНажмите на кнопку ниже, чтобы войти в Dashboard:",
            "en": f"💻 *Website login details:*\n\n🔑 Enter this PIN to log in.\nYour PIN code: `{web_pin}`\n\nClick the button below to open the Dashboard:"
        }
        
        keyboard = [[InlineKeyboardButton("💻 Vebsayt (Dashboard)", url=dash_url)]]
        await update.message.reply_text(
            text=texts.get(lang, texts["uz"]),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logging.exception("web_command error: %s", e)
        if update.message:
            await update.message.reply_text("Biroz xatolik yuz berdi :(")

async def app_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user:
            return
            
        db_user = await get_user_by_telegram_id(user.id)
        lang = db_user.get("language", "uz") if db_user else "uz"
        
        texts = {
            "uz": "📱 *Mini Ilovaga xush kelibsiz!*\n\nQuyidagi tugmani bosish orqali ilovaga kiring:",
            "ru": "📱 *Добро пожаловать в Мини-приложение!*\n\nНажмите кнопку ниже, чтобы войти:",
            "en": "📱 *Welcome to the Mini App!*\n\nClick the button below to enter:"
        }
        
        keyboard = [[InlineKeyboardButton("📱 Mini Ilova", web_app=WebAppInfo(url=MINI_APP_URL))]]
        await update.message.reply_text(
            text=texts.get(lang, texts["uz"]),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logging.exception("app_command error: %s", e)
        if update.message:
            await update.message.reply_text("Biroz xatolik yuz berdi :(")
