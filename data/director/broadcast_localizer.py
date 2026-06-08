class BroadcastLocalizer:
    """
    STEP 25B - FULL BULGARIAN BROADCAST LOCALIZATION LAYER
    """

    def localize(self, editorial):

        def tr_team(name):
            mapping = {
                "Bayern Munich": "Байерн Мюнхен",
                "PSG": "ПСЖ",
                "Real Madrid": "Реал Мадрид",
                "Barcelona": "Барселона",
                "Manchester City": "Манчестър Сити",
                "Arsenal": "Арсенал"
            }
            return mapping.get(name, name)

        def tr_momentum(m):
            return {
                "Balanced": "Баланс",
                "Even": "Равностойно",
                "Home Strong": "Домакински натиск",
                "Away Strong": "Гостуващ натиск"
            }.get(m, m)

        def tr_text(text):

            if not text:
                return text

            replacements = {
                "competitive matchup": "оспорван двубой",
                "tactical battle": "тактически сблъсък",
                "midfield": "център на терена",
                "momentum": "импулс",
                "analysis": "анализ",
                "form": "форма",
                "high intensity": "висока интензивност",
                "balanced": "балансиран",
                "pressure": "натиск"
            }

            for k, v in replacements.items():
                text = text.replace(k, v)
                text = text.replace(k.title(), v)

            return text

        return {
            "intro": tr_text(editorial.get("intro", "")),
            "form_section": tr_text(editorial.get("form_section", "")),
            "home_form": tr_text(editorial.get("home_form", "")),
            "away_form": tr_text(editorial.get("away_form", "")),
            "tactical_battle": tr_text(editorial.get("tactical_battle", "")),
            "match_flow": tr_text(editorial.get("match_flow", "")),
            "prediction_text": tr_text(editorial.get("prediction_text", "")),

            "key_factors": editorial.get("key_factors", []),

            "strengths_home": editorial.get("strengths_home", []),
            "strengths_away": editorial.get("strengths_away", []),
            "weaknesses": editorial.get("weaknesses", []),

            "momentum": tr_momentum(editorial.get("momentum", "")),

            "key_points": [
                tr_text(p) for p in editorial.get("key_points", [])
            ]
        }