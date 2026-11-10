# AI-buddy · Часть 2. Архитектура

Дата: 10 ноября 2026. Продукт — [1_PRODUCT.md](1_PRODUCT.md), план работ — [3_ROADMAP.md](3_ROADMAP.md).

---

## 1. Ключевые решения

| # | Решение | Почему |
|---|---|---|
| 1 | **Внешние сервисы — только через готовые MCP-серверы** (GitHub, Jira, Google Calendar, Miro) и прямой Telegram API. Своих MCP для них не пишем | Меньше кода; эти серверы поддерживаются авторами; при внедрении меняется только конфигурация |
| 2 | **Агент сам ходит в MCP-серверы**, без посредника | Меньше звеньев; MCP — стандарт, который ИИ-платформа Авито поддерживает (кейс, стр. 10) |
| 3 | **Только чтение.** Агенту доступны только инструменты чтения | Не можем навредить данным; кейс, стр. 12, п. 05 |
| 4 | **Свой бэкенд на Go** хранит наши данные (пользователи, карточки команд, каталог доступов, FAQ, брифы, метрики) и отдаёт их агенту через **свой небольшой MCP-сервер** | Go — основной язык Авито; агенту все данные нужны через один интерфейс (MCP) |
| 5 | **Своего API-мока сервисов Авито нет.** Для демо без внешних сервисов — **демо-режим со статичными записанными ответами** MCP-серверов | Не тратим время на имитацию API; жюри может запустить проект без внешних аккаунтов |
| 6 | **Router + Executor**: Router выбирает кейс, Executor выполняет его | Предсказуемо и тестируемо |
| 7 | **LLM-провайдер — через переменные окружения** | Требование кейса (стр. 9) |

---

## 2. Стек: требования PDF → наш выбор

| Слой | PDF (стр. 10–11) | Наш выбор |
|---|---|---|
| Backend | Go преимущественно; Python/Flask в отдельных случаях | **Go** — `backend` (данные, REST, свой MCP-сервер). **Python** — `agent` (бот, LLM, RAG) как ML-случай |
| БД | PostgreSQL, Redis | PostgreSQL 16 + pgvector (данные и индекс RAG); Redis 7 (блокировки, лимиты, кэш) |
| Frontend | React + TS + … | Не нужен: интерфейс — Telegram-бот |
| RPC / Kafka | RPC / Apache Kafka | MCP (JSON-RPC 2.0 поверх Streamable HTTP) + REST между `agent` и `backend`. Kafka — путь внедрения (события вместо опроса) |
| Инфраструктура | Docker, Grafana, Clickstream, CI/CD | docker-compose; Grafana поверх PostgreSQL; clickstream-события в PostgreSQL; GitHub Actions |
| ИИ-платформа | ИИ-агенты с MCP, ИИ-крон, feature flags | MCP; APScheduler как имитация ИИ-крона; feature flags в env и БД |
| SDD | spec-kit или OpenSpec | [OpenSpec](https://github.com/Fission-AI/OpenSpec) |

---

## 3. Компоненты — простая схема

### 3.1. Кто есть кто

```
┌──────────────┐
│   Стажёр /   │   1. пишет боту, нажимает кнопки
│  наставник   │ ◄───────────────────────────────────────────────┐
└──────┬───────┘                                                 │
       │ Telegram                                                │ 6. ответ + кнопки
       ▼                                                         │
┌──────────────────────────────────────────────────────────────────┴─────┐
│ AGENT (Python, Эмиль) — «мозг»                                          │
│                                                                        │
│  bot        ← принимает сообщения Telegram, рисует кнопки              │
│  router     ← 2. LLM: «какой это кейс?» (один из 17)                   │
│  executor   ← 3. LLM: выполняет кейс, вызывая инструменты (MCP)        │
│  scheduler  ← по расписанию запускает бриф и проверку доступов         │
│  rag        ← H3: поиск по playbook в pgvector                         │
└───────┬──────────────────────────────┬───────────────────────────┬─────┘
        │ 4. MCP: «дай данные»         │ 4. MCP: «дай наши данные» │ 5. REST: «сохрани»
        │    (только чтение)           │    (только чтение)        │   (бриф, FAQ, метрики)
        ▼                              ▼                           ▼
┌───────────────────────────┐   ┌──────────────────────────────────────────┐
│ ГОТОВЫЕ MCP-СЕРВЕРЫ       │   │ BACKEND (Go, Илья) — «память»             │
│ (не пишем, настраиваем)   │   │  REST API  ← запись наших данных          │
│                           │   │  buddy-mcp ← чтение наших данных агентом  │
│ • github-mcp-server       │   │  store     ← PostgreSQL                   │
│ • mcp-atlassian (Jira)    │   └───────────────────┬──────────────────────┘
│ • google-calendar-mcp     │                       │
│ • Miro MCP (официальный)  │                       ▼
└─────────────┬─────────────┘            ┌──────────────────────┐
              │ API сервисов             │ PostgreSQL + pgvector │──► Grafana (метрики)
              ▼                          │ Redis                 │
   GitHub · Jira · Google Calendar · Miro└──────────────────────┘

   Telegram Bot API (getChatMember) ◄── агент вызывает напрямую (проверка чатов в H1)
```

### 3.2. Что за чем происходит (пример: «Какие доступы нужны для ORD-118?»)
| Шаг | Кто | Что делает |
|---|---|---|
| 1 | Стажёр → бот | Пишет вопрос в Telegram |
| 2 | **Router** (LLM) | Определяет кейс `access_task_analysis`, параметр `task=ORD-118` |
| 3 | **Executor** (LLM + код) | Получает белый список инструментов кейса и вызывает их |
| 4a | → `mcp-atlassian` | `jira_get_issue(ORD-118)` — текст задачи |
| 4b | → `buddy-mcp` (наш) | `access_catalog_get` — какие объекты и где запрашивать |
| 4c | → `github-mcp-server`, Miro MCP, `google-calendar-mcp`, Telegram | Проверки: есть ли стажёр в репозитории, на доске, во встречах, в чате |
| 5 | Executor → backend (REST) | Сохраняет снимок статусов (чтобы потом уведомить об изменениях) |
| 6 | Бот → стажёр | «✅ GitHub; ⬜ Miro [Где запросить] [Я запросил]; ⛔ prod-репозиторий» |

### 3.3. Таблица «кто с кем говорит»
| Откуда → куда | Протокол | Зачем |
|---|---|---|
| Telegram ↔ `agent/bot` | Telegram Bot API (aiogram) | Сообщения и кнопки |
| `agent` → готовые MCP-серверы | MCP (stdio или Streamable HTTP) | Читать GitHub, Jira, Google Calendar, Miro |
| `agent` → `backend/buddy-mcp` | MCP (Streamable HTTP) | Читать карточку, каталог, FAQ, профиль, список стажёров |
| `agent` → `backend/REST` | HTTP JSON | Записывать **наши** данные: брифы, «прочитан», снимки, вопросы, FAQ, «я запросил», метрики |
| `agent` → Telegram Bot API | HTTPS | `getChatMember` для H1 |
| `backend` → PostgreSQL / Redis | SQL / Redis | Хранение |
| Grafana → PostgreSQL | SQL | Дашборд метрик |

---

## 4. Готовые MCP-серверы (не пишем, настраиваем)

Проверено по документации 10 ноября 2026. Точные имена инструментов сверяем при установке (команда `tools/list`).

| Сервис | MCP-сервер | Как запускаем | Только чтение | Инструменты, которые берём |
|---|---|---|---|---|
| **GitHub** | [github/github-mcp-server](https://github.com/github/github-mcp-server) — официальный, MIT | Docker `ghcr.io/github/github-mcp-server` (stdio, агент запускает как подпроцесс) или удалённый `https://api.githubcopilot.com/mcp/` | `--read-only` / `GITHUB_READ_ONLY`; удалённый — суффикс `/readonly`. Наборы: `GITHUB_TOOLSETS=pull_requests,issues,repos,users,orgs,notifications` | `list_pull_requests`, `search_pull_requests`, `pull_request_read` (методы `get_reviews`, `get_comments`, `get_review_comments`, `get_status`, `get_check_runs`), `list_notifications`, `list_repository_collaborators`, `get_team_members` |
| **Jira** | [sooperset/mcp-atlassian](https://github.com/sooperset/mcp-atlassian) · [docs](https://mcp-atlassian.soomiles.com/docs/configuration) — MIT, активно поддерживается | Docker `ghcr.io/sooperset/mcp-atlassian:latest`, `TRANSPORT=streamable-http`. Jira Cloud: `JIRA_URL`, `JIRA_USERNAME`, `JIRA_API_TOKEN`; self-hosted: `JIRA_PERSONAL_TOKEN` | `READ_ONLY_MODE=true`, `ENABLED_TOOLS=…` | `jira_search`, `jira_get_issue`, `jira_search_assignable_users`, `jira_get_user_profile`, `jira_get_all_projects` |
| **Google Calendar** | [nspady/google-calendar-mcp](https://github.com/nspady/google-calendar-mcp) (`@cocal/google-calendar-mcp`) — MIT, v2.8.1 от 28.10.2026, 1,2k звёзд | `TRANSPORT=http`, `PORT=3000`; OAuth (`GOOGLE_OAUTH_CREDENTIALS`), несколько аккаунтов; сервисный аккаунт (`GOOGLE_SERVICE_ACCOUNT_KEY`, v2.7+) | Отдельного режима нет → `ENABLED_TOOLS=list-events,search-events,get-event,list-calendars,get-freebusy,get-current-time` + белый список на стороне агента | `list-events`, `get-event`, `search-events`, `list-calendars` |
| **Miro** | [Официальный Miro MCP](https://developers.miro.com/docs/miro-mcp) | Удалённый `https://mcp.miro.com`, OAuth 2.1; авторизуется один сервисный аккаунт бота | Серверного режима нет → белый список на стороне агента | `board_search_boards`, `content_item_list_roles` (участники доски) |
| **Telegram** | Не MCP: [Bot API `getChatMember`](https://core.telegram.org/bots/api#getchatmember) | Бот — администратор командного чата | Только чтение | `getChatMember` |

Ограничения, которые учитываем:
- **Google Calendar:** у MCP нет фильтра «изменено после» и синхронизационных токенов, поэтому изменения (новые, перенесённые, отменённые) находим **сравнением со снимком**, который `agent` сохраняет в `backend`. Сервер запрашивает полный доступ к календарю, поэтому чтение-только обеспечиваем белым списком и `ENABLED_TOOLS`. Альтернатива — [официальный Google Calendar MCP](https://developers.google.com/workspace/calendar/api/v3/reference/mcp) (Developer Preview, только удалённый, со scope `calendar.events.readonly`).
- **Jira и Miro из РФ:** облачные Atlassian и Miro ограничены для пользователей из РФ (подробно — §11). Разработка — через доступные команде аккаунты; демо для жюри — через демо-режим (§8).

---

## 5. Свой MCP-сервер `buddy-mcp` (в Go-бэкенде)

### 5.1. Зачем он нужен
Готовые серверы отдают данные внешних сервисов. Но агенту нужны и **наши** данные, которых нет ни в одном внешнем сервисе:
- **карточка команды** — кто за что отвечает, маршруты, кого не дёргать (H2);
- **FAQ команды** — сохранённые ответы людей (H2);
- **каталог доступов** — какие объекты в 5 инструментах нужны роли, где запрашивать, кто владелец, что запрещено (H1);
- **профиль и роль** пользователя, его наставник, команда, логины в GitHub, Jira, Miro, Google, Telegram (все кейсы);
- **список стажёров наставника** и их последние статусы доступов (`/interns`);
- **отметки «я запросил»** (H1).

Почему отдаём их через MCP, а не только через REST:
1. **Один интерфейс для Executor:** любые данные — внешние и наши — агент получает одинаково, как инструменты. Проще промпты, белые списки и тесты.
2. **Внедряемость:** эти же инструменты можно подключить к ИИ-платформе Авито, где агенты настраиваются «с выбором MCP» (кейс, стр. 10).
3. **Права в одном месте:** сервер проверяет пользователя на каждом вызове. Стажёр не получит карточку чужой команды, даже если LLM попросит.

### 5.2. Как он устроен
- **Где живёт:** внутри Go-сервиса `backend` — тот же процесс, та же БД, отдельный HTTP-эндпоинт `/mcp`.
- **Библиотека:** [официальный MCP Go SDK](https://github.com/modelcontextprotocol/go-sdk).
- **Транспорт:** Streamable HTTP (JSON-RPC 2.0).
- **Кто пользователь:** заголовок `X-Buddy-User: <user_id>` + общий секрет сервиса `X-Buddy-Key` (агент и бэкенд в одной внутренней сети).
- **Инструмент** = имя + описание + JSON-схема входа + обработчик на Go. Обработчик проверяет права пользователя, делает запрос в PostgreSQL и возвращает JSON.
- **Только чтение:** все записи идут через REST, а не через MCP.

```
backend/
  cmd/backend/main.go         запуск: HTTP-сервер с /api/* (REST) и /mcp (MCP)
  internal/store/             доступ к PostgreSQL (pgx)
  internal/api/               REST-обработчики для agent: брифы, FAQ, снимки, клики, «я запросил»
  internal/mcp/
    server.go                 создание MCP-сервера, регистрация инструментов, проверка X-Buddy-*
    tools_team.go             team_card_get, team_faq_search
    tools_access.go           access_catalog_get, access_marks_get
    tools_people.go           user_profile_get, interns_list
  internal/redirect/          /r/<item_id> — редирект для учёта переходов («прочитано»)
  migrations/                 SQL-миграции (goose)
```

### 5.3. Инструменты `buddy-mcp` (все только чтение)
| Инструмент | Вход | Выход | Кейсы |
|---|---|---|---|
| `user_profile_get` | — | Роль, команда, наставник, тимлид, неделя стажировки, логины во внешних сервисах | Все |
| `team_card_get` | `section?` | Карточка **своей** команды или её раздел | H2 Q1–Q4, Q7, Q8 |
| `team_faq_search` | `text` | Записи FAQ команды | H2 |
| `access_catalog_get` | `objects?` | Объекты 5 инструментов для роли или задачи: ID, где запрашивать, владелец, запрещён ли | H1 |
| `access_marks_get` | — | Отметки «я запросил» с датами | H1 |
| `interns_list` | — | Стажёры наставника или команды тимлида + последние статусы H1 | `/interns` |

### 5.4. Пример вызова (JSON-RPC)
```jsonc
// agent → backend /mcp
{"jsonrpc":"2.0","id":7,"method":"tools/call",
 "params":{"name":"team_card_get","arguments":{"section":"routing"}}}
// заголовки: X-Buddy-User: u_daniil, X-Buddy-Key: ***
// ответ
{"jsonrpc":"2.0","id":7,"result":{"content":[{"type":"text",
 "text":"{\"routing\":[{\"situation\":\"упал staging\",\"go_to\":\"#orders-duty\",\"then\":\"Олег\",\"wait_minutes\":30}]}"}],
 "isError":false}}
```

---

## 6. LLM-часть: Router + Executor

### 6.1. Как работает
- **Router** получает текст и контекст (роль, команда, открытые задачи) и возвращает `{case, params}` — **один из 17 кейсов**. Сам решает, если нужно уточнение (`clarify`) или вопрос вне области (`out_of_scope`). Код проверяет только, что кейс есть в списке, и сразу запускает Executor этого кейса.
- **Executor** выполняет один кейс: свой промпт, своя JSON-схема ответа, **свой белый список инструментов**, не больше 3 вызовов.
- Кейсы по расписанию и кнопкам (`brief_render`, `access_events`) идут сразу в Executor, без Router.

### 6.2. Кейсы и белые списки инструментов
| Кейс | Инструменты (только чтение) |
|---|---|
| `brief_render`, `brief_now` | Программно, без LLM: `list_pull_requests` / `search_pull_requests` / `pull_request_read`, `list_notifications` (GitHub); `jira_search`, `jira_get_issue` (Jira); `list-events` (Calendar). LLM только формулирует бриф |
| `access_status`, `access_events`, `interns_status` | `access_catalog_get`, `access_marks_get`, `interns_list`, `user_profile_get` (buddy-mcp); `list_repository_collaborators` / `get_team_members` (GitHub); `jira_search_assignable_users` (Jira); `content_item_list_roles` (Miro); `list-events` (Calendar); `getChatMember` (Telegram) |
| `access_task_analysis` | Всё из строки выше + `jira_get_issue` |
| `team_q1`–`team_q4`, `team_q7`, `team_q8` | `team_card_get`, `team_faq_search`; для Q8 ещё проверки доступа |
| `team_q5`, `team_q6` | `jira_get_issue`, `jira_search`, `team_card_get` |
| `playbook_qa` | Не MCP: поиск в pgvector |
| `help`, `clarify`, `out_of_scope` | — |

### 6.3. Поток
```
Текст ─► Router ─► {case, params} ─► кейс из списка? ─► Executor(case)
                                                         │ MCP: только инструменты кейса
                                                         ▼
                              {result, citations} ─► код проверяет ссылки и цитаты ─► ответ + кнопки
Кнопки приложения ([Я запросил], эскалация, [Сохранить в FAQ], /teamcard) ─► REST backend (только НАШИ данные)
```

### 6.4. Запасные варианты без LLM
Router — правила по командам и ключевым словам; Executor — шаблоны; бриф — шаблонный; H3 — топ-3 найденных фрагмента со ссылками.

---

## 7. H3: RAG по playbook
- **Корпус:** снимок [avito-tech/playbook](https://github.com/avito-tech/playbook) на фиксированном коммите в `data/playbook/`.
- **Индексация** (скрипт `agent/rag/ingest.py`): разбивка по заголовкам markdown → эмбеддинги → таблица `rag_chunks` (pgvector). У каждого чанка: файл, заголовок, URL на GitHub с якорем.
- **Эмбеддинги:** по умолчанию локальная [intfloat/multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small) (sentence-transformers), модель задаётся через `EMBEDDINGS_MODEL`.
- **Поиск:** топ-5 по косинусной близости + порог; ниже порога — «в playbook этого нет».
- **Альтернатива на первый день:** [GitMCP](https://gitmcp.io) (`https://gitmcp.io/avito-tech/playbook`) — готовый MCP-сервер документации репозитория, чтобы сразу проверить вопросы.

---

## 8. Демо-режим (вместо своего API-мока)

**Задача:** жюри должно запустить проект и пройти сценарий без наших аккаунтов в GitHub, Jira, Google и Miro.

**Решение — статичные ответы:**
- `DEMO_MODE=true` → MCP-клиент агента вместо реальных серверов отвечает **записанными JSON** из `fixtures/`, по ключу «сервер + инструмент + аргументы».
- Файлы фикстур:
  - **записываются** с реальных серверов в режиме `RECORD_MODE=true` (во время разработки);
  - или **пишутся руками** для сценария демо.
- **Шаги сценария:** `fixtures/step_1/`, `step_2/`… Команда `/demo next` переключает шаг: например, «наставник оставил комментарий», «встречу перенесли», «доступ к Miro появился».
- Telegram работает по-настоящему, `getChatMember` — тоже, если задан тестовый чат.
- **Ссылки** в брифе в демо-режиме ведут на реальные публичные объекты, если они есть (например, тестовый репозиторий на GitHub), или на страницы `backend/r/...` с описанием объекта.

Плюсы:
- нет отдельного сервиса-мока;
- код агента одинаковый в реальном и демо-режиме — меняется только транспорт MCP-клиента;
- тесты используют те же фикстуры.

---

## 9. Данные (PostgreSQL)

| Таблица | Назначение |
|---|---|
| `users` | tg_chat_id, роль, команда, наставник, логины во внешних сервисах |
| `user_prefs` | время брифа, тихий режим |
| `team_cards` | версии карточек (YAML → JSON) |
| `access_catalog` | объекты 5 инструментов по ролям, где запрашивать, владелец, запрет |
| `access_marks` | отметки «я запросил» |
| `access_snapshots` | последние статусы доступов пользователя |
| `calendar_snapshots` | снимок событий для поиска изменений (H0) |
| `briefs` | брифы: статус отправки, `read_at` («прочитан»), пункты |
| `brief_events` | клики по ссылкам, [✅ Прочитал], 👍/👎, ответы |
| `questions`, `faq_entries` | вопросы H2, эскалации, ответы, FAQ |
| `rag_chunks` | H3: текст, метаданные, `embedding vector(384)` |
| `mcp_calls` | журнал всех вызовов MCP: кейс, сервер, инструмент, пользователь, результат |
| `clickstream` | события для Grafana |

---

## 10. Безопасность: «только чтение» на трёх уровнях
1. **Серверы:** GitHub — `--read-only`; Jira — `READ_ONLY_MODE=true` + `ENABLED_TOOLS`; Google Calendar — `ENABLED_TOOLS` (только чтение); `buddy-mcp` — без инструментов записи.
2. **Агент:** у каждого кейса белый список; вызов вне списка (в том числе инструментов записи Miro, у которого нет серверного режима «только чтение») отклоняется кодом и пишется в журнал.
3. **Учётные данные:** токены с минимальными правами — GitHub PAT только на чтение, Jira-аккаунт с правами просмотра, Miro — роль viewer у сервисного аккаунта.

Плюс: в сообщениях — ссылки, а не содержимое внутренних документов; секреты — только в env; журнал `mcp_calls` доказывает, что не было ни одного вызова записи.

---

## 11. Доступность сервисов в РФ
| Сервис | Статус | Как учитываем |
|---|---|---|
| GitHub | Доступен | Основной Git |
| Google Calendar | Доступен | По допущению, команда планирует в нём встречи |
| Jira (Atlassian) | Продажи в РФ остановлены в 2022 ([dev.ua](https://dev.ua/news/atlassian-leaves-russia)); облако для РФ отключено к 2024 ([Хабр](https://habr.com/ru/articles/1011252)); self-hosted работает ([CNews](https://cnews.ru/link/b8455)) | `mcp-atlassian` умеет и облако, и self-hosted; для жюри — демо-режим |
| Miro | Закрыт для РФ ([ppc.world](https://ppc.world/news/miro-ogranichila-rossiyanam-dostup-k-akkauntam-saytu-i-podderzhke/), [SecurityLab](https://www.securitylab.ru/news/578823.php)) | По решению команды оставляем; для жюри — демо-режим; на защите честно говорим про ограничение |
| Telegram | Замедление и ограничения с 2025–2026 ([Новая газета Европа](https://novayagazeta.eu/articles/2026/02/10/telegram-snova-blokiruiut-v-rossii)); актуальный статус перепроверить | Канал MVP; бот отделён от логики агента |
| LLM-API | Часть зарубежных может быть недоступна | Провайдер через env |

---

## 12. Нефункциональные требования
| ID | Требование |
|---|---|
| NFR-01 | Локальные действия бота (меню, кнопки) < 1 с |
| NFR-02 | LLM не блокирует бота: «печатает…», таймаут 20 с → запасной вариант |
| NFR-03 | Сбой MCP-сервера → понятный статус, бриф без блока, кнопка «повторить» |
| NFR-04 | Покрытие строк тестами > 80 % отдельно для Go (`backend`) и Python (`agent`); отчёты в репозитории |
| NFR-05 | `docker compose up` по README; с `DEMO_MODE=true` — без внешних аккаунтов |
| NFR-06 | Только синтетические данные; тестовые инструкции не выдаются за регламенты Авито |
| NFR-07 | LLM-провайдер, модель и модель эмбеддингов — через env |
| NFR-08 | 0 вызовов инструментов записи (журнал `mcp_calls`, тест) |
| NFR-09 | Спецификации OpenSpec в `openspec/specs/`, ID требований = ID тестов |

---

## 13. Структура репозитория

```
ai-buddy/
├─ README.md                  запуск, env, демо-режим, тестовые аккаунты, порядок проверки
├─ docker-compose.yml         backend, agent, postgres(pgvector), redis, grafana,
│                             mcp-atlassian, google-calendar-mcp
├─ .env.example               все переменные без секретов
├─ Makefile                   up, demo, test, cover, record-fixtures, ingest
│
├─ backend/                   Go — Илья
│  ├─ cmd/backend/
│  ├─ internal/{api,mcp,store,redirect,flags}/
│  ├─ migrations/
│  └─ go.mod
│
├─ agent/                     Python — Эмиль
│  ├─ buddy/
│  │  ├─ bot/                 aiogram: команды, кнопки, адаптер канала
│  │  ├─ router/              промпт, схема, правила без LLM
│  │  ├─ executors/           по модулю на кейс: brief, access, team, playbook, service
│  │  ├─ mcp_hub/             MCP-клиенты ко всем серверам + белые списки + запись/воспроизведение фикстур
│  │  ├─ brief/               сборщики, снимки календаря, ранжирование, шаблоны
│  │  ├─ access/              проверки 5 инструментов, статусы
│  │  ├─ rag/                 ingest.py, search.py
│  │  ├─ scheduler/           APScheduler (ИИ-крон)
│  │  ├─ llm/                 клиент по env, фейковая LLM для тестов
│  │  └─ prompts/             версионируемые промпты
│  ├─ tests/
│  └─ pyproject.toml
│
├─ mcp/                       конфиги готовых MCP-серверов (env-шаблоны, ENABLED_TOOLS)
│  ├─ github.env.example
│  ├─ atlassian.env.example
│  ├─ gcal.env.example
│  └─ miro.md                 как авторизовать сервисный аккаунт Miro
│
├─ seed/                      users.yaml, team_cards/demo-orders.yaml, access_catalog.yaml
├─ fixtures/                  демо-режим: step_1/ step_2/ … (записанные ответы MCP)
├─ data/playbook/             снимок avito-tech/playbook
├─ evals/                     router/ h1/ h2/ h3/ h0/ + run_evals.py
├─ openspec/                  config.yaml, specs/{h0-brief,h1-access,h2-team,h3-playbook,router}/spec.md, changes/
├─ infra/
│  ├─ grafana/                provisioning + dashboards/ai-buddy.json
│  └─ postgres/init.sql       CREATE EXTENSION vector
├─ .github/workflows/ci.yml   go test -cover, pytest --cov, openspec validate
└─ docs/                      1_PRODUCT.md 2_ARCHITECTURE.md 3_ROADMAP.md
```

---

## 14. Риски
| Риск | Мера | Решение к |
|---|---|---|
| Нет доступа к Jira Cloud или Miro из РФ во время разработки | Работать через доступные аккаунты; всё, что не получилось подключить, — сразу в фикстуры | Ср 18:00 |
| Имена инструментов готовых серверов отличаются от документации | Первым делом выполнить `tools/list` и зафиксировать реальные имена в белых списках | Ср 12:00 |
| Google Calendar MCP не даёт изменений «с момента» | Снимки и сравнение на нашей стороне (заложено) | — |
| Перегрузка Эмиля (бот + агент + RAG) | Даниил помогает: промпты, тексты, фикстуры, наборы evals; RAG — после H1 и H2 | Ежедневно |
| Telegram недоступен в момент защиты | Видео записываем в субботу | Сб |
