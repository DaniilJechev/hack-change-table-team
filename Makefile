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
	cd backend && $(GO) test ./...
	$(UV) run --project agent --frozen --extra dev pytest agent/tests

cover:
	$(PYTHON) scripts/check.py coverage

smoke:
	$(UV) run --project agent --frozen python -m buddy --smoke
	$(PYTHON) scripts/compose_smoke.py

validate:
	$(OPENSPEC) validate --all
