# AI-buddy

ИИ-наставник для стажёров в Telegram. Требования: [продукт](docs/1_PRODUCT.md), [архитектура](docs/2_ARCHITECTURE.md), [дорожная карта](docs/3_ROADMAP.md).

Сейчас реализован **каркас Этапа 0 — часть Ильи**: PostgreSQL 16 с pgvector, Redis 7, Grafana, Go-backend и Python-заглушка агента. Оба приложения отвечают только на `/health`, с `mode=stub`. Telegram-бот, LLM, MCP, хранение пользовательских данных и сценарии H0–H3 ещё не реализованы. Выбор LLM-провайдера и настоящий `/start` — часть Эмиля.

Полный согласованный план Ильи и дальнейшие этапы сохранены в [plans/ilya-backend-infra.md](plans/ilya-backend-infra.md). Спецификация каркаса: [scaffold](openspec/specs/scaffold/spec.md); выполненное изменение: [add-stage-zero-scaffold](openspec/changes/archive/2026-11-10-add-stage-zero-scaffold/proposal.md).

## Запуск каркаса

Нужны Docker Engine с работающим daemon, Docker Compose v2+ и `make`. Токены внешних сервисов не нужны.

```bash
make up
make ps
make smoke
curl --fail http://localhost:8080/health
```

`make up` собирает образы и ждёт успешных healthchecks всех пяти сервисов. Первый запуск скачивает образы и требует сети. Обычный `docker compose up --build` тоже поддерживается.

`make smoke` дополнительно проверяет HTTP и JSON приложений, Grafana, `PING` Redis и SQL с типом `vector`. После `make demo` можно выполнить `make smoke SMOKE_ARGS=--demo`, чтобы также проверить флаг агента. CI использует те же команды.

- Backend: <http://localhost:8080/health>.
- Grafana: <http://localhost:3000>, локальный вход по умолчанию `admin` / `buddy-local`. Дашборды появятся на следующем этапе.
- Agent проверяется внутри сети Compose на `http://agent:8081/health`; наружу его порт не опубликован.
- PostgreSQL и Redis доступны только в сети Compose. Backend и Grafana опубликованы на `127.0.0.1`.

Необязательные настройки можно задать в `.env`, скопировав `.env.example`. Поля в примере пустые; Compose использует локальные значения по умолчанию. Для БД это `buddy` / `buddy-local`, база `buddy`. Передача `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `EMBEDDINGS_MODEL` уже настроена, но заглушка не использует LLM. Реальные секреты хранить только в `.env`.

```bash
cp .env.example .env
make demo    # тот же каркас с DEMO_MODE=true; полного демо H0–H3 пока нет
make logs
make down    # останавливает контейнеры, сохраняет данные
```

Данные PostgreSQL, Redis и Grafana сохраняются в named volumes. PostgreSQL применяет `infra/postgres/init.sql` только при первом создании БД. Изменение `POSTGRES_USER`, `POSTGRES_PASSWORD` или `POSTGRES_DB` в `.env` не перенастраивает уже существующий volume. `make down` данные не удаляет.

Если порты заняты, задать `BACKEND_PORT` и `GRAFANA_PORT` в `.env`. Если `make up` не проходит, посмотреть `make ps` и `docker compose logs --tail=100`; при ошибке соединения с Docker socket проверить, запущен ли Docker daemon и есть ли права на него.

На Arch Linux после обновления ядра Docker может не стартовать из-за отсутствия модулей для ещё работающего старого ядра. Проверить `uname -r`, каталог `/usr/lib/modules/` и `journalctl -b -u docker.service`. Если версии не совпадают, требуется перезагрузка в установленное ядро; затем `sudo systemctl reset-failed docker.service docker.socket` и `sudo systemctl start docker`. Эти действия выполняются владельцем машины; Compose не меняет настройки ядра или firewall.

## Разработка и проверки

Нужны Go 1.27+, Python 3.12+ с `venv`; для OpenSpec — Node.js >= 22.12 и npm. Образы и Python-job CI используют Python 3.12.

```bash
make dev                 # Python-зависимости в локальной .venv
make test                # go test -race -cover, go vet, pytest
make cover               # отдельные отчёты, порог строго > 80 %
make check-config        # проверка Compose без Docker daemon
make validate-specs      # зафиксированная версия OpenSpec через npx
```

Отчёты: `reports/backend/index.html` и `reports/agent/html/index.html`. Go показывает покрытие операторов штатным `go test -cover`; Python — покрытие строк. Они не объединяются в один процент. Локальные отчёты исключены из Git; CI сохраняет их отдельными artifacts.

Backend можно запустить без Docker: `cd backend && go run ./cmd/backend`. Адрес задаётся `HTTP_ADDR`, по умолчанию `:8080`. `GET /health` проверяет живость процесса, подключения к БД на Этапе 0 ещё нет. При SIGINT/SIGTERM сервер завершает активные запросы с таймаутом 5 секунд.

Заглушка агента запускается командой `cd agent && python3 -m buddy` и слушает порт 8081. Она не делает сетевых запросов к Telegram, LLM или корпоративным сервисам. Эмиль заменит её своей реализацией.

`make ingest` и `make record-fixtures` пока завершаются явной ошибкой: они относятся к следующим этапам.
