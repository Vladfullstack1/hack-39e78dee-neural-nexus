from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .normalization import normalize_text, tokenize


@dataclass(frozen=True)
class RuleDocument:
    rule_id: str
    title: str
    text: str

    @property
    def searchable(self) -> str:
        return f"{self.rule_id} {self.title} {self.text}"


@dataclass(frozen=True)
class RuleHit:
    rule_id: str
    title: str
    text: str
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "text": self.text,
            "score": round(self.score, 4),
        }


_ID_KEYS = ("id", "rule_id", "ruleId", "number", "code", "ref", "article")
_TEXT_KEYS = ("text", "content", "description", "body", "rule", "value", "title", "name")
_CHILD_KEYS = ("rules", "items", "points", "clauses", "articles", "children", "sections")


def _first(mapping: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
    return None


def parse_rules(payload: Any) -> list[RuleDocument]:
    """Парсер для rules.json: поддерживает официальную структуру Grand Mobile (rules -> points) и вложенные дерева."""
    docs: list[RuleDocument] = []
    seen: set[tuple[str, str, str]] = set()

    def add_doc(rule_id: str, title: str, text: str) -> None:
        clean_id = normalize_text(rule_id)
        clean_title = normalize_text(title)
        clean_text = normalize_text(text)
        if not clean_id or not clean_text:
            return
        if clean_title == clean_text:
            clean_title = ""
        key = (clean_id, clean_title, clean_text)
        if key not in seen:
            docs.append(RuleDocument(rule_id=clean_id, title=clean_title, text=clean_text))
            seen.add(key)

    # 1. Поддержка официального формата Grand Mobile rules.json: {"rules": [{"id": ..., "category": ..., "points": [...]}]}
    if isinstance(payload, dict) and isinstance(payload.get("rules"), list):
        for section in payload["rules"]:
            if not isinstance(section, dict):
                continue
            sec_category = str(section.get("category") or section.get("title") or "").strip()
            sec_text = str(section.get("text") or "").strip()
            points = section.get("points")
            if isinstance(points, list) and points:
                for p in points:
                    if isinstance(p, dict):
                        ref = str(p.get("ref") or p.get("id") or "").strip()
                        p_text = str(p.get("text") or "").strip()
                        if ref and p_text:
                            add_doc(ref, sec_category, p_text)
                            # Добавляем алиасы для правил жалоб (ЖБ) и Зелёной Зоны (GZ) согласно taxonomy.json
                            if "правила жалоб" in sec_category.lower():
                                add_doc(f"ЖБ {ref}", sec_category, p_text)
                            elif "зелёной зоне" in sec_category.lower() or "зеленой зоне" in sec_category.lower():
                                if ref == "1.1":
                                    add_doc("GZ", sec_category, p_text)
            elif sec_text:
                # Раздел без points (например, Section 43: RP термины и их описание)
                bullets = [line.strip("• ").strip() for line in sec_text.splitlines() if line.strip().startswith("•")]
                if bullets:
                    for b in bullets:
                        m = re.search(r"\(сокращенно\s+([A-Za-zА-Яа-я0-9\s]+)\)", b)
                        term_id = m.group(1).strip() if m else str(section.get("id", "RP"))
                        add_doc(term_id, sec_category, b)
                else:
                    sec_id = str(section.get("id") or "").strip()
                    if sec_id:
                        add_doc(sec_id, sec_category, sec_text)
        if docs:
            return docs

    # 2. Универсальный рекурсивный обход для произвольных структур (sections -> rules и т.д.)
    def walk(node: Any, inherited_title: str = "", inherited_id: str = "") -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, inherited_title, inherited_id)
            return
        if isinstance(node, str):
            if inherited_id:
                add_doc(inherited_id, inherited_title, node)
            return
        if not isinstance(node, dict):
            return

        rule_id = _first(node, _ID_KEYS) or inherited_id
        title = _first(node, ("title", "name", "section", "heading", "category")) or inherited_title
        direct_text = _first(node, ("text", "content", "description", "body", "rule"))

        if rule_id and direct_text:
            add_doc(rule_id, title, direct_text)

        for key in _CHILD_KEYS:
            child = node.get(key)
            if child is not None:
                walk(child, title or inherited_title, rule_id or inherited_id)

        for key, value in node.items():
            if key in _CHILD_KEYS or key in _ID_KEYS or key in _TEXT_KEYS or key in {"_notice", "version", "lang", "source"}:
                continue
            if isinstance(value, str) and ("." in key or key[:1].isdigit()):
                walk(value, title or inherited_title, key)
            elif isinstance(value, (dict, list)):
                walk(value, title or inherited_title, rule_id or inherited_id)

    walk(payload)
    return docs


class RuleRetriever:
    """Гибридный поисковик (BM25 + точный буст по ID правила и разделу) для RAG."""

    def __init__(self, documents: list[RuleDocument] | None = None):
        self.documents = documents or []
        self._tokens = [tokenize(doc.searchable) for doc in self.documents]
        self._doc_freq: dict[str, int] = {}
        for tokens in self._tokens:
            for token in set(tokens):
                self._doc_freq[token] = self._doc_freq.get(token, 0) + 1
        self._avg_len = sum(map(len, self._tokens)) / max(1, len(self._tokens))

    @classmethod
    def from_file(cls, path: Path) -> "RuleRetriever":
        if not path.exists():
            return cls([])
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return cls([])
        return cls(parse_rules(payload))

    def search(self, query: str, top_k: int = 5) -> list[RuleHit]:
        if not self.documents:
            return []

        q_norm = normalize_text(query)
        q_lower = q_norm.lower()
        q_tokens = set(tokenize(q_norm))

        # Извлекаем явно упомянутые пункты правил (например, 2.1, 1.1.2, ЖБ 1.1.2, GZ)
        explicit_refs = set(re.findall(r"(?<!\d)\d{1,2}(?:\.\d{1,3}){1,3}(?!\d)", q_lower))
        for jb_match in re.findall(r"жб\s*(\d{1,2}(?:\.\d{1,3}){1,3})", q_lower):
            explicit_refs.add(f"жб {jb_match}")
            explicit_refs.add(jb_match)
        if re.search(r"(?<![a-zа-яё0-9])(?:gz|зз|гз|зелён|зелен)(?![a-zа-яё0-9])", q_lower):
            explicit_refs.add("gz")

        total = len(self.documents)
        raw_hits: list[RuleHit] = []
        k1, b = 1.5, 0.75

        mentions_general = bool(re.search(r"(?<![a-zа-яё])(?:оп|op)(?![a-zа-яё])", q_lower))
        mentions_gz = "gz" in explicit_refs
        mentions_jb = bool(re.search(r"(?<![a-zа-яё])жб(?![a-zа-яё])", q_lower))

        for doc, tokens in zip(self.documents, self._tokens):
            if not tokens:
                continue

            tf: dict[str, int] = {}
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1

            score = 0.0
            if q_tokens:
                for token in q_tokens:
                    freq = tf.get(token, 0)
                    if not freq:
                        continue
                    df = self._doc_freq.get(token, 0)
                    idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
                    denom = freq + k1 * (1 - b + b * len(tokens) / max(1, self._avg_len))
                    score += idf * (freq * (k1 + 1) / denom)

            doc_id_lower = doc.rule_id.lower()
            title_lower = doc.title.lower()

            # Точное совпадение идентификатора правила (без ложных подстрок вида "1.1" в "11.1")
            if doc_id_lower in explicit_refs:
                score += 15.0
                if "общие правила" in title_lower or not title_lower:
                    score += 4.0 if mentions_general else 2.5
                elif ("зелёной зоне" in title_lower or "зеленой зоне" in title_lower) and mentions_gz:
                    score += 4.0
                elif "правила жалоб" in title_lower and mentions_jb:
                    score += 4.0
            elif score > 0 and "общие правила" in title_lower:
                # Небольшой приоритет общим правилам игры при равном лексическом совпадении
                score += 0.5

            if score > 0:
                raw_hits.append(RuleHit(doc.rule_id, doc.title, doc.text, score))

        raw_hits.sort(key=lambda hit: hit.score, reverse=True)

        # Дедупликация по rule_id (оставляем наиболее релевантный раздел для каждого rule_id)
        deduped: list[RuleHit] = []
        seen_ids: set[str] = set()
        for hit in raw_hits:
            if hit.rule_id not in seen_ids:
                deduped.append(hit)
                seen_ids.add(hit.rule_id)
            if len(deduped) >= max(1, top_k):
                break

        return deduped

    def has_rule(self, rule_id: str) -> bool:
        normalized = normalize_text(rule_id).lower()
        return any(doc.rule_id.lower() == normalized for doc in self.documents)