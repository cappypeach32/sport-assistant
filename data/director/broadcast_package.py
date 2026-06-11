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

    return {
        "active": True,
        "phase": "halftime",
        "title": "Полувреме — ефирен пакет",
        "score_label": f"{home} {score_home}:{score_away} {away}",
        "stats_lines": [
            f"Владение: {home} {hs.get('possession', '—')}% — {away} {as_.get('possession', '—')}%",
            (
                f"Удари: {home} {hs.get('shots_total', '—')} "
                f"({hs.get('shots_on_target', '—')} в рамките) — "
                f"{away} {as_.get('shots_total', '—')} "
                f"({as_.get('shots_on_target', '—')})"
            ),
            f"xG: {home} {hs.get('xg', '—')} — {away} {as_.get('xg', '—')}",
        ],
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

    return {
        "active": True,
        "phase": "fulltime",
        "title": "Край — ефирен пакет",
        "score_label": f"{home} {score_home}:{score_away} {away}",
        "wrap_up": wrap[:600],
        "stats_lines": [
            f"Удари: {home} {hs.get('shots_total', '—')} — {away} {as_.get('shots_total', '—')}",
            f"xG: {home} {hs.get('xg', '—')} — {away} {as_.get('xg', '—')}",
            f"Владение: {home} {hs.get('possession', '—')}% — {away} {as_.get('possession', '—')}%",
        ],
        "detail_loading": gpt_pending and not bool(postmatch_text),
    }
