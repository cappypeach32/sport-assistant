import requests
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("FOOTBALL_API_KEY")

BASE_URL = "https://api.football-data.org/v4"

headers = {
    "X-Auth-Token": API_KEY
}


def get_teams():
    url = f"{BASE_URL}/teams"
    return requests.get(url, headers=headers).json()


def get_team_matches(team_id):

    return [
        {"home_score": 2, "away_score": 1},
        {"home_score": 1, "away_score": 1},
        {"home_score": 0, "away_score": 3},
        {"home_score": 2, "away_score": 0},
        {"home_score": 1, "away_score": 2},
    ]


def get_team_by_id(team_id):
    url = f"{BASE_URL}/teams/{team_id}"
    return requests.get(url, headers=headers).json()