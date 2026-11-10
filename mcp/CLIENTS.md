# Клиенты Этапа 1

Область: задачи Эмиля и подготовка данных Даниила. Серверы запускает Илья; здесь нет изменений Compose или backend.
Код использует официальный MCP Python SDK 1.x, зафиксированный в uv.lock. Клиент поддерживает stdio и Streamable HTTP.

## Без аккаунтов

Из корня репозитория после `uv sync --project agent --frozen --extra dev`:

```sh
uv run --project agent python -m buddy.mcp_hub.cli discover
```

По умолчанию CLI работает offline. Каталоги и ответы в fixtures помечены synthetic: это не доказательство подключения к официальным серверам.
Для вызова используйте аргументы из `seed/day_scenario.json`:

```sh
uv run --project agent python -m buddy.mcp_hub.cli call --server jira --tool jira_get_issue --arguments '{"issue_key":"ORD-118"}'
```

`--step step_2` выбирает второй шаг, где синтетический доступ к Miro уже появился. Команда /demo next и реализация кейсов бота относятся к Этапу 2–3 и здесь не добавляются.

## Подключение серверов Ильи

Скопируйте поля из mcp/clients.env.example в локальный .env. Не заменяйте существующий .env целиком.
- GITHUB_MCP_URL / JIRA_MCP_URL / GCAL_MCP_URL: точные адреса Streamable HTTP, включая путь /mcp, полученные от Ильи.
- Для GitHub stdio: GITHUB_MCP_COMMAND и JSON-массив GITHUB_MCP_ARGS. Клиент передаёт GITHUB_READ_ONLY=1; сам исполняемый сервер и его запуск готовит Илья.
- Bearer-токены MCP, если нужны: *_MCP_TOKEN. GitHub PAT для stdio: GITHUB_PERSONAL_ACCESS_TOKEN с минимальными правами чтения.
- Miro: официальный https://mcp.miro.com и OAuth. Не подменяйте read-only PAT полноправным токеном CLI.

```sh
uv run --project agent python -m buddy.mcp_hub.cli discover --live
uv run --project agent python -m buddy.mcp_hub.cli call --live --record --server jira --tool jira_get_issue --arguments '{"issue_key":"ORD-118"}'
```

Discovery проверяет имена и схемы реально публикуемых инструментов. Документированные имена — кандидаты, а не проверенный snapshot.
CLI показывает отсутствие инструмента и не выбирает похожий инструмент автоматически. Отдельно различаются успешный tools/list и успешный tools/call.
Клиент закрывает соединение после диагностики/вызова, поэтому его можно подключить к готовым серверам без управления их жизненным циклом.

## Miro OAuth

```sh
uv run --project agent python -m buddy.mcp_hub.cli auth-miro --live
```

Команда открывает браузер, принимает callback только на 127.0.0.1:8765 и использует SDK для OAuth/PKCE, dynamic registration и refresh.
Выберите тестовую команду Miro и viewer-аккаунт. OAuth-токены и клиент сохраняются только в .env; браузерный callback не логирует код.
Обычная диагностика не открывает браузер сама. OAuth flow, проверенный на локальном тесте, не означает успешного входа в Miro: это отмечается только после live discovery.

## Запись и безопасность

RECORD_MODE=true разрешён только вместе с явным --live; offline+record отклоняются. Внутри Hub действует тот же запрет.
Ключ записи: SHA-256(server/tool/JSON аргументов с отсортированными ключами) + шаг. Ошибки MCP не записываются.
Запись содержит происхождение, аргументы и результат. Настроенные секреты и секретные поля в JSON блокируют сохранение.
Не записывайте реальные корпоративные данные: используйте только синтетические тестовые объекты. Перед коммитом просмотрите записанные данные.
Журнал Hub содержит только кейс, сервер, инструмент, внутренний ID и исход; аргументы, ответы и тексты исключений в журнал не попадают.
Названия инструментов сервера, тексты описаний и результаты считаются внешними данными и не могут менять белый список.
