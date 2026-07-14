# Phase 7 — Hardening and Release

## Goal

Turn the validated feature set into supportable, observable, reproducible artifacts. Hardening is cumulative—this phase closes release-wide gaps but does not postpone feature-specific cleanup or tests.

## Release Scope

Declare the exact release profile before cutting artifacts:

- Core pre-alpha: Phases 0–4, single node, SQLite/filesystem, agent-relayed results.
- Cluster feature: included only if the Phase 5 exit gate is green.
- Kafka integration: included only if the Phase 6 exit gate is green and remains optional.
- PostgreSQL/S3: advertised only after their common conformance suites pass; configuration placeholders alone do not imply support.

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

Metrics labels must be bounded; task/owner IDs are never labels. Health endpoints distinguish liveness from readiness and fail readiness on unusable configured state/object storage.

## Security Gate

- Document the Unix socket and cloudpickle trusted-code boundaries prominently.
- Verify socket ownership/mode and state/object directory permissions at startup; refuse unsafe modes unless an explicit development override exists.
- Cluster authentication is required by default; rotate-key procedure and mixed-key behavior are documented/tested.
- Validate paths against traversal/symlink surprises and cap all frame, envelope, object, batch, and metadata sizes.
- Run fuzzers, dependency/vulnerability scans, secret scanning, static analysis, and a threat-model review covering local privilege, cluster impersonation, object tampering, replay, and denial of service.
- Publish supported-version and security-reporting policies.

## Performance Evidence

Benchmark from final installed artifacts, not source-tree shortcuts. Report hardware, OS, Python/Go versions, payload size, durability settings, worker count, run duration, warmup, and confidence ranges.

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

## Exit Gate

Phase 7 is complete when final artifacts—not developer builds—pass clean-install E2E and the release chaos run, service and upgrade paths are tested, security/observability requirements are met, documentation makes no stronger guarantee than the tests, and optional features are labeled according to their own gates.
