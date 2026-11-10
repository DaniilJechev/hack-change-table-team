# AI-buddy

> **Решение о приёмке Этапа 1, 10.11.2026:** пользователь подтвердил использование исключительно синтетических фикстур; имитации достаточно. Реальные аккаунты, корпоративные API-вызовы и OAuth Miro не требуются для закрытия этапа. Упоминания отложенной live-приёмки ниже описывают прежние критерии и опциональное расширение. Код проверен; закрытие Этапа 1 выполняется слиянием PR №4 в main.


Telegram-помощник для стажёров. Кейс Hack&Change 2026. Этап 0 объединён в main;
часть Ильи на Этапе 1 добавляет PostgreSQL-пользователей, REST-привязку и запуск MCP.
H0–H3 и подключение привязки к Telegram-обработчику ещё впереди.
Синтетические карточки не являются регламентами Авито.

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
Текущий Telegram-обработчик ещё не вызывает REST привязки и не открывает данные команды.

Backend готов к подключению: `make seed`, затем `make invite USER_ID=demo-daniil`.
Контракт и примеры — [backend/README.md](backend/README.md), схема — [OpenAPI](backend/openapi.yaml).
Опциональные готовые MCP поднимаются через `make mcp-up`; конфигурация, ограничения
чтения и проверка `make mcp-list` — в [mcp/README.md](mcp/README.md).

Выбран адаптер **OpenRouter**, модель по умолчанию в Compose `openrouter/auto`.
Для live LLM задайте `DEMO_MODE=false`, `LLM_PROVIDER=openrouter`, `LLM_MODEL` и `LLM_API_KEY`.
Для другого совместимого API: `LLM_PROVIDER=compatible` и `LLM_BASE_URL=https://ваш-сервис/v1`.
Провайдер и модель читаются только из env. Секреты не коммитятся; `.env.example` содержит пустые секреты.
В демо всегда используется FakeLLM; тесты не тратят API-бюджет. Ошибка или таймаут <=20 секунд возвращают шаблон.
`/start` вообще не обращается к LLM. Модель эмбеддингов задана env, скачивание и RAG — Этап 4.

## Тесты и покрытие

Нужны Python 3.12, Go >=1.24, uv 0.9.5 и Docker для интеграционных Go-тестов;
для OpenSpec Node >=22.12. Тесты создают отдельный временный PostgreSQL, а при
заданном `TEST_DATABASE_URL` используют в нём изолированные схемы.

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

- `backend/` — Go: миграции, пользователи, REST входа, /health и /ready; buddy-mcp — следующий этап.
- `agent/buddy/` — бот, конфигурация, схемы, LLM; пакеты под следующие этапы.
- `infra/` — pgvector init и Grafana provisioning.
- `seed/` — синтетические пользователи двух команд, карточка и черновой каталог.
- `evals/survey.md` — готовая анкета для стажёров. Результаты пока не собраны.
- `mcp/` — конфигурация готовых серверов чтения и проверенный набор инструментов.
- `fixtures/`, `data/playbook/` — место для данных следующих этапов.
- `openspec/` — спецификации и изменения; `docs/` — продукт, архитектура, roadmap.

Корпоративные сервисы — только чтение. Tools/list проверяется с синтетическими
credentials; реальные вызовы требуют подготовки тестовых аккаунтов и клиентов агента.
Начните с AGENTS.md и трёх документов в docs/. Поведение сверяйте с openspec/specs/.

## Ветка Этапа 1: Эмиль и Даниил

MCP-клиент, проверка инструментов, запись/replay и подготовка OAuth: [инструкция](mcp/CLIENTS.md).
Тестовые данные: [синтетический сценарий](seed/day_scenario.json), [подготовка живого окружения](seed/LIVE_SETUP.md).
Сейчас серверы и аккаунты ещё готовит команда: offline и локальные SDK-тесты не означают успешной живой интеграции.
Router/Executors, /demo next, бизнес-сценарии H0–H3 и backend этой задачей не реализуются.


### Привязка Telegram (Этап 1)

После запуска backend выполните `make seed`, затем `make invite USER_ID=demo-daniil`.
Передайте выданный одноразовый код адресату приватно. В личном чате бот принимает
`/start <код>`, повторный `/start` загружает профиль из backend. Роль и команда
определяются сервером; групповые сообщения не активируют приглашение.
Для локального агента нужны `BACKEND_URL` и `BUDDY_SERVICE_KEY` в `.env`, в Compose
они уже передаются. Без этих настроек остаётся автономный каркас. H0–H3 ещё не включены.
При недоступности backend бот отвечает безопасным шаблоном без LLM.

`seed/check_stage_one.py` — приёмка свежего изолированного Compose-проекта
`buddy-stage1-acceptance`: настоящий REST/PostgreSQL, offline Telegram и discovery
официальных GitHub/Jira/Calendar MCP. Запускается и в CI; секреты и код не печатает.
Приёмка не вызывает корпоративные tools/call и требует свежей тестовой БД.


### Этап 2: Router и демо (Эмиль + Даниил)

Реализованы Router с 16 кейсами RT-01, правила без LLM, каркас Executors с белыми списками
и лимитом 3 вызова. Это основа: бизнес-ответы H0–H3 пока явно шаблонные.

В личном чате:
- `/demo` — явно включить публичный синтетический пример без реальной привязки;
- `/demo next`, `/demo reset`, `/demo off` — следующий шаг, сброс и выход;
- `/ask` — восемь типов вопросов о команде; кнопки идут прямо в Executor;
- обычный текст после `/demo` или реальной привязки — маршрутизация запроса.

Демо изолировано по пользователям, имеет два шага и не обращается к внешним сервисам.
В live-режиме `/demo` отключён. Без активного демо требуется настоящий профиль из backend.
Сессии демо не переживают перезапуск. Публичный synthetic-профиль не заменяет реальную привязку.

Проверка из корня:
```sh
uv run --project agent --frozen --extra dev python seed/build_stage_two.py
uv run --project agent --frozen --extra dev python evals/run_router.py
uv run --project agent --frozen --extra dev python evals/run_router.py --dataset evals/router/holdout.jsonl
uv run --project agent --frozen --extra dev pytest agent/tests --cov=buddy
```

Карточка команды и каталог синтетические; правила доступа в них не являются регламентами Авито.
Контракты и cross-reference проверяются в `buddy/demo/contracts.py`.
Данные и отчёты оценки — `evals/router/`; полный доклад — `STAGE_2_EMIL_DANIIL_REPORT.md`.
Backend/buddy-mcp Ильи и бизнес-исполнители следующих этапов в это поручение не входят.
