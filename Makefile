.PHONY: all help sync format lint unit integration packaging test wheel clean db-up db-down

REPO_ROOT := $(shell git rev-parse --show-toplevel 2>/dev/null || pwd)
UV        ?= uv

# Local PostgreSQL for integration tests (see `make db-up`).
PG_VERSION   ?= 18
PG_CONTAINER ?= taskwire-test-pg
PG_PORT      ?= 54329
PG_PASSWORD  ?= taskwire
TEST_DB_URL  ?= postgresql://postgres:$(PG_PASSWORD)@localhost:$(PG_PORT)/postgres
PYTHON    ?= $(UV) run python

all: help

help:
	@echo "Targets:"
	@echo "  sync           Create .venv and install all deps from uv.lock"
	@echo "  format         Format Python code"
	@echo "  lint           Lint Python code"
	@echo "  unit           Run unit tests (no database required)"
	@echo "  db-up          Start a local PostgreSQL container for integration tests"
	@echo "  db-down        Stop that container"
	@echo "  integration    Run integration tests against \$$TASKWIRE_TEST_DATABASE_URL (default: the db-up container)"
	@echo "  packaging      Build the wheel and test it in a clean venv"
	@echo "  wheel          Build the wheel"
	@echo "  clean          Remove build artifacts"

sync:
	cd $(REPO_ROOT) && $(UV) sync --all-extras --locked

format:
	cd $(REPO_ROOT) && $(PYTHON) -m ruff format python harness

lint:
	cd $(REPO_ROOT) && $(PYTHON) -m ruff check python harness
	cd $(REPO_ROOT) && $(PYTHON) -m ruff format --check python harness

unit:
	cd $(REPO_ROOT) && $(PYTHON) -m pytest python/tests/unit -v

integration:
	cd $(REPO_ROOT) && TASKWIRE_TEST_DATABASE_URL=$${TASKWIRE_TEST_DATABASE_URL:-$(TEST_DB_URL)} \
		$(PYTHON) -m pytest python/tests/integration -v -m integration

# The container is started with --rm, so stopping it also deletes it.
db-up:
	-@docker stop $(PG_CONTAINER) >/dev/null 2>&1
	@for i in $$(seq 1 30); do \
		[ -z "$$(docker ps -aq -f name=^$(PG_CONTAINER)$$)" ] && break; sleep 1; done
	docker run -d --rm --name $(PG_CONTAINER) -e POSTGRES_PASSWORD=$(PG_PASSWORD) \
		-p 127.0.0.1:$(PG_PORT):5432 postgres:$(PG_VERSION) >/dev/null
	@printf 'waiting for PostgreSQL $(PG_VERSION)'
	@for i in $$(seq 1 60); do \
		if docker exec $(PG_CONTAINER) pg_isready -U postgres -h 127.0.0.1 >/dev/null 2>&1; then \
			echo ' ready'; echo 'export TASKWIRE_TEST_DATABASE_URL=$(TEST_DB_URL)'; exit 0; fi; \
		printf .; sleep 1; done; echo ' timed out'; docker logs $(PG_CONTAINER) | tail -20; exit 1

db-down:
	-docker stop $(PG_CONTAINER)

packaging:
	cd $(REPO_ROOT) && $(PYTHON) -m pytest python/tests/integration -v -m packaging

test: unit

wheel:
	cd $(REPO_ROOT) && $(UV) build --wheel

clean:
	rm -rf $(REPO_ROOT)/dist $(REPO_ROOT)/build
	find $(REPO_ROOT) -name "__pycache__" -not -path "*/.venv/*" -exec rm -rf {} + 2>/dev/null; true
	find $(REPO_ROOT) -name ".pytest_cache" -not -path "*/.venv/*" -exec rm -rf {} + 2>/dev/null; true
