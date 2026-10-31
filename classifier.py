from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .normalization import normalize_text, strip_placeholders, tokenize


LANG_LABELS = {"ru", "kk", "uz", "en", "und"}

# Уникальные казахские буквы (без 'і', которая также встречается в украинском)
KAZAKH_UNIQUE_CHARS = set("әңғқүұөһ")
KAZAKH_ALL_CHARS = set("әіңғқүұөһ")
UKRAINIAN_MARKERS = {"адмін", "адміністратор", "адміністратори", "будемо", "поможіть", "поможітя", "переверніть", "цікавить", "немає", "чому", "якщо", "щоб"}

UZBEK_MARKERS = {
    "salom", "assalomu", "aleykum", "allaykum", "iltimos", "rahmat", "menga", "meni", "mani",
    "uchun", "akkaunt", "oyin", "o'yin", "qanday", "qanaqa", "bo'ldi", "murojaat", "yordam",
    "bervorila", "berilar", "berila", "beradi", "berdi", "qildi", "qilip", "qilib", "oldirdi",
    "o'ldirdi", "sokdi", "sokyapti", "sokin", "so'k", "so'kindi", "onadan", "korila", "ketvoti",
    "aldab", "moshina", "mashina", "odam", "nega", "sabab", "besabab", "mesabab", "chora",
    "butunlay", "butunlayga", "to'lov", "men", "siz", "emas", "bor", "yo'q", "kerak", "nohaq",
    "shikoyat", "qoidabuzarlik", "kuzatyapman",
}

GAMING_LATIN_SLANG = {
    "dm", "db", "sk", "tk", "rk", "pg", "mg", "gz", "zz", "op", "nrp", "nonrp", "rp",
    "osk", "rod", "soft", "fraps", "kop", "ban", "mute", "warn", "fw", "fv", "jb", "dmg",
}


def detect_language(text: str, hinted: str | None = None, server_lang: str | None = None) -> str:
    cleaned = strip_placeholders(normalize_text(text)).lower()
    if not cleaned:
        if hinted in LANG_LABELS and hinted != "und":
            return hinted
        if server_lang in LANG_LABELS and server_lang != "und":
            return server_lang
        return "und"

    raw_words = set(re.findall(r"[a-zа-яёәіңғқүұөһ']+", cleaned))
    tokens = set(tokenize(cleaned))
    all_words = raw_words | tokens

    # 1. Проверка на казахский язык (учитывает нормализованные CP1251 символы)
    if any(ch in KAZAKH_UNIQUE_CHARS for ch in cleaned):
        return "kk"
    if "і" in cleaned and not (raw_words & UKRAINIAN_MARKERS):
        if server_lang != "ru" or hinted == "kk" or server_lang is None:
            return "kk"

    # 2. Проверка на узбекский язык
    if any(apostrophe in cleaned for apostrophe in ("oʻ", "gʻ", "o‘", "g‘", "o'", "g'")):
        return "uz"
    if all_words & UZBEK_MARKERS:
        return "uz"

    cyrillic = sum("а" <= ch <= "я" or ch == "ё" for ch in cleaned)
    latin = sum("a" <= ch <= "z" for ch in cleaned)

    # 3. Кириллический текст
    if cyrillic > 0 and cyrillic >= latin:
        if hinted == "kk" and server_lang == "kk":
            return "kk"
        return "ru"

    # 4. Латиница
    if latin > 0:
        if server_lang == "uz" or hinted == "uz":
            return "uz"
        latin_words = {w for w in raw_words if re.fullmatch(r"[a-z]+", w)}
        if latin_words and latin_words.issubset(GAMING_LATIN_SLANG):
            if server_lang in {"ru", "kk", "uz"}:
                return server_lang
            if hinted in {"ru", "kk", "uz"}:
                return hinted
            return "ru"
        if cyrillic > 0 and (server_lang == "ru" or hinted == "ru"):
            return "ru"
        if hinted in {"ru", "kk", "uz", "en"}:
            return hinted
        return "en"

    # 5. Только цифры/знаки препинания
    if server_lang in {"ru", "kk", "uz", "en"}:
        return server_lang
    if hinted in {"ru", "kk", "uz", "en"}:
        return hinted
    return "und"


def _load_taxonomy(path: Path) -> tuple[list[str], list[str], list[str], dict[str, list[str]]]:
    default_priorities = ["p0", "p1", "p2", "p3"]
    default_human = ["report.account", "report.payment", "complaint.admin"]
    if not path.exists():
        return [], default_priorities, default_human, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return [], default_priorities, default_human, {}

    categories: list[str] = []
    priorities: list[str] = []
    category_rules: dict[str, list[str]] = {}

    def collect(node: Any, out: list[str], keys: tuple[str, ...]) -> None:
        if isinstance(node, list):
            for item in node:
                collect(item, out, keys)
        elif isinstance(node, dict):
            value = next((node.get(k) for k in keys if isinstance(node.get(k), str)), None)
            if value and value not in out:
                out.append(value)
                if isinstance(node.get("rules"), list):
                    category_rules[value] = [str(r) for r in node["rules"] if str(r).strip()]
            for key, val in node.items():
                if isinstance(val, (dict, list)) and key not in {"_notice", "rules", "examples"}:
                    collect(val, out, keys)

    collect(data.get("categories", data), categories, ("code", "id", "name", "category"))
    collect(data.get("priorities", []), priorities, ("code", "id", "name", "priority"))
    needs_human_always = [
        str(x) for x in data.get("needs_human_always", default_human) if isinstance(x, str)
    ]
    return categories, (priorities or default_priorities), needs_human_always, category_rules


LEGACY_CATEGORY_ALIASES = {
    "complaint.cheating": "complaint.cheat",
    "complaint.scamming": "complaint.scam",
    "report.technical": "report.help",
    "report.account_security": "report.account",
    "report.ban_appeal": "complaint.admin",
}


class Taxonomy:
    def __init__(self, path: Path):
        self.categories, self.priorities, self.needs_human_always, self.category_rules = _load_taxonomy(path)

    def category(self, suggested: str, kind: str) -> str:
        if suggested in self.categories:
            return suggested
        aliased = LEGACY_CATEGORY_ALIASES.get(suggested)
        if aliased and aliased in self.categories:
            return aliased
        for rev_old, rev_new in LEGACY_CATEGORY_ALIASES.items():
            if suggested == rev_new and rev_old in self.categories:
                return rev_old
        fallback = f"{kind}.other" if kind == "complaint" else "report.question"
        if fallback in self.categories:
            return fallback
        if f"{kind}.other" in self.categories:
            return f"{kind}.other"
        candidates = [c for c in self.categories if c.startswith(kind + ".")]
        return candidates[0] if candidates else fallback

    def priority(self, suggested: str) -> str:
        return suggested if suggested in self.priorities else ("p2" if "p2" in self.priorities else self.priorities[-1])


COMPLAINT_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("complaint.admin", (
        "не согласен", "не согл", "ne sogl", "опру", "опра", "за что мут", "за что бан",
        "за что варн", "за что деморган", "админ выдал", "администратор выдал", "админ посадил",
        "админ забанил", "жалоба на адм", "накажите администратора", "снимите наказание",
        "смягчить наказание", "снизить наказание", "ошибочно забанили", "дал варн", "дала варн",
        "дал бан", "дал мут", "ban berdi", "mute berdi", "mute beradi", "nohaq ban", "әкімші",
        "решением администратора", "действий администрации",
    )),
    ("complaint.cheat", (
        "чит", "читы", "читер", "cheat", "hack", "хак", "аим", "aimbot", "софт", "soft",
        "автокликер", "макрос", "спидхак", "wallhack", "через стен", "летает по воздух",
        "багоюз", "багаюз", "bagayuz", "стороннее по", "скрипт", "гм", "телепортнулся", "тепнулся",
    )),
    ("complaint.rmt_ads", (
        "за реал", "реальные деньги", "продает аккаунт", "продаёт аккаунт", "продажа аккаунт",
        "продажа вирт", "покупка вирт", "рекламирует", "реклама сервер", "другой сервер",
        "сторонний проект", "продает за реал", "продаёт за реал",
    )),
    ("complaint.scam", (
        "скам", "обман", "мошенн", "развод", "кинул на", "не отдал", "не вернул", "вымога",
        "scam", "aldab", "aldov", "firib", "abmen", "махинац", "украл машин", "обманул",
    )),
    ("complaint.insult", (
        "оскорб", "оск", "мат", "обзыв", "унижа", "хейт", "родн", "мать", "мам", "отца",
        "семь", "расизм", "нацизм", "розн", "религ", "политик", "угроз", "докс", "osk",
        "so'k", "soʻk", "sokdi", "sokyapti", "sokin", "onadan", "haqorat", "сөг", "қорла", "балағат",
    )),
    ("complaint.dm", (
        "дм", "dm", "дб", "db", "ск", "sk", "тк", "tk", "рк", "rk", "пг", "pg", "мг", "mg",
        "нрп", "нонрп", "nonrp", "nrp", "убил", "убийств", "урон", "стреля", "расстрел",
        "задавил", "таран", "зелёной зоне", "зеленой зоне", " в зз", "зз ", "гз", "gz",
        "связал", "веревк", "верёвк", "наручник", "арест без", "уход от рп", "лив от рп",
        "oldirdi", "o'ldir", "kishan",
    )),
]

REPORT_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("report.payment", (
        "донат", "платеж", "платёж", "покуп", "оплат", "деньги не пришли", "не пришло",
        "гк", "gc", "возврат средств", "pay", "payment", "to'lov", "donat",
    )),
    ("report.account", (
        "взлом", "украли аккаунт", "аккаунт укра", "не могу войти", "потерял доступ",
        "пропала машина", "пропал", "пропало", "восстановить аккаунт", "пароль",
        "инвентар", "не появил", "hack account", "account stolen",
    )),
    ("report.complaint", (
        "дм", "dm", "дб", "db", "ск", "sk", "тк", "tk", "нрп", "нонрп", "софт", "чит",
        "оск", "врубает музыку", "следите", "спекайте", "посадите", "дайте мут", "кикните",
    )),
    ("report.help", (
        "застрял", "текстур", "выбраться", "подбросил", "в речку", "в воду", "переверн",
        "спавн", "респавн", "рестп", "тп ", "телепорт", "баг", "вылет", "лаг", "ошибка",
        "не запуска", "не работает", "почините", "развяж", "зав'язали", "связали",
        "потірявся", "потерялся", "bug", "crash", "qotib", "ishlamay", "жұмыс істемейді",
    )),
    ("report.question", (
        "как ", "где ", "когда ", "сколько ", "что будет если", "можно ли", "собеседование",
        "обзвон", "устроиться", "лицензи", "купить", "продать", "qanday", "qayerda",
    )),
]

BARE_OFFTOPIC_WORDS = {
    "помогите", "помоги", "помощь", "памагите", "памагіте", "поможіть", "помогите пожалуйста",
    "помоги пожалуйста", "помогите администрация", "хелп", "help", "yordam", "yordam bervorila",
    "yordam admin", "ау", "ауу", "аууу", "привет", "здравствуйте", "салам", "salom", "тут",
    "вы тут", "следите", "админ", "админы", "госку", "госку одобрите",
}

HUMAN_ALWAYS_PATTERNS = (
    "донат", "платеж", "платёж", "оплат", "возврат средств", "разбан", "апелляц",
    "взлом", "украли аккаунт", "аккаунт укра", "угрож", "угроза жизни", "докс", "личные данные",
    "шанта", "мошенн", "скам", "не могу войти после взлома", "пропала машина", "пропало имущество",
)

URGENT_PATTERNS = (
    "массов", "сервер лежит", "все не работает", "всех выкинуло", "утечк",
    "взлом сервера", "угроза жизни", "докс", "слив личных",
)
HIGH_PATTERNS = ("угрож", "шантаж", "докс", "распространяет данные", "массов", "взлом")
SPAM_PATTERNS = ("подписывайся на канал", "заработай легкие деньги", "casino", "онлайн казино", "ставки на спорт")


def _match_rule_ref_to_category(refs: list[str], kind: str, raw_ref_text: str) -> str | None:
    raw_lower = normalize_text(raw_ref_text).lower()
    if kind == "complaint":
        if "жб" in raw_lower and any(r in {"1.1.2", "1.2.2", "1.2.3", "1.2.7", "1.2.9"} for r in refs):
            return "complaint.invalid"
        for r in refs:
            if r in {"3.4", "3.2"}:
                return "complaint.admin"
            if r in {"5.2"}:
                return "complaint.cheat"
            if r in {"2.3", "4.4"}:
                return "complaint.rmt_ads"
            if r in {"4.1", "4.2", "4.3", "4.3.1", "4.5", "4.6", "4.7"}:
                return "complaint.scam"
            if r in {"2.1", "2.2", "2.4", "2.5", "2.6", "1.6", "5.6", "5.7", "5.8"}:
                return "complaint.insult"
            if r in {"5.1", "5.1.1", "5.3", "5.4", "5.4.1", "5.4.2", "5.5", "GZ"}:
                return "complaint.dm"
            if r in {"1.1.2", "1.2.2", "1.2.3", "1.2.7"}:
                return "complaint.invalid"
        if any(w in raw_lower for w in ("дм", "dm", "дб", "db", "ск", "sk", "тк", "tk", "зз", "гз", "gz", "нрп", "nrp")):
            return "complaint.dm"
        if any(w in raw_lower for w in ("оск", "osk", "мат", "род")):
            return "complaint.insult"
        if any(w in raw_lower for w in ("чит", "софт", "soft", "аим", "баг")):
            return "complaint.cheat"
        if any(w in raw_lower for w in ("скам", "scam", "обман")):
            return "complaint.scam"
        if any(w in raw_lower for w in ("опру", "не согл", "адм", "разбан")):
            return "complaint.admin"
    else:
        for r in refs:
            if r in {"1.4", "1.7"}:
                return "report.account"
            if r in {"3.1"}:
                return "report.help"
    return None


def classify(
    text: str,
    kind: str,
    taxonomy: Taxonomy,
    *,
    rule_refs_raw: str | None = None,
    has_evidence: bool = True,
) -> tuple[str, str, float, list[str]]:
    cleaned = strip_placeholders(normalize_text(text)).lower()
    combined = f"{cleaned} {normalize_text(rule_refs_raw).lower()}".strip()
    refs = extract_rule_refs(rule_refs_raw, text)

    matched_category: str | None = None

    if kind == "complaint":
        # 1. Проверяем текстовые паттерны жалоб
        for cat_code, patterns in COMPLAINT_PATTERNS:
            if any(p in combined for p in patterns):
                matched_category = cat_code
                break
        # 2. Если по тексту не сработало, проверяем пункты правил
        if not matched_category:
            matched_category = _match_rule_ref_to_category(refs, kind, rule_refs_raw or "")
        # 3. Проверка на невалидную/пустую жалобу или вопрос вместо жалобы
        if not matched_category:
            alpha_chars = sum(ch.isalpha() for ch in cleaned)
            if alpha_chars < 4 and not refs:
                matched_category = "complaint.invalid"
            elif any(q in cleaned for q in ("как купить", "как устроиться", "где взять", "почему не поднимается")):
                matched_category = "complaint.invalid"
        category = matched_category or "complaint.other"
    else:
        # Внутриигровой репорт
        # Проверяем, не является ли это пустым/оффтоп сообщением ("помогите", "ау", "+")
        norm_punct_stripped = re.sub(r"[^\w\s]", "", cleaned).strip()
        if not norm_punct_stripped or norm_punct_stripped.isdigit() or norm_punct_stripped in BARE_OFFTOPIC_WORDS:
            matched_category = "report.offtopic"
        else:
            for cat_code, patterns in REPORT_PATTERNS:
                if any(p in combined for p in patterns):
                    matched_category = cat_code
                    break
            if not matched_category and "?" in text:
                matched_category = "report.question"
            if not matched_category:
                matched_category = _match_rule_ref_to_category(refs, kind, rule_refs_raw or "")
            if not matched_category and any(w in norm_punct_stripped.split() for w in ("помогите", "помоги", "хелп", "help", "yordam")):
                matched_category = "report.help" if len(norm_punct_stripped.split()) >= 3 else "report.offtopic"
        category = matched_category or "report.question"

    resolved_category = taxonomy.category(category, kind)

    # Расчёт приоритета согласно правилам taxonomy.json
    reasons: list[str] = []
    if any(pattern in combined for pattern in URGENT_PATTERNS) or resolved_category in {"report.payment", "report.account"} or "2.6" in refs:
        priority = "p0"
        reasons.append("обнаружены признаки критического инцидента (аккаунт/платёж/угрозы/массовый сбой)")
    elif any(pattern in combined for pattern in HIGH_PATTERNS) or resolved_category in {"complaint.cheat", "complaint.scam", "complaint.rmt_ads"}:
        priority = "p1"
        reasons.append("обнаружены признаки серьёзного нарушения (читы/мошенничество/RMT)")
    elif resolved_category in {"report.offtopic", "report.question", "complaint.invalid"} or (kind == "complaint" and not has_evidence and resolved_category == "complaint.other"):
        priority = "p3"
        reasons.append("обращение низкого приоритета (вопрос по механике, оффтоп или без доказательств)")
    elif kind == "complaint":
        priority = "p1" if (has_evidence and resolved_category in {"complaint.admin"}) else "p2"
    else:
        priority = "p2"
        reasons.append("обычное обращение без признаков критичности")

    confidence = 0.84 if matched_category else 0.56
    if not matched_category:
        reasons.append("категория определена по типу обращения; недостаточно ключевых признаков")

    return resolved_category, taxonomy.priority(priority), confidence, reasons


def extract_rule_refs(*values: str | None) -> list[str]:
    refs: list[str] = []
    for value in values:
        if not value:
            continue
        norm_val = normalize_text(value)
        # Требуем хотя бы одну точку (например, 2.1, 1.1.2, 5.4.1), чтобы не захватывать обычные числа ("60 минут", "7 дней")
        for match in re.findall(r"(?<!\d)(\d{1,2}(?:\.\d{1,3}){1,3})(?!\d)", norm_val):
            # Отсекаем заведомо ложные последовательности вроде "1.2.3.4" или "67.67.67"
            parts = [int(p) for p in match.split(".")]
            if parts[0] <= 15 and all(p <= 50 for p in parts):
                if match not in refs:
                    refs.append(match)
        if re.search(r"(?<![a-zа-яё0-9])(?:gz|зз|гз)(?![a-zа-яё0-9])", norm_val.lower()):
            if "GZ" not in refs:
                refs.append("GZ")
    return refs[:10]


def extract_nickname(text: str, reported_nick: str | None = None) -> str | None:
    if reported_nick:
        return reported_nick
    cleaned = strip_placeholders(text)
    # Сначала ищем канонический игровой ник с нижним подчёркиванием (Имя_Фамилия)
    underscore_nicks = re.findall(r"\b([A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]{2,23})\b", cleaned)
    if underscore_nicks:
        return underscore_nicks[0]
    candidates = re.findall(r"(?<![\w])([A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9_]{2,23})(?![\w])", cleaned)
    blocked = {
        "Здравствуйте", "Помогите", "Приветствую", "правил", "игрок", "скрины",
        "есть", "жалоба", "меня", "семью", "админ", "сервер", "багаюз",
    }
    for candidate in candidates:
        if candidate not in blocked and not re.fullmatch(r"\d+(?:\.\d+)*", candidate):
            return candidate
    return None


def detect_spam(text: str) -> bool:
    lowered = strip_placeholders(normalize_text(text)).lower()
    if any(pattern in lowered for pattern in SPAM_PATTERNS):
        return True
    tokens = tokenize(lowered)
    return len(tokens) >= 12 and len(set(tokens)) <= 3


def must_escalate(
    text: str,
    category: str | None = None,
    taxonomy: Taxonomy | None = None,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if category and taxonomy and category in taxonomy.needs_human_always:
        reasons.append(f"требуется человек по регламенту категории «{category}»")
    lowered = normalize_text(text).lower()
    matched = [pattern for pattern in HUMAN_ALWAYS_PATTERNS if pattern in lowered]
    if matched:
        reasons.append(f"требуется человек: тема «{matched[0]}»")
    return bool(reasons), reasons
