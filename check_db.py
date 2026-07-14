import os
import store

store.init_db()
db = store._db()
doc = db.collection("users").document("local-dev-user").get()
if doc.exists:
    print(doc.to_dict())
else:
    print("User document does not exist.")
