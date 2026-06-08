from data.prediction_v2 import generate_prediction_v2
from data.ai_analyzer import generate_ai_analysis
from data.stream_engine import generate_stream_analysis
from data.studio_feed import generate_studio_feed
from data.studio_theme import generate_studio_ui


def run_match_orchestrator(team1: dict, team2: dict):

    # ⚽ STEP 1 — REAL DATA BASED PREDICTION
    prediction = generate_prediction_v2(team1, team2) or {}

    # 🧠 STEP 2 — LIGHT ANALYTICS (NO CHAT AI)
    ai = generate_ai_analysis(team1, team2) or {}

    # 📡 STEP 3 — STREAM ENGINE (UI ONLY LOGIC)
    stream = generate_stream_analysis(
        team1=team1,
        team2=team2,
        prediction=prediction,
        ai=ai
    ) or {}

    # 🎛 STEP 4 — UI LAYER ONLY
    studio_ui = generate_studio_ui(
        team1=team1,
        team2=team2,
        prediction=prediction,
        ai=ai,
        stream=stream
    ) or {}

    # 📺 FINAL OBS OUTPUT
    return generate_studio_feed(
        team1=team1,
        team2=team2,
        prediction=prediction,
        ai=ai,
        stream=stream,
        studio_ui=studio_ui
    )