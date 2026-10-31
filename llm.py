from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import Settings


@dataclass
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


class GeminiClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def draft(self, *, lang: str, kind: str, category: str, priority: str, message_text: str,
              rule_hits: list[dict[str, Any]], needs_human: bool, timeout: float | None = None) -> LLMResult | None:
        if not self.settings.llm_enabled:
            return None
            
        allowed_ids = [str(hit["rule_id"]) for hit in rule_hits]
        rules_text = "\n".join(f"[{hit['rule_id']}] {hit.get('title', '')} {hit.get('text', '')}" for hit in rule_hits)
        
        prompt = f"""Составь короткий ЧЕРНОВИК ответа службы поддержки Grand Mobile.
Верни только валидный JSON с ключами text (строка) и cited (массив строк).
Язык ответа: {lang}. Тип: {kind}. Категория: {category}. Приоритет: {priority}.
Обращение игрока: {message_text[:2500]}

Найденные фрагменты официальных правил:
{rules_text or '[Правила не найдены]'}

Разрешённые ID для cited: {json.dumps(allowed_ids, ensure_ascii=False)}.
Нужен человек: {str(needs_human).lower()}.

Ограничения:
- Не выдумывай правила, номера пунктов, факты, наказания или решения.
- Не обещай разбан, возврат денег, компенсацию или наказание другого игрока.
- Если правило не найдено, cited должен быть пустым и нельзя утверждать, что нарушение доказано.
- Если нужен человек, сообщи, что обращение передано администратору на проверку.
- Не публикуй персональные данные. Тон нейтральный и вежливый."""

        schema = {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "cited": {
                    "type": "array",
                    "items": {"type": "string"}
                },
            },
            "required": ["text", "cited"],
        }
        
        body = {
            "system_instruction": {
                "parts": [{"text": "Ты ассистент поддержки. Не выдумывай факты. Строго соблюдай предоставленные данные и возвращай строго JSON."}]
            },
            "contents": [{
                "role": "user",
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 350,
                "responseMimeType": "application/json",
                "responseSchema": schema
            },
        }
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.settings.gemini_model}:generateContent?key={self.settings.gemini_api_key}"
        request = urllib.request.Request(
            url, 
            data=json.dumps(body).encode("utf-8"), 
            headers={"Content-Type": "application/json"}, 
            method="POST"
        )
        
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.settings.gemini_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
                
            candidates = payload.get("candidates", [])
            if not candidates:
                return None
                
            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            if not parts:
                return None
                
            raw_text = next((part.get("text", "") for part in parts if part.get("text")), "")
            if not raw_text:
                return None
                
            # Безопасный парсинг JSON из ответа модели
            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError:
                # Попытка очистить Markdown блоки при наличии (например, ```json ... ```)
                cleaned = raw_text.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                parsed = json.loads(cleaned.strip())
                
            response_text = str(parsed.get("text", ""))
            raw_cited = parsed.get("cited", [])
            
            # Фильтрация и проверка cited на вхождение в разрешенные ID
            cited = [str(value) for value in raw_cited if str(value) in allowed_ids]
            
            usage = payload.get("usageMetadata", {})
            input_tokens = int(usage.get("promptTokenCount", 0))
            output_tokens = int(usage.get("candidatesTokenCount", 0))
            model_name = str(payload.get("modelVersion", self.settings.gemini_model))
            
            final_payload = {
                "text": response_text,
                "cited": cited
            }
            
            return LLMResult(
                text=json.dumps(final_payload, ensure_ascii=False),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model=model_name
            )
            
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError, json.JSONDecodeError):
            return None