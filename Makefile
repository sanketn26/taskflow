.PHONY: all help build sync format lint unit integration packaging test wheel clean db-up db-down \
        go-lint go-unit go-integration py-lint py-unit py-integration

REPO_ROOT := $(shell git rev-parse --show-toplevel 2>/dev/null || pwd)
UV        ?= uv
PY_DIR    := $(REPO_ROOT)/clients/python
PYRUN     := cd $(PY_DIR) && $(UV) run

# Local PostgreSQL for integration tests (see `make db-up`).
PG_VERSION   ?= 18
PG_PORT      ?= 54329
PG_PASSWORD  ?= taskwire
TEST_DB_URL  ?= postgresql://postgres:$(PG_PASSWORD)@localhost:$(PG_PORT)/postgres

all: help

help:
	@echo "Targets (each runs Go and Python unless noted):"
	@echo "  build          Build bin/taskwire (Go)"
	@echo "  sync           Create the Python venv from clients/python/uv.lock"
	@echo "  format         Format Go and Python code"
	@echo "  lint           go vet, gofmt check, ruff"
	@echo "  unit           Unit tests (no database)"
	@echo "  db-up/db-down  Start/stop a local PostgreSQL container"
	@echo "  integration    Go tests against \$$TASKWIRE_TEST_DATABASE_URL (default: the db-up container)"
	@echo "  packaging      Build the Python wheel and test it in a clean venv"
	@echo "  clean          Remove build artifacts"
	@echo "  go-lint go-unit go-integration py-lint py-unit py-integration: one side only"

build:
	cd $(REPO_ROOT) && go build -o bin/taskwire ./cmd/taskwire

sync:
	cd $(PY_DIR) && $(UV) sync --all-extras --locked

format:
	cd $(REPO_ROOT) && gofmt -w cmd internal
	$(PYRUN) ruff format .

lint: go-lint py-lint
unit: go-unit py-unit
integration: go-integration py-integration

go-lint:
	cd $(REPO_ROOT) && go vet ./...
	@cd $(REPO_ROOT) && out="$$(gofmt -l cmd internal)"; [ -z "$$out" ] || { echo "gofmt needed:"; echo "$$out"; exit 1; }

go-unit:
	cd $(REPO_ROOT) && go test ./...

# Tests that need PostgreSQL skip when the variable is unset; TASKWIRE_REQUIRE_INTEGRATION=1
# makes that a failure. -count=1 so a cached pass is never reported for a database test.
go-integration:
	cd $(REPO_ROOT) && TASKWIRE_TEST_DATABASE_URL=$${TASKWIRE_TEST_DATABASE_URL:-$(TEST_DB_URL)} \
		go test -count=1 ./...

py-lint:
	$(PYRUN) ruff check .
	$(PYRUN) ruff format --check .

py-unit:
	$(PYRUN) pytest tests/unit -v

# Python tests against a real controller arrive with the controller (Phase 2).
py-integration:
	@echo "no Python integration tests yet (they need the controller, Phase 2)"

# PostgreSQL from docker-compose.yml. Data is in tmpfs, so db-down also deletes it.
db-up:
	cd $(REPO_ROOT) && PG_VERSION=$(PG_VERSION) PG_PORT=$(PG_PORT) PG_PASSWORD=$(PG_PASSWORD) \
		docker compose up -d --wait postgres
	@echo 'export TASKWIRE_TEST_DATABASE_URL=$(TEST_DB_URL)'

db-down:
	cd $(REPO_ROOT) && docker compose down -v

packaging:
	$(PYRUN) pytest tests/integration -v -m packaging

test: unit

wheel:
	cd $(PY_DIR) && $(UV) build --wheel

# Build output only: bin/ and the Python dist/ and build/ directories.
clean:
	rm -rf bin clients/python/dist clients/python/build
