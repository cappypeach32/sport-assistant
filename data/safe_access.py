def safe_get(d, key, default=None):
    if not isinstance(d, dict):
        return default
    return d.get(key, default)


def safe_form(team):
    try:
        return team.get("form", {}).get("form", "N/A")
    except:
        return "N/A"