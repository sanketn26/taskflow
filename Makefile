.PHONY: all help sync format lint unit integration packaging test wheel clean

REPO_ROOT := $(shell git rev-parse --show-toplevel 2>/dev/null || pwd)
UV        ?= uv
PYTHON    ?= $(UV) run python

all: help

help:
	@echo "Targets:"
	@echo "  sync           Create .venv and install all deps from uv.lock"
	@echo "  format         Format Python code"
	@echo "  lint           Lint Python code"
	@echo "  unit           Run unit tests (no database required)"
	@echo "  integration    Run integration tests (requires PostgreSQL; see docs/phases/phase-0-repository.md)"
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
	cd $(REPO_ROOT) && $(PYTHON) -m pytest python/tests/integration -v -m integration

packaging:
	cd $(REPO_ROOT) && $(PYTHON) -m pytest python/tests/integration -v -m packaging

test: unit

wheel:
	cd $(REPO_ROOT) && $(UV) build --wheel

clean:
	rm -rf $(REPO_ROOT)/dist $(REPO_ROOT)/build
	find $(REPO_ROOT) -name "__pycache__" -not -path "*/.venv/*" -exec rm -rf {} + 2>/dev/null; true
	find $(REPO_ROOT) -name ".pytest_cache" -not -path "*/.venv/*" -exec rm -rf {} + 2>/dev/null; true
