import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

CACHE_TTL = 20  # seconds — paid plan: up from 45s (matches poll interval)


STAT_MAP = {
    "Shots on Goal":    "shots_on_target",
    "Shots off Goal":   "shots_off_target",
    "Total Shots":      "shots_total",
    "Ball Possession":  "possession",
    "Dangerous Attacks":"dangerous_attacks",
    "attacks":          "dangerous_attacks",   # alternate API key
    "expected_goals":   "xg",
    "Corner Kicks":     "corners",
    "Fouls":            "fouls",
    "Total passes":     "passes_total",
    "Passes accurate":  "passes_accurate",
    "Passes %":         "pass_accuracy",
    "Goalkeeper Saves": "saves",
    "Yellow Cards":     "yellow_cards",
    "Red Cards":        "red_cards",
    "Shots insidebox":  "shots_insidebox",
    "Blocked Shots":    "shots_blocked",
}


class LiveStatsCollector:
    """
    Pulls real-time statistics from API-Football.
    Caches results to stay within rate limits.
    """

    def __init__(self):
        self._stats_cache: dict = {}
        self._lineups_cache: dict = {}
        self._events_cache: dict = {}

    # --------------------------------------------------
    # INTERNAL HTTP
    # --------------------------------------------------

    def _get(self, endpoint: str, params: dict) -> list:
        try:
            res = requests.get(
                f"{BASE_URL}/{endpoint}",
                headers=HEADERS,
                params=params,
                timeout=10
            )
            if res.status_code == 200:
                return res.json().get("response", [])
            print(f"[LIVE_STATS] HTTP {res.status_code}: {endpoint}")
            return []
        except Exception as e:
            print(f"[LIVE_STATS] Error fetching {endpoint}: {e}")
            return []

    # --------------------------------------------------
    # LIVE STATISTICS
    # --------------------------------------------------

    def get_live_stats(self, fixture_id: int) -> dict:
        """
        Returns per-team stats dict for a running fixture.
        Shape: { "home": {...}, "away": {...}, "home_team": str, "away_team": str }
        """
        now = time.time()
        cached = self._stats_cache.get(fixture_id)

        if cached and (now - cached["ts"]) < CACHE_TTL:
            return cached["data"]

        raw = self._get("fixtures/statistics", {"fixture": fixture_id})

        stats: dict = {
            "home": {},
            "away": {},
            "home_team": "",
            "away_team": "",
            "fixture_id": fixture_id,
            "fetched_at": now,
        }

        for i, team_data in enumerate(raw[:2]):
            side = "home" if i == 0 else "away"
            team_stats: dict = {}

            for s in team_data.get("statistics", []):
                mapped = STAT_MAP.get(s["type"])
                if not mapped:
                    continue
                val = s.get("value")
                if isinstance(val, str) and "%" in val:
                    try:
                        val = int(val.replace("%", "").strip())
                    except ValueError:
                        val = 0
                team_stats[mapped] = val if val is not None else 0

            stats[side] = team_stats
            stats[f"{side}_team"] = team_data.get("team", {}).get("name", "")

        self._stats_cache[fixture_id] = {"ts": now, "data": stats}

        h = stats.get("home", {})
        a = stats.get("away", {})
        print(
            f"[LIVE_STATS] fixture={fixture_id} "
            f"home={stats.get('home_team','?')} "
            f"away={stats.get('away_team','?')} | "
            f"poss={h.get('possession','?')}%/{a.get('possession','?')}% | "
            f"shots={h.get('shots_total','?')}/{a.get('shots_total','?')} | "
            f"xG={h.get('xg','n/a')}/{a.get('xg','n/a')} | "
            f"dangerous={h.get('dangerous_attacks','?')}/{a.get('dangerous_attacks','?')}"
        )

        return stats

    # --------------------------------------------------
    # LINEUPS & FORMATIONS
    # --------------------------------------------------

    @staticmethod
    def _parse_players(items: list) -> list[dict]:
        players = []
        for item in items or []:
            p = item.get("player") or {}
            name = p.get("name") or ""
            if not name:
                continue
            players.append({
                "name":   name,
                "number": p.get("number"),
                "pos":    item.get("pos") or "",
            })
        return players

    def get_lineups(self, fixture_id: int, home_name: str = "", away_name: str = "") -> dict:
        """
        Returns formations and player lists for both teams.
        Shape: { home: {formation, team, coach, starting[], bench[]}, away: {...} }
        """
        cache_key = f"{fixture_id}:{home_name}:{away_name}"
        if cache_key in self._lineups_cache:
            return self._lineups_cache[cache_key]

        raw = self._get("fixtures/lineups", {"fixture": fixture_id})
        empty_side = {
            "formation": "",
            "team": "",
            "coach": "",
            "starting": [],
            "bench": [],
        }
        lineups: dict = {"home": dict(empty_side), "away": dict(empty_side)}

        def _side_key(team_name: str) -> str | None:
            tn = (team_name or "").lower().strip()
            hn = (home_name or "").lower().strip()
            an = (away_name or "").lower().strip()
            if hn and (tn == hn or hn in tn or tn in hn):
                return "home"
            if an and (tn == an or an in tn or tn in an):
                return "away"
            return None

        for team_data in raw[:2]:
            team_name = team_data.get("team", {}).get("name", "") or ""
            coach = (team_data.get("coach") or {}).get("name", "") or ""
            side_data = {
                "formation": team_data.get("formation") or "",
                "team":      team_name,
                "coach":     coach,
                "starting":  self._parse_players(team_data.get("startXI")),
                "bench":     self._parse_players(team_data.get("substitutes")),
            }

            key = _side_key(team_name)
            if key:
                lineups[key] = side_data
            elif not lineups["home"]["team"]:
                lineups["home"] = side_data
            else:
                lineups["away"] = side_data

        self._lineups_cache[cache_key] = lineups
        return lineups

    # --------------------------------------------------
    # LIVE EVENTS
    # --------------------------------------------------

    def get_events(self, fixture_id: int) -> list:
        """
        Returns list of match events (goals, cards, subs).
        """
        now = time.time()
        cached = self._events_cache.get(fixture_id)

        if cached and (now - cached["ts"]) < CACHE_TTL:
            return cached["data"]

        raw = self._get("fixtures/events", {"fixture": fixture_id})
        events = []

        for e in raw:
            events.append({
                "minute": e.get("time", {}).get("elapsed", 0),
                "type": e.get("type", ""),
                "detail": e.get("detail", ""),
                "team": e.get("team", {}).get("name", ""),
                "player": e.get("player", {}).get("name", ""),
            })

        self._events_cache[fixture_id] = {"ts": now, "data": events}
        return events

    # --------------------------------------------------
    # LIVE SCORE + MINUTE
    # --------------------------------------------------

    def get_live_fixture(self, fixture_id: int) -> dict:
        """
        Fetch live score and real match minute from /fixtures?id=...
        Cached separately with a short TTL.
        """
        now = time.time()
        cached = self._stats_cache.get(f"fixture_{fixture_id}")
        if cached and (now - cached["ts"]) < CACHE_TTL:
            return cached["data"]

        raw = self._get("fixtures", {"id": fixture_id})
        if not raw:
            return {}

        f = raw[0]
        goals  = f.get("goals", {})
        status = f.get("fixture", {}).get("status", {})

        result = {
            "home_goals":  goals.get("home"),
            "away_goals":  goals.get("away"),
            "minute":      status.get("elapsed") or 0,
            "status_short": status.get("short", ""),
            "status_long":  status.get("long", ""),
        }

        self._stats_cache[f"fixture_{fixture_id}"] = {"ts": now, "data": result}
        return result

    # --------------------------------------------------
    # CURRENT MATCH MINUTE (from events)
    # --------------------------------------------------

    def get_match_minute(self, fixture_id: int) -> int:
        """Derive current minute from live fixture data."""
        live = self.get_live_fixture(fixture_id)
        return live.get("minute", 0) or 0
