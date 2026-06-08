import time

class BroadcastDirector:
    """
    ESPN-style broadcast control center
    Controls WHAT is currently ON AIR
    """

    def __init__(self):
        self.current_match = None
        self.last_switch = time.time()
        self.rotation_interval = 20  # seconds

    def select_on_air_match(self, matches):
        """
        Controls stable rotation of featured match
        """

        if not matches:
            return None

        now = time.time()

        # keep current match if interval not passed
        if self.current_match and (now - self.last_switch) < self.rotation_interval:
            return self.current_match

        # pick next best match (simple stable selection)
        self.current_match = self._rank(matches)[0]
        self.last_switch = now

        return self.current_match

    def _rank(self, matches):
        """
        simple ESPN ranking logic (STEP 19 will upgrade AI scoring)
        """

        def score(m):
            base = 0

            if m.get("score"):
                base += 2

            if m.get("status") == "NS":
                base += 1

            base += len(m.get("home", "")) * 0.1
            base += len(m.get("away", "")) * 0.1

            return base

        return sorted(matches, key=score, reverse=True)