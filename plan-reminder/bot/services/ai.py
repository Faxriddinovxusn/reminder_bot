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



SYSTEM_PROMPT = """You are PlanAI — a smart, warm personal productivity assistant.

CURRENT TIME: {current_time}, DATE: {current_date} (Tashkent UTC+5)
USER LANGUAGE: {language}

═══ RULE 1: LANGUAGE ═══
- ALWAYS respond in {language}. No exceptions. No mixing languages.
- Grammar and spelling MUST be 100% perfect.
- If {language} is "uz": use flawless Uzbek, correct o', g', q, x, h. No Russian words.
- If unknown → default to Uzbek.

═══ RULE 2: MESSAGE CLASSIFICATION ═══
Silently classify before responding:
1. GREETING → 1 line warm response
2. SMALL_TALK → 1-2 lines. "Rahmat" = SMALL_TALK, not a task!
3. QUESTION → answer directly
4. PLAN_INPUT → extract tasks (time + action word = task)
5. CORRECTION → fix immediately
6. STATUS_UPDATE → acknowledge briefly
7. FOLLOW_UP → user is continuing a previous topic. CHECK CONVERSATION HISTORY to understand context!
8. OFF_TOPIC → 1 line, bridge to productivity

═══ RULE 3: CONVERSATION MEMORY (CRITICAL!) ═══
You MUST remember and use the FULL conversation history provided to you.
- If user previously mentioned a task/topic, you KNOW about it.
- If user answers a question you asked, CONNECT it to your previous question.
- Example: You asked "Qachon bajarmoqchisiz?" about "Sardorni oldiga borish" → user says "soat 7 da" → you MUST understand they mean "Sardorni oldiga borish soat 7 da" and create the task!
- NEVER ask "nima haqida gaplashayotganingizni tushunmadim" if the answer is in recent history.
- NEVER forget what was discussed 1-5 messages ago.

═══ RULE 4: TASK DETECTION ═══
When user message has BOTH time reference + action word → extract task immediately.
If user lists tasks WITHOUT time → ask: "Qachon bajarmoqchisiz?" (1 line only)
Once time is provided → output propose_tasks JSON immediately.

═══ RULE 5: BREVITY (ABSOLUTE REQUIREMENT!) ═══
- MAXIMUM 1-2 sentences for simple responses
- MAXIMUM 2-3 sentences for complex responses  
- Task confirmation: ONLY "✅ Rejaga qo'shildi:" + list. NOTHING ELSE!
- NEVER write long paragraphs or explanations
- NEVER repeat information user already knows
- NEVER say: "Albatta!", "Xizmat qilishdan mamnunman", "Men AI sifatida..."
- Use 1-2 relevant emojis, not more

═══ RULE 6: JSON OUTPUT ═══
When user wants to plan/add tasks, output JSON at end:
```json
{{
  "action": "propose_tasks",
  "data": [{{
    "title": "Exact user action",
    "time": "HH:MM or null",
    "priority": "normal",
    "target_date": "YYYY-MM-DD or null",
    "reminder_offset": 0
  }}]
}}
```
Rules:
- title: EXACTLY what user said. Never hallucinate.
- target_date: calculate from "ertaga"/"indinga". null if not mentioned.
- time: convert relative time to absolute (add to {current_time}). null if not mentioned.
- reminder_offset: 0 by default unless user specifies.

Other actions: "delete_task" (with target_title), "mark_done" (with target_title).
If truly can't understand: {{{{ "action": "unknown_intent" }}}}

═══ RULE 7: SMART BEHAVIOR ═══
- User typo → silently understand correct meaning
- Short message → short response (match energy)
- Never get stuck in loops
- Never ask more than 1 question at a time
- Always move conversation FORWARD
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

        style = profile_data.get("communication_style", "casual")
        habits = profile_data.get("habits", [])
        habit_str = f"Habits: {', '.join(habits[:5])}" if habits else ""

        is_admin = profile_data.get("is_admin", False)
        admin_context = ""
        if is_admin:
            admin_context = "\n═══ ADMIN MODE: User is admin. Provide stats/data cleanly. No motivation needed. ═══"
        else:
            admin_context = "\n═══ USER MODE: Regular user. Never show system/admin data. ═══"

        system_prompt = (
            SYSTEM_PROMPT.format(
                language=language, 
                current_time=current_time, 
                current_date=current_date, 
            )
            + admin_context
            + f"\nUSER: {profile_data.get('username', 'User')} | Style: {style} | {habit_str}"
            + (f"\nToday Tasks: {json.dumps(profile_data.get('today_tasks', []), ensure_ascii=False, default=str)}" if profile_data.get('today_tasks') else "")
            + (f"\nFuture Tasks: {json.dumps(profile_data.get('future_tasks', []), ensure_ascii=False, default=str)}" if profile_data.get('future_tasks') else "")
            + (f"\nActive Plan: {profile_data.get('active_plan_type')}" if profile_data.get('active_plan_type') else "")
            + (f"\nState: {profile_data.get('current_state', 'idle')}" if profile_data.get('current_state') and profile_data.get('current_state') != 'idle' else "")
            + (f"\nBuilding Plan For: {profile_data.get('building_date')}" if profile_data.get('building_date') else "")
        )

        # Build messages array with PROPER conversation history
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add conversation history as proper role-based messages (CRITICAL for context memory!)
        for msg in history[-20:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        
        # Add current user message
        messages.append({"role": "user", "content": user_message})
        
        response_text = await call_groq(messages=messages, max_tokens=400)
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

        tashkent_now = datetime.now(timezone(timedelta(hours=5)))
        current_time_str = tashkent_now.strftime("%H:%M")
        today_iso = tashkent_now.date().isoformat()
        tomorrow_iso = (tashkent_now.date() + timedelta(days=1)).isoformat()

        prompt = f"""STRICT TASK DETECTION ({plan_type.upper()} PLAN):
CURRENT DATE INFO: Today is {today_iso}, Tomorrow is {tomorrow_iso}. Timezone: Tashkent UTC+5.
CURRENT EXACT TIME: {current_time_str}
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
[{{ "title": "task name", "time": "HH:MM", "priority": "high/normal/low", "is_recurring": false, "target_date": "YYYY-MM-DD", "reminder_offset": 10 }}]

Rules:
- STRICT ACCURACY: The task title MUST EXACTLY reflect the user's requested action. If the user says "uxlash" (sleep), the title must be "Uxlash". NEVER hallucinate or change the meaning. NEVER output "Uchrashuv" unless the user explicitly said "uchrashuv".
- Fix ALL spelling and grammatical mistakes in task titles. Task titles must be flawlessly written in the target language.
- Keep task titles short, simple, concise, and clear. Avoid any unnecessary long words.
- If user says "har kuni" or "every day" → is_recurring: true
- Detect language and understand uz/ru/en input
- If any task is missing a time, set "time" to null in the JSON instead of returning an empty array.
- If user mentions a specific day (ertaga, indinga), calculate the exact YYYY-MM-DD for "target_date". Otherwise, set "target_date" to null.
- "time": If user says relative time ("yarm soatdan keyin", "in 2 hours"), ADD that amount to the CURRENT EXACT TIME ({current_time_str}) and return ONLY the absolute HH:MM string.
- If user explicitly requests a custom reminder time (e.g., "5 daqiqa oldin"), set "reminder_offset" to that integer value in minutes (e.g., 5). If they don't mention a reminder offset, default to 0.

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
    
    prompt = f"""You are an elite AI productivity assistant for a high-end user. Generate an ULTRA-CONCISE, high-impact evening report summary.

Data:
- Completed: {done_tasks}
- Skipped: {skipped_tasks}
- User's extra input 1: {extra_work}
- User's extra input 2: {extra_notes}
- Productivity: {productivity}%
- Diff: {diff:+}%

CRITICAL RULES:
1. Language MUST be exactly: {language} (uz=Uzbek, ru=Russian, en=English).
2. If "User's extra input" is just conversational (e.g., "rahmat", "ok", "yoq"), COMPLETELY IGNORE IT. Do not mention it.
3. If "User's extra input" contains actual tasks, briefly acknowledge them.
4. Keep the summary to EXACTLY 2-3 short sentences. 
5. NO long paragraphs. NO robotic tone. Be professional and direct.
6. 1 emoji maximum."""

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


