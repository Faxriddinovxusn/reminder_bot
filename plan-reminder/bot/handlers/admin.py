import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bot.services.db import get_db

AI_CONVERSATIONS_COLL = "ai_conversations"

# Rate-limit tracker for /send command (admin_id -> last_send_time)
_send_rate_limit = {}

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

async def format_datetime(value: Any) -> str:
    try:
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        if value:
            return str(value)
        return "-"
    except Exception as e:
        logging.exception("format_datetime error: %s", e)
        return "-"

async def send_chunked_text(update: Update, text: str) -> None:
    try:
        if not update.message:
            return
        chunk_size = 3500
        for start in range(0, len(text), chunk_size):
            await update.message.reply_text(text[start:start + chunk_size])
    except Exception as e:
        logging.exception("send_chunked_text error: %s", e)
        raise

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        db = get_db()
        users = await db.users.find({}).sort("last_active", -1).to_list(length=1000)
        if not users:
            if update.message:
                await update.message.reply_text("No users found.")
            return

        lines = ["Users:"]
        for user in users:
            username = user.get("username") or "unknown"
            telegram_id = user.get("telegram_id", "-")
            segment = user.get("segment", "new")
            interaction_count = user.get("interaction_count", 0)
            last_active = await format_datetime(user.get("last_active"))
            lines.append(
                f"- {username} ({telegram_id}) | {segment} | {interaction_count} messages | last active: {last_active}"
            )

        await send_chunked_text(update, "\n".join(lines))
    except Exception as e:
        logging.exception("users_command error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass

async def admin_see(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        if not update.message:
            return
        
        await update.message.reply_text("Ma'lumotlar yig'ilmoqda... Bu biroz vaqt olishi mumkin ⏳")
        
        db = get_db()
        users = await db.users.find({}).to_list(length=10000)
        
        total_users = len(users)
        segments = {"new": 0, "active": 0, "power_user": 0}
        languages = {"uz": 0, "ru": 0, "en": 0}
        total_interactions = 0
        
        for u in users:
            seg = u.get("segment", "new")
            segments[seg] = segments.get(seg, 0) + 1
            
            lang = u.get("language", "uz")
            languages[lang] = languages.get(lang, 0) + 1
            
            total_interactions += u.get("interaction_count", 0)
            
        avg_interactions = round(total_interactions / total_users, 1) if total_users > 0 else 0
        
        # Prepare data for AI
        data_summary = f"""
Siz botning asosiy admini uchun umumiy hisobot tayyorlab beruvchi AI siz.
Mana botdagi barcha foydalanuvchilarning umumlashtirilgan statistikasi:
- Umumiy foydalanuvchilar soni: {total_users}
- Faollik bo'yicha: Yangi ({segments.get('new', 0)}), Faol ({segments.get('active', 0)}), Juda faol ({segments.get('power_user', 0)})
- Tillari: O'zbek ({languages.get('uz',0)}), Rus ({languages.get('ru',0)}), Ingliz ({languages.get('en',0)})
- O'rtacha xabarlar soni har bir userga: {avg_interactions}

Iltimos, ushbu ma'lumotlarga asoslanib, adminga botning joriy holati haqida qisqa, tushunarli va professional tahliliy hisobot (summary) yozib bering. Userlar holatini yaxshilash uchun bitta qisqa maslahat ham bering.
"""
        from bot.services.ai import get_ai_response
        ai_reply = await get_ai_response(data_summary, "uz", [])
        
        await send_chunked_text(update, f"📊 AI Hisoboti:\n\n{ai_reply}")
    except Exception as e:
        logging.exception("admin_see error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("Xatolik yuz berdi.")
        except Exception:
            pass

async def user_detail_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        if not update.message:
            return
        if not context.args:
            await update.message.reply_text("Usage: /user [telegram_id]")
            return

        raw_telegram_id = context.args[0]
        if raw_telegram_id.isdigit():
            query = {"telegram_id": {"$in": [int(raw_telegram_id), raw_telegram_id]}}
        else:
            query = {"telegram_id": raw_telegram_id}
        db = get_db()
        user = await db.users.find_one(query)
        if not user:
            await update.message.reply_text("User not found.")
            return

        user_data = {}
        for key, value in user.items():
            if key == "_id":
                user_data[key] = str(value)
            elif isinstance(value, datetime):
                user_data[key] = await format_datetime(value)
            else:
                user_data[key] = value

        personality = user.get("personality", {})
        habits = user.get("habits", [])
        style = user.get("communication_style", "unknown")
        segment = user.get("segment", "new")

        detail_text = f"🧠 Xarakter: {style}\n"
        detail_text += f"📌 Segmenti: {segment}\n"
        detail_text += f"🔄 Odatlar: {', '.join(habits) if habits else '—'}\n"
        detail_text += f"💬 Xabarlar: {user.get('interaction_count', 0)}\n"
        if personality:
            traits = personality.get("personality_traits", [])
            energy = personality.get("energy_level", "—")
            detail_text += f"⚡ Energiya: {energy}\n"
            detail_text += f"🎯 Xususiyatlar: {', '.join(traits) if traits else '—'}\n"

        detail_text += "\n📄 To'liq malumot:\n"
        detail_text += json.dumps(user_data, ensure_ascii=False, indent=2, default=str)
        
        await send_chunked_text(update, detail_text)
    except Exception as e:
        logging.exception("user_detail_command error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass

async def custdev_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        db = get_db()
        conversations = await db[AI_CONVERSATIONS_COLL].find({}).sort("created_at", -1).to_list(length=10)
        if not conversations:
            if update.message:
                await update.message.reply_text("No conversation summaries found.")
            return

        lines = ["Last 10 AI conversation summaries:"]
        for item in conversations:
            created_at = await format_datetime(item.get("created_at"))
            username = item.get("username") or item.get("telegram_id", "unknown")
            segment = item.get("segment", "new")
            summary = item.get("summary", "")
            lines.append(f"- [{created_at}] {username} ({segment}) -> {summary}")

        await send_chunked_text(update, "\n".join(lines))
    except Exception as e:
        logging.exception("custdev_command error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass

async def admin_segment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        db = get_db()
        
        new_users = await db.users.count_documents({"segment": "new"})
        active_users = await db.users.count_documents({"segment": "active"})
        power_users = await db.users.count_documents({"segment": "power_user"})
        total = new_users + active_users + power_users
        
        text = f"📊 Segmentlar:\n\n"
        text += f"🆕 Yangi (0-5 xabar): {new_users} ta\n"
        text += f"✅ Faol (6-20 xabar): {active_users} ta\n"
        text += f"⚡ Power user (20+): {power_users} ta\n"
        text += f"👥 Jami: {total} ta"
        
        if update.message:
            await update.message.reply_text(text)
    except Exception as e:
        logging.exception("admin_segment error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass

async def admin_custdev(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update):
        return
    
    args = context.args or []
    db = get_db()
    
    if not args or args[0] == "list":
        surveys = await db.custdev_surveys.find({}).sort("created_at", -1).to_list(10)
        if not surveys:
            if update.message:
                await update.message.reply_text("Custdev so'rovlar yo'q.")
            return
        text = "📋 Custdev so'rovlar:\n\n"
        for s in surveys:
            count = len(s.get("responses", []))
            text += f"• {str(s['_id'])[:8]}... | {s['question'][:40]} | {count} javob\n"
        if update.message:
            await update.message.reply_text(text)
        return
    
    if args[0] == "view" and len(args) > 1:
        from bson import ObjectId
        survey = await db.custdev_surveys.find_one({"_id": ObjectId(args[1])})
        if not survey:
            if update.message:
                await update.message.reply_text("Topilmadi.")
            return
        responses = survey.get("responses", [])
        text = f"📊 {survey['question']}\n"
        text += f"Javoblar: {len(responses)} ta\n\n"
        for r in responses[-10:]:
            text += f"👤 @{r.get('username','?')}: {r.get('answer','')[:80]}\n\n"
        if update.message:
            await update.message.reply_text(text)
        return

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        db = get_db()
        total_users = await db.users.count_documents({})
        paid_users = await db.users.count_documents({"is_paid": True})
        total_tasks = await db.tasks.count_documents({})
        done_tasks = await db.tasks.count_documents({"is_done": True})
        
        text = f"📊 Statistika:\n\nUmumiy userlar: {total_users}\nPullik obunalar: {paid_users}\nUmumiy vazifalar: {total_tasks}\nBajarilgan vazifalar: {done_tasks}"
        if update.message:
            await update.message.reply_text(text)
    except Exception as e:
        logging.exception("admin_stats error: %s", e)

async def admin_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        if not update.message:
            return
            
        from bot.models.state import set_state
        await set_state(update.effective_user.id, "admin_awaiting_broadcast")
        await update.message.reply_text("📣 Barcha foydalanuvchilarga yubormoqchi bo'lgan xabaringizni yozing:")
    except Exception as e:
        logging.exception("admin_send error: %s", e)

async def admin_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        if not update.message:
            return
        if not context.args:
            await update.message.reply_text("Usage: /addadmin [telegram_id]")
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
            await update.message.reply_text("Usage: /removeadmin [telegram_id]")
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


async def admin_free_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        if not update.message:
            return
        db = get_db()
        result = await db.users.update_many(
            {},
            {"$set": {
                "is_paid": True,
                "paid_until": datetime(2099, 1, 1),
                "subscription_status": "paid"
            }}
        )
        count = result.modified_count
        await update.message.reply_text(f"✅ Barcha {count} ta user tekin qilindi")
    except Exception as e:
        logging.exception("admin_free_all error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass


async def admin_paid_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        if not update.message:
            return
        db = get_db()
        result = await db.users.update_many(
            {},
            {"$set": {
                "is_paid": False,
                "subscription_status": "expired"
            }}
        )
        count = result.modified_count
        await update.message.reply_text(f"✅ Barcha {count} ta user pullik rejimga o'tkazildi")
    except Exception as e:
        logging.exception("admin_paid_all error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass


async def admin_change_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        if not update.message:
            return
            
        from bot.models.state import set_state
        await set_state(update.effective_user.id, "admin_awaiting_price")
        await update.message.reply_text("💵 Obuna narxini qancha qilib qo'ymoqchisiz?\nIltimos, faqat raqam kiriting (masalan: 20000).")
    except Exception as e:
        logging.exception("admin_change_price error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass


async def admin_free_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        if not update.message:
            return
        if not context.args:
            await update.message.reply_text("Usage: /freeuserlar [telegram_id]\nMasalan: /freeuserlar 123456789")
            return
        try:
            target_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Noto'g'ri telegram_id.")
            return
        db = get_db()
        user = await db.users.find_one({"telegram_id": target_id})
        if not user:
            await update.message.reply_text("❌ User topilmadi.")
            return
        await db.users.update_one(
            {"telegram_id": target_id},
            {"$set": {
                "is_paid": True,
                "paid_until": datetime(2099, 1, 1)
            }}
        )
        username = user.get("username") or str(target_id)
        await update.message.reply_text(f"✅ @{username} tekin qilindi")
    except Exception as e:
        logging.exception("admin_free_user error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass


async def admin_paid_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        if not update.message:
            return
        if not context.args:
            await update.message.reply_text("Usage: /paiduserlar [telegram_id]\nMasalan: /paiduserlar 123456789")
            return
        try:
            target_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Noto'g'ri telegram_id.")
            return
        db = get_db()
        user = await db.users.find_one({"telegram_id": target_id})
        if not user:
            await update.message.reply_text("❌ User topilmadi.")
            return
        await db.users.update_one(
            {"telegram_id": target_id},
            {"$set": {
                "is_paid": False,
                "subscription_status": "expired"
            }}
        )
        username = user.get("username") or str(target_id)
        await update.message.reply_text(f"✅ @{username} pullik rejimga o'tkazildi")
    except Exception as e:
        logging.exception("admin_paid_user error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass


async def admin_see(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick user lookup — /see [ID or username]"""
    try:
        if not await is_admin(update):
            return
        if not update.message:
            return
        if not context.args:
            await update.message.reply_text(
                "📋 Foydalanish:\n"
                "/see [telegram_id] — ID bo'yicha\n"
                "/see [username] — Username bo'yicha\n\n"
                "Misol: /see 6028715926\n"
                "Misol: /see XusniddinWR"
            )
            return

        query_str = context.args[0]
        db = get_db()

        # Try finding by telegram_id (numeric) or username
        if query_str.isdigit():
            user = await db.users.find_one({"$or": [
                {"telegram_id": int(query_str)},
                {"telegram_id": query_str}
            ]})
        else:
            # Remove @ if present
            clean = query_str.lstrip("@")
            user = await db.users.find_one({"username": {"$regex": f"^{clean}$", "$options": "i"}})

        if not user:
            await update.message.reply_text(f"❌ '{query_str}' — foydalanuvchi topilmadi.")
            return

        username = user.get("username") or "—"
        tid = user.get("telegram_id", "—")
        lang = user.get("language", "uz")
        segment = user.get("segment", "new")
        style = user.get("communication_style", "unknown")
        interactions = user.get("interaction_count", 0)
        habits = user.get("habits", [])
        is_paid = user.get("is_paid", False)
        sub_status = user.get("subscription_status", "—")
        paid_until = await format_datetime(user.get("paid_until"))
        trial_end = await format_datetime(user.get("trial_end"))
        last_active = await format_datetime(user.get("last_active"))
        created_at = await format_datetime(user.get("created_at"))
        web_pin = user.get("web_pin", "—")

        # Count tasks
        total_tasks = await db.tasks.count_documents({"telegram_id": tid})
        done_tasks = await db.tasks.count_documents({"telegram_id": tid, "$or": [{"done": True}, {"is_done": True}]})
        rate = round((done_tasks / total_tasks * 100) if total_tasks > 0 else 0)

        text = (
            f"👤 @{username}\n"
            f"🆔 ID: {tid}\n"
            f"🌐 Til: {lang.upper()}\n"
            f"📌 Segment: {segment}\n"
            f"🧠 Muloqot usuli: {style}\n"
            f"💬 Xabarlar: {interactions}\n"
            f"🔄 Odatlar: {', '.join(habits[:5]) if habits else '—'}\n"
            f"\n💰 Obuna: {'✅ Pullik' if is_paid else '❌ Yo\'q'} ({sub_status})\n"
            f"📅 Obuna tugashi: {paid_until}\n"
            f"⏳ Sinov tugashi: {trial_end}\n"
            f"\n📊 Vazifalar: {done_tasks}/{total_tasks} ({rate}%)\n"
            f"🔑 Web PIN: {web_pin}\n"
            f"⏰ Oxirgi faollik: {last_active}\n"
            f"📆 Ro'yxatdan o'tgan: {created_at}"
        )
        await update.message.reply_text(text)
    except Exception as e:
        logging.exception("admin_see error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass


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


async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not await is_admin(update):
            return
        if not update.message:
            return
        text = (
            "🛠 Admin buyruqlari:\n\n"
            "👥 Foydalanuvchilar:\n"
            "/users — Barcha userlar ro'yxati\n"
            "/user [telegram_id] — User haqida to'liq JSON\n"
            "/see [ID yoki username] — User haqida tezkor ko'rish\n"
            "/segment — Segmentlar statistikasi\n"
            "\n💰 Obuna boshqaruvi:\n"
            "/freeall — Barcha userlarni tekin qilish\n"
            "/paidall — Barcha userlarni pullik rejimga o'tkazish\n"
            "/freeuserlar [telegram_id] — Bitta userni tekin qilish\n"
            "/paiduserlar [telegram_id] — Bitta userni pullik qilish\n"
            "/setprice [narx] — Obuna narxini o'zgartirish\n"
            "\n📢 Kommunikatsiya:\n"
            "/send — Barchaga media/matn yuborish (Broadcast)\n"
            "/broadcast [xabar] — Barchaga matn yuborish (eski)\n"
            "\n🎟️ Marketing:\n"
            "/promo [code] [%] [max] [kun] — Promokod yaratish\n"
            "/custdev — Custdev so'rovlar\n"
            "\n📊 Statistika:\n"
            "/stats — Umumiy statistika\n"
            "\n🔧 Admin boshqaruvi:\n"
            "/addadmin [telegram_id] — Admin qo'shish\n"
            "/removeadmin [telegram_id] — Admin o'chirish\n"
            "/adminhelp — Shu yordam"
        )
        await update.message.reply_text(text)
    except Exception as e:
        logging.exception("admin_help error: %s", e)
        try:
            if update.message:
                await update.message.reply_text("An error occurred.")
        except Exception:
            pass
