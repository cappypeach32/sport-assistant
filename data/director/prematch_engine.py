import os
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

API_KEY  = os.getenv("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"
HEADERS  = {"x-apisports-key": API_KEY}

# GPT setup — optional
_openai_key = os.getenv("OPENAI_API_KEY", "")
_gpt_available = bool(
    _openai_key
    and not _openai_key.startswith("YOUR_")
    and len(_openai_key) > 20
)
if _gpt_available:
    try:
        from openai import OpenAI
        _gpt_client = OpenAI(api_key=_openai_key)
    except Exception:
        _gpt_available = False
        _gpt_client = None
else:
    _gpt_client = None

CACHE_TTL     = 120   # 2 мин за pre-match данни — paid plan: up from 5 min
GPT_COOLDOWN  = 600   # 10 мин между GPT calls за един матч (непроменено — GPT ограничение)

# Форма → текст
FORM_LABELS = {"W": "победа", "D": "равен", "L": "загуба"}

STATUS_IS_LIVE = {
    "1H", "2H", "HT", "ET", "BT", "P", "SUSP", "INT", "LIVE"
}
STATUS_IS_NS   = {"NS", "TBD"}
STATUS_IS_FT   = {"FT", "AET", "PEN", "WO", "AWD"}


def _is_live(status_short: str) -> bool:
    return status_short in STATUS_IS_LIVE

def _is_ns(status_short: str) -> bool:
    return status_short in STATUS_IS_NS


class PreMatchEngine:
    """
    Phase 2 — Pre-Match Analysis Engine.

    За предстоящи мачове: събира H2H, форма, класиране и генерира
    пълен предматчов анализ на Български.

    За живи мачове: editorial-ът остава като контекст + генерира
    live narrative обновления след key moments.
    """

    def __init__(self):
        self._cache:       dict = {}   # fixture_id → editorial dict
        self._gpt_ts:      dict = {}   # fixture_id → last GPT call ts
        self._raw_cache:   dict = {}   # general data cache

    # --------------------------------------------------
    # API HELPERS
    # --------------------------------------------------

    def _get(self, endpoint: str, params: dict) -> list:
        cache_key = f"{endpoint}:{sorted(params.items())}"
        cached = self._raw_cache.get(cache_key)
        if cached and time.time() - cached["ts"] < CACHE_TTL:
            return cached["data"]

        try:
            res = requests.get(
                f"{BASE_URL}/{endpoint}",
                headers=HEADERS,
                params=params,
                timeout=12,
            )
            if res.status_code == 200:
                data = res.json().get("response", [])
                self._raw_cache[cache_key] = {"ts": time.time(), "data": data}
                return data
            print(f"[PREMATCH] HTTP {res.status_code}: {endpoint}")
            return []
        except Exception as e:
            print(f"[PREMATCH] Error {endpoint}: {e}")
            return []

    # --------------------------------------------------
    # DATA FETCHERS
    # --------------------------------------------------

    def _get_fixture_meta(self, fixture_id: int) -> dict:
        """Full fixture object — team IDs, season, league, status."""
        raw = self._get("fixtures", {"id": fixture_id})
        if not raw:
            return {}
        f = raw[0]
        return {
            "home_id":      f["teams"]["home"]["id"],
            "away_id":      f["teams"]["away"]["id"],
            "home_name":    f["teams"]["home"]["name"],
            "away_name":    f["teams"]["away"]["name"],
            "league_id":    f["league"]["id"],
            "league_name":  f["league"]["name"],
            "season":       f["league"]["season"],
            "date":         f["fixture"]["date"],
            "status_short": f["fixture"]["status"]["short"],
            "status_long":  f["fixture"]["status"]["long"],
            "home_goals":   f["goals"].get("home"),
            "away_goals":   f["goals"].get("away"),
        }

    def _get_h2h(self, home_id: int, away_id: int) -> list:
        """Last 8 H2H matches."""
        raw = self._get("fixtures/headtohead", {
            "h2h": f"{home_id}-{away_id}",
            "last": 8,
        })
        results = []
        for f in raw:
            results.append({
                "date":       f["fixture"]["date"][:10],
                "home":       f["teams"]["home"]["name"],
                "away":       f["teams"]["away"]["name"],
                "home_goals": f["goals"].get("home", 0),
                "away_goals": f["goals"].get("away", 0),
                "competition": f["league"]["name"],
            })
        return results

    def _get_team_stats(self, team_id: int, league_id: int, season: int) -> dict:
        """
        Get team form from last 5 finished matches (ANY competition).
        This works regardless of league coverage in API-Football.
        Falls back to league statistics only if recent fixtures are empty.
        """
        # Primary: last 10 finished matches from any competition
        raw = self._get("fixtures", {"team": team_id, "last": 10, "status": "FT"})

        if raw:
            wins = draws = losses = scored = conceded = 0
            form = []

            for f in raw:
                home_id    = f["teams"]["home"]["id"]
                home_goals = f["goals"].get("home") or 0
                away_goals = f["goals"].get("away") or 0

                if home_id == team_id:
                    s_goals, c_goals = home_goals, away_goals
                else:
                    s_goals, c_goals = away_goals, home_goals

                scored   += s_goals
                conceded += c_goals

                if s_goals > c_goals:
                    wins += 1;  form.append("W")
                elif s_goals == c_goals:
                    draws += 1; form.append("D")
                else:
                    losses += 1; form.append("L")

            played    = wins + draws + losses
            avg_score = round(scored   / played, 2) if played else None
            avg_conc  = round(conceded / played, 2) if played else None

            return {
                "played":    played,
                "wins":      wins,
                "draws":     draws,
                "losses":    losses,
                "scored":    scored,
                "conceded":  conceded,
                "avg_score": avg_score,
                "avg_conc":  avg_conc,
                "form":      form,
                "form_str":  "".join(form) if form else "—",
                "clean_sheets":      sum(1 for f in form if f == "W"),
                "failed_to_score":   0,
            }

        # Fallback: league statistics (works for domestic leagues)
        raw_stats = self._get("teams/statistics", {
            "team": team_id, "league": league_id, "season": season,
        })
        if not raw_stats:
            return {}

        s    = raw_stats[0] if isinstance(raw_stats, list) else raw_stats
        fix  = s.get("fixtures", {})
        goals = s.get("goals", {})
        form_str = s.get("form", "") or ""

        played = fix.get("played", {}).get("total", 0) or 0
        wins   = fix.get("wins",   {}).get("total", 0) or 0
        draws  = fix.get("draws",  {}).get("total", 0) or 0
        losses = fix.get("loses",  {}).get("total", 0) or 0
        scored   = goals.get("for",     {}).get("total", {}).get("total", 0) or 0
        conceded = goals.get("against", {}).get("total", {}).get("total", 0) or 0
        avg_score_raw = goals.get("for",     {}).get("average", {}).get("total")
        avg_conc_raw  = goals.get("against", {}).get("average", {}).get("total")

        return {
            "played":    played,
            "wins":      wins,
            "draws":     draws,
            "losses":    losses,
            "scored":    scored,
            "conceded":  conceded,
            "avg_score": float(avg_score_raw) if avg_score_raw else None,
            "avg_conc":  float(avg_conc_raw)  if avg_conc_raw  else None,
            "form":      list(form_str[-5:]) if form_str else [],
            "form_str":  form_str[-5:] if form_str else "—",
            "clean_sheets": s.get("clean_sheet", {}).get("total", 0) or 0,
            "failed_to_score": s.get("failed_to_score", {}).get("total", 0) or 0,
        }

    def _get_top_scorers(self, league_id: int, season: int, home_id: int, away_id: int) -> dict:
        """
        Fetch league top scorers and filter for the two teams.
        Returns { "home": [...], "away": [...] }
        """
        raw = self._get("players/topscorers", {"league": league_id, "season": season})
        result = {"home": [], "away": []}

        for entry in raw[:50]:
            player    = entry.get("player", {})
            stats_list = entry.get("statistics", [])
            if not stats_list:
                continue
            stat      = stats_list[0]
            team_id   = stat.get("team", {}).get("id")
            goals     = stat.get("goals", {}).get("total") or 0
            assists   = stat.get("goals", {}).get("assists") or 0
            apps      = stat.get("games", {}).get("appearences") or 0

            if team_id == home_id:
                result["home"].append({
                    "name":    player.get("name", ""),
                    "goals":   goals,
                    "assists": assists,
                    "apps":    apps,
                })
            elif team_id == away_id:
                result["away"].append({
                    "name":    player.get("name", ""),
                    "goals":   goals,
                    "assists": assists,
                    "apps":    apps,
                })

        return result

    def _get_coaches(self, home_id: int, away_id: int) -> dict:
        """Fetch coach names for both teams."""
        result = {}
        for side, team_id in [("home", home_id), ("away", away_id)]:
            raw = self._get("coachs", {"team": team_id})
            if raw:
                result[side] = raw[0].get("name", "")
        return result

    def _get_standings(self, league_id: int, season: int, home_id: int, away_id: int) -> dict:
        """
        Extract standings rows for both teams + full sorted table for table-impact calc.
        Returns { "home": {...}, "away": {...}, "full_table": [...] }
        """
        raw = self._get("standings", {"league": league_id, "season": season})
        if not raw:
            return {}

        standings_list = []
        try:
            standings_list = raw[0]["league"]["standings"][0]
        except (IndexError, KeyError, TypeError):
            pass

        full_table = []
        result: dict = {"full_table": []}

        for row in standings_list:
            tid   = row["team"]["id"]
            entry = {
                "team_id":     tid,
                "team_name":   row["team"]["name"],
                "position":    row["rank"],
                "points":      row["points"],
                "played":      row["all"]["played"],
                "goal_diff":   row["goalsDiff"],
                "form":        row.get("form", ""),
                "description": row.get("description", ""),
                "wins":        row["all"].get("win", 0),
                "draws":       row["all"].get("draw", 0),
                "losses":      row["all"].get("lose", 0),
            }
            full_table.append(entry)
            if tid in (home_id, away_id):
                result["home" if tid == home_id else "away"] = entry

        result["full_table"] = sorted(full_table, key=lambda x: x["position"])
        return result

    def _get_referee_data(self, fixture_id: int) -> dict:
        """
        Fetch referee name from fixture, then calculate stats from their recent games.
        Returns referee profile dict.
        """
        raw = self._get("fixtures", {"id": fixture_id})
        if not raw:
            return {}

        fix         = raw[0]
        ref_raw     = fix.get("fixture", {}).get("referee") or ""
        if not ref_raw:
            return {}

        ref_name    = ref_raw.split(",")[0].strip()
        season      = fix.get("league", {}).get("season", 2025)

        recent_fix  = self._get("fixtures", {
            "referee": ref_name,
            "season":  season,
            "status":  "FT",
        })
        if not recent_fix:
            return {"name": ref_name, "games_this_season": 0}

        total_games  = min(len(recent_fix), 20)
        total_goals  = 0
        home_wins    = 0

        for f in recent_fix[:20]:
            sc  = f.get("score", {}).get("fulltime", {})
            hg  = sc.get("home") or 0
            ag  = sc.get("away") or 0
            total_goals += hg + ag
            if hg > ag:
                home_wins += 1

        # Card stats from last 5 fixtures' events (parallelized)
        yellow_total = 0
        red_total    = 0
        pen_total    = 0
        games_evented = 0

        fixture_ids = [f.get("fixture", {}).get("id") for f in recent_fix[:5] if f.get("fixture", {}).get("id")]

        from concurrent.futures import ThreadPoolExecutor
        def _fetch_events(fid):
            return self._get("fixtures/events", {"fixture": fid})

        with ThreadPoolExecutor(max_workers=5) as executor:
            event_results = list(executor.map(_fetch_events, fixture_ids))

        for events in event_results:
            for e in events:
                etype  = e.get("type", "")
                detail = e.get("detail", "")
                if etype == "Card":
                    if "Yellow" in detail:
                        yellow_total += 1
                    elif "Red" in detail:
                        red_total += 1
                elif etype == "Goal" and detail == "Penalty":
                    pen_total += 1
            games_evented += 1

        avg_yellow = round(yellow_total / max(games_evented, 1), 1)
        avg_red    = round(red_total    / max(games_evented, 1), 2)
        avg_pen    = round(pen_total    / max(games_evented, 1), 2)
        avg_goals  = round(total_goals  / max(total_games, 1), 1)

        if   avg_yellow >= 5:   strictness = "много строг"
        elif avg_yellow >= 3.5: strictness = "строг"
        elif avg_yellow >= 2:   strictness = "среден"
        else:                   strictness = "либерален"

        return {
            "name":               ref_name,
            "games_this_season":  total_games,
            "avg_goals_per_game": avg_goals,
            "home_win_pct":       round(home_wins / max(total_games, 1) * 100),
            "avg_yellow":         avg_yellow,
            "avg_red":            avg_red,
            "avg_penalties":      avg_pen,
            "strictness":         strictness,
        }

    def _get_injuries(self, fixture_id: int, home_id: int, away_id: int) -> dict:
        """
        Fetch injury & suspension list for both teams for this fixture.
        Returns { "home": [...], "away": [...] }
        """
        raw    = self._get("injuries", {"fixture": fixture_id})
        result = {"home": [], "away": []}

        for entry in raw:
            team_id = entry.get("team", {}).get("id")
            player  = entry.get("player", {})
            info = {
                "name":   player.get("name", ""),
                "type":   entry.get("player", {}).get("type", ""),    # "Injured" / "Missing Fixture"
                "reason": entry.get("player", {}).get("reason", ""),  # "Knee Injury" etc.
            }
            if   team_id == home_id: result["home"].append(info)
            elif team_id == away_id: result["away"].append(info)

        return result

    @staticmethod
    def calculate_table_impact(
        full_table: list,
        home_id:    int,
        away_id:    int,
        home_goals: int,
        away_goals: int,
        home_name:  str = "",
        away_name:  str = "",
    ) -> dict:
        """
        Given live score, simulate what the standings would look like if the result stands.
        Returns impact dict with position changes and context.
        """
        if not full_table:
            return {}

        table = {row["team_id"]: dict(row) for row in full_table}
        if home_id not in table or away_id not in table:
            return {}

        total_teams = len(full_table)

        # Determine point gains
        if home_goals > away_goals:
            h_gain, a_gain = 3, 0
            result_lbl = f"победа на {home_name or 'домакина'}"
        elif away_goals > home_goals:
            h_gain, a_gain = 0, 3
            result_lbl = f"победа на {away_name or 'гостите'}"
        else:
            h_gain = a_gain = 1
            result_lbl = "равен"

        def projected_pos(team_id: int, pts_gain: int) -> int:
            new_pts = table[team_id]["points"] + pts_gain
            new_gd  = table[team_id]["goal_diff"] + (home_goals - away_goals if team_id == home_id else away_goals - home_goals)
            pos = 1
            for tid, row in table.items():
                if tid == team_id:
                    continue
                other_pts = row["points"]
                if other_pts > new_pts or (other_pts == new_pts and row["goal_diff"] > new_gd):
                    pos += 1
            return pos

        h_cur_pos  = table[home_id]["position"]
        a_cur_pos  = table[away_id]["position"]
        h_new_pos  = projected_pos(home_id, h_gain)
        a_new_pos  = projected_pos(away_id, a_gain)
        h_cur_pts  = table[home_id]["points"]
        a_cur_pts  = table[away_id]["points"]

        # Context labels (top 4/relegation are approximate — use description field)
        def pos_label(pos: int, desc: str) -> str:
            if desc:
                return desc
            if pos == 1:          return "Лидер"
            if pos <= 4:          return "Топ 4"
            if pos >= total_teams - 2: return "Зона на изпадане"
            return f"{pos}. място"

        h_desc = table[home_id].get("description", "")
        a_desc = table[away_id].get("description", "")

        return {
            "result_label":  result_lbl,
            "home": {
                "name":        home_name,
                "current_pos": h_cur_pos,
                "new_pos":     h_new_pos,
                "pos_change":  h_cur_pos - h_new_pos,  # positive = moved up
                "pts_before":  h_cur_pts,
                "pts_after":   h_cur_pts + h_gain,
                "pts_gain":    h_gain,
                "pos_label":   pos_label(h_new_pos, h_desc if h_gain > 0 else ""),
            },
            "away": {
                "name":        away_name,
                "current_pos": a_cur_pos,
                "new_pos":     a_new_pos,
                "pos_change":  a_cur_pos - a_new_pos,
                "pts_before":  a_cur_pts,
                "pts_after":   a_cur_pts + a_gain,
                "pts_gain":    a_gain,
                "pos_label":   pos_label(a_new_pos, a_desc if a_gain > 0 else ""),
            },
        }

    # --------------------------------------------------
    # RULE-BASED ANALYSIS (no GPT needed)
    # --------------------------------------------------

    def _build_rule_based(
        self,
        meta: dict,
        h2h: list,
        home_stats: dict,
        away_stats: dict,
        standings: dict,
        top_scorers: dict = None,
        coaches: dict = None,
        referee: dict = None,
        injuries: dict = None,
    ) -> dict:
        """
        Builds structured pre-match data from real API data.
        Used as-is when no GPT, or as context for GPT prompt.
        """
        home = meta["home_name"]
        away = meta["away_name"]
        league = meta["league_name"]
        date_str = meta["date"][:10] if meta.get("date") else "—"

        # Form summary
        def form_bg(form_list):
            if not form_list:
                return "Няма данни"
            return " → ".join(FORM_LABELS.get(r, r) for r in form_list)

        hs = home_stats
        as_ = away_stats

        home_form_bg = form_bg(hs.get("form", []))
        away_form_bg = form_bg(as_.get("form", []))

        # H2H summary
        h2h_lines = []
        home_h2h_wins = 0
        away_h2h_wins = 0
        total_h2h_goals = 0

        for m in h2h[:5]:
            hg = m.get("home_goals", 0) or 0
            ag = m.get("away_goals", 0) or 0
            total_h2h_goals += hg + ag
            winner = m["home"] if hg > ag else (m["away"] if ag > hg else "равен")
            if winner == home:
                home_h2h_wins += 1
            elif winner == away:
                away_h2h_wins += 1
            h2h_lines.append(
                f"{m['date']} — {m['home']} {hg}:{ag} {m['away']}"
            )

        avg_h2h_goals = round(total_h2h_goals / max(len(h2h[:5]), 1), 1)

        # Standings
        h_stand = standings.get("home", {})
        a_stand = standings.get("away", {})

        # Key advantages
        home_advantages = []
        away_advantages = []

        if hs.get("wins", 0) > as_.get("wins", 0):
            home_advantages.append("По-добри резултати в сезона")
        else:
            away_advantages.append("По-добри резултати в сезона")

        h_avg_s = float(hs.get("avg_score") or 0)
        a_avg_s = float(as_.get("avg_score") or 0)
        h_avg_c = float(hs.get("avg_conc")  or 0)
        a_avg_c = float(as_.get("avg_conc")  or 0)

        if h_avg_s > a_avg_s and h_avg_s > 0:
            home_advantages.append(f"По-голяма атакуваща ефективност ({h_avg_s:.2f} гола/мач)")
        elif a_avg_s > h_avg_s and a_avg_s > 0:
            away_advantages.append(f"По-голяма атакуваща ефективност ({a_avg_s:.2f} гола/мач)")

        if h_avg_c < a_avg_c and a_avg_c > 0:
            home_advantages.append(f"По-стабилна отбрана ({h_avg_c:.2f} допуснати/мач)")
        elif a_avg_c < h_avg_c and h_avg_c > 0:
            away_advantages.append(f"По-стабилна отбрана ({a_avg_c:.2f} допуснати/мач)")

        if h_stand.get("position", 99) < a_stand.get("position", 99):
            home_advantages.append(f"По-добро класиране — {h_stand.get('position')} място")
        elif a_stand.get("position", 99) < h_stand.get("position", 99):
            away_advantages.append(f"По-добро класиране — {a_stand.get('position')} място")

        if home_h2h_wins > away_h2h_wins:
            home_advantages.append(f"По-добра H2H история ({home_h2h_wins} победи)")
        elif away_h2h_wins > home_h2h_wins:
            away_advantages.append(f"По-добра H2H история ({away_h2h_wins} победи)")

        # Key factors
        key_factors = []
        if avg_h2h_goals > 0:
            key_factors.append(f"Средно {avg_h2h_goals} гола на директен двубой между двата отбора")
        if h_avg_s > 0:
            key_factors.append(f"{home} вкарва средно {h_avg_s:.2f} гола на мач тази сезон")
        if a_avg_s > 0:
            key_factors.append(f"{away} вкарва средно {a_avg_s:.2f} гола на мач тази сезон")
        if h_stand.get("position"):
            key_factors.append(f"{home} е на {h_stand['position']} място с {h_stand.get('points', 0)} точки")
        if a_stand.get("position"):
            key_factors.append(f"{away} е на {a_stand['position']} място с {a_stand.get('points', 0)} точки")
        if not key_factors:
            key_factors = ["Статистически данни не са налични за тази лига в момента"]

        # Prediction (weighted: form points + standings + home advantage)
        h_pts   = hs.get("wins", 0) * 3 + hs.get("draws", 0)
        a_pts   = as_.get("wins", 0) * 3 + as_.get("draws", 0)
        h_pos   = h_stand.get("position", 10) or 10
        a_pos   = a_stand.get("position", 10) or 10
        # Home advantage bonus (5 pts) + better position bonus
        h_score = h_pts + 5 + max(0, a_pos - h_pos) * 0.5
        a_score = a_pts       + max(0, h_pos - a_pos) * 0.5

        total = h_score + a_score
        if total < 5:
            # Not enough data — use balanced defaults
            home_win_pct, away_win_pct, draw_pct = 40, 30, 30
        else:
            home_win_pct = round(h_score / (total + h_score * 0.3) * 100)
            away_win_pct = round(a_score / (total + a_score * 0.3) * 100)
            draw_pct     = max(5, 100 - home_win_pct - away_win_pct)

        # Score prediction
        h_avg    = float(hs.get("avg_score") or 1.3)
        a_avg    = float(as_.get("avg_score") or 1.0)
        pred_home = max(1, round(h_avg * 0.85))
        pred_away = max(0, round(a_avg * 0.85))

        # Betting markets — based on Poisson approximation and form data
        import math
        exp_goals = h_avg + a_avg   # expected total goals

        # Over/Under 2.5
        # P(goals >= 3) from Poisson: 1 - P(0) - P(1) - P(2)
        def _poisson_under(lam, k):
            return sum(math.exp(-lam) * lam**i / math.factorial(i) for i in range(k+1))
        over25_pct  = round((1 - _poisson_under(exp_goals, 2)) * 100)
        under25_pct = 100 - over25_pct
        over15_pct  = round((1 - _poisson_under(exp_goals, 1)) * 100)
        under15_pct = 100 - over15_pct
        over35_pct  = round((1 - _poisson_under(exp_goals, 3)) * 100)

        # BTTS — P(home scores >= 1) * P(away scores >= 1)
        p_home_scores = round((1 - math.exp(-h_avg)) * 100)
        p_away_scores = round((1 - math.exp(-a_avg)) * 100)
        btts_pct      = round(p_home_scores * p_away_scores / 100)

        # Clean sheets
        cs_home_pct = round(math.exp(-a_avg) * 100)   # P(away scores 0)
        cs_away_pct = round(math.exp(-h_avg) * 100)   # P(home scores 0)

        # Alternative score (2nd most likely scenario)
        alt_home = pred_home + (1 if pred_away == 0 else 0)
        alt_away = pred_away + (0 if pred_home > pred_away else 1)
        if f"{alt_home}:{alt_away}" == f"{pred_home}:{pred_away}":
            alt_away = pred_away + 1

        return {
            "home":           home,
            "away":           away,
            "league":         league,
            "date":           date_str,
            "home_form":      hs.get("form_str", "—"),
            "away_form":      as_.get("form_str", "—"),
            "home_form_bg":   home_form_bg,
            "away_form_bg":   away_form_bg,
            "home_stats":     hs,
            "away_stats":     as_,
            "h2h":            h2h_lines,
            "h2h_raw":        h2h[:5],
            "h2h_home_wins":  home_h2h_wins,
            "h2h_away_wins":  away_h2h_wins,
            "avg_h2h_goals":  avg_h2h_goals,
            "home_standing":  h_stand,
            "away_standing":  a_stand,
            "home_advantages": home_advantages or ["Данните се зареждат"],
            "away_advantages": away_advantages or ["Данните се зареждат"],
            "key_factors":    key_factors,
            "prediction": {
                "home_win_pct":  home_win_pct,
                "away_win_pct":  away_win_pct,
                "draw_pct":      draw_pct,
                "likely_score":  f"{pred_home}:{pred_away}",
                "alt_score":     f"{alt_home}:{alt_away}",
                "over25_pct":    over25_pct,
                "under25_pct":   under25_pct,
                "over15_pct":    over15_pct,
                "under15_pct":   under15_pct,
                "over35_pct":    over35_pct,
                "btts_pct":      btts_pct,
                "btts_no_pct":   100 - btts_pct,
                "cs_home_pct":   cs_home_pct,
                "cs_away_pct":   cs_away_pct,
                "exp_goals":     round(exp_goals, 2),
                "source":        "statistical_model",
                "source_label":  "Статистически модел (форма + класиране + Poisson) — не е съвет за залагане",
            },
            "gpt_narrative":  "",   # populated if GPT available
            "top_scorers":    top_scorers or {"home": [], "away": []},
            "coaches":        coaches or {},
            "referee":        referee or {},
            "injuries":       injuries or {"home": [], "away": []},
            "full_table":     standings.get("full_table", []),
        }

    # --------------------------------------------------
    # GPT EDITORIAL (Bulgarian)
    # --------------------------------------------------

    def _has_real_data(self, data: dict) -> bool:
        """Returns True only when we have verified API data to base analysis on."""
        hs  = data.get("home_stats", {})
        as_ = data.get("away_stats", {})
        hst = data.get("home_standing", {})
        ast = data.get("away_standing", {})

        has_form     = (hs.get("played", 0) or 0) > 0 or (as_.get("played", 0) or 0) > 0
        has_standing = bool(hst.get("position") or ast.get("position"))
        has_h2h      = len(data.get("h2h", [])) > 0

        return has_form or has_standing or has_h2h

    def _generate_gpt_editorial(
        self,
        fixture_id: int,
        data: dict,
    ) -> str:
        if not _gpt_available or _gpt_client is None:
            return ""

        # CRITICAL: never call GPT without real data — prevents hallucination
        if not self._has_real_data(data):
            print(f"[PREMATCH] Skipping GPT — no real data available for fixture {fixture_id}")
            return ""

        now = time.time()
        if now - self._gpt_ts.get(fixture_id, 0) < GPT_COOLDOWN:
            return data.get("gpt_narrative", "")

        home = data["home"]
        away = data["away"]
        hs   = data["home_stats"]
        as_  = data["away_stats"]
        hst  = data["home_standing"]
        ast  = data["away_standing"]

        h2h_text = "\n".join(data["h2h"][:5]) if data["h2h"] else "Няма данни"

        # Data quality assessment
        has_stats    = (hs.get("played") or 0) > 0 or (as_.get("played") or 0) > 0
        has_standing = bool(hst.get("position") or ast.get("position"))
        has_h2h      = bool(data["h2h"])
        has_scorers  = bool(data.get("top_scorers", {}).get("home") or data.get("top_scorers", {}).get("away"))
        coaches      = data.get("coaches", {})

        # Top scorers text
        def scorer_line(player):
            g = player.get("goals", 0)
            a = player.get("assists", 0)
            parts = []
            if g: parts.append(f"{g} гола")
            if a: parts.append(f"{a} асист.")
            return f"{player['name']} ({', '.join(parts)})" if parts else player['name']

        home_scorers_text = "\n".join(f"  • {scorer_line(p)}" for p in data.get("top_scorers", {}).get("home", [])[:5])
        away_scorers_text = "\n".join(f"  • {scorer_line(p)}" for p in data.get("top_scorers", {}).get("away", [])[:5])

        # Referee context
        ref = data.get("referee", {})
        ref_text = ""
        if ref.get("name"):
            ref_text = (
                f"\nСЪДИЯ: {ref['name']} | "
                f"Строгост: {ref.get('strictness','н/д')} | "
                f"Средно жълти: {ref.get('avg_yellow','н/д')}/мач | "
                f"Средно червени: {ref.get('avg_red','н/д')}/мач | "
                f"Средно дузпи: {ref.get('avg_pen','н/д')}/мач"
            )

        # Injuries context — data["injuries"] is {"home": [...], "away": [...]}
        inj_dict  = data.get("injuries", {})
        inj_home  = inj_dict.get("home", []) if isinstance(inj_dict, dict) else []
        inj_away  = inj_dict.get("away", []) if isinstance(inj_dict, dict) else []
        def inj_line(i): return f"{i.get('name', i.get('player','?'))} ({i.get('type','?')}: {i.get('reason','?')})"
        inj_text = ""
        if inj_home or inj_away:
            inj_text = "\nКОНТУЗИИ / НАКАЗАНИЯ:"
            if inj_home:
                inj_text += f"\n{home}: " + " | ".join(inj_line(i) for i in inj_home[:5])
            if inj_away:
                inj_text += f"\n{away}: " + " | ".join(inj_line(i) for i in inj_away[:5])

        if not has_stats and not has_standing and not has_h2h:
            return ""

        stats_warning = "" if has_stats else \
            f"\n⚠ ВАЖНО: Детайлна статистика НЕ е налична за {data['league']}. Не измисляй резултати или числа. Анализирай само контекста."

        prompt = f"""Ти си ТОП спортен анализатор и коментатор за LIVE СТРИЙМ ПРЕДАВАНЕ. Пишеш САМО на Български. Стилът е ангажиращ, ТВ-готов, богат на детайли и ФАКТОЛОГИЧЕН — аудиторията ще следи предаването с теб целия мач.{stats_warning}

═══════════════════════════════════════
МАЧ: {home} срещу {away}
ТУРНИР: {data['league']} | ДАТА: {data['date']}
ТРЕНЬОРИ: {home}: {coaches.get('home', 'н/д')} | {away}: {coaches.get('away', 'н/д')}{ref_text}{inj_text}
═══════════════════════════════════════

КЛАСИРАНЕ:
{home}: {f"{hst['position']}. място, {hst['points']} точки, {hst.get('played',0)} изиграни" if hst.get('position') else 'н/д'}
{away}: {f"{ast['position']}. място, {ast['points']} точки, {ast.get('played',0)} изиграни" if ast.get('position') else 'н/д'}

ФОРМА — последни 10 мача:
{home}: {data['home_form']}
  П:{hs.get('wins',0)} Р:{hs.get('draws',0)} З:{hs.get('losses',0)} | Вкарани:{hs.get('scored',0)} Допуснати:{hs.get('conceded',0)} | Средно:{f"{hs['avg_score']:.2f}" if hs.get('avg_score') else 'н/д'} гола/мач
{away}: {data['away_form']}
  П:{as_.get('wins',0)} Р:{as_.get('draws',0)} З:{as_.get('losses',0)} | Вкарани:{as_.get('scored',0)} Допуснати:{as_.get('conceded',0)} | Средно:{f"{as_['avg_score']:.2f}" if as_.get('avg_score') else 'н/д'} гола/мач

КЛЮЧОВИ ИГРАЧИ (топ голмайстори в лигата):
{home}:
{home_scorers_text if home_scorers_text else '  н/д'}
{away}:
{away_scorers_text if away_scorers_text else '  н/д'}

H2H — последни срещи:
{chr(10).join(data['h2h'][:8]) if has_h2h else 'Няма данни'}
{f"Общо: {home} {data['h2h_home_wins']}П : {data['h2h_away_wins']}П {away} | Средно голове/мач: {data['avg_h2h_goals']}" if has_h2h else ''}

═══════════════════════════════════════
СТРОГИ ПРАВИЛА:
1. Използвай САМО данните по-горе — никакви измислени числа или резултати
2. Ако данни липсват — пиши "данните не са налични" или пропусни точката
3. Не споменавай играчи, освен ако са изрично дадени по-горе
4. Цитирай КОНКРЕТНИ числа от данните — не пиши общи фрази
5. Пиши богато и детайлно — аудиторията ще чете по време на ЦЕЛИЯ мач
═══════════════════════════════════════

Напиши ПЪЛЕН BROADCAST GUIDE точно по тази структура (без да пропускаш секции):

## УВОД ЗА СТРИЙМА
(2 параграфа — как да отвориш предаването, какъв е контекстът на мача в турнира, защо е важен за двата отбора, какво е на залог)

## ФОРМА И МОМЕНТ
### {home}
(2-3 параграфа — последни резултати с конкретни числа, тенденция нагоре/надолу, силна/слаба страна, домакинска/гостенска форма ако е релевантно)
### {away}
(2-3 параграфа — последни резултати с конкретни числа, тенденция нагоре/надолу, силна/слаба страна)

## КЛЮЧОВИ ИГРАЧИ ЗА НАБЛЮДЕНИЕ
(Само играчи дадени в данните. За всеки: роля в отбора, конкретна статистика, защо е опасен днес)
### {home}
• [Играч]: [позиция/роля] — [статистика] — [защо да го следим]
• [Играч]: [позиция/роля] — [статистика] — [защо да го следим]
### {away}
• [Играч]: [позиция/роля] — [статистика] — [защо да го следим]
• [Играч]: [позиция/роля] — [статистика] — [защо да го следим]

## ТАКТИЧЕСКИ РАЗБОР
### Как ще играе {home}?
(2 параграфа — вероятна система, силни страни, как атакуват, как се защитават, стандартни положения)
### Как ще играе {away}?
(2 параграфа — вероятна система, силни страни, как атакуват, как се защитават, стандартни положения)
### Ключовото тактическо противостоене
(1-2 изречения — кой конкретен дуел/зона на терена ще реши мача)
### Слабите места
• {home}: [конкретна слабост]
• {away}: [конкретна слабост]

## ГОВОРНИ ТОЧКИ ЗА СТРИЙМА
(7 теми — конкретни, базирани на данните, с достатъчно контекст за 2-3 минути дискусия)
1. **[Тема]**: [3-4 изречения с контекст, числа и мнение]
2. **[Тема]**: [3-4 изречения с контекст, числа и мнение]
3. **[Тема]**: [3-4 изречения с контекст, числа и мнение]
4. **[Тема]**: [3-4 изречения с контекст, числа и мнение]
5. **[Тема]**: [3-4 изречения с контекст, числа и мнение]
6. **[Тема]**: [3-4 изречения с контекст, числа и мнение]
7. **[Тема]**: [3-4 изречения с контекст, числа и мнение]

## ИСТОРИЧЕСКИ ФАКТИ
(7-8 конкретни факта от H2H и статистиката — само реални данни от по-горе)
• [факт с конкретни числа]
• [факт с конкретни числа]
• [факт с конкретни числа]
• [факт с конкретни числа]
• [факт с конкретни числа]
• [факт с конкретни числа]
• [факт с конкретни числа]

## ОЧАКВАНЕ ЗА МАЧА
(2 параграфа — очакван темп, стил на игра, кога да се очакват голове, дали очакваш отворен/затворен мач, какви моменти може да са ключови)

## ПРОГНОЗА
Победител: [отбор или равен]
Вероятен резултат: X:X
Алтернативен сценарий: X:X (ако мачът се развие по-различно)
И двата отбора да вкарат: [Да/Не]
Над 2.5 гола: [Вероятно/Малко вероятно]
Мотивация: (2-3 изречения с конкретни аргументи от данните защо)"""

        try:
            resp = _gpt_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=3500,
                temperature=0.72,
            )
            narrative = resp.choices[0].message.content.strip()
            self._gpt_ts[fixture_id] = now
            print(f"[PREMATCH] GPT editorial generated for fixture {fixture_id}")
            return narrative
        except Exception as e:
            print(f"[PREMATCH] GPT error: {e}")
            return ""

    # --------------------------------------------------
    # LIVE NARRATIVE (Bulgarian, triggered by key moments)
    # --------------------------------------------------

    def generate_live_narrative(
        self,
        fixture_id: int,
        home_team: str,
        away_team: str,
        minute: int,
        momentum: dict,
        key_moments: list,
    ) -> str:
        """
        Generates a short Bulgarian narrative update for the current
        match state. Rate-limited per fixture.
        """
        if not _gpt_available or _gpt_client is None:
            return self._rule_based_narrative(home_team, away_team, minute, momentum)

        now = time.time()
        live_key = f"live_{fixture_id}"
        if now - self._gpt_ts.get(live_key, 0) < 120:  # 2 мин cooldown
            return ""

        hm   = momentum.get("home_momentum_pct", 50)
        am   = momentum.get("away_momentum_pct", 50)
        hxd  = momentum.get("home_xg_delta", 0)
        axd  = momentum.get("away_xg_delta", 0)
        h_tr = momentum.get("home_trend", "")
        a_tr = momentum.get("away_trend", "")

        moments_text = ""
        if key_moments:
            top = key_moments[0]
            moments_text = f"Засечен момент: {top.get('title','')} — {top.get('message','')}"

        prompt = f"""Ти си TV спортен коментатор. Пишеш САМО на Български. Напиши 1-2 изречения за момента в мача.

{home_team} срещу {away_team} — {minute}'

Импулс: {home_team} {hm:.0f}% / {away_team} {am:.0f}%
xG тренд: {home_team} {h_tr} | {away_team} {a_tr}
xG Δ: {home_team} +{hxd:.2f} / {away_team} +{axd:.2f} в последните 8 мин
{moments_text}

Напиши 1-2 конкретни изречения. Без излишни думи."""

        try:
            resp = _gpt_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
                temperature=0.7,
            )
            narrative = resp.choices[0].message.content.strip()
            self._gpt_ts[live_key] = now
            return narrative
        except Exception as e:
            print(f"[PREMATCH] Live narrative GPT error: {e}")
            return self._rule_based_narrative(home_team, away_team, minute, momentum)

    def _rule_based_narrative(
        self, home: str, away: str, minute: int, momentum: dict
    ) -> str:
        hm  = momentum.get("home_momentum_pct", 50)
        am  = momentum.get("away_momentum_pct", 50)
        hxd = momentum.get("home_xg_delta", 0)
        axd = momentum.get("away_xg_delta", 0)

        if hm > 65:
            return f"{minute}' — {home} доминира с {hm:.0f}% импулс. xG Δ +{hxd:.2f} в последните 8 минути показва сериозен натиск."
        elif am > 65:
            return f"{minute}' — {away} поема инициативата с {am:.0f}% импулс. Защитата на {home} е под сериозен натиск."
        else:
            return f"{minute}' — Балансиран мач. Двата отбора се неутрализират взаимно в средата на терена."

    # --------------------------------------------------
    # HALF-TIME ANALYSIS
    # --------------------------------------------------

    _ht_cache: dict = {}  # fixture_id -> { ts, text }
    HT_COOLDOWN = 1800    # regenerate only once per half-time

    def generate_halftime_analysis(
        self,
        fixture_id: int,
        home: str, away: str,
        live_stats: dict,
        events: list,
        score_home: int, score_away: int,
    ) -> str:
        """
        Generate a detailed half-time broadcast summary.
        Triggered when match status == 'HT'.
        """
        if not _gpt_available or _gpt_client is None:
            return self._rule_based_halftime(
                home, away, live_stats, events, score_home, score_away
            )

        now = time.time()
        cached = self._ht_cache.get(fixture_id)
        if cached and now - cached["ts"] < self.HT_COOLDOWN:
            return cached["text"]

        hs = live_stats.get("home", {})
        as_ = live_stats.get("away", {})

        events_text = ""
        goals   = [e for e in events if e.get("type") == "Goal"]
        cards   = [e for e in events if e.get("type") == "Card"]
        subs    = [e for e in events if e.get("type") == "subst"]

        if goals:
            events_text += "ГОЛОВЕ:\n" + "\n".join(
                f"  {e['time']['elapsed']}' — {e['team']['name']}: {e['player']['name']}"
                for e in goals
            ) + "\n"
        if cards:
            events_text += "КАРТОНИ:\n" + "\n".join(
                f"  {e['time']['elapsed']}' — {e['team']['name']}: {e['player']['name']} ({e['detail']})"
                for e in cards
            ) + "\n"
        if subs:
            events_text += "СМЕНИ:\n" + "\n".join(
                f"  {e['time']['elapsed']}' — {e['team']['name']}: {e.get('assist',{}).get('name','?')} → {e['player']['name']}"
                for e in subs
            ) + "\n"

        prompt = f"""Ти си спортен коментатор за стрийм предаване. Пишеш САМО на Български. Стилът е ТВ-готов и ангажиращ.

ПОЛУВРЕМЕТО НА МАЧ: {home} срещу {away}
РЕЗУЛТАТ СЛЕД ПЪРВО ПОЛУВРЕМЕ: {home} {score_home}:{score_away} {away}

СТАТИСТИКА — ПЪРВО ПОЛУВРЕМЕ:
{home}: {hs.get('shots_total',0)} удара ({hs.get('shots_on_target',0)} в рамките) | Владение: {hs.get('possession',0)}% | xG: {hs.get('xg','—')} | Ъглови: {hs.get('corners',0)} | Фаули: {hs.get('fouls',0)}
{away}: {as_.get('shots_total',0)} удара ({as_.get('shots_on_target',0)} в рамките) | Владение: {as_.get('possession',0)}% | xG: {as_.get('xg','—')} | Ъглови: {as_.get('corners',0)} | Фаули: {as_.get('fouls',0)}

СЪБИТИЯ:
{events_text if events_text else 'Няма значими събития'}

Напиши ПОЛУВРЕМЕНЕН КОМЕНТАР по тази структура:

## КАК ИЗГЛЕЖДАШЕ ПЪРВОТО ПОЛУВРЕМЕ?
(1-2 параграфа — основна тема и характер на играта)

## КОЙ ДОМИНИРАШЕ И ЗАЩО?
(1 параграф с данни от статистиката)

## КЛЮЧОВИ МОМЕНТИ
• (3-5 конкретни момента от мача)

## КАКВО ДА ОЧАКВАМЕ ВТОРО ПОЛУВРЕМЕ?
(1 параграф — тактически корекции, кой е фаворит за продължението)

## ПОЛУВРЕМЕННА ОЦЕНКА
{home}: [кратка оценка — добра/слаба/средна игра и защо]
{away}: [кратка оценка — добра/слаба/средна игра и защо]"""

        try:
            resp = _gpt_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800,
                temperature=0.7,
            )
            text = resp.choices[0].message.content.strip()
            self._ht_cache[fixture_id] = {"ts": time.time(), "text": text}
            print(f"[HALFTIME] GPT analysis generated for fixture {fixture_id}")
            return text
        except Exception as e:
            print(f"[HALFTIME] GPT error: {e}")
            return self._rule_based_halftime(home, away, live_stats, events, score_home, score_away)

    def _rule_based_halftime(
        self, home: str, away: str,
        live_stats: dict, events: list,
        score_home: int, score_away: int,
    ) -> str:
        hs  = live_stats.get("home", {})
        as_ = live_stats.get("away", {})

        score_str = f"{home} {score_home}:{score_away} {away}"
        dominant = home if hs.get("shots_on_target", 0) >= as_.get("shots_on_target", 0) else away
        lines = [
            f"## РЕЗУЛТАТ СЛЕД ПЪРВО ПОЛУВРЕМЕ",
            f"{score_str}",
            f"## СТАТИСТИКА",
            f"{home}: {hs.get('shots_total',0)} удара, {hs.get('possession',0)}% владение",
            f"{away}: {as_.get('shots_total',0)} удара, {as_.get('possession',0)}% владение",
            f"## ДОМИНИРАЩ ОТБОР",
            f"{dominant} показа по-голяма опасност в атака.",
        ]
        return "\n".join(lines)

    # --------------------------------------------------
    # COMMENTARY QUEUE  (3 live talking points, refreshed every 5 min)
    # --------------------------------------------------

    _commentary_minute: dict = {}   # fixture_id -> last generated minute
    COMMENTARY_INTERVAL = 5         # minutes between refreshes

    def generate_commentary_queue(
        self,
        fixture_id:  int,
        home:        str,
        away:        str,
        minute:      int,
        score_home:  int,
        score_away:  int,
        live_stats:  dict,
        events:      list,
        momentum:    dict,
        force:       bool = False,   # force refresh (e.g. after goal)
    ) -> list[dict]:
        """
        Generate 3 fresh talking points for the streamer every COMMENTARY_INTERVAL minutes.
        Returns list of { minute, title, text } or empty list if not yet due.
        """
        last_min = self._commentary_minute.get(fixture_id, -99)
        if not force and (minute - last_min) < self.COMMENTARY_INTERVAL:
            return []   # not time yet

        if not _gpt_available or _gpt_client is None:
            points = self._rule_based_commentary(home, away, minute, score_home, score_away, live_stats, events)
            self._commentary_minute[fixture_id] = minute
            return points

        hs  = live_stats.get("home", {})
        as_ = live_stats.get("away", {})

        # Recent events (last 10 min)
        recent_events = [e for e in events if (minute - (e.get("time", {}).get("elapsed") or 0)) <= 12]
        def fmt_event(e):
            t = e.get("time", {}).get("elapsed", "?")
            typ = e.get("type", "")
            det = e.get("detail", "")
            team = e.get("team", {}).get("name", "")
            player = e.get("player", {}).get("name", "")
            if typ == "Goal":     return f"{t}' ГОЛ {team}: {player} ({det})"
            if typ == "Card":     return f"{t}' КАРТОН {team}: {player} ({det})"
            if typ == "subst":    return f"{t}' СМЯНА {team}: влиза {player}"
            return f"{t}' {typ} {team}"

        recent_text = "\n".join(fmt_event(e) for e in recent_events[-8:]) or "Няма нови събития"

        dom_team = momentum.get("dominant_team", "neutral")
        h_mom = momentum.get("home_momentum_pct", 50)
        a_mom = momentum.get("away_momentum_pct", 50)

        prompt = f"""Ти си спортен коментатор за стрийм предаване. Пишеш САМО на Български.

МАЧ: {home} {score_home}:{score_away} {away} | МИНУТА: {minute}'

СТАТИСТИКА:
{home}: {hs.get('shots_total',0)} удара ({hs.get('shots_on_target',0)} в рамките) | xG: {hs.get('xg','—')} | Владение: {hs.get('possession',0)}%
{away}: {as_.get('shots_total',0)} удара ({as_.get('shots_on_target',0)} в рамките) | xG: {as_.get('xg','—')} | Владение: {as_.get('possession',0)}%

ИМПУЛС: {home} {h_mom}% | {away} {a_mom}% | Доминира: {dom_team}

ПОСЛЕДНИ СЪБИТИЯ:
{recent_text}

Генерирай ТОЧНО 3 говорни точки за стриймъра — неща, за които трябва да говори СЕГА.
Всяка точка трябва да е конкретна, базирана на данните, и да звучи естествено казана на глас.

Отговори САМО в този формат (без нищо друго):
1. [Кратко заглавие]: [1-2 изречения за стриймъра]
2. [Кратко заглавие]: [1-2 изречения за стриймъра]
3. [Кратко заглавие]: [1-2 изречения за стриймъра]"""

        try:
            resp = _gpt_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=0.75,
            )
            raw = resp.choices[0].message.content.strip()
            points = self._parse_commentary_points(raw, minute)
            self._commentary_minute[fixture_id] = minute
            print(f"[COMMENTARY] {len(points)} points at {minute}' for fixture {fixture_id}")
            return points
        except Exception as e:
            print(f"[COMMENTARY] GPT error: {e}")
            points = self._rule_based_commentary(home, away, minute, score_home, score_away, live_stats, events)
            self._commentary_minute[fixture_id] = minute
            return points

    @staticmethod
    def _parse_commentary_points(raw: str, minute: int) -> list[dict]:
        """Parse GPT output into structured list of { minute, title, text }."""
        import re
        points = []
        for line in raw.strip().splitlines():
            m = re.match(r"^\d+\.\s*\[?(.+?)\]?:\s*(.+)$", line.strip())
            if m:
                points.append({
                    "minute": minute,
                    "title":  m.group(1).strip(),
                    "text":   m.group(2).strip(),
                })
        # Fallback: split on numbered lines even without bracket format
        if not points:
            for line in raw.strip().splitlines():
                m = re.match(r"^\d+\.\s*(.+)$", line.strip())
                if m:
                    content = m.group(1).strip()
                    if ":" in content:
                        title, text = content.split(":", 1)
                    else:
                        title, text = "Точка", content
                    points.append({"minute": minute, "title": title.strip(), "text": text.strip()})
        return points[:3]

    def _rule_based_commentary(
        self,
        home: str, away: str,
        minute: int, score_home: int, score_away: int,
        live_stats: dict, events: list,
    ) -> list[dict]:
        """Simple rule-based fallback when GPT unavailable."""
        hs  = live_stats.get("home", {})
        as_ = live_stats.get("away", {})
        points = []

        # Point 1: Score context
        if score_home == score_away:
            points.append({
                "minute": minute, "title": "Равен мач",
                "text": f"При {score_home}:{score_away} в {minute}-та минута всичко е отворено."
            })
        elif score_home > score_away:
            points.append({
                "minute": minute, "title": f"{home} водят",
                "text": f"{home} са напред с {score_home}:{score_away}. Въпросът е дали ще задържат преднината."
            })
        else:
            points.append({
                "minute": minute, "title": f"{away} водят",
                "text": f"{away} са напред с {score_home}:{score_away}. {home} трябва да реагират."
            })

        # Point 2: xG context
        hxg = float(hs.get("xg") or 0)
        axg = float(as_.get("xg") or 0)
        if hxg or axg:
            points.append({
                "minute": minute, "title": "xG статистика",
                "text": f"{home} xG: {hxg:.2f} | {away} xG: {axg:.2f}. {'Домакините създават повече опасност.' if hxg > axg else 'Гостите имат по-добра xG статистика.'}"
            })

        # Point 3: Possession
        hp = hs.get("possession", 0)
        if hp:
            dominant = home if hp > 50 else away
            points.append({
                "minute": minute, "title": "Владение",
                "text": f"{home} {hp}% — {away} {100-hp}%. {dominant} доминира с топката."
            })

        return points[:3]

    # --------------------------------------------------
    # POST-MATCH SUMMARY
    # --------------------------------------------------

    _ft_cache: dict = {}  # fixture_id -> { ts, text }

    def generate_postmatch_summary(
        self,
        fixture_id:  int,
        home:        str,
        away:        str,
        score_home:  int,
        score_away:  int,
        live_stats:  dict,
        events:      list,
        prematch_data: dict = None,
    ) -> str:
        """
        Generate a comprehensive full-time broadcast summary.
        Triggered when match status changes to FT.
        Cached permanently for the fixture (FT is final).
        """
        if fixture_id in self._ft_cache:
            return self._ft_cache[fixture_id]

        if not _gpt_available or _gpt_client is None:
            return self._rule_based_postmatch(home, away, live_stats, events, score_home, score_away)

        hs  = live_stats.get("home", {})
        as_ = live_stats.get("away", {})

        # Goals by team
        goals_home = [e for e in events if e.get("type") == "Goal" and e.get("team", {}).get("name") == home]
        goals_away = [e for e in events if e.get("type") == "Goal" and e.get("team", {}).get("name") == away]
        cards_home = [e for e in events if e.get("type") == "Card" and e.get("team", {}).get("name") == home]
        cards_away = [e for e in events if e.get("type") == "Card" and e.get("team", {}).get("name") == away]
        subs_home  = [e for e in events if e.get("type") == "subst" and e.get("team", {}).get("name") == home]
        subs_away  = [e for e in events if e.get("type") == "subst" and e.get("team", {}).get("name") == away]

        def fmt_goals(glist):
            return ", ".join(f"{g['player']['name']} {g['time']['elapsed']}'" for g in glist) or "—"

        def fmt_cards(clist):
            return ", ".join(f"{c['player']['name']} ({c['detail']}) {c['time']['elapsed']}'" for c in clist) or "—"

        # Pre-match prediction context
        pred_ctx = ""
        if prematch_data:
            pred = prematch_data.get("prediction", {})
            if pred.get("likely_score"):
                pred_ctx = f"\nПРЕДМАЧОВА ПРОГНОЗА: {pred['likely_score']}"

        prompt = f"""Ти си водещ спортен анализатор за стрийм предаване. Пишеш САМО на Български. Стилът е ТВ-готов, конкретен и ангажиращ.

МАЧ ПРИКЛЮЧИ: {home} {score_home}:{score_away} {away}
{pred_ctx}

ФИНАЛНА СТАТИСТИКА:
{home}: {hs.get('shots_total',0)} удара ({hs.get('shots_on_target',0)} в рамките) | xG: {hs.get('xg','—')} | Владение: {hs.get('possession',0)}% | Ъглови: {hs.get('corners',0)} | Пасове: {hs.get('passes_accurate','—')}/{hs.get('passes_total','—')}
{away}: {as_.get('shots_total',0)} удара ({as_.get('shots_on_target',0)} в рамките) | xG: {as_.get('xg','—')} | Владение: {as_.get('possession',0)}% | Ъглови: {as_.get('corners',0)} | Пасове: {as_.get('passes_accurate','—')}/{as_.get('passes_total','—')}

ГОЛОВЕ:
{home}: {fmt_goals(goals_home)}
{away}: {fmt_goals(goals_away)}

КАРТОНИ:
{home}: {fmt_cards(cards_home)}
{away}: {fmt_cards(cards_away)}

СМЕНИ: {home}: {len(subs_home)} | {away}: {len(subs_away)}

Напиши ФИНАЛЕН АНАЛИЗ точно по тази структура:

## КАК ЗАВЪРШИ МАЧЪТ?
(1-2 параграфа — обективен поглед на мача като цяло)

## КОЙ ЗАСЛУЖИ ПОБЕДАТА И ЗАЩО?
(1 параграф с данни от статистиката)

## КЛЮЧОВИЯТ МОМЕНТ НА МАЧА
(1 момент — гол, картон, смяна или тактическа промяна, която обърна мача)

## ИГРАЧ НА МАЧА
Избор: [Играч, отбор]
Защо: (1 изречение)

## ОЦЕНКИ
{home}: [X]/10 — [кратка мотивация]
{away}: [X]/10 — [кратка мотивация]

## ЗАКЛЮЧЕНИЕ ЗА СТРИЙМА
(1 параграф — финална дума към зрителите, какво остава като впечатление)"""

        try:
            resp = _gpt_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=900,
                temperature=0.7,
            )
            text = resp.choices[0].message.content.strip()
            self._ft_cache[fixture_id] = text
            print(f"[POSTMATCH] GPT summary generated for {home} {score_home}:{score_away} {away}")
            return text
        except Exception as e:
            print(f"[POSTMATCH] GPT error: {e}")
            return self._rule_based_postmatch(home, away, live_stats, events, score_home, score_away)

    def _rule_based_postmatch(
        self, home: str, away: str,
        live_stats: dict, events: list,
        score_home: int, score_away: int,
    ) -> str:
        hs  = live_stats.get("home", {})
        as_ = live_stats.get("away", {})
        if   score_home > score_away: winner = home
        elif score_away > score_home: winner = away
        else:                         winner = None

        lines = [
            f"## ФИНАЛЕН РЕЗУЛТАТ",
            f"{home} {score_home}:{score_away} {away}",
            f"## СТАТИСТИКА",
            f"{home}: {hs.get('shots_total',0)} удара, xG {hs.get('xg','—')}, {hs.get('possession',0)}% владение",
            f"{away}: {as_.get('shots_total',0)} удара, xG {as_.get('xg','—')}, {as_.get('possession',0)}% владение",
        ]
        if winner:
            lines.append(f"## ПОБЕДИТЕЛ\n{winner} заслужено спечели мача по-добрата ефективност пред вратата.")
        else:
            lines.append("## РЕЗУЛТАТ\nРавен резултат след балансиран мач.")
        return "\n".join(lines)

    # --------------------------------------------------
    # MAIN PIPELINE
    # --------------------------------------------------

    def analyze_fast(self, fixture_id: int, match: dict) -> dict:
        """
        Phase 1: rule-based analysis only (no GPT). Fast — ~2-3 seconds.
        Broadcasts immediately so user sees data quickly.
        """
        cached = self._cache.get(fixture_id)
        if cached and time.time() - cached["ts"] < CACHE_TTL:
            return cached["data"]

        print(f"[PREMATCH] Analyzing fixture {fixture_id}...")

        meta = self._get_fixture_meta(fixture_id)
        if not meta:
            return {"available": False}

        home_id    = meta["home_id"]
        away_id    = meta["away_id"]
        league_id  = meta["league_id"]
        season     = meta["season"]

        # Parallel API calls for faster loading
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = {}
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(self._get_h2h, home_id, away_id): "h2h",
                executor.submit(self._get_team_stats, home_id, league_id, season): "home_stats",
                executor.submit(self._get_team_stats, away_id, league_id, season): "away_stats",
                executor.submit(self._get_standings, league_id, season, home_id, away_id): "standings",
                executor.submit(self._get_top_scorers, league_id, season, home_id, away_id): "top_scorers",
                executor.submit(self._get_coaches, home_id, away_id): "coaches",
                executor.submit(self._get_referee_data, fixture_id): "referee",
                executor.submit(self._get_injuries, fixture_id, home_id, away_id): "injuries",
            }
            for future in as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                except Exception:
                    results[key] = {} if key not in ("h2h",) else []

        h2h         = results["h2h"]
        home_stats  = results["home_stats"]
        away_stats  = results["away_stats"]
        standings   = results["standings"]
        top_scorers = results["top_scorers"]
        coaches     = results["coaches"]
        referee     = results["referee"]
        injuries    = results["injuries"]

        data = self._build_rule_based(
            meta, h2h, home_stats, away_stats, standings,
            top_scorers=top_scorers, coaches=coaches,
            referee=referee, injuries=injuries,
        )

        # No GPT here — that's Phase 2
        result = {"available": True, "data": data, "meta": meta}
        self._cache[fixture_id] = {"ts": time.time(), "data": result}

        has_scorers = bool(top_scorers.get("home") or top_scorers.get("away"))
        print(f"[PREMATCH] Fast done — {meta['home_name']} vs {meta['away_name']} | Scorers={has_scorers} | Coach={bool(coaches)}")
        return result

    def generate_gpt_phase(self, fixture_id: int) -> dict | None:
        """
        Phase 2: generate GPT narrative and update cache.
        Returns updated result dict, or None if GPT unavailable/skipped.
        """
        if not _gpt_available:
            return None

        cached = self._cache.get(fixture_id)
        if not cached:
            return None

        result = cached["data"]
        data   = result.get("data", {})

        # Skip if narrative already exists
        if data.get("gpt_narrative"):
            return None

        narrative = self._generate_gpt_editorial(fixture_id, data)
        if not narrative:
            return None

        data["gpt_narrative"] = narrative
        result["data"] = data
        self._cache[fixture_id] = {"ts": time.time(), "data": result}
        return result

    def analyze(self, fixture_id: int, match: dict) -> dict:
        """
        Full pre-match analysis pipeline (legacy — used by loop).
        Returns structured editorial data (rule-based + optional GPT).
        Cached per fixture for 5 minutes.
        """
        cached = self._cache.get(fixture_id)
        if cached and time.time() - cached["ts"] < CACHE_TTL:
            return cached["data"]

        print(f"[PREMATCH] Analyzing fixture {fixture_id}...")

        meta = self._get_fixture_meta(fixture_id)
        if not meta:
            return {"available": False}

        home_id    = meta["home_id"]
        away_id    = meta["away_id"]
        league_id  = meta["league_id"]
        season     = meta["season"]

        # Parallel API calls for faster loading
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = {}
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(self._get_h2h, home_id, away_id): "h2h",
                executor.submit(self._get_team_stats, home_id, league_id, season): "home_stats",
                executor.submit(self._get_team_stats, away_id, league_id, season): "away_stats",
                executor.submit(self._get_standings, league_id, season, home_id, away_id): "standings",
                executor.submit(self._get_top_scorers, league_id, season, home_id, away_id): "top_scorers",
                executor.submit(self._get_coaches, home_id, away_id): "coaches",
                executor.submit(self._get_referee_data, fixture_id): "referee",
                executor.submit(self._get_injuries, fixture_id, home_id, away_id): "injuries",
            }
            for future in as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                except Exception:
                    results[key] = {} if key not in ("h2h",) else []

        h2h         = results["h2h"]
        home_stats  = results["home_stats"]
        away_stats  = results["away_stats"]
        standings   = results["standings"]
        top_scorers = results["top_scorers"]
        coaches     = results["coaches"]
        referee     = results["referee"]
        injuries    = results["injuries"]

        data = self._build_rule_based(
            meta, h2h, home_stats, away_stats, standings,
            top_scorers=top_scorers, coaches=coaches,
            referee=referee, injuries=injuries,
        )

        # GPT broadcast guide (if key available)
        if _gpt_available:
            data["gpt_narrative"] = self._generate_gpt_editorial(fixture_id, data)

        result = {"available": True, "data": data, "meta": meta}
        self._cache[fixture_id] = {"ts": time.time(), "data": result}

        has_scorers = bool(top_scorers.get("home") or top_scorers.get("away"))
        print(f"[PREMATCH] Done — {meta['home_name']} vs {meta['away_name']} | GPT={'yes' if _gpt_available else 'no'} | Scorers={has_scorers} | Coach={bool(coaches)}")
        return result
