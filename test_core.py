import json
from dataclasses import replace
from pathlib import Path

from app.classifier import Taxonomy, classify, detect_language, extract_rule_refs
from app.config import settings
from app.normalization import normalize_text
from app.rag import RuleDocument, RuleRetriever, parse_rules
from app.schemas import TriageRequest
from app.service import DuplicateGuard, SupportCopilot


def test_kazakh_legacy_character_normalization():
    assert normalize_text("ќ ґ ѕ є ј ў ї") == "қ ғ ң ә ө ұ ү"


def test_language_detection_kazakh():
    assert detect_language("менің аккаунтым жұмыс істемейді") == "kk"


def test_language_detection_ignores_url_placeholder():
    assert detect_language("<url>\n\n2.1оп", hinted="en", server_lang="ru") == "ru"
    assert detect_language("7 kun ban 2.1 op <url> onadan sokyapti", hinted="pt", server_lang="uz") == "uz"


def test_parse_nested_rules():
    payload = {"sections": [{"id": "2", "title": "Жалпы ережелер", "rules": [{"id": "2.1", "text": "Ойыншыларды қорлауға тыйым салынады"}]}]}
    docs = parse_rules(payload)
    assert len(docs) == 1
    assert docs[0].rule_id == "2.1"


def test_parse_official_rules_json():
    copilot = SupportCopilot()
    assert len(copilot.rules.documents) > 200
    assert copilot.rules.has_rule("2.1")
    assert copilot.rules.has_rule("5.2")


def test_retriever_returns_matching_rule():
    retriever = RuleRetriever([RuleDocument("2.1", "Insults", "Оскорблять других игроков запрещено")])
    hits = retriever.search("игрок оскорблял меня")
    assert hits
    assert hits[0].rule_id == "2.1"


def test_extract_rule_refs():
    assert extract_rule_refs("см. пункт 2.1 и 11.3") == ["2.1", "11.3"]
    assert extract_rule_refs("откинули на 60 минут за 5.2 ОП") == ["5.2"]


def test_complaint_is_escalated_and_no_fabricated_citation(tmp_path: Path):
    empty_rules = tmp_path / "empty_rules.json"
    empty_rules.write_text(json.dumps({"rules": []}), encoding="utf-8")
    custom = replace(settings, rules_path=empty_rules, use_llm=False)
    copilot = SupportCopilot(custom)
    request = TriageRequest.model_validate({
        "kind": "complaint",
        "messages": [{"role": "player", "text": "Игрок оскорбил меня, скриншот есть", "attachments": 1}],
        "images_count": 1,
    })
    result = copilot.triage(request)
    assert result.needs_human is True
    assert result.draft_reply.cited == []
    assert result.retrieval == []


def test_duplicate_guard():
    guard = DuplicateGuard(3600)
    assert guard.check_and_add("player-1", "report", "Помогите, не работает вход на сервер") is False
    assert guard.check_and_add("player-1", "report", "Помогите, не работает вход на сервер") is True


def test_fuzzy_duplicate_for_same_reporter_within_hour():
    guard = DuplicateGuard(3600, similarity_threshold=0.7)
    first = "У меня не работает вход на игровой сервер помогите пожалуйста"
    second = "Помогите пожалуйста у меня не работает вход на сервер игровой"
    assert guard.check_and_add("same-player", "report", first, "2026-03-14T10:00:00") is False
    assert guard.check_and_add("same-player", "report", second, "2026-03-14T10:30:00") is True


def test_rag_rule_citation_uses_only_loaded_rule(tmp_path: Path):
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps(
            {"sections": [{"id": "2", "title": "Общие правила", "rules": [{"id": "2.1", "text": "Запрещено оскорблять других игроков"}]}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    custom = replace(settings, rules_path=rules_path, use_llm=False)
    copilot = SupportCopilot(custom)
    request = TriageRequest.model_validate({
        "kind": "complaint",
        "messages": [{"role": "player", "text": "Игрок оскорблял игроков в чате, скриншоты есть", "attachments": 1}],
        "images_count": 1,
    })
    result = copilot.triage(request)
    assert result.needs_human is True
    assert result.draft_reply.cited == ["2.1"]
    assert result.retrieval[0]["rule_id"] == "2.1"


def test_no_cross_player_duplicate_without_reporter_hash():
    guard = DuplicateGuard(3600)
    text = "Помогите, не работает вход на сервер, пожалуйста"
    assert guard.check_and_add(None, "report", text) is False
    assert guard.check_and_add(None, "report", text) is False


def test_official_taxonomy_classification():
    tax = Taxonomy(settings.resolve_path(settings.taxonomy_path))
    cat_cheat, prio_cheat, _, _ = classify("5.2 OP, soft, fraps: <url>", "complaint", tax, rule_refs_raw="5.2 OP")
    assert cat_cheat == "complaint.cheat"
    assert prio_cheat == "p1"

    cat_offtopic, prio_offtopic, _, _ = classify("помогите", "report", tax)
    assert cat_offtopic == "report.offtopic"
    assert prio_offtopic == "p3"

    cat_help, prio_help, _, _ = classify("застрял в текстурах помогите выбраться", "report", tax)
    assert cat_help == "report.help"
    assert prio_help == "p2"

    cat_pay, prio_pay, _, _ = classify("оплатил гк, не пришло", "report", tax)
    assert cat_pay == "report.payment"
    assert prio_pay == "p0"
