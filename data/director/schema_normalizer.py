def normalize_match_output(featured, result):

    # =========================
    # SAFE EXTRACTION LAYER
    # =========================
    tactical = result.get("tactical_insight") or {}

    prediction = (
        result.get("prediction_summary")
        or result.get("prediction")
        or {}
    )

    key_points = result.get("key_points") or result.get("hooks") or []

    if not isinstance(key_points, list):
        key_points = []

    # =========================
    # MATCH DATA
    # =========================
    home = featured.get("home")
    away = featured.get("away")

    # =========================
    # SAFE INSIGHT LAYER
    # =========================
    momentum = (
        tactical.get("momentum")
        or result.get("momentum")
        or "Balanced"
    )

    insight = (
        tactical.get("analysis")
        or tactical.get("insight")
        or result.get("analysis")
        or result.get("pre_match_brief", {}).get("key_story")
        or "No insight available"
    )

    # =========================
    # SMART PREDICTION PARSING (FIX CORE ISSUE)
    # =========================
    home_win = prediction.get("home_win")
    away_win = prediction.get("away_win")

    # fallback for AI formats (Brazil/Real Madrid style)
    if home_win is None or away_win is None:
        values = list(prediction.values())

        if len(values) >= 2:
            home_win = values[0]
            away_win = values[1]

    # final safe fallback
    home_win = home_win if home_win is not None else 0
    away_win = away_win if away_win is not None else 0

    draw = prediction.get("draw", 0)

    confidence = result.get("confidence", 0.75)

    # =========================
    # FINAL CONTRACT
    # =========================
    return {
        "match": {
            "home": home,
            "away": away,
            "status": featured.get("status", "NS"),
            "date": featured.get("date")
        },

        "teams": {
            "home": {"name": home},
            "away": {"name": away}
        },

        "analysis": {
            "momentum": momentum,
            "insight": insight
        },

        "prediction": {
            "home_win": home_win,
            "away_win": away_win,
            "draw": draw,
            "confidence": confidence
        },

        "context": {
            "key_points": key_points,
            "summary": result.get("narrative", "No summary available")
        },

        "meta": {
            "mode": "WORLD_CUP_ESPN_V1",
            "version": "step_23A_plus_stable_v2",
            "normalized": True,
            "provider": "AI_SPORTS_ENGINE"
        },

        "editorial": result.get("editorial", {}),

        "raw": result
    }