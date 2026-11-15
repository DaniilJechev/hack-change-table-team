## MODIFIED Requirements

### Requirement: ST4-GRAPH-1 Детерминированный граф агента
Обработка запроса SHALL выполняться StateGraph LangGraph с узлами load_memory, resolve_followup, agent, route, execute, guard, save_memory. Режим агента и запасной путь SHALL выбираться кодом по FR-AG-01, FR-AG-05 и FR-AG-11. Единственный узел-исполнитель execute SHALL вызывать Registry по кейсу; семейство кейса MUST записываться в трассу по FR-AG-14. LLM MUST вызываться только внутри узлов и MUST NOT расширять белые списки инструментов. Узел guard MUST проверять ссылки и источники и подставлять ссылки кодом. Каждый шаг с LLM MUST сохранять запасной вариант без LLM. Граф MUST работать при DEMO_MODE=true на фикстурах и in-memory хранилище без внешних аккаунтов.

#### Scenario: Выбор исполнителя кодом
- **WHEN** Router вернул `team_q2`
- **THEN** выполняется узел `execute` с семейством `team` в трассе

#### Scenario: Прямой триггер в графе
- **WHEN** планировщик запускает `brief_render`
- **THEN** граф передаёт управление узлу `execute` с семейством `brief` без вызова Router

#### Scenario: Guard отклоняет выдуманную ссылку
- **WHEN** узел-исполнитель вернул ответ со ссылкой на неизвестный `source_id`
- **THEN** узел `guard` заменяет ответ безопасным шаблоном кейса

#### Scenario: Демо без сети
- **WHEN** граф запускается при `DEMO_MODE=true` без внешних аккаунтов и backend
- **THEN** все шаги демо проходят на фикстурах и in-memory хранилище без сетевых вызовов
