def generate_live_match_state(team1, team2, minute, score):

    """
    LIVE MATCH ENGINE v1
    Simulates live tactical momentum and commentary logic
    """

    home_score = score.get("home", 0)
    away_score = score.get("away", 0)

    # -------------------------
    # ⚡ MOMENTUM DETECTION
    # -------------------------
    if home_score > away_score:
        momentum = f"{team1['name']} controlling the match"

    elif away_score > home_score:
        momentum = f"{team2['name']} controlling the match"

    else:
        momentum = "Match remains balanced"

    # -------------------------
    # 🔥 MATCH TEMPERATURE
    # -------------------------
    if minute < 15:
        temperature = "Cautious opening phase"

    elif minute < 45:
        temperature = "Match intensity increasing"

    elif minute < 75:
        temperature = "Tactical pressure rising"

    else:
        temperature = "High pressure final phase"

    # -------------------------
    # 🎙 LIVE COMMENTARY
    # -------------------------
    commentary = f"""
LIVE MATCH UPDATE

Minute: {minute}

Score:
{team1['name']} {home_score} - {away_score} {team2['name']}

Momentum:
{momentum}

Match State:
{temperature}
"""

    # -------------------------
    # 🚨 DANGER LEVEL
    # -------------------------
    goal_difference = abs(home_score - away_score)

    if goal_difference >= 3:
        danger = "Dominant performance emerging"

    elif goal_difference == 2:
        danger = "Strong momentum advantage"

    elif goal_difference == 1:
        danger = "Tightly contested match"

    else:
        danger = "Any team can win"

    # -------------------------
    # 🎯 FINAL OUTPUT
    # -------------------------
    return {
        "minute": minute,

        "score": {
            team1["name"]: home_score,
            team2["name"]: away_score
        },

        "momentum": momentum,

        "temperature": temperature,

        "danger_level": danger,

        "commentary": commentary,

        "obs_live_overlay": {
            "minute": minute,
            "scoreline": f"{home_score}-{away_score}",
            "momentum": momentum,
            "danger": danger
        }
    }