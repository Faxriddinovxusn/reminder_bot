from groq import Groq
from dotenv import load_dotenv
import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple, Union
import json

from pathlib import Path

# Robust .env loading
_dir = Path(__file__).resolve().parent
while _dir != _dir.parent:
    _env = _dir / '.env'
    if _env.exists():
        load_dotenv(dotenv_path=str(_env))
        break
    _dir = _dir.parent

# ═══ CENTRAL API KEY MANAGER ═══
GROQ_API_KEYS = [
    k for k in [
        os.getenv("GROQ_API_KEY_1"),
        os.getenv("GROQ_API_KEY_2"),
        os.getenv("GROQ_API_KEY_3"),
        os.getenv("GROQ_API_KEY"),
    ] if k is not None
]

_current_key_index = 0


def _mask_key(key: str) -> str:
    """Show only first 8 and last 4 chars of a key for safe logging."""
    if len(key) <= 12:
        return "****"
    return f"{key[:8]}...{key[-4:]}"


def get_current_api_key() -> str:
    """Return the currently active Groq API key."""
    global _current_key_index
    if not GROQ_API_KEYS:
        raise RuntimeError("No valid Groq API keys configured!")
    return GROQ_API_KEYS[_current_key_index % len(GROQ_API_KEYS)]


def rotate_api_key() -> str:
    """Rotate to the next API key and return it."""
    global _current_key_index
    if not GROQ_API_KEYS:
        raise RuntimeError("No valid Groq API keys configured!")
    _current_key_index = (_current_key_index + 1) % len(GROQ_API_KEYS)
    new_key = GROQ_API_KEYS[_current_key_index]
    logging.info("Rotated to Groq API key #%d (%s)", _current_key_index + 1, _mask_key(new_key))
    return new_key


def get_groq_client() -> Groq:
    """Create a Groq client using the current active API key.
    Logs which key is being used (masked)."""
    key = get_current_api_key()
    logging.info("Using Groq API key #%d (%s)", _current_key_index + 1, _mask_key(key))
    return Groq(api_key=key)


async def call_groq(
    messages: list,
    max_tokens: int = 500,
    model: str = "llama-3.3-70b-versatile",
) -> str:
    """Call Groq API with automatic key rotation on 429/403 errors.

    - Tries the current key first
    - On rate-limit (429) or permission (403): rotates to next key
    - If ALL keys exhausted: waits 60 seconds, resets to first key, retries
    - Returns the response text content
    """
    global _current_key_index

    total_keys = len(GROQ_API_KEYS)
    if total_keys == 0:
        raise RuntimeError("No valid Groq API keys configured!")

    attempts = 0
    start_index = _current_key_index

    while True:
        try:
            client = get_groq_client()
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            error_code = getattr(e, "status_code", None)
            if error_code in (429, 403):
                attempts += 1
                logging.warning(
                    "Groq API key #%d failed with %s. Attempt %d/%d.",
                    _current_key_index + 1, error_code, attempts, total_keys,
                )
                rotate_api_key()

                # All keys have been tried once → cooldown
                if attempts >= total_keys:
                    logging.warning(
                        "All %d Groq API keys exhausted. Waiting 60 seconds...",
                        total_keys,
                    )
                    await asyncio.sleep(60)
                    # Reset to first key and counter
                    _current_key_index = 0
                    attempts = 0
                    logging.info("Cooldown finished. Retrying from key #1 (%s)", _mask_key(GROQ_API_KEYS[0]))
            else:
                # Non-retryable error — re-raise immediately
                raise



SYSTEM_PROMPT = """You are Plan Reminder AI. You are a smart, friendly, and invisible productivity assistant.

CURRENT TIME: {current_time}, DATE: {current_date} (Tashkent UTC+5)
USER LANGUAGE: {language}

═══ RULE 1: ALWAYS RESPOND IN USER'S LANGUAGE ═══
Detect language from user's message. NEVER switch language mid-conversation.

═══ RULE 2: INTENT AND CONVERSATION FLOW (CRITICAL) ═══
- NATURAL CONVERSATION: If the user talks about off-topic things, briefly and warmly answer their question in 1 sentence, and smoothly transition back to productivity.
- AUTO-DETECT PLANNING: If a user wants to add tasks AND ALL TASKS HAVE A CLEAR TIME, you MUST immediately output a JSON block with action "propose_tasks". Do NOT ask for confirmation yourself.
- CORRECTIONS/EDITS: If the system tells you the user is editing a plan, output the UPDATED list of tasks using the "propose_tasks" JSON action again.

═══ RULE 3: THE PLANNING SEQUENCE ═══
Step 1: CLARIFY. If any tasks are missing times, you MUST NOT output JSON `propose_tasks`. Instead, simply ask the user what time they want to do the task.
Step 2: EXECUTE. Once you extract the tasks and EVERY task has a clear time, IMMEDIATELY output the JSON block with action "propose_tasks". 
CRITICAL LIMITATION: You MUST NOT write long explanations when outputting "propose_tasks"! Write exactly 1 short sentence (e.g. "Rejalar ro'yxatini shakllantirdim:") and then output the JSON.

═══ RULE 4: HANDLING UNEXPECTED FLOWS & IN-PROGRESS TASKS ═══
- If a message starts with `<system>SYSTEM INFO:...</system>`, read the context carefully. It means the user responded unexpectedly to a system prompt (like confirmation or picking a time).
- You have FULL FREEDOM to handle unstructured chat naturally!
- If the user writes "bajarilmoqda", "tugatdim", or "qilmayman", match it to the tasks in `Current Tasks Today` happening right around this time. Respond enthusiastically or naturally.
- If the user says "kechikdim" or "uxlab qolibman", acknowledge it empathetically and smoothly ask what new time they would like to reschedule the nearest task to.
- If the user needs help ("yordam ber"), provide intelligent advice based strictly on their current context/tasks.

═══ RULE 5: COMMUNICATION, LOGIC & GRAMMAR (CRITICAL) ═══
- Be extremely logical. Think carefully before answering. Ensure your response makes complete sense based on the user's tasks and context.
- Be FLAWLESS in grammar and spelling in the target language (especially Uzbek). Do NOT make spelling mistakes.
- Be SHORT, precise, simple, and direct. Do NOT write unnecessary long paragraphs. 1-2 short sentences max.
- Be polite, respectful, warm, and supportive (e.g. use "Siz", "Iltimos", "Rahmat").
- ALWAYS use emojis naturally, but keep it balanced (1-2 emojis per message max).

═══ RULE 6: APP CONTROL (OUTPUTTING JSON) ═══
You have full control over the user's tasks!
When you detect the user wants to plan or add tasks, YOU MUST output a JSON block at the very end of your response!
Action types: "propose_tasks", "delete_task", "mark_done".

Example for PROPOSING tasks to add (use 24h format for time):
"Rejalar ro'yxatini shakllantirdim:"
```json
{{
  "action": "propose_tasks",
  "data": [
    {{"title": "Ishga borish", "time": "09:00", "priority": "high"}}
  ]
}}
```

Example for DELETING a task:
"Vazifa o'chirildi."
```json
{{
  "action": "delete_task",
  "target_title": "Ishga borish"
}}
```

NEVER use JSON action "add_tasks" directly, ONLY use "propose_tasks", "delete_task", or "mark_done".

CONVERSATION HISTORY:
{history}
"""

async def generate_summary(done_tasks: List[str], undone_tasks: List[str], language: str) -> str:
    try:
        prompt = f"Generate a 1-2 sentence motivational summary in {language} about today's tasks. Done: {', '.join(done_tasks) if done_tasks else 'none'}. Undone: {', '.join(undone_tasks) if undone_tasks else 'none'}."
        return await call_groq(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
        )
    except Exception as e:
        logging.exception("generate_summary error: %s", e)
        return "Summary generation failed."

async def get_ai_response(
    user_message: str,
    language: str,
    history: List[Dict[str, str]],
    user_profile: Optional[Dict[str, Any]] = None,
) -> Union[str, Tuple[str, Dict[str, Any]]]:
    try:
        tashkent = timezone(timedelta(hours=5))
        now = datetime.now(tashkent)
        current_time = now.strftime("%H:%M")
        current_date = now.strftime("%d.%m.%Y, %A")
        profile_data = user_profile or {}
        current_interaction_count = int(profile_data.get("interaction_count", 0) or 0)
        history_text = ""
        for msg in history:
            role = "USER" if msg["role"] == "user" else "AI"
            history_text += f"{role}: {msg['content']}\n"

        style = profile_data.get("communication_style", "casual")
        traits = profile_data.get("personality", {}).get("personality_traits", [])
        habits = profile_data.get("habits", [])
        preferred_style = profile_data.get("personality", {}).get("preferred_response_style", "short")

        habit_context = ""
        if habits:
            habit_context = f"Known habits: {', '.join(habits[:5])}"

        personality_context = f"""
USER PROFILE:
- Style: {style} → match this style in responses
- Traits: {traits}
- {habit_context}
- Preferred response: {preferred_style}

PERSONALIZATION RULES:
- casual style → use informal language, contractions, friendly tone
- formal style → professional language, no slang
- high energy → match with energetic responses
- low energy → calm, supportive tone
- If user has habit "running" and it's morning → mention it naturally
- If user has habit "early wake" → acknowledge it positively
- Never mention you're analyzing them
- Make it feel natural, like a friend who knows them well
"""

        system_prompt = (
            SYSTEM_PROMPT.format(
                language=language, 
                current_time=current_time, 
                current_date=current_date, 
                history=history_text
            )
            + "\n\nUSER_INFO:\n"
            + f"- Name: {profile_data.get('username', 'User')}\n"
            + f"- Interaction count: {current_interaction_count}\n"
            + f"- Known traits: {json.dumps(profile_data.get('personality', {}), ensure_ascii=False, default=str)}\n"
            + f"- Topics discussed: {json.dumps(profile_data.get('topics_discussed', []), ensure_ascii=False, default=str)}\n"
            + (f"- Current Tasks Today: {json.dumps(profile_data.get('today_tasks', []), ensure_ascii=False, default=str)}\n" if profile_data.get('today_tasks') else "")
            + (f"- Future Planned Tasks: {json.dumps(profile_data.get('future_tasks', []), ensure_ascii=False, default=str)}\n" if profile_data.get('future_tasks') else "")
            + (f"- Active Plan Type: {profile_data.get('active_plan_type')}\n" if profile_data.get('active_plan_type') else "")
            + (f"- Current State: {profile_data.get('current_state', 'idle')}\n" if profile_data.get('current_state') and profile_data.get('current_state') != 'idle' else "")
            + (f"- Currently Building Plan For Date: {profile_data.get('building_date')}\n" if profile_data.get('building_date') else "")
            + "\n"
            + personality_context
        )

        messages = [{"role": "system", "content": system_prompt}]
        messages.append({"role": "user", "content": user_message})
        
        response_text = await call_groq(messages=messages, max_tokens=800)
        profile_updates = {
            "last_active": datetime.utcnow(),
            "interaction_count": current_interaction_count + 1,
        }
        if user_profile is None:
            return response_text
        return response_text, profile_updates
    except Exception as e:
        logging.exception("get_ai_response error: %s", e)
        fallback_text = "An error occurred while processing your request."
        if user_profile is None:
            return fallback_text
        return fallback_text, {
            "last_active": datetime.utcnow(),
            "interaction_count": int((user_profile or {}).get("interaction_count", 0) or 0) + 1,
        }

async def extract_tasks_from_schedule(schedule_text: str, language: str) -> str:
    """Extract tasks with times from schedule text and return as JSON string"""
    try:
        prompt = f"""Extract all tasks with their times from this schedule (language: {language}).
Return ONLY valid JSON array, nothing else:
[{{"time": "HH:MM", "title": "task name in original language"}}, ...]

Schedule: {schedule_text}

Return only the JSON array."""
        
        return await call_groq(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
        )
    except Exception as e:
        logging.exception("extract_tasks_from_schedule error: %s", e)
        return "[]"

async def extract_tasks_from_text(text: str, language: str, user_habits: list = None, plan_type: str = "daily") -> List[Dict[str, Any]]:
    if user_habits is None:
        user_habits = []
    try:
        habit_hint = ""
        if user_habits:
            habit_hint = f"User's known habits: {user_habits}. If relevant, suggest adding habit tasks."

        period_instructions = {
            "daily": "The user is planning for TODAY. Tasks usually just need 'time'.",
            "weekly": "The user is planning for this WEEK. Map 'tomorrow', 'tuesday', etc. to appropriate dates/days if needed, or simply list them out. Be extremely flexible for up to 7 days.",
            "monthly": "The user is planning for this MONTH. Handle dates spanning up to 30 days."
        }
        period_hint = period_instructions.get(plan_type, "")

        prompt = f"""STRICT TASK DETECTION ({plan_type.upper()} PLAN):
Before extracting tasks, check if the message is valid for task extraction.
Extract tasks ONLY if the message contains BOTH:
1. A clear action/verb (e.g., do, go, eat, run, meet, call)
2. AND either a time OR a date reference.

{period_hint}

If the message contains an action but NO time/date reference, do NOT extract the task. Return an empty array [], the bot will ask for the time separately.

Examples:
- "lets speak in english" → no task (return [])
- "hey whats up" → no task (return [])
- "soat 7 da yuguraman" → has action (yugurish) and time (7 da) → extract it
- "9ga ishga boraman" → has action (ishga borish) and time (9) → extract it
- "yuguraman" → action but no time → no task (return [])
- "meeting at 3pm" → action and time → extract it

Extract tasks from this text based on the rules. Fix any typos automatically.
{habit_hint}
Return ONLY a valid JSON array, nothing else:
[{{ "title": "task name", "time": "HH:MM", "priority": "high/normal/low", "is_recurring": false }}]

Rules:
- Fix ALL spelling and grammatical mistakes in task titles. Task titles must be flawlessly written in the target language.
- Keep task titles short, simple, concise, and clear. Avoid any unnecessary long words.
- If user says "har kuni" or "every day" → is_recurring: true
- Detect language and understand uz/ru/en input
- If any task is missing a time, return an empty array [] so the bot can ask for the time later.

Text: {text}"""

        result = await call_groq(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )
        result = result.replace("```json", "").replace("```", "").strip()
        tasks = json.loads(result)
        if isinstance(tasks, list):
            def parse_time(t_str):
                if not t_str: return 9999 # Push to end if no time
                try:
                    h, m = map(int, t_str.split(':'))
                    return h * 60 + m
                except:
                    return 9999
            
            tasks.sort(key=lambda x: parse_time(x.get("time")))
            return tasks
        return []
    except Exception as e:
        logging.exception("extract_tasks_from_text error: %s", e)
        return []

async def extract_monthly_dates_and_tasks(text: str, language: str) -> Dict[str, List[Dict[str, Any]]]:
    try:
        tashkent_now = datetime.now(timezone(timedelta(hours=5)))
        today_date_str = tashkent_now.date().isoformat()
        
        prompt = f"""STRICT MONTHLY CALENDAR EXTRACTION:
The user is providing a list of important dates and tasks for their monthly plan.
Today's date is: {today_date_str}. Assume all dates are in the current or upcoming month.

Extract all tasks and group them by EXACT ISO dates (YYYY-MM-DD).

Return ONLY a valid JSON dictionary mapping dates to arrays of tasks. Do not return any other enclosing text, just pure JSON.
Example Response:
{{
  "2026-05-15": [{{ "title": "doktorga borish", "time": "14:00", "priority": "normal", "is_recurring": false }}],
  "2026-05-20": [{{ "title": "hamkorlar bilan uchrashuv", "time": null, "priority": "high", "is_recurring": false }}]
}}

Rules:
- Calculate absolute YYYY-MM-DD for any relative dates (e.g., "15-may", "keyingi payshanba").
- Fix spelling mistakes in task titles.
- Time should be "HH:MM" or null.
- Priority: "high", "normal", "low".

User Text: {text}"""

        result = await call_groq(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
        )
        result = result.replace("```json", "").replace("```", "").strip()
        data = json.loads(result)
        if isinstance(data, dict):
            def parse_time(t_str):
                if not t_str: return 9999
                try:
                    h, m = map(int, t_str.split(':'))
                    return h * 60 + m
                except:
                    return 9999
            
            for date_key, task_list in data.items():
                if isinstance(task_list, list):
                    task_list.sort(key=lambda x: parse_time(x.get("time")))
            return data
        return {}
    except Exception as e:
        logging.exception("extract_monthly_dates_and_tasks error: %s", e)
        return {}

async def generate_evening_report(
    done_tasks: list,
    skipped_tasks: list,
    extra_work: str,
    extra_notes: str,
    productivity: int,
    diff: int,
    language: str
) -> str:
    
    prompt = f"""You are a productivity coach. Generate a short, motivating daily summary.

Data:
- Completed tasks: {done_tasks}
- Skipped tasks: {skipped_tasks}
- Extra work done: {extra_work}
- Additional notes: {extra_notes}
- Productivity: {productivity}%
- Change from yesterday: {diff:+}%

Rules:
- Language: {language} (uz=Uzbek, ru=Russian, en=English)
- Max 3 sentences
- Be honest but encouraging
- If productivity > 80%: celebrate with energy
- If productivity 50-80%: acknowledge effort, suggest improvement
- If productivity < 50%: be kind, find positives, motivate for tomorrow
- Mention something specific from their tasks (not generic)
- End with one powerful sentence about tomorrow
- Use 1-2 emojis max, not excessive"""

    try:
        return await call_groq(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )
    except Exception as e:
        logging.error(f"generate_evening_report error: {e}")
        fallback = {
            "uz": "Bugun ham harakat qildingiz — bu muhim! Ertaga yanada kuchliroq bo'lasiz 💪",
            "ru": "Сегодня вы старались — это важно! Завтра будет ещё лучше 💪",
            "en": "You made effort today — that matters! Tomorrow will be even better 💪"
        }
        return fallback.get(language, fallback["uz"])

async def analyze_user_personality(
    telegram_id: int,
    message: str,
    language: str,
    current_profile: dict
) -> dict:
    
    existing = current_profile.get("personality", {})
    habits = current_profile.get("habits", [])
    count = current_profile.get("interaction_count", 0)
    
    # Only analyze every 5 interactions to save tokens
    if count % 5 != 0:
        return {}
    
    prompt = f"""Analyze this user message and update their profile.
Current profile: {existing}
Known habits: {habits}
Message: "{message}"
Language: {language}

Return ONLY valid JSON, nothing else:
{{
  "communication_style": "formal/casual/mixed",
  "energy_level": "high/medium/low",
  "personality_traits": ["trait1", "trait2"],
  "detected_habits": ["habit1"],
  "segment": "new/active/power_user",
  "preferred_response_style": "short/detailed/motivational"
}}

Rules:
- Only include fields you can detect from this message
- If not enough data, keep existing values
- detected_habits: only clear recurring patterns (running, early wake, etc.)"""

    try:
        result = await call_groq(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
        )
        result = result.replace("```json","").replace("```","").strip()
        return json.loads(result)
    except:
        return {}


