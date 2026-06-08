import random

def select_featured_match(fixtures):
    """
    ESPN-style featured match selector (WORLD CUP READY)
    """

    if not fixtures:
        return None

    def score_match(m):
        # basic intelligence scoring (step 17 → real stats upgrade)
        importance = random.uniform(0.4, 1.0)

        # boost if big teams exist
        big_teams = ["Brazil", "Germany", "Argentina", "France", "Spain", "England"]

        home = m["teams"]["home"]["name"]
        away = m["teams"]["away"]["name"]

        boost = 1.0
        if home in big_teams:
            boost += 0.2
        if away in big_teams:
            boost += 0.2

        return importance * boost

    ranked = sorted(fixtures, key=score_match, reverse=True)

    return ranked[0]