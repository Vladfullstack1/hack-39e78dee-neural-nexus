from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Iterator

from .schemas import TriageRequest
from .service import SupportCopilot


def open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8-sig")
    return path.open("r", encoding="utf-8-sig")


def iter_jsonl(path: Path) -> Iterator[dict]:
    with open_text(path) as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    yield value
            except json.JSONDecodeError as exc:
                print(f"[WARN] пропущена некорректная строка {line_no}: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run triage predictions for JSONL/JSONL.GZ input")
    parser.add_argument("--in", dest="input_path", required=True, type=Path)
    parser.add_argument("--out", dest="output_path", required=True, type=Path)
    args = parser.parse_args()
    if not args.input_path.exists():
        print(f"Файл не найден: {args.input_path}", file=sys.stderr)
        return 2

    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    copilot = SupportCopilot()
    count = 0
    total_latency = 0.0
    with args.output_path.open("w", encoding="utf-8") as output:
        for row in iter_jsonl(args.input_path):
            try:
                # Preserve known input fields; the Pydantic model filters unexpected columns.
                request = TriageRequest.model_validate(row)
                result = copilot.triage(request).model_dump()
                result["ticket_id"] = row.get("ticket_id")
                result["kind"] = row.get("kind", "report")
                output.write(json.dumps(result, ensure_ascii=False) + "\n")
                count += 1
                total_latency += float(result.get("processing", {}).get("latency_ms", 0))
                if count % 1000 == 0:
                    print(f"Обработано: {count}", file=sys.stderr)
            except Exception as exc:
                print(f"[WARN] ошибка обработки ticket_id={row.get('ticket_id')}: {type(exc).__name__}", file=sys.stderr)
                fallback = {"ticket_id": row.get("ticket_id"), "error": type(exc).__name__}
                output.write(json.dumps(fallback, ensure_ascii=False) + "\n")
    print(json.dumps({"processed": count, "mean_latency_ms": round(total_latency / max(1, count), 2), "output": str(args.output_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
