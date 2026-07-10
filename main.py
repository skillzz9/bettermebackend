import csv
import json
import os
from pathlib import Path
from datetime import datetime

import anthropic
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import store

load_dotenv()
store.init_db()

# ---------------------------------------------------------------------------
# Habits catalogue — loaded once at startup from the CSV
# ---------------------------------------------------------------------------

_HABITS_CSV = Path(__file__).parent / "data" / "habits.csv"

def _load_habits() -> list[dict]:
    with open(_HABITS_CSV) as f:
        return list(csv.DictReader(f))

HABITS: list[dict] = _load_habits()
HABITS_BY_ID: dict[int, dict] = {int(h["id"]): h for h in HABITS}

HABITS_CONTEXT = "\n".join(
    f"id={h['id']} | {h['pillar']} | {h['category']} | {h['habit']} | "
    f"level={h['level']} | {h['minutes']}min | cue: {h['cue']} | identity: {h['identity']}"
    for h in HABITS
)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

ADAM_SYSTEM_PROMPT = """You are Adam, a warm, gentle, and encouraging companion for the "Better Me" habit app. You are like a kind, patient friend who genuinely believes in the user — never a drill sergeant, never a hustle-culture coach.

WHO YOU'RE TALKING TO:
- Many users feel low, tired, unmotivated, overwhelmed, or down on themselves. They have often tried and "failed" before and carry shame about it.
- Meet them exactly where they are. On a hard day, showing up at all is a win.
- Never use grindset, hustle, "lock in", "no excuses", "discipline", or tough-love language. Warmth and permission, always.

CORE PHILOSOPHY:
- 1% better every day. Progress, not perfection. Tiny gentle actions quietly compound.
- The enemy is pressure and friction, not the person. If a habit feels hard, it's too big — make it smaller.
- Something tiny always beats nothing.

DISCOVERY — THREE QUESTIONS (ask ONE per message, warmly react to each answer before moving on, skip anything they've already told you):
1. FOCUS AREAS: What are some aspects they are trying to improve on in their life right now? (This helps determine which pillars to focus on).
2. PAST ATTEMPTS: What habits have they tried in the past that didn't quite stick? (This tells you exactly what they want so you can eventually offer an easier, micro version of it).
3. THE WHY / OBSTACLES: What do they feel got in the way or stopped them from achieving those habits? (This learns their behavior, energy levels, and psychological blockers).

After you have their three answers, you will be shown a list of habits — wait for that step and do not jump ahead to suggesting specific habits yet.

BEHAVIORAL RULES:
1. Keep responses under 3 sentences, conversational and warm.
2. Plain text only — no markdown, no asterisks, no bullet points, no headings.
3. Warm, curious, and human — like a caring friend, not a life coach.
4. Catch their name in conversation if offered, or ask lightly in your opening.
5. If someone sounds seriously distressed or mentions self-harm, respond with genuine warmth, gently suggest speaking with someone they trust or a professional helpline, and do not try to "fix" it with a habit."""

ADAM_GREETING = (
    "Hey, I'm Adam — it's really good to meet you. "
    "Before we talk about habits, I'd love to understand you a little. "
    "What are some aspects you are trying to improve on in your life right now?"
)

ONBOARDING_QUESTIONS = 3

# ---------------------------------------------------------------------------
# Initial habit suggestion (from CSV, after 4 onboarding questions)
# ---------------------------------------------------------------------------

HABIT_SUGGESTION_SYSTEM = """You are Adam, a warm habit coach designing the first set of starting habits for this person.

You have their full onboarding conversation and everything you remember about them (profile and past memories). Create 3 to 5 habits that YOU genuinely believe THIS specific person can stick to. The CSV list below is guidance and inspiration only — invent your own habits, adapt ones from the list, or ignore it entirely when you can do better for this person.

STICKABILITY IS THE ONLY GOAL:
A modest habit they keep beats an impressive one they drop. Judge every candidate habit against what you actually know about them:
- Their focus areas: what did they say they want to improve? Every habit should trace back to one of these.
- Their past attempts: what did they try before that didn't stick? Offer a much smaller, easier micro version of the thing they already wanted — that's the habit they're most motivated for.
- Their obstacles: what got in the way before (time, energy, motivation, chaos)? Do not suggest anything that would fail for the exact same reason. Design around the obstacle, not through it.
- Their memories and profile: use their daily rhythm, lifestyle, personality, and anything they've mentioned in past conversations to make each habit feel made for them.

HOW TO CREATE:
- If they sound low-energy, overwhelmed, or have a history of giving up, keep every habit tiny (2-5 minutes). Do not go bigger.
- Each habit should anchor naturally to a real moment already in their day (their cue) — ideally one they actually mentioned.
- The identity statement should feel personal and aspirational.
- Use their own words and specifics where you can, so the habits feel heard, not generated.

WRITE:
A warm, brief reply (2-3 sentences, plain text, no markdown) that:
- Acknowledges what they shared with genuine warmth
- Gently bridges into the habit suggestions

Available inspiration from CSV:
{habits_context}

Return JSON with:
- reply: your 2-3 sentence message
- final_habits: array of habit objects, each with id (string like "h1", "h2"...), habit (the name), cue (when/trigger), pillar (health/career/social/spiritual), identity (who they're becoming), minutes (integer, how many minutes)"""

HABIT_SUGGESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "final_habits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "habit": {"type": "string"},
                    "cue": {"type": "string"},
                    "pillar": {"type": "string"},
                    "identity": {"type": "string"},
                    "minutes": {"type": "integer"},
                },
                "required": ["id", "habit", "cue", "pillar", "identity", "minutes"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["reply", "final_habits"],
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Gap-fill phase (up to 3 follow-up questions after initial habit selection)
# ---------------------------------------------------------------------------

GAP_FILL_SYSTEM = """You are Adam, a warm habit coach. The user has just selected their first set of habits and you've acknowledged them. You now ask three follow-up questions — ONE per message, in this exact order — to fill the gaps you need to create a truly personalised daily plan at the end:

1. DAILY LIFE: "What does a typical day look like for you right now?" You need this to anchor their new habits to existing routines.
2. WHAT THEY ENJOY: What is one thing they really enjoy doing? This tells you what genuinely energises them, so you can build habits around it or pair harder habits with something they love.
3. MORNING OR EVENING: Are they a morning person or an evening person? This tells you when their habits should live in their day.

You already have the full conversation — if they've clearly already answered one of these, don't repeat it; briefly confirm what you know instead and move to the next question.

Be warm and brief — 1-2 warm sentences reacting to what they said, then ONE focused question."""

# ---------------------------------------------------------------------------
# Daily Chat System (Standard Check-in)
# ---------------------------------------------------------------------------

DAILY_CHAT_OPENER_SYSTEM = """You are Adam, a warm, gentle habit coach. You are opening a daily check-in conversation with someone you know well.

Look at everything you know about this person — their name, their memories, their habits, what was completed today and what wasn't, and when things were last done. Write ONE opening message that:
- Greets them warmly and personally (use their name if you know it)
- References something specific from what you see — a habit they completed, one that hasn't been done in a few days, or something you remember about them
- Ends with ONE focused question to kick off the check-in (don't ask "how did it go generally" — pick the most interesting thing you noticed and ask about that specifically)

Keep it under 3 sentences. Plain text, no markdown. Warm, curious, personal — not generic."""

DAILY_CHAT_SYSTEM = """You are Adam, a warm, gentle, and highly analytical habit coach. The user has already completed their onboarding and set up their habits.

You are having an end-of-day daily check-in.
Your ULTIMATE GOAL is to act as a behavioral detective. You must understand EXACTLY why the user succeeded or failed today so you can map their psychological patterns and friction points.

WHAT YOU HAVE:
- Everything you remember about them (their profile and memories from past conversations).
- Today's habit checklist: what was completed and what wasn't, each habit's size in minutes, and the exact times things were checked off.
- For habits NOT done today: when each was last checked off (or if it's never been done at all).

HOW TO DIAGNOSE:
Read the checklist like a behavioral detective.
- If they completed EVERYTHING (100%): Ask what the secret sauce was today. Was it their energy level? The time of day? Try to find the exact condition that led to success so we can replicate it.
- If they completed SOME things (Partial): Isolate the friction point. Did they run out of energy? Was a specific habit too long (e.g., 10 mins instead of 2 mins)? Was the cue wrong?
- If they completed NOTHING (0%): Identify the systemic blocker without inducing guilt. Were they completely exhausted? Did their schedule blow up?
- Use the completion TIMES as evidence: if everything gets done in the morning but evening habits keep slipping, or a habit only ever happens late at night, say what you notice and work with their real rhythm.

ADJUSTING DIFFICULTY:
- If a habit hasn't been checked off in several days (or never), it is too big or badly anchored — don't lecture, gently propose shrinking it (fewer minutes, an easier version, or a better cue) and ask if that would feel more doable.
- If a habit gets completed consistently and reliably, celebrate it and occasionally offer a small level-up (a couple more minutes or a slightly bigger version) — only ever as an invitation, never pressure.

RULES:
1. Do NOT just say 'good job' and move on. You must ask ONE focused, diagnostic question to figure out the "WHY".
2. If they manually added or deleted a habit today (check recent memories), gently ask them why they made that change.
3. Keep your responses under 3 sentences, conversational, and plain text. Always be supportive, never judgmental."""

# ---------------------------------------------------------------------------
# Final habit suggestion (free-form, custom habits after gap-fill)
# ---------------------------------------------------------------------------

FINAL_HABITS_SYSTEM = """You are Adam, a warm habit coach. You now know this person well from the full conversation. Design a personalised daily habit plan for them.

You are NOT limited to any preset list. Create habits from scratch that feel truly tailored to THIS specific person — their schedule, goals, struggles, personality, and what they enjoy. Make them feel like they were made just for them.

Guidelines:
- Create 3 to 6 habits total
- Mix across the life areas they actually care about (health, career, social, spiritual)
- Each habit must be small, specific, and anchored to a real moment they mentioned in their day
- Keep level 1-2 difficulty (small and sustainable) unless they clearly have energy for more
- Use their own language where possible and reference their specific situation
- The cue should anchor to something real they mentioned (their morning coffee, commute, lunch break, bedtime, etc.)
- The identity statement should feel personal and aspirational in a warm way

Return JSON with:
- reply: warm 2-3 sentence message introducing the habits (plain text, no markdown)
- final_habits: array of habit objects, each with id (string like "h1", "h2"...), habit (the name), cue (when/trigger), pillar (health/career/social/spiritual), identity (who they're becoming), minutes (integer, how many minutes)"""

FINAL_HABITS_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "final_habits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "habit": {"type": "string"},
                    "cue": {"type": "string"},
                    "pillar": {"type": "string"},
                    "identity": {"type": "string"},
                    "minutes": {"type": "integer"},
                },
                "required": ["id", "habit", "cue", "pillar", "identity", "minutes"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["reply", "final_habits"],
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Reply suggestion chips
# ---------------------------------------------------------------------------

SUGGESTION_SYSTEM = (
    "You generate 3 short suggestion replies a user might send to Adam, a warm habit coach. "
    "Read the recent conversation and write 3 diverse, realistic short responses to Adam's latest question. "
    "Each must be 3-7 words, casual and natural, covering different perspectives — "
    "e.g. one optimistic, one honest about struggles, one uncertain or vague. "
    "Think about what different real people might actually say. "
    "Return JSON with a 'suggestions' array of exactly 3 strings."
)

SUGGESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Long-term memory helpers
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM = (
    "You maintain a compact long-term memory bank for a habit-coaching app. "
    "Your job is to keep memories lean and precise — update existing ones rather than creating new ones wherever possible. "
    "Only create a brand-new memory when the fact is genuinely not covered by any existing memory. "
    "\n\nYou will be given:\n"
    "- EXISTING MEMORIES: a list of {id, text} pairs already stored\n"
    "- LATEST EXCHANGE: the conversation turn to learn from\n"
    "\nYour output:\n"
    "1. profile_updates — update any of these scalar fields if the exchange reveals something new or more precise: "
    "name, timezone, focus_area (health/career/social/spiritual), communication_style, lifestyle (daily rhythm/schedule/time available), motivation (their why), "
    "and these arrays: goals, traits (personality), obstacles (what gets in the way). "
    "Return null/empty for anything unchanged.\n"
    "2. memory_updates — list of {id, text} where id is an existing memory ID and text is the improved, more precise version. "
    "Use this when the exchange adds detail or corrects something already known.\n"
    "3. new_memories — only facts that are genuinely new and not covered by any existing memory. "
    "Ignore small talk, one-off moods, and anything fleeting. Be strict — if in doubt, skip it."
)

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "profile_updates": {
            "type": "object",
            "properties": {
                "name": {"type": ["string", "null"]},
                "timezone": {"type": ["string", "null"]},
                "focus_area": {"type": ["string", "null"]},
                "communication_style": {"type": ["string", "null"]},
                "lifestyle": {"type": ["string", "null"]},
                "motivation": {"type": ["string", "null"]},
                "goals": {"type": "array", "items": {"type": "string"}},
                "traits": {"type": "array", "items": {"type": "string"}},
                "obstacles": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "name", "timezone", "focus_area", "communication_style",
                "lifestyle", "motivation", "goals", "traits", "obstacles",
            ],
            "additionalProperties": False,
        },
        "memory_updates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["id", "text"],
                "additionalProperties": False,
            },
        },
        "new_memories": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["profile_updates", "memory_updates", "new_memories"],
    "additionalProperties": False,
}


def user_today(profile: dict) -> str:
    """Return today's date string in the user's local timezone."""
    tz_name = profile.get("timezone") or "UTC"
    try:
        user_tz = zoneinfo.ZoneInfo(tz_name)
        return datetime.now(user_tz).strftime("%Y-%m-%d")
    except Exception:
        return user_today(profile)

def build_memory_context(profile: dict, memories: list[str], habits: list[dict] = None, completed_today: dict = None, last_completions: dict = None) -> str:
    lines: list[str] = []
    labels = {
        "name": "Name",
        "timezone": "Timezone",
        "focus_area": "What they want to grow",
        "motivation": "Why this matters to them now",
        "lifestyle": "Their daily rhythm / time available",
        "communication_style": "How they like to be spoken to",
    }
    for key, label in labels.items():
        if profile.get(key):
            lines.append(f"- {label}: {profile[key]}")
    if profile.get("goals"):
        lines.append("- Goals: " + ", ".join(profile["goals"]))
    if profile.get("traits"):
        lines.append("- Personality: " + ", ".join(profile["traits"]))
    if profile.get("obstacles"):
        lines.append("- What's gotten in the way before: " + ", ".join(profile["obstacles"]))
    if habits:
        completed_today = completed_today or {}
        last_completions = last_completions or {}
        now = datetime.now()
        habit_status = []
        for h in habits:
            name = h.get("habit_name") or h.get("habit", "")
            minutes = h.get("minutes")
            if minutes:
                name = f"{name} ({minutes} min)"
            h_id = h.get("id")
            if h_id in completed_today:
                comp_time = completed_today[h_id]
                if comp_time:
                    t_str = datetime.fromtimestamp(comp_time).strftime('%I:%M %p')
                    habit_status.append(f"[x] {name} (Completed today at {t_str})")
                else:
                    habit_status.append(f"[x] {name} (Completed today)")
            else:
                last_ts = last_completions.get(h_id)
                if last_ts:
                    last_dt = datetime.fromtimestamp(last_ts)
                    days_ago = (now.date() - last_dt.date()).days
                    when = "yesterday" if days_ago == 1 else f"{days_ago} days ago"
                    habit_status.append(
                        f"[ ] {name} (Not completed today; last checked off {when} at {last_dt.strftime('%I:%M %p')})"
                    )
                else:
                    habit_status.append(f"[ ] {name} (Not completed today; never checked off yet)")
        lines.append("- Current Active Habits:\n  " + "\n  ".join(habit_status))

    if not lines and not memories:
        return (
            "\n\nThis is your first real conversation with this person. You don't "
            "know them yet — be curious and get to know them."
        )

    out = (
        "\n\nWHAT YOU ALREADY KNOW ABOUT THIS PERSON. Speak to them like you "
        "genuinely remember them and weave this in naturally — never recite it "
        "back as a list or say 'according to my notes':"
    )
    if lines:
        out += "\n" + "\n".join(lines)
    if memories:
        out += "\nThings they've told you in past conversations:\n" + "\n".join(
            f"- {m}" for m in memories
        )
    return out


def extract_and_store(user_id: str, user_text: str, adam_text: str) -> None:
    profile = store.get_profile(user_id)
    existing = store.get_memories_with_ids(user_id, 100)
    existing_str = "\n".join(f"[{m['id']}] {m['text']}" for m in existing) or "(none yet)"
    conversation = (
        f"EXISTING MEMORIES:\n{existing_str}\n\n"
        f"LATEST EXCHANGE:\nUser: {user_text}\nAdam: {adam_text}"
    )
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=800,
            system=EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": conversation}],
            output_config={"format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA}},
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        data = json.loads(text)
    except Exception as exc:
        print(f"[memory] extraction failed: {exc}")
        return

    store.save_profile(user_id, store.merge_profile(profile, data.get("profile_updates") or {}))

    for upd in data.get("memory_updates") or []:
        if upd.get("id") and upd.get("text"):
            store.update_memory(user_id, upd["id"], upd["text"])
            print(f"[memory] updated {upd['id']} for {user_id}: {upd['text']}")

    added = store.add_memories(user_id, data.get("new_memories") or [])
    if added:
        print(f"[memory] +{len(added)} new fact(s) for {user_id}: {added}")

# ---------------------------------------------------------------------------
# Claude helpers
# ---------------------------------------------------------------------------

def generate_suggestions(history: list[dict]) -> list[str]:
    recent = history[-6:]
    convo = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in recent)
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            system=SUGGESTION_SYSTEM,
            messages=[{"role": "user", "content": convo}],
            output_config={"format": {"type": "json_schema", "schema": SUGGESTION_SCHEMA}},
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        data = json.loads(text)
        return (data.get("suggestions") or [])[:3]
    except Exception as exc:
        print(f"[suggestions] generation failed: {exc}")
        return []


def suggest_initial_habits(user_id: str, history: list[dict]) -> tuple[str, list[dict]]:
    profile = store.get_profile(user_id)
    memories = store.get_memories(user_id)
    habits = store.get_user_habits(user_id)
    completed_today = store.get_completed_info(user_id, user_today(profile))
    memory_ctx = build_memory_context(profile, memories, habits, completed_today).strip()
    convo_text = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in history)
    system = HABIT_SUGGESTION_SYSTEM.format(habits_context=HABITS_CONTEXT)
    if memory_ctx:
        system += f"\n\nWhat you already know about this person:\n{memory_ctx}"
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1000,
        system=system,
        messages=[{"role": "user", "content": f"Conversation so far:\n{convo_text}"}],
        output_config={"format": {"type": "json_schema", "schema": HABIT_SUGGESTION_SCHEMA}},
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(text)
    reply = data["reply"]
    return reply, data.get("final_habits", [])


def generate_final_habits(user_id: str, history: list[dict]) -> tuple[str, list[dict]]:
    profile = store.get_profile(user_id)
    memories = store.get_memories(user_id)
    habits = store.get_user_habits(user_id)
    completed_today = store.get_completed_info(user_id, user_today(profile))
    memory_ctx = build_memory_context(profile, memories, habits, completed_today).strip()
    convo_text = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in history)
    system = FINAL_HABITS_SYSTEM
    if memory_ctx:
        system += f"\n\nWhat you already know about this person:\n{memory_ctx}"
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1200,
        system=system,
        messages=[{"role": "user", "content": f"Full conversation:\n{convo_text}"}],
        output_config={"format": {"type": "json_schema", "schema": FINAL_HABITS_SCHEMA}},
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(text)
    return data["reply"], data.get("final_habits", [])

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
import asyncio
import httpx
from datetime import datetime, timezone as dt_timezone
import zoneinfo

app = FastAPI(title="Better Me — Adam Coach API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def notification_worker():
    while True:
        try:
            now_utc = datetime.now(dt_timezone.utc)

            users_ref = store._db().collection("users").stream()
            for doc in users_ref:
                data = doc.to_dict()
                settings = data.get("settings", {})

                push_enabled = settings.get("pushEnabled", True)
                check_in_time = settings.get("checkInTime", "08:00 AM")
                push_token = settings.get("pushToken")
                tz_name = settings.get("timezone", "UTC")

                if not push_enabled:
                    continue

                try:
                    user_tz = zoneinfo.ZoneInfo(tz_name)
                    user_now = now_utc.astimezone(user_tz)
                except Exception:
                    user_now = now_utc

                user_date = user_now.strftime("%Y-%m-%d")

                # Parse the stored check-in time
                try:
                    target = datetime.strptime(check_in_time, "%I:%M %p")
                    target_hm = (target.hour, target.minute)
                except ValueError:
                    continue

                current_hm = (user_now.hour, user_now.minute)

                # Midnight reset: clear reflectionDue from previous day
                last_reflection_date = settings.get("last_notified_date")
                if last_reflection_date and last_reflection_date != user_date and settings.get("reflectionDue"):
                    settings["reflectionDue"] = False
                    store.save_settings(doc.id, settings)
                    print(f"[notify] Cleared stale reflectionDue for {doc.id} (was {last_reflection_date}, now {user_date})")

                # Persist last_notified_date in Firestore so restarts don't lose state
                already_sent = settings.get("last_notified_date") == user_date

                if current_hm == target_hm and not already_sent:
                    settings["reflectionDue"] = True
                    settings["last_notified_date"] = user_date
                    store.save_settings(doc.id, settings)
                    print(f"[notify] reflectionDue set for {doc.id} at {user_now.strftime('%I:%M %p')} ({tz_name}) | push_token={'yes' if push_token else 'MISSING'}")

                    if push_token:
                        message = {
                            "to": push_token,
                            "sound": "default",
                            "title": "Adam is waiting! 👋",
                            "body": "It's time for your daily check-in. Let's see how your habits went today.",
                            "data": {"type": "daily_checkin"},
                        }
                        async with httpx.AsyncClient() as http_client:
                            resp = await http_client.post(
                                "https://exp.host/--/api/v2/push/send",
                                json=message,
                                headers={"Accept": "application/json", "Content-Type": "application/json"},
                            )
                        print(f"[notify] Push sent to {doc.id}: {resp.status_code} {resp.text[:120]}")

        except Exception as e:
            print(f"[notify] Error in notification worker: {e}")

        # Wake up 2 seconds before the next minute so we never miss the boundary
        sleep_secs = max(1, 58 - datetime.now().second)
        await asyncio.sleep(sleep_secs)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(notification_worker())

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    user_id: str = "local-dev-user"
    # "onboarding" | "request_habits" | "gap_fill" | "final_habits"
    phase: str = "onboarding"


class CsvHabit(BaseModel):
    id: int
    pillar: str
    category: str
    habit: str
    level: int
    minutes: int
    cue: str
    identity: str


class FinalHabit(BaseModel):
    id: str
    habit: str
    cue: str
    pillar: str
    identity: str
    minutes: int


class ChatResponse(BaseModel):
    reply: str
    suggested_habits: list[CsvHabit] | None = None
    final_habits: list[FinalHabit] | None = None
    suggestions: list[str] | None = None


class SaveHabitsRequest(BaseModel):
    user_id: str = "local-dev-user"
    habits: list[dict]


class CompleteHabitRequest(BaseModel):
    user_id: str = "local-dev-user"
    habit_id: str
    date: str  # YYYY-MM-DD
    completed: bool = True

class AddHabitRequest(BaseModel):
    user_id: str = "local-dev-user"
    habit: dict

class DeleteHabitRequest(BaseModel):
    user_id: str = "local-dev-user"
    habit_id: str
    habit_name: str

class SettingsRequest(BaseModel):
    user_id: str
    settings: dict

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, background_tasks: BackgroundTasks) -> ChatResponse:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set")

    profile = store.get_profile(request.user_id)
    memories = store.get_memories(request.user_id)

    history: list[dict] = [{"role": "assistant", "content": ADAM_GREETING}]
    for m in request.messages:
        if m.role not in ("user", "assistant"):
            raise HTTPException(status_code=400, detail=f"Invalid role: {m.role}")
        history.append({"role": m.role, "content": m.content})

    last_user = next(
        (m.content for m in reversed(request.messages) if m.role == "user"), ""
    )

    # --- Phase: suggest initial habits from CSV ---
    if request.phase == "request_habits":
        try:
            reply, raw_habits = suggest_initial_habits(request.user_id, history)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Habit suggestion error: {exc}") from exc
        if last_user and reply:
            background_tasks.add_task(extract_and_store, request.user_id, last_user, reply)
        final_models = [
            FinalHabit(
                id=h["id"], habit=h["habit"], cue=h["cue"],
                pillar=h["pillar"], identity=h["identity"], minutes=int(h["minutes"]),
            )
            for h in raw_habits
        ]
        return ChatResponse(reply=reply, final_habits=final_models)

    # --- Phase: final custom habits ---
    if request.phase == "final_habits":
        try:
            reply, raw_final = generate_final_habits(request.user_id, history)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Final habit error: {exc}") from exc
        if last_user and reply:
            background_tasks.add_task(extract_and_store, request.user_id, last_user, reply)
        final_models = [
            FinalHabit(
                id=h["id"], habit=h["habit"], cue=h["cue"],
                pillar=h["pillar"], identity=h["identity"], minutes=int(h["minutes"]),
            )
            for h in raw_final
        ]
        return ChatResponse(reply=reply, final_habits=final_models)

    # --- Phase: gap_fill (follow-up questions after initial habit selection) ---
    if request.phase == "gap_fill":
        habits = store.get_user_habits(request.user_id)
        completed_today = store.get_completed_info(request.user_id, user_today(profile))
        system = GAP_FILL_SYSTEM + build_memory_context(profile, memories, habits, completed_today)
        try:
            response = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=512,
                system=system,
                messages=history,
            )
        except anthropic.APIError as exc:
            import traceback
            traceback.print_exc()
            print(f"Exception details: {exc}")
            raise HTTPException(status_code=502, detail=f"Claude API error: {exc}") from exc
        reply = next((b.text for b in response.content if b.type == "text"), "")
        if last_user and reply:
            background_tasks.add_task(extract_and_store, request.user_id, last_user, reply)
        suggestions = generate_suggestions(history + [{"role": "assistant", "content": reply}])
        return ChatResponse(reply=reply, suggestions=suggestions or None)

    # --- Phase: daily_chat ---
    if request.phase == "daily_chat":
        habits = store.get_user_habits(request.user_id)
        completed_today = store.get_completed_info(request.user_id, user_today(profile))
        last_completions = store.get_last_completions(request.user_id)
        memory_ctx = build_memory_context(profile, memories, habits, completed_today, last_completions)

        # No user messages yet — generate a personalized opening
        if not last_user:
            system = DAILY_CHAT_OPENER_SYSTEM + "\n" + memory_ctx
            try:
                response = client.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=256,
                    system=system,
                    messages=[{"role": "user", "content": "Start the check-in."}],
                )
            except anthropic.APIError as exc:
                raise HTTPException(status_code=502, detail=f"Claude API error: {exc}") from exc
            reply = next((b.text for b in response.content if b.type == "text"), "")
            return ChatResponse(reply=reply)

        system = DAILY_CHAT_SYSTEM + "\n" + memory_ctx
        try:
            response = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=512,
                system=system,
                messages=history,
            )
        except anthropic.APIError as exc:
            raise HTTPException(status_code=502, detail=f"Claude API error: {exc}") from exc
        reply = next((b.text for b in response.content if b.type == "text"), "")
        if last_user and reply:
            background_tasks.add_task(extract_and_store, request.user_id, last_user, reply)
        suggestions = generate_suggestions(history + [{"role": "assistant", "content": reply}])
        return ChatResponse(reply=reply, suggestions=suggestions or None)

    # --- Phase: onboarding (default) ---
    habits = store.get_user_habits(request.user_id)
    completed_today = store.get_completed_info(request.user_id, user_today(profile))
    system = ADAM_SYSTEM_PROMPT + build_memory_context(profile, memories, habits, completed_today)
    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1024,
            system=system,
            messages=history,
        )
    except anthropic.APIError as exc:
        raise HTTPException(status_code=502, detail=f"Claude API error: {exc}") from exc
    reply = next((b.text for b in response.content if b.type == "text"), "")
    if last_user and reply:
        background_tasks.add_task(extract_and_store, request.user_id, last_user, reply)
    suggestions = generate_suggestions(history + [{"role": "assistant", "content": reply}])
    return ChatResponse(reply=reply, suggestions=suggestions or None)


@app.post("/habits/save")
def save_habits(req: SaveHabitsRequest) -> dict:
    saved = store.save_user_habits(req.user_id, req.habits)
    return {"saved": len(saved), "habits": saved}


@app.post("/habits/complete")
def complete_habit(req: CompleteHabitRequest) -> dict:
    new_streak = store.set_completion(req.user_id, req.habit_id, req.date, req.completed)
    return {"streak": new_streak}

@app.post("/habits/add")
def add_habit(req: AddHabitRequest) -> dict:
    new_habit = store.add_user_habit(req.user_id, req.habit)
    return {"added": bool(new_habit), "habit": new_habit}

@app.post("/habits/delete")
def delete_habit(req: DeleteHabitRequest) -> dict:
    store.delete_user_habit(req.user_id, req.habit_id, req.habit_name)
    return {"deleted": True}

@app.get("/settings/{user_id}")
def get_settings_endpoint(user_id: str) -> dict:
    return store.get_settings(user_id)

@app.post("/settings")
def update_settings(req: SettingsRequest) -> dict:
    store.save_settings(req.user_id, req.settings)
    return {"status": "ok"}

@app.get("/dashboard/{user_id}")
def get_dashboard(user_id: str, date: str = Query(...)) -> dict:
    habits = store.get_user_habits(user_id)
    completed = store.get_completed_ids(user_id, date)
    streak = store.get_streak(user_id)["current_streak"]
    settings = store.get_settings(user_id)
    
    return {
        "habits": [
            {**h, "completed_today": h["id"] in completed}
            for h in habits
        ],
        "streak": streak,
        "reflectionDue": settings.get("reflectionDue", False),
    }
