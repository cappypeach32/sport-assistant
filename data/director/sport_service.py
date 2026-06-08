import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_FOOTBALL_KEY")

BASE_URL = "https://v3.football.api-sports.io"

HEADERS = {
    "x-apisports-key": API_KEY
}

# In-memory cache: date_str → {ts, fixtures}
_fixtures_cache: dict = {}
FIXTURES_CACHE_TTL = 60  # 1 минута — paid plan: up from 2 min

# ─── League whitelist ────────────────────────────────────────────────────────
# Only show matches from these competitions.
# Set to None or empty set to show ALL leagues.
ALLOWED_LEAGUE_IDS = {
    # ── Top 5 European leagues ──────────────────
    39,   # Premier League (England)
    140,  # La Liga (Spain)
    135,  # Serie A (Italy)
    78,   # Bundesliga (Germany)
    61,   # Ligue 1 (France)
    # ── UEFA club competitions ──────────────────
    2,    # UEFA Champions League
    3,    # UEFA Europa League
    848,  # UEFA Europa Conference League
    # ── International ───────────────────────────
    1,    # FIFA World Cup
    4,    # UEFA Euro Championship
    9,    # Copa America
    10,   # Friendlies (International)
    # ── Other top leagues ───────────────────────
    88,   # Eredivisie (Netherlands)
    203,  # Süper Lig (Turkey)
    144,  # Jupiler Pro League (Belgium)
    # ── Bulgaria ────────────────────────────────
    172,  # First League (Bulgaria)
    174,  # Cup (Bulgaria)
}


# =====================================================
# FIX: REAL FIXTURES (WORKING APPROACH)
# =====================================================
def get_fixtures(date=None):

    from datetime import datetime

    if date == "today" or date is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    else:
        date_str = date

    # Return from cache if fresh
    cached = _fixtures_cache.get(date_str)
    if cached and (time.time() - cached["ts"]) < FIXTURES_CACHE_TTL:
        print(f"[API-FOOTBALL] Cache hit for {date_str} ({len(cached['fixtures'])} fixtures)")
        return cached["fixtures"]

    url = f"{BASE_URL}/fixtures"
    params = {"date": date_str}

    try:
        res = requests.get(url, headers=HEADERS, params=params, timeout=10)

        print(f"[API-FOOTBALL] {res.status_code} for date={date_str}")

        if res.status_code == 429:
            print("[API-FOOTBALL] Rate limit reached — returning cached data if available")
            return cached["fixtures"] if cached else []

        if res.status_code != 200:
            print("[API-FOOTBALL ERROR]", res.text[:200])
            return cached["fixtures"] if cached else []

        remaining = res.headers.get("x-ratelimit-requests-remaining", "?")
        print(f"[API-FOOTBALL] Quota remaining: {remaining}")

        data = res.json().get("response", [])
        fixtures = []

        for f in data:
            try:
                fixtures.append({
                    "home":           f["teams"]["home"]["name"],
                    "away":           f["teams"]["away"]["name"],
                    "competition":    f["league"]["name"],
                    "competition_id": f["league"]["id"],
                    "status":         f["fixture"]["status"]["long"],
                    "start_time":     f["fixture"]["date"],
                    "raw_id":         f["fixture"]["id"]
                })
            except Exception:
                continue

        # Apply league whitelist filter
        if ALLOWED_LEAGUE_IDS:
            all_count = len(fixtures)
            fixtures = [f for f in fixtures if f["competition_id"] in ALLOWED_LEAGUE_IDS]
            print(f"[API-FOOTBALL] {len(fixtures)} fixtures for {date_str} (filtered from {all_count}, whitelist active)")
        else:
            print(f"[API-FOOTBALL] {len(fixtures)} fixtures for {date_str}")

        _fixtures_cache[date_str] = {"ts": time.time(), "fixtures": fixtures}
        return fixtures

    except Exception as e:
        print("[API-FOOTBALL EXCEPTION]", str(e))
        return cached["fixtures"] if cached else []