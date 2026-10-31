from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")


def _optional_float(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
    use_llm: bool = os.getenv("USE_LLM", "false").lower() in {"1", "true", "yes", "on"}
    gemini_timeout_seconds: float = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "4.0"))
    gemini_input_usd_per_1m: float | None = _optional_float("GEMINI_INPUT_USD_PER_1M")
    gemini_output_usd_per_1m: float | None = _optional_float("GEMINI_OUTPUT_USD_PER_1M")
    rules_path: Path = Path(os.getenv("RULES_PATH", "data/rules.json"))
    taxonomy_path: Path = Path(os.getenv("TAXONOMY_PATH", "data/taxonomy.json"))
    duplicate_ttl_seconds: int = int(os.getenv("DUPLICATE_TTL_SECONDS", "3600"))

    @property
    def llm_enabled(self) -> bool:
        return self.use_llm and bool(self.gemini_api_key)

    def resolve_path(self, path: Path) -> Path:
        return path if path.is_absolute() else ROOT_DIR / path


settings = Settings()
