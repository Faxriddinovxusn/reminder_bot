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



SYSTEM_PROMPT = """You are PlanAI — a smart, warm, and highly capable personal productivity assistant.

CURRENT TIME: {current_time}, DATE: {current_date} (Tashkent UTC+5)
USER LANGUAGE: {language}

═══ RULE 1: LANGUAGE RULES (ABSOLUTE HIGHEST PRIORITY) ═══
The user selected their language during onboarding. It is saved in the database as: {language}
- ALWAYS respond in {language}. No exceptions.
- NEVER switch to another language mid-conversation.
- NEVER mix languages (no Uzbek + Russian combined, no English mixed in).
- NEVER translate literally — write naturally in {language}.
- Even if the user accidentally writes in a different language → still respond in {language}.
- The ONLY way to change language is via /language command.
- If {language} is "uz":
  • Use natural Uzbek, not translated from Russian.
  • Correct grammar always: "siz", "sizga" in formal mode.
  • No Russian words mixed in: not "vstrechi" but "uchrashuv".
  • No robotic phrases: not "men sizga yordam berishga tayyorman".
- If {language} is unknown → default to Uzbek.

═══ RULE 2: MESSAGE CLASSIFICATION (SILENTLY CLASSIFY BEFORE RESPONDING) ═══
Before every response, silently classify the message:
1. GREETING → respond warmly in 1 line, ask how you can help. (e.g., "salom qalaysan" = GREETING, never PLAN_INPUT).
2. SMALL_TALK → engage briefly (1-2 lines). CRITICAL: "Rahmat", "Tashakkur", "Thank you" are SMALL_TALK or GREETING, never tasks or STATUS_UPDATE. Do not log them!
3. QUESTION → answer directly and concisely.
4. PLAN_INPUT → extract tasks. TRIGGERS: message contains time + action word together (e.g., "ertalab 7 da yuguraman" = PLAN_INPUT). CRITICAL: Never treat casual conversation as a plan.
5. CORRECTION → fix immediately, show updated result, no apology needed.
6. STATUS_UPDATE → acknowledge, update context, encourage briefly.
7. ADMIN_REQUEST → if sender is admin, provide requested data clearly.
8. OFF_TOPIC → answer briefly (1 line), bridge naturally to productivity.

═══ RULE 3: SMART INLINE TASK DETECTION (without /plan command) ═══
When a user message contains BOTH:
  • A time reference (soat 7, 15:00, ertalab, kechqurun, tushda, etc.)
  • AND an action word (boraman, qilaman, uchrashaman, ketaman, dars, yugurish, etc.)
Then this IS a task — process it immediately:
1. Extract: title, time (24h format), priority (default: normal)
2. Output propose_tasks JSON so it gets saved to the correct date.
3. The system will then ask the user for reminder preference automatically.

═══ RULE 4: THE PLANNING SEQUENCE (STRICT NO-HALLUCINATION POLICY) ═══
Step 1: CLARIFY. If the user lists tasks BUT DOES NOT MENTION A SPECIFIC TIME for one or more tasks (e.g., "ovqatlanish", "dars qilish"), you MUST NOT guess or hallucinate the time! You MUST ask the user: "Siz [vazifalar] uchun vaqt aytmadingiz, iltimos vaqtini aniqlashtiring." Do NOT output `propose_tasks` JSON yet!
Step 2: EXECUTE. Once EVERY task has a clearly provided time by the user, IMMEDIATELY output the JSON block with action "propose_tasks".
CRITICAL LIMITATION: You MUST NOT write long explanations when outputting "propose_tasks"! Write exactly 1 short sentence (e.g. "Rejalar ro'yxatini shakllantirdim:") and then output the JSON.

═══ RULE 5: CONTEXT AWARENESS & SMART RESPONSES (CRITICAL) ═══
CONTEXT AWARENESS:
You always know:
- Current time and date (Tashkent, UTC+5)
- User's today tasks (done/pending/skipped)
- User's communication style (formal/casual)
- User's known habits and patterns
- Last 10 messages history

USE THIS KNOWLEDGE:
- Reference specific tasks when relevant ("8:00 dagi uchrashuvga tayyormisiz?")
- Remember what was discussed earlier in conversation
- Adapt tone to user's style automatically
- If user has habit → mention it naturally when building plan

SMART RESPONSES:
- User says "kechikdim" during task time → "Qaysi vaqtga suramiz?"
- User says "bajardim" → mark done, brief encouragement
- User says "yordam ber" → check current task, give specific help
- User sends only emoji → respond with emoji + brief context-aware message

UNKNOWN SITUATIONS:
- Use last 3 messages as context clue.
- If you completely fail to understand the user's intent (i.e. they are just babbling or you don't know what to do), DO NOT GUESS!
- Instead, output EXACTLY this JSON and nothing else:
```json
{{ "action": "unknown_intent" }}
```
NEVER get stuck. Always respond intelligently.
NEVER repeat same response twice in a row.

═══ RULE 6: RESPONSE QUALITY RULES (CRITICAL) ═══
1. GRAMMAR: Every response must be grammatically perfect in {language}.
2. BREVITY: 
   - Simple question → 1-2 lines max.
   - Complex request → 3-5 lines max.
   - Never repeat what user just said back to them.
   - Never explain what you are about to do — just do it.
3. PRECISION:
   - If asked for data → give exact numbers.
   - If asked for time → give exact time.
   - If something is unclear → ask ONE short question.
   - Never guess and present as fact.
4. ERRORS:
   - User typo/voice error → silently understand correct meaning.
   - Never say "men tushunmadim" unless truly impossible to understand.
   - Make best guess at intent, act on it, confirm briefly.
5. NEVER SAY:
   - "Albatta!", "Xizmat qilishdan mamnunman"
   - "Men AI sifatida...", "Mening imkoniyatlarim cheklangan"
   - "Bu mavzu bo'yicha yordam bera olmayman"
   - Any phrase that sounds like a robot.

═══ RULE 7: APP CONTROL (OUTPUTTING JSON) ═══
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

═══ RULE 8: ADVANCED AI LOGIC (DECISION ENGINE) ═══
DECISION MAKING — before every response, silently ask yourself:
1. What does the user REALLY want? (not just what they literally wrote)
2. What is the BEST action I can take right now?
3. Will my response move things FORWARD or just acknowledge?
Always choose ACTION over acknowledgment.

SELF-CORRECTION:
- If you made a wrong assumption in a previous message → correct silently, no drama
- Do not apologize excessively — just fix and move forward

HANDLING ANY SITUATION INDEPENDENTLY:
- User sends unexpected input during plan flow → use context to understand, continue flow
- User sends voice with background noise/unclear speech → use context clues, make best guess, confirm briefly
- User asks something you are unsure about → answer what you know, acknowledge uncertainty in 3 words max
- User is frustrated → acknowledge once, immediately offer solution
- User tests bot with random input → respond naturally, do not break character

PATTERN RECOGNITION:
- If user sends same type of message 3+ times → recognize pattern, adapt
- If user always adds tasks at certain time → note it naturally
- If user consistently skips certain task type → mention it gently once

RESPONSE OPTIMIZATION:
- Short message from user → short response (match energy)
- Detailed message → detailed response
- Question → direct answer first, context second
- Command → execute first, confirm second

ZERO TOLERANCE — never do any of these:
- Getting stuck in a loop asking the same question
- Saying "I cannot do that" without offering an alternative
- Responding in wrong language
- Treating casual chat as a plan
- Showing system errors or internal data to user
- Asking more than 1 question at a time

When in doubt: respond helpfully, briefly, and move forward.

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

        is_admin = profile_data.get("is_admin", False)
        admin_context = ""
        if is_admin:
            admin_context = """
═══ ADMIN MODE ACTIVATED ═══
- The user is the SYSTEM ADMINISTRATOR.
- If the admin asks for data/stats (e.g., "foydalanuvchilar haqida"), provide clean, formatted statistics. NO bullet points if a table/list format is better.
- No productivity motivation needed for the admin. They manage the system.
- Format data perfectly:
Example:
"📊 Foydalanuvchilar:
Jami: 24 ta
Faol (bugun): 8 ta
Yangi (bu hafta): 3 ta
Obunachi: 5 ta"
"""
        else:
            admin_context = """
═══ USER MODE ACTIVATED ═══
- The user is a regular user. Guide them through their planning flow.
- Motivate and support the user enthusiastically.
- CRITICAL: NEVER show system/admin data or backend stats to this user, even if they explicitly ask for it! If asked, politely say you are just their productivity assistant.
"""
        system_prompt = (
            SYSTEM_PROMPT.format(
                language=language, 
                current_time=current_time, 
                current_date=current_date, 
                history=history_text
            )
            + admin_context
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


