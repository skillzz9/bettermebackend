import csv
import json
import os
import random
import re
from pathlib import Path
from datetime import datetime, timedelta

import json_repair
from strength import calculate_strength_scores

def parse_llm_json(text: str) -> dict:
    text = text.strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json_repair.loads(match.group(0))
        except Exception as e:
            print(f"[JSON Parse Error] json_repair.loads failed: {e}")
            print(f"[JSON Parse Error] Text block was: {match.group(0)[:200]}...")
    else:
        print(f"[JSON Parse Error] No curly braces block found in text: {text[:200]}...")
    return {}

import anthropic
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import store

load_dotenv()
store.init_db()

# ---------------------------------------------------------------------------
# Onboarding question variants (hardcoded, slight wording variation per user)
# ---------------------------------------------------------------------------

_Q1_VARIANTS = [
    "1. MAIN GOAL: Ask them what their primary fitness or nutrition goal is right now (e.g., building muscle, losing fat, improving endurance, or just feeling healthier).",
    "1. MAIN GOAL: Ask them what they are trying to achieve with their body or health at the moment, like gaining strength or dropping some body fat.",
    "1. MAIN GOAL: Ask them to describe their biggest fitness goal right now. What are they working towards?",
]

_Q2_VARIANTS = [
    "2. CURRENT BASELINE: Ask them what their current activity level and diet look like. Are they already going to the gym, or starting from scratch?",
    "2. CURRENT BASELINE: Ask them to give you a quick snapshot of how often they work out right now and how they usually eat.",
    "2. CURRENT BASELINE: Ask them about their current routine. How active are they, and do they track their food?",
]

_Q3_VARIANTS = [
    "3. OBSTACLES: Ask them what has made it hard for them to stick to a fitness routine or nutrition plan in the past. Time, fatigue, lack of knowledge?",
    "3. OBSTACLES: Ask them what usually trips them up when they try to get in shape. Do they run out of energy, get injured, or just lose motivation?",
    "3. OBSTACLES: Ask them what the biggest roadblock has been in their fitness journey so far.",
]


def _pick_questions() -> tuple[str, str, str]:
    return random.choice(_Q1_VARIANTS), random.choice(_Q2_VARIANTS), random.choice(_Q3_VARIANTS)

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

HUGO_SYSTEM_PROMPT = """Do NOT use bold text (**) or em dashes (—) anywhere in your response. You are Hugo, an empathetic, highly intelligent AI personal trainer and nutrition coach. You are talking to a new client to build their fitness plan.

WHO YOU'RE TALKING TO:
- Users want to get in shape but often struggle with consistency, fatigue, or knowing what to do.
- Be encouraging, confident, and empathetic. You are a world-class coach.

THE DYNAMIC ONBOARDING FLOW:
You must guide the user through these steps. Ask ONE question per message. Wait for their response before moving on.

0. MANUAL IMPORT FLOW: If the very first message in the chat history is you asking for their age, height, weight, gender, and name to IMPORT a program, wait for them to provide those stats. Once they do, simply respond with exactly: "Okay, let's get started!" and ask no further questions.

1. WAIT for the user to respond to your greeting (where you asked for their dream physique photo).
   - If they uploaded a photo, say: "Awesome. To map out the exact path to get there, would you be comfortable uploading a photo of your current physique so I can see your starting point? Please also tell me your current weight and height."
   - If they say they don't have one OR say they don't care about physique, ask: "No problem. What is your primary fitness goal right now?"
2. When they answer the fitness goal, THEN ask for their current physique, weight, and height.
3. Once they provide their current physique/stats, ask: "Are you currently going to the gym, or have you gone in the past?"
4. BRANCH BASED ON GYM HISTORY:
   - If they currently go: "Awesome that you're already going. What do you feel is the weakest link holding you back right now?"
   - If they've gone in the past: "That's okay! Everyone quits and comes back stronger. What made you quit?"
   - If they've never been: "What is your biggest worry you have about starting?"
5. AFTER THEY ANSWER THE BRANCH QUESTION FROM STEP 4:
   - If they went in the past, ask: "How long has your break been?"
   - If they currently go or have never been, SKIP this step and go directly to Step 6.
6. "How many times a week are you willing to spend exercising? Any time spent is worth it!"
7. "How long can a gym session be for you to fit into your schedule?"
8. "What does your diet and nutrition look like?"
9. "Last thing! I would like to know your name, age, and gender."
10. Once they provide their final stats, analyze their dream physique (if provided) and their current starting point. Tell them what the focus will be. AT THE END OF YOUR RESPONSE, you MUST say exactly: "Okay, let's get started!" DO NOT ask any more questions.

CRITICAL JSON OUTPUT:
You MUST output your response as a JSON object containing:
- `reply`: your conversational text response.
- `suggestions`: an array of 3-4 short predefined answers they can click for the NEXT question you just asked. (Leave empty if asking for their physical stats or photo).
- `ui_action`: a string indicating a specific UI state. Options:
  - `"asking_current_physique"`: use this when you ask for their current physique.
  - `"ready_to_start"`: use this ONLY when you say 'Okay, let's get started!'
  - `null`: for all other questions.
"""
ONBOARDING_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "suggestions": {"type": "array", "items": {"type": "string"}},
        "ui_action": {"type": ["string", "null"]}
    },
    "required": ["reply", "suggestions", "ui_action"],
    "additionalProperties": False,
}

HUGO_GREETING = (
    "Hey, I'm Hugo — your AI fitness coach. "
    "Please upload a photo of your dream physique so I know exactly what we are aiming for."
)

ONBOARDING_QUESTIONS = 9

# ---------------------------------------------------------------------------
# Initial habit suggestion (from CSV, after 4 onboarding questions)
# ---------------------------------------------------------------------------

HABIT_SUGGESTION_SYSTEM = """Do NOT use bold text (**) or em dashes (—) anywhere in your response. You are Hugo, an elite AI fitness coach designing the first set of daily targets for this person.

You have their full onboarding conversation and everything you remember about them (profile and past memories). Create 3 to 5 targets that YOU genuinely believe THIS specific person can stick to. The CSV list below is guidance and inspiration only — invent your own targets, adapt ones from the list, or ignore it entirely when you can do better for this person.

STICKABILITY IS THE ONLY GOAL:
A modest target they keep beats an impressive one they drop. Judge every candidate against what you actually know about them:
- Their focus areas: what did they say they want to improve? Every target should trace back to one of these.
- Their past attempts: what did they try before that didn't stick? Offer a much smaller, easier micro version of the thing they already wanted.
- Their obstacles: what got in the way before (time, energy, motivation, chaos)? Do not suggest anything that would fail for the exact same reason.
- Their memories and profile: use their daily rhythm, lifestyle, personality, and anything they've mentioned in past conversations.

HOW TO CREATE:
- If they sound low-energy, overwhelmed, or have a history of giving up, keep every target tiny (2-5 minutes). Do not go bigger.
- Each target should anchor naturally to a real moment already in their day (their cue).
- The identity statement should feel personal and aspirational.
- Use their own words and specifics where you can, so the targets feel heard, not generated.

WRITE:
A warm, brief reply (2-3 sentences, plain text, no markdown) that:
- Acknowledges what they shared with genuine warmth (including their physique photo if provided)
- Gently bridges into the target suggestions
- If they uploaded a photo, analyze the physique and suggest exercises in the `suggested_exercises` array. If no photo, leave it empty.

Available inspiration from CSV:
{habits_context}

Return JSON with:
- reply: your 2-3 sentence message
- final_habits: array of habit objects, each with id (string like "h1", "h2"...), habit (the name), cue (when/trigger), pillar (health/career/social/spiritual), identity (who they're becoming), minutes (integer, how many minutes)
- suggested_exercises: array of exercise objects (can be empty) if you are suggesting exercises based on their physique photo."""

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
        "suggested_exercises": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "reason": {"type": "string", "description": "Why they should do this (MAX 5 WORDS)"},
                    "target_muscle": {"type": "string"},
                    "reps": {"type": "string"},
                    "sets": {"type": "string"},
                    "recommended_split": {"type": "string", "description": "e.g., Push Day, Leg Day"},
                },
                "required": ["name", "reason", "target_muscle", "reps", "sets", "recommended_split"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["reply", "final_habits"],
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Workout Generation Phase
# ---------------------------------------------------------------------------

WORKOUT_GENERATION_SYSTEM = """Do NOT use bold text (**) or em dashes (—) anywhere in your response. You are Hugo, an elite AI fitness coach.
Your task is to generate a highly customized Push/Pull/Legs workout plan for the user based strictly on the provided Global Fitness Laws.

FOLLOW THESE LAWS EXACTLY:
{fitness_laws}

USER PROFILE & MEMORIES:
{memory_context}

INSTRUCTIONS:
1. Calculate the gap between their current and dream physique to choose their accessory movements.
2. If they train 5-6 days, add an Arm Day. If they want to get shredded, add Spin Bike cardio.
3. Set the target weight for all exercises to 0 for Week 1 (Calibration Week).
4. Output a strict JSON structure containing the program.

JSON SCHEMA:
Return a JSON object with:
- "reply": A 2-3 sentence encouraging message telling them their custom plan is ready.
- "workout_plan": The program structure containing "program_name" and "days".
  Each day in "days" must have "day_name" and "exercises".
  Each exercise must have "name", "sets" (int), "reps" (str), "target_weight_lbs" (int, always 0), "is_primary" (bool), and "rationale" (str).
"""

WORKOUT_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "workout_plan": {
            "type": "object",
            "properties": {
                "program_name": {"type": "string"},
                "days": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "day_name": {"type": "string"},
                            "exercises": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "sets": {"type": "integer"},
                                        "reps": {"type": "string"},
                                        "target_weight_lbs": {"type": "integer"},
                                        "is_primary": {"type": "boolean"},
                                        "rationale": {"type": "string"}
                                    },
                                    "required": ["name", "sets", "reps", "target_weight_lbs", "is_primary", "rationale"],
                                    "additionalProperties": False
                                }
                            }
                        },
                        "required": ["day_name", "exercises"],
                        "additionalProperties": False
                    }
                }
            },
            "required": ["program_name", "days"],
            "additionalProperties": False
        }
    },
    "required": ["reply", "workout_plan"],
    "additionalProperties": False
}

def build_workout_plan(user_id: str, history: list[dict], model: str) -> tuple[str, dict]:
    profile = store.get_profile(user_id)
    memories = store.get_memories(user_id)
    
    memory_ctx = build_memory_context(profile, memories, [], {})
    
    fitness_laws = ""
    laws_path = Path(__file__).parent / "fitness_laws.md"
    if laws_path.exists():
        with open(laws_path, "r") as f:
            fitness_laws = f.read()

    system_prompt = WORKOUT_GENERATION_SYSTEM.replace("{fitness_laws}", fitness_laws).replace("{memory_context}", memory_ctx)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=system_prompt,
            messages=history
        )
    except Exception as exc:
        print(f"Claude API error: {exc}")
        return "I had trouble generating your workout.", {}

    text = next((b.text for b in response.content if b.type == "text"), "")
    data = parse_llm_json(text)
    if not data:
        return "I had trouble formatting your workout.", {}
        
    return data.get("reply", ""), data.get("workout_plan", {})

# ---------------------------------------------------------------------------
# Gap-fill phase (up to 3 follow-up questions after initial habit selection)
# ---------------------------------------------------------------------------

GAP_FILL_SYSTEM = """Do NOT use bold text (**) or em dashes (—) anywhere in your response. You are Hugo, an elite AI fitness coach. The user has just selected their baseline targets. You now ask three follow-up questions — ONE per message, in this exact order — to finalize their programming:

1. GYM ACCESS: "Do you have access to a full gym, or are you working out at home? What equipment do you have?"
2. DIETARY RESTRICTIONS: "Do you have any food allergies, dietary restrictions, or foods you absolutely hate?"
3. SCHEDULE: "How many days a week can you realistically dedicate to training, and how long do you have for each session?"

You already have the full conversation — if they've clearly already answered one of these, don't repeat it; briefly confirm what you know instead and move to the next question.

Be confident and brief. 1-2 sentences reacting, then ONE focused question. Plain text only, no markdown."""

# ---------------------------------------------------------------------------
# Daily Chat System (Standard Check-in)
# ---------------------------------------------------------------------------

DAILY_CHAT_OPENER_SYSTEM = """Do NOT use bold text (**) or em dashes (—) anywhere in your response. You are Hugo, an empathetic AI fitness coach. You are opening a daily check-in conversation.

Look at everything you know about this person — their macros, their workouts, what was completed today and what wasn't, and their behavioral/fatigue log. Write ONE opening message that:
- Greets them warmly
- References their workout or nutrition from today (e.g., "I see you checked off your leg day" or "Noticed you missed your protein target")
- Ends with ONE focused question about how their body feels or why they missed a target (e.g., "How are your hamstrings feeling after those squats?" or "Did work run late today, causing you to miss the gym?")

Keep it under 3 sentences. Plain text only. Warm, analytical, and supportive."""

DAILY_CHAT_SYSTEM = """Do NOT use bold text (**) or em dashes (—) anywhere in your response. You are Hugo, an elite, empathetic AI fitness coach. The user is doing their daily check-in.

Your ULTIMATE GOAL is to act as a dynamic coach. You must understand how they felt during the workout, their fatigue levels, and their nutrition adherence, so you can adapt their plan.

WHAT YOU HAVE:
- Their profile, goals, and memories.
- Today's checklist (workouts, macros, sleep).
- A BEHAVIOR LOG (their past fatigue and adherence history).

HOW TO DIAGNOSE & ADAPT:
- If they hit all targets: Ask how the weight felt. Was it too easy? Should we increase volume/weight tomorrow?
- If they failed reps or feel exhausted: Be empathetic. "Let's lower the volume tomorrow" or "I'm bumping your carbs up by 30g to help you recover."
- If they skipped due to schedule: Adapt the plan. "No stress, we'll shift the workout to tomorrow."
- Always listen to their biofeedback (joint pain, sleepiness, hunger).

RULES:
1. If they are doing a normal check-in, ask ONE focused question to understand their biofeedback or friction. 
2. IF THEY ASK A DIRECT QUESTION (e.g. about a specific physique, how to build a muscle, or general advice), DO NOT ask any check-in questions or mention skipped workouts. Just answer their question directly and helpfully.
3. If they are fatigued or plateauing, actively tell them you are adjusting their plan (e.g., reducing weight, increasing food). YOU MUST DO THIS BY INCLUDING A "proposal" IN YOUR JSON! Do NOT just say you will do it; you must propose the actual change.
4. Keep your responses under 3 sentences (unless answering a complex question), conversational, and plain text only. No markdown. Always be supportive, never judgmental.
5. If you feel it's highly beneficial to add or swap an exercise based on their feedback, include them in the `suggested_exercises` JSON array. Otherwise, leave it empty.
6. IF AND ONLY IF a change to their macros (calories) would help, you can output a `proposal` block. BUT ensure it doesn't deviate from their goals (e.g. do not put them in a calorie surplus if they want to lose fat).
  - action: "update_calories"
  - title: "Update Daily Calories"
  - description: e.g. "Bumping by 200 to break your plateau"
  - oldValue: "2400 kcal"
  - newValue: "2600 kcal"
  - status: "pending"
  - payload: {"calories": 2600}

7. You MUST also provide an array of 3 quick-reply `suggestions` for the user (short, natural responses the user could tap to reply to your message).

You must return a JSON object containing `reply` and `suggestions`. You MAY optionally include `suggested_exercises` and a `proposal` object."""

EXERCISE_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "suggestions": {
            "type": "array",
            "items": {"type": "string"}
        },
        "suggested_exercises": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "reason": {"type": "string", "description": "Why they should do this (MAX 5 WORDS)"},
                    "target_muscle": {"type": "string"},
                    "recommended_split": {"type": "string"},
                    "sets": {"type": "string"},
                    "reps": {"type": "string"},
                },
                "required": ["name", "reason", "target_muscle", "recommended_split", "sets", "reps"],
                "additionalProperties": False,
            }
        },
        "proposal": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "action": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "oldValue": {"type": "string"},
                "newValue": {"type": "string"},
                "status": {"type": "string"},
                "payload": {
                    "type": "object",
                    "properties": {
                        "calories": {"type": "integer"}
                    },
                    "additionalProperties": False
                }
            },
            "required": ["id", "action", "title", "description", "status", "payload"],
            "additionalProperties": False
        }
    },
    "required": ["reply"],
    "additionalProperties": False,
}

PHYSIQUE_ANALYSIS_SYSTEM = """Do NOT use bold text (**) or em dashes (—) anywhere in your response. You are Hugo, an elite AI fitness coach. The user has uploaded an image or asked about a specific physique.
Do NOT do a daily check-in. Do NOT ask them about their workouts or why they skipped anything. 

Your ONLY job is to:
1. Analyze the main features of the physique (e.g., wide shoulders, thick back, defined chest).
2. Explain briefly how the user can build those exact features.
3. You MUST provide 3 to 5 exercise suggestions in the `suggested_exercises` JSON array that target those features.

You must return a JSON object containing `reply` (your text response) and `suggested_exercises`."""

# ---------------------------------------------------------------------------
# Final habit suggestion (free-form, custom habits after gap-fill)
# ---------------------------------------------------------------------------

FINAL_HABITS_SYSTEM = """Do NOT use bold text (**) or em dashes (—) anywhere in your response. You are Hugo, an elite AI fitness coach. You now know this person well from the full conversation. Design a personalised daily fitness and nutrition plan for them.

You are NOT limited to any preset list. Create targets from scratch that feel truly tailored to THIS specific person — their schedule, goals, struggles, personality, and what they enjoy. Make them feel like they were made just for them.

Guidelines:
- Create 3 to 6 daily targets total
- Mix across the life areas they actually care about (workouts, nutrition, steps, sleep, recovery)
- Each target must be small, specific, and anchored to a real moment they mentioned in their day
- Keep level 1-2 difficulty (small and sustainable) unless they clearly have energy for more
- Use their own language where possible and reference their specific situation
- The cue should anchor to something real they mentioned (their morning coffee, commute, lunch break, bedtime, etc.)
- The identity statement should feel personal and aspirational in a warm way

Return JSON with:
- reply: warm 2-3 sentence message introducing the targets (plain text, no markdown)
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
    "You generate 3 short suggestion replies a user might send to Hugo, an elite AI fitness coach. "
    "Read the recent conversation and write 3 diverse, realistic short responses to Hugo's latest question. "
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
    "You maintain the long-term memory of Hugo, an elite AI fitness coach. Hugo's single most important goal is to "
    "understand WHY this user succeeds or fails at their fitness targets, and to build a perfect profile of them. "
    "Keep memories lean and precise — update existing ones rather than creating new ones wherever possible.\n\n"
    "WHAT TO SAVE IN MEMORIES (durable facts that will matter in 6+ months):\n"
    "- Recurring struggles or psychological patterns (e.g. 'skips gym when stressed at work')\n"
    "- Strong preferences and dislikes (e.g. 'hates cardio', 'loves heavy compound lifts')\n"
    "- Commitments the user explicitly made\n"
    "- Key insight about WHY something works or fails for them\n"
    "- Important life context that shapes their schedule or habits\n"
    "- Their dream physique description if provided\n\n"
    "WHAT NEVER TO STORE IN MEMORIES:\n"
    "- Height, weight, age, gender — these go in settings_updates only, never memories\n"
    "- Current stats or measurements — stored in settings, not memories\n"
    "- Pure small talk with no behavioral signal\n"
    "- Inferred personality labels — only record traits the user explicitly expressed\n"
    "- Anything already captured in the profile fields (goals, focus_area, obstacles, motivation)\n\n"
    "Your output:\n"
    "1. settings_updates — ONLY if the user explicitly states their height, weight, age, or gender.\n"
    "2. profile_updates — update scalar fields (name, timezone, focus_area, communication_style, lifestyle, motivation) "
    "and arrays (goals, traits, obstacles). Only add to arrays if genuinely new — no rephrasing of existing entries. "
    "traits should only be things the user explicitly said about themselves, not your inferences.\n"
    "3. memory_updates — list of {id, text} to strengthen or correct existing memories.\n"
    "4. new_memories — genuinely new durable insights from the SAVE list above. Be selective — fewer, better memories beat many weak ones.\n"
    "5. behavior_events — ONLY during daily_chat: one entry per habit whose outcome the user explained TODAY."
)

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "settings_updates": {
            "type": "object",
            "properties": {
                "height": {"type": ["string", "null"]},
                "weight": {"type": ["string", "null"]},
                "age": {"type": ["integer", "null"]},
                "gender": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
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
                "current_physique_features": {"type": "array", "items": {"type": "string"}},
                "dream_physique_features": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "name", "timezone", "focus_area", "communication_style",
                "lifestyle", "motivation", "goals", "traits", "obstacles",
                "current_physique_features", "dream_physique_features"
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
        "behavior_events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "habit_name": {"type": ["string", "null"]},
                    "outcome": {"type": "string", "enum": ["completed", "skipped", "struggled"]},
                    "reason": {"type": "string"},
                    "factor": {
                        "type": "string",
                        "enum": [
                            "energy", "time_pressure", "schedule_disruption", "mood",
                            "cue_failed", "too_big", "environment", "motivation", "other",
                        ],
                    },
                },
                "required": ["habit_name", "outcome", "reason", "factor"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["profile_updates", "memory_updates", "new_memories", "behavior_events"],
    "additionalProperties": False,
}


def user_tz(profile: dict):
    """The user's zoneinfo timezone, falling back to UTC."""
    try:
        return zoneinfo.ZoneInfo(profile.get("timezone") or "UTC")
    except Exception:
        return dt_timezone.utc


def user_today(profile: dict) -> str:
    """Return today's date string in the user's local timezone."""
    return datetime.now(user_tz(profile)).strftime("%Y-%m-%d")

def build_memory_context(profile: dict, memories: list[dict], habits: list[dict] = None, completed_today: dict = None, last_completions: dict = None) -> str:
    tz = user_tz(profile)
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
        now = datetime.now(tz)
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
                    t_str = datetime.fromtimestamp(comp_time, tz).strftime('%I:%M %p')
                    habit_status.append(f"[x] {name} (Completed today at {t_str})")
                else:
                    habit_status.append(f"[x] {name} (Completed today)")
            else:
                last_ts = last_completions.get(h_id)
                if last_ts:
                    last_dt = datetime.fromtimestamp(last_ts, tz)
                    days_ago = (now.date() - last_dt.date()).days
                    when = "yesterday" if days_ago == 1 else f"{days_ago} days ago"
                    habit_status.append(
                        f"[ ] {name} (Not completed today; last checked off {when} at {last_dt.strftime('%I:%M %p')})"
                    )
                else:
                    habit_status.append(f"[ ] {name} (Not completed today; never checked off yet)")
        lines.append("- Current Active Targets:\n  " + "\n  ".join(habit_status))

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
        mem_lines = []
        for m in memories:
            ts = m.get("created_at")
            when = datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d") if ts else None
            mem_lines.append(f"- ({when}) {m['text']}" if when else f"- {m['text']}")
        out += (
            "\nThings they've told you in past conversations (dated so you can "
            "tell recent struggles from old ones):\n" + "\n".join(mem_lines)
        )
    return out


def build_behavior_log(events: list[dict]) -> str:
    """Chronological evidence log of why habits succeeded/failed, for daily check-ins."""
    if not events:
        return ""
    lines = []
    for e in events:
        habit = e.get("habit_name") or "(whole day)"
        lines.append(
            f"- {e.get('date', '?')} | {habit} | {e.get('outcome', '')} | "
            f"{e.get('reason', '')} [{e.get('factor', 'other')}]"
        )
    return (
        "\n\nBEHAVIOR LOG — day-by-day evidence from past check-ins on why habits "
        "were done or skipped (oldest first). This is your best diagnostic data: "
        "look for repeating factors, times, and situations across days, and when "
        "you spot a pattern, name it to the user gently and concretely:\n"
        + "\n".join(lines)
    )


def extract_and_store(user_id: str, history: list[dict], phase: str) -> None:
    profile = store.get_profile(user_id)
    existing = store.get_memories_with_ids(user_id, 100)
    existing_str = "\n".join(f"[{m['id']}] {m['text']}" for m in existing) or "(none yet)"

    checklist_str = ""
    if phase == "daily_chat":
        habits = store.get_user_habits(user_id)
        completed = store.get_completed_ids(user_id, user_today(profile))
        if habits:
            checklist_str = "\nTODAY'S HABIT CHECKLIST:\n" + "\n".join(
                f"- {'[x]' if h['id'] in completed else '[ ]'} {h.get('habit_name', '')}"
                for h in habits
            ) + "\n"

    recent = history[-8:]
    convo_lines = []
    for m in recent:
        text = m['content']
        if isinstance(text, list):
            text = next((c["text"] for c in text if c["type"] == "text"), "")
        convo_lines.append(f"{m['role'].capitalize()}: {text}")
    convo_str = "\n".join(convo_lines)
    conversation = (
        f"CONVERSATION PHASE: {phase}\n\n"
        f"EXISTING MEMORIES:\n{existing_str}\n"
        f"{checklist_str}\n"
        f"RECENT CONVERSATION:\n{convo_str}"
    )
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            system=EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": conversation}]
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        data = parse_llm_json(text)
    except Exception as exc:
        print(f"[memory] extraction failed: {exc}")
        return

    store.save_profile(user_id, store.merge_profile(profile, data.get("profile_updates") or {}))

    raw_settings = data.get("settings_updates") or {}
    settings_updates = {k: v for k, v in raw_settings.items() if v is not None and k != "weight"}
    if settings_updates:
        store.save_settings(user_id, settings_updates)
        print(f"[settings] updated for {user_id}: {settings_updates}")

    weight_str = str(raw_settings.get("weight") or "")
    if weight_str:
        nums = re.findall(r'\d+\.?\d*', weight_str)
        if nums:
            weight_val = float(nums[0])
            if "kg" in weight_str.lower():
                weight_val = weight_val * 2.20462
            today = datetime.now().strftime("%Y-%m-%d")
            store.add_weight_entry(user_id, today, round(weight_val, 1))
            print(f"[weight] auto-logged {round(weight_val, 1)} lbs for {user_id} on {today}")

    for upd in data.get("memory_updates") or []:
        if upd.get("id") and upd.get("text"):
            store.update_memory(user_id, upd["id"], upd["text"])
            print(f"[memory] updated {upd['id']} for {user_id}: {upd['text']}")

    added = store.add_memories(user_id, data.get("new_memories") or [])
    if added:
        print(f"[memory] +{len(added)} new fact(s) for {user_id}: {added}")

    events = data.get("behavior_events") or []
    if phase == "daily_chat" and events:
        store.add_behavior_events(user_id, user_today(profile), events)
        print(f"[memory] +{len(events)} behavior event(s) for {user_id}: {events}")

# ---------------------------------------------------------------------------
# Claude helpers
# ---------------------------------------------------------------------------

def generate_suggestions(history: list[dict], model: str = "claude-haiku-4-5-20251001") -> list[str]:
    recent = history[-6:]
    convo = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in recent)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=200,
            system=SUGGESTION_SYSTEM,
            messages=[{"role": "user", "content": convo}]
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        data = parse_llm_json(text)
        return (data.get("suggestions") or [])[:3]
    except Exception as exc:
        print(f"[suggestions] generation failed: {exc}")
        return []


def suggest_initial_habits(user_id: str, history: list[dict], model: str = "claude-haiku-4-5-20251001") -> tuple[str, list[dict], list[dict]]:
    profile = store.get_profile(user_id)
    memories = store.get_memories(user_id)
    habits = store.get_user_habits(user_id)
    completed_today = store.get_completed_info(user_id, user_today(profile))
    memory_ctx = build_memory_context(profile, memories, habits, completed_today).strip()
    convo_text = ""
    for m in history:
        # Extract text if content is a list
        text = m['content']
        if isinstance(text, list):
            text = next((c["text"] for c in text if c["type"] == "text"), "")
        convo_text += f"{m['role'].capitalize()}: {text}\n"
    system = HABIT_SUGGESTION_SYSTEM.format(habits_context=HABITS_CONTEXT)
    if memory_ctx:
        system += f"\n\nWhat you already know about this person:\n{memory_ctx}"
    response = client.messages.create(
        model=model,
        max_tokens=1000,
        system=system,
        messages=[{"role": "user", "content": f"Conversation so far:\n{convo_text}"}]
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    data = parse_llm_json(text)
    reply = data["reply"]
    return reply, data.get("final_habits", []), data.get("suggested_exercises", [])


def generate_final_habits(user_id: str, history: list[dict], model: str = "claude-haiku-4-5-20251001") -> tuple[str, list[dict]]:
    profile = store.get_profile(user_id)
    memories = store.get_memories(user_id)
    habits = store.get_user_habits(user_id)
    completed_today = store.get_completed_info(user_id, user_today(profile))
    memory_ctx = build_memory_context(profile, memories, habits, completed_today).strip()
    convo_text = ""
    for m in history:
        text = m['content']
        if isinstance(text, list):
            text = next((c["text"] for c in text if c["type"] == "text"), "")
        convo_text += f"{m['role'].capitalize()}: {text}\n"
    system = FINAL_HABITS_SYSTEM
    if memory_ctx:
        system += f"\n\nWhat you already know about this person:\n{memory_ctx}"
    response = client.messages.create(
        model=model,
        max_tokens=1200,
        system=system + "\n\nCRITICAL: The user has made some changes or confirmations. Finalize the list of habits.",
        messages=history
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    data = parse_llm_json(text)
    return data["reply"], data.get("final_habits", [])

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
import asyncio
import httpx
from datetime import datetime, timezone as dt_timezone
import zoneinfo

app = FastAPI(title="Better Me — Hugo Coach API")

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
                            "title": "Hugo is waiting! 👋",
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
    image_base64: str | None = None


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


class ExerciseSuggestion(BaseModel):
    name: str
    reason: str
    target_muscle: str
    recommended_split: str
    sets: str
    reps: str

class ChatResponse(BaseModel):
    reply: str
    suggested_habits: list[CsvHabit] | None = None
    final_habits: list[FinalHabit] | None = None
    suggestions: list[str] | None = None
    suggested_exercises: list[ExerciseSuggestion] | None = None
    ui_action: str | None = None
    workout_plan: dict | None = None
    proposal: dict | None = None


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

class WorkoutsRequest(BaseModel):
    user_id: str
    workouts: dict

class NutritionRequest(BaseModel):
    user_id: str
    nutrition: dict

class LogPhotoRequest(BaseModel):
    user_id: str
    image_base64: str

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

    history: list[dict] = [{"role": "assistant", "content": HUGO_GREETING}]
    has_image = False
    for m in request.messages:
        if m.role not in ("user", "assistant"):
            raise HTTPException(status_code=400, detail=f"Invalid role: {m.role}")
        
        # Inject physique analysis prompt if this is the latest message and it has an image
        text_content = m.content
        if m.image_base64 and m == request.messages[-1]:
            text_content += "\n\n[SYSTEM: The user has uploaded an image of their goal physique. Analyze this physique. Identify the main physical features and standout muscle proportions. For EACH standout feature you identify, immediately provide a specific exercise to develop that exact feature.]"

        if m.image_base64:
            has_image = True
            content_list = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": m.image_base64
                    }
                }
            ]
            if text_content.strip():
                content_list.append({
                    "type": "text",
                    "text": text_content
                })

            history.append({
                "role": m.role,
                "content": content_list
            })
        else:
            history.append({"role": m.role, "content": text_content})
            
    # Use Haiku for everything (including vision) as it's significantly cheaper and still excellent
    model_to_use = "claude-haiku-4-5-20251001"

    last_user = next(
        (m.content for m in reversed(request.messages) if m.role == "user"), ""
    )


    # --- Phase: suggest initial habits / generate workout ---
    if request.phase == "request_habits":
        try:
            reply, workout_plan = build_workout_plan(request.user_id, history, model=model_to_use)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Workout generation error: {exc}") from exc
            
        if workout_plan:
            store.save_workout_plan(request.user_id, workout_plan)
            
        if last_user and reply:
            background_tasks.add_task(
                extract_and_store,
                request.user_id,
                history + [{"role": "assistant", "content": reply}],
                request.phase,
            )
            
        return ChatResponse(reply=reply, workout_plan=workout_plan)

    # --- Phase: final custom habits ---
    if request.phase == "final_habits":
        try:
            reply, raw_final = generate_final_habits(request.user_id, history, model=model_to_use)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Final habit error: {exc}") from exc
        if last_user and reply:
            background_tasks.add_task(
            extract_and_store,
            request.user_id,
            history + [{"role": "assistant", "content": reply}],
            request.phase,
        )
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
                model=model_to_use,
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
            background_tasks.add_task(
            extract_and_store,
            request.user_id,
            history + [{"role": "assistant", "content": reply}],
            request.phase,
        )
        suggestions = generate_suggestions(history + [{"role": "assistant", "content": reply}], model=model_to_use)
        return ChatResponse(reply=reply, suggestions=suggestions or None)

    elif request.phase == "onboarding":
        try:
            response = client.messages.create(
                model=model_to_use,
                max_tokens=512,
                system=HUGO_SYSTEM_PROMPT,
                messages=history
            )
        except anthropic.APIError as exc:
            raise HTTPException(status_code=502, detail=f"Claude API error: {exc}") from exc
        
        text = next((b.text for b in response.content if b.type == "text"), "")
        data = parse_llm_json(text)
        if not data:
            data = {"reply": text, "suggestions": [], "ui_action": None}
            
        reply = data.get("reply", text)
        suggestions = data.get("suggestions", [])
        ui_action = data.get("ui_action")
        
        if last_user and reply:
            background_tasks.add_task(
                extract_and_store,
                request.user_id,
                history + [{"role": "assistant", "content": reply}],
                "onboarding",
            )
        return ChatResponse(reply=reply, suggestions=suggestions, ui_action=ui_action)

    # --- Phase: daily_chat ---
    if request.phase == "daily_chat":
        habits = store.get_user_habits(request.user_id)
        completed_today = store.get_completed_info(request.user_id, user_today(profile))
        last_completions = store.get_last_completions(request.user_id)
        behavior_events = store.get_behavior_events(request.user_id)
        memory_ctx = (
            build_memory_context(profile, memories, habits, completed_today, last_completions)
            + build_behavior_log(behavior_events)
        )

        # No user messages yet — generate a personalized opening
        if not last_user:
            system = DAILY_CHAT_OPENER_SYSTEM + "\n" + memory_ctx
            try:
                response = client.messages.create(
                    model=model_to_use,
                    max_tokens=256,
                    system=system,
                    messages=[{"role": "user", "content": "Start the check-in."}],
                )
            except anthropic.APIError as exc:
                raise HTTPException(status_code=502, detail=f"Claude API error: {exc}") from exc
            reply = next((b.text for b in response.content if b.type == "text"), "")
            return ChatResponse(reply=reply)

        if has_image:
            system = PHYSIQUE_ANALYSIS_SYSTEM
        else:
            system = DAILY_CHAT_SYSTEM + "\n" + memory_ctx
            
        try:
            response = client.messages.create(
                model=model_to_use,
                max_tokens=1024,
                system=system,
                messages=history
            )
        except anthropic.APIError as exc:
            raise HTTPException(status_code=502, detail=f"Claude API error: {exc}") from exc
            
        text = next((b.text for b in response.content if b.type == "text"), "")
        data = parse_llm_json(text)
        if not data:
            data = {"reply": text, "suggested_exercises": []}
            
        reply = data.get("reply", "")
        
        if last_user and reply:
            background_tasks.add_task(
            extract_and_store,
            request.user_id,
            history + [{"role": "assistant", "content": reply}],
            request.phase,
        )
        ex_list = data.get("suggested_exercises")
        if not isinstance(ex_list, list):
            ex_list = []
        ex_models = [ExerciseSuggestion(**e) for e in ex_list]
        
        return ChatResponse(
            reply=reply, 
            suggestions=data.get("suggestions") or None, 
            suggested_exercises=ex_models,
            proposal=data.get("proposal")
        )

    # --- Phase: onboarding (default) ---
    habits = store.get_user_habits(request.user_id)
    completed_today = store.get_completed_info(request.user_id, user_today(profile))
    q1, q2, q3 = _pick_questions()
    onboarding_system = HUGO_SYSTEM_PROMPT.replace("{Q1}", q1).replace("{Q2}", q2).replace("{Q3}", q3)
    system = onboarding_system + build_memory_context(profile, memories, habits, completed_today)
    try:
        response = client.messages.create(
            model=model_to_use,
            max_tokens=1024,
            system=system,
            messages=history,
        )
    except anthropic.APIError as exc:
        raise HTTPException(status_code=502, detail=f"Claude API error: {exc}") from exc
    reply = next((b.text for b in response.content if b.type == "text"), "")
    if last_user and reply:
        background_tasks.add_task(
            extract_and_store,
            request.user_id,
            history + [{"role": "assistant", "content": reply}],
            request.phase,
        )
    suggestions = generate_suggestions(history + [{"role": "assistant", "content": reply}], model=model_to_use)
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
    if new_habit:
        profile = store.get_profile(req.user_id)
        store.add_behavior_events(req.user_id, user_today(profile), [{
            "habit_name": new_habit.get("habit_name", ""),
            "outcome": "added",
            "reason": "manually added this habit themselves (reason not yet known)",
            "factor": "other",
        }])
    return {"added": bool(new_habit), "habit": new_habit}

@app.post("/habits/delete")
def delete_habit(req: DeleteHabitRequest) -> dict:
    store.delete_user_habit(req.user_id, req.habit_id, req.habit_name)
    profile = store.get_profile(req.user_id)
    store.add_behavior_events(req.user_id, user_today(profile), [{
        "habit_name": req.habit_name,
        "outcome": "removed",
        "reason": "manually removed this habit themselves (reason not yet known)",
        "factor": "other",
    }])
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

@app.get("/workouts/{user_id}")
def get_workouts(user_id: str) -> dict:
    return store.get_workouts(user_id)

@app.get("/workout_plan/{user_id}")
def get_workout_plan_endpoint(user_id: str) -> dict:
    plan = store.get_workout_plan(user_id)
    return plan or {}

class SaveWorkoutPlanRequest(BaseModel):
    user_id: str
    plan: dict

@app.post("/workout_plan/save")
def save_workout_plan_endpoint(req: SaveWorkoutPlanRequest) -> dict:
    store.save_workout_plan(req.user_id, req.plan)
    
    # Sync the daily workout schedule (workouts object) with the new plan
    days = req.plan.get("days", [])
    if days:
        first_day = days[0]
        today_exercises = []
        for ex in first_day.get("exercises", []):
            today_exercises.append({
                "name": ex.get("name", "Exercise"),
                "targetSets": str(ex.get("sets", 3)),
                "targetReps": str(ex.get("reps", "10")),
                "lastWeight": str(ex.get("target_weight_lbs", 0)),
            })
        
        first_day_name = re.sub(r'(?i)^Day\s*\d+[:\- ]*', '', first_day.get("day_name", "Workout"))
        is_today_rest = "rest" in first_day_name.lower()
        
        workouts_data = {
            "today": {
                "title": "Rest Day" if is_today_rest else first_day_name,
                "split": "Rest" if is_today_rest else first_day_name.split()[0],
                "duration": "0 min" if is_today_rest else "45-60 min",
                "exercises": today_exercises
            },
            "upcoming": []
        }

        today_date = datetime.now()
        for i, d in enumerate(days[1:]):
            future_date = today_date + timedelta(days=i+1)
            day_str = future_date.strftime("%A")
            
            day_name = re.sub(r'(?i)^Day\s*\d+[:\- ]*', '', d.get("day_name", f"Workout {i+2}"))
            is_rest = "rest" in day_name.lower()
            
            workouts_data["upcoming"].append({
                "id": f"w{i+2}",
                "date": day_str,
                "title": "Rest Day" if is_rest else day_name,
                "split": "Rest" if is_rest else (day_name.split()[0] if day_name else "Day"),
                "duration": "0 min" if is_rest else "45-60 min",
                "exercises": [] 
            })
        
        store.save_workouts(req.user_id, workouts_data)
        
    return {"status": "ok"}

@app.post("/workouts")
def update_workouts(req: WorkoutsRequest) -> dict:
    store.save_workouts(req.user_id, req.workouts)
    return {"status": "ok"}

@app.get("/strength_score/{user_id}")
def get_strength_score(user_id: str) -> dict:
    plan = store.get_workout_plan(user_id) or {}
    profile = store.get_profile(user_id) or {}
    bw_lbs = profile.get("weight_lbs", 0)
    name = profile.get("name", "Hugo")
    
    # Calculate custom scores
    scores = calculate_strength_scores(plan, bw_lbs)
    scores["user_name"] = name
    return scores

class AddExerciseRequest(BaseModel):
    user_id: str = "local-dev-user"
    split: str
    exercise: dict

@app.post("/workouts/add_exercise")
def add_exercise(req: AddExerciseRequest) -> dict:
    workouts = store.get_workouts(req.user_id)
    if not workouts:
        return {"status": "error", "message": "No workouts found"}
    
    added = False
    
    # Try today
    today = workouts.get("today")
    if today and today.get("split") == req.split:
        if "exercises" not in today:
            today["exercises"] = []
        today["exercises"].append(req.exercise)
        added = True
    else:
        # Try upcoming
        upcoming = workouts.get("upcoming", [])
        for w in upcoming:
            if w.get("split") == req.split:
                if "exercises" not in w:
                    w["exercises"] = []
                w["exercises"].append(req.exercise)
                added = True
                break

    if added:
        store.save_workouts(req.user_id, workouts)
        return {"status": "ok", "workouts": workouts}
    else:
        # If the split is not found, forcefully append it to today as a fallback
        if "today" not in workouts or not workouts["today"]:
            workouts["today"] = {"split": "Mixed", "exercises": []}
            
        today = workouts["today"]
        if "exercises" not in today:
            today["exercises"] = []
            
        today["exercises"].append(req.exercise)
        store.save_workouts(req.user_id, workouts)
        return {"status": "ok", "workouts": workouts, "note": "Added to today because split was not found"}

def _estimate_nutrition(user_id: str, profile: dict) -> dict:
    """Estimate macro targets using Mifflin-St Jeor BMR + activity level from workout plan."""
    settings = store.get_settings(user_id)

    # --- Weight ---
    weight_lbs = 175.0
    entries = store.get_weight_entries(user_id, limit=1)
    if entries:
        weight_lbs = entries[-1].get("weight_lbs", 175.0)
    weight_kg = weight_lbs * 0.453592

    # --- Height in cm ---
    height_cm = 175.0
    height_str = str(settings.get("height") or "")
    height_unit = settings.get("heightUnit", "cm")
    if height_str:
        nums = re.findall(r'\d+\.?\d*', height_str)
        if nums:
            if "'" in height_str or height_unit == "ft":
                feet = float(nums[0])
                inches = float(nums[1]) if len(nums) > 1 else 0
                height_cm = (feet * 12 + inches) * 2.54
            else:
                val = float(nums[0])
                height_cm = val if val > 100 else val * 30.48  # treat <100 as feet

    # --- Age ---
    age = 25
    try:
        age = int(settings.get("age") or 25)
    except (ValueError, TypeError):
        pass

    # --- Gender (from settings, default male) ---
    gender = str(settings.get("gender") or "male").lower()

    # --- Mifflin-St Jeor BMR ---
    if "female" in gender or gender == "f":
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161
    else:
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5

    # --- Activity level from workout plan ---
    workout_plan = store.get_workout_plan(user_id)
    training_days = 3  # default
    has_cardio = False
    if workout_plan:
        days = workout_plan.get("days", [])
        training_days = len(days)
        has_cardio = any(
            any(w in (d.get("day_name") or "").lower() for w in ["cardio", "spin", "hiit", "run", "conditioning"])
            for d in days
        )

    # Activity multipliers (standard Harris-Benedict scale)
    if training_days <= 1:
        activity = 1.2
    elif training_days <= 3:
        activity = 1.375
    elif training_days <= 5:
        activity = 1.55
    else:
        activity = 1.725
    if has_cardio:
        activity = min(activity + 0.1, 1.9)

    tdee = int(bmr * activity)

    # --- Goal adjustment ---
    goals_text = " ".join([g.lower() for g in (profile.get("goals") or [])])
    focus = (profile.get("focus_area") or "").lower()
    is_cutting = any(w in goals_text + " " + focus for w in ["fat", "cut", "lean", "lose", "shred", "deficit", "recomp"])

    calories = int(tdee * (0.85 if is_cutting else 1.1))
    protein = int(weight_lbs * (1.0 if is_cutting else 0.9))
    fat_cals = int(calories * 0.25)
    fats = int(fat_cals / 9)
    carbs = max(0, int((calories - protein * 4 - fat_cals) / 4))
    balance = calories - tdee

    return {
        "macros": {
            "calories": {"target": calories, "current": 0, "tdee": tdee, "balance": balance},
            "protein":  {"target": protein,  "current": 0},
            "carbs":    {"target": carbs,    "current": 0},
            "fats":     {"target": fats,     "current": 0},
        },
        "meals": [],
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
        "calorie_history": []
    }

def _fresh_day_from_targets(targets: dict) -> dict:
    """Create a zeroed nutrition entry copying macro targets from a previous day."""
    macros = {}
    for key in ["calories", "protein", "carbs", "fats"]:
        prev = targets.get("macros", {}).get(key, {})
        macros[key] = {k: v for k, v in prev.items() if k != "current"}
        macros[key]["current"] = 0
    return {"macros": macros, "meals": []}

@app.get("/nutrition/{user_id}")
def get_nutrition(user_id: str, date: str = None) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    target_date = date or today
    is_today = (target_date == today)

    data = store.get_nutrition(user_id, target_date)

    if not data:
        if not is_today:
            return {}
        previous = store.get_latest_nutrition_targets(user_id)
        if previous:
            data = _fresh_day_from_targets(previous)
        else:
            profile = store.get_profile(user_id)
            data = _estimate_nutrition(user_id, profile)
        store.save_nutrition(user_id, data, today)

    data["calorie_history"] = store.get_calorie_history(user_id)
    return data

@app.post("/nutrition")
def update_nutrition(req: NutritionRequest) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    store.save_nutrition(req.user_id, req.nutrition, today)
    return {"status": "ok"}

@app.post("/nutrition/log_photo")
def log_photo(req: LogPhotoRequest) -> dict:
    import uuid
    
    # Strip data URI prefix if present
    base64_data = req.image_base64
    if "," in base64_data[:50]:
        _, base64_data = base64_data.split(",", 1)
        
    # Detect media type from magic bytes
    if base64_data.startswith("iVBORw0KGgo"):
        media_type = "image/png"
    elif base64_data.startswith("UklGR"):
        media_type = "image/webp"
    else:
        media_type = "image/jpeg"

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=(
                "You are an expert nutritionist. Analyze the food in the image and return ONLY a JSON object "
                "with these exact fields:\n"
                '- "name": short meal name (e.g. "Grilled Chicken Breast")\n'
                '- "food": brief description of what you see (e.g. "Grilled chicken with roasted vegetables")\n'
                '- "cals": total calories as an integer\n'
                '- "protein": protein in grams as an integer\n'
                '- "carbs": carbohydrates in grams as an integer\n'
                '- "fats": fats in grams as an integer\n\n'
                "Return only the JSON object, no other text. Be realistic and accurate."
            ),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64_data
                            }
                        },
                        {
                            "type": "text",
                            "text": "Analyze this food and return the JSON with name, food description, cals, protein, carbs, and fats."
                        }
                    ]
                }
            ]
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        meal_data = parse_llm_json(text)
        if not meal_data or "cals" not in meal_data:
            raise HTTPException(status_code=502, detail="Could not parse nutrition data from image")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}")
        
    meal_data["id"] = str(uuid.uuid4())
    
    today = datetime.now().strftime("%Y-%m-%d")
    data = store.get_nutrition(req.user_id, today)

    if not data:
        previous = store.get_latest_nutrition_targets(req.user_id)
        if previous:
            data = _fresh_day_from_targets(previous)
        else:
            profile = store.get_profile(req.user_id)
            data = _estimate_nutrition(req.user_id, profile)

    if "meals" not in data:
        data["meals"] = []
    data["meals"].append(meal_data)

    data["macros"]["calories"]["current"] += meal_data.get("cals", 0)
    data["macros"]["protein"]["current"] += meal_data.get("protein", 0)
    data["macros"]["carbs"]["current"] += meal_data.get("carbs", 0)
    data["macros"]["fats"]["current"] += meal_data.get("fats", 0)

    store.save_nutrition(req.user_id, data, today)
    data["calorie_history"] = store.get_calorie_history(req.user_id)
    return {"status": "ok", "meal": meal_data, "nutrition": data}

class WeightEntryRequest(BaseModel):
    user_id: str
    date: str
    weight_lbs: float

@app.get("/weight/{user_id}")
def get_weight(user_id: str) -> dict:
    entries = store.get_weight_entries(user_id)
    return {"entries": entries}

@app.post("/weight")
def add_weight(req: WeightEntryRequest) -> dict:
    entry = store.add_weight_entry(req.user_id, req.date, req.weight_lbs)
    return {"status": "ok", "entry": entry}
