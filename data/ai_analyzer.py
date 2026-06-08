def normalize_form(form):
    """
    Accepts:
    - "W-D-L"
    - ["W","D","L"]
    - {"form":"W-D-L"}
    """

    if not form:
        return []

    if isinstance(form, dict):
        form = form.get("form", "")

    if isinstance(form, list):
        return form

    if isinstance(form, str):
        return form.split("-")

    return []


def calculate_form_score(form):
    """
    W = 3, D = 1, L = 0
    """

    results = normalize_form(form)

    score = 0

    for r in results:
        r = r.strip().upper()

        if r == "W":
            score += 3
        elif r == "D":
            score += 1
        elif r == "L":
            score += 0

    return score


def generate_ai_analysis(team1, team2):

    t1_score = calculate_form_score(team1.get("form", {}))
    t2_score = calculate_form_score(team2.get("form", {}))

    total = t1_score + t2_score

    # -------------------------
    # SAFETY FIX (avoid divide by zero)
    # -------------------------
    if total == 0:
        t1_win = 33.3
        t2_win = 33.3
        draw = 33.4
    else:
        t1_win = round((t1_score / total) * 100, 1)
        t2_win = round((t2_score / total) * 100, 1)

        # more realistic draw model
        draw = round(max(5.0, 100 - (t1_win + t2_win)), 1)

    # -------------------------
    # MOMENTUM LOGIC
    # -------------------------
    if t1_score > t2_score:
        momentum = f"{team1['name']} slight advantage"
    elif t2_score > t1_score:
        momentum = f"{team2['name']} slight advantage"
    else:
        momentum = "Balanced tactical matchup"

    # -------------------------
    # CLEAN NARRATIVE
    # -------------------------
    narrative = f"""
This is a competitive matchup between {team1['name']} and {team2['name']}.

Recent form analysis shows:
- {team1['name']} score: {t1_score}
- {team2['name']} score: {t2_score}

Momentum: {momentum}
""".strip()

    # -------------------------
    # STRUCTURED INSIGHT
    # -------------------------
    insight = {
        "summary": f"{team1['name']} vs {team2['name']} tactical balance analysis",
        "t1_score": t1_score,
        "t2_score": t2_score,
        "key_factor": "form consistency vs match control",
        "dominant_side": team1["name"] if t1_score > t2_score else team2["name"] if t2_score > t1_score else "Even"
    }

    # -------------------------
    # FINAL OUTPUT
    # -------------------------
    return {
        "form_score": {
            team1["name"]: t1_score,
            team2["name"]: t2_score
        },

        "prediction": {
            team1["name"]: t1_win,
            team2["name"]: t2_win,
            "draw": draw
        },

        "momentum": momentum,
        "narrative": narrative,
        "insight": insight
    }