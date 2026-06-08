import random

from data.providers.worldcup import get_world_cup_schedule


# =========================================================
# LEAGUE MATCH PROVIDERS (MOCK / EXTENSIBLE)
# =========================================================

def get_epl_match():
    return {
        "home": "Manchester City",
        "away": "Arsenal",
        "status": "NS",
        "league": "EPL",
        "priority_score": 0.9
    }


def get_la_liga_match():
    return {
        "home": "Real Madrid",
        "away": "Barcelona",
        "status": "NS",
        "league": "LA_LIGA",
        "priority_score": 0.85
    }


def get_ucl_match():
    return {
        "home": "Bayern Munich",
        "away": "PSG",
        "status": "NS",
        "league": "UCL",
        "priority_score": 0.95
    }


def get_world_cup_match():
    data = get_world_cup_schedule()

    if not data:
        return None

    matches = data.get("matches", [])

    if not matches:
        return None

    match = random.choice(matches)
    match["league"] = "WORLD_CUP"
    match["priority_score"] = 1.0

    return match


# =========================================================
# SAFE FALLBACK
# =========================================================
def fallback_match():
    return {
        "home": "Brazil",
        "away": "Germany",
        "status": "NS",
        "league": "FALLBACK",
        "priority_score": 0.1
    }


# =========================================================
# MULTI-LEAGUE SELECTION ENGINE (STEP 26 CORE)
# =========================================================
def get_live_match():
    """
    Advanced multi-league router with priority scoring
    """

    pool = [
        get_world_cup_match(),
        get_epl_match(),
        get_la_liga_match(),
        get_ucl_match()
    ]

    # remove invalid
    pool = [m for m in pool if m]

    if not pool:
        return fallback_match()

    # =====================================================
    # FEATURE SELECTION LOGIC (IMPORTANT UPGRADE)
    # =====================================================
    # instead of pure random → weighted selection
    weights = {
        "WORLD_CUP": 1.0,
        "UCL": 0.9,
        "EPL": 0.85,
        "LA_LIGA": 0.8,
        "FALLBACK": 0.1
    }

    def score(match):
        league = match.get("league", "FALLBACK")
        base = weights.get(league, 0.5)
        priority = match.get("priority_score", 0.5)
        return base * priority

    # sort by score (broadcast logic style)
    pool.sort(key=score, reverse=True)

    # top candidate (ESPN-style featured match)
    best_match = pool[0]

    # small randomness to avoid static behavior
    if random.random() < 0.2:
        return random.choice(pool)

    return best_match