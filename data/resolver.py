import json
import os


CACHE_FILE = os.path.join(os.path.dirname(__file__), "teams.json")


def load_teams():
    if not os.path.exists(CACHE_FILE):
        return {}

    with open(CACHE_FILE, "r") as f:
        return json.load(f)


def normalize(text: str):
    return text.lower().strip()


def resolve_team(name: str):
    teams = load_teams()

    query = normalize(name)

    for key, value in teams.items():

        # 1. direct match
        if query == key:
            return value

        # 2. name match
        if query == normalize(value.get("name", "")):
            return value

        # 3. alias match
        for alias in value.get("aliases", []):
            if query == normalize(alias):
                return value

    return None