import random


class LiveMatchIntelligence:
    """
    STEP 24 - BULGARIAN BROADCAST LIVE ENGINE
    FULL BG LOCALIZATION
    """

    def analyze(self, home_team, away_team, orchestrator_result):

        tactical = orchestrator_result.get("tactical_insight", {})
        momentum = tactical.get("momentum", "Баланс")

        # =====================================================
        # NORMALIZE ENGLISH → BULGARIAN
        # =====================================================

        momentum_translation = {
            "Home Strong": "Домакински натиск",
            "Away Strong": "Гостуващ натиск",
            "Balanced": "Баланс",
            "Even": "Равностойно"
        }

        momentum = momentum_translation.get(momentum, momentum)

        # =====================================================
        # MOMENTUM WEIGHTING
        # =====================================================

        momentum_map = {
            "Домакински натиск": 0.8,
            "Гостуващ натиск": 0.8,
            "Баланс": 0.5,
            "Равностойно": 0.5
        }

        base_pressure = momentum_map.get(momentum, 0.5)

        # =====================================================
        # PRESSURE ENGINE
        # =====================================================

        pressure_roll = random.random()

        if pressure_roll < base_pressure * 0.3:
            pressure = "Екстремен"

        elif pressure_roll < base_pressure * 0.6:
            pressure = "Висок"

        elif pressure_roll < base_pressure * 0.85:
            pressure = "Среден"

        else:
            pressure = "Нисък"

        # =====================================================
        # TEMPO ENGINE
        # =====================================================

        if momentum == "Домакински натиск":
            tempo = random.choice([
                "Бързо",
                "Експлозивно"
            ])

        elif momentum == "Гостуващ натиск":
            tempo = random.choice([
                "Контролирано",
                "Бързо"
            ])

        else:
            tempo = random.choice([
                "Бавно",
                "Контролирано",
                "Бързо"
            ])

        # =====================================================
        # DOMINANCE ENGINE
        # =====================================================

        dominance_weights = [
            home_team,
            away_team,
            "Равностойно"
        ]

        if momentum == "Домакински натиск":
            dominance_weights = [
                home_team,
                home_team,
                "Равностойно"
            ]

        elif momentum == "Гостуващ натиск":
            dominance_weights = [
                away_team,
                away_team,
                "Равностойно"
            ]

        dominance = random.choice(dominance_weights)

        # =====================================================
        # DANGER ENGINE
        # =====================================================

        if pressure in ["Екстремен", "Висок"]:
            danger_team = random.choice([
                home_team,
                away_team
            ])
        else:
            danger_team = random.choice([
                home_team,
                away_team,
                None
            ])

        # =====================================================
        # MATCH STATE ENGINE
        # =====================================================

        if pressure == "Екстремен" and tempo == "Експлозивно":

            match_state = "КРИТИЧЕН МОМЕНТ В МАЧА"

        elif momentum in [
            "Домакински натиск",
            "Гостуващ натиск"
        ] and pressure == "Висок":

            match_state = "ВИСОКА ИНТЕНЗИВНОСТ"

        elif dominance == "Равностойно" and tempo == "Контролирано":

            match_state = "ТАКТИЧЕСКО НАДЛЪГВАНЕ"

        elif pressure == "Нисък":

            match_state = "КОНТРОЛИРАНО ИЗГРАЖДАНЕ"

        else:

            match_state = "ДИНАМИЧЕН МАЧ"

        # =====================================================
        # FINAL OUTPUT
        # =====================================================

        return {
            "pressure": pressure,
            "tempo": tempo,
            "dominance": dominance,
            "dangerous_team": danger_team,
            "match_state": match_state,
            "momentum": momentum,

            # ANALYTICS
            "intensity_score": round(
                base_pressure * random.uniform(0.6, 1.0),
                2
            ),

            "stability_index": round(
                1 - base_pressure,
                2
            )
        }