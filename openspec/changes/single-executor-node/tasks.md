# Tasks

## 1. Спецификация
- [x] 1.1 Дельта `agent-orchestrator`: ADDED FR-AG-14
- [ ] 1.2 `openspec validate single-executor-node --strict` (нужен Node.js ≥ 22.12), согласовать с командой

## 2. Код
- [x] 2.1 Один узел `execute` вместо пяти; `family()` задаёт метку в `trace` и в `llm_calls[].node`
- [x] 2.2 Рёбра `resolve_followup`, `agent`, `route` ведут в `execute`

## 3. Проверка
- [x] 3.1 Тесты `test_fr_ag_14_*`
- [ ] 3.2 Прогон в LangGraph Studio по списку запросов
