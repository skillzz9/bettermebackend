"""Per-user memory, identity profile, habits, and streak storage.

Backed by Firebase Firestore. The public functions below are the whole interface.
"""

import json
import os
import time
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore

# Initialize Firebase Admin
# Supports two modes:
#   FIREBASE_SERVICE_ACCOUNT_JSON  — the full JSON string (for Railway/production)
#   FIREBASE_SERVICE_ACCOUNT       — path to a local JSON file (for local dev)
if not firebase_admin._apps:
    try:
        json_str = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
        if json_str:
            cred = credentials.Certificate(json.loads(json_str))
        else:
            cred_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "serviceAccountKey.json")
            cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        print(f"⚠️ Warning: Failed to initialize Firebase Admin: {e}")

def _db():
    return firestore.client()

EMPTY_PROFILE = {
    "name": None,
    "timezone": None,
    "focus_area": None,
    "communication_style": None,
    "lifestyle": None,
    "motivation": None,
    "goals": [],
    "traits": [],
    "obstacles": [],
}

def init_db() -> None:
    pass

# ---- Settings ----------------------------------------------------------------

def get_settings(user_id: str) -> dict:
    try:
        doc = _db().collection("users").document(user_id).get()
        if doc.exists:
            return doc.to_dict().get("settings", {})
        return {}
    except Exception:
        return {}

def save_settings(user_id: str, settings: dict) -> None:
    try:
        updates = {f"settings.{k}": v for k, v in settings.items()}
        _db().collection("users").document(user_id).update(updates)
    except Exception as e:
        # If the document doesn't exist, create it
        _db().collection("users").document(user_id).set({"settings": settings}, merge=True)
        print(f"Created new settings doc: {e}")

# ---- Identity profile --------------------------------------------------------

def get_profile(user_id: str) -> dict:
    try:
        doc = _db().collection("users").document(user_id).get()
        merged = dict(EMPTY_PROFILE)
        if doc.exists:
            data = doc.to_dict().get("profile", {})
            merged.update(data)
        return merged
    except Exception:
        return dict(EMPTY_PROFILE)

def save_profile(user_id: str, profile: dict) -> None:
    try:
        _db().collection("users").document(user_id).set({"profile": profile}, merge=True)
    except Exception as e:
        print(f"Failed to save profile: {e}")

def merge_profile(old: dict, updates: dict) -> dict:
    new = dict(old)
    for key in ("name", "timezone", "focus_area", "communication_style",
                "lifestyle", "motivation"):
        value = (updates.get(key) or "").strip() if updates.get(key) else None
        if value:
            new[key] = value
    for key in ("goals", "traits", "obstacles"):
        current = list(new.get(key) or [])
        seen = {x.lower() for x in current}
        for value in updates.get(key) or []:
            value = value.strip()
            if not value:
                continue
            value_lower = value.lower()
            # Skip if any existing entry is a substring match (catches rephrasing)
            if any(value_lower in s or s in value_lower for s in seen):
                continue
            current.append(value)
            seen.add(value_lower)
        new[key] = current
    return new

# ---- Memories ----------------------------------------------------------------

def get_memories(user_id: str, limit: int = 60) -> list[dict]:
    """Return memories as {text, created_at}, oldest first."""
    try:
        docs = _db().collection("users").document(user_id).collection("memories") \
            .order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit).stream()
        return [
            {"text": doc.to_dict().get("text", ""), "created_at": doc.to_dict().get("created_at")}
            for doc in docs
        ][::-1]
    except Exception:
        return []

def get_memories_with_ids(user_id: str, limit: int = 100) -> list[dict]:
    """Return memories as {id, text} so the extractor can target updates by ID."""
    try:
        docs = _db().collection("users").document(user_id).collection("memories") \
            .order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit).stream()
        return [{"id": doc.id, "text": doc.to_dict().get("text", "")} for doc in docs][::-1]
    except Exception:
        return []

def update_memory(user_id: str, memory_id: str, text: str) -> None:
    try:
        _db().collection("users").document(user_id).collection("memories") \
            .document(memory_id).update({"text": text.strip()})
    except Exception as e:
        print(f"Failed to update memory {memory_id}: {e}")

def add_memories(user_id: str, texts: list[str]) -> list[str]:
    now = time.time()
    existing = {m["text"].lower() for m in get_memories_with_ids(user_id, 500)}
    fresh = []
    for text in texts:
        text = (text or "").strip()
        if text and text.lower() not in existing:
            fresh.append(text)
            existing.add(text.lower())

    if fresh:
        try:
            batch = _db().batch()
            mem_ref = _db().collection("users").document(user_id).collection("memories")
            for t in fresh:
                doc_ref = mem_ref.document()
                batch.set(doc_ref, {"text": t, "created_at": now})
            batch.commit()
        except Exception as e:
            print(f"Failed to add memories: {e}")

    return fresh

# ---- Behavior events -----------------------------------------------------------
# Dated, structured records of WHY a habit succeeded or failed on a given day.
# These are separate from memories: memories hold durable patterns and facts,
# behavior events hold the day-by-day evidence those patterns are built from.

def add_behavior_events(user_id: str, date: str, events: list[dict]) -> None:
    """Each event: {habit_name, outcome, reason, factor}."""
    now = time.time()
    valid = [e for e in events if (e.get("reason") or "").strip()]
    if not valid:
        return
    try:
        batch = _db().batch()
        events_ref = _db().collection("users").document(user_id).collection("behavior_events")
        for e in valid:
            doc_ref = events_ref.document()
            batch.set(doc_ref, {
                "date": date,
                "habit_name": (e.get("habit_name") or "").strip() or None,
                "outcome": e.get("outcome", ""),
                "reason": e["reason"].strip(),
                "factor": e.get("factor", "other"),
                "created_at": now,
            })
        batch.commit()
    except Exception as e:
        print(f"Failed to add behavior events: {e}")

def get_behavior_events(user_id: str, limit: int = 40) -> list[dict]:
    """Most recent behavior events, oldest first."""
    try:
        docs = _db().collection("users").document(user_id).collection("behavior_events") \
            .order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit).stream()
        return [doc.to_dict() for doc in docs][::-1]
    except Exception:
        return []

# ---- User habits -------------------------------------------------------------

def save_user_habits(user_id: str, habits: list[dict]) -> list[dict]:
    """Replace user's habits with a new list. Returns saved habits with DB ids."""
    now = time.time()
    try:
        # Delete existing habits
        habits_ref = _db().collection("users").document(user_id).collection("habits")
        existing_docs = habits_ref.stream()
        batch = _db().batch()
        for doc in existing_docs:
            batch.delete(doc.reference)
        batch.commit()

        # Add new habits
        batch = _db().batch()
        for h in habits:
            doc_ref = habits_ref.document()
            batch.set(doc_ref, {
                "habit_name": h["habit"],
                "cue": h.get("cue", ""),
                "pillar": h.get("pillar", "health"),
                "identity_text": h.get("identity", ""),
                "minutes": int(h.get("minutes", 5)),
                "created_at": now
            })
        batch.commit()
    except Exception as e:
        print(f"Failed to save user habits: {e}")
        
    return get_user_habits(user_id)

def get_user_habits(user_id: str) -> list[dict]:
    try:
        docs = _db().collection("users").document(user_id).collection("habits").order_by("created_at").stream()
        res = []
        for doc in docs:
            d = doc.to_dict()
            d["id"] = doc.id
            res.append(d)
        return res
    except Exception:
        return []

def add_user_habit(user_id: str, habit: dict) -> dict:
    """Add a single custom habit to the user's list."""
    now = time.time()
    try:
        habits_ref = _db().collection("users").document(user_id).collection("habits")
        doc_ref = habits_ref.document()
        data = {
            "habit_name": habit.get("habit", ""),
            "cue": habit.get("cue", ""),
            "pillar": habit.get("pillar", "custom"),
            "identity_text": habit.get("identity", ""),
            "minutes": int(habit.get("minutes", 5)),
            "created_at": now
        }
        doc_ref.set(data)
        data["id"] = doc_ref.id
        return data
    except Exception as e:
        print(f"Failed to add custom habit: {e}")
        return {}

def delete_user_habit(user_id: str, habit_id: str, habit_name: str) -> None:
    """Remove a single habit from the user's list."""
    try:
        _db().collection("users").document(user_id).collection("habits") \
            .document(habit_id).delete()
    except Exception as e:
        print(f"Failed to save habits: {e}")
        return []

# ---- Workout Plans -----------------------------------------------------------

def save_workout_plan(user_id: str, workout_plan: dict) -> None:
    """Saves the generated workout plan JSON to Firestore."""
    now = time.time()
    try:
        workout_plan["created_at"] = now
        _db().collection("users").document(user_id).set({
            "workout_plan": workout_plan,
            "hasCompletedInitialChat": True
        }, merge=True)
    except Exception as e:
        print(f"Failed to save workout plan: {e}")

def get_workout_plan(user_id: str) -> dict | None:
    """Retrieves the user's current workout plan."""
    try:
        doc = _db().collection("users").document(user_id).get()
        if doc.exists:
            return doc.to_dict().get("workout_plan")
        return None
    except Exception:
        return None

# ---- Streak & completions ----------------------------------------------------

def get_streak(user_id: str) -> dict:
    try:
        doc = _db().collection("users").document(user_id).collection("streaks").document("current").get()
        if not doc.exists:
            return {"current_streak": 0, "last_completed_date": None}
        return doc.to_dict()
    except Exception:
        return {"current_streak": 0, "last_completed_date": None}

def get_completed_info(user_id: str, date: str) -> dict:
    try:
        docs = _db().collection("users").document(user_id).collection("daily_completions").where("completed_date", "==", date).stream()
        res = {}
        for doc in docs:
            d = doc.to_dict()
            if d.get("habit_id"):
                res[d["habit_id"]] = d.get("completed_at")
        return res
    except Exception:
        return {}

def get_last_completions(user_id: str) -> dict:
    """Most recent completion timestamp per habit, across all days."""
    try:
        docs = _db().collection("users").document(user_id).collection("daily_completions").stream()
        res = {}
        for doc in docs:
            d = doc.to_dict()
            hid = d.get("habit_id")
            ts = d.get("completed_at")
            if hid and ts and ts > res.get(hid, 0):
                res[hid] = ts
        return res
    except Exception:
        return {}

def get_completed_ids(user_id: str, date: str) -> set[str]:
    return set(get_completed_info(user_id, date).keys())

def set_completion(user_id: str, habit_id: str, date: str, completed: bool) -> int:
    """Toggle completion for one habit on a date. Returns updated streak."""
    try:
        completions_ref = _db().collection("users").document(user_id).collection("daily_completions")
        doc_id = f"{date}_{habit_id}"
        if completed:
            completions_ref.document(doc_id).set({
                "habit_id": habit_id,
                "completed_date": date,
                "completed_at": time.time()
            }, merge=True)
        else:
            completions_ref.document(doc_id).delete()
    except Exception as e:
        print(f"Failed to set completion: {e}")

    # Recalculate streak only when all habits are done for the day
    total = len(get_user_habits(user_id))
    done = len(get_completed_ids(user_id, date))

    if total > 0 and done >= total:
        return _maybe_increment_streak(user_id, date)
    return get_streak(user_id).get("current_streak", 0)

def _maybe_increment_streak(user_id: str, completed_date: str) -> int:
    info = get_streak(user_id)
    current = info.get("current_streak", 0)
    last = info.get("last_completed_date")

    if last == completed_date:
        return current

    yesterday = (
        datetime.strptime(completed_date, "%Y-%m-%d") - timedelta(days=1)
    ).strftime("%Y-%m-%d")

    new_streak = (current + 1) if last == yesterday else 1

    try:
        _db().collection("users").document(user_id).collection("streaks").document("current").set({
            "current_streak": new_streak,
            "last_completed_date": completed_date
        }, merge=True)
    except Exception as e:
        print(f"Failed to update streak: {e}")
        
    return new_streak

# ---- Workouts ----------------------------------------------------------------

def save_workouts(user_id: str, workouts_data: dict) -> None:
    try:
        _db().collection("users").document(user_id).set({"workouts": workouts_data}, merge=True)
    except Exception as e:
        print(f"Failed to save workouts: {e}")

def get_workouts(user_id: str) -> dict:
    try:
        doc = _db().collection("users").document(user_id).get()
        if doc.exists:
            return doc.to_dict().get("workouts", {})
        return {}
    except Exception:
        return {}

# ---- Body Weight -------------------------------------------------------------

def add_weight_entry(user_id: str, date: str, weight_lbs: float) -> dict:
    """Upsert a weight entry for a given date. Returns the saved entry."""
    now = time.time()
    try:
        doc_ref = _db().collection("users").document(user_id) \
            .collection("weight_entries").document(date)
        data = {"date": date, "weight_lbs": weight_lbs, "created_at": now}
        doc_ref.set(data, merge=True)
        return data
    except Exception as e:
        print(f"Failed to add weight entry: {e}")
        return {}

def get_weight_entries(user_id: str, limit: int = 30) -> list[dict]:
    """Return weight entries sorted by date ascending."""
    try:
        docs = _db().collection("users").document(user_id) \
            .collection("weight_entries") \
            .order_by("date", direction=firestore.Query.DESCENDING) \
            .limit(limit).stream()
        entries = [doc.to_dict() for doc in docs]
        return sorted(entries, key=lambda e: e["date"])
    except Exception:
        return []

# ---- Nutrition ---------------------------------------------------------------

def save_nutrition(user_id: str, nutrition_data: dict, date: str = None) -> None:
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    nutrition_data = dict(nutrition_data)
    nutrition_data["date"] = date
    try:
        _db().collection("users").document(user_id) \
            .collection("nutrition_log").document(date) \
            .set(nutrition_data)
    except Exception as e:
        print(f"Failed to save nutrition: {e}")

def get_nutrition(user_id: str, date: str = None) -> dict:
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    log_data = None
    try:
        doc = _db().collection("users").document(user_id) \
            .collection("nutrition_log").document(date).get()
        if doc.exists:
            log_data = doc.to_dict()
    except Exception:
        pass
    # Legacy fallback: if no log entry, or log entry has no meals,
    # check old single-field storage for this date
    try:
        doc = _db().collection("users").document(user_id).get()
        if doc.exists:
            legacy = doc.to_dict().get("nutrition", {})
            if legacy and legacy.get("last_updated") == date:
                legacy_meals = legacy.get("meals") or []
                log_meals = (log_data or {}).get("meals") or []
                if legacy_meals and not log_meals:
                    legacy["date"] = date
                    _db().collection("users").document(user_id) \
                        .collection("nutrition_log").document(date) \
                        .set(legacy)
                    return legacy
    except Exception:
        pass
    return log_data or {}

def get_latest_nutrition_targets(user_id: str) -> dict | None:
    """Return the most recent nutrition document to copy targets from.
    Falls back to the legacy nutrition field for users who haven't migrated yet."""
    try:
        docs = _db().collection("users").document(user_id) \
            .collection("nutrition_log") \
            .order_by("date", direction=firestore.Query.DESCENDING) \
            .limit(1).stream()
        for doc in docs:
            return doc.to_dict()
    except Exception:
        pass
    # Legacy fallback: old single-field storage
    try:
        doc = _db().collection("users").document(user_id).get()
        if doc.exists:
            legacy = doc.to_dict().get("nutrition")
            if legacy:
                return legacy
    except Exception:
        pass
    return None

def get_calorie_history(user_id: str, limit: int = 30) -> list[dict]:
    """Return [{date, calories}] for past days, oldest first, excluding today."""
    today = datetime.now().strftime("%Y-%m-%d")
    entries_by_date = {}
    try:
        docs = _db().collection("users").document(user_id) \
            .collection("nutrition_log") \
            .order_by("date", direction=firestore.Query.DESCENDING) \
            .limit(limit + 1).stream()
        for doc in docs:
            d = doc.to_dict()
            date = d.get("date")
            if date and date != today:
                entries_by_date[date] = d.get("macros", {}).get("calories", {}).get("current", 0)
    except Exception:
        pass
    # Include legacy single-field entry if it's a past date not already covered
    try:
        doc = _db().collection("users").document(user_id).get()
        if doc.exists:
            legacy = doc.to_dict().get("nutrition", {})
            legacy_date = legacy.get("last_updated") if legacy else None
            if legacy_date and legacy_date != today and legacy_date not in entries_by_date:
                entries_by_date[legacy_date] = legacy.get("macros", {}).get("calories", {}).get("current", 0)
    except Exception:
        pass
    entries = [{"date": d, "calories": c} for d, c in entries_by_date.items()]
    return sorted(entries, key=lambda e: e["date"])[-limit:]
