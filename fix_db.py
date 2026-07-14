import os
import store

store.init_db()
db = store._db()
users = db.collection("users").stream()
for user in users:
    print(f"Updating {user.id}")
    db.collection("users").document(user.id).set({"hasCompletedInitialChat": True}, merge=True)
print("Done!")
