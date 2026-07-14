#!/usr/bin/env python3
"""
Wipe ALL users from Firebase Firestore.

Usage:
  python wipe_firebase.py              # wipes everything, prompts for confirmation
  python wipe_firebase.py --yes        # skip confirmation prompt
"""

import argparse
import json
import os
import sys

import firebase_admin
from firebase_admin import auth, credentials, firestore

# ---- Firebase init -----------------------------------------------------------

if not firebase_admin._apps:
    json_str = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    if json_str:
        cred = credentials.Certificate(json.loads(json_str))
    else:
        cred_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "serviceAccountKey.json")
        cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)

db = firestore.client()

SUBCOLLECTIONS = ["memories", "habits", "daily_completions", "streaks", "behavior_events"]


def delete_subcollections(user_ref) -> None:
    for sub in SUBCOLLECTIONS:
        docs = user_ref.collection(sub).stream()
        for doc in docs:
            doc.reference.delete()


def wipe_all(yes: bool) -> None:
    users = list(db.collection("users").stream())

    if not users:
        print("No users found in Firestore — checking Auth...")
        _wipe_auth_users([])
        return

    print(f"Found {len(users)} user(s):")
    for u in users:
        d = u.to_dict()
        name = d.get("profile", {}).get("name") or "(no name)"
        print(f"  - {u.id}  [{name}]")

    if not yes:
        answer = input("\nWipe ALL of this data? This cannot be undone. Type 'yes' to confirm: ")
        if answer.strip().lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    for u in users:
        user_ref = db.collection("users").document(u.id)
        delete_subcollections(user_ref)
        user_ref.delete()
        try:
            auth.delete_user(u.id)
            print(f"Deleted user {u.id} (Firestore + Auth)")
        except Exception:
            print(f"Deleted user {u.id} (Firestore only — not in Auth)")

    _wipe_auth_users(users)
    print(f"\nDone — Firebase is clean.")


def _wipe_auth_users(firestore_users) -> None:
    known_ids = {u.id for u in firestore_users}
    page = auth.list_users()
    count = 0
    while page:
        for au in page.users:
            auth.delete_user(au.uid)
            label = "(already wiped from Firestore)" if au.uid in known_ids else "(Auth-only)"
            print(f"Deleted Auth user {au.uid} {au.email or ''} {label}")
            count += 1
        page = page.get_next_page()
    if count == 0:
        print("No Auth users found either. All clean.")
    else:
        print(f"Deleted {count} Auth user(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wipe all BetterMe Firebase users.")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    wipe_all(args.yes)
