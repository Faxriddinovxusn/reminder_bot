from telegram import Update
from telegram.ext import ContextTypes
import logging
from datetime import datetime, timezone, timedelta
import os
import tempfile
from pathlib import Path
from dotenv import load_dotenv
import httpx
from bson import ObjectId

# Robust .env loading
_dir = Path(__file__).resolve().parent
while _dir != _dir.parent:
    _env = _dir / '.env'
    if _env.exists():
        load_dotenv(dotenv_path=str(_env))
        break
    _dir = _dir.parent

from bot.models.user import get_user_by_telegram_id
from bot.services.ai import get_ai_response
from bot.messages import messages

# Temp folder for voice files — use system temp dir (cross-platform)
TEMP_DIR = tempfile.gettempdir()

GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

async def _transcribe_voice_httpx(file_path: str, file_name: str, api_key: str, language: str = "en") -> str:
    """Transcribe audio using Groq Whisper API via direct HTTP call (httpx).
    This avoids any SDK version issues completely."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        
        resp = await client.post(
            GROQ_WHISPER_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (file_name, file_bytes, "audio/ogg")},
            data={
                "model": "whisper-large-v3",
                "response_format": "json",
                "language": language,
                "prompt": "Salom, bu o'zbek tilidagi qisqa audio yozuv. Bugun vazifa, reja, majlis va uchrashuv bor." if language == "uz" else ""
            },
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get("text", "")

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming voice messages"""
    temp_file_path = None
    try:
        user = update.effective_user
        if not user:
            return
        
        tg_id = user.id
        db_user = await get_user_by_telegram_id(tg_id)
        if not db_user:
            return
        
        lang = db_user.get("language", "en")
        
        # Check subscription
        now = datetime.utcnow()
        trial_end = db_user.get("trial_end")
        is_paid = db_user.get("is_paid", False)
        paid_until = db_user.get("paid_until")
        
        admin_id_str = os.getenv("ADMIN_ID")
        is_admin = (admin_id_str and str(tg_id) == admin_id_str)
        if not is_admin and not (trial_end and now < trial_end) and not (is_paid and paid_until and now < paid_until):
            await update.message.reply_text(messages.get(lang, messages["en"])["trial_expired"])
            return
        
        # Get voice file from telegram
        voice_file = await update.message.voice.get_file()
        
        # Create temp file path
        file_name = f"voice_{tg_id}_{int(datetime.now().timestamp())}.ogg"
        temp_file_path = os.path.join(TEMP_DIR, file_name)
        
        # Download voice file
        await voice_file.download_to_drive(custom_path=temp_file_path)
        
        try:
            from bot.services.ai import get_current_api_key
            whisper_key = os.getenv("GROQ_WHISPER_API_KEY") or get_current_api_key()
            whisper_lang = "uz" if lang == "uz" else ("ru" if lang == "ru" else "en")
            
            transcribed_text = await _transcribe_voice_httpx(
                file_path=temp_file_path,
                file_name=file_name,
                api_key=whisper_key,
                language=whisper_lang,
            )
            
            if not transcribed_text or not transcribed_text.strip():
                logging.warning("Whisper returned empty transcription for user %s", tg_id)
                await update.message.reply_text("Ovozni aniqlab bo'lmadi. Iltimos, aniqroq gapiring.")
                return
        except Exception as e:
            logging.exception("Whisper transcription error: %s", e)
            await update.message.reply_text(f"Ovozni transcribe qilishda xato yuz berdi. (Xato: {str(e)[:200]})")
            return
        
        from bot.models.state import get_state, set_state, clear_state
        from bot.handlers.todo import update_user_profile_after_message, send_plan_confirmation_message, handle_evening_response_1
        from bot.services.ai import extract_tasks_from_text
        
        state_doc = await get_state(tg_id)
        current_state = state_doc.get("state", "idle")
        
        # Custdev Voice Support
        if current_state == "custdev_answering":
            from bot.handlers.todo import handle_custdev_answer
            await handle_custdev_answer(update, context, db_user, transcribed_text)
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
            return

        # Evening check-in voice support
        if current_state == "evening_checkin_1":
            await handle_evening_response_1(update, context, db_user, transcribed_text)
            return
        
        if current_state == "evening_checkin_2":
            # Legacy fallback: handle as step 1
            await handle_evening_response_1(update, context, db_user, transcribed_text)
            return
        
        # Voice edit support: if user is editing a plan via voice
        if current_state == "awaiting_plan_edit":
            import json as _json
            pending_tasks = state_doc.get("pending_tasks", [])
            tasks_str = _json.dumps(pending_tasks, ensure_ascii=False)
            transcribed_text = f"<system>SYSTEM INFO: The user is currently editing the following pending plan. You MUST output action 'propose_tasks' JSON block with the updated list of tasks.\nCURRENT TASKS: {tasks_str}</system>\n\nUSER EDITS: {transcribed_text}"
        
        if current_state == "awaiting_monthly_input":
            from bot.services.ai import extract_monthly_dates_and_tasks
            tz_offset = int(db_user.get("timezone_offset", 5) or 5)
            monthly_dict = await extract_monthly_dates_and_tasks(transcribed_text, lang, timezone_offset=tz_offset)
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
                no_data = {"uz": "Kechirasiz, sanalar topilmadi. Qaytadan kiriting.", "ru": "Извините, даты не найдены.", "en": "Sorry, no dates found."}
                await update.message.reply_text(no_data.get(lang, no_data["uz"]))
                return

        # Get conversation history from DB
        history = db_user.get("chat_history", [])
        
        # Get AI response based on transcribed text
        from bot.handlers.admin import is_admin as check_is_admin
        is_admin_user = await check_is_admin(update)

        user_profile = {
            "username": db_user.get("username") or user.username or "User",
            "interaction_count": db_user.get("interaction_count", 0),
            "personality": db_user.get("personality", {}),
            "habits": db_user.get("habits", []),
            "communication_style": db_user.get("communication_style", "casual"),
            "topics_discussed": db_user.get("topics_discussed", []),
            "current_state": current_state,
            "is_admin": is_admin_user,
        }
        
        tz_offset = int(db_user.get("timezone_offset", 5) or 5)
        response_data = await get_ai_response(transcribed_text, lang, history, user_profile, timezone_offset=tz_offset)
        ai_response = response_data[0] if isinstance(response_data, tuple) else response_data
        
        # Update history with new messages
        history.append({"role": "user", "content": transcribed_text})
        history.append({"role": "assistant", "content": ai_response})
        new_history = history[-20:]
        from bot.services.db import get_db
        await get_db().users.update_one({"telegram_id": tg_id}, {"$set": {"chat_history": new_history}})
        
        # Parse any actions AI generated
        from bot.api.routes import detect_and_execute_action
        action_result = await detect_and_execute_action(transcribed_text, ai_response, str(tg_id), get_db(), history[-6:])
        
        # Clean response
        import re
        clean_ai_response = re.sub(r"```json\s*\{.*?\}\s*```", "", ai_response, flags=re.DOTALL).strip()
        
        # Intercept propose_tasks from voice — SILENT ADD (same as ai_chat)
        if action_result and action_result.get("action") == "propose_tasks":
            extracted_tasks = action_result.get("data") or []
            if extracted_tasks:
                tasks_without_time = [t for t in extracted_tasks if not t.get("time")]
                if tasks_without_time:
                    title = tasks_without_time[0].get("title", "vazifa")
                    ask_msg = {
                        "uz": f"🕒 \"{title}\" — soat nechada?",
                        "ru": f"🕒 \"{title}\" — во сколько?",
                        "en": f"🕒 \"{title}\" — what time?"
                    }
                    reply_text = ask_msg.get(lang, ask_msg["uz"])
                    new_history[-1]["content"] = reply_text
                    await get_db().users.update_one({"telegram_id": tg_id}, {"$set": {"chat_history": new_history}})
                    await update.message.reply_text(reply_text)
                    return

                from bot.handlers.todo import save_confirmed_plan_tasks
                saved_ids = await save_confirmed_plan_tasks(tg_id, extracted_tasks)
                if saved_ids:
                    task_db = get_db()
                    for i, sid in enumerate(saved_ids):
                        offset = extracted_tasks[i].get("reminder_offset", 0) if i < len(extracted_tasks) else 0
                        try:
                            offset = int(offset)
                        except Exception:
                            offset = 0
                        await task_db.tasks.update_one(
                            {"_id": ObjectId(sid)},
                            {"$set": {"reminder_offset": offset, "reminder_sent": False if offset > 0 else True}}
                        )
                    # Build confirmation message
                    summary_parts = []
                    for i, t in enumerate(extracted_tasks):
                        title = t.get("title", "")
                        time_val = t.get("time", "")
                        offset = t.get("reminder_offset", 0)
                        try:
                            offset = int(offset)
                        except Exception:
                            offset = 0
                        date_str = t.get("target_date", "")
                        date_info = f" ({date_str})" if date_str else ""
                        if title:
                            if offset > 0:
                                summary_parts.append(f"• {time_val}{date_info} — {title} (🔔 {offset} min oldin)")
                            else:
                                summary_parts.append(f"• {time_val}{date_info} — {title} (🔔 vaqtida)")
                    summary_str = "\n".join(summary_parts)

                    db_user_fresh = await get_user_by_telegram_id(tg_id)
                    u_lang = (db_user_fresh or {}).get("language", "uz")
                    confirm_msgs = {
                        "uz": f"✅ Rejaga qo'shildi:\n{summary_str}",
                        "ru": f"✅ Добавлено в план:\n{summary_str}",
                        "en": f"✅ Added to plan:\n{summary_str}"
                    }
                    reply_text = confirm_msgs.get(u_lang, confirm_msgs["uz"])
                    if clean_ai_response:
                        reply_text = clean_ai_response + "\n\n" + reply_text

                    new_history[-1]["content"] = reply_text
                    await get_db().users.update_one({"telegram_id": tg_id}, {"$set": {"chat_history": new_history}})
                    await update.message.reply_text(reply_text)
                    return

        # Intercept unknown_intent from voice
        if action_result and action_result.get("action") == "unknown_intent":
            fallback_text = {
                "uz": "Kechirasiz, gapingizni tushuna olmadim, Iltimos qaytadan ravon gapirishingizni iltimos qilaman.",
                "ru": "Извините, я вас не понял. Пожалуйста, говорите яснее.",
                "en": "Sorry, I didn't quite understand you. Please speak more clearly."
            }
            await update.message.reply_text(fallback_text.get(lang, fallback_text["uz"]))
            return
        
        # Send AI response back to user
        await update.message.reply_text(clean_ai_response)
        
    except Exception as e:
        logging.exception("voice_handler error: %s", e)
        try:
            err_msg = {"uz": "Ovozni qayta ishlashda xato yuz berdi.", "ru": "Ошибка при обработке голосового.", "en": "Error processing voice message."}
            await update.message.reply_text(err_msg.get(lang if 'lang' in dir() else 'uz', err_msg["uz"]))
        except Exception:
            pass
    
    finally:
        # Delete temp file after use
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception as e:
                logging.warning("Failed to delete temp file %s: %s", temp_file_path, e)
