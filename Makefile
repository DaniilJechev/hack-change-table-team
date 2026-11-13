COMPOSE ?= docker compose
PYTHON ?= python
GO ?= go
UV ?= uv
STUDIO_PORT ?= 2024
OPENSPEC ?= openspec

.PHONY: up demo down test cover smoke validate setup studio
up:
	$(COMPOSE) up --build -d --wait --wait-timeout 180
	$(PYTHON) infra/setup_grafana.py --compose "$(COMPOSE)"

demo:
	$(COMPOSE) --env-file .env.example up --build -d --wait --wait-timeout 180
	$(PYTHON) infra/setup_grafana.py --compose "$(COMPOSE) --env-file .env.example"

# Named volumes сохраняются.
down:
	$(COMPOSE) down

setup:
	$(UV) sync --project agent --frozen --extra dev --python 3.12

# LangGraph Studio: граф агента на демо-фикстурах, чат + состояния + трейсы. API: http://127.0.0.1:2024
# --allow-blocking: демо читает фикстуры синхронно (os.getcwd, open); только для локального dev-сервера.
studio:
	$(UV) run --project agent --frozen --extra studio langgraph dev --config langgraph.json --no-browser --allow-blocking --port $(STUDIO_PORT)

test:
	$(PYTHON) infra/test_backend.py
	$(UV) run --project agent --frozen --extra dev pytest agent/tests

cover:
	$(PYTHON) scripts/check.py coverage

smoke:
	$(UV) run --project agent --frozen python -m buddy --smoke
	$(PYTHON) infra/smoke.py --compose "$(COMPOSE)" $(SMOKE_ARGS)

validate:
	$(OPENSPEC) validate --all

.PHONY: logs ps check-config validate-specs dev ingest record-fixtures
logs:
	$(COMPOSE) logs --follow

ps:
	$(COMPOSE) ps

check-config:
	$(COMPOSE) config --quiet

validate-specs: validate

dev: setup

.PHONY: seed invite test-backend-integration mcp-up mcp-down mcp-list buddy-check pptx-up pptx-list
seed:
	$(COMPOSE) exec -T backend backend seed

buddy-check:
	$(PYTHON) infra/buddy_probe.py --compose "$(COMPOSE)"

.PHONY: grafana-setup
grafana-setup:
	$(PYTHON) infra/setup_grafana.py --compose "$(COMPOSE)"

.PHONY: stage4-check
stage4-check:
	$(PYTHON) infra/stage_four_probe.py --compose "$(COMPOSE)"

.PHONY: github-check
github-check:
	$(PYTHON) infra/github_probe.py --compose "$(COMPOSE)" --user "$(USER_ID)"

invite:
	$(COMPOSE) exec -T backend backend invite --user "$(USER_ID)" --ttl "$(or $(TTL),24h)"

test-backend-integration:
	$(PYTHON) infra/test_backend.py

MCP_COMPOSE = $(COMPOSE) -f docker-compose.yml -f docker-compose.mcp.yml -f docker-compose.powerpoint.yml
PPTX_COMPOSE = $(COMPOSE) -f docker-compose.yml -f docker-compose.powerpoint.yml
mcp-up:
	$(MCP_COMPOSE) up --build -d --wait --wait-timeout 180 mcp-atlassian google-calendar-mcp powerpoint-mcp

mcp-down:
	$(MCP_COMPOSE) stop mcp-atlassian google-calendar-mcp powerpoint-mcp

pptx-up:
	$(PPTX_COMPOSE) up --build -d --wait --wait-timeout 180

pptx-list:
	$(UV) run --script infra/mcp_probe.py --compose "$(PPTX_COMPOSE)" --only powerpoint

mcp-list:
	$(UV) run --script infra/mcp_probe.py --compose "$(MCP_COMPOSE)"

# H3: index of the playbook snapshot. With RAG_SEARCH_MODE=vector|rrf it also writes e5
# embeddings to pgvector (needs `make setup-rag` and RAG_DATABASE_URL); bm25 needs neither.
ingest:
	$(UV) run --project agent --frozen python -m buddy.rag.ingest

.PHONY: setup-rag ingest-compose
setup-rag:
	$(UV) sync --project agent --frozen --extra dev --extra rag --python 3.12

# Embeddings from inside the running agent container (image built with AGENT_EXTRAS="--extra rag").
ingest-compose:
	$(COMPOSE) exec -T agent python -m buddy.rag.ingest --embed-only

record-fixtures:
	@echo "$@: not implemented until the corresponding roadmap stage"
	@exit 1
