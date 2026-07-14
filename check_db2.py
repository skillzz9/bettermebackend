from store import _db

docs = _db().collection("users").stream()
for doc in docs:
    data = doc.to_dict()
    workouts = data.get("workouts", {})
    print(f"--- User {doc.id} ---")
    today = workouts.get("today", {})
    upcoming = workouts.get("upcoming", [])
    print("Today exercises:", [e.get("name") for e in today.get("exercises", [])])
    for u in upcoming:
        print(f"{u.get('split')} exercises:", [e.get("name") for e in u.get("exercises", [])])
