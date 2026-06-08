import random
from datetime import datetime


class LiveMatchEngine:

    """
    REAL-TIME BROADCAST ENGINE
    Dynamic live state simulation
    """

    def generate_live_state(self, match):

        now = datetime.utcnow()

        minute = now.minute

        # =====================================================
        # TEMPO ENGINE
        # =====================================================

        if minute % 4 == 0:
            tempo = "Бързо"
        elif minute % 3 == 0:
            tempo = "Високо"
        else:
            tempo = "Контролирано"

        # =====================================================
        # PRESSURE ENGINE
        # =====================================================

        pressure_states = [
            "Нисък",
            "Среден",
            "Висок"
        ]

        pressure = random.choice(pressure_states)

        # =====================================================
        # DOMINANCE ENGINE
        # =====================================================

        dominance_states = [
            f"{match.get('home')} доминира",
            f"{match.get('away')} доминира",
            "Равностоен мач"
        ]

        dominance = random.choice(dominance_states)

        # =====================================================
        # MOMENTUM ENGINE
        # =====================================================

        momentum_states = [
            "Нарастващ натиск",
            "Тактическа битка",
            "Силен ритъм",
            "Спокойно темпо"
        ]

        momentum = random.choice(momentum_states)

        # =====================================================
        # LIVE COMMENTARY ENGINE
        # =====================================================

        commentary = [
            f"{match.get('home')} опитва да установи контрол.",
            f"{match.get('away')} изглежда опасен на контраатака.",
            "Мачът се играе при висока интензивност.",
            "И двата отбора действат предпазливо.",
            "Следва период на тактическо надлъгване.",
            "Темпото постепенно се покачва."
        ]

        live_summary = random.choice(commentary)

        # =====================================================
        # MATCH FLOW ENGINE
        # =====================================================

        flow = (
            "• Висок интензитет\n"
            "• Натиск в центъра\n"
            "• Повече пространства при преходите\n"
            "• Очакват се опасни положения"
        )

        # =====================================================
        # FINAL STRUCTURE
        # =====================================================

        return {
            "tempo": tempo,
            "pressure": pressure,
            "dominance": dominance,
            "momentum": momentum,
            "live_summary": live_summary,
            "match_flow": flow
        }