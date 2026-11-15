# AGENTS.md

Инструкции для AI-ассистентов (Claude Code, Codex, Cursor и др.) и для людей, которые работают в этом репозитории.

## Проект

**AI-buddy** — ИИ-наставник для стажёров Авито в виде Telegram-бота. Кейс Hack&Change 2026.
Главная цель — сократить время наставника на рутину; основной пользователь — стажёр.

Гипотезы MVP:
- **H0** — утренний бриф: изменения в Google Calendar, Git (GitHub) и Jira, у каждого пункта ссылка на объект.
- **H1** — доступы к 5 инструментам (GitHub, Jira, Miro, Google Calendar, Telegram): статус читается в самих инструментах, заявка подаётся в **имитацию** сервиса доступов, результат приходит программно.
- **H2** — «кто есть кто»: 8 типов вопросов о команде по карточке команды, FAQ и Jira.
- **H3** — RAG по публичному [AvitoTech Playbook](https://github.com/avito-tech/playbook).
- **H4** (добавлена 12.11) — проверка презентации стажёра к экваторной встрече по критериям; агент файл не правит.

H0 — основа продукта (привычка заходить в бота); канал — Telegram. Актуальные решения: `docs/1_PRODUCT.md`, раздел 10.

## Перед началом работы прочитай
1. `docs/1_PRODUCT.md` — гипотезы, требования (FR-*), кейсы агента, дерево user cases, метрики.
2. `docs/2_ARCHITECTURE.md` — компоненты, MCP-серверы, `buddy-mcp`, демо-режим, данные, безопасность, структура репозитория.
3. `docs/3_ROADMAP.md` — этапы, роли, дедлайны, какие инструменты ставим готовыми.
4. `docs/METRICS.md` — существующие метрики бота, ограничения и решение об отмене расширения.
5. `openspec/specs/*/spec.md` — спецификации поведения (источник истины для реализации и тестов).

Если код расходится со спецификацией — сначала обнови спецификацию (через изменение в `openspec/changes/`), потом код.

## Команда и зоны
| Кто | Роль | Зона |
|---|---|---|
| Илья | Backend / DevOps / desktop | `backend/` (Go), `desktop/` (Windows-приложение), `infra/`, `docker-compose.yml`, CI, запуск готовых MCP-серверов |
| Эмиль | ML / агент | `agent/` (Python): бот, LangGraph и агентный цикл, Router fallback, MCP, RAG и evals |
| Даниил | PM / team lead | `docs/`, `openspec/`, `seed/`, `fixtures/`, `evals/` (наборы), README |

## Структура и актуальный статус

```text
backend/      Go: REST, buddy-mcp, PostgreSQL и миграции
agent/        Python: orchestration/, bot/, channels/, H0–H4, MCP, memory, prompts
desktop/      React/Electron: дополнительный Windows-клиент
mcp/          готовые MCP, конфигурация и read-only обёртки
seed/         синтетические профили, карточки, каталог; backend bundle через seed/build_stage_two.py
fixtures/     ответы MCP и учебные PDF/PPTX
data/playbook/ снимок; индекс — agent/buddy/rag/data/index.json
evals/        оценки качества и отчёты
openspec/     спецификации, активные изменения, archive
infra/        Grafana, PostgreSQL init и эксплуатационные проверки
docs/         продукт, архитектура, roadmap, METRICS и история
reports/      доказательства проверок; индекс reports/README.md
```

H0–H4, LangGraph/память, каталог сценариев, имитации H1/прогресса и дополнительный
desktop-канал реализованы. Обычный диалог — основной путь, /ask сохранена для
совместимости. Реализации управления находятся в agent/buddy/orchestration/;
старые agent/graph/router/executors-модули внутри buddy — совместимые импорты.
Проверять состояние по docs/3_ROADMAP.md и датированным отчётам, а не ранним этапам.
Каталог метрик docs/METRICS.md описывает пять существующих агрегатов; расширение метрик отменено.

Управлять запуском по поручению пользователя в текущей сессии. Не запускать второй
polling с тем же токеном; не останавливать существующий стек ради чтения/правки docs.
Runtime-статус из исторического отчёта не является бессрочной инструкцией.

## Стек
- **Go** — `backend` (pgx, goose, официальный [MCP Go SDK](https://github.com/modelcontextprotocol/go-sdk)).
- **Python 3.12** — `agent` (aiogram 3, [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), asyncio-фоновые задачи, необязательный sentence-transformers).
- **PostgreSQL 16 + pgvector**, **Redis 7**, **Grafana**, **Docker Compose**, **GitHub Actions**.
- Готовые MCP-серверы: [github-mcp-server](https://github.com/github/github-mcp-server), [mcp-atlassian](https://github.com/sooperset/mcp-atlassian), [google-calendar-mcp](https://github.com/nspady/google-calendar-mcp), [Miro MCP](https://developers.miro.com/docs/miro-mcp).
- LLM-провайдер и модель — **только через переменные окружения** (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `EMBEDDINGS_MODEL`).

## Жёсткие правила
1. **Только чтение реальных корпоративных сервисов.** Не добавляй и не включай инструменты записи в GitHub, Jira, Google Calendar, Miro или Telegram. MCP-серверы запускаются в режиме только чтения; у каждого кейса агента — белый список инструментов. Записывать можно **наши** данные в `backend` через REST и — с 12.11 — в **имитации** корпоративных сервисов: сервис доступов (заявка H1) и трекер прогресса стажёра (отметка H4).
2. **Ссылка на источник обязательна.** Любой факт из внешнего сервиса или документа в ответе бота сопровождается ссылкой на конкретный объект. Ссылки подставляет код, а не LLM.
3. **У каждого шага с LLM есть запасной вариант без LLM** (правила, шаблоны).
4. **Демо-режим должен работать.** `DEMO_MODE=true` запускает весь сценарий на фикстурах без внешних аккаунтов. Не ломай его новыми вызовами без фикстур.
5. **Секреты — только в `.env`** (он в `.gitignore`). В репозиторий попадает только `.env.example` без секретов.
6. **Только синтетические данные.** Тестовые инструкции не выдавать за регламенты Авито.
7. **Права пользователя.** Стажёр видит только данные своей команды; `buddy-mcp` проверяет пользователя на каждом вызове.

Штатная доставка ответов нашего бота, подтверждённых пользователем эскалаций и H4
сохраняется по спецификациям. Запрет корпоративной записи означает отсутствие
произвольных write-инструментов, изменения прав и редактирования внешних объектов.

## Требования и тесты
- ID требований из спецификаций (`FR-H0-01`, `FR-H0-LINK-2`, `NFR-RO-1`, `RT-05` …) используются в именах тестов:
  - Go: `TestFR_H1_03_CheckInTool`;
  - Python: `test_fr_h0_link_2_item_without_url_is_hidden`.
- Каждый сценарий `#### Scenario:` из спецификации — минимум один тест.
- Покрытие строк тестами **> 80 %** отдельно для `backend` и `agent` (без сгенерированного кода и самих тестов).
- В тестах LLM подменяется фейком; реальная LLM — только в `evals/`.

## OpenSpec
- Спецификации: `openspec/specs/<capability>/spec.md` — `### Requirement:` (с SHALL/MUST) и `#### Scenario:` с **WHEN**/**THEN**.
- Новое поведение или изменение — через папку `openspec/changes/<имя>/` (`proposal.md`, `tasks.md`, дельты `## ADDED/MODIFIED/REMOVED Requirements`), затем `openspec archive <имя>`.
- Проверка: `openspec validate --all` (нужен Node.js ≥ 22.12, установка: `npm install -g @fission-ai/openspec@latest`).

## Команды разработки
```
make up              # поднять всё в docker compose
make demo            # запуск в демо-режиме (DEMO_MODE=true)
make test            # go test ./... и pytest
make cover           # отчёты о покрытии
make ingest          # индексация playbook для H3 (эмбеддинги — только при RAG_SEARCH_MODE=vector|rrf)
make setup-rag       # необязательные зависимости e5 + pgvector для режимов vector и rrf
make record-fixtures # пока заглушка; запись через MCP CLI --live --record (mcp/CLIENTS.md)
```

## Стиль работы
- Документация и тексты бота — на русском; код, идентификаторы и сообщения коммитов — на английском.
- Небольшие коммиты с понятным описанием; в `main` — только то, что проходит тесты.
- Не меняй `docs/` и `openspec/specs/` молча: изменение требований согласуй с командой.
