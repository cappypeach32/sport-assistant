from datetime import datetime, timezone
import asyncio
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

from data.director.sport_service import get_fixtures
from data.director.match_selection_engine import MatchSelectionEngine

# Phase 1 — AI Match Intelligence
from data.director.live_stats_collector import LiveStatsCollector
from data.director.momentum_engine import MomentumEngine
from data.director.key_moments_detector import KeyMomentsDetector
from data.director.tactical_engine import TacticalEngine

# Phase 2 — Pre-Match Editorial + Live Narrative
from data.director.prematch_engine import PreMatchEngine, _is_live, _is_ns, _gpt_available

# Phase 3 — Live Win Probability (removed from UI — stream-only assistant)
from data.director.broadcast_package import build_halftime_package, build_fulltime_package
from data.director.prep_kit import build_prep_kit


# =====================================================
# APP INIT
# =====================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start loop on startup, cancel it cleanly on shutdown (prevents zombie loops with --reload)."""
    task = asyncio.create_task(live_stats_loop())
    print("[STARTUP] AI Match Intelligence v3.0 — Phase 1 active")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="AI Sports Broadcast Assistant",
    version="3.0.0",
    lifespan=lifespan,
)

jinja_env = Environment(
    loader=FileSystemLoader("templates"),
    auto_reload=True
)

match_selector = MatchSelectionEngine()

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Phase 1 engines
stats_collector  = LiveStatsCollector()
momentum_engine  = MomentumEngine()
moments_detector = KeyMomentsDetector()
tactical_engine  = TacticalEngine()

# Phase 2 engine
prematch_engine  = PreMatchEngine()


# =====================================================
# GLOBAL STATE
# =====================================================

active_match: dict | None = None
clients: set = set()
state_lock = asyncio.Lock()

# Phase 1 — latest real intelligence (updated by background loop)
latest_intelligence: dict = {}

# Phase 2 — latest pre-match editorial + live narrative + half-time
latest_prematch:   dict = {}
latest_narrative:  str  = ""
latest_halftime:   str  = ""
latest_postmatch:    str   = ""
latest_postmatch_gpt_pending: bool = False
latest_commentary:   list  = []   # list of { minute, title, text }
_last_ht_status:     str   = ""
_last_ft_status:     str   = ""
_postmatch_gpt_spawned: set = set()  # fixture_ids with GPT task already started
_prep_gpt_spawned: set = set()       # prep page GPT tasks (independent of active match)
_prep_gpt_started: dict = {}         # fixture_id → spawn timestamp (stale watchdog)
PREP_GPT_STALE_SEC = 95              # re-spawn if background GPT appears stuck
_overlay_gpt_warm_spawned: set = set()  # deferred broadcast guide after prep
_last_goal_count:    int   = 0    # detect goals for forced commentary refresh
_last_prematch_refresh: dict = {}  # fixture_id → last background refresh ts
PREMATCH_REFRESH_INTERVAL = 1800   # 30 min — refresh prematch data for active match
latest_health:       dict  = {
    "last_poll_at": None,
    "last_error":   "",
    "api_ok":       False,
}


# =====================================================
# BROADCAST
# =====================================================

async def broadcast(payload: dict):
    global latest_postmatch, latest_postmatch_gpt_pending
    if payload.get("phase") == "finished" and payload.get("postmatch_summary"):
        latest_postmatch = payload["postmatch_summary"]
        if "postmatch_gpt_pending" in payload:
            latest_postmatch_gpt_pending = bool(payload["postmatch_gpt_pending"])

    dead = []
    for ws in clients:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for d in dead:
        clients.discard(d)


# =====================================================
# LIVE EVENTS (API-Football only — no simulated feed)
# =====================================================

def _event_icon(event_type: str, detail: str) -> str:
    if event_type == "Goal":
        return "⚽"
    if event_type == "Card":
        return "🟥" if "Red" in detail else "🟨"
    if event_type == "subst":
        return "🔄"
    if event_type == "Var":
        return "📺"
    return "📋"


def map_fixture_events(fixture_id: int | None) -> list[dict]:
    """Real match events from API-Football /fixtures/events."""
    if not fixture_id:
        return []
    rows = stats_collector.get_events(fixture_id)
    out: list[dict] = []
    for row in rows:
        etype = row.get("type", "") or ""
        detail = row.get("detail", "") or ""
        player = row.get("player", "") or ""
        team = row.get("team", "") or ""
        minute = row.get("minute", 0) or 0
        message = detail
        if player:
            message = f"{detail} — {player}" if detail else player
        out.append({
            "type": etype,
            "detail": detail,
            "icon": _event_icon(etype, detail),
            "team": team,
            "minute": minute,
            "message": message.strip(" —"),
            "player": player,
        })
    return sorted(out, key=lambda e: e.get("minute", 0), reverse=True)


def _empty_momentum_payload() -> dict:
    return {
        "home_pct": 50,
        "away_pct": 50,
        "home_xg_delta": 0,
        "away_xg_delta": 0,
        "home_shots_delta": 0,
        "away_shots_delta": 0,
        "home_trend": "—",
        "away_trend": "—",
        "dominant_team": "neutral",
        "pressure_spikes": [],
        "window_minutes": 8,
        "data_source": "unavailable",
    }


def _phase_live_summary(phase: str, real_available: bool) -> str:
    if phase == "prematch":
        return "Преди старта — използвайте предматчовия анализ."
    if phase == "finished":
        return "Мачът приключи — вижте финалния анализ."
    if real_available:
        return "Live статистики от API-Football."
    return "Изчакване на live статистики от API-Football…"


# =====================================================
# UI STATE
# =====================================================

def build_ui_state(momentum: dict, live_state: dict) -> dict:
    hm = momentum.get("home_momentum_pct", 50)
    am = momentum.get("away_momentum_pct", 50)
    swing = abs(hm - am)

    tempo = str(live_state.get("tempo", "")).lower()

    ui_theme = "balanced"
    if swing > 30:
        ui_theme = "dominant"
    if "fast" in tempo or "explosive" in tempo:
        ui_theme = "high_tempo"
    if swing > 40:
        ui_theme = "explosive"

    return {
        "theme": ui_theme,
        "animations": {
            "pulse": ui_theme in ["explosive", "high_tempo"],
            "glow":  ui_theme in ["dominant", "explosive"],
        },
    }


# =====================================================
# MATCH PHASE DETECTION
# =====================================================

LIVE_STATUSES = {
    "First Half", "Second Half", "Half Time",
    "Extra Time", "Break Time", "Penalty In Progress",
    "Live", "In Progress",
}
NS_STATUSES = {"Not Started", "Time To Be Defined", "Scheduled"}
LIVE_STATUSES_SHORT = {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT"}
FINISHED_STATUSES_SHORT = {"FT", "AET", "PEN"}
HT_STATUSES_SHORT = {"HT"}


def _match_status_long(match: dict) -> str:
    return (match.get("status") or "").strip()


def _match_status_short(match: dict) -> str:
    return (match.get("status_short") or "").strip().upper()


def sync_match_from_live_fixture(match: dict, live_fixture: dict) -> None:
    """Refresh status and score on active_match from latest API poll."""
    if not match or not live_fixture:
        return
    short = live_fixture.get("status_short")
    long_ = live_fixture.get("status_long")
    if short:
        match["status_short"] = short
    if long_:
        match["status"] = long_
    hg = live_fixture.get("home_goals")
    ag = live_fixture.get("away_goals")
    if hg is not None:
        match["home_goals"] = hg
    if ag is not None:
        match["away_goals"] = ag


def is_halftime(match: dict) -> bool:
    short = _match_status_short(match)
    long_lower = _match_status_long(match).lower()
    if short in HT_STATUSES_SHORT:
        return True
    return "half time" in long_lower or long_lower == "halftime"


def is_finished_match(match: dict) -> bool:
    short = _match_status_short(match)
    long_lower = _match_status_long(match).lower()
    if short in FINISHED_STATUSES_SHORT:
        return True
    return any(
        kw in long_lower
        for kw in ("match finished", "full time", "after extra time", "penalties")
    )


def get_match_phase(match: dict) -> str:
    """Returns 'prematch' | 'live' | 'finished'"""
    if is_finished_match(match):
        return "finished"

    status = _match_status_long(match)
    short = _match_status_short(match)

    if status in LIVE_STATUSES or short in LIVE_STATUSES_SHORT:
        return "live"

    long_lower = status.lower()
    if any(
        kw in long_lower
        for kw in (
            "first half", "second half", "half time", "extra time",
            "penalty", "in progress", "break time",
        )
    ):
        return "live"

    if short == "NS" or status in NS_STATUSES:
        return "prematch"

    return "prematch"


# =====================================================
# PHASE 1 — LIVE INTELLIGENCE BUILDER
# =====================================================

def build_live_intelligence(match: dict, minute: int) -> dict:
    """
    Pulls real stats, calculates momentum, detects key moments,
    and runs tactical analysis for a live fixture.

    Falls back gracefully when stats are unavailable (pre-match,
    no xG data, etc.).
    """
    fixture_id = match.get("raw_id")
    home = match.get("home", "Home")
    away = match.get("away", "Away")

    if not fixture_id:
        return {"available": False}

    # --- Real stats ---
    stats = stats_collector.get_live_stats(fixture_id)
    lineups = stats_collector.get_lineups(fixture_id, home, away)

    # Consider stats valid if the API returned team names AND at least one meaningful stat
    has_api_response = bool(stats.get("home_team") or stats.get("away_team"))
    home_stats = stats.get("home", {})
    away_stats = stats.get("away", {})
    has_meaningful_data = (
        home_stats.get("possession") is not None
        or home_stats.get("shots_total") not in (None, 0, "0")
        or away_stats.get("possession") is not None
        or away_stats.get("shots_total") not in (None, 0, "0")
    )
    if not has_api_response or not has_meaningful_data:
        if has_api_response and not has_meaningful_data:
            print(f"[INTELLIGENCE] API responded but stats are empty for fixture {fixture_id} — waiting for data")
        else:
            print(f"[INTELLIGENCE] No API response for fixture {fixture_id}")
        return {"available": False}

    print(f"[INTELLIGENCE] Real stats OK — home={stats.get('home_team')} away={stats.get('away_team')} | possession={stats.get('home',{}).get('possession','?')}% vs {stats.get('away',{}).get('possession','?')}% | xG={stats.get('home',{}).get('xg','?')} vs {stats.get('away',{}).get('xg','?')}")

    # --- Momentum ---
    # Seed with a zero-baseline snapshot so the first real snapshot
    # immediately produces a meaningful delta (cumulative xG/shots from 0).
    snaps = momentum_engine._history.get(fixture_id, [])
    if len(snaps) == 0 and minute > 5:
        baseline_minute = max(0, minute - momentum_engine.WINDOW_MINUTES)
        zero_stats = {
            "home": {k: 0 for k in stats.get("home", {})},
            "away": {k: 0 for k in stats.get("away", {})},
        }
        momentum_engine.add_snapshot(fixture_id, zero_stats, baseline_minute)

    momentum_engine.add_snapshot(fixture_id, stats, minute)
    momentum = momentum_engine.calculate(fixture_id)

    # --- Key Moments ---
    key_moments = moments_detector.detect(
        momentum=momentum,
        stats=stats,
        home_team=home,
        away_team=away,
        minute=minute,
    )

    # --- Tactical Analysis ---
    tactical = tactical_engine.analyze(
        fixture_id=fixture_id,
        home_team=home,
        away_team=away,
        lineups=lineups,
        stats=stats,
        momentum=momentum,
        minute=minute,
    )

    return {
        "available":   True,
        "stats":       stats,
        "momentum":    momentum,
        "key_moments": key_moments,
        "tactical":    tactical,
        "lineups":     lineups,
    }


# =====================================================
# CORE MATCH BUILDER
# =====================================================

def _should_show_ht_package(match: dict, phase: str) -> bool:
    if is_halftime(match):
        return True
    short = (match.get("status_short") or "").upper()
    if short == "2H" and phase == "live":
        return True
    status = (match.get("status") or "").lower()
    return phase == "live" and "second half" in status


async def _ensure_postmatch(match: dict, *, spawn_gpt: bool = True) -> None:
    """Build instant FT summary immediately; optionally start GPT refinement."""
    global latest_postmatch, latest_postmatch_gpt_pending, latest_intelligence, _postmatch_gpt_spawned

    fixture_id = match.get("raw_id")
    if not fixture_id:
        return

    loop = asyncio.get_running_loop()

    try:
        lf = await loop.run_in_executor(None, stats_collector.get_live_fixture, fixture_id)
        sync_match_from_live_fixture(match, lf or {})
    except Exception as e:
        print(f"[POSTMATCH] fixture sync error: {e}")

    if get_match_phase(match) != "finished":
        return

    cached = prematch_engine.get_cached_postmatch(fixture_id)
    if cached:
        latest_postmatch = cached
        latest_postmatch_gpt_pending = False
        return

    live_stats = latest_intelligence.get("stats", {})
    events = stats_collector._events_cache.get(fixture_id, {}).get("data", [])
    if not events:
        try:
            events = await loop.run_in_executor(None, stats_collector.get_events, fixture_id)
        except Exception as e:
            print(f"[POSTMATCH] events fetch error: {e}")
            events = []
    if not live_stats:
        try:
            intel = await loop.run_in_executor(None, build_live_intelligence, match, 90)
            if intel.get("available"):
                latest_intelligence.update(intel)
                live_stats = intel.get("stats", {})
        except Exception as e:
            print(f"[POSTMATCH] intelligence fetch error: {e}")

    lf = await loop.run_in_executor(None, stats_collector.get_live_fixture, fixture_id)
    sync_match_from_live_fixture(match, lf or {})
    score_h = (lf or {}).get("home_goals", match.get("home_goals", 0)) or 0
    score_a = (lf or {}).get("away_goals", match.get("away_goals", 0)) or 0
    pm_data = latest_prematch.get("data", {}) if latest_prematch.get("available") else {}

    if not latest_postmatch:
        latest_postmatch = prematch_engine.get_instant_postmatch(
            fixture_id,
            match.get("home", ""),
            match.get("away", ""),
            score_h,
            score_a,
            live_stats,
            events,
            pm_data,
        )
        print(f"[POSTMATCH] Instant summary for {match.get('home')} vs {match.get('away')}")

    if (
        spawn_gpt
        and _gpt_available
        and fixture_id not in prematch_engine._ft_cache
        and fixture_id not in _postmatch_gpt_spawned
    ):
        _postmatch_gpt_spawned.add(fixture_id)
        latest_postmatch_gpt_pending = True
        asyncio.create_task(_load_postmatch_gpt_async(
            fixture_id, match, score_h, score_a, live_stats, events, pm_data,
        ))
    elif not prematch_engine.get_cached_postmatch(fixture_id):
        latest_postmatch_gpt_pending = False


def build_overlay_response(
    match: dict,
    intelligence: dict | None = None,
    prematch: dict | None = None,
    live_narrative: str = "",
    halftime_analysis: str = "",
    postmatch_summary: str = "",
    postmatch_gpt_pending: bool = False,
) -> dict:

    if not match:
        return {}

    home = match.get("home", "Home")
    away = match.get("away", "Away")
    fixture_id = match.get("raw_id")
    live_fixture: dict = {}

    if fixture_id:
        # Prefer cache to avoid blocking async handlers on every broadcast
        cache_entry = stats_collector._stats_cache.get(f"fixture_{fixture_id}")
        if cache_entry and (time.time() - cache_entry["ts"]) < 45:
            live_fixture = cache_entry["data"]
        elif match.get("home_goals") is not None and is_finished_match(match):
            live_fixture = {
                "home_goals":  match.get("home_goals"),
                "away_goals":  match.get("away_goals"),
                "minute":      90,
                "status_short": match.get("status_short", "FT"),
                "status_long":  match.get("status", "Match Finished"),
            }
        else:
            live_fixture = stats_collector.get_live_fixture(fixture_id) or {}
        sync_match_from_live_fixture(match, live_fixture)

    match_phase = get_match_phase(match)

    # --- Phase 1 intelligence (API-Football only) ---
    intel = intelligence or {}
    real_available = intel.get("available", False)

    minute = int(live_fixture.get("minute") or 0)
    if minute <= 0 and match_phase != "live":
        minute = 0

    if real_available:
        momentum    = intel.get("momentum", {})
        key_moments = intel.get("key_moments", [])
        tactical    = intel.get("tactical", {})
        stats       = intel.get("stats", {})
        real_minute = live_fixture.get("minute", 0)
        if real_minute and int(real_minute) > 0:
            minute = int(real_minute)
    else:
        momentum    = {}
        key_moments = []
        tactical    = {}
        stats       = {}

    momentum_payload = (
        {
            "home_pct":           momentum.get("home_momentum_pct", 50),
            "away_pct":           momentum.get("away_momentum_pct", 50),
            "home_xg_delta":      momentum.get("home_xg_delta", 0),
            "away_xg_delta":      momentum.get("away_xg_delta", 0),
            "home_shots_delta":   momentum.get("home_shots_delta", 0),
            "away_shots_delta":   momentum.get("away_shots_delta", 0),
            "home_trend":         momentum.get("home_trend", "—"),
            "away_trend":         momentum.get("away_trend", "—"),
            "dominant_team":      momentum.get("dominant_team", "neutral"),
            "pressure_spikes":    momentum.get("pressure_spikes", []),
            "window_minutes":     momentum.get("window_minutes", 8),
            "data_source":        "real",
        }
        if real_available
        else _empty_momentum_payload()
    )

    match_events = (
        map_fixture_events(fixture_id)
        if fixture_id and match_phase in ("live", "finished")
        else []
    )

    ui_state = build_ui_state(momentum, {}) if real_available else {"theme": "balanced", "animations": {"pulse": False, "glow": False}}

    # --- Pre-match editorial ---
    pm      = prematch or {}
    pm_data = pm.get("data", {}) if pm.get("available") else {}

    # --- Live goals from fixture data ---
    live_hg = live_fixture.get("home_goals")
    live_ag = live_fixture.get("away_goals")

    # --- Live win probability (disabled) ---
    win_prob = {}

    # --- Live table impact (calculated when score is available) ---
    table_impact = {}
    full_table = pm_data.get("full_table", [])
    pm_meta    = pm.get("meta", {})
    if full_table and live_hg is not None and live_ag is not None:
        table_impact = prematch_engine.calculate_table_impact(
            full_table,
            home_id    = pm_meta.get("home_id", 0),
            away_id    = pm_meta.get("away_id", 0),
            home_goals = live_hg,
            away_goals = live_ag,
            home_name  = home,
            away_name  = away,
        )

    lineups_detail: dict = {"home": {}, "away": {}}
    recent_subs: list = []
    if fixture_id:
        lineups_detail = stats_collector.get_lineups(fixture_id, home, away)
        if match_phase in ("live", "finished"):
            for ev in stats_collector.get_events(fixture_id):
                if ev.get("type") == "subst":
                    recent_subs.append({
                        "minute": ev.get("minute", 0),
                        "team":   ev.get("team", ""),
                        "player": ev.get("player", ""),
                    })
            recent_subs.sort(key=lambda x: x.get("minute", 0), reverse=True)
            recent_subs = recent_subs[:8]

    stats_for_pkg = {
        "home": stats.get("home", {}) if real_available else {},
        "away": stats.get("away", {}) if real_available else {},
    }
    if match_phase in ("live", "finished") and fixture_id and not stats_for_pkg.get("home"):
        cached_stats = stats_collector._stats_cache.get(fixture_id)
        if cached_stats and cached_stats.get("data"):
            cs = cached_stats["data"]
            stats_for_pkg = {
                "home": cs.get("home", {}),
                "away": cs.get("away", {}),
            }
    score_h = int(live_hg if live_hg is not None else match.get("home_goals") or 0)
    score_a = int(live_ag if live_ag is not None else match.get("away_goals") or 0)

    if match_phase == "finished" and not postmatch_summary and fixture_id:
        events_for_pm = match_events
        if not events_for_pm:
            cached_ev = stats_collector._events_cache.get(fixture_id, {})
            events_for_pm = cached_ev.get("data", []) if cached_ev else []
        postmatch_summary = (
            prematch_engine.get_cached_postmatch(fixture_id)
            or prematch_engine.get_instant_postmatch(
                fixture_id, home, away, score_h, score_a,
                stats_for_pkg, events_for_pm, pm_data,
            )
        )

    broadcast_package: dict = {"active": False}
    if _should_show_ht_package(match, match_phase):
        broadcast_package = build_halftime_package(
            home, away, score_h, score_a, stats_for_pkg, halftime_analysis
        )
    elif match_phase == "finished":
        broadcast_package = build_fulltime_package(
            home, away, score_h, score_a, stats_for_pkg,
            postmatch_summary, postmatch_gpt_pending,
        )

    return {
        "type":    "LIVE_UPDATE",
        "success": True,
        "version": "3.0.0",
        "phase":   match_phase,

        "match": {
            "home":          home,
            "away":          away,
            "status":        match.get("status"),
            "status_short":  match.get("status_short", live_fixture.get("status_short", "")),
            "competition":   match.get("competition"),
            "minute":        minute,
            "raw_id":        match.get("raw_id"),
            "start_time":    match.get("start_time"),
            "home_goals":    live_fixture.get("home_goals"),
            "away_goals":    live_fixture.get("away_goals"),
        },

        "editorial": {
            "intro":        f"{home} срещу {away}",
            "live_summary": _phase_live_summary(match_phase, real_available),
            "key_factors":  pm_data.get("key_factors", [])[:3] if pm.get("available") else [],
        },

        # Phase 1 — real momentum (hidden in UI when data_source is unavailable)
        "momentum": momentum_payload,

        # Phase 1 — key moments alerts
        "key_moments": key_moments[:3],  # top 3 by severity

        # Phase 1 — tactical analysis
        "tactical": {
            "home_formation": tactical.get("home_formation", ""),
            "away_formation": tactical.get("away_formation", ""),
            "home_styles":    tactical.get("home_tactics", {}).get("active_styles", []),
            "away_styles":    tactical.get("away_tactics", {}).get("active_styles", []),
            "narrative":      tactical.get("narrative", ""),
        },

        # Live stats summary (for overlay display)
        "live_stats": {
            "home_possession":      stats.get("home", {}).get("possession", "–"),
            "away_possession":      stats.get("away", {}).get("possession", "–"),
            "home_shots":           stats.get("home", {}).get("shots_total", "–"),
            "away_shots":           stats.get("away", {}).get("shots_total", "–"),
            "home_shots_on_target": stats.get("home", {}).get("shots_on_target", "–"),
            "away_shots_on_target": stats.get("away", {}).get("shots_on_target", "–"),
            "home_xg":              stats.get("home", {}).get("xg", "–"),
            "away_xg":              stats.get("away", {}).get("xg", "–"),
            "home_dangerous":       stats.get("home", {}).get("dangerous_attacks", "–"),
            "away_dangerous":       stats.get("away", {}).get("dangerous_attacks", "–"),
        },

        "events":   match_events,
        "ui_state": ui_state,

        # Phase 2 — pre-match editorial
        "prematch": {
            "available":       pm.get("available", False),
            "home_form":       pm_data.get("home_form", ""),
            "away_form":       pm_data.get("away_form", ""),
            "home_form_bg":    pm_data.get("home_form_bg", ""),
            "away_form_bg":    pm_data.get("away_form_bg", ""),
            "home_stats":      pm_data.get("home_stats", {}),
            "away_stats":      pm_data.get("away_stats", {}),
            "home_standing":   pm_data.get("home_standing", {}),
            "away_standing":   pm_data.get("away_standing", {}),
            "home_advantages": pm_data.get("home_advantages", []),
            "away_advantages": pm_data.get("away_advantages", []),
            "key_factors":     pm_data.get("key_factors", []),
            "h2h":             pm_data.get("h2h", []),
            "h2h_raw":         pm_data.get("h2h_raw", []),
            "h2h_home_wins":   pm_data.get("h2h_home_wins", 0),
            "h2h_away_wins":   pm_data.get("h2h_away_wins", 0),
            "h2h_count":       pm_data.get("h2h_count", 0),
            "avg_h2h_goals":   pm_data.get("avg_h2h_goals", 0),
            "h2h_latest_scorers": pm_data.get("h2h_latest_scorers", ""),
            "standings_reliable": pm_data.get("standings_reliable", False),
            "group_scenarios": pm_data.get("group_scenarios", {}),
            "gpt_narrative":   pm_data.get("gpt_narrative", ""),
            "top_scorers":     pm_data.get("top_scorers", {"home": [], "away": []}),
            "coaches":         pm_data.get("coaches", {}),
            "referee":         pm_data.get("referee", {}),
            "injuries":        pm_data.get("injuries", {"home": [], "away": []}),
            "fingerprint":     pm_data.get("fingerprint", ""),
            "analyzed_at":     pm_data.get("analyzed_at", ""),
            "broadcast_guide_draft": pm_data.get("broadcast_guide_draft", ""),
            "gpt_guide_pending":   pm_data.get("gpt_guide_pending", False),
            "stream_facts":        pm_data.get("stream_facts", []),
            "stream_facts_gpt_pending": pm_data.get("stream_facts_gpt_pending", False),
        },

        "table_impact":      table_impact,
        "postmatch_summary": postmatch_summary,
        "postmatch_gpt_pending": postmatch_gpt_pending,
        "commentary_queue":  [],   # filled by loop

        # Phase 2 — live narrative (Bulgarian, updated by key moments)
        "live_narrative": live_narrative,
        "halftime_analysis": halftime_analysis,

        "broadcast_package": broadcast_package,
        "lineups_detail":    lineups_detail,
        "recent_subs":       recent_subs,

        "meta": {
            "mode":          "AI_MATCH_INTELLIGENCE_V3",
            "real_data":     real_available,
            "generated_at":  datetime.now(timezone.utc).isoformat(),
            "health": _build_health_meta(live_fixture, match),
        },
    }


def _build_health_meta(live_fixture: dict, match: dict) -> dict:
    """Health for overlay — based on current fixture fetch, not stale global cache."""
    now_iso = datetime.now(timezone.utc).isoformat()
    api_ok = bool(
        live_fixture
        and (live_fixture.get("status_short") or live_fixture.get("status_long"))
    )
    return {
        "last_poll_at":      latest_health.get("last_poll_at") or now_iso,
        "poll_interval_sec": POLL_INTERVAL,
        "api_ok":            api_ok,
        "last_error":        latest_health.get("last_error", "") if not api_ok else "",
        "status_short":      live_fixture.get("status_short") or match.get("status_short", ""),
        "status_long":       live_fixture.get("status_long") or match.get("status", ""),
    }


# =====================================================
# PHASE 1 — BACKGROUND LIVE STATS LOOP
# =====================================================

POLL_INTERVAL = 20  # seconds — paid plan: up from 45s


async def live_stats_loop():
    """
    Polls API-Football every 45s for the active match,
    builds full Phase 1 intelligence, and pushes to all clients.
    """
    global _last_ft_status, latest_postmatch, latest_postmatch_gpt_pending, _last_ht_status, latest_halftime, latest_narrative, latest_commentary, _last_goal_count, latest_health
    print("[LOOP] Live stats loop started")

    while True:
        await asyncio.sleep(POLL_INTERVAL)

        if not active_match:
            continue

        fixture_id = active_match.get("raw_id")
        if not fixture_id:
            continue

        try:
            loop_ref     = asyncio.get_running_loop()
            live_fixture = await loop_ref.run_in_executor(
                None, stats_collector.get_live_fixture, fixture_id
            )
            sync_match_from_live_fixture(active_match, live_fixture or {})
            match_phase = get_match_phase(active_match)

            # For finished matches, stop re-broadcasting once postmatch is ready (GPT runs in background task)
            if match_phase == "finished" and latest_postmatch and not latest_postmatch_gpt_pending:
                continue

            # For prematch — periodic refresh so standings/injuries/referee stay current
            if match_phase == "prematch":
                now_ts = time.time()
                last_pm = _last_prematch_refresh.get(fixture_id, 0)
                if now_ts - last_pm >= PREMATCH_REFRESH_INTERVAL:
                    _last_prematch_refresh[fixture_id] = now_ts
                    prematch_engine._cache.pop(fixture_id, None)
                    pm_result = await loop_ref.run_in_executor(
                        None, prematch_engine.analyze_fast, fixture_id, active_match,
                    )
                    if pm_result.get("available"):
                        old_fp = prematch_engine.compute_data_fingerprint(
                            latest_prematch.get("data", {}) if latest_prematch.get("available") else {}
                        )
                        latest_prematch = pm_result
                        new_fp = prematch_engine.compute_data_fingerprint(
                            pm_result.get("data", {})
                        )
                        if old_fp != new_fp:
                            print(f"[PREMATCH] Background refresh — data changed ({old_fp} → {new_fp})")
                elif latest_prematch.get("available"):
                    await asyncio.sleep(40)  # extra delay when prematch stable

            minute = int(live_fixture.get("minute") or 0) if live_fixture else 0
            print(
                f"[LOOP] tick phase={match_phase} fixture={fixture_id} "
                f"min={minute} status={active_match.get('status_short') or active_match.get('status')}"
            )
            intelligence = {}

            if match_phase == "live":
                intelligence = build_live_intelligence(active_match, minute)
                latest_intelligence.update(intelligence)

                # Live narrative after key moments
                if intelligence.get("available") and intelligence.get("key_moments"):
                    loop = asyncio.get_running_loop()
                    narrative = await loop.run_in_executor(
                        None,
                        prematch_engine.generate_live_narrative,
                        fixture_id,
                        active_match.get("home", ""),
                        active_match.get("away", ""),
                        minute,
                        intelligence.get("momentum", {}),
                        intelligence.get("key_moments", []),
                    )
                    if narrative:
                        latest_narrative = narrative

            # Post-match summary — instant on first finished tick, GPT async
            if match_phase == "finished":
                if not latest_postmatch or (
                    _gpt_available
                    and not prematch_engine.get_cached_postmatch(fixture_id)
                    and fixture_id not in _postmatch_gpt_spawned
                ):
                    await _ensure_postmatch(active_match)
                _last_ft_status = "FT"
            elif match_phase != "finished":
                _last_ft_status = match_phase
                latest_postmatch_gpt_pending = False

            # Half-time analysis trigger
            if is_halftime(active_match) and _last_ht_status != "HT":
                _last_ht_status = "HT"
                live_stats_snap = latest_intelligence.get("stats", {})
                events_snap     = stats_collector._events_cache.get(fixture_id, {}).get("data", [])
                lf = stats_collector.get_live_fixture(fixture_id)
                score_h = lf.get("home_goals", 0) or 0
                score_a = lf.get("away_goals", 0) or 0
                loop = asyncio.get_running_loop()
                ht_text = await loop.run_in_executor(
                    None,
                    prematch_engine.generate_halftime_analysis,
                    fixture_id,
                    active_match.get("home", ""),
                    active_match.get("away", ""),
                    live_stats_snap,
                    events_snap,
                    score_h, score_a,
                )
                if ht_text:
                    latest_halftime = ht_text
                    print(f"[HALFTIME] Analysis ready for {active_match.get('home')} vs {active_match.get('away')}")
            elif not is_halftime(active_match):
                _last_ht_status = active_match.get("status_short", "")

            # Commentary queue — refresh every 5 min or on goal
            if match_phase == "live" and intelligence.get("available"):
                events_snap    = stats_collector._events_cache.get(fixture_id, {}).get("data", [])
                current_goals  = len([e for e in events_snap if e.get("type") == "Goal"])
                goal_happened  = current_goals > _last_goal_count
                if goal_happened:
                    _last_goal_count = current_goals
                lf_now     = stats_collector.get_live_fixture(fixture_id)
                score_h_c  = lf_now.get("home_goals", 0) or 0
                score_a_c  = lf_now.get("away_goals", 0) or 0
                loop       = asyncio.get_running_loop()
                new_points = await loop.run_in_executor(
                    None,
                    prematch_engine.generate_commentary_queue,
                    fixture_id,
                    active_match.get("home", ""),
                    active_match.get("away", ""),
                    minute,
                    score_h_c,
                    score_a_c,
                    intelligence.get("stats", {}),
                    events_snap,
                    intelligence.get("momentum", {}),
                    goal_happened,   # force=True when goal scored
                )
                if new_points:
                    latest_commentary = new_points

            payload = build_overlay_response(
                active_match,
                intelligence=intelligence,
                prematch=latest_prematch,
                live_narrative=latest_narrative,
                halftime_analysis=latest_halftime,
                postmatch_summary=latest_postmatch,
                postmatch_gpt_pending=latest_postmatch_gpt_pending,
            )
            payload["commentary_queue"] = latest_commentary
            latest_health = {
                "last_poll_at": datetime.now(timezone.utc).isoformat(),
                "last_error":   "",
                "api_ok":       bool(
                    live_fixture
                    and (live_fixture.get("status_short") or live_fixture.get("status_long"))
                ),
            }
            await broadcast(payload)
            print(f"[LOOP] broadcast done — real={intelligence.get('available', False)}")

        except Exception as e:
            latest_health = {
                "last_poll_at": datetime.now(timezone.utc).isoformat(),
                "last_error":   str(e),
                "api_ok":       False,
            }
            print(f"[LOOP] Error: {e}")


# =====================================================
# DEBUG ENDPOINT
# =====================================================

@app.get("/debug/prematch/{fixture_id}")
def debug_prematch(fixture_id: int):
    """Force-run pre-match analysis and return full result including GPT narrative."""
    # Clear cache to force fresh GPT call
    prematch_engine._cache.pop(fixture_id, None)
    prematch_engine._gpt_ts.pop(fixture_id, None)

    match = {"raw_id": fixture_id}
    result = prematch_engine.analyze(fixture_id, match)

    data = result.get("data", {})
    return {
        "fixture_id":    fixture_id,
        "available":     result.get("available"),
        "gpt_narrative": data.get("gpt_narrative", ""),
        "gpt_available": bool(data.get("gpt_narrative")),
        "home":          data.get("home"),
        "away":          data.get("away"),
        "home_form":     data.get("home_form"),
        "away_form":     data.get("away_form"),
        "h2h_count":     len(data.get("h2h", [])),
    }


@app.get("/debug/stats/{fixture_id}")
def debug_stats(fixture_id: int):
    """
    Direct API-Football probe — shows raw stats, lineups, events for a fixture.
    Use to verify what the API actually returns.
    """
    raw_stats   = stats_collector._get("fixtures/statistics", {"fixture": fixture_id})
    raw_lineups = stats_collector._get("fixtures/lineups",    {"fixture": fixture_id})
    raw_events  = stats_collector._get("fixtures/events",     {"fixture": fixture_id})

    processed = stats_collector.get_live_stats(fixture_id)

    return {
        "fixture_id":         fixture_id,
        "raw_stats_teams":    len(raw_stats),
        "raw_lineups_teams":  len(raw_lineups),
        "raw_events_count":   len(raw_events),
        "processed_home":     processed.get("home"),
        "processed_away":     processed.get("away"),
        "home_team":          processed.get("home_team"),
        "away_team":          processed.get("away_team"),
        "has_api_response":   bool(processed.get("home_team") or processed.get("away_team")),
        "lineups_sample":     raw_lineups[:2] if raw_lineups else [],
        "events_sample":      raw_events[:3] if raw_events else [],
        "stats_sample":       raw_stats[:1] if raw_stats else [],
    }



# =====================================================
# HOME
# =====================================================

@app.get("/")
def home(request: Request):
    accept = (request.headers.get("accept") or "").lower()
    if "text/html" in accept and "application/json" not in accept:
        return RedirectResponse(url="/overlay", status_code=307)
    return {
        "status":  "ONLINE",
        "mode":    "AI_MATCH_INTELLIGENCE_V3",
        "version": "3.0.0",
        "overlay": "/overlay",
        "phase1": {
            "xg_momentum_engine":   True,
            "tactical_engine":      True,
            "key_moments_detector": True,
        }
    }


@app.get("/overlays")
@app.get("/overlay/")
def overlay_aliases():
    """Common typos / trailing slash → canonical overlay URL."""
    return RedirectResponse(url="/overlay", status_code=307)


# =====================================================
# OVERLAY UI
# =====================================================

@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


@app.get("/overlay", response_class=HTMLResponse)
def overlay(request: Request):
    return HTMLResponse(
        jinja_env.get_template("overlay.html").render(request=request)
    )


@app.get("/overlay/commentator", response_class=HTMLResponse)
def commentator_view(request: Request):
    """Teleprompter-style view for live commentators."""
    return HTMLResponse(
        jinja_env.get_template("commentator.html").render(request=request)
    )


@app.get("/overlay/prep", response_class=HTMLResponse)
def prep_view(request: Request):
    """Stream prep kit — research upcoming matches without going live."""
    return HTMLResponse(
        jinja_env.get_template("prep.html").render(request=request)
    )


# =====================================================
# STREAM PREP KIT API
# =====================================================

def _match_dict_from_prematch(fixture_id: int, result: dict) -> dict:
    data = result.get("data") or {}
    meta = result.get("meta") or {}
    return {
        "raw_id":      fixture_id,
        "home":        data.get("home") or meta.get("home_name", ""),
        "away":        data.get("away") or meta.get("away_name", ""),
        "competition": data.get("league") or meta.get("league_name", ""),
        "status":      "Not Started",
        "status_short": "NS",
        "start_time":  meta.get("date") or data.get("date", ""),
    }


def _prep_needs_gpt(data: dict) -> bool:
    if not data:
        return False
    return bool(data.get("prep_editorial_pending") and not data.get("prep_editorial"))


def _overlay_gpt_needs_warm(data: dict) -> bool:
    if not data:
        return False
    if data.get("gpt_guide_pending") and not data.get("gpt_narrative"):
        return True
    return False


async def _run_overlay_gpt_warm_async(fixture_id: int):
    """Generate broadcast guide in background after prep — does not block prep UI."""
    global _overlay_gpt_warm_spawned
    if fixture_id in _overlay_gpt_warm_spawned:
        return
    _overlay_gpt_warm_spawned.add(fixture_id)
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, prematch_engine.generate_overlay_gpt_phase, fixture_id,
        )
        print(f"[PREP] Overlay GPT warm done for fixture {fixture_id}")
    except Exception as e:
        print(f"[PREP] Overlay GPT warm error: {e}")
    finally:
        _overlay_gpt_warm_spawned.discard(fixture_id)


async def _run_prep_gpt_async(fixture_id: int):
    """Generate prep editorial (+ facts) — fast path, no broadcast guide."""
    global latest_prematch, _prep_gpt_spawned
    print(f"[PREP] GPT task started for fixture {fixture_id}")
    try:
        loop = asyncio.get_running_loop()
        gpt_result = await loop.run_in_executor(
            None, prematch_engine.generate_prep_gpt_phase, fixture_id,
        )
        if gpt_result and active_match and active_match.get("raw_id") == fixture_id:
            latest_prematch = gpt_result
            payload = build_overlay_response(
                active_match,
                intelligence=latest_intelligence or {},
                prematch=latest_prematch,
                live_narrative=latest_narrative,
                halftime_analysis=latest_halftime,
                postmatch_summary=latest_postmatch,
                postmatch_gpt_pending=latest_postmatch_gpt_pending,
            )
            payload["commentary_queue"] = latest_commentary
            await broadcast(payload)
            print(f"[PREP] GPT done — also broadcast to active overlay for {fixture_id}")
        else:
            print(f"[PREP] GPT done for fixture {fixture_id}")

        cached = prematch_engine.get_cached_prematch(fixture_id)
        inner = (cached or {}).get("data") or (gpt_result or {}).get("data") or {}
        if _overlay_gpt_needs_warm(inner):
            asyncio.create_task(_run_overlay_gpt_warm_async(fixture_id))
    except Exception as e:
        import traceback
        print(f"[PREP] GPT error: {e}")
        traceback.print_exc()
    finally:
        _prep_gpt_spawned.discard(fixture_id)
        _prep_gpt_started.pop(fixture_id, None)


def _spawn_prep_gpt_if_needed(fixture_id: int, data: dict) -> None:
    if not _prep_needs_gpt(data):
        return
    now = time.time()
    if fixture_id in _prep_gpt_spawned:
        started = _prep_gpt_started.get(fixture_id, now)
        if now - started < PREP_GPT_STALE_SEC:
            return
        print(f"[PREP] Stale GPT task for fixture {fixture_id} — re-spawning")
        _prep_gpt_spawned.discard(fixture_id)
    _prep_gpt_spawned.add(fixture_id)
    _prep_gpt_started[fixture_id] = now
    asyncio.create_task(_run_prep_gpt_async(fixture_id))


@app.get("/prep/{fixture_id}")
async def get_prep_kit(fixture_id: int, refresh: bool = False):
    """
    Load stream prep kit for a fixture without selecting it as the active live match.
    Triggers background GPT when guide/facts are not yet polished.
    """
    if refresh:
        prematch_engine._cache.pop(fixture_id, None)
        _prep_gpt_spawned.discard(fixture_id)
        _prep_gpt_started.pop(fixture_id, None)

    match_stub = {"raw_id": fixture_id}
    loop = asyncio.get_running_loop()

    cached = prematch_engine.get_cached_prematch(fixture_id)
    if cached and cached.get("available") and not refresh:
        result = cached
    else:
        result = await loop.run_in_executor(
            None, prematch_engine.analyze_fast, fixture_id, match_stub,
        )

    if not result.get("available"):
        return {"ok": False, "error": "Анализът не е наличен за този мач"}

    data = result.get("data") or {}
    _spawn_prep_gpt_if_needed(fixture_id, data)

    return {
        "ok":    True,
        "match": _match_dict_from_prematch(fixture_id, result),
        "kit":   build_prep_kit(result),
    }


# =====================================================
# OVERLAY DATA
# =====================================================

@app.get("/overlay-data")
def overlay_data(date: str = None):
    fixtures = get_fixtures(date or "today")
    featured = match_selector.get_top_matches(fixtures, limit=5)
    return {
        "success":      True,
        "matches":      fixtures,
        "featured":     featured,
        "active_match": active_match,
        "date":         date or "today",
    }


@app.get("/featured")
def featured_matches(date: str = None, limit: int = 5):
    """Top broadcast-priority matches for the selected day."""
    fixtures = get_fixtures(date or "today")
    limit = max(1, min(limit, 10))
    return {
        "success":  True,
        "featured": match_selector.get_top_matches(fixtures, limit=limit),
        "date":     date or "today",
    }


_upcoming_cache: dict = {"ts": 0, "data": None}
UPCOMING_CACHE_TTL = 600  # 10 минути — paid plan: up from 30 min


@app.get("/upcoming")
def upcoming_matches(days: int = 7, refresh: bool = False):
    """
    Returns Not Started matches for the next N days (default 7).
    Cached for 30 minutes to preserve API quota.
    Add ?refresh=true to force reload.
    """
    import time as _time
    from datetime import datetime, timedelta

    now_ts = _time.time()

    # Return cached if fresh
    if not refresh and _upcoming_cache["data"] and (now_ts - _upcoming_cache["ts"]) < UPCOMING_CACHE_TTL:
        cached = _upcoming_cache["data"]
        print(f"[UPCOMING] Cache hit — {cached['total']} matches, age {int(now_ts - _upcoming_cache['ts'])}s")
        return cached

    days        = max(1, min(days, 7))
    ns_statuses = {"Not Started", "Time To Be Defined", "Scheduled"}
    now         = datetime.now()

    all_upcoming: list = []
    by_date:      dict = {}

    for i in range(days):
        date_str = (now + timedelta(days=i)).strftime("%Y-%m-%d")
        fixtures = get_fixtures(date_str)

        ns_today = [m for m in fixtures if m.get("status") in ns_statuses]
        if ns_today:
            by_date[date_str] = sorted(ns_today, key=lambda m: m.get("start_time") or "")
            all_upcoming.extend(ns_today)

    competitions = sorted({m["competition"] for m in all_upcoming if m.get("competition")})

    result = {
        "success":      True,
        "upcoming":     all_upcoming,
        "by_date":      by_date,
        "competitions": competitions,
        "total":        len(all_upcoming),
        "days_loaded":  days,
        "cached_at":    datetime.now().strftime("%H:%M"),
    }

    _upcoming_cache["ts"]   = now_ts
    _upcoming_cache["data"] = result
    print(f"[UPCOMING] Fetched {len(all_upcoming)} upcoming matches over {days} days")
    return result


@app.get("/prematch-check/{fixture_id}")
def prematch_check(fixture_id: int, fp: str = ""):
    """Compare client fingerprint with server; refresh server cache if older than 30 min."""
    cached = prematch_engine.get_cached_prematch(fixture_id)
    cached_data = cached.get("data", {}) if cached.get("available") else {}
    server_fp = prematch_engine.compute_data_fingerprint(cached_data)
    analyzed_at = cached_data.get("analyzed_at", "")

    cache_entry = prematch_engine._cache.get(fixture_id, {})
    cache_age = time.time() - cache_entry.get("ts", 0) if cache_entry else 9999

    stale = bool(fp and server_fp and fp != server_fp)

    if cache_age >= PREMATCH_REFRESH_INTERVAL:
        prematch_engine._cache.pop(fixture_id, None)
        fresh = prematch_engine.analyze_fast(fixture_id, {"raw_id": fixture_id})
        fresh_data = fresh.get("data", {}) if fresh.get("available") else {}
        new_fp = prematch_engine.compute_data_fingerprint(fresh_data)
        if fp and new_fp and new_fp != fp:
            stale = True
        if new_fp:
            server_fp = new_fp
        analyzed_at = fresh_data.get("analyzed_at", analyzed_at)

    return {
        "fixture_id":  fixture_id,
        "fingerprint": server_fp,
        "stale":       stale,
        "analyzed_at": analyzed_at,
    }


@app.post("/refresh-prematch/{fixture_id}")
async def refresh_prematch(fixture_id: int):
    """Force fresh prematch analysis and push to connected clients."""
    global latest_prematch

    prematch_engine._cache.pop(fixture_id, None)
    _last_prematch_refresh[fixture_id] = time.time()

    match = active_match if active_match and active_match.get("raw_id") == fixture_id else {"raw_id": fixture_id}
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, prematch_engine.analyze_fast, fixture_id, match)

    if active_match and active_match.get("raw_id") == fixture_id and result.get("available"):
        latest_prematch = result
        payload = build_overlay_response(
            active_match,
            intelligence=latest_intelligence or {},
            prematch=latest_prematch,
            live_narrative=latest_narrative,
            halftime_analysis=latest_halftime,
            postmatch_summary=latest_postmatch,
            postmatch_gpt_pending=latest_postmatch_gpt_pending,
        )
        payload["commentary_queue"] = latest_commentary
        await broadcast(payload)

        if not result.get("data", {}).get("gpt_narrative"):
            asyncio.create_task(_load_prematch_async(fixture_id, active_match))

    data = result.get("data", {}) if result.get("available") else {}
    return {
        "ok":          True,
        "fingerprint": data.get("fingerprint", ""),
        "analyzed_at": data.get("analyzed_at", ""),
    }


# =====================================================
# SELECT MATCH
# =====================================================

@app.post("/select-match")
async def select_match(request: Request):
    global active_match, latest_intelligence, latest_prematch, latest_narrative

    data = await request.json()
    match = data.get("match")

    global latest_halftime, latest_postmatch, latest_postmatch_gpt_pending, latest_commentary, _last_goal_count, _last_ht_status, _last_ft_status, _postmatch_gpt_spawned

    old_fid = active_match.get("raw_id") if active_match else None
    new_fid = match.get("raw_id") if match else None
    same_match = bool(new_fid and new_fid == old_fid)

    active_match = match

    if not same_match:
        latest_intelligence = {}
        latest_narrative    = ""
        latest_halftime     = ""
        latest_postmatch    = ""
        latest_postmatch_gpt_pending = False
        latest_commentary   = []
        _last_goal_count    = 0
        _last_ht_status     = ""
        _last_ft_status     = ""
        if old_fid:
            _postmatch_gpt_spawned.discard(old_fid)
        latest_prematch     = {}
        if old_fid:
            tactical_engine._last_call_ts.pop(old_fid, None)

    if active_match:
        fixture_id  = active_match.get("raw_id")
        cached_pm   = None

        if fixture_id:
            cached_pm = prematch_engine.get_cached_prematch(fixture_id)

        if get_match_phase(active_match) == "finished":
            await _ensure_postmatch(active_match)

        if same_match and latest_prematch.get("available"):
            cached_pm = latest_prematch
        elif cached_pm and cached_pm.get("available"):
            latest_prematch = cached_pm

        if cached_pm and cached_pm.get("available"):
            payload = build_overlay_response(
                active_match,
                intelligence=latest_intelligence or {},
                prematch=latest_prematch,
                live_narrative=latest_narrative,
                halftime_analysis=latest_halftime,
                postmatch_summary=latest_postmatch,
                postmatch_gpt_pending=latest_postmatch_gpt_pending,
            )
            payload["commentary_queue"] = latest_commentary
            await broadcast(payload)

            pm_data = cached_pm.get("data", {})
            if pm_data.get("gpt_narrative"):
                return {"success": True, "active_match": active_match, "cached": True}

        else:
            loading_payload = build_overlay_response(
                active_match,
                prematch={"available": False},
                postmatch_summary=latest_postmatch,
                postmatch_gpt_pending=latest_postmatch_gpt_pending,
            )
            loading_payload["halftime_analysis"] = latest_halftime
            loading_payload["commentary_queue"]  = latest_commentary
            await broadcast(loading_payload)

        if fixture_id and not (cached_pm and cached_pm.get("data", {}).get("gpt_narrative")):
            asyncio.create_task(_load_prematch_async(fixture_id, active_match))

    return {
        "success":      True,
        "active_match": active_match,
    }


async def _load_prematch_async(fixture_id: int, match: dict):
    """
    Async task: runs prematch analysis in two phases for fast UI response.
    Phase 1: rule-based data (fast ~2-3s) → broadcast immediately
    Phase 2: GPT narrative (slow ~15-30s) → broadcast update when ready
    """
    global latest_prematch
    if active_match and active_match.get("raw_id") != fixture_id:
        return

    print(f"[BG] Prematch task started for fixture {fixture_id}")
    try:
        loop = asyncio.get_running_loop()

        cached = prematch_engine.get_cached_prematch(fixture_id)
        if cached and cached.get("available") and cached.get("data", {}).get("gpt_narrative"):
            latest_prematch = cached
            print(f"[BG] Prematch cache hit (with GPT) for fixture {fixture_id}")
            payload = build_overlay_response(
                match,
                intelligence=latest_intelligence or {},
                prematch=latest_prematch,
                live_narrative=latest_narrative,
                halftime_analysis=latest_halftime,
                postmatch_summary=latest_postmatch,
                postmatch_gpt_pending=latest_postmatch_gpt_pending,
            )
            payload["commentary_queue"] = latest_commentary
            await broadcast(payload)
            return

        result = await loop.run_in_executor(
            None, prematch_engine.analyze_fast, fixture_id, match
        )
        if active_match and active_match.get("raw_id") != fixture_id:
            return

        latest_prematch = result
        print(f"[BG] Phase 1 (rule-based) complete for fixture {fixture_id} — broadcasting")

        payload = build_overlay_response(
            match,
            intelligence=latest_intelligence or {},
            prematch=latest_prematch,
            live_narrative=latest_narrative,
            halftime_analysis=latest_halftime,
            postmatch_summary=latest_postmatch,
            postmatch_gpt_pending=latest_postmatch_gpt_pending,
        )
        payload["commentary_queue"] = latest_commentary
        await broadcast(payload)

        if result.get("data", {}).get("gpt_narrative"):
            print(f"[BG] GPT narrative restored from cache for fixture {fixture_id}")
            return

        gpt_result = await loop.run_in_executor(
            None, prematch_engine.generate_overlay_gpt_phase, fixture_id
        )
        if active_match and active_match.get("raw_id") != fixture_id:
            return

        if gpt_result:
            latest_prematch = gpt_result
            print(f"[BG] Phase 2 (GPT) complete for fixture {fixture_id} — broadcasting update")
            payload = build_overlay_response(
                match,
                intelligence=latest_intelligence or {},
                prematch=latest_prematch,
                live_narrative=latest_narrative,
                halftime_analysis=latest_halftime,
                postmatch_summary=latest_postmatch,
                postmatch_gpt_pending=latest_postmatch_gpt_pending,
            )
            payload["commentary_queue"] = latest_commentary
            await broadcast(payload)

    except Exception as e:
        import traceback
        print(f"[BG] ERROR in prematch task: {e}")
        traceback.print_exc()


async def _load_postmatch_gpt_async(
    fixture_id: int,
    match: dict,
    score_h: int,
    score_a: int,
    live_stats: dict,
    events: list,
    prematch_data: dict,
):
    """Phase 2: GPT post-match summary in background; broadcast when ready."""
    global latest_postmatch, latest_postmatch_gpt_pending

    if active_match and active_match.get("raw_id") != fixture_id:
        return

    print(f"[BG] Postmatch GPT task started for fixture {fixture_id}")
    try:
        loop = asyncio.get_running_loop()
        ft_text = await loop.run_in_executor(
            None,
            prematch_engine.generate_postmatch_summary,
            fixture_id,
            match.get("home", ""),
            match.get("away", ""),
            score_h,
            score_a,
            live_stats,
            events,
            prematch_data,
        )
        if active_match and active_match.get("raw_id") != fixture_id:
            return

        latest_postmatch_gpt_pending = False
        if ft_text:
            latest_postmatch = ft_text
        print(f"[BG] Postmatch GPT complete for fixture {fixture_id} — broadcasting update")
        payload = build_overlay_response(
            match,
            intelligence=latest_intelligence or {},
            prematch=latest_prematch,
            live_narrative=latest_narrative,
            halftime_analysis=latest_halftime,
            postmatch_summary=latest_postmatch,
            postmatch_gpt_pending=False,
        )
        payload["commentary_queue"] = latest_commentary
        await broadcast(payload)
    except Exception as e:
        latest_postmatch_gpt_pending = False
        import traceback
        print(f"[BG] ERROR in postmatch GPT task: {e}")
        traceback.print_exc()
        if active_match and active_match.get("raw_id") == fixture_id and latest_postmatch:
            payload = build_overlay_response(
                match,
                intelligence=latest_intelligence or {},
                prematch=latest_prematch,
                live_narrative=latest_narrative,
                halftime_analysis=latest_halftime,
                postmatch_summary=latest_postmatch,
                postmatch_gpt_pending=False,
            )
            payload["commentary_queue"] = latest_commentary
            await broadcast(payload)


# =====================================================
# WEBSOCKET
# =====================================================

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    print("[WS] CLIENT CONNECTED")

    try:
        if active_match:
            # Send full current state to newly connected client
            init_payload = build_overlay_response(
                active_match,
                intelligence=latest_intelligence or {},
                prematch=latest_prematch,
                live_narrative=latest_narrative,
                halftime_analysis=latest_halftime,
                postmatch_summary=latest_postmatch,
                postmatch_gpt_pending=latest_postmatch_gpt_pending,
            )
            init_payload["commentary_queue"] = latest_commentary
            await websocket.send_json(init_payload)
        else:
            await websocket.send_json({"type": "WAITING_MATCH"})

        # Keep connection alive — updates come from live_stats_loop()
        while True:
            await asyncio.sleep(30)

    except WebSocketDisconnect:
        clients.discard(websocket)
        print("[WS] CLIENT DISCONNECTED")
    except Exception as e:
        clients.discard(websocket)
        print("[WS ERROR]", str(e))
