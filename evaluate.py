from __future__ import annotations

import argparse
import gzip
import json
import statistics
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    rows: list[dict[str, Any]] = []
    with opener(path, "rt", encoding="utf-8-sig") as stream:
        for line in stream:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def macro_f1(y_true: list[str], y_pred: list[str]) -> float | None:
    if not y_true:
        return None
    labels = sorted(set(y_true) | set(y_pred))
    values = []
    for label in labels:
        tp = sum(a == label and b == label for a, b in zip(y_true, y_pred))
        fp = sum(a != label and b == label for a, b in zip(y_true, y_pred))
        fn = sum(a == label and b != label for a, b in zip(y_true, y_pred))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        values.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return sum(values) / len(values)


def evaluate(source_path: Path, pred_path: Path) -> dict[str, Any]:
    source = read_jsonl(source_path)
    preds = read_jsonl(pred_path)
    pred_by_id = {row.get("ticket_id"): row for row in preds if row.get("ticket_id") is not None}
    paired = [(row, pred_by_id.get(row.get("ticket_id"))) for row in source]
    paired = [(truth, pred) for truth, pred in paired if pred is not None]
    latencies = [float(pred.get("processing", {}).get("latency_ms", 0)) for _, pred in paired]
    latencies_nonzero = [x for x in latencies if x > 0]
    sorted_latencies = sorted(latencies_nonzero)
    p95 = sorted_latencies[min(len(sorted_latencies) - 1, int(0.95 * len(sorted_latencies)))] if sorted_latencies else None
    citation_count = sum(bool(pred.get("draft_reply", {}).get("cited")) for _, pred in paired)
    retrieved_count = sum(bool(pred.get("retrieval")) for _, pred in paired)
    human_count = sum(bool(pred.get("needs_human")) for _, pred in paired)
    spam_count = sum(bool(pred.get("is_spam_or_duplicate")) for _, pred in paired)
    category_pairs = [(truth.get("category"), pred.get("category")) for truth, pred in paired if truth.get("category") not in (None, "")]
    priority_pairs = [(truth.get("priority"), pred.get("priority")) for truth, pred in paired if truth.get("priority") not in (None, "")]
    category_accuracy = sum(a == b for a, b in category_pairs) / len(category_pairs) if category_pairs else None
    priority_f1 = macro_f1([str(a) for a, _ in priority_pairs], [str(b) for _, b in priority_pairs]) if priority_pairs else None
    costs = [pred.get("processing", {}).get("estimated_cost_usd") for _, pred in paired]
    numeric_costs = [float(x) for x in costs if isinstance(x, (int, float))]
    return {
        "source_rows": len(source),
        "prediction_rows": len(preds),
        "matched_rows": len(paired),
        "supervised_labels_available": {"category": len(category_pairs), "priority": len(priority_pairs)},
        "category_accuracy": category_accuracy,
        "priority_macro_f1": priority_f1,
        "avg_latency_ms": round(statistics.mean(latencies_nonzero), 2) if latencies_nonzero else None,
        "p95_latency_ms": round(p95, 2) if p95 is not None else None,
        "rule_retrieval_coverage": round(retrieved_count / max(1, len(paired)), 4),
        "draft_citation_rate": round(citation_count / max(1, len(paired)), 4),
        "needs_human_rate": round(human_count / max(1, len(paired)), 4),
        "spam_or_duplicate_rate": round(spam_count / max(1, len(paired)), 4),
        "avg_estimated_cost_usd": round(statistics.mean(numeric_costs), 8) if numeric_costs else None,
        "cost_samples": len(numeric_costs),
        "note": "Если category/priority отсутствуют в исходном файле (как в tickets_valid.jsonl по ТЗ), Accuracy и Macro-F1 не рассчитываются. Это не ноль и не оценка модели.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate prediction coverage and available labels")
    parser.add_argument("--in", dest="input_path", required=True, type=Path)
    parser.add_argument("--pred", dest="pred_path", required=True, type=Path)
    parser.add_argument("--out", dest="out_path", type=Path, default=Path("reports/evaluation.json"))
    args = parser.parse_args()
    report = evaluate(args.input_path, args.pred_path)
    args.out_path.parent.mkdir(parents=True, exist_ok=True)
    args.out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
