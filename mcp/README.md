# Готовые MCP-серверы

Этап 1: конфигурация и инфраструктура. Секреты задаются только в корневом `.env`.
Основные `make up` и `make demo` не запускают внешние MCP и не требуют аккаунтов.

## Запуск

1. В `.env` заполнить `GITHUB_PERSONAL_ACCESS_TOKEN`, `JIRA_URL`, `JIRA_USERNAME`,
   `JIRA_API_TOKEN`, `GOOGLE_SERVICE_ACCOUNT_JSON`. Последний — JSON ключа целиком,
   в одинарных кавычках; секретный файл создаётся только внутри tmpfs контейнера.
2. `make up` — агент с готовым GitHub MCP; `make mcp-up` — Jira и Calendar.
3. `make mcp-list` — initialize и tools/list через официальный MCP Python SDK.
   Проверка требует точного совпадения с `mcp/tools.json` и не вызывает инструменты.

Для локального SDK-скрипта используется отдельное окружение uv с зависимостью
`mcp==1.26.0`; зависимости агента и `uv.lock` не изменяются.
Порты доступны только через loopback: Jira 8000 (`MCP_ATLASSIAN_PORT`), Calendar
3001 (`MCP_CALENDAR_PORT`). `make mcp-down` останавливает только внешние серверы.

## Контракт для Эмиля

| Сервер | Внутри Compose | Версия | Ограничение |
|---|---|---|---|
| GitHub | `python /app/github_mcp.py`, stdio; env уже в agent | v2.1.0 | фиксированные `--read-only` и `--tools` |
| Jira | `http://mcp-atlassian:8000/mcp/` | v0.24.1 | `READ_ONLY_MODE=true`, пять `ENABLED_TOOLS` |
| Calendar | `http://google-calendar-mcp:3000/mcp` | npm 2.8.1 | шесть `ENABLED_TOOLS`, отключён `manage-accounts` |

URL также переданы в `MCP_ATLASSIAN_URL` и `MCP_CALENDAR_URL`. Docker socket в
agent не нужен. Все имена — в `tools.json`; это серверный набор, отдельные белые
списки по кейсам и вызовы инструментов реализует Эмиль.
Atlassian 0.24.1 требует `Authorization: Basic base64(JIRA_USERNAME:JIRA_API_TOKEN)`
на каждом HTTP-запросе; обе переменные и `JIRA_URL` переданы агенту. Fallback на
глобальные credentials без авторизации отключён. Скрипт tools/list читает значения
внутри контейнера и не печатает их.
Miro остаётся удалённым сервером с OAuth и клиентским белым списком; см. `miro.md`.

### Особенность Calendar 2.8.1

В upstream `manage-accounts` регистрируется отдельно от `ENABLED_TOOLS` и позволяет
изменять локальную авторизацию. Dockerfile удаляет ровно один вызов его регистрации;
если структура upstream меняется, сборка падает и требует ревью. Остальной код
готового сервера сохраняется. Проверка tools/list проверяет отсутствие этого инструмента.
HTTP-интерфейс администрирования upstream остаётся доступен только в локальной сети
разработки; клиент агента использует только MCP и белый список.

Google: создать отдельный сервисный аккаунт, включить Calendar API и дать ему
право **просмотра** только тестового календаря, без domain-wide delegation.
Upstream запрашивает scope `calendar`; реальное право на объекты ограничивает роль
читателя календаря. GitHub PAT — только чтение нужного репозитория, members и
notifications при необходимости. Jira-аккаунт — без прав изменения проектов.

## Приёмка без аккаунтов

CI подставляет явно синтетические, неработающие credentials и проверяет здоровье
контейнеров и tools/list. Это проверка протокола и набора инструментов.
Реальные `list_pull_requests`, `jira_get_issue`, `list-events` и OAuth Miro необходимо
проверить после подготовки аккаунтов; они не считаются выполненными этим smoke.

Исходники: [GitHub](https://github.com/github/github-mcp-server/tree/v2.1.0),
[Atlassian](https://github.com/sooperset/mcp-atlassian/tree/v0.24.1),
[Calendar](https://github.com/nspady/google-calendar-mcp/tree/v2.8.1).
