from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes
import logging

from bot.models.user import create_user, get_user_by_telegram_id, set_language, set_timezone
from bot.models.state import set_state, get_state, clear_state
from bot.messages import messages
from bot.config import MINI_APP_URL

# ═══ TIMEZONE MAP ═══
TIMEZONE_MAP = {
    "tz_uz": {"country": "O'zbekiston", "timezone": "UTC+5", "offset": 5},
    "tz_ru": {"country": "Rossiya", "timezone": "UTC+3", "offset": 3},
    "tz_tr": {"country": "Turkiya", "timezone": "UTC+3", "offset": 3},
    "tz_kz": {"country": "Qozog'iston", "timezone": "UTC+5", "offset": 5},
    "tz_us": {"country": "Amerika", "timezone": "UTC-5", "offset": -5},
}


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


async def _show_timezone_buttons(message_or_query, lang: str, edit: bool = True) -> None:
    """Show timezone country selection buttons."""
    tz_texts = {
        "uz": "🌍 Qaysi davlatdasiz?",
        "ru": "🌍 В какой вы стране?",
        "en": "🌍 What country are you in?",
    }
    keyboard = [
        [InlineKeyboardButton("🇺🇿 O'zbekiston (UTC+5)", callback_data="tz_uz")],
        [InlineKeyboardButton("🇷🇺 Rossiya (UTC+3)", callback_data="tz_ru")],
        [InlineKeyboardButton("🇹🇷 Turkiya (UTC+3)", callback_data="tz_tr")],
        [InlineKeyboardButton("🇰🇿 Qozog'iston (UTC+5)", callback_data="tz_kz")],
        [InlineKeyboardButton("🇺🇸 Amerika (UTC-5)", callback_data="tz_us")],
        [InlineKeyboardButton("🌍 Boshqa", callback_data="tz_other")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = tz_texts.get(lang, tz_texts["uz"])

    if edit:
        await message_or_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await message_or_query.reply_text(text, reply_markup=reply_markup)


async def _send_welcome_message(query, lang: str) -> None:
    """Send the final welcome/instruction message after onboarding is complete."""
    voice_messages = {
        "uz": "Assalomu aleykum!\n🎤 Ovozli xabarlar orqali ham muloqot qilishimiz mumkin, faqat aniq gapirsangiz bas.\n\n📅 Reja tuzish uchun: /plan\n📊 Statistika va jarayonlar uchun: /web\n📱 Mini ilovaga kirish uchun: /app\n🌐 Tilni o'zgartirish uchun: /language",
        "ru": "Здравствуйте!\n🎤 Мы можем общаться голосовыми сообщениями, просто говорите чётко.\n\n📅 Создать план: /plan\n📊 Статистика и процессы: /web\n📱 Открыть мини-приложение: /app\n🌐 Изменить язык: /language",
        "en": "Hello!\n🎤 We can also communicate via voice messages, just speak clearly.\n\n📅 To create a plan: /plan\n📊 For statistics and progress: /web\n📱 To open the mini-app: /app\n🌐 To change language: /language"
    }
    voice_text = voice_messages.get(lang, voice_messages["en"])
    await query.edit_message_text(voice_text)


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
            # Store selected language in user_data for timezone flow
            if context.user_data is not None:
                context.user_data['selected_lang'] = lang
            
        # After language selection → show timezone buttons
        await _show_timezone_buttons(query, lang, edit=True)
        
    except Exception as e:
        logging.exception("language callback error: %s", e)
        try:
            if update.callback_query and update.callback_query.message:
                await update.callback_query.message.reply_text("An error occurred.")
        except Exception:
            pass


async def timezone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle timezone country selection buttons (tz_uz, tz_ru, etc.)"""
    try:
        query = update.callback_query
        if query is None or query.from_user is None:
            return
        await query.answer()
        data = query.data or ""
        tg_id = query.from_user.id

        # Get user's language
        db_user = await get_user_by_telegram_id(tg_id)
        lang = db_user.get("language", "uz") if db_user else "uz"

        if data == "tz_other":
            # Ask user to type their country name
            ask_texts = {
                "uz": "🌍 Davlatingiz nomini yozing:",
                "ru": "🌍 Напишите название вашей страны:",
                "en": "🌍 Type your country name:",
            }
            await query.edit_message_text(ask_texts.get(lang, ask_texts["uz"]))
            await set_state(tg_id, "awaiting_custom_timezone")
            return

        # Known country selected
        tz_info = TIMEZONE_MAP.get(data)
        if not tz_info:
            return
        
        await set_timezone(tg_id, tz_info["country"], tz_info["timezone"], tz_info["offset"])

        # Show confirmation + welcome
        confirm_texts = {
            "uz": f"✅ {tz_info['country']} ({tz_info['timezone']}) tanlandi!",
            "ru": f"✅ {tz_info['country']} ({tz_info['timezone']}) выбрано!",
            "en": f"✅ {tz_info['country']} ({tz_info['timezone']}) selected!",
        }
        await query.edit_message_text(confirm_texts.get(lang, confirm_texts["uz"]))

        from datetime import datetime, timedelta, timezone
        user_tz = timezone(timedelta(hours=tz_info['offset']))
        local_time = datetime.now(user_tz).strftime("%H:%M")

        # Send welcome message showing current time
        voice_messages = {
            "uz": f"Hozir {tz_info['country']}da soat {local_time} bo'ldi, qanday rejangiz bor?",
            "ru": f"Сейчас в {tz_info['country']} {local_time}, какие у вас планы?",
            "en": f"It is now {local_time} in {tz_info['country']}, what are your plans?"
        }
        voice_text = voice_messages.get(lang, voice_messages["uz"])
        await query.message.reply_text(voice_text)

    except Exception as e:
        logging.exception("timezone_callback error: %s", e)
        try:
            if update.callback_query and update.callback_query.message:
                await update.callback_query.message.reply_text("An error occurred.")
        except Exception:
            pass


async def handle_custom_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle user typing a custom country name for timezone detection.
    Returns True if this handler consumed the message, False otherwise."""
    try:
        user = update.effective_user
        if not user or not update.message:
            return False
        
        tg_id = user.id
        state_doc = await get_state(tg_id)
        
        if state_doc.get("state") != "awaiting_custom_timezone":
            return False
        
        country_name = (update.message.text or "").strip()
        if not country_name:
            return False
        
        db_user = await get_user_by_telegram_id(tg_id)
        lang = db_user.get("language", "uz") if db_user else "uz"

        # Use AI to detect timezone offset
        processing_texts = {
            "uz": "⏳ Aniqlanmoqda...",
            "ru": "⏳ Определяю...",
            "en": "⏳ Detecting...",
        }
        wait_msg = await update.message.reply_text(processing_texts.get(lang, processing_texts["uz"]))

        try:
            from bot.services.ai import call_groq
            prompt = f"""User says their country is: "{country_name}"

Detect the most common UTC timezone offset for this country.
Return ONLY a valid JSON object, nothing else:
{{"country": "official country name", "timezone": "UTC+X or UTC-X", "offset": integer_offset}}

Examples:
- Japan → {{"country": "Japan", "timezone": "UTC+9", "offset": 9}}
- Germany → {{"country": "Germany", "timezone": "UTC+1", "offset": 1}}
- Brazil → {{"country": "Brazil", "timezone": "UTC-3", "offset": -3}}
- UAE → {{"country": "UAE", "timezone": "UTC+4", "offset": 4}}

If the country name is misspelled, fix it. If truly unrecognizable, return:
{{"country": "Unknown", "timezone": "UTC+0", "offset": 0}}"""

            ai_result = await call_groq(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
            )

            import json, re
            ai_result = ai_result.replace("```json", "").replace("```", "").strip()
            match = re.search(r"\{.*?\}", ai_result, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                country = parsed.get("country", country_name)
                tz_str = parsed.get("timezone", "UTC+0")
                offset = int(parsed.get("offset", 0))
                
                await set_timezone(tg_id, country, tz_str, offset)
                await clear_state(tg_id)

                from datetime import datetime, timedelta, timezone
                user_tz = timezone(timedelta(hours=offset))
                local_time = datetime.now(user_tz).strftime("%H:%M")

                confirm_texts = {
                    "uz": f"✅ {country} ({tz_str}) tanlandi!",
                    "ru": f"✅ {country} ({tz_str}) выбрано!",
                    "en": f"✅ {country} ({tz_str}) selected!",
                }
                await wait_msg.edit_text(confirm_texts.get(lang, confirm_texts["uz"]))

                # Send welcome message showing current time
                voice_messages = {
                    "uz": f"Hozir {country}da soat {local_time} bo'ldi, qanday rejangiz bor?",
                    "ru": f"Сейчас в {country} {local_time}, какие у вас планы?",
                    "en": f"It is now {local_time} in {country}, what are your plans?"
                }
                voice_text = voice_messages.get(lang, voice_messages["uz"])
                await update.message.reply_text(voice_text)
                return True
            else:
                raise ValueError("No JSON found in AI response")

        except Exception as ai_err:
            logging.exception("AI timezone detection error: %s", ai_err)
            # Fallback: save with UTC+0 and let user know
            await set_timezone(tg_id, country_name, "UTC+0", 0)
            await clear_state(tg_id)
            
            fallback_texts = {
                "uz": f"⚠️ \"{country_name}\" uchun aniq vaqt zonasi topilmadi. UTC+0 sifatida saqlandi.\nSozlamalarda o'zgartirish mumkin.",
                "ru": f"⚠️ Не удалось определить часовой пояс для \"{country_name}\". Сохранено как UTC+0.\nМожно изменить в настройках.",
                "en": f"⚠️ Couldn't detect timezone for \"{country_name}\". Saved as UTC+0.\nYou can change it in settings.",
            }
            await wait_msg.edit_text(fallback_texts.get(lang, fallback_texts["uz"]))

            voice_messages = {
                "uz": "📅 Reja tuzish uchun: /plan\n📱 Mini ilovaga kirish uchun: /app",
                "ru": "📅 Создать план: /plan\n📱 Открыть мини-приложение: /app",
                "en": "📅 To create a plan: /plan\n📱 To open the mini-app: /app"
            }
            await update.message.reply_text(voice_messages.get(lang, voice_messages["uz"]))
            return True

    except Exception as e:
        logging.exception("handle_custom_timezone error: %s", e)
        return True


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
        response_text = texts.get(lang, texts["uz"])
        await update.message.reply_text(
            text=response_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        from bot.models.user import log_command_to_history
        await log_command_to_history(user.id, "/web", response_text)
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
        response_text = texts.get(lang, texts["uz"])
        await update.message.reply_text(
            text=response_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        from bot.models.user import log_command_to_history
        await log_command_to_history(user.id, "/app", response_text)
    except Exception as e:
        logging.exception("app_command error: %s", e)
        if update.message:
            await update.message.reply_text("Biroz xatolik yuz berdi :(")

async def free_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user:
            return
            
        from bot.services.db import get_user_by_telegram_id
        from bot.handlers.todo import set_state
        
        db_user = await get_user_by_telegram_id(user.id)
        lang = db_user.get("language", "uz") if db_user else "uz"
        
        await set_state(user.id, "free_chat")
        
        texts = {
            "uz": "Endi men bilan erkin suhbatlashishingiz mumkin! 😊 Agar reja tuzmoqchi bo'lsangiz, /plan ni bosing.",
            "ru": "Теперь вы можете свободно общаться со мной! 😊 Если хотите составить план, нажмите /plan.",
            "en": "You can now chat with me freely! 😊 If you want to create a plan, press /plan."
        }
        
        response_text = texts.get(lang, texts["uz"])
        await update.message.reply_text(response_text)
        
        from bot.models.user import log_command_to_history
        await log_command_to_history(user.id, "/free", response_text)
    except Exception as e:
        logging.exception("free_command error: %s", e)
        if update.message:
            await update.message.reply_text("Biroz xatolik yuz berdi :(")

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user:
            return
            
        db_user = await get_user_by_telegram_id(user.id)
        lang = db_user.get("language", "uz") if db_user else "uz"
        
        keyboard = [
            [InlineKeyboardButton("🇺🇿 O'zbek", callback_data="lang_uz")],
            [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
            [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        texts = {
            "uz": "Tilni tanlang:",
            "ru": "Выберите язык:",
            "en": "Choose language:"
        }
        text = texts.get(lang, texts["uz"])
        
        await update.message.reply_text(text, reply_markup=reply_markup)
        
        from bot.models.user import log_command_to_history
        await log_command_to_history(user.id, "/language", text)
    except Exception as e:
        logging.exception("language_command error: %s", e)
        if update.message:
            await update.message.reply_text("Biroz xatolik yuz berdi :(")
