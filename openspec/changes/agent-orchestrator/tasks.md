# Tasks

## 0. Спецификация
- [ ] 0.1 Согласовать с Даниилом пункты 1–5 раздела «Что согласовать» в `proposal.md`, с Ильёй — пункт 6
- [ ] 0.1.1 Добавить MODIFIED FR-MEM-05 (правила уточнений — запасной вариант агента), если пункт 2 принят
- [ ] 0.2 Архивировать `stage-4-langgraph-memory` до этого изменения; `openspec validate agent-orchestrator --strict`

## 1. LLM
- [x] 1.1 `CompatibleLLM.chat(messages, tools)`: tool calls, без рассуждений, `data_collection: deny`
- [x] 1.2 `FakeLLM.chat` по сценарию ответов для тестов

## 2. Агент
- [x] 2.1 Инструменты из кейсов Executors, набор по роли, проверка аргументов (FR-AG-03)
- [x] 2.2 Цикл: до 3 обращений к LLM, 4 вызовов, 20 с (FR-AG-08)
- [x] 2.3 Проверка ответа и подстановка ссылок кодом (FR-AG-04, FR-AG-09)
- [x] 2.4 Запасной вариант: результат инструмента или Router (FR-AG-05)
- [x] 2.5 Узел `agent` в графе, быстрый путь и отказ по персональным данным (FR-AG-01, FR-AG-02)

## 3. Память
- [x] 3.1 Ход хранит текст пользователя и показанный ответ, до 600 символов, без секретов (FR-MEM-04)

## 4. Конфигурация
- [x] 4.1 `AGENT_MODE` в настройках и `.env.example`

## 5. Тесты и оценка
- [x] 5.1 Тесты `test_fr_ag_*` по каждому сценарию; покрытие `agent` > 80 %
- [x] 5.2 `evals/agent/cases.jsonl` и `evals/run_agent.py` (FR-AG-07)
- [x] 5.3 Прогнать `evals/run_agent.py` на DeepSeek и Haiku (12.11, отчёт `evals/agent/report.json`)
- [x] 5.4 Имя из карточки или статус доступа без инструмента → повтор с `tool_choice: "required"` (FR-AG-05, FR-AG-09)

## 6. Трассировка и LangGraph Studio
- [x] 6.1 `llm_calls` и `prompt` в состоянии, запись обращений к LLM (FR-AG-10)
- [x] 6.2 `langgraph.json`, `buddy/studio.py` (чат, персоны intern/mentor), `make studio`
- [x] 6.3 Тесты `test_fr_ag_10_*`

## 7. Значки доступов и стиль (13.11)
- [x] 7.1 FR-AG-12: правило значков в `SYSTEM`; `unknown` → ❌ в `access/service.py`
- [x] 7.2 FR-AG-13: стилевой блок в `SYSTEM`, краткие правила в промптах брифа и playbook
- [x] 7.3 Тесты `test_fr_ag_12_*`, `test_fr_ag_13_*`; кейс доступов со значками в `evals/agent/cases.jsonl`
- [ ] 7.4 Прогнать `evals/run_agent.py` на реальной LLM (вручную)
