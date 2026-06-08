import random
import time


class LiveEventGenerator:

    def __init__(self):
        self.last_event_time = 0

    def generate(self, home, away, live_state):

        events = []

        momentum = live_state.get("momentum", "Balanced")
        pressure = live_state.get("pressure", "Balanced")
        tempo = live_state.get("tempo", "Controlled")

        # =====================================================
        # MOMENTUM SHIFT EVENT
        # =====================================================
        if momentum == "High" or pressure == "Extreme":

            events.append({
                "type": "momentum_shift",
                "team": random.choice([home, away]),
                "strength": random.choice(["small", "medium", "major"]),
                "message": "Momentum is shifting rapidly!"
            })

        # =====================================================
        # CHANCE EVENT
        # =====================================================
        if tempo in ["Fast", "Explosive"]:

            events.append({
                "type": "chance",
                "team": random.choice([home, away]),
                "probability": round(random.uniform(0.2, 0.85), 2),
                "message": "Dangerous attacking opportunity!"
            })

        # =====================================================
        # GOAL EVENT (RARE)
        # =====================================================
        if random.random() < 0.08:

            events.append({
                "type": "goal",
                "team": random.choice([home, away]),
                "minute": random.randint(1, 90),
                "message": "GOAL!!! Incredible finish!"
            })

        # =====================================================
        # CARD EVENT
        # =====================================================
        if random.random() < 0.12:

            events.append({
                "type": "card",
                "team": random.choice([home, away]),
                "color": random.choice(["yellow", "red"]),
                "message": "Referee issues a card!"
            })

        return {
            "timestamp": time.time(),
            "events": events
        }