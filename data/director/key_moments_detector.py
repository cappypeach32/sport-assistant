class KeyMomentsDetector:
    """
    Detects significant match moments from real stat changes.

    Triggers alerts for: momentum shifts, dangerous spells, shot bursts,
    defensive collapses, pressing drops, and possession swings.

    Each alert has: type, icon, severity, title, message, data.
    """

    THRESHOLDS = {
        "momentum_shift":      20,     # pct swing between teams
        "dangerous_spell":      5,     # dangerous attacks in window
        "shot_burst":           4,     # shots in window
        "possession_swing":    12,     # possession % change
        "pressing_drop":       -6,     # drop in dangerous attacks
        "defensive_collapse":   0.22,  # xG delta against own team
        "xg_spike":            0.18,   # single xG window spike
    }

    SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}

    # --------------------------------------------------
    # DETECTION PIPELINE
    # --------------------------------------------------

    def detect(
        self,
        momentum: dict,
        stats: dict,
        home_team: str,
        away_team: str,
        minute: int,
    ) -> list:
        """
        Run all detectors against current momentum snapshot.
        Returns alerts sorted by severity (highest first).
        """
        alerts = []

        hm   = momentum.get("home_momentum_pct", 50)
        am   = momentum.get("away_momentum_pct", 50)
        hxd  = momentum.get("home_xg_delta", 0)
        axd  = momentum.get("away_xg_delta", 0)
        hsd  = momentum.get("home_shots_delta", 0)
        asd  = momentum.get("away_shots_delta", 0)
        hdd  = momentum.get("home_dangerous_delta", 0)
        add_ = momentum.get("away_dangerous_delta", 0)
        pd   = momentum.get("possession_drift", 0)
        win  = momentum.get("window_minutes", 8)

        home_curr_dan = stats.get("home", {}).get("dangerous_attacks", 0) or 0
        away_curr_dan = stats.get("away", {}).get("dangerous_attacks", 0) or 0

        alerts += self._check_momentum_shift(hm, am, home_team, away_team, minute)
        alerts += self._check_dangerous_spell(hdd, add_, home_team, away_team, minute, win)
        alerts += self._check_shot_burst(hsd, asd, home_team, away_team, minute, win)
        alerts += self._check_defensive_collapse(hxd, axd, home_team, away_team, minute)
        alerts += self._check_pressing_drop(hdd, add_, home_curr_dan, away_curr_dan, home_team, away_team, minute)
        alerts += self._check_possession_swing(pd, home_team, away_team, minute)
        alerts += self._check_xg_spike(momentum.get("pressure_spikes", []), home_team, away_team, minute)

        alerts.sort(
            key=lambda x: self.SEVERITY_ORDER.get(x["severity"], 0),
            reverse=True
        )

        return alerts

    # --------------------------------------------------
    # DETECTORS
    # --------------------------------------------------

    def _check_momentum_shift(self, hm, am, home, away, minute):
        swing = abs(hm - am)
        if swing < self.THRESHOLDS["momentum_shift"]:
            return []
        dominant = home if hm > am else away
        receding = away if hm > am else home
        return [{
            "type":     "MOMENTUM_SHIFT",
            "icon":     "📈",
            "severity": "high",
            "minute":   minute,
            "title":    "Смяна на импулса",
            "message":  f"{dominant} поема контрола — {receding} губи властта в средата",
            "data":     {"swing": round(swing, 1), "dominant": dominant},
        }]

    def _check_dangerous_spell(self, hdd, add_, home, away, minute, win):
        alerts = []
        t = self.THRESHOLDS["dangerous_spell"]
        if hdd >= t:
            alerts.append({
                "type":     "DANGEROUS_SPELL",
                "icon":     "⚠️",
                "severity": "high",
                "minute":   minute,
                "title":    "Опасна фаза",
                "message":  f"{home} е в опасна фаза — {hdd} атаки в последните {win} мин",
                "data":     {"team": home, "dangerous_attacks": hdd},
            })
        if add_ >= t:
            alerts.append({
                "type":     "DANGEROUS_SPELL",
                "icon":     "⚠️",
                "severity": "high",
                "minute":   minute,
                "title":    "Опасна фаза",
                "message":  f"{away} е в опасна фаза — {add_} атаки в последните {win} мин",
                "data":     {"team": away, "dangerous_attacks": add_},
            })
        return alerts

    def _check_shot_burst(self, hsd, asd, home, away, minute, win):
        alerts = []
        t = self.THRESHOLDS["shot_burst"]
        if hsd >= t:
            alerts.append({
                "type":     "SHOT_BURST",
                "icon":     "🔥",
                "severity": "medium",
                "minute":   minute,
                "title":    "Серия от удари",
                "message":  f"{home} — {hsd} удара в последните {win} мин",
                "data":     {"team": home, "shots": hsd},
            })
        if asd >= t:
            alerts.append({
                "type":     "SHOT_BURST",
                "icon":     "🔥",
                "severity": "medium",
                "minute":   minute,
                "title":    "Серия от удари",
                "message":  f"{away} — {asd} удара в последните {win} мин",
                "data":     {"team": away, "shots": asd},
            })
        return alerts

    def _check_defensive_collapse(self, hxd, axd, home, away, minute):
        alerts = []
        t = self.THRESHOLDS["defensive_collapse"]
        if hxd > t:
            alerts.append({
                "type":     "DEFENSIVE_COLLAPSE",
                "icon":     "🚨",
                "severity": "critical",
                "minute":   minute,
                "title":    "Отбранителен натиск",
                "message":  f"Защитата на {away} е под натиск — xG +{hxd:.2f} в прозореца",
                "data":     {"team": away, "xg_against": round(hxd, 3)},
            })
        if axd > t:
            alerts.append({
                "type":     "DEFENSIVE_COLLAPSE",
                "icon":     "🚨",
                "severity": "critical",
                "minute":   minute,
                "title":    "Отбранителен натиск",
                "message":  f"Защитата на {home} е под натиск — xG +{axd:.2f} в прозореца",
                "data":     {"team": home, "xg_against": round(axd, 3)},
            })
        return alerts

    def _check_pressing_drop(self, hdd, add_, home_curr, away_curr, home, away, minute):
        alerts = []
        t = self.THRESHOLDS["pressing_drop"]
        if hdd <= t and home_curr > 10:
            alerts.append({
                "type":     "PRESSING_DROP",
                "icon":     "📉",
                "severity": "medium",
                "minute":   minute,
                "title":    "Спад в пресинга",
                "message":  f"{home} намалява интензивността — вероятна умора",
                "data":     {"team": home, "delta": hdd},
            })
        if add_ <= t and away_curr > 10:
            alerts.append({
                "type":     "PRESSING_DROP",
                "icon":     "📉",
                "severity": "medium",
                "minute":   minute,
                "title":    "Спад в пресинга",
                "message":  f"{away} намалява интензивността — вероятна умора",
                "data":     {"team": away, "delta": add_},
            })
        return alerts

    def _check_possession_swing(self, pd, home, away, minute):
        if abs(pd) < self.THRESHOLDS["possession_swing"]:
            return []
        gaining = home if pd > 0 else away
        return [{
            "type":     "POSSESSION_SWING",
            "icon":     "🔄",
            "severity": "low",
            "minute":   minute,
            "title":    "Смяна на владението",
            "message":  f"{gaining} поема контрол над топката — {abs(pd):.0f}% промяна",
            "data":     {"team": gaining, "drift": round(pd, 1)},
        }]

    def _check_xg_spike(self, spikes, home, away, minute):
        alerts = []
        for spike in spikes:
            team_name = home if spike.get("team") == "home" else away
            alerts.append({
                "type":     "XG_SPIKE",
                "icon":     "💥",
                "severity": "high",
                "minute":   minute,
                "title":    "Скок в xG вероятността",
                "message":  f"{team_name} xG импулс +{spike.get('value', 0):.2f} — клъстър от качествени положения",
                "data":     {"team": team_name, "xg": spike.get("value")},
            })
        return alerts
