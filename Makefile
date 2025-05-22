# Makefile for taskflow using Poetry
# 
# > make help
#
# For detailed information about each command, run 'make help'

.PHONY: help setup install clean venv test coverage test-one lint lint-fix isort

define find.functions
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//'
endef

help:
	@echo 'The following commands can be used:'
	@echo ''
	$(call find.functions)

# Check if Poetry is installed and set POETRY_CMD
poetry-check:
	@if command -v poetry >/dev/null 2>&1; then \
		echo "Using system Poetry"; \
		POETRY_CMD="poetry"; \
	elif [ -f "$$HOME/.local/bin/poetry" ]; then \
		echo "Using Poetry from ~/.local/bin"; \
		POETRY_CMD="$$HOME/.local/bin/poetry"; \
	else \
		echo "Poetry is not installed. Installing poetry..."; \
		curl -sSL https://install.python-poetry.org | python3 -; \
		POETRY_CMD="$$HOME/.local/bin/poetry"; \
	fi; \
	export POETRY_CMD;

setup: ## Setup poetry and install all dependencies including development
setup: poetry-check
	$(HOME)/.local/bin/poetry install --all-extras --with dev

install: ## Install only production dependencies
install: poetry-check
	$(HOME)/.local/bin/poetry install --only main

dev-deps: ## Install development dependencies
dev-deps: poetry-check
	$(HOME)/.local/bin/poetry install --only dev

venv: ## Create and configure Poetry virtual environment
venv: poetry-check
	$(HOME)/.local/bin/poetry env use python3
	@echo "To activate: $(HOME)/.local/bin/poetry shell"

clean: ## Remove all build and cache files
clean:
	rm -rf dist
	rm -rf .pytest_cache
	rm -rf .coverage
	rm -rf htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.pyd" -delete
	find . -type f -name ".DS_Store" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -type d -name "*.egg" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".coverage" -exec rm -rf {} +
	find . -type d -name "htmlcov" -exec rm -rf {} +
	find . -type d -name ".benchmarks" -exec rm -rf {} +

clean-venv: ## Remove Poetry virtual environment
clean-venv:
	$(HOME)/.local/bin/poetry env remove --all

lint: ## Run linting checks with flake8
lint: poetry-check
	$(HOME)/.local/bin/poetry run flake8 src tests

lint-fix: ## Fix linting issues automatically where possible with black
lint-fix: poetry-check
	$(HOME)/.local/bin/poetry run black src tests

isort: ## Sort imports with isort
isort: poetry-check
	$(HOME)/.local/bin/poetry run isort src tests

package: ## Create distribution package
package: clean
	$(HOME)/.local/bin/poetry build

upload-test: ## Upload package to TestPyPI
upload-test: package
	$(HOME)/.local/bin/poetry publish --repository testpypi

upload: ## Upload package to PyPI
upload: package
	$(HOME)/.local/bin/poetry publish

test: ## Run all tests
test: poetry-check
	$(HOME)/.local/bin/poetry run pytest tests/

test-one: ## Run a specific test file, e.g., make test-one file=tests/test_file.py
test-one: poetry-check
	$(HOME)/.local/bin/poetry run pytest $(file) -v

coverage: ## Run tests with coverage
coverage: poetry-check
	$(HOME)/.local/bin/poetry run pytest --cov=src --cov-report=html --cov-report=term tests/

install-lint-deps: ## Install linting dependencies
install-lint-deps: poetry-check
	$(HOME)/.local/bin/poetry add --group dev flake8 black isort