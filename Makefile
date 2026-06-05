.PHONY: all build-agent build-sdk test test-unit test-integration bench clean fmt lint help

all: build-agent build-sdk

help:
	@echo "Targets:"
	@echo "  build-agent       Build the Go sidecar binary"
	@echo "  build-sdk         Install the Python SDK in editable mode"
	@echo "  test              Run all tests (agent + SDK unit)"
	@echo "  test-unit         Run Python unit tests only (no sidecar needed)"
	@echo "  test-integration  Run integration tests (requires running agent)"
	@echo "  bench             Run benchmarks"
	@echo "  fmt               Format all code (Go + Python)"
	@echo "  lint              Lint all code (Go + Python)"
	@echo "  clean             Remove build artifacts"
	@echo "  agent-run         Run the agent with example config"

# --- Agent (Go) ---

build-agent:
	mkdir -p agent/bin
	cd agent && go build \
		-ldflags "-X main.version=$(shell git describe --tags --always 2>/dev/null || echo dev)" \
		-o bin/taskflow-agent ./cmd/taskflow-agent

test-agent:
	cd agent && go test ./...

fmt-agent:
	cd agent && gofmt -w .

lint-agent:
	cd agent && go vet ./...

# --- SDK (Python 3.13+) ---

build-sdk:
	cd python && pip install -e ".[dev]"

test-unit:
	cd python && pytest tests/unit -v

test-integration:
	cd python && pytest tests/integration -v

bench:
	cd python && pytest tests/benchmark -v --tb=short

fmt-sdk:
	cd python && ruff format .

lint-sdk:
	cd python && ruff check .

# --- Combined ---

test: test-agent test-unit

fmt: fmt-agent fmt-sdk

lint: lint-agent lint-sdk

clean:
	rm -rf agent/bin
	find . -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true
	find . -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.egg-info" -exec rm -rf {} + 2>/dev/null; true

# --- Dev helpers ---

agent-run: build-agent
	./agent/bin/taskflow-agent --config config/taskflow.example.yaml
