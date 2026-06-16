import os
import re
import hashlib
import json
import time
import requests
from datetime import datetime, timezone
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

CACHE_TTL     = 120   # 2 мин за rule-based pre-match данни
CACHE_TTL_GPT = 3600  # 1 час когато GPT guide вече е готов
GPT_NARRATIVE_TTL = 6 * 3600  # 6 часа — цял broadcast ден без повторно генериране


# --------------------------------------------------
# Event helpers — accept normalized (overlay) or raw API shape
# --------------------------------------------------

def _evt_minute(e: dict) -> int:
    if "minute" in e:
        return int(e.get("minute") or 0)
    return int(e.get("time", {}).get("elapsed") or 0)


def _evt_team(e: dict) -> str:
    team = e.get("team")
    if isinstance(team, dict):
        return team.get("name", "") or ""
    return str(team or "")


def _evt_player(e: dict) -> str:
    player = e.get("player")
    if isinstance(player, dict):
        return player.get("name", "") or ""
    return str(player or "")


def _evt_detail(e: dict) -> str:
    return e.get("detail", "") or ""


def _evt_assist(e: dict) -> str:
    assist = e.get("assist")
    if isinstance(assist, dict):
        return assist.get("name", "?") or "?"
    return "?"


def _format_events_block(events: list) -> str:
    """Bulgarian event summary for GPT prompts."""
    goals = [e for e in events if e.get("type") == "Goal"]
    cards = [e for e in events if e.get("type") == "Card"]
    subs  = [e for e in events if e.get("type") == "subst"]

    parts = []
    if goals:
        parts.append(
            "ГОЛОВЕ:\n"
            + "\n".join(
                f"  {_evt_minute(e)}' — {_evt_team(e)}: {_evt_player(e)}"
                for e in goals
            )
        )
    if cards:
        parts.append(
            "КАРТОНИ:\n"
            + "\n".join(
                f"  {_evt_minute(e)}' — {_evt_team(e)}: {_evt_player(e)} ({_evt_detail(e)})"
                for e in cards
            )
        )
    if subs:
        parts.append(
            "СМЕНИ:\n"
            + "\n".join(
                f"  {_evt_minute(e)}' — {_evt_team(e)}: {_evt_assist(e)} → {_evt_player(e)}"
                for e in subs
            )
        )
    return "\n".join(parts)


def _format_event_line(e: dict) -> str:
    t = _evt_minute(e)
    typ = e.get("type", "")
    det = _evt_detail(e)
    team = _evt_team(e)
    player = _evt_player(e)
    if typ == "Goal":
        return f"{t}' ГОЛ {team}: {player} ({det})"
    if typ == "Card":
        return f"{t}' КАРТОН {team}: {player} ({det})"
    if typ == "subst":
        return f"{t}' СМЯНА {team}: влиза {player}"
    return f"{t}' {typ} {team}"
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
        self._gpt_narrative_cache: dict = {}  # fixture_id → {ts, text}
        self._stream_facts_cache: dict = {}    # fixture_id → {ts, facts: list[str]}
        self._events_cache: dict = {}   # fixture_id → events list
        self._fixture_players_cache: dict = {}  # fixture_id → fixtures/players response

    def _cache_ttl_for(self, result: dict) -> int:
        data = result.get("data", {}) if isinstance(result.get("data"), dict) else {}
        if data.get("gpt_narrative"):
            return CACHE_TTL_GPT
        return CACHE_TTL

    def _get_cached_narrative(self, fixture_id: int) -> str:
        cached = self._gpt_narrative_cache.get(fixture_id)
        if cached and time.time() - cached["ts"] < GPT_NARRATIVE_TTL:
            return cached.get("text", "") or ""
        return ""

    def _store_narrative_cache(self, fixture_id: int, text: str) -> None:
        if text:
            self._gpt_narrative_cache[fixture_id] = {"ts": time.time(), "text": text}

    def _apply_cached_narrative(self, fixture_id: int, data: dict) -> None:
        if data.get("gpt_narrative"):
            return
        cached = self._get_cached_narrative(fixture_id)
        if cached:
            data["gpt_narrative"] = cached
            data["broadcast_guide_draft"] = ""
            data["gpt_guide_pending"] = False

    def _get_cached_stream_facts(self, fixture_id: int) -> list[str]:
        cached = self._stream_facts_cache.get(fixture_id)
        if cached and time.time() - cached["ts"] < GPT_NARRATIVE_TTL:
            return cached.get("facts") or []
        return []

    def _store_stream_facts_cache(self, fixture_id: int, facts: list[str]) -> None:
        if facts:
            self._stream_facts_cache[fixture_id] = {"ts": time.time(), "facts": facts}

    def _apply_cached_stream_facts(self, fixture_id: int, data: dict) -> None:
        cached = self._get_cached_stream_facts(fixture_id)
        if cached:
            data["stream_facts"] = cached
            data["stream_facts_gpt_pending"] = False

    @staticmethod
    def _form_streak(form_list: list, char: str) -> int:
        n = 0
        for r in form_list or []:
            if r == char:
                n += 1
            else:
                break
        return n

    @staticmethod
    def _unbeaten_streak(form_list: list) -> int:
        n = 0
        for r in form_list or []:
            if r in ("W", "D"):
                n += 1
            else:
                break
        return n

    def _build_stream_facts(self, data: dict) -> list[str]:
        """Rule-based stream trivia — source of truth for GPT polish."""
        facts: list[str] = []
        home   = data.get("home", "")
        away   = data.get("away", "")
        hs     = data.get("home_stats") or {}
        as_    = data.get("away_stats") or {}
        hst    = data.get("home_standing") or {}
        ast    = data.get("away_standing") or {}
        pred   = data.get("prediction") or {}
        coaches = data.get("coaches") or {}
        referee = data.get("referee") or {}
        injuries = data.get("injuries") or {}
        h2h_raw = data.get("h2h_raw") or []

        hw = data.get("h2h_home_wins", 0)
        aw = data.get("h2h_away_wins", 0)
        h2h_count = data.get("h2h_count", 0)
        avg_goals = data.get("avg_h2h_goals", 0)

        if h2h_count >= 2:
            if hw > aw and hw >= 2:
                facts.append(
                    f"{home} доминира в директните срещи — "
                    f"{hw} победи от последните {h2h_count} мача срещу {away}"
                )
            elif aw > hw and aw >= 2:
                facts.append(
                    f"{away} доминира в директните срещи — "
                    f"{aw} победи от последните {h2h_count} мача срещу {home}"
                )
            if avg_goals >= 3.5:
                facts.append(f"Директните срещи са голови — средно {avg_goals} гола на мач")
            elif avg_goals <= 1.5:
                facts.append(f"H2H мачовете са тактически — средно само {avg_goals} гола на мач")

        if data.get("h2h_latest_scorers"):
            facts.append(data["h2h_latest_scorers"])

        if h2h_raw and h2h_count == 1:
            latest = h2h_raw[0]
            comp = latest.get("competition", "")
            comp_bit = f" ({comp})" if comp else ""
            facts.append(
                f"Единствената директна среща ({latest.get('date', '—')}): "
                f"{latest.get('home')} {latest.get('home_goals')}:{latest.get('away_goals')} "
                f"{latest.get('away')}{comp_bit}"
            )

        for team, stats in ((home, hs), (away, as_)):
            form = stats.get("form") or []
            w_streak = self._form_streak(form, "W")
            l_streak = self._form_streak(form, "L")
            ub_streak = self._unbeaten_streak(form)
            if w_streak >= 3:
                facts.append(f"{team} спечели последните {w_streak} мача подред")
            if l_streak >= 3:
                facts.append(
                    f"{team} не е печелил последните {l_streak} мача "
                    f"({stats.get('form_str', '')})"
                )
            if ub_streak >= 4:
                facts.append(f"{team} е непобедим в последните {ub_streak} мача")

            avg = float(stats.get("avg_score") or 0)
            if avg >= 2.0 and (stats.get("played") or 0) >= 3:
                facts.append(f"{team} вкарва средно {avg:.1f} гола на мач в последните мачове")

            avg_c = float(stats.get("avg_conc") or 0)
            if avg_c <= 0.8 and (stats.get("played") or 0) >= 3:
                facts.append(f"{team} допуска само {avg_c:.1f} гола на мач — стабилна отбрана")

        btts = pred.get("btts_pct")
        over25 = pred.get("over25_pct")
        if btts and btts >= 60:
            facts.append(f"Статистически модел: {btts}% шанс и двата отбора да вкарят")
        if over25 and over25 >= 65:
            facts.append(f"Моделът дава {over25}% за над 2.5 гола — потенциално открит мач")
        elif over25 and over25 <= 35:
            facts.append(f"Само {over25}% за над 2.5 гола по модела — вероятно затегната среща")

        ch, ca = coaches.get("home"), coaches.get("away")
        if ch and ca:
            facts.append(f"Треньорски дуел: {ch} ({home}) срещу {ca} ({away})")

        if referee.get("name"):
            if referee.get("cards_profile"):
                facts.append(f"Съдия {referee['name']} — {referee['cards_profile']}")
            elif referee.get("strictness"):
                facts.append(f"Съдия {referee['name']} — профил: {referee['strictness']}")

        for side_key, team in (("home", home), ("away", away)):
            players = (data.get("top_scorers") or {}).get(side_key) or []
            if players:
                p = players[0]
                label = p.get("label") or ""
                if p.get("goals") or label:
                    detail = label or f"{p.get('goals', 0)} гола"
                    facts.append(f"Звездата на {team}: {p.get('name', '?')} — {detail}")

        gs = data.get("group_scenarios") or {}
        for bullet in (gs.get("bullets") or [])[:2]:
            facts.append(bullet)

        inj_h = injuries.get("home") or []
        inj_a = injuries.get("away") or []
        if len(inj_h) >= 2:
            names = ", ".join(i.get("name", "?") for i in inj_h[:3])
            suffix = "..." if len(inj_h) > 3 else ""
            facts.append(f"{home} с важни липси: {names}{suffix}")
        if len(inj_a) >= 2:
            names = ", ".join(i.get("name", "?") for i in inj_a[:3])
            suffix = "..." if len(inj_a) > 3 else ""
            facts.append(f"{away} с важни липси: {names}{suffix}")

        if data.get("standings_reliable"):
            hp, ap = hst.get("points"), ast.get("points")
            if hp and hp == ap:
                facts.append(
                    f"И двата отбора са с {hp} точки — директен дуел за позицията в групата"
                )
            elif hst.get("position") == 1 and (hst.get("played") or 0) > 0:
                facts.append(f"{home} е лидер в групата с {hst.get('points', 0)} точки")
            elif ast.get("position") == 1 and (ast.get("played") or 0) > 0:
                facts.append(f"{away} е лидер в групата с {ast.get('points', 0)} точки")

        seen: set[str] = set()
        unique: list[str] = []
        for f in facts:
            f = f.strip()
            if f and f not in seen:
                seen.add(f)
                unique.append(f)
        return unique[:10]

    def _parse_stream_facts_gpt(self, raw: str, fallback: list[str]) -> list[str]:
        bullets: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^[•\-\*]\s*(.+)$", line) or re.match(r"^\d+\.\s*(.+)$", line)
            if m:
                bullets.append(m.group(1).strip())
        return bullets[:8] if bullets else fallback[:8]

    def _generate_stream_facts_gpt(self, fixture_id: int, data: dict) -> list[str]:
        """GPT polish of rule-based facts — no new data allowed."""
        if not _gpt_available or _gpt_client is None:
            return []

        raw_facts = data.get("stream_facts") or self._build_stream_facts(data)
        if not raw_facts:
            return []

        cached = self._get_cached_stream_facts(fixture_id)
        if cached:
            return cached

        home = data.get("home", "")
        away = data.get("away", "")
        facts_text = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(raw_facts))
        target = min(len(raw_facts), 8)

        prompt = f"""Ти си спортен коментатор за live стрийм. Пишеш САМО на Български.

МАЧ: {home} срещу {away}

Ето ФАКТИ (използвай САМО тях — без нови числа, имена или резултати):
{facts_text}

Преформулирай ги в {target} кратки bullets за казване на глас в ефир (15–30 сек всеки).
Стил: жив, интересен, но 100% фактически — без измисляне.

Отговори САМО с bullets, по един на ред, започващи с „• ":
• [текст]
• [текст]"""

        try:
            resp = _gpt_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=450,
                temperature=0.65,
            )
            raw = resp.choices[0].message.content.strip()
            polished = self._parse_stream_facts_gpt(raw, raw_facts)
            self._store_stream_facts_cache(fixture_id, polished)
            print(f"[STREAM-FACTS] GPT polished {len(polished)} facts for fixture {fixture_id}")
            return polished
        except Exception as e:
            print(f"[STREAM-FACTS] GPT error: {e}")
            return []

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
                "fixture_id": f["fixture"]["id"],
                "date":       f["fixture"]["date"][:10],
                "season":     f["league"]["season"],
                "home":       f["teams"]["home"]["name"],
                "away":       f["teams"]["away"]["name"],
                "home_goals": f["goals"].get("home", 0),
                "away_goals": f["goals"].get("away", 0),
                "competition": f["league"]["name"],
            })
        return results

    def _fixture_season(self, fixture_id: int) -> int | None:
        raw = self._get("fixtures", {"id": fixture_id})
        if not raw:
            return None
        return (raw[0].get("league") or {}).get("season")

    def _h2h_goal_scorers_line(self, fixture_id: int, season: int | None = None) -> str:
        """Format goal scorers from a finished H2H fixture, e.g. 'Raúl Jiménez (23'), Oswin Appollis (67')'."""
        if season is None:
            season = self._fixture_season(fixture_id)
        goals: list[dict] = []
        for ev in self._get_fixture_events_cached(fixture_id):
            if ev.get("type") != "Goal" or ev.get("detail") == "Own Goal":
                continue
            t = ev.get("time") or {}
            minute = t.get("elapsed")
            if minute is None:
                minute = t.get("extra") or 0
            player = ev.get("player") or {}
            pid    = player.get("id")
            pname  = player.get("name", "") or "?"
            names  = {pname}
            display = self._resolve_key_player_display_name(pid, names, season, pname)
            goals.append({"minute": int(minute or 0), "player": display})

        if not goals:
            return ""

        goals.sort(key=lambda g: g["minute"])
        parts = [f"{g['player']} ({g['minute']}')" for g in goals]
        return "Голове: " + ", ".join(parts)

    @staticmethod
    def _is_friendly_fixture(fixture: dict) -> bool:
        league = fixture.get("league") or {}
        name = (league.get("name") or "").lower()
        typ  = (league.get("type") or "").lower()
        return typ == "friendly" or "friend" in name

    def _aggregate_fixtures(self, fixtures: list, team_id: int) -> dict:
        wins = draws = losses = scored = conceded = 0
        form: list[str] = []
        comp_names: list[str] = []

        for f in fixtures:
            home_id    = f["teams"]["home"]["id"]
            home_goals = f["goals"].get("home") or 0
            away_goals = f["goals"].get("away") or 0
            comp_name  = (f.get("league") or {}).get("name", "") or ""
            if comp_name and comp_name not in comp_names:
                comp_names.append(comp_name)

            if home_id == team_id:
                s_goals, c_goals = home_goals, away_goals
            else:
                s_goals, c_goals = away_goals, home_goals

            scored   += s_goals
            conceded += c_goals

            if s_goals > c_goals:
                wins += 1
                form.append("W")
            elif s_goals == c_goals:
                draws += 1
                form.append("D")
            else:
                losses += 1
                form.append("L")

        played    = wins + draws + losses
        avg_score = round(scored / played, 2) if played else None
        avg_conc  = round(conceded / played, 2) if played else None

        return {
            "played":         played,
            "wins":           wins,
            "draws":          draws,
            "losses":         losses,
            "scored":         scored,
            "conceded":       conceded,
            "avg_score":      avg_score,
            "avg_conc":       avg_conc,
            "form":           form,
            "form_str":       "".join(form) if form else "—",
            "clean_sheets":   0,
            "failed_to_score": 0,
            "competitions":   comp_names,
        }

    def _get_team_stats(self, team_id: int, league_id: int, season: int) -> dict:
        """
        Form from recent finished matches — official competitions first, friendlies as fill.
        Falls back to league season stats for domestic leagues.
        """
        raw = self._get("fixtures", {"team": team_id, "last": 30, "status": "FT"})

        if raw:
            official = [f for f in raw if not self._is_friendly_fixture(f)]
            friendly = [f for f in raw if self._is_friendly_fixture(f)]

            if len(official) >= 3:
                pool = official[:10]
                pool_mix = {"official": len(pool), "friendly": 0}
            else:
                pool = (official + friendly)[:10]
                pool_mix = {
                    "official": sum(1 for f in pool if not self._is_friendly_fixture(f)),
                    "friendly": sum(1 for f in pool if self._is_friendly_fixture(f)),
                }

            stats = self._aggregate_fixtures(pool, team_id)
            stats["source"]    = "recent_fixtures"
            stats["pool_mix"]  = pool_mix
            return stats

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
            "source":            "league_season",
            "competitions":      [],
        }

    @staticmethod
    def _friendly_comp_label(name: str) -> str:
        low = (name or "").lower()
        if "friend" in low:
            return "приятелски срещи"
        return name

    def _stats_source_label(self, stats: dict, league_name: str = "") -> str:
        """Human-readable scope for form/goal averages shown in UI."""
        if stats.get("source") == "league_season":
            ln = league_name or "лигата"
            return f"статистика за текущия сезон в {ln}"

        played = stats.get("played") or 0
        comps  = stats.get("competitions") or []
        mix    = stats.get("pool_mix") or {}
        off    = mix.get("official", 0)
        fr     = mix.get("friendly", 0)

        if not played:
            return "няма скорошна форма"

        comp_summary = ""
        if comps:
            labels = [self._friendly_comp_label(c) for c in comps[:3]]
            if len(comps) > 3:
                comp_summary = f" ({', '.join(labels)} + още {len(comps) - 3})"
            elif len(labels) == 1:
                comp_summary = f" ({labels[0]})"
            else:
                comp_summary = f" ({', '.join(labels)})"

        if off and not fr:
            word = "мач" if off == 1 else "мача"
            return f"последните {off} официални {word}{comp_summary}"
        if off and fr:
            return f"последните {played} мача ({off} официални + {fr} приятелски){comp_summary}"
        if fr and not off:
            return f"последните {played} мача (приятелски срещи)"

        return f"последните {played} приключили мача{comp_summary}"

    @staticmethod
    def _comparison_key_factors(
        home: str,
        away: str,
        hs: dict,
        as_: dict,
    ) -> list[str]:
        factors: list[str] = []
        h_avg_s = float(hs.get("avg_score") or 0)
        a_avg_s = float(as_.get("avg_score") or 0)
        h_avg_c = float(hs.get("avg_conc") or 0)
        a_avg_c = float(as_.get("avg_conc") or 0)
        scope   = hs.get("source_label") or "последните мачове"

        if h_avg_s > 0 and a_avg_s > 0 and h_avg_s != a_avg_s:
            if h_avg_s > a_avg_s:
                pct = round((h_avg_s - a_avg_s) / a_avg_s * 100)
                factors.append(
                    f"{home} отбелязва с {pct}% повече голове средно "
                    f"({h_avg_s:.2f} срещу {a_avg_s:.2f} — {scope})"
                )
            else:
                pct = round((a_avg_s - h_avg_s) / h_avg_s * 100)
                factors.append(
                    f"{away} отбелязва с {pct}% повече голове средно "
                    f"({a_avg_s:.2f} срещу {h_avg_s:.2f} — {scope})"
                )

        if h_avg_c > 0 and a_avg_c > 0 and h_avg_c != a_avg_c:
            if h_avg_c < a_avg_c:
                pct = round((1 - h_avg_c / a_avg_c) * 100)
                factors.append(
                    f"{home} допуска {pct}% по-малко голове средно "
                    f"({h_avg_c:.2f} срещу {a_avg_c:.2f} — {scope})"
                )
            else:
                pct = round((1 - a_avg_c / h_avg_c) * 100)
                factors.append(
                    f"{away} допуска {pct}% по-малко голове средно "
                    f"({a_avg_c:.2f} срещу {h_avg_c:.2f} — {scope})"
                )

        return factors

    @staticmethod
    def _h2h_key_factor(h2h: list, h2h_count: int, avg_h2h_goals: float) -> str:
        if h2h_count <= 0:
            return ""
        latest = h2h[0]
        hg = latest.get("home_goals", 0) or 0
        ag = latest.get("away_goals", 0) or 0
        result = f"{latest.get('home', '?')} {hg}:{ag} {latest.get('away', '?')}"
        date = latest.get("date", "")
        comp = latest.get("competition", "")
        comp_bit = f", {comp}" if comp else ""

        if h2h_count == 1:
            return f"1 директна среща ({date}): {result}{comp_bit}"
        return (
            f"{h2h_count} директни срещи, средно {avg_h2h_goals} гола; "
            f"последна ({date}): {result}{comp_bit}"
        )

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

            entry = {
                "name":       player.get("name", ""),
                "firstname":  player.get("firstname"),
                "lastname":   player.get("lastname"),
                "player_id":  player.get("id"),
                "goals":      goals,
                "assists":    assists,
                "apps":       apps,
            }
            if team_id == home_id:
                result["home"].append(entry)
            elif team_id == away_id:
                result["away"].append(entry)

        return result

    @staticmethod
    def _should_use_league_topscorers(league_name: str) -> bool:
        """League topscorer table is meaningful for domestic leagues only."""
        n = (league_name or "").lower()
        skip = (
            "friend", "world cup", "fifa", "euro championship", "euro ",
            "copa america", "copa afr", "africa cup", "nations league",
            "qualification", "qualifying", "international",
        )
        return not any(k in n for k in skip)

    @staticmethod
    def _is_countable_goal_event(ev: dict) -> bool:
        if ev.get("type") != "Goal":
            return False
        detail = (ev.get("detail") or "").lower()
        skip = (
            "own goal", "missed penalty", "penalty missed",
            "penalty shootout", "cancelled", "disallowed", "var cancelled",
        )
        return not any(s in detail for s in skip)

    def _get_fixture_players_cached(self, fixture_id: int) -> list:
        if fixture_id in self._fixture_players_cache:
            return self._fixture_players_cache[fixture_id]
        data = self._get("fixtures/players", {"fixture": fixture_id}) or []
        self._fixture_players_cache[fixture_id] = data
        return data

    def _tally_team_from_fixture_players(
        self,
        fixture_id: int,
        team_id: int,
        tallies: dict[str, dict],
    ) -> bool:
        """Per-match player stats (preferred over parsing raw events)."""
        found = False
        for block in self._get_fixture_players_cached(fixture_id):
            if (block.get("team") or {}).get("id") != team_id:
                continue
            for entry in block.get("players") or []:
                player = entry.get("player") or {}
                stats_list = entry.get("statistics") or []
                if not stats_list:
                    continue
                st = stats_list[0]
                pid = player.get("id")
                pname = player.get("name") or ""
                games = st.get("games") or {}
                minutes = games.get("minutes") or 0
                goals_block = st.get("goals") or {}
                goals = int(goals_block.get("total") or 0)
                assists = int(goals_block.get("assists") or 0)
                if not minutes and goals == 0 and assists == 0:
                    continue
                if not pname and not pid:
                    continue
                key = self._touch_player_tally(tallies, pid, pname)
                tallies[key]["goals"] += goals
                tallies[key]["assists"] += assists
                if minutes:
                    tallies[key]["apps"] += 1
                found = True
            break
        return found

    def _tally_team_from_fixture_events(
        self,
        fixture_id: int,
        team_id: int,
        tallies: dict[str, dict],
    ) -> None:
        """Fallback: parse goal events when fixtures/players is unavailable."""
        seen_goals: set[tuple] = set()
        appeared: set[str] = set()

        for ev in self._get_fixture_events_cached(fixture_id):
            ev_team = (ev.get("team") or {}).get("id")
            if ev_team != team_id:
                continue

            player = ev.get("player") or {}
            assist = ev.get("assist") or {}
            player_id   = player.get("id")
            assist_id   = assist.get("id")
            player_name = player.get("name", "") or ""
            assist_name = assist.get("name", "") or ""
            minute = (ev.get("time") or {}).get("elapsed")

            if player_name or player_id:
                appeared.add(self._player_tally_key(player_id, player_name))
            if assist_name or assist_id:
                appeared.add(self._player_tally_key(assist_id, assist_name))

            if self._is_countable_goal_event(ev) and (player_name or player_id):
                dedupe = (fixture_id, minute, player_id or player_name, ev.get("detail"))
                if dedupe in seen_goals:
                    continue
                seen_goals.add(dedupe)
                key = self._touch_player_tally(tallies, player_id, player_name)
                tallies[key]["goals"] += 1
                if assist_name or assist_id:
                    akey = self._touch_player_tally(tallies, assist_id, assist_name)
                    tallies[akey]["assists"] += 1

        for key in appeared:
            tallies.setdefault(key, {
                "goals": 0, "assists": 0, "apps": 0,
                "player_id": None, "names": set(),
            })
            tallies[key]["apps"] += 1

    def _get_fixture_events_cached(self, fixture_id: int) -> list:
        if fixture_id in self._events_cache:
            return self._events_cache[fixture_id]
        data = self._get("fixtures/events", {"fixture": fixture_id}) or []
        self._events_cache[fixture_id] = data
        return data

    @staticmethod
    def _is_abbreviated_player_name(name: str) -> bool:
        return bool(re.match(r"^[\w]\.\s", (name or "").strip()))

    @staticmethod
    def _player_display_name(first: str, last: str, short: str = "") -> str:
        first = ((first or "").strip().split() or [""])[0]
        last  = (last or "").strip()
        short = (short or "").strip()
        if not first:
            if short and not PreMatchEngine._is_abbreviated_player_name(short):
                return short
            return short or "—"

        parts = [p for p in last.split() if p]
        if not parts:
            return first

        family = ""
        if short and "." in short:
            suffix = short.split(".", 1)[-1].strip()
            for part in reversed(parts):
                if part.casefold() == suffix.casefold():
                    family = part
                    break

        if not family:
            if len(parts) == 1:
                family = parts[0]
            elif len(parts[0]) >= 6:
                family = parts[0]
            else:
                family = parts[-1]

        return f"{first} {family}"

    def _resolve_key_player_display_name(
        self,
        player_id: int | None,
        names: set[str] | list[str],
        season: int | None,
        fallback: str = "",
    ) -> str:
        seen = {n.strip() for n in (names or []) if n and n.strip()}
        if fallback:
            seen.add(fallback.strip())

        abbreviated = [n for n in seen if self._is_abbreviated_player_name(n)]
        short_hint  = abbreviated[0] if abbreviated else (fallback or "")

        if player_id:
            params: dict = {"id": player_id}
            if season:
                params["season"] = season
            raw = self._get("players", params)
            if raw:
                profile = raw[0].get("player", raw[0])
                display = self._player_display_name(
                    profile.get("firstname"),
                    profile.get("lastname"),
                    short_hint or profile.get("name") or fallback,
                )
                if display and display != "—":
                    return display

        full_candidates = [n for n in seen if not self._is_abbreviated_player_name(n)]
        if full_candidates:
            best = max(full_candidates, key=len)
            parts = best.split()
            if len(parts) >= 2:
                return f"{parts[0]} {parts[-1]}"
            return best

        if fallback:
            return fallback
        return max(seen, key=len) if seen else "—"

    @staticmethod
    def _player_tally_key(player_id: int | None, name: str) -> str:
        if player_id:
            return f"id:{player_id}"
        return f"name:{(name or '').strip()}"

    def _touch_player_tally(
        self,
        tallies: dict[str, dict],
        player_id: int | None,
        name: str,
    ) -> str:
        key = self._player_tally_key(player_id, name)
        tallies.setdefault(key, {
            "goals": 0, "assists": 0, "apps": 0,
            "player_id": player_id, "names": set(),
        })
        if player_id:
            tallies[key]["player_id"] = player_id
        if name:
            tallies[key]["names"].add(name)
        return key

    def _get_key_players_from_recent(
        self,
        team_id: int,
        match_limit: int = 10,
        player_limit: int = 4,
        season: int | None = None,
    ) -> list:
        """
        Aggregate goals + assists from recent official matches (not squad order).
        """
        raw = self._get("fixtures", {"team": team_id, "last": 30, "status": "FT"}) or []
        official = [f for f in raw if not self._is_friendly_fixture(f)][:match_limit]
        if not official:
            return []

        if season is None:
            season = (official[0].get("league") or {}).get("season")

        from concurrent.futures import ThreadPoolExecutor
        fixture_ids = [
            f.get("fixture", {}).get("id")
            for f in official
            if f.get("fixture", {}).get("id")
        ]
        if fixture_ids:
            with ThreadPoolExecutor(max_workers=6) as executor:
                list(executor.map(self._get_fixture_events_cached, fixture_ids))
                list(executor.map(self._get_fixture_players_cached, fixture_ids))

        tallies: dict[str, dict] = {}

        for fixture in official:
            fid = fixture.get("fixture", {}).get("id")
            if not fid:
                continue
            if not self._tally_team_from_fixture_players(fid, team_id, tallies):
                self._tally_team_from_fixture_events(fid, team_id, tallies)

        if not tallies:
            return []

        ranked = sorted(
            tallies.items(),
            key=lambda x: (x[1]["goals"], x[1]["assists"], x[1]["apps"]),
            reverse=True,
        )

        players: list[dict] = []
        for _key, st in ranked[: max(player_limit, 8)]:
            if st["goals"] == 0 and st["assists"] == 0:
                continue
            fallback = max(st["names"], key=len) if st.get("names") else ""
            display_name = self._resolve_key_player_display_name(
                st.get("player_id"),
                st.get("names") or set(),
                season,
                fallback,
            )
            players.append({
                "name":           display_name,
                "goals":          st["goals"],
                "assists":        st["assists"],
                "apps":           st["apps"],
                "source":         "recent_matches",
                "window_matches": len(official),
            })
            if len(players) >= player_limit:
                break

        return self._finalize_key_players(players)

    @staticmethod
    def _finalize_key_players(players: list[dict]) -> list[dict]:
        if not players:
            return []
        max_g = max(p.get("goals") or 0 for p in players)
        max_a = max(p.get("assists") or 0 for p in players)
        tied_scorers = sum(1 for p in players if (p.get("goals") or 0) == max_g and max_g > 0)
        for p in players:
            p["label"] = PreMatchEngine._format_key_player_label(p, max_g, max_a, tied_scorers)
        return players

    @staticmethod
    def _format_key_player_label(player: dict, max_goals: int, max_assists: int, tied_scorers: int = 1) -> str:
        g = player.get("goals") or 0
        a = player.get("assists") or 0
        src = player.get("source", "")
        chunks: list[str] = []

        if g > 0:
            line = f"⚽ {g} гола"
            if g == max_goals and max_goals > 0:
                line += " — най-добър реализатор" if tied_scorers == 1 else " — в топ реализаторите"
            chunks.append(line)
        if a > 0:
            line = f"🎯 {a} асист."
            if a == max_assists and max_assists > 0 and (not g or a >= g):
                line += " — най-много създадени положения"
            chunks.append(line)

        if not chunks:
            return player.get("position") or "—"

        if src == "recent_matches":
            w = player.get("window_matches") or 10
            return f"{' + '.join(chunks)} (последните {w} официални мача)"
        if src == "league_topscorers":
            return f"{' + '.join(chunks)} (сезон в лигата)"

        return " + ".join(chunks)

    def _resolve_team_key_players(
        self,
        league_players: list,
        team_id: int,
        limit: int = 4,
        season: int | None = None,
        league_name: str = "",
    ) -> list:
        if league_players and self._should_use_league_topscorers(league_name):
            sorted_p = sorted(
                league_players,
                key=lambda x: (x.get("goals") or 0, x.get("assists") or 0),
                reverse=True,
            )
            players = []
            for p in sorted_p[:limit]:
                first = (p.get("firstname") or "").strip()
                last  = (p.get("lastname") or "").strip()
                short = p.get("name", "") or ""
                if first and last:
                    display = self._player_display_name(first, last, short)
                else:
                    display = self._resolve_key_player_display_name(
                        p.get("player_id"),
                        {short},
                        season,
                        short,
                    )
                players.append({
                    "name":    display,
                    "goals":   p.get("goals") or 0,
                    "assists": p.get("assists") or 0,
                    "apps":    p.get("apps") or 0,
                    "source":  "league_topscorers",
                })
            return self._finalize_key_players(players)

        return self._get_key_players_from_recent(
            team_id, player_limit=limit, season=season,
        )

    def _enrich_key_players(
        self,
        top_scorers: dict,
        home_id: int,
        away_id: int,
        season: int | None = None,
        league_name: str = "",
    ) -> dict:
        """League topscorers when available; else real stats from recent official matches."""
        return {
            "home": self._resolve_team_key_players(
                top_scorers.get("home") or [], home_id, season=season, league_name=league_name,
            ),
            "away": self._resolve_team_key_players(
                top_scorers.get("away") or [], away_id, season=season, league_name=league_name,
            ),
        }

    @staticmethod
    def _coach_display_name(entry: dict) -> str:
        first = (entry.get("firstname") or "").strip()
        last  = (entry.get("lastname") or "").strip()
        if first and last:
            parts = last.split()
            if len(parts) == 1:
                family = parts[0]
            elif len(parts[0]) >= 6:
                family = parts[0]
            else:
                family = parts[-1]
            return f"{first} {family}"
        return entry.get("name", "") or ""

    def _get_coaches(self, home_id: int, away_id: int) -> dict:
        """Fetch coach names for both teams (prefer full first + last name)."""
        result = {}
        for side, team_id in [("home", home_id), ("away", away_id)]:
            raw = self._get("coachs", {"team": team_id})
            if raw:
                result[side] = self._coach_display_name(raw[0])
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

        cards_profile = ""
        if games_evented > 0:
            cards_profile = (
                f"Средно {avg_yellow} жълти · {avg_red} червени на мач "
                f"(последните {games_evented} мача)"
            )

        return {
            "name":               ref_name,
            "games_this_season":  total_games,
            "avg_goals_per_game": avg_goals,
            "home_win_pct":       round(home_wins / max(total_games, 1) * 100),
            "avg_yellow":         avg_yellow,
            "avg_red":            avg_red,
            "avg_penalties":      avg_pen,
            "strictness":         strictness,
            "cards_sample_games": games_evented,
            "cards_profile":      cards_profile,
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

    @staticmethod
    def calculate_group_scenarios(
        full_table: list,
        home_id:    int,
        away_id:    int,
        home_name:  str = "",
        away_name:  str = "",
    ) -> dict:
        """
        Prematch group-stage scenarios: projected table positions for win / draw / loss.
        Only meaningful when standings already have played matches.
        """
        if not full_table:
            return {}

        bullets: list[str] = []
        for label, hg, ag in (
            (f"Победа на {home_name}", 1, 0),
            ("Равен", 1, 1),
            (f"Победа на {away_name}", 0, 1),
        ):
            impact = PreMatchEngine.calculate_table_impact(
                full_table, home_id, away_id, hg, ag, home_name, away_name,
            )
            if not impact:
                continue
            h = impact["home"]
            a = impact["away"]
            if hg > ag:
                extra = f" — {h['pos_label']}" if h.get("pos_label") else ""
                bullets.append(
                    f"{label} → {home_name} на {h['new_pos']}. място ({h['pts_after']} т.){extra}"
                )
            elif ag > hg:
                extra = f" — {a['pos_label']}" if a.get("pos_label") else ""
                bullets.append(
                    f"{label} → {away_name} на {a['new_pos']}. място ({a['pts_after']} т.){extra}"
                )
            else:
                h_extra = f", {h['pos_label']}" if h.get("pos_label") else ""
                a_extra = f", {a['pos_label']}" if a.get("pos_label") else ""
                bullets.append(
                    f"{label} → {home_name} {h['new_pos']}. място ({h['pts_after']} т.{h_extra}); "
                    f"{away_name} {a['new_pos']}. място ({a['pts_after']} т.{a_extra})"
                )

        if not bullets:
            return {}
        return {"available": True, "bullets": bullets}

    @staticmethod
    def compute_data_fingerprint(data: dict) -> str:
        """Stable hash of key prematch fields — used to detect stale client cache."""
        if not data:
            return ""
        pred = data.get("prediction") or {}
        latest_h2h = (data.get("h2h_raw") or [{}])[0] if data.get("h2h_raw") else {}
        key = {
            "home_win":   pred.get("home_win_pct"),
            "draw":       pred.get("draw_pct"),
            "away_win":   pred.get("away_win_pct"),
            "h2h_count":  data.get("h2h_count"),
            "h2h_latest": {
                "date":  latest_h2h.get("date"),
                "score": f"{latest_h2h.get('home_goals')}:{latest_h2h.get('away_goals')}",
            },
            "home_pos":   (data.get("home_standing") or {}).get("position"),
            "home_pts":   (data.get("home_standing") or {}).get("points"),
            "away_pos":   (data.get("away_standing") or {}).get("position"),
            "away_pts":   (data.get("away_standing") or {}).get("points"),
            "referee":    (data.get("referee") or {}).get("name"),
            "injuries_h": len((data.get("injuries") or {}).get("home") or []),
            "injuries_a": len((data.get("injuries") or {}).get("away") or []),
            "scorers_h":  [p.get("name") for p in (data.get("top_scorers") or {}).get("home") or []],
        }
        raw = json.dumps(key, sort_keys=True, default=str)
        return hashlib.md5(raw.encode()).hexdigest()[:12]

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

        hs = dict(home_stats or {})
        as_ = dict(away_stats or {})
        hs["source_label"] = self._stats_source_label(hs, league)
        as_["source_label"] = self._stats_source_label(as_, league)

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

        h2h_count   = len(h2h[:5])
        avg_h2h_goals = round(total_h2h_goals / max(h2h_count, 1), 1)

        home_id = meta.get("home_id")
        away_id = meta.get("away_id")
        season  = meta.get("season")

        h2h_latest_scorers = ""
        if h2h and h2h[0].get("fixture_id"):
            h2h_season = h2h[0].get("season")
            h2h_latest_scorers = self._h2h_goal_scorers_line(h2h[0]["fixture_id"], h2h_season)

        # Standings
        h_stand = standings.get("home", {})
        a_stand = standings.get("away", {})

        def _standings_meaningful(st: dict) -> bool:
            """Group/table position before any match played is misleading (e.g. WC group draw)."""
            return bool(st.get("position")) and (st.get("played") or 0) > 0

        standings_reliable = _standings_meaningful(h_stand) or _standings_meaningful(a_stand)

        group_scenarios: dict = {}
        if standings_reliable and standings.get("full_table") and home_id and away_id:
            group_scenarios = self.calculate_group_scenarios(
                standings["full_table"], home_id, away_id, home, away,
            )

        # Key advantages
        home_advantages = []
        away_advantages = []

        form_scope = "в последните мачове" if hs.get("source") != "league_season" else "в сезона"
        if hs.get("wins", 0) > as_.get("wins", 0):
            home_advantages.append(f"По-добри резултати {form_scope}")
        elif as_.get("wins", 0) > hs.get("wins", 0):
            away_advantages.append(f"По-добри резултати {form_scope}")

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

        if standings_reliable:
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
        h2h_factor = self._h2h_key_factor(h2h, h2h_count, avg_h2h_goals)
        if h2h_factor:
            key_factors.append(h2h_factor)
        key_factors.extend(self._comparison_key_factors(home, away, hs, as_))
        if _standings_meaningful(h_stand):
            key_factors.append(f"{home} е на {h_stand['position']} място с {h_stand.get('points', 0)} точки")
        if _standings_meaningful(a_stand):
            key_factors.append(f"{away} е на {a_stand['position']} място с {a_stand.get('points', 0)} точки")
        if not key_factors:
            key_factors = ["Статистически данни не са налични за тази лига в момента"]

        # Prediction (weighted: form points + standings + home advantage)
        h_pts   = hs.get("wins", 0) * 3 + hs.get("draws", 0)
        a_pts   = as_.get("wins", 0) * 3 + as_.get("draws", 0)
        h_pos   = h_stand.get("position", 10) or 10 if _standings_meaningful(h_stand) else 10
        a_pos   = a_stand.get("position", 10) or 10 if _standings_meaningful(a_stand) else 10
        pos_bonus_h = max(0, a_pos - h_pos) * 0.5 if standings_reliable else 0
        pos_bonus_a = max(0, h_pos - a_pos) * 0.5 if standings_reliable else 0
        # Home advantage bonus (5 pts) + better position bonus (only when table is live)
        h_score = h_pts + 5 + pos_bonus_h
        a_score = a_pts + pos_bonus_a

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
            "h2h_count":      h2h_count,
            "avg_h2h_goals":  avg_h2h_goals,
            "h2h_latest_scorers": h2h_latest_scorers,
            "home_standing":  h_stand,
            "away_standing":  a_stand,
            "standings_reliable": standings_reliable,
            "group_scenarios": group_scenarios,
            "home_advantages": home_advantages,
            "away_advantages": away_advantages,
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

    @staticmethod
    def _build_instant_broadcast_guide(data: dict) -> str:
        """
        Rule-based broadcast guide — same section structure as GPT, filled from API data.
        Shown immediately while GPT generates (~15-30s).
        """
        home   = data.get("home", "Домакин")
        away   = data.get("away", "Гост")
        league = data.get("league", "")
        date   = data.get("date", "—")
        hs     = data.get("home_stats") or {}
        as_    = data.get("away_stats") or {}
        hst    = data.get("home_standing") or {}
        ast    = data.get("away_standing") or {}
        pred   = data.get("prediction") or {}
        coaches = data.get("coaches") or {}

        def _form_block(team: str, stats: dict) -> str:
            if not (stats.get("played") or 0):
                return f"Статистиката за {team} не е налична в момента."
            scope = stats.get("source_label") or "последните мачове"
            avg_s = stats.get("avg_score")
            avg_c = stats.get("avg_conc")
            avg_s_txt = f"{avg_s:.2f}" if avg_s is not None else "—"
            avg_c_txt = f"{avg_c:.2f}" if avg_c is not None else "—"
            return (
                f"Форма ({scope}): {stats.get('wins', 0)}П {stats.get('draws', 0)}Р "
                f"{stats.get('losses', 0)}З · голове {stats.get('scored', 0)}:"
                f"{stats.get('conceded', 0)} · средно {avg_s_txt} вкарани / {avg_c_txt} допуснати на мач."
            )

        def _scorer_lines(side: str) -> str:
            players = (data.get("top_scorers") or {}).get(side) or []
            if not players:
                return "• Данните не са налични"
            return "\n".join(
                f"• {p.get('name', '?')} — {p.get('label') or 'няма статистика'}"
                for p in players
            )

        def _adv_lines(side: str) -> str:
            adv = (data.get("home_advantages") if side == "home" else data.get("away_advantages")) or []
            if not adv:
                return "• Няма изразено статистическо предимство в данните"
            return "\n".join(f"• {a}" for a in adv[:4])

        h2h_lines = []
        for factor in (data.get("key_factors") or []):
            if "H2H" in factor or "директ" in factor.lower() or "срещ" in factor.lower():
                h2h_lines.append(f"• {factor}")
        if data.get("h2h_latest_scorers"):
            h2h_lines.append(f"• {data['h2h_latest_scorers']}")
        for row in (data.get("h2h") or [])[:4]:
            h2h_lines.append(f"• {row}")
        if not h2h_lines:
            h2h_lines.append("• H2H данни не са налични")

        talking = []
        for i, factor in enumerate((data.get("key_factors") or [])[:5], 1):
            talking.append(f"{i}. **Тема {i}**: {factor}")
        if not talking:
            talking.append(f"1. **Контекст**: {home} срещу {away} в {league} на {date}.")

        if (hst.get("played") or 0) > 0 and hst.get("position"):
            h_rank = f"{hst['position']}. място, {hst.get('points', 0)} т."
        else:
            h_rank = "класирането още не е стартирало"
        if (ast.get("played") or 0) > 0 and ast.get("position"):
            a_rank = f"{ast['position']}. място, {ast.get('points', 0)} т."
        else:
            a_rank = "класирането още не е стартирало"

        coach_bit = ""
        if coaches.get("home") or coaches.get("away"):
            coach_bit = (
                f" Треньори: {coaches.get('home', '—')} ({home}) · "
                f"{coaches.get('away', '—')} ({away})."
            )

        ref = data.get("referee") or {}
        ref_bit = f" Съдия: {ref['name']} ({ref['cards_profile']})." if ref.get("name") and ref.get("cards_profile") else ""

        scenarios = (data.get("group_scenarios") or {}).get("bullets") or []
        scenario_bit = ""
        if scenarios:
            scenario_bit = " Сценарии: " + " | ".join(scenarios[:2])

        intro = (
            f"{league}, {date}: {home} срещу {away}.{coach_bit}{ref_bit} "
            f"Класиране: {home} — {h_rank}; {away} — {a_rank}.{scenario_bit}"
        )

        winner = home if (pred.get("home_win_pct") or 0) >= max(
            pred.get("draw_pct") or 0, pred.get("away_win_pct") or 0,
        ) else (
            away if (pred.get("away_win_pct") or 0) >= (pred.get("draw_pct") or 0) else "Равен"
        )

        return f"""## УВОД ЗА СТРИЙМА
{intro}

## ФОРМА И МОМЕНТ
### {home}
{_form_block(home, hs)}
### {away}
{_form_block(away, as_)}

## КЛЮЧОВИ ИГРАЧИ ЗА НАБЛЮДЕНИЕ
### {home}
{_scorer_lines("home")}
### {away}
{_scorer_lines("away")}

## ТАКТИЧЕСКИ РАЗБОР
### Как ще играе {home}?
{_adv_lines("home")}
### Как ще играе {away}?
{_adv_lines("away")}
### Ключовото тактическо противостоение
• Дуелът между атакуващия потенциал и отбраната ще определи мача — виж формата и H2H по-горе.

## ГОВОРНИ ТОЧКИ ЗА СТРИЙМА
{chr(10).join(talking)}

## ИСТОРИЧЕСКИ ФАКТИ
{chr(10).join(h2h_lines)}

## ОЧАКВАНЕ ЗА МАЧА
Очаквани общо голове (статистически модел): {pred.get('exp_goals', '—')}.
Моделът дава {pred.get('home_win_pct', '—')}% / {pred.get('draw_pct', '—')}% / {pred.get('away_win_pct', '—')}% за 1/X/2.

## ПРОГНОЗА
Победител (модел): {winner}
Вероятен резултат: {pred.get('likely_score', '—')}
Алтернатива: {pred.get('alt_score', '—')}
Над 2.5 гола: {pred.get('over25_pct', '—')}% · И двата отбора: {pred.get('btts_pct', '—')}%"""

    def _finalize_prematch_data(self, data: dict, fixture_id=None) -> dict:
        data["analyzed_at"] = datetime.now(timezone.utc).isoformat()
        data["fingerprint"] = self.compute_data_fingerprint(data)
        if data.get("gpt_narrative"):
            data["broadcast_guide_draft"] = ""
            data["gpt_guide_pending"] = False
        else:
            data["broadcast_guide_draft"] = self._build_instant_broadcast_guide(data)
            data["gpt_guide_pending"] = _gpt_available

        if not data.get("stream_facts"):
            data["stream_facts"] = self._build_stream_facts(data)
        if fixture_id:
            self._apply_cached_stream_facts(fixture_id, data)
        if "stream_facts_gpt_pending" not in data:
            data["stream_facts_gpt_pending"] = (
                _gpt_available and bool(data.get("stream_facts"))
            )
        return data

    # --------------------------------------------------
    # GPT EDITORIAL (Bulgarian)
    # --------------------------------------------------

    @staticmethod
    def _clean_gpt_placeholders(text: str) -> str:
        """Remove lines where GPT echoed prompt template brackets."""
        if not text:
            return ""
        placeholder_markers = (
            "[Играч]", "[позиция/роля]", "[статистика]", "[защо да го следим]",
            "[Тема]", "[факт с конкретни числа]", "[конкретна слабост]",
        )
        cleaned: list[str] = []
        for line in text.splitlines():
            if any(m in line for m in placeholder_markers):
                continue
            cleaned.append(line)
        return "\n".join(cleaned).strip()

    def _has_real_data(self, data: dict) -> bool:
        """Returns True only when we have verified API data to base analysis on."""
        hs  = data.get("home_stats", {})
        as_ = data.get("away_stats", {})
        hst = data.get("home_standing", {})
        ast = data.get("away_standing", {})

        has_form     = (hs.get("played", 0) or 0) > 0 or (as_.get("played", 0) or 0) > 0
        has_standing = (
            (hst.get("played") or 0) > 0 or (ast.get("played") or 0) > 0
        ) and bool(hst.get("position") or ast.get("position"))
        has_h2h      = len(data.get("h2h", [])) > 0

        return has_form or has_standing or has_h2h

    def _generate_gpt_editorial(
        self,
        fixture_id: int,
        data: dict,
    ) -> str:
        if not _gpt_available or _gpt_client is None:
            return ""

        cached_narr = self._get_cached_narrative(fixture_id)
        if cached_narr:
            return cached_narr

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
        has_standing = (
            (hst.get("played") or 0) > 0 or (ast.get("played") or 0) > 0
        ) and bool(hst.get("position") or ast.get("position"))
        has_h2h      = bool(data["h2h"])
        has_scorers  = bool(data.get("top_scorers", {}).get("home") or data.get("top_scorers", {}).get("away"))
        coaches      = data.get("coaches", {})

        # Top scorers text
        def scorer_line(player):
            label = player.get("label") or ""
            if label:
                return f"{player['name']} — {label}"
            g = player.get("goals", 0)
            a = player.get("assists", 0)
            parts = []
            if g:
                parts.append(f"{g} гола")
            if a:
                parts.append(f"{a} асист.")
            return f"{player['name']} ({', '.join(parts) if parts else 'няма данни'})"

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
{home}: {f"{hst['position']}. място, {hst['points']} точки, {hst.get('played',0)} изиграни" if (hst.get('played') or 0) > 0 and hst.get('position') else 'н/д (турнирът/лигата още не е започнала)'}
{away}: {f"{ast['position']}. място, {ast['points']} точки, {ast.get('played',0)} изиграни" if (ast.get('played') or 0) > 0 and ast.get('position') else 'н/д (турнирът/лигата още не е започнала)'}

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
(САМО играчи изрично изброени в секцията „КЛЮЧОВИ ИГРАЧИ“ по-горе. По 2–3 bullet-а на отбор: Име — позиция — статистика/роля — защо да го следим днес.)
(Ако в данните няма играчи — напиши един ред: „Ключови играчи: данните не са налични.“ НЕ копирай шаблони или placeholder текст.)
### {home}
### {away}

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
(5 теми — конкретни, базирани на данните, с контекст за 1-2 минути дискусия. Реални заглавия и текст, без квадратни скоби.)
1. **Заглавие**: 2-3 изречения с контекст, числа и мнение
2. **Заглавие**: 2-3 изречения
3. **Заглавие**: 2-3 изречения
4. **Заглавие**: 2-3 изречения
5. **Заглавие**: 2-3 изречения

## ИСТОРИЧЕСКИ ФАКТИ
(До 5 конкретни факта от H2H и статистиката — само реални данни. Ако липсват — по-малко факта, без измисляне.)

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
                max_tokens=1800,
                temperature=0.72,
            )
            narrative = self._clean_gpt_placeholders(resp.choices[0].message.content.strip())
            self._gpt_ts[fixture_id] = now
            self._store_narrative_cache(fixture_id, narrative)
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

        events_text = _format_events_block(events)

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

        recent_events = [e for e in events if (minute - _evt_minute(e)) <= 12]
        recent_text = "\n".join(_format_event_line(e) for e in recent_events[-8:]) or "Няма нови събития"

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

    _ft_cache: dict = {}  # fixture_id -> str (GPT summary)

    def get_cached_postmatch(self, fixture_id: int) -> str | None:
        return self._ft_cache.get(fixture_id)

    def get_instant_postmatch(
        self,
        fixture_id:    int,
        home:          str,
        away:          str,
        score_home:    int,
        score_away:    int,
        live_stats:    dict,
        events:        list,
        prematch_data: dict = None,
    ) -> str:
        """Fast rule-based FT summary for immediate UI; GPT refines in background."""
        cached = self._ft_cache.get(fixture_id)
        if cached:
            return cached
        return self._rule_based_postmatch(
            home, away, live_stats, events, score_home, score_away, prematch_data,
        )

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
            return self._rule_based_postmatch(home, away, live_stats, events, score_home, score_away, prematch_data)

        hs  = live_stats.get("home", {})
        as_ = live_stats.get("away", {})

        goals_home = [e for e in events if e.get("type") == "Goal" and _evt_team(e) == home]
        goals_away = [e for e in events if e.get("type") == "Goal" and _evt_team(e) == away]
        cards_home = [e for e in events if e.get("type") == "Card" and _evt_team(e) == home]
        cards_away = [e for e in events if e.get("type") == "Card" and _evt_team(e) == away]
        subs_home  = [e for e in events if e.get("type") == "subst" and _evt_team(e) == home]
        subs_away  = [e for e in events if e.get("type") == "subst" and _evt_team(e) == away]

        def fmt_goals(glist):
            return ", ".join(f"{_evt_player(g)} {_evt_minute(g)}'" for g in glist) or "—"

        def fmt_cards(clist):
            return ", ".join(
                f"{_evt_player(c)} ({_evt_detail(c)}) {_evt_minute(c)}'" for c in clist
            ) or "—"

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
                max_tokens=700,
                temperature=0.7,
            )
            text = resp.choices[0].message.content.strip()
            self._ft_cache[fixture_id] = text
            print(f"[POSTMATCH] GPT summary generated for {home} {score_home}:{score_away} {away}")
            return text
        except Exception as e:
            print(f"[POSTMATCH] GPT error: {e}")
            return self._rule_based_postmatch(home, away, live_stats, events, score_home, score_away, prematch_data)

    def _rule_based_postmatch(
        self, home: str, away: str,
        live_stats: dict, events: list,
        score_home: int, score_away: int,
        prematch_data: dict = None,
    ) -> str:
        hs  = live_stats.get("home", {})
        as_ = live_stats.get("away", {})
        if   score_home > score_away: winner, loser = home, away
        elif score_away > score_home: winner, loser = away, home
        else:                         winner, loser = None, None

        goals_home = [e for e in events if e.get("type") == "Goal" and _evt_team(e) == home]
        goals_away = [e for e in events if e.get("type") == "Goal" and _evt_team(e) == away]

        def fmt_goals(glist):
            return ", ".join(f"{_evt_player(g)} {_evt_minute(g)}'" for g in glist) or "—"

        h_shots = hs.get("shots_total", 0) or 0
        a_shots = as_.get("shots_total", 0) or 0
        h_xg = hs.get("xg")
        a_xg = as_.get("xg")
        h_pos = hs.get("possession", 0) or 0
        a_pos = as_.get("possession", 0) or 0

        if score_home > score_away:
            outcome = f"{home} победи с {score_home}:{score_away}."
        elif score_away > score_home:
            outcome = f"{away} победи с {score_away}:{score_home}."
        else:
            outcome = f"Равенство {score_home}:{score_away}."

        pred_note = ""
        if prematch_data:
            pred = prematch_data.get("prediction", {})
            if pred.get("likely_score"):
                pred_note = f" Предмачовата прогноза беше {pred['likely_score']}."

        lines = [
            "## КАК ЗАВЪРШИ МАЧЪТ?",
            f"{home} {score_home}:{score_away} {away}. {outcome}{pred_note}",
            "",
            "## СТАТИСТИКА",
            f"{home}: {h_shots} удара ({hs.get('shots_on_target', 0)} в рамките), xG {h_xg if h_xg is not None else '—'}, {h_pos}% владение, {hs.get('corners', 0)} ъглови",
            f"{away}: {a_shots} удара ({as_.get('shots_on_target', 0)} в рамките), xG {a_xg if a_xg is not None else '—'}, {a_pos}% владение, {as_.get('corners', 0)} ъглови",
        ]

        if goals_home or goals_away:
            lines += ["", "## ГОЛОВЕ", f"{home}: {fmt_goals(goals_home)}", f"{away}: {fmt_goals(goals_away)}"]

        if winner:
            edge = []
            try:
                if h_xg is not None and a_xg is not None and float(h_xg) != float(a_xg):
                    better_xg = home if float(h_xg) > float(a_xg) else away
                    edge.append(f"xG ({better_xg})")
            except (TypeError, ValueError):
                pass
            if h_shots != a_shots:
                edge.append(f"удари ({home if h_shots > a_shots else away})")
            if h_pos != a_pos:
                edge.append(f"владение ({home if h_pos > a_pos else away})")
            edge_txt = f" по {' и '.join(edge)}" if edge else ""
            lines += ["", "## КОЙ ЗАСЛУЖИ ПОБЕДАТА И ЗАЩО?", f"{winner} контролира ключовите показатели{edge_txt} и заслужено взе трите точки."]
        else:
            lines += ["", "## КОЙ ЗАСЛУЖИ ПОБЕДАТА И ЗАЩО?", "Балансиран мач — и двата отбора имаха моменти за победа."]

        all_goals = sorted(
            [e for e in events if e.get("type") == "Goal"],
            key=_evt_minute,
        )
        if all_goals:
            key_g = all_goals[-1] if winner else all_goals[0]
            lines += [
                "",
                "## КЛЮЧОВИЯТ МОМЕНТ НА МАЧА",
                f"{_evt_minute(key_g)}' — {_evt_player(key_g)} ({_evt_team(key_g)})",
            ]

        def rough_rating(shots, xg_val, pos, won):
            score = 5.5
            score += min(shots, 20) * 0.05
            score += (pos - 50) * 0.02
            try:
                if xg_val is not None:
                    score += float(xg_val) * 0.4
            except (TypeError, ValueError):
                pass
            if won is True:
                score += 0.8
            elif won is False:
                score -= 0.3
            return max(4.0, min(9.5, round(score, 1)))

        h_won = True if winner == home else (False if winner == away else None)
        a_won = True if winner == away else (False if winner == home else None)
        lines += [
            "",
            "## ОЦЕНКИ",
            f"{home}: {rough_rating(h_shots, h_xg, h_pos, h_won)}/10",
            f"{away}: {rough_rating(a_shots, a_xg, a_pos, a_won)}/10",
            "",
            "## ЗАКЛЮЧЕНИЕ ЗА СТРИЙМА",
            f"Мачът приключи {score_home}:{score_away}. Пълният AI анализ се доразработва във фонов режим.",
        ]
        return "\n".join(lines)

    # --------------------------------------------------
    # MAIN PIPELINE
    # --------------------------------------------------

    def get_cached_prematch(self, fixture_id: int) -> dict | None:
        """Return cached prematch result if still valid (incl. long-lived GPT cache)."""
        cached = self._cache.get(fixture_id)
        if not cached:
            return None
        if time.time() - cached["ts"] >= self._cache_ttl_for(cached["data"]):
            return None
        result = cached["data"]
        data = result.get("data", {})
        self._apply_cached_narrative(fixture_id, data)
        return result

    def analyze_fast(self, fixture_id: int, match: dict) -> dict:
        """
        Phase 1: rule-based analysis only (no GPT). Fast — ~2-3 seconds.
        Broadcasts immediately so user sees data quickly.
        """
        cached = self._cache.get(fixture_id)
        if cached and time.time() - cached["ts"] < self._cache_ttl_for(cached["data"]):
            result = cached["data"]
            data = result.get("data", {})
            self._apply_cached_narrative(fixture_id, data)
            return result

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
            for future in as_completed(futures, timeout=15):
                key = futures[future]
                try:
                    results[key] = future.result(timeout=10)
                except Exception:
                    results[key] = {} if key not in ("h2h",) else []

        h2h         = results.get("h2h", [])
        home_stats  = results.get("home_stats", {})
        away_stats  = results.get("away_stats", {})
        standings   = results.get("standings", {})
        top_scorers = self._enrich_key_players(
            results.get("top_scorers", {}), home_id, away_id,
            season=season, league_name=meta.get("league_name", ""),
        )
        coaches     = results.get("coaches", {})
        referee     = results.get("referee", {})
        injuries    = results.get("injuries", {})

        data = self._build_rule_based(
            meta, h2h, home_stats, away_stats, standings,
            top_scorers=top_scorers, coaches=coaches,
            referee=referee, injuries=injuries,
        )
        self._apply_cached_narrative(fixture_id, data)
        self._finalize_prematch_data(data, fixture_id)

        # No GPT here — that's Phase 2 (unless narrative restored from cache)
        result = {"available": True, "data": data, "meta": meta}
        self._cache[fixture_id] = {"ts": time.time(), "data": result}

        has_scorers = bool(top_scorers.get("home") or top_scorers.get("away"))
        recent_src  = any(p.get("source") == "recent_matches" for p in top_scorers.get("home", []) + top_scorers.get("away", []))
        print(
            f"[PREMATCH] Fast done — {meta['home_name']} vs {meta['away_name']} | "
            f"Players={has_scorers}{' (recent)' if recent_src else ''} | Coach={bool(coaches)}"
        )
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

        self._apply_cached_narrative(fixture_id, data)
        self._apply_cached_stream_facts(fixture_id, data)

        needs_narrative = not data.get("gpt_narrative")
        needs_facts = bool(data.get("stream_facts_gpt_pending"))

        if not needs_narrative and not needs_facts:
            result["data"] = data
            self._cache[fixture_id] = {"ts": time.time(), "data": result}
            return result

        if needs_narrative:
            narrative = self._generate_gpt_editorial(fixture_id, data)
            if narrative:
                data["gpt_narrative"] = narrative
                data["broadcast_guide_draft"] = ""
                data["gpt_guide_pending"] = False

        if needs_facts:
            polished = self._generate_stream_facts_gpt(fixture_id, data)
            if polished:
                data["stream_facts"] = polished
            data["stream_facts_gpt_pending"] = False

        result["data"] = data
        self._cache[fixture_id] = {"ts": time.time(), "data": result}
        return result if (data.get("gpt_narrative") or data.get("stream_facts")) else None

    def analyze(self, fixture_id: int, match: dict) -> dict:
        """
        Full pre-match analysis pipeline (legacy — used by loop).
        Returns structured editorial data (rule-based + optional GPT).
        Cached per fixture for 5 minutes.
        """
        cached = self._cache.get(fixture_id)
        if cached and time.time() - cached["ts"] < self._cache_ttl_for(cached["data"]):
            result = cached["data"]
            data = result.get("data", {})
            self._apply_cached_narrative(fixture_id, data)
            return result

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
            for future in as_completed(futures, timeout=15):
                key = futures[future]
                try:
                    results[key] = future.result(timeout=10)
                except Exception:
                    results[key] = {} if key not in ("h2h",) else []

        h2h         = results.get("h2h", [])
        home_stats  = results.get("home_stats", {})
        away_stats  = results.get("away_stats", {})
        standings   = results.get("standings", {})
        top_scorers = self._enrich_key_players(
            results.get("top_scorers", {}), home_id, away_id,
            season=season, league_name=meta.get("league_name", ""),
        )
        coaches     = results.get("coaches", {})
        referee     = results.get("referee", {})
        injuries    = results.get("injuries", {})

        data = self._build_rule_based(
            meta, h2h, home_stats, away_stats, standings,
            top_scorers=top_scorers, coaches=coaches,
            referee=referee, injuries=injuries,
        )
        self._apply_cached_narrative(fixture_id, data)
        if _gpt_available and not data.get("gpt_narrative"):
            data["gpt_narrative"] = self._generate_gpt_editorial(fixture_id, data)
        self._finalize_prematch_data(data, fixture_id)
        if _gpt_available and not self._get_cached_stream_facts(fixture_id):
            polished = self._generate_stream_facts_gpt(fixture_id, data)
            if polished:
                data["stream_facts"] = polished
            data["stream_facts_gpt_pending"] = False

        result = {"available": True, "data": data, "meta": meta}
        self._cache[fixture_id] = {"ts": time.time(), "data": result}

        has_scorers = bool(top_scorers.get("home") or top_scorers.get("away"))
        print(f"[PREMATCH] Done — {meta['home_name']} vs {meta['away_name']} | GPT={'yes' if _gpt_available else 'no'} | Players={has_scorers} | Coach={bool(coaches)}")
        return result
