import os
import firebase_admin
from firebase_admin import credentials, firestore, auth

cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# Wipe firestore users collection (including subcollections)
docs = db.collection("users").stream()
for doc in docs:
    print(f"Deleting firestore user {doc.id}")
    # Delete subcollections
    for sub in ["memories", "habits", "behavior_events", "streaks", "daily_completions"]:
        subdocs = doc.reference.collection(sub).stream()
        for s in subdocs:
            s.reference.delete()
    # Delete main doc
    doc.reference.delete()

# Wipe auth
try:
    page = auth.list_users()
    while page:
        for user in page.users:
            print(f"Deleting auth user {user.uid}")
            auth.delete_user(user.uid)
        page = page.get_next_page()
except Exception as e:
    print(f"Auth wipe error: {e}")

print("Wiped successfully.")
