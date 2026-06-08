def generate_studio_ui(team1, team2, prediction, ai, stream):

    t1_name = team1["name"]
    t2_name = team2["name"]

    t1_win = prediction["prediction"].get(t1_name, 0)
    t2_win = prediction["prediction"].get(t2_name, 0)
    draw = prediction["prediction"].get("draw", 0)

    momentum = prediction.get("momentum", "Balanced match")

    return {
        # -------------------------
        # 🎬 HEADER (OBS TITLE BAR)
        # -------------------------
        "header": f"⚽ {t1_name} vs {t2_name}",

        # -------------------------
        # 📊 MAIN SCORE CARDS
        # -------------------------
        "score_cards": [
            {
                "team": t1_name,
                "value": f"{t1_win}%",
                "type": "win_probability",
                "color": "green"
            },
            {
                "team": t2_name,
                "value": f"{t2_win}%",
                "type": "win_probability",
                "color": "red"
            },
            {
                "team": "Draw",
                "value": f"{draw}%",
                "type": "draw_probability",
                "color": "gray"
            }
        ],

        # -------------------------
        # ⚡ MOMENTUM BAR (OBS VISUAL)
        # -------------------------
        "momentum_bar": {
            "text": momentum,
            "intensity": "high" if "strong" in momentum else "medium",
            "style": "pulse"
        },

        # -------------------------
        # 🧠 AI INSIGHT PANEL
        # -------------------------
        "insight_panel": {
            "title": "AI Tactical Insight",
            "content": ai.get("narrative", "") if ai else "",
            "key_factor": ai.get("insight", {}).get("key_factor", "") if ai else ""
        },

        # -------------------------
        # 🎙 STREAM LOWER THIRD
        # -------------------------
        "lower_third": {
            "text": f"{t1_name} vs {t2_name} | Momentum: {momentum}",
            "style": "dark_overlay"
        },

        # -------------------------
        # 🚨 ALERT SYSTEM
        # -------------------------
        "alerts": [
            "Match is balanced" if "Balanced" in momentum else "Advantage detected"
        ]
    }