## ADDED Requirements

### Requirement: NFR-BOOT-MCP-1 Проверка Compose с PowerPoint MCP

Smoke-проверка SHALL допускать пять обязательных сервисов backend, agent, postgres,
redis, grafana и опциональные mcp-atlassian, google-calendar-mcp, powerpoint-mcp.
Она MUST отклонять неизвестные сервисы и отсутствие обязательных. Все обязательные
и присутствующий powerpoint-mcp MUST иметь State=running и Health=healthy.
Проверка Jira/Calendar MAY оставаться отдельной. Отсутствие PowerPoint MCP MUST NOT
ломать базовый стек; проверка контейнера не подтверждает выполнение PPTX-инструмента.

#### Scenario: Базовый стек
- **WHEN** присутствуют только пять обязательных running/healthy сервисов
- **THEN** проверка состава и состояния проходит без PowerPoint MCP

#### Scenario: PPTX overlay
- **WHEN** дополнительно присутствует running/healthy powerpoint-mcp
- **THEN** проверка принимает состав и состояние контейнеров

#### Scenario: Неисправный контейнер
- **WHEN** обязательный сервис или присутствующий powerpoint-mcp не running либо не healthy
- **THEN** smoke завершается ошибкой с именем неисправного сервиса

#### Scenario: Неполный или неизвестный состав
- **WHEN** обязательный сервис отсутствует или присутствует неизвестный сервис
- **THEN** smoke завершается ошибкой состава

#### Scenario: Другие опциональные MCP
- **WHEN** вместе с здоровым базовым стеком присутствуют mcp-atlassian или google-calendar-mcp
- **THEN** проверка допускает их и оставляет проверку MCP отдельному инструменту
