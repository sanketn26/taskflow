# Makefile for local virtual environment with Poetry
.PHONY: help setup cleanup test run lint lint-fx package update refresh install venv

# Project-local virtual environment
VENV_DIR := .venv
VENV_BIN := $(VENV_DIR)/bin
PYTHON := $(VENV_BIN)/python
PIP := $(VENV_BIN)/pip
POETRY := $(VENV_BIN)/poetry
ACTIVATE := source $(VENV_BIN)/activate

# Check if virtual environment exists
VENV_EXISTS := $(shell [ -d $(VENV_DIR) ] && echo "true" || echo "false")

help:
	@echo "Available targets:"
	@echo "  setup     - Create local venv, install Poetry, and install all dependencies"
	@echo "  cleanup   - Remove local venv and all build/test artifacts"
	@echo "  test      - Run all unit tests"
	@echo "  run       - Run the library (src/taskflow) as a module"
	@echo "  lint      - Run flake8 lint checks"
	@echo "  lint-fx   - Run isort and black to fix lint issues"
	@echo "  package   - Build the python package"
	@echo "  update    - Run 'poetry lock' to sync lock file"
	@echo "  refresh   - Run 'poetry lock' to regenerate lock file with latest versions"
	@echo "  install   - Run 'poetry install' to install dependencies"
	@echo "  venv      - Create local virtual environment only"
	@echo "  help      - Show this help message"

venv:
	@echo "Creating local virtual environment..."
	python3 -m venv $(VENV_DIR)
	@echo "Virtual environment created at $(VENV_DIR)"

setup: venv
	@echo "Installing Poetry in local virtual environment..."
	$(PIP) install --upgrade pip
	$(PIP) install poetry
	@echo "Installing project dependencies..."
	$(POETRY) install --all-extras --with dev
	@echo "Setup complete! Virtual environment is ready at $(VENV_DIR)"

cleanup:
	@echo "Removing local virtual environment and artifacts..."
	rm -rf $(VENV_DIR)
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf dist .pytest_cache .coverage htmlcov
	find . -type f -name '*.pyc' -delete
	find . -type f -name '*.pyo' -delete
	find . -type f -name '*.pyd' -delete
	find . -type f -name '.DS_Store' -delete
	find . -type d -name '*.egg-info' -exec rm -rf {} +
	find . -type d -name '*.egg' -exec rm -rf {} +
	find . -type d -name '.benchmarks' -exec rm -rf {} +
	@echo "Cleanup complete!"

test:
	$(POETRY) run pytest tests/

run:
	$(POETRY) run python -m src.taskflow

lint:
	$(POETRY) run flake8 src tests

lint-fx:
	$(POETRY) run isort src tests
	$(POETRY) run black src tests

package:
	$(POETRY) build

update:
	$(POETRY) lock

refresh:
	$(POETRY) lock --regenerate

install:
	$(POETRY) install