# Живой GitHub проекта

Ветка реализации: `feat/github-project-source`, основание main `8c37a88`.
Источник: https://github.com/DaniilJechev/hack-change-table-team.
Персональный режим согласован с Ильёй: свои PR, изменения ревью и комментарии к ним,
запросы ревью пользователю. Общая новостная лента всех PR/коммитов не добавляется.

## Настройка .env

```dotenv
DEMO_MODE=false
GITHUB_REPOSITORY=DaniilJechev/hack-change-table-team
GITHUB_TEAM_ID=demo-orders
GITHUB_CATALOG_OBJECT_ID=demo-orders-github
GITHUB_USER_LOGINS='{"Emil_Mardanov":"your-github-login"}'
GITHUB_PERSONAL_ACCESS_TOKEN=...
```

Замените `your-github-login` настоящим GitHub-логином без `@`. `Emil_Mardanov` —
внутренний ID пользователя backend, а не автоматически определённый GitHub login.
Для нескольких пользователей JSON может содержать несколько пар. Для аккаунта без
настроенного live-логина GitHub-источник недоступен: синтетическое имя не подставляется.
Не записывайте настоящий PAT в seed, Git или чат; только в локальный `.env`.
Для живого бота также должны быть заполнены TELEGRAM_BOT_TOKEN и LLM_PROVIDER,
LLM_MODEL, LLM_API_KEY (и LLM_BASE_URL для compatible) по основному README.

Настройка применяется только к `demo-orders`, к разрешённому объекту
`demo-orders-github`; запрещённый production и другие команды сохраняются как есть.
Она не меняет версии карточки, Telegram-привязки, seed, фикстуры и данные других сервисов.
Повтор `make seed` не стирает настройку: target и live-логин проецируются из env при чтении.
Без GITHUB_REPOSITORY действует прежнее поведение. После изменения env пересоздайте backend.

## Токен GitHub

Для этого персонального репозитория самый прямой вариант — fine-grained PAT,
созданный владельцем **DaniilJechev** и ограниченный **только hack-change-table-team**:
repository permissions **Pull requests: Read-only**, **Metadata: Read-only**.
Полномочия записи токену не нужны, готовый MCP дополнительно запускается с `--read-only`.

Для H1 GitHub требует, чтобы владелец токена имел write/maintain/admin доступ к репе,
хотя самому fine-grained токену достаточно Metadata read. Поэтому токен произвольного
читателя может читать PR, но не список collaborators: H0 работает, H1 показывает unknown.
Это не отсутствие доступа проверяемого пользователя. На публичной репе H1 проверяет
именно наличие в collaborators, а не просто возможность читать общедоступный код.

Источники прав: [список PR](https://docs.github.com/en/rest/pulls/pulls#list-pull-requests),
[участники репозитория](https://docs.github.com/en/rest/collaborators/collaborators#list-repository-collaborators).
Схемы ответов сверены с закреплённым [GitHub MCP v2.1.0](https://github.com/github/github-mcp-server/tree/v2.1.0).

## Запуск и проверка

Пользователь запускает бот самостоятельно:

```bash
make up
make seed
make github-check USER_ID=Emil_Mardanov
```

GitHub MCP уже в образе agent, отдельный `make mcp-up` для GitHub не нужен.
`github-check` использует настоящий MCP stdio в контейнере, читает профиль/каталог
через buddy-mcp, собирает GitHub-изменения за 24 часа и проверяет collaborators.
Он не запускает Telegram polling и не создаёт бриф/событие отправки; метаданные MCP
пишутся в наш журнал. Недоступность H1 выводится отдельно от работоспособности H0.
Для проверки токена другой команды передайте её user_id: настройки demo-orders ей не выдаются.

В личном чате после привязки `/start <код>`:

- «Есть что по PR?» — только GitHub, без ошибок Jira/Calendar в этом коротком ответе.
- `/brief` — общий H0: GitHub + Jira/Calendar, если они настроены.
- `/access` — GitHub проверяется по collaborators этой репы.
- «Где репозиторий?» — ссылка на эту репу из авторизованного каталога.

Если своих PR, новых ревью/комментариев и запросов ревью за период нет,
«Изменений нет» — нормальный результат. LLM не придумывает GitHub-активность.

## Демо и ограничения

`DEMO_MODE=true` и `/demo` по-прежнему работают только на старых фикстурах:
они не превращаются в копию настоящего репозитория и не обращаются к GitHub.
Чтобы проверять реальные данные, нужен live-режим и привязанный профиль backend.

Live-сборщик читает до 100 последних обновлённых PR, до 100 отзывов/комментариев/тредов
на свой изменённый PR. Полная история, произвольный поиск по коду, issues и CI-checks
этим изменением не реализуются. За GitHub API отвечает официальный read-only MCP;
личные PAT пользователей не связываются с Telegram автоматически.

Брифы сохраняются с переводом внутренних значений агента info/calendar в контракт
backend information/google_calendar, а при чтении переводятся обратно. Это сохраняет
одобрения PR и восстановление кнопок после рестарта. Размеры истории/память диалога
остаются отдельной задачей агента.
