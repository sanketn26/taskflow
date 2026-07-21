# Phase 8 — Hardening and Release

## Goal

Turn the validated feature set into supportable, observable, reproducible artifacts. Hardening is cumulative—this phase closes release-wide gaps but does not postpone feature-specific cleanup or tests.

## Phase 0 Baseline and Packaging Debt

Phase 0 established Poetry as the build backend, `pyproject.toml` as the release-version source, root `Makefile` targets, `TASKWIRE_AGENT_PATH` followed by `taskwire/bin/taskwire-agent` lookup, and a wheel that stages `agent/bin/taskwire-agent` into the Python package. Preserve those user-facing contracts.

The Phase 0 wheel is explicitly a single-host build and does not yet provide the final platform-specific wheel matrix/tagging required for release. This phase must make binary-bearing wheels carry correct non-`any` platform tags, build each artifact on its target platform, and test the exact output in a clean environment. It must also reconcile the development invocation `taskwire-agent --config PATH` with `taskwire-agent run --config PATH` by keeping the former as a documented compatibility alias or performing an explicit, tested migration. Extend the existing CI and Make targets rather than creating an unrelated release path.

## Release Scope

Declare the exact release profile before cutting artifacts:

- Core pre-alpha: Phases 0–4, single node, SQLite/filesystem, agent-relayed results.
- Worker/runtime support in v0.1 is Python only. The protocol and agent are
  language-neutral, but Node.js and Go support is advertised only after the
  Phase 10 or Phase 11 exit gate and corresponding package artifacts are green.
- Cluster feature: included only if the Phase 5 exit gate is green.
- Distributed storage (PostgreSQL/S3): included only if the Phase 6 exit gate is green and remains optional; configuration placeholders alone do not imply support.
- Kafka integration: included only if the Phase 7 exit gate is green and remains optional.
- Supported systems for v0.1 are Linux and macOS on the architectures named in the artifact matrix. Windows agent/runtime support is explicitly out of scope; pure Python protocol tests on Windows do not constitute platform support.

## Service Management

Provide systemd and launchd definitions plus foreground/container operation. The service runs as a dedicated unprivileged user, creates state/object/runtime directories with least privilege, sets conservative file-descriptor/process limits, and uses graceful SIGTERM shutdown. It must not run as root after initialization.

Commands:

```text
taskwire-agent run --config PATH
taskwire-agent validate-config --config PATH
taskwire-agent status --json --socket PATH
taskwire-agent version
taskwire-agent migrate --config PATH
```

`status` uses the local socket and has bounded timeouts. `migrate` supports dry-run/backup guidance and refuses unsafe downgrade. Service uninstall never deletes state or objects automatically.

## Packaging

- Build reproducible wheels for supported Python/platform combinations and a standalone agent artifact.
- Binary-bearing wheels have correct platform tags; no wheel containing `taskwire-agent` is published as `*-any.whl`.
- The Python package locates a bundled agent deterministically or reports a precise installation error; it never downloads binaries during import/install.
- Pure Python is supported. Any Rust extension is optional and parity-tested.
- Clean-venv smoke tests install the final wheel, import the SDK, validate config, locate/start the agent, execute a registered task, and shut down cleanly.
- Produce checksums, signatures/provenance, an SBOM, dependency/license inventory, and vulnerability scan results.
- Container images run as non-root, use a pinned minimal base, expose no cluster port unless enabled, and persist state/object directories through volumes.

## Observability

Structured logs include timestamp, level, component, node ID, task ID, owner ID hash, lease/transfer ID where relevant, stable error code, and retryability. Never log payloads, results, encryption keys, owner bearer values, DSNs with credentials, or full tracebacks containing arguments by default.

Metrics include:

- task counts/gauges by state, submission/claim/completion latency, attempts, stale leases, dead letters;
- result replay lag, unacknowledged result count, retention expiry;
- object bytes/latency/checksum failures/partial cleanup;
- worker count, restarts, circuit-breaker state, heartbeat failures;
- IPC connections, malformed frames, rejected/timeout submissions;
- cluster membership, transfer state/age, auth failures, forwarded completions;
- Kafka outbox pending age/count, retries, permanent errors, delivery latency.

Metrics labels must be bounded; task/owner IDs are never labels. Health endpoints distinguish liveness from readiness and fail readiness on unusable configured state/object storage. The Flower-class admin console, versioned `/admin/api/v1`, embedded UI, and full ops HTTP path contract are Phase 12; this phase still requires probe/metrics behavior sufficient for release when those surfaces are enabled, and must not document the console as shipped until Phase 12’s exit gate is green.

## Security Gate

- Document the Unix socket and cloudpickle trusted-code boundaries prominently.
- Verify socket ownership/mode and state/object directory permissions at startup; refuse unsafe modes unless an explicit development override exists.
- Cluster authentication is required by default; rotate-key procedure and mixed-key behavior are documented/tested.
- Validate paths against traversal/symlink surprises and cap all frame, envelope, object, batch, and metadata sizes.
- Run fuzzers, dependency/vulnerability scans, secret scanning, static analysis, and a threat-model review covering local privilege, cluster impersonation, object tampering, replay, and denial of service.
- Publish supported-version and security-reporting policies.

## Performance Evidence

Benchmark from final installed artifacts, not source-tree shortcuts. Report hardware, OS, agent Go version, worker runtime/version, payload size, durability settings, worker-pool count, run duration, warmup, and confidence ranges.

Measure submit-to-result p50/p95/p99, sustained tasks/s, SQLite contention, filesystem object thresholds, worker scaling, reconnect replay, cluster forwarding, and Kafka outbox lag. Compare fairly with Celery plus Redis for representative small and CPU-bound tasks. Performance targets are release criteria only after baselines are recorded; no unmeasured marketing claims.

## Reliability Gate

- Unit, integration, race, fuzz smoke, and clean-wheel suites pass on every supported platform/version.
- The exact release commit passes the seeded nightly chaos catalog without unexplained retry-to-green.
- Resource chaos covers ENOSPC, FD exhaustion, slow/corrupt storage, worker memory limits, and shutdown under load.
- Upgrade/reopen tests cover the previous supported schema/artifact version.
- Soak tests demonstrate bounded memory, connections, result records, outbox rows, temporary objects, and goroutines/threads.

## Release Checklist

1. Freeze protocol/config/event schemas and migration notes.
2. Run all applicable phase exit gates from a clean checkout.
3. Build artifacts in CI and run install/E2E tests against those exact artifacts.
4. Generate SBOM, provenance, signatures, checksums, and scan reports.
5. Verify examples and configuration reference against the shipped schema.
6. Publish limitations: at-least-once execution, cancellation boundary, retention, memory-backend loss, pure-Python heartbeat limit, and optional feature status.
7. Tag only the tested commit and rehearse rollback/yank procedures.
8. Verify `taskwire.__version__`, `taskwire-agent version`, wheel metadata, checksums, and provenance all name the same release version from `pyproject.toml`.

## Exit Gate

Phase 8 is complete when final artifacts—not developer builds—pass clean-install E2E and the release chaos run, service and upgrade paths are tested, security/observability requirements are met, documentation makes no stronger guarantee than the tests, and optional features are labeled according to their own gates.

---

## Implementation Guide

> **Do this after** the feature set you intend to ship has its own exit gates green
> (minimum Phases 0–4). Optional cluster/storage/Kafka only if 5–7 passed.

### Release profile file (create)

```text
docs/release/v0.1-profile.md
```

```markdown
# v0.1 release profile
- Core: Phases 0–4 (single node, SQLite/filesystem, Python only)
- Cluster: yes|no (Phase 5)
- Postgres/S3: yes|no (Phase 6)
- Kafka: yes|no (Phase 7)
- Platforms: linux/amd64, darwin/arm64, ...
- Windows agent: unsupported
```

### CLI surface to implement

```go
// agent/cmd/taskwire-agent/main.go
// subcommands:
//   version
//   run --config PATH          // canonical
//   --config PATH              // alias for run during transition; test both
//   validate-config --config PATH
//   status --json --socket PATH
//   migrate --config PATH [--dry-run]
```

```bash
taskwire-agent validate-config --config taskwire.yaml
taskwire-agent run --config taskwire.yaml
taskwire-agent status --json --socket ./run/agent.sock
```

### Packaging work

```text
packaging/service/taskwire-agent.service   # systemd
packaging/service/com.taskwire.agent.plist # launchd
packaging/docker/Dockerfile                # non-root, volumes for state/objects
```

Wheel rules:

```bash
# Binary-bearing wheel must NOT be py3-none-any
# Build on each target OS; tag e.g. manylinux / macosx
make wheel
python -m wheel tags dist/*.whl   # verify platform tag
make smoke-wheel                  # clean venv E2E registered task
```

### Observability minimum

```go
// structured log fields (no payloads / raw owner_id / DSN secrets):
// ts, level, component, node_id, task_id, owner_id_hash, lease_id, code, retryable

// Prometheus metrics on metrics.listen_addr (if set):
// taskwire_tasks{state=}
// taskwire_submit_latency_seconds
// taskwire_lease_stale_total
// taskwire_workers{pool=}
// taskwire_ipc_connections
```

Health (Phase 12 expands admin UI; Phase 8 still needs usable probes if HTTP enabled):

```text
GET /livez   → process up
GET /readyz  → stores usable + socket listening
```

### Security checklist (execute)

- [ ] Socket mode 0660 + ownership check at startup  
- [ ] State/object dirs permissions verified  
- [ ] Path traversal tests on filesystem store  
- [ ] Frame size caps fuzzed  
- [ ] `govulncheck`, dependency audit, secret scan in CI  
- [ ] Threat model note in `docs/security.md` (Phase 9 may host final docs)

### Performance evidence template

```text
docs/release/bench-YYYYMMDD.md
Hardware / OS / Go / Python:
Payload sizes:
Workers:
submit→result p50/p95/p99:
tasks/s sustained:
Comparison notes vs Celery+Redis (same hardware):
```

### Pre-tag checklist

```bash
# 1. freeze schemas
# 2. all applicable phase exit gates
make format lint unit integration smoke-wheel
# 3. chaos nightly seed recorded green
# 4. SBOM + checksums + provenance
# 5. examples match shipped schema (Phase 9)
# 6. version triple match: pyproject, agent -X version, wheel metadata
git tag v0.1.0 <tested-commit>
```

### Done checklist

- [ ] Release profile committed and honest about optional features  
- [ ] Platform-tagged wheels; no agent-in-`any` wheel  
- [ ] Service units + non-root container  
- [ ] Clean-install E2E from CI artifacts  
- [ ] Limitations published (at-least-once, cancel boundary, memory backend, GIL heartbeat)  

### Review request

```text
Please review Phase 8 (release).
Profile: <path>
Artifacts: <CI URL or dist/>
Commands: full gate list + chaos seed
Gaps: ...
```
