"""Latin football names → Bulgarian Cyrillic (broadcast-style)."""

import re

# Well-known players — media spellings used on Bulgarian streams
KNOWN_NAMES: dict[str, str] = {
    "erling haaland": "Ерлинг Хааланд",
    "martin ødegaard": "Мартин Йодегор",
    "kylian mbappe": "Килиан Мбапе",
    "kylian mbappé": "Килиан Мбапе",
    "franck kessie": "Франк Кесие",
    "franck kessié": "Франк Кесие",
    "amad diallo": "Амад Диало",
    "victor osimhen": "Виктор Осимен",
    "harry kane": "Хари Кейн",
    "mo salah": "Мохамед Салах",
    "mohamed salah": "Мохамед Салах",
    "virgil van dijk": "Вирджил ван Дайк",
    "kevin de bruyne": "Кевин Де Бройне",
    "erling braut haaland": "Ерлинг Хааланд",
    "cristiano ronaldo": "Кристиано Роналдо",
    "lionel messi": "Лионел Меси",
    "luka modric": "Лука Модрич",
    "luka modrić": "Лука Модрич",
    "antoine nusa": "Антоан Нуса",
    "alexander sorloth": "Александър Сорлот",
    "felix myhre": "Феликс Мюре",
}

_CHAR_MAP = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г",
    "h": "х", "i": "и", "j": "ж", "k": "к", "l": "л", "m": "м", "n": "н",
    "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т", "u": "у",
    "v": "в", "w": "у", "x": "кс", "y": "и", "z": "з",
}

_DIGRAPHS = [
    ("sch", "ш"), ("sh", "ш"), ("ch", "ч"), ("gh", "г"), ("ph", "ф"),
    ("th", "т"), ("wh", "у"), ("ck", "к"), ("qu", "кв"), ("ee", "и"),
    ("oo", "у"), ("ou", "у"), ("ea", "и"), ("ai", "ей"), ("ay", "ей"),
    ("ey", "ей"), ("oi", "ой"), ("oy", "ой"), ("au", "ау"), ("ä", "е"),
    ("ö", "ьо"), ("ü", "ю"), ("ø", "й"), ("é", "е"), ("è", "е"), ("ê", "е"),
    ("á", "а"), ("à", "а"), ("â", "а"), ("í", "и"), ("ó", "о"), ("ú", "у"),
    ("ç", "с"), ("ñ", "н"), ("ß", "с"), ("æ", "е"), ("å", "о"), ("ð", "д"),
    ("þ", "т"), ("ë", "е"), ("ï", "и"), ("œ", "ьо"), ("ć", "ч"), ("č", "ч"),
    ("š", "ш"), ("ž", "ж"), ("đ", "дж"), ("ř", "р"), ("ł", "л"), ("ń", "н"),
    ("ß", "с"),
]


def _is_mostly_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    latin = sum(
        1 for c in letters
        if ("a" <= c.lower() <= "z") or c.lower() in "àáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ"
    )
    return latin / len(letters) > 0.6


def _capitalize_bg(word: str) -> str:
    if not word:
        return word
    if len(word) == 1:
        return word.upper()
    return word[0].upper() + word[1:]


def _translit_word(word: str) -> str:
    w = word.lower().strip()
    if not w:
        return word

    for src, dst in _DIGRAPHS:
        w = w.replace(src, dst)

    out: list[str] = []
    i = 0
    while i < len(w):
        c = w[i]
        if c == "h" and i == 0:
            out.append("х")
        elif c == "c" and i + 1 < len(w) and w[i + 1] in "eiy":
            out.append("с")
            i += 1
            if i < len(w):
                out.append(_CHAR_MAP.get(w[i], w[i]))
        elif c == "g" and i + 1 < len(w) and w[i + 1] in "eiy":
            out.append("дж")
            i += 1
            if i < len(w):
                out.append(_CHAR_MAP.get(w[i], w[i]))
        else:
            out.append(_CHAR_MAP.get(c, c))
        i += 1
    return "".join(out)


def latin_name_to_bg(name: str) -> str:
    """Transliterate a player name to Bulgarian Cyrillic."""
    if not name or not name.strip():
        return name

    key = name.strip().lower()
    if key in KNOWN_NAMES:
        return KNOWN_NAMES[key]

    if re.search(r"[а-яА-Я]", name) and not re.search(r"[a-zA-Z]", name):
        return name

    if not _is_mostly_latin(name):
        return name

    parts = re.split(r"([\s\-'])", name.strip())
    result: list[str] = []
    for part in parts:
        if not part or part in "-'":
            result.append(part)
            continue
        sub_key = part.lower()
        if sub_key in KNOWN_NAMES:
            result.append(KNOWN_NAMES[sub_key])
        elif _is_mostly_latin(part):
            result.append(_capitalize_bg(_translit_word(part)))
        else:
            result.append(part)
    return "".join(result)


def build_player_name_map(data: dict) -> dict[str, str]:
    """Build latin → BG map from top scorers and editorial mentions."""
    mapping: dict[str, str] = {}

    for side in ("home", "away"):
        for player in (data.get("top_scorers") or {}).get(side) or []:
            latin = (player.get("name") or "").strip()
            if latin and latin not in mapping:
                mapping[latin] = latin_name_to_bg(latin)

    return mapping


def apply_name_map_to_text(text: str, name_map: dict[str, str]) -> str:
    """Replace known Latin player names in free text."""
    if not text or not name_map:
        return text

    result = text
    for latin, bg in sorted(name_map.items(), key=lambda x: -len(x[0])):
        if latin and bg and latin != bg:
            result = re.sub(re.escape(latin), bg, result, flags=re.IGNORECASE)
    return result
