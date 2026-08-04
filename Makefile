.PHONY: all format lint unit integration build-agent build-sdk wheel smoke-wheel \
	test bench clean agent-run proto help

REPO_ROOT := $(shell git rev-parse --show-toplevel 2>/dev/null || pwd)
VERSION   := $(shell grep -m1 '^version' $(REPO_ROOT)/pyproject.toml | cut -d'"' -f2)
PYTHON    ?= python3

all: help

help:
	@echo "Targets:"
	@echo "  format         Format all code (Go + Python)"
	@echo "  lint           Lint all code (Go + Python)"
	@echo "  unit           Run unit tests (Go + Python), no built agent required"
	@echo "  integration    Run integration tests (builds and drives the agent)"
	@echo "  build-agent    Build the Go sidecar binary to agent/bin/"
	@echo "  build-sdk      Install the Python SDK in editable mode"
	@echo "  wheel          Build a wheel bundling the built agent binary"
	@echo "  smoke-wheel    Install the wheel into a clean venv and smoke-test it"
	@echo "  bench          Run benchmarks"
	@echo "  clean          Remove build artifacts"
	@echo "  agent-run      Run the agent with example config"
	@echo "  proto          Regenerate Go and Python bindings from control.proto"

# --- Protocol codegen -------------------------------------------------------

# Bindings are committed, so this is only needed after editing control.proto.
# Requires protoc-gen-go, protoc-gen-go-grpc (go install) and grpcio-tools (pip).
proto:
	cd $(REPO_ROOT) && $(PYTHON) -m grpc_tools.protoc -I proto \
		--go_out=. --go_opt=module=github.com/sanketn26/taskwire \
		--go-grpc_out=. --go-grpc_opt=module=github.com/sanketn26/taskwire \
		proto/taskwire/v1/control.proto
	cd $(REPO_ROOT) && $(PYTHON) -m grpc_tools.protoc -I proto \
		--python_out=python/taskwire/protocol/pb \
		--grpc_python_out=python/taskwire/protocol/pb \
		proto/taskwire/v1/control.proto
	cd $(REPO_ROOT)/python/taskwire/protocol/pb && \
		mv taskwire/v1/control_pb2.py taskwire/v1/control_pb2_grpc.py . && \
		rm -rf taskwire && \
		sed -i 's/^from taskwire\.v1 import/from taskwire.protocol.pb import/' \
			control_pb2_grpc.py

# --- Agent (Go) -------------------------------------------------------------

build-agent:
	mkdir -p $(REPO_ROOT)/agent/bin
	cd $(REPO_ROOT)/agent && go build \
		-ldflags "-X main.version=$(VERSION)" \
		-o bin/taskwire-agent \
		./cmd/taskwire-agent

# --- SDK (Python 3.11-3.13) -------------------------------------------------

build-sdk:
	cd $(REPO_ROOT) && $(PYTHON) -m pip install -e .

# --- Formatting / linting ----------------------------------------------------

format:
	cd $(REPO_ROOT)/agent && gofmt -w .
	cd $(REPO_ROOT) && $(PYTHON) -m ruff format python harness

lint:
	cd $(REPO_ROOT)/agent && go vet ./...
	cd $(REPO_ROOT) && $(PYTHON) -m ruff check python harness

# --- Tests -------------------------------------------------------------------

unit:
	cd $(REPO_ROOT)/agent && go test ./...
	cd $(REPO_ROOT) && $(PYTHON) -m pytest python/tests/unit -v

integration:
	cd $(REPO_ROOT) && $(PYTHON) -m pytest python/tests/integration -v -m integration

test: unit

bench:
	cd $(REPO_ROOT) && $(PYTHON) -m pytest python/tests/benchmark -v --tb=short

# --- Wheel packaging ----------------------------------------------------

# Stages the built agent binary at python/taskwire/bin/ (gitignored) so
# pyproject.toml's [tool.poetry] include picks it up. This yields a
# single-host-platform wheel; per-platform wheel tagging is a follow-up.
wheel: build-agent
	mkdir -p $(REPO_ROOT)/python/taskwire/bin
	cp $(REPO_ROOT)/agent/bin/taskwire-agent $(REPO_ROOT)/python/taskwire/bin/taskwire-agent
	cd $(REPO_ROOT) && $(PYTHON) -m build --wheel

smoke-wheel: wheel
	rm -rf $(REPO_ROOT)/.smoke-venv
	$(PYTHON) -m venv $(REPO_ROOT)/.smoke-venv
	$(REPO_ROOT)/.smoke-venv/bin/pip install --quiet $(REPO_ROOT)/dist/*.whl
	cd /tmp && $(REPO_ROOT)/.smoke-venv/bin/python -c "\
import subprocess, sys; \
import taskwire; \
print('taskwire.__version__ =', taskwire.__version__); \
agent = taskwire.find_agent_binary(); \
print('agent binary =', agent); \
out = subprocess.run([str(agent), 'version'], capture_output=True, text=True, check=True).stdout.strip(); \
assert out == taskwire.__version__, f'version mismatch: agent={out!r} python={taskwire.__version__!r}'; \
print('OK: wheel import + agent discovery + version parity, no source tree on path')"
	rm -rf $(REPO_ROOT)/.smoke-venv

# --- Cleanup ------------------------------------------------------------

clean:
	rm -rf $(REPO_ROOT)/agent/bin
	rm -rf $(REPO_ROOT)/python/taskwire/bin
	rm -rf $(REPO_ROOT)/dist $(REPO_ROOT)/build $(REPO_ROOT)/.smoke-venv
	find $(REPO_ROOT) -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true
	find $(REPO_ROOT) -name "*.pyc" -delete 2>/dev/null; true
	find $(REPO_ROOT) -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null; true
	find $(REPO_ROOT) -name "*.egg-info" -exec rm -rf {} + 2>/dev/null; true

# --- Dev helpers --------------------------------------------------------

agent-run: build-agent
	$(REPO_ROOT)/agent/bin/taskwire-agent --config $(REPO_ROOT)/taskwire.example.yaml
