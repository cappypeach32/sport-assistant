import time


class BroadcastIntelligence:
    """
    STEP 22: Option B+ upgrade layer
    - ranks matches
    - stabilizes broadcast selection
    """

    def __init__(self):
        self.current_match = None
        self.last_switch = time.time()

    def score_match(self, match):
        """
        Computes Match Intelligence Score (MIS)
        """

        score = 0

        home = match["home"]
        away = match["away"]

        # 1. rivalry / hype proxy
        score += (len(home) + len(away)) * 0.1

        # 2. big teams bias
        big_teams = ["Brazil", "Germany", "France", "Argentina", "Spain", "England"]

        if home in big_teams:
            score += 2
        if away in big_teams:
            score += 2

        # 3. live status boost
        if match.get("status") == "LIVE":
            score += 3

        # 4. scheduled match stability boost
        if match.get("status") == "NS":
            score += 1

        return score

    def select_match(self, matches, switch_interval=30):
        """
        Stable broadcast selection (no jitter)
        """

        if not matches:
            return None

        now = time.time()
        should_switch = (now - self.last_switch) > switch_interval

        ranked = sorted(matches, key=self.score_match, reverse=True)
        top = ranked[0]

        # stable lock
        if not should_switch and self.current_match:
            return self.current_match

        self.current_match = top
        self.last_switch = now

        return top