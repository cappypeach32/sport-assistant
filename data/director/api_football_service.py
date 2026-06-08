import os
import requests

API_KEY = os.getenv("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"


HEADERS = {
    "x-apisports-key": API_KEY
}


def get_fixtures(date="today"):

    url = f"{BASE_URL}/fixtures"

    params = {
        "date": date
    }

    res = requests.get(url, headers=HEADERS, params=params)

    print("[API-FOOTBALL STATUS]", res.status_code)

    if res.status_code != 200:
        print("[API-FOOTBALL ERROR]", res.text)
        return []

    data = res.json().get("response", [])

    fixtures = []

    for f in data:

        try:
            fixtures.append({
                "home": f["teams"]["home"]["name"],
                "away": f["teams"]["away"]["name"],
                "competition": f["league"]["name"],
                "competition_id": f["league"]["id"],
                "status": f["fixture"]["status"]["long"],
                "start_time": f["fixture"]["date"],
                "raw_id": f["fixture"]["id"]
            })

        except:
            continue

    return fixtures