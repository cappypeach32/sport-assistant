from collections import deque


class MomentumEngine:
    """
    xG Momentum Engine — rolling window analysis over real API stats.

    Every 45s we receive a stats snapshot. This engine stores a history
    and computes how momentum has shifted in the last WINDOW_MINUTES.

    Output drives the broadcast overlay momentum bars and trend labels.
    """

    WINDOW_MINUTES = 8
    MAX_SNAPSHOTS = 30

    def __init__(self):
        # fixture_id -> deque of snapshot dicts
        self._history: dict = {}

    # --------------------------------------------------
    # SNAPSHOT INTAKE
    # --------------------------------------------------

    def add_snapshot(self, fixture_id: int, stats: dict, match_minute: int) -> None:
        """Store a timestamped stats snapshot for later momentum calculation."""
        if fixture_id not in self._history:
            self._history[fixture_id] = deque(maxlen=self.MAX_SNAPSHOTS)

        h = stats.get("home", {})
        a = stats.get("away", {})

        self._history[fixture_id].append({
            "minute": match_minute,
            "home_xg":        float(h.get("xg", 0) or 0),
            "away_xg":        float(a.get("xg", 0) or 0),
            "home_shots":     int(h.get("shots_total", 0) or 0),
            "away_shots":     int(a.get("shots_total", 0) or 0),
            "home_shots_on":  int(h.get("shots_on_target", 0) or 0),
            "away_shots_on":  int(a.get("shots_on_target", 0) or 0),
            "home_dangerous": int(h.get("dangerous_attacks", 0) or 0),
            "away_dangerous": int(a.get("dangerous_attacks", 0) or 0),
            "home_possession": int(h.get("possession", 50) or 50),
        })

    # --------------------------------------------------
    # MOMENTUM CALCULATION
    # --------------------------------------------------

    def calculate(self, fixture_id: int) -> dict:
        """
        Compute xG momentum from rolling window.

        Returns:
            home_momentum_pct  — 0..100 normalised score for home
            away_momentum_pct  — 0..100 normalised score for away
            deltas             — raw changes in xG, shots, dangerous attacks
            trend labels       — per-team human-readable trend
            dominant_team      — "home" | "away" | "neutral"
            pressure_spikes    — list of significant recent bursts
        """
        snaps = list(self._history.get(fixture_id, []))

        if len(snaps) < 2:
            return self._empty()

        latest = snaps[-1]

        # Find the snapshot closest to WINDOW_MINUTES before latest
        window_snap = snaps[0]
        for s in reversed(snaps[:-1]):
            if latest["minute"] - s["minute"] >= self.WINDOW_MINUTES:
                window_snap = s
                break

        # Raw deltas (cumulative stats only go up, so delta = change in window)
        hxd  = latest["home_xg"]        - window_snap["home_xg"]
        axd  = latest["away_xg"]        - window_snap["away_xg"]
        hsd  = latest["home_shots"]     - window_snap["home_shots"]
        asd  = latest["away_shots"]     - window_snap["away_shots"]
        hsod = latest["home_shots_on"]  - window_snap["home_shots_on"]
        asod = latest["away_shots_on"]  - window_snap["away_shots_on"]
        hdd  = latest["home_dangerous"] - window_snap["home_dangerous"]
        add_ = latest["away_dangerous"] - window_snap["away_dangerous"]
        pd   = latest["home_possession"] - window_snap["home_possession"]

        # Weighted composite momentum score
        # xG carries the most weight because it reflects shot quality, not just volume
        home_score = hxd * 40 + hsd * 3 + hsod * 5 + hdd * 0.5
        away_score = axd * 40 + asd * 3 + asod * 5 + add_ * 0.5

        # Normalise to 0-100 with 50 as balanced baseline
        total = abs(home_score) + abs(away_score)
        if total > 0:
            home_pct = round(home_score / total * 100 + 50, 1)
            away_pct = round(100 - home_pct, 1)
        else:
            home_pct = away_pct = 50.0

        home_pct = max(0.0, min(100.0, home_pct))
        away_pct = max(0.0, min(100.0, away_pct))

        # Pressure spikes — moments of concentrated attacking burst
        spikes = []
        if hxd > 0.20:
            spikes.append({"team": "home", "type": "xG spike", "value": round(hxd, 3)})
        if axd > 0.20:
            spikes.append({"team": "away", "type": "xG spike", "value": round(axd, 3)})
        if hdd >= 6:
            spikes.append({"team": "home", "type": "danger surge", "value": hdd})
        if add_ >= 6:
            spikes.append({"team": "away", "type": "danger surge", "value": add_})

        dominant = "neutral"
        if abs(home_pct - away_pct) > 15:
            dominant = "home" if home_pct > away_pct else "away"

        return {
            "home_momentum_pct":    home_pct,
            "away_momentum_pct":    away_pct,
            "home_xg_delta":        round(hxd, 3),
            "away_xg_delta":        round(axd, 3),
            "home_shots_delta":     hsd,
            "away_shots_delta":     asd,
            "home_dangerous_delta": hdd,
            "away_dangerous_delta": add_,
            "possession_drift":     round(pd, 1),
            "home_trend":           self._trend_label(hxd, hsd, hdd),
            "away_trend":           self._trend_label(axd, asd, add_),
            "dominant_team":        dominant,
            "momentum_balance":     round(abs(home_score - away_score), 2),
            "pressure_spikes":      spikes,
            "window_minutes":       self.WINDOW_MINUTES,
            "snapshots_used":       len(snaps),
        }

    # --------------------------------------------------
    # HELPERS
    # --------------------------------------------------

    def _trend_label(self, xg_delta: float, shots_delta: int, dangerous_delta: int) -> str:
        if xg_delta > 0.20 or shots_delta >= 5:
            return "🔥 Attacking surge"
        if xg_delta > 0.10 or shots_delta >= 3 or dangerous_delta >= 6:
            return "📈 Pressure increasing"
        if xg_delta < -0.05 and shots_delta <= 0:
            return "📉 Fading"
        if dangerous_delta <= -5:
            return "⬇ Pressing drop"
        return "➡ Stable"

    def _empty(self) -> dict:
        return {
            "home_momentum_pct":    50.0,
            "away_momentum_pct":    50.0,
            "home_xg_delta":        0.0,
            "away_xg_delta":        0.0,
            "home_shots_delta":     0,
            "away_shots_delta":     0,
            "home_dangerous_delta": 0,
            "away_dangerous_delta": 0,
            "possession_drift":     0.0,
            "home_trend":           "➡ Stable",
            "away_trend":           "➡ Stable",
            "dominant_team":        "neutral",
            "momentum_balance":     0.0,
            "pressure_spikes":      [],
            "window_minutes":       self.WINDOW_MINUTES,
            "snapshots_used":       0,
        }
