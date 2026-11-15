# AI-buddy

Помощник стажёра для Hack&Change 2026. Цель — сократить рутинные затраты времени
наставника. Основной интерфейс — Telegram и обычный диалог; дополнительный канал —
Windows-клиент AvitoCode. Данные демонстрации синтетические и не являются регламентами Авито.

| Сценарий | Что делает |
|---|---|
| H0 — бриф | Личные изменения GitHub, Jira и Calendar, приоритеты и ссылки |
| H1 — доступы | Проверяет пять инструментов; по кнопке подаёт заявку в имитацию сервиса доступов |
| H2 — команда | Отвечает по карточке, FAQ и Jira; помогает передать вопрос человеку |
| H3 — playbook | Ищет в фиксированном снимке AvitoTech Playbook и указывает источники |
| H4 — экватор | Проверяет текст PDF/PPTX, структуру и факты Jira, сравнивает версии; файл не редактирует |

Текущее описание обновлено 15.11.2026 для ветки `improved_structure`.
Реализованная возможность, проверка на фикстурах и проверка с настоящим сервисом —
разные статусы: [текущий статус и оставшиеся задачи](docs/3_ROADMAP.md).

## Настройка .env — до запуска

Нужны Docker Engine/Desktop с Compose v2. Для вспомогательных скриптов — Python 3.12;
Make необязателен. Go на хосте нужен для локальных Go-тестов, но не для сборки контейнеров.

Из корня проекта создайте `.env` из [.env.example](.env.example), если файла ещё нет.
Существующий `.env` не перезаписывайте:

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
```

В Linux/macOS: `test -f .env || cp .env.example .env`.
Compose автоматически читает корневой `.env`; переменные shell имеют приоритет.
Секреты хранятся только локально и не попадают в Git.

| Режим | Минимальные значения в .env |
|---|---|
| Фикстуры без аккаунтов и LLM | `DEMO_MODE=true`, `LLM_IN_DEMO=false`, `LLM_PROVIDER=fake`; токен Telegram и LLM-ключ пустые |
| Telegram с фикстурами и настоящей LLM | `DEMO_MODE=true`, `LLM_IN_DEMO=true`, `TELEGRAM_BOT_TOKEN`, `LLM_PROVIDER=openrouter`, `LLM_MODEL`, `LLM_API_KEY` |
| Живые данные | `DEMO_MODE=false`, настоящая LLM, профиль в backend и read-only учётные данные нужных источников; Telegram-токен для Telegram-канала |

Демо с LLM расходует API-бюджет. Для Telegram на фикстурах без LLM достаточно
добавить токен в первый режим. Без Telegram интерфейс desktop можно включить
через `CLIENT_API_ENABLED=true`; вход и ограничения описаны в [desktop/README.md](desktop/README.md).

| Переменные | Назначение |
|---|---|
| `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY` | Провайдер и модель только из окружения |
| `LLM_BASE_URL` | Адрес API при `LLM_PROVIDER=compatible` |
| `AGENT_ENGINE=graph`, `AGENT_MODE=agent` | LangGraph и агент с инструментами; без настоящей LLM сохраняется Router |
| `BUDDY_SERVICE_KEY` | Общий ключ backend/agent; задайте собственный для использования вне локального демо |
| `POSTGRES_PASSWORD`, `GRAFANA_*_PASSWORD` | Пароли; локальные defaults Compose предназначены для демонстрации |
| `RAG_SEARCH_MODE=bm25` | Поиск без загрузки модели; `vector`/`rrf` требуют дополнительных зависимостей и индекса |
| `BRIEF_TIME`, `BRIEF_TIMEZONE`, `REDIRECT_BASE_URL` | Расписание брифа и доступный пользователю адрес учёта кликов |
| `MEMORY_*`, `REDIS_URL` | Память диалога; в демо она остаётся в процессе |
| `H4_*`, `CLIENT_API_ENABLED` | История/пороги презентаций и дополнительный клиентский API |

Полный пример с комментариями: [.env.example](.env.example). При запуске Python вне Compose
задайте согласованную пару `BACKEND_URL` и `BUDDY_SERVICE_KEY`; Compose передаёт её сам.
Изменение пароля PostgreSQL в `.env` не меняет пароль уже созданной БД в существующем томе.

## Запуск и применение настроек

Базовый стек — пять сервисов: PostgreSQL + pgvector, Redis, backend, agent, Grafana.

```powershell
docker compose up --build -d --wait --wait-timeout 180
python infra/setup_grafana.py
docker compose ps
```

Для чтения PPTX добавьте шестой сервис, он не требует корпоративного аккаунта:

```powershell
docker compose -f docker-compose.yml -f docker-compose.powerpoint.yml up --build -d --wait --wait-timeout 180
```

PDF читается без PowerPoint MCP. Live Jira и Calendar подключаются отдельно:
[mcp/README.md](mcp/README.md). В демо корпоративные MCP не требуются.

После изменения `.env` пересоздайте соответствующие контейнеры через `docker compose up -d`;
обычный `restart` не применяет новую конфигурацию. Используйте те же `-f`, что при запуске.
После изменения кода добавьте `--build`. Не запускайте второй polling-процесс с тем же токеном.

С Make: `make up`, `make pptx-up`, `make demo`. Последний использует `.env.example`,
но унаследованные переменные shell могут переопределить значения.
`docker compose down` / `make down` останавливает стек и сохраняет тома. Не используйте
`down -v` для обычной остановки: там пользовательские данные и история.

Порты по умолчанию доступны только локально:
[backend health](http://127.0.0.1:8080/health),
[backend ready](http://127.0.0.1:8080/ready),
[agent health](http://127.0.0.1:8081/health), [Grafana](http://127.0.0.1:3000).
Они переопределяются `BACKEND_PORT`, `AGENT_PORT`, `GRAFANA_PORT`.
Grafana Viewer создаётся скриптом настройки; локальный login — `viewer`, пароль
задаёт `GRAFANA_VIEWER_PASSWORD` (демонстрационный default указан в Compose).

## Проверка бота

1. `/demo` включает синтетического стажёра. Спросите «Что нового?» или отправьте `/brief`.
2. «Какие доступы нужны для ORD-118?» — статусы, источники и кнопка заявки в имитацию.
3. `/demo next` меняет шаг сценария; доступны три шага. `/demo reset` начинает заново.
4. Спросите о команде или playbook обычным текстом; `/ask` остаётся только совместимостью.
5. «Что делает /settings?» объясняет команду; `/settings brief 09:00` меняет время,
   `/settings quiet on|off` — тихий режим. При quiet on в день без изменений бриф не приходит.
6. Отправьте PDF/PPTX: бот проверяет текст до 10 МиБ и 30 слайдов. Учебные файлы:
   [fixtures/equator](fixtures/equator), сценарий — [H4_OPERATIONS.md](docs/archive/2026-11-15/H4_OPERATIONS.md).
7. `/demo mentor` и `/demo intern` переключают учебные роли; `/demo off` выходит из демо.

Для реального профиля импортируйте синтетический seed и выдайте приглашение:

```powershell
docker compose exec -T backend backend seed
docker compose exec -T backend backend invite --user demo-daniil
```

Код передаётся адресату приватно для `/start <код>`. Повторный `/start` показывает
профиль. Операции seed и приглашений подробнее описаны в [backend/README.md](backend/README.md).

## Проверки разработки

```powershell
uv sync --project agent --frozen --extra dev --python 3.12
uv run --project agent --frozen --extra dev pytest agent/tests
uv run --project agent --frozen --extra dev ruff check --config agent/pyproject.toml agent scripts infra
uv run --project agent --frozen python -m buddy --smoke
openspec validate --all
```

OpenSpec требует Node.js ≥ 22.12 и CLI `@fission-ai/openspec`.
Go-интеграции: `python infra/test_backend.py` (Go на хосте и Docker либо отдельный
`TEST_DATABASE_URL`). Покрытие: `make cover`; тесты используют фейки LLM.
Windows-окружение с ограниченным Temp: команда с `--basetemp` приведена в [agent/README.md](agent/README.md).
Реальные LLM-evals запускаются отдельно с согласованным бюджетом: [evals/README.md](evals/README.md).

Текущий `infra/smoke.py` проверяет базовый набор и опциональные Jira/Calendar, но не
учитывает PowerPoint-контейнер. С PPTX проверяйте `compose ps` и health отдельно;
ошибка состава контейнеров сама по себе не означает неисправность бота.

## Документация и границы

- [Продукт](docs/1_PRODUCT.md), [архитектура](docs/2_ARCHITECTURE.md), [статус и план](docs/3_ROADMAP.md).
- [Каталог метрик](docs/METRICS.md): доступные данные, формулы и запланированные события.
- [Структура агента](agent/README.md), [backend](backend/README.md), [Windows-клиент](desktop/README.md).
- [Источники MCP](mcp/README.md), [живой GitHub](mcp/GITHUB_PROJECT.md), [персональная Jira](mcp/JIRA_BRIEF.md).
- [Отчёты и свидетельства проверок](reports/README.md), [аудит OpenSpec](docs/OPENSPEC_STATUS.md).
- [Исторические основные документы](docs/archive/README.md).

Права и ссылки проверяет код. Изменение корпоративных объектов запрещено; разрешены
наши данные и имитации доступов/прогресса. Telegram доставляет ответы и явно подтверждённые
эскалации в рамках продукта. Настоящие корпоративные интеграции требуют своей проверки;
подключённая LLM и здоровые контейнеры не подтверждают готовность промышленного внедрения.
Экономия часов наставника пока не измерена пользовательским пилотом.

## Лицензия

Оригинальный код команды — [MIT](LICENSE). Сторонние зависимости, модели и тексты
сохраняют свои условия: [уведомления и ограничения](THIRD_PARTY_NOTICES.md),
[реестр зависимостей](third_party/dependency-licenses.json). В частности, корпус
AvitoTech Playbook не включён в MIT проекта; права на его распространение требуют
отдельного подтверждения.
