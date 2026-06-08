def generate_stream_analysis(team1, team2, prediction=None, ai=None):

    t1 = team1
    t2 = team2

    # -------------------------
    # 🎙 INTRO (PURE STORYTELLING)
    # -------------------------
    intro = f"""
Tonight we have a massive clash between {t1['name']} and {t2['name']}.
Both teams are entering this match with huge expectations.
"""

    # -------------------------
    # 📊 FORM COMPARISON (DATA ONLY)
    # -------------------------
    comparison = f"""
FORM COMPARISON:
{t1['name']}: {t1['form'].get('form', 'N/A')}
{t2['name']}: {t2['form'].get('form', 'N/A')}
"""

    # -------------------------
    # 🧠 AI NARRATIVE (SINGLE SOURCE)
    # -------------------------
    ai_narrative = ""

    if isinstance(ai, dict):
        ai_narrative = ai.get("narrative", "")

    # -------------------------
    # 🎯 KEY INSIGHT
    # (NO DUPLICATED MOMENTUM)
    # -------------------------
    insight = f"""
KEY INSIGHT:
{ai_narrative}
"""

    # -------------------------
    # 📊 CLEAN PREDICTION BLOCK
    # -------------------------
    prediction_data = {}

    if isinstance(prediction, dict):
        prediction_data = prediction.get("prediction", {})

    prediction_block = f"""
AI PREDICTION:

{t1['name']}: {prediction_data.get(t1['name'], 0)}%
{t2['name']}: {prediction_data.get(t2['name'], 0)}%
Draw: {prediction_data.get('draw', 0)}%
"""

    # -------------------------
    # 🎬 FINAL SCRIPT
    # -------------------------
    script = f"""
🎙 MATCH PREVIEW SCRIPT

{intro}

{comparison}

{insight}

{prediction_block}

🔥 Stay tuned for live analysis during the match!
"""

    # -------------------------
    # ✅ FINAL OUTPUT
    # -------------------------
    return {
        "intro": intro,

        "comparison": comparison,

        "insight": insight,

        "prediction": prediction_data,

        "full_script": script,

        # OBS READY BLOCKS
        "obs_blocks": {
            "intro": intro,
            "comparison": comparison,
            "insight": insight,
            "prediction": prediction_block
        }
    }