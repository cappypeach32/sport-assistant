import os
import time
from dotenv import load_dotenv

load_dotenv()

_openai_key = os.getenv("OPENAI_API_KEY", "")
_openai_available = bool(
    _openai_key
    and not _openai_key.startswith("YOUR_")
    and len(_openai_key) > 20
)

if _openai_available:
    try:
        from openai import OpenAI
        _client = OpenAI(api_key=_openai_key)
        print("[TACTICAL] OpenAI client ready — narrative generation enabled")
    except Exception:
        _openai_available = False
        _client = None
        print("[TACTICAL] OpenAI import failed — narrative generation disabled")
else:
    _client = None
    print("[TACTICAL] No OpenAI key — narrative generation disabled (rule-based only)")

# Formation → base tactical description
FORMATION_STYLES: dict = {
    "4-3-3":   "high press, wide attacking channels",
    "4-2-3-1": "compact defensive shape, quick counter-attack",
    "4-4-2":   "traditional balanced shape, wide midfield",
    "3-5-2":   "wing-back dominance, numerical midfield advantage",
    "5-4-1":   "deep low block, defensive solidity",
    "5-3-2":   "deep defensive block, transition football",
    "4-1-4-1": "holding midfielder screen, possession football",
    "3-4-3":   "high defensive line, attacking fullbacks",
    "4-3-2-1": "narrow midfield, vertical short passing",
    "4-5-1":   "defensive midfield overload, wing containment",
    "3-4-2-1": "wide wing-backs with creative support strikers",
    "4-4-1-1": "two-bank defensive shape, attacking midfielder pivot",
}

# How long to wait between GPT calls per fixture (seconds)
GPT_COOLDOWN = 90


class TacticalEngine:
    """
    AI Tactical Analysis Engine.

    1. classify_style()   — derives playing style from formation + live stats
    2. generate_narrative() — GPT-4o-mini tactical commentary (rate-limited)
    3. analyze()           — full pipeline, returns structured output
    """

    def __init__(self):
        self._last_narrative: dict = {}
        self._last_call_ts:   dict = {}

    # --------------------------------------------------
    # STYLE CLASSIFIER
    # --------------------------------------------------

    def classify_style(self, formation: str, stats: dict) -> dict:
        """
        Returns tactical style labels derived from formation + stats.
        No LLM call needed — pure rule-based for low latency.
        """
        base = FORMATION_STYLES.get(formation, "balanced shape")

        possession = int(stats.get("possession", 50) or 50)
        passes     = int(stats.get("passes_total", 0) or 0)
        shots      = int(stats.get("shots_total", 0) or 0)
        dangerous  = int(stats.get("dangerous_attacks", 0) or 0)
        fouls      = int(stats.get("fouls", 0) or 0)
        corners    = int(stats.get("corners", 0) or 0)
        saves      = int(stats.get("saves", 0) or 0)

        active_styles = []

        # Possession-based style
        if possession >= 58:
            active_styles.append("possession dominance")
        elif possession <= 40:
            active_styles.append("low block / counter")

        # Press indicators
        if fouls >= 10:
            active_styles.append("high press")

        # Attacking volume
        if dangerous >= 30 and shots >= 10:
            active_styles.append("overloading attack")
        elif dangerous >= 20:
            active_styles.append("sustained attacking pressure")

        # Transition football (low possession but high dangerous attacks)
        if possession < 45 and dangerous >= 15:
            active_styles.append("transition football")

        # Set piece threat
        if corners >= 7:
            active_styles.append("set-piece threat")

        # Defensive solidity indicator
        if saves >= 5 and possession < 45:
            active_styles.append("defensive resistance")

        if not active_styles:
            active_styles.append(base)

        return {
            "formation":          formation,
            "base_style":         base,
            "active_styles":      active_styles,
            "possession_control": (
                "dominant"  if possession >= 55 else
                "contested" if possession >= 45 else
                "conceding"
            ),
            "attacking_intensity": (
                "high"   if dangerous >= 25 else
                "medium" if dangerous >= 12 else
                "low"
            ),
            "defensive_solidity": (
                "compact"     if fouls < 8  else
                "aggressive"  if fouls < 14 else
                "struggling"
            ),
            "raw": {
                "possession": possession,
                "shots":      shots,
                "dangerous":  dangerous,
                "fouls":      fouls,
                "corners":    corners,
            },
        }

    # --------------------------------------------------
    # GPT NARRATIVE
    # --------------------------------------------------

    def generate_narrative(
        self,
        fixture_id: int,
        home_team: str,
        away_team: str,
        home_tactics: dict,
        away_tactics: dict,
        momentum: dict,
        minute: int,
    ) -> str:
        """
        Generate a sharp 2-sentence tactical insight via GPT-4o-mini.
        Rate-limited to once per GPT_COOLDOWN seconds per fixture.
        Falls back to a rule-based description when cooling down.
        """
        now = time.time()
        last = self._last_call_ts.get(fixture_id, 0)

        if not _openai_available or _client is None:
            return self._fallback(home_team, away_team, home_tactics, away_tactics)

        if now - last < GPT_COOLDOWN:
            return self._last_narrative.get(fixture_id, self._fallback(home_team, away_team, home_tactics, away_tactics))

        home_styles = ", ".join(home_tactics.get("active_styles", []))
        away_styles = ", ".join(away_tactics.get("active_styles", []))
        hm = momentum.get("home_momentum_pct", 50)
        am = momentum.get("away_momentum_pct", 50)
        h_trend = momentum.get("home_trend", "Stable")
        a_trend = momentum.get("away_trend", "Stable")

        prompt = f"""Ти си TV спортен анализатор. Пишеш САМО на Български език. Стилът е кратък и конкретен.

Мач: {home_team} срещу {away_team} — {minute}'

{home_team}:
  Схема: {home_tactics.get('formation', '?')}
  Стил: {home_styles}
  Контрол на топката: {home_tactics.get('possession_control')}
  Атакуваща интензивност: {home_tactics.get('attacking_intensity')}
  xG тренд: {h_trend}
  Импулс: {hm:.0f}%

{away_team}:
  Схема: {away_tactics.get('formation', '?')}
  Стил: {away_styles}
  Контрол на топката: {away_tactics.get('possession_control')}
  Атакуваща интензивност: {away_tactics.get('attacking_intensity')}
  xG тренд: {a_trend}
  Импулс: {am:.0f}%

Напиши точно 2 изречения — конкретен тактически анализ за случващото се в момента. Споменавай имената на отборите. Максимум 55 думи. Без излишни думи."""

        try:
            response = _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
                temperature=0.72,
            )
            narrative = response.choices[0].message.content.strip()
            self._last_narrative[fixture_id] = narrative
            self._last_call_ts[fixture_id] = now
            print(f"[TACTICAL] GPT narrative generated for fixture {fixture_id}")
            return narrative
        except Exception as e:
            print(f"[TACTICAL] GPT error: {e}")
            fallback = self._fallback(home_team, away_team, home_tactics, away_tactics)
            self._last_narrative[fixture_id] = fallback
            self._last_call_ts[fixture_id] = now
            return fallback

    # --------------------------------------------------
    # FULL PIPELINE
    # --------------------------------------------------

    def analyze(
        self,
        fixture_id: int,
        home_team: str,
        away_team: str,
        lineups: dict,
        stats: dict,
        momentum: dict,
        minute: int,
    ) -> dict:
        """Run the full tactical pipeline and return a structured result."""
        home_formation = lineups.get("home", {}).get("formation", "4-4-2")
        away_formation = lineups.get("away", {}).get("formation", "4-4-2")

        home_tactics = self.classify_style(home_formation, stats.get("home", {}))
        away_tactics = self.classify_style(away_formation, stats.get("away", {}))

        narrative = self.generate_narrative(
            fixture_id, home_team, away_team,
            home_tactics, away_tactics, momentum, minute
        )

        return {
            "home_formation": home_formation,
            "away_formation": away_formation,
            "home_tactics":   home_tactics,
            "away_tactics":   away_tactics,
            "narrative":      narrative,
            "minute":         minute,
        }

    # --------------------------------------------------
    # FALLBACK (no GPT)
    # --------------------------------------------------

    def _fallback(self, home_team, away_team, home_tactics, away_tactics) -> str:
        hs = ", ".join(home_tactics.get("active_styles", ["balanced"]))
        as_ = ", ".join(away_tactics.get("active_styles", ["balanced"]))
        hf = home_tactics.get("formation", "?")
        af = away_tactics.get("formation", "?")
        return (
            f"{home_team} ({hf}) playing {hs}. "
            f"{away_team} ({af}) responding with {as_}."
        )
