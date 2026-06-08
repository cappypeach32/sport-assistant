"""
Live Win Probability Engine
Uses Poisson distribution to calculate in-play win/draw/loss probabilities
based on: current score, elapsed minute, xG data, and pre-match baseline.
"""
import math

# Average total goals per 90-minute match (Premier League ~2.8)
AVG_GOALS_PER_90 = 2.8
GOAL_RATE_PER_MIN = AVG_GOALS_PER_90 / 90  # ~0.031 per team per minute

# Pre-compute factorials to avoid repeated calls
_FACT = [math.factorial(k) for k in range(15)]


def _poisson(lam: float, k: int) -> float:
    """P(X = k) for Poisson(λ)."""
    if k >= len(_FACT):
        return 0.0
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / _FACT[k]


def calculate(
    home_goals: int,
    away_goals: int,
    minute: int,
    pre_home_win: float = 40.0,   # % from pre-match prediction
    pre_draw: float    = 25.0,
    pre_away_win: float = 35.0,
    home_xg: float = 0.0,
    away_xg: float = 0.0,
) -> dict:
    """
    Calculate live win probabilities using in-play Poisson model.

    Strategy:
    - Use xG per minute (if available) to project remaining goals.
    - Fall back to pre-match strength ratio if xG is missing.
    - Enumerate all possible score outcomes for remaining time.
    """
    minute    = max(0, min(minute, 90))
    remaining = max(90 - minute, 0)

    # Derived strength from pre-match probabilities
    total_pre  = (pre_home_win + pre_draw + pre_away_win) or 100.0
    h_strength = (pre_home_win + 0.5 * pre_draw) / total_pre
    a_strength = (pre_away_win + 0.5 * pre_draw) / total_pre

    # Expected remaining goals
    if (home_xg > 0 or away_xg > 0) and minute > 0:
        # xG-based projection scaled to remaining time
        home_rate = (home_xg / minute) * remaining
        away_rate = (away_xg / minute) * remaining
    else:
        # Pre-match strength × base rate × remaining minutes
        home_rate = h_strength * GOAL_RATE_PER_MIN * remaining
        away_rate = a_strength * GOAL_RATE_PER_MIN * remaining

    # Clamp rates to reasonable range
    home_rate = max(0.0, min(home_rate, 8.0))
    away_rate = max(0.0, min(away_rate, 8.0))

    # Enumerate all possible additional goal combinations
    home_win_p = 0.0
    draw_p     = 0.0
    away_win_p = 0.0

    for h_more in range(12):
        ph = _poisson(home_rate, h_more)
        if ph < 1e-7:
            break
        for a_more in range(12):
            pa = _poisson(away_rate, a_more)
            if pa < 1e-7:
                break
            p = ph * pa
            final_h = home_goals + h_more
            final_a = away_goals + a_more
            if   final_h > final_a: home_win_p += p
            elif final_h < final_a: away_win_p += p
            else:                   draw_p     += p

    total = home_win_p + draw_p + away_win_p
    if total > 0:
        home_win_p /= total
        draw_p     /= total
        away_win_p /= total

    # Round to integers that sum to 100
    hw = round(home_win_p * 100)
    dp = round(draw_p     * 100)
    aw = 100 - hw - dp

    # Determine momentum label
    if   home_goals > away_goals: situation = "winning"
    elif away_goals > home_goals: situation = "losing"
    else:                         situation = "drawing"

    return {
        "home_win_pct":     hw,
        "draw_pct":         dp,
        "away_win_pct":     aw,
        "minutes_remaining": remaining,
        "situation":        situation,
        "home_rate":        round(home_rate, 2),
        "away_rate":        round(away_rate, 2),
        "is_live_calc":     True,
    }


def get_situation_label(situation: str, home: str, away: str) -> str:
    """Bulgarian situation description."""
    if situation == "winning": return f"{home} печели"
    if situation == "losing":  return f"{away} печели"
    return "Равен"
