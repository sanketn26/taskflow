# Phase 7 — Hardening + Release

## Goal

Production-ready, publicly releasable library. Single `pip install <package>` on a clean machine. `taskwire-agent` binary installs as a system service. Benchmarks published. Documentation complete.

## Naming (resolved 2026-06-11)

The project was originally called *taskwire*, but `taskwire` on PyPI is OpenStack TaskFlow — an actively published library in the same domain, so both the distribution and the import package would have collided. The project is now **taskwire** (verified available on PyPI): distribution `taskwire`, `import taskwire`, binary `taskwire-agent`, socket under `/var/run/taskwire/`. Before first publish, re-verify the name is still free and register it on TestPyPI early.

## Testable Outcome

- `pip install <package>` on a clean Linux and macOS machine — no manual steps
- `taskwire-agent install` registers and starts a system service
- `taskwire-agent start/stop/status` work on both Linux (systemd) and macOS (launchd)
- `taskwire-agent status --json` reports queue depth, active leases, worker PIDs, dead-letter count, cluster members
- Full E2E test passes on clean machine with no prior config
- Benchmarks published against honest baselines (see Benchmarks section)
- Socket is 0660; systemd unit runs as a dedicated non-root user; gossip key documented as required for multi-host
- All existing Phase 1–6 tests still pass, on standard CPython 3.11–3.13 and free-threaded 3.13t, with and without the Rust extension

---

## Files

```
agent/internal/service/service.go            (new — ServiceManager interface + Detect())
agent/internal/service/systemd.go            (new — Linux implementation)
agent/internal/service/launchd.go            (new — macOS implementation)
agent/cmd/taskwire-agent/main.go             (add install/uninstall subcommands)

packaging/service/taskwire-agent.service     (systemd unit template)
packaging/service/io.taskwire.agent.plist    (launchd plist template)
packaging/scripts/build_platforms.sh         (cross-compile agent for all targets)
packaging/scripts/bundle_wheel.sh            (copy binaries into python/taskwire/_bin/)
packaging/docker/Dockerfile.agent            (containerised agent)

python/taskwire/_bin.py                      (new — locate bundled binary)
python/tests/benchmark/bench_taskwire.py     (new)
pyproject.toml                               (include _bin/**/* in wheel)
```

---

## Go

### `agent/internal/service/service.go`

Abstracts OS-level service management behind an interface. Concrete implementations in `systemd.go` and `launchd.go`. Templates are read from `packaging/service/` via `//go:embed` at compile time — templates are not duplicated inside the agent module.

#### `ServiceManager` interface

| Method | Signature | Description |
|--------|-----------|-------------|
| `Install` | `(configPath string) error` | Write service unit/plist from embedded template, enable service |
| `Uninstall` | `() error` | Disable and remove service unit/plist |
| `Start` | `() error` | Start the service via OS service manager |
| `Stop` | `() error` | Stop the service |
| `Status` | `() (string, error)` | Return human-readable status: "running", "stopped", "not installed" |

#### `Detect() ServiceManager`

Factory function. Returns the correct implementation based on OS:
- Linux: check `/run/systemd/private` exists → `SystemdManager`
- macOS: `runtime.GOOS == "darwin"` → `LaunchdManager`
- Other: return `UnsupportedManager` that returns a clear error

#### `SystemdManager` (Linux)

| Method | Responsibility |
|--------|----------------|
| `Install(configPath)` | Execute embedded `taskwire-agent.service` template with `{BinaryPath, ConfigPath}`. Write output to `/etc/systemd/system/taskwire-agent.service`. `systemctl daemon-reload`. `systemctl enable taskwire-agent`. |
| `Uninstall` | `systemctl disable taskwire-agent`. Remove unit file. `systemctl daemon-reload`. |
| `Start` | `systemctl start taskwire-agent` |
| `Stop` | `systemctl stop taskwire-agent` |
| `Status` | `systemctl is-active taskwire-agent` |

Templates are embedded using:
```go
//go:embed ../../../packaging/service/taskwire-agent.service
var systemdTemplate string

//go:embed ../../../packaging/service/io.taskwire.agent.plist
var launchdTemplate string
```

#### `LaunchdManager` (macOS)

| Method | Responsibility |
|--------|----------------|
| `Install(configPath)` | Execute embedded `io.taskwire.agent.plist` template. Write to `~/Library/LaunchAgents/io.taskwire.agent.plist`. `launchctl load -w <plist_path>`. |
| `Uninstall` | `launchctl unload -w plist`. Remove file. |
| `Start` | `launchctl start io.taskwire.agent` |
| `Stop` | `launchctl stop io.taskwire.agent` |
| `Status` | `launchctl list io.taskwire.agent` — parse stdout for PID |

---

### `agent/cmd/taskwire-agent/main.go` (additions)

Add subcommands via `flag` or `cobra`:

| Subcommand | Responsibility |
|------------|----------------|
| `start` | Default (no subcommand). Load config, start server, block on signal. |
| `install --config <path>` | `service.Detect().Install(configPath)`. Print "Service installed and started." |
| `uninstall` | `service.Detect().Uninstall()` |
| `status` | `service.Detect().Status()`, print result |
| `version` | Print binary version (injected at build time via `ldflags`) |

---

## Packaging

### `packaging/service/taskwire-agent.service`

Systemd unit template. Populated by `SystemdManager.Install`.

```
[Unit]
Description=Taskwire Agent
After=network.target

[Service]
ExecStart={{.BinaryPath}} --config {{.ConfigPath}}
Restart=always
RestartSec=5
# The socket executes arbitrary Python on behalf of connecting clients —
# never run this as root.
User=taskwire
Group=taskwire
RuntimeDirectory=taskwire
StateDirectory=taskwire
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/taskwire

[Install]
WantedBy=multi-user.target
```

`Install` creates the `taskwire` system user/group (`useradd --system`) if missing. Developers who installed the SDK join the `taskwire` group to reach the socket.

### `packaging/service/io.taskwire.agent.plist`

launchd plist template. Populated by `LaunchdManager.Install`.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>io.taskwire.agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>{{.BinaryPath}}</string>
    <string>--config</string>
    <string>{{.ConfigPath}}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
```

### `packaging/scripts/build_platforms.sh`

Cross-compiles `taskwire-agent` for all target platforms. Called by CI before building the Python wheel.

| Target | `GOOS` | `GOARCH` | Output |
|--------|--------|----------|--------|
| Linux x86-64 | `linux` | `amd64` | `dist/linux_amd64/taskwire-agent` |
| macOS Intel | `darwin` | `amd64` | `dist/darwin_amd64/taskwire-agent` |
| macOS Apple Silicon | `darwin` | `arm64` | `dist/darwin_arm64/taskwire-agent` |
| Windows x86-64 | `windows` | `amd64` | `dist/windows_amd64/taskwire-agent.exe` |

### `packaging/scripts/bundle_wheel.sh`

Copies compiled binaries from `packaging/dist/{platform}/` into `python/taskwire/_bin/{platform}/` so `poetry build` includes them in the wheel.

### `packaging/docker/Dockerfile.agent`

Minimal container image for the agent. Based on `gcr.io/distroless/static`. Copies the pre-built linux/amd64 binary. Exposes gossip port `7946`. Useful for users running the agent in containers rather than as a system service.

---

## Python

### `python/taskwire/_bin.py`

Locates the `taskwire-agent` binary bundled inside the wheel. Similar to how Playwright ships browser binaries.

| Function | Signature | Responsibility |
|----------|-----------|----------------|
| `binary_path` | `() -> str` | Return absolute path to bundled binary. Resolve `taskwire/_bin/{platform_tag()}/taskwire-agent` (`.exe` on Windows) relative to this file's location. Raise `RuntimeError` with a clear message if not found — indicates a broken or incomplete wheel. |
| `platform_tag` | `() -> str` | Return `"linux_amd64"`, `"darwin_arm64"`, `"darwin_amd64"`, or `"windows_amd64"` based on `sys.platform` and `platform.machine()`. Raise `RuntimeError` for unsupported platforms. |

The binary is not executed by the Python library at runtime — it is shipped for users who want to run the agent without installing Go. The SDK connects to a running agent; it never starts one.

---

### `pyproject.toml` (additions)

```toml
[tool.poetry.include]
- "taskwire/_bin/**/*"
```

Build process: `packaging/scripts/bundle_wheel.sh` runs first, populating `python/taskwire/_bin/`. Then `poetry build` includes those binaries in the wheel.

---

## Benchmarks

### `python/tests/benchmark/bench_taskwire.py`

**Baselines must be the honest ones.** Beating `ThreadPoolExecutor` at CPU-bound work on GIL CPython is a strawman — *anything* multi-process wins that. The baselines a skeptical reader will demand:

| Scenario | Baseline | What it proves |
|----------|----------|----------------|
| CPU-bound, 1000 tasks | `ProcessPoolExecutor` | taskwire's overhead vs the stdlib's same-machine multi-process answer. Target: within 10% on one node; the win is that the *same code* then scales to N nodes. |
| CPU-bound, 1000 tasks, 3 nodes | `ProcessPoolExecutor` (1 node — its ceiling) | the actual value proposition: horizontal scale with zero infra |
| Throughput + latency, small tasks | Celery + Redis (`solo` and `prefork`) | "Celery without the broker" needs numbers vs Celery *with* the broker: submit→result round-trip latency p50/p99, tasks/s sustained |
| I/O-bound, 200 × sleep(10ms) | `ThreadPoolExecutor` | honesty in the other direction: threads will *win* this on one machine. Publish it anyway and say so — credibility is the currency of a benchmarks page. |

Also benchmark **internally**: pure-Python vs Rust codec/result-server (justifies `native/` in the README), and `persistence: none` vs `wal` submit throughput (documents the durability tax).

```
CPU task: compute SHA-256 of a 1MB buffer 100 times
Measure: wall time for all futures to resolve; report p50/p99 per-task latency, not just totals
Environment: pinned in docs/benchmarks/ (machine type, Python version, GIL vs free-threaded)
```

Benchmarks run in CI and results committed to `docs/benchmarks/` on each release.

---

## Observability

A queue you cannot inspect is a queue you cannot trust in production. Minimum viable surface, all read-only:

| Surface | Detail |
|---------|--------|
| `taskwire-agent status --json` | Connects to the local socket, sends a STATUS frame (new type `0x0A`, local-socket only — never served on the cluster TCP listener). Returns: queue depth, active leases (task_id, age, attempts), worker PIDs + restart counts, dead-letter count, cluster members + their queue depths. Human-readable table without `--json`. |
| `taskwire-agent deadletter list / requeue <task_id>` | Inspect and retry dead-lettered tasks. The requeue path is the operator's poison-task recovery story. |
| Prometheus (optional) | `metrics.listen_addr` in config; when set, expose `/metrics`: `taskwire_queue_depth`, `taskwire_tasks_submitted_total`, `taskwire_tasks_completed_total`, `taskwire_lease_expiries_total`, `taskwire_deadletter_total`, `taskwire_worker_restarts_total`. Counters live in the queue/lease structs from the start — the endpoint just reads them. |
| Structured logs | `log/slog` JSON in the agent; task_id as a field everywhere a task is touched. The worker logs task start/end/duration at INFO. |

---

## Security Hardening (release gate)

The threat model in one sentence: **the agent executes arbitrary pickled Python from anyone who can reach its socket, and ships pickled callables between nodes.** Every item below follows from that.

| Item | Detail |
|------|--------|
| Non-root agent | systemd `User=taskwire`; launchd runs per-user. `Install` refuses to write a root-running unit. |
| Socket permissions | 0660 + `socket_group` (Phase 2) — verified by `test_socket_permissions` in the e2e suite |
| Cluster auth | gossip `encryption_key` + HMAC handshake on the cluster TCP listener (Phase 5); README's multi-host section makes the key a step 1, not a footnote |
| Honest docs | A SECURITY.md stating plainly: task payloads are code; the socket is an arbitrary-code-execution boundary; never expose the cluster port to untrusted networks. Users respect software that states its trust model; they abandon software that hides it. |
| Frame limits | `max_frame_size` enforced on every listener (Phase 1) — fuzz the codec with `go-fuzz`/`atheris` here |

---

## Wheel Building (Rust + Go in one package)

The wheel carries two native artifacts: the Go agent binary (`_bin/`) and the Rust extension (`_native`). Build matrix via `cibuildwheel` + `maturin`:

| Step | Tool |
|------|------|
| Cross-compile agent | `build_platforms.sh` (Go — trivial cross-compilation) |
| Build `_native` per platform/abi3 | `maturin` under `cibuildwheel` (abi3-py311 → one wheel per OS/arch covers CPython ≥ 3.11) |
| Bundle agent into wheel | `bundle_wheel.sh` before the wheel is finalised |
| sdist fallback | sdist must install and pass tests with *neither* native artifact — pure-Python codec/result-server/heartbeat, agent downloaded separately or built from source. CI has an explicit `TASKWIRE_PURE_PYTHON=1` job. |

Free-threaded (`cp313t`) wheels for `_native` ship only when PyO3's free-threaded support is stable for our usage; until then 3.13t users get the (perfectly correct there) pure-Python paths.

---

## Release Checklist

| Item | Description |
|------|-------------|
| **Name consistency check** | Project renamed taskwire → taskwire (old name is OpenStack TaskFlow on PyPI). Before publish: grep the whole repo, systemd/launchd templates, and socket default paths for any leftover `taskwire`; re-verify `taskwire` is still free on PyPI. |
| Semantic versioning | `0.1.0` for first public release |
| Go binary versioned | `ldflags -X main.version=$(git describe --tags)` |
| Python `__version__` | From `pyproject.toml` via `importlib.metadata.version(<package>)` |
| Supported-Python honesty | Classifiers: 3.11, 3.12, 3.13 (+3.13t experimental). `requires-python = ">=3.11"` — not `>=3.13`, which would exclude ~everyone. |
| Changelog | Update `CHANGELOG.md` with all Phase 1–7 features |
| PyPI release | Publish to TestPyPI first; install-test the actual artifacts on clean VMs |
| GitHub release | Tag `v0.1.0`, attach pre-built agent binaries as release assets |
| README | Value proposition, quick start (5 lines), *durability and trust-model paragraphs above the fold*, config reference, benchmark table with honest baselines |
| SECURITY.md | Trust model + disclosure contact |

---

## Tests

| Test | Asserts |
|------|---------|
| `test_install_service_linux` | (CI, Linux) `taskwire-agent install` writes unit file, `systemctl is-active` returns "active" |
| `test_install_service_macos` | (CI, macOS) `taskwire-agent install` writes plist, `launchctl list` shows pid |
| `test_binary_path_found` | `_bin.binary_path()` returns a path that exists and is executable |
| `test_fresh_machine_e2e` | Docker container with only `pip install <package>`. Run `taskwire-agent start`. Submit task. Verify result. |
| `test_socket_permissions` | Socket file mode is 0660 after agent start |
| `test_status_json` | `taskwire-agent status --json` returns parseable JSON with queue_depth, workers, leases keys |
| `bench_cpu_bound` | Single node: within 10% of ProcessPoolExecutor. 3 nodes: > 2× ProcessPoolExecutor's single-node ceiling. |

---

## Design Patterns Applied

| Pattern | Where | Why |
|---------|-------|-----|
| Strategy | `ServiceManager` interface | Systemd / launchd / Windows Service are interchangeable |
| Factory | `service.Detect()` | OS detection in one place; callers just call the interface |
| Template Method | `packaging/service/` templates | Structure is fixed; values injected at install time |
| Embedded Resources | `//go:embed` pointing into `packaging/service/` | Templates live in one place; binary embeds them at compile time |
