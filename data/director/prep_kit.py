"""Stream prep kit — structured prematch payload for /overlay/prep."""

import re

from data.director.prematch_engine import _gpt_available
from data.director.name_translit import (
    apply_name_map_to_text,
    build_player_name_map,
    latin_name_to_bg,
)

_stats_collector = None


def _get_stats_collector():
    global _stats_collector
    if _stats_collector is None:
        from data.director.live_stats_collector import LiveStatsCollector
        _stats_collector = LiveStatsCollector()
    return _stats_collector


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


_SKIP_LATIN_TOKENS = {
    "world", "cup", "group", "stage", "final", "league", "norway", "ivory",
    "coast", "africa", "europe", "fifa", "uefa", "team", "coach", "form",
}


def _enrich_player_names_in_text(text: str, name_map: dict[str, str]) -> str:
    if not text:
        return text
    text = apply_name_map_to_text(text, name_map)

    def _fix(m: re.Match) -> str:
        word = m.group(0)
        if word.lower() in _SKIP_LATIN_TOKENS or len(word) < 3:
            return word
        if word.isupper() and len(word) <= 4:
            return word
        return latin_name_to_bg(word)

    return re.sub(r"\b[A-ZÀ-ÖØ-Þ][a-zà-ÿ'\-]{1,}\b", _fix, text)


PLACEHOLDER_NAMES = frozenset({
    "име", "name", "играч", "player", "n/a", "na", "tbd", "?", "—", "-", "xxx",
})


def is_placeholder_name(name: str) -> bool:
    n = (name or "").strip().lower()
    if n in PLACEHOLDER_NAMES:
        return True
    if re.match(r"^\d+\s*ред", n):
        return True
    return False


def _extract_scheme(text: str) -> str:
    m = re.search(r"(?:вероятна\s+)?схема\s*:?\s*([\d]+(?:\s*[-/]\s*[\d]+)+)", text or "", re.I)
    return m.group(1).replace(" ", "") if m else ""


def _extract_team_subsection(body: str, team_label: str) -> str:
    if not body or not team_label:
        return ""
    label_l = team_label.lower().strip()
    for chunk in re.split(r"(?=^### )", body, flags=re.M):
        chunk = chunk.strip()
        if not chunk.startswith("### "):
            continue
        header = chunk.split("\n", 1)[0][4:].strip().lower()
        if header == label_l or label_l in header or header in label_l:
            return chunk.split("\n", 1)[1] if "\n" in chunk else ""
        # "Ivory Coast" vs "Кот д'Ивоar" — match first word
        if label_l.split()[0][:4] == header.split()[0][:4]:
            return chunk.split("\n", 1)[1] if "\n" in chunk else ""
    return ""


def _parse_formation_player_list(section_text: str, name_map: dict[str, str]) -> list[str]:
    players: list[str] = []
    if not section_text:
        return players

    in_tactics = False
    for line in section_text.split("\n"):
        t = line.strip()
        if not t:
            continue
        if re.match(r"^основна\s+тактическ", t, re.I):
            in_tactics = True
            continue
        if in_tactics:
            continue
        if re.search(r"(?:вероятна\s+)?схема\s*:", t, re.I):
            continue
        if re.match(r"^състав", t, re.I):
            continue
        if t.startswith("[") and t.endswith("]"):
            continue
        if re.match(r"^[•\-\*]", t):
            continue

        role_m = re.match(
            r"^(?:вратар|защитник|халф|нападател|полузащитник|def|mid|fwd|gk)\s*:\s*(.+)$",
            t,
            re.I,
        )
        if role_m:
            t = role_m.group(1).strip()

        name = re.sub(r"\*\*", "", t).strip()
        name = re.sub(r"\([^)]*\)", "", name).strip()
        if not name or is_placeholder_name(name):
            continue
        if name in name_map:
            players.append(name_map[name])
        elif name_map.get(name):
            players.append(name_map[name])
        else:
            players.append(latin_name_to_bg(name) if re.search(r"[a-zA-Z]", name) else name)
        if len(players) >= 11:
            break

    return players


def _resolve_player_bg(latin: str, name_map: dict[str, str]) -> str:
    latin = (latin or "").strip()
    if not latin:
        return ""
    if latin in name_map:
        return name_map[latin]
    surname = latin.split()[-1].lower().strip(".")
    for key, bg in name_map.items():
        if key.lower().split()[-1].strip(".") == surname:
            return bg
    for key, bg in name_map.items():
        if surname and surname in key.lower():
            return bg
    return latin_name_to_bg(latin)


def _scheme_lines(scheme: str) -> list[int]:
    m = re.match(r"([\d]+(?:-[\d]+)+)", (scheme or "").replace(" ", ""))
    if not m:
        return [1, 4, 3, 3]
    parts = [int(x) for x in m.group(1).split("-")]
    return [1] + parts


def _probable_xi_from_squad(
    squad: list[dict],
    scheme: str,
    priority_names: list[str],
    name_map: dict[str, str],
    priority_latin: list[str] | None = None,
) -> list[str]:
    if not squad:
        return []

    lines = _scheme_lines(scheme)
    outfield = lines[1:]
    if len(outfield) == 3:
        need_def, need_mid, need_att = outfield
    elif len(outfield) == 4:
        need_def, need_mid, need_att = outfield[0], outfield[1] + outfield[2], outfield[3]
    else:
        need_def = outfield[0] if outfield else 4
        need_mid = sum(outfield[1:-1]) if len(outfield) > 2 else 3
        need_att = outfield[-1] if len(outfield) > 1 else 3

    by_pos: dict[str, list[dict]] = {
        "Goalkeeper": [], "Defender": [], "Midfielder": [], "Attacker": [],
    }
    pri = {n.lower().strip() for n in priority_names if n}
    pri_surnames = {n.split()[-1] for n in pri if n}
    for latin in priority_latin or []:
        if latin:
            pri_surnames.add(latin.split()[-1].lower().strip("."))

    for p in squad:
        pos = p.get("position") or "Midfielder"
        if pos not in by_pos:
            pos = "Midfielder"
        latin = (p.get("name") or "").strip()
        if not latin:
            continue
        bg = _resolve_player_bg(latin, name_map)
        by_pos[pos].append({"latin": latin, "bg": bg})

    for pos in by_pos:
        by_pos[pos].sort(
            key=lambda x: (
                0 if x["bg"].lower() in pri or x["latin"].lower() in pri
                or x["latin"].split()[-1].lower().strip(".") in pri_surnames
                or x["bg"].split()[-1].lower() in pri_surnames else 1,
                x["bg"],
            )
        )

    picked: list[str] = []

    def _take(pos: str, n: int) -> None:
        count = 0
        for x in by_pos[pos]:
            if x["bg"] not in picked:
                picked.append(x["bg"])
                count += 1
                if count >= n:
                    break

    _take("Goalkeeper", 1)
    _take("Defender", need_def)
    _take("Midfielder", need_mid)
    _take("Attacker", need_att)

    for pos in ("Defender", "Midfielder", "Attacker", "Goalkeeper"):
        for x in by_pos[pos]:
            if x["bg"] not in picked and len(picked) < 11:
                picked.append(x["bg"])

    return picked[:11]


def _squad_from_lineups(side: str, lineups: dict | None, name_map: dict[str, str]) -> dict | None:
    side_data = (lineups or {}).get(side) or {}
    starting = side_data.get("starting") or []
    if len(starting) < 8:
        return None
    players = []
    for p in starting[:11]:
        latin = (p.get("name") or "").strip()
        if not latin:
            continue
        players.append(_resolve_player_bg(latin, name_map))
    if len(players) < 8:
        return None
    scheme = (side_data.get("formation") or "").replace(" ", "")
    return {"scheme": scheme, "players": players, "source": "api"}


def _names_from_key_players_section(prep_sections: list[dict], team_label: str, name_map: dict[str, str]) -> list[str]:
    body = ""
    for sec in prep_sections:
        if (sec.get("title") or "").upper() == "КЛЮЧОВИ ИГРАЧИ":
            body = sec.get("body") or ""
            break
    if not body:
        return []

    section = _extract_team_subsection(body, team_label)
    names: list[str] = []
    for line in section.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^[⭐\*]+\s*(.+?)(?:\s*[-–—:]|$)", line)
        if m:
            raw = re.sub(r"\*\*", "", m.group(1)).strip()
            if raw and not is_placeholder_name(raw):
                names.append(name_map.get(raw) or latin_name_to_bg(raw))
    return names


def build_formation_squads(
    data: dict,
    meta: dict,
    lineups: dict | None,
    prep_sections: list[dict],
    team_squads: dict | None = None,
) -> dict:
    """Best available XI per team: API lineups → GPT list → key players."""
    home = data.get("home") or meta.get("home_name", "")
    away = data.get("away") or meta.get("away_name", "")
    name_map = build_player_name_map(data)

    schemes_body = ""
    for sec in prep_sections:
        if (sec.get("title") or "").upper() == "ВЕРОЯТНИ СХЕМИ":
            schemes_body = sec.get("body") or ""
            break

    squads: dict = {}
    for side, label in (("home", home), ("away", away)):
        api = _squad_from_lineups(side, lineups, name_map)
        section_text = _extract_team_subsection(schemes_body, label)
        gpt_players = _parse_formation_player_list(section_text, name_map)
        gpt_scheme = _extract_scheme(section_text)
        default_scheme = "4-2-3-1" if side == "away" else "4-3-3"
        scheme = gpt_scheme or default_scheme

        real_gpt = [p for p in gpt_players if not is_placeholder_name(p)]

        extra = _names_from_key_players_section(prep_sections, label, name_map)
        kp = [
            name_map.get(p.get("name_latin", "")) or p.get("name", "")
            for p in (data.get("top_scorers") or {}).get(side) or []
        ]
        priority = [n for n in extra + kp if n and not is_placeholder_name(n)]
        priority_latin = [
            (p.get("name_latin") or p.get("name") or "").strip()
            for p in (data.get("top_scorers") or {}).get(side) or []
        ]
        roster = ((team_squads or {}).get(side) or [])
        probable = _probable_xi_from_squad(
            roster, scheme, priority, name_map, priority_latin=priority_latin,
        ) if roster else []

        if api and len(api["players"]) >= 8:
            squad = api
            if gpt_scheme:
                squad["scheme"] = gpt_scheme
        elif len(probable) >= 8:
            squad = {"scheme": scheme, "players": probable, "source": "roster"}
        elif len(real_gpt) >= 8:
            squad = {"scheme": scheme, "players": real_gpt, "source": "gpt"}
        elif api:
            squad = api
        else:
            merged = []
            for n in real_gpt + priority:
                if n and n not in merged and not is_placeholder_name(n):
                    merged.append(n)
            if len(merged) >= 5:
                squad = {
                    "scheme": scheme,
                    "players": merged,
                    "source": "partial",
                }
            else:
                continue

        squad["team_label"] = label
        squad["players"] = (squad.get("players") or [])[:11]
        squads[side] = squad

    return squads


def _bg_key_players(top_scorers: dict, name_map: dict[str, str]) -> dict:
    out: dict = {"home": [], "away": []}
    for side in ("home", "away"):
        for p in (top_scorers or {}).get(side) or []:
            latin = (p.get("name") or "?").strip()
            bg = _resolve_player_bg(latin, name_map)
            out[side].append({**p, "name": bg, "name_latin": latin})
    return out


def build_prep_kit(
    prematch_result: dict,
    lineups: dict | None = None,
    team_squads: dict | None = None,
) -> dict:
    """Turn prematch engine result into a stream-prep kit for the UI."""
    if not prematch_result or not prematch_result.get("available"):
        return {"ready": False}

    data = prematch_result.get("data") or {}
    meta = prematch_result.get("meta") or {}
    home = data.get("home") or meta.get("home_name", "")
    away = data.get("away") or meta.get("away_name", "")

    guide = data.get("gpt_narrative") or data.get("broadcast_guide_draft") or ""
    name_map = build_player_name_map(data)
    prep_text = data.get("prep_editorial") or ""
    if prep_text:
        prep_text = _enrich_player_names_in_text(prep_text, name_map)
    prep_sections = parse_prep_editorial(prep_text) if prep_text else []
    for sec in prep_sections:
        title_up = (sec.get("title") or "").upper()
        if title_up in ("ВЕРОЯТНИ СХЕМИ", "КЛЮЧОВИ ИГРАЧИ"):
            sec["body"] = _enrich_player_names_in_text(sec.get("body") or "", name_map)
    has_gpt_prep = bool(prep_text)
    ai_pending = bool(
        _gpt_available
        and not has_gpt_prep
        and not data.get("prep_editorial_gpt_failed")
    )

    injuries = data.get("injuries") or {}
    inj_home = injuries.get("home") or []
    inj_away = injuries.get("away") or []

    fixture_id = meta.get("fixture_id") or data.get("fixture_id")
    if team_squads is None:
        col = _get_stats_collector()
        team_squads = {
            "home": col.get_team_squad(meta.get("home_id") or 0),
            "away": col.get_team_squad(meta.get("away_id") or 0),
        }
    if lineups is None and fixture_id:
        col = _get_stats_collector()
        lineups = col.get_lineups(int(fixture_id), home, away)

    formation_squads = build_formation_squads(
        data, meta, lineups, prep_sections, team_squads=team_squads,
    )

    return {
        "ready": True,
        "teams": {"home": home, "away": away},
        "match_label": f"{home} срещу {away}",
        "headline": _prep_headline(prep_sections, f"{home} срещу {away} — детайлен анализ"),
        "competition": data.get("league") or meta.get("league_name", ""),
        "date": data.get("date") or (meta.get("date") or "")[:10],
        "analyzed_at": data.get("analyzed_at", ""),
        "fingerprint": data.get("fingerprint", ""),
        "loading": {
            "prep_editorial": ai_pending,
        },
        "prep_editorial_failed": bool(data.get("prep_editorial_gpt_failed")),
        "prep_ai_unavailable": bool(not _gpt_available and not has_gpt_prep),
        "prep_editorial": {
            "text": prep_text,
            "sections": prep_sections,
            "is_gpt": has_gpt_prep,
            "is_draft": False,
        },
        "player_name_map": name_map,
        "formation_squads": formation_squads,
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
        "key_players": _bg_key_players(data.get("top_scorers") or {}, name_map),
        "key_factors": data.get("key_factors") or [],
        "advantages": {
            "home": data.get("home_advantages") or [],
            "away": data.get("away_advantages") or [],
        },
        "group_scenarios": data.get("group_scenarios") or {},
        "coaches": data.get("coaches") or {},
        "referee": data.get("referee") or {},
        "injuries": {
            "home": [{
                "name": latin_name_to_bg(i.get("name", "?")),
                "reason": i.get("reason", i.get("type", "")),
            } for i in inj_home],
            "away": [{
                "name": latin_name_to_bg(i.get("name", "?")),
                "reason": i.get("reason", i.get("type", "")),
            } for i in inj_away],
        },
        "talking_points": _extract_talking_points(guide),
        "historical_facts": _extract_historical_facts(guide),
        "broadcast_guide": guide,
        "has_gpt_guide": bool(data.get("gpt_narrative")),
    }
