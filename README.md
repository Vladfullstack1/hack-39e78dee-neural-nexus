# Grand Mobile Support Copilot (Decentrathon 6.0)

Готовое решение для кейса **Support Copilot (Grand Mobile)**: автоматический триаж обращений игроков (`complaint` и `report`), нормализация текста (включая legacy CP1251 казахские символы), классификация по 14 официальным категориям и 4 уровням приоритета `taxonomy.json`, гибридный RAG-поиск по официальной базе `rules.json` (BM25 + точный матчинг пунктов правил, разделов ОП/ЖБ/GZ и RP-терминов), генерация безопасных черновиков ответа на языке обращения (`ru`, `kk`, `uz`, `en`), контроль эскалации (`needs_human`) и пакетный инференс по JSONL/JSONL.GZ.

## Ключевые возможности

- **`POST /triage`**: определяет язык (`ru`, `kk`, `uz`, `en`, `und`), категорию (из 14 категорий официального `taxonomy.json`), приоритет (`p0`–`p3`), флаг `is_spam_or_duplicate`, извлечённые сущности (`nickname`, `rule_refs`, `has_evidence`, `server`), черновик ответа с подтверждёнными цитатами правил (`cited`), `needs_human`, `confidence`, `reasons`, `retrieval` и метрики обработки (`latency_ms`, `estimated_cost_usd`).
- **Гибридный RAG по `data/rules.json`**: индексирует все 11 разделов правил Grand Mobile (включая Общие правила игры, Правила жалоб `ЖБ`, Правила Зелёной Зоны `GZ` и глоссарий RP-терминов `DM`, `DB`, `SK`, `TK`, `RK`, `PG`, `MG`, `NonRP`) с дедупликацией по `rule_id` и защитой от вымышленных цитат.
- **Нормализация казахской кодировки и плейсхолдеров**: исправляет искажённые CP1251-символы (`ќ→қ`, `ґ→ғ`, `ѕ→ң`, `є→ә`, `ј→ө`, `ў→ұ`, `ї→ү`) и корректно обрабатывает токены анонимизации (`<url>`, `<digits>`, `<email>`, `<handle>`).
- **Безопасная эскалация (`needs_human`)**: автоматически передаёт администратору все жалобы на игроков (`complaint`), жалобы на администрацию (`complaint.admin`), вопросы по аккаунту/имуществу (`report.account`) и платежам/донату (`report.payment`). Черновик никогда не обещает автоматический бан, разбан или возврат средств.
- **Дедупликация (`DuplicateGuard`)**: обнаруживает повторные и перефразированные обращения от одного `reporter_hash` (и по одному `reported_hash`/`reported_nick`) в рамках часового окна TTL.
- **Веб-панель администратора и SQLite-очередь**: интерфейс на `GET /` для интерактивной проверки обращений, редактирования черновика и сохранения обратной связи (`POST /admin/feedback`, `GET /admin/queue`, `GET /admin/feedback/stats`).

## Быстрый старт

### Локальный запуск (Linux / macOS / Windows)

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows CMD:
# .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

На Windows также можно запустить `START_WINDOWS.bat`.
- Веб-интерфейс: `http://127.0.0.1:8000`
- Swagger UI (`/docs`): `http://127.0.0.1:8000/docs`
- Health-check (`/health`): `http://127.0.0.1:8000/health`

### Запуск через Docker

```bash
docker compose up --build
```

## Пакетное предсказание и оценка метрик

Прогон на валидационной выборке `tickets_valid.jsonl`:

```bash
python -m app.predict --in data/tickets_valid.jsonl --out pred_valid.jsonl
python -m app.evaluate --in data/tickets_valid.jsonl --pred pred_valid.jsonl --out reports/evaluation.json
```

Или через `Makefile`:

```bash
make predict IN=data/tickets_valid.jsonl OUT=pred_valid.jsonl
make evaluate IN=data/tickets_valid.jsonl PRED=pred_valid.jsonl
```

### Результаты на `tickets_valid.jsonl` (1 000 обращений)

- **Обработано**: 1 000 / 1 000 без ошибок
- **Средняя задержка (`avg_latency_ms`)**: ~1.6 мс
- **p95 задержка (`p95_latency_ms`)**: ~2.7 мс
- **Покрытие правилами (`rule_retrieval_coverage`)**: 93.8%
- **Доля черновиков с цитатами (`draft_citation_rate`)**: 93.7%
- **Доля передачи человеку (`needs_human_rate`)**: 81.8%
- **Внешние затраты API (`avg_estimated_cost_usd`)**: $0.00 (локальный детерминированный режим)

## Запуск тестов

```bash
pytest -q
```

Все 13 модульных тестов (`tests/test_core.py`) проходят успешно.

## Структура репозитория

```text
app/
  main.py           FastAPI-сервис и маршруты API
  service.py        Основной пайплайн триажа, safety gates и DuplicateGuard
  rag.py            Парсер официального rules.json и гибридный BM25 retriever
  classifier.py     Определение языка, таксономия, классификация категорий и приоритетов
  normalization.py  Нормализация CP1251 казахских букв, токенизация и морфология
  llm.py            Опциональный клиент Gemini API со строгой JSON-схемой
  predict.py        Пакетный инференс для JSONL / JSONL.GZ
  evaluate.py       Расчёт метрик качества и производительности
  storage.py        SQLite-хранилище очереди и обратной связи
  static/index.html Веб-интерфейс копайлота поддержки
data/
  rules.json        Официальный свод правил Grand Mobile
  taxonomy.json     Официальная таксономия категорий и приоритетов
reports/
  evaluation.json   Отчёт метрик на валидационном наборе
tests/
  test_core.py      Набор модульных тестов
```
