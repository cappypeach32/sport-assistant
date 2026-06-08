import requests

BASE_URL = "https://www.thesportsdb.com/api/v1/json/3"


def search_team(name):
    url = f"{BASE_URL}/searchteams.php?t={name}"
    return requests.get(url).json()


def get_last_events(team_name):
    url = f"{BASE_URL}/eventslast.php?id={team_name}"
    return requests.get(url).json()