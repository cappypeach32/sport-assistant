import time

class MatchRotator:
    """
    ESPN-style match rotation system
    Used for WORLD CUP dashboard auto-switching
    """

    def __init__(self):
        self.index = 0
        self.last_switch = time.time()

    def get_next(self, matches, interval=12):
        """
        Returns next match based on time rotation
        """

        if not matches:
            return None

        now = time.time()

        if now - self.last_switch > interval:
            self.index = (self.index + 1) % len(matches)
            self.last_switch = now

        return matches[self.index]