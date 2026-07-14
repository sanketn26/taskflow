# Phase 0 — Repository, Packaging, and Test Spine

## Goal

Create a buildable repository and artifact path before protocol or runtime work begins. This phase proves that Python, Go, optional Rust acceleration, packaged configuration, and test tooling can be developed and shipped together without relying on source-tree imports.

## Repository Contract

```text
agent/
  cmd/taskwire-agent/
  internal/
  pkg/
python/
  taskwire/
  tests/{unit,integration}/
native/                       # optional accelerator only
harness/
docs/phases/
taskwire.example.yaml
Makefile
pyproject.toml
go.mod
```

The Python distribution name and import package are `taskwire`; the Go executable is `taskwire-agent`. One documented build frontend owns wheel creation. If the wheel bundles the Go binary or Rust extension, the build pipeline must declare exactly how platform artifacts enter the wheel. Editable installs may use developer-built binaries, but release tests never do.

## Build and Packaging Rules

- Support CPython 3.11–3.13 initially; pure Python must work on every supported interpreter.
- Rust is optional acceleration. Import failure selects the pure-Python implementation without changing public behavior.
- The agent binary is found through a deterministic packaged path or explicit configuration. The package never downloads or compiles a binary during import.
- Builds run from the repository root and never depend on the developer's current directory.
- Source distributions either build a working platform artifact through documented prerequisites or fail with a precise unsupported-build message.
- Version information comes from one release source and is visible through both `taskwire.__version__` and `taskwire-agent version`.

## Developer Commands

The root build interface must provide equivalent commands for:

```text
make format
make lint
make unit
make integration
make build-agent
make build-sdk
make wheel
make smoke-wheel
```

Commands may delegate to language-specific tools but must be non-interactive and suitable for CI. Generated artifacts go to ignored build directories, never source folders.

## Testing Foundation

Create pytest markers for `integration`, `chaos`, `cluster`, `kafka`, and `resource`. Establish deterministic temporary-directory and free-port helpers. All waits poll observable state with a deadline; fixed sleeps are not readiness checks.

`AgentHarness` owns one agent process, config, socket, logs, state/object directories, lifecycle, raw status calls, and worker PID discovery. It must:

- allocate isolated paths and ports;
- start a built agent and poll until its socket responds;
- terminate with SIGTERM, then bounded SIGKILL fallback;
- capture stdout/stderr and attach them on failure;
- assert no orphan workers or owned socket remain;
- support kill/restart without deleting storage.

Create a seeded chaos timeline (`TASKWIRE_CHAOS_SEED`) that records action, target, and monotonic timestamp. Later phases add capabilities to this harness; Phase 0 only proves lifecycle and diagnostics with a stub agent.

## CI Foundation

- Formatting/lint and Python/Go unit jobs run on every push.
- Matrix smoke tests cover supported Python versions and target operating systems.
- Integration jobs install artifacts into clean environments.
- Cache keys include lockfiles/toolchain versions; caches never provide undeclared build inputs.
- CI uploads logs and built artifacts on failure and applies timeouts to every job.

## Required Tests

- Python imports from an editable install and from a built wheel with the source directory unavailable.
- Go agent builds and reports the same version as Python.
- Absence of the optional native module exercises the pure-Python fallback.
- Agent lookup succeeds for a correctly bundled/configured binary and produces an actionable error otherwise.
- `AgentHarness` starts/stops a stub agent without leaked process, FD, socket, or temporary path.
- Commands work from the repository root in a clean checkout.

## Implementation Order

1. Establish source roots, version source, formatting, linting, and unit-test commands.
2. Build the minimal Go agent and Python package.
3. Prove wheel construction and deterministic agent discovery.
4. Add `AgentHarness`, markers, deadline polling, and artifact diagnostics.
5. Add the clean-install CI matrix.

## Exit Gate

Phase 0 is complete when a clean virtual environment can install the built wheel, import `taskwire`, locate the matching built agent or report its absence clearly, run the pure-Python fallback, and execute the harness lifecycle test without reading the source checkout.
