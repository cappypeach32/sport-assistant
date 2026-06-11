"""Structured HT/FT broadcast packages for overlay + commentator views."""

import re


def _parse_bullets_from_text(text: str, limit: int = 3) -> list[str]:
    if not text:
        return []
    bullets: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^[\d•\-]+\.?\s*(.+)$", line)
        if m:
            bullets.append(m.group(1).strip())
        elif line.startswith("•"):
            bullets.append(line.lstrip("• ").strip())
    if bullets:
        return bullets[:limit]

    chunks = [c.strip() for c in re.split(r"\n\n+", text) if len(c.strip()) > 20]
    return chunks[:limit]


def _extract_section(text: str, keywords: tuple[str, ...]) -> str:
    if not text:
        return ""
    parts = re.split(r"\n##\s+", text)
    for part in parts:
        head = part.split("\n", 1)[0].lower()
        if any(kw in head for kw in keywords):
            body = part.split("\n", 1)
            return body[1].strip() if len(body) > 1 else ""
    return ""


def _short_team(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "—"
    parts = name.split()
    return parts[-1] if len(parts) > 1 and len(parts[-1]) > 2 else name


def _stat_val(v) -> str | None:
    if v is None or v == "" or v in ("—", "–", "-", "n/a", "N/A"):
        return None
    return str(v)


def _fmt_pct(v) -> str | None:
    raw = _stat_val(v)
    if raw is None:
        return None
    return raw if raw.endswith("%") else f"{raw}%"


def build_stats_table(home: str, away: str, hs: dict, as_: dict) -> dict:
    """Compact home/away stats table — omit rows with no API data."""
    rows: list[dict] = []

    h_sh = _stat_val(hs.get("shots_total"))
    a_sh = _stat_val(as_.get("shots_total"))
    if h_sh is not None or a_sh is not None:
        rows.append({"label": "Удари", "home": h_sh or "—", "away": a_sh or "—"})

    h_sot = _stat_val(hs.get("shots_on_target"))
    a_sot = _stat_val(as_.get("shots_on_target"))
    if h_sot is not None or a_sot is not None:
        rows.append({"label": "В рамките", "home": h_sot or "—", "away": a_sot or "—"})

    h_xg = _stat_val(hs.get("xg"))
    a_xg = _stat_val(as_.get("xg"))
    if h_xg is not None or a_xg is not None:
        rows.append({"label": "xG", "home": h_xg or "—", "away": a_xg or "—"})

    h_pos = _fmt_pct(hs.get("possession"))
    a_pos = _fmt_pct(as_.get("possession"))
    if h_pos is not None or a_pos is not None:
        rows.append({"label": "Владение", "home": h_pos or "—", "away": a_pos or "—"})

    return {
        "available": bool(rows),
        "home_short": _short_team(home),
        "away_short": _short_team(away),
        "rows": rows,
    }


def _stats_lines_from_table(table: dict) -> list[str]:
    if not table.get("available"):
        return []
    h = table["home_short"]
    a = table["away_short"]
    return [f"{row['label']}: {h} {row['home']} · {a} {row['away']}" for row in table["rows"]]


def build_halftime_package(
    home: str,
    away: str,
    score_home: int,
    score_away: int,
    live_stats: dict,
    halftime_text: str = "",
) -> dict:
    hs = live_stats.get("home", {}) if live_stats else {}
    as_ = live_stats.get("away", {}) if live_stats else {}

    second_half = _extract_section(
        halftime_text,
        ("второ полувреме", "очакваме", "какво да очакваме"),
    )
    bullets = _parse_bullets_from_text(second_half, 3)
    if len(bullets) < 3:
        bullets = _ht_bullets_rule_based(home, away, score_home, score_away, hs, as_)

    stats_table = build_stats_table(home, away, hs, as_)

    return {
        "active": True,
        "phase": "halftime",
        "title": "Полувреме — ефирен пакет",
        "score_label": f"{home} {score_home}:{score_away} {away}",
        "stats_table": stats_table,
        "stats_lines": _stats_lines_from_table(stats_table),
        "bullets": bullets[:3],
        "detail_loading": not bool(halftime_text),
    }


def _ht_bullets_rule_based(
    home: str,
    away: str,
    score_home: int,
    score_away: int,
    hs: dict,
    as_: dict,
) -> list[str]:
    bullets: list[str] = []

    if score_home > score_away:
        bullets.append(f"{home} водят — очаквайте дали ще затворят мача или {away} ще натисне за изравняване.")
    elif score_away > score_home:
        bullets.append(f"{away} водят — {home} трябва да повишат темпото и риска във второто полувреме.")
    else:
        bullets.append(f"Равен {score_home}:{score_away} — второто полувреме е отворено за двата отбора.")

    hp = hs.get("possession")
    ap = as_.get("possession")
    if hp is not None and ap is not None:
        dom = home if hp >= ap else away
        bullets.append(f"Владение: {home} {hp}% — {away} {ap}%. {dom} контролираше повече топката в първото полувреме.")

    hxg = hs.get("xg")
    axg = as_.get("xg")
    if hxg not in (None, "—", "") or axg not in (None, "—", ""):
        bullets.append(f"xG: {home} {hxg} — {away} {axg}. Следете дали опасността ще се превърне в голове.")

    while len(bullets) < 3:
        bullets.append("Следете смените и дали отборът с по-висок пресинг ще доминира след почивката.")
        break

    return bullets[:3]


def build_fulltime_package(
    home: str,
    away: str,
    score_home: int,
    score_away: int,
    live_stats: dict,
    postmatch_text: str = "",
    gpt_pending: bool = False,
) -> dict:
    hs = live_stats.get("home", {}) if live_stats else {}
    as_ = live_stats.get("away", {}) if live_stats else {}

    wrap = _extract_section(postmatch_text, ("заключение", "как завърши", "финал"))
    if not wrap:
        paras = [p.strip() for p in re.split(r"\n\n+", postmatch_text or "") if p.strip()]
        wrap = paras[0] if paras else ""

    if not wrap:
        if score_home > score_away:
            wrap = f"{home} спечели {score_home}:{score_away} срещу {away}."
        elif score_away > score_home:
            wrap = f"{away} спечели {score_away}:{score_home} срещу {home}."
        else:
            wrap = f"Мачът завърши {score_home}:{score_away} между {home} и {away}."

    stats_table = build_stats_table(home, away, hs, as_)

    return {
        "active": True,
        "phase": "fulltime",
        "title": "Край — ефирен пакет",
        "score_label": f"{home} {score_home}:{score_away} {away}",
        "wrap_up": wrap[:600],
        "stats_table": stats_table,
        "stats_lines": _stats_lines_from_table(stats_table),
        "detail_loading": gpt_pending and not bool(postmatch_text),
    }
