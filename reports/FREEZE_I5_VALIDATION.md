# И5: проверка запуска и журнала MCP — 12.11.2026

Проверена цепочка И1 → И3 → И2 (#23 → #24 → #25), commit ed3fa5d.
Изоляция: новый Compose-проект `buddy-freeze-acceptance`, новые именованные тома,
порты backend=18087, agent=18088, Grafana=18089. Настройки из `.env.example`,
все пароли явно синтетические, Telegram/LLM/GitHub/Jira токены пустые.
Рабочая `.env` и основной Compose-проект не менялись.

## Результаты

- `make up COMPOSE='docker compose --env-file .env.example -p buddy-freeze-acceptance'`
  с указанными ниже env: backend и agent собраны; все пять контейнеров healthy;
  Grafana Viewer настроен штатным infra/setup_grafana.py.
- `infra/smoke.py`: HTTP backend/agent/Grafana, Redis, PostgreSQL и pgvector — OK.
- `backend seed` внутри контейнера импортировал пользователей, каталог и правила имитации.
- `python -m buddy --smoke` внутри контейнера: offline /start + fake LLM — OK.
- `infra/freeze_probe.py`: полный офлайн-сценарий Telegram H0–H3 с заявкой H1 прошёл.
  Журнал: 37 MCP-вызовов, из них 36 успешных чтений, 0 успешных внешних записей,
  1 намеренная попытка board_share отклонена с tool_denied. Вызовы по серверам:
  GitHub 13, Jira 9, Calendar 8, Miro 7. Аргументы/ответы не попадают в журнал.
- Тестовый стенд остановлен, тома сохранены; Telegram polling не запускался.
- На момент проверки CI #23/#24/#25/#26 зелёный: tests, compose, mcp.

Дополнительно в И3 отдельно проверены реальные HTTP-запросы к нашему backend:
pending → granted тимлидом, запрет production, изоляция пользователей, отметка прогресса,
перезапуск backend и повторный CLI seed без потери данных. Go race/PostgreSQL — проходят,
backend coverage 91,5%, API 100%; Python — 564 теста, 97,4%.

## Воспроизведение

Используйте отдельный проект и свободные порты. Например, в отдельном терминале
с корнем этого checkout как текущим каталогом:

```sh
export DEMO_MODE=true TELEGRAM_BOT_TOKEN= LLM_API_KEY= LLM_IN_DEMO=false
export GITHUB_PERSONAL_ACCESS_TOKEN= JIRA_API_TOKEN=
export BACKEND_PORT=18087 AGENT_PORT=18088 GRAFANA_PORT=18089
export BUDDY_SERVICE_KEY=freeze_synthetic POSTGRES_PASSWORD=freeze_synthetic
export GRAFANA_ADMIN_PASSWORD=freeze_synthetic GRAFANA_DB_PASSWORD=freeze_metrics_synthetic
export GRAFANA_VIEWER_PASSWORD=freeze_viewer_synthetic
export COMPOSE='docker compose --env-file .env.example -p buddy-freeze-acceptance'
make up
python infra/smoke.py
make seed
$COMPOSE exec -T agent python -m buddy --smoke
uv run --project agent --frozen --extra dev python infra/freeze_probe.py
$COMPOSE stop
```

Переменные демонстрационные; они не должны заменять секреты рабочей установки.
`freeze_probe.py` только читает синтетические фикстуры и работает через фейковый
Telegram-транспорт без сети, не перезаписывает демо-отчёт.

## Ограничения

Это чистые контейнеры/тома **на текущей машине**, а не отдельная физическая машина.
Настоящие корпоративные аккаунты, Telegram polling и внешняя LLM не проверялись;
журнал выше относится к воспроизведению фикстур. CI mcp отдельно проверяет read-only
discovery официальных runtimes с синтетическими credentials. Полный H4/PDF остаётся
за Эмилем; этот прогон подтверждает готовность методов трекера и H1, а не всего H4.
