import sys
import os
import json
import firebase_admin
from firebase_admin import credentials, firestore, auth

def main():
    if len(sys.argv) < 2:
        print("Usage: python wipe_user.py <email>")
        sys.exit(1)
        
    email = sys.argv[1]
    
    # Initialize Firebase Admin
    if not firebase_admin._apps:
        try:
            json_str = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
            if json_str:
                cred = credentials.Certificate(json.loads(json_str))
            else:
                # Resolve relative to the backend directory
                base_dir = os.path.dirname(os.path.abspath(__file__))
                cred_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT", os.path.join(base_dir, "serviceAccountKey.json"))
                cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            print(f"Failed to initialize Firebase Admin: {e}")
            sys.exit(1)
            
    try:
        user = auth.get_user_by_email(email)
        uid = user.uid
        print(f"Found user {email} with UID: {uid}")
        
        # Delete from Firestore
        db = firestore.client()
        # Delete subcollections first
        for subcol in ["habits", "memories", "behavior_events"]:
            docs = db.collection("users").document(uid).collection(subcol).stream()
            count = 0
            for doc in docs:
                doc.reference.delete()
                count += 1
            if count > 0:
                print(f"Deleted {count} documents from {subcol}")
                
        # Delete main user document
        db.collection("users").document(uid).delete()
        print(f"Deleted main Firestore document for {uid}")
        
        # Delete from Auth
        auth.delete_user(uid)
        print(f"Deleted Auth user {email}")
        
        print(f"Account {email} successfully wiped! You can now start fresh.")
        
    except auth.UserNotFoundError:
        print(f"User {email} not found in Firebase Auth.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
