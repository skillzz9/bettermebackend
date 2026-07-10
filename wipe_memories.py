#!/usr/bin/env python3
"""
Wipe a user's memories and/or profile from the database.

Usage:
  python wipe_memories.py                  # wipe memories + profile for local-dev-user
  python wipe_memories.py --user my-id     # target a specific user
  python wipe_memories.py --memories-only  # keep profile, only clear memories
  python wipe_memories.py --profile-only   # keep memories, only clear profile
"""

import argparse
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "betterme.db"


def wipe(user_id: str, memories: bool, profile: bool) -> None:
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        if memories:
            cur = conn.execute(
                "DELETE FROM memories WHERE user_id = ?", (user_id,)
            )
            print(f"Deleted {cur.rowcount} memory row(s) for '{user_id}'")

        if profile:
            cur = conn.execute(
                "DELETE FROM profiles WHERE user_id = ?", (user_id,)
            )
            print(f"Deleted {cur.rowcount} profile row(s) for '{user_id}'")

        # Always wipe habits, completions and streak so the dashboard resets too
        cur = conn.execute("DELETE FROM user_habits WHERE user_id = ?", (user_id,))
        print(f"Deleted {cur.rowcount} habit row(s) for '{user_id}'")
        cur = conn.execute("DELETE FROM daily_completions WHERE user_id = ?", (user_id,))
        print(f"Deleted {cur.rowcount} completion row(s) for '{user_id}'")
        cur = conn.execute("DELETE FROM streaks WHERE user_id = ?", (user_id,))
        print(f"Deleted {cur.rowcount} streak row(s) for '{user_id}'")

        conn.commit()
    finally:
        conn.close()

    print("Done — restart the conversation to start fresh.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wipe BetterMe user data for testing.")
    parser.add_argument("--user", default="local-dev-user", help="User ID to wipe")
    parser.add_argument("--memories-only", action="store_true", help="Only wipe memories")
    parser.add_argument("--profile-only", action="store_true", help="Only wipe profile")
    args = parser.parse_args()

    do_memories = not args.profile_only
    do_profile = not args.memories_only

    wipe(args.user, memories=do_memories, profile=do_profile)
