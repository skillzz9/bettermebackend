from pydantic import BaseModel

EXERCISE_STANDARDS = {
    "push": {
        "bench press": 1.0,
        "dumbbell bench press": 1.15,
        "incline dumbbell press": 1.25,
        "incline bench press": 1.15,
        "machine chest press": 0.8,
        "overhead press": 1.6,
        "shoulder press": 1.8,
    },
    "pull": {
        "barbell row": 1.0,
        "pull-up": 1.0,
        "pullup": 1.0,
        "lat pulldown": 0.9,
        "cable row": 0.9,
        "dumbbell row": 1.1,
        "chin-up": 0.9,
        "deadlift": 0.6,
    },
    "legs": {
        "barbell squat": 1.0,
        "squat": 1.0,
        "leg press": 0.5,
        "hack squat": 0.7,
        "split squat": 1.8,
        "lunges": 2.0,
        "romanian deadlift": 1.1,
        "leg extension": 3.0,
    }
}

def calculate_strength_scores(plan: dict, bw_lbs: float) -> dict:
    scores = {"push": 0, "pull": 0, "legs": 0}
    max_e1rm = {"push": 0, "pull": 0, "legs": 0}
    
    if not plan or bw_lbs <= 0:
        return scores
        
    for day in plan.get("days", []):
        for ex in day.get("exercises", []):
            name = ex.get("name", "").lower()
            weight = ex.get("target_weight_lbs", 0)
            if not weight or weight == 0:
                continue
                
            reps_str = str(ex.get("reps", "10"))
            try:
                # If "8-10", parse 8. 
                reps = int(reps_str.replace(" ", "").split("-")[0])
            except:
                reps = 10
                
            # Brzycki formula
            e1rm = weight / (1.0278 - (0.0278 * reps))
            if e1rm < 0: 
                e1rm = weight
            
            # Find category and standard
            matched = False
            for cat, standards in EXERCISE_STANDARDS.items():
                if matched: break
                for std_name, multiplier in standards.items():
                    if std_name in name:
                        standardized_e1rm = e1rm * multiplier
                        if standardized_e1rm > max_e1rm[cat]:
                            max_e1rm[cat] = standardized_e1rm
                        matched = True
                        break

    # Normalize to 0-100 based on body weight standards
    push_ratio = max_e1rm["push"] / bw_lbs
    scores["push"] = min(100, int((push_ratio / 2.0) * 100))
    
    pull_ratio = max_e1rm["pull"] / bw_lbs
    scores["pull"] = min(100, int((pull_ratio / 2.0) * 100))
    
    legs_ratio = max_e1rm["legs"] / bw_lbs
    scores["legs"] = min(100, int((legs_ratio / 2.5) * 100))
        
    return scores
