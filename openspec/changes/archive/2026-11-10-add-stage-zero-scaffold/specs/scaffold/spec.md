## ADDED Requirements

### Requirement: NFR-BOOT-1 Запуск каркаса одной командой
Команда `make up` SHALL собирать и запускать PostgreSQL 16 с pgvector, Redis 7, Grafana, backend и agent через Docker Compose и ждать успешных healthchecks. Прикладные сервисы Этапа 0 MUST явно обозначаться как заглушки.

Команда `make smoke` SHALL проверять здоровье всех пяти запущенных контейнеров, HTTP-ответы приложений, доступность Grafana, ответ Redis и работу типа `vector` в PostgreSQL; ошибка любой проверки MUST приводить к ненулевому коду завершения.

#### Scenario: Запуск без внешних аккаунтов
- **WHEN** разработчик запускает каркас без токенов Telegram, LLM и корпоративных сервисов
- **THEN** все пять сервисов запускаются; заглушка agent не обращается к внешним сервисам

#### Scenario: Демо-запуск каркаса
- **WHEN** разработчик выполняет `make demo`
- **THEN** Compose запускает тот же каркас с `DEMO_MODE=true`; это ещё не реализация сценариев H0–H3

### Requirement: NFR-HEALTH-1 Проверка живости заглушек
Backend и agent SHALL отвечать на `GET /health` HTTP-статусом 200 и JSON с полями `status=ok`, `service` и `mode=stub`. Healthcheck Этапа 0 SHALL проверять живость процесса, а не готовность будущих прикладных интеграций. Неизвестный путь MUST возвращать 404.

#### Scenario: Проверка HTTP
- **WHEN** клиент запрашивает `/health` у backend или agent
- **THEN** получает 200, `Content-Type: application/json` и имя соответствующего сервиса

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
