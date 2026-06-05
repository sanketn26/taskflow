# Phase 7 — Hardening + Release

## Goal

Production-ready, publicly releasable library. Single `pip install taskflow` on a clean machine. `taskflow-agent` binary installs as a system service. Benchmarks published. Documentation complete.

## Testable Outcome

- `pip install taskflow` on a clean Linux and macOS machine — no manual steps
- `taskflow-agent install` registers and starts a system service
- `taskflow-agent start/stop/status` work on both Linux (systemd) and macOS (launchd)
- Full E2E test passes on clean machine with no prior config
- Benchmark: taskflow `map` over 1000 tasks is ≥ 2× faster than `ThreadPoolExecutor.map` for CPU-bound work (demonstrates free-threaded + multi-process benefit)
- All existing Phase 1–6 tests still pass

---

## Files

```
agent/internal/service/service.go            (new — ServiceManager interface + Detect())
agent/internal/service/systemd.go            (new — Linux implementation)
agent/internal/service/launchd.go            (new — macOS implementation)
agent/cmd/taskflow-agent/main.go             (add install/uninstall subcommands)

packaging/service/taskflow-agent.service     (systemd unit template)
packaging/service/io.taskflow.agent.plist    (launchd plist template)
packaging/scripts/build_platforms.sh         (cross-compile agent for all targets)
packaging/scripts/bundle_wheel.sh            (copy binaries into python/taskflow/_bin/)
packaging/docker/Dockerfile.agent            (containerised agent)

python/taskflow/_bin.py                      (new — locate bundled binary)
python/tests/benchmark/bench_taskflow.py     (new)
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
| `Install(configPath)` | Execute embedded `taskflow-agent.service` template with `{BinaryPath, ConfigPath}`. Write output to `/etc/systemd/system/taskflow-agent.service`. `systemctl daemon-reload`. `systemctl enable taskflow-agent`. |
| `Uninstall` | `systemctl disable taskflow-agent`. Remove unit file. `systemctl daemon-reload`. |
| `Start` | `systemctl start taskflow-agent` |
| `Stop` | `systemctl stop taskflow-agent` |
| `Status` | `systemctl is-active taskflow-agent` |

Templates are embedded using:
```go
//go:embed ../../../packaging/service/taskflow-agent.service
var systemdTemplate string

//go:embed ../../../packaging/service/io.taskflow.agent.plist
var launchdTemplate string
```

#### `LaunchdManager` (macOS)

| Method | Responsibility |
|--------|----------------|
| `Install(configPath)` | Execute embedded `io.taskflow.agent.plist` template. Write to `~/Library/LaunchAgents/io.taskflow.agent.plist`. `launchctl load -w <plist_path>`. |
| `Uninstall` | `launchctl unload -w plist`. Remove file. |
| `Start` | `launchctl start io.taskflow.agent` |
| `Stop` | `launchctl stop io.taskflow.agent` |
| `Status` | `launchctl list io.taskflow.agent` — parse stdout for PID |

---

### `agent/cmd/taskflow-agent/main.go` (additions)

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

### `packaging/service/taskflow-agent.service`

Systemd unit template. Populated by `SystemdManager.Install`.

```
[Unit]
Description=Taskflow Agent
After=network.target

[Service]
ExecStart={{.BinaryPath}} --config {{.ConfigPath}}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### `packaging/service/io.taskflow.agent.plist`

launchd plist template. Populated by `LaunchdManager.Install`.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>io.taskflow.agent</string>
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

Cross-compiles `taskflow-agent` for all target platforms. Called by CI before building the Python wheel.

| Target | `GOOS` | `GOARCH` | Output |
|--------|--------|----------|--------|
| Linux x86-64 | `linux` | `amd64` | `dist/linux_amd64/taskflow-agent` |
| macOS Intel | `darwin` | `amd64` | `dist/darwin_amd64/taskflow-agent` |
| macOS Apple Silicon | `darwin` | `arm64` | `dist/darwin_arm64/taskflow-agent` |
| Windows x86-64 | `windows` | `amd64` | `dist/windows_amd64/taskflow-agent.exe` |

### `packaging/scripts/bundle_wheel.sh`

Copies compiled binaries from `packaging/dist/{platform}/` into `python/taskflow/_bin/{platform}/` so `poetry build` includes them in the wheel.

### `packaging/docker/Dockerfile.agent`

Minimal container image for the agent. Based on `gcr.io/distroless/static`. Copies the pre-built linux/amd64 binary. Exposes gossip port `7946`. Useful for users running the agent in containers rather than as a system service.

---

## Python

### `python/taskflow/_bin.py`

Locates the `taskflow-agent` binary bundled inside the wheel. Similar to how Playwright ships browser binaries.

| Function | Signature | Responsibility |
|----------|-----------|----------------|
| `binary_path` | `() -> str` | Return absolute path to bundled binary. Resolve `taskflow/_bin/{platform_tag()}/taskflow-agent` (`.exe` on Windows) relative to this file's location. Raise `RuntimeError` with a clear message if not found — indicates a broken or incomplete wheel. |
| `platform_tag` | `() -> str` | Return `"linux_amd64"`, `"darwin_arm64"`, `"darwin_amd64"`, or `"windows_amd64"` based on `sys.platform` and `platform.machine()`. Raise `RuntimeError` for unsupported platforms. |

The binary is not executed by the Python library at runtime — it is shipped for users who want to run the agent without installing Go. The SDK connects to a running agent; it never starts one.

---

### `pyproject.toml` (additions)

```toml
[tool.poetry.include]
- "taskflow/_bin/**/*"
```

Build process: `packaging/scripts/bundle_wheel.sh` runs first, populating `python/taskflow/_bin/`. Then `poetry build` includes those binaries in the wheel.

---

## Benchmarks

### `python/tests/benchmark/bench_taskflow.py`

Measures taskflow against `concurrent.futures.ThreadPoolExecutor` and `joblib.Parallel`.

**CPU-bound benchmark** (benefits from Python 3.13 free-threaded + multi-process workers):

```
Task: compute SHA-256 of a 1MB buffer 100 times
Items: 100 tasks
Measure: wall time for all futures to resolve
```

**I/O-bound benchmark** (simulates network latency):

```
Task: sleep(0.01)
Items: 200 tasks
Measure: wall time (should be ~0.01s with enough workers)
```

**Target numbers** (to publish in README):

| Scenario | ThreadPoolExecutor | joblib | taskflow |
|----------|--------------------|--------|----------|
| CPU-bound 100 tasks | baseline | ~1× | ≥ 2× faster |
| I/O-bound 200 tasks | baseline | ~1× | ≥ 1.5× faster |

Benchmarks run in CI and results committed to `docs/benchmarks/` on each release.

---

## Release Checklist

| Item | Description |
|------|-------------|
| Semantic versioning | `0.1.0` for first public release |
| Go binary versioned | `ldflags -X main.version=$(git describe --tags)` |
| Python `__version__` | From `pyproject.toml` via `importlib.metadata.version("taskflow")` |
| Changelog | Update `CHANGELOG.md` with all Phase 1–7 features |
| PyPI release | `poetry publish` to PyPI; test on TestPyPI first |
| GitHub release | Tag `v0.1.0`, attach pre-built agent binaries as release assets |
| README | Value proposition, quick start (5 lines), config reference, benchmark table |

---

## Tests

| Test | Asserts |
|------|---------|
| `test_install_service_linux` | (CI, Linux) `taskflow-agent install` writes unit file, `systemctl is-active` returns "active" |
| `test_install_service_macos` | (CI, macOS) `taskflow-agent install` writes plist, `launchctl list` shows pid |
| `test_binary_path_found` | `_bin.binary_path()` returns a path that exists and is executable |
| `test_fresh_machine_e2e` | Docker container with only `pip install taskflow`. Run `taskflow-agent start`. Submit task. Verify result. |
| `bench_cpu_bound` | taskflow ≥ 2× faster than ThreadPoolExecutor |

---

## Design Patterns Applied

| Pattern | Where | Why |
|---------|-------|-----|
| Strategy | `ServiceManager` interface | Systemd / launchd / Windows Service are interchangeable |
| Factory | `service.Detect()` | OS detection in one place; callers just call the interface |
| Template Method | `packaging/service/` templates | Structure is fixed; values injected at install time |
| Embedded Resources | `//go:embed` pointing into `packaging/service/` | Templates live in one place; binary embeds them at compile time |
