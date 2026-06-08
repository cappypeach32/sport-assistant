import random
import time


class LiveEventGenerator:

    """
    STEP 25:
    Production-ready Live Event Generator Layer
    """

    def generate(self, home, away, live_state, minute=1):

        events = []

        pressure = live_state.get("pressure", "Low")
        tempo = live_state.get("tempo", "Controlled")
        dominance = live_state.get("dominance", "Even")

        # =====================================================
        # INTENSITY ENGINE (IMPORTANT FIX)
        # =====================================================

        intensity = 0

        if pressure in ["High", "Extreme"]:
            intensity += 1

        if tempo in ["Fast", "Explosive"]:
            intensity += 1

        if dominance != "Even":
            intensity += 1

        # =====================================================
        # MOMENTUM SHIFT
        # =====================================================

        if intensity >= 2 and random.random() < 0.6:
            events.append({
                "type": "momentum_shift",
                "team": dominance if dominance != "Even" else random.choice([home, away]),
                "impact": "HIGH",
                "minute": minute,
                "description": f"Momentum shifting toward {dominance}",
                "intensity": intensity
            })

        # =====================================================
        # GOAL EVENT (CONTROLLED PROBABILITY)
        # =====================================================

        goal_prob = 0.08 + (intensity * 0.05)

        if random.random() < goal_prob:
            events.append({
                "type": "goal",
                "team": random.choice([home, away]),
                "impact": "CRITICAL",
                "minute": minute,
                "description": "⚽ GOAL! Clinical finish after attacking sequence",
                "intensity": intensity
            })

        # =====================================================
        # BIG CHANCE
        # =====================================================

        chance_prob = 0.15 + (tempo == "Fast") * 0.1

        if random.random() < chance_prob:
            events.append({
                "type": "big_chance",
                "team": random.choice([home, away]),
                "impact": "MEDIUM",
                "minute": minute,
                "description": "🔥 Big scoring opportunity created",
                "intensity": intensity
            })

        # =====================================================
        # YELLOW CARD (PRESSURE BASED)
        # =====================================================

        if pressure in ["High", "Extreme"] and random.random() < 0.5:
            events.append({
                "type": "yellow_card",
                "team": random.choice([home, away]),
                "impact": "LOW",
                "minute": minute,
                "description": "🟨 Tactical foul under pressure",
                "intensity": intensity
            })

        # =====================================================
        # RED CARD (RARE + CONTEXTUAL)
        # =====================================================

        if pressure == "Extreme" and intensity == 3 and random.random() < 0.2:
            events.append({
                "type": "red_card",
                "team": random.choice([home, away]),
                "impact": "VERY_HIGH",
                "minute": minute,
                "description": "🟥 RED CARD! Match changing incident",
                "intensity": intensity
            })

        # =====================================================
        # MOMENTUM STATE (GLOBAL FEEL)
        # =====================================================

        if intensity == 0:
            momentum_state = "Stable"
        elif intensity == 1:
            momentum_state = "Controlled Pressure"
        elif intensity == 2:
            momentum_state = "Unstable"
        else:
            momentum_state = "Highly Volatile"

        # =====================================================
        # FINAL OUTPUT (OBS READY)
        # =====================================================

        return {
            "events": events,
            "momentum_state": momentum_state,
            "event_count": len(events),
            "minute": minute,
            "intensity": intensity,
            "timestamp": time.time()
        }