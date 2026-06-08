from data.football_data import get_team_matches


def calculate_form(matches):
    """
    Returns last 5 matches form like: W-W-L-D-W
    """

    form = []

    for match in matches[:5]:

        home_score = match.get("home_score", 0)
        away_score = match.get("away_score", 0)

        is_home = match.get("is_home", False)

        if is_home:
            if home_score > away_score:
                form.append("W")
            elif home_score < away_score:
                form.append("L")
            else:
                form.append("D")
        else:
            if away_score > home_score:
                form.append("W")
            elif away_score < home_score:
                form.append("L")
            else:
                form.append("D")

    return "-".join(form)


def get_team_form(team_id):
    """
    Safe version that handles API response formats
    """

    raw = get_team_matches(team_id)

    # -------------------------
    # normalize response
    # -------------------------
    if isinstance(raw, dict):

        if "response" in raw:
            matches = raw["response"]

        elif "matches" in raw:
            matches = raw["matches"]

        elif "data" in raw:
            matches = raw["data"]

        else:
            matches = []

    elif isinstance(raw, list):
        matches = raw

    else:
        matches = []

    if not matches:
        return {
            "form": None,
            "goals_scored": 0,
            "goals_conceded": 0,
            "matches_count": 0
        }

    form = []
    goals_scored = 0
    goals_conceded = 0

    for match in matches[:5]:

        home = match.get("home_score", 0)
        away = match.get("away_score", 0)

        goals_scored += home
        goals_conceded += away

        if home > away:
            form.append("W")
        elif home < away:
            form.append("L")
        else:
            form.append("D")

    return {
        "form": "-".join(form),
        "goals_scored": goals_scored,
        "goals_conceded": goals_conceded,
        "matches_count": len(matches[:5])
    }