from datetime import datetime, timezone

# =====================================================
# PRIORITY COMPETITIONS
# =====================================================

TOP_LEAGUES = {
    1:   100,  # FIFA World Cup
    2:   95,   # UEFA Champions League
    4:   92,   # UEFA Euro
    39:  90,   # Premier League
    140: 88,   # La Liga
    135: 85,   # Serie A
    78:  85,   # Bundesliga
    61:  84,   # Ligue 1
    3:   82,   # UEFA Europa League
    172: 78,   # Bulgaria First League
    174: 72,   # Bulgaria Cup
}

# =====================================================
# BIG CLUBS
# =====================================================

BIG_CLUBS = {
    "Real Madrid",
    "Barcelona",
    "Manchester City",
    "Manchester United",
    "Liverpool",
    "Arsenal",
    "Chelsea",
    "Bayern Munich",
    "PSG",
    "Juventus",
    "Inter",
    "AC Milan"
}

# =====================================================
# DERBY MATCHES
# =====================================================

DERBIES = [
    ("Real Madrid", "Barcelona"),
    ("Manchester United", "Liverpool"),
    ("Arsenal", "Tottenham"),
    ("Inter", "AC Milan"),
    ("Bayern Munich", "Dortmund"),
]

# =====================================================
# MATCH SELECTION ENGINE
# =====================================================

class MatchSelectionEngine:

    # =================================================
    # DERBY DETECTION
    # =================================================

    def is_derby(self, home, away):

        for a, b in DERBIES:

            if (
                (home == a and away == b)
                or
                (home == b and away == a)
            ):
                return True

        return False

    # =================================================
    # SCORE MATCH
    # =================================================

    def score_match(self, match):

        score = 0

        competition_id = match.get("competition_id")
        competition = match.get("competition", "")

        home = match.get("home", "")
        away = match.get("away", "")

        status = (match.get("status") or "").lower()

        # =============================================
        # COMPETITION IMPORTANCE
        # =============================================

        if competition_id in TOP_LEAGUES:
            score += TOP_LEAGUES[competition_id]
        else:
            score += 40

        # =============================================
        # BIG CLUB BOOST
        # =============================================

        if home in BIG_CLUBS:
            score += 15

        if away in BIG_CLUBS:
            score += 15

        # =============================================
        # DERBY BOOST
        # =============================================

        if self.is_derby(home, away):
            score += 40

        # =============================================
        # LIVE PRIORITY
        # =============================================

        LIVE_STATES = [
            "live", "1st half", "2nd half", "first half", "second half",
            "halftime", "half time", "ht", "in progress", "extra time",
        ]

        if any(x in status for x in LIVE_STATES):
            score += 100

        elif "finished" in status:
            score -= 50

        else:
            # upcoming
            score += 60

        # =============================================
        # TIME PROXIMITY BOOST
        # =============================================

        try:

            start_time = match.get("start_time")

            if start_time:

                dt = datetime.fromisoformat(
                    start_time.replace("Z", "+00:00")
                )

                now = datetime.now(timezone.utc)

                diff_hours = abs(
                    (dt - now).total_seconds()
                ) / 3600

                # closer kickoff = higher priority
                proximity_boost = max(0, 48 - diff_hours)

                score += proximity_boost

        except Exception:
            pass

        # =============================================
        # WORLD CUP BOOST
        # =============================================

        if "world cup" in competition.lower():
            score += 50

        return round(score, 2)

    # =================================================
    # SELECT BEST MATCH
    # =================================================

    def select_best_match(self, fixtures):

        if not fixtures:
            return None

        ranked = []

        for match in fixtures:

            try:

                match["broadcast_score"] = self.score_match(match)

                ranked.append(match)

            except Exception:
                continue

        if not ranked:
            return None

        ranked.sort(
            key=lambda x: x["broadcast_score"],
            reverse=True
        )

        return ranked[0]

    # =================================================
    # TOP MATCHES
    # =================================================

    def get_top_matches(self, fixtures, limit=5):

        ranked = []

        for match in fixtures:

            try:

                match["broadcast_score"] = self.score_match(match)

                ranked.append(match)

            except Exception:
                continue

        ranked.sort(
            key=lambda x: x["broadcast_score"],
            reverse=True
        )

        return ranked[:limit]