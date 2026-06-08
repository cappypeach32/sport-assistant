class EditorialMatchEngine:
    """
    STEP 25A PRO FINAL
    Bulgarian Broadcast Editorial Engine
    FULL STABLE VERSION
    """

    def generate(self, featured, orchestrator_result, live_state):

        # =====================================================
        # SAFE INPUTS
        # =====================================================

        home = featured.get("home") or "Домакин"
        away = featured.get("away") or "Гост"

        tactical = orchestrator_result.get("tactical_insight") or {}
        prediction = orchestrator_result.get("prediction_summary") or {}
        key_points = orchestrator_result.get("key_points") or []

        # =====================================================
        # SAFE LIVE STATE NORMALIZATION
        # =====================================================

        def safe(val, default):
            return val if val not in [None, ""] else default

        tempo_raw = safe(live_state.get("tempo"), "Controlled")
        pressure_raw = safe(live_state.get("pressure"), "Low")
        dominance_raw = safe(live_state.get("dominance"), "Even")
        momentum_raw = safe(tactical.get("momentum"), "Balanced")

        # =====================================================
        # FULL BULGARIAN TRANSLATION MAP (FIXED)
        # =====================================================

        translate = {
            "Controlled": "Контролирано",
            "Fast": "Бързо",
            "Explosive": "Експлозивно",
            "Slow": "Бавно",

            "Low": "Нисък",
            "Moderate": "Среден",
            "High": "Висок",
            "Extreme": "Екстремен",

            "Balanced": "Балансиран",
            "Even": "Равностоен",

            "medium": "Средно темпо",
            "balanced": "Балансиран",
            "neutral": "Неутрален",

            "Not Started": "Не е започнал",
            "Full Time": "Край на мача",
            "Live": "На живо"
        }

        tempo = translate.get(tempo_raw, tempo_raw)
        pressure = translate.get(pressure_raw, pressure_raw)
        dominance = translate.get(dominance_raw, dominance_raw)
        momentum = translate.get(momentum_raw, momentum_raw)

        # =====================================================
        # CLEAN TACTICAL TEXT NORMALIZATION (STRONG FIX)
        # =====================================================

        analysis = tactical.get("analysis") or ""

        analysis = analysis.replace("This is a competitive matchup between",
                                    "Очаква се изключително оспорван сблъсък между")

        analysis = analysis.replace("Recent form analysis shows",
                                    "Последната форма показва")

        analysis = analysis.replace("Balanced tactical matchup",
                                    "балансиран тактически двубой")

        analysis = analysis.replace("Momentum", "Импулс")

        # =====================================================
        # INTRO (BG PRO LEVEL)
        # =====================================================

        intro = (
            f"Предстоящият сблъсък между {home} и {away} "
            f"се очаква да бъде ключов мач от програмата. "
            f"Двата отбора влизат с висока мотивация и "
            f"търсят максимален резултат."
        )

        # =====================================================
        # FORM ANALYSIS (REALISTIC BG)
        # =====================================================

        form_section = (
            f"{home} и {away} демонстрират колеблива, но конкурентна форма. "
            f"Очаква се тактически балансиран двубой с висока интензивност."
        )

        home_form = (
            f"{home} ще търси контрол върху играта и ранно надмощие. "
            f"Ключът за тях ще бъде темпото в първите минути."
        )

        away_form = (
            f"{away} разчита на контраатаки и бързи преходи. "
            f"Те са опасни при грешки на съперника."
        )

        # =====================================================
        # TACTICAL BATTLE (IMPROVED LOGIC)
        # =====================================================

        tactical_battle = (
            f"Тактическият сблъсък ще се реши в централната зона. "
            f"{analysis}. "
            f"Темпото ({tempo}) и натискът ({pressure}) "
            f"ще определят развоя на мача."
        )

        # =====================================================
        # KEY FACTORS (MORE REALISTIC)
        # =====================================================

        key_factors = [
            "Контрол в средата на терена",
            "Преходи от защита към атака",
            "Стандартни положения",
            "Първи гол",
            f"Темпо: {tempo}"
        ]

        strengths_home = [
            f"{home} силен в началните минути",
            "Владение на топката",
            "Организирана атака"
        ]

        strengths_away = [
            f"{away} опасен на контра",
            "Бързи преходи",
            "Ефективност в малко положения"
        ]

        weaknesses = [
            f"{home} допуска пространства при висока линия",
            f"{away} трудно под натиск",
            "Грешки при висока интензивност"
        ]

        # =====================================================
        # MATCH FLOW (REALISTIC BROADCAST STYLE)
        # =====================================================

        match_flow = (
            "Сценарий на мача:\n\n"
            "• Висока интензивност в началото\n"
            "• Тактическа битка в центъра\n"
            "• Повече пространства след 60-та минута\n"
            "• Възможни ключови моменти от статични положения"
        )

        # =====================================================
        # LIVE SUMMARY (FIXED LOGIC)
        # =====================================================

        live_summary = (
            f"Мачът в момента е {dominance.lower()} с "
            f"{tempo.lower()} темпо и "
            f"{pressure.lower()} натиск."
        )

        # =====================================================
        # SAFE PREDICTION (FIXED)
        # =====================================================

        home_win = prediction.get("home_win", 45)
        away_win = prediction.get("away_win", 30)
        draw = prediction.get("draw", 25)

        prediction_text = (
            f"{home} има леко предимство според моделите, "
            f"но {away} остава напълно конкурентен.\n\n"
            f"Вероятности:\n"
            f"• {home}: {home_win:.1f}%\n"
            f"• {away}: {away_win:.1f}%\n"
            f"• Равенство: {draw:.1f}%"
        )

        # =====================================================
        # FINAL OUTPUT
        # =====================================================

        return {
            "intro": intro,
            "form_section": form_section,
            "home_form": home_form,
            "away_form": away_form,
            "tactical_battle": tactical_battle,
            "key_factors": key_factors,
            "strengths_home": strengths_home,
            "strengths_away": strengths_away,
            "weaknesses": weaknesses,
            "match_flow": match_flow,
            "live_summary": live_summary,
            "prediction_text": prediction_text,
            "momentum": momentum,
            "key_points": key_points
        }