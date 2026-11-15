# scaffold Specification

## Purpose
Каркас Этапа 0 для локальной разработки: Docker Compose с PostgreSQL и pgvector,
Redis, Grafana и заглушками backend/agent; проверки живости, конфигурация через
окружение, команды приёмки и CI с отдельным покрытием Go и Python.

## Requirements

### Requirement: NFR-BOOT-1 Запуск каркаса одной командой
Команда `make up` SHALL собирать и запускать PostgreSQL 16 с pgvector, Redis 7, Grafana, backend и agent через Docker Compose и ждать успешных healthchecks. Прикладные сервисы Этапа 0 MUST обозначать этап полем `stage=0`; агент сохраняет реализованные `/start` и `/help`.

Команда `make smoke` SHALL проверять здоровье всех пяти запущенных контейнеров, HTTP-ответы приложений, доступность Grafana, ответ Redis и работу типа `vector` в PostgreSQL; ошибка любой проверки MUST приводить к ненулевому коду завершения.

#### Scenario: Запуск без внешних аккаунтов
- **WHEN** разработчик запускает каркас без токенов Telegram, LLM и корпоративных сервисов
- **THEN** все пять сервисов запускаются; агент работает offline без обращений к внешним сервисам

#### Scenario: Демо-запуск каркаса
- **WHEN** разработчик выполняет `make demo`
- **THEN** Compose запускает тот же каркас с `DEMO_MODE=true`; это ещё не реализация сценариев H0–H3

### Requirement: NFR-HEALTH-1 Проверка живости заглушек
Backend и agent SHALL отвечать на `GET /health` HTTP-статусом 200 и JSON с полями `status=ok`, `service` и `stage=0`, пока процесс работает. Agent MUST возвращать 503 при завершившейся polling-задаче, если процесс ещё обслуживает health. Поле `mode` агента MAY обозначать `offline` или `telegram`. Healthcheck Этапа 0 SHALL проверять живость процесса, а не готовность будущих прикладных интеграций. Неизвестный путь MUST возвращать 404.

#### Scenario: Проверка HTTP
- **WHEN** клиент запрашивает `/health` у backend или agent
- **THEN** получает 200, `Content-Type: application/json`, stage=0 и имя соответствующего сервиса

#### Scenario: Неизвестный путь
- **WHEN** клиент запрашивает несуществующий HTTP-путь
- **THEN** получает 404, а не успешный healthcheck

### Requirement: NFR-CI-1 Тесты и покрытие каркаса
Команды `make test` и `make cover` SHALL запускать Go- и Python-тесты. Команда `make cover` MUST создавать отчёты отдельно для backend и agent и завершаться ошибкой при покрытии <= 80 %. GitHub Actions SHALL проверять тесты, сборку, Compose и OpenSpec.

#### Scenario: Проверки в CI
- **WHEN** создаётся pull request или обновляется main
- **THEN** CI запускает Go- и Python-тесты, публикует отдельные отчёты покрытия и проверяет запуск каркаса через Compose

### Requirement: NFR-ENV-1 Настройка каркаса
Каркас SHALL запускаться с локальными значениями по умолчанию без `.env`. Файл `.env.example` MUST перечислять настройки без секретных значений. Переменные `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `EMBEDDINGS_MODEL` и `DEMO_MODE` SHALL передаваться агенту через окружение.

#### Scenario: Параметры будущего агента
- **WHEN** пользователь задаёт настройки агента в локальном `.env`
- **THEN** Compose передаёт их контейнеру agent; заглушка не выполняет вызовов LLM

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
