from data.providers.api_football import get_world_cup_fixtures


def get_world_cup_schedule():
    """
    World Cup schedule (clean + engine-safe version)
    """

    data = get_world_cup_fixtures()

    if not data:
        return {
            "total": 0,
            "matches": []
        }

    fixtures = data.get("response", []) or []

    matches = []

    for f in fixtures:
        try:
            home_team = f["teams"]["home"]["name"]
            away_team = f["teams"]["away"]["name"]

            fixture = f.get("fixture", {}) or {}
            status = fixture.get("status", {}).get("short", "NS")

            goals = f.get("goals", {}) or {}

            matches.append({
                "home": home_team,
                "away": away_team,
                "date": fixture.get("date"),
                "status": status,
                "score": {
                    "home": goals.get("home"),
                    "away": goals.get("away")
                }
            })

        except Exception:
            continue

    return {
        "total": len(matches),
        "matches": matches
    }


# -------------------------
# BACKWARD COMPATIBILITY FIX (IMPORTANT)
# -------------------------
def get_worldcup_schedule():
    """
    Alias for old imports (prevents ImportError in main.py)
    """
    return get_world_cup_schedule()