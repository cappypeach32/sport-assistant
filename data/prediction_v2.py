def normalize_form(form):
    """
    Supports:
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


def calculate_form_weights(form):
    """
    Weighted recent performance model
    """

    results = normalize_form(form)

    if not results:
        return 0

    score = 0
    weight = 1.0

    # reverse = recent matches stronger impact
    for r in reversed(results):

        r = r.upper().strip()

        if r == "W":
            score += 3 * weight
        elif r == "D":
            score += 1 * weight
        elif r == "L":
            score += 0

        weight += 0.15  # smoother scaling

    return round(score, 2)


def attack_strength(form):
    """
    Safe attacking index
    """

    if not form:
        return 0.5  # neutral baseline

    goals = form.get("goals_scored", 0) or 0
    matches = form.get("matches_count", 1) or 1

    return goals / matches


def defense_strength(form):
    """
    Lower is better (goals conceded per match)
    """

    if not form:
        return 0.5

    goals = form.get("goals_conceded", 0) or 0
    matches = form.get("matches_count", 1) or 1

    return goals / matches


def generate_prediction_v2(team1, team2):

    t1_form = team1.get("form", {})
    t2_form = team2.get("form", {})

    # -------------------------
    # FORM SCORE
    # -------------------------
    t1_score = calculate_form_weights(t1_form)
    t2_score = calculate_form_weights(t2_form)

    # -------------------------
    # ATTACK / DEFENSE
    # -------------------------
    t1_attack = attack_strength(t1_form)
    t2_attack = attack_strength(t2_form)

    t1_defense = defense_strength(t1_form)
    t2_defense = defense_strength(t2_form)

    # -------------------------
    # NORMALIZED POWER MODEL
    # -------------------------
    t1_power = (t1_score * 0.6) + (t1_attack * 10) - (t1_defense * 8)
    t2_power = (t2_score * 0.6) + (t2_attack * 10) - (t2_defense * 8)

    # safety floor
    t1_power = max(t1_power, 0.1)
    t2_power = max(t2_power, 0.1)

    total = t1_power + t2_power

    # -------------------------
    # PROBABILITIES
    # -------------------------
    t1_win = round((t1_power / total) * 100, 1)
    t2_win = round((t2_power / total) * 100, 1)

    draw = round(max(5, 100 - (t1_win + t2_win)), 1)

    # normalize if overflow
    if t1_win + t2_win + draw > 100:
        scale = 100 / (t1_win + t2_win + draw)
        t1_win *= scale
        t2_win *= scale
        draw *= scale

    # -------------------------
    # MOMENTUM
    # -------------------------
    if t1_power > t2_power * 1.15:
        momentum = f"{team1['name']} strong advantage"
    elif t2_power > t1_power * 1.15:
        momentum = f"{team2['name']} strong advantage"
    else:
        momentum = "Balanced match"

    # -------------------------
    # OBS FRIENDLY OUTPUT
    # -------------------------
    formatted = f"{team1['name']} {t1_win:.1f}% | {team2['name']} {t2_win:.1f}% | Draw {draw:.1f}%"

    # -------------------------
    # INSIGHT LAYER
    # -------------------------
    insight = {
        "summary": "Form + attack + defense weighted model",
        "t1_power": round(t1_power, 2),
        "t2_power": round(t2_power, 2),
        "key_factor": "attack efficiency vs defensive stability",
        "dominant_side": team1["name"] if t1_power > t2_power else team2["name"]
    }

    # -------------------------
    # FINAL OUTPUT
    # -------------------------
    return {
        "power_index": {
            team1["name"]: round(t1_power, 2),
            team2["name"]: round(t2_power, 2)
        },

        "prediction": {
            team1["name"]: round(t1_win, 1),
            team2["name"]: round(t2_win, 1),
            "draw": round(draw, 1)
        },

        "formatted": formatted,
        "momentum": momentum,
        "insight": insight
    }