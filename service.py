from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections import OrderedDict
from datetime import datetime
from typing import Any

from .classifier import (
    Taxonomy,
    classify,
    detect_language,
    detect_spam,
    extract_nickname,
    extract_rule_refs,
    must_escalate,
)
from .config import Settings, settings
from .llm import GeminiClient
from .normalization import normalize_text, strip_placeholders, tokenize
from .rag import RuleRetriever
from .schemas import TriageRequest, TriageResponse


STATIC_REPLIES = {
    "ru": {
        "human": "Здравствуйте! Спасибо за обращение. Мы передали его администратору для проверки. Пожалуйста, ожидайте решения специалиста.",
        "rule": "Здравствуйте! Мы нашли в правилах игры пункт(ы) {refs}, которые могут относиться к описанной ситуации. Черновик подготовлен для проверки администратором; окончательное решение принимает специалист.",
        "generic": "Здравствуйте! Спасибо за обращение. Мы зарегистрировали его. Для точного ответа администратору может понадобиться дополнительная проверка.",
        "duplicate": "Здравствуйте! Похоже, это повторное обращение по той же проблеме. Мы объединим его с предыдущим, чтобы сохранить историю рассмотрения.",
    },
    "kk": {
        "human": "Сәлеметсіз бе! Өтінішіңізге рақмет. Біз оны тексеру үшін әкімшіге жібердік. Маманданған қызметкердің жауабын күтіңіз.",
        "rule": "Сәлеметсіз бе! Ойын ережелерінен сипатталған жағдайға қатысты болуы мүмкін {refs} тармағын таптық. Бұл әкімші тексеруіне арналған жоба; соңғы шешімді маман қабылдайды.",
        "generic": "Сәлеметсіз бе! Өтінішіңіз тіркелді. Нақты жауап беру үшін әкімшінің қосымша тексеруі қажет болуы мүмкін.",
        "duplicate": "Сәлеметсіз бе! Бұл сол мәселе бойынша қайталанған өтінішке ұқсайды. Қарау тарихын сақтау үшін оны алдыңғы өтінішпен біріктіреміз.",
    },
    "uz": {
        "human": "Assalomu alaykum! Murojaatingiz uchun rahmat. Uni tekshirish uchun administratorga yubordik. Mutaxassis javobini kuting.",
        "rule": "Assalomu alaykum! O‘yin qoidalarida vaziyatga tegishli bo‘lishi mumkin bo‘lgan {refs} bandini topdik. Bu administrator tekshiruvi uchun tayyorlangan loyiha; yakuniy qarorni mutaxassis qabul qiladi.",
        "generic": "Assalomu alaykum! Murojaatingiz qabul qilindi. Aniq javob berish uchun administrator qo‘shimcha tekshiruv o‘tkazishi mumkin.",
        "duplicate": "Assalomu alaykum! Bu ayni muammo bo‘yicha takroriy murojaatga o‘xshaydi. Uni oldingi murojaat bilan birlashtiramiz.",
    },
    "en": {
        "human": "Hello! Thank you for contacting support. Your request has been forwarded to an administrator for review. Please wait for a specialist's decision.",
        "rule": "Hello! We found rule section(s) {refs} that may relate to the situation described. This draft is for administrator review; the final decision is made by a specialist.",
        "generic": "Hello! Thank you for contacting support. Your request has been registered. An administrator may need to review it before we can provide a precise answer.",
        "duplicate": "Hello! This appears to be a duplicate request about the same issue. We will link it with the earlier request to keep the review history together.",
    },
    "und": {
        "human": "Здравствуйте! Обращение передано администратору для проверки.",
        "rule": "Здравствуйте! Найдены возможные связанные пункты правил: {refs}. Черновик требует проверки администратором.",
        "generic": "Здравствуйте! Обращение зарегистрировано. Для точного ответа может потребоваться проверка администратора.",
        "duplicate": "Похоже, это повторное обращение по той же проблеме.",
    },
}


class DuplicateGuard:
    def __init__(self, ttl_seconds: int = 3600, max_items: int = 50000, similarity_threshold: float = 0.78):
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self.similarity_threshold = similarity_threshold
        self._seen: OrderedDict[str, tuple[float, str, str, set[str], str | None]] = OrderedDict()

    def check_and_add(
        self,
        reporter_hash: str | None,
        kind: str,
        text: str,
        created_at: str | None = None,
        reported_id: str | None = None,
    ) -> bool:
        cleaned = strip_placeholders(normalize_text(text)).lower()
        normalized = re.sub(r"\W+", " ", cleaned).strip()
        if len(normalized) < 12 and not (reported_id and len(normalized) >= 3):
            return False
        if not reporter_hash:
            return False
        identity = reporter_hash
        try:
            event_time = (
                datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
                if created_at
                else time.time()
            )
        except (ValueError, TypeError):
            event_time = time.time()
        tokens = set(tokenize(normalized))
        if not tokens:
            return False
        exact_key = hashlib.sha256(f"{identity}|{kind}|{normalized}|{reported_id or ''}".encode("utf-8")).hexdigest()
        is_duplicate = False
        for key, (seen_time, seen_identity, seen_kind, seen_tokens, seen_target) in reversed(self._seen.items()):
            if abs(event_time - seen_time) > self.ttl_seconds:
                continue
            if seen_identity != identity or seen_kind != kind:
                continue
            if exact_key == key:
                is_duplicate = True
                break
            if tokens and seen_tokens:
                overlap = len(tokens & seen_tokens) / max(1, len(tokens | seen_tokens))
                if overlap >= self.similarity_threshold:
                    is_duplicate = True
                    break
                if reported_id and seen_target and reported_id == seen_target and overlap >= 0.4:
                    is_duplicate = True
                    break
        self._seen[exact_key] = (event_time, identity, kind, tokens, reported_id)
        self._seen.move_to_end(exact_key)
        while len(self._seen) > self.max_items:
            self._seen.popitem(last=False)
        return is_duplicate


class SupportCopilot:
    def __init__(self, config: Settings = settings):
        self.config = config
        self.rules = RuleRetriever.from_file(config.resolve_path(config.rules_path))
        self.taxonomy = Taxonomy(config.resolve_path(config.taxonomy_path))
        self.gemini = GeminiClient(config)
        self.duplicates = DuplicateGuard(config.duplicate_ttl_seconds)

    def reload_knowledge(self) -> dict[str, int]:
        self.rules = RuleRetriever.from_file(self.config.resolve_path(self.config.rules_path))
        self.taxonomy = Taxonomy(self.config.resolve_path(self.config.taxonomy_path))
        return {"rules_loaded": len(self.rules.documents), "categories_loaded": len(self.taxonomy.categories)}

    def triage(self, request: TriageRequest) -> TriageResponse:
        started = time.perf_counter()
        player_messages = [m for m in request.messages if m.role in {"player", "reported_player"}]
        source_text = " ".join(normalize_text(m.text) for m in player_messages if m.text.strip())
        if not source_text:
            source_text = " ".join(normalize_text(m.text) for m in request.messages if m.text.strip())
        normalized_text = normalize_text(source_text)

        has_evidence = bool(
            request.images_count > 0
            or any(m.attachments > 0 for m in request.messages)
            or "<url>" in normalized_text.lower()
            or "http://" in normalized_text.lower()
            or "https://" in normalized_text.lower()
        )

        lang = detect_language(normalized_text, request.lang, request.server_lang)
        category, priority, confidence, classifier_reasons = classify(
            normalized_text,
            request.kind,
            self.taxonomy,
            rule_refs_raw=request.rule_refs_raw,
            has_evidence=has_evidence,
        )
        refs_from_player = extract_rule_refs(request.rule_refs_raw, normalized_text)
        nickname = extract_nickname(normalized_text, request.reported_nick)

        retrieval_query = " ".join(part for part in [normalized_text, " ".join(refs_from_player)] if part)
        hits = self.rules.search(retrieval_query, top_k=4)
        if not hits:
            category_rule_hints = self.taxonomy.category_rules.get(category, [])
            if category_rule_hints:
                hits = self.rules.search(" ".join(category_rule_hints[:2]), top_k=3)

        if hits:
            top_score = hits[0].score
            relevant_hits = [h for h in hits if h.score >= top_score * 0.45]
            cited_refs = list(dict.fromkeys(hit.rule_id for hit in relevant_hits))
        else:
            cited_refs = []

        spam = detect_spam(normalized_text)
        target_id = request.reported_hash or request.reported_nick
        duplicate = self.duplicates.check_and_add(
            request.reporter_hash,
            request.kind,
            normalized_text,
            request.created_at,
            reported_id=target_id,
        )
        is_spam_or_duplicate = spam or duplicate

        escalate, escalation_reasons = must_escalate(normalized_text, category=category, taxonomy=self.taxonomy)
        needs_human = escalate
        reasons = list(classifier_reasons) + escalation_reasons
        if request.kind == "complaint":
            needs_human = True
            reasons.append("жалоба требует проверки фактов и доказательств администратором")
        if request.kind == "complaint" and not has_evidence:
            reasons.append("в выгрузке не обнаружены вложения/скриншоты; доказательства нужно запросить или проверить")
            needs_human = True
        if not hits and request.kind == "complaint":
            reasons.append("не найден подтверждённый фрагмент правил; автоматический вывод о нарушении запрещён")
        if lang == "und":
            needs_human = True
            reasons.append("язык обращения не удалось надёжно определить")
        if duplicate:
            reasons.append("обнаружен повтор обращения в пределах окна дедупликации")
        if spam:
            reasons.append("обнаружены признаки рекламного/спам-сообщения")
        if any(word in normalized_text.lower() for word in ("верни", "вернуть", "компенсац", "накаж", "забан", "мут")) and request.kind == "complaint":
            needs_human = True
            reasons.append("решение о наказании или компенсации принимает администратор")

        if is_spam_or_duplicate:
            reply_type = "duplicate" if duplicate else "generic"
        elif cited_refs:
            reply_type = "rule"
        elif needs_human:
            reply_type = "human"
        else:
            reply_type = "generic"

        refs_text = ", ".join(cited_refs[:3]) if cited_refs else ""
        draft_text = STATIC_REPLIES.get(lang, STATIC_REPLIES["ru"])[reply_type].format(refs=refs_text)
        draft_cited = cited_refs[:3] if reply_type == "rule" and cited_refs else []

        llm_used = False
        input_tokens = output_tokens = 0
        llm_model: str | None = None
        if self.config.llm_enabled and not is_spam_or_duplicate:
            llm_result = self.gemini.draft(
                lang=lang,
                kind=request.kind,
                category=category,
                priority=priority,
                message_text=normalized_text,
                rule_hits=[hit.as_dict() for hit in hits],
                needs_human=needs_human,
            )
            if llm_result:
                try:
                    candidate = json.loads(llm_result.text)
                    candidate_text = normalize_text(candidate.get("text", ""))
                    allowed_all = {hit.rule_id for hit in hits}
                    candidate_cited = [item for item in candidate.get("cited", []) if item in allowed_all]
                    risky_terms = (
                        "разбан гарант", "вернём деньги", "вернем деньги",
                        "игрок будет наказан", "получит бан", "накажем игрока",
                        "будет заблокирован", "разблокируем",
                    )
                    contains_risky_promise = any(term in candidate_text.lower() for term in risky_terms)
                    mentioned_refs = re.findall(r"\b\d+(?:\.\d+)+\b", candidate_text)
                    contains_unknown_ref = any(ref not in allowed_all for ref in mentioned_refs)
                    if candidate_text and not contains_risky_promise and not contains_unknown_ref:
                        draft_text = candidate_text
                        draft_cited = candidate_cited
                        llm_used = True
                        input_tokens = llm_result.input_tokens
                        output_tokens = llm_result.output_tokens
                        llm_model = llm_result.model
                except (ValueError, TypeError, AttributeError):
                    pass

        if is_spam_or_duplicate:
            confidence = max(confidence, 0.9 if duplicate else 0.86)
        if needs_human:
            confidence = min(confidence, 0.82)

        cost_usd: float | None = 0.0 if not self.config.llm_enabled else None
        cost_basis = "local_no_external_api" if not self.config.llm_enabled else "not_available"
        if llm_used and self.config.gemini_input_usd_per_1m is not None and self.config.gemini_output_usd_per_1m is not None:
            cost_usd = (
                input_tokens * self.config.gemini_input_usd_per_1m
                + output_tokens * self.config.gemini_output_usd_per_1m
            ) / 1_000_000
            cost_basis = "gemini_usage_metadata_and_configured_rates"

        processing = {
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "llm_used": llm_used,
            "llm_model": llm_model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": round(cost_usd, 8) if cost_usd is not None else None,
            "cost_basis": cost_basis,
            "rag_rules_loaded": len(self.rules.documents),
        }
        extracted: dict[str, Any] = {
            "nickname": nickname,
            "rule_refs": refs_from_player,
            "has_evidence": has_evidence,
            "server": request.region,
        }
        return TriageResponse(
            ticket_id=request.ticket_id or str(uuid.uuid4()),
            lang=lang,
            category=category,
            priority=priority,
            is_spam_or_duplicate=is_spam_or_duplicate,
            extracted=extracted,
            draft_reply={"lang": lang, "text": draft_text, "cited": draft_cited},
            needs_human=needs_human,
            confidence=round(max(0.0, min(1.0, confidence)), 2),
            reasons=reasons,
            retrieval=[hit.as_dict() for hit in hits],
            processing=processing,
        )


copilot = SupportCopilot()
