# Phase 8 — Examples, Use Cases, and Adoption Guide

## Goal

Turn the implemented framework into something a developer can evaluate and use without reading the architecture documents. Deliver copy-pasteable examples, runnable sample projects, operational recipes, and a troubleshooting guide for the workflows Taskwire actually supports.

Phase 8 is documentation and example code, not a place to introduce new runtime behavior. If an example requires an unsupported feature, either move that feature into an earlier implementation phase or label the example as a future design.

## Testable Outcome

- A new user can install Taskwire, start a local agent, submit a task, and receive a result by following one page.
- Every documented command and Python example runs in CI from a clean environment.
- Examples cover success, task exceptions, timeouts, cancellation, worker failure, SQLite/filesystem recovery, clustering, and Kafka integration.
- Each use case states its delivery guarantee, infrastructure requirements, and failure limitations.
- The README links to a five-minute quick start and the complete examples guide.
- Example configuration is derived from `taskwire.example.yaml` and validated by both config parsers.
- Unsupported claims such as exactly-once execution or cancellation of running/remote tasks never appear in examples.

---

## Deliverables

```text
docs/
├── quickstart.md
├── use-cases.md
├── operations.md
└── troubleshooting.md

examples/
├── 01_local_task/
│   ├── README.md
│   ├── tasks.py
│   └── taskwire.yaml
├── 02_batch_processing/
├── 03_durable_queue/
├── 04_error_handling/
├── 05_multi_node_cluster/
├── 06_kafka_results/
└── 07_observability/

python/tests/examples/
└── test_examples.py
```

Each example directory is self-contained and includes prerequisites, commands, expected output, cleanup instructions, and a short explanation of its guarantee boundary.

---

## Quick Start — Local Direct Mode

This is the primary five-minute path and depends only on the Phase 4 single-node MVP.

### 1. Install

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install taskwire
```

The published wheel should contain the platform agent binary. Source or unsupported-platform installs must display the separate agent installation instructions instead of silently downloading an executable.

### 2. Configure

Create `taskwire.yaml`:

```yaml
cluster:
  enabled: false

socket: "/tmp/taskwire-example.sock"
socket_group: ""

queue:
  max_attempts: 5
  max_frame_size_mb: 16
  lease_ttl_ms: 30000

storage:
  state:
    type: "sqlite"
    sqlite:
      path: "/tmp/taskwire-example-state.db"
  objects:
    default: "local"
    inline_threshold_bytes: 65536
    result_retention_seconds: 86400
    stores:
      local:
        type: "filesystem"
        path: "/tmp/taskwire-example-objects"

ipc:
  submit_ack_timeout_ms: 5000

workers:
  count: 2

result_delivery:
  mode: "agent"
```

The real example should be generated from or checked against the canonical configuration schema so it cannot drift as fields are added.

### 3. Start the agent

```bash
taskwire-agent start --config taskwire.yaml
```

The quick start must show how to run in the foreground for learning and how to stop the process cleanly. Service installation belongs in the operations guide.

### 4. Define and submit a task

Create `tasks.py`:

```python
from taskwire import Runtime, task


@task(label="general", idempotent=True)
def add(left: int, right: int) -> int:
    return left + right


with Runtime(config="taskwire.yaml") as runtime:
    future = runtime.submit(add, 20, 22)
    print(future.result(timeout=10))
```

Expected output:

```text
42
```

Explain what happened:

```text
application ── SUBMIT ──► local agent ── leased TASK ──► worker
application ◄── RESULT ─ local agent ◄── COMPLETE(ref) ─ worker
application ── result ACK ──► local agent
```

The task is acknowledged after its input reference and task state satisfy the configured durability contract. The worker stores the result through its agent and completes with a fenced result reference. The agent retains result metadata for reconnect replay until the Runtime acknowledges it or retention expires.

---

## Use Case 1 — Batch Data Transformation

Use `Runtime.map()` when the same independent operation applies to many inputs:

```python
from taskwire import Runtime, task


@task(label="cpu", idempotent=True)
def normalise(value: str) -> str:
    return value.strip().lower()


with Runtime(config="taskwire.yaml") as runtime:
    futures = runtime.map(normalise, [" Alpha ", "BETA", " gamma"])
    results = [future.result(timeout=10) for future in futures]

print(results)
```

This is suitable for independent image transforms, document conversion, report generation, and data cleanup. It is not a workflow/DAG engine: dependency graphs, chords, scheduled jobs, and transactional pipelines are outside v0.1 unless implemented in a later phase.

Operational notes:

- Mark a task idempotent only if repeating it is safe.
- Keep each submission below `queue.max_frame_size_mb`.
- Pass object-store references rather than embedding large files in task payloads.
- Apply application-level concurrency limits when submitting very large batches; the v0.1 API does not promise an unbounded streaming map.

---

## Use Case 2 — Error Handling and Timeouts

```python
from taskwire import Runtime, TaskExecutionError, task


@task
def divide(left: float, right: float) -> float:
    return left / right


with Runtime(config="taskwire.yaml") as runtime:
    future = runtime.submit(divide, 10, 0)
    try:
        future.result(timeout=10)
    except TaskExecutionError as error:
        print(f"task failed: {error}")
    except TimeoutError:
        print("the task may still be running")
```

A caller-side timeout does not cancel work. It only stops waiting. The guide must show explicit cancellation separately and warn users not to assume the task stopped.

Document these terminal outcomes:

| Outcome | Meaning |
|---|---|
| Result | Worker executed and the receiver accepted the result |
| `TaskExecutionError` | User task raised or its result could not be serialized |
| `DeliveryFailedError` | Task ran, but configured result delivery exhausted its retries |
| `TimeoutError` | Caller stopped waiting; execution state is unknown |
| Cancelled Future | Agent confirmed removal before the task was leased |
| Submission-outcome-unknown error | Runtime lost the connection or timed out before SUBMIT ACK |

---

## Use Case 3 — Best-Effort Cancellation

```python
with Runtime(config="taskwire.yaml") as runtime:
    future = runtime.submit(generate_report, report_id)
    if future.cancel():
        print("removed before execution")
    else:
        print("already leased, remote, completed, or unknown")
```

Cancellation succeeds only for a task still queued at its current owner. v0.1 does not terminate running Python code or chase remotely forwarded tasks. Tasks with external side effects must remain safe under retries and cancellation races.

---

## Use Case 4 — Surviving an Agent Restart with SQLite

Use the default SQLite state store when acknowledged queued work must survive an agent restart:

```yaml
storage:
  state:
    type: "sqlite"
    sqlite:
      path: "/var/lib/taskwire/state.db"
```

Guarantee:

- SUBMIT ACK is sent only after the task record is durable.
- Queued and leased records recover after agent restart.
- Recovery can execute an in-flight task again.
- Task code remains responsible for idempotency.
- Result metadata is replayable after Runtime reconnect until acknowledged or retention expiry.

The runnable example should submit idempotent tasks, terminate the agent after ACK, restart it with the same SQLite database and object directory, and demonstrate eventual completion. CI runs this scenario with the example itself rather than a rewritten test-only equivalent.

---

## Use Case 5 — CPU or Capability Routing Across Nodes

This example is available only after Phase 5 and requires authenticated clustering.

```yaml
cluster:
  enabled: true
  allow_insecure: false
  node_name: "cpu-node-1"
  bind_addr: "0.0.0.0:7946"
  advertise_addr: "10.0.1.21:7946"
  task_port: 7947
  seeds: ["10.0.1.20:7946"]
  mdns: false
  encryption_key: "<base64-encoded-32-byte-key>"

workers:
  count: 8
  labels:
    workload: "compute"

routing:
  rules:
    - match: {label: "cpu"}
      prefer: {workload: "compute"}
```

```python
@task(label="cpu", idempotent=True)
def render_thumbnail(object_key: str) -> str:
    ...
```

The guide must explain firewall ports, seed discovery, routable advertise addresses, key distribution, node-failure duplicates, and the rule that cluster ports must never be exposed to untrusted networks. An example encryption key must never be presented as production-ready.

---

## Use Case 6 — Durable Results with Kafka

This example is available only after Phase 6 and requires the Kafka extra plus a reachable Kafka cluster.

```bash
python -m pip install "taskwire[kafka]"
```

```yaml
result_delivery:
  mode: "queue"
  queue:
    type: "kafka"
    brokers: ["kafka-1:9092", "kafka-2:9092"]
    topic: "taskwire-results"
```

Use queue delivery when the submitting Runtime may restart before consuming results. The application must persist task IDs and explicitly reattach:

```python
with Runtime(config="taskwire.yaml") as runtime:
    futures = runtime.reattach(task_ids=load_unfinished_task_ids())
    for future in futures:
        persist_result(future.task_id, future.result(timeout=60))
```

The final example must use the implemented `reattach` return shape rather than treating this sketch as the API contract. Document topic retention, result size limits, ACLs/TLS, poison messages, consumer cost, and the fact that per-Runtime groups read and discard results belonging to other Runtimes in v0.1.

---

## Use Case 7 — Operations and Observability

```bash
taskwire-agent status --json
```

Example fields:

```json
{
  "queue_depth": 3,
  "active_leases": [],
  "worker_pids": [4101, 4102],
  "worker_restarts": 0,
  "deadletter_count": 1,
  "members": []
}
```

The operations example covers:

- foreground development and system-service operation;
- graceful shutdown and its drain deadline;
- SQLite database and object-directory ownership, capacity, backup, and corruption handling;
- worker restart and dead-letter investigation;
- Prometheus endpoint configuration and alert suggestions;
- structured log fields, especially `task_id` and `lease_id`;
- version compatibility during agent and SDK upgrades.

Example alerts should include sustained queue growth, repeated lease expiry, worker restart storms, dead-letter growth, state/object storage pressure, and result-delivery failures.

---

## When Taskwire Is and Is Not a Good Fit

### Good fits

- A Python application needs local multi-process task execution with a small operational footprint.
- Work must burst onto nearby authenticated machines without operating a general-purpose task broker.
- Tasks are independent, retry-safe, and naturally represented as Python callables.
- A platform team owns worker topology and configuration while application developers own task functions.

### Poor fits for v0.1

- Exactly-once financial or transactional side effects.
- Untrusted multi-tenant task submission.
- Long-lived workflows requiring DAG state, schedules, chords, or human approval steps.
- Hard cancellation of running Python functions.
- Very large payload transport; use object storage and pass references instead.
- Durable result recovery without Kafka queue mode.
- Environments where cloudpickle compatibility across worker and submitter versions cannot be controlled.

---

## Documentation and Example Testing

Examples are product code and must not rot.

| Check | Requirement |
|---|---|
| Python snippets | Extract or import from `examples/`; run with pytest rather than duplicating snippets manually |
| Shell commands | Execute in clean containers/virtual environments where practical |
| Config files | Parse with both Python and Go config implementations |
| Local quick start | Run on every supported Python version in CI |
| SQLite/filesystem recovery | Run as an integration test with real process termination/restart |
| Cluster example | Run in the multi-node harness with authentication enabled |
| Kafka example | Run in the Kafka CI tier and verify reattachment |
| Links | Check local links and headings in CI |
| Expected output | Normalize nondeterministic IDs, ports, PIDs, and timings before comparison |

Do not place credentials, fixed production addresses, or downloadable executable scripts in examples. Avoid hidden setup: every required service, port, extra, and environment variable must be listed before the first command.

---

## Implementation Order

1. Write and test the local quick start against the Phase 4 wheel.
2. Add batch, error, timeout, and cancellation examples using only MVP APIs.
3. Add the SQLite/filesystem crash-recovery example and operations guide.
4. Add clustering and Kafka examples only when their feature gates are green.
5. Add troubleshooting based on failures observed in integration and chaos tests.
6. Promote the five-minute path into the README after testing it from the published TestPyPI artifact.

## Exit Gate

Phase 8 is complete when a person unfamiliar with the repository can choose an appropriate use case, run its example from a clean machine, understand the guarantee and failure boundary, and clean up every process and external resource it started. All example suites must pass against the exact release candidate artifact.
