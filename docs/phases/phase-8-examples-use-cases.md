# Phase 8 — Examples, Use Cases, and Adoption Guide

## Goal

Ship executable documentation that teaches the supported architecture and proves the released artifacts work in realistic scenarios. Examples use registered task identity, agent-relayed results, SQLite/filesystem defaults, and the shipped configuration schema.

## Deliverables

```text
examples/quickstart/
examples/batch/
examples/recovery/
examples/cancellation/
examples/clustering/       # only when Phase 5 ships
examples/kafka-outbox/     # only when Phase 6 ships
docs/configuration.md
docs/operations.md
docs/security.md
docs/migrations.md
```

Every example has pinned prerequisites, a minimal config, setup/run/cleanup commands, expected output, failure behavior, and an automated smoke test. Examples install the built wheel and agent artifact rather than importing the source checkout.

## Quick Start

The default quick start requires no external service:

```yaml
socket: "./run/agent.sock"
ipc:
  submit_ack_timeout_ms: 5000
  reconnect_backoff_ms: 250
  result_batch_size: 100
queue:
  max_attempts: 5
  max_frame_size_mb: 16
  lease_ttl_ms: 30000
storage:
  state: {type: sqlite, dsn: "./data/state.db"}
  objects:
    default: local
    inline_threshold_bytes: 65536
    result_retention_seconds: 86400
    stores:
      local: {type: filesystem, root: "./data/objects"}
tasks:
  allow_inline_functions: false
workers:
  count: 2
  shutdown_grace_ms: 30000
  labels: {workload: general}
  resources: {max_memory_mb: 2048, max_cpu_percent: 80}
cluster: {enabled: false, allow_insecure: false, node_name: "", bind_addr: "0.0.0.0:7946", advertise_addr: "", task_port: 7947, seeds: [], mdns: true, encryption_key: ""}
routing: {rules: []}
integrations:
  kafka: {enabled: false, brokers: [], topic: taskwire-results, delivery_timeout_ms: 30000}
metrics: {listen_addr: ""}
```

```python
from taskwire import Runtime, task

@task(name="examples.add", version="v1", idempotent=True)
def add(a: int, b: int) -> int:
    return a + b

with Runtime(config="taskwire.yaml") as runtime:
    future = runtime.submit(add, 20, 22)
    print(future.result(timeout=10))
```

The guide explains that the worker imports the module containing `examples.add`; the function itself is not sent over the wire. Expected output is `42`.

## Required Use Cases

### Batch transformation

Submit bounded batches, apply backpressure, use idempotent output keys, gather results with per-future timeouts, and explain possible re-execution after lease expiry. Demonstrate an argument/result crossing the inline threshold and show that the SDK resolves `ObjectRef` transparently.

### Errors, timeouts, and dead letters

Show a structured task exception, result deserialization error, `Future.result(timeout)` without cancellation, worker crash/retry, and max-attempt dead letter. Make clear that timeout does not imply the task stopped.

### Best-effort cancellation

Demonstrate successful cancellation while queued and `False`/too-late after leasing. State that v0.1 does not cancel running or remotely owned work.

### Agent and Runtime recovery

With SQLite/filesystem, submit and ACK work, terminate the Runtime before result ACK, restart with the persisted owner ID/task IDs, call `reattach`, and receive cursor-replayed results. Separately restart the agent with queued work and demonstrate recovery. Contrast the explicit memory backend, where agent restart may lose acknowledged work.

### Capability routing and clustering

Only when Phase 5 ships: configure authenticated nodes with distinct labels, route work, transfer a filesystem-backed object, and demonstrate convergence after a node restart. Never use `allow_insecure` in production examples and never imply exactly-once execution during partitions.

### Kafka outbox integration

Only when Phase 6 ships: enable `integrations.kafka`, consume versioned terminal events, deduplicate by event ID, and fetch an authorized result reference. Stop Kafka, complete a task through the Runtime, show outbox lag, restart Kafka, and observe later publication. Do not present Kafka as Runtime result delivery or require it for reattach.

### Operations

Cover config validation, status JSON, metrics, structured logs, graceful shutdown, backup/restore of state plus objects, retention, schema migration, key rotation, and cleanup. Backup instructions preserve consistency between task-state references and immutable objects.

## Adoption Guidance

Good fits include trusted Python services needing low-operations background work, single-node durable execution, registered task deployments, and workloads comfortable with at-least-once semantics.

Poor fits for v0.1 include untrusted multi-tenant code, hard real-time scheduling, exactly-once side effects, cancellation of running/distributed tasks, environments unable to align task deployments, and globally shared scheduling that requires an unimplemented backend/feature.

The comparison guide separates verified facts from benchmarks and explains when an established broker-based system is the better choice.

## Documentation Tests

- Extract and run all Python/YAML snippets where practical.
- Validate every YAML example with both Python and Go loaders.
- Run quickstart, errors, cancellation, and recovery from final installed artifacts in CI.
- Gate cluster and Kafka examples behind their feature markers and execute them in the corresponding CI tiers.
- Scan docs for retired terms and fields: `callback_addr`, `result_delivery`, direct worker RESULT, Bolt/WAL persistence, and Kafka-based Future resolution.
- Check internal links and ensure commands use repository paths that exist.

## Exit Gate

Phase 8 is complete when a new user can run the default quickstart without external infrastructure, every advertised recovery/failure claim is exercised by an automated example, all configuration snippets match the shared schema, optional examples track shipped feature gates, and no example teaches a superseded callback or queue-delivery architecture.
