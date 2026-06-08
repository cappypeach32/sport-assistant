import time


class MatchRotator:
    """
    ESPN-style auto match switching system
    """

    def __init__(self):
        self.index = 0
        self.last_switch = time.time()

    def get_next(self, matches, interval=10):
        """
        Returns next match in rotation (TV-style switching)
        """

        if not matches:
            return None

        now = time.time()

        if now - self.last_switch > interval:
            self.index = (self.index + 1) % len(matches)
            self.last_switch = now

        return matches[self.index]