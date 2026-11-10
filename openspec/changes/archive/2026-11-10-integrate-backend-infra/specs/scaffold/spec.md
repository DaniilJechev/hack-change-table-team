## MODIFIED Requirements

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
