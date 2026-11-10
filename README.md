# AI-buddy

Telegram-помощник для стажёров. Кейс Hack&Change 2026. Техническая часть **Этапа 0: каркас** реализована и проверена.
H0–H3, привязка аккаунтов и MCP ещё не реализованы. Синтетические карточки не являются регламентами Авито.

## Запуск

Нужны Docker Engine с Compose v2 и GNU Make. Python/Go на хосте для контейнерного запуска не нужны.

```sh
make up
```

На Windows без Make та же команда:

```powershell
docker compose up --build -d --wait --wait-timeout 180
```

Пять сервисов: PostgreSQL 16 + pgvector, Redis 7, Grafana, Go backend и Python agent.
Порты доступны только локально: backend 8080, agent 8081, Grafana 3000.
Проверки: `http://localhost:8080/health`, `http://localhost:8081/health`, `http://localhost:3000/api/health`.
Grafana: пользователь buddy; локальный демо-пароль buddy_local_demo, если не переопределён в .env.

Без `.env` каркас работает offline, без Telegram и без внешних аккаунтов. В контейнере:

```sh
docker compose exec agent python -m buddy --smoke
```

Это проверка реального aiogram Dispatcher с фейковым транспортом и fake LLM; полный сценарий H0–H3 будет позже.
`make demo` использует `.env.example` и не требует аккаунтов (переменные shell имеют приоритет в Compose).

## Telegram и LLM

Скопируйте `.env.example` в `.env`, задайте `TELEGRAM_BOT_TOKEN` от @BotFather, перезапустите agent.
Не запускайте второй polling-процесс для того же токена. Бот отвечает на `/start` и `/help`.
Код после `/start` на Этапе 0 не создаёт привязку и не открывает данные команды.

Выбран адаптер **OpenRouter**, модель по умолчанию в Compose `openrouter/auto`.
Для live LLM задайте `DEMO_MODE=false`, `LLM_PROVIDER=openrouter`, `LLM_MODEL` и `LLM_API_KEY`.
Для другого совместимого API: `LLM_PROVIDER=compatible` и `LLM_BASE_URL=https://ваш-сервис/v1`.
Провайдер и модель читаются только из env. Секреты не коммитятся; `.env.example` содержит пустые секреты.
В демо всегда используется FakeLLM; тесты не тратят API-бюджет. Ошибка или таймаут <=20 секунд возвращают шаблон.
`/start` вообще не обращается к LLM. Модель эмбеддингов задана env, скачивание и RAG — Этап 4.

## Тесты и покрытие

Нужны Python 3.12, Go >=1.24, uv 0.9.5; для OpenSpec Node >=22.12.

```sh
make setup
make test
make cover
make validate
```

Без Make: `uv sync --project agent --frozen --extra dev --python 3.12`, затем `python scripts/check.py coverage`.
`uv.lock` фиксирует версии зависимостей. Python-тесты используют фейки и httpx MockTransport.
Порог покрытия строго >80% отдельно для backend и agent; в CI проверяются также lint, OpenSpec и Compose.
`make smoke` проверяет offline /start, здоровье пяти контейнеров, HTTP backend/agent/Grafana, PING Redis и SQL с типом vector. `SMOKE_ARGS=--demo` дополнительно проверяет DEMO_MODE.
`make ps`, `make logs` и `make check-config` показывают состояние, логи и проверяют конфигурацию.
`make down` останавливает сервисы, сохраняя тома с данными.
`make ingest` и `make record-fixtures` завершаются явной ошибкой до реализации H3 и MCP.
PostgreSQL применяет init.sql только на новом томе; изменение пароля в .env не меняет пароль существующей БД.

## Структура и дальнейшая работа

- `backend/` — Go HTTP /health, место для REST, buddy-mcp и миграций.
- `agent/buddy/` — бот, конфигурация, схемы, LLM; пакеты под следующие этапы.
- `infra/` — pgvector init и Grafana provisioning.
- `seed/` — синтетические пользователи двух команд, карточка и черновой каталог.
- `evals/survey.md` — готовая анкета для стажёров. Результаты пока не собраны.
- `fixtures/`, `mcp/`, `data/playbook/` — место для данных следующих этапов.
- `openspec/` — спецификации и изменения; `docs/` — продукт, архитектура, roadmap.

Корпоративные сервисы — только чтение. Сейчас их инструменты вообще не подключены.
Начните с AGENTS.md и трёх документов в docs/. Поведение сверяйте с openspec/specs/.

## Ветка Этапа 1: Эмиль и Даниил

MCP-клиент, проверка инструментов, запись/replay и подготовка OAuth: [инструкция](mcp/CLIENTS.md).
Тестовые данные: [синтетический сценарий](seed/day_scenario.json), [подготовка живого окружения](seed/LIVE_SETUP.md).
Сейчас серверы и аккаунты ещё готовит команда: offline и локальные SDK-тесты не означают успешной живой интеграции.
Router/Executors, /demo next, бизнес-сценарии H0–H3 и backend этой задачей не реализуются.
