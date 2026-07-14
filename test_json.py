import re
import json

text = """Here is the JSON:
```json
{
  "reply": "Your shoulders look great. Here are some exercises to improve them.",
  "suggested_exercises": [
    {
      "name": "Lateral Raises",
      "reason": "Builds shoulder width",
      "target_muscle": "Deltoids",
      "recommended_split": "Push Day",
      "sets": "3",
      "reps": "12-15"
    }
  ]
}
```
"""

def parse_llm_json(text: str) -> dict:
    text = text.strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception as e:
            print(f"Failed: {e}")
            return {}
    return {}

print(parse_llm_json(text))
