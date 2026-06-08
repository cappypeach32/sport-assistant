from data.resolver import resolve_team
from data.football_data import get_team_matches


def get_team(team_name):
    return resolve_team(team_name)


def get_last_fixtures(team_id):
    return get_team_matches(team_id)