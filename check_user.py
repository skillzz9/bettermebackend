import sys
import os
import json
import firebase_admin
from firebase_admin import credentials, firestore, auth

def main():
    if len(sys.argv) < 2:
        print("Usage: python check_user.py <email>")
        sys.exit(1)
        
    email = sys.argv[1]
    
    if not firebase_admin._apps:
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            cred_path = os.path.join(base_dir, "serviceAccountKey.json")
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            print(f"Failed to initialize Firebase Admin: {e}")
            sys.exit(1)
            
    try:
        user = auth.get_user_by_email(email)
        db = firestore.client()
        user_doc = db.collection('users').document(user.uid).get()
        if user_doc.exists:
            data = user_doc.to_dict()
            if 'workout_plan' in data:
                print(f"User {email} has a workout_plan:")
                print(json.dumps(data['workout_plan'], indent=2))
            else:
                print(f"User {email} exists but DOES NOT have a workout_plan.")
        else:
            print(f"User document for {email} not found in Firestore.")
    except Exception as e:
        print(f"Error checking user: {e}")

if __name__ == "__main__":
    main()
