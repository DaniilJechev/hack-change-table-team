# Понятный трейс хода и его хранение в PostgreSQL

## Why
В LangGraph Studio по узлу `agent` трудно понять, что произошло за ход: `prompt` — плоский список сообщений,
где история не отделена от текущего вопроса; `decision` в агентском пути — решение первого инструмента;
`response` — внутренний контракт бота (`actions`, `citations`, `entities`, `folded`, `links`). Трейсы живут
только в состоянии графа и пропадают после хода, поэтому разбирать живые ответы бота нельзя.

## What Changes
- ADDED FR-AG-15, FR-AG-16: состояние графа хранит поле `turn` — один читаемый JSON на ход: запрос, вход модели
  (системный промпт, доверенный контекст, история, текущее сообщение — раздельно), шаги агента с вызовами
  инструментов и их результатами, прочие обращения к LLM, ответ с источниками и кнопками, лимиты, итог.
  Заменяет поле `prompt` из FR-AG-10; `llm_calls` остаётся как сырой журнал. `ExecutorResponse` и бот не меняются.
- ADDED FR-TR-01…05 (capability `agent-traces`): агент после каждого хода отправляет `turn` в backend
  (`POST /api/v1/agent-traces`, только ключ сервиса), backend хранит его в таблице `agent_traces`
  с текстом пользователя и ответом, удаляет записи старше `AGENT_TRACE_RETENTION_DAYS` (по умолчанию 30),
  отдаёт их только по ключу сервиса; Grafana видит агрегат `metrics_agent` без текста.
  Сбой записи не мешает ответу.

## Impact
`agent/buddy/graph/` (новый `turn.py`, `build.py`, `state.py`), `agent/buddy/data/traces.py`, `agent/buddy/service.py`,
`agent/buddy/app.py`; `backend/migrations/00007_agent_traces.sql`, `backend/internal/store/`, `backend/internal/api/`;
`infra/` (панель Grafana), README. Корпоративные сервисы не затрагиваются. Демо-режим пишет трейсы, если
backend доступен, и работает без него. FR-AG-10 живёт в активном изменении `agent-orchestrator`: после его
архивации убрать из FR-AG-10 упоминание поля `prompt`.
