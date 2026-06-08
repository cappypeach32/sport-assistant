def generate_studio_feed(team1, team2, prediction, ai, stream, studio_ui):
    """
    STUDIO FEED v1
    - OBS ready structure
    - fully safe production version
    """

    # -------------------------
    # SAFE GUARDS
    # -------------------------
    prediction = prediction if isinstance(prediction, dict) else {}
    stream = stream if isinstance(stream, dict) else {}
    studio_ui = studio_ui if isinstance(studio_ui, dict) else {}

    insight_panel = studio_ui.get("insight_panel", {})
    momentum_bar = studio_ui.get("momentum_bar", {})

    prediction_data = prediction.get("prediction", {})

    # fallback safe momentum
    momentum = (
        momentum_bar.get("text")
        or prediction.get("momentum")
        or "Unknown"
    )

    # -------------------------
    # MAIN OUTPUT
    # -------------------------
    return {
        # 🎬 TITLE
        "title": f"{team1['name']} vs {team2['name']} - AI Match Studio",

        # 🧠 PRE MATCH
        "pre_match_brief": {
            "matchup": f"{team1['name']} vs {team2['name']}",
            "key_story": insight_panel.get("content", "No insight available"),
            "tone": "High intensity tactical matchup"
        },

        # 📊 KEY POINTS
        "key_points": [
            f"{team1['name']} form: {team1['form'].get('form')}",
            f"{team2['name']} form: {team2['form'].get('form')}",
            f"Prediction: {prediction_data}",
            "Midfield control will decide the match",
            "Early goals likely to shift momentum"
        ],

        # ⚔️ INSIGHT
        "tactical_insight": {
            "analysis": insight_panel.get("content", "No narrative available"),
            "momentum": momentum
        },

        # 🎙 SCRIPT
        "live_script": stream.get("full_script", ""),

        # 🔥 HOOKS
        "hooks": [
            f"This match between {team1['name']} and {team2['name']} could explode early",
            "AI model suggests a tightly balanced encounter",
            "Watch the first 15 minutes carefully — key momentum shift expected"
        ],

        # 📈 PREDICTION
        "prediction_summary": prediction_data,

        # 🧾 META
        "meta": {
            "version": "studio_feed_v1",
            "mode": "OBS_READY"
        }
    }