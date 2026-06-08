from data.providers.api_football import get_fixtures


def get_match_context(team1_id, team2_id):
    """
    CENTRAL MATCH DATA LAYER (REAL DATA ONLY)
    """

    t1_matches = get_fixtures(team1_id)
    t2_matches = get_fixtures(team2_id)

    return {
        "team1_matches": t1_matches,
        "team2_matches": t2_matches
    }