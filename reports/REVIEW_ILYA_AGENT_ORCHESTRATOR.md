# Проверка зоны backend/DevOps: ветка `feat/agent-orchestrator`

Для: Илья. Дата: 13.11.2026.
Ветка: `feat/agent-orchestrator` (9 коммитов от `main`, последний `fefc921`).

В ветке появились агентный цикл на LLM, сноски-ссылки в Telegram и H4 (проверка презентации к экватору). Ниже только то, что затрагивает твою зону: `backend/`, `infra/`, Compose, `Makefile`, CI и готовые MCP-серверы. Пункты идут по убыванию важности.

Что проверено: тесты `agent` проходят (690 passed). Что не проверено: `go test ./...` (в моём окружении нет `go`) и все задачи CI.

## 1. CI, задача MCP: исправлено 13.11 (коммит `aaa10b8`)

Что было:
- В `.github/workflows/ci.yml` (строка 97) сервисы поднимались только из `docker-compose.yml` и `docker-compose.mcp.yml`, без `powerpoint-mcp`.
- Следующий шаг, `make mcp-list`, теперь использует `MCP_COMPOSE` с overlay `docker-compose.powerpoint.yml`.
- `infra/mcp_probe.py` опрашивает все серверы из `mcp/tools.json`, а там добавлен `powerpoint`.
- В CI шаг упал на `docker compose port powerpoint-mcp 8000`: `service "powerpoint-mcp" is not running`.

Что сделано:
- В шаги `up`, `logs` и `down` этой задачи добавлен `-f docker-compose.powerpoint.yml`, так что CI теперь поднимает и опрашивает сервер PowerPoint.
- `seed/check_stage_one.py` проверяет только GitHub, Jira и Calendar, его это не задевает.
- Заодно исправлен порядок импортов в `agent/tests/test_h4_equator.py`, на котором падал ruff в задаче `tests`.

Проверить: что задачи `mcp` и `tests` в PR прошли. Если сервер PowerPoint не нужен в общей проверке, можно вместо этого опрашивать его только через `make pptx-list`.

## 2. Новый MCP-сервер PowerPoint (H4)

PPTX читается готовым [Office-PowerPoint-MCP-Server](https://github.com/GongRzhe/Office-PowerPoint-MCP-Server) 2.0.7 (MIT) через нашу обёртку. PDF читает сам агент через pypdf, этот путь сервера не касается.

### `mcp/powerpoint/entrypoint.py`
- Из примерно 30 инструментов сторонней библиотеки остаются два: `open_presentation` и `extract_presentation_text`. Остальные удаляются из реестра через приватное поле `app._tool_manager._tools`. Если реестр выглядит иначе, сервер не стартует. Проверить, что при смене версии библиотеки сервер именно не стартует, а не работает с лишними инструментами.
- `open_presentation` принимает только имя `.pptx` внутри `PPTX_ROOT`: абсолютные пути и `../` отклоняются. Сообщение об открытии не раскрывает путь на сервере.
- В памяти держится не больше 20 открытых презентаций, самые старые вытесняются.
- Защита от DNS rebinding остаётся включённой. `allowed_hosts` берётся из `ALLOWED_HOSTS`, по умолчанию `localhost:*,127.0.0.1:*,powerpoint-mcp:*`.

### `mcp/powerpoint/Dockerfile` и `requirements.txt`
- Реестр проверяется на этапе сборки (`runpy.run_path` без запуска сервера), сервер работает от uid 10001.
- Версии зафиксированы: `office-powerpoint-mcp-server==2.0.7`, `mcp==1.26.0`, `python-pptx==1.0.2`, `pillow==11.3.0`, `fonttools==4.59.0`. У агента `mcp>=1.28`. Образы разные, но расхождение стоит иметь в виду.

### `docker-compose.powerpoint.yml` (новый overlay, включается вручную)
- `powerpoint-mcp`: `read_only: true`, `init: true`, `user: 10001:10001`, порт только на `127.0.0.1:${MCP_POWERPOINT_PORT:-8002}`, `/tmp` в tmpfs, проверка готовности по TCP.
- Том `presentations` хранится в памяти (`tmpfs`, `uid=10001,gid=10001,mode=0700`). Агент пишет туда загруженный файл, MCP читает его только на чтение (`:ro`). Файл удаляется сразу после чтения.
- Overlay меняет и сервис `agent`: добавляет `PPTX_MCP_URL=http://powerpoint-mcp:8000/mcp`, `PPTX_UPLOAD_DIR=/presentations` и том.
- Проверить на живом Compose, что агент (uid 10001, `read_only: true`) может писать в `/presentations`.

### `mcp/tools.json`, `mcp/README.md`, `infra/mcp_probe.py`
- Добавлен набор `powerpoint` с двумя инструментами и строка в таблице README.
- `mcp_probe.py` принимает `--only powerpoint`; выбор сервиса, порта и пути переписан на словарь.

### `Makefile`
- `MCP_COMPOSE` теперь включает `docker-compose.powerpoint.yml`, поэтому `mcp-up` и `mcp-down` всегда поднимают и останавливают `powerpoint-mcp`.
- Новые команды: `pptx-up` (только основной Compose и PowerPoint) и `pptx-list`.
- Проверить, что `make mcp-up` не мешает живому режиму без H4.

## 3. Значения по умолчанию в Compose и `.env`

`docker-compose.yml`, сервис `agent`, новые переменные:

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `AGENT_MODE` | `agent` | `agent` — LLM отвечает сама и вызывает кейсы как инструменты; `router` — прежний путь Router → Executor |
| `MEMORY_MAX_TURNS` | `6` | окно диалоговой памяти в ходах |
| `MEMORY_TTL_SECONDS` | `1800` | время жизни памяти в Redis |

- **Важно:** в живом режиме теперь по умолчанию отвечает агент на LLM. Ход занимает примерно 3–4 с (p95 около 6 с, бриф около 10 с) вместо прежних 1–3 с. По `evals/agent/report.json` это около $0.0002 за ход. Без настоящей LLM (`LLM_PROVIDER=fake`, демо без `LLM_IN_DEMO`) работает прежний путь.
- В Redis теперь лежит текст реплик: до 600 символов на реплику, секреты вырезаются, TTL 30 минут.

`.env.example`:
- `LLM_MODEL` сменился с `anthropic/claude-haiku-5.5` на `deepseek/deepseek-v4.1-flash-1017`.
- Добавлены `AGENT_MODE`, `MEMORY_MAX_TURNS`, `MEMORY_TTL_SECONDS`.
- Добавлены `LANGSMITH_TRACING=false`, `LANGSMITH_API_KEY=`, `LANGSMITH_PROJECT=ai-buddy`. Трассировка в облако LangSmith выключена.
- Проверить, что `docker compose --env-file .env.example config --quiet` в CI проходит.

## 4. Зависимости и образ агента

- `agent/pyproject.toml` и `agent/uv.lock`: в основные зависимости добавлены `langsmith>=0.4` и `pypdf>=6,<7`, так что образ агента пересоберётся с ними.
- Новый extra `studio` (`langgraph-cli[inmem]`) нужен только для локальной разработки и в образ не попадает.
- LangGraph Studio: новые `langgraph.json` и `make studio` (`langgraph dev` на порту `STUDIO_PORT=2024`, флаг `--allow-blocking`, только демо-фикстуры). В `.gitignore` добавлен `.langgraph_api/`.

## 5. Backend

- `backend/internal/store/team_card.go`: у человека в карточке команды появилось необязательное текстовое поле `about` в `personSchema`.
- `backend/internal/store/stage_three_test.go`: в YAML для загрузки добавлено `about`.
- Проверить: `go test ./...` и загрузку карточки через REST — и с `about`, и без него.

## 6. Что запустить

```bash
make test
```

```bash
make up && make buddy-check
```

```bash
make pptx-up && make pptx-list
```

```bash
make mcp-up && make mcp-list
```

И убедиться, что CI на ветке зелёный, в том числе задача MCP после исправления (см. п. 1).

## Связанные документы
- `openspec/changes/agent-orchestrator/proposal.md`, раздел 6 «Зоны ответственности».
- `openspec/changes/h4-equator-check-draft/` и `reports/H4_EQUATOR_REPORT.md` — H4 и сервер PowerPoint.
- `mcp/README.md` — таблица MCP-серверов.
