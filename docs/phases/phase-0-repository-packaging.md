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

---

## Implementation Guide

> **Status:** Baseline is present in this repository. Treat this section as a
> residual checklist and orientation. If anything below is missing in your tree,
> implement it before Phase 1/2 work.

### What already exists (verify, do not re-invent)

| Path | Role |
|------|------|
| `pyproject.toml` | Single version source; wheel includes staged agent binary |
| `Makefile` | `format`, `lint`, `unit`, `integration`, `build-agent`, `wheel`, `smoke-wheel` |
| `agent/cmd/taskwire-agent/` | Agent entrypoint (stub server until Phase 2) |
| `python/taskwire/__init__.py` | `__version__`, public exports |
| `python/taskwire/agent_locate.py` | `TASKWIRE_AGENT_PATH` then `taskwire/bin/taskwire-agent` |
| `python/taskwire/_accel.py` | Optional accel; pure-Python fallback |
| `harness/agent_harness.py` | Process lifecycle + isolated dirs |
| `harness/timing.py` | `wait_until` deadline polling |
| `harness/chaos.py` | Seeded `ChaosTimeline` / `TASKWIRE_CHAOS_SEED` |
| `python/tests/unit/*` | Version, locate, accel fallback |
| `python/tests/integration/*` | Lifecycle, wheel install, version parity |

### Residual checklist

```bash
make format lint unit integration smoke-wheel
```

- [ ] `taskwire.__version__` matches `taskwire-agent version` (from `pyproject.toml`)
- [ ] `find_agent_binary()` prefers `TASKWIRE_AGENT_PATH`, else packaged path
- [ ] Missing binary raises `AgentNotFoundError` with actionable message
- [ ] Pure-Python path works when native accel is absent
- [ ] `AgentHarness` starts/stops stub agent; no orphan process/socket
- [ ] Wheel install works with source tree not on `PYTHONPATH`
- [ ] Pytest markers registered: `integration`, `chaos`, `cluster`, `kafka`, `resource`

### Minimal harness shape (reference)

```python
# harness/agent_harness.py — public surface you must preserve
class AgentHarness:
    def __init__(self, base_dir: Path | None = None): ...
    def start(self) -> None: ...
    def stop(self, *, grace_s: float = 5.0) -> None: ...
    def status(self) -> object: ...          # Phase 1+: StatusSnapshot
    def worker_pids(self) -> list[int]: ...  # Phase 3 fills this
    def kill(self) -> None: ...              # hard kill, keep storage
    def restart(self) -> None: ...
```

### Agent discovery (reference)

```python
# python/taskwire/agent_locate.py
def find_agent_binary() -> Path:
    env = os.environ.get("TASKWIRE_AGENT_PATH")
    if env:
        p = Path(env)
        if p.is_file() and os.access(p, os.X_OK):
            return p
        raise AgentNotFoundError(f"TASKWIRE_AGENT_PATH set but not executable: {env}")
    packaged = Path(__file__).resolve().parent / "bin" / "taskwire-agent"
    if packaged.is_file() and os.access(packaged, os.X_OK):
        return packaged
    raise AgentNotFoundError(
        "taskwire-agent not found; set TASKWIRE_AGENT_PATH or install a "
        "platform wheel that bundles the agent"
    )
```

### Done checklist / review request

```text
Please review Phase 0.
Commands: make format lint unit integration smoke-wheel
Gaps: <none | list>
```

**Pass criteria:** Exit gate above + residual checklist green.
