import random
import time


class MatchEventEngine:
    """
    REAL-TIME BROADCAST EVENT ENGINE V2
    ESPN / SKY SPORTS style simulation
    """

    def __init__(self):

        self.last_event_time = time.time()

        self.home_momentum = 50
        self.away_momentum = 50

    # =====================================================
    # SAFE CLAMP
    # =====================================================

    def _clamp(self, value):

        return max(0, min(100, value))

    # =====================================================
    # MOMENTUM ENGINE
    # =====================================================

    def update_momentum(
        self,
        home_strength,
        away_strength
    ):

        drift = random.uniform(-5, 5)

        self.home_momentum = self._clamp(
            self.home_momentum
            + (home_strength * 0.1)
            + drift
        )

        self.away_momentum = self._clamp(
            self.away_momentum
            + (away_strength * 0.1)
            - drift
        )

        return {
            "home": round(self.home_momentum, 1),
            "away": round(self.away_momentum, 1)
        }

    # =====================================================
    # BROADCAST EVENT ENGINE
    # =====================================================

    def generate_event(
        self,
        minute,
        home_team,
        away_team
    ):

        events = []

        # =====================================================
        # GOAL EVENT
        # =====================================================

        if random.random() < 0.05:

            scorer = random.choice(
                [home_team, away_team]
            )

            events.append({

                "type": "GOAL",

                "icon": "⚽",

                "team": scorer,

                "minute": minute,

                "impact": "high",

                "urgency": "critical",

                "message": (
                    f"ГОООЛ! {scorer} "
                    f"намира мрежата "
                    f"в {minute}'"
                )
            })

        # =====================================================
        # DANGEROUS ATTACK
        # =====================================================

        if random.random() < 0.15:

            attacking_team = random.choice(
                [home_team, away_team]
            )

            events.append({

                "type": "DANGEROUS_ATTACK",

                "icon": "🚨",

                "team": attacking_team,

                "minute": minute,

                "impact": "medium",

                "urgency": "high",

                "message": (
                    f"Опасна атака за "
                    f"{attacking_team}"
                )
            })

        # =====================================================
        # YELLOW CARD
        # =====================================================

        if random.random() < 0.08:

            team = random.choice(
                [home_team, away_team]
            )

            events.append({

                "type": "YELLOW_CARD",

                "icon": "🟨",

                "team": team,

                "minute": minute,

                "impact": "medium",

                "urgency": "medium",

                "message": (
                    f"Жълт картон за "
                    f"{team}"
                )
            })

        # =====================================================
        # RED CARD
        # =====================================================

        if random.random() < 0.02:

            team = random.choice(
                [home_team, away_team]
            )

            events.append({

                "type": "RED_CARD",

                "icon": "🟥",

                "team": team,

                "minute": minute,

                "impact": "very_high",

                "urgency": "critical",

                "message": (
                    f"Червен картон за "
                    f"{team}!"
                )
            })

        # =====================================================
        # PRESSURE SPIKE
        # =====================================================

        if random.random() < 0.12:

            team = random.choice(
                [home_team, away_team]
            )

            events.append({

                "type": "PRESSURE",

                "icon": "🔥",

                "team": team,

                "minute": minute,

                "impact": "analysis",

                "urgency": "medium",

                "message": (
                    f"{team} засилва "
                    f"натиска"
                )
            })

        # =====================================================
        # MOMENTUM SHIFT
        # =====================================================

        if random.random() < 0.1:

            dominant = (
                home_team
                if self.home_momentum >
                self.away_momentum
                else away_team
            )

            events.append({

                "type": "MOMENTUM_SHIFT",

                "icon": "📈",

                "team": dominant,

                "minute": minute,

                "impact": "analysis",

                "urgency": "low",

                "home_momentum": round(
                    self.home_momentum,
                    1
                ),

                "away_momentum": round(
                    self.away_momentum,
                    1
                ),

                "message": (
                    f"Импулсът в мача "
                    f"се измества към "
                    f"{dominant}"
                )
            })

        # =====================================================
        # NO EVENT FALLBACK
        # =====================================================

        if not events:

            events.append({

                "type": "TACTICAL_PLAY",

                "icon": "🎯",

                "team": "neutral",

                "minute": minute,

                "impact": "low",

                "urgency": "low",

                "message": (
                    "Тактическо надлъгване "
                    "в центъра на терена"
                )
            })

        # =====================================================
        # RETURN MOST IMPORTANT EVENT
        # =====================================================

        priority = {
            "critical": 4,
            "high": 3,
            "medium": 2,
            "low": 1
        }

        events.sort(
            key=lambda x: priority.get(
                x["urgency"],
                0
            ),
            reverse=True
        )

        return events[0]

