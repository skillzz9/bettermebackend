"""Generates habits.csv — Adam's curated habit library.

Each habit is tagged so it can power the "ladder" (level 1 = tiny starter,
5 = advanced), be filtered by pillar, and drive the check-off UI. Adam picks
a fitting rung from this library, then personalizes the wording per user.

Columns: id, pillar, category, habit, level, minutes, cue, identity
  level   1..5  difficulty rung (1 = under 2 min, 5 = 30+ min / advanced)
  minutes approx time to do it
  cue     a suggested anchor / how to start it
  identity the kind of person the habit builds

Run:  python generate_habits.py   ->  writes habits.csv next to this file
"""

import csv
from pathlib import Path

# (habit, category, level, minutes, cue, identity)
HEALTH = [
    ("Make your bed", "morning", 1, 1, "Right after you stand up", "someone who starts the day with order"),
    ("Drink a glass of water when you wake up", "hydration", 1, 1, "Keep a glass by your bed", "someone who takes care of their body"),
    ("Open the curtains for daylight", "energy", 1, 1, "First thing after waking", "someone who lets light in"),
    ("Stand up and stretch for 30 seconds", "movement", 1, 1, "After you brush your teeth", "someone who moves their body"),
    ("Take 3 slow deep breaths", "mind-body", 1, 1, "Before you get out of bed", "someone who starts calm"),
    ("Put on your walking shoes", "movement", 1, 1, "After breakfast", "someone who moves daily"),
    ("Fill a water bottle for the day", "hydration", 1, 2, "While the kettle boils", "someone who stays hydrated"),
    ("Step outside for one minute of fresh air", "energy", 1, 2, "Mid-morning", "someone who gets fresh air"),
    ("Do 5 squats", "movement", 2, 2, "While the coffee brews", "someone who moves their body"),
    ("Eat one piece of fruit", "nutrition", 2, 2, "With breakfast", "someone who nourishes themselves"),
    ("Walk for 5 minutes", "movement", 2, 5, "After lunch", "someone who walks daily"),
    ("Do 10 push-ups (on knees is fine)", "movement", 2, 3, "Before your shower", "someone who builds strength"),
    ("Stretch your back and neck", "mind-body", 2, 3, "Mid-afternoon slump", "someone who cares for their body"),
    ("Add one vegetable to a meal", "nutrition", 2, 3, "At dinner", "someone who eats well"),
    ("Put your phone away 10 min before bed", "sleep", 2, 10, "When you start your bedtime routine", "someone who sleeps well"),
    ("Take a 10-minute walk", "movement", 3, 10, "After lunch", "someone who walks daily"),
    ("Do a 10-minute home workout", "movement", 3, 10, "After you change out of work clothes", "someone who trains"),
    ("Cook one simple meal at home", "nutrition", 3, 20, "When you get hungry for dinner", "someone who feeds themselves well"),
    ("Go to bed at a consistent time", "sleep", 3, 15, "When your wind-down alarm goes off", "someone who protects their sleep"),
    ("Stretch or do mobility for 10 minutes", "mind-body", 3, 10, "In the evening", "someone who stays limber"),
    ("Walk 20 minutes outside", "movement", 4, 20, "Mid-morning or after dinner", "someone who moves every day"),
    ("Do a 20-minute strength session", "movement", 4, 20, "After work", "someone who gets stronger"),
    ("Meal-prep one healthy lunch", "nutrition", 4, 30, "Sunday afternoon", "someone who plans their nutrition"),
    ("No screens for the last 30 min before bed", "sleep", 4, 30, "After your evening routine", "someone who rests deeply"),
    ("Go for a 30-minute run", "movement", 5, 30, "First thing, or after work", "a runner"),
    ("Complete a full 45-minute workout", "movement", 5, 45, "Your set gym time", "an athlete in progress"),
    ("Cook all meals from whole foods today", "nutrition", 5, 60, "Across the day", "someone who fuels their body"),
]

CAREER = [
    ("Write down your top task for today", "planning", 1, 1, "Before you open email", "someone who works with intention"),
    ("Open the doc you're avoiding", "focus", 1, 1, "First thing at your desk", "someone who faces the hard thing"),
    ("Read one page of a work/skill book", "learning", 1, 2, "With your morning coffee", "a lifelong learner"),
    ("Tidy your desk for 2 minutes", "environment", 1, 2, "Before you start work", "someone who works in clarity"),
    ("Write one sentence on your side project", "creativity", 1, 2, "In the evening", "a creator"),
    ("Check your bank balance", "finance", 1, 1, "With morning coffee", "someone in control of their money"),
    ("Note one thing you learned today", "learning", 1, 2, "End of the workday", "someone who grows at work"),
    ("Do 5 minutes of focused work, no phone", "focus", 2, 5, "Right after you sit down", "someone who focuses deeply"),
    ("Plan tomorrow's 3 priorities", "planning", 2, 5, "Before you log off", "someone who works on purpose"),
    ("Watch one short tutorial", "learning", 2, 10, "Lunch break", "someone building a skill"),
    ("Read 5 pages of a book in your field", "learning", 2, 10, "On your commute or coffee break", "an expert in the making"),
    ("Save a small amount of money", "finance", 2, 2, "On payday", "someone who builds wealth slowly"),
    ("Reach out to one person in your industry", "network", 2, 5, "Late morning", "someone who builds their network"),
    ("Do 25 minutes of deep, distraction-free work", "focus", 3, 25, "Your first work block", "someone who does focused work"),
    ("Practice a skill for 15 minutes", "skill", 3, 15, "After dinner", "someone mastering their craft"),
    ("Review your weekly goals", "planning", 3, 10, "Monday morning", "someone with direction"),
    ("Log today's spending", "finance", 3, 10, "Before bed", "someone who knows their numbers"),
    ("Work on your side project for 20 minutes", "creativity", 3, 20, "Your protected evening slot", "a builder"),
    ("Two 25-minute focus blocks with a break", "focus", 4, 60, "Morning", "someone who protects deep work"),
    ("Study/practice a skill for 30 minutes", "skill", 4, 30, "Set study time", "someone leveling up"),
    ("Draft a plan for a goal you keep postponing", "planning", 4, 30, "Weekend morning", "someone who moves goals forward"),
    ("Ship one small piece of the side project", "creativity", 5, 60, "Focused evening", "someone who ships"),
    ("Do a 90-minute deep-work session", "focus", 5, 90, "Your peak-energy window", "a deep worker"),
    ("Review and adjust your monthly budget", "finance", 5, 45, "Start of the month", "someone who runs their finances"),
]

SOCIAL = [
    ("Text one person 'thinking of you'", "connection", 1, 1, "Mid-morning", "someone who stays connected"),
    ("Smile and greet someone", "connection", 1, 1, "First interaction of the day", "someone warm to others"),
    ("Send one message to a friend you miss", "connection", 1, 2, "During a break", "a good friend"),
    ("Give one genuine compliment", "kindness", 1, 1, "When you notice something", "someone who lifts others"),
    ("Say thank you to someone specifically", "kindness", 1, 1, "When someone helps you", "someone who appreciates people"),
    ("React to a friend's post thoughtfully", "connection", 1, 2, "While scrolling", "a present friend"),
    ("Ask someone how their day really is", "communication", 2, 3, "Over dinner or a call", "someone who listens"),
    ("Reply to a message you've been putting off", "communication", 2, 3, "Evening", "someone who shows up for people"),
    ("Do one small favor for someone", "kindness", 2, 5, "When you see the chance", "a giving person"),
    ("Call a family member", "connection", 2, 10, "After dinner", "someone close to their family"),
    ("Have a 10-minute real conversation", "connection", 3, 10, "Lunch or evening", "someone who connects deeply"),
    ("Make plans to see a friend", "connection", 3, 10, "This week", "someone who nurtures friendships"),
    ("Listen fully to someone without your phone", "communication", 3, 15, "During any conversation", "a present listener"),
    ("Check in on someone going through a hard time", "kindness", 3, 10, "When they cross your mind", "a caring friend"),
    ("Meet a friend for coffee or a walk", "connection", 4, 30, "Weekend", "someone who invests in relationships"),
    ("Host or join a small get-together", "community", 4, 60, "This weekend", "someone who brings people together"),
    ("Have an honest conversation you've avoided", "communication", 4, 20, "When it feels right", "someone brave in relationships"),
    ("Volunteer or help in your community", "community", 5, 90, "Weekend", "someone who gives back"),
    ("Reconnect with someone you've lost touch with", "connection", 5, 30, "This week", "someone who mends bonds"),
]

SPIRITUAL = [
    ("Name one thing you're grateful for", "gratitude", 1, 1, "Before you get out of bed", "someone who notices the good"),
    ("Take 3 mindful breaths", "mindfulness", 1, 1, "When you feel rushed", "someone who stays grounded"),
    ("Sit in silence for one minute", "stillness", 1, 1, "Before your day starts", "someone at peace with stillness"),
    ("Write one line in a journal", "reflection", 1, 2, "End of the day", "someone who reflects"),
    ("Look up at the sky for a moment", "nature", 1, 1, "When you step outside", "someone who notices wonder"),
    ("Set one gentle intention for the day", "meaning", 1, 2, "With your morning drink", "someone who lives on purpose"),
    ("Put your hand on your heart and pause", "mindfulness", 1, 1, "In a stressful moment", "someone kind to themselves"),
    ("Write 3 things you're grateful for", "gratitude", 2, 3, "Before bed", "someone who counts their blessings"),
    ("Meditate for 3 minutes", "mindfulness", 2, 3, "After you wake", "someone who tends their mind"),
    ("Step outside and notice 5 things", "nature", 2, 5, "Mid-day", "someone present in the world"),
    ("Reflect on one thing that went well", "reflection", 2, 3, "End of the day", "someone who honors small wins"),
    ("Read a short passage that inspires you", "meaning", 2, 5, "Morning or night", "someone who feeds their spirit"),
    ("Meditate for 10 minutes", "mindfulness", 3, 10, "Morning", "someone with a calm mind"),
    ("Journal about how you're really feeling", "reflection", 3, 10, "Evening", "someone honest with themselves"),
    ("Take a slow walk with no phone", "nature", 3, 15, "After dinner", "someone who finds peace outdoors"),
    ("Do a short act of kindness with no reward", "meaning", 3, 10, "When the chance appears", "someone who lives their values"),
    ("Sit quietly and breathe for 15 minutes", "stillness", 4, 15, "Morning or night", "someone who cultivates stillness"),
    ("Write a reflection on your week", "reflection", 4, 20, "Sunday evening", "someone who lives examined"),
    ("Spend 30 minutes in nature", "nature", 4, 30, "Weekend", "someone connected to the natural world"),
    ("Do a 20-minute guided meditation", "mindfulness", 5, 20, "Set daily time", "someone with a deep practice"),
    ("Take a tech-free hour for reflection", "stillness", 5, 60, "Weekend morning", "someone who protects their inner life"),
    ("Volunteer time toward something meaningful", "meaning", 5, 90, "Weekend", "someone who serves a purpose"),
]

PILLARS = {
    "health": HEALTH,
    "career": CAREER,
    "social": SOCIAL,
    "spiritual": SPIRITUAL,
}


def main() -> None:
    out = Path(__file__).parent / "habits.csv"
    rows = []
    hid = 1
    for pillar, habits in PILLARS.items():
        for habit, category, level, minutes, cue, identity in habits:
            rows.append(
                {
                    "id": hid,
                    "pillar": pillar,
                    "category": category,
                    # A "family" is a progression ladder. Habits in the same
                    # family are the same theme at different difficulties, so
                    # leveling up = same family, next-higher level.
                    "family": f"{pillar}:{category}",
                    "habit": habit,
                    "level": level,
                    "minutes": minutes,
                    "cue": cue,
                    "identity": identity,
                }
            )
            hid += 1

    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["id", "pillar", "category", "family", "habit", "level", "minutes", "cue", "identity"],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} habits to {out}")


if __name__ == "__main__":
    main()
