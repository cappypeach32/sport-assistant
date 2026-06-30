"""Stream prep kit — structured prematch payload for /overlay/prep."""

import re


PREP_SECTION_ICONS = {
    "ЗАГЛАВИЕ": "🏆",
    "УВОД": "📌",
    "ОБЩ КОНТЕКСТ": "🌍",
    "ВЕРОЯТНИ СХЕМИ": "📐",
    "КЛЮЧОВИ ИГРАЧИ": "⭐",
    "ТАКТИЧЕСКИ КЛЮЧОВЕ": "🎯",
    "ИСТОРИЯ МЕЖДУ ОТБОРИТЕ": "📜",
    "СИЛНИ СТРАНИ И СЛАБОСТИ": "⚖️",
    "КАК ОЧАКВАМ ДА ПРОТЕЧЕ МАЧЪТ": "⏱️",
    "АНАЛИЗ И ОЧАКВАН СЦЕНАРИЙ": "🔮",
}


def parse_prep_editorial(text: str) -> list[dict]:
    """Split ## sectioned prep editorial into UI blocks."""
    if not text or not text.strip():
        return []

    sections: list[dict] = []
    chunks = re.split(r"\n(?=## )", text.strip())
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"^## (.+?)(?:\n|$)", chunk)
        if not m:
            continue
        title = m.group(1).strip()
        body = chunk[m.end():].strip()
        key = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
        sections.append({
            "key": key,
            "title": title,
            "body": body,
            "icon": PREP_SECTION_ICONS.get(title.upper(), "📄"),
        })
    return sections


def _prep_headline(sections: list[dict], fallback: str) -> str:
    for sec in sections:
        if sec.get("title", "").upper() == "ЗАГЛАВИЕ" and sec.get("body"):
            return sec["body"].split("\n")[0].strip()
    return fallback

def _extract_talking_points(narrative: str) -> list[dict]:
    if not narrative:
        return []
    m = re.search(r"##\s*ГОВОРНИ[\s\S]*?(?=\n## |$)", narrative, re.IGNORECASE)
    if not m:
        return []
    points = []
    for line in m.group(0).split("\n"):
        hit = re.match(r"^\d+\.\s*\*?\*?(.+?)\*?\*?:\s*(.+)$", line.strip())
        if hit:
            points.append({
                "title": hit.group(1).replace("**", "").strip(),
                "text":  hit.group(2).strip(),
            })
    return points[:6]


def _extract_historical_facts(narrative: str) -> list[str]:
    if not narrative:
        return []
    m = re.search(r"##\s*ИСТОРИЧЕСКИ[\s\S]*?(?=\n## |$)", narrative, re.IGNORECASE)
    if not m:
        return []
    facts = []
    for line in m.group(0).split("\n"):
        line = line.strip()
        if line.startswith("##"):
            continue
        line = re.sub(r"^[-•*]\s*", "", line)
        line = re.sub(r"^\d+\.\s*", "", line)
        if line and not line.startswith("("):
            facts.append(line)
    return facts[:8]


def _form_summary(stats: dict, form_str: str) -> dict:
    if not stats and (not form_str or form_str == "—"):
        return {"available": False}
    return {
        "available": True,
        "form_str":  form_str or stats.get("form_str", "—"),
        "played":    stats.get("played", 0),
        "wins":      stats.get("wins", 0),
        "draws":     stats.get("draws", 0),
        "losses":    stats.get("losses", 0),
        "scored":    stats.get("scored", 0),
        "conceded":  stats.get("conceded", 0),
        "avg_score": stats.get("avg_score"),
        "avg_conc":  stats.get("avg_conc"),
        "source_label": stats.get("source_label", ""),
    }


def build_prep_kit(prematch_result: dict) -> dict:
    """Turn prematch engine result into a stream-prep kit for the UI."""
    if not prematch_result or not prematch_result.get("available"):
        return {"ready": False}

    data = prematch_result.get("data") or {}
    meta = prematch_result.get("meta") or {}
    home = data.get("home") or meta.get("home_name", "")
    away = data.get("away") or meta.get("away_name", "")

    guide = data.get("gpt_narrative") or data.get("broadcast_guide_draft") or ""
    prep_text = data.get("prep_editorial") or data.get("prep_editorial_draft") or ""
    prep_sections = parse_prep_editorial(prep_text)

    injuries = data.get("injuries") or {}
    inj_home = injuries.get("home") or []
    inj_away = injuries.get("away") or []

    return {
        "ready": True,
        "match_label": f"{home} срещу {away}",
        "headline": _prep_headline(prep_sections, f"{home} срещу {away} — детайлен анализ"),
        "competition": data.get("league") or meta.get("league_name", ""),
        "date": data.get("date") or (meta.get("date") or "")[:10],
        "analyzed_at": data.get("analyzed_at", ""),
        "fingerprint": data.get("fingerprint", ""),
        "loading": {
            "prep_editorial": bool(
                data.get("prep_editorial_pending") and not data.get("prep_editorial")
            ),
        },
        "prep_editorial_failed": bool(data.get("prep_editorial_gpt_failed")),
        "prep_editorial": {
            "text": prep_text,
            "sections": prep_sections,
            "is_gpt": bool(data.get("prep_editorial")),
            "is_draft": bool(data.get("prep_editorial_draft") and not data.get("prep_editorial")),
        },
        "stream_facts": data.get("stream_facts") or [],
        "form": {
            "home": _form_summary(data.get("home_stats") or {}, data.get("home_form", "")),
            "away": _form_summary(data.get("away_stats") or {}, data.get("away_form", "")),
        },
        "standings": {
            "home": data.get("home_standing") or {},
            "away": data.get("away_standing") or {},
            "reliable": data.get("standings_reliable", False),
        },
        "h2h": {
            "rows": data.get("h2h") or [],
            "count": data.get("h2h_count", 0),
            "home_wins": data.get("h2h_home_wins", 0),
            "away_wins": data.get("h2h_away_wins", 0),
            "avg_goals": data.get("avg_h2h_goals", 0),
            "latest_scorers": data.get("h2h_latest_scorers", ""),
        },
        "key_players": data.get("top_scorers") or {"home": [], "away": []},
        "key_factors": data.get("key_factors") or [],
        "advantages": {
            "home": data.get("home_advantages") or [],
            "away": data.get("away_advantages") or [],
        },
        "group_scenarios": data.get("group_scenarios") or {},
        "coaches": data.get("coaches") or {},
        "referee": data.get("referee") or {},
        "injuries": {
            "home": [{"name": i.get("name", "?"), "reason": i.get("reason", i.get("type", ""))} for i in inj_home],
            "away": [{"name": i.get("name", "?"), "reason": i.get("reason", i.get("type", ""))} for i in inj_away],
        },
        "talking_points": _extract_talking_points(guide),
        "historical_facts": _extract_historical_facts(guide),
        "broadcast_guide": guide,
        "has_gpt_guide": bool(data.get("gpt_narrative")),
    }
