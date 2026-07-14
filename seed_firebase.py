import os
import store

store.init_db()
db = store._db()
user_id = "local-dev-user"

workouts_data = {
    "today": {
        "id": "w1",
        "title": "Chest & Triceps Focus",
        "split": "Push Day",
        "duration": "45-60 min",
        "exercises": [
            { "name": "Barbell Bench Press", "targetSets": "3", "targetReps": "8-10", "lastWeight": "225" },
            { "name": "Incline Dumbbell Press", "targetSets": "3", "targetReps": "10-12", "lastWeight": "85" },
            { "name": "Cable Crossovers", "targetSets": "3", "targetReps": "12-15", "lastWeight": "40" },
            { "name": "Tricep Pushdowns", "targetSets": "4", "targetReps": "10-12", "lastWeight": "50" },
        ]
    },
    "upcoming": [
        { "id": "w2", "date": "Tomorrow, Jul 14", "title": "Back & Biceps", "split": "Pull Day", "duration": "50 min" },
        { "id": "w3", "date": "Wed, Jul 15", "title": "Quads, Hamstrings & Calves", "split": "Leg Day", "duration": "60 min" },
    ]
}

nutrition_data = {
    "macros": {
        "calories": { "target": 2400, "current": 1850 },
        "protein": { "target": 180, "current": 135 },
        "carbs": { "target": 250, "current": 190 },
        "fats": { "target": 75, "current": 60 }
    },
    "meals": [
        { "id": "m1", "name": "Breakfast", "food": "Oatmeal & Protein Shake", "cals": 550 },
        { "id": "m2", "name": "Lunch", "food": "Chicken Breast & Rice", "cals": 650 },
        { "id": "m3", "name": "Dinner", "food": "Not Logged Yet", "cals": 0 },
    ]
}

print(f"Seeding data for {user_id}...")
store.save_workouts(user_id, workouts_data)
store.save_nutrition(user_id, nutrition_data)
print("Done!")
