# Один узел-исполнитель в графе

## Why
Пять узлов `brief`, `playbook`, `team`, `access`, `template` в графе делают одно и то же: вызывают `Registry.execute` по кейсу. Различие только в имени, которое попадает в `trace` и в трассу LLM. Лишние узлы и рёбра усложняют схему и LangGraph Studio, но не добавляют поведения.

## What Changes
- ADDED FR-AG-14: в графе один узел-исполнитель `execute`; семейство кейса (`brief`, `playbook`, `team`, `access`, `template`) определяет код и записывает в `trace` и в поле `node` обращений к LLM. Заменяет формулировку «один узел на семейство» из ST4-GRAPH-1.
- Остальные узлы (`load_memory`, `resolve_followup`, `agent`, `route`, `guard`, `save_memory`) и рёбра между ними не меняются.

## Impact
`agent/buddy/graph/build.py`, `agent/buddy/graph/__init__.py`, `agent/buddy/graph/state.py`, `agent/tests/test_graph_trace.py`. Белые списки инструментов, запасные варианты без LLM, значения `trace` и `llm_calls[].node` для пользователя и тестов не меняются. `ST4-GRAPH-1` живёт в активном изменении `stage-4-langgraph-memory`: после его архивации заменить в нём упоминание узлов семейств.
