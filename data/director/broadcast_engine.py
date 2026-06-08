import time


class FeaturedMatchEngine:
    """
    ESPN-style featured match selector (stable + deterministic)
    """

    def __init__(self):
        self.last_match_id = None
        self.last_switch = time.time()

    def _score_match(self, m):
        home = m["home"]
        away = m["away"]

        score = 0

        score += (len(home) + len(away)) * 0.1

        if m.get("score"):
            score += 2

        if m.get("status") == "NS":
            score += 1

        return score

    def select_featured_match(self, fixtures, interval=30):

        if not fixtures:
            return None

        now = time.time()
        should_switch = (now - self.last_switch) > interval

        ranked = sorted(
            fixtures,
            key=self._score_match,
            reverse=True
        )

        top_match = ranked[0] if ranked else None

        if not top_match:
            return None

        match_id = top_match.get("id")

        if not should_switch and self.last_match_id:
            return next(
                (m for m in fixtures if m.get("id") == self.last_match_id),
                top_match
            )

        self.last_match_id = match_id
        self.last_switch = now

        return top_match


# =========================================================
# ✅ WRAPPER (THIS FIXES YOUR IMPORT ERROR)
# =========================================================

_engine = FeaturedMatchEngine()

def select_featured_match(fixtures):
    """
    Backwards-compatible function for main.py
    """
    return _engine.select_featured_match(fixtures)