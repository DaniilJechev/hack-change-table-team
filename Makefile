COMPOSE ?= docker compose
PYTHON ?= python
GO ?= go
UV ?= uv
OPENSPEC ?= openspec

.PHONY: up demo down test cover smoke validate setup
up:
	$(COMPOSE) up --build -d --wait --wait-timeout 180

demo:
	$(COMPOSE) --env-file .env.example up --build -d --wait --wait-timeout 180

# Named volumes сохраняются.
down:
	$(COMPOSE) down

setup:
	$(UV) sync --project agent --frozen --extra dev --python 3.12

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

.PHONY: seed invite test-backend-integration mcp-up mcp-down mcp-list
seed:
	$(COMPOSE) exec -T backend backend seed

invite:
	$(COMPOSE) exec -T backend backend invite --user "$(USER_ID)" --ttl "$(or $(TTL),24h)"

test-backend-integration:
	$(PYTHON) infra/test_backend.py

MCP_COMPOSE = $(COMPOSE) -f docker-compose.yml -f docker-compose.mcp.yml
mcp-up:
	$(MCP_COMPOSE) up --build -d --wait --wait-timeout 180 mcp-atlassian google-calendar-mcp

mcp-down:
	$(MCP_COMPOSE) stop mcp-atlassian google-calendar-mcp

mcp-list:
	$(UV) run --script infra/mcp_probe.py --compose "$(MCP_COMPOSE)"

ingest record-fixtures:
	@echo "$@: not implemented until the corresponding roadmap stage"
	@exit 1
