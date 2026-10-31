from __future__ import annotations

import re
import unicodedata

# Legacy CP1251-lookalike characters described in the Grand Mobile brief.
CP1251_KK_MAP = str.maketrans({
    "ќ": "қ", "ґ": "ғ", "ѕ": "ң", "є": "ә",
    "ј": "ө", "ў": "ұ", "ї": "ү",
    "Ќ": "Қ", "Ґ": "Ғ", "Ѕ": "Ң", "Є": "Ә",
    "Ј": "Ө", "Ў": "Ұ", "Ї": "Ү",
})

PLACEHOLDER_RE = re.compile(r"<(?:url|email|handle|digits)>", flags=re.IGNORECASE)

ROOT_SYNONYMS: tuple[tuple[str, str], ...] = (
    ("оскорб", "оскорб"),
    ("оск", "оскорб"),
    ("родственник", "родн"),
    ("родн", "родн"),
    ("мошеннич", "мошенник"),
    ("мошенник", "мошенник"),
    ("читер", "чит"),
    ("читы", "чит"),
    ("читам", "чит"),
    ("блокировк", "блок"),
    ("заблокир", "блок"),
    ("разбан", "блок"),
    ("администрац", "админ"),
    ("администратор", "админ"),
    ("доказательств", "доказательств"),
    ("скриншот", "скрин"),
    ("скрин", "скрин"),
    ("убийств", "убийств"),
    ("убил", "убийств"),
    ("убива", "убийств"),
    ("игрок", "игрок"),
    ("игров", "игрок"),
)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).translate(CP1251_KK_MAP)
    text = text.replace("\u200b", " ").replace("\ufeff", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_placeholders(value: str | None) -> str:
    if not value:
        return ""
    cleaned = PLACEHOLDER_RE.sub(" ", str(value))
    return re.sub(r"\s+", " ", cleaned).strip()


def tokenize(value: str) -> list[str]:
    text = strip_placeholders(normalize_text(value)).lower()
    raw_tokens = re.findall(r"[a-zа-яёәіңғқүұөһ0-9]+", text, flags=re.IGNORECASE)
    suffixes = (
        "иями", "ями", "ами", "ение", "ание", "ения", "ания", "ением", "анием",
        "ого", "ему", "ыми", "ими", "ешь", "ете", "али", "яли", "ала", "яла",
        "ать", "ять", "ить", "еть", "ова", "ева", "ств", "ость", "ости",
        "ов", "ев", "ей", "ой", "ый", "ий", "ая", "яя", "ое", "ее", "ую", "юю",
        "ам", "ям", "ах", "ях", "ом", "ем", "ил", "ял", "ал", "ы", "и", "а", "я", "е", "у", "ю", "о",
    )
    normalized: list[str] = []
    for token in raw_tokens:
        stem = token
        for prefix, canonical in ROOT_SYNONYMS:
            if stem == prefix or (len(prefix) >= 4 and stem.startswith(prefix)):
                stem = canonical
                break
        else:
            if len(token) > 4 and not re.fullmatch(r"[a-z0-9]+", token):
                for suffix in suffixes:
                    if len(stem) - len(suffix) >= 3 and stem.endswith(suffix):
                        stem = stem[:-len(suffix)]
                        break
        normalized.append(stem)
    return normalized
