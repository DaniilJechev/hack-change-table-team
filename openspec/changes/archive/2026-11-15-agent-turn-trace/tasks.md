> Закрыто и архивировано 15.11.2026 по поручению пользователя. Дельты применены к базовым спецификациям.
> Исторические незавершённые пункты оставлены без галочек: они не объявляются выполненными.
> Дальнейшие проверки и развитие: [OPENSPEC_STATUS.md](../../../../docs/OPENSPEC_STATUS.md).
> Это поручение не является подтверждением согласования с другими членами команды.

# Tasks

## 1. Спецификация
- [x] 1.1 Дельты `agent-orchestrator` (FR-AG-15, FR-AG-16) и `agent-traces` (FR-TR-01…05)
- [x] 1.2 `openspec validate agent-turn-trace --strict`
- [ ] 1.3 Согласовать с командой

## 2. Агент
- [x] 2.1 `buddy/graph/turn.py`: `turn` из `llm_calls` и состояния; поле `turn` вместо `prompt`
- [x] 2.2 Studio и тесты FR-AG-10/FR-AG-15
- [x] 2.3 `buddy/data/traces.py`: фоновая отправка `turn` в backend, сбой — только в лог

## 3. Backend
- [x] 3.1 Миграция `00007_agent_traces.sql`, view `metrics_agent`
- [x] 3.2 Store и REST `POST/GET /api/v1/agent-traces`, срок хранения
- [x] 3.3 Роль метрик читает `metrics_agent`; панель Grafana

## 4. Проверка
- [x] 4.1 `make test`, покрытие > 80 % для agent и backend
- [x] 4.2 Демо: ход в боте → запись в `agent_traces`
