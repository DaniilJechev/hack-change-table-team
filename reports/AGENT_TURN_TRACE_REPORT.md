# Трейс хода агента: читаемый JSON и хранение в PostgreSQL

Изменение OpenSpec: `openspec/changes/agent-turn-trace` (FR-AG-15, FR-AG-16, FR-TR-01…05). Ветка
`feat/agent-improvements`, не закоммичено.

## Задача 2: понятный JSON хода (`turn`)

В состоянии графа появилось поле `turn`, `prompt` убран. `turn` собирается кодом из `llm_calls`
(`agent/buddy/graph/turn.py`), обновляется после узлов с LLM и окончательно — в `guard`.

| Поле | Что внутри |
|---|---|
| `request` | текст, роль, путь: `agent`, `router` (запасной), `followup`, `trigger` |
| `model_input` | `system_prompt`, `trusted_context`, `history` (прошлые реплики), `current_message`, `tools_offered` |
| `steps` | по шагу агента: `model_text`, `tool_calls` (инструмент, аргументы, результат, метки `S#`), `error` |
| `forced_refresh` | был ли принудительный цикл за свежими фактами (FR-AG-05) |
| `other_llm_calls` | Router, формулировка брифа, ответ playbook: узел, промпт, ответ |
| `answer` | текст, `sources` с номерами, подписи кнопок, `more_blocks` (если есть) |
| `limits`, `fallback`, `duration_ms`, `case` | лимиты 3 шага / 4 инструмента, запасной путь, время, кейс |

Во внутренний контракт бота (`ExecutorResponse`) изменения не вносились. `decision`/`response` остаются
в состоянии для бота, `llm_calls` — сырой журнал. В `turn` нет `entities`, `brief_id`, данных кнопок и ключа API.

## Задача 1: трейсы в PostgreSQL

- Агент (`agent/buddy/data/traces.py`): после каждого хода `AgentService.handle/trigger` отправляет запись
  в фоне; сбой → строка `agent trace not saved: <вид ошибки>`, ответ не меняется. Включается, если заданы
  `BACKEND_URL` и `BUDDY_SERVICE_KEY` (в Compose — всегда, в том числе в демо).
- Backend: миграция `00007_agent_traces.sql` (таблица `agent_traces`, `turn` в `jsonb`, без FK на users —
  демо-пользователей там нет), view `metrics_agent`.
- REST только по `X-Buddy-Key`: `POST /api/v1/agent-traces` (201, повтор id не создаёт дубль, тело ≤ 256 КиБ),
  `GET /api/v1/agent-traces?user_id=&limit=` (без `turn`, до 200), `GET /api/v1/agent-traces/{id}`.
  Описаны в `backend/openapi.yaml`.
- Срок хранения: `AGENT_TRACE_RETENTION_DAYS` (по умолчанию 30, 1…36500), старые строки удаляются при записи.
- Grafana: роль `buddy_metrics` читает только `metrics_agent`, не таблицу; панель «Агент · ходы по дням и путям».

## Проверка

| Что | Результат |
|---|---|
| `pytest` агента | 702 passed, покрытие 97 % (`turn.py`, `traces.py` — 100 %) |
| Go-тесты backend (в Docker, Go локально не установлен) | все пакеты ok, покрытие 82.5–100 % по пакетам; `gofmt`, `go vet` чистые |
| `openspec validate --all` / `--strict` для изменения | 29 passed / valid |
| Живой стек (Compose + DeepSeek): 2 хода в демо | 2 строки в `agent_traces`, история и текущий вопрос раздельно, `metrics_agent` считает |
| REST без ключа | 401 |
| LangGraph Studio | в состоянии есть `turn`, нет `prompt` |

Не проверено: панель Grafana в браузере (JSON валиден, view читается).

## Как проверить самому

```sh
docker compose up --build -d --wait
# написать боту в Telegram или в Studio (make studio), затем:
curl -s -H "X-Buddy-Key: $BUDDY_SERVICE_KEY" "http://127.0.0.1:$BACKEND_PORT/api/v1/agent-traces?limit=5"
curl -s -H "X-Buddy-Key: $BUDDY_SERVICE_KEY" "http://127.0.0.1:$BACKEND_PORT/api/v1/agent-traces/<id>"
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select * from metrics_agent"'
cd agent && uv run --extra dev pytest -q tests/test_turn_trace.py tests/test_agent_traces.py tests/test_graph_trace.py
```

## Осталось

- Согласовать изменение с командой (`tasks.md` 1.3), после архивации `agent-orchestrator` убрать
  `prompt` из текста FR-AG-10.
- `docs/2_ARCHITECTURE.md` не менялся: раздел о данных стоит дополнить таблицей `agent_traces` (зона Даниила).
