# Phase 9 — Examples, Use Cases, and Adoption Guide

## Goal

Ship executable documentation that teaches the supported architecture and proves the released artifacts work in realistic scenarios. Examples use registered task identity, agent-relayed results, SQLite/filesystem defaults, and the shipped configuration schema.

## Phase 0 Baseline

All examples use the repository's established root commands and artifact contract: build with `make wheel`, install the produced wheel into a clean environment, and let `find_agent_binary()` locate the bundled agent unless an example intentionally demonstrates `TASKWIRE_AGENT_PATH`. Examples must not copy binaries into ad hoc locations, compile on import, or add the checkout to `PYTHONPATH`.

Automated example tests reuse `AgentHarness`, `wait_until`, failure diagnostics, and `TASKWIRE_CHAOS_SEED`, and use the existing `integration`, `chaos`, `cluster`, `kafka`, and `resource` markers. Version output shown in documentation must be derived from the installed package rather than hard-coded.

## Deliverables

```text
examples/quickstart/
examples/batch/
examples/recovery/
examples/cancellation/
examples/clustering/               # only when Phase 5 ships
examples/distributed-storage/      # only when Phase 6 ships
examples/kafka-outbox/             # only when Phase 7 ships
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
  task_query_batch_size: 100
  read_timeout_ms: 30000
  write_timeout_ms: 30000
  object_transfer_timeout_ms: 60000
  object_chunk_bytes: 262144
  max_active_transfers: 4
  max_transfer_bytes: 1073741824
  write_queue_size: 256
  max_message_size_mb: 16
queue:
  max_attempts: 5
  lease_ttl_ms: 30000
  reaper_interval_ms: 1000
storage:
  state: {type: sqlite, dsn: "./data/state.db", sqlite_busy_timeout_ms: 5000, sqlite_synchronous: FULL}
  objects:
    default: local
    inline_threshold_bytes: 65536
    result_retention_seconds: 86400
    sweep_interval_ms: 60000
    sweep_batch_size: 100
    stores:
      local: {type: filesystem, root: "./data/objects"}
tasks:
  allow_inline_functions: false
workers:
  shutdown_grace_ms: 30000
  restart_backoff_min_ms: 250
  restart_backoff_max_ms: 30000
  restart_limit: 5
  restart_window_seconds: 60
  pools:
    - name: python-default
      runtime: python
      command: ["python3", "-m", "taskwire.worker.runner", "quickstart.tasks"]
      count: 2
      working_directory: "."
      environment: {}
      labels: {workload: general}
      resources: {max_memory_mb: 2048, max_cpu_percent: 80}
cluster:
  enabled: false
  allow_insecure: false
  node_name: ""
  bind_addr: "0.0.0.0:7946"
  advertise_addr: ""
  task_port: 7947
  seeds: []
  mdns: true
  encryption_key: ""
  auth_clock_skew_ms: 30000
  auth_timeout_ms: 5000
  replay_cache_size: 4096
  transfer_timeout_ms: 30000
  ownership_timeout_ms: 120000
  steal_batch_size: 10
routing: {rules: []}
integrations:
  kafka:
    enabled: false
    brokers: []
    topic: taskwire-results
    delivery_timeout_ms: 30000
    batch_size: 100
    max_in_flight: 10
    retry_backoff_ms: 1000
    shutdown_grace_ms: 10000
    preserve_owner_order: true
    max_event_bytes: 1048576
    published_retention_seconds: 604800
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

With SQLite/filesystem, submit and confirm the response, terminate the Runtime before acknowledging the result, restart with the persisted owner ID/task IDs, call `reattach`, and receive cursor-replayed results. Separately restart the agent with queued work and demonstrate recovery. Contrast the explicit memory backend, where agent restart may lose acknowledged work.

### Capability routing and clustering

Only when Phase 5 ships: configure authenticated nodes with distinct labels, route work, transfer a filesystem-backed object, and demonstrate convergence after a node restart. Never use `allow_insecure` in production examples and never imply exactly-once execution during partitions.

### Distributed storage backends

Only when Phase 6 ships: configure a shared PostgreSQL state store and S3 object store, run two agents against the same backend pair, and demonstrate that a task claimed by one agent is never also claimed by the other. Contrast this shared-backend topology with clustering's forwarding protocol above — either can be used alone, or together, but this example does not enable clustering. Never inline credentials in the example config; read them from the environment.

### Kafka outbox integration

Only when Phase 7 ships: enable `integrations.kafka`, consume versioned terminal events, deduplicate by event ID, and fetch an authorized result reference. Stop Kafka, complete a task through the Runtime, show outbox lag, restart Kafka, and observe later publication. Do not present Kafka as Runtime result delivery or require it for reattach.

### Operations

Cover config validation, status JSON, metrics, structured logs, graceful shutdown, backup/restore of state plus objects, retention, schema migration, key rotation, and cleanup. Backup instructions preserve consistency between task-state references and immutable objects.

## Adoption Guidance

Good fits for v0.1 include trusted Python services needing low-operations background work, single-node durable execution, registered task deployments, and workloads comfortable with at-least-once semantics. The architecture is language-neutral, but Node.js and Go examples must remain explicitly future-gated until Phase 10 or Phase 11 passes.

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

Phase 9 is complete when a new user can run the default quickstart without external infrastructure, every advertised recovery/failure claim is exercised by an automated example, all configuration snippets match the shared schema, optional examples track shipped feature gates, and no example teaches a superseded callback or queue-delivery architecture.

---

## Implementation Guide

> **Docs follow code.** Only document features whose phase exit gates passed.
> Every example installs the **built wheel**, not a `PYTHONPATH` checkout.

### Directory layout

```text
examples/quickstart/
  README.md
  taskwire.yaml
  tasks.py
  main.py
  test_smoke.py
examples/batch/
examples/recovery/
examples/cancellation/
examples/clustering/            # only if Phase 5 shipped
examples/distributed-storage/   # only if Phase 6 shipped
examples/kafka-outbox/          # only if Phase 7 shipped
docs/configuration.md
docs/operations.md
docs/security.md
docs/migrations.md
```

### Quickstart files (copy-adapt)

```python
# examples/quickstart/tasks.py
from taskwire import task

@task(name="examples.add", version="v1", invocation="python_args", idempotent=True)
def add(a: int, b: int) -> int:
    return a + b
```

```python
# examples/quickstart/main.py
from taskwire import Runtime
from tasks import add

with Runtime(config="taskwire.yaml") as runtime:
    print(runtime.submit(add, 20, 22).result(timeout=10))
```

```yaml
# examples/quickstart/taskwire.yaml — use full Phase 1 schema;
# pools.command must import tasks module, e.g.:
#   command: ["python3", "-m", "taskwire.worker.runner", "tasks"]
```

````markdown
# examples/quickstart/README.md
## Prerequisites
- Python 3.11+
- Built wheel: `make wheel` from repo root

## Run
```bash
python -m venv .venv && source .venv/bin/activate
pip install ../../../dist/taskwire-*.whl
taskwire-agent run --config taskwire.yaml &
python main.py   # → 42
```
````

### Example matrix

| Example | Teaches | Automated test |
|---------|---------|----------------|
| quickstart | submit/result | exit 0, prints 42 |
| batch | backpressure, ObjectRef, gather timeouts | N futures complete |
| recovery | Runtime reattach + agent restart | result after restart |
| cancellation | queued True / leased False | asserts return values |
| clustering | labels, auth (no allow_insecure in prod docs) | marker `cluster` |
| distributed-storage | shared PG/S3, no double-claim | marker `resource`/`integration` |
| kafka-outbox | terminal events, lag, resume | marker `kafka` |

### Doc scan (CI script)

```bash
# fail if retired terms appear in docs/examples
rg -n "callback_addr|result_delivery|direct worker RESULT|Bolt|WAL persistence" docs examples && exit 1 || true
```

### Adoption wording to include

**Good fits:** trusted Python services, single-node durable jobs, registered
`name@version`, at-least-once OK, defined production work.

**Poor fits:** untrusted multi-tenant code, hard real-time, exactly-once side
effects, cancel-running as hard requirement, exploration notebooks as the
deploy unit.

### Done checklist

- [ ] Quickstart runs with no Redis/Kafka/Postgres  
- [ ] Snippets validate with Python + Go config loaders  
- [ ] Recovery/failure claims have automated tests  
- [ ] Optional examples gated on feature flags  
- [ ] No retired architecture terms  

### Review request

```text
Please review Phase 9.
Walkthrough: I followed examples/quickstart README on a clean machine.
Commands: example smokes + doc link check
Gaps: ...
```
