SHELL := /bin/sh
COMPOSE ?= docker compose
PYTHON ?= $(if $(wildcard .venv/bin/python),$(CURDIR)/.venv/bin/python,python3)
REPORTS_DIR := $(CURDIR)/reports
OPENSPEC_VERSION := 1.17.0

.PHONY: help up down demo logs ps smoke dev test test-go test-agent cover cover-go cover-agent check-config validate-specs ingest record-fixtures

help:
	@echo 'up / demo       — собрать каркас и дождаться healthchecks'
	@echo 'down / logs / ps — остановить / логи / состояние контейнеров'
	@echo 'smoke           — проверить пять сервисов после up или demo'
	@echo 'dev             — установить Python-зависимости для тестов в .venv'
	@echo 'test / cover    — Go + Python; отчёты покрытия в reports/'
	@echo 'check-config    — проверить Compose без запуска Docker daemon'
	@echo 'validate-specs  — проверить OpenSpec (Node >= 22.12)'

up:
	$(COMPOSE) up --build --detach --wait --wait-timeout 180

demo:
	DEMO_MODE=true $(COMPOSE) up --build --detach --wait --wait-timeout 180

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs --follow

ps:
	$(COMPOSE) ps

smoke:
	COMPOSE="$(COMPOSE)" $(PYTHON) infra/smoke.py $(SMOKE_ARGS)

dev:
	python3 -m venv .venv
	.venv/bin/python -m pip install -e './agent[dev]'

test: test-go test-agent

test-go:
	cd backend && go test -race -cover ./...
	cd backend && go vet ./...

test-agent:
	cd agent && $(PYTHON) -m pytest

cover: cover-go cover-agent

cover-go:
	mkdir -p "$(REPORTS_DIR)/backend"
	cd backend && go test -race -covermode=atomic -coverprofile="$(REPORTS_DIR)/backend/coverage.out" ./...
	cd backend && go tool cover -html="$(REPORTS_DIR)/backend/coverage.out" -o "$(REPORTS_DIR)/backend/index.html"
	cd backend && go tool cover -func="$(REPORTS_DIR)/backend/coverage.out" > "$(REPORTS_DIR)/backend/functions.txt"
	@cat "$(REPORTS_DIR)/backend/functions.txt"
	@awk '/^total:/ { found=1; if ($$3+0 <= 80) { print "Backend coverage must be > 80%"; exit 1 } } END { if (!found) exit 1 }' "$(REPORTS_DIR)/backend/functions.txt"

cover-agent:
	mkdir -p "$(REPORTS_DIR)/agent"
	cd agent && $(PYTHON) -m pytest --cov=buddy --cov-report=term-missing --cov-report=xml:"$(REPORTS_DIR)/agent/coverage.xml" --cov-report=html:"$(REPORTS_DIR)/agent/html" --cov-fail-under=80.01

check-config:
	$(COMPOSE) config --quiet

validate-specs:
	OPENSPEC_TELEMETRY=0 npx --yes @fission-ai/openspec@$(OPENSPEC_VERSION) validate --all

ingest record-fixtures:
	@echo '$@: будет реализовано Эмилем на следующих этапах; каркас не выполняет эту операцию.' >&2
	@exit 1
