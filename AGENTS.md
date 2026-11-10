# AGENTS.md

Инструкции для AI-ассистентов (Claude Code, Codex, Cursor и др.) и для людей, которые работают в этом репозитории.

## Проект

**AI-buddy** — ИИ-наставник для стажёров Авито в виде Telegram-бота. Кейс Hack&Change 2026.
Главная цель — сократить время наставника на рутину; основной пользователь — стажёр.

Гипотезы MVP:
- **H0** — утренний бриф: изменения в Google Calendar, Git (GitHub) и Jira, у каждого пункта ссылка на объект.
- **H1** — мониторинг доступов к 5 инструментам (GitHub, Jira, Miro, Google Calendar, Telegram), **только чтение**.
- **H2** — «кто есть кто»: 8 типов вопросов о команде по карточке команды, FAQ и Jira.
- **H3** — RAG по публичному [AvitoTech Playbook](https://github.com/avito-tech/playbook).

## Перед началом работы прочитай
1. `docs/1_PRODUCT.md` — гипотезы, требования (FR-*), кейсы агента, дерево user cases, метрики.
2. `docs/2_ARCHITECTURE.md` — компоненты, MCP-серверы, `buddy-mcp`, демо-режим, данные, безопасность, структура репозитория.
3. `docs/3_ROADMAP.md` — этапы, роли, дедлайны, какие инструменты ставим готовыми.
4. `openspec/specs/*/spec.md` — спецификации поведения (источник истины для реализации и тестов).

Если код расходится со спецификацией — сначала обнови спецификацию (через изменение в `openspec/changes/`), потом код.

## Команда и зоны
| Кто | Роль | Зона |
|---|---|---|
| Илья | Backend / DevOps | `backend/` (Go), `infra/`, `docker-compose.yml`, CI, запуск готовых MCP-серверов |
| Эмиль | ML / frontend | `agent/` (Python): бот, Router + Executor, MCP-клиенты, демо-режим, RAG, evals |
| Даниил | PM / team lead | `docs/`, `openspec/`, `seed/`, `fixtures/`, `evals/` (наборы), README |

## Структура репозитория (целевая)
```
backend/     Go: REST API + собственный MCP-сервер buddy-mcp (/mcp) + миграции
agent/       Python: bot/ router/ executors/ mcp_hub/ brief/ access/ rag/ scheduler/ llm/ prompts/
mcp/         конфиги готовых MCP-серверов (github, atlassian, google calendar, miro)
seed/        тестовые пользователи, карточки команд, каталог доступов
fixtures/    записанные ответы MCP для демо-режима (step_1/, step_2/ …)
data/playbook/  снимок avito-tech/playbook
evals/       наборы и скрипт оценки качества AI
openspec/    спецификации (OpenSpec)
infra/       grafana, postgres init
docs/        продуктовая документация
```
Сейчас в репозитории есть только `docs/` и `openspec/`; остальное создаётся по `docs/3_ROADMAP.md`.

## Стек
- **Go** — `backend` (pgx, goose, официальный [MCP Go SDK](https://github.com/modelcontextprotocol/go-sdk)).
- **Python 3.12** — `agent` (aiogram 3, [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), APScheduler, sentence-transformers).
- **PostgreSQL 16 + pgvector**, **Redis 7**, **Grafana**, **Docker Compose**, **GitHub Actions**.
- Готовые MCP-серверы: [github-mcp-server](https://github.com/github/github-mcp-server), [mcp-atlassian](https://github.com/sooperset/mcp-atlassian), [google-calendar-mcp](https://github.com/nspady/google-calendar-mcp), [Miro MCP](https://developers.miro.com/docs/miro-mcp).
- LLM-провайдер и модель — **только через переменные окружения** (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `EMBEDDINGS_MODEL`).

## Жёсткие правила
1. **Только чтение корпоративных сервисов.** Не добавляй и не включай инструменты записи в GitHub, Jira, Google Calendar, Miro или Telegram. MCP-серверы запускаются в режиме только чтения; у каждого кейса агента — белый список инструментов. Записывать можно только **наши** данные в `backend` через REST.
2. **Ссылка на источник обязательна.** Любой факт из внешнего сервиса или документа в ответе бота сопровождается ссылкой на конкретный объект. Ссылки подставляет код, а не LLM.
3. **У каждого шага с LLM есть запасной вариант без LLM** (правила, шаблоны).
4. **Демо-режим должен работать.** `DEMO_MODE=true` запускает весь сценарий на фикстурах без внешних аккаунтов. Не ломай его новыми вызовами без фикстур.
5. **Секреты — только в `.env`** (он в `.gitignore`). В репозиторий попадает только `.env.example` без значений.
6. **Только синтетические данные.** Тестовые инструкции не выдавать за регламенты Авито.
7. **Права пользователя.** Стажёр видит только данные своей команды; `buddy-mcp` проверяет пользователя на каждом вызове.

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

## Команды (появятся по мере разработки)
```
make up              # поднять всё в docker compose
make demo            # запуск в демо-режиме (DEMO_MODE=true)
make test            # go test ./... и pytest
make cover           # отчёты о покрытии
make ingest          # индексация playbook для H3
make record-fixtures # запись ответов MCP в fixtures/
```

## Стиль работы
- Документация и тексты бота — на русском; код, идентификаторы и сообщения коммитов — на английском.
- Небольшие коммиты с понятным описанием; в `main` — только то, что проходит тесты.
- Не меняй `docs/` и `openspec/specs/` молча: изменение требований согласуй с командой.
